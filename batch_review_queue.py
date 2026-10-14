"""Prioritized hospital-local evidence queue. Never authorizes or stages."""
import argparse
import csv
import json
import os
from pathlib import Path
from collections import Counter
import batch_dicom_review as review
import batch_dicom_stage as stage
import batch_reconcile as base

LANES=('eligible_standard_review','identity_variation_review','technical_holds_only')


def build_queue(plan):
    grouped=review.groups(plan)
    rows=[]
    for group in grouped:
        lane=('identity_variation_review' if group['requires_variant_resolution'] else
              'eligible_standard_review' if group['eligible_indices'] else 'technical_holds_only')
        rows.append({**group,'lane':lane,'evidence':{
            'authoritative_identity':'not_reviewed','approved_profile':'not_reviewed',
            'all_frames_and_overlays':'not_reviewed'},'authorization':'not_granted_by_queue',
            'partial_study':bool(group['eligible_indices'] and group['blocked']),
            'hold_reasons':sorted({reason for entry in group['blocked'] for reason in entry['reasons']})})
    # First fully reviewable studies, then partial studies. Within each class,
    # smaller review scopes first; deterministic anonymous-label tie breaking.
    rows.sort(key=lambda g:(LANES.index(g['lane']),g['partial_study'],
                            -bool(g['eligible_indices']),len(g['eligible_indices']) or g['total_instances'],g['label'],g['group_id']))
    for position,row in enumerate(rows,1): row['priority']=position
    lanes={}
    for lane in LANES:
        selected=[r for r in rows if r['lane']==lane]
        lanes[lane]={'study_groups':len(selected),'unique_patients':len({r['label'] for r in selected}),
            'eligible_instance_paths':sum(len(r['eligible_indices']) for r in selected),
            'held_instance_paths':sum(len(r['blocked']) for r in selected)}
    absent=sorted(set(plan['patients'])-{r['label'] for r in rows})
    return {'plan_sha256':stage.digest(plan),'profile':stage.PROFILE,'groups':rows,
        'patients_without_inventoried_studies':absent,
        'inventory_exceptions':plan.get('exceptions',[]),
        'summary':{'cohort_patients':len(plan['patients']),'study_groups':len(rows),'lanes':lanes,
            'patients_without_inventoried_studies':len(absent),
            'eligible_instance_paths':sum(len(r['eligible_indices']) for r in rows),
            'held_instance_paths':sum(len(r['blocked']) for r in rows),
            'authorizations_created':0,'report_count_required':0,'release_allowed':False},
        'release_allowed':False}


def save_queue(run,packet):
    stage.write_json(run/'review-queue.private.json',packet)
    columns=['priority','group_id','label','eligible_instances','held_instances','partial_study',
             'identity_variant_count','hold_reasons','authoritative_identity','approved_profile','all_frames_and_overlays']
    for lane in LANES:
        with (run/(lane+'.private.csv')).open('x',encoding='utf-8',newline='') as stream:
            writer=csv.writer(stream); writer.writerow(columns)
            for g in packet['groups']:
                if g['lane']==lane:
                    writer.writerow([g['priority'],g['group_id'],g['label'],len(g['eligible_indices']),len(g['blocked']),
                        g['partial_study'],len(g['identity_variants']),'; '.join(g['hold_reasons']),
                        *g['evidence'].values()])


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='/etc/hoag-research/config.json')
    parser.add_argument('--run',help='Private bundle run; defaults to latest bundle')
    args=parser.parse_args()
    os.umask(0o077)
    try:
        config=json.loads(Path(args.config).read_text())
        root=(Path(config['state_dir'])/'batch-reconciliation').resolve()
        path=Path(args.run)/'bundle-plan.private.json' if args.run else max(root.glob('run-*/bundle-plan.private.json'),key=lambda p:p.stat().st_mtime_ns)
        if root not in path.resolve().parents or any(p.is_symlink() for p in (path,*path.parents)): raise ValueError('private_path')
        if os.name!='nt' and any(p.stat().st_uid!=os.geteuid() or p.stat().st_mode & 0o077 for p in (path,path.parent)):
            raise ValueError('private_permissions')
        bundle=json.loads(path.read_text())
        packet=build_queue(bundle['stage_plan'])
        packet['source_bundle_run']=str(path.parent)
        run=base.private_run_directory(config)
        save_queue(run,packet)
        print(json.dumps(packet['summary']))
        return 0
    except Exception:
        print(json.dumps({'queue_prepared':False,'release_allowed':False}))
        return 2


if __name__=='__main__': raise SystemExit(main())
