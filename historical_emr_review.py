"""Authenticated historical EMR leads: source-bound, review-only browser data.

Clinical report text and DICOM patient demographics MUST only be returned by
existing authenticated Flask routes. No exports, approval, association or
catalog mutation. Feature disabled unless root-controlled gate is installed.
"""
import hmac
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

import engine
from emr_txt_leads import exam_dates, exam_terms
from historical_emr_overlap import build_historical_index
from txt_candidate_discovery import clues, all_patient_names
from strict_identity_match import normalize_name
from store import token

GATE = Path("/etc/hoag-research/historical-emr-review.enabled")


def permitted():
    """Fail closed when the historical review gate is absent or unreadable."""
    try:
        if GATE.is_symlink() or not GATE.is_file() or GATE.stat().st_size > 32:
            return False
        return GATE.read_text().strip() == "AUTHORIZED"
    except OSError:
        # Includes PermissionError when unprivileged synthetic tests cannot
        # traverse the protected /etc/hoag-research directory.
        return False


def checked_report_identity(report_text, expected_dicom_name):
    """Require all recognized EMR names to agree with the indexed DICOM name."""
    names = all_patient_names(report_text)
    expected = normalize_name(expected_dicom_name)
    if not expected or not names:
        return 'unverified'
    if len(names) != 1 or expected not in names:
        return 'conflict'
    return 'consistent'

def eligible_pairs(config):
    """Re-evaluate uniquely matched pairs from indexed source metadata."""
    if not permitted():
        raise PermissionError("Historical review not enabled")
    catalog=Path(config["state_dir"])/"workflow.sqlite"
    with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        ready=db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0]!="1":
            raise ValueError("Index not ready")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Index busy")
        dicoms=[dict(r) for r in db.execute(
            "SELECT * FROM files WHERE kind='dicom' AND active=0 AND status='ok'")]
        reports=[dict(r) for r in db.execute(
            "SELECT * FROM files WHERE kind='report' AND active=0 AND status='ok'")]
    history,_=build_historical_index([row["metadata"] for row in dicoms])
    by_name=defaultdict(set)
    for uid,details in history.items():
        if len(details["names"])==1 and len(details["dates"])==1:
            by_name[next(iter(details["names"]))].add(uid)
    matches=defaultdict(list)
    report_by_id={}
    for row in reports:
        try:
            narrative,sha=engine.read_report(config,row)
            if sha!=json.loads(row["metadata"]).get("hash"):
                continue
        except (OSError,ValueError,UnicodeError,KeyError,TypeError):
            continue
        extracted=clues(narrative,Path(row["path"]).stem)
        dates=exam_dates(narrative)
        modes,_=exam_terms(narrative)
        if not modes:
            modes=extracted["modalities"]
        if extracted.get('name_conflict') or not extracted['name']:
            continue
        for uid in by_name.get(extracted['name'],()):
            if checked_report_identity(narrative, next(iter(history[uid]['names']))) != 'consistent':
                continue
            if dates & history[uid]['dates'] and modes & history[uid]['modalities']:
                matches[uid].append(row["id"])
                report_by_id[row["id"]]=row
    # Fail closed if the strong-lead population expanded unexpectedly.
    if len(matches)>20:
        raise ValueError("Historical candidate scope changed")
    # Exclude documents nominated for more than one historical examination.
    reuse_counts=defaultdict(int)
    for report_ids in matches.values():
        for report_id in set(report_ids):
            reuse_counts[report_id]+=1
    verified=[]
    for uid,ids in sorted(matches.items()):
        if len(ids)!=1 or reuse_counts[ids[0]]!=1:
            continue
        rows=[row for row in dicoms
              if json.loads(row["metadata"]).get("StudyInstanceUID")==uid]
        if not rows:
            continue
        try:
            for row in rows:
                engine.source_path(config,row)
        except (OSError,ValueError,TypeError):
            continue
        study=history[uid]
        identifier=token(config,"historical-emr-review",uid+"\0"+str(ids[0]))[:32]
        verified.append({
            "token":identifier,
            "uid":uid,
            "report":report_by_id[ids[0]],
            "study":study,
            "instances":len(rows)
        })
    return verified


def listing(config):
    leads=eligible_pairs(config)
    return {"count":len(leads),"review_only":True,"candidates":[
        {"token":lead["token"],"label":"Historical study "+lead["token"][:8].upper(),
         "instance_count":lead["instances"],
         "evidence":["exact patient name","exam / service date","modality"],
         "status":"Unconfirmed; not indexed in active review catalog"}
        for i,lead in enumerate(leads)]}


def preview(config, selected_token):
    if not isinstance(selected_token,str) or len(selected_token)!=32:
        raise LookupError("Candidate not found")
    lead=next((item for item in eligible_pairs(config)
               if hmac.compare_digest(item["token"],selected_token)),None)
    if lead is None:
        raise LookupError("Candidate not found")
    report=lead["report"]
    narrative,sha=engine.read_report(config,report)
    if sha!=json.loads(report["metadata"]).get("hash"):
        raise ValueError("Report changed")
    study=lead["study"]
    if checked_report_identity(narrative, next(iter(study["names"]))) != "consistent":
        raise LookupError("Patient identity conflict")
    return {
        "report_text":narrative,
        "dicom_patient_name":next(iter(study["names"])),
        "dicom_study_date":next(iter(study["dates"])),
        "dicom_modality":", ".join(sorted(study["modalities"])),
        "dicom_instances":lead["instances"],
        "evidence":["exact patient name","exam / service date","modality"],
        "patient_identity_status":"parsed_names_consistent_manual_review_required",
        "review_only":True,
        "ohif_available":True,
        "viewer_url":"/viewer?StudyInstanceUIDs="+lead["uid"],
        "message":"Historical DICOM source is available for read-only OHIF review; no association or export approval."
    }
