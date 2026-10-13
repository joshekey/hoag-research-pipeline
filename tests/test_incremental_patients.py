"""Synthetic end-to-end checks: mixed-folder import through reviewed patient export."""
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import pydicom
from pydicom.uid import generate_uid
import engine
import research_matching
import test_workflow as workflow
import test_tabbed_workflow as tabbed
from store import database, enqueue
from worker import process_one


class IncrementalPatientTests(unittest.TestCase):
    setUp=workflow.WorkflowTests.setUp
    study=workflow.WorkflowTests.study
    prepare=workflow.WorkflowTests.prepare
    approve=workflow.WorkflowTests.approve
    client=tabbed.TabbedWorkflowTests.client

    def folder(self, name, patient='NEW_PATIENT', accession='NEW_EXAM'):
        folder=self.sources[0]/name;folder.mkdir()
        uid=generate_uid()
        ds=workflow.image(folder/'image',uid,generate_uid())
        ds.PatientID=patient;ds.AccessionNumber=accession
        ds.save_as(folder/'image',enforce_file_format=True)
        (folder/'report.txt').write_text(f'Patient: Synthetic Person\nMRN: {patient}\nAccession: {accession}\nFindings: Clear lungs.',encoding='utf-8')
        return uid,folder

    def run_job(self,client,auth,kind,payload):
        response=client.post('/api/jobs',headers=auth,json=dict(kind=kind,payload=payload))
        self.assertEqual(response.status_code,200,response.get_json())
        process_one(self.config)
        with database(self.config) as db:
            job=db.execute('SELECT * FROM jobs WHERE id=?',(response.get_json()['id'],)).fetchone()
            self.assertEqual(job['state'],'completed',job['message'])

    def test_mixed_folders_associate_review_export_crosswalk_remove(self):
        self.approve();before=self.study()
        with patch('research_matching.request_broker',side_effect=ValueError('No synthetic SQL')):
            research_matching.build(self.config)
        with database(self.config) as db:
            evidence=research_matching.evidence('Patient: Synthetic Person\nAccession: NEW_EXAM\nFindings: Synthetic SQL report')
            cursor=db.execute("INSERT INTO match_reports(source,ref,path,hash,evidence) VALUES ('sql','synthetic-sql','synthetic-sql','stable-hash',?)",(json.dumps(evidence),))
            db.executemany('INSERT INTO match_report_keys VALUES (?,?)',[(key,cursor.lastrowid) for key in research_matching.keys(evidence)])
            old_index=[tuple(r) for r in db.execute('SELECT * FROM match_reports ORDER BY id')]
        first,a=self.folder('patient-a-exam-one')
        second,b=self.folder('patient-a-exam-two',accession='NEW_EXAM_TWO')
        originals={p:p.read_bytes() for folder in (a,b) for p in folder.iterdir()}
        client,auth=self.client()
        read=Path.read_bytes
        def guarded_read(path):
            self.assertNotEqual(path,self.report,'Existing report source must not be reread')
            return read(path)
        with patch.object(Path,'read_bytes',guarded_read),patch('research_matching.build',side_effect=AssertionError('No global report rebuild')),patch('research_matching.request_broker',side_effect=AssertionError('No SQL reindex or catalog fetch')):
            self.run_job(client,auth,'add_dicom',{'folders':[{'root':0,'relative':a.name},{'root':0,'relative':b.name}]})
        self.assertEqual(self.study(),before)
        with database(self.config) as db:
            current={r['id']:tuple(r) for r in db.execute('SELECT * FROM match_reports')}
            self.assertTrue(all(current[r[0]]==r for r in old_index))
        patients=client.get('/api/patients',headers=auth).get_json()['rows']
        subject=next(p['subject'] for p in patients if p['patient']=='NEW_PATIENT')
        self.assertEqual(next(p['studies'] for p in patients if p['subject']==subject),2)
        for uid in (first,second):
            suggestions=client.get('/api/study/'+uid+'/suggestions',headers=auth).get_json()
            self.assertTrue(suggestions['ready']);self.assertTrue(suggestions['rows'])
            report=next(r for r in suggestions['rows'] if Path(r['path']).parent==(a if uid==first else b))
            response=client.post('/api/study/'+uid+'/report-association',headers=auth,json=dict(source='txt',ref=report['ref'],hash=report['hash'],confirm=True,note='Verified synthetic patient and examination'))
            self.assertEqual(response.status_code,200,response.get_json())
        with patch('engine.analyzer',return_value=workflow.NoNLP()):
            self.run_job(client,auth,'prepare_patient',dict(subject=subject,uids=[first,second]))
        for uid in (first,second):
            detail=client.get('/api/study/'+uid,headers=auth).get_json()
            self.assertEqual(client.get('/api/image/'+str(detail['images'][0]['id']),headers=auth).status_code,200)
            sanitized=detail['study']['sanitized'];self.assertNotIn('NEW_PATIENT',sanitized)
            response=client.post('/api/study/'+uid+'/approve',headers=auth,json=dict(text=sanitized,rectangles=[],note='Synthetic review complete',images_reviewed=True,report_reviewed=True))
            self.assertEqual(response.status_code,200,response.get_json())
        self.run_job(client,auth,'export_patient',dict(subject=subject,uids=[first,second]))
        exports=list((self.output/subject).glob('*/DICOM/*.dcm'));self.assertEqual(len(exports),2)
        tags=[pydicom.dcmread(p) for p in exports]
        self.assertEqual({str(d.PatientName) for d in tags},{subject})
        self.assertEqual({d.PatientID for d in tags},{subject})
        self.assertEqual(len({d.StudyInstanceUID for d in tags}),2)
        crosswalk=client.post('/api/exports/crosswalk',headers=auth,json={'subject':subject}).data
        self.assertIn(b'NEW_PATIENT',crosswalk)
        response=client.post('/api/patients/'+subject+'/remove',headers=auth,json=dict(confirm=True,uids=[first,second]))
        self.assertEqual(response.get_json(),{'removed':2})
        self.assertEqual(client.get('/api/patients',headers=auth).get_json()['total'],1)
        self.assertEqual(client.post('/api/exports/crosswalk',headers=auth,json={'subject':subject}).data,crosswalk)
        self.assertTrue(all(p.exists() for p in exports))
        self.assertEqual({p:p.read_bytes() for p in originals},originals)
        self.assertEqual(self.study(),before)
        self.run_job(client,auth,'add_dicom',{'folders':[{'root':0,'relative':a.name},{'root':0,'relative':b.name}]})
        restored=client.get('/api/patients/'+subject+'/studies',headers=auth).get_json()['rows']
        self.assertEqual(len(restored),2)
        self.assertTrue(all(r['state'] not in ('approved','exported') for r in restored))

    def test_removal_requires_csrf_idle_and_complete_patient_snapshot(self):
        client,auth=self.client();subject=self.study()['subject'];url='/api/patients/'+subject+'/remove'
        payload=dict(confirm=True,uids=[self.uid])
        self.assertEqual(client.post(url,json=payload).status_code,401)
        self.assertEqual(client.post(url,headers={'Authorization':auth['Authorization']},json=payload).status_code,403)
        for bad in ({},dict(confirm=True,uids=[]),dict(confirm=True,uids=[self.uid,'wrong'])):
            self.assertEqual(client.post(url,headers=auth,json=bad).status_code,400)
        enqueue(self.config,'scan')
        self.assertEqual(client.post(url,headers=auth,json=payload).status_code,400)
        self.assertTrue(self.path.exists());self.assertTrue(self.study())

    def test_first_selected_folder_import_and_report_change(self):
        uid,folder=self.folder('first-patient')
        with database(self.config) as db:
            db.execute('DELETE FROM studies');db.execute('DELETE FROM report_keys')
            db.execute('DELETE FROM report_search');db.execute('DELETE FROM files')
            db.execute("DELETE FROM settings WHERE key IN ('scan_folders','scan_parser')")
            db.execute("UPDATE settings SET value='0' WHERE key='scan_complete'")
        selected=[dict(root=0,relative=folder.name)]
        engine.scan(self.config,folders=selected,additive=True)
        with database(self.config) as db:
            self.assertEqual(json.loads(db.execute("SELECT value FROM settings WHERE key='scan_folders'").fetchone()[0]),selected)
            self.assertEqual(db.execute('SELECT count(*) FROM studies').fetchone()[0],1)
            report=db.execute("SELECT id FROM files WHERE kind='report'").fetchone()[0]
        engine.prepare(self.config,uid,report,nlp=workflow.NoNLP())
        with database(self.config) as db:
            text=db.execute('SELECT sanitized FROM studies WHERE uid=?',(uid,)).fetchone()[0]
        engine.approve(self.config,uid,text,[],'Synthetic review complete')
        (folder/'report.txt').write_text('Patient: Synthetic Person\nAccession: CHANGED\nFindings: Changed report',encoding='utf-8')
        engine.scan(self.config,folders=selected,additive=True)
        with database(self.config) as db:
            study=db.execute('SELECT * FROM studies WHERE uid=?',(uid,)).fetchone()
            self.assertEqual(study['state'],'needs_review');self.assertIsNone(study['approved_hash'])
            self.assertEqual(db.execute("SELECT count(*) FROM match_reports WHERE source='txt'").fetchone()[0],1)
        (folder/'report.txt').unlink()
        engine.scan(self.config,folders=selected,additive=True)
        with database(self.config) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM match_reports WHERE source='txt'").fetchone()[0],0)
            self.assertEqual(db.execute('SELECT count(*) FROM report_keys').fetchone()[0],0)

    def test_changed_selected_images_invalidate_only_their_review(self):
        uid,folder=self.folder('changed')
        engine.scan(self.config,folders=[dict(root=0,relative=folder.name)],additive=True)
        self.approve();before=self.study()
        with database(self.config) as db:
            db.execute("UPDATE studies SET state='approved',approved_fingerprint=fingerprint WHERE uid=?",(uid,))
        ds=pydicom.dcmread(folder/'image');ds.StudyDescription='Changed description';ds.save_as(folder/'image',enforce_file_format=True)
        engine.scan(self.config,folders=[dict(root=0,relative=folder.name)],additive=True)
        self.assertEqual(before,self.study())
        with database(self.config) as db:
            row=db.execute('SELECT * FROM studies WHERE uid=?',(uid,)).fetchone()
            self.assertIsNone(row['approved_fingerprint']);self.assertNotEqual(row['state'],'approved')
