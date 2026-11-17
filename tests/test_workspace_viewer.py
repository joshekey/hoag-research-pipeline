"""Real synthetic files through the authenticated workspace and DICOMweb API."""
import base64
import gc
import json
from pathlib import Path
from unittest.mock import patch
import unittest

import pydicom
from pydicom.encaps import encapsulate
from pydicom.uid import JPEGBaseline8Bit
import engine
from store import database
import test_workflow as workflow
from webapp import create_app
from ohif_poc import clinical_dicomweb as gateway


class WorkspaceViewerTests(unittest.TestCase):
    def setUp(self):
        workflow.WorkflowTests.setUp(self)
        self.addCleanup(gc.collect)
        self.gate = self.root / 'viewer.uid'
        self.gate.write_text('ALL_INDEXED')
        gate = patch.object(gateway, 'ALLOWLIST', self.gate)
        gate.start()
        self.addCleanup(gate.stop)
        self.assets = self.root / 'viewer-assets'
        self.assets.mkdir()
        self.config['ohif_assets'] = str(self.assets)
        self.client = create_app(self.config).test_client()
        self.auth = {'Authorization': 'Basic ' + base64.b64encode(b'admin:synthetic-password').decode()}
        self.base = '/ohif/dicomweb/studies/' + self.uid
        self.instance = self.base + '/series/' + self.series + '/instances/' + str(self.original.SOPInstanceUID)

    def get(self, path):
        return self.client.get(path, headers=self.auth)

    def test_review_queue_includes_prepared_exams_and_excludes_completed(self):
        for state, count in [('candidate', 1), ('review', 1), ('approved', 0), ('exported', 0)]:
            with self.subTest(state=state):
                with database(self.config) as db:
                    db.execute('UPDATE studies SET state=?', (state,))
                self.assertEqual(self.get('/api/studies?paged=1&workflow=review').json['total'], count)
                self.assertEqual(self.get('/api/studies?paged=1&q=SYNTHETIC123').json['total'], 1)

    def test_patient_search_accepts_displayed_names_and_first_last_order(self):
        for query in ('Synthetic Person', 'Person Synthetic', 'Synthetic, Person', 'Synthetic^Person'):
            with self.subTest(query=query):
                response = self.client.get('/api/studies', query_string={'paged': '1', 'q': query}, headers=self.auth)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['total'], 1)
        self.assertEqual(self.get('/api/studies?paged=1&q=Unrelated+Person').json['total'], 0)

    def test_configuration_is_authenticated_same_origin_and_iframe_compatible(self):
        self.assertEqual(self.client.get('/app-config.js').status_code, 401)
        response = self.get('/app-config.js')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"useSharedArrayBuffer: 'FALSE'", response.data)
        self.assertIn(b"qidoRoot: selectedExamRoot", response.data)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')

    def test_missing_viewer_assets_and_closed_gate_have_visible_reasons(self):
        response = self.get('/api/study/' + self.uid + '/ohif-status').json
        self.assertFalse(response['enabled'])
        self.assertIn('files are missing', response['reason'])
        (self.assets / 'index.html').write_text('<p>Synthetic viewer shell</p>')
        self.assertTrue(self.get('/api/study/' + self.uid + '/ohif-status').json['enabled'])
        self.gate.unlink()
        self.assertFalse(self.get('/api/study/' + self.uid + '/ohif-status').json['enabled'])

    def test_metadata_resolves_catalog_once_and_reads_each_header_once(self):
        for i in range(5):
            workflow.image(self.sources[0] / f'frame-{i}.dcm', self.uid, self.series)
        engine.scan(self.config)
        with patch.object(gateway, 'scoped_catalog', wraps=gateway.scoped_catalog) as catalog, \
                patch.object(pydicom, 'dcmread', wraps=pydicom.dcmread) as read:
            response = self.get(self.base + '/metadata')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json), 6)
        self.assertEqual(catalog.call_count, 1)
        self.assertEqual(read.call_count, 6)
        self.assertNotIn('InlineBinary', response.get_data(as_text=True))
        self.gate.unlink()
        self.assertEqual(self.get(self.base + '/metadata').status_code, 404)

    def test_native_multiframe_returns_requested_frame_and_rejects_out_of_bounds(self):
        ds = pydicom.dcmread(self.path)
        first = ds.PixelData
        second = bytes(reversed(first))
        ds.NumberOfFrames = 2
        ds.PixelData = first + second
        ds.save_as(self.path, enforce_file_format=True)
        engine.scan(self.config)
        for frame, expected in [(1, first), (2, second)]:
            response = self.get(self.instance + '/frames/' + str(frame))
            self.assertEqual(response.status_code, 200)
            self.assertIn(b'\r\n\r\n' + expected + b'\r\n--', response.data)
            self.assertIn('transfer-syntax=1.2.840.10008.1.2.1', response.content_type)
        self.assertEqual(self.get(self.instance + '/frames/3').status_code, 404)

    def test_compressed_multiframe_returns_original_requested_codestream(self):
        ds = pydicom.dcmread(self.path)
        ds.file_meta.TransferSyntaxUID = JPEGBaseline8Bit
        ds.NumberOfFrames = 2
        ds.PixelData = encapsulate([b'SYNTHETIC_FRAME_A', b'SYNTHETIC_FRAME_B'])
        ds['PixelData'].is_undefined_length = True
        ds.save_as(self.path, enforce_file_format=True)
        engine.scan(self.config)
        response = self.get(self.instance + '/frames/2')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'SYNTHETIC_FRAME_B', response.data)
        self.assertNotIn(b'SYNTHETIC_FRAME_A', response.data)
        self.assertIn('image/jpeg', response.content_type)

    def test_source_mutation_and_wrong_instance_remain_blocked(self):
        self.assertEqual(self.get(self.instance + '/frames/1').status_code, 200)
        self.assertEqual(self.get(self.base + '/series/1.2.999/instances/1.2.999/frames/1').status_code, 404)
        self.path.write_bytes(b'changed synthetic source')
        self.assertEqual(self.get(self.instance + '/frames/1').status_code, 400)

    def test_viewer_discovery_preserves_patient_and_exam_context(self):
        response = self.get('/ohif/dicomweb/studies?StudyInstanceUID=' + self.uid)
        self.assertEqual(response.status_code, 200)
        study = response.json[0]
        self.assertEqual(study['00100010']['Value'][0]['Alphabetic'], 'Synthetic^Person')
        self.assertEqual(study['00100020']['Value'], ['SYNTHETIC123'])
        self.assertEqual(study['00080050']['Value'], ['ACC_TEST_123'])
        self.assertEqual(study['00080020']['Value'], ['20200102'])

    def test_scoped_viewer_cannot_discover_or_load_a_different_exam(self):
        from pydicom.uid import generate_uid
        other = generate_uid()
        workflow.image(self.sources[0] / 'other.dcm', other, generate_uid())
        engine.scan(self.config)
        root = '/ohif/dicomweb/scope/' + self.uid
        response = self.get(root + '/studies')
        self.assertEqual(response.status_code, 200)
        self.assertEqual([r['0020000D']['Value'][0] for r in response.json], [self.uid])
        self.assertEqual(self.get(root + '/studies?StudyInstanceUID=' + other).status_code, 404)
        self.assertEqual(self.get(root + '/studies/' + other + '/metadata').status_code, 404)
        metadata = self.get(root + '/studies/' + self.uid + '/metadata')
        self.assertEqual(metadata.status_code, 200)
        self.assertTrue(metadata.json[0]['7FE00010']['BulkDataURI'].startswith(root + '/'))

    def test_installer_copies_every_dashboard_asset_and_viewer_config(self):
        source = (Path(__file__).parents[1] / 'install.sh').read_text()
        self.assertIn('"$ROOT"/static/*.js "$ROOT"/static/*.css', source)
        self.assertIn('"$ROOT/ohif_poc/clinical_app_config_392.js"', source)


if __name__ == '__main__':
    unittest.main()
