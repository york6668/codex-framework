"""Finite project orchestration regressions. No model, native queries or DB use."""
import copy
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
from unittest import mock

import test_framework as fixtures
import task_runtime as task
import task_coordination as coord
import framework_runtime as runtime


def reserve_in_process(home, identity, gate, out):
    gate.wait(5)
    store=task.Store(home,identity)
    try:
        value,execution=task.reserve(store,1)
        out.put({'task':identity,'reserved':bool(execution),'ready':task.partition(value)['ready']})
    except Exception as exc:out.put({'task':identity,'error':repr(exc)})


class ProjectRuntimeTests(fixtures.FixtureCase):
    def spec(self, identity, project=None, role='implementation', **extra):
        value={'request_id':'request-'+identity,'requirement':'Complete current '+identity+' deliverable',
               'acceptance_version':'v1','workspace':str(self.root),
               'steps':[{'id':'a','instruction':'Complete only assigned deliverable','checks':[{'kind':'message','equals':'42'}]}]}
        if project:
            value.update(project={'id':project,'goal':'Goal '+project,'responsibility':'Duty '+project,'source_ref':'synthetic:user-current',
                                  'obligations':[{'id':'deliver','acceptance':'Observable '+project+' result','task_ids':[identity]}]},
                         assignment={'owner':'owner-'+identity,'role':role,'delivery_owner':'integrator','covers':['deliver']})
        elif role!='implementation':value['assignment']={'owner':'owner-'+identity,'role':role,'delivery_owner':'integrator','covers':[]}
        value.update(extra);return value

    def create(self,identity,**options):
        store=task.Store(self.a.codex_home,identity)
        return store,store.create(identity,self.spec(identity,**options))

    def change(self,store,operation,**fields):
        return task.change(store,{'operation':operation,'expected_revision':store.read()['revision'],**fields})

    def worker(self):
        return {'session_id':'synthetic-worker','status':'completed','message':'42','message_sha256':task.digest(b'42'),'exit':0,'events_sha256':'1'*64}

    def run_synthetic(self,store):
        with mock.patch('task_worker.execute',return_value=self.worker()):return task.run(store,store.read()['revision'])

    def report(self,value,**fields):
        result={'request_id':value['spec']['request_id'],'task_id':value['task_id'],'input_revision':value['input_revision'],
                'task_input_sha256':value['acceptance_sha256'],'verdict':'PASS'}
        if value['spec'].get('project'):result['project_id']=value['spec']['project']['id']
        if value['spec'].get('request_identity'):result['request_identity']=value['spec']['request_identity']
        result.update(fields);return result

    def put_report(self,name,report):
        path=self.root/name;path.write_bytes(task.encoded(report));return {'path':name,'sha256':task.digest(path.read_bytes())}

    def observe(self,store,**fields):
        value=store.read();ref=self.put_report(value['task_id']+'-observed.json',self.report(value,**fields))
        with mock.patch('task_worker.execute',side_effect=AssertionError('no model')),mock.patch('task_worker.inspect_session',side_effect=AssertionError('no native query')):
            return task.observe_external(store,value['revision'],ref)

    def complete_external(self,identity,project=None,role='implementation',review=False,**extra):
        path=self.root/(identity+'.txt');path.write_text(identity)
        steps=[{'id':'a','instruction':'Observe already produced '+identity,'checks':[{'kind':'file','path':path.name,'sha256':task.digest(path.read_bytes())}]}]
        store,value=self.create(identity,project=project,role=role,steps=steps,execution_mode='external',review_required=review,**extra)
        value=self.observe(store)
        if review:
            ref=self.put_report(identity+'-review.json',self.report(value,reviewer_thread='independent-'+identity,candidate_tree='1'*40))
            self.change(store,'review',report_path=str(self.root/ref['path']),report_sha256=ref['sha256'])
            value=self.change(store,'accept')
        self.assertTrue(value['accepted']);return store,value

    def edge(self,value,status='proven'):
        return {'task_id':value['task_id'],'step_id':None,'input_sha256':value['acceptance_sha256'],
                'basis':{'status':status,'source_ref':'synthetic:actual-producer','reason':'Consumer needs accepted output'}}

    def resource(self,object='schema/table',mode='read',operation='inspect'):
        return {'kind':'logical','identity':'synthetic://node/shared-db','object':object,'mode':mode,'operation':operation,'source_ref':'synthetic:known-object'}

    def coordinate(self,ids,**extra):
        return coord.workset(self.a.codex_home,{'operation':'coordinate','task_ids':ids,**extra})

    def test_three_projects_current_goal_and_request_enter_actual_prompt(self):
        captures=[];stores=[]
        def execute(prompt,*args,**kwargs):captures.append(prompt);return self.worker()
        for p in ['core','business','resources']:
            store,value=self.create(p,project=p,request_identity={'thread_id':'thread-'+p,'turn_id':'turn-current','message_id':'message-current'});stores.append(store)
        with mock.patch('task_worker.execute',side_effect=execute):
            out=coord.workset(self.a.codex_home,{'operation':'work','task_ids':['core','business','resources'],'expected_revisions':{p:1 for p in ['core','business','resources']},'max_turns':3})
        self.assertEqual([x['task_id'] for x in out['executed']],['core','business','resources'])
        self.assertTrue(all(out['projects'][p]['complete'] for p in ['core','business','resources']))
        self.assertFalse(out['overall_complete'])
        for p,prompt in zip(['core','business','resources'],captures):
            for expected in ['Goal '+p,'Duty '+p,'owner-'+p,'integrator','request-'+p,'turn-current','message-current']:self.assertIn(expected,prompt)

    def test_new_fault_amends_only_original_owner_and_keeps_other_records(self):
        core,_=self.create('core',project='core');business,_=self.create('business',project='business');resources,_=self.create('resources',project='resources')
        self.change(resources,'approval',step='a',decision='denied',source_ref='synthetic:exact-original-refusal')
        before={s.path:s.path.read_bytes() for s in [business,resources]}
        changed=core.read()['spec'];changed['requirement']+='; new bounded fault from investigator';changed['acceptance_version']='v2'
        value=self.change(core,'amend',spec=changed)
        self.assertEqual(value['spec']['assignment']['owner'],'owner-core')
        for s in [business,resources]:self.assertEqual(s.path.read_bytes(),before[s.path])
        self.assertEqual(resources.read()['steps']['a']['wait']['kind'],'explicit_denial')

    def test_one_line_refusal_only_blocks_proven_consumers(self):
        resource,r=self.create('resource',project='resources')
        self.change(resource,'approval',step='a',decision='denied',source_ref='synthetic:denied-object')
        consumer,_=self.create('consumer',requires=[self.edge(r)])
        hypothesis,_=self.create('hypothesis',requires=[self.edge(r,'hypothesis')])
        independent,_=self.create('independent')
        out=self.coordinate(['resource','consumer','hypothesis','independent'])
        self.assertEqual(out['tasks']['consumer']['work_partition']['ready'],[])
        for identity in ['hypothesis','independent']:self.assertEqual(out['tasks'][identity]['work_partition']['ready'],['a'])
        self.assertEqual(resource.read()['steps']['a']['wait']['kind'],'explicit_denial')

    def test_explicit_local_hypothesis_preserves_legacy_order_without_promoting_it(self):
        steps=[{'id':'a','instruction':'Original denied step','checks':[{'kind':'message','equals':'42'}]},
               {'id':'b','instruction':'Independent verification','depends_on':['a'],'depends_on_basis':{'status':'hypothesis','source_ref':'synthetic:unverified'},'checks':[{'kind':'message','equals':'42'}]}]
        store,_=self.create('local',steps=steps);value=self.change(store,'approval',step='a',decision='denied',source_ref='synthetic:refusal')
        self.assertEqual(task.partition(value)['ready'],['b'])
        legacy=copy.deepcopy(value);del legacy['spec']['steps'][1]['depends_on_basis']
        self.assertEqual(task.partition(legacy)['ready'],[])
        self.assertEqual(self.run_synthetic(store)['steps']['b']['state'],'verified')

    def test_two_process_shared_file_reservation_is_exclusive(self):
        checks=[{'kind':'file','path':'shared.txt','sha256':'1'*64}]
        for identity in ['writer-a','writer-b']:self.create(identity,steps=[{'id':'a','instruction':'Write same shared file','checks':checks}])
        context=multiprocessing.get_context('fork');gate=context.Event();out=context.Queue()
        processes=[context.Process(target=reserve_in_process,args=(str(self.a.codex_home),i,gate,out)) for i in ['writer-a','writer-b']]
        for p in processes:p.start()
        gate.set();results=[out.get(timeout=10) for p in processes]
        for p in processes:p.join(10);self.assertEqual(p.exitcode,0)
        self.assertFalse(any('error' in r for r in results),results)
        self.assertEqual(sum(r['reserved'] for r in results),1,results)
        other,_=self.create('other');self.assertEqual(task.partition(other.read())['ready'],['a'])

    def test_released_occupation_resumes_waiter_without_clearing_original_refusal(self):
        checks=[{'kind':'file','path':'released.txt','sha256':task.digest(b'actual')}]
        first,_=self.create('first-writer',steps=[{'id':'a','instruction':'Write target','checks':checks}])
        waiting,_=self.create('waiting-writer',steps=[{'id':'a','instruction':'Write target','checks':checks}])
        value,execution=task.reserve(first,1);blocked,no_execution=task.reserve(waiting,1)
        self.assertFalse(no_execution);self.assertEqual(blocked['steps']['a']['state'],'blocked')
        (self.root/'released.txt').write_bytes(b'actual')
        task.finish(first,value['revision'],execution['id'],self.worker())
        with coord.context(waiting.root):self.assertEqual(task.partition(waiting.read())['ready'],['a'])
        resumed,new_execution=task.reserve(waiting,blocked['revision']);self.assertTrue(new_execution)
        self.assertEqual(resumed['steps']['a']['state'],'running')

    def test_db_read_sharing_exact_write_conflict_and_unrelated_object(self):
        def create(identity,resource):return self.create(identity,steps=[{'id':'a','instruction':'Synthetic resource declaration only','resources':[resource],'checks':[{'kind':'message','equals':'42'}]}])[0]
        reader=create('reader',self.resource());a,e=task.reserve(reader,1);self.assertTrue(e)
        compatible=create('compatible',self.resource());self.assertTrue(task.reserve(compatible,1)[1])
        writer=create('writer',self.resource(mode='write',operation='update'));self.assertFalse(task.reserve(writer,1)[1])
        different=create('different',self.resource(object='schema/other',mode='write',operation='update'));self.assertTrue(task.reserve(different,1)[1])

    def test_denied_effect_survives_new_task_without_blocking_different_operation(self):
        def steps(operation):return [{'id':'a','instruction':'Synthetic logical effect','resources':[self.resource(mode='write',operation=operation)],'checks':[{'kind':'message','equals':'42'}]}]
        store,_=self.create('old',steps=steps('update'));self.change(store,'approval',step='a',decision='denied',source_ref='synthetic:platform-exact-update')
        same,_=self.create('renamed',steps=steps('update'));read,_=self.create('read',steps=[{'id':'a','instruction':'Independent permitted inspect','resources':[self.resource()],'checks':[{'kind':'message','equals':'42'}]}])
        self.assertFalse(task.reserve(same,1)[1]);self.assertTrue(task.reserve(read,1)[1])
        self.assertEqual(store.read()['steps']['a']['wait']['source_ref'],'synthetic:platform-exact-update')

    def test_cross_task_refusal_retains_inode_after_original_path_is_moved(self):
        original=self.root/'original-object';original.write_bytes(b'x')
        def steps(path):return [{'id':'a','instruction':'Write this same file effect','checks':[{'kind':'file','path':path,'sha256':task.digest(b'x')}]}]
        old,_=self.create('original-owner',steps=steps(original.name));self.change(old,'approval',step='a',decision='denied',source_ref='synthetic:original-object-refusal')
        alias=self.root/'alias-object';os.link(original,alias);original.rename(self.root/'moved-object')
        candidate,_=self.create('renamed-task',steps=steps(alias.name))
        value,execution=task.reserve(candidate,1)
        self.assertFalse(execution);self.assertIn('retained_refusal',value['steps']['a']['wait']['reason'])
        self.assertEqual(old.read()['restrictions'][0]['resource_effects'][0]['object_identity']['inode'],alias.stat().st_ino)

    def test_same_object_write_refusal_is_not_cleared_by_renaming_operation(self):
        target=self.root/'same-effect';target.write_bytes(b'x')
        def steps(operation):return [{'id':'a','instruction':'Declared file write','resources':[{'kind':'file','identity':target.name,'object':'','mode':'write','operation':operation,'source_ref':'synthetic:known-file-effect'}],'checks':[{'kind':'message','equals':'42'}]}]
        old,_=self.create('old-effect',steps=steps('old-name'));self.change(old,'approval',step='a',decision='denied',source_ref='synthetic:original-file-write-refusal')
        renamed,_=self.create('renamed-effect',steps=steps('new-name'))
        value,execution=task.reserve(renamed,1);self.assertFalse(execution);self.assertEqual(value['steps']['a']['runs'],0)
        self.assertEqual(value['steps']['a']['wait']['reason'],'related_retained_refusal')

    def test_external_arbitrary_command_is_not_downgraded_to_passive_read(self):
        target=self.root/'protected-target';target.write_bytes(b'original')
        old,_=self.create('original-write',steps=[{'id':'a','instruction':'Original denied write','checks':[{'kind':'file','path':target.name,'sha256':task.digest(b'original')}]}])
        self.change(old,'approval',step='a',decision='denied',source_ref='synthetic:precise-original-write-denial')
        command=[sys.executable,'-c',"from pathlib import Path;Path('protected-target').write_text('changed-by-validator')"]
        with self.assertRaisesRegex(task.TaskError,'passive file checks'):
            self.create('command-observation',execution_mode='external',steps=[{'id':'a','instruction':'Claim existing effects','resources':[{'kind':'file','identity':target.name,'object':'','mode':'write','operation':'write','source_ref':'synthetic:declared-write'}],'checks':[{'kind':'command','argv':command,'timeout':1}]}])
        self.assertEqual(target.read_bytes(),b'original')
        # A genuinely passive observation can read the existing file without a write.
        observed,_=self.create('passive-observation',execution_mode='external',steps=[{'id':'a','instruction':'Read existing allowed output only','checks':[{'kind':'file','path':target.name,'sha256':task.digest(b'original')}]}])
        self.assertTrue(self.observe(observed)['accepted']);self.assertEqual(target.read_bytes(),b'original')

    def test_old_request_report_rejected_and_current_return_does_not_accept(self):
        store,value=self.create('current',project='core',request_identity={'thread_id':'thread','turn_id':'current','message_id':'new'})
        old=self.report(value);old['request_identity']=dict(old['request_identity'],turn_id='old')
        with self.assertRaisesRegex(task.TaskError,'current project/task/native request'):coord.bound_report(value,old)
        for key,bad in [('project_id','resources'),('task_id','different'),('request_id','old'),('input_revision',0),('input_revision',True),('task_input_sha256','0'*64)]:
            report=self.report(value);report[key]=bad
            with self.subTest(key=key),self.assertRaises(task.TaskError):coord.bound_report(value,report)
        coord.bound_report(value,self.report(value));self.assertFalse(store.read()['accepted'])

    def test_missing_obligation_keeps_project_open_after_local_pass(self):
        spec=self.spec('first',project='core');spec['project']['obligations'][0]['task_ids'].append('missing-task')
        store=task.Store(self.a.codex_home,'first');store.create('first',spec);self.run_synthetic(store)
        out=self.coordinate(['first']);self.assertTrue(out['tasks']['first']['accepted']);self.assertFalse(out['projects']['core']['complete']);self.assertFalse(out['overall_complete'])
        self.assertIn('required_task_missing',{g['kind'] for g in out['projects']['core']['gaps']})

    def test_explicit_pause_stays_in_project_and_not_cleared_by_new_input(self):
        a,_=self.create('paused',project='core');b,_=self.create('continue',project='business')
        self.change(a,'block',step='a',condition='User paused this project',evidence_ref='synthetic:direct-user-project-scope')
        current=a.read()['spec'];current['acceptance_version']='v2';value=self.change(a,'amend',spec=current)
        self.assertFalse(task.partition(value)['ready']);self.assertTrue(self.run_synthetic(b)['accepted'])

    def test_external_effect_observation_has_no_model_or_native_worker(self):
        store,value=self.complete_external('observed',project='core')
        self.assertEqual(value['steps']['a']['runs'],0);self.assertIsNone(value['native_session_id']);self.assertEqual(value['steps']['a']['evidence'],[])
        self.assertEqual(value['history'][0]['external_observation']['producer_kind'],'external_observation')
        with self.assertRaisesRegex(task.TaskError,'without model dispatch'):task.run(store,value['revision'])
        with self.assertRaisesRegex(task.TaskError,'not model resume'):task.resume(store)

    def test_external_report_pass_cannot_replace_missing_actual_file_or_denial(self):
        steps=[{'id':'a','instruction':'Existing output','checks':[{'kind':'file','path':'absent','sha256':'1'*64}]}]
        store,_=self.create('missing-effect',execution_mode='external',steps=steps)
        value=self.observe(store);self.assertFalse(value['accepted']);self.assertEqual(value['steps']['a']['state'],'blocked')
        other,value=self.create('denied-effect',execution_mode='external',steps=steps);self.change(other,'approval',step='a',decision='denied',source_ref='synthetic:hard-refusal')
        with self.assertRaisesRegex(task.TaskError,'cannot clear'):self.observe(other)

    def prepare_final_chain(self):
        components=[]
        for p in ['core','business','resources']:components.append(self.complete_external(p,project=p,review=True)[1])
        versions=[];files=[]
        for p in ['core','business','resources']:
            repo=self.root/('source-'+p);repo.mkdir();fixtures.run_git(repo,'init','-b','release');fixtures.run_git(repo,'config','user.email','fixture@example.invalid');fixtures.run_git(repo,'config','user.name','Fixture')
            (repo/'result.txt').write_text('merged '+p);fixtures.run_git(repo,'add','.');fixtures.run_git(repo,'commit','-m','isolated merged delivery')
            commit=fixtures.run_git(repo,'rev-parse','HEAD');tree=fixtures.run_git(repo,'rev-parse','HEAD^{tree}')
            versions.append({'project_id':p,'repo':repo.name,'ref':'refs/heads/release','commit':commit,'tree':tree})
            loaded=self.root/('loaded-'+p);loaded.write_bytes((repo/'result.txt').read_bytes());files.append({'project_id':p,'source':'result.txt','loaded':loaded.name,'sha256':task.digest(loaded.read_bytes())})
        ref=self.put_report('loaded-manifest.json',{'versions':versions,'files':files});artifact={'manifest':ref['path'],'sha256':ref['sha256'],'versions':versions}
        integration,iv=self.complete_external('integrate',role='integration',requires=[self.edge(v) for v in components],artifact=artifact)
        regression,rv=self.complete_external('regress',role='final_regression',requires=[self.edge(iv)],artifact=artifact)
        report=self.report(rv,artifact_sha256=artifact['sha256'],component_inputs={v['task_id']:v['acceptance_sha256'] for v in components},reviewer_thread='independent-cross-line')
        rr=self.put_report('final-regression.json',report)
        tracking,tv=self.complete_external('tracking',role='coordination',requires=[self.edge(rv)])
        # The own fixture tracking output is the existing ledger stand-in; no real ledger is accessed.
        final={'components':[v['task_id'] for v in components],'integration':iv['task_id'],'regression':rv['task_id'],'tracking':tv['task_id'],'regression_report':rr,'tracking_file':{'path':'tracking.txt','sha256':task.digest(b'tracking')}}
        return [v['task_id'] for v in components]+['integrate','regress','tracking'],final,report

    def test_three_local_pass_require_actual_merge_load_cross_line_review_and_original_tracking_effect(self):
        ids,final,report=self.prepare_final_chain()
        self.assertFalse(self.coordinate(ids)['overall_complete'])
        result=self.coordinate(ids,finalization=final);self.assertTrue(result['overall_complete'],result['finalization'])
        missing=self.coordinate(ids+['current-new-fault'],finalization=final)
        self.assertFalse(missing['overall_complete']);self.assertIn('named_task_not_observed',{g['kind'] for g in missing['finalization']['gaps']})
        self.create('current-existing-fault')
        pending=self.coordinate(ids+['current-existing-fault'],finalization=final)
        self.assertFalse(pending['overall_complete']);self.assertIn('named_tasks_remaining',{g['kind'] for g in pending['finalization']['gaps']})
        (self.root/'loaded-business').write_text('unreviewed installed bytes')
        result=self.coordinate(ids,finalization=final);self.assertFalse(result['overall_complete']);self.assertIn('final_acceptance_unproven',{g['kind'] for g in result['finalization']['gaps']})

    def test_final_loaded_version_bundle_must_cover_every_component_project(self):
        original=self.put_report
        def narrow(name,report):
            if name=='loaded-manifest.json':
                report['versions'][:]=[v for v in report['versions'] if v['project_id']=='core']
                report['files'][:]=[v for v in report['files'] if v['project_id']=='core']
            return original(name,report)
        with mock.patch.object(self,'put_report',side_effect=narrow):ids,final,report=self.prepare_final_chain()
        (self.root/'loaded-business').unlink();(self.root/'loaded-resources').unlink()
        result=self.coordinate(ids,finalization=final);self.assertFalse(result['overall_complete'])
        self.assertTrue(any('omits a component project' in g.get('detail','') for g in result['finalization']['gaps']))

    def test_unobserved_named_task_keeps_whole_open_but_independent_work_runs(self):
        store,_=self.create('allowed-work')
        with mock.patch('task_worker.execute',return_value=self.worker()) as producer:
            result=coord.workset(self.a.codex_home,{'operation':'work','task_ids':['missing-current','allowed-work'],'expected_revisions':{'missing-current':0,'allowed-work':1},'max_turns':2})
        self.assertEqual(producer.call_count,1);self.assertTrue(result['tasks']['allowed-work']['accepted'])
        self.assertFalse(result['overall_complete']);self.assertIn('missing-current',result['errors'])

    def test_current_cross_line_request_and_tracking_bytes_are_required(self):
        ids,final,report=self.prepare_final_chain()
        original=(self.root/'final-regression.json').read_bytes()
        report['request_id']='old-archive';final['regression_report']=self.put_report('final-regression.json',report)
        self.assertFalse(self.coordinate(ids,finalization=final)['overall_complete'])
        (self.root/'final-regression.json').write_bytes(original);final['regression_report']['sha256']=task.digest(original)
        (self.root/'tracking.txt').write_text('old ledger bytes');self.assertFalse(self.coordinate(ids,finalization=final)['overall_complete'])

    def test_isolated_installed_cli_current_request_and_physical_collection(self):
        fixtures.install_runtime(self.a)
        cli=[sys.executable,str(runtime.launcher_path(self.a)),'--home',str(self.a.home),'task']
        def invoke(value):
            result=subprocess.run(cli,input=json.dumps(value),text=True,capture_output=True,timeout=12)
            self.assertEqual(result.stderr,'');return result,json.loads(result.stdout)
        (self.root/'cli-output').write_bytes(b'actual')
        spec=self.spec('cli',project='core',execution_mode='external',steps=[{'id':'a','instruction':'Collect already produced effect','checks':[{'kind':'file','path':'cli-output','sha256':task.digest(b'actual')}]}])
        result,value=invoke({'operation':'register','task_id':'cli','spec':spec});self.assertEqual(result.returncode,0)
        reference=self.put_report('cli-report.json',self.report(value))
        result,reply=invoke({'operation':'verify-return','task_id':'cli',**reference});self.assertEqual(result.returncode,0);self.assertEqual(reply['status'],'bound')
        result,reply=invoke({'operation':'verify-effects','task_id':'cli','expected_revision':1,**reference});self.assertEqual(result.returncode,0);self.assertTrue(reply['accepted']);self.assertEqual(reply['steps']['a']['runs'],0)
        result,reply=invoke({'operation':'coordinate','task_ids':['cli']});self.assertEqual(result.returncode,0);self.assertFalse(reply['overall_complete'])
        result,reply=invoke({'operation':'work','task_ids':['cli'],'expected_revisions':{'cli':reply['tasks']['cli']['revision']}});self.assertEqual(result.returncode,2);self.assertEqual(reply['executed'],[])

    def test_optional_check_basis_does_not_change_omitted_input_semantics(self):
        import execution_check as check
        import test_progress_check as inputs
        helper=inputs.ProgressChecks();request=helper.progress(helper.base())
        before=check.evaluate(request)
        self.assertEqual({v['id']:v['state'] for v in before['steps']}['verify'],'dependency_blocked')
        request['progress']['steps'][1]['depends_on_basis']={'status':'hypothesis','source_ref':'synthetic:unproven'}
        result=check.evaluate(request);by={v['id']:v for v in result['steps']}
        self.assertEqual(by['verify']['state'],'ready');self.assertEqual(by['restore']['state'],'direct_refusal')
        self.assertEqual(by['verify']['unproven_dependencies'],['restore'])
        del request['progress']['steps'][1]['depends_on_basis']
        self.assertEqual(check.evaluate(request),before)

    def test_new_input_shape_errors_are_structured_not_exceptions(self):
        for field,value in [('project',[]),('assignment',[]),('request_identity',[]),('requires',[{}]),('artifact',[]),('known_tasks',[[]])]:
            spec=self.spec('bad');spec[field]=value
            with self.subTest(field=field),self.assertRaises(task.TaskError):task.specification(spec)
        resource=self.resource();resource['mode']=[]
        with self.assertRaises(task.TaskError):task.specification(self.spec('bad',steps=[{'id':'a','instruction':'Bad shape','checks':[{'kind':'message','equals':'42'}],'resources':[resource]}]))
