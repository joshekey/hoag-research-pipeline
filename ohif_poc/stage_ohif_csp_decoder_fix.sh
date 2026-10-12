#!/usr/bin/env bash
# Fix OHIF libjpeg-turbo worker CSP only. Does not alter sources or authorization.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
APP=/opt/hoag-research
STAMP="$(date +%Y%m%d-%H%M%S)"
cd "$ROOT"

echo "=== VALIDATE VIEWER INLINE BOOTSTRAP HASHES ==="
# Verify hashes against installed OHIF 3.9.2 index rather than permitting
# arbitrary inline JS. No patient data is involved in this asset check.
sudo /opt/hoag-research/venv/bin/python - <<'PY'
import hashlib
import re
from pathlib import Path

path=Path("/opt/hoag-research/ohif-assets/index.html")
if not path.is_file():
    raise SystemExit("STOP: OHIF index.html not installed")
source=path.read_bytes()
found=set()
for match in re.finditer(rb"<script\b[^>]*>(.*?)</script\s*>",source,re.I|re.S):
    body=match.group(1)
    if not body.strip():
        continue
    import base64
    found.add("sha256-"+base64.b64encode(hashlib.sha256(body).digest()).decode("ascii"))
expected={
    "sha256-RUJKPsAHcLA3YS8dWuPEiqEPTlkfTEHKow0zXWj7MBE=",
    "sha256-YGQALmFLH0ots68+CN8MF4DamHt77VywRO0d1cRTm3Y=",
}
if not expected.issubset(found):
    print("STOP: Bundled OHIF inline script hashes differ from verified browser log.")
    print("Expected scripts present:",len(expected.intersection(found)),"of",len(expected))
    raise SystemExit(1)
print("Verified OHIF bootstrap inline script hashes:",len(expected))
PY

echo "=== RUN ALL SYNTHETIC REGRESSION TESTS ==="
/opt/hoag-research/venv/bin/python -m unittest discover -s tests -q
/opt/hoag-research/venv/bin/python -m py_compile webapp.py

echo "=== BACKUP PRODUCTION DASHBOARD ==="
BACKUP="/var/lib/hoag-research/backups/pre-ohif-csp-$STAMP.tar.gz"
sudo tar -czf "$BACKUP" -C "$APP" webapp.py
sudo chmod 0600 "$BACKUP"
echo "Rollback backup: $BACKUP"

echo "=== INSTALL CSP FIX ONLY ==="
sudo install -o root -g root -m 0644 webapp.py "$APP/webapp.py"
sudo systemctl restart hoag-dashboard.service

echo "=== VERIFY UNAUTHENTICATED ACCESS ==="
pass=0
for n in $(seq 1 20); do
    code="$(curl -s -m 3 -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/api/studies || true)"
    if [ "$code" = "401" ]; then pass=1; break; fi
    sleep 1
done
if [ "$pass" != 1 ]; then
    echo "STOP: expected HTTP 401 from unauthenticated study catalog"
    exit 1
fi
echo "Unauthenticated study catalog: HTTP 401"

for svc in hoag-dashboard hoag-worker hoag-mount hoag-sql nginx; do
    printf '%s: ' "$svc"
    systemctl is-active "$svc"
done
echo "Staged OHIF-specific CSP repair. Historical gate, data, exports unchanged."
