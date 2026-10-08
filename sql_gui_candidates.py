"""Authenticated dashboard-side SQL candidate client.

Validates study membership and local DICOM identity before making a fixed
Unix-socket request. Never opens root-owned SQL files from dashboard process.
"""
import json
import socket
import sqlite3
from pathlib import Path
import pydicom
import engine
from strict_identity_match import normalize_date, normalize_name

SOCKET = "/run/hoag-sql/control.sock"

def request_broker(body):
    payload = (json.dumps(body) + "\n").encode("utf-8")
    if len(payload) > 4096:
        raise ValueError("SQL broker request too large")
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as connection:
            connection.settimeout(15)
            connection.connect(SOCKET)
            connection.sendall(payload)
            response = bytearray()
            while len(response) <= 3 * 1024 * 1024:
                block = connection.recv(32768)
                if not block:
                    break
                response.extend(block)
                if response.endswith(b"\n"):
                    break
        if len(response) > 3 * 1024 * 1024 or not response.endswith(b"\n"):
            raise ValueError("SQL broker response exceeded limit")
        answer = json.loads(response)
    except (OSError, ValueError):
        raise ValueError("SQL report broker unavailable or returned invalid response") from None
    if "error" in answer:
        raise ValueError(answer["error"])
    return answer

def study_request(config, uid):
    catalog = Path(config["state_dir"]) / "workflow.sqlite"
    with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        ready = db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0] != "1":
            raise ValueError("A successful catalog scan is required")
        study = db.execute("SELECT * FROM studies WHERE uid=?", (uid,)).fetchone()
        if study is None:
            raise LookupError("Study not in catalog")
        instances = db.execute("""SELECT * FROM files WHERE kind='dicom' AND active=1
                       AND status='ok' AND json_extract(metadata,'$.StudyInstanceUID')=?""",
                       (uid,)).fetchall()
    if not instances:
        raise ValueError("No active DICOM instances")
    metas = [json.loads(row["metadata"]) for row in instances]
    identities = {(m.get("PatientName", ""), m.get("PatientID", ""),
                   m.get("IssuerOfPatientID", "")) for m in metas}
    if len(identities) != 1:
        raise ValueError("Conflicting indexed DICOM patient identities")
    # Source path validation preserves HOAG's read-only source protections.
    source = engine.source_path(config, instances[0])
    ds = pydicom.dcmread(source, stop_before_pixels=True,
                        specific_tags=["PatientName", "PatientBirthDate"])
    name = str(ds.get("PatientName", ""))
    if normalize_name(name) != normalize_name(study["name"]):
        raise ValueError("DICOM source identity differs from catalog")
    dob = str(ds.get("PatientBirthDate", ""))
    date = normalize_date(study["date"])
    if not normalize_name(name) or not normalize_date(dob) or not date:
        raise ValueError("Study identity or date missing")
    return {"uid": uid, "name": name, "dob": dob, "date": date,
            "modality": str(study["modality"] or "")}

def candidates(config, uid):
    result = request_broker({"action": "list", **study_request(config, uid)})
    return result["candidates"], result["total"]

def report_text(config, uid, token):
    result = request_broker({"action": "preview",
                            **study_request(config, uid), "token": token})
    return result["text"]
