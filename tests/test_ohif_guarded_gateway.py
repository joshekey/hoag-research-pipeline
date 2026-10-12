"""Synthetic guarded QIDO/WADO integration tests. No clinical data."""
import unittest
from unittest import mock
from ohif_poc.synthetic_dicomweb import ROOT, STUDY, SERIES, SOPS
from ohif_poc.study_capability import issue
from ohif_poc.synthetic_guarded_gateway import create_guarded_synthetic_app

KEY = b"test-only-key-1234567890-abcdefghijklmnop"

class GuardedGatewayTests(unittest.TestCase):
    def setUp(self):
        self.user = "synthetic-reviewer"
        self.app = create_guarded_synthetic_app(KEY, lambda: self.user)
        self.client = self.app.test_client()
        self.token = issue(KEY, STUDY, self.user, now=1000, ttl=120)

    def get(self, path, token=None):
        return self.client.get(path, headers={"X-HOAG-OHIF-Capability": token or self.token})

    def test_missing_token_rejected(self):
        self.assertEqual(self.client.get(ROOT + "/studies").status_code, 403)

    def test_valid_study_list_and_image_retrieval(self):
        with mock.patch("ohif_poc.study_capability.time.time", return_value=1050):
            studies = self.get(ROOT + "/studies")
            self.assertEqual(studies.status_code, 200)
            self.assertEqual(studies.headers["Cache-Control"], "no-store")
            self.assertEqual(studies.json[0]["00201208"]["Value"][0], 157)
            image = self.get(ROOT + "/studies/" + STUDY + "/series/" + SERIES + "/instances/" + SOPS[0])
            self.assertEqual(image.status_code, 200)
            self.assertIn("multipart/related", image.content_type)

    def test_expired_token_rejected(self):
        self.assertEqual(self.get(ROOT + "/studies").status_code, 403)

    def test_wrong_identity_rejected(self):
        self.user = "different-user"
        with mock.patch("ohif_poc.study_capability.time.time", return_value=1050):
            self.assertEqual(self.get(ROOT + "/studies").status_code, 403)

    def test_absent_principal_rejected(self):
        self.user = ""
        self.assertEqual(self.get(ROOT + "/studies").status_code, 401)

    def test_other_study_and_upload_rejected(self):
        with mock.patch("ohif_poc.study_capability.time.time", return_value=1050):
            self.assertEqual(self.get(ROOT + "/studies/1.2.999/metadata").status_code, 404)
            self.assertEqual(self.client.post(ROOT + "/studies",
                              headers={"X-HOAG-OHIF-Capability": self.token}).status_code, 405)

    def test_non_dicomweb_routes_rejected(self):
        self.assertEqual(self.client.get("/").status_code, 404)

if __name__ == "__main__":
    unittest.main()
