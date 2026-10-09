"""Synthetic integration tests for disabled-by-default clinical OHIF gateway.

No actual patient DICOM files, identifiers, network connections, or exports.
"""
import base64
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

    def test_no_upload_endpoint(self):
        self.assertEqual(self.client.post("/ohif/dicomweb/studies",
            headers=self.auth,data=b"no").status_code,403)

if __name__=="__main__":
    unittest.main()
