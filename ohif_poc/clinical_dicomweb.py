"""HOAG single-study DICOMweb pilot API — no public server or login.

Every route is registered UNDER the existing Flask application's mandatory
Basic authentication middleware. No STOW/upload or unrestricted study search.
The allowlist is a root-controlled, read-only single-UID file, NOT browser input.
Original DICOM may contain PHI; do not log response bodies or request paths.
NOT a de-identified export, not final clinical review approval.
"""
import io
import json
import re
from collections import Counter
from pathlib import Path

import pydicom
from flask import Blueprint, Response, abort, request
from pydicom.uid import ExplicitVRLittleEndian, ImplicitVRLittleEndian

from ohif_poc.clinical_retrieval_core import instance_bytes
from ohif_poc.scoped_catalog import scoped_catalog

ROOT = "/ohif/dicomweb"
UID_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")
MAX_PIXEL_BYTES = 128 * 1024 * 1024
MAX_INSTANCE_BYTES = 128 * 1024 * 1024

# This config is NOT in the git repository or sourced from browser input.
ALLOWLIST = Path("/etc/hoag-research/ohif-pilot-study.uid")


def enabled_study():
    if not ALLOWLIST.is_file() or ALLOWLIST.is_symlink():
        abort(404)
    if ALLOWLIST.stat().st_size > 128:
        abort(404)
    uid = ALLOWLIST.read_text().strip()
    if not UID_RE.fullmatch(uid):
        abort(404)
    return uid


def dicom_json(data):
    return Response(json.dumps(data), content_type="application/dicom+json")


def get_index(config, study):
    allow = enabled_study()
    if study != allow:
        abort(404)
    return scoped_catalog(config, study, allow)


def checked_record(config, study, series, sop):
    indexed = get_index(config, study)
    row = indexed.get((series, sop))
    if not row:
        abort(404)
    return row


def metadata_for(config, uid, series, sop):
    row = checked_record(config, uid, series, sop)
    # Verify original index-root containment, source mtime/size, and header.
    from ohif_poc.scoped_catalog import scoped_instance
    path = scoped_instance(config, uid, uid, series, sop)
    ds = pydicom.dcmread(path, stop_before_pixels=True)
    meta = ds.to_json_dict()
    meta["7FE00010"] = {
        "vr": "OW",
        "BulkDataURI": (
            ROOT + "/studies/" + uid + "/series/" + series
            + "/instances/" + sop + "/frames/1"
        ),
    }
    return meta


def qido_study(study, series_count, instance_count):
    return {
        "0020000D": {"vr": "UI", "Value": [study]},
        "00201206": {"vr": "IS", "Value": [series_count]},
        "00201208": {"vr": "IS", "Value": [instance_count]},
    }


def multipart(payload, media, label):
    boundary = "hoag-ohif-pilot-" + label
    body = (b"--" + boundary.encode() + b"\r\nContent-Type: "
            + media.encode() + b"\r\n\r\n" + payload
            + b"\r\n--" + boundary.encode() + b"--\r\n")
    return Response(body, content_type=(
        'multipart/related; type="' + media + '"; boundary=' + boundary
    ))


def create_blueprint(config):
    """Register only inside webapp.create_app so Basic auth covers all routes."""
    bp = Blueprint("ohif_pilot", __name__)

    @bp.get(ROOT + "/studies")
    def studies():
        uid = enabled_study()
        indexed = get_index(config, uid)
        return dicom_json([qido_study(
            uid, len({s for s, _ in indexed}), len(indexed)
        )])

    @bp.get(ROOT + "/studies/<uid>/series")
    def series(uid):
        indexed = get_index(config, uid)
        counts = Counter(s for s, _ in indexed)
        return dicom_json([{
            "0020000D": {"vr": "UI", "Value": [uid]},
            "0020000E": {"vr": "UI", "Value": [s]},
            "00201209": {"vr": "IS", "Value": [n]},
            "00080060": {"vr": "CS", "Value": ["MR"]},
        } for s, n in sorted(counts.items())])

    @bp.get(ROOT + "/studies/<uid>/metadata")
    def study_metadata(uid):
        indexed = get_index(config, uid)
        return dicom_json([metadata_for(config, uid, s, sop) for s, sop in indexed])

    @bp.get(ROOT + "/studies/<uid>/series/<series>/instances")
    @bp.get(ROOT + "/studies/<uid>/series/<series>/metadata")
    def series_metadata(uid, series):
        indexed = get_index(config, uid)
        selected = [(s, sop) for s, sop in indexed if s == series]
        if not selected:
            abort(404)
        return dicom_json([metadata_for(config, uid, s, sop) for s, sop in selected])

    @bp.get(ROOT + "/studies/<uid>/series/<series>/instances/<sop>/metadata")
    def instance_metadata(uid, series, sop):
        return dicom_json([metadata_for(config, uid, series, sop)])

    @bp.get(ROOT + "/studies/<uid>/series/<series>/instances/<sop>")
    def instance(uid, series, sop):
        checked_record(config, uid, series, sop)
        raw = instance_bytes(config, uid, uid, series, sop, max_bytes=MAX_INSTANCE_BYTES)
        return multipart(raw, "application/dicom", "instance")

    @bp.get(ROOT + "/studies/<uid>/series/<series>/instances/<sop>/frames/<int:frame>")
    def frame(uid, series, sop, frame):
        if frame != 1:
            abort(404)  # pilot preflight established single-frame objects only
        checked_record(config, uid, series, sop)
        raw = instance_bytes(config, uid, uid, series, sop, max_bytes=MAX_INSTANCE_BYTES)
        ds = pydicom.dcmread(io.BytesIO(raw))
        if int(ds.get("NumberOfFrames", 1) or 1) != 1:
            abort(415)
        syntax = ds.file_meta.TransferSyntaxUID
        if syntax not in (ExplicitVRLittleEndian, ImplicitVRLittleEndian):
            abort(415)  # Do not send compressed frames with incorrect media type
        pixels = bytes(ds.PixelData)
        if len(pixels) > MAX_PIXEL_BYTES:
            abort(413)
        return multipart(pixels, "application/octet-stream", "frame")

    return bp
