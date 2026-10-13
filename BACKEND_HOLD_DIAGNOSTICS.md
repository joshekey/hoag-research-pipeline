# Staging hold diagnostics (1.50)

Run batch_hold_diagnostics.py --plan <private stage-plan.private.json>.
Add --inspect-headers to reread headers of held paths without pixel decoding.
Add --verify-source-hashes only when a full read of held objects is intended.
Without the hash option, current headers are diagnostic observations, not proof
that bytes match the prior inventory. No holds are cleared in any mode.

The input is the existing staging plan, NOT the readiness or report-selection
manifest. All cohort patients remain included. Exact reasons, overlapping category
totals, mutually exclusive reason combinations and unique affected patients,
studies and paths are reported. Occurrences are separate from unique paths.
Do not sum reason/category totals to calculate unique held instances.

The 69,446 technical holds are not 69,446 unusable images. The original inventory
uses burned_in_or_unknown for YES, missing, empty and non-NO annotation values.
This diagnostic splits those cases. Missing NO does not establish visible PHI;
even an explicit NO does not replace required all-frame review. Overlay presence
requires content review, not an assumption that every overlay contains PHI.

Identity review is required for all objects and is independent of the technical
hold count. Raw identity variations are reported by attribute and scope; these
are observations, not proven patient mismatches or authoritative alias approval.
Metadata age errors require verified correction/profile review. Duplicate issues
require object metadata and pixel findings together: equal pixels alone do not
clear differing metadata. Prior generic ValueError records may have lost the
precise original cause; current access/header checks cannot reconstruct history.
Missing-folder/unreadable inventory exceptions remain separate from the inventoried
path denominator. Zero counts for missing exception fields mean no recorded
scope, not that the exception affected no studies.

Verified existing evidence can support a reviewer: unchanged source hashes,
authoritative identifier history, hash-bound all-frame reviews, and prior detailed
duplicate comparisons. This tool never turns those observations into approval.
Patient identity needs authoritative review; pixel/overlay concerns need image
review; unresolved decoding and changed files need source-level investigation.

Results are written as hold-diagnostics.private.json in a new hospital-local
0700 run directory with 0600 permissions. Detailed paths and findings stay private;
terminal output contains counts and fixed diagnostic categories only. Original
plan, sources, mounts, databases, approvals and staging checkpoints are untouched.
Reports remain optional and do not participate in technical-hold counts.

Install only batch_hold_diagnostics.py alongside existing batch_reconcile.py and
batch_readiness.py. No service restart or migration. Rollback is to stop using
the diagnostic; retain private results for audit. No release/anonymization/staging
operation exists in this module.
