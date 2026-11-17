# Single patient/exam review workspace — 1.6.0

Choose an exam in **Review queue** or **Patient search**. The selected patient,
DOB, accession, modality and date stay visible above two panes. Search does not
change the selected exam. Old Images/Matching/Review/Export links resolve into
this same workspace; they no longer move the images between screens.

## Daily workflow

1. Inspect the exam in **Images & pixel masking** on the left. Select a series,
   scroll through instances/frames with Previous/Next, the slider, arrow keys,
   or the mouse wheel when drawing is off. Expand **Jump to instance / frame**
   for direct navigation. **Fit** fits the whole image; zoom adds scrollable detail.
2. On the right, use **Find / change report**. Choose a result from the selector
   or use Previous/Next report. Search/filter and the complete result list with
   ranked suggestions expand in place; hundreds of reports do not push the
   preview below the list. Check the independent report identity and explicitly
   confirm the relationship, then **Use this report for this exam**.
3. **Linked report & privacy draft** stays beside the images. Prepare the privacy
   draft, inspect the original, and edit sanitized text. Preparation is explicit
   and requires a linked report. Re-preparing asks before replacing review work.
4. Once the draft is ready and an image is loaded, enable **Draw pixel mask**.
   Choose the scope for NEW masks, drag, and release. The live overlay uses
   original image coordinates even when zoomed. A scope change or image change
   cancels unfinished drawing. **Undo last mask** and individual **Remove** actions
   make correction explicit. Existing masks do not change when the scope selector
   changes. The list names every mask's target and the count on the current frame.
5. **Save draft** stores text, masks and notes without approval. Saved work survives
   reopening; review confirmations must be made again. Unsaved changes trigger
   a warning before leaving the exam. No patient content is stored in browser storage.
6. **Review & approve** opens the summary below the same workspace. Verify the
   report, mask scope counts, destination, review confirmations and note. Approve
   explicitly, then **Export selected approved study** separately. Source changes,
   stale revisions and incomplete review still block export.

## Mask scopes and compatibility

- **This image / frame only** binds the rectangle to the source Series Instance
  UID, SOP Instance UID and zero-based frame. Other frames and instances remain
  unchanged, including other frames in a multiframe file.
- **Every image and frame in this series** binds the rectangle to the source
  Series Instance UID. It applies to all instances/frames in that series, not
  another series in the same exam. Rectangles must fit every targeted instance.
- **Entire exam (existing mask)** identifies legacy masks. Existing rectangle
  arrays retain their original all-instance/all-frame meaning and remain readable
  and exportable. Editing a legacy mask collection writes a version 2 document
  while explicitly retaining the old masks' `exam` scope. Nothing is silently
  narrowed or migrated merely by opening an exam.

The stored form is `{"version":2,"masks":[...]}`. Entries contain `scope`, `rect`
(`[x,y,width,height]`), and the required source UID/frame fields. Unknown versions,
foreign targets, mismatched SOP/series pairs, nonexistent frames, invalid rectangles
and more than 100 masks are rejected. Targets and geometry are checked on scoped
save and approval; export rechecks source integrity and applies masks before UID
anonymization. Zero replaces the selected stored samples. Other pixels and source
files remain unchanged. A burned-in-annotation instance needs masks on every frame;
covering the right identifier locations still requires human review.

No database migration or reindex is required for this update. Old approved content
hashes remain valid. Once version 2 masks have been saved, older releases cannot
interpret those documents; use the pre-update backup and a deliberate recovery
plan rather than downgrading the application over newer review data.

## Focused image viewer

The primary workspace no longer loads OHIF or depends on OHIF assets, viewer gates,
iframes, Docker downloads or a separate DICOMweb client. It uses authenticated,
no-store, same-origin PNG previews decoded by the server. The selected exam UID
is checked against the returned source instance. Multiframe previews decode the
requested frame; native and supported JPEG/JPEG-LS/JPEG2000 sources use the installed
pydicom codecs. There is no persistent image cache in the browser.

Controls include Auto contrast, the first stored LINEAR DICOM window, CT lung,
CT soft tissue, CT bone, and explicit custom center/width. CT presets reject
non-CT images. Missing/unsupported windows have visible recovery errors; Auto or
Custom can be selected. The viewer supports monochrome image review and pixel
masking, not diagnostic measurements, MPR, color masking, or defacing. Unsupported
formats should use specialist processing. Native instance order is catalog order;
frame counts are discovered when an instance opens. Display tracking is not an
approval or proof that every frame has been reviewed.

Existing gated legacy/historical OHIF routes and assets are preserved for backwards
compatibility. They are not loaded by the primary workspace; installation no longer
downloads OHIF. This change does not widen historical or SQL access permissions.

## Installation and validation

Use the repository installer with `--no-configure` on an existing installation.
It copies the new mask module and all application assets, preserves configuration,
and prints a pre-update backup location. Version should show **1.6.0**. Verify service
status and `manage.py doctor`, then hard-refresh the browser. No production deployment
is implied by this guide or passing tests.

Regression checks cover legacy approval/export compatibility; scoped frame/series
output samples; untouched source bytes; malformed/foreign mask targets; mixed image
sizes; stale revisions; native and JPEG browser rendering; window errors and recovery;
100 report results; responsive layout; draft save/reload; approval and exact export
pixels across four instances in two series. Run `npm test`, `python -m unittest discover
-s tests -v`, and `npm run test:browser` (with Playwright Chromium installed). The full
Python suite targets Linux; some permission/system-module tests do not run on Windows.
