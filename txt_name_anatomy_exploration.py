"""Read-only exploratory TXT search: patient name + modality + exam anatomy.

Ranks ONLY cases having a validated name match plus modality/anatomy.
This intentionally does not create candidates in the normal HOAG matching
tables or authorize report associations, de-identification, or exports.
No clinical values, paths, UIDs or report text are printed.
"""
import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

import pydicom

import engine
from txt_candidate_discovery import clues, MODALITIES, ANATOMY, clean_id
from strict_identity_match import normalize_name


def clinical_terms(header):
    import re
    return ({key for key, rx in MODALITIES if re.search(rx, header, re.I)},
            {key for key, rx in ANATOMY if re.search(rx, header, re.I)})


def evaluate_exploratory(study, report, dicom_modalities, dicom_anatomy):
    """Returns evidence-only lead; name matching is mandatory, no auto approval."""
    name = normalize_name(study["name"])
    if not name or not report["name"] or name != report["name"]:
        return None
    # An explicit mismatched identifier is a hard conflict.
    conflicts = []
    if study["uid"] and report["study_uid"] and study["uid"] != report["study_uid"]:
        conflicts.append("study_uid")
    if (clean_id(study["accession"]) and report["accession"] and
            clean_id(study["accession"]) != report["accession"]):
        conflicts.append("accession")
    if (clean_id(study["patient"]) and report["patient_id"] and
            clean_id(study["patient"]) != report["patient_id"]):
        conflicts.append("patient_id")
    if conflicts:
        return {"category":"conflict", "score":0, "evidence":["name"],
                "conflicts":conflicts, "manual_review_only":True}

    evidence=["name"]
    if dicom_modalities & report["modalities"]:
        evidence.append("modality")
    if dicom_anatomy & report["anatomy"]:
        evidence.append("anatomy")
    if clean_id(study["accession"]) and clean_id(study["accession"]) == report["accession"]:
        evidence.append("accession")
    if study["uid"] == report["study_uid"]:
        evidence.append("study_uid")
    if "modality" not in evidence and "anatomy" not in evidence:
        return None
    score = (40 + (20 if "modality" in evidence else 0)
             + (25 if "anatomy" in evidence else 0)
             + (20 if "accession" in evidence else 0)
             + (20 if "study_uid" in evidence else 0))
    return {"category":"exploratory", "score":score, "evidence":evidence,
            "conflicts":[], "manual_review_only":True}


def inspect(config):
    dbfile=Path(config["state_dir"])/"workflow.sqlite"
    with sqlite3.connect(dbfile.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        ready=db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0]!="1":
            raise ValueError("A complete catalog scan is required")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Wait for current processing to complete")
        has_sql=bool(db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='sql_associations'").fetchone())
        exclusion=("" if not has_sql else
                   "AND NOT EXISTS (SELECT 1 FROM sql_associations a WHERE a.uid=s.uid AND a.study_fingerprint=s.fingerprint)")
        studies=[dict(r) for r in db.execute("""SELECT s.uid,s.accession,s.patient,s.name,s.date,
          s.modality,s.fingerprint FROM studies s
          WHERE s.state NOT IN ('conflict','approved','exported','review')
          AND s.report_id IS NULL """+exclusion+" ORDER BY s.uid")]
        reports=[dict(r) for r in db.execute("""SELECT * FROM files
          WHERE kind='report' AND active=0 AND status='ok' ORDER BY id""")]
        rows={s["uid"]:db.execute("""SELECT * FROM files WHERE kind='dicom' AND active=1
          AND status='ok' AND json_extract(metadata,'$.StudyInstanceUID')=? ORDER BY id LIMIT 8""",
          (s["uid"],)).fetchall() for s in studies}
    summary=Counter(studies_examined=len(studies), historical_reports_considered=len(reports))
    searchable=[]
    for study in studies:
        modalities={m.strip().upper() for m in str(study["modality"] or "").split(",") if m.strip()}
        anatomy=set()
        try:
            for row in rows[study["uid"]]:
                source=engine.source_path(config,row)
                dataset=pydicom.dcmread(source,stop_before_pixels=True,
                  specific_tags=["StudyDescription","SeriesDescription","BodyPartExamined","ProtocolName"])
                text=" ".join(str(dataset.get(tag,"")) for tag in
                      ("StudyDescription","SeriesDescription","BodyPartExamined","ProtocolName"))
                header_mods,header_anatomy=clinical_terms(text)
                modalities.update(header_mods)
                anatomy.update(header_anatomy)
        except (OSError,ValueError,AttributeError,pydicom.errors.InvalidDicomError):
            summary["studies_source_header_unavailable"]+=1
            continue
        if anatomy:
            summary["studies_with_dicom_anatomy_clues"]+=1
        if modalities:
            summary["studies_with_modality_clues"]+=1
        if normalize_name(study["name"]):
            summary["studies_with_patient_name"]+=1
        searchable.append((study,modalities,anatomy))
    leads=Counter()
    conflicts=Counter()
    for report in reports:
        try:
            text,hash_value=engine.read_report(config,report)
            if hash_value!=json.loads(report["metadata"]).get("hash"):
                summary["reports_changed"]+=1
                continue
        except (OSError,ValueError,UnicodeError,KeyError):
            summary["reports_unavailable"]+=1
            continue
        summary["reports_verified"]+=1
        extracted=clues(text,Path(report["path"]).stem)
        if extracted["name"]:
            summary["reports_with_parsed_name"]+=1
        for idx,(study,modalities,anatomy) in enumerate(searchable):
            candidate=evaluate_exploratory(study,extracted,modalities,anatomy)
            if not candidate:
                continue
            if candidate["category"]=="conflict":
                summary["name_matched_pairs_with_identifier_conflicts"]+=1
                conflicts[idx]+=1
            else:
                summary["exploratory_pairs"]+=1
                leads[idx]+=1
                if "anatomy" in candidate["evidence"]:
                    summary["exploratory_pairs_with_anatomy"]+=1
                if "modality" in candidate["evidence"]:
                    summary["exploratory_pairs_with_modality"]+=1
                if all(x in candidate["evidence"] for x in ("name","modality","anatomy")):
                    summary["exploratory_pairs_with_all_three"]+=1
    summary["studies_with_exploratory_leads"]=len(leads)
    summary["studies_with_name_identifier_conflicts"]=len(conflicts)
    summary["studies_without_exploratory_leads"]=len(studies)-len(leads)
    return dict(summary)


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--config",default="/etc/hoag-research/config.json")
    args=parser.parse_args()
    result=inspect(json.loads(Path(args.config).read_text()))
    print("=== HOAG EXPLORATORY NAME / MODALITY / ANATOMY REPORT MATCHING ===")
    print(json.dumps(result,sort_keys=True,indent=2))
    print("Aggregate counts only; no identifiers, UIDs, paths, narratives or crosswalks printed.")
    print("Read-only. No change to matching, SQL confirmations, catalog, approvals or exports.")


if __name__=="__main__":
    main()
