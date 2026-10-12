"""Bounded report evidence indexing and explainable, human-confirmed study suggestions."""
import json
import re
import time
from pathlib import Path
from store import database,checkpoint,progress
from strict_identity_match import normalize_name,normalize_date
from txt_candidate_discovery import clues,all_patient_names,clean_id,evaluate
from sql_gui_candidates import request_broker
from sql_dicom_pilot import regions

LABELS={'study_uid':'Study identifier','accession':'Accession','exact_filename_accession':'Accession in filename',
        'name':'Full patient name','dob':'Date of birth','patient_id':'Patient ID (check issuing system)',
        'exam_date':'Examination date','modality':'Imaging type','anatomy':'Body area','name_declarations':'Multiple patient names'}


def evidence(value, filename=''):
    from report_format import format_report
    value,_=format_report(value)
    info=clues(value,Path(filename).stem)
    generic=re.search(r'(?im)^\s*date\s*:\s*([^\r\n]+)',value[:20000])
    info['report_date']=normalize_date(generic[1].strip()) if generic else ''
    names=all_patient_names(value)
    info['name_conflict']=len(names)>1
    if info['name_conflict']:info['name']=''
    info['name_tokens']=sorted({word for name in names for word in name.split()})
    info['modalities']=sorted(info['modalities']);info['anatomy']=sorted(info['anatomy'])
    return info


def keys(info):
    result=[]
    for prefix,field in [('U','study_uid'),('A','accession'),('P','patient_id'),('N','name')]:
        if info.get(field):result.append(prefix+':'+info[field])
    if info.get('filename_key'):result.append('A:'+info['filename_key'])
    result.extend('W:'+word for word in info.get('name_tokens',[]))
    return sorted(set(result))


def rank(study, info, dob='', anatomy=()):
    info=dict(info);info['modalities']=set(info['modalities']);info['anatomy']=set(info['anatomy'])
    result=evaluate(study,info,study_dob=dob,study_anatomy=anatomy)
    conflicts=set(result['conflicts'])
    if info.get('name_conflict'):conflicts.add('name_declarations')
    sm=set((study.get('modality') or '').split(','))
    if sm and info['modalities'] and not sm.intersection(info['modalities']):conflicts.add('modality')
    if anatomy and info['anatomy'] and not set(anatomy).intersection(info['anatomy']):conflicts.add('anatomy')
    hits=set(result['evidence'])
    report_date=info.get('report_date','')
    supporting_date=bool(report_date and report_date==normalize_date(study.get('date')))
    if supporting_date:result['score']+=10
    strong=not conflicts and (bool(hits & {'study_uid','accession'}) and 'name' in hits or {'name','dob','exam_date'}<=hits)
    bucket='differences' if conflicts else 'strong' if strong else 'review'
    return {'bucket':bucket,'points':result['score'],'matches':[LABELS[x] for x in result['evidence']]+(['Report header date (verify examination date)'] if supporting_date else []),
            'differences':[LABELS[x] for x in sorted(conflicts)],'missing':[LABELS[x] for x in ('name','dob','exam_date') if not info[x]],
            'manual_confirmation_required':True}


def build(config, job=None):
    from engine import require_scan,family_name
    run=str(time.time_ns());sql_status='not checked';count=0
    with database(config) as db:
        require_scan(db)
        db.execute("INSERT OR REPLACE INTO settings VALUES ('suggestions_ready','0')")
        db.execute('DELETE FROM match_report_keys');db.execute('DELETE FROM match_reports');db.execute('DELETE FROM study_suggestions')
    def save(rows):
        nonlocal count
        with database(config) as db:
            for row in rows:
                cursor=db.execute('INSERT INTO match_reports(source,ref,path,hash,evidence) VALUES (?,?,?,?,?)',(row['source'],row['ref'],row['path'],row['hash'],json.dumps(row['evidence'])))
                db.executemany('INSERT OR IGNORE INTO match_report_keys VALUES (?,?)',[(key,cursor.lastrowid) for key in keys(row['evidence'])])
        count+=len(rows);progress(config,job,count,'Reading report demographics locally','Index report evidence')
    last=0
    while True:
        checkpoint(config,job)
        with database(config) as db:
            rows=[dict(r) for r in db.execute("SELECT f.id,f.path,f.metadata,s.text FROM files f JOIN report_search s ON s.rowid=f.id WHERE f.kind='report' AND f.active=1 AND f.status='ok' AND f.id>? ORDER BY f.id LIMIT 100",(last,))]
        if not rows:break
        save([{'source':'txt','ref':str(r['id']),'path':r['path'],'hash':json.loads(r['metadata'])['hash'],'evidence':evidence(r['text'],r['path'])} for r in rows]);last=rows[-1]['id']
    with database(config) as db:txt_total=db.execute("SELECT count(*) FROM files WHERE kind='report' AND active=1 AND status='ok'").fetchone()[0]
    txt_status=str(count)+' of '+str(txt_total)+' TXT reports searchable'+('; reindex TXT sources to restore missing search text' if count<txt_total else '')
    offset=0;snapshot=None;after=''
    try:
        while True:
            checkpoint(config,job)
            data=request_broker({'action':'match_catalog','offset':offset,'after':after})
            if snapshot is not None and snapshot!=data['snapshot']:raise ValueError('SQL index changed during matching')
            snapshot=data['snapshot'];save(data['rows'])
            offset+=len(data['rows'])
            after=data.get('next_after','')
            if not data.get('has_more',False):break
            if not data['rows']:raise ValueError('SQL evidence index returned an incomplete page')
        sql_status='searched'
    except ValueError:
        sql_status='unavailable — TXT suggestions only; check SQL index/service'
        with database(config) as db:
            db.execute("DELETE FROM match_report_keys WHERE report IN (SELECT id FROM match_reports WHERE source='sql')")
            db.execute("DELETE FROM match_reports WHERE source='sql'")
    last='';processed=0
    while True:
        checkpoint(config,job)
        with database(config) as db:studies=[dict(r) for r in db.execute('SELECT * FROM studies WHERE uid>? ORDER BY uid LIMIT 100',(last,))]
        if not studies:break
        for study in studies:
            checkpoint(config,job)
            with database(config) as db:
                first=db.execute("SELECT metadata FROM files WHERE kind='dicom' AND active=1 AND status='ok' AND json_extract(metadata,'$.StudyInstanceUID')=? ORDER BY id LIMIT 1",(study['uid'],)).fetchone()
                meta=json.loads(first[0]) if first else {}
                studykeys=['N:'+normalize_name(study['name']),'A:'+clean_id(study['accession']),'P:'+clean_id(study['patient']),'U:'+study['uid']]
                studykeys+=['W:'+word for word in normalize_name(family_name(study['name'])).split()]
                placeholders=','.join('?' for _ in studykeys)
                candidates=[dict(r) for r in db.execute("SELECT r.* FROM match_reports r JOIN match_report_keys k ON k.report=r.id WHERE k.key IN ("+placeholders+") GROUP BY r.id ORDER BY MIN(CASE substr(k.key,1,1) WHEN 'U' THEN 0 WHEN 'A' THEN 1 WHEN 'N' THEN 2 WHEN 'P' THEN 3 ELSE 4 END),r.id LIMIT 1001",studykeys)]
            anatomy=regions(meta.get('BodyPartExamined','')+' '+meta.get('SeriesDescription',''))
            ranked=[]
            for report in candidates[:1000]:
                info=json.loads(report['evidence']); result=rank(study,info,meta.get('PatientBirthDate',''),anatomy)
                ranked.append((report,result))
            ranked.sort(key=lambda x:({'strong':0,'review':1,'differences':2}[x[1]['bucket']],-x[1]['points'],x[0]['source']!='sql',x[0]['id']))
            with database(config) as db:
                for position,(report,result) in enumerate(ranked[:10]):
                    db.execute('INSERT INTO study_suggestions VALUES (?,?,?,?,?,?)',(study['uid'],report['id'],study['fingerprint'],position,json.dumps(result),run))
            processed+=1
            if processed%100==0:progress(config,job,processed,'Comparing studies with indexed report evidence','Find report suggestions')
        last=studies[-1]['uid']
    with database(config) as db:
        for key,value in [('suggestions_ready','1'),('suggestions_txt',txt_status),('suggestions_sql',sql_status),('suggestions_built',str(time.time())),('suggestions_run',run)]:
            db.execute('INSERT OR REPLACE INTO settings VALUES (?,?)',(key,value))
    progress(config,job,processed,'Report suggestions ready; relationships require researcher confirmation','Find report suggestions')


def suggestions(config,uid):
    with database(config) as db:
        ready=db.execute("SELECT value FROM settings WHERE key='suggestions_ready'").fetchone()
        rebuilding=db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') AND kind IN ('scan','reindex','reindex_sql','suggest_reports','match') LIMIT 1").fetchone()
        if rebuilding:ready=None
        txt=db.execute("SELECT value FROM settings WHERE key='suggestions_txt'").fetchone()
        sql=db.execute("SELECT value FROM settings WHERE key='suggestions_sql'").fetchone()
        rows=[dict(r) for r in db.execute('SELECT r.*,s.ranking FROM study_suggestions s JOIN match_reports r ON r.id=s.report JOIN studies d ON d.uid=s.uid WHERE s.uid=? AND s.fingerprint=d.fingerprint ORDER BY s.position',(uid,))] if ready and ready[0]=='1' else []
    return {'ready':bool(ready and ready[0]=='1'),'sql_status':sql[0] if sql else 'not checked','txt_status':txt[0] if txt else 'not checked',
            'rows':[dict(source=r['source'],ref=r['ref'],path=r['path'],hash=r['hash'],identity=json.loads(r['evidence']),**json.loads(r['ranking'])) for r in rows]}
