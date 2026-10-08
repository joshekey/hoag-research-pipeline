"""Synthetic-only tests for strict patient-and-exam candidate selection."""
import unittest
from strict_identity_match import evaluate

class StrictIdentityTests(unittest.TestCase):
    report = "PATIENT NAME: EXAMPLE, ALICE MEDICAL RECORD NUMBER: OTHER123 DATE OF BIRTH: 2/3/1980 DATE OF STUDY: 9/2/2021 FINDINGS: synthetic"

    def test_all_three_match_despite_different_mrn(self):
        result = evaluate("EXAMPLE^ALICE", "19800203", "20210902", self.report)
        self.assertTrue(result["candidate"])
        self.assertTrue(result["requires_human_review"])

    def test_wrong_name_rejected(self):
        result = evaluate("DIFFERENT^ALICE", "19800203", "20210902", self.report)
        self.assertFalse(result["candidate"])
        self.assertIn("name", result["conflicting"])

    def test_wrong_dob_rejected(self):
        result = evaluate("EXAMPLE^ALICE", "19800204", "20210902", self.report)
        self.assertFalse(result["candidate"])

    def test_result_date_not_exam_date(self):
        text = "PATIENT NAME: EXAMPLE, ALICE MRN: ANY DOB: 2/3/1980 FINDINGS: synthetic"
        result = evaluate("EXAMPLE^ALICE", "19800203", "20210902", text, result_date="2021-09-02")
        self.assertFalse(result["candidate"])
        self.assertTrue(result["result_date_only"])

    def test_verified_exam_date(self):
        text = "PATIENT NAME: EXAMPLE, ALICE MRN: ANY DOB: 2/3/1980 FINDINGS: synthetic"
        result = evaluate("EXAMPLE^ALICE", "19800203", "20210902", text, verified_exam_date="2021-09-02")
        self.assertTrue(result["candidate"])

if __name__ == "__main__":
    unittest.main()
