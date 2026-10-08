"""Local discovery catalog. Contains PHI. Does not anonymize or export."""
import argparse
from contextlib import closing
import json
import os
import re
import sqlite3
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

TAGS = ['StudyInstanceUID', 'SOPInstanceUID', 'AccessionNumber', 'PatientID', 'StudyDate', 'Modality']


def connect(path):
    db = sqlite3.connect(path, timeout=30)
    db.execute('PRAGMA journal_mode=WAL')
    db.execute('''CREATE TABLE IF NOT EXISTS files (
        path TEXT PRIMARY KEY, kind TEXT, size INTEGER, mtime INTEGER,
        metadata TEXT, status TEXT)''')
    return db


def scan(dbpath, root, kind):
    import pydicom
    root = Path(root).resolve(strict=True)
    if not root.is_dir():
        raise ValueError('Source must be a directory')
    db = connect(dbpath)
    total = skipped = failed = 0
    def walk_error(error):
        # Do not print potentially identifying source paths.
        raise RuntimeError('Directory traversal failed; check share permissions') from None
    try:
        for directory, _, names in os.walk(root, followlinks=False, onerror=walk_error):
            for name in names:
                path = Path(directory) / name
                if path.is_symlink() or (kind == 'report' and path.suffix.lower() != '.txt'):
                    continue
                stat = path.stat()
                key = str(path)
                previous = db.execute('SELECT size,mtime,status FROM files WHERE path=?', (key,)).fetchone()
                if previous == (stat.st_size, stat.st_mtime_ns, 'ok'):
                    skipped += 1
                    continue
                metadata, status = {}, 'ok'
                try:
                    if kind == 'dicom':
                        ds = pydicom.dcmread(path, stop_before_pixels=True, specific_tags=TAGS)
                        if not ds.get('StudyInstanceUID') or not ds.get('SOPInstanceUID'):
                            raise ValueError('Missing study or instance UID')
                        metadata = {tag: str(ds.get(tag, '')) for tag in TAGS}
                    else:
                        # Bounded preview for discovery; not an NLP de-identification pass.
                        with path.open('rb') as source:
                            raw = source.read(1024 * 1024 + 1)
                        metadata = {'text': raw[:1024 * 1024].decode('utf-8-sig', errors='replace'),
                                    'truncated': len(raw) > 1024 * 1024}
                    after = path.stat()
                    if (after.st_size, after.st_mtime_ns) != (stat.st_size, stat.st_mtime_ns):
                        raise ValueError('Source changed during read')
                except (OSError, ValueError, EOFError, UnicodeError):
                    status = 'error'
                    metadata = {}
                    failed += 1
                db.execute('INSERT OR REPLACE INTO files VALUES (?,?,?,?,?,?)',
                           (key, kind, stat.st_size, stat.st_mtime_ns, json.dumps(metadata), status))
                db.commit()  # Checkpoint each file so interrupted scans resume.
                total += 1
        print(json.dumps({'indexed': total, 'unchanged': skipped, 'errors': failed}))
    finally:
        db.close()
    return 1 if failed else 0


def summary(dbpath):
    with closing(sqlite3.connect(Path(dbpath).resolve().as_uri() + '?mode=ro', uri=True)) as db:
        return [dict(kind=k, status=s, count=n, bytes=b) for k, s, n, b in db.execute(
            'SELECT kind,status,count(*),sum(size) FROM files GROUP BY kind,status')]


def sql_type(path):
    with open(path, 'rb') as source:
        header = source.read(65536).decode('utf-8-sig', errors='replace')
    signatures = {'PostgreSQL': r'PostgreSQL database dump|^PGDMP',
                  'MySQL/MariaDB': r'MySQL dump|MariaDB dump',
                  'SQL Server': r'\bGO\s*$|\[dbo\]',
                  'SQLite': r'SQLite format 3|PRAGMA foreign_keys'}
    guesses = [name for name, pattern in signatures.items() if re.search(pattern, header, re.M | re.I)]
    print(json.dumps({'possible_database_types': guesses or ['Unknown'], 'executed': False}))


def dashboard(dbpath):
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path not in ('/', '/api/status'):
                self.send_error(404)
                return
            try:
                rows = summary(dbpath)
            except sqlite3.Error:
                self.send_error(503, 'Run an initial scan first')
                return
            if self.path == '/api/status':
                body = json.dumps(rows).encode()
                content_type = 'application/json'
            else:
                table = ''.join(f'<tr><td>{r["kind"]}</td><td>{r["status"]}</td><td>{r["count"]:,}</td><td>{r["bytes"] / 2**30:.2f} GiB</td></tr>' for r in rows)
                body = ('''<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>HOAG Research Discovery</title>
                <style>body{background:#101b2c;color:#e8eef8;font:17px system-ui;max-width:1000px;margin:70px auto;padding:24px}h1{font-size:42px}small{color:#7dd3fc}section{background:#1c2b40;padding:28px;border-radius:18px;margin:24px 0}table{width:100%;text-align:left;border-collapse:collapse}td,th{padding:16px;border-bottom:1px solid #34465e}p{line-height:1.6}</style>
                <small>HOAG / RESEARCH DATA OPERATIONS</small><h1>Discovery dashboard</h1><p>Read-only source inventory. Refresh to see scan progress.</p><section><table><tr><th>Source</th><th>Status</th><th>Files</th><th>Indexed source size</th></tr>'''
                        + table + '''</table></section><section><b>Discovery phase</b><p>Matching, anonymization, image viewing, and research exports are not enabled in this version. Catalog contents contain identifying data and must remain inside the hospital environment.</p></section></html>''').encode()
                content_type = 'text/html; charset=utf-8'
            self.send_response(200)
            self.send_header('Content-Type', content_type)
            self.send_header('Cache-Control', 'no-store')
            self.send_header('X-Content-Type-Options', 'nosniff')
            self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    HTTPServer(('127.0.0.1', 8080), Handler).serve_forever()


def main():
    os.umask(0o077)
    parser = argparse.ArgumentParser(description=__doc__)
    subs = parser.add_subparsers(dest='command', required=True)
    for command in ('scan', 'status', 'dashboard'):
        sub = subs.add_parser(command)
        sub.add_argument('--db', required=True)
        if command == 'scan':
            sub.add_argument('--root', required=True)
            sub.add_argument('--kind', choices=('dicom', 'report'), required=True)
    sub = subs.add_parser('inspect-sql')
    sub.add_argument('path')
    args = parser.parse_args()
    if args.command == 'scan':
        return scan(args.db, args.root, args.kind)
    if args.command == 'status':
        print(json.dumps(summary(args.db)))
    elif args.command == 'inspect-sql':
        sql_type(args.path)
    else:
        dashboard(args.db)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
