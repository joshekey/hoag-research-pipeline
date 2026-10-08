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

A writable output share is required: a 500 GB local disk cannot hold a 2–3 TB full export. Output must not overlap source roots. Keep local state on hospital-managed storage; it contains PHI and identity mappings.

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
