"""Synthetic only OHIF DICOMweb protocol checks: no hospital files."""
import io
import unittest
import pydicom
from ohif_poc.synthetic_dicomweb import create_test_app, ROOT, STUDY, SERIES, SOPS, SERIES_IDS, COUNTS, INSTANCES

class SyntheticDicomwebTests(unittest.TestCase):
    def setUp(self):
        self.client=create_test_app().test_client()

    def test_qido_study_series_and_instances(self):
        a=self.client.get(ROOT+"/studies")
        self.assertEqual(a.status_code,200)
        self.assertEqual(a.content_type,"application/dicom+json")
        self.assertEqual(a.json[0]["0020000D"]["Value"][0],STUDY)
        series=self.client.get(ROOT+"/studies/"+STUDY+"/series")
        self.assertEqual(len(series.json),6)
        self.assertEqual(series.json[0]["0020000E"]["Value"][0],SERIES)
        self.assertEqual(sum(x["00201209"]["Value"][0] for x in series.json),157)
        instances=self.client.get(ROOT+"/studies/"+STUDY+"/series/"+SERIES+"/instances")
        self.assertEqual(len(instances.json),COUNTS[0])
        self.assertEqual(instances.headers["Cache-Control"],"no-store")

    def test_metadata_and_bulkdata_uri(self):
        response=self.client.get(ROOT+"/studies/"+STUDY+"/metadata")
        self.assertEqual(response.status_code,200)
        self.assertEqual(len(response.json),157)
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
        self.assertEqual(ds.pixel_array.shape,(64,64))

    def test_wado_frame_multipart(self):
        response=self.client.get(ROOT+"/studies/"+STUDY+"/series/"+SERIES+"/instances/"+SOPS[0]+"/frames/1")
        self.assertEqual(response.status_code,200)
        self.assertIn("multipart/related",response.content_type)
        self.assertGreater(len(response.data),8192)

    def test_disallows_foreign_study_sop_and_frame(self):
        base=ROOT+"/studies/"+STUDY+"/series/"+SERIES+"/instances/"
        self.assertEqual(self.client.get(ROOT+"/studies/1.2.99/metadata").status_code,404)
        self.assertEqual(self.client.get(base+"1.2.99").status_code,404)
        self.assertEqual(self.client.get(base+SOPS[0]+"/frames/2").status_code,404)

    def test_all_six_series_have_unique_instances(self):
        seen=set()
        for series, count in zip(SERIES_IDS, COUNTS):
            response=self.client.get(ROOT+"/studies/"+STUDY+"/series/"+series+"/instances")
            self.assertEqual(response.status_code,200)
            self.assertEqual(len(response.json),count)
            for metadata in response.json:
                sop=metadata["00080018"]["Value"][0]
                self.assertNotIn(sop,seen)
                seen.add(sop)
                self.assertIn("/series/"+series+"/",metadata["7FE00010"]["BulkDataURI"])
        self.assertEqual(len(seen),157)

    def test_rejects_sop_from_different_series(self):
        wrong_series=SERIES_IDS[1]
        url=ROOT+"/studies/"+STUDY+"/series/"+wrong_series+"/instances/"+SOPS[0]
        self.assertEqual(self.client.get(url).status_code,404)
        self.assertEqual(self.client.get(url+"/metadata").status_code,404)

    def test_distinct_frames_between_scroll_positions(self):
        path=ROOT+"/studies/"+STUDY+"/series/"+SERIES+"/instances/"
        first=self.client.get(path+SOPS[0]+"/frames/1")
        second=self.client.get(path+SOPS[1]+"/frames/1")
        self.assertNotEqual(first.data,second.data)

    def test_no_upload_routes(self):
        self.assertEqual(self.client.post(ROOT+"/studies",data=b"fake").status_code,405)

if __name__=="__main__":
    unittest.main()
