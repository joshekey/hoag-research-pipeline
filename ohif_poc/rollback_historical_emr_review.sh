#!/usr/bin/env bash
# Roll back ONLY the review UI using the backup path provided as argument.
set -euo pipefail
if [ "$#" -ne 1 ] || [[ "$1" != /var/lib/hoag-research/backups/pre-historical-emr-review-*.tar.gz ]]; then
  echo "Usage: $0 /var/lib/hoag-research/backups/pre-historical-emr-review-YYYYMMDD-HHMMSS.tar.gz"
  exit 2
fi
BACKUP="$1"
sudo test -f "$BACKUP"
echo "=== DISABLE HISTORICAL REPORT REVIEW GATE ==="
if sudo test -e /etc/hoag-research/historical-emr-review.enabled; then
  sudo rm -f /etc/hoag-research/historical-emr-review.enabled
fi
echo "=== RESTORE PREVIOUS DASHBOARD ==="
sudo tar -xzf "$BACKUP" -C /opt/hoag-research
sudo systemctl restart hoag-dashboard.service
echo "=== SERVICES ==="
for svc in hoag-dashboard hoag-worker hoag-mount hoag-sql nginx; do
  printf "%s: " "$svc"; systemctl is-active "$svc"
done
echo "Historical review gate disabled. Other clinical gateways unchanged."
