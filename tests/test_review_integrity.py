"""Synthetic regression coverage for review revisions and deliberate corrections."""
import unittest
import engine
import report_browser
import test_workflow as workflow
import test_tabbed_workflow as tabbed
from store import database


class ReviewIntegrityTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    study = workflow.WorkflowTests.study
    client = tabbed.TabbedWorkflowTests.client

    def report_id(self, path=None):
        with database(self.config) as db:
            return db.execute('SELECT id FROM files WHERE path=?', (str(path or self.report),)).fetchone()[0]

    def prepare(self, report_id=None):
        engine.prepare(self.config, self.uid, report_id or self.report_id(), nlp=workflow.NoNLP())

    def approve(self, **changes):
        args = dict(expected_revision=workflow.revision(self.config, self.uid))
        args.update(changes)
        engine.approve(self.config, self.uid, self.study()['sanitized'], [[0, 0, 1, 1]], 'Synthetic review', **args)

    def test_stale_tab_cannot_approve_old_text_for_new_report(self):
        self.prepare()
        client, auth = self.client()
        old = client.get(f'/api/study/{self.uid}', headers=auth).get_json()['study']
        second = self.sources[1] / 'second.txt'
        second.write_text('Patient: Synthetic Person\nAccession: ACC_TEST_123\nFindings: Different finding', encoding='utf-8')
        engine.scan(self.config)
        self.prepare(self.report_id(second))
        response = client.post(f'/api/study/{self.uid}/approve', headers=auth, json={
            'review_revision': old['review_revision'], 'text': old['sanitized'],
            'note': 'Stale synthetic review', 'images_reviewed': True, 'report_reviewed': True})
        self.assertEqual(response.status_code, 400)
        self.assertIn('Review changed', response.get_json()['error'])
        self.assertEqual(self.study()['state'], 'review')
        with self.assertRaises(ValueError):
            engine.export_study(self.config, self.uid)
        self.approve()
        folder = engine.export_study(self.config, self.uid)
        self.assertIn('Different finding', (folder/'Report/report.txt').read_text())

    def test_missing_and_previous_preparation_revisions_are_rejected(self):
        self.prepare()
        previous = workflow.revision(self.config, self.uid)
        self.prepare()
        for revision in (None, previous, '\u00e9'*64):
            with self.subTest(revision=revision), self.assertRaisesRegex(ValueError, 'Review changed'):
                self.approve(expected_revision=revision)

    def test_stale_or_missing_association_revision_is_rejected(self):
        result = report_browser.preview(self.config, 'txt', str(self.report_id()))
        old = workflow.revision(self.config, self.uid)
        self.prepare()
        for revision in (None, old):
            with self.subTest(revision=revision), self.assertRaisesRegex(ValueError, 'Review changed'):
                report_browser.associate(self.config, self.uid, dict(result, confirm=True, review_revision=revision), 'synthetic')

    def test_reopen_preserves_work_revokes_approval_and_exports_corrections(self):
        self.prepare()
        self.approve()
        before = self.study()
        old = workflow.revision(self.config, self.uid)
        client, auth = self.client()
        uri = f'/api/study/{self.uid}/reopen-review'
        self.assertEqual(client.post(uri, headers=auth, json={}).status_code, 400)
        self.assertEqual(client.post(uri, headers=auth, json={'review_revision': old}).status_code, 200)
        after = self.study()
        for field in ('sanitized', 'redactions', 'review_note', 'source_hashes', 'report_hash'):
            self.assertEqual(after[field], before[field])
        self.assertIsNone(after['approved_hash'])
        with self.assertRaises(ValueError):
            engine.export_study(self.config, self.uid)
        with self.assertRaisesRegex(ValueError, 'Review changed'):
            self.approve(expected_revision=old)
        engine.approve(self.config, self.uid, 'Corrected synthetic text', [[0, 0, 2, 2]], 'Corrected review',
                       expected_revision=workflow.revision(self.config, self.uid))
        folder = engine.export_study(self.config, self.uid)
        self.assertEqual((folder/'Report/report.txt').read_text(), 'Corrected synthetic text')
        self.assertEqual(self.study()['redactions'], '[[0, 0, 2, 2]]')
        # Reopening an exported review changes future approval, never old packages.
        engine.reopen_review(self.config, self.uid, workflow.revision(self.config, self.uid))
        self.assertTrue((folder/'manifest.json').is_file())
        self.assertEqual(self.study()['state'], 'review')


    def test_out_of_bounds_mask_is_rejected_before_approval(self):
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'outside an image'):
            engine.approve(self.config, self.uid, self.study()['sanitized'], [[7, 7, 2, 2]],
                           'Synthetic review', expected_revision=workflow.revision(self.config, self.uid))
        self.assertEqual(self.study()['state'], 'review')
        self.assertIsNone(self.study()['approved_hash'])

    def test_masks_must_fit_every_instance_not_only_the_displayed_one(self):
        import pydicom
        import numpy as np
        other_path = self.sources[0] / 'smaller.dcm'
        other = workflow.image(other_path, self.uid, self.series)
        other.Rows = other.Columns = 4
        other.PixelData = np.arange(16, dtype='<u2').tobytes()
        other.save_as(other_path, enforce_file_format=True)
        engine.scan(self.config)
        self.prepare()
        with self.assertRaisesRegex(ValueError, 'outside an image'):
            engine.approve(self.config, self.uid, self.study()['sanitized'], [[3, 3, 2, 2]],
                           'Synthetic review', expected_revision=workflow.revision(self.config, self.uid))
        self.assertEqual(self.study()['state'], 'review')

    def test_multiframe_mask_changes_every_frame_and_preserves_unmasked_pixels(self):
        import numpy as np
        import pydicom
        ds = pydicom.dcmread(self.path)
        original = np.arange(128, dtype='<u2').reshape(2, 8, 8)
        ds.NumberOfFrames = 2
        ds.PixelData = original.tobytes()
        engine.mask_pixels(ds, [[2, 3, 3, 2]])
        expected = original.copy()
        expected[:, 3:5, 2:5] = 0
        np.testing.assert_array_equal(ds.pixel_array, expected)

    def test_save_draft_persists_without_approval_and_rejects_stale_revision(self):
        self.prepare()
        client, auth = self.client()
        old = workflow.revision(self.config, self.uid)
        body = dict(review_revision=old, text='Unfinished synthetic text', rectangles=[[1, 1, 2, 2]], note='')
        uri = f'/api/study/{self.uid}/review-draft'
        result = client.post(uri, headers=auth, json=body)
        self.assertEqual(result.status_code, 200)
        self.assertFalse(result.get_json()['approved'])
        self.assertNotEqual(result.get_json()['review_revision'], old)
        self.assertEqual(self.study()['sanitized'], body['text'])
        self.assertEqual(self.study()['redactions'], '[[1, 1, 2, 2]]')
        self.assertEqual(self.study()['state'], 'review')
        self.assertIsNone(self.study()['approved_hash'])
        with self.assertRaises(ValueError):
            engine.export_study(self.config, self.uid)
        body['text'] = 'Stale edit'
        self.assertEqual(client.post(uri, headers=auth, json=body).status_code, 400)
        self.assertEqual(self.study()['sanitized'], 'Unfinished synthetic text')
        self.approve()
        body['review_revision'] = workflow.revision(self.config, self.uid)
        self.assertEqual(client.post(uri, headers=auth, json=body).status_code, 400)
        self.assertEqual(self.study()['state'], 'approved')

    def test_draft_rejects_invalid_masks_without_losing_previous_content(self):
        self.prepare()
        original = self.study()['sanitized']
        for invalid in ([[True, 0, 1, 1]], [[0, 0, 0, 1]], [[0, 0, 1, 1]] * 101):
            with self.subTest(masks=invalid), self.assertRaises(ValueError):
                engine.save_review_draft(self.config, self.uid, 'Replacement', invalid, '',
                                        expected_revision=workflow.revision(self.config, self.uid))
        self.assertEqual(self.study()['sanitized'], original)
