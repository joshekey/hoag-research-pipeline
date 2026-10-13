# Backend batch reconciliation, version 1.50

This standalone tool investigates the reported 14 missing indexed TXT files, 25
missing TXT locations and seven unresolved DICOM folders. It does not change the
GUI, mount configuration, source files, approvals, crosswalk inputs or workflow
database. It does not create anonymized data or release datasets. The previous
aggregate preflight establishes the starting counts, not the cause of each gap.
Actual causes require running this tool inside the hospital environment.

## Reviewed deployment: code only

Use the existing `codex/research-workbench` checkout. No installer, service restart,
remount, database migration, index rebuild or application configuration is needed.
The server needs the existing application Python runtime and modules.

```bash
(
set -e
cd "$HOME/hoag-research-pipeline-v1.5.0"
git pull --ff-only origin codex/research-workbench
sudo install -m 0644 batch_reconcile.py /opt/hoag-research/batch_reconcile.py
)
```

After reviewing this document, a hospital operator may run the dry run:

```bash
cd /opt/hoag-research
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python \
  /opt/hoag-research/batch_reconcile.py \
  --crosswalk-dir /home/sratai/crosswalk
```

This command writes only a new restricted local report directory beneath
`/var/lib/hoag-research/batch-reconciliation/run-<random-id>/`. Directories are
0700 and files are 0600. The inputs must be readable by `hoag-indexer`. A shared
or symlinked report directory is rejected. Keep the private reports out of the
delivery share and out of GitHub, Codex, email, and external logs.

Only aggregate counters, a random run ID and a release-disabled flag go to stdout.
`manifest.private.json` contains the authorized patient crosswalk, paths, document
IDs, DICOM header evidence, report hashes, instance file metadata and candidate
comparisons. `exceptions.private.json` contains local troubleshooting details.
Neither contains report narratives. Nonzero exit status indicates an incomplete
run. A completed dry run may still contain unresolved exceptions and is never an
export authorization. A failed run must not be interpreted as a clean inventory.

Rollback of this code-only addition is simply to stop invoking it. It installs no
service, scheduled task or hooks. An operator may remove only
`/opt/hoag-research/batch_reconcile.py` to withdraw the command. Preserve restricted
reports according to hospital retention requirements. Do not restore the research
database, change fstab, remove credentials or revert mounts for this rollback.

## How gaps are investigated

* Match documents to the patient crosswalk by exact `PatientId`/`PATIENTID`, never
  assume the crosswalk key equals DICOM MRN. Require unique patient keys, GUIDs and
  `P###` labels. Reject missing or duplicated mapping keys.
* Match TXT basenames against active and inactive catalog entries. Differentiate
  absent entries, inactive/error entries, ambiguous filenames, changed content,
  source access failures and duplicate source use. Compare current report hashes
  with the catalog snapshot. Do not rebuild report evidence or query the SQL broker.
* For blank TXT locations, look for the explicit document ID plus `.txt` as a
  discovery lead. Such a recovered lead remains held for review. Do not infer a
  report from PDF/TIFF contents or automatically run OCR.
* Windows drive paths cannot establish a Linux location on their own. If a missing
  filename has no usable index entry, record `txt_windows_prefix_mapping_needed`.
  An authorized operator can supply a private JSON file containing exact prefix
  translations, then rerun with `--path-map /home/sratai/crosswalk/path-map.json`:

  ```json
  [
    {"windows_prefix":"X:\\ExampleReports", "linux_prefix":"/mnt/hoag-bulk/ExampleReports"}
  ]
  ```

  This is a synthetic example, not a guessed hospital mapping. The operator must
  replace both prefixes with verified locations. Probes must stay within configured
  read-only sources. Each probe records readability and whether it lies within the
  last saved scan scope. Finding a file does not change the index or accept a match.
* Translate DICOM UNC paths using the kernel's current CIFS mount source and root,
  including share names with spaces and explicit subdirectory mounts. Compare
  whole path components and reject traversal, symlinks and ambiguous mappings.
  Record the mapped path, nearest existing parent and case-only final-component
  leads for unresolved folders; never silently rewrite a patient folder name.
* Verify source mounts are read-only. Verify the output's effective remote path is
  exactly `//vnwpradiant01/ozgur imaging/aaa_Anon_Studies` and is not read-only.
  No write probe or remount occurs. Exclude that output subdirectory when it is
  reachable through the imaging share as well as through the output mount.
* Inspect DICOM headers only, without reindexing. Record instance counts, missing
  identifiers, duplicate SOP UIDs, cross-patient Study UID reuse, inconsistent
  patient/examination headers and directory read errors. The default discovery
  limit is 250,000 files; reaching it makes the run incomplete. An operator can
  explicitly raise `--max-files` after reviewing storage scope.

## Association rules and review

A suggested association needs independent evidence from the actual DICOM header
and actual TXT report. Full normalized patient name and DOB must agree, and the
DICOM name must also agree with the crosswalk's name. For the examination, require
either the exact Study UID, or exact accession plus explicit examination date.
Any conflicting populated patient ID, name, DOB, accession, Study UID or exam date
holds the comparison. A document scan date, filename, or report result date is not
examination evidence. MRN alone is not sufficient, and punctuation is not stripped
to manufacture an identifier match. Multiple reported patient names are a conflict.

Missing evidence, duplicate sources/instances, crosswalk conflicts and reports
eligible for multiple studies remain held. Even an evidence-sufficient result is
`evidence_sufficient_pending_review`, with `accepted=false` and
`release_allowed=false`. Reviewers must resolve exceptions in the authorized
environment. The patient-level package design does not require one-to-one output
pairing; these comparisons provide traceable evidence and detect incorrect grouping.
Reports with insufficient examination evidence are retained in the private manifest
for a separate explicit decision, not silently omitted or assigned.

## Validation plan for a future anonymization stage

This stage is not implemented or invoked by this tool. Before implementing or
running it, review and approve the following plan:

1. Reconcile all 80 patients and all 926 document records. Explicitly resolve or
   authorize exclusions for the 14 missing TXT records, 25 missing locations and
   seven unresolved DICOM folders. Reconcile counts per patient and overall.
2. Approve the private manifest and crosswalk mapping, retaining `P###` labels.
   Bind subsequent actions to input crosswalk hashes, report hashes, instance
   fingerprints and a reviewed manifest hash. Revalidate sources immediately before
   processing. Any changed or unreadable input returns to review.
3. Write copies into an isolated 0700 staging directory on restricted hospital
   storage, outside source mounts and outside the delivery share. Never modify
   originals. Preserve the private crosswalk separately from delivery packages.
4. Define and approve the de-identification profile: replace patient name/ID with
   the authorized label, retain existing Patient Age, and explicitly decide missing
   age handling and date removal/shifting. Review institution/provider fields,
   nested sequences, private elements, all identifying text and UID remapping.
   Do not change the app's existing anonymization profile silently.
5. Validate every supported DICOM object, all frames and transfer syntaxes. Check
   pixel decoding, dimensions, frame counts and pixel-value preservation where no
   authorized redaction is needed. Inspect burned-in text/overlays; require an
   explicit pixel-redaction review where appropriate. Route unsupported objects,
   decode failures, missing frames, inconsistent age or identifying content to
   exceptions. Header-only inspection is not pixel validation.
6. Apply the app's existing TXT redaction to every included report in staging.
   Review for residual identifiers and verify retained clinical content. Use
   anonymous filenames and the same `P###` identity as DICOM. Keep source hashes and
   report membership in the private audit, not in identifying delivery filenames.
7. Independently validate output metadata, recursive sequences, pixels, report
   contents, anonymized paths, UID consistency, patient grouping and counts. Record
   validation results and reviewer authorization. Do not let a generic dry-run
   success flag satisfy any release gate.
8. Require explicit hospital release authorization before copying reviewed packages
   into `/mnt/hoag-output`. Recheck its exact effective SMB subdirectory immediately
   before delivery. Stage delivery atomically, verify checksums/counts, and provide
   an anonymous package manifest. Never include the identifying crosswalk.

No diagnosis, image interpretation, de-identification certification or production
SMB verification is established by synthetic software tests.
