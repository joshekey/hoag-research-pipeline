"""Authenticated, source-independent discovery and deliberate manual association."""
import json
import time
import engine
from store import database, idle, audit, token
from sql_gui_candidates import request_broker
from research_matching import evidence


def search(config, body):
    source=body.get('source','all'); q=body.get('q',''); offset=body.get('offset',0); sort=body.get('sort','date')
    if source not in ('all','txt','sql') or not isinstance(q,str) or len(q)>120 or type(offset) is not int or offset<0:
        raise ValueError('Invalid report search')
    order={'filename':'f.path','patient':"json_extract(f.metadata,'$.name_hint')",'date':'f.mtime DESC'}.get(sort)
    if not order: raise ValueError('Invalid report sort')
    groups=[]; errors=[]
    if source in ('all','txt'):
        with database(config) as db:
            where="f.kind='report' AND f.active=1 AND f.status='ok'"
            params=[]
            if q:
                where+=' AND report_search MATCH ?';params=[engine.fts_phrase(q)]
                join=' JOIN report_search ON report_search.rowid=f.id'
            else: join=''
            count=db.execute('SELECT count(*) FROM files f'+join+' WHERE '+where,params).fetchone()[0]
            rows=[{'source':'txt','ref':str(r['id']),'path':r['path'],'name':json.loads(r['metadata']).get('name_hint',''),'date':''}
                  for r in db.execute('SELECT f.* FROM files f'+join+' WHERE '+where+' ORDER BY '+order+',f.id LIMIT 100 OFFSET ?',params+[offset])]
        groups.append({'source':'txt','rows':rows,'total':count})
    if source in ('all','sql'):
        try:
            result=request_broker({'action':'browse','q':q,'offset':offset,'sort':sort})
            groups.append(dict(result,source='sql'))
        except ValueError as error: errors.append('SQL: '+str(error))
    return {'groups':groups,'errors':errors,'offset':offset}


def preview(config, source, ref):
    if source=='sql':
        from report_format import format_report
        result=dict(request_broker({'action':'browse_preview','token':ref}))
        result['formatted'],result['format_status']=format_report(result['text'])
        # Older broker responses may omit evidence that is present in the narrative.
        extracted=evidence(result['text'])
        supplied=result.get('identity') or {}
        result['identity']={key: supplied.get(key) or value for key,value in extracted.items()}
        result['identity'].update({key:value for key,value in supplied.items() if value})
        procedure=evidence(result.get('procedure',''))
        for key in ('modalities','anatomy'):
            result['identity'][key]=sorted(set(result['identity'].get(key,[]))|set(procedure[key]))
        return result
    if source!='txt': raise ValueError('Invalid report source')
    with database(config) as db:
        row=db.execute("SELECT * FROM files WHERE id=? AND kind='report' AND active=1 AND status='ok'",(int(ref),)).fetchone()
    if not row: raise ValueError('Report unavailable')
    value,hash_value=engine.read_report(config,row)
    if hash_value!=json.loads(row['metadata'])['hash']: raise ValueError('Report changed; reindex')
    return {'source':'txt','ref':str(row['id']),'text':value,'hash':hash_value,'identity':evidence(value,str(row['path'])),'path':row['path']}


def associate(config, uid, body, actor):
    if body.get('confirm') is not True: raise ValueError('Confirm the patient and examination relationship')
    source=body.get('source'); ref=body.get('ref'); result=preview(config,source,ref)
    if result['hash']!=body.get('hash'): raise ValueError('Preview changed; preview again')
    note=body.get('note','')
    if not isinstance(note,str) or len(note)>2000: raise ValueError('Invalid association note')
    with database(config) as db:
        db.execute('BEGIN IMMEDIATE');idle(db);engine.require_scan(db)
        study=db.execute('SELECT * FROM studies WHERE uid=?',(uid,)).fetchone()
        if not study: raise ValueError('Study unavailable')
        if source=='sql':
            path='sql-report:'+ref
            metadata=json.dumps({'hash':result['hash'],'sql_ref':ref})
            db.execute("INSERT INTO files(path,root,kind,size,mtime,metadata,status,active,seen) VALUES (?,'','sql_report',0,0,?,'ok',1,0) ON CONFLICT(path) DO UPDATE SET metadata=excluded.metadata,active=1",(path,metadata))
            report_id=db.execute('SELECT id FROM files WHERE path=?',(path,)).fetchone()[0]
        else: report_id=int(ref)
        db.execute('INSERT OR REPLACE INTO browser_associations VALUES (?,?,?,?,?,?,?)',(uid,report_id,study['fingerprint'],result['hash'],actor,time.time(),note))
        db.execute("UPDATE studies SET report_id=?,report_hash=NULL,sanitized=NULL,approved_hash=NULL,approved_fingerprint=NULL,state=CASE WHEN state='conflict' THEN state ELSE 'needs_review' END WHERE uid=?",(report_id,uid))
        audit(db,actor,'manual-report-association',token(config,'study',uid)[:20])
    return {'associated':True,'approved':False,'report_id':report_id}


def authorized(config, uid, report_id):
    with database(config) as db:
        row=db.execute('SELECT a.report_hash,f.metadata FROM browser_associations a JOIN studies s ON s.uid=a.uid JOIN files f ON f.id=a.report_id WHERE a.uid=? AND a.report_id=? AND a.fingerprint=s.fingerprint AND f.active=1',(uid,report_id)).fetchone()
    return bool(row and row['report_hash']==json.loads(row['metadata']).get('hash'))
