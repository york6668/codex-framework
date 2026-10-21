"""Explicit report scope cannot be relabelled; no native capability is inferred."""
import copy
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

import execution_check as check
import source_evidence as source
import test_execution_check as fixtures
import test_business_evidence as business_fixtures
import test_progress_check as progress_fixtures
import test_resource_role as maintenance_fixtures

ROOT = Path(check.__file__).resolve().parents[1]


class OutcomeScopeBindingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='outcome-scope-')
        self.root = Path(self.temp.name).resolve()
        self.addCleanup(self.temp.cleanup)

    def record(self, body, resource):
        path = self.root / 'result.json'
        path.write_text(json.dumps(body))
        return {'source': 'node', 'kind': 'adoption', 'result': 'applied', 'version': 'one',
                'node_path': path.name, 'digest': hashlib.sha256(path.read_bytes()).hexdigest(),
                'resource': resource}

    def test_hash_and_version_do_not_transfer_completion_to_another_object(self):
        original = {'kind': 'repo', 'id': 'original', 'path': 'entry'}
        for claimed in ({**original, 'id': 'another'}, {**original, 'kind': 'runtime'},
                        {**original, 'path': 'another-entry'}, {**original, 'path': ''}):
            with self.subTest(claimed=claimed):
                record = self.record({'result': 'applied', 'version': 'one', 'resource': original}, claimed)
                self.assertFalse(source.verify({}, {'root': str(self.root)}, record))
        record = self.record({'result': 'applied', 'version': 'one', 'resource': original}, original)
        self.assertTrue(source.verify({}, {'root': str(self.root)}, record))

    def test_existing_resource_semantics_are_shared_without_new_dimensions(self):
        original = {'kind': 'repo', 'id': 'same', 'path': '', 'access': 'read'}
        claimed = {'kind': 'repo', 'id': 'same', 'access': 'read'}
        record = self.record({'result': 'applied', 'version': 'one', 'resource': original}, claimed)
        self.assertTrue(source.verify({}, {'root': str(self.root)}, record))
        self.assertEqual(check.resource(original), check.resource(claimed))
        broader = {**claimed, 'access': 'write'}
        record['resource'] = broader
        self.assertEqual(check.resource(original), check.resource(broader))
        self.assertFalse(source.verify({}, {'root': str(self.root)}, record))
        for value in ([], {'kind': 'repo'}, {'kind': 'repo', 'id': 'x', 'path': '../outside'}):
            with self.subTest(value=value):
                with self.assertRaises(check.CheckError):
                    check.resource(value)

    def test_unscoped_legacy_source_is_not_forced_into_a_new_report_schema(self):
        record = self.record({'result': 'applied', 'version': 'one'}, {'kind': 'repo', 'id': 'legacy'})
        self.assertTrue(source.verify({}, {'root': str(self.root)}, record))
        record.pop('resource')
        self.assertTrue(source.verify({}, {'root': str(self.root)}, record))

    def test_malformed_declared_scope_is_an_evidence_gap_not_an_exception(self):
        for declared in (None, [], [['kind', 'repo'], ['id', 'current']],
                         {'kind': 'repo'}, {'kind': 'repo', 'id': 'x', 'path': '/absolute'}):
            record = self.record({'result': 'applied', 'version': 'one', 'resource': declared},
                                 {'kind': 'repo', 'id': 'current'})
            with self.subTest(declared=declared):
                self.assertFalse(source.verify({}, {'root': str(self.root)}, record))

    def close_request(self):
        request = fixtures.ClosureChecks().close_case()
        request['project']['root'] = str(self.root)
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'adoption')
        original = {**request['issue']['resource'], 'path': 'scripts/other-entry.py'}
        report = {'result': 'applied', 'version': 'commit:abc', 'resource': original}
        record.update(self.record(report, request['issue']['resource']), version='commit:abc')
        return request, report, record

    def call(self, request):
        actual = subprocess.run([sys.executable, '-B', str(ROOT / 'scripts/codex_framework.py'), 'check'],
                                input=json.dumps(request), capture_output=True, text=True, timeout=10)
        self.assertEqual(actual.stderr, '')
        return actual.returncode, json.loads(actual.stdout)

    def test_normal_entry_does_not_close_target_from_another_objects_result(self):
        request, report, record = self.close_request()
        code, result = self.call(request)
        self.assertEqual(code, 2)
        self.assertEqual(result['status'], 'needs_evidence')
        self.assertFalse(result['continuous_eligible'])
        self.assertIn('unverified_adoption_source', result['missing'])
        # Original A remains valid as A evidence; it does not become B adoption.
        record['resource'] = report['resource']
        self.assertTrue(source.verify(request['context'], request['project'], record))
        code, result = self.call(request)
        self.assertEqual(code, 2)
        self.assertIn('adoption_resource_mismatch', result['blocked'])

    def test_actual_matching_target_result_remains_consumable_without_retesting_all_sources(self):
        request, report, record = self.close_request()
        report['resource'] = copy.deepcopy(request['issue']['resource'])
        path = self.root / record['node_path']; path.write_text(json.dumps(report))
        record['digest'] = hashlib.sha256(path.read_bytes()).hexdigest()
        code, result = self.call(request)
        self.assertEqual(code, 0)
        self.assertEqual(result['status'], 'ready')
        self.assertTrue(result['continuous_eligible'])
        self.assertEqual(len(request['issue']['independent_verification']['attempts']), 1)

    def native_record(self, body, claimed):
        text = body if isinstance(body, str) else json.dumps(body)
        item = {'id': 'native-result', 'type': 'commandExecution', 'status': 'completed',
                'exitCode': 0, 'command': 'offline recorded result one',
                'output': {'text': text, 'truncated': False}}
        record = {'source': 'codex', 'kind': 'adoption', 'task_ref': 'codex:thread:original',
                  'native_item_id': item['id'], 'resource': claimed, 'result': 'applied',
                  'version': 'one', 'digest': hashlib.sha256(text.encode()).hexdigest()}
        context = {'native_sources': [{'schemaVersion': 1, 'thread': {'id': 'original'},
                    'turns': [{'items': [item]}]}]}
        return context, record

    def test_native_structured_outcome_cannot_be_relabelled_either(self):
        original = {'kind': 'capability', 'id': 'original-entry', 'path': 'bounded-case'}
        for claimed in ({**original, 'id': 'other-entry'}, {**original, 'path': ''}):
            with self.subTest(claimed=claimed):
                context, record = self.native_record({'result': 'applied', 'version': 'one',
                    'resource': original}, claimed)
                self.assertFalse(source.verify(context, {}, record))
        context, record = self.native_record({'result': 'applied', 'version': 'one',
            'resource': original}, original)
        self.assertTrue(source.verify(context, {}, record))

    def test_native_duplicate_scope_is_not_last_value_wins(self):
        claimed = {'kind': 'capability', 'id': 'target'}
        body = ('{"result":"applied","version":"one","resource":{"kind":"capability","id":"source"},'
                '"resource":{"kind":"capability","id":"target"}}')
        context, record = self.native_record(body, claimed)
        self.assertFalse(source.verify(context, {}, record))
        context, record = self.native_record({'result': 'applied', 'version': 'one', 'resource': []}, claimed)
        self.assertFalse(source.verify(context, {}, record))

    def test_native_raw_and_unscoped_sources_keep_legacy_source_only_meaning(self):
        claimed = {'kind': 'repo', 'id': 'legacy'}
        for body in ('recorded output one', '{ordinary non-JSON CLI output one',
                     {'result': 'applied', 'version': 'one'}):
            with self.subTest(body=body):
                context, record = self.native_record(body, claimed)
                self.assertTrue(source.verify(context, {}, record))

    def test_normal_entry_binds_native_report_scope_and_accepts_real_target_binding(self):
        request = fixtures.ClosureChecks().close_case()
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'adoption')
        item = next(i for s in request['context']['native_sources'] for t in s['turns']
                    for i in t['items'] if i['id'] == record['native_item_id'])
        original = {**request['issue']['resource'], 'path': 'scripts/other-entry.py'}
        for scope, expected_exit in ((original, 2), (request['issue']['resource'], 0)):
            body = json.dumps({'result': 'applied', 'version': 'commit:abc', 'resource': scope})
            record['digest'] = hashlib.sha256(body.encode()).hexdigest()
            item['output'] = {'text': body, 'truncated': False}
            code, result = self.call(request)
            self.assertEqual(code, expected_exit)
            if expected_exit:
                self.assertIn('unverified_adoption_source', result['missing'])
                self.assertFalse(result['continuous_eligible'])
            else:
                self.assertEqual(result['status'], 'ready')

    def test_all_declared_conditions_survive_without_an_enumerated_dimension_list(self):
        base = {'kind': 'capability', 'id': 'same-object'}
        qualifiers = {'new-unlisted-condition': {'sample': ['only-this-case'], 'required': True}}
        original = {**base, **qualifiers}
        for claimed in (base, {**base, 'new-unlisted-condition': {'sample': ['other-case'], 'required': True}},
                        {**original, 'extra-unproved-condition': 'another'},
                        {**base, 'new-unlisted-condition': {'sample': ['only-this-case'], 'required': 1}}):
            with self.subTest(claimed=claimed):
                record = self.record({'result': 'applied', 'version': 'one', 'resource': original}, claimed)
                self.assertFalse(source.verify({}, {'root': str(self.root)}, record))
                context, native = self.native_record({'result': 'applied', 'version': 'one', 'resource': original}, claimed)
                self.assertFalse(source.verify(context, {}, native))
        record = self.record({'result': 'applied', 'version': 'one', 'resource': original}, original)
        self.assertTrue(source.verify({}, {'root': str(self.root)}, record))
        context, native = self.native_record({'result': 'applied', 'version': 'one', 'resource': original}, original)
        before = copy.deepcopy(native)
        self.assertTrue(source.verify(context, {}, native))
        self.assertEqual(native, before)

    def test_normal_entry_does_not_expand_same_object_completion_to_new_conditions(self):
        for channel in ('node', 'codex'):
            with self.subTest(channel=channel):
                request = fixtures.ClosureChecks().close_case()
                base = request['issue']['resource']
                original = {**base, 'applicability': {'case': 'observed', 'extent': 'bounded'}}
                target = {**base, 'applicability': {'case': 'different', 'extent': 'wider'}}
                request['issue']['resource'] = target
                for fact in request['context']['evidence']:
                    if fact.get('resource') == base:
                        fact['resource'] = target
                for case in request['issue']['local_verification']['cases']:
                    case['resource'] = target
                record = next(r for r in request['context']['evidence'] if r['kind'] == 'adoption')
                body = {'result': 'applied', 'version': 'commit:abc', 'resource': original}
                if channel == 'node':
                    path = self.root / 'qualified.json';path.write_text(json.dumps(body))
                    request['project']['root'] = str(self.root)
                    record.update(source='node', node_path=path.name, digest=hashlib.sha256(path.read_bytes()).hexdigest())
                else:
                    item = next(i for s in request['context']['native_sources'] for t in s['turns']
                                for i in t['items'] if i['id'] == record['native_item_id'])
                    text = json.dumps(body);item['output'] = {'text': text, 'truncated': False}
                    record['digest'] = hashlib.sha256(text.encode()).hexdigest()
                code, result = self.call(request)
                self.assertEqual(code, 2)
                self.assertIn('unverified_adoption_source', result['missing'])
                self.assertFalse(result['continuous_eligible'])
                # Matching the declared target remains consumable. This is a
                # generated source-consistency control, not proof of its truth.
                body['resource'] = target
                text = json.dumps(body)
                if channel == 'node':
                    path.write_text(text)
                else:
                    item['output'] = {'text': text, 'truncated': False}
                record['digest'] = hashlib.sha256(text.encode()).hexdigest()
                code, result = self.call(request)
                self.assertEqual(code, 0)
                self.assertEqual(result['status'], 'ready')

    def qualified_close(self):
        request = fixtures.ClosureChecks().close_case()
        original = copy.deepcopy(request['issue']['resource'])
        target = {**original, 'new-unlisted-condition': {'cases': ['different'], 'bounded': True}}
        request['issue']['resource'] = target
        for record in request['context']['evidence']:
            if record.get('resource') == original:
                record['resource'] = copy.deepcopy(target)
        for case in request['issue']['local_verification']['cases']:
            case['resource'] = copy.deepcopy(target)
        return request, target

    def test_consumer_does_not_expand_honestly_scoped_source_to_different_target(self):
        for channel in ('node', 'codex'):
            with self.subTest(channel=channel):
                request, target = self.qualified_close()
                original = {**target, 'new-unlisted-condition': {'cases': ['observed'], 'bounded': True}}
                record = next(r for r in request['context']['evidence'] if r['kind'] == 'adoption')
                record['resource'] = original
                body = json.dumps({'resource': original, 'result': 'applied', 'version': 'commit:abc'})
                record['digest'] = hashlib.sha256(body.encode()).hexdigest()
                if channel == 'node':
                    path = self.root / 'honest.json'; path.write_text(body)
                    request['project']['root'] = str(self.root)
                    record.update(source='node', node_path=path.name)
                else:
                    item = next(i for s in request['context']['native_sources'] for t in s['turns']
                                for i in t['items'] if i['id'] == record['native_item_id'])
                    item['output'] = {'text': body, 'truncated': False}
                before = copy.deepcopy(request)
                self.assertTrue(source.verify(request['context'], request['project'], record))
                code, result = self.call(request)
                self.assertEqual(code, 2)
                self.assertIn('adoption_applicability', result['missing'])
                self.assertNotIn('unverified_adoption_source', result['missing'])
                self.assertEqual(result['blocked'], [])
                self.assertFalse(result['continuous_eligible'])
                self.assertEqual(request, before)

    def test_every_closure_outcome_keeps_its_own_conditions(self):
        kinds = ('regression_original', 'regression_affected', 'adoption', 'effect', 'acceptance', 'followup')
        for kind in kinds:
            with self.subTest(kind=kind):
                request, target = self.qualified_close()
                self.assertEqual(self.call(request)[0], 0)
                record = next(r for r in request['context']['evidence'] if r['kind'] == kind)
                record['resource'] = {**target, 'new-unlisted-condition': {'cases': ['observed'], 'bounded': True}}
                code, result = self.call(request)
                self.assertEqual(code, 2)
                label = 'goal_acceptance' if kind == 'acceptance' else kind
                self.assertIn(label + '_applicability', result['missing'])
                self.assertFalse(result['continuous_eligible'])

    def test_completed_progress_keeps_original_acceptance_conditions(self):
        request, target = self.qualified_close()
        request['phase'] = 'progress'
        request['progress'] = {'checkpoint': 'stage_end', 'claim': 'completed', 'steps': [],
            'resource': target, 'version': 'commit:abc', 'goal_ref': request['issue']['goal_ref'],
            'acceptance_ref': request['issue']['acceptance_ref']}
        progress_fixtures.ContinuationChecks().review(request, request['issue']['goal_ref'])
        baseline = check.evaluate(request)
        self.assertEqual(baseline['status'], 'ready')
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'acceptance')
        record['resource'] = {**target, 'new-unlisted-condition': {'cases': ['different'], 'bounded': 1}}
        self.assertIn('goal_acceptance_applicability', check.evaluate(request)['missing'])

    def test_project_adapter_does_not_promote_bounded_run_to_different_target(self):
        request, _, _ = business_fixtures.business_case()
        base = request['action']['resource']
        request['action']['resource'] = {**base, 'declared-extent': ['target']}
        for record in request['context']['evidence']:
            if record.get('resource') == base:
                record['resource'] = copy.deepcopy(request['action']['resource'])
        self.assertEqual(check.evaluate(request)['status'], 'ready')
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'business_run')
        record['resource'] = {**base, 'declared-extent': ['original']}
        result = check.evaluate(request)
        self.assertIn('business_run_applicability', result['missing'])
        self.assertEqual(result['blocked'], [])

    def test_release_test_is_not_another_scopes_release_acceptance(self):
        request = fixtures.action_case()
        target = {**request['action']['resource'], 'declared-extent': ['target']}
        request['action'].update(kind='publish', resource=target,
            release={'version': 'commit:abc', 'test_ref': 'node:test:release',
                     'goal_ref': 'codex:user:publish-goal'})
        request['context']['evidence'] = [fixtures.fact('node:test:release', 'release_test', 'passed', 'commit:abc')]
        fixtures.bind_native_sources(request)
        fixtures.WorkspaceBoundaryChecks().add_user_evidence(
            request, 'codex:user:publish-goal', 'goal', 'Publish this accepted framework version')
        record = request['context']['evidence'][0]
        record.update(resource=target, related_ref='codex:user:publish-goal')
        request['issue'] = {'resource': target}
        fixtures.attach_independent_verification(request, 'commit:abc')
        request['action']['release']['independent_verification'] = request.pop('issue')['independent_verification']
        self.assertEqual(check.evaluate(request)['status'], 'ready')
        record['resource'] = {**target, 'declared-extent': ['original']}
        result = check.evaluate(request)
        self.assertIn('release_test_applicability', result['missing'])
        self.assertEqual(result['blocked'], [])

    def test_resource_facts_do_not_gain_additional_applicability(self):
        request = maintenance_fixtures.maintenance_case()
        target = {**request['action']['resource'], 'declared-extent': ['target']}
        request['action']['resource'] = target
        for record in request['context']['evidence']:
            if record.get('resource') is not None:
                record['resource'] = copy.deepcopy(target)
        for ref in ('codex:contract:resource', 'codex:observe:resource'):
            maintenance_fixtures.alter(request, ref, resource=target)
        self.assertEqual(check.evaluate(request)['status'], 'ready')
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'resource_contract')
        original = {**target, 'declared-extent': ['original']}
        record['resource'] = original
        maintenance_fixtures.alter(request, record['ref'], resource=original)
        result = check.evaluate(request)
        self.assertIn('resource_contract_applicability', result['missing'])
        self.assertEqual(result['blocked'], [])

    def scoped_maintenance(self):
        request = maintenance_fixtures.maintenance_case()
        target = {**request['action']['resource'], 'new-unlisted-condition': {'cases': ['target'], 'bounded': True}}
        request['action']['resource'] = target
        for record in request['context']['evidence']:
            if record.get('resource') is not None:
                record['resource'] = copy.deepcopy(target)
        for ref in ('codex:contract:resource', 'codex:observe:resource'):
            maintenance_fixtures.alter(request, ref, resource=target)
        return request, target

    def test_structured_facts_bind_original_body_even_without_success_result(self):
        for kind in ('resource_contract', 'resource_observation'):
            for condition in ({'cases': ['source'], 'bounded': True},
                              {'cases': ['target'], 'bounded': 1}):
                with self.subTest(kind=kind, condition=condition):
                    request, target = self.scoped_maintenance()
                    self.assertEqual(self.call(request)[0], 0)
                    record = next(r for r in request['context']['evidence'] if r['kind'] == kind)
                    original = {**target, 'new-unlisted-condition': condition}
                    maintenance_fixtures.alter(request, record['ref'], resource=original)
                    before = copy.deepcopy(request)
                    code, result = self.call(request)
                    self.assertEqual(code, 2)
                    expected_gap = ('unverified_resource_observation_source' if kind == 'resource_observation'
                                    else 'resource_contract_applicability')
                    self.assertIn(expected_gap, result['missing'])
                    self.assertNotIn('platform_denied_action', result['blocked'])
                    self.assertEqual(request, before)

    def test_contract_missing_or_added_conditions_are_not_inferred_from_wrapper(self):
        for mutate in (lambda t: {k:v for k,v in t.items() if k != 'new-unlisted-condition'},
                       lambda t: {**t, 'another-condition': ['source-only']}):
            request, target = self.scoped_maintenance()
            record = next(r for r in request['context']['evidence'] if r['kind'] == 'resource_contract')
            self.assertIsNone(record.get('result'))
            maintenance_fixtures.alter(request, record['ref'], resource=mutate(target))
            code, result = self.call(request)
            self.assertEqual(code, 2)
            self.assertIn('resource_contract_applicability', result['missing'])
            self.assertEqual(result['blocked'], [])

    def test_contract_original_object_mismatch_keeps_existing_resource_boundary(self):
        request, target = self.scoped_maintenance()
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'resource_contract')
        maintenance_fixtures.alter(request, record['ref'], resource={**target, 'id': 'another'})
        code, result = self.call(request)
        self.assertEqual(code, 2)
        self.assertIn('resource_contract_resource_mismatch', result['blocked'])

    def test_business_expectation_original_scope_survives_structured_consumption(self):
        request, expected, _ = business_fixtures.business_case()
        target = {**request['action']['resource'], 'arbitrary-condition': {'case': 'target'}}
        request['action']['resource'] = target
        for record in request['context']['evidence']:
            if record.get('resource') is not None:
                record['resource'] = copy.deepcopy(target)
        expected['resource'] = target
        business_fixtures.replace_body(request, 'business:expected', expected)
        self.assertEqual(self.call(request)[0], 0)
        expected['resource'] = {**target, 'arbitrary-condition': {'case': 'source'}}
        business_fixtures.replace_body(request, 'business:expected', expected)
        code, result = self.call(request)
        self.assertEqual(code, 2)
        self.assertIn('business_expectation_applicability', result['missing'])
        self.assertEqual(result['blocked'], [])

    def test_unlabelled_command_fact_body_uses_the_same_scope_check(self):
        request, target = self.scoped_maintenance()
        record = next(r for r in request['context']['evidence'] if r['kind'] == 'resource_observation')
        record.pop('result')
        self.assertEqual(self.call(request)[0], 0)
        maintenance_fixtures.alter(request, record['ref'], resource={**target, 'extra-condition': ['original']})
        code, result = self.call(request)
        self.assertEqual(code, 2)
        self.assertIn('resource_observation_applicability', result['missing'])
        self.assertEqual(result['blocked'], [])

    def replace_original_text(self, request, ref, text):
        record = next(r for r in request['context']['evidence'] if r['ref'] == ref)
        item = next(i for s in request['context']['native_sources'] for t in s['turns']
                    for i in t['items'] if i['id'] == record['native_item_id'])
        record['digest'] = hashlib.sha256(text.encode()).hexdigest()
        if item['type'] == 'commandExecution':
            item['output']['text'] = text
        else:
            item['text'] = text

    def test_business_original_duplicate_scopes_cannot_be_last_value_wins(self):
        for variant in ('resource', 'nested', 'identical'):
            with self.subTest(variant=variant):
                request, expected, _ = business_fixtures.business_case()
                target = {**request['action']['resource'], 'arbitrary-condition': {'case': 'target'}}
                request['action']['resource'] = target
                for record in request['context']['evidence']:
                    if record.get('resource') is not None:
                        record['resource'] = copy.deepcopy(target)
                prefix = json.dumps(expected)[:-1]
                a = {**target, 'arbitrary-condition': {'case': 'observed'}}
                if variant in ('resource', 'identical'):
                    first = a if variant == 'resource' else target
                    text = prefix + ',"resource":' + json.dumps(first) + ',"resource":' + json.dumps(target) + '}'
                else:
                    scope = json.dumps(target).replace('"case": "target"', '"case": "observed", "case": "target"')
                    text = prefix + ',"resource":' + scope + '}'
                self.replace_original_text(request, 'business:expected', text)
                before = copy.deepcopy(request)
                code, result = self.call(request)
                self.assertEqual(code, 1)
                self.assertEqual(result['status'], 'invalid')
                self.assertEqual(request, before)

    def test_shared_original_parser_keeps_valid_legacy_and_rejects_non_json_constants(self):
        request, expected, _ = business_fixtures.business_case()
        self.assertEqual(self.call(request)[0], 0)
        expected['unused'] = float('nan')
        self.replace_original_text(request, 'business:expected', json.dumps(expected))
        code, result = self.call(request)
        self.assertEqual(code, 1)
        self.assertEqual(result['status'], 'invalid')
        value = {'resource': {'kind': 'repo', 'id': 'same', 'condition': [True, 1]}, 'result': 'passed'}
        self.assertEqual(source.parse_statement(json.dumps(value)), value)
        for text in ('{"a":1,"a":2}', '{"nested":{"a":1,"a":2}}', '{"a":NaN}'):
            with self.subTest(text=text), self.assertRaises(ValueError):
                source.parse_statement(text)
