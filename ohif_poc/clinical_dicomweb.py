"""HOAG catalog-scoped DICOMweb viewer API — reuses dashboard login.

Every route is registered UNDER the existing Flask application's mandatory
Basic authentication middleware. No STOW/upload or unrestricted study search.
The root-controlled selector enables a single UID or the complete eligible indexed catalog.
Original DICOM may contain PHI; do not log response bodies or request paths.
NOT a de-identified export, not final clinical review approval.
"""
import io
import json
import re
import sqlite3
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


def allowed_studies(config):
    """Root-controlled selector: one pilot UID, or explicitly enabled ALL_INDEXED.

    ALL_INDEXED includes only active complete catalog studies; each DICOM
    retrieval is still independently checked against its series/SOP records.
    A missing/invalid selector fails closed. This endpoint inherits HOAG login.
    """
    if ALLOWLIST.is_symlink() or not ALLOWLIST.is_file():
        abort(404)
    if ALLOWLIST.stat().st_size > 128:
        abort(404)
    selector = ALLOWLIST.read_text().strip()
    if selector != "ALL_INDEXED" and not UID_RE.fullmatch(selector):
        abort(404)
    catalog = Path(config["state_dir"]) / "workflow.sqlite"
    with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as db:
        ready = db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0] != "1":
            abort(404)
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            abort(404)
        rows = db.execute("""SELECT s.uid FROM studies s WHERE s.count > 0
            AND s.state NOT IN ('conflict','needs_review')
            AND (SELECT count(*) FROM files f WHERE f.kind='dicom' AND f.active=1
                AND f.status='ok' AND json_extract(f.metadata,'$.StudyInstanceUID')=s.uid)=s.count
            ORDER BY s.uid""").fetchall()
    active = [row[0] for row in rows]
    if selector == "ALL_INDEXED":
        if not active:
            abort(404)
        return active
    return [selector] if selector in active else []


def authorized_uid(config, requested_uid):
    if requested_uid not in allowed_studies(config):
        abort(404)
    return requested_uid


def dicom_json(data):
    return Response(json.dumps(data), content_type="application/dicom+json")


def get_index(config, study):
    authorized_uid(config, study)
    return scoped_catalog(config, study, study)


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
        result = []
        for uid in allowed_studies(config):
            indexed = get_index(config, uid)
            result.append(qido_study(
                uid, len({s for s, _ in indexed}), len(indexed)
            ))
        return dicom_json(result)

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
