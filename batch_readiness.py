"""Hospital-local second-pass analysis. No acceptance, indexing or export."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import logging
import os
from pathlib import Path
import warnings

import pydicom
import batch_reconcile as base


IDENTITY = ('PatientName', 'PatientID', 'IssuerOfPatientID', 'PatientBirthDate')


def normalized(tag, value):
    if tag == 'PatientName':
        return base.normalize_name(value)
    if tag == 'PatientBirthDate':
        return base.normalize_date(value)
    return value.strip()  # Identifier case and punctuation are significant.


def classify_values(tag, values):
    raw = set(values)
    populated = {normalized(tag, v) for v in raw if v.strip()}
    if len(populated) > 1:
        return 'conflicting_populated_values'
    if '' in populated:
        return 'invalid_value'
    if any(not v.strip() for v in raw):
        return 'missing_values'
    return 'formatting_only' if len(raw) > 1 else 'consistent'


def fingerprint(path):
    before = path.stat()
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(block)
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise base.ReconciliationError('source_changed_during_hash')
    return digest.hexdigest()


def analyze(manifest, config, mounted):
    if manifest.get('mode') != 'dry_run_only':
        raise base.ReconciliationError('complete_manifest_required')
    roots, output = config['source_roots'], Path(config['output_root']).resolve()
    patients = manifest['private_crosswalk']
    states = {}
    folders = {}
    for patient in patients:
        label = patient[''].strip()
        if label in states or not base.re.fullmatch(r'P[0-9]+', label):
            raise base.ReconciliationError('invalid_patient_labels')
        states[label] = {'label': label, 'blockers': set(), 'studies': set(), 'paths': set()}
        try:
            folders[label] = base.source_checked(base.map_unc(patient['INPUTFOLDER'], mounted, roots), roots, output, mounted)
        except Exception:
            states[label]['blockers'].add('folder_mapping_unverified')
    by_key = {p['PATIENTID'].strip(): p[''].strip() for p in patients}
    details, objects, groups = [], {}, defaultdict(list)
    def hold(labels, reason):
        for label in labels:
            states[label]['blockers'].add(reason)
    # Re-read every header: the original manifest retained only one header per study.
    for study in manifest['studies']:
        for instance in study['instances']:
            path = Path(instance['path'])
            owners = [label for label, folder in folders.items() if path == folder or folder in path.parents]
            if not owners:
                owners = [study['label']]
                hold(owners, 'instance_ownership_unverified')
            if len(owners) != 1:
                hold(owners, 'overlapping_patient_folders')
            for label in owners:
                states[label]['paths'].add(str(path))
                states[label]['studies'].add(study['uid'])
            if str(path) in objects:
                objects[str(path)]['owners'].update(owners)
                continue
            record = {'path': str(path), 'owners': set(owners), 'study': study['uid'],
                      'size': instance['size'], 'mtime_ns': instance['mtime_ns']}
            objects[str(path)] = record
            try:
                base.source_checked(path, roots, output, mounted)
                before = path.stat()
                if (before.st_size, before.st_mtime_ns) != (instance['size'], instance['mtime_ns']):
                    raise base.ReconciliationError('changed_since_reconciliation')
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore')
                    ds = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=base.TAGS)
                meta = {tag: str(ds.get(tag, '')) for tag in base.TAGS}
                after = path.stat()
                if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                    raise base.ReconciliationError('changed_during_header_read')
                if any(meta[tag] != value for tag, value in (
                    ('StudyInstanceUID', study['uid']), ('SeriesInstanceUID', instance['series_uid']),
                    ('SOPInstanceUID', instance['sop_uid']))):
                    raise base.ReconciliationError('membership_changed')
                record['metadata'] = meta
                groups[meta['SOPInstanceUID']].append(record)
            except Exception as error:
                hold(owners, 'instance_unverified')
                details.append({'kind': 'header_recheck', 'path': str(path), 'error': base.error_code(error)})
    duplicates = []
    for uid, records in groups.items():
        if len(records) < 2:
            continue
        labels = set().union(*(r['owners'] for r in records))
        studies = {r['study'] for r in records}
        series = {r['metadata']['SeriesInstanceUID'] for r in records}
        hashes = []
        try:
            for record in records:
                path = base.source_checked(record['path'], roots, output, mounted)
                hashes.append(fingerprint(path))
                current = path.stat()
                if (current.st_size, current.st_mtime_ns) != (record['size'], record['mtime_ns']):
                    raise base.ReconciliationError('changed_since_reconciliation')
            category = 'identical_bytes' if len(set(hashes)) == 1 else 'different_bytes_unresolved'
        except Exception:
            category = 'unreadable_or_changed'
        cross_patient, cross_study = len(labels) > 1, len(studies) > 1
        if category != 'identical_bytes' or cross_patient or cross_study or len(series) > 1:
            hold(labels, 'duplicate_uid_conflict_or_uncertainty')
        duplicates.append({'sop_uid': uid, 'classification': category, 'cross_patient': cross_patient,
                           'cross_study': cross_study, 'cross_series': len(series) > 1,
                           'labels': sorted(labels), 'studies': sorted(studies),
                           'paths': [r['path'] for r in records], 'sha256': hashes})
    identities = []
    patient_ids = defaultdict(set)
    study_owners = defaultdict(set)
    for label, state in states.items():
        records = [r for r in objects.values() if label in r['owners'] and 'metadata' in r]
        classifications = {tag: classify_values(tag, [r['metadata'][tag] for r in records]) for tag in IDENTITY}
        if not records:
            state['blockers'].add('no_verified_instances')
        for tag, category in classifications.items():
            if category not in ('consistent', 'formatting_only'):
                state['blockers'].add('identity_' + category)
        patient = next(p for p in patients if p[''].strip() == label)
        expected_name = base.normalize_name(' '.join(patient.get(k, '') for k in ('FNAME', 'MNAME', 'LNAME')))
        if not expected_name or any(base.normalize_name(r['metadata']['PatientName']) != expected_name for r in records):
            state['blockers'].add('crosswalk_identity_unverified')
        identities.append({'label': label, 'fields': classifications})
        for r in records:
            meta = r['metadata']
            if meta['PatientID']:
                patient_ids[(meta['IssuerOfPatientID'], meta['PatientID'])].add(label)
            study_owners[r['study']].add(label)
    for labels in patient_ids.values():
        if len(labels) > 1: hold(labels, 'identity_shared_between_labels')
    for labels in study_owners.values():
        if len(labels) > 1: hold(labels, 'study_shared_between_labels')
    for uid, labels in study_owners.items():
        records = [r for r in objects.values() if r['study'] == uid and 'metadata' in r]
        for tag in ('AccessionNumber', 'StudyDate'):
            if len({r['metadata'][tag] for r in records}) > 1:
                hold(labels, 'study_metadata_conflict')
    # Keep all non-comparison exceptions as explicit holds. Repeated candidate
    # comparisons are not patient-level blockers by themselves.
    doc_labels = defaultdict(set)
    txt_diagnostics = []
    for doc in manifest['documents']:
        if doc['patient_key'] in by_key: doc_labels[doc['docid']].add(by_key[doc['patient_key']])
        if not doc.get('sha256') or not doc.get('text_location'):
            diagnostic = {'docid': doc['docid'], 'missing_location': not bool(doc.get('text_location')),
                          'source_probes': doc.get('source_probes', []), 'index_candidates': []}
            for candidate in doc.get('index_candidates', []):
                probe = dict(candidate)
                try:
                    path = base.source_checked(candidate['path'], roots, output, mounted)
                    with path.open('rb') as stream: stream.read(1)
                    probe['current_access'] = 'readable'
                    probe['in_saved_scan_scope'] = base.in_scan_scope(path, config, {'scan_folders': manifest.get('scan_scope') or 'null'})
                except Exception as error: probe['current_access'] = base.error_code(error)
                diagnostic['index_candidates'].append(probe)
            txt_diagnostics.append(diagnostic)
        if doc.get('sha256') and doc.get('resolved_path'):
            try:
                path = base.source_checked(doc['resolved_path'], roots, output, mounted)
                if fingerprint(path) != doc['sha256']:
                    raise base.ReconciliationError('report_changed')
            except Exception:
                hold(doc_labels[doc['docid']], 'txt_changed_or_unreadable')
    reassessed = {'association_needs_review', 'duplicate_sop_uid', 'inconsistent_patient_identity_across_studies'}
    diagnostics = []
    for issue in manifest['exceptions']:
        if issue['code'] in reassessed: continue
        labels = set()
        if issue.get('label') in states: labels.add(issue['label'])
        for key in issue.get('patient_keys', []):
            if key in by_key: labels.add(by_key[key])
        for docid in [issue.get('docid'), *issue.get('documents', [])]: labels.update(doc_labels.get(docid, set()))
        if not labels: labels = set(states)  # Unscoped exceptions cannot be cleared.
        hold(labels, issue['code'])
        diagnostic = dict(issue)
        if issue['code'] == 'dicom_folder_unresolved' and issue.get('label') in folders:
            path = folders[issue['label']]
            diagnostic['current_folder_details'] = base.folder_diagnostic(path)
            try:
                with os.scandir(path): pass
                diagnostic['current_access'] = 'accessible_requires_new_reconciliation'
            except OSError as error: diagnostic['current_access'] = type(error).__name__
        if issue['code'] in ('dicom_header_unreadable', 'invalid_dicom'):
            try:
                path = base.source_checked(issue['path'], roots, output, mounted)
                with warnings.catch_warnings():
                    warnings.simplefilter('ignore')
                    ds = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=base.TAGS)
                diagnostic['current_header'] = 'readable_requires_new_reconciliation' if all(ds.get(t) for t in ('StudyInstanceUID', 'SeriesInstanceUID', 'SOPInstanceUID')) else 'missing_required_uids'
            except Exception as error: diagnostic['current_header'] = base.error_code(error)
        diagnostics.append(diagnostic)
    # Require every study and every mapped report to have an independently
    # supported link. Unrelated candidate mismatches do not need to disappear.
    supported = defaultdict(set)
    study_links = defaultdict(set)
    clearable = {'duplicate_sop_uid', 'inconsistent_patient_identity_across_studies',
                 'inconsistent_PatientName', 'inconsistent_PatientID',
                 'inconsistent_IssuerOfPatientID', 'inconsistent_PatientBirthDate'}
    for study in manifest['studies']:
        label = study['label']
        for link in study['associations']:
            conflicts = set(link['conflicts']) - clearable
            if link['patient_evidence'] and link['study_evidence'] and not conflicts and 'explicit_txt_location' not in link['missing']:
                supported[link['docid']].add(study['uid'])
                study_links[study['uid']].add(link['docid'])
    for doc in manifest['documents']:
        label = by_key.get(doc['patient_key'])
        if not label: continue
        if len(supported[doc['docid']]) != 1:
            states[label]['blockers'].add('report_association_unverified')
    for label, state in states.items():
        if any(not study_links[uid] for uid in state['studies']): state['blockers'].add('study_without_supported_report')
        blockers = state['blockers']
        if any('conflict' in b or 'shared' in b or 'overlapping' in b for b in blockers): status = 'conflicted'
        elif any(b.startswith(('dicom_', 'txt_', 'missing_', 'no_verified', 'identity_missing', 'instance_', 'folder_')) for b in blockers): status = 'incomplete'
        elif blockers: status = 'pending'
        else: status = 'verified_for_anonymization_review'
        state['status'] = status
        state['study_count'], state['instance_path_count'] = len(state.pop('studies')), len(state.pop('paths'))
        state['blockers'] = sorted(blockers)
    counts = Counter(s['status'] for s in states.values())
    for status in ('verified_for_anonymization_review', 'incomplete', 'conflicted', 'pending'): counts.setdefault(status, 0)
    counts.update(patients=len(states), unique_instance_paths=len(objects), unique_studies=len(study_owners), duplicate_uid_groups=len(duplicates))
    for category in ('identical_bytes', 'different_bytes_unresolved', 'unreadable_or_changed'):
        selected = [d for d in duplicates if d['classification'] == category]
        counts['duplicate_' + category + '_groups'] = len(selected)
        for field in ('labels', 'studies', 'paths'):
            counts['duplicate_' + category + '_unique_' + field] = len({v for d in selected for v in d[field]})
    counts['duplicate_cross_patient_groups'] = sum(d['cross_patient'] for d in duplicates)
    counts['duplicate_cross_study_groups'] = sum(d['cross_study'] for d in duplicates)
    for category in ('conflicting_populated_values', 'missing_values', 'invalid_value', 'formatting_only'):
        labels = {r['label'] for r in identities if category in r['fields'].values()}
        affected = [r for r in objects.values() if r['owners'] & labels]
        counts['identity_' + category + '_unique_patients'] = len(labels)
        counts['identity_' + category + '_unique_studies'] = len({r['study'] for r in affected})
        counts['identity_' + category + '_unique_instance_paths'] = len(affected)
    return {'counts': dict(counts), 'patients': list(states.values()), 'duplicates': duplicates,
            'identity': identities, 'diagnostics': diagnostics, 'header_rechecks': details,
            'txt_diagnostics': txt_diagnostics,
            'accepted_associations': 0, 'release_allowed': False, 'pixel_validation': 'not_performed'}


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
        private_root = (Path(config['state_dir']) / 'batch-reconciliation').resolve()
        if private_root not in path.resolve().parents or any(p.is_symlink() for p in (path, *path.parents)):
            raise base.ReconciliationError('private_manifest_required')
        raw = path.read_bytes()
        result = analyze(json.loads(raw), config, base.mounts(Path('/proc/self/mountinfo').read_text()))
        result['input_manifest_sha256'] = hashlib.sha256(raw).hexdigest()
        run = base.private_run_directory(config)
        with (run / 'readiness.private.json').open('x', encoding='utf-8') as stream:
            json.dump(result, stream, indent=2)
        print(json.dumps({'counts': result['counts'], 'accepted_associations': 0, 'release_allowed': False}))
        return 0
    except Exception:
        print(json.dumps({'analysis_completed': False, 'release_allowed': False}))
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
