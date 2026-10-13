import base64
import csv
import io
import json
import unittest
from unittest.mock import patch

import engine
import pydicom
from pydicom.uid import generate_uid
import patient_identity
import test_workflow as workflow
from store import database, initialize
from webapp import create_app


class PatientIdentityTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    study = workflow.WorkflowTests.study
    prepare = workflow.WorkflowTests.prepare
    approve = workflow.WorkflowTests.approve

    def test_same_patient_multiple_studies_and_update_preserve_identity(self):
        first = self.study()['subject']
        with database(self.config) as db:
            second = patient_identity.subject_for_patient(self.config, db, 'SYNTHETIC123', 'SYNTHETIC_HOSPITAL', 'other-study')
            other_issuer = patient_identity.subject_for_patient(self.config, db, 'SYNTHETIC123', 'OTHER_HOSPITAL', 'third-study')
            other_patient = patient_identity.subject_for_patient(self.config, db, 'OTHER_MRN', 'SYNTHETIC_HOSPITAL', 'fourth-study')
        self.assertEqual(first, second)
        self.assertNotEqual(first, other_issuer)
        self.assertNotEqual(first, other_patient)
        before = self.study()
        with patch('engine.pydicom.dcmread', side_effect=AssertionError('Update must not read DICOM')):
            initialize(self.config)
        self.assertEqual(before, self.study())
        with database(self.config) as db:
            changed_config = dict(self.config, secret_key='changed-secret')
            self.assertEqual(first, patient_identity.subject_for_patient(changed_config, db, 'SYNTHETIC123', 'SYNTHETIC_HOSPITAL', 'new-study'))

    def test_export_records_snapshot_and_crosswalk_is_authenticated_audited(self):
        self.approve()
        folder = engine.export_study(self.config, self.uid)
        self.assertFalse(list(folder.glob('*crosswalk*')))
        with database(self.config) as db:
            db.execute("UPDATE studies SET name='Changed name',patient='Changed MRN' WHERE uid=?", (self.uid,))
        app = create_app(self.config)
        client = app.test_client()
        self.assertEqual(client.post('/api/exports/crosswalk').status_code, 401)
        headers = {'Authorization': 'Basic '+base64.b64encode(b'admin:synthetic-password').decode()}
        self.assertEqual(client.post('/api/exports/crosswalk', headers=headers).status_code, 403)
        client.get('/', headers=headers)
        with client.session_transaction() as session:
            headers['X-CSRF-Token'] = session['csrf']
        result = client.post('/api/exports/crosswalk', headers=headers)
        self.assertEqual(result.status_code, 200)
        self.assertEqual(result.headers['Cache-Control'], 'no-store')
        rows = list(csv.DictReader(io.StringIO(result.data.decode('utf-8-sig'))))
        self.assertEqual(rows[0]['Original MRN'], 'SYNTHETIC123')
        self.assertEqual(rows[0]['Original patient name'], 'Synthetic^Person')
        self.assertEqual(rows[0]['Anonymous name'], rows[0]['Anonymous patient ID'])
        self.assertEqual(rows[0]['Mapping status'], 'Recorded at export')
        with database(self.config) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='download-patient-crosswalk'").fetchone()[0], 1)

    def test_two_real_exports_keep_identical_patient_tags_and_distinct_study_uids(self):
        self.approve()
        first = engine.export_study(self.config, self.uid)
        first_tags = pydicom.dcmread(next((first/'DICOM').glob('*.dcm')), stop_before_pixels=True)
        self.uid = generate_uid()
        second_path = self.sources[0]/'second-study'
        workflow.image(second_path, self.uid, generate_uid())
        engine.scan(self.config)
        self.approve()
        second = engine.export_study(self.config, self.uid)
        second_tags = pydicom.dcmread(next((second/'DICOM').glob('*.dcm')), stop_before_pixels=True)
        self.assertEqual(first_tags.PatientID, second_tags.PatientID)
        self.assertEqual(first_tags.PatientName, second_tags.PatientName)
        self.assertNotEqual(first_tags.StudyInstanceUID, second_tags.StudyInstanceUID)
        with database(self.config) as db:
            rows = list(csv.DictReader(io.StringIO(patient_identity.crosswalk_csv(db).lstrip('\ufeff'))))
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0]['Anonymous patient ID'], rows[1]['Anonymous patient ID'])

    def test_missing_patient_and_changed_subject_block_export(self):
        with database(self.config) as db:
            s = self.study()
            with self.assertRaisesRegex(ValueError, 'MRN'):
                patient_identity.export_identity(self.config, db, s, [{'metadata':json.dumps({'PatientID':'','IssuerOfPatientID':''})}])
            s['subject'] = 'wrong'
            with self.assertRaisesRegex(ValueError, 'differs'):
                patient_identity.export_identity(self.config, db, s, [{'metadata':json.dumps({'PatientID':'SYNTHETIC123','IssuerOfPatientID':'SYNTHETIC_HOSPITAL'})}])

    def test_legacy_rows_are_explicit_and_csv_formulas_escaped(self):
        with database(self.config) as db:
            export = db.execute("INSERT INTO exports(study,folder,manifest_hash,created) VALUES ('old','restricted','hash',1)")
            legacy = list(csv.DictReader(io.StringIO(patient_identity.crosswalk_csv(db).lstrip('\ufeff'))))
            self.assertIn('unavailable', legacy[0]['Mapping status'])
            patient_identity.record_export(db, export.lastrowid, dict(patient='=123',name='@formula',issuer='test',subject='SUBJECT_TEST',accession='+123',study_uid='old',anonymous_study_uid='2.25.1'))
            row = list(csv.DictReader(io.StringIO(patient_identity.crosswalk_csv(db).lstrip('\ufeff'))))[0]
            self.assertEqual(row['Original MRN'], "'=123")
            self.assertEqual(row['Original patient name'], "'@formula")


if __name__ == '__main__':
    unittest.main()
