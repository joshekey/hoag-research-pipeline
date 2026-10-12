"""Read-only summary of historical study/report lead uniqueness.

Runs inside the hospital server. Outputs aggregate counts only.
No modifications to source images, reports, database or approval state.
"""
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path

import engine
from emr_txt_leads import exam_dates, exam_terms
from historical_emr_overlap import build_historical_index
from txt_candidate_discovery import clues


def summarize(config):
    dbfile = Path(config["state_dir"]) / "workflow.sqlite"
    with sqlite3.connect(dbfile.as_uri() + "?mode=ro", uri=True) as db:
        db.row_factory = sqlite3.Row
        pending = db.execute(
            "SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')"
        ).fetchone()[0]
        if pending:
            raise ValueError("Processing job is active")
        dicom = [r[0] for r in db.execute(
            "SELECT metadata FROM files WHERE kind='dicom' AND active=0 AND status='ok'"
        )]
        reports = [dict(r) for r in db.execute(
            "SELECT * FROM files WHERE kind='report' AND active=0 AND status='ok'"
        )]
    studies, _ = build_historical_index(dicom)
    by_name = defaultdict(set)
    for uid, info in studies.items():
        if len(info["names"]) == 1 and len(info["dates"]) == 1:
            by_name[next(iter(info["names"]))].add(uid)
    candidates = defaultdict(set)
    counts = Counter()
    for row in reports:
        try:
            body, actual = engine.read_report(config, row)
            if actual != json.loads(row["metadata"]).get("hash"):
                counts["changed_reports"] += 1
                continue
        except (OSError, ValueError, UnicodeError, KeyError):
            counts["unreadable_reports"] += 1
            continue
        fields = clues(body, Path(row["path"]).stem)
        dates = exam_dates(body)
        modes, _ = exam_terms(body)
        if not modes:
            modes = fields["modalities"]
        for uid in by_name.get(fields["name"], ()):
            study = studies[uid]
            if study["dates"] & dates and study["modalities"] & modes:
                candidates[uid].add(row["id"])
    counts["strong_lead_studies"] = len(candidates)
    counts["strong_lead_pairs"] = sum(map(len, candidates.values()))
    counts["studies_with_one_report_candidate"] = sum(
        len(v) == 1 for v in candidates.values()
    )
    counts["studies_with_multiple_report_candidates"] = sum(
        len(v) > 1 for v in candidates.values()
    )
    return dict(counts)


if __name__ == "__main__":
    from store import load_config
    result = summarize(load_config("/etc/hoag-research/config.json"))
    print("=== HISTORICAL LEAD UNIQUENESS (AGGREGATES ONLY) ===")
    print(json.dumps(result, indent=2, sort_keys=True))
    print("No identifiers, report contents, filenames or crosswalks displayed.")
    print("Read-only; no reindexing, approvals or exports.")
