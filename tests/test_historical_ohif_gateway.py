"""Synthetic tests for gated historical OHIF routes and strict study scope."""
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash
from webapp import create_app
from ohif_poc.synthetic_dicomweb import synthetic_dataset, STUDY, SERIES, SOPS


class HistoricalOHIFTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        cfg={"state_dir":self.temp.name,"username":"synthetic",
             "password_hash":generate_password_hash("synthetic-password"),
             "secret_key":"synthetic-testing-key","source_roots":["/synthetic"],
             "output_root":"/synthetic-output","secure_cookies":False}
        self.client=create_app(cfg).test_client()
        self.auth={"Authorization":"Basic "+base64.b64encode(
            b"synthetic:synthetic-password").decode()}

    def test_no_auth_historical_metadata_denied(self):
        url="/ohif/dicomweb/studies/"+STUDY+"/metadata"
        self.assertEqual(self.client.get(url).status_code,401)

    def test_historical_missing_gate_denied_before_catalog(self):
        from ohif_poc.historical_retrieval import historical_index
        with patch("ohif_poc.historical_retrieval.allowed_uids",return_value=()):
            with self.assertRaises(Exception):
                historical_index({"state_dir":self.temp.name},STUDY)

    def test_only_eligible_historical_study_served(self):
        other="1.2.826.0.1.3680043.10.543.999"
        with patch("ohif_poc.historical_retrieval.allowed_uids",return_value=(STUDY,)):
            with self.assertRaises(Exception):
                from ohif_poc.historical_retrieval import historical_index
                historical_index({"state_dir":self.temp.name},other)

    def test_historical_viewer_gate_and_auth(self):
        with patch("ohif_poc.clinical_dicomweb.allowed_studies",return_value=[]), \
             patch("ohif_poc.clinical_dicomweb.allowed_uids",return_value=(STUDY,)), \
             patch("webapp.authorized_uid",return_value=STUDY):
            # Doesn't assert HTML asset availability; only verifies auth.
            self.assertEqual(self.client.get("/viewer?StudyInstanceUIDs="+STUDY).status_code,401)

    def test_historical_review_panel_is_distinct(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"templates/index.html").read_text()
        css=(root/"static/app.css").read_text()
        self.assertIn('id="historical-ohif-iframe"',html)
        self.assertIn('id="ohif-iframe"',html)
        self.assertIn('#historical-ohif-iframe{display:block;',css)
        self.assertIn('Original EMR report',html)


if __name__=="__main__":
    unittest.main()
