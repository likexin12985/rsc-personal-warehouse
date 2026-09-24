#!/bin/sh
set -eu
umask 077
[ "$#" -eq 0 ] || exit 64
: "${POSTGRES_DB:?POSTGRES_DB is required}"
# libpq treats a dbname containing '=' or a URI as connection parameters.
# Accept the project's plain ASCII database identifiers before calling any tool.
case "$POSTGRES_DB" in *[!0-9A-Za-z_]*) printf '%s\n' 'RSC_BACKUP_DATABASE_NAME_REFUSED' >&2; exit 64;; esac
[ "${#POSTGRES_DB}" -le 63 ] || exit 64
# The production command runs inside db. Only a local Unix socket may be selected.
case "${PGHOST:-}" in
 *','*|*[[:space:]]*) printf '%s\n' 'RSC_BACKUP_SINGLE_LOCAL_SOCKET_REQUIRED' >&2; exit 64;;
 ''|/*) ;;
 *) printf '%s\n' 'RSC_BACKUP_LOCAL_SOCKET_REQUIRED' >&2; exit 64;;
esac
unset PGSERVICE PGSERVICEFILE PGOPTIONS PGHOSTADDR PGREQUIRESSL PGREQUIREPEER
# Detect a closed client while a long query is still running. This is scoped
# to the backup clients, not a database-wide setting or a caller PGOPTIONS.
PGOPTIONS='-c client_connection_check_interval=1000'
export PGOPTIONS
RSC_BACKUP_LIB=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$RSC_BACKUP_LIB/capacity.sh"
capacity_prepare "${TMPDIR:-/tmp}" 3
RSC_BACKUP_JOB="rsc_backup_$$"
PGDATABASE=$POSTGRES_DB
PGUSER=star_oam_backup
PGPORT=5432
PGCONNECT_TIMEOUT=5
PGAPPNAME="${RSC_BACKUP_JOB}_lock"
export RSC_BACKUP_LIB RSC_BACKUP_JOB PGDATABASE PGUSER PGPORT PGCONNECT_TIMEOUT PGAPPNAME
case "$(psql --version)" in 'psql (PostgreSQL) 16.'*) ;; *) exit 64;; esac
case "$(pg_dump --version)" in 'pg_dump (PostgreSQL) 16.'*) ;; *) exit 64;; esac
exec psql -X -w -q --set=ON_ERROR_STOP=1 --file "$RSC_BACKUP_LIB/lock.sql"
