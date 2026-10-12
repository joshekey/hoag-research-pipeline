import base64
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch
import engine
import txt_review
import mount_service
import test_workflow as workflow
from store import database
from webapp import create_app


class TabbedWorkflowTests(unittest.TestCase):
    setUp=workflow.WorkflowTests.setUp

    def client(self):
        client=create_app(self.config).test_client()
        auth={'Authorization':'Basic '+base64.b64encode(b'admin:synthetic-password').decode()}
        client.get('/',headers=auth)
        with client.session_transaction() as session:auth['X-CSRF-Token']=session['csrf']
        return client,auth

    def test_last_name_discovery_and_conflicting_patient_preview_blocked(self):
        other=self.sources[1]/'unrelated-filename.txt'
        other.write_text('Patient Name: Synthetic, Other\nExam Date: 01/02/2020\nFindings: PRIVATE SYNTHETIC OTHER NARRATIVE',encoding='utf-8')
        engine.scan(self.config)
        client,auth=self.client()
        rows=client.get(f'/api/study/{self.uid}/txt-candidates',headers=auth).get_json()
        self.assertEqual(rows['surname'],'Synthetic')
        self.assertEqual(rows['total'],2)
        report=next(row for row in rows['rows'] if row['path']==str(other))
        preview=client.get(f'/api/study/{self.uid}/txt-report/{report["id"]}',headers=auth).get_json()
        self.assertFalse(preview['preview_allowed']);self.assertEqual(preview['text'],'')
        self.assertIn('patient name',preview['conflicts'])
        response=client.post(f'/api/study/{self.uid}/txt-association',json={'report_id':report['id'],'note':'Synthetic reviewer note','confirm':True},headers=auth)
        self.assertEqual(response.status_code,400)

    def test_sort_search_pagination_and_unknown_study(self):
        for i in range(104):
            (self.sources[1]/f'Synthetic-report-{i:03}.txt').write_text('Name: Synthetic, Person\nSynthetic report',encoding='utf-8')
        engine.scan(self.config)
        result=txt_review.candidates(self.config,self.uid,sort='filename',offset=100)
        self.assertEqual(result['total'],105);self.assertEqual(len(result['rows']),5)
        with self.assertRaises(ValueError):txt_review.candidates(self.config,self.uid,sort='path;DROP TABLE files')
        with self.assertRaises(ValueError):txt_review.candidates(self.config,'9.9.9')
        self.assertEqual(txt_review.candidates(self.config,self.uid,query='NO_SUCH_SYNTHETIC_NAME')['total'],0)

    def test_association_preparation_and_export_are_separate(self):
        client,auth=self.client()
        data=client.get(f'/api/study/{self.uid}/txt-candidates',headers=auth).get_json()
        report=data['rows'][0]['id']
        uri=f'/api/study/{self.uid}/txt-association'
        self.assertEqual(client.post(uri,json={'report_id':report,'note':'Verified synthetic study','confirm':False},headers=auth).status_code,400)
        result=client.post(uri,json={'report_id':report,'note':'Verified synthetic study','confirm':True},headers=auth).get_json()
        self.assertTrue(result['associated']);self.assertFalse(result['approved'])
        with self.assertRaises(ValueError):engine.export_study(self.config,self.uid)
        engine.prepare(self.config,self.uid,report,nlp=workflow.NoNLP())
        with self.assertRaises(ValueError):engine.export_study(self.config,self.uid)
        with database(self.config) as db:study=dict(db.execute('SELECT * FROM studies WHERE uid=?',(self.uid,)).fetchone())
        engine.approve(self.config,self.uid,study['sanitized'],[],'Synthetic image and report review')
        folder=engine.export_study(self.config,self.uid)
        self.assertTrue((folder/'Report/report.txt').is_file());self.assertEqual(len(list((folder/'DICOM').glob('*.dcm'))),1)

    def test_conflicting_exam_blocks_prepare(self):
        self.report.write_text('Accession: ACC_TEST_123\nExam Date: 01/03/2020\nSynthetic report',encoding='utf-8')
        engine.scan(self.config)
        with database(self.config) as db:report=db.execute("SELECT id FROM files WHERE kind='report'").fetchone()[0]
        with self.assertRaisesRegex(ValueError,'Conflicting'):engine.prepare(self.config,self.uid,report,nlp=workflow.NoNLP())

    def test_late_conflicting_name_declaration_blocks_narrative(self):
        self.report.write_text('Patient Name: Synthetic, Person\n'+('Synthetic padding.\n'*1300)+'Patient Name: Other, Patient\n',encoding='utf-8')
        engine.scan(self.config)
        with database(self.config) as db:report=db.execute("SELECT id FROM files WHERE kind='report'").fetchone()[0]
        result=txt_review.preview(self.config,self.uid,report)
        self.assertFalse(result['preview_allowed']);self.assertEqual(result['text'],'')

    def test_report_cannot_be_associated_with_two_studies(self):
        from pydicom.uid import generate_uid
        other_uid=generate_uid()
        workflow.image(self.sources[0]/'other-study',other_uid,generate_uid())
        engine.scan(self.config)
        with database(self.config) as db:report=db.execute("SELECT id FROM files WHERE kind='report'").fetchone()[0]
        txt_review.associate(self.config,self.uid,report,'Verified synthetic first examination',True,'synthetic')
        with self.assertRaisesRegex(ValueError,'another study'):
            txt_review.associate(self.config,other_uid,report,'Verified synthetic second examination',True,'synthetic')

    def test_changed_report_and_source_identity_are_rejected(self):
        with database(self.config) as db:report=db.execute("SELECT id FROM files WHERE kind='report'").fetchone()[0]
        self.report.write_text('SYNTHETIC CHANGED REPORT')
        with self.assertRaises(ValueError):txt_review.preview(self.config,self.uid,report)
        with patch('txt_review.pydicom.dcmread',return_value={'StudyInstanceUID':self.uid,'PatientName':'Different^Patient'}):
            with self.assertRaisesRegex(ValueError,'identity'):txt_review.candidates(self.config,self.uid)

    def test_sql_hold_and_blue_tabs_are_visible_without_enabling_gates(self):
        client,auth=self.client()
        response=client.get('/',headers=auth)
        for label in ('1 · Sources','2 · Images','3 · Reports','4 · Anonymize','5 · Export'):
            self.assertIn(label,response.get_data(as_text=True))
        with patch('webapp.SQL_REVIEW_GATE',self.root/'absent-gate'):
            self.assertEqual(client.get(f'/api/study/{self.uid}/sql-candidates',headers=auth).status_code,503)

    def test_output_share_change_retains_review_and_scan_readiness(self):
        with database(self.config) as db:report=db.execute("SELECT id FROM files WHERE kind='report'").fetchone()[0]
        engine.prepare(self.config,self.uid,report,nlp=workflow.NoNLP())
        with database(self.config) as db:study=dict(db.execute('SELECT * FROM studies WHERE uid=?',(self.uid,)).fetchone())
        engine.approve(self.config,self.uid,study['sanitized'],[],'Synthetic complete privacy review')
        base=self.root/'mount-settings';base.mkdir();fstab=base/'fstab';fstab.write_text('')
        targets=dict(zip(['bulk','imaging','output'],[*self.config['source_roots'],self.config['output_root']]))
        account=types.SimpleNamespace(pw_uid=1001,pw_gid=1001)
        body={'slot':'output','unc':'//synthetic/research-output','username':'synthetic','password':'synthetic-secret','confirm':True}
        with patch.dict(sys.modules,{'pwd':types.SimpleNamespace(getpwnam=lambda _:account)}),patch.multiple(mount_service,BASE=base,FSTAB=fstab,TARGETS=targets),patch('mount_service.command'),patch('mount_service.os.path.ismount',return_value=False):
            mount_service.apply(body,self.config)
        with database(self.config) as db:
            self.assertEqual(db.execute("SELECT value FROM settings WHERE key='scan_complete'").fetchone()[0],'1')
            self.assertEqual(db.execute('SELECT state FROM studies WHERE uid=?',(self.uid,)).fetchone()[0],'approved')
        self.assertIn(' rw,credentials=',fstab.read_text())
        self.assertTrue((engine.export_study(self.config,self.uid)/'Report/report.txt').exists())


if __name__=='__main__':unittest.main()
