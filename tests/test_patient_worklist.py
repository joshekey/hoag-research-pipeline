import json
import unittest
from unittest.mock import patch
from pydicom.uid import generate_uid
import engine
import patient_worklist
import test_workflow as workflow
from store import database, enqueue
import worker


class PatientWorklistTests(unittest.TestCase):
    setUp=workflow.WorkflowTests.setUp
    study=workflow.WorkflowTests.study
    prepare=workflow.WorkflowTests.prepare
    approve=workflow.WorkflowTests.approve

    def test_grouping_and_selection_boundaries(self):
        other=generate_uid()
        workflow.image(self.sources[0]/'second',other,generate_uid())
        engine.scan(self.config)
        result=patient_worklist.listing(self.config)
        self.assertEqual(result['total'],1)
        self.assertEqual(result['rows'][0]['studies'],2)
        subject=self.study()['subject']
        self.assertEqual(len(patient_worklist.examinations(self.config,subject)),2)
        for selection in [[self.uid,'foreign'],[self.uid,self.uid],[]]:
            with self.assertRaises(ValueError):patient_worklist.targets(self.config,dict(subject=subject,uids=selection))
        with self.assertRaises(ValueError):patient_worklist.targets(self.config,dict(subject='foreign',uids=[self.uid]))

    def test_batch_exports_only_approved_and_writes_anonymous_manifest(self):
        other=generate_uid()
        workflow.image(self.sources[0]/'second',other,generate_uid())
        engine.scan(self.config)
        self.approve()
        subject=self.study()['subject']
        job=enqueue(self.config,'export_patient',dict(subject=subject,uids=[self.uid,other]))
        worker.process_one(self.config)
        with database(self.config) as db:
            result=db.execute('SELECT * FROM jobs WHERE id=?',(job,)).fetchone()
            self.assertEqual(result['state'],'completed',result['message'])
            self.assertIn('1 held',result['message'])
            exports=db.execute('SELECT * FROM exports').fetchall()
            self.assertEqual(len(exports),1)
        from pathlib import Path
        manifest=json.loads(next((Path(self.config['output_root'])/subject).glob('patient-manifest-*.json')).read_text())
        self.assertEqual(manifest['held_count'],1)
        self.assertEqual(len(manifest['studies']),1)
        self.assertNotIn(self.uid,json.dumps(manifest))
        self.assertNotIn('SYNTHETIC123',json.dumps(manifest))

    def test_bulk_prepare_does_not_use_unconfirmed_candidate(self):
        with database(self.config) as db:
            report=db.execute("SELECT id FROM files WHERE kind='report' LIMIT 1").fetchone()[0]
            db.execute('UPDATE studies SET report_id=? WHERE uid=?',(report,self.uid))
        with patch('engine.prepare') as prepare:
            with self.assertRaisesRegex(ValueError,'failed'):
                patient_worklist.process(self.config,'prepare_patient',dict(subject=self.study()['subject'],uids=[self.uid]),None)
            prepare.assert_not_called()
