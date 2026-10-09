"""Synthetic same-origin OHIF browser session checks, no clinical records."""
import unittest
from unittest.mock import patch
from ohif_poc.study_capability import issue
from ohif_poc.synthetic_browser_session import create_session_test_app
from ohif_poc.synthetic_dicomweb import ROOT, STUDY, SERIES, SOPS

KEY=b"synthetic-browser-auth-key-0123456789abcdef"
SESSION_KEY="test-only-session-secret-not-for-deployment"

class SyntheticBrowserSessionTests(unittest.TestCase):
    def setUp(self):
        self.app=create_session_test_app(KEY,SESSION_KEY)
        self.client=self.app.test_client()

    def attach(self,principal="synthetic-reviewer",expire=False):
        token=issue(KEY,STUDY,principal,now=1000,ttl=120)
        with self.client.session_transaction(base_url="https://localhost") as data:
            data["_synthetic_verified_identity"]=principal
            data["_synthetic_study_capability"]=token

    def test_no_session_denied(self):
        with patch("ohif_poc.study_capability.time.time",return_value=1050):
            self.assertEqual(self.client.get(ROOT+"/studies",base_url="https://localhost").status_code,401)

    def test_session_scoped_study_and_image(self):
        self.attach()
        with patch("ohif_poc.study_capability.time.time",return_value=1050):
            response=self.client.get(ROOT+"/studies",base_url="https://localhost")
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json[0]["00201208"]["Value"][0],157)
            self.assertEqual(response.headers["Cache-Control"],"no-store")
            self.assertNotIn("Access-Control-Allow-Origin",response.headers)
            path=ROOT+"/studies/"+STUDY+"/series/"+SERIES+"/instances/"+SOPS[0]
            self.assertEqual(self.client.get(path,base_url="https://localhost").status_code,200)

    def test_expired_session_denied(self):
        self.attach()
        with patch("ohif_poc.study_capability.time.time",return_value=1120):
            self.assertEqual(self.client.get(ROOT+"/studies").status_code,403)

    def test_other_study_denied(self):
        self.attach()
        with patch("ohif_poc.study_capability.time.time",return_value=1050):
            self.assertEqual(self.client.get(ROOT+"/studies/1.2.999/metadata",base_url="https://localhost").status_code,404)

    def test_no_browser_supplied_identity(self):
        with patch("ohif_poc.study_capability.time.time",return_value=1050):
            self.assertEqual(self.client.get(ROOT+"/studies",
                headers={"X-User":"synthetic-reviewer"},base_url="https://localhost").status_code,401)

    def test_no_upload(self):
        self.attach()
        with patch("ohif_poc.study_capability.time.time",return_value=1050):
            self.assertEqual(self.client.post(ROOT+"/studies",data=b"data",base_url="https://localhost").status_code,405)

if __name__=="__main__":
    unittest.main()
