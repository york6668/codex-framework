"""Bounded carriage controls; no native model, authority or domain-quality proof.

The named examples reconstruct relations from real cases, using generated
records. Production source/effect provenance remains the original caller's job.
"""
import copy
import json
from unittest import mock

import test_framework as fixtures
import test_delivery_view as delivery_fixtures
import task_runtime as task
import task_coordination as coord
import source_evidence


class RequirementCarriageTests(fixtures.FixtureCase):
    def spec(self):
        return {'request_id': 'business-request', 'requirement': 'Deliver the business result using the applicable method',
                'acceptance_version': 'v1', 'workspace': str(self.root), 'max_runs': 1,
                'steps': [{'id': 'one', 'instruction': 'Inspect the engineering correction',
                           'checks': [{'kind': 'message', 'equals': '42'}]}],
                'request_identity': {'thread_id': 'fixture-owner', 'turn_id': 'original-turn', 'message_id': 'original-input'},
                'result_scope': {'object_ref': 'original-object', 'goal': 'Original result and professional method',
                                 'source_ref': 'fixture:original-business-requirement',
                                 'obligations': [{'id': 'business-result', 'acceptance':
                                     'Judge the business demand and result; inspect project source only when necessary, without collecting it as the business deliverable'}]}}

    def amended(self):
        store = task.Store(self.a.codex_home, 'carriage')
        first = store.create('carriage', self.spec())
        latest = copy.deepcopy(first['spec'])
        latest['requirement'] = 'Apply this engineering correction'
        latest['request_identity']['turn_id'] = 'correction-turn'
        latest['request_identity']['message_id'] = 'correction-input'
        latest['result_scope'].update(goal='Current engineering correction', source_ref='fixture:current-correction',
                                     obligations=[{'id': 'source-fix', 'acceptance': 'Correct the affected source'}])
        value = task.change(store, {'operation': 'amend', 'expected_revision': first['revision'], 'spec': latest})
        return store, value

    def retained(self, prompt):
        prefix = 'Retained original requirements: '
        return json.loads(next(line[len(prefix):] for line in prompt.splitlines() if line.startswith(prefix)))

    def test_amended_original_obligation_and_its_origin_reach_the_next_handoff(self):
        store, value = self.amended()
        context = self.retained(coord.prompt(value))
        rows = {r['id']: r for r in context['obligations']}
        self.assertEqual(context['original_goal'], 'Original result and professional method')
        self.assertEqual(rows['business-result']['acceptance'], self.spec()['result_scope']['obligations'][0]['acceptance'])
        self.assertEqual(rows['business-result']['source_ref'], 'fixture:original-business-requirement')
        self.assertEqual(rows['business-result']['input_revision'], 1)
        self.assertEqual(rows['business-result']['request_identity']['turn_id'], 'original-turn')
        self.assertEqual(rows['source-fix']['input_revision'], 2)
        self.assertEqual(rows['source-fix']['request_identity']['turn_id'], 'correction-turn')
        out = task.view(store.read())
        self.assertEqual(out['result_dimensions']['requirements'], context)
        self.assertFalse(out['accepted'])

    def test_actual_dispatch_builder_keeps_original_result_without_a_user_reminder(self):
        store, value = self.amended()
        observed = []
        def isolated_worker(prompt, *args, **kwargs):
            context = self.retained(prompt)
            self.assertEqual({r['id'] for r in context['obligations']}, {'business-result', 'source-fix'})
            observed.append(prompt)
            return {'status': 'completed', 'message': '42', 'message_sha256': task.digest(b'42'),
                    'session_id': 'fixture-session', 'prompt_sent': True, 'exit': 0}
        with mock.patch('task_worker.execute', side_effect=isolated_worker) as worker:
            value = task.run_once(store, value['revision'])
        self.assertEqual(worker.call_count, 1)
        self.assertEqual(value['steps']['one']['state'], 'verified')
        self.assertFalse(value['accepted'])
        self.assertEqual(task.view(value)['next_action']['kind'], 'continue_original_obligations')
        self.assertEqual(set(task.view(value)['next_action']['obligations']), {'business-result', 'source-fix'})
        pending = {r['id']: r for r in task.view(value)['next_action']['requirements']}
        self.assertEqual(pending['business-result']['acceptance'], self.spec()['result_scope']['obligations'][0]['acceptance'])
        self.assertEqual(pending['business-result']['source_ref'], 'fixture:original-business-requirement')
        self.assertEqual(len(observed), 1)

    def test_old_assistant_stage_is_not_replayed_as_current_user_authority(self):
        spec = self.spec()
        spec['requirement'] = 'Assistant diagnostic stage only: no new research in this stage'
        store = task.Store(self.a.codex_home, 'phase')
        first = store.create('phase', spec)
        latest = self.spec()
        latest['requirement'] = 'Current authorized implementation with original result responsibility'
        value = task.change(store, {'operation': 'amend', 'expected_revision': first['revision'], 'spec': latest})
        prompt = coord.prompt(value)
        self.assertNotIn(spec['requirement'], prompt)
        self.assertIn('business-result', prompt)
        self.assertEqual(value['restrictions'], [])
        item = {'type': 'userMessage', 'content': [{'type': 'text', 'text': '<codex_delegation>old assistant phase</codex_delegation>',
                                                 'codexDelegation': {'sourceThreadId': 'fixture-parent'}}]}
        self.assertFalse(source_evidence.direct_user_message(item, 'old assistant phase'))
        self.assertTrue(source_evidence.direct_user_message({'type': 'userMessage', 'text': 'Pause this exact action'}, 'Pause this exact action'))

    def test_explicit_refusal_survives_amendment_while_independent_work_stays_ready(self):
        spec = self.spec()
        target = self.root / 'denied.txt'; target.write_bytes(b'safe')
        spec['steps'] = [{'id': 'one', 'instruction': 'Original exact file action',
                          'checks': [{'kind': 'file', 'path': 'denied.txt', 'sha256': task.digest(b'safe')}]}]
        store = task.Store(self.a.codex_home, 'denial')
        first = store.create('denial', spec)
        denied = task.change(store, {'operation': 'approval', 'expected_revision': first['revision'],
                                     'step': 'one', 'decision': 'denied', 'source_ref': 'fixture:exact-platform-refusal'})
        latest = copy.deepcopy(spec); latest['requirement'] = 'Current implementation stage'
        latest['steps'].append({'id': 'independent', 'instruction': 'Independent analysis of acquired input',
                                'checks': [{'kind': 'message', 'equals': '42'}]})
        value = task.change(store, {'operation': 'amend', 'expected_revision': denied['revision'], 'spec': latest})
        out = task.view(value)
        self.assertEqual(out['work_partition']['ready'], ['independent'])
        self.assertTrue(out['current_constraints']['one']['blocked'])
        self.assertEqual(value['steps']['one']['runs'], 0)
        self.assertIn('fixture:exact-platform-refusal', json.dumps(value['restrictions']))

    def original_report(self, record, **details):
        fields = ('delivery_id', 'stage', 'case', 'task_id', 'baseline', 'version', 'inputs', 'result')
        report = {k: copy.deepcopy(record[k]) for k in fields}
        report.update(details)
        record['report'] = json.dumps(report, sort_keys=True)
        record['sha256'] = task.digest(record['report'].encode())
        return record

    def test_verified_original_report_scope_survives_active_and_historical_projection(self):
        request = delivery_fixtures.DeliveryViewTests().request()
        source = request['observations'][0]
        self.original_report(source, scope='Source assembly only', runtime_adopted=False,
                             limitations=['No expert-behavior improvement is established'])
        out = coord.delivery_view(request)['deliveries']['deliver']
        retained = out['reported_observations'][0]
        self.assertEqual(retained['report'], source['report'])
        self.assertFalse(json.loads(retained['report'])['runtime_adopted'])
        request['baseline']['id'] = 'changed-requirement'
        history = coord.delivery_view(request)['deliveries']['deliver']
        self.assertEqual(history['reported_observations'][0]['report'], source['report'])
        self.assertFalse(history['complete'])

    def test_invalid_pilot_unrun_neutral_and_exact_denial_all_survive(self):
        request = delivery_fixtures.DeliveryViewTests().request()
        verify = request['observations'].pop()
        request['deliveries'][0]['stages']['verification']['checks'] = ['neutral-control']
        for case, result, details in [('biased-pilot', 'FAIL', {'control_valid': False}),
                                     ('neutral-control', 'NOT_RUN', {'executed': False}),
                                     ('exact-admission', 'NOT_RUN', {'denied': True, 'retry_allowed': False,
                                      'old_limit_source': 'assistant diagnostic stage', 'reviewer_full_input_visible': False})]:
            record = copy.deepcopy(verify);record.update(case=case, result=result)
            request['observations'].append(self.original_report(record, **details))
        out = coord.delivery_view(request)['deliveries']['deliver']
        originals = {r['case']: json.loads(r['report']) for r in out['reported_observations']}
        self.assertFalse(originals['biased-pilot']['control_valid'])
        self.assertFalse(originals['neutral-control']['executed'])
        self.assertTrue(originals['exact-admission']['denied'])
        self.assertFalse(originals['exact-admission']['retry_allowed'])
        self.assertFalse(out['complete'])
        self.assertEqual(out['next_actions'][0]['stage'], 'verification')
        self.assertEqual(out['next_actions'][0]['checks'], ['neutral-control'])
        self.assertFalse(out['next_actions'][0]['execution_authorized'])

    def test_source_candidate_does_not_erase_finite_architecture_or_actual_correction(self):
        request = delivery_fixtures.DeliveryViewTests().request()
        template = request['deliveries'][0]; observation = request['observations'][0]
        request['deliveries'] = []; request['observations'] = []
        for identity, version in [('architecture', '801'), ('actual-correction', '308'), ('source-candidate', 'de1')]:
            row = copy.deepcopy(template);row.update(id=identity, goal=identity)
            row['stages'] = {'source': {'version': version, 'inputs': {}},
                             'adoption': {'version': version, 'inputs': {'source': version}}}
            request['deliveries'].append(row)
            for stage in ['source', 'adoption']:
                if identity == 'source-candidate' and stage == 'adoption': continue
                record = copy.deepcopy(observation)
                record.update(delivery_id=identity, stage=stage, version=version, inputs=row['stages'][stage]['inputs'])
                request['observations'].append(self.original_report(record, scope=identity, expert_quality_proven=False))
        request['mainlines'][0]['delivery_ids'] = [r['id'] for r in request['deliveries']]
        out = coord.delivery_view(request)
        self.assertTrue(out['deliveries']['architecture']['complete'])
        self.assertTrue(out['deliveries']['actual-correction']['complete'])
        pending = out['deliveries']['source-candidate']
        self.assertFalse(pending['complete'])
        self.assertEqual([r['stage'] for r in pending['next_actions']], ['adoption'])
        self.assertTrue(all(not json.loads(r['report'])['expert_quality_proven']
                            for delivery in out['deliveries'].values() for r in delivery['reported_observations']))
        self.assertFalse(out['summary_is_execution_gate'])
