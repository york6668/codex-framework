"""Persistent, node-local anomaly interlock for native Codex usage telemetry.

Tokens/responses are separate proxy measures, never credits or a billing cap.
No scheduler, model call, account request, or automatic unlock is performed.
"""
import hashlib
from contextlib import contextmanager
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
    return {"threads": len(selected), "records": db.execute("SELECT COUNT(*) FROM responses").fetchone()[0], "observed_at": now.isoformat()}


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
        db.execute("SAVEPOINT native_scan")
        try:
            if previous_time and now.timestamp() < previous_time:
                raise ValueError("clock moved backwards")
            source = scan(db, p, now)
            assessment = compare(db, p, now)
        except (OSError, ValueError, KeyError, TypeError, sqlite3.Error) as exc:
            # A decoder can emit records before discovering a damaged frame.
            # Keep prior verified measurements, but never commit that partial scan.
            db.execute("ROLLBACK TO native_scan")
            source = {"status": "UNAVAILABLE", "error": type(exc).__name__, "reason": str(exc)[:240]}
            assessment = {"status": "MEASUREMENT_UNAVAILABLE"}
        finally:
            db.execute("RELEASE native_scan")
        changed = old_policy is not None and old_policy != digest(p)
        scope = {k:p[k] for k in ("source_db","state_db","timezone","workload")}
        scope["roots"] = sorted(p["roots"])
        reasons = ([assessment["status"]] if assessment["status"] not in ADMISSIBLE else [])
        if changed:reasons.append("POLICY_CHANGED")
        lock = old_lock
        if not lock and reasons:
            reason = reasons[0]
            lock = {"id": digest({"at": now.isoformat(), "reason": reason, "policy": digest(p)}),
                    "at": now.isoformat(), "reason": reason, "assessment": assessment}
            put(db, "lock", lock)
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
                if lock["id"]==original_identity:
                    put(db,"locked_scope",scope);put(db,"locked_policy",digest(p))
            for reason in set(reasons+[lock["reason"]]):
                detail = assessment if reason in reasons and reason != "POLICY_CHANGED" else lock.get("assessment",{}) if reason==lock["reason"] else {"old_policy":old_policy,"new_policy":digest(p)}
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
        if release:
            valid = bool(lock and release.get("lock_id")==lock["id"] and release.get("evidence")
                         and assessment["status"] in ADMISSIBLE and get(db,"locked_scope")==scope
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
            db.execute("INSERT INTO history(at,action,detail) VALUES (?,?,?)", (now.isoformat(), "MANUAL_RELEASE",
                       encoded({"lock":lock,"findings":findings,"evidence":release["evidence"],
                                "old_policy":get(db,"locked_policy"),"new_policy":digest(p),"assessment_status":assessment["status"]})))
            lock, findings = None, {}
            put(db,"lock",None);put(db,"lock_findings",{});put(db,"locked_scope",None);put(db,"locked_policy",None)
        result = {"schema": SCHEMA, "allowed": lock is None, "lock": lock, "assessment": assessment,
                  "source": source, "measurement": "native responses and token categories; not account credits",
                  "scope": "configured local native roots and descendants; checked admission boundaries only",
                  "policy_sha256":digest(p), "lock_findings":findings,
                  "lock_scope_verified":bool(lock and get(db,"locked_scope")),
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
