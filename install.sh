#!/usr/bin/env bash
set -euo pipefail
umask 027
[[ $EUID -eq 0 ]] || { echo 'Run with sudo bash install.sh'; exit 1; }
source /etc/os-release
[[ "$ID" == rhel && "$VERSION_ID" == 10* ]] || { echo 'This installer targets RHEL 10.'; exit 1; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
dnf install -y python3 python3-pip cifs-utils
id hoag-indexer >/dev/null 2>&1 || useradd --system --home-dir /var/lib/hoag-research --shell /sbin/nologin hoag-indexer
install -d -m 0755 /opt/hoag-research
install -d -o hoag-indexer -g hoag-indexer -m 0750 /var/lib/hoag-research
install -m 0644 "$ROOT/pipeline.py" /opt/hoag-research/pipeline.py
python3 -m venv /opt/hoag-research/venv
/opt/hoag-research/venv/bin/pip install -r "$ROOT/requirements.txt"
install -m 0644 "$ROOT/hoag-dashboard.service" /etc/systemd/system/hoag-dashboard.service
systemctl daemon-reload
echo 'Installed. See README.md for mounting shares, scanning, and starting the dashboard.'
echo 'No shares were mounted and no firewall rules were changed.'
