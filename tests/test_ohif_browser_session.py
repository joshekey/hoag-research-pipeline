"""Synthetic OHIF browser-cookie tests; no clinical records or credentials.

Use real current time for Flask secure-session signing. Patching time.time
globally also changes itsdangerous cookie timestamp verification and can
incorrectly invalidate otherwise legitimate test sessions.
"""
import time
import unittest

from ohif_poc.study_capability import issue
from ohif_poc.synthetic_browser_session import create_session_test_app
from ohif_poc.synthetic_dicomweb import ROOT, STUDY, SERIES, SOPS

KEY=b"synthetic-browser-auth-key-0123456789abcdef"
SESSION_KEY="test-only-session-secret-not-for-deployment"
HTTPS="https://localhost"

class SyntheticBrowserSessionTests(unittest.TestCase):
    def setUp(self):
        self.app=create_session_test_app(KEY,SESSION_KEY)
        self.client=self.app.test_client()

    def attach(self, principal="synthetic-reviewer", expired=False):
        issued=int(time.time())-(200 if expired else 0)
        token=issue(KEY,STUDY,principal,now=issued,ttl=120)
        with self.client.session_transaction(base_url=HTTPS) as data:
            data["_synthetic_verified_identity"]=principal
            data["_synthetic_study_capability"]=token

    def get(self, path, **kwargs):
        return self.client.get(path,base_url=HTTPS,**kwargs)

    def post(self, path, **kwargs):
        return self.client.post(path,base_url=HTTPS,**kwargs)

    def test_no_session_denied(self):
        self.assertEqual(self.get(ROOT+"/studies").status_code,401)

    def test_session_scoped_study_and_image(self):
        self.attach()
        response=self.get(ROOT+"/studies")
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.json[0]["00201208"]["Value"][0],157)
        self.assertEqual(response.headers["Cache-Control"],"no-store")
        self.assertNotIn("Access-Control-Allow-Origin",response.headers)
        path=ROOT+"/studies/"+STUDY+"/series/"+SERIES+"/instances/"+SOPS[0]
        self.assertEqual(self.get(path).status_code,200)

    def test_expired_session_denied(self):
        self.attach(expired=True)
        self.assertEqual(self.get(ROOT+"/studies").status_code,403)

    def test_other_study_denied(self):
        self.attach()
        self.assertEqual(self.get(ROOT+"/studies/1.2.999/metadata").status_code,404)

    def test_no_browser_supplied_identity(self):
        self.assertEqual(self.get(ROOT+"/studies",headers={
            "X-User":"synthetic-reviewer"}).status_code,401)

    def test_no_upload(self):
        self.attach()
        self.assertEqual(self.post(ROOT+"/studies",data=b"data").status_code,405)

if __name__=="__main__":
    unittest.main()
