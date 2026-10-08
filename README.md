# HOAG Research Pipeline

Internal RHEL 10 web application for indexing SMB shares, correlating TXT reports, reviewing de-identification, and exporting research packages. Designed for one worker on a 4 CPU / 12 GB server. No patient data is sent to GitHub or an external AI service.

## Install or update

```bash
sudo dnf install -y git
git clone https://github.com/joshekey/hoag-research-pipeline.git
cd hoag-research-pipeline
sudo bash research_build
```

For subsequent updates:

```bash
cd hoag-research-pipeline
git pull --ff-only
sudo bash research_build --no-configure
```

The installer installs Python dependencies and the English NLP model, a dedicated service account, web and worker services, NGINX HTTPS on **8443**, and SMB client tools. It enables NGINX's SELinux network-proxy boolean and, if firewalld is running, opens TCP 8443 in its default zone. SELinux remains enabled. Restrict the firewall to the intended hospital network using your normal IT controls. Package repositories, PyPI, and GitHub release downloads must be reachable during installation. Processing uses the locally installed model.

The interactive console wizard asks for two source UNC paths and a **separate writable output share**, usernames, domains, and hidden passwords. Sources are mounted read-only using encrypted SMB 3.1.1. Paths with spaces are supported. Credentials remain root-only under `/etc/hoag-research`; the wizard backs up `/etc/fstab` and manages only its own mount entries. Leave a UNC blank to retain an IT-managed mount at the displayed mountpoint. Credentials, server names, and clinical data are not included in this public repository.

A writable output share is required: a 500 GB local disk cannot hold a 2â€“3 TB full export. Output must not overlap source roots. Keep local state on hospital-managed storage; it contains PHI and identity mappings.

## Open the application

Use the server FQDN printed by the installer: `https://YOUR_SERVER:8443`.

```bash
sudo cat /etc/hoag-research/initial-admin-password
```

Username is `admin`. An initial random password is generated once. The installer creates a bootstrap self-signed TLS certificate. Have IT replace `/etc/hoag-research/tls.crt` and `tls.key` with a trusted certificate/key, then rerun `sudo bash research_build --no-configure` to install them into the SELinux-labeled `/etc/pki/tls` paths used by NGINX. Use a hospital-trusted certificate before entering credentials; do not bypass browser certificate warnings. Existing certificates and configuration are preserved on updates.

Change the administrator password locally:

```bash
sudo /opt/hoag-research/configure --reset-password
```

This version uses one administrator account with HTTP Basic authentication over HTTPS, session-bound CSRF checks, and local audit events for queued jobs, approvals and exports. It is not an enterprise SSO or multi-role deployment. Only NGINX exposes the application; the Python server listens on loopback.

## Workflow

1. Click **Index both shares**. Mixed shares are supported: discover standard DICOM files (including extensionless files), `.txt` reports, and `.sql` dumps. Unknown files are ignored. Errors and jobs are visible. Scans checkpoint file by file and skip unchanged successful files. Missing files are marked inactive after a successful traversal. A scan invalidates all prior approvals, including if it is interrupted.
2. Search the study catalog. Report candidates come from exact Study UID labels, accession labels, or accession-matching filenames. A candidate is not an accepted relationship. Ambiguous and missing matches remain for manual review. SQL dumps are identified heuristically, never executed or automatically imported; a source-specific SQL connector requires inspecting its schema.
3. Open **Review**. Choose a candidate or search reports by filename; click **Prepare sanitized report**. The worker applies local Presidio NLP, identifier replacements, and explicit-field/date rules. It also fingerprints every image in that study. **Prepare single-candidate reports** batches this preparation without approving any matches.
4. When the job completes, click **Reload prepared report**. Compare the original and sanitized reports and edit any remaining identifiers. Inspect **every image and frame**, not only the first preview. Record pixel masks where appropriate and a review note. Previews are for de-identification review, not diagnosis.
5. Confirm report association, sanitized report, and image review; approve the study. Approval covers the reviewed report, mask configuration, catalog fingerprint, and source content hashes.
6. Export one approved study or click **Export all approved studies**. The worker checks source content, applies the DICOM profile, validates generated identifiers, and writes the package to the output share. Batch failures retain successful packages; the failed job and affected study show error details.

```text
SUBJECT_<research-id>/
  STUDY_<research-id>_<package-id>/
    DICOM/
      <remapped-instance-uid>.dcm
    Report/
      report.txt
    manifest.json
    manifest.csv
```

Manifests contain relative paths, byte counts, and SHA-256 checksums. Original filenames, patient IDs, accession numbers, and source paths do not enter the manifests. Research patient IDs and study/series/instance/reference UIDs use persistent keyed mappings; protect and back up `secret_key`. Changing it changes identities. A protected local catalog retains source associations and export paths.

## De-identification scope

The DICOM engine uses Kitware's `dicom-anonymizer` **2026c** baseline with additional recursive private-tag, free-text, date/time, overlay, and identifier cleanup. Dates are emptied rather than shifted; private acquisition metadata is discarded. Confirm that this preserves what the research team needs before processing the full archive. This is not a claim of complete DICOM IOD conformance or HIPAA certification.

Supported export SOP classes: CT, enhanced CT, MR, enhanced MR, CR, presentation DX, presentation mammography, ultrasound image/multiframe, and secondary capture image. Unsupported SOP classes (including SR, encapsulated PDF, PR, RT and whole-slide imaging) are quarantined. JPEG/JPEG-LS/JPEG 2000 decoders are installed, but not every transfer syntax is guaranteed; decoding failures stop the relevant review/export. Preview and pixel masking currently support monochrome images only. Compressed images with masks are written as uncompressed little-endian.

Masks are `[x, y, width, height]` rectangles in original pixel coordinates and apply to every image/frame in the selected study. Out-of-bounds masks fail the export. `BurnedInAnnotation=YES` requires masks; a missing or incorrect tag does not prove the pixels are clean. Color images requiring masks, recognizable faces requiring defacing, and unsupported objects need specialist processing outside this application. Image review is mandatory for every study.

Text NLP can miss PHI and can remove useful clinical terms. Report review is mandatory. Default text encoding is UTF-8; use the wizard for CP1252 or UTF-16. Oversized or undecodable reports fail rather than being silently truncated. Matching is deliberately exact; no fuzzy automatic association.

Validate representative HOAG studies and reports against the hospital's approved release criteria before releasing any output. No generic installer can infer the hospital's privacy policy or prove de-identification on unseen images and clinical text.

## Operations

```bash
sudo systemctl status hoag-dashboard hoag-worker nginx
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python /opt/hoag-research/manage.py doctor
sudo /opt/hoag-research/configure
sudo systemctl restart hoag-dashboard hoag-worker
```

`doctor` checks configured mounts, read-only source access, output free space and NLP availability. `manage.py scan` / `match` queue work from the console. Only one job runs at a time. Interrupted jobs fail visibly; requeue them from the UI. No scan or export runs automatically during installation. App updates preserve configuration, source files, catalogs and exports. The prior discovery-only `catalog.sqlite` is not imported; the new workflow uses `workflow.sqlite` and requires a fresh scan.

Failed exports remain in restricted `OUTPUT/.staging` for operator investigation. Never send this directory to researchers. Completed directories appear only after all package files have been written. A network outage during final publication can leave a complete package without a corresponding catalog record; reconcile it using manifests before retrying. Output sizes can grow substantially when compressed pixels are masked; allow adequate capacity.

Back up `/etc/hoag-research` and `/var/lib/hoag-research` using hospital controls. Stop both application services before filesystem-copy backups of the SQLite database and WAL files, or use SQLite's online backup API. The catalog is local SQLite with WAL and indexed study/report keys, designed for a single worker. Archive-scale throughput must be measured on the actual shares. Per-instance reads are capped at 512 MiB by default; unusually large multiframe studies require a reviewed configuration change or another processing tool. Services cap worker RAM at 6 GiB and web RAM at 4 GiB.

Installation uses systemd/NGINX/SELinux conventions for RHEL 10. Synthetic workflow tests run in CI; actual RHEL installation, SMB permissions, trusted TLS and source-specific clinical validation still need to be verified on your server.

## Development verification

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pip install https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl
python -m unittest discover -s tests -v
bash -n install.sh
bash -n scripts/configure
```

Tests use synthetic DICOM/report fixtures only: full export, source immutability, ambiguous matches, changed sources/reports, persistent UID mappings, private/nested identifiers, masking, checksums, queue exclusion, mount checks, authentication, CSRF, image previews and real local NLP. Keep PHI, credentials and actual SQL dumps out of GitHub issues, commits and CI artifacts.

References: [Kitware DICOM anonymizer](https://github.com/KitwareMedical/dicom-anonymizer), [Presidio](https://microsoft.github.io/presidio/), [DICOM confidentiality options](https://dicom.nema.org/medical/dicom/current/output/chtml/part15/sect_E.3.html), [RHEL SMB mounting](https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/managing_file_systems/mounting-an-smb-share).

### Multiple source folders and SQL schema inspection

Open **Choose source folders** in the web workspace. Browse a share and check as many folders as needed (for example A-C and D-F). Include folders containing the reports and SQL dump. Selected parents include all their descendants. **Index selected folders** replaces the active catalog scope with that selection; existing export packages remain. **Index both entire shares** explicitly scans everything. Folder browsing is restricted to configured source mounts and excludes symlinks.

The server setup wizard prompts locally for SMB username, domain, and a hidden password. Credentials stay in root-only files on the server, outside GitHub. Persistent systemd automount entries reconnect the configured shares as needed; changed or expired passwords require rerunning `sudo /opt/hoag-research/configure`.

After indexing a SQL dump, use **Inspect table and column names**. This bounded heuristic reads at most the first 32 MiB and shows supported CREATE TABLE definitions, highlighting potential accession, study UID, patient ID, report, or path fields. It never executes SQL, connects to a database, imports rows, or returns row values. Dumps without schema statements or with later schema definitions may require a separate local inspection. Table and column identifiers themselves are displayed only in the authenticated hospital workspace.

Database row matching is not implemented yet. Once the actual schema is known, a source-specific importer can extract study-to-report relationships into the local catalog. Accession or study UID links are preferred; patient ID alone does not establish a unique study/report match. The original dump remains read-only and must stay outside the public repository.

## Enhanced workspace and GUI share setup

The dashboard now shows workflow stages, separate file/report/study counts, processing phase, elapsed time, scanning rate and last successful scan. A complete archive count is unknown during traversal, so no percentage or ETA is invented. Studies appear incrementally during catalog building; review/export remain blocked until a successful scan completes. Search, status/modality/date filters, sorting and numbered pagination stay intact while reviewing a study in the same page.

Matching queues expose unmatched, single-candidate, ambiguous and conflicting studies. Review shows candidate report text alongside images, highlighted `[REDACTED]` tokens, zoom, frame navigation and drawable rectangular masks in original pixel coordinates. Masks still apply to every instance/frame in a study; different image dimensions may require specialist handling. Existing manual review requirements remain.

Jobs can be cancelled cooperatively or retried. Cancellation waits for the current filesystem/decoder/NLP operation and checks safe boundaries; it cannot immediately interrupt a blocked SMB read. Completed batch studies remain; partial exports remain restricted in `.staging`. A cancelled scan requires a fresh successful scan. Source errors are listed locally (first 100). Export history shows the latest 100 packages and supports queued manifest/file checksum verification. Integrity verification does not certify de-identification.

**Sources & mounts** lets the authenticated administrator connect or reconnect the three standard share slots. Enter UNC, SMB username, domain and password and confirm the reconnection checkbox. A local Unix-socket broker (`hoag-mount.service`) runs as root, accepts only the application service UID, validates fixed mountpoints, and uses fixed SMB options without a shell. The web and worker services remain unprivileged and keep `NoNewPrivileges=yes`. The broker is deliberately a privileged service and should be included in hospital security review. It cannot change arbitrary mountpoints or override unrelated IT-managed fstab entries. Passwords are cleared from the form after submission, never included in jobs, process arguments or audit events, and saved root-only under `/etc/hoag-research`. Trusted HTTPS is required for credential entry.

Mount changes are blocked while any job is queued/running, reserve the catalog during reconnection, and invalidate scan readiness and approvals. Rescan after a connection change. Failed connections restore prior fstab/credential settings and attempt to reconnect the prior mount; verify connection health before retrying. The installer refuses updates while a job is active. Wait for your current scan to finish before installing this release.

```bash
sudo systemctl --no-pager status hoag-mount
```

Database inspection remains schema-only; actual SQL row matching still requires a source-specific importer. No hospital data, hostnames or credentials should be committed to this public repository.
