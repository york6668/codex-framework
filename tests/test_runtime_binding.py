"""Isolated N1–N9 runtime binding regressions; no live home, model or service."""

import contextlib
import copy
import io
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
from unittest import mock

from test_framework import FixtureCase, seed_runtime, put, run_git
import codex_framework as cf
import framework_refresh as refresh
import framework_runtime as runtime


class RuntimeBindingTests(FixtureCase):
    def setUp(self):
        super().setUp()
        self.p = seed_runtime(self.repo)
        self.env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

    def install(self, f=None, revision=None, **kwargs):
        return runtime.install(f or self.a, revision or run_git(self.repo, "rev-parse", "HEAD"), **kwargs)

    def cli(self, *arguments, payload=None, f=None):
        f = f or self.a
        return subprocess.run([sys.executable, str(runtime.launcher_path(f)),
                               "--home", str(f.home), "--codex-home", str(f.codex_home), *arguments],
                              input=json.dumps(payload) if payload is not None else None,
                              capture_output=True, text=True, env=self.env, timeout=10)

    def publish(self, mutate=None):
        path = self.repo / ".codex/AGENTS.md"
        path.write_text(path.read_text() + "\nQ isolated fixture.\n")
        if mutate:
            mutate()
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-m", "Q isolated fixture")
        return run_git(self.repo, "rev-parse", "HEAD")

    def installed_state(self):
        return (runtime.pointer(self.a).read_bytes(), self.a.target(".codex/AGENTS.md").read_bytes(),
                self.a.state_path.read_bytes())

    def test_n1_development_dependency_edit_and_missing_checkout_do_not_drift_cli(self):
        self.install()
        before = self.cli("check", payload={})
        module = self.repo / "scripts/execution_check.py"
        module.write_text(module.read_text() + "\ndef dispatch():\n print('unpublished drift')\n return 0\n")
        after = self.cli("check", payload={})
        self.assertEqual((before.returncode, before.stdout), (after.returncode, after.stdout))
        self.repo.rename(self.root / "moved-source")
        result = self.cli("sync-status")
        self.assertEqual(result.returncode, 0, result.stderr)
        binding = json.loads(result.stdout)["runtime"]
        self.assertEqual(binding["status"], "matched")
        self.assertEqual(binding["tool_revision"], self.p)
        self.assertEqual(self.cli("status").returncode, 0)

    def test_n2_update_and_delayed_import_in_old_running_process_stay_physical(self):
        self.install()
        previous = runtime.version_path(self.a, self.p)
        script = ("import sys; sys.path.insert(0,sys.argv[1]); import codex_framework; "
                  "print('pinned',flush=True); sys.stdin.readline(); import source_evidence; "
                  "print(source_evidence.__file__,flush=True)")
        process = subprocess.Popen([sys.executable, "-u", "-c", script, str(previous / "scripts")],
                                   stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   text=True, env=self.env)
        try:
            self.assertEqual(process.stdout.readline().strip(), "pinned")
            q = self.publish()
            self.assertEqual(self.install(revision=q, expected_runtime=self.p)["status"], "installed")
            stdout, stderr = process.communicate("continue\n", timeout=5)
            self.assertEqual(process.returncode, 0, stderr)
            self.assertIn(str(previous / "scripts/source_evidence.py"), stdout)
            binding = json.loads(self.cli("sync-status").stdout)["runtime"]
            self.assertEqual(binding["tool_revision"], q)
            self.assertEqual(binding["status"], "matched")
            self.assertTrue(previous.is_dir())
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()

    def test_n3_document_only_refresh_reports_actual_tool_mismatch(self):
        self.install()
        q = self.publish()
        refresh.refresh(self.a, snapshot=refresh.snapshot_at_revision(self.a, q), expected_revision=self.p)
        result = json.loads(self.cli("sync-status").stdout)
        self.assertEqual(result["document_status"], "current")
        self.assertEqual(result["status"], "runtime-revision-mismatch")
        self.assertEqual(result["runtime"]["tool_revision"], self.p)
        self.assertEqual(result["runtime"]["document_revision"], q)
        self.assertEqual(self.cli("check", payload={}).returncode, 1)

    def test_n3_incompatible_contract_does_not_replace_p(self):
        self.install()
        before = self.installed_state()
        def incompatible():
            path = self.repo / "scripts/framework_runtime.py"
            path.write_text(path.read_text().replace("INPUT_CONTRACT = 1", "INPUT_CONTRACT = 2"))
        q = self.publish(incompatible)
        with self.assertRaisesRegex(cf.SyncError, "契约"):
            self.install(revision=q)
        self.assertEqual(self.installed_state(), before)

    def test_n4_incomplete_module_and_wrong_git_mode_reject_before_changes(self):
        for problem in ("missing", "mode", "symlink"):
            with self.subTest(problem=problem):
                # Each negative candidate branches from the valid committed P.
                run_git(self.repo, "reset", "--hard", self.p)
                if not runtime.active(self.a):
                    self.install(revision=self.p)
                before = self.installed_state()
                def mutate():
                    path = self.repo / "scripts/source_evidence.py"
                    if problem == "missing":
                        path.unlink()
                    elif problem == "mode":
                        path.chmod(0o755)
                    else:
                        path.unlink()
                        path.symlink_to(self.root / "outside.py")
                q = self.publish(mutate)
                with self.assertRaises(cf.SyncError):
                    self.install(revision=q)
                self.assertEqual(self.installed_state(), before)

    def test_n4_managed_hash_and_symlink_damage_are_never_executed(self):
        self.install()
        path = runtime.version_path(self.a, self.p) / "scripts/source_evidence.py"
        original = path.read_bytes()
        marker = self.root / "unsafe-code-executed"
        path.write_text(original.decode() + "\nopen(" + repr(str(marker)) + ",'w').write('bad')\n")
        result = self.cli("check", payload={})
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("hash/mode mismatch", result.stdout)
        self.assertFalse(marker.exists())
        path.write_bytes(original)
        path.unlink()
        path.symlink_to(self.root / "outside.py")
        self.assertNotEqual(self.cli("check", payload={}).returncode, 0)
        self.assertFalse(marker.exists())

    def test_n4_document_snapshot_cannot_install_code_or_expand_targets(self):
        self.install()
        before = self.installed_state()
        snapshot = refresh.snapshot_at_revision(self.a, self.p)
        snapshot["files"]["scripts/execution_check.py"] = "raise RuntimeError('unapproved')"
        with self.assertRaises(cf.SyncError):
            refresh.validate_snapshot(snapshot)
        self.assertEqual(self.installed_state(), before)
        with self.assertRaises(cf.SyncError):
            runtime.version_path(self.a, "../outside")
        with self.assertRaises(cf.SyncError):
            runtime.target(self.a, "../../outside")

    def test_n5_interrupt_after_document_state_is_detected_and_recovered(self):
        self.install()
        before = self.installed_state()
        q = self.publish()
        def interrupt(name):
            if name == "state":
                raise RuntimeError("isolated interruption")
        with mock.patch.object(runtime, "checkpoint", side_effect=interrupt):
            with self.assertRaisesRegex(RuntimeError, "interruption"):
                self.install(revision=q)
        result = json.loads(self.cli("sync-status").stdout)
        self.assertEqual(result["runtime"]["status"], "update-incomplete")
        self.assertNotEqual(result["status"], "current")
        recovered = self.cli("runtime-recover")
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertEqual(json.loads(recovered.stdout)["status"], "recovered")
        self.assertEqual(self.installed_state(), before)
        self.assertEqual(json.loads(self.cli("sync-status").stdout)["runtime"]["status"], "matched")
        self.assertTrue(runtime.version_path(self.a, q).exists())

    def test_n5_interrupt_after_pointer_switch_restores_p(self):
        self.install()
        before = self.installed_state()
        q = self.publish()
        def interrupt(name):
            if name == "active":
                raise RuntimeError("interrupted after pointer")
        with mock.patch.object(runtime, "checkpoint", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                self.install(revision=q)
        self.assertEqual(runtime.active(self.a)["revision"], q)
        self.assertEqual(runtime.recover(self.a)["status"], "recovered")
        self.assertEqual(self.installed_state(), before)

    def test_n5_first_install_interruption_has_pinned_recovery_entry(self):
        # A known old mutable launcher is migrated, never an unknown user file.
        old = runtime.launcher_path(self.a)
        old.parent.mkdir(parents=True)
        old.symlink_to(self.repo / "scripts/codex_framework.py")
        def interrupt(name):
            if name == "launcher":
                raise RuntimeError("first install interrupted")
        with mock.patch.object(runtime, "checkpoint", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                self.install()
        self.assertFalse(old.is_symlink())
        self.assertEqual(json.loads(self.cli("sync-status").stdout)["runtime"]["status"], "update-incomplete")
        self.assertEqual(json.loads(self.cli("runtime-recover").stdout)["status"], "recovered")
        self.assertTrue(old.is_symlink())
        self.assertIsNone(runtime.active(self.a))

    def test_n5_stale_expectation_and_lock_cannot_overwrite_other_update(self):
        self.install()
        before = self.installed_state()
        q = self.publish()
        self.assertEqual(self.install(revision=q, expected_runtime="none")["status"], "superseded")
        self.assertEqual(self.installed_state(), before)
        with self.a.lock():
            blocked = self.cli("install", "--revision", q)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertEqual(self.installed_state(), before)

    def test_n6_conflict_and_unpublished_source_are_preserved(self):
        self.install()
        q = self.publish(lambda: put(self.repo / ".codex/memory-framework.md", "Q independent document"))
        put(self.a.target(".codex/AGENTS.md"), "unpublished local method")
        put(self.a.codex_home / "config.toml", "private fixture configuration")
        module = self.repo / "scripts/execution_check.py"
        module.write_text(module.read_text() + "\n# in-flight source work\n")
        before_source = module.read_bytes()
        result = self.install(revision=q)
        self.assertEqual(result["status"], "conflicts")
        self.assertEqual(runtime.active(self.a)["revision"], self.p)
        self.assertEqual(self.a.target(".codex/AGENTS.md").read_text(), "unpublished local method")
        self.assertEqual(module.read_bytes(), before_source)
        self.assertEqual((self.a.codex_home / "config.toml").read_text(), "private fixture configuration")
        # Existing data-only refresh can still deliver independent documents.
        result = refresh.refresh(self.a, snapshot=refresh.snapshot_at_revision(self.a, q), expected_revision=self.p)
        self.assertIn(".codex/AGENTS.md", result["conflicts"])
        self.assertEqual(self.a.target(".codex/memory-framework.md").read_text(), "Q independent document")
        from test_execution_check import action_case
        self.assertEqual(self.cli("check", payload=action_case()).returncode, 0)

    def test_n6_local_override_same_upstream_and_source_work_are_identified(self):
        self.install()
        local = self.a.target(".codex/memory-framework.md")
        local.write_text("unpublished method refinement")
        q = self.publish()
        put(self.repo / "scripts/execution_check.py", "uncommitted source work")
        result = self.install(revision=q)
        self.assertEqual(result["status"], "installed")
        self.assertIn(".codex/memory-framework.md", result["local_overrides"])
        self.assertIn("scripts/execution_check.py", result["source_overrides"])
        binding = json.loads(self.cli("sync-status").stdout)["runtime"]
        self.assertEqual(binding["status"], "local-overrides")
        self.assertEqual(local.read_text(), "unpublished method refinement")
        self.assertNotEqual((runtime.version_path(self.a, q) / "scripts/execution_check.py").read_text(), "uncommitted source work")

    def test_n6_recovery_does_not_overwrite_post_failure_user_edit(self):
        self.install()
        q = self.publish()
        def interrupt(name):
            if name == "state":
                raise RuntimeError("isolated interruption")
        with mock.patch.object(runtime, "checkpoint", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                self.install(revision=q)
        local = self.a.target(".codex/AGENTS.md")
        local.write_text("later valid work")
        result = runtime.recover(self.a)
        self.assertEqual(result["status"], "recovery-conflicts")
        self.assertIn(".codex/AGENTS.md", result["conflicts"])
        self.assertEqual(local.read_text(), "later valid work")
        self.assertTrue(runtime.journal(self.a).exists())

    def test_n7_custom_home_codex_home_and_known_hook_migration(self):
        custom = cf.Framework(self.repo, self.root / "user with spaces", self.root / "custom codex")
        old_command = shlex.join([sys.executable, str(self.repo / "scripts/codex_framework.py"),
                                  "--home", str(custom.home), "--codex-home", str(custom.codex_home), "session-start"])
        config = {"hooks": {"SessionStart": [{"matcher": "startup", "hooks": [
            {"type": "command", "command": old_command, "timeout": 5},
            {"type": "command", "command": "echo unrelated", "timeout": 9}]}],
            "Stop": [{"hooks": [{"type": "command", "command": "echo stop"}]}]}}
        put(custom.codex_home / "hooks.json", json.dumps(config))
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.root / "platform-home")}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cf.main(["--repo", str(self.repo), "--home", str(custom.home),
                                          "--codex-home", str(custom.codex_home), "install"]), 0)
        self.assertFalse((self.root / "platform-home").exists())
        actual = json.loads((custom.codex_home / "hooks.json").read_text())
        own = actual["hooks"]["SessionStart"][0]["hooks"][0]
        self.assertNotIn(str(self.repo), own["command"])
        self.assertIn(str(runtime.launcher_path(custom)), own["command"])
        self.assertEqual(own["timeout"], 5)
        self.assertEqual(actual["hooks"]["SessionStart"][0]["hooks"][1], config["hooks"]["SessionStart"][0]["hooks"][1])
        self.assertEqual(actual["hooks"]["Stop"], config["hooks"]["Stop"])
        self.assertEqual(self.cli("sync-status", f=custom).returncode, 0)
        hook = subprocess.run(own["command"], shell=True, input=json.dumps({"hook_event_name":"SessionStart","source":"startup"}),
                              text=True, capture_output=True, env=self.env, timeout=5)
        self.assertEqual(hook.returncode, 0, hook.stderr)
        self.assertLess(len(json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"].encode()), 1800)
        self.assertFalse(any("trust" in p.name for p in custom.home.rglob("*")))

    def test_n7_unknown_launcher_and_unapproved_origin_are_not_changed(self):
        put(runtime.launcher_path(self.a), "user-owned executable")
        with self.assertRaises(cf.SyncError):
            self.install()
        self.assertEqual(runtime.launcher_path(self.a).read_text(), "user-owned executable")
        run_git(self.repo, "remote", "set-url", "origin", "https://example.invalid/other.git")
        with self.assertRaisesRegex(cf.SyncError, "来源"):
            self.install()

    def test_n8_rollback_restores_matching_documents_tools_and_keeps_versions(self):
        self.install()
        old_doc = self.a.target(".codex/AGENTS.md").read_bytes()
        q = self.publish()
        self.install(revision=q)
        result = self.cli("runtime-rollback", "--revision", self.p, "--expected-runtime", q)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.a.target(".codex/AGENTS.md").read_bytes(), old_doc)
        info = json.loads(self.cli("sync-status").stdout)
        self.assertEqual(info["document_status"], "rolled-back")
        self.assertEqual(info["runtime"]["status"], "matched")
        self.assertEqual(info["runtime"]["tool_revision"], self.p)
        self.assertEqual(info["observed_revision"], q)
        self.assertEqual(info["applied_revision"], self.p)
        self.assertTrue(runtime.version_path(self.a, q).is_dir())
        self.assertEqual(self.cli("status").returncode, 0)

    def test_n8_retained_package_manifest_cannot_self_authorize_tampered_rollback(self):
        self.install()
        q = self.publish()
        self.install(revision=q)
        directory = runtime.version_path(self.a, self.p)
        m = json.loads((directory / "manifest.json").read_text())
        p = directory / "scripts/source_evidence.py"
        p.write_text(p.read_text() + "\n# altered retained version\n")
        m["files"]["scripts/source_evidence.py"]["sha256"] = runtime.digest(p.read_bytes())
        (directory / "manifest.json").write_bytes(runtime.encoded(m))
        with self.assertRaisesRegex(cf.SyncError, "hash"):
            self.install(revision=self.p, rollback=True)
        self.assertEqual(runtime.active(self.a)["revision"], q)

    def test_n9_package_has_complete_closure_and_preserves_public_check_exit(self):
        self.install()
        directory = runtime.version_path(self.a, self.p)
        manifest, snapshot = runtime.validate(directory)
        self.assertEqual(set(manifest["files"]), {"scripts/" + n for n in runtime.MODULES} | {"snapshot.json"})
        self.assertNotIn("scripts/acceptance_capture.py", manifest["files"])
        expected_documents = {name for name in run_git(self.repo, "ls-tree", "-r", "--name-only", self.p).splitlines()
                              if cf.allowed(name)}
        self.assertEqual(set(snapshot["files"]), expected_documents)
        self.assertEqual(self.cli("status").stdout.strip(),
                         "same=" + str(len(expected_documents)) + " update=0 keep-local=0 conflict=0")
        result = self.cli("check", payload={})
        self.assertEqual(result.returncode, 1)
        self.assertNotIn("Traceback", result.stderr)

    def test_n3_missing_runtime_reports_document_and_attempted_active_identity(self):
        self.install()
        (runtime.version_path(self.a, self.p) / "scripts/source_evidence.py").unlink()
        result = self.cli("sync-status")
        self.assertNotEqual(result.returncode, 0)
        data = json.loads(result.stdout)
        self.assertEqual(data["active_revision"], self.p)
        self.assertEqual(data["document_revision"], self.p)
        self.assertIsNone(data["tool_revision"])

    def test_n4_compile_and_late_import_smoke_failure_leave_active_p(self):
        self.install()
        before = self.installed_state()
        for body in ("this is invalid syntax !!!", "raise RuntimeError('candidate import fails')"):
            run_git(self.repo, "reset", "--hard", self.p)
            q = self.publish(lambda: put(self.repo / "scripts/source_evidence.py", body))
            with self.assertRaises(cf.SyncError):
                self.install(revision=q)
            self.assertEqual(self.installed_state(), before)

    def test_n4_prepared_version_hash_is_not_repaired_by_overwrite(self):
        self.install()
        q = self.publish()
        directory, _, _ = runtime.prepare(self.a, q)
        file = directory / "scripts/execution_check.py"
        file.write_text(file.read_text() + "\n# unknown prepared edit\n")
        damaged = file.read_bytes()
        before = self.installed_state()
        with self.assertRaises(cf.SyncError):
            self.install(revision=q)
        self.assertEqual(file.read_bytes(), damaged)
        self.assertEqual(self.installed_state(), before)

    def test_n5_unsafe_recovery_record_is_rejected_before_any_restore(self):
        self.install()
        q = self.publish()
        def interrupt(name):
            if name == "prepared":
                raise RuntimeError("interrupt")
        with mock.patch.object(runtime, "checkpoint", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                self.install(revision=q)
        before = self.installed_state()
        tx = json.loads(runtime.journal(self.a).read_text())
        tx["changes"]["../../outside"] = {"before":{"data":None},"after":{"data":None}}
        runtime.journal(self.a).write_text(json.dumps(tx))
        with self.assertRaises(cf.SyncError):
            runtime.recover(self.a)
        self.assertEqual(self.installed_state(), before)

    def test_n7_default_home_uses_existing_codex_home_environment_without_rewriting_it(self):
        custom = self.root / "selected-runtime-home"
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(custom)}), \
                mock.patch.object(cf.Path, "home", return_value=self.a.home), \
                contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(cf.main(["--repo", str(self.repo), "install"]), 0)
            self.assertEqual(os.environ["CODEX_HOME"], str(custom))
        self.assertTrue((custom / "framework-sync/runtime/active.json").is_file())
        self.assertFalse(self.a.codex_home.exists())

    def test_n8_rollback_preserves_independent_unpublished_method(self):
        self.install()
        q = self.publish()
        self.install(revision=q)
        path = self.a.target(".codex/memory-framework.md")
        path.write_text("new valid local method")
        self.install(revision=self.p, rollback=True)
        self.assertEqual(path.read_text(), "new valid local method")
        data = json.loads(self.cli("sync-status").stdout)
        self.assertEqual(data["runtime"]["status"], "local-overrides")
        self.assertEqual(data["applied_revision"], self.p)

    def test_r1_migration_preserves_foreign_and_unknown_target_commands(self):
        def command(home=None, codex=None, equal=False):
            args = [sys.executable, "-B", str(self.repo / "scripts/codex_framework.py")]
            if home is not None:
                args += ["--home=" + str(home)] if equal else ["--home", str(home)]
            if codex is not None:
                args += ["--codex-home=" + str(codex)] if equal else ["--codex-home", str(codex)]
            return shlex.join(args + ["session-start"])
        current = {"type":"command", "command":command(self.a.home, self.a.codex_home, True), "timeout":4}
        foreign = [
            {"type":"command", "command":command(self.root / "another user", self.root / "another codex"), "timeout":3},
            {"type":"command", "command":command(self.a.home, self.root / "another codex")},
            {"type":"command", "command":command(self.root / "another user", self.a.codex_home)},
            {"type":"command", "command":command(None, self.a.codex_home)},
            {"type":"command", "command":command()},
            {"type":"command", "command":"echo " + command(self.a.home, self.a.codex_home)},
            {"type":"command", "command":command(self.a.home, self.a.codex_home) + " && echo unexpected"},
            {"type":"command", "command":17},
            {"type":"command", "command":"'unterminated"},
        ]
        original = {"hooks":{"SessionStart":[{"matcher":"startup", "hooks":[current] + foreign}]}}
        put(self.a.codex_home / "hooks.json", json.dumps(original))
        self.install()
        actual = json.loads((self.a.codex_home / "hooks.json").read_text())
        handlers = actual["hooks"]["SessionStart"][0]["hooks"]
        self.assertEqual(handlers[1:], foreign)
        args = shlex.split(handlers[0]["command"])
        self.assertEqual(args[:2], [sys.executable, "-B"])
        self.assertIn(str(runtime.launcher_path(self.a)), args)
        self.assertIn("--home=" + str(self.a.home), args)
        self.assertEqual(actual["hooks"]["SessionStart"][0]["matcher"], "startup")
        self.assertEqual(handlers[0]["timeout"], 4)

    def test_r1_explicit_hook_install_keeps_other_target_and_current_properties(self):
        self.install()
        own = {"type":"command", "command":runtime.stable_command(self.a), "timeout":4, "additionalContextLimit":1700}
        foreign_command = shlex.join([sys.executable, str(self.repo / "scripts/codex_framework.py"),
                                     "--home", str(self.root / "other-user"), "--codex-home", str(self.root / "other-codex"), "session-start"])
        foreign = {"type":"command", "command":foreign_command, "timeout":3}
        original = {"hooks":{"SessionStart":[{"matcher":"startup", "extra":"preserve", "hooks":[own, copy.deepcopy(own), foreign]}],
                             "Stop":[{"hooks":[{"type":"command","command":"echo stop"}]}]}}
        put(self.a.codex_home / "hooks.json", json.dumps(original))
        result = self.cli("install-hook")
        self.assertEqual(result.returncode, 0, result.stderr)
        path = self.a.codex_home / "hooks.json"
        actual = json.loads(path.read_text())
        self.assertEqual(actual["hooks"]["SessionStart"], [{"matcher":"startup", "extra":"preserve", "hooks":[own, foreign]}])
        self.assertEqual(actual["hooks"]["Stop"], original["hooks"]["Stop"])
        first = path.read_bytes()
        self.assertEqual(self.cli("install-hook").returncode, 0)
        self.assertEqual(path.read_bytes(), first)

    def test_r2_active_pointer_shape_matrix_has_structured_status_and_hook_unknown(self):
        self.install()
        pointer = runtime.pointer(self.a)
        original = pointer.read_bytes()
        valid = json.loads(original)
        shapes = [[], [1], None, 0, True, "pointer", {}]
        for field, value in (("version", True), ("version", []), ("revision", []), ("revision", 7),
                             ("revision", "../escape"), ("manifest_sha256", []), ("manifest_sha256", "bad"),
                             ("source_repo", []), ("source_repo", "relative"), ("previous_revision", [])):
            shapes.append({**valid, field:value})
        try:
            for value in shapes:
                with self.subTest(value=value):
                    pointer.write_text(json.dumps(value))
                    status = self.cli("sync-status")
                    hook = self.cli("session-start", payload={"hook_event_name":"SessionStart", "source":"startup"})
                    self.assertEqual(status.returncode, 1)
                    self.assertTrue(status.stdout, status.stderr)
                    data = json.loads(status.stdout)
                    self.assertEqual(data["status"], "runtime-unavailable")
                    self.assertIsNone(data["tool_revision"])
                    self.assertEqual(data["document_revision"], self.p)
                    self.assertNotIn("Traceback", status.stderr)
                    self.assertEqual(hook.returncode, 0, hook.stderr)
                    context = json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"]
                    self.assertIn("unknown", context)
                    self.assertLessEqual(len(context.encode()), 1800)
                    self.assertNotIn("Traceback", hook.stderr)
        finally:
            pointer.write_bytes(original)

    def test_r1_effective_target_options_and_runner_identity(self):
        source = str(self.repo / "scripts/codex_framework.py")
        stable = str(runtime.launcher_path(self.a))
        base = [sys.executable, source]
        current = ["--home", str(self.a.home), "--codex-home", str(self.a.codex_home)]
        cases = [
            (base + current + ["session-start"], True),
            (base + ["--home=" + str(self.a.home), "session-start"], True),
            ([stable, "session-start"] + current, True),
            ([sys.executable, "-B", "-u", source] + current + ["session-start"], True),
            (base + ["--home", str(self.root / "other")] + current + ["session-start"], True),
            (base + current + ["--home", str(self.root / "other"), "session-start"], False),
            (base + current + ["--repo", str(self.root / "other-repo"), "session-start"], False),
            (base + ["--codex-home", str(self.a.codex_home), "session-start"], False),
            (base + ["--home", "relative", "session-start"], False),
            (base + current + ["--unknown", "value", "session-start"], False),
            (base + current + ["session-start", "session-start"], False),
            (["/usr/bin/env", "python3", source] + current + ["session-start"], False),
            (["echo", source] + current + ["session-start"], False),
            ([sys.executable, "-c", source] + current + ["session-start"], False),
            ([sys.executable, str(self.root / "foreign/scripts/codex_framework.py")] + current + ["session-start"], False),
        ]
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.root / "unrelated-runtime")}):
            for args, matched in cases:
                with self.subTest(args=args):
                    value = runtime.hook_identity(self.a, {"type":"command", "command":shlex.join(args)})
                    self.assertEqual(value is not None, matched)

    def test_r1_foreign_only_hook_configuration_is_preserved_when_current_added(self):
        self.install()
        foreign = shlex.join([sys.executable, str(self.repo / "scripts/codex_framework.py"),
                              "--home", str(self.root / "other"), "session-start"])
        groups = [{"matcher":"resume", "hooks":[{"type":"command", "command":foreign, "timeout":2},
                                                  {"type":"command", "command":"'unterminated"}]},
                  {"matcher":"clear", "hooks":"opaque-unconfirmed"}]
        put(self.a.codex_home / "hooks.json", json.dumps({"hooks":{"SessionStart":groups}}))
        result = self.cli("install-hook")
        self.assertEqual(result.returncode, 0, result.stderr)
        actual = json.loads((self.a.codex_home / "hooks.json").read_text())["hooks"]["SessionStart"]
        self.assertEqual(actual[:2], groups)
        self.assertEqual(len(actual), 3)
        before = (self.a.codex_home / "hooks.json").read_bytes()
        self.assertEqual(self.cli("install-hook").returncode, 0)
        self.assertEqual((self.a.codex_home / "hooks.json").read_bytes(), before)

    def test_r2_bad_pointer_diagnostic_reads_each_identity_source_defensively(self):
        self.install()
        pointer, state = runtime.pointer(self.a), self.a.state_path
        original_pointer, original_state = pointer.read_bytes(), state.read_bytes()
        shapes = [[], None, 7, {"version":1,"sync":[]}, {"version":1,"sync":None},
                  {"version":1,"sync":{"applied_revision":[]}},
                  {"version":1,"sync":{"applied_revision":self.p}}]
        try:
            pointer.write_text("[]")
            for value in shapes:
                with self.subTest(value=value):
                    state.write_text(json.dumps(value))
                    result = self.cli("sync-status")
                    self.assertEqual(result.returncode, 1)
                    data = json.loads(result.stdout)
                    self.assertEqual(data["status"], "runtime-unavailable")
                    expected = self.p if value == shapes[-1] else None
                    self.assertEqual(data["document_revision"], expected)
                    self.assertNotIn("Traceback", result.stderr)
                    hook = self.cli("session-start", payload={"hook_event_name":"SessionStart","source":"resume"})
                    self.assertEqual(hook.returncode, 0)
                    self.assertIn("unknown", json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"])
        finally:
            pointer.write_bytes(original_pointer)
            state.write_bytes(original_state)

    def test_r2_pending_recovery_state_shapes_are_rejected_without_import_or_restore(self):
        def interrupt(name):
            if name == "launcher":
                raise RuntimeError("isolated first migration interruption")
        with mock.patch.object(runtime, "checkpoint", side_effect=interrupt):
            with self.assertRaises(RuntimeError):
                self.install()
        path = runtime.journal(self.a)
        original = path.read_bytes()
        good = json.loads(original)
        shapes = [[], None, 7, {}, {**good,"candidate":[]}, {**good,"previous":[]},
                  {**good,"changes":[]}, {**good,"changes":{"state":{"before":False,"after":{"data":None}}}}]
        try:
            for value in shapes:
                with self.subTest(value=value):
                    path.write_text(json.dumps(value))
                    before = self.a.state_path.read_bytes()
                    result = self.cli("sync-status")
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(json.loads(result.stdout)["status"], "runtime-unavailable")
                    self.assertNotIn("Traceback", result.stderr)
                    hook = self.cli("session-start", payload={"hook_event_name":"SessionStart","source":"clear"})
                    self.assertEqual(hook.returncode, 0)
                    self.assertIn("unknown", json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"])
                    with self.assertRaises(cf.SyncError):
                        runtime.recover(self.a)
                    self.assertEqual(self.a.state_path.read_bytes(), before)
        finally:
            path.write_bytes(original)
        self.assertEqual(self.cli("runtime-recover").returncode, 0)

    def test_r2_manifest_object_and_field_shape_matrix_never_loads_invalid_package(self):
        self.install()
        pointer = runtime.pointer(self.a)
        original_pointer = pointer.read_bytes()
        path = runtime.version_path(self.a, self.p) / "manifest.json"
        original_manifest = path.read_bytes()
        good = json.loads(original_manifest)
        shapes = [[], None, "manifest", {**good,"version":True}, {**good,"input_contract":[]},
                  {**good,"revision":[]}, {**good,"files":[]}, {**good,"files":"files"}]
        for entry in (None, [], {"sha256":[],"mode":"100644"}, {"sha256":"0"*64,"mode":[]}):
            value = copy.deepcopy(good)
            value["files"]["scripts/source_evidence.py"] = entry
            shapes.append(value)
        try:
            for value in shapes:
                with self.subTest(value=value):
                    path.write_text(json.dumps(value))
                    active = json.loads(original_pointer)
                    active["manifest_sha256"] = runtime.digest(path.read_bytes())
                    pointer.write_text(json.dumps(active))
                    result = self.cli("sync-status")
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(json.loads(result.stdout)["document_revision"], self.p)
                    self.assertNotIn("Traceback", result.stderr)
                    hook = self.cli("session-start", payload={"hook_event_name":"SessionStart","source":"startup"})
                    self.assertEqual(hook.returncode, 0)
                    self.assertIn("unknown", json.loads(hook.stdout)["hookSpecificOutput"]["additionalContext"])
                    with self.assertRaises(cf.SyncError):
                        runtime.validate(path.parent)
        finally:
            path.write_bytes(original_manifest)
            pointer.write_bytes(original_pointer)
