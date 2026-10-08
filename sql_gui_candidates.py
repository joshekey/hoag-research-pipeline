"""Read-only SQL narrative candidates for an authenticated hospital-only review panel.

Not linked to existing TXT prepare/approval/export pipelines. Never auto-associates
a narrative with a DICOM study. No patient values appear in response metadata.
"""
import hashlib
import hmac
import json
import re
import sqlite3
from pathlib import Path

from strict_identity_match import evaluate, extract_header, normalize_date, normalize_name
from sql_dicom_pilot import regions, modalities, text


def index_path(config):
    path = Path(config["state_dir"]) / "sql-private" / "reports-v2.sqlite"
    if path.is_symlink() or not path.is_file():
        raise ValueError("Restricted SQL report index unavailable")
    if path.stat().st_mode & 0o077:
        raise ValueError("SQL report index permissions are not restricted")
    return path


def key_for(config, report_id):
    return hmac.new(config["secret_key"].encode("utf-8"), b"sql-candidate:" + report_id,
                    hashlib.sha256).hexdigest()[:32]


def candidates(config, uid):
    catalog = Path(config["state_dir"]) / "workflow.sqlite"
    with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        ready = db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0] != "1":
            raise ValueError("A successful catalog scan is required")
        study = db.execute("SELECT * FROM studies WHERE uid=?", (uid,)).fetchone()
        if study is None:
            raise LookupError("Study not in catalog")
        instances = db.execute("""SELECT metadata FROM files WHERE kind='dicom' AND active=1
                       AND status='ok' AND json_extract(metadata,'$.StudyInstanceUID')=?""",
                       (uid,)).fetchall()
    if not instances:
        raise ValueError("No active study instances")
    # Cross-instance identity consistency is mandatory.
    metas = [json.loads(row[0]) for row in instances]
    identities = {(m.get("PatientName", ""), m.get("PatientBirthDate", ""),
                   m.get("PatientID", "")) for m in metas}
    if len(identities) != 1:
        raise ValueError("DICOM patient metadata conflict")
    # PatientBirthDate is indexed in the enhanced build's DICOM metadata.
    name = metas[0].get("PatientName", "") or study["name"]
    dob = metas[0].get("PatientBirthDate", "")
    if not normalize_name(name) or not normalize_date(dob):
        raise ValueError("Patient name or DOB missing from DICOM catalog")
    study_date = normalize_date(study["date"])
    study_mods = {m.strip() for m in text(study["modality"]).upper().split(",") if m.strip()}
    # Study description not guaranteed in stored catalog; the explicit
    # procedure consistency signal is unknown until separate review.
    found = []
    with sqlite3.connect(index_path(config).as_uri() + "?mode=ro", uri=True) as db:
        for report_id, narrative, procedure, result_date in db.execute(
                "SELECT report_id,text,procedure_name,result_date FROM narratives"):
            header = extract_header(narrative)
            if not header["name"] or not header["dob"]:
                continue
            if header["name"] != normalize_name(name) or header["dob"] != normalize_date(dob):
                continue
            checks = evaluate(name, dob, study_date, narrative, result_date=result_date)
            sql_mods = modalities(procedure)
            if study_mods and sql_mods and not (study_mods & sql_mods):
                mod_status = "conflict"
            elif study_mods and sql_mods:
                mod_status = "compatible"
            else:
                mod_status = "unknown"
            found.append({
                "token": key_for(config, report_id),
                "name_dob": True,
                "exam_date_verified": bool(checks["checks"]["exam_date"]),
                "result_date_matches": bool(checks["result_date_only"]),
                "modality": mod_status,
                "anatomy": "requires image/procedure comparison",
                "review_required": True,
            })
    found.sort(key=lambda r: (not r["exam_date_verified"], not r["result_date_matches"],
                              r["modality"] != "compatible", r["token"]))
    return found[:30], len(found)


def report_text(config, uid, token):
    # Tokens can only resolve for exact-DOB/name candidates of this study.
    if not re.fullmatch(r"[a-f0-9]{32}", token):
        raise LookupError("Candidate unavailable")
    rows, _ = candidates(config, uid)
    if not any(row["token"] == token for row in rows):
        raise LookupError("Candidate unavailable")
    with sqlite3.connect(index_path(config).as_uri() + "?mode=ro", uri=True) as db:
        for report_id, narrative in db.execute("SELECT report_id,text FROM narratives"):
            if hmac.compare_digest(key_for(config, report_id), token):
                value = narrative.decode("utf-8", errors="replace") if isinstance(narrative, bytes) else str(narrative)
                return value
    raise LookupError("Candidate unavailable")
