"""The live pilot smoke must distinguish the public knowledge entry from the old site."""

from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import subprocess
import threading


SCRIPT = Path(__file__).resolve().parents[2] / "scripts/smoke_test.sh"
PUBLIC = '<!doctype html><html><head><title>交流备件知识大全</title><script src="/assets/public.js"></script></head></html>'
PRIVATE = '<!doctype html><html><head><title>RSC个人仓</title><script src="/xx/assets/private.js"></script></head></html>'
OLD = '<!doctype html><html><head><title>RSC个人仓</title><script src="/assets/old.js"></script></head></html>'
PUBLIC_BUNDLE = '资料待更新 星星后台管理 豫ICP备2026043964号-1 https://beian.miit.gov.cn/'


@contextmanager
def website(overrides=None):
    bodies = {
        "/api/health": (200, json.dumps({"ok": True})),
        "/api/auth/login-options": (200, json.dumps({"password_enabled": False, "sms_enabled": True})),
        "/api/auth/me": (401, json.dumps({"detail": "authentication required"})),
        "/": (200, PUBLIC),
        "/login": (200, PUBLIC),
        "/xx/": (200, PRIVATE),
        "/assets/public.js": (200, PUBLIC_BUNDLE),
        "/xx/assets/private.js": (200, "warehouse client bundle"),
    }
    bodies.update(overrides or {})

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            status, body = bodies.get(self.path, (404, "missing"))
            payload = body.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json" if self.path.startswith("/api/") else "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def smoke(url):
    return subprocess.run(
        ["sh", str(SCRIPT)],
        env={"PATH": "/usr/bin:/bin", "SMOKE_BASE_URL": url, "SMOKE_PRIVATE_PATH": "/xx/"},
        capture_output=True, text=True, timeout=20,
    )


def test_public_knowledge_and_private_warehouse_routes_pass():
    with website() as url:
        result = smoke(url)
    assert result.returncode == 0, result.stderr
    assert "public_knowledge_entry_ok=true" in result.stdout
    assert "login_alias_public_ok=true" in result.stdout
    assert "private_warehouse_entry_ok=true" in result.stdout
    assert "public_bundle_ok=true" in result.stdout
    assert "private_bundle_ok=true" in result.stdout


def test_old_homepage_cannot_pass_with_http_200():
    with website({"/": (200, OLD)}) as url:
        result = smoke(url)
    assert result.returncode != 0
    assert "public_home_not_knowledge_entry" in result.stderr


def test_login_alias_must_remain_public():
    with website({"/login": (200, PRIVATE)}) as url:
        result = smoke(url)
    assert result.returncode != 0
    assert "login_alias_not_public_entry" in result.stderr


def test_private_path_cannot_return_public_entry():
    with website({"/xx/": (200, PUBLIC)}) as url:
        result = smoke(url)
    assert result.returncode != 0
    assert "private_home_not_warehouse_entry" in result.stderr


def test_public_html_with_old_login_bundle_cannot_pass():
    with website({"/assets/public.js": (200, PUBLIC_BUNDLE + " /auth/sms 验证码登录")}) as url:
        result = smoke(url)
    assert result.returncode != 0
    assert "public_bundle_invalid" in result.stderr
    assert "public_bundle_ok=true" not in result.stdout


def test_public_bundle_must_display_filing_and_official_link():
    with website({"/assets/public.js": (200, "资料待更新 星星后台管理")}) as url:
        result = smoke(url)
    assert result.returncode != 0
    assert "public_bundle_invalid" in result.stderr


def test_public_bundle_html_fallback_cannot_pass():
    with website({"/assets/public.js": (200, PUBLIC)}) as url:
        result = smoke(url)
    assert result.returncode != 0
    assert "public_bundle_invalid" in result.stderr


def test_private_bundle_html_fallback_cannot_pass():
    with website({"/xx/assets/private.js": (200, PRIVATE)}) as url:
        result = smoke(url)
    assert result.returncode != 0
    assert "private_bundle_invalid" in result.stderr
