# DICOM group review and controlled authorization (1.50)

This tool turns the existing patient-bundle DICOM inventory into study-level
review packets. It does not infer identity from names, ages, folders or reports.
No anonymization runs during preparation or authorization. All 80 cohort members
remain included; missing patients and reports do not block other verified objects.

## Prepare

```bash
cd "$HOME/hoag-research-workbench"
git switch codex/research-workbench
git pull --ff-only origin codex/research-workbench
sudo install -m 0644 batch_dicom_review.py /opt/hoag-research/
cd /opt/hoag-research
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python batch_dicom_review.py prepare
```

Default target is the latest private patient-bundle run. Use --run with its exact
private directory to pin all subsequent steps. Preparation creates only:

* dicom-review-summary.private.csv: one row per study, anonymous P### label,
  eligible/held counts, number of identity variants, review priority.
* dicom-review-packet.private.json: original metadata variants, hashes, paths,
  group membership and blocking reasons. PHI: keep hospital-local.
* group-authorizations.private.json: initially empty and release disabled.

No source reads or database writes are required to prepare these artifacts.
They are derived from the existing hash-bound plan. Staging later rereads and
verifies sources. Existing reports, exports and review templates are untouched.

## What the reviewer must establish

For each selected study/patient, retain three separate protected evidence files:

1. Identity: authoritative patient-master records/verified identifier mappings,
   with the P### label, group IDs, all observed variants, and a reasoned statement
   that the eligible objects belong to this patient. Within-study mixed identities
   require study-specific investigation. A matching filename/name is insufficient.
2. Profile: approval of HOAG-1.50-STAGING-AGE-v1, including original PatientAge
   handling and limitations documented in BACKEND_DICOM_STAGING.md.
3. Pixels: documented review of all frames of the eligible objects (bound to
   group IDs/source hashes), including burned-in text and identifying visual
   features, affirming no pixel identifiers requiring redaction remain.

Store evidence under the private batch-reconciliation root in a protected run
directory, owned by hoag-indexer, directories 0700/files 0600. Do not substitute
empty template files or a generic blanket assertion for actual review evidence.
The tool records reviewer attestations and hashes evidence files. It does NOT
authenticate a clinician or independently establish that the evidence is true;
the hospital's authorized local review process supplies that trust.

## Authorize in groups, after the above review

```bash
cd /opt/hoag-research
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python batch_dicom_review.py authorize \
  --run "$REVIEW_RUN" --patient P001 \
  --reviewer "$REVIEWER" \
  --identity-evidence "$IDENTITY_EVIDENCE" \
  --profile-evidence "$PROFILE_EVIDENCE" \
  --pixels-evidence "$PIXEL_EVIDENCE" \
  --identity-verified --profile-reviewed --all-frames-reviewed-no-identifiers
```

Set variables locally to the actual run/reviewer/evidence paths, not placeholders.
To approve one study instead, replace --patient P001 with --group G... from the
private CSV. This expands approvals to the eligible source hashes, avoiding
thousands of manual JSON edits. Blocked objects remain blocked even within an
approved group. Different-byte duplicate, overlay, missing burned-in flag, missing
identity, cross-patient identifier/UID/hash ownership and source-verification holds
cannot be overridden by this command.

Within-study identity variations prevent patient-wide approval. Review that
individual study with --group and add --resolve-identity-variations only AFTER
the authoritative evidence explicitly resolves every observed variation. Patient
groups with different identities across studies also require that explicit flag.
This does not guess aliases, clear technical holds or repair source metadata.
Unresolved groups should receive no approval. The CSV is informational; editing
it cannot grant approval or change group membership.

## Stage and resume only after completing the authorization round

```bash
cd /opt/hoag-research
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python batch_dicom_review.py stage --run "$REVIEW_RUN"
```

Evidence hashes are rechecked, then existing DICOM staging verifies source hashes,
anonymizes only authorized eligible objects, preserves original age, and performs
independent metadata/UID/pixel read-back checks. Output goes into that SAME private
bundle's P###/DICOM folders, beside the existing redacted reports. Report files are
not changed. No output goes to /mnt/hoag-output. Release stays disabled.

Repeat the identical stage command to resume. Complete the intended authorization
round BEFORE staging: the ledger is frozen once checkpoint.private.json exists.
Changing the ledger would invalidate the existing resumable checkpoint binding.
For a later approval round, prepare a new bundle run using the existing runner;
do not delete checkpoints or manually change hashes to bypass this safeguard.
No automatic promotion/delivery or cross-run merge is implemented.

Concurrency uses the bundle and DICOM stage locks. After a crash, confirm no
process is active before an administrator removes a stale lock; never steal it
automatically. All outputs are private with restrictive permissions. Terminal
output contains aggregate counts or existing anonymous-label staging progress.
Patient-level evidence must never be pasted into Codex or GitHub.

Rollback: stop using the standalone review command. Preserve private evidence,
authorizations and checkpoints for audit. No mount, database, source or export
rollback is required. Existing release safeguards are unchanged.
