import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import workstation_check as check


class WorkstationCheckTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.now = "2026-10-14T08:00:00+00:00"
        self.path = self.root / "state.json"
        self.data = {"project_id": "example", "task_ref": "codex:thread:original",
                     "node_id": "mac-mini", "version": "v2", "observed_at": self.now,
                     "status": "HEALTHY", "complete": True, "resources": []}
        self.payload = {"schema": "workstation-check-v1", "now": self.now,
                        "scope": {"project_id": "example", "task_ref": "codex:thread:original",
                                  "node_id": "mac-mini", "version": "v2"},
                        "sources": [{"id": "service", "path": str(self.path),
                                     "max_age_seconds": 900, "status_path": "status",
                                     "ok_values": ["HEALTHY"]}]}

    def result(self):
        return check.evaluate(self.payload)

    def save(self):
        self.path.write_text(json.dumps(self.data))

    def test_legitimate_fresh_healthy_path(self):
        self.save()
        self.assertEqual(self.result()["status"], "normal")

    def test_existing_failure_is_abnormal_even_when_old(self):
        self.data["status"] = "FAIL_STOPPED"
        self.data["observed_at"] = "2026-10-14T07:13:31+00:00"
        self.save()
        self.assertEqual(self.result()["status"], "abnormal")

    def test_self_stopped_missing_and_expired_are_not_normal(self):
        self.data["status"] = "STOPPED_ON_FAILURE"
        self.save()
        self.assertEqual(self.result()["status"], "abnormal")
        self.data["status"] = "HEALTHY"
        self.data["observed_at"] = "2026-10-14T07:00:00+00:00"
        self.save()
        self.assertEqual(self.result()["status"], "unknown")
        self.path.unlink()
        self.assertEqual(self.result()["status"], "unknown")

    def test_read_error_and_offline_are_unknown(self):
        self.path.mkdir()
        self.assertEqual(self.result()["status"], "unknown")
        self.path.rmdir()
        self.payload["node_reachable"] = False
        self.assertEqual(self.result()["status"], "unknown")

    def test_observer_disk_error_and_corrupt_record_are_unknown(self):
        self.save()
        with mock.patch.object(check, "read_source", side_effect=OSError("ENOSPC")):
            self.assertEqual(self.result()["status"], "unknown")
        self.path.write_text("{bad json")
        self.assertEqual(self.result()["status"], "unknown")

    def test_wrong_project_node_task_and_unadopted_version(self):
        for field, value in (("project_id", "other"), ("node_id", "other"),
                             ("task_ref", "codex:thread:other"), ("version", "v1")):
            self.data[field] = value
            self.save()
            self.assertEqual(self.result()["status"], "unknown", field)
            self.data[field] = self.payload["scope"][field]

    def test_legacy_failure_without_task_or_node_binding_is_explicit_unknown(self):
        self.data.pop("task_ref")
        self.data.pop("node_id")
        self.data["status"] = "FAIL_STOPPED"
        self.save()
        result = self.result()
        self.assertEqual(result["status"], "unknown")
        self.assertIn("reported_failure_in_unbound_source", result["sources"][0]["reasons"])

    def test_cleanup_claim_requires_fresh_complete_resource_observation(self):
        self.payload["resource_claims"] = [{"effect_key": "cleanup-1", "source_id": "service",
                                            "resource_id": "container-1", "expected_state": "absent"}]
        self.data["resources"] = [{"id": "container-1", "state": "running"}]
        self.save()
        self.assertEqual(self.result()["status"], "abnormal")
        self.data["resources"] = []
        self.data["complete"] = False
        self.save()
        self.assertEqual(self.result()["status"], "unknown")
        self.data["complete"] = True
        self.save()
        self.assertEqual(self.result()["status"], "normal")

    def test_duplicate_effect_key_and_unbounded_source_are_rejected(self):
        self.save()
        claim = {"effect_key": "cleanup-1", "source_id": "service",
                 "resource_id": "container-1", "expected_state": "absent"}
        self.payload["resource_claims"] = [claim, dict(claim)]
        with self.assertRaises(check.CheckError):
            self.result()
        self.payload["resource_claims"] = []
        self.path.write_text("x" * 70000)
        self.assertEqual(self.result()["status"], "unknown")

    def test_framework_cli_returns_nonzero_on_unknown(self):
        self.save()
        cli = Path(__file__).resolve().parents[1] / "scripts/codex_framework.py"
        good = subprocess.run([sys.executable, str(cli), "workstation-check"],
                              input=json.dumps(self.payload), text=True, capture_output=True)
        self.assertEqual(good.returncode, 0, good.stderr)
        self.assertEqual(json.loads(good.stdout)["status"], "normal")
        self.path.unlink()
        missing = subprocess.run([sys.executable, str(cli), "workstation-check"],
                                 input=json.dumps(self.payload), text=True, capture_output=True)
        self.assertEqual(missing.returncode, 2, missing.stderr)
        self.assertEqual(json.loads(missing.stdout)["status"], "unknown")


if __name__ == "__main__":
    unittest.main()
