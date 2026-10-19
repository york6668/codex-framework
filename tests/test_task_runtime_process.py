"""REAL_ENTRY_FIXTURE process/recovery drills with a synthetic worker protocol.

These run installed candidate processes and real tiny file effects. They do NOT
prove native model availability, native Goal/Hook adoption, or business delivery.
"""
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from unittest import mock

import test_framework as fixtures
import framework_runtime as runtime
import task_worker


PRODUCER = r'''#!/usr/bin/env python3
import hashlib,json,os,re,sys,time
from pathlib import Path
root=Path.cwd()
def emit(x):print(json.dumps(x),flush=True)
if 'app-server' in sys.argv:
 for line in sys.stdin:
  q=json.loads(line)
  if 'id' not in q:continue
  if q['method']=='initialize':emit({'id':q['id'],'result':{'userAgent':'synthetic-process-fixture'}})
  elif q['method']=='thread/turns/list':emit({'id':q['id'],'result':json.loads((root/'malformed-page.json').read_text()) if (root/'malformed-page.json').exists() else {'data':[json.loads((root/'fixture-turn.json').read_text())]}})
  else:emit({'id':q['id'],'error':{'code':-32601}})
else:
 prompt=sys.stdin.read()
 name=re.search(r'Create ([abc])\.txt',prompt.split('Current step: ',1)[1]).group(1)
 session='00000000-0000-0000-0000-000000000001'
 if name!='a' or not (root/'hide-session').exists():emit({'type':'thread.started','thread_id':session})
 counts=json.loads((root/'effect-counts.json').read_text()) if (root/'effect-counts.json').exists() else {}
 counts[name]=counts.get(name,0)+1
 (root/'effect-counts.json').write_text(json.dumps(counts))
 (root/(name+'.txt')).write_text(name.upper()+'\n')
 turn={'id':'fixture-turn-'+name,'status':'completed','itemsView':'full','items':[
  {'type':'userMessage','id':'fixture-input-'+name,'content':[{'type':'text','text':prompt}]},
  {'type':'agentMessage','id':'fixture-final-'+name,'phase':'final_answer','text':name}]}
 (root/'fixture-turn.json').write_text(json.dumps(turn))
 if name=='a' and (root/'drop-receipt').exists():
  (root/'producer.pid').write_text(str(os.getpid()))
  (root/'effect-committed').write_text('ready')
  end=time.monotonic()+10
  while not (root/'release-producer').exists() and time.monotonic()<end:time.sleep(.02)
 if name=='a' and (root/'live-mutation').exists():(root/'a.txt').write_text('late mutation')
 try:
  emit({'type':'item.completed','item':{'type':'agent_message','text':name}})
  emit({'type':'turn.completed','usage':{'input_tokens':0,'output_tokens':0}})
 except BrokenPipeError:pass
 (root/'producer-exited').write_text('done')
'''


class TaskProcessTests(fixtures.FixtureCase):
    def setUp(self):
        super().setUp()
        self.work = self.root / 'work'; self.work.mkdir()
        binary = self.root / 'fixture-bin'; binary.mkdir()
        fake = binary / 'codex'; fake.write_text(PRODUCER); fake.chmod(0o755)
        self.env = dict(os.environ, PATH=str(binary) + os.pathsep + os.environ['PATH'], PYTHONDONTWRITEBYTECODE='1')
        revision = fixtures.seed_runtime(self.repo)
        runtime.install(self.a, revision)
        self.argv = [sys.executable, str(runtime.launcher_path(self.a)), '--home', str(self.a.home), 'task']

    def call(self, value):
        return subprocess.run(self.argv, input=json.dumps(value), text=True, capture_output=True,
                              env=self.env, timeout=30)

    def register(self):
        steps=[]
        for index,name in enumerate('abc'):
            steps.append({'id':name,'instruction':'Create '+name+'.txt','depends_on':[] if not index else ['abc'[index-1]],
                          'checks':[{'kind':'file','path':name+'.txt','sha256':hashlib.sha256((name.upper()+'\n').encode()).hexdigest()}]})
        spec={'request_id':'fixture-request','requirement':'Create three requested phase files','acceptance_version':'v1','workspace':str(self.work),'steps':steps}
        from test_persistent_dependencies import bind_local_contract
        for producer,consumer in [('a','b'),('b','c')]:bind_local_contract(spec,'process-fixture',producer,consumer)
        result=self.call({'operation':'register','task_id':'process-fixture','spec':spec})
        self.assertEqual(result.returncode,0,result.stderr)
        return json.loads(result.stdout)

    def wait_file(self,name):
        deadline=time.monotonic()+8
        while time.monotonic()<deadline:
            if (self.work/name).exists():return
            time.sleep(.02)
        self.fail('owned fixture marker missing: '+name)

    def test_real_entry_consumes_three_results_and_auto_continues_without_operator_goal_reprompt(self):
        initial=self.register()
        result=self.call({'operation':'run','task_id':'process-fixture','expected_revision':initial['revision']})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        value=json.loads(result.stdout)
        self.assertTrue(value['accepted'])
        self.assertEqual(json.loads((self.work/'effect-counts.json').read_text()),{'a':1,'b':1,'c':1})
        pids={entry['worker_observation']['pid'] for entry in value['history'] if 'worker_observation' in entry}
        # Normal result history contains validation; step evidence retains actual child PIDs.
        pids.update(e['pid'] for step in value['steps'].values() for e in step['evidence'])
        self.assertEqual(len(pids),3)
        self.assertEqual(len({step['session_id'] for step in value['steps'].values()}),1)

    def test_real_controller_pid_restart_recovers_lost_receipt_and_continues_once(self):
        self.restart_lost_receipt(expect_session=True)

    def test_restart_before_native_identity_uses_only_exact_file_readback(self):
        self.restart_lost_receipt(expect_session=False)

    def restart_lost_receipt(self,expect_session):
        initial=self.register()
        if not expect_session:(self.work/'hide-session').touch()
        (self.work/'drop-receipt').touch()
        process=subprocess.Popen(self.argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,
                                 text=True,env=self.env)
        process.stdin.write(json.dumps({'operation':'run','task_id':'process-fixture','expected_revision':initial['revision']}));process.stdin.close()
        self.addCleanup(lambda: process.poll() is None and process.kill())
        self.addCleanup(process.stdout.close)
        self.addCleanup(process.stderr.close)
        self.wait_file('effect-committed')
        state_path=self.a.codex_home/'task-runtime/process-fixture.json'
        original=json.loads(state_path.read_text())
        self.assertEqual(original['execution']['controller_pid'],process.pid)
        self.assertEqual(original['steps']['a']['state'],'running')
        original_id=original['execution']['id']
        if expect_session:
            # The producer effect marker can precede the controller's durable
            # on_session callback. Inject loss only after the intended native
            # identity is committed; the other test covers the earlier window.
            deadline=time.monotonic()+5
            while not original['execution'].get('session_id') and time.monotonic()<deadline:
                time.sleep(.02);original=json.loads(state_path.read_text())
            self.assertEqual(original['execution']['id'],original_id)
            self.assertEqual(original['execution'].get('session_id'),'00000000-0000-0000-0000-000000000001')
        else:
            self.assertIsNone(original['execution'].get('session_id'))
        os.kill(process.pid,signal.SIGKILL);process.wait(timeout=5)
        held=self.call({'operation':'resume','task_id':'process-fixture'})
        self.assertEqual(held.returncode,1)
        self.assertIn('worker_pid is still running',held.stdout)
        self.assertEqual(json.loads((self.work/'effect-counts.json').read_text()),{'a':1})
        (self.work/'release-producer').touch();self.wait_file('producer-exited')
        end=time.monotonic()+5
        while time.monotonic()<end:
            try:os.kill(original['execution']['worker_pid'],0)
            except ProcessLookupError:break
            time.sleep(.02)
        # Fresh process gets only the task id, not a replacement goal/plan/body.
        result=self.call({'operation':'resume','task_id':'process-fixture'})
        self.assertEqual(result.returncode,0,result.stdout+result.stderr)
        current=json.loads(result.stdout)
        self.assertTrue(current['accepted'])
        recovered=current['history'][0]
        if expect_session:
            self.assertEqual(recovered.get('execution',{}).get('id'),original_id,recovered)
            self.assertTrue(any(e.get('native_turn_id')=='fixture-turn-a' for e in current['steps']['a']['evidence']))
            self.assertNotIn('reconciled_execution',recovered)
        else:
            self.assertEqual(recovered.get('reconciled_execution',{}).get('id'),original_id,recovered)
            self.assertFalse(current['steps']['a']['evidence'])
            self.assertEqual(recovered['source_ref'],'restart/passive-target-readback/'+original_id)
        self.assertEqual(json.loads((self.work/'effect-counts.json').read_text()),{'a':1,'b':1,'c':1})
        new_pids={entry['execution']['controller_pid'] for entry in current['history'][1:]}
        self.assertTrue(new_pids)
        self.assertNotIn(process.pid,new_pids)
        self.assertEqual(current['input_revision'],1)
        self.assertIsNone(current['delivery']['user_decision'])

    def test_live_controller_and_child_cannot_be_consumed_by_passive_reconcile(self):
        initial=self.register();(self.work/'drop-receipt').touch();(self.work/'live-mutation').touch()
        process=subprocess.Popen(self.argv,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=self.env)
        self.addCleanup(lambda:process.poll() is None and process.kill())
        process.stdin.write(json.dumps({'operation':'run','task_id':'process-fixture','expected_revision':initial['revision'],'max_turns':1}));process.stdin.close();process.stdin=None
        self.wait_file('effect-committed')
        value=json.loads(self.call({'operation':'status','task_id':'process-fixture'}).stdout)
        self.assertEqual(value['execution']['controller_pid'],process.pid)
        self.assertIsInstance(value['execution']['worker_pid'],int)
        denied=self.call({'operation':'reconcile','task_id':'process-fixture','expected_revision':value['revision'],'step':'a','source_ref':'passive-file-while-live'})
        self.assertEqual(denied.returncode,1)
        self.assertIn('controller_pid is still running',denied.stdout)
        accepted=self.call({'operation':'accept','task_id':'process-fixture','expected_revision':value['revision']})
        self.assertEqual(accepted.returncode,1)
        (self.work/'release-producer').touch();stdout,stderr=process.communicate(timeout=8)
        self.assertEqual(process.returncode,2,stdout+stderr)
        current=json.loads(self.call({'operation':'status','task_id':'process-fixture'}).stdout)
        self.assertFalse(current['accepted'])
        self.assertEqual((self.work/'a.txt').read_text(),'late mutation')

    def test_installed_accept_cannot_skip_registered_worker_and_checks(self):
        initial=self.register()
        # The protected accept entry cannot be called around the worker/check path.
        result=self.call({'operation':'accept','task_id':'process-fixture','expected_revision':initial['revision']})
        self.assertEqual(result.returncode,1)
        self.assertEqual(json.loads(result.stdout)['status'],'task_error')
        current=json.loads(self.call({'operation':'status','task_id':'process-fixture'}).stdout)
        self.assertFalse(current['accepted'])
        self.assertTrue(all(step['state']=='pending' for step in current['steps'].values()))
        self.assertFalse(any((self.work/(n+'.txt')).exists() for n in 'abc'))

    def test_installed_workspace_rebase_retains_denial_and_executes_other_target(self):
        nested=self.work/'nested';nested.mkdir();(nested/'a.txt').write_bytes(b'A\n')
        check=lambda name,path:{'id':name,'instruction':'Create '+name+'.txt','checks':[{'kind':'file','path':path,'sha256':hashlib.sha256((name.upper()+'\n').encode()).hexdigest()}]}
        spec={'request_id':'anchor-original','requirement':'Produce exact technical fixture','acceptance_version':'v1','workspace':str(self.work),'steps':[check('a','nested/a.txt')]}
        registered=self.call({'operation':'register','task_id':'anchor','spec':spec});self.assertEqual(registered.returncode,0,registered.stdout)
        value=json.loads(registered.stdout)
        denied=self.call({'operation':'approval','task_id':'anchor','expected_revision':value['revision'],'step':'a','decision':'denied','source_ref':'synthetic-original-file-denial'})
        self.assertEqual(denied.returncode,0,denied.stdout);value=json.loads(denied.stdout)
        spec=dict(spec,workspace=str(nested),acceptance_version='v2',steps=[check('a','a.txt'),check('b','b.txt')])
        amended=self.call({'operation':'amend','task_id':'anchor','expected_revision':value['revision'],'spec':spec})
        self.assertEqual(amended.returncode,0,amended.stdout);value=json.loads(amended.stdout)
        self.assertEqual(value['work_partition']['ready'],['b'])
        result=self.call({'operation':'run','task_id':'anchor','expected_revision':value['revision'],'max_turns':2})
        self.assertEqual(result.returncode,2,result.stdout+result.stderr);value=json.loads(result.stdout)
        self.assertEqual(value['steps']['a']['runs'],0)
        self.assertEqual(value['steps']['a']['wait']['source_ref'],'synthetic-original-file-denial')
        self.assertEqual(value['steps']['b']['state'],'verified')
        self.assertEqual(json.loads((nested/'effect-counts.json').read_text()),{'b':1})
        self.assertFalse(value['accepted'])

    def test_installed_post_amend_alias_is_not_dispatched_and_independent_b_runs(self):
        original=self.work/'original';original.write_bytes(b'A\n')
        step=lambda name,path:{'id':name,'instruction':'Create '+name+'.txt','checks':[{'kind':'file','path':path,'sha256':hashlib.sha256((name.upper()+'\n').encode()).hexdigest()}]}
        spec={'request_id':'timing-original','requirement':'Exact current file fixture','acceptance_version':'v1','workspace':str(self.work),'steps':[step('a','original')]}
        registered=self.call({'operation':'register','task_id':'timing','spec':spec});self.assertEqual(registered.returncode,0,registered.stdout);value=json.loads(registered.stdout)
        denied=self.call({'operation':'approval','task_id':'timing','expected_revision':value['revision'],'step':'a','decision':'denied','source_ref':'synthetic-original-object'});self.assertEqual(denied.returncode,0,denied.stdout);value=json.loads(denied.stdout)
        spec=dict(spec,acceptance_version='v2',steps=[step('a','a.txt'),step('b','b.txt')])
        amended=self.call({'operation':'amend','task_id':'timing','expected_revision':value['revision'],'spec':spec});self.assertEqual(amended.returncode,0,amended.stdout);value=json.loads(amended.stdout)
        self.assertEqual(value['work_partition']['ready'],['a','b'])
        os.link(original,self.work/'a.txt')
        observed=self.call({'operation':'status','task_id':'timing'});self.assertEqual(observed.returncode,0,observed.stdout)
        self.assertEqual(json.loads(observed.stdout)['work_partition']['ready'],['b'])
        result=self.call({'operation':'run','task_id':'timing','expected_revision':value['revision'],'max_turns':2})
        self.assertEqual(result.returncode,2,result.stdout+result.stderr);value=json.loads(result.stdout)
        self.assertEqual(value['steps']['a']['runs'],0)
        self.assertEqual(value['steps']['b']['state'],'verified')
        self.assertEqual(json.loads((self.work/'effect-counts.json').read_text()),{'b':1})
        self.assertFalse(value['accepted'])

    def test_installed_fifo_validation_needs_no_writer_and_independent_b_finishes(self):
        fifo=self.work/'pipe';os.mkfifo(fifo,0o600)
        steps=[{'id':'a','instruction':'Create a.txt','checks':[{'kind':'file','path':'pipe','sha256':hashlib.sha256(b'A\n').hexdigest()}]},
               {'id':'b','instruction':'Create b.txt','checks':[{'kind':'file','path':'b.txt','sha256':hashlib.sha256(b'B\n').hexdigest()}]}]
        registered=self.call({'operation':'register','task_id':'fifo','spec':{'request_id':'fifo-no-writer','requirement':'Local I/O error must not stop B','acceptance_version':'v1','workspace':str(self.work),'steps':steps}})
        self.assertEqual(registered.returncode,0,registered.stdout);value=json.loads(registered.stdout)
        start=time.monotonic()
        result=subprocess.run(self.argv,input=json.dumps({'operation':'run','task_id':'fifo','expected_revision':value['revision'],'max_turns':3}),text=True,capture_output=True,env=self.env,timeout=3)
        self.assertLess(time.monotonic()-start,3)
        self.assertEqual(result.returncode,2,result.stdout+result.stderr);value=json.loads(result.stdout)
        self.assertEqual(value['steps']['a']['wait']['kind'],'validation_error')
        self.assertIn('expected ordinary file',value['steps']['a']['validation']['checks'][0]['detail'])
        self.assertEqual(value['steps']['b']['state'],'verified')
        self.assertEqual(json.loads((self.work/'effect-counts.json').read_text()),{'a':1,'b':1})
        self.assertFalse(value['accepted'])

    def test_real_native_child_is_reaped_without_prompt_when_callback_stops_it(self):
        with mock.patch.dict(os.environ,self.env):
            result=task_worker.execute('Create a.txt',str(self.work),on_process=lambda pid:False)
        self.assertEqual(result['status'],'failed')
        self.assertEqual(result['error'],'coordinator_pre_prompt_blocked')
        self.assertFalse(result['prompt_sent'])
        self.assertIsInstance(result['pid'],int)
        self.assertIsNotNone(result['exit'])
        self.assertFalse((self.work/'effect-counts.json').exists())

    def test_two_real_clients_cas_conflict_and_independent_fact_rebase(self):
        initial=self.register()
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
