import contextlib
import hashlib
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
                    "scopes": [scope("framework", "")],
                    "independent_verification_required": True},
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


def bind_native_sources(request):
    """Provide native-shaped source items; later mutations cannot alter them."""
    entries = []
    for record in request["context"]["evidence"]:
        record["source"] = "codex"
        record["native_item_id"] = record["ref"]
        kind = record["kind"]
        if kind in ("goal", "cause", "authorization", "confirmation"):
            body = ("user requested complete behavior" if kind == "goal" else
                    "diagnosed cause" if kind == "cause" else
                    "user granted this action")
            record["digest"] = hashlib.sha256(body.encode()).hexdigest()
            item = {"id": record["ref"], "type": "agentMessage" if kind == "cause" else "userMessage",
                    "text": body}
        else:
            body = "result " + str(record.get("version", ""))
            failed = kind == "failure"
            item = {"id": record["ref"], "type": "commandExecution",
                    "status": "completed", "exitCode": 1 if failed else 0,
                    "command": "verify " + kind, "output": {"text": body, "truncated": False}}
        entries.append(item)
    request["context"]["native_sources"] = [{"schemaVersion": 1,
        "thread": {"id": request["task"]["native_ref"].removeprefix("codex:thread:")},
        "turns": [{"items": entries}]}]


def attach_local_verification(request, target, version):
    """A fixed expected output and a manually invoked, version-bound result."""
    plan_ref, expected_ref, result_ref = "codex:plan:local", "codex:expect:local", "codex:run:local"
    body = "local output"
    value_hash = hashlib.sha256(body.encode()).hexdigest()
    records = request["context"]["evidence"]
    for ref, kind in ((plan_ref, "local_plan"), (expected_ref, "local_expectation"),
                      (result_ref, "local_result")):
        record = fact(ref, kind, "passed" if kind == "local_result" else None,
                      version if kind == "local_result" else None)
        record["source"] = "codex"
        record["native_item_id"] = ref
        record["digest"] = (hashlib.sha256(("case:original expected:" + value_hash + " " + version).encode()).hexdigest()
                            if kind == "local_plan" else value_hash)
        if kind != "local_plan":
            record["resource"] = target
            record["related_ref"] = plan_ref if kind == "local_expectation" else expected_ref
        records.append(record)
    request["context"]["native_sources"][0]["turns"][0]["items"] += [
        {"id": plan_ref, "type": "agentMessage",
         "text": "case:original expected:" + value_hash + " " + version},
        {"id": expected_ref, "type": "agentMessage", "text": body},
        {"id": result_ref, "type": "commandExecution", "status": "completed", "exitCode": 0,
         "command": "manual local command " + version,
         "output": {"text": body, "truncated": False}}]
    return {"plan_ref": plan_ref, "cases": [{"id": "original", "resource": target,
            "expected_ref": expected_ref, "observed_ref": result_ref}]}


def attach_independent_verification(request, version, outcome="passed", *, verifier="codex:thread:review-task",
                                    suffix="1", revision_ref=None):
    """Synthetic, bounded second-task evidence; it is not a real review."""
    target = request["issue"]["resource"]
    lock_ref, verdict_ref = "codex:review:lock:" + suffix, "codex:review:verdict:" + suffix
    digests = [hashlib.sha256((suffix + part).encode()).hexdigest()
               for part in ("data", "environment", "criteria")]
    lock_text = "\n".join(("code:" + version, "data:" + digests[0],
                           "environment:" + digests[1], "criteria:" + digests[2]))
    lock_digest = hashlib.sha256(lock_text.encode()).hexdigest()
    verdict_text = "\n".join(("version:" + version, "lock:" + lock_digest,
                              "outcome:" + outcome))
    for ref, kind, body, result in ((lock_ref, "verification_lock", lock_text, None),
                                    (verdict_ref, "verification_verdict", verdict_text, outcome)):
        record = {"ref": ref, "source": "codex", "kind": kind,
                  "project_id": request["project"]["id"], "task_ref": verifier,
                  "resource": target, "version": version, "native_item_id": ref,
                  "digest": hashlib.sha256(body.encode()).hexdigest()}
        if result is not None:
            record.update(result=result, related_ref=lock_ref)
        request["context"]["evidence"].append(record)
    items = [{"id": lock_ref, "type": "agentMessage", "text": lock_text}]
    if outcome == "not_run":
        items.append({"id": verdict_ref, "type": "agentMessage", "text": verdict_text})
    else:
        items.append({"id": verdict_ref, "type": "commandExecution",
                      "status": "completed" if outcome == "passed" else "failed",
                      "exitCode": 0 if outcome == "passed" else 1,
                      "command": "synthetic independent probe " + version,
                      "output": {"text": verdict_text, "truncated": False}})
    snapshots = request["context"].setdefault("native_sources", [])
    existing = next((entry for entry in snapshots
                     if entry.get("thread", {}).get("id") == verifier.removeprefix("codex:thread:")), None)
    if existing is None:
        snapshots.append({"schemaVersion": 1,
                          "thread": {"id": verifier.removeprefix("codex:thread:")},
                          "turns": [{"items": items}]})
    else:
        existing["turns"][0]["items"].extend(items)
    attempt = {"task_ref": verifier, "version": version, "lock_ref": lock_ref,
               "verdict_ref": verdict_ref, "data_digest": digests[0],
               "environment_digest": digests[1], "criteria_digest": digests[2],
               "outcome": outcome}
    if revision_ref:
        attempt["revision_ref"] = revision_ref
    request["issue"].setdefault("independent_verification", {"attempts": []})["attempts"].append(attempt)
    request["issue"]["acceptance_owner"] = verifier
    return attempt


class ActionChecks(unittest.TestCase):
    def test_resume_flags_newer_user_goal_without_deciding_its_meaning(self):
        request = action_case()
        helper = WorkspaceBoundaryChecks()
        helper.add_user_evidence(request, "codex:user:old-goal", "goal", "Old scheduling goal")
        helper.add_user_evidence(request, "codex:user:new-goal", "goal", "Current resource goal")
        request["phase"] = "resume"
        request["resume"] = {"resource": request["action"]["resource"],
                             "goal_ref": "codex:user:old-goal"}
        self.assertIn("newer_goal_requires_scope_review", check.evaluate(request)["missing"])
        request["resume"]["goal_ref"] = "codex:user:new-goal"
        self.assertEqual(check.evaluate(request)["status"], "ready")
        old_item, new_item = request["context"]["native_sources"][0]["turns"][0]["items"]
        request["context"]["native_sources"] = [
            {"schemaVersion": 1, "thread": {"id": "framework-task"},
             "turns": [{"items": [old_item]}]},
            {"schemaVersion": 1, "thread": {"id": "framework-task"},
             "turns": [{"items": [new_item]}]}]
        self.assertIn("goal_order_unverified", check.evaluate(request)["missing"])

    def test_agent_goal_cannot_be_used_as_direct_user_goal(self):
        request = action_case()
        WorkspaceBoundaryChecks().add_user_evidence(
            request, "codex:agent:goal", "goal", "Agent restates a past goal")
        request["context"]["native_sources"][0]["turns"][0]["items"][0]["type"] = "agentMessage"
        request["phase"] = "resume"
        request["resume"] = {"resource": request["action"]["resource"],
                             "goal_ref": "codex:agent:goal"}
        self.assertIn("unverified_goal_source", check.evaluate(request)["missing"])

    def test_native_user_content_blocks_preserve_goal_source(self):
        request = action_case()
        WorkspaceBoundaryChecks().add_user_evidence(
            request, "codex:user:goal", "goal", "Current resource goal")
        item = request["context"]["native_sources"][0]["turns"][0]["items"][0]
        item["content"] = [{"type": "text", "text": item.pop("text")}]
        request["phase"] = "resume"
        request["resume"] = {"resource": request["action"]["resource"],
                             "goal_ref": "codex:user:goal"}
        self.assertEqual(check.evaluate(request)["status"], "ready")

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

    def test_cross_project_shared_resource_conflict_uses_physical_overlap(self):
        request = action_case()
        request["context"]["native_operations"] = [{
            "ref": "codex:other:write", "project_id": "another-project",
            "task_ref": "codex:thread:other", "effect_key": "unrelated-effect",
            "resource": {"kind": "repo", "id": "framework", "path": "scripts/check.py"},
            "state": "running"}]
        self.assertIn("concurrent_resource_conflict", check.evaluate(request)["blocked"])
        request["context"]["native_operations"][0]["resource"]["path"] = "docs/unrelated.md"
        self.assertEqual(check.evaluate(request)["status"], "ready")

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
        bind_native_sources(request)
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
        bind_native_sources(request)
        self.assertIn("independent_verification_not_run", check.evaluate(request)["missing"])
        WorkspaceBoundaryChecks().add_user_evidence(
            request, "codex:user:publish-goal", "goal", "Publish this accepted framework version")
        request["action"]["release"]["goal_ref"] = "codex:user:publish-goal"
        request["issue"] = {"resource": request["action"]["resource"]}
        attach_independent_verification(request, "commit:abc")
        request["action"]["release"]["independent_verification"] = request.pop("issue")["independent_verification"]
        self.assertEqual(check.evaluate(request)["status"], "ready")


class WorkspaceBoundaryChecks(unittest.TestCase):
    def local_case(self):
        request = action_case()
        target = request["action"]["resource"]
        request["action"]["workspace"] = {
            "channel": "cloud_threads", "execution_node": "mac-mini", "data_node": "mac-mini",
            "destination_node": "mac-mini", "side_effect": "local_change",
            "authorization_ref": "codex:user:local-maintenance"}
        self.add_user_evidence(request, "codex:user:local-maintenance", "authorization",
                               "Local workstation maintenance is authorized", "granted", target,
                               {"execution_nodes": ["mac-mini"], "data_nodes": ["mac-mini"],
                                "destination_nodes": ["mac-mini"],
                                "side_effects": ["read", "local_change"]})
        return request

    def add_user_evidence(self, request, ref, kind, text, result=None, target=None, workspace=None):
        record = {"ref": ref, "source": "codex", "kind": kind, "project_id": "framework",
                  "task_ref": request["task"]["native_ref"], "native_item_id": ref,
                  "digest": hashlib.sha256(text.encode()).hexdigest()}
        if result:
            record["result"] = result
        if target:
            record["resource"] = target
        if workspace:
            record["workspace"] = workspace
        request["context"].setdefault("evidence", []).append(record)
        sources = request["context"].setdefault("native_sources", [{"schemaVersion": 1,
            "thread": {"id": "framework-task"}, "turns": [{"items": []}]}])
        sources[0]["turns"][0]["items"].append({"id": ref, "type": "userMessage", "text": text})

    def test_coordination_channel_does_not_move_local_execution_or_renew_grant(self):
        request = self.local_case()
        self.assertEqual(check.evaluate(request)["status"], "ready")
        request["action"]["workspace"]["channel"] = "local_cli"
        self.assertEqual(check.evaluate(request)["status"], "ready")

    def test_bounded_local_log_read_uses_existing_task_scope(self):
        request = action_case()
        request["action"]["kind"] = "read"
        request["action"]["workspace"] = {
            "channel": "cloud_threads", "execution_node": "mac-mini", "data_node": "mac-mini",
            "destination_node": "mac-mini", "side_effect": "read"}
        self.assertEqual(check.evaluate(request)["status"], "ready")

    def test_local_tool_error_is_not_missing_user_authorization(self):
        request = self.local_case()
        request["context"]["tool_failure"] = {"kind": "context_unavailable",
                                               "ref": "codex:tool:local-error"}
        self.assertEqual(check.evaluate(request)["status"], "ready")
        request["action"]["assertion"] = "capability_missing"
        self.assertIn("tool_context_is_not_capability_absence", check.evaluate(request)["blocked"])

    def test_agent_or_tool_text_cannot_be_user_authorization(self):
        request = self.local_case()
        request["context"]["native_sources"][0]["turns"][0]["items"][0]["type"] = "agentMessage"
        self.assertIn("local_action_authorization", check.evaluate(request)["missing"])

    def test_real_transfer_outside_local_grant_needs_specific_authorization_and_risk_review(self):
        request = self.local_case()
        workspace = request["action"]["workspace"]
        workspace.update(destination_node="assistant-cloud", side_effect="transfer",
                         transfer_authorization_ref="codex:user:local-maintenance")
        result = check.evaluate(request)
        self.assertIn("outside_authorized_workspace", result["blocked"])
        self.assertIn("cross_environment_necessity_and_impacts", result["missing"])
        workspace["channel"] = "local_cli"
        self.assertIn("outside_authorized_workspace", check.evaluate(request)["blocked"])
        workspace.pop("transfer_authorization_ref")
        self.assertIn("cross_environment_authorization", check.evaluate(request)["missing"])

    def test_irreversible_delete_requires_action_confirmation(self):
        request = self.local_case()
        request["context"]["evidence"][0]["workspace"]["side_effects"].append("irreversible_delete")
        workspace = request["action"]["workspace"]
        workspace["side_effect"] = "irreversible_delete"
        self.assertIn("irreversible_action_confirmation", check.evaluate(request)["missing"])
        workspace["confirmation_ref"] = "codex:user:delete-confirmation"
        self.add_user_evidence(request, workspace["confirmation_ref"], "confirmation",
                               "Confirm this specific irreversible deletion", "granted",
                               request["action"]["resource"])
        self.assertEqual(check.evaluate(request)["status"], "ready")

    def test_platform_denial_stops_same_action_across_channels_not_independent_repair(self):
        request = self.local_case()
        request["context"]["tool_failure"] = {
            "kind": "platform_denied", "ref": "codex:tool:denied",
            "resource": request["action"]["resource"], "action_kind": "write",
            "side_effect": "local_change"}
        self.assertIn("platform_denied_action", check.evaluate(request)["blocked"])
        request["action"]["workspace"]["channel"] = "different_thread"
        self.assertIn("platform_denied_action", check.evaluate(request)["blocked"])
        request["action"].pop("workspace")
        self.assertIn("platform_denied_action", check.evaluate(request)["blocked"])
        request["action"]["resource"] = {"kind": "repo", "id": "framework", "path": "scripts/other.py"}
        request["action"]["effect_key"] = "independent-repair"
        self.assertEqual(check.evaluate(request)["status"], "ready")

    def test_denial_survives_write_to_invoke_with_same_effect(self):
        request = self.local_case()
        request["context"]["tool_failure"] = {
            "kind": "platform_denied", "ref": "codex:tool:denied",
            "resource": request["action"]["resource"], "action_kind": "write",
            "effect_key": request["action"]["effect_key"],
            "destination_node": "mac-mini", "side_effect": "local_change"}
        request["action"]["kind"] = "invoke"
        self.assertIn("platform_denied_action", check.evaluate(request)["blocked"])
        request["action"]["effect_key"] = "different-independent-effect"
        self.assertNotIn("platform_denied_action", check.evaluate(request)["blocked"])

    def test_local_capacity_does_not_authorize_other_project_shutdown(self):
        request = self.local_case()
        request["action"]["resource"] = {"kind": "service", "id": "other-project"}
        result = check.evaluate(request)
        self.assertIn("outside_project_scope", result["blocked"])
        self.assertIn("outside_task_scope", result["blocked"])

    def test_one_local_incident_keeps_responsibility_and_data_boundary_across_failures(self):
        request = self.local_case()
        target = request["action"]["resource"]
        request["context"]["incident"] = {
            "goal_ref": "codex:user:local-incident", "resource": target,
            "responsible_node": "mac-mini", "data_node": "mac-mini",
            "destination_nodes": ["mac-mini"]}
        self.add_user_evidence(request, "codex:user:local-incident", "goal",
                               "Diagnose and recover this incident on the local workstation")
        request["action"]["workspace"]["responsible_node"] = "mac-mini"

        # A repeated capacity failure asks for diagnosis, never a fresh grant.
        request["context"]["tool_failure"] = {
            "kind": "resource_exhausted", "resource": target,
            "action_kind": "write", "ref": "codex:tool:capacity"}
        result = check.evaluate(request)
        self.assertIn("local_resource_diagnosis_before_retry", result["missing"])
        self.assertNotIn("local_action_authorization", result["missing"])
        log_read = json.loads(json.dumps(request))
        log_read["action"]["kind"] = "read"
        log_read["action"]["workspace"]["side_effect"] = "read"
        log_read["action"]["workspace"].pop("authorization_ref")
        self.assertEqual(check.evaluate(log_read)["status"], "ready")

        # A tool-context failure does not revoke the old grant either.
        request["context"]["tool_failure"] = {"kind": "context_unavailable", "ref": "codex:tool:local"}
        self.assertEqual(check.evaluate(request)["status"], "ready")

        # Cloud CI can test synthetic code while the incident stays owned locally.
        cloud_test = json.loads(json.dumps(request))
        cloud_test["context"].pop("tool_failure")
        cloud_test["action"]["workspace"] = {
            "channel": "cloud_threads", "execution_node": "assistant-cloud",
            "data_node": "synthetic-fixture", "destination_node": "synthetic-fixture",
            "responsible_node": "mac-mini", "side_effect": "synthetic_test",
            "authorization_ref": "codex:user:synthetic-test", "fixture_ref": "codex:item:fixture"}
        self.add_user_evidence(cloud_test, "codex:user:synthetic-test", "authorization",
                               "Synthetic code-only CI is authorized", "granted", target,
                               {"execution_nodes": ["assistant-cloud"],
                                "data_nodes": ["synthetic-fixture"],
                                "destination_nodes": ["synthetic-fixture"],
                                "side_effects": ["synthetic_test"]})
        self.add_user_evidence(cloud_test, "codex:item:fixture", "synthetic_fixture",
                               "This fixture contains synthetic code data only")
        self.assertEqual(check.evaluate(cloud_test)["status"], "ready")

        # A later resource problem cannot turn that CI into a real cloud restore.
        transfer = json.loads(json.dumps(request))
        transfer["context"].pop("tool_failure")
        transfer["action"]["workspace"].update(
            channel="cloud_threads", destination_node="assistant-cloud",
            responsible_node="assistant-cloud", side_effect="transfer",
            transfer_authorization_ref="codex:user:local-maintenance")
        result = check.evaluate(transfer)
        self.assertIn("incident_responsibility_drift", result["blocked"])
        self.assertIn("incident_destination_drift", result["blocked"])
        self.assertIn("outside_authorized_workspace", result["blocked"])
        self.assertIn("cross_environment_necessity_and_impacts", result["missing"])

        # A real platform refusal stays bound to that transfer across channels.
        transfer["context"]["tool_failure"] = {
            "kind": "platform_denied", "resource": target, "action_kind": "write",
            "side_effect": "transfer", "ref": "codex:tool:denied-transfer"}
        transfer["action"]["workspace"]["channel"] = "local_cli"
        self.assertIn("platform_denied_action", check.evaluate(transfer)["blocked"])

        # The original local investigation is still authorized and responsible.
        resumed_local = json.loads(json.dumps(transfer))
        resumed_local["action"]["workspace"] = request["action"]["workspace"]
        self.assertEqual(check.evaluate(resumed_local)["status"], "ready")
        resumed_local["action"].pop("workspace")
        self.assertIn("workspace_boundary_for_incident", check.evaluate(resumed_local)["missing"])


class ClosureChecks(unittest.TestCase):
    def close_case(self):
        request = action_case()
        request.pop("action")
        request["phase"] = "close_issue"
        request["issue"] = {"task_ref": "codex:thread:framework-task", "project_id": "framework",
                            "resource": {"kind": "repo", "id": "framework", "path": "scripts/check.py"},
                            "goal_ref": "codex:user:goal", "acceptance_ref": "codex:run:goal",
                            "failure_ref": "codex:tool:failure", "cause_ref": "node:diagnosis",
                            "fix_ref": "codex:commit:fix", "acceptance_owner": "codex:thread:framework-task",
                            "followup_owner": "codex:thread:framework-task",
                            "regression": {"original_ref": "node:test:original",
                                           "affected_ref": "node:test:affected", "version": "commit:abc",
                                           "result": "passed"},
                            "adoption": {"ref": "codex:tool:install", "version": "commit:abc"},
                            "effect": {"ref": "node:runtime:observation", "result": "observed"},
                            "followup": {"ref": "codex:turn:continued"}}
        request["context"]["evidence"] = [
            fact("codex:user:goal", "goal"),
            fact("codex:tool:failure", "failure", "failed"), fact("node:diagnosis", "cause"),
            fact("codex:commit:fix", "fix"),
            fact("node:test:original", "regression_original", "passed", "commit:abc"),
            fact("node:test:affected", "regression_affected", "passed", "commit:abc"),
            fact("codex:run:goal", "acceptance", "passed", "commit:abc"),
            fact("codex:tool:install", "adoption", "applied", "commit:abc"),
            fact("node:runtime:observation", "effect", "observed", "commit:abc"),
            fact("codex:turn:continued", "followup", "used_correction", "commit:abc")]
        request["context"]["evidence"][4]["related_ref"] = "codex:tool:failure"
        request["context"]["evidence"][6]["related_ref"] = "codex:user:goal"
        request["context"]["evidence"][9]["related_ref"] = "codex:commit:fix"
        bind_native_sources(request)
        request["issue"]["local_verification"] = attach_local_verification(
            request, request["issue"]["resource"], "commit:abc")
        attach_independent_verification(request, "commit:abc")
        return request

    def test_self_test_cannot_sign_independent_acceptance(self):
        request = self.close_case()
        request["issue"].pop("independent_verification")
        result = check.evaluate(request)
        self.assertIn("independent_verification_not_run", result["missing"])
        self.assertEqual(result["verification_outcome"], "not_run")
        request = self.close_case()
        attempt = request["issue"]["independent_verification"]["attempts"][0]
        attempt["task_ref"] = request["task"]["native_ref"]
        self.assertIn("verification_not_independent", check.evaluate(request)["blocked"])
        request = self.close_case()
        request["issue"]["acceptance_owner"] = request["task"]["native_ref"]
        self.assertIn("completion_responsibility", check.evaluate(request)["missing"])

    def test_verification_freezes_inputs_and_exact_version(self):
        request = self.close_case()
        attempt = request["issue"]["independent_verification"]["attempts"][0]
        self.assertEqual(check.evaluate(request)["status"], "ready")
        attempt["data_digest"] = hashlib.sha256(b"different-data").hexdigest()
        self.assertIn("verification_lock_unverified", check.evaluate(request)["missing"])
        request = self.close_case()
        attempt = request["issue"]["independent_verification"]["attempts"][0]
        attempt["version"] = "commit:old"
        self.assertIn("independent_verification_version_mismatch", check.evaluate(request)["missing"])
        request = self.close_case()
        attempt = request["issue"]["independent_verification"]["attempts"][0]
        request["context"]["native_sources"][-1]["turns"][0]["items"].reverse()
        self.assertIn("verification_verdict_unverified", check.evaluate(request)["missing"])

    def test_failed_and_not_run_are_not_acceptance_and_failed_revision_is_preserved(self):
        request = self.close_case()
        request["issue"].pop("independent_verification")
        attach_independent_verification(request, "commit:abc", "failed", suffix="fail")
        result = check.evaluate(request)
        self.assertIn("independent_verification_failed", result["deviations"])
        self.assertEqual(result["verification_outcome"], "failed")
        attach_independent_verification(request, "commit:abc", suffix="retry")
        self.assertIn("failed_version_reused_without_revision", check.evaluate(request)["blocked"])
        request = self.close_case()
        request["issue"].pop("independent_verification")
        attach_independent_verification(request, "commit:abc", "not_run", suffix="pending")
        result = check.evaluate(request)
        self.assertIn("independent_verification_not_run", result["missing"])
        self.assertEqual(result["verification_outcome"], "not_run")

    def test_failed_verification_returns_to_implementation_then_tests_new_version(self):
        request = self.close_case()
        latest = request["issue"]["independent_verification"]["attempts"][0]
        old = attach_independent_verification(request, "commit:old", "failed", suffix="old")
        request["issue"]["independent_verification"]["attempts"] = [old, latest]
        review_items = request["context"]["native_sources"][-1]["turns"][0]["items"]
        review_items[:] = review_items[-2:] + review_items[:-2]
        latest["revision_ref"] = request["issue"]["fix_ref"]
        fix = next(record for record in request["context"]["evidence"] if record["ref"] == latest["revision_ref"])
        fix["version"] = "commit:abc"
        fix_item = next(item for item in request["context"]["native_sources"][0]["turns"][0]["items"]
                        if item["id"] == latest["revision_ref"])
        fix_item["output"]["text"] = "result commit:abc"
        result = check.evaluate(request)
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["verification_outcome"], "passed")
        latest.pop("revision_ref")
        self.assertIn("failed_verification_revision", check.evaluate(request)["missing"])

    def test_old_failed_version_stays_invalid_after_revised_pass(self):
        request = self.close_case()
        first = request["issue"]["independent_verification"]["attempts"][0]
        first["outcome"] = "failed"
        first_record = next(record for record in request["context"]["evidence"]
                            if record["ref"] == first["verdict_ref"])
        first_record["result"] = "failed"
        first_item = request["context"]["native_sources"][-1]["turns"][0]["items"][1]
        first_item["status"], first_item["exitCode"] = "failed", 1
        old_text = first_item["output"]["text"].replace("outcome:passed", "outcome:failed")
        first_item["output"]["text"] = old_text
        first_record["digest"] = hashlib.sha256(old_text.encode()).hexdigest()
        revised = attach_independent_verification(request, "commit:new", suffix="revised",
                                                  revision_ref="codex:commit:new")
        request["context"]["evidence"].append(fact("codex:commit:new", "fix", version="commit:new"))
        # The old code is presented again after the revised candidate passed.
        attach_independent_verification(request, "commit:abc", suffix="old-again")
        self.assertIn("failed_version_reused_without_revision", check.evaluate(request)["blocked"])

    def test_attempt_array_cannot_reverse_native_verdict_order(self):
        request = self.close_case()
        earlier_pass = request["issue"]["independent_verification"]["attempts"][0]
        later_fail = attach_independent_verification(request, "commit:old", "failed", suffix="later")
        request["issue"]["independent_verification"]["attempts"] = [later_fail, earlier_pass]
        self.assertIn("verification_attempt_order_unverified", check.evaluate(request)["missing"])

    def test_ordinary_closure_does_not_require_unrequested_independent_task(self):
        request = self.close_case()
        request["project"]["independent_verification_required"] = False
        request["issue"].pop("independent_verification")
        request["issue"]["acceptance_owner"] = request["task"]["native_ref"]
        self.assertEqual(check.evaluate(request)["status"], "ready")

    def test_not_run_between_failure_and_old_pass_cannot_clear_failure(self):
        request = self.close_case()
        request["issue"].pop("independent_verification")
        attach_independent_verification(request, "commit:old", "failed", suffix="old-fail")
        attach_independent_verification(request, "commit:new", "not_run", suffix="new-pending")
        attach_independent_verification(request, "commit:old", "passed", suffix="old-reused")
        self.assertIn("failed_version_reused_without_revision", check.evaluate(request)["blocked"])

    def test_close_issue_rechecks_newer_user_goal(self):
        request = self.close_case()
        WorkspaceBoundaryChecks().add_user_evidence(
            request, "codex:user:new-goal", "goal", "Newly corrected framework outcome")
        self.assertIn("newer_goal_requires_scope_review", check.evaluate(request)["missing"])

    def test_closure_requires_actual_links_tested_adoption_and_effect(self):
        request = self.close_case()
        self.assertEqual(check.evaluate(request)["status"], "ready")
        request["context"]["evidence"][8]["result"] = "unknown"
        self.assertIn("observed_effect", check.evaluate(request)["missing"])
        request["context"]["evidence"][8]["result"] = "observed"
        request["issue"]["adoption"]["version"] = "commit:older"
        self.assertIn("adopted_tested_version", check.evaluate(request)["missing"])
        request["issue"]["adoption"]["version"] = "commit:abc"
        request["context"]["evidence"][4]["result"] = "failed"
        self.assertIn("regression_passed", check.evaluate(request)["missing"])
        request["context"]["evidence"][4]["result"] = "passed"
        request["context"]["evidence"][4]["project_id"] = "trading"
        self.assertIn("unresolved_regression_original_reference", check.evaluate(request)["missing"])

    def test_full_log_copy_is_rejected(self):
        request = self.close_case()
        request["context"]["evidence"][0]["body"] = "full private transcript"
        with self.assertRaises(check.CheckError):
            check.evaluate(request)

    def test_synthetic_result_cannot_replace_local_runtime_recovery(self):
        request = self.close_case()
        request["issue"]["effect"] = {"ref": "node:test:adjacent", "result": "observed"}
        self.assertIn("observed_effect", check.evaluate(request)["missing"])
        request = self.close_case()
        local_result = next(record for record in request["context"]["evidence"]
                            if record["kind"] == "local_result")
        local_result["result"] = "failed"
        self.assertIn("local_verification_results", check.evaluate(request)["missing"])

    def test_unfinished_local_incident_retains_followup_owner(self):
        request = self.close_case()
        request["issue"].pop("followup_owner")
        self.assertIn("completion_responsibility", check.evaluate(request)["missing"])

    def test_cloud_ci_cannot_close_incident_without_local_adoption_and_effect(self):
        request = self.close_case()
        request["context"]["incident"] = {
            "goal_ref": request["issue"]["goal_ref"], "resource": request["issue"]["resource"],
            "responsible_node": "mac-mini", "data_node": "mac-mini",
            "destination_nodes": ["mac-mini"]}
        result = check.evaluate(request)
        self.assertIn("local_adoption_at_responsible_node", result["missing"])
        self.assertIn("local_runtime_effect_at_responsible_node", result["missing"])
        for record in request["context"]["evidence"]:
            if record["kind"] in ("adoption", "effect"):
                record["node_id"] = "assistant-cloud"
        self.assertIn("local_runtime_effect_at_responsible_node", check.evaluate(request)["missing"])
        for record in request["context"]["evidence"]:
            if record["kind"] in ("adoption", "effect"):
                record["node_id"] = "mac-mini"
        self.assertEqual(check.evaluate(request)["status"], "ready")
        request["issue"]["goal_ref"] = "codex:user:different-goal"
        self.assertIn("incident_goal_or_resource_drift", check.evaluate(request)["blocked"])

    def test_smoke_pass_and_unverified_native_result_cannot_close_goal(self):
        request = self.close_case()
        request["issue"].pop("acceptance_ref")
        self.assertIn("goal_acceptance_beyond_smoke_test", check.evaluate(request)["missing"])
        request = self.close_case()
        request["context"]["native_sources"][0]["turns"][0]["items"][4]["exitCode"] = 1
        self.assertIn("unverified_regression_original_source", check.evaluate(request)["missing"])
        request = self.close_case()
        request["context"]["native_sources"][0]["turns"][0]["items"][8]["output"]["truncated"] = True
        self.assertIn("unverified_effect_source", check.evaluate(request)["missing"])

    def test_local_verification_is_required_before_continuous_and_close(self):
        request = self.close_case()
        local = request["issue"].pop("local_verification")
        self.assertIn("local_verification_plan", check.evaluate(request)["missing"])
        request["issue"]["local_verification"] = local
        request["phase"] = "resume"
        request["resume"] = {"resource": request["issue"]["resource"],
                             "goal_ref": request["issue"]["goal_ref"],
                             "stage": "continuous", "version": "commit:abc"}
        self.assertIn("local_verification_plan", check.evaluate(request)["missing"])
        request["resume"]["local_verification"] = local
        self.assertEqual(check.evaluate(request)["status"], "ready")
        self.assertTrue(check.evaluate(request)["continuous_eligible"])
        request["resume"]["stage"] = "work"
        self.assertFalse(check.evaluate(request)["continuous_eligible"])
        request["resume"]["stage"] = "continuous"
        request["phase"] = "verify_local"
        request["local_verification"] = {**local, "resource": request["issue"]["resource"],
                                         "version": "commit:abc"}
        self.assertEqual(check.evaluate(request)["status"], "ready")
        self.assertTrue(check.evaluate(request)["continuous_eligible"])

    def test_local_output_and_plan_must_be_real_and_match(self):
        request = self.close_case()
        records = request["context"]["evidence"]
        result = next(record for record in records if record["kind"] == "local_result")
        local = request["issue"]["local_verification"]
        items = request["context"]["native_sources"][0]["turns"][0]["items"]
        actual = next(item for item in items if item["id"] == result["ref"])
        actual["output"]["text"] = "wrong output"
        self.assertIn("unverified_local_result_source", check.evaluate(request)["missing"])
        actual["output"]["text"] = "local output"
        actual["status"] = "failed"
        self.assertIn("unverified_local_result_source", check.evaluate(request)["missing"])
        actual["status"] = "completed"
        result["digest"] = hashlib.sha256(b"wrong output").hexdigest()
        self.assertIn("unverified_local_result_source", check.evaluate(request)["missing"])
        result["digest"] = hashlib.sha256(b"local output").hexdigest()
        result["version"] = "commit:older"
        self.assertIn("local_verification_results", check.evaluate(request)["missing"])
        result["version"] = "commit:abc"
        result["result"] = "unknown"
        self.assertIn("local_verification_results", check.evaluate(request)["missing"])
        result["result"] = "passed"
        local["cases"][0]["id"] = "invented"
        self.assertIn("local_case_not_fixed_in_plan", check.evaluate(request)["missing"])
        local["cases"][0]["id"] = "original"
        items[-3], items[-1] = items[-1], items[-3]
        self.assertIn("local_plan_not_before_result", check.evaluate(request)["missing"])
        items[-3], items[-1] = items[-1], items[-3]
        local["cases"] = []
        self.assertIn("local_verification_cases", check.evaluate(request)["missing"])


if __name__ == "__main__":
    unittest.main()
