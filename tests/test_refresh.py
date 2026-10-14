import contextlib
import io
import json
import subprocess
import sys
from unittest import mock

from test_framework import FixtureCase, GitFixtureCase, put, run_git
import codex_framework as cf
import framework_refresh as rf


def snapshot(framework, revision="a" * 40):
    files = framework.collect()
    return {"source": cf.REMOTE, "branch": cf.BRANCH, "revision": revision,
            "truncated": False,
            "tree": [{"path": name, "mode": "100644", "type": "blob", "sha": rf.blob_sha(value)}
                     for name, value in files.items()],
            "files": {name: value.decode() for name, value in files.items()}}


class RefreshTests(FixtureCase):
    def install_snapshot(self, revision="a" * 40):
        return rf.refresh(self.a, snapshot=snapshot(self.a, revision),
                          expected_revision=rf.sync_status(self.a)["expected_revision"])

    def test_native_success_never_requires_local_git(self):
        with mock.patch.object(rf, "local_snapshot", side_effect=AssertionError("local route must not run")):
            result = self.install_snapshot()
        self.assertEqual(result["route"], "cloud")
        self.assertEqual(result["applied_revision"], "a" * 40)
        self.assertEqual(self.a.collect(local=True), self.a.collect())

    def test_unchanged_revision_does_not_fetch_snapshot_or_reinstall(self):
        self.install_snapshot()
        files = list(self.a.home.rglob("*"))
        before = {p: p.stat().st_mtime_ns for p in files if p.is_file()}
        with mock.patch.object(rf, "local_snapshot", side_effect=AssertionError("no Git")), \
                mock.patch.object(self.a, "reconcile", side_effect=AssertionError("no reinstall")):
            result = rf.refresh(self.a, revision="a" * 40, expected_revision="a" * 40)
        self.assertEqual(result["changed"], [])
        self.assertEqual(before, {p: p.stat().st_mtime_ns for p in before})

    def test_new_version_requests_complete_snapshot(self):
        self.install_snapshot()
        before = self.a.state_path.read_bytes()
        result = rf.refresh(self.a, revision="b" * 40, expected_revision="a" * 40)
        self.assertEqual(result["status"], "snapshot-required")
        self.assertEqual(before, self.a.state_path.read_bytes())
        put(self.repo / ".codex/memory-framework.md", "updated shared method\n")
        result = self.install_snapshot("b" * 40)
        self.assertIn(".codex/memory-framework.md", result["changed"])
        self.assertEqual(self.a.target(".codex/memory-framework.md").read_text(), "updated shared method\n")

    def test_conflict_preserves_local_btc_and_applies_independent_update(self):
        self.install_snapshot()
        path = self.a.target(".codex/AGENTS.md")
        local = path.read_text() + "\nBTC local exception\n"
        put(path, local)
        put(self.repo / ".codex/AGENTS.md", "new upstream global rule\n")
        put(self.repo / ".codex/memory-framework.md", "new memory method\n")
        result = self.install_snapshot("b" * 40)
        self.assertEqual(result["status"], "conflicts")
        self.assertEqual(result["applied_revision"], "a" * 40)
        self.assertEqual(result["observed_revision"], "b" * 40)
        self.assertEqual(path.read_text(), local)
        self.assertEqual(self.a.target(".codex/memory-framework.md").read_text(), "new memory method\n")
        self.assertEqual(rf.cached_files(self.a)[".codex/AGENTS.md"], b"new upstream global rule\n")
        result = self.install_snapshot("b" * 40)
        self.assertFalse(result["notify"])
        put(path, "new upstream global rule\n")
        result = self.install_snapshot("b" * 40)
        self.assertEqual(result["applied_revision"], "b" * 40)

    def test_both_routes_fail_keep_installed_version_and_notify_once(self):
        self.install_snapshot()
        before = self.a.collect(local=True)
        for expected_notify in (True, False):
            with mock.patch.object(rf, "local_snapshot", side_effect=cf.SyncError("本机认证不可用")) as local:
                result = rf.refresh(self.a, cloud_error="原生连接无权读取", expected_revision="a" * 40)
                local.assert_called_once()
            self.assertEqual(result["status"], "pending-user")
            self.assertEqual(result["notify"], expected_notify)
            self.assertEqual(result["applied_revision"], "a" * 40)
            self.assertEqual(self.a.collect(local=True), before)
        recovered = rf.refresh(self.a, revision="a" * 40, expected_revision="a" * 40)
        self.assertEqual(recovered["status"], "current")
        self.assertNotIn("cloud_error", self.a.read_state()["sync"])

    def test_missing_truncated_wrong_source_or_extra_file_cannot_delete_data(self):
        self.install_snapshot()
        for kind in ("missing", "truncated", "wrong-source", "extra", "bad-content", "symlink"):
            with self.subTest(kind=kind):
                data = snapshot(self.a, "b" * 40)
                if kind == "missing":
                    del data["files"][rf.REFERENCE]
                elif kind == "truncated":
                    data["truncated"] = True
                elif kind == "wrong-source":
                    data["source"] = "https://example.invalid/framework.git"
                elif kind == "extra":
                    data["files"][".codex/auth.json"] = "not shared"
                elif kind == "symlink":
                    data["tree"][0]["mode"] = "120000"
                else:
                    data["files"][".codex/AGENTS.md"] = "changed outside returned tree"
                before = self.a.collect(local=True)
                with mock.patch.object(rf, "local_snapshot", side_effect=cf.SyncError("本机不可用")):
                    result = rf.refresh(self.a, snapshot=data, expected_revision="a" * 40)
                self.assertEqual(result["status"], "pending-user")
                self.assertEqual(self.a.collect(local=True), before)

    def test_private_files_and_unrelated_skills_are_never_modified(self):
        private = {".codex/config.toml": "existing runtime", ".codex/memories/notes.md": "local memory",
                   ".codex/workstation/local.md": "device paths", ".agents/skills/btc-core-strategy/SKILL.md": "BTC methods",
                   ".codex/btc/runtime.json": "local asset facts"}
        for name, content in private.items():
            put(self.a.home / name, content)
        self.install_snapshot()
        for name, content in private.items():
            self.assertEqual((self.a.home / name).read_text(), content)

    def test_stale_concurrent_snapshot_does_not_roll_back_newer_revision(self):
        self.install_snapshot()
        older = snapshot(self.a, "b" * 40)
        put(self.repo / ".codex/memory-framework.md", "latest\n")
        self.install_snapshot("c" * 40)
        result = rf.refresh(self.a, snapshot=older, expected_revision="a" * 40)
        self.assertEqual(result["status"], "superseded")
        self.assertEqual(self.a.target(".codex/memory-framework.md").read_text(), "latest\n")
        self.assertEqual(rf.sync_status(self.a)["applied_revision"], "c" * 40)

    def test_lock_prevents_concurrent_cli_refresh_before_fallback_network(self):
        self.install_snapshot()
        arguments = [sys.executable, str(self.repo / "scripts/codex_framework.py"), "--home", str(self.a.home),
                     "refresh", "--cloud-error", "测试连接不可用", "--expected-revision", "a" * 40]
        with self.a.lock():
            result = subprocess.run(arguments, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 1)
        self.assertIn("已有框架同步操作", result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(rf.sync_status(self.a)["applied_revision"], "a" * 40)

    def test_default_status_uses_native_snapshot_not_older_source_checkout(self):
        self.install_snapshot()
        data = snapshot(self.a, "b" * 40)
        data["files"][".codex/memory-framework.md"] = "native newer\n"
        for item in data["tree"]:
            if item["path"] == ".codex/memory-framework.md":
                item["sha"] = rf.blob_sha(b"native newer\n")
        rf.refresh(self.a, snapshot=data, expected_revision="a" * 40)
        with contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(cf.main(["--home", str(self.a.home), "status"]), 0)
        self.assertIn("update=0", output.getvalue())
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cf.main(["--home", str(self.a.home), "apply"]), 0)
        self.assertEqual(self.a.target(".codex/memory-framework.md").read_text(), "native newer\n")

    def test_hook_install_merges_existing_handlers_and_is_idempotent(self):
        config = {"description": "existing", "hooks": {"SessionStart": [
            {"matcher": "resume", "hooks": [{"type": "command", "command": "echo existing"}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "echo stopped"}]}]}}
        path = self.a.codex_home / "hooks.json"
        put(path, json.dumps(config))
        output = rf.install_hook(self.a)
        self.assertEqual(output["trust"], "native-review-required")
        mtime = path.stat().st_mtime_ns
        rf.install_hook(self.a)
        self.assertEqual(path.stat().st_mtime_ns, mtime)
        installed = json.loads(path.read_text())
        self.assertEqual(installed["hooks"]["SessionStart"][0], config["hooks"]["SessionStart"][0])
        self.assertEqual(installed["hooks"]["Stop"], config["hooks"]["Stop"])
        self.assertEqual(len(installed["hooks"]["SessionStart"]), 2)
        self.assertNotIn("UserPromptSubmit", installed["hooks"])
        self.assertFalse((self.a.codex_home / "config.toml").exists())
        self.assertFalse(any("trust" in p.name for p in self.a.home.rglob("*")))

    def test_hook_emits_context_only_for_start_resume_clear_and_never_blocks(self):
        self.install_snapshot()
        spec = rf.install_hook(self.a)
        for source in ("startup", "resume", "clear", "compact", "ordinary-message"):
            event = {"hook_event_name": "SessionStart", "source": source}
            result = subprocess.run(spec["command"], shell=True, input=json.dumps(event),
                                    capture_output=True, text=True, cwd=self.root, timeout=3)
            self.assertEqual(result.returncode, 0, result.stderr)
            output = json.loads(result.stdout)
            if source in ("startup", "resume", "clear"):
                self.assertEqual(output["hookSpecificOutput"]["hookEventName"], "SessionStart")
                self.assertIn(str(self.a.home / rf.REFERENCE), output["hookSpecificOutput"]["additionalContext"])
                self.assertIn("原任务记录", output["hookSpecificOutput"]["additionalContext"])
                self.assertIn("resume/feedback/close_issue", output["hookSpecificOutput"]["additionalContext"])
                self.assertNotIn("continue", output)
            else:
                self.assertEqual(output, {})
        # The hook runs no refresh; its execution does not change the installed version.
        self.assertEqual(rf.sync_status(self.a)["applied_revision"], "a" * 40)


class LocalFallbackTests(GitFixtureCase):
    def test_manual_pull_after_native_refresh_updates_cached_published_source(self):
        initial = run_git(self.receiver, "rev-parse", "HEAD")
        rf.refresh(self.client, snapshot=rf.snapshot_at_revision(self.client, initial), expected_revision="none")
        self.publish_fixture(".codex/memory-framework.md", "new explicit Git revision\n")
        with contextlib.redirect_stdout(io.StringIO()):
            code = cf.main(["--repo", str(self.receiver), "--home", str(self.client.home), "pull", "--apply"])
        self.assertEqual(code, 0)
        self.assertEqual(rf.cached_files(self.client)[".codex/memory-framework.md"], b"new explicit Git revision\n")
        self.assertEqual(rf.sync_status(self.client)["applied_revision"], run_git(self.receiver, "rev-parse", "HEAD"))

    def test_cloud_unavailable_falls_back_to_actual_git_without_switching_checkout(self):
        published = "published by Git\n"
        self.publish_fixture(".codex/memory-framework.md", published)
        run_git(self.receiver, "switch", "-c", "working-task")
        put(self.receiver / ".codex/AGENTS.md", "uncommitted work\n")
        before = run_git(self.receiver, "status", "--porcelain")
        result = rf.refresh(self.client, cloud_error="原生连接在此任务不可用", expected_revision="none")
        self.assertEqual(result["route"], "local")
        self.assertEqual(result["status"], "current")
        self.assertEqual(self.client.target(".codex/memory-framework.md").read_text(), published)
        self.assertEqual(run_git(self.receiver, "branch", "--show-current"), "working-task")
        self.assertEqual(run_git(self.receiver, "status", "--porcelain"), before)

    def test_local_fetch_timeout_is_bounded_and_records_failure(self):
        original = cf.git
        def timed_git(repo, *args, **kwargs):
            if args[0] == "fetch":
                self.assertEqual(kwargs["timeout"], 10)
                raise cf.SyncError("Git 超时")
            return original(repo, *args, **kwargs)
        with mock.patch.object(cf, "git", side_effect=timed_git):
            result = rf.refresh(self.client, cloud_error="云端当前不可用", expected_revision="none")
        self.assertEqual(result["status"], "pending-user")
        self.assertIn("超时", result["local_error"])
