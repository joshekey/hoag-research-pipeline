"""Patient worklist and explicitly selected, independently checked batch operations."""
import json
import uuid
from pathlib import Path

from store import database, checkpoint, progress, research_uid, idle, audit


def remove(config, subject, payload):
    """Remove a reviewed selection from the catalog, never from source storage."""
    with database(config) as db:
        db.execute('BEGIN IMMEDIATE')
        idle(db)
        uids = [r[0] for r in db.execute('SELECT uid FROM studies WHERE subject=?',(subject,))]
        expected = payload.get('uids')
        if payload.get('confirm') is not True or not isinstance(expected,list) or any(not isinstance(u,str) for u in expected) or not uids or sorted(expected)!=sorted(uids):
            raise ValueError('Patient examinations changed; reload and confirm removal again')
        for uid in uids:
            db.execute("UPDATE files SET active=0 WHERE kind='dicom' AND json_extract(metadata,'$.StudyInstanceUID')=?",(uid,))
            for table in ('txt_associations','browser_associations','study_suggestions'):
                db.execute('DELETE FROM '+table+' WHERE uid=?',(uid,))
            db.execute('DELETE FROM studies WHERE uid=?',(uid,))
        audit(db,config['username'],'remove-patient-from-catalog',subject)
    return dict(removed=len(uids))


def listing(config, query='', offset=0, attention=False):
    with database(config) as db:
        where = 'subject IN (SELECT subject FROM studies WHERE name LIKE ? OR patient LIKE ? OR subject LIKE ?)'
        if attention:
            where += " AND subject IN (SELECT subject FROM studies WHERE state NOT IN ('approved','exported'))"
        args = ['%' + query[:200] + '%'] * 3
        total = db.execute('SELECT count(DISTINCT subject) FROM studies WHERE '+where, args).fetchone()[0]
        rows = [dict(r) for r in db.execute('SELECT subject,MIN(name) name,MIN(patient) patient,count(*) studies,'
            "sum(report_id IS NOT NULL) associated,sum(state='approved') approved,sum(state='exported') exported "
            'FROM studies WHERE '+where+' GROUP BY subject ORDER BY name,subject LIMIT 25 OFFSET ?', [*args, offset])]
    return dict(rows=rows,total=total,offset=offset)


def examinations(config, subject):
    with database(config) as db:
        rows = [dict(r) for r in db.execute('''SELECT s.uid,s.subject,s.name,s.patient,s.date,s.modality,s.count,
            s.state,s.report_id,s.last_error,length(s.sanitized)>0 prepared,
            (SELECT count(*) FROM study_suggestions g WHERE g.uid=s.uid AND g.fingerprint=s.fingerprint) suggestions,
            (SELECT metadata FROM files f WHERE f.kind='dicom' AND f.active=1 AND f.status='ok'
                AND json_extract(f.metadata,'$.StudyInstanceUID')=s.uid ORDER BY f.id LIMIT 1) metadata
            FROM studies s WHERE s.subject=? ORDER BY s.date DESC,s.uid''', (subject,))]
    for row in rows:
        meta=json.loads(row.pop('metadata') or '{}')
        row['description']=meta.get('StudyDescription') or ('Series: '+meta['SeriesDescription'] if meta.get('SeriesDescription') else 'Description unavailable')
        row['issuer']=meta.get('IssuerOfPatientID','')
    return rows


def targets(config, payload):
    subject=payload.get('subject'); uids=payload.get('uids')
    if not isinstance(subject,str) or not isinstance(uids,list) or not uids or len(uids)>10000 or any(not isinstance(u,str) for u in uids) or len(set(uids))!=len(uids):
        raise ValueError('Choose examinations from one patient')
    rows={r['uid']:r for r in examinations(config,subject)}
    if any(u not in rows for u in uids):
        raise ValueError('Patient selection changed; reload the patient worklist')
    return [rows[u] for u in uids]


def process(config, kind, payload, job):
    import engine
    rows=targets(config,payload)
    exported=[]; failures=0; held=0
    for i,row in enumerate(rows,1):
        checkpoint(config,job)
        eligible=(row['state']=='approved') if kind=='export_patient' else (row['report_id'] is not None and row['state'] not in ('approved','exported') and not row['prepared'])
        if not eligible:
            held+=1; continue
        try:
            if kind=='export_patient':
                folder=engine.export_study(config,row['uid'],job)
                exported.append({'study_id':research_uid(config,row['uid']),'folder':folder.name})
            else:
                from report_browser import authorized
                if not authorized(config,row['uid'],row['report_id']):
                    raise ValueError('Confirm this report association before bulk preparation')
                engine.prepare(config,row['uid'],row['report_id'],job)
        except ValueError as error:
            failures+=1
            with database(config) as db:
                db.execute('UPDATE studies SET last_error=? WHERE uid=?',(str(error),row['uid']))
        progress(config,job,i,f'{i} of {len(rows)} selected examinations processed; {held} held; {failures} failed','Patient batch')
    if exported:
        # Only anonymous identifiers and relative research-package paths leave the server.
        parent=Path(config['output_root'])/payload['subject']
        manifest={'version':1,'subject_id':payload['subject'],'studies':exported,'held_count':held,'failed_count':failures}
        path=parent/('patient-manifest-'+uuid.uuid4().hex[:12]+'.json')
        with path.open('x',encoding='utf-8') as f: json.dump(manifest,f,indent=2)
    if failures:
        raise ValueError(f'{failures} examinations failed; completed studies retained. Open the patient worklist to review errors.')
    return f'Completed {len(rows)-held} examinations; {held} held. '+('Patient manifest saved beside study folders.' if exported else '')
