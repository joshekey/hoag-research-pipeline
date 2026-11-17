import json
import unittest
import pydicom
import engine
import test_workflow as workflow
from store import database


class StudyConsistencyTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    study = workflow.WorkflowTests.study
    prepare = workflow.WorkflowTests.prepare
    approve = workflow.WorkflowTests.approve

    def add_image(self, **fields):
        path = self.sources[0] / 'second.dcm'
        ds = workflow.image(path, self.uid, self.series)
        for field, value in fields.items():
            setattr(ds, field, value)
        ds.save_as(path, enforce_file_format=True)

    def test_dob_conflict_quarantines_study(self):
        ds = pydicom.dcmread(self.path)
        ds.PatientBirthDate = '19800101'
        ds.save_as(self.path, enforce_file_format=True)
        self.add_image(PatientBirthDate='19700101')
        engine.scan(self.config)
        self.assertEqual(self.study()['state'], 'conflict')
        self.assertIn('date of birth', self.study()['last_error'])
        with self.assertRaises(ValueError):
            self.prepare()

    def test_study_date_conflict_quarantines_study(self):
        self.add_image(StudyDate='20210102')
        engine.scan(self.config)
        self.assertEqual(self.study()['state'], 'conflict')
        self.assertIn('study date', self.study()['last_error'])
        with self.assertRaises(ValueError):
            engine.export_study(self.config, self.uid)

    def test_missing_dates_do_not_create_a_contradiction(self):
        self.add_image(StudyDate='', PatientBirthDate='19700101')
        engine.scan(self.config)
        self.assertNotEqual(self.study()['state'], 'conflict')
        self.approve()
        folder = engine.export_study(self.config, self.uid)
        self.assertEqual(len(list((folder/'DICOM').glob('*.dcm'))), 2)

    def test_existing_catalog_is_checked_without_requiring_a_rescan(self):
        self.add_image()
        engine.scan(self.config)
        self.approve()
        # Model an older catalog whose conflict was not detected at ingestion.
        with database(self.config) as db:
            row = db.execute("SELECT * FROM files WHERE kind='dicom' ORDER BY id DESC LIMIT 1").fetchone()
            meta = json.loads(row['metadata']); meta['StudyDate'] = '20210102'
            db.execute('UPDATE files SET metadata=? WHERE id=?', (json.dumps(meta), row['id']))
        with self.assertRaisesRegex(ValueError, 'Conflicting DICOM'):
            engine.export_study(self.config, self.uid)
        engine.reopen_review(self.config, self.uid, workflow.revision(self.config, self.uid))
        with self.assertRaisesRegex(ValueError, 'Conflicting DICOM'):
            engine.approve(self.config, self.uid, self.study()['sanitized'], [], 'Synthetic review',
                           expected_revision=workflow.revision(self.config, self.uid))
        with self.assertRaisesRegex(ValueError, 'Conflicting DICOM'):
            self.prepare()
