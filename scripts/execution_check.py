"""Stateless checks over references to native tasks and node observations.

The caller reads the original records with the tools available to its task.  This
module checks the supplied, short-lived envelope; it never stores a second task
history or grants permissions that the underlying platform has not granted.
"""

import json
import re
import sys
from datetime import datetime, timezone

from source_evidence import native_precedes, source_text, verify as verify_source


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


def tool_failures(context):
    """Read all supplied failures; a later failure never replaces an earlier denial."""
    failures = items(context.get("tool_failures", []), "context.tool_failures")
    previous = context.get("tool_failure")
    if previous is not None:
        failures = [obj(previous, "context.tool_failure"), *failures]
    if len(failures) > 64:
        raise CheckError("too many tool failures")
    return [obj(failure, "tool failure") for failure in failures]


def denied_action_matches(failure, action, target, workspace):
    if (failure.get("kind") != "platform_denied"
            or failure.get("resource") is None
            or failure.get("action_kind") != action.get("kind")
            or not overlaps(resource(failure["resource"]), target)):
        return False
    if not isinstance(workspace, dict):
        return True  # Missing workspace facts cannot clear a refusal.
    effect = failure.get("side_effect")
    destination = failure.get("destination_node")
    return ((effect is None or effect == workspace.get("side_effect"))
            and (destination is None or destination == workspace.get("destination_node")))


def node_snapshot_fresh(context):
    """Use the source observation time, never the time it was reread."""
    observed = context.get("node_snapshot_observed_at")
    maximum = context.get("node_snapshot_max_age_seconds")
    if not isinstance(observed, str) or not isinstance(maximum, int) or not 1 <= maximum <= 86400:
        return False
    try:
        instant = datetime.fromisoformat(observed.replace("Z", "+00:00"))
    except ValueError:
        return False
    if instant.tzinfo is None:
        return False
    age = (datetime.now(timezone.utc) - instant).total_seconds()
    return -60 <= age <= maximum


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
    if phase not in ("resume", "action", "feedback", "verify_local", "close_issue", "progress"):
        raise CheckError("phase must be resume, action, feedback, verify_local, close_issue or progress")
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
                           "node_path", "node_id", "workspace"}:
            raise CheckError("evidence may contain only references and outcome metadata")
        evidence[ref] = record

    action_input = payload.get("action")
    strict_sources = (phase in ("resume", "feedback", "verify_local", "close_issue", "progress")
                      or (phase == "action" and isinstance(action_input, dict)
                          and (action_input.get("kind") in ("repair", "publish")
                               or "recovery" in action_input or "workspace" in action_input
                               or "incident" in context)))

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

    def check_local_verification(validation, target, version):
        """Check fixed expectations against manually observed local outputs."""
        if not isinstance(validation, dict):
            need("local_verification_plan")
            need("local_verification_results")
            return
        plan_ref = validation.get("plan_ref")
        plan = linked(plan_ref, "local_plan")
        plan_text = source_text(context, plan) if plan and plan.get("source") == "codex" else None
        if not plan_text or not version or version not in plan_text:
            need("local_verification_plan")
        cases = items(validation.get("cases", []), "local_verification.cases")
        if not cases:
            need("local_verification_cases")
            need("local_verification_results")
        seen = set()
        for entry in cases:
            case = obj(entry, "local verification case")
            case_id = identifier(case.get("id"), "local verification case.id")
            if case_id in seen:
                raise CheckError("local verification case IDs must be unique")
            seen.add(case_id)
            candidate = resource(case.get("resource"))
            if (not any(covers(scope, candidate, False) for scope in project_scopes)
                    or not any(covers(scope, candidate, False) for scope in task_scopes)):
                block("local_verification_outside_scope")
            if not overlaps(candidate, target):
                need("local_verification_unrelated_case")
            expected = linked(case.get("expected_ref"), "local_expectation")
            observed = linked(case.get("observed_ref"), "local_result")
            expected_digest = expected.get("digest") if expected else None
            if (not isinstance(expected_digest, str)
                    or not re.fullmatch(r"[0-9a-f]{64}", expected_digest)
                    or not plan_text or not re.search(
                        r"(?:^|\n)case:" + re.escape(case_id) +
                        r"\s+expected:" + expected_digest + r"(?:\s|$)", plan_text)):
                need("local_case_not_fixed_in_plan")
            if (expected is None or observed is None or expected.get("resource") != case.get("resource")
                    or observed.get("resource") != case.get("resource")
                    or expected.get("related_ref") != plan_ref
                    or observed.get("related_ref") != case.get("expected_ref")
                    or observed.get("version") != version
                    or observed.get("result") != "passed"
                    or observed.get("digest") != expected_digest):
                need("local_verification_results")
            if (plan and observed and not native_precedes(context, plan, observed)):
                need("local_plan_not_before_result")
        if not version:
            need("local_verification_version")

    if phase == "progress":
        progress = obj(payload.get("progress"), "progress")
        checkpoint = progress.get("checkpoint")
        if checkpoint not in ("after_failure", "wait", "stage_end", "handoff"):
            raise CheckError("progress.checkpoint is invalid")
        claim = progress.get("claim", "working")
        if claim not in ("working", "waiting", "completed", "continuous_tracking"):
            raise CheckError("progress.claim is invalid")
        steps = items(progress.get("steps", []), "progress.steps")
        if len(steps) > 32:
            raise CheckError("progress has too many remaining steps")
        decisions = []
        by_id = {}
        for entry in steps:
            entry = obj(entry, "progress step")
            step_id = identifier(entry.get("id"), "progress step.id")
            if step_id in by_id:
                raise CheckError("progress step IDs must be unique")
            dependencies = items(entry.get("depends_on", []), "progress step.depends_on")
            if len(set(dependencies)) != len(dependencies) or any(d not in by_id for d in dependencies):
                raise CheckError("progress dependencies must name earlier steps once")
            action = obj(entry.get("action"), "progress step.action")
            single = evaluate({**payload, "phase": "action", "action": action})
            if "platform_denied_action" in single["blocked"]:
                state = "direct_refusal"
            elif any(by_id[d]["state"] != "ready" for d in dependencies):
                state = "dependency_blocked"
            elif (action.get("capabilities") or action.get("cost", 0)) and not node_snapshot_fresh(context):
                state = "needs_evidence"
                single["missing"] = [*single["missing"], "node_snapshot_stale_or_unverified"]
            elif single["status"] == "ready":
                state = "ready"
            elif single["status"] == "needs_evidence":
                state = "needs_evidence"
            else:
                state = "blocked"
            decision = {"id": step_id, "state": state,
                        "blocked": single["blocked"], "missing": single["missing"]}
            decisions.append(decision)
            by_id[step_id] = decision
        if claim == "working" and any(s["state"] == "needs_evidence" for s in decisions):
            need("remaining_step_status_unresolved")
        if claim == "waiting":
            if any(s["state"] == "ready" for s in decisions):
                block("independent_work_remaining")
            if not decisions or not any(s["state"] == "direct_refusal" for s in decisions):
                block("waiting_without_verified_blocker")
            if any(s["state"] in ("needs_evidence", "blocked") for s in decisions):
                need("remaining_step_status_unresolved")
            for entry, decision in zip(steps, decisions):
                if decision["state"] != "direct_refusal":
                    continue
                action = entry["action"]
                target = resource(action["resource"])
                workspace = action.get("workspace")
                matching = [f for f in tool_failures(context)
                            if denied_action_matches(f, action, target, workspace)]
                if not any((record := linked(f.get("ref"), "failure")) is not None
                           and record.get("result") == "failed" for f in matching):
                    need("wait_refusal_unverified")
        elif claim == "completed":
            if decisions:
                block("remaining_steps_not_complete")
            if context.get("incident") is not None:
                need("incident_closure_required")
            acceptance = linked(progress.get("acceptance_ref"), "acceptance") if progress.get("acceptance_ref") else None
            if acceptance is None or acceptance.get("result") != "passed":
                need("goal_acceptance_unverified")
        elif claim == "continuous_tracking":
            # A static task snapshot cannot promise future model executions.
            need("active_execution_unverified")
        status = ("blocked" if blocked else "needs_evidence" if missing else "ready")
        return {"status": status, "blocked": blocked, "missing": missing,
                "deviations": deviations, "steps": decisions,
                "wait_allowed": claim == "waiting" and status == "ready",
                "continuous_eligible": False,
                "meaning": "advisory_progress_from_supplied_native_sources; no runtime interception"}

    if phase == "resume":
        resume = obj(payload.get("resume"), "resume")
        target = resource(resume.get("resource"))
        if (not any(covers(scope, target, False) for scope in project_scopes)
                or not any(covers(scope, target, False) for scope in task_scopes)):
            block("resume_outside_scope")
        linked(resume.get("goal_ref"), "goal")
        check_corrections(target, resume.get("accepted_corrections", []))
        if resume.get("stage") == "continuous":
            check_local_verification(resume.get("local_verification"), target,
                                     resume.get("version"))
        elif resume.get("stage") not in (None, "work", "local"):
            raise CheckError("resume.stage must be work, local or continuous")
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
    elif phase == "verify_local":
        validation = obj(payload.get("local_verification"), "local_verification")
        target = resource(validation.get("resource"))
        if (not any(covers(scope, target, False) for scope in project_scopes)
                or not any(covers(scope, target, False) for scope in task_scopes)):
            block("local_verification_outside_scope")
        check_local_verification(validation, target, validation.get("version"))
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
        if ((required or cost)
                and ("node_snapshot_observed_at" in context
                     or "node_snapshot_max_age_seconds" in context)
                and not node_snapshot_fresh(context)):
            need("node_snapshot_stale_or_unverified")
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
        for failure in tool_failures(context):
            if (failure.get("kind") == "context_unavailable"
                    and action.get("assertion") == "capability_missing"):
                block("tool_context_is_not_capability_absence")
            if (failure.get("kind") == "resource_exhausted" and failure.get("resource") is not None
                    and resource(failure["resource"]) == target
                    and failure.get("action_kind") == kind and kind != "repair"):
                recovery = action.get("recovery")
                if not isinstance(recovery, dict) or recovery.get("source_ref") != failure.get("ref"):
                    need("local_resource_diagnosis_before_retry")
                else:
                    source = linked(recovery.get("source_ref"), "failure")
                    cause = linked(recovery.get("cause_ref"), "cause")
                    correction = linked(recovery.get("correction_ref"), "correction")
                    if (source is None or cause is None or correction is None
                            or correction.get("result") != "verified"):
                        need("local_resource_diagnosis_before_retry")
            if denied_action_matches(failure, action, target, action.get("workspace")):
                block("platform_denied_action")
        workspace = action.get("workspace")
        incident = context.get("incident")
        incident_target = None
        if incident is not None:
            incident = obj(incident, "context.incident")
            incident_target = resource(incident.get("resource"))
            if overlaps(incident_target, target) and workspace is None:
                need("workspace_boundary_for_incident")
        if workspace is not None:
            workspace = obj(workspace, "action.workspace")
            # The coordination channel is descriptive; the tool's actual node,
            # data location and destination determine the workspace relation.
            if workspace.get("channel") is not None:
                identifier(workspace["channel"], "action.workspace.channel")
            executor = identifier(workspace.get("execution_node"), "action.workspace.execution_node")
            data_node = identifier(workspace.get("data_node"), "action.workspace.data_node")
            destination = identifier(workspace.get("destination_node"), "action.workspace.destination_node")
            side_effect = workspace.get("side_effect")
            if side_effect not in ("read", "local_change", "transfer", "irreversible_delete",
                                   "synthetic_test"):
                raise CheckError("action.workspace.side_effect is invalid")
            if (kind == "read") != (side_effect == "read"):
                block("action_effect_mismatch")
            if incident_target is not None and overlaps(incident_target, target):
                linked(incident.get("goal_ref"), "goal")
                responsible = identifier(incident.get("responsible_node"), "context.incident.responsible_node")
                original_data = identifier(incident.get("data_node"), "context.incident.data_node")
                allowed_destinations = items(incident.get("destination_nodes"),
                                             "context.incident.destination_nodes")
                if workspace.get("responsible_node") != responsible:
                    block("incident_responsibility_drift")
                if side_effect == "synthetic_test":
                    fixture = (linked(workspace.get("fixture_ref"), "synthetic_fixture")
                               if workspace.get("fixture_ref") else None)
                    if fixture is None:
                        need("synthetic_input_provenance")
                else:
                    if data_node != original_data:
                        block("incident_data_boundary_drift")
                    if destination not in allowed_destinations:
                        block("incident_destination_drift")
            cross_environment = destination != data_node
            # A read already covered by project/task scopes needs no second grant.
            if cross_environment or side_effect != "read":
                auth_ref = workspace.get("transfer_authorization_ref" if cross_environment
                                         else "authorization_ref")
                authorization = linked(auth_ref, "authorization") if auth_ref else None
                if authorization is None or authorization.get("result") != "granted":
                    need("cross_environment_authorization" if cross_environment
                         else "local_action_authorization")
                else:
                    authorized = obj(authorization.get("workspace", {}), "authorization.workspace")
                    if (authorization.get("resource") != action.get("resource")
                            or executor not in items(authorized.get("execution_nodes", []), "authorized execution_nodes")
                            or data_node not in items(authorized.get("data_nodes", []), "authorized data_nodes")
                            or destination not in items(authorized.get("destination_nodes", []), "authorized destination_nodes")
                            or side_effect not in items(authorized.get("side_effects", []), "authorized side_effects")):
                        block("outside_authorized_workspace")
            if cross_environment:
                review = linked(workspace.get("risk_review_ref"), "risk_review") if workspace.get("risk_review_ref") else None
                if review is None or review.get("resource") != action.get("resource"):
                    need("cross_environment_necessity_and_impacts")
            if side_effect == "irreversible_delete":
                confirmation = (linked(workspace.get("confirmation_ref"), "confirmation")
                                if workspace.get("confirmation_ref") else None)
                if (confirmation is None or confirmation.get("result") != "granted"
                        or confirmation.get("resource") != action.get("resource")):
                    need("irreversible_action_confirmation")
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
            if (defect.get("cause_layer") == "implementation"
                    and any(f.get("kind") == "context_unavailable"
                            for f in tool_failures(context))):
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
            target = None
        else:
            target = resource(issue["resource"])
            if (not any(covers(scope, target, True) for scope in project_scopes)
                    or not any(covers(scope, target, True) for scope in task_scopes)):
                block("issue_outside_scope")

        if target is not None:
            check_local_verification(issue.get("local_verification"), target,
                                     obj(issue.get("regression", {}), "issue.regression").get("version"))

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
        incident = context.get("incident")
        if incident is not None:
            incident = obj(incident, "context.incident")
            responsible = identifier(incident.get("responsible_node"), "context.incident.responsible_node")
            if (issue.get("goal_ref") != incident.get("goal_ref")
                    or (target is not None and target != resource(incident.get("resource")))):
                block("incident_goal_or_resource_drift")
            if applied is None or applied.get("node_id") != responsible:
                need("local_adoption_at_responsible_node")
            if observed is None or observed.get("node_id") != responsible:
                need("local_runtime_effect_at_responsible_node")
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
    continuous_eligible = (status == "ready" and
                           (phase == "verify_local" or phase == "close_issue" or
                            (phase == "resume" and payload["resume"].get("stage") == "continuous")))
    return {"status": status, "blocked": blocked, "missing": missing, "deviations": deviations,
            "continuous_eligible": continuous_eligible,
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
