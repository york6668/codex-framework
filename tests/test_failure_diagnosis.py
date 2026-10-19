"""Run bounded regression cases against a selected framework checkout.

Native snapshots are constructed test inputs, never authorization evidence.
The file probes perform real temporary filesystem operations. No model, API,
Docker, business data or task dispatch is used.
"""
import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


SOURCE = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(SOURCE / "scripts"), str(SOURCE / "tests")]
sys.dont_write_bytecode = True
import source_evidence as sources
import execution_check as check
import test_execution_check as fixtures


def command_item(item_id="run:one", body="observed"):
    return {"id": item_id, "type": "commandExecution", "status": "completed",
            "exitCode": 0, "command": "bounded evidence probe",
            "output": {"text": body, "truncated": False}}


def snapshot(items, order=None):
    result = {"schemaVersion": 1, "thread": {"id": "framework-task"},
              "turns": [{"id": "turn:one", "items": items}]}
    if order:
        result["page"] = {"order": order}
    return result


def record(item_id="run:one", result="observed"):
    return {"source": "codex", "kind": "observation", "task_ref": "codex:thread:framework-task",
            "native_item_id": item_id, "digest": hashlib.sha256(b"observed").hexdigest(),
            "result": result}


def parent_grant_case():
    request = fixtures.WorkspaceBoundaryChecks().local_case()
    request["task"]["native_ref"] = request["context"]["task_ref"] = "codex:thread:child-task"
    return request


class FailureDiagnosisChecks(unittest.TestCase):
    def test_conflicting_native_observations_do_not_choose_first_success(self):
        first = command_item()
        second = copy.deepcopy(first)
        second["exitCode"] = 1
        for entries in ((first, second), (second, first)):
            with self.subTest(first_exit=entries[0]["exitCode"]):
                context = {"native_sources": [snapshot([item]) for item in entries]}
                self.assertFalse(sources.verify(context, {}, record()))

    def test_identical_overlapping_pages_remain_usable(self):
        page = snapshot([command_item()])
        self.assertTrue(sources.verify({"native_sources": [page, copy.deepcopy(page)]}, {}, record()))

    def test_contradictory_native_order_does_not_prove_review_after_result(self):
        a, b = command_item("a"), command_item("b")
        context = {"native_sources": [snapshot([a, b]), snapshot([b, a])]}
        earlier, later = record("a"), record("b")
        self.assertFalse(sources.native_precedes(context, earlier, later))

    def test_missing_conflicting_and_symlink_loop_files_are_local_gaps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            target = root / "loop"
            target.symlink_to(target)
            for path in ("missing.json", "loop"):
                with self.subTest(path=path):
                    node = {"source": "node", "node_path": path, "digest": "0" * 64}
                    self.assertFalse(sources.verify({}, {"root": directory}, node))

    def test_bad_node_path_does_not_abort_independent_cli_step(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "loop").symlink_to(root / "loop")
            request = fixtures.action_case()
            request["project"]["root"] = directory
            request["context"]["evidence"] = [{"ref": "node:premise:broken", "source": "node",
                "kind": "premise", "project_id": "framework", "task_ref": request["task"]["native_ref"],
                "node_path": "loop", "digest": "0" * 64, "epistemic_status": "confirmed",
                "resource": request["action"]["resource"]}]
            write = copy.deepcopy(request["action"])
            write["basis_refs"] = ["node:premise:broken"]
            read = {"kind": "read", "resource": request["action"]["resource"]}
            request.update(phase="progress", progress={"checkpoint": "after_failure", "claim": "working",
                "steps": [{"id": "affected", "action": write}, {"id": "independent", "action": read}]})
            actual = subprocess.run([sys.executable, str(SOURCE / "scripts/codex_framework.py"), "check"],
                input=json.dumps(request), capture_output=True, text=True, timeout=10)
            self.assertEqual(actual.stderr, "")
            output = json.loads(actual.stdout)
            self.assertIn("independent", output["work_partition"]["ready_step_ids"])
            self.assertIn("affected", output["work_partition"]["unresolved_step_ids"])
            self.assertIn("decision_basis_source_unverified", output["steps"][0]["missing"])

    def test_boolean_exit_is_not_evidence_of_execution(self):
        item = command_item()
        item["exitCode"] = False
        self.assertFalse(sources.verify({"native_sources": [snapshot([item])]}, {}, record()))

    def test_node_outcome_uses_the_same_bytes_as_its_digest(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            original = json.dumps({"result": "failed", "version": "one"}).encode()
            path.write_bytes(original)
            evidence = {"source": "node", "node_path": "report.json", "result": "passed",
                        "version": "one", "digest": hashlib.sha256(original).hexdigest()}
            read_bytes = sources._node_bytes
            changed = []
            def changed_after_read(base, selected):
                value = read_bytes(base, selected)
                path.write_text(json.dumps({"result": "passed", "version": "one"}))
                changed.append(True)
                return value
            with mock.patch.object(sources, "_node_bytes", changed_after_read):
                self.assertFalse(sources.verify({}, {"root": directory}, evidence))
            self.assertTrue(changed)

    def test_ambiguous_json_and_unexecuted_verified_labels_are_not_passes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "report.json"
            for body, outcome in [('{"result":"failed","result":"passed","version":"one"}', "passed"),
                                  ('{"result":"failed","version":"one"}', "verified")]:
                with self.subTest(outcome=outcome):
                    path.write_text(body)
                    evidence = {"source": "node", "node_path": "report.json", "result": outcome,
                        "version": "one", "digest": hashlib.sha256(path.read_bytes()).hexdigest()}
                    self.assertFalse(sources.verify({}, {"root": directory}, evidence))

    def test_verified_parent_source_reports_link_gap_without_granting_child(self):
        request = parent_grant_case()
        result = check.evaluate(request)
        self.assertEqual(result["status"], "needs_evidence")
        self.assertIn("unresolved_authorization_reference", result["missing"])
        self.assertIn("upstream_authorization_link_required", result["missing"])
        self.assertNotIn("local_action_authorization", result["missing"])

    def test_source_diagnostic_never_clears_denial_or_upgrades_agent_quote(self):
        request = parent_grant_case()
        request["context"]["tool_failure"] = {"kind": "platform_denied", "action_kind": "write",
            "resource": request["action"]["resource"]}
        self.assertIn("platform_denied_action", check.evaluate(request)["blocked"])
        request["context"].pop("tool_failure")
        request["context"]["native_sources"][0]["turns"][0]["items"][0]["type"] = "agentMessage"
        result = check.evaluate(request)
        self.assertNotIn("upstream_authorization_link_required", result["missing"])
        self.assertNotEqual(result["status"], "ready")

    def test_node_paths_are_relative_and_identity_errors_do_not_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "result.json"
            path.write_text('{"result":"passed","version":"one"}')
            record = {"source":"node", "node_path":"result.json", "result":"passed",
                      "version":"one", "digest":hashlib.sha256(path.read_bytes()).hexdigest()}
            self.assertTrue(sources.verify({}, {"root": directory}, record))
            for selected in (str(path), "../result.json", "./result.json", "bad\x00path", "a//b", "C:/result.json"):
                with self.subTest(path=selected):
                    self.assertFalse(sources.verify({}, {"root": directory}, {**record, "node_path": selected}))

    def test_malformed_native_body_and_typed_conflicts_do_not_become_execution(self):
        first = command_item()
        bad = copy.deepcopy(first)
        bad["output"]["text"] = ["observed"]
        self.assertFalse(sources.verify({"native_sources": [snapshot([bad])]}, {}, record()))
        bad = copy.deepcopy(first)
        bad["exitCode"] = False
        for entries in ((first, bad), (bad, first)):
            self.assertFalse(sources.verify({"native_sources": [snapshot([i]) for i in entries]}, {}, record()))

    def test_failed_exit_requires_an_integer_or_native_failed_state(self):
        for code, status, expected in [(1, "completed", True), (True, "completed", False),
                                      (False, "failed", False), ("1", "completed", False),
                                      (None, "completed", False), (None, "failed", True),
                                      (0, "failed", False), (1, "running", False), (1, None, False)]:
            with self.subTest(code=code, status=status):
                item = command_item()
                item.update(exitCode=code, status=status)
                self.assertEqual(sources.verify({"native_sources": [snapshot([item])]}, {},
                                                record(result="failed")), expected)

    def test_node_path_swap_is_rejected_at_stat_and_final_open_boundaries(self):
        for boundary in ("after_is_file", "before_open", "parent_before_open"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                own = Path(directory).resolve()
                root = own / "root"
                root.mkdir()
                nested = root / "nested"
                nested.mkdir()
                target = nested / "evidence.json"
                target.write_text('{"result":"failed","version":"one"}')
                outside = own / "outside" / "evidence.json"
                outside.parent.mkdir()
                outside.write_text('{"result":"passed","version":"one"}')
                evidence = {"source": "node", "node_path": "nested/evidence.json", "result": "passed",
                    "version": "one", "digest": hashlib.sha256(outside.read_bytes()).hexdigest()}
                changed = []
                original_is_file, original_open = Path.is_file, os.open

                def replace():
                    if boundary == "parent_before_open":
                        nested.rename(root / "kept")
                        nested.symlink_to(outside.parent, target_is_directory=True)
                    else:
                        target.unlink()
                        target.symlink_to(outside)
                    changed.append(True)

                def after_stat(path):
                    valid = original_is_file(path)
                    if path == target and not changed:
                        replace()
                    return valid

                def before_open(path, flags, *args, **kwargs):
                    wanted = "nested" if boundary == "parent_before_open" else "evidence.json"
                    if path == wanted and kwargs.get("dir_fd") is not None and not changed:
                        replace()
                    return original_open(path, flags, *args, **kwargs)

                with mock.patch.object(Path, "is_file", after_stat if boundary == "after_is_file" else original_is_file), \
                        mock.patch.object(os, "open", original_open if boundary == "after_is_file" else before_open):
                    self.assertFalse(sources.verify({}, {"root": str(root)}, evidence))
                self.assertTrue(changed)

    def test_node_root_ancestor_swap_cannot_redirect_a_checked_file(self):
        for boundary in ("after_is_file", "before_ancestor_open", "before_root_open", "after_root_open"):
            with self.subTest(boundary=boundary), tempfile.TemporaryDirectory() as directory:
                own = Path(directory).resolve()
                anchor = own / "anchor"
                root = anchor / "root"
                root.mkdir(parents=True)
                target = root / "evidence.json"
                target.write_text('{"result":"failed","version":"one"}')
                external = own / "external"
                outside = external / "root" / "evidence.json"
                outside.parent.mkdir(parents=True)
                outside.write_text('{"result":"passed","version":"one"}')
                evidence = {"source": "node", "node_path": "evidence.json", "result": "passed",
                    "version": "one", "digest": hashlib.sha256(outside.read_bytes()).hexdigest()}
                changed = []
                original_is_file, original_open = Path.is_file, os.open

                def replace():
                    anchor.rename(own / "kept-anchor")
                    anchor.symlink_to(external, target_is_directory=True)
                    changed.append(True)

                def after_stat(path):
                    valid = original_is_file(path)
                    if path == target and not changed:
                        replace()
                    return valid

                def around_open(path, flags, *args, **kwargs):
                    relative = kwargs.get("dir_fd") is not None
                    wanted = "anchor" if boundary == "before_ancestor_open" else "root"
                    if relative and path == wanted and boundary != "after_root_open" and not changed:
                        replace()
                    descriptor = original_open(path, flags, *args, **kwargs)
                    if relative and path == "root" and boundary == "after_root_open" and not changed:
                        replace()
                    return descriptor

                with mock.patch.object(Path, "is_file", after_stat if boundary == "after_is_file" else original_is_file), \
                        mock.patch.object(os, "open", original_open if boundary == "after_is_file" else around_open):
                    self.assertFalse(sources.verify({}, {"root": str(root)}, evidence))
                self.assertTrue(changed)
                self.assertEqual(target.resolve(), outside)

    def test_node_root_and_contained_aliases_remain_usable(self):
        with tempfile.TemporaryDirectory() as directory:
            own = Path(directory)
            root = own / "root"
            nested = root / "nested"
            nested.mkdir(parents=True)
            target = nested / "evidence.json"
            target.write_text('{"result":"passed","version":"one"}')
            (root / "alias").symlink_to(nested, target_is_directory=True)
            (own / "root-alias").symlink_to(root, target_is_directory=True)
            evidence = {"source": "node", "result": "passed", "version": "one",
                "digest": hashlib.sha256(target.read_bytes()).hexdigest()}
            for selected_root in (root, own / "root-alias"):
                for path in ("nested/evidence.json", "alias/evidence.json"):
                    with self.subTest(root=selected_root, path=path):
                        self.assertTrue(sources.verify({}, {"root": str(selected_root)},
                                                       {**evidence, "node_path": path}))


if __name__ == "__main__":
    unittest.main()
