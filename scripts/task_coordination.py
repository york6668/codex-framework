"""Project context and scoped relationships for the existing task runtime.

These declarations identify work, not permissions. The effect index contains
only live/uncertain reservations and retained refusals, never a second todo list.
"""
from contextlib import contextmanager
from contextvars import ContextVar
import copy
import fcntl
import functools
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import time

ROOT=ContextVar('task_coordination_root',default=None)
LOCKED=ContextVar('task_effect_index_locked',default=False)
WORKSET=ContextVar('task_coordination_workset',default=())
ROLES={'implementation','investigation','verification','integration','final_regression','coordination'}
MODES={'read','write','exclusive'}

def task():
    import task_runtime
    return task_runtime

def need(ok,reason):task().require(ok,reason)

def identifier(value):return isinstance(value,str) and bool(task().ID.fullmatch(value))

def hashes(value):return isinstance(value,str) and bool(task().SHA.fullmatch(value))

def text(value):return task().text(value)

@contextmanager
def context(root,workset=None):
    token=ROOT.set(Path(root));other=WORKSET.set(tuple(workset)) if workset is not None else None
    try:yield
    finally:
        if other is not None:WORKSET.reset(other)
        ROOT.reset(token)

def scoped(function):
    @functools.wraps(function)
    def call(store,*args,**kwargs):
        with context(store.root):return function(store,*args,**kwargs)
    return call

def relative(value,label):
    need(isinstance(value,str) and value and not Path(value).is_absolute() and '..' not in Path(value).parts,'invalid '+label)

def basis(value):
    need(isinstance(value,dict) and set(value)<={'status','source_ref','reason','source_sha256'},'invalid dependency basis')
    need(value.get('status') in ('proven','hypothesis','unknown'),'invalid dependency basis status')
    if value['status']=='proven':need(text(value.get('source_ref')) and text(value.get('reason')),'proven dependency needs its source and consumer reason')
    if 'source_sha256' in value:need(hashes(value['source_sha256']),'invalid dependency source fingerprint')

def local_dependencies(step):
    edges=step.get('depends_on',[]);declared=step.get('depends_on_basis')
    if declared is None:return []
    need(isinstance(declared,dict),'invalid depends_on_basis')
    if 'status' in declared:
        need(len(edges)<=1,'single dependency basis requires at most one edge');basis(declared)
        return edges if declared['status']=='proven' else []
    need(set(declared)<=set(edges),'dependency basis names unknown edge')
    for value in declared.values():basis(value)
    return [edge for edge in edges if edge in declared and declared[edge]['status']=='proven']

def local_endpoint(value,step):
    spec=value['spec']
    return {'task_id':value['task_id'],'request_id':spec['request_id'],'acceptance_version':spec['acceptance_version'],
            'workspace':spec['workspace'],'step':{k:step.get(k,[]) if k=='resources' else step[k]
                for k in ('id','instruction','checks','resources')}}

def local_dependency_gaps(value,step,trail=(),memo=None):
    """Unproved edges require inquiry, never implicit waiting or dispatch."""
    memo={} if memo is None else memo
    key=step['id']
    need(key not in trail and len(trail)<32,'invalid local dependency graph/depth')
    if key in memo:return memo[key]
    declared=step.get('depends_on_basis') or {};steps={s['id']:s for s in value['spec']['steps']};gaps=[]
    for edge in step.get('depends_on',[]):
        fact=declared if 'status' in declared else declared.get(edge)
        base={'step_id':step['id'],'depends_on':edge,'source_ref':fact.get('source_ref') if fact else None}
        if not fact or fact['status']!='proven' or not hashes(fact.get('source_sha256')):
            gaps.append(dict(base,kind='local_dependency_evidence_required'));continue
        try:
            source=read_json(value['spec']['workspace'],fact['source_ref'],fact['source_sha256'],value=value,unique=True)
            producer=steps[edge]
            need(task().encoded(source.get('producer'))==task().encoded(local_endpoint(value,producer)) and task().encoded(source.get('consumer'))==task().encoded(local_endpoint(value,step)),
                 'dependency source does not bind actual producer/consumer work')
            requirement=source.get('requires')
            need(isinstance(requirement,dict) and set(source)=={'producer','consumer','requires'}
                 and set(requirement)=={'kind','reason'} and requirement.get('reason')==fact['reason'],
                 'dependency source has no bound necessary result/effect')
            if requirement.get('kind')=='shared_effect':
                conflict=task().isolated_io('local_dependency_effect',lambda:any(
                    incompatible(a,b,False) for a in effect_projection(value,resources(value,producer))
                    for b in effect_projection(value,resources(value,step))),2)[0]
                need(conflict,
                     'compatible or different actual resources are not a shared effect')
            else:need(requirement.get('kind')=='required_result','unsupported necessary dependency relation')
        except (task().TaskError,OSError,ValueError) as exc:
            gaps.append(dict(base,kind='local_dependency_evidence_required',detail=str(exc)));continue
        upstream=local_dependency_gaps(value,producer,trail+(key,),memo)
        if upstream:
            # Keep a bounded pointer to the original producer conditions. Do
            # not recursively duplicate a shared DAG in every output record.
            unresolved=any(g['kind']=='local_dependency_evidence_required' for g in upstream)
            gaps.append(dict(base,kind='local_dependency_evidence_required' if unresolved else 'upstream_local_result_unresolved',
                             upstream_step_id=edge,upstream_kinds=sorted({g['kind'] for g in upstream})))
            continue
        parent=value['steps'][edge];execution=value.get('execution')
        uncertain=(parent.get('unresolved_execution') or
                   (parent.get('continuation') and not parent['continuation']['resolved']) or
                   (execution and execution['step']==edge))
        if parent['state']!='verified' or uncertain:
            gaps.append(dict(base,kind='required_local_result_not_verified'))
        else:
            checked=parent.get('validation') or {}
            if (not checked.get('passed') or checked.get('acceptance_sha256')!=value['acceptance_sha256']
                    or task().assessment(value,edge,checked.get('scope_after'),related=False)['blocked']):
                gaps.append(dict(base,kind='required_local_result_stale'))
    memo[key]=gaps
    return gaps

def validate(spec):
    if 'result_scope' in spec:validate_result_scope(spec['result_scope'])
    p=spec.get('project');assignment=spec.get('assignment')
    need(p is None or assignment is not None,'project context needs an assignment')
    need(spec.get('execution_mode','native') in ('native','external'),'invalid task execution mode')
    if p is not None:
        need(isinstance(p,dict) and set(p)=={'id','goal','responsibility','source_ref','obligations'},'invalid project context')
        need(identifier(p['id']) and all(text(p[k]) for k in ('goal','responsibility','source_ref')),'project goal/responsibility/source missing')
        need(isinstance(p['obligations'],list) and 0<len(p['obligations'])<=32,'invalid project obligations')
        seen=set()
        for item in p['obligations']:
            need(isinstance(item,dict) and set(item)=={'id','acceptance','task_ids'},'invalid project obligation')
            need(identifier(item['id']) and item['id'] not in seen and text(item['acceptance']),'invalid obligation identity/acceptance')
            ids=item['task_ids'];need(isinstance(ids,list) and 0<len(ids)<=32 and all(identifier(x) for x in ids) and len(set(ids))==len(ids),'invalid obligation task membership');seen.add(item['id'])
        need(isinstance(assignment,dict) and set(assignment)=={'owner','role','delivery_owner','covers'},'invalid task assignment')
        need(text(assignment['owner']) and text(assignment['delivery_owner']) and isinstance(assignment['role'],str) and assignment['role'] in ROLES,'invalid assignment responsibility')
        need(isinstance(assignment['covers'],list) and assignment['covers'] and all(identifier(x) and x in seen for x in assignment['covers']) and len(set(assignment['covers']))==len(assignment['covers']),'assignment must cover declared obligations')
    if p is None and assignment is not None:
        need(isinstance(assignment,dict) and set(assignment)=={'owner','role','delivery_owner','covers'},'invalid cross-project assignment')
        need(text(assignment['owner']) and text(assignment['delivery_owner']) and isinstance(assignment['role'],str) and assignment['role'] in ROLES and assignment['covers']==[],'invalid cross-project responsibility')
    if spec.get('execution_mode')=='external':
        need(all(c['kind']=='file' for step in spec['steps'] for c in step['checks']),'external effect collection only supports passive file checks; run authorized verification separately in its original task')
    identity=spec.get('request_identity')
    if identity is not None:
        need(isinstance(identity,dict) and {'thread_id','turn_id','message_id'}<=set(identity)<= {'thread_id','turn_id','message_id','automation_id'} and all(text(v) for v in identity.values()),'invalid current native request identity')
    peers=spec.get('known_tasks',[])
    need(isinstance(peers,list) and len(peers)<=32 and all(identifier(v) for v in peers) and len(set(peers))==len(peers),'invalid known task references')
    requires=spec.get('requires',[])
    need(isinstance(requires,list) and len(requires)<=32,'invalid cross-task dependencies')
    for item in requires:
        need(isinstance(item,dict) and set(item)=={'task_id','step_id','input_sha256','basis'},'invalid cross-task dependency')
        need(identifier(item['task_id']) and (item['step_id'] is None or identifier(item['step_id'])) and hashes(item['input_sha256']),'invalid dependency identity/version');basis(item['basis'])
    for step in spec['steps']:
        local_dependencies(step)
        resources=step.get('resources',[])
        need(isinstance(resources,list) and len(resources)<=32,'invalid declared resources')
        for resource in resources:
            need(isinstance(resource,dict) and set(resource)=={'kind','identity','object','mode','operation','source_ref'},'invalid resource descriptor')
            need(resource['kind'] in ('file','logical') and isinstance(resource['mode'],str) and resource['mode'] in MODES and identifier(resource['operation']) and text(resource['source_ref']),'invalid resource effect/source')
            need(text(resource['identity']) and isinstance(resource['object'],str) and len(resource['object'])<=1024,'invalid resource identity/object')
            if resource['kind']=='file':relative(resource['identity'],'resource file path');need(resource['object']=='','file object must use its path identity')
            else:need(not resource['object'].startswith('/') and '..' not in resource['object'].split('/'),'invalid logical object path')
    artifact=spec.get('artifact')
    if artifact is not None:
        need(isinstance(artifact,dict) and set(artifact)=={'manifest','sha256','versions'},'invalid delivery artifact')
        relative(artifact['manifest'],'loaded manifest path');need(hashes(artifact['sha256']),'invalid loaded manifest fingerprint')
        need(isinstance(artifact['versions'],list) and 0<len(artifact['versions'])<=16,'invalid release versions')
        ids=set()
        for v in artifact['versions']:
            need(isinstance(v,dict) and set(v)=={'project_id','repo','ref','commit','tree'},'invalid release version')
            need(identifier(v['project_id']) and v['project_id'] not in ids,'duplicate release project');ids.add(v['project_id']);relative(v['repo'],'release repository')
            need(isinstance(v['ref'],str) and v['ref'].startswith('refs/heads/') and not any(c in v['ref'] for c in '\n\r\x00 '),'invalid release target ref')
            need(all(isinstance(v[k],str) and re.fullmatch('[0-9a-f]{40}',v[k]) for k in ('commit','tree')),'invalid immutable release identity')

def membership(value):
    p=value['spec'].get('project')
    if p:
        selected=[o for o in p['obligations'] if o['id'] in value['spec']['assignment']['covers']]
        need(all(value['task_id'] in o['task_ids'] for o in selected),'task assignment does not belong to declared project obligation')

def resources(value,step,scope=None):
    spec=value['spec'];declared=step.get('resources')
    if declared is None:
        declared=[]
    declared=copy.deepcopy(declared)
    explicit_files={r['identity'] for r in declared if r['kind']=='file'}
    declared += [{'kind':'file','identity':c['path'],'object':'','mode':'write','operation':'write','source_ref':'registered file-output acceptance target'} for c in step['checks'] if c['kind']=='file' and c['path'] not in explicit_files]
    result=[]
    for original in declared:
        item=copy.deepcopy(original)
        if item['kind']=='file':
            # Filesystem identity comes from the bounded captured scope below.
            # Do not resolve/stat the target again outside that IO budget.
            item['identity']=os.path.abspath(Path(spec['workspace'])/item['identity'])
            original=next((t for t in (scope or {}).get('targets',[]) if t['path']==item['identity'] and t.get('exists') and t.get('inode',0)>0),None)
            if original:item['object_identity']={'device':original['device'],'inode':original['inode']}
        result.append(item)
    return result

def overlaps(a,b):
    if a['kind']!=b['kind']:return False
    if a['kind']=='file':
        if a['identity']==b['identity']:return True
        identities=[]
        for item in (a,b):
            retained=item.get('object_identity')
            if retained:identities.append((retained['device'],retained['inode']));continue
            try:
                info=os.stat(item['identity'],follow_symlinks=False)
                identities.append((info.st_dev,info.st_ino) if info.st_ino>0 else None)
            except OSError:identities.append(None)
        if identities[0] is not None and identities[0]==identities[1]:return True
        try:
            x=os.stat(a['identity'],follow_symlinks=False);y=os.stat(b['identity'],follow_symlinks=False)
            return x.st_ino>0 and (x.st_dev,x.st_ino)==(y.st_dev,y.st_ino)
        except OSError:return False
    if a['identity']!=b['identity']:return False
    if a['object']==b['object']:return True
    return ((a['mode']=='exclusive' and (not a['object'] or b['object'].startswith(a['object']+'/'))) or
            (b['mode']=='exclusive' and (not b['object'] or a['object'].startswith(b['object']+'/'))))

def incompatible(a,b,denial=False):
    if not overlaps(a,b):return False
    if denial:
        # Operation names are caller labels, not permission distinctions.
        # Retain the observed read/change category on the actual object.
        return a['operation']=='legacy_unknown' or (a['mode']=='read')==(b['mode']=='read')
    return a['mode']!='read' or b['mode']!='read'

def index_path(root):return task().cf.safe_path(root,'.effects.json')

def read_index(root):
    path=index_path(root)
    try:fd=os.open(path,os.O_RDONLY|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0))
    except FileNotFoundError:return []
    try:
        info=os.fstat(fd);need(stat.S_ISREG(info.st_mode) and info.st_size<=1048576,'effect reservation index must be bounded regular data')
        raw=b''
        while len(raw)<=1048576:
            chunk=os.read(fd,min(65536,1048577-len(raw)))
            if not chunk:break
            raw+=chunk
        need(len(raw)<=1048576,'effect reservation index size bound')
    finally:os.close(fd)
    value=json.loads(raw);need(isinstance(value,dict) and set(value)=={'version','entries'} and value['version']==1 and isinstance(value['entries'],list) and len(value['entries'])<=256,'invalid effect reservation index')
    for e in value['entries']:
        need(isinstance(e,dict) and set(e)=={'task_id','execution_id','state','resources','source_ref'} and identifier(e['task_id']) and isinstance(e['resources'],list) and e['state'] in ('active','uncertain','denial'),'invalid effect reservation entry')
        for item in e['resources']:
            need(isinstance(item,dict) and {'kind','identity','object','mode','operation','source_ref'}<=set(item)<={'kind','identity','object','mode','operation','source_ref','object_identity'},'invalid stored resource effect')
            need(item['kind'] in ('file','logical') and isinstance(item['mode'],str) and item['mode'] in MODES and text(item['identity']) and isinstance(item['object'],str) and identifier(item['operation']),'invalid stored effect identity/mode')
            if 'object_identity' in item:
                original=item['object_identity'];need(item['kind']=='file' and isinstance(original,dict) and set(original)=={'device','inode'} and all(type(v) is int and v>=0 for v in original.values()) and original['inode']>0,'invalid retained file object identity')
    return value['entries']

@contextmanager
def index_lock(root):
    root=Path(root);root.mkdir(parents=True,exist_ok=True)
    fd=os.open(task().cf.safe_path(root,'.effects.lock'),os.O_CREAT|os.O_RDWR|getattr(os,'O_NOFOLLOW',0),0o600)
    token=None
    try:
        need(stat.S_ISREG(os.fstat(fd).st_mode),'effect index lock is not a regular file')
        until=time.monotonic()+2
        while True:
            try:fcntl.flock(fd,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                need(time.monotonic()<until,'effect reservation update is busy; keep its original owner')
                time.sleep(.01)
        token=LOCKED.set(True);yield
    finally:
        if token is not None:LOCKED.reset(token)
        os.close(fd)

def own_entries(value):
    result=[];specs={s['id']:s for s in value['spec']['steps']};execution=value.get('execution')
    if execution:
        effects=resources(value,specs[execution['step']],execution.get('dispatch_scope'))
        if execution.get('producer_kind')=='external_observation':
            effects=[dict(e,mode='read',operation='read') for e in effects]
        result.append({'task_id':value['task_id'],'execution_id':execution['id'],'state':'active','resources':effects,'source_ref':'current durable execution'})
    for name,state in value['steps'].items():
        unresolved=state.get('unresolved_execution')
        if state['state'] in ('running','blocked','reconcile') and unresolved and not(execution and unresolved['id']==execution['id']):
            result.append({'task_id':value['task_id'],'execution_id':unresolved['id'],'state':'uncertain','resources':resources(value,specs[name],unresolved.get('dispatch_scope')),'source_ref':(state.get('wait') or {}).get('source_ref','unresolved original effect')})
    for hold in value.get('restrictions',[]):
        effects=hold.get('resource_effects')
        if effects is None:
            effects=[]
            for path in hold['files']:
                item={'kind':'file','identity':path,'object':'','mode':'write','operation':'legacy_unknown','source_ref':'retained original file restriction'}
                original=next((t for t in hold.get('targets',[]) if t['path']==path and t.get('exists') and t.get('inode',0)>0),None)
                if original:item['object_identity']={'device':original['device'],'inode':original['inode']}
                effects.append(item)
        result.append({'task_id':value['task_id'],'execution_id':task().digest(task().encoded(hold)),'state':'denial','resources':effects,'source_ref':hold['wait'].get('source_ref') or hold['wait'].get('evidence_ref') or 'retained original restriction'})
    for name,state in value['steps'].items():
        cp=state.get('continuation')
        if cp and not cp['resolved']:
            result.append({'task_id':value['task_id'],'execution_id':cp['origin']['attempt_id'],'state':'uncertain','resources':resources(value,specs[name]),'source_ref':'retained original continuation'})
    return [x for x in result if x['resources']]

def sync_index(store,value):
    own=own_entries(value)
    if not own and not index_path(store.root).exists():return
    def write():
        entries=[e for e in read_index(store.root) if e['task_id']!=value['task_id']]+own
        need(len(entries)<=256,'effect reservation capacity reached; resolve originals, do not evict')
        task().cf.write_file(index_path(store.root),task().encoded({'version':1,'entries':entries}))
    if LOCKED.get():write()
    else:
        with index_lock(store.root):write()

def effect_projection(value,effects):
    """Project the proposed controller entrance, not a hypothetical producer.

    External collection only reads acquired files, including when its output
    checks would conservatively reserve writes for a native producer.
    """
    return [dict(e,mode='read',operation='read') for e in effects] if value['spec'].get('execution_mode')=='external' else effects

def effect_conflicts(value,step,wanted=None):
    root=ROOT.get()
    if root is None:return []
    wanted=resources(value,step) if wanted is None else wanted
    wanted=effect_projection(value,wanted)
    if not wanted:return []
    def inspect():
        matches=[]
        entries=read_index(root)
        known=set(value['spec'].get('known_tasks',[]))|set(WORKSET.get())
        for identity in sorted(known-{value['task_id']}):
            try:entries+=own_entries(peer(root,identity))
            except (task().TaskError,OSError,ValueError):continue
        for entry in entries:
            if entry['task_id']==value['task_id']:continue
            if any(incompatible(old,new,entry['state']=='denial') for old in entry['resources'] for new in wanted):
                matches.append({'kind':'retained_refusal' if entry['state']=='denial' else 'occupied_effect','task_id':entry['task_id'],'execution_id':entry['execution_id'],'source_ref':entry['source_ref'],'state':entry['state']})
        return matches
    try:return task().isolated_io('effect_conflict_metadata',inspect,2)[0]
    except (task().TaskError,OSError) as exc:return [{'kind':'effect_identity_unknown','detail':str(exc),'source_ref':'effect reservation metadata'}]

def peer(root,identity):
    return task().isolated_io('related_task_metadata',lambda:task().Store(Path(root).parent,identity).read(),2)[0]

def dependency_gaps(value,trail=()):
    root=ROOT.get();gaps=[]
    if value['task_id'] in trail:return [{'kind':'dependency_cycle','task_id':value['task_id']}]
    need(len(trail)<=32,'dependency graph depth bound')
    for edge in value['spec'].get('requires',[]):
        if edge['basis']['status']!='proven':continue
        base={'task_id':edge['task_id'],'step_id':edge['step_id'],'source_ref':edge['basis']['source_ref'],'reason':edge['basis']['reason']}
        if root is None:gaps.append(dict(base,kind='related_task_context_missing'));continue
        try:other=peer(root,edge['task_id'])
        except (task().TaskError,OSError,ValueError) as exc:gaps.append(dict(base,kind='dependency_not_observed',detail=str(exc)));continue
        if other['acceptance_sha256']!=edge['input_sha256']:gaps.append(dict(base,kind='dependency_version_changed'));continue
        nested=dependency_gaps(other,trail+(value['task_id'],))
        if nested:gaps.append(dict(base,kind='upstream_dependency_unresolved',upstream=nested));continue
        names=[edge['step_id']] if edge['step_id'] else list(other['steps'])
        if any(n not in other['steps'] or other['steps'][n]['state']!='verified' for n in names) or (edge['step_id'] is None and not current_accepted(other)):
            gaps.append(dict(base,kind='required_result_not_accepted'));continue
        if other['spec'].get('artifact'):
            try:artifact(other)
            except (task().TaskError,OSError,ValueError) as exc:
                gaps.append(dict(base,kind='loaded_dependency_changed',detail=str(exc)));continue
        for name in names:
            state=other['steps'][name];checked=state.get('validation') or {}
            if checked.get('acceptance_sha256')!=other['acceptance_sha256'] or not checked.get('passed') or task().assessment(other,name,checked.get('scope_after'),related=False)['blocked']:
                gaps.append(dict(base,kind='required_result_stale'));break
    return gaps

def related(value,step,dependencies=True):
    return (local_dependency_gaps(value,step)+dependency_gaps(value) if dependencies else [])+effect_conflicts(value,step)

def prompt(value):
    spec=value['spec'];project=spec.get('project')
    if project is None and not spec.get('assignment') and not spec.get('request_identity') and not spec.get('result_scope'):return ''
    identity={'request_id':spec['request_id'],'task_id':value['task_id'],'input_revision':value['input_revision'],'acceptance_sha256':value['acceptance_sha256'],'native_request':spec.get('request_identity')}
    return '\nCurrent request identity: '+json.dumps(identity,ensure_ascii=False)+'\nProject goal/responsibility: '+json.dumps(project,ensure_ascii=False)+'\nThis assignment: '+json.dumps(spec.get('assignment'),ensure_ascii=False)+'\nDeclared input relationships: '+json.dumps(spec.get('requires',[]),ensure_ascii=False)+'\nOriginal result scope: '+json.dumps(spec.get('result_scope'),ensure_ascii=False)+'\nThese facts do not grant permissions. Answer this current request and assigned deliverables; an older PASS or another project result is not its acceptance.'

def bound_report(value,report):
    need(isinstance(report,dict),'report must be an object')
    expected={'request_id':value['spec']['request_id'],'task_id':value['task_id'],'input_revision':value['input_revision'],'task_input_sha256':value['acceptance_sha256']}
    if value['spec'].get('project'):expected['project_id']=value['spec']['project']['id']
    if value['spec'].get('request_identity'):expected['request_identity']=value['spec']['request_identity']
    need(all(type(report.get(k)) is type(v) and report[k]==v for k,v in expected.items()),'report does not answer current project/task/native request/input')
    return expected

def read_json(workspace,path,fingerprint,value=None,unique=False):
    relative(path,'evidence path');need(hashes(fingerprint),'invalid evidence fingerprint')
    def read():
        target=task().cf.safe_path(workspace,path)
        fd=os.open(target,os.O_RDONLY|getattr(os,'O_NONBLOCK',0)|getattr(os,'O_NOFOLLOW',0))
        try:
            info=os.fstat(fd);need(stat.S_ISREG(info.st_mode) and info.st_size<=task().LIMIT,'evidence must be a bounded ordinary file')
            if value is not None:check_passive_object(value,str(target),info.st_dev,info.st_ino)
            raw=b''
            while len(raw)<=task().LIMIT:
                chunk=os.read(fd,min(65536,task().LIMIT+1-len(raw)))
                if not chunk:break
                raw+=chunk
            need(len(raw)<=task().LIMIT and task().digest(raw)==fingerprint,'evidence bytes changed')
            if value is not None:
                end=os.fstat(fd);current=os.stat(target,follow_symlinks=False)
                identity=lambda s:(s.st_dev,s.st_ino,s.st_size,s.st_mtime_ns,s.st_ctime_ns)
                need(identity(info)==identity(end)==identity(current),'evidence changed during passive read')
                check_passive_object(value,str(target),end.st_dev,end.st_ino)
            def pairs(entries):
                result={}
                for key,child in entries:
                    need(key not in result,'duplicate dependency source field');result[key]=child
                return result
            parsed=json.loads(raw,object_pairs_hook=pairs if unique else None)
            need(isinstance(parsed,dict),'evidence must be an object');return parsed
        finally:os.close(fd)
    return task().isolated_io('project_evidence',read,3)[0]

def artifact(value):
    declared=value['spec'].get('artifact')
    if declared is None:return None
    workspace=value['spec']['workspace'];manifest=read_json(workspace,declared['manifest'],declared['sha256'])
    need(set(manifest)=={'versions','files'} and manifest['versions']==declared['versions'],'loaded artifact version set mismatch')
    files=manifest['files'];need(isinstance(files,list) and 0<len(files)<=64,'invalid loaded artifact closure')
    projects={v['project_id']:v for v in declared['versions']};seen=set();covered=set()
    for version in declared['versions']:
        repo=task().cf.safe_path(workspace,version['repo'])
        for argv,expected in [(['git','-C',str(repo),'show-ref','--verify','--hash',version['ref']],version['commit']),(['git','-C',str(repo),'rev-parse',version['commit']+'^{tree}'],version['tree'])]:
            observed=task().checked_command(workspace,{'kind':'command','argv':argv,'timeout':3,'max_output_bytes':4096})
            need(observed['passed'] and observed['stdout_sha256']==task().digest((expected+'\n').encode()),'merged source ref/tree does not match delivery')
    for item in files:
        need(isinstance(item,dict) and set(item)=={'project_id','source','loaded','sha256'} and item['project_id'] in projects and hashes(item['sha256']),'invalid loaded file mapping')
        relative(item['source'],'source artifact path');relative(item['loaded'],'loaded artifact path');need(item['loaded'] not in seen,'duplicate loaded destination');seen.add(item['loaded']);covered.add(item['project_id'])
        version=projects[item['project_id']];repo=task().cf.safe_path(workspace,version['repo'])
        blob=task().checked_command(workspace,{'kind':'command','argv':['git','-C',str(repo),'cat-file','blob',version['commit']+':'+item['source']],'timeout':3,'max_output_bytes':task().FILE_BYTES})
        need(blob['passed'] and blob['stdout_sha256']==item['sha256'],'merged source artifact mismatch')
        checked=task().checked_file(value,value['spec']['steps'][0]['id'],{'kind':'file','path':item['loaded'],'sha256':item['sha256'],'timeout':3})
        need(checked['passed'],'actual loaded file differs from merged source')
    need(covered==set(projects),'release part has no actual loaded file evidence')
    return {'sha256':declared['sha256'],'versions':declared['versions'],'loaded_files':len(files)}

# Delivery views are derived from existing native tasks and scoped evidence.
# They have no Store, scheduler, permission grant or persistent status of their own.
DELIVERY_STAGES = ('source', 'build', 'install', 'load', 'adoption', 'verification')
REUSE_AXES = ('requirements', 'code', 'input', 'environment', 'method')


def native_task_projection(binding, snapshots):
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
                found[task().encoded(turn)] = turn
    if not found:
        return dict(binding, native_status='unknown', native_observation='selected_turn_not_observed')
    if len(found) != 1:
        return dict(binding, native_status='unknown', native_observation='conflicting_selected_turn_snapshots')
    turn = next(iter(found.values()))
    status = turn.get('status')
    return dict(binding, native_status=status if isinstance(status, str) else 'unknown',
                native_observation='supplied_selected_turn', completion_scope='native turn only')


def delivery_view(request):
    """Read-only current delivery relationships; no task registration or effects.

    Original callers retain evidence/authorship and domain interpretation duties.
    A source reference and fingerprint identify an observation, not its truth.
    This operation checks the supplied current-baseline/version relationships.
    """
    baseline = request.get('baseline')
    need(isinstance(baseline, dict) and set(baseline) == {'id', 'source_ref'}
         and all(text(baseline[k]) for k in baseline), 'delivery view needs the current requirement baseline and source')
    definitions = request.get('deliveries')
    mainlines = request.get('mainlines')
    bindings = request.get('tasks', [])
    observations = request.get('observations', [])
    snapshots = request.get('native_sources', [])
    need(isinstance(definitions, list) and 0 < len(definitions) <= 64, 'delivery view needs1..64 existing deliverables')
    need(isinstance(mainlines, list) and 0 < len(mainlines) <= 32, 'delivery view needs1..32 mainlines')
    need(isinstance(bindings, list) and len(bindings) <= 128, 'invalid existing task bindings')
    need(isinstance(observations, list) and len(observations) <= 256, 'invalid delivery observations')
    need(isinstance(snapshots, list) and len(snapshots) <= 128, 'invalid native snapshots')
    tasks = {}
    for binding in bindings:
        need(isinstance(binding, dict) and set(binding) == {'id', 'thread_id', 'turn_id', 'source_ref'}
             and identifier(binding['id']) and all(text(binding[k]) for k in ('thread_id', 'turn_id', 'source_ref')),
             'invalid original task identity')
        need(binding['id'] not in tasks, 'duplicate original task binding')
        tasks[binding['id']] = native_task_projection(binding, snapshots)
    records = {}
    for observation in observations:
        need(isinstance(observation, dict) and text(observation.get('delivery_id')), 'observation needs a delivery identity')
        records.setdefault(observation['delivery_id'], []).append(observation)
    deliveries = {}
    for declared in definitions:
        need(isinstance(declared, dict) and identifier(declared.get('id')), 'invalid deliverable identity')
        identity = declared['id']
        need(identity not in deliveries, 'duplicate deliverable identity')
        row = {'goal': declared.get('goal'), 'source_ref': declared.get('source_ref'), 'task_ids': declared.get('task_ids'),
               'stages': {}, 'gaps': [], 'historical_observations': [], 'implementation_delivery': {'complete': False},
               'verification_delivery': {'complete': False}, 'complete': False}
        deliveries[identity] = row
        row['declared_contract'] = copy.deepcopy(declared)
        if isinstance(declared.get('stages'), dict):
            row['stages'] = {name: {'required': name in declared['stages'], 'complete': False,
                'status': 'pending' if name in declared['stages'] else 'not_required_by_this_contract',
                'target': copy.deepcopy(declared['stages'].get(name)), 'observations': [], 'gaps': []}
                for name in DELIVERY_STAGES}
        try:
            need(set(declared) == {'id', 'goal', 'source_ref', 'task_ids', 'stages'}
                 and text(declared['goal']) and text(declared['source_ref']), 'invalid deliverable contract')
            ids = declared['task_ids']
            need(isinstance(ids, list) and len(ids) <= 128 and all(identifier(i) for i in ids)
                 and len(set(ids)) == len(ids), 'invalid deliverable task membership')
            stages = declared['stages']
            need(isinstance(stages, dict) and stages and set(stages) <= set(DELIVERY_STAGES)
                 and any(s != 'verification' for s in stages), 'deliverable needs an explicit implementation stage')
            row['tasks'] = {i: tasks.get(i) for i in ids}
            if not ids or set(ids) - set(tasks):
                row['gaps'].append({'kind': 'original_task_binding_missing', 'task_ids': sorted(set(ids) - set(tasks))})
            eligible = []
            for record in records.get(identity, []):
                fields = {'delivery_id', 'stage', 'case', 'task_id', 'baseline', 'version', 'inputs', 'result', 'source_ref', 'sha256', 'report'}
                need(fields <= set(record) <= fields | {'reuse'} and record['stage'] in DELIVERY_STAGES
                     and all(text(record[k]) for k in ('case', 'task_id', 'baseline', 'version', 'source_ref'))
                     and hashes(record['sha256']) and isinstance(record['inputs'], dict)
                     and all(text(k) and text(v) for k, v in record['inputs'].items())
                     and record['result'] in ('PASS', 'FAIL', 'NOT_RUN', 'UNKNOWN'), 'invalid scoped delivery observation')
                need(record['task_id'] in ids, 'delivery observation belongs to another task')
                need(isinstance(record['report'], str) and task().digest(record['report'].encode()) == record['sha256'],
                     'original report bytes do not match the evidence fingerprint')
                def unique(pairs):
                    out = {}
                    for key, child in pairs:
                        need(key not in out, 'duplicate original report field')
                        out[key] = child
                    return out
                def constant(value):
                    raise ValueError('non-JSON original report value')
                reported = json.loads(record['report'], object_pairs_hook=unique, parse_constant=constant)
                need(isinstance(reported, dict) and all(type(reported.get(k)) is type(record[k]) and reported[k] == record[k]
                     for k in ('delivery_id', 'stage', 'case', 'task_id', 'baseline', 'version', 'inputs', 'result')),
                     'observation does not bind its original report content')
                current = record['baseline'] == baseline['id']
                if not current:
                    reuse = record.get('reuse')
                    current = (isinstance(reuse, dict) and set(reuse) == {'baseline', 'source_ref', *REUSE_AXES}
                               and reuse['baseline'] == baseline['id'] and text(reuse['source_ref'])
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
                need(isinstance(expected, dict) and {'version', 'inputs'} <= set(expected) <= {'version', 'inputs', 'checks', 'independent'}
                     and (expected['version'] is None or text(expected['version'])) and isinstance(expected['inputs'], dict)
                     and all(text(k) and text(v) for k, v in expected['inputs'].items()), 'invalid stage target/version links')
                checks = expected.get('checks', ['result'])
                need(isinstance(checks, list) and checks and len(checks) <= 64 and all(text(x) for x in checks)
                     and len(set(checks)) == len(checks) and type(expected.get('independent', False)) is bool,
                     'invalid current acceptance cases')
                for linked, version in expected['inputs'].items():
                    if linked in DELIVERY_STAGES:
                        need(linked != stage and linked in stages and isinstance(stages[linked], dict)
                             and stages[linked].get('version') == version, 'stage input refers to a different current version')
                prior = [name for name in DELIVERY_STAGES[:DELIVERY_STAGES.index(stage)] if name in stages and name != 'verification']
                if prior:
                    upstream = 'load' if stage == 'verification' and 'load' in prior else prior[-1]
                    need(upstream in expected['inputs'] and expected['inputs'][upstream] == stages[upstream]['version'],
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
            row['implementation_delivery'] = {'complete': all(s['complete'] for k, s in row['stages'].items()
                if s['required'] and k != 'verification'), 'scope': 'declared implementation stages only'}
            verify = row['stages']['verification']
            row['verification_delivery'] = {'required': verify['required'], 'complete': verify['complete'] if verify['required'] else None,
                                            'scope': 'current exact-version declared verification cases only'}
            row['complete'] = not row['gaps']
        except (task().TaskError, TypeError, ValueError, RecursionError) as exc:
            row['gaps'].append({'kind': 'invalid_delivery_contract_or_observation', 'detail': str(exc)})
    lines, linked, issues = {}, set(), []
    for line in mainlines:
        need(isinstance(line, dict) and set(line) == {'id', 'goal', 'source_ref', 'delivery_ids'}
             and identifier(line['id']) and text(line['goal']) and text(line['source_ref']), 'invalid mainline')
        need(line['id'] not in lines, 'duplicate mainline')
        ids = line['delivery_ids']
        need(isinstance(ids, list) and ids and len(ids) <= 64 and all(identifier(i) for i in ids)
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


def project_view(values,errors):
    projects={}
    for value in values.values():
        p=value['spec'].get('project')
        if not p:continue
        result=projects.setdefault(p['id'],{'goal':p['goal'],'responsibility':p['responsibility'],'obligations':{},'gaps':[],'complete':False})
        if (result['goal'],result['responsibility'])!=(p['goal'],p['responsibility']):result['gaps'].append({'kind':'project_context_disagrees','task_id':value['task_id']})
        for obligation in p['obligations']:
            old=result['obligations'].get(obligation['id'])
            if old and old['acceptance']!=obligation['acceptance']:result['gaps'].append({'kind':'obligation_criteria_disagree','obligation':obligation['id']})
            item=result['obligations'].setdefault(obligation['id'],{'acceptance':obligation['acceptance'],'task_ids':[]})
            item['task_ids']=sorted(set(item['task_ids'])|set(obligation['task_ids']))
    for identity,result in projects.items():
        for name,obligation in result['obligations'].items():
            for member in obligation['task_ids']:
                value=values.get(member)
                if value is None:result['gaps'].append({'kind':'required_task_missing','obligation':name,'task_id':member,'detail':errors.get(member)});continue
                p=value['spec'].get('project');assignment=value['spec'].get('assignment',{})
                if not p or p['id']!=identity or name not in assignment.get('covers',[]):result['gaps'].append({'kind':'wrong_task_assignment','task_id':member,'obligation':name})
                elif not current_accepted(value) or dependency_gaps(value) or any(task().assessment(value,n)['blocked'] for n in value['steps']):result['gaps'].append({'kind':'project_obligation_remaining','task_id':member,'obligation':name})
        result['complete']=not result['gaps']
        result['completion_scope']='registered finite task obligations only; native task end and delivery-stage completion are separate'
    return projects

def declared_link(value,identity,fingerprint):
    return any(e['task_id']==identity and e['step_id'] is None and e['input_sha256']==fingerprint and e['basis']['status']=='proven' for e in value['spec'].get('requires',[]))

def component_result_digest(value):
    """Identify acquired current results, separately from their input contract."""
    for step in value['spec']['steps']:
        for check in step['checks']:
            if check['kind']=='file':
                need(task().checked_file(value,None,check)['passed'],'component result file changed')
    return task().digest(task().encoded({'steps':{name:state.get('validation') for name,state in value['steps'].items()},
                                        'result_state':value.get('result_state')}))

def component_bindings(values,ids,integration,loaded):
    """Read original reviews and Git objects; labels alone cannot bind adoption.

    Reviewer provenance and whether its scope answers the user's full goal
    remain the original coordinator's responsibility. No source is executed.
    """
    workspace=integration['spec']['workspace'];declared=integration['spec']['artifact']
    manifest=read_json(workspace,declared['manifest'],declared['sha256'],value=integration)
    versions={v['project_id']:v for v in loaded['versions']};bindings={}
    for identity in ids:
        value=values[identity];review=value.get('review');need(isinstance(review,dict),'component has no independent content review')
        path=Path(review['path']);report=read_json(path.parent,path.name,review['sha256'],value=value);bound_report(value,report)
        need(report.get('verdict')=='PASS' and report.get('candidate_tree')==review['candidate_tree']
             and report.get('reviewer_thread')==review['reviewer_thread'],'component original review identity changed')
        project=value['spec'].get('project',{}).get('id');need(project in versions,'component has no adopted project version')
        version=versions[project];repo=task().cf.safe_path(workspace,version['repo'])
        mapped={f['source']:f for f in manifest['files'] if f['project_id']==project}
        scope=report.get('reviewed_files')
        if scope is None:
            need(review['candidate_tree']==version['tree'],'component reviewed tree differs from adopted tree without reviewed-file scope')
            scope=[{'path':name,'sha256':entry['sha256']} for name,entry in sorted(mapped.items())]
        need(isinstance(scope,list) and 0<len(scope)<=64,'invalid component reviewed-file scope')
        seen=set();files=[]
        for item in scope:
            need(isinstance(item,dict) and set(item) in ({'path','sha256'},{'path','sha256','mode'})
                 and hashes(item.get('sha256')),'invalid reviewed-file identity')
            name=item['path'];relative(name,'reviewed file path');need(name not in seen,'duplicate reviewed file');seen.add(name)
            need(name in mapped and mapped[name]['sha256']==item['sha256'],'reviewed file missing or changed in actual loaded mapping')
            modes=[]
            for tree in (review['candidate_tree'],version['tree']):
                blob=task().checked_command(workspace,{'kind':'command','argv':['git','-C',str(repo),'cat-file','blob',tree+':'+name],
                    'timeout':3,'max_output_bytes':task().FILE_BYTES})
                need(blob['passed'] and blob['stdout_sha256']==item['sha256'],'reviewed Git bytes differ from adopted content')
                mode=task().checked_command(workspace,{'kind':'command','argv':['git','--literal-pathspecs','-C',str(repo),'ls-tree','--format=%(objectmode)',tree,'--',name],
                    'timeout':3,'max_output_bytes':4096})
                matches=[m for m in ('100644','100755') if mode['passed'] and mode['stdout_sha256']==task().digest((m+'\n').encode())]
                need(len(matches)==1,'reviewed Git object is not an ordinary source file');modes.append(matches[0])
            need(modes[0]==modes[1] and ('mode' not in item or item['mode']==modes[0]),'reviewed Git mode differs from adopted content')
            files.append({'path':name,'sha256':item['sha256'],'mode':modes[0],'loaded':mapped[name]['loaded']})
        bindings[identity]={'task_input_sha256':value['acceptance_sha256'],'result_sha256':component_result_digest(value),
            'review_sha256':review['sha256'],'candidate_tree':review['candidate_tree'],'project_id':project,
            'adopted_commit':version['commit'],'adopted_tree':version['tree'],'reviewed_files':sorted(files,key=lambda f:f['path'])}
    return bindings

def non_gating_summary(values, declared):
    """A tracking label cannot remove existing functional responsibilities."""
    identity = declared.get('tracking')
    if identity is None or identity in declared['components'] + [declared['integration'], declared['regression']]:
        return None
    for value in values.values():
        spec = value['spec']
        if any(identity in obligation['task_ids'] for obligation in (spec.get('project') or {}).get('obligations', [])):
            return None
        if value['task_id'] != identity and any(edge['task_id'] == identity and edge['basis']['status'] == 'proven'
                                               for edge in spec.get('requires', [])):
            return None
    value = values.get(identity)
    if value is not None and (value['spec'].get('assignment') or {}).get('role') != 'coordination':
        return None
    return identity


def summary_observation(values, declared, regression):
    """Aggregate freshness is observable work, not delivery admission."""
    result = {'synchronized': None, 'gaps': [], 'execution_gate': False}
    identity = declared.get('tracking')
    if identity is None:
        result['status'] = 'not_requested'
        return result
    result.update(task_id=identity, synchronized=False, status='pending')
    result['non_gating_task_id'] = non_gating_summary(values, declared)
    if result['non_gating_task_id'] is None:
        result['status'] = 'functional_task'
        result['gaps'].append({'kind': 'summary_identity_has_functional_scope', 'task_id': identity})
        return result
    value = values.get(identity)
    if value is None:
        result['gaps'].append({'kind': 'summary_task_not_observed', 'task_id': identity})
        return result
    try:
        need(current_accepted(value) and not dependency_gaps(value), 'summary task has not accepted its current input')
        need(declared_link(value, regression['task_id'], regression['acceptance_sha256']), 'summary does not refer to the current regression')
        ledger = declared['tracking_file']
        need(isinstance(ledger, dict) and set(ledger) == {'path', 'sha256'}, 'invalid summary-file reference')
        checks = [(step['id'], check) for step in value['spec']['steps'] for check in step['checks']
                  if check['kind'] == 'file' and check['path'] == ledger['path'] and check['sha256'] == ledger['sha256']]
        need(checks, 'summary update has no registered actual file observation')
        need(task().checked_file(value, checks[0][0], dict(checks[0][1]))['passed'], 'summary bytes are stale or unavailable')
        result.update(synchronized=True, status='current')
    except (task().TaskError, OSError, ValueError) as exc:
        result['gaps'].append({'kind': 'summary_not_current', 'detail': str(exc)})
    return result


def finalization(values,project_results,declared):
    result={'complete':False,'gaps':[],'summary':{'synchronized':None,'status':'not_observed','execution_gate':False}}
    if declared is None:result['gaps'].append({'kind':'cross_line_finalization_not_provided'});return result
    need(isinstance(declared,dict) and {'components','integration','regression','regression_report'}<=set(declared)<= {'components','integration','regression','regression_report','tracking','tracking_file'} and ('tracking' in declared)==('tracking_file' in declared),'invalid finalization contract')
    ids=declared['components'];need(isinstance(ids,list) and 0<len(ids)<=32 and all(identifier(x) for x in ids) and len(set(ids))==len(ids),'invalid final component set')
    for name in ('integration','regression'):need(identifier(declared[name]),'invalid finalization task identity')
    if 'tracking' in declared:need(identifier(declared['tracking']),'invalid summary task identity')
    need(len(set(ids+[declared[n] for n in ('integration','regression')]))==len(ids)+2,'finalization responsibilities overlap')
    needed=set(ids+[declared[n] for n in ('integration','regression')])
    if needed-set(values):result['gaps'].append({'kind':'finalization_tasks_missing','task_ids':sorted(needed-set(values))});return result
    integration=values[declared['integration']];regression=values[declared['regression']]
    result['summary']=summary_observation(values,declared,regression)
    summary_id=result['summary'].get('non_gating_task_id')
    for identity,p in project_results.items():
        gaps=[g for g in p['gaps'] if summary_id is None or g.get('task_id')!=summary_id]
        if gaps:result['gaps'].append({'kind':'project_scope_incomplete','project_id':identity,'gaps':gaps})
        required={m for o in p['obligations'].values() for m in o['task_ids']}
        omitted=[m for m in required if values.get(m,{}).get('spec',{}).get('assignment',{}).get('role') in ('implementation','investigation') and m not in ids]
        if omitted:result['gaps'].append({'kind':'final_component_omitted','task_ids':sorted(omitted)})
    for value,role in [(integration,'integration'),(regression,'final_regression')]:
        if value['spec'].get('assignment',{}).get('role')!=role or not current_accepted(value) or dependency_gaps(value):result['gaps'].append({'kind':'final_stage_not_accepted','role':role,'task_id':value['task_id']})
    for identity in ids:
        value=values[identity]
        if not value['spec'].get('project') or not value['spec'].get('assignment'):
            result['gaps'].append({'kind':'component_project_responsibility_missing','task_id':identity})
            continue
        if not current_accepted(value) or not value.get('review') or value['review'].get('task_input_sha256')!=value['acceptance_sha256']:result['gaps'].append({'kind':'component_independent_verification_missing','task_id':identity})
        if not declared_link(integration,identity,value['acceptance_sha256']):result['gaps'].append({'kind':'integration_input_not_bound','task_id':identity})
    if not declared_link(regression,integration['task_id'],integration['acceptance_sha256']):result['gaps'].append({'kind':'regression_not_after_integration'})
    try:
        loaded=artifact(integration);tested=artifact(regression)
        need(loaded is not None and loaded==tested,'final regression did not use the same actual merged/loaded release')
        required_projects={values[i]['spec'].get('project',{}).get('id') for i in ids}
        need(required_projects<={v['project_id'] for v in loaded['versions']},'common loaded release omits a component project')
        components=component_bindings(values,ids,integration,loaded)
        result['component_results']=components
        reportref=declared['regression_report'];need(isinstance(reportref,dict) and set(reportref)=={'path','sha256'},'invalid final regression report reference')
        report=read_json(regression['spec']['workspace'],reportref['path'],reportref['sha256']);bound_report(regression,report)
        need(report.get('verdict')=='PASS' and report.get('artifact_sha256')==loaded['sha256'] and report.get('component_inputs')=={i:values[i]['acceptance_sha256'] for i in ids},'final regression result does not cover current common inputs')
        need(report.get('component_results')==components,'final regression does not bind current component results/reviews/adopted content')
        implementers={values[i]['spec'].get('assignment',{}).get('owner') for i in ids}|{integration['spec'].get('assignment',{}).get('owner')}
        need(text(report.get('reviewer_thread')) and report['reviewer_thread'] not in implementers,'final regression is not independent of implementation/integration')
        for item in [values[i] for i in ids]+[integration]:need(report['reviewer_thread'] not in {s.get('session_id') for s in item['steps'].values()},'worker self-test cannot sign cross-line final regression')
        result['artifact']=loaded;result['independent_report_sha256']=reportref['sha256']
    except (task().TaskError,OSError,ValueError) as exc:result['gaps'].append({'kind':'final_acceptance_unproven','detail':str(exc)})
    result['complete']=not result['gaps']
    result['completion_scope']='declared functional delivery and current loaded regression; aggregate synchronization is separate'
    return result

def workset(codex_home,request):
    ids=request.get('task_ids');need(isinstance(ids,list) and 0<len(ids)<=32 and all(identifier(v) for v in ids) and len(set(ids))==len(ids),'workset needs named existing task IDs')
    operation=request['operation'];maximum=request.get('max_turns',3)
    need(type(maximum) is int and 1<=maximum<=4,'workset invocation allows1..4 turns')
    root=Path(codex_home)/'task-runtime';errors={};values={};executed=[];received=[];intake_errors=[];failed_intakes=set()
    incoming=request.get('results',{})
    need(isinstance(incoming,dict) and set(incoming)<=set(ids),'workset results must name selected original tasks')
    need(not incoming or operation=='work','result intake needs an explicit bounded work invocation')
    for item in incoming.values():
        need(isinstance(item,dict) and {'result'}<=set(item)<= {'result','review'},'invalid original result intake')
        for key in ('result','review'):
            if key in item:
                ref=item[key];need(isinstance(ref,dict) and set(ref)=={'path','sha256'} and hashes(ref.get('sha256')),'invalid intake reference')
                relative(ref.get('path'),'original intake path')
    def refresh():
        values.clear();errors.clear()
        for identity in ids:
            try:values[identity]=peer(root,identity)
            except (task().TaskError,OSError,ValueError) as exc:errors[identity]=str(exc)
    with context(root,ids):
        refresh()
        if operation=='work':
            expected=request.get('expected_revisions');need(isinstance(expected,dict) and set(expected)==set(ids) and all(type(v) is int for v in expected.values()),'workset needs exact task revision expectations')
            need(all(value['revision']==expected[identity] for identity,value in values.items()),'workset input revision changed')
            for identity,item in incoming.items():
                if identity not in values:continue
                store=task().Store(codex_home,identity)
                try:
                    for operation_name,key in [('collect-result','result'),('review-result','review')]:
                        if key not in item:continue
                        before=store.read();change={'operation':operation_name,'expected_revision':before['revision'],**item[key]}
                        current=task().change(store,change)
                        received.append({'task_id':identity,'operation':operation_name,'revision':current['revision'],
                                         'state':'duplicate_reused' if before['revision']==current['revision'] else 'consumed'})
                except (task().TaskError,OSError,ValueError) as exc:
                    failed_intakes.add(identity);intake_errors.append({'task_id':identity,'detail':str(exc)})
                refresh()
            cursor=0
            for _ in range(maximum):
                selected=None
                for offset in range(len(ids)):
                    index=(cursor+offset)%len(ids);identity=ids[index]
                    if identity not in failed_intakes and identity in values and not values[identity]['accepted'] and task().partition(values[identity])['ready'] and values[identity]['execution'] is None:
                        if values[identity]['spec'].get('execution_mode')=='external' and identity not in request.get('observations',{}):continue
                        selected=identity;cursor=(index+1)%len(ids);break
                if selected is None:break
                try:
                    store=task().Store(codex_home,selected)
                    if values[selected]['spec'].get('execution_mode')=='external':
                        observation=request['observations'][selected]
                        need(isinstance(observation,dict) and set(observation)=={'path','sha256'},'invalid external observation reference')
                        task().observe_external(store,values[selected]['revision'],observation)
                    else:task().run(store,values[selected]['revision'],1)
                    executed.append({'task_id':selected,'result':'returned'})
                except (task().TaskError,OSError,ValueError) as exc:executed.append({'task_id':selected,'result':'scoped_error','detail':str(exc)})
                refresh()
        projected={identity:task().view(value) for identity,value in values.items()}
        # Read only explicitly declared missing members, never enumerate tasks.
        extra={member for value in values.values() for obligation in value['spec'].get('project',{}).get('obligations',[]) for member in obligation['task_ids']}-set(values)
        need(len(extra)<=64,'project membership lookup bound')
        for identity in sorted(extra):
            try:values[identity]=peer(root,identity)
            except (task().TaskError,OSError,ValueError) as exc:errors[identity]=str(exc)
        projects=project_view(values,errors);closed=finalization(values,projects,request.get('finalization'))
        summary_id=closed['summary'].get('non_gating_task_id')
        functional_errors={identity:detail for identity,detail in errors.items() if identity!=summary_id}
        if functional_errors:
            closed['gaps'] += [{'kind':'named_task_not_observed','task_id':identity,'detail':detail} for identity,detail in sorted(functional_errors.items())]
            closed['complete']=False
        remaining=[identity for identity,current in projected.items() if not current['accepted'] and identity!=summary_id]
        if remaining:
            closed['gaps'].append({'kind':'named_tasks_remaining','task_ids':remaining})
            closed['complete']=False
        return {'scope':'named existing task/project workset; no permissions or original-record authenticity granted','tasks':projected,'errors':errors,'projects':projects,'executed':executed,'received':received,'intake_errors':intake_errors,'continuation':continuation_projection(projected,closed),'finalization':closed,'overall_complete':closed['complete'],'model_adoption':'not established by this projection or synthetic tests'}

# Optional original-result scope, held in the existing task record. These
# relationships do not authenticate a native source or grade domain reasoning.
def validate_result_scope(scope):
    need(isinstance(scope,dict) and set(scope)=={'object_ref','goal','source_ref','obligations'},'invalid original result scope')
    need(all(text(scope[k]) for k in ('object_ref','goal','source_ref')),'original object/goal/source missing')
    rows=scope['obligations'];need(isinstance(rows,list) and 0<len(rows)<=32,'invalid original obligations')
    seen=set()
    for row in rows:
        need(isinstance(row,dict) and set(row)=={'id','acceptance'} and identifier(row.get('id')) and text(row.get('acceptance')) and row['id'] not in seen,'invalid original obligation')
        seen.add(row['id'])

def result_state(value):
    return value.get('result_state')

def initialize_result(value,prior=None):
    scope=value['spec'].get('result_scope')
    if scope is None:
        need(prior is None,'original result obligations cannot be removed by amendment');return
    if prior:
        need(scope['object_ref']==prior['object_ref'],'amendment cannot substitute original business object')
    state=copy.deepcopy(prior) if prior else {'object_ref':scope['object_ref'],'original_goal':scope['goal'],'obligations':{},'pending':[]}
    for row in scope['obligations']:
        old=state['obligations'].get(row['id'])
        need(old is None or old==row['acceptance'],'amendment cannot silently change original acceptance')
        if old is None:state['pending'].append(row['id'])
        state['obligations'][row['id']]=row['acceptance']
    # Current-request acceptance always requires a current result/review. Old
    # finite results stay in history, not as an answer to the amended input.
    state['pending']=list(state['obligations']);state['result']=None;state['review']=None;state['receipts']=[]
    need(len(state['obligations'])<=64,'retained original obligation bound')
    value['result_state']=state

def evidence_reference(value,ref):
    need(isinstance(ref,dict) and set(ref)=={'path','sha256'},'invalid passive evidence reference')
    relative(ref.get('path'),'passive evidence path');need(hashes(ref.get('sha256')),'invalid passive evidence hash')
    check={'kind':'file',**ref}
    # This is a passive evidence read, not execution of an arbitrary first
    # registered step. Exact local/peer path and FD guards remain mandatory;
    # original step/dependency constraints still govern execution/acceptance.
    need(task().checked_file(value,None,check)['passed'],'passive evidence bytes unavailable, changed or blocked')

def origin(value):
    need(isinstance(value,dict) and set(value)=={'operation_id','attempt_id','session_id','turn_id'},'invalid original operation/attempt/native mapping')
    need(text(value['operation_id']) and text(value['attempt_id']),'original operation/attempt missing')
    need(all(value[k] is None or text(value[k]) for k in ('session_id','turn_id')),'invalid original native identity')
    need(value['turn_id'] is None or value['session_id'] is not None,'native turn requires original session')

def checkpoint(value,request):
    need(result_state(value) is not None,'continuation checkpoint needs original result scope')
    key=request.get('step');need(key in value['steps'],'unknown checkpoint step')
    current=value['steps'][key];mapping=request.get('origin');origin(mapping)
    prior=current.get('continuation');active=value.get('execution')
    if prior:need(prior['origin']==mapping,'continuation must retain original operation/attempt/native turn')
    if active and active['step']==key:
        need(active['id']==mapping['attempt_id'] and active.get('session_id')==mapping['session_id'],'checkpoint is not the original native execution')
        known=next((e.get('native_turn_id') for e in reversed(current['evidence']) if e.get('execution_id')==active['id'] and e.get('native_turn_id')),None)
        need(known is None or known==mapping['turn_id'],'checkpoint substituted native turn')
    refs=request.get('effects');need(isinstance(refs,list) and len(refs)<=16,'invalid original durable effects')
    saved=copy.deepcopy(prior) if prior else {'origin':copy.deepcopy(mapping),'effects':[],'terminal':'unknown','terminal_ref':None,'resolved':False}
    for ref in refs:
        need(isinstance(ref,dict) and set(ref)=={'kind','path','sha256'} and ref.get('kind') in ('input','freeze','commit','delivery'),'invalid durable effect kind')
        old=next((e for e in saved['effects'] if e['kind']==ref['kind'] and e['path']==ref['path']),None)
        need(old is None or old==ref,'original input/freeze/effect cannot be overwritten')
        evidence_reference(value,{k:ref[k] for k in ('path','sha256')})
        if old is None:saved['effects'].append(copy.deepcopy(ref))
    need(len(saved['effects'])<=16,'durable effect reference bound')
    if prior:
        for ref in prior['effects']:evidence_reference(value,{k:ref[k] for k in ('path','sha256')})
    saved['resolved']=False;current['continuation']=saved
    value['accepted']=False
    if active and active['step']==key:
        current['unresolved_execution']=active;current['state']='reconcile'
        current['wait']={'kind':'original_continuation_unknown','next':'read_original_terminal_and_effects_without_replay'}
        value['execution']=None
    return saved

def continue_original(value,request):
    key=request.get('step');need(key in value['steps'],'unknown continuation step')
    saved=value['steps'][key].get('continuation');need(saved is not None,'no original continuation checkpoint')
    for ref in saved['effects']:evidence_reference(value,{k:ref[k] for k in ('path','sha256')})
    ref=request.get('terminal_ref')
    if ref is not None:
        need(isinstance(ref,dict) and set(ref)=={'path','sha256'},'invalid original terminal reference')
        evidence_reference(value,ref)
        report=read_json(value['spec']['workspace'],ref['path'],ref['sha256'],value=value)
        need(isinstance(report,dict) and set(report)=={'origin','status','source_ref'},'invalid original terminal readback')
        origin(report['origin']);need(report['origin']==saved['origin'] and text(report['source_ref']),'terminal readback belongs to another original operation/attempt/turn')
        need(report['status'] in ('unknown','running','completed','not_executed','failed'),'invalid original terminal status')
        need(saved['terminal'] not in ('completed','not_executed','failed') or saved['terminal']==report['status'],'terminal observation cannot rewrite a known original outcome')
        if report['status']=='not_executed':need(not any(e['kind'] in ('commit','delivery') for e in saved['effects']),'NOT_EXECUTED conflicts with already durable effects')
        saved['terminal']=report['status'];saved['terminal_ref']=copy.deepcopy(ref)
    saved['next_action']={'completed':'consume_original_result','not_executed':'decide_original_authorized_step_with_remaining_budget','failed':'inspect_original_failure_without_replay'}.get(saved['terminal'],'inspect_original_native_and_durable_effects')
    saved['may_launch']=saved['may_commit']=saved['may_send']=False
    saved['source_authentication']='caller must verify permitted original native read; JSON labels are not authentication'
    return saved

def result_receipts(state):
    if 'receipts' in state:
        return state['receipts']
    # Read old records without inventing a native source. A later mutation
    # persists the same original references; old input history is untouched.
    if state.get('result') is None:
        return []
    return [{'result':state['result'], 'native_sha256':None,
             'reviews':[state['review']] if state.get('review') else []}]


def review_content(value,state,result,reference,report):
    bound_report(value,report)
    need(report.get('object_ref')==state['object_ref'],'result belongs to another original business object')
    need(report.get('result_sha256')==reference['sha256'],'substantive review is not for current actual result')
    reviewer=report.get('reviewer_thread')
    need(text(reviewer) and reviewer not in (value['spec'].get('assignment',{}).get('owner'),
         value['spec'].get('request_identity',{}).get('thread_id')),'independent substantive reviewer missing')
    rows=report.get('assessments');need(isinstance(rows,list) and len(rows)<=64,'invalid substantive assessments')
    seen=set()
    for row in rows:
        need(isinstance(row,dict) and set(row)=={'id','verdict','reason'} and
             isinstance(row.get('id'),str) and row['id'] in state['obligations'] and row['id'] not in seen and
             row['verdict'] in ('PASS','FAIL','NOT_RUN','UNKNOWN') and text(row['reason']),'invalid substantive assessment')
        seen.add(row['id']);answer=next((a for a in result['answers'] if a['id']==row['id']),None)
        if row['verdict']=='PASS':
            need(answer is not None and answer['status']=='answered' and answer['method_fit']=='applicable',
                 'partial/unknown/inapplicable result cannot satisfy original obligation')
    return {row['id']:row['verdict'] for row in rows}


def native_identity(report):
    native=report.get('native_result')
    need(isinstance(native,dict) and set(native)=={'thread_id','turn_id','final_ref','final_sha256'} and
         all(text(native.get(k)) for k in ('thread_id','turn_id','final_ref')) and hashes(native.get('final_sha256')),
         'actual current native result identity missing')
    # Thread/turn identify the returned execution. Its source reference and
    # content fingerprint cannot manufacture another native execution.
    return {key:native[key] for key in ('thread_id','turn_id')}


def retained_native_identity(value,receipt):
    if receipt.get('native_identity') is not None:
        return receipt['native_identity']
    # Old records did not retain the stable identity. Derive it only from
    # the original bounded report; an unavailable old read stays unverified.
    try:
        ref=receipt['result']
        report=read_json(value['spec']['workspace'],ref['path'],ref['sha256'],value=value)
        bound_report(value,report)
        return native_identity(report)
    except (task().TaskError,OSError,ValueError):
        return None


def pending_result_actions(value,pending):
    state=result_state(value);ready=[];followup=[];errors=[]
    for receipt in result_receipts(state):
        ref=receipt['result']
        if receipt['reviews'] and not pending:continue
        try:
            report=read_json(value['spec']['workspace'],ref['path'],ref['sha256'],value=value)
            bound_report(value,report);_result_content(value,state,report)
            if not receipt['reviews']:
                ready.append(copy.deepcopy(ref))
            else:
                unresolved=[row['id'] for row in report['answers'] if row['id'] in pending]
                if unresolved:followup.append({'kind':'continue_original_result','result':copy.deepcopy(ref),'obligations':unresolved})
        except (task().TaskError,OSError,ValueError) as exc:
            errors.append({'result_sha256':ref['sha256'],'detail':str(exc)})
    return ready,followup,errors


def receipt_coverage(value):
    state=result_state(value);assessed={};errors=[];identities={}
    for receipt in result_receipts(state):
        if not receipt['reviews']:
            continue
        try:
            actual=receipt['result'];evidence_reference(value,actual)
            result=read_json(value['spec']['workspace'],actual['path'],actual['sha256'],value=value)
            bound_report(value,result);_result_content(value,state,result)
            identity=native_identity(result);key=tuple(identity.values())
            need(receipt.get('native_identity') in (None,identity),'retained native identity differs from original report')
            need(key not in identities or identities[key]==actual['sha256'],
                 'same original native return has conflicting result bytes')
            identities[key]=actual['sha256']
            current={}
            for ref in receipt['reviews']:
                evidence_reference(value,ref)
                report=read_json(value['spec']['workspace'],ref['path'],ref['sha256'],value=value)
                current.update(review_content(value,state,result,actual,report))
            for key,verdict in current.items():assessed.setdefault(key,set()).add(verdict)
        except (task().TaskError,OSError,ValueError) as exc:
            # One held/stale original cannot stop intake of an independent
            # return. It contributes no current coverage until proved again.
            errors.append({'result_sha256':receipt['result']['sha256'],'detail':str(exc)})
    return [key for key in state['obligations'] if 'PASS' not in assessed.get(key,set()) or assessed[key]&{'FAIL','UNKNOWN'}],errors


def collect_result(value,reference,review=False):
    state=result_state(value);need(state is not None,'original result scope missing')
    evidence_reference(value,reference)
    report=read_json(value['spec']['workspace'],reference['path'],reference['sha256'],value=value);bound_report(value,report)
    need(report.get('object_ref')==state['object_ref'],'result belongs to another original business object')
    receipts=copy.deepcopy(result_receipts(state))
    changed='receipts' not in state
    if review:
        receipt=next((r for r in receipts if r['result']['sha256']==report.get('result_sha256')),None)
        need(receipt is not None,'current actual result missing')
        actual=receipt['result'];evidence_reference(value,actual)
        result=read_json(value['spec']['workspace'],actual['path'],actual['sha256'],value=value)
        bound_report(value,result);_result_content(value,state,result)
        review_content(value,state,result,actual,report)
        if not any(r['sha256']==reference['sha256'] for r in receipt['reviews']):
            receipt['reviews'].append(copy.deepcopy(reference));changed=True
        if changed:
            state['review']=copy.deepcopy(reference)
        for step in value['steps'].values():
            cp=step.get('continuation')
            if cp and cp['terminal']=='completed' and cp['origin'] in result['origins'] and not cp['resolved']:
                cp['resolved']=True;changed=True
    else:
        _result_content(value,state,report)
        native_sha=task().digest(task().encoded(report['native_result']));identity=native_identity(report)
        receipt=next((r for r in receipts if r['result']['sha256']==reference['sha256']),None)
        need(not any(r['result']['sha256']!=reference['sha256'] and
                     retained_native_identity(value,r)==identity for r in receipts),
             'same original native return has conflicting result bytes')
        if receipt is None:
            receipts.append({'result':copy.deepcopy(reference),'native_sha256':native_sha,'native_identity':identity,'reviews':[]});changed=True
            state['result']=copy.deepcopy(reference);state['review']=None
        elif receipt['native_sha256'] is None:
            receipt['native_sha256']=native_sha;receipt['native_identity']=identity;changed=True
    need(len(receipts)<=64 and sum(len(r['reviews']) for r in receipts)<=64,'current result receipt bound')
    if not changed:
        return False
    state['receipts']=receipts
    state['pending']=receipt_coverage(value)[0]
    value['accepted']=False
    return True


def continuation_projection(projected,closed):
    actions=[];waiting=[]
    for identity,current in projected.items():
        owner=current['spec'].get('assignment',{}).get('owner')
        for key in current['work_partition']['ready']:
            actions.append({'task_id':identity,'step':key,'kind':'execute_or_collect_original_step',
                            'owner':owner,'expected_revision':current['revision']})
        for item in current['work_partition']['unresolved']:
            actions.append({'task_id':identity,'kind':'verify_original_dependency_evidence','step':item['step'],'owner':owner})
        dimensions=current.get('result_dimensions')
        if dimensions:
            if dimensions['reviewable_results']:
                actions.append({'task_id':identity,'kind':'review_original_results','owner':owner,
                                'expected_revision':current['revision'],'results':dimensions['reviewable_results']})
            for action in dimensions['continuable_results']:
                actions.append({'task_id':identity,'owner':owner,'expected_revision':current['revision'],**action})
            if dimensions['pending_obligations'] and not current['work_partition']['waiting'] and not current['work_partition']['ready']:
                actions.append({'task_id':identity,'kind':'continue_original_obligations','owner':owner,
                                'obligations':dimensions['pending_obligations']})
        waiting.extend({'task_id':identity,**item} for item in current['work_partition']['waiting'])
    if not closed['complete'] and not actions and not waiting:
        actions.append({'kind':'resolve_original_finalization','gaps':closed['gaps']})
    return {'must_continue':bool(actions),'may_wait':bool(waiting) and not actions,
            'next_actions':actions,'scoped_waits':waiting,'host_execution_required':True,'background_active':False,
            'scope':'bounded existing-task decision; caller needs an actual authorised original host opportunity, not a new scheduler or delivery claim'}


def _result_content(value,state,report):
    need(report.get('object_ref')==state['object_ref'],'original object mismatch')
    evidence_reference(value,report.get('answer'))
    native_identity(report)
    rows=report.get('answers');need(isinstance(rows,list) and len(rows)<=64,'invalid current result answers')
    seen=set();checked=set()
    for row in rows:
        need(isinstance(row,dict) and set(row)=={'id','status','method_fit','evidence'} and isinstance(row.get('id'),str) and row['id'] in state['obligations'] and row['id'] not in seen,'unrequested/duplicate original obligation')
        seen.add(row['id']);need(row['status'] in ('answered','partial','unanswered','unknown') and row['method_fit'] in ('applicable','limited','not_applicable','unknown'),'invalid actual result dimensions')
        refs=row['evidence'];need(isinstance(refs,list) and len(refs)<=16 and (row['status']!='answered' or refs),'actual answered result needs execution evidence')
        for ref in refs:
            need(isinstance(ref,dict) and set(ref)=={'path','sha256'} and isinstance(ref.get('path'),str) and hashes(ref.get('sha256')),'invalid execution evidence')
            identity=(ref['path'],ref['sha256'])
            if identity not in checked:
                need(len(checked)<64,'distinct execution evidence bound');evidence_reference(value,ref);checked.add(identity)
    origins=report.get('origins');need(isinstance(origins,list) and len(origins)<=32,'invalid original result operation mappings')
    for mapping in origins:origin(mapping)
    for step in value['steps'].values():
        cp=step.get('continuation')
        if cp and cp['origin'] in origins:
            need(cp['terminal']=='completed','UNKNOWN/running/failed original outcome cannot be consumed as complete')
            for ref in cp['effects']:evidence_reference(value,{k:ref[k] for k in ('path','sha256')})

def result_projection(value):
    state=result_state(value)
    if state is None:return None
    pending,errors=receipt_coverage(value)
    reviewable,followup,unavailable=pending_result_actions(value,pending);errors+=unavailable
    unreviewed=[r['result'] for r in result_receipts(state) if not r['reviews']]
    original_unknown=any(s.get('continuation') and not s['continuation']['resolved'] for s in value['steps'].values())
    return {'object_ref':state['object_ref'],'original_goal':state['original_goal'],'pending_obligations':pending,
        'program_checks_passed':all(s['state']=='verified' for s in value['steps'].values()),
        'model_turn_terminal':[{'origin':s['continuation']['origin'],'status':s['continuation']['terminal']} for s in value['steps'].values() if s.get('continuation')],
        'current_result':state.get('result'),'substantive_review':state.get('review'),
        'result_receipts':copy.deepcopy(result_receipts(state)),'unreviewed_results':unreviewed,
        'reviewable_results':reviewable,'continuable_results':followup,
        'receipt_errors':errors,
        'business_outcome':'pending' if pending or original_unknown or unreviewed or errors else 'reviewed_declared_scope',
        'native_authenticity':'not established by file labels; original caller must verify actual native records',
        'wake_source':'not provided by this task runtime; each continuation requires an actual host execution opportunity',
        'original_operations':[s['continuation'] for s in value['steps'].values() if s.get('continuation')]}


def validate_current_result(value):
    state=result_state(value)
    if state is None:return
    pending,errors=receipt_coverage(value)
    need(not pending and state.get('result') and result_receipts(state) and
         all(r['reviews'] for r in result_receipts(state)),'original user result is still pending')
    need(not errors,'current original result evidence is stale or unavailable')


def current_accepted(value):
    if not value['accepted']:return False
    try:validate_current_result(value)
    except (task().TaskError,OSError,ValueError):return False
    return True


def validate_result_state(value):
    state=result_state(value)
    need(('result_scope' in value['spec'])==(state is not None),'original result state/scope mismatch')
    if state is None:return
    need(isinstance(state,dict) and {'object_ref','original_goal','obligations','pending','result','review'}<=set(state)<= {'object_ref','original_goal','obligations','pending','result','review','receipts'},'invalid original result state')
    need(state['object_ref']==value['spec']['result_scope']['object_ref'] and text(state['original_goal']),'original goal/object changed')
    obligations=state['obligations'];pending=state['pending']
    need(isinstance(obligations,dict) and 0<len(obligations)<=64 and all(identifier(k) and text(v) for k,v in obligations.items()),'invalid retained original obligations')
    need(isinstance(pending,list) and all(isinstance(k,str) and k in obligations for k in pending) and len(set(pending))==len(pending),'invalid pending original obligations')
    for key in ('result','review'):
        ref=state[key]
        if ref is not None:
            need(isinstance(ref,dict) and set(ref)=={'path','sha256'} and hashes(ref.get('sha256')),'invalid retained result reference')
            relative(ref['path'],'retained result path')


    receipts=state.get('receipts',[])
    need(isinstance(receipts,list) and len(receipts)<=64,'invalid current result receipts')
    seen=set();reviews=0
    for receipt in receipts:
        need(isinstance(receipt,dict) and {'result','native_sha256','reviews'}<=set(receipt)<= {'result','native_sha256','reviews','native_identity'} and
             (receipt['native_sha256'] is None or hashes(receipt['native_sha256'])),'invalid retained result receipt')
        identity=receipt.get('native_identity')
        need(identity is None or (isinstance(identity,dict) and set(identity)=={'thread_id','turn_id'} and
             all(text(identity[k]) for k in identity)),'invalid retained native identity')
        need(isinstance(receipt['result'],dict) and set(receipt['result'])=={'path','sha256'} and hashes(receipt['result'].get('sha256')),'invalid result receipt reference')
        relative(receipt['result'].get('path'),'retained result path')
        need(receipt['result']['sha256'] not in seen,'duplicate current result receipt');seen.add(receipt['result']['sha256'])
        need(isinstance(receipt['reviews'],list),'invalid retained review references');reviews+=len(receipt['reviews'])
        for ref in receipt['reviews']:
            need(isinstance(ref,dict) and set(ref)=={'path','sha256'} and hashes(ref.get('sha256')),'invalid retained review reference')
            relative(ref.get('path'),'retained review path')
    need(reviews<=64,'current substantive review bound')

def check_passive_object(value,path,device,inode,step_id=None):
    """Check the actual opened read object, including peer READ refusals.

    The identity is captured from this FD, not supplied as a permission label.
    No original step declaration is changed or used as a substitute for it.
    """
    scope={'files':[path],'file_objects':[{'device':device,'inode':inode}]}
    step={'id':step_id,'checks':[]}
    need(not any(task().same_restricted_scope(h,step,scope) for h in value.get('restrictions',[])),'passive object matches retained local restriction')
    wanted=[{'kind':'file','identity':path,'object':'','mode':'read','operation':'read','source_ref':'actual passive descriptor',
             'object_identity':{'device':device,'inode':inode}}]
    conflicts=effect_conflicts(value,step,wanted=wanted)
    need(not conflicts,'passive object has retained refusal or unresolved peer effect')
