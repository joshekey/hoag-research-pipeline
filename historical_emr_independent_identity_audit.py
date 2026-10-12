"""Independent read-only identity audit for historical EMR report leads.

No web access or authorization gate needed; no database modifications.
No names, MRNs, source paths, UIDs, dates or report text printed.
"""
import json
import sqlite3
from pathlib import Path
from collections import Counter, defaultdict

import pydicom
import engine
from emr_txt_leads import exam_dates, exam_terms
from historical_emr_overlap import build_historical_index
from txt_candidate_discovery import clues, all_patient_names, PRINTED_HEADER, PATIENT_NAME_LINES
from strict_identity_match import normalize_name


def audit(config):
    catalog=Path(config["state_dir"])/"workflow.sqlite"
    with sqlite3.connect(catalog.as_uri()+"?mode=ro", uri=True) as db:
        db.row_factory=sqlite3.Row
        if db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()[0]!="1":
            raise ValueError("Catalog not ready")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Processing job running")
        dicoms=[dict(r) for r in db.execute(
            "SELECT * FROM files WHERE kind='dicom' AND active=0 AND status='ok'")]
        reports=[dict(r) for r in db.execute(
            "SELECT * FROM files WHERE kind='report' AND active=0 AND status='ok'")]
    history,_=build_historical_index([r["metadata"] for r in dicoms])
    byname=defaultdict(set)
    for uid,entry in history.items():
        if len(entry["names"])==1 and len(entry["dates"])==1:
            byname[next(iter(entry["names"]))].add(uid)
    candidates=defaultdict(list)
    report_cache={}
    counts=Counter()
    for row in reports:
        try:
            body,sha=engine.read_report(config,row)
            if sha!=json.loads(row["metadata"]).get("hash"):
                counts["reports_with_changed_hash"]+=1
                continue
        except (OSError,ValueError,UnicodeError,KeyError,TypeError):
            counts["reports_unavailable"]+=1
            continue
        parsed=clues(body,Path(row["path"]).stem)
        dos=exam_dates(body)
        modes,_=exam_terms(body)
        if not modes:
            modes=parsed["modalities"]
        for uid in byname.get(parsed["name"],()):
            if dos & history[uid]["dates"] and modes & history[uid]["modalities"]:
                candidates[uid].append(row["id"])
                report_cache[row["id"]]=body
    counts["qualifying_studies"]=len(candidates)
    counts["qualifying_pairs"]=sum(map(len,candidates.values()))
    if len(candidates)>20:
        raise ValueError("Unexpected candidate count; stop")
    eligible={uid:ids[0] for uid,ids in candidates.items() if len(ids)==1}
    counts["unique_pairs_for_independent_identity_check"]=len(eligible)
    byuid=defaultdict(list)
    for row in dicoms:
        meta=json.loads(row["metadata"])
        uid=meta.get("StudyInstanceUID")
        if uid in eligible:
            byuid[uid].append(row)
    for uid,rid in eligible.items():
        expected=next(iter(history[uid]["names"]))
        body=report_cache[rid]
        printed={normalize_name(m.group(1)) for m in PRINTED_HEADER.finditer(body[:20000])}
        labeled={normalize_name(m.group(1).strip()) for m in PATIENT_NAME_LINES.finditer(body[:20000])}
        observed=all_patient_names(body)
        if printed:
            counts["pairs_with_printed_header_name"]+=1
        if labeled:
            counts["pairs_with_patient_name_label"]+=1
        if printed and labeled and printed != labeled:
            counts["pairs_with_printed_vs_labeled_name_conflict"]+=1
        if not observed or observed!={expected}:
            counts["pairs_failed_report_name_identity_check"]+=1
            continue
        counts["pairs_passed_report_name_identity_check"]+=1
        source_names=set()
        invalid=False
        for row in byuid[uid]:
            try:
                source=engine.source_path(config,row)
                ds=pydicom.dcmread(source,stop_before_pixels=True,
                                   specific_tags=["PatientName","StudyInstanceUID",
                                                  "SeriesInstanceUID","SOPInstanceUID"])
                meta=json.loads(row["metadata"])
                if any(str(ds.get(key,"")) != str(meta.get(key,"")) for key in
                       ("StudyInstanceUID","SeriesInstanceUID","SOPInstanceUID")):
                    invalid=True
                    break
                source_names.add(normalize_name(ds.get("PatientName","")))
                counts["original_dicom_headers_read"]+=1
            except (OSError,ValueError,TypeError,pydicom.errors.InvalidDicomError):
                invalid=True
                break
        if invalid or source_names!={expected} or len(byuid[uid])!=history[uid]["instances"]:
            counts["pairs_failed_original_dicom_identity_check"]+=1
        else:
            counts["pairs_passed_original_dicom_identity_check"]+=1
    return dict(counts)


if __name__=="__main__":
    from store import load_config
    results=audit(load_config("/etc/hoag-research/config.json"))
    print("=== INDEPENDENT HISTORICAL PATIENT IDENTITY AUDIT ===")
    print(json.dumps(results,sort_keys=True,indent=2))
    print("No patient names, identifiers, UIDs, filenames, dates or text shown.")
    print("Read only: does not enable the historical viewer or approve a report.")
