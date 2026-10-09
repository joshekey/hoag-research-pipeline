"""Explicit reviewer confirmation of SQL study/report *association only*.

A confirmation never changes a study approval state or enables research export.
A changed study catalog fingerprint invalidates its prior confirmation.
"""
import hashlib
import json
import re
import time
from store import audit, database, idle
import sql_gui_candidates

SCHEMA = """
CREATE TABLE IF NOT EXISTS sql_associations (
  uid TEXT PRIMARY KEY,
  token TEXT NOT NULL,
  report_sha256 TEXT NOT NULL,
  study_fingerprint TEXT NOT NULL,
  actor TEXT NOT NULL,
  confirmed_at REAL NOT NULL,
  note TEXT NOT NULL,
  date_verified INTEGER NOT NULL,
  conflicts_acknowledged INTEGER NOT NULL
);
"""

def confirm(config, uid, token, note, date_verified, conflicts_acknowledged, actor):
    if not re.fullmatch(r"[a-f0-9]{32}", str(token)):
        raise ValueError("Invalid SQL candidate token")
    if not isinstance(note, str) or not (15 <= len(note.strip()) <= 2000):
        raise ValueError("Provide a 15–2000 character clinical association review note")
    if date_verified is not True or conflicts_acknowledged is not True:
        raise ValueError("Confirm independent exam-date verification and review of conflicting or missing fields")
    # The broker independently authorizes UID and identity for both list and preview.
    candidates, _ = sql_gui_candidates.candidates(config, uid)
    candidate = next((c for c in candidates if c["token"] == token), None)
    if candidate is None:
        raise ValueError("SQL candidate no longer eligible for review")
    original = sql_gui_candidates.report_text(config, uid, token)
    digest = hashlib.sha256(original.encode("utf-8")).hexdigest()
    with database(config) as db:
        db.executescript(SCHEMA)
        db.execute("BEGIN IMMEDIATE")
        idle(db)
        ready = db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0] != "1":
            raise ValueError("Successful scan required")
        study = db.execute("SELECT fingerprint,state FROM studies WHERE uid=?", (uid,)).fetchone()
        if not study or study["state"] in ("approved", "exported"):
            raise ValueError("Cannot change a finalized study association")
        db.execute("""INSERT INTO sql_associations
          (uid,token,report_sha256,study_fingerprint,actor,confirmed_at,note,date_verified,conflicts_acknowledged)
          VALUES (?,?,?,?,?,?,?,?,?)
          ON CONFLICT(uid) DO UPDATE SET
          token=excluded.token,report_sha256=excluded.report_sha256,
          study_fingerprint=excluded.study_fingerprint,actor=excluded.actor,
          confirmed_at=excluded.confirmed_at,note=excluded.note,
          date_verified=excluded.date_verified,conflicts_acknowledged=excluded.conflicts_acknowledged""",
          (uid,token,digest,study["fingerprint"],actor,time.time(),note.strip(),1,1))
        audit(db, actor, "sql-association-confirmed", "study:" + hashlib.sha256(uid.encode()).hexdigest()[:20])
    return {"recorded": True, "research_export_authorized": False,
            "report_prepared": False, "requires_deidentification_review": True}
