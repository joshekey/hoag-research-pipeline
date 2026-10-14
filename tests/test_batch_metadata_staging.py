import copy
import unittest
from unittest.mock import patch
import pydicom
import batch_dicom_stage as stage
import batch_dicom_review as review
import test_batch_dicom_stage as fixture


class MetadataStagingTests(unittest.TestCase):
    setUp = fixture.StagingTests.setUp
    setup_batch = fixture.StagingTests.setup_batch
    run_batch = fixture.StagingTests.run_batch
    prepare = fixture.StagingTests.prepare

    def metadata(self, annotation=None):
        self.prepare()
        ds = pydicom.dcmread(self.path)
        if annotation is None:
            if 'BurnedInAnnotation' in ds: del ds.BurnedInAnnotation
        else: ds.BurnedInAnnotation = annotation
        ds.save_as(self.path, enforce_file_format=True)
        record = self.plan['records'][0]
        record['sha256'] = stage.fingerprint(self.path)
        record['holds'] = ['burned_in_or_unknown']
        record.pop('burned_in_annotation', None)  # Also exercise old inventory plans.
        self.ledger = review.new_ledger(self.plan, True)
        self.evidence = {k:{'sha256':'synthetic-'+k} for k in ('identity','profile')}

    def approve(self):
        review.authorize(self.plan,self.ledger,review.groups(self.plan,True),'reviewer',self.evidence)

    def test_unknown_stages_without_pixel_approval_and_preserves_source_age_pixels(self):
        self.metadata()
        original = self.path.read_bytes()
        self.assertFalse(review.groups(self.plan)[0]['eligible_indices'])
        self.approve()
        self.assertNotIn('pixel_identifiers_absent',next(iter(self.ledger['objects'].values())))
        result=stage.stage(self.plan,self.ledger,self.config,self.mounted,self.run)
        self.assertEqual(result['objects']['0']['status'],'metadata_anonymized_awaiting_pixel_review')
        target=next(self.run.rglob('*.dcm'))
        ds=pydicom.dcmread(target)
        self.assertNotIn('BurnedInAnnotation',ds)
        self.assertIn('PIXEL REVIEW PENDING',ds.DeidentificationMethod)
        self.assertEqual(str(ds.PatientAge),'050Y')
        self.assertEqual(self.path.read_bytes(),original)
        counts=stage.progress_counts(result,1)
        self.assertEqual(counts['dicom_files_metadata_anonymized'],1)
        self.assertEqual(counts['files_awaiting_pixel_review'],1)
        self.assertFalse(result['release_allowed'])
        self.assertEqual(stage.stage(self.plan,self.ledger,self.config,self.mounted,self.run),result)

    def test_explicit_yes_from_old_plan_cannot_stage(self):
        self.metadata('YES'); self.approve()
        result=stage.stage(self.plan,self.ledger,self.config,self.mounted,self.run)
        self.assertEqual(result['objects']['0']['code'],'known_burned_in_identifiers')
        self.assertFalse(list(self.run.rglob('*.dcm')))

    def test_other_holds_missing_identity_and_known_yes_remain_blocked(self):
        self.metadata()
        for reason in ('overlay_review','duplicate_uid_requires_resolution','source_unverified','invalid_age'):
            with self.subTest(reason=reason):
                self.plan['records'][0]['holds']=['burned_in_or_unknown',reason]
                self.assertFalse(review.groups(self.plan,True)[0]['eligible_indices'])
        self.plan['records'][0]['holds']=['burned_in_or_unknown']
        self.plan['records'][0]['burned_in_annotation']='YES'
        self.assertFalse(review.groups(self.plan,True)[0]['eligible_indices'])
        self.plan['records'][0]['burned_in_annotation']=''
        self.plan['records'][0]['identity']['PatientID']=''
        self.assertFalse(review.groups(self.plan,True)[0]['eligible_indices'])

    def test_pilot_then_resume_uses_same_binding_and_does_not_rewrite(self):
        self.metadata()
        self.plan['records'].append(copy.deepcopy(self.plan['records'][0]))
        self.ledger=review.new_ledger(self.plan,True); self.approve()
        first=stage.stage(self.plan,self.ledger,self.config,self.mounted,self.run,pilot_limit=1)
        self.assertEqual(stage.progress_counts(first,2)['dicom_files_metadata_anonymized'],1)
        target=next(self.run.rglob('*.dcm')); before=target.stat().st_mtime_ns
        second=stage.stage(self.plan,self.ledger,self.config,self.mounted,self.run)
        self.assertEqual(stage.progress_counts(second,2)['dicom_files_metadata_anonymized'],2)
        self.assertEqual(target.stat().st_mtime_ns,before)
        self.assertEqual(first['binding'],second['binding'])

    def test_source_change_and_validation_failure_do_not_stage(self):
        self.metadata(); self.approve()
        with patch('batch_stage_validation.validate',side_effect=ValueError('pixels')):
            state=stage.stage(self.plan,self.ledger,self.config,self.mounted,self.run)
        self.assertEqual(state['objects']['0']['code'],'processing_or_validation_failed')
        self.assertFalse(list(self.run.rglob('*.dcm')))
        self.path.write_bytes(self.path.read_bytes()+b'changed')
        state=stage.stage(self.plan,self.ledger,self.config,self.mounted,self.run)
        self.assertEqual(state['objects']['0']['code'],'source_changed')

    def test_no_identity_or_profile_evidence_cannot_authorize(self):
        self.metadata()
        for key in ('identity','profile'):
            evidence=dict(self.evidence); del evidence[key]
            with self.assertRaises(ValueError):
                review.authorize(self.plan,self.ledger,review.groups(self.plan,True),'reviewer',evidence)

    def test_cli_metadata_mode_separate_ledger_output_and_pilot(self):
        import io
        import json
        from contextlib import redirect_stdout
        from pathlib import Path
        import batch_reconcile as base
        self.metadata()
        run=base.private_run_directory(self.config)
        cfg=self.root/'synthetic-config.json'; cfg.write_text(json.dumps(self.config))
        stage.write_json(run/'bundle-plan.private.json',{'stage_plan':self.plan})
        original_read=Path.read_text
        def call(action,*args):
            with patch('sys.argv',['review',action,'--metadata-only','--config',str(cfg),'--run',str(run),*args]), redirect_stdout(io.StringIO()), patch('batch_reconcile.mounts',return_value=self.mounted), patch.object(Path,'read_text',autospec=True) as read:
                read.side_effect=lambda p,*a,**kw: '' if p.as_posix()=='/proc/self/mountinfo' else original_read(p,*a,**kw)
                return review.main()
        self.assertEqual(call('prepare'),0)
        flags=['--patient','P001','--reviewer','reviewer','--identity-verified','--profile-reviewed']
        for kind in ('identity','profile'):
            evidence=run/(kind+'.private.txt'); evidence.write_text('synthetic evidence '+kind); evidence.chmod(0o600)
            flags.extend(['--'+kind+'-evidence',str(evidence)])
        self.assertEqual(call('authorize',*flags),0)
        self.assertEqual(call('stage','--pilot-limit','1'),0)
        self.assertFalse((run/'checkpoint.private.json').exists())
        self.assertFalse((run/'group-authorizations.private.json').exists())
        self.assertEqual(len(list((run/'metadata-staging').rglob('*.dcm'))),1)
        self.assertEqual(call('stage'),0)
        self.assertEqual(call('authorize',*flags),2)

    def test_unsupported_pixel_decoding_still_blocks_metadata_copy(self):
        self.metadata()
        ds=pydicom.dcmread(self.path); ds.PixelData=b'xx'; ds.save_as(self.path,enforce_file_format=True)
        self.plan['records'][0]['sha256']=stage.fingerprint(self.path)
        self.ledger=review.new_ledger(self.plan,True); self.approve()
        result=stage.stage(self.plan,self.ledger,self.config,self.mounted,self.run)
        self.assertEqual(result['objects']['0']['code'],'unsupported_pixel_decoding')
        self.assertFalse(list(self.run.rglob('*.dcm')))
