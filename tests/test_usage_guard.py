import concurrent.futures
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import usage_guard as guard


class UsageGuardTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "native.sqlite"
        self.log = self.root / "root.jsonl"
        self.log.write_text("")
        with sqlite3.connect(self.source) as db:
            db.execute("CREATE TABLE threads(id TEXT, rollout_path TEXT, source TEXT)")
            db.execute("CREATE TABLE thread_spawn_edges(parent_thread_id TEXT, child_thread_id TEXT)")
            db.execute("INSERT INTO threads VALUES (?,?,?)", ("root", str(self.log), "vscode"))
        self.now = datetime(2026, 11, 2, 10, tzinfo=timezone.utc)
        self.days = ["2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23", "2026-10-26", "2026-10-27"]
        self.config = {"schema": guard.SCHEMA, "source_db": str(self.source),
                       "state_db": str(self.root / "meter.sqlite"), "timezone": "Etc/UTC",
                       "roots": ["root"], "workload": "routine", "reviewed_days": [
                           {"date": d, "workload": "routine", "evidence": "reviewed fixture " + d} for d in self.days]}
        self.policy = self.root / "policy.json"
        for day in self.days:
            for i in range(3):
                self.event(day + "T09:" + str(5 + 20*i).zfill(2) + ":00+00:00", day + "-" + str(i))
        self.seal_baseline()

    def seal_baseline(self):
        # Simulate explicit review of these generated normal-day records.
        with guard.connect(self.config['state_db']) as db:
            guard.scan(db, self.config, self.now)
            fingerprints = {r['date']: r['usage_fingerprint'] for r in guard.baseline_candidates(db, self.config, self.now)}
        for row in self.config['reviewed_days']:
            row['usage_fingerprint'] = fingerprints[row['date']]
        self.save_policy()

    def save_policy(self):
        self.policy.write_text(json.dumps(self.config))

    def event(self, stamp, rid, *, path=None, thread="root", model="model-a", cached=80, output=10, partial=False):
        path = path or self.log
        event = {"timestamp": stamp, "type": "token_usage_record", "payload": {
            "thread_id": thread, "response_id": rid,
            "usage": {"input_tokens": 100, "cached_input_tokens": cached, "output_tokens": output}}}
        with path.open("a") as f:
            f.write(json.dumps({"timestamp": stamp, "type": "turn_context", "payload": {"model": model, "effort": "high"}}) + "\n")
            f.write(json.dumps(event) + ("" if partial else "\n"))

    def check(self, **kwargs):
        return guard.evaluate(self.policy, now=kwargs.pop("now", self.now), **kwargs)

    def observed_reference(self):
        self.config['reviewed_days'] = []
        for day in self.days[:2]:
            for i in range(3):self.event(day+'T08:30:00+00:00',day+'-early-'+str(i))
        with guard.connect(self.config['state_db']) as db:
            guard.scan(db,self.config,self.now)
            hashes = {r['date']:r['usage_fingerprint'] for r in guard.baseline_candidates(db,self.config,self.now,leading_minutes=120)}
        self.config['change_reference_days'] = [
            {'date':day,'workload':'routine','evidence':'reviewed complete observed fixture; not normal',
             'quality':'OBSERVED_NOT_NORMAL','usage_fingerprint':hashes[day]} for day in self.days[:2]]
        self.save_policy()

    def test_no_history_is_monitored_not_normal_and_source_failure_still_latches(self):
        self.config['reviewed_days']=[];self.save_policy()
        got=self.check()
        self.assertTrue(got['allowed']);self.assertEqual(got['assessment']['status'],guard.MONITORED)
        self.assertTrue(got['assessment']['unassessed'])
        self.source.rename(self.root/'missing.sqlite')
        self.assertEqual(self.check()['lock']['reason'],'MEASUREMENT_UNAVAILABLE')

    def test_observed_envelope_warns_for_one_burst_then_latches_sustained_growth(self):
        self.observed_reference()
        for i in range(7):self.event('2026-11-02T09:30:00+00:00','late-'+str(i))
        first=self.check();self.assertTrue(first['allowed']);self.assertTrue(first['assessment']['warnings'])
        self.assertEqual(first['assessment']['normal_reference_days'],0)
        for i in range(7):self.event('2026-11-02T08:30:00+00:00','early-'+str(i))
        got=self.check();self.assertFalse(got['allowed'])
        self.assertIn('SUSTAINED_OBSERVED_GROWTH',{r['rule'] for r in got['assessment']['anomalies']})
        self.assertEqual(self.check(now=self.now+timedelta(days=1))['lock']['id'],got['lock']['id'])
        self.assertEqual(json.loads(self.policy.read_text())['reviewed_days'],[])

    def test_same_elapsed_daily_growth_needs_responses_and_tokens(self):
        self.observed_reference()
        for i in range(13):self.event('2026-11-02T07:00:00+00:00','daily-'+str(i))
        got=self.check();self.assertFalse(got['allowed'])
        self.assertEqual({r['rule'] for r in got['assessment']['anomalies']},{'DAILY_RESPONSE_AND_TOKEN_GROWTH'})

    def test_small_or_unknown_profile_is_not_a_zero_allowance(self):
        self.observed_reference()
        for i in range(12):self.event('2026-11-02T09:30:00+00:00','new-'+str(i),model='new-model')
        got=self.check();self.assertTrue(got['allowed']);self.assertTrue(got['assessment']['unassessed'])
        self.assertFalse(got['assessment']['anomalies'])

    def test_sparse_known_profile_uses_frozen_peaks_without_inventing_activity(self):
        for i in range(2):self.event(self.days[0]+'T15:30:00+00:00','sparse-ref-'+str(i),model='sparse',cached=0)
        self.observed_reference();original=self.policy.read_text()
        for i in range(2):self.event('2026-11-02T09:30:00+00:00','shifted-'+str(i),model='sparse',cached=20)
        got=self.check();self.assertTrue(got['allowed']);self.assertFalse(got['assessment']['warnings'])
        comparisons={(r['window'],r['metric']):r for r in got['assessment']['comparisons']}
        hour=comparisons['hour_latest','sparse/high:responses']
        self.assertEqual(hour['comparison_basis'],'observed_hourly_peak_envelope')
        self.assertEqual(hour['same_clock_samples'],[0,0]);self.assertEqual(hour['samples'],[2,0])
        self.assertEqual(hour['positive_reference_days'],1);self.assertEqual(hour['threshold'],4)
        self.assertEqual(comparisons['day','sparse/high:responses']['comparison_basis'],'observed_full_day_envelope')
        self.assertEqual(comparisons['hour_latest','model-a/high:responses']['comparison_basis'],'same_clock')
        self.assertIn('hour_latest:sparse/high:cached_input',got['assessment']['unassessed'])
        self.assertEqual(self.policy.read_text(),original)

    def test_sparse_profile_sustained_growth_latches_but_one_reference_stays_unknown(self):
        for i in range(2):self.event(self.days[0]+'T08:30:00+00:00','sparse-ref-'+str(i),model='sparse')
        self.observed_reference()
        for hour in ('08','09'):
            for i in range(5):self.event('2026-11-02T'+hour+':30:00+00:00',hour+'-sparse-'+str(i),model='sparse')
        refs=self.config['change_reference_days'];self.config['change_reference_days']=refs[:1];self.save_policy()
        got=self.check();self.assertTrue(got['allowed']);self.assertTrue(got['assessment']['unassessed'])
        # Restoring the second reference is separately reviewed below; compare
        # directly so a policy-change latch cannot stand in for anomaly proof.
        self.config['change_reference_days']=refs
        with guard.connect(self.config['state_db']) as db:
            measured=guard.compare(db,self.config,self.now)
        self.assertEqual(measured['status'],'ANOMALOUS')
        self.assertEqual({r['rule'] for r in measured['anomalies']},
                         {'SUSTAINED_OBSERVED_GROWTH','DAILY_RESPONSE_AND_TOKEN_GROWTH'})
        self.save_policy();got=self.check()
        self.assertEqual(got['lock']['reason'],'ANOMALOUS')
        self.assertEqual(self.check(now=self.now+timedelta(days=1))['lock']['id'],got['lock']['id'])

    def test_sparse_midnight_fallback_keeps_higher_fingerprinted_leading_usage(self):
        for hour in ('22','23'):
            for i in range(10):self.event('2026-10-18T'+hour+':30:00+00:00','leading-'+hour+str(i),model='sparse')
        self.event(self.days[0]+'T15:30:00+00:00','small-day-peak',model='sparse')
        self.observed_reference()
        for hour in ('22','23'):
            for i in range(5):self.event('2026-11-01T'+hour+':30:00+00:00','current-'+hour+str(i),model='sparse')
        got=self.check(now=datetime(2026,11,2,0,15,tzinfo=timezone.utc))
        self.assertTrue(got['allowed']);self.assertFalse(got['assessment']['warnings'])
        rows=[r for r in got['assessment']['comparisons']
              if r['window'].startswith('hour_') and r['metric']=='sparse/high:responses']
        self.assertEqual(len(rows),2)
        for row in rows:
            self.assertEqual(row['samples'],[10,0]);self.assertEqual(row['threshold'],20)
            self.assertEqual(row['actual'],5)

    def test_small_reviewed_baseline_and_observed_references_still_require_integrity(self):
        self.config['reviewed_days']=self.config['reviewed_days'][:1];self.save_policy()
        self.event(self.days[0]+'T13:00:00+00:00','late-normal')
        self.assertEqual(self.check()['assessment']['status'],'BASELINE_CHANGED')
        self.observed_reference()
        self.event('2026-10-18T22:30:00+00:00','late-leading-two-hours')
        self.assertEqual(self.check()['assessment']['status'],'BASELINE_CHANGED')

    def test_baseline_only_legacy_lock_needs_explicit_review_and_keeps_new_faults(self):
        self.config['reviewed_days']=[];self.save_policy()
        old={'at':self.now.isoformat(),'reason':'INSUFFICIENT_BASELINE','assessment':{'status':'INSUFFICIENT_BASELINE'}}
        old['id']=guard.digest({'at':old['at'],'reason':old['reason'],'policy':guard.digest(self.config)})
        with guard.connect(self.config['state_db']) as db:guard.put(db,'lock',old)
        got=self.check();self.assertFalse(got['allowed']);self.assertEqual(got['lock']['id'],old['id'])
        release={'lock_id':old['id'],'evidence':'explicit revised user requirement and reviewed intact scope',
                 'policy_sha256':got['policy_sha256'],'reviewed_reasons':['INSUFFICIENT_BASELINE']}
        self.source.rename(self.root/'missing.sqlite')
        with self.assertRaises(ValueError):self.check(release=release)
        (self.root/'missing.sqlite').rename(self.source)
        with self.assertRaises(ValueError):self.check(release=release)
        got=self.check();self.assertIn('MEASUREMENT_UNAVAILABLE',got['lock_findings'])
        release['reviewed_reasons'].append('MEASUREMENT_UNAVAILABLE')
        self.assertTrue(self.check(release=release)['allowed'])
        with guard.connect(self.config['state_db']) as db:
            event=json.loads(db.execute("SELECT detail FROM history WHERE action='MANUAL_RELEASE'").fetchone()[0])
        self.assertEqual(event['lock']['id'],old['id']);self.assertIn('old_policy',event);self.assertIn('new_policy',event)

    def test_anomaly_cannot_be_released_by_removing_its_references(self):
        self.observed_reference()
        for hour in ('08','09'):
            for i in range(7):self.event('2026-11-02T'+hour+':30:00+00:00',hour+'-'+str(i))
        lock=self.check()['lock']
        self.config['change_reference_days']=[];self.save_policy()
        got=self.check(now=self.now+timedelta(days=1))
        with self.assertRaises(ValueError):
            self.check(now=self.now+timedelta(days=1),release={'lock_id':lock['id'],'evidence':'not sufficient',
                'policy_sha256':got['policy_sha256'],'reviewed_reasons':list(got['lock_findings'])})

    def test_observed_sustained_windows_cross_midnight_without_using_today_as_reference(self):
        for day in ('2026-10-18','2026-10-19'):
            for hour in ('22','23'):
                for i in range(3):self.event(day+'T'+hour+':30:00+00:00',day+hour+str(i))
        self.observed_reference()
        for hour in ('22','23'):
            for i in range(7):self.event('2026-11-01T'+hour+':30:00+00:00','current-'+hour+str(i))
        got=self.check(now=datetime(2026,11,2,0,15,tzinfo=timezone.utc))
        self.assertFalse(got['allowed'])
        self.assertEqual({r['rule'] for r in got['assessment']['anomalies']},{'SUSTAINED_OBSERVED_GROWTH'})
        self.assertEqual(got['assessment']['change_reference_days'],self.days[:2])

    def test_normal_measurement_does_not_bypass_explicit_release_review(self):
        self.source.rename(self.root/'missing.sqlite');old=self.check()
        (self.root/'missing.sqlite').rename(self.source)
        self.assertEqual(self.check()['assessment']['status'],'NORMAL')
        with self.assertRaises(ValueError):self.check(release={'lock_id':old['lock']['id'],'evidence':'incomplete review'})
        request={'lock_id':old['lock']['id'],'evidence':'source restored and exact original scope reviewed',
                 'policy_sha256':old['policy_sha256'],'reviewed_reasons':list(old['lock_findings'])}
        self.assertTrue(self.check(release=request)['allowed'])

    def test_legacy_lock_cannot_bind_to_a_changed_scope_using_last_policy_hash(self):
        original=dict(self.config)
        old={'at':self.now.isoformat(),'reason':'INSUFFICIENT_BASELINE','assessment':{'status':'INSUFFICIENT_BASELINE'}}
        old['id']=guard.digest({'at':old['at'],'reason':old['reason'],'policy':guard.digest(original)})
        with sqlite3.connect(self.source) as db:db.execute('INSERT INTO threads VALUES (?,?,?)',('other',str(self.log),'vscode'))
        self.config['roots']=['other'];self.config['reviewed_days']=[];self.save_policy()
        with guard.connect(self.config['state_db']) as db:
            guard.put(db,'lock',old)
            # Even the last policy may already have been updated by old code.
            guard.put(db,'policy',guard.digest(self.config))
        got=self.check();self.assertFalse(got['lock_scope_verified'])
        with self.assertRaises(ValueError):self.check(release={'lock_id':old['id'],'evidence':'cannot prove original scope',
            'policy_sha256':got['policy_sha256'],'reviewed_reasons':list(got['lock_findings'])})

    def test_normal_then_anomaly_then_restart_and_midnight_stay_locked(self):
        for i in range(3):
            self.event("2026-11-02T09:30:00+00:00", "today-" + str(i))
        first = self.check()
        self.assertTrue(first["allowed"])
        self.assertFalse(first["hard_account_spending_cap"])
        for i in range(3, 6):
            self.event("2026-11-02T09:45:00+00:00", "today-" + str(i))
        locked = self.check()
        self.assertFalse(locked["allowed"])
        self.assertEqual(locked["lock"]["reason"], "ANOMALOUS")
        self.assertEqual(self.check()["lock"]["id"], locked["lock"]["id"])
        with self.assertRaises(ValueError):
            self.check(release={"lock_id": locked["lock"]["id"], "evidence": "still abnormal"})
        next_day = self.now + timedelta(days=1)
        self.assertEqual(self.check(now=next_day)["lock"]["id"], locked["lock"]["id"])
        released = self.check(now=next_day, release={"lock_id": locked["lock"]["id"], "evidence": "human confirmed original cause fixed",
                             "policy_sha256":locked['policy_sha256'],'reviewed_reasons':list(locked['lock_findings'])})
        self.assertTrue(released["allowed"])

    def test_comparison_is_same_elapsed_time_not_whole_day_allowance(self):
        for d in self.days:
            for i in range(10):
                self.event(d + "T18:00:00+00:00", d + "-late-" + str(i))
        self.seal_baseline()
        for i in range(6):
            self.event("2026-11-02T09:30:00+00:00", "early-" + str(i))
        got = self.check()
        metric = next(x for x in got["assessment"]["anomalies"] if x["metric"].endswith(":responses") and x["window"] == "day")
        self.assertEqual(metric["threshold"], 4.5)
        self.assertFalse(got["allowed"])

    def test_weekend_and_unreviewed_incident_days_are_not_baselines(self):
        got = self.check(now=datetime(2026, 11, 1, 10, tzinfo=timezone.utc))
        self.assertEqual(got["assessment"]["status"], guard.MONITORED)
        self.assertTrue(got["allowed"])
        self.assertEqual(got["assessment"]["peer_days"], [])

    def test_short_spike_detected_even_within_daily_normal_volume(self):
        # Move almost all baseline work to earlier hours. Day totals remain 23;
        # six responses in the last hour exceed normal same-hour work of three.
        for d in self.days:
            for i in range(20):
                self.event(d + "T08:00:00+00:00", d + "-early-" + str(i))
        self.seal_baseline()
        for i in range(6):
            self.event("2026-11-02T09:30:00+00:00", "burst-" + str(i))
        got = self.check()
        self.assertTrue(got["assessment"]["anomalies"])
        self.assertEqual({x["window"] for x in got["assessment"]["anomalies"]}, {"hour"})

    def test_hour_burst_crosses_midnight_and_baseline_binds_leading_hour(self):
        for i in range(6):
            self.event('2026-11-01T23:45:00+00:00', 'midnight-burst-' + str(i))
        got = self.check(now=datetime(2026, 11, 2, 0, 15, tzinfo=timezone.utc))
        self.assertEqual(got['assessment']['status'], 'ANOMALOUS')
        self.assertEqual({v['window'] for v in got['assessment']['anomalies']}, {'hour'})
        self.event('2026-10-18T23:45:00+00:00', 'late-leading-baseline')
        self.assertEqual(self.check()['assessment']['status'], 'BASELINE_CHANGED')

    def test_descendant_failed_usage_and_duplicate_response_counted_once(self):
        child = self.root / "child.jsonl"
        child.write_text("")
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO threads VALUES (?,?,?)", ("child", str(child), json.dumps({"subagent": {"thread_spawn": {"parent_thread_id": "root"}}})))
        self.event("2026-11-02T09:30:00+00:00", "child-response", path=child, thread="child")
        self.event("2026-11-02T09:30:00+00:00", "child-response", path=child, thread="child")
        got = self.check()
        self.assertEqual(got["source"]["records"], 22)
        self.assertEqual(got["source"]["threads"], 2)
        self.assertEqual(self.check()["source"]["records"], 22)

    def test_partial_append_retained_then_counted_and_categories_do_not_double_count(self):
        self.event("2026-11-02T09:30:00+00:00", "partial", partial=True)
        self.assertEqual(self.check()["source"]["records"], 21)
        with self.log.open("a") as f:
            f.write("\n")
        got = self.check()
        values = {v["metric"].split(":")[-1]: v["actual"] for v in got["assessment"]["comparisons"] if v["window"] == "day"}
        self.assertEqual(values, {"responses": 1, "uncached_input": 20, "cached_input": 80, "output": 10})

    def test_missing_source_and_missing_day_fail_closed_and_latch(self):
        self.source.rename(self.root / "missing.sqlite")
        got = self.check()
        self.assertEqual(got["lock"]["reason"], "MEASUREMENT_UNAVAILABLE")
        self.assertFalse(got["allowed"])

    def test_incomplete_baseline_cannot_be_counted_as_zero(self):
        self.config["reviewed_days"][0]["date"] = "2026-10-28"
        self.save_policy()
        self.assertEqual(self.check()["lock"]["reason"], "INCOMPLETE_BASELINE_COVERAGE")

    def test_policy_changes_and_clock_rollback_need_review(self):
        self.assertTrue(self.check()["allowed"])
        self.config["workload"] = "reviewed-new-workload"
        for row in self.config["reviewed_days"]:
            row["workload"] = self.config["workload"]
        self.save_policy()
        lock = self.check()["lock"]
        self.assertEqual(lock["reason"], "POLICY_CHANGED")
        rollback = self.check(now=self.now - timedelta(minutes=1))
        self.assertFalse(rollback["allowed"])
        self.assertEqual(rollback["assessment"]["status"], "MEASUREMENT_UNAVAILABLE")

    def test_late_history_cannot_silently_change_reviewed_normal_baseline(self):
        self.event(self.days[0] + 'T12:00:00+00:00', 'unreviewed-late-usage')
        self.assertEqual(self.check()['lock']['reason'], 'BASELINE_CHANGED')

    def test_inherited_records_are_only_counted_in_original_thread(self):
        child = self.root / 'child.jsonl'
        child.write_bytes(self.log.read_bytes())
        with sqlite3.connect(self.source) as db:
            db.execute('INSERT INTO threads VALUES (?,?,?)', ('child', str(child), json.dumps({'subagent': {'thread_spawn': {'parent_thread_id': 'root'}}})))
        got = self.check(history=True)
        self.assertEqual(got['source']['records'], 21)
        self.assertEqual(len(got['baseline_candidates']), 7)
        self.assertTrue(all(r['review_status'] == 'UNREVIEWED' for r in got['baseline_candidates']))

    def test_separate_processes_share_one_persistent_latch(self):
        for i in range(6):
            self.event("2026-11-02T09:30:00+00:00", "parallel-" + str(i))
        code = "import sys,json;sys.path.insert(0,sys.argv[1]);import usage_guard;from datetime import datetime;print(json.dumps(usage_guard.evaluate(sys.argv[2],now=datetime.fromisoformat(sys.argv[3]))))"
        command = [sys.executable, "-c", code, str(Path(guard.__file__).parent), str(self.policy), self.now.isoformat()]
        with concurrent.futures.ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda _: subprocess.run(command, text=True, capture_output=True, check=True), range(2)))
        locks = [json.loads(r.stdout)["lock"]["id"] for r in results]
        self.assertEqual(locks[0], locks[1])
        with sqlite3.connect(self.config["state_db"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM history WHERE action='LOCK'").fetchone()[0], 1)


@unittest.skipUnless(shutil.which("zstd"), "compressed native sources require zstd")
class CompressedUsageGuardTests(unittest.TestCase):
    setUp = UsageGuardTests.setUp
    seal_baseline = UsageGuardTests.seal_baseline
    save_policy = UsageGuardTests.save_policy
    event = UsageGuardTests.event
    check = UsageGuardTests.check

    def compressed(self, data):
        return subprocess.run(["zstd", "-q", "-c"], input=data,
                              capture_output=True, check=True).stdout

    def compress_log(self, path=None):
        path = path or self.log
        target = Path(str(path) + ".zst")
        target.write_bytes(self.compressed(path.read_bytes()))
        path.unlink()
        return target

    def test_compressed_transition_replays_without_double_counting_or_unlocking(self):
        before = self.check(history=True)
        self.event("2026-11-02T09:30:00+00:00", "new-before-compression")
        target = self.compress_log()
        original = target.read_bytes()
        with patch.object(guard.subprocess, "Popen", side_effect=FileNotFoundError("zstd unavailable")):
            failed = self.check()
        got = self.check(history=True)
        self.assertEqual(got["source"]["records"], 22)
        self.assertEqual(got["assessment"]["status"], "NORMAL")
        self.assertFalse(got["allowed"])
        self.assertEqual(got["lock"]["id"], failed["lock"]["id"])
        self.assertEqual(got["baseline_candidates"], before["baseline_candidates"])
        self.assertEqual(self.check()["source"]["records"], 22)
        self.assertEqual(target.read_bytes(), original)
        release = {"lock_id": got["lock"]["id"], "evidence": "verified compression-only fixture recovery",
                   "policy_sha256": got["policy_sha256"], "reviewed_reasons": list(got["lock_findings"])}
        self.assertTrue(self.check(release=release)["allowed"])

    def test_plain_form_has_native_precedence_even_if_index_names_compressed(self):
        target = Path(str(self.log) + ".zst")
        target.write_bytes(b"invalid compressed sibling")
        with sqlite3.connect(self.source) as db:
            db.execute("UPDATE threads SET rollout_path=? WHERE id='root'", (str(target),))
        self.event("2026-11-02T09:30:00+00:00", "plain-current")
        self.assertEqual(self.check()["source"]["records"], 22)

    def test_native_materialization_rescans_and_counts_only_new_responses(self):
        data = self.log.read_bytes()
        target = self.compress_log()
        self.assertEqual(self.check()["source"]["records"], 21)
        self.log.write_bytes(data)
        self.event("2026-11-02T09:30:00+00:00", "after-materialization")
        self.assertEqual(self.check()["source"]["records"], 22)
        self.assertTrue(target.exists())

    def test_damaged_multiframe_scan_cannot_commit_partial_usage(self):
        self.event("2026-11-02T09:30:00+00:00", "unverified-new")
        data = self.log.read_bytes()
        target = self.compress_log()
        # The first frame emits valid records before the second frame fails.
        target.write_bytes(self.compressed(data) + self.compressed(b"{}\n")[:-2])
        got = self.check()
        self.assertEqual(got["assessment"]["status"], "MEASUREMENT_UNAVAILABLE")
        with guard.connect(self.config["state_db"]) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM responses").fetchone()[0], 21)
        target.write_bytes(self.compressed(data))
        repaired = self.check()
        self.assertEqual(repaired["source"]["records"], 22)
        self.assertEqual(repaired["lock"]["id"], got["lock"]["id"])

    def test_compressed_partial_record_and_oversized_line_fail_closed(self):
        target = self.compress_log()
        for data in (b'{"type":"turn_context"}', b"x" * (16 * 1024 * 1024 + 1) + b"\n"):
            target.write_bytes(self.compressed(data))
            got = self.check()
            self.assertFalse(got["allowed"])
            self.assertEqual(got["assessment"]["status"], "MEASUREMENT_UNAVAILABLE")

    def test_compressed_descendant_keeps_original_event_ownership(self):
        child = self.root / "child.jsonl"
        child.write_bytes(self.log.read_bytes())
        self.event("2026-11-02T09:30:00+00:00", "child-own", path=child, thread="child")
        self.compress_log(child)
        with sqlite3.connect(self.source) as db:
            db.execute("INSERT INTO threads VALUES (?,?,?)", ("child", str(child), "vscode"))
            db.execute("INSERT INTO thread_spawn_edges VALUES (?,?)", ("root", "child"))
        got = self.check()
        self.assertEqual(got["source"]["records"], 22)
        self.assertEqual(got["source"]["threads"], 2)
        self.assertEqual(self.check()["source"]["records"], 22)

    def test_representation_change_between_resolve_and_open_does_not_latch(self):
        resolve = guard.rollout_source
        data = self.log.read_bytes()
        switched = []

        def compress_after_resolve(value):
            result = resolve(value)
            if not switched:
                self.compress_log()
                switched.append(True)
            return result

        with patch.object(guard, "rollout_source", side_effect=compress_after_resolve):
            got = self.check()
        self.assertTrue(got["allowed"])
        self.assertEqual(got["source"]["records"], 21)
        switched.clear()

        def materialize_after_resolve(value):
            result = resolve(value)
            if not switched:
                self.log.write_bytes(data)
                Path(str(self.log) + ".zst").unlink()
                switched.append(True)
            return result

        with patch.object(guard, "rollout_source", side_effect=materialize_after_resolve):
            got = self.check()
        self.assertTrue(got["allowed"])
        self.assertEqual(got["source"]["records"], 21)

    def test_persistent_missing_representation_retries_are_bounded(self):
        self.log.unlink()
        with patch.object(guard.time, "sleep") as sleep:
            got = self.check()
        self.assertEqual(sleep.call_count, 3)
        self.assertEqual(got["assessment"]["status"], "MEASUREMENT_UNAVAILABLE")
        self.assertFalse(got["allowed"])


if __name__ == "__main__":
    unittest.main()
