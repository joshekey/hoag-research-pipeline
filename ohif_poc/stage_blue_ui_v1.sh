#!/usr/bin/env bash
# HOAG blue UI v1: UI assets only. No DICOM, SQL, source share, or approval changes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/hoag-research
STAMP="$(date +%Y%m%d-%H%M%S)"
cd "$ROOT"

echo "=== RUN REGRESSION TESTS ==="
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q

echo "=== CHECK FRONT-END SYNTAX IF AVAILABLE ==="
if command -v node >/dev/null 2>&1; then
  node --check static/app.js
elif command -v js >/dev/null 2>&1 && js --help 2>&1 | grep -q 'JavaScript'; then
  js -c static/app.js
else
  echo "JavaScript syntax validator unavailable; browser acceptance testing required."
fi

echo "=== VERIFY PRODUCTION UI ASSETS ==="
for file in static/app.js static/app.css templates/index.html; do
  sudo test -f "$APP/$file"
done

echo "=== CREATE ROLLBACK BACKUP ==="
BACKUP="/var/lib/hoag-research/backups/pre-blue-ui-$STAMP.tar.gz"
sudo tar -czf "$BACKUP" -C "$APP" \
  static/app.js static/app.css templates/index.html
sudo chmod 0600 "$BACKUP"
echo "Rollback backup: $BACKUP"

echo "=== INSTALL BLUE UI ONLY ==="
sudo install -o root -g root -m 0644 static/app.js "$APP/static/app.js"
sudo install -o root -g root -m 0644 static/app.css "$APP/static/app.css"
sudo install -o root -g root -m 0644 templates/index.html "$APP/templates/index.html"
sudo systemctl restart hoag-dashboard.service

echo "=== CHECK AUTHENTICATION ==="
pass=0
for n in $(seq 1 20); do
  code="$(curl -s --max-time 3 -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:8080/api/studies || true)"
  if [ "$code" = "401" ]; then pass=1; break; fi
  sleep 1
done
if [ "$pass" != 1 ]; then
  echo "STOP: authentication smoke test did not return HTTP 401"
  exit 1
fi
echo "Unauthenticated study catalog: HTTP 401"
for svc in hoag-dashboard hoag-worker hoag-mount hoag-sql nginx; do
  printf '%s: ' "$svc"
  systemctl is-active "$svc"
done
echo "Blue UI staged; source data, DICOM gateway, feature gates and exports unchanged."
echo "Rollback: sudo tar -xzf $BACKUP -C $APP && sudo systemctl restart hoag-dashboard"
