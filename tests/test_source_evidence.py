import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from source_evidence import verify


class SourceEvidenceChecks(unittest.TestCase):
    def test_node_file_is_read_at_current_path_and_cannot_escape_project(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            report = root / "report.json"
            report.write_text(json.dumps({"result": "passed", "version": "commit:one"}))
            record = {"source": "node", "node_path": "report.json", "result": "passed",
                      "version": "commit:one", "digest": hashlib.sha256(report.read_bytes()).hexdigest()}
            self.assertTrue(verify({}, {"root": directory}, record))
            report.write_text(json.dumps({"result": "failed", "version": "commit:one"}))
            self.assertFalse(verify({}, {"root": directory}, record))
            record["digest"] = hashlib.sha256(report.read_bytes()).hexdigest()
            self.assertFalse(verify({}, {"root": directory}, record))
            record["node_path"] = "../report.json"
            self.assertFalse(verify({}, {"root": directory}, record))


if __name__ == "__main__":
    unittest.main()
