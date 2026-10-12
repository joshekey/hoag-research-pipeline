"""Synthetic-only tests: never use clinical data in CI."""
import tempfile
import unittest
from pathlib import Path
from sql_report_probe import parse_rows, probe, UnsupportedStatement

class SQLReportProbeTests(unittest.TestCase):
    def test_escaped_text_and_null(self):
        values = b"('FINDINGS: text\\nIMPRESSION: good',NULL,5),('other','',-3);"
        rows = list(parse_rows(values))
        self.assertEqual(len(rows), 2)
        self.assertIn(b'\n', rows[0][0])
        self.assertIsNone(rows[0][1])

    def test_reject_sql_function(self):
        with self.assertRaises(UnsupportedStatement):
            list(parse_rows(b'(NOW());'))

    def test_aggregate_no_values(self):
        with tempfile.TemporaryDirectory() as tmp:
            filename = Path(tmp) / 'synthetic.sql'
            values = ['NULL'] * 28
            values[5] = "'FINDINGS: synthetic\\\\nIMPRESSION: synthetic'"
            filename.write_text('INSERT INTO ' + chr(96) + 'labdataex' + chr(96) + ' VALUES (' + ','.join(values) + ');\n')
            counts, scanned = probe(filename)
            self.assertGreater(scanned, 0)
            self.assertEqual(counts['labdataex.rows'], 1)
            self.assertEqual(counts['labdataex.candidate_narrative_column_6.FINDINGS:'], 1)
            self.assertFalse(any('synthetic' in key for key in counts))

    def test_wrong_width_not_counted(self):
        with tempfile.TemporaryDirectory() as tmp:
            filename = Path(tmp) / 'synthetic.sql'
            filename.write_text('INSERT INTO ' + chr(96) + 'hl7labnotes' + chr(96) + " VALUES ('a','IMPRESSION: synthetic');\n")
            counts, _ = probe(filename)
            self.assertEqual(counts['hl7labnotes.rows'], 0)
            self.assertEqual(counts['hl7labnotes.width_mismatch'], 1)

if __name__ == '__main__':
    unittest.main()
