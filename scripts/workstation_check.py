"""Bounded, read-only checks of node observations supplied for one Codex task.

This is an advisory interpretation of existing node files. It cannot authenticate
the caller's source selection or wake a model when no Codex task is executing.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
import sys


MAX_INPUT = 131072
MAX_SOURCE = 65536
MAX_SOURCES = 32
FAILURE_PREFIXES = ("FAIL", "ERROR", "STOPPED_ON_", "UNHEALTHY", "OOM")


class CheckError(ValueError):
    pass


def object_value(value, name):
    if not isinstance(value, dict):
        raise CheckError(name + " must be an object")
    return value


def text_value(value, name):
    if not isinstance(value, str) or not value.strip():
        raise CheckError(name + " must be a nonempty string")
    return value


def instant(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be a string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return result.astimezone(timezone.utc)


def field(item, selector):
    for part in selector.split("."):
        if not isinstance(item, dict) or not part or part not in item:
            return None
        item = item[part]
    return item


def read_source(path):
    p = Path(path)
    if not p.is_absolute() or p.is_symlink():
        raise OSError("source must be an absolute ordinary path without a file symlink")
    before = p.stat()
    if not p.is_file() or before.st_size > MAX_SOURCE:
        raise OSError("source is not an ordinary bounded file")
    with p.open("rb") as stream:
        raw = stream.read(MAX_SOURCE + 1)
    after = p.stat()
    if len(raw) > MAX_SOURCE or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise OSError("source is oversized or changed during read")
    return object_value(json.loads(raw), "source")


def classify_source(spec, scope, now):
    name = text_value(spec.get("id"), "source.id")
    path = text_value(spec.get("path"), "source.path")
    age_limit = spec.get("max_age_seconds")
    if not isinstance(age_limit, int) or isinstance(age_limit, bool) or not 1 <= age_limit <= 86400:
        raise CheckError("source.max_age_seconds must be between 1 and 86400")
    selectors = {k: spec.get(k + "_path", k) for k in
                 ("project_id", "task_ref", "node_id", "version", "observed_at")}
    if any(not isinstance(s, str) or not s for s in selectors.values()):
        raise CheckError("source selectors must be nonempty strings")
    status_path = spec.get("status_path")
    if status_path is not None and (not isinstance(status_path, str) or not status_path):
        raise CheckError("source.status_path must be a nonempty string")
    ok_values = spec.get("ok_values", [])
    if not isinstance(ok_values, list) or any(not isinstance(x, str) for x in ok_values):
        raise CheckError("source.ok_values must be strings")
    try:
        data = read_source(path)
    except (OSError, ValueError, UnicodeError) as exc:
        return {"id": name, "status": "unknown", "reasons": ["source_unreadable"],
                "detail": type(exc).__name__}, None
    reasons = []
    state = field(data, status_path) if status_path else None
    failed = isinstance(state, str) and state.upper().startswith(FAILURE_PREFIXES)
    for key in ("project_id", "task_ref", "node_id", "version"):
        if field(data, selectors[key]) != scope[key]:
            reasons.append(key + "_unverified")
    if reasons:
        return {"id": name, "status": "unknown",
                "reasons": (["reported_failure_in_unbound_source"] if failed else []) + reasons}, None
    if status_path and not failed and state not in ok_values:
        reasons.append("status_unrecognized")
    try:
        observed = instant(field(data, selectors["observed_at"]))
        lag = (now - observed).total_seconds()
        if lag < -60:
            reasons.append("clock_ahead")
        if lag > age_limit:
            reasons.append("source_expired")
    except (ValueError, TypeError):
        reasons.append("observed_at_unverified")
    return {"id": name, "status": "abnormal" if failed else "unknown" if reasons else "normal",
            "reasons": (["reported_failure"] if failed else []) + reasons}, data


def classify_claim(claim, sources):
    effect = text_value(claim.get("effect_key"), "claim.effect_key")
    source_id = text_value(claim.get("source_id"), "claim.source_id")
    resource_id = text_value(claim.get("resource_id"), "claim.resource_id")
    expected = text_value(claim.get("expected_state"), "claim.expected_state")
    source_result, data = sources.get(source_id, (None, None))
    result = {"effect_key": effect, "resource_id": resource_id}
    if source_result is None or source_result["status"] != "normal" or data is None:
        return dict(result, status="unknown", reason="resource_source_unverified")
    resources = data.get("resources")
    if data.get("complete") is not True or not isinstance(resources, list):
        return dict(result, status="unknown", reason="resource_snapshot_incomplete")
    matches = [r for r in resources if isinstance(r, dict) and r.get("id") == resource_id]
    if len(matches) > 1:
        return dict(result, status="unknown", reason="duplicate_resource_identity")
    if not matches:
        state = "absent"
    else:
        state = matches[0].get("state")
    if not isinstance(state, str):
        return dict(result, status="unknown", reason="resource_state_unverified")
    return dict(result, status="normal" if state == expected else "abnormal",
                reason="resource_postcondition_matched" if state == expected else "resource_postcondition_mismatch",
                observed_state=state)


def evaluate(payload):
    payload = object_value(payload, "input")
    if payload.get("schema") != "workstation-check-v1":
        raise CheckError("unsupported schema")
    scope = object_value(payload.get("scope"), "scope")
    for key in ("project_id", "task_ref", "node_id", "version"):
        text_value(scope.get(key), "scope." + key)
    if not scope["task_ref"].startswith("codex:thread:"):
        raise CheckError("scope.task_ref must be a native Codex thread reference")
    try:
        now = instant(payload.get("now")) if "now" in payload else datetime.now(timezone.utc)
    except ValueError as exc:
        raise CheckError("now must be a timezone-aware timestamp") from exc
    specs = payload.get("sources")
    if not isinstance(specs, list) or not 1 <= len(specs) <= MAX_SOURCES:
        raise CheckError("sources must contain 1 to 32 entries")
    seen = set()
    sources = {}
    for spec in specs:
        spec = object_value(spec, "source")
        if spec.get("id") in seen:
            raise CheckError("source ids must be unique")
        seen.add(spec.get("id"))
        result, data = classify_source(spec, scope, now)
        sources[result["id"]] = (result, data)
    claims = payload.get("resource_claims", [])
    if not isinstance(claims, list) or len(claims) > 64:
        raise CheckError("resource_claims must be a bounded array")
    claim_results = []
    effect_keys = set()
    for claim in claims:
        claim = object_value(claim, "claim")
        identity = (claim.get("effect_key"), claim.get("resource_id"))
        if identity in effect_keys:
            raise CheckError("claim effect and resource pairs must be unique")
        effect_keys.add(identity)
        claim_results.append(classify_claim(claim, sources))
    all_results = [r[0] for r in sources.values()] + claim_results
    if payload.get("node_reachable") is False:
        all_results.append({"status": "unknown", "reason": "node_unreachable"})
    elif payload.get("node_reachable") not in (None, True):
        raise CheckError("node_reachable must be boolean")
    status = "abnormal" if any(r["status"] == "abnormal" for r in all_results) else \
             "unknown" if any(r["status"] == "unknown" for r in all_results) else "normal"
    return {"status": status, "scope": scope, "sources": [r[0] for r in sources.values()],
            "resource_claims": claim_results,
            "node_reachable": payload.get("node_reachable", True)}


def dispatch():
    try:
        raw = sys.stdin.buffer.read(MAX_INPUT + 1)
        if len(raw) > MAX_INPUT:
            raise CheckError("input is oversized")
        result = evaluate(json.loads(raw))
    except (CheckError, ValueError, TypeError, UnicodeError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "normal" else 2


if __name__ == "__main__":
    sys.exit(dispatch())
