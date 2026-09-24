#!/usr/bin/env sh
set -eu
umask 077
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
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
# TMPDIR is owned by the outer supervisor, on the backup filesystem.
: "${TMPDIR:?supervised scratch required}"
: "${RSC_BACKUP_DEADLINE_MONOTONIC:?total deadline required}"
TMP_DIR=$(mktemp -d "$TMPDIR/.star-oam-joint.XXXXXX")
JOB_DIR="$TMP_DIR/job"
mkdir "$JOB_DIR"
STAMP=$(date +%Y%m%d_%H%M%S)_$$
FINAL="$BACKUP_DIR/backup_$STAMP.tar"
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
cd "$ROOT_DIR"
SUPERVISOR=/opt/rsc-supervisor/deadline_runner.py
SENDER="$ROOT_DIR/deployment/backup-worker/lease_sender.py"
remaining_seconds() {
  python3 -c 'import os,time; left=float(os.environ["RSC_BACKUP_DEADLINE_MONOTONIC"])-time.monotonic(); assert left>0; print(left)'
}
worker_run() {
  remaining=$(remaining_seconds)
  python3 "$SENDER" -- docker compose run --rm --no-deps -T --user "$(id -u):$(id -g)" \
    --volume "$JOB_DIR:/job:rw" --entrypoint python object-backup \
    /opt/rsc-backup-worker/deadline_runner.py --seconds "$remaining" --grace 1 --require-lease --lease-seconds 2 \
    -- python /opt/rsc-backup-worker/backup_worker.py \
    --job-dir /job --maximum-bytes "$RSC_BACKUP_MAXIMUM_BYTES" \
    --region "$RSC_OSS_BACKUP_REGION" --bucket "$RSC_OSS_BACKUP_BUCKET" "$@"
}
worker_run --preflight > "$TMP_DIR/worker-preflight.log"
remaining=$(remaining_seconds)
PGPASSWORD="$OAM_DB_BACKUP_PASSWORD" POSTGRES_DB="$POSTGRES_DB" \
  RSC_BACKUP_MAXIMUM_BYTES="$RSC_BACKUP_MAXIMUM_BYTES" capacity_run \
  python3 "$SENDER" -- docker compose exec -T -e PGPASSWORD -e POSTGRES_DB -e RSC_BACKUP_MAXIMUM_BYTES \
  db python3 "$SUPERVISOR" --seconds "$remaining" --grace 1 --require-lease --lease-seconds 2 \
  -- sh /opt/rsc-backup/backup_database.sh > "$JOB_DIR/snapshot-export.tar"
test -s "$JOB_DIR/snapshot-export.tar"
remaining=$(remaining_seconds)
RSC_BACKUP_MAXIMUM_BYTES="$RSC_BACKUP_MAXIMUM_BYTES" capacity_run \
  python3 "$SENDER" -- docker compose exec -T -e RSC_BACKUP_MAXIMUM_BYTES api python3 "$SUPERVISOR" \
  --seconds "$remaining" --grace 1 --require-lease --lease-seconds 2 \
  -- sh /opt/rsc-backup/legacy-export.sh > "$JOB_DIR/uploads.tar.gz"
test -s "$JOB_DIR/uploads.tar.gz"
worker_run > "$TMP_DIR/worker-result.log"
test -f "$JOB_DIR/backup-complete.json"
test -s "$JOB_DIR/verified-joint.tar"
test "$(wc -l < "$JOB_DIR/verified-joint.sha256" | tr -d ' ')" -eq 1
grep -Eq '^[0-9a-f]{64}  verified-joint.tar$' "$JOB_DIR/verified-joint.sha256"
(
  cd "$JOB_DIR"
  sha256sum -c verified-joint.sha256 >/dev/null
)
# Only a successful, independently checksum-verified worker output is published.
# An exclusive hard link cannot replace an earlier or competing backup.
remaining_seconds >/dev/null
ln "$JOB_DIR/verified-joint.tar" "$FINAL"
if ! find "$BACKUP_DIR" -maxdepth 1 -type f -name 'backup_*.tar' -mtime +14 -delete; then
  printf '%s\n' 'backup-retention: cleanup failed after verified publication' >&2
fi
printf 'backup=%s\n' "$FINAL"
