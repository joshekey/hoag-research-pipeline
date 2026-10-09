"""Read-only OHIF compatibility preflight for an indexed HOAG study.

Run only on the hospital server. No patient identifiers, dates, series UIDs,
or file paths are printed. Does not alter source DICOM or workflow databases.
Not a clinical image-review validator.
"""
import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path

import pydicom
import engine

REQUIRED = ("StudyInstanceUID", "SeriesInstanceUID", "SOPInstanceUID",
            "SOPClassUID", "TransferSyntaxUID")

def check(config, uid):
    catalog = Path(config["state_dir"]) / "workflow.sqlite"
    with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        ready = db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not ready or ready[0] != "1":
            raise ValueError("Catalog scan incomplete")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("Active processing job")
        study = db.execute("SELECT uid,count FROM studies WHERE uid=?", (uid,)).fetchone()
        if study is None:
            raise ValueError("Requested study not in catalog")
        rows = db.execute("""SELECT * FROM files WHERE kind='dicom' AND active=1 AND status='ok'
                         AND json_extract(metadata,'$.StudyInstanceUID')=? ORDER BY id""",(uid,)).fetchall()
        if len(rows) != study["count"]:
            raise ValueError("Indexed file count mismatch")
    series = set()
    instances = set()
    syntax_counts = Counter()
    multi_frame = 0
    missing = Counter()
    errors = 0
    for row in rows:
        try:
            path = engine.source_path(config,row)
            ds = pydicom.dcmread(path, stop_before_pixels=True)
            transfer_syntax = str(getattr(ds.file_meta,"TransferSyntaxUID",""))
            values = {"StudyInstanceUID": str(ds.get("StudyInstanceUID","")),
                      "SeriesInstanceUID":str(ds.get("SeriesInstanceUID","")),
                      "SOPInstanceUID":str(ds.get("SOPInstanceUID","")),
                      "SOPClassUID":str(ds.get("SOPClassUID","")),
                      "TransferSyntaxUID":transfer_syntax}
            for tag in REQUIRED:
                if not values[tag]:
                    missing[tag]+=1
            if values["StudyInstanceUID"] != uid:
                errors+=1
            if values["SeriesInstanceUID"]:
                series.add(values["SeriesInstanceUID"])
            if values["SOPInstanceUID"]:
                instances.add(values["SOPInstanceUID"])
            syntax_counts["known" if transfer_syntax else "missing"]+=1
            if int(ds.get("NumberOfFrames",1) or 1)>1:
                multi_frame+=1
        except (OSError,ValueError,pydicom.errors.InvalidDicomError,AttributeError,TypeError):
            errors+=1
    return {"instances_indexed":len(rows),"series_count":len(series),
            "unique_sop_instances":len(instances),"multi_frame_objects":multi_frame,
            "missing_required_tags":dict(missing),"read_or_consistency_errors":errors,
            "ready_for_dicomweb_adapter_design":not (errors or missing or len(instances)!=len(rows))}

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--study-position",type=int,default=6)
    args=parser.parse_args()
    if args.study_position<1:
        raise SystemExit("Invalid study position")
    config=json.loads(Path("/etc/hoag-research/config.json").read_text())
    catalog=Path(config["state_dir"])/"workflow.sqlite"
    with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
        row=db.execute("SELECT uid FROM studies ORDER BY uid LIMIT 1 OFFSET ?",(args.study_position-1,)).fetchone()
    if row is None:
        raise SystemExit("No study at this position")
    result=check(config,row[0])
    print("HOAG OHIF preflight — aggregate only")
    print(json.dumps(result,indent=2,sort_keys=True))
    print("No patient identifiers, UIDs, image data, or paths displayed.")
    print("This does not authorize publication, approval or export.")

if __name__=="__main__":
    main()
