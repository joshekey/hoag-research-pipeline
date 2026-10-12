"""Read-only EMR TXT matching: name + exam/DOS, supported by modality and anatomy.

No workflow writes, automatic associations or exports. Identifiers and report
content remain on the hospital server; CLI reports aggregate counts only.
EMR MRNs and accessions may belong to separate identifier namespaces.
"""
import argparse
import json
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path

import pydicom

import engine
from strict_identity_match import normalize_name, normalize_date
from txt_candidate_discovery import clues, MODALITIES, ANATOMY, clean_id

# Only explicit exam/performed/procedure/service dates. Do not silently use
# result, collection, admission, discharge, dictation, print or signature dates.
DOS = re.compile(
    r"(?im)^[ \t]*(?:date[ \t]+of[ \t]+service|"
    r"date[ \t]+of[ \t]+(?:exam(?:ination)?|study|procedure)|"
    r"(?:exam(?:ination)?|study|procedure|performed|service)[ \t]+date|"
    r"exam[ \t]+performed(?:[ \t]+on)?|performed[ \t]+on)"
    r"[ \t]*[:=#-][ \t]*"
    r"(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{8})\b"
)
# Some EMRs put the label and date on the same line but with spaces instead
# of punctuation. Require an explicit date-shaped token immediately after it.
DOS_SPACE = re.compile(
    r"(?im)^[ \t]*(?:date[ \t]+of[ \t]+service|procedure[ \t]+date|"
    r"exam(?:ination)?[ \t]+date|study[ \t]+date)"
    r"[ \t]+(\d{4}[-/]\d{1,2}[-/]\d{1,2}|\d{1,2}[-/]\d{1,2}[-/]\d{2,4}|\d{8})\b"
)
# Only procedure-heading context for modality/anatomy matching; clinical
# history or comparison paragraphs may mention unrelated exams.
HEADING = re.compile(
    r"(?im)^[ \t]*(?:results[ \t]+)?(?:(?:MRI?|CT|XRAY|X-RAY|"
    r"ULTRASOUND|US|MAMMOGRAM|PET|NUCLEAR[ \t]+MEDICINE)"
    r"\b[^\r\n]{0,150}|(?:EXAM|EXAMINATION|PROCEDURE|STUDY)[ \t]*:"
    r"[^\r\n]{0,150})"
)

def exam_dates(text):
    """Explicit exam/service dates only; preserve possible ambiguity."""
    scope=text[:24000]
    values=set()
    for regex in (DOS, DOS_SPACE):
        for match in regex.finditer(scope):
            date=normalize_date(match.group(1))
            if date:
                values.add(date)
    return values

def exam_terms(text):
    headings="\n".join(HEADING.findall(text[:24000]))
    return (
        {name for name,pattern in MODALITIES if re.search(pattern,headings,re.I)},
        {name for name,pattern in ANATOMY if re.search(pattern,headings,re.I)}
    )

def day_difference(a,b):
    try:
        return abs((datetime.strptime(a,"%Y%m%d")-
                    datetime.strptime(b,"%Y%m%d")).days)
    except (TypeError,ValueError):
        return None

def evaluate_emr(study, report, dos_dates, report_modalities,
                 report_anatomy, dicom_modalities, dicom_anatomy):
    """Exploratory leads only. Name is required; date is primary evidence."""
    name=normalize_name(study.get("name"))
    if not name or not report["name"] or name!=report["name"]:
        return None
    date=normalize_date(study.get("date"))
    differences=[day_difference(date,d) for d in dos_dates] if date else []
    differences=[d for d in differences if d is not None]
    nearest=min(differences) if differences else None
    modal=bool(dicom_modalities & report_modalities)
    anatomy=bool(dicom_anatomy & report_anatomy)
    conflict_date=nearest is not None and nearest>1
    # Same patient with an unrelated exam date is not a matching lead.
    if conflict_date:
        return None
    # If a report lacks a trustworthy DOS, matching both modality and anatomy
    # is required to expose a lower-confidence lead for manual triage.
    if nearest is None and not (modal and anatomy):
        return None
    # If DOS is known, also require a matching modality or anatomy,
    # otherwise two examinations on the same day could be confused.
    if nearest is not None and not (modal or anatomy):
        return None
    evidence=["exact_patient_name"]
    if nearest==0:
        evidence.append("same_exam_or_service_date")
    elif nearest==1:
        evidence.append("adjacent_day_exam_or_service_date")
    else:
        evidence.append("no_explicit_exam_or_service_date")
    if modal:
        evidence.append("modality")
    if anatomy:
        evidence.append("anatomy")
    if clean_id(study.get("patient")) and report["patient_id"]:
        evidence.append("mrn_same" if clean_id(study["patient"])==report["patient_id"]
                        else "mrn_different_namespace_unverified")
    if clean_id(study.get("accession")) and report["accession"]:
        evidence.append("accession_same" if clean_id(study["accession"])==report["accession"]
                        else "accession_different_namespace_unverified")
    # When both independent dates and anatomy are present, prioritize them.
    score=40+(35 if nearest==0 else 18 if nearest==1 else 0)+(15 if modal else 0)+(20 if anatomy else 0)
    return {"score":score,
            "category":("date_supported_lead" if nearest is not None else "anatomy_only_lead"),
            "evidence":evidence,
            "requires_manual_review":True,
            "cannot_approve_without_identity_and_exam_verification":True}

def scan(config):
    catalog=Path(config["state_dir"])/"workflow.sqlite"
    with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        ready=db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0]!="1":
            raise ValueError("Completed catalog scan required")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Processing is active")
        has_sql=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_associations'").fetchone())
        exclusion=("AND NOT EXISTS (SELECT 1 FROM sql_associations a WHERE a.uid=s.uid AND a.study_fingerprint=s.fingerprint)"
                   if has_sql else "")
        studies=[dict(row) for row in db.execute(
            """SELECT s.uid,s.name,s.patient,s.accession,s.date,s.modality,s.fingerprint
            FROM studies s WHERE s.state NOT IN ('conflict','approved','exported','review')
            AND s.report_id IS NULL """+exclusion+" ORDER BY s.uid")]
        rows={}
        for study in studies:
            rows[study["uid"]]=[dict(r) for r in db.execute(
                """SELECT * FROM files WHERE kind='dicom' AND active=1 AND status='ok'
                AND json_extract(metadata,'$.StudyInstanceUID')=?
                ORDER BY id LIMIT 8""",(study["uid"],))]
        reports=[dict(r) for r in db.execute(
            "SELECT * FROM files WHERE kind='report' AND status='ok' ORDER BY id")]
    summary=Counter(studies_examined=len(studies),reports_considered=len(reports))
    study_clues={}
    for s in studies:
        modalities={m.strip().upper() for m in (s["modality"] or "").split(",") if m.strip()}
        anatomy=set()
        try:
            for row in rows[s["uid"]]:
                path=engine.source_path(config,row)
                ds=pydicom.dcmread(path,stop_before_pixels=True,
                    specific_tags=["StudyDescription","SeriesDescription","BodyPartExamined","ProtocolName"])
                header=" ".join(str(ds.get(key,"")) for key in
                    ("StudyDescription","SeriesDescription","BodyPartExamined","ProtocolName"))
                modalities.update(k for k,rx in MODALITIES if re.search(rx,header,re.I))
                anatomy.update(k for k,rx in ANATOMY if re.search(rx,header,re.I))
        except (OSError,ValueError,AttributeError,pydicom.errors.InvalidDicomError):
            summary["study_header_unavailable"]+=1
            continue
        study_clues[s["uid"]]=(modalities,anatomy)
        if anatomy:
            summary["studies_with_dicom_anatomy"]+=1
    leads={s["uid"]:[] for s in studies}
    for report in reports:
        try:
            narrative,sha=engine.read_report(config,report)
            if sha!=json.loads(report["metadata"]).get("hash"):
                summary["reports_hash_changed"]+=1
                continue
        except (OSError,ValueError,UnicodeError,KeyError):
            summary["reports_unavailable"]+=1
            continue
        summary["reports_verified"]+=1
        parsed=clues(narrative,Path(report["path"]).stem)
        dates=exam_dates(narrative)
        modalities,anatomy=exam_terms(narrative)
        if dates:
            summary["reports_with_explicit_dos"]+=1
        if parsed["name"]:
            summary["reports_with_extracted_name"]+=1
        for study in studies:
            if study["uid"] not in study_clues:
                continue
            dicom_mod,dicom_anat=study_clues[study["uid"]]
            candidate=evaluate_emr(study,parsed,dates,modalities,anatomy,dicom_mod,dicom_anat)
            if candidate:
                leads[study["uid"]].append(candidate)
    for options in leads.values():
        if options:
            summary["studies_with_exploratory_leads"]+=1
            summary["exploratory_pairs"]+=len(options)
            summary["date_supported_pairs"]+=sum(a["category"]=="date_supported_lead" for a in options)
            summary["anatomy_only_pairs"]+=sum(a["category"]=="anatomy_only_lead" for a in options)
        else:
            summary["studies_without_leads"]+=1
    return dict(summary)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default="/etc/hoag-research/config.json")
    args=parser.parse_args()
    result=scan(json.loads(Path(args.config).read_text()))
    print("=== HOAG EMR NAME / DOS / EXAM LEAD SEARCH ===")
    print(json.dumps(result,sort_keys=True,indent=2))
    print("Read-only. No patient information, report text, study IDs, or source paths displayed.")
    print("No associations, approved studies, review states, or exports changed.")

if __name__=="__main__":
    main()
