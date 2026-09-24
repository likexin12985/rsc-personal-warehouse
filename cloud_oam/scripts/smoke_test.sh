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
  /*) ;;
  *)
    printf '%s\n' 'SMOKE_PRIVATE_PATH must begin with /' >&2
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
PUBLIC_HOME=$(curl_readonly -fsS --max-filesize 262144 "$BASE_URL/")
PUBLIC_LOGIN_ALIAS=$(curl_readonly -fsS --max-filesize 262144 "$BASE_URL/login")
PRIVATE_HOME=$(curl_readonly -fsS --max-filesize 262144 "$BASE_URL$PRIVATE_PATH")
OPTIONS=$(curl_readonly -fsS "$BASE_URL/api/auth/login-options")
AUTH_GUARD_STATUS=$(curl_readonly -sS -o /dev/null -w '%{http_code}' \
  "$BASE_URL/api/auth/me")

python3 - "$HEALTH" "$PUBLIC_HOME" "$PUBLIC_LOGIN_ALIAS" "$PRIVATE_HOME" "$OPTIONS" "$AUTH_GUARD_STATUS" <<'PY'
import json
import re
import sys

health = json.loads(sys.argv[1])
public_home, public_login_alias, private_home = sys.argv[2:5]
options = json.loads(sys.argv[5])
auth_guard_status = int(sys.argv[6])

def public_entry(document):
    return (len(document) <= 262144
            and re.search(r'<title>\s*交流备件知识大全\s*</title>', document) is not None
            and re.search(r'<script\b[^>]*\bsrc="/assets/[^\"]+"', document) is not None
            and re.search(r'<title>\s*RSC个人仓\s*</title>', document) is None)

def private_entry(document):
    return (len(document) <= 262144
            and re.search(r'<title>\s*RSC个人仓\s*</title>', document) is not None
            and re.search(r'<script\b[^>]*\bsrc="/xx/assets/[^\"]+"', document) is not None)

assert health.get("ok") is True, health
assert public_entry(public_home), "public_home_not_knowledge_entry"
assert public_entry(public_login_alias), "login_alias_not_public_entry"
assert private_entry(private_home), "private_home_not_warehouse_entry"
assert options.get("password_enabled") is False, options
assert options.get("sms_enabled") is True or options.get("wechat_enabled") is True, options
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
required = ("资料待更新", "星星后台管理", "豫ICP备2026043964号-1", "https://beian.miit.gov.cn/")
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
