"""Static integration checks for same-login OHIF clinical pilot assets."""
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]

class OHIFEmbedTests(unittest.TestCase):
    def test_viewer_control_and_same_origin(self):
        html=(ROOT/"templates/index.html").read_text()
        js=(ROOT/"static/app.js").read_text()
        self.assertIn('id="ohif-pilot-panel"', html)
        self.assertIn('id="ohif-iframe"', html)
        self.assertIn("'/viewer?StudyInstanceUIDs='", js)
        self.assertIn("'/api/study/'", js)
        self.assertNotIn("http://127.0.0.1:3001", js)

    def test_clinical_config_no_public_endpoints(self):
        script=(ROOT/"ohif_poc/clinical_app_config_392.js").read_text()
        self.assertIn("qidoRoot: '/ohif/dicomweb'",script)
        self.assertIn("wadoRoot: '/ohif/dicomweb'",script)
        self.assertIn("supportsStow: false",script)
        self.assertNotIn("cloudfront.net",script)
        self.assertNotIn("http://",script)
        self.assertNotIn("https://",script)

    def test_ohif_iframe_sized_by_csp_compatible_stylesheet(self):
        html=(ROOT/"templates/index.html").read_text()
        css=(ROOT/"static/app.css").read_text()
        self.assertIn('id="ohif-iframe"',html)
        self.assertNotIn('id="ohif-iframe" style=',html)
        self.assertIn('#ohif-iframe{display:block;',css)
        self.assertIn('width:100%;height:clamp(',css)
        self.assertIn('min-height:560px',css)

    def test_gate_disabled_until_allowlist_exists(self):
        source=(ROOT/"ohif_poc/clinical_dicomweb.py").read_text()
        self.assertIn("ohif-pilot-study.uid",source)
        self.assertIn("if not ALLOWLIST.is_file()",source)
        self.assertNotIn("app.run(",source)

if __name__ == "__main__":
    unittest.main()
