"""Synthetic only OHIF DICOMweb protocol checks: no hospital files."""
import io
import unittest
import pydicom
from ohif_poc.synthetic_dicomweb import create_test_app, ROOT, STUDY, SERIES, SOPS

class SyntheticDicomwebTests(unittest.TestCase):
    def setUp(self):
        self.client=create_test_app().test_client()

    def test_qido_study_series_and_instances(self):
        a=self.client.get(ROOT+"/studies")
        self.assertEqual(a.status_code,200)
        self.assertEqual(a.content_type,"application/dicom+json")
        self.assertEqual(a.json[0]["0020000D"]["Value"][0],STUDY)
        series=self.client.get(ROOT+"/studies/"+STUDY+"/series")
        self.assertEqual(series.json[0]["0020000E"]["Value"][0],SERIES)
        instances=self.client.get(ROOT+"/studies/"+STUDY+"/series/"+SERIES+"/instances")
        self.assertEqual(len(instances.json),2)
        self.assertEqual(instances.headers["Cache-Control"],"no-store")

    def test_metadata_and_bulkdata_uri(self):
        response=self.client.get(ROOT+"/studies/"+STUDY+"/metadata")
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json),2)
        first=response.json[0]
        self.assertIn("BulkDataURI",first["7FE00010"])
        self.assertIn("frames/1",first["7FE00010"]["BulkDataURI"])
        self.assertEqual(first["00100010"]["vr"],"PN")

    def test_wado_instance_multipart_dicom(self):
        response=self.client.get(ROOT+"/studies/"+STUDY+"/series/"+SERIES+"/instances/"+SOPS[0])
        self.assertEqual(response.status_code,200)
        self.assertIn("multipart/related",response.content_type)
        raw=response.data.split(b"\r\n\r\n",1)[1].split(b"\r\n--ohif-synthetic-test--",1)[0]
        ds=pydicom.dcmread(io.BytesIO(raw))
        self.assertEqual(str(ds.SOPInstanceUID),SOPS[0])
        self.assertEqual(ds.pixel_array.shape,(2,2))

    def test_wado_frame_multipart(self):
        response=self.client.get(ROOT+"/studies/"+STUDY+"/series/"+SERIES+"/instances/"+SOPS[0]+"/frames/1")
        self.assertEqual(response.status_code,200)
        self.assertIn("multipart/related",response.content_type)
        self.assertIn(b"\x01\x00\x02\x00",response.data)

    def test_disallows_foreign_study_sop_and_frame(self):
        base=ROOT+"/studies/"+STUDY+"/series/"+SERIES+"/instances/"
        self.assertEqual(self.client.get(ROOT+"/studies/1.2.99/metadata").status_code,404)
        self.assertEqual(self.client.get(base+"1.2.99").status_code,404)
        self.assertEqual(self.client.get(base+SOPS[0]+"/frames/2").status_code,404)

    def test_no_upload_routes(self):
        self.assertEqual(self.client.post(ROOT+"/studies",data=b"fake").status_code,405)

if __name__=="__main__":
    unittest.main()
