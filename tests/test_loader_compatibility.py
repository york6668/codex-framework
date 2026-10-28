"""Approved historical loader recovery; no legacy task engine or node data."""

import ast
import json
import subprocess
import sys
from unittest import mock

import test_framework as fixtures
import framework_runtime as runtime


class LoaderCompatibilityTests(fixtures.FixtureCase):
    def prior_managed_loader_fixture(self, literal=True):
        path=self.repo/'scripts/framework_runtime.py'
        current=path.read_text()
        tree=ast.parse(current)
        assignments={node.targets[0].id:node for node in tree.body if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name)}
        template='HOME = %r\nMODULES = %r\n# synthetic previously approved managed template\n'
        legacy=current.replace(ast.get_source_segment(current,assignments['MODULES'].value),repr(runtime.LEGACY_MODULES),1)
        expression=repr(template) if literal else "''.join(["+repr(template)+"])"
        legacy=legacy.replace(ast.get_source_segment(current,assignments['LOADER'].value),expression,1)
        marker=self.root/'old-source-was-executed'
        legacy+='\nfrom pathlib import Path\nPath('+repr(str(marker))+').write_text("unexpected")\n'
        path.write_text(legacy)
        prior=fixtures.seed_runtime(self.repo)
        if literal:
            runtime.install(self.a,prior)
        else:
            # Recreate a package already installed by an older installer. The
            # current installer rejects this expression even on first install;
            # only fixture setup supplies the historical rendered launcher.
            rendered=(template%(str(self.a.codex_home),runtime.LEGACY_MODULES)).encode()
            with mock.patch.object(runtime,'prior_loader_bytes',return_value=rendered):
                runtime.install(self.a,prior)
        # The initial explicit package install runs its approved smoke commands.
        # Only the later old-template recognition must avoid executing old code.
        marker.unlink()
        launcher=runtime.launcher_path(self.a)
        launcher.write_bytes((template%(str(self.a.codex_home),runtime.LEGACY_MODULES)).encode())
        path.write_text(current)
        fixtures.run_git(self.repo,'add','scripts/framework_runtime.py')
        fixtures.run_git(self.repo,'commit','-m','new exact runtime fixture')
        new=fixtures.run_git(self.repo,'rev-parse','HEAD')
        return prior,new,marker


    def test_exact_prior_managed_loader_migrates_without_executing_old_source(self):
        prior,new,marker=self.prior_managed_loader_fixture()
        self.assertEqual(len(runtime.load(runtime.version_path(self.a,prior)/'manifest.json')['files']),8)
        result=runtime.install(self.a,new,expected_runtime=prior)
        self.assertEqual(result['status'],'installed')
        self.assertEqual(runtime.active(self.a)['revision'],new)
        self.assertEqual(runtime.launcher_path(self.a).read_bytes(),runtime.loader_bytes(self.a))
        self.assertEqual(len(runtime.load(runtime.version_path(self.a,new)/'manifest.json')['files']),len(runtime.MODULES)+1)
        self.assertFalse(marker.exists())


    def test_prior_loader_migration_preserves_unknown_or_unverified_state(self):
        prior,new,marker=self.prior_managed_loader_fixture()
        launcher=runtime.launcher_path(self.a);active=runtime.pointer(self.a)
        approved=runtime.root(self.a)/'approved.json'
        module=runtime.version_path(self.a,prior)/'scripts/framework_runtime.py'
        original={p:p.read_bytes() for p in [launcher,active,approved,module]}
        mutations=[(launcher,original[launcher]+b'\n# local customization\n'),
                   (module,original[module]+b'\n# unverified package mutation\n'),
                   (approved,runtime.encoded({})),
                   (active,runtime.encoded(dict(json.loads(original[active]),manifest_sha256='f'*64)))]
        for target,changed in mutations:
            with self.subTest(target=str(target)):
                for path,raw in original.items():path.write_bytes(raw)
                target.write_bytes(changed)
                before_active=active.read_bytes();before_loader=launcher.read_bytes()
                with self.assertRaises(fixtures.cf.SyncError):runtime.install(self.a,new,expected_runtime=prior)
                self.assertEqual(active.read_bytes(),before_active)
                self.assertEqual(launcher.read_bytes(),before_loader)
                self.assertFalse(runtime.journal(self.a).exists())
        self.assertFalse(marker.exists())


    def test_prior_loader_change_after_recognition_cannot_be_blessed_as_before_image(self):
        prior,new,marker=self.prior_managed_loader_fixture()
        launcher=runtime.launcher_path(self.a)
        before_active=runtime.pointer(self.a).read_bytes()
        unknown=b"owned intervening local launcher edit\n"
        recognize=runtime.prior_loader_bytes
        def changed_after_recognition(framework,pointer):
            known=recognize(framework,pointer)
            launcher.write_bytes(unknown)
            return known
        with mock.patch('framework_runtime.prior_loader_bytes',side_effect=changed_after_recognition):
            with self.assertRaisesRegex(fixtures.cf.SyncError,'入口核对后改变'):
                runtime.install(self.a,new,expected_runtime=prior)
        self.assertEqual(launcher.read_bytes(),unknown)
        self.assertEqual(runtime.pointer(self.a).read_bytes(),before_active)
        self.assertFalse(runtime.journal(self.a).exists())
        self.assertFalse(marker.exists())


    def test_prior_loader_template_requires_literal_without_running_expression(self):
        prior,new,marker=self.prior_managed_loader_fixture(literal=False)
        before=runtime.launcher_path(self.a).read_bytes()
        with self.assertRaisesRegex(fixtures.cf.SyncError,'模板无法静态核对'):
            runtime.install(self.a,new,expected_runtime=prior)
        self.assertEqual(runtime.active(self.a)['revision'],prior)
        self.assertEqual(runtime.launcher_path(self.a).read_bytes(),before)
        self.assertFalse(marker.exists())


    def test_manifest_cannot_disguise_current_module_code_as_legacy_seven(self):
        revision=fixtures.seed_runtime(self.repo);runtime.install(self.a,revision)
        directory=runtime.version_path(self.a,revision);m=runtime.load(directory/'manifest.json')
        for name in set(runtime.MODULES)-set(runtime.LEGACY_MODULES):
            (directory/'scripts'/name).unlink();del m['files']['scripts/'+name]
        raw=runtime.encoded(m);(directory/'manifest.json').write_bytes(raw)
        active=runtime.active(self.a);active['manifest_sha256']=runtime.digest(raw)
        runtime.pointer(self.a).write_bytes(runtime.encoded(active))
        result=subprocess.run([sys.executable,str(runtime.launcher_path(self.a)),'--home',str(self.a.home),'status'],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,1)
        self.assertIn('closure mismatch',result.stdout)
