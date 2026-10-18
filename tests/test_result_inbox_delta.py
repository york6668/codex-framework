"""440 independent failure delta only. All native identities are fixtures."""
import copy
import json
from unittest import mock

import test_framework as fixtures
import test_result_inbox as inbox
import test_task_continuity as continuity
import task_runtime as task
import task_coordination as coord


class ResultInboxDeltaTests(fixtures.FixtureCase):
    def setUp(self):
        super().setUp(); self.addCleanup(mock.patch.stopall)
        self.model = mock.patch('task_worker.execute', side_effect=AssertionError('no model')).start()
        self.native = mock.patch('task_worker.inspect_session', side_effect=AssertionError('no native query')).start()
        self.origin = {'operation_id':'only-A-operation','attempt_id':'only-A-attempt',
                       'session_id':'only-A-session','turn_id':'only-A-turn'}

    ref = inbox.ResultInboxTests.ref
    create = inbox.ResultInboxTests.create
    change = inbox.ResultInboxTests.change
    report = inbox.ResultInboxTests.report
    finite = inbox.ResultInboxTests.finite
    checkpoint = inbox.ResultInboxTests.checkpoint
    result = inbox.ResultInboxTests.result
    review = inbox.ResultInboxTests.review
    collect_review = inbox.ResultInboxTests.collect_review
    deny_peer_read = continuity.ContinuityTests.deny_peer_read

    def coordinate(self):
        return coord.workset(self.a.codex_home, {'operation':'coordinate','task_ids':['current']})

    def test_native_digest_and_source_reference_cannot_split_one_return(self):
        for field in ('final_sha256', 'final_ref', 'both'):
            store, _ = self.create('case-'+field); self.finite(store)
            first, _, _ = self.collect_review(store, 'first-'+field, ('analysis',))
            body = json.loads((self.root/first['path']).read_bytes())
            body['answers'][0]['id'] = 'later'
            if field in ('final_sha256', 'both'):
                answer = self.ref('changed-answer-'+field+'.txt', b'Different final body for the same original thread/turn')
                body['answer'] = answer; body['native_result']['final_sha256'] = answer['sha256']
            if field in ('final_ref', 'both'):
                body['native_result']['final_ref'] = 'fixture:conflicting-observation-of-same-final'
            conflicting = self.ref('conflict-'+field+'.json', body); before = store.path.read_bytes()
            with self.assertRaisesRegex(task.TaskError, 'same original native return'):
                self.change(store, 'collect-result', **conflicting)
            self.assertEqual(store.path.read_bytes(), before)
            self.assertFalse(store.read()['accepted'])
            self.assertEqual(store.read()['result_state']['pending'], ['later'])

    def test_440_receipt_without_stable_key_still_rejects_conflict_atomically(self):
        store, _ = self.create(); first, _, value = self.collect_review(store, 'first', ('analysis',))
        value['result_state']['receipts'][0].pop('native_identity'); store.save(value)
        body = json.loads((self.root/first['path']).read_bytes())
        body['native_result']['final_sha256'] = 'a'*64
        second = self.ref('conflicting-observation.json', body); before = store.path.read_bytes()
        with self.assertRaisesRegex(task.TaskError, 'same original native return'):
            self.change(store, 'collect-result', **second)
        self.assertEqual(store.path.read_bytes(), before)

    def test_actual_current_input_correction_uses_current_result_without_old_coverage(self):
        store, _ = self.create(); self.finite(store)
        prior, _, _ = self.collect_review(store, 'prior', ('analysis',))
        prior_native = json.loads((self.root/prior['path']).read_bytes())['native_result']
        old_input = store.read()['acceptance_sha256']
        corrected_source = self.ref('actual-correction-source.json', {'source':'fixture:current-native-correction','observed':'new input'})
        spec = copy.deepcopy(store.read()['spec']); spec['acceptance_version'] = 'explicit-current-correction-v2'
        spec['steps'][0]['checks'] = [{'kind':'file', **corrected_source}]
        current = self.change(store, 'amend', spec=spec)
        self.assertNotEqual(current['acceptance_sha256'], old_input)
        self.assertEqual(current['result_state']['pending'], ['analysis','later'])
        self.assertEqual(current['result_state']['receipts'], [])
        self.finite(store)
        current_ref = self.result(store, 'current-corrected')
        body = json.loads((self.root/current_ref['path']).read_bytes())
        body['native_result'] = dict(prior_native, final_ref='fixture:actual-current-correction-source')
        corrected = self.ref('corrected-current-result.json', body)
        self.change(store, 'collect-result', **corrected)
        self.change(store, 'review-result', **self.review(store, corrected, 'current-corrected'))
        accepted = self.change(store, 'accept')
        self.assertTrue(accepted['accepted']); self.assertEqual(accepted['input_revision'], 2)
        superseded = [h['superseded_input'] for h in accepted['history'] if 'superseded_input' in h]
        self.assertEqual(superseded[-1]['result_state']['receipts'][0]['result'], prior)
        self.assertEqual(self.model.call_count+self.native.call_count, 0)

    def test_unknown_A_preserves_safe_B_review_then_waits_only_for_A(self):
        store, _ = self.create(); self.finite(store); self.checkpoint(store)
        original = copy.deepcopy(store.read()['steps']['a']['continuation'])
        result = self.result(store, 'independent-B', ('later',))
        review = self.review(store, result, 'independent-B', ('later',))
        self.change(store, 'collect-result', **result)
        out = self.coordinate(); frontier = out['continuation']
        self.assertTrue(frontier['must_continue']); self.assertFalse(frontier['may_wait'])
        actions = [a for a in frontier['next_actions'] if a['kind']=='review_original_results']
        self.assertEqual(actions[0]['results'], [result])
        self.assertEqual(out['tasks']['current']['next_action']['kind'], 'review_original_results')
        self.change(store, 'review-result', **review)
        out = self.coordinate()
        self.assertFalse(out['continuation']['must_continue']); self.assertTrue(out['continuation']['may_wait'])
        self.assertEqual(store.read()['result_state']['pending'], ['analysis'])
        self.assertEqual(store.read()['steps']['a']['continuation'], original)
        self.assertFalse(out['overall_complete'])
        with self.assertRaises(task.TaskError): self.change(store, 'accept')
        self.assertEqual(self.model.call_count+self.native.call_count, 0)

    def test_exact_B_read_refusal_is_not_a_reviewable_action(self):
        store, _ = self.create(); self.finite(store); self.checkpoint(store)
        result = self.result(store, 'independent-B', ('later',))
        self.change(store, 'collect-result', **result)
        peer = self.deny_peer_read(store, result['path'])
        before = {p.path:p.path.read_bytes() for p in (store,peer)}
        out = self.coordinate(); dimensions = out['tasks']['current']['result_dimensions']
        self.assertEqual(dimensions['reviewable_results'], [])
        self.assertTrue(dimensions['receipt_errors'])
        self.assertTrue(out['continuation']['may_wait']); self.assertFalse(out['continuation']['must_continue'])
        self.assertEqual(before, {p.path:p.path.read_bytes() for p in (store,peer)})

    def test_new_unknown_same_origin_after_receipt_prevents_review_action(self):
        store, _ = self.create(); self.finite(store)
        result = self.result(store, 'only-A', ('analysis',), origins=[self.origin])
        self.change(store, 'collect-result', **result); self.checkpoint(store)
        out = self.coordinate()
        self.assertEqual(out['tasks']['current']['result_dimensions']['reviewable_results'], [])
        self.assertTrue(out['continuation']['may_wait'])
        self.assertFalse(out['overall_complete'])

    def test_stale_B_evidence_never_makes_review_action_or_overall_pass(self):
        store, _ = self.create(); self.finite(store); self.checkpoint(store)
        result = self.result(store, 'independent-B', ('later',))
        self.change(store, 'collect-result', **result)
        (self.root/'execution.txt').write_text('changed actual B evidence')
        out = self.coordinate()
        self.assertEqual(out['tasks']['current']['result_dimensions']['reviewable_results'], [])
        self.assertTrue(out['continuation']['may_wait']); self.assertFalse(out['overall_complete'])

    def test_reviewed_but_unresolved_B_needs_its_own_followup_not_global_wait(self):
        store, _ = self.create(); self.finite(store); self.checkpoint(store)
        original = copy.deepcopy(store.read()['steps']['a']['continuation'])
        result = self.result(store, 'independent-B', ('later',), status='partial')
        self.change(store, 'collect-result', **result)
        self.change(store, 'review-result', **self.review(store, result, 'B-unknown', ('later',), 'UNKNOWN'))
        out = self.coordinate(); frontier = out['continuation']
        self.assertTrue(frontier['must_continue']); self.assertFalse(frontier['may_wait'])
        actions = [a for a in frontier['next_actions'] if a['kind']=='continue_original_result']
        self.assertEqual(actions[0]['obligations'], ['later'])
        self.assertEqual(actions[0]['result'], result)
        self.assertEqual(out['tasks']['current']['next_action']['kind'], 'continue_original_result')
        self.assertEqual(store.read()['steps']['a']['continuation'], original)
        self.assertFalse(out['overall_complete'])
