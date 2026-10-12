#!/usr/bin/env bash
# Stage explicit historical-study selection with historical review gate OFF.
# Do not modify source catalog, DICOMweb, SQL broker, or export approvals.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/hoag-research
GATE=/etc/hoag-research/historical-emr-review.enabled
STAMP="$(date +%Y%m%d-%H%M%S)"
cd "$ROOT"
echo "=== REQUIRE DISABLED HISTORICAL GATE ==="
if sudo test -e "$GATE" || sudo test -L "$GATE"; then
  echo "STOP: disable historical review before installing this fix"
  exit 1
fi
echo "=== REGRESSION AND PYTHON CHECKS ==="
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q
/opt/hoag-research/venv/bin/python -m py_compile historical_emr_review.py
echo "=== BACKUP ==="
BACKUP="/var/lib/hoag-research/backups/pre-historical-study-selection-$STAMP.tar.gz"
sudo tar -czf "$BACKUP" -C "$APP" \
  historical_emr_review.py static/app.js templates/index.html
sudo chmod 0600 "$BACKUP"
echo "Backup: $BACKUP"
echo "=== STAGE HISTORICAL STUDY SELECTION ==="
sudo install -o root -g root -m 0644 historical_emr_review.py "$APP/historical_emr_review.py"
sudo install -o root -g root -m 0644 static/app.js "$APP/static/app.js"
sudo install -o root -g root -m 0644 templates/index.html "$APP/templates/index.html"
sudo systemctl restart hoag-dashboard.service
echo "=== CONFIRM AUTH AND GATE ==="
ok=0
for n in $(seq 1 20); do
  code="$(curl -s -m 3 -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:8080/api/historical-emr/candidates || true)"
  if [ "$code" = 401 ]; then ok=1; break; fi
  sleep 1
done
if [ "$ok" -ne 1 ]; then
  echo "STOP: service authentication test did not return 401"
  exit 1
fi
sudo test ! -e "$GATE"
sudo test ! -L "$GATE"
echo "Unauthenticated historical API: HTTP 401"
for svc in hoag-dashboard hoag-worker hoag-mount hoag-sql nginx; do
  printf '%s: ' "$svc"; systemctl is-active "$svc"
done
echo "Staged; historical review gate remains disabled."
