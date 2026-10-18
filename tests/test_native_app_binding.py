"""Optional native-plan binding only; fixtures are not App runtime evidence."""
import copy
import json
from unittest import mock

import test_framework as fixtures
import test_task_continuity as continuity
import task_runtime as task
import task_coordination as coord


class NativeAppBindingTests(fixtures.FixtureCase):
    def setUp(self):
        super().setUp(); self.addCleanup(mock.patch.stopall)
        self.model=mock.patch('task_worker.execute',side_effect=AssertionError('no model')).start()
        self.native=mock.patch('task_worker.inspect_session',side_effect=AssertionError('no native query')).start()
        self.identity={'thread_id':'fixture-local-thread','turn_id':'fixture-original-user-goal',
                       'message_id':'fixture-original-user-message','automation_id':'fixture-native-plan'}
        self.origin={'operation_id':'original-A','attempt_id':'original-A-attempt','session_id':'fixture-local-thread','turn_id':'fixture-A-turn'}

    ref=continuity.ContinuityTests.ref
    change=continuity.ContinuityTests.change
    report=continuity.ContinuityTests.report
    finite=continuity.ContinuityTests.finite
    checkpoint=continuity.ContinuityTests.checkpoint
    terminal=continuity.ContinuityTests.terminal
    review=continuity.ContinuityTests.review

    def create(self, identity='current', **extra):
        return continuity.ContinuityTests.create(self,identity,request_identity=copy.deepcopy(self.identity),**extra)

    def result(self, store, name, ids, origins=None):
        ref=continuity.ContinuityTests.result(self,store,ids=ids,origins=origins)
        report=json.loads((self.root/ref['path']).read_bytes())
        report['native_result']['thread_id']=self.identity['thread_id']
        report['native_result']['turn_id']='fixture-heartbeat-'+name
        return self.ref(name+'-result.json',report)

    def test_optional_plan_binding_preserves_legacy_request_and_current_goal(self):
        store,value=self.create()
        self.assertEqual(value['spec']['request_identity'],self.identity)
        self.assertFalse(value['accepted']);self.assertEqual(value['result_state']['pending'],['analysis','later'])
        legacy={k:v for k,v in self.identity.items() if k!='automation_id'}
        _,old=continuity.ContinuityTests.create(self,'legacy',request_identity=legacy)
        self.assertEqual(old['spec']['request_identity'],legacy)
        self.assertEqual(self.model.call_count+self.native.call_count,0)

    def test_invalid_plan_binding_is_atomic_and_does_not_add_scheduler_fields(self):
        for n,invalid in enumerate([None,'',[],True,7]):
            identity=dict(self.identity,automation_id=invalid); store=task.Store(self.a.codex_home,'invalid-'+str(n))
            with self.assertRaises(task.TaskError):continuity.ContinuityTests.create(self,'invalid-'+str(n),request_identity=identity)
            self.assertFalse(store.path.exists())
        for name in ('api_key','start_schedule','schedule_status','authenticated'):
            with self.assertRaises(task.TaskError):continuity.ContinuityTests.create(self,'invalid-'+name,request_identity=dict(self.identity,**{name:'fixture'}))

    def test_other_plan_result_cannot_answer_current_input(self):
        store,_=self.create();ref=self.result(store,'B',('later',))
        body=json.loads((self.root/ref['path']).read_bytes());body['request_identity']['automation_id']='another-plan'
        wrong=self.ref('wrong-plan-result.json',body);before=store.path.read_bytes()
        with self.assertRaisesRegex(task.TaskError,'current project/task/native'):self.change(store,'collect-result',**wrong)
        self.assertEqual(store.path.read_bytes(),before)

    def test_other_plan_review_cannot_accept_a_current_result(self):
        store,_=self.create();ref=self.result(store,'B',('later',));self.change(store,'collect-result',**ref)
        review=self.review(store,ref,ids=('later',));body=json.loads((self.root/review['path']).read_bytes())
        body['request_identity']['automation_id']='another-plan';wrong=self.ref('wrong-plan-review.json',body);before=store.path.read_bytes()
        with self.assertRaisesRegex(task.TaskError,'current project/task/native'):self.change(store,'review-result',**wrong)
        self.assertEqual(store.path.read_bytes(),before);self.assertFalse(store.read()['accepted'])

    def test_later_fixture_return_reuses_B_and_preserves_goal_until_A_review(self):
        store,_=self.create();self.finite(store);self.checkpoint(store)
        original=copy.deepcopy(store.read()['steps']['a']['continuation'])
        b=self.result(store,'B',('later',));self.change(store,'collect-result',**b)
        br=self.ref('B-review.json',(self.root/self.review(store,b,ids=('later',))['path']).read_bytes())
        self.change(store,'review-result',**br)
        self.assertEqual(store.read()['result_state']['pending'],['analysis']);self.assertFalse(store.read()['accepted'])
        before=store.path.read_bytes();self.change(store,'collect-result',**b);self.change(store,'review-result',**br)
        self.assertEqual(store.path.read_bytes(),before)
        self.assertEqual(store.read()['steps']['a']['continuation'],original)
        self.change(store,'continue',step='a',terminal_ref=self.terminal())
        a=self.result(store,'A',('analysis',),origins=[self.origin]);self.change(store,'collect-result',**a)
        self.change(store,'review-result',**self.review(store,a,ids=('analysis',)))
        final=self.change(store,'accept');self.assertTrue(final['accepted'])
        self.assertEqual(final['input_revision'],1);self.assertEqual(final['spec']['request_identity'],self.identity)
        self.assertEqual(len(final['result_state']['receipts']),2)
        self.assertTrue(final['steps']['a']['continuation']['resolved'])
        self.assertEqual(self.model.call_count+self.native.call_count,0)
