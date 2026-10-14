import json
import os
import contextlib
import io
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

REPO = Path(os.environ.get("FRAMEWORK_REPO", str(Path(__file__).resolve().parents[1])))
sys.path.insert(0, str(REPO / "scripts"))
import codex_framework as cf
import framework_refresh as refresh
import workstation_entry as entry


class WorkstationEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.codex_home = self.root / ".codex"
        self.manifest = self.codex_home / "workstation/observation-sources.json"
        self.manifest.parent.mkdir(parents=True)
        self.state = self.codex_home / "workstation/example/state.json"
        self.state.parent.mkdir()
        self.run_dir = self.codex_home / "workstation/example/runs"
        self.run_dir.mkdir()
        self.framework = cf.Framework(REPO, self.root, self.codex_home)
        self.now = datetime.now(timezone.utc)
        self.source = {"id": "example-service", "path": str(self.state),
                       "status_path": "status", "time_path": "observed_at",
                       "max_age_seconds": 900, "ok_values": ["HEALTHY"],
                       "failure_values": ["STOPPED_ON_FAILURE"],
                       "identity": {"project_id": "example", "task_ref": "codex:thread:original",
                                    "version": "v1"}}

    def identified(self, **fields):
        return {"project_id": "example", "task_ref": "codex:thread:original",
                "node_id": "test-node", "version": "v1", **fields}

    def context(self, source="startup"):
        return refresh.session_start(self.framework, {
            "hook_event_name": "SessionStart", "source": source
        })["hookSpecificOutput"]["additionalContext"]

    def write_manifest(self):
        self.manifest.write_text(json.dumps({"schema": "workstation-entry-v1",
                                             "node_id": "test-node",
                                             "sources": self.source if isinstance(self.source, list)
                                             else [self.source]}))

    def test_real_shape_failure_is_in_startup_context_without_fault_prompt(self):
        self.source.pop("identity")  # Legacy failures stay visible without a healthy identity claim.
        self.source["follow"] = {"id_path": "last_run", "directory": str(self.run_dir),
                                 "status_path": "status", "time_path": "finished_at",
                                 "failure_values": ["FAIL_STOPPED"]}
        self.write_manifest()
        self.state.write_text(json.dumps({"status": "STOPPED_ON_FAILURE", "last_run": "run-1",
                                          "stopped_at": self.now.isoformat()}))
        (self.run_dir / "run-1.json").write_text(json.dumps({"status": "FAIL_STOPPED",
                                                               "finished_at": self.now.isoformat()}))
        for source in ("startup", "resume", "clear"):
            context = self.context(source)
            self.assertIn("FAIL_STOPPED", context)
            self.assertIn("异常", context)
            self.assertIn("原任务", context)
            self.assertLessEqual(len(context.encode()), 1800)

    def test_fresh_health_and_stale_health_are_distinct(self):
        self.write_manifest()
        self.state.write_text(json.dumps(self.identified(
            status="HEALTHY", observed_at=self.now.isoformat())))
        self.assertIn("正常", self.context())
        self.state.write_text(json.dumps(self.identified(
            status="HEALTHY", observed_at=(self.now - timedelta(hours=1)).isoformat())))
        context = self.context()
        self.assertIn("未知", context)
        self.assertNotIn("工作站观察: 正常", context)

    def test_missing_manifest_and_unreadable_source_are_unknown(self):
        self.assertIn("未知", self.context())
        self.assertEqual(entry.evaluate(self.codex_home)["files_read"], 0)
        self.write_manifest()
        self.state.write_text("{broken json")
        self.assertIn("未知", self.context())

    def test_unbound_or_wrong_version_healthy_is_unknown(self):
        self.write_manifest()
        self.state.write_text(json.dumps({"status": "HEALTHY", "observed_at": self.now.isoformat()}))
        self.assertEqual(entry.evaluate(self.codex_home)["status"], "unknown")
        self.state.write_text(json.dumps(self.identified(
            version="wrong", status="HEALTHY", observed_at=self.now.isoformat())))
        self.assertEqual(entry.evaluate(self.codex_home)["status"], "unknown")

    def test_outside_and_parent_symlink_sources_are_not_read(self):
        outside = self.root / "outside.json"
        outside.write_text(json.dumps(self.identified(
            status="HEALTHY", observed_at=self.now.isoformat())))
        self.source["path"] = str(outside)
        self.write_manifest()
        result = entry.evaluate(self.codex_home)
        self.assertEqual((result["status"], result["files_read"]), ("unknown", 1))
        link = self.codex_home / "workstation/linked"
        link.symlink_to(self.root, target_is_directory=True)
        self.source["path"] = str(link / outside.name)
        self.write_manifest()
        result = entry.evaluate(self.codex_home)
        self.assertEqual((result["status"], result["files_read"]), ("unknown", 1))

    def test_direct_entry_uses_exact_files_and_reports_budget(self):
        self.write_manifest()
        self.state.write_text(json.dumps(self.identified(
            status="HEALTHY", observed_at=self.now.isoformat())))
        with mock.patch.object(Path, "rglob", side_effect=AssertionError("directory walk")):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = cf.main(["--codex-home", str(self.codex_home), "workstation-entry"])
        result = json.loads(output.getvalue())
        self.assertEqual((code, result["status"]), (0, "normal"))
        self.assertEqual(result["files_read"], 2)
        self.assertEqual(result["read_attempts"], 2)
        self.assertLess(result["bytes_read"], 8192)

    def test_total_read_budget_fails_closed(self):
        self.source = []
        for index in range(8):
            path = self.state.parent / f"state-{index}.json"
            path.write_text(json.dumps(self.identified(
                status="HEALTHY", observed_at=self.now.isoformat(), padding="x" * 9000)))
            self.source.append({"id": f"source-{index}", "path": str(path),
                                "status_path": "status", "time_path": "observed_at",
                                "max_age_seconds": 900, "ok_values": ["HEALTHY"],
                                "identity": {"project_id": "example", "task_ref": "codex:thread:original",
                                             "version": "v1"}})
        self.write_manifest()
        result = entry.evaluate(self.codex_home)
        self.assertEqual(result["status"], "unknown")
        self.assertLessEqual(result["bytes_read"], entry.MAX_TOTAL_BYTES)
        self.assertLessEqual(result["read_attempts"], entry.MAX_READS)
        self.assertLess(result["files_read"], 9)

    def test_bad_follow_identifier_cannot_escape_directory(self):
        self.source["follow"] = {"id_path": "last_run", "directory": str(self.run_dir),
                                 "status_path": "status", "time_path": "finished_at",
                                 "failure_values": ["FAIL_STOPPED"]}
        self.write_manifest()
        self.state.write_text(json.dumps(self.identified(
            status="HEALTHY", observed_at=self.now.isoformat(), last_run="../elsewhere")))
        self.assertIn("未知", self.context())

    def test_observer_itself_fails_unknown_without_silencing_hook(self):
        import workstation_entry
        with mock.patch.object(workstation_entry, "summary", side_effect=OSError("ENOSPC")):
            context = self.context()
        self.assertIn("未知(观察入口失败)", context)
        self.assertIn("Codex 共享框架启动检查", context)

    def test_hook_carries_fixed_fallback_without_losing_failure(self):
        self.source.pop("identity")
        self.write_manifest()
        self.state.write_text(json.dumps({"status": "STOPPED_ON_FAILURE"}))
        context = self.context()
        self.assertIn("STOPPED_ON_FAILURE", context)
        self.assertIn("先报告具体状态、归属限制和可执行的原任务核对", context)
        self.assertIn("已收到摘要无需重复调用观察入口", context)
        self.assertIn("codex-framework workstation-entry", context)
        self.assertIn("codex-framework sync-status", context)
        self.assertIn("常规启动不解析同步器源码", context)
        self.assertIn("不递归枚举工作站", context)
        self.assertLessEqual(len(context.encode()), 1800)


if __name__ == "__main__":
    unittest.main()
