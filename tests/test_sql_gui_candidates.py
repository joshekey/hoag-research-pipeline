"""Synthetic tests for dashboard-to-broker integration; no hospital records."""
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
                           "PatientID": "FAKE", "IssuerOfPatientID": "TEST"
                       }), "dicom", 1, "ok"))
        self.config = {"state_dir": str(state)}
        self.ds = Dataset()
        self.ds.PatientName = "SAMPLE^PERSON"
        self.ds.PatientBirthDate = "19800203"

    def test_candidate_scoped_and_preview(self):
        def broker(body):
            self.assertEqual(body["uid"], self.uid)
            self.assertEqual(set(body), {"uid", "action"} if body["action"] == "list" else {"uid", "action", "token"})
            if body["action"] == "list":
                return {"candidates":[{"token":"abc", "name_dob":True,
                                       "exam_date_verified":False,
                                       "result_date_matches":True}], "total":1}
            if body["token"] == "abc":
                return {"text":"FINDINGS: synthetic"}
            raise ValueError("Candidate unavailable")
        with patch("sql_gui_candidates.engine.source_path", return_value=Path("/synthetic/image.dcm")), \
             patch("sql_gui_candidates.pydicom.dcmread", return_value=self.ds), \
             patch("sql_gui_candidates.request_broker", side_effect=broker):
            rows, total = candidates(self.config, self.uid)
            self.assertEqual(total, 1)
            self.assertTrue(rows[0]["name_dob"])
            self.assertIn("FINDINGS:", report_text(self.config, self.uid, "abc"))
            with self.assertRaises(ValueError):
                report_text(self.config, self.uid, "not-a-token")

    def test_incomplete_catalog_blocked(self):
        with sqlite3.connect(Path(self.config["state_dir"]) / "workflow.sqlite") as db:
            db.execute("UPDATE settings SET value='0'")
        with self.assertRaises(ValueError):
            candidates(self.config, self.uid)

if __name__ == "__main__":
    unittest.main()
