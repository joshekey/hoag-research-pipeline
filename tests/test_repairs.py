import contextlib
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
import install_support
import pipeline
from store import database, enqueue
from worker import process_one
import test_workflow as workflow


class ScanRepairs(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def test_bad_dicom_and_unrelated_files_do_not_abort(self):
        (self.sources[0] / 'bad.dcm').write_bytes(b'SYNTHETIC INVALID FILE')
        (self.sources[0] / 'notes.bin').write_bytes(b'SYNTHETIC OTHER FILE')
        engine.scan(self.config)
        with database(self.config) as db:
            self.assertEqual(db.execute("SELECT status FROM files WHERE path=?", (str(self.sources[0] / 'bad.dcm'),)).fetchone()[0], 'error')
            self.assertEqual(db.execute("SELECT status FROM files WHERE path=?", (str(self.sources[0] / 'notes.bin'),)).fetchone()[0], 'ignored')
            self.assertEqual(db.execute('SELECT count(*) FROM studies').fetchone()[0], 1)

    def test_deleted_files_reconciled_and_failed_traversal_not_reconciled(self):
        self.path.unlink()
        def failed_walk(*args, **kwargs):
            kwargs['onerror'](OSError('synthetic'))
            return iter(())
        with patch('engine.os.walk', side_effect=failed_walk), self.assertRaises(OSError):
            engine.scan(self.config)
        with database(self.config) as db:
            self.assertEqual(db.execute('SELECT active FROM files WHERE path=?', (str(self.path),)).fetchone()[0], 1)
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()[0], '0')
        engine.scan(self.config)
        with database(self.config) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM studies').fetchone()[0], 0)
            self.assertEqual(db.execute('SELECT active FROM files WHERE path=?', (str(self.path),)).fetchone()[0], 0)

    def test_encoding_change_reparses_unchanged_report(self):
        self.config['report_encoding'] = 'utf-16'
        engine.scan(self.config)
        with database(self.config) as db:
            self.assertEqual(db.execute('SELECT status FROM files WHERE path=?', (str(self.report),)).fetchone()[0], 'error')
        self.config['report_encoding'] = 'utf-8-sig'
        engine.scan(self.config)
        with database(self.config) as db:
            self.assertEqual(db.execute('SELECT status FROM files WHERE path=?', (str(self.report),)).fetchone()[0], 'ok')

    def test_batch_flush_and_service_account_file_errors(self):
        for i in range(205):
            (self.sources[0] / f'other-{i}.bin').write_bytes(b'OTHER')
        engine.scan(self.config)
        with database(self.config) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM files WHERE kind='other' AND active=1").fetchone()[0], 205)
        original_stat = Path.stat
        def denied(path, *args, **kwargs):
            if path == self.path:
                raise PermissionError('synthetic permission denied')
            return original_stat(path, *args, **kwargs)
        with patch.object(Path, 'stat', denied):
            engine.scan(self.config)
        with database(self.config) as db:
            self.assertEqual(db.execute('SELECT status FROM files WHERE path=?', (str(self.path),)).fetchone()[0], 'error')
            self.assertEqual(db.execute('SELECT count(*) FROM studies').fetchone()[0], 0)

    def test_update_reservation_blocks_jobs_and_release_preserves_identity(self):
        config_path = self.root / 'config.json'
        config_path.write_text(json.dumps(self.config))
        install_support.check_idle(config_path, reserve=True)
        with self.assertRaisesRegex(ValueError, 'update'):
            enqueue(self.config, 'scan')
        self.assertFalse(process_one(self.config))
        install_support.check_idle(config_path, release=True)
        enqueue(self.config, 'scan')
        with self.assertRaisesRegex(ValueError, 'active'):
            install_support.check_idle(config_path, reserve=True)
        self.assertEqual(json.loads(config_path.read_text())['secret_key'], self.config['secret_key'])

    def test_backup_includes_wal_configuration_and_legacy_catalog(self):
        config_dir = self.root / 'configuration'
        config_dir.mkdir()
        (config_dir / 'config.json').write_text(json.dumps(self.config))
        (config_dir / 'share.credentials').write_text('SYNTHETIC SECRET')
        app, system = self.root / 'application', self.root / 'units'
        app.mkdir(); system.mkdir()
        (app / 'manage.py').write_text('synthetic')
        (system / 'hoag-dashboard.service').write_text('synthetic')
        legacy = self.root / 'legacy'
        legacy.mkdir()
        with contextlib.closing(sqlite3.connect(legacy / 'catalog.sqlite')) as db, db:
            db.execute('CREATE TABLE files (path TEXT)')
        # Keep a WAL writer open to exercise the SQLite online backup API.
        with database(self.config) as writer:
            writer.execute("INSERT INTO settings VALUES ('synthetic_backup','retained')")
            writer.commit()
            dest = install_support.backup(config_dir, app, system, self.root / 'backup', legacy)
        entries = json.loads((dest / 'databases.json').read_text())
        self.assertEqual(len(entries), 2)
        record = next(x for x in entries if x['source'].endswith('workflow.sqlite'))
        with contextlib.closing(sqlite3.connect(dest / record['backup'])) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='synthetic_backup'").fetchone()[0], 'retained')
        self.assertEqual((dest / 'configuration/share.credentials').read_text(), 'SYNTHETIC SECRET')


class LegacyRepairs(unittest.TestCase):
    def test_invalid_kind_change_and_deletion(self):
        with tempfile.TemporaryDirectory() as tmp, contextlib.redirect_stdout(io.StringIO()):
            base = Path(tmp)
            source = base / 'source'; source.mkdir()
            report = source / 'synthetic.txt'
            report.write_text('Synthetic report')
            db = str(base / 'catalog.sqlite')
            self.assertEqual(pipeline.scan(db, source, 'report'), 0)
            self.assertEqual(pipeline.scan(db, source, 'dicom'), 1)
            self.assertEqual(pipeline.summary(db)[0]['status'], 'error')
            self.assertEqual(pipeline.scan(db, source, 'report'), 0)
            report.unlink()
            self.assertEqual(pipeline.scan(db, source, 'report'), 0)
            self.assertEqual(pipeline.summary(db), [])


if __name__ == '__main__':
    unittest.main()
