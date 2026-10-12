#!/usr/bin/env bash
# SQL patient-identity incident containment plus Help/reconcile UI.
# SQL preview/list/confirmation remains DISABLED until independent audit.
# This installer never indexes sources, changes any clinical data or authorizes export.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/hoag-research
GATE=/etc/hoag-research/sql-candidate-review.enabled
STAMP="$(date +%Y%m%d-%H%M%S)"
cd "$ROOT"

echo "=== VERIFY SQL REVIEW SAFETY GATE IS OFF ==="
if sudo test -e "$GATE" || sudo test -L "$GATE"; then
  echo "STOP: SQL review authorization gate exists. Disable it before proceeding."
  exit 1
fi

echo "=== VERIFY SYNTAX AND SYNTHETIC REGRESSION TESTS ==="
/opt/hoag-research/venv/bin/python -m py_compile \
  webapp.py sql_broker.py tests/test_sql_review_safety_gate.py
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q
if command -v node >/dev/null 2>&1; then
  node --check static/app.js
else
  echo "JavaScript parser not installed; browser verification remains required."
fi

echo "=== BACKUP CURRENT PRODUCTION FILES ==="
BACKUP="/var/lib/hoag-research/backups/pre-sql-identity-hold-help-$STAMP.tar.gz"
sudo tar -czf "$BACKUP" -C "$APP" \
  webapp.py sql_broker.py static/app.js static/app.css templates/index.html
sudo chmod 0600 "$BACKUP"
echo "Rollback backup: $BACKUP"

echo "=== INSTALL SQL IDENTITY GUARD + HELP/RECONCILE UI ==="
sudo install -o root -g root -m 0644 webapp.py "$APP/webapp.py"
sudo install -o root -g root -m 0644 sql_broker.py "$APP/sql_broker.py"
sudo install -o root -g root -m 0644 static/app.js "$APP/static/app.js"
sudo install -o root -g root -m 0644 static/app.css "$APP/static/app.css"
sudo install -o root -g root -m 0644 templates/index.html "$APP/templates/index.html"
sudo systemctl restart hoag-sql.service
sudo systemctl restart hoag-dashboard.service

echo "=== VERIFY AUTHENTICATION AND SQL SAFETY HOLD ==="
ok=0
for n in $(seq 1 20); do
  auth="$(curl -sS -m 3 -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:8080/api/studies || true)"
  if [ "$auth" = "401" ]; then ok=1; break; fi
  sleep 1
done
test "$ok" = 1
sudo test ! -e "$GATE"
sudo test ! -L "$GATE"
echo "Unauthenticated studies API: HTTP 401"
for svc in hoag-dashboard hoag-worker hoag-mount hoag-sql nginx; do
  printf '%s: ' "$svc"; systemctl is-active "$svc"
done
echo "SQL candidate review: safety hold ON (missing authorization gate)"
echo "No DICOM/report/export source files or research approvals modified."
echo "Rollback archive: $BACKUP"
