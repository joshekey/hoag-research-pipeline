# P001 study worksheet and first ten-file pilot (1.50)

This workflow generates hospital-local evidence, not automatic approvals. The
worksheet contains private identifiers. Do not paste it into Codex or GitHub.
The generator rechecks all source hashes and headers in the selected group and
locates the original crosswalk manifest by its plan-bound digest. It does not
contact an MPI or prove the crosswalk is authoritative; the authorized reviewer
must verify provenance and any required identifier mappings locally.

Run the following on the hospital server in Bash, with no batch job active.

```bash
cd "$HOME/hoag-research-workbench"
git switch codex/research-workbench && git pull --ff-only origin codex/research-workbench
sudo install -m 0644 batch_identity_worksheet.py batch_dicom_review.py /opt/hoag-research/
read -r -p "Existing private patient-bundle run directory: " REVIEW_RUN
REVIEW_GROUP=Gccbf79ccb5abf39e5c08
cd /opt/hoag-research
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python batch_dicom_review.py worksheet \
  --metadata-only --run "$REVIEW_RUN" --group "$REVIEW_GROUP"
```

Only aggregate counts go to stdout. Detailed evidence and a human-readable
worksheet are created in the chosen run as:
`Gccbf79ccb5abf39e5c08-worksheet.private.json` and
`Gccbf79ccb5abf39e5c08-worksheet.private.txt`.
Existing worksheets are not overwritten. Source discrepancies cause a nonzero
exit status after writing the findings. Resolve these before approval; do not
edit inventory hashes or discard holds.

Open the worksheet in a hospital-local editor:

```bash
sudo -u hoag-indexer vi "$REVIEW_RUN/$REVIEW_GROUP-worksheet.private.txt"
```

The reviewer must establish:

- The P001 crosswalk row belongs to the intended patient, using its actual verified
  provenance. Crosswalk PATIENTID may be an internal key, not the DICOM MRN.
- Every observed PatientID plus IssuerOfPatientID links to that patient through
  authoritative source-system records or independently verified mapping. Compare
  name/DOB as corroboration. Names, folders and report matches alone are not proof.
- Any missing values, identifier changes, mixed identities, held paths and fresh
  source discrepancies have been investigated. Do not authorize unresolved scope.
- The metadata-only profile is approved: consistent P001 identity and UID mapping,
  original PatientAge retained, dates/institution/private/nested identifiers removed,
  originals untouched, independent metadata and pixel-equality validation, restricted
  staging only. Pixel equality does not certify absence of identifying content.

If the crosswalk already contains verified authoritative identifiers covering
all observed tuples, document that provenance and comparison. Otherwise obtain
patient-master/MPI/source-system evidence first. No software command substitutes
for this missing evidence. The observed source record itself is not independent
proof of patient identity.

After actual review, record TWO separate approvals in the hospital editor. These
commands do not prefill approval text. Each record must include the reviewer name
or reference, role, UTC date, explicit decision, P001/group, worksheet plan hash,
and the eligible object scope. Identity approval must cite the actual authority
record and mapping evidence. Profile approval must explicitly permit metadata-only
staging with pixel review pending and identify the reviewed profile/limitations.
Do not use the blank worksheet as either approval.

```bash
IDENTITY_EVIDENCE="$REVIEW_RUN/$REVIEW_GROUP-identity-approved.private.txt"
PROFILE_EVIDENCE="$REVIEW_RUN/$REVIEW_GROUP-profile-approved.private.txt"
sudo -u hoag-indexer sh -c 'umask 077; exec vi "$1"' sh "$IDENTITY_EVIDENCE"
sudo -u hoag-indexer sh -c 'umask 077; exec vi "$1"' sh "$PROFILE_EVIDENCE"
read -r -p "Authorized reviewer reference matching the approval records: " REVIEWER
```

Only after both approvals genuinely exist, authorize this group and immediately
attempt the pilot. The `&&` prevents staging if authorization fails. No variation
override or pixel-safety attestation is supplied. If variant resolution is required,
stop and investigate; this first-group recipe intentionally does not bypass it.

```bash
cd /opt/hoag-research
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python batch_dicom_review.py authorize \
  --metadata-only --run "$REVIEW_RUN" --group "$REVIEW_GROUP" \
  --reviewer "$REVIEWER" --identity-evidence "$IDENTITY_EVIDENCE" \
  --profile-evidence "$PROFILE_EVIDENCE" --identity-verified --profile-reviewed &&
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python batch_dicom_review.py stage \
  --metadata-only --run "$REVIEW_RUN" --pilot-limit 10
```

Given the reported zero prior authorizations, only this group is authorized.
The pilot attempts up to ten new files; source/decode/validation failures may
mean fewer than ten successful copies. It does not automatically retry until ten
succeed. Existing held objects remain held. Copies go to
`<REVIEW_RUN>/metadata-staging/P001/DICOM/`, never the export share.
Every successful copy remains awaiting pixel review; release is disabled.

The first staging checkpoint freezes this authorization ledger. Subsequent
resume uses the same evidence. Additional groups need a separate authorization
round/run under the existing workflow; do not edit the frozen ledger.
Reports are optional and remain unchanged. No GUI, database, mounts, original
DICOM, or existing exports are modified.

Rollback: stop using the worksheet action. The added module creates only private
worksheet artifacts. Preserve them for audit. The existing metadata staging
rollback procedure remains in BACKEND_METADATA_PILOT.md.
