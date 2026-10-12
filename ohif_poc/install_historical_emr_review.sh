#!/usr/bin/env bash
# Review-only historical EMR lead panel; requires hospital approval.
# No DICOMweb changes, image access changes, activation, association or exports.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/hoag-research
GATE=/etc/hoag-research/historical-emr-review.enabled
BACKUPS=/var/lib/hoag-research/backups
if [ "$(printenv HOAG_APPROVED_HISTORICAL_REVIEW || true)" != YES ]; then
  echo "STOP: Explicit hospital approval required for historical clinical report preview."
  exit 1
fi
cd "$ROOT"
echo "=== TESTS ==="
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q
/opt/hoag-research/venv/bin/python -m py_compile \
  historical_emr_review.py webapp.py
echo "=== HEALTH PRECHECK ==="
sudo systemctl is-active --quiet hoag-dashboard
sudo test -d "$APP/ohif-assets"
if sudo test -e "$GATE" || sudo test -L "$GATE"; then
  echo "STOP: Historical review gate already exists. Inspect before installing."
  exit 1
fi
sudo python3 - <<'PY'
import json,sqlite3
from pathlib import Path
cfg=json.loads(Path("/etc/hoag-research/config.json").read_text())
with sqlite3.connect(Path(cfg["state_dir"])/"workflow.sqlite") as db:
    if db.execute("SELECT 1 FROM jobs WHERE state IN ('queued','running')").fetchone():
        raise SystemExit("STOP: job running")
print("Index idle.")
PY
echo "=== BACKUP ==="
STAMP="$(date +%Y%m%d-%H%M%S)"
BACKUP="$BACKUPS/pre-historical-emr-review-$STAMP.tar.gz"
sudo tar -czf "$BACKUP" -C "$APP" webapp.py static/app.js templates/index.html
sudo chmod 0600 "$BACKUP"
echo "Backup: $BACKUP"
echo "=== INSTALL REVIEW-ONLY MODULE AND INTERFACE ==="
sudo install -o root -g root -m 0644 historical_emr_review.py "$APP/historical_emr_review.py"
sudo install -o root -g root -m 0644 historical_emr_lead_uniqueness.py "$APP/historical_emr_lead_uniqueness.py"
sudo install -o root -g root -m 0644 historical_emr_overlap.py "$APP/historical_emr_overlap.py"
sudo install -o root -g root -m 0644 emr_txt_leads.py "$APP/emr_txt_leads.py"
sudo install -o root -g root -m 0644 txt_candidate_discovery.py "$APP/txt_candidate_discovery.py"
sudo install -o root -g root -m 0644 strict_identity_match.py "$APP/strict_identity_match.py"
sudo install -o root -g root -m 0644 webapp.py "$APP/webapp.py"
sudo install -o root -g root -m 0644 static/app.js "$APP/static/app.js"
sudo install -o root -g root -m 0644 templates/index.html "$APP/templates/index.html"
echo "=== ENABLE EXPLICIT ROOT-CONTROLLED REVIEW GATE ==="
sudo python3 - <<'PY'
import os,pwd
from pathlib import Path
p=Path("/etc/hoag-research/historical-emr-review.enabled")
if p.is_symlink():
    raise SystemExit("STOP: symlink gate")
if p.exists():
    raise SystemExit("STOP: existing gate; inspect before replacing")
fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
with os.fdopen(fd,"w") as out:
    out.write("AUTHORIZED\n")
    out.flush()
    os.fsync(out.fileno())
os.chown(p,0,pwd.getpwnam("hoag-indexer").pw_gid)
os.chmod(p,0o640)
print("Historical report preview gate enabled.")
PY
echo "=== RESTART DASHBOARD ONLY ==="
sudo systemctl restart hoag-dashboard.service
ready=0
for n in $(seq 1 20); do
  status="$(curl -s -m 3 -o /dev/null -w '%{http_code}' \
    http://127.0.0.1:8080/api/historical-emr/candidates || true)"
  if [ "$status" = 401 ]; then ready=1; break; fi
  sleep 1
done
if [ "$ready" -ne 1 ]; then
  echo "STOP: expected unauthenticated HTTP 401. Rollback from backup."
  exit 1
fi
echo "Unauthenticated historical report preview rejected: HTTP 401"
for service in hoag-dashboard hoag-worker hoag-mount hoag-sql nginx; do
  printf "%s: " "$service"; systemctl is-active "$service"
done
echo "DONE. View the Historical EMR candidate review panel; no approval/export actions exist."
