"""CAND01: acquired review/source content must match the actually loaded set."""
import copy
import json
import unittest
from pathlib import Path
from unittest import mock

import test_project_runtime as fixtures
import task_coordination as coord
import task_runtime as task
from test_framework import run_git


class ComponentAdoptionTests(fixtures.ProjectRuntimeTests):
    # Only this class's new methods are loaded below; inherited suites stay out.
    def setUp(self):
        super().setUp()
        self.model_guard=mock.patch('task_worker.execute',side_effect=AssertionError('no model'))
        self.native_guard=mock.patch('task_worker.inspect_session',side_effect=AssertionError('no native query'))
        self.model_guard.start();self.native_guard.start()
        self.addCleanup(self.model_guard.stop);self.addCleanup(self.native_guard.stop)

    def write_review(self,store,**fields):
        original=store.read()['review'];report=json.loads(Path(original['path']).read_text());report.update(fields)
        ref=self.put_report(store.read()['task_id']+'-review.json',report)
        self.change(store,'review',report_path=str(self.root/ref['path']),report_sha256=ref['sha256'])

    def scoped_merge(self,mutation='unrelated',fake_hash=False):
        def change(project,repo,store):
            if project!='core':return
            fingerprint=task.digest((repo/'result.txt').read_bytes())
            tree=run_git(repo,'rev-parse','HEAD^{tree}')
            self.write_review(store,reviewed_files=[{'path':'result.txt','sha256':fingerprint,'mode':'100644'}])
            if mutation=='unrelated':(repo/'peer.txt').write_text('Independent peer contribution')
            elif mutation=='bytes':(repo/'result.txt').write_text('Changed after component review')
            elif mutation=='mode':(repo/'result.txt').chmod(0o755)
            run_git(repo,'add','.');run_git(repo,'commit','-m','combined delivery')
            self.assertNotEqual(tree,run_git(repo,'rev-parse','HEAD^{tree}'))
            if fake_hash:
                self.write_review(store,reviewed_files=[{'path':'result.txt','sha256':task.digest((repo/'result.txt').read_bytes()),'mode':'100644'}])
        return change

    def test_full_tree_and_explicit_unchanged_scope_support_real_merge(self):
        ids,final,report=self.prepare_final_chain(merge_after_review=self.scoped_merge())
        out=self.coordinate(ids,finalization=final)
        self.assertTrue(out['overall_complete'],out['finalization'])
        binding=out['finalization']['component_results']['core']
        self.assertNotEqual(binding['candidate_tree'],binding['adopted_tree'])
        self.assertEqual(binding['reviewed_files'][0]['sha256'],task.digest(b'merged core'))
        self.assertEqual(binding['reviewed_files'][0]['mode'],'100644')

    def test_changed_reviewed_bytes_modes_and_false_declared_hash_never_pass(self):
        for mutation,fake_hash in [('bytes',False),('mode',False),('bytes',True)]:
            with self.subTest(mutation=mutation,fake_hash=fake_hash):
                # Each negative has an independent workspace to avoid a second
                # registration or any retry of an unresolved original task.
                f=ComponentAdoptionTests();f.setUp()
                try:
                    ids,final,_=f.prepare_final_chain(merge_after_review=f.scoped_merge(mutation,fake_hash),allow_unbound=True)
                    out=f.coordinate(ids,finalization=final)
                    self.assertFalse(out['overall_complete'])
                    self.assertIn('final_acceptance_unproven',{g['kind'] for g in out['finalization']['gaps']})
                finally:f.doCleanups();f.tearDown()

    def test_new_review_under_same_input_invalidates_old_final_report(self):
        ids,final,_=self.prepare_final_chain();store=task.Store(self.a.codex_home,'core')
        before=store.read()['acceptance_sha256']
        self.write_review(store,reviewer_thread='independent-core-new-review')
        self.assertEqual(store.read()['acceptance_sha256'],before)
        out=self.coordinate(ids,finalization=final)
        self.assertFalse(out['overall_complete'])
        self.assertTrue(any('component results/reviews/adopted content' in g.get('detail','') for g in out['finalization']['gaps']))

    def test_original_report_byte_drift_cannot_be_replaced_by_saved_labels(self):
        ids,final,_=self.prepare_final_chain();store=task.Store(self.a.codex_home,'core')
        path=Path(store.read()['review']['path']);body=json.loads(path.read_text());body['note']='Changed after accepted review';path.write_bytes(task.encoded(body))
        out=self.coordinate(ids,finalization=final)
        self.assertFalse(out['overall_complete'])
        self.assertIn('final_acceptance_unproven',{g['kind'] for g in out['finalization']['gaps']},out['finalization'])

    def test_input_only_or_invented_result_binding_cannot_close(self):
        ids,final,report=self.prepare_final_chain()
        for wrong in (None,dict(report['component_results'],core={**report['component_results']['core'],'result_sha256':'0'*64})):
            with self.subTest(binding=wrong is None):
                revised=copy.deepcopy(report)
                if wrong is None:revised.pop('component_results')
                else:revised['component_results']=wrong
                ref=self.put_report('final-regression.json',revised);request=copy.deepcopy(final);request['regression_report']=ref
                self.assertFalse(self.coordinate(ids,finalization=request)['overall_complete'])

    def test_source_tree_object_not_format_equality_controls_scope(self):
        def fake(project,repo,store):
            if project=='core':
                self.write_review(store,candidate_tree='1'*40,reviewed_files=[{'path':'result.txt','sha256':task.digest((repo/'result.txt').read_bytes())}])
        ids,final,_=self.prepare_final_chain(merge_after_review=fake,allow_unbound=True)
        out=self.coordinate(ids,finalization=final)
        self.assertFalse(out['overall_complete'])
        self.assertTrue(any('reviewed Git bytes' in g.get('detail','') for g in out['finalization']['gaps']))

    def test_literal_git_name_accepts_real_scope_and_cannot_borrow_neighbor_mode(self):
        for changed_mode in (False,True):
            with self.subTest(changed_mode=changed_mode):
                f=ComponentAdoptionTests();f.setUp();name=':(literal)source.txt'
                original=f.put_report
                def mapping(filename,body):
                    if filename=='loaded-manifest.json':
                        for item in body['files']:
                            if item['project_id']=='core':item['source']=name
                    return original(filename,body)
                def source(project,repo,store):
                    if project!='core':return
                    raw=(repo/'result.txt').read_bytes();(repo/name).write_bytes(raw)
                    if changed_mode:(repo/'source.txt').write_bytes(raw)
                    run_git(repo,'add','--all');run_git(repo,'commit','-m','literal reviewed source')
                    f.write_review(store,candidate_tree=run_git(repo,'rev-parse','HEAD^{tree}'),
                        reviewed_files=[{'path':name,'sha256':task.digest(raw),'mode':'100644'}])
                    if changed_mode:
                        (repo/name).chmod(0o755);run_git(repo,'add','--all');run_git(repo,'commit','-m','changed literal file mode')
                try:
                    with mock.patch.object(f,'put_report',side_effect=mapping):
                        ids,final,_=f.prepare_final_chain(merge_after_review=source,allow_unbound=True)
                    out=f.coordinate(ids,finalization=final)
                    self.assertEqual(out['overall_complete'],not changed_mode,out['finalization'])
                finally:f.doCleanups();f.tearDown()


def load_tests(loader,tests,pattern):
    return unittest.TestSuite(ComponentAdoptionTests(name) for name in ComponentAdoptionTests.__dict__ if name.startswith('test_'))


if __name__=='__main__':unittest.main()
