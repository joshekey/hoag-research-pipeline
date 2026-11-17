# Workflow review and stabilization — 2026-11-17

## Scope and evidence

Reviewed the patient/exam selection, independent TXT/SQL report browsing and
association, native image/mask controls, OHIF integration, review revisions,
preparation, approval, and export paths. Existing synthetic tests also cover
matching identity conflicts, source changes, authentication, SQL gates, source
access, and installation behavior. This is a code and synthetic workflow review;
production patient data, production server health, and hospital-scale performance
were not inspected. It is not a claim that every possible defect is eliminated.

## Fixed in this change

- Large report lists pushed the preview below all results: separate scrolling
  results and reader, selected-row highlight, and Previous/Next navigation.
- Masking was disabled before preparation without local guidance, and the
  rectangle appeared only after release: explicit readiness/recovery messages,
  a link to Review, and a live drag preview with zoom-correct source coordinates.
- Old images remained available during subsequent fetches, and a drag could
  finish after image context changed: hide the stale image, cancel superseded
  requests/drags, and gate drawing on successful load and current review state.
- Mask errors incorrectly replaced connection-health results: show the mask
  error without changing source-health information.
- Masks could be approved despite not fitting another instance: validate all
  source headers before approval, retaining export-time checks.
- Work could only be persisted through approval: add audited, revision-checked
  Save draft for masks, sanitized report, and note. Saving keeps state `review`,
  clears approval fields, and cannot overwrite a newer revision or locked review.
- Old report rows were still clickable while a replacement search ran: remove
  stale rows immediately and keep loading completion tied to the active request.
- Large sticky identity and approval areas obscured controls: compact the patient
  and exam banner and keep approval actions in document flow. Collapse preparation
  after a draft exists and move raw mask coordinates into an advanced section.

## Regression coverage

- Real browser: 100 report rows, desktop/mobile layout, next/previous report,
  confirmation reset, patient selection, OHIF native/JPEG pixel rendering.
- Real browser: prepare, draw live at 125% zoom, undo, cancel a gesture when
  navigating, save/reload the draft, approve, export, decode all three output
  DICOM instances, assert masked samples are zero, and verify source hashes.
- Python: out-of-bounds and mixed-dimension approval rejection; multiframe
  masking preserves all unmasked samples; draft persistence, stale revision
  rejection, approved-review locking, invalid masks, and export gating.
- JavaScript: save/reopen, late draft-save response after selecting another exam,
  stale report-row removal, and existing selection/approval race regressions.

## Next product priorities

1. **Per-series/instance/frame masks.** Current masks intentionally cover the
   entire exam. A scoped mask model needs a versioned schema, source identity
   binding, migration of existing approvals, and output-pixel regressions.
2. **Hospital-data acceptance and performance.** Validate representative large
   studies, report formats and missing/unsupported sources on the server; measure
   source I/O, decoding and matching latency before changing concurrency/caches.
3. **Native series ordering and inspection progress.** The native fallback uses
   catalog order and discovers frame counts on demand. Geometry-aware ordering
   and complete frame counts need catalog enrichment and upgrade handling.
4. **Structured report reading and search filters.** Retain original text/hash
   for association while adding source-supported date/type summaries and a
   separately labeled formatted view for XML or irregular report text.

No automatic association, approval or export was added. Existing SQL authorization
gates and historical-study separation remain. Deployment and production acceptance
are separate from this pull request.
