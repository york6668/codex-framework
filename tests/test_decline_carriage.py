"""Offline native-output/consumer tests, not a model or approval experiment."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

import task_runtime as task
import task_worker


class DeclineCarriageTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='offline-native-decline-')
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)

    def execute(self, events, malformed=False):
        binary = self.root / 'offline-protocol'
        source = '#!' + sys.executable + '\nimport sys\nsys.stdin.read()\n'
        source += '\n'.join('print(' + repr(json.dumps(e)) + ', flush=True)' for e in events) + '\n'
        if malformed:
            source += "print('not-json', flush=True)\n"
        binary.write_text(source)
        binary.chmod(0o755)
        with mock.patch('task_worker.shutil.which', return_value=str(binary)):
            return task_worker.execute('offline parser input; no model', str(self.root), timeout=5)

    def events(self, kind, ending):
        return [{'type': 'thread.started', 'thread_id': 'offline-session'},
                {'type': 'item.completed', 'item': {'id': 'offline-declined-item',
                    'type': kind, 'status': 'declined'}}, ending]

    def store(self):
        spec = {'request_id': 'original-incident', 'requirement': 'Diagnose the execution anomaly, retaining its original goal',
            'acceptance_version': 'v1', 'workspace': str(self.root), 'max_runs': 1,
            'steps': [{'id': 'a', 'instruction': 'Exact affected offline effect',
                       'checks': [{'kind': 'message', 'equals': '42'}]},
                      {'id': 'b', 'instruction': 'Independent diagnosis',
                       'checks': [{'kind': 'message', 'equals': '42'}]}]}
        store = task.Store(self.root / 'private-codex-home', 'offline-incident')
        value = store.create('offline-incident', spec)
        return store, value

    def consume(self, worker):
        store, value = self.store()
        value, execution = task.reserve(store, value['revision'])
        return store, task.finish(store, value['revision'], execution['id'], worker)

    def assert_scoped_decline(self, store, value):
        self.assertEqual(value['steps']['a']['wait']['kind'], 'explicit_denial')
        self.assertEqual(value['steps']['a']['wait']['next'], 'retain_original_native_denial')
        observation = value['steps']['a']['evidence'][-1]
        self.assertTrue(observation.get('native_permission_declined') is True
                        or observation['error'] == 'native_permission_declined')
        self.assertEqual(len(value['restrictions']), 1)
        result = task.view(value)
        self.assertEqual(result['work_partition']['ready'], ['b'])
        self.assertEqual(result['next_action']['step'], 'b')
        self.assertEqual(result['spec']['requirement'], 'Diagnose the execution anomaly, retaining its original goal')
        self.assertFalse(result['accepted'])
        self.assertNotIn('approval_required', value['spec']['steps'][0])
        with self.assertRaisesRegex(task.TaskError, 'cannot revoke'):
            task.change(store, {'operation': 'reconcile', 'expected_revision': value['revision'],
                'step': 'a', 'source_ref': 'offline-file-exists'})

    def test_terminal_errors_do_not_erase_decline_before_runtime_consumption(self):
        for kind, ending in [('command_execution', {'type': 'turn.failed', 'error': {'message': 'offline terminal failure'}}),
                             ('file_change', {'type': 'error', 'message': 'offline stream error'})]:
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(dir=self.root) as case:
                previous = self.root
                self.root = Path(case)
                try:
                    worker = self.execute(self.events(kind, ending))
                    self.assertEqual(worker['status'], 'failed')
                    self.assertEqual(worker['error'], ending['type'])
                    self.assert_scoped_decline(*self.consume(worker))
                finally:
                    self.root = previous

    def test_unknown_transport_and_original_denial_both_survive(self):
        worker = self.execute(self.events('command_execution', {'type': 'item.completed',
            'item': {'type': 'agent_message', 'text': 'offline result'}}), malformed=True)
        self.assertEqual(worker['status'], 'unknown')
        self.assertIn('JSONDecodeError', worker['error'])
        store, value = self.consume(worker)
        self.assert_scoped_decline(store, value)
        self.assertIn('unresolved_execution', value['steps']['a'])
        self.assertIsNone(value['native_session_id'])

    def test_completed_final_after_decline_does_not_accept_the_effect(self):
        worker = self.execute(self.events('file_change', {'type': 'turn.completed', 'usage': {}}))
        self.assertEqual(worker['status'], 'failed')
        self.assertEqual(worker['error'], 'native_permission_declined')
        self.assert_scoped_decline(*self.consume(worker))

    def test_ordinary_failure_does_not_become_denial_or_new_user_approval(self):
        worker = self.execute([{'type': 'thread.started', 'thread_id': 'offline-session'},
            {'type': 'item.completed', 'item': {'id': 'ordinary-command', 'type': 'command_execution',
                'status': 'completed', 'exit_code': 7}},
            {'type': 'turn.failed', 'error': {'message': 'ordinary execution failure'}}])
        self.assertNotIn('native_permission_declined', worker)
        store, value = self.consume(worker)
        self.assertEqual(value['steps']['a']['wait']['kind'], 'failed')
        self.assertEqual(value['restrictions'], [])
        self.assertEqual(task.view(value)['next_action']['step'], 'b')
        self.assertEqual(worker['tool_failures'][0]['exit_code'], 7)

    def test_amendment_does_not_clear_original_refusal_or_repeat_its_effect(self):
        worker = self.execute(self.events('command_execution', {'type': 'turn.failed', 'error': {}}))
        store, value = self.consume(worker)
        spec = copy.deepcopy(value['spec'])
        spec['acceptance_version'] = 'v2'
        value = task.change(store, {'operation': 'amend', 'expected_revision': value['revision'], 'spec': spec})
        self.assertEqual(task.view(value)['work_partition']['ready'], ['b'])
        denied_runs = value['steps']['a']['runs']
        calls = []
        def independent(prompt, *args, **kwargs):
            self.assertIn('Current step: Independent diagnosis', prompt)
            calls.append(prompt)
            return {'status': 'completed', 'session_id': 'offline-session', 'message': '42',
                'prompt_sent': True, 'message_sha256': hashlib.sha256(b'42').hexdigest()}
        with mock.patch('task_worker.execute', side_effect=independent):
            value = task.run_once(store, value['revision'])
        self.assertEqual(len(calls), 1)
        self.assertEqual(value['steps']['a']['runs'], denied_runs)
        self.assertEqual(value['steps']['b']['state'], 'verified')
        self.assertFalse(value['accepted'])
