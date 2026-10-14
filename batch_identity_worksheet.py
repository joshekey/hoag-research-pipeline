"""Private evidence worksheet only: never creates an identity/profile approval."""
from collections import Counter
import json
import os
import pydicom
import batch_dicom_stage as stage
import batch_reconcile as base
from pathlib import Path


def generate(plan, grouped, group_id, root, run, config, private):
    selected=[g for g in grouped if g['group_id']==group_id]
    if len(selected)!=1: raise ValueError('unknown_group')
    group=selected[0]
    manifest=None
    for path in root.glob('run-*/manifest.private.json'):
        candidate=json.loads(private(path).read_text())
        if stage.digest(candidate)==plan['manifest_sha256']:
            manifest=candidate
            break
    if manifest is None: raise ValueError('matching_manifest_missing')
    patients=[p for p in manifest['private_crosswalk'] if p.get('','').strip()==group['label']]
    if len(patients)!=1: raise ValueError('crosswalk_patient_not_unique')
    mounted=base.mounts(Path('/proc/self/mountinfo').read_text())
    fields=('PatientName','PatientID','IssuerOfPatientID','PatientBirthDate')
    observed=Counter(); discrepancies=[]; objects=[]
    indices=sorted(group['eligible_indices']+[x['index'] for x in group['blocked']])
    for index in indices:
        record=plan['records'][index]
        entry={'index':index,'source_sha256':record.get('sha256'),'path':record['path']}
        try:
            source=base.source_checked(record['path'],config['source_roots'],Path(config['output_root']).resolve(),mounted)
            if stage.fingerprint(source)!=record.get('sha256'): raise ValueError('source_changed')
            ds=pydicom.dcmread(source,stop_before_pixels=True)
            identity={k:str(ds.get(k,'')) for k in fields}
            entry['observed_identity']=identity
            entry['study_uid']=str(ds.get('StudyInstanceUID',''))
            entry['sop_uid']=str(ds.get('SOPInstanceUID',''))
            entry['annotation_status']=str(ds.get('BurnedInAnnotation',''))
            entry['patient_age']=str(ds.get('PatientAge',''))
            if identity!=record.get('identity'): raise ValueError('identity_changed')
            if entry['study_uid']!=record['study_uid'] or entry['sop_uid']!=record.get('sop_uid'):
                raise ValueError('membership_changed')
            if stage.fingerprint(source)!=record.get('sha256'): raise ValueError('source_changed')
            observed[tuple(identity[k] for k in fields)]+=1
            entry['source_recheck']='passed'
        except Exception as error:
            code=str(error) if type(error) is ValueError and str(error) in (
                'source_changed','identity_changed','membership_changed') else 'source_access_or_header_failure'
            discrepancies.append({'index':index,'reason':code})
            entry['source_recheck']=code
        objects.append(entry)
    packet={'plan_sha256':stage.digest(plan),'manifest_sha256':stage.digest(manifest),
            'group':group,'crosswalk_row':patients[0], 'source_rechecks':objects,
            'source_discrepancies':discrepancies,'identity_approved':False,
            'profile_approved':False,'pixel_review_pending':True,'release_allowed':False}
    # New files only; repeat generation must not overwrite reviewer notes.
    evidence=run/(group_id+'-worksheet.private.json')
    worksheet=run/(group_id+'-worksheet.private.txt')
    if any(p.exists() or p.is_symlink() for p in (evidence,worksheet)):
        raise ValueError('worksheet_already_exists')
    lines=['RESTRICTED HOSPITAL REVIEW WORKSHEET - NOT AN APPROVAL',
        'Group: '+group_id, 'Patient label: '+group['label'],
        'Plan SHA256: '+stage.digest(plan), 'Manifest SHA256: '+stage.digest(manifest),
        'Study UID: '+group['study_uid'],
        'Eligible paths: '+str(len(group['eligible_indices'])),
        'Held paths: '+str(len(group['blocked'])),
        'Fresh source discrepancies: '+str(len(discrepancies)),
        '', 'EXISTING CROSSWALK ROW (provenance must be confirmed by reviewer)',
        json.dumps(patients[0],ensure_ascii=True,indent=2),
        '', 'OBSERVED DICOM IDENTITIES (fresh hash-verified headers)']
    for values,count in sorted(observed.items()):
        lines.append(json.dumps({'instance_paths':count,**dict(zip(fields,values))},ensure_ascii=True))
    lines.extend(['','RECORDED VARIATIONS / HOLDS / SOURCE DISCREPANCIES',
        json.dumps({'identity_variants':group['identity_variants'],'blocked':group['blocked'],
                    'source_discrepancies':discrepancies},ensure_ascii=True,indent=2),
        '', 'IDENTITY REVIEW - reviewer must complete from actual authoritative records',
        'The crosswalk PATIENTID may be an internal key, NOT an MRN. Do not assume equivalence.',
        'Confirm who supplied/verified the crosswalk and its patient-to-P### mapping.',
        'For EVERY observed identifier tuple, document the authoritative patient-master/MPI',
        'or source-system record linking PatientID AND its issuer to this crosswalk patient.',
        'Compare name and DOB as corroboration; name, age, folder and report match are insufficient.',
        'Document authoritative namespace/history evidence for each difference; unresolved stays held.',
        'No differences detected does not itself prove identity. The generator makes no identity decision.',
        'Authority record reference / provenance: [reviewer to complete]',
        'Identifier mapping and discrepancy resolution: [reviewer to complete]',
        'Scope confirmed (group, eligible source hashes, P###): [reviewer to complete]',
        'Reviewer / role / UTC date / decision: [reviewer to complete]',
        '', 'METADATA PROFILE REVIEW - separate approval required',
        'Profile: '+stage.PROFILE+'; mode: '+stage.METADATA_MODE,
        'Name/ID become P###. Preserve original PatientAge; never infer age.',
        'Remove dates/times, institution/free text, identifying nested metadata and private tags.',
        'Map non-class UIDs consistently using the existing secret. Originals remain untouched.',
        'Unknown annotation status permits restricted copies only. Pixels remain unchanged.',
        'Known burned-in identifiers, overlays, unsupported decoding and other holds remain blocked.',
        'Read-back checks metadata/UIDs/age and decoded pixel equality; NOT pixel privacy clearance.',
        'Output is private metadata-staging, no exports; release remains disabled.',
        'Reviewed implementation/profile reference and any limitations: [reviewer to complete]',
        'Reviewer / role / UTC date / decision: [reviewer to complete]',
        '', 'After genuine confirmation, create TWO separate signed/reviewer-attributed records:',
        'identity approval and metadata-only profile approval. Bind each to the group and plan hash.',
        'Do not submit this uncompleted worksheet as approval. No pixel safety attestation is requested.',
        'Detailed per-instance paths/hashes are in the sibling private JSON. Keep both hospital-local.'])
    for path,text in ((evidence,json.dumps(packet,indent=2)),(worksheet,'\n'.join(lines)+'\n')):
        fd=os.open(path,os.O_CREAT|os.O_EXCL|os.O_WRONLY,0o600)
        with os.fdopen(fd,'w',encoding='utf-8') as stream: stream.write(text)
    return {'worksheets_created':1,'instance_paths_rechecked':len(indices),
            'source_discrepancies':len(discrepancies),'observed_identity_variants':len(observed),
            'eligible_instance_paths':len(group['eligible_indices']),
            'held_instance_paths':len(group['blocked']),'approvals_created':0,'release_allowed':False}
