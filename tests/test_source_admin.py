import json
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

import engine
import mount_service
from source_admin import coverage
from store import database, enqueue
import test_dashboard as dashboard
import test_workflow as workflow


class SourceAdminTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    client_auth = dashboard.DashboardTests.client_auth
    broker = dashboard.DashboardTests.broker

    def test_mount_inspection_prefills_only_public_fields_and_flags_it_entries(self):
        base, fstab, targets, pwd = self.broker()
        fstab.write_text('//host/share\\040name '+targets['bulk']+' cifs ro 0 0 # hoag-research:'+targets['bulk']+'\n//it/images '+targets['imaging']+' cifs ro 0 0\n')
        (base / (Path(targets['bulk']).name+'.credentials')).write_text('username=reader\npassword=DO-NOT-RETURN\ndomain=HOSPITAL\n')
        with patch.multiple(mount_service, BASE=base, FSTAB=fstab, TARGETS=targets):
            result = mount_service.dispatch({'action':'inspect'}, self.config)
        self.assertNotIn('DO-NOT-RETURN', json.dumps(result))
        bulk, imaging = result['mounts'][:2]
        self.assertEqual(bulk['unc'], '//host/share name')
        self.assertEqual(bulk['username'], 'reader')
        self.assertEqual(bulk['domain'], 'HOSPITAL')
        self.assertTrue(bulk['editable'])
        self.assertFalse(imaging['editable'])

    def test_connection_test_is_isolated_readonly_and_keeps_catalog(self):
        body = dict(action='test', slot='imaging',unc='//host/new',username='u',password='DO-NOT-LOG')
        account = types.SimpleNamespace(pw_uid=os.geteuid() if hasattr(os,'geteuid') else 1001, pw_gid=1001)
        with database(self.config) as db:
            before = dict(db.execute('SELECT key,value FROM settings'))
        with patch.dict(sys.modules, {'pwd':types.SimpleNamespace(getpwnam=lambda _:account)}), patch('mount_service.command') as command:
            result = mount_service.dispatch(body,self.config)
        self.assertIn('test passed',result['message'])
        calls=command.call_args_list
        self.assertEqual(len(calls),2)
        args=calls[0].args[0]
        self.assertEqual(args[:4],['/usr/bin/mount','-t','cifs','//host/new'])
        self.assertNotIn(args[4],self.config['source_roots'])
        self.assertTrue(args[-1].startswith('ro,credentials='))
        self.assertNotIn('DO-NOT-LOG',str(calls))
        self.assertFalse(Path(args[4]).exists())
        with database(self.config) as db:
            self.assertEqual(before,dict(db.execute('SELECT key,value FROM settings')))

    def test_connection_test_failure_is_sanitized_and_cleans_credentials(self):
        account=types.SimpleNamespace(pw_uid=os.geteuid() if hasattr(os,'geteuid') else 1001,pw_gid=1001)
        with patch.dict(sys.modules, {'pwd':types.SimpleNamespace(getpwnam=lambda _:account)}),patch('mount_service.command',side_effect=OSError('DO-NOT-LOG')) as command:
            with self.assertRaisesRegex(ValueError,'Connection test failed') as caught:
                mount_service.dispatch(dict(action='test',slot='bulk',unc='//host/share',username='u',password='DO-NOT-LOG'),self.config)
        self.assertNotIn('DO-NOT-LOG',str(caught.exception))
        self.assertFalse(Path(command.call_args.args[0][4]).parent.exists())

    def test_test_and_apply_require_idle_and_apply_still_requires_confirmation(self):
        body=dict(action='test',slot='imaging',unc='//host/share',username='u',password='p')
        account=types.SimpleNamespace(pw_uid=os.geteuid() if hasattr(os,'geteuid') else 1001,pw_gid=1001)
        enqueue(self.config,'scan')
        with patch.dict(sys.modules, {'pwd':types.SimpleNamespace(getpwnam=lambda _:account)}),patch('mount_service.command') as command:
            with self.assertRaisesRegex(ValueError,'Wait for the current job'):
                mount_service.dispatch(body,self.config)
            command.assert_not_called()
        with self.assertRaisesRegex(ValueError,'Confirm'):
            mount_service.validate(dict(body,action='apply'))

    def test_source_coverage_reports_scope_and_excludes_inactive_images(self):
        with database(self.config) as db:
            db.execute("INSERT OR REPLACE INTO settings VALUES ('scan_folders',?)",(json.dumps([dict(root=0,relative='subset')]),))
        data=coverage(self.config)
        self.assertEqual(data['sources'][0]['folders'],['subset'])
        self.assertEqual(data['sources'][1]['folders'],[])
        self.assertEqual(data['sources'][0]['patients'],1)
        self.assertEqual(data['sources'][0]['studies'],1)
        with database(self.config) as db:
            db.execute("UPDATE files SET active=0 WHERE kind='dicom'")
        self.assertEqual(coverage(self.config)['sources'][0]['images'],None)

    def test_descriptions_indexed_and_returned_in_list_and_detail(self):
        path=self.sources[0]/'sample.dcm'
        with database(self.config) as db:
            path=Path(db.execute("SELECT path FROM files WHERE kind='dicom'").fetchone()[0])
        import pydicom
        ds=pydicom.dcmread(path);ds.StudyDescription='CT CHEST WITHOUT CONTRAST';ds.save_as(path,enforce_file_format=True)
        engine.scan(self.config)
        client,auth=self.client_auth()
        listing=client.get('/api/studies?paged=1',headers=auth).json
        self.assertEqual(listing['rows'][0]['description'],'CT CHEST WITHOUT CONTRAST')
        self.assertEqual(client.get('/api/study/'+self.uid,headers=auth).json['study']['description'],'CT CHEST WITHOUT CONTRAST')
        self.assertEqual(client.get('/api/source-coverage').status_code,401)
        self.assertEqual(client.get('/api/mounts').status_code,401)
        self.assertEqual(client.post('/api/mount',json={'action':'test'}).status_code,401)
        self.assertEqual(client.post('/api/mount',json={'action':'test'},headers={'Authorization':auth['Authorization']}).status_code,403)
