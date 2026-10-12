"""Prepare a *draft* SQL narrative for manual de-identification review.

This does NOT update the legacy studies report_id/state, approve the package,
or change export eligibility. Original SQL bytes remain in the protected index.
"""
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import engine
import sql_gui_candidates
from report_format import format_report
from store import audit, database, idle

SCHEMA = """
CREATE TABLE IF NOT EXISTS sql_review_drafts (
    uid TEXT PRIMARY KEY REFERENCES studies(uid),
    source_sha256 TEXT NOT NULL,
    source_study_fingerprint TEXT NOT NULL,
    draft TEXT NOT NULL,
    prepared_at REAL NOT NULL,
    actor TEXT NOT NULL
);
"""

def _source(config, uid):
    # Broker rechecks active DICOM study identity and restricts candidate token
    # to this study. Source is not taken from a browser-supplied path.
    with database(config) as db:
        existing = db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='sql_associations'"
        ).fetchone()
        if not existing:
            raise ValueError("SQL association is required")
        assoc = db.execute(
            """SELECT a.token,a.report_sha256,a.study_fingerprint,
                      a.date_verified,a.conflicts_acknowledged,s.fingerprint,s.state
               FROM sql_associations a JOIN studies s ON a.uid=s.uid
               WHERE a.uid=?""", (uid,)
        ).fetchone()
        if not assoc or not assoc["date_verified"] or not assoc["conflicts_acknowledged"]:
            raise ValueError("Verified SQL association is required")
        if assoc["fingerprint"] != assoc["study_fingerprint"]:
            raise ValueError("Study changed after SQL association")
        if assoc["state"] in ("conflict", "approved", "exported"):
            raise ValueError("Study state does not allow a SQL draft")
        token, expected_hash, fingerprint = assoc["token"], assoc["report_sha256"], assoc["fingerprint"]
        rows = db.execute(
            """SELECT metadata FROM files WHERE kind='dicom' AND active=1 AND status='ok'
               AND json_extract(metadata,'$.StudyInstanceUID')=?""", (uid,)
        ).fetchall()
        if not rows:
            raise ValueError("DICOM catalog is incomplete")
        identifiers = set()
        for row in rows:
            meta = json.loads(row["metadata"])
            identifiers.update(str(meta.get(key) or "") for key in
                ("PatientID", "PatientName", "AccessionNumber",
                 "StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID"))
    original = sql_gui_candidates.report_text(config, uid, token)
    current_hash = hashlib.sha256(original.encode("utf-8")).hexdigest()
    if current_hash != expected_hash:
        raise ValueError("Confirmed SQL source fingerprint changed")
    readable, status = format_report(original)
    if status == "unparsed":
        raise ValueError("SQL narrative cannot safely be formatted for review")
    if len(readable.encode("utf-8")) > config["max_report_bytes"]:
        raise ValueError("Report exceeds configured size limit")
    return readable, current_hash, fingerprint, identifiers


def prepare_draft(config, uid, actor="admin", nlp=None):
    readable, source_hash, fingerprint, identifiers = _source(config, uid)
    # Presidio failures fail closed; never silently skip name/date filtering.
    cleaned = engine.sanitize_report(readable, identifiers, nlp)
    if not cleaned.strip():
        raise ValueError("Sanitized SQL draft is empty")
    with database(config) as db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        idle(db)
        ready = db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0] != "1":
            raise ValueError("Successful scan is required")
        study = db.execute("SELECT fingerprint,state FROM studies WHERE uid=?", (uid,)).fetchone()
        assoc = db.execute(
            "SELECT report_sha256,study_fingerprint FROM sql_associations WHERE uid=?", (uid,)
        ).fetchone()
        if not study or study["state"] in ("conflict","approved","exported") or not assoc or (
            study["fingerprint"] != fingerprint or assoc["study_fingerprint"] != fingerprint
            or assoc["report_sha256"] != source_hash
        ):
            raise ValueError("SQL study or association changed during preparation")
        db.execute(
            """INSERT INTO sql_review_drafts
               (uid,source_sha256,source_study_fingerprint,draft,prepared_at,actor)
               VALUES (?,?,?,?,?,?)
               ON CONFLICT(uid) DO UPDATE SET
               source_sha256=excluded.source_sha256,
               source_study_fingerprint=excluded.source_study_fingerprint,
               draft=excluded.draft,prepared_at=excluded.prepared_at,actor=excluded.actor""",
            (uid,source_hash,fingerprint,cleaned,time.time(),actor)
        )
        audit(db,actor,"sql-draft-prepared",hashlib.sha256(uid.encode()).hexdigest()[:20])
    return {"prepared":True,"draft":cleaned,"manual_review_required":True,
            "study_approved":False,"export_authorized":False}


def read_draft(config, uid):
    with database(config) as db:
        exists = db.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_review_drafts'"
        ).fetchone()
        if not exists:
            return {"prepared":False}
        row = db.execute(
            """SELECT d.draft,d.source_sha256,d.source_study_fingerprint,
                      a.report_sha256,a.study_fingerprint,s.fingerprint
               FROM sql_review_drafts d
               JOIN sql_associations a ON a.uid=d.uid
               JOIN studies s ON s.uid=d.uid WHERE d.uid=?""", (uid,)
        ).fetchone()
    if not row or row["source_sha256"]!=row["report_sha256"] or (
        row["source_study_fingerprint"]!=row["fingerprint"]
        or row["study_fingerprint"]!=row["fingerprint"]
    ):
        return {"prepared":False}
    # Reauthorization and original fingerprint check on read.
    _readable, current_hash, _fp, _ids = _source(config,uid)
    if row["source_sha256"]!=current_hash:
        return {"prepared":False}
    return {"prepared":True,"draft":row["draft"],"manual_review_required":True,
            "study_approved":False,"export_authorized":False}
