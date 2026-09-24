#!/usr/bin/env sh
set -eu
ROOT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
command -v python3 >/dev/null 2>&1 || {
  printf '%s\n' 'pilot-deploy: python3_required' >&2
  exit 2
}
exec python3 "$ROOT_DIR/scripts/pilot_release.py" "$@"
