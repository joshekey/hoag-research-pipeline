"""Read-only technical-hold investigation; writes only private diagnostic output."""
import argparse
from collections import defaultdict
import hashlib
import json
import logging
import os
from pathlib import Path
import warnings

import pydicom
import batch_reconcile as base
from batch_readiness import fingerprint


REASONS = {
    'missing_identity_evidence': ('identity', 'Identify which required field is missing; obtain authoritative evidence. Missing issuer alone is a strict-policy hold, not proof of wrong identity.'),
    'cross_patient_identifier_collision': ('identity', 'Resolve issuing namespace and authoritative patient ownership; never assume shared identifiers are safe.'),
    'cross_patient_ownership_conflict': ('identity', 'Resolve cross-patient study UID or source hash ownership with authoritative evidence.'),
    'same_hash_has_unresolved_scope': ('identity', 'Investigate every ownership scope for this hash; do not clear one occurrence independently.'),
    'missing_source_hash': ('source_access_or_change', 'Obtain a verified source inventory before authorization.'),
    'overlay_review': ('pixel_overlay', 'Inspect overlays and all frames; overlay presence alone does not prove identifying text.'),
    'burned_in_or_unknown': ('pixel_overlay', 'Separate YES, missing and other values; absence of NO is not proof of burned-in identifiers. Existing hash-bound all-frame review may support resolution, but no automatic clearance.'),
    'invalid_age': ('metadata', 'Verify the source PatientAge and obtain an authorized correction or explicit profile decision; never infer or replace it here.'),
    'duplicate_uid_requires_resolution': ('duplicate_object', 'Compare content, metadata, study membership and existing pixel findings; equal pixels alone do not clear differing metadata.'),
    'source_unverified': ('source_access_or_change', 'Recheck source access and inventory provenance. Generic historical errors may not retain enough evidence to identify the original cause.'),
}


def unique_counts(records):
    return {'unique_patients': len({r['label'] for r in records if r.get('label')}),
            'unique_studies': len({r['study_uid'] for r in records if r.get('study_uid')}),
            'unique_instance_paths': len({r['path'] for r in records if r.get('path')})}


def inspect_record(record, config, mounted, verify_hashes):
    result = {'label':record.get('label'), 'study_uid':record.get('study_uid'),
              'path':record['path'], 'original_holds':record['holds'],
              'source_hash_check':'not_performed', 'pixel_decode':'not_performed'}
    try:
        path = base.source_checked(record['path'],config['source_roots'],Path(config['output_root']).resolve(),mounted)
        before = path.stat()
        if verify_hashes:
            if not record.get('sha256'):
                result['source_hash_check'] = 'no_plan_hash'
            else:
                result['source_hash_check'] = 'matches_plan' if fingerprint(path)==record['sha256'] else 'differs_from_plan'
        with warnings.catch_warnings():
            warnings.simplefilter('ignore')
            ds = pydicom.dcmread(path,stop_before_pixels=True)
        after = path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
            raise base.ReconciliationError('changed_during_diagnostic')
        bia = str(ds.get('BurnedInAnnotation','')).strip().upper()
        result['burned_in_attribute'] = {'YES':'explicit_yes','NO':'explicit_no','':'missing_or_empty'}.get(bia,'other_value')
        result['overlay_tags'] = sorted({str(e.tag) for e in ds.iterall() if 0x6000<=e.tag.group<=0x60ff})
        result['overlay_presence'] = 'present' if result['overlay_tags'] else 'absent'
        result['overlay_payload_present'] = any(0x6000<=e.tag.group<=0x60ff and e.tag.element==0x3000 for e in ds.iterall())
        result['overlay_bit_position_present'] = any(0x6000<=e.tag.group<=0x60ff and e.tag.element==0x0102 for e in ds.iterall())
        result['burned_in_normalization_changed'] = str(ds.get('BurnedInAnnotation','')).upper()!=bia
        result['missing_identity_fields'] = [tag for tag in ('PatientName','PatientID','IssuerOfPatientID','PatientBirthDate') if not str(ds.get(tag,'')).strip()]
        result['header_identity_differs_from_plan'] = any(str(ds.get(tag,''))!=value for tag,value in record.get('identity',{}).items())
        age = str(ds.get('PatientAge',''))
        result['age_format'] = 'missing' if not age else ('valid' if base.re.fullmatch(r'[0-9]{3}[DWMY]',age) else 'invalid')
        result['age_valid_after_whitespace_cleanup'] = bool(base.re.fullmatch(r'[0-9]{3}[DWMY]',age.strip()))
        result['membership'] = 'matches_plan' if (str(ds.get('StudyInstanceUID',''))==record.get('study_uid')
            and str(ds.get('SOPInstanceUID',''))==record.get('sop_uid')) else 'differs_or_missing'
        result['status'] = 'header_readable_now'
    except Exception as error:
        result['status'] = 'header_unverified_now'
        result['error_type'] = base.error_code(error) if isinstance(error,base.ReconciliationError) else type(error).__name__
    return result


def diagnose(plan, config=None, mounted=None, inspect_headers=False, verify_hashes=False, emit=lambda value:None):
    if plan.get('release_allowed') is not False or not isinstance(plan.get('records'),list):
        raise ValueError('staging_plan_required')
    records = plan['records']
    by_reason, combinations, categories = defaultdict(list),defaultdict(list),defaultdict(list)
    for record in records:
        reasons = sorted(set(record['holds']))
        if reasons: combinations[tuple(reasons)].append(record)
        for reason in reasons:
            by_reason[reason].append(record)
            categories[REASONS.get(reason,('unclassified',''))[0]].append(record)
    breakdown = {reason:{**unique_counts(rows),'record_occurrences':len(rows),
        'category':REASONS.get(reason,('unclassified',''))[0]} for reason,rows in sorted(by_reason.items())}
    # Identity variations are reported separately: they were NOT included in the
    # technical hold total. No identity is accepted or rejected by this diagnostic.
    identity = []
    for tag in ('PatientName','PatientID','IssuerOfPatientID','PatientBirthDate'):
        for scope in ('within_study','across_patient_studies'):
            groups = defaultdict(list)
            for record in records:
                if 'identity' in record:
                    groups[(record['label'], record['study_uid'] if scope=='within_study' else '')].append(record)
            for key, rows in groups.items():
                if scope=='across_patient_studies' and len({r['study_uid'] for r in rows})<2: continue
                values = {r['identity'].get(tag,'') for r in rows}
                populated = {v for v in values if v.strip()}
                for kind, applies in [('populated_variation',len(populated)>1),('missing_value',any(not v.strip() for v in values))]:
                    if applies: identity.append({'attribute':tag,'scope':scope,'kind':kind,'records':rows})
    identity_counts = []
    for key in sorted({(r['attribute'],r['scope'],r['kind']) for r in identity}):
        rows = [record for finding in identity if (finding['attribute'],finding['scope'],finding['kind'])==key for record in finding['records']]
        identity_counts.append(dict(attribute=key[0],scope=key[1],kind=key[2],**unique_counts(rows)))
    held = [r for r in records if r['holds']]
    clear = [r for r in records if not r['holds']]
    summary = {'cohort_patients':len(plan['patients']), 'inventory_record_occurrences':len(records),
        'technical_hold_record_occurrences':len(held), 'technical_hold_unique':unique_counts(held),
        'no_technical_hold_unique':unique_counts(clear), 'all_inventory_unique':unique_counts(records),
        'identity_review_required_unique':unique_counts(records),
        'prior_reported_hold_count':plan.get('counts',{}).get('instances_with_technical_holds'),
        'report_count_required':0,'release_allowed':False}
    inspections = []
    if inspect_headers:
        seen = set()
        for record in held:
            if record['path'] in seen: continue
            seen.add(record['path'])
            inspections.append(inspect_record(record,config,mounted,verify_hashes))
            if len(inspections)%1000==0: emit({'phase':'header_diagnostics','unique_paths_checked':len(inspections)})
    inspection_counts = {}
    for field in ('status','burned_in_attribute','overlay_presence','age_format','source_hash_check','membership','error_type'):
        groups=defaultdict(list)
        for record in inspections:
            if field in record: groups[record[field]].append(record)
        inspection_counts[field]={value:unique_counts(rows) for value,rows in sorted(groups.items())}
    exceptions = defaultdict(list)
    for item in plan.get('exceptions',[]):
        exceptions[item['code']].append({'label':item.get('label'),'study_uid':item.get('uid'),'path':item.get('path') or item.get('mapped_linux_path')})
    errors=defaultdict(list)
    for record in held:
        if 'source_unverified' in record['holds']: errors[record.get('error_type','not_recorded')].append(record)
    return {'summary':summary,'exact_reasons':breakdown,
        'persisted_source_error_types':{k:unique_counts(v) for k,v in sorted(errors.items())},
        'categories':{k:unique_counts(v) for k,v in sorted(categories.items())},
        'reason_combinations':[{'reasons':list(k),**unique_counts(v)} for k,v in sorted(combinations.items())],
        'identity_observations_separate_from_technical_holds':identity_counts,
        'inventory_exceptions':{k:{'finding_occurrences':len(v),**unique_counts(v)} for k,v in sorted(exceptions.items())},
        'current_header_diagnostics':inspection_counts,
        'resolution_guidance':{k:REASONS.get(k,('unclassified','Requires investigation; no automatic clearance.'))[1] for k in by_reason},
        'private_findings':{'held_records':held,'source_inspections':inspections,'inventory_exceptions':plan.get('exceptions',[])},
        'release_allowed':False,'holds_cleared':0}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='/etc/hoag-research/config.json')
    parser.add_argument('--plan',required=True)
    parser.add_argument('--inspect-headers',action='store_true')
    parser.add_argument('--verify-source-hashes',action='store_true')
    args=parser.parse_args()
    if args.verify_source_hashes and not args.inspect_headers: parser.error('--verify-source-hashes requires --inspect-headers')
    os.umask(0o077); logging.disable(logging.CRITICAL)
    try:
        config=json.loads(Path(args.config).read_text())
        path=Path(args.plan)
        root=(Path(config['state_dir'])/'batch-reconciliation').resolve()
        if root not in path.resolve().parents or any(p.is_symlink() for p in (path,*path.parents)): raise ValueError('private_path')
        if os.name!='nt' and (path.stat().st_uid!=os.geteuid() or path.stat().st_mode & 0o077): raise ValueError('private_permissions')
        raw=path.read_bytes()
        result=diagnose(json.loads(raw),config,base.mounts(Path('/proc/self/mountinfo').read_text()) if args.inspect_headers else {},
            args.inspect_headers,args.verify_source_hashes,lambda value:print(json.dumps(value),flush=True))
        result['input_plan_sha256']=hashlib.sha256(raw).hexdigest()
        run=base.private_run_directory(config)
        with (run/'hold-diagnostics.private.json').open('x',encoding='utf-8') as stream: json.dump(result,stream,indent=2)
        print(json.dumps({k:v for k,v in result.items() if k not in ('private_findings','input_plan_sha256','resolution_guidance')}))
        return 0
    except Exception:
        print(json.dumps({'diagnostic_completed':False,'release_allowed':False}))
        return 2


if __name__=='__main__': raise SystemExit(main())
