"""Synthetic OHIF study preflight checks, no hospital DICOM or PHI."""
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pydicom.dataset import Dataset, FileMetaDataset
from ohif_poc.preflight import check

class OhifPreflightTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name)
        self.uid="1.2.3"
        self.config={"state_dir":str(self.state)}
        with sqlite3.connect(self.state/"workflow.sqlite") as db:
            db.executescript("""
              CREATE TABLE settings(key TEXT,value TEXT);
              INSERT INTO settings VALUES('scan_complete','1');
              CREATE TABLE jobs(state TEXT);
              CREATE TABLE studies(uid TEXT,count INTEGER);
              INSERT INTO studies VALUES('1.2.3',2);
              CREATE TABLE files(id INTEGER PRIMARY KEY,root TEXT,path TEXT,size INTEGER,mtime INTEGER,
                                 metadata TEXT,kind TEXT,active INTEGER,status TEXT);
            """)
            for n in (1,2):
                db.execute("""INSERT INTO files(root,path,size,mtime,metadata,kind,active,status)
                              VALUES (?,?,?,?,?,?,?,?)""",
                           ("/synthetic",f"/synthetic/{n}.dcm",1,1,
                            json.dumps({"StudyInstanceUID":self.uid}),"dicom",1,"ok"))

    def make_ds(self,n=1):
        ds=Dataset()
        ds.StudyInstanceUID=self.uid
        ds.SeriesInstanceUID="1.2.3.4"
        ds.SOPInstanceUID=f"1.2.3.4.{n}"
        ds.SOPClassUID="1.2.840.10008.5.1.4.1.1.4"
        ds.file_meta=FileMetaDataset()
        ds.file_meta.TransferSyntaxUID="1.2.840.10008.1.2.1"
        return ds

    def test_two_instances_one_series(self):
        values=[self.make_ds(1),self.make_ds(2)]
        with patch("ohif_poc.preflight.engine.source_path",return_value=Path("/synthetic")), \
             patch("ohif_poc.preflight.pydicom.dcmread",side_effect=values):
            output=check(self.config,self.uid)
        self.assertEqual(output["instances_indexed"],2)
        self.assertEqual(output["series_count"],1)
        self.assertEqual(output["unique_sop_instances"],2)
        self.assertTrue(output["ready_for_dicomweb_adapter_design"])
        self.assertNotIn("uid",output)

    def test_duplicate_sop_blocks_ready(self):
        values=[self.make_ds(1),self.make_ds(1)]
        with patch("ohif_poc.preflight.engine.source_path",return_value=Path("/synthetic")), \
             patch("ohif_poc.preflight.pydicom.dcmread",side_effect=values):
            output=check(self.config,self.uid)
        self.assertFalse(output["ready_for_dicomweb_adapter_design"])

    def test_active_job_fails(self):
        with sqlite3.connect(self.state/"workflow.sqlite") as db:
            db.execute("INSERT INTO jobs VALUES('running')")
        with self.assertRaises(ValueError):
            check(self.config,self.uid)

if __name__=="__main__":
    unittest.main()
