import unittest
from types import SimpleNamespace
import engine


class Detector:
    def analyze(self, text, **kwargs):
        self.kwargs = kwargs
        # Simulate the false positives from generic NLP even if the detector
        # ignores our requested entity list. Clinical spans must survive.
        results = []
        for word, kind in [('LUMBOSACRAL', 'PERSON'), ('T12-L1', 'DATE_TIME'),
                           ('MRI', 'ORGANIZATION'), ('AP', 'LOCATION'),
                           ('one year', 'DATE_TIME'), ('T2', 'PERSON')]:
            if word in text:
                start = text.index(word)
                results.append(SimpleNamespace(start=start, end=start+len(word), entity_type=kind))
        return results


class ClinicalRedactionTests(unittest.TestCase):
    def test_clinical_body_is_preserved_exactly(self):
        text = ('MAGNETIC RESONANCE IMAGING OF THE LUMBOSACRAL SPINE WITHOUT CONTRAST\n'
                'REASON FOR EXAM: 79-year-old describes one year of pain.\n'
                'TECHNIQUE: Sagittal T1-weighted, T2-weighted MRI.\n'
                "LABS: Patient's laboratory findings met department guidelines.\n"
                'FINDINGS: At T12-L1, a 3 mm AP disc bulge. L4-5 and L5-S1.\n'
                'IMPRESSION: Mild central spinal canal stenosis.\n')
        detector = Detector()
        self.assertEqual(engine.sanitize_report(text, [], detector), text)
        self.assertNotIn('PERSON', detector.kwargs['entities'])

    def test_identifiers_in_body_and_headers_are_removed(self):
        text = ('Patient Name: Example^Jane\nMRN: MRN12345\nDOB: 01/02/1970\n'
                'Findings: Jane Example (MRN12345) has a 3 mm AP bulge at T12-L1.\n'
                'IMPRESSION: MRI shows no acute findings. Contact jane@example.test.\n')
        clean = engine.sanitize_report(text, ['Example^Jane', 'MRN12345'], Detector())
        for value in ['Example', 'Jane', 'MRN12345', '01/02/1970', 'jane@example.test']:
            self.assertNotIn(value, clean)
        self.assertIn('3 mm AP bulge at T12-L1', clean)
        self.assertIn('MRI shows no acute findings', clean)

    def test_known_identifier_is_not_exempted_by_clinical_spelling(self):
        # A genuine patient name may also be an ordinary or clinical word.
        clean = engine.sanitize_report('Findings: May has an MRI.', ['May'], Detector())
        self.assertEqual(clean, 'Findings: [REDACTED] has an MRI.')

    def test_only_valid_full_dates_are_removed(self):
        text = 'MRI 10/5/2012 and 2020-04-17; level 12-1-3; 3 mm; 20200417.'
        clean = engine.sanitize_report(text, [], Detector())
        self.assertNotIn('10/5/2012', clean)
        self.assertNotIn('2020-04-17', clean)
        self.assertNotIn('20200417', clean)
        self.assertIn('3 mm', clean)
