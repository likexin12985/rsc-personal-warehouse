"""Explicit synthetic-only OpenBao experiment; never starts a production service.

No dependency beyond Python's standard library. Tokens/shares and snapshots stay
in memory. Only the encrypted Raft store exists in a new 0700 temporary directory,
which is removed after the child process exits. This is not a provider or deployer.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import importlib
import json
import os
from pathlib import Path
import platform
import re
import socket
import stat
import subprocess
import tempfile
import time

VERSION = "2.7.1"
KEYS = ("rsc-authentication-idempotency", "rsc-material-request-contact")


def require(condition, step):
    if not condition:
        # Deliberately exclude actual response bodies, tokens and data.
        raise RuntimeError("isolated OpenBao check failed: " + step)


class UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=8):
        super().__init__("localhost", timeout=timeout)
        self.path = str(path)

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class UnixClient:
    def __init__(self, path):
        self.path = path

    def request(self, method, path, body=None, token=None, timeout_seconds=8):
        require(path.startswith("/v1/") and "\n" not in path, "local API path")
        headers = {}
        if token:
            headers["X-Vault-Token"] = token
        if isinstance(body, bytes):
            payload = body
            headers["Content-Type"] = "application/octet-stream"
        elif body is not None:
            payload = json.dumps(body, separators=(",", ":")).encode()
            headers["Content-Type"] = "application/json"
        else:
            payload = None
        require(0 < timeout_seconds <= 45, "bounded operation timeout")
        conn = UnixConnection(self.path, timeout=timeout_seconds)
        try:
            conn.request(method, path, body=payload, headers=headers)
            response = conn.getresponse()
            content = response.read(16 * 1024 * 1024 + 1)
            require(len(content) <= 16 * 1024 * 1024, "bounded response")
            if content and "json" in response.getheader("Content-Type", ""):
                content = json.loads(content)
            return response.status, content
        except TimeoutError:
            raise RuntimeError("isolated OpenBao check failed: timed out " + method + " " + path) from None
        finally:
            conn.close()


class LocalBao:
    """One disposable non-dev, Shamir sealed Raft node, local UDS API only."""

    def __init__(self, binary, expected_sha256=None):
        self.binary = Path(binary).resolve(strict=True)
        require(self.binary.is_file(), "binary file")
        self.binary_sha256 = hashlib.sha256(self.binary.read_bytes()).hexdigest()
        lock = json.loads(Path(__file__).with_name("official_release_2.7.1.json").read_text())
        runtime = (platform.system().lower(), platform.machine().lower())
        names = {("darwin", "arm64"): "darwin_arm64", ("linux", "x86_64"): "linux_amd64"}
        require(runtime in names, "supported runtime platform")
        locked = lock["artifacts"][names[runtime]]["binary_sha256"]
        require(self.binary_sha256 == locked, "signed release binary digest")
        if expected_sha256 is not None:
            require(self.binary_sha256 == expected_sha256, "caller binary digest")
        self.root_token = None
        self.shares = []
        self.process = None
        self.temporary = None
        self.socket_path = None
        self.cluster_port = None
        self.metrics_port = None

    def __enter__(self):
        try:
            self.start()
            self.initialize()
            return self
        except BaseException:
            self.close()
            raise

    def __exit__(self, *_):
        self.close()

    def start(self):
        # Use /tmp to stay under macOS's short Unix socket pathname limit.
        self.temporary = tempfile.TemporaryDirectory(prefix="rsc-bao-", dir="/tmp")
        self.directory = Path(self.temporary.name)
        self.directory.chmod(0o700)
        self.socket_path = self.directory / "api.sock"
        (self.directory / "raft").mkdir(mode=0o700)
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.cluster_port = probe.getsockname()[1]
            with socket.socket() as other:
                other.bind(("127.0.0.1", 0))
                self.metrics_port = other.getsockname()[1]
        config = {
            "ui": False, "raw_storage_endpoint": False, "log_level": "error",
            "api_addr": "http://127.0.0.1:1",
            "cluster_addr": "https://127.0.0.1:" + str(self.cluster_port),
            "storage": {"raft": {"path": str(self.directory / "raft"), "node_id": "isolated-node"}},
            "listener": [{"unix": {
                "address": str(self.socket_path), "socket_mode": "0600",
                "socket_user": str(os.getuid()), "socket_group": str(os.getgid()),
            }}, {"tcp": {
                "address": "127.0.0.1:" + str(self.metrics_port),
                "cluster_address": "127.0.0.1:" + str(self.cluster_port),
                "tls_disable": True,
                "telemetry": {"metrics_only": True, "unauthenticated_metrics_access": False},
            }}],
        }
        config_path = self.directory / "config.json"
        config_path.write_text(json.dumps(config))
        config_path.chmod(0o600)
        self.config = config
        # Explicit clean environment: no inherited cloud credentials/BAO tokens.
        env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
               "TMPDIR": str(self.directory), "GOMAXPROCS": "2"}
        self.process = subprocess.Popen(
            [str(self.binary), "server", "-config=" + str(config_path)],
            env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL, start_new_session=True,
        )
        self.client = UnixClient(self.socket_path)
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            require(self.process.poll() is None, "server startup (no raw logs retained)")
            try:
                status, _ = self.client.request("GET", "/v1/sys/seal-status")
                if status == 200:
                    require(stat.S_IMODE(self.socket_path.stat().st_mode) == 0o600, "UDS permission")
                    return
            except (OSError, http.client.HTTPException):
                pass
            time.sleep(0.1)
        require(False, "server startup deadline")

    def initialize(self):
        status, body = self.client.request("POST", "/v1/sys/init", {"secret_shares": 3, "secret_threshold": 2}, timeout_seconds=30)
        require(status == 200, "initialize")
        self.shares = body["keys_base64"]
        self.root_token = body["root_token"]
        require(len(self.shares) == 3, "Shamir share count")
        self.unseal()

    def unseal(self, shares=None):
        selected = self.shares if shares is None else shares
        for index, share in enumerate(selected[:2]):
            status, body = self.client.request("POST", "/v1/sys/unseal", {"key": share})
            require(status == 200, "manual unseal status " + str(status))
            require(body["sealed"] == (index == 0), "manual threshold enforced")
        self.wait_ready()

    def wait_ready(self):
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            status, body = self.client.request("GET", "/v1/sys/health")
            if status == 200:
                require(body["version"] == VERSION and not body["sealed"], "version and ready")
                return
            time.sleep(0.1)
        require(False, "leader readiness deadline")

    def root(self, method, path, body=None):
        return self.client.request(method, path, body, token=self.root_token)

    def close(self):
        if self.process is not None:
            self.process.terminate()
            try:
                self.process.wait(timeout=8)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait(timeout=5)
            self.process = None
        self.root_token = None
        self.shares.clear()
        if self.temporary is not None:
            self.temporary.cleanup()
            self.temporary = None

    def listener_evidence(self, application_token=None):
        """Read the process's real TCP listening FDs; fail closed if uninspectable."""
        if platform.system() == "Darwin":
            result = subprocess.run(
                ["/usr/sbin/lsof", "-nP", "-a", "-p", str(self.process.pid), "-iTCP", "-sTCP:LISTEN", "-Fn"],
                capture_output=True, text=True, timeout=5,
            )
            require(result.returncode in (0, 1), "lsof listener inspection")
            listeners = [x[1:] for x in result.stdout.splitlines() if x.startswith("n")]
        else:
            fds = Path("/proc") / str(self.process.pid) / "fd"
            sockets = set()
            for fd in fds.iterdir():
                try:
                    target = os.readlink(fd)
                    if target.startswith("socket:["):
                        sockets.add(target[8:-1])
                except FileNotFoundError:
                    continue
            listeners = []
            for table in ("tcp", "tcp6"):
                path = Path("/proc") / str(self.process.pid) / "net" / table
                if not path.exists():
                    continue
                for line in path.read_text().splitlines()[1:]:
                    row = line.split()
                    if row[3] == "0A" and row[9] in sockets:
                        address, port = row[1].split(":")
                        require(table == "tcp" and address == "0100007F", "loopback-only actual listener")
                        listeners.append("127.0.0.1:" + str(int(port, 16)))
        allowed = {"127.0.0.1:" + str(self.cluster_port), "127.0.0.1:" + str(self.metrics_port)}
        require(set(listeners) == allowed, "exact metrics and cluster loopback TCP listeners")
        require(len(listeners) == 2, "exact two loopback TCP listeners")
        restricted_paths = [
            ("POST", "/v1/sys/init", {"secret_shares": 1, "secret_threshold": 1}),
            ("GET", "/v1/sys/health", None),
            ("GET", "/v1/sys/mounts", None),
            ("POST", "/v1/sys/mounts/forbidden", {"type": "transit"}),
            ("POST", "/v1/transit/decrypt/" + KEYS[0], {}),
        ]
        # Even root must be denied on the metrics-only TCP listener.
        tokens = [self.root_token] + ([application_token] if application_token else [])
        for token in tokens:
            for method, path, payload in restricted_paths:
                connection = http.client.HTTPConnection("127.0.0.1", self.metrics_port, timeout=3)
                try:
                    connection.request(method, path, body=None if payload is None else json.dumps(payload),
                                       headers={"X-Vault-Token": token, "Content-Type": "application/json"})
                    response = connection.getresponse()
                    response.read(4096)
                    require(response.status in (403, 404), "metrics TCP denies privileged API " + path)
                finally:
                    connection.close()
        return {"udsMode": "0600", "apiTcpListeners": 0,
                "clusterLoopbackListenerCount": 1, "metricsLoopbackListenerCount": 1,
                "rootTcpApiPathsDenied": len(restricted_paths),
                "applicationTcpApiPathsDenied": len(restricted_paths) if application_token else 0,
                "actualListenersVerified": True, "highAvailabilityVerified": False}


def b64(value):
    return base64.b64encode(value).decode("ascii")


def transit_contract(server):
    require(server.root("POST", "/v1/sys/mounts/transit", {"type": "transit"})[0] == 204, "mount Transit")
    policy = json.loads(Path(__file__).with_name("api-decrypt-policy.json.example").read_text())
    require(server.root("PUT", "/v1/sys/policies/acl/isolated-decrypt", {"policy": json.dumps(policy)})[0] == 204, "exact decrypt policy")
    status, response = server.root("POST", "/v1/auth/token/create", {
        "policies": ["isolated-decrypt"], "no_default_policy": True, "ttl": "5m",
    })
    require(status == 200, "create runtime token")
    token = response["auth"]["client_token"]
    require(response["auth"]["policies"] == ["isolated-decrypt"], "no default or broad policy")
    privileged_listener = server.listener_evidence(token)
    records = []
    for key in KEYS:
        status, _ = server.root("POST", "/v1/transit/keys/" + key, {
            "type": "aes256-gcm96", "derived": True, "exportable": False,
            "allow_plaintext_backup": False, "convergent_encryption": False,
        })
        require(status in (200, 204), "create derived purpose key")
        context = b64(("synthetic-derivation:" + key).encode())
        aad = b64(("synthetic-aead:" + key).encode())
        status, value = server.root("POST", "/v1/transit/datakey/wrapped/" + key,
                                    {"bits": 256, "context": context, "associated_data": aad})
        require(status == 200 and "plaintext" not in value["data"], "wrapped-only data key")
        ciphertext = value["data"]["ciphertext"]
        require(re.fullmatch(r"vault:v1:[A-Za-z0-9+/=]+", ciphertext) is not None, "real initial version prefix")
        payload = {"ciphertext": ciphertext, "context": context, "associated_data": aad}
        status, value = server.client.request("POST", "/v1/transit/decrypt/" + key, payload, token=token)
        require(status == 200, "runtime decrypt allowed")
        clear = base64.b64decode(value["data"]["plaintext"], validate=True)
        require(len(clear) == 32, "256-bit unwrapped data key")
        for name, changed in (("context", b64(b"wrong-context")), ("associated_data", b64(b"wrong-aad"))):
            status, _ = server.client.request("POST", "/v1/transit/decrypt/" + key, {**payload, name: changed}, token=token)
            require(status == 400, "reject mismatched " + name)
        for name in ("context", "associated_data"):
            omitted = {k: v for k, v in payload.items() if k != name}
            status, _ = server.client.request("POST", "/v1/transit/decrypt/" + key, omitted, token=token)
            require(status == 400, "reject missing " + name)
        other_key = KEYS[1] if key == KEYS[0] else KEYS[0]
        status, _ = server.client.request("POST", "/v1/transit/decrypt/" + other_key, payload, token=token)
        # If second key is not created yet, both 400 and 404 are fail-closed.
        require(status in (400, 404), "reject different purpose key")
        require(server.root("POST", "/v1/transit/keys/" + key + "/rotate", {})[0] in (200, 204), "rotate purpose key")
        status, current = server.root("POST", "/v1/transit/datakey/wrapped/" + key,
                                     {"bits": 256, "context": context, "associated_data": aad})
        require(status == 200 and current["data"]["ciphertext"].startswith("vault:v2:"), "genuine next version")
        current_payload = {**payload, "ciphertext": current["data"]["ciphertext"]}
        status, value = server.client.request("POST", "/v1/transit/decrypt/" + key, current_payload, token=token)
        require(status == 200, "current version decrypt")
        current_clear = base64.b64decode(value["data"]["plaintext"], validate=True)
        require(len(current_clear) == 32, "current version 256-bit data key")
        status, value = server.client.request("POST", "/v1/transit/decrypt/" + key, payload, token=token)
        require(status == 200 and base64.b64decode(value["data"]["plaintext"]) == clear, "historical version after rotation")
        records.append((key, payload, clear))
        records.append((key, current_payload, current_clear))
    denied = [
        ("POST", "/v1/transit/encrypt/" + KEYS[0], {"plaintext": b64(b"synthetic")}),
        ("POST", "/v1/transit/datakey/wrapped/" + KEYS[0], {"bits": 256}),
        ("GET", "/v1/transit/keys/" + KEYS[0], None),
        ("POST", "/v1/transit/keys/" + KEYS[0] + "/rotate", {}),
        ("GET", "/v1/transit/export/encryption-key/" + KEYS[0], None),
        ("GET", "/v1/sys/storage/raft/snapshot", None),
        ("POST", "/v1/auth/token/create", {}),
        ("POST", "/v1/transit/decrypt/unapproved", {}),
    ]
    for method, path, body in denied:
        require(server.client.request(method, path, body, token=token)[0] == 403, "runtime ACL rejects " + path)
    key, payload, clear = records[0]
    require(server.root("PUT", "/v1/sys/seal", {})[0] == 204, "manual seal")
    require(server.client.request("POST", "/v1/transit/decrypt/" + key, payload, token=token)[0] == 503, "sealed decrypt denied")
    server.unseal()
    status, value = server.client.request("POST", "/v1/transit/decrypt/" + key, payload, token=token)
    require(status == 200 and base64.b64decode(value["data"]["plaintext"]) == clear, "decrypt after manual unseal")
    status, snapshot = server.root("GET", "/v1/sys/storage/raft/snapshot")
    require(status == 200 and isinstance(snapshot, bytes) and len(snapshot) > 100, "real Raft snapshot")
    with LocalBao(server.binary) as restored:
        # Different fresh cluster is expected to reject original seal checksum.
        status, _ = restored.root("POST", "/v1/sys/storage/raft/snapshot", snapshot)
        require(status == 400, "different seal snapshot rejects ordinary restore")
        status, _ = restored.root("POST", "/v1/sys/storage/raft/snapshot-force", snapshot)
        require(status in (200, 204), "explicit disposable snapshot-force restore")
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            status, body = restored.client.request("GET", "/v1/sys/seal-status")
            if status == 200 and body["sealed"]:
                break
            time.sleep(0.1)
        require(body["sealed"], "restored cluster requires original manual shares")
        restored.unseal(server.shares)
        restored.root_token = server.root_token
        for restored_key, restore_payload, expected in records:
            status, value = restored.client.request("POST", "/v1/transit/decrypt/" + restored_key, restore_payload, token=token)
            require(status == 200 and base64.b64decode(value["data"]["plaintext"]) == expected, "restored historical purpose data key")
        restored_listener = restored.listener_evidence()
    require(server.root("POST", "/v1/auth/token/revoke", {"token": token})[0] == 204, "revoke runtime token")
    require(server.client.request("POST", "/v1/transit/decrypt/" + key, payload, token=token)[0] == 403, "revoked token denied")
    return {
        "purposeCount": 2, "dataKeyBytes": 32, "derivedContextVerified": True,
        "associatedDataVerified": True, "wrappedOnlyVerified": True,
        "realVersionsVerified": [1, 2], "runtimeDeniedOperations": len(denied),
        "manualSealUnsealVerified": True, "revokedTokenRejected": True,
        "snapshotRestoreFreshDirectoryVerified": True, "restoredHistoricalPurposeCount": 2,
        "restoredDataKeys": 4, "restoredVersionsPerPurpose": [1, 2],
        "snapshotForceUsedOnlyInDisposableCluster": True, "restoreListeners": restored_listener,
        "privilegedListeners": privileged_listener,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--binary", required=True)
    parser.add_argument("--hook", action="append", default=[], help="trusted local module:function; no untrusted modules")
    parser.add_argument("--skip-transit", action="store_true", help="run a separate new hook-only experiment")
    args = parser.parse_args()
    report = {"schema": "rsc.openbao.isolated-runtime.v1", "version": VERSION,
              "productionReady": False, "productionConfigured": False,
              "syntheticOnly": True, "platform": platform.system() + "/" + platform.machine()}
    try:
        with LocalBao(args.binary) as server:
            report["binarySha256"] = server.binary_sha256
            report["listeners"] = server.listener_evidence()
            if not args.skip_transit:
                report["transit"] = transit_contract(server)
            for hook in args.hook:
                module, name = hook.split(":", 1)
                report[name] = getattr(importlib.import_module(module), name)(server)
        report["cleanupCompleted"] = True
        report["result"] = "passed"
    except Exception as error:
        report["result"] = "failed"
        # Only errors from this harness are allowed into the public report.
        safe_identity_code = type(error).__name__ == "IdentityContractError" and re.fullmatch(r"identity_[a-z_]+", str(error))
        report["failure"] = str(error) if str(error).startswith("isolated OpenBao check failed:") or safe_identity_code else type(error).__name__
    print(json.dumps(report, ensure_ascii=True, indent=2))
    return 0 if report["result"] == "passed" else 1


if __name__ == "__main__":
    raise SystemExit(main())
