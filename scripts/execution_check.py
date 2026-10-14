"""Stateless checks over references to native tasks and node observations.

The caller reads the original records with the tools available to its task.  This
module checks the supplied, short-lived envelope; it never stores a second task
history or grants permissions that the underlying platform has not granted.
"""

import json
import re
import sys

from source_evidence import verify as verify_source


class CheckError(ValueError):
    pass


def obj(value, name):
    if not isinstance(value, dict):
        raise CheckError(name + " must be an object")
    return value


def items(value, name):
    if not isinstance(value, list):
        raise CheckError(name + " must be an array")
    return value


def identifier(value, name):
    if not isinstance(value, str) or not value.strip():
        raise CheckError(name + " must be a nonempty string")
    return value


def resource(value):
    value = obj(value, "resource")
    kind = identifier(value.get("kind"), "resource.kind")
    name = identifier(value.get("id"), "resource.id")
    path = value.get("path", "")
    if not isinstance(path, str) or path.startswith("/") or any(
            part in ("", ".", "..") for part in path.split("/") if path):
        raise CheckError("resource.path must be a relative canonical path")
    return kind, name, path.rstrip("/")


def covers(scope, target, write):
    scope = obj(scope, "scope")
    base = resource(scope)
    access = scope.get("access")
    if access not in ("read", "write"):
        raise CheckError("scope.access must be read or write")
    return (base[:2] == target[:2]
            and (not base[2] or target[2] == base[2] or target[2].startswith(base[2] + "/"))
            and (access == "write" or not write))


def overlaps(left, right):
    return (left[:2] == right[:2] and
            (not left[2] or not right[2] or left[2] == right[2]
             or left[2].startswith(right[2] + "/")
             or right[2].startswith(left[2] + "/")))


def evaluate(payload):
    """Return an advisory decision. Missing facts never become success."""
    payload = obj(payload, "input")
    project = obj(payload.get("project"), "project")
    task = obj(payload.get("task"), "task")
    context = obj(payload.get("context"), "context")
    project_id = identifier(project.get("id"), "project.id")
    task_ref = identifier(task.get("native_ref"), "task.native_ref")
    if task.get("project_id") != project_id:
        raise CheckError("task.project_id does not match project.id")
    if not task_ref.startswith("codex:thread:"):
        raise CheckError("task.native_ref must reference the original Codex thread")
    project_scopes = items(project.get("scopes"), "project.scopes")
    task_scopes = items(task.get("scopes"), "task.scopes")
    for scope in project_scopes + task_scopes:
        covers(scope, resource(scope), False)
    phase = payload.get("phase", "action")
    if phase not in ("resume", "action", "feedback", "close_issue"):
        raise CheckError("phase must be resume, action, feedback or close_issue")
    blocked, missing, deviations = [], [], []

    def block(code):
        if code not in blocked:
            blocked.append(code)

    def need(code):
        if code not in missing:
            missing.append(code)

    def deviate(code):
        if code not in deviations:
            deviations.append(code)

    if context.get("task_ref") != task_ref:
        block("resume_original_task")
    if not project.get("preferences_ref"):
        need("project_preferences_reference")
    evidence = {}
    for record in items(context.get("evidence", []), "context.evidence"):
        record = obj(record, "evidence")
        ref = identifier(record.get("ref"), "evidence.ref")
        if ref in evidence or record.get("source") not in ("codex", "node"):
            raise CheckError("evidence references must be unique and name a native or node source")
        if set(record) - {"ref", "source", "kind", "project_id", "task_ref", "version",
                           "result", "related_ref", "digest", "resource", "native_item_id",
                           "node_path"}:
            raise CheckError("evidence may contain only references and outcome metadata")
        evidence[ref] = record

    action_input = payload.get("action")
    strict_sources = (phase in ("resume", "feedback", "close_issue")
                      or (phase == "action" and isinstance(action_input, dict)
                          and (action_input.get("kind") in ("repair", "publish")
                               or "recovery" in action_input)))

    def linked(ref, kind):
        record = evidence.get(ref)
        if (record is None or record.get("kind") != kind
                or record.get("project_id") != project_id
                or record.get("task_ref") != task_ref):
            need("unresolved_" + kind + "_reference")
            return None
        if strict_sources and not verify_source(context, project, record):
            need("unverified_" + kind + "_source")
            return None
        return record

    def check_corrections(target, selected):
        selected = items(selected, "selected accepted corrections")
        corrections = items(project.get("accepted_corrections", []), "project.accepted_corrections")
        if corrections and not project.get("corrections_source_ref"):
            need("accepted_corrections_source")
        for correction in corrections:
            correction = obj(correction, "accepted correction")
            if overlaps(resource(correction.get("resource")), target):
                ref = identifier(correction.get("ref"), "accepted correction.ref")
                version = identifier(correction.get("version"), "accepted correction.version")
                matching = [entry for entry in selected if isinstance(entry, dict) and entry.get("ref") == ref]
                if not matching:
                    need("accepted_correction_not_used")
                elif any(entry.get("version") != version for entry in matching):
                    block("stale_correction_version")
                source_record = evidence.get(ref)
                if (source_record is None or source_record.get("project_id") != project_id
                        or source_record.get("kind") != "fix"
                        or source_record.get("version") != version
                        or not verify_source(context, project, source_record)):
                    need("accepted_correction_unverified")

    if phase == "resume":
        resume = obj(payload.get("resume"), "resume")
        target = resource(resume.get("resource"))
        if (not any(covers(scope, target, False) for scope in project_scopes)
                or not any(covers(scope, target, False) for scope in task_scopes)):
            block("resume_outside_scope")
        linked(resume.get("goal_ref"), "goal")
        check_corrections(target, resume.get("accepted_corrections", []))
    elif phase == "feedback":
        feedback = obj(payload.get("feedback"), "feedback")
        target = resource(feedback.get("resource"))
        if (not any(covers(scope, target, False) for scope in project_scopes)
                or not any(covers(scope, target, False) for scope in task_scopes)):
            block("feedback_outside_task_scope")
        expected = linked(feedback.get("expected_ref"), "expectation")
        observed = linked(feedback.get("observed_ref"), "observation")
        if expected and observed:
            for fact in (expected, observed):
                if resource(fact.get("resource")) != target:
                    block("feedback_resource_mismatch")
                if not isinstance(fact.get("digest"), str) or not re.fullmatch(r"[0-9a-f]{64}", fact["digest"]):
                    raise CheckError("feedback digest must be a SHA-256 reference to the original value")
            if expected["digest"] != observed["digest"]:
                deviate("observed_result_differs_from_expectation")
                prior_ref = feedback.get("prior_correction_ref")
                if prior_ref:
                    prior = evidence.get(prior_ref)
                    if (prior is None or prior.get("kind") != "fix"
                            or prior.get("project_id") != project_id
                            or not verify_source(context, project, prior)):
                        need("unverified_prior_correction")
                    else:
                        deviate("correction_ineffective_or_regressed")
    elif phase == "action":
        action = obj(payload.get("action"), "action")
        kind = action.get("kind")
        if kind not in ("read", "write", "invoke", "repair", "publish"):
            raise CheckError("action.kind is invalid")
        write = kind != "read"
        target = resource(action.get("resource"))
        if not any(covers(scope, target, write) for scope in project_scopes):
            block("outside_project_scope")
        if not any(covers(scope, target, write) for scope in task_scopes):
            block("outside_task_scope")
        check_corrections(target, action.get("accepted_corrections", []))
        required = items(action.get("capabilities", []), "action.capabilities")
        available = items(context.get("capabilities", []), "context.capabilities")
        if any(capability not in available for capability in required):
            need("execution_capability")
        cost = action.get("cost", 0)
        if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
            raise CheckError("action.cost must be nonnegative")
        if cost:
            remaining = context.get("remaining_budget")
            if isinstance(remaining, bool) or not isinstance(remaining, (int, float)):
                need("budget_fact")
            elif cost > remaining:
                block("budget_exceeded")
        if (required or cost) and not context.get("node_snapshot_ref"):
            need("node_runtime_snapshot")
        observations = items(context.get("native_operations", []), "context.native_operations")
        by_ref = {}
        for record in observations:
            record = obj(record, "native operation")
            ref = identifier(record.get("ref"), "native operation.ref")
            if ref in by_ref:
                raise CheckError("duplicate native operation reference")
            if set(record) - {"ref", "project_id", "task_ref", "effect_key", "resource", "state"}:
                raise CheckError("native operations may contain only references and outcome metadata")
            by_ref[ref] = record
        for dependency in items(action.get("depends_on", []), "action.depends_on"):
            if by_ref.get(dependency, {}).get("state") != "succeeded":
                need("dependency_unverified")
        if write:
            effect_key = identifier(action.get("effect_key"), "action.effect_key")
            if not context.get("native_snapshot_ref") or context.get("native_snapshot_complete") is not True:
                need("native_operations_snapshot")
            for record in observations:
                state = record.get("state")
                if state not in ("succeeded", "running", "failed", "unknown"):
                    raise CheckError("native operation.state is invalid")
                if record.get("project_id") != project_id:
                    continue
                if record.get("effect_key") == effect_key:
                    if state in ("succeeded", "running", "unknown"):
                        block("duplicate_or_uncertain_effect")
                    elif state == "failed":
                        recovery = obj(action.get("recovery", {}), "action.recovery")
                        if record.get("task_ref") != task_ref:
                            block("resume_original_task_for_recovery")
                        if (recovery.get("source_ref") != record["ref"]
                                or not recovery.get("cause_ref")
                                or not recovery.get("correction_ref")):
                            need("failed_operation_recovery")
                        else:
                            linked(recovery["cause_ref"], "cause")
                            correction = linked(recovery["correction_ref"], "correction")
                            if correction is None or correction.get("result") != "verified":
                                need("verified_correction")
                if (state in ("running", "unknown") and record.get("task_ref") != task_ref
                        and record.get("resource") is not None
                        and overlaps(resource(record["resource"]), target)):
                    block("concurrent_resource_conflict")
        failure = context.get("tool_failure")
        if failure is not None:
            failure = obj(failure, "context.tool_failure")
            if (failure.get("kind") == "context_unavailable"
                    and action.get("assertion") == "capability_missing"):
                block("tool_context_is_not_capability_absence")
        if kind == "repair":
            defect = obj(action.get("defect", {}), "action.defect")
            if not defect.get("failure_ref") or not defect.get("cause_ref"):
                need("root_cause_evidence")
            else:
                linked(defect["failure_ref"], "failure")
                linked(defect["cause_ref"], "cause")
            if defect.get("cause_layer") not in (
                    "rules", "execution", "implementation", "data", "environment", "tool_context"):
                need("root_cause_layer")
            authorization = linked(defect.get("authorization_ref"), "authorization")
            if authorization is None or authorization.get("result") != "granted":
                need("repair_authorization")
            affected = items(defect.get("affected_resources", []), "action.defect.affected_resources")
            if not affected:
                need("affected_scope")
            for entry in affected:
                candidate = resource(entry)
                if (not any(covers(scope, candidate, False) for scope in project_scopes)
                        or not any(covers(scope, candidate, False) for scope in task_scopes)):
                    block("affected_scope_outside_task")
            if (failure and failure.get("kind") == "context_unavailable"
                    and defect.get("cause_layer") == "implementation"):
                block("tool_context_is_not_implementation_defect")
        if kind == "publish":
            release = obj(action.get("release", {}), "action.release")
            test = linked(release.get("test_ref"), "release_test")
            if (not release.get("version") or test is None
                    or test.get("version") != release.get("version")
                    or test.get("result") != "passed"):
                need("tested_release_version")
    else:
        issue = obj(payload.get("issue"), "issue")
        if issue.get("task_ref") != task_ref or issue.get("project_id") != project_id:
            block("issue_owner_mismatch")
        if issue.get("resource") is None:
            need("issue_resource")
        else:
            target = resource(issue["resource"])
            if (not any(covers(scope, target, True) for scope in project_scopes)
                    or not any(covers(scope, target, True) for scope in task_scopes)):
                block("issue_outside_scope")

        for key in ("goal_ref", "failure_ref", "cause_ref", "fix_ref", "acceptance_ref"):
            if not issue.get(key):
                need(key)
        for key, kind in (("goal_ref", "goal"), ("failure_ref", "failure"),
                          ("cause_ref", "cause"), ("fix_ref", "fix"),
                          ("acceptance_ref", "acceptance")):
            if issue.get(key):
                linked(issue[key], kind)
        regression = obj(issue.get("regression", {}), "issue.regression")
        adoption = obj(issue.get("adoption", {}), "issue.adoption")
        effect = obj(issue.get("effect", {}), "issue.effect")
        followup = obj(issue.get("followup", {}), "issue.followup")
        for key in ("original_ref", "affected_ref", "version"):
            if not regression.get(key):
                need("regression_" + key)
        original = linked(regression.get("original_ref"), "regression_original")
        affected = linked(regression.get("affected_ref"), "regression_affected")
        if (regression.get("result") != "passed" or original is None or affected is None
                or original.get("result") != "passed" or affected.get("result") != "passed"
                or original.get("related_ref") != issue.get("failure_ref")
                or original.get("version") != regression.get("version")
                or affected.get("version") != regression.get("version")):
            need("regression_passed")
        applied = linked(adoption.get("ref"), "adoption")
        if (adoption.get("version") != regression.get("version") or applied is None
                or applied.get("version") != regression.get("version")
                or applied.get("result") != "applied"):
            need("adopted_tested_version")
        observed = linked(effect.get("ref"), "effect")
        if (effect.get("result") != "observed" or observed is None
                or observed.get("result") != "observed"
                or observed.get("version") != regression.get("version")):
            need("observed_effect")
        acceptance = linked(issue.get("acceptance_ref"), "acceptance")
        if (acceptance is None or acceptance.get("result") != "passed"
                or acceptance.get("version") != regression.get("version")
                or acceptance.get("related_ref") != issue.get("goal_ref")
                or issue.get("acceptance_ref") in (regression.get("original_ref"),
                                                    regression.get("affected_ref"))):
            need("goal_acceptance_beyond_smoke_test")
        continuation = linked(followup.get("ref"), "followup")
        if (continuation is None or continuation.get("result") != "used_correction"
                or continuation.get("version") != regression.get("version")
                or continuation.get("related_ref") != issue.get("fix_ref")):
            need("followup_used_correction")
        if not issue.get("acceptance_owner") or not issue.get("followup_owner"):
            need("completion_responsibility")

    status = ("blocked" if blocked else "needs_evidence" if missing else
              "deviation" if deviations else "ready")
    return {"status": status, "blocked": blocked, "missing": missing, "deviations": deviations,
            "meaning": "source_excerpt_and_input_consistency_only; provenance, authorization and goal semantics remain external"}


def dispatch():
    try:
        result = evaluate(json.load(sys.stdin))
    except (CheckError, ValueError, TypeError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready" else 2


if __name__ == "__main__":
    sys.exit(dispatch())
