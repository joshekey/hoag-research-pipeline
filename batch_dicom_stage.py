"""Restricted, resumable DICOM staging. No export or release command exists."""
import argparse
from collections import Counter, defaultdict
import copy
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import warnings

import pydicom
import batch_reconcile as base
from batch_readiness import fingerprint
import batch_stage_validation
import engine

PROFILE = 'HOAG-1.50-STAGING-AGE-v1'
METADATA_MODE = 'metadata_only_pending_pixel_review'

def remaining_holds(record, metadata_only=False):
    return [h for h in record['holds'] if not (metadata_only and h == 'burned_in_or_unknown'
        and record.get('burned_in_annotation', '').strip().upper() != 'YES')]

def progress_counts(state, total):
    objects = list(state['objects'].values())
    held = [v for v in objects if v['status']=='held']
    return {'dicom_files_metadata_anonymized':sum(v['status'] in ('validated_staging_only',
                'metadata_anonymized_awaiting_pixel_review') for v in objects),
            'files_awaiting_pixel_review':sum(v['status']=='metadata_anonymized_awaiting_pixel_review' for v in objects),
            'files_held':sum(v['status']=='held' for v in objects),
            'hold_counts':dict(Counter(v['code'] for v in held)),
            'technical_reason_counts':dict(Counter(r for v in held for r in set(v.get('reasons',[])))),
            'files_held_for_identity_or_source':sum(v['code'] in ('identity_review_required',
                'source_changed','source_changed_during_processing') or 'source_unverified' in v.get('reasons',[]) for v in held),
            'files_not_processed':total-len(objects), 'release_allowed':False}



def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def write_json(path, value):
    if any(p.is_symlink() for p in (path,*path.parents,path.with_suffix('.partial'))): raise ValueError('symlink')
    temp = path.with_suffix('.partial')
    with temp.open('w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.flush(); os.fsync(stream.fileno())
    os.replace(temp, path)


def plan(manifest, config, mounted, emit=lambda value: None):
    if manifest.get('mode') != 'dry_run_only': raise ValueError('complete_manifest_required')
    patients = manifest['private_crosswalk']
    labels = [p[''].strip() for p in patients]
    if len(labels) != len(set(labels)) or any(not re.fullmatch('P[0-9]+', x) for x in labels): raise ValueError('labels')
    folders, exceptions = {}, []
    for patient in patients:
        label = patient[''].strip()
        path = None
        try:
            path = base.map_unc(patient['INPUTFOLDER'], mounted, config['source_roots'])
            base.source_checked(path, config['source_roots'], Path(config['output_root']).resolve(), mounted)
            with os.scandir(path): pass
            folders[label] = path
        except Exception as error:
            exceptions.append({'label':label, 'code':'folder_unresolved', 'error_type':base.error_code(error),
                **(base.folder_diagnostic(path) if path is not None else {})})
    records = []
    for study in manifest['studies']:
        for item in study['instances']:
            record = {'label': study['label'], 'study_uid': study['uid'], 'path':item['path'], 'holds':[]}
            records.append(record)
            try:
                path = base.source_checked(item['path'], config['source_roots'], Path(config['output_root']).resolve(), mounted)
                owners = [label for label, folder in folders.items() if folder in path.parents]
                if owners != [study['label']]: raise ValueError('ambiguous_folder_ownership')
                before = path.stat()
                if (before.st_size,before.st_mtime_ns) != (item['size'],item['mtime_ns']): raise ValueError('source_changed')
                record['sha256'] = fingerprint(path)
                ds = pydicom.dcmread(path, stop_before_pixels=True)
                after = path.stat()
                if (before.st_size,before.st_mtime_ns) != (after.st_size,after.st_mtime_ns): raise ValueError('source_changed')
                if str(ds.get('StudyInstanceUID','')) != study['uid'] or str(ds.get('SOPInstanceUID','')) != item['sop_uid']: raise ValueError('membership_changed')
                record['sop_uid'] = str(ds.SOPInstanceUID)
                record['identity'] = {k:str(ds.get(k,'')) for k in ('PatientName','PatientID','IssuerOfPatientID','PatientBirthDate')}
                if any(0x6000 <= e.tag.group <= 0x60ff for e in ds.iterall()): record['holds'].append('overlay_review')
                record['burned_in_annotation'] = str(ds.get('BurnedInAnnotation',''))
                if str(ds.get('BurnedInAnnotation','')).upper() != 'NO': record['holds'].append('burned_in_or_unknown')
                if str(ds.get('PatientAge','')) and not re.fullmatch(r'[0-9]{3}[DWMY]', str(ds.PatientAge)): record['holds'].append('invalid_age')
            except Exception as error:
                record['holds'].append('source_unverified')
                record['error_type'] = base.error_code(error) if isinstance(error, base.ReconciliationError) else type(error).__name__
        emit({'phase':'inventory', 'studies_checked':len({r['study_uid'] for r in records}), 'instance_paths_checked':len(records)})
    # Conflicting duplicate bytes cannot be approved by the general identity ledger.
    groups = defaultdict(list)
    for record in records:
        if record.get('sop_uid'): groups[record['sop_uid']].append(record)
    for group in groups.values():
        if len({(r['label'],r['study_uid'],r['sha256']) for r in group}) > 1:
            for r in group: r['holds'].append('duplicate_uid_requires_resolution')
    # Preserve all earlier DICOM inventory exceptions, including unreadable paths.
    exceptions.extend(e for e in manifest['exceptions'] if e['code'] in (
        'dicom_header_unreadable','invalid_dicom','dicom_directory_access_failed','excluded_source_directory'))
    counts = {'cohort_patients':len(labels), 'accessible_patient_folders':len(folders),
        'unresolved_patient_folders':len(labels)-len(folders), 'inventoried_instance_paths':len(records),
        'instances_with_technical_holds':sum(bool(r['holds']) for r in records),
        'instances_awaiting_authoritative_review':sum(not r['holds'] for r in records),
        'report_count_required':0, 'release_allowed':False}
    return {'profile':PROFILE, 'manifest_sha256':digest(manifest), 'patients':labels,
        'records':records,'exceptions':exceptions,'counts':counts,'release_allowed':False}


def stage(plan_data, reviews, config, mounted, run, emit=lambda value: None, pilot_limit=0):
    if pilot_limit < 0: raise ValueError('invalid_pilot_limit')
    metadata_only = reviews.get('staging_mode') == METADATA_MODE
    attempts = 0
    # The ledger is a hospital-local attestation, not an inferred MRN alias table.
    if (reviews.get('profile') != PROFILE or reviews.get('plan_sha256') != digest(plan_data)
        or not reviews.get('profile_reviewed_by')): raise ValueError('profile_review_required')
    state_path = run/'checkpoint.private.json'
    binding = digest([plan_data, reviews, hashlib.sha256(config['secret_key'].encode()).hexdigest()])
    state = json.loads(state_path.read_text()) if state_path.exists() else {'binding':binding,'objects':{},'release_allowed':False}
    if state['binding'] != binding: raise ValueError('resume_inputs_changed')
    lock = run/'.stage-lock'
    fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(fd)
    try:
        for number, label in enumerate(plan_data['patients'], 1):
            if any(p.is_symlink() for p in (run/label,run/label/'reports',run/label/'DICOM')): raise ValueError('symlink')
            (run/label/'reports').mkdir(parents=True, exist_ok=True, mode=0o700)
            for index, record in enumerate(plan_data['records']):
                if record['label'] != label: continue
                key = str(index)
                review = reviews.get('objects',{}).get(record.get('sha256',''),{})
                holds = remaining_holds(record, metadata_only)
                identity_ok = (review.get('label') == label and review.get('identity_verified_by')
                               and review.get('authority_reference'))
                pixels_ok = review.get('all_frames_reviewed_by') and review.get('pixel_identifiers_absent') is True
                if holds or not identity_ok or (not metadata_only and not pixels_ok):
                    code = 'technical_hold' if holds else ('identity_review_required' if not identity_ok else 'pixel_review_required')
                    state['objects'][key] = {'status':'held','code':code,'reasons':holds}
                    continue
                previous = state['objects'].get(key, {})
                if pilot_limit and attempts >= pilot_limit and previous.get('status') not in (
                        'validated_staging_only', 'metadata_anonymized_awaiting_pixel_review'):
                    continue
                try:
                    if previous.get('status') not in ('validated_staging_only', 'metadata_anonymized_awaiting_pixel_review'):
                        attempts += 1
                    path = base.source_checked(record['path'], config['source_roots'], Path(config['output_root']).resolve(), mounted)
                    if fingerprint(path) != record['sha256']: raise ValueError('source_changed')
                    source = pydicom.dcmread(path)
                    if str(source.get('BurnedInAnnotation','')).strip().upper() == 'YES':
                        raise ValueError('known_burned_in_identifiers')
                    if any(0x6000 <= e.tag.group <= 0x60ff for e in source.iterall()):
                        raise ValueError('overlay_review_required')
                    target = run/label/'DICOM'/('study-'+engine.research_uid(config, record['study_uid']))/(f'instance-{index+1:08d}.dcm')
                    if any(p.is_symlink() for p in (target,target.with_suffix('.partial'),*target.parents)): raise ValueError('symlink')
                    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                    if target.exists():
                        if state['objects'].get(key,{}).get('sha256') != fingerprint(target): raise ValueError('existing_output_unverified')
                        batch_stage_validation.validate(source, target, label, config)
                        if fingerprint(path) != record['sha256']: raise ValueError('source_changed_during_processing')
                        continue
                    try:
                        source.pixel_array  # Unsupported decoding is an object-level hold.
                    except Exception:
                        raise ValueError('unsupported_pixel_decoding') from None
                    ds = engine.anonymize_dataset(config, copy.deepcopy(source), label, [])
                    # Only age is retained from the patient-demographic group.
                    for dataset in [ds, *[item for e in ds.iterall() if e.VR=='SQ' for item in e.value]]:
                        for e in list(dataset):
                            if e.tag.group == 0x0010: del dataset[e.tag]
                    ds.PatientName = label; ds.PatientID = label
                    if 'PatientAge' in source: ds.PatientAge = source.PatientAge
                    ds.PatientIdentityRemoved = 'YES'
                    ds.DeidentificationMethod = PROFILE + ('; METADATA ONLY; PIXEL REVIEW PENDING' if metadata_only else '')
                    if metadata_only:
                        # Preserve unknown status; never manufacture a NO declaration.
                        if 'BurnedInAnnotation' in source: ds.BurnedInAnnotation = source.BurnedInAnnotation
                        elif 'BurnedInAnnotation' in ds: del ds.BurnedInAnnotation
                    ds.LongitudinalTemporalInformationModified = 'REMOVED'
                    temp = target.with_suffix('.partial')
                    ds.save_as(temp, enforce_file_format=True)
                    batch_stage_validation.validate(source, temp, label, config)
                    if fingerprint(path) != record['sha256']: raise ValueError('source_changed_during_processing')
                    os.replace(temp, target)
                    state['objects'][key] = {'status':('metadata_anonymized_awaiting_pixel_review' if metadata_only else 'validated_staging_only'), 'sha256':fingerprint(target)}
                except Exception as error:
                    allowed = {'source_changed','source_changed_during_processing','known_burned_in_identifiers',
                               'overlay_review_required','unsupported_pixel_decoding','existing_output_unverified'}
                    code = str(error) if type(error) is ValueError and str(error) in allowed else 'processing_or_validation_failed'
                    state['objects'][key] = {'status':'held','code':code}
                write_json(state_path, state)
                if (index+1) % 20 == 0:
                    emit({'phase':'restricted_staging','patient':label,'objects_checked':index+1,
                          **progress_counts(state,len(plan_data['records']))})
            write_json(state_path, state)
            emit({'phase':'restricted_staging','patient':label,'patient_number':number,
                'cohort_patients':len(plan_data['patients']), **progress_counts(state,len(plan_data['records']))})
        # Optional reports are independently attested and never gate DICOM work.
        state.setdefault('reports', {})
        for index, report in enumerate(reviews.get('reports', [])):
            key = str(index)
            try:
                label = report['label']
                if label not in plan_data['patients'] or not report.get('ownership_verified_by'): raise ValueError('ownership_review_required')
                path = base.source_checked(report['path'],config['source_roots'],Path(config['output_root']).resolve(),mounted)
                if path.stat().st_size > config['max_report_bytes']: raise ValueError('report_too_large')
                raw = path.read_bytes()
                if hashlib.sha256(raw).hexdigest() != report['sha256']: raise ValueError('report_changed')
                identifiers = {v for r in plan_data['records'] if r['label']==label for v in r.get('identity',{}).values() if v}
                clean = engine.sanitize_report(raw.decode(config['report_encoding'], errors='strict'),identifiers)
                target = run/label/'reports'/(f'report-{index+1:06d}.txt')
                if any(p.is_symlink() for p in (target,*target.parents)): raise ValueError('symlink')
                data = ('Patient: '+label+'\n'+clean).encode('utf-8')
                if target.exists():
                    if fingerprint(target) != state['reports'].get(key,{}).get('sha256'): raise ValueError('unverified_existing_report')
                else:
                    with target.open('xb') as stream: stream.write(data)
                state['reports'][key] = {'status':'redacted_staging_awaiting_independent_review','sha256':fingerprint(target)}
            except Exception as error:
                state['reports'][key] = {'status':'held','code':type(error).__name__}
            write_json(state_path,state)
    finally:
        lock.unlink()
    emit({'phase':'staging_summary', **progress_counts(state,len(plan_data['records']))})
    return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', default='/etc/hoag-research/config.json')
    parser.add_argument('--manifest')
    parser.add_argument('--resume-run')
    parser.add_argument('--reviews')
    args = parser.parse_args()
    os.umask(0o077); logging.disable(logging.CRITICAL)
    warnings.filterwarnings('ignore')
    try:
        config = json.loads(Path(args.config).read_text())
        mounted = base.mounts(Path('/proc/self/mountinfo').read_text())
        root = (Path(config['state_dir'])/'batch-reconciliation').resolve()
        def private(path):
            path = Path(path)
            if root not in path.resolve().parents or any(p.is_symlink() for p in (path,*path.parents)): raise ValueError('private_path_required')
            if os.name != 'nt' and (path.stat().st_uid != os.geteuid() or path.stat().st_mode & 0o077): raise ValueError('private_permissions')
            return path
        emit = lambda value: print(json.dumps(value), flush=True)
        if args.resume_run:
            run = private(args.resume_run)
            plan_data = json.loads(private(run/'stage-plan.private.json').read_text())
            reviews = json.loads(private(args.reviews).read_text())
            stage(plan_data, reviews, config, mounted, run, emit)
        else:
            source = private(args.manifest)
            data = plan(json.loads(source.read_text()), config, mounted, emit)
            run = base.private_run_directory(config)
            write_json(run/'stage-plan.private.json', data)
            write_json(run/'review-template.private.json', {'profile':PROFILE,'plan_sha256':digest(data),
                'profile_reviewed_by':'', 'objects':{}})
            emit(data['counts'])
        return 0
    except Exception:
        print(json.dumps({'completed':False,'release_allowed':False}))
        return 2


if __name__ == '__main__': raise SystemExit(main())
