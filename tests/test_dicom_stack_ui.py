"""Synthetic static integration tests for stack viewer controls.

No clinical files, external requests, or browser runtime required.
"""
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class DicomStackUITests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = (ROOT/"templates/index.html").read_text(encoding="utf-8")
        cls.js = (ROOT/"static/app.js").read_text(encoding="utf-8")
        cls.css = (ROOT/"static/app.css").read_text(encoding="utf-8")

    def test_controls_exist_and_are_wired(self):
        for name in ("dicom-series","dicom-position","dicom-prev","dicom-next",
                     "dicom-position-label","dicom-review-progress"):
            self.assertIn('id="'+name+'"',self.html)
            self.assertIn("$('"+name+"')",self.js)

    def test_series_uid_grouping_and_scoped_instances(self):
        self.assertIn("meta.SeriesInstanceUID",self.js)
        self.assertIn("setupDicomSeries(d.images)",self.js)
        self.assertIn("currentSeries()?.images",self.js)
        self.assertIn("showSeries()",self.js)

    def test_wheel_keyboard_and_frame_navigation(self):
        self.assertIn("addEventListener('wheel'",self.js)
        self.assertIn("passive:false",self.js)
        self.assertIn("addEventListener('keydown'",self.js)
        self.assertIn("navigateStack(1)",self.js)
        self.assertIn("navigateStack(-1)",self.js)
        self.assertIn("X-Frames",self.js)

    def test_display_tracking_not_export_approval(self):
        self.assertIn("inspectedFrames.add",self.js)
        self.assertIn("Displayed is not clinically reviewed.",self.js)
        self.assertIn("Viewing frames does not constitute final review.",self.html)
        self.assertNotIn("inspectedFrames.size===total",self.js)

if __name__=="__main__":
    unittest.main()
