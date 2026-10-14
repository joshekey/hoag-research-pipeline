# Prioritized DICOM evidence review queue, version 1.50

Run batch_review_queue.py after group review preparation. The default input is
the latest patient-bundle run; --run can pin that existing private directory.
No source files, mounts, database, authorizations or checkpoints are changed.
It reads the existing inventory and creates a NEW protected diagnostic directory.

Output files:

* eligible_standard_review.private.csv: studies with eligible instances and no
  detected within-study identity variation. Fully eligible studies first, then
  partial studies, with smaller eligible scopes first within each class.
* identity_variation_review.private.csv: all detected variation studies, including
  studies with zero eligible instances. Requires authoritative study-specific
  resolution; do not mix these into routine patient-wide approvals.
* technical_holds_only.private.csv: non-variation studies with no eligible paths.
* review-queue.private.json: private group details, immutable source-plan digest,
  source bundle directory, patient cohort gaps and inventory exceptions.

Group IDs match batch_dicom_review.py. Each row contains P###, eligible/held counts,
partial-study flag, identity-variant count, hold reasons, and three initially
not_reviewed evidence checkpoints. These CSVs are worksheets only. Editing them
does NOT grant approval or modify the authorization ledger.

For each standard-review group:

1. Check authoritative identity evidence against all observed identifiers and
   the crosswalk's P### assignment. No detected within-study variation does not
   prove identity; historical differences across studies still require evidence.
2. Confirm explicit approval of the documented deidentification profile, including
   preservation of original PatientAge. No new age calculation is performed.
3. Obtain documented all-frame image/overlay review for the eligible objects.
   Neither BurnedInAnnotation=NO nor queue priority establishes pixel safety.
4. Keep partial-study held objects excluded and label coverage as incomplete.

Do not authorize based on counts. Use the separate controlled group-review tool
only after real evidence is reviewed. Pin its --run to source_bundle_run from
the private JSON, not the NEW diagnostic directory. All queue authorizations_created
counts are zero regardless of any separate prior approvals; this is a preparation
tool, not an approval-status audit. Full cohort includes patients lacking an
inventoried study. A patient's appearance in multiple queues is possible, so do
not add their unique-patient counts. Study/instance queue partitions are disjoint.

The command prints only aggregate queue counts. Detailed files stay hospital-local
(directories 0700/files 0600). Reports are not required. No staging, anonymization,
export or delivery is performed; release remains disabled. Repeat preparation to
create another snapshot without overwriting review worksheets. Rollback is to
stop using this standalone preparation tool; preserve existing evidence.
