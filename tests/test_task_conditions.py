"""AUD-002: ordinary task waits are not durable peer refusals.

All stores, sources, failures and effects here are isolated synthetic fixtures.
"""
import copy
import datetime as dt
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
import subprocess
import sys
from unittest import mock

from test_framework import FixtureCase, seed_runtime
import framework_runtime as runtime
import task_coordination as coordination
import task_runtime as task


class TaskConditionTests(FixtureCase):
    def spec(self, **changes):
        return dict(request_id='ordinary-condition-request', requirement='Continue after the actual input is ready',
            acceptance_version='v1', workspace=str(self.root), max_runs=3,
            assignment={'owner':'synthetic:original-owner','delivery_owner':'synthetic:original-owner',
                        'role':'implementation','covers':[]},
            steps=[{'id':'a','instruction':'Use the awaited input',
                    'checks':[{'kind':'file','path':'result.bin','sha256':task.digest(b'ready')}]},
                   {'id':'b','instruction':'Independent branch','checks':[{'kind':'message','equals':'42'}]}], **changes)

    def create(self, identity='ordinary', **changes):
        store = task.Store(self.a.codex_home, identity)
        return store, store.create(identity, self.spec(**changes))

    def change(self, store, operation, **fields):
        return task.change(store, dict(operation=operation, expected_revision=store.read()['revision'], **fields))

    def block(self, store, identity='input-ready', kind='dependency', step='a'):
        return self.change(store, 'block', step=step, reason_kind=kind, condition_id=identity,
                           condition='Actual source input is unavailable', evidence_ref='synthetic:original-wait-source')

    def report(self, store, identity='input-ready', step='a', **changes):
        value = store.read(); condition = next(c for c in value['conditions'] if c['id'] == identity)
        spec = next(s for s in value['spec']['steps'] if s['id'] == step)
        row = dict(kind='condition_satisfaction', task_id=value['task_id'], request_id=value['spec']['request_id'],
            input_revision=value['input_revision'], task_input_sha256=value['acceptance_sha256'], step_id=step,
            condition_id=identity, condition_kind=condition['kind'], owner_ref=condition['owner_ref'],
            condition_sha256=(condition.get('resolution') or {}).get('condition_sha256') or task.digest(task.encoded(condition)),
            scope_sha256=task.digest(task.encoded(task.file_scope(value['spec'], spec))),
            result='satisfied', evidence_refs=['synthetic:actual-producer-return'],
            observed_at=dt.datetime.now(dt.timezone.utc).isoformat())
        row.update(changes)
        path='satisfied-'+identity+'.json'; raw=task.encoded(row)
        (self.root/path).write_bytes(raw)
        return dict(step=step, condition_id=identity, path=path, sha256=task.digest(raw), max_age_seconds=60)

    def test_unclassified_new_block_is_rejected_without_writing_or_inferring_text(self):
        store, _ = self.create(); before=store.path.read_bytes()
        with self.assertRaisesRegex(task.TaskError, 'explicit reason_kind'):
            self.change(store,'block',step='a',condition='ordinary input unavailable',evidence_ref='synthetic:wait')
        self.assertEqual(store.path.read_bytes(),before)
        self.assertFalse(coordination.index_path(store.root).exists())

    def test_ordinary_wait_is_local_and_actual_resolution_refreshes_original_and_next_step(self):
        store, _ = self.create(); value=self.block(store)
        self.assertEqual(task.partition(value)['ready'],['b'])
        self.assertEqual(value['restrictions'],[])
        self.assertEqual(coordination.own_entries(value),[])
        peer,_=self.create('same-object-peer')
        with coordination.context(store.root):
            self.assertFalse(task.assessment(peer.read(),'a')['blocked'])
        (self.root/'result.bin').write_bytes(b'ready')
        # File changes alone are not an original-owner resolution.
        self.assertEqual(task.partition(store.read())['ready'],['b'])
        value=self.change(store,'resolve-condition',**self.report(store))
        self.assertEqual(value['conditions'][0]['state'],'resolved')
        self.assertEqual(task.view(store.read())['work_partition']['ready'],['a','b'])
        with coordination.context(store.root):
            self.assertFalse(task.assessment(peer.read(),'a')['blocked'])
        worker={'session_id':'synthetic-session','status':'completed','message':'42','exit':0}
        with mock.patch('task_worker.execute',return_value=worker) as execute:
            value=task.run(store,value['revision'])
        self.assertTrue(value['accepted'])
        self.assertEqual(execute.call_count,2)
        self.assertEqual(value['steps']['a']['runs'],1)

    def test_wrong_current_input_condition_owner_object_or_stale_result_cannot_resolve(self):
        changes=[{'input_revision':0},{'input_revision':True},{'task_id':'other'}, {'request_id':'old'},
                 {'task_input_sha256':'0'*64},{'owner_ref':'different-owner'},{'condition_id':'different-condition'},
                 {'condition_kind':'capability'},{'condition_sha256':'0'*64},{'scope_sha256':'0'*64},
                 {'result':'not_satisfied'},{'observed_at':'2000-01-01T00:00:00Z'}, {'evidence_refs':[]}]
        for index, fields in enumerate(changes):
            with self.subTest(fields=fields):
                store,_=self.create('wrong-'+str(index)); self.block(store); before=store.path.read_bytes()
                with self.assertRaises((task.TaskError, ValueError)):
                    self.change(store,'resolve-condition',**self.report(store,**fields))
                self.assertEqual(store.path.read_bytes(),before)

    def test_changed_resolution_bytes_are_not_consumed(self):
        store,_=self.create();self.block(store);reference=self.report(store);before=store.path.read_bytes()
        (self.root/reference['path']).write_bytes(b'{"result":"satisfied"}')
        with self.assertRaises(task.TaskError):
            self.change(store,'resolve-condition',**reference)
        self.assertEqual(store.path.read_bytes(),before)

    def test_amendment_retains_wait_and_old_input_report_cannot_release_it(self):
        store,_=self.create();self.block(store);old=self.report(store)
        spec=store.read()['spec'];spec['acceptance_version']='v2'
        value=self.change(store,'amend',spec=spec)
        self.assertEqual(task.partition(value)['ready'],['b'])
        with self.assertRaisesRegex(task.TaskError,'current task/input'):
            self.change(store,'resolve-condition',**old)
        value=self.change(store,'resolve-condition',**self.report(store))
        self.assertEqual(task.partition(value)['ready'],['a','b'])

    def test_multiple_conditions_are_resolved_separately(self):
        store,_=self.create();self.block(store);self.block(store,'capacity',kind='capability')
        self.change(store,'resolve-condition',**self.report(store))
        self.assertEqual(task.partition(store.read())['ready'],['b'])
        self.change(store,'resolve-condition',**self.report(store,'capacity'))
        self.assertEqual(task.partition(store.read())['ready'],['a','b'])

    def test_business_failure_goes_to_original_diagnosis_without_a_peer_denial(self):
        store,_=self.create();self.block(store,kind='business_failure')
        projected=task.view(store.read())
        self.assertEqual(projected['next_action']['kind'],'execute_or_collect_original_step')
        actions=projected['delivery_responsibility']['next_actions']
        self.assertIn('diagnose_original_failure',[a['kind'] for a in actions])
        self.assertEqual(store.read()['restrictions'],[])
        self.assertEqual(coordination.own_entries(store.read()),[])

    def test_resolution_never_clears_coexisting_denial_or_user_stop(self):
        for kind in ('denial','user_stop'):
            with self.subTest(kind=kind):
                store,_=self.create(kind);self.block(store)
                if kind=='denial':self.change(store,'approval',step='a',decision='denied',source_ref='synthetic:actual-denial')
                else:self.change(store,'block',step='a',reason_kind='user_stop',condition='Explicit user stop',evidence_ref='synthetic:actual-stop')
                restrictions=copy.deepcopy(store.read()['restrictions'])
                entries=copy.deepcopy(coordination.read_index(store.root))
                self.change(store,'resolve-condition',**self.report(store))
                self.assertEqual(store.read()['restrictions'],restrictions)
                self.assertEqual(coordination.read_index(store.root),entries)
                self.assertEqual(task.partition(store.read())['ready'],['b'])
                waits=task.view(store.read())['delivery_responsibility']['scoped_waits']
                self.assertIn('retain_exact_refusal' if kind=='denial' else 'wait_for_original_explicit_user_resumption',
                              [w['recovery'] for w in waits])

    def test_unknown_inflight_effect_survives_ordinary_condition_resolution(self):
        store,value=self.create();value,execution=task.reserve(store,value['revision'])
        self.block(store)
        value=self.change(store,'resolve-condition',**self.report(store))
        self.assertEqual(value['steps']['a']['unresolved_execution']['id'],execution['id'])
        self.assertEqual(value['steps']['a']['state'],'reconcile')
        self.assertEqual(task.partition(value)['ready'],['b'])
        self.assertTrue(any(e['execution_id']==execution['id'] and e['state']=='uncertain'
                            for e in coordination.read_index(store.root)))

    def test_condition_resolution_does_not_reexecute_an_already_verified_effect(self):
        store,value=self.create();(self.root/'result.bin').write_bytes(b'ready')
        with mock.patch('task_worker.execute',return_value={'session_id':'synthetic','status':'completed','message':'42','exit':0}):
            task.run(store,value['revision'])
        runs=store.read()['steps']['a']['runs'];self.block(store)
        value=self.change(store,'resolve-condition',**self.report(store))
        self.assertEqual(value['steps']['a']['state'],'verified')
        self.assertEqual(value['steps']['a']['runs'],runs)
        with mock.patch('task_worker.execute',side_effect=AssertionError('completed effect replayed')):
            self.assertTrue(task.run(store,value['revision'])['accepted'])

    def test_resolving_condition_cannot_revalidate_or_replay_a_changed_completed_effect(self):
        store,value=self.create();(self.root/'result.bin').write_bytes(b'ready')
        with mock.patch('task_worker.execute',return_value={'session_id':'synthetic','status':'completed','message':'42','exit':0}):
            task.run(store,value['revision'])
        self.block(store);(self.root/'result.bin').write_bytes(b'changed-completed-effect')
        value=self.change(store,'resolve-condition',**self.report(store))
        self.assertEqual(value['steps']['a']['state'],'blocked')
        self.assertEqual(value['steps']['a']['wait']['kind'],'verified_object_changed')
        self.assertNotIn('a',task.partition(value)['ready'])

    def test_same_object_other_step_cannot_release_the_original_steps_condition(self):
        spec=self.spec();spec['steps'][1]['checks']=copy.deepcopy(spec['steps'][0]['checks'])
        store=task.Store(self.a.codex_home,'same-object');store.create('same-object',spec);self.block(store)
        with self.assertRaisesRegex(task.TaskError,'original object'):
            self.change(store,'resolve-condition',**self.report(store,step='b'))
        self.assertEqual(store.read()['conditions'][0]['state'],'open')

    def test_object_change_during_source_read_does_not_release_the_wait(self):
        store,_=self.create();self.block(store);ref=self.report(store);before=store.path.read_bytes()
        original=coordination.read_json
        def changed(*args,**kwargs):
            body=original(*args,**kwargs);(self.root/'result.bin').write_bytes(b'changed-after-source-read');return body
        with mock.patch.object(coordination,'read_json',side_effect=changed):
            with self.assertRaisesRegex(task.TaskError,'changed while'):
                self.change(store,'resolve-condition',**ref)
        self.assertEqual(store.path.read_bytes(),before)

    def test_resolution_cas_conflict_is_before_report_read_or_state_change(self):
        store,_=self.create();self.block(store);reference=self.report(store);revision=store.read()['revision']
        self.change(store,'delivery',event='sent',source_ref='synthetic:independent-fact');before=store.path.read_bytes()
        with mock.patch.object(coordination,'read_json',side_effect=AssertionError('stale writer read source')):
            with self.assertRaises(task.RevisionConflict):
                task.change(store,dict(operation='resolve-condition',expected_revision=revision,**reference))
        self.assertEqual(store.path.read_bytes(),before)

    def test_resolution_partial_commit_recovers_index_without_repeating_resolution(self):
        store,_=self.create();self.block(store)
        self.change(store,'approval',step='a',decision='denied',source_ref='synthetic:retained-refusal')
        reference=self.report(store);old_revision=store.read()['revision']
        before=copy.deepcopy(coordination.read_index(store.root))
        with mock.patch.object(coordination,'write_index',side_effect=OSError('synthetic post-save index failure')):
            with self.assertRaises(task.TaskCommitError) as raised:
                self.change(store,'resolve-condition',**reference)
        self.assertEqual(raised.exception.commit_report['committed_revision'],old_revision+1)
        self.assertEqual(store.read()['conditions'][0]['state'],'resolved')
        self.assertEqual(coordination.read_index(store.root),before)
        revision=store.read()['revision']
        self.assertEqual(self.change(store,'resolve-condition',**reference)['revision'],revision)
        self.assertEqual(store.sync_effects(revision)['status'],'effects_synchronized')
        self.assertEqual(coordination.read_index(store.root),before)

    def test_legacy_unclassified_stop_is_not_migrated_or_released(self):
        store,_=self.create()
        def legacy(value):
            value['steps']['a'].update(state='blocked',wait={'kind':'external_condition','condition':'Old ambiguous source',
                                                           'evidence_ref':'synthetic:legacy-original'})
            task.remember_restriction(value,'a')
        store.update(1,legacy)
        before=store.path.read_bytes(); entries=coordination.read_index(store.root)
        with self.assertRaisesRegex(task.TaskError,'legacy stops/denials'):
            self.change(store,'resolve-condition',step='a',condition_id='legacy',path='unused.json',sha256='0'*64,max_age_seconds=60)
        self.assertEqual(store.path.read_bytes(),before)
        self.assertEqual(coordination.read_index(store.root),entries)
        self.assertIn('legacy_unclassified_stop',task.view(store.read())['current_constraints']['a']['restriction_kinds'])

    def test_version_bound_cli_uses_new_condition_path_and_preserves_old_runtime_closure(self):
        revision=seed_runtime(self.repo);runtime.install(self.a,revision)
        command=[sys.executable,'-B',str(runtime.launcher_path(self.a)),'--home',str(self.a.home),'task']
        def invoke(payload):
            result=subprocess.run(command,input=json.dumps(payload),text=True,capture_output=True,timeout=10)
            return result.returncode,json.loads(result.stdout)
        code,value=invoke({'operation':'register','task_id':'installed','spec':self.spec()});self.assertEqual(code,0)
        code,value=invoke({'operation':'block','task_id':'installed','expected_revision':value['revision'],'step':'a',
                          'reason_kind':'dependency','condition_id':'input-ready','condition':'Input not available','evidence_ref':'synthetic:original'})
        self.assertEqual(code,0);self.assertEqual(value['restrictions'],[])
        store=task.Store(self.a.codex_home,'installed');ref=self.report(store)
        code,value=invoke({'operation':'resolve-condition','task_id':'installed','expected_revision':value['revision'],**ref})
        self.assertEqual(code,0);self.assertEqual(value['work_partition']['ready'],['a','b'])
        self.assertEqual(value['conditions'][0]['state'],'resolved')

