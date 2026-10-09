"""Independent, hospital-local study validation for the SQL broker.

Requests supply a UID only; all identity fields come from the active catalog and
a source-validated DICOM header. Never return PHI to the dashboard caller.
"""
import json
import sqlite3
from pathlib import Path

import pydicom

import engine
from strict_identity_match import normalize_date, normalize_name


def authorized_study(uid, config):
    catalog = Path(config["state_dir"]) / "workflow.sqlite"
    with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        ready = db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if ready is None or ready[0] != "1":
            raise ValueError("Incomplete scan")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Active job")
        study = db.execute("SELECT * FROM studies WHERE uid=?", (uid,)).fetchone()
        if study is None:
            raise ValueError("Unknown study")
        files = db.execute("""SELECT * FROM files WHERE kind='dicom' AND active=1
             AND status='ok' AND json_extract(metadata,'$.StudyInstanceUID')=?""", (uid,)).fetchall()
        if not files or len(files) != study["count"]:
            raise ValueError("DICOM catalog incomplete")
        identities = set()
        for file in files:
            meta = json.loads(file["metadata"])
            identities.add((meta.get("PatientName", ""), meta.get("PatientID", ""),
                            meta.get("IssuerOfPatientID", "")))
        if len(identities) != 1:
            raise ValueError("Conflicting DICOM patient metadata")
        first = files[0]
    name = identities.pop()[0]
    if normalize_name(name) != normalize_name(study["name"]):
        raise ValueError("Catalog identity mismatch")
    # source_path enforces configured read-only mounts, containment, and mtime/size.
    path = engine.source_path(config, first)
    ds = pydicom.dcmread(path, stop_before_pixels=True,
                        specific_tags=["PatientName", "PatientBirthDate", "StudyInstanceUID", "StudyDate"])
    if str(ds.get("StudyInstanceUID", "")) != uid or normalize_name(ds.get("PatientName", "")) != normalize_name(name):
        raise ValueError("DICOM header/catalog mismatch")
    study_date = normalize_date(study["date"])
    if not study_date or normalize_date(ds.get("StudyDate", "")) != study_date:
        raise ValueError("DICOM study date missing or inconsistent")
    return {"name": normalize_name(name),
            "dob": normalize_date(ds.get("PatientBirthDate", "")),
            "date": study_date,
            "modality": str(study["modality"] or "")}
