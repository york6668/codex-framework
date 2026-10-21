"""Product version names and physical loading, using owned temporary packages."""
import json
import os
from pathlib import Path
import subprocess
import sys
import unittest

from test_framework import FixtureCase, seed_runtime, run_git
import codex_framework as cf
import execution_check as check
import framework_runtime as runtime


class ReleaseVersionTests(FixtureCase):
    def setUp(self):
        super().setUp()
        path = self.repo / 'scripts/codex_framework.py'
        path.write_text(path.read_text().replace(
            'RELEASE_VERSION = "' + cf.RELEASE_VERSION + '"', 'RELEASE_VERSION = "0.1.0"', 1))
        self.revision = seed_runtime(self.repo)

    def cli(self, command, *, source=False, payload=None):
        entry = self.repo / 'scripts/codex_framework.py' if source else runtime.launcher_path(self.a)
        return subprocess.run([sys.executable, '-B', str(entry), '--repo', str(self.repo),
                               '--home', str(self.a.home), '--codex-home', str(self.a.codex_home), command],
                              input=json.dumps(payload) if payload is not None else None,
                              capture_output=True, text=True, timeout=10,
                              env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))

    def test_matching_named_install_and_actual_version_report(self):
        result = runtime.install(self.a, self.revision, release_version='0.1.0')
        self.assertEqual(result['release_version'], '0.1.0')
        response = self.cli('version')
        self.assertEqual(response.returncode, 0, response.stderr)
        value = json.loads(response.stdout)
        self.assertEqual(value['version'], '0.1.0')
        self.assertEqual(value['runtime']['tool_revision'], self.revision)
        for key in ('tool_version', 'active_version', 'document_version'):
            self.assertEqual(value['runtime'][key], '0.1.0')
        response = self.cli('check', payload={})
        self.assertEqual(response.returncode, 1)
        value = json.loads(response.stdout)
        self.assertEqual((value['tool_version'], value['tool_revision']), ('0.1.0', self.revision))

    def test_wrong_named_target_does_not_prepare_or_switch_any_package(self):
        self.assertFalse(self.a.codex_home.exists())
        with self.assertRaisesRegex(cf.SyncError, '产品版本不符'):
            runtime.install(self.a, self.revision, release_version='0.2.0')
        self.assertFalse(self.a.codex_home.exists())
        with self.assertRaises(cf.SyncError):
            runtime.install(self.a, self.revision, release_version='00.1.0')
        self.assertFalse(self.a.codex_home.exists())

    def test_source_version_is_not_reported_as_the_loaded_version(self):
        runtime.install(self.a, self.revision, release_version='0.1.0')
        response = self.cli('version', source=True)
        self.assertEqual(response.returncode, 0, response.stderr)
        value = json.loads(response.stdout)
        self.assertEqual(value['source_version'], '0.1.0')
        self.assertIsNone(value['version'])
        self.assertEqual(value['runtime']['active_version'], '0.1.0')
        self.assertIsNone(value['runtime']['tool_version'])
        self.assertIsNone(check.loaded_tool_version())

    def test_unreadable_state_reports_unknown_version_without_traceback(self):
        state = self.a.codex_home / 'framework-sync/state.json'
        state.parent.mkdir(parents=True)
        state.write_text('{')
        response = self.cli('version', source=True)
        self.assertEqual(response.returncode, 1, response.stdout + response.stderr)
        self.assertEqual(response.stderr, '')
        value = json.loads(response.stdout)
        self.assertIsNone(value['version'])
        self.assertEqual(value['runtime']['status'], 'unknown')
        self.assertIn('同步基线', value['runtime']['reason'])
        self.assertEqual(state.read_text(), '{')

    def test_legacy_has_no_invented_version_and_declaration_is_literal(self):
        self.assertIsNone(runtime.declared_release_version('FORMAT = 1\n'))
        self.assertEqual(runtime.declared_release_version('RELEASE_VERSION = "0.1.0"\n'), '0.1.0')
        for source in ('RELEASE_VERSION = 1', 'RELEASE_VERSION = "01.0.0"',
                       'RELEASE_VERSION = "deadbee"', 'RELEASE_VERSION = value()',
                       'RELEASE_VERSION = "0.1.0"\nRELEASE_VERSION = "0.1.0"',
                       'RELEASE_VERSION: str = "0.1.0"',
                       'RELEASE_VERSION, other = ("0.1.0", 1)',
                       'RELEASE_VERSION = "0.1.0"\nRELEASE_VERSION += "+changed"'):
            with self.subTest(source=source), self.assertRaises(cf.SyncError):
                runtime.declared_release_version(source)

    def test_named_update_and_rollback_keep_versions_separate_from_hash_checks(self):
        runtime.install(self.a, self.revision, release_version='0.1.0')
        path = self.repo / 'scripts/codex_framework.py'
        path.write_text(path.read_text().replace('RELEASE_VERSION = "0.1.0"', 'RELEASE_VERSION = "0.2.0"'))
        run_git(self.repo, 'add', 'scripts/codex_framework.py')
        run_git(self.repo, 'commit', '-m', 'next isolated product version')
        newer = run_git(self.repo, 'rev-parse', 'HEAD')
        runtime.install(self.a, newer, expected_runtime=self.revision, release_version='0.2.0')
        current = json.loads(self.cli('version').stdout)
        self.assertEqual((current['version'], current['runtime']['tool_revision']), ('0.2.0', newer))
        before = runtime.pointer(self.a).read_bytes()
        with self.assertRaises(cf.SyncError):
            runtime.install(self.a, self.revision, rollback=True, release_version='0.2.0')
        self.assertEqual(runtime.pointer(self.a).read_bytes(), before)
        runtime.install(self.a, self.revision, expected_runtime=newer, rollback=True, release_version='0.1.0')
        current = json.loads(self.cli('version').stdout)
        self.assertEqual((current['version'], current['runtime']['tool_revision']), ('0.1.0', self.revision))

    def test_explicit_delete_and_non_name_rebinding_reject_before_named_prepare(self):
        declaration = 'RELEASE_VERSION = "0.1.0"\n'
        changes = ('del RELEASE_VERSION', 'import json as RELEASE_VERSION',
                   'def RELEASE_VERSION(): pass', 'class RELEASE_VERSION: pass',
                   'try:\n    pass\nexcept Exception as RELEASE_VERSION:\n    pass',
                   'match value:\n    case {"version": RELEASE_VERSION}: pass')
        for change in changes:
            with self.subTest(change=change), self.assertRaises(cf.SyncError):
                runtime.declared_release_version(declaration + change)
        path = self.repo / 'scripts/codex_framework.py'
        path.write_text(path.read_text().replace(declaration, declaration + 'del RELEASE_VERSION\n', 1))
        run_git(self.repo, 'add', 'scripts/codex_framework.py')
        run_git(self.repo, 'commit', '-m', 'owned explicit-deletion regression')
        deleted = run_git(self.repo, 'rev-parse', 'HEAD')
        self.assertFalse(self.a.codex_home.exists())
        response = subprocess.run([sys.executable, '-B', cf.__file__, '--repo', str(self.repo),
                                   '--home', str(self.a.home), '--codex-home', str(self.a.codex_home),
                                   'install', '--revision', deleted, '--release-version', '0.1.0'],
                                  capture_output=True, text=True, timeout=10,
                                  env=dict(os.environ, PYTHONDONTWRITEBYTECODE='1'))
        self.assertEqual(response.returncode, 1, response.stdout + response.stderr)
        self.assertIn('唯一字符串常量', response.stderr)
        self.assertFalse(self.a.codex_home.exists())

    def test_existing_startup_entry_routes_to_product_version_then_integrity(self):
        runtime.install(self.a, self.revision, release_version='0.1.0')
        result = self.cli('session-start', payload={'hook_event_name': 'SessionStart', 'source': 'startup'})
        self.assertEqual(result.returncode, 0, result.stderr)
        context = json.loads(result.stdout)['hookSpecificOutput']['additionalContext']
        self.assertIn('按产品版本规划、加载与更新', context)
        self.assertIn('已完成的版本核对直接复用', context)
        self.assertIn('用 version 查看实际产品版本', context)
        self.assertIn('再用 sync-status 核绑定', context)
        self.assertIn('hash 只作内容及并发校验', context)
        self.assertIn('未编号保持未编号', context)

    def test_named_and_native_cli_options_keep_their_own_scope(self):
        prefix = [sys.executable, '-B', cf.__file__, '--repo', str(self.repo),
                  '--home', str(self.a.home), '--codex-home', str(self.a.codex_home)]
        help_result = subprocess.run(prefix + ['--help'], capture_output=True, text=True, timeout=10)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        self.assertIn('--release-version', help_result.stdout)
        self.assertIn('--native-cli', help_result.stdout)
        for command, flags, error in (
                ('install', ['--native-cli', sys.executable], '--native-cli 仅用于 install-hook'),
                ('install-hook', ['--release-version', '0.1.0'], '--release-version 仅用于 install/runtime-rollback')):
            response = subprocess.run(prefix + [command] + flags, capture_output=True, text=True, timeout=10)
            self.assertEqual(response.returncode, 2, response.stdout + response.stderr)
            self.assertIn(error, response.stderr)
        self.assertFalse(self.a.codex_home.exists())


if __name__ == '__main__':
    unittest.main()
