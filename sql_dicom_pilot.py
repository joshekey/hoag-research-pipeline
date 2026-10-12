"""Hospital-local, aggregate-only DICOM to SQL report candidate diagnostic.

Standalone development tool. Does not approve, sanitize, export, or change catalog.
Requires a complete HOAG scan and does not treat result/encounter dates as exam dates.
No patient names, UIDs, report IDs, file paths, dates or narratives are printed.
"""
import json
import re
import sqlite3
from collections import Counter
from pathlib import Path

from strict_identity_match import evaluate, extract_header, normalize_date, normalize_name

REGIONS = {
    "LUMBAR": r"\bLUMBAR\b|\bL[\s-]?SPINE\b",
    "CERVICAL": r"\bCERVICAL\b|\bC[\s-]?SPINE\b",
    "THORACIC": r"\bTHORACIC\b|\bT[\s-]?SPINE\b",
    "PELVIS": r"\bPELVIS\b|\bPELVIC\b",
    "CHEST": r"\bCHEST\b",
    "BRAIN": r"\bBRAIN\b|\bHEAD\b",
    "KNEE": r"\bKNEE\b",
    "SHOULDER": r"\bSHOULDER\b",
    "HIP": r"\bHIP\b",
}
MODALITIES = {
    "MR": r"\b(?:MR|MRI)\b",
    "CT": r"\bCT\b",
    "US": r"\b(?:US|ULTRASOUND)\b",
    "DX": r"\b(?:XRAY|X-RAY|X RAY|RADIOGRAPH|XR)\b",
}


def text(value):
    return value.decode("utf-8", "replace") if isinstance(value, bytes) else str(value or "")


def regions(value):
    value = text(value).upper()
    return {k for k, expression in REGIONS.items() if re.search(expression, value)}


def modalities(value):
    value = text(value).upper()
    found = {k for k, expression in MODALITIES.items() if re.search(expression, value)}
    if "DX" in found:
        found.add("CR")
    return found


def compare(study, report):
    """Return only classifications, never identifiers or clinical values."""
    result = evaluate(study["name"], study["dob"], study["date"],
                      report["narrative"], result_date=report["result_date"])
    # Missing modality/anatomy is unknown, not permission to ignore a conflict.
    study_mods, report_mods = study["modalities"], modalities(report["procedure"])
    study_regions, report_regions = study["regions"], regions(report["procedure"])
    modality_status = ("compatible" if study_mods and report_mods and study_mods & report_mods
                       else "conflict" if study_mods and report_mods else "unknown")
    region_status = ("compatible" if study_regions and report_regions and study_regions & report_regions
                     else "conflict" if study_regions and report_regions else "unknown")
    identity = result["checks"]
    name_dob = identity["name"] and identity["dob"]
    conflicts = modality_status == "conflict" or region_status == "conflict"
    # A verified clinical match requires verified exam date, patient identity,
    # procedure compatibility, plus a separate human approval (not provided here).
    strict = bool(result["candidate"] and not conflicts
                  and modality_status == "compatible" and region_status == "compatible")
    supporting = bool(name_dob and result["result_date_only"] and not conflicts)
    return {
        "strict_candidate": strict,
        "name_dob_result_date_support": supporting,
        "name_dob": bool(name_dob),
        "name_dob_conflicts": bool(not name_dob),
        "modality": modality_status,
        "region": region_status,
        "exam_date_proven": bool(identity["exam_date"]),
    }


def compare_all(studies, reports):
    output = []
    for study in studies:
        counter = Counter()
        for report in reports:
            finding = compare(study, report)
            if not finding["name_dob"]:
                continue
            counter["name_dob"] += 1
            if finding["strict_candidate"]:
                counter["strict_candidate"] += 1
            if finding["name_dob_result_date_support"]:
                counter["supporting_result_date_only"] += 1
            if finding["modality"] == "conflict":
                counter["modality_conflict"] += 1
            if finding["region"] == "conflict":
                counter["region_conflict"] += 1
            if finding["modality"] == "unknown" or finding["region"] == "unknown":
                counter["unknown_exam_classification"] += 1
        output.append(counter)
    return output


def catalog_studies(config):
    import pydicom
    catalog = Path(config["state_dir"]) / "workflow.sqlite"
    roots = [Path(root).resolve(strict=True) for root in config["source_roots"]]
    with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as db:
        complete = db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()
        if not complete or complete[0] != "1":
            raise ValueError("HOAG scan incomplete")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("HOAG job active")
        rows = db.execute("SELECT uid,date,modality FROM studies ORDER BY uid").fetchall()
        studies = []
        for uid, date, modality in rows:
            record = db.execute("""SELECT path FROM files WHERE kind='dicom'
                                   AND active=1 AND status='ok'
                                   AND json_extract(metadata,'$.StudyInstanceUID')=?
                                   LIMIT 1""", (uid,)).fetchone()
            if not record:
                continue
            path = Path(record[0])
            if path.is_symlink():
                raise ValueError("DICOM symlink not allowed")
            real = path.resolve(strict=True)
            if not any(real.is_relative_to(root) for root in roots):
                raise ValueError("DICOM source escaped configured roots")
            ds = pydicom.dcmread(real, stop_before_pixels=True,
                                specific_tags=["PatientName", "PatientBirthDate",
                                               "StudyDescription", "SeriesDescription",
                                               "BodyPartExamined", "Modality"])
            descriptors = " ".join(str(ds.get(k, "")) for k in
                                   ("StudyDescription", "SeriesDescription", "BodyPartExamined"))
            mods = {p.strip() for p in text(modality).upper().split(",") if p.strip()}
            studies.append({"name": str(ds.get("PatientName", "")),
                            "dob": str(ds.get("PatientBirthDate", "")),
                            "date": normalize_date(date),
                            "modalities": mods,
                            "regions": regions(descriptors)})
    return studies


def sql_reports(config):
    path = Path(config["state_dir"]) / "sql-private" / "reports-v2.sqlite"
    if path.is_symlink():
        raise ValueError("SQL index symlink not allowed")
    if path.stat().st_mode & 0o077:
        raise ValueError("SQL index file permissions not restricted")
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        return [{"narrative": narrative, "procedure": procedure, "result_date": date}
                for narrative, procedure, date in db.execute(
                    "SELECT text,procedure_name,result_date FROM narratives")]


def main():
    config = json.loads(Path("/etc/hoag-research/config.json").read_text())
    studies = catalog_studies(config)
    reports = sql_reports(config)
    summaries = compare_all(studies, reports)
    print("HOAG V2 SQL/DICOM DIAGNOSTIC — AGGREGATE ONLY")
    print("Studies:", len(studies))
    print("SQL narratives:", len(reports))
    for i, counts in enumerate(summaries, 1):
        print("Study", i, "name_DOB", counts["name_dob"],
              "strict_exam_candidates", counts["strict_candidate"],
              "result_date_only", counts["supporting_result_date_only"],
              "modality_conflicts", counts["modality_conflict"],
              "anatomy_conflicts", counts["region_conflict"],
              "unknown_exam", counts["unknown_exam_classification"])
    print("No identifiers, exam dates, report text or clinical values displayed.")
    print("No matches approved or exported.")


if __name__ == "__main__":
    main()
