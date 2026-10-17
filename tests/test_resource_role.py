"""Synthetic resource-role relations, never real Docker/database acceptance."""
import contextlib
from datetime import datetime, timedelta, timezone
import hashlib
import io
import json
import unittest
from unittest import mock

import test_execution_check as fixtures
from test_execution_check import check, framework


def body_update(request, ref, value):
    body = json.dumps(value, ensure_ascii=False)
    record = next(r for r in request["context"]["evidence"] if r["ref"] == ref)
    record["digest"] = hashlib.sha256(body.encode()).hexdigest()
    item = next(i for i in request["context"]["native_sources"][0]["turns"][0]["items"]
                if i["id"] == ref)
    if item["type"] == "commandExecution":
        item["output"]["text"] = body
    else:
        item["text"] = body


def body_get(request, ref):
    item = next(i for i in request["context"]["native_sources"][0]["turns"][0]["items"]
                if i["id"] == ref)
    return json.loads(item["output"]["text"] if item["type"] == "commandExecution" else item["text"])


def maintenance_case(name="local-mysql"):
    helper = fixtures.WorkspaceBoundaryChecks()
    request = helper.local_case()
    target = {"kind":"service", "id":name}
    request["project"]["scopes"] = [{**target,"access":"write"}]
    request["task"]["scopes"] = [{**target,"access":"write"}]
    request["action"].update(resource=target,capabilities=[])
    request["context"]["evidence"][0]["resource"] = target
    contract = {"resource":target,"role":"rebuildable_model","retention":"none",
                "rebuild_ref":"repo:authorised-service:compose-and-source",
                "availability":"restart_allowed"}
    observation = {"resource":target,"observed_at":datetime.now(timezone.utc).isoformat(),
                   "target_verified":True,"dependencies":"quiesced","retention_conflict":False,
                   "normal_baseline":True,"goal_result":"not_run"}
    helper.add_user_evidence(request,"codex:contract:resource","resource_contract",
                             json.dumps(contract),target=target)
    helper.add_user_evidence(request,"codex:observe:resource","resource_observation",
                             json.dumps(observation),result="observed",target=target)
    item = request["context"]["native_sources"][0]["turns"][0]["items"][-1]
    body = item["text"]
    item.clear()
    item.update(id="codex:observe:resource",type="commandExecution",status="completed",exitCode=0,
                command="synthetic bounded resource observation",output={"text":body,"truncated":False})
    request["action"]["maintenance"] = {"contract_ref":"codex:contract:resource",
        "observation_ref":"codex:observe:resource","strategy":"rebuild",
        "max_age_seconds":86400}
    return request


def alter(request, ref, **updates):
    value = body_get(request,ref)
    value.update(updates)
    body_update(request,ref,value)


def complete_case(request):
    fixtures.WorkspaceBoundaryChecks().add_user_evidence(request,"codex:user:capacity","goal",
        "Capacity must meet the intended workload with normal browser and IDE baseline")
    request["action"]["maintenance"].update(claim="complete",goal_ref="codex:user:capacity")
    next(r for r in request["context"]["evidence"]
         if r["ref"]=="codex:observe:resource")["related_ref"]="codex:user:capacity"
    alter(request,"codex:observe:resource",goal_result="passed")
    return request


class ResourceRoleChecks(unittest.TestCase):
    def test_rebuildable_mysql_and_starrocks_use_rebuild_without_production_windows(self):
        for name in ("local-mysql","local-starrocks","production-named-local-model"):
            with self.subTest(name=name):
                out=check.evaluate(maintenance_case(name))
                self.assertEqual(out["status"],"ready")
                self.assertEqual(out["resource_route"],"stop_rebuild_and_verify")

    def test_rebuildable_contract_rejects_unfounded_hot_takeover_gate(self):
        request=maintenance_case()
        request["action"]["maintenance"]["strategy"]="hot_takeover"
        self.assertIn("unsupported_continuity_requirement",check.evaluate(request)["blocked"])

    def test_actual_continuity_requirement_can_use_preservation(self):
        request=maintenance_case()
        alter(request,"codex:contract:resource",role="retained_state",retention="required",
              availability="continuous_required",rebuild_ref=None)
        request["action"]["maintenance"]["strategy"]="hot_takeover"
        self.assertEqual(check.evaluate(request)["resource_route"],"preserve_with_continuity")
        self.assertEqual(check.evaluate(request)["status"],"ready")

    def test_external_personal_and_retained_scope_do_not_inherit_discard_permission(self):
        for role in ("external_business","personal","retained_state"):
            request=maintenance_case()
            alter(request,"codex:contract:resource",role=role,retention="required")
            self.assertIn("rebuild_requires_discardable_managed_scope",check.evaluate(request)["blocked"])

    def test_unknown_role_is_discovered_without_becoming_production_or_deletion(self):
        request=maintenance_case()
        alter(request,"codex:contract:resource",role="unknown",retention="unknown")
        request["action"]["maintenance"]["strategy"]="discover"
        request["action"]["kind"]="read"
        request["action"]["workspace"]["side_effect"]="read"
        out=check.evaluate(request)
        self.assertIn("resource_role_unverified",out["missing"])
        self.assertNotIn("unsupported_continuity_requirement",out["blocked"])

    def test_role_does_not_grant_local_authority(self):
        request=maintenance_case()
        request["action"]["workspace"].pop("authorization_ref")
        self.assertIn("local_action_authorization",check.evaluate(request)["missing"])

    def test_reconstruction_source_and_interruption_are_actual_conditions(self):
        request=maintenance_case()
        alter(request,"codex:contract:resource",rebuild_ref=None,availability="unknown")
        out=check.evaluate(request)
        self.assertIn("reconstruction_source",out["missing"])
        self.assertIn("rebuild_interruption_contract",out["missing"])

    def test_unknown_target_and_inflight_dependencies_hold_only_affected_operation(self):
        request=maintenance_case()
        alter(request,"codex:observe:resource",target_verified=False,dependencies="unknown")
        out=check.evaluate(request)
        self.assertIn("exact_resource_identity",out["missing"])
        self.assertIn("affected_dependency_safe_point",out["missing"])
        alter(request,"codex:observe:resource",target_verified=True,dependencies="busy")
        self.assertIn("affected_dependency_safe_point",check.evaluate(request)["missing"])
        alter(request,"codex:observe:resource",dependencies="clear")
        self.assertEqual(check.evaluate(request)["status"],"ready")

    def test_observed_unique_content_prevents_discarding_on_old_contract(self):
        request=maintenance_case()
        alter(request,"codex:observe:resource",retention_conflict=True)
        self.assertIn("rebuild_conflicts_with_observed_retention",check.evaluate(request)["blocked"])

    def test_stale_original_observation_is_not_refreshed_by_reading_it(self):
        request=maintenance_case()
        alter(request,"codex:observe:resource",
              observed_at=(datetime.now(timezone.utc)-timedelta(days=2)).isoformat())
        self.assertIn("resource_observation_stale_or_unverified",check.evaluate(request)["missing"])

    def test_temporary_chrome_shutdown_is_relief_not_capacity_completion(self):
        request=complete_case(maintenance_case())
        request["action"]["maintenance"]["strategy"]="temporary_relief"
        alter(request,"codex:observe:resource",normal_baseline=False)
        out=check.evaluate(request)
        self.assertIn("temporary_relief_not_capacity_solution",out["blocked"])
        self.assertIn("resource_goal_under_normal_baseline",out["missing"])

    def test_completion_needs_goal_link_normal_baseline_and_real_observed_result(self):
        request=complete_case(maintenance_case())
        self.assertEqual(check.evaluate(request)["status"],"ready")
        alter(request,"codex:observe:resource",goal_result="not_run")
        self.assertIn("resource_goal_under_normal_baseline",check.evaluate(request)["missing"])
        alter(request,"codex:observe:resource",goal_result="passed",normal_baseline=False)
        self.assertIn("resource_goal_under_normal_baseline",check.evaluate(request)["missing"])
        alter(request,"codex:observe:resource",normal_baseline=True)
        request["context"]["evidence"][-2]["related_ref"]="codex:user:another-goal"
        self.assertIn("resource_result_goal_mismatch",check.evaluate(request)["blocked"])

    def test_irreversible_delete_confirmation_stays_independent_of_rebuildability(self):
        request=maintenance_case()
        request["action"]["workspace"]["side_effect"]="irreversible_delete"
        request["context"]["evidence"][0]["workspace"]["side_effects"].append("irreversible_delete")
        self.assertIn("irreversible_action_confirmation",check.evaluate(request)["missing"])
        fixtures.WorkspaceBoundaryChecks().add_user_evidence(request,"codex:user:delete","confirmation",
            "Confirm this exact object and deletion consequences","granted",request["action"]["resource"])
        request["action"]["workspace"]["confirmation_ref"]="codex:user:delete"
        self.assertEqual(check.evaluate(request)["status"],"ready")

    def test_platform_native_confirmation_is_not_replaced_by_resource_contract(self):
        request=maintenance_case()
        request["action"]["access"]=fixtures.AccessRoutingChecks().access_case()["action"]["access"]
        request["action"]["access"]["platform_requirement"]="confirmation"
        self.assertIn("native_action_confirmation",check.evaluate(request)["missing"])

    def test_platform_denial_and_unknown_effect_survive_resource_classification(self):
        request=maintenance_case()
        request["context"]["tool_failure"]={"kind":"platform_denied","action_kind":"write",
            "resource":request["action"]["resource"],"side_effect":"local_change"}
        self.assertIn("platform_denied_action",check.evaluate(request)["blocked"])
        request["context"].pop("tool_failure")
        request["context"]["native_operations"]=[{"ref":"codex:op:old","project_id":"framework",
            "task_ref":request["task"]["native_ref"],"resource":request["action"]["resource"],
            "state":"unknown","effect_key":request["action"]["effect_key"]}]
        self.assertIn("duplicate_or_uncertain_effect",check.evaluate(request)["blocked"])

    def test_rebuild_cannot_hide_as_read_or_expand_scope_or_destination(self):
        request=maintenance_case()
        request["action"]["kind"]="read";request["action"]["workspace"]["side_effect"]="read"
        self.assertIn("resource_strategy_requires_actual_effect",check.evaluate(request)["blocked"])
        request=maintenance_case()
        request["action"]["resource"]={"kind":"service","id":"unrelated-personal"}
        self.assertIn("outside_task_scope",check.evaluate(request)["blocked"])
        request=maintenance_case()
        request["action"]["workspace"].update(destination_node="new-remote",side_effect="transfer")
        self.assertIn("cross_environment_authorization",check.evaluate(request)["missing"])

    def test_agent_restatement_and_failed_or_truncated_observation_are_not_source_facts(self):
        request=maintenance_case()
        request["context"]["native_sources"][0]["turns"][0]["items"][-2]["type"]="agentMessage"
        self.assertIn("native_resource_contract_source",check.evaluate(request)["missing"])
        for field,value in (("exitCode",1),("status","failed")):
            request=maintenance_case()
            request["context"]["native_sources"][0]["turns"][0]["items"][-1][field]=value
            self.assertIn("unverified_resource_observation_source",check.evaluate(request)["missing"])
        request=maintenance_case()
        request["context"]["native_sources"][0]["turns"][0]["items"][-1]["output"]["truncated"]=True
        self.assertIn("unverified_resource_observation_source",check.evaluate(request)["missing"])

    def test_digest_resource_and_task_binding_are_not_caller_role_labels(self):
        request=maintenance_case()
        request["context"]["evidence"][-1]["digest"]="0"*64
        self.assertIn("unverified_resource_observation_source",check.evaluate(request)["missing"])
        request=maintenance_case()
        alter(request,"codex:contract:resource",resource={"kind":"service","id":"other"})
        self.assertIn("resource_contract_resource_mismatch",check.evaluate(request)["blocked"])
        request=maintenance_case()
        request["context"]["evidence"][-2]["task_ref"]="codex:thread:other"
        self.assertIn("unresolved_resource_contract_reference",check.evaluate(request)["missing"])

    def test_both_command_fact_kinds_require_digest_without_changing_legacy_sources(self):
        for ref,reason in (("codex:observe:resource","resource_observation_digest"),
                           ("codex:contract:resource","resource_contract_digest")):
            for supplied in (None, ""):
                with self.subTest(ref=ref,digest=supplied):
                    request=maintenance_case()
                    record=next(r for r in request["context"]["evidence"] if r["ref"]==ref)
                    item=next(i for i in request["context"]["native_sources"][0]["turns"][0]["items"]
                              if i["id"]==ref)
                    if ref=="codex:contract:resource":
                        body=item["text"];item.clear()
                        item.update(id=ref,type="commandExecution",status="completed",exitCode=0,
                                    command="synthetic structured contract read",
                                    output={"text":body,"truncated":False})
                    if supplied is None:record.pop("digest")
                    else:record["digest"]=supplied
                    self.assertIn(reason,check.evaluate(request)["missing"])
                    body=item["output"]["text"]
                    record["digest"]=hashlib.sha256(body.encode()).hexdigest()
                    self.assertEqual(check.evaluate(request)["status"],"ready")

    def test_bad_shapes_and_types_return_structured_cli_invalid(self):
        mutations=[
            lambda r:r["action"].update(maintenance=[]),
            lambda r:r["action"]["maintenance"].update(max_age_seconds=True),
            lambda r:r["action"]["maintenance"].update(strategy=[]),
            lambda r:body_update(r,"codex:contract:resource",[]),
            lambda r:alter(r,"codex:observe:resource",target_verified="true"),
            lambda r:r["action"]["maintenance"].update(internal=True),
        ]
        for mutate in mutations:
            request=maintenance_case();mutate(request)
            output=io.StringIO()
            with contextlib.redirect_stdout(output),mock.patch("sys.stdin",io.StringIO(json.dumps(request))):
                self.assertEqual(framework.main(["check"]),1)
            self.assertEqual(json.loads(output.getvalue())["status"],"invalid")

    def test_no_maintenance_preserves_complete_legacy_output(self):
        request=fixtures.WorkspaceBoundaryChecks().local_case()
        self.assertEqual(check.evaluate(request),{"status":"ready","blocked":[],"missing":[],
            "deviations":[],"continuous_eligible":False,
            "meaning":"source_excerpt_and_input_consistency_only; provenance, authorization and goal semantics remain external"})

    def test_progress_propagates_resource_route_for_independent_step(self):
        request=maintenance_case()
        request["phase"]="progress"
        request["progress"]={"checkpoint":"after_failure","claim":"working",
                             "steps":[{"id":"local","action":request["action"]}]}
        out=check.evaluate(request)
        self.assertEqual(out["status"],"ready")
        self.assertEqual(out["steps"][0]["resource_route"],"stop_rebuild_and_verify")


if __name__=="__main__":
    unittest.main()
