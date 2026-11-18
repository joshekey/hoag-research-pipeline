# Source administration and workspace improvements — 1.7.0

## Understand a small patient inventory

Open Administration → Sources & mounts. Connection health and available space are
followed by cards showing the configured remote share, local mountpoint, patient-ID,
study and DICOM-file counts, unreadable/ignored file counts, and the last successful
scan's folder scope. These are indexed counts, not a census of the remote server.
Patients without an ID are not included in the patient-ID count. After an unsuccessful
scan or a connection change, old counts remain visible with a rescan-required label.

Use Browse / change scan folders to inspect the connected source. Select all folders
you want to retain, including report sources: a new scan replaces the active catalog
scope. Subfolders are included. Source errors link to the existing error list; Processing
jobs shows phase/count/error details and supports retrying failed jobs. Folder traversal
failures can stop the scan and appear in Processing jobs rather than as file errors.

## Edit a connection

1. Choose Edit connection on its source card. The current share, username and domain
   are prefilled; the saved password is never returned. Enter the password again.
2. Use Test connection & preview folders. The root broker opens a separate temporary
   read-only SMB connection with fixed options. It leaves the live mount, fstab,
   saved credentials, scan readiness and approvals unchanged. The preview lists up
   to 50 folders among the first 2,000 entries; an empty preview is not proof of an
   empty share. The test does not verify output write access.
3. Confirm and choose Save & reconnect share. Any edit requires a new test in the UI.
   The server still validates all inputs, requires confirmation, and reserves the
   idle worker/catalog during reconnection. Test and save are separate operations;
   a successful test cannot guarantee the subsequent connection will succeed.
4. For source changes, run a successful scan before review/export. A failed reconnect
   restores prior mount settings and attempts to reconnect them, but scan readiness
   and approvals remain invalidated. Check connection health before rescanning.

The four standard broker-managed slots can be edited. IT-managed fstab entries and
custom mountpoints remain protected. Output changes keep existing packages on their
original shares; check output health after saving. Neither test nor edit runs a scan
automatically. Credentials travel only through the protected local Unix socket.

## Workspace

- DICOM Study Description is indexed and displayed in the selected exam header and
  each worklist row. Multiple distinct descriptions are summarized rather than
  silently choosing one. Missing values have an explicit fallback.
- Existing catalogs need a successful scan/reindex to populate descriptions. Reindex
  requires renewed review/approval; installation does not do this automatically.
  The parser version ensures unchanged files are reread on the next scan.
- Filter exams by modality, review status and date without changing the selected
  patient/exam. Review queue still excludes approved/exported exams; use Patient
  search to find those states.
- Series choices show description, image count, displayed-instance count and mask
  count. Displaying images or drawing masks never asserts that images were reviewed.
- The checklist reflects linked report, saved draft/masks and explicit review
  confirmations. Confirmations are reset on reopening an unapproved draft, as before.
- Adjust Image pane width; only that numeric preference is stored in local storage.
  Small screens retain the stacked layout.
- With focus outside form controls, use left/right arrows for images/frames, M to
  toggle rectangle drawing, and Escape to cancel. The active tool and scope are shown.
- Export feedback shows the selected exam's most recent export job from the recent
  job list, or explains why export is blocked. Existing save/revision/approval and
  unsaved-change protections remain in force.

## Deployment and verification

Use the normal `research_build --no-configure` update and check version **1.7.0**,
service health and doctor. The installer includes the new Python/JavaScript modules.
No database schema migration is required. Hard-refresh browser assets after updating.

Tests use synthetic files, temporary fstab/credential files and mocked mount commands;
browser tests exercise the editor, description, filters, pane layout, navigation and
existing scoped-mask/export workflow. Live SMB credentials, server permissions and
production-scale performance require validation on the deployment server.
