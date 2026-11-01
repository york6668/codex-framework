"""Persistent, node-local anomaly interlock for native Codex usage telemetry.

Tokens/responses are separate proxy measures, never credits or a billing cap.
No scheduler, model call, account request, or automatic unlock is performed.
"""
import hashlib
import json
import math
import re
import sqlite3
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

SCHEMA = "native-usage-guard-v1"
METRICS = ("responses", "uncached_input", "cached_input", "output")


def encoded(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest(value):
    return hashlib.sha256(encoded(value).encode()).hexdigest()


def source_connection(path):
    """Never create a source DB or silently omit a live WAL."""
    path = Path(path).resolve(strict=True)
    uri = path.as_uri() + "?mode=ro"
    # SQLite may need to create a shared-memory file even in read-only mode.
    # Immutable is safe only when no WAL exists; never use it to hide a WAL.
    if not Path(str(path) + "-wal").exists():
        uri += "&immutable=1"
    return sqlite3.connect(uri, uri=True, timeout=5)


def policy(path):
    p = json.loads(Path(path).read_text())
    if not isinstance(p, dict) or p.get("schema") != SCHEMA:
        raise ValueError("unsupported usage policy")
    for key in ("source_db", "state_db", "timezone", "workload", "roots", "reviewed_days"):
        if key not in p:
            raise ValueError("missing policy field: " + key)
    if not isinstance(p["roots"], list) or not p["roots"] or any(not isinstance(r, str) or not r for r in p["roots"]):
        raise ValueError("explicit native root thread identities required")
    if len(set(p["roots"])) != len(p["roots"]):
        raise ValueError("duplicate native roots")
    if Path(p["source_db"]).resolve() == Path(p["state_db"]).resolve():
        raise ValueError("meter state must not overwrite native source")
    ZoneInfo(p["timezone"])
    if not isinstance(p["workload"], str) or not p["workload"]:
        raise ValueError("fixed comparable workload required")
    if not isinstance(p["reviewed_days"], list):
        raise ValueError("reviewed_days must be a list")
    days = set()
    for row in p["reviewed_days"]:
        day = datetime.strptime(row["date"], "%Y-%m-%d").date()
        if day in days or not row.get("evidence") or row.get("workload") != p["workload"]:
            raise ValueError("distinct reviewed normal days with workload and evidence required")
        if not re.fullmatch(r"[0-9a-f]{64}", row.get("usage_fingerprint", "")):
            raise ValueError("reviewed day must bind its exact observed usage fingerprint")
        days.add(day)
    return p


def connect(path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA synchronous=FULL")
    db.executescript("""
      CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS cursors (path TEXT PRIMARY KEY, device INTEGER,
        inode INTEGER, offset INTEGER NOT NULL, model TEXT NOT NULL);
      CREATE TABLE IF NOT EXISTS responses (id TEXT PRIMARY KEY, thread TEXT NOT NULL,
        stamp REAL NOT NULL, model TEXT NOT NULL, input INTEGER NOT NULL,
        cached INTEGER NOT NULL, output INTEGER NOT NULL);
      CREATE INDEX IF NOT EXISTS response_time ON responses(stamp);
      CREATE TABLE IF NOT EXISTS history (id INTEGER PRIMARY KEY, at TEXT NOT NULL,
        action TEXT NOT NULL, detail TEXT NOT NULL);
    """)
    return db


def get(db, key):
    row = db.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return json.loads(row[0]) if row else None


def put(db, key, value):
    db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (key, encoded(value)))


def scan(db, p, now):
    """Incremental bounded-memory reads; one globally unique response per event."""
    with source_connection(p["source_db"]) as source:
        source.row_factory = sqlite3.Row
        threads = {r["id"]: dict(r) for r in source.execute("SELECT id,rollout_path,source FROM threads")}
        if not set(p["roots"]) <= threads.keys():
            raise ValueError("a configured native root is missing")
        parents = {}
        if source.execute("SELECT 1 FROM sqlite_master WHERE name='thread_spawn_edges'").fetchone():
            parents.update((r[1], r[0]) for r in source.execute("SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges"))
        for tid, row in threads.items():
            try:
                parents[tid] = json.loads(row["source"])["subagent"]["thread_spawn"]["parent_thread_id"]
            except (KeyError, ValueError, TypeError):
                pass
    selected = set(p["roots"])
    while True:
        children = {tid for tid, parent in parents.items() if parent in selected}
        if children <= selected:
            break
        selected |= children
    if not selected <= threads.keys():
        raise ValueError("native descendant metadata missing")
    for tid in sorted(selected):
        path = Path(threads[tid]["rollout_path"]).resolve(strict=True)
        stat = path.stat()
        old = db.execute("SELECT * FROM cursors WHERE path=?", (str(path),)).fetchone()
        same = old and (old["device"], old["inode"]) == (stat.st_dev, stat.st_ino) and old["offset"] <= stat.st_size
        offset, model = (old["offset"], old["model"]) if same else (0, "UNKNOWN")
        with path.open("rb") as stream:
            stream.seek(offset)
            while True:
                line = stream.readline(16 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 16 * 1024 * 1024:
                    raise ValueError("native record exceeds bounded reader")
                if not line.endswith(b"\n"):
                    break  # Do not consume a partially written final record.
                event = json.loads(line)
                v = event.get("payload", {})
                if event.get("type") == "turn_context":
                    model = str(v.get("model", "UNKNOWN")) + "/" + str(v.get("effort", "UNKNOWN"))
                # Forked rollouts can contain inherited parent records. Count
                # each thread only in its own authoritative rollout.
                if event.get("type") == "token_usage_record" and v.get("thread_id", tid) == tid:
                    rid, usage = v.get("response_id"), v.get("usage", {})
                    values = [usage.get(k) for k in ("input_tokens", "cached_input_tokens", "output_tokens")]
                    if not rid or any(type(n) is not int or n < 0 for n in values) or values[1] > values[0] or "UNKNOWN" in model:
                        raise ValueError("incomplete native response usage")
                    stamp = datetime.fromisoformat(event["timestamp"].replace("Z", "+00:00"))
                    if stamp.tzinfo is None or stamp.timestamp() > now.timestamp() + 60:
                        raise ValueError("native usage clock is invalid")
                    record = (tid, stamp.timestamp(), model, *values)
                    prior = db.execute("SELECT thread,stamp,model,input,cached,output FROM responses WHERE id=?", (rid,)).fetchone()
                    if prior and tuple(prior) != record:
                        raise ValueError("conflicting native response identity")
                    db.execute("INSERT OR IGNORE INTO responses VALUES (?,?,?,?,?,?,?)", (rid, *record))
                offset = stream.tell()
        db.execute("INSERT OR REPLACE INTO cursors VALUES (?,?,?,?,?)", (str(path), stat.st_dev, stat.st_ino, offset, model))
    put(db, "scope_threads", sorted(selected))
    return {"threads": len(selected), "records": db.execute("SELECT COUNT(*) FROM responses").fetchone()[0], "observed_at": now.isoformat()}


def day_class(day, p):
    return p.get("day_classes", {}).get(day.isoformat(), "weekend" if day.weekday() >= 5 else "weekday")


def baseline_candidates(db, p, now):
    """Bounded metadata for human review; never declares a day normal."""
    zone = ZoneInfo(p["timezone"])
    today = now.astimezone(zone).date()
    start = datetime.combine(today - timedelta(days=60), datetime.min.time(), zone).timestamp() - 3600
    end = datetime.combine(today, datetime.min.time(), zone).timestamp()
    scope = set(get(db, "scope_threads") or p["roots"])
    days = defaultdict(list)
    for row in db.execute("SELECT id,thread,stamp,model,input,cached,output FROM responses WHERE stamp>=? AND stamp<? ORDER BY id", (start, end)):
        if row["thread"] in scope:
            days[datetime.fromtimestamp(row["stamp"], zone).date().isoformat()].append(tuple(row))
    result = []
    for day, rows in sorted(days.items()):
        d = datetime.fromisoformat(day).date()
        if d < today - timedelta(days=60):
            continue
        midnight = datetime.combine(d, datetime.min.time(), zone).timestamp()
        leading = [r for r in days.get((d - timedelta(days=1)).isoformat(), []) if r[2] >= midnight - 3600]
        result.append({"date": day, "day_class": day_class(d, p), "responses": len(rows),
                       "usage_fingerprint": digest(sorted(rows + leading)), "review_status": "UNREVIEWED",
                       "coverage": "Observed day plus preceding 60 minutes only; review complete comparable work and source coverage."})
    return result


def compare(db, p, now):
    local = now.astimezone(ZoneInfo(p["timezone"]))
    day, seconds = local.date(), local.hour * 3600 + local.minute * 60 + local.second
    peers = [datetime.strptime(r["date"], "%Y-%m-%d").date() for r in p["reviewed_days"]]
    peers = [d for d in peers if day - timedelta(days=60) <= d < day and day_class(d, p) == day_class(day, p)]
    # Reviewed normal days are frozen inputs, never automatically learned from
    # the incident day, future dates, unreviewed days, or another workload.
    if len(peers) < 7:
        return {"status": "INSUFFICIENT_BASELINE", "peer_days": [d.isoformat() for d in peers], "required_days": 7}
    zone = ZoneInfo(p["timezone"])
    # A repeated/skipped local hour is not a comparable same-clock window.
    if any(datetime.combine(d, datetime.min.time(), zone).utcoffset() !=
           datetime.combine(d + timedelta(days=1), datetime.min.time(), zone).utcoffset() for d in peers + [day]):
        return {"status": "INCOMPARABLE_LOCAL_DAY", "reason": "timezone offset changes within a selected day"}
    selected = set(peers + [day])
    counts = defaultdict(lambda: defaultdict(int))
    scope_threads = set(get(db, "scope_threads") or p["roots"])
    zone = ZoneInfo(p["timezone"])
    start = datetime.combine(min(selected), datetime.min.time(), zone).timestamp()
    windows = {}
    for d in selected:
        end = datetime.combine(d, local.time(), zone).timestamp()
        windows[(d, "day")] = (datetime.combine(d, datetime.min.time(), zone).timestamp(), end)
        windows[(d, "hour")] = (end - 3600, end)
    for row in db.execute("SELECT thread,stamp,model,input,cached,output FROM responses WHERE stamp>=? AND stamp<=?", (start - 3600, now.timestamp())):
        if row["thread"] not in scope_threads:
            continue
        values = (1, row["input"] - row["cached"], row["cached"], row["output"])
        for key, (left, right) in windows.items():
            if left <= row["stamp"] <= right:
                for metric, value in zip(METRICS, values):
                    counts[key][row["model"] + ":" + metric] += value
    # Zero activity cannot establish a measured full normal day. This also
    # catches absent/rotated history instead of silently making tiny baselines.
    covered = set()
    for row in db.execute("SELECT thread,stamp FROM responses WHERE stamp>=? AND stamp<?", (start, datetime.combine(day, datetime.min.time(), zone).timestamp())):
        if row["thread"] in scope_threads:
            covered.add(datetime.fromtimestamp(row["stamp"], zone).date())
    if not set(peers) <= covered:
        return {"status": "INCOMPLETE_BASELINE_COVERAGE", "missing_days": sorted(d.isoformat() for d in set(peers) - covered)}
    fingerprints = {r["date"]: r["usage_fingerprint"] for r in baseline_candidates(db, p, now)}
    changed = [r["date"] for r in p["reviewed_days"] if datetime.fromisoformat(r["date"]).date() in peers
               and fingerprints.get(r["date"]) != r["usage_fingerprint"]]
    if changed:
        return {"status": "BASELINE_CHANGED", "days": changed}
    comparisons, anomalies = [], []
    for window in ("day", "hour"):
        current = counts[(day, window)]
        for metric, actual in sorted(current.items()):
            samples = [counts[(d, window)].get(metric, 0) for d in peers]
            med = median(samples)
            mad = median(abs(v - med) for v in samples)
            p95 = sorted(samples)[math.ceil(.95 * len(samples)) - 1]
            # Conservative robust comparison, not a fixed daily allowance or
            # a probabilistic promise with this small historical sample.
            threshold = max(p95, med + 6 * 1.4826 * mad) * 1.5
            item = {"window": window, "metric": metric, "actual": actual, "median": med,
                    "mad": mad, "p95": p95, "threshold": threshold, "samples": len(samples)}
            comparisons.append(item)
            if actual > threshold:
                anomalies.append(item)
    return {"status": "ANOMALOUS" if anomalies else "NORMAL", "day": day.isoformat(),
            "timezone": p["timezone"], "day_class": day_class(day, p), "workload": p["workload"],
            "same_day_elapsed_seconds": seconds, "peer_days": [d.isoformat() for d in peers],
            "comparisons": comparisons, "anomalies": anomalies}


def evaluate(policy_path, *, now=None, release=None, history=False):
    now = now or datetime.now(timezone.utc)
    p = policy(policy_path)
    db = connect(p["state_db"])
    try:
        # Scan, compare, and latch are one serialized transaction. A second
        # process cannot clear/overwrite an earlier lock or count usage twice.
        db.execute("BEGIN IMMEDIATE")
        old_lock, old_policy = get(db, "lock"), get(db, "policy")
        previous_time = get(db, "last_checked")
        try:
            if previous_time and now.timestamp() < previous_time:
                raise ValueError("clock moved backwards")
            source = scan(db, p, now)
            assessment = compare(db, p, now)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            source = {"status": "UNAVAILABLE", "error": type(exc).__name__, "reason": str(exc)[:240]}
            assessment = {"status": "MEASUREMENT_UNAVAILABLE"}
        changed = old_policy is not None and old_policy != digest(p)
        reason = "POLICY_CHANGED" if changed else assessment["status"]
        if release:
            if (not old_lock or release.get("lock_id") != old_lock["id"] or not release.get("evidence")
                    or assessment["status"] != "NORMAL"):
                raise ValueError("release requires exact lock, human review evidence and a currently normal measurement")
            db.execute("INSERT INTO history(at,action,detail) VALUES (?,?,?)", (now.isoformat(), "MANUAL_RELEASE", encoded({"lock": old_lock, "evidence": release["evidence"]})))
            put(db, "lock", None)
            old_lock, changed, reason = None, False, "NORMAL"
        lock = old_lock
        if not lock and reason != "NORMAL":
            lock = {"id": digest({"at": now.isoformat(), "reason": reason, "policy": digest(p)}),
                    "at": now.isoformat(), "reason": reason, "assessment": assessment}
            put(db, "lock", lock)
            db.execute("INSERT INTO history(at,action,detail) VALUES (?,?,?)", (now.isoformat(), "LOCK", encoded(lock)))
        result = {"schema": SCHEMA, "allowed": lock is None, "lock": lock, "assessment": assessment,
                  "source": source, "measurement": "native responses and token categories; not account credits",
                  "scope": "configured local native roots and descendants; checked admission boundaries only",
                  "hard_account_spending_cap": False,
                  "inflight_limit": "Already running model responses and unguarded/cloud/other-host activity cannot be stopped here."}
        if history and source.get("status") != "UNAVAILABLE":
            result["baseline_candidates"] = baseline_candidates(db, p, now)
        put(db, "policy", digest(p))
        put(db, "last_checked", max(previous_time or 0, now.timestamp()))
        put(db, "latest", result)
        db.commit()
        return result
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def dispatch():
    try:
        request = json.load(sys.stdin)
        action = request.get("action", "check")
        if action not in ("check", "history", "release"):
            raise ValueError("action must be check, history or explicit human release")
        result = evaluate(request["policy"], release=request.get("release") if action == "release" else None, history=action == "history")
        print(encoded(result))
        return 0 if result["allowed"] else 2
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(encoded({"schema": SCHEMA, "allowed": False, "status": "GUARD_UNAVAILABLE",
                       "error": type(exc).__name__, "reason": str(exc)[:240], "hard_account_spending_cap": False}))
        return 2


if __name__ == "__main__":
    sys.exit(dispatch())
