import json
import unittest
from pathlib import Path
from unittest.mock import patch
import batch_identity_worksheet as worksheet
import batch_dicom_review as review
import batch_dicom_stage as stage
import test_batch_reconcile as fixture

class WorksheetTests(unittest.TestCase):
    setUp=fixture.BatchReconciliationTests.setUp
    setup_batch=fixture.BatchReconciliationTests.setup_batch
    run_batch=fixture.BatchReconciliationTests.run_batch

    def prepare(self):
        self.setup_batch()
        manifest=self.run_batch()
        self.plan=stage.plan(manifest,self.config,self.mounted)
        self.private_root=self.root/'private'; self.private_root.mkdir()
        self.run=self.private_root/'run-synthetic'; self.run.mkdir()
        stage.write_json(self.run/'manifest.private.json',manifest)
        self.grouped=review.groups(self.plan,True)

    def generate(self):
        original=Path.read_text
        with patch('batch_reconcile.mounts',return_value=self.mounted), patch.object(Path,'read_text',autospec=True) as read:
            read.side_effect=lambda p,*a,**kw: '' if p.as_posix()=='/proc/self/mountinfo' else original(p,*a,**kw)
            return worksheet.generate(self.plan,self.grouped,self.grouped[0]['group_id'],self.private_root,self.run,self.config,lambda p:p)

    def test_private_worksheet_has_evidence_not_approval_and_public_counts_no_phi(self):
        self.prepare(); before=self.path.read_bytes()
        result=self.generate()
        self.assertEqual(result['source_discrepancies'],0)
        self.assertEqual(result['approvals_created'],0)
        self.assertNotIn('SYNTHETIC123',json.dumps(result))
        text=next(self.run.glob('*worksheet.private.txt')).read_text()
        self.assertIn('SYNTHETIC123',text)
        self.assertIn('INTERNAL1',text)
        self.assertIn('NOT an MRN',text)
        packet=json.loads(next(self.run.glob('*worksheet.private.json')).read_text())
        self.assertFalse(packet['identity_approved'])
        self.assertFalse(packet['release_allowed'])
        self.assertEqual(self.path.read_bytes(),before)
        self.assertFalse(list(self.run.glob('*authorizations*')))
        with self.assertRaisesRegex(ValueError,'already_exists'): self.generate()

    def test_changed_source_is_recorded_not_approved(self):
        self.prepare(); self.path.write_bytes(self.path.read_bytes()+b'changed')
        result=self.generate()
        self.assertEqual(result['source_discrepancies'],1)
        self.assertEqual(result['approvals_created'],0)

    def test_manifest_mismatch_fails_closed(self):
        self.prepare(); self.plan['manifest_sha256']='incorrect'
        with self.assertRaisesRegex(ValueError,'matching_manifest_missing'): self.generate()
