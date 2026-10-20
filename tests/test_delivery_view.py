"""Current delivery relationships, using bounded synthetic native snapshots.

No native author, domain outcome or real platform permission is authenticated.
The normal CLI case proves this read-only path needs no TaskStore or scheduler.
"""
import copy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]/'scripts'))
import task_coordination as coord
import task_runtime as task


class DeliveryViewTests(unittest.TestCase):
    def seal(self, record):
        fields=('delivery_id','stage','case','task_id','baseline','version','inputs','result')
        record['report']=json.dumps({k:record[k] for k in fields},sort_keys=True)
        record['sha256']=task.digest(record['report'].encode())
        return record

    def request(self):
        targets = {'source': ('source-new', {}), 'build': ('build-new', {'source':'source-new'}),
                   'install': ('package-new', {'build':'build-new'}), 'load': ('package-new', {'install':'package-new'}),
                   'adoption': ('package-new', {'load':'package-new'}), 'verification': ('package-new', {'load':'package-new'})}
        records=[]
        for name,(version,inputs) in targets.items():
            records.append({'delivery_id':'deliver','stage':name,'case':'result',
                            'task_id':'verify' if name=='verification' else 'implement','baseline':'latest',
                            'version':version,'inputs':inputs,'result':'PASS',
                            'source_ref':'synthetic:original-report-'+name,'sha256':task.digest(name.encode())})
        for record in records:self.seal(record)
        return {'operation':'delivery-view','baseline':{'id':'latest','source_ref':'synthetic:current-user'},
                'mainlines':[{'id':'main','goal':'Latest full observable result','source_ref':'synthetic:mainline','delivery_ids':['deliver']}],
                'deliveries':[{'id':'deliver','goal':'Latest observable delivery','source_ref':'synthetic:original-contract',
                               'task_ids':['implement','verify'],'stages':{name:{'version':v,'inputs':inputs,
                                **({'independent':True} if name=='verification' else {})} for name,(v,inputs) in targets.items()}}],
                'tasks':[{'id':name,'thread_id':'thread-'+name,'turn_id':'current-'+name,'source_ref':'synthetic:original-'+name}
                         for name in ['implement','verify']],
                'native_sources':[{'schemaVersion':1,'thread':{'id':'thread-'+name},'turns':[{'id':'current-'+name,'status':'completed','items':[]}]}
                                  for name in ['implement','verify']], 'observations':records}

    def project(self, request):
        with mock.patch.object(task, 'Store', side_effect=AssertionError('no Store')):
            return coord.delivery_view(request)

    def test_current_full_relationship_positive_and_task_terminal_is_not_delivery(self):
        request=self.request();self.assertTrue(self.project(request)['complete'])
        request['observations']=[];out=self.project(request)
        self.assertEqual(out['tasks']['implement']['native_status'],'completed')
        self.assertFalse(out['complete']);self.assertFalse(out['deliveries']['deliver']['implementation_delivery']['complete'])

    def test_multiple_tasks_and_deliverables_keep_all_remaining_results(self):
        request=self.request();other=copy.deepcopy(request['deliveries'][0]);other['id']='second';request['deliveries'].append(other)
        request['mainlines'][0]['delivery_ids'].append('second');out=self.project(request)
        self.assertTrue(out['deliveries']['deliver']['complete']);self.assertFalse(out['deliveries']['second']['complete'])
        self.assertFalse(out['mainlines']['main']['complete'])

    def test_unfrozen_current_target_cannot_inherit_old_source_or_task_pass(self):
        request=self.request();request['deliveries'][0]['stages']['source']['version']=None
        out=self.project(request);self.assertFalse(out['complete'])
        self.assertIn('current_version_not_frozen',{g['kind'] for g in out['deliveries']['deliver']['gaps']})

    def test_each_stage_version_and_actual_input_link_stays_distinct(self):
        for stage in coord.DELIVERY_STAGES:
            request=self.request();next(r for r in request['observations'] if r['stage']==stage)['version']='old'
            self.seal(next(r for r in request['observations'] if r['stage']==stage))
            with self.subTest(stage=stage):self.assertFalse(self.project(request)['deliveries']['deliver']['stages'][stage]['complete'])
        request=self.request();request['deliveries'][0]['stages']['install']['inputs']['build']='old-build'
        self.assertFalse(self.project(request)['complete'])

    def test_historical_pass_requires_exact_five_axis_applicability(self):
        request=self.request()
        for record in request['observations']:
            record['baseline']='old-27a';self.seal(record)
        self.assertFalse(self.project(request)['complete'])
        for record in request['observations']:
            record['reuse']={'baseline':'latest','source_ref':'synthetic:actual-current-assessment',
                             **{axis:'unchanged' for axis in coord.REUSE_AXES}}
        self.assertTrue(self.project(request)['complete'])
        for axis in coord.REUSE_AXES:
            altered=copy.deepcopy(request);altered['observations'][0]['reuse'][axis]='changed'
            with self.subTest(axis=axis):self.assertFalse(self.project(altered)['complete'])

    def test_new_required_case_needs_new_affected_verification(self):
        request=self.request();request['deliveries'][0]['stages']['verification']['checks']=['result','new-case']
        out=self.project(request);self.assertTrue(out['deliveries']['deliver']['implementation_delivery']['complete'])
        self.assertFalse(out['deliveries']['deliver']['verification_delivery']['complete'])
        added=copy.deepcopy(request['observations'][-1]);added['case']='new-case';added['source_ref']='synthetic:actually-new-case'
        request['observations'].append(added);self.assertFalse(self.project(request)['complete'])
        self.seal(added);self.assertTrue(self.project(request)['complete'])

    def test_implementation_verification_and_adoption_do_not_replace_each_other(self):
        request=self.request();request['observations']=[r for r in request['observations'] if r['stage']!='verification']
        out=self.project(request);self.assertTrue(out['deliveries']['deliver']['implementation_delivery']['complete'])
        self.assertFalse(out['deliveries']['deliver']['verification_delivery']['complete'])
        request=self.request();request['observations']=[r for r in request['observations'] if r['stage']!='adoption']
        out=self.project(request);self.assertTrue(out['deliveries']['deliver']['verification_delivery']['complete'])
        self.assertFalse(out['deliveries']['deliver']['implementation_delivery']['complete'])

    def test_verification_must_bind_current_loaded_bundle(self):
        request=self.request();request['observations'][-1]['inputs']['load']='package-old';self.seal(request['observations'][-1])
        self.assertFalse(self.project(request)['complete'])
        request=self.request();request['deliveries'][0]['stages']['verification']['inputs']['load']='package-old'
        self.assertFalse(self.project(request)['complete'])

    def test_self_review_does_not_become_independent_through_role_names(self):
        request=self.request();request['tasks'][1]['thread_id']=request['tasks'][0]['thread_id']
        out=self.project(request);self.assertFalse(out['complete'])
        self.assertIn('independent_verification_not_bound',{g['kind'] for g in out['deliveries']['deliver']['gaps']})

    def test_bad_delivery_is_local_and_independent_complete_delivery_remains_visible(self):
        request=self.request();bad=copy.deepcopy(request['deliveries'][0]);bad.update(id='bad',stages=[])
        request['deliveries'].append(bad);request['mainlines'][0]['delivery_ids'].append('bad')
        out=self.project(request);self.assertTrue(out['deliveries']['deliver']['complete']);self.assertFalse(out['deliveries']['bad']['complete'])
        self.assertFalse(out['summary_is_execution_gate'])

    def test_conflicting_selected_turn_does_not_first_win_or_use_old_completed_turn(self):
        request=self.request();changed=copy.deepcopy(request['native_sources'][0]);changed['turns'][0]['status']='failed'
        request['native_sources'].append(changed);out=self.project(request)
        self.assertEqual(out['tasks']['implement']['native_status'],'unknown');self.assertFalse(out['complete'])
        request=self.request();request['tasks'][0]['turn_id']='unobserved-new-turn'
        self.assertEqual(self.project(request)['tasks']['implement']['native_status'],'unknown')

    def test_conflicting_original_result_hash_is_not_new_result_coverage(self):
        request=self.request();changed=copy.deepcopy(request['observations'][0]);changed['source_ref']='synthetic:renamed-summary';changed['sha256']='f'*64
        request['observations'].append(changed);self.assertFalse(self.project(request)['complete'])

    def test_malformed_and_unlinked_cases_do_not_silently_disappear(self):
        request=self.request();request['mainlines'][0]['delivery_ids']=['missing']
        out=self.project(request);self.assertFalse(out['complete']);self.assertTrue(out['issues'])
        request=self.request();request['tasks'].append(copy.deepcopy(request['tasks'][0]))
        with self.assertRaises(task.TaskError):self.project(request)

    def test_unfrozen_or_bad_stage_retains_all_original_stage_targets(self):
        request=self.request();request['deliveries'][0]['stages']['source']['version']=None
        out=self.project(request);row=out['deliveries']['deliver']
        self.assertEqual(set(row['stages']),set(coord.DELIVERY_STAGES))
        self.assertEqual(row['stages']['adoption']['target']['version'],'package-new')
        self.assertFalse(row['complete'])

    def test_deep_invalid_report_is_a_local_gap_not_a_traceback(self):
        request=self.request();record=request['observations'][0]
        record['report']='['*1500+'0'+']'*1500;record['sha256']=task.digest(record['report'].encode())
        bad=copy.deepcopy(request['deliveries'][0]);bad['id']='bad';request['deliveries'].append(bad)
        request['mainlines'][0]['delivery_ids'].append('bad')
        record['delivery_id']='bad';request['observations'][0]=self.seal(copy.deepcopy(self.request()['observations'][0]))
        request['observations'].append(record)
        out=self.project(request)
        self.assertTrue(out['deliveries']['deliver']['complete']);self.assertFalse(out['deliveries']['bad']['complete'])

    def test_normal_cli_delivery_view_never_creates_home_or_task_store(self):
        with tempfile.TemporaryDirectory() as directory:
            home=Path(directory)/'not-created';repo=Path(__file__).resolve().parents[1]
            argv=[sys.executable,str(repo/'scripts/codex_framework.py'),'--home',str(home),'task']
            p=subprocess.run(argv,input=json.dumps(self.request()),text=True,capture_output=True,timeout=10)
            self.assertEqual(p.returncode,0,p.stderr);self.assertEqual(p.stderr,'');out=json.loads(p.stdout)
            self.assertTrue(out['complete']);self.assertEqual(out['effect'],'pure_projection_no_store_or_tool_calls')
            self.assertFalse(home.exists())


if __name__=='__main__':unittest.main()
