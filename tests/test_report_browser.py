import json
import sqlite3
import time
import unittest
from pathlib import Path
from unittest.mock import patch
import engine
import report_browser
import sql_browse
import worker
import sql_reindex
import test_workflow as workflow
from store import database,enqueue
import test_tabbed_workflow as tabbed
from contextlib import closing


class BrowserTests(unittest.TestCase):
    setUp=workflow.WorkflowTests.setUp
    client=tabbed.TabbedWorkflowTests.client

    def test_unrelated_report_is_searchable_previewable_and_manually_associable(self):
        self.report.write_text('Patient Name: Different, Example\nExam Date: 01/03/2020\nFindings: synthetic narrative')
        engine.scan(self.config)
        client,auth=self.client()
        result=client.post('/api/report-browser/search',json={'source':'txt','q':'Different'},headers=auth)
        item=result.get_json()['groups'][0]['rows'][0]
        preview=client.post('/api/report-browser/preview',json=item,headers=auth).get_json()
        self.assertIn('synthetic narrative',preview['text'])
        body=dict(preview,confirm=True,note='Synthetic reviewer verified this deliberate relationship')
        response=client.post(f'/api/study/{self.uid}/report-association',json=body,headers=auth)
        self.assertEqual(response.status_code,200)
        engine.prepare(self.config,self.uid,response.get_json()['report_id'],nlp=workflow.NoNLP())
        with self.assertRaises(ValueError):engine.export_study(self.config,self.uid)

    def test_sql_preview_and_full_review_export_pipeline(self):
        sql={'source':'sql','ref':'synthetic-ref','text':'Patient Name: Synthetic, Person\nFindings: Synthetic SQL report','hash':'' ,'identity':{'name':'Synthetic Person'}}
        sql['hash']=engine.digest(sql['text'].encode())
        with patch('report_browser.request_broker',return_value=sql):
            result=report_browser.associate(self.config,self.uid,dict(sql,confirm=True),'synthetic')
            engine.prepare(self.config,self.uid,result['report_id'],nlp=workflow.NoNLP())
            with database(self.config) as db:study=dict(db.execute('SELECT * FROM studies WHERE uid=?',(self.uid,)).fetchone())
            engine.approve(self.config,self.uid,study['sanitized'],[],'Reviewed synthetic sources')
            folder=engine.export_study(self.config,self.uid)
            self.assertTrue((folder/'Report/report.txt').exists())
        with patch('report_browser.request_broker',side_effect=ValueError('SQL source changed')):
            with self.assertRaises(ValueError):engine.export_study(self.config,self.uid)

    def test_changed_preview_requires_preview_again_and_confirmation(self):
        with database(self.config) as db:ref=str(db.execute("SELECT id FROM files WHERE kind='report'").fetchone()[0])
        result=report_browser.preview(self.config,'txt',ref)
        with self.assertRaises(ValueError):report_browser.associate(self.config,self.uid,dict(result,confirm=False),'synthetic')
        with self.assertRaises(ValueError):report_browser.associate(self.config,self.uid,dict(result,confirm=True,hash='stale'),'synthetic')

    def test_sql_global_search_and_snapshot_tokens(self):
        index=Path(self.config['state_dir'])/'browser.sqlite'
        with closing(sqlite3.connect(index)) as db:
            db.execute('CREATE TABLE narratives(report_id BLOB PRIMARY KEY,text BLOB,procedure_name BLOB,result_date BLOB)')
            db.executemany('INSERT INTO narratives VALUES(?,?,?,?)',[(b'1',b'Patient Name: Other, Person\nFindings: synthetic A',b'CT',b'20200101'),(b'2',b'Patient Name: Sample, Person\nFindings: synthetic B',b'MR',b'20200102')]);db.commit()
        result=sql_browse.query(index,b'synthetic-key',{'action':'browse','q':''})
        self.assertEqual(result['total'],2)
        ref=result['rows'][0]['ref']
        self.assertIn('synthetic',sql_browse.query(index,b'synthetic-key',{'action':'browse_preview','token':ref})['text'])
        with closing(sqlite3.connect(index)) as db:db.execute("UPDATE narratives SET text=? WHERE report_id=?",(b'changed',b'1'));db.commit()
        with self.assertRaises(ValueError):sql_browse.query(index,b'synthetic-key',{'action':'browse_preview','token':ref})

    def test_reindex_rereads_unchanged_files_and_preserves_scope(self):
        with database(self.config) as db:db.execute("DELETE FROM report_search")
        enqueue(self.config,'reindex')
        self.assertTrue(worker.process_one(self.config))
        with database(self.config) as db:
            self.assertGreater(db.execute('SELECT count(*) FROM report_search').fetchone()[0],0)
            self.assertEqual(db.execute('SELECT state FROM jobs ORDER BY id DESC LIMIT 1').fetchone()[0],'completed')

    def test_search_does_not_require_selected_study_or_completed_scan(self):
        with database(self.config) as db:db.execute("UPDATE settings SET value='0' WHERE key='scan_complete'")
        result=report_browser.search(self.config,{'source':'txt','q':''})
        self.assertEqual(result['groups'][0]['total'],1)

    def test_sql_rebuild_publishes_atomically_and_failure_preserves_previous_index(self):
        source=self.sources[1]/'synthetic.sql';source.write_text('-- synthetic dump')
        engine.scan(self.config)
        index=Path(self.config['state_dir'])/'sql-index.sqlite';index.write_bytes(b'old index')
        with database(self.config) as db:file_id=db.execute("SELECT id FROM files WHERE kind='sql'").fetchone()[0]
        def run(builder):
            job_id=enqueue(self.config,'reindex_sql',{'file_id':file_id})
            with database(self.config) as db:db.execute("UPDATE jobs SET state='running' WHERE id=?",(job_id,))
            with patch('sql_reindex.build',side_effect=builder):
                sql_reindex.request(self.config,index,{'action':'reindex_start','job_id':job_id,'file_id':file_id})
                for _ in range(100):
                    result=sql_reindex.request(self.config,index,{'action':'reindex_status','job_id':job_id})
                    if result['state']!='running': break
                    time.sleep(.01)
            with database(self.config) as db:db.execute("UPDATE jobs SET state='completed' WHERE id=?",(job_id,))
            return result
        def good(source,staging,callback=None,independent=False):
            self.assertEqual(index.read_bytes(),b'old index');callback(1000)
            staging.write_bytes(b'new index');return {'narratives':2},{}
        self.assertEqual(run(good)['state'],'completed');self.assertEqual(index.read_bytes(),b'new index')
        def bad(*args,**kwargs):raise ValueError('synthetic invalid schema')
        self.assertEqual(run(bad)['state'],'failed');self.assertEqual(index.read_bytes(),b'new index')

    def test_sql_rebuild_rejects_unqueued_requests(self):
        with self.assertRaises(ValueError):sql_reindex.request(self.config,Path(self.config['state_dir'])/'index.sqlite',{'action':'reindex_start','job_id':999,'file_id':999})
