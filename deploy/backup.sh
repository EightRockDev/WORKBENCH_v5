#!/usr/bin/env bash
# ===========================================================================
# Eight Rock Workbench v5.0 — nightly database backup (Section 9.1)
# ---------------------------------------------------------------------------
# A pg_dump writes ONE closed file, then it is safe to sync to OneDrive as a
# backup target (Section 9.2: a closed dump file is safe to sync; the LIVE
# database must never live in a synced folder).
#
# Schedule (crontab -e, as root):
#   15 2 * * *  /opt/8rw/deploy/backup.sh >> /var/log/8rw/backup.log 2>&1
# ===========================================================================
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/8rw}"
DB_NAME="${DB_NAME:-workbench}"
LOCAL_BACKUP_DIR="${LOCAL_BACKUP_DIR:-/backup/8rw}"          # a SECOND local disk
ONEDRIVE_BACKUP_DIR="${ONEDRIVE_BACKUP_DIR:-}"               # optional synced target
RETAIN_DAYS="${RETAIN_DAYS:-30}"

STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="${LOCAL_BACKUP_DIR}/workbench-${STAMP}.dump"
mkdir -p "$LOCAL_BACKUP_DIR"

echo "[$(date -Is)] dumping ${DB_NAME} -> ${OUT}"
# Custom format (-Fc): compressed, restorable with pg_restore.
sudo -u postgres pg_dump -Fc "$DB_NAME" > "$OUT"

# Only after the dump file is fully written and closed do we copy it to OneDrive.
if [ -n "$ONEDRIVE_BACKUP_DIR" ]; then
    mkdir -p "$ONEDRIVE_BACKUP_DIR"
    cp "$OUT" "$ONEDRIVE_BACKUP_DIR/"
    echo "[$(date -Is)] synced closed dump to ${ONEDRIVE_BACKUP_DIR}"
fi

# Retention
find "$LOCAL_BACKUP_DIR" -name 'workbench-*.dump' -mtime +"$RETAIN_DAYS" -delete
echo "[$(date -Is)] backup complete; pruned dumps older than ${RETAIN_DAYS}d"
