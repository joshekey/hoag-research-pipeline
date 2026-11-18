"""Loopback-only synthetic application for browser regression tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
import threading
import time
import logging
import io
import hashlib
import pydicom
from unittest.mock import patch
import numpy as np
from pydicom.uid import generate_uid, JPEGBaseline8Bit
from pydicom.encaps import encapsulate
from PIL import Image
from flask import jsonify
import test_workflow as workflow
import engine
from store import database, research_uid
from webapp import create_app
from worker import process_one
from ohif_poc import clinical_dicomweb

fixture = workflow.WorkflowTests()
fixture.setUp()
other = workflow.image(fixture.sources[0] / 'other.dcm', generate_uid(), generate_uid())
other.PatientName = 'Example^Jordan'
other.PatientID = 'DEMO-002'
other.AccessionNumber = 'EXAM_B'
other.SeriesDescription = 'Synthetic JPEG stack'
other.Rows = other.Columns = 64
other.BitsAllocated = other.BitsStored = 8
other.HighBit = 7
pixels = (np.indices((64, 64)).sum(axis=0)*2).astype('uint8')
compressed = io.BytesIO()
Image.fromarray(pixels).save(compressed, format='JPEG')
other.file_meta.TransferSyntaxUID = JPEGBaseline8Bit
other.PixelData = encapsulate([compressed.getvalue()])
other['PixelData'].is_undefined_length = True
other.save_as(fixture.sources[0] / 'other.dcm', enforce_file_format=True)
# A stack with real geometry and distinguishable nonuniform synthetic pixels.
for i in range(3):
    path = fixture.path if i == 0 else fixture.sources[0] / f'slice-{i}.dcm'
    ds = fixture.original if i == 0 else workflow.image(path, fixture.uid, fixture.series)
    ds.PatientBirthDate = '19720514'
    ds.StudyDescription = 'Synthetic CT chest'
    ds.SeriesDescription = 'Synthetic axial stack'
    ds.SeriesNumber = 1
    ds.InstanceNumber = i+1
    ds.ImagePositionPatient = [0, 0, i*2]
    ds.ImageOrientationPatient = [1, 0, 0, 0, 1, 0]
    ds.PixelSpacing = [1, 1]
    ds.SliceThickness = 2
    ds.Rows = ds.Columns = 64
    ds.WindowCenter = 1200
    ds.WindowWidth = 2400
    ds.PixelData = ((np.indices((64,64)).sum(axis=0)*20+i*100).astype('<u2')).tobytes()
    ds.save_as(path, enforce_file_format=True)
scout = workflow.image(fixture.sources[0] / 'scout.dcm', fixture.uid, generate_uid())
scout.PatientBirthDate = '19720514'
scout.SeriesDescription = 'Synthetic scout'
scout.save_as(fixture.sources[0] / 'scout.dcm', enforce_file_format=True)
engine.scan(fixture.config)
source_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in fixture.sources[0].iterdir() if path.is_file()}
gate = fixture.root / 'viewer.uid'
gate.write_text('ALL_INDEXED')
clinical_dicomweb.ALLOWLIST = gate
fixture.config['ohif_assets'] = str(Path(__file__).parents[1] / '.browser-fixtures/ohif-assets')
app = create_app(fixture.config)

@app.get('/__synthetic_fixture')
def info():
    return jsonify(uid=fixture.uid, other=str(other.StudyInstanceUID))

@app.get('/__synthetic_export_pixels')
def exported_pixels():
    with database(fixture.config) as db:
        row = db.execute('SELECT folder FROM exports WHERE study=? ORDER BY id DESC', (fixture.uid,)).fetchone()
    paths = list((Path(row['folder']) / 'DICOM').glob('*.dcm')) if row else []
    exact = bool(paths)
    for path in fixture.sources[0].iterdir():
        original = pydicom.dcmread(path)
        if str(original.StudyInstanceUID) != fixture.uid:
            continue
        expected = original.pixel_array.copy()
        if str(original.SOPInstanceUID) == str(fixture.original.SOPInstanceUID):
            expected[8:16, 8:16] = 0
        if str(original.SeriesInstanceUID) == fixture.series:
            expected[24:32, 24:32] = 0
        target = Path(row['folder']) / 'DICOM' / (research_uid(fixture.config, str(original.SOPInstanceUID)) + '.dcm') if row else None
        exact = exact and target.is_file() and np.array_equal(pydicom.dcmread(target).pixel_array, expected)
    return jsonify(count=len(paths), scoped_pixels_exact=bool(exact),
                   source_unchanged=all(hashlib.sha256(path.read_bytes()).hexdigest() == digest for path, digest in source_hashes.items()))

original_prepare = engine.prepare
def prepare(*args, **kwargs):
    kwargs['nlp'] = workflow.NoNLP()
    return original_prepare(*args, **kwargs)
engine.prepare = prepare

def worker():
    while True:
        process_one(fixture.config)
        time.sleep(.1)
threading.Thread(target=worker, daemon=True).start()

if __name__ == '__main__':
    logging.getLogger('werkzeug').setLevel(logging.ERROR)
    app.run(host='127.0.0.1', port=8766, threaded=True, use_reloader=False)
