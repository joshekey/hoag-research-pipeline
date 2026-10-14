"""Hospital-local group review and authorization; no automatic identity approval."""
import argparse
from collections import defaultdict, Counter
import csv
import hashlib
import json
import logging
import os
from pathlib import Path
import warnings

import batch_dicom_stage as stage
import batch_reconcile as base

FIELDS=('PatientName','PatientID','IssuerOfPatientID','PatientBirthDate')


def groups(plan, metadata_only=False):
    by_study=defaultdict(list); uid_owners=defaultdict(set); hash_owners=defaultdict(set); id_owners=defaultdict(set)
    for index,record in enumerate(plan['records']):
        by_study[(record['label'],record['study_uid'])].append((index,record))
        uid_owners[record['study_uid']].add(record['label'])
        if record.get('sha256'): hash_owners[record['sha256']].add(record['label'])
        identity=record.get('identity',{})
        if identity.get('PatientID'):
            id_owners[(identity.get('IssuerOfPatientID','').strip(),identity['PatientID'].strip())].add(record['label'])
    result=[]
    for (label,uid),rows in sorted(by_study.items()):
        variants={tuple(r.get('identity',{}).get(k,'').strip() for k in FIELDS) for _,r in rows}
        blocked=[]; eligible=[]
        for index,record in rows:
            reasons=stage.remaining_holds(record, metadata_only)
            if len(uid_owners[uid])>1 or len(hash_owners.get(record.get('sha256'),set()))>1:
                reasons.append('cross_patient_ownership_conflict')
            identity=record.get('identity',{})
            if len(id_owners.get((identity.get('IssuerOfPatientID','').strip(),identity.get('PatientID','').strip()),set()))>1:
                reasons.append('cross_patient_identifier_collision')
            if not record.get('sha256'): reasons.append('missing_source_hash')
            if any(not record.get('identity',{}).get(k,'').strip() for k in FIELDS):
                reasons.append('missing_identity_evidence')
            if reasons: blocked.append({'index':index,'reasons':sorted(set(reasons))})
            else: eligible.append(index)
        group_id='G'+stage.digest([label,uid,[(i,r.get('sha256')) for i,r in rows]])[:20]
        result.append({'group_id':group_id,'label':label,'study_uid':uid,
            'identity_variants':[dict(zip(FIELDS,v)) for v in sorted(variants)],
            'requires_variant_resolution':len(variants)>1,
            'eligible_indices':eligible,'blocked':blocked,'total_instances':len(rows)})
    blocked_hashes={plan['records'][entry['index']].get('sha256') for g in result for entry in g['blocked']}
    for group in result:
        kept=[]
        for index in group['eligible_indices']:
            if plan['records'][index].get('sha256') in blocked_hashes:
                group['blocked'].append({'index':index,'reasons':['same_hash_has_unresolved_scope']})
            else: kept.append(index)
        group['eligible_indices']=kept
    return result


def new_ledger(plan, metadata_only=False):
    return {'profile':stage.PROFILE,'plan_sha256':stage.digest(plan),
            **({'staging_mode':stage.METADATA_MODE} if metadata_only else {}),
            'profile_reviewed_by':'','objects':{},'group_reviews':[], 'release_allowed':False}


def authorize(plan, ledger, selected_groups, reviewer, evidence, resolve_variants=False):
    if ledger.get('plan_sha256')!=stage.digest(plan): raise ValueError('plan_changed')
    metadata_only = ledger.get('staging_mode') == stage.METADATA_MODE
    required = ('identity','profile') if metadata_only else ('identity','profile','pixels')
    if not reviewer.strip() or not all(evidence.get(k) for k in required):
        raise ValueError('separate_evidence_required')
    approved=0
    variants={tuple(v.get(k,'') for k in FIELDS) for g in selected_groups for v in g['identity_variants']}
    if len(variants)>1 and not resolve_variants: raise ValueError('authoritative_variant_resolution_required')
    for group in selected_groups:
        if group['requires_variant_resolution'] and not resolve_variants:
            raise ValueError('authoritative_variant_resolution_required')
        if not group['eligible_indices']: continue
        for index in group['eligible_indices']:
            record=plan['records'][index]
            ledger['objects'][record['sha256']]={'label':group['label'],
                'identity_verified_by':reviewer,'authority_reference':evidence['identity']['sha256'],
                **({'pixel_review_pending':True} if metadata_only else
                   {'all_frames_reviewed_by':reviewer,'pixel_identifiers_absent':True})}
            approved+=1
        ledger['group_reviews'].append({'group_id':group['group_id'],'reviewer':reviewer,
            'evidence':evidence,'identity_variants_explicitly_resolved':resolve_variants,
            'eligible_indices':group['eligible_indices']})
    if not approved: raise ValueError('no_eligible_objects_in_selection')
    ledger['profile_reviewed_by']=reviewer
    return approved


def public_counts(plan, grouped, ledger):
    eligible={i for g in grouped for i in g['eligible_indices']}
    return {'cohort_patients':len(plan['patients']), 'study_groups':len(grouped),
        'instances_available_for_evidence_review':len(eligible),
        'instances_remaining_held':len(plan['records'])-len(eligible),
        'studies_with_identity_variations':sum(g['requires_variant_resolution'] for g in grouped),
        'authorized_instance_paths':sum(i in eligible and r.get('sha256') in ledger['objects'] for i,r in enumerate(plan['records'])),
        'report_count_required':0,'release_allowed':False}


def write_table(path, grouped):
    with path.open('x',encoding='utf-8',newline='') as stream:
        out=csv.writer(stream)
        out.writerow(['group_id','patient_label','instance_paths','eligible_for_review','held','identity_variants','review_priority'])
        for g in sorted(grouped,key=lambda x:(not x['requires_variant_resolution'],x['label'],x['group_id'])):
            out.writerow([g['group_id'],g['label'],g['total_instances'],len(g['eligible_indices']),len(g['blocked']),
                          len(g['identity_variants']),'within-study identity review' if g['requires_variant_resolution'] else 'standard evidence review'])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action',choices=['prepare','worksheet','authorize','stage'])
    parser.add_argument('--config',default='/etc/hoag-research/config.json')
    parser.add_argument('--run',help='Existing private patient-bundle run; default latest bundle')
    choice=parser.add_mutually_exclusive_group()
    choice.add_argument('--patient'); choice.add_argument('--group')
    parser.add_argument('--reviewer')
    parser.add_argument('--metadata-only',action='store_true',help='Restricted copies; pixel review remains pending')
    parser.add_argument('--pilot-limit',type=int,default=0,help='Maximum new staging attempts; zero resumes all authorized objects')
    for name in ('identity','profile','pixels'): parser.add_argument('--'+name+'-evidence')
    parser.add_argument('--identity-verified',action='store_true')
    parser.add_argument('--profile-reviewed',action='store_true')
    parser.add_argument('--all-frames-reviewed-no-identifiers',action='store_true')
    parser.add_argument('--resolve-identity-variations',action='store_true')
    args=parser.parse_args()
    os.umask(0o077); logging.disable(logging.CRITICAL); warnings.filterwarnings('ignore')
    lock=None
    try:
        config=json.loads(Path(args.config).read_text())
        root=(Path(config['state_dir'])/'batch-reconciliation').resolve()
        def private(path):
            path=Path(path)
            if root not in path.resolve().parents or any(p.is_symlink() for p in (path,*path.parents)):
                raise ValueError('private_path_required')
            if os.name!='nt' and (path.stat().st_uid!=os.geteuid() or path.stat().st_mode & 0o077):
                raise ValueError('private_permissions_required')
            return path
        run=private(args.run) if args.run else private(max(root.glob('run-*/bundle-plan.private.json'),key=lambda p:p.stat().st_mtime_ns)).parent
        private(run)
        # Share the bundle writer lock; stage additionally uses its own DICOM lock.
        lock_path=run/'.bundle-lock'
        fd=os.open(lock_path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.close(fd); lock=lock_path
        bundle=json.loads(private(run/'bundle-plan.private.json').read_text())
        plan=bundle['stage_plan']; grouped=groups(plan,args.metadata_only)
        prefix = 'metadata-' if args.metadata_only else ''
        staging_run = run/'metadata-staging' if args.metadata_only else run
        if staging_run.exists(): private(staging_run)
        packet_path=run/(prefix+'dicom-review-packet.private.json')
        ledger_path=run/(prefix+'group-authorizations.private.json')
        if args.action=='prepare':
            if packet_path.exists():
                if json.loads(private(packet_path).read_text())['plan_sha256']!=stage.digest(plan): raise ValueError('review_plan_changed')
            else:
                stage.write_json(packet_path,{'plan_sha256':stage.digest(plan),'groups':grouped,
                    'source_records':plan['records'],'inventory_exceptions':plan['exceptions'],'release_allowed':False})
            table=run/(prefix+'dicom-review-summary.private.csv')
            if not table.exists(): write_table(table,grouped)
            if not ledger_path.exists(): stage.write_json(ledger_path,new_ledger(plan,args.metadata_only))
        if args.action=='worksheet':
            import batch_identity_worksheet
            if not args.metadata_only or not args.group: raise ValueError('metadata_group_required')
            counts=batch_identity_worksheet.generate(plan,grouped,args.group,root,run,config,private)
            print(json.dumps(counts))
            return 2 if counts['source_discrepancies'] else 0
        ledger=json.loads(private(ledger_path).read_text())
        if ledger['plan_sha256']!=stage.digest(plan): raise ValueError('review_plan_changed')
        if (ledger.get('staging_mode') == stage.METADATA_MODE) != args.metadata_only: raise ValueError('staging_mode_mismatch')
        if args.action=='authorize':
            if (staging_run/'checkpoint.private.json').exists(): raise ValueError('authorization_frozen_after_staging')
            if not (args.patient or args.group) or not (args.identity_verified and args.profile_reviewed and (args.metadata_only or args.all_frames_reviewed_no_identifiers)):
                raise ValueError('explicit_review_attestations_required')
            selected=[g for g in grouped if g['label']==args.patient or g['group_id']==args.group]
            if not selected: raise ValueError('unknown_group')
            # Never hide within-study conflicts inside a patient-wide approval.
            if args.patient and any(g['requires_variant_resolution'] for g in selected):
                raise ValueError('within_study_variants_require_individual_study_review')
            evidence={}
            required = ('identity','profile') if args.metadata_only else ('identity','profile','pixels')
            for kind in required:
                path=private(getattr(args,kind+'_evidence'))
                if not path.is_file() or path.stat().st_size==0: raise ValueError('empty_evidence')
                evidence[kind]={'path':str(path),'sha256':stage.fingerprint(path)}
            if len({v['path'] for v in evidence.values()})!=len(required): raise ValueError('separate_evidence_artifacts_required')
            authorize(plan,ledger,selected,args.reviewer or '',evidence,args.resolve_identity_variations)
            stage.write_json(ledger_path,ledger)
        if args.action=='stage':
            if not ledger['objects']: raise ValueError('no_authorized_objects')
            for review in ledger['group_reviews']:
                for item in review['evidence'].values():
                    if stage.fingerprint(private(item['path']))!=item['sha256']: raise ValueError('review_evidence_changed')
            mounted=base.mounts(Path('/proc/self/mountinfo').read_text())
            staging_run.mkdir(mode=0o700,exist_ok=True)
            stage.stage(plan,ledger,config,mounted,staging_run,lambda value:print(json.dumps(value),flush=True),pilot_limit=args.pilot_limit)
        if args.action != 'stage': print(json.dumps(public_counts(plan,grouped,ledger)))
        return 0
    except Exception as error:
        # Only authored fixed reason codes may reach the terminal.
        code=str(error) if type(error) is ValueError and str(error).replace('_','').isalpha() else type(error).__name__
        print(json.dumps({'completed':False,'reason':code,'release_allowed':False}))
        return 2
    finally:
        if lock is not None: lock.unlink()


if __name__=='__main__': raise SystemExit(main())
