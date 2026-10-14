import contextlib
import io
import json
import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import execution_check as check
import codex_framework as framework


def scope(project, path, access="write"):
    return {"kind": "repo", "id": project, "path": path, "access": access}


def action_case():
    return {
        "project": {"id": "framework", "preferences_ref": "repo:framework:AGENTS.md",
                    "scopes": [scope("framework", "")]},
        "task": {"native_ref": "codex:thread:framework-task", "project_id": "framework",
                 "scopes": [scope("framework", "scripts")]},
        "context": {"task_ref": "codex:thread:framework-task", "capabilities": ["python"],
                    "remaining_budget": 1, "native_snapshot_ref": "codex:threads:read-1",
                    "native_snapshot_complete": True, "node_snapshot_ref": "node:capabilities:one",
                    "native_operations": []},
        "action": {"kind": "write", "resource": {"kind": "repo", "id": "framework",
                    "path": "scripts/check.py"}, "effect_key": "fix-check-1",
                   "capabilities": ["python"]},
    }


def fact(ref, kind, result=None, version=None):
    value = {"ref": ref, "source": "codex" if kind in ("failure", "fix", "adoption") else "node",
             "kind": kind, "project_id": "framework", "task_ref": "codex:thread:framework-task"}
    if result:
        value["result"] = result
    if version:
        value["version"] = version
    return value


class ActionChecks(unittest.TestCase):
    def test_project_isolation_and_task_scope(self):
        request = action_case()
        self.assertEqual(check.evaluate(request)["status"], "ready")
        request["action"]["resource"]["id"] = "trading"
        result = check.evaluate(request)
        self.assertIn("outside_project_scope", result["blocked"])
        request["action"]["resource"]["id"] = "framework"
        request["action"]["resource"]["path"] = "README.md"
        self.assertIn("outside_task_scope", check.evaluate(request)["blocked"])
        request["context"]["task_ref"] = "codex:thread:other-framework-task"
        self.assertIn("resume_original_task", check.evaluate(request)["blocked"])

    def test_context_failure_does_not_prove_absent_capability(self):
        request = action_case()
        request["context"]["tool_failure"] = {"ref": "codex:tool:one", "kind": "context_unavailable"}
        request["action"]["assertion"] = "capability_missing"
        self.assertIn("tool_context_is_not_capability_absence", check.evaluate(request)["blocked"])
        request["action"]["kind"] = "repair"
        request["action"]["defect"] = {"failure_ref": "codex:tool:one", "cause_ref": "node:fact:one",
                                         "cause_layer": "implementation"}
        self.assertIn("tool_context_is_not_implementation_defect", check.evaluate(request)["blocked"])

    def test_native_dedup_conflict_unknown_and_failed_recovery(self):
        request = action_case()
        existing = {"ref": "codex:tool:first", "project_id": "framework",
                    "task_ref": "codex:thread:other", "effect_key": "fix-check-1",
                    "resource": {"kind": "repo", "id": "framework", "path": "scripts/check.py"},
                    "state": "unknown"}
        request["context"]["native_operations"] = [existing]
        result = check.evaluate(request)
        self.assertIn("duplicate_or_uncertain_effect", result["blocked"])
        self.assertIn("concurrent_resource_conflict", result["blocked"])
        existing["state"] = "failed"
        self.assertIn("failed_operation_recovery", check.evaluate(request)["missing"])
        existing["task_ref"] = "codex:thread:framework-task"
        request["action"]["recovery"] = {"source_ref": "codex:tool:first",
                                           "cause_ref": "node:diagnosis", "correction_ref": "codex:tool:fix"}
        request["context"]["evidence"] = [fact("node:diagnosis", "cause"),
                                             fact("codex:tool:fix", "correction", "verified")]
        self.assertEqual(check.evaluate(request)["status"], "ready")
        request["action"]["effect_key"] = "different-action"
        existing["task_ref"] = "codex:thread:other"
        existing["state"] = "running"
        self.assertIn("concurrent_resource_conflict", check.evaluate(request)["blocked"])

    def test_dependencies_capability_budget_and_missing_snapshot(self):
        request = action_case()
        request["action"]["depends_on"] = ["codex:tool:predecessor"]
        request["action"]["capabilities"] = ["remote-node"]
        request["action"]["cost"] = 2
        request["context"].pop("native_snapshot_ref")
        request["context"].pop("node_snapshot_ref")
        result = check.evaluate(request)
        self.assertEqual(result["status"], "blocked")
        self.assertIn("budget_exceeded", result["blocked"])
        self.assertIn("execution_capability", result["missing"])
        self.assertIn("dependency_unverified", result["missing"])
        self.assertIn("native_operations_snapshot", result["missing"])
        self.assertIn("node_runtime_snapshot", result["missing"])
        request["context"]["native_operations"] = [{"ref": "codex:tool:predecessor",
                                                      "state": "succeeded", "project_id": "framework"}]
        request["context"]["remaining_budget"] = 3
        request["context"]["capabilities"].append("remote-node")
        request["context"]["native_snapshot_ref"] = "codex:threads:read-2"
        request["context"]["node_snapshot_ref"] = "node:runtime:two"
        self.assertEqual(check.evaluate(request)["status"], "ready")

    def test_cli_returns_nonzero_when_not_ready(self):
        request = action_case()
        request["context"].pop("native_snapshot_ref")
        with contextlib.redirect_stdout(io.StringIO()) as output, \
                mock.patch("sys.stdin", io.StringIO(json.dumps(request))):
            self.assertEqual(framework.main(["check"]), 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "needs_evidence")

    def test_publish_requires_test_for_exact_candidate_version(self):
        request = action_case()
        request["action"]["kind"] = "publish"
        request["action"]["release"] = {"version": "commit:abc", "test_ref": "node:test:release"}
        request["context"]["evidence"] = [fact("node:test:release", "release_test", "passed", "commit:older")]
        self.assertIn("tested_release_version", check.evaluate(request)["missing"])
        request["context"]["evidence"][0]["version"] = "commit:abc"
        self.assertEqual(check.evaluate(request)["status"], "ready")


class ClosureChecks(unittest.TestCase):
    def close_case(self):
        request = action_case()
        request.pop("action")
        request["phase"] = "close_issue"
        request["issue"] = {"task_ref": "codex:thread:framework-task", "project_id": "framework",
                            "resource": {"kind": "repo", "id": "framework", "path": "scripts/check.py"},
                            "failure_ref": "codex:tool:failure", "cause_ref": "node:diagnosis",
                            "fix_ref": "codex:commit:fix", "acceptance_owner": "codex:thread:framework-task",
                            "followup_owner": "codex:thread:framework-task",
                            "regression": {"original_ref": "node:test:original",
                                           "affected_ref": "node:test:affected", "version": "commit:abc",
                                           "result": "passed"},
                            "adoption": {"ref": "codex:tool:install", "version": "commit:abc"},
                            "effect": {"ref": "node:runtime:observation", "result": "observed"}}
        request["context"]["evidence"] = [
            fact("codex:tool:failure", "failure"), fact("node:diagnosis", "cause"),
            fact("codex:commit:fix", "fix"),
            fact("node:test:original", "regression_original", "passed", "commit:abc"),
            fact("node:test:affected", "regression_affected", "passed", "commit:abc"),
            fact("codex:tool:install", "adoption", "applied", "commit:abc"),
            fact("node:runtime:observation", "effect", "observed", "commit:abc")]
        request["context"]["evidence"][3]["related_ref"] = "codex:tool:failure"
        return request

    def test_closure_requires_actual_links_tested_adoption_and_effect(self):
        request = self.close_case()
        self.assertEqual(check.evaluate(request)["status"], "ready")
        request["context"]["evidence"][-1]["result"] = "unknown"
        self.assertIn("observed_effect", check.evaluate(request)["missing"])
        request["context"]["evidence"][-1]["result"] = "observed"
        request["issue"]["adoption"]["version"] = "commit:older"
        self.assertIn("adopted_tested_version", check.evaluate(request)["missing"])
        request["issue"]["adoption"]["version"] = "commit:abc"
        request["context"]["evidence"][3]["result"] = "failed"
        self.assertIn("regression_passed", check.evaluate(request)["missing"])
        request["context"]["evidence"][3]["result"] = "passed"
        request["context"]["evidence"][3]["project_id"] = "trading"
        self.assertIn("unresolved_regression_original_reference", check.evaluate(request)["missing"])

    def test_full_log_copy_is_rejected(self):
        request = self.close_case()
        request["context"]["evidence"][0]["body"] = "full private transcript"
        with self.assertRaises(check.CheckError):
            check.evaluate(request)


if __name__ == "__main__":
    unittest.main()
