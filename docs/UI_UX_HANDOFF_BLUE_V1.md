# HOAG Research — UI/UX Redesign & Engineering Handoff (Draft v1)

Status: design/implementation in progress, **not** clinical acceptance or release approval. All examples used in tests must be synthetic.

## Product objective
A researcher begins at a DICOM study and can see report search coverage, examine candidate evidence, inspect images and narrative side by side, and record manual decisions with a distinct privacy and export gate. Technical maintenance must not interrupt this review flow.

## Information architecture
Six screens, accessible using the permanent application sidebar, with stable hash links:
1. Dashboard (#overview): operational status, source/inventory summary.
2. DICOM Studies (#studies): filters, sortable study list, open Review.
3. Study Review (#review): selected study context, DICOM viewer, TXT/SQL candidates, evidence, PHI review, and final actions.
4. Report Matching (#matching): matching queues and unresolved cases; never silently attach a report.
5. Exports (#packages): show packages only after separate association, image, and text reviews.
6. Administration (#administration): controlled links to mounting, jobs, historical research and read-only schema inspection.

## Blue visual system
Dark navy base (#07152b), medium-navy panels (#11243d), clinical blue accents (#65aaff), primary action blue (#2775d5), accessible focus blue (#a8dcff). Amber warnings and red errors must remain distinct. Avoid external scripts, fonts, or stylesheets. Follow browser CSP and existing login.

## Selected-study invariant
The active study UID originates in the authenticated Study Catalog review action. On selection change, all pending candidate lists, report text, image series, OHIF iframe, checked confirmations and review-local loading states must reset or remain bound to the previous request generation; stale responses must not overwrite the new study. No candidate search response can change the approval state.

## Report candidate evidence
Show explicit source (TXT/SQL/historical), patient name, exam date, modality, anatomy, conflicts, and source coverage. "No candidates found" must differ from "source not searched", "query failed", "candidate conflict", and "manually rejected". A heuristic score is not a probability. Reused reports and inconsistent patient identities are never automatically accepted.

## Clinical and security boundaries
Authenticated viewer only; HTTPS 8443 via NGINX; source shares mounted read-only. OHIF uses a narrowly scoped CSP exception for its JPEG decoder; the main dashboard must not inherit it. Source text, identifiers, URLs containing StudyInstanceUIDs and original DICOM image paths must not be written to GitHub, logs, screenshots outside authorized systems or external telemetry. Never use live PHI for regression fixtures.

## Approval and export
Manual association is distinct from report redaction/draft preparation, image PHI inspection, reviewer authorization, and eligibility to export. The UI must never imply that viewing a study constitutes a review or approval. No workflow-engine or export changes are included in blue UI v1.

## Current implementation (blue UI v1)
- Six screen links and screen-level client-side navigation.
- Blue styling and keyboard-visible focus.
- Active-study banner and automatic per-study TXT/SQL candidate discovery.
- Historical reviews remain independent, explicitly identifiable and feature-gated.
- No new backend mutation endpoints, schema migrations, or changed clinical permissions.

## Remaining before external team acceptance
- Confirm OHIF renders compressed and uncompressed examples in the target browsers after CSP fix.
- Browser-level E2E navigation and stale-response tests, including back/forward/history.
- Independent validation that one patient's report cannot appear under another selected study.
- Unified study-specific historical search rather than unrelated global historical leads.
- More useful error/status messages and search coverage disclosures.
- Responsive clinical two-pane viewer/report layout tested at large and small resolutions.
- Formal roles/permissions, audit trail, review/approval semantics and documentation.
- Performance budgets for studies with hundreds or thousands of images.
- Clinical privacy, security and accessibility acceptance reviews.

## Synthetic acceptance scenarios
A. Open Study A -> its images and report suggestions load; switch immediately to Study B -> no A content remains.
B. Study with zero TXT and zero SQL candidates -> clearly says no matches found, not approved.
C. SQL lookup timeout -> failure is displayed without affecting DICOM image viewing.
D. A report matches multiple studies -> reject one-to-one auto-assignment.
E. OHIF decoder CSP permits bundled worker while dashboard CSP remains strict.
F. Login absent -> both metadata and pixel endpoints deny access.
G. Historical gate disabled -> historical report previews and image retrieval fail closed.
H. Screen keyboard navigation, back/forward and narrow widths stay usable.
I. Successful report preview never changes the approved/export state.

## Safe deployment
Run regression tests, verify front-end syntax, back up assets, install only static/app.js, static/app.css, templates/index.html, restart only hoag-dashboard, confirm unauthenticated catalog gives HTTP 401, and verify other services. Rollback by restoring those assets from the timestamped backup, then restart hoag-dashboard. Feature gates are unchanged.
