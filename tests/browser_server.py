"""Loopback-only synthetic application for browser regression tests."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1]))
import threading
import time
import logging
import io
from unittest.mock import patch
import numpy as np
from pydicom.uid import generate_uid, JPEGBaseline8Bit
from pydicom.encaps import encapsulate
from PIL import Image
from flask import jsonify
import test_workflow as workflow
import engine
from store import database
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
engine.scan(fixture.config)
gate = fixture.root / 'viewer.uid'
gate.write_text('ALL_INDEXED')
clinical_dicomweb.ALLOWLIST = gate
fixture.config['ohif_assets'] = str(Path(__file__).parents[1] / '.browser-fixtures/ohif-assets')
app = create_app(fixture.config)

@app.get('/__synthetic_fixture')
def info():
    return jsonify(uid=fixture.uid, other=str(other.StudyInstanceUID))

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
