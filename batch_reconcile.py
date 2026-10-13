"""Hospital-local, read-only batch reconciliation. Never anonymizes or releases data."""
import argparse
from collections import Counter, defaultdict
from contextlib import closing
import csv
import hashlib
import json
import logging
import os
from pathlib import Path, PureWindowsPath
import re
import sqlite3
import stat
import uuid
import warnings
import xml.etree.ElementTree as ET
import zipfile

import pydicom
from strict_identity_match import normalize_name, normalize_date
from txt_candidate_discovery import clues, all_patient_names


class ReconciliationError(ValueError):
    pass


def error_code(error):
    return str(error) if isinstance(error, ReconciliationError) else type(error).__name__


TAGS = ['PatientName', 'PatientID', 'IssuerOfPatientID', 'PatientBirthDate',
        'PatientAge', 'StudyInstanceUID', 'SeriesInstanceUID', 'SOPInstanceUID',
        'SOPClassUID', 'AccessionNumber', 'StudyDate', 'Modality', 'BurnedInAnnotation']


def unc(value):
    """Preserve component boundaries and spaces; never fuzzy-map patient folders."""
    text = str(value).replace('\\', '/').rstrip('/')
    if not re.fullmatch(r'//[^/]+/[^/]+(?:/[^/]+)*', text):
        raise ReconciliationError('invalid_unc')
    parts = text[2:].split('/')
    if any(p in ('.', '..') or any(ord(c) < 32 for c in p) for p in parts):
        raise ReconciliationError('unsafe_unc')
    return parts


def unescape(value):
    return re.sub(r'\\([0-7]{3})', lambda m: chr(int(m[1], 8)), value)


def mounts(text):
    result = {}
    for line in text.splitlines():
        left, right = line.split(' - ', 1)
        fields, filesystem = left.split(), right.split()
        target, root = unescape(fields[4]), unescape(fields[3])
        if filesystem[0] not in ('cifs', 'smb3'):
            continue
        remote = unc(unescape(filesystem[1]))
        sub = root.strip('/').split('/') if root != '/' else []
        # Kernel versions expose a subdirectory in SOURCE, FSROOT, or both.
        if sub:
            if len(remote) == 2:
                remote += sub
            elif [x.casefold() for x in remote[2:]] != [x.casefold() for x in sub]:
                raise ReconciliationError('inconsistent_mount_subdirectory')
        result[target] = {'remote': remote, 'readonly': 'ro' in fields[5].split(',')}
    return result


def map_unc(value, mounted, roots):
    parts = unc(value)
    options = []
    for root in roots:
        mount = mounted.get(root)
        if mount and [x.casefold() for x in parts[:len(mount['remote'])]] == [x.casefold() for x in mount['remote']]:
            options.append((len(mount['remote']), Path(root).joinpath(*parts[len(mount['remote']):])))
    if not options:
        raise ReconciliationError('unc_not_under_configured_source_mount')
    best = max(n for n, _ in options)
    paths = {p for n, p in options if n == best}
    if len(paths) != 1:
        raise ReconciliationError('ambiguous_unc_mount')
    return paths.pop()


def contained(path, roots, output):
    path = Path(path)
    if '..' in path.parts or any(p.is_symlink() for p in (path, *path.parents)):
        raise ReconciliationError('symlink_or_traversal')
    resolved = path.resolve()
    if resolved == output or output in resolved.parents:
        raise ReconciliationError('export_directory_excluded')
    if not any(resolved == Path(r).resolve() or Path(r).resolve() in resolved.parents for r in roots):
        raise ReconciliationError('outside_source_scope')
    return path


def source_checked(path, roots, output, mounted):
    path = contained(path, roots, output)
    candidates = [r for r in roots if path == Path(r) or Path(r) in path.parents]
    root = max(candidates, key=len)
    if not mounted.get(root, {}).get('readonly'):
        raise ReconciliationError('source_mount_not_verified_readonly')
    remote = mounted[root]['remote'] + list(path.relative_to(root).parts)
    export = mounted.get(str(output), {}).get('remote')
    expected = unc('//vnwpradiant01/ozgur imaging/aaa_Anon_Studies')
    for blocked in (export, expected):
        if blocked and [p.casefold() for p in remote[:len(blocked)]] == [p.casefold() for p in blocked]:
            raise ReconciliationError('output_alias_excluded')
    return path


def folder_diagnostic(path):
    details = {'mapped_linux_path': str(path)}
    try:
        parent = path.parent
        while not parent.exists() and parent != parent.parent: parent = parent.parent
        details['nearest_existing_parent'] = str(parent)
        if parent == path.parent:
            details['case_only_candidates'] = [p.name for p in parent.iterdir() if p.name.casefold() == path.name.casefold()][:20]
    except OSError as error:
        details['parent_access_error'] = type(error).__name__
    return details


def in_scan_scope(path, config, settings):
    try:
        folders = json.loads(settings.get('scan_folders', 'null'))
        selected = [Path(r) for r in config['source_roots']] if folders is None else [
            Path(config['source_roots'][f['root']])/f['relative'] for f in folders]
        resolved = Path(path).resolve()
        return any(resolved == root.resolve() or root.resolve() in resolved.parents for root in selected)
    except (ValueError, TypeError, KeyError, IndexError):
        return None


def xlsx_rows(path):
    ns = {'s': 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
    with zipfile.ZipFile(path) as book:
        if sum(i.file_size for i in book.infolist()) > 100 * 1024 * 1024:
            raise ReconciliationError('workbook_too_large')
        strings = []
        if 'xl/sharedStrings.xml' in book.namelist():
            strings = [''.join(t.text or '' for t in n.findall('.//s:t', ns))
                       for n in ET.fromstring(book.read('xl/sharedStrings.xml')).findall('s:si', ns)]
        rows = []
        for row in ET.fromstring(book.read('xl/worksheets/sheet1.xml')).findall('s:sheetData/s:row', ns):
            cells = {}
            for cell in row.findall('s:c', ns):
                value = cell.findtext('s:v', default='', namespaces=ns)
                if cell.get('t') == 's': value = strings[int(value)]
                if cell.get('t') == 'inlineStr': value = ''.join(t.text or '' for t in cell.findall('.//s:t', ns))
                cells[re.sub('[0-9]', '', cell.attrib['r'])] = value
            if any(cells.values()): rows.append(cells)
    if not rows: raise ReconciliationError('empty_workbook')
    headers = rows[0]
    if not {'docid', 'PatientId', 'TextFileLocation'} <= set(headers.values()):
        raise ReconciliationError('document_columns_missing')
    return [{name: row.get(column, '') for column, name in headers.items()} for row in rows[1:]]


def association(meta, report, crosswalk_name):
    """Strict proposed links only: patient identity PLUS independent exam evidence."""
    expected = {'name': normalize_name(meta.get('PatientName')),
                'dob': normalize_date(meta.get('PatientBirthDate')),
                'exam_date': normalize_date(meta.get('StudyDate')),
                'study_uid': meta.get('StudyInstanceUID', ''),
                'accession': meta.get('AccessionNumber', '').strip().upper(),
                'patient_id': meta.get('PatientID', '').strip().upper()}
    hits, conflicts, missing = [], [], []
    for key, value in expected.items():
        actual = report.get(key, '')
        if not value or not actual: missing.append(key)
        elif value == actual: hits.append(key)
        else: conflicts.append(key)
    if not crosswalk_name or not expected['name']: missing.append('crosswalk_name')
    elif normalize_name(crosswalk_name) != expected['name']: conflicts.append('crosswalk_name')
    if report.get('name_conflict'): conflicts.append('multiple_report_names')
    patient = {'name', 'dob'} <= set(hits) and 'crosswalk_name' not in missing
    exam = 'study_uid' in hits or {'accession', 'exam_date'} <= set(hits)
    eligible = patient and exam and not conflicts
    return {'status': 'evidence_sufficient_pending_review' if eligible else 'review_required',
            'patient_evidence': patient, 'study_evidence': exam,
            'matches': hits, 'conflicts': conflicts, 'missing': missing,
            'accepted': False, 'release_allowed': False}


def report_evidence(text):
    info = clues(text)
    # Do not collapse punctuation in identifiers into an apparent exact match.
    from txt_candidate_discovery import PATIENT_ID, ACCESSION, capture
    info['patient_id'] = capture(PATIENT_ID, text[:20000]).strip().upper()
    info['accession'] = capture(ACCESSION, text[:20000]).strip().upper()
    names = all_patient_names(text)
    info['name_conflict'] = len(names) > 1
    if info['name_conflict']: info['name'] = ''
    return info


def reconcile(config, mapping_dir, mounted, path_maps=(), max_files=0):
    if max_files < 0:
        raise ReconciliationError('invalid_file_discovery_limit')
    issues, counts = [], Counter()
    def issue(code, **details):
        counts[code] += 1
        issues.append({'code': code, **details})
    mapping_hashes = {name:hashlib.sha256((Path(mapping_dir)/name).read_bytes()).hexdigest() for name in ('mrn_guid_mapping.csv','ScannedDocs.xlsx')}
    roots = config['source_roots']
    output = Path(config['output_root']).resolve()
    expected_output = unc('//vnwpradiant01/ozgur imaging/aaa_Anon_Studies')
    destination = mounted.get(config['output_root'])
    if not destination or destination['readonly'] or [p.casefold() for p in destination['remote']] != [p.casefold() for p in expected_output]:
        issue('output_mount_not_exact_intended_subdirectory')
    for root in roots:
        if root not in mounted or not mounted[root]['readonly']:
            issue('source_mount_not_verified_readonly', root=root)
    with (Path(mapping_dir) / 'mrn_guid_mapping.csv').open(encoding='utf-8-sig', newline='') as stream:
        patients = list(csv.DictReader(stream))
    if not patients or not {'PATIENTID','GUID','FNAME','LNAME','INPUTFOLDER',''} <= set(patients[0]):
        raise ReconciliationError('patient_columns_missing')
    documents = xlsx_rows(Path(mapping_dir) / 'ScannedDocs.xlsx')
    for key in ('PATIENTID', 'GUID', ''):
        values = [p[key].strip().casefold() for p in patients]
        if not all(values) or len(values) != len(set(values)):
            raise ReconciliationError('duplicate_or_missing_patient_mapping_key')
    if any(not re.fullmatch(r'P[0-9]+', p[''].strip()) for p in patients):
        raise ReconciliationError('invalid_anonymous_label')
    by_id = {p['PATIENTID'].strip(): p for p in patients}
    duplicate_docs = {k for k, n in Counter(d.get('docid') for d in documents).items() if n > 1}
    database_path = (Path(config['state_dir']) / 'workflow.sqlite').resolve()
    with closing(sqlite3.connect(database_path.as_uri() + '?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        db.execute('PRAGMA query_only=ON'); db.execute('BEGIN')
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():
            raise ReconciliationError('wait_for_active_application_job')
        settings = dict(db.execute('SELECT key,value FROM settings'))
        files = [dict(r) for r in db.execute("SELECT * FROM files WHERE kind='report'")]
    indexed = defaultdict(list)
    for row in files: indexed[PureWindowsPath(row['path']).name.casefold()].append(row)
    results, evidence_by_patient, used = [], defaultdict(list), defaultdict(list)
    for doc in documents:
        record = {'docid': doc['docid'], 'patient_key': doc['PatientId'],
                  'text_location': doc.get('TextFileLocation', ''), 'accepted': False}
        results.append(record)
        if not doc['docid'].strip():
            issue('missing_document_id', **record); continue
        if doc['PatientId'] not in by_id:
            issue('unmapped_document_patient', **record); continue
        if doc['docid'] in duplicate_docs:
            issue('duplicate_document_id', **record); continue
        location = doc.get('TextFileLocation', '').strip()
        if not location: issue('missing_txt_location', **record)
        filename = PureWindowsPath(location).name if location else doc['docid'] + '.txt'
        matches = indexed.get(filename.casefold(), [])
        active = [r for r in matches if r['active'] and r['status'] == 'ok']
        record['index_candidates'] = [{'path': r['path'], 'active': r['active'], 'status': r['status']} for r in matches]
        if len(active) != 1:
            issue('ambiguous_index_filename' if len(active) > 1 else 'txt_missing_from_active_index', **record)
            # Explicit administrator-supplied prefix translations only. No drive guessing.
            if matches: issue('txt_index_entry_inactive_or_error', docid=doc['docid'])
            probes = []
            for mapping in path_maps:
                source = PureWindowsPath(location)
                try: relative = source.relative_to(PureWindowsPath(mapping['windows_prefix']))
                except ValueError: continue
                probe = Path(mapping['linux_prefix']).joinpath(*relative.parts)
                try:
                    source_checked(probe, roots, output, mounted)
                    with probe.open('rb') as stream: stream.read(1)
                    status = 'readable_but_not_active_indexed'
                except Exception as error: status = type(error).__name__
                probes.append({'path': str(probe), 'status': status,
                               'in_saved_scan_scope': in_scan_scope(probe,config,settings)})
            record['source_probes'] = probes
            if location and not probes: issue('txt_windows_prefix_mapping_needed', docid=doc['docid'])
            continue
        row = active[0]; record['resolved_path'] = row['path']
        used[row['path']].append(record)
        counts['txt_resolved_once'] += 1
        counts['mapped_txt_resolved_once' if location else 'missing_location_docid_candidates'] += 1
        if not location:
            issue('missing_location_docid_candidate_requires_review', docid=doc['docid'], path=row['path'])
        try:
            if row['root'] not in roots or not mounted.get(row['root'], {}).get('readonly'):
                raise ReconciliationError('report_source_mount_not_readonly')
            path = source_checked(row['path'], roots, output, mounted)
            before = path.stat()
            if before.st_size > config['max_report_bytes']: raise ReconciliationError('report_too_large')
            raw = path.read_bytes(); after = path.stat()
            digest = hashlib.sha256(raw).hexdigest()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns) or digest != json.loads(row['metadata']).get('hash'):
                raise ReconciliationError('report_changed_since_index')
            info = report_evidence(raw.decode(config['report_encoding'], errors='strict'))
            record['sha256'] = digest
            # Report narratives are never written into the manifest or logs.
            evidence_by_patient[doc['PatientId']].append((record, info))
        except Exception as error:
            issue('txt_source_validation_failed', docid=doc['docid'], error_type=error_code(error))
    for path, records in used.items():
        if len(records) > 1:
            issue('duplicate_report_source', path=path, documents=[r['docid'] for r in records])
            for record in records: record['duplicate_source'] = True
    content_groups = defaultdict(list)
    for record in results:
        if record.get('sha256'): content_groups[record['sha256']].append(record)
    for digest, records in content_groups.items():
        if len(records) > 1:
            issue('duplicate_report_content', documents=[r['docid'] for r in records])
            for record in records: record['duplicate_source'] = True

    studies, sop_owners = {}, defaultdict(list)
    walked = 0
    for patient in patients:
        label, key = patient[''].strip(), patient['PATIENTID'].strip()
        folder = None
        try:
            folder = map_unc(patient['INPUTFOLDER'], mounted, roots)
            source_checked(folder, roots, output, mounted)
            source_root = next(r for r in roots if folder == Path(r) or Path(r) in folder.parents)
            if not mounted[source_root]['readonly']: raise ReconciliationError('dicom_mount_not_readonly')
            with os.scandir(folder): pass
        except Exception as error:
            details = folder_diagnostic(folder) if folder is not None else {}
            issue('dicom_folder_unresolved', label=label, input_folder=patient['INPUTFOLDER'], error_type=error_code(error), **details)
            continue
        counts['dicom_folders_found'] += 1
        def walk_error(error):
            issue('dicom_directory_access_failed', label=label, path=error.filename, error_type=error_code(error))
        for directory, dirs, names in os.walk(folder, followlinks=False, onerror=walk_error):
            kept = []
            for name in dirs:
                child = Path(directory)/name
                try:
                    source_checked(child, roots, output, mounted)
                    # Exclude exports even when visible through the read-only imaging mount.
                    remote = mounted[source_root]['remote'] + list(child.relative_to(source_root).parts)
                    if [x.casefold() for x in remote[:len(expected_output)]] == [x.casefold() for x in expected_output]:
                        raise ReconciliationError('output_alias_excluded')
                    kept.append(name)
                except ValueError: issue('excluded_source_directory', label=label, path=str(child))
            dirs[:] = kept
            for name in names:
                walked += 1
                if max_files and walked > max_files: raise ReconciliationError('file_discovery_limit_reached')
                path = Path(directory)/name
                try:
                    source_checked(path, roots, output, mounted)
                    before = path.stat()
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        ds = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=TAGS)
                    after = path.stat()
                    if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns): raise ReconciliationError('dicom_changed_during_read')
                    meta = {tag: str(ds.get(tag, '')) for tag in TAGS}
                    if not all(meta[tag] for tag in ('StudyInstanceUID','SeriesInstanceUID','SOPInstanceUID')):
                        raise ReconciliationError('missing_dicom_identifiers')
                except pydicom.errors.InvalidDicomError:
                    if path.suffix.lower() == '.dcm': issue('invalid_dicom', label=label, path=str(path))
                    else: counts['non_dicom_files_skipped'] += 1
                    continue
                except Exception as error:
                    issue('dicom_header_unreadable', label=label, path=str(path), error_type=error_code(error)); continue
                uid = meta['StudyInstanceUID']
                study = studies.setdefault(uid, {'uid':uid, 'patient_key':key, 'label':label, 'metadata':meta,
                                                'instances':[], 'conflicts':[], 'associations':[], 'release_allowed':False})
                if study['patient_key'] != key: study['conflicts'].append('study_uid_in_multiple_patients')
                for tag in ('PatientName','PatientID','IssuerOfPatientID','PatientBirthDate','AccessionNumber','StudyDate'):
                    if study['metadata'][tag] != meta[tag]: study['conflicts'].append('inconsistent_'+tag)
                study['instances'].append({'path':str(path),'size':before.st_size,'mtime_ns':before.st_mtime_ns,
                                           'sop_uid':meta['SOPInstanceUID'],'series_uid':meta['SeriesInstanceUID'],
                                           'pixel_validation':'not_performed','metadata_validation':'header_only'})
                sop_owners[meta['SOPInstanceUID']].append(uid)
    for sop, owners in sop_owners.items():
        if len(owners) > 1:
            issue('duplicate_sop_uid', sop_uid=sop)
            for uid in owners: studies[uid]['conflicts'].append('duplicate_sop_uid')
    grouped = defaultdict(list)
    identities = defaultdict(set)
    for study in studies.values():
        grouped[study['patient_key']].append(study)
        meta = study['metadata']
        if meta['PatientID']:
            identities[(meta['IssuerOfPatientID'],meta['PatientID'])].add(study['patient_key'])
    for key, group in grouped.items():
        identity_values = {tuple(s['metadata'][tag] for tag in
                           ('PatientName','PatientID','IssuerOfPatientID','PatientBirthDate')) for s in group}
        if len(identity_values)>1:
            issue('inconsistent_patient_identity_across_studies', label=group[0]['label'])
            for study in group: study['conflicts'].append('inconsistent_patient_identity_across_studies')
    for keys in identities.values():
        if len(keys)>1:
            issue('dicom_patient_identity_in_multiple_crosswalk_patients', patient_keys=sorted(keys))
            for key in keys:
                for study in grouped[key]: study['conflicts'].append('patient_identity_multiple_labels')
    proposed = defaultdict(list)
    for study in studies.values():
        patient = by_id[study['patient_key']]
        name = ' '.join(patient.get(k, '') for k in ('FNAME','MNAME','LNAME'))
        for record, info in evidence_by_patient[study['patient_key']]:
            comparison = association(study['metadata'], info, name)
            if study['conflicts'] or record.get('duplicate_source') or not record['text_location']:
                comparison['status'] = 'review_required'
                comparison['conflicts'] += list(set(study['conflicts']))
                if record.get('duplicate_source'): comparison['conflicts'].append('duplicate_report_source')
                if not record['text_location']: comparison['missing'].append('explicit_txt_location')
            comparison['docid'] = record['docid']
            study['associations'].append(comparison)
            if comparison['status'] == 'evidence_sufficient_pending_review': proposed[record['docid']].append(comparison)
        if not study['associations']: issue('study_without_readable_mapped_report', label=study['label'], uid=study['uid'])
    for docid, candidates in proposed.items():
        if len(candidates) > 1:
            issue('report_matches_multiple_studies', docid=docid)
            for candidate in candidates:
                candidate['status'] = 'review_required'; candidate['conflicts'].append('multiple_studies')
    for study in studies.values():
        for comparison in study['associations']:
            counts[comparison['status']] += 1
            if comparison['status']=='review_required':
                issue('association_needs_review', label=study['label'], uid=study['uid'], **comparison)
    counts.update(patients=len(patients), documents=len(documents), dicom_studies=len(studies),
                  dicom_instances=sum(len(s['instances']) for s in studies.values()), accepted_associations=0)
    if any(hashlib.sha256((Path(mapping_dir)/name).read_bytes()).hexdigest()!=value for name,value in mapping_hashes.items()):
        raise ReconciliationError('crosswalk_changed_during_run')
    return {'mapping_sha256':mapping_hashes, 'mode':'dry_run_only', 'release_allowed':False, 'counts':dict(counts),
            'private_crosswalk':patients, 'documents':results, 'studies':list(studies.values()),
            'exceptions':issues, 'scan_scope':settings.get('scan_folders'), 'mounts':mounted,
            'gates':{'reconciliation_review':False,'anonymization_plan_review':False,
                     'pixel_and_metadata_validation':False,'redacted_report_review':False,'release_authorization':False}}


def private_run_directory(config):
    root = Path(config['state_dir']) / 'batch-reconciliation'
    if any(p.is_symlink() for p in (root, *root.parents)): raise ReconciliationError('unsafe_private_report_directory')
    if root.exists() and os.name != 'nt' and (root.stat().st_uid != os.geteuid() or stat.S_IMODE(root.stat().st_mode) != 0o700):
        raise ReconciliationError('private_report_directory_permissions')
    if any(root.resolve() == Path(p).resolve() or Path(p).resolve() in root.resolve().parents
           for p in [*config['source_roots'],config['output_root']]):
        raise ReconciliationError('private_reports_must_not_be_in_delivery_or_source')
    root.mkdir(mode=0o700, exist_ok=True)
    run = root / ('run-' + uuid.uuid4().hex)
    run.mkdir(mode=0o700)
    return run


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/hoag-research/config.json')
    parser.add_argument('--crosswalk-dir', default='/home/sratai/crosswalk')
    parser.add_argument('--path-map', help='Private JSON list of explicit windows_prefix/linux_prefix translations')
    parser.add_argument('--max-files', type=int, default=0,
                        help='Optional discovery limit; 0 means unlimited (default)')
    args = parser.parse_args()
    if args.max_files < 0:
        parser.error('--max-files must be zero (unlimited) or positive')
    os.umask(0o077)
    logging.disable(logging.CRITICAL)
    try:
        config = json.loads(Path(args.config).read_text())
        mapping = json.loads(Path(args.path_map).read_text()) if args.path_map else []
        mounted = mounts(Path('/proc/self/mountinfo').read_text())
        run = private_run_directory(config)
        try:
            result = reconcile(config, args.crosswalk_dir, mounted, mapping, args.max_files)
        except Exception as error:
            result = {'mode':'incomplete_dry_run', 'release_allowed':False,
                      'error_type':error_code(error), 'counts':{'completed':0},
                      'exceptions':[{'code':'incomplete_run','error_type':error_code(error)}]}
        for filename, value in [('manifest.private.json',result),
                                ('exceptions.private.json',result.get('exceptions', []))]:
            with (run/filename).open('x',encoding='utf-8') as stream: json.dump(value,stream,indent=2)
        print(json.dumps({'mode':result['mode'],'counts':result['counts'],
                          'run_id':run.name,'release_allowed':False}))
        return 0 if result['mode']=='dry_run_only' else 2
    except Exception:
        print('Reconciliation could not start. Check local configuration, mappings and private-directory permissions. No export attempted.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
