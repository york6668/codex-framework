"""Dependency closure is checked at decisions; descriptor reads retain guards."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

import test_framework as fixtures
import task_runtime as task
import framework_runtime as runtime
import task_coordination as coord


class DependencyIOTests(fixtures.FixtureCase):
    def spec(self,identity,checks,**fields):
        return {'request_id':'current-'+identity,'requirement':'Consume only the current fixed result '+identity,
                'acceptance_version':'v1','workspace':str(self.root),'execution_mode':'external',
                'steps':[{'id':'read','instruction':'Validate exact passive files; no producer replay','checks':checks}],**fields}

    def report(self,store):
        value=store.read();body={'request_id':value['spec']['request_id'],'task_id':value['task_id'],
                               'input_revision':value['input_revision'],'task_input_sha256':value['acceptance_sha256'],'verdict':'PASS'}
        name=value['task_id']+'-result.json';raw=task.encoded(body);(self.root/name).write_bytes(raw)
        return {'path':name,'sha256':task.digest(raw)}

    def fixture(self,count=1,proof_count=1):
        source=self.root/'producer-source';source.mkdir();fixtures.run_git(source,'init','-b','release')
        fixtures.run_git(source,'config','user.name','Fixture');fixtures.run_git(source,'config','user.email','fixture@example.invalid')
        files=[]
        for n in range(count):
            name=f'part-{n:02}.txt';raw=('loaded-'+str(n)).encode();(source/name).write_bytes(raw)
            loaded=Path('installed/verified/runtime/parts/current')/name;(self.root/loaded).parent.mkdir(parents=True,exist_ok=True);(self.root/loaded).write_bytes(raw)
            files.append({'project_id':'core','source':name,'loaded':loaded.as_posix(),'sha256':task.digest(raw)})
        fixtures.run_git(source,'add','.');fixtures.run_git(source,'commit','-m','owned fixed dependency closure')
        versions=[{'project_id':'core','repo':source.name,'ref':'refs/heads/release','commit':fixtures.run_git(source,'rev-parse','HEAD'),'tree':fixtures.run_git(source,'rev-parse','HEAD^{tree}')}]
        raw=task.encoded({'versions':versions,'files':files});(self.root/'loaded-manifest.json').write_bytes(raw)
        proofs=[]
        for n in range(proof_count):
            name=Path('acceptance/independent-current-source/current-published-version/actual-install-readback')/f'proof-{n}.json'
            (self.root/name).parent.mkdir(parents=True,exist_ok=True);(self.root/name).write_bytes(b'fixed-proof')
            proofs.append({'kind':'file','path':name.as_posix(),'sha256':task.digest(b'fixed-proof')})
        producer=task.Store(self.a.codex_home,'producer');producer.create('producer',self.spec('producer',proofs,artifact={'manifest':'loaded-manifest.json','sha256':task.digest(raw),'versions':versions}))
        accepted=task.observe_external(producer,1,self.report(producer));self.assertTrue(accepted['accepted'])
        checks=[]
        for name in ['current-request.json','consumption-receipt.json','task3-current-request.json']:
            (self.root/name).write_bytes(b'current-request')
            checks.append({'kind':'file','path':name,'sha256':task.digest(b'current-request')})
        consumer=task.Store(self.a.codex_home,'consumer');value=consumer.create('consumer',self.spec('consumer',checks,known_tasks=['producer'],requires=[{'task_id':'producer','step_id':None,'input_sha256':accepted['acceptance_sha256'],'basis':{'status':'proven','source_ref':'synthetic:actual-loaded-producer','reason':'This consumer needs the current accepted producer version'}}]))
        return producer,consumer,value,self.report(consumer),proofs

    def test_normal_installed_consumer_with64_loaded_files_keeps_original_worker_budget(self):
        producer,consumer,value,ref,proofs=self.fixture(count=64,proof_count=8)
        fixtures.install_runtime(self.a)
        argv=[sys.executable,str(runtime.launcher_path(self.a)),'--home',str(self.a.home),'task']
        request={'operation':'verify-effects','task_id':'consumer','expected_revision':value['revision'],**ref}
        result=subprocess.run(argv,input=json.dumps(request),text=True,capture_output=True,timeout=45)
        self.assertEqual(result.stderr,'');self.assertEqual(result.returncode,0,result.stdout)
        final=json.loads(result.stdout);self.assertTrue(final['accepted']);self.assertIsNone(final['native_session_id']);self.assertEqual(final['steps']['read']['runs'],0)
        checks=final['steps']['read']['validation']['checks'];self.assertEqual(len(checks),3)
        self.assertTrue(all(c['passed'] and c['io']['limit_seconds']==5 for c in checks),checks)
        self.assertLess(len(task.encoded(consumer.read())),task.LIMIT)

    def test_changed_upstream_blocks_before_consumer_body_read(self):
        producer,consumer,value,ref,proofs=self.fixture()
        new=producer.read()['spec'];new['acceptance_version']='v2'
        task.change(producer,{'operation':'amend','expected_revision':producer.read()['revision'],'spec':new})
        with mock.patch('task_runtime.checked_file',side_effect=AssertionError('blocked consumer body read')):
            with self.assertRaisesRegex(task.TaskError,'unresolved relationship'):task.observe_external(consumer,value['revision'],ref)
        self.assertFalse(consumer.read()['accepted']);self.assertEqual(consumer.read()['steps']['read']['runs'],0)

    def test_upstream_change_during_fd_read_prevents_acceptance(self):
        producer,consumer,value,ref,proofs=self.fixture()
        target=self.root/'current-request.json';info=target.stat();identity=(info.st_dev,info.st_ino);real_read,real_fstat=os.read,os.fstat
        def read(fd,n):
            opened=real_fstat(fd)
            if (opened.st_dev,opened.st_ino)==identity:(self.root/proofs[0]['path']).write_bytes(b'changed-upstream-proof')
            return real_read(fd,n)
        with mock.patch('task_runtime.os.read',side_effect=read):final=task.observe_external(consumer,value['revision'],ref)
        self.assertFalse(final['accepted']);self.assertFalse(final['steps']['read']['validation']['passed'])
        self.assertTrue(final['steps']['read']['validation']['checks'][0]['passed'])
        self.assertEqual((self.root/proofs[0]['path']).read_bytes(),b'changed-upstream-proof')

    def test_fd_boundary_still_honors_peer_read_refusal(self):
        producer,consumer,value,ref,proofs=self.fixture()
        resource={'kind':'file','identity':'current-request.json','object':'','mode':'read','operation':'inspect','source_ref':'synthetic:exact-read-effect'}
        old=task.Store(self.a.codex_home,'original-refusal');spec=self.spec('old',[{'kind':'message','equals':'42'}]);spec.pop('execution_mode');spec['steps'][0]['resources']=[resource];old.create('original-refusal',spec)
        task.change(old,{'operation':'approval','expected_revision':1,'step':'read','decision':'denied','source_ref':'synthetic:original-target-read-refusal'})
        target=self.root/'current-request.json';info=target.stat();identity=(info.st_dev,info.st_ino);real_read,real_fstat=os.read,os.fstat
        def read(fd,n):
            opened=real_fstat(fd)
            if (opened.st_dev,opened.st_ino)==identity:raise AssertionError('refused body read')
            return real_read(fd,n)
        with coord.context(consumer.root),mock.patch('task_runtime.os.read',side_effect=read):
            with self.assertRaises(task.IOBudgetError) as caught:task.checked_file(value,'read',value['spec']['steps'][0]['checks'][0])
        self.assertIn('retained_refusal',str(caught.exception.report))
        self.assertEqual(old.read()['steps']['read']['wait']['kind'],'explicit_denial')

    def test_result_and_progress_channels_still_enforce_original_limit(self):
        def progress():
            for _ in range(6000):task.io_progress('bounded-progress',detail='x'*64)
            return True
        for producer in [lambda:'x'*task.LIMIT,progress]:
            with self.subTest(producer=producer),self.assertRaisesRegex(task.IOBudgetError,'validation_worker_output_limit') as caught:
                task.isolated_io('budget-regression',producer,2)
            self.assertTrue(caught.exception.report['worker_reaped'])
