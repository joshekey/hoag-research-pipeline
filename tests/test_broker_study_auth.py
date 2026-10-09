"""Synthetic authorization checks for root SQL broker."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from pydicom.dataset import Dataset
import broker_study_auth

class StudyAuthorizationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.uid="1.2.3"
        self.file=self.root / "image.dcm"
        self.file.write_bytes(b"synthetic")
        with sqlite3.connect(self.root/"workflow.sqlite") as db:
            db.executescript("""
                CREATE TABLE settings (key TEXT,value TEXT);
                INSERT INTO settings VALUES ('scan_complete','1');
                CREATE TABLE jobs (state TEXT);
                CREATE TABLE studies (uid TEXT,name TEXT,date TEXT,modality TEXT,count INTEGER);
                CREATE TABLE files (path TEXT,metadata TEXT,kind TEXT,active INTEGER,status TEXT);
            """)
            db.execute("INSERT INTO studies VALUES (?,?,?,?,?)",(self.uid,"TEST^SAMPLE","20210902","MR",1))
            db.execute("INSERT INTO files VALUES (?,?,?,?,?)",(str(self.file),json.dumps({
                "StudyInstanceUID":self.uid,"PatientName":"TEST^SAMPLE","PatientID":"SYN001",
                "IssuerOfPatientID":"SYN"}),"dicom",1,"ok"))
        self.config={"state_dir":str(self.root)}
        self.ds=Dataset()
        self.ds.PatientName="TEST^SAMPLE"
        self.ds.PatientBirthDate="19800203"
        self.ds.StudyInstanceUID=self.uid
        self.ds.StudyDate="20210902"

    def checked(self):
        with patch("broker_study_auth.engine.source_path",return_value=self.file), \
             patch("broker_study_auth.pydicom.dcmread",return_value=self.ds):
            return broker_study_auth.authorized_study(self.uid,self.config)

    def test_valid_catalog(self):
        self.assertEqual(self.checked()["dob"],"19800203")

    def test_incomplete_scan_rejected(self):
        with sqlite3.connect(self.root/"workflow.sqlite") as db:
            db.execute("UPDATE settings SET value='0'")
        with self.assertRaises(ValueError):
            self.checked()

    def test_active_job_rejected(self):
        with sqlite3.connect(self.root/"workflow.sqlite") as db:
            db.execute("INSERT INTO jobs VALUES ('running')")
        with self.assertRaises(ValueError):
            self.checked()

    def test_header_identity_conflict_rejected(self):
        self.ds.PatientName="OTHER^PATIENT"
        with self.assertRaises(ValueError):
            self.checked()

    def test_header_date_conflict_rejected(self):
        self.ds.StudyDate="20210903"
        with self.assertRaises(ValueError):
            self.checked()

    def test_missing_instances_rejected(self):
        with sqlite3.connect(self.root/"workflow.sqlite") as db:
            db.execute("UPDATE studies SET count=2")
        with self.assertRaises(ValueError):
            self.checked()

if __name__=="__main__":
    unittest.main()
