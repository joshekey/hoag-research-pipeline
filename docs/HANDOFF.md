# HOAG Research Pipeline — Codex Engineering Handoff

**Updated:** 2026-10-12  
**Status:** Engineering handoff / active development, **NOT approved as a clinically validated or production-ready research export workflow**.  
**Repository:** `joshekey/hoag-research-pipeline`  

**Integration update — 1.2.0:** The Codex integration consolidates the simpler release's
scanner/update safeguards and the experimental UI modules in `codex/tabbed-blue-workflow`
(PR #2), with a blue left-to-right Sources / Images / Reports / Anonymize & review / Export
workflow. See `docs/TABBED_WORKFLOW.md` for current behavior and migration details. The
branch split described below is historical context for this integration. The user confirmed
the hospital currently shows the simpler GUI; no live version number was independently
verified. SQL review remains on safety hold and SQL reports remain ineligible for export.
New surname/scoped-TXT and DOM race tests pass; full integrated Linux CI passed before
publication. Visual browser checking was stopped by the computer-use tool because it
could not verify the current browser URL. No hospital system or clinical source data was
accessed by Codex during this integration.
**Instruction to Codex:** Read this entire document before changing code, selecting a base branch, running an installer, or touching a hospital system.

## 1. Branches: CRITICAL, do not blindly merge or deploy

Two tracks exist and have **diverged**:

| Track | Ref | Purpose |
| --- | --- | --- |
| Authoritative full application release | `78c08a8` (v1.1.0; default-branch README documents the release) | `manage.py`, `research_build`, scanner/inventory correctness, service installer, upgrade/backup/doctor |
| Research / experimental clinical features | `feature/report-metadata-candidates-20261008` | OHIF DICOMweb, SQL report broker, historical EMR study matching, blue redesigned UI, help, reconciliation, candidate matching safeguards and synthetic tests |

A GitHub comparison of `78c08a8` against the research branch returned **diverged**: the research branch was 251 commits ahead and 2 behind at the time of inspection. The default release has changes the research branch does not necessarily include.

**Do not run `research_build` over the research pilot or install the research branch over v1.1.0 without a deliberate reviewed integration plan.** Both installations use `/opt/hoag-research` and `hoag-dashboard`. Confirm actual installed version and source layout first. Existing credentials, secret keys, files, backups and study mappings are critical.

### Reference: user's v1.1.0 checkout / installation commands

These are **reference commands only**, not approval for Codex to execute a privileged deployment:

```bash
cd ~
git clone https://github.com/joshekey/hoag-research-pipeline.git hoag-research-pipeline-v1.1.0
cd hoag-research-pipeline-v1.1.0
git checkout 78c08a8

if sudo test -f /etc/hoag-research/config.json; then
  sudo bash research_build --no-configure
else
  sudo bash research_build
fi
```

Release diagnostics:

```bash
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python /opt/hoag-research/manage.py version
sudo systemctl --no-pager --full status hoag-dashboard hoag-worker hoag-mount nginx
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python /opt/hoag-research/manage.py doctor
```

Research branch additionally uses `hoag-sql`. **Do not assume the v1.1.0 checkout above was actually installed**: verify the live installation rather than inferring from a command saved in chat.

## 2. Product objective

An authorized reviewer must be able to:

1. Find a DICOM examination in **DICOM Studies** and select **Review**.
2. Inspect the **same study's** original images and candidate TXT/SQL reports in one workspace.
3. Understand precisely which sources have been searched: no candidates vs not searched vs source unavailable vs identity conflict.
4. See explainable evidence: verified DICOM source patient identity, report patient identifiers, exam/service date, modality, anatomy, and explicit conflicts. A heuristic score is NOT a clinical probability.
5. Manually validate the report/examination association, inspect every necessary image/frame for burned-in identifiers, edit sanitized narrative and masks where necessary.
6. Approve and export only through independent audited steps; viewing images or finding a candidate never approves/export-enables a study.
7. Use in-app **Help** without developer assistance.

Six main screens in experimental blue UI: Dashboard, DICOM Studies, Study Review, Report Matching, Exports, Administration; Help & user guide. Primary blue tokens: `#07152b` background, `#11243d` panel, `#65aaff` accent, `#2775d5` action, `#a8dcff` focus. See feature-branch `docs/UI_UX_HANDOFF_BLUE_V1.md`.

## 3. BLOCKER: wrong-patient SQL candidate shown

A reviewer selected a DICOM study and reported that **SQL candidate review displayed a different patient's report**. Treat this as a privacy and data-integrity safety incident until independently resolved. The exact root cause and actual hospital-source association have **not** been validated.

**Fail-closed protections built on the research branch:**

- `webapp.py` requires a root-owned explicit `/etc/hoag-research/sql-candidate-review.enabled` authorization gate for SQL candidate list/preview/association/draft endpoints. Missing gate => HTTP 503. **Keep SQL candidate review DISABLED. Do not create this gate or bypass it to test the UI.**
- `templates/index.html` / `static/app.js`: SQL patient search field populated from the selected DICOM study. `webapp.py` compares the entered name with independently validated DICOM patient name, and disallows cross-patient search.
- `sql_broker.py`: report candidate HMAC tokens scoped to study; checks header name, all independently parsed patient-name lines (`txt_candidate_discovery.all_patient_names`), and conflicting DOB; also validates report token on preview. Missing evidence must remain visibly unknown, not treated as matching.
- `sql_gui_candidates.py` and `broker_study_auth.py`: verify source/catalog study identity and bounded broker requests.
- `tests/test_sql_broker.py`, `tests/test_sql_review_safety_gate.py` and related synthetic tests.
- A private, read-only aggregate audit tool `ohif_poc/sql_identity_audit.py` was recently added on the research branch with synthetic `tests/test_sql_identity_audit.py`. **No completed hospital-data audit result was supplied in this conversation.** Review the tool's authorization, permissions, bounded resource use and correctness before running it; do not treat it as independently validated.

The user requested a patient search box; it exists in development, but **manually entering a name must NEVER override DICOM-verified patient identity**. Confirmation of identity requires examination metadata and source header verification, not merely same patient name.

**Release criteria for re-enabling SQL candidate previews**: reproducible synthetic cross-patient tests, reviewed SQL header parser for the hospital's actual formats, read-only in-hospital audit with aggregate-only output, validation of both primary and secondary report identity, DICOM header/catalog agreement, correct accession/exam date/anatomy where available, no token cross-study leakage, and explicit authorized reviewer acceptance. Keep SQL association and export blocked separately.

## 4. Key technical components on the research branch

| Component | Files / service |
| --- | --- |
| Authenticated dashboard/UI | `webapp.py`, `templates/index.html`, `static/app.js`, `static/app.css`, `hoag-dashboard` |
| Inventory, state, de-identification, export | `engine.py`, `store.py`, worker service; inspect current installer/runtime paths before assuming versions |
| SQL fixed private index and broker | `sql_broker.py`, `broker_study_auth.py`, `sql_gui_candidates.py`, `candidate_scoring.py`, `sql_association.py`, `sql_prepare_review.py`, `hoag-sql.service` |
| Historical reports | `historical_emr_review.py`, `historical_emr_overlap.py`, `historical_emr_independent_identity_audit.py`, `historical_emr_source_validation.py`, `txt_candidate_discovery.py` |
| OHIF / DICOMweb | `ohif_poc/clinical_dicomweb.py`, `ohif_poc/historical_retrieval.py`, `ohif_poc/clinical_retrieval_core.py`, local OHIF 3.9.2 assets |
| UI specification | `docs/UI_UX_HANDOFF_BLUE_V1.md` |
| Audit proof-of-concept | `ohif_poc/sql_identity_audit.py`; do NOT assume the audit has passed |

Target research host: hospital RHEL 10.x, approximately 4 CPUs, 12 GB RAM, 500 GB local disk, external readonly SMB sources and a separate writable output share. Application installed under `/opt/hoag-research`; config and root-owned gates under `/etc/hoag-research`; application HTTPS via NGINX `:8443`, Flask on loopback `:8080`. SELinux enforcing and authorized hospital network. **Do not put host-specific patient data, SMB credentials, key material, full file paths to patient sources, real patient UIDs, screenshots, or report narratives in GitHub.**

### Matching and workflow distinctions

- **Catalog `unmatched` is TXT-based** and can be shown misleadingly as “No report match”. Blue UI changed wording to “No indexed TXT match · SQL not checked”. SQL candidate discovery is independent.
- Active TXT preparation (`engine.prepare`) requires a valid active indexed TXT report; it does not work on an SQL/historical candidate. The research UI moved this into collapsed **Legacy TXT report preparation**. Missing active TXT records or a `conflict` state may trigger `Study or report unavailable / conflicting study identity`.
- `Reconcile report sources` in experimental UI reloads study-specific TXT candidates and attempts a separate SQL broker search; it does **not** modify matching tables, re-index, approve or export. During the SQL safety hold, it should report the hold, NOT claim zero SQL reports.
- Historical EMR candidate review is independent from active study selection, protected by `/etc/hoag-research/historical-emr-review.enabled`, and is NOT an integrated active-study report association mechanism.
- Earlier historical review identified 2 unique candidate study/report pairs and 348 historical DICOM images, with compatibility preflight 348/348 (including JPEG Lossless Process 14). This validates syntaxes/header consistency for that pilot, NOT patient acceptance or export permission.
- Earlier active catalog contained 8 studies / 390 images, including a 157-image study with an SQL report lead. Counts are historical observations and **must not be assumed current**.

### OHIF viewer

The research viewer uses authenticated DICOMweb and original unmodified DICOM sources. Supported pilot syntaxes include uncompressed, JPEG Lossless Process 14/SV1 and JPEG2000 Lossless. A browser error “Something went wrongfalse” was traced to OHIF libjpeg-turbo WebAssembly worker `new Function` blocked by Content Security Policy. Research `webapp.py` now has a narrowly scoped OHIF CSP exception plus fixed bootstrap hashes; **do not apply `unsafe-eval` to dashboard HTML**. The CSP change was deployed with synthetic tests, but a successful post-fix real-browser rendering validation is still required. Never log pixel payloads or identifiers.

## 5. Last known testing and deployment facts (research feature branch)

- The user reported the full synthetic suite passed **209 tests** (2026-10-12), with one nonfatal OHIF index file ResourceWarning.
- The SQL identity-hold/help deployment subsequently reported full test pass, backup created, HTTP 401 for unauthenticated catalog, and all five services active (`hoag-dashboard`, `hoag-worker`, `hoag-mount`, `hoag-sql`, `nginx`). A transient curl connection failure during service restart preceded HTTP 401; not necessarily a persistent outage.
- The blue six-screen UI, Help guide, patient-name search, TXT/SQL reconcile and SQL hold were installed in the research runtime; separate later UI-only changes collapsed legacy TXT controls. **Recheck exact installed version before assuming that last UI-only change was deployed.**
- A newer read-only identity audit script and tests were added after the last reported test run. Their end-to-end production results are **not known**.
- v1.1.0's separate `research_build` release may have different runtime layout/services; no evidence in this conversation confirms it was deployed. This is why branch integration requires care.

## 6. Highest-priority Codex backlog

**P0 — do not proceed to clinical release without these:**
1. Reproduce and isolate wrong-patient SQL preview using **synthetic fixtures**, audit parser and broker, and fix identity verification on both list and preview. Keep SQL gate off.
2. Verify system version/install layout and choose integration strategy between release v1.1.0 and experimental research branch. Never accidentally regress scanner correctness, upgrade backup, SQL safety hold, CSP, or export authorization.
3. Build a reviewed in-hospital read-only identity audit with aggregate output (optionally anonymous ordinals only). Confirm 157-image example without placing PHI into prompts/logs. Validate the original `ohif_poc/sql_identity_audit.py` first.
4. Confirm OHIF DICOMweb image retrieval and JPEG browser rendering across modalities, study switching and independent active/historical scope.
5. Harden stale-response controls: switching studies must clear all pending SQL/TXT report text and OHIF images; async response for Study A cannot display under Study B.

**P1 — UX and matching:**
6. Single study Review screen with two-pane OHIF + candidate evidence/report preview, explicit source coverage, clear candidate statuses, missing/conflict reasoning and manually verified association.
7. Background **reconciliation job** separate from ingestion: on completed scan/new report index, queue eligible studies for bounded SQL/TXT matching without blocking full DICOM indexing. Do not automatically approve.
8. Better error messages and Help; distinguish missing TXT, SQL not searched, SQL service unavailable, SQL safety hold, conflicting identity.
9. Accessibility, mobile/responsive layouts, browser E2E and keyboard navigation; safe role/permission model for a team.

**P2 — engineering handoff quality:**
10. CI tests, isolated synthetic fixtures, code owners, deployment/rollback runbook, backup restoration drills, versioned release notes, API contracts, performance budgets and formal acceptance checklists.

## 7. Safe development and tests

Research checkout:
```bash
cd ~/hoag-research-v2-dev
git status --short
git branch --show-current
git pull --ff-only
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q
```
Do not assume the research checkout exists on another developer workstation. Use Python virtualenv matching the chosen branch's documented requirements. Never copy a production DB, DICOM, original clinical text or host credentials to Codex. Tests should use synthetic source names and synthetic UIDs.

Relevant research staging helpers are under `ohif_poc/stage_*.sh`. Each one is **privileged and changes production**; inspect full contents, backup/recovery and current system state before proposing it. Do not run installers or change gates as part of ordinary coding/test work.

**Recovery:** research UI backups were created under `/var/lib/hoag-research/backups/`; v1.1.0 release backups may instead be under `/var/backups/hoag-research/`. Confirm which installer produced each backup. Never blindly extract an archive over a newer runtime. The secret key controls research pseudonyms and mappings; protect it.

## 8. Definition of Done for engineering handoff

- Receiving team can explain the codebase and safely reproduce a build **without PHI**.
- Branches consolidated or maintained with explicit version boundaries and upgrade/rollback tests.
- SQL wrong-patient incident independently resolved and audited; gate enabled only by authorized human decision.
- Every displayed report belongs to the selected study or is clearly an unverified suggestion with explainable source evidence.
- Scans, matching/reconciliation, preview, manual association, PHI review, approval and export are separate, tested stages.
- Browser OHIF renders supported images reliably, with authenticated scoped DICOMweb.
- Help, navigation, error states, performance and accessibility pass documented user acceptance tests.
- Full clinical privacy/security review and research protocol approval occur **separately** from passing automated tests.

## 9. Suggested first Codex prompt

> Read `docs/HANDOFF.md` and the branch-specific README/UI handoff. Do not deploy, change authorization gates, process real PHI or run `research_build`. First compare release v1.1.0 (`78c08a8`) with `feature/report-metadata-candidates-20261008`. Propose a branch-safe integration plan, a synthetic reproduction plan for the SQL wrong-patient bug, and the highest-priority changes needed to make Study Review safe and intuitive. Implement only after tests and constraints are explicit. Provide complete files/scripts for any code changes; no partial copy-paste snippets.

## 10. Operating rules for Codex

- Work in the GitHub repository and the chosen branch; don't claim production status based on a commit.
- Never introduce outbound PHI transfer, patient-identifiable logs, external telemetry, cloud image APIs or real patient test fixtures.
- Keep source SMB mounts read-only, SELinux enforced, existing authentication and server-side study scope.
- Preserve root-controlled safety gates and distinguish `401` unauthorized, `404` unavailable, and `503` safety hold.
- No automatic report approval, clinical inference, or research export on a candidate-match score alone.
- Respect user preference: deliver complete updated scripts, not snippets.
- Ask for explicit authorization before any production installer, service restart, gate enablement, data mutation, or deployment.
