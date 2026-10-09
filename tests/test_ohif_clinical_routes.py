"""Synthetic integration tests for disabled-by-default clinical OHIF gateway.

No actual patient DICOM files, identifiers, network connections, or exports.
"""
import base64
import json
import io
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from webapp import create_app
from ohif_poc.synthetic_dicomweb import synthetic_dataset, STUDY, SERIES, SOPS

class ClinicalOHIFPilotTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.state=Path(self.tmp.name)
        from werkzeug.security import generate_password_hash
        self.config={
            "state_dir":str(self.state),
            "username":"admin",
            "password_hash":generate_password_hash("synthetic-password"),
            "secret_key":"synthetic-dashboard-key",
            "source_roots":["/synthetic-source"],
            "output_root":"/synthetic-output",
            "secure_cookies":False,
        }
        self.app=create_app(self.config)
        self.client=self.app.test_client()
        self.auth={"Authorization":"Basic "+base64.b64encode(
            b"admin:synthetic-password").decode()}
        import sqlite3
        with sqlite3.connect(self.state/"workflow.sqlite") as db:
            db.execute("UPDATE settings SET value='1' WHERE key='scan_complete'")
            db.execute("""INSERT INTO studies(uid,subject,fingerprint,redactions,source_hashes,state,count)
                          VALUES (?,?,?,?,?,?,?)""",
                       (STUDY,"SYNTHETIC-SUBJECT","fp",'[]','{}',"unmatched",2))
            for idx,sop in enumerate(SOPS[:2],1):
                db.execute("""INSERT INTO files(path,root,kind,size,mtime,metadata,status,active)
                    VALUES (?,?,?,?,?,?,?,1)""",
                    ("/synthetic/"+str(idx)+".dcm","/synthetic","dicom",1,1,
                     json.dumps({"StudyInstanceUID":STUDY,"SeriesInstanceUID":SERIES,"SOPInstanceUID":sop}),"ok"))
        self.allowed=self.state/"ohif.uid"
        self.allowed.write_text(STUDY)
        self.allowpatch=patch("ohif_poc.clinical_dicomweb.ALLOWLIST",self.allowed)
        self.allowpatch.start()
        self.addCleanup(self.allowpatch.stop)

    def test_no_authorization_denied(self):
        self.assertEqual(self.client.get("/ohif/dicomweb/studies").status_code,401)
        self.assertEqual(self.client.get("/viewer?StudyInstanceUIDs="+STUDY).status_code,401)

    def test_allowlist_missing_fails_closed(self):
        self.allowed.unlink()
        self.assertEqual(self.client.get("/ohif/dicomweb/studies",
                                        headers=self.auth).status_code,404)

    def test_study_discovery_only_approved_pilot(self):
        indexed={(SERIES,SOPS[0]):{},(SERIES,SOPS[1]):{}}
        with patch("ohif_poc.clinical_dicomweb.scoped_catalog",return_value=indexed):
            result=self.client.get("/ohif/dicomweb/studies",headers=self.auth)
        self.assertEqual(result.status_code,200)
        self.assertEqual(result.json[0]["00201208"]["Value"][0],2)
        self.assertEqual(result.headers["Cache-Control"],"no-store")

    def test_foreign_study_denied_before_index(self):
        with patch("ohif_poc.clinical_dicomweb.scoped_catalog") as lookup:
            result=self.client.get("/ohif/dicomweb/studies/1.2.999/metadata",
                                   headers=self.auth)
        self.assertEqual(result.status_code,404)
        lookup.assert_not_called()

    def test_foreign_series_and_sop_denied(self):
        indexed={(SERIES,SOPS[0]):{}}
        with patch("ohif_poc.clinical_dicomweb.scoped_catalog",return_value=indexed):
            result=self.client.get("/ohif/dicomweb/studies/"+STUDY+
                "/series/1.2.999/instances/"+SOPS[0],headers=self.auth)
        self.assertEqual(result.status_code,404)

    def test_instance_retrieval_only_authenticated(self):
        ds=synthetic_dataset(SOPS[0])
        out=io.BytesIO()
        ds.save_as(out,enforce_file_format=True)
        fake={"size":len(out.getvalue())}
        with patch("ohif_poc.clinical_dicomweb.scoped_catalog",
                   return_value={(SERIES,SOPS[0]):fake}), \
             patch("ohif_poc.clinical_dicomweb.instance_bytes",
                   return_value=out.getvalue()):
            response=self.client.get("/ohif/dicomweb/studies/"+STUDY+
                "/series/"+SERIES+"/instances/"+SOPS[0],headers=self.auth)
        self.assertEqual(response.status_code,200)
        self.assertIn("multipart/related",response.content_type)

    def test_lossless_compressed_frame_media_types(self):
        from pydicom.encaps import encapsulate
        from pydicom.uid import JPEGLosslessSV1, JPEG2000Lossless
        for syntax, media in ((JPEGLosslessSV1,"image/jpeg"),
                              (JPEG2000Lossless,"image/jp2")):
            with self.subTest(media=media):
                ds=synthetic_dataset(SOPS[0])
                ds.file_meta.TransferSyntaxUID=syntax
                ds.PixelData=encapsulate([b"FAKE_COMPRESSED_CODESTREAM"])
                ds["PixelData"].is_undefined_length=True
                out=io.BytesIO()
                ds.save_as(out,enforce_file_format=True)
                with patch("ohif_poc.clinical_dicomweb.scoped_catalog",
                           return_value={(SERIES,SOPS[0]):{"size":len(out.getvalue())}}), \
                     patch("ohif_poc.clinical_dicomweb.instance_bytes",
                           return_value=out.getvalue()):
                    reply=self.client.get("/ohif/dicomweb/studies/"+STUDY+
                          "/series/"+SERIES+"/instances/"+SOPS[0]+"/frames/1",
                          headers=self.auth)
                self.assertEqual(reply.status_code,200)
                self.assertIn(media,reply.content_type)
                self.assertIn(b"FAKE_COMPRESSED_CODESTREAM",reply.data)

    def test_all_indexed_mode_lists_multiple_eligible_studies(self):
        import sqlite3
        other="1.2.826.0.1.3680043.10.543.777"
        with sqlite3.connect(self.state/"workflow.sqlite") as db:
            db.execute("""INSERT INTO studies(uid,subject,fingerprint,redactions,source_hashes,state,count)
                          VALUES (?,?,?,?,?,?,?)""",
                       (other,"SYNTHETIC-OTHER","fp2",'[]','{}',"unmatched",1))
            db.execute("""INSERT INTO files(path,root,kind,size,mtime,metadata,status,active)
                          VALUES (?,?,?,?,?,?,?,1)""",
                       ("/synthetic/other.dcm","/synthetic","dicom",1,1,
                        json.dumps({"StudyInstanceUID":other,
                                    "SeriesInstanceUID":other+".1",
                                    "SOPInstanceUID":other+".1.1"}),"ok"))
        self.allowed.write_text("ALL_INDEXED")
        with patch("ohif_poc.clinical_dicomweb.scoped_catalog",
                   side_effect=lambda _cfg,study,_allowed:
                       {(SERIES,SOPS[0]):{}} if study==STUDY
                       else {(other+".1",other+".1.1"): {}}):
            reply=self.client.get("/ohif/dicomweb/studies",headers=self.auth)
        self.assertEqual(reply.status_code,200)
        self.assertEqual(len(reply.json),2)
        self.assertEqual(
            {x["0020000D"]["Value"][0] for x in reply.json},{STUDY,other})

    def test_all_indexed_mode_excludes_incomplete_study(self):
        import sqlite3
        self.allowed.write_text("ALL_INDEXED")
        with sqlite3.connect(self.state/"workflow.sqlite") as db:
            db.execute("UPDATE studies SET count=999 WHERE uid=?",(STUDY,))
        result=self.client.get("/ohif/dicomweb/studies",headers=self.auth)
        self.assertEqual(result.status_code,404)

    def test_no_upload_endpoint(self):
        self.assertEqual(self.client.post("/ohif/dicomweb/studies",
            headers=self.auth,data=b"no").status_code,403)

if __name__=="__main__":
    unittest.main()
