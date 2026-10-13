"""Restricted patient registry and immutable export crosswalk; never shipped in packages."""
import csv
import io
import json
import time
from datetime import datetime, timezone

from store import token, research_uid


def subject_for_patient(config, db, patient, issuer, uid):
    # Preserve the original 1.50 algorithm and every existing subject identifier.
    key = issuer + '\0' + patient if patient else uid
    row = db.execute('SELECT subject FROM patient_identities WHERE patient_key=?', (key,)).fetchone()
    if row:
        return row['subject']
    subject = 'SUBJECT_' + token(config, 'subject', key)[:20]
    if patient:
        db.execute('INSERT INTO patient_identities VALUES (?,?,?,?,?)',
                   (key, subject, patient, issuer, time.time()))
    return subject


def export_identity(config, db, study, rows):
    identities = {(str(m.get('PatientID', '')), str(m.get('IssuerOfPatientID', '')))
                  for m in (json.loads(row['metadata']) for row in rows)}
    if len(identities) != 1:
        raise ValueError('Patient identifiers differ within this study; specialist review is required')
    patient, issuer = identities.pop()
    if not patient.strip():
        raise ValueError('A source MRN / Patient ID is required for consistent patient exports; specialist review is required')
    subject = subject_for_patient(config, db, patient, issuer, study['uid'])
    if subject != study['subject'] or patient != study['patient']:
        raise ValueError('Patient export identity differs from the reviewed study; specialist review is required')
    return dict(patient=patient, name=study['name'] or '', issuer=issuer, subject=subject,
                accession=study['accession'] or '', study_uid=study['uid'],
                anonymous_study_uid=research_uid(config, study['uid']))


def record_export(db, export_id, identity):
    db.execute('''INSERT INTO export_identities
        (export_id,patient,name,issuer,subject,accession,study_uid,anonymous_study_uid)
        VALUES (:export_id,:patient,:name,:issuer,:subject,:accession,:study_uid,:anonymous_study_uid)''',
        dict(identity, export_id=export_id))


def crosswalk_csv(db, subject=None):
    """One row per completed export; snapshots survive catalog replacement/deletion.

    Older exports have no immutable identity snapshot. Include their export IDs
    with an explicit status rather than inventing a mapping from today's catalog.
    """
    out = io.StringIO(newline='')
    writer = csv.writer(out)
    writer.writerow(['Export ID', 'Exported UTC', 'Original patient name', 'Original MRN',
                     'MRN issuer / source system', 'Anonymous name', 'Anonymous patient ID',
                     'Original accession', 'Original study UID', 'Anonymous study UID', 'Mapping status'])
    def safe(value):
        value = '' if value is None else str(value)
        # CSV quoting alone does not prevent spreadsheet formula execution.
        return "'" + value if value.lstrip().startswith(('=', '+', '-', '@')) or value.startswith(('\t', '\r', '\n')) else value
    for row in db.execute('''SELECT e.id,e.created,i.* FROM exports e
                            LEFT JOIN export_identities i ON i.export_id=e.id WHERE (? IS NULL OR i.subject=?) ORDER BY e.id''', (subject,subject)):
        values = [row['id'], datetime.fromtimestamp(row['created'], timezone.utc).isoformat(),
                  row['name'], row['patient'], row['issuer'], row['subject'], row['subject'],
                  row['accession'], row['study_uid'], row['anonymous_study_uid'],
                  'Recorded at export' if row['export_id'] is not None else 'Legacy export: identity snapshot unavailable']
        writer.writerow([safe(v) for v in values])
    return '\ufeff' + out.getvalue()
