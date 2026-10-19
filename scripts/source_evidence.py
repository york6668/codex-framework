"""Check short-lived evidence against Codex read_thread output or node files.

The caller obtains Codex snapshots with the native read_thread tool.  This
module does not authenticate caller-supplied snapshots and cannot judge whether
a successful command satisfies a user's goal.  It does reject unsupported,
missing, truncated, failed, or version-unbound source records.
"""

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path, PureWindowsPath
import stat


@contextmanager
def _node_root(base):
    """Anchor the canonical root before inspecting its evidence paths.

    O_NOFOLLOW on an absolute root only checks its last component. Walk every
    canonical component through its held parent so a substituted ancestor
    cannot redirect the root open. The resulting descriptor defines this read.
    """
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    parent = os.open(base.anchor, flags | os.O_DIRECTORY)
    try:
        for component in base.parts[1:]:
            child = os.open(component, flags | os.O_DIRECTORY, dir_fd=parent)
            os.close(parent)
            parent = child
        yield parent
    finally:
        os.close(parent)


def _node_bytes(root, parts):
    """Read an ordinary object relative to the already held project root."""
    if not parts:
        return None
    opened = []
    flags = os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK
    parent = root
    try:
        for component in parts[:-1]:
            parent = os.open(component, flags | os.O_DIRECTORY, dir_fd=parent)
            opened.append(parent)
        file_info = os.stat(parts[-1], dir_fd=parent, follow_symlinks=False)
        if not stat.S_ISREG(file_info.st_mode):
            return None
        descriptor = os.open(parts[-1], flags, dir_fd=parent)
        opened.append(descriptor)
        current = os.fstat(descriptor)
        if (not stat.S_ISREG(current.st_mode)
                or (current.st_dev, current.st_ino) != (file_info.st_dev, file_info.st_ino)):
            return None
        with os.fdopen(os.dup(descriptor), "rb") as stream:
            return stream.read()
    finally:
        for descriptor in reversed(opened):
            os.close(descriptor)


def direct_user_message(item, body):
    """Keep known task delegations distinct from direct human evidence."""
    if item is None or item.get("type") != "userMessage":
        return False
    blocks = item.get("content", [])
    if not isinstance(blocks, list):
        raise ValueError("user message content must be an array")
    return ("<codex_delegation>" not in (body or "")
            and not any(isinstance(block, dict) and "codexDelegation" in block for block in blocks))


def _body(item):
    if item.get("type") in ("agentMessage", "userMessage"):
        content = item.get("content")
        if isinstance(content, list):
            blocks = [block.get("text") for block in content
                      if isinstance(block, dict) and block.get("type") == "text"
                      and isinstance(block.get("text"), str)]
            if blocks:
                return "\n".join(blocks)
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
    found, identity = None, None
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
                    # Overlapping pages can repeat an identical item. Conflicting
                    # observations have no supported ordering here; first wins
                    # must not turn a stale success into current evidence.
                    try:
                        candidate = json.dumps(item, sort_keys=True, allow_nan=False)
                    except (ValueError, TypeError, RecursionError):
                        return None
                    if found is not None and candidate != identity:
                        return None
                    found, identity = item, candidate
    return found


def native_item(context, record):
    """Return a verified native item; its text still needs semantic interpretation."""
    return _native_item(context, record) if verify(context, {}, record) else None


def source_text(context, record):
    """Read a verified source's text for the local plan's fixed case identifiers."""
    if not verify(context, {}, record):
        return None
    item = _native_item(context, record)
    return _body(item) if item is not None and item.get("type") in ("agentMessage", "userMessage") else None


def source_body(context, record):
    """Return verified native text or command output without treating it as acceptance."""
    if not verify(context, {}, record):
        return None
    item = _native_item(context, record)
    return _body(item) if item is not None else None


def native_precedes(context, earlier, later):
    """Require plan and result in one native snapshot with known order."""
    if (earlier.get("task_ref") != later.get("task_ref")
            or not earlier.get("native_item_id") or not later.get("native_item_id")
            or earlier["native_item_id"] == later["native_item_id"]
            or _native_item(context, earlier) is None or _native_item(context, later) is None):
        return False
    snapshots = context.get("native_sources", [])
    if not isinstance(snapshots, list):
        return False
    relations = set()
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
                    if item["id"] in positions:
                        return False
                    positions[item["id"]] = (turn_index, item_index)
        first, second = positions.get(earlier.get("native_item_id")), positions.get(later.get("native_item_id"))
        if first is None or second is None:
            continue
        if first[0] == second[0]:
            relations.add(first[1] < second[1])
            continue
        page = snapshot.get("page")
        order = page.get("order") if isinstance(page, dict) else None
        if order == "newest_first":
            relations.add(first[0] > second[0])
        if order == "oldest_first":
            relations.add(first[0] < second[0])
    return relations == {True}


def verify(context, project, record):
    """Return whether the declared result is supported by a source excerpt.

    A checked item is source evidence, not semantic acceptance. The executor
    still interprets goals, diagnoses causes and observes downstream effects.
    """
    source = record.get("source")
    if record.get("kind") == "goal" and source != "codex":
        return False
    if source == "codex":
        item = _native_item(context, record)
        if item is None:
            return False
        if (record.get("kind") == "verification_verdict"
                and record.get("result") == "not_run"
                and item.get("type") != "agentMessage"):
            return False
        if record.get("kind") in ("goal", "authorization", "confirmation") and item.get("type") != "userMessage":
            return False
        output = item.get("output")
        if isinstance(output, dict) and output.get("truncated") is not False:
            return False
        body = _body(item)
        if not isinstance(body, str):
            return False
        if record.get("kind") in ("authorization", "confirmation") and not direct_user_message(item, body):
            return False
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
                    or type(item.get("exitCode")) is not int or item["exitCode"] != 0):
                return False
            # A successful smoke command cannot silently claim an unrelated
            # tested, adopted or observed version.
            if record.get("version") and record["version"] not in (item.get("command", "") + body):
                return False
        elif result == "failed":
            exit_code = item.get("exitCode")
            if (item.get("type") != "commandExecution"
                    or item.get("status") not in ("completed", "failed")
                    or (exit_code is not None and (type(exit_code) is not int or exit_code == 0))
                    or (exit_code is None and item.get("status") != "failed")):
                return False
        return True
    if source == "node":
        relative = record.get("node_path")
        root = project.get("root")
        digest = record.get("digest")
        if (not isinstance(root, str) or not root or not isinstance(relative, str)
                or not relative or not digest or "\x00" in relative or "\\" in relative
                or PureWindowsPath(relative).drive or Path(relative).is_absolute()
                or any(part in ("", ".", "..") for part in relative.split("/"))):
            return False
        try:
            base = Path(root).resolve(strict=True)
            with _node_root(base) as descriptor:
                path = (base / relative).resolve(strict=True)
                if not path.is_relative_to(base) or not path.is_file():
                    return False
                current = _node_bytes(descriptor, path.relative_to(base).parts)
        except (OSError, ValueError, RuntimeError, AttributeError, NotImplementedError):
            return False
        if current is None:
            return False
        if hashlib.sha256(current).hexdigest() != digest:
            return False
        if record.get("result") in ("passed", "verified", "applied", "observed", "used_correction"):
            def unique(pairs):
                value = {}
                for key, child in pairs:
                    if key in value:
                        raise ValueError("duplicate evidence field")
                    value[key] = child
                return value

            def reject_constant(value):
                raise ValueError("non-JSON evidence constant")

            try:
                # Parse the bytes whose digest was checked, not a second read
                # that could describe another result after replacement.
                statement = json.loads(current, object_pairs_hook=unique, parse_constant=reject_constant)
            except (ValueError, UnicodeError, RecursionError):
                return False
            return (isinstance(statement, dict)
                    and statement.get("result") == record["result"]
                    and statement.get("version") == record.get("version"))
        return True
    return False
