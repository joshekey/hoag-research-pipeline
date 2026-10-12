#!/usr/bin/env bash
# UI-only refinement. Preserve TXT preparation endpoint and all clinical safeguards.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/hoag-research
STAMP="$(date +%Y%m%d-%H%M%S)"
cd "$ROOT"
echo "=== FULL SYNTHETIC REGRESSION TESTS ==="
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q
if command -v node >/dev/null 2>&1; then
  node --check static/app.js
else
  echo "JavaScript parser unavailable: verify revised review screen in browser."
fi
echo "=== BACKUP BEFORE UI UPDATE ==="
BACKUP="/var/lib/hoag-research/backups/pre-legacy-txt-ui-$STAMP.tar.gz"
sudo tar -czf "$BACKUP" -C "$APP" \
  static/app.js static/app.css templates/index.html
sudo chmod 0600 "$BACKUP"
echo "Rollback backup: $BACKUP"
echo "=== INSTALL INTERFACE ONLY ==="
sudo install -o root -g root -m 0644 static/app.js "$APP/static/app.js"
sudo install -o root -g root -m 0644 static/app.css "$APP/static/app.css"
sudo install -o root -g root -m 0644 templates/index.html "$APP/templates/index.html"
sudo systemctl restart hoag-dashboard.service
echo "=== CHECK DASHBOARD AUTH ==="
ready=0
for attempt in $(seq 1 20); do
  code="$(curl -s --max-time 3 -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:8080/api/studies || true)"
  if [ "$code" = 401 ]; then ready=1; break; fi
  sleep 1
done
test "$ready" = 1
echo "Unauthenticated study catalog: HTTP 401"
for svc in hoag-dashboard hoag-worker hoag-mount hoag-sql nginx; do
  printf "%s: " "$svc"; systemctl is-active "$svc"
done
echo "UI-only update staged. SQL identity safety hold and all feature gates unchanged."
echo "Rollback: sudo tar -xzf $BACKUP -C $APP && sudo systemctl restart hoag-dashboard"
