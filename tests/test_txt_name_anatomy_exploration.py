"""Pure synthetic safety tests for exploratory TXT name/modality/anatomy leads."""
import unittest
from txt_candidate_discovery import clues
from txt_name_anatomy_exploration import evaluate_exploratory


class ExploratoryTXTTests(unittest.TestCase):
    def setUp(self):
        self.study={"uid":"1.2.3.4","name":"DOE^JANE","accession":"T10001","patient":"MR111"}

    def test_name_modality_anatomy_forms_exploratory_lead(self):
        report=clues("Patient Name: Jane Doe\nEXAM: CT LUMBAR SPINE")
        candidate=evaluate_exploratory(self.study,report,{"CT"},{"LUMBAR"})
        self.assertEqual(candidate["category"],"exploratory")
        self.assertEqual(set(candidate["evidence"]),{"name","modality","anatomy"})
        self.assertTrue(candidate["manual_review_only"])

    def test_name_only_insufficient(self):
        report=clues("Patient Name: Jane Doe\nClinical history unremarkable")
        self.assertIsNone(evaluate_exploratory(self.study,report,{"CT"},{"LUMBAR"}))

    def test_modality_anatomy_without_name_insufficient(self):
        report=clues("EXAM: CT LUMBAR SPINE")
        self.assertIsNone(evaluate_exploratory(self.study,report,{"CT"},{"LUMBAR"}))

    def test_explicit_identifier_conflict_not_a_candidate(self):
        report=clues("Patient Name: Jane Doe\nAccession: T99999\nEXAM: CT LUMBAR SPINE")
        candidate=evaluate_exploratory(self.study,report,{"CT"},{"LUMBAR"})
        self.assertEqual(candidate["category"],"conflict")
        self.assertIn("accession",candidate["conflicts"])


if __name__=="__main__":
    unittest.main()
