"""Conservative local-only report-key extraction and candidate matching.

Identifiers live only in the restricted local catalog; do not export or log them.
Metadata matches are *candidates*, never approvals.
"""
import re
from pathlib import Path

ACCESSION = re.compile(r'(?im)\b(?:accession(?:\s*(?:number|no\.?|#))?|acc\s*#)\s*(?:[:=]|\s+)\s*([A-Za-z0-9][A-Za-z0-9._-]*)')
UID = re.compile(r'(?im)^\s*Study(?:\s*Instance)?\s*UID\s*[:=]\s*([0-9.]+)')
NAME = re.compile(r'(?im)^\s*PATIENT\s*NAME\s*:\s*([^\r\n]+)')
MRN = re.compile(r'(?im)^\s*(?:MEDICAL\s*RECORD\s*NUMBER|MRN)\s*:\s*([A-Za-z0-9_-]+)')
DOB = re.compile(r'(?im)^\s*(?:DATE\s*OF\s*BIRTH|DOB)\s*:\s*([0-9/-]+)')
STUDY_DATE = re.compile(r'(?im)^\s*(?:DATE\s*OF\s*STUDY|EXAM\s*DATE)\s*:\s*([0-9/-]+)')
TITLE = re.compile(r'(?im)^\s*((?:MRI|MR|CT|US|ULTRASOUND|XRAY|X-RAY)\s+[^\r\n]{3,110})\s*$')


def norm_id(value):
    return re.sub(r'[^A-Z0-9]', '', str(value or '').upper())


def norm_name(value):
    value = str(value or '').replace('^', ' ').replace(',', ' ')
    return ' '.join(sorted(re.findall(r'[A-Z]+', value.upper())))


def date8(value):
    value = str(value or '').strip()
    if re.fullmatch(r'\d{8}', value):
        return value
    match = re.fullmatch(r'(\d{1,2})/(\d{1,2})/(\d{2}|\d{4})', value)
    if not match:
        return ''
    mm, dd, yyyy = match.groups()
    if len(yyyy) == 2:
        yyyy = str(2000 + int(yyyy) if int(yyyy) <= 30 else 1900 + int(yyyy))
    try:
        from datetime import date
        return date(int(yyyy), int(mm), int(dd)).strftime('%Y%m%d')
    except ValueError:
        return ''


def modality(value):
    start = str(value or '').upper().strip()
    if re.match(r'^MR(?:I)?\b', start):
        return 'MR'
    if re.match(r'^CT\b', start):
        return 'CT'
    if re.match(r'^(?:US|ULTRASOUND)\b', start):
        return 'US'
    if re.match(r'^(?:XRAY|X-RAY)\b', start):
        return 'DX'
    return ''


def first(pattern, text):
    hit = pattern.search(text)
    return hit.group(1).strip() if hit else ''


def extract(text, filename):
    title = first(TITLE, text)
    accessions = set(ACCESSION.findall(text))
    # Retain legacy filename-stem behavior; never equate an arbitrary filename to an accession
    # unless it exactly equals the DICOM accession.
    accessions.add(Path(filename).stem)
    return {
        'accessions': sorted(accessions),
        'uids': sorted(set(UID.findall(text))),
        'patient_name': norm_name(first(NAME, text)),
        'mrn': norm_id(first(MRN, text)),
        'birth_date': date8(first(DOB, text)),
        'study_date': date8(first(STUDY_DATE, text)),
        'modality': modality(title),
    }


def metadata_reason(dicom, report):
    """Require date and modality plus two identifying demographics or exact MRN.

    This emits candidate reasons only. Missing/conflicting fields do not match.
    """
    if not report.get('study_date') or report['study_date'] != date8(dicom.get('StudyDate')):
        return ''
    if not report.get('modality') or report['modality'] != dicom.get('Modality'):
        return ''
    rid = report.get('mrn', '')
    did = norm_id(dicom.get('PatientID'))
    if rid and did and rid == did:
        return 'MRN + study date + modality (review required)'
    if (report.get('patient_name') and report['patient_name'] == norm_name(dicom.get('PatientName'))
            and report.get('birth_date') and report['birth_date'] == date8(dicom.get('PatientBirthDate'))):
        return 'Name + birth date + study date + modality (review required)'
    return ''
