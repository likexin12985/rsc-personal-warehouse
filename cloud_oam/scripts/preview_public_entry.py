"""Run the real Caddy route layout on loopback using built assets and an auth stub.

Use only for local preview/testing. No production server or database is contacted.
"""
import argparse
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import re
import socket
import subprocess
import tempfile
from threading import Thread
import time
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]


class AuthStub(BaseHTTPRequestHandler):
    def do_GET(self):
        payload = b'{"detail":"Local preview: no live authentication service"}'
        self.send_response(503 if self.path == "/api/auth/login-options" else 401)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(payload)

    def log_message(self, *_):
        pass


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *_):
        return None


def check_routes(port, process):
    """Exercise real route behavior; the only API upstream is our loopback stub."""
    opener = build_opener(ProxyHandler({}), NoRedirect())

    def get(path):
        try:
            response = opener.open(f"http://127.0.0.1:{port}{path}", timeout=3)
        except HTTPError as error:
            response = error
        with response:
            return response.status, response.headers, response.read()

    deadline = time.monotonic() + 10
    while True:
        if process.poll() is not None:
            raise RuntimeError("Caddy exited before route checks")
        try:
            get("/")
            break
        except (URLError, TimeoutError):
            if time.monotonic() >= deadline:
                raise RuntimeError("Caddy did not start within 10 seconds")
            time.sleep(0.1)

    checked = []
    for path, title in [("/", "交流备件知识大全"), ("/login", "交流备件知识大全"),
                        ("/xx/", "RSC个人仓"), ("/xx/my-stock", "RSC个人仓")]:
        status, headers, body = get(path)
        assert status == 200 and f"<title>{title}</title>".encode() in body, path
        assert headers.get("Cache-Control") == "no-cache", path
        assert headers.get("X-Content-Type-Options") == "nosniff", path
        assert "frame-ancestors 'none'" in headers.get("Content-Security-Policy", ""), path
        checked.append(path)

    status, headers, _ = get("/xx")
    assert status == 308 and headers.get("Location") == "/xx/", "private redirect"
    checked.append("/xx")
    for folder in ["dist", "dist-warehouse"]:
        html = (ROOT / "frontend" / folder / "index.html").read_text()
        scripts = re.findall(r'<script[^>]+src="([^\"]+)"', html)
        assert scripts, folder
        for asset in scripts:
            status, headers, body = get(asset + "?route-check=1")
            assert status == 200 and "javascript" in headers.get("Content-Type", ""), asset
            assert b"<!doctype html>" not in body.lower(), asset
            checked.append(asset)

    for path in ["/assets/missing.js", "/brand/missing.png", "/xx/assets/missing.js"]:
        status, _, _ = get(path)
        assert status == 404, f"missing resource must return 404: {path}"
        checked.append(path)
    for path in ["/sw.js", "/xx/sw.js"]:
        status, headers, _ = get(path)
        assert status == 200 and headers.get("Cache-Control") == "no-cache", path
        checked.append(path)

    status, _, body = get("/xx/manifest.webmanifest")
    assert status == 200 and json.loads(body)["scope"] == "/xx/", "private manifest"
    checked.append("/xx/manifest.webmanifest")
    status, headers, body = get("/knowledge-catalog.json")
    assert status == 200 and "json" in headers.get("Content-Type", ""), "public catalog type"
    assert body == (ROOT / "frontend/dist/knowledge-catalog.json").read_bytes(), "catalog artifact"
    checked.append("/knowledge-catalog.json")
    for path, expected in [("/api/auth/me", 401), ("/api/auth/login-options", 503)]:
        status, headers, body = get(path)
        assert status == expected and "detail" in json.loads(body), path
        assert headers.get("Cache-Control") == "no-store", "preserve upstream auth cache policy"
        checked.append(path)
    return checked


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--caddy", required=True)
    parser.add_argument("--port", type=int, default=18085)
    parser.add_argument("--check", action="store_true", help="verify local routes and stop; use --port 0 for a free port")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("port must be between 0 and 65535")
    if args.port == 0:
        with socket.socket() as port_socket:
            port_socket.bind(("127.0.0.1", 0))
            args.port = port_socket.getsockname()[1]
    server = ThreadingHTTPServer(("127.0.0.1", 0), AuthStub)
    Thread(target=server.serve_forever, daemon=True).start()
    source = (ROOT / "frontend/Caddyfile").read_text()
    body = re.split(r"^\{\$APP_DOMAIN:localhost\} \{\n", source, flags=re.M)[1]
    body = body.replace("reverse_proxy api:8000", f"reverse_proxy 127.0.0.1:{server.server_port}")
    body = body.replace("/srv/public", '"' + str(ROOT / "frontend/dist") + '"')
    body = body.replace("/srv/warehouse", '"' + str(ROOT / "frontend/dist-warehouse") + '"')
    config = "{\n admin off\n persist_config off\n auto_https off\n}\n" + f"http://127.0.0.1:{args.port} {{\n bind 127.0.0.1\n" + body
    try:
        with tempfile.TemporaryDirectory(prefix="rsc-public-preview-") as directory:
            path = Path(directory) / "Caddyfile"
            path.write_text(config)
            environment = dict(os.environ, XDG_CONFIG_HOME=directory, XDG_DATA_HOME=directory)
            if args.check:
                with (Path(directory) / "caddy.log").open("w+") as log:
                    subprocess.run([args.caddy, "validate", "--config", str(path)], check=True,
                                   env=environment, stdout=log, stderr=log)
                    process = subprocess.Popen([args.caddy, "run", "--config", str(path)],
                                               env=environment, stdout=log, stderr=log)
                    try:
                        paths = check_routes(args.port, process)
                        version = subprocess.check_output([args.caddy, "version"], text=True).strip()
                        print(json.dumps({"check": "local-caddy-routes", "status": "passed",
                                          "caddyVersion": version, "paths": paths,
                                          "api": "loopback stub only; real auth, TLS and production remain unverified"}))
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=5)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
            else:
                subprocess.run([args.caddy, "validate", "--config", str(path)], check=True, env=environment)
                print(f"Preview: http://127.0.0.1:{args.port}/ (private entry: /xx/; stub auth only)", flush=True)
                subprocess.run([args.caddy, "run", "--config", str(path)], check=True, env=environment)
    finally:
        server.shutdown()
        server.server_close()


if __name__ == "__main__":
    main()
