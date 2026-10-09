"""Linux-only, explicitly constructed OpenBao decrypt transport; not enabled.

The deployment owns three distinct non-root UIDs (API, OpenBao, token
projector), an API-readable shared group and two private /run directories.
Directories are 0750; the socket is 0660; the projected token is 0440 on a
read-only mount. Upper directories are root-owned and not group/world writable.
The API cannot replace either private directory or its entries. The projector
rotates tokens by atomic replacement through its separate writable mount.
Deployment must also drop Linux capabilities and prohibit privilege escalation;
UID and POSIX mode checks alone do not establish those process restrictions.

Opaque token bytes cannot prove policy, TTL, renewal or revocation. Deployment
must establish the reviewed periodic service token, never a root token. Its
policy permits only the two purpose-specific decrypt paths and self lookup /
renewal; the API holds that same token and can therefore renew its own token.
This transport itself calls only decrypt; actual authorization is checked by
OpenBao. This component creates no identity, loads
no environment credentials, and caches neither tokens nor connections.
"""

from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import math
import os
from pathlib import PurePosixPath
import re
import socket
import stat
import struct
import sys
import time

from .openbao_transit_candidate import OpenBaoDecryptRequest, OpenBaoDecryptResponse


_PATHS = frozenset({
    "/v1/transit/decrypt/rsc-authentication-idempotency",
    "/v1/transit/decrypt/rsc-material-request-contact",
})
_MAX_BODY = 16 * 1024
_MAX_WIRE = 32 * 1024
_MAX_TOKEN = 4096
_MAX_REQUEST = 32 * 1024
_TOKEN = re.compile(rb"[A-Za-z0-9._-]{10,4096}")


class OpenBaoTransportUnavailable(RuntimeError):
    """Fixed safe failure; no path, token, payload or original error chain."""


@dataclass(frozen=True, slots=True)
class _Identity:
    api_uid: int
    bao_uid: int
    bao_gid: int
    shared_gid: int
    projector_uid: int


def _absolute_parts(value: str) -> tuple[str, ...]:
    if (
        type(value) is not str or not value.startswith("/run/")
        or str(PurePosixPath(value)) != value or ".." in PurePosixPath(value).parts
        or len(value) > 512 or len(PurePosixPath(value).name) > 80
        or any(ord(c) < 33 or ord(c) > 126 for c in value)
    ):
        raise ValueError("invalid path")
    parts = PurePosixPath(value).parts[1:]
    if len(parts) < 3:
        raise ValueError("private directory required")
    return parts


def _assert_api_identity(identity: _Identity) -> None:
    if (
        sys.platform != "linux" or not hasattr(socket, "SO_PEERCRED")
        or os.getuid() != identity.api_uid or os.geteuid() != identity.api_uid
        or identity.shared_gid not in {os.getegid(), *os.getgroups()}
    ):
        raise ValueError("unavailable runtime identity")


def _directory_stat(value, *, owner: int, group: int | None, private: bool):
    if (
        not stat.S_ISDIR(value.st_mode) or value.st_uid != owner
        or (group is not None and value.st_gid != group)
        or (private and stat.S_IMODE(value.st_mode) != 0o750)
        or (not private and stat.S_IMODE(value.st_mode) & 0o022)
    ):
        raise ValueError("uncontrolled directory")
    return value.st_dev, value.st_ino, value.st_uid, value.st_gid, stat.S_IMODE(value.st_mode)


def _open_directory(parts: tuple[str, ...], *, owner: int, group: int):
    """Walk using anchored fds, refusing symlinks in every path component."""
    descriptor = os.open("/", os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW)
    chain = []
    try:
        chain.append(_directory_stat(os.fstat(descriptor), owner=0, group=None, private=False))
        for index, part in enumerate(parts[:-1]):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC | os.O_NOFOLLOW,
                            dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
            private = index == len(parts) - 2
            chain.append(_directory_stat(os.fstat(descriptor), owner=owner if private else 0,
                                         group=group if private else None, private=private))
        return descriptor, tuple(chain)
    except BaseException:
        os.close(descriptor)
        raise


def _socket_stat(value, identity: _Identity):
    if (
        not stat.S_ISSOCK(value.st_mode) or stat.S_IMODE(value.st_mode) != 0o660
        or value.st_uid != identity.bao_uid or value.st_gid != identity.shared_gid
        or value.st_nlink != 1
    ):
        raise ValueError("invalid endpoint")
    return value.st_dev, value.st_ino, value.st_uid, value.st_gid, value.st_mode, value.st_ctime_ns


def _token_stat(value, identity: _Identity):
    if (
        not stat.S_ISREG(value.st_mode) or stat.S_IMODE(value.st_mode) != 0o440
        or value.st_uid != identity.projector_uid or value.st_gid != identity.shared_gid
        or value.st_nlink != 1 or not 10 <= value.st_size <= _MAX_TOKEN
    ):
        raise ValueError("invalid projected credential")
    return value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns


def _peer_identity(endpoint: socket.socket):
    pid, uid, gid = struct.unpack("3i", endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i")))
    if pid <= 0:
        raise ValueError("invalid peer")
    return uid, gid


def _check_deadline(deadline):
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise TimeoutError("deadline exceeded")
    return remaining


class _DeadlineSocket(socket.socket):
    def __init__(self, deadline):
        super().__init__(socket.AF_UNIX, socket.SOCK_STREAM)
        self._deadline, self._received = deadline, 0

    def connect(self, address):
        self.settimeout(_check_deadline(self._deadline))
        return super().connect(address)

    def sendall(self, data, flags=0):
        self.settimeout(_check_deadline(self._deadline))
        return super().sendall(data, flags)

    def recv_into(self, buffer, nbytes=0, flags=0):
        self.settimeout(_check_deadline(self._deadline))
        count = super().recv_into(buffer, nbytes, flags)
        self._received += count
        if self._received > _MAX_WIRE:
            raise ValueError("response exceeds wire bound")
        return count


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, transport, deadline):
        super().__init__("localhost")
        self._transport, self._deadline = transport, deadline

    def connect(self):
        self.sock = self._transport._connect(self._deadline)


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON key")
        result[key] = value
    return result


def _invalid_constant(_):
    raise ValueError("invalid JSON constant")


class OpenBaoUnixDecryptTransport:
    """Fixed decrypt-only HTTP over Linux UDS, with peer and file provenance."""

    def __init__(
        self, *, socket_path: str, token_file: str,
        api_uid: int, bao_uid: int, bao_gid: int, shared_gid: int, token_projector_uid: int,
    ) -> None:
        valid = False
        try:
            if (
                any(type(value) is not int or value < 0 for value in
                    (api_uid, bao_uid, bao_gid, shared_gid, token_projector_uid))
                or min(api_uid, bao_uid, bao_gid, token_projector_uid, shared_gid) == 0
                or len({api_uid, bao_uid, token_projector_uid}) != 3
            ):
                raise ValueError("invalid identity")
            self._identity = _Identity(api_uid, bao_uid, bao_gid, shared_gid, token_projector_uid)
            _assert_api_identity(self._identity)
            self._socket_parts, self._token_parts = _absolute_parts(socket_path), _absolute_parts(token_file)
            if self._socket_parts[:-1] == self._token_parts[:-1]:
                raise ValueError("separate controlled directories required")
            self._socket_chain, self._socket_identity = self._endpoint_snapshot()
            descriptor, self._token_chain = _open_directory(
                self._token_parts, owner=token_projector_uid, group=shared_gid,
            )
            os.close(descriptor)
            valid = True
        except Exception:
            pass
        if not valid:
            raise OpenBaoTransportUnavailable("OpenBao decrypt transport is unavailable")

    def _endpoint_snapshot(self):
        descriptor, chain = _open_directory(
            self._socket_parts, owner=self._identity.bao_uid, group=self._identity.shared_gid,
        )
        try:
            return chain, _socket_stat(os.stat(self._socket_parts[-1], dir_fd=descriptor, follow_symlinks=False), self._identity)
        finally:
            os.close(descriptor)

    def _read_token(self, deadline):
        _check_deadline(deadline)
        descriptor, chain = _open_directory(
            self._token_parts, owner=self._identity.projector_uid, group=self._identity.shared_gid,
        )
        token_fd = None
        try:
            if chain != self._token_chain:
                raise ValueError("projected credential directory changed")
            token_fd = os.open(self._token_parts[-1], os.O_RDONLY | os.O_CLOEXEC | os.O_NOFOLLOW | os.O_NONBLOCK,
                               dir_fd=descriptor)
            before = _token_stat(os.fstat(token_fd), self._identity)
            if not os.fstatvfs(token_fd).f_flag & os.ST_RDONLY:
                raise ValueError("projection is not read-only")
            token = os.read(token_fd, _MAX_TOKEN + 1)
            if (
                len(token) != before[2] or _TOKEN.fullmatch(token) is None
                or _token_stat(os.fstat(token_fd), self._identity) != before
                or _token_stat(os.stat(self._token_parts[-1], dir_fd=descriptor, follow_symlinks=False), self._identity) != before
            ):
                raise ValueError("projected credential changed or malformed")
            current_fd, current_chain = _open_directory(
                self._token_parts, owner=self._identity.projector_uid, group=self._identity.shared_gid,
            )
            try:
                if current_chain != self._token_chain:
                    raise ValueError("projected credential directory changed")
            finally:
                os.close(current_fd)
            _check_deadline(deadline)
            return token.decode("ascii")
        finally:
            if token_fd is not None:
                os.close(token_fd)
            os.close(descriptor)

    def _connect(self, deadline):
        _assert_api_identity(self._identity)
        descriptor, chain = _open_directory(
            self._socket_parts, owner=self._identity.bao_uid, group=self._identity.shared_gid,
        )
        endpoint = None
        try:
            before = _socket_stat(os.stat(self._socket_parts[-1], dir_fd=descriptor, follow_symlinks=False), self._identity)
            if chain != self._socket_chain or before != self._socket_identity:
                raise ValueError("endpoint changed")
            endpoint = _DeadlineSocket(deadline)
            # /proc/self/fd anchors the connect to the already verified parent;
            # ordinary pathname re-resolution cannot replace an ancestor.
            endpoint.connect(f"/proc/self/fd/{descriptor}/{self._socket_parts[-1]}")
            if (
                _peer_identity(endpoint) != (self._identity.bao_uid, self._identity.bao_gid)
                or _socket_stat(os.stat(self._socket_parts[-1], dir_fd=descriptor, follow_symlinks=False), self._identity) != before
                or self._endpoint_snapshot() != (self._socket_chain, self._socket_identity)
            ):
                raise ValueError("endpoint identity changed")
            _check_deadline(deadline)
            return endpoint
        except BaseException:
            if endpoint is not None:
                endpoint.close()
            raise
        finally:
            os.close(descriptor)

    def decrypt(self, *, request, timeout_seconds):
        result = conn = response = None
        try:
            if (
                type(request) is not OpenBaoDecryptRequest or request.path not in _PATHS
                or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 3.0
            ):
                raise ValueError("invalid request")
            deadline = time.monotonic() + timeout_seconds
            if any(type(value) is not str or not 1 <= len(value) <= 8192 for value in
                   (request.ciphertext, request.context, request.associated_data)):
                raise ValueError("invalid request")
            payload = json.dumps({"ciphertext": request.ciphertext, "context": request.context,
                                  "associated_data": request.associated_data},
                                 separators=(",", ":"), ensure_ascii=True).encode("ascii")
            if len(payload) > _MAX_REQUEST:
                raise ValueError("request too large")
            token = self._read_token(deadline)
            conn = _UnixConnection(self, deadline)
            conn.request("POST", request.path, body=payload, headers={
                "X-Vault-Token": token, "Content-Type": "application/json",
                "Accept": "application/json", "Connection": "close",
            })
            response = conn.getresponse()
            names = [name.lower() for name, _ in response.getheaders()]
            if (
                len(names) != len(set(names)) or "transfer-encoding" in names or "location" in names
                or not 200 <= response.status <= 599 or 300 <= response.status < 400
                or response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json"
            ):
                raise ValueError("invalid response")
            size = response.getheader("Content-Length", "")
            if re.fullmatch(r"0|[1-9][0-9]{0,5}", size) is None or not 1 <= int(size) <= _MAX_BODY:
                raise ValueError("invalid response")
            raw = response.read(int(size) + 1)
            if len(raw) != int(size):
                raise ValueError("invalid response")
            _check_deadline(deadline)
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object, parse_constant=_invalid_constant)
            if type(document) is not dict:
                raise ValueError("invalid response")
            _check_deadline(deadline)
            result = OpenBaoDecryptResponse(response.status, document)
        except Exception:
            pass
        finally:
            try:
                if response is not None:
                    response.close()
                if conn is not None:
                    conn.close()
            except Exception:
                result = None
        if result is None:
            raise OpenBaoTransportUnavailable("OpenBao decrypt transport is unavailable")
        return result
