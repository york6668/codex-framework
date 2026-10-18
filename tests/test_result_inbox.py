"""Affected original-result intake/continuation contracts; native data are fixtures.

No model, native status query, scheduler or business operation is invoked.
"""
import copy
import json
from unittest import mock

import test_framework as fixtures
import test_task_continuity as continuity
import task_runtime as task
import task_coordination as coord


class ResultInboxTests(fixtures.FixtureCase):
    def setUp(self):
        super().setUp()
        self.addCleanup(mock.patch.stopall)
        self.model = mock.patch('task_worker.execute', side_effect=AssertionError('no model')).start()
        self.native = mock.patch('task_worker.inspect_session', side_effect=AssertionError('no native query')).start()
        self.origin = {'operation_id':'original-domain-operation', 'attempt_id':'original-domain-attempt',
                       'session_id':'original-session', 'turn_id':'original-turn'}
    ref = continuity.ContinuityTests.ref
    create = continuity.ContinuityTests.create
    change = continuity.ContinuityTests.change
    report = continuity.ContinuityTests.report
    finite = continuity.ContinuityTests.finite
    checkpoint = continuity.ContinuityTests.checkpoint
    terminal = continuity.ContinuityTests.terminal

    def result(self, store, name, ids=('analysis', 'later'), **fields):
        ref = continuity.ContinuityTests.result(self, store, ids=ids, **fields)
        body = json.loads((self.root / ref['path']).read_bytes())
        body['native_result']['turn_id'] = 'fixture-final-' + name
        return self.ref(name + '-result.json', body)

    def review(self, store, result, name, ids=('analysis', 'later'), verdict='PASS'):
        ref = continuity.ContinuityTests.review(self, store, result, ids=ids, verdict=verdict)
        return self.ref(name + '-review.json', (self.root / ref['path']).read_bytes())

    def collect_review(self, store, name, ids=('analysis', 'later')):
        result = self.result(store, name, ids)
        self.change(store, 'collect-result', **result)
        review = self.review(store, result, name, ids)
        return result, review, self.change(store, 'review-result', **review)

    def work(self, stores, **extra):
        return coord.workset(self.a.codex_home, {
            'operation': 'work', 'task_ids': list(stores), 'max_turns': 1,
            'expected_revisions': {key: store.read()['revision'] for key, store in stores.items()}, **extra})

    def test_duplicate_result_and_review_are_byte_identical_no_commit(self):
        store, _ = self.create(); self.finite(store)
        result, review, _ = self.collect_review(store, 'a', ('analysis',))
        before = store.path.read_bytes()
        for operation, ref in [('collect-result', result), ('review-result', review)]:
            current = self.change(store, operation, **ref)
            self.assertEqual(current['result_state']['pending'], ['later'])
            self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(self.model.call_count + self.native.call_count, 0)

    def test_distinct_partial_returns_accumulate_without_erasing_original_goal(self):
        store, _ = self.create(); self.finite(store)
        a, _, first = self.collect_review(store, 'a', ('analysis',))
        self.assertEqual(first['result_state']['pending'], ['later'])
        b, _, final = self.collect_review(store, 'b', ('later',))
        self.assertEqual(final['result_state']['pending'], [])
        self.assertEqual([r['result'] for r in final['result_state']['receipts']], [a, b])
        self.assertTrue(self.change(store, 'accept')['accepted'])

    def test_same_native_return_with_conflicting_body_is_atomic_rejection(self):
        store, _ = self.create(); result = self.result(store, 'a', ('analysis',))
        self.change(store, 'collect-result', **result); before = store.path.read_bytes()
        body = json.loads((self.root / result['path']).read_bytes())
        body['answers'][0]['status'] = 'partial'
        conflict = self.ref('conflict.json', body)
        with self.assertRaisesRegex(task.TaskError, 'conflicting result bytes'):
            self.change(store, 'collect-result', **conflict)
        self.assertEqual(store.path.read_bytes(), before)

    def test_later_assessment_supersedes_only_its_original_result_and_named_obligation(self):
        store, _ = self.create(); self.finite(store)
        result, _, _ = self.collect_review(store, 'a')
        self.change(store, 'review-result', **self.review(store, result, 'negative', ('analysis',), 'UNKNOWN'))
        self.assertEqual(store.read()['result_state']['pending'], ['analysis'])
        self.change(store, 'review-result', **self.review(store, result, 'corrected', ('analysis',)))
        self.assertEqual(store.read()['result_state']['pending'], [])
        b = self.result(store, 'b', ('analysis',)); self.change(store, 'collect-result', **b)
        self.change(store, 'review-result', **self.review(store, b, 'b-negative', ('analysis',), 'FAIL'))
        self.assertEqual(store.read()['result_state']['pending'], ['analysis'])
        with self.assertRaises(task.TaskError): self.change(store, 'accept')

    def test_stale_A_does_not_prevent_safe_B_intake_or_grant_whole_acceptance(self):
        store, _ = self.create(); self.finite(store)
        a, _, _ = self.collect_review(store, 'a', ('analysis',))
        (self.root / a['path']).write_text('changed actual A')
        _, _, value = self.collect_review(store, 'b', ('later',))
        self.assertEqual(value['result_state']['pending'], ['analysis'])
        view = task.view(value)['result_dimensions']
        self.assertEqual(len(view['receipt_errors']), 1)
        self.assertEqual(view['business_outcome'], 'pending')
        with self.assertRaises(task.TaskError): self.change(store, 'accept')

    def test_unknown_original_attempt_and_independent_B_remain_separate(self):
        store, _ = self.create(); self.finite(store); self.checkpoint(store)
        origin = copy.deepcopy(store.read()['steps']['a']['continuation'])
        self.collect_review(store, 'b', ('later',))
        self.assertEqual(store.read()['steps']['a']['continuation'], origin)
        self.assertEqual(store.read()['result_state']['pending'], ['analysis'])
        bad = self.result(store, 'bad', ('analysis',), origins=[self.origin])
        before = store.path.read_bytes()
        with self.assertRaisesRegex(task.TaskError, 'UNKNOWN'):
            self.change(store, 'collect-result', **bad)
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(self.model.call_count + self.native.call_count, 0)

    def test_original_intake_and_independent_ready_effect_use_same_finite_entry(self):
        parent, _ = self.create(); self.finite(parent)
        independent, _ = self.create('independent-task')
        result = self.result(parent, 'returned', ('analysis',))
        review = self.review(parent, result, 'returned', ('analysis',))
        observed = self.ref('independent-observed.json', self.report(independent.read(), verdict='PASS'))
        with mock.patch('task_runtime.observe_external', wraps=task.observe_external) as execute:
            first = self.work({'current': parent, 'independent-task': independent},
                results={'current': {'result': result, 'review': review}},
                observations={'independent-task': observed})
            self.assertEqual(execute.call_count, 1)
        self.assertEqual([x['task_id'] for x in first['executed']], ['independent-task'])
        self.assertEqual([r['state'] for r in first['received']], ['consumed', 'consumed'])
        self.assertTrue(all(s['state'] == 'verified' for s in independent.read()['steps'].values()))
        self.assertEqual(parent.read()['result_state']['pending'], ['later'])
        before = {s.read()['task_id']: s.path.read_bytes() for s in (parent, independent)}
        second = self.work({'current': parent, 'independent-task': independent},
            results={'current': {'result': result, 'review': review}}, observations={'independent-task': observed})
        self.assertEqual([r['state'] for r in second['received']], ['duplicate_reused', 'duplicate_reused'])
        self.assertEqual(second['executed'], [])
        self.assertEqual(before, {s.read()['task_id']: s.path.read_bytes() for s in (parent, independent)})
        self.assertTrue(second['continuation']['must_continue']); self.assertFalse(second['overall_complete'])
        self.assertFalse(second['continuation']['background_active'])
        self.assertEqual(self.model.call_count + self.native.call_count, 0)

    def test_bad_intake_is_local_and_independent_ready_still_runs(self):
        parent, _ = self.create(); independent, _ = self.create('independent-task')
        bad = self.ref('bad-result.json', {'not': 'current result'})
        observed = self.ref('independent-observed.json', self.report(independent.read(), verdict='PASS'))
        out = self.work({'current': parent, 'independent-task': independent},
            results={'current': {'result': bad}}, observations={'independent-task': observed})
        self.assertEqual(len(out['intake_errors']), 1)
        self.assertEqual([x['task_id'] for x in out['executed']], ['independent-task'])
        self.assertEqual(parent.read()['revision'], 1)
        self.assertTrue(out['continuation']['must_continue'])

    def test_finite_limit_leaves_actual_next_action_without_background_claim(self):
        a, _ = self.create('a'); b, _ = self.create('b')
        observations = {s.read()['task_id']: self.ref(s.read()['task_id']+'-observation.json', self.report(s.read(), verdict='PASS')) for s in (a,b)}
        out = self.work({'a':a, 'b':b}, observations=observations)
        self.assertEqual(len(out['executed']), 1); self.assertFalse(out['overall_complete'])
        frontier = out['continuation']; self.assertTrue(frontier['must_continue']); self.assertFalse(frontier['may_wait'])
        self.assertTrue(any(x.get('task_id') == 'b' and x.get('step') == 'a' for x in frontier['next_actions']))
        self.assertTrue(frontier['host_execution_required']); self.assertFalse(frontier['background_active'])

    def test_real_scoped_wait_remains_wait_without_erasing_original_goal(self):
        store, _ = self.create()
        for step in ('a', 'independent'):
            self.change(store, 'block', step=step, condition='actual external condition for this fixture step', evidence_ref='fixture:original-condition')
        out = coord.workset(self.a.codex_home, {'operation':'coordinate', 'task_ids':['current']})
        self.assertFalse(out['continuation']['must_continue']); self.assertTrue(out['continuation']['may_wait'])
        self.assertEqual(len(out['continuation']['scoped_waits']), 2)
        self.assertFalse(out['overall_complete'])
        self.assertEqual(task.view(store.read())['next_action']['kind'], 'resolve_specific_wait')
        self.assertEqual(store.read()['result_state']['pending'], ['analysis','later'])

    def test_legacy_reference_state_migrates_on_actual_mutation_only(self):
        store, _ = self.create(); self.finite(store)
        result, review, value = self.collect_review(store, 'a', ('analysis',))
        value['result_state'].pop('receipts'); store.save(value)
        before = store.path.read_bytes()
        self.assertEqual(task.view(store.read())['result_dimensions']['pending_obligations'], ['later'])
        self.assertEqual(store.path.read_bytes(), before)
        migrated = self.change(store, 'collect-result', **result)
        self.assertEqual(migrated['result_state']['receipts'][0]['reviews'], [review])
        self.assertEqual(migrated['result_state']['pending'], ['later'])
        stable = store.path.read_bytes(); self.change(store, 'collect-result', **result)
        self.assertEqual(store.path.read_bytes(), stable)

    def test_invalid_intake_shape_or_stale_revision_precedes_any_effect(self):
        store, _ = self.create(); result = self.result(store, 'a')
        before = store.path.read_bytes()
        for results in ({'current': []}, {'current': {'result': {'path': 'a', 'sha256': []}}},
                        {'other': {'result': result}}, {'current': {'result': result, 'effect_intents': []}}):
            with self.assertRaises(task.TaskError): self.work({'current': store}, results=results)
            self.assertEqual(store.path.read_bytes(), before)
        with self.assertRaises(task.TaskError):
            self.work({'current': store}, results={'current': {'result': result}}, expected_revisions={'current': 0})
        self.assertEqual(store.path.read_bytes(), before)
