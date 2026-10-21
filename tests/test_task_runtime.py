"""Execution entry regressions; synthetic workers are explicitly not native proof."""
import copy
import ast
import hashlib
import json
import os
import stat
import time
import unicodedata
from pathlib import Path
import subprocess
import sys
from unittest import mock

import test_framework as fixtures
import task_runtime as task
import framework_runtime as runtime


class TaskRuntimeTests(fixtures.FixtureCase):
    def spec(self, steps=None, **extra):
        return {"request_id": "request-1", "requirement": "Deliver the requested result, not merely a final message",
                "acceptance_version": "v1", "workspace": str(self.root),
                "steps": steps or [{"id": "a", "instruction": "Return 42", "checks": [{"kind": "message", "equals": "42"}]}], **extra}

    def create(self, spec=None):
        store = task.Store(self.a.codex_home, "fixture")
        value = store.create("fixture", spec or self.spec())
        return store, value

    def worker(self, message="42", status="completed"):
        return {"session_id": "native-fixture-session", "status": status, "message": message,
                "message_sha256": hashlib.sha256(message.encode()).hexdigest(), "exit": 0,
                "events_sha256": "1" * 64}

    def mutate(self, store, operation, **fields):
        return task.change(store, {"operation": operation, "expected_revision": store.read()["revision"], **fields})

    def denied_then_new_target(self, existing=False, review=False):
        original=self.root/'original';original.write_bytes(b'wanted')
        destination=self.root/'new'
        if existing:destination.write_bytes(b'wanted')
        first={'id':'a','instruction':'Write original target','checks':[{'kind':'file','path':'original','sha256':hashlib.sha256(b'wanted').hexdigest()}]}
        store,value=self.create(self.spec([first]))
        self.mutate(store,'approval',step='a',decision='denied',source_ref='original-precise-file-denial')
        new={'id':'a','instruction':'Write new target','checks':[{'kind':'file','path':'new','sha256':hashlib.sha256(b'wanted').hexdigest()}]}
        spec=self.spec([new,{'id':'b','instruction':'Independent message','checks':[{'kind':'message','equals':'42'}]}],review_required=review)
        value=self.mutate(store,'amend',spec=spec)
        return store,value,original,destination

    def test_post_amend_alias_is_rechecked_before_dispatch_and_other_branch_runs(self):
        store,value,original,destination=self.denied_then_new_target()
        self.assertEqual(task.partition(value)['ready'],['a','b'])
        os.link(original,destination)
        self.assertEqual(task.view(store.read())['work_partition']['ready'],['b'])
        with mock.patch('task_worker.execute',return_value=self.worker()) as worker:value=task.run(store,value['revision'])
        self.assertEqual(worker.call_count,1)
        self.assertEqual(value['steps']['a']['runs'],0)
        self.assertEqual(value['steps']['a']['wait']['reason'],'current_object_restriction')
        self.assertEqual(value['steps']['b']['state'],'verified')
        self.assertFalse(value['accepted'])

    def test_change_after_reservation_stops_prompt_and_preserves_independent_work(self):
        store,value,original,destination=self.denied_then_new_target()
        sent=[]
        def execute(prompt,workspace,session,on_process,on_session,**options):
            is_a='Current step: Write new target' in prompt
            if is_a:os.link(original,destination)
            allowed=on_process(os.getpid());sent.append((is_a,allowed))
            if not allowed:return dict(self.worker(status='failed'),error='coordinator_pre_prompt_blocked',prompt_sent=False)
            on_session('independent-native-fixture')
            return dict(self.worker(),session_id='independent-native-fixture',prompt_sent=True)
        with mock.patch('task_worker.execute',side_effect=execute):value=task.run(store,value['revision'])
        self.assertEqual(sent,[(True,False),(False,True)])
        self.assertFalse(value['steps']['a']['wait']['possible_effects'])
        self.assertEqual(value['steps']['b']['state'],'verified')
        self.assertFalse(value['accepted'])

    def test_change_after_prompt_is_unknown_effect_not_automatic_retry(self):
        store,value,original,destination=self.denied_then_new_target()
        calls=[]
        def execute(prompt,workspace,session,on_process,on_session,**options):
            is_a='Current step: Write new target' in prompt;calls.append((is_a,session))
            self.assertTrue(on_process(os.getpid()))
            if is_a:os.link(original,destination)
            allowed=on_session('owned-a' if is_a else 'owned-b')
            if not allowed:return dict(self.worker(status='unknown'),session_id='owned-a',error='coordinator_scope_changed_after_prompt',prompt_sent=True)
            return dict(self.worker(),session_id='owned-b',prompt_sent=True)
        with mock.patch('task_worker.execute',side_effect=execute):value=task.run(store,value['revision'],max_turns=4)
        self.assertEqual(len(calls),2)
        self.assertIsNone(calls[1][1])
        self.assertEqual(value['steps']['a']['state'],'reconcile')
        self.assertTrue(value['steps']['a']['wait']['possible_effects'])
        self.assertEqual(value['steps']['b']['state'],'verified')
        self.assertFalse(value['accepted'])

    def test_returned_success_cannot_validate_a_new_denied_alias(self):
        store,value,original,destination=self.denied_then_new_target()
        def execute(prompt,*args,**kwargs):
            if 'Current step: Write new target' in prompt:os.link(original,destination)
            return dict(self.worker(),prompt_sent=True)
        with mock.patch('task_worker.execute',side_effect=execute), mock.patch('task_runtime.checked_file',side_effect=AssertionError('denied body read')):
            value=task.run(store,value['revision'])
        self.assertFalse(value['steps']['a']['validation']['passed'])
        self.assertTrue(value['steps']['a']['wait']['possible_effects'])
        self.assertEqual(value['steps']['b']['state'],'verified')
        self.assertFalse(value['accepted'])

    def test_alias_after_validation_blocks_acceptance_without_dispatch(self):
        store,value,original,destination=self.denied_then_new_target(existing=True,review=True)
        with mock.patch('task_worker.execute',return_value=self.worker()):value=task.run(store,value['revision'])
        self.assertEqual(value['steps']['a']['state'],'verified')
        destination.unlink();os.link(original,destination)
        with self.assertRaisesRegex(task.TaskError,'current object constraints'):
            self.mutate(store,'accept')
        self.assertFalse(task.view(store.read())['accepted'])

    def test_descriptor_identity_is_checked_before_reading_a_racing_alias(self):
        store,value,original,destination=self.denied_then_new_target(existing=True)
        real_open=os.open;real_read=os.read;real_fstat=os.fstat;swapped=[]
        target_identity=(original.stat().st_dev,original.stat().st_ino)
        def reading(fd,n):
            info=real_fstat(fd)
            if (info.st_dev,info.st_ino)==target_identity:raise AssertionError('forbidden descriptor body read')
            return real_read(fd,n)
        def opening(path,*args,**kwargs):
            if path=='new' and kwargs.get('dir_fd') is not None and not swapped:
                destination.unlink();os.link(original,destination);swapped.append(True)
            return real_open(path,*args,**kwargs)
        with mock.patch('task_worker.execute',return_value=self.worker()), mock.patch('task_runtime.os.open',side_effect=opening), mock.patch('task_runtime.os.read',side_effect=reading):
            value=task.run(store,value['revision'])
        self.assertTrue(os.path.samefile(original,destination))
        self.assertIn('descriptor matches retained',value['steps']['a']['validation']['checks'][0]['detail'])
        self.assertEqual(value['steps']['b']['state'],'verified')
        self.assertFalse(value['accepted'])

    def test_stronger_stability_requirement_stops_only_the_unsupported_step(self):
        marker=self.root/'must-not-run'
        steps=[{'id':'a','instruction':'Needs atomic object isolation','stable_objects_required':True,
                'checks':[{'kind':'command','argv':[sys.executable,'-c',"from pathlib import Path;Path('must-not-run').touch()"],'timeout':1}]},
               {'id':'b','instruction':'Independent allowed work','checks':[{'kind':'message','equals':'42'}]}]
        store,value=self.create(self.spec(steps))
        with mock.patch('task_worker.execute',return_value=self.worker()) as worker:value=task.run(store,value['revision'])
        self.assertEqual(worker.call_count,1)
        self.assertEqual(value['steps']['a']['wait']['reason'],'stable_object_isolation_adapter_unavailable')
        self.assertFalse(marker.exists())
        self.assertEqual(value['steps']['b']['state'],'verified')

    def test_file_open_type_and_read_delays_are_bounded_without_stopping_b(self):
        path=self.root/'slow';path.write_bytes(b'wanted')
        real_open,real_fstat,real_read=os.open,os.fstat,os.read
        target_identity=(path.stat().st_dev,path.stat().st_ino)
        for index,phase in enumerate(['open_target','fstat_target','read_target']):
            with self.subTest(phase=phase):
                spec=self.spec([{'id':'a','instruction':'Exact slow file','checks':[{'kind':'file','path':'slow','sha256':hashlib.sha256(b'wanted').hexdigest(),'timeout':1}]},
                                {'id':'b','instruction':'Independent message','checks':[{'kind':'message','equals':'42'}]}])
                store=task.Store(self.a.codex_home,'slow-'+str(index));value=store.create('slow-'+str(index),spec)
                def opening(name,*args,**kwargs):
                    if name=='slow' and kwargs.get('dir_fd') is not None:time.sleep(5)
                    return real_open(name,*args,**kwargs)
                def stated(fd):
                    info=real_fstat(fd)
                    if (info.st_dev,info.st_ino)==target_identity:time.sleep(5)
                    return info
                def reading(fd,n):
                    info=real_fstat(fd)
                    if (info.st_dev,info.st_ino)==target_identity:time.sleep(5)
                    return real_read(fd,n)
                patch=mock.patch('task_runtime.os.open',side_effect=opening) if phase=='open_target' else mock.patch('task_runtime.os.fstat',side_effect=stated) if phase=='fstat_target' else mock.patch('task_runtime.os.read',side_effect=reading)
                start=time.monotonic()
                with patch,mock.patch('task_worker.execute',return_value=self.worker()):value=task.run(store,value['revision'])
                self.assertLess(time.monotonic()-start,3)
                row=value['steps']['a']['validation']['checks'][0]
                self.assertIn('timeout',row['error'])
                self.assertEqual(row['io']['last_observation']['phase'],phase)
                self.assertEqual(value['steps']['b']['state'],'verified')
                self.assertFalse(value['accepted'])

    def test_stalled_metadata_is_not_retried_at_every_decision_in_one_run(self):
        path=self.root/'metadata-slow';path.write_bytes(b'wanted');real_stat=os.stat
        spec=self.spec([{'id':'a','instruction':'Slow target','checks':[{'kind':'file','path':'metadata-slow','sha256':hashlib.sha256(b'wanted').hexdigest()}]},
                        {'id':'b','instruction':'Independent message','checks':[{'kind':'message','equals':'42'}]}])
        store,value=self.create(spec)
        def stated(name,*args,**kwargs):
            if Path(name)==path:time.sleep(5)
            return real_stat(name,*args,**kwargs)
        start=time.monotonic()
        with mock.patch('task_runtime.os.stat',side_effect=stated),mock.patch('task_worker.execute',return_value=self.worker()) as worker:value=task.run(store,value['revision'])
        self.assertLess(time.monotonic()-start,4)
        self.assertEqual(worker.call_count,1)
        self.assertEqual(value['steps']['a']['runs'],0)
        self.assertEqual(value['steps']['b']['state'],'verified')

    def test_file_byte_limit_stops_before_body_and_independent_b_continues(self):
        (self.root/'large').write_bytes(b'x'*512)
        spec=self.spec([{'id':'a','instruction':'Check bounded file','checks':[{'kind':'file','path':'large','sha256':hashlib.sha256(b'x'*512).hexdigest(),'max_bytes':16}]},
                        {'id':'b','instruction':'Independent message','checks':[{'kind':'message','equals':'42'}]}])
        store,value=self.create(spec)
        real_read,real_fstat=os.read,os.fstat;info=(self.root/'large').stat();target_identity=(info.st_dev,info.st_ino)
        def reading(fd,n):
            current=real_fstat(fd)
            if (current.st_dev,current.st_ino)==target_identity:raise AssertionError('over-budget file read')
            return real_read(fd,n)
        with mock.patch('task_worker.execute',return_value=self.worker()),mock.patch('task_runtime.os.read',side_effect=reading):value=task.run(store,value['revision'])
        self.assertIn('byte budget',value['steps']['a']['validation']['checks'][0]['detail'])
        self.assertEqual(value['steps']['b']['state'],'verified')

    def test_validator_output_limit_preserves_partial_effect_without_retry(self):
        script="from pathlib import Path;import sys;p=Path('once');p.write_text(p.read_text()+'x' if p.exists() else 'x');sys.stdout.write('x'*1048576)"
        spec=self.spec([{'id':'a','instruction':'Bound validator output','checks':[{'kind':'command','argv':[sys.executable,'-c',script],'timeout':2,'max_output_bytes':4096}]},
                        {'id':'b','instruction':'Independent message','checks':[{'kind':'message','equals':'42'}]}])
        store,value=self.create(spec)
        with mock.patch('task_worker.execute',return_value=self.worker()):value=task.run(store,value['revision'],max_turns=4)
        self.assertEqual((self.root/'once').read_text(),'x')
        self.assertTrue(value['steps']['a']['wait']['validator_possible_effects'])
        self.assertIn('output_limit',value['steps']['a']['validation']['checks'][0]['error'])
        self.assertEqual(value['steps']['b']['state'],'verified')

    def test_validator_exited_parent_with_open_descendant_pipe_has_a_deadline(self):
        script="import subprocess,sys;p=subprocess.Popen([sys.executable,'-c','import time;time.sleep(1.5)']);print(p.pid,flush=True)"
        spec=self.spec([{'id':'a','instruction':'Bound descendant pipe','checks':[{'kind':'command','argv':[sys.executable,'-c',script],'timeout':1}]},
                        {'id':'b','instruction':'Independent message','checks':[{'kind':'message','equals':'42'}]}])
        store,value=self.create(spec);start=time.monotonic()
        with mock.patch('task_worker.execute',return_value=self.worker()):value=task.run(store,value['revision'])
        self.assertLess(time.monotonic()-start,3)
        self.assertIn('timeout',value['steps']['a']['validation']['checks'][0]['error'])
        self.assertTrue(value['steps']['a']['wait']['validator_possible_effects'])
        self.assertEqual(value['steps']['b']['state'],'verified')
        time.sleep(.6)  # The owned finite fixture descendant exits naturally even if cleanup is unavailable.

    def test_fifo_replacement_after_metadata_is_rejected_by_actual_fd_type(self):
        path=self.root/'race-pipe';path.write_bytes(b'wanted');real_open=os.open
        spec=self.spec([{'id':'a','instruction':'Check actual type','checks':[{'kind':'file','path':'race-pipe','sha256':hashlib.sha256(b'wanted').hexdigest()}]},
                        {'id':'b','instruction':'Independent message','checks':[{'kind':'message','equals':'42'}]}])
        store,value=self.create(spec)
        def opening(name,*args,**kwargs):
            if name=='race-pipe' and kwargs.get('dir_fd') is not None:
                path.unlink();os.mkfifo(path,0o600)
            return real_open(name,*args,**kwargs)
        start=time.monotonic()
        with mock.patch('task_runtime.os.open',side_effect=opening),mock.patch('task_worker.execute',return_value=self.worker()):value=task.run(store,value['revision'])
        self.assertLess(time.monotonic()-start,3)
        self.assertTrue(stat.S_ISFIFO(path.stat().st_mode))
        self.assertIn('expected ordinary file',value['steps']['a']['validation']['checks'][0]['detail'])
        self.assertEqual(value['steps']['b']['state'],'verified')

    def test_validator_effect_cannot_make_a_denied_alias_acceptable(self):
        store,value,original,destination=self.denied_then_new_target(existing=True)
        spec=copy.deepcopy(value['spec'])
        command="from pathlib import Path;import os;p=Path('new');p.unlink();os.link('original',p)"
        spec['steps'][0]['checks'].append({'kind':'command','argv':[sys.executable,'-c',command],'timeout':1})
        value=self.mutate(store,'amend',spec=spec)
        with mock.patch('task_worker.execute',return_value=self.worker()), mock.patch('task_runtime.checked_file',side_effect=AssertionError('denied file read')):
            value=task.run(store,value['revision'])
        self.assertTrue(os.path.samefile(original,destination))
        self.assertFalse(value['steps']['a']['validation']['passed'])
        self.assertEqual(value['steps']['b']['state'],'verified')

    def test_unreadable_alias_representation_blocks_only_the_affected_branch(self):
        store,value,original,destination=self.denied_then_new_target()
        destination.symlink_to(original)
        self.assertEqual(task.partition(store.read())['ready'],['b'])
        with mock.patch('task_worker.execute',return_value=self.worker()) as worker:value=task.run(store,value['revision'])
        self.assertEqual(worker.call_count,1)
        self.assertEqual(value['steps']['a']['wait']['reason'],'object_identity_not_established')
        self.assertEqual(value['steps']['b']['state'],'verified')

    def test_resume_queries_the_original_unknown_result_without_worker_replay(self):
        (self.root/'x').write_bytes(b'wanted')
        spec=self.spec([{'id':'a','instruction':'Produce exact file','checks':[{'kind':'file','path':'x','sha256':hashlib.sha256(b'wanted').hexdigest()}]}])
        store,value=self.create(spec)
        with mock.patch('task_worker.execute',return_value=dict(self.worker(status='unknown'),prompt_sent=True)):
            value=task.run(store,value['revision'])
        original=value['steps']['a']['unresolved_execution']
        self.assertEqual(value['steps']['a']['state'],'reconcile')
        with mock.patch('task_runtime.os.kill',side_effect=ProcessLookupError), mock.patch('task_worker.inspect_session',return_value=self.worker()) as read, mock.patch('task_worker.execute',side_effect=AssertionError('unknown effect repeated')):
            value=task.resume(store)
        read.assert_called_once_with(original['session_id'],original['id'],str(self.root))
        self.assertTrue(value['accepted'])
        self.assertEqual(value['steps']['a']['runs'],1)

    def test_repeated_resume_never_replays_a_validation_with_lost_receipt(self):
        command=[sys.executable,'-c',"from pathlib import Path;p=Path('once');p.write_text(p.read_text()+'x' if p.exists() else 'x')"]
        spec=self.spec([{'id':'a','instruction':'Validate once','checks':[{'kind':'command','argv':command,'timeout':1}]}])
        store,value=self.create(spec);value,execution=task.reserve(store,value['revision'])
        store.update(value['revision'],lambda v:v['execution'].update(validation_started=True,state='validating'))
        subprocess.run(command,cwd=self.root,check=True)
        with mock.patch('task_runtime.os.kill',side_effect=ProcessLookupError), mock.patch('task_runtime.subprocess.run',side_effect=AssertionError('validation effect replayed')), mock.patch('task_worker.inspect_session',side_effect=AssertionError('native result cannot prove lost validator outcome')):
            task.resume(store);value=task.resume(store)
        self.assertEqual((self.root/'once').read_text(),'x')
        self.assertEqual(value['steps']['a']['state'],'reconcile')
        self.assertFalse(value['accepted'])

    def test_amendment_preserves_unknown_producer_even_when_constraint_was_derived(self):
        store,value,original,destination=self.denied_then_new_target()
        def execute(prompt,workspace,session,on_process,on_session,**options):
            is_a='Current step: Write new target' in prompt
            on_process(os.getpid())
            if is_a:os.link(original,destination)
            on_session('owned-a' if is_a else 'owned-b')
            return dict(self.worker(status='unknown' if is_a else 'completed'),session_id='owned-a' if is_a else 'owned-b',
                        error='coordinator_scope_changed_after_prompt' if is_a else None,prompt_sent=True)
        with mock.patch('task_worker.execute',side_effect=execute):value=task.run(store,value['revision'])
        destination.unlink();destination.write_bytes(b'wanted')
        spec=copy.deepcopy(value['spec']);spec['acceptance_version']='v3'
        value=self.mutate(store,'amend',spec=spec)
        self.assertEqual(value['steps']['a']['state'],'reconcile')
        self.assertEqual(task.partition(value)['ready'],['b'])
        self.assertTrue(any(h['wait']['kind']=='unresolved_prior_execution' for h in value['restrictions']))

    def test_accepted_receipt_does_not_authorize_automatic_restore_after_external_change(self):
        (self.root/'x').write_bytes(b'wanted')
        store,value=self.create(self.spec([{'id':'a','instruction':'Exact file','checks':[{'kind':'file','path':'x','sha256':hashlib.sha256(b'wanted').hexdigest()}]}]))
        with mock.patch('task_worker.execute',return_value=self.worker()):value=task.run(store,value['revision'])
        self.assertTrue(value['accepted'])
        (self.root/'x').write_bytes(b'external different content')
        self.assertFalse(task.view(store.read())['accepted'])
        with mock.patch('task_worker.execute',side_effect=AssertionError('external change blindly overwritten')):
            value=task.run(store,value['revision'])
        self.assertFalse(value['accepted'])
        self.assertIn('accepted_object_changed',value['steps']['a']['wait']['reason'])

    def test_final_text_without_current_result_continues_then_checks_actual_result(self):
        store, value = self.create()
        with mock.patch("task_worker.execute", side_effect=[self.worker("old installation PASS"), self.worker()]):
            value = task.run(store, value["revision"], max_turns=2)
        self.assertTrue(value["accepted"])
        self.assertEqual(len(value["history"]), 2)
        self.assertFalse(value["history"][0]["validation"]["passed"])
        self.assertEqual(value["steps"]["a"]["runs"], 2)

    def test_three_stages_continue_two_edges_without_operator_reprompt(self):
        steps = [{"id": name, "instruction": "Return " + name,
                  "depends_on": [] if index == 0 else ["abc"[index-1]],
                  "checks": [{"kind": "message", "equals": name}]} for index, name in enumerate("abc")]
        spec=self.spec(steps)
        from test_persistent_dependencies import bind_local_contract
        for producer,consumer in [('a','b'),('b','c')]:bind_local_contract(spec,'fixture',producer,consumer)
        store, value = self.create(spec)
        with mock.patch("task_worker.execute", side_effect=[self.worker(n) for n in "abc"]) as worker:
            value = task.run(store, value["revision"])
        self.assertTrue(value["accepted"])
        self.assertEqual(worker.call_count, 3)
        self.assertEqual(worker.call_args_list[1].args[2], "native-fixture-session")
        self.assertEqual(worker.call_args_list[2].args[2], "native-fixture-session")

    def test_one_blocked_branch_and_dependency_do_not_block_independent_branch(self):
        steps = [{"id": n, "instruction": "Return 42", "depends_on": ["a"] if n == "b" else [],
                  "checks": [{"kind": "message", "equals": "42"}]} for n in "abc"]
        store, value = self.create(self.spec(steps))
        value = self.mutate(store, "block", step="a", condition="known input unavailable", evidence_ref="native-item-input-gap")
        self.assertEqual(task.partition(value)["ready"], ["c"])
        with mock.patch("task_worker.execute", return_value=self.worker()) as worker:
            value = task.run(store, value["revision"])
        self.assertFalse(value["accepted"])
        self.assertEqual(value["steps"]["c"]["state"], "verified")
        self.assertEqual(worker.call_count, 1)

    def test_native_failure_not_retried_and_other_branch_runs(self):
        steps = [{"id": n, "instruction": "Return 42", "checks": [{"kind": "message", "equals": "42"}]} for n in "ab"]
        store, value = self.create(self.spec(steps))
        with mock.patch("task_worker.execute", side_effect=[self.worker(status="failed"), self.worker()]) as worker:
            value = task.run(store, value["revision"])
        self.assertEqual(worker.call_count, 2)
        self.assertEqual(value["steps"]["a"]["state"], "blocked")
        self.assertEqual(value["steps"]["b"]["state"], "verified")

    def test_no_progress_stops_boundedly_and_retains_failed_checks(self):
        store, value = self.create()
        with mock.patch("task_worker.execute", return_value=self.worker("same wrong result")) as worker:
            value = task.run(store, value["revision"], max_turns=4)
        self.assertEqual(worker.call_count, 2)
        self.assertEqual(value["steps"]["a"]["wait"]["kind"], "no_progress")
        self.assertFalse(value["accepted"])

    def test_current_revision_compare_and_swap_rejects_old_writer(self):
        store, value = self.create()
        self.mutate(store, "delivery", event="sent", source_ref="native-send-accepted")
        with self.assertRaisesRegex(task.TaskError, "revision conflict"):
            store.update(value["revision"], lambda v: v.update(accepted=True))
        self.assertFalse(store.read()["accepted"])

    def test_unrelated_fact_change_does_not_discard_current_execution(self):
        store, value = self.create()
        value, execution = task.reserve(store, value["revision"])
        self.mutate(store, "delivery", event="sent", source_ref="unrelated-native-send")
        value = task.finish(store, value["revision"], execution["id"], self.worker())
        self.assertEqual(value["steps"]["a"]["state"], "verified")
        self.assertEqual(value["delivery"]["sent"]["source_ref"], "unrelated-native-send")

    def test_explicit_block_survives_late_success_and_restart(self):
        store, value = self.create()
        value, execution = task.reserve(store, value["revision"])
        value = self.mutate(store, "block", step="a", condition="exact action cancelled", evidence_ref="original-cancellation-item")
        with self.assertRaises(task.TaskError):
            task.finish(store, value["revision"], execution["id"], self.worker())
        reopened = task.Store(self.a.codex_home, "fixture")
        with mock.patch("task_worker.execute", side_effect=AssertionError("cancelled action resumed")):
            value = task.resume(reopened)
        self.assertFalse(value["accepted"])
        self.assertEqual(value["steps"]["a"]["state"], "blocked")

    def test_session_registration_rebases_only_an_unrelated_fact(self):
        store, value = self.create()
        def native(prompt, workspace, session, on_session, **options):
            self.mutate(store, "delivery", event="sent", source_ref="independent-send")
            on_session("native-fixture-session")
            return self.worker()
        with mock.patch("task_worker.execute", side_effect=native):
            value = task.run(store, value["revision"])
        self.assertTrue(value["accepted"])
        self.assertEqual(value["native_session_id"], "native-fixture-session")
        self.assertEqual(value["delivery"]["sent"]["source_ref"], "independent-send")

    def test_resume_cannot_steal_a_live_controller_execution(self):
        store, value = self.create()
        task.reserve(store, value["revision"])
        before = store.path.read_bytes()
        with mock.patch("task_worker.inspect_session", side_effect=AssertionError("live execution inspected for takeover")):
            with self.assertRaisesRegex(task.TaskError, "controller_pid is still running"):
                task.resume(store)
        self.assertEqual(store.path.read_bytes(), before)

    def test_denial_and_explicit_block_cannot_be_revoked_by_effect_readback(self):
        wanted = b"one effect"
        steps = [{"id": n, "instruction": "Create " + n, "checks": [{"kind": "file", "path": n, "sha256": hashlib.sha256(wanted).hexdigest()}]} for n in "ab"]
        store, value = self.create(self.spec(steps))
        task.reserve(store, value["revision"])
        (self.root / "a").write_bytes(wanted)
        value = self.mutate(store, "approval", step="a", decision="denied", source_ref="original-specific-denial")
        self.assertEqual(task.partition(value)["ready"], ["b"])
        with self.assertRaisesRegex(task.TaskError, "cannot revoke"):
            self.mutate(store, "reconcile", step="a", source_ref="actual-effect-exists")
        value = self.mutate(store, "block", step="a", condition="exact cancellation", evidence_ref="original-cancellation")
        with self.assertRaisesRegex(task.TaskError, "cannot revoke"):
            self.mutate(store, "reconcile", step="a", source_ref="actual-effect-exists")
        self.assertFalse(store.read()["accepted"])

    def test_native_decline_cannot_be_cleared_by_file_reconciliation(self):
        (self.root/'effect').write_bytes(b'existing')
        spec=self.spec([{'id':'a','instruction':'known effect','checks':[{'kind':'file','path':'effect','sha256':hashlib.sha256(b'existing').hexdigest()}]}])
        store,value=self.create(spec)
        value,execution=task.reserve(store,value['revision'])
        failed=dict(self.worker(status='failed'),error='native_permission_declined')
        value=task.finish(store,value['revision'],execution['id'],failed)
        self.assertEqual(value['steps']['a']['wait']['kind'],'explicit_denial')
        with self.assertRaisesRegex(task.TaskError,'cannot revoke'):
            self.mutate(store,'reconcile',step='a',source_ref='existing-file-readback')
        with mock.patch('task_worker.execute',side_effect=AssertionError('denied effect repeated')):
            value=task.resume(store)
        self.assertFalse(value['accepted'])

    def test_unrelated_amendment_preserves_denial_and_runs_independent_branch(self):
        steps=[{'id':n,'instruction':'Return '+n,'checks':[{'kind':'message','equals':'42'}]} for n in 'ab']
        store,value=self.create(self.spec(steps))
        self.mutate(store,'approval',step='a',decision='denied',source_ref='original-exact-denial')
        amended=self.spec(steps);amended['acceptance_version']='v2';amended['requirement']+='; change reporting only'
        value=self.mutate(store,'amend',spec=amended)
        self.assertEqual(task.partition(value)['ready'],['b'])
        self.assertEqual(value['steps']['a']['wait']['source_ref'],'original-exact-denial')
        with mock.patch('task_worker.execute',return_value=self.worker()) as worker:
            value=task.run(store,value['revision'])
        self.assertEqual(worker.call_count,1)
        self.assertEqual(value['steps']['b']['state'],'verified')
        self.assertFalse(value['accepted'])

    def test_denial_survives_removed_step_and_renamed_same_file_effect(self):
        def step(name,path):return {'id':name,'instruction':'Create the requested output','checks':[{'kind':'file','path':path,'sha256':'1'*64}]}
        store,value=self.create(self.spec([step('a','a.txt')]))
        self.mutate(store,'approval',step='a',decision='denied',source_ref='denied-file-a')
        value=self.mutate(store,'amend',spec=self.spec([step('b','b.txt')]))
        self.assertEqual(task.partition(value)['ready'],['b'])
        value=self.mutate(store,'amend',spec=self.spec([step('renamed','./a.txt'),step('b','b.txt')]))
        self.assertEqual(task.partition(value)['ready'],['b'])
        self.assertEqual(value['steps']['renamed']['wait']['source_ref'],'denied-file-a')

    def test_same_file_denial_survives_workspace_rebase_and_lexical_aliases(self):
        cases=[('parent-to-child','nested/result.txt','result.txt'),
               ('child-to-parent','result.txt','./nested//result.txt'),
               ('dot-workspace','nested/result.txt','./nested/result.txt')]
        for index,(name,old_path,new_path) in enumerate(cases):
            with self.subTest(case=name):
                base=self.root/name;nested=base/'nested';nested.mkdir(parents=True)
                (nested/'result.txt').write_bytes(b'wanted')
                old_workspace=nested if name=='child-to-parent' else base
                new_workspace=nested if name=='parent-to-child' else base
                if name=='dot-workspace':new_workspace=str(nested)+'/..'
                old=self.spec([{'id':'a','instruction':'Original exact file action','checks':[{'kind':'file','path':old_path,'sha256':hashlib.sha256(b'wanted').hexdigest()}]}]);old['workspace']=str(old_workspace)
                store=task.Store(self.a.codex_home,'anchor-'+str(index));store.create('anchor-'+str(index),old)
                self.mutate(store,'approval',step='a',decision='denied',source_ref='original-file-denial')
                other=Path(new_workspace)/'other.txt';other.write_bytes(b'wanted')
                amended=self.spec([{'id':'renamed','instruction':'New display words for the target','checks':[{'kind':'file','path':new_path,'sha256':hashlib.sha256(b'wanted').hexdigest()}]},
                                   {'id':'b','instruction':'Other actual target','checks':[{'kind':'file','path':'other.txt','sha256':hashlib.sha256(b'wanted').hexdigest()}]}]);amended['workspace']=str(new_workspace);amended['acceptance_version']='v2'
                value=self.mutate(store,'amend',spec=amended)
                self.assertEqual(task.partition(value)['ready'],['b'])
                with mock.patch('task_worker.execute',return_value=self.worker()) as worker:value=task.run(store,value['revision'])
                self.assertEqual(worker.call_count,1)
                self.assertEqual(value['steps']['renamed']['runs'],0)
                self.assertEqual(value['steps']['renamed']['wait']['source_ref'],'original-file-denial')
                self.assertEqual(value['steps']['b']['state'],'verified')
                self.assertFalse(value['accepted'])

    def test_existing_object_denial_survives_hardlink_and_rename_aliases(self):
        for index,kind in enumerate(['existing-hardlink','later-hardlink','rename']):
            with self.subTest(kind=kind):
                base=self.root/kind;child=base/'child';child.mkdir(parents=True)
                original=base/'original';original.write_bytes(b'wanted');alias=child/'alias'
                if kind=='existing-hardlink':os.link(original,alias)
                old=self.spec([{'id':'a','instruction':'Exact original object','checks':[{'kind':'file','path':'original','sha256':hashlib.sha256(b'wanted').hexdigest()}]}]);old['workspace']=str(base)
                store=task.Store(self.a.codex_home,'object-'+str(index));store.create('object-'+str(index),old)
                denied=self.mutate(store,'approval',step='a',decision='denied',source_ref='original-object-denial')
                recorded=denied['restrictions'][0]['file_objects']
                self.assertTrue(recorded)
                if kind=='later-hardlink':os.link(original,alias)
                elif kind=='rename':original.rename(alias)
                self.assertIn({'device':alias.stat().st_dev,'inode':alias.stat().st_ino},recorded)
                new=self.spec([{'id':'new-id','instruction':'Different wording and anchor','checks':[{'kind':'file','path':'alias','sha256':hashlib.sha256(b'wanted').hexdigest()}]}]);new['workspace']=str(child)
                value=self.mutate(store,'amend',spec=new)
                with mock.patch('task_worker.execute',side_effect=AssertionError('same denied object dispatched')):value=task.run(store,value['revision'])
                self.assertEqual(value['steps']['new-id']['state'],'blocked')
                self.assertEqual(value['steps']['new-id']['runs'],0)
                self.assertFalse(value['accepted'])

    def test_filesystem_case_and_unicode_aliases_follow_actual_object_identity(self):
        original=self.root/'Target-é.txt';original.write_bytes(b'same bytes')
        variants=['TARGET-É.TXT',unicodedata.normalize('NFD',original.name)]
        for index,name in enumerate(variants):
            with self.subTest(name=name):
                alias=self.root/name
                if not alias.exists():alias.write_bytes(b'same bytes')
                same=os.path.samefile(original,alias)
                old=self.spec([{'id':'a','instruction':'Original object','checks':[{'kind':'file','path':original.name,'sha256':hashlib.sha256(b'same bytes').hexdigest()}]}])
                store=task.Store(self.a.codex_home,'filesystem-'+str(index));store.create('filesystem-'+str(index),old)
                self.mutate(store,'approval',step='a',decision='denied',source_ref='exact-original-object')
                new=self.spec([{'id':'renamed','instruction':'Alias presentation','checks':[{'kind':'file','path':name,'sha256':hashlib.sha256(b'same bytes').hexdigest()}]}])
                value=self.mutate(store,'amend',spec=new)
                self.assertEqual(task.partition(value)['ready'],[] if same else ['renamed'])

    def test_same_bytes_and_labels_do_not_bind_a_distinct_file_target(self):
        left=self.root/'left';right=self.root/'right';left.mkdir();right.mkdir()
        for base in [left,right]:(base/'output').write_bytes(b'same bytes')
        self.assertFalse(os.path.samefile(left/'output',right/'output'))
        old=self.spec([{'id':'a','instruction':'Exact output action','checks':[{'kind':'file','path':'output','sha256':hashlib.sha256(b'same bytes').hexdigest()}]}]);old['workspace']=str(left)
        store,value=self.create(old)
        self.mutate(store,'approval',step='a',decision='denied',source_ref='left-object-only')
        new=copy.deepcopy(old);new['workspace']=str(right);new['acceptance_version']='v2'
        value=self.mutate(store,'amend',spec=new)
        self.assertEqual(task.partition(value)['ready'],['a'])
        with mock.patch('task_worker.execute',return_value=self.worker()):value=task.run(store,value['revision'])
        self.assertTrue(value['accepted'])
        self.assertTrue(value['restrictions'])

    def test_unsupported_symlink_or_escape_is_rejected_without_clearing_denial(self):
        original=self.root/'original';original.write_bytes(b'wanted')
        (self.root/'alias').symlink_to(original)
        directory=self.root/'directory';directory.mkdir();(self.root/'directory-alias').symlink_to(directory,target_is_directory=True)
        old=self.spec([{'id':'a','instruction':'Exact file','checks':[{'kind':'file','path':'original','sha256':hashlib.sha256(b'wanted').hexdigest()}]}])
        store,value=self.create(old);self.mutate(store,'approval',step='a',decision='denied',source_ref='original-denial')
        for workspace,path in [(str(self.root),'alias'),(str(self.root),'directory/../original'),(str(self.root/'directory-alias'),'original')]:
            with self.subTest(workspace=workspace,path=path):
                new=copy.deepcopy(old);new['workspace']=workspace;new['steps'][0]['checks'][0]['path']=path
                before=store.path.read_bytes()
                with self.assertRaises(task.cf.SyncError):self.mutate(store,'amend',spec=new)
                self.assertEqual(store.path.read_bytes(),before)

    def test_validator_local_failure_blocks_only_its_branch_without_retry(self):
        checks=[{'kind':'command','argv':[str(self.root/'missing-validator')],'timeout':1}]
        steps=[{'id':'a','instruction':'Return A','checks':checks},
               {'id':'b','instruction':'Return 42 independently','checks':[{'kind':'message','equals':'42'}]}]
        store,value=self.create(self.spec(steps))
        with mock.patch('task_worker.execute',return_value=self.worker()) as worker:
            value=task.run(store,value['revision'],max_turns=4)
        self.assertEqual(worker.call_count,2)
        self.assertEqual(value['steps']['a']['runs'],1)
        self.assertEqual(value['steps']['a']['wait']['kind'],'validation_error')
        self.assertEqual(value['steps']['a']['validation']['checks'][0]['error_type'],'FileNotFoundError')
        self.assertEqual(value['steps']['b']['state'],'verified')
        self.assertIsNone(value['execution'])
        self.assertFalse(value['accepted'])

    def test_validator_timeout_is_not_replayed_and_independent_branch_continues(self):
        marker=self.root/'validation-count'
        script="from pathlib import Path;import time;p=Path('validation-count');p.write_text(p.read_text()+'x' if p.exists() else 'x');time.sleep(5)"
        steps=[{'id':'a','instruction':'Return A','checks':[{'kind':'command','argv':[sys.executable,'-c',script],'timeout':1}]},
               {'id':'b','instruction':'Return 42 independently','checks':[{'kind':'message','equals':'42'}]}]
        store,value=self.create(self.spec(steps))
        with mock.patch('task_worker.execute',return_value=self.worker()):value=task.run(store,value['revision'],max_turns=4)
        self.assertEqual(marker.read_text(),'x')
        self.assertEqual(value['steps']['a']['wait']['kind'],'validation_error')
        self.assertEqual(value['steps']['b']['state'],'verified')

    def test_late_old_input_result_cannot_overwrite_amendment(self):
        store, value = self.create()
        value, execution = task.reserve(store, value["revision"])
        spec = self.spec(); spec["request_id"] = "request-2"; spec["acceptance_version"] = "v2"
        value = self.mutate(store, "amend", spec=spec)
        before = store.path.read_bytes()
        with self.assertRaises(task.TaskError):
            task.finish(store, value["revision"], execution["id"], self.worker())
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(value["steps"]["a"]["state"], "reconcile")
        self.assertEqual(value["input_revision"], 2)

    def test_missing_validator_cannot_complete_or_accept(self):
        store, value = self.create()
        value, execution = task.reserve(store, value["revision"])
        with mock.patch('task_runtime.validate', return_value=None):
            with self.assertRaisesRegex(task.TaskError, "validator did not run"):
                task.finish(store, value["revision"], execution["id"], self.worker())
        with self.assertRaises(task.TaskError):
            self.mutate(store, "accept")
        self.assertFalse(store.read()["accepted"])

    def test_delivery_accepted_does_not_mean_read_or_human_decision(self):
        steps = self.spec()["steps"]; steps[0]["approval_required"] = True
        store, value = self.create(self.spec(steps))
        value = self.mutate(store, "delivery", event="sent", source_ref="tool-send-accepted")
        self.assertFalse(value["delivery"]["read"])
        self.assertIsNone(value["delivery"]["user_decision"])
        value = self.mutate(store, "delivery", event="user_accepted", source_ref="stop-generated-user-shaped-message")
        self.assertIsNone(value["delivery"]["user_decision"])
        self.assertEqual(task.partition(value)["ready"], [])
        with self.assertRaisesRegex(task.TaskError, "cannot authenticate"):
            self.mutate(store, "approval", step="a", decision="approved", source_ref="assistant-ACK")

    def test_actual_file_effect_recovered_without_worker_or_replay(self):
        wanted = b"one actual effect\n"
        spec = self.spec([{"id": "a", "instruction": "Produce effect once", "checks": [{"kind": "file", "path": "effect.txt", "sha256": hashlib.sha256(wanted).hexdigest()}]}])
        store, value = self.create(spec)
        value, execution = task.reserve(store, value["revision"])
        (self.root / "effect.txt").write_bytes(wanted)
        # New Store instance only tests disk state; real PID restart is a separate drill.
        reopened = task.Store(self.a.codex_home, "fixture")
        with mock.patch("task_worker.execute", side_effect=AssertionError("must not repeat effect")), mock.patch('task_runtime.os.kill',side_effect=ProcessLookupError):
            value = self.mutate(reopened, "reconcile", step="a", source_ref="actual-file-target-readback")
        self.assertEqual(value["steps"]["a"]["state"], "verified")
        self.assertEqual((self.root / "effect.txt").read_bytes(), wanted)

    def test_reconciliation_cannot_rerun_a_possible_effectful_check_command(self):
        spec = self.spec([{"id": "a", "instruction": "test", "checks": [{"kind": "command", "argv": [sys.executable, "-c", "raise AssertionError('not a passive recovery read')"], "timeout": 1}]}])
        store, value = self.create(spec)
        task.reserve(store, value["revision"])
        with mock.patch("task_runtime.subprocess.run", side_effect=AssertionError("unsafe replay")), mock.patch('task_runtime.os.kill',side_effect=ProcessLookupError):
            with self.assertRaisesRegex(task.TaskError, "only reads exact file effects"):
                self.mutate(store, "reconcile", step="a", source_ref="original-native-state")

    def test_verified_file_changed_after_run_cannot_be_accepted(self):
        wanted = b"expected"
        (self.root / "x").write_bytes(wanted)
        store, value = self.create(self.spec([{"id": "a", "instruction": "x", "checks": [{"kind": "file", "path": "x", "sha256": hashlib.sha256(wanted).hexdigest()}]}], review_required=True))
        with mock.patch("task_worker.execute", return_value=self.worker()):
            value = task.run(store, value["revision"])
        (self.root / "x").write_bytes(b"changed")
        with self.assertRaises(task.TaskError):
            self.mutate(store, "accept")

    def test_review_and_load_need_same_current_input_and_real_candidate_tree(self):
        store, value = self.create(self.spec(review_required=True, load_required=True))
        with mock.patch("task_worker.execute", return_value=self.worker()):
            value = task.run(store, value["revision"])
        with self.assertRaisesRegex(task.TaskError, "review missing"):
            self.mutate(store, "accept")
        report = self.root / "review.json"
        report.write_text(json.dumps({"task_input_sha256": "0" * 64, "verdict": "PASS", "reviewer_thread": "different-independent-thread", "candidate_tree": "1" * 40}))
        with self.assertRaisesRegex(task.TaskError, "different task input"):
            self.mutate(store, "review", report_path=str(report), report_sha256=hashlib.sha256(report.read_bytes()).hexdigest())

    def test_version_bound_installed_entry_contains_task_engine_and_rejects_accept_without_checks(self):
        revision = fixtures.seed_runtime(self.repo)
        runtime.install(self.a, revision)
        argv = [sys.executable, str(runtime.launcher_path(self.a)), "--home", str(self.a.home), "task"]
        def call(payload):
            return subprocess.run(argv, input=json.dumps(payload), text=True, capture_output=True, timeout=10)
        registered = call({"operation": "register", "task_id": "entry", "spec": self.spec()})
        self.assertEqual(registered.returncode, 0, registered.stderr)
        value = json.loads(registered.stdout)
        denied = call({"operation": "accept", "task_id": "entry", "expected_revision": value["revision"]})
        self.assertEqual(denied.returncode, 1)
        self.assertIn("mandatory current acceptance evidence missing", denied.stdout)
        self.assertEqual(denied.stderr, "")

    def test_seven_to_nine_module_upgrade_and_rollback_preserve_task_state(self):
        runtime_file = self.repo / 'scripts/framework_runtime.py'
        cli_file = self.repo / 'scripts/codex_framework.py'
        original_runtime, original_cli = runtime_file.read_bytes(), cli_file.read_bytes()
        additions = {n: (self.repo/'scripts'/n).read_bytes() for n in ['task_runtime.py','task_worker.py']}
        tree = ast.parse(original_runtime)
        declaration = next(n for n in tree.body if isinstance(n,ast.Assign) and isinstance(n.targets[0],ast.Name) and n.targets[0].id=='MODULES')
        lines = original_runtime.decode().splitlines(keepends=True)
        lines[declaration.lineno-1:declaration.end_lineno] = ['MODULES = '+repr(runtime.LEGACY_MODULES)+'\n']
        runtime_file.write_text(''.join(lines))
        cli_file.write_text(original_cli.decode().replace(', "task"))', '))').replace('    if args.command == "task":\n        from task_runtime import dispatch\n        return dispatch(codex_home or (args.home or Path.home()) / ".codex")\n',''))
        for name in additions:(self.repo/'scripts'/name).unlink()
        legacy = fixtures.seed_runtime(self.repo)
        runtime.install(self.a,legacy)
        self.assertEqual(len(runtime.load(runtime.version_path(self.a,legacy)/'manifest.json')['files']),8)
        runtime_file.write_bytes(original_runtime);cli_file.write_bytes(original_cli)
        for name,raw in additions.items():(self.repo/'scripts'/name).write_bytes(raw)
        fixtures.run_git(self.repo,'add','.');fixtures.run_git(self.repo,'commit','-m','add durable task entry')
        current=fixtures.run_git(self.repo,'rev-parse','HEAD')
        runtime.install(self.a,current,expected_runtime=legacy)
        state=self.a.codex_home/'task-runtime/preserved.json';state.parent.mkdir();state.write_text('preserved node state')
        runtime.install(self.a,legacy,expected_runtime=current,rollback=True)
        self.assertEqual(runtime.active(self.a)['revision'],legacy)
        self.assertEqual(state.read_text(),'preserved node state')
        normal=subprocess.run([sys.executable,str(runtime.launcher_path(self.a)),'--home',str(self.a.home),'sync-status'],capture_output=True,text=True,timeout=10)
        self.assertEqual(normal.returncode,0,normal.stdout+normal.stderr)
        self.assertEqual(json.loads(normal.stdout)['runtime']['tool_revision'],legacy)

    def prior_managed_loader_fixture(self, literal=True):
        path=self.repo/'scripts/framework_runtime.py'
        current=path.read_text()
        tree=ast.parse(current)
        assignments={node.targets[0].id:node for node in tree.body if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name)}
        template='HOME = %r\nMODULES = %r\n# synthetic previously approved managed template\n'
        legacy=current.replace(ast.get_source_segment(current,assignments['MODULES'].value),repr(runtime.LEGACY_MODULES),1)
        expression=repr(template) if literal else "''.join(["+repr(template)+"])"
        legacy=legacy.replace(ast.get_source_segment(current,assignments['LOADER'].value),expression,1)
        marker=self.root/'old-source-was-executed'
        legacy+='\nfrom pathlib import Path\nPath('+repr(str(marker))+').write_text("unexpected")\n'
        path.write_text(legacy)
        prior=fixtures.seed_runtime(self.repo)
        if literal:
            runtime.install(self.a,prior)
        else:
            # Recreate a package already installed by an older installer. The
            # current installer rejects this expression even on first install;
            # only fixture setup supplies the historical rendered launcher.
            rendered=(template%(str(self.a.codex_home),runtime.LEGACY_MODULES)).encode()
            with mock.patch.object(runtime,'prior_loader_bytes',return_value=rendered):
                runtime.install(self.a,prior)
        # The initial explicit package install runs its approved smoke commands.
        # Only the later old-template recognition must avoid executing old code.
        marker.unlink()
        launcher=runtime.launcher_path(self.a)
        launcher.write_bytes((template%(str(self.a.codex_home),runtime.LEGACY_MODULES)).encode())
        path.write_text(current)
        fixtures.run_git(self.repo,'add','scripts/framework_runtime.py')
        fixtures.run_git(self.repo,'commit','-m','new exact runtime fixture')
        new=fixtures.run_git(self.repo,'rev-parse','HEAD')
        return prior,new,marker

    def test_exact_prior_managed_loader_migrates_without_executing_old_source(self):
        prior,new,marker=self.prior_managed_loader_fixture()
        self.assertEqual(len(runtime.load(runtime.version_path(self.a,prior)/'manifest.json')['files']),8)
        result=runtime.install(self.a,new,expected_runtime=prior)
        self.assertEqual(result['status'],'installed')
        self.assertEqual(runtime.active(self.a)['revision'],new)
        self.assertEqual(runtime.launcher_path(self.a).read_bytes(),runtime.loader_bytes(self.a))
        self.assertEqual(len(runtime.load(runtime.version_path(self.a,new)/'manifest.json')['files']),len(runtime.MODULES)+1)
        self.assertFalse(marker.exists())

    def test_prior_loader_migration_preserves_unknown_or_unverified_state(self):
        prior,new,marker=self.prior_managed_loader_fixture()
        launcher=runtime.launcher_path(self.a);active=runtime.pointer(self.a)
        approved=runtime.root(self.a)/'approved.json'
        module=runtime.version_path(self.a,prior)/'scripts/framework_runtime.py'
        original={p:p.read_bytes() for p in [launcher,active,approved,module]}
        mutations=[(launcher,original[launcher]+b'\n# local customization\n'),
                   (module,original[module]+b'\n# unverified package mutation\n'),
                   (approved,runtime.encoded({})),
                   (active,runtime.encoded(dict(json.loads(original[active]),manifest_sha256='f'*64)))]
        for target,changed in mutations:
            with self.subTest(target=str(target)):
                for path,raw in original.items():path.write_bytes(raw)
                target.write_bytes(changed)
                before_active=active.read_bytes();before_loader=launcher.read_bytes()
                with self.assertRaises(fixtures.cf.SyncError):runtime.install(self.a,new,expected_runtime=prior)
                self.assertEqual(active.read_bytes(),before_active)
                self.assertEqual(launcher.read_bytes(),before_loader)
                self.assertFalse(runtime.journal(self.a).exists())
        self.assertFalse(marker.exists())

    def test_prior_loader_change_after_recognition_cannot_be_blessed_as_before_image(self):
        prior,new,marker=self.prior_managed_loader_fixture()
        launcher=runtime.launcher_path(self.a)
        before_active=runtime.pointer(self.a).read_bytes()
        unknown=b"owned intervening local launcher edit\n"
        recognize=runtime.prior_loader_bytes
        def changed_after_recognition(framework,pointer):
            known=recognize(framework,pointer)
            launcher.write_bytes(unknown)
            return known
        with mock.patch('framework_runtime.prior_loader_bytes',side_effect=changed_after_recognition):
            with self.assertRaisesRegex(fixtures.cf.SyncError,'入口核对后改变'):
                runtime.install(self.a,new,expected_runtime=prior)
        self.assertEqual(launcher.read_bytes(),unknown)
        self.assertEqual(runtime.pointer(self.a).read_bytes(),before_active)
        self.assertFalse(runtime.journal(self.a).exists())
        self.assertFalse(marker.exists())

    def test_prior_loader_template_requires_literal_without_running_expression(self):
        prior,new,marker=self.prior_managed_loader_fixture(literal=False)
        before=runtime.launcher_path(self.a).read_bytes()
        with self.assertRaisesRegex(fixtures.cf.SyncError,'模板无法静态核对'):
            runtime.install(self.a,new,expected_runtime=prior)
        self.assertEqual(runtime.active(self.a)['revision'],prior)
        self.assertEqual(runtime.launcher_path(self.a).read_bytes(),before)
        self.assertFalse(marker.exists())

    def test_manifest_cannot_disguise_current_module_code_as_legacy_seven(self):
        revision=fixtures.seed_runtime(self.repo);runtime.install(self.a,revision)
        directory=runtime.version_path(self.a,revision);m=runtime.load(directory/'manifest.json')
        for name in set(runtime.MODULES)-set(runtime.LEGACY_MODULES):
            (directory/'scripts'/name).unlink();del m['files']['scripts/'+name]
        raw=runtime.encoded(m);(directory/'manifest.json').write_bytes(raw)
        active=runtime.active(self.a);active['manifest_sha256']=runtime.digest(raw)
        runtime.pointer(self.a).write_bytes(runtime.encoded(active))
        result=subprocess.run([sys.executable,str(runtime.launcher_path(self.a)),'--home',str(self.a.home),'status'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1)
        self.assertIn('closure mismatch',result.stdout)

    def test_review_candidate_and_actual_load_binding_rejects_changed_release(self):
        revision=fixtures.seed_runtime(self.repo);runtime.install(self.a,revision)
        store,value=self.create(self.spec(review_required=True,load_required=True))
        with mock.patch('task_worker.execute',return_value=self.worker()):value=task.run(store,value['revision'])
        report=self.root/'review.json'
        report.write_text(json.dumps({'task_input_sha256':value['acceptance_sha256'],'verdict':'PASS',
          'reviewer_thread':'synthetic-independent-identity','candidate_tree':fixtures.run_git(self.repo,'rev-parse','HEAD^{tree}')}))
        value=self.mutate(store,'review',report_path=str(report),report_sha256=hashlib.sha256(report.read_bytes()).hexdigest())
        value=task.change(store,{'operation':'loaded','expected_revision':value['revision'],'revision':revision,'framework':self.a})
        fixtures.run_git(self.repo,'commit','--allow-empty','-m','same tree different release identity')
        later=fixtures.run_git(self.repo,'rev-parse','HEAD');runtime.install(self.a,later,expected_runtime=revision)
        with self.assertRaisesRegex(task.TaskError,'loaded release changed'):
            self.mutate(store,'accept')
        runtime.install(self.a,revision,expected_runtime=later,rollback=True)
        value=self.mutate(store,'accept')
        self.assertTrue(value['accepted'])
        self.assertEqual(value['loaded']['review_sha256'],value['review']['sha256'])
        self.assertIsNone(value['delivery']['user_decision'])
