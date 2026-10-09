"""SYNTHETIC ONLY: minimal DICOMweb protocol fixture for OHIF tests.

Never serve hospital DICOM with this module. It has no production routes,
config loading, clinical disk access, server launcher or authentication.
Only instantiate with Flask.test_client() in isolated unit tests.
"""
import io
import json
import uuid

from flask import Flask, Response, abort
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian

ROOT = "/ohif-pilot/dicomweb"
STUDY = "1.2.826.0.1.3680043.10.543.100"
SERIES = STUDY + ".1"
SOPS = (SERIES + ".1", SERIES + ".2")
SOP_CLASS = "1.2.840.10008.5.1.4.1.1.4"
DICOM_JSON = "application/dicom+json"

def synthetic_dataset(sop, instance):
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = SOP_CLASS
    meta.MediaStorageSOPInstanceUID = sop
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = "1.2.826.0.1.3680043.10.543.1"
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.is_little_endian = True
    ds.is_implicit_VR = False
    ds.SOPClassUID = SOP_CLASS
    ds.SOPInstanceUID = sop
    ds.StudyInstanceUID = STUDY
    ds.SeriesInstanceUID = SERIES
    ds.Modality = "MR"
    ds.PatientName = "SYNTHETIC^ONLY"
    ds.PatientID = "NONCLINICAL"
    ds.StudyDate = "20000101"
    ds.StudyTime = "120000"
    ds.SeriesNumber = 1
    ds.InstanceNumber = instance
    ds.Rows = 2
    ds.Columns = 2
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.PixelSpacing = ["1", "1"]
    ds.ImageOrientationPatient = ["1", "0", "0", "0", "1", "0"]
    ds.ImagePositionPatient = ["0", "0", str(instance)]
    ds.PixelData = b"\x00\x00\x01\x00\x02\x00\x03\x00"
    return ds

DATA = [synthetic_dataset(sop, n) for n, sop in enumerate(SOPS, 1)]

def json_metadata(ds):
    payload = ds.to_json_dict()
    # Pixel data delivered at WADO frames URI rather than inlining.
    payload["7FE00010"] = {"vr": "OW", "BulkDataURI": (
        ROOT + "/studies/" + STUDY + "/series/" + SERIES
        + "/instances/" + str(ds.SOPInstanceUID) + "/frames/1"
    )}
    return payload

def response_json(entries):
    return Response(json.dumps(entries), content_type=DICOM_JSON,
                    headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"})

def create_test_app():
    """Do NOT use Flask.run() or register in HOAG production routes."""
    app = Flask(__name__)

    @app.get(ROOT + "/studies")
    def studies():
        return response_json([{
            "0020000D": {"vr": "UI", "Value": [STUDY]},
            "00201206": {"vr": "IS", "Value": [1]},
            "00201208": {"vr": "IS", "Value": [len(DATA)]},
            "00080020": {"vr": "DA", "Value": ["20000101"]},
        }])

    @app.get(ROOT + "/studies/<study>/series")
    def series(study):
        if study != STUDY: abort(404)
        return response_json([{
            "0020000D": {"vr":"UI","Value":[STUDY]},
            "0020000E": {"vr":"UI","Value":[SERIES]},
            "00080060": {"vr":"CS","Value":["MR"]},
            "00201209": {"vr":"IS","Value":[len(DATA)]},
        }])

    @app.get(ROOT + "/studies/<study>/series/<series>/instances")
    def instances(study, series):
        if study != STUDY or series != SERIES: abort(404)
        return response_json([json_metadata(ds) for ds in DATA])

    @app.get(ROOT + "/studies/<study>/metadata")
    def study_metadata(study):
        if study != STUDY: abort(404)
        return response_json([json_metadata(ds) for ds in DATA])

    @app.get(ROOT + "/studies/<study>/series/<series>/metadata")
    def series_metadata(study, series):
        if study != STUDY or series != SERIES: abort(404)
        return response_json([json_metadata(ds) for ds in DATA])

    @app.get(ROOT + "/studies/<study>/series/<series>/instances/<sop>/metadata")
    def instance_metadata(study, series, sop):
        if study != STUDY or series != SERIES or sop not in SOPS: abort(404)
        return response_json([json_metadata(DATA[SOPS.index(sop)])])

    @app.get(ROOT + "/studies/<study>/series/<series>/instances/<sop>")
    def instance(study, series, sop):
        if study != STUDY or series != SERIES or sop not in SOPS: abort(404)
        data = io.BytesIO()
        DATA[SOPS.index(sop)].save_as(data, enforce_file_format=True)
        boundary = "ohif-synthetic-test"
        contents = data.getvalue()
        body = (b"--" + boundary.encode() +
                b"\r\nContent-Type: application/dicom\r\n\r\n" + contents +
                b"\r\n--" + boundary.encode() + b"--\r\n")
        return Response(body, content_type=(
            'multipart/related; type="application/dicom"; boundary=' + boundary),
            headers={"Cache-Control":"no-store"})

    @app.get(ROOT + "/studies/<study>/series/<series>/instances/<sop>/frames/<int:frame>")
    def frame(study, series, sop, frame):
        if study != STUDY or series != SERIES or sop not in SOPS or frame != 1: abort(404)
        boundary = "ohif-synthetic-frame"
        body = (b"--" + boundary.encode() +
                b'\r\nContent-Type: application/octet-stream\r\n\r\n' +
                bytes(DATA[SOPS.index(sop)].PixelData) +
                b"\r\n--" + boundary.encode() + b"--\r\n")
        return Response(body, content_type=(
            'multipart/related; type="application/octet-stream"; boundary=' + boundary),
            headers={"Cache-Control":"no-store"})

    return app
