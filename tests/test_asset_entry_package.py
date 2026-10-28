"""Ordinary producer -> installed, version-bound reader; only temporary homes."""
import hashlib
import json
import os
import subprocess
import sys

from test_framework import FixtureCase, seed_runtime
import framework_runtime as runtime


class AssetEntryPackageTests(FixtureCase):
    def test_installed_reader_consumes_optional_producer_without_claiming_health(self):
        revision = seed_runtime(self.repo)
        release = runtime.declared_release_version(
            (self.repo / "scripts/codex_framework.py").read_text())
        env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1")

        def cli(path, *args):
            return subprocess.run(
                [sys.executable, "-B", str(path), "--home", str(self.a.home),
                 "--codex-home", str(self.a.codex_home), *args],
                capture_output=True, text=True, env=env, timeout=30)

        installed = cli(self.repo / "scripts/codex_framework.py", "--repo", str(self.repo),
                        "install", "--revision", revision, "--release-version", release,
                        "--expected-runtime", "none")
        self.assertEqual(installed.returncode, 0, installed.stdout + installed.stderr)
        directory = runtime.version_path(self.a, revision)
        manifest = json.loads((directory / "manifest.json").read_text())
        self.assertEqual(len(runtime.MODULES), 8)
        self.assertEqual(set(manifest["files"]),
                         {"scripts/" + x for x in runtime.MODULES} | {"snapshot.json"})
        self.assertFalse((directory / "adapters").exists())
        self.assertEqual((directory / "scripts/workstation_entry.py").read_bytes(),
                         (self.repo / "scripts/workstation_entry.py").read_bytes())

        work = self.a.codex_home / "workstation"
        work.mkdir(parents=True, exist_ok=True)
        identity = {"project_id": "file-metadata-package-test", "task_ref": "synthetic:package",
                    "node_id": "isolated-node", "version": release}
        stages = {x: {"status": "UNKNOWN", "value": None} for x in
                  ("candidate", "installed", "loaded", "selection", "reserved", "effect", "health", "retirement")}
        stages["installed"] = {"op": "field", "source": "active", "field": "revision",
                               "status": "METADATA_OBSERVED"}
        selection = {"schema": "node-asset-selection-v1", "identity": identity,
                     "sources": {"active": {"kind": "json", "path": str(runtime.pointer(self.a)),
                                            "allowed_root": str(self.a.codex_home)}},
                     "assets": [{"id": "framework", "version_label": release,
                                 "owner_ref": "synthetic-owner", "stages": stages}]}
        selection_path = work / "selection.json"
        selection_path.write_text(json.dumps(selection))
        facts = work / "facts.json"
        produced = subprocess.run(
            [sys.executable, "-B", str(self.repo / "adapters/file-metadata/asset_facts.py"),
             "--selection", str(selection_path), "--output", str(facts)],
            capture_output=True, text=True, env=env, timeout=10)
        self.assertEqual(produced.returncode, 0, produced.stdout + produced.stderr)
        record = json.loads(facts.read_text())
        self.assertEqual(record["producer_version"], "0.1.0")
        (work / "observation-sources.json").write_text(json.dumps({
            "schema": "workstation-entry-v1", "node_id": identity["node_id"], "sources": [{
                "id": "assets", "kind": "asset-facts-v1", "path": str(facts), "identity": identity,
                "status_path": "status", "time_path": "observed_at", "max_age_seconds": 900,
                "ok_values": [], "failure_values": ["OBSERVATION_FAILED"]}]}))
        facts_hash = hashlib.sha256(facts.read_bytes()).hexdigest()
        readback = cli(runtime.launcher_path(self.a), "workstation-entry")
        self.assertEqual(readback.returncode, 2, readback.stdout + readback.stderr)
        observed = json.loads(readback.stdout)
        self.assertEqual(observed["status"], "unknown")
        asset = observed["asset_observations"][0]
        self.assertEqual(asset["metadata_status"], "current")
        self.assertEqual(asset["assets"][0]["stages"]["installed"]["value"], revision)
        self.assertFalse(asset["runtime_health_assessed"])
        self.assertEqual(hashlib.sha256(facts.read_bytes()).hexdigest(), facts_hash)
        version = cli(runtime.launcher_path(self.a), "version")
        self.assertEqual(version.returncode, 0, version.stdout + version.stderr)
        status = json.loads(version.stdout)
        self.assertEqual(status["source_version"], release)
        for key in ("tool_version", "active_version", "document_version"):
            self.assertEqual(status["runtime"][key], release)


if __name__ == "__main__":
    import unittest
    unittest.main()
