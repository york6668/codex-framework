"""Read a small node source map for Codex SessionStart without starting services."""

import json
from datetime import datetime, timezone
from pathlib import Path
import re


MANIFEST = "workstation/observation-sources.json"
MAX_MANIFEST = 8192
MAX_SOURCE = 16384
MAX_SOURCES = 8
IDENTIFIER = re.compile(r"[A-Za-z0-9_.-]{1,48}\Z")


def _read(path, limit):
    path = Path(path)
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ValueError("source unavailable")
    before = path.stat()
    if before.st_size > limit:
        raise ValueError("source oversized")
    with path.open("rb") as stream:
        raw = stream.read(limit + 1)
    after = path.stat()
    if len(raw) > limit or (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
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


def _source(spec, now):
    name = spec.get("id")
    if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
        return "unknown", "来源标识无效"
    try:
        state = _read(spec.get("path"), MAX_SOURCE)
        base, label = _state(state, spec, now, allow_follow_time="follow" in spec)
    except (OSError, ValueError, TypeError, UnicodeError):
        return "unknown", name + "=不可读"
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
        detail = _read(Path(directory) / (run_id + ".json"), MAX_SOURCE)
        after, after_label = _state(detail, follow, now)
    except (OSError, ValueError, TypeError, UnicodeError):
        after, after_label = "unknown", "运行记录不可读"
    status = "abnormal" if "abnormal" in (base, after) else \
             "unknown" if "unknown" in (base, after) else "normal"
    return status, name + "=" + label + "/" + after_label


def summary(codex_home):
    """Return a bounded startup hint; any broken observation fails visibly unknown."""
    path = Path(codex_home) / MANIFEST
    try:
        manifest = _read(path, MAX_MANIFEST)
        if manifest.get("schema") != "workstation-entry-v1":
            raise ValueError("unsupported schema")
        node = manifest.get("node_id")
        specs = manifest.get("sources")
        if (not isinstance(node, str) or not IDENTIFIER.fullmatch(node)
                or not isinstance(specs, list) or not 1 <= len(specs) <= MAX_SOURCES):
            raise ValueError("invalid node source map")
        results = [_source(spec, datetime.now(timezone.utc)) if isinstance(spec, dict)
                   else ("unknown", "来源定义无效") for spec in specs]
    except (OSError, ValueError, TypeError, UnicodeError):
        return "工作站观察: 未知(节点来源清单缺失或不可读)；查 workstation/observation-sources.json。"
    problems = [label for status, label in results if status != "normal"]
    status = "异常" if any(state == "abnormal" for state, _ in results) else \
             "未知" if problems else "正常"
    selected = problems[:2] if problems else [label for _, label in results[:1]]
    more = " 等" if len(problems) > 2 else ""
    message = ("工作站观察[" + node + "]: " + status + " " + ";".join(selected) + more +
               "；查节点来源清单与原任务原件，未配置资源不视为健康。")
    raw = message.encode("utf-8")
    return raw[:420].decode("utf-8", "ignore")
