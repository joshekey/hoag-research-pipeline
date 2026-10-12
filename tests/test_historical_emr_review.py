"""Synthetic protection checks for historical EMR review preview."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

from werkzeug.security import generate_password_hash

from webapp import create_app
from historical_emr_review import checked_report_identity


class HistoricalEMRReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        config={
            "state_dir":self.tmp.name,
            "username":"synthetic",
            "password_hash":generate_password_hash("safe-test-password"),
            "secret_key":"synthetic-key",
            "source_roots":["/unused"],
            "output_root":"/unused-output",
            "secure_cookies":False,
        }
        self.app=create_app(config)
        self.client=self.app.test_client()
        self.auth={"Authorization":"Basic "+base64.b64encode(
            b"synthetic:safe-test-password").decode()}

    def test_study_specific_review_payload_and_selection_contract(self):
        from historical_emr_review import listing
        fake=[
            {"token":"a"*32,"instances":120,"study":{"dates":{"20261001"},
             "modalities":{"MR"}}},
            {"token":"b"*32,"instances":228,"study":{"dates":{"20261002"},
             "modalities":{"CT"}}},
        ]
        with patch("historical_emr_review.eligible_pairs",return_value=fake):
            result=listing({})
        self.assertTrue(result["independent_from_active_catalog"])
        self.assertEqual([r["study_ref"] for r in result["candidates"]],
                         ["AAAAAAAA","BBBBBBBB"])
        self.assertEqual([r["instance_count"] for r in result["candidates"]],
                         [120,228])

    def test_historical_frontend_cannot_reuse_stale_selection(self):
        root=Path(__file__).resolve().parents[1]
        js=(root/"static/app.js").read_text()
        self.assertIn("historicalSelectedToken=lead.token",js)
        self.assertIn("serial!==historicalSelectionSerial",js)
        self.assertIn("preview.historical_study_ref!==lead.study_ref",js)
        self.assertIn("frame.removeAttribute('src')",js)

    def test_blue_workspace_screens_and_selected_study_navigation(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"templates/index.html").read_text()
        js=(root/"static/app.js").read_text()
        css=(root/"static/app.css").read_text()
        for screen in ("overview","studies","review","matching","packages","administration"):
            self.assertIn('href="#'+screen+'"',html)
            self.assertIn("'"+screen+"'",js)
        self.assertIn("window.addEventListener('hashchange',showWorkspaceScreen)",js)
        self.assertIn("window.location.hash='#review'",js)
        self.assertIn("updateSelectedStudyBanner(d.study)",js)
        self.assertIn("if(effective==='studies'&&screen==='review'",js)
        self.assertIn("--accent:#65aaff",css)
        self.assertIn("#review-report-overview",css)

    def test_review_automatically_looks_up_study_reports(self):
        root=Path(__file__).resolve().parents[1]
        js=(root/"static/app.js").read_text()
        html=(root/"templates/index.html").read_text()
        start=js.index("async function openStudy(value)")
        end=js.index("async function loadSQLDraft()",start)
        review=js[start:end]
        self.assertIn("const sqlTotal=await loadSQLCandidates()",review)
        self.assertIn("const txtCount=d.candidates.length",review)
        self.assertIn("generation!==reviewRequest||uid!==value",review)
        self.assertIn('id="review-report-match-status"',html)
        self.assertIn('id="review-report-match-results"',html)

    def test_report_reuse_across_studies_is_rejected(self):
        from historical_emr_review import unique_report_assignments
        self.assertEqual(unique_report_assignments({
            "1.2.3":[10], "1.2.4":[10]}),{})

    def test_ambiguous_study_is_rejected_not_ranked(self):
        from historical_emr_review import unique_report_assignments
        self.assertEqual(unique_report_assignments({
            "1.2.3":[10,11], "1.2.4":[12]}),{"1.2.4":12})

    def test_distinct_study_report_pairs_remain_eligible(self):
        from historical_emr_review import unique_report_assignments
        self.assertEqual(unique_report_assignments({
            "1.2.3":[10], "1.2.4":[11]}),{"1.2.3":10,"1.2.4":11})

    def test_conflicting_displayed_patient_name_is_blocked(self):
        report = ('DOE, JANE (MR # EMR111)\n'
                  'Patient Name - OTHER, JOHN\n'
                  'Date of Service: 10/01/2026\nMRI CERVICAL SPINE\n')
        self.assertEqual(checked_report_identity(report,'DOE^JANE'),'conflict')

    def test_consistent_names_still_require_manual_verification(self):
        report = ('DOE, JANE (MR # EMR111)\n'
                  'Patient Name - JANE DOE\n'
                  'Date of Service: 10/01/2026\nMRI CERVICAL SPINE\n')
        self.assertEqual(checked_report_identity(report,'DOE^JANE'),'consistent')

    def test_unrecognized_identity_fails_closed(self):
        self.assertEqual(checked_report_identity('MRI CERVICAL SPINE','DOE^JANE'),'unverified')

    def test_unauthenticated_listing_denied(self):
        self.assertEqual(self.client.get("/api/historical-emr/candidates").status_code,401)
        self.assertEqual(self.client.get("/api/historical-emr/candidate/"+"a"*32).status_code,401)

    def test_unreadable_gate_fails_closed_without_http_500(self):
        gate=MagicMock()
        gate.is_symlink.side_effect=PermissionError("protected path")
        with patch("historical_emr_review.GATE",gate):
            from historical_emr_review import permitted
            self.assertFalse(permitted())
            response=self.client.get("/api/historical-emr/candidates",
                                     headers=self.auth)
        self.assertEqual(response.status_code,404)

    def test_no_root_gate_fails_closed(self):
        with patch("historical_emr_review.permitted",return_value=False):
            self.assertEqual(self.client.get("/api/historical-emr/candidates",
                headers=self.auth).status_code,404)
            self.assertEqual(self.client.get("/api/historical-emr/candidate/"+"a"*32,
                headers=self.auth).status_code,404)

    def test_authenticated_listing_when_authorized(self):
        with patch("historical_emr_review.permitted",return_value=True), \
             patch("historical_emr_review.listing",return_value={
                "count":1,"review_only":True,"candidates":[{
                "token":"a"*32,"label":"Historical candidate 1",
                "instance_count":5,"evidence":["exact patient name"],
                "status":"Unconfirmed"}]}):
            res=self.client.get("/api/historical-emr/candidates",headers=self.auth)
        self.assertEqual(res.status_code,200)
        self.assertTrue(res.json["review_only"])
        self.assertEqual(res.headers["Cache-Control"],"no-store")

    def test_authenticated_review_preview_no_approval(self):
        with patch("historical_emr_review.permitted",return_value=True), \
             patch("historical_emr_review.preview",return_value={
                "report_text":"SYNTHETIC REPORT",
                "dicom_patient_name":"SYNTHETIC",
                "dicom_study_date":"20000101",
                "dicom_modality":"MR",
                "dicom_instances":5,
                "review_only":True,
                "ohif_available":False,
                "evidence":["exact patient name"]}):
            res=self.client.get("/api/historical-emr/candidate/"+"a"*32,headers=self.auth)
        self.assertEqual(res.status_code,200)
        self.assertEqual(res.json["report_text"],"SYNTHETIC REPORT")
        self.assertTrue(res.json["review_only"])
        self.assertNotIn("approved",res.json)

    def test_preview_unknown_token_404(self):
        with patch("historical_emr_review.permitted",return_value=True), \
             patch("historical_emr_review.preview",side_effect=LookupError):
            self.assertEqual(self.client.get(
                "/api/historical-emr/candidate/"+"a"*32,
                headers=self.auth).status_code,404)

    def test_no_write_endpoints(self):
        with patch("historical_emr_review.permitted",return_value=True):
            self.assertEqual(self.client.post(
                "/api/historical-emr/candidates",headers=self.auth,
                data=b"not allowed").status_code,403)

    def test_panel_does_not_offer_approval_or_export(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/"templates/index.html").read_text()
        self.assertIn('id="historical-emr-review"',html)
        panel=html.split('id="historical-emr-review"',1)[1].split(
            '<section id="review"',1)[0]
        self.assertNotIn("Approve",panel)
        self.assertNotIn("Export approved",panel)
        self.assertIn('id="historical-emr-report"',panel)


if __name__=="__main__":
    unittest.main()
