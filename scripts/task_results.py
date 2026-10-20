"""Original result binding, partial coverage and intake/review.

The caller supplies the existing bounded IO/effect context. This module never
imports the runtime or coordination, opens a Store, or authenticates file labels.
"""
import copy


class TaskResults:
    def __init__(self, context):
        self.context = context

    def bound_report(self,value,report):
        self.context.need(isinstance(report,dict),'report must be an object')
        expected={'request_id':value['spec']['request_id'],'task_id':value['task_id'],'input_revision':value['input_revision'],'task_input_sha256':value['acceptance_sha256']}
        if value['spec'].get('project'):expected['project_id']=value['spec']['project']['id']
        if value['spec'].get('request_identity'):expected['request_identity']=value['spec']['request_identity']
        self.context.need(all(type(report.get(k)) is type(v) and report[k]==v for k,v in expected.items()),'report does not answer current project/task/native request/input')
        return expected


    def validate_result_scope(self,scope):
        self.context.need(isinstance(scope,dict) and set(scope)=={'object_ref','goal','source_ref','obligations'},'invalid original result scope')
        self.context.need(all(self.context.text(scope[k]) for k in ('object_ref','goal','source_ref')),'original object/goal/source missing')
        rows=scope['obligations'];self.context.need(isinstance(rows,list) and 0<len(rows)<=32,'invalid original obligations')
        seen=set()
        for row in rows:
            self.context.need(isinstance(row,dict) and set(row)=={'id','acceptance'} and self.context.identifier(row.get('id')) and self.context.text(row.get('acceptance')) and row['id'] not in seen,'invalid original obligation')
            seen.add(row['id'])


    def result_state(self,value):
        return value.get('result_state')


    def initialize_result(self,value,prior=None):
        scope=value['spec'].get('result_scope')
        if scope is None:
            self.context.need(prior is None,'original result obligations cannot be removed by amendment');return
        if prior:
            self.context.need(scope['object_ref']==prior['object_ref'],'amendment cannot substitute original business object')
        state=copy.deepcopy(prior) if prior else {'object_ref':scope['object_ref'],'original_goal':scope['goal'],'obligations':{},'pending':[]}
        for row in scope['obligations']:
            old=state['obligations'].get(row['id'])
            self.context.need(old is None or old==row['acceptance'],'amendment cannot silently change original acceptance')
            if old is None:state['pending'].append(row['id'])
            state['obligations'][row['id']]=row['acceptance']
        # Current-request acceptance always requires a current result/review. Old
        # finite results stay in history, not as an answer to the amended input.
        state['pending']=list(state['obligations']);state['result']=None;state['review']=None;state['receipts']=[]
        self.context.need(len(state['obligations'])<=64,'retained original obligation bound')
        value['result_state']=state


    def evidence_reference(self,value,ref):
        self.context.need(isinstance(ref,dict) and set(ref)=={'path','sha256'},'invalid passive evidence reference')
        self.context.relative(ref.get('path'),'passive evidence path');self.context.need(self.context.hashes(ref.get('sha256')),'invalid passive evidence hash')
        check={'kind':'file',**ref}
        # This is a passive evidence read, not execution of an arbitrary first
        # registered step. Exact local/peer path and FD guards remain mandatory;
        # original step/dependency constraints still govern execution/acceptance.
        self.context.need(self.context.checked_file(value,None,check)['passed'],'passive evidence bytes unavailable, changed or blocked')


    def origin(self,value):
        self.context.need(isinstance(value,dict) and set(value)=={'operation_id','attempt_id','session_id','turn_id'},'invalid original operation/attempt/native mapping')
        self.context.need(self.context.text(value['operation_id']) and self.context.text(value['attempt_id']),'original operation/attempt missing')
        self.context.need(all(value[k] is None or self.context.text(value[k]) for k in ('session_id','turn_id')),'invalid original native identity')
        self.context.need(value['turn_id'] is None or value['session_id'] is not None,'native turn requires original session')


    def checkpoint(self,value,request):
        self.context.need(self.result_state(value) is not None,'continuation checkpoint needs original result scope')
        key=request.get('step');self.context.need(key in value['steps'],'unknown checkpoint step')
        current=value['steps'][key];mapping=request.get('origin');self.origin(mapping)
        prior=current.get('continuation');active=value.get('execution')
        if prior:self.context.need(prior['origin']==mapping,'continuation must retain original operation/attempt/native turn')
        if active and active['step']==key:
            self.context.need(active['id']==mapping['attempt_id'] and active.get('session_id')==mapping['session_id'],'checkpoint is not the original native execution')
            known=next((e.get('native_turn_id') for e in reversed(current['evidence']) if e.get('execution_id')==active['id'] and e.get('native_turn_id')),None)
            self.context.need(known is None or known==mapping['turn_id'],'checkpoint substituted native turn')
        refs=request.get('effects');self.context.need(isinstance(refs,list) and len(refs)<=16,'invalid original durable effects')
        saved=copy.deepcopy(prior) if prior else {'origin':copy.deepcopy(mapping),'effects':[],'terminal':'unknown','terminal_ref':None,'resolved':False}
        for ref in refs:
            self.context.need(isinstance(ref,dict) and set(ref)=={'kind','path','sha256'} and ref.get('kind') in ('input','freeze','commit','delivery'),'invalid durable effect kind')
            old=next((e for e in saved['effects'] if e['kind']==ref['kind'] and e['path']==ref['path']),None)
            self.context.need(old is None or old==ref,'original input/freeze/effect cannot be overwritten')
            self.evidence_reference(value,{k:ref[k] for k in ('path','sha256')})
            if old is None:saved['effects'].append(copy.deepcopy(ref))
        self.context.need(len(saved['effects'])<=16,'durable effect reference bound')
        if prior:
            for ref in prior['effects']:self.evidence_reference(value,{k:ref[k] for k in ('path','sha256')})
        saved['resolved']=False;current['continuation']=saved
        value['accepted']=False
        if active and active['step']==key:
            current['unresolved_execution']=active;current['state']='reconcile'
            current['wait']={'kind':'original_continuation_unknown','next':'read_original_terminal_and_effects_without_replay'}
            value['execution']=None
        return saved


    def continue_original(self,value,request):
        key=request.get('step');self.context.need(key in value['steps'],'unknown continuation step')
        saved=value['steps'][key].get('continuation');self.context.need(saved is not None,'no original continuation checkpoint')
        for ref in saved['effects']:self.evidence_reference(value,{k:ref[k] for k in ('path','sha256')})
        ref=request.get('terminal_ref')
        if ref is not None:
            self.context.need(isinstance(ref,dict) and set(ref)=={'path','sha256'},'invalid original terminal reference')
            self.evidence_reference(value,ref)
            report=self.context.read_json(value['spec']['workspace'],ref['path'],ref['sha256'],value=value)
            self.context.need(isinstance(report,dict) and set(report)=={'origin','status','source_ref'},'invalid original terminal readback')
            self.origin(report['origin']);self.context.need(report['origin']==saved['origin'] and self.context.text(report['source_ref']),'terminal readback belongs to another original operation/attempt/turn')
            self.context.need(report['status'] in ('unknown','running','completed','not_executed','failed'),'invalid original terminal status')
            self.context.need(saved['terminal'] not in ('completed','not_executed','failed') or saved['terminal']==report['status'],'terminal observation cannot rewrite a known original outcome')
            if report['status']=='not_executed':self.context.need(not any(e['kind'] in ('commit','delivery') for e in saved['effects']),'NOT_EXECUTED conflicts with already durable effects')
            saved['terminal']=report['status'];saved['terminal_ref']=copy.deepcopy(ref)
        saved['next_action']={'completed':'consume_original_result','not_executed':'decide_original_authorized_step_with_remaining_budget','failed':'inspect_original_failure_without_replay'}.get(saved['terminal'],'inspect_original_native_and_durable_effects')
        saved['may_launch']=saved['may_commit']=saved['may_send']=False
        saved['source_authentication']='caller must verify permitted original native read; JSON labels are not authentication'
        return saved


    def result_receipts(self,state):
        if 'receipts' in state:
            return state['receipts']
        # Read old records without inventing a native source. A later mutation
        # persists the same original references; old input history is untouched.
        if state.get('result') is None:
            return []
        return [{'result':state['result'], 'native_sha256':None,
                 'reviews':[state['review']] if state.get('review') else []}]


    def review_content(self,value,state,result,reference,report):
        self.bound_report(value,report)
        self.context.need(report.get('object_ref')==state['object_ref'],'result belongs to another original business object')
        self.context.need(report.get('result_sha256')==reference['sha256'],'substantive review is not for current actual result')
        reviewer=report.get('reviewer_thread')
        self.context.need(self.context.text(reviewer) and reviewer not in (value['spec'].get('assignment',{}).get('owner'),
             value['spec'].get('request_identity',{}).get('thread_id')),'independent substantive reviewer missing')
        rows=report.get('assessments');self.context.need(isinstance(rows,list) and len(rows)<=64,'invalid substantive assessments')
        seen=set()
        for row in rows:
            self.context.need(isinstance(row,dict) and set(row)=={'id','verdict','reason'} and
                 isinstance(row.get('id'),str) and row['id'] in state['obligations'] and row['id'] not in seen and
                 row['verdict'] in ('PASS','FAIL','NOT_RUN','UNKNOWN') and self.context.text(row['reason']),'invalid substantive assessment')
            seen.add(row['id']);answer=next((a for a in result['answers'] if a['id']==row['id']),None)
            if row['verdict']=='PASS':
                self.context.need(answer is not None and answer['status']=='answered' and answer['method_fit']=='applicable',
                     'partial/unknown/inapplicable result cannot satisfy original obligation')
        return {row['id']:row['verdict'] for row in rows}


    def native_identity(self,report):
        native=report.get('native_result')
        self.context.need(isinstance(native,dict) and set(native)=={'thread_id','turn_id','final_ref','final_sha256'} and
             all(self.context.text(native.get(k)) for k in ('thread_id','turn_id','final_ref')) and self.context.hashes(native.get('final_sha256')),
             'actual current native result identity missing')
        # Thread/turn identify the returned execution. Its source reference and
        # content fingerprint cannot manufacture another native execution.
        return {key:native[key] for key in ('thread_id','turn_id')}


    def retained_native_identity(self,value,receipt):
        if receipt.get('native_identity') is not None:
            return receipt['native_identity']
        # Old records did not retain the stable identity. Derive it only from
        # the original bounded report; an unavailable old read stays unverified.
        try:
            ref=receipt['result']
            report=self.context.read_json(value['spec']['workspace'],ref['path'],ref['sha256'],value=value)
            self.bound_report(value,report)
            return self.native_identity(report)
        except (self.context.error_type,OSError,ValueError):
            return None


    def pending_result_actions(self,value,pending):
        state=self.result_state(value);ready=[];followup=[];errors=[]
        for receipt in self.result_receipts(state):
            ref=receipt['result']
            if receipt['reviews'] and not pending:continue
            try:
                report=self.context.read_json(value['spec']['workspace'],ref['path'],ref['sha256'],value=value)
                self.bound_report(value,report);self._result_content(value,state,report)
                if not receipt['reviews']:
                    ready.append(copy.deepcopy(ref))
                else:
                    unresolved=[row['id'] for row in report['answers'] if row['id'] in pending]
                    if unresolved:followup.append({'kind':'continue_original_result','result':copy.deepcopy(ref),'obligations':unresolved})
            except (self.context.error_type,OSError,ValueError) as exc:
                errors.append({'result_sha256':ref['sha256'],'detail':str(exc)})
        return ready,followup,errors


    def receipt_coverage(self,value):
        state=self.result_state(value);assessed={};errors=[];identities={}
        for receipt in self.result_receipts(state):
            if not receipt['reviews']:
                continue
            try:
                actual=receipt['result'];self.evidence_reference(value,actual)
                result=self.context.read_json(value['spec']['workspace'],actual['path'],actual['sha256'],value=value)
                self.bound_report(value,result);self._result_content(value,state,result)
                identity=self.native_identity(result);key=tuple(identity.values())
                self.context.need(receipt.get('native_identity') in (None,identity),'retained native identity differs from original report')
                self.context.need(key not in identities or identities[key]==actual['sha256'],
                     'same original native return has conflicting result bytes')
                identities[key]=actual['sha256']
                current={}
                for ref in receipt['reviews']:
                    self.evidence_reference(value,ref)
                    report=self.context.read_json(value['spec']['workspace'],ref['path'],ref['sha256'],value=value)
                    current.update(self.review_content(value,state,result,actual,report))
                for key,verdict in current.items():assessed.setdefault(key,set()).add(verdict)
            except (self.context.error_type,OSError,ValueError) as exc:
                # One held/stale original cannot stop intake of an independent
                # return. It contributes no current coverage until proved again.
                errors.append({'result_sha256':receipt['result']['sha256'],'detail':str(exc)})
        return [key for key in state['obligations'] if 'PASS' not in assessed.get(key,set()) or assessed[key]&{'FAIL','UNKNOWN'}],errors


    def collect_result(self,value,reference,review=False):
        state=self.result_state(value);self.context.need(state is not None,'original result scope missing')
        self.evidence_reference(value,reference)
        report=self.context.read_json(value['spec']['workspace'],reference['path'],reference['sha256'],value=value);self.bound_report(value,report)
        self.context.need(report.get('object_ref')==state['object_ref'],'result belongs to another original business object')
        receipts=copy.deepcopy(self.result_receipts(state))
        changed='receipts' not in state
        if review:
            receipt=next((r for r in receipts if r['result']['sha256']==report.get('result_sha256')),None)
            self.context.need(receipt is not None,'current actual result missing')
            actual=receipt['result'];self.evidence_reference(value,actual)
            result=self.context.read_json(value['spec']['workspace'],actual['path'],actual['sha256'],value=value)
            self.bound_report(value,result);self._result_content(value,state,result)
            self.review_content(value,state,result,actual,report)
            if not any(r['sha256']==reference['sha256'] for r in receipt['reviews']):
                receipt['reviews'].append(copy.deepcopy(reference));changed=True
            if changed:
                state['review']=copy.deepcopy(reference)
            for step in value['steps'].values():
                cp=step.get('continuation')
                if cp and cp['terminal']=='completed' and cp['origin'] in result['origins'] and not cp['resolved']:
                    cp['resolved']=True;changed=True
        else:
            self._result_content(value,state,report)
            native_sha=self.context.digest(self.context.encoded(report['native_result']));identity=self.native_identity(report)
            receipt=next((r for r in receipts if r['result']['sha256']==reference['sha256']),None)
            self.context.need(not any(r['result']['sha256']!=reference['sha256'] and
                         self.retained_native_identity(value,r)==identity for r in receipts),
                 'same original native return has conflicting result bytes')
            if receipt is None:
                receipts.append({'result':copy.deepcopy(reference),'native_sha256':native_sha,'native_identity':identity,'reviews':[]});changed=True
                state['result']=copy.deepcopy(reference);state['review']=None
            elif receipt['native_sha256'] is None:
                receipt['native_sha256']=native_sha;receipt['native_identity']=identity;changed=True
        self.context.need(len(receipts)<=64 and sum(len(r['reviews']) for r in receipts)<=64,'current result receipt bound')
        if not changed:
            return False
        state['receipts']=receipts
        state['pending']=self.receipt_coverage(value)[0]
        value['accepted']=False
        return True


    def _result_content(self,value,state,report):
        self.context.need(report.get('object_ref')==state['object_ref'],'original object mismatch')
        self.evidence_reference(value,report.get('answer'))
        self.native_identity(report)
        rows=report.get('answers');self.context.need(isinstance(rows,list) and len(rows)<=64,'invalid current result answers')
        seen=set();checked=set()
        for row in rows:
            self.context.need(isinstance(row,dict) and set(row)=={'id','status','method_fit','evidence'} and isinstance(row.get('id'),str) and row['id'] in state['obligations'] and row['id'] not in seen,'unrequested/duplicate original obligation')
            seen.add(row['id']);self.context.need(row['status'] in ('answered','partial','unanswered','unknown') and row['method_fit'] in ('applicable','limited','not_applicable','unknown'),'invalid actual result dimensions')
            refs=row['evidence'];self.context.need(isinstance(refs,list) and len(refs)<=16 and (row['status']!='answered' or refs),'actual answered result needs execution evidence')
            for ref in refs:
                self.context.need(isinstance(ref,dict) and set(ref)=={'path','sha256'} and isinstance(ref.get('path'),str) and self.context.hashes(ref.get('sha256')),'invalid execution evidence')
                identity=(ref['path'],ref['sha256'])
                if identity not in checked:
                    self.context.need(len(checked)<64,'distinct execution evidence bound');self.evidence_reference(value,ref);checked.add(identity)
        origins=report.get('origins');self.context.need(isinstance(origins,list) and len(origins)<=32,'invalid original result operation mappings')
        for mapping in origins:self.origin(mapping)
        for step in value['steps'].values():
            cp=step.get('continuation')
            if cp and cp['origin'] in origins:
                self.context.need(cp['terminal']=='completed','UNKNOWN/running/failed original outcome cannot be consumed as complete')
                for ref in cp['effects']:self.evidence_reference(value,{k:ref[k] for k in ('path','sha256')})


    def result_observation(self,value):
        """Project and validate the same bounded coverage observation."""
        state=self.result_state(value)
        if state is None:return None,None
        pending,errors=self.receipt_coverage(value)
        acceptance_error=self._acceptance_error(value,pending,errors)
        reviewable,followup,unavailable=self.pending_result_actions(value,pending);errors+=unavailable
        unreviewed=[r['result'] for r in self.result_receipts(state) if not r['reviews']]
        original_unknown=any(s.get('continuation') and not s['continuation']['resolved'] for s in value['steps'].values())
        return {'object_ref':state['object_ref'],'original_goal':state['original_goal'],'pending_obligations':pending,
            'program_checks_passed':all(s['state']=='verified' for s in value['steps'].values()),
            'model_turn_terminal':[{'origin':s['continuation']['origin'],'status':s['continuation']['terminal']} for s in value['steps'].values() if s.get('continuation')],
            'current_result':state.get('result'),'substantive_review':state.get('review'),
            'result_receipts':copy.deepcopy(self.result_receipts(state)),'unreviewed_results':unreviewed,
            'reviewable_results':reviewable,'continuable_results':followup,
            'receipt_errors':errors,
            'business_outcome':'pending' if pending or original_unknown or unreviewed or errors else 'reviewed_declared_scope',
            'native_authenticity':'not established by file labels; original caller must verify actual native records',
            'wake_source':'not provided by this task runtime; each continuation requires an actual host execution opportunity',
            'original_operations':[s['continuation'] for s in value['steps'].values() if s.get('continuation')]},acceptance_error


    def result_projection(self,value):
        return self.result_observation(value)[0]


    def _acceptance_error(self,value,pending,errors):
        state=self.result_state(value)
        if not (not pending and state.get('result') and self.result_receipts(state) and
                all(r['reviews'] for r in self.result_receipts(state))):
            return 'original user result is still pending'
        if errors:return 'current original result evidence is stale or unavailable'
        return None


    def validate_current_result(self,value):
        if self.result_state(value) is None:return
        error=self._acceptance_error(value,*self.receipt_coverage(value))
        self.context.need(error is None,error)


    def current_accepted(self,value):
        if not value['accepted']:return False
        try:self.validate_current_result(value)
        except (self.context.error_type,OSError,ValueError):return False
        return True


    def validate_result_state(self,value):
        state=self.result_state(value)
        self.context.need(('result_scope' in value['spec'])==(state is not None),'original result state/scope mismatch')
        if state is None:return
        self.context.need(isinstance(state,dict) and {'object_ref','original_goal','obligations','pending','result','review'}<=set(state)<= {'object_ref','original_goal','obligations','pending','result','review','receipts'},'invalid original result state')
        self.context.need(state['object_ref']==value['spec']['result_scope']['object_ref'] and self.context.text(state['original_goal']),'original goal/object changed')
        obligations=state['obligations'];pending=state['pending']
        self.context.need(isinstance(obligations,dict) and 0<len(obligations)<=64 and all(self.context.identifier(k) and self.context.text(v) for k,v in obligations.items()),'invalid retained original obligations')
        self.context.need(isinstance(pending,list) and all(isinstance(k,str) and k in obligations for k in pending) and len(set(pending))==len(pending),'invalid pending original obligations')
        for key in ('result','review'):
            ref=state[key]
            if ref is not None:
                self.context.need(isinstance(ref,dict) and set(ref)=={'path','sha256'} and self.context.hashes(ref.get('sha256')),'invalid retained result reference')
                self.context.relative(ref['path'],'retained result path')


        receipts=state.get('receipts',[])
        self.context.need(isinstance(receipts,list) and len(receipts)<=64,'invalid current result receipts')
        seen=set();reviews=0
        for receipt in receipts:
            self.context.need(isinstance(receipt,dict) and {'result','native_sha256','reviews'}<=set(receipt)<= {'result','native_sha256','reviews','native_identity'} and
                 (receipt['native_sha256'] is None or self.context.hashes(receipt['native_sha256'])),'invalid retained result receipt')
            identity=receipt.get('native_identity')
            self.context.need(identity is None or (isinstance(identity,dict) and set(identity)=={'thread_id','turn_id'} and
                 all(self.context.text(identity[k]) for k in identity)),'invalid retained native identity')
            self.context.need(isinstance(receipt['result'],dict) and set(receipt['result'])=={'path','sha256'} and self.context.hashes(receipt['result'].get('sha256')),'invalid result receipt reference')
            self.context.relative(receipt['result'].get('path'),'retained result path')
            self.context.need(receipt['result']['sha256'] not in seen,'duplicate current result receipt');seen.add(receipt['result']['sha256'])
            self.context.need(isinstance(receipt['reviews'],list),'invalid retained review references');reviews+=len(receipt['reviews'])
            for ref in receipt['reviews']:
                self.context.need(isinstance(ref,dict) and set(ref)=={'path','sha256'} and self.context.hashes(ref.get('sha256')),'invalid retained review reference')
                self.context.relative(ref.get('path'),'retained review path')
        self.context.need(reviews<=64,'current substantive review bound')
