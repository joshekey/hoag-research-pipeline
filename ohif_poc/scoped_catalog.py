"""Study-scoped read-only DICOM catalog resolver for a FUTURE OHIF adapter.

NO HTTP routes, tokens, browser access, server launcher, or clinical data
exports are provided here. The caller must authenticate independently and
pass an authorization-scoped study UID obtained from trusted server context.
"""
import json
import re
import sqlite3
from pathlib import Path

import pydicom

import engine

UID_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]+)*\Z")


def _uid(value):
    if not isinstance(value, str) or not 1 <= len(value) <= 100 or not UID_PATTERN.fullmatch(value):
        raise ValueError("Invalid DICOM identifier")
    return value


def scoped_catalog(config, requested_study, authorized_study):
    """Return active instance mappings only for ONE independently authorized UID.

    Caller-supplied paths, patient names, series filters, and arbitrary SQL
    queries are never accepted. No patient metadata returned.
    """
    uid = _uid(requested_study)
    if uid != _uid(authorized_study):
        raise PermissionError("Requested study is not authorized")
    catalog = Path(config["state_dir"]) / "workflow.sqlite"
    with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        complete = db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not complete or complete[0] != "1":
            raise ValueError("Catalog not ready")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Processing job active")
        study = db.execute("SELECT count,state FROM studies WHERE uid=?", (uid,)).fetchone()
        if study is None or study["state"] in ("conflict", "needs_review"):
            raise ValueError("Study unavailable")
        rows = db.execute(
            """SELECT * FROM files WHERE kind='dicom' AND active=1 AND status='ok'
               AND json_extract(metadata,'$.StudyInstanceUID')=? ORDER BY id""",
            (uid,),
        ).fetchall()
        if not rows or len(rows) != study["count"]:
            raise ValueError("Study catalog incomplete")
        mapping = {}
        for row in rows:
            meta = json.loads(row["metadata"])
            if str(meta.get("StudyInstanceUID", "")) != uid:
                raise ValueError("Catalog study mismatch")
            series = _uid(str(meta.get("SeriesInstanceUID", "")))
            sop = _uid(str(meta.get("SOPInstanceUID", "")))
            key = (series, sop)
            if key in mapping:
                raise ValueError("Duplicate SOP in study")
            mapping[key] = dict(row)
    return mapping


def scoped_instance(config, requested_study, authorized_study, requested_series, requested_sop):
    """Verify a single DICOM path against catalog AND source header.

    Returns Path to read-only source. A future response layer must still
    authorize the HTTP request, bound responses, and prevent cache/log leaks.
    """
    mapping = scoped_catalog(config, requested_study, authorized_study)
    key = (_uid(requested_series), _uid(requested_sop))
    if key not in mapping:
        raise LookupError("DICOM instance unavailable")
    row = mapping[key]
    source = engine.source_path(config, row)
    ds = pydicom.dcmread(
        source, stop_before_pixels=True,
        specific_tags=["StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"],
    )
    if (str(ds.get("StudyInstanceUID", "")), str(ds.get("SeriesInstanceUID", "")),
            str(ds.get("SOPInstanceUID", ""))) != (
            requested_study, requested_series, requested_sop):
        raise ValueError("DICOM source/catalog identifiers disagree")
    return source
