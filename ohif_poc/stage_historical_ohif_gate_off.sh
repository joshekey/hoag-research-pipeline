#!/usr/bin/env bash
# Stage scoped historical OHIF gateway with clinical review gate remaining OFF.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/hoag-research
GATE=/etc/hoag-research/historical-emr-review.enabled
STAMP="$(date +%Y%m%d-%H%M%S)"
cd "$ROOT"
if sudo test -e "$GATE" || sudo test -L "$GATE"; then
  echo "STOP: historical review gate must remain OFF during staged installation."
  exit 1
fi
echo "=== TEST CODE ==="
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q
/opt/hoag-research/venv/bin/python -m py_compile \
  webapp.py historical_emr_review.py \
  ohif_poc/historical_retrieval.py ohif_poc/clinical_dicomweb.py \
  ohif_poc/historical_preflight.py
echo "=== CLINICAL HEADER COMPATIBILITY PREFLIGHT ==="
sudo env PYTHONPATH="$ROOT" /opt/hoag-research/venv/bin/python \
  ohif_poc/historical_preflight.py
echo "=== BACKUP ==="
BACKUP="/var/lib/hoag-research/backups/pre-historical-ohif-$STAMP.tar.gz"
sudo tar -czf "$BACKUP" -C "$APP" \
  webapp.py historical_emr_review.py \
  ohif_poc/clinical_dicomweb.py \
  static/app.js static/app.css templates/index.html
sudo chmod 0600 "$BACKUP"
echo "Rollback backup: $BACKUP"
echo "=== INSTALL READ-ONLY HISTORICAL VIEWER ==="
for file in \
  "webapp.py" \
  "historical_emr_review.py" \
  "ohif_poc/clinical_dicomweb.py" \
  "ohif_poc/historical_retrieval.py" \
  "static/app.js" \
  "static/app.css" \
  "templates/index.html"; do
  sudo install -o root -g root -m 0644 "$file" "$APP/$file"
done
echo "=== RESTART DASHBOARD ONLY ==="
sudo systemctl restart hoag-dashboard.service
ready=0
for n in $(seq 1 20); do
  code="$(curl -s -m 3 -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:8080/ohif/dicomweb/studies || true)"
  if [ "$code" = 401 ]; then ready=1; break; fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "STOP: dashboard failed unauthenticated access check; restore rollback backup."
  exit 1
fi
sudo test ! -e "$GATE"
sudo test ! -L "$GATE"
echo "Historical review remains DISABLED. Unauthenticated DICOMweb: HTTP 401."
for name in hoag-dashboard hoag-worker hoag-mount hoag-sql nginx; do
  printf "%s: " "$name"; systemctl is-active "$name"
done
echo "STAGED. Do not enable historical review until authorized browser checks."
