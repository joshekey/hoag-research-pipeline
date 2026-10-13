import unittest
import re
import shlex
from pathlib import Path
from unittest.mock import patch
import engine
import test_workflow as workflow
import test_tabbed_workflow as tabbed
from store import database
from webapp import create_app


class WorkbenchTests(unittest.TestCase):
    setUp=workflow.WorkflowTests.setUp
    client=tabbed.TabbedWorkflowTests.client

    def test_sql_browser_restores_radiology_layout_without_changing_source(self):
        import report_browser
        raw='<Envelope><notes>CT CHEST</notes><notes>FINDINGS: Synthetic finding.</notes><notes>IMPRESSION: Synthetic conclusion.</notes></Envelope>'
        source={'source':'sql','ref':'synthetic','text':raw,'hash':'original-hash'}
        with patch('report_browser.request_broker',return_value=source):
            result=report_browser.preview(self.config,'sql','synthetic')
        self.assertIn('FINDINGS\nSynthetic finding.',result['formatted'])
        self.assertIn('IMPRESSION\nSynthetic conclusion.',result['formatted'])
        self.assertEqual(result['text'],raw)
        self.assertEqual(result['hash'],'original-hash')

    def test_installer_includes_every_template_asset(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/'templates/index.html').read_text(encoding='utf-8')
        required=set(re.findall(r'(?:src|href)="/static/([^"]+)"',html))
        shipped=set()
        for line in (root/'install.sh').read_text().splitlines():
            if line.startswith('install ') and line.endswith('/opt/hoag-research/static/'):
                for argument in shlex.split(line):
                    if argument.startswith('$ROOT/static/'):
                        shipped.update(p.name for p in root.glob(argument.removeprefix('$ROOT/')))
        self.assertTrue(required)
        self.assertFalse(required-shipped, f'Installer missing assets: {required-shipped}')

    def test_report_only_import_preserves_studies_and_does_not_read_dicom(self):
        with database(self.config) as db:before=dict(db.execute('SELECT * FROM studies WHERE uid=?',(self.uid,)).fetchone())
        extra=self.root/'new-reports';extra.mkdir();(extra/'new.txt').write_text('Patient: Synthetic Person\nDate: 01/02/2020\nFindings: Synthetic new report')
        (extra/'export.sql').write_text('-- synthetic export')
        self.config['source_roots'].append(str(extra))
        with patch('engine.pydicom.dcmread',side_effect=AssertionError('DICOM must not be reread')):
            engine.scan(self.config,folders=[{'root':2,'relative':'.'}],add_reports=True)
        with database(self.config) as db:
            after=dict(db.execute('SELECT * FROM studies WHERE uid=?',(self.uid,)).fetchone())
            self.assertEqual(after,before)
            self.assertEqual(db.execute("SELECT count(*) FROM files WHERE kind='dicom' AND active=1").fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM files WHERE kind='report' AND active=1").fetchone()[0],2)
            self.assertEqual(db.execute("SELECT count(*) FROM files WHERE kind='sql' AND active=1").fetchone()[0],1)

    def test_report_import_removal_only_affects_selected_report_scope(self):
        self.report.unlink()
        engine.scan(self.config,folders=[{'root':1,'relative':'.'}],add_reports=True)
        with database(self.config) as db:
            self.assertEqual(db.execute('SELECT count(*) FROM studies').fetchone()[0],1)
            self.assertEqual(db.execute("SELECT count(*) FROM files WHERE kind='report' AND active=1").fetchone()[0],0)
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()[0],'1')

    def test_sql_readiness_distinguishes_missing_dump_from_available_index(self):
        client,auth=self.client()
        with patch('sql_gui_candidates.request_broker',return_value={'available':False,'reports':None,'message':'SQL report index unavailable'}):
            data=client.get('/api/sql-readiness',headers=auth).get_json()
            self.assertEqual(data['dumps'],[]);self.assertFalse(data['broker']['available'])
        (self.sources[1]/'export.sql').write_text('-- synthetic export')
        engine.scan(self.config,folders=[{'root':1,'relative':'.'}],add_reports=True)
        with patch('sql_gui_candidates.request_broker',return_value={'available':True,'reports':5,'message':'SQL report index ready'}):
            data=client.get('/api/sql-readiness',headers=auth).get_json()
            self.assertEqual(len(data['dumps']),1);self.assertEqual(data['broker']['reports'],5)

    def test_folder_browser_shows_sql_exports_without_opening_contents(self):
        (self.sources[1]/'emr.sql').write_text('-- synthetic export')
        (self.sources[1]/'UPPER.SQL').write_text('-- synthetic export')
        (self.sources[1]/'archive.sql.gz').write_bytes(b'not a plain SQL file')
        data=engine.browse_folders(self.config,1)
        self.assertEqual(set(data['sql_files']),{'emr.sql','UPPER.SQL'})
        files={row['name']:row for row in data['files']}
        self.assertTrue(files['UPPER.SQL']['supported'])
        self.assertFalse(files['archive.sql.gz']['supported'])

    def test_first_navigation_step_is_dicom_and_sources_are_operational(self):
        client,auth=self.client();html=client.get('/',headers=auth).get_data(as_text=True)
        nav=html.split('id="primary-navigation"')[1].split('</nav>')[0]
        self.assertLess(nav.index('#studies'),nav.index('#matching'))
        self.assertNotIn('#overview',nav)
