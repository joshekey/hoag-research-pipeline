"""Synthetic SQL draft integration. No clinical records or external services."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import sql_prepare_review

UID = "1.2.3"
ORIGINAL = "<r><notes>MRI LUMBAR SPINE</notes><notes>FINDINGS:</notes><notes>Synthetic MRN 98765.</notes><notes>CONCLUSION:</notes><notes>Normal.</notes></r>"
SOURCE_SHA = hashlib.sha256(ORIGINAL.encode("utf-8")).hexdigest()

class SQLDraftTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state = Path(self.tmp.name)
        self.config = {"state_dir":str(self.state), "max_report_bytes":100000}
        with sqlite3.connect(self.state/"workflow.sqlite") as db:
            db.executescript("""
                CREATE TABLE studies(uid TEXT PRIMARY KEY,fingerprint TEXT,state TEXT);
                CREATE TABLE settings(key TEXT,value TEXT);
                CREATE TABLE jobs(state TEXT);
                CREATE TABLE files(metadata TEXT,kind TEXT,active INTEGER,status TEXT);
                CREATE TABLE audit(id INTEGER PRIMARY KEY,at REAL,actor TEXT,action TEXT,target TEXT);
                CREATE TABLE sql_associations(uid TEXT PRIMARY KEY,token TEXT,report_sha256 TEXT,
                    study_fingerprint TEXT,date_verified INTEGER,conflicts_acknowledged INTEGER);
                INSERT INTO studies VALUES('1.2.3','fingerprint-one','unmatched');
                INSERT INTO settings VALUES('scan_complete','1');
            """)
            db.execute("INSERT INTO files VALUES (?,?,?,?)",
                       (json.dumps({"StudyInstanceUID":UID, "PatientName":"SYNTHETIC^TEST",
                                    "PatientID":"TEST000"}), "dicom", 1, "ok"))
            db.execute("INSERT INTO sql_associations VALUES(?,?,?,?,?,?)",
                       (UID,"a"*32,SOURCE_SHA,"fingerprint-one",1,1))
        p=patch.object(sql_prepare_review.sql_gui_candidates,"report_text",return_value=ORIGINAL)
        p.start()
        self.addCleanup(p.stop)
        # Keep NLP deterministic for this synthetic unit test.
        self.nlp = type("NLP",(),{"analyze":lambda self,**kwargs: []})()

    def test_draft_prepares_without_approving_study(self):
        result=sql_prepare_review.prepare_draft(self.config,UID,nlp=self.nlp)
        self.assertTrue(result["prepared"])
        self.assertFalse(result["study_approved"])
        self.assertFalse(result["export_authorized"])
        self.assertIn("FINDINGS", result["draft"])
        self.assertIn("Normal.", result["draft"])
        visible=sql_prepare_review.read_draft(self.config, UID)
        self.assertTrue(visible["prepared"])
        with sqlite3.connect(self.state/"workflow.sqlite") as db:
            self.assertEqual(db.execute("SELECT state FROM studies").fetchone()[0],"unmatched")
            self.assertEqual(db.execute("SELECT count(*) FROM sql_review_drafts").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM audit").fetchone()[0],1)

    def test_fingerprint_changed_rejected(self):
        with sqlite3.connect(self.state/"workflow.sqlite") as db:
            db.execute("UPDATE studies SET fingerprint='changed'")
        with self.assertRaises(ValueError):
            sql_prepare_review.prepare_draft(self.config,UID,nlp=self.nlp)

    def test_report_content_changed_rejected(self):
        with patch.object(sql_prepare_review.sql_gui_candidates,"report_text",return_value="changed"):
            with self.assertRaises(ValueError):
                sql_prepare_review.prepare_draft(self.config,UID,nlp=self.nlp)

    def test_draft_rejected_without_verification(self):
        with sqlite3.connect(self.state/"workflow.sqlite") as db:
            db.execute("UPDATE sql_associations SET date_verified=0")
        with self.assertRaises(ValueError):
            sql_prepare_review.prepare_draft(self.config,UID,nlp=self.nlp)

    def test_approved_study_rejected(self):
        with sqlite3.connect(self.state/"workflow.sqlite") as db:
            db.execute("UPDATE studies SET state='approved'")
        with self.assertRaises(ValueError):
            sql_prepare_review.prepare_draft(self.config,UID,nlp=self.nlp)

    def test_active_job_rejected(self):
        with sqlite3.connect(self.state/"workflow.sqlite") as db:
            db.execute("INSERT INTO jobs VALUES('running')")
        with self.assertRaises(ValueError):
            sql_prepare_review.prepare_draft(self.config,UID,nlp=self.nlp)

if __name__=="__main__":
    unittest.main()
