"""Lost-reply safety through the real check entry, with isolated local processes.

Native envelopes here are synthetic. A subprocess/pipe test exercises actual
local I/O loss, not a workstation VPN, platform reconnect or automatic wake-up.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

sys.path[:0] = [str(Path(__file__).resolve().parents[1] / "scripts"),
                str(Path(__file__).resolve().parent)]
import execution_check as check
from test_execution_check import action_case

ROOT = Path(__file__).resolve().parents[1]


def disconnected_case():
    request = action_case()
    request["context"]["tool_failures"] = [{
        "kind": "transport_disconnected", "ref": "codex:failure:lost-reply",
        "operation_ref": "codex:operation:original", "project_id": "framework",
        "task_ref": request["task"]["native_ref"], "execution_node": "synthetic-node",
        "resource": copy.deepcopy(request["action"]["resource"]),
        "action_kind": "write", "effect_key": request["action"]["effect_key"]}]
    return request


def reconcile(request, state, ref="codex:status:original"):
    failure = request["context"]["tool_failures"][0]
    statement = {key: copy.deepcopy(failure[key]) for key in
                 ("operation_ref", "project_id", "task_ref", "execution_node",
                  "resource", "action_kind", "effect_key")}
    statement.update(state=state, terminal=state in ("not_executed", "succeeded", "failed"))
    if state in ("succeeded", "failed"):
        statement["result_ref"] = "node:result:original"
    record = {"ref": ref, "source": "codex", "kind": "operation_status",
              "project_id": failure["project_id"], "task_ref": failure["task_ref"],
              "resource": copy.deepcopy(failure["resource"]), "result": "observed",
              "related_ref": failure["operation_ref"], "native_item_id": ref}
    item = {"id": ref, "type": "commandExecution", "status": "completed", "exitCode": 0,
            "command": "read original operation status", "output": {"truncated": False}}
    bind_body(record, item, statement)
    request["context"].setdefault("evidence", []).append(record)
    request["context"].setdefault("native_sources", [{"schemaVersion": 1,
        "thread": {"id": failure["task_ref"].removeprefix("codex:thread:")},
        "turns": [{"id": "turn:read", "items": []}]}])[0]["turns"][0]["items"].append(item)
    failure.setdefault("reconciliation_refs", []).append(ref)
    return record, item, statement


def bind_body(record, item, statement):
    body = json.dumps(statement)
    record["digest"] = hashlib.sha256(body.encode()).hexdigest()
    item["output"]["text"] = body


class ConnectionRecoveryTests(unittest.TestCase):
    def test_lost_reply_without_operation_index_cannot_be_replayed(self):
        request = disconnected_case()
        original = copy.deepcopy(request)
        result = check.evaluate(request)
        self.assertIn("disconnected_operation_unresolved", result["blocked"])
        self.assertEqual(result["connection_recovery"][0]["state"], "unknown")
        self.assertEqual(result["connection_recovery"][0]["failure_layer"], "execution_transport")
        self.assertEqual(request, original)

    def test_pre_submission_terminal_non_execution_rechecks_normal_prerequisites(self):
        request = disconnected_case()
        reconcile(request, "not_executed")
        self.assertEqual(check.evaluate(request)["status"], "ready")
        request["context"]["capabilities"] = []
        self.assertIn("execution_capability", check.evaluate(request)["missing"])

    def test_received_running_finished_and_unknown_are_not_resubmission(self):
        for state, next_action in [("running", "observe_original_operation"),
                                   ("succeeded", "consume_original_result"),
                                   ("failed", "diagnose_original_failure"),
                                   ("unknown", "reconcile_original_operation")]:
            with self.subTest(state=state):
                request = disconnected_case()
                reconcile(request, state)
                result = check.evaluate(request)
                self.assertNotEqual(result["status"], "ready")
                self.assertEqual(result["connection_recovery"][0]["next_action"], next_action)

    def test_connection_rebuilt_does_not_resolve_missing_original_session(self):
        request = disconnected_case()
        request["context"]["tool_failures"][0]["kind"] = "session_unavailable"
        request["context"]["capabilities"] += ["network_online", "new_connection_ready"]
        result = check.evaluate(request)
        self.assertIn("disconnected_operation_unresolved", result["blocked"])
        self.assertEqual(result["connection_recovery"][0]["failure_layer"], "execution_session")

    def test_read_and_unrelated_work_continue_in_existing_progress(self):
        request = disconnected_case()
        request["action"]["capabilities"] = []
        read = {"kind": "read", "resource": request["action"]["resource"]}
        independent = copy.deepcopy(request["action"])
        independent.update(effect_key="independent-fix", capabilities=[])
        independent["resource"]["path"] = "scripts/independent.py"
        request.update(phase="progress", progress={"checkpoint": "after_failure", "claim": "working",
            "steps": [{"id": "original", "action": request["action"]},
                      {"id": "reconcile", "action": read},
                      {"id": "independent", "action": independent}]})
        result = check.evaluate(request)
        self.assertEqual(result["work_partition"]["ready_step_ids"], ["reconcile", "independent"])
        self.assertEqual(result["work_partition"]["blocked_step_ids"], ["original"])
        self.assertFalse(result["wait_allowed"])
        self.assertEqual(result["steps"][0]["connection_recovery"][0]["state"], "unknown")

    def test_denial_and_user_stop_survive_online_and_proven_non_execution(self):
        for kind, reason in [("platform_denied", "platform_denied_action"),
                             ("user_cancelled", "user_cancelled_action")]:
            with self.subTest(kind=kind):
                request = disconnected_case()
                reconcile(request, "not_executed")
                # The prior refusal must survive the later transport record.
                request["context"]["tool_failure"] = {"kind": kind, "action_kind": "write",
                    "effect_key": request["action"]["effect_key"], "resource": request["action"]["resource"]}
                self.assertIn(reason, check.evaluate(request)["blocked"])

    def test_online_node_without_action_grant_does_not_gain_permission(self):
        from test_execution_check import WorkspaceBoundaryChecks
        request = WorkspaceBoundaryChecks().local_case()
        failure = disconnected_case()["context"]["tool_failures"][0]
        failure.update(resource=copy.deepcopy(request["action"]["resource"]),
                       effect_key=request["action"]["effect_key"])
        request["context"]["tool_failures"] = [failure]
        reconcile(request, "not_executed")
        request["action"]["workspace"].pop("authorization_ref", None)
        result = check.evaluate(request)
        self.assertNotEqual(result["status"], "ready")
        self.assertIn("local_action_authorization", result["missing"])

    def test_sender_or_effect_rename_cannot_replay_unknown_overlapping_work(self):
        request = disconnected_case()
        request["task"]["native_ref"] = request["context"]["task_ref"] = "codex:thread:another"
        request["action"]["effect_key"] = "renamed-effect"
        self.assertIn("disconnected_operation_unresolved", check.evaluate(request)["blocked"])

    def test_duplicate_reordered_status_reads_do_not_choose_first_or_last(self):
        request = disconnected_case()
        reconcile(request, "succeeded", "codex:status:one")
        reconcile(request, "succeeded", "codex:status:two")
        refs = request["context"]["tool_failures"][0]["reconciliation_refs"]
        refs.append(refs[0])
        result = check.evaluate(request)
        refs.reverse()
        self.assertEqual(check.evaluate(request), result)
        self.assertIn("duplicate_or_uncertain_effect", result["blocked"])
        reconcile(request, "not_executed", "codex:status:late-stale")
        result = check.evaluate(request)
        self.assertIn("original_operation_status_conflict", result["missing"])
        self.assertEqual(result["connection_recovery"][0]["state"], "unknown")
        refs.reverse()
        self.assertEqual(check.evaluate(request), result)

    def test_terminal_status_cannot_discard_other_unknown_native_operations(self):
        request = disconnected_case()
        reconcile(request, "not_executed")
        failure = request["context"]["tool_failures"][0]
        request["context"]["native_operations"] = [{"ref": failure["operation_ref"],
            "project_id": failure["project_id"], "task_ref": failure["task_ref"],
            "resource": failure["resource"], "effect_key": failure["effect_key"], "state": "unknown"}]
        self.assertIn("duplicate_or_uncertain_effect", check.evaluate(request)["blocked"])

    def test_status_must_bind_original_operation_node_and_complete_authoritative_read(self):
        for field, value in [("operation_ref", "another"), ("execution_node", "another"),
                             ("task_ref", "another"), ("effect_key", "another"),
                             ("project_id", "another"), ("action_kind", "read"),
                             ("terminal", False), ("state", "not_found"),
                             ("resource", {"kind": "repo", "id": "another"})]:
            with self.subTest(field=field):
                request = disconnected_case()
                record, item, body = reconcile(request, "not_executed")
                body[field] = value
                bind_body(record, item, body)
                self.assertIn("disconnected_operation_unresolved", check.evaluate(request)["blocked"])
        for mutation in ("truncated", "failed_read", "agent_claim", "missing_digest", "duplicate_keys"):
            with self.subTest(mutation=mutation):
                request = disconnected_case()
                record, item, body = reconcile(request, "not_executed")
                if mutation == "truncated": item["output"]["truncated"] = True
                if mutation == "failed_read": item["exitCode"] = 1
                if mutation == "agent_claim": item["type"] = "agentMessage"
                if mutation == "missing_digest": record.pop("digest")
                if mutation == "duplicate_keys":
                    text = item["output"]["text"][:-1] + ', "state":"not_executed"}'
                    item["output"]["text"] = text
                    record["digest"] = hashlib.sha256(text.encode()).hexdigest()
                self.assertIn("disconnected_operation_unresolved", check.evaluate(request)["blocked"])

    def test_local_process_finishes_after_reply_pipe_is_lost_without_second_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            # This process represents one authorized effect. Closing its response
            # pipe does not cancel it; stdin is an isolated completion handshake.
            worker = """import json, pathlib, sys
root = pathlib.Path(sys.argv[1])
(root / 'effects').write_text('one execution')
print('started', flush=True)
sys.stdin.readline()
(root / 'result').write_text('succeeded')
try:
    print('done', flush=True)
except BrokenPipeError:
    pass
"""
            with subprocess.Popen([sys.executable, "-c", worker, directory], stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True) as process:
                self.assertEqual(process.stdout.readline().strip(), "started")
                process.stdout.close()
                request = disconnected_case()
                record, item, body = reconcile(request, "running")
                self.assertIsNone(process.poll())
                self.assertIn("disconnected_operation_unresolved", check.evaluate(request)["blocked"])
                process.stdin.write("finish\n")
                process.stdin.flush()
                process.wait(timeout=10)
            self.assertEqual((root / "result").read_text(), "succeeded")
            # The status read is real local I/O. Only its native wrapper is a
            # fixture; it cannot certify the original workstation's state.
            body.update(state="succeeded", terminal=True, result_ref=str(root / "result"))
            status = subprocess.run([sys.executable, "-c",
                "import pathlib,sys; p=pathlib.Path(sys.argv[1]); assert p.read_text()=='succeeded'; print(sys.argv[2])",
                str(root / "result"), json.dumps(body)], capture_output=True, text=True, timeout=10)
            self.assertEqual(status.returncode, 0)
            item["output"]["text"] = status.stdout
            record["digest"] = hashlib.sha256(status.stdout.encode()).hexdigest()
            for _ in range(2):
                checked = subprocess.run([sys.executable, str(ROOT / "scripts/codex_framework.py"), "check"],
                    input=json.dumps(request), capture_output=True, text=True, timeout=10)
                self.assertEqual(checked.returncode, 2, checked.stderr)
                result = json.loads(checked.stdout)
                self.assertEqual(result["connection_recovery"][0]["next_action"], "consume_original_result")
            self.assertEqual((root / "effects").read_text(), "one execution")


if __name__ == "__main__":
    unittest.main()
