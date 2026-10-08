"""Synthetic-only tests for read-only SQL GUI candidate API support."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pydicom.dataset import Dataset
from sql_gui_candidates import candidates, report_text

class SQLCandidateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        state = Path(self.tmp.name)
        indexdir = state / "sql-private"
        indexdir.mkdir(mode=0o700)
        index = indexdir / "reports-v2.sqlite"
        with sqlite3.connect(index) as db:
            db.execute("CREATE TABLE narratives (report_id BLOB PRIMARY KEY, text BLOB, procedure_name BLOB, result_date BLOB)")
            good = b"PATIENT NAME: SAMPLE, PERSON MRN: unrelated DATE OF BIRTH: 2/3/1980 FINDINGS: synthetic"
            wrong = b"PATIENT NAME: DIFFERENT, PERSON MRN: unrelated DATE OF BIRTH: 2/3/1980"
            db.executemany("INSERT INTO narratives VALUES (?,?,?,?)", [
                (b"1", good, b"MRI LUMBAR SPINE", b"2021-09-02"),
                (b"2", wrong, b"MRI LUMBAR SPINE", b"2021-09-02")
            ])
        index.chmod(0o600)
        self.uid = "1.2.3.4"
        with sqlite3.connect(state / "workflow.sqlite") as db:
            db.executescript("""
              CREATE TABLE settings (key TEXT, value TEXT);
              INSERT INTO settings VALUES ('scan_complete','1');
              CREATE TABLE studies (uid TEXT, date TEXT, modality TEXT, name TEXT);
              CREATE TABLE files (path TEXT,metadata TEXT,kind TEXT,active INTEGER,status TEXT);
            """)
            db.execute("INSERT INTO studies VALUES (?,?,?,?)",
                       (self.uid, "20210902", "MR", "SAMPLE^PERSON"))
            db.execute("INSERT INTO files VALUES (?,?,?,?,?)",
                       ("/synthetic/image.dcm", json.dumps({
                           "StudyInstanceUID": self.uid, "PatientName": "SAMPLE^PERSON",
                           "PatientID": "FAKE", "PatientBirthDate": "19800203"
                       }), "dicom", 1, "ok"))
        self.config = {"state_dir": str(state), "secret_key": "synthetic-key"}
        self.ds = Dataset()
        self.ds.PatientName = "SAMPLE^PERSON"
        self.ds.PatientBirthDate = "19800203"

    def test_candidate_scoped_and_preview(self):
        with patch("sql_gui_candidates.engine.source_path", return_value=Path("/synthetic/image.dcm")),              patch("sql_gui_candidates.pydicom.dcmread", return_value=self.ds):
            rows, total = candidates(self.config, self.uid)
            self.assertEqual(total, 1)
            self.assertTrue(rows[0]["name_dob"])
            self.assertFalse(rows[0]["exam_date_verified"])
            self.assertTrue(rows[0]["result_date_matches"])
            self.assertIn("FINDINGS:", report_text(self.config, self.uid, rows[0]["token"]))
            with self.assertRaises(LookupError):
                report_text(self.config, self.uid, "0" * 32)

    def test_incomplete_catalog_blocked(self):
        with sqlite3.connect(Path(self.config["state_dir"]) / "workflow.sqlite") as db:
            db.execute("UPDATE settings SET value='0'")
        with self.assertRaises(ValueError):
            candidates(self.config, self.uid)

if __name__ == "__main__":
    unittest.main()
