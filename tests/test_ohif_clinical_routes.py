"""Synthetic integration tests for disabled-by-default clinical OHIF gateway.

No actual patient DICOM files, identifiers, network connections, or exports.
"""
import base64
from copy import deepcopy
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
        # Collect sqlite context-manager connections before Windows removes files.
        import gc
        self.addCleanup(gc.collect)
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

    def test_ohif_csp_supports_jpeg_worker_without_loosening_dashboard(self):
        # Browser JPEG Lossless worker needs dynamic Function (Emscripten).
        viewer=self.client.get("/viewer?StudyInstanceUIDs="+STUDY,headers=self.auth)
        viewer_csp=viewer.headers["Content-Security-Policy"]
        self.assertIn("'unsafe-eval'",viewer_csp)
        self.assertIn("'wasm-unsafe-eval'",viewer_csp)
        self.assertIn("'sha256-RUJKPsAHcLA3YS8dWuPEiqEPTlkfTEHKow0zXWj7MBE='",viewer_csp)
        self.assertIn("frame-ancestors 'self'",viewer_csp)
        dashboard=self.client.get("/",headers=self.auth)
        dashboard_csp=dashboard.headers["Content-Security-Policy"]
        self.assertNotIn("'unsafe-eval'",dashboard_csp)
        self.assertNotIn("'unsafe-inline'",dashboard_csp)
        self.assertIn("frame-ancestors 'none'",dashboard_csp)

    def test_manifest_uses_credentials_without_removing_authentication(self):
        from webapp import authenticated_viewer_html
        for ending in ('>', '/>'):
            source='<link rel="manifest" crossorigin="anonymous" href="/manifest.json"'+ending
            result=authenticated_viewer_html(source)
            self.assertIn('crossorigin="use-credentials"',result)
            self.assertNotIn('anonymous',result)
            self.assertEqual(result.count('crossorigin'),1)
        self.assertEqual(self.client.get('/manifest.json').status_code,401)

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

    def test_series_qido_uses_real_modality_not_mr_constant(self):
        indexed={(SERIES,SOPS[0]):{"metadata":json.dumps({"Modality":"CT"})},
                 (SERIES,SOPS[1]):{"metadata":json.dumps({"Modality":"CT"})}}
        with patch("ohif_poc.clinical_dicomweb.scoped_catalog",return_value=indexed):
            reply=self.client.get("/ohif/dicomweb/studies/"+STUDY+"/series",
                                  headers=self.auth)
        self.assertEqual(reply.status_code,200)
        self.assertEqual(reply.json[0]["00080060"]["Value"],["CT"])
        self.assertEqual(reply.json[0]["00201209"]["Value"],[2])

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

    def test_invalid_lossless_codestream_is_rejected_on_server(self):
        from pydicom.encaps import encapsulate
        from pydicom.uid import JPEGLossless, JPEGLosslessSV1, JPEG2000Lossless
        for syntax, media in ((JPEGLossless,"image/jpeg"),
                              (JPEGLosslessSV1,"image/jpeg"),
                              (JPEG2000Lossless,"image/jp2")):
            with self.subTest(media=media):
                ds=deepcopy(synthetic_dataset(SOPS[0]))
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
                self.assertEqual(reply.status_code,415)


    def test_lossless_jpeg2000_decodes_exact_samples(self):
        import numpy as np
        from PIL import Image
        from pydicom.encaps import encapsulate
        from pydicom.uid import JPEG2000Lossless
        ds=deepcopy(synthetic_dataset(SOPS[0]))
        pixels=np.arange(int(ds.Rows)*int(ds.Columns),dtype=np.uint16).reshape(int(ds.Rows),int(ds.Columns))
        encoded=io.BytesIO();Image.fromarray(pixels).save(encoded,format='JPEG2000',no_jp2=True,irreversible=False)
        ds.file_meta.TransferSyntaxUID=JPEG2000Lossless
        ds.PixelData=encapsulate([encoded.getvalue()]);ds['PixelData'].is_undefined_length=True
        out=io.BytesIO();ds.save_as(out,enforce_file_format=True)
        with patch('ohif_poc.clinical_dicomweb.scoped_catalog',return_value={(SERIES,SOPS[0]):{'size':len(out.getvalue())}}), patch('ohif_poc.clinical_dicomweb.instance_bytes',return_value=out.getvalue()):
            reply=self.client.get('/ohif/dicomweb/studies/'+STUDY+'/series/'+SERIES+'/instances/'+SOPS[0]+'/frames/1',headers=self.auth)
        self.assertEqual(reply.status_code,200)
        self.assertIn('application/octet-stream',reply.content_type)
        actual=reply.data.split(b'\r\n\r\n',1)[1].rsplit(b'\r\n--',1)[0]
        self.assertEqual(actual,pixels.astype('<u2').tobytes())

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
