"""Synthetic HTTP checks: SQL identity incident must fail closed by default."""
import base64
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from werkzeug.security import generate_password_hash
from webapp import create_app, sql_review_permitted


class SQLReviewSafetyGateTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.config={
            "state_dir":str(self.root),
            "username":"synthetic",
            "password_hash":generate_password_hash("synthetic-password"),
            "secret_key":"synthetic-gate-secret",
            "source_roots":["/synthetic"],
            "output_root":"/synthetic-output",
            "secure_cookies":False,
        }
        self.app=create_app(self.config)
        self.client=self.app.test_client()
        self.auth={"Authorization":"Basic "+base64.b64encode(
            b"synthetic:synthetic-password").decode()}
        self.off=self.root/"sql-approval-off"
        self.gate_patch=patch("webapp.SQL_REVIEW_GATE",self.off)
        self.gate_patch.start()
        self.addCleanup(self.gate_patch.stop)

    def test_sql_gate_missing_denies_all_candidates_and_previews(self):
        for path in (
            "/api/study/1.2.3/sql-candidates?patient_name=SAMPLE%5EPERSON",
            "/api/study/1.2.3/sql-candidate/"+"a"*32,
            "/api/study/1.2.3/sql-draft",
        ):
            with self.subTest(path=path):
                r=self.client.get(path,headers=self.auth)
                self.assertEqual(r.status_code,503)
                self.assertIn("temporarily disabled",r.json["error"])

    def test_sql_gate_denies_mutating_routes(self):
        self.client.get("/",headers=self.auth)
        with self.app.test_request_context("/"):
            pass
        for path in ("/api/study/1.2.3/sql-association",
                     "/api/study/1.2.3/sql-draft"):
            with self.subTest(path=path):
                r=self.client.post(path,headers=self.auth,json={})
                self.assertIn(r.status_code,(403,503))
        self.assertFalse(sql_review_permitted())

    def test_sql_gate_symlink_fails_closed(self):
        target=self.root/"sql-target"
        target.write_text("AUTHORIZED")
        self.off.symlink_to(target)
        self.assertFalse(sql_review_permitted())

    def test_cross_patient_entry_rejected_when_gate_is_mocked_on(self):
        # Nothing reaches the SQL broker if an operator typed a different patient.
        with patch("webapp.sql_review_permitted",return_value=True),\
             patch("sql_gui_candidates.study_request",return_value={"name":"SAMPLE^PERSON"}) as study,\
             patch("sql_gui_candidates.candidates") as broker:
            r=self.client.get(
                "/api/study/1.2.3/sql-candidates?patient_name=OTHER%5EPERSON",
                headers=self.auth)
        self.assertEqual(r.status_code,400)
        study.assert_called_once()
        broker.assert_not_called()


if __name__=="__main__":
    unittest.main()
