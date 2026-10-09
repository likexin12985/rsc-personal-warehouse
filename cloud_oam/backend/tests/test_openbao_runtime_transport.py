"""Synthetic tests only: real local UDS HTTP, separately mocked Linux provenance.

These tests never read a deployed token. They do not establish Linux peercred,
read-only mounts, service policy/expiry/revocation, or production readiness.
"""

from contextlib import contextmanager
import json
import os
from pathlib import PurePosixPath
import socket
import stat
import struct
import threading
import time
from types import SimpleNamespace

import pytest

from app import openbao_runtime_transport as runtime
from app.openbao_transit_candidate import OpenBaoDecryptRequest


IDENTITY = runtime._Identity(41001, 41002, 42002, 42001, 41003)
ARGS = dict(api_uid=41001, bao_uid=41002, bao_gid=42002, shared_gid=42001,
            token_projector_uid=41003, socket_path="/run/rsc-bao/api.sock",
            token_file="/run/rsc-token/api-token")
REQUEST = OpenBaoDecryptRequest(
    "/v1/transit/decrypt/rsc-material-request-contact", "vault:v1:synthetic", "Yw==", "YQ==",
)
SAFE_MESSAGE = "OpenBao decrypt transport is unavailable"


def assert_safe_error(call):
    with pytest.raises(runtime.OpenBaoTransportUnavailable) as caught:
        call()
    assert caught.value.args == (SAFE_MESSAGE,)
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None


def synthetic_stat(**changes):
    values = dict(st_mode=stat.S_IFREG | 0o440, st_uid=IDENTITY.projector_uid,
                  st_gid=IDENTITY.shared_gid, st_nlink=1, st_size=23,
                  st_dev=1, st_ino=2, st_mtime_ns=3, st_ctime_ns=4)
    values.update(changes)
    return SimpleNamespace(**values)


@pytest.mark.parametrize("field,value", [
    ("api_uid", True), ("bao_uid", -1), ("bao_gid", 0), ("shared_gid", False),
    ("token_projector_uid", 0), ("api_uid", 0), ("bao_uid", 0),
    ("shared_gid", 0), ("api_uid", 1.5), ("token_projector_uid", "41003"),
    ("bao_uid", 41001), ("token_projector_uid", 41002),
])
def test_constructor_rejects_unsafe_numeric_identity_before_io(monkeypatch, field, value):
    reached = []
    monkeypatch.setattr(runtime, "_assert_api_identity", lambda *_: reached.append(True))
    assert_safe_error(lambda: runtime.OpenBaoUnixDecryptTransport(**(ARGS | {field: value})))
    assert reached == []


@pytest.mark.parametrize("path", [
    "/tmp/a/b", "run/a/b", "/run/token", "/run/a/../b/token", "/run//a/b",
    "/run/a/./b", "/run/a/b/", "/run/a/b\n", "/run/a/中文", "/run/a/" + "x" * 81,
])
def test_explicit_run_paths_reject_ambiguous_or_uncontrolled_forms(path):
    with pytest.raises(ValueError):
        runtime._absolute_parts(path)


@pytest.mark.parametrize("changes", [
    {"st_mode": stat.S_IFREG | 0o750}, {"st_uid": IDENTITY.api_uid},
    {"st_gid": 999}, {"st_mode": stat.S_IFDIR | 0o770},
    {"st_mode": stat.S_IFDIR | 0o755}, {"st_mode": stat.S_IFDIR | 0o4750},
])
def test_private_directory_requires_separate_owner_and_exact_permissions(changes):
    value = synthetic_stat(st_mode=stat.S_IFDIR | 0o750, st_uid=IDENTITY.bao_uid)
    value.__dict__.update(changes)
    with pytest.raises(ValueError):
        runtime._directory_stat(value, owner=IDENTITY.bao_uid, group=IDENTITY.shared_gid, private=True)


def test_ancestor_requires_root_and_no_group_or_other_write():
    for mode, uid in [(0o775, 0), (0o757, 0), (0o755, IDENTITY.api_uid)]:
        with pytest.raises(ValueError):
            runtime._directory_stat(synthetic_stat(st_mode=stat.S_IFDIR | mode, st_uid=uid),
                                    owner=0, group=None, private=False)
    runtime._directory_stat(synthetic_stat(st_mode=stat.S_IFDIR | 0o755, st_uid=0),
                            owner=0, group=None, private=False)


@pytest.mark.parametrize("changes", [
    {"st_mode": stat.S_IFLNK | 0o440}, {"st_mode": stat.S_IFIFO | 0o440},
    {"st_mode": stat.S_IFREG | 0o640}, {"st_mode": stat.S_IFREG | 0o444},
    {"st_uid": IDENTITY.api_uid}, {"st_gid": 999}, {"st_nlink": 2},
    {"st_size": 9}, {"st_size": 4097},
])
def test_token_metadata_rejects_uncontrolled_projection(changes):
    with pytest.raises(ValueError):
        runtime._token_stat(synthetic_stat(**changes), IDENTITY)


@pytest.mark.parametrize("changes", [
    {"st_mode": stat.S_IFREG | 0o660}, {"st_mode": stat.S_IFSOCK | 0o666},
    {"st_mode": stat.S_IFSOCK | 0o600}, {"st_uid": IDENTITY.api_uid},
    {"st_gid": 999}, {"st_nlink": 2},
])
def test_socket_metadata_rejects_uncontrolled_endpoint(changes):
    value = synthetic_stat(st_mode=stat.S_IFSOCK | 0o660, st_uid=IDENTITY.bao_uid)
    value.__dict__.update(changes)
    with pytest.raises(ValueError):
        runtime._socket_stat(value, IDENTITY)


def test_linux_identity_is_required_and_real_effective_uid_must_match(monkeypatch):
    monkeypatch.setattr(runtime.sys, "platform", "linux")
    monkeypatch.setattr(runtime.socket, "SO_PEERCRED", 17, raising=False)
    monkeypatch.setattr(runtime.os, "getuid", lambda: IDENTITY.api_uid)
    monkeypatch.setattr(runtime.os, "geteuid", lambda: IDENTITY.api_uid)
    monkeypatch.setattr(runtime.os, "getegid", lambda: IDENTITY.shared_gid)
    monkeypatch.setattr(runtime.os, "getgroups", lambda: [])
    runtime._assert_api_identity(IDENTITY)
    monkeypatch.setattr(runtime.os, "geteuid", lambda: 0)
    with pytest.raises(ValueError):
        runtime._assert_api_identity(IDENTITY)
    monkeypatch.setattr(runtime.os, "geteuid", lambda: IDENTITY.api_uid)
    monkeypatch.setattr(runtime.os, "getegid", lambda: 999)
    with pytest.raises(ValueError):
        runtime._assert_api_identity(IDENTITY)
    monkeypatch.setattr(runtime.os, "getegid", lambda: IDENTITY.shared_gid)
    monkeypatch.setattr(runtime.sys, "platform", "darwin")
    with pytest.raises(ValueError):
        runtime._assert_api_identity(IDENTITY)


def test_peercred_parses_kernel_tuple_and_rejects_invalid_pid(monkeypatch):
    monkeypatch.setattr(runtime.socket, "SO_PEERCRED", 17, raising=False)
    calls = []
    def get_option(*args):
        calls.append(args)
        return struct.pack("3i", 100, IDENTITY.bao_uid, IDENTITY.bao_gid)
    assert runtime._peer_identity(SimpleNamespace(getsockopt=get_option)) == (IDENTITY.bao_uid, IDENTITY.bao_gid)
    assert calls == [(socket.SOL_SOCKET, 17, struct.calcsize("3i"))]
    with pytest.raises(ValueError):
        runtime._peer_identity(SimpleNamespace(getsockopt=lambda *_: struct.pack("3i", 0, 1, 1)))


def test_directory_walk_uses_nofollow_for_all_components_and_rejects_symlink(tmp_path, monkeypatch):
    # Real O_NOFOLLOW behavior; ownership checks are separately covered above.
    real = tmp_path / "real"
    real.mkdir()
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    original_open = os.open
    flags_seen = []
    def opening(path, flags, **kwargs):
        flags_seen.append(flags)
        return original_open(path, flags, **kwargs)
    monkeypatch.setattr(runtime.os, "open", opening)
    monkeypatch.setattr(runtime, "_directory_stat", lambda value, **_: (value.st_dev, value.st_ino))
    parts = PurePosixPath(str(link / "api.sock")).parts[1:]
    with pytest.raises(OSError):
        runtime._open_directory(parts, owner=IDENTITY.bao_uid, group=IDENTITY.shared_gid)
    assert flags_seen and all(flags & os.O_NOFOLLOW and flags & os.O_DIRECTORY for flags in flags_seen)


@pytest.fixture
def projected_token(tmp_path, monkeypatch):
    """Real files/read/rotation; Linux UID mapping and read-only mount are mocked."""
    folder = tmp_path / "projection"
    folder.mkdir(mode=0o750)
    path = folder / "api-token"
    path.write_bytes(b"hvs.synthetic-token-one")
    path.chmod(0o440)
    chain = ("synthetic-reviewed-directory",)
    real_fstat, real_stat = os.fstat, os.stat
    def map_owner(value):
        result = SimpleNamespace(**{name: getattr(value, name) for name in (
            "st_mode", "st_uid", "st_gid", "st_nlink", "st_size", "st_dev", "st_ino", "st_mtime_ns", "st_ctime_ns",
        )})
        result.st_uid, result.st_gid = IDENTITY.projector_uid, IDENTITY.shared_gid
        return result
    monkeypatch.setattr(runtime.os, "fstat", lambda fd: map_owner(real_fstat(fd)))
    monkeypatch.setattr(runtime.os, "stat", lambda *a, **kw: map_owner(real_stat(*a, **kw)))
    monkeypatch.setattr(runtime.os, "fstatvfs", lambda _: SimpleNamespace(f_flag=os.ST_RDONLY))
    monkeypatch.setattr(runtime, "_open_directory", lambda *_a, **_kw: (os.open(folder, os.O_RDONLY | os.O_DIRECTORY), chain))
    transport = object.__new__(runtime.OpenBaoUnixDecryptTransport)
    transport._identity = IDENTITY
    transport._token_parts, transport._token_chain = ("run", "projection", "api-token"), chain
    return transport, path


def test_projected_token_is_read_fresh_after_atomic_rotation(projected_token):
    transport, path = projected_token
    assert transport._read_token(time.monotonic() + 1) == "hvs.synthetic-token-one"
    replacement = path.with_name("replacement")
    replacement.write_bytes(b"hvs.synthetic-token-two")
    replacement.chmod(0o440)
    os.replace(replacement, path)
    assert transport._read_token(time.monotonic() + 1) == "hvs.synthetic-token-two"
    assert not any("hvs." in repr(value) for value in vars(transport).values())


@pytest.mark.parametrize("value", [b"hvs.synthetic\n", b"hvs.synthetic\r\n", b"hvs.\x00synthetic", b"x" * 4097, b"short", b"secret\xffvalue"])
def test_projected_token_rejects_malformed_bytes(projected_token, value):
    transport, path = projected_token
    replacement = path.with_name("malformed-projection")
    replacement.write_bytes(value)
    replacement.chmod(0o440)
    os.replace(replacement, path)
    with pytest.raises(ValueError):
        transport._read_token(time.monotonic() + 1)


def test_projected_token_requires_actual_read_only_mount(projected_token, monkeypatch):
    transport, _ = projected_token
    monkeypatch.setattr(runtime.os, "fstatvfs", lambda _: SimpleNamespace(f_flag=0))
    with pytest.raises(ValueError, match="not read-only"):
        transport._read_token(time.monotonic() + 1)


@pytest.mark.parametrize("kind", ["symlink", "hardlink", "writable"])
def test_projected_token_rejects_links_and_writable_mode(projected_token, kind):
    transport, path = projected_token
    if kind == "writable":
        path.chmod(0o640)
    elif kind == "hardlink":
        os.link(path, path.with_name("other-link"))
    else:
        actual = path.with_name("actual")
        path.rename(actual)
        path.symlink_to(actual)
    with pytest.raises((OSError, ValueError)):
        transport._read_token(time.monotonic() + 1)


def test_projected_token_rejects_mid_read_replacement(projected_token, monkeypatch):
    transport, path = projected_token
    original_read = os.read
    def replacing_read(*args):
        value = original_read(*args)
        replacement = path.with_name("new-token")
        replacement.write_bytes(b"hvs.synthetic-token-two")
        replacement.chmod(0o440)
        os.replace(replacement, path)
        return value
    monkeypatch.setattr(runtime.os, "read", replacing_read)
    with pytest.raises(ValueError):
        transport._read_token(time.monotonic() + 1)


def test_projected_token_rejects_changed_directory_after_read(projected_token, monkeypatch):
    transport, _ = projected_token
    original = runtime._open_directory
    count = 0
    def changed(*args, **kwargs):
        nonlocal count
        fd, chain = original(*args, **kwargs)
        count += 1
        return fd, chain if count == 1 else ("replaced-directory",)
    monkeypatch.setattr(runtime, "_open_directory", changed)
    with pytest.raises(ValueError):
        transport._read_token(time.monotonic() + 1)


@pytest.mark.parametrize("phase", ["peer_uid", "peer_gid", "socket_before", "socket_after", "directory_after"])
def test_linux_connect_rejects_peer_or_pinned_endpoint_drift_without_sending(tmp_path, monkeypatch, phase):
    transport = object.__new__(runtime.OpenBaoUnixDecryptTransport)
    transport._identity, transport._socket_parts = IDENTITY, ("run", "rsc-bao", "api.sock")
    transport._socket_chain, transport._socket_identity = ("reviewed-dir",), ("reviewed-socket",)
    events = []
    endpoint = SimpleNamespace(connect=lambda p: events.append(("connect", p)), close=lambda: events.append(("close",)))
    monkeypatch.setattr(runtime, "_assert_api_identity", lambda _: None)
    monkeypatch.setattr(runtime, "_open_directory", lambda *_a, **_kw: (os.open(tmp_path, os.O_RDONLY), transport._socket_chain))
    monkeypatch.setattr(runtime.os, "stat", lambda *_a, **_kw: None)
    count = 0
    def socket_stat(*_):
        nonlocal count
        count += 1
        return ("changed",) if (phase == "socket_before" or phase == "socket_after" and count == 2) else transport._socket_identity
    monkeypatch.setattr(runtime, "_socket_stat", socket_stat)
    monkeypatch.setattr(runtime, "_DeadlineSocket", lambda _: endpoint)
    monkeypatch.setattr(runtime, "_peer_identity", lambda _: (
        999 if phase == "peer_uid" else IDENTITY.bao_uid,
        999 if phase == "peer_gid" else IDENTITY.bao_gid,
    ))
    transport._endpoint_snapshot = lambda: (("changed",), transport._socket_identity) if phase == "directory_after" else (transport._socket_chain, transport._socket_identity)
    with pytest.raises(ValueError):
        transport._connect(time.monotonic() + 1)
    if phase == "socket_before":
        assert events == []
    else:
        assert events[0][0] == "connect" and events[0][1].startswith("/proc/self/fd/")
        assert events[-1] == ("close",)


def http_reply(body=b'{"data":{"plaintext":"synthetic"}}', *, status=200, extra=b"", content_type=b"application/json"):
    return (f"HTTP/1.1 {status} Test\r\n".encode() + b"Content-Type: " + content_type + b"\r\nContent-Length: " +
            str(len(body)).encode() + b"\r\n" + extra + b"Connection: close\r\n\r\n" + body)


@contextmanager
def local_wire_server(tmp_path, replies):
    """Real OS UDS HTTP only; this intentionally bypasses Linux provenance."""
    # macOS sockaddr_un has a short pathname bound.
    import tempfile
    with tempfile.TemporaryDirectory(prefix="bao-wire-", dir="/tmp") as folder:
        path = folder + "/api.sock"
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(path)
        server.listen(4)
        server.settimeout(1)
        requests, errors = [], []
        def serve():
            try:
                for reply in replies:
                    peer, _ = server.accept()
                    with peer:
                        peer.settimeout(1)
                        raw = b""
                        while b"\r\n\r\n" not in raw:
                            raw += peer.recv(4096)
                        headers, body = raw.split(b"\r\n\r\n", 1)
                        length = int(next(line.split(b":", 1)[1] for line in headers.split(b"\r\n") if line.lower().startswith(b"content-length:")))
                        while len(body) < length:
                            body += peer.recv(4096)
                        requests.append((headers, body))
                        if callable(reply):
                            reply(peer)
                        else:
                            peer.sendall(reply)
            except (BrokenPipeError, ConnectionResetError):
                pass  # Expected when the bounded client rejects a hostile response.
            except Exception as error:
                errors.append(type(error).__name__)
        worker = threading.Thread(target=serve, daemon=True)
        worker.start()
        transport = object.__new__(runtime.OpenBaoUnixDecryptTransport)
        tokens = iter(["hvs.synthetic-token-one", "hvs.synthetic-token-two"])
        transport._read_token = lambda _: next(tokens)
        def connect(deadline):
            endpoint = runtime._DeadlineSocket(deadline)
            endpoint.connect(path)
            return endpoint
        transport._connect = connect
        try:
            yield transport, requests
        finally:
            worker.join(timeout=2)
            server.close()
            assert not worker.is_alive()
            assert errors == []


def test_real_local_uds_http_uses_fixed_decrypt_path_and_fresh_token(tmp_path):
    with local_wire_server(tmp_path, [http_reply(), http_reply()]) as (transport, requests):
        for path in sorted(runtime._PATHS):
            response = transport.decrypt(request=OpenBaoDecryptRequest(path, REQUEST.ciphertext, REQUEST.context, REQUEST.associated_data), timeout_seconds=1)
            assert response.status_code == 200 and response.body == {"data": {"plaintext": "synthetic"}}
        assert len(requests) == 2
        assert b"X-Vault-Token: hvs.synthetic-token-one" in requests[0][0]
        assert b"X-Vault-Token: hvs.synthetic-token-two" in requests[1][0]
        for headers, body in requests:
            assert headers.startswith(b"POST /v1/transit/decrypt/rsc-")
            assert b"Connection: close" in headers
            assert json.loads(body) == {"ciphertext": REQUEST.ciphertext, "context": REQUEST.context, "associated_data": REQUEST.associated_data}


@pytest.mark.parametrize("reply", [
    http_reply(status=302, extra=b"Location: https://example.invalid/\r\n"),
    http_reply(extra=b"Location: /other\r\n"),
    http_reply(extra=b"Transfer-Encoding: chunked\r\n"),
    http_reply(extra=b"content-length: 1\r\n"),
    http_reply(content_type=b"text/html"),
    http_reply(b'{"data":{},"data":{}}'),
    http_reply(b'{"data":{"value":NaN}}'),
    http_reply(b'{"data":{"value":Infinity}}'),
    http_reply(b'[]'), http_reply(b'\xff'), http_reply(b'x' * 16385),
    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 99\r\n\r\n{}",
    b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: 02\r\n\r\n{}",
    http_reply(extra=b"".join(f"X-Synthetic-{i}: ".encode() + b"x" * 1024 + b"\r\n" for i in range(40))),
])
def test_real_local_uds_rejects_hostile_response_without_retry(tmp_path, reply):
    with local_wire_server(tmp_path, [reply]) as (transport, requests):
        assert_safe_error(lambda: transport.decrypt(request=REQUEST, timeout_seconds=1))
        assert len(requests) == 1


def test_real_local_uds_drip_response_obeys_one_total_deadline(tmp_path):
    def drip(peer):
        for byte in http_reply():
            peer.sendall(bytes([byte]))
            time.sleep(0.025)
    with local_wire_server(tmp_path, [drip]) as (transport, requests):
        started = time.monotonic()
        assert_safe_error(lambda: transport.decrypt(request=REQUEST, timeout_seconds=0.12))
        elapsed = time.monotonic() - started
        assert 0.08 <= elapsed < 0.6
        assert len(requests) == 1


@pytest.mark.parametrize("timeout", [True, 0, -1, 3.01, float("nan"), float("inf"), "3"])
def test_invalid_deadline_rejected_before_token_or_network(timeout):
    transport = object.__new__(runtime.OpenBaoUnixDecryptTransport)
    calls = []
    transport._read_token = lambda _: calls.append(True)
    assert_safe_error(lambda: transport.decrypt(request=REQUEST, timeout_seconds=timeout))
    assert calls == []


@pytest.mark.parametrize("path", ["https://example.invalid", "/v1/sys/init", "/v1/transit/encrypt/rsc-material-request-contact", REQUEST.path + "?x=1"])
def test_arbitrary_url_path_and_query_never_read_token(path):
    transport = object.__new__(runtime.OpenBaoUnixDecryptTransport)
    calls = []
    transport._read_token = lambda _: calls.append(True)
    assert_safe_error(lambda: transport.decrypt(request=OpenBaoDecryptRequest(path, "x", "y", "z"), timeout_seconds=1))
    assert calls == []


def test_sensitive_token_or_network_exception_has_no_public_chain():
    transport = object.__new__(runtime.OpenBaoUnixDecryptTransport)
    def fail(_):
        raise RuntimeError("hvs.synthetic-do-not-expose /run/private secret request")
    transport._read_token = fail
    assert_safe_error(lambda: transport.decrypt(request=REQUEST, timeout_seconds=1))


def test_valid_constructor_pins_distinct_directories_without_loading_token(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(runtime, "_assert_api_identity", lambda identity: calls.append(("identity", identity)))
    monkeypatch.setattr(runtime.OpenBaoUnixDecryptTransport, "_endpoint_snapshot", lambda _: (("socket-dir",), ("socket-inode",)))
    def open_directory(parts, **identity):
        calls.append(("directory", parts, identity))
        return os.open(tmp_path, os.O_RDONLY | os.O_DIRECTORY), ("token-dir",)
    monkeypatch.setattr(runtime, "_open_directory", open_directory)
    monkeypatch.setattr(runtime.OpenBaoUnixDecryptTransport, "_read_token", lambda *_: pytest.fail("constructor must not load credential"))
    transport = runtime.OpenBaoUnixDecryptTransport(**ARGS)
    assert transport._socket_chain == ("socket-dir",)
    assert transport._socket_identity == ("socket-inode",)
    assert transport._token_chain == ("token-dir",)
    assert calls == [("identity", IDENTITY), ("directory", ("run", "rsc-token", "api-token"),
                                             {"owner": IDENTITY.projector_uid, "group": IDENTITY.shared_gid})]


def test_constructor_rejects_same_directory_for_bao_and_projector(monkeypatch):
    monkeypatch.setattr(runtime, "_assert_api_identity", lambda _: None)
    monkeypatch.setattr(runtime.OpenBaoUnixDecryptTransport, "_endpoint_snapshot", lambda _: pytest.fail("must reject before socket lookup"))
    assert_safe_error(lambda: runtime.OpenBaoUnixDecryptTransport(**(ARGS | {"token_file": "/run/rsc-bao/api-token"})))


def test_constructor_suppresses_sensitive_filesystem_error(monkeypatch):
    monkeypatch.setattr(runtime, "_assert_api_identity", lambda _: None)
    def fail(_):
        raise OSError("synthetic secret projection path")
    monkeypatch.setattr(runtime.OpenBaoUnixDecryptTransport, "_endpoint_snapshot", fail)
    assert_safe_error(lambda: runtime.OpenBaoUnixDecryptTransport(**ARGS))


def test_reviewed_peer_success_connects_through_anchored_fd(tmp_path, monkeypatch):
    transport = object.__new__(runtime.OpenBaoUnixDecryptTransport)
    transport._identity, transport._socket_parts = IDENTITY, ("run", "rsc-bao", "api.sock")
    transport._socket_chain, transport._socket_identity = ("reviewed-dir",), ("reviewed-socket",)
    connected = []
    endpoint = SimpleNamespace(connect=connected.append, close=lambda: pytest.fail("valid connection prematurely closed"))
    monkeypatch.setattr(runtime, "_assert_api_identity", lambda _: None)
    monkeypatch.setattr(runtime, "_open_directory", lambda *_a, **_kw: (os.open(tmp_path, os.O_RDONLY), transport._socket_chain))
    monkeypatch.setattr(runtime.os, "stat", lambda *_a, **_kw: None)
    monkeypatch.setattr(runtime, "_socket_stat", lambda *_: transport._socket_identity)
    monkeypatch.setattr(runtime, "_DeadlineSocket", lambda _: endpoint)
    monkeypatch.setattr(runtime, "_peer_identity", lambda _: (IDENTITY.bao_uid, IDENTITY.bao_gid))
    transport._endpoint_snapshot = lambda: (transport._socket_chain, transport._socket_identity)
    assert transport._connect(time.monotonic() + 1) is endpoint
    assert len(connected) == 1 and connected[0].startswith("/proc/self/fd/") and connected[0].endswith("/api.sock")


def test_real_local_uds_header_and_body_share_total_deadline(tmp_path):
    def delay_stages(peer):
        header, body = http_reply().split(b"\r\n\r\n", 1)
        time.sleep(0.07)
        peer.sendall(header + b"\r\n\r\n")
        time.sleep(0.07)
        peer.sendall(body)
    with local_wire_server(tmp_path, [delay_stages]) as (transport, requests):
        started = time.monotonic()
        assert_safe_error(lambda: transport.decrypt(request=REQUEST, timeout_seconds=0.12))
        assert time.monotonic() - started < 0.6
        assert len(requests) == 1


def test_token_read_time_is_included_in_network_deadline(tmp_path):
    with local_wire_server(tmp_path, []) as (transport, requests):
        transport._read_token = lambda _: (time.sleep(0.03) or "hvs.synthetic-token-one")
        assert_safe_error(lambda: transport.decrypt(request=REQUEST, timeout_seconds=0.01))
        assert requests == []
