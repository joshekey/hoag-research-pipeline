"""Synthetic tests for the read-only SQL identity audit. No hospital data."""
import unittest
from ohif_poc.sql_identity_audit import report_identity_classification


class SQLIdentityAuditTests(unittest.TestCase):
    def setUp(self):
        self.study={
            "name":"PERSON SAMPLE","dob":"19800203",
            "date":"20210902","modality":"MR",
        }
        self.base="PATIENT NAME: SAMPLE, PERSON MRN: FAKE DATE OF BIRTH: 02/03/1980\n"

    def test_consistent_name_dob_still_needs_exam_verification(self):
        self.assertEqual(report_identity_classification(
            self.study,self.base+"FINDINGS: synthetic", "MRI LUMBAR"),
            "identity_consistent_candidate_needs_exam_verification")

    def test_secondary_patient_declaration_is_conflict(self):
        self.assertEqual(report_identity_classification(
            self.study,self.base+"Patient Name - OTHER, PERSON\n", "MRI LUMBAR"),
            "conflicting_patient_name_declarations")

    def test_different_primary_patient_is_excluded(self):
        self.assertEqual(report_identity_classification(
            self.study,"PATIENT NAME: OTHER, PERSON MRN: FAKE", "MRI LUMBAR"),
            "different_primary_patient_name")

    def test_dob_conflict_is_excluded(self):
        self.assertEqual(report_identity_classification(
            self.study,"PATIENT NAME: SAMPLE, PERSON MRN: FAKE DATE OF BIRTH: 01/01/1990",
            "MRI LUMBAR"),"conflicting_dob")

    def test_exam_date_disagreement_is_excluded(self):
        self.assertEqual(report_identity_classification(
            self.study,self.base+"EXAM DATE: 01/01/2020\n","MRI LUMBAR"),
            "conflicting_exam_date")

    def test_wrong_modality_is_excluded(self):
        self.assertEqual(report_identity_classification(
            self.study,self.base,"CT CHEST"),"conflicting_modality")


if __name__=="__main__":
    unittest.main()
