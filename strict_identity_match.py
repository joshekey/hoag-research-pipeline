"""Strict local candidate matching: patient name, DOB, exam date.

This is a standalone development component. SQL result dates must NOT be
silently treated as exam dates. Candidates still require human review.
"""
import re
from datetime import datetime

NAME = re.compile(r"(?is)\bPATIENT\s*NAME\s*:\s*(.*?)\s*(?=\b(?:MEDICAL\s*RECORD\s*NUMBER|MRN|MR\s*#)\s*[:=#])")
DOB = re.compile(r"(?i)\b(?:DATE\s*OF\s*BIRTH|DOB)\s*:\s*(\d{1,4}[-/]\d{1,2}[-/]\d{1,4}|\d{8})")
EXAM = re.compile(r"(?i)\b(?:DATE\s*OF\s*STUDY|EXAM\s*DATE|DATE\s*OF\s*EXAM(?:INATION)?)\s*:\s*(\d{1,4}[-/]\d{1,2}[-/]\d{1,4}|\d{8})")

def normalize_name(value):
    s = str(value or "").replace("^", " ").replace(",", " ")
    return " ".join(sorted(re.findall(r"[A-Z]+", s.upper())))

def normalize_date(value):
    s = str(value or "").strip()
    for fmt in ("%Y%m%d", "%Y-%m-%d", "%m/%d/%Y", "%m/%d/%y",
                "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).strftime("%Y%m%d")
        except ValueError:
            pass
    return ""

def extract_header(narrative):
    text = str(narrative or "")
    name, dob, exam = NAME.search(text), DOB.search(text), EXAM.search(text)
    return {
        "name": normalize_name(name.group(1)) if name else "",
        "dob": normalize_date(dob.group(1)) if dob else "",
        "exam_date": normalize_date(exam.group(1)) if exam else "",
    }

def evaluate(dicom_name, dicom_dob, dicom_study_date, report_text,
             verified_exam_date=None, result_date=None):
    """Never approve. A report result date is only supporting evidence.

    verified_exam_date must come from a separately verified exam-date field,
    not simply a renamed SQL result_date. Missing values never match.
    """
    fields = extract_header(report_text)
    if verified_exam_date is not None:
        fields["exam_date"] = normalize_date(verified_exam_date)
    checks = {
        "name": bool(fields["name"] and fields["name"] == normalize_name(dicom_name)),
        "dob": bool(fields["dob"] and fields["dob"] == normalize_date(dicom_dob)),
        "exam_date": bool(fields["exam_date"] and fields["exam_date"] == normalize_date(dicom_study_date)),
    }
    missing = [k for k in checks if not fields[k]]
    conflicting = [k for k in checks if fields[k] and not checks[k]]
    return {
        "candidate": all(checks.values()),
        "checks": checks,
        "missing": missing,
        "conflicting": conflicting,
        "result_date_only": bool(not fields["exam_date"] and normalize_date(result_date)
                                 and normalize_date(result_date) == normalize_date(dicom_study_date)),
        "requires_human_review": True,
    }
