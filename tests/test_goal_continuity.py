"""Concrete issue continuity, real-source dependency counterexamples, isolated IO."""

import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from test_framework import FixtureCase, seed_runtime
from test_coordination_files import view_request, event
import coordination_files as files
import framework_runtime as runtime


def action(name="inspect", effect="read"):
    return {"object_ref": "demo:" + name, "destination_ref": "demo:node", "effect": effect}


def path(name="inspect", **changes):
    return {"id": name, "purpose": "Compare competing explanations using original evidence",
            "question_ids": ["cause"], "action": action(name),
            "permission": {"status": "allowed", "evidence_refs": ["demo:authorized-read"]},
            "capability": {"status": "supported", "evidence_refs": ["demo:read-contract"]},
            "dependencies": [], **changes}


def goal(name="issue-A"):
    return {"goal_id": name, "source_ref": "demo:original-request", "statement": "Correct this concrete defect",
            "owner_ref": "demo:owner", "original_refs": ["demo:original-object"],
            "phase_refs": ["demo:diagnostic-task", "demo:repair-task"],
            "criteria": [{"id": "root", "criterion_ref": "demo:issue-specific-root-verification"}],
            "category": "demo:browsing-only", "relations": [], "repairs": [], "reviews": []}


def document(*goals):
    return {"format_version": 1, "revision": 1, "change_id": "seed",
            "authority": {"owner_ref": "demo:owner", "storage_ref": "demo:private-goals"},
            "goals": list(goals) or [goal()]}


def review(**changes):
    return {"id": "review-1", "trigger": {"kind": "return", "ref": "demo:actual-return"},
            "reviewed_at": "2030-01-01T10:00:00Z", "basis_refs": ["demo:actual-return#sha256:v1"],
            "rationale": "The finite repair passed, but the original cause is still unknown",
            "criteria": [{"id": "root", "status": "unknown", "reason": "No original-scenario retest",
                          "evidence_refs": ["demo:finite-test"]}],
            "causal_questions": [{"id": "cause", "question": "What caused the original failure?",
                                  "status": "open", "required_for_closure": True,
                                  "evidence_refs": ["demo:original-failure"]}],
            "paths": [path()], "attempts": [],
            "decision": {"kind": "act", "path_id": "inspect", "owner_ref": "demo:investigator",
                         "reason": "This read distinguishes the remaining explanations"}, **changes}


def attempt(result, effect=None, basis=None):
    return {"id": "attempt-1", "action": effect or action(), "result": result,
            "basis_refs": basis or review()["basis_refs"], "evidence_refs": ["demo:original-attempt-result"]}


def wait():
    return {"kind": "wait", "owner_ref": "demo:original-owner", "reason": "The required original event is unavailable",
            "resume_when": [{"source_ref": "demo:original-event", "condition": "Original read-only event diagnostics become visible",
                             "owner_ref": "demo:event-provider"}]}


class GoalContinuityTests(unittest.TestCase):
    def evaluate(self, value=None, doc=None, name="issue-A"):
        result = files.handle({"operation": "goal-review", "goals": doc or document(),
                               "goal_id": name, "review": value or review()})
        self.assertEqual(result["status"], "goal_review", result)
        self.assertFalse(result["domain_action_performed"])
        self.assertFalse(result["action_executed"])
        return result

    def test_finite_task_completed_does_not_satisfy_root_goal(self):
        value = review(decision={"kind": "close", "owner_ref": "demo:owner",
                                "reason": "Finite task completed", "acceptance_ref": "demo:phase-pass"})
        result = self.evaluate(value)
        self.assertEqual(result["goal_state"], "OPEN")
        self.assertIn("root_criteria_or_required_cause_unresolved", result["decision_issues"])

    def test_repaired_side_defect_leaves_original_cause_and_next_probe(self):
        g = goal()
        g["repairs"] = [{"repair_ref": "demo:side-defect-patch", "version_refs": ["demo:release-2"],
                         "node_refs": ["demo:node"], "verification_refs": ["demo:side-test-pass"],
                         "recurrence_refs": [], "impact_refs": ["demo:partial-result-impact"]}]
        result = self.evaluate(doc=document(g))
        self.assertEqual(result["unresolved_causal_questions"], ["cause"])
        self.assertEqual(result["selected_next_action"]["path_id"], "inspect")
        self.assertEqual(result["goal_state"], "OPEN")

    def test_denied_action_stays_stopped_and_read_only_exploration_continues(self):
        value = review(paths=[path("write", action=action("protected", "write")), path()],
                       attempts=[attempt("denied", action("protected", "write"))])
        result = self.evaluate(value)
        self.assertIn("original_denial_retained", result["paths"][0]["reasons"])
        self.assertTrue(result["decision_supported"])
        self.assertEqual(result["selected_next_action"]["action"]["effect"], "read")
        value["decision"] = wait()
        self.assertIn("independent_evidence_path_available", self.evaluate(value)["decision_issues"])

    def test_unavailable_evidence_keeps_owner_observable_recovery_without_loop(self):
        value = review(paths=[path(capability={"status": "unavailable", "evidence_refs": ["demo:tool-contract"]})],
                       decision=wait())
        g = goal(); g["reviews"] = [value]
        next_review = dict(value, id="review-2", trigger={"kind": "natural_turn", "ref": "demo:existing-hourly-turn"})
        result = self.evaluate(next_review, document(g))
        self.assertTrue(result["decision_supported"])
        self.assertEqual(result["goal_state"], "OPEN")
        self.assertFalse(result["evidence_changed"])
        self.assertFalse(result["record_needed"])
        self.assertTrue(result["wait"]["resume_when"])

    def test_unknown_checks_original_result_before_repeat_even_with_new_evidence(self):
        value = review(paths=[path("write", action=action("protected", "write")), path()],
                       basis_refs=["demo:new-relevant-observation"],
                       attempts=[attempt("unknown", action("protected", "write"))])
        result = self.evaluate(value)
        self.assertIn("inspect_original_effect_before_any_retry", result["paths"][0]["reasons"])
        self.assertTrue(result["decision_supported"])

    def test_retry_uses_effect_and_evidence_not_new_path_task_or_trigger_labels(self):
        value = review(attempts=[attempt("failed")])
        g = goal(); g["reviews"] = [value]
        next_review = review(id="review-2", paths=[path("renamed", action=action())],
                             trigger={"kind": "resume", "ref": "demo:new-task"},
                             decision={"kind": "act", "path_id": "renamed", "owner_ref": "demo:owner", "reason": "retry"})
        self.assertFalse(self.evaluate(next_review, document(g))["decision_supported"])
        next_review["basis_refs"] += ["demo:actual-corrected-input#sha256:v2"]
        self.assertTrue(self.evaluate(next_review, document(g))["decision_supported"])

    def test_unrelated_consensus_revision_is_not_new_evidence_for_a_repeated_effect(self):
        old = review(attempts=[attempt('failed')]); g = goal(); g['reviews'] = [old]
        current = review(id='review-2', basis_refs=[*old['basis_refs'], 'demo:unrelated-consensus-revision-2'])
        current['paths'][0]['basis_refs'] = old['basis_refs']
        self.assertFalse(self.evaluate(current, document(g))['decision_supported'])
        current['paths'][0]['basis_refs'] = ['demo:actual-corrected-input#sha256:v2']
        self.assertTrue(self.evaluate(current, document(g))['decision_supported'])

    def closed_review(self):
        value = review(paths=[], decision={"kind": "close", "owner_ref": "demo:owner",
            "reason": "Issue-specific root criteria independently verified", "acceptance_ref": "demo:actual-owner-decision"})
        value["criteria"][0].update(status="met", evidence_refs=["demo:exact-issue-retest"])
        value["causal_questions"][0].update(status="answered", evidence_refs=["demo:causal-proof"])
        return value

    def test_each_issue_has_own_closure_and_shared_hypothesis_does_not_close_peer(self):
        a, b = goal(), goal("issue-B")
        a["relations"] = [{"issue_ref": "demo:issue-B", "status": "hypothesis",
                           "statement": "Possibly related", "evidence_refs": []}]
        a["reviews"] = [self.closed_review()]
        doc = document(a, b)
        self.assertEqual(self.evaluate(a["reviews"][-1], doc)["goal_state"], "CLOSED_IN_SUPPLIED_RECORDS")
        self.assertEqual(self.evaluate(doc=doc, name="issue-B")["goal_state"], "OPEN")

    def test_new_issue_does_not_clear_original_effect_denial(self):
        a, b = goal(), goal("issue-B")
        a["reviews"] = [review(attempts=[attempt("denied")])]
        self.assertIn("original_denial_retained", self.evaluate(doc=document(a, b), name="issue-B")["paths"][0]["reasons"])

    def test_slash_component_collision_cannot_overwrite_original_denial(self):
        a, b = goal('issue/segment'), goal('issue')
        old = review(id='round', attempts=[dict(attempt('denied'), id='attempt')])
        other = review(id='segment/round', attempts=[dict(attempt('done'), id='attempt')])
        a['reviews'] = [old]; b['reviews'] = [other]
        current = review(id='new-review', basis_refs=['demo:actual-new-input'])
        result = self.evaluate(current, document(a, b), name='issue')
        self.assertFalse(result['decision_supported'])
        self.assertIn('original_denial_retained', result['paths'][0]['reasons'])

    def test_ambiguous_legacy_reference_cannot_reconcile_unknown_but_structured_reference_can(self):
        a, b = goal('issue/segment'), goal('issue')
        a['reviews'] = [review(id='round', attempts=[dict(attempt('unknown'), id='attempt')])]
        b['reviews'] = [review(id='segment/round', attempts=[dict(attempt('done', action('other-effect')), id='attempt')])]
        current, body, record = self.dependency_review()
        current.update(id='new-review', basis_refs=['demo:actual-original-effect-new-proof'], paths=[path()])
        current['decision']['path_id'] = 'inspect'
        reference = 'issue/segment/round/attempt'
        body['resource'].update(original_attempt_ref=reference, effect_outcome='not_executed', effect_action=action())
        record['resource'] = copy.deepcopy(body['resource'])
        proof = {'record':record,'expected_scope':copy.deepcopy(body['resource']),'max_age_seconds':300}
        resolution = {'id':'original-inspected','attempt_ref':reference,'result':'not_executed','action':action(),
                      'evidence_refs':['demo:original-object'],'proof':proof}
        current['reconciliations'] = [resolution]; self.bind_native(current,body,record)
        result = self.evaluate(current,document(a,b),name='issue')
        self.assertFalse(result['decision_supported'])
        self.assertTrue(result['reconciliation_gaps'])
        reference = {'goal_id':'issue/segment','review_id':'round','attempt_id':'attempt'}
        resolution['attempt_ref'] = reference
        body['resource']['original_attempt_ref'] = reference
        record['resource'] = copy.deepcopy(body['resource']);proof['expected_scope'] = copy.deepcopy(body['resource'])
        self.bind_native(current,body,record)
        self.assertTrue(self.evaluate(current,document(a,b),name='issue')['decision_supported'])

    def test_recurrence_reopens_same_issue_and_cannot_reuse_prior_closure(self):
        old = self.closed_review(); g = goal(); g["reviews"] = [old]
        value = dict(old, id="review-2", trigger={"kind": "recurrence", "ref": "demo:actual-new-failure"},
                     basis_refs=[*old["basis_refs"], "demo:recurrence#v2"])
        result = self.evaluate(value, document(g))
        self.assertEqual(result["goal_state"], "OPEN")
        self.assertIn("recurrence_requires_fresh_issue_verification", result["decision_issues"])

    def test_user_stop_overrides_hourly_fallback_until_exact_explicit_resume(self):
        stopped = review(decision=wait(), user_control={"id": "stop-1", "kind": "user_stop", "source_ref": "demo:actual-user-stop"})
        g, unrelated = goal(), goal("issue-B"); g["reviews"] = [stopped]
        doc = document(g, unrelated)
        natural = review(id="review-2", trigger={"kind": "natural_turn", "ref": "demo:hourly"})
        result = self.evaluate(natural, doc)
        self.assertEqual(result["goal_state"], "USER_STOPPED")
        self.assertFalse(result["decision_supported"])
        self.assertTrue(self.evaluate(doc=doc, name="issue-B")["decision_supported"])
        natural["user_control"] = {"id": "resume-1", "kind": "user_resume", "supersedes": "another-stop", "source_ref": "demo:resume"}
        self.assertFalse(self.evaluate(natural, doc)["decision_supported"])
        natural["user_control"]["supersedes"] = "stop-1"
        self.assertTrue(self.evaluate(natural, doc)["decision_supported"])

    def test_explicit_node_proof_reuses_core_hash_scope_and_source_observation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            value, body, record = self.dependency_review()
            raw = json.dumps(body).encode(); (root / "result.json").write_bytes(raw)
            record.update(source="node", node_path="result.json", digest=hashlib.sha256(raw).hexdigest())
            proof = value["paths"][0]["dependencies"][0]["proof"]
            proof["project"] = {"root": str(root)}
            self.assertTrue(self.evaluate(value)["decision_supported"])
            (root / "result.json").write_text('{"result":"unknown"}')
            self.assertFalse(self.evaluate(value)["decision_supported"])

    def test_prior_causal_question_cannot_vanish_after_phase_return(self):
        g = goal(); g["reviews"] = [review()]
        result = files.handle({"operation": "goal-review", "goals": document(g), "goal_id": g["goal_id"],
                               "review": review(id="review-2", causal_questions=[], paths=[])})
        self.assertEqual(result["status"], "invalid_record")

    def dependency_review(self):
        value = review()
        scope = {"kind": "report", "id": "producer-result", "node_id": "demo:node",
                 "product_version": "1.0.0", "content_ref": "demo:source-v1", "request_ref": "demo:request", "input_revision": 2}
        body = {"resource": scope, "result": "passed", "status": "completed", "version": "1.0.0",
                "observed_at": "2030-01-01T09:59:00Z"}
        record = {"source": "codex", "kind": "observation", "result": "passed", "version": "1.0.0",
                  "task_ref": "codex:thread:producer", "native_item_id": "command-1", "resource": copy.deepcopy(scope)}
        dep = {"ref": "demo:producer", "status": "satisfied", "reason": "Consumer needs exact current result",
               "evidence_refs": ["demo:actual-return"],
               "proof": {"record": record, "expected_scope": copy.deepcopy(scope), "max_age_seconds": 300}}
        value["paths"] = [path("dependent", dependencies=[dep]), path()]
        value["decision"].update(path_id="dependent")
        return value, body, record

    def bind_native(self, value, body, record, **changes):
        raw = json.dumps(body)
        record["digest"] = hashlib.sha256(raw.encode()).hexdigest()
        item = {"id": "command-1", "type": "commandExecution", "status": "completed", "exitCode": 0,
                "command": "demo report 1.0.0", "output": {"text": raw, "truncated": False}, **changes}
        value["evidence_context"] = {"native_sources": [{"schemaVersion": 1, "thread": {"id": "producer"},
            "turns": [{"id": "turn-result", "items": [item]}]}]}

    def test_actual_bound_fresh_result_allows_dependent_step(self):
        value, body, record = self.dependency_review(); self.bind_native(value, body, record)
        self.assertTrue(self.evaluate(value)["decision_supported"])

    def test_verified_return_refreshes_original_blocked_item_and_selects_successor(self):
        old, _, _ = self.dependency_review(); old['decision'] = wait()
        old['paths'][0]['dependencies'][0]['status'] = 'missing'
        old['paths'][1]['capability']['status'] = 'unavailable'
        g = goal(); g['reviews'] = [old]
        self.assertTrue(self.evaluate(old, document(g))['decision_supported'])
        current, body, record = self.dependency_review()
        current.update(id='review-2', basis_refs=['demo:actual-new-verification#sha256:v2'])
        self.bind_native(current, body, record)
        result = self.evaluate(current, document(g))
        self.assertTrue(result['decision_supported'])
        self.assertIsNone(result['wait'])
        self.assertEqual(result['selected_next_action']['path_id'], 'dependent')

    def test_exact_original_unknown_resolution_refreshes_projection_without_effect_replay(self):
        for outcome in ('not_executed', 'committed'):
            with self.subTest(outcome=outcome):
                old = review(attempts=[attempt('unknown')], decision=wait())
                g = goal(); g['reviews'] = [old]
                current, body, record = self.dependency_review()
                current.update(id='review-2', basis_refs=['demo:actual-original-effect-inspection#sha256:v2'], paths=[path()])
                current['decision']['path_id'] = 'inspect'
                ref = 'issue-A/review-1/attempt-1'
                body['resource'].update(original_attempt_ref=ref, effect_outcome=outcome, effect_action=action())
                record['resource'] = copy.deepcopy(body['resource'])
                proof = {'record': record, 'expected_scope': copy.deepcopy(body['resource']), 'max_age_seconds': 300}
                current['reconciliations'] = [{'id': 'original-inspected', 'attempt_ref': ref, 'result': outcome,
                    'action': action(), 'evidence_refs': ['demo:original-object-result'], 'proof': proof}]
                self.bind_native(current, body, record)
                result = self.evaluate(current, document(g))
                self.assertNotIn('inspect_original_effect_before_any_retry', result['paths'][0]['reasons'])
                self.assertEqual(result['decision_supported'], outcome == 'not_executed')
                if outcome == 'committed':
                    self.assertIn('original_effect_already_committed_do_not_repeat', result['paths'][0]['reasons'])
                current['reconciliations'][0]['proof']['expected_scope'] = 'not-an-object'
                self.assertFalse(self.evaluate(current, document(g))['decision_supported'])

    def test_stale_missing_unknown_ack_inflight_wrong_node_version_input_select_read_first(self):
        for change in ("stale", "missing", "unknown", "ack", "inflight", "node", "version", "input", "truncated"):
            with self.subTest(change=change):
                value, body, record = self.dependency_review()
                native_changes = {}
                if change == "stale": body["observed_at"] = "2029-12-01T00:00:00Z"
                if change == "unknown": body.update(result="unknown", status="unknown")
                if change == "ack": body = {"accepted": True}
                if change == "inflight": body["status"] = "in_progress"
                if change == "node": body["resource"]["node_id"] = "demo:other-node"
                if change == "version": body["resource"]["product_version"] = "0.9.0"
                if change == "input": body["resource"]["input_revision"] = 1
                self.bind_native(value, body, record, **native_changes)
                if change == "missing": value["evidence_context"]["native_sources"] = []
                if change == "truncated": value["evidence_context"]["native_sources"][0]["turns"][0]["items"][0]["output"]["truncated"] = True
                result = self.evaluate(value)
                self.assertFalse(result["decision_supported"])
                self.assertIn("dependency_result_unverified:demo:producer", result["paths"][0]["reasons"])
                value["decision"]["path_id"] = "inspect"
                self.assertTrue(self.evaluate(value)["decision_supported"])

    def test_old_successor_or_stop_cannot_mask_a_new_return(self):
        for settled_kind in ("successor_executed", "legal_stop_recorded"):
            request = view_request()
            request["progress"][-1].update(kind=settled_kind, reason="No available evidence at that time")
            self.assertFalse(files.handle(request)["tasks"][0]["next_action_review_needed"])
            request["progress"].append(event(4, "diagnostic_returned", result="cause_still_unknown"))
            self.assertTrue(files.handle(request)["tasks"][0]["next_action_review_needed"])

    def test_goal_store_cas_history_and_no_mutation_of_original_objects(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve(); target = root / "goals.json"
            original = root / "original-denied-object.json"; original.write_text('{"accepted":false}')
            before = original.read_bytes()
            binding = {"kind": "goals", "path": str(target), "format_version": 1}
            doc = document(); doc["goals"][0]["original_refs"] = [str(original)]
            request = {"operation": "commit", "binding": binding, "expected_revision": 0,
                       "create": True, "change_id": "seed", "document": doc}
            self.assertEqual(files.handle(request)["status"], "committed")
            next_doc = copy.deepcopy(doc); next_doc.update(revision=2, change_id="review")
            next_doc["goals"][0]["reviews"].append(review())
            request.update(expected_revision=1, change_id="review", document=next_doc)
            self.assertEqual(files.handle(request)["status"], "committed")
            self.assertEqual(files.handle(request)["status"], "revision_conflict")
            dropped = copy.deepcopy(next_doc); dropped.update(revision=3, change_id="drop")
            dropped["goals"][0]["reviews"] = []
            request.update(expected_revision=2, change_id="drop", document=dropped)
            self.assertEqual(files.handle(request)["status"], "invalid_record")
            self.assertEqual(original.read_bytes(), before)
            self.assertFalse((root / "task-runtime").exists())


class GoalInstalledTests(FixtureCase):
    def test_normal_version_bound_cli_persists_and_reviews_concrete_issue(self):
        revision = seed_runtime(self.repo); runtime.install(self.a, revision)
        target = self.root / "private-goal.json"
        doc = document(); doc["goals"][0]["reviews"] = [review()]
        payload = {"operation": "commit", "binding": {"kind": "goals", "path": str(target), "format_version": 1},
                   "expected_revision": 0, "create": True, "change_id": "seed", "document": doc}
        command = [sys.executable, "-B", str(runtime.launcher_path(self.a)), "--home", str(self.a.home), "coordination"]
        saved = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True, timeout=10)
        self.assertEqual(saved.returncode, 0, saved.stderr)
        payload = {"operation": "goal-review", "goals": json.loads(target.read_text()),
                   "goal_id": "issue-A", "review": review()}
        checked = subprocess.run(command, input=json.dumps(payload), text=True, capture_output=True, timeout=10)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        output = json.loads(checked.stdout)
        self.assertEqual(output["tool_revision"], revision)
        self.assertTrue(output["decision_supported"])
        self.assertEqual(output["selected_next_action"]["path_id"], "inspect")
        self.assertFalse((self.a.codex_home / "task-runtime").exists())


if __name__ == "__main__":
    unittest.main()
