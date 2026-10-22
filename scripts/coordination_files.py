"""Opt-in, bounded coordination files. Records do not authorize real actions.

No Task Store, network, model, dispatch, directory discovery or domain execution.
Only cooperating local writers share the per-file CAS lock. View consumes supplied
observations; it cannot authenticate their authors or judge domain semantics.
"""

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import uuid

from source_evidence import (_node_root, _node_bytes, parse_statement, resource_scope_matches,
                             source_body, verify as verify_source)

FORMAT = 1
MAX_BYTES = 1024 * 1024
MAX_REQUEST_BYTES = 4 * MAX_BYTES
MAX_ROWS = 256
MAX_EVENTS = 4096
KINDS = {"agreements", "tasks", "progress", "goals"}
REQUIREMENT_FIELDS = ("request_ref", "requirement_ref", "agreements", "scope", "acceptance", "write_scope")
TASK_FIELDS = set(REQUIREMENT_FIELDS) | {
    "task_id", "input_revision", "plan_revision", "assignment", "execution", "native_binding",
    "dependencies", "steps", "disposition", "plan_changes", "input_history", "stop_conditions",
    "remaining_outside_scope", "result_binding",
}
LIMITS = ["supplied_records_only", "caller_and_owner_not_authenticated",
          "domain_semantics_not_verified", "no_execution_or_retry_permission",
          "no_cross_file_transaction_or_distributed_lock"]


class FileError(ValueError):
    def __init__(self, status, reason):
        super().__init__(reason)
        self.status = status


def need(value, reason, status="invalid_record"):
    if not value:
        raise FileError(status, reason)


def text(value):
    return isinstance(value, str) and bool(value.strip())


def positive(value):
    return type(value) is int and value > 0


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), allow_nan=False).encode("utf-8")


def timestamp(value):
    need(text(value), "time must include timezone or be explicit null")
    try:
        result = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        need(result.utcoffset() is not None, "time must include timezone")
        return result
    except ValueError as exc:
        raise FileError("invalid_record", "invalid timestamp") from exc


def strings(value, *, nonempty=False):
    return (isinstance(value, list) and (bool(value) or not nonempty)
            and all(text(v) for v in value) and len(set(value)) == len(value))


def key(record):
    need(isinstance(record, dict) and text(record.get("id")) and text(record.get("version")),
         "agreement needs id and string version")
    return record["id"], record["version"]


def format_check(value):
    need(isinstance(value, dict), "record must be an object")
    need(type(value.get("format_version")) is int and value["format_version"] == FORMAT,
         "unsupported format_version; original retained", "unsupported_format")


def validate_snapshot(kind, document):
    format_check(document)
    need(positive(document.get("revision")), "snapshot revision must be positive integer")
    authority = document.get("authority")
    need(isinstance(authority, dict) and text(authority.get("owner_ref"))
         and text(authority.get("storage_ref")), "snapshot needs explicit authority references")
    rows = document.get(kind)
    need(isinstance(rows, list) and len(rows) <= MAX_ROWS, "invalid or oversized snapshot rows")
    if kind == "agreements":
        seen = {}
        for row in rows:
            identity = key(row)
            need(identity not in seen, "duplicate agreement version")
            seen[identity] = row
            need(row.get("status") in ("candidate", "effective", "superseded", "withdrawn", "conflict"),
                 "invalid agreement status")
            need(row.get("kind") in ("requirement", "fact", "decision", "suggestion"), "invalid agreement kind")
            source = row.get("source")
            need(isinstance(source, dict) and text(source.get("ref")) and text(source.get("level"))
                 and "occurred_at" in source, "agreement needs original source and evidence level")
            if source["occurred_at"] is not None:
                timestamp(source["occurred_at"])
            need(text(row.get("statement")) and isinstance(row.get("interpretation"), str),
                 "statement and interpretation must remain separate")
            scope = row.get("applies_to")
            need(isinstance(scope, dict) and strings(scope.get("systems"), nonempty=True)
                 and strings(scope.get("objects"), nonempty=True), "agreement applicability missing")
            for dimension in ("stages", "product_versions"):
                need(dimension not in scope or strings(scope[dimension], nonempty=True), "invalid agreement applicability dimension")
            need(strings(row.get("canonical_refs")) and isinstance(row.get("unresolved"), list)
                 and isinstance(row.get("changes"), list) and isinstance(row.get("supersedes"), list),
                 "agreement needs canonical refs, unresolved, changes and supersedes")
        visiting, done = set(), set()

        def visit(identity):
            need(identity not in visiting, "agreement supersedes cycle")
            if identity in done:
                return
            visiting.add(identity)
            for parent in seen[identity]["supersedes"]:
                parent = key(parent)
                need(parent in seen, "superseded agreement absent", "incomplete_history")
                visit(parent)
            visiting.remove(identity)
            done.add(identity)

        for identity in seen:
            visit(identity)
    elif kind == "goals":
        validate_goals(rows)
    else:
        need(text(document.get("system_id")), "tasks need system_id")
        seen = set()
        for row in rows:
            need(isinstance(row, dict) and text(row.get("task_id")), "task_id missing")
            need(row["task_id"] not in seen, "duplicate task_id")
            seen.add(row["task_id"])
            need(all(text(row.get(k)) for k in ("request_ref", "requirement_ref"))
                 and positive(row.get("input_revision")) and positive(row.get("plan_revision")),
                 "task needs source, input and plan revision")
            scope, assignment = row.get("scope"), row.get("assignment")
            need(isinstance(scope, dict) and text(scope.get("goal"))
                 and strings(scope.get("objects"), nonempty=True) and strings(scope.get("excluded")),
                 "task scope missing")
            need(isinstance(assignment, dict) and all(text(assignment.get(k)) for k in
                 ("executor", "delivery_owner")), "task assignment missing")
            acceptance = row.get("acceptance")
            need(isinstance(acceptance, list) and acceptance and all(isinstance(a, dict)
                 and text(a.get("id")) and text(a.get("criterion_ref")) for a in acceptance),
                 "task acceptance missing")
            need(len({a["id"] for a in acceptance}) == len(acceptance), "duplicate acceptance id")
            need(isinstance(row.get("agreements"), list), "task agreement references missing")
            for agreement in row["agreements"]:
                key(agreement)
            need(strings(row.get("write_scope")) and isinstance(row.get("execution"), dict)
                 and isinstance(row.get("native_binding"), dict)
                 and strings(row.get("stop_conditions")), "task execution/binding/scope missing")
            need(isinstance(row.get("plan_changes"), list), "task plan history missing")
            need("result_binding" not in row or isinstance(row["result_binding"], dict), "invalid result binding")
            dependencies = row.get("dependencies")
            need(isinstance(dependencies, list), "task dependencies missing")
            for dep in dependencies:
                need(isinstance(dep, dict) and dep.get("status") in ("proven", "unknown", "hypothesis")
                     and all(text(dep.get(k)) for k in ("producer_ref", "consumer_ref", "required_input_ref",
                         "required_version", "reason", "source_ref")), "invalid dependency basis")
    return document


def validate_events(events):
    need(isinstance(events, list) and len(events) <= MAX_EVENTS, "invalid or oversized event history")
    seen, system = set(), None
    for seq, event in enumerate(events, 1):
        format_check(event)
        need(event.get("seq") == seq and type(event.get("seq")) is int,
             "event sequence missing or incomplete", "incomplete_history")
        need(text(event.get("event_id")) and event["event_id"] not in seen, "duplicate or missing event_id")
        seen.add(event["event_id"])
        need(all(text(event.get(k)) for k in ("task_id", "request_ref", "system_id", "kind", "actor"))
             and positive(event.get("input_revision")) and positive(event.get("plan_revision")),
             "event identity/revisions missing")
        system = system or event["system_id"]
        need(event["system_id"] == system, "mixed system event history")
        for field in ("occurred_at", "observed_at", "read_at"):
            need(field in event, "event needs three distinct times, null if unknown")
            if event[field] is not None:
                timestamp(event[field])
        need(strings(event.get("evidence_refs")), "event needs evidence_refs")
        need("covers" not in event or strings(event["covers"], nonempty=True), "invalid criterion coverage")
        need("consumed_event_ids" not in event or strings(event["consumed_event_ids"], nonempty=True),
             "settlement frontier must explicitly name the consumed evidence events")
        if event["kind"] == "delivery_accepted":
            need(strings(event.get("effect_event_ids"), nonempty=True)
                 and strings(event.get("closes"), nonempty=True) and text(event.get("decision_ref")),
                 "acceptance needs explicit effect ids, closed criteria and decision reference")
        if event["kind"] == "goal_verification_pass":
            need(strings(event.get("covers"), nonempty=True) and isinstance(event.get("result_binding"), dict),
                 "goal verification needs exact scope and criterion ids")
    return events


def decode(kind, raw):
    try:
        if kind != "progress":
            return validate_snapshot(kind, parse_statement(raw.decode("utf-8")))
        need(not raw or raw.endswith(b"\n"), "incomplete event tail; original retained", "incomplete_history")
        # JSONL records end at LF. A bare CR may be JSON whitespace, but it
        # cannot separate two objects into two complete records.
        lines = raw.split(b"\n")[:-1]
        need(all(lines), "blank event line", "incomplete_history")
        return validate_events([parse_statement(line.decode("utf-8")) for line in lines])
    except FileError:
        raise
    except (UnicodeError, ValueError, TypeError, RecursionError) as exc:
        raise FileError("invalid_record", "invalid UTF-8 JSON; original retained") from exc


def binding_check(binding):
    fields = {"kind", "path", "format_version"}
    need(isinstance(binding, dict) and fields <= set(binding) <= fields | {"lock_path"},
         "binding must select one explicit kind/path/format_version")
    need(binding["kind"] in KINDS, "unsupported binding kind", "unsupported_format")
    format_check(binding)
    path = binding["path"]
    need(text(path) and Path(path).is_absolute() and bool(Path(path).name) and Path(path).as_posix() == path
         and ".." not in Path(path).parts and "." not in path.split("/"), "path must be absolute and canonical")
    if "lock_path" in binding:
        lock = binding["lock_path"]
        need(text(lock) and Path(lock).as_posix() == lock and ".." not in Path(lock).parts
             and "." not in lock.split("/") and Path(lock).parent == Path(path).parent
             and Path(lock).name != Path(path).name, "explicit lock must be another canonical file in the same parent")
    return Path(path)


def read_at(parent, name):
    try:
        fd = os.open(name, os.O_RDONLY | os.O_NONBLOCK | os.O_NOFOLLOW, dir_fd=parent)
    except FileNotFoundError:
        return None, None
    with os.fdopen(fd, "rb") as stream:
        before = os.fstat(stream.fileno())
        need(stat.S_ISREG(before.st_mode) and before.st_nlink == 1,
             "expected a single-link ordinary file", "read_unavailable")
        need(before.st_size <= MAX_BYTES, "file exceeds bounded adapter budget", "budget_exceeded")
        raw = stream.read(MAX_BYTES + 1)
        after = os.fstat(stream.fileno())
        signature = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        need(len(raw) <= MAX_BYTES, "file exceeds bounded adapter budget", "budget_exceeded")
        need(signature(before) == signature(after), "file changed during read", "revision_conflict")
        return raw, before


@contextlib.contextmanager
def locked(parent, name):
    fd = os.open(name, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW | os.O_NONBLOCK,
                 0o600, dir_fd=parent)
    try:
        info = os.fstat(fd)
        need(stat.S_ISREG(info.st_mode) and info.st_nlink == 1, "invalid file lock", "write_unavailable")
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise FileError("revision_conflict", "writer holds original file lock; reread before merge") from exc
        yield
    finally:
        os.close(fd)


def atomic_replace(parent, name, original, raw, mode):
    """Replace a bounded snapshot, including an immutable complete-line append.

    No partial tail is repaired implicitly. After replace, any durability failure
    is UNKNOWN and the caller must inspect this same object before recording again.
    """
    temporary = ".coordination-" + uuid.uuid4().hex
    replaced = False
    try:
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
        with os.fdopen(fd, "wb") as stream:
            stream.write(raw)
            stream.flush()
            os.fchmod(stream.fileno(), mode)
            os.fsync(stream.fileno())
        current, _ = read_at(parent, name)
        need(current == original, "object changed outside file lock; reread and merge", "revision_conflict")
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        replaced = True
        os.fsync(parent)
    except OSError as exc:
        raise FileError("write_outcome_unknown" if replaced else "write_unavailable",
                        "replacement may be visible; inspect original object" if replaced else
                        "record not replaced; original retained") from exc
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except OSError:
            pass


def preserve_extensions(previous, incoming, known):
    need(all(k in incoming and incoming[k] == v for k, v in previous.items() if k not in known),
         "unknown snapshot extensions must be retained; use original adapter for extension changes")


def preserve(kind, previous, incoming):
    """Reject silent loss of history; these checks do not approve the new intent."""
    need(previous["authority"] == incoming["authority"], "authority rebinding requires original owner migration")
    known = {"format_version", "revision", "change_id", "authority"} | {
        "agreements": {"agreements", "systems"}, "tasks": {"tasks", "system_id"}, "goals": {"goals"}}[kind]
    preserve_extensions(previous, incoming, known)
    if kind == "agreements":
        new = {key(row): row for row in incoming[kind]}
        for old in previous[kind]:
            need(key(old) in new, "agreement history cannot be removed")
            current = new[key(old)]
            excluded = {"status", "status_changes"}
            need({k: v for k, v in old.items() if k not in excluded} ==
                 {k: v for k, v in current.items() if k not in excluded}, "agreement version content is immutable")
            history = old.get("status_changes", [])
            updated = current.get("status_changes", [])
            need(isinstance(updated, list) and updated[:len(history)] == history, "status history cannot be removed")
            if current["status"] != old["status"]:
                need(len(updated) == len(history) + 1, "status change needs sourced append")
                change = updated[-1]
                need(isinstance(change, dict) and change.get("from") == old["status"]
                     and change.get("to") == current["status"] and text(change.get("source_ref"))
                     and text(change.get("reason")), "invalid status change provenance")
    elif kind == "goals":
        new = {g["goal_id"]: g for g in incoming[kind]}
        for old in previous[kind]:
            need(old["goal_id"] in new, "concrete issue history cannot be removed")
            current = new[old["goal_id"]]
            appended = {"reviews", "phase_refs", "repairs", "relations"}
            need({k: v for k, v in old.items() if k not in appended} ==
                 {k: v for k, v in current.items() if k not in appended},
                 "goal identity/criteria remain sourced; record a changed requirement as a linked goal")
            for field in appended:
                need(current.get(field, [])[:len(old.get(field, []))] == old.get(field, []),
                     "goal history is append-only: " + field)
    else:
        need(previous["system_id"] == incoming["system_id"], "cannot change task system identity")
        new = {row["task_id"]: row for row in incoming[kind]}
        for old in previous[kind]:
            need(old["task_id"] in new, "task/obligation history cannot be removed")
            current = new[old["task_id"]]
            preserve_extensions(old, current, TASK_FIELDS)
            if current == old:
                continue
            need(current["plan_revision"] == old["plan_revision"] + 1, "changed task needs next plan revision")
            history = old.get("plan_changes", [])
            need(current["plan_changes"][:len(history)] == history and
                 len(current["plan_changes"]) == len(history) + 1, "plan change needs sourced append")
            change = current["plan_changes"][-1]
            need(isinstance(change, dict) and change.get("revision") == current["plan_revision"]
                 and change.get("input_revision") == current["input_revision"] and text(change.get("source_ref"))
                 and text(change.get("kind")), "invalid plan change provenance")
            changed = any(current[k] != old[k] for k in REQUIREMENT_FIELDS)
            need(current["input_revision"] == old["input_revision"] + int(changed),
                 "requirement change increments input_revision; implementation change does not")
            old_history = old.get("input_history", [])
            expected = old_history + ([{k: old[k] for k in (*REQUIREMENT_FIELDS, "input_revision")}] if changed else [])
            need(current.get("input_history", []) == expected, "prior requirement/acceptance must remain in input_history")
            if changed:
                need(change["kind"] == "requirement_change", "changed requirement needs requirement_change source")


def file_operation(request):
    binding = request.get("binding")
    path = binding_check(binding)
    kind, operation = binding["kind"], request["operation"]
    with _node_root(path.parent) as parent:
        if operation == "read":
            raw, _ = read_at(parent, path.name)
            need(raw is not None, "explicit file is absent; binding not connected", "read_unavailable")
            value = decode(kind, raw)
            return {"status": "read", "binding": binding, "sha256": hashlib.sha256(raw).hexdigest(),
                    "read_at": dt.datetime.now(dt.timezone.utc).isoformat(),
                    **({"events": value, "last_seq": len(value), "history_complete_within_file": True}
                       if kind == "progress" else {"document": value, "revision": value["revision"]})}
        need(text(request.get("change_id")), "commit needs logical change_id")
        expected_field = "expected_last_seq" if kind == "progress" else "expected_revision"
        incompatible = "expected_revision" if kind == "progress" else "expected_last_seq"
        need(incompatible not in request and type(request.get(expected_field)) is int
             and request[expected_field] >= 0, "commit needs correct expected revision/seq")
        lock_name = Path(binding["lock_path"]).name if "lock_path" in binding else path.name + ".coordination.lock"
        with locked(parent, lock_name):
            original, info = read_at(parent, path.name)
            need(original is not None or (request.get("create") is True and request[expected_field] == 0),
                 "absent file requires explicit create and expected 0", "read_unavailable")
            previous = decode(kind, original) if original is not None else None
            if kind == "progress":
                event = dict(request.get("event", {}))
                need(event.get("change_id") == request["change_id"], "event change_id must match commit")
                line = encoded(event) + b"\n"
                for old, old_line in zip(previous or [], (original or b"").split(b"\n")[:-1]):
                    if old["event_id"] == event.get("event_id"):
                        need(old_line + b"\n" == line, "same event_id has different bytes", "event_conflict")
                        return {"status": "duplicate_reused", "binding": binding, "change_id": request["change_id"],
                                "last_seq": len(previous), "sha256": hashlib.sha256(original).hexdigest()}
                revision = len(previous or [])
                need(request[expected_field] == revision, "last sequence changed; reread", "revision_conflict")
                validate_events((previous or []) + [event])
                raw = (original or b"") + line
            else:
                revision = previous["revision"] if previous else 0
                need(request[expected_field] == revision, "snapshot revision changed; reread", "revision_conflict")
                incoming = validate_snapshot(kind, request.get("document"))
                need(incoming.get("change_id") == request["change_id"], "document change_id must match commit")
                need(incoming["revision"] == revision + 1, "snapshot must be exactly next revision")
                if previous is not None:
                    preserve(kind, previous, incoming)
                raw = encoded(incoming) + b"\n"
            need(len(raw) <= MAX_BYTES, "file exceeds bounded adapter budget", "budget_exceeded")
            atomic_replace(parent, path.name, original, raw, stat.S_IMODE(info.st_mode) if info else 0o600)
            try:
                readback, _ = read_at(parent, path.name)
            except (OSError, FileError) as exc:
                raise FileError("write_outcome_unknown", "record replaced but readback unavailable; inspect original object") from exc
            need(readback == raw, "replacement readback differs; inspect original object", "write_outcome_unknown")
            return {"status": "committed", "binding": binding, "change_id": request["change_id"],
                    "last_seq" if kind == "progress" else "revision": revision + 1,
                    "sha256": hashlib.sha256(raw).hexdigest(), "readback_verified": True}


def applicable(event, task, system_id):
    return (event["system_id"] == system_id and event["task_id"] == task["task_id"]
            and event["request_ref"] == task["request_ref"] and event["input_revision"] == task["input_revision"]
            and event["plan_revision"] <= task["plan_revision"])


def result_matches(event, task):
    binding = task.get("result_binding")
    return (isinstance(binding, dict) and all(text(binding.get(k)) for k in
            ("kind", "id", "node_id", "product_version", "content_ref", "effect_key"))
            and resource_scope_matches(event.get("result_binding"), binding))


def same_effect(event, task):
    original, current = event.get("result_binding"), task.get("result_binding")
    fields = ("kind", "id", "node_id", "effect_key")
    return (isinstance(original, dict) and isinstance(current, dict)
            and all(text(original.get(k)) and original.get(k) == current.get(k) for k in fields)
            and original.get("path", "") == current.get("path", ""))


def goal_action(value):
    """Canonical effect identity, independent of issue/path/task/sender labels."""
    need(isinstance(value, dict) and set(value) == {"object_ref", "destination_ref", "effect"}
         and all(text(v) for v in value.values()), "goal action needs exact object/destination/effect")
    return tuple(value[k] for k in ("object_ref", "destination_ref", "effect"))


def goal_rows(value, name):
    need(isinstance(value, list) and len(value) <= MAX_ROWS
         and all(isinstance(row, dict) and text(row.get("id")) for row in value)
         and len({row["id"] for row in value}) == len(value), "invalid/duplicate goal " + name)
    return value


def structured_attempt_ref(value):
    return (isinstance(value, dict) and set(value) == {"goal_id", "review_id", "attempt_id"}
            and all(text(v) for v in value.values()))


def validate_goal_review(goal, review):
    need(isinstance(review, dict) and all(text(review.get(k)) for k in
         ("id", "rationale", "reviewed_at")), "goal review needs model rationale and identity")
    timestamp(review["reviewed_at"])
    trigger = review.get("trigger")
    need(isinstance(trigger, dict) and trigger.get("kind") in
         ("return", "blocked", "resume", "natural_turn", "recurrence", "correction", "failure", "publication", "adoption")
         and text(trigger.get("ref")),
         "goal review needs an actual return/block/resume/natural turn/recurrence reference")
    need(strings(review.get("basis_refs"), nonempty=True), "goal review needs versioned evidence references")
    need(isinstance(review.get("evidence_context", {}), dict), "evidence context must use the existing source contract")
    control = review.get("user_control")
    if control is not None:
        need(isinstance(control, dict) and control.get("kind") in ("user_stop", "user_resume")
             and all(text(control.get(k)) for k in ("id", "source_ref")), "user control needs exact sourced stop/resume")
        need(control["kind"] != "user_resume" or text(control.get("supersedes")),
             "only explicit user resumption tied to the original stop can resume")
    criteria = goal_rows(review.get("criteria"), "criterion assessments")
    need({a["id"] for a in criteria} == {a["id"] for a in goal["criteria"]},
         "review must assess this concrete issue's own criteria")
    for item in criteria:
        need(item.get("status") in ("met", "unmet", "unknown") and text(item.get("reason"))
             and strings(item.get("evidence_refs")), "criterion assessment needs reason and evidence")
        need(item["status"] != "met" or item["evidence_refs"], "met criterion needs actual evidence")
    for question in goal_rows(review.get("causal_questions"), "causal questions"):
        need(text(question.get("question")) and question.get("status") in ("open", "answered", "ruled_out")
             and type(question.get("required_for_closure")) is bool and strings(question.get("evidence_refs")),
             "causal question needs explicit uncertainty, applicability and evidence")
        need(question["status"] == "open" or question["evidence_refs"], "resolved cause needs evidence")
    for path in goal_rows(review.get("paths"), "evidence paths"):
        goal_action(path.get("action"))
        need("basis_refs" not in path or strings(path["basis_refs"], nonempty=True),
             "path-specific evidence basis must name actual relevant source revisions")
        need(text(path.get("purpose")) and strings(path.get("question_ids"))
             and set(path["question_ids"]) <= {q["id"] for q in review["causal_questions"]},
             "path must explain the uncertainty it can resolve")
        for field, statuses in (("permission", ("allowed", "denied", "unknown")),
                                ("capability", ("supported", "unavailable", "unknown"))):
            basis = path.get(field)
            need(isinstance(basis, dict) and basis.get("status") in statuses
                 and strings(basis.get("evidence_refs"), nonempty=True), "path needs actual " + field + " basis")
        need(isinstance(path.get("dependencies"), list), "path dependencies must be explicit")
        for dep in path["dependencies"]:
            need(isinstance(dep, dict) and text(dep.get("ref")) and text(dep.get("reason"))
                 and dep.get("status") in ("satisfied", "missing", "unknown")
                 and strings(dep.get("evidence_refs"), nonempty=True), "invalid actual path dependency")
    for attempt in goal_rows(review.get("attempts"), "actual attempts"):
        goal_action(attempt.get("action"))
        need(attempt.get("result") in ("done", "failed", "denied", "unknown")
             and strings(attempt.get("basis_refs"), nonempty=True)
             and strings(attempt.get("evidence_refs"), nonempty=True), "actual attempt needs original result/evidence")
    for reconciliation in goal_rows(review.get("reconciliations", []), "original-result reconciliations"):
        goal_action(reconciliation.get("action"))
        need((text(reconciliation.get("attempt_ref")) or structured_attempt_ref(reconciliation.get("attempt_ref")))
             and reconciliation.get("result") in ("committed", "not_executed")
             and strings(reconciliation.get("evidence_refs"), nonempty=True)
             and isinstance(reconciliation.get("proof"), dict), "reconciliation needs proof of the exact original outcome")
    decision = review.get("decision")
    need(isinstance(decision, dict) and decision.get("kind") in ("act", "wait", "close")
         and text(decision.get("owner_ref")) and text(decision.get("reason")), "model must choose action or evidenced wait/closure proposal")
    if decision["kind"] == "act":
        need(text(decision.get("path_id")), "action decision needs a concrete evidence path")
    elif decision["kind"] == "wait":
        conditions = decision.get("resume_when")
        need(isinstance(conditions, list) and conditions and all(isinstance(c, dict)
             and all(text(c.get(k)) for k in ("source_ref", "condition", "owner_ref")) for c in conditions),
             "waiting needs an observable evidence change, source and responsible owner")
    else:
        need(text(decision.get("acceptance_ref")), "closure proposal needs original owner acceptance reference")


def validate_goals(rows):
    goal_rows([dict(g, id=g.get("goal_id")) if isinstance(g, dict) else g for g in rows], "concrete issues")
    for goal in rows:
        need(all(text(goal.get(k)) for k in ("goal_id", "source_ref", "statement", "owner_ref"))
             and strings(goal.get("original_refs"), nonempty=True) and strings(goal.get("phase_refs")),
             "goal must link the original requirement/objects and finite phases")
        criteria = goal_rows(goal.get("criteria"), "closure criteria")
        need(criteria and all(text(c.get("criterion_ref")) for c in criteria), "define closure per concrete issue")
        for relation in goal.get("relations", []):
            need(isinstance(relation, dict) and text(relation.get("issue_ref"))
                 and relation.get("status") in ("hypothesis", "supported", "ruled_out")
                 and text(relation.get("statement")) and strings(relation.get("evidence_refs")),
                 "cross-issue relation must retain its evidence level")
            need(relation["status"] == "hypothesis" or relation["evidence_refs"], "non-hypothetical relation needs evidence")
        for repair in goal.get("repairs", []):
            need(isinstance(repair, dict) and text(repair.get("repair_ref"))
                 and all(strings(repair.get(k)) for k in
                         ("version_refs", "node_refs", "verification_refs", "recurrence_refs", "impact_refs")),
                 "repair history needs applicability, verification, recurrence and domain impact references")
        reviews = goal_rows(goal.get("reviews"), "reviews")
        for index, review in enumerate(reviews):
            validate_goal_review(goal, review)
            if index:
                old_questions = {q["id"] for q in reviews[index - 1]["causal_questions"]}
                need(old_questions <= {q["id"] for q in review["causal_questions"]},
                     "unresolved causal questions cannot silently disappear at a phase boundary")


def goal_dependency_supported(review, dependency):
    """Reuse core source verification and exact resource scope, not a status label.

    This is for a consumer that claims a returned result is ready. Read-only
    discovery paths need no fabricated completed producer. Stable historical
    evidence is reusable by the model; a new dependent dispatch needs a current
    observation of that producer's applicable result and absence of in-flight work.
    """
    proof = dependency.get("proof", {})
    if not isinstance(proof, dict):
        return False
    record, expected = proof.get("record"), proof.get("expected_scope")
    if not isinstance(record, dict) or not isinstance(expected, dict):
        return False
    if not all(text(expected.get(k)) for k in ("kind", "id", "node_id", "product_version", "content_ref", "request_ref")):
        return False
    if not positive(expected.get("input_revision")) or not resource_scope_matches(record.get("resource"), expected):
        return False
    maximum = proof.get("max_age_seconds")
    if type(maximum) is not int or not 0 <= maximum <= 86400:
        return False
    body = source_body(review.get("evidence_context", {}), record)
    if body is None and record.get("source") == "node":
        project = proof.get("project", {})
        if not isinstance(project, dict) or not verify_source({}, project, record):
            return False
        try:
            base = Path(project["root"])
            with _node_root(base) as descriptor:
                raw = _node_bytes(descriptor, Path(record["node_path"]).parts)
            if raw is None or hashlib.sha256(raw).hexdigest() != record.get("digest"):
                return False
            body = raw.decode("utf-8")
        except (OSError, ValueError, UnicodeError):
            return False
    if body is None:
        return False
    try:
        actual = parse_statement(body)
        if not (isinstance(actual, dict) and resource_scope_matches(actual.get("resource"), expected)
                and actual.get("result") == record.get("result")
                and actual["result"] in ("passed", "verified", "applied", "observed")
                and actual.get("status") == "completed"):
            return False
        age = (timestamp(review["reviewed_at"]) - timestamp(actual.get("observed_at"))).total_seconds()
        return 0 <= age <= maximum
    except (FileError, ValueError, TypeError):
        return False


def goal_review(request):
    """Build a bounded evidence packet for the model; never run a proposed action.

    Stated permission, evidence authenticity, root cause and domain acceptance
    remain model/owner judgments. No URI is followed. Only an explicitly bound
    node-file dependency proof uses the existing core source reader.
    """
    document = validate_snapshot("goals", request.get("goals"))
    goal = next((g for g in document["goals"] if g["goal_id"] == request.get("goal_id")), None)
    need(goal is not None, "select an existing concrete issue; no implicit registration")
    review = request.get("review")
    if review is None:
        state = "OPEN"
        if goal["reviews"]:
            state = goal_review(dict(request, review=goal["reviews"][-1]))["goal_state"]
        return {"status": "goal_review", "goal_id": goal["goal_id"], "goal_state": state,
                "model_review_needed": True, "original_refs": goal["original_refs"],
                "criteria": goal["criteria"], "phase_refs": goal["phase_refs"],
                "last_review": goal["reviews"][-1] if goal["reviews"] else None,
                "meaning": "read current canonical evidence and judge this issue again at the actual trigger"}
    validate_goal_review(goal, review)
    if any(r["id"] == review["id"] for r in goal["reviews"]):
        need(goal["reviews"][-1] == review, "review replay must match the exact latest record")
    previous = [r for r in goal["reviews"] if r["id"] != review["id"]]
    if previous:
        need({q["id"] for q in previous[-1]["causal_questions"]} <= {q["id"] for q in review["causal_questions"]},
             "carry prior causal questions into the current model judgment")
    controls = [r["user_control"] for r in previous if r.get("user_control")]
    stopped_by = None
    control_issue = False
    for control in controls + ([review["user_control"]] if review.get("user_control") else []):
        if control["kind"] == "user_stop":
            stopped_by = control
        elif stopped_by and control.get("supersedes") == stopped_by["id"]:
            stopped_by = None
        else:
            control_issue = True
    # Restrictions bind effects across issue/task/path labels. A new case cannot
    # grant a denied write. UNKNOWN reconciliation remains an original-object
    # investigation, not an automatic retry switch in this optional helper.
    reviews = [(g["goal_id"], r) for g in document["goals"] for r in g["reviews"]]
    reviews += [(goal["goal_id"], review)]
    attempts = {(identity, r["id"], a["id"]): a for identity, r in reviews for a in r["attempts"]}
    aliases = {}
    for identity in attempts:
        aliases.setdefault("/".join(identity), []).append(identity)
    def attempt_identity(reference):
        if structured_attempt_ref(reference):
            return tuple(reference[k] for k in ("goal_id", "review_id", "attempt_id"))
        # Read legacy references only when their components are unambiguous.
        # A collision can never erase a refusal or reconcile an UNKNOWN.
        matches = aliases.get(reference, []) if text(reference) else []
        return matches[0] if len(matches) == 1 else None
    reconciled = {}
    reconciliation_gaps = []
    for _, r in reviews:
        for resolution in r.get("reconciliations", []):
            identity = attempt_identity(resolution["attempt_ref"])
            target = attempts.get(identity)
            if identity is None:
                reconciliation_gaps.append({"attempt_ref": resolution["attempt_ref"], "reason": "missing_or_ambiguous_original_attempt"})
            scope = resolution["proof"].get("expected_scope", {})
            if (isinstance(scope, dict) and target and target["result"] == "unknown" and
                    goal_action(target["action"]) == goal_action(resolution["action"]) and
                    attempt_identity(scope.get("original_attempt_ref")) == identity and
                    scope.get("effect_outcome") == resolution["result"] and
                    scope.get("effect_action") == resolution["action"] and
                    goal_dependency_supported(r, {"proof": resolution["proof"]})):
                reconciled.setdefault(identity, set()).add(resolution["result"])
    candidates = []
    for path in review["paths"]:
        reasons = []
        action = goal_action(path["action"])
        matching = [(ref, a) for ref, a in attempts.items() if goal_action(a["action"]) == action]
        if stopped_by:
            reasons.append("user_stopped")
        if path["permission"]["status"] != "allowed":
            reasons.append("permission_" + path["permission"]["status"])
        if path["capability"]["status"] != "supported":
            reasons.append("capability_" + path["capability"]["status"])
        reasons.extend("dependency_" + d["status"] + ":" + d["ref"] for d in path["dependencies"]
                       if d["status"] != "satisfied")
        reasons.extend("dependency_result_unverified:" + d["ref"] for d in path["dependencies"]
                       if d["status"] == "satisfied" and not goal_dependency_supported(review, d))
        if any(a["result"] == "denied" for _, a in matching):
            reasons.append("original_denial_retained")
        if any(a["result"] == "unknown" and len(reconciled.get(ref, set())) != 1 for ref, a in matching):
            reasons.append("inspect_original_effect_before_any_retry")
        if any(reconciled.get(ref) == {"committed"} for ref, _ in matching):
            reasons.append("original_effect_already_committed_do_not_repeat")
        basis = path.get("basis_refs", review["basis_refs"])
        if any(set(basis) <= set(a["basis_refs"]) for _, a in matching):
            reasons.append("no_new_evidence_for_same_effect")
        candidates.append({"path_id": path["id"], "eligible_in_supplied_records": not reasons,
                           "reasons": reasons, "action": path["action"], "purpose": path["purpose"]})
    decision, issues = review["decision"], []
    if control_issue:
        issues.append("user_control_provenance_mismatch")
    eligible = [p for p in candidates if p["eligible_in_supplied_records"]]
    chosen = next((p for p in eligible if p["path_id"] == decision.get("path_id")), None)
    unresolved = [q["id"] for q in review["causal_questions"] if q["status"] == "open" and q["required_for_closure"]]
    pending = [c["id"] for c in review["criteria"] if c["status"] != "met"]
    if decision["kind"] == "act" and chosen is None:
        issues.append("chosen_path_not_supported")
    if decision["kind"] == "wait" and eligible:
        issues.append("independent_evidence_path_available")
    if decision["kind"] == "close":
        if stopped_by:
            issues.append("user_stop_requires_explicit_resumption")
        if pending or unresolved:
            issues.append("root_criteria_or_required_cause_unresolved")
        if decision["owner_ref"] != goal["owner_ref"]:
            issues.append("closure_owner_mismatch")
        if review["trigger"]["kind"] == "recurrence":
            issues.append("recurrence_requires_fresh_issue_verification")
    changed = not previous or set(review["basis_refs"]) != set(previous[-1]["basis_refs"])
    return {"status": "goal_review", "goal_id": goal["goal_id"],
            "goal_state": "USER_STOPPED" if stopped_by else
                "CLOSED_IN_SUPPLIED_RECORDS" if decision["kind"] == "close" and not issues else "OPEN",
            "user_stop": stopped_by,
            "model_review_needed": bool(issues), "decision_supported": not issues,
            "decision_issues": issues, "pending_criteria": pending, "unresolved_causal_questions": unresolved,
            "paths": candidates, "selected_next_action": chosen if not issues and decision["kind"] == "act" else None,
            "wait": decision if not issues and decision["kind"] == "wait" else None,
            "evidence_changed": changed, "record_needed": changed or not previous or
                previous[-1]["decision"] != decision or previous[-1]["criteria"] != review["criteria"] or
                previous[-1]["causal_questions"] != review["causal_questions"] or
                previous[-1]["paths"] != review["paths"] or bool(review["attempts"]) or bool(review.get("user_control")),
            "reconciled_original_attempts": [{"attempt_ref": dict(zip(("goal_id", "review_id", "attempt_id"), k)),
                                              "results": sorted(v)} for k, v in reconciled.items()],
            "reconciliation_gaps": reconciliation_gaps,
            "action_executed": False, "root_cause_verified": False,
            "meaning": "model judgment over referenced evidence; finite phase completion never closes an issue"}


def view(request):
    agreements = validate_snapshot("agreements", request.get("agreements"))
    tasks = validate_snapshot("tasks", request.get("tasks"))
    events = validate_events(request.get("progress"))
    as_of = timestamp(request.get("as_of"))
    budget = request.get("max_age_seconds")
    need(type(budget) is int and budget >= 0, "view needs explicit source freshness budget")
    read_gaps = request.get("source_read_gaps", [])
    need(isinstance(read_gaps, list) and len(read_gaps) <= MAX_ROWS and all(isinstance(gap, dict)
         and all(text(gap.get(k)) for k in ("task_id", "source_ref", "status")) for gap in read_gaps),
         "source read gaps need an explicit consumer task and source reference")
    by_agreement = {key(row): row for row in agreements["agreements"]}
    projected = []
    for task in tasks["tasks"]:
        current = [e for e in events if applicable(e, task, tasks["system_id"])]
        historical = [e["event_id"] for e in events if e["task_id"] == task["task_id"] and e not in current]
        gaps = []
        gaps.extend("source_read_gap:" + gap["source_ref"] for gap in read_gaps if gap["task_id"] == task["task_id"])
        if request.get("history_complete") is not True:
            gaps.append("history_completeness_not_supplied")
        for ref in task["agreements"]:
            agreement = by_agreement.get(key(ref))
            if (not agreement or agreement["status"] != "effective" or agreement["unresolved"] or
                    tasks["system_id"] not in agreement["applies_to"]["systems"] or
                    not set(task["scope"]["objects"]) <= set(agreement["applies_to"]["objects"])):
                gaps.append("agreement_not_effective_or_applicable:" + "@".join(key(ref)))
            if agreement and any(other is not agreement and other["id"] == agreement["id"]
                                 and other["status"] == "effective" for other in agreements["agreements"]):
                gaps.append("agreement_conflicting_effective_versions:" + agreement["id"])
            if agreement and ref.get("source_ref", agreements["authority"]["storage_ref"]) != agreements["authority"]["storage_ref"]:
                gaps.append("agreement_source_binding_mismatch:" + agreement["id"])
            if agreement:
                scope = agreement["applies_to"]
                if "stages" in scope and task["scope"].get("stage") not in scope["stages"]:
                    gaps.append("agreement_stage_unverified:" + agreement["id"])
                if "product_versions" in scope and task.get("result_binding", {}).get("product_version") not in scope["product_versions"]:
                    gaps.append("agreement_version_unverified:" + agreement["id"])
        restrictions = [e for e in events if e["kind"] in ("platform_denied", "effect_unknown")
                        and (same_effect(e, task) or (e["system_id"] == tasks["system_id"]
                             and e["task_id"] == task["task_id"]))]
        # A PASS, new task/input id or owner label cannot clear these records.
        # Original-state reconciliation is a reported fact, never retry authority.
        unresolved = []
        for restriction in restrictions:
            resolved = [e for e in current if e["kind"] == "original_effect_reconciled"
                        and e.get("related_event_id") == restriction["event_id"]
                        and restriction["kind"] == "effect_unknown" and e.get("result") == "committed"
                        and same_effect(e, task) and e["evidence_refs"]
                        and e["actor"] == task["assignment"]["delivery_owner"]
                        and e["seq"] > restriction["seq"]]
            if not resolved:
                unresolved.append(restriction["event_id"])
        if unresolved:
            gaps.append("original_restrictions_unresolved")
        accepted, acceptance_effects = {}, {}
        for acceptance in [e for e in current if e["kind"] == "delivery_accepted"]:
            candidates = {e["event_id"]: e for e in current if e["seq"] < acceptance["seq"]}
            effects = [candidates.get(i) for i in acceptance.get("effect_event_ids", [])]
            valid = (acceptance["actor"] == task["assignment"]["delivery_owner"]
                     and text(acceptance.get("decision_ref")) and acceptance["evidence_refs"]
                     and acceptance["observed_at"] is not None and timestamp(acceptance["observed_at"]) <= as_of
                     and effects and all(e and e["kind"] == "goal_verification_pass" and e.get("result") == "PASS"
                         and result_matches(e, task) and e["evidence_refs"] and e["observed_at"] is not None
                         and timestamp(e["observed_at"]) <= as_of for e in effects))
            if valid:
                for identity in acceptance.get("closes", []):
                    supporting = [e for e in effects if identity in e.get("covers", [])]
                    if supporting:
                        accepted[identity] = acceptance["event_id"]
                        acceptance_effects[identity] = max(supporting, key=lambda e: e["seq"])["event_id"]
        event_sequences = {e["event_id"]: e["seq"] for e in current}
        later_failures = {identity: [e["event_id"] for e in current
                         if result_matches(e, task) and e.get("result") in ("FAIL", "UNKNOWN")
                         and e["kind"] in ("goal_verification_fail", "delivery_rejected")
                         # Repeating an owner decision cannot refresh the
                         # cited effect or erase a failure after that effect.
                         and e["seq"] > event_sequences[acceptance_effects[identity]]
                         and ("covers" not in e or identity in e["covers"])]
                         for identity in accepted}
        pending = [a["id"] for a in task["acceptance"]
                   if a["id"] not in accepted or gaps or later_failures.get(a["id"])]
        latest = current[-1] if current else None
        observed = timestamp(latest["observed_at"]) if latest and latest["observed_at"] else None
        age = (as_of - observed).total_seconds() if observed else None
        freshness = "unknown" if age is None or age < 0 else ("fresh" if age <= budget else "stale")
        successors = [e["event_id"] for e in current if e["kind"] == "successor_executed"
                      and e["evidence_refs"] and e.get("related_event_id") in set(accepted.values())
                      and e["seq"] > event_sequences[e["related_event_id"]]]
        stopped = [e["event_id"] for e in current if e["kind"] == "legal_stop_recorded"
                   and e["evidence_refs"] and text(e.get("reason"))]
        facts = {e["event_id"] for e in current if e["kind"] not in ("successor_executed", "legal_stop_recorded")}
        by_event = {e["event_id"]: e for e in current}
        frontiers, unbound = {}, []
        for identity in successors + stopped:
            event = by_event[identity]
            consumed = event.get("consumed_event_ids")
            valid = (strings(consumed, nonempty=True) and event["observed_at"] is not None
                     and timestamp(event["observed_at"]) <= as_of
                     and all(ref in by_event and by_event[ref]["seq"] < event["seq"] for ref in consumed))
            if valid:
                # Explicit causal consumption is required. Source times only
                # reject an impossible claim; later bookkeeping seq proves no
                # knowledge of the returns already in the journal.
                valid = all(by_event[ref]["observed_at"] is not None and
                            timestamp(by_event[ref]["observed_at"]) <= timestamp(event["observed_at"])
                            for ref in consumed)
            if valid:
                frontiers[identity] = set(consumed)
            else:
                unbound.append(identity)
        # A newer decision also acknowledges any earlier decisions/late ledger
        # entries it actually reviewed. Missing legacy bindings require review,
        # but an explicit later frontier can finish that review without erasing
        # the old records or turning their append order into causal knowledge.
        settled = [identity for identity, consumed in frontiers.items() if set(by_event) - {identity} <= consumed]
        settled_through = max((event_sequences[ref] for consumed in frontiers.values() for ref in consumed if ref in facts), default=0)
        projected.append({"task_id": task["task_id"], "input_revision": task["input_revision"],
            "scope_accepted_in_supplied_records": not pending, "pending_acceptance": pending,
            "acceptance_events": accepted, "acceptance_effects": acceptance_effects,
            "later_effect_failures": later_failures,
            "gaps": gaps, "historical_event_ids": historical,
            "recorded_failures": [e["event_id"] for e in events if e["task_id"] == task["task_id"]
                                  and e.get("result") in ("FAIL", "UNKNOWN")],
            "recorded_restrictions": [e["event_id"] for e in restrictions], "unresolved_restrictions": unresolved,
            "latest_observation": {"event_id": latest["event_id"] if latest else None,
                                   "observed_at": latest["observed_at"] if latest else None,
                                   "age_seconds": age, "freshness": freshness},
            "dependencies": task["dependencies"], "successor_event_ids": successors,
            "stop_event_ids": stopped,
            "continuation_settled_through_seq": settled_through,
            "continuation_frontier_event_ids": sorted(facts, key=event_sequences.get),
            "settlement_event_ids": settled, "unbound_settlement_event_ids": unbound,
            "next_action_review_needed": not settled})
    return {"status": "read_only_view", "tasks": projected,
            "source_read_gaps": request.get("source_read_gaps", []),
            "meaning": "relationships in supplied records only; stable scoped acceptance does not expire by age"}


def handle(request):
    operation = request.get("operation") if isinstance(request, dict) else None
    output = {"schema": "coordination-files-result-v1", "operation": operation,
              "domain_action_performed": False, "limitations": LIMITS}
    try:
        need(operation in ("read", "view", "commit", "goal-review"), "operation must be read/view/commit/goal-review")
        # Reject action-like parameters instead of suggesting an alternate route.
        allowed = ({"operation", "agreements", "tasks", "progress", "as_of", "max_age_seconds",
                    "history_complete", "source_read_gaps"} if operation == "view" else
                   {"operation", "goals", "goal_id", "review"} if operation == "goal-review" else
                   {"operation", "binding"} if operation == "read" else
                   {"operation", "binding", "expected_revision", "expected_last_seq", "create",
                    "change_id", "document", "event"})
        need(set(request) <= allowed, "unknown operation parameter")
        output.update(goal_review(request) if operation == "goal-review" else
                      view(request) if operation == "view" else file_operation(request))
    except FileError as exc:
        output.update(status=exc.status, reason=str(exc), binding=request.get("binding") if isinstance(request, dict) else None,
                      change_id=request.get("change_id") if isinstance(request, dict) else None,
                      recovery="inspect_original_object_and_repair_records_only; do_not_repeat_domain_action")
    except OSError as exc:
        output.update(status="read_unavailable" if operation == "read" else "write_unavailable",
                      errno=exc.errno, reason="explicit file/parent unavailable; no implicit directory creation")
    except (ValueError, TypeError, RecursionError, OverflowError) as exc:
        output.update(status="invalid_record", reason="invalid JSON shape or bounded value")
    return output


def dispatch():
    from execution_check import loaded_tool_revision, loaded_tool_version
    try:
        raw = sys.stdin.buffer.read(MAX_REQUEST_BYTES + 1)
        need(len(raw) <= MAX_REQUEST_BYTES, "request exceeds bounded budget", "budget_exceeded")
        request = parse_statement(raw.decode("utf-8"))
        output = handle(request)
    except (ValueError, UnicodeError, RecursionError) as exc:
        output = {"schema": "coordination-files-result-v1", "status": getattr(exc, "status", "invalid_record"),
                  "reason": "invalid or oversized UTF-8 JSON request", "domain_action_performed": False}
    output.update(tool_revision=loaded_tool_revision(), tool_version=loaded_tool_version())
    print(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False))
    return 0 if output["status"] in ("read", "read_only_view", "goal_review", "committed", "duplicate_reused") else 1
