"""Scenario checks for scoped failures and task progress decisions."""

import copy
import hashlib
import json
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "scripts"),
                str(Path(__file__).resolve().parents[1] / "tests")]
import execution_check as check
import test_execution_check as fixtures


def bind_dependency(request, producer_id, consumer_id, relation="required_input"):
    """An acquired, native-shaped synthetic consumer contract, not real proof."""
    steps = {s["id"]: s for s in request["progress"]["steps"]}
    producer, consumer = steps[producer_id], steps[consumer_id]
    ref = "codex:dependency:" + producer_id + ":" + consumer_id
    reason = "This consumer requires the producer's exact input or shared effect"
    body = json.dumps({"producer": {"step_id": producer_id, "action": producer["action"]},
        "consumer": {"step_id": consumer_id, "action": consumer["action"]},
        "requires": {"kind": relation, "resource": producer["action"]["resource"], "reason": reason}})
    record = {"ref": ref, "kind": "dependency", "source": "codex", "project_id": request["project"]["id"],
        "task_ref": request["task"]["native_ref"], "native_item_id": ref,
        "resource": copy.deepcopy(consumer["action"]["resource"]), "result": "observed",
        "digest": hashlib.sha256(body.encode()).hexdigest()}
    request["context"]["evidence"].append(record)
    request["context"]["native_sources"][0]["turns"][0]["items"].append({
        "id": ref, "type": "commandExecution", "status": "completed", "exitCode": 0,
        "command": "read consumer contract", "output": {"text": body, "truncated": False}})
    basis = {"status": "proven", "source_ref": ref, "reason": reason}
    consumer["depends_on_basis"] = {**consumer.get("depends_on_basis", {}), producer_id: basis}
    return record


class ProgressChecks(unittest.TestCase):
    def test_five_synthetic_restarts_keep_new_goal_and_scoped_work(self):
        for restart in range(1, 6):
            with self.subTest(restart=restart):
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
                progress = self.progress(request)
                progress["progress"]["checkpoint"] = "handoff"
                result = check.evaluate(progress)
                self.assertEqual({step["id"]: step["state"] for step in result["steps"]},
                                 {"restore": "direct_refusal", "verify": "dependency_blocked",
                                  "diagnose": "ready"})

    def test_after_failure_cannot_claim_working_without_remaining_steps(self):
        request = self.progress(self.base())
        request["progress"]["steps"] = []
        self.assertIn("remaining_steps_unlisted", check.evaluate(request)["missing"])

    def test_handoff_rechecks_selected_goal_after_new_direct_message(self):
        request = self.base()
        helper = fixtures.WorkspaceBoundaryChecks()
        helper.add_user_evidence(request, "codex:user:old-goal", "goal", "Old scheduling goal")
        helper.add_user_evidence(request, "codex:user:new-goal", "goal", "New framework goal")
        request = self.progress(request)
        request["progress"]["checkpoint"] = "handoff"
        request["resume"] = {"goal_ref": "codex:user:old-goal"}
        self.assertIn("newer_goal_requires_scope_review", check.evaluate(request)["missing"])

    def test_completed_requires_matching_goal_version_independent_review_and_scope(self):
        request = fixtures.ClosureChecks().close_case()
        request["phase"] = "progress"
        request["progress"] = {"checkpoint": "handoff", "claim": "completed", "steps": [],
                               "goal_ref": request["issue"]["goal_ref"],
                               "version": "commit:abc", "acceptance_ref": request["issue"]["acceptance_ref"],
                               "resource": request["issue"]["resource"],
                               "independent_verification": request["issue"]["independent_verification"]}
        ContinuationChecks().review(request, goal=request["issue"]["goal_ref"])
        self.assertEqual(check.evaluate(request)["status"], "ready")
        request["progress"].pop("independent_verification")
        request.pop("issue")
        self.assertIn("independent_verification_not_run", check.evaluate(request)["missing"])
        request["progress"]["version"] = "commit:older"
        self.assertIn("goal_acceptance_unverified", check.evaluate(request)["missing"])
        request["progress"]["resource"] = {"kind": "repo", "id": "another-project"}
        self.assertIn("progress_outside_scope", check.evaluate(request)["blocked"])

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
        result = {**request, "phase": "progress", "progress": {
            "checkpoint": "after_failure", "claim": claim,
            "steps": [
                {"id": "restore", "action": restore, "depends_on": []},
                {"id": "verify", "action": verify, "depends_on": ["restore"]},
                {"id": "diagnose", "action": diagnose, "depends_on": []}]}}
        bind_dependency(result, "restore", "verify")
        return result

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
        self.assertIn("remaining_goal_review", result["missing"])
        self.assertFalse(result["wait_allowed"])
        ContinuationChecks().review(request)
        result = check.evaluate(request)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["wait_allowed"])
        request["context"]["evidence"] = [r for r in request["context"]["evidence"]
                                             if r.get("kind") != "failure"]
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



class ContinuationChecks(unittest.TestCase):
    def add(self, request, ref, kind, text, *, thread=None, item_type="agentMessage", **extra):
        import hashlib
        thread = thread or request["task"]["native_ref"]
        record = {"ref": ref, "source": "codex", "kind": kind, "project_id": "framework",
                  "task_ref": thread, "native_item_id": ref,
                  "digest": hashlib.sha256(text.encode()).hexdigest(), **extra}
        request["context"].setdefault("evidence", []).append(record)
        item = {"id": ref, "type": item_type}
        if item_type in ("agentMessage", "userMessage"):
            item["text"] = text
        else:
            item["output"] = {"text": text, "truncated": False}
        sources = request["context"].setdefault("native_sources", [])
        snapshot = next((x for x in sources if x["thread"]["id"] == thread.removeprefix("codex:thread:")), None)
        if snapshot is None:
            snapshot = {"schemaVersion": 1, "thread": {"id": thread.removeprefix("codex:thread:")},
                        "turns": [{"items": []}]}
            sources.append(snapshot)
        snapshot["turns"][0]["items"].append(item)
        return record, item

    def review(self, request, goal=None):
        import json
        goal = goal or "codex:user:whole-goal"
        if not any(record["ref"] == goal for record in request["context"].get("evidence", [])):
            self.add(request, goal, "goal", "Continue the whole authorized goal", item_type="userMessage")
        request["progress"]["goal_ref"] = goal
        projection = {"goal_ref": goal, "step_ids": [s["id"] for s in request["progress"]["steps"]]}
        if request["progress"]["claim"] == "completed":
            projection.update(version=request["progress"].get("version"), resource=request["progress"].get("resource"))
        self.add(request, "codex:review:remaining", "goal_review", json.dumps(projection))
        request["progress"]["continuation"] = {"review_ref": "codex:review:remaining"}

    def base(self):
        request = ProgressChecks().progress(ProgressChecks().base())
        request["context"]["tool_failures"] = []
        request["progress"]["steps"] = request["progress"]["steps"][2:]
        request["progress"]["checkpoint"] = "stage_end"
        self.review(request)
        return request

    def selected(self):
        request = self.base()
        request["progress"]["continuation"]["selected_step_id"] = "diagnose"
        return request

    def handoff(self, stage="suggested"):
        import json
        request = self.selected()
        target, version = "codex:thread:review-task", "commit:fixed-2"
        h = {"target_task_ref": target, "version": version, "claim": stage,
             "authorization_ref": "codex:user:message", "sent_ref": "codex:send:one",
             "received_ref": "codex:receive:one"}
        request["progress"]["continuation"]["handoff"] = h
        self.add(request, h["authorization_ref"], "authorization", "Send this scope to the existing review task",
                 item_type="userMessage", result="granted",
                 resource=request["progress"]["steps"][0]["action"]["resource"],
                 workspace={"target_task_refs": [target]})
        _, sent = self.add(request, h["sent_ref"], "handoff_sent", json.dumps({"threadId": "review-task"}),
                           item_type="functionCallOutput", version=version)
        sent["name"] = "send_message_to_thread"
        sender = request["task"]["native_ref"].removeprefix("codex:thread:")
        self.add(request, h["received_ref"], "handoff_received",
                 "<codex_delegation><source_thread_id>"+sender+"</source_thread_id><input>verify "+version+
                 "</input></codex_delegation>", thread=target, item_type="userMessage", version=version)
        return request

    def test_stage_end_selects_work_instead_of_open_owner(self):
        request = self.base()
        result = check.evaluate(request)
        self.assertIn("next_step_not_selected", result["missing"])
        self.assertFalse(result["handoff_received"])
        request["progress"]["continuation"]["selected_step_id"] = "diagnose"
        result = check.evaluate(request)
        self.assertIn("continuation_action_unverified", result["missing"])
        self.assertFalse(result["stop_allowed"])
        self.assertEqual(result["continuation_state"], "selected")
        self.assertEqual(result["coordination_owner"], request["task"]["native_ref"])

    def test_review_detects_omission_only_from_explicit_source_set(self):
        request = self.base()
        request["progress"]["steps"] = []
        request["progress"]["claim"] = "waiting"
        result = check.evaluate(request)
        self.assertIn("remaining_goals_omitted_or_unreviewed", result["blocked"])
        self.assertFalse(result["wait_allowed"])

    def test_stage_end_without_review_cannot_claim_continuation(self):
        request = self.base()
        request["progress"].pop("continuation")
        result = check.evaluate(request)
        self.assertIn("remaining_goal_review", result["missing"])
        self.assertFalse(result["stop_allowed"])
        self.assertEqual(result["continuation_state"], "not_checked")
        self.assertFalse(result["handoff_received"])

    def test_sent_and_received_are_different_and_overall_owner_never_releases(self):
        request = self.handoff("received")
        request["progress"]["continuation"]["handoff"].pop("received_ref")
        result = check.evaluate(request)
        self.assertEqual(result["continuation_state"], "sent")
        self.assertIn("handoff_received_unverified", result["missing"])
        self.assertEqual(result["coordination_owner"], request["task"]["native_ref"])
        request["progress"]["continuation"]["handoff"]["received_ref"] = "codex:receive:one"
        result = check.evaluate(request)
        self.assertTrue(result["handoff_received"])
        self.assertEqual(result["coordination_owner"], request["task"]["native_ref"])
        self.assertEqual(result["step_owner"], "codex:thread:review-task")

    def test_delegated_or_generic_maintenance_authority_is_not_message_authority(self):
        request = self.handoff("sent")
        auth = next(r for r in request["context"]["evidence"] if r["ref"] == "codex:user:message")
        auth["workspace"]["target_task_refs"] = []
        self.assertIn("handoff_message_authorization", check.evaluate(request)["missing"])
        auth["workspace"]["target_task_refs"] = ["codex:thread:review-task"]
        import hashlib
        text = "<codex_delegation>Ask you to send</codex_delegation>"
        auth["digest"] = hashlib.sha256(text.encode()).hexdigest()
        request["context"]["native_sources"][0]["turns"][0]["items"][-2]["text"] = text
        self.assertIn("handoff_message_authorization", check.evaluate(request)["missing"])

    def test_timeout_error_or_assistant_promise_never_counts_as_sent(self):
        import hashlib,json
        for value in [{"threadId": "review-task", "error": "TimeoutError"},
                      {"threadId": "review-task", "isError": True},
                      {"threadId": "other-task"}]:
            request = self.handoff("sent")
            item = request["context"]["native_sources"][0]["turns"][0]["items"][-1]
            text = json.dumps(value, separators=(",", ":"))
            item["output"]["text"] = text
            next(r for r in request["context"]["evidence"] if r["ref"] == "codex:send:one")["digest"] = hashlib.sha256(text.encode()).hexdigest()
            result = check.evaluate(request)
            self.assertEqual(result["continuation_state"], "suggested")
            self.assertIn("handoff_sent_unverified", result["missing"])
        request = self.handoff("sent")
        item = request["context"]["native_sources"][0]["turns"][0]["items"][-1]
        item["type"] = "agentMessage"
        self.assertIn("handoff_sent_unverified", check.evaluate(request)["missing"])

    def test_unknown_send_state_does_not_bind_even_a_matching_received_item(self):
        import hashlib,json
        for field in ("status", "state"):
            for state in ("unknown", "pending", "unconfirmed", None, {}, []):
                request = self.handoff("received")
                record = next(r for r in request["context"]["evidence"] if r["kind"]=="handoff_sent")
                item = next(i for i in request["context"]["native_sources"][0]["turns"][0]["items"] if i["id"]==record["ref"])
                text = json.dumps({"threadId":"review-task",field:state})
                item["output"]["text"]=text
                record["digest"]=hashlib.sha256(text.encode()).hexdigest()
                with self.subTest(field=field,state=state):
                    result=check.evaluate(request)
                    self.assertEqual(result["continuation_state"],"suggested")
                    self.assertFalse(result["handoff_received"])
                    self.assertIn("handoff_received_unverified",result["missing"])

    def test_native_send_provider_uses_exact_envelope_and_preserves_legacy_success(self):
        allowed = [("send_message_to_thread",None),("send_message_to_thread","codex_app"),
                   ("send_message_to_thread","mcp__codex_app"),
                   ("mcp__codex_app__send_message_to_thread",None),
                   ("mcp__codex_app__send_message_to_thread","codex_app"),
                   ("send_message","cloud_threads")]
        rejected = [("send_message_to_thread","shell_echo"),("send_message_to_thread","unrelated_service"),
                    ("mcp__unrelated_service__send_message_to_thread",None),
                    ("mcp__codex_app__send_message_to_thread","unrelated_service"),
                    ("send_message","codex_app"),("send_message_to_thread",[])]
        for accepted, pairs in ((True,allowed),(False,rejected)):
            for name,namespace in pairs:
                request=self.handoff("sent")
                request["progress"]["continuation"]["handoff"].pop("received_ref")
                item=request["context"]["native_sources"][0]["turns"][0]["items"][-1]
                item["name"]=name
                if namespace is None:item.pop("namespace",None)
                else:item["namespace"]=namespace
                with self.subTest(name=name,namespace=namespace):
                    result=check.evaluate(request)
                    self.assertEqual(result["status"],"needs_evidence")
                    self.assertFalse(result["stop_allowed"])
                    self.assertEqual(result["continuation_state"],"sent" if accepted else "suggested")

    def test_receipt_schema_strict_boolean_types_in_every_supported_channel(self):
        import json
        native={"type":"functionCallOutput","name":"send_message_to_thread","output":{}}
        for flag,good in (("success",True),("isError",False)):
            for value in (True,False,None,0,1,"true","false","unknown",{},[]):
                for channel in ("legacy","structured","text"):
                    leaf={"threadId":"review-task",flag:value}
                    payload=(leaf if channel=="legacy" else {"isError":False,"structuredContent":leaf}
                             if channel=="structured" else {"isError":False,"content":[{"type":"text","text":json.dumps(leaf)}]})
                    expected=type(value) is bool and value is good
                    with self.subTest(flag=flag,value=value,channel=channel):
                        self.assertEqual(check.message_receipt_matches(native,json.dumps(payload),"review-task"),expected)

    def test_receipt_schema_requires_target_and_consistent_known_envelopes(self):
        import json
        target={"threadId":"review-task"}
        valid=[target,{**target,"success":True,"isError":False,"error":None,"errors":[]},
               {**target,"status":"queued","state":"accepted"},
               {"isError":False,"content":[{"type":"text","text":json.dumps(target)}]},
               {"isError":False,"structuredContent":target,"content":[{"type":"text","text":"Action completed."}]},
               {"structuredContent":target,"content":[{"type":"text","text":json.dumps(target)}]},
               {"threadId":"review-task","thread_id":"review-task"}]
        invalid=[{},None,[],True,{"success":True},{"isError":False,"content":[]},
                 {**target,"thread_id":"other-task"},{**target,"success":True,"isError":True},
                 {**target,"success":False,"isError":False},{**target,"status":"unknown"},
                 {**target,"error":False},{**target,"errors":{}},{"threadId":None},
                 {"threadId":[]},{"data":target},{**target,"ok":False},
                 {"structuredContent":target,"isError":1},
                 {"structuredContent":target,"_meta":{"transport":{"success":"unknown"}}},
                 {"structuredContent":target,"content":[{"type":"text","text":json.dumps({"success":False})}]},
                 {"structuredContent":target,"content":[{"type":"text","text":json.dumps({"errors":["failed"]})}]},
                 {"structuredContent":target,"content":[{"type":"text","text":json.dumps({"threadId":"other-task"})}]},
                 {"structuredContent":target,"content":{"type":"text","text":"unknown"}},
                 {"structuredContent":target,"content":[{"type":"image","text":"unknown"}]},
                 {"structuredContent":target,"content":[{"type":"text","text":'{"success":false,"success":true}'}]},
                 {"structuredContent":target,"content":[{"type":"text","text":'{"success":'}]}]
        native={"type":"functionCallOutput","name":"send_message_to_thread","output":{}}
        for expected,values in ((True,valid),(False,invalid)):
            for payload in values:
                with self.subTest(payload=payload):
                    self.assertEqual(check.message_receipt_matches(native,json.dumps(payload),"review-task"),expected)
        self.assertFalse(check.message_receipt_matches(native,'{"threadId":"review-task","success":false,"success":true}',"review-task"))
        wrapped=target
        for _ in range(10):wrapped={"structuredContent":wrapped}
        self.assertFalse(check.message_receipt_matches(native,json.dumps(wrapped),"review-task"))
        for value in (1,"false",None,True):
            native["output"]["isError"]=value
            self.assertFalse(check.message_receipt_matches(native,json.dumps(target),"review-task"))

    def test_invalid_receipt_schema_cannot_bind_receiver_through_normal_cli(self):
        import contextlib,hashlib,io,json
        import codex_framework as framework
        values=[{"threadId":"review-task","success":"unknown"},
                {"threadId":"review-task","isError":1},{"threadId":"review-task","success":None},
                {"threadId":"review-task","success":True,"isError":True},
                {"structuredContent":{"threadId":"review-task"},"isError":False,
                 "content":[{"type":"text","text":'{"success":false}'}]},
                {"data":{"threadId":"review-task"},"success":True}]
        for payload in values:
            request=self.handoff("received")
            record=next(r for r in request["context"]["evidence"] if r["kind"]=="handoff_sent")
            item=next(i for i in request["context"]["native_sources"][0]["turns"][0]["items"] if i["id"]==record["ref"])
            text=json.dumps(payload);record["digest"]=hashlib.sha256(text.encode()).hexdigest();item["output"]["text"]=text
            output=io.StringIO()
            with self.subTest(payload=payload),contextlib.redirect_stdout(output),unittest.mock.patch("sys.stdin",io.StringIO(json.dumps(request))):
                self.assertEqual(framework.main(["check"]),2)
            result=json.loads(output.getvalue())
            self.assertEqual(result["continuation_state"],"suggested")
            self.assertFalse(result["handoff_received"])
            self.assertEqual(result["step_owner"],request["task"]["native_ref"])

    def test_receipt_requires_exact_version_and_sender(self):
        import hashlib
        for text in ["<codex_delegation><source_thread_id>framework-task</source_thread_id>commit:fixed-20</codex_delegation>",
                     "<codex_delegation><source_thread_id>other-task</source_thread_id>commit:fixed-2</codex_delegation>"]:
            request = self.handoff("received")
            request["context"]["native_sources"][-1]["turns"][0]["items"][0]["text"] = text
            request["context"]["evidence"][-1]["digest"] = hashlib.sha256(text.encode()).hexdigest()
            self.assertIn("handoff_received_unverified", check.evaluate(request)["missing"])

    def test_target_native_delivery_is_accepted_but_arbitrary_tool_echo_is_not(self):
        request = self.handoff("received")
        item = request["context"]["native_sources"][-1]["turns"][0]["items"][0]
        text = item.pop("text")
        item.update(type="functionCallOutput", name="send_message", namespace="cloud_threads",
                    output={"text":text,"truncated":False})
        self.assertTrue(check.evaluate(request)["handoff_received"])
        item["namespace"] = "shell_echo"
        self.assertFalse(check.evaluate(request)["handoff_received"])

    def test_malformed_continuation_metadata_returns_structured_input_error(self):
        import contextlib,io,json
        import codex_framework as framework
        variants = ["continuation", "handoff", "authority_workspace", "authority_targets", "projection_ids"]
        for variant in variants:
            request = self.handoff("received")
            c = request["progress"]["continuation"]
            if variant == "continuation":
                request["progress"]["continuation"] = []
            elif variant == "handoff":
                c["handoff"] = []
            elif variant == "authority_workspace":
                next(r for r in request["context"]["evidence"] if r["ref"]=="codex:user:message")["workspace"] = []
            elif variant == "authority_targets":
                next(r for r in request["context"]["evidence"] if r["ref"]=="codex:user:message")["workspace"]["target_task_refs"] = "codex:thread:review-task"
            else:
                import hashlib
                text=json.dumps({"goal_ref":"codex:user:whole-goal","step_ids":"diagnose"})
                record=next(r for r in request["context"]["evidence"] if r["kind"]=="goal_review")
                record["digest"]=hashlib.sha256(text.encode()).hexdigest()
                item=next(i for i in request["context"]["native_sources"][0]["turns"][0]["items"] if i["id"]==record["ref"])
                item["text"]=text
            with self.subTest(variant=variant):
                output=io.StringIO()
                with contextlib.redirect_stdout(output), unittest.mock.patch("sys.stdin",io.StringIO(json.dumps(request))):
                    self.assertEqual(framework.main(["check"]),1)
                self.assertEqual(json.loads(output.getvalue())["status"],"invalid")

    def test_result_return_is_not_global_completion_and_retains_coordinator(self):
        request = self.handoff("returned")
        h = request["progress"]["continuation"]["handoff"]
        h["result_ref"] = "codex:result:one"
        _, item = self.add(request, h["result_ref"], "handoff_result", "failed "+h["version"],
                           thread=h["target_task_ref"], version=h["version"])
        item["phase"] = "final_answer"
        result = check.evaluate(request)
        self.assertEqual(result["continuation_state"], "returned")
        self.assertFalse(result["continuous_eligible"])
        self.assertEqual(result["coordination_owner"], request["task"]["native_ref"])

    def test_cancelled_action_stops_without_blocking_independent_work(self):
        request = self.base()
        action = request["progress"]["steps"][0]["action"]
        request["context"]["tool_failures"] = [{"kind": "user_cancelled", "resource": copy.deepcopy(action["resource"]),
            "action_kind": "read", "side_effect": "read"}]
        request["progress"]["continuation"]["selected_step_id"] = "diagnose"
        result = check.evaluate(request)
        self.assertIn("user_cancelled_action", result["steps"][0]["blocked"])
        self.assertIn("selected_step_not_executable", result["blocked"])
        action["resource"]["path"] = "scripts/independent.py"
        self.assertEqual(check.evaluate(request)["work_partition"]["ready_step_ids"], ["diagnose"])

    def test_in_flight_same_effect_is_not_dispatched_again(self):
        request = self.selected()
        action = request["progress"]["steps"][0]["action"]
        action.update(kind="write",effect_key="same-effect")
        action["workspace"].update(side_effect="local_change",authorization_ref="codex:user:local-maintenance")
        request["context"]["node_snapshot_observed_at"] = datetime.now(timezone.utc).isoformat()
        request["context"]["node_snapshot_max_age_seconds"] = 900
        request["context"]["native_operations"] = [{"ref":"codex:work:running","project_id":"framework",
            "task_ref":request["task"]["native_ref"],"resource":action["resource"],"effect_key":"same-effect","state":"running"}]
        result = check.evaluate(request)
        self.assertIn("duplicate_or_uncertain_effect",result["steps"][0]["blocked"])
        self.assertIn("selected_step_not_executable",result["blocked"])



class TaskBoundaryChecks(unittest.TestCase):
    """Supplied relationship contracts; no real diagnosis, dispatch or resource work."""

    def mixed(self):
        request = fixtures.action_case()
        request['project']['scopes'].append({'kind': 'service', 'id': 'docker', 'access': 'write'})
        request['task']['scopes'] = copy.deepcopy(request['project']['scopes'])
        request['context']['node_snapshot_observed_at'] = datetime.now(timezone.utc).isoformat()
        request['context']['node_snapshot_max_age_seconds'] = 900

        def action(kind, resource, effect, capabilities):
            result = {'kind': kind, 'resource': resource, 'capabilities': capabilities}
            if kind != 'read':
                result['effect_key'] = effect
            return result

        code = action('write', {'kind': 'repo', 'id': 'framework', 'path': 'scripts/core.py'}, 'code', ['python'])
        offline = action('invoke', {'kind': 'repo', 'id': 'framework', 'path': 'tests/offline.py'}, 'offline', ['python'])
        live = action('invoke', {'kind': 'service', 'id': 'docker', 'path': 'mysql'}, 'live', ['docker'])
        adoption = action('read', live['resource'], None, [])
        request.update(phase='progress', progress={'checkpoint': 'after_failure', 'claim': 'working', 'steps': [
            {'id': 'code', 'action': code}, {'id': 'offline', 'action': offline},
            {'id': 'live', 'action': live}, {'id': 'adoption', 'action': adoption, 'depends_on': ['code', 'live']}]})
        # This consumer really needs the live observation. Supply that
        # contract instead of relying on an unsupported dependency label.
        ContinuationChecks().review(request)
        bind_dependency(request, 'code', 'adoption')
        bind_dependency(request, 'live', 'adoption')
        request['progress']['continuation']['selected_step_id'] = 'code'
        return request

    def assert_partition(self, result, ready, blocked, unresolved):
        self.assertEqual(result['work_partition'], {'ready_step_ids': ready,
            'blocked_step_ids': blocked, 'unresolved_step_ids': unresolved})
        self.assertEqual(set(ready + blocked + unresolved), {s['id'] for s in result['steps']})
        self.assertEqual(len(ready + blocked + unresolved), len(result['steps']))

    def test_docker_offline_and_oom_only_hold_dependent_field_work(self):
        for exhausted in (False, True):
            with self.subTest(exhausted=exhausted):
                request = self.mixed()
                if exhausted:
                    request['context']['tool_failures'] = [{'kind': 'resource_exhausted',
                        'ref': 'codex:tool:oom', 'resource': request['progress']['steps'][2]['action']['resource'],
                        'action_kind': 'invoke'}]
                result = check.evaluate(request)
                self.assertEqual(result['status'], 'needs_evidence')
                self.assert_partition(result, ['code', 'offline'], ['adoption'], ['live'])
                self.assertNotIn('selected_step_not_executable', result['blocked'])
                request['progress']['claim'] = 'waiting'
                result = check.evaluate(request)
                self.assertIn('independent_work_remaining', result['blocked'])
                self.assertFalse(result['wait_allowed'])

    def test_readonly_diagnosis_cannot_write_invoke_or_touch_other_task_scope(self):
        request = fixtures.action_case()
        request['task']['scopes'][0]['access'] = 'read'
        request['action']['kind'] = 'read'
        self.assertEqual(check.evaluate(request)['status'], 'ready')
        for kind in ('write', 'invoke', 'repair', 'publish'):
            with self.subTest(kind=kind):
                request['action']['kind'] = kind
                self.assertIn('outside_task_scope', check.evaluate(request)['blocked'])
        request['action']['kind'] = 'read'
        request['action']['resource']['path'] = 'other-task/config.py'
        self.assertIn('outside_task_scope', check.evaluate(request)['blocked'])

    def test_unrelated_new_issue_does_not_interrupt_original_work(self):
        request = self.mixed()
        request['context']['tool_failures'] = [{'kind': 'platform_denied', 'ref': 'codex:other:denied',
            'resource': {'kind': 'repo', 'id': 'other-project'}, 'action_kind': 'write'}]
        result = check.evaluate(request)
        self.assert_partition(result, ['code', 'offline'], ['adoption'], ['live'])
        request['progress']['steps'][0]['action']['resource']['id'] = 'other-project'
        self.assertIn('outside_project_scope', check.evaluate(request)['steps'][0]['blocked'])

    def test_scoped_pause_and_explicit_project_stop_have_different_coverage(self):
        request = self.mixed()
        request['context']['tool_failures'] = [{'kind': 'user_cancelled', 'ref': 'codex:user:stop',
            'resource': copy.deepcopy(request['progress']['steps'][0]['action']['resource'])}]
        ContinuationChecks().add(request, 'codex:user:stop', 'goal', 'Pause only the code action', item_type='userMessage')
        result = check.evaluate(request)
        self.assert_partition(result, ['offline'], ['code', 'adoption'], ['live'])
        request['context']['tool_failures'][0]['resource'] = {'kind': 'repo', 'id': 'framework'}
        request['context']['tool_failures'].append({'kind': 'user_cancelled', 'ref': 'codex:user:stop',
            'resource': {'kind': 'service', 'id': 'docker'}})
        import hashlib
        record = next(r for r in request['context']['evidence'] if r['ref'] == 'codex:user:stop')
        item = next(i for i in request['context']['native_sources'][0]['turns'][0]['items'] if i['id'] == 'codex:user:stop')
        item['text'] = 'Stop the entire project, including its repository and service actions'
        record['digest'] = hashlib.sha256(item['text'].encode()).hexdigest()
        result = check.evaluate(request)
        self.assert_partition(result, [], ['code', 'offline', 'live', 'adoption'], [])
        self.assertIn('user_cancelled_action', result['steps'][1]['blocked'])
        self.assertNotEqual(result['status'], 'ready')
        self.assertFalse(result['continuous_eligible'])

    def test_shared_conflict_isolates_actual_file_and_its_dependents(self):
        request = self.mixed()
        action = request['progress']['steps'][0]['action']
        request['context']['native_operations'] = [{'ref': 'codex:other:inflight', 'project_id': 'other',
            'task_ref': 'codex:thread:other', 'resource': copy.deepcopy(action['resource']),
            'effect_key': 'other-change', 'state': 'running'}]
        result = check.evaluate(request)
        self.assert_partition(result, ['offline'], ['code', 'adoption'], ['live'])
        request['context']['native_operations'][0]['resource']['path'] = 'scripts/other.py'
        self.assert_partition(check.evaluate(request), ['code', 'offline'], ['adoption'], ['live'])

    def test_new_user_correction_restores_legal_branches_without_clearing_denial(self):
        import hashlib
        import json
        request = self.mixed()
        request['context']['tool_failures'] = [
            {'kind': 'user_cancelled', 'ref': 'codex:user:old-stop', 'resource': {'kind': 'repo', 'id': 'framework'}},
            {'kind': 'platform_denied', 'ref': 'codex:tool:live-denied',
             'resource': {'kind': 'service', 'id': 'docker'}, 'action_kind': 'invoke'}]
        ContinuationChecks().add(request, 'codex:user:resume', 'goal',
            'Resume framework code and offline validation; the service refusal remains', item_type='userMessage')
        self.assertIn('newer_goal_requires_scope_review', check.evaluate(request)['missing'])
        request['progress']['goal_ref'] = 'codex:user:resume'
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'goal_review')
        text = json.dumps({'goal_ref': request['progress']['goal_ref'],
                           'step_ids': [s['id'] for s in request['progress']['steps']]})
        record['digest'] = hashlib.sha256(text.encode()).hexdigest()
        source = request['context']['native_sources'][0]['turns'][0]['items']
        next(i for i in source if i['id'] == record['native_item_id'])['text'] = text
        request['context']['tool_failures'] = request['context']['tool_failures'][1:]
        result = check.evaluate(request)
        self.assert_partition(result, ['code', 'offline'], ['live', 'adoption'], [])
        self.assertIn('platform_denied_action', result['steps'][2]['blocked'])

    def test_late_diagnosis_returns_to_original_owner_and_does_not_complete_goal(self):
        request = ContinuationChecks().handoff('returned')
        h = request['progress']['continuation']['handoff']
        h['result_ref'] = 'codex:result:diagnosis'
        record, item = ContinuationChecks().add(request, h['result_ref'], 'handoff_result',
            'bounded diagnosis ' + h['version'], thread=h['target_task_ref'], version=h['version'])
        item['phase'] = 'final_answer'
        result = check.evaluate(request)
        self.assertEqual(result['continuation_state'], 'returned')
        self.assertEqual(result['coordination_owner'], request['task']['native_ref'])
        self.assert_partition(result, ['diagnose'], [], [])
        request['progress']['claim'] = 'completed'
        self.assertIn('remaining_steps_not_complete', check.evaluate(request)['blocked'])
        record['version'] = 'commit:old'
        self.assertIn('handoff_returned_unverified', check.evaluate(request)['missing'])

    def test_unavailable_channel_and_missing_send_authority_preserve_independent_work(self):
        request = ContinuationChecks().handoff('received')
        h = request['progress']['continuation']['handoff']
        request['context']['evidence'] = [r for r in request['context']['evidence'] if r['ref'] != h['authorization_ref']]
        sent = next(i for i in request['context']['native_sources'][0]['turns'][0]['items'] if i['id'] == h['sent_ref'])
        sent['output']['text'] = '{"threadId":"review-task","status":"unknown"}'
        record = next(r for r in request['context']['evidence'] if r['ref'] == h['sent_ref'])
        import hashlib
        record['digest'] = hashlib.sha256(sent['output']['text'].encode()).hexdigest()
        result = check.evaluate(request)
        self.assertFalse(result['handoff_received'])
        self.assertEqual(result['coordination_owner'], request['task']['native_ref'])
        self.assertIn('handoff_received_unverified', result['missing'])
        self.assert_partition(result, ['diagnose'], [], [])

    def test_inventory_delivery_does_not_close_candidates_or_unknown_ownership(self):
        request = self.mixed()
        request['progress']['steps'].extend([
            {'id': 'candidate_review', 'action': {'kind': 'read', 'resource': {'kind': 'service', 'id': 'docker',
                'path': 'candidate-review'}, 'capabilities': ['candidate-evidence']}},
            {'id': 'unknown_owner', 'action': {'kind': 'read', 'resource': {'kind': 'service', 'id': 'docker',
                'path': 'anonymous-owner'}, 'capabilities': ['owner-evidence']}}])
        # Refresh the provided goal-review set, rather than pretending a completed
        # inventory can prove those derived questions absent.
        import hashlib
        import json
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'goal_review')
        text = json.dumps({'goal_ref': request['progress']['goal_ref'],
                           'step_ids': [s['id'] for s in request['progress']['steps']]})
        record['digest'] = hashlib.sha256(text.encode()).hexdigest()
        items = request['context']['native_sources'][0]['turns'][0]['items']
        next(i for i in items if i['id'] == record['native_item_id'])['text'] = text
        result = check.evaluate(request)
        self.assert_partition(result, ['code', 'offline'], ['adoption'], ['live', 'candidate_review', 'unknown_owner'])
        request['progress']['steps'] = request['progress']['steps'][:4]
        self.assertIn('remaining_goals_omitted_or_unreviewed', check.evaluate(request)['blocked'])

    def test_feedback_correction_must_replace_old_plan_version(self):
        request = fixtures.action_case()
        target = copy.deepcopy(request['action']['resource'])
        request['project']['accepted_corrections'] = [{'ref': 'codex:fix:threshold',
            'resource': target, 'version': 'commit:corrected'}]
        request['project']['corrections_source_ref'] = 'codex:user:feedback'
        record, item = ContinuationChecks().add(request, 'codex:fix:threshold', 'fix',
            'verified criterion commit:corrected', item_type='commandExecution', version='commit:corrected')
        item.update(status='completed', exitCode=0, command='verify criterion commit:corrected')
        self.assertIn('accepted_correction_not_used', check.evaluate(request)['missing'])
        request['action']['accepted_corrections'] = [{'ref': record['ref'], 'version': 'commit:old'}]
        self.assertIn('stale_correction_version', check.evaluate(request)['blocked'])
        request['action']['accepted_corrections'][0]['version'] = 'commit:corrected'
        self.assertEqual(check.evaluate(request)['status'], 'ready')

    def test_feedback_acknowledgment_is_not_actual_followup_or_cancel_release(self):
        request = fixtures.ClosureChecks().close_case()
        request['context']['evidence'] = [r for r in request['context']['evidence'] if r['kind'] != 'followup']
        self.assertIn('followup_used_correction', check.evaluate(request)['missing'])
        request = self.mixed()
        request['context']['tool_failures'] = [{'kind': 'user_cancelled', 'ref': 'codex:user:cancelled',
            'resource': request['progress']['steps'][0]['action']['resource']}]
        ContinuationChecks().add(request, 'codex:feedback:grouped', 'observation', 'feedback grouped; no cancellation release')
        result = check.evaluate(request)
        self.assertIn('user_cancelled_action', result['steps'][0]['blocked'])
        self.assert_partition(result, ['offline'], ['code', 'adoption'], ['live'])

    def test_merged_candidate_requires_combination_test_and_exact_independent_version(self):
        request = fixtures.ClosureChecks().close_case()
        target = request['issue']['resource']
        review = request['issue']['independent_verification']
        request['phase'] = 'action'
        request['action'] = {'kind': 'publish', 'resource': target, 'effect_key': 'release',
            'depends_on': ['codex:merge:combined'], 'release': {'goal_ref': request['issue']['goal_ref'],
                'version': 'commit:merged', 'test_ref': 'codex:test:combined', 'independent_verification': review}}
        request['context']['native_operations'] = [{'ref': 'codex:merge:combined', 'project_id': 'framework',
            'task_ref': request['task']['native_ref'], 'effect_key': 'merge', 'state': 'unknown'}]
        result = check.evaluate(request)
        self.assertIn('dependency_unverified', result['missing'])
        self.assertIn('tested_release_version', result['missing'])
        request['context']['native_operations'][0]['state'] = 'succeeded'
        record, item = ContinuationChecks().add(request, 'codex:test:combined', 'release_test',
            'combination regression commit:merged', item_type='commandExecution', version='commit:merged', result='passed',
            resource=copy.deepcopy(target), related_ref=request['issue']['goal_ref'])
        item.update(status='completed', exitCode=0, command='combination regression commit:merged')
        self.assertIn('independent_verification_version_mismatch', check.evaluate(request)['missing'])
        fixtures.attach_independent_verification(request, 'commit:merged', suffix='combined')
        self.assertEqual(check.evaluate(request)['status'], 'ready')
        item['exitCode'] = 1
        self.assertIn('tested_release_version', check.evaluate(request)['missing'])



class DeliveryAcceptanceChecks(unittest.TestCase):
    """Completion evidence must describe the requested target, not just a run."""

    def completed(self):
        request = fixtures.ClosureChecks().close_case()
        issue = request['issue']
        request['phase'] = 'progress'
        request['progress'] = {'checkpoint': 'stage_end', 'claim': 'completed', 'steps': [],
            'goal_ref': issue['goal_ref'], 'version': issue['regression']['version'],
            'acceptance_ref': issue['acceptance_ref'], 'resource': copy.deepcopy(issue['resource']),
            'independent_verification': copy.deepcopy(issue['independent_verification'])}
        ContinuationChecks().review(request, goal=issue['goal_ref'])
        return request

    def publish(self):
        request = self.completed()
        issue = request['issue']
        request['phase'] = 'action'
        request['action'] = {'kind': 'publish', 'resource': copy.deepcopy(issue['resource']),
            'effect_key': 'publish-fixed', 'release': {'goal_ref': issue['goal_ref'],
                'version': issue['regression']['version'], 'test_ref': 'codex:test:release',
                'independent_verification': copy.deepcopy(issue['independent_verification'])}}
        _, item = ContinuationChecks().add(request, 'codex:test:release', 'release_test',
            'test commit:abc', item_type='commandExecution', result='passed', version='commit:abc',
            resource=copy.deepcopy(issue['resource']), related_ref=issue['goal_ref'])
        item.update(status='completed', exitCode=0, command='test commit:abc')
        return request

    def rewrite_review(self, request, **changes):
        import hashlib
        import json
        record = next(r for r in request['context']['evidence']
                      if r['ref'] == request['progress']['continuation']['review_ref'])
        item = next(i for src in request['context']['native_sources'] for turn in src['turns']
                    for i in turn['items'] if i['id'] == record['native_item_id'])
        body = json.loads(item['text'])
        body.update(changes)
        item['text'] = json.dumps(body)
        record['digest'] = hashlib.sha256(item['text'].encode()).hexdigest()

    def test_completion_acceptance_scope_is_required_and_exact(self):
        request = self.completed()
        self.assertEqual(check.evaluate(request)['status'], 'ready')
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'acceptance')
        record['resource'] = {'kind': 'repo', 'id': 'framework', 'path': 'scripts/scheduler.py'}
        self.assertIn('goal_acceptance_resource_mismatch', check.evaluate(request)['blocked'])
        record.pop('resource')
        self.assertIn('goal_acceptance_scope', check.evaluate(request)['missing'])

    def test_final_message_or_terminal_command_is_not_goal_acceptance(self):
        request = self.completed()
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'acceptance')
        item = next(i for i in request['context']['native_sources'][0]['turns'][0]['items']
                    if i['id'] == record['native_item_id'])
        item.update(type='agentMessage', phase='final_answer', text='completed scheduler investigation commit:abc')
        self.assertIn('unverified_acceptance_source', check.evaluate(request)['missing'])
        item.update(type='commandExecution', status='completed', exitCode=1)
        self.assertIn('unverified_acceptance_source', check.evaluate(request)['missing'])

    def test_all_issue_outcomes_keep_the_same_resource(self):
        kinds = ['regression_original', 'regression_affected', 'adoption', 'effect', 'acceptance', 'followup']
        for kind in kinds:
            with self.subTest(kind=kind):
                request = fixtures.ClosureChecks().close_case()
                record = next(r for r in request['context']['evidence'] if r['kind'] == kind)
                record['resource'] = {'kind': 'repo', 'id': 'framework', 'path': 'scripts/other.py'}
                label = 'goal_acceptance' if kind == 'acceptance' else kind
                self.assertIn(label + '_resource_mismatch', check.evaluate(request)['blocked'])
                record.pop('resource')
                self.assertIn(label + '_scope', check.evaluate(request)['missing'])

    def test_release_test_goal_resource_and_version_are_independent_bindings(self):
        request = self.publish()
        self.assertEqual(check.evaluate(request)['status'], 'ready')
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'release_test')
        record['related_ref'] = 'codex:user:another-goal'
        self.assertIn('release_test_goal_mismatch', check.evaluate(request)['blocked'])
        record['related_ref'] = request['issue']['goal_ref']
        record['resource'] = {'kind': 'repo', 'id': 'framework', 'path': 'scripts/scheduler.py'}
        self.assertIn('release_test_resource_mismatch', check.evaluate(request)['blocked'])
        record['resource'] = copy.deepcopy(request['issue']['resource'])
        record.pop('related_ref')
        self.assertIn('release_test_goal_reference', check.evaluate(request)['missing'])
        record['related_ref'] = request['issue']['goal_ref']
        request['action']['release']['version'] = 'commit:merged'
        self.assertIn('tested_release_version', check.evaluate(request)['missing'])

    def test_completed_cannot_omit_a_supplied_unfinished_goal_review(self):
        request = self.completed()
        self.rewrite_review(request, step_ids=['farside-repair'])
        self.assertIn('remaining_goals_omitted_or_unreviewed', check.evaluate(request)['blocked'])
        request['progress'].pop('continuation')
        self.assertIn('remaining_goal_review', check.evaluate(request)['missing'])
        self.assertFalse(check.evaluate(request)['wait_allowed'])

    def test_completion_review_matches_candidate_version_and_resource(self):
        request = self.completed()
        self.rewrite_review(request, version='commit:older')
        self.assertIn('goal_review_version_mismatch', check.evaluate(request)['missing'])
        self.rewrite_review(request, version='commit:abc', resource={'kind': 'repo', 'id': 'framework', 'path': 'other'})
        self.assertIn('goal_review_resource_mismatch', check.evaluate(request)['blocked'])
        self.rewrite_review(request, resource=None)
        self.assertIn('goal_review_resource', check.evaluate(request)['missing'])

    def test_newer_remaining_review_prevents_old_empty_finish_and_selects_next_action(self):
        import json
        request = self.completed()
        goal = request['issue']['goal_ref']
        ContinuationChecks().add(request, 'codex:review:after-return', 'goal_review', json.dumps({
            'goal_ref': goal, 'version': 'commit:abc', 'resource': request['issue']['resource'],
            'step_ids': ['remaining-fix']}))
        self.assertIn('newer_goal_review_requires_scope_review', check.evaluate(request)['missing'])
        request['progress']['continuation']['review_ref'] = 'codex:review:after-return'
        self.assertIn('remaining_goals_omitted_or_unreviewed', check.evaluate(request)['blocked'])
        request['progress']['claim'] = 'working'
        request['progress']['steps'] = [{'id': 'remaining-fix', 'action': {
            'kind': 'read', 'resource': copy.deepcopy(request['issue']['resource'])}}]
        self.assertIn('next_step_not_selected', check.evaluate(request)['missing'])
        request['progress']['continuation']['selected_step_id'] = 'remaining-fix'
        result = check.evaluate(request)
        self.assertIn('continuation_action_unverified', result['missing'])
        self.assertEqual(result['work_partition']['ready_step_ids'], ['remaining-fix'])
        self.assertFalse(result['continuous_eligible'])

    def test_unordered_or_unverified_other_review_cannot_silently_validate_completion(self):
        import json
        request = self.completed()
        record, item = ContinuationChecks().add(request, 'codex:review:ambiguous', 'goal_review', json.dumps({
            'goal_ref': request['issue']['goal_ref'], 'step_ids': ['still-open']}))
        source = request['context']['native_sources'][0]
        source['turns'][0]['items'].remove(item)
        request['context']['native_sources'].append({'schemaVersion': 1,
            'thread': {'id': request['task']['native_ref'].removeprefix('codex:thread:')},
            'turns': [{'items': [item]}]})
        self.assertIn('goal_review_order_unverified', check.evaluate(request)['missing'])
        record['digest'] = '0' * 64
        self.assertIn('goal_review_source_unverified', check.evaluate(request)['missing'])

    def test_malformed_outcome_scope_has_structured_cli_error(self):
        import json
        import subprocess
        request = self.completed()
        next(r for r in request['context']['evidence'] if r['kind'] == 'acceptance')['resource'] = []
        frozen = json.dumps(request)
        p = subprocess.run([sys.executable, '-B', str(Path(__file__).resolve().parents[1] /
            'scripts/codex_framework.py'), 'check'], input=frozen, text=True, capture_output=True, timeout=10)
        self.assertEqual(p.returncode, 1)
        self.assertEqual(p.stderr, '')
        self.assertEqual(json.loads(p.stdout)['status'], 'invalid')


class AuthorizationScopeContinuationChecks(unittest.TestCase):
    """A scoped handoff reuses a covering grant to the same actual recipient."""

    def setUp(self):
        import tempfile
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve() / "repo"
        (self.root / "scripts").mkdir(parents=True)

    def request(self):
        request = ContinuationChecks().handoff("received")
        request["project"]["root"] = str(self.root)
        h = request["progress"]["continuation"]["handoff"]
        authorization = next(r for r in request["context"]["evidence"] if r["ref"] == h["authorization_ref"])
        authorization["resource"] = {"kind": "repo", "id": "framework", "path": "scripts"}
        authorization["workspace"].update(execution_nodes=["mac-mini"], data_nodes=["mac-mini"],
            destination_nodes=["mac-mini"], side_effects=["read"])
        return request

    def test_same_authorized_recipient_accepts_safe_child_scope(self):
        result = check.evaluate(self.request())
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["continuation_state"], "received")

    def test_missing_path_facts_requests_evidence_without_new_message_authority(self):
        request = self.request()
        request["project"].pop("root")
        result = check.evaluate(request)
        self.assertIn("handoff_authorized_resource_unverified", result["missing"])
        self.assertNotIn("handoff_message_authorization", result["missing"])
        self.assertNotEqual(result["status"], "ready")

    def test_covering_directory_does_not_authorize_another_recipient(self):
        request = self.request()
        request["progress"]["continuation"]["handoff"]["target_task_ref"] = "codex:thread:another-task"
        self.assertIn("handoff_message_authorization", check.evaluate(request)["missing"])

    def test_handoff_child_cannot_escape_grant_through_symlink_or_node(self):
        outside = self.root.parent / "outside"
        outside.mkdir()
        (self.root / "scripts/escape").symlink_to(outside, target_is_directory=True)
        request = self.request()
        request["progress"]["steps"][0]["action"]["resource"]["path"] = "scripts/escape/new.py"
        self.assertIn("handoff_message_authorization", check.evaluate(request)["missing"])
        request = self.request()
        request["progress"]["steps"][0]["action"]["workspace"]["execution_node"] = "another-node"
        self.assertIn("handoff_outside_authorized_workspace", check.evaluate(request)["blocked"])


    def bound_request(self):
        request = self.request()
        h = request["progress"]["continuation"]["handoff"]
        ref = h["authorization_ref"]
        text = "Send only this scripts directory scope on mac-mini to the existing review task. No other execution, data or destination node is authorized."
        native = next(i for s in request["context"]["native_sources"] for t in s["turns"] for i in t["items"] if i["id"] == ref)
        native["text"] = text
        import hashlib
        next(r for r in request["context"]["evidence"] if r["ref"] == ref)["digest"] = hashlib.sha256(text.encode()).hexdigest()
        return request

    def identity_proof(self, request, aliases, node="mac-mini", item_type="commandExecution", root=None):
        import json
        body = json.dumps({"node_id": node, "aliases": aliases, "root": str(root or self.root)})
        record, native = ContinuationChecks().add(request, "codex:node:identity", "node_identity", body,
            item_type=item_type, result="observed", node_id=node)
        if item_type == "commandExecution":
            native.update(status="completed", exitCode=0, command="observe existing node and checkout identity")
        request["context"]["node_identity_ref"] = record["ref"]

    def test_original_grant_nodes_survive_all_or_single_proposed_node_changes(self):
        import json
        keys = ["execution_node", "data_node", "destination_node"]
        for changed in ([key] for key in keys):
            request = self.bound_request()
            for key in changed:
                request["progress"]["steps"][0]["action"]["workspace"][key] = "foreign-node"
            before = json.dumps(request, sort_keys=True)
            result = check.evaluate(request)
            self.assertIn("handoff_outside_authorized_workspace", result["blocked"])
            self.assertFalse(result["handoff_received"])
            self.assertEqual(json.dumps(request, sort_keys=True), before)
        request = self.bound_request()
        for key in keys:
            request["progress"]["steps"][0]["action"]["workspace"][key] = "foreign-node"
        result = check.evaluate(request)
        self.assertIn("handoff_outside_authorized_workspace", result["blocked"])
        self.assertFalse(result["handoff_received"])

    def test_original_node_and_effect_bounds_apply_also_to_exact_resource(self):
        request = self.bound_request()
        h = request["progress"]["continuation"]["handoff"]
        grant = next(r for r in request["context"]["evidence"] if r["ref"] == h["authorization_ref"])
        grant["resource"] = dict(request["progress"]["steps"][0]["action"]["resource"])
        for key in ["execution_node", "data_node", "destination_node"]:
            request["progress"]["steps"][0]["action"]["workspace"][key] = "foreign-node"
        self.assertFalse(check.evaluate(request)["handoff_received"])
        request = self.bound_request()
        request["progress"]["steps"][0]["action"]["workspace"]["side_effect"] = "local_change"
        self.assertIn("handoff_outside_authorized_workspace", check.evaluate(request)["blocked"])

    def test_verified_node_alias_keeps_the_original_machine_and_directory(self):
        request = self.bound_request()
        for key in ["execution_node", "data_node", "destination_node"]:
            request["progress"]["steps"][0]["action"]["workspace"][key] = "known-host.local"
        self.identity_proof(request, ["known-host.local"])
        result = check.evaluate(request)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["handoff_received"])

    def test_unknown_self_asserted_or_wrong_source_alias_cannot_change_machine(self):
        for mode in ("absent", "self_asserted", "assistant", "other_node", "other_root"):
            with self.subTest(mode=mode):
                request = self.bound_request()
                for key in ["execution_node", "data_node", "destination_node"]:
                    request["progress"]["steps"][0]["action"]["workspace"][key] = "unknown-host.local"
                if mode == "self_asserted":
                    request["context"]["node_aliases"] = {"unknown-host.local": "mac-mini"}
                elif mode == "assistant":
                    self.identity_proof(request, ["unknown-host.local"], item_type="agentMessage")
                elif mode == "other_node":
                    self.identity_proof(request, ["unknown-host.local"], node="foreign-node")
                elif mode == "other_root":
                    self.identity_proof(request, ["unknown-host.local"], root=self.root.parent)
                result = check.evaluate(request)
                self.assertNotEqual(result["status"], "ready")
                self.assertFalse(result["handoff_received"])

    def test_alias_resolution_retains_platform_refusal_and_recipient_bounds(self):
        request = self.bound_request()
        action = request["progress"]["steps"][0]["action"]
        for key in ["execution_node", "data_node", "destination_node"]:
            action["workspace"][key] = "known-host.local"
        self.identity_proof(request, ["known-host.local"])
        request["context"]["tool_failures"] = [{"kind": "platform_denied", "action_kind": "read",
            "resource": dict(action["resource"]), "destination_node": "mac-mini", "side_effect": "read"}]
        result = check.evaluate(request)
        self.assertEqual(result["steps"][0]["state"], "direct_refusal")
        self.assertFalse(result["handoff_received"])


    def test_old_exact_message_grant_without_node_fields_stays_compatible(self):
        request = ContinuationChecks().handoff("received")
        request["progress"]["steps"][0]["action"].pop("workspace")
        result = check.evaluate(request)
        self.assertEqual(result["status"], "ready")
        self.assertTrue(result["handoff_received"])

    def test_malformed_identity_reference_is_structured_input_error(self):
        request = self.bound_request()
        request["context"]["node_identity_ref"] = []
        with self.assertRaises(check.CheckError):
            check.evaluate(request)




class ActiveOpportunityChecks(unittest.TestCase):
    """Synthetic relationships only; actual manager continuation needs native evidence."""

    def rewrite(self, request, record, text):
        import hashlib
        record['digest'] = hashlib.sha256(text.encode()).hexdigest()
        # Find without verifying the now intentionally changed digest.
        item = next(i for s in request['context']['native_sources'] for t in s['turns']
                    for i in t['items'] if i['id'] == record['native_item_id'])
        if item['type'] in ('userMessage', 'agentMessage'):
            item['text'] = text
        else:
            item['output']['text'] = text

    def event(self, request, name='codex:event:child-return'):
        helper = ContinuationChecks()
        record, item = helper.add(request, name, 'continuation_event', 'Finite child result or ledger merge returned',
            item_type='functionCallOutput', related_ref=request['progress']['goal_ref'])
        source = request['context']['native_sources'][0]['turns'][0]['items']
        source.remove(item)
        review = request['progress']['continuation']['review_ref']
        source.insert(next(i for i, x in enumerate(source) if x['id'] == review), item)
        request['progress']['continuation']['event_ref'] = name
        return record, item

    def execution(self, request, name='codex:execution:next'):
        c = request['progress']['continuation']
        record, item = ContinuationChecks().add(request, name, 'continuation_execution', 'Allowed scoped action finished',
            item_type='commandExecution', result='observed', related_ref=c['review_ref'],
            resource=copy.deepcopy(request['progress']['steps'][0]['action']['resource']))
        item.update(command='inspect registered manager entry', status='completed', exitCode=0)
        c['execution_ref'] = name
        return record, item

    def test_four_original_boundary_gaps_cannot_authorize_stop(self):
        for mode in ('stage_end', 'handoff', 'selected', 'returned'):
            request = ContinuationChecks().selected()
            if mode in ('stage_end', 'handoff'):
                request['progress']['checkpoint'] = mode
                request['progress'].pop('continuation')
            elif mode == 'returned':
                request = ContinuationChecks().handoff('returned')
                h = request['progress']['continuation']['handoff']
                h['result_ref'] = 'codex:child:finite-result'
                _, item = ContinuationChecks().add(request, h['result_ref'], 'handoff_result', 'failed '+h['version'],
                    thread=h['target_task_ref'], version=h['version'])
                item['phase'] = 'final_answer'
            with self.subTest(mode=mode):
                result = check.evaluate(request)
                self.assertEqual(result['status'], 'needs_evidence')
                self.assertFalse(result['stop_allowed'])
                self.assertEqual(result['work_partition']['ready_step_ids'], ['diagnose'])
                if mode == 'returned':
                    self.assertEqual(result['next_operation'], 'review_returned_result_and_continue')

    def test_actual_scoped_action_after_review_advances_only_one_step(self):
        request = ContinuationChecks().selected()
        self.event(request)
        self.execution(request)
        result = check.evaluate(request)
        self.assertEqual(result['status'], 'ready')
        self.assertTrue(result['local_action_observed'])
        self.assertFalse(result['stop_allowed'])
        self.assertEqual(result['next_operation'], 'review_remaining_work')

    def test_second_return_needs_a_new_review_and_new_action(self):
        import json
        request = ContinuationChecks().selected()
        self.event(request)
        self.execution(request)
        helper = ContinuationChecks()
        helper.add(request, 'codex:event:second-return', 'continuation_event', 'A later finite result returned',
                   item_type='functionCallOutput', related_ref=request['progress']['goal_ref'])
        request['progress']['continuation']['event_ref'] = 'codex:event:second-return'
        self.assertIn('post_event_goal_review_unverified', check.evaluate(request)['missing'])
        helper.add(request, 'codex:review:second', 'goal_review', json.dumps({
            'goal_ref': request['progress']['goal_ref'], 'step_ids': ['diagnose']}))
        request['progress']['continuation']['review_ref'] = 'codex:review:second'
        self.assertFalse(check.evaluate(request)['local_action_observed'])
        self.execution(request, 'codex:execution:second')
        result = check.evaluate(request)
        self.assertEqual(result['status'], 'ready')
        self.assertFalse(result['stop_allowed'])

    def test_event_must_be_actual_current_goal_input_before_review(self):
        for variant in ('assistant_promise', 'other_goal', 'after_review', 'missing_source'):
            request = ContinuationChecks().selected()
            record, item = self.event(request)
            self.execution(request)
            if variant == 'assistant_promise':
                item.update(type='agentMessage', text=item['output']['text'])
                item.pop('output')
            elif variant == 'other_goal':
                record['related_ref'] = 'codex:user:other-goal'
            elif variant == 'after_review':
                source = request['context']['native_sources'][0]['turns'][0]['items']
                source.remove(item); source.append(item)
            else:
                record['native_item_id'] = 'codex:missing:event'
            with self.subTest(variant=variant):
                result = check.evaluate(request)
                self.assertIn('post_event_goal_review_unverified', result['missing'])
                self.assertFalse(result['stop_allowed'])

    def test_failed_unknown_stale_wrong_scope_and_self_reported_execution_do_not_advance(self):
        for variant in ('failed', 'running', 'unknown', 'old', 'wrong_resource', 'wrong_review', 'assistant', 'no_source'):
            request = ContinuationChecks().selected()
            record, item = self.execution(request)
            if variant == 'failed': item.update(status='failed', exitCode=1)
            elif variant == 'running': item.update(status='inProgress', exitCode=None)
            elif variant == 'unknown': item.pop('exitCode')
            elif variant == 'old':
                source = request['context']['native_sources'][0]['turns'][0]['items']
                source.remove(item); source.insert(0, item)
            elif variant == 'wrong_resource': record['resource']['path'] = 'other-task/object'
            elif variant == 'wrong_review': record['related_ref'] = 'codex:review:old'
            elif variant == 'assistant':
                item.update(type='agentMessage', text=item['output']['text']); item.pop('output')
            else: record['native_item_id'] = 'missing'
            with self.subTest(variant=variant):
                result = check.evaluate(request)
                self.assertFalse(result['local_action_observed'])
                self.assertIn('continuation_execution_unverified', result['missing'])
                self.assertFalse(result['stop_allowed'])

    def test_sent_waits_for_receipt_and_received_does_not_release_whole_goal(self):
        request = ContinuationChecks().handoff('sent')
        request['progress']['continuation']['handoff'].pop('received_ref')
        result = check.evaluate(request)
        self.assertEqual(result['next_operation'], 'verify_original_handoff_receipt')
        self.assertFalse(result['stop_allowed'])
        request = ContinuationChecks().handoff('received')
        result = check.evaluate(request)
        self.assertEqual(result['status'], 'ready')
        self.assertTrue(result['handoff_received'])
        self.assertFalse(result['stop_allowed'])

    def test_old_sent_receipt_cannot_prove_a_new_post_return_dispatch(self):
        import json
        request = ContinuationChecks().handoff('received')
        ContinuationChecks().add(request, 'codex:review:new', 'goal_review', json.dumps({
            'goal_ref': request['progress']['goal_ref'], 'step_ids': ['diagnose']}))
        request['progress']['continuation']['review_ref'] = 'codex:review:new'
        result = check.evaluate(request)
        self.assertFalse(result['handoff_received'])
        self.assertFalse(result['stop_allowed'])

    def test_new_goal_or_supplied_newer_review_invalidates_old_action_plan(self):
        import json
        for variant in ('goal', 'review'):
            request = ContinuationChecks().selected()
            self.execution(request)
            if variant == 'goal':
                ContinuationChecks().add(request, 'codex:user:changed', 'goal', 'New direct requirement', item_type='userMessage')
            else:
                ContinuationChecks().add(request, 'codex:review:changed', 'goal_review', json.dumps({
                    'goal_ref': request['progress']['goal_ref'], 'step_ids': ['diagnose', 'uncreated-derived-work']}))
            with self.subTest(variant=variant):
                result = check.evaluate(request)
                self.assertNotEqual(result['status'], 'ready')
                self.assertFalse(result['stop_allowed'])

    def paused(self):
        import json
        request = ContinuationChecks().base()
        target = copy.deepcopy(request['progress']['steps'][0]['action']['resource'])
        helper = ContinuationChecks()
        helper.add(request, 'codex:user:pause', 'pause', 'Pause this entire authorized scope', item_type='userMessage',
                   related_ref=request['progress']['goal_ref'], resource=target)
        helper.add(request, 'codex:review:paused', 'goal_review', json.dumps({
            'goal_ref': request['progress']['goal_ref'], 'step_ids': ['diagnose']}))
        request['progress'].update(claim='paused', resource=target, pause_ref='codex:user:pause',
            continuation={'review_ref': 'codex:review:paused'})
        return request

    def test_current_user_pause_is_a_legitimate_stop_without_dispatch(self):
        result = check.evaluate(self.paused())
        self.assertEqual(result['status'], 'ready')
        self.assertTrue(result['stop_allowed'])
        self.assertEqual(result['next_operation'], 'respect_user_pause')

    def test_pause_cannot_cover_unrelated_work_or_execute_paused_work(self):
        request = self.paused()
        request['progress']['steps'][0]['action']['resource']['path'] = 'scripts/unrelated.py'
        self.assertIn('independent_work_outside_pause', check.evaluate(request)['blocked'])
        request = self.paused()
        request['progress']['continuation']['selected_step_id'] = 'diagnose'
        self.assertIn('paused_work_must_not_execute', check.evaluate(request)['blocked'])

    def test_old_or_assistant_pause_is_not_a_direct_current_user_pause(self):
        for variant in ('assistant', 'old', 'missing'):
            request = self.paused()
            record = next(r for r in request['context']['evidence'] if r['kind'] == 'pause')
            item = check.native_item(request['context'], record)
            if variant == 'assistant': item['type'] = 'agentMessage'
            elif variant == 'old':
                source = request['context']['native_sources'][0]['turns'][0]['items']
                source.remove(item); source.insert(0, item)
            else: request['progress'].pop('pause_ref')
            with self.subTest(variant=variant):
                self.assertFalse(check.evaluate(request)['stop_allowed'])

    def test_true_verified_wait_and_accepted_goal_are_legitimate_stop_controls(self):
        request = ProgressChecks().progress(ProgressChecks().base(), 'waiting')
        request['progress']['steps'] = request['progress']['steps'][:2]
        ContinuationChecks().review(request)
        result = check.evaluate(request)
        self.assertTrue(result['wait_allowed'])
        self.assertTrue(result['stop_allowed'])
        result = check.evaluate(DeliveryAcceptanceChecks().completed())
        self.assertTrue(result['stop_allowed'])

    def test_managed_entry_unknown_existing_provider_and_exact_refusal_remain_distinct(self):
        for variant in ('unknown_entry', 'existing_provider', 'exact_refusal', 'external_business'):
            request = fixtures.AccessRoutingChecks().access_case()
            action = copy.deepcopy(request['action'])
            action.pop('capabilities', None)
            if variant == 'unknown_entry': action['access']['authentication_path'] = 'unknown'
            elif variant == 'exact_refusal':
                request['context']['tool_failures'] = [{'kind': 'platform_denied', 'resource': copy.deepcopy(action['resource']),
                    'action_kind': 'read', 'side_effect': 'read', 'destination_node': 'mac-mini'}]
            elif variant == 'external_business':
                action['access'].update(resource_owner='external_business', identity_role='business')
            request.update(phase='progress', progress={'checkpoint': 'after_failure', 'claim': 'working',
                'steps': [{'id': 'metadata', 'action': action}]})
            result = check.evaluate(request)
            with self.subTest(variant=variant):
                self.assertIn('access_route', result['steps'][0])
                self.assertFalse(result['stop_allowed'])
                if variant == 'unknown_entry':
                    self.assertEqual(result['steps'][0]['access_route'], 'discover_existing_authentication')
                    self.assertEqual(result['steps'][0]['state'], 'needs_evidence')
                elif variant == 'exact_refusal':
                    self.assertEqual(result['steps'][0]['state'], 'direct_refusal')
                else:
                    self.assertEqual(result['steps'][0]['state'], 'ready')

    def test_new_reference_shapes_fail_structurally_without_traceback(self):
        import contextlib, io, json
        import codex_framework as framework
        for key in ('event_ref', 'execution_ref', 'review_ref'):
            for value in (None, [], {}, 1, False, ''):
                request = ContinuationChecks().selected()
                request['progress']['continuation'][key] = value
                output, error = io.StringIO(), io.StringIO()
                with self.subTest(key=key, value=value), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error), unittest.mock.patch('sys.stdin', io.StringIO(json.dumps(request))):
                    self.assertEqual(framework.main(['check']), 1)
                    self.assertEqual(json.loads(output.getvalue())['status'], 'invalid')
                    self.assertEqual(error.getvalue(), '')

    def test_review_cannot_precede_the_selected_user_goal(self):
        request = ContinuationChecks().selected()
        self.execution(request)
        source = request['context']['native_sources'][0]['turns'][0]['items']
        goal = next(i for i in source if i['id'] == request['progress']['goal_ref'])
        source.remove(goal); source.append(goal)
        self.assertIn('goal_review_order_unverified', check.evaluate(request)['missing'])

    def test_pause_outside_current_project_does_not_stop_this_goal(self):
        request = self.paused()
        request['progress']['steps'] = []
        request['progress']['resource'] = {'kind': 'repo', 'id': 'other-project'}
        pause = next(r for r in request['context']['evidence'] if r['kind'] == 'pause')
        pause['resource'] = copy.deepcopy(request['progress']['resource'])
        self.assertFalse(check.evaluate(request)['stop_allowed'])
        self.assertIn('progress_outside_scope', check.evaluate(request)['blocked'])

    def test_handoff_reference_shapes_are_structured_input_errors(self):
        import contextlib, io, json
        import codex_framework as framework
        for key in ('authorization_ref', 'sent_ref', 'received_ref', 'execution_ref', 'result_ref'):
            for value in ([], {}, 1, False):
                request = ContinuationChecks().handoff('received')
                request['progress']['continuation']['handoff'][key] = value
                output, error = io.StringIO(), io.StringIO()
                with self.subTest(key=key, value=value), contextlib.redirect_stdout(output), contextlib.redirect_stderr(error), unittest.mock.patch('sys.stdin', io.StringIO(json.dumps(request))):
                    self.assertEqual(framework.main(['check']), 1)
                    self.assertEqual(json.loads(output.getvalue())['status'], 'invalid')
                    self.assertEqual(error.getvalue(), '')

    def test_pause_rejects_text_and_structured_delegation_with_valid_source_digest(self):
        for variant in ('wrapper', 'structured', 'empty_marker', 'null_marker', 'false_marker'):
            request = self.paused()
            record = next(r for r in request['context']['evidence'] if r['kind'] == 'pause')
            item = check.native_item(request['context'], record)
            text = item['text']
            if variant == 'wrapper':
                self.rewrite(request, record, '<codex_delegation><input>'+text+'</input></codex_delegation>')
            else:
                marker = {'sourceThreadId': 'another-task'} if variant == 'structured' else {} if variant == 'empty_marker' else None if variant == 'null_marker' else False
                item['content'] = [{'type': 'text', 'text': text, 'codexDelegation': marker}]
            with self.subTest(variant=variant):
                result = check.evaluate(request)
                self.assertEqual(result['status'], 'needs_evidence')
                self.assertFalse(result['stop_allowed'])
                self.assertIn('current_user_pause_unverified', result['missing'])

    def test_pause_and_message_authorization_share_direct_source_filter(self):
        for mode in ('pause', 'message'):
            for marker in ({}, None, False, {'sourceThreadId': 'other'}):
                request = self.paused() if mode == 'pause' else ContinuationChecks().handoff('received')
                kind = 'pause' if mode == 'pause' else 'authorization'
                record = next(r for r in request['context']['evidence'] if r['kind'] == kind and r['ref'] in ('codex:user:pause', 'codex:user:message'))
                item = check.native_item(request['context'], record)
                item['content'] = [{'type': 'text', 'text': item['text'], 'codexDelegation': marker}]
                with self.subTest(mode=mode, marker=marker):
                    result = check.evaluate(request)
                    self.assertNotEqual(result['status'], 'ready')
                    self.assertFalse(result['stop_allowed'])

    def test_malformed_pause_content_is_structured_input_error(self):
        import contextlib, io, json
        import codex_framework as framework
        request = self.paused()
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'pause')
        item = check.native_item(request['context'], record)
        item['content'] = {}
        output, error = io.StringIO(), io.StringIO()
        with contextlib.redirect_stdout(output), contextlib.redirect_stderr(error), unittest.mock.patch('sys.stdin', io.StringIO(json.dumps(request))):
            self.assertEqual(framework.main(['check']), 1)
        self.assertEqual(json.loads(output.getvalue())['status'], 'invalid')
        self.assertEqual(error.getvalue(), '')


class CurrentRequestChecks(unittest.TestCase):
    """Optional supplied request/report relations, never real host interception."""
    def add_request(self, request, ref='request:current', version=None, target=None, *, after_review=False, structured=False, related=None):
        helper = ContinuationChecks()
        version = version or request['progress']['version']
        target = copy.deepcopy(target or request['progress']['resource'])
        record, item = helper.add(request, ref, 'task_request',
            '<codex_delegation><source_thread_id>coordinator</source_thread_id><input>Review '+version+'</input></codex_delegation>',
            item_type='functionCallOutput', version=version, resource=target,
            related_ref=related or request['progress']['goal_ref'])
        item.update(name='send_message', namespace='cloud_threads')
        if structured:
            item['type'] = 'userMessage'
            item['content'] = [{'type':'text','text':item.pop('output')['text'],
                                'codexDelegation':{'sourceThreadId':'coordinator'}}]
        if not after_review:
            source = request['context']['native_sources'][0]['turns'][0]['items']
            source.remove(item)
            source.insert(next(n for n,x in enumerate(source) if x['id']==request['progress']['continuation']['review_ref']), item)
        return record, item

    def add_report(self, request, selected='request:current', ref='report:current', *, body=None, version=None, target=None):
        import json
        original = next(r for r in request['context']['evidence'] if r['ref']==selected)
        version = version or original['version'];target=copy.deepcopy(target or original['resource'])
        body = body or {'version':version,'outcome':'passed'}
        record,item=ContinuationChecks().add(request,ref,'task_report',json.dumps(body),item_type='commandExecution',
            version=version,resource=target,related_ref=selected,result='observed')
        item.update(command='read frozen report '+version,status='completed',exitCode=0)
        return record,item

    def bound(self, **kwargs):
        request=DeliveryAcceptanceChecks().completed()
        self.add_request(request, **kwargs);self.add_report(request)
        request['progress']['current_request']={'input_ref':'request:current','report_ref':'report:current'}
        return request

    def test_matching_current_input_report_and_old_acceptance_stay_valid_for_that_scope(self):
        for structured in (False,True):
            request=self.bound(structured=structured);result=check.evaluate(request)
            self.assertEqual(result['status'],'ready');self.assertTrue(result['stop_allowed'])
            self.assertEqual(result['current_request']['state'],'report_matched')
            self.assertEqual(result['current_request']['outcome'],'passed')

    def test_different_current_object_and_version_cannot_close_stale_consistent_pack(self):
        request=DeliveryAcceptanceChecks().completed()
        target={'kind':'repo','id':'framework','path':'scripts/current-object'}
        self.add_request(request,version='manifest:new',target=target)
        self.add_report(request)
        request['progress']['current_request']={'input_ref':'request:current','report_ref':'report:current'}
        result=check.evaluate(request)
        self.assertIn('current_request_resource_mismatch',result['blocked'])
        self.assertIn('current_request_version_mismatch',result['blocked'])
        self.assertIn('current_request_acceptance_mismatch',result['blocked'])
        self.assertFalse(result['stop_allowed'])

    def test_same_object_new_frozen_version_rejects_old_result_and_lock(self):
        request=self.bound();selected=next(r for r in request['context']['evidence'] if r['ref']=='request:current')
        old=selected['version'];selected['version']='manifest:revision2'
        ActiveOpportunityChecks().rewrite(request,selected,'<codex_delegation><source_thread_id>coordinator</source_thread_id><input>Review manifest:revision2</input></codex_delegation>')
        result=check.evaluate(request)
        self.assertIn('current_request_version_mismatch',result['blocked'])
        self.assertIn('current_request_report_unverified',result['missing'])
        self.assertFalse(result['stop_allowed'])

    def test_hash_mention_or_conflicting_top_level_identity_is_not_binding(self):
        for body in ({'manifest_sha256':'old','verdict':'PASS_SCOPED','mentions':'commit:abc'},
                     {'manifest_sha256':'old','version':'commit:abc','verdict':'PASS_SCOPED'},
                     {'notes':'commit:abc PASS'}, {'version':'commit:abc','outcome':'passed','verdict':'FAIL_SCOPED'}):
            request=self.bound();record=next(r for r in request['context']['evidence'] if r['ref']=='report:current')
            import json
            ActiveOpportunityChecks().rewrite(request,record,json.dumps(body))
            with self.subTest(body=body):
                result=check.evaluate(request);self.assertIn('current_request_report_unverified',result['missing']);self.assertFalse(result['stop_allowed'])

    def test_failed_current_report_is_bound_but_not_candidate_success(self):
        request=self.bound();record=next(r for r in request['context']['evidence'] if r['ref']=='report:current')
        import json
        ActiveOpportunityChecks().rewrite(request,record,json.dumps({'manifest_sha256':'commit:abc','verdict':'FAIL_TWO_ADOPTION_BLOCKERS'}))
        result=check.evaluate(request)
        self.assertEqual(result['current_request']['state'],'report_matched')
        self.assertEqual(result['current_request']['outcome'],'failed')
        self.assertIn('current_request_report_failed',result['deviations']);self.assertFalse(result['stop_allowed'])

    def test_not_run_and_unrecognized_outcome_never_become_pass(self):
        import json
        for verdict in ('NOT_RUN_ENVIRONMENT','PASS in prose',None,[],True):
            request=self.bound();record=next(r for r in request['context']['evidence'] if r['ref']=='report:current')
            ActiveOpportunityChecks().rewrite(request,record,json.dumps({'version':'commit:abc','verdict':verdict}))
            with self.subTest(verdict=verdict):
                result=check.evaluate(request);self.assertFalse(result['stop_allowed'])
                self.assertNotEqual(result['current_request']['outcome'],'passed')

    def test_report_source_relationship_resource_and_order_are_required(self):
        for variant in ('assistant','other_request','other_resource','missing','truncated','before_request'):
            request=self.bound();record=next(r for r in request['context']['evidence'] if r['ref']=='report:current')
            item=check.native_item(request['context'],record)
            if variant=='assistant':item['type']='agentMessage';item['text']=item['output']['text'];item.pop('output')
            elif variant=='other_request':record['related_ref']='request:other'
            elif variant=='other_resource':record['resource']['path']='scripts/other'
            elif variant=='missing':record['native_item_id']='missing'
            elif variant=='truncated':item['output']['truncated']=True
            else:
                source=request['context']['native_sources'][0]['turns'][0]['items'];source.remove(item);source.insert(0,item)
            with self.subTest(variant=variant):
                result=check.evaluate(request);self.assertIn('current_request_report_unverified',result['missing']);self.assertFalse(result['stop_allowed'])

    def test_review_must_follow_selected_request(self):
        request=self.bound(after_review=True);result=check.evaluate(request)
        self.assertIn('current_request_review_unverified',result['missing']);self.assertFalse(result['stop_allowed'])

    def test_unverified_or_wrong_provider_input_cannot_bind(self):
        for variant in ('promise','provider','missing','version_mention_missing'):
            request=self.bound();record=next(r for r in request['context']['evidence'] if r['ref']=='request:current')
            item=check.native_item(request['context'],record)
            if variant=='promise':item['type']='agentMessage';item['text']=item['output']['text'];item.pop('output')
            elif variant=='provider':item['namespace']='unrelated_provider'
            elif variant=='missing':record['native_item_id']='missing'
            else:ActiveOpportunityChecks().rewrite(request,record,'<codex_delegation><source_thread_id>coordinator</source_thread_id><input>Other work</input></codex_delegation>')
            with self.subTest(variant=variant):
                result=check.evaluate(request);self.assertIn('current_request_input_unverified',result['missing']);self.assertFalse(result['stop_allowed'])

    def test_two_pending_requests_can_return_selected_one_without_latest_cancellation(self):
        request=self.bound();self.add_request(request,ref='request:second',version='manifest:second',after_review=True)
        request['progress']['claim']='working'
        result=check.evaluate(request)
        self.assertEqual(result['current_request']['state'],'report_matched')
        self.assertEqual(result['current_request']['pending_request_refs'],['request:second'])
        self.assertNotIn('current_request_superseded',result['missing']);self.assertFalse(result['stop_allowed'])
        request['progress']['claim']='completed';result=check.evaluate(request)
        self.assertIn('other_supplied_requests_unresolved',result['missing'])
        self.add_report(request,selected='request:second',ref='report:second');self.assertTrue(check.evaluate(request)['stop_allowed'])

    def test_explicit_newer_correction_requires_rebinding_not_silent_latest_selection(self):
        request=self.bound();self.add_request(request,ref='request:correction',version='manifest:fixed',after_review=True,related='request:current')
        result=check.evaluate(request)
        self.assertIn('current_request_superseded',result['missing']);self.assertFalse(result['stop_allowed'])
        self.assertEqual(result['current_request']['input_ref'],'request:current')

    def test_unrelated_child_event_version_is_not_the_next_action_version(self):
        request=self.bound();ContinuationChecks().add(request,'event:child','continuation_event','Child result manifest:other',
            item_type='functionCallOutput',version='manifest:other',resource=copy.deepcopy(request['progress']['resource']))
        self.assertEqual(check.evaluate(request)['status'],'ready')

    def test_request_binding_does_not_grant_message_or_pause_authority(self):
        request=ActiveOpportunityChecks().paused()
        self.add_request(request,version='manifest:input',target=request['progress']['resource'])
        request['progress']['current_request']={'input_ref':'request:current'}
        pause=next(r for r in request['context']['evidence'] if r['kind']=='pause')
        ActiveOpportunityChecks().rewrite(request,pause,'<codex_delegation><source_thread_id>coordinator</source_thread_id><input>Pause</input></codex_delegation>')
        result=check.evaluate(request);self.assertIn('current_user_pause_unverified',result['missing']);self.assertFalse(result['stop_allowed'])

    def test_optional_absence_preserves_the_entire_previous_progress_result(self):
        request=DeliveryAcceptanceChecks().completed();result=check.evaluate(request)
        self.assertNotIn('current_request',result);self.assertEqual(result['status'],'ready')

    def test_new_relation_shapes_return_structured_invalid(self):
        import io,contextlib,json
        import codex_framework as framework
        for value in (None,[],False,{}, {'input_ref':[]},{'input_ref':'request:current','report_ref':{}},{'input_ref':'x','unknown':1}):
            request=self.bound();request['progress']['current_request']=value;output,error=io.StringIO(),io.StringIO()
            with self.subTest(value=value),contextlib.redirect_stdout(output),contextlib.redirect_stderr(error),unittest.mock.patch('sys.stdin',io.StringIO(json.dumps(request))):
                self.assertEqual(framework.main(['check']),1)
            self.assertEqual(json.loads(output.getvalue())['status'],'invalid');self.assertEqual(error.getvalue(),'')

    def test_report_root_resource_and_native_exit_type_are_not_overridden_by_labels(self):
        import json
        for variant in ('root_resource','false_exit','duplicate_json','non_json_constant'):
            request=self.bound();record=next(r for r in request['context']['evidence'] if r['ref']=='report:current')
            item=check.native_item(request['context'],record)
            if variant=='root_resource':
                ActiveOpportunityChecks().rewrite(request,record,json.dumps({'version':'commit:abc','outcome':'passed',
                    'resource':{'kind':'repo','id':'framework','path':'scripts/wrong'}}))
            elif variant=='false_exit':item['exitCode']=False
            elif variant=='duplicate_json':ActiveOpportunityChecks().rewrite(request,record,'{"version":"old","version":"commit:abc","outcome":"passed"}')
            else:ActiveOpportunityChecks().rewrite(request,record,'{"version":"commit:abc","outcome":"passed","other":NaN}')
            with self.subTest(variant=variant):
                result=check.evaluate(request);self.assertIn('current_request_report_unverified',result['missing']);self.assertFalse(result['stop_allowed'])

    def test_supplied_failure_for_frozen_request_cannot_hide_behind_a_selected_pass(self):
        request=self.bound();self.add_report(request,ref='report:failed',body={'version':'commit:abc','outcome':'failed'})
        result=check.evaluate(request)
        self.assertIn('current_request_failure_unresolved',result['deviations'])
        self.assertFalse(result['stop_allowed'])

    def test_missing_report_and_unknown_request_order_keep_specific_gaps(self):
        request=self.bound();request['progress']['current_request'].pop('report_ref')
        self.assertIn('current_request_report_unverified',check.evaluate(request)['missing'])
        request=self.bound();source=request['context']['native_sources'][0];source['turns'].append({'items':[]})
        item=next(x for x in source['turns'][0]['items'] if x['id']=='request:current');source['turns'][0]['items'].remove(item);source['turns'][-1]['items'].append(item)
        self.assertIn('current_request_review_unverified',check.evaluate(request)['missing'])


class RequestRevisionChecks(unittest.TestCase):
    """Verified replacement chains and independent pending work, offline only."""
    def revised(self, depth=1):
        helper=CurrentRequestChecks();request=helper.bound()
        child=next(r for r in request['context']['evidence'] if r['ref']=='request:current')
        source=request['context']['native_sources'][0]['turns'][0]['items']
        for n in range(depth):
            parent,item=helper.add_request(request,ref='request:old-'+str(n),version='manifest:old-'+str(n))
            child['related_ref']=parent['ref']
            ActiveOpportunityChecks().rewrite(request,child,'<codex_delegation><source_thread_id>coordinator</source_thread_id><input>Replace '+parent['ref']+' with '+child['version']+'</input></codex_delegation>')
            source.remove(item);source.insert(next(i for i,x in enumerate(source) if x['id']==child['native_item_id']),item)
            child=parent
        return request

    def test_reselection_completes_without_unexecuted_obsolete_reports(self):
        for depth in (1,3):
            request=self.revised(depth);result=check.evaluate(request)
            with self.subTest(depth=depth):
                self.assertEqual(result['status'],'ready');self.assertTrue(result['stop_allowed'])
                self.assertEqual(result['current_request']['pending_request_refs'],[])
                self.assertEqual(result['current_request']['superseded_request_refs'],['request:old-'+str(n) for n in range(depth)])

    def test_unrelated_newer_same_resource_and_version_stays_pending(self):
        request=self.revised();CurrentRequestChecks().add_request(request,ref='request:parallel',version='commit:abc',after_review=True)
        result=check.evaluate(request)
        self.assertEqual(result['current_request']['pending_request_refs'],['request:parallel'])
        self.assertIn('other_supplied_requests_unresolved',result['missing']);self.assertFalse(result['stop_allowed'])
        self.assertNotIn('request:parallel',result['current_request']['superseded_request_refs'])

    def test_missing_source_or_order_does_not_clear_a_replacement_chain(self):
        for variant in ('missing_parent','digest','provider','new_before_old','unknown_page_order','cycle','wrong_owner','unknown_origin'):
            request=self.revised(2);records={r['ref']:r for r in request['context']['evidence']};old=records['request:old-1'];current=records['request:current'];source=request['context']['native_sources'][0];items=source['turns'][0]['items'];item=next(x for x in items if x['id']==old['native_item_id'])
            if variant=='missing_parent':request['context']['evidence'].remove(old)
            elif variant=='digest':old['digest']='0'*64
            elif variant=='provider':item['namespace']='other_provider'
            elif variant=='new_before_old':items.remove(item);items.append(item)
            elif variant=='unknown_page_order':items.remove(item);source['turns'].append({'items':[item]});source.pop('page',None)
            elif variant=='cycle':old['related_ref']=current['ref']
            elif variant=='wrong_owner':old['task_ref']='codex:thread:other'
            else:old['related_ref']='request:missing-origin'
            with self.subTest(variant=variant):
                result=check.evaluate(request)
                self.assertIn('current_request_revision_unverified',result['missing']);self.assertFalse(result['stop_allowed'])
                self.assertNotIn('superseded_request_refs',result['current_request'])

    def test_newer_input_without_explicit_relation_never_retires_old(self):
        request=self.revised();current=next(r for r in request['context']['evidence'] if r['ref']=='request:current');current['related_ref']=request['progress']['goal_ref']
        result=check.evaluate(request)
        self.assertEqual(result['current_request']['pending_request_refs'],['request:old-0'])
        self.assertFalse(result['stop_allowed']);self.assertNotIn('superseded_request_refs',result['current_request'])

    def test_old_report_cannot_be_returned_as_the_new_request_report(self):
        request=self.revised();CurrentRequestChecks().add_report(request,selected='request:old-0',ref='report:old')
        request['progress']['current_request']['report_ref']='report:old'
        result=check.evaluate(request)
        self.assertIn('current_request_report_unverified',result['missing']);self.assertFalse(result['stop_allowed'])
        self.assertEqual(result['current_request']['pending_request_refs'],[])

    def test_superseded_failure_is_retained_with_new_version_success(self):
        import json
        request=self.revised();CurrentRequestChecks().add_report(request,selected='request:old-0',ref='report:old-failed',body={'version':'manifest:old-0','outcome':'failed'})
        before=json.dumps(request,sort_keys=True);result=check.evaluate(request)
        self.assertEqual(result['status'],'ready');self.assertTrue(result['stop_allowed'])
        self.assertEqual(result['current_request']['superseded_failed_request_refs'],['request:old-0'])
        self.assertEqual(json.dumps(request,sort_keys=True),before)

    def test_same_frozen_version_failure_is_not_erased_by_new_input_ref(self):
        request=self.revised();old=next(r for r in request['context']['evidence'] if r['ref']=='request:old-0');old['version']='commit:abc'
        ActiveOpportunityChecks().rewrite(request,old,'<codex_delegation><source_thread_id>coordinator</source_thread_id><input>Review commit:abc</input></codex_delegation>')
        CurrentRequestChecks().add_report(request,selected=old['ref'],ref='report:old-failed',body={'version':'commit:abc','outcome':'failed'})
        result=check.evaluate(request)
        self.assertIn('current_request_failure_unresolved',result['deviations']);self.assertFalse(result['stop_allowed'])
        self.assertEqual(result['current_request']['superseded_failed_request_refs'],[old['ref']])

    def test_completed_revised_parallel_branch_does_not_leave_its_old_input_pending(self):
        request=self.revised();helper=CurrentRequestChecks()
        helper.add_request(request,ref='request:parallel',version='manifest:parallel',after_review=True)
        helper.add_report(request,selected='request:parallel',ref='report:parallel')
        request['progress']['current_request']={'input_ref':'request:parallel','report_ref':'report:parallel'}
        request['progress']['claim']='working'
        result=check.evaluate(request)
        self.assertEqual(result['current_request']['state'],'report_matched')
        self.assertEqual(result['current_request']['pending_request_refs'],[])
        self.assertEqual(result['current_request']['superseded_request_refs'],['request:old-0'])

    def test_unreported_replacement_branch_itself_stays_pending(self):
        request=self.revised();helper=CurrentRequestChecks();helper.add_request(request,ref='request:parallel',version='manifest:parallel',after_review=True);helper.add_report(request,selected='request:parallel',ref='report:parallel')
        request['progress']['current_request']={'input_ref':'request:parallel','report_ref':'report:parallel'}
        request['progress']['claim']='working'
        request['context']['evidence']=[r for r in request['context']['evidence'] if r['ref']!='report:current']
        result=check.evaluate(request)
        self.assertEqual(result['current_request']['pending_request_refs'],['request:current'])
        self.assertFalse(result['stop_allowed'])

    def test_replacement_fork_is_not_resolved_by_latest_time(self):
        request=self.revised();CurrentRequestChecks().add_request(request,ref='request:fork',version='manifest:fork',after_review=True,related='request:old-0')
        result=check.evaluate(request)
        self.assertEqual(result['current_request']['pending_request_refs'],['request:fork'])
        self.assertFalse(result['stop_allowed'])

    def test_selecting_superseded_middle_of_chain_remains_rejected(self):
        request=self.revised(2);helper=CurrentRequestChecks();helper.add_report(request,selected='request:old-0',ref='report:middle')
        request['progress']['current_request']={'input_ref':'request:old-0','report_ref':'report:middle'}
        result=check.evaluate(request)
        self.assertIn('current_request_superseded',result['missing']);self.assertFalse(result['stop_allowed'])


if __name__ == "__main__":
    unittest.main()
