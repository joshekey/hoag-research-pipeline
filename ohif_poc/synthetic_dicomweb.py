"""Synthetic-only DICOMweb fixture for OHIF wheel and six-series testing.

NO clinical images, disk reads, production routes, credentials, or server launcher.
Only the existing explicitly local test server may import create_test_app.
"""
import io
import json
from functools import lru_cache

from flask import Flask, Response, abort
from pydicom.dataset import FileDataset, FileMetaDataset
from pydicom.uid import ExplicitVRLittleEndian

ROOT = "/ohif-pilot/dicomweb"
STUDY = "1.2.826.0.1.3680043.10.543.100"
COUNTS = (30, 28, 27, 26, 24, 22)  # 157 synthetic single-frame instances
SERIES_IDS = tuple(STUDY + "." + str(i) for i in range(1, 7))
SERIES = SERIES_IDS[0]  # Compatibility for older synthetic fixture tests
SOPS = tuple(SERIES + "." + str(i) for i in range(1, COUNTS[0] + 1))
SOP_CLASS = "1.2.840.10008.5.1.4.1.1.4"
DICOM_JSON = "application/dicom+json"
DIM = 64

# Keep all identifiers synthetic, never copy metadata from hospital studies.
INSTANCES = {
    series: tuple(series + "." + str(n) for n in range(1, count + 1))
    for series, count in zip(SERIES_IDS, COUNTS)
}
INDEX = {
    sop: (series, number)
    for series, sops in INSTANCES.items()
    for number, sop in enumerate(sops, 1)
}


@lru_cache(maxsize=160)
def synthetic_dataset(sop):
    if sop not in INDEX:
        raise KeyError("Synthetic SOP not found")
    series, number = INDEX[sop]
    series_num = SERIES_IDS.index(series) + 1
    meta = FileMetaDataset()
    meta.MediaStorageSOPClassUID = SOP_CLASS
    meta.MediaStorageSOPInstanceUID = sop
    meta.TransferSyntaxUID = ExplicitVRLittleEndian
    meta.ImplementationClassUID = "1.2.826.0.1.3680043.10.543.1"
    ds = FileDataset(None, {}, file_meta=meta, preamble=b"\0" * 128)
    ds.SOPClassUID = SOP_CLASS
    ds.SOPInstanceUID = sop
    ds.StudyInstanceUID = STUDY
    ds.SeriesInstanceUID = series
    ds.Modality = "MR"
    ds.SeriesDescription = "SYNTHETIC TEST SERIES " + str(series_num)
    ds.StudyDescription = "SYNTHETIC OHIF SCROLL TEST"
    ds.PatientName = "SYNTHETIC^ONLY"
    ds.PatientID = "NONCLINICAL"
    ds.StudyDate = "20000101"
    ds.StudyTime = "120000"
    ds.SeriesNumber = series_num
    ds.InstanceNumber = number
    ds.Rows = DIM
    ds.Columns = DIM
    ds.SamplesPerPixel = 1
    ds.PhotometricInterpretation = "MONOCHROME2"
    ds.BitsAllocated = 16
    ds.BitsStored = 16
    ds.HighBit = 15
    ds.PixelRepresentation = 0
    ds.PixelSpacing = ["1", "1"]
    ds.ImageOrientationPatient = ["1", "0", "0", "0", "1", "0"]
    ds.ImagePositionPatient = ["0", "0", str(number)]
    # A distinct grayscale pattern for each synthetic image, without PHI.
    pixels = bytearray(DIM * DIM * 2)
    for y in range(DIM):
        for x in range(DIM):
            value = 200 + ((x * 17 + y * 9 + number * 59 + series_num * 107) % 3600)
            pos = 2 * (y * DIM + x)
            pixels[pos:pos + 2] = value.to_bytes(2, "little")
    ds.PixelData = bytes(pixels)
    return ds


def json_metadata(ds):
    result = ds.to_json_dict()
    result["7FE00010"] = {
        "vr": "OW",
        "BulkDataURI": (
            ROOT + "/studies/" + STUDY + "/series/" + str(ds.SeriesInstanceUID)
            + "/instances/" + str(ds.SOPInstanceUID) + "/frames/1"
        ),
    }
    return result


def response_json(entries):
    return Response(
        json.dumps(entries), content_type=DICOM_JSON,
        headers={"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff"},
    )


def allowed(study, series=None, sop=None):
    return (
        study == STUDY
        and (series is None or series in INSTANCES)
        and (sop is None or sop in INSTANCES.get(series, ()))
    )


def create_test_app():
    """Synthetic-only Flask fixture; never register inside clinical HOAG."""
    app = Flask(__name__)

    @app.get(ROOT + "/studies")
    def studies():
        return response_json([{
            "0020000D": {"vr": "UI", "Value": [STUDY]},
            "00201206": {"vr": "IS", "Value": [len(COUNTS)]},
            "00201208": {"vr": "IS", "Value": [sum(COUNTS)]},
            "00080020": {"vr": "DA", "Value": ["20000101"]},
        }])

    @app.get(ROOT + "/studies/<study>/series")
    def series_list(study):
        if not allowed(study):
            abort(404)
        return response_json([
            {
                "0020000D": {"vr": "UI", "Value": [STUDY]},
                "0020000E": {"vr": "UI", "Value": [series]},
                "00080060": {"vr": "CS", "Value": ["MR"]},
                "0008103E": {"vr": "LO", "Value": ["SYNTHETIC TEST SERIES " + str(i)]},
                "00200011": {"vr": "IS", "Value": [i]},
                "00201209": {"vr": "IS", "Value": [len(INSTANCES[series])]},
            }
            for i, series in enumerate(SERIES_IDS, 1)
        ])

    @app.get(ROOT + "/studies/<study>/series/<series>/instances")
    def instances(study, series):
        if not allowed(study, series):
            abort(404)
        return response_json([
            json_metadata(synthetic_dataset(sop)) for sop in INSTANCES[series]
        ])

    @app.get(ROOT + "/studies/<study>/metadata")
    def study_metadata(study):
        if not allowed(study):
            abort(404)
        return response_json([
            json_metadata(synthetic_dataset(sop))
            for series in SERIES_IDS for sop in INSTANCES[series]
        ])

    @app.get(ROOT + "/studies/<study>/series/<series>/metadata")
    def series_metadata(study, series):
        if not allowed(study, series):
            abort(404)
        return response_json([
            json_metadata(synthetic_dataset(sop)) for sop in INSTANCES[series]
        ])

    @app.get(ROOT + "/studies/<study>/series/<series>/instances/<sop>/metadata")
    def instance_metadata(study, series, sop):
        if not allowed(study, series, sop):
            abort(404)
        return response_json([json_metadata(synthetic_dataset(sop))])

    @app.get(ROOT + "/studies/<study>/series/<series>/instances/<sop>")
    def instance(study, series, sop):
        if not allowed(study, series, sop):
            abort(404)
        binary = io.BytesIO()
        synthetic_dataset(sop).save_as(binary, enforce_file_format=True)
        boundary = "ohif-synthetic-test"
        body = (
            b"--" + boundary.encode()
            + b"\r\nContent-Type: application/dicom\r\n\r\n" + binary.getvalue()
            + b"\r\n--" + boundary.encode() + b"--\r\n"
        )
        return Response(
            body,
            content_type='multipart/related; type="application/dicom"; boundary=' + boundary,
            headers={"Cache-Control": "no-store"},
        )

    @app.get(ROOT + "/studies/<study>/series/<series>/instances/<sop>/frames/<int:frame>")
    def frame(study, series, sop, frame):
        if not allowed(study, series, sop) or frame != 1:
            abort(404)
        boundary = "ohif-synthetic-frame"
        body = (
            b"--" + boundary.encode()
            + b"\r\nContent-Type: application/octet-stream\r\n\r\n"
            + bytes(synthetic_dataset(sop).PixelData)
            + b"\r\n--" + boundary.encode() + b"--\r\n"
        )
        return Response(
            body,
            content_type=(
                'multipart/related; type="application/octet-stream"; boundary=' + boundary
            ),
            headers={"Cache-Control": "no-store"},
        )

    return app
