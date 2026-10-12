"""Read-only historical DICOM retrieval for explicitly gated EMR review studies.

Never modifies workflow.sqlite or active flags. Every request must pass the
historical report-review gate AND match an independently verified unique
study/report association. Paths, SOPs and source headers are checked anew.
"""
import io
import json
import sqlite3
import time
from pathlib import Path
from threading import RLock

import pydicom
from flask import abort
from ohif_poc.scoped_catalog import _uid
import engine
import historical_emr_review

MAX_BYTES = 128 * 1024 * 1024
_LOCK = RLock()
_CACHE = (0.0, ())
CACHE_SECONDS = 25


def allowed_uids(config):
    """Short-lived in-process cache, with gate checked on every access."""
    if not historical_emr_review.permitted():
        abort(404)
    global _CACHE
    now=time.monotonic()
    with _LOCK:
        stamp, entries=_CACHE
        if now-stamp > CACHE_SECONDS or not entries:
            entries=tuple(item["uid"] for item in historical_emr_review.eligible_pairs(config))
            _CACHE=(now,entries)
    return entries


def historical_index(config, uid):
    uid=_uid(uid)
    if uid not in allowed_uids(config):
        abort(404)
    catalog=Path(config["state_dir"])/"workflow.sqlite"
    with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():
            abort(404)
        rows=[dict(row) for row in db.execute("""SELECT * FROM files
             WHERE kind='dicom' AND active=0 AND status='ok'
             AND json_extract(metadata,'$.StudyInstanceUID')=? ORDER BY id""",(uid,))]
    if not rows:
        abort(404)
    mapped={}
    for row in rows:
        meta=json.loads(row["metadata"])
        series=_uid(meta.get("SeriesInstanceUID",""))
        sop=_uid(meta.get("SOPInstanceUID",""))
        if (series,sop) in mapped:
            abort(404)
        mapped[(series,sop)]=row
    return mapped


def historical_source(config, uid, series, sop):
    row=historical_index(config,uid).get((_uid(series),_uid(sop)))
    if not row:
        abort(404)
    try:
        source=engine.source_path(config,row)
        ds=pydicom.dcmread(source,stop_before_pixels=True,
                           specific_tags=["StudyInstanceUID","SeriesInstanceUID",
                                          "SOPInstanceUID","PatientName"])
        meta=json.loads(row["metadata"])
        if any(str(ds.get(key,""))!=expected for key,expected in
               (("StudyInstanceUID",uid),("SeriesInstanceUID",series),
                ("SOPInstanceUID",sop),("PatientName",str(meta.get("PatientName",""))))):
            abort(404)
        return source,row
    except (OSError,ValueError,pydicom.errors.InvalidDicomError):
        abort(404)


def historical_bytes(config, uid, series, sop):
    source,row=historical_source(config,uid,series,sop)
    if not 132<=row["size"]<=MAX_BYTES:
        abort(413)
    try:
        with source.open("rb") as stream:
            data=stream.read(MAX_BYTES+1)
        if len(data)!=row["size"]:
            abort(404)
        # Verify returned bytes still belong to requested study/SOP.
        ds=pydicom.dcmread(io.BytesIO(data),stop_before_pixels=True)
        if (str(ds.get("StudyInstanceUID","")),
            str(ds.get("SeriesInstanceUID","")),
            str(ds.get("SOPInstanceUID",""))) != (uid,series,sop):
            abort(404)
        engine.source_path(config,row)
        return data
    except (OSError,ValueError,pydicom.errors.InvalidDicomError):
        abort(404)
