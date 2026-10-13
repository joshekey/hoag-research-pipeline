"""Patient-based DICOM and redacted TXT/SQL restricted staging. Never releases."""
import argparse
from collections import Counter, defaultdict
import hashlib
import json
import logging
import os
from pathlib import Path
import warnings

import batch_dicom_stage as dicom
import batch_reconcile as base
import batch_report_selection as selection
import engine
from report_format import format_report
from sql_gui_candidates import request_broker


def sha(raw): return hashlib.sha256(raw).hexdigest()


def read_selected(record, config, mounted, broker=request_broker):
    if record['source']=='sql':
        result=broker({'action':'browse_preview','token':record['ref']})
        text=result['text']
        if sha(text.encode('utf-8'))!=record['sha256'] or result['hash']!=record['sha256']:
            raise ValueError('sql_report_changed')
        if len(text.encode('utf-8'))>config['max_report_bytes']: raise ValueError('report_too_large')
        text,status=format_report(text)
        if '<' in text and '>' in text: raise ValueError('unresolved_markup')
        return text
    path=base.source_checked(record['path'],config['source_roots'],Path(config['output_root']).resolve(),mounted)
    if path.stat().st_size>config['max_report_bytes']: raise ValueError('report_too_large')
    raw=path.read_bytes()
    if sha(raw)!=record['sha256']: raise ValueError('txt_report_changed')
    return raw.decode(config['report_encoding'],errors='strict')


def prepare(manifest, stage_plan, config, mounted, broker=request_broker, emit=lambda x:None):
    if stage_plan['manifest_sha256']!=dicom.digest(manifest): raise ValueError('manifest_plan_mismatch')
    txt=selection.select_reports(manifest,config,mounted)
    reports=[dict(r,source='txt') for r in txt['reports']]
    patients=manifest['private_crosswalk']
    known=defaultdict(set)
    for record in stage_plan['records']:
        value=record.get('identity',{}).get('PatientID','').strip()
        if value: known[record['label']].add(value)
    after=''; snapshot=None; seen=set(); sql_complete=False
    try:
        while True:
            page=broker({'action':'match_catalog','after':after})
            if snapshot is None: snapshot=page['snapshot']
            if page['snapshot']!=snapshot: raise ValueError('sql_index_changed')
            for row in page['rows']:
                if row['ref'] in seen: raise ValueError('repeated_sql_record')
                seen.add(row['ref'])
                record={'source':'sql','ref':row['ref'],'sha256':row['hash'],
                        'status':'held','labels':[],'reasons':[],'accepted':False,'release_allowed':False}
                reports.append(record)
                try:
                    text=read_selected(record,config,mounted,broker)
                    record.update(selection.evaluate(text,patients,known))
                except Exception: record['reasons']=['sql_preview_or_source_validation_failed']
            emit({'phase':'sql_selection','records_checked':len(seen)})
            if not page['has_more']: sql_complete=True; break
            if not page['next_after'] or page['next_after']==after: raise ValueError('sql_cursor_stalled')
            after=page['next_after']
    except Exception:
        # Incomplete SQL discovery never pretends to be complete, and does not
        # invalidate independently verified DICOM or TXT work.
        sql_complete=False
    # Preserve cross-source ambiguity as a hold, never silently deduplicate.
    groups=defaultdict(list)
    for r in reports:
        if r.get('sha256'): groups[r['sha256']].append(r)
    for group in groups.values():
        if len({label for r in group for label in r['labels']})>1:
            for r in group:
                r['status']='held'; r['reasons'].append('cross_source_patient_conflict')
    identities={p[''].strip():[p.get(k,'') for k in ('FNAME','MNAME','LNAME')] for p in patients}
    for record in stage_plan['records']:
        identities[record['label']].extend(record.get('identity',{}).values())
    return {'version':'1.50','mode':'patient_bundle_staging_plan','stage_plan':stage_plan,
        'reports':reports,'redaction_identifiers':{k:sorted(set(v)-{''}) for k,v in identities.items()},
        'sql_discovery_complete':sql_complete,'sql_snapshot':snapshot,'release_allowed':False}


def summary(plan):
    return {'cohort_patients':len(plan['stage_plan']['patients']),
        'reports':dict(Counter(r['source']+'_'+r['status'] for r in plan['reports'])),
        'patients_with_selected_reports':len({label for r in plan['reports'] if r['status']=='selected_by_exact_name' for label in r['labels']}),
        'sql_discovery_complete':plan['sql_discovery_complete'],'release_allowed':False}


def process(plan, reviews, config, mounted, run, broker=request_broker, emit=lambda x:None):
    if plan.get('mode')!='patient_bundle_staging_plan': raise ValueError('bundle_plan_required')
    # Existing DICOM review gates remain mandatory. Missing ledger never blocks reports.
    if reviews and reviews.get('profile_reviewed_by'):
        if reviews.get('reports'): raise ValueError('use_bundle_report_plan_not_legacy_report_list')
        dicom.stage(plan['stage_plan'],reviews,config,mounted,run,emit)
    else:
        emit({'phase':'dicom','status':'awaiting_existing_identity_and_profile_reviews','release_allowed':False})
    binding=dicom.digest(plan)
    state_path=run/'report-checkpoint.private.json'
    if state_path.is_symlink(): raise ValueError('symlink')
    state=json.loads(state_path.read_text()) if state_path.exists() else {'binding':binding,'reports':{},'release_allowed':False}
    if state['binding']!=binding: raise ValueError('resume_plan_changed')
    for index,record in enumerate(plan['reports']):
        key=str(index)
        if record['status']!='selected_by_exact_name' or len(record['labels'])!=1:
            state['reports'][key]={'status':'held_selection'}; continue
        label=record['labels'][0]
        target=run/label/'reports'/(f'report-{index+1:06d}-{record["source"]}.txt')
        try:
            if label not in plan['stage_plan']['patients']: raise ValueError('label')
            if any(p.is_symlink() for p in (target,target.with_suffix('.partial'),*target.parents)): raise ValueError('symlink')
            text=read_selected(record,config,mounted,broker)
            if target.exists():
                if sha(target.read_bytes())!=state['reports'].get(key,{}).get('sha256'): raise ValueError('staged_report_changed')
                continue
            identifiers=plan['redaction_identifiers'][label]
            # Mandatory redaction for BOTH TXT and SQL; no copy-through fallback.
            clean=engine.sanitize_report(text,identifiers)
            # Independent second pass: any known identifier left in the output
            # holds the report, including failures to redact body occurrences.
            import re
            for value in identifiers:
                if len(value)>=2 and re.search(r'(?<!\w)'+re.escape(value)+r'(?!\w)',clean,re.I):
                    raise ValueError('residual_identifier')
            target.parent.mkdir(parents=True,exist_ok=True,mode=0o700)
            payload=('Patient: '+label+'\n'+clean).encode('utf-8')
            temp=target.with_suffix('.partial')
            with temp.open('wb') as stream:
                stream.write(payload); stream.flush(); os.fsync(stream.fileno())
            if temp.read_bytes()!=payload: raise ValueError('readback_failed')
            os.replace(temp,target)
            state['reports'][key]={'status':'redacted_awaiting_independent_review','sha256':sha(payload)}
        except Exception as error:
            state['reports'][key]={'status':'held_redaction_or_source','error_type':type(error).__name__}
        dicom.write_json(state_path,state)
        if (index+1)%20==0: emit({'phase':'report_redaction','reports_checked':index+1,'release_allowed':False})
    dicom.write_json(state_path,state)
    emit({'phase':'reports_complete','counts':dict(Counter(v['status'] for v in state['reports'].values())),
          'sql_discovery_complete':plan['sql_discovery_complete'],'release_allowed':False})
    return state


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',default='/etc/hoag-research/config.json')
    parser.add_argument('--prepare-latest',action='store_true')
    parser.add_argument('--process-latest',action='store_true')
    parser.add_argument('--reviews',help='Existing private hash-bound DICOM review ledger')
    args=parser.parse_args()
    if args.prepare_latest==args.process_latest: parser.error('Choose prepare-latest or process-latest')
    os.umask(0o077); logging.disable(logging.CRITICAL); warnings.filterwarnings('ignore')
    try:
        config=json.loads(Path(args.config).read_text())
        mounted=base.mounts(Path('/proc/self/mountinfo').read_text())
        root=(Path(config['state_dir'])/'batch-reconciliation').resolve()
        def private(path):
            path=Path(path)
            if root not in path.resolve().parents or any(p.is_symlink() for p in (path,*path.parents)): raise ValueError('private_path')
            if os.name!='nt' and any(p.stat().st_uid!=os.geteuid() or p.stat().st_mode & 0o077 for p in (path,path.parent)): raise ValueError('private_permissions')
            return path
        emit=lambda value:print(json.dumps(value),flush=True)
        if args.prepare_latest:
            stage_path=max(root.glob('run-*/stage-plan.private.json'),key=lambda p:p.stat().st_mtime_ns)
            stage_plan=json.loads(private(stage_path).read_text())
            manifest=None
            for path in root.glob('run-*/manifest.private.json'):
                candidate=json.loads(private(path).read_text())
                if dicom.digest(candidate)==stage_plan['manifest_sha256']: manifest=candidate; break
            if manifest is None: raise ValueError('matching_manifest_missing')
            plan=prepare(manifest,stage_plan,config,mounted,emit=emit)
            run=base.private_run_directory(config)
            dicom.write_json(run/'bundle-plan.private.json',plan)
            # An empty template holds DICOM; never fabricate review approval.
            reviews={'profile':dicom.PROFILE,'plan_sha256':dicom.digest(stage_plan),
                     'profile_reviewed_by':'','objects':{}}
            previous=stage_path.parent/'review-template.private.json'
            if previous.exists():
                candidate=json.loads(private(previous).read_text())
                if candidate.get('plan_sha256')==dicom.digest(stage_plan) and candidate.get('profile')==dicom.PROFILE:
                    reviews=candidate
            dicom.write_json(run/'review-template.private.json',reviews)
            emit(summary(plan))
        else:
            path=max(root.glob('run-*/bundle-plan.private.json'),key=lambda p:p.stat().st_mtime_ns)
            plan=json.loads(private(path).read_text()); run=path.parent
            reviews=json.loads(private(args.reviews).read_text()) if args.reviews else json.loads(private(run/'review-template.private.json').read_text())
            lock=run/'.bundle-lock'
            fd=os.open(lock,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600); os.close(fd)
            try: process(plan,reviews,config,mounted,run,emit=emit)
            finally: lock.unlink()
        return 0
    except Exception:
        print(json.dumps({'completed':False,'release_allowed':False}))
        return 2


if __name__=='__main__': raise SystemExit(main())
