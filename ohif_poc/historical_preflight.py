"""Read-only preflight for historical OHIF candidate image compatibility.

Use only on hospital host. Print aggregate counts, never patient data/UIDs.
The historical review authorization gate must remain disabled during preflight.
"""
import json
import sqlite3
from collections import Counter
from pathlib import Path

import pydicom
import engine
from ohif_poc.clinical_dicomweb import SUPPORTED_SYNTAXES
from historical_emr_overlap import build_historical_index
from historical_emr_lead_uniqueness import summarize
from emr_txt_leads import exam_dates, exam_terms
from txt_candidate_discovery import clues
from strict_identity_match import normalize_name
from collections import defaultdict


def check(config):
    from historical_emr_review import checked_report_identity
    cat=Path(config["state_dir"])/"workflow.sqlite"
    with sqlite3.connect(cat.as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        rows=[dict(x) for x in db.execute(
            "SELECT * FROM files WHERE kind='dicom' AND active=0 AND status='ok'")]
        texts=[dict(x) for x in db.execute(
            "SELECT * FROM files WHERE kind='report' AND active=0 AND status='ok'")]
    historic,_=build_historical_index([r["metadata"] for r in rows])
    names=defaultdict(set)
    for uid,entry in historic.items():
        if len(entry["names"])==1 and len(entry["dates"])==1:
            names[next(iter(entry["names"]))].add(uid)
    selected=defaultdict(set)
    for row in texts:
        try:
            txt,hash_value=engine.read_report(config,row)
            if hash_value!=json.loads(row["metadata"]).get("hash"):
                continue
        except (OSError,ValueError,KeyError,UnicodeError,TypeError):
            continue
        fields=clues(txt,Path(row["path"]).stem)
        dates=exam_dates(txt)
        mods,_=exam_terms(txt)
        if not mods:
            mods=fields["modalities"]
        for uid in names.get(fields["name"],()):
            if (checked_report_identity(txt,next(iter(historic[uid]["names"])))=="consistent"
                    and dates & historic[uid]["dates"] and mods & historic[uid]["modalities"]):
                selected[uid].add(row["id"])
    if len(selected)!=2 or any(len(v)!=1 for v in selected.values()):
        raise ValueError("STOP: expected exactly two unique validated historical leads")
    counts=Counter(studies=len(selected))
    syntax_counts=Counter()
    for row in rows:
        meta=json.loads(row["metadata"])
        if meta.get("StudyInstanceUID") not in selected:
            continue
        try:
            path=engine.source_path(config,row)
            ds=pydicom.dcmread(path,stop_before_pixels=True)
            if (str(ds.get("StudyInstanceUID","")) != meta.get("StudyInstanceUID") or
                str(ds.get("SeriesInstanceUID","")) != meta.get("SeriesInstanceUID") or
                str(ds.get("SOPInstanceUID","")) != meta.get("SOPInstanceUID") or
                normalize_name(str(ds.get("PatientName",""))) !=
                normalize_name(meta.get("PatientName",""))):
                counts["invalid_headers"]+=1
            elif int(ds.get("NumberOfFrames",1) or 1)!=1:
                counts["multiframe"]+=1
            elif str(ds.file_meta.TransferSyntaxUID) not in SUPPORTED_SYNTAXES:
                counts["unsupported_transfer_syntax"]+=1
                syntax_counts[str(ds.file_meta.TransferSyntaxUID.name)]+=1
            else:
                counts["compatible_instances"]+=1
        except (OSError,ValueError,AttributeError,pydicom.errors.InvalidDicomError):
            counts["invalid_headers"]+=1
        counts["instances_total"]+=1
    # Never conceal aggregate diagnostic evidence behind the deployment gate.
    # Do not print identifiers, filenames, report narratives, or paths.
    print("=== HISTORICAL OHIF COMPATIBILITY (AGGREGATE ONLY) ===")
    print(json.dumps({
        "studies":counts["studies"],
        "instances_total":counts["instances_total"],
        "compatible_instances":counts["compatible_instances"],
        "invalid_headers":counts["invalid_headers"],
        "multiframe":counts["multiframe"],
        "unsupported_transfer_syntax":counts["unsupported_transfer_syntax"],
        "unsupported_syntax_names_and_counts":dict(sorted(syntax_counts.items())),
    },sort_keys=True,indent=2))
    print("No patient information, UIDs, filenames, source paths, or image data displayed.")
    if counts["instances_total"]!=348 or counts["compatible_instances"]!=348:
        raise ValueError("STOP: image preflight incompatible; review aggregate categories above. No installation performed.")
    return dict(counts)


if __name__=="__main__":
    from store import load_config
    result=check(load_config("/etc/hoag-research/config.json"))
    print("Historical OHIF preflight passed. No clinical data printed.")
