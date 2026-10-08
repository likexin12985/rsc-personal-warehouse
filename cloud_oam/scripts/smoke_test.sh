#!/usr/bin/env sh
set -eu

# Read-only smoke test. The target is mandatory so this script can never fall
# through to a historical production host.
: "${SMOKE_BASE_URL:?set SMOKE_BASE_URL to the reviewed HTTPS target}"
BASE_URL=${SMOKE_BASE_URL%/}
PRIVATE_PATH=${SMOKE_PRIVATE_PATH:-/xx/}
CURL_CONNECT_TIMEOUT=${SMOKE_CURL_CONNECT_TIMEOUT:-3}
CURL_MAX_TIME=${SMOKE_CURL_MAX_TIME:-10}
case "$PRIVATE_PATH" in
  /xx/) ;;
  *)
    printf '%s\n' 'SMOKE_PRIVATE_PATH must be exactly /xx/' >&2
    exit 2
    ;;
esac
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
    curl --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" \
      --resolve "${SMOKE_RESOLVE_HOST}:443:${SMOKE_RESOLVE_IP}" "$@"
  else
    curl --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" "$@"
  fi
}

HEALTH=$(curl_readonly -fsS "$BASE_URL/api/health")
HEALTH_LIVE=$(curl_readonly -fsS "$BASE_URL/api/health/live")
HEALTH_READY=$(curl_readonly -fsS "$BASE_URL/api/health/ready")
HEALTH_HEADERS=$(curl_readonly -fsS -D - -o /dev/null "$BASE_URL/api/health")
HEALTH_LIVE_HEADERS=$(curl_readonly -fsS -D - -o /dev/null "$BASE_URL/api/health/live")
HEALTH_READY_HEADERS=$(curl_readonly -fsS -D - -o /dev/null "$BASE_URL/api/health/ready")
PUBLIC_HOME=$(curl_readonly -fsS --max-filesize 262144 "$BASE_URL/")
PUBLIC_LOGIN_ALIAS=$(curl_readonly -fsS --max-filesize 262144 "$BASE_URL/login")
PRIVATE_HOME=$(curl_readonly -fsS --max-filesize 262144 "$BASE_URL$PRIVATE_PATH")
OPTIONS=$(curl_readonly -fsS "$BASE_URL/api/auth/login-options")
OPTIONS_HEADERS=$(curl_readonly -fsS -D - -o /dev/null "$BASE_URL/api/auth/login-options")
AUTH_GUARD_STATUS=$(curl_readonly -sS -o /dev/null -w '%{http_code}' \
  "$BASE_URL/api/auth/me")
AUTH_GUARD_HEADERS=$(curl_readonly -sS -D - -o /dev/null \
  "$BASE_URL/api/auth/me")
EXPECTED_RELEASE_SCOPE=${SMOKE_EXPECTED_RELEASE_SCOPE:-}

python3 - "$HEALTH" "$HEALTH_LIVE" "$HEALTH_READY" "$HEALTH_HEADERS" "$HEALTH_LIVE_HEADERS" "$HEALTH_READY_HEADERS" "$PUBLIC_HOME" "$PUBLIC_LOGIN_ALIAS" "$PRIVATE_HOME" "$OPTIONS" "$OPTIONS_HEADERS" "$AUTH_GUARD_STATUS" "$AUTH_GUARD_HEADERS" "$EXPECTED_RELEASE_SCOPE" <<'PY'
import json
import re
import sys

health, health_live, health_ready = (json.loads(value) for value in sys.argv[1:4])
health_headers, health_live_headers, health_ready_headers = sys.argv[4:7]
public_home, public_login_alias, private_home = sys.argv[7:10]
options = json.loads(sys.argv[10])
options_headers = sys.argv[11]
auth_guard_status = int(sys.argv[12])
auth_guard_headers = sys.argv[13]
expected_release_scope = sys.argv[14]

def public_entry(document):
    return (len(document) <= 262144
            and re.search(r'<title>\s*交流备件知识大全\s*</title>', document) is not None
            and re.search(r'<script\b[^>]*\bsrc="/assets/[^\"]+"', document) is not None
            and re.search(r'<title>\s*RSC个人仓\s*</title>', document) is None)

def private_entry(document):
    return (len(document) <= 262144
            and re.search(r'<title>\s*RSC个人仓\s*</title>', document) is not None
            and re.search(r'<script\b[^>]*\bsrc="/xx/assets/[^\"]+"', document) is not None)

assert health.get("ok") is True and health.get("status") == "ready", health
assert health_live.get("ok") is True and health_live.get("status") == "live", health_live
assert health_ready.get("ok") is True and health_ready.get("status") == "ready", health_ready
for headers in (health_headers, health_live_headers, health_ready_headers):
    assert "cache-control: no-store, max-age=0" in headers.lower(), headers
assert "cache-control: no-store, max-age=0" in options_headers.lower(), options_headers
assert "pragma: no-cache" in options_headers.lower(), options_headers
assert "referrer-policy: no-referrer" in options_headers.lower(), options_headers
assert "cache-control: private, no-store, max-age=0" in auth_guard_headers.lower(), auth_guard_headers
assert "pragma: no-cache" in auth_guard_headers.lower(), auth_guard_headers
assert "referrer-policy: no-referrer" in auth_guard_headers.lower(), auth_guard_headers
if expected_release_scope:
    for probe in (health, health_live, health_ready):
        assert probe.get("release_scope") == expected_release_scope, probe
assert public_entry(public_home), "public_home_not_knowledge_entry"
assert public_entry(public_login_alias), "login_alias_not_public_entry"
assert private_entry(private_home), "private_home_not_warehouse_entry"
assert options.get("password_enabled") is False, options
assert options.get("wechat_enabled") is False, options
assert options.get("sms_enabled") is True, options
assert auth_guard_status == 401, auth_guard_status
PY

# Read only the same-origin, single-segment public bundle named by the checked
# HTML. A successful HTML fallback for a missing asset must not pass release.
PUBLIC_BUNDLE_PATH=$(python3 - "$PUBLIC_HOME" <<'PY'
import re
import sys

match = re.search(r'<script\b[^>]*\bsrc="(/assets/[A-Za-z0-9._-]+\.js)"', sys.argv[1])
assert match is not None and len(match.group(1)) <= 160, "public_bundle_path_invalid"
print(match.group(1))
PY
)
curl_readonly -fsS --max-filesize 1048576 "$BASE_URL$PUBLIC_BUNDLE_PATH" | python3 -c '
import sys

bundle = sys.stdin.buffer.read(1048577)
required = (
    "资料待更新",
    "星星后台管理",
    "https://rscwz.cn/xx",
    "豫ICP备2026043964号-1",
    "https://beian.miit.gov.cn/",
)
forbidden = ("验证码登录", "/auth/me", "/auth/refresh", "/auth/sms", "cloud-oam-auth-refresh", "inventory-transactions")
assert 0 < len(bundle) <= 1048576 and all(value.encode() in bundle for value in required) and not any(value.encode() in bundle for value in forbidden), "public_bundle_invalid"
'

PRIVATE_BUNDLE_PATH=$(python3 - "$PRIVATE_HOME" <<'PY'
import re
import sys

match = re.search(r'<script\b[^>]*\bsrc="(/xx/assets/[A-Za-z0-9._-]+\.js)"', sys.argv[1])
assert match is not None and len(match.group(1)) <= 160, "private_bundle_path_invalid"
print(match.group(1))
PY
)
curl_readonly -fsS --max-filesize 2097152 "$BASE_URL$PRIVATE_BUNDLE_PATH" | python3 -c '
import sys

bundle = sys.stdin.buffer.read(2097153)
assert 0 < len(bundle) <= 2097152 and b"<html" not in bundle[:512].lower(), "private_bundle_invalid"
print("health_ok=true")
print("public_knowledge_entry_ok=true")
print("login_alias_public_ok=true")
print("private_warehouse_entry_ok=true")
print("password_login_disabled=true")
print("passwordless_login_ready=true")
print("auth_guard_ok=true")
print("public_bundle_ok=true")
print("private_bundle_ok=true")
'
