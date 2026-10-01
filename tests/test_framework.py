import contextlib
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import codex_framework as cf


def put(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)


def run_git(repo, *args):
    result = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True,
                            env=dict(os.environ, GIT_TERMINAL_PROMPT="0"), timeout=20)
    if result.returncode:
        raise AssertionError(result.stderr)
    return result.stdout.strip()


class FixtureCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="framework-test-")
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        shutil.copytree(ROOT, self.repo, ignore=shutil.ignore_patterns(".git", "__pycache__"))
        self.a = cf.Framework(self.repo, self.root / "device-a")
        self.b = cf.Framework(self.repo, self.root / "device-b")

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, framework=None):
        results = (framework or self.a).reconcile(apply=True)
        self.assertNotIn("conflict", [action for _, action in results])
        return results


class SyncTests(FixtureCase):
    def test_two_devices_install_preserves_private_files_and_is_idempotent(self):
        private = {
            ".codex/config.toml": 'model = "device-model"\n[features]\nmemories = false\n',
            ".codex/auth.json": '{"token":"private-device-token"}',
            ".codex/memories/MEMORY.md": "private historical facts",
            ".codex/memories_v2/memory_summary.md": "independent memories",
            ".codex/state_5.sqlite": "opaque local state",
            ".codex/workstation/local.md": "this device's resources",
            ".agents/skills/unrelated/SKILL.md": "user-owned skill",
        }
        for device in (self.a, self.b):
            for name, text in private.items():
                put(device.home / name, text)
            self.apply(device)
            times = {name: device.target(name).stat().st_mtime_ns for name in device.collect()}
            state_time = device.state_path.stat().st_mtime_ns
            self.assertTrue(all(a == "same" for _, a in self.apply(device)))
            self.assertEqual(times, {name: device.target(name).stat().st_mtime_ns for name in device.collect()})
            self.assertEqual(state_time, device.state_path.stat().st_mtime_ns)
            for name, text in private.items():
                self.assertEqual((device.home / name).read_text(), text)
            self.assertEqual(device.target(".codex/AGENTS.md").read_bytes(),
                             (self.repo / ".codex/AGENTS.md").read_bytes())
        self.assertEqual(self.a.collect(local=True), self.b.collect(local=True))

    def test_status_does_not_create_state_or_install_files(self):
        results = self.a.reconcile()
        self.assertTrue(all(a == "update" for _, a in results))
        self.assertFalse(self.a.home.exists())

    def test_first_install_keeps_existing_different_entry(self):
        path = self.a.target(".codex/AGENTS.md")
        put(path, "existing personal rule")
        results = dict(self.a.reconcile(apply=True))
        self.assertEqual(results[".codex/AGENTS.md"], "conflict")
        self.assertEqual(path.read_text(), "existing personal rule")
        self.assertTrue(self.a.target(".codex/memory-framework.md").exists())
        path.write_bytes((self.repo / ".codex/AGENTS.md").read_bytes())
        self.apply()

    def test_conflict_keeps_both_versions_and_applies_independent_update(self):
        self.apply()
        local_rule = self.a.target(".codex/AGENTS.md")
        put(local_rule, "local edit")
        put(self.repo / ".codex/AGENTS.md", "upstream edit")
        put(self.repo / ".codex/memory-framework.md", "upstream method")
        results = dict(self.a.reconcile(apply=True))
        self.assertEqual(results[".codex/AGENTS.md"], "conflict")
        self.assertEqual(local_rule.read_text(), "local edit")
        self.assertEqual((self.repo / ".codex/AGENTS.md").read_text(), "upstream edit")
        self.assertEqual(self.a.target(".codex/memory-framework.md").read_text(), "upstream method")
        put(local_rule, "upstream edit")
        self.assertTrue(all(a == "same" for _, a in self.apply()))

    def test_local_only_changes_survive_repeated_apply(self):
        self.apply()
        local = self.a.target(".codex/AGENTS.md")
        put(local, "device-specific edit")
        for _ in range(2):
            self.assertEqual(dict(self.apply())[".codex/AGENTS.md"], "keep-local")
            self.assertEqual(local.read_text(), "device-specific edit")

    def test_export_only_framework_and_does_not_advance_published_baseline(self):
        self.apply()
        state = self.a.state_path.read_bytes()
        previous = (self.repo / ".codex/AGENTS.md").read_bytes()
        put(self.a.target(".codex/AGENTS.md"), "review me")
        new_ref = ".agents/skills/york-task-alignment/references/new-method.md"
        put(self.a.target(new_ref), "new reusable method")
        put(self.a.home / ".codex/memories/private.md", "private history")
        put(self.a.home / ".codex/config.toml", 'token = "do-not-export"')
        put(self.a.home / ".agents/skills/unrelated/SKILL.md", "unrelated")
        self.a.reconcile(apply=True, export=True)
        self.assertEqual((self.repo / ".codex/AGENTS.md").read_text(), "review me")
        self.assertEqual((self.repo / new_ref).read_text(), "new reusable method")
        self.assertFalse((self.repo / ".codex/memories").exists())
        self.assertFalse((self.repo / ".codex/config.toml").exists())
        self.assertFalse((self.repo / ".agents/skills/unrelated").exists())
        self.assertEqual(self.a.state_path.read_bytes(), state)
        (self.repo / ".codex/AGENTS.md").write_bytes(previous)
        self.assertEqual(dict(self.apply())[".codex/AGENTS.md"], "keep-local")

    def test_export_conflict_retains_repository_edit(self):
        self.apply()
        put(self.a.target(".codex/AGENTS.md"), "local")
        put(self.repo / ".codex/AGENTS.md", "repository")
        results = dict(self.a.reconcile(apply=True, export=True))
        self.assertEqual(results[".codex/AGENTS.md"], "conflict")
        self.assertEqual((self.repo / ".codex/AGENTS.md").read_text(), "repository")

    def test_upstream_deletion_respects_local_changes(self):
        name = ".agents/skills/york-task-alignment/references/optional.md"
        put(self.repo / name, "original")
        self.apply(self.a)
        self.apply(self.b)
        put(self.b.target(name), "device b edit")
        (self.repo / name).unlink()
        self.apply(self.a)
        self.assertFalse(self.a.target(name).exists())
        self.assertEqual(dict(self.b.reconcile(apply=True))[name], "conflict")
        self.assertEqual(self.b.target(name).read_text(), "device b edit")

    def test_export_cannot_delete_required_entry(self):
        self.apply()
        source = (self.repo / ".codex/AGENTS.md").read_bytes()
        self.a.target(".codex/AGENTS.md").unlink()
        with self.assertRaises(cf.SyncError):
            self.a.reconcile(apply=True, export=True)
        self.assertEqual((self.repo / ".codex/AGENTS.md").read_bytes(), source)

    def test_custom_codex_home_and_explicit_test_home(self):
        special = cf.Framework(self.repo, self.a.home, self.root / "custom-codex")
        self.apply(special)
        self.assertTrue((self.root / "custom-codex/AGENTS.md").exists())
        self.assertFalse((self.a.home / ".codex/AGENTS.md").exists())
        self.assertTrue((self.a.home / ".agents/skills/york-java-test/SKILL.md").exists())
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(self.root / "never-touch")}):
            with contextlib.redirect_stdout(io.StringIO()):
                code = cf.main(["--repo", str(self.repo), "--home", str(self.b.home), "install"])
        self.assertEqual(code, 0)
        self.assertFalse((self.root / "never-touch").exists())

    def test_cloud_install_forwards_existing_options(self):
        # Capture the shell boundary without risking a real install if options are dropped.
        python = self.root / "bin/python3"
        put(python, "#!" + sys.executable + "\nimport json, sys\nprint(json.dumps(sys.argv[1:]))\n")
        python.chmod(0o755)
        options = ["--home", str(self.root / "cloud user"),
                   "--codex-home", str(self.root / "installed codex")]
        result = subprocess.run(
            ["sh", str(self.repo / "scripts/cloud-install.sh")] + options,
            cwd=self.root, capture_output=True, text=True, timeout=20,
            env=dict(os.environ, PATH=str(python.parent) + os.pathsep + os.environ.get("PATH", "")),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout),
                         [str(self.repo / "scripts/codex_framework.py"), "install"] + options)

    def test_explicit_cloud_target_preserves_runtime_home_and_updates_on_apply(self):
        runtime = self.root / "platform-runtime"
        installed = self.root / "installed codex"
        put(runtime / "AGENTS.md", "platform instructions")
        put(runtime / "auth.json", "platform-owned authentication")
        target = cf.Framework(self.repo, self.b.home, installed)
        arguments = ["--repo", str(self.repo), "--home", str(self.b.home),
                     "--codex-home", str(installed)]
        with mock.patch.dict(os.environ, {"CODEX_HOME": str(runtime)}):
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cf.main(arguments + ["install"]), 0)
                self.assertEqual(target.collect(local=True), target.collect())
                before = target.target(".codex/AGENTS.md").read_bytes()
                put(self.repo / ".codex/AGENTS.md", "updated shared instructions")
                self.assertEqual(cf.main(arguments + ["status"]), 0)
                self.assertEqual(target.target(".codex/AGENTS.md").read_bytes(), before)
                self.assertEqual(cf.main(arguments + ["apply"]), 0)
                self.assertEqual(target.collect(local=True), target.collect())
            self.assertEqual(os.environ["CODEX_HOME"], str(runtime))
        self.assertFalse((self.b.home / ".codex").exists())
        self.assertEqual((runtime / "AGENTS.md").read_text(), "platform instructions")
        self.assertEqual((runtime / "auth.json").read_text(), "platform-owned authentication")
        self.assertEqual(sorted(p.name for p in runtime.iterdir()), ["AGENTS.md", "auth.json"])

    def test_symlink_destination_does_not_modify_external_file(self):
        external = self.root / "external.md"
        put(external, "do not change")
        target = self.a.target(".codex/AGENTS.md")
        target.parent.mkdir(parents=True)
        target.symlink_to(external)
        with self.assertRaises(cf.SyncError):
            self.a.reconcile(apply=True)
        self.assertEqual(external.read_text(), "do not change")

    def test_source_symlink_is_rejected_before_install(self):
        path = self.repo / ".agents/skills/york-java-test/references/injected.md"
        path.symlink_to(ROOT / "README.md")
        with self.assertRaises(cf.SyncError):
            self.a.reconcile(apply=True)
        self.assertFalse(self.a.target(".codex/AGENTS.md").exists())

    def test_state_cannot_expand_scope(self):
        put(self.a.state_path, json.dumps({"version": 1, "source": cf.REMOTE,
                                         "base": {".codex/auth.json": None}}))
        with self.assertRaises(cf.SyncError):
            self.a.reconcile(apply=True)

    def test_launcher_executes_from_another_working_directory(self):
        self.apply()
        launcher = self.a.launcher()
        self.assertEqual(self.a.launcher(), launcher)
        result = subprocess.run([sys.executable, str(launcher), "--home", str(self.a.home), "status"],
                                cwd=self.root, capture_output=True, text=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("conflict=0", result.stdout)

    def test_empty_or_malformed_source_is_not_applied(self):
        (self.repo / ".agents/skills/york-java-test/SKILL.md").unlink()
        with self.assertRaises(cf.SyncError):
            self.a.reconcile(apply=True)
        self.assertFalse(self.a.target(".codex/AGENTS.md").exists())


class GitFixtureCase(FixtureCase):
    # Use a real local Git remote, including commits and pulls, without network credentials.
    def setUp(self):
        super().setUp()
        run_git(self.repo, "init", "-b", cf.BRANCH)
        run_git(self.repo, "config", "user.email", "fixture@example.invalid")
        run_git(self.repo, "config", "user.name", "Fixture")
        run_git(self.repo, "add", ".")
        run_git(self.repo, "commit", "-m", "fixture initial")
        self.remote = self.root / "remote.git"
        run_git(self.root, "clone", "--bare", str(self.repo), str(self.remote))
        self.receiver = self.root / "receiver"
        run_git(self.root, "clone", str(self.remote), str(self.receiver))
        run_git(self.receiver, "remote", "set-url", "origin", cf.REMOTE)
        run_git(self.receiver, "config", "url." + str(self.remote) + ".insteadOf", cf.REMOTE)
        self.client = cf.Framework(self.receiver, self.root / "receiver-home")
        self.apply(self.client)

    def publish_fixture(self, name, text):
        put(self.repo / name, text)
        run_git(self.repo, "add", name)
        run_git(self.repo, "commit", "-m", "fixture update")
        run_git(self.repo, "push", str(self.remote), cf.BRANCH)


class GitSyncTests(GitFixtureCase):
    def test_pull_round_trip_with_unrelated_checkout_edit(self):
        put(self.receiver / "README.md", "unrelated checkout edit")
        self.publish_fixture(".codex/AGENTS.md", "published revision")
        self.client.pull()
        self.apply(self.client)
        self.assertEqual(self.client.target(".codex/AGENTS.md").read_text(), "published revision")
        self.assertEqual((self.receiver / "README.md").read_text(), "unrelated checkout edit")

    def test_offline_pull_leaves_installed_framework_unchanged(self):
        before = self.client.collect(local=True)
        self.remote.rename(self.root / "offline.git")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = cf.main(["--repo", str(self.receiver), "--home", str(self.client.home), "pull", "--apply"])
        self.assertEqual(code, 1)
        self.assertEqual(before, self.client.collect(local=True))

    def test_wrong_remote_or_branch_is_not_pulled(self):
        run_git(self.receiver, "remote", "set-url", "origin", "https://example.invalid/other.git")
        with self.assertRaises(cf.SyncError):
            self.client.pull()
        run_git(self.receiver, "remote", "set-url", "origin", cf.REMOTE)
        run_git(self.receiver, "switch", "-c", "other-task")
        with self.assertRaises(cf.SyncError):
            self.client.pull()

    def test_overlapping_uncommitted_edit_is_preserved(self):
        put(self.receiver / ".codex/AGENTS.md", "review checkout edit")
        before = self.client.collect(local=True)
        self.publish_fixture(".codex/AGENTS.md", "published revision")
        with self.assertRaises(cf.SyncError):
            self.client.pull()
        self.assertEqual((self.receiver / ".codex/AGENTS.md").read_text(), "review checkout edit")
        self.assertEqual(before, self.client.collect(local=True))


class PackageTests(unittest.TestCase):
    def test_nine_skill_names_match_directories(self):
        names = sorted(p.name for p in (ROOT / ".agents/skills").iterdir())
        self.assertEqual(names, sorted(cf.SKILLS))
        for name in names:
            text = (ROOT / ".agents/skills" / name / "SKILL.md").read_text()
            self.assertTrue(text.startswith("---\n"))
            self.assertIn("\nname: " + name + "\n", text)
            self.assertRegex(text, r"\ndescription: .+\n")

    def test_relative_document_links_resolve(self):
        missing = []
        for path in ROOT.rglob("*.md"):
            for link in re.findall(r"\]\(([^)]+)\)", path.read_text()):
                if "://" in link or link.startswith(("#", "$")):
                    continue
                destination = link.split("#", 1)[0]
                if destination and not (path.parent / destination).exists():
                    missing.append(str(path.relative_to(ROOT)) + " -> " + link)
        self.assertEqual(missing, [])

    def test_no_fixed_user_paths_or_disallowed_payload(self):
        for root in [ROOT / ".codex", ROOT / ".agents"]:
            for path in root.rglob("*"):
                if path.is_file():
                    relative = path.relative_to(ROOT).as_posix()
                    self.assertTrue(cf.allowed(relative), relative)
                    self.assertNotIn("/Users/york", path.read_text(), relative)
                    self.assertNotIn("/Applications/工作", path.read_text(), relative)
        self.assertFalse((ROOT / ".codex/config.toml").exists())
        self.assertFalse((ROOT / ".codex/memories").exists())


if __name__ == "__main__":
    unittest.main()
