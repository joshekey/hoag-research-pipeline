#!/usr/bin/env bash
set -euo pipefail
umask 027
[[ $EUID -eq 0 ]] || { echo 'Run with sudo bash research_build'; exit 1; }
source /etc/os-release
[[ "$ID" == rhel && "$VERSION_ID" == 10* ]] || { echo 'This installer targets RHEL 10.'; exit 1; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
# Updates must never interrupt a scan, review preparation or export.
if [[ -f /etc/hoag-research/config.json ]]; then
  python3 - <<'PY_CHECK_IDLE'
import json, sqlite3
from pathlib import Path
config = json.loads(Path('/etc/hoag-research/config.json').read_text())
path = Path(config['state_dir']) / 'workflow.sqlite'
if path.exists():
    with sqlite3.connect(path.as_uri() + '?mode=ro', uri=True) as db:
        if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running') LIMIT 1").fetchone():
            raise SystemExit('An indexing or processing job is active. Wait for completion before installing updates.')
PY_CHECK_IDLE
fi
dnf install -y python3 python3-pip cifs-utils nginx openssl policycoreutils-python-utils
id hoag-indexer >/dev/null 2>&1 || useradd --system --home-dir /var/lib/hoag-research --shell /sbin/nologin hoag-indexer
install -d -m 0755 /opt/hoag-research /opt/hoag-research/templates /opt/hoag-research/static
install -d -o hoag-indexer -g hoag-indexer -m 0700 /var/lib/hoag-research
install -d -o root -g hoag-indexer -m 0750 /etc/hoag-research
python3 -m venv /opt/hoag-research/venv
/opt/hoag-research/venv/bin/pip install -r "$ROOT/requirements.txt"
/opt/hoag-research/venv/bin/pip install 'https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl'
# The service account must be able to traverse and read the root-installed runtime.
chmod -R a+rX /opt/hoag-research/venv
systemctl stop hoag-dashboard.service hoag-worker.service hoag-mount.service hoag-sql.service 2>/dev/null || true
for file in manage.py store.py engine.py worker.py webapp.py configure.py sql_schema.py mount_service.py sql_gui_candidates.py sql_broker.py broker_study_auth.py strict_identity_match.py sql_dicom_pilot.py config.example.json; do
  install -m 0644 "$ROOT/$file" "/opt/hoag-research/$file"
done
install -m 0644 "$ROOT/templates/index.html" /opt/hoag-research/templates/index.html
install -m 0644 "$ROOT/static/app.js" "$ROOT/static/app.css" /opt/hoag-research/static/
install -m 0755 "$ROOT/scripts/configure" /opt/hoag-research/configure
install -m 0644 "$ROOT/hoag-dashboard.service" "$ROOT/hoag-worker.service" "$ROOT/hoag-mount.service" "$ROOT/hoag-sql.service" /etc/systemd/system/
systemctl daemon-reload
if [[ ! -f /etc/hoag-research/config.json ]]; then
  /opt/hoag-research/venv/bin/python /opt/hoag-research/configure.py --initialize
fi
if [[ "${1:-}" != --no-configure ]]; then
  /opt/hoag-research/configure
fi
runuser -u hoag-indexer -- /opt/hoag-research/venv/bin/python /opt/hoag-research/manage.py init
if [[ ! -f /etc/hoag-research/tls.key ]]; then
  openssl req -x509 -newkey rsa:3072 -nodes -days 365 \
    -keyout /etc/hoag-research/tls.key -out /etc/hoag-research/tls.crt \
    -subj "/CN=$(hostname -f)" -addext "subjectAltName=DNS:$(hostname -f),DNS:$(hostname -s)"
  chmod 600 /etc/hoag-research/tls.key
fi
install -m 0644 "$ROOT/nginx.conf" /etc/nginx/conf.d/hoag-research.conf
install -m 0600 /etc/hoag-research/tls.key /etc/pki/tls/private/hoag-research.key
install -m 0644 /etc/hoag-research/tls.crt /etc/pki/tls/certs/hoag-research.crt
restorecon /etc/pki/tls/private/hoag-research.key /etc/pki/tls/certs/hoag-research.crt
restorecon -RF /etc/hoag-research /opt/hoag-research /var/lib/hoag-research /etc/nginx/conf.d/hoag-research.conf
setsebool -P httpd_can_network_connect 1
nginx -t
systemctl enable --now hoag-dashboard.service hoag-worker.service hoag-mount.service hoag-sql.service nginx.service
systemctl reload nginx.service
if systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-port=8443/tcp
  firewall-cmd --reload
fi
echo "Ready: https://$(hostname -f):8443"
echo 'Username: admin. Read initial password: sudo cat /etc/hoag-research/initial-admin-password'
echo 'Replace the bootstrap TLS certificate with a hospital-trusted certificate before browser use.'
echo 'Open the dashboard, index shares, review matches and images, then approve and export.'
