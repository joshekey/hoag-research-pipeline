import base64
import hashlib
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pydicom
from pydicom.dataset import Dataset, FileDataset, FileMetaDataset
from pydicom.sequence import Sequence
from pydicom.uid import ExplicitVRLittleEndian, CTImageStorage, generate_uid
from werkzeug.security import generate_password_hash

import engine
from store import database, enqueue, initialize, research_uid
from webapp import create_app
from worker import process_one


class NoNLP:
    def analyze(self, **kwargs):
        return []


def image(path, study, series, sop=None, burned='NO'):
    meta = FileMetaDataset()
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.MediaStorageSOPClassUID = CTImageStorage
    meta.MediaStorageSOPInstanceUID = sop or generate_uid()
    ds = FileDataset(str(path), {}, file_meta=meta, preamble=b'\0' * 128)
    ds.SOPClassUID = CTImageStorage
    ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
    ds.StudyInstanceUID = study
    ds.SeriesInstanceUID = series
    ds.FrameOfReferenceUID = generate_uid()
    ds.PatientName = 'Synthetic^Person'
    ds.PatientID = 'SYNTHETIC123'
    ds.IssuerOfPatientID = 'SYNTHETIC_HOSPITAL'
    ds.AccessionNumber = 'ACC_TEST_123'
    ds.InstitutionName = 'Synthetic Medical Center'
    ds.StudyDate = '20200102'
    ds.Modality = 'CT'
    ds.Rows = ds.Columns = 8
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = 'MONOCHROME2'
    ds.BitsAllocated = ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.PixelData = np.arange(64, dtype='<u2').reshape(8, 8).tobytes()
    ds.BurnedInAnnotation = burned
    ds.add_new((0x0011, 0x0010), 'LO', 'PRIVATE_SYNTHETIC')
    ds.add_new((0x0011, 0x1001), 'LO', 'PRIVATE_IDENTIFIER')
    nested = Dataset()
    nested.PatientName = 'Nested^Identifier'
    nested.ReferencedSOPClassUID = CTImageStorage
    nested.ReferencedSOPInstanceUID = ds.SOPInstanceUID
    ds.ReferencedImageSequence = Sequence([nested])
    ds.save_as(path, enforce_file_format=True)
    return ds


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.sources = [self.root / 'images', self.root / 'reports']
        self.output = self.root / 'output'
        for path in [*self.sources, self.output]:
            path.mkdir()
        self.config = dict(source_roots=list(map(str, self.sources)), output_root=str(self.output),
                           state_dir=str(self.root / 'state'), secret_key='synthetic-test-key',
                           username='admin', password_hash=generate_password_hash('synthetic-password'),
                           report_encoding='utf-8-sig', max_report_bytes=10485760,
                           max_dicom_bytes=536870912, minimum_free_bytes=0,
                           require_mounts=False, secure_cookies=False)
        initialize(self.config)
        self.uid, self.series = generate_uid(), generate_uid()
        self.path = self.sources[0] / 'extensionless'
        self.original = image(self.path, self.uid, self.series)
        self.report = self.sources[1] / 'ACC_TEST_123.txt'
        self.report.write_text('Patient: Synthetic Person\nMRN: SYNTHETIC123\nAccession: ACC_TEST_123\nFindings: Clear lungs.\nDate: 01/02/2020', encoding='utf-8')
        engine.scan(self.config)

    def study(self):
        with database(self.config) as db:
            return dict(db.execute('SELECT * FROM studies WHERE uid=?', (self.uid,)).fetchone())

    def prepare(self):
        with database(self.config) as db:
            report_id = db.execute("SELECT id FROM files WHERE kind='report'").fetchone()['id']
        engine.prepare(self.config, self.uid, report_id, nlp=NoNLP())

    def approve(self, masks=None):
        self.prepare()
        engine.approve(self.config, self.uid, self.study()['sanitized'], masks or [], 'Synthetic image/report review completed')

    def test_complete_export_source_unchanged_and_manifests(self):
        before = self.path.read_bytes()
        self.approve()
        folder = engine.export_study(self.config, self.uid)
        self.assertEqual(before, self.path.read_bytes())
        files = list((folder / 'DICOM').glob('*.dcm'))
        self.assertEqual(len(files), 1)
        ds = pydicom.dcmread(files[0])
        self.assertEqual(ds.StudyInstanceUID, research_uid(self.config, self.uid))
        self.assertEqual(ds.SeriesInstanceUID, research_uid(self.config, self.series))
        self.assertEqual(ds.SOPInstanceUID, research_uid(self.config, self.original.SOPInstanceUID))
        self.assertEqual(ds.PatientID, self.study()['subject'])
        self.assertEqual(str(ds.PatientName), self.study()['subject'])
        self.assertFalse(any(e.tag.is_private for e in ds.iterall()))
        self.assertNotIn(b'Nested', files[0].read_bytes())
        self.assertNotIn(b'SYNTHETIC123', files[0].read_bytes())
        self.assertEqual(ds.file_meta.MediaStorageSOPInstanceUID, ds.SOPInstanceUID)
        np.testing.assert_array_equal(ds.pixel_array, self.original.pixel_array)
        report = (folder / 'Report/report.txt').read_text()
        self.assertNotIn('SYNTHETIC123', report)
        self.assertNotIn('ACC_TEST_123', report)
        self.assertIn('Clear lungs', report)
        manifest = json.loads((folder / 'manifest.json').read_text())
        for entry in manifest['files']:
            self.assertEqual(hashlib.sha256((folder / entry['path']).read_bytes()).hexdigest(), entry['sha256'])
        self.assertNotIn(self.uid, (folder / 'manifest.json').read_text())

    def test_ambiguous_matches_are_not_selected(self):
        (self.sources[1] / 'another.txt').write_text('Accession: ACC_TEST_123\nDifferent report')
        engine.scan(self.config)
        self.assertEqual(self.study()['state'], 'ambiguous')
        self.assertIsNone(self.study()['report_id'])
        with self.assertRaises(ValueError):
            engine.export_study(self.config, self.uid)

    def test_changed_source_blocks_export(self):
        self.approve()
        self.path.write_bytes(self.path.read_bytes() + b'\0\0')
        with self.assertRaises(ValueError):
            engine.export_study(self.config, self.uid)
        self.assertFalse(list(self.output.glob('SUBJECT_*')))

    def test_changed_report_blocks_export(self):
        self.approve()
        self.report.write_text('Changed report')
        with self.assertRaises(ValueError):
            engine.export_study(self.config, self.uid)

    def test_changed_content_with_same_size_and_time_blocks_export(self):
        self.approve()
        stat = self.path.stat()
        raw = bytearray(self.path.read_bytes())
        raw[-1] ^= 1
        self.path.write_bytes(raw)
        os.utime(self.path, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        with self.assertRaisesRegex(ValueError, 'content differs'):
            engine.export_study(self.config, self.uid)

    def test_unsupported_sop_class_is_quarantined(self):
        ds = pydicom.dcmread(self.path)
        ds.SOPClassUID = pydicom.uid.BasicTextSRStorage
        ds.file_meta.MediaStorageSOPClassUID = ds.SOPClassUID
        ds.save_as(self.path, enforce_file_format=True)
        engine.scan(self.config)
        self.approve()
        with self.assertRaisesRegex(ValueError, 'Unsupported DICOM SOP'):
            engine.export_study(self.config, self.uid)

    def test_batch_export_preserves_successes(self):
        self.approve()
        enqueue(self.config, 'export_approved')
        self.assertTrue(process_one(self.config))
        self.assertEqual(self.study()['state'], 'exported')
        with database(self.config) as db:
            self.assertEqual(db.execute('SELECT state FROM jobs').fetchone()[0], 'completed')

    def test_rescan_invalidates_approval_and_deleted_files(self):
        self.approve()
        self.report.unlink()
        engine.scan(self.config)
        self.assertEqual(self.study()['state'], 'unmatched')
        self.assertIsNone(self.study()['approved_hash'])
        with database(self.config) as db:
            self.assertEqual(db.execute("SELECT active FROM files WHERE kind='report'").fetchone()[0], 0)

    def test_burned_text_requires_mask_and_mask_changes_pixels(self):
        ds = pydicom.dcmread(self.path)
        ds.BurnedInAnnotation = 'YES'
        ds.save_as(self.path, enforce_file_format=True)
        engine.scan(self.config)
        self.approve()
        with self.assertRaises(ValueError):
            engine.export_study(self.config, self.uid)
        self.approve([[1, 1, 2, 2]])
        folder = engine.export_study(self.config, self.uid)
        out = pydicom.dcmread(next((folder / 'DICOM').glob('*.dcm')))
        self.assertTrue((out.pixel_array[1:3, 1:3] == 0).all())

    def test_duplicate_uid_conflict_is_quarantined(self):
        image(self.sources[0] / 'duplicate.dcm', self.uid, self.series, self.original.SOPInstanceUID)
        engine.scan(self.config)
        self.approve()
        with self.assertRaisesRegex(ValueError, 'Conflicting files'):
            engine.export_study(self.config, self.uid)
        self.assertEqual(self.study()['state'], 'review')

    def test_mount_and_output_overlap_protection(self):
        with patch('engine.os.path.ismount', return_value=False):
            config = {**self.config, 'require_mounts': True}
            with self.assertRaises(ValueError):
                engine.scan(config)
        with self.assertRaises(ValueError):
            engine.checked_root(self.config, self.sources[0], output=True)

    def test_queue_and_approval_exclusion(self):
        self.prepare()
        enqueue(self.config, 'match')
        with self.assertRaises(ValueError):
            enqueue(self.config, 'scan')
        with self.assertRaises(ValueError):
            engine.approve(self.config, self.uid, 'Clean report', [], 'reviewed')
        self.assertTrue(process_one(self.config))
        self.assertFalse(process_one(self.config))

    def test_worker_failure_is_visible(self):
        enqueue(self.config, 'export', {'uid': self.uid})
        process_one(self.config)
        with database(self.config) as db:
            job = db.execute('SELECT * FROM jobs').fetchone()
            self.assertEqual(job['state'], 'failed')
            self.assertIn('review', job['message'])

    def test_auth_csrf_and_preview(self):
        app = create_app(self.config)
        client = app.test_client()
        self.assertEqual(client.get('/api/status').status_code, 401)
        auth = {'Authorization': 'Basic ' + base64.b64encode(b'admin:synthetic-password').decode()}
        self.assertEqual(client.get('/', headers=auth).status_code, 200)
        self.assertEqual(client.post('/api/jobs', headers=auth, json={'kind': 'scan'}).status_code, 403)
        with client.session_transaction() as session:
            csrf = session['csrf']
        with database(self.config) as db:
            file_id = db.execute("SELECT id FROM files WHERE kind='dicom'").fetchone()[0]
        response = client.get(f'/api/image/{file_id}', headers=auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, 'image/png')
        self.assertEqual(response.headers['X-Frames'], '1')
        response = client.post('/api/jobs', headers={**auth, 'X-CSRF-Token': csrf}, json={'kind': 'scan'})
        self.assertEqual(response.status_code, 200)

    def test_real_nlp_redacts_synthetic_name_and_phone(self):
        text = 'John Smith called 212-555-0199. Findings: clear lungs.'
        result = engine.sanitize_report(text, [])
        self.assertNotIn('John Smith', result)
        self.assertNotIn('212-555-0199', result)
        self.assertIn('clear lungs', result)


if __name__ == '__main__':
    unittest.main()
