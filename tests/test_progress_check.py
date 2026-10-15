"""Scenario checks for scoped failures and task progress decisions."""

import copy
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "scripts"),
                str(Path(__file__).resolve().parents[1] / "tests")]
import execution_check as check
import test_execution_check as fixtures


class ProgressChecks(unittest.TestCase):
    def test_after_failure_cannot_claim_working_without_remaining_steps(self):
        request = self.progress(self.base())
        request["progress"]["steps"] = []
        self.assertIn("remaining_steps_unlisted", check.evaluate(request)["missing"])

    def test_new_goal_and_scoped_refusal_require_both_resume_and_progress(self):
        request = self.base()
        helper = fixtures.WorkspaceBoundaryChecks()
        helper.add_user_evidence(request, "codex:user:old-goal", "goal", "Old scheduling goal")
        helper.add_user_evidence(request, "codex:user:new-goal", "goal", "Current resource goal")
        request["phase"] = "resume"
        request["resume"] = {"resource": request["action"]["resource"],
                             "goal_ref": "codex:user:old-goal"}
        self.assertIn("newer_goal_requires_scope_review", check.evaluate(request)["missing"])
        request["resume"]["goal_ref"] = "codex:user:new-goal"
        self.assertEqual(check.evaluate(request)["status"], "ready")
        request = self.progress(request)
        result = check.evaluate(request)
        self.assertEqual({step["id"]: step["state"] for step in result["steps"]},
                         {"restore": "direct_refusal", "verify": "dependency_blocked",
                          "diagnose": "ready"})

    def base(self):
        request = fixtures.WorkspaceBoundaryChecks().local_case()
        target = request["action"]["resource"]
        refusal = {"kind": "platform_denied", "ref": "codex:tool:denied",
                   "resource": target, "action_kind": "write",
                   "side_effect": "local_change",
                   "destination_node": "mac-mini"}
        request["context"]["tool_failures"] = [
            refusal,
            {"kind": "resource_exhausted", "ref": "codex:tool:old-capacity",
             "resource": target, "action_kind": "write"}]
        request["context"]["evidence"].append({
            "ref": refusal["ref"], "source": "codex", "kind": "failure",
            "project_id": "framework", "task_ref": request["task"]["native_ref"],
            "native_item_id": refusal["ref"], "result": "failed"})
        request["context"]["native_sources"][0]["turns"][0]["items"].append({
            "id": refusal["ref"], "type": "commandExecution", "status": "failed",
            "exitCode": 1, "command": "refused local write",
            "output": {"text": "platform denied", "truncated": False}})
        return request

    def read_step(self, request):
        action = copy.deepcopy(request["action"])
        action["kind"] = "read"
        action["workspace"]["side_effect"] = "read"
        action.pop("capabilities")
        action.pop("effect_key")
        action["workspace"].pop("authorization_ref")
        return action

    def progress(self, request, claim="working"):
        restore = copy.deepcopy(request["action"])
        diagnose = self.read_step(request)
        verify = self.read_step(request)
        return {**request, "phase": "progress", "progress": {
            "checkpoint": "after_failure", "claim": claim,
            "steps": [
                {"id": "restore", "action": restore, "depends_on": []},
                {"id": "verify", "action": verify, "depends_on": ["restore"]},
                {"id": "diagnose", "action": diagnose, "depends_on": []}]}}

    def test_multiple_failures_preserve_refusal_and_allow_independent_read(self):
        request = self.base()
        denied = check.evaluate(request)
        self.assertIn("platform_denied_action", denied["blocked"])
        self.assertIn("local_resource_diagnosis_before_retry", denied["missing"])
        read = self.read_step(request)
        request["action"] = read
        self.assertEqual(check.evaluate(request)["status"], "ready")

    def test_progress_classifies_direct_dependency_and_independent_steps(self):
        request = self.progress(self.base())
        result = check.evaluate(request)
        self.assertEqual({item["id"]: item["state"] for item in result["steps"]},
                         {"restore": "direct_refusal",
                          "verify": "dependency_blocked", "diagnose": "ready"})
        self.assertFalse(result["wait_allowed"])

    def test_wait_requires_no_independent_work_and_verified_refusal(self):
        request = self.progress(self.base(), "waiting")
        request["progress"]["checkpoint"] = "wait"
        self.assertIn("independent_work_remaining", check.evaluate(request)["blocked"])
        request["progress"]["steps"].pop()
        result = check.evaluate(request)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["wait_allowed"])
        request["context"]["evidence"].pop()
        self.assertIn("wait_refusal_unverified", check.evaluate(request)["missing"])

    def test_channel_change_does_not_clear_denial(self):
        request = self.base()
        request["action"]["workspace"]["channel"] = "different_thread"
        self.assertIn("platform_denied_action", check.evaluate(request)["blocked"])
        request["action"]["workspace"]["execution_node"] = "different_machine"
        self.assertIn("platform_denied_action", check.evaluate(request)["blocked"])

    def test_no_execution_does_not_support_continuous_tracking(self):
        request = self.progress(self.base(), "continuous_tracking")
        request["progress"]["checkpoint"] = "stage_end"
        self.assertIn("active_execution_unverified", check.evaluate(request)["missing"])

    def test_handoff_cannot_claim_completion_with_blocked_steps(self):
        request = self.progress(self.base(), "completed")
        request["progress"]["checkpoint"] = "handoff"
        self.assertIn("remaining_steps_not_complete", check.evaluate(request)["blocked"])

    def test_old_runtime_measurement_does_not_make_resource_action_ready(self):
        request = self.progress(self.base())
        request["context"]["tool_failures"] = []
        request["progress"]["steps"] = request["progress"]["steps"][:1]
        request["context"]["node_snapshot_observed_at"] = "2020-01-01T00:00:00Z"
        request["context"]["node_snapshot_max_age_seconds"] = 900
        result = check.evaluate(request)
        self.assertEqual(result["steps"][0]["state"], "needs_evidence")
        self.assertIn("node_snapshot_stale_or_unverified", result["steps"][0]["missing"])
        single = {**request, "phase": "action", "action": request["progress"]["steps"][0]["action"]}
        self.assertIn("node_snapshot_stale_or_unverified", check.evaluate(single)["missing"])
        request["context"]["node_snapshot_observed_at"] = datetime.now(timezone.utc).isoformat()
        self.assertEqual(check.evaluate(request)["steps"][0]["state"], "ready")

    def test_synthetic_completion_cannot_close_local_incident_at_handoff(self):
        request = self.progress(self.base(), "completed")
        request["progress"]["checkpoint"] = "handoff"
        request["progress"]["steps"] = []
        request["context"]["incident"] = {
            "goal_ref": "codex:user:local-incident",
            "resource": request["action"]["resource"],
            "responsible_node": "mac-mini", "data_node": "mac-mini",
            "destination_nodes": ["mac-mini"]}
        self.assertIn("incident_closure_required", check.evaluate(request)["missing"])


if __name__ == "__main__":
    unittest.main()
