"""Read exact bounded node sources for startup and explicit inspection."""

import json
import stat
from datetime import datetime, timezone
from pathlib import Path
import re


MANIFEST = "observation-sources.json"
MAX_MANIFEST = 8192
MAX_SOURCE = 16384
MAX_SOURCES = 8
MAX_READS = 1 + 2 * MAX_SOURCES
MAX_TOTAL_BYTES = 65536
IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,48}\Z")
IDENTITY_FIELDS = ("project_id", "task_ref", "node_id", "version")


class ReadBudget:
    def __init__(self):
        self.attempts = 0
        self.files = 0
        self.bytes = 0


def _read(path, limit, root, budget):
    budget.attempts += 1
    if budget.attempts > MAX_READS:
        raise ValueError("observation read limit")
    path = Path(path)
    if not path.is_absolute() or ".." in path.parts or root.is_symlink():
        raise ValueError("source outside node root")
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ValueError("source outside node root") from exc
    if relative == Path("."):
        raise ValueError("source is a directory")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise ValueError("source contains a symlink")
    before = path.stat()
    allowed = min(limit, MAX_TOTAL_BYTES - budget.bytes)
    if not stat.S_ISREG(before.st_mode) or allowed < 1 or before.st_size > allowed:
        raise ValueError("source unavailable or oversized")
    with path.open("rb") as stream:
        raw = stream.read(allowed)
    budget.files += 1
    budget.bytes += len(raw)
    after = path.stat()
    identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns)
    if len(raw) != before.st_size or identity(before) != identity(after):
        raise ValueError("source changed during read")
    result = json.loads(raw)
    if not isinstance(result, dict):
        raise ValueError("source must be an object")
    return result


def _field(record, key):
    if not isinstance(key, str) or not key:
        return None
    for part in key.split("."):
        if not isinstance(record, dict) or part not in record:
            return None
        record = record[part]
    return record


def _observed(record, key, max_age, now):
    try:
        value = _field(record, key)
        stamp = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if stamp.tzinfo is None or not isinstance(max_age, int) or not 1 <= max_age <= 86400:
            return False
        delta = (now - stamp.astimezone(timezone.utc)).total_seconds()
        return -60 <= delta <= max_age
    except (AttributeError, TypeError, ValueError):
        return False


def _state(record, spec, now, *, allow_follow_time=False):
    value = _field(record, spec.get("status_path", "status"))
    if not isinstance(value, str) or not IDENTIFIER.fullmatch(value):
        return "unknown", "?"
    failures = spec.get("failure_values", [])
    passing = spec.get("ok_values", [])
    if not isinstance(failures, list) or not isinstance(passing, list):
        return "unknown", value
    if value in failures:
        return "abnormal", value
    if value not in passing:
        return "unknown", value
    key = spec.get("time_path")
    if key is None and allow_follow_time:
        return "normal", value
    if not _observed(record, key, spec.get("max_age_seconds"), now):
        return "unknown", value + "(陈旧/无时间)"
    return "normal", value


def _bound(record, spec, node, paths=None):
    """A healthy claim needs matching project, native task, node and version."""
    expected = spec.get("identity")
    paths = paths if paths is not None else spec.get("identity_paths", {})
    if not isinstance(expected, dict) or not isinstance(paths, dict):
        return False
    if expected.get("node_id", node) != node:
        return False
    for key in IDENTITY_FIELDS:
        value = node if key == "node_id" else expected.get(key)
        if not isinstance(value, str) or not value or len(value) > 160:
            return False
        if _field(record, paths.get(key, key)) != value:
            return False
    return True


def _with_identity(status, label, record, spec, node, paths=None):
    if _bound(record, spec, node, paths):
        return status, label
    return ("unknown" if status == "normal" else status), label + "(归属未核)"


ASSET_STAGES = ("candidate", "installed", "loaded", "selection", "reserved",
                "effect", "health", "retirement")


def _safe_value(value, depth=0):
    if value is None or type(value) in (bool, int):
        return value
    if isinstance(value, str) and len(value) <= 256 and not any(ord(c) < 32 for c in value):
        return value
    if depth < 1 and isinstance(value, dict) and len(value) <= 8:
        if any(not isinstance(k, str) or not IDENTIFIER.fullmatch(k) for k in value):
            raise ValueError("invalid asset value key")
        return {k: _safe_value(v, depth + 1) for k, v in value.items()
                if isinstance(k, str) and IDENTIFIER.fullmatch(k)}
    raise ValueError("invalid asset scalar")


def _asset_projection(record, spec, now, node):
    """Expose selected metadata; no inference from file presence to health."""
    result = {"source_id": spec["id"], "metadata_status": "unknown", "assets": [],
              "runtime_health_assessed": False}
    if record.get("status") == "OBSERVATION_FAILED":
        result["reason"] = "observer_reported_failure"
        return result
    if record.get("schema") != "node-asset-facts-v1" or not _bound(record, spec, node):
        result["reason"] = "schema_or_identity_unverified"
        return result
    if not _observed(record, "observed_at", spec.get("max_age_seconds"), now):
        result["reason"] = "metadata_expired_or_time_unverified"
        return result
    rows = record.get("assets")
    refs = record.get("source_refs")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 8 or not isinstance(refs, list) or len(refs) > 32:
        result["reason"] = "invalid_asset_envelope"
        return result
    ref_map = {}
    for ref in refs:
        if (not isinstance(ref, dict) or not isinstance(ref.get("id"), str)
                or not IDENTIFIER.fullmatch(ref["id"]) or ref["id"] in ref_map
                or not isinstance(ref.get("path"), str) or not ref["path"].startswith("/")
                or len(ref["path"]) > 512 or any(ord(c) < 32 for c in ref["path"])
                or ("mtime_ns" in ref and (type(ref["mtime_ns"]) is not int or ref["mtime_ns"] < 0))
                or (ref.get("sha256") is not None and not re.fullmatch(r"[0-9a-f]{64}", ref["sha256"]))):
            result["reason"] = "invalid_source_reference"
            return result
        ref_map[ref["id"]] = {k: ref[k] for k in ("id", "path", "sha256", "mtime_ns") if k in ref}
    seen = set()
    row_ids = [r.get("id") for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)]
    ambiguous = {name for name in row_ids if row_ids.count(name) > 1}
    for row in rows:
        try:
            if not isinstance(row, dict):
                raise ValueError("asset must be an object")
            name = row.get("id")
            version = row.get("version_label")
            if (not isinstance(name, str) or not IDENTIFIER.fullmatch(name) or name in seen or name in ambiguous
                    or not isinstance(version, str) or not re.fullmatch(r"[A-Za-z0-9_.:+/=-]{1,64}", version)
                    or set(row.get("stages", {})) != set(ASSET_STAGES)):
                raise ValueError("invalid asset identity/stages")
            seen.add(name)
            stages = {}
            for key in ASSET_STAGES:
                stage = row["stages"][key]
                if (not isinstance(stage, dict) or not isinstance(stage.get("status"), str)
                        or not IDENTIFIER.fullmatch(stage["status"])
                        or not isinstance(stage.get("refs", []), list)
                        or len(stage.get("refs", [])) > 8
                        or any(r not in ref_map for r in stage.get("refs", []))):
                    raise ValueError("invalid asset stage")
                stages[key] = {"status": stage["status"], "value": _safe_value(stage.get("value")),
                               "source_time": _safe_value(stage.get("source_time")),
                               "refs": stage.get("refs", [])}
            if stages["health"]["status"] not in ("UNKNOWN", "NOT_ASSESSED"):
                raise ValueError("metadata producer cannot assert runtime health")
            diagnostics = row.get("diagnostic_refs", [])
            if not isinstance(diagnostics, list) or len(diagnostics) > 8:
                raise ValueError("invalid diagnostic references")
            clean = {"id": name, "version_label": "UNVERIFIED" if stages["installed"]["status"] == "UNKNOWN" else version,
                     "owner_ref": _safe_value(row.get("owner_ref")),
                     "stages": stages,
                     "diagnostic_refs": [_safe_value(r) for r in diagnostics]}
            result["assets"].append(clean)
        except (ValueError, TypeError, KeyError):
            result.setdefault("asset_errors", []).append("invalid_asset_row")
    result["metadata_status"] = ("partial" if result.get("asset_errors") else "current") if result["assets"] else "unknown"
    result["observed_at"] = record["observed_at"]
    result["source_refs"] = list(ref_map.values())
    return result


def _source(spec, now, node, root, budget, asset_sink=None):
    name = spec.get("id")
    if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
        return "unknown", "来源标识无效"
    try:
        state = _read(spec.get("path"), MAX_SOURCE, root, budget)
        base, label = _state(state, spec, now, allow_follow_time="follow" in spec)
        base, label = _with_identity(base, label, state, spec, node)
    except (OSError, ValueError, TypeError, UnicodeError):
        return "unknown", name + "=不可读/越界/超限"
    if spec.get("kind") == "asset-facts-v1":
        try:
            detail = _asset_projection(state, spec, now, node)
        except (ValueError, TypeError, KeyError):
            detail = {"source_id": name, "metadata_status": "unknown", "assets": [],
                      "runtime_health_assessed": False, "reason": "invalid_asset_projection"}
        # Projection cannot erase a failure already classified by the source.
        if base != "abnormal" and state.get("status") == "OBSERVATION_FAILED":
            base, label = _with_identity("abnormal", "OBSERVATION_FAILED", state, spec, node)
        if base == "abnormal":
            detail["source_failure"] = label
        else:
            # Metadata freshness/installation is never a runtime-health PASS.
            base = "unknown"
            label = detail["metadata_status"] + ":runtime_unverified"
        if asset_sink is not None:
            asset_sink.append(detail)
    follow = spec.get("follow")
    if follow is None:
        return base, name + "=" + label
    if not isinstance(follow, dict):
        return (base if base == "abnormal" else "unknown"), name + "=" + label + "/后续来源无效"
    run_id = _field(state, follow.get("id_path"))
    if not isinstance(run_id, str) or not IDENTIFIER.fullmatch(run_id):
        return (base if base == "abnormal" else "unknown"), name + "=" + label + "/运行标识无效"
    directory = follow.get("directory")
    if not isinstance(directory, str):
        return (base if base == "abnormal" else "unknown"), name + "=" + label + "/目录无效"
    try:
        detail = _read(Path(directory) / (run_id + ".json"), MAX_SOURCE, root, budget)
        after, after_label = _state(detail, follow, now)
        after, after_label = _with_identity(
            after, after_label, detail, spec, node,
            follow.get("identity_paths", spec.get("identity_paths", {})))
    except (OSError, ValueError, TypeError, UnicodeError):
        after, after_label = "unknown", "运行记录不可读/越界/超限"
    status = "abnormal" if "abnormal" in (base, after) else \
             "unknown" if "unknown" in (base, after) else "normal"
    return status, name + "=" + label + "/" + after_label


def _history(specs, now, node, root, budget):
    """Optional annotation errors do not invalidate already read active sources."""
    history, errors = [], []
    if not isinstance(specs, list) or len(specs) > 1:
        return history, [{"reason": "invalid_historical_source_list",
                          "scope_annotation_authenticated": False}]
    for index, item in enumerate(specs):
        name = item.get("id") if isinstance(item, dict) else None
        valid_id = isinstance(name, str) and IDENTIFIER.fullmatch(name)
        if (not isinstance(item, dict) or not valid_id
                or item.get("scope_status") != "COMPLETED_FINITE_SCOPE"
                or not isinstance(item.get("scope_ref"), str) or not item["scope_ref"]):
            errors.append({"source_index": index, "source_id": name if valid_id else None,
                           "reason": "invalid_historical_scope_annotation",
                           "scope_annotation_authenticated": False})
            continue
        prior, prior_label = _source(item, now, node, root, budget)
        history.append({"id": name, "classification_in_original_source": prior,
                        "reported_label": prior_label, "scope_status": item["scope_status"],
                        "scope_ref": item["scope_ref"], "scope_annotation_authenticated": False})
    return history, errors


def evaluate(codex_home):
    """Inspect exact files with a total budget; never search for a missing source."""
    root = Path(codex_home).resolve() / "workstation"
    budget = ReadBudget()
    mapping_valid = False
    try:
        manifest = _read(root / MANIFEST, MAX_MANIFEST, root, budget)
        if manifest.get("schema") != "workstation-entry-v1":
            raise ValueError("unsupported schema")
        node = manifest.get("node_id")
        specs = manifest.get("sources")
        if (not isinstance(node, str) or not IDENTIFIER.fullmatch(node)
                or not isinstance(specs, list) or not 1 <= len(specs) <= MAX_SOURCES):
            raise ValueError("invalid node source map")
        now = datetime.now(timezone.utc)
        asset_details, results = [], []
        for index, spec in enumerate(specs):
            details = []
            observed = _source(spec, now, node, root, budget, details) if isinstance(spec, dict) \
                       else ("unknown", "来源定义无效")
            for detail in details:
                detail["source_index"] = index
            asset_details.extend(details)
            results.append(observed)
        mapping_valid = True
    except (OSError, ValueError, TypeError, UnicodeError):
        status = "unknown"
        message = "工作站观察: 未知(节点来源清单缺失或不可读)；查 workstation/observation-sources.json。"
    else:
        history, history_errors = _history(manifest.get("historical_sources", []), now, node, root, budget)
        problems = [label for state, label in results if state != "normal"]
        status = "abnormal" if any(state == "abnormal" for state, _ in results) else \
                 "unknown" if problems or history_errors else "normal"
        selected = problems[:2] if problems else [label for _, label in results[:1]]
        more = " 等" if len(problems) > 2 else ""
        display = {"abnormal": "异常", "unknown": "未知", "normal": "正常"}[status]
        message = ("工作站观察[" + node + "]: " + display + " " + ";".join(selected) + more +
                   ("；历史来源配置无效" if history_errors else "") +
                   "；查节点来源清单与原任务原件，未配置资源不视为健康。")
    result = {"schema": "workstation-entry-result-v1", "status": status,
              "summary": message.encode("utf-8")[:420].decode("utf-8", "ignore"),
              "read_attempts": budget.attempts, "files_read": budget.files,
              "bytes_read": budget.bytes}
    if mapping_valid and asset_details:
        result["asset_observations"] = asset_details
        labels = [a["id"] + ":" + a["version_label"] for d in asset_details
                  if d["metadata_status"] in ("current", "partial") for a in d["assets"]]
        if labels:
            current_rows = {d["source_index"] for d in asset_details
                            if d["metadata_status"] == "current"
                            and results[d["source_index"]][0] != "abnormal"}
            unresolved_rows = [(state, label) for index, (state, label) in enumerate(results)
                               if state != "normal" and index not in current_rows]
            unresolved = ([label for state, label in unresolved_rows if state == "abnormal"] +
                          [label for state, label in unresolved_rows if state != "abnormal"])
            prefix = ({"abnormal": "异常 ", "unknown": "未知 ", "normal": ""}[status] +
                      ";".join(unresolved[:2]) + "；") if unresolved else ""
            if history_errors:
                prefix += "历史来源配置无效；"
            message = "工作站观察[" + node + "]: " + prefix + "资产元数据 " + ";".join(labels) + "；运行健康未知。"
            if history:
                message += "历史来源单列，范围由调用者核对，原失败保留。"
            result["summary"] = message.encode("utf-8")[:420].decode("utf-8", "ignore")
    if mapping_valid and history:
        result["historical_observations"] = history
    if mapping_valid and history_errors:
        result["historical_errors"] = history_errors
    if mapping_valid and ("historical_sources" in manifest or any(
            isinstance(spec, dict) and spec.get("kind") == "asset-facts-v1" for spec in specs)):
        result["active_observations"] = [
            {"source_index": index,
             "source_id": spec["id"] if isinstance(spec, dict) and isinstance(spec.get("id"), str)
                          and IDENTIFIER.fullmatch(spec["id"]) else None,
             "classification": state, "reported_label": label}
            for index, (spec, (state, label)) in enumerate(zip(specs, results))]
    return result


def summary(codex_home):
    return evaluate(codex_home)["summary"]


def dispatch(codex_home):
    try:
        result = evaluate(codex_home)
    except Exception:
        result = {"schema": "workstation-entry-result-v1", "status": "unknown",
                  "summary": "工作站观察: 未知(观察入口失败)",
                  "read_attempts": 0, "files_read": 0, "bytes_read": 0}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "normal" else 2
