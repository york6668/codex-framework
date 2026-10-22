import json
import io
import os
from contextlib import redirect_stdout
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import observation


class ObservationTests(unittest.TestCase):
    def healthy(self, service):
        return {'service': service, 'owned_local_binding': True, 'container_healthy': True}

    def test_requires_container_and_protocol(self):
        calls = []
        actual = observation.collect({'node_id': 'test-node'}, self.healthy, calls.append)
        self.assertEqual(actual['status'], 'HEALTHY')
        self.assertEqual(calls, list(observation.SERVICES))

    def test_wrong_container_binding_is_not_probed(self):
        def wrong(service):
            return dict(self.healthy(service), owned_local_binding=False)
        with patch.object(observation, 'ping_service') as ping:
            actual = observation.collect({}, wrong, ping)
        self.assertEqual(actual['status'], 'UNHEALTHY')
        ping.assert_not_called()

    def test_probe_error_preserves_unknown_without_credentials(self):
        def fail(service):
            raise OSError('private-connection-detail')
        actual = observation.collect({}, self.healthy, fail)
        self.assertEqual(actual['status'], 'UNKNOWN')
        self.assertNotIn('private-connection-detail', json.dumps(actual))

    def test_atomic_private_bounded_output(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.json'
            observation.atomic_json(path, {'status': 'OBSERVING'})
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            with self.assertRaises(ValueError):
                observation.atomic_json(path, {'large': 'x' * observation.MAX_BYTES})
            self.assertEqual(json.loads(path.read_text())['status'], 'OBSERVING')

    def test_replace_failure_does_not_publish_success(self):
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'source.json'
            observation.atomic_json(path, {'status': 'OBSERVING'})
            with patch('observation.os.replace', side_effect=PermissionError):
                with self.assertRaises(PermissionError):
                    observation.atomic_json(path, {'status': 'HEALTHY'})
            self.assertEqual(json.loads(path.read_text())['status'], 'OBSERVING')
            self.assertEqual(len(list(Path(folder).iterdir())), 1)

    def test_observed_actions_preserve_manager_failure(self):
        for action in observation.OBSERVED_ACTIONS:
            with self.subTest(action=action), patch.object(observation, 'main', return_value=2) as publish:
                self.assertEqual(observation.run_action(action, [], lambda *_: 7), 7)
                publish.assert_called_once_with({'action': action, 'exit_code': 7})

    def test_observer_failure_is_visible_after_successful_action(self):
        with patch.object(observation, 'main', return_value=2):
            self.assertEqual(observation.run_action('status', [], lambda *_: 0), 2)

    def test_original_exception_is_preserved_and_observed(self):
        def fail(*_):
            raise RuntimeError('manager failed')
        with patch.object(observation, 'main', return_value=0) as publish:
            with self.assertRaisesRegex(RuntimeError, 'manager failed'):
                observation.run_action('sync', [], fail)
            publish.assert_called_once_with({'action': 'sync', 'exit_code': 1})

    def test_unrelated_action_does_not_collect(self):
        with patch.object(observation, 'main') as publish:
            self.assertEqual(observation.run_action('report', [], lambda *_: 0), 0)
            publish.assert_not_called()

    def test_explicit_observe_does_not_execute_manager_action(self):
        with patch.object(observation, 'main', return_value=0) as publish:
            self.assertEqual(observation.run_action('observe', [], lambda *_: self.fail('unexpected dispatch')), 0)
            publish.assert_called_once_with()

    def test_identity_failure_withdraws_previous_healthy_claim(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'workstation/mi-local/observation.json'
            observation.atomic_json(source, {'status': 'HEALTHY'})
            with patch.dict(os.environ, {'CODEX_HOME': folder}), \
                    patch.object(observation.subprocess, 'check_output', side_effect=OSError), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(observation.main(), 2)
            self.assertEqual(json.loads(source.read_text())['status'], 'OBSERVING')
            self.assertEqual(json.loads(source.with_name('previous-observation.json').read_text())['status'], 'HEALTHY')

    def test_first_write_failure_cannot_leave_old_claim_active(self):
        with tempfile.TemporaryDirectory() as folder:
            source = Path(folder) / 'workstation/mi-local/observation.json'
            observation.atomic_json(source, {'status': 'HEALTHY'})
            with patch.dict(os.environ, {'CODEX_HOME': folder}), \
                    patch.object(observation, 'atomic_json', side_effect=PermissionError), \
                    redirect_stdout(io.StringIO()):
                self.assertEqual(observation.main(), 2)
            self.assertFalse(source.exists())
            self.assertEqual(json.loads(source.with_name('previous-observation.json').read_text())['status'], 'HEALTHY')


if __name__ == '__main__':
    unittest.main()
