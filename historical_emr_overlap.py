"""Read-only overlap audit: historical DICOM index vs previously indexed EMR TXT.

Do not read inactive DICOM source files or modify their active status.
The old DICOM metadata is historical and may no longer describe current source
files; overlap evidence is for population discovery, NOT study/report linkage.
CLI prints AGGREGATES ONLY, never identifiers, paths, dates or report text.
"""
import argparse
import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import engine
from emr_txt_leads import exam_dates, exam_terms, dicom_surname
from strict_identity_match import normalize_date, normalize_name
from txt_candidate_discovery import clues, MODALITIES


def name_tokens(value):
    return tuple(re.findall(r"[A-Z]+", normalize_name(value)))


def build_historical_index(rows):
    """Group indexed inactive instances by study UID, retaining no paths."""
    studies={}
    counters=Counter()
    for metadata_text in rows:
        counters["historical_dicom_rows"]+=1
        try:
            meta=json.loads(metadata_text)
        except (TypeError, ValueError):
            counters["invalid_historical_metadata_rows"]+=1
            continue
        uid=str(meta.get("StudyInstanceUID") or "")
        if not uid:
            counters["missing_uid_rows"]+=1
            continue
        entry=studies.setdefault(uid,{"names":set(),"surnames":set(),"dates":set(),
                                      "modalities":set(),"instances":0})
        entry["instances"]+=1
        name=normalize_name(meta.get("PatientName"))
        if name:
            entry["names"].add(name)
        surname=dicom_surname(meta.get("PatientName"))
        if surname:
            entry["surnames"].add(surname)
        date=normalize_date(meta.get("StudyDate"))
        if date:
            entry["dates"].add(date)
        mod=str(meta.get("Modality") or "").upper().strip()
        if mod:
            entry["modalities"].add(mod)
    counters["historical_studies"]=len(studies)
    counters["historical_studies_with_name"]=sum(bool(v["names"]) for v in studies.values())
    counters["historical_studies_with_date"]=sum(bool(v["dates"]) for v in studies.values())
    counters["historical_studies_with_modality"]=sum(bool(v["modalities"]) for v in studies.values())
    counters["historical_studies_conflicting_patient_names"]=sum(len(v["names"])>1 for v in studies.values())
    return studies,counters


def audit(config):
    catalog=Path(config["state_dir"])/"workflow.sqlite"
    with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        ready=db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0]!="1":
            raise ValueError("A complete current catalog scan is required")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Wait for active catalog jobs")
        # Only inventory metadata, never open inactive DICOM source paths.
        dicom_rows=[r[0] for r in db.execute("""SELECT metadata FROM files
            WHERE kind='dicom' AND active=0 AND status='ok'""")]
        reports=[dict(r) for r in db.execute("""SELECT * FROM files
            WHERE kind='report' AND active=0 AND status='ok' ORDER BY id""")]
    studies,counts=build_historical_index(dicom_rows)
    counts["historical_reports_considered"]=len(reports)
    # Use small inverted indexes instead of comparing each TXT with all 80k files.
    by_name=defaultdict(set)
    by_surname=defaultdict(set)
    for uid,study in studies.items():
        for name in study["names"]:
            by_name[name].add(uid)
        for surname in study["surnames"]:
            if len(surname)==1 and len(surname[0])>=3:
                by_surname[surname[0]].add(uid)
    matched_exact_studies=set()
    matched_surname_studies=set()
    exact_date_studies=set()
    exact_modality_studies=set()
    exact_date_modality_studies=set()
    matched_exact_reports=set()
    matched_surname_reports=set()
    for report in reports:
        try:
            text,actual_sha=engine.read_report(config,report)
            if actual_sha != json.loads(report["metadata"]).get("hash"):
                counts["changed_report_hashes"]+=1
                continue
        except (OSError,ValueError,UnicodeError,KeyError,TypeError):
            counts["unavailable_reports"]+=1
            continue
        counts["verified_historical_reports"]+=1
        parsed=clues(text,Path(report["path"]).stem)
        name=parsed["name"]
        if not name:
            continue
        counts["reports_with_extracted_name"]+=1
        exact=by_name.get(name,set())
        tokens=set(name_tokens(name))
        surname_matches=set()
        for word in tokens:
            surname_matches.update(by_surname.get(word,set()))
        if exact:
            matched_exact_reports.add(report["id"])
            matched_exact_studies.update(exact)
            counts["exact_name_study_report_pairs"]+=len(exact)
        if surname_matches:
            matched_surname_reports.add(report["id"])
            matched_surname_studies.update(surname_matches)
            counts["surname_only_or_exact_study_report_pairs"]+=len(surname_matches)
        if not exact:
            continue
        # Dates only qualify when explicitly tagged as exam / service dates.
        dates=exam_dates(text)
        mods,_=exam_terms(text)
        # For coverage audit only, an absent exam-heading modality may be
        # supplemented by a general mention, but never confirms a match.
        if not mods:
            mods=parsed["modalities"]
        for uid in exact:
            study=studies[uid]
            date_agreement=bool(dates & study["dates"])
            modality_agreement=bool(mods & study["modalities"])
            if date_agreement:
                counts["exact_name_and_exact_dos_pairs"]+=1
                exact_date_studies.add(uid)
            if modality_agreement:
                counts["exact_name_and_modality_pairs"]+=1
                exact_modality_studies.add(uid)
            if date_agreement and modality_agreement:
                counts["exact_name_dos_modality_pairs"]+=1
                exact_date_modality_studies.add(uid)
    counts["historical_studies_with_exact_name_overlap"]=len(matched_exact_studies)
    counts["historical_studies_with_surname_overlap"]=len(matched_surname_studies)
    counts["historical_studies_with_exact_name_and_dos"]=len(exact_date_studies)
    counts["historical_studies_with_exact_name_and_modality"]=len(exact_modality_studies)
    counts["historical_studies_with_name_dos_modality"]=len(exact_date_modality_studies)
    counts["historical_reports_with_exact_name_overlap"]=len(matched_exact_reports)
    counts["historical_reports_with_surname_overlap"]=len(matched_surname_reports)
    return dict(counts)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default="/etc/hoag-research/config.json")
    args=parser.parse_args()
    stats=audit(json.loads(Path(args.config).read_text()))
    print("=== HISTORICAL DICOM / EMR TXT POPULATION OVERLAP ===")
    print(json.dumps(stats,indent=2,sort_keys=True))
    print("Aggregates only: no names, UIDs, dates, filenames, paths or narratives displayed.")
    print("Read-only. Historical DICOM metadata is not source-validated.")
    print("No active flags, associations, approvals, scan scope or exports changed.")


if __name__=="__main__":
    main()
