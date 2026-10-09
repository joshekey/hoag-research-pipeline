"""Association confirmation tests use synthetic metadata; never hospital records."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sql_association

class AssociationTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.config={"state_dir":str(self.root)}
        self.uid="1.2.3"
        with sqlite3.connect(self.root/"workflow.sqlite") as db:
            db.executescript("""
            CREATE TABLE settings (key TEXT,value TEXT);
            INSERT INTO settings VALUES ('scan_complete','1');
            CREATE TABLE jobs (state TEXT);
            CREATE TABLE studies (uid TEXT, fingerprint TEXT, state TEXT);
            CREATE TABLE audit (id INTEGER PRIMARY KEY,at REAL,actor TEXT,action TEXT,target TEXT);
            INSERT INTO studies VALUES ('1.2.3','fingerprint-one','unmatched');
            """)
        self.p1=patch.object(sql_association.sql_gui_candidates,"candidates",
                             return_value=([{"token":"a"*32,"score":75}],1))
        self.p2=patch.object(sql_association.sql_gui_candidates,"report_text",
                             return_value="PATIENT NAME: SYNTHETIC, USER FINDINGS: synthetic")
        self.p1.start()
        self.p2.start()
        self.addCleanup(self.p1.stop)
        self.addCleanup(self.p2.stop)

    def run_confirm(self,**kw):
        opts=dict(config=self.config,uid=self.uid,token="a"*32,
                  note="Confirmed exam from original report and DICOM.",
                  date_verified=True,conflicts_acknowledged=True,actor="admin")
        opts.update(kw)
        return sql_association.confirm(**opts)

    def test_confirmation_not_export_approval(self):
        result=self.run_confirm()
        self.assertTrue(result["recorded"])
        self.assertFalse(result["research_export_authorized"])
        with sqlite3.connect(self.root/"workflow.sqlite") as db:
            self.assertEqual(db.execute("SELECT state FROM studies").fetchone()[0],"unmatched")
            self.assertEqual(db.execute("SELECT count(*) FROM sql_associations").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM audit").fetchone()[0],1)

    def test_missing_verification_rejected(self):
        with self.assertRaises(ValueError):
            self.run_confirm(date_verified=False)

    def test_stale_token_rejected(self):
        with self.assertRaises(ValueError):
            self.run_confirm(token="b"*32)

    def test_active_job_rejected(self):
        with sqlite3.connect(self.root/"workflow.sqlite") as db:
            db.execute("INSERT INTO jobs VALUES ('running')")
        with self.assertRaises(ValueError):
            self.run_confirm()

    def test_approved_study_not_reassociated(self):
        with sqlite3.connect(self.root/"workflow.sqlite") as db:
            db.execute("UPDATE studies SET state='approved'")
        with self.assertRaises(ValueError):
            self.run_confirm()

if __name__=="__main__":
    unittest.main()
