"""One durable worker. Interrupted jobs fail visibly and may be explicitly requeued."""
import json
import time
from pathlib import Path
import engine
from store import audit, database


def process_one(config):
    with database(config) as db:
        db.execute('BEGIN IMMEDIATE')
        job = db.execute("SELECT * FROM jobs WHERE state='queued' ORDER BY id LIMIT 1").fetchone()
        if not job:
            return False
        db.execute("UPDATE jobs SET state='running',message='Starting' WHERE id=?", (job['id'],))
    try:
        payload = json.loads(job['payload'])
        if job['kind'] == 'scan':
            engine.scan(config, job['id'], payload.get('folders'))
        elif job['kind'] == 'match':
            engine.match(config, job['id'])
        elif job['kind'] == 'inspect_sql':
            engine.inspect_sql(config, int(payload['file_id']), job['id'])
        elif job['kind'] == 'prepare':
            engine.prepare(config, payload['uid'], int(payload['report_id']), job['id'])
        elif job['kind'] == 'export':
            engine.export_study(config, payload['uid'], job['id'])
        elif job['kind'] in ('prepare_candidates', 'export_approved'):
            with database(config) as db:
                if job['kind'] == 'prepare_candidates':
                    targets = [dict(r) for r in db.execute("SELECT s.uid,c.report FROM studies s JOIN candidates c ON c.study=s.uid WHERE s.state='candidate'")]
                else:
                    targets = [dict(r) for r in db.execute("SELECT uid FROM studies WHERE state='approved'")]
            failures = 0
            for target in targets:
                try:
                    if job['kind'] == 'prepare_candidates':
                        engine.prepare(config, target['uid'], target['report'], job['id'])
                    else:
                        engine.export_study(config, target['uid'], job['id'])
                except Exception as error:
                    failures += 1
                    message = str(error) if type(error) is ValueError else type(error).__name__ + ': processing failed'
                    with database(config) as db:
                        db.execute('UPDATE studies SET last_error=? WHERE uid=?', (message, target['uid']))
            if failures:
                raise ValueError(f'{failures} of {len(targets)} studies failed; successful studies retained, inspect remaining study reviews')
        else:
            raise ValueError('Unknown job')
        with database(config) as db:
            db.execute("UPDATE jobs SET state='completed',finished=?,message='Completed' WHERE id=?", (time.time(), job['id']))
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
        if not process_one(config):
            time.sleep(2)
