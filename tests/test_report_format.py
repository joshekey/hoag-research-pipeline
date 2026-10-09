"""Synthetic SOAP/XML report rendering tests; no real clinical records."""
import unittest
from report_format import format_report

XML = """<?xml version="1.0" encoding="ISO-8859-1"?>
<?xml-stylesheet href="/fake/report.xsl" type="text/xsl"?>
<SOAP-ENV:Envelope xmlns:SOAP-ENV="http://schemas.xmlsoap.org/soap/envelope/">
<SOAP-ENV:Body><return><facilityInfo><facName>Example Facility</facName></facilityInfo>
<labResult><labName>MRI LUMBAR SPINE WO CONTRAST</labName>
<findings>Small synthetic disc bulge at L4-L5.</findings>
<impression>1. Synthetic conclusion. &amp; follow-up.</impression>
</labResult></return></SOAP-ENV:Body></SOAP-ENV:Envelope>"""

class ReportFormatTests(unittest.TestCase):
    def test_xml_report_readable_and_order_preserved(self):
        shown, status = format_report(XML)
        self.assertEqual(status, "xml")
        self.assertIn("MRI LUMBAR SPINE WO CONTRAST", shown)
        self.assertIn("FINDINGS:\nSmall synthetic disc bulge at L4-L5.", shown)
        self.assertIn("IMPRESSION:\n1. Synthetic conclusion. & follow-up.", shown)
        self.assertLess(shown.index("FINDINGS"), shown.index("IMPRESSION"))
        self.assertNotIn("SOAP-ENV:Envelope", shown)
        self.assertNotIn("<labResult>", shown)

    def test_plain_report_not_modified(self):
        raw = "FINDINGS:\n  Synthetic.\nIMPRESSION: Synthetic."
        self.assertEqual(format_report(raw), (raw, "plain"))

    def test_broken_xml_kept_available_as_source(self):
        raw = "<SOAP:Envelope><broken>"
        self.assertEqual(format_report(raw), (raw, "unparsed"))

    def test_dtd_rejected_without_expansion(self):
        raw = '<!DOCTYPE r [<!ENTITY xx "private">]><r>&xx;</r>'
        self.assertEqual(format_report(raw), (raw, "unparsed"))

    def test_limit_exceeded_retains_original(self):
        raw = "<r>" + ("x" * (2 * 1024 * 1024)) + "</r>"
        self.assertEqual(format_report(raw), (raw, "unparsed"))

if __name__ == "__main__":
    unittest.main()
