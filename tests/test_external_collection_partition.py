"""External result intake follows the existing per-step readiness boundary."""
import copy
from unittest import mock

import test_framework as fixtures
from test_persistent_dependencies import bind_local_contract
import task_coordination as coord
import task_runtime as task


class ExternalCollectionPartitionTests(fixtures.FixtureCase):
    def setUp(self):
        super().setUp()
        self.model = mock.patch('task_worker.execute', side_effect=AssertionError('no model dispatch')).start()
        self.native = mock.patch('task_worker.inspect_session', side_effect=AssertionError('no native query')).start()
        self.addCleanup(mock.patch.stopall)

    def create(self, steps=None, scope=True, dependency=None):
        for name in ('live', 'source', 'replay'):
            (self.root / (name + '.txt')).write_bytes(name.encode())
        spec = {'request_id': 'full-business-request', 'requirement': 'Original complete business goal',
                'acceptance_version': 'v1', 'workspace': str(self.root), 'execution_mode': 'external', 'max_runs': 1,
                'steps': steps or [{'id': name, 'instruction': 'Check acquired ' + name,
                                   'checks': [{'kind': 'file', 'path': name + '.txt', 'sha256': task.digest(name.encode())}]}
                                  for name in ('live', 'source', 'replay')]}
        if scope:
            spec['result_scope'] = {'object_ref': 'business-system', 'goal': 'Complete original lifecycle',
                                    'source_ref': 'fixture:original-request',
                                    'obligations': [{'id': name, 'acceptance': 'Actual ' + name}
                                                    for name in ('capture', 'implementation', 'review', 'delivery', 'correction')]}
        if dependency:
            spec['steps'][1]['depends_on'] = ['live']
            if dependency == 'proven':
                bind_local_contract(spec, 'current', 'live', 'source')
        store = task.Store(self.a.codex_home, 'current')
        value = store.create('current', spec)
        report = task.encoded({'task_id': 'current', 'request_id': spec['request_id'], 'input_revision': 1,
                               'task_input_sha256': value['acceptance_sha256'], 'verdict': 'PASS'})
        (self.root / 'return.json').write_bytes(report)
        return store, {'path': 'return.json', 'sha256': task.digest(report)}

    def block(self, store):
        return task.change(store, {'operation': 'block', 'expected_revision': store.read()['revision'], 'step': 'live',
                                   'condition': 'Only this fixture environment is unavailable', 'evidence_ref': 'fixture:scoped-observation'})

    def collect(self, store, ref):
        return task.observe_external(store, store.read()['revision'], ref)

    def assert_partial(self, value):
        self.assertFalse(value['accepted'])
        self.assertEqual({name for name, state in value['steps'].items() if state['state'] == 'verified'}, {'source', 'replay'})
        self.assertTrue(coord.result_projection(value)['pending_obligations'])
        self.assertTrue(all(state['runs'] == 0 for state in value['steps'].values()))
        self.assertEqual((self.model.call_count, self.native.call_count), (0, 0))

    def test_blocked_first_step_keeps_independent_collection_and_full_goal(self):
        store, ref = self.create()
        before = self.block(store)
        restrictions = copy.deepcopy(before['restrictions'])
        value = self.collect(store, ref)
        self.assert_partial(value)
        self.assertEqual(value['steps']['live'], before['steps']['live'])
        self.assertEqual(value['restrictions'], restrictions)
        with self.assertRaises(task.TaskError):
            task.change(store, {'operation': 'accept', 'expected_revision': value['revision']})

    def test_first_file_failure_does_not_skip_later_independent_checks(self):
        store, ref = self.create()
        (self.root / 'live.txt').write_bytes(b'wrong acquired bytes')
        value = self.collect(store, ref)
        self.assert_partial(value)
        self.assertEqual(value['steps']['live']['state'], 'blocked')
        self.assertFalse(value['steps']['live']['validation']['passed'])
        self.assertEqual(value['steps']['live']['verification_attempts'], 1)

    def test_exact_refusal_is_not_read_but_other_file_is_collected(self):
        steps = [{'id': name, 'instruction': 'Check ' + name,
                  'checks': [{'kind': 'file', 'path': ('replay' if name == 'replay' else 'live') + '.txt',
                              'sha256': task.digest(('replay' if name == 'replay' else 'live').encode())}]}
                 for name in ('live', 'source', 'replay')]
        store, ref = self.create(steps)
        task.change(store, {'operation': 'approval', 'expected_revision': 1, 'step': 'live',
                            'decision': 'denied', 'source_ref': 'fixture:exact-original-refusal'})
        original = task.checked_file
        def guarded(value, step, check):
            self.assertNotEqual(check['path'], 'live.txt', 'retained exact object was read')
            return original(value, step, check)
        with mock.patch('task_runtime.checked_file', side_effect=guarded):
            value = self.collect(store, ref)
        self.assertEqual(value['steps']['replay']['state'], 'verified')
        self.assertEqual(value['steps']['source']['state'], 'pending')
        self.assertEqual(value['steps']['live']['wait']['kind'], 'explicit_denial')
        self.assertFalse(value['accepted'])

    def test_unknown_original_is_retained_while_independent_files_are_collected(self):
        store, ref = self.create()
        origin = {'operation_id': 'original-op', 'attempt_id': 'original-attempt', 'session_id': 'fixture-session', 'turn_id': 'fixture-turn'}
        before = task.change(store, {'operation': 'checkpoint', 'expected_revision': 1, 'step': 'live', 'origin': origin, 'effects': []})
        value = self.collect(store, ref)
        self.assert_partial(value)
        self.assertEqual(value['steps']['live']['continuation'], before['steps']['live']['continuation'])
        self.assertFalse(value['steps']['live']['continuation']['resolved'])

    def test_no_ready_step_preserves_error_without_clearing_original_hold(self):
        steps = [{'id': 'live', 'instruction': 'Check acquired file',
                  'checks': [{'kind': 'file', 'path': 'live.txt', 'sha256': task.digest(b'live')}]}]
        store, ref = self.create(steps)
        self.block(store)
        before = store.path.read_bytes()
        with self.assertRaisesRegex(task.TaskError, 'cannot clear a refusal/unresolved'):
            self.collect(store, ref)
        self.assertEqual(store.path.read_bytes(), before)

    def test_all_ready_files_keep_existing_finite_acceptance(self):
        store, ref = self.create(scope=False)
        value = self.collect(store, ref)
        self.assertTrue(value['accepted'])
        self.assertTrue(all(state['state'] == 'verified' for state in value['steps'].values()))

    def test_true_local_consumer_waits_while_unrelated_replay_is_collected(self):
        store, ref = self.create(dependency='proven')
        self.block(store)
        value = self.collect(store, ref)
        self.assertEqual(value['steps']['replay']['state'], 'verified')
        self.assertEqual(value['steps']['source']['state'], 'pending')
        waiting = task.partition(value)['waiting']
        self.assertIn('source', [row['step'] for row in waiting])
        self.assertFalse(value['accepted'])

    def test_missing_local_basis_neither_authorizes_consumer_nor_blocks_replay(self):
        store, ref = self.create(dependency='missing')
        self.block(store)
        value = self.collect(store, ref)
        self.assertEqual(value['steps']['replay']['state'], 'verified')
        self.assertEqual(value['steps']['source']['state'], 'pending')
        self.assertIn('source', [row['step'] for row in task.partition(value)['unresolved']])
        self.assertFalse(value['accepted'])
