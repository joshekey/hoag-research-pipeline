#!/usr/bin/env bash
# Single-study HOAG OHIF pilot; requires hospital approval. No export changes.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ASSETS="$HOME/hoag-ohif-sandbox/assets"
APP=/opt/hoag-research
STATE=/var/lib/hoag-research
STAMP="$(date +%Y%m%d-%H%M%S)"
if [ "$(printenv HOAG_APPROVED_CLINICAL_VIEW || true)" != YES ]; then
  echo "STOP: Explicit hospital approval is required before clinical image serving."
  exit 1
fi
test -f "$ASSETS/index.html"
test -f "$ASSETS/app-config.js"
cd "$ROOT"
echo "=== TESTS ==="
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q
/opt/hoag-research/venv/bin/python -m py_compile webapp.py ohif_poc/clinical_dicomweb.py

echo "=== PREFLIGHT ==="
sudo python3 - <<'PY'
import json,sqlite3
from pathlib import Path
cfg=json.loads(Path("/etc/hoag-research/config.json").read_text())
dbfile=Path(cfg["state_dir"])/"workflow.sqlite"
with sqlite3.connect(dbfile.as_uri()+"?mode=ro",uri=True) as db:
    active=db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
    rows=db.execute("""SELECT s.uid FROM studies s JOIN sql_associations a ON a.uid=s.uid
    WHERE s.count=157 AND s.fingerprint=a.study_fingerprint AND a.date_verified=1
    AND a.conflicts_acknowledged=1 AND s.state NOT IN ('conflict','needs_review')""").fetchall()
if active or len(rows)!=1:
    raise SystemExit("STOP: Expect precisely one verified 157-instance study and no jobs.")
print("Single permitted clinical pilot confirmed; identifiers omitted.")
PY

echo "=== VERIFY PILOT TRANSFER SYNTAX / FRAMES ==="
sudo env PYTHONPATH="$ROOT" /opt/hoag-research/venv/bin/python - <<'PY'
import json,sqlite3
from pathlib import Path
from ohif_poc.preflight import check
cfg=json.loads(Path("/etc/hoag-research/config.json").read_text())
catalog=Path(cfg["state_dir"])/"workflow.sqlite"
with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
    rows=db.execute("""SELECT s.uid FROM studies s JOIN sql_associations a ON a.uid=s.uid
    WHERE s.count=157 AND s.fingerprint=a.study_fingerprint AND a.date_verified=1
    AND a.conflicts_acknowledged=1 AND s.state NOT IN ('conflict','needs_review')""").fetchall()
if len(rows)!=1:
    raise SystemExit("STOP: No unique verified pilot study.")
result=check(cfg,rows[0][0])
if not(result["instances_indexed"]==157 and result["native_uncompressed_objects"]==157
       and result["multi_frame_objects"]==0 and result["read_or_consistency_errors"]==0
       and not result["missing_required_tags"]):
    raise SystemExit("STOP: Pilot requires unsupported frames or transfer syntax; do not enable gateway.")
print("157/157 native little-endian single-frame objects verified.")
PY

echo "=== BACKUP ==="
BACKUP="$STATE/backups/pre-ohif-clinical-$STAMP.tar.gz"
sudo tar -czf "$BACKUP" -C "$APP" webapp.py static/app.js templates/index.html
sudo chmod 0600 "$BACKUP"
echo "Backup: $BACKUP"

echo "=== INSTALL LOCAL OHIF ASSETS ==="
sudo install -d -o root -g root -m 0755 "$APP/ohif-assets"
sudo cp -R "$ASSETS/." "$APP/ohif-assets/"
sudo chown -R root:root "$APP/ohif-assets"
sudo find "$APP/ohif-assets" -type d -exec chmod 0755 {} +
sudo find "$APP/ohif-assets" -type f -exec chmod 0644 {} +
sudo install -o root -g root -m 0644 "$ROOT/ohif_poc/clinical_app_config_392.js" "$APP/ohif-assets/app-config.js"

echo "=== INSTALL SECURED SINGLE-STUDY GATEWAY ==="
sudo install -d -o root -g root -m 0755 "$APP/ohif_poc"
for file in scoped_catalog.py clinical_retrieval_core.py clinical_dicomweb.py; do
  sudo install -o root -g root -m 0644 "$ROOT/ohif_poc/$file" "$APP/ohif_poc/$file"
done
sudo install -o root -g root -m 0644 "$ROOT/webapp.py" "$APP/webapp.py"
sudo install -o root -g root -m 0644 "$ROOT/static/app.js" "$APP/static/app.js"
sudo install -o root -g root -m 0644 "$ROOT/templates/index.html" "$APP/templates/index.html"

echo "=== SINGLE STUDY ALLOWLIST ==="
sudo python3 - <<'PY'
import json,os,pwd,sqlite3
from pathlib import Path
cfg=json.loads(Path("/etc/hoag-research/config.json").read_text())
catalog=Path(cfg["state_dir"])/"workflow.sqlite"
with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
    active=db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
    rows=db.execute("""SELECT s.uid FROM studies s JOIN sql_associations a ON a.uid=s.uid
    WHERE s.count=157 AND s.fingerprint=a.study_fingerprint AND a.date_verified=1
    AND a.conflicts_acknowledged=1 AND s.state NOT IN ('conflict','needs_review')""").fetchall()
if active or len(rows)!=1:
    raise SystemExit("STOP: Pilot authorization changed. Gateway not enabled.")
gate=Path("/etc/hoag-research/ohif-pilot-study.uid")
if gate.is_symlink():
    raise SystemExit("STOP: Symlink allowlist forbidden")
fd=os.open(gate,os.O_WRONLY|os.O_CREAT|os.O_TRUNC|os.O_NOFOLLOW,0o640)
with os.fdopen(fd,"w") as f:
    f.write(rows[0][0]+"\n")
os.chown(gate,0,pwd.getpwnam("hoag-indexer").pw_gid)
os.chmod(gate,0o640)
print("One pilot study authorized; identifiers omitted.")
PY

echo "=== RESTART DASHBOARD ONLY ==="
sudo /opt/hoag-research/venv/bin/python -m py_compile "$APP/webapp.py" "$APP/ohif_poc/clinical_dicomweb.py"
sudo systemctl restart hoag-dashboard.service
sudo systemctl is-active hoag-dashboard.service
for svc in hoag-worker hoag-mount hoag-sql nginx; do systemctl is-active "$svc"; done
echo "=== UNAUTHENTICATED ACCESS CHECK ==="
status="$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/ohif/dicomweb/studies)"
test "$status" = 401 || { echo "STOP: Expected HTTP 401, got $status"; exit 1; }
echo "Unauthenticated gateway rejected: HTTP $status"
echo "Pilot installed. Image viewing does not approve research export."
