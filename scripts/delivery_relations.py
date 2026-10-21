"""Pure projections of supplied delivery relationships.

No Store, file reads, tool calls, scheduler or native authorship inference.
The context only supplies the existing contract validation and encoding helpers.
"""
import copy
import json


DELIVERY_STAGES = ('source', 'build', 'install', 'load', 'adoption', 'verification')
REUSE_AXES = ('requirements', 'code', 'input', 'environment', 'method')


class DeliveryRelations:
    def __init__(self, context):
        self.context = context

    def native_task_projection(self,binding, snapshots):
        """Observe the selected original turn, never infer a delivery from its end."""
        found = {}
        for snapshot in snapshots:
            if not isinstance(snapshot, dict) or type(snapshot.get('schemaVersion')) is not int or snapshot['schemaVersion'] != 1:
                continue
            thread = snapshot.get('thread')
            if not isinstance(thread, dict) or thread.get('id') != binding['thread_id']:
                continue
            turns = snapshot.get('turns')
            if not isinstance(turns, list):
                continue
            for turn in turns:
                if isinstance(turn, dict) and turn.get('id') == binding['turn_id']:
                    found[self.context.encoded(turn)] = turn
        if not found:
            return dict(binding, native_status='unknown', native_observation='selected_turn_not_observed')
        if len(found) != 1:
            return dict(binding, native_status='unknown', native_observation='conflicting_selected_turn_snapshots')
        turn = next(iter(found.values()))
        status = turn.get('status')
        return dict(binding, native_status=status if isinstance(status, str) else 'unknown',
                    native_observation='supplied_selected_turn', completion_scope='native turn only')


    def delivery_view(self,request):
        """Read-only current delivery relationships; no task registration or effects.

        Original callers retain evidence/authorship and domain interpretation duties.
        A source reference and fingerprint identify an observation, not its truth.
        This operation checks the supplied current-baseline/version relationships.
        """
        baseline = request.get('baseline')
        self.context.need(isinstance(baseline, dict) and set(baseline) == {'id', 'source_ref'}
             and all(self.context.text(baseline[k]) for k in baseline), 'delivery view needs the current requirement baseline and source')
        definitions = request.get('deliveries')
        mainlines = request.get('mainlines')
        bindings = request.get('tasks', [])
        observations = request.get('observations', [])
        snapshots = request.get('native_sources', [])
        self.context.need(isinstance(definitions, list) and 0 < len(definitions) <= 64, 'delivery view needs1..64 existing deliverables')
        self.context.need(isinstance(mainlines, list) and 0 < len(mainlines) <= 32, 'delivery view needs1..32 mainlines')
        self.context.need(isinstance(bindings, list) and len(bindings) <= 128, 'invalid existing task bindings')
        self.context.need(isinstance(observations, list) and len(observations) <= 256, 'invalid delivery observations')
        self.context.need(isinstance(snapshots, list) and len(snapshots) <= 128, 'invalid native snapshots')
        tasks = {}
        for binding in bindings:
            self.context.need(isinstance(binding, dict) and set(binding) == {'id', 'thread_id', 'turn_id', 'source_ref'}
                 and self.context.identifier(binding['id']) and all(self.context.text(binding[k]) for k in ('thread_id', 'turn_id', 'source_ref')),
                 'invalid original task identity')
            self.context.need(binding['id'] not in tasks, 'duplicate original task binding')
            tasks[binding['id']] = self.native_task_projection(binding, snapshots)
        records = {}
        for observation in observations:
            self.context.need(isinstance(observation, dict) and self.context.text(observation.get('delivery_id')), 'observation needs a delivery identity')
            records.setdefault(observation['delivery_id'], []).append(observation)
        deliveries = {}
        for declared in definitions:
            self.context.need(isinstance(declared, dict) and self.context.identifier(declared.get('id')), 'invalid deliverable identity')
            identity = declared['id']
            self.context.need(identity not in deliveries, 'duplicate deliverable identity')
            row = {'goal': declared.get('goal'), 'source_ref': declared.get('source_ref'), 'task_ids': declared.get('task_ids'),
                   'stages': {}, 'gaps': [], 'historical_observations': [], 'implementation_delivery': {'complete': False},
                   'verification_delivery': {'complete': False}, 'complete': False,
                   'reported_observations': [], 'next_actions': []}
            deliveries[identity] = row
            row['declared_contract'] = copy.deepcopy(declared)
            if isinstance(declared.get('stages'), dict):
                row['stages'] = {name: {'required': name in declared['stages'], 'complete': False,
                    'status': 'pending' if name in declared['stages'] else 'not_required_by_this_contract',
                    'target': copy.deepcopy(declared['stages'].get(name)), 'observations': [], 'gaps': []}
                    for name in DELIVERY_STAGES}
            try:
                self.context.need(set(declared) == {'id', 'goal', 'source_ref', 'task_ids', 'stages'}
                     and self.context.text(declared['goal']) and self.context.text(declared['source_ref']), 'invalid deliverable contract')
                ids = declared['task_ids']
                self.context.need(isinstance(ids, list) and len(ids) <= 128 and all(self.context.identifier(i) for i in ids)
                     and len(set(ids)) == len(ids), 'invalid deliverable task membership')
                stages = declared['stages']
                self.context.need(isinstance(stages, dict) and stages and set(stages) <= set(DELIVERY_STAGES)
                     and any(s != 'verification' for s in stages), 'deliverable needs an explicit implementation stage')
                row['tasks'] = {i: tasks.get(i) for i in ids}
                if not ids or set(ids) - set(tasks):
                    row['gaps'].append({'kind': 'original_task_binding_missing', 'task_ids': sorted(set(ids) - set(tasks))})
                eligible = []
                for record in records.get(identity, []):
                    fields = {'delivery_id', 'stage', 'case', 'task_id', 'baseline', 'version', 'inputs', 'result', 'source_ref', 'sha256', 'report'}
                    self.context.need(fields <= set(record) <= fields | {'reuse'} and record['stage'] in DELIVERY_STAGES
                         and all(self.context.text(record[k]) for k in ('case', 'task_id', 'baseline', 'version', 'source_ref'))
                         and self.context.hashes(record['sha256']) and isinstance(record['inputs'], dict)
                         and all(self.context.text(k) and self.context.text(v) for k, v in record['inputs'].items())
                         and record['result'] in ('PASS', 'FAIL', 'NOT_RUN', 'UNKNOWN'), 'invalid scoped delivery observation')
                    self.context.need(record['task_id'] in ids, 'delivery observation belongs to another task')
                    self.context.need(isinstance(record['report'], str) and self.context.digest(record['report'].encode()) == record['sha256'],
                         'original report bytes do not match the evidence fingerprint')
                    def unique(pairs):
                        out = {}
                        for key, child in pairs:
                            self.context.need(key not in out, 'duplicate original report field')
                            out[key] = child
                        return out
                    def constant(value):
                        raise ValueError('non-JSON original report value')
                    reported = json.loads(record['report'], object_pairs_hook=unique, parse_constant=constant)
                    self.context.need(isinstance(reported, dict) and all(type(reported.get(k)) is type(record[k]) and reported[k] == record[k]
                         for k in ('delivery_id', 'stage', 'case', 'task_id', 'baseline', 'version', 'inputs', 'result')),
                         'observation does not bind its original report content')
                    # Keep the verified original body, including its limitations
                    # and nonselected cases. Labels/version links cannot replace
                    # what the actual report says or authenticate its semantics.
                    row['reported_observations'].append(copy.deepcopy(record))
                    current = record['baseline'] == baseline['id']
                    if not current:
                        reuse = record.get('reuse')
                        current = (isinstance(reuse, dict) and set(reuse) == {'baseline', 'source_ref', *REUSE_AXES}
                                   and reuse['baseline'] == baseline['id'] and self.context.text(reuse['source_ref'])
                                   and all(reuse[k] == 'unchanged' for k in REUSE_AXES))
                        if not current:
                            row['historical_observations'].append({k:copy.deepcopy(v) for k,v in record.items() if k!='report'})
                            continue
                    eligible.append(record)
                for stage in DELIVERY_STAGES:
                    expected = stages.get(stage)
                    state = {'required': stage in stages, 'complete': False, 'observations': [], 'gaps': []}
                    row['stages'][stage] = state
                    if stage not in stages:
                        state['status'] = 'not_required_by_this_contract'
                        continue
                    self.context.need(isinstance(expected, dict) and {'version', 'inputs'} <= set(expected) <= {'version', 'inputs', 'checks', 'independent'}
                         and (expected['version'] is None or self.context.text(expected['version'])) and isinstance(expected['inputs'], dict)
                         and all(self.context.text(k) and self.context.text(v) for k, v in expected['inputs'].items()), 'invalid stage target/version links')
                    checks = expected.get('checks', ['result'])
                    self.context.need(isinstance(checks, list) and checks and len(checks) <= 64 and all(self.context.text(x) for x in checks)
                         and len(set(checks)) == len(checks) and type(expected.get('independent', False)) is bool,
                         'invalid current acceptance cases')
                    for linked, version in expected['inputs'].items():
                        if linked in DELIVERY_STAGES:
                            self.context.need(linked != stage and linked in stages and isinstance(stages[linked], dict)
                                 and stages[linked].get('version') == version, 'stage input refers to a different current version')
                    prior = [name for name in DELIVERY_STAGES[:DELIVERY_STAGES.index(stage)] if name in stages and name != 'verification']
                    if prior:
                        upstream = 'load' if stage == 'verification' and 'load' in prior else prior[-1]
                        self.context.need(upstream in expected['inputs'] and expected['inputs'][upstream] == stages[upstream]['version'],
                             'stage needs its actual upstream current-version link')
                    state['target'] = copy.deepcopy(expected)
                    if expected['version'] is None:
                        state['gaps'].append({'kind': 'current_version_not_frozen'})
                    for case in checks:
                        candidates = [r for r in eligible if r['stage'] == stage and r['case'] == case
                                      and r['version'] == expected['version'] and r['inputs'] == expected['inputs']]
                        if not candidates:
                            state['gaps'].append({'kind': 'current_stage_evidence_missing', 'case': case})
                            continue
                        state['observations'].extend({k:copy.deepcopy(v) for k,v in record.items() if k!='report'} for record in candidates)
                        identities = {}
                        for record in candidates:
                            original = tasks.get(record['task_id'], {})
                            key = (original.get('thread_id'), original.get('turn_id'))
                            identities.setdefault(key, set()).add((record['result'], record['sha256']))
                        if any(len(results) > 1 for results in identities.values()):
                            state['gaps'].append({'kind': 'conflicting_original_result', 'case': case})
                        if any(r['result'] != 'PASS' for r in candidates):
                            state['gaps'].append({'kind': 'stage_result_not_passed_or_conflicting', 'case': case})
                        if any(tasks.get(r['task_id'], {}).get('native_observation') == 'conflicting_selected_turn_snapshots'
                               for r in candidates):
                            state['gaps'].append({'kind': 'conflicting_original_native_observation', 'case': case})
                        if expected.get('independent'):
                            producers = {tasks[r['task_id']]['thread_id'] for r in eligible
                                         if r['stage'] != 'verification' and r['task_id'] in tasks and r['stage'] in stages
                                         and r['version'] == stages[r['stage']].get('version')
                                         and r['inputs'] == stages[r['stage']].get('inputs')}
                            if any(r['task_id'] not in tasks or tasks[r['task_id']]['thread_id'] in producers for r in candidates):
                                state['gaps'].append({'kind': 'independent_verification_not_bound', 'case': case})
                    state['complete'] = not state['gaps']
                    state['status'] = 'bound_current_observations' if state['complete'] else 'pending'
                    row['gaps'].extend(dict(gap, stage=stage) for gap in state['gaps'])
                    if not state['complete']:
                        affected={g['case'] for g in state['gaps'] if 'case' in g}
                        row['next_actions'].append({'kind':'inspect_current_stage_gap','stage':stage,
                            'version':expected['version'],'checks':[c for c in checks if not affected or c in affected],
                            'gaps':copy.deepcopy(state['gaps']),'execution_authorized':False})
                row['implementation_delivery'] = {'complete': all(s['complete'] for k, s in row['stages'].items()
                    if s['required'] and k != 'verification'), 'scope': 'declared implementation stages only'}
                verify = row['stages']['verification']
                row['verification_delivery'] = {'required': verify['required'], 'complete': verify['complete'] if verify['required'] else None,
                                                'scope': 'current exact-version declared verification cases only'}
                row['complete'] = not row['gaps']
            except (self.context.error_type, TypeError, ValueError, RecursionError) as exc:
                row['gaps'].append({'kind': 'invalid_delivery_contract_or_observation', 'detail': str(exc)})
        lines, linked, issues = {}, set(), []
        for line in mainlines:
            self.context.need(isinstance(line, dict) and set(line) == {'id', 'goal', 'source_ref', 'delivery_ids'}
                 and self.context.identifier(line['id']) and self.context.text(line['goal']) and self.context.text(line['source_ref']), 'invalid mainline')
            self.context.need(line['id'] not in lines, 'duplicate mainline')
            ids = line['delivery_ids']
            self.context.need(isinstance(ids, list) and ids and len(ids) <= 64 and all(self.context.identifier(i) for i in ids)
                 and len(set(ids)) == len(ids), 'invalid mainline delivery membership')
            linked.update(ids)
            gaps = [{'delivery_id': i, 'kind': 'delivery_pending' if i in deliveries else 'delivery_missing'}
                    for i in ids if i not in deliveries or not deliveries[i]['complete']]
            lines[line['id']] = dict(line, complete=not gaps, gaps=gaps)
        if set(deliveries) - linked:
            issues.append({'kind': 'unlinked_deliveries', 'delivery_ids': sorted(set(deliveries) - linked)})
        if set(records) - set(deliveries):
            issues.append({'kind': 'observation_for_unknown_delivery', 'delivery_ids': sorted(set(records) - set(deliveries))})
        return {'baseline': baseline, 'mainlines': lines, 'deliveries': deliveries, 'tasks': tasks, 'issues': issues,
                'complete': not issues and all(line['complete'] for line in lines.values()),
                'scope': 'supplied current requirement/version/evidence bindings; no native authenticity or domain acceptance granted',
                'effect': 'pure_projection_no_store_or_tool_calls', 'summary_is_execution_gate': False}
