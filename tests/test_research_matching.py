import json
import unittest
import sys
import types
import sqlite3
from contextlib import closing
from unittest.mock import patch
import engine
import research_matching as matching
import report_browser
import test_workflow as workflow
import test_tabbed_workflow as tabbed
from store import database,enqueue,Cancelled
from worker import process_one
import local_sources
import mount_service
import sql_browse


class ResearchMatchingTests(unittest.TestCase):
    setUp=workflow.WorkflowTests.setUp
    client=tabbed.TabbedWorkflowTests.client

    def build(self,sql=None):
        response={'rows':sql or [],'total':len(sql or []),'snapshot':'synthetic','has_more':False,'next_after':''}
        with patch('research_matching.request_broker',return_value=response):matching.build(self.config)

    def test_patient_and_generic_date_headers_are_supporting_evidence(self):
        info=matching.evidence('Patient: Synthetic Person\nDate: 01/02/2020\nFindings: Clear lungs')
        self.assertEqual(info['name'],'PERSON SYNTHETIC');self.assertEqual(info['report_date'],'20200102');self.assertEqual(info['exam_date'],'')
        result=matching.rank({'name':'Synthetic^Person','date':'20200102','modality':'CT'},info)
        self.assertEqual(result['bucket'],'review');self.assertIn('Examination date',result['missing'])
        self.assertIn('Report header date (verify examination date)',result['matches'])

    def test_equal_evidence_prefers_sql_and_preserves_txt_choice(self):
        self.report.write_text('Patient: Synthetic Person\nAccession: ACC_TEST_123\nDate: 01/02/2020\nFindings: Clear lungs')
        engine.scan(self.config)
        sql=[{'source':'sql','ref':'synthetic-ref','path':'SQL CT report','hash':'synthetic-hash','evidence':matching.evidence(self.report.read_text(),self.report.name)}]
        self.build(sql)
        result=matching.suggestions(self.config,self.uid)
        self.assertEqual([r['source'] for r in result['rows']],['sql','txt'])
        self.assertEqual(result['rows'][0]['bucket'],'strong')
        with database(self.config) as db:self.assertIsNone(db.execute('SELECT report_id FROM studies WHERE uid=?',(self.uid,)).fetchone()[0])

    def test_last_name_lead_is_kept_with_identity_differences(self):
        other=self.sources[1]/'other.txt';other.write_text('Patient: Synthetic Other\nDate: 01/02/2020\nFindings: Synthetic other report')
        engine.scan(self.config);self.build()
        result=matching.suggestions(self.config,self.uid)
        row=next(r for r in result['rows'] if r['path']==str(other))
        self.assertEqual(row['bucket'],'differences');self.assertIn('Full patient name',row['differences'])
        self.assertIn('Synthetic other report',report_browser.preview(self.config,'txt',row['ref'])['text'])

    def test_late_second_patient_name_cannot_be_strong(self):
        info=matching.evidence(self.report.read_text()+'\n'+('padding\n'*4000)+'Patient: Different Person\n')
        result=matching.rank({'name':'Synthetic^Person','accession':'ACC_TEST_123','date':'20200102','modality':'CT'},info)
        self.assertEqual(result['bucket'],'differences');self.assertIn('Multiple patient names',result['differences'])

    def test_sql_failure_is_coverage_error_not_no_reports(self):
        with patch('research_matching.request_broker',side_effect=ValueError('unavailable')):matching.build(self.config)
        result=matching.suggestions(self.config,self.uid)
        self.assertTrue(result['ready']);self.assertIn('unavailable',result['sql_status']);self.assertEqual(len(result['rows']),1)

    def test_scan_automatically_builds_suggestions(self):
        job=enqueue(self.config,'reindex')
        with patch('research_matching.request_broker',side_effect=ValueError('unavailable')):process_one(self.config)
        self.assertTrue(matching.suggestions(self.config,self.uid)['ready'])
        with database(self.config) as db:self.assertEqual(db.execute('SELECT state FROM jobs WHERE id=?',(job,)).fetchone()[0],'completed')

    def test_queue_filters_paginates_and_removes_associated_studies(self):
        self.build();client,auth=self.client()
        data=client.get('/api/research-queue',headers=auth).get_json();self.assertEqual(data['total'],1)
        self.assertEqual(client.get('/api/research-queue?bucket=review',headers=auth).get_json()['total'],0)
        self.assertEqual(client.get('/api/research-queue?offset=100',headers=auth).get_json()['rows'],[])
        row=matching.suggestions(self.config,self.uid)['rows'][0];preview=report_browser.preview(self.config,row['source'],row['ref'])
        report_browser.associate(self.config,self.uid,dict(preview,confirm=True),'synthetic')
        self.assertEqual(client.get('/api/research-queue',headers=auth).get_json()['total'],0)

    def test_changed_fingerprint_and_cancelled_run_hide_suggestions(self):
        self.build()
        with database(self.config) as db:db.execute("UPDATE studies SET fingerprint='changed' WHERE uid=?",(self.uid,))
        self.assertEqual(matching.suggestions(self.config,self.uid)['rows'],[])
        job=enqueue(self.config,'suggest_reports')
        with database(self.config) as db:db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?',(job,))
        with self.assertRaises(Cancelled):matching.build(self.config,job)
        self.assertFalse(matching.suggestions(self.config,self.uid)['ready'])

    def test_three_source_health_and_selected_scope_ignore_unselected_missing_root(self):
        third=self.root/'database';third.mkdir();self.config['source_roots'].append(str(third))
        client,auth=self.client();data=client.get('/api/health',headers=auth).get_json()
        self.assertEqual(len(data['shares']),4);self.assertTrue(all(r['ok'] for r in data['shares']))
        self.config['source_roots'].append(str(self.root/'unavailable-source'))
        selected=engine.selection(self.config,[{'root':0,'relative':'.'}]);self.assertEqual(len(selected),1)

    def test_third_share_mount_is_readonly_and_registers_without_replacing_other_sources(self):
        base=self.root/'mount-config';base.mkdir();fstab=base/'fstab';fstab.write_text('')
        target=self.root/'third-share'
        targets={'bulk':self.config['source_roots'][0],'imaging':self.config['source_roots'][1],'database':str(target),'output':self.config['output_root']}
        account=types.SimpleNamespace(pw_uid=1001,pw_gid=1001)
        saved=[]
        modules={'pwd':types.SimpleNamespace(getpwnam=lambda _:account),'configure':types.SimpleNamespace(save=lambda config:saved.append(config))}
        with patch.dict(sys.modules,modules),patch.multiple(mount_service,BASE=base,FSTAB=fstab,TARGETS=targets),patch('mount_service.command'),patch('mount_service.os.path.ismount',return_value=False):
            mount_service.apply({'slot':'database','unc':'//synthetic/sql','username':'synthetic','password':'synthetic-password','confirm':True},self.config)
        self.assertIn(' ro,credentials=',fstab.read_text());self.assertEqual(saved[0]['source_roots'],[*self.config['source_roots'],str(target)])
        self.assertEqual(saved[0]['output_root'],self.config['output_root'])

    def test_local_input_inventory_only_copies_txt_and_explicit_sql(self):
        sql=self.root/'synthetic.sql';sql.write_text('-- synthetic')
        (self.sources[1]/'unrelated.bin').write_bytes(b'synthetic')
        rows=list(local_sources.inputs(str(self.sources[1]),str(sql)))
        self.assertEqual([r[0] for r in rows],['txt','sql'])
        with self.assertRaises(ValueError):list(local_sources.inputs('relative/path',''))

    def test_real_sql_evidence_pages_and_preview_tokens_feed_matching(self):
        index=self.root/'synthetic-index.sqlite'
        narrative=b'Patient: Synthetic Person\nAccession: ACC_TEST_123\nDate: 01/02/2020\nFindings: Synthetic SQL report'
        with closing(sqlite3.connect(index)) as db:
            db.execute('CREATE TABLE narratives(report_id BLOB PRIMARY KEY,text BLOB,procedure_name BLOB,result_date BLOB)')
            db.executemany('INSERT INTO narratives VALUES(?,?,?,?)',[(str(i).encode(),narrative,b'CT chest',b'20200103') for i in range(102)])
            db.commit()
        def broker(body):return sql_browse.query(index,b'synthetic-key',body)
        first=broker({'action':'match_catalog','after':''});second=broker({'action':'match_catalog','after':first['next_after']})
        self.assertEqual(len(first['rows']),100);self.assertEqual(len(second['rows']),2)
        self.assertEqual(first['rows'][0]['evidence']['report_date'],'20200102')
        self.assertEqual(first['rows'][0]['evidence']['result_date'],'20200103')
        with patch('research_matching.request_broker',side_effect=broker):matching.build(self.config)
        rows=matching.suggestions(self.config,self.uid)['rows']
        selected=next(row for row in rows if row['source']=='sql')
        with patch('report_browser.request_broker',side_effect=broker):
            preview=report_browser.preview(self.config,'sql',selected['ref'])
            self.assertEqual(preview['hash'],selected['hash']);self.assertIn('Synthetic SQL report',preview['text'])
