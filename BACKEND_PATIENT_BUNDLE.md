# Patient DICOM + redacted TXT/SQL bundles, version 1.50

The combined runner is batch_patient_bundle.py. It groups by P### patient, never
by report/exam pairs. It uses the existing DICOM stage plan and its review gates.
Reports are optional. All cohort patients remain in the plan; missing sources and
unverified objects remain held without blocking other verified objects.

Prepare reads all indexed TXT reports through the existing exact-name selector
and ALL SQL narrative catalog pages through the private broker (not just SQL
reports previously associated in the workflow database). SQL previews use signed
broker references and SHA256. Catalog snapshot changes, preview failures and
unavailable SQL are recorded as incomplete/held, not silently claimed complete.
This performs no reindexing, SQL source modification or workflow database writes.
Selection is exact first/last-name with existing ambiguity/identifier holds.

Processing stages DICOM objects only with existing authoritative identity,
profile and all-frame review attestations. A matching reviewed template beside
the source staging plan is carried forward; a missing/empty template never
becomes approved automatically. --reviews may supply a separately reviewed
private ledger bound to the same DICOM plan. The legacy reports list must be
empty when using this combined runner to avoid two competing report writers.

Both TXT and SQL reports ALWAYS pass through engine.sanitize_report and the
installed local NLP model before being written. SQL XML/SOAP is rendered to
plain text first; unresolved markup is held. Known patient names and identifiers
are supplied to the redactor and checked for residual matches afterwards.
There is no raw-report copying fallback. NLP/source/redaction failures hold the
report; they do not block independently verified DICOM.

Restricted output layout:

```text
P001/DICOM/study-<anonymous UID>/instance-00000001.dcm
P001/reports/report-000001-txt.txt
P001/reports/report-000002-sql.txt
```

Original PatientAge is retained by the DICOM profile. Anonymous P### identity is
consistent. Copies preserve originals. DICOM read-back validation remains active;
redacted reports require independent review because automated NLP cannot prove
that every identifier is removed. Release stays disabled; nothing is written
to /mnt/hoag-output by this runner. Do not copy the private run folder into the
delivery share: it also contains private plans, identifier evidence and ledgers.

## Hospital commands

```bash
cd "$HOME/hoag-research-workbench"
git switch codex/research-workbench
git pull --ff-only origin codex/research-workbench
sudo install -m 0644 batch_patient_bundle.py batch_report_selection.py \
  batch_dicom_stage.py batch_stage_validation.py batch_reconcile.py batch_readiness.py \
  /opt/hoag-research/
cd /opt/hoag-research
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python batch_patient_bundle.py --prepare-latest
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python batch_patient_bundle.py --process-latest
```

Preparation finds the latest stage-plan.private.json and its hash-matching
reconciliation manifest. It creates a new private run with bundle-plan.private.json
and review-template.private.json. Processing uses the latest bundle plan, rechecks
sources and resumes through private DICOM/report checkpoints. Do not run multiple
preparations or edit plans while a run is active. Repeat --process-latest to resume
the same run; do not prepare again for resume. Use --reviews <private ledger path>
when the existing reviewed DICOM ledger has another filename. All ledger paths
must remain in the private batch-reconciliation directory with protected ownership
and permissions. Empty ledgers skip DICOM but still redact selected reports.

Progress and final output contain counts/phases only. Report plans and checkpoints
stay local. Hard-kill recovery requires confirming processes stopped before an
administrator removes a stale .bundle-lock or .stage-lock. Existing output with
missing/mismatched checkpoint hashes is held rather than overwritten. Partial
files remain private, never released.

No service restart, mount change or database migration is needed. Rollback is to
stop using this standalone runner and preserve its private outputs for audit.
The previous DICOM and report selection commands remain available unchanged.
