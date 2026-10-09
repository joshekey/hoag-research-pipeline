"""Entirely synthetic TXT matching tests; never access hospital records."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from txt_candidate_discovery import clues, evaluate, discover


class TXTDiscoveryTests(unittest.TestCase):
    def study(self, **update):
        obj={"uid":"1.2.3.4","accession":"A10045","patient":"MR123",
             "name":"SMITH^JANE","date":"20261001","modality":"CT"}
        obj.update(update)
        return obj

    def test_exact_accession_name_dob_and_exam_date(self):
        r=clues("Accession: A10045\nPatient Name: Jane Smith\nMRN: MR123\n"
                "DOB: 01/02/1980\nExam Date: 10/01/2026\nCT LUMBAR SPINE")
        a=evaluate(self.study(),r,study_dob="19800102",study_anatomy=["LUMBAR"])
        self.assertEqual(a["confidence"],"strong")
        self.assertTrue(a["requires_manual_confirmation"])
        self.assertIn("accession",a["evidence"])

    def test_name_alone_does_not_create_candidate(self):
        r=clues("Patient Name: Jane Smith\nHistory of MRI imaging")
        a=evaluate(self.study(),r)
        self.assertEqual(a["confidence"],"insufficient")

    def test_conflicting_accession_rejects_even_if_name_matches(self):
        r=clues("Accession: WRONG23\nPatient Name: Jane Smith\nExam Date: 20261001")
        a=evaluate(self.study(),r)
        self.assertEqual(a["confidence"],"insufficient")
        self.assertIn("accession",a["conflicts"])

    def test_result_date_not_treated_as_exam_date(self):
        r=clues("Patient Name: Jane Smith\nResult Date: 20261001")
        a=evaluate(self.study(),r)
        self.assertNotIn("exam_date",a["evidence"])
        self.assertEqual(a["confidence"],"insufficient")

    def test_historical_reports_require_explicit_opt_in_and_never_reactivate(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            database=state/"workflow.sqlite"
            report=state/"synthetic-historical.txt"
            report.write_text("Accession: A10045\\nPatient Name: Jane Smith\\nExam Date: 10/01/2026")
            import hashlib
            sha=hashlib.sha256(report.read_bytes()).hexdigest()
            with sqlite3.connect(database) as db:
                db.executescript("""CREATE TABLE settings(key TEXT,value TEXT);
                CREATE TABLE jobs(state TEXT);
                CREATE TABLE studies(uid TEXT,accession TEXT,patient TEXT,name TEXT,
                  date TEXT,modality TEXT,state TEXT,report_id INTEGER,fingerprint TEXT);
                CREATE TABLE files(id INTEGER,path TEXT,root TEXT,kind TEXT,
                  size INTEGER,mtime INTEGER,metadata TEXT,status TEXT,active INTEGER);""")
                db.execute("INSERT INTO settings VALUES('scan_complete','1')")
                db.execute("INSERT INTO studies VALUES(?,?,?,?,?,?,?,?,?)",
                           ("1.2.3.4","A10045","MR123","SMITH^JANE","20261001",
                            "CT","unmatched",None,"fp"))
                db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)",
                           (2,str(report),str(state),"report",report.stat().st_size,
                            report.stat().st_mtime_ns,json.dumps({"hash":sha}),"ok",0))
            with patch("txt_candidate_discovery.engine.read_report",
                       return_value=(report.read_text(),sha)):
                normal=discover({"state_dir":tmp})
                historical=discover({"state_dir":tmp},include_verified_inactive=True)
            self.assertEqual(normal["summary"]["reports_indexed"],0)
            self.assertEqual(historical["summary"]["historical_reports_verified"],1)
            self.assertEqual(historical["summary"]["studies_with_candidates"],1)
            self.assertTrue(historical["candidates"]["1.2.3.4"][0]["historical_inactive"])
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute("SELECT active FROM files WHERE id=2").fetchone()[0],0)
                self.assertEqual(db.execute("SELECT state FROM studies").fetchone()[0],"unmatched")

    def test_historical_changed_hash_never_matches(self):
        with tempfile.TemporaryDirectory() as tmp:
            p=Path(tmp)/"workflow.sqlite"
            with sqlite3.connect(p) as db:
                db.executescript("""CREATE TABLE settings(key TEXT,value TEXT);
                CREATE TABLE jobs(state TEXT);
                CREATE TABLE studies(uid TEXT,accession TEXT,patient TEXT,name TEXT,
                  date TEXT,modality TEXT,state TEXT,report_id INTEGER,fingerprint TEXT);
                CREATE TABLE files(id INTEGER,path TEXT,root TEXT,kind TEXT,
                  size INTEGER,mtime INTEGER,metadata TEXT,status TEXT,active INTEGER);""")
                db.execute("INSERT INTO settings VALUES('scan_complete','1')")
                db.execute("INSERT INTO studies VALUES(?,?,?,?,?,?,?,?,?)",
                           ("1.2.3.4","A10045","MR123","SMITH^JANE","20261001",
                            "CT","unmatched",None,"fp"))
                db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)",
                           (2,"/synthetic/report.txt",tmp,"report",12,1,
                            json.dumps({"hash":"expected"}),"ok",0))
            with patch("txt_candidate_discovery.engine.read_report",
                       return_value=("Accession: A10045","changed")):
                result=discover({"state_dir":tmp},include_verified_inactive=True)
            self.assertEqual(result["summary"]["reports_changed"],1)
            self.assertEqual(result["summary"]["studies_without_candidates"],1)

    def test_read_only_scan_uses_existing_reports(self):
        with tempfile.TemporaryDirectory() as tmp:
            state=Path(tmp)
            database=state/"workflow.sqlite"
            report=state/"synthetic.txt"
            report.write_text("Accession: A10045\nPatient Name: Jane Smith\nExam Date: 10/01/2026")
            import hashlib
            sha=hashlib.sha256(report.read_bytes()).hexdigest()
            with sqlite3.connect(database) as db:
                db.executescript("""CREATE TABLE settings(key TEXT,value TEXT);
                CREATE TABLE jobs(state TEXT);
                CREATE TABLE studies(uid TEXT,accession TEXT,patient TEXT,name TEXT,
                  date TEXT,modality TEXT,state TEXT,report_id INTEGER,fingerprint TEXT);
                CREATE TABLE files(id INTEGER,path TEXT,root TEXT,kind TEXT,
                  size INTEGER,mtime INTEGER,metadata TEXT,status TEXT,active INTEGER);""")
                db.execute("INSERT INTO settings VALUES('scan_complete','1')")
                db.execute("INSERT INTO studies VALUES(?,?,?,?,?,?,?,?,?)",
                           ("1.2.3.4","A10045","MR123","SMITH^JANE","20261001",
                            "CT","unmatched",None,"fp"))
                db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?,?,?)",
                           (1,str(report),str(state),"report",report.stat().st_size,
                            report.stat().st_mtime_ns,json.dumps({"hash":sha}),"ok",1))
            def synthetic_read(_config,row):
                return report.read_text(),sha
            with patch("txt_candidate_discovery.engine.read_report",side_effect=synthetic_read):
                summary=discover({"state_dir":tmp})
            self.assertEqual(summary["summary"]["studies_with_candidates"],1)
            self.assertEqual(summary["summary"]["reports_scanned"],1)
            self.assertEqual(summary["candidates"]["1.2.3.4"][0]["report_id"],1)
            with sqlite3.connect(database) as db:
                self.assertEqual(db.execute("SELECT state FROM studies").fetchone()[0],"unmatched")


if __name__=="__main__":
    unittest.main()
