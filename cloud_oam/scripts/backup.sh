#!/usr/bin/env sh
set -eu
umask 077
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -f "$ROOT_DIR/.env" ]; then
  set -a
  . "$ROOT_DIR/.env"
  set +a
fi
: "${OAM_DB_BACKUP_PASSWORD:?read-only database backup password required}"
: "${POSTGRES_DB:?database required}"
: "${RSC_OSS_BACKUP_REGION:?dedicated OSS backup region required}"
: "${RSC_OSS_BACKUP_BUCKET:?dedicated OSS backup bucket required}"
: "${RSC_BACKUP_MAXIMUM_BYTES:?explicit backup capacity budget required}"
BACKUP_DIR=${BACKUP_DIR:-/opt/star-oam/backups}
# Bind mount sources cannot contain ':' or a newline.
case "$BACKUP_DIR" in *:*|*'
'*) exit 64;; esac
case "$RSC_BACKUP_MAXIMUM_BYTES" in ''|*[!0-9]*) exit 64;; esac
. "$ROOT_DIR/deployment/backup/capacity.sh"
capacity_validate
mkdir -p "$BACKUP_DIR"
BACKUP_DIR=$(CDPATH= cd -- "$BACKUP_DIR" && pwd)
capacity_prepare "$BACKUP_DIR" 8

: "${RSC_BACKUP_TIMEOUT_SECONDS:?explicit total backup deadline required}"
case "$RSC_BACKUP_TIMEOUT_SECONDS" in ''|0*|*[!0-9]*) exit 64;; esac
[ "${#RSC_BACKUP_TIMEOUT_SECONDS}" -le 5 ] && [ "$RSC_BACKUP_TIMEOUT_SECONDS" -le 86400 ] || exit 64
# Host Python is an explicit deployment prerequisite. Do not use a local venv.
python3 -c 'import sys; assert sys.version_info >= (3, 11)'
exec python3 "$ROOT_DIR/deployment/backup-worker/deadline_runner.py" \
  --seconds "$RSC_BACKUP_TIMEOUT_SECONDS" --grace 6 --temp-parent "$BACKUP_DIR" \
  -- /bin/sh "$ROOT_DIR/scripts/backup_steps.sh"
