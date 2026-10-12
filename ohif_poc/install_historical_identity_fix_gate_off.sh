#!/usr/bin/env bash
# Install hardened historical review parser/gateway only. Keep review gate OFF.
# Does not touch existing OHIF, NGINX, SQL, worker or catalog state.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/hoag-research
GATE=/etc/hoag-research/historical-emr-review.enabled
STAMP="$(date +%Y%m%d-%H%M%S)"
cd "$ROOT"
echo "=== VERIFY ROOT GATE IS DISABLED ==="
if sudo test -e "$GATE" || sudo test -L "$GATE"; then
  echo "STOP: Historical EMR gate is still present; remove it separately first."
  exit 1
fi
echo "=== REGRESSION TESTS ==="
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q
/opt/hoag-research/venv/bin/python -m py_compile \
  historical_emr_review.py txt_candidate_discovery.py
echo "=== BACKUP PRODUCTION MODULES ==="
BACKUP="/var/lib/hoag-research/backups/pre-historical-identity-fix-$STAMP.tar.gz"
sudo tar -czf "$BACKUP" -C "$APP" \
  historical_emr_review.py txt_candidate_discovery.py
sudo chmod 0600 "$BACKUP"
echo "Rollback archive: $BACKUP"
echo "=== INSTALL REVIEW-ONLY IDENTITY CORRECTION ==="
sudo install -o root -g root -m 0644 \
  historical_emr_review.py "$APP/historical_emr_review.py"
sudo install -o root -g root -m 0644 \
  txt_candidate_discovery.py "$APP/txt_candidate_discovery.py"
echo "=== RESTART HOAG DASHBOARD ==="
sudo systemctl restart hoag-dashboard.service
ready=0
for n in $(seq 1 15); do
  status="$(curl -s -m 3 -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:8080/api/historical-emr/candidates || true)"
  if [ "$status" = 401 ]; then ready=1; break; fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "STOP: Dashboard authentication check failed."
  exit 1
fi
echo "Dashboard requires authentication: 401."
echo "=== CONFIRM HISTORICAL GATE REMAINS DISABLED ==="
sudo test ! -e "$GATE"
sudo test ! -L "$GATE"
for service in hoag-dashboard hoag-worker hoag-mount hoag-sql nginx; do
  printf '%s: ' "$service"; systemctl is-active "$service"
done
echo "Identity fix installed. Historical report review remains DISABLED."
