"""Regression for the optional project adapter; generated data stays in memory."""
import copy
import hashlib
import json
import unittest

from test_execution_check import action_case
import execution_check as check


def business_case():
    request = action_case()
    request['action'] = {'kind': 'read', 'resource': request['action']['resource']}
    run = dict(schema=1, runId='owned-run', caseId='member-favorite',
               sourceVersion='source-v1', testVersion='test-v1',
               environment='local-in-process', evidenceKind='synthetic-controller')
    contract = dict(request=['/gameCode'], response=[], state=['/gameCode', '/exists'],
                    bindings={'selected': 'request:/gameCode', 'actual': 'after:/gameCode'})
    operation = dict(method='POST', path='/f/memberFav/add', contract=contract,
                     postconditions={'status':200,'before':{'/exists':False},'after':{'/exists':True}},
                     binding_equalities=[['selected','actual']])
    expected = dict(level='engineering', run=run, operations={'add': operation})
    before = {'/gameCode': 'dynamic-game', '/exists': False}
    after = {'/gameCode': 'dynamic-game', '/exists': True}
    projected = {'/gameCode': 'dynamic-game'}
    def event(phase):
        return dict(run=copy.deepcopy(run), operationId='add', phase=phase)
    prepared = dict(event('PREPARED'), method=operation['method'], path=operation['path'],
                    contract=contract, request=projected, before=before)
    observed = dict(event('OBSERVED'), method=operation['method'], path=operation['path'],
                    status=200, request=projected, response={}, before=before, after=after)
    bindings = {name:dict(source=source,value='dynamic-game') for name,source in contract['bindings'].items()}
    result = dict(run=run, operations={'add':dict(prepared=prepared, observed=observed,
                                               bindings=bindings, verdict=event('PASS'))})
    request['business_verification'] = {'expected_ref':'business:expected', 'observed_ref':'business:actual'}
    request['context']['evidence'] = []
    request['context']['native_sources'] = [{'schemaVersion':1, 'thread':{'id':'framework-task'},
                                           'turns':[{'items':[]}]}]
    def source(ref, kind, body, result=None, related=None):
        text = json.dumps(body) if isinstance(body, dict) else body
        record = dict(ref=ref,kind=kind,source='codex',project_id='framework',
                      task_ref=request['task']['native_ref'],native_item_id=ref,
                      digest=hashlib.sha256(text.encode()).hexdigest(), resource=request['action']['resource'])
        item = dict(id=ref,type='userMessage' if kind=='goal' else 'agentMessage',text=text)
        if kind != 'goal':
            record['version']='test-v1'
        if result:
            record['result']=result
            item=dict(id=ref,type='commandExecution',status='completed',exitCode=0,
                      command='bounded read test-v1',output={'text':text,'truncated':False})
        if related:
            record['related_ref']=related
        request['context']['evidence'].append(record)
        request['context']['native_sources'][0]['turns'][0]['items'].append(item)
    source('business:goal','goal','Review bounded Controller evidence; actual test2 remains unrun')
    source('business:expected','business_expectation',expected,related='business:goal')
    source('business:actual','business_run',result,'observed','business:expected')
    return request, json.loads(json.dumps(expected)), json.loads(json.dumps(result))


def replace_body(request, ref, body):
    text=json.dumps(body)
    record=next(r for r in request['context']['evidence'] if r['ref']==ref)
    record['digest']=hashlib.sha256(text.encode()).hexdigest()
    item=next(r for r in request['context']['native_sources'][0]['turns'][0]['items'] if r['id']==ref)
    if item['type']=='commandExecution':
        item['output']['text']=text
    else:
        item['text']=text


class BusinessEvidenceChecks(unittest.TestCase):
    def test_bounded_project_result_and_dynamic_values(self):
        request, expected, observed=business_case()
        self.assertEqual('ready',check.evaluate(request)['status'])
        for value in ('first-generated-id','another-generated-id'):
            for section in ('request','before','after'):
                observed['operations']['add']['observed'][section]['/gameCode']=value
            for section in ('request','before'):
                observed['operations']['add']['prepared'][section]['/gameCode']=value
            for binding in observed['operations']['add']['bindings'].values():
                binding['value']=value
            replace_body(request,'business:actual',observed)
            self.assertEqual('ready',check.evaluate(request)['status'])

    def test_layer_versions_and_captured_relations(self):
        for mutation, code in (
            (lambda e,o:e.update(level='business_e2e'),'business_end_to_end_not_proven_by_adapter'),
            (lambda e,o:o['run'].update(testVersion='old-version'),'business_run_identity_or_version_mismatch'),
            (lambda e,o:o['operations']['add']['observed'].update(operationId='other-operation'),'business_event_identity_mismatch'),
            (lambda e,o:o['operations']['add']['observed'].update(path='/another'),'business_exchange_action_mismatch'),
            (lambda e,o:o['operations']['add']['observed']['request'].update({'/gameCode':'wrong'}),'business_exchange_preparation_mismatch'),
            (lambda e,o:o['operations']['add']['observed'].pop('after'),'business_selected_observation_missing'),
            (lambda e,o:o['operations']['add']['bindings']['actual'].update(value='wrong'),'business_binding_source_mismatch'),
            (lambda e,o:o['operations']['add']['verdict'].update(phase='FAIL'),'business_operation_not_passed'),
            (lambda e,o:o['operations']['add'].update(interrupted={'phase':'OUTCOME_UNKNOWN'}),'business_operation_failure_or_recovery_requires_review'),
            (lambda e,o:o['operations'].update(extra={}), 'business_operation_set_mismatch'),
            (lambda e,o:o['operations']['add']['observed']['after'].update({'/exists':False}), 'business_domain_postcondition_mismatch'),
            (lambda e,o:e['operations']['add'].pop('postconditions'), 'business_domain_expectation_missing'),
            (lambda e,o:o.update(read_errors=[{'error_type':'OSError'}]), 'business_capture_read_incomplete'),
        ):
            with self.subTest(code=code):
                request,expected,observed=business_case(); mutation(expected,observed)
                replace_body(request,'business:expected',expected);replace_body(request,'business:actual',observed)
                result=check.evaluate(request)
                self.assertIn(code,result['blocked']+result['missing'])

    def test_malformed_objects_and_projection_fields(self):
        for path,value in ((('run',),[]),(('operations',),[]),
                           (('operations','add'),[]),
                           (('operations','add','contract','state'),'prefix/gameCode/suffix'),
                           (('operations','add','contract','state'),[{},'/exists']),
                           (('operations','add','contract','bindings'),[])):
            request,expected,_=business_case(); cursor=expected
            for key in path[:-1]: cursor=cursor[key]
            cursor[path[-1]]=value; replace_body(request,'business:expected',expected)
            with self.subTest(path=path,value=value),self.assertRaises(check.CheckError):
                check.evaluate(request)
        request,expected,observed=business_case()
        expected['operations']['add']['contract']['request']=['/password']
        expected['operations']['add']['contract']['bindings']={}
        replace_body(request,'business:expected',expected)
        self.assertIn('business_sensitive_projection',check.evaluate(request)['blocked'])

    def test_native_source_scope_and_order(self):
        for mutate,code in (
            (lambda r:r['context']['evidence'][-1].update(task_ref='codex:thread:other'),'unresolved_business_run_reference'),
            (lambda r:r['context']['evidence'][-1].update(resource={'kind':'repo','id':'other','path':''}),'business_run_resource_mismatch'),
            (lambda r:r['context']['native_sources'][0]['turns'][0]['items'][-1]['output'].update(truncated=True),'unverified_business_run_source'),
            (lambda r:r['context']['native_sources'][0]['turns'][0]['items'].reverse(),'business_expectation_not_before_observation'),
            (lambda r:r['context']['evidence'][-1].update(related_ref='other'),'business_observation_expectation_mismatch'),
        ):
            request,_,_=business_case();mutate(request)
            with self.subTest(code=code):
                result=check.evaluate(request);self.assertIn(code,result['blocked']+result['missing'])

    def test_consistent_wrong_dynamic_relation_and_boolean_numbers_fail(self):
        request,expected,observed=business_case()
        observed['operations']['add']['observed']['after']['/gameCode']='unrelated'
        observed['operations']['add']['bindings']['actual']['value']='unrelated'
        replace_body(request,'business:actual',observed)
        self.assertIn('business_dynamic_relationship_mismatch',check.evaluate(request)['blocked'])
        request,expected,observed=business_case()
        observed['operations']['add']['observed']['after']['/exists']=1
        replace_body(request,'business:actual',observed)
        self.assertIn('business_domain_postcondition_mismatch',check.evaluate(request)['blocked'])
        request,expected,observed=business_case()
        observed['run']['schema']=True
        replace_body(request,'business:actual',observed)
        self.assertIn('business_run_identity_or_version_mismatch',check.evaluate(request)['blocked'])
        request,expected,observed=business_case()
        expected['operations']['add']['postconditions']={'after':{}}
        replace_body(request,'business:expected',expected)
        self.assertIn('business_domain_expectation_missing',check.evaluate(request)['missing'])

    def test_failed_business_review_keeps_independent_progress_read_ready(self):
        request,expected,observed=business_case()
        request['phase']='progress'
        request['progress']={'checkpoint':'after_failure','claim':'working','goal_ref':'business:goal',
                             'resource':request['action']['resource'],
                             'steps':[{'id':'independent-read','action':request['action']}]}
        observed['operations']['add']['verdict']['phase']='FAIL'
        replace_body(request,'business:actual',observed)
        result=check.evaluate(request)
        self.assertEqual(['independent-read'],result['work_partition']['ready_step_ids'])
        self.assertIn('business_operation_not_passed',result['missing'])
        expected['operations']['add']['postconditions']={'status':200}
        replace_body(request,'business:expected',expected)
        self.assertIn('business_domain_expectation_missing',check.evaluate(request)['missing'])

    def test_adapter_is_consumed_at_completed_boundary(self):
        request,expected,observed=business_case()
        request['phase']='progress'
        request['progress']={'checkpoint':'stage_end','claim':'completed','version':'core-v1',
                             'resource':request['action']['resource'],'steps':[],'goal_ref':'business:goal'}
        observed['operations']['add']['verdict']['phase']='FAIL'
        replace_body(request,'business:actual',observed)
        self.assertIn('business_operation_not_passed',check.evaluate(request)['missing'])


class BusinessEvidenceRepairChecks(unittest.TestCase):
    @staticmethod
    def with_http(observed):
        for captured in observed['operations'].values():
            http=copy.deepcopy(captured['observed'])
            http.pop('after')
            http['phase']='HTTP_OBSERVED'
            captured['http-observed']=http

    def test_present_required_operations_must_be_objects(self):
        for value in (None, False, 0, '', [], 'captured'):
            request,expected,observed=business_case()
            observed['operations']['add']=value
            replace_body(request,'business:actual',observed)
            with self.subTest(value=value),self.assertRaises(check.CheckError):
                check.evaluate(request)
        request,expected,observed=business_case()
        expected['operations']['cleanup']=copy.deepcopy(expected['operations']['add'])
        observed['operations']['cleanup']=None
        replace_body(request,'business:expected',expected);replace_body(request,'business:actual',observed)
        with self.assertRaises(check.CheckError):check.evaluate(request)
        request,expected,observed=business_case()
        observed['operations'].pop('add');replace_body(request,'business:actual',observed)
        self.assertIn('business_operation_set_mismatch',check.evaluate(request)['missing'])

    def test_complete_http_complement_is_optional_and_matches_full_observation(self):
        request,expected,observed=business_case()
        self.assertEqual('ready',check.evaluate(request)['status'])
        self.with_http(observed);replace_body(request,'business:actual',observed)
        self.assertEqual('ready',check.evaluate(request)['status'])
        for field,value in (('operationId','wrong'),('phase','OBSERVED'),('run',{}),
                            ('method','GET'),('path','/unrelated'),('status',201),
                            ('request',{}),('response',{'/wrong':True}),('before',{})):
            changed=copy.deepcopy(observed)
            changed['operations']['add']['http-observed'][field]=value
            replace_body(request,'business:actual',changed)
            with self.subTest(field=field):
                self.assertIn('business_http_observation_mismatch',check.evaluate(request)['blocked'])

    def test_http_complement_shapes_and_missing_full_result(self):
        for value in (None,False,0,[], 'HTTP_OBSERVED'):
            request,expected,observed=business_case()
            observed['operations']['add']['http-observed']=value
            replace_body(request,'business:actual',observed)
            with self.subTest(value=value),self.assertRaises(check.CheckError):check.evaluate(request)
        request,expected,observed=business_case();self.with_http(observed)
        # A historical PASS does not supply a missing full exchange or after-state.
        observed['operations']['add'].pop('observed')
        replace_body(request,'business:actual',observed)
        self.assertIn('business_operation_observed_missing',check.evaluate(request)['missing'])

    def test_old_pass_never_clears_later_failed_or_unfinished_attempt(self):
        for attempts in (
            {'recovery-first-started':{'phase':'RECOVERY_STARTED','attemptId':'first'}},
            {'recovery-first-started':{'phase':'RECOVERY_STARTED','attemptId':'first'},
             'recovery-first-failed':{'phase':'RECOVERY_FAILED','attemptId':'first'}},
            {'recovery-first-failed':{'phase':'RECOVERY_FAILED','attemptId':'first'},
             'recovery-second-verdict':{'phase':'PASS','attemptId':'second'}},
        ):
            request,expected,observed=business_case();self.with_http(observed)
            observed['operations']['add']['recovery']=attempts
            replace_body(request,'business:actual',observed)
            with self.subTest(attempts=attempts):
                self.assertIn('business_operation_failure_or_recovery_requires_review',check.evaluate(request)['missing'])
        request,expected,observed=business_case();self.with_http(observed)
        observed['operations']['add']['failed']={'phase':'CHECK_FAILED','stage':'after-state'}
        replace_body(request,'business:actual',observed)
        self.assertIn('business_operation_failure_or_recovery_requires_review',check.evaluate(request)['missing'])


if __name__=='__main__':
    unittest.main()
