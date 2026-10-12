"""Validate source availability for uniquely paired historical EMR TXT leads.

READ ONLY: no DICOM pixel reads, DB updates, reindexing, or report assignment.
Print aggregate counts only. Never print patient identifiers, UIDs, dates,
paths, report content, or source exceptions containing paths.
"""
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import engine
from emr_txt_leads import exam_dates, exam_terms
from historical_emr_overlap import build_historical_index
from txt_candidate_discovery import clues


def verify(config, max_studies=20):
    catalog=Path(config["state_dir"])/"workflow.sqlite"
    with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        ready=db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0]!="1":
            raise ValueError("Catalog scan incomplete")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Processing job active")
        dicoms=[dict(row) for row in db.execute(
            "SELECT * FROM files WHERE kind='dicom' AND active=0 AND status='ok'")]
        reports=[dict(row) for row in db.execute(
            "SELECT * FROM files WHERE kind='report' AND active=0 AND status='ok'")]
    history,_=build_historical_index([row["metadata"] for row in dicoms])
    lookup=defaultdict(set)
    for uid,record in history.items():
        if len(record["names"])==1 and len(record["dates"])==1:
            lookup[next(iter(record["names"]))].add(uid)
    pairs=defaultdict(set)
    for report in reports:
        try:
            text,sha=engine.read_report(config,report)
            if sha != json.loads(report["metadata"]).get("hash"):
                continue
        except (OSError,ValueError,UnicodeError,KeyError,TypeError):
            continue
        parsed=clues(text,Path(report["path"]).stem)
        dos=exam_dates(text)
        modes,_=exam_terms(text)
        if not modes:
            modes=parsed["modalities"]
        for uid in lookup.get(parsed["name"],()):
            if dos & history[uid]["dates"] and modes & history[uid]["modalities"]:
                pairs[uid].add(report["id"])
    counts=Counter()
    counts["strong_lead_studies"]=len(pairs)
    counts["strong_lead_pairs"]=sum(len(x) for x in pairs.values())
    counts["studies_with_single_lead"]=sum(len(x)==1 for x in pairs.values())
    counts["studies_with_multiple_leads"]=sum(len(x)>1 for x in pairs.values())
    if len(pairs)>max_studies or any(len(x)!=1 for x in pairs.values()):
        counts["source_validation_skipped_due_to_ambiguity"]=1
        return dict(counts)
    matched=set(pairs)
    checked_studies=set()
    failed_studies=set()
    for row in dicoms:
        try:
            uid=json.loads(row["metadata"]).get("StudyInstanceUID")
        except (ValueError,TypeError):
            continue
        if uid not in matched:
            continue
        checked_studies.add(uid)
        counts["historical_instances_checked"]+=1
        try:
            engine.source_path(config,row)  # read-only mount, path, size and mtime
            counts["historical_instances_unchanged"]+=1
        except (OSError,ValueError,TypeError):
            failed_studies.add(uid)
            counts["historical_instances_missing_or_changed"]+=1
    counts["historical_studies_fully_source_accessible"]=len(checked_studies-failed_studies)
    counts["historical_studies_with_missing_or_changed_files"]=len(failed_studies)
    return dict(counts)


if __name__=="__main__":
    from store import load_config
    outcome=verify(load_config("/etc/hoag-research/config.json"))
    print("=== HISTORICAL EMR LEAD SOURCE VALIDATION ===")
    print(json.dumps(outcome,sort_keys=True,indent=2))
    print("Aggregate counts only. No patient information, IDs, UIDs or paths.")
    print("No DICOM image bytes read. No database, scope or approval changes.")
