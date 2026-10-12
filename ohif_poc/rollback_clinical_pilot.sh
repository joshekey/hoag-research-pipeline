#!/usr/bin/env bash
# Roll back only OHIF clinical pilot; never modify source mounts or export data.
set -euo pipefail
if [ "$#" -ne 1 ]; then
  echo "Usage: rollback_clinical_pilot.sh /var/lib/hoag-research/backups/pre-ohif-clinical-TIMESTAMP.tar.gz"
  exit 1
fi
ARCHIVE="$1"
case "$ARCHIVE" in
  /var/lib/hoag-research/backups/pre-ohif-clinical-*.tar.gz) ;;
  *) echo "STOP: Unexpected backup path"; exit 1 ;;
esac
sudo test -f "$ARCHIVE"
echo "=== DISABLE PILOT CLINICAL IMAGE ACCESS ==="
sudo rm -f /etc/hoag-research/ohif-pilot-study.uid
echo "=== RESTORE APPLICATION CODE AND UI ==="
sudo tar -xzf "$ARCHIVE" -C /opt/hoag-research \
  webapp.py static/app.js templates/index.html
sudo /opt/hoag-research/venv/bin/python -m py_compile /opt/hoag-research/webapp.py
echo "=== RESTART DASHBOARD ONLY ==="
sudo systemctl restart hoag-dashboard.service
sudo systemctl is-active hoag-dashboard.service
echo "=== VERIFY NO CLINICAL GATEWAY ==="
code="$(curl -sS -o /dev/null -w '%{http_code}' http://127.0.0.1:8080/ohif/dicomweb/studies)"
echo "Unauthenticated gateway status: $code (401 from the existing HOAG login is expected)"
echo "Pilot access disabled; research worker, SQL and original images unchanged."
