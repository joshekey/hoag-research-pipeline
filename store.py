"""Restricted local catalog and a durable single-worker job queue."""
import contextlib
import hashlib
import hmac
import json
import sqlite3
import time
from pathlib import Path


def load_config(path):
    config = json.loads(Path(path).read_text())
    config['config_path'] = str(Path(path).resolve())
    return config


@contextlib.contextmanager
def database(config):
    path = Path(config['state_dir'])
    path.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(path / 'workflow.sqlite', timeout=60)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA foreign_keys=ON')
    db.execute('PRAGMA journal_mode=WAL')
    try:
        yield db
        db.commit()
    except BaseException:
        db.rollback()
        raise
    finally:
        db.close()


def initialize(config):
    with database(config) as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS files (
          id INTEGER PRIMARY KEY, path TEXT UNIQUE NOT NULL, root TEXT NOT NULL,
          kind TEXT NOT NULL, size INTEGER, mtime INTEGER, metadata TEXT NOT NULL,
          status TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1, seen TEXT);
        CREATE INDEX IF NOT EXISTS files_kind ON files(kind,active,status);
        CREATE INDEX IF NOT EXISTS files_study ON files(json_extract(metadata,'$.StudyInstanceUID'))
          WHERE kind='dicom' AND active=1 AND status='ok';
        CREATE TABLE IF NOT EXISTS studies (
          uid TEXT PRIMARY KEY, subject TEXT NOT NULL, accession TEXT, patient TEXT,
          name TEXT, date TEXT, modality TEXT, count INTEGER, size INTEGER,
          fingerprint TEXT NOT NULL, report_id INTEGER REFERENCES files(id),
          report_hash TEXT, sanitized TEXT, redactions TEXT NOT NULL DEFAULT '[]',
          source_hashes TEXT NOT NULL DEFAULT '{}',
          last_error TEXT NOT NULL DEFAULT '',
          state TEXT NOT NULL DEFAULT 'unmatched', review_note TEXT,
          approved_fingerprint TEXT, approved_hash TEXT);
        CREATE TABLE IF NOT EXISTS candidates (
          study TEXT REFERENCES studies(uid) ON DELETE CASCADE,
          report INTEGER REFERENCES files(id), reason TEXT,
          PRIMARY KEY(study,report));
        CREATE TABLE IF NOT EXISTS report_keys (
          key TEXT NOT NULL, report INTEGER REFERENCES files(id), reason TEXT NOT NULL,
          PRIMARY KEY(key,report,reason));
        CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        INSERT OR IGNORE INTO settings VALUES ('scan_complete','0');
        CREATE TABLE IF NOT EXISTS jobs (
          id INTEGER PRIMARY KEY, kind TEXT NOT NULL, payload TEXT NOT NULL,
          state TEXT NOT NULL DEFAULT 'queued', progress INTEGER NOT NULL DEFAULT 0,
          message TEXT NOT NULL DEFAULT '', created REAL NOT NULL, finished REAL);
        CREATE TABLE IF NOT EXISTS audit (
          id INTEGER PRIMARY KEY, at REAL NOT NULL, actor TEXT NOT NULL,
          action TEXT NOT NULL, target TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS exports (
          id INTEGER PRIMARY KEY, study TEXT NOT NULL, folder TEXT NOT NULL,
          manifest_hash TEXT NOT NULL, created REAL NOT NULL);
        ''')
        columns = {r['name'] for r in db.execute('PRAGMA table_info(studies)')}
        for name, declaration in [('source_hashes', "TEXT NOT NULL DEFAULT '{}'"), ('last_error', "TEXT NOT NULL DEFAULT ''")]:
            if name not in columns:
                db.execute(f'ALTER TABLE studies ADD COLUMN {name} {declaration}')


def token(config, namespace, value):
    return hmac.new(config['secret_key'].encode(), (namespace + '\0' + value).encode(), hashlib.sha256).hexdigest()


def research_uid(config, value):
    return '2.25.' + str(int(token(config, 'uid', value)[:32], 16))


def audit(db, actor, action, target=''):
    db.execute('INSERT INTO audit(at,actor,action,target) VALUES (?,?,?,?)',
               (time.time(), actor, action, target))


def enqueue(config, kind, payload=None, actor='admin'):
    if kind not in ('scan', 'match', 'prepare', 'prepare_candidates', 'export', 'export_approved', 'inspect_sql'):
        raise ValueError('Unknown job type')
    with database(config) as db:
        db.execute('BEGIN IMMEDIATE')
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():
            raise ValueError('Another job is queued or running')
        cursor = db.execute('INSERT INTO jobs(kind,payload,created) VALUES (?,?,?)',
                            (kind, json.dumps(payload or {}), time.time()))
        audit(db, actor, 'queue:' + kind, str(cursor.lastrowid))
        return cursor.lastrowid


def idle(db):
    if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():
        raise ValueError('Wait for the current job before changing a review')


def progress(config, job, count, message):
    if job is not None:
        with database(config) as db:
            db.execute('UPDATE jobs SET progress=?,message=? WHERE id=?', (count, message, job))
