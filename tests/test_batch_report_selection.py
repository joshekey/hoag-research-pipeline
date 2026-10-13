import hashlib
from pathlib import Path
import unittest

import batch_report_selection as selection
import engine
import test_batch_reconcile as fixtures


class ReportSelectionTests(unittest.TestCase):
    setUp = fixtures.BatchReconciliationTests.setUp
    setup_batch = fixtures.BatchReconciliationTests.setup_batch
    run_batch = fixtures.BatchReconciliationTests.run_batch

    def test_exact_first_last_with_middle_and_structured_names(self):
        for name in ('Alex Smith', 'Alex James Smith', 'SMITH, ALEX JAMES', 'Smith^Alex^James'):
            self.assertTrue(selection.exact_name(name, 'Alex', 'Smith'))
        for name in ('Alexander Smith', 'Alex Smithson', 'Smith Alex', 'Alex Smyth'):
            self.assertFalse(selection.exact_name(name, 'Alex', 'Smith'))
        self.assertFalse(selection.exact_name('Alex Oneil', 'Alex', "O'Neil"))
        self.assertTrue(selection.exact_name('de la Cruz, Mary Ann Jane', 'Mary Ann', 'de la Cruz'))

    def test_name_only_is_sufficient_and_missing_dob_and_mrn_are_allowed(self):
        patients = [{'': 'P001', 'FNAME': 'Alex', 'LNAME': 'Smith'}]
        result = selection.evaluate('Patient Name: Smith, Alex\nFindings: synthetic.', patients, {})
        self.assertEqual(result['status'], 'selected_by_exact_name')
        self.assertFalse(result['accepted'])
        self.assertFalse(result['release_allowed'])

    def test_shared_name_multiple_names_and_explicit_identifier_conflicts_held(self):
        patients = [{'': 'P001', 'FNAME': 'Alex', 'LNAME': 'Smith'}]
        other = dict(patients[0]); other[''] = 'P002'
        self.assertIn('shared_first_last_name', selection.evaluate('Name: Alex Smith', patients+[other], {})['reasons'])
        self.assertEqual(selection.evaluate('Name: Alex Smith\nName: Other Person', patients, {})['status'], 'held')
        for text in ('Name: Alex Smith\nMRN: BAD', 'Smith, Alex (MR # BAD)', 'Name: Alex Smith\nMRN: OK\nMRN: BAD'):
            self.assertIn('explicit_identifier_conflict', selection.evaluate(text, patients, {'P001': {'OK'}})['reasons'])
        self.assertIn('identifier_not_verifiable', selection.evaluate('Name: Alex Smith\nMRN: X', patients, {})['reasons'])
        self.assertIn('crosswalk_patient_conflict', selection.evaluate('Name: Alex Smith', patients, {}, {'P002'})['reasons'])
        self.assertEqual(selection.evaluate('The referring doctor is Alex Smith.', patients, {})['status'], 'held')

    def test_full_index_selection_without_exam_matching_preserves_sources_and_database(self):
        self.setup_batch()
        manifest = self.run_batch()
        self.report.write_text('Patient Name: Synthetic, Person\nFindings: Synthetic only.')
        extra = self.sources[1]/'extra.txt'
        extra.write_text('Patient Name: Person Synthetic\nFindings: additional report.')
        engine.scan(self.config)
        paths = [self.path, self.report, extra, Path(self.config['state_dir'])/'workflow.sqlite']
        before = {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
        result = selection.select_reports(manifest, self.config, self.mounted)
        self.assertEqual(result['counts']['selected_by_exact_name'], 2)
        self.assertFalse(result['release_allowed'])
        self.assertEqual(before, {str(p): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths})
        self.assertNotIn('Synthetic only', str(result))

    def test_changed_source_held(self):
        self.setup_batch()
        manifest = self.run_batch()
        self.report.write_text('Name: Person Synthetic')
        result = selection.select_reports(manifest, self.config, self.mounted)
        self.assertEqual(result['counts'].get('selected_by_exact_name', 0), 0)
        self.assertEqual(result['reason_counts']['source_or_index_validation_failed'], 1)

    def test_inactive_index_entry_is_held(self):
        import sqlite3
        self.setup_batch()
        manifest = self.run_batch()
        with sqlite3.connect(Path(self.config['state_dir'])/'workflow.sqlite') as db:
            db.execute("UPDATE files SET active=0 WHERE kind='report'")
        db.close()
        result = selection.select_reports(manifest, self.config, self.mounted)
        self.assertEqual(result['counts'].get('selected_by_exact_name', 0), 0)
        self.assertEqual(result['reason_counts']['inactive_or_error_index_entry'], 1)

    def test_shared_identifier_stays_held(self):
        patients = [{'': 'P001', 'FNAME': 'Alex', 'LNAME': 'Smith'}]
        result = selection.evaluate('Name: Alex Smith\nMRN: A', patients, {'P001': {'A'}, 'P002': {'A'}})
        self.assertIn('identifier_shared_with_other_patient', result['reasons'])
