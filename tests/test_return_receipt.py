import copy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import return_receipt as receipt


class ReturnReceiptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        report = self.root / "verdict.json"
        report.write_text('{"status":"limited original domain report"}')
        self.payload = "合并当前两个有限结果\n"
        self.expected = {
            "request": {"sender_thread": "parent", "target_thread": "owner",
                        "request_id": "input-A", "payload_utf8_sha256": hashlib.sha256(self.payload.encode()).hexdigest()},
            "results": [{
                "object_key": "writer", "report_ref": str(report),
                "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                "identities": [{"name": "adoption", "value": "distinct-adoption"}],
                "outcome": "limited", "summary": "源码采用，十二项通过😀",
                "limitations": ["未部署", "不代表真实库验收"],
                "next_scope": "原交易 owner 离线设计，不连接真实DB"
            }, {
                "object_key": "window", "report_ref": str(report),
                "report_sha256": hashlib.sha256(report.read_bytes()).hexdigest(),
                "identities": [{"name": "window", "value": "distinct-window"}],
                "outcome": "limited", "summary": "保存证据的有限观察",
                "limitations": ["健康记录缺口", "未证业务代表性", "4.75GiB不变，RAM净收益0"],
                "next_scope": "原资源 owner 仅审计已有材料，不重复窗口"
            }]
        }
        self.ledger = {"receipts": {}, "next": {}, "unrelated": {"pending": True}}
        self.bindings = []
        for row in self.expected["results"]:
            key = row["object_key"]
            value = {"evidence": {"verdict": {"path": str(report), "sha256": row["report_sha256"]}},
                     "scope": row["summary"], "limits": row["limitations"]}
            self.ledger["receipts"][key] = value
            self.ledger["next"][key] = row["next_scope"]
            self.bindings.append({
                "object_key": key, "receipt_pointer": "/receipts/" + key,
                "receipt_value": value, "report_pointer": "/receipts/" + key + "/evidence/verdict",
                "next_pointer": "/next/" + key, "next_value": row["next_scope"]})
        self.raw = json.dumps(self.ledger, ensure_ascii=False).encode()
        self.body = receipt.produce(self.expected, self.raw, self.bindings, payload=self.payload)
        self.observed = {"thread_id": "owner", "final_ref": "native-final-A",
                         "completed": True, "truncated": False, "text": self.body}

    def js(self, contracts=None, observation=None):
        node = shutil.which("node")
        self.assertIsNotNone(node, "Node is needed for actual pure-JS receiver verification")
        script = ("const fs=require('fs');const {receiveFinal}=require(process.argv[1]);"
                  "const d=JSON.parse(fs.readFileSync(0,'utf8'));"
                  "process.stdout.write(JSON.stringify(receiveFinal(d.contracts,d.observed)));")
        run = subprocess.run([node, "-e", script, str(ROOT/"scripts/return_receipt_receiver.js")],
                             input=json.dumps({"contracts": contracts or [self.expected],
                                               "observed": self.observed if observation is None else observation}),
                             capture_output=True, text=True, check=True)
        self.assertEqual(run.stderr, "")
        return json.loads(run.stdout)

    def test_same_committed_producer_to_actual_whole_body_receiver(self):
        result = self.js()
        self.assertEqual(result["return_acceptance"], "accepted")
        self.assertEqual(result["pending_request_refs"], [])
        self.assertEqual(json.loads(self.body)["results"], self.expected["results"])

    def test_successful_merge_with_old_final_rejects_only_return(self):
        observation = dict(self.observed, text="ab7 45入口/40输出对/104守卫")
        result = self.js(observation=observation)
        self.assertEqual(result["return_acceptance"], "rejected")
        self.assertTrue(result["preserve_completed_work"])
        self.assertTrue(result["independent_work_may_continue"])
        self.assertEqual(result["pending_request_refs"], ["input-A"])

    def test_correct_metadata_and_links_do_not_accept_old_or_extra_prose(self):
        for text in [self.body + "\n旧ab7通过",
                     "旧ab7通过\n" + self.body,
                     self.body + "\n" + "\n".join(r["report_ref"] for r in self.expected["results"])]:
            with self.subTest(text=text[-25:]):
                self.assertEqual(self.js(observation=dict(self.observed, text=text))["return_acceptance"], "rejected")

    def test_correct_ids_with_contradictory_current_summary_rejected(self):
        altered = json.loads(self.body)
        altered["results"][0]["summary"] = "old ab7 installation passed"
        self.assertEqual(self.js(observation=dict(self.observed, text=receipt.canonical(altered)))["reason"],
                         "complete_body_mismatch")

    def test_limits_and_next_owner_cannot_be_dropped(self):
        for field, replacement in [("limitations", ["all capacity accepted"]), ("next_scope", "run real SQL")]:
            altered = json.loads(self.body)
            altered["results"][1][field] = replacement
            self.assertEqual(self.js(observation=dict(self.observed, text=receipt.canonical(altered)))["return_acceptance"],
                             "rejected")

    def test_one_of_two_requested_results_is_not_complete(self):
        altered = json.loads(self.body)
        altered["results"].pop()
        self.assertEqual(self.js(observation=dict(self.observed, text=receipt.canonical(altered)))["return_acceptance"],
                         "rejected")

    def test_multiple_senders_do_not_silently_replace_known_pending_request(self):
        other = copy.deepcopy(self.expected)
        other["request"].update(sender_thread="other-owner", request_id="input-B")
        other["results"] = [dict(other["results"][0], summary="independent supplemental identity check")]
        observed = dict(self.observed, text=receipt.render(other, "c"*64))
        result = self.js([self.expected, other], observed)
        self.assertEqual(result["accepted_request_refs"], ["input-B"])
        self.assertEqual(result["pending_request_refs"], ["input-A"])

    def test_old_request_does_not_satisfy_independent_new_request(self):
        current = copy.deepcopy(self.expected)
        current["request"].update(request_id="new-feasibility", sender_thread="main")
        result = self.js([current])
        self.assertEqual(result["reason"], "current_known_request_not_returned")
        self.assertEqual(result["pending_request_refs"], ["new-feasibility"])

    def test_resume_reread_uses_same_pinned_contract_not_latest_table_topic(self):
        self.assertEqual(self.js()["return_acceptance"], "accepted")
        changed = json.loads(self.raw)
        changed["unrelated"]["pending"] = False
        body = receipt.produce(self.expected, json.dumps(changed).encode(), self.bindings, payload=self.payload)
        self.assertNotEqual(body, self.body)
        self.assertEqual(self.js(observation=dict(self.observed, final_ref="post-resume-final", text=body))["return_acceptance"],
                         "accepted")

    def test_unknown_native_evidence_and_wrong_source(self):
        for update in [{"truncated": True}, {"completed": False}, {"final_ref": ""}, {"text": None}]:
            self.assertEqual(self.js(observation=dict(self.observed, **update))["return_acceptance"], "unknown")
        self.assertEqual(self.js(observation=dict(self.observed, thread_id="unrelated"))["reason"],
                         "native_source_mismatch")

    def test_duplicate_fields_or_noncanonical_whole_body_rejected(self):
        for body in [self.body + "\n", " " + self.body,
                     self.body.replace('{"format":', '{"format":"forged","format":', 1)]:
            self.assertEqual(self.js(observation=dict(self.observed, text=body))["return_acceptance"], "rejected")

    def test_malformed_fields_are_structured_and_do_not_crash(self):
        for field, value in [("request", []), ("request", None), ("results", [{}]),
                             ("request", {"nested": [[[]]]})]:
            altered = json.loads(self.body)
            altered[field] = value
            self.assertEqual(self.js(observation=dict(self.observed, text=json.dumps(altered)))["return_acceptance"], "rejected")
        bad = copy.deepcopy(self.expected)
        bad["results"][0]["identities"] = ["bad"]
        self.assertEqual(self.js([bad])["return_acceptance"], "invalid")

    def test_parent_must_pre_pin_entire_meaning_not_only_report_tokens(self):
        incomplete = {"request": self.expected["request"], "results": [{"report_sha256": "a"*64}]}
        self.assertEqual(self.js([incomplete])["return_acceptance"], "invalid")

    def test_invalid_and_duplicate_expected_requests_do_not_accept(self):
        self.assertEqual(self.js([self.expected, self.expected])["return_acceptance"], "invalid")
        bad = copy.deepcopy(self.expected)
        bad["request"]["payload_utf8_sha256"] = "short"
        with self.assertRaises(receipt.ReceiptError):
            receipt.render(bad, "d"*64)

    def test_producer_checks_actual_committed_projection_and_report_identity(self):
        for kind in ["receipt", "next", "report"]:
            bad = copy.deepcopy(self.ledger)
            if kind == "receipt": bad["receipts"]["writer"]["scope"] = "changed"
            if kind == "next": bad["next"]["writer"] = "run live"
            if kind == "report": bad["receipts"]["writer"]["evidence"]["verdict"]["sha256"] = "f"*64
            with self.assertRaises(receipt.ReceiptError):
                receipt.produce(self.expected, json.dumps(bad).encode(), self.bindings, payload=self.payload)
        self.assertEqual(json.loads(self.raw), self.ledger)

    def test_producer_rejects_changed_original_report_without_writes(self):
        Path(self.expected["results"][0]["report_ref"]).write_text('{"status":"changed"}')
        with self.assertRaisesRegex(receipt.ReceiptError, "fingerprint"):
            receipt.produce(self.expected, self.raw, self.bindings, payload=self.payload)

    def test_producer_rejects_report_outside_selected_receipt_and_duplicate_binding(self):
        for bindings in [[self.bindings[0], self.bindings[0]],
                         [dict(self.bindings[0], report_pointer="/unrelated"), self.bindings[1]],
                         [dict(self.bindings[0], object_key=[]), self.bindings[1]]]:
            with self.assertRaises(receipt.ReceiptError):
                receipt.produce(self.expected, self.raw, bindings, payload=self.payload)

    def test_unknown_fields_do_not_become_free_text_escape_hatch(self):
        altered = json.loads(self.body)
        altered["commentary"] = "old ab7 passed"
        self.assertEqual(self.js(observation=dict(self.observed, text=receipt.canonical(altered)))["return_acceptance"], "rejected")

    def build_dispatch(self, request_id="parent-owned-001", payload=None):
        payload = self.payload if payload is None else payload
        script = ("const fs=require('fs');const {makeDispatch}=require(process.argv[1]);"
                  "const d=JSON.parse(fs.readFileSync(0,'utf8'));"
                  "process.stdout.write(JSON.stringify(makeDispatch('parent','owner',d.id,d.payload,d.results)));")
        r = subprocess.run(["node", "-e", script, str(ROOT/"scripts/return_receipt_receiver.js")],
                           input=json.dumps({"id":request_id,"payload":payload,"results":self.expected["results"]}),
                           text=True,capture_output=True,check=True)
        return json.loads(r.stdout)

    def test_parent_owned_logical_packet_roundtrips_without_child_fco(self):
        dispatch = self.build_dispatch()
        packet = receipt.parse_dispatch(dispatch["prompt"])
        self.assertEqual(packet["payload"], self.payload)
        self.assertEqual(packet["expected"]["request"]["request_id"], "parent-owned-001")
        self.assertEqual(dispatch["prompt_utf8_sha256"], hashlib.sha256(dispatch["prompt"].encode()).hexdigest())
        self.assertEqual(packet["expected"]["request"]["payload_utf8_sha256"],
                         hashlib.sha256(self.payload.encode()).hexdigest())
        body = receipt.produce(packet["expected"], self.raw, self.bindings, payload=packet["payload"])
        self.assertEqual(self.js([dispatch["expected"]], dict(self.observed,text=body))["return_acceptance"], "accepted")

    def test_provider_wrapper_is_not_logical_payload_and_cannot_be_substituted(self):
        dispatch = self.build_dispatch()
        wrapper = "<codex_delegation><input>" + dispatch["prompt"] + "</input></codex_delegation>"
        self.assertNotEqual(hashlib.sha256(wrapper.encode()).hexdigest(),
                            dispatch["expected"]["request"]["payload_utf8_sha256"])
        with self.assertRaises(receipt.ReceiptError):
            receipt.parse_dispatch(wrapper)
        altered = json.loads(dispatch["prompt"])
        altered["payload"] += "\n"
        with self.assertRaisesRegex(receipt.ReceiptError,"fingerprint"):
            receipt.parse_dispatch(receipt.canonical(altered))

    def test_stale_id_and_child_recomputed_mutated_payload_do_not_replace_parent_contract(self):
        dispatch = self.build_dispatch()
        stale = self.build_dispatch(request_id="old-parent-request")
        changed = self.build_dispatch(payload=self.payload+"extra")
        for other in [stale, changed]:
            body = receipt.produce(other["expected"], self.raw, self.bindings,
                                   payload=receipt.parse_dispatch(other["prompt"])["payload"])
            result = self.js([dispatch["expected"]], dict(self.observed,text=body))
            self.assertEqual(result["return_acceptance"], "rejected")
            self.assertEqual(result["pending_request_refs"], ["parent-owned-001"])

    def test_pure_js_utf8_sha_matches_stdlib_without_normalization(self):
        vectors = ["", "abc", "合并\r\n😀", "e\u0301", "é", "\x00\tend",
                   "a"*55, "a"*56, "a"*63, "a"*64, "a"*65, "汉😀"*500]
        script = ("const fs=require('fs'),{sha256Utf8}=require(process.argv[1]);"
                  "process.stdout.write(JSON.stringify(JSON.parse(fs.readFileSync(0,'utf8')).map(sha256Utf8)));")
        r = subprocess.run(["node","-e",script,str(ROOT/"scripts/return_receipt_receiver.js")],
                           input=json.dumps(vectors),text=True,capture_output=True,check=True)
        self.assertEqual(json.loads(r.stdout),[hashlib.sha256(s.encode()).hexdigest() for s in vectors])
        self.assertNotEqual(json.loads(r.stdout)[3],json.loads(r.stdout)[4])

    def test_producer_refuses_wrong_logical_payload_before_report_reads(self):
        from unittest.mock import patch
        with patch.object(Path,"read_bytes",side_effect=AssertionError("must not read report")):
            with self.assertRaisesRegex(receipt.ReceiptError,"payload fingerprint"):
                receipt.produce(self.expected,self.raw,self.bindings,payload="different")



if __name__ == "__main__":
    unittest.main()

