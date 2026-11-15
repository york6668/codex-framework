"""Persistent, node-local anomaly interlock for native Codex usage telemetry.

Tokens/responses are separate proxy measures, never credits or a billing cap.
No scheduler, model call, account request, or automatic unlock is performed.
"""
import hashlib
from contextlib import closing, contextmanager
import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from statistics import median
from zoneinfo import ZoneInfo

SCHEMA = "native-usage-guard-v1"
METRICS = ("responses", "uncached_input", "cached_input", "output")
MONITORED = "MONITORED_LIMITED_BASELINE"
ADMISSIBLE = {"NORMAL", MONITORED}


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
    return closing(sqlite3.connect(uri, uri=True, timeout=5))


def policy(path):
    return validate_policy(json.loads(Path(path).read_text()))


def validate_policy(p):
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
    active, retired = (p.get(k, []) for k in ("automation_selectors", "retired_automation_selectors"))
    if not isinstance(active, list) or not isinstance(retired, list):
        raise ValueError("automation selectors must be lists")
    identities = set()
    for selector in active + retired:
        if not isinstance(selector, dict) or set(selector) != {"automation_id", "codex_automations_db"}:
            raise ValueError("automation selector requires only automation_id and codex_automations_db")
        aid, path = selector["automation_id"], selector["codex_automations_db"]
        if not isinstance(aid, str) or not aid.strip() or aid != aid.strip():
            raise ValueError("explicit native automation identity required")
        if not isinstance(path, str) or not Path(path).is_absolute():
            raise ValueError("explicit absolute native automation database required")
        if aid in identities:
            raise ValueError("duplicate native automation selector")
        identities.add(aid)
        if Path(path).resolve() in {Path(p["source_db"]).resolve(), Path(p["state_db"]).resolve()}:
            raise ValueError("native automation database must be distinct from telemetry and meter state")
    ZoneInfo(p["timezone"])
    if not isinstance(p["workload"], str) or not p["workload"]:
        raise ValueError("fixed comparable workload required")
    if not isinstance(p["reviewed_days"], list):
        raise ValueError("reviewed_days must be a list")
    if p.get("day_mode", "workday") not in ("workday", "continuous"):
        raise ValueError("day_mode must describe workday or continuous scheduled work")
    if not isinstance(p.get("change_reference_days", []), list):
        raise ValueError("change_reference_days must be a list")
    days = set()
    for row in p["reviewed_days"] + p.get("change_reference_days", []):
        day = datetime.strptime(row["date"], "%Y-%m-%d").date()
        if day in days or not row.get("evidence") or row.get("workload") != p["workload"]:
            raise ValueError("distinct reviewed normal days with workload and evidence required")
        if not re.fullmatch(r"[0-9a-f]{64}", row.get("usage_fingerprint", "")):
            raise ValueError("reviewed day must bind its exact observed usage fingerprint")
        days.add(day)
    for row in p.get("change_reference_days", []):
        if row.get("quality") != "OBSERVED_NOT_NORMAL":
            raise ValueError("change references must disclose that observations are not normal-day evidence")
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


def rollout_source(value):
    """Resolve the native logical path, preferring plain JSONL as Codex does."""
    plain = Path(value)
    if plain.name.endswith(".jsonl.zst"):
        plain = plain.with_suffix("")
    try:
        return plain.resolve(strict=True), False
    except FileNotFoundError:
        if not plain.name.endswith(".jsonl"):
            raise
        return Path(str(plain) + ".zst").resolve(strict=True), True


@contextmanager
def rollout_stream(value):
    # Native compression/materialization can replace the representation between
    # resolution and open. Retry only this same logical source, never a scan or
    # failed decode; a persistent missing source still fails closed.
    for attempt in range(4):
        try:
            path, compressed = rollout_source(value)
            source = path.open("rb")
            break
        except FileNotFoundError:
            if attempt == 3:
                raise
            time.sleep(0.05)
    with source:
        stat = os.fstat(source.fileno())
        if not compressed:
            yield path, compressed, stat, source
            return
        # Keep the original file open across representation changes. Decode
        # only in memory; never materialize or alter the native session file.
        process = subprocess.Popen(["zstd", "-q", "-d", "-c"], stdin=source,
                                   stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
        try:
            yield path, compressed, stat, process.stdout
            if process.wait() != 0:
                raise ValueError("native compressed rollout decoding failed")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait()
            process.stdout.close()


def selector_key(selector):
    return digest({"automation_id": selector["automation_id"],
                   "codex_automations_db": str(Path(selector["codex_automations_db"]).resolve())})


def protection_scope(p):
    scope = {k: p[k] for k in ("source_db", "state_db", "timezone", "workload")}
    scope["roots"] = sorted(p["roots"])
    for key in ("automation_selectors", "retired_automation_selectors"):
        if p.get(key):
            scope[key] = sorted(p[key], key=lambda row: row["automation_id"])
    return scope


def automation_scope(p, now, prior_scopes):
    """Read only native identity metadata, including failed and unfinished runs."""
    scopes, owners = [], {}
    selectors = [(s, False) for s in p.get("automation_selectors", [])]
    selectors += [(s, True) for s in p.get("retired_automation_selectors", [])]
    for selector, retired in selectors:
        aid = selector["automation_id"]
        saved = prior_scopes.get(selector_key(selector))
        if retired and saved is None:
            raise ValueError("retirement requires previously verified native coverage: " + aid)
        roots = set()
        with source_connection(selector["codex_automations_db"]) as source:
            source.execute("BEGIN")  # One registry snapshot, including a live WAL.
            for table, required in (("automations", {"id"}),
                                    ("automation_runs", {"thread_id", "automation_id", "status", "created_at", "updated_at"})):
                kind = source.execute("SELECT type FROM sqlite_master WHERE name=?", (table,)).fetchone()
                columns = {row[1] for row in source.execute("PRAGMA table_info(" + table + ")")}
                if not kind or kind[0] != "table" or not required <= columns:
                    raise ValueError("native automation schema unavailable: " + table)
            definitions = len(source.execute("SELECT id FROM automations WHERE id=?", (aid,)).fetchall())
            if retired and definitions:
                raise ValueError("retired native automation definition still exists or reappeared: " + aid)
            if not retired and definitions != 1:
                raise ValueError("native automation identity missing or duplicate: " + aid)
            rows = source.execute("SELECT thread_id,status,created_at,updated_at FROM automation_runs WHERE automation_id=?", (aid,))
            for tid, status, created, updated in rows:
                if (not isinstance(tid, str) or not tid.strip() or tid != tid.strip()
                        or not isinstance(status, str) or not status.strip()
                        or type(created) is not int or type(updated) is not int
                        or created < 0 or updated < created or updated > (now.timestamp() + 60) * 1000):
                    raise ValueError("invalid native automation run metadata: " + aid)
                if tid in roots or tid in owners:
                    raise ValueError("duplicate native automation run identity")
                # Even an incompatible DB without its native uniqueness constraint
                # must not assign this root to a second automation.
                if source.execute("SELECT COUNT(*) FROM automation_runs WHERE thread_id=?", (tid,)).fetchone()[0] != 1:
                    raise ValueError("ambiguous native automation run identity")
                roots.add(tid)
                owners[tid] = aid
            if retired:
                if not roots <= set(saved):
                    raise ValueError("unreviewed run registered for retired automation: " + aid)
                roots = set(saved)
                for tid in roots:
                    if tid in owners and owners[tid] != aid:
                        raise ValueError("retired root reassigned to another automation")
                    registered = source.execute("SELECT automation_id FROM automation_runs WHERE thread_id=?", (tid,)).fetchall()
                    if len(registered) > 1 or any(row[0] != aid for row in registered):
                        raise ValueError("retired root has conflicting native ownership")
                    owners[tid] = aid
        scopes.append({**selector, "codex_automations_db": str(Path(selector["codex_automations_db"]).resolve()),
                       "root_thread_ids": sorted(roots), "run_count": len(roots),
                       "coverage": "RETIRED_VERIFIED_ROOTS" if retired else "REGISTERED_ROOTS" if roots else "AWAITING_NATIVE_RUN"})
    return scopes


def native_scope(p, now, prior_scopes):
    """Resolve explicit roots and native registry roots without reading messages."""
    automation_scopes = automation_scope(p, now, prior_scopes)
    with source_connection(p["source_db"]) as source:
        source.execute("BEGIN")
        source.row_factory = sqlite3.Row
        threads = {}
        for row in source.execute("SELECT id,rollout_path,source FROM threads"):
            if row["id"] in threads:
                raise ValueError("duplicate native thread identity")
            threads[row["id"]] = dict(row)
        roots = set(p["roots"]) | {tid for scope in automation_scopes for tid in scope["root_thread_ids"]}
        if not roots <= threads.keys():
            raise ValueError("a configured native root is missing")
        parents = {}
        def parent(child, ancestor):
            if not isinstance(ancestor, str) or not ancestor or child == ancestor:
                raise ValueError("invalid native parent identity")
            if child in parents and parents[child] != ancestor:
                raise ValueError("conflicting native parent identities")
            parents[child] = ancestor
        if source.execute("SELECT 1 FROM sqlite_master WHERE name='thread_spawn_edges'").fetchone():
            edge_children = set()
            for ancestor, child in source.execute("SELECT parent_thread_id,child_thread_id FROM thread_spawn_edges"):
                if child in edge_children:
                    raise ValueError("duplicate native parent edge")
                edge_children.add(child)
                parent(child, ancestor)
        for tid, row in threads.items():
            try:
                ancestor = json.loads(row["source"])["subagent"]["thread_spawn"]["parent_thread_id"]
            except (KeyError, ValueError, TypeError):
                continue
            parent(tid, ancestor)
    if any(tid in parents for scope in automation_scopes for tid in scope["root_thread_ids"]):
        raise ValueError("native automation run is not a root thread")
    selected = set(roots)
    while True:
        children = {tid for tid, parent in parents.items() if parent in selected}
        if children <= selected:
            break
        selected |= children
    if not selected <= threads.keys():
        raise ValueError("native descendant metadata missing")
    return threads, selected, sorted(roots), automation_scopes


def scan(db, p, now):
    """Incremental bounded-memory reads; one globally unique response per event."""
    prior_scopes = get(db, "automation_scopes") or {}
    threads, selected, roots, automation_scopes = native_scope(p, now, prior_scopes)
    if not set(get(db, "scope_threads") or []) <= selected:
        raise ValueError("previously measured native threads are missing from protection scope")
    for scope in automation_scopes:
        key = selector_key(scope)
        if not set(prior_scopes.get(key, [])) <= set(scope["root_thread_ids"]):
            raise ValueError("previously measured native automation roots are missing")
        prior_scopes[key] = scope["root_thread_ids"]
    for tid in sorted(selected):
        with rollout_stream(threads[tid]["rollout_path"]) as (path, compressed, stat, stream):
            old = db.execute("SELECT * FROM cursors WHERE path=?", (str(path),)).fetchone()
            same = not compressed and old and (old["device"], old["inode"]) == (stat.st_dev, stat.st_ino) and old["offset"] <= stat.st_size
            offset, model = (old["offset"], old["model"]) if same else (0, "UNKNOWN")
            if not compressed:
                stream.seek(offset)
            while True:
                line = stream.readline(16 * 1024 * 1024 + 1)
                if not line:
                    break
                if len(line) > 16 * 1024 * 1024:
                    raise ValueError("native record exceeds bounded reader")
                if not line.endswith(b"\n"):
                    if compressed:
                        raise ValueError("incomplete compressed native record")
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
                if not compressed:
                    offset = stream.tell()
        if compressed:
            # Compression is a cold-file representation, not a new source.
            # Replay fully to verify its frames; response identities dedupe it.
            # A later native materialization must not reuse a stale plain offset.
            db.execute("DELETE FROM cursors WHERE path=?", (str(path.with_suffix("")),))
        else:
            db.execute("INSERT OR REPLACE INTO cursors VALUES (?,?,?,?,?)", (str(path), stat.st_dev, stat.st_ino, offset, model))
    put(db, "scope_threads", sorted(selected))
    if automation_scopes:
        put(db, "automation_scopes", prior_scopes)
    return {"threads": len(selected), "records": db.execute("SELECT COUNT(*) FROM responses").fetchone()[0],
            "root_thread_ids": roots, "automation_scopes": automation_scopes, "observed_at": now.isoformat()}


def day_class(day, p):
    default = "continuous" if p.get("day_mode") == "continuous" else "weekend" if day.weekday() >= 5 else "weekday"
    return p.get("day_classes", {}).get(day.isoformat(), default)


def baseline_candidates(db, p, now, *, leading_minutes=60):
    """Bounded metadata for human review; never declares a day normal."""
    zone = ZoneInfo(p["timezone"])
    today = now.astimezone(zone).date()
    start = datetime.combine(today - timedelta(days=60), datetime.min.time(), zone).timestamp() - leading_minutes * 60
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
        leading = [r for r in days.get((d - timedelta(days=1)).isoformat(), []) if r[2] >= midnight - leading_minutes * 60]
        result.append({"date": day, "day_class": day_class(d, p), "responses": len(rows),
                       "usage_fingerprint": digest(sorted(rows + leading)), "review_status": "UNREVIEWED",
                       "leading_minutes": leading_minutes,
                       "coverage": "Observed day plus preceding minutes only; review complete comparable work and source coverage."})
    return result


def compare_change(db, p, now, normal_peers):
    """A frozen observed envelope detects sustained growth, not normality.

    Two reviewed observation days and two completed nonoverlapping hours
    confirm a doubling. Sparse known profiles use a conservative full-day or
    hourly-peak envelope, not a zero allowance or an invented normal sample.
    """
    zone = ZoneInfo(p["timezone"])
    local = now.astimezone(zone)
    day = local.date()
    refs = [r for r in p.get("change_reference_days", [])
            if day - timedelta(days=60) <= datetime.fromisoformat(r["date"]).date() < day
            and day_class(datetime.fromisoformat(r["date"]).date(), p) == day_class(day, p)]
    chosen = {r["date"] for r in refs}
    # Even one reviewed day must be intact; a low sample count must not hide
    # a missing or later changed reference.
    for rows, minutes, selected in ((p["reviewed_days"], 60, {d.isoformat() for d in normal_peers}),
                                     (refs, 120, chosen)):
        if not selected:continue
        current = {r["date"]:r["usage_fingerprint"] for r in baseline_candidates(db, p, now, leading_minutes=minutes)}
        missing = sorted(selected - current.keys())
        if missing:
            return {"status":"INCOMPLETE_BASELINE_COVERAGE", "missing_days":missing}
        changed = [r["date"] for r in rows if r["date"] in selected and current[r["date"]] != r["usage_fingerprint"]]
        if changed:
            return {"status":"BASELINE_CHANGED", "days":changed}
    result = {"status":MONITORED, "day":day.isoformat(), "timezone":p["timezone"],
              "day_class":day_class(day,p), "workload":p["workload"],
              "normal_reference_days":len(normal_peers), "peer_days":[d.isoformat() for d in normal_peers],
              "change_reference_days":sorted(chosen), "reference_quality":"OBSERVED_NOT_NORMAL",
              "comparisons":[], "warnings":[], "anomalies":[], "unassessed":[],
              "limitation":"Normal daily use is unconfirmed; only covered observed growth is assessed. Not account credits."}
    if not refs:
        result["unassessed"].append("No frozen comparable change reference; relative anomaly detection is unavailable.")
        return result
    days = [datetime.fromisoformat(r["date"]).date() for r in refs]
    # Same-clock comparison is undefined across a repeated/skipped local hour.
    if any(datetime.combine(d,datetime.min.time(),zone).utcoffset() !=
           datetime.combine(d+timedelta(days=1),datetime.min.time(),zone).utcoffset() for d in days+[day]):
        result["unassessed"].append("Local timezone offset changes: same-clock growth not compared.")
        return result
    end = local.replace(minute=0,second=0,microsecond=0)
    windows = {"day":(local.replace(hour=0,minute=0,second=0,microsecond=0),local),
               "hour_previous":(end-timedelta(hours=2),end-timedelta(hours=1)),
               "hour_latest":(end-timedelta(hours=1),end)}
    spans = {}
    for anchor in days+[day]:
        shift = anchor-day
        for name,(left,right) in windows.items():
            spans[(anchor,name)] = ((left+shift).timestamp(),(right+shift).timestamp())
    counts = defaultdict(lambda:defaultdict(int))
    reference_totals = defaultdict(lambda:defaultdict(int))
    reference_hours = defaultdict(lambda:defaultdict(int))
    reference_dates = set(days)
    scope = set(get(db,"scope_threads") or p["roots"])
    for row in db.execute("SELECT thread,stamp,model,input,cached,output FROM responses WHERE stamp>=? AND stamp<?",
                          (min(v[0] for v in spans.values()),now.timestamp())):
        if row["thread"] not in scope:
            continue
        values = (1,row["input"]-row["cached"],row["cached"],row["output"])
        stamp = datetime.fromtimestamp(row["stamp"],zone)
        if stamp.date() in reference_dates:
            for metric,value in zip(METRICS,values):
                key = row["model"]+":"+metric
                reference_totals[stamp.date()][key] += value
                reference_hours[(stamp.date(),stamp.hour)][key] += value
        for key,(left,right) in spans.items():
            if left <= row["stamp"] < right:
                for metric,value in zip(METRICS,values):
                    counts[key][row["model"]+":"+metric] += value
    exceeded = defaultdict(dict)
    for window in windows:
        actual = counts[(day,window)]
        metrics = set(actual).union(*(counts[(d,window)] for d in days), *reference_totals.values())
        for metric in sorted(metrics):
            samples = [counts[(d,window)].get(metric,0) for d in days]
            clock_samples = samples
            basis = "same_clock"
            if len(samples)>=2 and any(v<=0 for v in samples):
                # Reviewed complete scoped days can contain an idle profile.
                # Its known activity at other hours is a conservative bound;
                # a new model/category with no positive history stays unknown.
                if window=="day":
                    samples = [reference_totals[d].get(metric,0) for d in days]
                    basis = "observed_full_day_envelope"
                else:
                    # Cross-midnight same-clock windows may lie in the
                    # fingerprinted leading period. Never discard that higher
                    # known observation when using the whole-day peak.
                    samples = [max(clock, max(reference_hours[(d,h)].get(metric,0) for h in range(24)))
                               for d,clock in zip(days,clock_samples)]
                    basis = "observed_hourly_peak_envelope"
            if len(samples)<2 or max(samples)<=0:
                if actual.get(metric,0):result["unassessed"].append(window+":"+metric)
                continue
            item = {"window":window, "metric":metric, "actual":actual.get(metric,0),
                    "samples":samples, "threshold":2*max(samples), "reference_quality":"OBSERVED_NOT_NORMAL",
                    "comparison_basis":basis, "same_clock_samples":clock_samples,
                    "positive_reference_days":sum(v>0 for v in samples),
                    "start":windows[window][0].isoformat(), "end":windows[window][1].isoformat()}
            result["comparisons"].append(item)
            if item["actual"]>item["threshold"]:
                result["warnings"].append(item)
                exceeded[metric][window] = item
    for metric,signals in exceeded.items():
        if {"hour_previous","hour_latest"} <= signals.keys():
            result["anomalies"].append({"rule":"SUSTAINED_OBSERVED_GROWTH", "metric":metric,
                "evidence":[signals["hour_previous"],signals["hour_latest"]],
                "meaning":"Two successive complete hours exceed twice their applicable frozen observed bounds; sparse profiles use hourly peaks. Cause and billing impact unknown."})
        response_metric = metric.rsplit(":",1)[0]+":responses"
        if metric!=response_metric and "day" in signals and "day" in exceeded.get(response_metric,{}):
            for key in (response_metric,metric):
                result["anomalies"].append({"rule":"DAILY_RESPONSE_AND_TOKEN_GROWTH", "metric":key,
                    "evidence":[exceeded[response_metric]["day"],signals["day"]],
                    "meaning":"Daily response count and a token category both exceed twice their observed bounds; sparse profiles use complete reference-day totals. Not a fixed daily cap."})
    if result["anomalies"]:
        result["status"] = "ANOMALOUS"
    return result


def compare(db, p, now):
    local = now.astimezone(ZoneInfo(p["timezone"]))
    day, seconds = local.date(), local.hour * 3600 + local.minute * 60 + local.second
    peers = [datetime.strptime(r["date"], "%Y-%m-%d").date() for r in p["reviewed_days"]]
    peers = [d for d in peers if day - timedelta(days=60) <= d < day and day_class(d, p) == day_class(day, p)]
    # Missing normal history is uncertainty, not evidence of abnormal use.
    # Integrity is still checked before returning a limited assessment.
    change = compare_change(db, p, now, peers)
    if change["status"] not in ADMISSIBLE or len(peers) < 7:
        return change
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
    covered, known_models = set(), set()
    for row in db.execute("SELECT thread,stamp,model FROM responses WHERE stamp>=? AND stamp<?", (start, datetime.combine(day, datetime.min.time(), zone).timestamp())):
        if row["thread"] in scope_threads:
            recorded_day = datetime.fromtimestamp(row["stamp"], zone).date()
            covered.add(recorded_day)
            if recorded_day in peers:known_models.add(row["model"])
    if not set(peers) <= covered:
        return {"status": "INCOMPLETE_BASELINE_COVERAGE", "missing_days": sorted(d.isoformat() for d in set(peers) - covered)}
    fingerprints = {r["date"]: r["usage_fingerprint"] for r in baseline_candidates(db, p, now)}
    changed = [r["date"] for r in p["reviewed_days"] if datetime.fromisoformat(r["date"]).date() in peers
               and fingerprints.get(r["date"]) != r["usage_fingerprint"]]
    if changed:
        return {"status": "BASELINE_CHANGED", "days": changed}
    comparisons, anomalies, unassessed = [], [], []
    for window in ("day", "hour"):
        current = counts[(day, window)]
        metrics = set(current) | {model+":"+metric for model in known_models for metric in METRICS}
        for metric in sorted(metrics):
            actual = current.get(metric,0)
            samples = [counts[(d, window)].get(metric, 0) for d in peers]
            if actual and metric.rsplit(":",1)[0] not in known_models:
                unassessed.append(window+":"+metric)
                continue
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
    return {"status": "ANOMALOUS" if anomalies else MONITORED if unassessed else "NORMAL", "day": day.isoformat(),
            "timezone": p["timezone"], "day_class": day_class(day, p), "workload": p["workload"],
            "same_day_elapsed_seconds": seconds, "peer_days": [d.isoformat() for d in peers],
            "comparisons": comparisons, "anomalies": anomalies, "unassessed":unassessed}


def migrate_scope(db, p, lock, source, request, now):
    """Reviewed selector lifecycle changes preserve coverage, never unlock."""
    if not isinstance(request, dict) or not lock or request.get("lock_id") != lock["id"]:
        raise ValueError("scope migration requires the exact existing lock")
    before = validate_policy(request.get("from_policy"))
    original_scope = get(db, "locked_scope")
    if (digest(before) != get(db, "locked_policy")
            or (original_scope is not None and protection_scope(before) != original_scope)
            or request.get("to_policy_sha256") != digest(p)
            or not isinstance(request.get("evidence"), str) or not request["evidence"].strip()):
        raise ValueError("scope migration requires verified old policy, current fingerprint and review evidence")
    lifecycle = {"automation_selectors", "retired_automation_selectors"}
    if {k: v for k, v in before.items() if k not in lifecycle} != {k: v for k, v in p.items() if k not in lifecycle}:
        raise ValueError("scope migration must preserve source, meter, roots, workload and comparison policy")
    old_active = {selector_key(s) for s in before.get("automation_selectors", [])}
    old_retired = {selector_key(s) for s in before.get("retired_automation_selectors", [])}
    active = {selector_key(s) for s in p.get("automation_selectors", [])}
    retired = {selector_key(s) for s in p.get("retired_automation_selectors", [])}
    if not (old_retired <= retired and old_active | old_retired <= active | retired
            and (old_active != active or old_retired != retired)):
        raise ValueError("migration may add selectors or retire them, but cannot remove or reactivate coverage")
    if source.get("status") == "UNAVAILABLE":
        raise ValueError("scope migration requires complete current measurement")
    # A pre-upgrade meter may have retained a policy hash but no scope. Only
    # the supplied policy matching that saved hash reconstructs the old scope;
    # the new policy (or a second check of it) cannot certify its own migration.
    if original_scope is None:
        put(db, "locked_scope", protection_scope(before))
    receipt = {"lock_id": lock["id"], "from_policy_sha256": digest(before), "to_policy_sha256": digest(p),
               "from_scope": protection_scope(before), "to_scope": protection_scope(p),
               "retired_coverage": [s for s in source["automation_scopes"] if s["coverage"] == "RETIRED_VERIFIED_ROOTS"],
               "evidence": request["evidence"], "at": now.isoformat()}
    prior = get(db, "scope_migration")
    # A duplicate request is harmless and must not multiply migration history.
    if not prior or any(prior.get(k) != receipt[k] for k in receipt if k != "at"):
        db.execute("INSERT INTO history(at,action,detail) VALUES (?,?,?)", (now.isoformat(), "SCOPE_MIGRATION", encoded(receipt)))
        put(db, "scope_migration", receipt)


def additive_scope(before, after):
    """Preserve the existing explicit-release interface for added selectors."""
    if not isinstance(before, dict):
        return False
    if {k: v for k, v in before.items() if k != "automation_selectors"} != {k: v for k, v in after.items() if k != "automation_selectors"}:
        return False
    return ({selector_key(s) for s in before.get("automation_selectors", [])}
            < {selector_key(s) for s in after.get("automation_selectors", [])})


def release_scope_verified(db, p, lock):
    scope = protection_scope(p)
    if get(db, "locked_scope") == scope:
        return True
    if additive_scope(get(db, "locked_scope"), scope):
        return True
    migration = get(db, "scope_migration") or {}
    return bool(lock and migration.get("lock_id") == lock["id"]
                and migration.get("from_policy_sha256") == get(db, "locked_policy")
                and migration.get("from_scope") == get(db, "locked_scope")
                and migration.get("to_policy_sha256") == digest(p) and migration.get("to_scope") == scope)


def evaluate(policy_path, *, now=None, release=None, migration=None, history=False):
    now = now or datetime.now(timezone.utc)
    p = policy(policy_path)
    db = connect(p["state_db"])
    try:
        # Scan, compare, and latch are one serialized transaction. A second
        # process cannot clear/overwrite an earlier lock or count usage twice.
        db.execute("BEGIN IMMEDIATE")
        old_lock, old_policy = get(db, "lock"), get(db, "policy")
        measured_scope, measured_policy = get(db, "measured_scope"), get(db, "measured_policy")
        scope = protection_scope(p)
        previous_time = get(db, "last_checked")
        db.execute("SAVEPOINT native_scan")
        try:
            if previous_time and now.timestamp() < previous_time:
                raise ValueError("clock moved backwards")
            source = scan(db, p, now)
            assessment = compare(db, p, now)
            put(db, "measured_scope", scope)
            put(db, "measured_policy", digest(p))
            pending = [scope["automation_id"] for scope in source["automation_scopes"]
                       if scope["coverage"] == "AWAITING_NATIVE_RUN"]
            if pending and assessment["status"] in ADMISSIBLE:
                assessment["status"] = MONITORED
                assessment.setdefault("unassessed", []).extend("automation:" + aid + ":AWAITING_NATIVE_RUN" for aid in pending)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            # A decoder can emit records before discovering a damaged frame.
            # Keep prior verified measurements, but never commit that partial scan.
            db.execute("ROLLBACK TO native_scan")
            source = {"status": "UNAVAILABLE", "error": type(exc).__name__, "reason": str(exc)[:240]}
            assessment = {"status": "MEASUREMENT_UNAVAILABLE"}
        finally:
            db.execute("RELEASE native_scan")
        changed = old_policy is not None and old_policy != digest(p)
        reasons = ([assessment["status"]] if assessment["status"] not in ADMISSIBLE else [])
        if changed:reasons.append("POLICY_CHANGED")
        lock = old_lock
        if not lock and reasons:
            reason = reasons[0]
            lock = {"id": digest({"at": now.isoformat(), "reason": reason, "policy": digest(p)}),
                    "at": now.isoformat(), "reason": reason, "assessment": assessment,
                    "scope_binding_version": 1}
            put(db, "lock", lock)
            put(db, "scope_migration", None)
            put(db, "locked_scope", measured_scope if changed else scope)
            put(db, "locked_policy", (measured_policy or old_policy) if changed else digest(p))
            db.execute("INSERT INTO history(at,action,detail) VALUES (?,?,?)", (now.isoformat(), "LOCK", encoded(lock)))
        findings = get(db,"lock_findings") or {}
        if lock:
            # Retain subsequent faults under an old baseline-only latch. They
            # must be reviewed even if their source later becomes readable.
            if not get(db,"locked_scope"):
                # A legacy latch embeds its original policy in its identity.
                # The last-checked policy hash alone is not that binding: it
                # may already reflect a changed scope while the old lock held.
                original_identity = digest({"at":lock.get("at"),"reason":lock["reason"],"policy":digest(p)})
                if "scope_binding_version" not in lock and lock["id"]==original_identity:
                    put(db,"locked_scope",scope);put(db,"locked_policy",digest(p))
            for reason in set(reasons+[lock["reason"]]):
                detail = ({"old_policy":old_policy,"new_policy":digest(p)} if reason == "POLICY_CHANGED"
                          else assessment if reason in reasons else lock.get("assessment",{}))
                if reason not in findings:
                    findings[reason] = {"first_at":now.isoformat(), "detail":detail}
                    db.execute("INSERT INTO history(at,action,detail) VALUES (?,?,?)",
                               (now.isoformat(),"LOCK_FINDING",encoded({"reason":reason,"detail":detail})))
                elif reason=="ANOMALOUS" and assessment["status"]=="ANOMALOUS":
                    saved = findings[reason]["detail"]
                    metrics = {r["metric"]:r for r in saved.get("anomalies",[])}
                    added = {r["metric"]:r for r in assessment.get("anomalies",[]) if r["metric"] not in metrics}
                    if added:
                        metrics.update(added);saved["anomalies"] = list(metrics.values())
                        db.execute("INSERT INTO history(at,action,detail) VALUES (?,?,?)",
                                   (now.isoformat(),"LOCK_FINDING",encoded({"reason":reason,"additional_metrics":added})))
            put(db,"lock_findings",findings)
        put(db,"policy",digest(p))
        put(db,"last_checked",max(previous_time or 0,now.timestamp()))
        if migration is not None:
            try:
                migrate_scope(db, p, lock, source, migration, now)
            except (ValueError, KeyError, TypeError) as exc:
                put(db, "latest", {"allowed": False, "lock": lock, "assessment": assessment, "migration_rejected": True})
                db.commit()  # Preserve newly observed faults even when review inputs are invalid.
                raise ValueError("scope migration rejected: " + str(exc)) from exc
        if release is not None:
            valid = bool(lock and release.get("lock_id")==lock["id"] and release.get("evidence")
                         and assessment["status"] in ADMISSIBLE and release_scope_verified(db, p, lock)
                         and release.get("policy_sha256")==digest(p) and set(release.get("reviewed_reasons",[]))==set(findings))
            # An anomaly cannot vanish merely by removing its comparison,
            # including when unrelated remaining normal references look valid.
            metrics = {r["metric"] for r in findings.get("ANOMALOUS",{}).get("detail",{}).get("anomalies",[])}
            compared = {r["metric"] for r in assessment.get("comparisons",[])}
            valid = valid and metrics <= compared
            if not valid:
                put(db,"latest",{"allowed":False,"lock":lock,"assessment":assessment,"release_rejected":True})
                db.commit()  # A rejected release must not erase a newly observed fault.
                raise ValueError("release requires exact lock, verified original scope, review evidence, no current fault, current policy fingerprint and all recorded reasons")
            migration_receipt = get(db, "scope_migration")
            if additive_scope(get(db, "locked_scope"), scope):
                migration_receipt = {"kind": "ADDITIVE_COVERAGE", "from_scope": get(db, "locked_scope"),
                                     "to_scope": scope, "from_policy_sha256": get(db, "locked_policy"),
                                     "to_policy_sha256": digest(p), "evidence": release["evidence"]}
            db.execute("INSERT INTO history(at,action,detail) VALUES (?,?,?)", (now.isoformat(), "MANUAL_RELEASE",
                       encoded({"lock":lock,"findings":findings,"evidence":release["evidence"],
                                "old_policy":get(db,"locked_policy"),"new_policy":digest(p),"assessment_status":assessment["status"],
                                "scope_migration":migration_receipt})))
            lock, findings = None, {}
            put(db,"lock",None);put(db,"lock_findings",{});put(db,"locked_scope",None);put(db,"locked_policy",None)
            put(db,"scope_migration",None)
        result = {"schema": SCHEMA, "allowed": lock is None, "lock": lock, "assessment": assessment,
                  "source": source, "measurement": "native responses and token categories; not account credits",
                  "scope": "configured local native roots, selected native automation runs and descendants; checked admission boundaries only",
                  "policy_sha256":digest(p), "lock_findings":findings,
                  "lock_scope_verified":bool(lock and get(db,"locked_scope")),
                  "scope_migration":get(db,"scope_migration"),
                  "hard_account_spending_cap": False,
                  "inflight_limit": "Already running model responses and unguarded/cloud/other-host activity cannot be stopped here."}
        if history and source.get("status") != "UNAVAILABLE":
            result["baseline_candidates"] = baseline_candidates(db, p, now)
            result["change_reference_candidates"] = baseline_candidates(db, p, now, leading_minutes=120)
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
        if action not in ("check", "history", "migrate", "release"):
            raise ValueError("action must be check, history, reviewed scope migration or explicit release")
        if action in ("migrate", "release") and not isinstance(request.get("migration" if action == "migrate" else "release"), dict):
            raise ValueError("explicit review object required")
        result = evaluate(request["policy"], release=request.get("release") if action == "release" else None,
                          migration=request.get("migration") if action == "migrate" else None, history=action == "history")
        print(encoded(result))
        return 0 if result["allowed"] else 2
    except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
        print(encoded({"schema": SCHEMA, "allowed": False, "status": "GUARD_UNAVAILABLE",
                       "error": type(exc).__name__, "reason": str(exc)[:240], "hard_account_spending_cap": False}))
        return 2


if __name__ == "__main__":
    sys.exit(dispatch())
