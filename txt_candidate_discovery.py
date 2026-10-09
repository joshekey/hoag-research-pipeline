"""Read-only, PHI-safe TXT report candidate discovery for indexed HOAG studies.

No reports or identifiers are printed, persisted, exported, or approved.
Candidates returned in-process require manual review and independent evidence.
"""
import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

import engine
from strict_identity_match import normalize_date, normalize_name

ACCESSION = re.compile(r"(?im)^\s*(?:accession(?:\s*(?:number|no\.?|#))?|acc\s*#?)\s*[:=#]\s*([A-Z0-9._-]{3,64})\b")
PATIENT_ID = re.compile(r"(?im)^\s*(?:patient\s*(?:id|number)|mrn|medical\s*record\s*(?:number|#))\s*[:=#]\s*([A-Z0-9._-]{3,64})\b")
NAME = re.compile(r"(?im)^\s*(?:patient\s*name|name)\s*[:=]\s*([^\r\n]{3,120})")
DOB = re.compile(r"(?im)^\s*(?:date\s*of\s*birth|dob)\s*[:=]\s*([^\r\n]{6,32})")
EXAM_DATE = re.compile(r"(?im)^\s*(?:date\s*of\s*(?:study|exam(?:ination)?)|exam(?:ination)?\s*date|study\s*date)\s*[:=]\s*([^\r\n]{6,32})")
STUDY_UID = re.compile(r"(?im)^\s*study(?:\s*instance)?\s*uid\s*[:=]\s*([0-9]+(?:\.[0-9]+)+)\b")
MODALITIES = (("CT", r"\b(?:CT|COMPUTED TOMOGRAPHY)\b"),
              ("MR", r"\b(?:MRI?|MAGNETIC RESONANCE)\b"),
              ("US", r"\b(?:US|ULTRASOUND|SONOGRAM)\b"),
              ("DX", r"\b(?:XRAY|X-RAY|RADIOGRAPH)\b"),
              ("MG", r"\b(?:MAMMOGRAM|MAMMOGRAPHY)\b"),
              ("NM", r"\b(?:NUCLEAR MEDICINE)\b"),
              ("PT", r"\b(?:PET)\b"))
ANATOMY = (("LUMBAR",r"\bLUMBAR\b"),("CERVICAL",r"\bCERVICAL\b"),
           ("THORACIC",r"\bTHORACIC\b"),("BRAIN",r"\b(?:BRAIN|HEAD)\b"),
           ("CHEST",r"\bCHEST\b"),("ABDOMEN",r"\bABDOMEN\b"),
           ("PELVIS",r"\bPELVIS\b"),("KNEE",r"\bKNEE\b"),
           ("SHOULDER",r"\bSHOULDER\b"))

def capture(pattern, text):
    m = pattern.search(text[:20000])
    return m.group(1).strip() if m else ""

def clean_id(value):
    return re.sub(r"[^A-Z0-9]", "", str(value or "").upper())

def clues(text, filename_stem=""):
    header = text[:20000]
    return {
        "accession": clean_id(capture(ACCESSION,header)),
        "patient_id": clean_id(capture(PATIENT_ID,header)),
        "name": normalize_name(capture(NAME,header)),
        "dob": normalize_date(capture(DOB,header)),
        "exam_date": normalize_date(capture(EXAM_DATE,header)),
        "study_uid": capture(STUDY_UID,header),
        "filename_key": clean_id(filename_stem),
        "modalities": {m for m,rx in MODALITIES if re.search(rx,header,re.I)},
        "anatomy": {a for a,rx in ANATOMY if re.search(rx,header,re.I)},
    }

def evaluate(study, report, *, study_dob="", study_anatomy=()):
    """Evidence score is for ranking ONLY; never a confirmation."""
    evidence = []
    conflicts = []
    score = 0
    def compare(label, a, b, weight):
        nonlocal score
        if a and b:
            if a == b:
                score += weight
                evidence.append(label)
            else:
                conflicts.append(label)
    accession = clean_id(study.get("accession"))
    patient = clean_id(study.get("patient"))
    compare("study_uid", study.get("uid"), report["study_uid"], 100)
    compare("accession", accession, report["accession"], 90)
    if accession and report["filename_key"] == accession:
        score += 60
        evidence.append("exact_filename_accession")
    compare("patient_id", patient, report["patient_id"], 35)
    compare("name", normalize_name(study.get("name")), report["name"], 25)
    compare("dob", normalize_date(study_dob), report["dob"], 20)
    compare("exam_date", normalize_date(study.get("date")), report["exam_date"], 30)
    study_mods = {m.strip().upper() for m in str(study.get("modality") or "").split(",") if m.strip()}
    if study_mods and report["modalities"] and study_mods.intersection(report["modalities"]):
        evidence.append("modality")
        score += 5
    if study_anatomy and set(study_anatomy).intersection(report["anatomy"]):
        evidence.append("anatomy")
        score += 5
    # Mismatched accession, UID or patient is a hard conflict, not a weak lead.
    hard = bool(set(conflicts).intersection({"study_uid","accession","patient_id","name","dob"}))
    anchor = bool(set(evidence).intersection({"study_uid","accession","exact_filename_accession"}))
    identity = bool(set(evidence).intersection({"patient_id","name","dob"}))
    date = "exam_date" in evidence
    eligible = not hard and (anchor or (identity and date))
    confidence = ("strong" if eligible and score >= 80 else
                  "review" if eligible else "insufficient")
    return {"score":score,"confidence":confidence,
            "evidence":evidence,"conflicts":conflicts,
            "requires_manual_confirmation":True}

def discover(config, limit_per_study=10):
    """Return in-memory candidate summaries; no database writes."""
    if not 1 <= limit_per_study <= 100:
        raise ValueError("Invalid candidate limit")
    catalog=Path(config["state_dir"])/"workflow.sqlite"
    with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        ready=db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0]!="1":
            raise ValueError("A completed catalog scan is required")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Indexing or processing in progress")
        has_sql = bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_associations'").fetchone())
        exclusion = ("""AND NOT EXISTS (SELECT 1 FROM sql_associations a
                 WHERE a.uid=s.uid AND a.study_fingerprint=s.fingerprint)""" if has_sql else "")
        studies=[dict(r) for r in db.execute("""SELECT s.uid,s.accession,s.patient,s.name,s.date,s.modality,
               s.state,s.report_id,s.fingerprint FROM studies s
               WHERE s.state NOT IN ('conflict','approved','exported','review')
               AND s.report_id IS NULL
               """ + exclusion + """ ORDER BY s.uid""")]
        reports=[dict(r) for r in db.execute("""SELECT * FROM files
             WHERE kind='report' AND active=1 AND status='ok' ORDER BY id""")]
    result={s["uid"]:[] for s in studies}
    totals=Counter()
    totals["studies_examined"]=len(studies)
    totals["reports_indexed"]=len(reports)
    # For each report read once, validate the original hash and source inventory.
    for report in reports:
        try:
            text, sha=engine.read_report(config, report)
            if sha != json.loads(report["metadata"]).get("hash"):
                totals["reports_changed"]+=1
                continue
        except (OSError,ValueError,UnicodeError,KeyError):
            totals["reports_unreadable"]+=1
            continue
        totals["reports_scanned"]+=1
        clue=clues(text,Path(report["path"]).stem)
        for study in studies:
            assessment=evaluate(study,clue)
            if assessment["confidence"]=="insufficient":
                if assessment["conflicts"]:
                    totals["conflicting_pairs"]+=1
                continue
            result[study["uid"]].append({"report_id":report["id"],
                    "report_sha256":sha,**assessment})
    for uid, matches in result.items():
        matches.sort(key=lambda r:(-r["score"],r["report_id"]))
        if matches:
            totals["studies_with_candidates"]+=1
            totals["candidate_pairs"]+=len(matches)
            if len(matches)>1 and matches[0]["score"]==matches[1]["score"]:
                totals["ambiguous_top_score"]+=1
        else:
            totals["studies_without_candidates"]+=1
        result[uid]=matches[:limit_per_study]
    return {"summary":dict(totals),"candidates":result}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default="/etc/hoag-research/config.json")
    args=parser.parse_args()
    cfg=json.loads(Path(args.config).read_text())
    outcome=discover(cfg)
    print("=== READ-ONLY TXT REPORT DISCOVERY ===")
    print(json.dumps(outcome["summary"],indent=2,sort_keys=True))
    print("No patient identifiers, study UIDs, filenames, report text, or candidate mappings printed.")
    print("No associations, research approvals, or exports changed.")

if __name__=="__main__":
    main()
