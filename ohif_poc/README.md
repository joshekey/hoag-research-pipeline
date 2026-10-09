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

## Future access capability (offline only)

`study_capability.py` defines a 1–300-second, HMAC-signed, study-and-user-bound
capability for a future DICOMweb service; seven synthetic unit tests cover
scoping, expiration, tampering and input validation. **No HTTP route uses this
yet.** A token does not replace hospital SSO, session authentication, per-study
authorization, header security, cache controls, or the catalog resolver. The
future gateway must obtain the authenticated identity independently rather
than from URL/query/body parameters and must not place tokens in URLs, logs,
referers or browser history. This module does not provide revocation or replay
prevention; key management, session revocation, and a fail-closed gateway still
need design approval before any clinical HTTP access.

## Synthetic HTTP authorization test harness (not for clinical use)

`synthetic_guarded_gateway.py` wraps ONLY the synthetic QIDO/WADO test
fixture with study-scoped HMAC capability checks and an independently supplied,
server-trusted test principal. No token issuance route, hospital filesystem
access, network listener, or production installation instructions are provided.
It does not protect the separate running synthetic browser server until that
server is explicitly replaced; do not use the test harness for actual records.
Header-based tokens require a future secure session integration into OHIF,
short-lived issuance, revocation, browser security tests and an approved data
gateway before any clinical use. Unauthorized and expired-request tests are
in `tests/test_ohif_guarded_gateway.py`.

## Synthetic browser-session boundary (offline only)

`synthetic_browser_session.py` demonstrates same-origin Flask session cookies
(`Secure`, `HttpOnly`, `SameSite=Strict`) checked on EVERY synthetic QIDO/WADO
request. No public login/issuer endpoint, clinical file loader, or network
listener exists. Tests populate a synthetic verified identity through Flask's
test-client session APIs. **Never expose that fixture as an identity provider.**
The currently running OHIF localhost pilot is still unguarded synthetic data
only; no production clinical authentication or token renewals are deployed.

## HOAG same-login clinical OHIF pilot (pending clinical validation)

The development branch now contains:
- `clinical_retrieval_core.py` for restricted metadata/original-byte retrieval,
- `clinical_dicomweb.py` for QIDO/WADO pilot routes (one server-side allowlisted study),
- `clinical_app_config_392.js` for the proven OHIF 3.9.2 local bundle,
- `webapp.py` / dashboard button for an embedded same-origin OHIF iframe,
- `install_clinical_pilot.sh` and `rollback_clinical_pilot.sh`.

The gateway is disabled unless `/etc/hoag-research/ohif-pilot-study.uid`
exists and names the independently confirmed 157-instance pilot. All OHIF
routes inherit HOAG's existing Basic authentication; there is NO additional
viewer login prompt. The clinical config intentionally has no third-party
DICOM servers. This is clinical PHI display, not de-identification.

**Not production-approved solely because tests pass.** Hospital IT must
authorize clinical data display via this authenticated web channel, the trusted
TLS certificate, and review the code/CSP, DICOMweb conformance, peer access,
browser behavior and original image metadata before enabling. The first
gateway only permits native little-endian single-frame images; the
`preflight.py` aggregate now shows `compressed_or_other_syntax_objects`.
If nonzero, do not enable pilot until transfer syntax support is implemented
and tested. The client-side JS viewer has no access to source file paths.

The install script requires the explicit
`HOAG_APPROVED_CLINICAL_VIEW=YES` acknowledgment. It checks for one
confirmed associated study, creates a root-controlled allowlist, copies only
prebuilt OHIF assets into the app, and restarts the dashboard only. Keep the
synthetic localhost:3001 pilot independent. Restore via the rollback script
with the generated archive path. No SQL or DICOM research export behavior is
changed; final pixel de-identification review remains necessary.

## Expand from one study to all complete indexed studies

`enable_all_indexed.sh` is a controlled and reversible update for the **already-installed** OHIF pilot. It reuses HOAG Basic authentication, enumerates only fully indexed active studies, enforces the indexed source/series/SOP relationship on every request, and keeps source shares read-only. It requires explicit hospital approval for extending identifiable image access from one pilot to the full catalog. Before changing anything it runs all synthetic tests and a read-only transfer syntax/multiframe check of every eligible study; unsupported studies currently stop the rollout rather than silently misrendering images. It backs up the current webapp, gateway and one-study selector, installs the two updated files, sets the root-controlled selector to `ALL_INDEXED`, and restarts only the dashboard. The viewer should then appear on every eligible study without another login. It does not authorize de-identification or export.
