"""Aggregate-only diagnostic for TXT report matching coverage; no catalog mutations.

Only run inside the hospital environment. Never print patient identifiers,
filenames, UIDs, report content, or example values.
"""
import argparse
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

import engine
from txt_candidate_discovery import clues, clean_id
from strict_identity_match import normalize_name, normalize_date


def inspect(config):
    catalog=Path(config["state_dir"])/"workflow.sqlite"
    with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        ready=db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0]!="1":
            raise ValueError("Scan incomplete")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Wait for indexing job to finish")
        sql_table=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_associations'").fetchone())
        extra=("""AND NOT EXISTS (SELECT 1 FROM sql_associations a
                 WHERE a.uid=s.uid AND a.study_fingerprint=s.fingerprint)""" if sql_table else "")
        studies=[dict(r) for r in db.execute("""SELECT s.uid,s.accession,s.patient,
             s.name,s.date,s.modality,s.fingerprint FROM studies s
             WHERE s.state NOT IN ('conflict','approved','exported','review')
             AND s.report_id IS NULL """+extra)]
        reports=[dict(r) for r in db.execute("""SELECT * FROM files
            WHERE kind='report' AND status='ok' AND active=0 ORDER BY id""")]
    counts=Counter()
    counts["studies_examined"]=len(studies)
    counts["inactive_reports_available"]=len(reports)
    candidate_accessions={clean_id(s["accession"]) for s in studies if clean_id(s["accession"])}
    candidate_names={normalize_name(s["name"]) for s in studies if normalize_name(s["name"])}
    candidate_patients={clean_id(s["patient"]) for s in studies if clean_id(s["patient"])}
    candidate_dates={normalize_date(s["date"]) for s in studies if normalize_date(s["date"])}
    raw_accession_studies=set()
    raw_patient_studies=set()
    raw_accession_reports=set()
    raw_patient_reports=set()
    study_tokens=[]
    for i,study in enumerate(studies):
        a=clean_id(study["accession"])
        p=clean_id(study["patient"])
        study_tokens.append((i,a if len(a)>=5 else "",p if len(p)>=5 else ""))
    for row in reports:
        try:
            text,hash_value=engine.read_report(config,row)
            if hash_value != json.loads(row["metadata"]).get("hash"):
                counts["hash_changed"]+=1
                continue
        except (OSError,ValueError,UnicodeError,KeyError):
            counts["unreadable"]+=1
            continue
        counts["verified_reports_read"]+=1
        found=clues(text,Path(row["path"]).stem)
        # Exact full-text token occurrences are diagnostic, NOT match approval.
        body=text.upper()
        filename=Path(row["path"]).stem.upper()
        for i,accession,patient in study_tokens:
            if accession:
                pattern=r"(?<![A-Z0-9])"+re.escape(accession)+r"(?![A-Z0-9])"
                if re.search(pattern,body) or re.search(pattern,filename):
                    raw_accession_studies.add(i)
                    raw_accession_reports.add(row["id"])
            if patient:
                pattern=r"(?<![A-Z0-9])"+re.escape(patient)+r"(?![A-Z0-9])"
                if re.search(pattern,body):
                    raw_patient_studies.add(i)
                    raw_patient_reports.add(row["id"])
        for field in ("accession","patient_id","name","dob","exam_date","study_uid","filename_key"):
            if found[field]:
                counts["reports_with_"+field]+=1
        if found["modalities"]:
            counts["reports_with_modality_clue"]+=1
        if found["anatomy"]:
            counts["reports_with_anatomy_clue"]+=1
        if found["accession"] in candidate_accessions and found["accession"]:
            counts["reports_with_matching_explicit_accession"]+=1
        if found["filename_key"] in candidate_accessions and found["filename_key"]:
            counts["reports_with_matching_filename_accession"]+=1
        if found["patient_id"] in candidate_patients and found["patient_id"]:
            counts["reports_with_matching_patient_id"]+=1
        if found["name"] in candidate_names and found["name"]:
            counts["reports_with_matching_name"]+=1
        if found["exam_date"] in candidate_dates and found["exam_date"]:
            counts["reports_with_matching_exam_date"]+=1
        # Content checks are limited to aggregate presence of common label variants;
        # no report samples or report field values are returned.
        import re
        header=text[:16000]
        patterns={
            "medical_record_labels":r"(?im)^\s*(?:mrn|mr\s*#|medical record(?: number)?|patient id)\b",
            "accession_labels":r"(?im)^\s*(?:accession|acc\s*#?)\b",
            "exam_date_labels":r"(?im)^\s*(?:exam(?:ination)? date|date of (?:exam|study)|study date)\b",
            "service_date_labels":r"(?im)^\s*(?:date of service|performed on|procedure date|exam performed)\b",
            "result_date_labels":r"(?im)^\s*(?:result date|finalized|dictated|signed)\b",
            "patient_name_labels":r"(?im)^\s*(?:patient name|name)\b",
        }
        for key,regex in patterns.items():
            if re.search(regex,header):
                counts["reports_with_"+key]+=1
    counts["studies_with_any_raw_accession_occurrence"]=len(raw_accession_studies)
    counts["studies_with_any_raw_patient_id_occurrence"]=len(raw_patient_studies)
    counts["reports_with_any_raw_accession_occurrence"]=len(raw_accession_reports)
    counts["reports_with_any_raw_patient_id_occurrence"]=len(raw_patient_reports)
    return dict(counts)


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",default="/etc/hoag-research/config.json")
    args=p.parse_args()
    result=inspect(json.loads(Path(args.config).read_text()))
    print("=== HOAG TXT MATCHING COVERAGE: AGGREGATE ONLY ===")
    for key,value in sorted(result.items()):
        print(f"{key}: {value}")
    print("Read-only. No patient identifiers, study UIDs, filenames or reports displayed.")
    print("No changes to index, SQL associations, reviews or exports.")


if __name__=="__main__":
    main()
