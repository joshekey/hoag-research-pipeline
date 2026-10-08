"""Synthetic SQL/DICOM candidate diagnostics; no hospital data."""
import unittest
from sql_dicom_pilot import compare, compare_all, regions, modalities

STUDY = {
    "name": "SAMPLE^PERSON",
    "dob": "19800203",
    "date": "20210902",
    "modalities": {"MR"},
    "regions": {"LUMBAR"},
}
REPORT = {
    "narrative": "PATIENT NAME: PERSON, SAMPLE MRN: ALT DOB: 2/3/1980 DATE OF STUDY: 9/2/2021 FINDINGS: synthetic",
    "procedure": b"MRI LUMBAR SPINE",
    "result_date": b"2021-09-02",
}

class PilotTests(unittest.TestCase):
    def test_verified_exam_date(self):
        result = compare(STUDY, REPORT)
        self.assertTrue(result["strict_candidate"])
        self.assertFalse(result["name_dob_result_date_support"])

    def test_result_date_is_not_exam_date(self):
        report = dict(REPORT, narrative="PATIENT NAME: PERSON, SAMPLE MRN: ALT DOB: 2/3/1980 FINDINGS: synthetic")
        result = compare(STUDY, report)
        self.assertFalse(result["strict_candidate"])
        self.assertTrue(result["name_dob_result_date_support"])

    def test_wrong_patient_excluded(self):
        report = dict(REPORT, narrative="PATIENT NAME: OTHER, PERSON MRN: ALT DOB: 2/3/1980 DATE OF STUDY: 9/2/2021")
        counts = compare_all([STUDY], [report])
        self.assertEqual(counts[0]["name_dob"], 0)

    def test_anatomy_conflict(self):
        report = dict(REPORT, procedure="MRI CERVICAL SPINE")
        result = compare(STUDY, report)
        self.assertFalse(result["strict_candidate"])
        self.assertEqual(result["region"], "conflict")

    def test_modality_conflict(self):
        report = dict(REPORT, procedure="CT LUMBAR SPINE")
        result = compare(STUDY, report)
        self.assertFalse(result["strict_candidate"])
        self.assertEqual(result["modality"], "conflict")

    def test_unknown_exam_not_strict(self):
        report = dict(REPORT, procedure="OTHER PROCEDURE")
        result = compare(STUDY, report)
        self.assertFalse(result["strict_candidate"])
        self.assertEqual(result["modality"], "unknown")

    def test_normalization(self):
        self.assertEqual(regions("MR L-SPINE"), {"LUMBAR"})
        self.assertIn("MR", modalities(b"MRI LUMBAR"))

if __name__ == "__main__":
    unittest.main()
