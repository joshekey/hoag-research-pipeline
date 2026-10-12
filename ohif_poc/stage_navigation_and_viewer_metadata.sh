#!/usr/bin/env bash
# Safe staging: OHIF QIDO modality correction plus dashboard navigation.
# Never modifies source images, DB catalog, associations, exports, or feature gates.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/hoag-research
STAMP="$(date +%Y%m%d-%H%M%S)"
cd "$ROOT"
echo "=== RUN FULL REGRESSION TESTS ==="
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q
echo "=== COMPILE DICOMWEB GATEWAY ==="
/opt/hoag-research/venv/bin/python -m py_compile ohif_poc/clinical_dicomweb.py
if command -v node >/dev/null 2>&1; then
  echo "=== CHECK JAVASCRIPT ==="
  node --check static/app.js
else
  echo "JS parser not installed; manual browser check required."
fi
echo "=== BACKUP DASHBOARD AND VIEWER GATEWAY ==="
BACKUP="/var/lib/hoag-research/backups/pre-ohif-modality-navigation-$STAMP.tar.gz"
sudo tar -czf "$BACKUP" -C "$APP" \
  ohif_poc/clinical_dicomweb.py static/app.js static/app.css templates/index.html
sudo chmod 0600 "$BACKUP"
echo "Rollback backup: $BACKUP"
echo "=== INSTALL READ-ONLY CLINICAL VIEWER AND NAVIGATION UPDATE ==="
sudo install -o root -g root -m 0644 ohif_poc/clinical_dicomweb.py "$APP/ohif_poc/clinical_dicomweb.py"
sudo install -o root -g root -m 0644 static/app.js "$APP/static/app.js"
sudo install -o root -g root -m 0644 static/app.css "$APP/static/app.css"
sudo install -o root -g root -m 0644 templates/index.html "$APP/templates/index.html"
sudo systemctl restart hoag-dashboard.service
echo "=== CHECK AUTHENTICATION AND HEALTH ==="
pass=0
for n in $(seq 1 20); do
  code="$(curl --silent --max-time 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/studies || true)"
  if [ "$code" = "401" ]; then pass=1; break; fi
  sleep 1
done
if [ "$pass" -ne 1 ]; then
  echo "FAIL: unauthenticated study catalog did not return 401."
  exit 1
fi
echo "Unauthenticated study catalog: HTTP 401"
for svc in hoag-dashboard hoag-worker hoag-mount hoag-sql nginx; do
  printf '%s: ' "$svc"; systemctl is-active "$svc"
done
echo "STAGED. No catalog, report, export or authorization-gate changes."
