"""Hospital-local indexed TXT selection by exact first and last name. Dry run only."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import sqlite3
import unicodedata

import batch_reconcile as base
from txt_candidate_discovery import PATIENT_NAME_LINES, PRINTED_HEADER


def canonical(value):
    # Preserve order, accents, apostrophes and hyphens; no fuzzy/token-bag match.
    return ' '.join(unicodedata.normalize('NFC', str(value)).casefold().split())


def exact_name(value, first, last):
    value, first, last = canonical(value), canonical(first), canonical(last)
    if not first or not last: return False
    if '^' in value:
        parts = value.split('^')
        return len(parts) >= 2 and parts[0].strip() == last and parts[1].strip() == first
    if ',' in value:
        family, given = value.split(',', 1)
        given = given.strip()
        return family.strip() == last and (given == first or given.startswith(first + ' '))
    # Unstructured header: FIRST [middle components] LAST. Compound first/last
    # names must match the complete crosswalk fields at word boundaries.
    return value == first + ' ' + last or (
        value.startswith(first + ' ') and value.endswith(' ' + last)
        and len(value) > len(first) + len(last) + 2)


def declarations(text):
    names = []
    for match in PATIENT_NAME_LINES.finditer(text):
        name = re.split(r'\s+(?:MRN|MR\s*#|MEDICAL\s+RECORD\s+(?:NUMBER|#)|DOB|DATE\s+OF\s+BIRTH)\b',
                        match.group(1), maxsplit=1, flags=re.I)[0].strip()
        names.append(name)
    names.extend(m.group(1).strip() for m in PRINTED_HEADER.finditer(text))
    return names


ID_FIELDS = re.compile(r'(?im)(?:^[ \t]*(?:patient[ \t]*(?:id|number)|mrn|medical[ \t]*record[ \t]*(?:number|#))\s*[:=#]|\bMR\s*#)\s*([A-Za-z0-9._-]+)(?=\s|[)\]}]|$)')


def evaluate(text, patients, known_ids, expected_labels=()):
    names = declarations(text)
    reasons = set()
    candidates = set()
    for name in names:
        matched = {p[''].strip() for p in patients if exact_name(name, p['FNAME'], p['LNAME'])}
        candidates.update(matched)
        if len(matched) > 1: reasons.add('shared_first_last_name')
        if not matched: reasons.add('unmatched_patient_name_declaration')
    if not names: reasons.add('no_explicit_patient_name')
    if len(candidates) > 1: reasons.add('multiple_patient_candidates')
    if not candidates: reasons.add('no_exact_name_match')
    ids = {m.group(1) for m in ID_FIELDS.finditer(text)}
    if len(candidates) == 1:
        label = next(iter(candidates))
        if expected_labels and set(expected_labels) != {label}: reasons.add('crosswalk_patient_conflict')
        if ids and not known_ids.get(label): reasons.add('identifier_not_verifiable')
        if ids and ids - known_ids.get(label, set()): reasons.add('explicit_identifier_conflict')
        other_ids = set().union(*(values for owner, values in known_ids.items() if owner != label))
        if ids & other_ids: reasons.add('identifier_shared_with_other_patient')
    selected = len(candidates) == 1 and not reasons
    return {'labels': sorted(candidates), 'status': 'selected_by_exact_name' if selected else 'held',
            'reasons': sorted(reasons), 'accepted': False, 'release_allowed': False}


def select_reports(manifest, config, mounted):
    if manifest.get('mode') != 'dry_run_only': raise ValueError('complete_manifest_required')
    patients = manifest['private_crosswalk']
    labels = [p[''].strip() for p in patients]
    if len(set(labels)) != len(labels) or any(not re.fullmatch(r'P[0-9]+', v) for v in labels):
        raise ValueError('invalid_labels')
    known_ids = defaultdict(set)
    for study in manifest['studies']:
        value = study['metadata'].get('PatientID', '').strip()
        if value: known_ids[study['label']].add(value)
    by_key = {p['PATIENTID'].strip(): p[''].strip() for p in patients}
    expected = defaultdict(set)
    for doc in manifest['documents']:
        if doc.get('resolved_path') and doc['patient_key'] in by_key:
            expected[doc['resolved_path']].add(by_key[doc['patient_key']])
        for candidate in doc.get('index_candidates', []):
            if doc['patient_key'] in by_key: expected[candidate['path']].add(by_key[doc['patient_key']])
    db_path = (Path(config['state_dir'])/'workflow.sqlite').resolve()
    with closing(sqlite3.connect(db_path.as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON')
        db.execute('BEGIN')
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():
            raise ValueError('active_job')
        rows = [dict(r) for r in db.execute("SELECT * FROM files WHERE kind='report'")]
    records = []
    for row in rows:
        if Path(row['path']).suffix.lower() != '.txt': continue
        record = {'path': row['path'], 'labels': [], 'status': 'held', 'reasons': [],
                  'accepted': False, 'release_allowed': False}
        records.append(record)
        if not row['active'] or row['status'] != 'ok':
            record['reasons'] = ['inactive_or_error_index_entry']; continue
        try:
            path = base.source_checked(row['path'], config['source_roots'], Path(config['output_root']).resolve(), mounted)
            before = path.stat()
            if before.st_size > config['max_report_bytes']: raise ValueError('report_too_large')
            raw = path.read_bytes()
            after = path.stat()
            digest = hashlib.sha256(raw).hexdigest()
            if ((before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns)
                or digest != json.loads(row['metadata']).get('hash')): raise ValueError('changed_report')
            record.update(evaluate(raw.decode(config['report_encoding'], errors='strict'), patients, known_ids, expected[row['path']]))
            record['sha256'] = digest
        except Exception:
            record['reasons'] = ['source_or_index_validation_failed']
    # Identical text attributed to different patients is always held.
    groups = defaultdict(list)
    for record in records:
        if record.get('sha256'): groups[record['sha256']].append(record)
    for group in groups.values():
        if len({v for r in group for v in r['labels']}) > 1:
            for record in group:
                record['status'] = 'held'; record['reasons'].append('duplicate_content_multiple_patients')
    counts = Counter(r['status'] for r in records)
    counts.update(indexed_txt_records=len(records), patients=len(patients),
        patients_with_selected_reports=len({v for r in records if r['status']=='selected_by_exact_name' for v in r['labels']}))
    reasons = Counter(reason for r in records for reason in set(r['reasons']))
    return {'policy': 'exact_first_last_name_v1', 'mode': 'report_selection_dry_run',
            'counts': dict(counts), 'reason_counts': dict(reasons), 'reports': records,
            'accepted_reports': 0, 'release_allowed': False,
            'dicom_readiness': 'unchanged_not_reassessed',
            'identifier_evidence': 'observed_manifest_ids_only_not_authoritative_alias_approval'}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/hoag-research/config.json')
    parser.add_argument('--manifest', required=True)
    args = parser.parse_args()
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    try:
        config = json.loads(Path(args.config).read_text())
        path = Path(args.manifest)
        root = (Path(config['state_dir'])/'batch-reconciliation').resolve()
        if root not in path.resolve().parents or any(p.is_symlink() for p in (path, *path.parents)):
            raise ValueError('private_manifest_required')
        raw = path.read_bytes()
        result = select_reports(json.loads(raw), config, base.mounts(Path('/proc/self/mountinfo').read_text()))
        result['input_manifest_sha256'] = hashlib.sha256(raw).hexdigest()
        run = base.private_run_directory(config)
        with (run/'report-selection.private.json').open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
        print(json.dumps({key: result[key] for key in ('counts', 'reason_counts', 'accepted_reports', 'release_allowed')}))
        return 0
    except Exception:
        print(json.dumps({'report_selection_completed': False, 'release_allowed': False}))
        return 2


if __name__ == '__main__': raise SystemExit(main())
