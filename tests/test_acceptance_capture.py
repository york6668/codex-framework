import hashlib
from pathlib import Path
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import acceptance_capture as capture


class AcceptanceCaptureTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.events = self.root / "events.jsonl"
        self.diagnostics = self.root / "diagnostics.log"

    def run_fixture(self, source, *, stdout_limit=128 * 1024, stderr_limit=64 * 1024):
        return capture.run_bounded([sys.executable, "-c", source], cwd=self.root,
                                   stdout_path=self.events, stderr_path=self.diagnostics,
                                   timeout_seconds=5, stdout_limit=stdout_limit,
                                   stderr_limit=stderr_limit)

    def test_verbose_diagnostics_do_not_exhaust_json_event_budget(self):
        info = b"INFO cli diagnostic without task event\n" * 5000
        source = ("import os\n"
                  "os.write(2, b'INFO cli diagnostic without task event\\n' * 5000)\n"
                  "os.write(1, b'{\"type\":\"turn.completed\"}\\n')\n")
        result = self.run_fixture(source)
        self.assertEqual(result["exit_code"], 0)
        self.assertIsNone(result["termination_reason"])
        self.assertEqual(self.events.read_bytes(), b'{"type":"turn.completed"}\n')
        self.assertEqual(result["stderr_bytes_seen"], len(info))
        self.assertEqual(result["stderr_sha256"], hashlib.sha256(info).hexdigest())
        self.assertEqual(result["stderr_bytes_saved"], 64 * 1024)
        self.assertTrue(result["stderr_truncated"])
        saved = self.diagnostics.read_bytes()
        self.assertEqual(saved[:32768], info[:32768])
        self.assertEqual(saved[-32768:], info[-32768:])
        self.assertEqual(self.events.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.diagnostics.stat().st_mode & 0o777, 0o600)

    def test_oversized_json_stream_fails_and_keeps_bounded_evidence(self):
        source = ("import os,time\n"
                  "os.write(1, b'x' * 3000)\n"
                  "os.write(2, b'WARN before protocol overflow\\n')\n"
                  "time.sleep(1)\n")
        result = self.run_fixture(source, stdout_limit=1024, stderr_limit=64)
        self.assertEqual(result["termination_reason"], "stdout_limit")
        self.assertNotEqual(result["exit_code"], 0)
        self.assertEqual(result["stdout_bytes_saved"], 1024)
        self.assertEqual(result["stdout_bytes_seen"], 3000)
        self.assertLessEqual(result["stderr_bytes_saved"], 64)

    def test_rejects_shared_output_path_before_start(self):
        with self.assertRaises(ValueError):
            capture.run_bounded([sys.executable, "-c", "pass"], cwd=self.root,
                                stdout_path=self.events, stderr_path=self.events)
        self.assertFalse(self.events.exists())


if __name__ == "__main__":
    unittest.main()
