import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

import pydicom
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian, SecondaryCaptureImageStorage, generate_uid

import pipeline


class DiscoveryTests(unittest.TestCase):
    def test_scan_resume_and_update(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp) / 'source'
            root.mkdir()
            meta = FileMetaDataset()
            meta.TransferSyntaxUID = ExplicitVRLittleEndian
            meta.MediaStorageSOPClassUID = SecondaryCaptureImageStorage
            meta.MediaStorageSOPInstanceUID = generate_uid()
            ds = FileDataset(str(root / 'image.dcm'), {}, file_meta=meta, preamble=b'\0' * 128)
            ds.SOPClassUID = meta.MediaStorageSOPClassUID
            ds.SOPInstanceUID = meta.MediaStorageSOPInstanceUID
            ds.StudyInstanceUID = generate_uid()
            ds.AccessionNumber = 'SYNTHETIC001'
            ds.save_as(root / 'image.dcm', enforce_file_format=True)
            database = str(Path(tmp) / 'catalog.sqlite')
            before = (root / 'image.dcm').read_bytes()
            with contextlib.redirect_stdout(io.StringIO()) as output:
                self.assertEqual(pipeline.scan(database, root, 'dicom'), 0)
                self.assertEqual(pipeline.scan(database, root, 'dicom'), 0)
            self.assertEqual(json.loads(output.getvalue().splitlines()[-1])['unchanged'], 1)
            self.assertEqual(pipeline.summary(database)[0]['count'], 1)
            self.assertEqual(before, (root / 'image.dcm').read_bytes())
            (root / 'report.txt').write_text('Synthetic report', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()):
                pipeline.scan(database, root, 'report')
                (root / 'report.txt').write_text('Synthetic report updated', encoding='utf-8')
                pipeline.scan(database, root, 'report')
            with contextlib.closing(pipeline.connect(database)) as db:
                metadata = json.loads(db.execute("SELECT metadata FROM files WHERE kind='report'").fetchone()[0])
                self.assertEqual(metadata['text'], 'Synthetic report updated')

    def test_sql_detection_does_not_execute(self):
        with tempfile.TemporaryDirectory() as tmp:
            dump = Path(tmp) / 'example.sql'
            dump.write_text('-- PostgreSQL database dump\nDROP DATABASE synthetic;', encoding='utf-8')
            with contextlib.redirect_stdout(io.StringIO()) as output:
                pipeline.sql_type(dump)
            self.assertEqual(json.loads(output.getvalue())['possible_database_types'], ['PostgreSQL'])


if __name__ == '__main__':
    unittest.main()
