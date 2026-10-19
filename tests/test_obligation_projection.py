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
from obligation_projection import ProjectionError, load_json, project, resolve


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


if __name__ == "__main__":
    unittest.main()
