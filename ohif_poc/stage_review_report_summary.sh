#!/usr/bin/env bash
# Read-only UI enhancement: selected-study TXT/SQL candidate summary.
# This does not change report association, research export, DICOM or SQL matching engines.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/hoag-research
GATE=/etc/hoag-research/historical-emr-review.enabled
STAMP="$(date +%Y%m%d-%H%M%S)"
cd "$ROOT"
echo "=== VERIFY REGRESSION SUITE ==="
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q
if command -v node >/dev/null 2>&1; then
  node --check static/app.js
else
  echo "Node.js not installed: JavaScript syntax check skipped"
fi
echo "=== BACKUP ORIGINAL DASHBOARD ASSETS ==="
BACKUP="/var/lib/hoag-research/backups/pre-selected-study-report-summary-$STAMP.tar.gz"
sudo tar -czf "$BACKUP" -C "$APP" static/app.js templates/index.html
sudo chmod 0600 "$BACKUP"
echo "Backup: $BACKUP"
echo "=== UPDATE DASHBOARD ONLY ==="
sudo install -o root -g root -m 0644 static/app.js "$APP/static/app.js"
sudo install -o root -g root -m 0644 templates/index.html "$APP/templates/index.html"
sudo systemctl restart hoag-dashboard.service
echo "=== VERIFY AUTHENTICATION ==="
ok=0
for attempt in $(seq 1 20); do
  status="$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/studies || true)"
  if [ "$status" = 401 ]; then ok=1; break; fi
  sleep 1
done
if [ "$ok" -ne 1 ]; then
  echo "STOP: dashboard did not return expected HTTP 401"
  exit 1
fi
echo "Unauthenticated study catalog: HTTP 401"
for service in hoag-dashboard hoag-worker hoag-mount hoag-sql nginx; do
  printf '%s: ' "$service"
  systemctl is-active "$service"
done
if sudo test -e "$GATE"; then
  echo "Historical review gate: enabled (left unchanged)"
else
  echo "Historical review gate: disabled (left unchanged)"
fi
echo "Selected-study report summary installed. No report links or approvals changed."
