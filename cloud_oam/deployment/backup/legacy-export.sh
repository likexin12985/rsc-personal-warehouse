#!/bin/sh
set -eu
umask 077
[ "$#" -eq 0 ] || exit 64
RSC_BACKUP_LIB=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$RSC_BACKUP_LIB/capacity.sh"
capacity_prepare "${TMPDIR:-/tmp}" 2
staging=$(mktemp -d)
trap 'rm -rf -- "$staging"' EXIT
trap 'exit 129' HUP
trap 'exit 130' INT
trap 'exit 143' TERM
capacity_run tar -czf "$staging/uploads.tar.gz" -C /data/uploads .
test -s "$staging/uploads.tar.gz"
cat "$staging/uploads.tar.gz"
