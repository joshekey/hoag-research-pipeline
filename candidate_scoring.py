"""Explainable heuristic candidate score, never probability or approval.

Missing evidence adds zero. Contradictions are explicitly visible and block
automatic qualification; a human may investigate but cannot override conflicts
via this score. MRN is deliberately excluded.
"""
import re
from strict_identity_match import normalize_date, normalize_name
from sql_dicom_pilot import modalities, regions, text

WEIGHTS = {"name": 35, "dob": 25, "exam_date": 25, "exam_type": 15}
REGION_KEYS = tuple(regions("lumbar cervical thoracic pelvis chest brain knee shoulder hip"))


def date_evidence(exam, result, study_date):
    exam = normalize_date(exam)
    result = normalize_date(result)
    target = normalize_date(study_date)
    if exam:
        return "match" if exam == target else "conflict"
    if result and result == target:
        return "result-date-only"
    return "missing"


def score(study, report, header):
    sn, rn = normalize_name(study.get("name")), header.get("name", "")
    sd, rd = normalize_date(study.get("dob")), normalize_date(header.get("dob"))
    name = "match" if sn and rn and sn == rn else ("conflict" if sn and rn else "missing")
    dob = "match" if sd and rd and sd == rd else ("conflict" if sd and rd else "missing")
    date = date_evidence(header.get("exam_date"), report.get("result_date"), study.get("date"))
    sm = {m.strip() for m in text(study.get("modality")).upper().split(",") if m.strip()}
    rm = modalities(report.get("procedure"))
    sr = set(study.get("regions", ()))
    rr = regions(report.get("procedure"))
    if sm and rm and not sm & rm:
        exam_type = "conflict"
    elif sr and rr and not sr & rr:
        exam_type = "conflict"
    elif sm and rm and sm & rm and sr and rr and sr & rr:
        exam_type = "match"
    else:
        exam_type = "missing"
    evidence = {"name": name, "dob": dob, "exam_date": date, "exam_type": exam_type}
    points = sum(weight for k, weight in WEIGHTS.items() if evidence[k] == "match")
    conflicts = [key for key, state in evidence.items() if state == "conflict"]
    return {"score": points, "evidence": evidence, "conflicts": conflicts,
            "result_date_support": date == "result-date-only",
            "manual_review_required": True, "score_is_probability": False}
