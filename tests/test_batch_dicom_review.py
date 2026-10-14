import copy
import io
import json
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch
import unittest
import batch_dicom_review as review
import batch_dicom_stage as stage
import test_batch_dicom_stage as fixture


class GroupReviewTests(unittest.TestCase):
    setUp=fixture.StagingTests.setUp
    setup_batch=fixture.StagingTests.setup_batch
    run_batch=fixture.StagingTests.run_batch
    prepare=fixture.StagingTests.prepare

    def evidence(self):
        return {k:{'path':'synthetic-'+k,'sha256':k+'-synthetic-hash'} for k in ('identity','profile','pixels')}

    def test_group_authorization_feeds_existing_staging_and_resume(self):
        self.prepare()
        ledger=review.new_ledger(self.plan)
        grouped=review.groups(self.plan)
        self.assertEqual(review.authorize(self.plan,ledger,grouped,'synthetic-reviewer',self.evidence()),1)
        state=stage.stage(self.plan,ledger,self.config,self.mounted,self.run)
        self.assertEqual(state['objects']['0']['status'],'validated_staging_only')
        self.assertEqual(stage.stage(self.plan,ledger,self.config,self.mounted,self.run),state)
        self.assertFalse(ledger['release_allowed'])

    def test_technical_holds_cannot_be_bulk_approved(self):
        self.prepare()
        self.plan['records'][0]['holds']=['overlay_review']
        grouped=review.groups(self.plan)
        self.assertEqual(grouped[0]['eligible_indices'],[])
        with self.assertRaisesRegex(ValueError,'no_eligible'):
            review.authorize(self.plan,review.new_ledger(self.plan),grouped,'reviewer',self.evidence(),True)

    def test_patient_identifier_variants_require_explicit_authoritative_resolution(self):
        self.prepare()
        record=copy.deepcopy(self.plan['records'][0])
        record['identity']['PatientID']='OTHER-ID'; record['sha256']='other-synthetic-hash'
        self.plan['records'].append(record)
        grouped=review.groups(self.plan)
        self.assertTrue(grouped[0]['requires_variant_resolution'])
        with self.assertRaisesRegex(ValueError,'variant_resolution'):
            review.authorize(self.plan,review.new_ledger(self.plan),grouped,'reviewer',self.evidence())
        ledger=review.new_ledger(self.plan)
        self.assertEqual(review.authorize(self.plan,ledger,grouped,'reviewer',self.evidence(),True),2)

    def test_cross_patient_conflicts_and_missing_identity_stay_held(self):
        self.prepare()
        record=copy.deepcopy(self.plan['records'][0]); record['label']='P002'
        self.plan['patients'].append('P002'); self.plan['records'].append(record)
        grouped=review.groups(self.plan)
        self.assertTrue(all(not g['eligible_indices'] for g in grouped))
        self.plan['records']=self.plan['records'][:1]
        self.plan['records'][0]['identity']['PatientID']=''
        self.assertEqual(review.groups(self.plan)[0]['eligible_indices'],[])

    def test_no_reports_required_and_missing_patients_remain_counted(self):
        self.prepare()
        self.plan['patients'].append('P002')
        counts=review.public_counts(self.plan,review.groups(self.plan),review.new_ledger(self.plan))
        self.assertEqual(counts['cohort_patients'],2)
        self.assertEqual(counts['report_count_required'],0)
        self.assertEqual(counts['authorized_instance_paths'],0)

    def test_changed_plan_and_missing_evidence_rejected(self):
        self.prepare()
        ledger=review.new_ledger(self.plan)
        with self.assertRaises(ValueError): review.authorize(self.plan,ledger,review.groups(self.plan),'reviewer',{})
        self.plan['patients'].append('P002')
        with self.assertRaisesRegex(ValueError,'plan_changed'):
            review.authorize(self.plan,ledger,review.groups(self.plan),'reviewer',self.evidence())

    def test_cli_evidence_tamper_blocks_staging_and_ledger_freezes(self):
        import batch_reconcile as base
        self.prepare()
        run=base.private_run_directory(self.config)
        config_path=self.root/'synthetic-config.json'
        config_path.write_text(json.dumps(self.config))
        stage.write_json(run/'bundle-plan.private.json',{'stage_plan':self.plan})
        def call(action,*args):
            with patch('sys.argv',['review',action,'--config',str(config_path),'--run',str(run),*args]), redirect_stdout(io.StringIO()), patch('batch_reconcile.mounts',return_value=self.mounted), patch.object(Path,'read_text',autospec=True) as read:
                original=original_read
                read.side_effect=lambda p,*a,**kw: '' if p.as_posix()=='/proc/self/mountinfo' else original(p,*a,**kw)
                return review.main()
        original_read=Path.read_text
        self.assertEqual(call('prepare'),0)
        flags=['--patient','P001','--reviewer','synthetic-reviewer','--identity-verified','--profile-reviewed','--all-frames-reviewed-no-identifiers']
        for name in ('identity','profile','pixels'):
            file=run/(name+'.private.txt'); file.write_text('synthetic reviewed evidence '+name)
            file.chmod(0o600)
            flags.extend(['--'+name+'-evidence',str(file)])
        self.assertEqual(call('authorize',*flags),0)
        pixel_file=run/'pixels.private.txt'; pixel_file.write_text('changed')
        self.assertEqual(call('stage'),2)
        self.assertFalse(list(run.rglob('*.dcm')))
        pixel_file.write_text('synthetic reviewed evidence pixels')
        self.assertEqual(call('stage'),0)
        self.assertEqual(call('authorize',*flags),2)
