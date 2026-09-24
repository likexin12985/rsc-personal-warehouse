#!/bin/sh
set -eu
case "${RSC_BACKUP_SNAPSHOT:-}" in ''|*[!0-9a-fA-F-]*) exit 64;; esac
[ "${#RSC_BACKUP_SNAPSHOT}" -le 100 ] || exit 64
umask 077
. "$RSC_BACKUP_LIB/capacity.sh"
capacity_prepare "${TMPDIR:-/tmp}" 3
staging=$(mktemp -d)
trap 'rm -rf -- "$staging"' EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
PGAPPNAME="${RSC_BACKUP_JOB}_catalog"
export PGAPPNAME
capacity_run psql -X -w -q -A -t --set=ON_ERROR_STOP=1 --set="exported_snapshot=$RSC_BACKUP_SNAPSHOT" \
 --file "$RSC_BACKUP_LIB/catalog.sql" > "$staging/files.ndjson"
PGAPPNAME="${RSC_BACKUP_JOB}_dump"
export PGAPPNAME
capacity_run pg_dump -w -U star_oam_backup --dbname "$PGDATABASE" --enable-row-security \
 --snapshot="$RSC_BACKUP_SNAPSHOT" --lock-wait-timeout=1500ms > "$staging/database.sql"
printf '{"schema":"cloud_oam.database_object_snapshot_candidate.v1","snapshot_id":"%s"}\n' \
 "$RSC_BACKUP_SNAPSHOT" > "$staging/snapshot.json"
tar -cf - -C "$staging" database.sql files.ndjson snapshot.json
