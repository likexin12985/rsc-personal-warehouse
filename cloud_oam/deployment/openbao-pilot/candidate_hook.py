"""Synthetic LocalBao hook and bounded UDS transport, never production wiring.

Run only through isolated_openbao_harness.py --hook
candidate_hook:verify_candidate_adapter. No CLI, default endpoint, credential
discovery or production identity renewal is provided. Only stdlib plus the
isolated, otherwise unregistered application candidate is imported.
"""

from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import http.client
import json
import math
import os
from pathlib import Path
import re
import socket
import stat
import time
import uuid

from app.openbao_transit_candidate import (
    OpenBaoCandidateConfigurationError,
    OpenBaoDecryptRequest,
    OpenBaoDecryptResponse,
    OpenBaoKeyCoordinate,
    OpenBaoReviewedPin,
    OpenBaoTransitCandidate,
    OpenBaoWrappedKey,
    associated_data_b64,
    context_b64,
)


_PURPOSES = ("authentication_idempotency", "material_request_contact")
_PATHS = frozenset(OpenBaoKeyCoordinate(p, "test", "isolated-openbao-2-7-1", 1).decrypt_path for p in _PURPOSES)
_INSTANCE = "isolated-openbao-2-7-1"
_MAX_BODY = 16 * 1024
_MAX_WIRE = 32 * 1024


class PrototypeTransportUnavailable(RuntimeError):
    """Fixed safe boundary, including timeout, permission and malformed data."""


def _require(condition, label):
    if not condition:
        raise RuntimeError("isolated OpenBao check failed: candidate " + label)


def _socket_identity(path):
    path = Path(path)
    parent, endpoint = path.parent.lstat(), path.lstat()
    if (
        not path.is_absolute() or not stat.S_ISDIR(parent.st_mode)
        or stat.S_IMODE(parent.st_mode) != 0o700
        or parent.st_uid != os.getuid()
        or not stat.S_ISSOCK(endpoint.st_mode)
        or stat.S_IMODE(endpoint.st_mode) != 0o600
        or endpoint.st_uid != os.getuid()
    ):
        raise ValueError("invalid prototype socket")
    return endpoint.st_dev, endpoint.st_ino


class _DeadlineSocket(socket.socket):
    def __init__(self, deadline):
        super().__init__(socket.AF_UNIX, socket.SOCK_STREAM)
        self._deadline = deadline
        self._received = 0

    def _remaining(self):
        remaining = self._deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("prototype deadline exceeded")
        self.settimeout(remaining)

    def connect(self, address):
        self._remaining()
        return super().connect(address)

    def sendall(self, data, flags=0):
        self._remaining()
        return super().sendall(data, flags)

    def recv_into(self, buffer, nbytes=0, flags=0):
        self._remaining()
        count = super().recv_into(buffer, nbytes, flags)
        self._received += count
        if self._received > _MAX_WIRE:
            raise ValueError("prototype response too large")
        return count


class _UnixConnection(http.client.HTTPConnection):
    def __init__(self, path, identity, deadline):
        super().__init__("localhost")
        self._path, self._identity, self._deadline = path, identity, deadline

    def connect(self):
        if _socket_identity(self._path) != self._identity:
            raise ValueError("prototype socket changed")
        self.sock = _DeadlineSocket(self._deadline)
        self.sock.connect(str(self._path))


def _strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate response key")
        result[key] = value
    return result


def _invalid_constant(_value):
    raise ValueError("invalid JSON constant")


class PrototypeUnixDecryptTransport:
    """Explicit disposable-UDS-only transport; no retry/redirect/token renewal."""

    def __init__(self, *, socket_path, token):
        identity = None
        try:
            if type(token) is not str or re.fullmatch(r"[A-Za-z0-9._-]{10,2048}", token) is None:
                raise ValueError("invalid prototype token")
            identity = _socket_identity(socket_path)
        except Exception:
            pass
        if identity is None:
            raise PrototypeTransportUnavailable("prototype decrypt unavailable")
        self._path = Path(socket_path)
        self._identity = identity
        self._token = token

    def decrypt(self, *, request, timeout_seconds):
        result = None
        conn = None
        response = None
        try:
            if (
                type(request) is not OpenBaoDecryptRequest or request.path not in _PATHS
                or type(timeout_seconds) not in (int, float)
                or not math.isfinite(timeout_seconds) or not 0 < timeout_seconds <= 3.0
            ):
                raise ValueError("invalid prototype request")
            if any(type(v) is not str or not 1 <= len(v) <= 8192 for v in
                   (request.ciphertext, request.context, request.associated_data)):
                raise ValueError("invalid prototype request")
            payload = json.dumps({
                "ciphertext": request.ciphertext, "context": request.context,
                "associated_data": request.associated_data,
            }, separators=(",", ":"), ensure_ascii=True).encode("ascii")
            deadline = time.monotonic() + timeout_seconds
            conn = _UnixConnection(self._path, self._identity, deadline)
            conn.request("POST", request.path, body=payload, headers={
                "X-Vault-Token": self._token, "Content-Type": "application/json",
                "Accept": "application/json", "Connection": "close",
            })
            response = conn.getresponse()
            headers = response.getheaders()
            names = [name.lower() for name, _ in headers]
            if (
                len(names) != len(set(names)) or "transfer-encoding" in names
                or "location" in names or 300 <= response.status < 400
                or response.getheader("Content-Type", "").split(";", 1)[0].strip().lower() != "application/json"
            ):
                raise ValueError("invalid prototype response")
            size = response.getheader("Content-Length", "")
            if re.fullmatch(r"0|[1-9][0-9]{0,5}", size) is None or not 1 <= int(size) <= _MAX_BODY:
                raise ValueError("invalid prototype response")
            raw = response.read(int(size) + 1)
            if len(raw) != int(size) or time.monotonic() >= deadline:
                raise ValueError("invalid prototype response")
            document = json.loads(raw.decode("utf-8"), object_pairs_hook=_strict_object,
                                  parse_constant=_invalid_constant)
            if type(document) is not dict or time.monotonic() >= deadline:
                raise ValueError("invalid prototype response")
            result = OpenBaoDecryptResponse(response.status, document)
        except Exception:
            pass
        finally:
            # HTTPResponse owns a makefile reference even after HTTPConnection
            # closes a Connection: close socket. Explicitly close both on early
            # rejection; otherwise a retained exception traceback keeps it live.
            try:
                if response is not None:
                    response.close()
                if conn is not None:
                    conn.close()
            except Exception:
                result = None
        if result is None:
            # Raise outside the handler; sensitive transport errors have no chain.
            raise PrototypeTransportUnavailable("prototype decrypt unavailable")
        return result


def _read_or_create_keys(server):
    status, mounts = server.root("GET", "/v1/sys/mounts")
    _require(status == 200 and isinstance(mounts.get("data"), dict), "mount inspection")
    transit = mounts["data"].get("transit/")
    if transit is None:
        _require(server.root("POST", "/v1/sys/mounts/transit", {"type": "transit"})[0] == 204, "new mount")
    else:
        _require(transit.get("type") == "transit", "existing mount type")
    expected = {"type": "aes256-gcm96", "derived": True, "exportable": False,
                "allow_plaintext_backup": False, "convergent_encryption": False}
    for purpose in _PURPOSES:
        coordinate = OpenBaoKeyCoordinate(purpose, "test", _INSTANCE, 1)
        path = "/v1/" + coordinate.key_path
        status, value = server.root("GET", path)
        if status == 404:
            _require(server.root("POST", path, expected)[0] in (200, 204), "new purpose key")
            status, value = server.root("GET", path)
        _require(status == 200 and isinstance(value.get("data"), dict), "key inspection")
        data = value["data"]
        _require(all(type(data.get(k)) is type(v) and data[k] == v for k, v in expected.items()), "immutable key settings")
        _require(data.get("deletion_allowed") is False, "key deletion disabled")


def _create_runtime_token(server):
    policy_name = "isolated-candidate-" + uuid.uuid4().hex[:12]
    policy = {"path": {path.removeprefix("/v1/"): {"capabilities": ["update"]} for path in sorted(_PATHS)}}
    _require(server.root("PUT", "/v1/sys/policies/acl/" + policy_name,
                         {"policy": json.dumps(policy, sort_keys=True)})[0] == 204, "new exact policy")
    status, response = server.root("POST", "/v1/auth/token/create", {
        "policies": [policy_name], "no_default_policy": True, "ttl": "5m", "renewable": False,
    })
    _require(status == 200 and response["auth"]["policies"] == [policy_name], "exact temporary token")
    return policy_name, response["auth"]["client_token"]


def _plaintext(value):
    data = value.get("data")
    _require(isinstance(data, dict) and set(data) == {"plaintext"}, "reference decrypt fields")
    raw = base64.b64decode(data["plaintext"], validate=True)
    _require(len(raw) == 32 and base64.b64encode(raw).decode("ascii") == data["plaintext"], "reference AES256 key")
    return raw


def _execute(server):
    socket_path = Path(server.socket_path)
    _require(socket_path.parent.name.startswith("rsc-bao-"), "disposable directory")
    _socket_identity(socket_path)
    _read_or_create_keys(server)
    records, pins = [], []
    # Pins are fixed from original generation responses BEFORE candidate use.
    for purpose in _PURPOSES:
        for app_version in (1, 2):
            coordinate = OpenBaoKeyCoordinate(purpose, "test", _INSTANCE, app_version)
            context, aad = context_b64(coordinate), associated_data_b64(coordinate)
            key_name = coordinate.key_path.rsplit("/", 1)[1]
            status, response = server.root("POST", "/v1/transit/datakey/wrapped/" + key_name,
                {"bits": 256, "context": context, "associated_data": aad})
            _require(status == 200 and isinstance(response.get("data"), dict)
                     and "plaintext" not in response["data"], "wrapped-only generation")
            ciphertext = response["data"]["ciphertext"]
            matched = re.fullmatch(r"vault:v([1-9][0-9]*):[A-Za-z0-9+/=]+", ciphertext)
            _require(matched is not None, "actual Transit prefix")
            entry = OpenBaoWrappedKey(coordinate, ciphertext, int(matched[1]))
            pins.append(OpenBaoReviewedPin(
                coordinate, entry.transit_key_version,
                hashlib.sha256(ciphertext.encode("ascii")).hexdigest(),
                hashlib.sha256(base64.b64decode(context)).hexdigest(),
                hashlib.sha256(base64.b64decode(aad)).hexdigest(),
            ))
            status, clear_response = server.root("POST", coordinate.decrypt_path,
                {"ciphertext": ciphertext, "context": context, "associated_data": aad})
            _require(status == 200, "independent reference decrypt")
            records.append((entry, _plaintext(clear_response)))
    policy_name, token = _create_runtime_token(server)
    revoke_attempted = False
    try:
        transport = PrototypeUnixDecryptTransport(socket_path=socket_path, token=token)
        entries = tuple(entry for entry, _ in records)
        loader = OpenBaoTransitCandidate(environment="test", provider_instance_id=_INSTANCE,
            entries=entries, reviewed_pins=tuple(pins), transport=transport)
        for entry, expected in reversed(records):
            _require(loader.resolve(entry.coordinate.purpose, entry.coordinate.application_key_version) == expected,
                     "current and historical application keys")
        original = entries[0]
        request = OpenBaoDecryptRequest(original.coordinate.decrypt_path, original.ciphertext,
            context_b64(original.coordinate), associated_data_b64(original.coordinate))
        rejected = []
        for label, changed in (
            ("wrong_context", replace(request, context=base64.b64encode(b"wrong-context").decode())),
            ("wrong_aad", replace(request, associated_data=base64.b64encode(b"wrong-aad").decode())),
            ("cross_purpose", replace(request, path=OpenBaoKeyCoordinate(_PURPOSES[1], "test", _INSTANCE, 1).decrypt_path)),
        ):
            _require(transport.decrypt(request=changed, timeout_seconds=3).status_code == 400, "negative " + label)
            rejected.append(label)
        wrong_pin_rejected = False
        try:
            OpenBaoTransitCandidate(environment="test", provider_instance_id=_INSTANCE,
                entries=entries, reviewed_pins=(replace(pins[0], ciphertext_sha256="0" * 64), *pins[1:]),
                transport=transport)
        except OpenBaoCandidateConfigurationError:
            wrong_pin_rejected = True
        _require(wrong_pin_rejected, "wrong pin rejected before decryption")
        revoke_attempted = True
        _require(server.root("POST", "/v1/auth/token/revoke", {"token": token})[0] == 204, "temporary token revoked")
        _require(transport.decrypt(request=request, timeout_seconds=3).status_code == 403, "revoked token denied")
        return {
            "syntheticOnly": True, "productionConfigured": False,
            "purposeCount": 2, "applicationVersionsPerPurpose": [1, 2],
            "wrappedDataKeys": 4, "actualTransitVersions": sorted({e.transit_key_version for e in entries}),
            "candidateDecryptsMatchIndependentReference": True,
            "canonicalContextAndAAD": True, "remoteNegativeChecks": rejected,
            "independentPinMismatchRejected": True, "temporaryTokenRevoked": True,
            "productionIdentityRenewalTested": False,
            "candidateTransportBudgetSeconds": 3, "existingKeyConfigurationMutated": False,
        }
    finally:
        if not revoke_attempted:
            # A known newly created token may be cleaned once; no unknown write replay.
            server.root("POST", "/v1/auth/token/revoke", {"token": token})
        server.root("DELETE", "/v1/sys/policies/acl/" + policy_name)


def verify_candidate_adapter(server):
    """Trusted hook for an already-owned synthetic LocalBao, never a deployer."""
    result = None
    try:
        result = _execute(server)
    except Exception:
        pass
    if result is None:
        raise RuntimeError("isolated OpenBao check failed: candidate hook")
    return result
