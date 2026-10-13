"""One durable worker. Interrupted jobs fail visibly and may be explicitly requeued."""
import json
import time
from pathlib import Path
import engine
from store import Cancelled, audit, checkpoint, database


def process_one(config):
    with database(config) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM settings WHERE key='installation_active' AND value='1'").fetchone():
            return False
        job = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
        if not job:
            return False
        db.execute("UPDATE jobs SET state='running',message='Starting',started=? WHERE id=?", (time.time(), job['id']))
    try:
        checkpoint(config, job['id'])
        payload = json.loads(job['payload'])
        completion = 'Completed'
        if job['kind'] == 'scan':
            engine.scan(config, job['id'], payload.get('folders'))
        elif job['kind'] == 'add_dicom':
            if not payload.get('folders'):raise ValueError('Select DICOM and report folders to import')
            engine.scan(config,job['id'],payload['folders'],additive=True)
        elif job['kind'] == 'add_reports':
            if not payload.get('folders'):raise ValueError('Select TXT/SQL source folders to import')
            engine.scan(config,job['id'],payload['folders'],add_reports=True)
        elif job['kind'] == 'reindex':
            with database(config) as db:
                saved=db.execute("SELECT value FROM settings WHERE key='scan_folders'").fetchone()
            engine.scan(config,job['id'],json.loads(saved[0]) if saved else None,force=True)
        elif job['kind'] == 'reindex_sql':
            from sql_gui_candidates import request_broker
            request_broker({'action':'reindex_start','job_id':job['id'],'file_id':int(payload['file_id'])})
            while True:
                result=request_broker({'action':'reindex_status','job_id':job['id']})
                if result['state']=='completed':
                    checkpoint(config,job['id']);break
                if result['state']=='failed': raise ValueError(result['error'])
                from store import progress
                progress(config,job['id'],result.get('progress',0),'Reading SQL dump; old index remains available','SQL reindex')
                # Broker callback observes cancellation before publishing the index.
                time.sleep(1)
        elif job['kind'] == 'suggest_reports':
            from research_matching import build
            build(config,job['id'])
        elif job['kind'] == 'match':
            engine.match(config, job['id'])
        elif job['kind'] == 'validate_export':
            engine.validate_export(config, int(payload['export_id']), job['id'])
        elif job['kind'] == 'inspect_sql':
            engine.inspect_sql(config, int(payload['file_id']), job['id'])
        elif job['kind'] == 'prepare':
            engine.prepare(config, payload['uid'], int(payload['report_id']), job['id'])
        elif job['kind'] in ('prepare_patient', 'export_patient'):
            from patient_worklist import process
            completion = process(config, job['kind'], payload, job['id'])
        elif job['kind'] == 'export':
            engine.export_study(config, payload['uid'], job['id'])
        elif job['kind'] in ('prepare_candidates', 'export_approved'):
            with database(config) as db:
                if job['kind'] == 'prepare_candidates':
                    targets = [dict(r) for r in db.execute("SELECT s.uid,c.report FROM studies s JOIN candidates c ON c.study=s.uid WHERE s.state='candidate'")]
                else:
                    targets = [dict(r) for r in db.execute("SELECT uid FROM studies WHERE state='approved'")]
            failures = 0
            for index, target in enumerate(targets, 1):
                checkpoint(config, job['id'])
                try:
                    if job['kind'] == 'prepare_candidates':
                        engine.prepare(config, target['uid'], target['report'], job['id'])
                    else:
                        engine.export_study(config, target['uid'], job['id'])
                except Cancelled:
                    raise
                except Exception as error:
                    failures += 1
                    message = str(error) if type(error) is ValueError else type(error).__name__ + ': processing failed'
                    with database(config) as db:
                        db.execute('UPDATE studies SET last_error=? WHERE uid=?', (message, target['uid']))
            if failures:
                raise ValueError(f'{failures} of {len(targets)} studies failed; successful studies retained, inspect remaining study reviews')
        else:
            raise ValueError('Unknown job')
        if job['kind'] in ('scan','reindex','reindex_sql','match','add_reports'):
            from research_matching import build
            build(config,job['id'])
        with database(config) as db:
            db.execute("UPDATE jobs SET state='completed',finished=?,message=? WHERE id=?", (time.time(), completion, job['id']))
    except Cancelled:
        with database(config) as db:
            db.execute("UPDATE jobs SET state='cancelled',finished=?,message='Cancelled at a safe boundary; partial exports remain in restricted staging' WHERE id=?", (time.time(), job['id']))
            audit(db, 'worker', 'job-cancelled', str(job['id']))
    except Exception as error:
        # ValueError messages are authored by this application. Never log arbitrary PHI-bearing exception text.
        message = str(error) if type(error) is ValueError else type(error).__name__ + ': processing failed; check mounts, encoding, dependencies and supported DICOM types'
        with database(config) as db:
            db.execute("UPDATE jobs SET state='failed',finished=?,message=? WHERE id=?", (time.time(), message, job['id']))
            if job['kind'] in ('prepare', 'export'):
                db.execute('UPDATE studies SET last_error=? WHERE uid=?', (message, json.loads(job['payload']).get('uid')))
            audit(db, 'worker', 'job-failed', str(job['id']))
    return True


def run(config):
    import fcntl
    lock = (Path(config['state_dir']) / 'worker.lock').open('w')
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with database(config) as db:
        db.execute("UPDATE jobs SET state='failed',message='Interrupted by restart; requeue explicitly',finished=? WHERE state='running'", (time.time(),))
    while True:
        if config.get('config_path'):
            from store import load_config
            fresh=load_config(config['config_path'])
            config['source_roots']=fresh['source_roots']
            config['local_source_roots']=fresh.get('local_source_roots',[])
        if not process_one(config):
            time.sleep(2)
