"""New UDS transport tests only; the 49 candidate tests stay terminal."""

import base64
from contextlib import contextmanager
import importlib.util
import json
import os
from pathlib import Path
import socket
import tempfile
import threading
import time
import traceback

import pytest

from app.openbao_transit_candidate import OpenBaoDecryptRequest


MODULE = Path(__file__).resolve().parents[2] / "deployment/openbao-pilot/candidate_hook.py"
TOKEN = "synthetic-test-token-only"
MARKER = "synthetic-sensitive-marker-must-not-escape"
REQUEST = OpenBaoDecryptRequest(
    "/v1/transit/decrypt/rsc-authentication-idempotency",
    "vault:v1:" + "A" * 80,
    base64.b64encode(b"synthetic-context").decode(),
    base64.b64encode(b"synthetic-aad").decode(),
)


@pytest.fixture
def hook():
    spec = importlib.util.spec_from_file_location("candidate_hook", MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(autouse=True)
def block_tcp(monkeypatch):
    original = socket.socket.connect
    def connect(sock, address):
        if sock.family != socket.AF_UNIX:
            pytest.fail("only synthetic AF_UNIX transport is allowed")
        return original(sock, address)
    monkeypatch.setattr(socket.socket, "connect", connect)


def response(body=None, *, status=200, headers=()):
    body = body if body is not None else json.dumps({"data": {"plaintext": base64.b64encode(bytes(range(32))).decode()}}).encode()
    initial = [f"HTTP/1.1 {status} Test", "Content-Type: application/json", f"Content-Length: {len(body)}", "Connection: close"]
    initial.extend(headers)
    return ("\r\n".join(initial) + "\r\n\r\n").encode() + body


@contextmanager
def uds_server(send):
    with tempfile.TemporaryDirectory(prefix="rsc-hook-test-", dir="/tmp") as directory:
        os.chmod(directory, 0o700)
        path = Path(directory) / "api.sock"
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        listener.bind(str(path))
        path.chmod(0o600)
        listener.listen(4)
        listener.settimeout(0.05)
        calls, failures = [], []
        stopped = threading.Event()
        def run():
            while not stopped.is_set():
                try:
                    connection, _ = listener.accept()
                except socket.timeout:
                    continue
                except OSError:
                    return
                with connection:
                    try:
                        connection.settimeout(1)
                        raw = b""
                        while b"\r\n\r\n" not in raw:
                            raw += connection.recv(4096)
                            if len(raw) > 32 * 1024:
                                raise AssertionError("unexpected large request")
                        head, body = raw.split(b"\r\n\r\n", 1)
                        size = int(next(line.split(b":", 1)[1] for line in head.split(b"\r\n") if line.lower().startswith(b"content-length:")))
                        while len(body) < size:
                            body += connection.recv(4096)
                        assert b"X-Vault-Token: " + TOKEN.encode() in head
                        calls.append((head.split(b"\r\n", 1)[0].decode(), json.loads(body)))
                        send(connection)
                    except (BrokenPipeError, ConnectionResetError):
                        pass
                    except Exception as exc:
                        failures.append(type(exc).__name__)
        thread = threading.Thread(target=run, daemon=True)
        thread.start()
        try:
            yield path, calls
        finally:
            stopped.set()
            listener.close()
            thread.join(timeout=2)
            assert not thread.is_alive()
            assert not failures


def test_exact_decrypt_contract_success_and_no_credential_representation(hook):
    with uds_server(lambda connection: connection.sendall(response())) as (path, calls):
        transport = hook.PrototypeUnixDecryptTransport(socket_path=path, token=TOKEN)
        value = transport.decrypt(request=REQUEST, timeout_seconds=3)
        assert value.status_code == 200
        assert base64.b64decode(value.body["data"]["plaintext"]) == bytes(range(32))
        assert calls == [("POST " + REQUEST.path + " HTTP/1.1", {
            "ciphertext": REQUEST.ciphertext, "context": REQUEST.context,
            "associated_data": REQUEST.associated_data,
        })]
        assert TOKEN not in repr(transport)


@pytest.mark.parametrize("status", [400, 403, 503])
def test_negative_remote_status_preserved_without_retry(hook, status):
    with uds_server(lambda connection: connection.sendall(response(b'{"errors":["synthetic"]}', status=status))) as (path, calls):
        value = hook.PrototypeUnixDecryptTransport(socket_path=path, token=TOKEN).decrypt(request=REQUEST, timeout_seconds=3)
        assert value.status_code == status
        assert len(calls) == 1


@pytest.mark.parametrize("wire", [
    response(b'{"data":{"plaintext":"first","plaintext":"' + MARKER.encode() + b'"}}'),
    response(b'{"data":{},"data":{"plaintext":"' + MARKER.encode() + b'"}}'),
    response(b'{"data":{"plaintext":NaN}}'),
    response(b'{"data":{"plaintext":Infinity}}'),
    response(b'{"data":' + MARKER.encode()),
    response(b"[1,2,3]"),
    response(b"\xff"),
    response(status=302, headers=("Location: https://not-called.example/" + MARKER,)),
    response(headers=("Transfer-Encoding: chunked",)),
    response(headers=("Content-Length: 5",)),
    response(headers=("Content-Type: application/json",)),
    response(b"x" * (16 * 1024 + 1)),
    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n\r\n{}",
    b"HTTP/1.1 200 OK\r\nContent-Type: text/plain\r\nContent-Length: 2\r\n\r\n{}",
    b"HTTP/1.1 200 OK\r\nX-Test: " + b"x" * (32 * 1024) + b"\r\n\r\n",
], ids=["nested_duplicate", "root_duplicate", "nan", "infinity", "invalid_json",
        "array_root", "invalid_utf8", "redirect", "chunked", "duplicate_length",
        "duplicate_type", "oversize_body", "missing_length", "wrong_type", "oversize_header"])
def test_malformed_reply_rejected_without_leak_or_redirect(hook, wire, caplog, capsys):
    with uds_server(lambda connection: connection.sendall(wire)) as (path, calls):
        with pytest.raises(hook.PrototypeTransportUnavailable) as caught:
            hook.PrototypeUnixDecryptTransport(socket_path=path, token=TOKEN).decrypt(request=REQUEST, timeout_seconds=3)
        assert len(calls) == 1
        assert caught.value.__cause__ is None and caught.value.__context__ is None
        output = capsys.readouterr()
        assert MARKER not in "".join(traceback.format_exception(caught.value)) + output.out + output.err + caplog.text


@pytest.mark.parametrize("phase", ["headers", "body"])
def test_total_deadline_bounds_slow_drip_not_just_each_recv(hook, phase):
    wire = response()
    head, body = wire.split(b"\r\n\r\n", 1)
    def send(connection):
        if phase == "body":
            connection.sendall(head + b"\r\n\r\n")
            delayed = body
        else:
            delayed = wire
        for byte in delayed:
            connection.sendall(bytes([byte]))
            time.sleep(0.025)
    with uds_server(send) as (path, calls):
        transport = hook.PrototypeUnixDecryptTransport(socket_path=path, token=TOKEN)
        started = time.monotonic()
        with pytest.raises(hook.PrototypeTransportUnavailable):
            transport.decrypt(request=REQUEST, timeout_seconds=0.12)
        assert 0.09 <= time.monotonic() - started < 0.45
        assert len(calls) == 1


@pytest.mark.parametrize("timeout", [0, -1, True, float("nan"), float("inf"), 3.01])
def test_budget_must_be_explicit_finite_and_at_most_three_seconds(hook, timeout):
    with uds_server(lambda connection: connection.sendall(response())) as (path, calls):
        with pytest.raises(hook.PrototypeTransportUnavailable):
            hook.PrototypeUnixDecryptTransport(socket_path=path, token=TOKEN).decrypt(request=REQUEST, timeout_seconds=timeout)
        assert calls == []


def test_other_paths_blocked_before_transport(hook):
    request = OpenBaoDecryptRequest("/v1/transit/keys/unapproved/rotate", REQUEST.ciphertext, REQUEST.context, REQUEST.associated_data)
    with uds_server(lambda connection: connection.sendall(response())) as (path, calls):
        with pytest.raises(hook.PrototypeTransportUnavailable):
            hook.PrototypeUnixDecryptTransport(socket_path=path, token=TOKEN).decrypt(request=request, timeout_seconds=3)
        assert calls == []


def test_changed_or_loose_socket_fails_before_request(hook):
    with uds_server(lambda connection: connection.sendall(response())) as (path, calls):
        transport = hook.PrototypeUnixDecryptTransport(socket_path=path, token=TOKEN)
        path.chmod(0o666)
        with pytest.raises(hook.PrototypeTransportUnavailable):
            transport.decrypt(request=REQUEST, timeout_seconds=3)
        assert calls == []


def test_hook_failure_never_carries_raw_server_exception(hook):
    class Unavailable:
        @property
        def socket_path(self):
            raise RuntimeError(MARKER)
    with pytest.raises(RuntimeError) as caught:
        hook.verify_candidate_adapter(Unavailable())
    assert str(caught.value) == "isolated OpenBao check failed: candidate hook"
    assert caught.value.__cause__ is None and caught.value.__context__ is None
