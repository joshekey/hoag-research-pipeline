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
from contextlib import closing
from pathlib import Path

import pydicom
from flask import Blueprint, Response, abort, request, g, has_request_context
from pydicom.uid import (ExplicitVRLittleEndian, ImplicitVRLittleEndian,
                         JPEGLossless, JPEGLosslessSV1, JPEG2000Lossless,
                         JPEGBaseline8Bit, JPEGExtended12Bit, JPEG2000,
                         JPEGLSLossless, JPEGLSNearLossless, RLELossless)
from pydicom.encaps import generate_frames

from ohif_poc.clinical_retrieval_core import instance_bytes
from ohif_poc.scoped_catalog import scoped_catalog, request_cached
from ohif_poc.historical_retrieval import historical_index, historical_source, historical_bytes, allowed_uids

ROOT = "/ohif/dicomweb"
UID_RE = re.compile(r"^[0-9]+(?:\.[0-9]+)*$")
MAX_PIXEL_BYTES = 128 * 1024 * 1024
MAX_INSTANCE_BYTES = 128 * 1024 * 1024
SUPPORTED_SYNTAXES = {
    str(ExplicitVRLittleEndian): "application/octet-stream",
    str(ImplicitVRLittleEndian): "application/octet-stream",
    str(JPEGLossless): "image/jpeg",
    str(JPEGLosslessSV1): "image/jpeg",
    str(JPEG2000Lossless): "image/jp2",
    str(JPEG2000): "image/jp2",
    str(JPEGBaseline8Bit): "image/jpeg",
    str(JPEGExtended12Bit): "image/jpeg",
    str(JPEGLSLossless): "image/jls",
    str(JPEGLSNearLossless): "image/jls",
    str(RLELossless): "image/dicom-rle",
}

# This config is NOT in the git repository or sourced from browser input.
ALLOWLIST = Path("/etc/hoag-research/ohif-pilot-study.uid")


@request_cached
def allowed_studies(config, requested=None):
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
    with closing(sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True)) as db:
        ready = db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0] != "1":
            abort(404)
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            abort(404)
        scope = " AND s.uid=?" if requested else ""
        rows = db.execute("""SELECT s.uid FROM studies s WHERE s.count > 0
            AND s.state NOT IN ('conflict','needs_review')
            AND (SELECT count(*) FROM files f WHERE f.kind='dicom' AND f.active=1
                AND f.status='ok' AND json_extract(f.metadata,'$.StudyInstanceUID')=s.uid)=s.count
            """ + scope + " ORDER BY s.uid", (requested,) if requested else ()).fetchall()
    active = [row[0] for row in rows]
    if selector == "ALL_INDEXED":
        if not active and not requested:
            abort(404)
        return active
    if requested and selector != requested:
        return []
    if selector not in active:
        abort(404)
    return [selector]


def authorized_uid(config, requested_uid):
    if requested_uid not in allowed_studies(config, requested_uid):
        abort(404)
    return requested_uid


def dicom_json(data):
    return Response(json.dumps(data), content_type="application/dicom+json")


@request_cached
def get_index(config, study):
    if has_request_context() and g.get('ohif_viewer_scope') not in (None, study):
        abort(404)
    if study in allowed_studies(config, study):
        return scoped_catalog(config, study, study)
    return historical_index(config, study)


def viewer_authorized_uid(config, uid):
    if uid in allowed_studies(config, uid):
        return uid
    if uid in allowed_uids(config):
        return uid
    abort(404)


def is_historical(config, uid):
    return uid not in allowed_studies(config, uid)


def checked_record(config, study, series, sop):
    indexed = get_index(config, study)
    row = indexed.get((series, sop))
    if not row:
        abort(404)
    return row


def metadata_for(config, uid, series, sop):
    row = checked_record(config, uid, series, sop)
    # Verify original index-root containment, source mtime/size, and header.
    import engine
    path = historical_source(config,uid,series,sop)[0] if is_historical(config,uid) else engine.source_path(config, row)
    ds = pydicom.dcmread(path, stop_before_pixels=True)
    if tuple(str(ds.get(k, '')) for k in ('StudyInstanceUID', 'SeriesInstanceUID', 'SOPInstanceUID')) != (uid, series, sop):
        raise ValueError('DICOM source/catalog identifiers disagree')
    if not is_historical(config, uid):
        engine.source_path(config, row)
    meta = ds.to_json_dict()
    syntax = str(ds.file_meta.TransferSyntaxUID)
    if syntax not in SUPPORTED_SYNTAXES:
        abort(415)
    meta["00020010"] = {"vr": "UI", "Value": [syntax]}
    meta["7FE00010"] = {
        "vr": "OW",
        "BulkDataURI": (
            (ROOT + '/scope/' + g.ohif_viewer_scope if has_request_context() and g.get('ohif_viewer_scope') else ROOT)
            + "/studies/" + uid + "/series/" + series
            + "/instances/" + sop + "/frames/1"
        ),
    }
    return meta


def qido_study(study, series_count, instance_count, metadata=None):
    result = {
        "0020000D": {"vr": "UI", "Value": [study]},
        "00201206": {"vr": "IS", "Value": [series_count]},
        "00201208": {"vr": "IS", "Value": [instance_count]},
    }
    meta = metadata or {}
    for tag, vr, key in [('00100020', 'LO', 'PatientID'), ('00100030', 'DA', 'PatientBirthDate'),
                         ('00080020', 'DA', 'StudyDate'), ('00080050', 'SH', 'AccessionNumber'),
                         ('00081030', 'LO', 'StudyDescription'), ('00080061', 'CS', 'Modality')]:
        if meta.get(key):
            result[tag] = {'vr': vr, 'Value': [str(meta[key])]}
    if meta.get('PatientName'):
        result['00100010'] = {'vr': 'PN', 'Value': [{'Alphabetic': str(meta['PatientName'])}]}
    return result


def multipart(payload, media, label, syntax=None):
    boundary = "hoag-ohif-pilot-" + label
    part_type = media + ('; transfer-syntax=' + syntax if syntax else '')
    body = (b"--" + boundary.encode() + b"\r\nContent-Type: "
            + part_type.encode() + b"\r\n\r\n" + payload
            + b"\r\n--" + boundary.encode() + b"--\r\n")
    return Response(body, content_type=(
        'multipart/related; type="' + media + '"; boundary=' + boundary
        + ('; transfer-syntax=' + syntax if syntax else '')
    ))


def create_blueprint(config):
    """Register only inside webapp.create_app so Basic auth covers all routes."""
    bp = Blueprint("ohif_pilot", __name__)

    @bp.url_value_preprocessor
    def bind_viewer_scope(endpoint, values):
        scope = values.pop('scope', None) if values else None
        if scope is not None:
            if not UID_RE.fullmatch(scope) or (values.get('uid') and values['uid'] != scope):
                abort(404)
            g.ohif_viewer_scope = scope

    @bp.get(ROOT + "/studies")
    @bp.get(ROOT + "/scope/<scope>/studies")
    def studies():
        result = []
        selected = request.args.get("StudyInstanceUID", "").strip()
        scope = g.get('ohif_viewer_scope')
        if scope:
            if selected and selected != scope:
                abort(404)
            selected = scope
        if selected:
            viewer_authorized_uid(config, selected)
            uids = [selected]
        else:
            uids = allowed_studies(config)
        for uid in uids:
            indexed = get_index(config, uid)
            result.append(qido_study(
                uid, len({s for s, _ in indexed}), len(indexed),
                json.loads(next(iter(indexed.values())).get('metadata', '{}')) if indexed else {}
            ))
        return dicom_json(result)

    @bp.get(ROOT + "/studies/<uid>/series")
    @bp.get(ROOT + "/scope/<scope>/studies/<uid>/series")
    def series(uid):
        indexed = get_index(config, uid)
        counts = Counter(s for s, _ in indexed)
        result = []
        for series_uid, count in sorted(counts.items()):
            # Never infer modality: CT, MR, US etc. must reflect the actual source.
            sop = next(sop for series, sop in indexed if series == series_uid)
            row = indexed[(series_uid, sop)]
            meta = json.loads(row["metadata"])
            modality = str(meta.get("Modality") or "").strip().upper()
            if not re.fullmatch(r"[A-Z0-9_]{1,16}", modality):
                modality = "OT"
            result.append({
                "0020000D": {"vr": "UI", "Value": [uid]},
                "0020000E": {"vr": "UI", "Value": [series_uid]},
                "00201209": {"vr": "IS", "Value": [count]},
                "00080060": {"vr": "CS", "Value": [modality]},
            })
        return dicom_json(result)

    @bp.get(ROOT + "/studies/<uid>/metadata")
    @bp.get(ROOT + "/scope/<scope>/studies/<uid>/metadata")
    def study_metadata(uid):
        indexed = get_index(config, uid)
        return dicom_json([metadata_for(config, uid, s, sop) for s, sop in indexed])

    @bp.get(ROOT + "/studies/<uid>/series/<series>/instances")
    @bp.get(ROOT + "/studies/<uid>/series/<series>/metadata")
    @bp.get(ROOT + "/scope/<scope>/studies/<uid>/series/<series>/instances")
    @bp.get(ROOT + "/scope/<scope>/studies/<uid>/series/<series>/metadata")
    def series_metadata(uid, series):
        indexed = get_index(config, uid)
        selected = [(s, sop) for s, sop in indexed if s == series]
        if not selected:
            abort(404)
        return dicom_json([metadata_for(config, uid, s, sop) for s, sop in selected])

    @bp.get(ROOT + "/studies/<uid>/series/<series>/instances/<sop>/metadata")
    @bp.get(ROOT + "/scope/<scope>/studies/<uid>/series/<series>/instances/<sop>/metadata")
    def instance_metadata(uid, series, sop):
        return dicom_json([metadata_for(config, uid, series, sop)])

    @bp.get(ROOT + "/studies/<uid>/series/<series>/instances/<sop>")
    @bp.get(ROOT + "/scope/<scope>/studies/<uid>/series/<series>/instances/<sop>")
    def instance(uid, series, sop):
        checked_record(config, uid, series, sop)
        raw = historical_bytes(config, uid, series, sop) if is_historical(config,uid) else instance_bytes(config, uid, uid, series, sop, max_bytes=MAX_INSTANCE_BYTES)
        return multipart(raw, "application/dicom", "instance")

    @bp.get(ROOT + "/studies/<uid>/series/<series>/instances/<sop>/frames/<int:frame>")
    @bp.get(ROOT + "/scope/<scope>/studies/<uid>/series/<series>/instances/<sop>/frames/<int:frame>")
    def frame(uid, series, sop, frame):
        checked_record(config, uid, series, sop)
        raw = historical_bytes(config, uid, series, sop) if is_historical(config,uid) else instance_bytes(config, uid, uid, series, sop, max_bytes=MAX_INSTANCE_BYTES)
        ds = pydicom.dcmread(io.BytesIO(raw))
        frame_count = int(ds.get("NumberOfFrames", 1) or 1)
        if frame < 1 or frame > frame_count:
            abort(404)
        syntax = str(ds.file_meta.TransferSyntaxUID)
        media = SUPPORTED_SYNTAXES.get(syntax)
        if media is None:
            abort(415)
        if media == "application/octet-stream":
            # Native frames are contiguous; 1-bit frames may begin mid-byte.
            from pydicom.pixels.utils import get_expected_length
            if int(ds.get('BitsAllocated', 0)) == 1:
                abort(415)  # bit-packed SEG requires a dedicated renderer
            frame_size = get_expected_length(ds, unit='bytes') // frame_count
            if not frame_size or len(ds.PixelData) < frame_size * frame_count:
                abort(415)
            pixels = bytes(ds.PixelData[(frame-1)*frame_size:frame*frame_size])
        else:
            # Return the single original encapsulated frame. No decompression,
            # transcoding, or changes to original DICOM image samples.
            try:
                from itertools import islice
                frames = generate_frames(ds.PixelData, number_of_frames=frame_count)
                pixels = next(islice(frames, frame-1, frame))
            except (ValueError, TypeError, KeyError, StopIteration):
                abort(415)
        if not pixels or len(pixels) > MAX_PIXEL_BYTES:
            abort(413 if len(pixels) > MAX_PIXEL_BYTES else 415)
        return multipart(pixels, media, "frame", syntax)

    return bp
