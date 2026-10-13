import csv
import hashlib
import io
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import zipfile
from contextlib import redirect_stdout
from xml.sax.saxutils import escape

import batch_reconcile as batch
import engine
import pydicom
import test_workflow as workflow


def workbook(path, rows):
    xml = '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
    for number, row in enumerate(rows, 1):
        xml += '<row>'
        for col, text in enumerate(row):
            xml += f'<c r="{chr(65+col)}{number}" t="inlineStr"><is><t>{escape(str(text))}</t></is></c>'
        xml += '</row>'
    with zipfile.ZipFile(path, 'w') as book: book.writestr('xl/worksheets/sheet1.xml', xml+'</sheetData></worksheet>')


class BatchReconciliationTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp

    def setup_batch(self):
        self.maps = self.root/'crosswalk';self.maps.mkdir()
        with (self.maps/'mrn_guid_mapping.csv').open('w', newline='') as stream:
            writer=csv.writer(stream)
            writer.writerow(['PATIENTID','GUID','FNAME','MNAME','LNAME','INPUTFOLDER','OUTPUTFOLDER',''])
            writer.writerow(['INTERNAL1','synthetic-guid','Person','','Synthetic',r'\\synthetic\images','unused','P001'])
        workbook(self.maps/'ScannedDocs.xlsx', [['docid','PatientId','TextFileLocation'],
            ['DOC1','INTERNAL1',r'C:\reports\ACC_TEST_123.txt']])
        ds=pydicom.dcmread(self.path);ds.PatientBirthDate='19700101';ds.save_as(self.path,enforce_file_format=True)
        self.report.write_text('Patient Name: Synthetic^Person\nMRN: SYNTHETIC123\nDOB: 01/01/1970\n'
                               'Exam Date: 01/02/2020\nAccession: ACC_TEST_123\nFindings: Clear lungs.',encoding='utf-8')
        engine.scan(self.config)
        self.mounted={str(self.sources[0]):dict(remote=['synthetic','images'],readonly=True),
                      str(self.sources[1]):dict(remote=['synthetic','reports'],readonly=True),
                      str(self.output):dict(remote=['vnwpradiant01','ozgur imaging','aaa_Anon_Studies'],readonly=False)}

    def run_batch(self, **kwargs):
        return batch.reconcile(self.config,self.maps,self.mounted,**kwargs)

    def test_end_to_end_private_dry_run_changes_no_sources_or_catalog(self):
        self.setup_batch()
        files=[self.path,self.report,Path(self.config['state_dir'])/'workflow.sqlite']
        before={str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files}
        result=self.run_batch()
        self.assertEqual(result['counts']['patients'],1)
        self.assertEqual(result['counts']['txt_resolved_once'],1)
        self.assertEqual(result['counts']['evidence_sufficient_pending_review'],1)
        self.assertFalse(result['release_allowed'])
        self.assertTrue(all(v is False for v in result['gates'].values()))
        link=result['studies'][0]['associations'][0]
        self.assertTrue(link['patient_evidence']);self.assertTrue(link['study_evidence'])
        self.assertFalse(link['accepted'])
        self.assertNotIn('Clear lungs',json.dumps(result))
        self.assertEqual(before,{str(p):hashlib.sha256(p.read_bytes()).hexdigest() for p in files})

    def test_patient_and_exam_evidence_are_both_required(self):
        meta=dict(PatientName='Sample^Alex',PatientBirthDate='19700101',StudyDate='20200102',
                  PatientID='ID-1',AccessionNumber='ACC-1',StudyInstanceUID='1.2.3')
        report=dict(name='ALEX SAMPLE',dob='19700101',exam_date='20200102',patient_id='ID-1',accession='ACC-1',study_uid='')
        self.assertEqual(batch.association(meta,report,'Alex Sample')['status'],'evidence_sufficient_pending_review')
        for key in ('name','dob','accession','exam_date'):
            insufficient=dict(report);insufficient[key]=''
            self.assertEqual(batch.association(meta,insufficient,'Alex Sample')['status'],'review_required',key)
        for key in report:
            conflicting=dict(report);conflicting[key]='CONFLICT'
            self.assertEqual(batch.association(meta,conflicting,'Alex Sample')['status'],'review_required',key)
        self.assertEqual(batch.association(meta,report,'Another Patient')['status'],'review_required')
        self.assertEqual(batch.association(meta,dict(report,name_conflict=True),'Alex Sample')['status'],'review_required')

    def test_missing_files_blank_locations_and_folder_are_distinct(self):
        self.setup_batch()
        workbook(self.maps/'ScannedDocs.xlsx',[['docid','PatientId','TextFileLocation'],
            ['MISSING','INTERNAL1',r'C:\reports\missing.txt'],['BLANK','INTERNAL1','']])
        result=self.run_batch(path_maps=[dict(windows_prefix='C:\\reports',linux_prefix=str(self.sources[1]))])
        self.assertEqual(result['counts']['missing_txt_location'],1)
        self.assertEqual(result['counts']['txt_missing_from_active_index'],2)
        self.assertEqual(result['documents'][0]['source_probes'][0]['status'],'FileNotFoundError')
        text=(self.maps/'mrn_guid_mapping.csv').read_text().replace('\\\\synthetic\\images','\\\\synthetic\\images\\Missing')
        (self.maps/'mrn_guid_mapping.csv').write_text(text)
        result=self.run_batch()
        self.assertEqual(result['counts']['dicom_folder_unresolved'],1)
        self.assertEqual(result['counts']['dicom_studies'],0)

    def test_duplicate_instances_and_documents_do_not_become_accepted(self):
        self.setup_batch()
        (self.sources[0]/'duplicate.dcm').write_bytes(self.path.read_bytes())
        result=self.run_batch()
        self.assertEqual(result['counts']['duplicate_sop_uid'],1)
        self.assertEqual(result['studies'][0]['associations'][0]['status'],'review_required')
        workbook(self.maps/'ScannedDocs.xlsx',[['docid','PatientId','TextFileLocation'],
            ['DOC1','INTERNAL1',r'C:\reports\ACC_TEST_123.txt'],['DOC1','INTERNAL1',r'C:\reports\ACC_TEST_123.txt']])
        self.assertEqual(self.run_batch()['counts']['duplicate_document_id'],2)

    def test_different_patient_ids_in_one_folder_hold_all_studies(self):
        self.setup_batch()
        from pydicom.uid import generate_uid
        ds=pydicom.dcmread(self.path)
        ds.StudyInstanceUID=generate_uid();ds.SOPInstanceUID=generate_uid()
        ds.PatientID='OTHER_SYNTHETIC_ID'
        ds.save_as(self.sources[0]/'another-study.dcm',enforce_file_format=True)
        result=self.run_batch()
        self.assertEqual(result['counts']['inconsistent_patient_identity_across_studies'],1)
        self.assertTrue(all(c['status']=='review_required' for s in result['studies'] for c in s['associations']))

    def test_current_bytes_must_match_index_snapshot(self):
        self.setup_batch();self.report.write_text('Changed source',encoding='utf-8')
        result=self.run_batch()
        self.assertEqual(result['counts']['txt_source_validation_failed'],1)
        self.assertEqual(result['studies'][0]['associations'],[])

    def test_mount_spaces_subdirectory_and_output_alias_exclusion(self):
        text='1 0 0:1 / /mnt/source ro,relatime - cifs //vnwpradiant01/ozgur\\040imaging ro\n'
        text+='2 0 0:2 /aaa_Anon_Studies /mnt/output rw,relatime - cifs //vnwpradiant01/ozgur\\040imaging rw\n'
        mounted=batch.mounts(text)
        self.assertEqual(mounted['/mnt/output']['remote'],['vnwpradiant01','ozgur imaging','aaa_Anon_Studies'])
        self.assertEqual(batch.map_unc(r'\\vnwpradiant01\ozgur imaging\A-D\Person Folder',mounted,['/mnt/source']),Path('/mnt/source/A-D/Person Folder'))
        with self.assertRaisesRegex(ValueError,'output_alias'):
            batch.source_checked(Path('/mnt/source/aaa_Anon_Studies/P001'),['/mnt/source'],Path('/mnt/output'),mounted)
        with self.assertRaises(ValueError):batch.unc('//server/share/../other')
        direct='2 0 0:2 / /mnt/output rw - cifs //vnwpradiant01/ozgur\\040imaging/aaa_Anon_Studies rw'
        self.assertEqual(batch.mounts(direct)['/mnt/output']['remote'],mounted['/mnt/output']['remote'])

    def test_output_root_mount_and_writable_source_flagged(self):
        self.setup_batch()
        self.mounted[str(self.output)]['remote']=['vnwpradiant01','ozgur imaging']
        self.mounted[str(self.sources[0])]['readonly']=False
        result=self.run_batch()
        self.assertEqual(result['counts']['output_mount_not_exact_intended_subdirectory'],1)
        self.assertEqual(result['counts']['source_mount_not_verified_readonly'],1)
        self.assertEqual(result['counts']['dicom_studies'],0)

    def test_cli_only_prints_aggregate_counts_and_writes_private_reports(self):
        self.setup_batch()
        config_path=self.root/'config.json';config_path.write_text(json.dumps(self.config))
        original=Path.read_text
        def read(path,*args,**kwargs):
            if str(path).replace('\\','/')=='/proc/self/mountinfo':return 'synthetic'
            return original(path,*args,**kwargs)
        output=io.StringIO()
        with patch('sys.argv',['batch_reconcile.py','--config',str(config_path),'--crosswalk-dir',str(self.maps)]), \
             patch.object(Path,'read_text',read),patch('batch_reconcile.mounts',return_value=self.mounted),redirect_stdout(output):
            # main intentionally suppresses all library logs in the standalone process.
            import logging
            previous_disable=logging.root.manager.disable
            previous_umask=os.umask(0o077)
            try:self.assertEqual(batch.main(),0)
            finally:os.umask(previous_umask);logging.disable(previous_disable)
        summary=json.loads(output.getvalue())
        for forbidden in ('Synthetic','INTERNAL1','SYNTHETIC123','DOC1','Clear lungs',str(self.path)):
            self.assertNotIn(forbidden,output.getvalue())
        run=Path(self.config['state_dir'])/'batch-reconciliation'/summary['run_id']
        self.assertTrue((run/'exceptions.private.json').exists())
        manifest=json.loads((run/'manifest.private.json').read_text())
        self.assertFalse(manifest['release_allowed'])
        if os.name!='nt':self.assertEqual((run/'manifest.private.json').stat().st_mode & 0o777,0o600)

    def test_explicit_path_probe_distinguishes_unindexed_readable_file(self):
        self.setup_batch()
        (self.sources[1]/'missing.txt').write_text('synthetic',encoding='utf-8')
        workbook(self.maps/'ScannedDocs.xlsx',[['docid','PatientId','TextFileLocation'],
            ['MISSING','INTERNAL1',r'C:\reports\missing.txt']])
        result=self.run_batch(path_maps=[dict(windows_prefix='C:\\reports',linux_prefix=str(self.sources[1]))])
        probe=result['documents'][0]['source_probes'][0]
        self.assertEqual(probe['status'],'readable_but_not_active_indexed')
        self.assertTrue(probe['in_saved_scan_scope'])
        self.assertFalse(result['documents'][0]['accepted'])

    def test_selecting_export_alias_is_not_scanned(self):
        self.setup_batch()
        self.mounted[str(self.sources[0])]['remote']=['vnwpradiant01','ozgur imaging']
        (self.sources[0]/'aaa_Anon_Studies').mkdir()
        source=self.maps/'mrn_guid_mapping.csv'
        source.write_text(source.read_text().replace('\\\\synthetic\\images','\\\\vnwpradiant01\\ozgur imaging\\aaa_Anon_Studies'))
        result=self.run_batch()
        self.assertEqual(result['counts']['dicom_folder_unresolved'],1)
        self.assertEqual(result['counts']['dicom_instances'],0)

    @unittest.skipIf(os.name=='nt','Unix private permissions')
    def test_private_run_directory_permissions(self):
        self.setup_batch();run=batch.private_run_directory(self.config)
        self.assertEqual(run.stat().st_mode & 0o777,0o700)
        run.parent.chmod(0o755)
        with self.assertRaises(ValueError):batch.private_run_directory(self.config)
