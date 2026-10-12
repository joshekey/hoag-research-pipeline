"""Independent report discovery: records identify themselves, never a selected study."""
import hashlib
import hmac
import json
import sqlite3
import time
from contextlib import closing
from strict_identity_match import extract_header
from research_matching import evidence
from sql_dicom_pilot import text


def query(index, key, data):
    action = data.get('action')
    if action not in ('browse', 'browse_preview', 'match_catalog') or set(data) - {'action','q','offset','sort','token','after'}:
        raise ValueError('Invalid report browser request')
    snapshot = str(index.stat().st_mtime_ns) + ':' + str(index.stat().st_size)
    def token(rid, narrative, procedure, date):
        values = [snapshot, (rid if isinstance(rid,bytes) else str(rid).encode()).hex(), text(narrative), text(procedure), text(date)]
        return (rid if isinstance(rid,bytes) else str(rid).encode()).hex()+':'+hmac.new(key, b'report-browser:' + json.dumps(values).encode(), hashlib.sha256).hexdigest()
    with closing(sqlite3.connect(index.as_uri()+'?mode=ro', uri=True)) as db:
        deadline = time.monotonic()+10
        db.set_progress_handler(lambda: int(time.monotonic()>deadline), 10000)
        if action == 'match_catalog':
            offset=data.get('offset',0)
            if type(offset) is not int or offset<0:raise ValueError('Invalid report evidence page')
            after=data.get('after','')
            try: after_id=bytes.fromhex(after)
            except (ValueError,TypeError):raise ValueError('Invalid evidence cursor') from None
            count=db.execute('SELECT count(*) FROM narratives').fetchone()[0] if not after else None
            rows=[]
            for rid,narrative,procedure,date in db.execute('SELECT report_id,text,procedure_name,result_date FROM narratives WHERE report_id>? ORDER BY report_id LIMIT 100',(after_id,)):
                value=text(narrative)
                info=evidence(value)
                procedure_info=evidence(text(procedure))
                info['modalities']=sorted(set(info['modalities'])|set(procedure_info['modalities']))
                info['anatomy']=sorted(set(info['anatomy'])|set(procedure_info['anatomy']))
                # Result dates are retained as supporting context, never promoted to exam dates.
                info['result_date']=text(date)
                rows.append({'source':'sql','ref':token(rid,narrative,procedure,date),'path':text(procedure) or 'SQL report',
                             'hash':hashlib.sha256(value.encode()).hexdigest(),'evidence':info})
            return {'rows':rows,'total':count,'snapshot':snapshot,'has_more':len(rows)==100,'next_after':rows[-1]['ref'].split(':')[0] if rows else after}
        if action == 'browse_preview':
            wanted = data.get('token','')
            if not isinstance(wanted,str) or len(wanted)>1024 or ':' not in wanted:
                raise ValueError('Invalid report reference')
            try: reference=bytes.fromhex(wanted.split(':')[0])
            except ValueError: raise ValueError('Invalid report reference') from None
            for rid, narrative, procedure, date in db.execute('SELECT report_id,text,procedure_name,result_date FROM narratives WHERE report_id=?',(reference,)):
                if hmac.compare_digest(token(rid,narrative,procedure,date), wanted):
                    if len(narrative)>2*1024*1024:
                        raise ValueError('Report exceeds preview size limit')
                    value = text(narrative)
                    return {'source':'sql','ref':wanted,'text':value,'hash':hashlib.sha256(value.encode()).hexdigest(),
                            'identity':evidence(value),'procedure':text(procedure),'date':text(date)}
            raise ValueError('SQL report changed or is unavailable; search again')
        q = data.get('q','')
        offset = data.get('offset',0)
        sort = {'date':'result_date','filename':'report_id','patient':'report_id'}.get(data.get('sort','date'))
        if not isinstance(q,str) or len(q)>120 or type(offset) is not int or offset<0 or not sort:
            raise ValueError('Invalid search parameters')
        where = "instr(lower(CAST(text AS TEXT)),lower(?))>0 OR instr(lower(CAST(procedure_name AS TEXT)),lower(?))>0"
        params=(q,q)
        if not q:
            where='1=1';params=()
        elif db.execute("SELECT 1 FROM sqlite_master WHERE name='narrative_search'").fetchone():
            where='rowid IN (SELECT rowid FROM narrative_search WHERE narrative_search MATCH ?)'
            params=('"'+q.replace('"','""')+'"',)
        count = db.execute('SELECT count(*) FROM narratives WHERE '+where,params).fetchone()[0]
        rows=[]
        for rid,narrative,procedure,date in db.execute('SELECT report_id,text,procedure_name,result_date FROM narratives WHERE '+where+' ORDER BY '+sort+',report_id LIMIT 100 OFFSET ?',(*params,offset)):
            header=evidence(text(narrative))
            rows.append({'source':'sql','ref':token(rid,narrative,procedure,date),'path':text(procedure) or 'SQL report',
                         'name':header.get('name',''),'date':text(date)})
        return {'rows':rows,'total':count}
