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


def _source(spec, now, node, root, budget):
    name = spec.get("id")
    if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
        return "unknown", "来源标识无效"
    try:
        state = _read(spec.get("path"), MAX_SOURCE, root, budget)
        base, label = _state(state, spec, now, allow_follow_time="follow" in spec)
        base, label = _with_identity(base, label, state, spec, node)
    except (OSError, ValueError, TypeError, UnicodeError):
        return "unknown", name + "=不可读/越界/超限"
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


def evaluate(codex_home):
    """Inspect exact files with a total budget; never search for a missing source."""
    root = Path(codex_home).resolve() / "workstation"
    budget = ReadBudget()
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
        results = [_source(spec, now, node, root, budget) if isinstance(spec, dict)
                   else ("unknown", "来源定义无效") for spec in specs]
    except (OSError, ValueError, TypeError, UnicodeError):
        status = "unknown"
        message = "工作站观察: 未知(节点来源清单缺失或不可读)；查 workstation/observation-sources.json。"
    else:
        problems = [label for state, label in results if state != "normal"]
        status = "abnormal" if any(state == "abnormal" for state, _ in results) else \
                 "unknown" if problems else "normal"
        selected = problems[:2] if problems else [label for _, label in results[:1]]
        more = " 等" if len(problems) > 2 else ""
        display = {"abnormal": "异常", "unknown": "未知", "normal": "正常"}[status]
        message = ("工作站观察[" + node + "]: " + display + " " + ";".join(selected) + more +
                   "；查节点来源清单与原任务原件，未配置资源不视为健康。")
    return {"schema": "workstation-entry-result-v1", "status": status,
            "summary": message.encode("utf-8")[:420].decode("utf-8", "ignore"),
            "read_attempts": budget.attempts, "files_read": budget.files,
            "bytes_read": budget.bytes}


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
