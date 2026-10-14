# DICOM-first restricted staging — version 1.50

**Metadata-only staging option (still 1.50):** See [the pilot workflow](BACKEND_METADATA_PILOT.md).
Unknown burned-in annotation status can now defer pixel review while creating
restricted, identity-approved metadata-anonymized copies. The default workflow
below remains unchanged; it still requires completed pixel review. The new mode
uses separate approvals/checkpoints and keeps release disabled.

This is a proposed profile and hospital-local staging implementation, not a
release-approved deidentification system. No export/release command exists.
All crosswalk patients remain in the cohort. Missing reports do not gate DICOM;
missing patients and held objects do not gate other verified objects. Partial
patient packages must never be represented as complete. No index, mount, source,
application approval or workflow database is modified.

## Proposed profile: HOAG-1.50-STAGING-AGE-v1

* Existing pinned dicom-anonymizer 2026c baseline plus recursive local cleanup.
* PatientName and PatientID become the crosswalk P### label. All other patient
  group metadata is removed, except the original PatientAge. Missing age remains
  missing; malformed age is held. No age inference from birth dates.
* Remove dates/times, institution information, free-text descriptors, private
  tags and identifying nested attributes. Dates are removed, not shifted.
* Replace instance/study/series/reference UIDs deterministically using the
  existing secret-key HMAC mapping; retain class and transfer-syntax UIDs.
  Preserve the secret through updates. Changing it invalidates resume.
* Original pixels are preserved. Every staged image requires a hash-bound
  attestation that ALL frames have been reviewed for pixel identifiers and
  recognizable identifying features. BurnedInAnnotation=NO alone is insufficient.
* BurnedInAnnotation YES/unknown, overlays, unsupported SOP classes or failed
  decoding remain held. No automatic pixel redaction or silent duplicate removal.
* Different-byte duplicate SOP objects remain held even with matching pixels;
  identical copies are preserved as separate instance files with consistent UIDs.
* Independent read-back validates P### identity, original age, recursive prohibited
  metadata, mapped UIDs, file meta, preamble and exact decoded-pixel equality.
  This is not a complete IOD validator or a guarantee that every possible public
  attribute is free of PHI. Independent clinical/privacy and conformance review
  remain required before any future release implementation.

Reference: https://dicom.nema.org/medical/Dicom/current/output/chtml/part15/sect_E.3.html
The installed baseline is 2026c; this does not claim conformance to later revisions.

## Dry-run inventory and missing folders

Install batch_dicom_stage.py and batch_stage_validation.py alongside the existing
engine, store and batch helpers. Run as hoag-indexer with `--manifest` pointing
to the completed manifest in the private batch-reconciliation directory.
It rechecks folder mappings/access and hashes inventoried DICOM files. Private
exceptions include nearest existing parents and case-only folder leads. Never
rename or guess folder identities. A recovered folder needs a new read-only
reconciliation inventory before staging; no application reindex is required.

The private new run directory contains stage-plan.private.json and an EMPTY
review-template.private.json. No anonymized images are produced by this command.
Counts distinguish cohort, accessible/unresolved folders, technical holds and
objects awaiting review. These are inventory counts, not verified-patient counts.
All object identities require authoritative review, including apparently matching
names. The seven unresolved folders cannot be diagnosed from aggregate counts.

## Required review ledger, hospital only

The profile must be explicitly reviewed first. Set profile_reviewed_by to the
authorized reviewer; preserve the generated profile and plan_sha256. Each entry
in objects is keyed by the SOURCE SHA256 and contains:

```json
{
  "label": "P001",
  "identity_verified_by": "authorized reviewer reference",
  "authority_reference": "private patient-master or demographic-history evidence reference",
  "all_frames_reviewed_by": "authorized image reviewer reference",
  "pixel_identifiers_absent": true
}
```

The evidence must verify the exact source object belongs to that P### patient;
namespace aliases and historical changes require authoritative records. These
are trusted local human attestations, not cryptographically authenticated user
signatures. Never auto-fill them from similar names, ages, index membership, or
the dry-run recommendations. Conflicting within-study IDs deserve first review.
An empty ledger holds everything. Technical holds cannot be overridden here.

Optional reports entries contain path, source sha256, label and
ownership_verified_by. Their selection can come from the exact-name report dry
run with its conflicts held, followed by authorized review. Staging uses the
existing local NLP redactor, adds the P### label and retains reports as
redacted_staging_awaiting_independent_review. Failed or absent reports never
block DICOM. No report/exam pairing is used.

## Restricted execution and resume (after review only)

`batch_dicom_stage.py --resume-run <private run directory> --reviews <private ledger>`

Both paths must be inside state_dir/batch-reconciliation, with no symlinks, owned
by hoag-indexer and inaccessible to other users. New directories are 0700; files
are 0600. The CLI uses the existing state directory, never the output SMB mount.
Do not run multiple processes against a run. An exclusive lock prevents this.
After a hard kill, an administrator must confirm no process is active before
removing only the stale .stage-lock file. Do not automatically steal locks.

Resume requires identical plan, ledger and UID key. Existing completed objects
are hash-checked and independently validated again. Changes/tampering hold them;
no original or existing staged object is silently overwritten. A crash between
file finalization and checkpoint can leave an unverified orphan held for manual
review. .partial files stay private and are never considered delivered.

Output: P###/DICOM/study-<anonymous UID>/instance-<ordinal>.dcm and P###/reports/.
The anonymous UID names are stable study-folder labels instead of sequential
study numbers. Progress prints anonymous patient label, counts and phase only.
Private checkpoint records validated-staging versus held statuses; release is
always false. Staging must not be copied into the delivery share at this stage.

## Deployment and rollback

Install only the two new modules. No service restart, migration, reindex or remount
is needed. Keep application VERSION at 1.50. First run inventory only. Review the
private plan, evidence and this profile before permitting restricted staging.
Rollback: stop the standalone command and stop using these two modules; no
database rollback or mount change is needed. Preserve the private run, checkpoints
and source crosswalks for audit. Do not delete originals, prior exports or keys.
