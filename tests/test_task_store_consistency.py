"""Cross-file commit failures on fresh synthetic task stores only."""
import copy
from contextlib import redirect_stdout
import io
import json
import multiprocessing
from pathlib import Path
import sys
from unittest import mock

import test_framework as fixtures
import task_runtime as task
import task_coordination as coordination


def hold_index(root, ready, release):
    with coordination.index_lock(Path(root)):
        ready.set()
        release.wait(8)


class TaskStoreConsistencyTests(fixtures.FixtureCase):
    def create(self, identity="synthetic"):
        target = self.root / (identity + ".txt")
        target.write_bytes(b"synthetic artifact")
        spec = {"request_id": "request-" + identity, "requirement": "Synthetic consistency check",
                "acceptance_version": "v1", "workspace": str(self.root),
                "steps": [{"id": "a", "instruction": "Observe synthetic artifact",
                           "checks": [{"kind": "file", "path": target.name,
                                       "sha256": task.digest(target.read_bytes())}]}]}
        store = task.Store(self.a.codex_home, identity)
        return store, store.create(identity, spec)

    def verified(self):
        store, value = self.create()
        value, execution = task.reserve(store, value["revision"])
        worker = {"status": "completed", "message": "synthetic", "session_id": "synthetic-worker",
                  "message_sha256": task.digest(b"synthetic"), "events_sha256": "1" * 64, "exit": 0}
        value = task.finish(store, value["revision"], execution["id"], worker)
        self.assertEqual(value["steps"]["a"]["state"], "verified")
        return store, value

    def dispatch(self, store, request):
        stream = io.TextIOWrapper(io.BytesIO(json.dumps({"task_id": store.path.stem, **request}).encode()))
        out = io.StringIO()
        with mock.patch.object(sys, "stdin", stream), redirect_stdout(out):
            code = task.dispatch(store.root.parent)
        return code, json.loads(out.getvalue())

    def test_effect_lock_conflict_leaves_accept_revision_and_bytes_unchanged(self):
        store, value = self.verified()
        before = store.path.read_bytes()
        index = coordination.index_path(store.root).read_bytes()
        context = multiprocessing.get_context("spawn")
        ready, release = context.Event(), context.Event()
        process = context.Process(target=hold_index, args=(str(store.root), ready, release))
        process.start()
        try:
            self.assertTrue(ready.wait(5))
            with self.assertRaisesRegex(task.TaskError, "busy"):
                task.change(store, {"operation": "accept", "expected_revision": value["revision"]})
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(coordination.index_path(store.root).read_bytes(), index)
            self.assertFalse(store.read()["accepted"])
        finally:
            release.set()
            process.join(5)
            if process.is_alive():
                process.terminate(); process.join(5)
        self.assertEqual(process.exitcode, 0)

    def test_malformed_index_is_rejected_before_task_save(self):
        store, value = self.create()
        coordination.index_path(store.root).write_bytes(b"{")
        before = store.path.read_bytes()
        with self.assertRaises((task.TaskError, ValueError)):
            store.update(value["revision"], lambda current: current["delivery"].update(sent=True))
        self.assertEqual(store.path.read_bytes(), before)

    def test_full_index_is_rejected_before_reservation_or_record_save(self):
        store, value = self.create()
        entry = {"task_id": "peer", "execution_id": "original", "state": "uncertain",
                 "resources": [], "source_ref": "synthetic:original"}
        raw = task.encoded({"version": 1, "entries": [dict(entry, task_id="peer-" + str(i)) for i in range(256)]})
        coordination.index_path(store.root).write_bytes(raw)
        before = store.path.read_bytes()
        with self.assertRaisesRegex(task.TaskError, "capacity"):
            task.reserve(store, value["revision"])
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(coordination.index_path(store.root).read_bytes(), raw)

    def test_oversize_index_is_rejected_before_task_save(self):
        store, value = self.create()
        resource = {"kind": "logical", "identity": "synthetic", "object": "x" * 6000,
                    "mode": "write", "operation": "write", "source_ref": "synthetic:resource"}
        entries = [{"task_id": "peer-" + str(i), "execution_id": "original", "state": "uncertain",
                    "resources": [resource], "source_ref": "synthetic:original"} for i in range(160)]
        raw = task.encoded({"version": 1, "entries": entries})
        self.assertLess(len(raw), 1048576)
        coordination.index_path(store.root).write_bytes(raw)
        before = store.path.read_bytes()
        def grow(current):
            current["restrictions"] = [{"files": [], "resource_effects": [resource] * 16,
                                        "wait": {"source_ref": "synthetic:denial"}}]
        with self.assertRaisesRegex(task.TaskError, "size"):
            store.update(value["revision"], grow)
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(coordination.index_path(store.root).read_bytes(), raw)

    def test_oversize_record_is_rejected_before_publishing_a_reservation(self):
        store, value = self.create()
        before = store.path.read_bytes()
        def oversized_reservation(current):
            current["execution"] = {"id": "synthetic-oversized", "step": "a"}
            current["history"].append({"synthetic_padding": "x" * task.LIMIT})
        with self.assertRaisesRegex(task.TaskError, "record size"):
            store.update(value["revision"], oversized_reservation, reservation=True)
        self.assertEqual(store.path.read_bytes(), before)
        self.assertFalse(coordination.index_path(store.root).exists())

    def test_no_change_and_legacy_record_keep_revision_and_fingerprint(self):
        store, value = self.create()
        before = store.path.read_bytes()
        self.assertEqual(store.update(1, lambda current: task.NO_CHANGE), value)
        self.assertEqual(store.path.read_bytes(), before)
        code, observed = self.dispatch(store, {"operation": "status"})
        self.assertEqual(code, 0)
        self.assertEqual(observed["revision"], 1)
        self.assertEqual(observed["record_sha256"], value["record_sha256"])
        self.assertEqual(store.path.read_bytes(), before)

    def test_index_failure_after_accept_is_explicit_and_recovery_does_not_replay(self):
        store, value = self.verified()
        original = task.cf.write_file
        def fail_index(path, raw):
            if path == coordination.index_path(store.root):
                raise OSError("synthetic index write failure")
            return original(path, raw)
        with mock.patch.object(task.cf, "write_file", side_effect=fail_index):
            code, report = self.dispatch(store, {"operation": "accept", "expected_revision": value["revision"]})
        self.assertEqual(code, 1)
        self.assertEqual(report["status"], "task_partial_commit")
        self.assertEqual(report["phase"], "effect_index_write")
        self.assertEqual(report["task_commit_state"], "committed")
        self.assertEqual(report["committed_revision"], value["revision"] + 1)
        self.assertTrue(report["index_sync_pending"])
        self.assertTrue(store.read()["accepted"])
        durable = store.path.read_bytes()
        with self.assertRaises(task.RevisionConflict):
            task.change(store, {"operation": "accept", "expected_revision": value["revision"]})
        # Neither passive status nor index-only recovery executes a validator.
        with mock.patch.object(task, "validate", side_effect=AssertionError("no validator replay")):
            self.assertEqual(self.dispatch(store, {"operation": "status"})[0], 0)
            self.assertEqual(store.path.read_bytes(), durable)
            for _ in range(2):
                code, recovered = self.dispatch(store, {"operation": "sync-effects", "expected_revision": report["committed_revision"]})
                self.assertEqual(code, 0)
                self.assertFalse(recovered["index_sync_pending"])
                self.assertFalse(recovered["task_record_changed"])
                self.assertEqual(store.path.read_bytes(), durable)

    def test_post_replace_task_failure_reports_committed_not_unexecuted(self):
        store, value = self.verified()
        original = task.cf.write_file
        def fail_after_replace(path, raw):
            original(path, raw)
            if path == store.path:raise OSError("synthetic post-replace failure")
        with mock.patch.object(task.cf, "write_file", side_effect=fail_after_replace):
            with self.assertRaises(task.TaskCommitError) as caught:
                task.change(store, {"operation": "accept", "expected_revision": value["revision"]})
        report = caught.exception.commit_report
        self.assertEqual(report["task_commit_state"], "committed")
        self.assertEqual(report["committed_revision"], value["revision"] + 1)
        self.assertEqual(report["phase"], "task_record_write")
        self.assertTrue(store.read()["accepted"])
        store.sync_effects(report["committed_revision"])

    def test_post_replace_index_failure_is_reported_and_recovery_failure_stays_explicit(self):
        store, value = self.verified()
        original = task.cf.write_file
        def fail_after_index_replace(path, raw):
            original(path, raw)
            if path == coordination.index_path(store.root):raise OSError("synthetic post-index-replace failure")
        with mock.patch.object(task.cf, "write_file", side_effect=fail_after_index_replace):
            code, report = self.dispatch(store, {"operation": "accept", "expected_revision": value["revision"]})
            self.assertEqual(code, 1)
            self.assertEqual(report["task_commit_state"], "committed")
            code, failed_recovery = self.dispatch(store, {"operation": "sync-effects", "expected_revision": report["committed_revision"]})
        self.assertEqual(code, 1)
        self.assertEqual(failed_recovery["phase"], "effect_index_recovery")
        self.assertTrue(failed_recovery["index_sync_pending"])
        self.assertEqual(failed_recovery["committed_revision"], report["committed_revision"])
        self.assertFalse(store.sync_effects(report["committed_revision"])["index_sync_pending"])

    def test_pre_replace_task_failure_preserves_both_files_and_old_cas(self):
        store, value = self.verified()
        before = store.path.read_bytes()
        index = coordination.index_path(store.root).read_bytes()
        original = task.cf.write_file
        def fail_record(path, raw):
            if path == store.path:raise OSError("synthetic record write failure")
            return original(path, raw)
        with mock.patch.object(task.cf, "write_file", side_effect=fail_record):
            with self.assertRaises(OSError):
                task.change(store, {"operation": "accept", "expected_revision": value["revision"]})
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(coordination.index_path(store.root).read_bytes(), index)
        self.assertEqual(store.read()["revision"], value["revision"])

    def test_failed_readback_is_unknown_and_never_invents_a_commit_revision(self):
        store, value = self.verified()
        original = task.cf.write_file
        def fail_index(path, raw):
            if path == coordination.index_path(store.root):raise OSError("synthetic index failure")
            return original(path, raw)
        with mock.patch.object(store, "read", side_effect=[copy.deepcopy(value), OSError("synthetic readback failure")]), \
             mock.patch.object(task.cf, "write_file", side_effect=fail_index):
            with self.assertRaises(task.TaskCommitError) as caught:
                task.change(store, {"operation": "accept", "expected_revision": value["revision"]})
        report = caught.exception.commit_report
        self.assertEqual(report["task_commit_state"], "unknown")
        self.assertIsNone(report["committed_revision"])
        self.assertIn("record_observation_error", report)
        self.assertTrue(store.read()["accepted"])

    def test_reservation_task_failure_retains_unknown_claim_and_blocks_replacement(self):
        store, value = self.create()
        original = task.cf.write_file
        def fail_record(path, raw):
            if path == store.path:raise OSError("synthetic record failure after reservation")
            return original(path, raw)
        with mock.patch.object(task.cf, "write_file", side_effect=fail_record):
            with self.assertRaises(task.TaskCommitError) as caught:
                task.reserve(store, value["revision"])
        self.assertEqual(caught.exception.commit_report["task_commit_state"], "not_committed")
        self.assertEqual(store.read()["revision"], value["revision"])
        index = coordination.index_path(store.root).read_bytes()
        self.assertEqual(coordination.read_index(store.root)[0]["state"], "active")
        for operation in (lambda: store.sync_effects(value["revision"]),
                          lambda: task.reserve(store, value["revision"])):
            with self.assertRaisesRegex(task.TaskError, "unmatched original effect"):
                operation()
            self.assertEqual(coordination.index_path(store.root).read_bytes(), index)
        self.assertIsNone(store.read()["execution"])

    def test_reservation_index_failure_stops_before_record_or_worker(self):
        store, value = self.create()
        original = task.cf.write_file
        def fail_index(path, raw):
            if path == coordination.index_path(store.root):raise OSError("synthetic reservation failure")
            return original(path, raw)
        with mock.patch.object(task.cf, "write_file", side_effect=fail_index), \
             mock.patch("task_worker.execute", side_effect=AssertionError("no worker")):
            with self.assertRaises(task.TaskCommitError) as caught:
                task.run_once(store, value["revision"])
        self.assertEqual(caught.exception.commit_report["task_commit_state"], "not_committed")
        self.assertEqual(caught.exception.commit_report["phase"], "reservation_index_write")
        self.assertEqual(store.read()["revision"], value["revision"])
        self.assertIsNone(store.read()["execution"])

    def test_unmatched_resources_cannot_be_erased_by_recovery_or_ordinary_update(self):
        store, value = self.create()
        value, _ = task.reserve(store, value["revision"])
        entries = coordination.read_index(store.root)
        extra = dict(entries[0]["resources"][0], identity=str(self.root / "unknown-original.txt"))
        entries[0]["resources"].append(extra)
        raw = task.encoded({"version": 1, "entries": entries})
        task.cf.write_file(coordination.index_path(store.root), raw)
        before = store.path.read_bytes()
        for operation in (lambda: store.sync_effects(value["revision"]),
                          lambda: store.update(value["revision"], lambda current: current["delivery"].update(sent=True))):
            with self.assertRaisesRegex(task.TaskError, "unmatched original effect resources"):
                operation()
            self.assertEqual(store.path.read_bytes(), before)
            self.assertEqual(coordination.index_path(store.root).read_bytes(), raw)

    def test_recovery_preserves_peers_denials_and_unresolved_continuation(self):
        store, value = self.create()
        def seed(current):
            current["steps"]["a"]["continuation"] = {
                "origin": {"operation_id": "synthetic-op", "attempt_id": "synthetic-attempt",
                           "session_id": "synthetic-session", "turn_id": "synthetic-turn"},
                "terminal": "unknown", "terminal_ref": None, "effects": [], "resolved": False}
        value = store.update(value["revision"], seed)
        original_entries = coordination.read_index(store.root)
        refusal = dict(original_entries[0], state="denial", execution_id="retained-denial", source_ref="synthetic:refusal")
        peer = dict(original_entries[0], task_id="peer", execution_id="peer-unknown")
        task.cf.write_file(coordination.index_path(store.root), task.encoded({"version": 1, "entries": [peer, refusal] + original_entries}))
        before = store.path.read_bytes()
        store.sync_effects(value["revision"])
        entries = coordination.read_index(store.root)
        self.assertIn(peer, entries)
        self.assertIn(refusal, entries)
        self.assertIn(original_entries[0], entries)
        self.assertEqual(store.path.read_bytes(), before)
        with self.assertRaises(task.TaskError):
            task.change(store, {"operation": "accept", "expected_revision": value["revision"]})

    def test_resolved_continuation_recovery_retires_only_its_claim(self):
        store, value = self.create()
        def seed(current):
            current["steps"]["a"]["continuation"] = {
                "origin": {"operation_id": "synthetic-op", "attempt_id": "synthetic-attempt",
                           "session_id": "synthetic-session", "turn_id": "synthetic-turn"},
                "terminal": "completed", "terminal_ref": None, "effects": [], "resolved": False}
        value = store.update(value["revision"], seed)
        original_entries = coordination.read_index(store.root)
        peer = dict(original_entries[0], task_id="peer", execution_id="peer-unknown")
        refusal = dict(original_entries[0], state="denial", execution_id="retained-denial", source_ref="synthetic:refusal")
        task.cf.write_file(coordination.index_path(store.root), task.encoded({"version": 1, "entries": [peer, refusal] + original_entries}))
        original = task.cf.write_file
        def fail_index(path, raw):
            if path == coordination.index_path(store.root):raise OSError("synthetic resolution index failure")
            return original(path, raw)
        with mock.patch.object(task.cf, "write_file", side_effect=fail_index):
            with self.assertRaises(task.TaskCommitError) as caught:
                store.update(value["revision"], lambda current: current["steps"]["a"]["continuation"].update(resolved=True))
        current = store.read()
        self.assertTrue(current["steps"]["a"]["continuation"]["resolved"])
        self.assertEqual(caught.exception.commit_report["committed_revision"], current["revision"])
        store.sync_effects(current["revision"])
        self.assertEqual(coordination.read_index(store.root), [peer, refusal])

    def test_amend_revision_cas_and_recovery_never_reapply_input(self):
        store, value = self.create()
        revised = copy.deepcopy(value["spec"])
        revised["acceptance_version"] = "v2"
        original = task.cf.write_file
        # Ensure an index exists even though this amendment has no live claims.
        task.cf.write_file(coordination.index_path(store.root), task.encoded({"version": 1, "entries": []}))
        def fail_index(path, raw):
            if path == coordination.index_path(store.root):raise OSError("synthetic amendment index failure")
            return original(path, raw)
        with mock.patch.object(task.cf, "write_file", side_effect=fail_index):
            with self.assertRaises(task.TaskCommitError) as caught:
                task.change(store, {"operation": "amend", "expected_revision": 1, "spec": revised})
        self.assertEqual(caught.exception.commit_report["committed_revision"], 2)
        self.assertEqual(store.read()["input_revision"], 2)
        before = store.path.read_bytes()
        with self.assertRaises(task.RevisionConflict):store.sync_effects(1)
        store.sync_effects(2)
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(store.read()["spec"], revised)

    def test_index_lock_ownership_is_bound_to_one_root(self):
        store, _ = self.create()
        other = self.root / "different-root"
        with coordination.index_lock(store.root):
            with coordination.index_lock(store.root):pass
            with self.assertRaisesRegex(task.TaskError, "different stores"):
                with coordination.index_lock(other):pass

    def test_workset_keeps_partial_commit_details_without_dispatching_worker(self):
        store, value = self.create()
        task.cf.write_file(coordination.index_path(store.root), task.encoded({"version": 1, "entries": []}))
        original = task.cf.write_file
        def fail_index(path, raw):
            if path == coordination.index_path(store.root):raise OSError("synthetic workset index failure")
            return original(path, raw)
        with mock.patch.object(task.cf, "write_file", side_effect=fail_index), \
             mock.patch("task_worker.execute", side_effect=AssertionError("no worker")):
            result = coordination.workset(store.root.parent, {"operation": "work", "task_ids": [value["task_id"]],
                "expected_revisions": {value["task_id"]: value["revision"]}, "max_turns": 1})
        self.assertEqual(result["executed"][0]["result"], "scoped_error")
        self.assertEqual(result["executed"][0]["commit"]["committed_revision"], value["revision"] + 1)
        self.assertTrue(result["executed"][0]["commit"]["index_sync_pending"])

    def test_partial_finish_observation_is_not_mislabeled_as_unapplied(self):
        store, value = self.create()
        worker = {"status": "completed", "message": "synthetic", "session_id": "synthetic-worker",
                  "message_sha256": task.digest(b"synthetic"), "events_sha256": "1" * 64, "exit": 0}
        original = coordination.write_index
        def fail_consumed_index(target, raw):
            if target.read()["steps"]["a"]["state"] == "verified":
                raise OSError("synthetic consumed-result index failure")
            return original(target, raw)
        with mock.patch.object(coordination, "write_index", side_effect=fail_consumed_index), \
             mock.patch("task_worker.execute", return_value=worker) as execute:
            with self.assertRaises(task.TaskCommitError) as caught:
                task.run_once(store, value["revision"])
        self.assertEqual(execute.call_count, 1)
        current = store.read()
        self.assertEqual(current["steps"]["a"]["state"], "verified")
        self.assertEqual(caught.exception.commit_report["committed_revision"], current["revision"])
        observations = list(store.root.glob("*.observation.json"))
        self.assertEqual(len(observations), 1)
        saved = json.loads(observations[0].read_bytes())
        self.assertEqual(saved["state"], "partial_task_commit")
        self.assertEqual(saved["commit"]["committed_revision"], current["revision"])
        original_index = coordination.index_path(store.root).read_bytes()
        corrupted = json.loads(original_index)
        corrupted["entries"][0]["resources"].append(dict(corrupted["entries"][0]["resources"][0],
                                                       identity=str(self.root / "unknown-original.txt")))
        task.cf.write_file(coordination.index_path(store.root), task.encoded(corrupted))
        with self.assertRaisesRegex(task.TaskError, "unmatched original effect resources"):
            store.sync_effects(current["revision"])
        task.cf.write_file(coordination.index_path(store.root), original_index)
        # Original terminal validation is sufficient to retire its stale active claim.
        store.sync_effects(current["revision"])
        self.assertEqual(coordination.read_index(store.root), [])

    def test_workset_intake_partial_commit_preserves_original_receipt_and_duplicate_noop(self):
        from test_result_inbox import ResultInboxTests
        fixture = ResultInboxTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.addCleanup(fixture.doCleanups)
        store, _ = fixture.create()
        fixture.finite(store)
        result = fixture.result(store, "synthetic-partial")
        original = task.cf.write_file
        def fail_index(path, raw):
            if path == coordination.index_path(store.root):raise OSError("synthetic intake index failure")
            return original(path, raw)
        with mock.patch.object(task.cf, "write_file", side_effect=fail_index):
            response = fixture.work({store.path.stem: store}, results={store.path.stem: {"result": result}})
        failure = response["intake_errors"][0]
        self.assertEqual(failure["commit"]["committed_revision"], store.read()["revision"])
        self.assertTrue(failure["commit"]["index_sync_pending"])
        self.assertEqual(store.read()["result_state"]["receipts"][0]["result"], result)
        store.sync_effects(store.read()["revision"])
        before = store.path.read_bytes()
        response = fixture.work({store.path.stem: store}, results={store.path.stem: {"result": result}})
        self.assertEqual(response["received"][0]["state"], "duplicate_reused")
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(fixture.model.call_count + fixture.native.call_count, 0)

    def test_external_validation_partial_commit_recovers_original_read_reservation(self):
        from test_result_inbox import ResultInboxTests
        fixture = ResultInboxTests()
        fixture.setUp()
        self.addCleanup(fixture.tearDown)
        self.addCleanup(fixture.doCleanups)
        store, _ = fixture.create()
        original = coordination.write_index
        def fail_external_commit(target, raw):
            current = target.read()
            if current["history"] and "external_observation" in current["history"][-1]:
                raise OSError("synthetic external validation commit failure")
            return original(target, raw)
        with mock.patch.object(coordination, "write_index", side_effect=fail_external_commit):
            with self.assertRaises(task.TaskCommitError) as caught:
                fixture.finite(store)
        current = store.read()
        self.assertEqual(caught.exception.commit_report["committed_revision"], current["revision"])
        self.assertEqual(current["steps"]["a"]["state"], "verified")
        self.assertEqual(coordination.read_index(store.root)[0]["resources"][0]["mode"], "read")
        before = store.path.read_bytes()
        store.sync_effects(current["revision"])
        self.assertEqual(coordination.read_index(store.root), [])
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(fixture.model.call_count + fixture.native.call_count, 0)


if __name__ == "__main__":
    import unittest
    unittest.main()
