import copy
from pathlib import Path
import unittest
import pydicom
import batch_readiness as readiness
from test_batch_reconcile import BatchReconciliationTests


class ReadinessTests(unittest.TestCase):
    setUp = BatchReconciliationTests.setUp
    setup_batch = BatchReconciliationTests.setup_batch
    run_batch = BatchReconciliationTests.run_batch

    def prepare(self):
        self.setup_batch()
        ds = pydicom.dcmread(self.path)
        ds.IssuerOfPatientID = 'SYNTHETIC'
        ds.save_as(self.path, enforce_file_format=True)

    def analyze(self):
        return readiness.analyze(self.run_batch(), self.config, self.mounted)

    def test_ready_requires_supported_links_and_never_accepts(self):
        self.prepare()
        result = self.analyze()
        self.assertEqual(result['counts']['verified_for_anonymization_review'], 1)
        self.assertEqual(result['accepted_associations'], 0)
        self.assertFalse(result['release_allowed'])

    def test_identical_copies_are_not_identity_conflicts(self):
        self.prepare()
        (self.sources[0]/'copy.dcm').write_bytes(self.path.read_bytes())
        result = self.analyze()
        self.assertEqual(result['counts']['duplicate_identical_bytes_groups'], 1)
        self.assertEqual(result['counts']['verified_for_anonymization_review'], 1)
        self.assertTrue((self.sources[0]/'copy.dcm').exists())

    def test_different_bytes_same_uid_remain_blocked(self):
        self.prepare()
        ds = pydicom.dcmread(self.path)
        ds.PatientAge = '051Y'
        ds.save_as(self.sources[0]/'different.dcm', enforce_file_format=True)
        result = self.analyze()
        self.assertEqual(result['counts']['duplicate_different_bytes_unresolved_groups'], 1)
        self.assertEqual(result['counts']['verified_for_anonymization_review'], 0)

    def test_missing_and_conflicting_identity_are_distinct(self):
        self.assertEqual(readiness.classify_values('PatientID', ['', '123']), 'missing_values')
        self.assertEqual(readiness.classify_values('PatientID', ['123', '124']), 'conflicting_populated_values')
        self.assertEqual(readiness.classify_values('PatientName', ['Sample^Alex', 'Alex Sample']), 'formatting_only')

    def test_changed_report_blocks_readiness(self):
        self.prepare()
        manifest = self.run_batch()
        self.report.write_text('changed synthetic report')
        result = readiness.analyze(manifest, self.config, self.mounted)
        self.assertEqual(result['counts']['verified_for_anonymization_review'], 0)

    def test_unrelated_comparison_does_not_block_supported_study(self):
        self.prepare()
        manifest = self.run_batch()
        manifest['exceptions'].append({'code': 'association_needs_review', 'label': 'P001'})
        result = readiness.analyze(manifest, self.config, self.mounted)
        self.assertEqual(result['counts']['verified_for_anonymization_review'], 1)

    def test_cross_patient_uid_reuse_blocks_both_patients(self):
        self.prepare()
        manifest = self.run_batch()
        other = copy.deepcopy(manifest['private_crosswalk'][0])
        other[''] = 'P002'; other['PATIENTID'] = 'INTERNAL2'
        manifest['private_crosswalk'].append(other)
        result = readiness.analyze(manifest, self.config, self.mounted)
        self.assertEqual(result['counts']['verified_for_anonymization_review'], 0)
        self.assertEqual(result['counts']['conflicted'], 2)

    def test_patient_bundle_does_not_require_exam_pairing(self):
        self.prepare()
        manifest = self.run_batch()
        for study in manifest['studies']:
            study['associations'] = []
            manifest['exceptions'].append({'code': 'study_without_readable_mapped_report', 'label': study['label']})
        result = readiness.analyze(manifest, self.config, self.mounted)
        self.assertEqual(result['counts']['verified_for_anonymization_review'], 1)
        self.assertFalse(result['patient_report_checks'][0]['accepted'])

    def test_wrong_patient_report_is_still_blocked_without_exam_matching(self):
        self.prepare()
        self.report.write_text('Patient Name: Other^Patient\nMRN: OTHER\nDOB: 01/01/1970\n')
        import engine
        engine.scan(self.config)
        result = self.analyze()
        self.assertEqual(result['counts']['verified_for_anonymization_review'], 0)
        self.assertFalse(result['patient_report_checks'][0]['independent_patient_evidence'])

    def test_duplicate_pixel_classification(self):
        from batch_root_causes import compare_pixel_objects
        self.prepare()
        first = pydicom.dcmread(self.path)
        other = copy.deepcopy(first)
        other.PatientAge = '099Y'
        self.assertEqual(compare_pixel_objects([first, other])[0], 'same_stored_pixels_nonpixel_bytes_differ')
        from pydicom.uid import ImplicitVRLittleEndian, ExplicitVRLittleEndian
        other.file_meta.TransferSyntaxUID = (ImplicitVRLittleEndian if first.file_meta.TransferSyntaxUID != ImplicitVRLittleEndian else ExplicitVRLittleEndian)
        self.assertEqual(compare_pixel_objects([first, other])[0], 'same_decoded_pixels_encoding_differs')
        other.file_meta.TransferSyntaxUID = first.file_meta.TransferSyntaxUID
        other.RescaleSlope = '2'
        self.assertFalse(compare_pixel_objects([first, other])[1])
        raw = bytearray(other.PixelData)
        raw[0] ^= 1
        other.PixelData = bytes(raw)
        self.assertEqual(compare_pixel_objects([first, other])[0], 'decoded_pixels_differ')
        del other.PixelData
        with self.assertRaises(ValueError): compare_pixel_objects([first, other])

    def test_attribute_scope_and_namespace_candidates(self):
        from batch_root_causes import identity_breakdown
        objects = {}
        for i, (study, issuer, pid) in enumerate([('S1', 'A', '1'), ('S2', 'B', '2')]):
            objects[str(i)] = {'owners': {'P001'}, 'path': str(i), 'study': study,
                'metadata': dict(PatientName='Sample^Alex', PatientID=pid,
                    IssuerOfPatientID=issuer, PatientBirthDate='19700101', StudyDate='20200101')}
        result = identity_breakdown(objects)
        self.assertFalse(any(r['scope'] == 'within_study' for r in result['private_fields']))
        self.assertTrue(result['unconfirmed_explanations'])
        self.assertIsNone(result['confirmed_cross_patient_conflicts'])
        self.assertEqual(result['aggregate_fields'][0]['unique_studies'], 2)

    def test_lossy_name_normalization_is_not_cleared(self):
        from batch_root_causes import identity_breakdown
        objects = {str(i): {'owners': {'P001'}, 'path': str(i), 'study': 'S1',
            'metadata': dict(PatientName=name, PatientID='1', IssuerOfPatientID='A',
                PatientBirthDate='19700101', StudyDate='20200101')}
            for i, name in enumerate(['Smith^Alex', 'Alex^Smith'])}
        self.assertEqual(len(identity_breakdown(objects)['normalization_audit']), 1)
