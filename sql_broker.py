"""Read-only, fixed-index SQL report broker. Never executes caller SQL or accepts paths.

The Unix peer must be the HOAG web service UID. Requests contain only study-
scoped identity keys obtained from a locally verified DICOM header.
"""
import hashlib
import hmac
import json
import os
import pwd
import re
import socket
import sqlite3
import struct
from pathlib import Path

from strict_identity_match import extract_header, normalize_date, normalize_name
from sql_dicom_pilot import modalities, text
from broker_study_auth import authorized_study

SOCKET = "/run/hoag-sql/control.sock"
INDEX = Path("/var/lib/hoag-research/sql-private/reports-v2.sqlite")
MAX_REQUEST = 4096
MAX_NARRATIVE = 2 * 1024 * 1024

def require_index():
    if INDEX.is_symlink() or not INDEX.is_file():
        raise ValueError("SQL report index unavailable")
    if INDEX.parent.stat().st_mode & 0o777 != 0o700 or INDEX.stat().st_mode & 0o777 != 0o600:
        raise ValueError("SQL report index permissions invalid")

def secret():
    config = json.loads(Path("/etc/hoag-research/config.json").read_text())
    return str(config["secret_key"]).encode("utf-8")

def report_token(key, uid, rid):
    return hmac.new(key, b"sql-candidate:" + uid.encode("ascii") + b":" + rid,
                    hashlib.sha256).hexdigest()[:32]

def query(data):
    if not isinstance(data, dict) or data.get("action") not in ("list", "preview"):
        raise ValueError("Invalid request")
    if set(data) - {"action", "uid", "token"}:
        raise ValueError("Unexpected request field")
    uid = data.get("uid", "")
    if not isinstance(uid, str) or not re.fullmatch(r"[0-9.]{1,100}", uid):
        raise ValueError("Invalid study identifier")
    # Never trust client-provided demographics or dates.
    config = json.loads(Path("/etc/hoag-research/config.json").read_text())
    study = authorized_study(uid, config)
    name, dob, date, modal = (study[k] for k in ("name", "dob", "date", "modality"))
    wanted_token = data.get("token", "")
    if data["action"] == "preview" and (not isinstance(wanted_token, str)
                                        or not re.fullmatch(r"[a-f0-9]{32}", wanted_token)):
        raise ValueError("Invalid candidate token")
    require_index()
    matches = []
    key = secret()
    # Index is finalized/read-only. Root-only DB remains owned by root.
    uri = INDEX.as_uri() + "?mode=ro"
    with sqlite3.connect(uri, uri=True) as db:
        for rid, narrative, procedure, result_date in db.execute(
                "SELECT report_id,text,procedure_name,result_date FROM narratives"):
            header = extract_header(narrative)
            if header["name"] != name or header["dob"] != dob:
                continue
            token = report_token(key, uid, rid)
            if data["action"] == "preview":
                if not hmac.compare_digest(token, wanted_token):
                    continue
                if not narrative or len(narrative) > MAX_NARRATIVE:
                    raise ValueError("Narrative size unsupported")
                return {"text": text(narrative), "read_only": True}
            mods = modalities(procedure)
            study_mods = {p.strip().upper() for p in modal.split(",") if p.strip()}
            mod_status = ("compatible" if study_mods and mods and study_mods & mods
                          else "conflict" if study_mods and mods else "unknown")
            matches.append({
                "token": token,
                "name_dob": True,
                "exam_date_verified": bool(header["exam_date"] and header["exam_date"] == date),
                "result_date_matches": bool(not header["exam_date"]
                                             and normalize_date(result_date) == date),
                "modality": mod_status,
                "anatomy": "requires image/procedure comparison",
                "review_required": True,
            })
    if data["action"] == "preview":
        raise LookupError("Candidate unavailable")
    matches.sort(key=lambda row: (not row["exam_date_verified"], not row["result_date_matches"],
                                  row["modality"] != "compatible", row["token"]))
    return {"candidates": matches[:30], "total": len(matches), "read_only": True}

def handle(data):
    try:
        return query(data)
    except (ValueError, LookupError):
        return {"error": "SQL candidate unavailable or invalid"}
    except Exception:
        return {"error": "SQL broker could not complete request"}

def main():
    account = pwd.getpwnam("hoag-indexer")
    os.umask(0o077)
    directory = Path(SOCKET).parent
    directory.mkdir(mode=0o750, parents=True, exist_ok=True)
    os.chown(directory, 0, account.pw_gid)
    if Path(SOCKET).exists():
        Path(SOCKET).unlink()
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as server:
        server.bind(SOCKET)
        os.chown(SOCKET, 0, account.pw_gid)
        os.chmod(SOCKET, 0o660)
        server.listen(8)
        while True:
            conn, _ = server.accept()
            with conn:
                conn.settimeout(10)
                try:
                    _pid, uid, _gid = struct.unpack("3i", conn.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
                    if uid != account.pw_uid:
                        continue
                    raw = bytearray()
                    while len(raw) <= MAX_REQUEST and not raw.endswith(b"\n"):
                        part = conn.recv(1024)
                        if not part:
                            break
                        raw.extend(part)
                    if len(raw) > MAX_REQUEST or not raw.endswith(b"\n"):
                        answer = {"error": "Invalid request size"}
                    else:
                        answer = handle(json.loads(raw))
                    conn.sendall(json.dumps(answer).encode("utf-8") + b"\n")
                except (OSError, ValueError, TypeError):
                    try:
                        conn.sendall(b'{"error":"Invalid SQL broker request"}\n')
                    except OSError:
                        pass

if __name__ == "__main__":
    main()
