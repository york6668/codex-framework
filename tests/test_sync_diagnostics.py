import contextlib
import errno
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import codex_framework as cf
import framework_refresh as rf


class GitDiagnosticTests(unittest.TestCase):
    def git_failure(self, stderr):
        result = SimpleNamespace(returncode=128, stdout="", stderr=stderr)
        with mock.patch.object(cf.subprocess, "run", return_value=result) as run:
            with self.assertRaises(cf.GitError) as raised:
                cf.git(Path("/synthetic/not-accessed"), "fetch")
        self.assertEqual(run.call_count, 1)
        return raised.exception

    def test_local_denial_and_remote_authentication_are_not_equated(self):
        local = self.git_failure("error: cannot open '.git/FETCH_HEAD': Permission denied")
        remote = self.git_failure("git@example.invalid: Permission denied (publickey).")
        self.assertEqual(local.details["category"], "filesystem_access")
        self.assertEqual(remote.details["category"], "authentication")
        self.assertNotEqual(str(local), str(remote))
        self.assertEqual(local.details["exit_code"], 128)

    def test_unlocated_denial_host_trust_network_and_unknown_stay_distinct(self):
        for stderr, category in (
            ("Permission denied", "access_denied"),
            ("Host key verification failed. Permission denied", "host_trust"),
            ("fatal: Could not resolve host: example.invalid", "network"),
            ("fatal: unrelated repository format failure", "unclassified"),
        ):
            with self.subTest(category=category):
                self.assertEqual(self.git_failure(stderr).details["category"], category)

    def test_diagnostic_does_not_retain_raw_secret_or_url(self):
        secret = "synthetic-secret-do-not-retain"
        error = self.git_failure("fatal: Authentication failed for 'https://u:" + secret + "@example.invalid/'")
        self.assertEqual(error.details["category"], "authentication")
        self.assertNotIn(secret, str(error) + json.dumps(error.details))
        self.assertNotIn("https://", str(error) + json.dumps(error.details))

    def test_system_launch_failure_and_timeout_preserve_categories_without_retry(self):
        for original, category, number in (
            (PermissionError(errno.EPERM, "synthetic OS refusal"), "process_access", errno.EPERM),
            (FileNotFoundError(errno.ENOENT, "synthetic missing binary"), "process_unavailable", errno.ENOENT),
            (subprocess.TimeoutExpired("git", 1), "timeout", None),
        ):
            with self.subTest(category=category):
                with mock.patch.object(cf.subprocess, "run", side_effect=original) as run:
                    with self.assertRaises(cf.GitError) as raised:
                        cf.git(Path("/synthetic/not-accessed"), "fetch")
                self.assertEqual(run.call_count, 1)
                self.assertEqual(raised.exception.details["category"], category)
                self.assertEqual(raised.exception.details["errno"], number)

    def test_failure_keeps_installed_state_and_diagnostic_without_assigning_user_repair(self):
        saved = {"status": "current", "observed_revision": "a" * 40,
                 "applied_revision": "a" * 40}
        framework = SimpleNamespace(
            lock=contextlib.nullcontext,
            read_state=lambda: {"sync": saved},
            load_state=lambda: {},
            save_state=lambda old, sync: (saved.clear(), saved.update(sync)),
        )
        error = cf.GitError("fetch", "filesystem_access", "本机文件访问被拒绝", exit_code=128)
        for notify in (True, False):
            with mock.patch.object(rf, "local_snapshot", side_effect=error) as read:
                result = rf.refresh(framework, cloud_error="synthetic native read unavailable",
                                    expected_revision="a" * 40)
            read.assert_called_once()
            self.assertEqual(result["status"], "pending-user")  # Legacy output preserved.
            self.assertEqual(result["applied_revision"], "a" * 40)
            self.assertEqual(result["notify"], notify)
            self.assertEqual(result["last_git_error"]["category"], "filesystem_access")
            self.assertNotIn("待用户处理", result["message"])
            self.assertIn("不证明缺少用户授权", result["message"])
        recovered=rf.refresh(framework,revision="a"*40,expected_revision="a"*40)
        self.assertEqual(recovered['status'],'current')
        self.assertNotIn('local_error',recovered)
        self.assertEqual(recovered['last_git_error']['scope'],
                         'last_failed_git_attempt_not_current_authorization')

    def test_explicit_fetch_keeps_its_existing_opt_in_semantics(self):
        with mock.patch.object(cf.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='',stderr='')) as run:
            cf.git(Path('/synthetic/not-accessed'),'fetch')
        self.assertNotIn('--no-lazy-fetch',run.call_args.args[0])
        self.assertEqual(run.call_count,1)


class SourceBaselineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="framework-baseline-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.git("init", "-b", cf.BRANCH)
        self.git("config", "user.email", "fixture@example.invalid")
        self.git("config", "user.name", "Fixture")
        self.git("remote", "add", "origin", cf.REMOTE)
        self.commit("base")
        self.base = self.git("rev-parse", "HEAD")
        self.git("checkout", "-b", "fixture-selected")
        self.commit("selected")
        self.selected = self.git("rev-parse", "HEAD")
        self.git("checkout", cf.BRANCH)
        self.commit("local-candidate")
        self.head = self.git("rev-parse", "HEAD")
        # A stale cache can claim the local candidate equals origin.
        self.git("update-ref", "refs/remotes/origin/" + cf.BRANCH, self.head)
        self.framework = cf.Framework(self.repo, self.root / "home")

    def git(self, *args):
        return subprocess.check_output(["git", "-C", str(self.repo), *args],
                                       stderr=subprocess.PIPE, text=True).strip()

    def commit(self, text):
        (self.repo / "fixture.txt").write_text(text)
        self.git("add", "fixture.txt")
        self.git("commit", "-m", text)

    def test_stale_tracking_cannot_replace_selected_baseline_in_normal_cli(self):
        (self.repo / "fixture.txt").write_text("protected uncommitted work")
        before = self.git("status", "--porcelain")
        original = cf.git
        calls = []

        def read_only_git(repo, *args, **kwargs):
            calls.append(args)
            self.assertNotIn(args[0], ("fetch", "push", "merge", "update-ref"))
            self.assertTrue(kwargs.get('no_lazy_fetch'))
            return original(repo, *args, **kwargs)

        output = io.StringIO()
        with mock.patch.object(cf, "git", side_effect=read_only_git), contextlib.redirect_stdout(output):
            code = cf.main(["--repo", str(self.repo), "--home", str(self.framework.home),
                            "sync-status", "--revision", self.selected])
        value = json.loads(output.getvalue())
        baseline = value["source_baseline"]
        self.assertEqual(code, 0)
        self.assertEqual((baseline["status"], baseline["ahead"], baseline["behind"]), ("diverged", 1, 1))
        self.assertEqual(baseline["selected_revision"], self.selected)
        self.assertEqual(baseline["cached_tracking_revision"], self.head)
        self.assertFalse(baseline["cached_tracking_matches_selected"])
        self.assertFalse(baseline["remote_read_performed"])
        self.assertFalse(baseline["working_tree_compared"])
        self.assertEqual(value["publication"]["status"], "not-checked")
        self.assertEqual(self.git("status", "--porcelain"), before)
        self.assertEqual(self.git("rev-parse", "HEAD"), self.head)
        self.assertTrue(calls)

    def test_cached_sync_current_does_not_claim_live_publication_or_query_git(self):
        with mock.patch.object(rf, "source_baseline") as compare:
            value = rf.sync_status(self.framework)
        compare.assert_not_called()
        self.assertEqual(value["publication"]["status"], "not-checked")
        self.assertNotIn("source_baseline", value)

    def test_selected_same_commit_and_missing_object_have_explicit_distinct_results(self):
        self.assertEqual(rf.source_baseline(self.framework, self.head)["status"], "same-commit")
        absent = rf.source_baseline(self.framework, "f" * 40)
        self.assertEqual(absent["status"], "unknown")
        self.assertEqual(absent["selected_revision"], "f" * 40)
        self.assertNotIn("head", absent)
        self.assertIn("error_details", absent)

    def test_invalid_selected_revision_and_wrong_origin_do_not_adopt_cache(self):
        for invalid in (None, "origin/" + cf.BRANCH, "short", []):
            with self.subTest(invalid=invalid), self.assertRaises(cf.SyncError):
                rf.source_baseline(self.framework, invalid)
        self.git("remote", "set-url", "origin", "https://example.invalid/unapproved.git")
        value = rf.source_baseline(self.framework, self.selected)
        self.assertEqual(value["status"], "unknown")
        self.assertNotIn("head", value)

    def test_shallow_boundary_cannot_turn_missing_history_into_divergence_counts(self):
        (self.repo / '.git/shallow').write_text(self.selected+'\n')
        result=rf.source_baseline(self.framework,self.selected)
        self.assertEqual(result['status'],'history-incomplete')
        self.assertEqual(result['selected_revision'],self.selected)
        self.assertNotIn('ahead',result)
        self.assertNotIn('behind',result)
        self.assertEqual(rf.source_baseline(self.framework,self.head)['status'],'same-commit')

    def promisor_result(self, selected):
        remote=self.root/'empty-remote.git';trace=self.root/'git.trace'
        subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
        self.git('config','url.'+remote.as_uri()+'.insteadOf',cf.REMOTE)
        self.git('config','extensions.partialClone','origin')
        self.git('config','remote.origin.promisor','true')
        self.git('config','remote.origin.partialclonefilter','blob:none')
        restricted={'GIT_TRACE':str(trace),'GIT_CONFIG_COUNT':'2',
                    'GIT_CONFIG_KEY_0':'protocol.allow','GIT_CONFIG_VALUE_0':'never',
                    'GIT_CONFIG_KEY_1':'protocol.file.allow','GIT_CONFIG_VALUE_1':'always'}
        with mock.patch.dict(os.environ,restricted):
            os.environ.pop('GIT_NO_LAZY_FETCH',None)
            result=rf.source_baseline(self.framework,selected)
        observed=trace.read_text()
        self.assertNotIn('built-in: git fetch ',observed)
        self.assertNotIn('built-in: git upload-pack ',observed)
        self.assertNotIn('remote-https',observed)
        self.assertEqual(result['status'],'unknown')
        self.assertFalse(result['remote_read_performed'])
        return result

    def test_promisor_missing_object_never_invokes_implicit_fetch(self):
        self.promisor_result('f'*40)

    def test_promisor_missing_parent_during_history_walk_never_fetches(self):
        tree=self.git('rev-parse','HEAD^{tree}')
        raw=('tree '+tree+'\nparent '+'f'*40+'\n'
             'author Fixture <fixture@example.invalid> 1700000000 +0000\n'
             'committer Fixture <fixture@example.invalid> 1700000000 +0000\n\n'
             'owned partial history\n')
        created=subprocess.run(['git','-C',str(self.repo),'hash-object','-t','commit','-w','--stdin'],
                               input=raw,text=True,capture_output=True,check=True)
        result=self.promisor_result(created.stdout.strip())
        self.assertNotIn('ahead',result)
        self.assertNotIn('behind',result)

    def test_unsupported_no_lazy_option_is_unknown_without_unprotected_retry(self):
        result=SimpleNamespace(returncode=129,stdout='',stderr='unknown option: --no-lazy-fetch')
        with mock.patch.object(cf.subprocess,'run',return_value=result) as run:
            out=rf.source_baseline(self.framework,self.selected)
        self.assertEqual(out['status'],'unknown')
        self.assertEqual(run.call_count,1)
        self.assertIn('--no-lazy-fetch',run.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
