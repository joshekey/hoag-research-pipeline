import copy
import tempfile
from pathlib import Path
import unittest
import pydicom
import batch_dicom_stage as batch
import batch_stage_validation as validation
import test_batch_reconcile as fixture


class StagingTests(unittest.TestCase):
    setUp = fixture.BatchReconciliationTests.setUp
    setup_batch = fixture.BatchReconciliationTests.setup_batch
    run_batch = fixture.BatchReconciliationTests.run_batch

    def prepare(self):
        self.setup_batch()
        ds = pydicom.dcmread(self.path); ds.PatientAge = '050Y'
        ds.save_as(self.path, enforce_file_format=True)
        manifest = self.run_batch()
        manifest['documents'] = []
        self.plan = batch.plan(manifest,self.config,self.mounted)
        self.run = self.root/'private-stage'; self.run.mkdir()
        self.reviews = {'profile':batch.PROFILE, 'profile_reviewed_by':'synthetic-reviewer',
            'plan_sha256':batch.digest(self.plan),'objects':{r['sha256']:{'label':r['label'],
                'identity_verified_by':'synthetic-reviewer','authority_reference':'synthetic-master-record',
                'all_frames_reviewed_by':'synthetic-reviewer','pixel_identifiers_absent':True} for r in self.plan['records']}}

    def test_dicom_only_age_resume_and_validation(self):
        self.prepare()
        original = self.path.read_bytes()
        result = batch.stage(self.plan,self.reviews,self.config,self.mounted,self.run)
        self.assertEqual(result['objects']['0']['status'],'validated_staging_only')
        outputs = list(self.run.rglob('*.dcm'))
        self.assertEqual(len(outputs),1)
        self.assertEqual(str(pydicom.dcmread(outputs[0]).PatientAge),'050Y')
        self.assertTrue((self.run/'P001'/'reports').is_dir())
        self.assertEqual(batch.stage(self.plan,self.reviews,self.config,self.mounted,self.run),result)
        self.assertEqual(self.path.read_bytes(),original)
        self.assertFalse(result['release_allowed'])

    def test_review_gates_hold_and_cohort_not_dropped(self):
        self.prepare()
        self.plan['patients'].append('P002')
        self.reviews['plan_sha256'] = batch.digest(self.plan)
        self.reviews['objects'] = {}
        result=batch.stage(self.plan,self.reviews,self.config,self.mounted,self.run)
        self.assertEqual(result['objects']['0']['status'],'held')
        self.assertEqual(len(self.plan['patients']),2)
        self.assertFalse(list(self.run.rglob('*.dcm')))

    def test_changed_source_and_burned_images_hold(self):
        self.prepare()
        self.path.write_bytes(self.path.read_bytes()+b'changed')
        result=batch.stage(self.plan,self.reviews,self.config,self.mounted,self.run)
        self.assertEqual(result['objects']['0']['status'],'held')

    def test_unapproved_profile_and_changed_resume_inputs_rejected(self):
        self.prepare()
        bad=copy.deepcopy(self.reviews); bad['profile_reviewed_by']=''
        with self.assertRaises(ValueError): batch.stage(self.plan,bad,self.config,self.mounted,self.run)
        batch.stage(self.plan,self.reviews,self.config,self.mounted,self.run)
        self.reviews['profile_reviewed_by']='changed-reviewer'
        with self.assertRaises(ValueError): batch.stage(self.plan,self.reviews,self.config,self.mounted,self.run)

    def test_independent_validator_rejects_identifier_and_pixel_change(self):
        self.prepare()
        batch.stage(self.plan,self.reviews,self.config,self.mounted,self.run)
        target=next(self.run.rglob('*.dcm'))
        ds=pydicom.dcmread(target); ds.InstitutionName='SYNTHETIC LEAK'; ds.save_as(target,enforce_file_format=True)
        with self.assertRaises(ValueError): validation.validate(pydicom.dcmread(self.path),target,'P001',self.config)

    def test_missing_patient_does_not_block_verified_dicom(self):
        self.prepare()
        self.plan['patients'].append('P002')
        self.reviews['plan_sha256']=batch.digest(self.plan)
        result=batch.stage(self.plan,self.reviews,self.config,self.mounted,self.run)
        self.assertEqual(result['objects']['0']['status'],'validated_staging_only')
        self.assertEqual(len(self.plan['patients']),2)

    def test_technical_holds_cannot_be_overridden_by_generic_review(self):
        self.prepare()
        self.plan['records'][0]['holds']=['burned_in_or_unknown','duplicate_uid_requires_resolution']
        self.reviews['plan_sha256']=batch.digest(self.plan)
        result=batch.stage(self.plan,self.reviews,self.config,self.mounted,self.run)
        self.assertEqual(result['objects']['0']['status'],'held')

    def test_optional_report_redaction_and_failure_do_not_block_images(self):
        from unittest.mock import patch
        import test_workflow
        self.prepare()
        self.reviews['reports']=[{'path':str(self.report),'label':'P001','sha256':batch.fingerprint(self.report),
                                 'ownership_verified_by':'synthetic-reviewer'}]
        with patch('engine.analyzer', return_value=test_workflow.NoNLP()):
            result=batch.stage(self.plan,self.reviews,self.config,self.mounted,self.run)
        self.assertEqual(result['objects']['0']['status'],'validated_staging_only')
        self.assertEqual(result['reports']['0']['status'],'redacted_staging_awaiting_independent_review')
        output=(self.run/'P001'/'reports'/'report-000001.txt').read_text()
        self.assertIn('P001',output)
        self.assertNotIn('SYNTHETIC123',output)

    def test_live_progress_contains_only_anonymous_labels_and_counts(self):
        self.prepare()
        events=[]
        batch.stage(self.plan,self.reviews,self.config,self.mounted,self.run,events.append)
        self.assertIn('P001',str(events))
        self.assertNotIn('SYNTHETIC123',str(events))
        self.assertNotIn(str(self.path),str(events))
