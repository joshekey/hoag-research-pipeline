"""Synthetic checks for historical lead uniqueness."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_emr_lead_uniqueness import summarize


class HistoricalUniquenessTests(unittest.TestCase):
    def test_unique_study_report_pair(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            report = root / "synthetic.txt"
            report.write_text(
                "DOE, JANE (MR # EMR111)\nDate of Service: 10/01/2026\n"
                "MRI CERVICAL SPINE\n"
            )
            sha = hashlib.sha256(report.read_bytes()).hexdigest()
            with sqlite3.connect(root / "workflow.sqlite") as db:
                db.executescript("""
                CREATE TABLE jobs(state TEXT);
                CREATE TABLE files(path TEXT, root TEXT, kind TEXT,
                    active INTEGER, status TEXT, metadata TEXT, id INTEGER);
                """)
                db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?)",(
                    "/synthetic/dicom",temporary,"dicom",0,"ok",
                    json.dumps({"StudyInstanceUID":"1.2.3","PatientName":"DOE^JANE",
                                "StudyDate":"20261001","Modality":"MR"}),1))
                db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?)",(
                    str(report),temporary,"report",0,"ok",
                    json.dumps({"hash":sha}),2))
            with patch("historical_emr_lead_uniqueness.engine.read_report",
                       return_value=(report.read_text(),sha)):
                result = summarize({"state_dir":temporary})
            self.assertEqual(result["strong_lead_studies"],1)
            self.assertEqual(result["strong_lead_pairs"],1)
            self.assertEqual(result["studies_with_one_report_candidate"],1)
            with sqlite3.connect(root / "workflow.sqlite") as db:
                self.assertEqual(db.execute("SELECT SUM(active) FROM files").fetchone()[0],0)


if __name__ == "__main__":
    unittest.main()
