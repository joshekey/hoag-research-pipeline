"""Synthetic tests for restricted local index builder; no clinical records."""
import sqlite3
import tempfile
import unittest
from pathlib import Path
from sql_report_index import build

def insert(table, values):
    return "INSERT INTO `" + table + "` VALUES (" + ",".join(values) + ");\n"

def quoted(value):
    return "'" + value.replace("'", "''") + "'"

class ReportIndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.source = root / "sample.sql"
        self.restricted = root / "restricted"
        self.restricted.mkdir(mode=0o700)
        self.output = self.restricted / "report-index.sqlite"

    def write_source(self, report_id="101", encounter_id="501"):
        extended = ["NULL"] * 28
        extended[0] = quoted(report_id)
        extended[5] = quoted("FINDINGS: Synthetic / IMPRESSION: Synthetic")
        lab = ["NULL"] * 7
        lab[0] = quoted(report_id)
        lab[1] = quoted(encounter_id)
        lab[2] = quoted("801")
        lab[6] = quoted("2020-01-02")
        self.source.write_text(
            insert("labdataex", extended)
            + insert("labdata", lab)
            + insert("enc", [quoted(encounter_id), quoted("patient-synthetic"),
                             quoted("doctor-synthetic"), quoted("2020-01-02")])
            + insert("items", [quoted("801"), quoted("MR LUMBAR")])
        )

    def test_join_and_restricted_permissions(self):
        self.write_source()
        totals, _ = build(self.source, self.output)
        self.assertEqual(totals["narratives"], 1)
        self.assertEqual(totals["with_procedure_name"], 1)
        self.assertEqual(self.output.stat().st_mode & 0o777, 0o600)
        with sqlite3.connect(self.output) as db:
            self.assertEqual(db.execute(
                "SELECT procedure_name FROM narratives").fetchone()[0], b"MR LUMBAR")

    def test_missing_link_does_not_publish(self):
        self.write_source(encounter_id="no-such-encounter")
        # Rewrite encounter row to unrelated encounter
        raw = self.source.read_text().replace(
            "INSERT INTO `enc` VALUES ('no-such-encounter'",
            "INSERT INTO `enc` VALUES ('other-encounter'")
        self.source.write_text(raw)
        with self.assertRaisesRegex(ValueError, "Unlinked"):
            build(self.source, self.output)
        self.assertFalse(self.output.exists())

    def test_refuse_overwrite(self):
        self.write_source()
        self.output.write_bytes(b"existing")
        with self.assertRaisesRegex(ValueError, "overwrite"):
            build(self.source, self.output)
        self.assertEqual(self.output.read_bytes(), b"existing")

    def test_no_narrative_no_results(self):
        self.write_source()
        self.source.write_text(self.source.read_text().replace("FINDINGS:", "HISTORY:").replace("IMPRESSION:", "CONCLUSION:"))
        totals, _ = build(self.source, self.output)
        self.assertEqual(totals["narratives"], 0)

if __name__ == "__main__":
    unittest.main()
