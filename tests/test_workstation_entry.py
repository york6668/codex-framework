import json
import os
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


class WorkstationEntryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(tempfile.gettempdir()).resolve())
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.codex_home = self.root / ".codex"
        self.manifest = self.codex_home / "workstation/observation-sources.json"
        self.manifest.parent.mkdir(parents=True)
        self.state = self.root / "state.json"
        self.run_dir = self.root / "runs"
        self.run_dir.mkdir()
        self.framework = cf.Framework(REPO, self.root, self.codex_home)
        self.now = datetime.now(timezone.utc)
        self.source = {"id": "example-service", "path": str(self.state),
                       "status_path": "status", "time_path": "observed_at",
                       "max_age_seconds": 900, "ok_values": ["HEALTHY"],
                       "failure_values": ["STOPPED_ON_FAILURE"]}

    def context(self, source="startup"):
        return refresh.session_start(self.framework, {
            "hook_event_name": "SessionStart", "source": source
        })["hookSpecificOutput"]["additionalContext"]

    def write_manifest(self):
        self.manifest.write_text(json.dumps({"schema": "workstation-entry-v1",
                                             "node_id": "test-node", "sources": [self.source]}))

    def test_real_shape_failure_is_in_startup_context_without_fault_prompt(self):
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
        self.state.write_text(json.dumps({"status": "HEALTHY", "observed_at": self.now.isoformat()}))
        self.assertIn("正常", self.context())
        self.state.write_text(json.dumps({"status": "HEALTHY",
                                          "observed_at": (self.now - timedelta(hours=1)).isoformat()}))
        context = self.context()
        self.assertIn("未知", context)
        self.assertNotIn("工作站观察: 正常", context)

    def test_missing_manifest_and_unreadable_source_are_unknown(self):
        self.assertIn("未知", self.context())
        self.write_manifest()
        self.state.write_text("{broken json")
        self.assertIn("未知", self.context())

    def test_bad_follow_identifier_cannot_escape_directory(self):
        self.source["follow"] = {"id_path": "last_run", "directory": str(self.run_dir),
                                 "status_path": "status", "time_path": "finished_at",
                                 "failure_values": ["FAIL_STOPPED"]}
        self.write_manifest()
        self.state.write_text(json.dumps({"status": "HEALTHY", "observed_at": self.now.isoformat(),
                                          "last_run": "../elsewhere"}))
        self.assertIn("未知", self.context())

    def test_observer_itself_fails_unknown_without_silencing_hook(self):
        import workstation_entry
        with mock.patch.object(workstation_entry, "summary", side_effect=OSError("ENOSPC")):
            context = self.context()
        self.assertIn("未知(观察入口失败)", context)
        self.assertIn("Codex 共享框架启动检查", context)


if __name__ == "__main__":
    unittest.main()
