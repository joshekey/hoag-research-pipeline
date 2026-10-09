#!/usr/bin/env bash
# Extend existing HOAG OHIF pilot to all COMPLETE indexed studies, only with
# institution-approved broader original-clinical-image access.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/hoag-research
GATE=/etc/hoag-research/ohif-pilot-study.uid
STAMP="$(date +%Y%m%d-%H%M%S)"
if [ "$(printenv HOAG_APPROVED_ALL_INDEXED || true)" != YES ]; then
  echo "STOP: Hospital approval is required for expanding clinical access."
  exit 1
fi
cd "$ROOT"
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q
/opt/hoag-research/venv/bin/python -m py_compile webapp.py ohif_poc/clinical_dicomweb.py
sudo env PYTHONPATH="$ROOT" /opt/hoag-research/venv/bin/python - <<'PY'
import json,sqlite3
from pathlib import Path
from ohif_poc.preflight import check
cfg=json.loads(Path("/etc/hoag-research/config.json").read_text())
catalog=Path(cfg["state_dir"])/"workflow.sqlite"
with sqlite3.connect(catalog.as_uri()+"?mode=ro",uri=True) as db:
    active=db.execute("SELECT count(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
    rows=db.execute("""SELECT s.uid FROM studies s WHERE s.count>0
      AND s.state NOT IN ('conflict','needs_review')
      AND (SELECT count(*) FROM files f WHERE f.kind='dicom' AND f.active=1
      AND f.status='ok' AND json_extract(f.metadata,'$.StudyInstanceUID')=s.uid)=s.count
      ORDER BY s.uid""").fetchall()
if active or not rows: raise SystemExit("STOP: No eligible studies or processing busy.")
count=0
compressed=0
for (uid,) in rows:
    r=check(cfg,uid)
    if not (r["ready_for_dicomweb_adapter_design"] and r["multi_frame_objects"]==0
            and r["native_uncompressed_objects"]+r["supported_lossless_compressed_objects"]==r["instances_indexed"]
            and r["unsupported_transfer_syntax_objects"]==0):
        raise SystemExit("STOP: Unsupported transfer syntax or multiframe study.")
    count += r["instances_indexed"]
    compressed += r["supported_lossless_compressed_objects"]
print("Eligible complete studies:",len(rows))
print("Compatible indexed instances:",count)
print("Supported lossless compressed instances:",compressed)
print("No patient identifiers or actual UIDs displayed.")
PY
sudo test -f "$GATE"
sudo test -f "$APP/ohif-assets/index.html"
BACKUP="/var/lib/hoag-research/backups/pre-ohif-all-indexed-$STAMP.tar.gz"
sudo tar -czf "$BACKUP" -C / opt/hoag-research/webapp.py \
  opt/hoag-research/ohif_poc/clinical_dicomweb.py \
  etc/hoag-research/ohif-pilot-study.uid
sudo chmod 0600 "$BACKUP"
echo "Protected rollback archive created: $BACKUP"
sudo install -o root -g root -m 0644 webapp.py "$APP/webapp.py"
sudo install -o root -g root -m 0644 ohif_poc/clinical_dicomweb.py "$APP/ohif_poc/clinical_dicomweb.py"
sudo /opt/hoag-research/venv/bin/python -m py_compile "$APP/webapp.py" "$APP/ohif_poc/clinical_dicomweb.py"
sudo python3 - <<'PY'
import os,pwd
from pathlib import Path
p=Path("/etc/hoag-research/ohif-pilot-study.uid")
if p.is_symlink() or not p.is_file(): raise SystemExit("STOP: Unsafe allowlist")
tmp=p.with_name(".ohif-allowlist-next")
fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL|os.O_NOFOLLOW,0o640)
with os.fdopen(fd,"w") as f:
    f.write("ALL_INDEXED\n")
    f.flush();os.fsync(f.fileno())
os.chown(tmp,0,pwd.getpwnam("hoag-indexer").pw_gid)
os.chmod(tmp,0o640)
os.replace(tmp,p)
print("Multi-study selector enabled.")
PY
sudo systemctl restart hoag-dashboard.service
ok=0
for n in $(seq 1 15); do
  code="$(curl --max-time 3 -sS -o /dev/null -w '%{http_code}' \
      http://127.0.0.1:8080/ohif/dicomweb/studies 2>/dev/null || true)"
  if [ "$code" = 401 ]; then ok=1;break;fi
  sleep 1
done
if [ "$ok" -ne 1 ]; then echo "STOP: Unauthenticated 401 not confirmed; use rollback archive.";exit 1;fi
echo "Unauthenticated DICOMweb denied (401)."
for svc in hoag-dashboard hoag-worker hoag-mount hoag-sql nginx; do
  printf '%s: ' "$svc"; systemctl is-active "$svc"
done
echo "Multi-study OHIF activated for complete eligible indexed studies only."
