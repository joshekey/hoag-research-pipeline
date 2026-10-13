#!/usr/bin/env bash
set -euo pipefail
umask 027
[[ $EUID -eq 0 ]] || { echo 'Run with sudo bash research_build'; exit 1; }
source /etc/os-release
[[ "$ID" == rhel && "$VERSION_ID" == 10* ]] || { echo 'This installer targets RHEL 10.'; exit 1; }
ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
[[ $# -le 1 && ( $# -eq 0 || "$1" == --no-configure ) ]] || { echo 'Usage: sudo bash research_build [--no-configure]'; exit 1; }
exec 9>/run/hoag-research-install.lock
flock -n 9 || { echo 'Another installation is running'; exit 1; }
if [[ "${1:-}" == --no-configure && ! -f /etc/hoag-research/config.json ]]; then
  echo 'No full application configuration exists. Run sudo bash research_build to configure the shares.'
  exit 1
fi
# Updates must never interrupt a scan, review preparation or export.
if [[ -f /etc/hoag-research/config.json ]]; then
  python3 "$ROOT/install_support.py" check-idle
fi
if pgrep -f '^.*python[^ ]* /opt/hoag-research/pipeline.py scan( |$)' >/dev/null; then
  echo 'A legacy discovery scan is active. Wait for it to finish before installing.'
  exit 1
fi
dnf install -y python3 python3-pip cifs-utils nginx openssl policycoreutils-python-utils
id hoag-indexer >/dev/null 2>&1 || useradd --system --home-dir /var/lib/hoag-research --shell /sbin/nologin hoag-indexer
install -d -m 0755 /opt/hoag-research /opt/hoag-research/templates /opt/hoag-research/static
install -d -o hoag-indexer -g hoag-indexer -m 0700 /var/lib/hoag-research
install -d -o root -g root -m 0700 /var/lib/hoag-research/sql-private
install -d -o root -g hoag-indexer -m 0750 /etc/hoag-research
RUNTIME="/opt/hoag-research-runtimes/$(date +%s)-$$"
install -d -m 0755 /opt/hoag-research-runtimes
python3 -m venv "$RUNTIME"
"$RUNTIME/bin/pip" install -r "$ROOT/requirements.txt"
"$RUNTIME/bin/pip" install 'https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl'
# The service account must be able to traverse and read the root-installed runtime.
chmod -R a+rX "$RUNTIME"
runuser -u hoag-indexer -- "$RUNTIME/bin/python" -c 'import flask, pydicom, dicomanonymizer, presidio_analyzer, spacy; spacy.load("en_core_web_sm")'
# Recheck after downloads and close the queue/installer race before copying files.
python3 "$ROOT/install_support.py" reserve
ACTIVE=()
for unit in hoag-dashboard.service hoag-worker.service hoag-mount.service hoag-sql.service; do
  if systemctl is-active --quiet "$unit"; then ACTIVE+=("$unit"); fi
done
resume_previous() {
  python3 "$ROOT/install_support.py" release || true
  if [[ ${#ACTIVE[@]} -gt 0 ]]; then systemctl start "${ACTIVE[@]}"; fi
  echo "Installation failed. Preserve the printed backup and inspect service status before retrying."
}
trap resume_previous ERR
systemctl stop hoag-dashboard.service hoag-worker.service hoag-mount.service hoag-sql.service 2>/dev/null || true
for unit in hoag-dashboard.service hoag-worker.service hoag-mount.service hoag-sql.service; do
  if systemctl is-active --quiet "$unit"; then
    echo "Could not stop $unit; refusing to replace application files."
    resume_previous
    exit 1
  fi
done
python3 "$ROOT/install_support.py" check-idle
BACKUP="/var/backups/hoag-research/$(date +%s)-$$"
python3 "$ROOT/install_support.py" backup --destination "$BACKUP"
# Switch only after the new environment is usable; retain the old one for recovery.
if [[ -e /opt/hoag-research/venv || -L /opt/hoag-research/venv ]]; then
  mv /opt/hoag-research/venv "/opt/hoag-research/venv.previous.$(date +%s)-$$"
fi
ln -s "$RUNTIME" /opt/hoag-research/venv
for file in "$ROOT"/*.py "$ROOT/config.example.json" "$ROOT/VERSION"; do
  install -m 0644 "$file" "/opt/hoag-research/$(basename "$file")"
done
install -d -m 0755 /opt/hoag-research/ohif_poc
for file in "$ROOT"/ohif_poc/*.py; do
  install -m 0644 "$file" "/opt/hoag-research/ohif_poc/$(basename "$file")"
done
install -m 0644 "$ROOT/templates/index.html" /opt/hoag-research/templates/index.html
install -m 0644 "$ROOT"/static/*.js "$ROOT"/static/*.css /opt/hoag-research/static/
if [ -f /opt/hoag-research/ohif-assets/index.html ]; then
  install -m 0644 "$ROOT/ohif_poc/clinical_app_config_392.js" /opt/hoag-research/ohif-assets/app-config.js
fi
install -m 0755 "$ROOT/scripts/configure" /opt/hoag-research/configure
install -m 0644 "$ROOT/hoag-dashboard.service" "$ROOT/hoag-worker.service" "$ROOT/hoag-mount.service" /etc/systemd/system/
install -m 0644 "$ROOT/hoag-sql.service" /etc/systemd/system/
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
restorecon -RF "$RUNTIME"
setsebool -P httpd_can_network_connect 1
nginx -t
python3 "$ROOT/install_support.py" release
systemctl enable --now hoag-dashboard.service hoag-worker.service hoag-mount.service nginx.service
systemctl reload nginx.service
systemctl enable --now hoag-sql.service
for unit in "${ACTIVE[@]}"; do
  if [[ "$unit" == hoag-sql.service ]]; then systemctl start "$unit"; fi
done
trap - ERR
if systemctl is-active --quiet firewalld; then
  firewall-cmd --permanent --add-port=8443/tcp
  firewall-cmd --reload
fi
echo "Ready: https://$(hostname -f):8443"
echo 'Username: admin. Read initial password: sudo cat /etc/hoag-research/initial-admin-password'
echo 'Replace the bootstrap TLS certificate with a hospital-trusted certificate before browser use.'
echo 'Open the dashboard, index shares, review matches and images, then approve and export.'
