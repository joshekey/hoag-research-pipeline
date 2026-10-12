"""Synthetic scoring tests; scores are not probabilities or approval."""
import unittest
from candidate_scoring import score
from strict_identity_match import extract_header

STUDY = {"name":"SAMPLE^PATIENT", "dob":"19800102", "date":"20210902",
         "modality":"MR", "regions":["LUMBAR"]}
REPORT = {"procedure":b"MRI LUMBAR SPINE", "result_date":b"2021-09-02"}
TEXT = "PATIENT NAME: PATIENT, SAMPLE MRN: A1 DATE OF BIRTH: 1/2/1980 FINDINGS: synthetic"

class ScoringTests(unittest.TestCase):
    def test_result_date_is_not_exam_date(self):
        result=score(STUDY,REPORT,extract_header(TEXT))
        self.assertEqual(result["score"],75)
        self.assertEqual(result["evidence"]["exam_date"],"result-date-only")
        self.assertTrue(result["result_date_support"])
        self.assertTrue(result["manual_review_required"])

    def test_missing_dob_lowers_score(self):
        study=dict(STUDY,dob="")
        result=score(study,REPORT,extract_header(TEXT))
        self.assertEqual(result["score"],50)
        self.assertEqual(result["evidence"]["dob"],"missing")

    def test_conflicting_dob_is_not_missing(self):
        study=dict(STUDY,dob="19790102")
        result=score(study,REPORT,extract_header(TEXT))
        self.assertEqual(result["evidence"]["dob"],"conflict")
        self.assertIn("dob",result["conflicts"])

    def test_verified_exam_date_full_score(self):
        result=score(STUDY,REPORT,extract_header(TEXT+" EXAM DATE: 9/2/2021"))
        self.assertEqual(result["score"],100)

    def test_exam_type_conflict_not_hidden(self):
        result=score(STUDY,dict(REPORT,procedure=b"MRI CERVICAL SPINE"),extract_header(TEXT))
        self.assertEqual(result["evidence"]["exam_type"],"conflict")
        self.assertIn("exam_type",result["conflicts"])

if __name__=="__main__":
    unittest.main()
