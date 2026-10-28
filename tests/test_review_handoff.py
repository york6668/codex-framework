"""Behavioural tests of original-call adapters; native envelopes below are fixtures.

They do not sign natural compaction, model adoption or platform-wide interception.
"""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import return_receipt as known
import review_handoff as review


def digest(raw):
    return hashlib.sha256(raw.encode() if isinstance(raw, str) else raw).hexdigest()


class ReviewHandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.subjects = [{"object_key": "candidate", "ref": "fixed-input.json", "sha256": "a" * 64}]
        self.questions = [{"question_key": "gate-%02d" % i, "text": "核对此项和实际限制 %d" % i,
                           "object_keys": ["candidate"]} for i in range(1, 15)]
        self.dispatch = self.make()
        self.evidence_text = '{"actual_probe":"failed truthfully, after execution"}\n'
        self.evidence = {"probe.json": self.evidence_text.encode()}
        self.result = {"subjects": self.subjects, "answers": [
            {"object_key": "candidate", "question_key": q["question_key"], "status": "failed",
             "answer": "Observed failure, not predicted PASS: " + q["text"],
             "evidence": [{"ref": "probe.json", "sha256": digest(self.evidence_text)}]} for q in self.questions],
            "limitations": ["Synthetic native envelopes; natural model adoption not run"],
            "next_scope": "Original owner corrects only this failure; other authorised work continues"}
        self.body, self.rb = self.produce(self.dispatch, self.result)
        self.native = self.native_read(self.dispatch, self.body)

    def js(self, op, **args):
        source = """
const fs=require('fs');
const old=require(process.argv[1]+'/scripts/return_receipt_receiver.js');
const api=require(process.argv[1]+'/scripts/review_handoff.js').createReviewHandoff(old.sha256Utf8,old.receiveFinal);
const d=JSON.parse(fs.readFileSync(0,'utf8'));
(async()=>{
 let r;
 if(d.op==='make') r=api.makeReviewDispatch(...d.args);
 else if(d.op==='receive') r=api.receiveReview(d.dispatches,d.selected,d.native,d.readback,d.source);
 else if(d.op==='recoverNative') r=api.recoverNativeDispatch(d.native,d.pin);
 else if(d.op==='recover') r=api.recoverDispatch(d.raw,d.sha);
 else if(d.op==='observe') r=api.observeReview(d.dispatch,d.native);
 else if(d.op==='readOnly') {
   const calls=[];
   const got=await api.readReview(d.dispatches,d.selected,{
     source:d.source,readNative:async target=>{calls.push(['readNative',target]);return d.native;},
     readReport:async ref=>{calls.push(['readReport',ref]);return d.readback;}});
   r={...got,calls};
 }
 else if(d.op==='decide') r=api.decideReview(d.bound,d.decision);
 else if(d.op==='continue') {
   const calls=[], retained=[]; const record=d.record;
   const io={source:d.source,readNative:async target=>{calls.push('read');return d.native;},
     readReport:async ref=>d.readback,
     retain:async raw=>{retained.push(JSON.parse(raw));return d.retain!==false;},
     consume:async bound=>{calls.push('consume');if(d.consumeThrows)throw Error('lost consumer ack');
       if(d.consumerCli) {
         const report={...d.consumerTemplate,native_result:{thread_id:d.selected.target_thread,
           turn_id:bound.native_turn_id,final_ref:bound.native_final_ref,final_sha256:bound.final_utf8_sha256}};
         const text=JSON.stringify(report);fs.writeFileSync(d.consumerPath,text);
         const request={...d.consumerRequest,sha256:old.sha256Utf8(text)};
         const r=require('child_process').spawnSync(d.consumerCli[0],d.consumerCli.slice(1),{input:JSON.stringify(request),encoding:'utf8'});
         if(r.status!==0 || r.stderr)throw Error('original consumer failed: '+r.stdout+r.stderr);
         const actual=JSON.parse(r.stdout);if(actual.result_state.result.sha256!==request.sha256)throw Error('consumer readback mismatch');
         return {status:'committed',receipt_ref:actual.task_id+'/revision/'+actual.revision};
       }
       return {status:'committed',receipt_ref:'original-consumer-receipt'};}};
   if(d.inspect)io.inspectConsumption=async identity=>{calls.push('inspect');return {identity,status:d.inspect,receipt_ref:'original-consumer-receipt'};};
   const outputs=[];
   for(let i=0;i<(d.repeat||1);i++)outputs.push(await api.continueReview(record,d.dispatches,d.selected,io));
   r={outputs,record,calls,retained};
 }
 else if(d.op==='io') {
   const calls=[], record=d.record;
   try {
     const sent=await api.sendReview(record,{retain:async raw=>{calls.push(['retain',JSON.parse(raw).attempt]);return d.retain!==false;},
       send:async (target,prompt)=>{calls.push(['send',target,prompt]);if(d.sendThrows)throw Error('lost ack');return {native:'ack'};}});
     const received=d.doRead?await api.readReview(d.dispatches,d.selected,{
       source:d.source,readNative:async target=>{calls.push(['readNative',target]);return d.native;},
       readReport:async ref=>{calls.push(['readReport',ref]);return d.readback;}}):null;
     r={sent,received,calls,record};
   }catch(e){r={error:e.message,calls,record};}
 } else throw Error('unknown test operation');
 process.stdout.write(JSON.stringify(r));
})().catch(e=>{process.stdout.write(JSON.stringify({error:e.message,errorName:e.name}));});
"""
        run = subprocess.run(["node", "-e", source, str(ROOT)], input=json.dumps(dict(op=op, **args)),
                             capture_output=True, text=True, check=True)
        self.assertEqual(run.stderr, "")
        return json.loads(run.stdout)

    def make(self, sender="parent", request_id="new-review", supersedes=None):
        return self.js("make", args=[sender, "reviewer", request_id, "只读核对当前对象，未知结果不得预填。😀\n",
                                    self.subjects, self.questions, supersedes])

    def produce(self, dispatch, result):
        report = self.root / (dispatch["contract"]["request"]["sender_thread"] + "-" +
                              dispatch["contract"]["request"]["request_id"] + ".json")
        raw = json.dumps({"original_request": dispatch["contract"]["request"],
                          "domain_review": result, "domain_status": "own schema retained"}, ensure_ascii=False, indent=2).encode()
        report.write_bytes(raw)
        body = review.produce_review(dispatch["prompt"], str(report), raw, request_pointer="/original_request",
                                     result_pointer="/domain_review", evidence_readbacks=self.evidence)
        rb = {"ref": str(report), "text": raw.decode(), "truncated": False,
              "evidence": [{"ref": ref, "text": value.decode(), "truncated": False} for ref, value in self.evidence.items()]}
        return body, rb

    def native_read(self, dispatch, final, style="app", marker=False):
        r = dispatch["contract"]["request"]
        xml = "<codex_delegation><source_thread_id>" + r["sender_thread"] + "</source_thread_id><input>" + dispatch["prompt"] + "</input></codex_delegation>"
        item = {"type": "userMessage", "id": "native-input", "content": [{"type": "text", "text": xml,
                "codexDelegation": {"sourceThreadId": r["sender_thread"], "input": dispatch["prompt"]}}]}
        if style == "cloud":
            item = {"type": "functionCallOutput", "id": "native-input", "name": "send_message", "namespace": "cloud_threads",
                    "output": {"text": xml, "truncated": False}}
        items = [item]
        if marker:
            items.append({"type": "contextCompaction", "id": "visible-marker-no-private-content"})
        items.append({"type": "agentMessage", "id": "native-final", "phase": "final_answer", "text": final})
        return {"isError": False, "content": [{"type": "text", "text": json.dumps({"schemaVersion": 1,
            "thread": {"id": "reviewer"}, "turns": [{"id": "native-turn", "status": "completed", "error": None,
                                                      "items": items}]}, ensure_ascii=False)}]}

    def receive(self, dispatches=None, selected=None, native=None, readback=None):
        return self.js("receive", dispatches=dispatches if dispatches is not None else [self.dispatch],
                       selected=selected or self.dispatch["contract"]["request"],
                       native=self.native if native is None else native, readback=self.rb if readback is None else readback)

    def cloud_projection(self):
        # Synthetic transport control follows the observed parent cloud read.
        # The exact captured parent sample is replayed separately as evidence.
        return {'content': [{'type': 'text', 'text': 'Action completed.'}],
                'structuredContent': {'threadId': 'reviewer', 'parentThreadId': None,
                    'latestTurn': {'id': 'native-turn', 'status': 'completed', 'error': None},
                    'items': [{'id': 'native-final', 'turnId': 'native-turn',
                               'role': 'assistant', 'text': self.body, 'truncated': False}],
                    'nextCursor': 'original-provider-cursor'}, 'isError': False}

    def result_source(self):
        return review.review_source(self.dispatch["prompt"], self.rb["ref"], self.rb["text"].encode(),
            request_pointer="/original_request", result_pointer="/domain_review", evidence_readbacks=self.evidence)

    def test_original_result_is_read_independently_of_human_final(self):
        source = self.result_source()
        self.assertEqual(set(source), {"request", "report"})
        native = self.native_read(self.dispatch, "核对完成，原结果已保存。存在失败项，由原责任续修。")
        got = self.js("readOnly", dispatches=[self.dispatch], selected=source["request"],
                      native=native, source=source, readback=self.rb)
        self.assertEqual(got["evaluation"]["binding"], "bound")
        self.assertEqual(got["evaluation"]["result"], self.result)
        self.assertEqual(got["evaluation"]["result_acceptance"], "unreviewed")
        self.assertEqual(got["calls"], [["readNative", "reviewer"], ["readReport", self.rb["ref"]]])
        missing = self.js("receive", dispatches=[self.dispatch], selected=source["request"], native=native, readback=self.rb)
        self.assertEqual(missing["binding"], "unknown")

    def test_result_locator_does_not_grant_read_or_clear_old_obligations(self):
        source = self.result_source()
        source["request"] = dict(source["request"], request_id="wrong-request")
        got = self.js("readOnly", dispatches=[self.dispatch], selected=self.dispatch["contract"]["request"],
                      native=self.native_read(self.dispatch, "只供用户阅读"), source=source, readback=self.rb)
        self.assertEqual(got["calls"], [["readNative", "reviewer"]])
        self.assertEqual(got["evaluation"]["binding"], "rejected")
        self.assertEqual(got["evaluation"]["pending_requests"], [self.dispatch["contract"]["request"]])

    def test_local_readback_projects_only_requested_result(self):
        # Synthetic canary stays in the original node's unrelated fields, not
        # the locator or adapter output. This is not a test with real secrets.
        import uuid
        marker = "local-only-" + uuid.uuid4().hex
        report = json.loads(self.rb["text"])
        report["unrelated_local_detail"] = marker
        self.rb["text"] = json.dumps(report, ensure_ascii=False)
        Path(self.rb["ref"]).write_text(self.rb["text"])
        source = self.result_source()
        native = self.native_read(self.dispatch, "结果供原责任核收")
        body = json.loads(native["content"][0]["text"])
        body["turns"][0]["items"].insert(1, {"id": "private-tool-output", "type": "functionCallOutput", "text": marker})
        native["content"][0]["text"] = json.dumps(body)
        got = self.js("readOnly", dispatches=[self.dispatch], selected=source["request"],
                      native=native, source=source, readback=self.rb)
        self.assertEqual(got["evaluation"]["binding"], "bound")
        self.assertNotIn(marker, json.dumps(got))
        self.assertNotIn(marker, json.dumps(source))
        self.assertNotIn("native_readback", got)

    def test_verified_native_pagination_stays_unknown_without_reading_report(self):
        raw = self.native_read(self.dispatch, "结果在较早轮次")
        body = json.loads(raw["content"][0]["text"])
        body["turns"] = []
        body["page"] = {"order": "newest_first", "limit": 1, "hasMore": True, "nextCursor": "provider-cursor"}
        raw["content"][0]["text"] = json.dumps(body)
        got = self.js("readOnly", dispatches=[self.dispatch], selected=self.dispatch["contract"]["request"],
                      native=raw, source=self.result_source(), readback=self.rb)
        self.assertEqual(got["evaluation"]["binding"], "unknown")
        self.assertEqual(got["evaluation"]["next_cursor"], "provider-cursor")
        self.assertEqual(got["calls"], [["readNative", "reviewer"]])

    def test_prose_return_duplicate_and_lost_receipt_never_replay(self):
        source = self.result_source()
        arguments = dict(dispatches=[self.dispatch], selected=source["request"], source=source,
                         native=self.native_read(self.dispatch, "核对完成，原结果供责任方审阅"), readback=self.rb)
        record = {"dispatch": self.dispatch, "attempt": "returned", "native_receipt": None}
        got = self.js("continue", record=record, repeat=2, **arguments)
        self.assertEqual(got["calls"].count("consume"), 1)
        self.assertTrue(all(row["state"] == "consumed" for row in got["outputs"]))
        lost = self.js("continue", record=record, consumeThrows=True, **arguments)
        self.assertEqual(lost["outputs"][0]["state"], "unknown")
        resumed = self.js("continue", record=lost["record"], inspect="committed", **arguments)
        self.assertNotIn("consume", resumed["calls"])
        self.assertEqual(resumed["outputs"][0]["state"], "consumed")

    def test_cloud_actual_projection_is_recognized_but_not_a_fabricated_native_final(self):
        raw = self.cloud_projection()
        got = self.receive(native=raw)
        self.assertEqual(got['binding'], 'unknown')
        self.assertEqual(got['native_read_schema'], 'cloud_threads')
        self.assertEqual(got['reason'], 'cloud projection lacks original input and native final identity')
        self.assertEqual(got['next_cursor'], 'original-provider-cursor')
        self.assertEqual(got['pending_requests'], [self.dispatch['contract']['request']])
        # No implicit pagination, report read, header-derived dispatch or schema conversion.
        read = self.js('readOnly', dispatches=[self.dispatch], selected=self.dispatch['contract']['request'],
                       native=raw, readback=self.rb)
        self.assertEqual(read['calls'], [['readNative', 'reviewer']])
        self.assertNotIn('native_readback', read)
        self.assertEqual(read['evaluation']['native_read_schema'], 'cloud_threads')

    def test_cloud_latest_status_cannot_certify_an_older_request_turn(self):
        pin = {'sender_thread': 'parent', 'target_thread': 'reviewer',
               'turn_id': 'older-turn', 'prompt_utf8_sha256': self.dispatch['prompt_utf8_sha256']}
        got = self.js('recoverNative', native=self.cloud_projection(), pin=pin)
        self.assertEqual((got['state'], got['reason']), ('unknown', 'cloud historical turn status unavailable'))
        self.assertFalse(got['may_resend'])
        self.assertEqual(got['native_read_schema'], 'cloud_threads')
        pin['turn_id'] = 'native-turn'
        got = self.js('recoverNative', native=self.cloud_projection(), pin=pin)
        self.assertEqual(got['state'], 'unknown')
        self.assertNotIn('dispatch', got)

    def test_cloud_projection_shapes_fail_structurally_without_throwing(self):
        mutations = [lambda r: r.update(isError=True),
                     lambda r: r['structuredContent'].update(latestTurn=[]),
                     lambda r: r['structuredContent'].update(latestTurn=None),
                     lambda r: r['structuredContent'].update(items=[None]),
                     lambda r: r['structuredContent'].update(items={}),
                     lambda r: r['structuredContent']['latestTurn'].update(id=[]),
                     lambda r: r['structuredContent']['latestTurn'].update(status=[]),
                     lambda r: r['structuredContent']['items'][0].update(turnId=[]),
                     lambda r: r['structuredContent']['items'][0].update(role=[]),
                     lambda r: r['structuredContent']['items'][0].update(text=[]),
                     lambda r: r['structuredContent']['items'][0].update(truncated='false'),
                     lambda r: r['structuredContent'].update(nextCursor=[]),
                     lambda r: r['structuredContent'].update(parentThreadId=[]),
                     lambda r: r['structuredContent'].update(schemaVersion=1),
                     lambda r: r['structuredContent']['items'].append(copy.deepcopy(r['structuredContent']['items'][0])),
                     lambda r: r['content'][0].update(text='{"schemaVersion":1}'),
                     lambda r: r.update(structuredContent=[])]
        for mutate in mutations:
            raw = self.cloud_projection(); mutate(raw)
            got = self.receive(native=raw)
            self.assertEqual(got['binding'], 'unknown', got)
            self.assertNotIn('errorName', got)
            self.assertEqual(got['accepted_requests'], [])

    def test_cloud_projection_aggregate_text_budget_is_bounded(self):
        raw = self.cloud_projection()
        first = raw['structuredContent']['items'][0]
        first['text'] = 'x' * (1024 * 1024 + 1)
        second = copy.deepcopy(first); second['id'] = 'second-item'
        raw['structuredContent']['items'].append(second)
        got = self.receive(native=raw)
        self.assertEqual(got['binding'], 'unknown')
        self.assertEqual(got['reason'], 'cloud projection text budget')
        self.assertNotIn('native_read_schema', got)

    def test_native_provider_discriminator_is_not_an_unvalidated_body_alias(self):
        pin = {'sender_thread': 'parent', 'target_thread': 'reviewer',
               'turn_id': 'native-turn', 'prompt_utf8_sha256': self.dispatch['prompt_utf8_sha256']}
        for latest in ['absent', None, [], {}, {'id': 'native-turn', 'status': 'completed', 'error': None}]:
            raw = copy.deepcopy(self.native)
            body = json.loads(raw['content'][0]['text'])
            body['threadId'] = 'reviewer'
            if latest != 'absent': body['latestTurn'] = latest
            raw['content'][0]['text'] = json.dumps(body)
            got = self.js('recoverNative', native=raw, pin=pin)
            self.assertEqual(got.get('state'), 'unknown', got)
            self.assertEqual(got.get('reason'), 'conflicting native read schemas', got)
            self.assertNotIn('errorName', got)
            observed = self.js('observe', native=raw, dispatch=self.dispatch)
            self.assertEqual(observed.get('reason'), 'conflicting native read schemas', observed)

    def decision(self, bound, verdict="accepted"):
        return self.js("decide", bound=bound, decision={"decision": verdict, "final_ref": bound["native_final_ref"],
            "final_utf8_sha256": bound["final_utf8_sha256"], "report_sha256": bound["report_sha256"],
            "reason": "Original parent has separately reviewed actual failure and limits"})

    def test_H01_unlocated_old_final_is_unknown_without_destroying_old_work(self):
        result = self.receive(native=self.native_read(self.dispatch, "Old statistics E2E PASS c2be; current input a473 was not answered"))
        self.assertEqual(result["binding"], "unknown")
        self.assertEqual(result["pending_requests"], [self.dispatch["contract"]["request"]])
        self.assertTrue(result["preserve_completed_work"])

    def test_H02_fourteen_actual_failed_answers_bound_but_not_auto_accepted(self):
        self.assertNotIn("result", self.dispatch["contract"])
        self.assertNotIn("report", self.dispatch["contract"])
        bound = self.receive()
        self.assertEqual((bound["binding"], bound["coverage"], bound["result_acceptance"]), ("bound", "complete", "unreviewed"))
        self.assertEqual({a["status"] for a in bound["result"]["answers"]}, {"failed"})
        self.assertEqual(self.decision(bound)["result_acceptance"], "accepted")

    def test_H03_current_header_cannot_replace_original_report_subject_or_answers(self):
        for field in ["original_request", "domain_review"]:
            rb = copy.deepcopy(self.rb)
            old = json.loads(rb["text"])
            if field == "original_request": old[field]["request_id"] = "old-statistics"
            else: old[field]["subjects"][0]["sha256"] = "c" * 64
            rb["text"] = json.dumps(old)
            body = json.loads(self.body)
            body["report"]["sha256"] = digest(rb["text"])
            result = self.receive(native=self.native_read(self.dispatch, known.canonical(body)), readback=rb)
            self.assertEqual(result["binding"], "rejected")
        # Coherent self-labelled but semantically wrong prose remains UNREVIEWED.
        changed = copy.deepcopy(self.result)
        changed["answers"][0]["answer"] = "Old unrelated result with current header"
        body, rb = self.produce(self.dispatch, changed)
        bound = self.receive(native=self.native_read(self.dispatch, body), readback=rb)
        self.assertEqual(bound["result_acceptance"], "unreviewed")
        self.assertEqual(self.decision(bound, "rejected")["result_acceptance"], "rejected")

    def test_H04_native_multiple_senders_same_id_keep_independent_request(self):
        other = self.make(sender="owner-B")
        body, rb = self.produce(other, self.result)
        native = self.native_read(other, body, style="cloud")
        bound = self.receive([self.dispatch, other], other["contract"]["request"], native, rb)
        decided = self.decision(bound)
        self.assertEqual(decided["pending_requests"], [self.dispatch["contract"]["request"]])
        self.assertEqual(decided["accepted_requests"], [other["contract"]["request"]])

    def test_H05_explicit_revision_retires_only_its_own_received_request(self):
        other = self.make(sender="owner-B")
        revision = self.make(request_id="revision-A", supersedes=self.dispatch["contract"]["request"])
        body, rb = self.produce(revision, self.result)
        bound = self.receive([self.dispatch, other, revision], revision["contract"]["request"], self.native_read(revision, body), rb)
        self.assertEqual(bound["superseded_requests"], [self.dispatch["contract"]["request"]])
        self.assertEqual(self.decision(bound)["pending_requests"], [other["contract"]["request"]])
        self.assertIn("error", self.make(sender="other", supersedes=self.dispatch["contract"]["request"]))

    def test_H06_missing_blocked_not_run_answers_remain_partial(self):
        for status in [None, "not_run", "blocked", "unknown", "limited"]:
            r = copy.deepcopy(self.result)
            if status is None: r["answers"].pop()
            else: r["answers"][0].update(status=status, evidence=[])
            body, rb = self.produce(self.dispatch, r)
            bound = self.receive(native=self.native_read(self.dispatch, body), readback=rb)
            self.assertEqual((bound["binding"], bound["coverage"]), ("bound", "partial"))
            self.assertIn("error", self.decision(bound))
            self.assertTrue(bound["independent_work_may_continue"])

    def test_H07_visible_compaction_marker_does_not_change_original_request(self):
        self.assertEqual(self.receive(native=self.native_read(self.dispatch, "old topic", marker=True))["binding"], "unknown")
        self.assertEqual(self.receive(native=self.native_read(self.dispatch, self.body, marker=True))["binding"], "bound")

    def test_H08_missing_cache_or_failed_retention_sends_nothing(self):
        for record in [None, {"dispatch": self.dispatch, "attempt": "prepared", "native_receipt": None}]:
            r = self.js("io", record=record, retain=False)
            self.assertIn("error", r)
            self.assertFalse(any(c[0] == "send" for c in r["calls"]))

    def test_H09_after_send_restore_only_original_pinned_native_input_not_final(self):
        pin = {"sender_thread": "parent", "target_thread": "reviewer", "turn_id": "native-turn",
               "prompt_utf8_sha256": self.dispatch["prompt_utf8_sha256"]}
        for style in ["app", "cloud"]:
            native = self.native_read(self.dispatch, "wrong final cannot restore authority", style=style)
            r = self.js("recoverNative", native=native, pin=pin)
            self.assertEqual(r["state"], "recovered")
            self.assertEqual(r["dispatch"], self.dispatch)
            self.assertFalse(r["may_resend"])
        for key in pin:
            bad = dict(pin, **{key: "f" * 64 if key.endswith("sha256") else "wrong"})
            self.assertEqual(self.js("recoverNative", native=self.native, pin=bad)["state"], "unknown")
        self.assertEqual(self.js("recoverNative", native=None, pin=pin)["state"], "unknown")

    def test_H10_one_recovered_request_never_claims_complete_pending_set(self):
        raw = known.canonical({"dispatch": self.dispatch, "attempt": "started", "native_receipt": None})
        r = self.js("recover", raw=raw, sha=digest(raw))
        self.assertEqual(r["state"], "recovered")
        self.assertFalse(r["may_resend"])
        self.assertEqual(self.js("recover", raw=None, sha=None)["state"], "unknown")
        self.assertEqual(self.receive(dispatches=[])["binding"], "unknown")
        r = self.js("io", record=r["record"])
        self.assertFalse(r["calls"])

    def test_H11_actual_full_final_no_trim_ambiguous_missing_or_truncated_is_unknown(self):
        for suffix in ["\n", "\nold result", " commentary"]:
            self.assertEqual(self.receive(native=self.native_read(self.dispatch, self.body + suffix))["binding"], "rejected")
        for kind in ["unfinished", "truncated", "two-finals", "wrong-phase", "wrong-input", "no-thread"]:
            n = copy.deepcopy(self.native)
            b = json.loads(n["content"][0]["text"])
            t = b["turns"][0]
            if kind == "unfinished": t["status"] = "inProgress"
            if kind == "truncated": t["items"][-1]["truncated"] = True
            if kind == "two-finals": t["items"].append(copy.deepcopy(t["items"][-1]))
            if kind == "wrong-phase": t["items"][-1]["phase"] = "commentary"
            if kind == "wrong-input": t["items"][0]["content"][0]["codexDelegation"]["sourceThreadId"] = "wrong"
            if kind == "no-thread": b["thread"]["id"] = "wrong"
            n["content"][0]["text"] = json.dumps(b)
            self.assertEqual(self.receive(native=n)["binding"], "unknown", kind)

    def test_H12_refused_dependency_is_only_synthetic_metadata_and_stays_partial(self):
        r = copy.deepcopy(self.result)
        r["answers"][0].update(status="blocked", answer="synthetic-refusal-ref; no forwarding or alternate tool", evidence=[])
        body, rb = self.produce(self.dispatch, r)
        result = self.receive(native=self.native_read(self.dispatch, body), readback=rb)
        self.assertEqual(result["coverage"], "partial")
        self.assertTrue(result["independent_work_may_continue"])

    def test_H13_old_known_mode_original_bytes_unchanged_and_text_report_preflight(self):
        self.assertEqual(digest((ROOT / "scripts/return_receipt_receiver.js").read_bytes()),
                         "aaa0995a1584e0ab610df0bcdd2c3ee9e4a156bdf0a4c4ad231bba79cfd99ef4")
        report = self.root / "original-report.txt"
        report.write_text("原报告是文本；不改结论，不补造旧回执。\n")
        expected = {"request": self.dispatch["contract"]["request"], "results": [{"object_key": "fixed",
            "report_ref": str(report), "report_sha256": digest(report.read_bytes()), "identities": [],
            "outcome": "limited", "summary": "original finite meaning", "limitations": ["No replay"], "next_scope": "original next"}]}
        payload = json.loads(self.dispatch["prompt"])["payload"]
        self.assertEqual(known.preflight(expected, payload=payload)["state"], "ready")
        ledger = {"receipt": {"report": {"path": str(report), "sha256": expected["results"][0]["report_sha256"]}}, "next": "original next"}
        bindings = [{"object_key": "fixed", "receipt_pointer": "/receipt", "receipt_value": ledger["receipt"],
            "report_pointer": "/receipt/report", "next_pointer": "/next", "next_value": ledger["next"]}]
        self.assertEqual(json.loads(known.produce(expected, json.dumps(ledger).encode(), bindings, payload=payload))["results"], expected["results"])
        # Known-mode recovery uses the ORIGINAL input, regardless of child final.
        prompt = known.canonical({"format": "finite-dispatch-v1", "payload": payload, "expected": expected})
        fake = copy.deepcopy(self.dispatch); fake["prompt"] = prompt
        native = self.native_read(fake, "actual producer failed after merge")
        pin = {"sender_thread": "parent", "target_thread": "reviewer", "turn_id": "native-turn", "prompt_utf8_sha256": digest(prompt)}
        recovered = self.js("recoverNative", native=native, pin=pin)
        self.assertEqual(recovered["expected"], expected)
        self.assertEqual(recovered["acceptance"], "unreviewed")
        report.write_bytes(b"\xff")
        expected["results"][0]["report_sha256"] = digest(report.read_bytes())
        with self.assertRaisesRegex(known.ReceiptError, "UTF-8"):
            known.preflight(expected, payload=payload)

    def test_H14_original_send_and_read_callbacks_used_once_without_semantic_auto_pass(self):
        record = {"dispatch": self.dispatch, "attempt": "prepared", "native_receipt": None}
        r = self.js("io", record=record, dispatches=[self.dispatch], selected=self.dispatch["contract"]["request"],
                    native=self.native, readback=self.rb, doRead=True)
        self.assertEqual([c[0] for c in r["calls"]], ["retain", "send", "retain", "readNative", "readReport"])
        self.assertEqual(r["calls"][1][1:], ["reviewer", self.dispatch["prompt"]])
        self.assertEqual(r["received"]["evaluation"]["result_acceptance"], "unreviewed")
        self.assertEqual(self.js("io", record=r["record"])["calls"], [])
        thrown = self.js("io", record=record, sendThrows=True)
        self.assertEqual(thrown["sent"]["state"], "unknown")
        self.assertEqual(len([c for c in thrown["calls"] if c[0] == "send"]), 1)

    def test_evidence_must_be_read_back_not_merely_linked_or_self_labelled(self):
        for kind in ["missing", "changed", "wrong-ref", "truncated"]:
            rb = copy.deepcopy(self.rb)
            if kind == "missing": rb["evidence"] = []
            if kind == "changed": rb["evidence"][0]["text"] += "new"
            if kind == "wrong-ref": rb["evidence"][0]["ref"] = "other"
            if kind == "truncated": rb["evidence"][0]["truncated"] = True
            self.assertEqual(self.receive(readback=rb)["binding"], "rejected")
        with self.assertRaisesRegex(known.ReceiptError, "evidence"):
            review.produce_review(self.dispatch["prompt"], self.rb["ref"], self.rb["text"].encode(),
                request_pointer="/original_request", result_pointer="/domain_review", evidence_readbacks={})

    def test_shapes_duplicate_fields_wrong_input_and_no_untrusted_read(self):
        for dispatches in [None, [None], [{}], [self.dispatch, self.dispatch]]:
            self.assertEqual(self.js("receive", dispatches=dispatches, selected=self.dispatch["contract"]["request"],
                                    native=self.native, readback=self.rb)["binding"], "unknown")
        for key, value in [("result", []), ("request", None), ("report", {"ref": []})]:
            body = json.loads(self.body); body[key] = value
            self.assertEqual(self.receive(native=self.native_read(self.dispatch, known.canonical(body)))["binding"], "rejected")
        body = self.body.replace('{"format":', '{"format":"fake","format":', 1)
        self.assertEqual(self.receive(native=self.native_read(self.dispatch, body))["binding"], "rejected")
        wrong = self.native_read(self.dispatch, "unparseable old result")
        r = self.js("io", record={"dispatch": self.dispatch, "attempt": "prepared", "native_receipt": None},
                    dispatches=[self.dispatch], selected=self.dispatch["contract"]["request"], native=wrong, readback=self.rb, doRead=True)
        self.assertFalse(any(c[0] == "readReport" for c in r["calls"]))

    def test_readback_bounds_and_invalid_unicode_are_structured_not_thrown(self):
        for value in ["x" * 65537, "😀" * 16385, "\ud800"]:
            for field in ["report", "evidence"]:
                rb = copy.deepcopy(self.rb)
                if field == "report": rb["text"] = value
                else: rb["evidence"][0]["text"] = value
                actual = self.receive(readback=rb)
                self.assertNotIn("error", actual)
                self.assertIn(actual["binding"], ("unknown", "rejected"))
        native = self.native_read(self.dispatch, self.body)
        body = json.loads(native["content"][0]["text"])
        c = body["turns"][0]["items"][0]["content"][0]
        c["codexDelegation"]["input"] = "x" * 65537
        c["text"] = "<codex_delegation><source_thread_id>parent</source_thread_id><input>" + c["codexDelegation"]["input"] + "</input></codex_delegation>"
        native["content"][0]["text"] = json.dumps(body)
        pin = {"sender_thread": "parent", "target_thread": "reviewer", "turn_id": "native-turn", "prompt_utf8_sha256": "a" * 64}
        self.assertEqual(self.js("recoverNative", native=native, pin=pin)["state"], "unknown")

    def test_native_null_adjacent_shapes_preserve_structured_unknown(self):
        pin = {"sender_thread": "parent", "target_thread": "reviewer", "turn_id": "native-turn", "prompt_utf8_sha256": self.dispatch["prompt_utf8_sha256"]}
        for native in [None, {"content": [None]}, {"content": [{"type": "text", "text": "null"}]},
                       {"content": [{"type": "text", "text": "[]"}]}, {"content": []},
                       {"content": [{}]}, {"content": [{"type": "text", "text": None}]}]:
            actual = self.receive(native=native) if native is not None else self.js("receive", dispatches=[self.dispatch],
                selected=self.dispatch["contract"]["request"], native=None, readback=self.rb)
            self.assertEqual(actual["binding"], "unknown")
            self.assertNotIn("error", actual)
            self.assertEqual(self.js("observe", dispatch=self.dispatch, native=native)["state"], "unknown")
            self.assertEqual(self.js("recoverNative", native=native, pin=pin)["state"], "unknown")
            read = self.js("readOnly", dispatches=[self.dispatch], selected=self.dispatch["contract"]["request"],
                           native=native, readback=self.rb)
            self.assertEqual(read["evaluation"]["binding"], "unknown")
            self.assertEqual(read["calls"], [["readNative", "reviewer"]])
        for value in [None, [], False, "text"]:
            native = self.native_read(self.dispatch, self.body)
            raw = json.loads(native["content"][0]["text"])
            raw["turns"][0]["items"][0]["content"] = [value]
            native["content"][0]["text"] = json.dumps(raw)
            self.assertEqual(self.receive(native=native)["binding"], "unknown")
        for flag in ["false", [], 0, True]:
            native = self.native_read(self.dispatch, self.body)
            raw = json.loads(native["content"][0]["text"])
            raw["turns"][0]["items"][-1]["truncated"] = flag
            native["content"][0]["text"] = json.dumps(raw)
            self.assertEqual(self.receive(native=native)["binding"], "unknown")


if __name__ == "__main__":
    unittest.main()
