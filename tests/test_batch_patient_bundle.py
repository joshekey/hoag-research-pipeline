import copy
import unittest
from unittest.mock import patch
import batch_patient_bundle as bundle
import batch_dicom_stage as dicom
import test_batch_dicom_stage as fixture
import test_workflow


class BundleTests(unittest.TestCase):
    setUp=fixture.StagingTests.setUp
    setup_batch=fixture.StagingTests.setup_batch
    run_batch=fixture.StagingTests.run_batch
    prepare_dicom=fixture.StagingTests.prepare

    def setup_bundle(self):
        self.prepare_dicom()
        self.manifest=self.run_batch()
        self.plan=dicom.plan(self.manifest,self.config,self.mounted)
        self.reviews['plan_sha256']=dicom.digest(self.plan)
        self.sql='Patient Name: Synthetic, Person\nMRN: SYNTHETIC123\nFindings: Synthetic report.'
        def broker(request):
            if request['action']=='match_catalog':
                return {'snapshot':'synthetic-snapshot','rows':[{'ref':'synthetic-token','hash':bundle.sha(self.sql.encode())}],
                    'has_more':False,'next_after':''}
            return {'text':self.sql,'hash':bundle.sha(self.sql.encode())}
        self.broker=broker

    def test_patient_folder_contains_verified_dicom_and_redacted_txt_and_sql(self):
        self.setup_bundle()
        plan=bundle.prepare(self.manifest,self.plan,self.config,self.mounted,self.broker)
        self.assertEqual(bundle.summary(plan)['reports'],{'txt_selected_by_exact_name':1,'sql_selected_by_exact_name':1})
        original=self.report.read_bytes()
        with patch('engine.analyzer',return_value=test_workflow.NoNLP()):
            state=bundle.process(plan,self.reviews,self.config,self.mounted,self.run,self.broker)
        self.assertEqual(len(list((self.run/'P001'/'DICOM').rglob('*.dcm'))),1)
        reports=list((self.run/'P001'/'reports').glob('*.txt'))
        self.assertEqual(len(reports),2)
        for report in reports:
            text=report.read_text()
            self.assertIn('P001',text); self.assertIn('[REDACTED]',text)
            self.assertNotIn('SYNTHETIC123',text)
            self.assertNotIn('Synthetic, Person',text)
        self.assertEqual(self.report.read_bytes(),original)
        self.assertFalse(state['release_allowed'])
        with patch('engine.analyzer',return_value=test_workflow.NoNLP()):
            self.assertEqual(bundle.process(plan,self.reviews,self.config,self.mounted,self.run,self.broker),state)

    def test_redactor_failure_never_copies_original_report(self):
        self.setup_bundle()
        plan=bundle.prepare(self.manifest,self.plan,self.config,self.mounted,self.broker)
        with patch('engine.sanitize_report',side_effect=ValueError('synthetic failure')):
            state=bundle.process(plan,{},self.config,self.mounted,self.run,self.broker)
        self.assertFalse(list(self.run.rglob('*.txt')))
        self.assertTrue(all(r['status']=='held_redaction_or_source' for r in state['reports'].values()))

    def test_sql_failure_does_not_block_txt_or_verified_dicom(self):
        self.setup_bundle()
        def failed(request): raise ValueError('synthetic offline')
        plan=bundle.prepare(self.manifest,self.plan,self.config,self.mounted,failed)
        self.assertFalse(plan['sql_discovery_complete'])
        with patch('engine.analyzer',return_value=test_workflow.NoNLP()):
            bundle.process(plan,self.reviews,self.config,self.mounted,self.run,failed)
        self.assertEqual(len(list(self.run.rglob('*.dcm'))),1)
        self.assertEqual(len(list(self.run.rglob('*.txt'))),1)

    def test_conflicting_sql_patient_is_held(self):
        self.setup_bundle()
        self.sql='Patient Name: Synthetic, Person\nMRN: WRONG\nFindings: Synthetic.'
        plan=bundle.prepare(self.manifest,self.plan,self.config,self.mounted,self.broker)
        sql=next(r for r in plan['reports'] if r['source']=='sql')
        self.assertEqual(sql['status'],'held')
        self.assertIn('explicit_identifier_conflict',sql['reasons'])

    def test_sql_change_between_selection_and_redaction_is_held(self):
        self.setup_bundle()
        plan=bundle.prepare(self.manifest,self.plan,self.config,self.mounted,self.broker)
        self.sql+=' changed'
        with patch('engine.analyzer',return_value=test_workflow.NoNLP()):
            state=bundle.process(plan,{},self.config,self.mounted,self.run,self.broker)
        self.assertEqual(state['reports']['1']['status'],'held_redaction_or_source')

    def test_reports_do_not_require_dicom_review_approval(self):
        self.setup_bundle()
        plan=bundle.prepare(self.manifest,self.plan,self.config,self.mounted,self.broker)
        with patch('engine.analyzer',return_value=test_workflow.NoNLP()):
            bundle.process(plan,{},self.config,self.mounted,self.run,self.broker)
        self.assertFalse(list(self.run.rglob('*.dcm')))
        self.assertEqual(len(list(self.run.rglob('*.txt'))),2)

    def test_no_reports_still_allows_verified_dicom(self):
        self.setup_bundle()
        plan=bundle.prepare(self.manifest,self.plan,self.config,self.mounted,self.broker)
        plan['reports']=[]
        bundle.process(plan,self.reviews,self.config,self.mounted,self.run,self.broker)
        self.assertEqual(len(list(self.run.rglob('*.dcm'))),1)

    def test_sql_catalog_pagination_and_snapshot_change(self):
        self.setup_bundle()
        def pages(request):
            if request['action']=='browse_preview': return self.broker(request)
            if not request['after']:
                return {'snapshot':'one','rows':[],'has_more':True,'next_after':'next'}
            return {'snapshot':'two','rows':[],'has_more':False,'next_after':''}
        plan=bundle.prepare(self.manifest,self.plan,self.config,self.mounted,pages)
        self.assertFalse(plan['sql_discovery_complete'])

    def test_sql_xml_is_rendered_and_redacted_before_writing(self):
        self.setup_bundle()
        self.sql='<Report><PatientName>Synthetic, Person</PatientName><MRN>SYNTHETIC123</MRN><Findings>Test findings.</Findings></Report>'
        plan=bundle.prepare(self.manifest,self.plan,self.config,self.mounted,self.broker)
        sql=next(r for r in plan['reports'] if r['source']=='sql')
        self.assertEqual(sql['status'],'selected_by_exact_name')
        with patch('engine.analyzer',return_value=test_workflow.NoNLP()):
            bundle.process(plan,{},self.config,self.mounted,self.run,self.broker)
        output=next((self.run/'P001'/'reports').glob('*-sql.txt')).read_text()
        self.assertNotIn('SYNTHETIC123',output)
        self.assertNotIn('<PatientName>',output)
        self.assertIn('[REDACTED]',output)
