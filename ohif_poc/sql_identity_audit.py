"""Private hospital-host SQL-to-DICOM identity audit: aggregate statistics ONLY.

Read-only. Never display report IDs, study identifiers, patient names, dates,
file paths, or clinical narrative. Does not use/enable the browser SQL gate.
Run under hospital authorization with the root-only SQL private index.
"""
import json
import sqlite3
from collections import Counter
from pathlib import Path

from broker_study_auth import authorized_study
from sql_broker import INDEX, require_index
from sql_dicom_pilot import text, modalities
from strict_identity_match import extract_header, normalize_name
from txt_candidate_discovery import all_patient_names


def report_identity_classification(study, narrative, procedure):
    """Reason codes only; never return protected source content."""
    header = extract_header(narrative)
    expected = normalize_name(study["name"])
    if not expected or not header["name"]:
        return "missing_primary_patient_name"
    if header["name"] != expected:
        return "different_primary_patient_name"
    names = all_patient_names(text(narrative))
    if not names:
        return "no_independent_patient_name_line"
    if names != {expected}:
        return "conflicting_patient_name_declarations"
    dob = study.get("dob", "")
    if dob and header["dob"] and dob != header["dob"]:
        return "conflicting_dob"
    if not dob or not header["dob"]:
        return "missing_dob_evidence"
    if header["exam_date"] and header["exam_date"] != study["date"]:
        return "conflicting_exam_date"
    expected_mods = {x.strip().upper() for x in study.get("modality", "").split(",") if x.strip()}
    observed_mods = modalities(procedure)
    if expected_mods and observed_mods and not expected_mods.intersection(observed_mods):
        return "conflicting_modality"
    return "identity_consistent_candidate_needs_exam_verification"


def audit(config, index=INDEX):
    if index == INDEX:
        require_index()
    with sqlite3.connect(index.as_uri() + "?mode=ro", uri=True) as sql:
        sql_records = [(narrative, procedure) for narrative, procedure
                       in sql.execute("SELECT text,procedure_name FROM narratives")]
    catalog = Path(config["state_dir"]) / "workflow.sqlite"
    with sqlite3.connect(catalog.as_uri() + "?mode=ro", uri=True) as db:
        if not db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone() == ("1",):
            raise ValueError("STOP: incomplete active catalog")
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise ValueError("STOP: index is busy")
        study_specs = db.execute("SELECT uid,count FROM studies ORDER BY uid").fetchall()
    if not study_specs:
        raise ValueError("STOP: no active studies")
    overall = Counter()
    per_study = []
    for uid, count in study_specs:
        try:
            study = authorized_study(uid, config)
        except (ValueError, OSError, LookupError) as exc:
            # Never print exception contents, which might include source paths.
            overall["study_identity_unavailable"] += 1
            continue
        local = Counter()
        for narrative, procedure in sql_records:
            result = report_identity_classification(study, narrative, procedure)
            # Focus on names which the candidate matcher would consider.
            if result == "different_primary_patient_name":
                continue
            local[result] += 1
        local["name_linked_report_population"] = sum(local.values())
        overall.update(local)
        overall["studies_validated"] += 1
        if count == 157:
            per_study.append(local)
    return {
        "active_studies_in_catalog":len(study_specs),
        "active_studies_validated":overall["studies_validated"],
        "studies_without_validated_identity":overall["study_identity_unavailable"],
        "sql_narratives_evaluated":len(sql_records),
        "aggregate_name_linked_results":{
            key:overall[key] for key in (
                "name_linked_report_population",
                "no_independent_patient_name_line",
                "conflicting_patient_name_declarations",
                "conflicting_dob",
                "missing_dob_evidence",
                "conflicting_exam_date",
                "conflicting_modality",
                "identity_consistent_candidate_needs_exam_verification",
            )
        },
        "studies_with_157_images":len(per_study),
        "157_image_study_aggregate":{
            key:sum(st[key] for st in per_study)
            for key in (
                "name_linked_report_population",
                "no_independent_patient_name_line",
                "conflicting_patient_name_declarations",
                "conflicting_dob",
                "missing_dob_evidence",
                "conflicting_exam_date",
                "conflicting_modality",
                "identity_consistent_candidate_needs_exam_verification",
            )
        },
        "browser_sql_review_enabled":False,
        "report_associations_changed":False,
    }


def main():
    from store import load_config
    config=load_config("/etc/hoag-research/config.json")
    result=audit(config)
    print("=== HOAG SQL IDENTITY AUDIT — AGGREGATE ONLY ===")
    print(json.dumps(result, indent=2, sort_keys=True))
    print("No names, UIDs, DOBs, paths, report IDs, dates, narrative or pixel data displayed.")


if __name__ == "__main__":
    main()
