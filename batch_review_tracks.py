"""Technical-only diagnostics and standard evidence work batches; no approvals."""
import argparse
from collections import defaultdict
import copy
import csv
import json
import os
from pathlib import Path

import batch_review_queue as queue
import batch_hold_diagnostics as diagnostic
import batch_dicom_stage as stage
import batch_reconcile as base


def prepare_tracks(plan, config=None, mounted=None, inspect_headers=False, verify_hashes=False, emit=lambda x:None):
    packet=queue.build_queue(plan)
    technical=[g for g in packet['groups'] if g['lane']=='technical_holds_only']
    standard=[g for g in packet['groups'] if g['lane']=='eligible_standard_review']
    records=[]
    for group in technical:
        for entry in group['blocked']:
            record=copy.deepcopy(plan['records'][entry['index']])
            record['holds']=entry['reasons']  # Include group-review derived holds.
            records.append(record)
    subset=dict(plan,records=records,counts={},exceptions=[])
    result=diagnostic.diagnose(subset,config,mounted,inspect_headers,verify_hashes,emit)
    observations=defaultdict(list)
    for r in records:
        missing=[k for k in ('PatientName','PatientID','IssuerOfPatientID','PatientBirthDate') if not r.get('identity',{}).get(k,'').strip()]
        if missing==['IssuerOfPatientID']: observations['issuer_only_missing_strict_policy_hold'].append(r)
        if len(r['holds'])==1: observations['sole_hold_'+r['holds'][0]].append(r)
    for r in result['private_findings']['source_inspections']:
        if r.get('burned_in_attribute')=='missing_or_empty': observations['annotation_flag_missing_not_proven_burned_in'].append(r)
        if r.get('burned_in_attribute')=='explicit_no' and 'burned_in_or_unknown' in r['original_holds']:
            observations['annotation_hold_disagrees_with_current_header'].append(r)
        if r.get('overlay_presence')=='present' and not r.get('overlay_payload_present') and not r.get('overlay_bit_position_present'):
            observations['overlay_metadata_without_detected_payload_or_bit_position'].append(r)
        if r.get('burned_in_normalization_changed'): observations['annotation_whitespace_rule_candidate'].append(r)
        if r.get('age_format')=='invalid' and r.get('age_valid_after_whitespace_cleanup'):
            observations['age_whitespace_rule_candidate'].append(r)
        if r.get('header_identity_differs_from_plan'): observations['identity_header_differs_from_inventory'].append(r)
    work=[]
    for index,g in enumerate(standard):
        work.append({'batch':index//10+1,'group_id':g['group_id'],'label':g['label'],
            'partial_study':g['partial_study'],'eligible_instances':len(g['eligible_indices']),
            'held_instances':len(g['blocked']),'identity_status':'not_reviewed',
            'profile_status':'not_reviewed','all_frames_status':'not_reviewed',
            'reviewer':'','identity_evidence_reference':'','profile_evidence_reference':'','pixel_evidence_reference':'',
            'eligible_source_objects':[plan['records'][i] for i in g['eligible_indices']],
            'blocked_objects':g['blocked']})
    return {'plan_sha256':stage.digest(plan),'technical_diagnostic':result,
        'policy_observations':{k:diagnostic.unique_counts(v) for k,v in sorted(observations.items())},
        'private_policy_findings':dict(observations),'standard_review_worklist':work,
        'summary':{'cohort_patients':len(plan['patients']),'technical_only_studies':len(technical),
            'technical_only_instance_records':len(records),'standard_review_studies':len(standard),
            'standard_review_batches':(len(standard)+9)//10,
            'standard_eligible_instances':sum(len(g['eligible_indices']) for g in standard),
            'standard_held_instances':sum(len(g['blocked']) for g in standard),
            'identity_variation_studies_kept_separate':sum(g['lane']=='identity_variation_review' for g in packet['groups']),
            'holds_cleared':0,'authorizations_created':0,'release_allowed':False},'release_allowed':False}


def public_result(result):
    return {'summary':result['summary'],'exact_technical_reasons':result['technical_diagnostic']['exact_reasons'],
        'reason_combinations':result['technical_diagnostic']['reason_combinations'],
        'persisted_source_error_types':result['technical_diagnostic']['persisted_source_error_types'],
        'current_headers':result['technical_diagnostic']['current_header_diagnostics'],
        'policy_observations_not_confirmed_defects':result['policy_observations']}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='/etc/hoag-research/config.json')
    parser.add_argument('--run')
    parser.add_argument('--inspect-headers',action='store_true')
    parser.add_argument('--verify-source-hashes',action='store_true')
    args=parser.parse_args()
    if args.verify_source_hashes and not args.inspect_headers: parser.error('Hash verification requires header inspection')
    os.umask(0o077)
    try:
        config=json.loads(Path(args.config).read_text())
        root=(Path(config['state_dir'])/'batch-reconciliation').resolve()
        path=Path(args.run)/'bundle-plan.private.json' if args.run else max(root.glob('run-*/bundle-plan.private.json'),key=lambda p:p.stat().st_mtime_ns)
        if root not in path.resolve().parents or any(p.is_symlink() for p in (path,*path.parents)): raise ValueError('private_path')
        if os.name!='nt' and any(p.stat().st_uid!=os.geteuid() or p.stat().st_mode & 0o077 for p in (path,path.parent)): raise ValueError('private_permissions')
        bundle=json.loads(path.read_text())
        mounted=base.mounts(Path('/proc/self/mountinfo').read_text()) if args.inspect_headers else {}
        result=prepare_tracks(bundle['stage_plan'],config,mounted,args.inspect_headers,args.verify_source_hashes,
            lambda x:print(json.dumps(x),flush=True))
        result['source_bundle_run']=str(path.parent)
        run=base.private_run_directory(config)
        stage.write_json(run/'review-tracks.private.json',result)
        with (run/'standard-review-batches.private.csv').open('x',encoding='utf-8',newline='') as stream:
            fields=['batch','group_id','label','partial_study','eligible_instances','held_instances','identity_status',
                    'profile_status','all_frames_status','reviewer','identity_evidence_reference','profile_evidence_reference','pixel_evidence_reference']
            writer=csv.DictWriter(stream,fieldnames=fields,extrasaction='ignore'); writer.writeheader()
            writer.writerows(result['standard_review_worklist'])
        print(json.dumps(public_result(result)))
        return 0
    except Exception:
        print(json.dumps({'diagnostic_completed':False,'release_allowed':False}))
        return 2


if __name__=='__main__': raise SystemExit(main())
