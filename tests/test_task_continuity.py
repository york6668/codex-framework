"""Original-result continuity through task entry; no model or native query.

Native terminal files below are explicit fixtures, not proof of live recovery.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
from unittest import mock

import test_framework as fixtures
import task_runtime as task
import task_coordination as coord
import framework_runtime as runtime


class ContinuityTests(fixtures.FixtureCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(mock.patch.stopall)
        self.model=mock.patch('task_worker.execute',side_effect=AssertionError('no model dispatch')).start()
        self.native=mock.patch('task_worker.inspect_session',side_effect=AssertionError('no new native query')).start()
        self.origin={'operation_id':'original-domain-operation','attempt_id':'original-domain-attempt','session_id':'original-session','turn_id':'original-turn'}

    def ref(self,name,content):
        p=self.root/name;p.write_bytes(content if isinstance(content,bytes) else task.encoded(content));return {'path':name,'sha256':task.digest(p.read_bytes())}

    def create(self,identity='current',**extra):
        original=self.ref('input.txt',b'already acquired public input')
        spec={'request_id':'current-request','requirement':'Answer the full original question','acceptance_version':'v1','workspace':str(self.root),
              'execution_mode':'external','max_runs':1,
              'steps':[{'id':'a','instruction':'Read acquired input only','checks':[{'kind':'file',**original}]},
                       {'id':'independent','instruction':'Independent same input','checks':[{'kind':'file',**self.ref('independent.txt',b'independent acquired input')}]}],
              'request_identity':{'thread_id':'owner','turn_id':'current-request-turn','message_id':'current-request-message'},
              'assignment':{'owner':'owner','role':'implementation','delivery_owner':'parent','covers':[]},
              'result_scope':{'object_ref':'original-business-object','goal':'Full original goal','source_ref':'fixture:original-request',
                              'obligations':[{'id':'analysis','acceptance':'Applicable method and actual result'},
                                             {'id':'later','acceptance':'Original unresolved follow-up'}]},**extra}
        store=task.Store(self.a.codex_home,identity);return store,store.create(identity,spec)

    def change(self,store,operation,**fields):
        return task.change(store,{'operation':operation,'expected_revision':store.read()['revision'],**fields})

    def report(self,value,**fields):
        return {'task_id':value['task_id'],'request_id':value['spec']['request_id'],'input_revision':value['input_revision'],
                'task_input_sha256':value['acceptance_sha256'],'request_identity':value['spec']['request_identity'],**fields}

    def result(self,store,status='answered',method='applicable',ids=('analysis','later'),origins=None):
        value=store.read();ref=self.ref('answer.txt',b'Actual answer after execution')
        report=self.report(value,object_ref='original-business-object',answer=ref,
            native_result={'thread_id':'owner','turn_id':'actual-final-turn','final_ref':'fixture:actual-native-final','final_sha256':ref['sha256']},
            answers=[{'id':i,'status':status,'method_fit':method,'evidence':[self.ref('execution.txt',b'Observed facts, reasoning, competing explanation, limits')]} for i in ids],
            origins=[] if origins is None else origins)
        return self.ref('actual-result.json',report)

    def review(self,store,reference,ids=('analysis','later'),verdict='PASS'):
        return self.ref('substantive-review.json',self.report(store.read(),object_ref='original-business-object',
            result_sha256=reference['sha256'],reviewer_thread='independent-reviewer',
            assessments=[{'id':i,'verdict':verdict,'reason':'Actual domain review of original question and method'} for i in ids]))

    def finite(self,store):
        value=store.read();ref=self.ref('finite-check.json',self.report(value,verdict='PASS'))
        return task.observe_external(store,value['revision'],ref)

    def checkpoint(self,store,effects=None):
        return self.change(store,'checkpoint',step='a',origin=self.origin,effects=effects or [])

    def terminal(self,status='completed',origin=None):
        return self.ref('terminal.json',{'origin':origin or self.origin,'status':status,'source_ref':'fixture:original-native-read'})

    def test_finite_pass_and_model_final_cannot_close_original_goal(self):
        store,_=self.create();value=self.finite(store)
        self.assertFalse(value['accepted']);self.assertTrue(all(s['state']=='verified' for s in value['steps'].values()))
        with self.assertRaisesRegex(task.TaskError,'pending'):self.change(store,'accept')
        projection=task.view(value)['result_dimensions'];self.assertEqual(projection['business_outcome'],'pending')
        self.assertEqual(projection['pending_obligations'],['analysis','later'])

    def test_actual_current_result_then_independent_scope_review(self):
        store,_=self.create();self.finite(store);ref=self.result(store)
        value=self.change(store,'collect-result',**ref);self.assertFalse(value['accepted'])
        self.assertEqual(value['result_state']['pending'],['analysis','later'])
        self.change(store,'review-result',**self.review(store,ref));value=self.change(store,'accept')
        self.assertTrue(value['accepted']);self.assertEqual(value['result_state']['pending'],[])
        self.assertEqual((self.model.call_count,self.native.call_count),(0,0))

    def test_narrow_correct_answer_preserves_unanswered_original_obligation(self):
        store,_=self.create();self.finite(store);ref=self.result(store,ids=('analysis',))
        self.change(store,'collect-result',**ref);value=self.change(store,'review-result',**self.review(store,ref,ids=('analysis',)))
        self.assertEqual(value['result_state']['pending'],['later'])
        with self.assertRaises(task.TaskError):self.change(store,'accept')

    def test_partial_unknown_or_inapplicable_cannot_receive_substantive_pass(self):
        for status,method in [('partial','applicable'),('unknown','unknown'),('answered','limited'),('answered','not_applicable')]:
            store,_=self.create('case-'+status+'-'+method);ref=self.result(store,status,method)
            self.change(store,'collect-result',**ref)
            with self.assertRaisesRegex(task.TaskError,'partial/unknown/inapplicable'):self.change(store,'review-result',**self.review(store,ref))
            self.assertFalse(store.read()['accepted'])

    def test_old_true_pass_cannot_answer_current_native_request(self):
        store,_=self.create();ref=self.result(store)
        p=self.root/ref['path'];data=json.loads(p.read_bytes());data['request_identity']['turn_id']='old-btc-turn'
        bad=self.ref(ref['path'],data)
        with self.assertRaisesRegex(task.TaskError,'current project/task/native'):self.change(store,'collect-result',**bad)
        self.assertFalse(store.read()['accepted'])

    def test_changed_actual_answer_invalidates_review_and_acceptance(self):
        store,_=self.create();self.finite(store);ref=self.result(store)
        self.change(store,'collect-result',**ref);self.change(store,'review-result',**self.review(store,ref));self.change(store,'accept')
        (self.root/'answer.txt').write_text('changed answer')
        self.assertFalse(task.view(store.read())['accepted'])
        with self.assertRaises(task.TaskError):self.change(store,'accept')

    def test_amend_keeps_unfinished_goal_and_actual_result_history(self):
        store,_=self.create();ref=self.result(store,ids=('analysis',));self.change(store,'collect-result',**ref)
        spec=copy.deepcopy(store.read()['spec']);spec['result_scope']['obligations']=spec['result_scope']['obligations'][:1]
        value=self.change(store,'amend',spec=spec)
        self.assertEqual(value['result_state']['pending'],['analysis','later'])
        self.assertEqual(value['history'][-1]['superseded_input']['result_state']['result'],ref)
        spec.pop('result_scope')
        with self.assertRaisesRegex(task.TaskError,'cannot be removed'):self.change(store,'amend',spec=spec)

    def test_I01_acquired_input_interruption_retains_original_without_fetch(self):
        store,_=self.create();ref=self.ref('public-acquired.txt',b'already acquired real-package surrogate')
        self.checkpoint(store,[{'kind':'input',**ref}]);first=store.read()['steps']['a']['continuation']
        for _ in range(2):self.change(store,'continue',step='a')
        value=store.read();cp=value['steps']['a']['continuation'];self.assertEqual(cp['origin'],first['origin']);self.assertEqual(cp['effects'],first['effects'])
        self.assertEqual(task.partition(value)['ready'],['independent'])
        self.assertFalse(any(cp[k] for k in ('may_launch','may_commit','may_send')))
        self.assertEqual(self.model.call_count+self.native.call_count,0)

    def test_I02_freeze_interruption_preserves_first_bytes_and_identity(self):
        store,_=self.create();ref=self.ref('first-freeze.json',{'created_at':'real-first-clock-in-fixture','criteria':['original-condition']})
        self.checkpoint(store,[{'kind':'freeze',**ref}]);self.change(store,'continue',step='a',terminal_ref=self.terminal())
        self.assertEqual(store.read()['steps']['a']['continuation']['next_action'],'consume_original_result')
        (self.root/'first-freeze.json').write_text('rewritten criteria')
        with self.assertRaises(task.TaskError):self.change(store,'continue',step='a')
        altered=self.ref('first-freeze.json',b'new same-path bytes')
        with self.assertRaisesRegex(task.TaskError,'cannot be overwritten'):self.checkpoint(store,[{'kind':'freeze',**altered}])

    def test_I03_unknown_and_running_never_dispatch_commit_or_send(self):
        store,_=self.create();self.checkpoint(store)
        for status in ('unknown','running','unknown'):
            value=self.change(store,'continue',step='a',terminal_ref=self.terminal(status));cp=value['steps']['a']['continuation']
            self.assertFalse(any(cp[k] for k in ('may_launch','may_commit','may_send')))
            self.assertEqual(task.partition(value)['ready'],['independent'])
        with self.assertRaisesRegex(task.TaskError,'UNKNOWN'):self.change(store,'collect-result',**self.result(store,origins=[self.origin]))
        self.assertEqual(self.model.call_count+self.native.call_count,0)

    def test_I03_completed_consumes_existing_result_no_second_attempt(self):
        store,_=self.create();self.finite(store);ref=self.ref('first-freeze.json',{'original':'frozen'})
        self.checkpoint(store,[{'kind':'freeze',**ref}]);self.change(store,'continue',step='a',terminal_ref=self.terminal())
        result=self.result(store,origins=[self.origin]);self.change(store,'collect-result',**result)
        self.change(store,'review-result',**self.review(store,result));value=self.change(store,'accept')
        self.assertTrue(value['steps']['a']['continuation']['resolved']);self.assertTrue(value['accepted'])
        self.assertEqual(value['steps']['a']['runs'],0);self.assertEqual(self.model.call_count+self.native.call_count,0)

    def test_not_executed_keeps_original_budget_and_requires_original_decision(self):
        store,_=self.create();self.checkpoint(store);value=self.change(store,'continue',step='a',terminal_ref=self.terminal('not_executed'))
        self.assertEqual(value['spec']['max_runs'],1);self.assertEqual(value['steps']['a']['runs'],0)
        self.assertFalse(value['steps']['a']['continuation']['may_launch'])
        self.assertEqual(value['steps']['a']['continuation']['next_action'],'decide_original_authorized_step_with_remaining_budget')

    def test_not_executed_cannot_erase_actual_input_or_effect(self):
        store,_=self.create();self.checkpoint(store,[{'kind':'commit',**self.ref('actual-commit.txt',b'original effect')}])
        with self.assertRaisesRegex(task.TaskError,'conflicts'):self.change(store,'continue',step='a',terminal_ref=self.terminal('not_executed'))

    def test_wrong_original_turn_and_terminal_regression_rejected(self):
        store,_=self.create();self.checkpoint(store)
        wrong=dict(self.origin,turn_id='different-turn')
        with self.assertRaisesRegex(task.TaskError,'another original'):self.change(store,'continue',step='a',terminal_ref=self.terminal(origin=wrong))
        self.change(store,'continue',step='a',terminal_ref=self.terminal())
        with self.assertRaisesRegex(task.TaskError,'cannot rewrite'):self.change(store,'continue',step='a',terminal_ref=self.terminal('unknown'))

    def test_unknown_review_producer_to_normal_task_consumer(self):
        import test_review_handoff as rf
        fixture=rf.ReviewHandoffTests('test_H02_fourteen_actual_failed_answers_bound_but_not_auto_accepted')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        fixtures.install_runtime(self.a);store,_=self.create()
        actual=self.ref('actual-review-answer.json',fixture.rb['text'].encode())
        template=self.report(store.read(),object_ref='original-business-object',answer=actual,
            answers=[{'id':'analysis','status':'answered','method_fit':'unknown','evidence':[actual]}],origins=[])
        cli=[sys.executable,str(runtime.launcher_path(self.a)),'--home',str(self.a.home),'task']
        result=fixture.js('continue',record={'dispatch':fixture.dispatch,'attempt':'returned','native_receipt':None},
            dispatches=[fixture.dispatch],selected=fixture.dispatch['contract']['request'],native=fixture.native,readback=fixture.rb,
            consumerCli=cli,consumerTemplate=template,consumerPath=str(self.root/'consumed-current.json'),
            consumerRequest={'operation':'collect-result','task_id':'current','expected_revision':1,'path':'consumed-current.json'})
        self.assertEqual(result['outputs'][0]['state'],'consumed')
        current=store.read();self.assertEqual(current['revision'],2)
        self.assertIsNotNone(current['result_state']['result']);self.assertEqual(current['result_state']['pending'],['analysis','later'])
        self.assertEqual(current['steps']['a']['runs'],0);self.assertFalse(current['accepted'])

    def test_local_denial_does_not_block_independent_ready_step(self):
        store,_=self.create();self.checkpoint(store)
        self.change(store,'approval',step='a',decision='denied',source_ref='fixture:exact-original-denial')
        self.assertEqual(task.partition(store.read())['ready'],['independent'])
        self.change(store,'continue',step='a',terminal_ref=self.terminal())
        self.assertEqual(store.read()['steps']['a']['approval']['decision'],'denied')
        self.assertEqual(self.model.call_count+self.native.call_count,0)

    def test_not_executed_native_attempt_preserves_already_acquired_input(self):
        store,_=self.create();ref=self.ref('acquired-before-native.txt',b'already obtained')
        self.checkpoint(store,[{'kind':'input',**ref}])
        value=self.change(store,'continue',step='a',terminal_ref=self.terminal('not_executed'))
        self.assertEqual(value['steps']['a']['continuation']['effects'][0]['sha256'],ref['sha256'])
        self.assertFalse(value['steps']['a']['continuation']['may_launch'])

    def deny_peer_read(self,store,path):
        spec=copy.deepcopy(store.read()['spec']);spec['execution_mode']='native'
        spec['steps']=[{'id':'deny','instruction':'Synthetic exact read refusal, never run',
            'checks':[{'kind':'message','equals':'unused'}],
            'resources':[{'kind':'file','identity':path,'object':'','mode':'read','operation':'inspect','source_ref':'fixture:actual-read-object'}]}]
        peer=task.Store(self.a.codex_home,'denied-'+store.read()['task_id'])
        peer.create('denied-'+store.read()['task_id'],spec)
        self.change(peer,'approval',step='deny',decision='denied',source_ref='fixture:exact-peer-read-refusal')
        return peer

    def test_dynamic_report_answer_evidence_and_terminal_obey_peer_read_refusal(self):
        for kind in ('report','answer','evidence','terminal'):
            with self.subTest(kind=kind):
                self.a=fixtures.cf.Framework(self.repo,self.root/('dynamic-case-'+kind))
                store,_=self.create('dynamic-'+kind);ref=self.result(store)
                target={'report':ref['path'],'answer':'answer.txt','evidence':'execution.txt','terminal':'terminal.json'}[kind]
                if kind=='terminal':self.checkpoint(store);ref=self.terminal()
                peer=self.deny_peer_read(store,target);before=store.path.read_bytes()
                with self.assertRaises(task.TaskError):
                    if kind=='terminal':self.change(store,'continue',step='a',terminal_ref=ref)
                    else:self.change(store,'collect-result',**ref)
                self.assertEqual(store.path.read_bytes(),before)
                self.assertEqual(peer.read()['steps']['deny']['approval']['decision'],'denied')
                # Remove only this own synthetic namespace's fixture after each
                # subcase; real refusals are never cleared or changed.
                self.assertFalse(store.read()['accepted'])

    def test_dynamic_alias_uses_retained_fd_identity_after_denied_path_moved(self):
        import os
        store,_=self.create();ref=self.result(store);peer=self.deny_peer_read(store,ref['path'])
        original=self.root/ref['path'];alias=self.root/'report-alias.json';os.link(original,alias)
        original.rename(self.root/'moved-original-report.json')
        with self.assertRaises(task.TaskError):self.change(store,'collect-result',path=alias.name,sha256=ref['sha256'])
        self.assertIsNone(store.read()['result_state']['result'])
        self.assertEqual(peer.read()['steps']['deny']['approval']['decision'],'denied')

    def test_unrelated_peer_refusal_preserves_safe_dynamic_report_read(self):
        store,_=self.create();ref=self.result(store);self.ref('other-denied.txt',b'other actual object')
        peer=self.deny_peer_read(store,'other-denied.txt')
        value=self.change(store,'collect-result',**ref)
        self.assertEqual(value['result_state']['result'],ref)
        self.assertEqual(peer.read()['steps']['deny']['approval']['decision'],'denied')

    def test_json_actual_read_rechecks_refusal_after_preliminary_file_check(self):
        store,_=self.create();ref=self.result(store);original=coord.evidence_reference;injected=[]
        def checked(value,reference):
            result=original(value,reference)
            if not injected:
                injected.append(True);self.deny_peer_read(store,ref['path'])
            return result
        with mock.patch.object(coord,'evidence_reference',side_effect=checked):
            with self.assertRaises(task.TaskError):self.change(store,'collect-result',**ref)
        self.assertIsNone(store.read()['result_state']['result'])

    def test_legal_accumulated_33_and64_obligations_have_current_acceptance_route(self):
        for count in (33,64):
            with self.subTest(count=count):
                ids=['goal%02d'%i for i in range(count)]
                scope={'object_ref':'original-business-object','goal':'All original duties','source_ref':'fixture:original',
                    'obligations':[{'id':i,'acceptance':'Answer '+i} for i in ids[:32]]}
                store,_=self.create('many-'+str(count),result_scope=scope)
                spec=copy.deepcopy(store.read()['spec']);spec['result_scope']['obligations']=[{'id':i,'acceptance':'Answer '+i} for i in ids[32:]]
                self.change(store,'amend',spec=spec);self.finite(store)
                result=self.result(store,ids=ids);self.change(store,'collect-result',**result)
                self.change(store,'review-result',**self.review(store,result,ids=ids));value=self.change(store,'accept')
                self.assertEqual(len(value['result_state']['obligations']),count);self.assertEqual(value['result_state']['pending'],[])
                self.assertTrue(value['accepted']);self.assertEqual(value['spec']['max_runs'],1)
                self.assertEqual(value['steps']['a']['runs'],0)

    def test_65th_obligation_rejected_atomically_without_losing_original64(self):
        ids=['goal%02d'%i for i in range(64)]
        scope={'object_ref':'original-business-object','goal':'All duties','source_ref':'fixture:original',
            'obligations':[{'id':i,'acceptance':'Answer '+i} for i in ids[:32]]}
        store,_=self.create(result_scope=scope);spec=copy.deepcopy(store.read()['spec'])
        spec['result_scope']['obligations']=[{'id':i,'acceptance':'Answer '+i} for i in ids[32:]]
        self.change(store,'amend',spec=spec);before=store.path.read_bytes()
        spec['result_scope']['obligations']=[{'id':'new65','acceptance':'New requested duty'}]
        with self.assertRaisesRegex(task.TaskError,'bound'):self.change(store,'amend',spec=spec)
        self.assertEqual(store.path.read_bytes(),before);self.assertEqual(len(store.read()['result_state']['pending']),64)

    def test_local_A_refusal_allows_safe_dynamic_B_partial_result(self):
        store,_=self.create();self.finite(store)
        self.change(store,'approval',step='a',decision='denied',source_ref='fixture:exact-A-input-only')
        ref=self.result(store,ids=('later',));p=self.root/ref['path'];report=json.loads(p.read_bytes())
        report['answers'][0]['evidence']=[{'path':'independent.txt','sha256':task.digest((self.root/'independent.txt').read_bytes())}]
        ref=self.ref(ref['path'],report);value=self.change(store,'collect-result',**ref)
        self.assertEqual(value['result_state']['result'],ref);self.assertEqual(value['steps']['independent']['state'],'verified')
        self.assertEqual(value['steps']['a']['approval']['decision'],'denied');self.assertFalse(value['accepted'])
        review=self.review(store,ref,ids=('later',));value=self.change(store,'review-result',**review)
        self.assertEqual(value['result_state']['pending'],['analysis'])
        with self.assertRaises(task.TaskError):self.change(store,'accept')
        # Mapping a truly refused A object to the same passive B consumer cannot
        # grant a read. Actual FD/path checks, not a chosen step, decide this.
        report['answer']={'path':'input.txt','sha256':task.digest((self.root/'input.txt').read_bytes())}
        bad=self.ref('denied-answer-result.json',report)
        with self.assertRaises(task.TaskError):self.change(store,'collect-result',**bad)
        self.assertEqual(store.read()['steps']['a']['approval']['decision'],'denied')

    def test_unknown_A_operation_does_not_block_unrelated_B_result_collection(self):
        store,_=self.create();self.finite(store);self.checkpoint(store)
        ref=self.result(store,ids=('later',),origins=[]);value=self.change(store,'collect-result',**ref)
        value=self.change(store,'review-result',**self.review(store,ref,ids=('later',)))
        self.assertEqual(value['result_state']['pending'],['analysis'])
        self.assertEqual(value['steps']['a']['continuation']['terminal'],'unknown')
        self.assertFalse(value['steps']['a']['continuation']['resolved'])
        self.assertEqual(task.view(value)['result_dimensions']['business_outcome'],'pending')
        with self.assertRaises(task.TaskError):self.change(store,'accept')
        self.assertEqual(self.model.call_count+self.native.call_count,0)

    def test_current_normal_installed_entry_persists_continuation_without_model(self):
        fixtures.install_runtime(self.a)
        def invoke(request):
            r=subprocess.run([sys.executable,str(runtime.launcher_path(self.a)),'--home',str(self.a.home),'task'],input=json.dumps(request),capture_output=True,text=True,timeout=20)
            self.assertEqual(r.stderr,'');return r,json.loads(r.stdout)
        store,value=self.create()
        r,reply=invoke({'operation':'checkpoint','task_id':'current','expected_revision':value['revision'],'step':'a','origin':self.origin,'effects':[]})
        self.assertEqual(r.returncode,0)
        r,reply=invoke({'operation':'continue','task_id':'current','expected_revision':reply['revision'],'step':'a'})
        self.assertEqual(r.returncode,0);self.assertEqual(reply['result_dimensions']['business_outcome'],'pending')
        self.assertEqual(reply['work_partition']['ready'],['independent']);self.assertEqual(reply['steps']['a']['runs'],0)
        self.assertEqual(reply['steps']['a']['continuation']['origin'],self.origin)


import test_review_handoff as reviewfixtures
import unittest

class ReviewContinuityTests(unittest.TestCase):
    # Reuse the actual producer/native-envelope/receiver construction. The old
    # protocol suite is not rerun or counted as new continuity verification.
    js=reviewfixtures.ReviewHandoffTests.js
    make=reviewfixtures.ReviewHandoffTests.make
    produce=reviewfixtures.ReviewHandoffTests.produce
    native_read=reviewfixtures.ReviewHandoffTests.native_read
    setUp=reviewfixtures.ReviewHandoffTests.setUp

    def continuation(self,**extra):
        return self.js('continue',record={'dispatch':self.dispatch,'attempt':'returned','native_receipt':None},
            dispatches=[self.dispatch],selected=self.dispatch['contract']['request'],native=self.native,readback=self.rb,**extra)

    def test_unknown_native_result_never_calls_original_consumer(self):
        self.native=None;r=self.continuation(repeat=2)
        self.assertNotIn('consume',r['calls']);self.assertTrue(all(x['state']=='unknown' for x in r['outputs']))

    def test_actual_return_consumed_once_and_parent_goal_stays_pending(self):
        r=self.continuation(repeat=2)
        self.assertEqual(r['calls'].count('consume'),1)
        self.assertEqual([x['state'] for x in r['outputs']],['consumed','consumed'])
        self.assertTrue(all(x['parent_goal']=='pending_original_scope_review' for x in r['outputs']))
        self.assertEqual(r['retained'][0]['consumption']['state'],'started')

    def test_lost_consumer_ack_does_not_replay_on_next_host_opportunity(self):
        r=self.continuation(consumeThrows=True,repeat=2)
        self.assertEqual(r['calls'].count('consume'),1);self.assertEqual(r['record']['consumption']['state'],'unknown')
        self.assertEqual(r['outputs'][1]['state'],'unknown')

    def test_original_committed_readback_recovers_without_second_consume(self):
        r=self.continuation(consumeThrows=True,inspect='committed',repeat=2)
        self.assertEqual(r['calls'].count('consume'),1);self.assertEqual(r['calls'].count('inspect'),1)
        self.assertEqual(r['outputs'][1]['state'],'consumed')

    def test_not_executed_consumer_still_does_not_automatically_repeat(self):
        r=self.continuation(consumeThrows=True,inspect='not_executed',repeat=2)
        self.assertEqual(r['calls'].count('consume'),1);self.assertEqual(r['outputs'][1]['state'],'unknown')

    def test_failed_durable_intent_never_calls_consumer(self):
        r=self.continuation(retain=False)
        self.assertNotIn('consume',r['calls']);self.assertEqual(r['outputs'][0]['state'],'unknown')
