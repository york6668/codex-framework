"""Bounded local-file durability and scoped projection; no real user data or IO."""

import copy
import errno
import fcntl
import json
import os
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_framework import ROOT, FixtureCase, seed_runtime
import coordination_files as files
import codex_framework as cf
import framework_runtime as runtime


def agreement_document():
    return {"format_version": 1, "revision": 1, "change_id": "seed",
            "authority": {"owner_ref": "demo:personal", "storage_ref": "demo:agreements"},
            "systems": [], "agreements": [{"id": "A", "version": "1", "status": "effective",
                "kind": "requirement", "source": {"ref": "demo:original", "level": "synthetic_original",
                    "occurred_at": "2030-01-01T09:00:00Z"}, "statement": "Deliver a usable demo result",
                "interpretation": "Verify through its normal entry", "applies_to": {"systems": ["demo"],
                    "objects": ["demo-result"]}, "canonical_refs": ["demo:requirement"], "changes": [],
                "supersedes": [], "unresolved": []}]}


def task_document():
    return {"format_version": 1, "revision": 1, "change_id": "seed", "system_id": "demo",
            "authority": {"owner_ref": "demo:owner", "storage_ref": "demo:tasks"},
            "tasks": [{"task_id": "T", "request_ref": "demo:request", "requirement_ref": "demo:requirement",
                "input_revision": 1, "plan_revision": 1, "agreements": [{"id": "A", "version": "1"}],
                "scope": {"goal": "Usable demo", "objects": ["demo-result"], "excluded": ["other node"]},
                "acceptance": [{"id": "usable", "criterion_ref": "demo:criterion"}],
                "assignment": {"executor": "demo:engineer", "delivery_owner": "demo:owner"},
                "write_scope": ["demo:result"], "execution": {"node_id": "demo-node"},
                "native_binding": {"provider": "synthetic", "request_ref": "demo:native-input"},
                "dependencies": [], "plan_changes": [], "stop_conditions": ["original denial or unknown"],
                "result_binding": {"kind": "file", "id": "demo-result", "path": "result",
                    "node_id": "demo-node", "product_version": "1.0.0", "content_ref": "demo:content-v1",
                    "effect_key": "demo-result-write"}}]}


def event(seq=1, kind="diagnostic_returned", **fields):
    return {"format_version": 1, "change_id": "change-" + str(seq), "event_id": "E" + str(seq),
            "seq": seq, "system_id": "demo", "task_id": "T", "request_ref": "demo:request",
            "input_revision": 1, "plan_revision": 1, "kind": kind, "actor": "demo:engineer",
            "occurred_at": "2030-01-01T09:00:00Z", "observed_at": "2030-01-01T09:01:00Z",
            "read_at": "2030-01-01T10:00:00Z", "evidence_refs": ["demo:evidence"],
            "result": "diagnosis_only", **fields}


def view_request():
    binding = task_document()["tasks"][0]["result_binding"]
    return {"operation": "view", "agreements": agreement_document(), "tasks": task_document(),
            "as_of": "2030-01-01T10:00:00Z", "max_age_seconds": 600, "history_complete": True,
            "progress": [event(1, "goal_verification_pass", result="PASS", covers=["usable"], result_binding=binding),
                         event(2, "delivery_accepted", actor="demo:owner", decision_ref="demo:decision",
                               effect_event_ids=["E1"], closes=["usable"]),
                         event(3, "successor_executed", related_event_id="E2", consumed_event_ids=["E1", "E2"], result="recorded_and_read_back")]}


class CoordinationFileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="coordination-files-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()

    def request(self, kind="agreements", operation="commit", **fields):
        return {"operation": operation, "binding": {"kind": kind, "format_version": 1,
                "path": str(self.root / (kind + (".jsonl" if kind == "progress" else ".json")))}, **fields}

    def seed(self, kind="agreements"):
        document = agreement_document() if kind == "agreements" else task_document()
        request = self.request(kind, create=True, change_id="seed", expected_revision=0, document=document)
        self.assertEqual(files.handle(request)["status"], "committed")
        return document, Path(request["binding"]["path"])

    def append(self, value, expected):
        return files.handle(self.request("progress", event=value, expected_last_seq=expected,
                                         create=True, change_id=value["change_id"]))

    def row(self, request):
        output = files.handle(request)
        self.assertEqual(output["status"], "read_only_view", output)
        self.assertFalse(output["domain_action_performed"])
        return output["tasks"][0]

    def test_read_is_explicit_does_not_create_file_lock_directory_or_store(self):
        result = files.handle(self.request(operation="read"))
        self.assertEqual(result["status"], "read_unavailable")
        self.assertEqual(list(self.root.iterdir()), [])
        request = self.request(create=True, change_id="seed", expected_revision=0, document=agreement_document())
        request["binding"]["path"] = str(self.root / "absent" / "agreements.json")
        self.assertEqual(files.handle(request)["status"], "write_unavailable")
        self.assertFalse((self.root / "absent").exists())

    def test_snapshot_cas_and_pre_replace_failure_retain_original(self):
        document, path = self.seed()
        document.update(revision=2, change_id="next")
        request = self.request(expected_revision=1, change_id="next", document=document)
        self.assertEqual(files.handle(request)["status"], "committed")
        before = path.read_bytes()
        self.assertEqual(files.handle(request)["status"], "revision_conflict")
        self.assertEqual(path.read_bytes(), before)
        with mock.patch.object(files.os, "replace", side_effect=OSError(errno.EIO, "injected")):
            document.update(revision=3, change_id="later")
            self.assertEqual(files.handle(self.request(expected_revision=2, change_id="later", document=document))["status"],
                             "write_unavailable")
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(list(self.root.glob(".coordination-*")))

    def test_noncooperating_editor_update_is_checked_again_before_replace(self):
        document, path = self.seed()
        document.update(revision=2, change_id="next")
        external = dict(agreement_document(), external_note="retain this update")
        original = files.os.fsync
        changed = []
        def edit_during_flush(fd):
            result = original(fd)
            if not changed:
                changed.append(True)
                path.write_bytes(files.encoded(external) + b"\n")
            return result
        with mock.patch.object(files.os, "fsync", side_effect=edit_during_flush):
            output = files.handle(self.request(expected_revision=1, change_id="next", document=document))
        self.assertEqual(output["status"], "revision_conflict")
        self.assertEqual(json.loads(path.read_bytes()), external)

    def test_existing_writer_lock_and_unknown_extensions_are_preserved(self):
        path = self.root / "agreements.json"
        initial = dict(agreement_document(), original_extension={"original_source_groups": ["demo:source"]})
        path.write_bytes(files.encoded(initial))
        incoming = dict(initial, revision=2, change_id="next")
        request = self.request(expected_revision=1, change_id="next", document=incoming)
        lock = self.root / "agreements.lock"
        request["binding"]["lock_path"] = str(lock)
        with lock.open("wb") as stream:
            inode = lock.stat().st_ino
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.assertEqual(files.handle(request)["status"], "revision_conflict")
        self.assertEqual(files.handle(request)["status"], "committed")
        self.assertEqual(lock.stat().st_ino, inode)
        self.assertFalse((self.root / "agreements.json.coordination.lock").exists())
        incoming.update(revision=3, change_id="bad-drop")
        incoming.pop("original_extension")
        request.update(expected_revision=2, change_id="bad-drop")
        self.assertEqual(files.handle(request)["status"], "invalid_record")

    def test_concurrent_processes_exactly_one_commit_without_losing_winner(self):
        document, path = self.seed()
        requests = []
        for name in ("writer-a", "writer-b"):
            value = dict(document, revision=2, change_id=name)
            requests.append(self.request(expected_revision=1, change_id=name, document=value))
        processes = [subprocess.Popen([sys.executable, "-B", str(ROOT / "scripts/codex_framework.py"), "coordination"],
                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in requests]
        for process, request in zip(processes, requests):
            process.stdin.write(json.dumps(request)); process.stdin.close(); process.stdin = None
        outputs = [json.loads(process.communicate(timeout=10)[0]) for process in processes]
        self.assertCountEqual([o["status"] for o in outputs], ["committed", "revision_conflict"])
        winner = next(o["change_id"] for o in outputs if o["status"] == "committed")
        self.assertEqual(json.loads(path.read_text())["change_id"], winner)

    def test_event_duplicate_identical_bytes_reuses_original_even_with_stale_seq(self):
        value = event()
        self.assertEqual(self.append(value, 0)["status"], "committed")
        self.assertEqual(self.append(value, 0)["status"], "duplicate_reused")
        value["result"] = "different"
        self.assertEqual(self.append(value, 1)["status"], "event_conflict")
        self.assertEqual(self.append(event(3), 1)["status"], "incomplete_history")

    def test_imported_event_spacing_is_a_byte_conflict_and_partial_tail_never_repaired(self):
        path = self.root / "progress.jsonl"
        raw = (json.dumps(event()) + "\n").encode()
        path.write_bytes(raw)
        self.assertEqual(self.append(event(), 1)["status"], "event_conflict")
        path.write_bytes(raw + b'{"format_version":1')
        before = path.read_bytes()
        self.assertEqual(self.append(event(2), 1)["status"], "incomplete_history")
        self.assertEqual(path.read_bytes(), before)

    def test_post_replace_durability_failure_is_unknown_and_record_recovery_is_idempotent(self):
        original = files.os.fsync
        def fail_directory(fd):
            if stat.S_ISDIR(os.fstat(fd).st_mode):
                raise OSError(errno.EIO, "injected directory durability failure")
            return original(fd)
        with mock.patch.object(files.os, "fsync", side_effect=fail_directory):
            result = self.append(event(), 0)
        self.assertEqual(result["status"], "write_outcome_unknown")
        self.assertEqual(files.handle(self.request("progress", "read"))["last_seq"], 1)
        self.assertEqual(self.append(event(), 0)["status"], "duplicate_reused")

    def test_cross_file_partial_failure_does_not_undo_snapshot_or_repeat_effect(self):
        document, path = self.seed("tasks")
        progress = self.root / "progress.jsonl"
        progress.write_bytes(b'{"partial":')
        self.assertEqual(self.append(event(), 0)["status"], "incomplete_history")
        self.assertEqual(json.loads(path.read_bytes()), document)
        self.assertEqual(progress.read_bytes(), b'{"partial":')

    def test_unknown_formats_duplicate_keys_nonfinite_symlinks_fifo_and_oversize_refuse(self):
        _, path = self.seed()
        for raw in (b'{"format_version":2}', b'{"format_version":true}',
                    b'{"format_version":1,"format_version":1}', b'{"x":NaN}'):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                output = files.handle(self.request(operation="read"))
                self.assertNotEqual(output["status"], "read")
                self.assertEqual(path.read_bytes(), raw)
        path.unlink(); path.symlink_to(self.root / "missing")
        self.assertEqual(files.handle(self.request(operation="read"))["status"], "read_unavailable")
        path.unlink(); os.mkfifo(path)
        self.assertEqual(files.handle(self.request(operation="read"))["status"], "read_unavailable")
        path.unlink(); path.write_bytes(files.encoded(agreement_document()))
        os.link(path, self.root / "hardlink")
        self.assertEqual(files.handle(self.request(operation="read"))["status"], "read_unavailable")
        (self.root / "hardlink").unlink()
        path.unlink(); path.write_bytes(b" " * (files.MAX_BYTES + 1))
        self.assertEqual(files.handle(self.request(operation="read"))["status"], "budget_exceeded")
        parent = self.root / "alias"; parent.symlink_to(self.root, target_is_directory=True)
        request = self.request(operation="read"); request["binding"]["path"] = str(parent / path.name)
        self.assertEqual(files.handle(request)["status"], "read_unavailable")

    def test_agreement_history_immutable_and_status_correction_is_sourced(self):
        document, path = self.seed()
        document.update(revision=2, change_id="correction")
        old = document["agreements"][0]
        old["source"]["ref"] = "demo:substituted"
        request = self.request(expected_revision=1, change_id="correction", document=document)
        self.assertEqual(files.handle(request)["status"], "invalid_record")
        old["source"]["ref"] = "demo:original"; old["status"] = "superseded"
        old["status_changes"] = [{"from": "effective", "to": "superseded", "source_ref": "demo:correction", "reason": "New acceptance"}]
        new = copy.deepcopy(old); new.update(version="2", status="effective", status_changes=[],
                                            supersedes=[{"id": "A", "version": "1"}])
        document["agreements"].append(new)
        self.assertEqual(files.handle(request)["status"], "committed")
        new["supersedes"] = [{"id": "A", "version": "2"}]
        document.update(revision=3, change_id="bad-cycle")
        self.assertEqual(files.handle(self.request(expected_revision=2, change_id="bad-cycle", document=document))["status"], "invalid_record")
        self.assertEqual(len(json.loads(path.read_bytes())["agreements"]), 2)

    def test_requirement_and_plan_revisions_preserve_old_obligations(self):
        document, _ = self.seed("tasks")
        document.update(revision=2, change_id="implementation")
        row = document["tasks"][0]
        row.update(plan_revision=2, plan_changes=[{"revision": 2, "input_revision": 1,
                    "kind": "internal_implementation_change", "source_ref": "demo:diagnosis"}])
        self.assertEqual(files.handle(self.request("tasks", expected_revision=1, change_id="implementation", document=document))["status"], "committed")
        previous = copy.deepcopy(row)
        document.update(revision=3, change_id="requirement")
        row["scope"]["goal"] = "New requirement"
        row.update(plan_revision=3, input_revision=2)
        row["plan_changes"].append({"revision": 3, "input_revision": 2, "kind": "requirement_change", "source_ref": "demo:new-original"})
        request = self.request("tasks", expected_revision=2, change_id="requirement", document=document)
        self.assertEqual(files.handle(request)["status"], "invalid_record")
        row["input_history"] = [{k: previous[k] for k in (*files.REQUIREMENT_FIELDS, "input_revision")}]
        self.assertEqual(files.handle(request)["status"], "committed")
        document.update(revision=4, change_id="drop"); document["tasks"] = []
        self.assertEqual(files.handle(self.request("tasks", expected_revision=3, change_id="drop", document=document))["status"], "invalid_record")

    def test_local_tests_diagnosis_terminal_and_wrong_scope_cannot_close(self):
        for kind in ("diagnostic_returned", "selftest_pass", "native_terminal"):
            request = view_request(); request["progress"][0]["kind"] = kind
            self.assertEqual(self.row(request)["pending_acceptance"], ["usable"])
        for field, value in (("node_id", "other-node"), ("product_version", "2.0.0"), ("content_ref", "other")):
            request = view_request(); request["progress"][0]["result_binding"][field] = value
            self.assertEqual(self.row(request)["pending_acceptance"], ["usable"])
        request = view_request(); request["progress"][0]["input_revision"] = 2
        self.assertEqual(self.row(request)["pending_acceptance"], ["usable"])

    def test_owner_acceptance_and_actual_successor_separate_from_freshness(self):
        row = self.row(view_request())
        self.assertTrue(row["scope_accepted_in_supplied_records"])
        self.assertEqual(row["successor_event_ids"], ["E3"])
        self.assertEqual(row["latest_observation"]["freshness"], "stale")
        request = view_request(); request["progress"][1]["actor"] = "demo:engineer"
        self.assertFalse(self.row(request)["scope_accepted_in_supplied_records"])
        request = view_request(); request["progress"].pop()
        self.assertTrue(self.row(request)["next_action_review_needed"])

    def test_foreign_goals_extension_remains_protected_for_existing_snapshot_kinds(self):
        for kind, initial in (("agreements", agreement_document()), ("tasks", task_document())):
            with self.subTest(kind=kind):
                path = self.root / (kind + '.json')
                initial['goals'] = {'private_extension': ['original-field']}
                path.write_bytes(files.encoded(initial))
                before = path.read_bytes()
                for edit in ('delete', 'replace'):
                    incoming = copy.deepcopy(initial); incoming.update(revision=2, change_id=edit)
                    if edit == 'delete': incoming.pop('goals')
                    else: incoming['goals'] = {'private_extension': ['replacement']}
                    request = self.request(kind, expected_revision=1, change_id=edit, document=incoming)
                    self.assertEqual(files.handle(request)['status'], 'invalid_record')
                    self.assertEqual(path.read_bytes(), before)

    def test_delayed_old_settlement_needs_explicit_actual_consumption_frontier(self):
        for kind in ('successor_executed', 'legal_stop_recorded'):
            request = view_request(); request['as_of'] = '2030-01-01T11:00:00Z'
            request['progress'].pop()
            request['progress'].append(event(3, 'diagnostic_returned', observed_at='2030-01-01T10:00:00Z'))
            old = event(4, kind, related_event_id='E2', reason='Old wait only',
                        consumed_event_ids=['E1','E2'], observed_at='2030-01-01T09:02:00Z')
            request['progress'].append(old)
            self.assertTrue(self.row(request)['next_action_review_needed'])
            request['progress'][-1].pop('consumed_event_ids')
            self.assertTrue(self.row(request)['next_action_review_needed'])
            request['progress'].append(event(5, 'legal_stop_recorded', reason='New evidence actually reviewed',
                consumed_event_ids=['E1','E2','E3','E4'], observed_at='2030-01-01T10:01:00Z'))
            self.assertFalse(self.row(request)['next_action_review_needed'])
            # A fresh wall clock without a causal frontier proves no review.
            request['progress'][-1].pop('consumed_event_ids')
            self.assertTrue(self.row(request)['next_action_review_needed'])

    def test_reread_does_not_refresh_source_future_or_incomplete_history_cannot_close(self):
        request = view_request(); request["progress"] = [event(1, "execution_started")]
        self.assertEqual(self.row(request)["latest_observation"]["age_seconds"], 3540)
        request["progress"][0]["read_at"] = "2030-01-01T10:30:00Z"
        self.assertEqual(self.row(request)["latest_observation"]["freshness"], "stale")
        request = view_request(); request["history_complete"] = False
        self.assertFalse(self.row(request)["scope_accepted_in_supplied_records"])
        request = view_request(); request["progress"][0]["observed_at"] = "2031-01-01T00:00:00Z"
        self.assertFalse(self.row(request)["scope_accepted_in_supplied_records"])

    def test_denial_unknown_old_input_new_task_or_pass_cannot_erase_same_effect(self):
        for kind in ("platform_denied", "effect_unknown"):
            request = view_request()
            binding = copy.deepcopy(request["tasks"]["tasks"][0]["result_binding"])
            binding.update(product_version="0.1.0", content_ref="old-content")
            request["progress"].append(event(4, kind, task_id="old-task", result="UNKNOWN", result_binding=binding))
            row = self.row(request)
            self.assertFalse(row["scope_accepted_in_supplied_records"])
            self.assertEqual(row["unresolved_restrictions"], ["E4"])
            request["progress"][-1]["result_binding"]["id"] = "independent-object"
            self.assertTrue(self.row(request)["scope_accepted_in_supplied_records"])

    def test_unknown_original_readback_reconciles_records_not_execution_permission(self):
        request = view_request()
        binding = request["tasks"]["tasks"][0]["result_binding"]
        request["progress"].append(event(4, "effect_unknown", result="UNKNOWN", result_binding=binding))
        request["progress"].append(event(5, "original_effect_reconciled", actor="demo:owner", result="committed",
                                          related_event_id="E4", result_binding=binding))
        row = self.row(request)
        self.assertTrue(row["scope_accepted_in_supplied_records"])
        self.assertEqual(row["recorded_failures"], ["E4"])
        self.assertEqual(row["recorded_restrictions"], ["E4"])

    def test_unsupported_arguments_and_bad_shapes_do_not_open_action_routes(self):
        for request in (None, [], {"operation": "execute"}, {"operation": "commit", "send": "target"}):
            self.assertEqual(files.handle(request)["status"], "invalid_record")
        request = view_request(); request["agreements"]["agreements"][0]["status"] = "superseded"
        self.assertFalse(self.row(request)["scope_accepted_in_supplied_records"])

    def test_later_same_scope_failure_reopens_only_its_criteria(self):
        request = view_request()
        request["progress"].append(event(4, "goal_verification_fail", result="FAIL", covers=["usable"],
            result_binding=request["tasks"]["tasks"][0]["result_binding"]))
        row = self.row(request)
        self.assertFalse(row["scope_accepted_in_supplied_records"])
        self.assertEqual(row["later_effect_failures"], {"usable": ["E4"]})

    def test_typed_scope_stage_and_version_applicability_cannot_be_inferred(self):
        for field in ("covers", "effect_event_ids", "closes"):
            request = view_request()
            request["progress"][0 if field == "covers" else 1][field] = "usable"
            self.assertEqual(files.handle(request)["status"], "invalid_record")
        request = view_request()
        request["agreements"]["agreements"][0]["applies_to"].update(stages=["delivery"], product_versions=["1.0.0"])
        self.assertFalse(self.row(request)["scope_accepted_in_supplied_records"])
        request["tasks"]["tasks"][0]["scope"]["stage"] = "delivery"
        self.assertTrue(self.row(request)["scope_accepted_in_supplied_records"])

    def test_source_read_failure_marks_only_named_consumer_unknown_not_business_fail(self):
        request = view_request()
        request["source_read_gaps"] = [{"task_id": "other-task", "source_ref": "demo:unreachable", "status": "read_unavailable"}]
        self.assertTrue(self.row(request)["scope_accepted_in_supplied_records"])
        request["source_read_gaps"][0]["task_id"] = "T"
        row = self.row(request)
        self.assertFalse(row["scope_accepted_in_supplied_records"])
        self.assertEqual(row["recorded_failures"], [])
        self.assertIn("source_read_gap:demo:unreachable", row["gaps"])

    def test_acceptance_before_or_after_failure_cannot_refresh_old_effect(self):
        for failure_before_first_acceptance in (True, False):
            for result in ("FAIL", "UNKNOWN"):
                request = view_request()
                binding = request["tasks"]["tasks"][0]["result_binding"]
                if failure_before_first_acceptance:
                    request["progress"] = [request["progress"][0],
                        event(2, "goal_verification_fail", result=result, covers=["usable"], result_binding=binding),
                        event(3, "delivery_accepted", actor="demo:owner", decision_ref="demo:reaccept",
                              effect_event_ids=["E1"], closes=["usable"])]
                else:
                    request["progress"] = request["progress"][:2] + [
                        event(3, "goal_verification_fail", result=result, covers=["usable"], result_binding=binding),
                        event(4, "delivery_accepted", actor="demo:owner", decision_ref="demo:reaccept",
                              effect_event_ids=["E1"], closes=["usable"])]
                row = self.row(request)
                self.assertFalse(row["scope_accepted_in_supplied_records"])
                self.assertEqual(row["acceptance_effects"], {"usable": "E1"})
                self.assertTrue(row["later_effect_failures"]["usable"])

    def test_renewed_effect_closes_affected_criterion_and_retains_other_acceptance(self):
        request = view_request()
        request["tasks"]["tasks"][0]["acceptance"].append({"id": "receipt", "criterion_ref": "demo:receipt"})
        request["progress"] = request["progress"][:2]
        request["progress"][0]["covers"].append("receipt")
        request["progress"][1]["closes"].append("receipt")
        binding = request["tasks"]["tasks"][0]["result_binding"]
        request["progress"].append(event(3, "goal_verification_fail", result="FAIL", covers=["usable"], result_binding=binding))
        self.assertEqual(self.row(request)["pending_acceptance"], ["usable"])
        request["progress"].extend([
            event(4, "goal_verification_pass", result="PASS", covers=["usable"], result_binding=binding),
            event(5, "delivery_accepted", actor="demo:owner", decision_ref="demo:new-effect-accepted",
                  effect_event_ids=["E1", "E4"], closes=["usable"])])
        row = self.row(request)
        self.assertTrue(row["scope_accepted_in_supplied_records"])
        self.assertEqual(row["acceptance_effects"], {"usable": "E4", "receipt": "E1"})
        self.assertEqual(row["recorded_failures"], ["E3"])

    def test_public_cli_does_not_accept_repeated_ack_of_contradicted_pass(self):
        request = view_request()
        request["progress"] = request["progress"][:2] + [
            event(3, "goal_verification_fail", result="FAIL", covers=["usable"],
                  result_binding=request["tasks"]["tasks"][0]["result_binding"]),
            event(4, "delivery_accepted", actor="demo:owner", decision_ref="demo:repeat-ack",
                  effect_event_ids=["E1"], closes=["usable"])]
        result = subprocess.run([sys.executable, "-B", str(ROOT / "scripts/codex_framework.py"), "coordination"],
                                input=json.dumps(request), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(json.loads(result.stdout)["tasks"][0]["scope_accepted_in_supplied_records"])

    def test_write_scope_change_requires_new_input_and_preserves_original_scope(self):
        document, path = self.seed("tasks")
        original = copy.deepcopy(document["tasks"][0])
        document.update(revision=2, change_id="scope-change")
        row = document["tasks"][0]
        row.update(plan_revision=2, plan_changes=[{"revision": 2, "input_revision": 1,
                   "kind": "internal_implementation_change", "source_ref": "demo:internal-adjustment"}])
        row["write_scope"].append("demo:new-destination")
        request = self.request("tasks", expected_revision=1, change_id="scope-change", document=document)
        before = path.read_bytes()
        self.assertEqual(files.handle(request)["status"], "invalid_record")
        self.assertEqual(path.read_bytes(), before)
        row["input_revision"] = 2
        row["plan_changes"][-1].update(input_revision=2, kind="requirement_change", source_ref="demo:new-scope-source")
        row["input_history"] = [{k: original[k] for k in (*files.REQUIREMENT_FIELDS, "input_revision")}]
        self.assertEqual(files.handle(request)["status"], "committed")
        readback = json.loads(path.read_bytes())["tasks"][0]
        self.assertEqual(readback["input_history"][0]["write_scope"], original["write_scope"])

    def test_unknown_task_extension_cannot_be_removed_or_changed(self):
        original = task_document()
        original["tasks"][0]["domain_metadata"] = {"pending_owner_obligation": "demo:retain", "nested": {"key": "value"}}
        path = self.root / "tasks.json"
        for edit in ("delete", "change_nested"):
            path.write_bytes(files.encoded(original))
            document = copy.deepcopy(original)
            document.update(revision=2, change_id="plan")
            row = document["tasks"][0]
            row.update(plan_revision=2, plan_changes=[{"revision": 2, "input_revision": 1,
                       "kind": "internal_implementation_change", "source_ref": "demo:parameter"}])
            if edit == "delete":
                row.pop("domain_metadata")
            else:
                row["domain_metadata"]["nested"]["key"] = "replacement"
            before = path.read_bytes()
            request = self.request("tasks", expected_revision=1, change_id="plan", document=document)
            self.assertEqual(files.handle(request)["status"], "invalid_record")
            self.assertEqual(path.read_bytes(), before)
        document = copy.deepcopy(original)
        document.update(revision=2, change_id="valid-plan")
        document["tasks"][0].update(plan_revision=2, plan_changes=[{"revision": 2, "input_revision": 1,
            "kind": "internal_implementation_change", "source_ref": "demo:parameter"}])
        document["tasks"][0]["execution"]["parameter"] = "smaller-batch"
        self.assertEqual(files.handle(self.request("tasks", expected_revision=1, change_id="valid-plan", document=document))["status"], "committed")
        self.assertEqual(json.loads(path.read_bytes())["tasks"][0]["domain_metadata"], original["tasks"][0]["domain_metadata"])

    def test_only_lf_separates_events_and_crlf_history_keeps_original_bytes(self):
        path = self.root / "progress.jsonl"
        raw = files.encoded(event(1)) + b"\r" + files.encoded(event(2)) + b"\n"
        path.write_bytes(raw)
        self.assertNotEqual(files.handle(self.request("progress", "read"))["status"], "read")
        self.assertNotEqual(self.append(event(3), 2)["status"], "committed")
        self.assertEqual(path.read_bytes(), raw)
        crlf = files.encoded(event(1)) + b"\r\n"
        path.write_bytes(crlf)
        self.assertEqual(files.handle(self.request("progress", "read"))["last_seq"], 1)
        self.assertEqual(self.append(event(2), 1)["status"], "committed")
        self.assertTrue(path.read_bytes().startswith(crlf))
        self.assertEqual(self.append(event(2), 1)["status"], "duplicate_reused")
        self.assertEqual(self.append(event(1), 0)["status"], "event_conflict")


class CoordinationInstalledTests(FixtureCase):
    def test_new_cli_module_and_docs_load_from_owned_runtime_without_task_store(self):
        revision = seed_runtime(self.repo)
        runtime.install(self.a, revision)
        self.assertIn("coordination_files.py", runtime.MODULES)
        payload = view_request()
        result = subprocess.run([sys.executable, "-B", str(runtime.launcher_path(self.a)),
                                "--home", str(self.a.home), "--codex-home", str(self.a.codex_home), "coordination"],
                                input=json.dumps(payload), text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(json.loads(result.stdout)["tasks"][0]["scope_accepted_in_supplied_records"])
        self.assertEqual(json.loads(result.stdout)["tool_revision"], revision)
        self.assertEqual(json.loads(result.stdout)["tool_version"], cf.RELEASE_VERSION)
        self.assertFalse((self.a.codex_home / "task-runtime").exists())
        self.assertTrue(self.a.target(".agents/skills/york-task-alignment/references/coordination-file-contract.md").exists())

    def test_previous_twelve_module_runtime_remains_supported(self):
        path = self.repo / "scripts/framework_runtime.py"
        path.write_text(path.read_text().replace(', "coordination_files.py")', ')', 1))
        revision = seed_runtime(self.repo)
        runtime.install(self.a, revision)
        result = subprocess.run([sys.executable, "-B", str(runtime.launcher_path(self.a)),
                                "--home", str(self.a.home), "--codex-home", str(self.a.codex_home), "version"],
                                text=True, capture_output=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout)["runtime"]["active_revision"], revision)
