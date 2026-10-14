# Metadata-only DICOM pilot, HOAG 1.50

Unknown burned-in annotation status now defers pixel clearance until release;
it does not prevent restricted metadata anonymization after authoritative identity
and profile approval. This is an explicit mode, not an approval migration.
Default staging still requires the original pixel attestations.

## Scope and safeguards

- All 80 patients remain in the plan; reports are optional and never gate DICOM.
- Approve patient/study ownership using independently verified evidence. No name,
  folder, report match or assumed historical MRN change establishes DICOM identity.
- Only `burned_in_or_unknown` can be deferred in metadata-only mode. Existing old
  plans combine YES and unknown: staging rereads the hash-verified source and
  still blocks explicit YES. Preparation counts from old plans are conditional.
- Overlays, different-byte duplicate UID holds, invalid ages, missing identity,
  cross-patient collisions and source problems remain blocked. Unsupported pixel
  decoding or failed independent validation holds that object.
- Preserve original PatientAge; replace PatientName/PatientID with P###; remove
  dates, institution information, private tags and identifying nested metadata;
  consistently remap non-class UIDs under HOAG-1.50-STAGING-AGE-v1. Identity and
  profile evidence must explicitly approve this metadata-only staging mode.
- Pixels remain unchanged. Output records `METADATA ONLY; PIXEL REVIEW PENDING`;
  no `BurnedInAnnotation=NO` is manufactured. Even an original NO does not certify
  that pixels are safe. Every metadata-mode copy awaits independent image review.
- Read-back validates metadata, age, UID mapping, file meta and decoded-pixel
  equality before counting a file as metadata-anonymized. Pixel equality is not
  a search for identifiers or an image privacy clearance.
- Release remains disabled. No delivery command exists; do not copy staging into
  `/mnt/hoag-output`. Originals, mounts, database and existing exports are unchanged.

## Deploy only the two backend modules

Run in Bash on the hospital server. Stop any active standalone batch process
before installing or rolling back these modules. No service restart is needed.

```bash
cd "$HOME/hoag-research-workbench"
git switch codex/research-workbench
git pull --ff-only origin codex/research-workbench

HOAG_CODE_BACKUP="/opt/hoag-research/backend-backup-$(date +%Y%m%d-%H%M%S)"
sudo install -d -m 0700 "$HOAG_CODE_BACKUP"
sudo cp -p /opt/hoag-research/batch_dicom_stage.py /opt/hoag-research/batch_dicom_review.py "$HOAG_CODE_BACKUP/"
sudo install -m 0644 batch_dicom_stage.py batch_dicom_review.py /opt/hoag-research/
```

This uses the existing installed dependencies, engine and independent validator.
There is no reindex, inventory rebuild, database migration or configuration change.
VERSION remains 1.50. Keep the backup path locally for rollback.

## Prepare and review locally

Supply the existing patient-bundle run directory (the one containing
`bundle-plan.private.json`). Keep this shell open so the variables persist.

```bash
read -r -p "Existing private patient-bundle run directory: " REVIEW_RUN
cd /opt/hoag-research
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python batch_dicom_review.py prepare \
  --metadata-only --run "$REVIEW_RUN"
```

Inspect `metadata-dicom-review-summary.private.csv` and
`metadata-dicom-review-packet.private.json` inside that run, only on the hospital
server. Preparation writes private review artifacts but no DICOM. It does not
change the plan, old ledger, report staging or original review packet.

For each verified study, retain two separate nonempty evidence files in a private
run under `/var/lib/hoag-research/batch-reconciliation`, owned by hoag-indexer,
directories 0700/files 0600:

1. Authoritative identity evidence bound to the P### patient, selected group and
   its actual source identities/hashes; explicit resolution of any variations.
2. Approved profile evidence, including PatientAge handling and permission to
   create restricted metadata-only copies while image review remains pending.

These are actual reviewer records, not generated placeholder attestations. The
software binds hashes but cannot establish that a human assertion is true.
No pixel evidence file or all-frames-reviewed flag is required for this mode.

After that review, authorize a selected study:

```bash
read -r -p "Verified study group ID from the private CSV: " REVIEW_GROUP
read -r -p "Authorized reviewer reference: " REVIEWER
read -r -p "Private identity evidence file: " IDENTITY_EVIDENCE
read -r -p "Private metadata-only profile approval file: " PROFILE_EVIDENCE
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python batch_dicom_review.py authorize \
  --metadata-only --run "$REVIEW_RUN" --group "$REVIEW_GROUP" \
  --reviewer "$REVIEWER" --identity-evidence "$IDENTITY_EVIDENCE" \
  --profile-evidence "$PROFILE_EVIDENCE" --identity-verified --profile-reviewed
```

Repeat for independently verified groups. `--patient P###` can replace `--group`
only where the entire eligible patient scope has been independently verified.
Within-study identity variations require individual study review; do not approve
an entire patient to hide them. `--resolve-identity-variations` is permitted only
when authoritative evidence explicitly resolves all observed variants; it cannot
clear other technical holds.

Complete the intended authorization round before the pilot. The metadata ledger
is frozen when staging starts so evidence cannot change underneath resume.
Unverified groups need not be approved and will not block approved groups.

## Pilot, inspect, then resume

```bash
cd /opt/hoag-research
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python batch_dicom_review.py stage \
  --metadata-only --run "$REVIEW_RUN" --pilot-limit 10
```

This caps NEW object attempts at ten, including failed attempts. Independently
validated existing copies are rechecked but do not consume the limit. All cohort
members remain counted. No image-review approvals are synthesized.

Pilot copies and their separate checkpoint live under:

```text
<REVIEW_RUN>/metadata-staging/P###/DICOM/study-<anonymous UID>/instance-<ordinal>.dcm
<REVIEW_RUN>/metadata-staging/P###/reports/       (empty; optional reports unchanged)
<REVIEW_RUN>/metadata-staging/checkpoint.private.json
```

Existing redacted reports remain in the parent bundle's P###/reports folder;
this pilot deliberately does not copy or overwrite them. Final package assembly
and delivery remain disabled. The P### labels match across both staging areas.

Review the pilot locally: confirm expected P### membership, retained age,
metadata validation success and pixel-review-pending status. Investigate any
failed validation before increasing scope. Independent validation runs for every
new file; it is not merely a ten-file sample. After the pilot is satisfactory,
run the same authorized scope without the limit:

```bash
cd /opt/hoag-research
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python batch_dicom_review.py stage \
  --metadata-only --run "$REVIEW_RUN"
```

This resumes all already-authorized eligible objects, not all 80 patients
unconditionally. Do not edit the frozen ledger. For a later authorization round,
prepare a new bundle using the existing runner; preserve old staging and evidence.
There is no automatic merge or release. Plan, evidence or UID-key changes prevent
resume. Source changes, tampered output and uncheckpointed orphan files remain
held; completed files are never overwritten silently.

Terminal progress contains only anonymous labels and aggregate counts:
`dicom_files_metadata_anonymized`, `files_awaiting_pixel_review`,
`files_held_for_identity_or_source`, `files_held`, `hold_counts`,
`technical_reason_counts`, `files_not_processed`, and `release_allowed: false`.
Technical reason counts can overlap. The final summary replaces another broad
inventory report. Counts of successful hospital files are available only after
these hospital-local commands run; synthetic tests are not cohort results.

## Rollback

After confirming the batch process is stopped, restore only the two saved modules:

```bash
read -r -p "Backend backup directory created during deployment: " HOAG_CODE_BACKUP
sudo install -m 0644 "$HOAG_CODE_BACKUP/batch_dicom_stage.py" /opt/hoag-research/
sudo install -m 0644 "$HOAG_CODE_BACKUP/batch_dicom_review.py" /opt/hoag-research/
```

Preserve metadata staging, its ledger/checkpoint and private evidence. Older code
cannot resume metadata-mode staging. Do not pass that ledger to older commands or
move pending copies into another run. No data, mount or database rollback is needed.
