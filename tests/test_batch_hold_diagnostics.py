import copy
import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch
import batch_hold_diagnostics as diagnostic
import test_batch_dicom_stage as fixture


class HoldTests(unittest.TestCase):
    def test_overlaps_repeated_paths_and_identity_are_not_added_together(self):
        a={'label':'P001','study_uid':'S1','path':'one','holds':['overlay_review','burned_in_or_unknown','overlay_review'],
           'identity':{'PatientID':'A'}}
        b={'label':'P001','study_uid':'S2','path':'two','holds':['invalid_age'],'identity':{'PatientID':'B'}}
        plan={'patients':['P001','P002'],'records':[a,copy.deepcopy(a),b], 'release_allowed':False,
              'counts':{'instances_with_technical_holds':3},'exceptions':[{'label':'P002','code':'folder_unresolved'}]}
        before=copy.deepcopy(plan)
        result=diagnostic.diagnose(plan)
        self.assertEqual(result['summary']['technical_hold_record_occurrences'],3)
        self.assertEqual(result['summary']['technical_hold_unique']['unique_instance_paths'],2)
        self.assertEqual(result['exact_reasons']['overlay_review']['unique_instance_paths'],1)
        self.assertEqual(result['categories']['pixel_overlay']['unique_patients'],1)
        self.assertEqual(result['summary']['cohort_patients'],2)
        self.assertEqual(result['holds_cleared'],0)
        self.assertEqual(plan,before)
        self.assertTrue(result['identity_observations_separate_from_technical_holds'])

    def test_unknown_reasons_and_unheld_objects_preserved(self):
        records=[{'label':'P001','study_uid':'S1','path':'one','holds':[]},
                 {'label':'P002','study_uid':'S2','path':'two','holds':['future_hold']}]
        result=diagnostic.diagnose({'patients':['P001','P002'],'records':records,'release_allowed':False})
        self.assertEqual(result['exact_reasons']['future_hold']['category'],'unclassified')
        self.assertEqual(result['summary']['no_technical_hold_unique']['unique_instance_paths'],1)
        self.assertEqual(result['summary']['identity_review_required_unique']['unique_instance_paths'],2)


class HeaderTests(unittest.TestCase):
    setUp=fixture.StagingTests.setUp
    setup_batch=fixture.StagingTests.setup_batch
    run_batch=fixture.StagingTests.run_batch
    prepare=fixture.StagingTests.prepare

    def test_missing_burned_in_attribute_is_not_classified_as_yes(self):
        import pydicom
        self.prepare()
        ds=pydicom.dcmread(self.path)
        del ds.BurnedInAnnotation
        ds.save_as(self.path,enforce_file_format=True)
        self.plan['records'][0]['holds']=['burned_in_or_unknown']
        before=self.path.read_bytes()
        result=diagnostic.diagnose(self.plan,self.config,self.mounted,True,True)
        counts=result['current_header_diagnostics']
        self.assertEqual(counts['burned_in_attribute']['missing_or_empty']['unique_instance_paths'],1)
        self.assertEqual(counts['source_hash_check']['differs_from_plan']['unique_instance_paths'],1)
        self.assertEqual(self.path.read_bytes(),before)
        self.assertEqual(result['holds_cleared'],0)

    def test_unreadable_file_does_not_abort_other_diagnostics(self):
        self.prepare()
        self.plan['records'][0]['holds']=['source_unverified']
        self.plan['records'][0]['path']=str(self.sources[0]/'missing.dcm')
        result=diagnostic.diagnose(self.plan,self.config,self.mounted,True)
        self.assertEqual(result['current_header_diagnostics']['error_type']['FileNotFoundError']['unique_instance_paths'],1)

    def test_terminal_summary_has_no_private_evidence(self):
        self.prepare()
        self.plan['records'][0]['holds']=['overlay_review']
        result=diagnostic.diagnose(self.plan,self.config,self.mounted,True)
        public={k:v for k,v in result.items() if k not in ('private_findings','input_plan_sha256','resolution_guidance')}
        text=json.dumps(public)
        for forbidden in (str(self.path),'SYNTHETIC123','P001',self.plan['records'][0]['study_uid']):
            self.assertNotIn(forbidden,text)
