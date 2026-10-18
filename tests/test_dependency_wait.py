"""DS02: scoped dependency proof and safe waiting; no real model/native effects."""
import copy
import hashlib
import json
import unittest

import test_progress_check as progress_fixtures
import execution_check as check


class DependencyWaitTests(unittest.TestCase):
    def request(self, reviewed=True):
        helper = progress_fixtures.ProgressChecks()
        value = helper.progress(helper.base(), 'waiting')
        value['progress']['checkpoint'] = 'wait'
        value['progress']['steps'].pop()  # Only refused producer + its consumer.
        if reviewed:
            progress_fixtures.ContinuationChecks().review(value)
        value['context']['native_operations'] = [{
            'ref': 'codex:original:unknown', 'state': 'unknown',
            'resource': copy.deepcopy(value['action']['resource']),
            'effect_key': 'unrelated-original-attempt'}]
        return value

    def inspect(self, value, state, wait=False):
        before = copy.deepcopy(value)
        result = check.evaluate(value)
        self.assertEqual(value, before)
        self.assertEqual([s['state'] for s in result['steps']], ['direct_refusal', state])
        self.assertEqual(result['wait_allowed'], wait)
        return result

    def record(self, value):
        return next(r for r in value['context']['evidence'] if r['kind'] == 'dependency')

    def item(self, value):
        ref = self.record(value)['native_item_id']
        return next(i for i in value['context']['native_sources'][0]['turns'][0]['items'] if i['id'] == ref)

    def body(self, value, mutate):
        item = self.item(value)
        body = json.loads(item['output']['text'])
        mutate(body)
        text = json.dumps(body)
        item['output']['text'] = text
        self.record(value)['digest'] = hashlib.sha256(text.encode()).hexdigest()

    def test_missing_and_invented_basis_cannot_wait_or_authorize_action(self):
        for basis in (None, {}, {'status':'proven','source_ref':'codex:invented','reason':'asserted necessity'},
                      {'status':'unknown'}, {'status':'hypothesis'}):
            with self.subTest(basis=basis):
                value = self.request()
                entry = value['progress']['steps'][1]
                if basis is None:
                    entry.pop('depends_on_basis')
                else:
                    entry['depends_on_basis'] = basis
                result = self.inspect(value, 'needs_evidence')
                self.assertEqual(result['work_partition']['unresolved_step_ids'], ['verify'])
                self.assertEqual(result['steps'][1]['unproven_dependencies'], ['restore'])

    def test_required_input_and_same_object_shared_effect_can_wait(self):
        for kind in ('required_input','shared_effect'):
            value = self.request()
            if kind == 'shared_effect':
                self.body(value, lambda b: b['requires'].update(kind=kind))
            self.inspect(value, 'dependency_blocked', True)

    def test_original_source_not_role_or_nonempty_ref_is_required(self):
        for change in ('missing_snapshot','wrong_digest','no_digest','failed','truncated',
                       'assistant','delegation','foreign_task','foreign_project','wrong_resource','wrong_kind'):
            with self.subTest(change=change):
                value = self.request()
                record, item = self.record(value), self.item(value)
                if change == 'missing_snapshot': value['context']['native_sources'] = []
                elif change == 'wrong_digest': record['digest'] = '0'*64
                elif change == 'no_digest': record.pop('digest')
                elif change == 'failed': item['exitCode'] = 1
                elif change == 'truncated': item['output']['truncated'] = True
                elif change == 'assistant': item.update(type='agentMessage',text=item['output']['text']);item.pop('output');record.pop('result')
                elif change == 'delegation':
                    item.update(type='userMessage',text=item['output']['text'],content=[{'codexDelegation':{'sourceThreadId':'other'}}]);item.pop('output');record.pop('result')
                elif change == 'foreign_task': record['task_ref'] = 'codex:thread:foreign'
                elif change == 'foreign_project': record['project_id'] = 'other'
                elif change == 'wrong_resource': record['resource'] = {'kind':'repo','id':'framework','path':'scripts/elsewhere'}
                elif change == 'wrong_kind': record['kind'] = 'observation'
                self.inspect(value, 'needs_evidence')

    def test_direct_user_consumer_contract_is_a_source_not_an_action_grant(self):
        value = self.request()
        record, item = self.record(value), self.item(value)
        item.update(type='userMessage',text=item['output']['text']);item.pop('output');record.pop('result')
        self.inspect(value, 'dependency_blocked', True)
        value['progress']['steps'][1]['action']['resource']['path'] = 'outside-task'
        self.inspect(value, 'needs_evidence')
        self.assertIn('outside_task_scope',check.evaluate(value)['steps'][1]['blocked'])

    def test_source_must_bind_exact_producer_consumer_effect_and_input(self):
        for path, replacement in [
            (('producer','step_id'),'other'), (('consumer','step_id'),'other'),
            (('producer','action','effect_key'),'other-effect'),
            (('consumer','action','kind'),'write'),
            (('consumer','action','workspace','destination_node'),'elsewhere'),
            (('requires','resource','path'),'scripts/unrelated.json'),
            (('requires','kind'),'optional'), (('requires','reason'),'other reason')]:
            with self.subTest(path=path):
                value = self.request()
                def mutate(body):
                    current = body
                    for part in path[:-1]: current = current[part]
                    current[path[-1]] = replacement
                self.body(value, mutate)
                self.inspect(value, 'needs_evidence')

    def test_common_repo_parent_cannot_invent_shared_effect_for_separate_files(self):
        value = self.request()
        step = value['progress']['steps'][1]
        step['action']['resource']['path'] = 'scripts/independent.json'
        value['context']['evidence'].remove(self.record(value))
        value['context']['native_sources'][0]['turns'][0]['items'] = [i for i in value['context']['native_sources'][0]['turns'][0]['items'] if not i['id'].startswith('codex:dependency:')]
        progress_fixtures.bind_dependency(value,'restore','verify','shared_effect')
        for resource in ({'kind':'repo','id':'framework','path':'scripts'}, value['action']['resource']):
            self.body(value, lambda b:b['requires'].update(resource=resource))
            self.inspect(value, 'needs_evidence')

    def test_missing_one_of_multiple_edges_remains_unresolved(self):
        value = self.request(reviewed=False)
        other = copy.deepcopy(value['progress']['steps'][0]);other['id'] = 'other'
        value['progress']['steps'].insert(1,other)
        value['progress']['steps'][2]['depends_on'].append('other')
        progress_fixtures.ContinuationChecks().review(value)
        result = check.evaluate(value)
        self.assertEqual(result['steps'][2]['state'],'needs_evidence')
        self.assertEqual(result['steps'][2]['unproven_dependencies'],['other'])
        self.assertFalse(result['wait_allowed'])

    def test_same_object_refusal_and_unknown_are_not_cleared_by_missing_proof(self):
        value = self.request()
        consumer = value['progress']['steps'][1]
        consumer['action'] = copy.deepcopy(value['progress']['steps'][0]['action'])
        consumer.pop('depends_on_basis')
        result = check.evaluate(value)
        self.assertEqual([s['state'] for s in result['steps']],['direct_refusal','direct_refusal'])
        self.assertTrue(result['wait_allowed'])
        self.assertEqual(value['context']['native_operations'][0]['state'],'unknown')

    def test_source_shapes_and_duplicate_keys_do_not_self_confirm(self):
        for body in ('[]','null','{"producer":{},"producer":{}}', '{"producer":NaN}', 'x', ' '*65537):
            with self.subTest(body=body[:80]):
                value = self.request()
                self.item(value)['output']['text'] = body
                self.record(value)['digest'] = hashlib.sha256(body.encode()).hexdigest()
                self.inspect(value, 'needs_evidence')

    def test_independent_step_is_ready_while_false_dependency_is_unresolved(self):
        helper = progress_fixtures.ProgressChecks();value = helper.progress(helper.base(),'waiting')
        value['progress']['steps'][1].pop('depends_on_basis')
        progress_fixtures.ContinuationChecks().review(value)
        result = check.evaluate(value)
        self.assertFalse(result['wait_allowed'])
        self.assertEqual(result['work_partition'],{'ready_step_ids':['diagnose'],
            'blocked_step_ids':['restore'],'unresolved_step_ids':['verify']})

    def test_compatible_reads_cannot_be_turned_into_a_shared_effect_barrier(self):
        value = self.request()
        value['progress']['steps'][0]['action'] = copy.deepcopy(value['progress']['steps'][1]['action'])
        record = self.record(value);item = self.item(value)
        value['context']['evidence'].remove(record)
        value['context']['native_sources'][0]['turns'][0]['items'].remove(item)
        progress_fixtures.bind_dependency(value, 'restore', 'verify', 'shared_effect')
        result = check.evaluate(value)
        self.assertEqual([s['state'] for s in result['steps']], ['ready', 'needs_evidence'])
        self.assertFalse(result['wait_allowed'])


if __name__ == '__main__': unittest.main()
