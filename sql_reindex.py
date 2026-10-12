"""Broker-side rebuild of one cataloged dump; atomic publication preserves old index."""
import json
import threading
import sqlite3
from contextlib import closing
from pathlib import Path
import engine
from store import Cancelled
from sql_report_index import build

lock=threading.Lock()
current={}


def request(config, index, data):
    job_id=data.get('job_id')
    if type(job_id) is not int or set(data)-{'action','job_id','file_id'}:
        raise ValueError('Invalid SQL reindex request')
    with lock:
        if data['action']=='reindex_status':
            if current.get('job_id')!=job_id: raise ValueError('SQL reindex interrupted; retry the job')
            return dict(current)
        with closing(sqlite3.connect((Path(config['state_dir'])/'workflow.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            job=db.execute("SELECT * FROM jobs WHERE id=? AND kind='reindex_sql' AND state='running'",(job_id,)).fetchone()
            file_id=data.get('file_id')
            if not job or json.loads(job['payload']).get('file_id')!=file_id:
                raise ValueError('SQL reindex requires its running worker job')
            row=db.execute("SELECT * FROM files WHERE id=? AND kind='sql' AND active=1 AND status='ok'",(file_id,)).fetchone()
            if not row: raise ValueError('Select an indexed SQL dump')
            row=dict(row)
        if current.get('state')=='running': raise ValueError('SQL rebuild already running')
        if Path(row['root']).resolve() not in [Path(root).resolve() for root in config['source_roots']]:
            raise ValueError('SQL source is outside configured shares')
        source=engine.source_path(config,row)
        current.clear();current.update(job_id=job_id,state='running')
        def rebuild():
            staging=index.parent/('rebuild-'+str(job_id)+'.sqlite')
            try:
                staging.unlink(missing_ok=True)
                def tick(count):
                    engine.source_path(config,row)
                    with closing(sqlite3.connect((Path(config['state_dir'])/'workflow.sqlite').as_uri()+'?mode=ro',uri=True)) as db:
                        job=db.execute('SELECT cancel_requested,state FROM jobs WHERE id=?',(job_id,)).fetchone()
                        if not job or job[0] or job[1]!='running': raise Cancelled('SQL rebuild cancelled')
                    with lock: current.update(progress=count)
                totals,_=build(source,staging,independent=True,callback=tick)
                tick(current.get('progress',0))
                if not totals['narratives']: raise ValueError('No supported SQL narratives; old index retained')
                staging.replace(index)
                with lock: current.update(state='completed',total=totals['narratives'])
            except ValueError as error:
                with lock:current.update(state='failed',error='SQL rebuild failed: '+str(error)+'. Previous index retained. Inspect the export schema in Operations.')
            except Exception:
                with lock: current.update(state='failed',error='SQL rebuild failed or cancelled; old index retained. Check dump schema, relationships, mount, and available disk space.')
            finally: staging.unlink(missing_ok=True)
        threading.Thread(target=rebuild,daemon=True).start()
        return dict(current)
