"""Synthetic historical-index overlap tests; no real patient information."""
import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from historical_emr_overlap import audit, build_historical_index


class HistoricalEMROverlapTests(unittest.TestCase):
    def test_group_historical_dicoms_by_study_not_instance(self):
        entries=[
            json.dumps({"StudyInstanceUID":"1.2.3","PatientName":"DOE^JANE",
                        "StudyDate":"20261001","Modality":"MR"}),
            json.dumps({"StudyInstanceUID":"1.2.3","PatientName":"DOE^JANE",
                        "StudyDate":"20261001","Modality":"MR"}),
            json.dumps({"StudyInstanceUID":"1.2.4","PatientName":"OTHER^JOHN",
                        "StudyDate":"20261002","Modality":"CT"}),
        ]
        grouped,stats=build_historical_index(entries)
        self.assertEqual(stats["historical_dicom_rows"],3)
        self.assertEqual(stats["historical_studies"],2)
        self.assertEqual(grouped["1.2.3"]["instances"],2)

    def test_overlap_counts_no_mutations(self):
        with tempfile.TemporaryDirectory() as temp:
            state=Path(temp)
            txt=state/"synthetic.txt"
            txt.write_text("DOE, JANE (MR # EMR999)\nDate of Service: 10/01/2026\n"
                           "MRI CERVICAL SPINE WITHOUT CONTRAST\n")
            sha=hashlib.sha256(txt.read_bytes()).hexdigest()
            dbfile=state/"workflow.sqlite"
            with sqlite3.connect(dbfile) as db:
                db.executescript("""CREATE TABLE settings(key TEXT,value TEXT);
                    CREATE TABLE jobs(state TEXT);
                    CREATE TABLE files(id INTEGER PRIMARY KEY,path TEXT,root TEXT,
                        kind TEXT,active INTEGER,status TEXT,metadata TEXT);""")
                db.execute("INSERT INTO settings VALUES('scan_complete','1')")
                for index in range(2):
                    db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?)",
                       (index+1,"/unused/image"+str(index),temp,"dicom",0,"ok",
                        json.dumps({"StudyInstanceUID":"1.2.3",
                                    "PatientName":"DOE^JANE",
                                    "StudyDate":"20261001","Modality":"MR"})))
                db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?)",
                           (3,str(txt),temp,"report",0,"ok",json.dumps({"hash":sha})))
            with patch("historical_emr_overlap.engine.read_report",
                       return_value=(txt.read_text(),sha)):
                result=audit({"state_dir":temp})
            self.assertEqual(result["historical_dicom_rows"],2)
            self.assertEqual(result["historical_studies"],1)
            self.assertEqual(result["historical_studies_with_exact_name_overlap"],1)
            self.assertEqual(result["historical_studies_with_name_dos_modality"],1)
            with sqlite3.connect(dbfile) as db:
                self.assertEqual(db.execute("SELECT SUM(active) FROM files").fetchone()[0],0)

    def test_no_name_overlap_returns_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            dbfile=Path(temp)/"workflow.sqlite"
            with sqlite3.connect(dbfile) as db:
                db.executescript("""CREATE TABLE settings(key TEXT,value TEXT);
                    CREATE TABLE jobs(state TEXT);
                    CREATE TABLE files(id INTEGER PRIMARY KEY,path TEXT,root TEXT,
                        kind TEXT,active INTEGER,status TEXT,metadata TEXT);""")
                db.execute("INSERT INTO settings VALUES('scan_complete','1')")
                db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?)",
                           (1,"/unused/img",temp,"dicom",0,"ok",
                            json.dumps({"StudyInstanceUID":"1.2.3",
                                        "PatientName":"DOE^JANE","StudyDate":"20261001"})))
                db.execute("INSERT INTO files VALUES(?,?,?,?,?,?,?)",
                           (2,"/unused/report",temp,"report",0,"ok",
                            json.dumps({"hash":"abc"})))
            with patch("historical_emr_overlap.engine.read_report",
                       return_value=("OTHER, JOHN (MR # EMR999)","abc")):
                result=audit({"state_dir":temp})
            self.assertEqual(result["historical_studies_with_exact_name_overlap"],0)
            self.assertEqual(result["historical_studies_with_surname_overlap"],0)


if __name__=="__main__":
    unittest.main()
