"""A complete correction round trip in an isolated, generic project."""

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


CLI = Path(__file__).resolve().parents[1] / "scripts/codex_framework.py"
THREAD = "codex:thread:isolated-project-task"
RESOURCE = {"kind": "repo", "id": "isolated-project", "path": "rule.py"}


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def evidence(ref, kind, *, source="codex", result=None, version=None, related_ref=None,
             value_digest=None, resource=None):
    record = {"ref": ref, "source": source, "kind": kind,
              "project_id": "isolated-project", "task_ref": THREAD}
    for key, value in (("result", result), ("version", version),
                       ("related_ref", related_ref), ("digest", value_digest),
                       ("resource", resource)):
        if value is not None:
            record[key] = value
    return record


def cli(payload):
    completed = subprocess.run([sys.executable, str(CLI), "check"],
                               input=json.dumps(payload), text=True, capture_output=True,
                               timeout=5)
    return completed.returncode, json.loads(completed.stdout)


def run_rule(path, value):
    return subprocess.check_output([sys.executable, str(path), str(value)],
                                   text=True, timeout=5).strip()


def attach_native(request, ref, body, *, message=False, failed=False):
    """Wrap the subprocess value in the same item shape as native read_thread."""
    record = next(entry for entry in request["context"]["evidence"] if entry["ref"] == ref)
    record["source"] = "codex"
    record["native_item_id"] = ref
    if message:
        record["digest"] = digest(body)
        item = {"id": ref, "type": "userMessage", "text": body}
    else:
        item = {"id": ref, "type": "commandExecution", "status": "completed",
                "exitCode": 1 if failed else 0, "command": "isolated behavior probe",
                "output": {"text": body, "truncated": False}}
    request["context"]["native_sources"][0]["turns"][0]["items"].append(item)


class CorrectionLoop(unittest.TestCase):
    def test_deviation_correction_adoption_and_next_turn(self):
        with tempfile.TemporaryDirectory(prefix="framework-correction-") as directory:
            root = Path(directory)
            source = root / "source" / "rule.py"
            deployed = root / "deployed" / "rule.py"
            source.parent.mkdir()
            deployed.parent.mkdir()
            source.write_text("import sys\nv=int(sys.argv[1])\nprint('hold' if v >= 3 else 'go')\n")

            # A real command result, compared with the pre-existing boundary expectation.
            expected, actual = "go", run_rule(source, 3)
            self.assertEqual(actual, "hold")
            request = {
                "project": {"id": "isolated-project", "preferences_ref": "repo:isolated-project:AGENTS.md",
                            "scopes": [{**RESOURCE, "path": "", "access": "write"}]},
                "task": {"native_ref": THREAD, "project_id": "isolated-project",
                         "scopes": [{**RESOURCE, "access": "write"}]},
                "context": {"task_ref": THREAD, "native_snapshot_ref": "codex:read:task",
                            "native_snapshot_complete": True, "native_operations": [],
                            "native_sources": [{"schemaVersion": 1,
                                "thread": {"id": "isolated-project-task"},
                                "turns": [{"items": []}]}],
                            "evidence": [
                                evidence("node:expect:boundary", "expectation",
                                         value_digest=digest(expected), resource=RESOURCE),
                                evidence("codex:run:failure#output", "observation",
                                         value_digest=digest(actual), resource=RESOURCE)]},
                "phase": "feedback",
                "feedback": {"resource": RESOURCE, "expected_ref": "node:expect:boundary",
                             "observed_ref": "codex:run:failure#output"}}
            attach_native(request, "node:expect:boundary", expected, message=True)
            attach_native(request, "codex:run:failure#output", actual)
            code, outcome = cli(request)
            self.assertEqual((code, outcome["status"]), (2, "deviation"))
            self.assertIn("observed_result_differs_from_expectation", outcome["deviations"])
            request["context"]["evidence"][1]["resource"] = {**RESOURCE, "path": "other.py"}
            self.assertIn("feedback_resource_mismatch", cli(request)[1]["blocked"])
            request["context"]["evidence"][1]["resource"] = RESOURCE

            # Diagnose the observed boundary error and check the original task's repair scope.
            self.assertIn("v >= 3", source.read_text())
            request["context"]["evidence"] += [
                evidence("codex:run:failure", "failure"),
                evidence("codex:diagnosis:boundary", "cause")]
            attach_native(request, "codex:run:failure", actual, failed=True)
            attach_native(request, "codex:diagnosis:boundary", "v >= 3 violates the requested boundary", message=True)
            request["phase"] = "action"
            request.pop("feedback")
            request["action"] = {"kind": "repair", "resource": RESOURCE,
                                 "effect_key": "correct-boundary-3",
                                 "defect": {"failure_ref": "codex:run:failure",
                                            "cause_ref": "codex:diagnosis:boundary",
                                            "cause_layer": "implementation",
                                            "authorization_ref": "codex:user:original-request",
                                            "affected_resources": [RESOURCE]}}
            self.assertIn("repair_authorization", cli(request)[1]["missing"])
            request["context"]["evidence"].append(
                evidence("codex:user:original-request", "authorization", result="granted"))
            attach_native(request, "codex:user:original-request",
                          "original task authorizes correction in rule.py", message=True)
            request["action"]["defect"]["affected_resources"] = [
                {**RESOURCE, "path": "other-project-file.py"}]
            self.assertIn("affected_scope_outside_task", cli(request)[1]["blocked"])
            request["action"]["defect"]["affected_resources"] = [RESOURCE]
            self.assertEqual(cli(request)[1]["status"], "ready")

            # An attempted edit that leaves the original failure unchanged is not a fix.
            source.write_text(source.read_text() + "# attempted workaround\n")
            ineffective_version = digest(source.read_text())
            self.assertEqual(run_rule(source, 3), "hold")
            request["context"]["evidence"] += [
                evidence("codex:edit:ineffective", "fix", version=ineffective_version),
                evidence("codex:run:bad-fix#output", "observation",
                         value_digest=digest("hold"), resource=RESOURCE)]
            attach_native(request, "codex:edit:ineffective", ineffective_version)
            attach_native(request, "codex:run:bad-fix#output", run_rule(source, 3))
            request["phase"] = "feedback"
            request["feedback"] = {"resource": RESOURCE, "expected_ref": "node:expect:boundary",
                                   "observed_ref": "codex:run:bad-fix#output",
                                   "prior_correction_ref": "codex:edit:ineffective"}
            self.assertIn("correction_ineffective_or_regressed", cli(request)[1]["deviations"])
            request["phase"] = "action"
            request.pop("feedback")

            # Change the faulty condition, then run the original and adjacent cases.
            source.write_text(source.read_text().replace("v >= 3", "v > 3"))
            fixed_version = digest(source.read_text())
            self.assertEqual(run_rule(source, 3), "go")
            self.assertEqual([run_rule(source, v) for v in (2, 4)], ["go", "hold"])
            shutil.copy2(source, deployed)
            self.assertEqual(digest(deployed.read_text()), fixed_version)
            self.assertEqual(run_rule(deployed, 3), "go")

            # A later turn must name and use the adopted correction for this resource.
            request["project"]["accepted_corrections"] = [
                {"ref": "codex:edit:boundary", "resource": RESOURCE, "version": fixed_version}]
            request["project"]["corrections_source_ref"] = "repo:isolated-project:rule.py@" + fixed_version
            request["context"]["evidence"] += [
                evidence("codex:user:goal", "goal"),
                evidence("codex:edit:boundary", "fix", version=fixed_version)]
            attach_native(request, "codex:user:goal",
                          "value 3 returns go and adjacent values remain correct", message=True)
            attach_native(request, "codex:edit:boundary", fixed_version)
            request["phase"] = "resume"
            request["resume"] = {"resource": RESOURCE, "goal_ref": "codex:user:goal",
                                 "accepted_corrections": []}
            self.assertIn("accepted_correction_not_used", cli(request)[1]["missing"])
            request["resume"]["accepted_corrections"] = [
                {"ref": "codex:edit:boundary", "version": fixed_version}]
            self.assertEqual(cli(request)[1]["status"], "ready")
            request["resume"]["resource"] = {**RESOURCE, "path": "outside.py"}
            self.assertIn("resume_outside_scope", cli(request)[1]["blocked"])
            request["resume"]["resource"] = RESOURCE
            native_items = request["context"]["native_sources"][0]["turns"][0]["items"]
            fix_item = next(item for item in native_items if item["id"] == "codex:edit:boundary")
            native_items.remove(fix_item)
            self.assertIn("accepted_correction_unverified", cli(request)[1]["missing"])
            native_items.append(fix_item)
            request["phase"] = "action"
            request.pop("resume")
            request["action"] = {"kind": "read", "resource": RESOURCE}
            self.assertIn("accepted_correction_not_used", cli(request)[1]["missing"])
            request["action"]["accepted_corrections"] = [
                {"ref": "codex:edit:boundary", "version": "old-version"}]
            self.assertIn("stale_correction_version", cli(request)[1]["blocked"])
            request["action"]["accepted_corrections"][0]["version"] = fixed_version
            self.assertEqual(cli(request)[1]["status"], "ready")
            self.assertEqual(run_rule(deployed, 3), "go")

            request["phase"] = "close_issue"
            request.pop("action")
            request["issue"] = {"task_ref": THREAD, "project_id": "isolated-project",
                                "resource": RESOURCE, "failure_ref": "codex:run:failure",
                                "goal_ref": "codex:user:goal", "acceptance_ref": "codex:run:goal",
                                "cause_ref": "codex:diagnosis:boundary",
                                "fix_ref": "codex:edit:boundary",
                                "acceptance_owner": THREAD, "followup_owner": THREAD,
                                "regression": {"original_ref": "node:test:boundary",
                                               "affected_ref": "node:test:adjacent",
                                               "version": fixed_version, "result": "passed"},
                                "adoption": {"ref": "codex:copy:deployed", "version": fixed_version},
                                "effect": {"ref": "node:run:deployed", "result": "observed"},
                                "followup": {"ref": "codex:run:next-turn"}}
            request["context"]["evidence"] += [
                evidence("node:test:boundary", "regression_original",
                         result="passed", version=fixed_version,
                         related_ref="codex:run:failure"),
                evidence("node:test:adjacent", "regression_affected",
                         result="passed", version=fixed_version),
                evidence("codex:run:goal", "acceptance", result="passed",
                         version=fixed_version, related_ref="codex:user:goal"),
                evidence("codex:copy:deployed", "adoption", result="applied",
                         version=fixed_version),
                evidence("node:run:deployed", "effect",
                         result="observed", version=fixed_version)]
            for ref, body, message in (
                    ("node:test:boundary", "value 3: go; " + fixed_version, False),
                    ("node:test:adjacent", "values 2 and 4: go, hold; " + fixed_version, False),
                    ("codex:run:goal", "goal behavior: go, go, hold; " + fixed_version, False),
                    ("codex:copy:deployed", "copied " + fixed_version, False),
                    ("node:run:deployed", "deployed value 3: go; " + fixed_version, False)):
                attach_native(request, ref, body, message=message)
            # Fix the local acceptance input before invoking the normal CLI.
            local_output = run_rule(deployed, 3)
            local_plan = "case:boundary expected:" + digest("go") + " " + fixed_version
            request["context"]["evidence"] += [
                evidence("codex:plan:local", "local_plan", value_digest=digest(local_plan)),
                evidence("codex:expect:local", "local_expectation",
                         value_digest=digest("go"), related_ref="codex:plan:local", resource=RESOURCE),
                evidence("codex:run:local", "local_result", result="passed",
                         version=fixed_version, value_digest=digest(local_output),
                         related_ref="codex:expect:local", resource=RESOURCE)]
            attach_native(request, "codex:plan:local", local_plan, message=True)
            attach_native(request, "codex:expect:local", "go", message=True)
            attach_native(request, "codex:run:local", local_output)
            local_item = request["context"]["native_sources"][0]["turns"][0]["items"][-1]
            local_item["command"] += " " + fixed_version
            request["issue"]["local_verification"] = {
                "plan_ref": "codex:plan:local",
                "cases": [{"id": "boundary", "resource": RESOURCE,
                           "expected_ref": "codex:expect:local", "observed_ref": "codex:run:local"}]}
            self.assertIn("followup_used_correction", cli(request)[1]["missing"])
            request["context"]["evidence"].append(
                evidence("codex:run:next-turn", "followup", result="used_correction",
                         version=fixed_version, related_ref="codex:edit:boundary"))
            attach_native(request, "codex:run:next-turn", "next turn used " + fixed_version)
            self.assertEqual(cli(request)[1]["status"], "ready")

            # A later regression reopens the issue even after a prior closure.
            source.write_text(source.read_text().replace("v > 3", "v >= 3"))
            self.assertEqual(run_rule(source, 3), "hold")
            request["context"]["evidence"].append(
                evidence("codex:run:regressed#output", "observation",
                         value_digest=digest("hold"), resource=RESOURCE))
            attach_native(request, "codex:run:regressed#output", run_rule(source, 3))
            request["phase"] = "feedback"
            request["feedback"] = {"resource": RESOURCE, "expected_ref": "node:expect:boundary",
                                   "observed_ref": "codex:run:regressed#output",
                                   "prior_correction_ref": "codex:edit:boundary"}
            self.assertIn("correction_ineffective_or_regressed", cli(request)[1]["deviations"])


if __name__ == "__main__":
    unittest.main()
