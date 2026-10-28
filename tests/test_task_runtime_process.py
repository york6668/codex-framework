"""Installed legacy recovery, real process ownership and CAS regressions.

The synthetic RPC server only reads generated fixtures; it cannot execute a
model. Retired automatic-dispatch demos live only in their original Git history.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

import test_framework as fixtures
import framework_runtime as runtime
import task_runtime as task
import task_worker

READER = r'''#!/usr/bin/env python3
import json,sys
from pathlib import Path
root=Path.cwd()
if 'app-server' not in sys.argv:
 (root/'unexpected-model-dispatch').touch()
 sys.exit(9)
for line in sys.stdin:
 q=json.loads(line)
 if 'id' not in q:continue
 if q['method']=='initialize':result={'userAgent':'synthetic-read-only-recovery'}
 elif q['method']=='thread/turns/list':
  result=json.loads((root/'malformed-page.json').read_text()) if (root/'malformed-page.json').exists() else {'data':[json.loads((root/'fixture-turn.json').read_text())]}
 else:
  print(json.dumps({'id':q['id'],'error':{'code':-32601}}),flush=True);continue
 print(json.dumps({'id':q['id'],'result':result}),flush=True)
'''

class TaskProcessTests(fixtures.FixtureCase):
    def setUp(self):
        super().setUp()
        self.work = self.root / 'work'; self.work.mkdir()
        binary = self.root / 'fixture-bin'; binary.mkdir()
        fake = binary / 'codex'; fake.write_text(READER); fake.chmod(0o755)
        self.env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'], PYTHONDONTWRITEBYTECODE='1')
        runtime.install(self.a, fixtures.seed_runtime(self.repo))
        self.argv = [sys.executable, str(runtime.launcher_path(self.a)), '--home', str(self.a.home), 'task']

    def call(self, value):
        return subprocess.run(self.argv, input=json.dumps(value), text=True, capture_output=True, env=self.env, timeout=20)

    def historical(self):
        spec={'request_id':'fixture-request','requirement':'Recover original results without replay','acceptance_version':'v1','workspace':str(self.work),
              'steps':[{'id':name,'instruction':'Historical file effect','checks':[{'kind':'file','path':name+'.txt','sha256':hashlib.sha256(name.encode()).hexdigest()}]} for name in ('a','b')]}
        self.store=task.Store(self.a.codex_home,'process-fixture')
        return self.store.create('process-fixture',spec)

    def test_every_retired_dispatch_leaves_existing_state_and_effects_untouched(self):
        initial=self.historical();before=self.store.path.read_bytes()
        for operation in ('register','run','work'):
            payload={'operation':operation,'task_id':'process-fixture','task_ids':['process-fixture'],'spec':initial['spec'],
                     'expected_revision':initial['revision'],'expected_revisions':{'process-fixture':initial['revision']}}
            got=self.call(payload)
            self.assertEqual(got.returncode,2,got.stdout+got.stderr)
            self.assertEqual(json.loads(got.stdout)['status'],'retired_operation')
            self.assertEqual(self.store.path.read_bytes(),before)
        self.assertFalse((self.work/'unexpected-model-dispatch').exists())

    def test_live_owner_stops_recovery_then_original_native_result_is_consumed_once(self):
        initial=self.historical()
        owner=subprocess.Popen([sys.executable,'-c','import sys;sys.stdin.read()'],stdin=subprocess.PIPE)
        def stop():
            if owner.poll() is None:owner.communicate(timeout=5)
        self.addCleanup(stop)
        value,execution=task.reserve(self.store,initial['revision'])
        self.store.update(value['revision'],lambda v:v['execution'].update(controller_pid=owner.pid,session_id='original-session'))
        before=self.store.path.read_bytes()
        refused=self.call({'operation':'resume','task_id':'process-fixture'})
        self.assertEqual(refused.returncode,1,refused.stdout)
        self.assertIn('still running',refused.stdout)
        self.assertEqual(self.store.path.read_bytes(),before)
        stop()
        (self.work/'a.txt').write_text('a')
        turn={'id':'original-turn','status':'completed','itemsView':'full','items':[
            {'type':'userMessage','id':'original-input','content':[{'type':'text','text':'Framework task execution '+execution['id']+'\nHistorical request'}]},
            {'type':'agentMessage','id':'original-final','phase':'final_answer','text':'Original result'}]}
        (self.work/'fixture-turn.json').write_text(json.dumps(turn))
        result=self.call({'operation':'resume','task_id':'process-fixture'})
        self.assertEqual(result.returncode,2,result.stdout+result.stderr)
        recovered=json.loads(result.stdout)
        self.assertEqual(recovered['steps']['a']['state'],'verified')
        self.assertEqual(recovered['steps']['a']['evidence'][-1]['native_final_id'],'original-final')
        self.assertEqual(recovered['steps']['b']['runs'],0)
        self.assertFalse(recovered['accepted'])
        consumed=self.store.path.read_bytes()
        again=self.call({'operation':'resume','task_id':'process-fixture'})
        self.assertEqual(again.returncode,2,again.stdout)
        self.assertEqual(self.store.path.read_bytes(),consumed)
        self.assertEqual((self.work/'a.txt').read_text(),'a')
        self.assertFalse((self.work/'b.txt').exists())
        self.assertFalse((self.work/'unexpected-model-dispatch').exists())

    def test_two_real_clients_cas_conflict_and_independent_fact_rebase(self):
        initial=self.historical()
        requests=[{'operation':'delivery','task_id':'process-fixture','expected_revision':initial['revision'],
                   'event':event,'source_ref':'fixture-'+event} for event in ['sent','read']]
        clients=[]
        for request in requests:
            p=subprocess.Popen(self.argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=self.env)
            p.stdin.write(json.dumps(request));p.stdin.close();p.stdin=None
            clients.append(p)
        results=[]
        for p in clients:
            stdout,stderr=p.communicate(timeout=10);results.append((p.returncode,stdout,stderr))
        self.assertEqual(sorted(r[0] for r in results),[0,1])
        loser=next(i for i,r in enumerate(results) if r[0])
        self.assertIn('revision conflict',results[loser][1])
        current=self.call({'operation':'status','task_id':'process-fixture'})
        value=json.loads(current.stdout)
        retry=dict(requests[loser],expected_revision=value['revision'])
        rebased=self.call(retry);self.assertEqual(rebased.returncode,0,rebased.stdout)
        value=json.loads(rebased.stdout)
        self.assertEqual(value['delivery']['sent']['source_ref'],'fixture-sent')
        self.assertEqual(value['delivery']['unverified_read_observation']['source_ref'],'fixture-read')
        self.assertFalse(value['delivery']['read'])
        self.assertIsNone(value['delivery']['user_decision'])

    def test_recovery_rpc_incomplete_final_cannot_certify_original_success(self):
        final={'type':'agentMessage','id':'original-final','phase':'final_answer','text':'complete'}
        for finals in ([],[final,final],[dict(final,truncated=True)],[dict(final,text=None)],[dict(final,text='x'*32769)],[dict(final,id='')],[{k:v for k,v in final.items() if k!='id'}]):
            page={'data':[{'id':'original-turn','status':'completed','itemsView':'full','items':[
                {'type':'userMessage','content':[{'type':'text','text':'Framework task execution exact-execution\nfixture'}]},*finals]}]}
            (self.work/'malformed-page.json').write_text(json.dumps(page))
            with mock.patch.dict(os.environ,self.env):
                got=task_worker.inspect_session('fixture-session','exact-execution',str(self.work))
            self.assertEqual(got['status'],'unknown')
            self.assertEqual(got['message'],'')
        self.assertFalse((self.work/'unexpected-model-dispatch').exists())

    def test_original_native_identity_and_completion_error_are_not_normalized(self):
        for turn_id,error in (("",None),("   ",None),("original-turn",{"message":"completion conflicts with failure"})):
            page={'data':[{'id':turn_id,'status':'completed','error':error,'itemsView':'full','items':[
                {'type':'userMessage','id':'original-input','content':[{'type':'text','text':'Framework task execution exact-execution\nfixture'}]},
                {'type':'agentMessage','id':'original-final','phase':'final_answer','text':'complete'}]}]}
            (self.work/'malformed-page.json').write_text(json.dumps(page))
            with mock.patch.dict(os.environ,self.env):
                got=task_worker.inspect_session('fixture-session','exact-execution',str(self.work))
            self.assertEqual(got['status'],'unknown')
            self.assertNotIn('native_final_id',got)
        self.assertFalse((self.work/'unexpected-model-dispatch').exists())

    def test_actual_rpc_malformed_pages_remain_unknown_without_dispatch(self):
        pages = [[], {'data': {}}, {'data': [[]]}, {'data': [{'id': 'x', 'items': [None]}]}]
        for page in pages:
            with self.subTest(page=page):
                (self.work/'malformed-page.json').write_text(json.dumps(page))
                with mock.patch.dict(os.environ, self.env):
                    result=task_worker.inspect_session('fixture-session','fixture-execution',str(self.work))
                self.assertEqual(result['status'],'unknown')
                self.assertIn('invalid',result['error'])
        self.assertFalse((self.work/'effect-counts.json').exists())

    def test_actual_recovery_rpc_preserves_native_declined_items(self):
        for kind in ['commandExecution','fileChange']:
            with self.subTest(kind=kind):
                page={'data':[{'id':'owned-turn','status':'completed','itemsView':'full','items':[
                  {'type':'userMessage','content':[{'type':'text','text':'Framework task execution exact-execution\nfixture'}]},
                  {'type':kind,'id':'original-declined-effect','status':'declined'},
                  {'type':'agentMessage','phase':'final_answer','text':'completed'}]}]}
                (self.work/'malformed-page.json').write_text(json.dumps(page))
                with mock.patch.dict(os.environ,self.env):
                    result=task_worker.inspect_session('fixture-session','exact-execution',str(self.work))
                self.assertEqual(result['status'],'failed')
                self.assertEqual(result['error'],'native_permission_declined')
        self.assertFalse((self.work/'effect-counts.json').exists())
