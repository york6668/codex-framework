"""Author regression expectations for P04 C01/C15/C16. Fixtures are temporary."""
import copy
import importlib.util
import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


def load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


entry = load("p04_entry", ROOT / "scripts/workstation_entry.py")
producer = load("p04_producer", ROOT / "adapters/file-metadata/asset_facts.py")
IDENTITY = {"project_id": "workstation-asset-observation", "task_ref": "codex:thread:test-original",
            "node_id": "test-node", "version": "candidate-test"}
STAGES = ("candidate", "installed", "loaded", "selection", "reserved", "effect", "health", "retirement")


def row(name="example"):
    stages = {k: {"status": "UNKNOWN", "value": None, "refs": []} for k in STAGES}
    stages["installed"] = {"status": "METADATA_OBSERVED", "value": "current-new", "refs": ["release"]}
    stages["reserved"] = {"status": "ROLLBACK_RETAINED", "value": "previous-old", "refs": ["release"]}
    stages["loaded"] = {"status": "SAVED_HOST_ONLY", "value": "NOT_SERVICE",
                        "source_time": "2026-10-01T01:02:03+00:00", "refs": ["release"]}
    return {"id": name, "version_label": "current-new_prev-old", "owner_ref": "original-owner",
            "stages": stages, "diagnostic_refs": ["original-entry:NOT_RUN"]}


class EntryCases(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.home = Path(self.tmp.name).resolve()
        self.work = self.home / "workstation"
        self.work.mkdir()
        self.path = self.work / "facts.json"
        self.record = {"schema": "node-asset-facts-v1", **IDENTITY,
                       "status": "RUNTIME_UNVERIFIED", "observed_at": datetime.now(timezone.utc).isoformat(),
                       "source_refs": [{"id": "release", "path": str(self.work / "release.json"), "sha256": "a" * 64}],
                       "assets": [row()]}
        self.spec = {"id": "assets", "kind": "asset-facts-v1", "path": str(self.path),
                     "identity": IDENTITY, "status_path": "status", "time_path": "observed_at",
                     "max_age_seconds": 900, "ok_values": [], "failure_values": ["OBSERVATION_FAILED"]}
        self.manifest = {"schema": "workstation-entry-v1", "node_id": "test-node", "sources": [self.spec]}

    def tearDown(self):
        self.tmp.cleanup()

    def run_entry(self):
        self.path.write_text(json.dumps(self.record))
        (self.work / "observation-sources.json").write_text(json.dumps(self.manifest))
        return entry.evaluate(self.home)

    def test_current_metadata_not_health_and_saved_time_not_refreshed(self):
        result = self.run_entry()
        self.assertEqual(result["status"], "unknown")
        meta = result["asset_observations"][0]
        self.assertEqual(meta["metadata_status"], "current")
        self.assertFalse(meta["runtime_health_assessed"])
        stages = meta["assets"][0]["stages"]
        self.assertEqual(stages["reserved"]["value"], "previous-old")
        self.assertEqual(stages["loaded"]["source_time"], "2026-10-01T01:02:03+00:00")
        self.assertEqual(stages["health"]["status"], "UNKNOWN")

    def test_expired_metadata_not_current(self):
        self.record["observed_at"] = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        result = self.run_entry()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["asset_observations"][0]["assets"], [])

    def test_wrong_node_task_and_version_do_not_project(self):
        for key in ["node_id", "task_ref", "version"]:
            original = self.record[key]
            self.record[key] = "wrong"
            self.assertEqual(self.run_entry()["asset_observations"][0]["assets"], [])
            self.record[key] = original

    def test_missing_source_is_unknown(self):
        self.run_entry()
        self.path.unlink()
        self.assertEqual(entry.evaluate(self.home)["status"], "unknown")

    def test_observer_failure_does_not_reuse_healthy_payload(self):
        self.record["status"] = "OBSERVATION_FAILED"
        result = self.run_entry()
        self.assertEqual(result["status"], "abnormal")
        self.assertEqual(result["asset_observations"][0]["assets"], [])

    def test_metadata_health_injection_is_rejected_and_other_asset_retained(self):
        self.record["assets"].append(row("core"))
        self.record["assets"][0]["stages"]["health"]["status"] = "HEALTHY"
        result = self.run_entry()
        self.assertEqual(result["status"], "unknown")
        meta = result["asset_observations"][0]
        self.assertEqual(meta["metadata_status"], "partial")
        self.assertEqual([x["id"] for x in meta["assets"]], ["core"])

    def test_duplicate_asset_identity_is_not_arbitrarily_first_wins(self):
        self.record["assets"] += [row(), row("core")]
        meta = self.run_entry()["asset_observations"][0]
        self.assertEqual([x["id"] for x in meta["assets"]], ["core"])

    def test_an_independent_active_failure_is_not_hidden_by_asset_summary(self):
        failed = self.work / "active.json"
        failed.write_text(json.dumps({"status": "FAIL_ACTIVE"}))
        self.manifest["sources"].append({"id": "independent-active", "path": str(failed),
            "status_path": "status", "failure_values": ["FAIL_ACTIVE"]})
        result = self.run_entry()
        self.assertEqual(result["status"], "abnormal")
        self.assertIn("independent-active=FAIL_ACTIVE", result["summary"])
        self.assertEqual(len(result["asset_observations"][0]["assets"]), 1)

    def test_unselected_fields_and_invalid_reference_metadata_are_not_projected(self):
        self.record["secret"] = "NEVER_PROJECT_THIS"
        self.record["assets"][0]["secret"] = "NEVER_PROJECT_THIS"
        result = self.run_entry()
        self.assertNotIn("NEVER_PROJECT_THIS", json.dumps(result))
        self.record["source_refs"][0]["mtime_ns"] = {"unexpected": "NEVER_PROJECT_THIS"}
        result = self.run_entry()
        self.assertEqual(result["asset_observations"][0]["assets"], [])
        self.assertNotIn("NEVER_PROJECT_THIS", json.dumps(result))

    def test_closed_historical_failure_preserved_not_active_gate(self):
        historic = self.work / "historic.json"
        historic.write_text(json.dumps({"status": "STOPPED_ON_FAILURE", "observed_at": "2026-01-01T00:00:00Z"}))
        self.manifest["historical_sources"] = [{"id": "closed-retest", "path": str(historic),
            "status_path": "status", "failure_values": ["STOPPED_ON_FAILURE"],
            "scope_status": "COMPLETED_FINITE_SCOPE", "scope_ref": "original-user-closed-scope"}]
        result = self.run_entry()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["historical_observations"][0]["classification_in_original_source"], "abnormal")
        self.assertFalse(result["historical_observations"][0]["scope_annotation_authenticated"])

    def test_invalid_history_config_is_local_and_keeps_valid_assets(self):
        self.manifest["historical_sources"] = [{"id": "bad"}]
        result = self.run_entry()
        self.assertEqual(result["status"], "unknown")
        self.assertEqual(result["asset_observations"][0]["metadata_status"], "current")
        self.assertIn("historical_errors", result)
        self.assertIn("历史来源配置无效", result["summary"])

    def add_active_failure(self, name="active-failure"):
        failed = self.work / "active-failure.json"
        failed.write_text(json.dumps({**IDENTITY, "status": "FAIL_ACTIVE"}))
        self.manifest["sources"].append({"id": name, "path": str(failed), "identity": IDENTITY,
                                        "status_path": "status", "failure_values": ["FAIL_ACTIVE"]})

    def test_declared_asset_failures_and_original_labels_survive_projection(self):
        for value in ("FAIL_ASSET", "ERROR_CHANNEL", "OBSERVATION_FAILED"):
            with self.subTest(status=value):
                self.record["status"] = value
                self.spec["failure_values"] = [value]
                result = self.run_entry()
                self.assertEqual(result["status"], "abnormal")
                self.assertIn(value, result["summary"])
                self.assertEqual(result["active_observations"][0]["classification"], "abnormal")
                self.assertEqual(result["asset_observations"][0]["source_failure"], value)

    def test_declared_failure_survives_invalid_projection_and_unbound_identity(self):
        self.record["status"] = "FAIL_ASSET"
        self.spec["failure_values"] = ["FAIL_ASSET"]
        self.record["source_refs"][0]["sha256"] = 42
        self.record["task_ref"] = "wrong"
        result = self.run_entry()
        self.assertEqual(result["status"], "abnormal")
        self.assertIn("FAIL_ASSET(归属未核)", result["summary"])
        self.assertEqual(result["asset_observations"][0]["assets"], [])
        self.record["task_ref"] = IDENTITY["task_ref"]
        result = self.run_entry()
        self.assertEqual(result["status"], "abnormal")
        self.assertIn("FAIL_ASSET", result["summary"])
        self.assertEqual(result["asset_observations"][0]["reason"], "invalid_asset_projection")

    def test_optional_history_errors_keep_active_failure_and_assets(self):
        self.add_active_failure()
        self.manifest["historical_sources"] = [{"id": "missing-proof"}]
        result = self.run_entry()
        self.assertEqual(result["status"], "abnormal")
        self.assertIn("FAIL_ACTIVE", result["summary"])
        self.assertIn("历史来源配置无效", result["summary"])
        self.assertEqual(len(result["asset_observations"][0]["assets"]), 1)
        self.assertFalse(result["historical_errors"][0]["scope_annotation_authenticated"])

    def test_invalid_optional_history_shapes_do_not_authenticate_completion(self):
        for value in (None, {}, [None], [{"id": "a"}, {"id": "b"}],
                      [{"id": "a", "scope_status": "UNKNOWN", "scope_ref": "unverified"}]):
            with self.subTest(history=value):
                self.manifest["historical_sources"] = value
                result = self.run_entry()
                self.assertEqual(result["status"], "unknown")
                self.assertEqual(len(result["asset_observations"][0]["assets"]), 1)
                self.assertNotIn("historical_observations", result)
                self.assertTrue(result["historical_errors"])
                self.assertTrue(all(x["scope_annotation_authenticated"] is False
                                    for x in result["historical_errors"]))

    def test_same_id_different_sources_use_exact_rows_in_both_orders(self):
        self.add_active_failure("assets")
        for reverse in (False, True):
            with self.subTest(reverse=reverse):
                if reverse:
                    self.manifest["sources"].reverse()
                result = self.run_entry()
                self.assertEqual(result["status"], "abnormal")
                self.assertIn("assets=FAIL_ACTIVE", result["summary"])
                self.assertEqual([x["source_index"] for x in result["active_observations"]], [0, 1])
                self.assertEqual([x["classification"] for x in result["active_observations"]],
                                 ["abnormal", "unknown"] if reverse else ["unknown", "abnormal"])
                self.assertEqual(result["asset_observations"][0]["source_index"], 1 if reverse else 0)

    def test_same_id_unreadable_other_source_is_not_suppressed(self):
        self.manifest["sources"].append({"id": "assets", "path": str(self.work / "missing.json")})
        result = self.run_entry()
        self.assertEqual(result["status"], "unknown")
        self.assertIn("不可读", result["summary"])
        self.assertEqual(len(result["asset_observations"][0]["assets"]), 1)
        self.assertEqual(result["active_observations"][1]["source_index"], 1)

    def test_active_failure_precedes_other_unknowns_in_bounded_summary(self):
        for name in ("missing1", "missing2"):
            self.manifest["sources"].append({"id": name, "path": str(self.work / name)})
        self.add_active_failure()
        result = self.run_entry()
        self.assertEqual(result["status"], "abnormal")
        self.assertIn("FAIL_ACTIVE", result["summary"])
        self.assertEqual(result["active_observations"][3]["classification"], "abnormal")
        self.assertLessEqual(len(result["summary"].encode()), 420)

    def test_ordinary_v1_fresh_and_old_failure_controls_unchanged(self):
        self.spec.pop("kind")
        self.spec["ok_values"] = ["HEALTHY"]
        for state, expected in [("HEALTHY", "normal"), ("STOPPED_ON_FAILURE", "abnormal"),
                                ("UNSEEN_STATE", "unknown")]:
            self.record["status"] = state
            self.spec["failure_values"] = ["STOPPED_ON_FAILURE"]
            result = self.run_entry()
            self.assertEqual(result["status"], expected)
            self.assertEqual(result["schema"], "workstation-entry-result-v1")
            self.assertEqual(result["read_attempts"], 2)
            self.assertEqual(result["files_read"], 2)
            self.assertIn("assets=" + state, result["summary"])
            self.assertNotIn("asset_observations", result)
            self.assertNotIn("active_observations", result)


class ProducerCases(unittest.TestCase):
    def test_new_release_does_not_inherit_old_finite_host_load(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for name, body in {"release": {"commit": "new68f"}, "load": {"commit": "old6a52"}}.items():
                (root / (name + ".json")).write_text(json.dumps(body))
            sources = {name: {"path": str(root / (name + ".json")), "kind": "json", "allowed_root": tmp}
                       for name in ("release", "load")}
            a = row()
            a["version_label"] = "new68f"
            a["stages"] = {k: {"status": "UNKNOWN", "value": None} for k in STAGES}
            a["stages"]["installed"] = {"op": "field", "source": "release", "field": "commit", "status": "METADATA_OBSERVED"}
            a["stages"]["loaded"] = {"op": "bindings", "checks": [["release", "commit", "load", "commit"]],
                                       "status": "SAVED_HOST_ONLY", "value": "MUST_NOT_INHERIT"}
            result = producer.collect({"schema": "node-asset-selection-v1", "identity": IDENTITY,
                                       "sources": sources, "assets": [a]})
            actual = result["assets"][0]
            self.assertEqual(actual["version_label"], "new68f")
            self.assertEqual(actual["stages"]["installed"]["value"], "new68f")
            self.assertEqual(actual["stages"]["loaded"]["status"], "UNKNOWN")
            self.assertFalse(result["runtime_health_assessed"])

    def test_missing_one_asset_source_does_not_hide_other_asset(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "valid.json"
            path.write_text('{"commit":"new"}')
            sources = {"good": {"path": str(path), "kind": "json", "allowed_root": tmp},
                       "bad": {"path": str(Path(tmp)/'absent.json'), "kind": "json", "allowed_root": tmp}}
            assets = []
            for name in ["good", "bad"]:
                a = row(name)
                a["stages"] = {k: {"status": "UNKNOWN", "value": None} for k in STAGES}
                a["stages"]["installed"] = {"op": "field", "status": "OBSERVED", "source": name, "field": "commit"}
                assets.append(a)
            result = producer.collect({"schema": "node-asset-selection-v1", "identity": IDENTITY,
                                       "sources": sources, "assets": assets})
            self.assertEqual(result["assets"][0]["stages"]["installed"]["value"], "new")
            self.assertEqual(result["assets"][1]["stages"]["installed"]["status"], "UNKNOWN")
            self.assertEqual(result["assets"][1]["version_label"], "UNVERIFIED")
            self.assertFalse(result["runtime_health_assessed"])

    def test_symlink_outside_exact_selected_root_is_not_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp);allowed = root/'allowed';allowed.mkdir()
            outside = root/'outside.json';outside.write_text('{"x":1}')
            link = allowed/'alias.json';link.symlink_to(outside)
            with self.assertRaises(ValueError):
                producer.Reader({"x":{"path":str(link),"allowed_root":str(allowed),"kind":"json"}}).read("x")

    def test_failed_atomic_publication_preserves_original_and_cleans_own_temp(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'observation.json';path.write_text('original')
            with patch.object(producer.os, 'replace', side_effect=PermissionError):
                with self.assertRaises(PermissionError):producer.publish(path,{"status":"RUNTIME_UNVERIFIED"})
            self.assertEqual(path.read_text(),'original')
            self.assertEqual([p.name for p in Path(tmp).iterdir()],[path.name])


if __name__ == "__main__":
    unittest.main(verbosity=2)
