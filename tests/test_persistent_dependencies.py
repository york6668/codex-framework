"""DEP05: source-bound local task edges, finite fixtures only."""
import copy
import json
import os
import unittest
from pathlib import Path
from unittest import mock
import test_project_runtime as fixtures
import task_runtime as task
import task_coordination as coord


def bind_local_contract(spec,task_id,producer,consumer,kind='required_result'):
    steps={s['id']:s for s in spec['steps']}
    def endpoint(key):
        step=steps[key]
        return {'task_id':task_id,'request_id':spec['request_id'],'acceptance_version':spec['acceptance_version'],
            'workspace':spec['workspace'],'step':{k:copy.deepcopy(step.get(k,[]) if k=='resources' else step[k])
                for k in ('id','instruction','checks','resources')}}
    reason='This exact consumer requires the original producer result/effect'
    body={'producer':endpoint(producer),'consumer':endpoint(consumer),'requires':{'kind':kind,'reason':reason}}
    name='.dependency-'+task_id+'-'+producer+'-'+consumer+'.json';raw=task.encoded(body)
    (Path(spec['workspace'])/name).write_bytes(raw)
    entry=steps[consumer];entry['depends_on_basis']={**entry.get('depends_on_basis',{}),producer:{
        'status':'proven','source_ref':name,'source_sha256':task.digest(raw),'reason':reason}}


class PersistentDependencyTests(fixtures.ProjectRuntimeTests):
    def prepare(self,fact=None):
        steps=[{'id':'clock','instruction':'Original refused clock operation','checks':[{'kind':'message','equals':'42'}]},
            {'id':'host','instruction':'Separate host action','depends_on':['clock'],'checks':[{'kind':'message','equals':'42'}]},
            {'id':'safe','instruction':'Independent bounded work','checks':[{'kind':'message','equals':'42'}]}]
        spec=self.spec('local-edge',steps=steps)
        if fact=='valid':bind_local_contract(spec,'local-edge','clock','host')
        elif fact is not None:spec['steps'][1]['depends_on_basis']=fact
        store=task.Store(self.a.codex_home,'local-edge');store.create('local-edge',spec)
        self.change(store,'approval',step='clock',decision='denied',source_ref='fixture:accurate-clock-refusal')
        return store

    def test_omitted_invented_or_unknown_basis_neither_waits_nor_dispatches_consumer(self):
        for fact in (None,{'status':'unknown'},{'status':'hypothesis'},
                     {'status':'proven','source_ref':'nonexistent:original','reason':'assertion'}):
            with self.subTest(fact=fact):
                f=PersistentDependencyTests();f.setUp()
                try:
                    store=f.prepare(fact);before=store.read();partition=task.partition(before)
                    self.assertEqual(store.read(),before)
                    self.assertEqual(partition['ready'],['safe'])
                    self.assertEqual([r['step'] for r in partition['unresolved']],['host'])
                    self.assertEqual([r['step'] for r in partition['waiting']],['clock'])
                    self.assertEqual(before['steps']['clock']['wait']['kind'],'explicit_denial')
                finally:f.doCleanups();f.tearDown()

    def test_valid_original_result_relationship_keeps_only_its_consumer_waiting(self):
        store=self.prepare('valid');partition=task.partition(store.read())
        self.assertEqual(partition['ready'],['safe']);self.assertEqual(partition['unresolved'],[])
        self.assertEqual([r['step'] for r in partition['waiting']],['clock','host'])
        self.assertIn('required_local_result_not_verified',partition['waiting'][1]['condition'])

    def test_wrong_consumer_or_changed_original_bytes_requires_evidence(self):
        store=self.prepare('valid');spec=store.read()['spec'];fact=spec['steps'][1]['depends_on_basis']['clock']
        path=Path(spec['workspace'])/fact['source_ref'];raw=path.read_bytes()
        path.write_bytes(raw+b' ')
        self.assertEqual([r['step'] for r in task.partition(store.read())['unresolved']],['host'])
        body=json.loads(raw);body['consumer']['step']['instruction']='Different original duty';path.write_bytes(task.encoded(body))
        revised=copy.deepcopy(spec);revised['steps'][1]['depends_on_basis']['clock']['source_sha256']=task.digest(path.read_bytes())
        self.change(store,'amend',spec=revised)
        self.assertEqual([r['step'] for r in task.partition(store.read())['unresolved']],['host'])

    def test_false_edge_removed_after_inquiry_does_not_remove_original_refusal(self):
        store=self.prepare();spec=copy.deepcopy(store.read()['spec'])
        spec['steps'][1]['depends_on']=[]
        self.change(store,'amend',spec=spec)
        partition=task.partition(store.read());self.assertEqual(partition['ready'],['host','safe'])
        self.assertEqual(partition['unresolved'],[])
        self.assertEqual(store.read()['steps']['clock']['wait']['kind'],'explicit_denial')

    def test_compatible_reads_do_not_become_shared_effect_dependency(self):
        store=self.prepare();spec=copy.deepcopy(store.read()['spec'])
        for s in spec['steps'][:2]:s['resources']=[self.resource(mode='read')]
        bind_local_contract(spec,'local-edge','clock','host','shared_effect')
        self.change(store,'amend',spec=spec)
        self.assertEqual([r['step'] for r in task.partition(store.read())['unresolved']],['host'])

    def test_missing_edge_in_partial_mapping_does_not_inherit_hard_status(self):
        store=self.prepare('valid');spec=copy.deepcopy(store.read()['spec'])
        spec['steps'][1]['depends_on'].append('safe')
        # Backward edges are explicit; move the independent producer before host.
        spec['steps']=[spec['steps'][0],spec['steps'][2],spec['steps'][1]]
        self.change(store,'amend',spec=spec)
        partition=task.partition(store.read())
        self.assertEqual(partition['ready'],['safe']);self.assertEqual([r['step'] for r in partition['unresolved']],['host'])

    def test_bounded_original_source_rejects_duplicates_oversize_and_fifo(self):
        for variant in ('duplicate','oversize','fifo'):
            with self.subTest(variant=variant):
                f=PersistentDependencyTests();f.setUp()
                try:
                    store=f.prepare('valid');spec=copy.deepcopy(store.read()['spec'])
                    fact=spec['steps'][1]['depends_on_basis']['clock'];path=Path(spec['workspace'])/fact['source_ref']
                    body=json.loads(path.read_bytes())
                    if variant=='duplicate':
                        raw=b'{"producer":'+task.encoded(body['producer'])+b',"producer":'+task.encoded(body['producer'])+b',"consumer":'+task.encoded(body['consumer'])+b',"requires":'+task.encoded(body['requires'])+b'}'
                        path.write_bytes(raw);fact['source_sha256']=task.digest(raw)
                    elif variant=='oversize':
                        raw=b' '* (task.LIMIT+1);path.write_bytes(raw);fact['source_sha256']=task.digest(raw)
                    else:path.unlink();os.mkfifo(path)
                    f.change(store,'amend',spec=spec)
                    partition=task.partition(store.read())
                    self.assertEqual(partition['ready'],['safe'])
                    self.assertEqual([r['step'] for r in partition['unresolved']],['host'])
                finally:f.doCleanups();f.tearDown()

    def test_verified_producer_file_drift_does_not_release_its_consumer(self):
        output=self.root/'producer.txt';output.write_bytes(b'original')
        steps=[{'id':'a','instruction':'Produce original input','checks':[{'kind':'file','path':output.name,'sha256':task.digest(b'original')}]},
            {'id':'b','instruction':'Consume exact original input','depends_on':['a'],'checks':[{'kind':'message','equals':'42'}]},
            {'id':'c','instruction':'Independent work','checks':[{'kind':'message','equals':'42'}]}]
        spec=self.spec('drift',steps=steps);bind_local_contract(spec,'drift','a','b')
        store=task.Store(self.a.codex_home,'drift');store.create('drift',spec)
        with mock.patch('task_worker.execute',return_value=self.worker()):task.run(store,store.read()['revision'],max_turns=1)
        self.assertEqual(task.partition(store.read())['ready'],['b','c'])
        output.write_bytes(b'changed')
        partition=task.partition(store.read());self.assertEqual(partition['ready'],['c'])
        self.assertTrue(any(r['step']=='b' and 'required_local_result_stale' in r['condition'] for r in partition['waiting']))

    def test_transitive_stale_or_unproved_input_keeps_only_independent_work_ready(self):
        for missing_source in (False,True):
            with self.subTest(missing_source=missing_source):
                f=PersistentDependencyTests();f.setUp()
                try:
                    (f.root/'a.txt').write_bytes(b'A');(f.root/'b.txt').write_bytes(b'B from A')
                    steps=[{'id':'a','instruction':'Produce A','checks':[{'kind':'file','path':'a.txt','sha256':task.digest(b'A')}]},
                        {'id':'b','instruction':'Derive B from A','depends_on':['a'],'checks':[{'kind':'file','path':'b.txt','sha256':task.digest(b'B from A')}]},
                        {'id':'c','instruction':'Consume B from current A','depends_on':['b'],'checks':[{'kind':'message','equals':'42'}]},
                        {'id':'safe','instruction':'Independent work','checks':[{'kind':'message','equals':'42'}]}]
                    spec=f.spec('chain',steps=steps);bind_local_contract(spec,'chain','a','b');bind_local_contract(spec,'chain','b','c')
                    store=task.Store(f.a.codex_home,'chain');store.create('chain',spec)
                    with mock.patch('task_worker.execute',return_value=f.worker()):task.run(store,store.read()['revision'],max_turns=2)
                    self.assertEqual(task.partition(store.read())['ready'],['c','safe'])
                    if missing_source:(f.root/spec['steps'][1]['depends_on_basis']['a']['source_ref']).unlink()
                    else:(f.root/'a.txt').write_bytes(b'changed A')
                    before=store.path.read_bytes();partition=task.partition(store.read());view=task.view(store.read())
                    self.assertEqual(store.path.read_bytes(),before)
                    self.assertEqual(partition['ready'],['safe'])
                    self.assertEqual(view['next_action']['step'],'safe')
                    if missing_source:self.assertEqual([r['step'] for r in partition['unresolved']],['b','c'])
                    else:self.assertEqual([r['step'] for r in partition['waiting']],['b','c'])
                    with mock.patch('task_worker.execute',side_effect=AssertionError('no dispatch')) as guard:
                        _,execution=task.reserve(store,store.read()['revision'])
                    self.assertEqual(execution['step'],'safe');self.assertEqual(guard.call_count,0)
                finally:f.doCleanups();f.tearDown()

    def test_shared_upstream_is_checked_once_per_local_graph_walk(self):
        steps=[]
        for key,parents in [('a',[]),('b',['a']),('c',['b']),('d',['b'])]:
            path=self.root/(key+'.txt');path.write_bytes(key.encode())
            steps.append({'id':key,'instruction':'Produce '+key,'depends_on':parents,
                'checks':[{'kind':'file','path':path.name,'sha256':task.digest(key.encode())}]})
        steps.extend([{'id':'e','instruction':'Consume both current branches','depends_on':['c','d'],'checks':[{'kind':'message','equals':'42'}]},
            {'id':'safe','instruction':'Independent work','checks':[{'kind':'message','equals':'42'}]}])
        spec=self.spec('dag',steps=steps)
        for producer,consumer in [('a','b'),('b','c'),('b','d'),('c','e'),('d','e')]:bind_local_contract(spec,'dag',producer,consumer)
        store=task.Store(self.a.codex_home,'dag');store.create('dag',spec)
        with mock.patch('task_worker.execute',return_value=self.worker()):task.run(store,store.read()['revision'],max_turns=4)
        self.assertEqual(task.partition(store.read())['ready'],['e','safe'])
        (self.root/'a.txt').write_bytes(b'changed')
        value=store.read()
        with mock.patch.object(coord,'read_json',wraps=coord.read_json) as reads:
            gaps=coord.local_dependency_gaps(value,value['spec']['steps'][4])
        self.assertTrue(gaps);self.assertEqual(reads.call_count,5)
        self.assertEqual(task.partition(value)['ready'],['safe'])

    def test_external_passive_defaults_and_explicit_reads_have_same_dependency_route(self):
        for external,explicit in [(True,False),(True,True),(False,True)]:
            with self.subTest(external=external,explicit=explicit):
                f=PersistentDependencyTests();f.setUp()
                try:
                    (f.root/'shared').write_bytes(b'acquired');(f.root/'other').write_bytes(b'independent')
                    steps=[{'id':key,'instruction':'Observe '+key,'checks':[{'kind':'file','path':'shared' if key!='c' else 'other',
                        'sha256':task.digest(b'acquired' if key!='c' else b'independent')}]} for key in ('a','b','c')]
                    steps[1]['depends_on']=['a']
                    if explicit:
                        for i in (0,1):steps[i]['resources']=[{'kind':'file','identity':'shared','object':'',
                            'mode':'write' if not external and i==0 else 'read','operation':'change' if not external and i==0 else 'inspect','source_ref':'fixture:original-effect'}]
                    spec=f.spec('passive',execution_mode='external' if external else 'native',steps=steps)
                    bind_local_contract(spec,'passive','a','b','shared_effect')
                    store=task.Store(f.a.codex_home,'passive');store.create('passive',spec)
                    partition=task.partition(store.read());self.assertEqual(partition['ready'],['a','c'])
                    self.assertEqual([r['step'] for r in partition['unresolved']],['b'] if external else [])
                    self.assertEqual([r['step'] for r in partition['waiting']],[] if external else ['b'])
                finally:f.doCleanups();f.tearDown()


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(PersistentDependencyTests(name) for name in PersistentDependencyTests.__dict__ if name.startswith('test_'))

if __name__=='__main__':unittest.main()
