"""Stateless checks over references to native tasks and node observations.

The caller reads the original records with the tools available to its task.  This
module checks the supplied, short-lived envelope; it never stores a second task
history or grants permissions that the underlying platform has not granted.
"""

import json
import re
import sys
from pathlib import Path, PureWindowsPath
from datetime import datetime, timezone

from source_evidence import native_item, native_precedes, source_body, source_text, verify as verify_source
from source_evidence import direct_user_message as source_direct_user_message


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


def direct_user_message(item, body):
    """Reuse the same source boundary for permission, pause and dispatch."""
    try:
        return source_direct_user_message(item, body)
    except ValueError as exc:
        raise CheckError(str(exc)) from exc


def received_request(item, body):
    """Recognize supplied task input, without granting message/pause authority."""
    if item is None:
        return False
    if item.get("type") == "userMessage":
        if direct_user_message(item, body):
            return True
        content = item.get("content", [])
        if any(isinstance(b, dict) and isinstance(b.get("codexDelegation"), dict)
               and isinstance(b["codexDelegation"].get("sourceThreadId"), str)
               and b["codexDelegation"]["sourceThreadId"].strip() for b in content):
            return True
    elif not (item.get("type") == "functionCallOutput"
              and item.get("namespace") == "cloud_threads" and item.get("name") == "send_message"):
        return False
    return bool(body and body.lstrip().startswith("<codex_delegation>")
                and re.search(r"<source_thread_id>[^<\s]+</source_thread_id>", body)
                and "<input>" in body and body.rstrip().endswith("</codex_delegation>"))


def task_report_identity(body):
    """Read explicit report identity/outcome fields, never a hash mention in prose."""
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate report field")
            result[key] = value
        return result
    def reject_constant(value):
        raise ValueError("non-JSON report constant")
    try:
        report = json.loads(body, object_pairs_hook=unique, parse_constant=reject_constant) if body else None
    except (ValueError, RecursionError):
        return None
    if not isinstance(report, dict):
        return None
    versions = [report[k] for k in ("version", "manifest_sha256", "candidate_manifest_sha256") if k in report]
    if not versions or any(not isinstance(v, str) or not v.strip() for v in versions) or len(set(versions)) != 1:
        return None
    outcomes = []
    if "outcome" in report:
        if report["outcome"] not in ("passed", "failed", "not_run"):
            return None
        outcomes.append(report["outcome"])
    if "verdict" in report:
        verdict = report["verdict"]
        if not isinstance(verdict, str) or not re.fullmatch(r"(?:PASS|FAIL|NOT_RUN)(?:_[A-Z0-9]+)*", verdict):
            return None
        outcomes.append("passed" if verdict == "PASS" or verdict.startswith("PASS_") else
                        "failed" if verdict == "FAIL" or verdict.startswith("FAIL_") else "not_run")
    if not outcomes or len(set(outcomes)) != 1:
        return None
    if "resource" in report:
        try:
            resource(report["resource"])
        except CheckError:
            return None
    return versions[0], outcomes[0], report.get("resource")


def resource(value):
    value = obj(value, "resource")
    kind = identifier(value.get("kind"), "resource.kind")
    name = identifier(value.get("id"), "resource.id")
    path = value.get("path", "")
    if not isinstance(path, str) or path.startswith("/") or any(
            part in ("", ".", "..") for part in path.split("/") if path):
        raise CheckError("resource.path must be a relative canonical path")
    return kind, name, path.rstrip("/")


def dependency_statement(body, producer, consumer, reason):
    """Bind a necessary input/shared effect to the actual supplied consumer.

    This is a relation check over an acquired contract/observation, not an
    oracle for its truth or a permission to execute either action.
    """
    def unique(pairs):
        value = {}
        for key, child in pairs:
            if key in value:
                raise ValueError("duplicate dependency field")
            value[key] = child
        return value

    def reject_constant(value):
        raise ValueError("non-JSON dependency constant")

    def same(left, right):
        return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)

    if not isinstance(body, str) or len(body) > 65536:
        return False
    try:
        statement = json.loads(body, object_pairs_hook=unique, parse_constant=reject_constant)
        if not isinstance(statement, dict) or set(statement) != {"producer", "consumer", "requires"}:
            return False
        for key, entry in (("producer", producer), ("consumer", consumer)):
            if not same(statement[key], {"step_id": entry["id"], "action": entry["action"]}):
                return False
        requirement = statement["requires"]
        if (not isinstance(requirement, dict) or set(requirement) != {"kind", "resource", "reason"}
                or requirement.get("reason") != reason):
            return False
        needed = resource(requirement["resource"])
        produced = resource(producer["action"]["resource"])
        consumed = resource(consumer["action"]["resource"])
        if requirement["kind"] == "required_input":
            return needed == produced
        if requirement["kind"] == "shared_effect":
            return (needed in (produced, consumed) and overlaps(produced, consumed)
                    and any(e["action"].get("kind") != "read" for e in (producer, consumer)))
        return False
    except (ValueError, RecursionError):
        return False


def covers(scope, target, write):
    scope = obj(scope, "scope")
    base = resource(scope)
    access = scope.get("access")
    if access not in ("read", "write"):
        raise CheckError("scope.access must be read or write")
    return (base[:2] == target[:2]
            and (not base[2] or target[2] == base[2] or target[2].startswith(base[2] + "/"))
            and (access == "write" or not write))


def workspace_covers(authorized, proposed, required=False):
    """Keep the original grant's explicit node/effect bounds, including reads."""
    if not isinstance(authorized, dict):
        return None
    bounds = ("execution_nodes", "data_nodes", "destination_nodes", "side_effects")
    if not required and not any(key in authorized for key in bounds):
        return True  # Older exact-resource message grants need no new node fields.
    if not isinstance(proposed, dict):
        return None
    for actual, permitted in (("execution_node", "execution_nodes"), ("data_node", "data_nodes"),
                              ("destination_node", "destination_nodes"), ("side_effect", "side_effects")):
        if permitted not in authorized:
            if required:
                return None
            continue
        values = items(authorized[permitted], "authorized " + permitted)
        for value in values:
            identifier(value, "authorized " + permitted + " entry")
        if any(value.strip().casefold() in {"unknown", "unspecified", "?", "*"} for value in values):
            return None
        if proposed.get(actual) not in values:
            return False
    return True


def authorization_covers(declared, target, project, workspace=None, authorized=None, root_node=None):
    """Compare an existing grant; None means local path identity is unverified.

    Only a known, existing directory in this project's local checkout can
    extend a resource grant to descendants. No filesystem names grant authority.
    """
    if declared is None or target is None:
        return False
    allowed, requested = resource(declared), resource(target)
    unknown = {"unknown", "unspecified", "?", "*"}
    if any(part.strip().casefold() in unknown for part in (*allowed[:2], *requested[:2])):
        return None
    if allowed[:2] != requested[:2]:
        return False
    if allowed[2] and requested[2] != allowed[2] and not requested[2].startswith(allowed[2] + "/"):
        return False
    exact = allowed == requested
    if allowed[0] != "repo":
        return True if exact else None
    if any("\\" in path or "\x00" in path or PureWindowsPath(path).drive
           for path in (allowed[2], requested[2])):
        return False
    checkout = project.get("root")
    if allowed[1] != project.get("id") or not checkout:
        return True if exact else None
    if not isinstance(checkout, str) or not Path(checkout).is_absolute():
        return None
    if not exact:
        bounds = workspace_covers(authorized, workspace, required=True)
        if bounds is not True:
            return bounds
        nodes = [workspace.get(key) for key in ("execution_node", "data_node", "destination_node")]
        original_nodes = set(authorized["data_nodes"])
        anchor = root_node or (next(iter(original_nodes)) if len(original_nodes) == 1 else None)
        if anchor is None:
            return None
        if anchor not in original_nodes or any(node != anchor for node in nodes):
            return False
    try:
        base = Path(checkout).resolve(strict=True)
        if not base.is_dir():
            return None
        directory = (base / allowed[2]).resolve(strict=not exact)
        destination = (base / requested[2]).resolve(strict=False)
        if not directory.is_relative_to(base) or not destination.is_relative_to(directory):
            return False
        if not exact and not directory.is_dir():
            return False
        existing = destination
        while not existing.exists() and existing != base:
            existing = existing.parent
        if existing.stat().st_dev != base.stat().st_dev:
            return False
        return True
    except (OSError, ValueError, RuntimeError):
        return None


def overlaps(left, right):
    return (left[:2] == right[:2] and
            (not left[2] or not right[2] or left[2] == right[2]
             or left[2].startswith(right[2] + "/")
             or right[2].startswith(left[2] + "/")))


def business_observation(expected, observed):
    """Inspect the project's bounded BusinessEvidence format, never execute it.

    This checks selected observations, not arbitrary secrets, producer provenance,
    domain oracles, browser execution or real end-to-end acceptance.
    """
    expected = obj(expected, "business expectation")
    observed = obj(observed, "business observation")
    wanted = obj(expected.get("run"), "business expectation.run")
    run = obj(observed.get("run"), "business observation.run")
    operations = obj(expected.get("operations"), "business expectation.operations")
    actual = obj(observed.get("operations"), "business observation.operations")
    if expected.get("level") not in ("engineering", "business_e2e"):
        raise CheckError("business expectation.level must be engineering or business_e2e")
    missing, blocked = [], []
    if items(observed.get("read_errors", []), "business observation.read_errors"):
        missing.append("business_capture_read_incomplete")
    if expected["level"] == "business_e2e":
        missing.append("business_end_to_end_not_proven_by_adapter")
    fields = ("runId", "caseId", "sourceVersion", "testVersion", "environment", "evidenceKind")
    for key in fields:
        value = wanted.get(key)
        if (not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", value)
                or value in ("unversioned", "unknown")):
            raise CheckError("business expectation requires bounded run identity and versions")
    if wanted["evidenceKind"] not in ("synthetic-controller", "live-project-runner"):
        raise CheckError("unsupported business evidence kind")
    if any(run.get(key) != wanted[key] for key in fields) or type(run.get("schema")) is not int or run["schema"] != 1:
        blocked.append("business_run_identity_or_version_mismatch")
    if not operations or len(operations) > 32:
        raise CheckError("business expectation requires 1 to 32 operations")
    if set(actual) != set(operations):
        missing.append("business_operation_set_mismatch")

    def equal(left, right):
        # JSON booleans and numbers have distinct meanings.
        return json.dumps(left, sort_keys=True) == json.dumps(right, sort_keys=True)

    for operation_id, required in operations.items():
        identifier(operation_id, "business operation ID")
        required = obj(required, "business expected operation")
        contract = obj(required.get("contract"), "business operation.contract")
        for section in ("request", "response", "state"):
            pointers = items(contract.get(section), "business contract." + section)
            if (len(pointers) != len(set(pointers)) if all(isinstance(p, str) for p in pointers) else True):
                raise CheckError("business projection pointers must be unique strings")
            for pointer in pointers:
                if not re.fullmatch(r"(/[A-Za-z0-9_-]+)+", pointer):
                    raise CheckError("business projection pointer is invalid")
                if re.search(r"auth|cookie|token|secret|password|credential|session|email|phone|account", pointer, re.I):
                    blocked.append("business_sensitive_projection")
        bindings = obj(contract.get("bindings"), "business contract.bindings")
        for name, value in bindings.items():
            identifier(name, "business binding name")
            if not isinstance(value, str) or ":" not in value:
                raise CheckError("business binding must name a selected source and pointer")
            section, pointer = value.split(":", 1)
            projection = "state" if section in ("before", "after") else section
            if section not in ("request", "response", "before", "after") or pointer not in contract[projection]:
                raise CheckError("business binding source is not selected")
        if operation_id not in actual:
            continue  # The operation-set mismatch above retains this gap.
        captured = obj(actual[operation_id], "business captured operation")
        if set(captured) - {"prepared", "http-observed", "observed", "bindings", "verdict"}:
            # Failed/interrupted and recovery records remain useful evidence,
            # but a later state lookup cannot replace a lost exchange.
            missing.append("business_operation_failure_or_recovery_requires_review")
        http = None
        if "http-observed" in captured:
            http = obj(captured["http-observed"], "business HTTP observation")
            if (not equal(http.get("run"), run) or http.get("operationId") != operation_id
                    or http.get("phase") != "HTTP_OBSERVED"):
                blocked.append("business_http_observation_mismatch")
        events = {}
        for name, phase in (("prepared", "PREPARED"), ("observed", "OBSERVED"), ("verdict", "PASS")):
            value = captured.get(name)
            if value is None:
                missing.append("business_operation_" + name + "_missing")
                continue
            value = obj(value, "business event")
            events[name] = value
            if not equal(value.get("run"), run) or value.get("operationId") != operation_id:
                blocked.append("business_event_identity_mismatch")
            if value.get("phase") != phase:
                missing.append("business_operation_not_passed")
        if not all(key in events for key in ("prepared", "observed", "verdict")):
            continue
        prepared, observation = events["prepared"], events["observed"]
        if http is not None and any(
                key not in http or key not in observation or not equal(http[key], observation[key])
                for key in ("method", "path", "status", "request", "response", "before")):
            blocked.append("business_http_observation_mismatch")
        for key in ("method", "path"):
            if not isinstance(required.get(key), str) or not required[key]:
                raise CheckError("business expected operation requires method and path")
            if prepared.get(key) != required[key] or observation.get(key) != required[key]:
                blocked.append("business_exchange_action_mismatch")
        if not equal(prepared.get("contract"), contract):
            blocked.append("business_contract_mismatch")
        postconditions = required.get("postconditions")
        if (not isinstance(postconditions, dict) or not postconditions
                or not any(key in ("response", "after") and isinstance(value, dict) and value
                           for key, value in postconditions.items())):
            missing.append("business_domain_expectation_missing")
        else:
            for section, value in postconditions.items():
                if section == "status":
                    matches = equal(observation.get(section), value)
                elif section in ("request", "response", "before", "after"):
                    value = obj(value, "business postcondition projection")
                    captured_values = observation.get(section)
                    matches = (isinstance(captured_values, dict) and all(
                        key in captured_values and equal(captured_values[key], wanted_value)
                        for key, wanted_value in value.items()))
                else:
                    raise CheckError("unsupported business postcondition source")
                if not matches:
                    blocked.append("business_domain_postcondition_mismatch")
        if not equal(prepared.get("request"), observation.get("request")) or not equal(prepared.get("before"), observation.get("before")):
            blocked.append("business_exchange_preparation_mismatch")
        for section, projection in (("request", "request"), ("response", "response"), ("before", "state"), ("after", "state")):
            values = observation.get(section)
            if not isinstance(values, dict) or set(values) != set(contract[projection]):
                missing.append("business_selected_observation_missing")
                continue
            if any(isinstance(v, (dict, list)) or (isinstance(v, str) and len(v) > 512) for v in values.values()):
                missing.append("business_selected_observation_invalid")
        captured_bindings = captured.get("bindings")
        if not isinstance(captured_bindings, dict) or set(captured_bindings) != set(bindings):
            missing.append("business_binding_missing")
            continue
        for name, source in bindings.items():
            section, pointer = source.split(":", 1)
            values = observation.get(section)
            if (not isinstance(values, dict) or pointer not in values
                    or not equal(captured_bindings[name], {"source": source, "value": values[pointer]})):
                blocked.append("business_binding_source_mismatch")
        for pair in items(required.get("binding_equalities", []), "business binding_equalities"):
            if (not isinstance(pair, list) or len(pair) != 2
                    or not all(isinstance(name, str) and name in bindings for name in pair)):
                raise CheckError("business binding equality requires two selected binding names")
            left, right = (captured_bindings.get(name) for name in pair)
            if (not isinstance(left, dict) or not isinstance(right, dict)
                    or not equal(left.get("value"), right.get("value"))):
                blocked.append("business_dynamic_relationship_mismatch")
    return list(dict.fromkeys(blocked)), list(dict.fromkeys(missing))


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
            or not overlaps(resource(failure["resource"]), target)):
        return False
    # Tool labels are entrances, not effect identities.
    denied_kind = failure.get("action_kind")
    current_kind = action.get("kind")
    if (denied_kind == "read" or current_kind == "read") and denied_kind != current_kind:
        return False
    denied_effect = failure.get("effect_key")
    if denied_effect is not None and denied_effect != action.get("effect_key"):
        return False
    if not isinstance(workspace, dict):
        return True  # Missing workspace facts cannot clear a refusal.
    effect = failure.get("side_effect")
    destination = failure.get("destination_node")
    return ((effect is None or effect == workspace.get("side_effect"))
            and (destination is None or destination == workspace.get("destination_node")))


def message_receipt_matches(item, text, target_id):
    """Validate supported native receipt schemas; unresolved evidence stays unresolved."""
    providers = {
        "send_message_to_thread": (None, "codex_app", "mcp__codex_app"),
        "mcp__codex_app__send_message_to_thread": (None, "codex_app", "mcp__codex_app"),
        "send_message": (None, "cloud_threads"),
    }
    if (not isinstance(item, dict) or item.get("type") != "functionCallOutput"
            or not isinstance(item.get("name"), str)
            or item.get("namespace") not in providers.get(item["name"], ())
            or not isinstance(text, str) or len(text) > 65536):
        return False
    destinations = {"threadId", "thread_id", "targetThreadId"}
    controls = {"success", "isError", "status", "state", "error", "errors"}
    states = {"sent", "queued", "accepted", "delivered", "success", "succeeded",
              "completed", "started", "ok"}
    metadata = {"hostId", "host_id", "messageId", "message_id"}

    def flags(value):
        # bool is a subtype of int: equality/truthiness would accept 0 and 1.
        if "success" in value and (type(value["success"]) is not bool or value["success"] is not True):
            return False
        if "isError" in value and (type(value["isError"]) is not bool or value["isError"] is not False):
            return False
        for key in ("status", "state"):
            if key in value and (not isinstance(value[key], str) or value[key] not in states):
                return False
        if "error" in value and value["error"] is not None and value["error"] != "":
            return False
        if "errors" in value and value["errors"] is not None and value["errors"] != []:
            return False
        return True

    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("duplicate receipt field")
            result[key] = value
        return result

    def reject_constant(value):
        raise ValueError("non-JSON receipt constant")

    def decode(value):
        return json.loads(value, object_pairs_hook=unique_object, parse_constant=reject_constant)

    nodes = 0
    def audit(value, depth=0):
        # Metadata cannot provide a target, but must not hide explicit failures.
        nonlocal nodes
        nodes += 1
        if depth > 8 or nodes > 256:
            return False
        if isinstance(value, dict):
            return flags(value) and all(audit(child, depth+1) for child in value.values())
        if isinstance(value, list):
            return all(audit(child, depth+1) for child in value)
        return True

    def receipt(value, depth=0):
        if depth > 8 or not isinstance(value, dict) or not audit(value):
            return None
        if "content" in value or "structuredContent" in value:
            # MCP CallToolResult. Only declared payload channels carry targets.
            if set(value) - controls - {"content", "structuredContent", "_meta"}:
                return None
            targets = []
            if "_meta" in value and not isinstance(value["_meta"], dict):
                return None
            if "structuredContent" in value:
                result = receipt(value["structuredContent"], depth+1)
                if result is None:
                    return None
                targets.extend(result)
            if "content" in value:
                content = value["content"]
                if not isinstance(content, list) or len(content) > 32:
                    return None
                for block in content:
                    if (not isinstance(block, dict) or block.get("type") != "text"
                            or not isinstance(block.get("text"), str)
                            or set(block) - {"type", "text", "annotations", "_meta"}):
                        return None
                    if any(key in block and not isinstance(block[key], dict) for key in ("annotations", "_meta")):
                        return None
                    try:
                        decoded = decode(block["text"])
                    except (ValueError, RecursionError):
                        # Malformed/duplicate JSON is not presentation text.
                        if block["text"].lstrip().startswith(("{", "[")):
                            return None
                        continue
                    result = receipt(decoded, depth+1)
                    if result is None:
                        return None
                    targets.extend(result)
            return targets
        # Verified legacy native return: a direct target plus optional typed flags
        # and identity metadata. Arbitrary nested objects cannot prove a send.
        if set(value) - destinations - controls - metadata:
            return None
        for key in metadata:
            if key in value and value[key] is not None and not isinstance(value[key], str):
                return None
        targets = []
        for key in destinations:
            if key in value:
                target = value[key]
                if not isinstance(target, str) or not target.strip():
                    return None
                targets.append(target)
        return targets

    if not isinstance(item.get("output"), dict) or not audit(item):
        return False
    try:
        targets = receipt(decode(text))
    except (ValueError, RecursionError):
        return False
    return bool(targets) and set(targets) == {target_id}


def contains_version(text, version):
    return bool(re.search(r"(?<![\w:./-])" + re.escape(version) + r"(?![\w:./-])", text))


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
    access_route = None
    resource_route = None
    basis_review = None

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
                           "node_path", "node_id", "workspace", "epistemic_status",
                           "origin_refs", "supersedes_ref"}:
            raise CheckError("evidence may contain only references and outcome metadata")
        evidence[ref] = record

    action_input = payload.get("action")
    strict_sources = ("business_verification" in payload or phase in ("resume", "feedback", "verify_local", "close_issue", "progress")
                      or (phase == "action" and isinstance(action_input, dict)
                          and (action_input.get("kind") in ("repair", "publish")
                               or "recovery" in action_input or "workspace" in action_input
                               or "incident" in context or "access" in action_input
                               or "maintenance" in action_input)))

    # A verified upstream source is a provenance gap, not proof that the
    # user withheld permission. This set changes diagnostics only.
    upstream_authorization_links = set()

    def linked(ref, kind):
        record = evidence.get(ref)
        if (record is None or record.get("kind") != kind
                or record.get("project_id") != project_id
                or record.get("task_ref") != task_ref):
            if (kind == "authorization" and record is not None
                    and record.get("kind") == kind and record.get("project_id") == project_id
                    and record.get("task_ref") != task_ref and record.get("result") == "granted"
                    and direct_user_message(native_item(context, record), source_body(context, record))):
                upstream_authorization_links.add(ref)
            need("unresolved_" + kind + "_reference")
            return None
        if strict_sources and not verify_source(context, project, record):
            need("unverified_" + kind + "_source")
            return None
        return record

    # Optional node facts resolve names, never enlarge the original user grant.
    node_aliases, observed_root_node = {}, None
    identity_ref = context.get("node_identity_ref")
    if identity_ref is not None:
        identifier(identity_ref, "context.node_identity_ref")
        identity_record = linked(identity_ref, "node_identity")
        identity_item = native_item(context, identity_record) if identity_record else None
        identity_body = source_body(context, identity_record) if identity_record else None
        try:
            observed = json.loads(identity_body) if identity_body else None
            valid = (isinstance(observed, dict) and set(observed) == {"node_id", "aliases", "root"}
                     and identity_item is not None and identity_item.get("type") == "commandExecution"
                     and identity_item.get("status") == "completed" and identity_item.get("exitCode") == 0
                     and identity_record.get("result") == "observed"
                     and observed.get("node_id") == identity_record.get("node_id")
                     and isinstance(observed.get("node_id"), str) and bool(observed["node_id"].strip())
                     and isinstance(observed.get("aliases"), list)
                     and all(isinstance(v, str) and bool(v.strip()) for v in observed["aliases"])
                     and len(set(observed["aliases"])) == len(observed["aliases"])
                     and not any(v.strip().casefold() in {"unknown", "unspecified", "?", "*"}
                                 for v in [observed["node_id"], *observed["aliases"]])
                     and isinstance(observed.get("root"), str) and Path(observed["root"]).is_absolute()
                     and isinstance(project.get("root"), str) and Path(project["root"]).is_absolute()
                     and Path(observed["root"]).resolve(strict=True) == Path(project["root"]).resolve(strict=True))
        except (OSError, ValueError, RuntimeError):
            valid = False
        if valid:
            observed_root_node = observed["node_id"]
            node_aliases = {v: observed_root_node for v in [observed_root_node, *observed["aliases"]]}
        else:
            need("node_identity_unverified")

    def node_identity(value):
        return node_aliases.get(value, value) if isinstance(value, str) else value

    def workspace_identity(value, plural=False):
        if not isinstance(value, dict):
            return value
        result = dict(value)
        for key in (("execution_nodes", "data_nodes", "destination_nodes") if plural else
                    ("execution_node", "data_node", "destination_node", "responsible_node")):
            if key in result:
                result[key] = [node_identity(v) for v in items(result[key], "authorized " + key)] if plural else node_identity(result[key])
        return result

    def denied_matches(failure, action, target, workspace):
        original = dict(failure)
        if "destination_node" in original:
            original["destination_node"] = node_identity(original["destination_node"])
        return denied_action_matches(original, action, target, workspace_identity(workspace))

    def check_current_goal(ref, required=False):
        """Review the selected goal at every handoff or completion boundary."""
        candidates = [record for record in evidence.values()
                      if record.get("kind") == "goal" and record.get("project_id") == project_id
                      and record.get("task_ref") == task_ref]
        if not ref:
            if required or candidates:
                need("current_goal_unselected")
            return None
        goal = linked(ref, "goal")
        if goal is None:
            return None
        for candidate in candidates:
            if candidate.get("ref") == goal["ref"]:
                continue
            if not verify_source(context, project, candidate):
                need("goal_source_unverified")
            elif native_precedes(context, goal, candidate):
                need("newer_goal_requires_scope_review")
            elif not native_precedes(context, candidate, goal):
                need("goal_order_unverified")
        return goal

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

    def check_decision_basis(action, target):
        """Compare explicit premise lineage, never infer facts from source prose.

        This optional projection is not an authority registry or a runtime gate.
        Callers still establish semantic entailment and complete provenance.
        """
        if "basis_refs" not in action:
            return None
        selected = items(action["basis_refs"], "action.basis_refs")
        for ref in selected:
            identifier(ref, "basis reference")
        if len(set(selected)) != len(selected):
            raise CheckError("basis references must be unique")
        if action.get("kind") == "read":
            return {"selected_refs": selected, "purpose": "inquiry_only"}
        seen, active, superseding = set(), set(), set()

        def source(ref):
            record = evidence.get(ref)
            if (record is None or record.get("kind") != "premise"
                    or record.get("project_id") != project_id):
                need("decision_basis_source_unverified")
                return None
            digest = record.get("digest")
            if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                need("decision_basis_digest")
                return None
            if not verify_source(context, project, record):
                need("decision_basis_source_unverified")
                return None
            if record.get("resource") is None or not overlaps(resource(record["resource"]), target):
                block("decision_basis_scope_mismatch")
                return None
            return record

        def visit(ref):
            if ref in active:
                need("decision_basis_cycle")
                return
            if ref in seen:
                return
            active.add(ref)
            record = source(ref)
            if record is not None:
                state = record.get("epistemic_status")
                if state not in ("confirmed", "assumption", "unknown"):
                    need("decision_basis_status_unverified")
                elif state != "confirmed":
                    need("decision_basis_unconfirmed")
                origins = items(record.get("origin_refs", []), "premise.origin_refs")
                for origin in origins:
                    identifier(origin, "premise origin reference")
                if len(set(origins)) != len(origins):
                    raise CheckError("premise origins must be unique")
                for origin in origins:
                    visit(origin)
                item = native_item(context, record)
                body = source_body(context, record)
                # Assistant/other-task summaries may carry an established source;
                # neither their wording nor a confirmed label creates a root fact.
                if not origins and not (direct_user_message(item, body) or
                        (item is not None and item.get("type") == "commandExecution"
                         and item.get("status") == "completed" and item.get("exitCode") == 0
                         and record.get("result") == "observed")):
                    need("decision_basis_origin_unverified")
                for correction in evidence.values():
                    if correction.get("supersedes_ref") != ref:
                        continue
                    replacement = source(correction["ref"])
                    replacement_item = native_item(context, replacement) if replacement else None
                    replacement_body = source_body(context, replacement) if replacement else None
                    if (replacement is None or replacement.get("epistemic_status") != "confirmed"
                            or replacement.get("resource") != record.get("resource")
                            or not direct_user_message(replacement_item, replacement_body)):
                        need("decision_correction_source_unverified")
                    elif not native_precedes(context, record, replacement):
                        need("decision_correction_order_unverified")
                    else:
                        block("decision_basis_superseded")
                        superseding.add(replacement["ref"])
            active.remove(ref)
            seen.add(ref)

        for ref in selected:
            visit(ref)
        return {"selected_refs": selected, "lineage_refs": sorted(seen),
                "superseding_refs": sorted(superseding),
                "meaning": "supplied_source_relations_only; semantic_entailment_not_verified"}

    def check_outcome_scope(record, target, label):
        """A successful result for another resource cannot accept this target."""
        if record is None:
            return
        declared = record.get("resource")
        if declared is None:
            need(label + "_scope")
        elif resource(declared) != target:
            block(label + "_resource_mismatch")

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

    def check_independent_verification(validation, target, version):
        """Check a separate task's frozen inputs and each recorded verdict."""
        if not isinstance(validation, dict):
            need("independent_verification_not_run")
            return
        attempts = items(validation.get("attempts", []), "independent_verification.attempts")
        if not attempts:
            need("independent_verification_not_run")
            return
        if len(attempts) > 16:
            raise CheckError("too many independent verification attempts")
        previous = None
        attempted_versions = set()
        versions_before_failure = set()
        unresolved_failures = set()
        historically_failed_versions = set()
        for attempt in attempts:
            attempt = obj(attempt, "independent verification attempt")
            verifier = identifier(attempt.get("task_ref"), "verification task_ref")
            if not verifier.startswith("codex:thread:") or verifier == task_ref:
                block("verification_not_independent")
            tested_version = identifier(attempt.get("version"), "verification version")
            outcome = attempt.get("outcome")
            if outcome not in ("passed", "failed", "not_run"):
                raise CheckError("verification outcome must be passed, failed or not_run")
            digests = []
            for key in ("data_digest", "environment_digest", "criteria_digest"):
                value = attempt.get(key)
                if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                    need("verification_lock_incomplete")
                else:
                    digests.append(value)

            def independent(ref, kind):
                record = evidence.get(ref)
                if (record is None or record.get("kind") != kind
                        or record.get("project_id") != project_id
                        or record.get("task_ref") != verifier
                        or record.get("source") != "codex"):
                    need("independent_" + kind + "_source")
                    return None
                if not verify_source(context, project, record):
                    need("independent_" + kind + "_source")
                    return None
                if record.get("resource") != target:
                    need("verification_resource_mismatch")
                return record

            lock = independent(attempt.get("lock_ref"), "verification_lock")
            verdict = independent(attempt.get("verdict_ref"), "verification_verdict")
            locked = source_text(context, lock) if lock is not None else None
            if (len(digests) != 3 or lock is None or not locked
                    or lock.get("version") != tested_version
                    or "\n".join(("code:" + tested_version, "data:" + digests[0],
                                  "environment:" + digests[1], "criteria:" + digests[2])) not in locked):
                need("verification_lock_unverified")
            observed = source_body(context, verdict) if verdict is not None else None
            if (verdict is None or lock is None or not observed
                    or verdict.get("version") != tested_version
                    or verdict.get("result") != outcome
                    or verdict.get("related_ref") != attempt.get("lock_ref")
                    or "\n".join(("version:" + tested_version, "lock:" + str(lock.get("digest")),
                                  "outcome:" + outcome)) not in observed
                    or not native_precedes(context, lock, verdict)):
                need("verification_verdict_unverified")
            if previous is not None:
                prior_verdict = evidence.get(previous.get("verdict_ref"))
                if (prior_verdict is None or verdict is None
                        or not native_precedes(context, prior_verdict, verdict)):
                    need("verification_attempt_order_unverified")
            if tested_version in historically_failed_versions or tested_version in versions_before_failure:
                block("failed_version_reused_without_revision")
            if unresolved_failures:
                revision = linked(attempt.get("revision_ref"), "fix")
                if revision is None or revision.get("version") != tested_version:
                    need("failed_verification_revision")
            if outcome == "failed":
                if not unresolved_failures:
                    versions_before_failure.update(attempted_versions)
                historically_failed_versions.add(tested_version)
                unresolved_failures.add(tested_version)
            elif outcome == "passed" and unresolved_failures:
                unresolved_failures.clear()
            attempted_versions.add(tested_version)
            previous = attempt
        if previous["version"] != version:
            need("independent_verification_version_mismatch")
        if previous["outcome"] == "failed":
            deviate("independent_verification_failed")
        elif previous["outcome"] == "not_run":
            need("independent_verification_not_run")

    # Optional project adapter: two source references, no new global gate.
    business = payload.get("business_verification")
    if business is not None:
        business = obj(business, "business_verification")
        if set(business) != {"expected_ref", "observed_ref"}:
            raise CheckError("business_verification requires only expected_ref and observed_ref")
        expected_record = linked(business["expected_ref"], "business_expectation")
        observed_record = linked(business["observed_ref"], "business_run")
        envelope_name = {"verify_local": "local_verification", "close_issue": "issue",
                         "action": "action", "resume": "resume", "progress": "progress",
                         "feedback": "feedback"}[phase]
        envelope = obj(payload.get(envelope_name), envelope_name)
        target = resource(envelope.get("resource"))
        if (not any(covers(s, target, False) for s in project_scopes)
                or not any(covers(s, target, False) for s in task_scopes)):
            block("business_verification_outside_scope")
        for record, label in ((expected_record, "business_expectation"), (observed_record, "business_run")):
            check_outcome_scope(record, target, label)
        if expected_record and observed_record:
            check_current_goal(expected_record.get("related_ref"), required=True)
            if observed_record.get("related_ref") != expected_record["ref"]:
                block("business_observation_expectation_mismatch")
            observed_item = native_item(context, observed_record)
            if (observed_item is None or observed_item.get("type") != "commandExecution"
                    or observed_item.get("status") != "completed" or observed_item.get("exitCode") != 0
                    or observed_record.get("result") != "observed"
                    or not observed_record.get("digest")):
                need("business_actual_observation_source")
            if not native_precedes(context, expected_record, observed_record):
                need("business_expectation_not_before_observation")
            try:
                expected_body = json.loads(source_body(context, expected_record) or "null")
                observed_body = json.loads(source_body(context, observed_record) or "null")
            except ValueError:
                raise CheckError("business source must contain complete JSON")
            failures, gaps = business_observation(expected_body, observed_body)
            if (expected_record.get("version") != expected_body["run"]["testVersion"]
                    or observed_record.get("version") != expected_body["run"]["testVersion"]):
                block("business_evidence_version_mismatch")
            for failure in failures:
                block(failure)
            for gap in gaps:
                need(gap)

    if phase == "progress":
        progress = obj(payload.get("progress"), "progress")
        checkpoint = progress.get("checkpoint")
        if checkpoint not in ("after_failure", "wait", "stage_end", "handoff"):
            raise CheckError("progress.checkpoint is invalid")
        claim = progress.get("claim", "working")
        if claim not in ("working", "waiting", "paused", "completed", "continuous_tracking"):
            raise CheckError("progress.claim is invalid")
        steps = items(progress.get("steps", []), "progress.steps")
        if len(steps) > 32:
            raise CheckError("progress has too many remaining steps")
        if claim == "working" and not steps:
            need("remaining_steps_unlisted")
        earlier_resume = payload.get("resume")
        selected_goal = check_current_goal(
            progress.get("goal_ref") or (earlier_resume.get("goal_ref")
                                         if isinstance(earlier_resume, dict) else None),
            required=claim == "completed")
        decisions = []
        by_id = {}
        entries_by_id = {}
        for entry in steps:
            entry = obj(entry, "progress step")
            step_id = identifier(entry.get("id"), "progress step.id")
            if step_id in by_id:
                raise CheckError("progress step IDs must be unique")
            dependencies = items(entry.get("depends_on", []), "progress step.depends_on")
            for dependency in dependencies:
                identifier(dependency, "progress dependency")
            if len(set(dependencies)) != len(dependencies) or any(d not in by_id for d in dependencies):
                raise CheckError("progress dependencies must name earlier steps once")
            action = obj(entry.get("action"), "progress step.action")
            basis = entry.get("depends_on_basis")
            mappings = {}
            effective_dependencies = []
            unproven_dependencies = []
            dependency_gaps = []
            if basis is not None:
                basis = obj(basis, "progress step.depends_on_basis")
                if "status" in basis:
                    if len(dependencies) > 1:
                        raise CheckError("one dependency basis cannot stand for multiple edges")
                    mappings = {d: basis for d in dependencies}
                    if not dependencies and basis.get("status") not in ("proven", "hypothesis", "unknown"):
                        raise CheckError("dependency basis status is invalid")
                else:
                    if set(basis) - set(dependencies):
                        raise CheckError("dependency basis names an unknown edge")
                    mappings = basis
                for dependency, fact in mappings.items():
                    fact = obj(fact, "dependency basis")
                    if set(fact) - {"status", "source_ref", "reason"} or fact.get("status") not in ("proven", "hypothesis", "unknown"):
                        raise CheckError("dependency basis fields/status are invalid")
                    if fact["status"] == "proven":
                        identifier(fact.get("source_ref"), "dependency source_ref")
                        if not isinstance(fact.get("reason"), str) or not fact["reason"].strip():
                            raise CheckError("proven dependency needs its consumer reason")
            for dependency in dependencies:
                fact = mappings.get(dependency)
                gap = None
                if fact is None:
                    gap = "dependency_basis_missing"
                elif fact["status"] != "proven":
                    gap = "dependency_basis_unproven"
                else:
                    record = evidence.get(fact["source_ref"])
                    digest = record.get("digest") if record else None
                    original = (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)
                                and record.get("kind") == "dependency"
                                and record.get("project_id") == project_id
                                and record.get("task_ref") == task_ref
                                and record.get("resource") is not None
                                and resource(record["resource"]) == resource(entry["action"]["resource"])
                                and verify_source(context, project, record))
                    item = native_item(context, record) if original else None
                    body = source_body(context, record) if item else None
                    if not (item and (direct_user_message(item, body) or
                            (item.get("type") == "commandExecution"
                             and item.get("status") == "completed" and item.get("exitCode") == 0
                             and record.get("result") == "observed"))):
                        gap = "dependency_basis_source_unverified"
                    elif not dependency_statement(body, entries_by_id[dependency], entry, fact["reason"]):
                        gap = "dependency_basis_consumer_unverified"
                if gap:
                    unproven_dependencies.append(dependency)
                    if gap not in dependency_gaps:
                        dependency_gaps.append(gap)
                else:
                    effective_dependencies.append(dependency)
            single_payload = {**payload, "phase": "action", "action": action}
            # Review evidence belongs to the aggregate goal, not every
            # independent next action. Keep its gap without blocking reads.
            single_payload.pop("business_verification", None)
            single = evaluate(single_payload)
            if "platform_denied_action" in single["blocked"]:
                state = "direct_refusal"
            elif dependency_gaps:
                # Missing proof cannot justify either global waiting or a
                # speculative write. Other independent steps keep their route.
                state = "needs_evidence"
                single["missing"] = [*single["missing"], *dependency_gaps]
            elif any(by_id[d]["state"] != "ready" for d in effective_dependencies):
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
            if dependencies or basis is not None:
                decision["unproven_dependencies"] = unproven_dependencies
            if "access_route" in single:
                decision["access_route"] = single["access_route"]
            if "resource_route" in single:
                decision["resource_route"] = single["resource_route"]
            if "decision_basis" in single:
                decision["decision_basis"] = single["decision_basis"]
            decisions.append(decision)
            by_id[step_id] = decision
            entries_by_id[step_id] = entry
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
                            if denied_matches(f, action, target, workspace)]
                if not any((record := linked(f.get("ref"), "failure")) is not None
                           and record.get("result") == "failed" for f in matching):
                    need("wait_refusal_unverified")
        elif claim == "completed":
            if decisions:
                block("remaining_steps_not_complete")
            if context.get("incident") is not None:
                need("incident_closure_required")
            acceptance = linked(progress.get("acceptance_ref"), "acceptance") if progress.get("acceptance_ref") else None
            version = progress.get("version")
            if (acceptance is None or acceptance.get("result") != "passed"
                    or selected_goal is None or acceptance.get("related_ref") != selected_goal.get("ref")
                    or not version or acceptance.get("version") != version):
                need("goal_acceptance_unverified")
            target = progress.get("resource") or (action_input.get("resource") if isinstance(action_input, dict) else None)
            if target is None:
                need("verification_resource")
            else:
                completed_target = resource(target)
                check_outcome_scope(acceptance, completed_target, "goal_acceptance")
                if (not any(covers(scope, completed_target, False) for scope in project_scopes)
                        or not any(covers(scope, completed_target, False) for scope in task_scopes)):
                    block("progress_outside_scope")
                validation = progress.get("independent_verification")
                if validation is None and isinstance(payload.get("issue"), dict):
                    validation = payload["issue"].get("independent_verification")
                if validation is not None or project.get("independent_verification_required") is True:
                    check_independent_verification(validation, target, version)
        elif claim == "continuous_tracking":
            # A static task snapshot cannot promise future model executions.
            need("active_execution_unverified")
        # Optional, short-lived projection of the existing goal review and handoff.
        # It never discovers omitted user goals or sends a message itself.
        continuation = progress.get("continuation")
        continuation_state, selected_step = "not_checked", None
        received, executed = False, False
        review = None
        if continuation is None:
            if claim in ("waiting", "paused", "completed") or checkpoint in ("stage_end", "handoff"):
                need("remaining_goal_review")
        else:
            continuation = obj(continuation, "progress.continuation")
            if set(continuation) - {"review_ref", "selected_step_id", "handoff", "event_ref", "execution_ref"}:
                raise CheckError("continuation may contain only review, event, selection, execution and handoff references")
            for key in ("review_ref", "event_ref", "execution_ref"):
                if key in continuation:
                    identifier(continuation[key], "continuation." + key)
            review = linked(continuation.get("review_ref"), "goal_review")
            body = source_body(context, review) if review else None
            try:
                projection = json.loads(body) if body else None
            except ValueError:
                projection = None
            if projection is None:
                need("remaining_goal_review")
            else:
                projection = obj(projection, "goal review")
                remaining = items(projection.get("step_ids"), "goal review.step_ids")
                for step_id in remaining:
                    identifier(step_id, "goal review step_id")
                if len(set(remaining)) != len(remaining):
                    raise CheckError("goal review step IDs must be unique")
                if selected_goal is None or projection.get("goal_ref") != selected_goal.get("ref"):
                    need("remaining_goal_review_for_current_goal")
                elif not native_precedes(context, selected_goal, review):
                    need("goal_review_order_unverified")
                if set(remaining) != set(by_id):
                    block("remaining_goals_omitted_or_unreviewed")
                # A supplied current event must be followed by this review, not
                # an older pre-dispatch plan reused after a worker returns.
                if "event_ref" in continuation:
                    current_event = linked(continuation["event_ref"], "continuation_event")
                    event_item = native_item(context, current_event) if current_event else None
                    if (event_item is None or event_item.get("type") not in
                            ("userMessage", "functionCallOutput", "commandExecution")
                            or current_event.get("related_ref") != projection.get("goal_ref")
                            or not native_precedes(context, current_event, review)):
                        need("post_event_goal_review_unverified")
                if claim == "completed":
                    if not progress.get("version") or projection.get("version") != progress.get("version"):
                        need("goal_review_version_mismatch")
                    reviewed_resource = projection.get("resource")
                    if reviewed_resource is None:
                        need("goal_review_resource")
                    elif target is not None and resource(reviewed_resource) != resource(target):
                        block("goal_review_resource_mismatch")
                # Do not reuse a pre-result review when a supplied later review
                # of this goal exists. This compares supplied sources only.
                for candidate in evidence.values():
                    if (candidate.get("kind") != "goal_review" or candidate == review
                            or candidate.get("project_id") != project_id
                            or candidate.get("task_ref") != task_ref):
                        continue
                    candidate_body = source_body(context, candidate)
                    try:
                        candidate_review = json.loads(candidate_body) if candidate_body else None
                    except ValueError:
                        candidate_review = None
                    if not isinstance(candidate_review, dict):
                        need("goal_review_source_unverified")
                        continue
                    if candidate_review.get("goal_ref") != projection.get("goal_ref"):
                        continue
                    if native_precedes(context, review, candidate):
                        need("newer_goal_review_requires_scope_review")
                    elif not native_precedes(context, candidate, review):
                        need("goal_review_order_unverified")
            selected_step = continuation.get("selected_step_id")
            if selected_step is not None:
                identifier(selected_step, "continuation.selected_step_id")
                if selected_step not in by_id or by_id[selected_step]["state"] != "ready":
                    block("selected_step_not_executable")
            elif any(s["state"] == "ready" for s in decisions):
                if claim != "paused":
                    need("next_step_not_selected")
            continuation_state = "selected" if selected_step else "reviewed"
            if "execution_ref" in continuation:
                execution = linked(continuation["execution_ref"], "continuation_execution")
                execution_item = native_item(context, execution) if execution else None
                selected_action = next((entry["action"] for entry in steps
                                        if entry["id"] == selected_step), None)
                executed = bool(selected_action and by_id[selected_step]["state"] == "ready"
                    and review is not None and execution is not None
                    and execution.get("related_ref") == review.get("ref")
                    and execution.get("result") == "observed"
                    and execution.get("resource") is not None
                    and resource(execution["resource"]) == resource(selected_action["resource"])
                    and execution_item is not None and execution_item.get("type") == "commandExecution"
                    and execution_item.get("status") == "completed" and execution_item.get("exitCode") == 0
                    and native_precedes(context, review, execution))
                if executed:
                    continuation_state = "executed"
                else:
                    need("continuation_execution_unverified")
            handoff = continuation.get("handoff")
            if handoff is not None:
                handoff = obj(handoff, "continuation.handoff")
                if set(handoff) - {"target_task_ref", "version", "claim", "authorization_ref",
                                   "sent_ref", "received_ref", "execution_ref", "result_ref"}:
                    raise CheckError("handoff may contain only identity, state and evidence references")
                for key in ("authorization_ref", "sent_ref", "received_ref", "execution_ref", "result_ref"):
                    if handoff.get(key) is not None:
                        identifier(handoff[key], "handoff." + key)
                target_task = identifier(handoff.get("target_task_ref"), "handoff.target_task_ref")
                version = identifier(handoff.get("version"), "handoff.version")
                if not target_task.startswith("codex:thread:") or target_task == task_ref:
                    raise CheckError("handoff target must be another native task")
                stages = ("suggested", "sent", "received", "executing", "returned")
                declared = handoff.get("claim", "suggested")
                if declared not in stages:
                    raise CheckError("handoff.claim is invalid")
                continuation_state = "suggested"
                authorization = linked(handoff.get("authorization_ref"), "authorization") if handoff.get("authorization_ref") else None
                authority_item = native_item(context, authorization) if authorization else None
                authority_text = source_body(context, authorization) if authorization else ""
                # A relayed delegation is input, not direct human message authority.
                direct_authority = direct_user_message(authority_item, authority_text)
                authority_scope = workspace_identity(obj(authorization.get("workspace", {}), "message authorization.workspace"), plural=True) if authorization else {}
                targets = items(authority_scope.get("target_task_refs", []), "authorized target_task_refs")
                for target in targets:
                    identifier(target, "authorized target task")
                selected_action = next((entry["action"] for entry in steps
                                        if entry["id"] == selected_step), None)
                authority_valid = (authorization is not None and authorization.get("result") == "granted"
                                   and direct_authority and target_task in targets)
                selected_workspace = workspace_identity(selected_action.get("workspace")) if selected_action else None
                node_bounds = workspace_covers(authority_scope, selected_workspace) if authority_valid else False
                coverage = (authorization_covers(authorization.get("resource"), selected_action.get("resource"),
                                                 project, selected_workspace, authority_scope, observed_root_node)
                            if authority_valid and selected_action else False)
                allowed = authority_valid and coverage is True and node_bounds is True
                if authority_valid and node_bounds is False:
                    block("handoff_outside_authorized_workspace")
                elif authority_valid and node_bounds is None:
                    need("handoff_authorized_workspace_unverified")
                if authority_valid and coverage is None:
                    need("handoff_authorized_resource_unverified")
                elif not allowed and node_bounds is not False:
                    need("handoff_message_authorization")
                elif not authority_valid:
                    need("handoff_message_authorization")

                def event(ref, kind, owner):
                    record = evidence.get(ref) if ref else None
                    if (record is None or record.get("kind") != kind
                            or record.get("project_id") != project_id or record.get("task_ref") != owner
                            or record.get("version") != version):
                        return None, ""
                    item = native_item(context, record)
                    return item, source_body(context, record) if item else ""

                sent, sent_text = event(handoff.get("sent_ref"), "handoff_sent", task_ref)
                target_id, sender_id = target_task.removeprefix("codex:thread:"), task_ref.removeprefix("codex:thread:")
                sent_ok = (allowed and selected_step is not None and by_id.get(selected_step, {}).get("state") == "ready"
                           and review is not None and handoff.get("sent_ref") in evidence
                           and native_precedes(context, review, evidence[handoff["sent_ref"]])
                           and message_receipt_matches(sent, sent_text, target_id))
                if sent_ok:
                    continuation_state = "sent"
                received_item, received_text = event(handoff.get("received_ref"), "handoff_received", target_task)
                wrapper = "<source_thread_id>" + sender_id + "</source_thread_id>"
                content = received_item.get("content", []) if received_item else []
                if not isinstance(content, list):
                    raise CheckError("received message content must be an array")
                structured = any(isinstance(b, dict) and isinstance(b.get("codexDelegation"), dict)
                    and b["codexDelegation"].get("sourceThreadId") == sender_id for b in content)
                received = bool(sent_ok and received_item is not None
                    and (received_item.get("type") == "userMessage" or
                         (received_item.get("type") == "functionCallOutput"
                          and received_item.get("namespace") == "cloud_threads"
                          and received_item.get("name") == "send_message"))
                    and ((received_text.lstrip().startswith("<codex_delegation>")
                          and wrapper in received_text) or structured)
                    and contains_version(received_text, version))
                if received:
                    continuation_state = "received"
                execution, execution_text = event(handoff.get("execution_ref"), "handoff_execution", target_task)
                if (received and execution is not None and execution.get("type") == "commandExecution"
                        and execution.get("status") in ("inProgress", "completed")
                        and contains_version(execution.get("command", "") + " " + execution_text, version)):
                    continuation_state = "executing"
                result, result_text = event(handoff.get("result_ref"), "handoff_result", target_task)
                if (received and result is not None and contains_version(result_text, version)
                        and ((result.get("type") == "agentMessage" and result.get("phase") == "final_answer")
                             or (result.get("type") == "commandExecution" and result.get("status") == "completed"))):
                    continuation_state = "returned"
                if stages.index(declared) > stages.index(continuation_state):
                    need("handoff_" + declared + "_unverified")
        request_binding = None
        if "current_request" in progress:
            binding = obj(progress["current_request"], "progress.current_request")
            if set(binding) - {"input_ref", "report_ref"}:
                raise CheckError("current_request may contain only input and report references")
            request_ref = identifier(binding.get("input_ref"), "current_request.input_ref")
            if "report_ref" in binding:
                identifier(binding["report_ref"], "current_request.report_ref")
            request_binding = {"input_ref": request_ref, "state": "unverified", "outcome": None,
                               "pending_request_refs": []}

            def request_record(ref):
                record = evidence.get(ref)
                if (record is None or record.get("kind") not in ("task_request", "continuation_event")
                        or record.get("source") != "codex" or record.get("project_id") != project_id
                        or record.get("task_ref") != task_ref or not verify_source(context, project, record)):
                    return None
                version = record.get("version")
                if (not isinstance(version, str) or not version.strip() or record.get("resource") is None
                        or not received_request(native_item(context, record), source_body(context, record))
                        or not contains_version(source_body(context, record), version)):
                    return None
                resource(record["resource"])
                return record

            def report_record(ref, request):
                record = evidence.get(ref)
                if (record is None or record.get("kind") != "task_report" or record.get("source") != "codex"
                        or record.get("project_id") != project_id or record.get("task_ref") != task_ref
                        or record.get("related_ref") != request["ref"] or record.get("result") != "observed"
                        or record.get("version") != request["version"] or record.get("resource") is None
                        or resource(record["resource"]) != resource(request["resource"])
                        or not verify_source(context, project, record)
                        or not native_precedes(context, request, record)):
                    return None
                report_item = native_item(context, record)
                if (report_item is None or report_item.get("type") != "commandExecution"
                        or type(report_item.get("exitCode")) is not int or report_item["exitCode"] != 0):
                    return None
                identity = task_report_identity(source_body(context, record))
                return ((record, identity[1]) if identity and identity[0] == request["version"]
                        and (identity[2] is None or resource(identity[2]) == resource(request["resource"])) else None)

            reports_by_request = {}
            for record in evidence.values():
                if record.get("kind") == "task_report" and isinstance(record.get("related_ref"), str):
                    reports_by_request.setdefault(record["related_ref"], []).append(record["ref"])

            request = request_record(request_ref)
            if request is None:
                need("current_request_input_unverified")
            else:
                requested_target = resource(request["resource"])
                if (not any(covers(s, requested_target, False) for s in project_scopes)
                        or not any(covers(s, requested_target, False) for s in task_scopes)):
                    block("current_request_outside_scope")
                request_binding.update(state="selected", resource=request["resource"], version=request["version"])
                if review is None or not native_precedes(context, request, review):
                    need("current_request_review_unverified")
                if claim == "completed":
                    current_target = progress.get("resource")
                    if current_target is None or resource(current_target) != requested_target:
                        block("current_request_resource_mismatch")
                    if progress.get("version") != request["version"]:
                        block("current_request_version_mismatch")
                    acceptance_record = evidence.get(progress.get("acceptance_ref"))
                    if (acceptance_record is not None and (acceptance_record.get("version") != request["version"]
                            or acceptance_record.get("resource") != request["resource"])):
                        block("current_request_acceptance_mismatch")
                    validation = progress.get("independent_verification")
                    if validation is None and isinstance(payload.get("issue"), dict):
                        validation = payload["issue"].get("independent_verification")
                    if validation is not None or project.get("independent_verification_required") is True:
                        check_independent_verification(validation, request["resource"], request["version"])
                observed_report = report_record(binding.get("report_ref"), request)
                if observed_report is None:
                    if claim == "completed" or "report_ref" in binding:
                        need("current_request_report_unverified")
                else:
                    request_binding.update(state="report_matched", report_ref=binding["report_ref"], outcome=observed_report[1])
                    if observed_report[1] == "failed":
                        deviate("current_request_report_failed")
                    elif observed_report[1] == "not_run":
                        need("current_request_report_not_run")
                if any((prior := report_record(ref, request)) is not None and prior[1] == "failed"
                       for ref in reports_by_request.get(request_ref, [])):
                    deviate("current_request_failure_unresolved")
                # Follow only verified explicit revision links; timestamps or
                # matching names never cancel an independent request.
                def ancestors_of(selected):
                    ancestors, visited, current = [], {selected["ref"]}, selected
                    while current.get("related_ref") is not None:
                        parent_ref = current["related_ref"]
                        if not isinstance(parent_ref, str) or not parent_ref.strip() or parent_ref in visited:
                            return None
                        parent_record = evidence.get(parent_ref)
                        if parent_record is not None and parent_record.get("kind") == "goal":
                            break
                        parent = request_record(parent_ref)
                        if parent is None or not native_precedes(context, parent, current):
                            return None
                        ancestors.append(parent)
                        visited.add(parent_ref)
                        current = parent
                    return ancestors

                selected_ancestors = ancestors_of(request)
                if selected_ancestors is None:
                    need("current_request_revision_unverified")
                supplied_requests = [request] + [record for record in evidence.values()
                    if record.get("kind") == "task_request" and record.get("ref") != request_ref
                    and record.get("project_id") == project_id and record.get("task_ref") == task_ref]
                superseded_records = {}
                for supplied in supplied_requests:
                    verified = request_record(supplied["ref"])
                    lineage = ancestors_of(verified) if verified is not None else None
                    for ancestor in lineage or []:
                        superseded_records.setdefault(ancestor["ref"], ancestor)
                superseded = set(superseded_records)
                if request_ref in superseded:
                    need("current_request_superseded")
                if superseded:
                    request_binding["superseded_request_refs"] = list(superseded_records)
                    prior_failures = [record for record in superseded_records.values()
                        if any((prior := report_record(ref, record)) is not None and prior[1] == "failed"
                               for ref in reports_by_request.get(record["ref"], []))]
                    if prior_failures:
                        request_binding["superseded_failed_request_refs"] = [record["ref"] for record in prior_failures]
                        selected_history = {record["ref"] for record in selected_ancestors or []}
                        if any(record["ref"] in selected_history and record["version"] == request["version"]
                               and resource(record["resource"]) == requested_target for record in prior_failures):
                            deviate("current_request_failure_unresolved")
                # Only explicitly supplied request records form this bounded
                # view. Ordinary child-result events never become new goals.
                for candidate in evidence.values():
                    if (candidate.get("kind") != "task_request" or candidate.get("ref") == request_ref
                            or candidate.get("project_id") != project_id or candidate.get("task_ref") != task_ref):
                        continue
                    if candidate["ref"] in superseded:
                        continue
                    other = request_record(candidate["ref"])
                    if candidate.get("related_ref") == request_ref:
                        if other is None or not native_precedes(context, request, other):
                            need("current_request_correction_unverified")
                        else:
                            need("current_request_superseded")
                    resolved = other is not None and any(report_record(ref, other) is not None
                        for ref in reports_by_request.get(candidate["ref"], []))
                    if not resolved:
                        request_binding["pending_request_refs"].append(candidate["ref"])
                if claim == "completed" and request_binding["pending_request_refs"]:
                    need("other_supplied_requests_unresolved")
        if claim == "paused":
            pause_ref = progress.get("pause_ref")
            if pause_ref is not None:
                identifier(pause_ref, "progress.pause_ref")
            pause = linked(pause_ref, "pause") if pause_ref else None
            pause_item = native_item(context, pause) if pause else None
            direct_pause = direct_user_message(pause_item, source_body(context, pause) if pause else "")
            paused_target = progress.get("resource")
            if (pause is None or not direct_pause
                    or selected_goal is None or pause.get("related_ref") != selected_goal.get("ref")
                    or paused_target is None or pause.get("resource") is None
                    or resource(pause["resource"]) != resource(paused_target)
                    or not native_precedes(context, selected_goal, pause)
                    or review is None or not native_precedes(context, pause, review)):
                need("current_user_pause_unverified")
            elif any(not covers({**pause["resource"], "access": "write"}, resource(entry["action"]["resource"]), False)
                     for entry in steps):
                block("independent_work_outside_pause")
            if paused_target is not None and (not any(covers(scope, resource(paused_target), False) for scope in project_scopes)
                    or not any(covers(scope, resource(paused_target), False) for scope in task_scopes)):
                block("progress_outside_scope")
            if selected_step is not None or executed or received:
                block("paused_work_must_not_execute")
        if claim == "working" and checkpoint in ("stage_end", "handoff"):
            if continuation_state == "returned":
                need("returned_result_requires_next_action")
            elif not executed and not received:
                need("continuation_action_unverified")
        status = ("blocked" if blocked else "needs_evidence" if missing else
                  "deviation" if deviations else "ready")
        stop_allowed = claim in ("waiting", "paused", "completed") and status == "ready"
        if stop_allowed:
            next_operation = {"waiting": "report_scoped_blockers", "paused": "respect_user_pause",
                              "completed": "deliver_accepted_goal"}[claim]
        elif claim != "working":
            next_operation = "resolve_stop_evidence_and_continue_independent_work"
        elif continuation_state == "returned":
            next_operation = "review_returned_result_and_continue"
        elif continuation_state == "sent":
            next_operation = "verify_original_handoff_receipt"
        elif received or executed:
            next_operation = "review_remaining_work"
        elif selected_step and by_id.get(selected_step, {}).get("state") == "ready":
            next_operation = "execute_selected_step_or_authorized_handoff"
        else:
            next_operation = "review_goal_and_select_allowed_step"
        result = {"status": status, "blocked": blocked, "missing": missing,
                "deviations": deviations, "steps": decisions,
                # Per-step conditions are separate from the aggregate review.
                # These lists neither grant permission nor discover omitted work.
                "work_partition": {
                    "ready_step_ids": [s["id"] for s in decisions if s["state"] == "ready"],
                    "blocked_step_ids": [s["id"] for s in decisions
                                         if s["state"] in ("direct_refusal", "dependency_blocked", "blocked")],
                    "unresolved_step_ids": [s["id"] for s in decisions if s["state"] == "needs_evidence"]},
                "continuation_state": continuation_state, "selected_step_id": selected_step,
                "handoff_received": received,
                "local_action_observed": executed,
                "stop_allowed": stop_allowed, "next_operation": next_operation,
                "coordination_owner": task_ref,
                "step_owner": (continuation["handoff"]["target_task_ref"] if received else task_ref),
                "wait_allowed": claim == "waiting" and status == "ready",
                "continuous_eligible": False,
                "meaning": "advisory_progress_from_supplied_native_sources; no runtime interception"}
        if request_binding is not None:
            result["current_request"] = request_binding
        return result

    if phase == "resume":
        resume = obj(payload.get("resume"), "resume")
        target = resource(resume.get("resource"))
        if (not any(covers(scope, target, False) for scope in project_scopes)
                or not any(covers(scope, target, False) for scope in task_scopes)):
            block("resume_outside_scope")
        check_current_goal(resume.get("goal_ref"), required=True)
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
        if "workspace" in action:
            action = {**action, "workspace": workspace_identity(action["workspace"])}
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
        basis_review = check_decision_basis(action, target)
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
                if (state in ("running", "unknown") and record.get("task_ref") != task_ref
                        and record.get("resource") is not None
                        and overlaps(resource(record["resource"]), target)):
                    block("concurrent_resource_conflict")
                if record.get("project_id") == project_id and record.get("effect_key") == effect_key:
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
            if denied_matches(failure, action, target, action.get("workspace")):
                block("platform_denied_action")
            if (failure.get("kind") == "user_cancelled" and denied_matches(
                    {**failure, "kind": "platform_denied"}, action, target, action.get("workspace"))):
                block("user_cancelled_action")
        workspace = action.get("workspace")
        incident = context.get("incident")
        incident_target = None
        if incident is not None:
            incident = workspace_identity(obj(incident, "context.incident"))
            if "destination_nodes" in incident:
                incident["destination_nodes"] = [node_identity(v) for v in items(incident["destination_nodes"], "incident.destination_nodes")]
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
                    if not cross_environment and auth_ref in upstream_authorization_links:
                        need("upstream_authorization_link_required")
                    else:
                        need("cross_environment_authorization" if cross_environment
                             else "local_action_authorization")
                else:
                    authorized = workspace_identity(obj(authorization.get("workspace", {}), "authorization.workspace"), plural=True)
                    coverage = authorization_covers(authorization.get("resource"), action.get("resource"),
                                                     project, workspace, authorized, observed_root_node)
                    if coverage is None:
                        need("authorized_resource_path_unverified")
                    if (coverage is False
                            or workspace_covers(authorized, workspace, required=True) is not True):
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
        access = action.get("access")
        if access is not None:
            access = obj(access, "action.access")
            choices = {
                "resource_owner": ("managed_workstation", "external_business", "unknown"),
                "identity_role": ("maintenance", "business", "test", "unknown"),
                "authentication_path": ("existing_session", "managed_provider", "missing", "unknown",
                                        "failed", "new_credentials", "native_authentication"),
                "permission_change": ("none", "persistent"),
                "secret_handling": ("provider_only", "expose", "transfer"),
                "platform_requirement": ("none", "confirmation", "denied", "trust_record_edit"),
            }
            if set(access) - set(choices) - {"change_authorization_ref", "confirmation_ref"}:
                raise CheckError("action.access may contain only classification and evidence references")
            for field, allowed in choices.items():
                if access.get(field) not in allowed:
                    raise CheckError("action.access." + field + " is invalid")
            # These are supplied facts, never credentials or a grant of authority.
            access_route = "reuse_existing_authentication"
            if "unknown" in (access["resource_owner"], access["identity_role"]):
                need("resource_ownership_and_identity_purpose")
                access_route = "discover_existing_authentication"
            path = access["authentication_path"]
            if path in ("missing", "unknown", "failed"):
                need("existing_authentication_path")
                access_route = "discover_existing_authentication"
            if path == "native_authentication":
                need("native_authentication_step")
                access_route = "native_authentication_step_only"
            if path == "failed":
                block("failed_authentication_no_retry")
            for failure in tool_failures(context):
                if (failure.get("kind") == "authentication_failed"
                        and failure.get("resource") is not None
                        and overlaps(resource(failure["resource"]), target)
                        and path in ("existing_session", "managed_provider", "new_credentials")):
                    block("failed_authentication_no_retry")
            changes = []
            if path == "new_credentials":
                changes.append("new_credentials")
            if access["permission_change"] == "persistent":
                changes.append("persistent_permission")
            if access["secret_handling"] == "transfer":
                changes.append("secret_transfer")
                if not isinstance(workspace, dict) or workspace.get("side_effect") != "transfer":
                    block("secret_transfer_workspace_required")
            if changes:
                access_route = "apply_authorized_access_change"
                if kind == "read":
                    block("access_change_requires_write_effect")
                ref = access.get("change_authorization_ref")
                grant = linked(ref, "authorization") if ref else None
                approved = obj(grant.get("workspace", {}), "access authorization.workspace") if grant else {}
                allowed_changes = items(approved.get("access_changes", []), "authorized access_changes")
                if (grant is None or grant.get("result") != "granted"
                        or grant.get("resource") != action.get("resource")
                        or any(change not in allowed_changes for change in changes)):
                    need("specific_access_change_authorization")
                    access_route = "resolve_specific_access_change"
            if access["secret_handling"] == "expose":
                block("secret_read_or_output_forbidden")
            requirement = access["platform_requirement"]
            if requirement == "confirmation":
                ref = access.get("confirmation_ref")
                confirmation = linked(ref, "confirmation") if ref else None
                confirmed = obj(confirmation.get("workspace", {}), "confirmation.workspace") if confirmation else {}
                effects = items(confirmed.get("effect_keys", []), "confirmation.workspace.effect_keys")
                for effect in effects:
                    identifier(effect, "confirmation effect_key")
                if action.get("effect_key") is not None:
                    identifier(action["effect_key"], "action.effect_key")
                if (confirmation is None or confirmation.get("result") != "granted"
                        or confirmation.get("resource") != action.get("resource")
                        or not action.get("effect_key")
                        or action["effect_key"] not in effects):
                    need("native_action_confirmation")
                    access_route = "native_confirmation"
            elif requirement in ("denied", "trust_record_edit"):
                block("platform_denied_action" if requirement == "denied" else "direct_trust_edit_forbidden")
            if blocked:
                access_route = "stop_affected_action"
        maintenance = action.get("maintenance")
        if maintenance is not None:
            maintenance = obj(maintenance, "action.maintenance")
            if set(maintenance) - {"contract_ref", "observation_ref", "strategy", "claim",
                                   "max_age_seconds", "goal_ref"}:
                raise CheckError("action.maintenance contains unsupported fields")
            strategy = maintenance.get("strategy")
            if strategy not in ("rebuild", "preserve", "hot_takeover", "discover", "temporary_relief"):
                raise CheckError("action.maintenance.strategy is invalid")
            claim = maintenance.get("claim", "plan")
            if claim not in ("plan", "complete"):
                raise CheckError("action.maintenance.claim is invalid")
            age = maintenance.get("max_age_seconds")
            if isinstance(age, bool) or not isinstance(age, int) or not 1 <= age <= 86400:
                raise CheckError("action.maintenance.max_age_seconds must be an integer from 1 to 86400")

            def resource_fact(ref, fact_kind, fields, command_only=False):
                record = linked(ref, fact_kind)
                if record is None:
                    return None
                digest = record.get("digest")
                if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                    need(fact_kind + "_digest")
                    return None
                check_outcome_scope(record, target, fact_kind)
                item = native_item(context, record)
                body = source_body(context, record)
                command = (item is not None and item.get("type") == "commandExecution"
                           and item.get("status") == "completed" and item.get("exitCode") == 0)
                if not command and (command_only or not direct_user_message(item, body)):
                    need("native_" + fact_kind + "_source")
                    return None
                def unique(pairs):
                    result = {}
                    for key, value in pairs:
                        if key in result:
                            raise CheckError("duplicate resource fact field")
                        result[key] = value
                    return result
                try:
                    value = json.loads(body, object_pairs_hook=unique) if body else None
                except (ValueError, RecursionError):
                    need("structured_" + fact_kind)
                    return None
                value = obj(value, fact_kind + " body")
                if set(value) != set(fields) | {"resource"}:
                    raise CheckError(fact_kind + " body fields are invalid")
                if resource(value["resource"]) != target:
                    block(fact_kind + "_resource_mismatch")
                return value

            contract = resource_fact(maintenance.get("contract_ref"), "resource_contract",
                                     ("role", "retention", "rebuild_ref", "availability"))
            observation = resource_fact(maintenance.get("observation_ref"), "resource_observation",
                                        ("observed_at", "target_verified", "dependencies",
                                         "retention_conflict", "normal_baseline", "goal_result"), True)
            resource_route = {"rebuild": "stop_rebuild_and_verify", "preserve": "preserve_and_verify",
                              "hot_takeover": "preserve_with_continuity", "discover": "discover_resource_role",
                              "temporary_relief": "temporary_relief_only"}[strategy]
            if strategy != "discover" and (kind == "read" or not isinstance(workspace, dict)):
                block("resource_strategy_requires_actual_effect")
            if contract is not None:
                for field, allowed in {
                        "role": ("rebuildable_model", "retained_state", "external_business", "personal", "unknown"),
                        "retention": ("none", "required", "unknown"),
                        "availability": ("restart_allowed", "continuous_required", "unknown")}.items():
                    if contract[field] not in allowed:
                        raise CheckError("resource_contract." + field + " is invalid")
                if contract["rebuild_ref"] is not None:
                    identifier(contract["rebuild_ref"], "resource_contract.rebuild_ref")
                if contract["role"] == "unknown":
                    need("resource_role_unverified")
                if strategy == "rebuild":
                    if contract["role"] != "rebuildable_model" or contract["retention"] != "none":
                        block("rebuild_requires_discardable_managed_scope")
                    if not contract["rebuild_ref"]:
                        need("reconstruction_source")
                    if contract["availability"] != "restart_allowed":
                        need("rebuild_interruption_contract")
                if strategy == "hot_takeover" and contract["availability"] != "continuous_required":
                    block("unsupported_continuity_requirement")
            if observation is not None:
                for field in ("target_verified", "retention_conflict", "normal_baseline"):
                    if not isinstance(observation[field], bool):
                        raise CheckError("resource_observation." + field + " must be boolean")
                if observation["dependencies"] not in ("clear", "quiesced", "busy", "unknown"):
                    raise CheckError("resource_observation.dependencies is invalid")
                if observation["goal_result"] not in ("passed", "failed", "not_run"):
                    raise CheckError("resource_observation.goal_result is invalid")
                if not node_snapshot_fresh({"node_snapshot_observed_at": observation["observed_at"],
                                            "node_snapshot_max_age_seconds": age}):
                    need("resource_observation_stale_or_unverified")
                if not observation["target_verified"]:
                    need("exact_resource_identity")
                if strategy != "discover" and observation["dependencies"] not in ("clear", "quiesced"):
                    need("affected_dependency_safe_point")
                if strategy == "rebuild" and observation["retention_conflict"]:
                    block("rebuild_conflicts_with_observed_retention")
                if claim == "complete":
                    goal = check_current_goal(maintenance.get("goal_ref"), required=True)
                    observed = evidence.get(maintenance.get("observation_ref"))
                    if goal is not None and observed.get("related_ref") != goal["ref"]:
                        block("resource_result_goal_mismatch")
                    if observation["goal_result"] != "passed" or not observation["normal_baseline"]:
                        need("resource_goal_under_normal_baseline")
            if strategy == "temporary_relief" and claim == "complete":
                block("temporary_relief_not_capacity_solution")
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
            release_goal = check_current_goal(release.get("goal_ref"), required=True)
            test = linked(release.get("test_ref"), "release_test")
            if (not release.get("version") or test is None
                    or test.get("version") != release.get("version")
                    or test.get("result") != "passed"):
                need("tested_release_version")
            check_outcome_scope(test, target, "release_test")
            if test is not None:
                if not test.get("related_ref") or release_goal is None:
                    need("release_test_goal_reference")
                elif test.get("related_ref") != release_goal.get("ref"):
                    block("release_test_goal_mismatch")
            if (release.get("independent_verification") is not None
                    or project.get("independent_verification_required") is True):
                check_independent_verification(release.get("independent_verification"),
                                               action.get("resource"), release.get("version"))
    else:
        issue = obj(payload.get("issue"), "issue")
        check_current_goal(issue.get("goal_ref"), required=True)
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
            if (issue.get("independent_verification") is not None
                    or project.get("independent_verification_required") is True
                    or (issue.get("acceptance_owner") not in (None, task_ref))):
                check_independent_verification(issue.get("independent_verification"), issue["resource"],
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
        if target is not None:
            for record, label in ((original, "regression_original"), (affected, "regression_affected"),
                                  (applied, "adoption"), (observed, "effect"),
                                  (acceptance, "goal_acceptance"), (continuation, "followup")):
                check_outcome_scope(record, target, label)
        verification = issue.get("independent_verification")
        attempts = verification.get("attempts", []) if isinstance(verification, dict) else []
        verifier = attempts[-1].get("task_ref") if attempts and isinstance(attempts[-1], dict) else None
        if (not issue.get("acceptance_owner") or not issue.get("followup_owner")
                or (verifier and issue.get("acceptance_owner") != verifier)):
            need("completion_responsibility")

    status = ("blocked" if blocked else "needs_evidence" if missing else
              "deviation" if deviations else "ready")
    continuous_eligible = (status == "ready" and
                           (phase == "verify_local" or phase == "close_issue" or
                            (phase == "resume" and payload["resume"].get("stage") == "continuous")))
    result = {"status": status, "blocked": blocked, "missing": missing, "deviations": deviations,
              "continuous_eligible": continuous_eligible,
              "meaning": "source_excerpt_and_input_consistency_only; provenance, authorization and goal semantics remain external"}
    if basis_review is not None:
        result["decision_basis"] = basis_review
    if access_route is not None:
        result["access_route"] = access_route
    if resource_route is not None:
        result["resource_route"] = ("stop_affected_action" if blocked else
                                    "resolve_resource_facts" if missing else resource_route)
    if phase == "close_issue":
        validation = payload["issue"].get("independent_verification")
        attempts = validation.get("attempts", []) if isinstance(validation, dict) else []
        result["verification_outcome"] = (attempts[-1].get("outcome")
                                          if attempts and isinstance(attempts[-1], dict) else
                                          "not_run" if (project.get("independent_verification_required") is True
                                                        or payload["issue"].get("acceptance_owner") not in (None, task_ref))
                                          else "not_required")
    return result


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
