# HOAG Research Pipeline — discovery pilot

Initial RHEL 10 installer and read-only catalog. **Not a de-identification product yet.**
This release inventories DICOM headers and TXT reports, identifies likely SQL dump formats,
and provides an aggregate status dashboard. It does not match reports, anonymize, or export.
Those stages require validating the real source conventions and HOAG's release policy.

## Install

This public repository can be cloned without GitHub authentication:

```bash
sudo dnf install -y git
git clone https://github.com/joshekey/hoag-research-pipeline.git hoag-research-pipeline
cd hoag-research-pipeline
sudo bash research_build
```

The installer needs enabled RHEL package repositories and access to Python's package index.
It installs a dedicated service account, Python virtual environment, cifs-utils, and a
dashboard systemd unit. It does not mount shares, start scans, or open network ports.
Re-running updates application files and dependencies; restart the dashboard afterward.
SELinux remains enabled. Investigate access denials rather than disabling it.

## Mount sources

Ask your share administrator for a read-only share account. Create a root-owned credentials
file using `sudoedit /root/hoag-smb.credentials`, containing:

```ini
username=YOUR_SHARE_ACCOUNT
password=YOUR_SHARE_PASSWORD
domain=YOUR_DOMAIN
```

Do not place this file inside the repository or put passwords on the command line.

```bash
sudo chmod 600 /root/hoag-smb.credentials
sudo mkdir -p /mnt/hoag-bulk /mnt/hoag-imaging
sudo mount -t cifs '//SERVER/BULK_SHARE' /mnt/hoag-bulk -o ro,credentials=/root/hoag-smb.credentials,vers=3.1.1,seal,uid=$(id -u hoag-indexer),gid=$(id -g hoag-indexer),file_mode=0400,dir_mode=0500,nosuid,nodev,noexec
sudo mount -t cifs '//SERVER/IMAGING_SHARE' /mnt/hoag-imaging -o ro,credentials=/root/hoag-smb.credentials,vers=3.1.1,seal,uid=$(id -u hoag-indexer),gid=$(id -g hoag-indexer),file_mode=0400,dir_mode=0500,nosuid,nodev,noexec
findmnt /mnt/hoag-bulk
findmnt /mnt/hoag-imaging
```

Both sources are mounted read-only. Share names containing spaces are quoted.
The bulk share and imaging share may each contain mixed content; their DICOM, TXT, and SQL
locations have not yet been confirmed. The commands assume one account can read both shares;
use separate root-owned credential files if their access accounts differ.

The example requires SMB 3.1.1 with encryption; coordinate unsupported
options with IT. These are temporary mounts; configure persistent mounts with IT after the pilot.
RHEL guidance: https://docs.redhat.com/en/documentation/red_hat_enterprise_linux/10/html/managing_file_systems/mounting-an-smb-share

## Run a small pilot

After inspecting the mounted shares locally, set the three paths below to actual locations.
Do not infer contents from share names. Choose representative small subfolders first. The catalog contains PHI, including report text;
keep `/var/lib/hoag-research` restricted and on hospital-managed storage. No patient data
belongs in GitHub issues, logs, commits, or CI artifacts.

```bash
DICOM_PILOT='/mnt/hoag-imaging/REPLACE_WITH_DICOM_SUBFOLDER'
REPORT_PILOT='/mnt/hoag-bulk/REPLACE_WITH_REPORT_SUBFOLDER'
SQL_DUMP='/mnt/hoag-bulk/REPLACE_WITH_DATABASE.sql'
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python /opt/hoag-research/pipeline.py scan --db /var/lib/hoag-research/catalog.sqlite --root "$DICOM_PILOT" --kind dicom
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python /opt/hoag-research/pipeline.py scan --db /var/lib/hoag-research/catalog.sqlite --root "$REPORT_PILOT" --kind report
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python /opt/hoag-research/pipeline.py status --db /var/lib/hoag-research/catalog.sqlite
sudo -u hoag-indexer /opt/hoag-research/venv/bin/python /opt/hoag-research/pipeline.py inspect-sql "$SQL_DUMP"
sudo systemctl enable --now hoag-dashboard
curl http://127.0.0.1:8080/api/status
```

SQL inspection reads only the first 64 KiB and executes nothing; the result is a heuristic.
The dashboard listens only at http://127.0.0.1:8080 on the server. For access from your workstation,
have IT configure an authenticated HTTPS reverse proxy. Do not expose this unauthenticated
pilot dashboard directly. SSH is not required for installation or command execution by you.

Scans read headers without pixels, retain report previews up to 1 MiB, skip unchanged successful
files, and retry errors. Non-DICOM files in the DICOM tree count as errors. Nonstandard DICOM
files lacking the standard preamble are rejected rather than force-parsed. Symlinks are skipped.
Use a single scanning process at a time. Source deletion reconciliation and source snapshots
are not implemented; counts describe catalog entries, not a verified current source snapshot.
Report encoding is provisionally UTF-8 with replacement; validate it before matching.

SQLite keeps this single-worker pilot simple. PostgreSQL migration, report candidate matching,
review controls, validated de-identification, Orthanc/OHIF, and exports are subsequent work.
Full output must go to a separate writable share; the 500 GB server disk is for catalog/staging.
Target export layout:

```text
SUBJECT_000001/STUDY_000001/DICOM/*.dcm
SUBJECT_000001/STUDY_000001/Report/report.txt
```

## Development checks (synthetic data only)

```bash
python -m pip install -r requirements.txt
python -m unittest discover -s tests -v
bash -n install.sh
```
