import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
from obligation_projection import (ProjectionError, READ_INPUT_BYTES, READ_OUTPUT_BYTES,
                                   encoded, load_json, project, read_context, resolve)


class ObligationProjectionTests(unittest.TestCase):
    def sample(self):
        return {"frontier": {}, "workstreams": [{"id": "A", "state": "OLD_IN_PROGRESS",
            "latest_native": {"turn": "new", "status": "completed"},
            "acceptance": {"original_turn": "old", "status": "not_run"},
            "delivery": {"sent": True, "received": False},
            "remaining": ["original goal"]}, {"id": "B", "state": "pending",
            "next_action": {"scope": "independent existing review", "input_ref": "local:held"}}]}

    def test_singular_plural_and_pointer_aliases_retain_every_origin(self):
        d = self.sample(); d["frontier"] = {"assigned_ref": "#/workstreams[id=A]",
            "assigned_refs": ["#/workstreams/0", "#/workstreams[id=B]", "#/workstreams[id=A]"]}
        out = project(d, "#/frontier", ["assigned"])
        self.assertEqual(out["status"], "ok")
        self.assertEqual(len(out["obligations"]), 2)
        self.assertEqual(len(out["obligations"][0]["origins"]), 3)
        self.assertEqual(out["obligations"][0]["resolved_pointer"], "#/workstreams/0")

    def test_plural_only_original_default_assigned_family_is_not_lost(self):
        d = self.sample(); d["frontier"]["assigned_inflight_do_not_duplicate_refs"] = [
            "#/workstreams[id=A]", "#/workstreams[id=B]"]
        self.assertEqual(len(project(d, "#/frontier")["obligations"]), 2)

    def test_invalid_refs_and_shapes_are_local_not_global_wait(self):
        d = self.sample(); d["frontier"] = {"x_ref": [], "x_refs": [None, "", "#bad", "#/missing", "#/workstreams/1"]}
        out = project(d, "#/frontier", ["x"])
        self.assertEqual(out["status"], "partial")
        self.assertEqual(len(out["issues"]), 5)
        self.assertEqual(out["obligations"][0]["value"], d["workstreams"][1])
        d["frontier"] = {"x_ref": "#/workstreams/0", "x_refs": "#/workstreams/1"}
        self.assertEqual(len(project(d, "#/frontier", ["x"])["obligations"]), 1)

    def test_null_empty_families_do_not_claim_ready_or_goal_exhaustion(self):
        d = {"frontier": {"x_ref": None, "x_refs": []}}
        out = project(d, "#/frontier", ["x"])
        self.assertEqual(out["obligations"], [])
        self.assertEqual(out["interpretation"]["readiness"], "not_evaluated")
        self.assertNotIn("overall_complete", out)

    def test_json_pointer_escapes_fragment_unicode_and_literal_selector_key(self):
        d = {"a/b": {"~ key": 7}, "workstreams[id=A]": "literal"}
        self.assertEqual(resolve(d, "#/a~1b/~0%20key"), (7, "/a~1b/~0 key"))
        self.assertEqual(resolve(d, "/workstreams[id=A]")[0], "literal")
        self.assertEqual(resolve(d, "#")[0], d)
        for ref in ("#/a~2b", "#/x%2", "#/x%FF", "elsewhere.json#/x"):
            with self.subTest(ref=ref), self.assertRaises(ProjectionError): resolve(d, ref)

    def test_exact_unique_selector_and_canonical_array_index(self):
        d = {"rows": [{"id": "a"}, {"id": "ab"}, {"id": 7}]}
        self.assertEqual(resolve(d, "#/rows[id=a]")[1], "/rows/0")
        for ref in ("#/rows[id=7]", "#/rows[id=missing]", "#/rows/01", "#/rows/-1", "#/rows/" + "9" * 5000):
            with self.subTest(ref=ref), self.assertRaises(ProjectionError): resolve(d, ref)
        d["rows"].append({"id": "a"})
        with self.assertRaises(ProjectionError) as ctx: resolve(d, "#/rows[id=a]")
        self.assertEqual(ctx.exception.code, "ambiguous_id")

    def test_emitted_fragments_roundtrip_percent_hash_unicode_and_source_origins(self):
        d = {"f%/架": {"x_refs": ["/key%literal #", "#/key%25literal%20%23"]},
             "key%literal #": {"state": "pending"}}
        out = project(d, "/f%~1架", ["x"])
        self.assertEqual(len(out["obligations"]), 1)
        row = out["obligations"][0]
        self.assertEqual(resolve(d, row["resolved_pointer"])[0], row["value"])
        self.assertEqual(resolve(d, out["frontier_pointer"])[0], d["f%/架"])
        for origin in row["origins"]:
            self.assertEqual(resolve(d, origin["source_pointer"])[0], origin["ref"])

    def test_native_completion_does_not_replace_original_acceptance_or_delivery(self):
        d = self.sample(); d["frontier"]["x_ref"] = "#/workstreams/0"
        row = project(d, "#/frontier", ["x"])["obligations"][0]
        self.assertEqual(row["value"], d["workstreams"][0])
        self.assertEqual(row["value"]["acceptance"]["status"], "not_run")
        self.assertFalse(row["value"]["delivery"]["received"])
        self.assertNotIn("ready", row)

    def test_refused_a_and_independent_b_both_remain_visible(self):
        d = self.sample(); d["workstreams"][0]["state"] = "platform_denied"
        d["frontier"]["x_refs"] = ["#/workstreams/0", "#/workstreams/1"]
        out = project(d, "#/frontier", ["x"])
        self.assertEqual([r["value"]["id"] for r in out["obligations"]], ["A", "B"])
        self.assertEqual(out["obligations"][0]["value"]["state"], "platform_denied")
        self.assertEqual(out["interpretation"]["authorization"], "not_evaluated")
        self.assertNotIn("wait_allowed", out)

    def test_projection_retains_values_without_mutating_source(self):
        d = self.sample(); d["frontier"]["x_ref"] = "#/workstreams/0"
        before = copy.deepcopy(d);out = project(d, "#/frontier", ["x"])
        out["obligations"][0]["value"]["acceptance"]["status"] = "changed by caller"
        self.assertEqual(d, before)

    def test_same_projection_can_be_reused_after_notification_or_fallback(self):
        d = self.sample(); d["frontier"]["x_refs"] = ["#/workstreams/0", "#/workstreams/0"]
        first = project(d, "#/frontier", ["x"])
        repeated = project(d, "#/frontier", ["x"])
        self.assertEqual(first, repeated)
        self.assertEqual(len(repeated["obligations"]), 1)
        self.assertEqual(repeated["obligations"][0]["value"]["remaining"], ["original goal"])
        self.assertEqual(repeated["interpretation"]["native_current_status"], "not_queried")
        self.assertNotIn("notification_received", repeated)

    def test_duplicate_keys_nonfinite_and_bad_json_are_explicit_invalid(self):
        for raw in (b'{"x":1,"x":2}', b'{"x":NaN}', b'{', b'\xff'):
            with self.subTest(raw=raw), self.assertRaises(ProjectionError): load_json(raw)

    def test_normal_cli_partial_and_invalid_keep_structured_io_and_source_unchanged(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "document.json";d = self.sample()
            d["frontier"]["x_refs"] = ["#/workstreams/0", "#/absent", "#/workstreams/1"]
            raw = json.dumps(d).encode();path.write_bytes(raw)
            argv = [sys.executable, str(SCRIPTS / "obligation_projection.py"), str(path), "--frontier", "#/frontier", "--family", "x"]
            p = subprocess.run(argv, text=True, capture_output=True)
            out = json.loads(p.stdout);self.assertEqual((p.returncode, p.stderr), (2, ""))
            self.assertEqual(len(out["obligations"]), 2)
            self.assertEqual(out["source"]["sha256"], hashlib.sha256(raw).hexdigest())
            self.assertEqual(path.read_bytes(), raw)
            path.write_bytes(b'{')
            p = subprocess.run(argv, text=True, capture_output=True)
            self.assertEqual((p.returncode, p.stderr, json.loads(p.stdout)["status"]), (1, "", "invalid"))


class ContextReadTests(unittest.TestCase):
    def source(self, document, path="/authorized/document.json"):
        raw = encoded(document).encode("ascii")
        return {"path": path, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}

    def read(self, document, ref="#", **options):
        return read_context(document, ref, self.source(document), **options)

    def test_container_lists_references_without_disclosing_descendant_values(self):
        d = {"tasks": [{"id": "A", "state": "denied", "body": "DO_NOT_DISCLOSE" * 5000}]}
        before = copy.deepcopy(d)
        out = self.read(d, "#/tasks")
        self.assertEqual(out["content_mode"], "child_descriptors")
        self.assertEqual(out["items"][0]["pointer"], "#/tasks/0")
        self.assertEqual(out["items"][0]["type"], "object")
        self.assertNotIn("DO_NOT_DISCLOSE", encoded(out))
        self.assertNotIn("value", out["items"][0])
        self.assertFalse(out["descendants_included"])
        self.assertEqual(self.read(d, "#/tasks/0/state")["text"], "denied")
        self.assertEqual(out["interpretation"]["receipt"], "not_recorded")
        self.assertEqual(d, before)

    def test_all_pages_are_exactly_once_with_escaped_pointers(self):
        d = {"items": {"a/b": 1, "~x": 2, "架#%": 3}}
        pages = []; cursor = None
        while True:
            out = self.read(d, "#/items", page_size=1, cursor=cursor)
            pages.extend(out["items"])
            cursor = out["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(len(pages), 3)
        self.assertEqual([resolve(d, item["pointer"])[0] for item in pages], [1, 2, 3])
        self.assertEqual(self.read(d, "#/items/a~1b")["value"], 1)

    def test_cursor_rejects_source_change_pointer_change_and_different_file(self):
        d = {"a": [1, 2], "b": [1, 2]}
        cursor = self.read(d, "#/a", page_size=1)["next_cursor"]
        changed = copy.deepcopy(d); changed["b"].append(3)
        cases = [(changed, "#/a", self.source(changed)),
                 (d, "#/b", self.source(d)),
                 (d, "#/a", self.source(d, "/other/document.json"))]
        for document, ref, source in cases:
            with self.subTest(ref=ref, source=source), self.assertRaises(ProjectionError) as caught:
                read_context(document, ref, source, cursor=cursor)
            self.assertEqual(caught.exception.code, "stale_cursor")

    def test_unchanged_selected_value_does_not_claim_whole_source_unchanged(self):
        d = {"selected": {"a": 1, "b": 2}, "other": "old"}
        first = self.read(d, "#/selected")
        d = {"selected": {"b": 2, "a": 1}, "other": "new"}
        second = self.read(d, "#/selected", if_value_sha256=first["value_sha256"])
        self.assertEqual(second["status"], "unchanged")
        self.assertNotIn("items", second)
        self.assertNotEqual(first["source"]["sha256"], second["source"]["sha256"])
        d["selected"]["a"] = 3
        self.assertEqual(self.read(d, "#/selected", if_value_sha256=first["value_sha256"])["status"], "ok")

    def test_text_pages_preserve_unicode_and_remain_within_output_bound(self):
        d = {"text": ("中文😀\n" * 1001) + "tail"}
        pieces = []; cursor = None
        while True:
            out = self.read(d, "#/text", cursor=cursor)
            self.assertLessEqual(len(encoded(out).encode("ascii")) + 1, READ_OUTPUT_BYTES)
            pieces.append(out["text"])
            cursor = out["next_cursor"]
            if cursor is None:
                break
        self.assertEqual("".join(pieces), d["text"])

    def test_byte_budget_shortens_card_page_without_silently_dropping_entries(self):
        d = {(str(i) + "键" * 200): i for i in range(40)}
        seen = []; cursor = None
        while True:
            out = self.read(d, page_size=50, cursor=cursor)
            self.assertLessEqual(len(encoded(out).encode("ascii")) + 1, READ_OUTPUT_BYTES)
            seen.extend(resolve(d, row["pointer"])[0] for row in out["items"])
            cursor = out["next_cursor"]
            if cursor is None:
                break
        self.assertEqual(seen, list(range(40)))
        with self.assertRaises(ProjectionError) as caught:
            self.read({"key" * READ_OUTPUT_BYTES: 1})
        self.assertEqual(caught.exception.code, "entry_too_large")

    def test_empty_values_scalar_types_and_bad_options(self):
        for value in (None, True, False, 3, 2.5):
            self.assertEqual(self.read(value)["value"], value)
        for value in ([], {}):
            out = self.read(value)
            self.assertEqual((out["items"], out["next_cursor"]), ([], None))
        self.assertEqual(self.read("")["text"], "")
        for kwargs in ({"page_size": 0}, {"page_size": 51}, {"page_size": True},
                       {"if_value_sha256": "bad"}, {"cursor": "bad"},
                       {"cursor": "bad", "if_value_sha256": "a" * 64}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ProjectionError):
                self.read({}, **kwargs)

    def test_cli_input_bound_and_no_mutation_or_descendant_disclosure(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "source.json"
            raw = json.dumps({"item": {"private": "PRIVATE_MARKER" * 2000}}).encode()
            path.write_bytes(raw)
            command = [sys.executable, str(SCRIPTS / "obligation_projection.py"), str(path), "--read", "#"]
            result = subprocess.run(command, capture_output=True)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertLessEqual(len(result.stdout), READ_OUTPUT_BYTES)
            self.assertNotIn(b"PRIVATE_MARKER", result.stdout)
            self.assertEqual(path.read_bytes(), raw)
            path.write_bytes(b" " * (READ_INPUT_BYTES + 1))
            result = subprocess.run(command, capture_output=True)
            self.assertEqual((result.returncode, json.loads(result.stdout)["code"]), (1, "input_too_large"))
            self.assertLessEqual(len(result.stdout), READ_OUTPUT_BYTES)
            path.write_bytes(b'{"a":1,"a":2}')
            result = subprocess.run(command, capture_output=True)
            self.assertEqual((result.returncode, json.loads(result.stdout)["code"]), (1, "invalid_json"))


if __name__ == "__main__":
    unittest.main()
