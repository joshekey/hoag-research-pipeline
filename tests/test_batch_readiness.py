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
