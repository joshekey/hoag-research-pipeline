"""Synthetic broker tests; never call hospital SQL or emit clinical records."""
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import sql_broker

class BrokerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.private = root / "sql-private"
        self.private.mkdir(mode=0o700)
        self.index = self.private / "reports-v2.sqlite"
        with sqlite3.connect(self.index) as db:
            db.execute("CREATE TABLE narratives (report_id BLOB, text BLOB, procedure_name BLOB, result_date BLOB)")
            db.executemany("INSERT INTO narratives VALUES (?,?,?,?)", [
                (b"1", b"PATIENT NAME: SAMPLE, PERSON MRN: DIFFERENT DATE OF BIRTH: 2/3/1980 FINDINGS: synthetic", b"MRI LUMBAR", b"2021-09-02"),
                (b"2", b"PATIENT NAME: OTHER, PERSON MRN: DIFFERENT DATE OF BIRTH: 2/3/1980 FINDINGS: synthetic", b"MRI LUMBAR", b"2021-09-02"),
            ])
        self.index.chmod(0o600)
        self.patch_index = patch.object(sql_broker, "INDEX", self.index)
        self.patch_secret = patch.object(sql_broker, "secret", return_value=b"synthetic-key")
        self.patch_index.start()
        self.patch_secret.start()
        self.addCleanup(self.patch_index.stop)
        self.addCleanup(self.patch_secret.stop)
        self.request = {"action":"list", "uid":"1.2.3"}
        self.auth_patch = patch.object(sql_broker, "authorized_study", return_value={
            "name":"PERSON SAMPLE", "dob":"19800203", "date":"20210902", "modality":"MR"})
        self.config_patch = patch.object(sql_broker, "load_config", return_value={"state_dir":"/synthetic", "secret_key":"synthetic-key"})
        self.auth_patch.start()
        self.addCleanup(self.auth_patch.stop)
        self.config_patch.start()
        self.addCleanup(self.config_patch.stop)

    def test_only_same_name_dob_returned(self):
        result = sql_broker.query(self.request)
        self.assertEqual(result["total"], 1)
        self.assertEqual(len(result["candidates"]), 1)
        self.assertFalse(result["candidates"][0]["exam_date_verified"])
        self.assertTrue(result["candidates"][0]["result_date_matches"])
        self.assertNotIn("SAMPLE", str(result))

    def test_preview_scoped_by_study(self):
        token = sql_broker.query(self.request)["candidates"][0]["token"]
        good = sql_broker.query(dict(self.request, action="preview", token=token))
        self.assertIn("FINDINGS:", good["text"])
        with self.assertRaises(LookupError):
            sql_broker.query(dict(self.request, uid="1.2.99", action="preview", token=token))
        with self.assertRaises(ValueError):
            sql_broker.query(dict(self.request, name="OTHER^PERSON", action="preview", token=token))

    def test_refuses_unrestricted_file_permissions(self):
        self.index.chmod(0o644)
        with self.assertRaises(ValueError):
            sql_broker.query(self.request)

    def test_rejects_unexpected_fields(self):
        with self.assertRaises(ValueError):
            sql_broker.query(dict(self.request, sql="SELECT * FROM narratives"))

if __name__ == "__main__":
    unittest.main()
