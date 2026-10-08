import base64
import json
import sys
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
import mount_service
from store import Cancelled, checkpoint, database, enqueue, initialize, progress
import test_workflow as workflow
from webapp import create_app
from worker import process_one


class DashboardTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    study = workflow.WorkflowTests.study
    prepare = workflow.WorkflowTests.prepare
    approve = workflow.WorkflowTests.approve
    def client_auth(self):
        client = create_app(self.config).test_client()
        auth = {'Authorization': 'Basic ' + base64.b64encode(b'admin:synthetic-password').decode()}
        client.get('/', headers=auth)
        with client.session_transaction() as session:
            auth['X-CSRF-Token'] = session['csrf']
        return client, auth

    def test_paged_catalog_filters_and_sort_allowlist(self):
        client, auth = self.client_auth()
        result = client.get('/api/studies?paged=1&state=candidate&modality=CT&from=2019-01-01&to=2021-01-01', headers=auth).get_json()
        self.assertEqual(result['total'], 1)
        self.assertEqual(result['rows'][0]['uid'], self.uid)
        self.assertEqual(client.get('/api/studies?paged=1&state=approved', headers=auth).get_json()['total'], 0)
        self.assertEqual(client.get('/api/studies?sort=uid;DROP TABLE studies', headers=auth).status_code, 400)
        self.assertEqual(client.get('/api/studies?from=invalid', headers=auth).status_code, 400)

    def test_cancel_queued_and_retry_preserves_scope(self):
        client, auth = self.client_auth()
        scope = {'folders': [{'root': 0, 'relative': '.'}]}
        job = enqueue(self.config, 'scan', scope)
        self.assertEqual(client.post(f'/api/jobs/{job}/cancel', json={}, headers=auth).status_code, 200)
        with database(self.config) as db:
            self.assertEqual(db.execute('SELECT state FROM jobs WHERE id=?', (job,)).fetchone()[0], 'cancelled')
        retried = client.post(f'/api/jobs/{job}/retry', json={}, headers=auth).get_json()['id']
        with database(self.config) as db:
            self.assertEqual(json.loads(db.execute('SELECT payload FROM jobs WHERE id=?', (retried,)).fetchone()[0]), scope)

    def test_cancel_scan_blocks_export_until_rescan(self):
        job = enqueue(self.config, 'scan')
        with database(self.config) as db:
            db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (job,))
        # Simulate cancellation during traversal rather than before the job starts.
        with patch('engine.selection', side_effect=Cancelled()):
            process_one(self.config)
        # The initial checkpoint stopped before touching readiness; now cancel inside scan.
        with database(self.config) as db:
            db.execute("UPDATE jobs SET state='queued',cancel_requested=0 WHERE id=?", (job,))
        with patch('engine.selection', side_effect=Cancelled()):
            process_one(self.config)
        with database(self.config) as db:
            self.assertEqual(db.execute('SELECT state FROM jobs WHERE id=?', (job,)).fetchone()[0], 'cancelled')
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()[0], '0')
        with self.assertRaises(ValueError):
            engine.match(self.config)

    def test_running_cancel_and_mount_routes_require_csrf(self):
        client, auth = self.client_auth()
        job = enqueue(self.config, 'scan')
        with database(self.config) as db:
            db.execute("UPDATE jobs SET state='running' WHERE id=?", (job,))
        self.assertEqual(client.post(f'/api/jobs/{job}/cancel', json={}, headers=auth).status_code, 200)
        with self.assertRaises(Cancelled):
            checkpoint(self.config, job)
        no_csrf = {'Authorization': auth['Authorization']}
        self.assertEqual(client.post('/api/mount', json={}, headers=no_csrf).status_code, 403)
        self.assertEqual(client.post('/api/mount', json={}).status_code, 401)

    def test_health_candidate_text_and_status(self):
        client, auth = self.client_auth()
        self.assertTrue(all(r['ok'] for r in client.get('/api/health', headers=auth).get_json()['shares']))
        with database(self.config) as db:
            report = db.execute("SELECT id FROM files WHERE kind='report'").fetchone()[0]
        self.assertIn('Clear lungs', client.get(f'/api/report/{report}', headers=auth).get_json()['text'])
        status = client.get('/api/status', headers=auth).get_json()
        self.assertEqual(status['settings']['scan_complete'], '1')
        self.assertIn('CT', status['modalities'])

    def test_package_validation_detects_tampering(self):
        self.approve()
        folder = engine.export_study(self.config, self.uid)
        with database(self.config) as db:
            export_id = db.execute('SELECT id FROM exports').fetchone()[0]
        engine.validate_export(self.config, export_id)
        with database(self.config) as db:
            row = db.execute('SELECT * FROM exports WHERE id=?', (export_id,)).fetchone()
            self.assertGreater(row['bytes'], 0)
            self.assertEqual(row['validation'], 'Checksums verified')
        (folder / 'Report' / 'report.txt').write_text('altered')
        with self.assertRaises(ValueError):
            engine.validate_export(self.config, export_id)
        with database(self.config) as db:
            self.assertEqual(db.execute('SELECT validation FROM exports WHERE id=?', (export_id,)).fetchone()[0], 'Validation failed')

    def broker(self):
        base = self.root / 'etc';base.mkdir()
        fstab = base / 'fstab';fstab.write_text('# existing unrelated entries\n')
        targets = dict(zip(['bulk','imaging','output'], [*self.config['source_roots'], self.config['output_root']]))
        account = types.SimpleNamespace(pw_uid=1001, pw_gid=1001)
        return base, fstab, targets, types.SimpleNamespace(getpwnam=lambda _: account)

    def test_mount_validation_rejects_arbitrary_targets_and_controls(self):
        for body in [{'slot':'/etc'}, {'slot':'bulk','unc':'//server/share','username':'u','password':'p\nmalicious','confirm':True}, {'slot':'bulk','unc':'//server/share/sub','username':'u','password':'p','confirm':True}, {'slot':'bulk','unc':'//server/share','username':'u','password':'p'}]:
            with self.assertRaises(ValueError):
                mount_service.validate(body)
        valid = mount_service.validate({'slot':'bulk','unc':r'\\server\share name','username':'u','domain':'d','password':'p','confirm':True})
        self.assertEqual(valid['unc'],'//server/share name')

    def test_mount_saves_private_credentials_and_fixed_readonly_options(self):
        base, fstab, targets, pwd = self.broker()
        body = {'slot':'bulk','unc':'//server/share name','username':'synthetic','domain':'domain','password':'synthetic-secret','confirm':True}
        with patch.dict(sys.modules, {'pwd':pwd}), patch.multiple(mount_service, BASE=base,FSTAB=fstab,TARGETS=targets), patch('mount_service.command') as command, patch('mount_service.os.path.ismount', return_value=False):
            mount_service.apply(body, self.config)
            calls = str(command.call_args_list)
        self.assertNotIn('synthetic-secret', calls)
        self.assertIn(' ro,credentials=', fstab.read_text())
        self.assertIn('vers=3.1.1,seal', fstab.read_text())
        credential=base / (Path(targets['bulk']).name+'.credentials')
        self.assertIn('password=synthetic-secret', credential.read_text())
        if sys.platform != 'win32':
            self.assertEqual(credential.stat().st_mode & 0o777, 0o600)
        with database(self.config) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()[0], '0')
            self.assertNotIn('synthetic-secret', str([tuple(r) for r in db.execute('SELECT * FROM audit')]))

    def test_mount_failure_restores_fstab_and_credentials(self):
        base, fstab, targets, pwd = self.broker()
        original=fstab.read_text();credential=base / (Path(targets['bulk']).name+'.credentials');credential.write_text('old-settings')
        body={'slot':'bulk','unc':'//server/share','username':'u','password':'new-secret','confirm':True}
        with patch.dict(sys.modules, {'pwd':pwd}), patch.multiple(mount_service, BASE=base,FSTAB=fstab,TARGETS=targets), patch('mount_service.os.path.ismount', return_value=False), patch('mount_service.command', side_effect=OSError('synthetic-secret')):
            with self.assertRaisesRegex(ValueError, 'Connection failed') as raised:
                mount_service.apply(body,self.config)
        self.assertNotIn('synthetic-secret',str(raised.exception))
        self.assertEqual(fstab.read_text(),original)
        self.assertEqual(credential.read_text(),'old-settings')

    def test_mount_blocked_during_active_job(self):
        base, fstab, targets, pwd = self.broker();original=fstab.read_text()
        enqueue(self.config,'scan')
        body={'slot':'bulk','unc':'//server/share','username':'u','password':'p','confirm':True}
        with patch.dict(sys.modules, {'pwd':pwd}), patch.multiple(mount_service, BASE=base,FSTAB=fstab,TARGETS=targets), patch('mount_service.command') as command:
            with self.assertRaisesRegex(ValueError,'Wait for the current job'):
                mount_service.apply(body,self.config)
            command.assert_not_called()
        self.assertEqual(fstab.read_text(),original)

    def test_mount_refuses_it_managed_entry_and_duplicate_share(self):
        base, fstab, targets, pwd = self.broker()
        body={'slot':'bulk','unc':'//server/share','username':'u','password':'p','confirm':True}
        with patch.dict(sys.modules, {'pwd':pwd}), patch.multiple(mount_service, BASE=base,FSTAB=fstab,TARGETS=targets):
            fstab.write_text('//other/share '+targets['bulk']+' cifs ro 0 0\n')
            with self.assertRaisesRegex(ValueError,'managed by IT'):
                mount_service.apply(body,self.config)
            fstab.write_text('//server/share '+targets['output']+' cifs rw 0 0\n')
            with self.assertRaisesRegex(ValueError,'separate share'):
                mount_service.apply(body,self.config)

    def test_scan_progress_with_many_reports_and_unchanged_files(self):
        for index in range(105):
            (self.sources[1] / f'synthetic-{index}.txt').write_text(f'Accession: SYNTHETIC-{index}\nFindings: synthetic.')
        job = enqueue(self.config, 'scan')
        engine.scan(self.config, job)
        engine.scan(self.config, job)
        with database(self.config) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM files WHERE kind='report' AND active=1").fetchone()[0], 106)
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()[0], '1')
            self.assertGreater(db.execute('SELECT progress FROM jobs WHERE id=?', (job,)).fetchone()[0], 100)
