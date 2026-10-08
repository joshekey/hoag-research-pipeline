"""Restricted hospital-local SQL radiology index builder (development, not GUI-integrated).

Never executes SQL or writes to source shares. Output is PHI-bearing, root-only.
Uses validated positional fields from the HOAG schema reconnaissance.
Ambiguous duplicate keys or unsupported target statements abort the build.
"""
import argparse
import json
import os
import sqlite3
import stat
import tempfile
from collections import Counter
from pathlib import Path

from sql_report_probe import HEADER, MAX_LINE, UnsupportedStatement, parse_rows

TARGETS = {"labdataex", "labdata", "enc", "items"}
SCHEMA = """
CREATE TABLE narratives (
 report_id BLOB PRIMARY KEY, text BLOB NOT NULL, encounter_id BLOB,
 item_id BLOB, result_date BLOB, patient_ref BLOB,
 encounter_date BLOB, procedure_name BLOB
);
CREATE TABLE lab (
 report_id BLOB PRIMARY KEY, encounter_id BLOB, item_id BLOB, result_date BLOB
);
CREATE TABLE encounters (
 encounter_id BLOB PRIMARY KEY, patient_ref BLOB, encounter_date BLOB
);
CREATE TABLE procedures (
 item_id BLOB PRIMARY KEY, item_name BLOB
);
CREATE TABLE provenance (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

def _insert_unique(db, table, columns, vals):
    colnames = ",".join(columns)
    slots = ",".join("?" for _ in vals)
    key = vals[0]
    old = db.execute("SELECT " + colnames + " FROM " + table
                     + " WHERE " + columns[0] + "=?", (key,)).fetchone()
    if old is not None:
        if tuple(old) != tuple(vals):
            raise ValueError("Conflicting duplicate key in " + table)
        return
    db.execute("INSERT INTO " + table + "(" + colnames + ") VALUES (" + slots + ")", vals)

def build(sql_path, output_path):
    sql_path = Path(sql_path).resolve(strict=True)
    output_path = Path(output_path)
    if not sql_path.is_file() or sql_path == output_path.resolve():
        raise ValueError("Input must be an existing SQL file and distinct from output")
    if output_path.is_symlink() or output_path.exists():
        raise ValueError("Refusing to overwrite existing or symlinked index")
    if not output_path.parent.is_dir() or output_path.parent.is_symlink():
        raise ValueError("Output parent must already exist and not be a symlink")
    if (output_path.parent.stat().st_mode & 0o077) != 0:
        raise ValueError("Output directory must have mode 0700")
    os.umask(0o077)
    stats = Counter()
    fd, temp_name = tempfile.mkstemp(prefix=".sql-index-", suffix=".sqlite", dir=output_path.parent)
    os.close(fd)
    temp = Path(temp_name)
    try:
        with sqlite3.connect(temp) as db:
            db.executescript(SCHEMA)
            db.execute("BEGIN")
            with sql_path.open("rb", buffering=1048576) as handle:
                for line in handle:
                    m = HEADER.match(line[:2048])
                    if not m:
                        continue
                    table = (m.group(1) or m.group(2)).decode("ascii").lower()
                    if table not in TARGETS:
                        continue
                    stats[table + "_statements"] += 1
                    if len(line) > MAX_LINE or not line.rstrip().endswith(b";"):
                        raise ValueError("Unsupported or oversized targeted INSERT")
                    try:
                        for row in parse_rows(line[m.end():]):
                            stats[table + "_rows"] += 1
                            if table == "labdataex":
                                if len(row) != 28:
                                    raise ValueError("labdataex layout changed")
                                rid, narrative = row[0], row[5]
                                if rid and narrative and (b"FINDINGS:" in narrative.upper()
                                                          or b"IMPRESSION:" in narrative.upper()):
                                    _insert_unique(db, "narratives", ["report_id", "text"], [rid, narrative])
                            elif table == "labdata":
                                if len(row) < 7:
                                    raise ValueError("labdata row too short")
                                if row[0]:
                                    _insert_unique(db, "lab", ["report_id", "encounter_id",
                                                                "item_id", "result_date"],
                                                   [row[0], row[1], row[2], row[6]])
                            elif table == "enc":
                                if len(row) < 4:
                                    raise ValueError("enc row too short")
                                if row[0]:
                                    _insert_unique(db, "encounters", ["encounter_id", "patient_ref",
                                                                      "encounter_date"],
                                                   [row[0], row[1], row[3]])
                            elif table == "items":
                                if len(row) < 2:
                                    raise ValueError("items row too short")
                                if row[0]:
                                    _insert_unique(db, "procedures", ["item_id", "item_name"],
                                                   [row[0], row[1]])
                    except UnsupportedStatement:
                        raise ValueError("Unsupported expression in target SQL statement") from None
            db.execute("""UPDATE narratives SET
                encounter_id=(SELECT encounter_id FROM lab WHERE lab.report_id=narratives.report_id),
                item_id=(SELECT item_id FROM lab WHERE lab.report_id=narratives.report_id),
                result_date=(SELECT result_date FROM lab WHERE lab.report_id=narratives.report_id)""")
            db.execute("""UPDATE narratives SET
                patient_ref=(SELECT patient_ref FROM encounters WHERE encounters.encounter_id=narratives.encounter_id),
                encounter_date=(SELECT encounter_date FROM encounters WHERE encounters.encounter_id=narratives.encounter_id),
                procedure_name=(SELECT item_name FROM procedures WHERE procedures.item_id=narratives.item_id)""")
            # Fail closed on missing critical relationships. Do not consider these study matches.
            missing = db.execute("""SELECT count(*) FROM narratives WHERE encounter_id IS NULL
                OR patient_ref IS NULL OR item_id IS NULL""").fetchone()[0]
            if missing:
                raise ValueError("Unlinked narrative records; index not published")
            totals = {
                "narratives": db.execute("SELECT count(*) FROM narratives").fetchone()[0],
                "with_procedure_name": db.execute("SELECT count(*) FROM narratives WHERE procedure_name IS NOT NULL").fetchone()[0],
                "with_result_date": db.execute("SELECT count(*) FROM narratives WHERE result_date IS NOT NULL").fetchone()[0],
            }
            db.execute("INSERT INTO provenance VALUES (?,?)", ("source_bytes", str(sql_path.stat().st_size)))
            db.commit()
        os.chmod(temp, stat.S_IRUSR | stat.S_IWUSR)
        os.replace(temp, output_path)
        return totals, stats
    finally:
        temp.unlink(missing_ok=True)

def locate_sql_from_catalog():
    config = json.loads(Path("/etc/hoag-research/config.json").read_text())
    path = Path(config["state_dir"]) / "workflow.sqlite"
    with sqlite3.connect(path.as_uri() + "?mode=ro", uri=True) as db:
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():
            raise ValueError("Active HOAG job; index not started")
        rows = db.execute("SELECT path FROM files WHERE kind='sql' AND active=1 AND status='ok'").fetchall()
    if len(rows) != 1:
        raise ValueError("Expected exactly one active SQL dump")
    return rows[0][0]

if __name__ == "__main__":
    p = argparse.ArgumentParser(description="Hospital-local, PHI-bearing SQL report index builder")
    p.add_argument("--output", required=True, help="New file under an existing root-only directory")
    p.add_argument("--synthetic-sql", help="Synthetic test input only; omit for hospital catalog lookup")
    args = p.parse_args()
    totals, stats = build(args.synthetic_sql or locate_sql_from_catalog(), args.output)
    print("Restricted local index created. NO report approval implied.")
    for k, v in sorted(totals.items()):
        print(k + ":", v)
    for k, v in sorted(stats.items()):
        print(k + ":", v)
