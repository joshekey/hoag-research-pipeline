"""Authenticated preview windows preserve frame geometry and study binding."""
import io
import unittest
import numpy as np
import pydicom
from PIL import Image
import engine
from store import database
import test_workflow as workflow
import test_tabbed_workflow as tabbed


class NativeReviewViewerTests(unittest.TestCase):
    setUp = workflow.WorkflowTests.setUp
    client = tabbed.TabbedWorkflowTests.client

    def image_id(self):
        with database(self.config) as db:
            return db.execute("SELECT id FROM files WHERE kind='dicom'").fetchone()[0]

    def test_preview_requires_auth_and_binds_selected_exam(self):
        client, auth = self.client()
        uri = '/api/image/' + str(self.image_id())
        self.assertEqual(client.get(uri).status_code, 401)
        response = client.get(uri + '?study_uid=' + self.uid, headers=auth)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers['Cache-Control'], 'no-store')
        self.assertEqual(client.get(uri + '?study_uid=1.2.999', headers=auth).status_code, 400)

    def test_custom_windows_and_monochrome_inversion_preserve_geometry(self):
        self.original.PhotometricInterpretation = 'MONOCHROME1'
        self.original.save_as(self.path, enforce_file_format=True)
        engine.scan(self.config)
        client, auth = self.client()
        response = client.get(f'/api/image/{self.image_id()}?window=custom&center=32&width=64', headers=auth)
        self.assertEqual(response.status_code, 200)
        rendered = np.array(Image.open(io.BytesIO(response.data)))
        expected = 255 - (np.arange(64).reshape(8, 8) / 63 * 255).astype('uint8')
        np.testing.assert_array_equal(rendered, expected)
        self.assertEqual((response.headers['X-Rows'], response.headers['X-Columns']), ('8', '8'))

    def test_invalid_windows_and_frame_indices_fail_with_recovery_errors(self):
        client, auth = self.client()
        uri = '/api/image/' + str(self.image_id())
        for query in ['window=missing', 'window=custom&width=0&center=0', 'window=custom&width=2&center=nan', 'window=dicom', 'frame=-1', 'frame=1']:
            with self.subTest(query=query):
                response = client.get(uri + '?' + query, headers=auth)
                self.assertEqual(response.status_code, 400)
                self.assertIn('error', response.get_json())

    def test_multiframe_preview_decodes_only_selected_frame(self):
        self.original.NumberOfFrames = 2
        pixels = np.arange(128, dtype='<u2').reshape(2, 8, 8)
        self.original.PixelData = pixels.tobytes()
        self.original.save_as(self.path, enforce_file_format=True)
        engine.scan(self.config)
        client, auth = self.client()
        images = []
        for frame in (0, 1):
            response = client.get(f'/api/image/{self.image_id()}?frame={frame}&window=custom&center=64&width=128', headers=auth)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers['X-Frames'], '2')
            images.append(np.array(Image.open(io.BytesIO(response.data))))
        self.assertTrue((images[1] > images[0]).all())
