"""Synthetic authorization boundary tests for future OHIF catalog resolution."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydicom.dataset import Dataset
from ohif_poc.scoped_catalog import scoped_catalog, scoped_instance

class ScopedCatalogTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)
        self.config={"state_dir":str(self.root)}
        self.study="1.2.3"
        self.series="1.2.3.4"
        self.sop="1.2.3.4.5"
        with sqlite3.connect(self.root/"workflow.sqlite") as db:
            db.executescript("""
            CREATE TABLE settings(key TEXT,value TEXT);
            INSERT INTO settings VALUES('scan_complete','1');
            CREATE TABLE jobs(state TEXT);
            CREATE TABLE studies(uid TEXT,count INTEGER,state TEXT);
            INSERT INTO studies VALUES('1.2.3',1,'unmatched');
            CREATE TABLE files(id INTEGER PRIMARY KEY,kind TEXT,active INTEGER,status TEXT,
                               path TEXT,metadata TEXT);
            """)
            db.execute("""INSERT INTO files(kind,active,status,path,metadata)
                VALUES('dicom',1,'ok',?,?)""",(
                "/synthetic/test.dcm",
                json.dumps({"StudyInstanceUID":self.study,
                            "SeriesInstanceUID":self.series,
                            "SOPInstanceUID":self.sop})))

    def test_authorized_single_study(self):
        mapping=scoped_catalog(self.config,self.study,self.study)
        self.assertEqual(list(mapping),[(self.series,self.sop)])

    def test_foreign_study_denied(self):
        with self.assertRaises(PermissionError):
            scoped_catalog(self.config,self.study,"1.2.99")

    def test_invalid_identifier_denied(self):
        with self.assertRaises(ValueError):
            scoped_catalog(self.config,"../etc/passwd","../etc/passwd")

    def test_missing_series_sop_denied(self):
        with self.assertRaises(LookupError):
            scoped_instance(self.config,self.study,self.study,self.series,"1.2.99")

    def test_header_consistency(self):
        ds=Dataset()
        ds.StudyInstanceUID=self.study
        ds.SeriesInstanceUID=self.series
        ds.SOPInstanceUID=self.sop
        with patch("ohif_poc.scoped_catalog.engine.source_path",
                   return_value=Path("/synthetic/test.dcm")), \
             patch("ohif_poc.scoped_catalog.pydicom.dcmread",return_value=ds):
            self.assertEqual(scoped_instance(
                self.config,self.study,self.study,self.series,self.sop),
                Path("/synthetic/test.dcm"))
            ds.SOPInstanceUID="1.2.100"
            with self.assertRaises(ValueError):
                scoped_instance(self.config,self.study,self.study,self.series,self.sop)

    def test_catalog_incomplete(self):
        with sqlite3.connect(self.root/"workflow.sqlite") as db:
            db.execute("UPDATE studies SET count=2")
        with self.assertRaises(ValueError):
            scoped_catalog(self.config,self.study,self.study)

    def test_active_job_denied(self):
        with sqlite3.connect(self.root/"workflow.sqlite") as db:
            db.execute("INSERT INTO jobs VALUES('running')")
        with self.assertRaises(ValueError):
            scoped_catalog(self.config,self.study,self.study)

if __name__=="__main__":
    unittest.main()
