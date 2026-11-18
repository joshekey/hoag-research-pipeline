"""Scoped masks must change only their bound source instances and frames."""
import json
import unittest
import numpy as np
import pydicom
from pydicom.uid import generate_uid
import engine
import pixel_masks
import test_workflow as workflow
from store import database, research_uid


class ScopedMaskTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    study = workflow.WorkflowTests.study
    prepare = workflow.WorkflowTests.prepare

    def document(self, scope='image', **changes):
        mask = dict(scope=scope, series_uid=self.series, rect=[1, 1, 2, 2])
        if scope == 'image':
            mask.update(sop_uid=str(self.original.SOPInstanceUID), frame=0)
        mask.update(changes)
        return dict(version=2, masks=[mask])

    def approve(self, masks):
        engine.approve(self.config, self.uid, self.study()['sanitized'], masks, 'Scoped synthetic review', expected_revision=workflow.revision(self.config, self.uid))

    def test_image_and_series_export_touch_only_bound_pixels(self):
        first = pydicom.dcmread(self.path)
        first.NumberOfFrames = 2
        pixels = np.arange(1, 129, dtype='<u2').reshape(2, 8, 8)
        first.PixelData = pixels.tobytes()
        first.save_as(self.path, enforce_file_format=True)
        same = workflow.image(self.sources[0] / 'same-series', self.uid, self.series)
        series_b = generate_uid()
        other = workflow.image(self.sources[0] / 'other-series', self.uid, series_b)
        other2 = workflow.image(self.sources[0] / 'other-series-two', self.uid, series_b)
        originals = {str(ds.SOPInstanceUID): ds.pixel_array.copy() for ds in [first, same, other, other2]}
        sources = {path: path.read_bytes() for path in self.sources[0].iterdir()}
        engine.scan(self.config)
        self.prepare()
        doc = self.document(frame=1)
        doc['masks'].append(dict(scope='series', series_uid=series_b, rect=[4, 4, 2, 2]))
        engine.save_review_draft(self.config, self.uid, self.study()['sanitized'], doc, '', expected_revision=workflow.revision(self.config, self.uid))
        self.assertEqual(json.loads(self.study()['redactions']), doc)
        self.approve(doc)
        folder = engine.export_study(self.config, self.uid)
        for ds in [first, same, other, other2]:
            expected = originals[str(ds.SOPInstanceUID)].copy()
            if ds is first:
                expected[1, 1:3, 1:3] = 0
            if ds in [other, other2]:
                expected[4:6, 4:6] = 0
            exported = pydicom.dcmread(folder / 'DICOM' / (research_uid(self.config, str(ds.SOPInstanceUID)) + '.dcm'))
            np.testing.assert_array_equal(exported.pixel_array, expected)
        for path, content in sources.items():
            self.assertEqual(path.read_bytes(), content)

    def test_unknown_targets_mismatched_series_and_invalid_frames_cannot_save_or_approve(self):
        self.prepare()
        for doc in [self.document(sop_uid=generate_uid()), self.document(series_uid=generate_uid()), self.document(frame=1), self.document('series', series_uid=generate_uid())]:
            for operation in ('save', 'approve'):
                with self.subTest(doc=doc, operation=operation), self.assertRaises(ValueError):
                    if operation == 'save':
                        engine.save_review_draft(self.config, self.uid, self.study()['sanitized'], doc, '', expected_revision=workflow.revision(self.config, self.uid))
                    else:
                        self.approve(doc)
            self.assertEqual(self.study()['state'], 'review')
            self.assertEqual(self.study()['redactions'], '[]')

    def test_bounds_are_checked_only_within_scope(self):
        smaller = workflow.image(self.sources[0] / 'small', self.uid, generate_uid())
        smaller.Rows = smaller.Columns = 4
        smaller.PixelData = np.arange(16, dtype='<u2').tobytes()
        smaller.save_as(self.sources[0] / 'small', enforce_file_format=True)
        engine.scan(self.config)
        self.prepare()
        self.approve(self.document(rect=[6, 6, 2, 2]))
        engine.reopen_review(self.config, self.uid, workflow.revision(self.config, self.uid))
        with self.assertRaisesRegex(ValueError, 'outside an image'):
            self.approve({'version': 2, 'masks': [{'scope': 'exam', 'rect': [6, 6, 2, 2]}]})

    def test_legacy_approved_content_hash_and_all_frame_behavior_are_preserved(self):
        self.prepare()
        self.approve([[1, 1, 2, 2]])
        before = self.study()
        self.assertIsInstance(json.loads(before['redactions']), list)
        folder = engine.export_study(self.config, self.uid)
        ds = pydicom.dcmread(next((folder / 'DICOM').glob('*.dcm')))
        self.assertTrue((ds.pixel_array[1:3, 1:3] == 0).all())
        self.assertEqual(self.study()['approved_hash'], before['approved_hash'])

    def test_malformed_documents_fail_closed(self):
        for doc in [{'version': 3, 'masks': []}, {'version': 2, 'masks': [dict(scope='all', rect=[0, 0, 1, 1])]}, self.document(frame=True), self.document(frame=-1), self.document(rect=[0, 0, 0, 1]), self.document(sop_uid='not-a-uid'), {'version': 2, 'masks': self.document()['masks'] * 101}]:
            with self.subTest(doc=doc), self.assertRaises(ValueError):
                pixel_masks.entries(doc)

    def test_burned_annotation_on_unmasked_frames_is_not_exportable(self):
        ds = pydicom.dcmread(self.path)
        ds.NumberOfFrames = 2
        ds.PixelData = ds.PixelData * 2
        ds.BurnedInAnnotation = 'YES'
        with self.assertRaisesRegex(ValueError, 'every frame'):
            engine.anonymize_dataset(self.config, ds, 'SYNTHETIC', self.document(frame=0))
        with self.assertRaisesRegex(ValueError, 'every frame'):
            engine.anonymize_dataset(self.config, ds, 'SYNTHETIC', self.document('series', series_uid=generate_uid()))
