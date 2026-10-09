# HOAG OHIF integration — isolated proof of concept

**State: design/preflight only; not an installed OHIF viewer or production DICOMweb service.**

## Why this is separate

The running HOAG viewer serves authenticated PNG frames via \`/api/image/<file_id>?frame=<n>\`.
OHIF needs an image source with QIDO-RS study/series/instance discovery, WADO-RS
metadata, and WADO-RS original DICOM instances/frames (or an OHIF-specific
data-source extension). Pointing OHIF directly at the PNG endpoint cannot work.

The existing clinical viewer, report association, and sanitized draft stay in
place. Do not expose raw indexed DICOM through a general-purpose web server.

## Proposed pilot sequence

1. Run \`preflight.py\` against the **sixth UID-sorted pilot study**. It opens
   only the existing indexed instances, preserves engine.source_path's mount,
   containment and source-mtime/size validation, and prints aggregate
   compatibility counts without patient identifiers.
2. On a disconnected development machine, obtain an OHIF release bundle whose
   version has been approved by hospital IT and whose dependency licenses and
   artifact hashes have been checked. Do **not** fetch remote JavaScript/CSS
   from a CDN into the hospital clinical workflow.
3. Build a **synthetic-only** DICOMweb compatibility fixture and test OHIF
   series switching, mouse wheel, frame count, orientation, transfer syntax,
   network requests, and browser memory before attaching hospital DICOM.
4. Implement a **same-origin, locally hosted, explicitly study-scoped** DICOMweb
   adapter with QIDO-RS and WADO-RS. Verify it independently maps every
   request to an approved catalog UID and to the exact indexed SOP instance;
   reject arbitrary studies/paths/foreign series/foreign instances.
5. Prefer a backend-validated, short-lived, one-study capability with bounded
   expiration for embedding. Do not assume browser authentication/CSRF cookies
   or an OHIF iframe make an unprotected DICOMweb endpoint safe.
6. Reverse-proxy through the existing hospital TLS origin after validating
   access controls, response caching (\`Cache-Control: no-store\`), MIME and
   byte-range/multipart behavior, and no open CORS. Keep service loopback-only.
7. Integrate OHIF as an **optional viewer** for selected studies. Retain the
   original HOAG viewer as fallback until real-world functionality and reviewed
   image/frame coverage meet acceptance criteria.
8. Build separate, auditable, explicit human pixel review (including multiframe
   objects, overlays, screenshots and burned-in text). OHIF display is not a
   de-identification clearance. Legacy export should remain blocked until
   approved in the existing workflow.

The synthetic fixture is intentionally **not runnable as a web server**. Unit tests use Flask `test_client()` only. The sample OHIF app configuration is illustrative and does not include a version-pinned viewer bundle or registration of built-in mode/extension modules; do not expect the configuration file alone to launch a viewer.

## Security / data handling decisions

- Never mount the source share read/write or copy clinical data into this repository.
- No open DICOMweb/QIDO browsing, STOW-RS upload, C-FIND, or external origin.
- No PHI-bearing URL logs, third-party telemetry, analytics, CDNs or public demo
  servers. Deactivate logs on patient identifiers and tokens.
- OHIF is a diagnostic-quality viewer platform, **not** itself proof that all
  burned-in identifiers, overlays and nested metadata were reviewed.
- Respect existing SELinux enforcing, systemd confinement, read-only source
  mounts, root-restricted SQL index, and explicit export approval.
- Do not reuse the application's static CSP / X-Frame-Options blindly.
  OHIF needs a carefully limited same-origin iframe policy; preserve DENY for
  unrelated endpoints. Confirm with hospital IT before modifying NGINX.
- Verify DICOMweb compliance for transfer syntaxes, enhanced/multiframe SOP
  classes, concatenations, frame numbers, and metadata BulkDataURI behavior.

## OHIF 3.11 example configuration

\`ohif_app_config.js\` is a starting *offline* configuration, not ready to deploy.
It assumes the future restricted same-origin URLs \`/ohif-pilot/dicomweb\`.
Do not enable these routes until their authorization and DICOMweb contract
tests pass. It intentionally disables upload and public study browsing.

References:
- https://docs.ohif.org/3.11/configuration/datasources/dicom-web/
- https://docs.ohif.org/deployment/authorization/
- https://docs.ohif.org/deployment/cors/

## Pilot exit criteria

- Every synthetic study/series/instance and multiframe case loads correctly.
- Study-scoped URL cannot retrieve any study, series, or SOP outside the
  selected catalog set. Unauthenticated/expired requests return 401/403.
- The 157-instance hospital pilot displays correct series boundaries and
  orientation without leaking clinical data outside the approved origin.
- Browser wheel scrolling/zoom behave acceptably, with no inaccessible tools.
- Source data and existing approval/export state remain unchanged.
- Checksum/fingerprint validation and explicit clinical image review are kept
  independently of OHIF's display state.

## Six-series synthetic browser test

The synthetic DICOMweb fixture now generates **157 strictly synthetic images**
across six series (30, 28, 27, 26, 24, 22 images). Each image has a distinct
64×64 grayscale pixel pattern and synthetic identifiers; it never reads HOAG
source shares. It uses the same hard-coded synthetic StudyInstanceUID as the
earlier two-image fixture, so restart the already-running loopback-only
Waitress test server to load the new fixture and hard-refresh the browser.
These data test wheel navigation and series switching, **not** radiology accuracy.
The offline, network-disabled OHIF container remains unchanged.
