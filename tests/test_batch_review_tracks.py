import copy
import json
import unittest
from unittest.mock import patch
import batch_review_tracks as tracks
from test_batch_review_queue import record


class TrackTests(unittest.TestCase):
    def test_scopes_are_separate_and_no_holds_are_changed(self):
        plan={'patients':['P001','P002','P003','P004'],'release_allowed':False,'records':[
            record('P001','S1',1),record('P001','S1',2,['invalid_age']),
            record('P002','S2',3,['burned_in_or_unknown']),
            record('P003','S3',4,patient_id='a'),record('P003','S3',5,patient_id='b')]}
        before=copy.deepcopy(plan)
        result=tracks.prepare_tracks(plan)
        self.assertEqual(result['summary']['technical_only_instance_records'],1)
        self.assertEqual(result['summary']['standard_held_instances'],1)
        self.assertEqual(result['summary']['identity_variation_studies_kept_separate'],1)
        self.assertNotIn('invalid_age',result['technical_diagnostic']['exact_reasons'])
        self.assertEqual(plan,before)
        self.assertEqual(result['summary']['holds_cleared'],0)

    def test_101_standard_studies_produce_11_unapproved_review_batches(self):
        plan={'patients':['P001'],'release_allowed':False,'records':[record('P001','S'+str(i),i) for i in range(101)]}
        result=tracks.prepare_tracks(plan)
        self.assertEqual(result['summary']['standard_review_batches'],11)
        self.assertTrue(all(r['identity_status']=='not_reviewed' for r in result['standard_review_worklist']))
        self.assertEqual(result['summary']['authorizations_created'],0)

    def test_issuer_only_missing_identified_as_policy_observation(self):
        item=record('P001','S1',1); item['identity']['IssuerOfPatientID']=''
        result=tracks.prepare_tracks({'patients':['P001'],'release_allowed':False,'records':[item]})
        self.assertEqual(result['policy_observations']['issuer_only_missing_strict_policy_hold']['unique_instance_paths'],1)
        self.assertEqual(result['technical_diagnostic']['exact_reasons']['missing_identity_evidence']['category'],'identity')

    def test_header_diagnostics_distinguish_unknown_flag_and_overlay_metadata(self):
        item=record('P001','S1',1,['overlay_review','burned_in_or_unknown'])
        observed=dict(item,original_holds=item['holds'],status='header_readable_now',
            burned_in_attribute='missing_or_empty',overlay_presence='present',overlay_payload_present=False,
            overlay_bit_position_present=False,source_hash_check='not_performed')
        with patch('batch_hold_diagnostics.inspect_record',return_value=observed):
            result=tracks.prepare_tracks({'patients':['P001'],'release_allowed':False,'records':[item]}, {},{},True)
        self.assertIn('annotation_flag_missing_not_proven_burned_in',result['policy_observations'])
        self.assertIn('overlay_metadata_without_detected_payload_or_bit_position',result['policy_observations'])
        public=json.dumps(tracks.public_result(result))
        self.assertNotIn('P001',public)
        self.assertNotIn('S1',public)
