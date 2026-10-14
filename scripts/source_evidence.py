"""Check short-lived evidence against Codex read_thread output or node files.

The caller obtains Codex snapshots with the native read_thread tool.  This
module does not authenticate caller-supplied snapshots and cannot judge whether
a successful command satisfies a user's goal.  It does reject unsupported,
missing, truncated, failed, or version-unbound source records.
"""

import hashlib
from pathlib import Path


def _body(item):
    if item.get("type") in ("agentMessage", "userMessage"):
        return item.get("text", "")
    output = item.get("output")
    return output.get("text", "") if isinstance(output, dict) else ""


def _native_item(context, record):
    item_id = record.get("native_item_id")
    thread_id = record.get("task_ref", "").removeprefix("codex:thread:")
    if not item_id or not thread_id:
        return None
    snapshots = context.get("native_sources", [])
    if not isinstance(snapshots, list):
        return None
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != 1:
            continue
        if not isinstance(snapshot.get("thread"), dict) or snapshot["thread"].get("id") != thread_id:
            continue
        for turn in snapshot.get("turns", []) or []:
            if not isinstance(turn, dict):
                continue
            for item in turn.get("items", []) or []:
                if not isinstance(item, dict):
                    continue
                if item.get("id") == item_id:
                    return item
    return None


def source_text(context, record):
    """Read a verified source's text for the local plan's fixed case identifiers."""
    if not verify(context, {}, record):
        return None
    item = _native_item(context, record)
    return _body(item) if item is not None and item.get("type") in ("agentMessage", "userMessage") else None


def native_precedes(context, earlier, later):
    """Require plan and result in one native snapshot with known order."""
    snapshots = context.get("native_sources", [])
    if not isinstance(snapshots, list):
        return False
    for snapshot in snapshots:
        if not isinstance(snapshot, dict) or snapshot.get("schemaVersion") != 1:
            continue
        thread = snapshot.get("thread")
        if (not isinstance(thread, dict) or
                thread.get("id") != earlier.get("task_ref", "").removeprefix("codex:thread:")):
            continue
        positions = {}
        for turn_index, turn in enumerate(snapshot.get("turns", []) or []):
            if not isinstance(turn, dict):
                continue
            for item_index, item in enumerate(turn.get("items", []) or []):
                if not isinstance(item, dict):
                    continue
                if item.get("id") in (earlier.get("native_item_id"), later.get("native_item_id")):
                    positions[item["id"]] = (turn_index, item_index)
        first, second = positions.get(earlier.get("native_item_id")), positions.get(later.get("native_item_id"))
        if first is None or second is None:
            continue
        if first[0] == second[0]:
            return first[1] < second[1]
        page = snapshot.get("page")
        order = page.get("order") if isinstance(page, dict) else None
        if order == "newest_first":
            return first[0] > second[0]
        if order == "oldest_first":
            return first[0] < second[0]
    return False


def verify(context, project, record):
    """Return whether the declared result is supported by a source excerpt.

    A checked item is source evidence, not semantic acceptance. The executor
    still interprets goals, diagnoses causes and observes downstream effects.
    """
    source = record.get("source")
    if source == "codex":
        item = _native_item(context, record)
        if item is None:
            return False
        if record.get("kind") in ("authorization", "confirmation") and item.get("type") != "userMessage":
            return False
        output = item.get("output")
        if isinstance(output, dict) and output.get("truncated") is not False:
            return False
        body = _body(item)
        digest = record.get("digest")
        if item.get("type") != "commandExecution" and not digest:
            return False
        if record.get("kind") in ("expectation", "observation") and not digest:
            return False
        if digest and hashlib.sha256(body.encode()).hexdigest() != digest:
            return False
        result = record.get("result")
        if result in ("passed", "verified", "applied", "observed", "used_correction"):
            if (item.get("type") != "commandExecution" or item.get("status") != "completed"
                    or item.get("exitCode") != 0):
                return False
            # A successful smoke command cannot silently claim an unrelated
            # tested, adopted or observed version.
            if record.get("version") and record["version"] not in (item.get("command", "") + body):
                return False
        elif result == "failed":
            if item.get("type") != "commandExecution" or (item.get("exitCode") in (None, 0)
                    and item.get("status") != "failed"):
                return False
        return True
    if source == "node":
        relative = record.get("node_path")
        root = project.get("root")
        digest = record.get("digest")
        if not root or not relative or not digest:
            return False
        base = Path(root).resolve()
        path = (base / relative).resolve()
        if not path.is_relative_to(base) or not path.is_file():
            return False
        try:
            current = path.read_bytes()
        except OSError:
            return False
        if hashlib.sha256(current).hexdigest() != digest:
            return False
        if record.get("result") in ("passed", "applied", "observed", "used_correction"):
            try:
                import json
                statement = json.loads(path.read_text())
            except (OSError, ValueError, UnicodeError):
                return False
            return (isinstance(statement, dict)
                    and statement.get("result") == record["result"]
                    and statement.get("version") == record.get("version"))
        return True
    return False
