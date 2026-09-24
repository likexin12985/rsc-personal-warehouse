#!/bin/sh
set -eu
case "${RSC_BACKUP_LOCK_HASH:-}" in ''|*[!0-9a-f]*) exit 64;; esac
[ "${#RSC_BACKUP_LOCK_HASH}" -eq 64 ] || exit 64
PGAPPNAME="${RSC_BACKUP_JOB}_snapshot"
export PGAPPNAME
exec psql -X -w -q --set=ON_ERROR_STOP=1 --set="locked_inventory=$RSC_BACKUP_LOCK_HASH" --file "$RSC_BACKUP_LIB/snapshot.sql"
