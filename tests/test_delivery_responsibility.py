"""Original delivery responsibility survives finite task outcomes.

Synthetic state controls do not authenticate a native owner or endpoint receipt.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import task_coordination as coordination
import task_runtime as task


class DeliveryResponsibilityTests(unittest.TestCase):
    def current(self, identity="segment", executor="engineer", final_owner="domain-owner"):
        return {"task_id":identity,"revision":7,"input_revision":3,"acceptance_sha256":"a"*64,
            "accepted":False,"execution":None,
            "spec":{"request_id":"original-request","requirement":"Implement one segment",
                "acceptance_version":"0.1.3","request_identity":{"thread_id":"original-thread","turn_id":"current-turn","message_id":"current-input"},
                "assignment":{"owner":executor,"role":"implementation","delivery_owner":final_owner,"covers":[]},
                "result_scope":{"goal":"Recipient obtains a usable result","object_ref":"original-result","source_ref":"original-request-source","obligations":[{"id":"usable","acceptance":"Current result is usable at the requested recipient"}]}},
            "steps":{"work":{"state":"pending"}},
            "work_partition":{"ready":["work"],"waiting":[],"unresolved":[],"verified":[]},
            "result_dimensions":{"original_goal":"Recipient obtains a usable result","pending_obligations":["usable"],"reviewable_results":[],"continuable_results":[],"business_outcome":"pending"}}

    def project(self, value):
        return coordination.delivery_responsibility(value)

    def test_dispatch_does_not_transfer_original_delivery_responsibility(self):
        out=self.project(self.current())
        self.assertEqual((out["executor"],out["delivery_owner"]),("engineer","domain-owner"))
        self.assertEqual(out["original_goal"],"Recipient obtains a usable result")
        action=out["next_actions"][0]
        self.assertEqual((action["owner"],action["delivery_owner"]),("engineer","domain-owner"))
        self.assertEqual(action["current_input"]["input_revision"],3)
        self.assertEqual(action["current_input"]["acceptance_version"],"0.1.3")

    def test_local_acceptance_cannot_certify_recipient_effect(self):
        value=self.current();value["accepted"]=True
        value["work_partition"].update(ready=[],verified=["work"])
        out=self.project(value)
        self.assertTrue(out["segment_accepted"])
        self.assertIsNone(out["end_to_end_complete"])
        self.assertEqual(out["pending_obligations"],["usable"])

    def test_failed_segment_routes_diagnosis_back_without_repeating_effect(self):
        value=self.current();value["steps"]["work"].update(state="blocked",wait={"kind":"failed","execution_id":"attempt-1"})
        value["work_partition"].update(ready=[],waiting=[{"step":"work","condition":value["steps"]["work"]["wait"]}])
        out=self.project(value);action=out["next_actions"][0]
        self.assertEqual(action["kind"],"diagnose_original_failure")
        self.assertEqual(action["owner"],"engineer");self.assertEqual(action["delivery_owner"],"domain-owner")
        self.assertFalse(action["may_repeat_effect"])
        self.assertEqual(action["condition"]["execution_id"],"attempt-1")

    def test_denial_remains_scoped_and_independent_segment_continues(self):
        a=self.current("a");a["work_partition"].update(ready=[],waiting=[{"step":"work","condition":"retained_refusal","restriction_refs":["original-denial"]}])
        b=self.current("b")
        out=coordination.continuation_projection({"a":a,"b":b},{"complete":False,"gaps":[]})
        self.assertTrue(out["must_continue"]);self.assertFalse(out["may_wait"])
        self.assertEqual([x["task_id"] for x in out["next_actions"]],["b"])
        self.assertEqual(out["scoped_waits"][0]["delivery_owner"],"domain-owner")
        self.assertFalse(out["scoped_waits"][0]["may_repeat_effect"])

    def test_unknown_attempt_is_not_relaunched_or_user_approval_exit(self):
        value=self.current();value["work_partition"].update(ready=[],waiting=[{"step":"work","condition":"original_continuation_requires_readback","origin":{"operation_id":"original"}}])
        out=self.project(value)
        self.assertEqual(out["scoped_waits"][0]["recovery"],"inspect_only_original_permitted_outcome")
        self.assertFalse(out["scoped_waits"][0]["may_repeat_effect"])
        self.assertEqual(out["next_actions"],[])

    def test_reviewable_independent_result_is_not_hidden_by_other_unknown(self):
        a=self.current("a");a["work_partition"].update(ready=[],waiting=[{"step":"work","condition":"reconcile"}])
        b=self.current("b");b["work_partition"].update(ready=[],verified=["work"])
        b["result_dimensions"]["reviewable_results"]=[{"path":"original-result.json","sha256":"b"*64}]
        out=coordination.continuation_projection({"a":a,"b":b},{"complete":False,"gaps":[]})
        review=next(x for x in out["next_actions"] if x["kind"]=="review_original_results")
        self.assertEqual(review["owner"],"domain-owner");self.assertFalse(out["may_wait"])

    def test_current_goal_version_and_source_are_not_inherited_from_another_input(self):
        old=self.current();new=copy.deepcopy(old)
        new["spec"]["acceptance_version"]="0.1.4";new["input_revision"]=4
        new["spec"]["result_scope"]["goal"]="Changed original target"
        self.assertNotEqual(self.project(old)["current_input"],self.project(new)["current_input"])
        self.assertEqual(self.project(new)["original_goal"],"Changed original target")
        self.assertEqual(self.project(old)["goal_source"],"original-request-source")

    def test_missing_assignment_does_not_invent_an_assistant_owner(self):
        value=self.current();del value["spec"]["assignment"]
        out=self.project(value)
        self.assertIsNone(out["delivery_owner"]);self.assertIsNone(out["executor"])
        self.assertIn("delivery_owner_not_declared",out["gaps"])

    def test_nullable_old_project_and_assignment_remain_readable(self):
        value=self.current();value['spec'].update(project=None,assignment=None)
        self.assertIsNone(self.project(value)['delivery_owner'])

    def test_retained_goal_and_complete_requirement_origins_reach_all_continuations(self):
        value=self.current()
        value['spec']['result_scope'].update(goal='Current engineering slice',source_ref='current-slice')
        requirement={'id':'usable','acceptance':'Original recipient obtains a usable result',
            'source_ref':'original-business-source','input_revision':1,
            'request_identity':{'thread_id':'original','turn_id':'original-turn','message_id':'original-message'}}
        retained={'original_goal':'Original business outcome','object_ref':'original-result',
                  'obligations':[requirement]}
        value['result_dimensions']['requirements']=retained
        value['work_partition'].update(ready=[],verified=['work'])
        original=copy.deepcopy(value)
        out=self.project(value);action=out['next_actions'][0]
        self.assertEqual(out['original_goal'],'Original business outcome')
        self.assertIsNone(out['goal_source'])  # Never relabel the current slice as its origin.
        self.assertEqual(action['requirements'],[requirement])
        self.assertEqual(action['requirement_context'],retained)
        projected=coordination.continuation_projection({'segment':value},{'complete':False,'gaps':[]})
        self.assertEqual(projected['next_actions'][0]['requirements'],[requirement])
        action['requirements'][0]['source_ref']='changed projection'
        self.assertEqual(value,original)

    def test_normal_cli_status_consumes_existing_assignment_without_extra_schema(self):
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp).resolve();(root/"out.txt").write_text("owned result")
            spec={"request_id":"original","requirement":"Finite segment","acceptance_version":"0.1.3","workspace":str(root),"execution_mode":"external","max_runs":1,
                "assignment":{"owner":"engineer","role":"implementation","delivery_owner":"domain-owner","covers":[]},
                "steps":[{"id":"work","instruction":"Read only owned output","checks":[{"kind":"file","path":"out.txt","sha256":task.digest(b"owned result")}]}]}
            store=task.Store(str(root/"codex"),"current");store.create("current",spec)
            entry=Path(__file__).resolve().parents[1]/"scripts/codex_framework.py"
            r=subprocess.run([sys.executable,str(entry),"--home",str(root),"--codex-home",str(root/"codex"),"task"],
                input=json.dumps({"operation":"status","task_id":"current"}),text=True,capture_output=True)
            self.assertEqual(r.returncode,0,r.stderr+r.stdout)
            out=json.loads(r.stdout);self.assertEqual(out["delivery_responsibility"]["delivery_owner"],"domain-owner")
            self.assertEqual(out["next_action"]["delivery_owner"],"domain-owner")
            self.assertEqual(store.read()["revision"],1)


if __name__ == "__main__":unittest.main()
