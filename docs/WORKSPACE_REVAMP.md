# Patient and exam workspace

The daily workflow starts in **Review queue** or **Patient search**. Both open
the same exam workspace. Search and pagination do not change the selected exam.
The persistent banner identifies the patient, patient ID, date of birth,
accession, modality, date, and workflow status. Selection is restored within
the same browser tab after refresh; report text and review edits are not stored
in browser storage. Unsaved edits require confirmation before leaving the exam.

1. **Match report**: inspect images and compare report evidence side by side.
   Previewing a report does not associate it. Explicitly confirm the relationship.
2. **Review**: prepare the associated report, inspect the original and sanitized
   text and every image/frame, apply masks, and approve. Reopen an approved review
   before corrections. Existing revision and source-integrity checks remain.
3. **Export**: confirm the selected exam and destination, then export the approved
   package. Queue and patient search remain available. Approved/exported exams
   leave the review queue but remain searchable.

Source setup, indexing, matching rebuilds and jobs are under Administration.
The stylesheet uses Hoag's blue `#005E92` and orange `#F9902D`, with white/paper
surfaces and dark text. Palette reference: https://www.hoag.org/.

## OHIF deployment and verification

`research_build --no-configure` now installs **all** dashboard JavaScript and CSS,
including the report browser, workspace, and new theme. No manual static-file
copy is needed. If OHIF assets are absent, the installer downloads the official
OHIF 3.9.2 static layer by pinned SHA-256 before stopping services. Existing
OHIF assets and the root-controlled study selector are preserved.

`/app-config.js` is always served from the application release, so an older
configuration in the asset folder cannot silently send requests to pilot paths.
SharedArrayBuffer is disabled for this non-isolated iframe deployment, and
request/decoder concurrency is bounded. All DICOM and static routes retain login.
Browser responses remain `no-store`; no persistent DICOM cache is introduced.
Each viewer instance uses an exam-scoped DICOMweb root. Discovery returns only
that exam, and requests for another exam under that root are rejected. OHIF cannot
silently switch to a different patient while the workspace banner stays unchanged.

The viewer displays a reason when unavailable instead of disappearing. Check
the scan, source access, active jobs, identity conflicts, assets and existing
`/etc/hoag-research/ohif-pilot-study.uid` selector. The selector supports a single
UID or the existing `ALL_INDEXED` mode. This release does not widen that selector.
For a complete catalog rollout, the server administrator must configure the
intended scope. Missing assets can also be staged with:

```bash
python scripts/fetch_ohif_assets.py /path/to/new/ohif-assets
```

The adapter resolves the catalog once per metadata request and reads each source
header once. Individual requests still recheck authorization and source state.
Native multi-frame images and JPEG/JPEG-LS/JPEG2000/RLE encapsulated frames are
served without altering source samples. Bit-packed frames and other unsupported
syntaxes still return an explicit error; browser rendering depends on supported
SOP classes and decoder capabilities. The existing 128 MiB per-instance retrieval
limit remains. This is not a general-purpose PACS.

## Regression checks

```bash
npm ci
npm test
python -m unittest discover -s tests -v
npx playwright install chromium
python scripts/fetch_ohif_assets.py .browser-fixtures/ohif-assets
npm run test:browser
```

Use `PYTHON_EXECUTABLE` for a non-default Python runtime. The browser harness binds
only to loopback, creates synthetic DICOM/reports, runs the real Flask routes and
worker, and never connects to hospital sources. Its NLP stub is test-only.
Browser tests verify selection/search/navigation, mobile width, report association
through actual export, and rendered pixels in the official OHIF bundle (native
and real JPEG). Synthetic API tests cover multi-frame retrieval, source mutation,
authorization revocation, queue states, and bounded catalog/header reads.

After deployment, test a representative hospital study in Images, including
switching series and frames, then matching/review/export on an approved test case.
Synthetic tests cannot establish network-share latency or compatibility with
every production acquisition format. Do not rerun indexing just to test the UI.
