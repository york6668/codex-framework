"""Original unresolved footprints survive input amendments; synthetic files only."""
import copy
from unittest import mock

import test_framework as fixtures
import test_task_continuity as continuity
import task_runtime as task
import task_coordination as coordination


class ContinuationResourceTests(fixtures.FixtureCase):
    def fixture(self, **options):
        original = continuity.ContinuityTests()
        original.setUp()
        self.addCleanup(original.tearDown)
        self.addCleanup(original.doCleanups)
        store, value = original.create("origin", **options)
        return original, store, value

    def file_check(self, fixture, name):
        return {"kind": "file", **fixture.ref(name, b"synthetic bytes")}

    def logical(self, object):
        return {"kind": "logical", "identity": "synthetic-database", "object": object,
                "mode": "write", "operation": "update", "source_ref": "synthetic:registered-resource"}

    def peer(self, fixture, identity, check=None, resources=None):
        spec = {"request_id": "request-" + identity, "requirement": "Synthetic reservation only",
                "acceptance_version": "v1", "workspace": str(fixture.root), "known_tasks": ["origin"],
                "steps": [{"id": "a", "instruction": "Do not execute a worker",
                           "checks": [check or {"kind": "message", "equals": "42"}],
                           "resources": resources or []}]}
        store = task.Store(fixture.a.codex_home, identity)
        return store, store.create(identity, spec)

    def own_resources(self, store):
        return [r for e in coordination.read_index(store.root) if e["task_id"] == "origin" for r in e["resources"]]

    def assert_blocked(self, peer):
        value, execution = task.reserve(peer, peer.read()["revision"])
        self.assertFalse(execution)
        self.assertEqual(value["steps"]["a"]["state"], "blocked")

    def assert_sync_preserves_record(self, store):
        before = store.path.read_bytes()
        for _ in range(2):
            report = store.sync_effects(store.read()["revision"])
            self.assertFalse(report["index_sync_pending"])
            self.assertFalse(report["task_record_changed"])
            self.assertEqual(store.path.read_bytes(), before)

    def test_unknown_continuation_repeated_file_replacement_retains_all_scopes(self):
        fixture, store, value = self.fixture()
        old = value["spec"]["steps"][0]["checks"][0]
        fixture.checkpoint(store, [{"kind": "commit", "path": old["path"], "sha256": old["sha256"]}])
        peer, _ = self.peer(fixture, "peer-original", old)
        self.assert_blocked(peer)
        for name in ("replacement-b.txt", "replacement-c.txt"):
            amended = copy.deepcopy(store.read()["spec"])
            amended["steps"][0]["checks"] = [self.file_check(fixture, name)]
            fixture.change(store, "amend", spec=amended)
            self.assert_sync_preserves_record(store)
            self.assert_blocked(peer)
        current = store.read()
        self.assertEqual(current["steps"]["a"]["continuation"]["terminal"], "unknown")
        self.assertFalse(current["steps"]["a"]["continuation"]["resolved"])
        self.assertEqual({r["identity"] for r in self.own_resources(store)},
                         {str(fixture.root / name) for name in (old["path"], "replacement-b.txt", "replacement-c.txt")})
        self.assertEqual(fixture.model.call_count + fixture.native.call_count, 0)

    def test_unknown_continuation_file_scope_shrink_retains_removed_target(self):
        fixture, store, value = self.fixture()
        original = value["spec"]["steps"][0]["checks"][0]
        second = self.file_check(fixture, "second.txt")
        spec = copy.deepcopy(value["spec"])
        spec["steps"][0]["checks"].append(second)
        fixture.change(store, "amend", spec=spec)
        fixture.checkpoint(store)
        spec["steps"][0]["checks"] = [second]
        fixture.change(store, "amend", spec=spec)
        self.assert_sync_preserves_record(store)
        peer, _ = self.peer(fixture, "peer-removed-target", original)
        self.assert_blocked(peer)
        self.assertEqual({r["identity"] for r in self.own_resources(store)},
                         {str(fixture.root / original["path"]), str(fixture.root / second["path"])})

    def test_resource_replace_shrink_clear_and_mode_change_preserve_original_claim(self):
        for edit in ("replace", "shrink", "clear", "mode"):
            with self.subTest(edit=edit):
                fixture, store, value = self.fixture()
                spec = copy.deepcopy(value["spec"])
                spec["steps"][0]["resources"] = [self.logical("schema/a"), self.logical("schema/b")]
                fixture.change(store, "amend", spec=spec)
                fixture.checkpoint(store)
                replacement = copy.deepcopy(spec)
                if edit == "replace":replacement["steps"][0]["resources"] = [self.logical("schema/c")]
                elif edit == "shrink":replacement["steps"][0]["resources"] = [self.logical("schema/b")]
                elif edit == "clear":replacement["steps"][0]["resources"] = []
                else:replacement["steps"][0]["resources"][0]["mode"] = "read"
                fixture.change(store, "amend", spec=replacement)
                self.assert_sync_preserves_record(store)
                for object in ("schema/a", "schema/b"):
                    peer, _ = self.peer(fixture, "peer-" + object[-1], resources=[self.logical(object)])
                    self.assert_blocked(peer)
                self.assertTrue(all(any(r["kind"] == "logical" and r["object"] == object and r["mode"] == "write"
                                        for r in self.own_resources(store)) for object in ("schema/a", "schema/b")))

    def test_clearing_all_current_effects_and_rebasing_workspace_keeps_old_file(self):
        fixture, store, value = self.fixture()
        original = value["spec"]["steps"][0]["checks"][0]
        fixture.checkpoint(store)
        replacement = copy.deepcopy(value["spec"])
        replacement["execution_mode"] = "native"
        replacement["steps"][0]["checks"] = [{"kind": "message", "equals": "42"}]
        replacement["steps"][0]["resources"] = []
        other = fixture.root / "new-workspace"
        other.mkdir()
        replacement["workspace"] = str(other)
        fixture.change(store, "amend", spec=replacement)
        self.assert_sync_preserves_record(store)
        peer, _ = self.peer(fixture, "peer-old-workspace", original)
        self.assert_blocked(peer)
        self.assertIn(str(fixture.root / original["path"]), {r["identity"] for r in self.own_resources(store)})

    def test_same_scope_revisions_deduplicate_and_other_origins_do_not_leak(self):
        fixture, store, value = self.fixture()
        old_spec = copy.deepcopy(value["spec"])
        old_spec["steps"][0]["checks"] = [self.file_check(fixture, "unrelated-history.txt")]
        fixture.change(store, "amend", spec=old_spec)
        fixture.change(store, "amend", spec=value["spec"])
        fixture.checkpoint(store)
        for _ in range(3):
            fixture.change(store, "amend", spec=store.read()["spec"])
        self.assert_sync_preserves_record(store)
        self.assertEqual(len(self.own_resources(store)), 1)
        self.assertNotIn(str(fixture.root / "unrelated-history.txt"), {r["identity"] for r in self.own_resources(store)})

    def test_historical_scope_requires_the_exact_original_native_mapping(self):
        fixture, store, value = self.fixture()
        fixture.checkpoint(store)
        old = copy.deepcopy(store.read())
        old["spec"]["steps"][0]["checks"] = [self.file_check(fixture, "other-origin.txt")]
        old["steps"]["a"]["continuation"]["origin"]["turn_id"] = "another-original-turn"
        store.update(store.read()["revision"], lambda current: current["history"].append({"superseded_input": old}))
        self.assert_sync_preserves_record(store)
        self.assertEqual({r["identity"] for r in self.own_resources(store)},
                         {str(fixture.root / value["spec"]["steps"][0]["checks"][0]["path"])})

    def test_missing_original_scope_is_reported_without_rewriting_the_index(self):
        fixture, store, _ = self.fixture()
        fixture.checkpoint(store)
        value = store.read()
        old = copy.deepcopy(value)
        old["spec"].pop("workspace")
        value["history"].append({"superseded_input": old})
        store.save(value)  # Construct an incomplete legacy fact only in this fixture.
        before = store.path.read_bytes()
        index = coordination.index_path(store.root).read_bytes()
        with self.assertRaises(task.TaskError):store.sync_effects(value["revision"])
        self.assertEqual(store.path.read_bytes(), before)
        self.assertEqual(coordination.index_path(store.root).read_bytes(), index)

    def test_partial_amend_keeps_original_and_recovers_union_from_durable_history(self):
        fixture, store, value = self.fixture()
        original = value["spec"]["steps"][0]["checks"][0]
        fixture.checkpoint(store)
        replacement = copy.deepcopy(value["spec"])
        replacement["steps"][0]["checks"] = [self.file_check(fixture, "new-target.txt")]
        original_write = task.cf.write_file
        def fail_index(path, raw):
            if path == coordination.index_path(store.root):raise OSError("synthetic amendment index failure")
            return original_write(path, raw)
        with mock.patch.object(task.cf, "write_file", side_effect=fail_index):
            with self.assertRaises(task.TaskCommitError) as caught:
                fixture.change(store, "amend", spec=replacement)
        self.assertEqual(caught.exception.commit_report["committed_revision"], store.read()["revision"])
        self.assertEqual({r["identity"] for r in self.own_resources(store)}, {str(fixture.root / original["path"])})
        self.assert_sync_preserves_record(store)
        self.assertEqual({r["identity"] for r in self.own_resources(store)},
                         {str(fixture.root / original["path"]), str(fixture.root / "new-target.txt")})

    def test_completed_terminal_alone_retains_scope_and_review_allows_safe_release(self):
        fixture, store, value = self.fixture()
        original = value["spec"]["steps"][0]["checks"][0]
        fixture.finite(store)
        fixture.checkpoint(store, [{"kind": "commit", "path": original["path"], "sha256": original["sha256"]}])
        replacement = copy.deepcopy(value["spec"])
        replacement["steps"][0]["checks"] = [self.file_check(fixture, "after-original.txt")]
        fixture.change(store, "amend", spec=replacement)
        fixture.change(store, "continue", step="a", terminal_ref=fixture.terminal())
        self.assertFalse(store.read()["steps"]["a"]["continuation"]["resolved"])
        peer, _ = self.peer(fixture, "peer-original", original)
        self.assert_blocked(peer)
        result = fixture.result(store, origins=[fixture.origin])
        fixture.change(store, "collect-result", **result)
        review = fixture.review(store, result)
        original_write = task.cf.write_file
        def fail_index(path, raw):
            if path == coordination.index_path(store.root):raise OSError("synthetic reviewed-scope release failure")
            return original_write(path, raw)
        with mock.patch.object(task.cf, "write_file", side_effect=fail_index):
            with self.assertRaises(task.TaskCommitError):fixture.change(store, "review-result", **review)
        self.assertTrue(store.read()["steps"]["a"]["continuation"]["resolved"])
        self.assert_sync_preserves_record(store)
        self.assertEqual(self.own_resources(store), [])
        self.assertTrue(task.reserve(peer, peer.read()["revision"])[1])
        # A later input with no unresolved original can freely change its scope.
        replacement["steps"][0]["checks"] = [self.file_check(fixture, "legal-next.txt")]
        fixture.change(store, "amend", spec=replacement)
        self.assertEqual(self.own_resources(store), [])
        self.assertEqual(fixture.model.call_count + fixture.native.call_count, 0)

    def test_idle_scope_amend_does_not_create_a_historical_reservation(self):
        fixture, store, value = self.fixture()
        original = value["spec"]["steps"][0]["checks"][0]
        replacement = copy.deepcopy(value["spec"])
        replacement["steps"][0]["checks"] = [self.file_check(fixture, "idle-new.txt")]
        fixture.change(store, "amend", spec=replacement)
        self.assert_sync_preserves_record(store)
        peer, _ = self.peer(fixture, "peer-idle", original)
        self.assertTrue(task.reserve(peer, peer.read()["revision"])[1])
