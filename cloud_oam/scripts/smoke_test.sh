#!/usr/bin/env sh
set -eu

# Read-only smoke test. The target is mandatory so this script can never fall
# through to a historical production host.
: "${SMOKE_BASE_URL:?set SMOKE_BASE_URL to the reviewed HTTPS target}"
BASE_URL=${SMOKE_BASE_URL%/}
case "$BASE_URL" in
  https://*) ;;
  http://127.0.0.1:*|http://localhost:*) ;;
  *)
    printf '%s\n' 'SMOKE_BASE_URL must use HTTPS or an explicit loopback target' >&2
    exit 2
    ;;
esac

curl_readonly() {
  if [ -n "${SMOKE_RESOLVE_HOST:-}" ] && [ -n "${SMOKE_RESOLVE_IP:-}" ]; then
    curl --resolve "${SMOKE_RESOLVE_HOST}:443:${SMOKE_RESOLVE_IP}" "$@"
  else
    curl "$@"
  fi
}

HEALTH=$(curl_readonly -fsS "$BASE_URL/api/health")
WEB_STATUS=$(curl_readonly -sS -o /dev/null -w '%{http_code}' "$BASE_URL/")
OPTIONS=$(curl_readonly -fsS "$BASE_URL/api/auth/login-options")
AUTH_GUARD_STATUS=$(curl_readonly -sS -o /dev/null -w '%{http_code}' \
  "$BASE_URL/api/auth/me")

python3 - "$HEALTH" "$WEB_STATUS" "$OPTIONS" "$AUTH_GUARD_STATUS" <<'PY'
import json
import sys

health = json.loads(sys.argv[1])
web_status = int(sys.argv[2])
options = json.loads(sys.argv[3])
auth_guard_status = int(sys.argv[4])

assert health.get("ok") is True, health
assert web_status == 200, web_status
assert options.get("password_enabled") is False, options
assert options.get("sms_enabled") is True or options.get("wechat_enabled") is True, options
assert auth_guard_status == 401, auth_guard_status
print("health_ok=true")
print("web_ok=true")
print("password_login_disabled=true")
print("passwordless_login_ready=true")
print("auth_guard_ok=true")
PY
