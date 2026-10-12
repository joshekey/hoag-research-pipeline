> Updated in 1.3.0: see [Report browser and reindexing](REPORT_BROWSER.md) for the current unrestricted TXT/SQL workflow. The patient-filtered candidate behavior below describes release 1.2.0.

# Tabbed blue workflow — 1.2.0

This integration starts from the standalone 1.1.0 release and preserves its scanner,
backup, service-account/runtime and update checks. It incorporates the experimental
branch's SQL safety hold, authenticated optional OHIF components, historical review
tools and Help. Historical tools remain separate and feature-gated. No gate is created
or enabled by the installer. No hospital processing or deployment was performed during
development. The user confirmed the live GUI currently uses the simpler release.

## Workflow

1. **Sources:** Connect read-only DICOM/report shares and select folders to index.
2. **Images:** Filter studies and select **View images**. Browse series, instances and
   frames. The selected study persists across the following steps.
3. **Reports:** TXT candidates load using the DICOM family name, exact accession and
   study UID. Sort, search, preview and select one report using its checkbox. Header
   patient/exam conflicts block narrative preview and association. Missing evidence is
   unknown; reviewers must independently verify it and record an association note.
   Selecting a candidate never approves, prepares or exports the study.
4. **Anonymize & review:** Prepare the associated TXT redaction draft, inspect every
   image/frame, add masks where appropriate, edit the sanitized report, and explicitly
   approve. Draft preparation completes asynchronously and reloads the current study.
5. **Export:** Confirm the selected approved study and current output mount. Use
   **Choose or connect an output share** for a different Windows share. Only the output
   mount is changed; sources and approval are retained. Source changes still invalidate
   readiness and approvals. Exported packages include DICOM/, Report/ and manifests.

The original data is displayed only through the authenticated hospital application.
The private SQLite surname search index contains report text. It is covered by the
same restricted state directory and consistent pre-update backups as the catalog.
FTS phrase matching is a discovery heuristic, never confirmation that a report belongs
to a study. File changes, source identity mismatches, explicit identity/date conflicts,
and active jobs block the relevant association/preparation operations.

## SQL limitation and reported identity incident

The existing SQL matching safety hold remains in force. It must appear as **on safety
hold**, not **no matches**. SQL list, preview, association and draft endpoints fail closed
without the root-controlled authorization gate. The installer refuses a gate-enabled
deployment; it never changes gate permissions or contents. SQL drafts/associations do
not enter the TXT export engine. The reported wrong-patient incident has not been
independently resolved on hospital data, and SQL clinical previews/exports are not part
of this release's authorized workflow. See HANDOFF.md for the audit prerequisites.

Optional OHIF assets and existing pilot selectors are retained, not installed/enabled.
The built-in series/frame viewer is available without OHIF. Historical reviews are kept
in Administration and do not alter active-study selection or authorize exports.

## Update from the simpler release

Wait for indexing and exports to finish and leave the GUI idle. Use the tested commit
provided with the release in a fresh checkout. If /etc/hoag-research/config.json exists,
run research_build --no-configure. The installer prints a restricted backup location,
retains the secret key, credentials, catalog and completed export packages, and installs
all Python modules needed by the integrated UI. The SQL broker is installed but is only
restarted if it was already active; no private SQL index is built. Runtime installation
is checked as hoag-indexer. Neither OHIF nor historical/SQL authorization gates change.

Confirm manage.py version prints 1.2.0, check the four base services, and run doctor.
Refresh with Ctrl+Shift+R. Rescan the chosen DICOM, TXT and SQL folders once to build
the new local TXT surname index. The GUI reports a required rescan if that index is
not current. Real RHEL mounting, browser rendering and clinical acceptance must still
be checked by the hospital operator; synthetic tests do not establish these outcomes.

## Validation

New tests exercise surname search, paging/sorting, conflicting patient and exam blocking,
changed-source rejection, separate association/preparation/approval/export, and the SQL
safety hold. The integrated legacy suite covers broker tokens, authenticated DICOMweb,
source containment, clinical gates, image stack navigation and reviewed exports.
Windows cannot run all POSIX permission/service tests; Linux CI is the authoritative
platform check. Automated visual browser validation was unavailable in this session.
