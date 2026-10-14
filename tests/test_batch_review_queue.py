import copy
import unittest
import tempfile
from pathlib import Path
import batch_review_queue as queue


def record(label,study,number,holds=(),patient_id=None):
    return {'label':label,'study_uid':study,'path':str(number),'sha256':str(number),
            'holds':list(holds),'identity':dict(PatientID=patient_id or label,PatientName=label,
                IssuerOfPatientID='synthetic',PatientBirthDate='19700101')}


class QueueTests(unittest.TestCase):
    def test_priority_separation_and_all_cohort_patients_preserved(self):
        plan={'patients':['P001','P002','P003','P004'],'records':[
            record('P001','S1',1),record('P002','S2',2,patient_id='A'),
            record('P002','S2',3,patient_id='B'),record('P003','S3',4,['overlay_review'])]}
        before=copy.deepcopy(plan)
        result=queue.build_queue(plan)
        self.assertEqual([g['lane'] for g in result['groups']],list(queue.LANES))
        self.assertEqual(result['summary']['eligible_instance_paths'],3)
        self.assertEqual(result['summary']['held_instance_paths'],1)
        self.assertEqual(result['patients_without_inventoried_studies'],['P004'])
        self.assertEqual(plan,before)
        self.assertEqual(result['summary']['authorizations_created'],0)

    def test_partial_groups_do_not_hide_held_objects(self):
        plan={'patients':['P001'],'records':[record('P001','S1',1),record('P001','S1',2,['invalid_age']),record('P001','S2',3)]}
        result=queue.build_queue(plan)
        self.assertFalse(result['groups'][0]['partial_study'])
        self.assertTrue(result['groups'][1]['partial_study'])
        self.assertEqual(result['groups'][1]['hold_reasons'],['invalid_age'])

    def test_variation_study_with_no_eligible_objects_stays_separate(self):
        plan={'patients':['P001'],'records':[record('P001','S1',1,['overlay_review'],'A'),record('P001','S1',2,['overlay_review'],'B')]}
        result=queue.build_queue(plan)
        self.assertEqual(result['summary']['lanes']['identity_variation_review']['held_instance_paths'],2)
        self.assertEqual(result['summary']['eligible_instance_paths'],0)

    def test_private_files_created_without_authorization_ledger(self):
        packet=queue.build_queue({'patients':['P001'],'records':[record('P001','S1',1)]})
        with tempfile.TemporaryDirectory() as directory:
            queue.save_queue(Path(directory),packet)
            self.assertEqual(len(list(Path(directory).iterdir())),4)
            self.assertFalse((Path(directory)/'group-authorizations.private.json').exists())
            self.assertTrue(all(v=='not_reviewed' for v in packet['groups'][0]['evidence'].values()))
