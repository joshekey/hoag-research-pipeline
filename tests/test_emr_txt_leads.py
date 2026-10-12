"""Synthetic EMR name/DOS matching tests; no real patient data."""
import unittest
from emr_txt_leads import exam_dates, exam_terms, evaluate_emr
from txt_candidate_discovery import clues


class EMRLeadTests(unittest.TestCase):
    def setUp(self):
        self.study={"uid":"1.2.3.4","name":"DOE^JANE",
                    "patient":"PACSPAT001","accession":"PACSACC001",
                    "date":"20261001","modality":"MR"}

    def report(self,text):
        return clues(text,"some-emr-document")

    def evaluate(self,text,study=None):
        return evaluate_emr(study or self.study,self.report(text),
            exam_dates(text),*exam_terms(text),{"MR"},{"CERVICAL"})

    def test_name_dos_modality_anatomy_and_different_mrn_acc(self):
        text=("DOE, JANE (MR # EMR777) Admission Date: 09/30/2026\n"
              "Results MRI Cervical Spine wo Contrast (Accession EMR222)\n"
              "Date of Service: 10/01/2026\n"
              "MRI CERVICAL SPINE WITHOUT CONTRAST\n")
        candidate=self.evaluate(text)
        self.assertEqual(candidate["category"],"date_supported_lead")
        self.assertIn("same_exam_or_service_date",candidate["evidence"])
        self.assertIn("mrn_different_namespace_unverified",candidate["evidence"])
        self.assertIn("accession_different_namespace_unverified",candidate["evidence"])
        self.assertTrue(candidate["requires_manual_review"])

    def test_admission_discharge_print_and_signed_dates_not_dos(self):
        text=("DOE, JANE (MR # EMR777) Admission Date: 10/01/2026, "
              "Discharge Date: 10/03/2026\n"
              "Results MRI Cervical Spine wo Contrast\n"
              "Printed by EMPLOYEE at 10/01/2026\n"
              "Signed by REPORTER 10/01/2026\n")
        self.assertEqual(exam_dates(text),set())
        candidate=self.evaluate(text)
        self.assertEqual(candidate["category"],"anatomy_only_lead")

    def test_different_identifier_namespace_not_proof_of_identity(self):
        text=("DOE, JANE (MR # EMR777)\nDate of Service: 10/01/2026\n"
              "MRI CERVICAL SPINE\n")
        result=self.evaluate(text)
        self.assertIsNotNone(result)
        self.assertTrue(result["requires_manual_review"])
        self.assertTrue(result["cannot_approve_without_identity_and_exam_verification"])

    def test_name_only_cannot_match(self):
        self.assertIsNone(self.evaluate("DOE, JANE (MR # EMR777)\nNo scan title\n"))

    def test_dos_different_day_rejected(self):
        text=("DOE, JANE (MR # EMR777)\nDate of Service: 09/17/2026\n"
              "MRI CERVICAL SPINE WITHOUT CONTRAST\n")
        self.assertIsNone(self.evaluate(text))

    def test_patient_name_mismatch_blocks_same_dos(self):
        text=("DOE, JOHN (MR # EMR777)\nDate of Service: 10/01/2026\n"
              "MRI CERVICAL SPINE\n")
        self.assertIsNone(self.evaluate(text))

    def test_dos_without_modality_or_anatomy_not_enough(self):
        text="DOE, JANE (MR # EMR777)\nDate of Service: 10/01/2026\n"
        self.assertIsNone(self.evaluate(text))

    def test_adjacent_day_remains_manual_review(self):
        text=("DOE, JANE (MR # EMR777)\nExam Date: 10/02/2026\n"
              "MRI CERVICAL SPINE\n")
        result=self.evaluate(text)
        self.assertEqual(result["category"],"date_supported_lead")
        self.assertIn("adjacent_day_exam_or_service_date",result["evidence"])

    def test_result_date_alone_is_not_service_date(self):
        text=("DOE, JANE (MR # EMR777)\nResult Date: 10/01/2026\n"
              "MRI CERVICAL SPINE\n")
        self.assertEqual(exam_dates(text),set())

if __name__=="__main__":
    unittest.main()
