"""Isolated OpenBao Transit contract candidate; NOT a production provider.

No Settings/factory/readiness registration, files, credentials, default network
client, database access or release-readiness result exists here. The caller
must supply independently reviewed immutable pins and an explicit transport.
This prepares a future additive provider contract without reinterpreting any
Aliyun row, response, application envelope or historical migration.
"""

from __future__ import annotations

import base64
import binascii
from dataclasses import dataclass
import hashlib
import hmac
import json
import re
from typing import Final, Literal, Mapping, Protocol


PROVIDER: Final = "openbao_transit_v1"
Purpose = Literal["authentication_idempotency", "material_request_contact"]
_KEY_NAMES: Final = {
    "authentication_idempotency": "rsc-authentication-idempotency",
    "material_request_contact": "rsc-material-request-contact",
}
_ENVIRONMENTS: Final = frozenset({"development", "test", "staging", "production"})
_INSTANCE = re.compile(r"[a-z0-9][a-z0-9-]{2,62}", re.ASCII)
_SHA256 = re.compile(r"[0-9a-f]{64}", re.ASCII)
_CIPHERTEXT = re.compile(r"vault:v([1-9][0-9]{0,9}):([A-Za-z0-9+/]+={0,2})", re.ASCII)
_MAX_VERSION = 2_147_483_647
_DECRYPT_TIMEOUT_SECONDS = 3.0


class OpenBaoCandidateConfigurationError(RuntimeError):
    """Static non-sensitive error; never includes supplied material."""


class OpenBaoCandidateUnavailable(RuntimeError):
    """Unknown/decrypt-unavailable, not proof of authentication failure."""


def _version(value: object) -> None:
    if type(value) is not int or not 1 <= value <= _MAX_VERSION:
        raise OpenBaoCandidateConfigurationError("OpenBao version is invalid")


@dataclass(frozen=True, slots=True, repr=False)
class OpenBaoKeyCoordinate:
    purpose: Purpose
    environment: str
    provider_instance_id: str
    application_key_version: int

    def __post_init__(self) -> None:
        if type(self.purpose) is not str or self.purpose not in _KEY_NAMES:
            raise OpenBaoCandidateConfigurationError("OpenBao purpose is invalid")
        if type(self.environment) is not str or self.environment not in _ENVIRONMENTS:
            raise OpenBaoCandidateConfigurationError("OpenBao environment is invalid")
        if (
            type(self.provider_instance_id) is not str
            or _INSTANCE.fullmatch(self.provider_instance_id) is None
        ):
            raise OpenBaoCandidateConfigurationError("OpenBao instance is invalid")
        _version(self.application_key_version)

    @property
    def key_path(self) -> str:
        return "transit/keys/" + _KEY_NAMES[self.purpose]

    @property
    def decrypt_path(self) -> str:
        return "/v1/transit/decrypt/" + _KEY_NAMES[self.purpose]


def _binding_bytes(coordinate: OpenBaoKeyCoordinate, schema: str) -> bytes:
    if type(coordinate) is not OpenBaoKeyCoordinate:
        raise OpenBaoCandidateConfigurationError("OpenBao coordinate is invalid")
    # Different schema names domain-separate derivation from AEAD binding.
    return json.dumps(
        {
            "schema": schema,
            "application": "cloud_oam",
            "provider": PROVIDER,
            "environment": coordinate.environment,
            "provider_instance_id": coordinate.provider_instance_id,
            "purpose": coordinate.purpose,
            "key_path": coordinate.key_path,
            "application_key_version": coordinate.application_key_version,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    ).encode("ascii")


def context_b64(coordinate: OpenBaoKeyCoordinate) -> str:
    """Canonical derivation context shared with the isolated real prototype."""
    return base64.b64encode(
        _binding_bytes(coordinate, "rsc.openbao.derivation-context.v1")
    ).decode("ascii")


def associated_data_b64(coordinate: OpenBaoKeyCoordinate) -> str:
    """Canonical wrap AAD, distinct from the application's envelope AAD."""
    return base64.b64encode(
        _binding_bytes(coordinate, "rsc.openbao.wrap-aad.v1")
    ).decode("ascii")


def _canonical_base64(value: object) -> bytes:
    if type(value) is not str or not value or len(value) > 8192:
        raise ValueError("invalid material")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("invalid material") from None
    if base64.b64encode(decoded).decode("ascii") != value:
        raise ValueError("invalid material")
    return decoded


@dataclass(frozen=True, slots=True, repr=False)
class OpenBaoWrappedKey:
    coordinate: OpenBaoKeyCoordinate
    ciphertext: str
    transit_key_version: int

    def __post_init__(self) -> None:
        if type(self.coordinate) is not OpenBaoKeyCoordinate:
            raise OpenBaoCandidateConfigurationError("OpenBao coordinate is invalid")
        _version(self.transit_key_version)
        match = (
            _CIPHERTEXT.fullmatch(self.ciphertext)
            if type(self.ciphertext) is str and len(self.ciphertext) <= 8192
            else None
        )
        try:
            if match is None or int(match[1]) != self.transit_key_version:
                raise ValueError("invalid material")
            # aes256-gcm96 wraps exactly a 32-byte DEK: nonce + DEK + tag.
            if len(_canonical_base64(match[2])) != 12 + 32 + 16:
                raise ValueError("invalid material")
        except ValueError:
            raise OpenBaoCandidateConfigurationError(
                "OpenBao wrapped key is invalid"
            ) from None


@dataclass(frozen=True, slots=True, repr=False)
class OpenBaoReviewedPin:
    """Expected external review input, never derived from a decrypt response.

    The caller must obtain this from a separately reviewed immutable manifest.
    This in-memory candidate neither persists pins nor proves that provenance.
    """

    coordinate: OpenBaoKeyCoordinate
    transit_key_version: int
    ciphertext_sha256: str
    context_sha256: str
    associated_data_sha256: str

    def __post_init__(self) -> None:
        if type(self.coordinate) is not OpenBaoKeyCoordinate:
            raise OpenBaoCandidateConfigurationError("OpenBao pin coordinate is invalid")
        _version(self.transit_key_version)
        for value in (
            self.ciphertext_sha256, self.context_sha256, self.associated_data_sha256
        ):
            if type(value) is not str or _SHA256.fullmatch(value) is None:
                raise OpenBaoCandidateConfigurationError("OpenBao pin is invalid")


@dataclass(frozen=True, slots=True, repr=False)
class OpenBaoDecryptRequest:
    path: str
    ciphertext: str
    context: str
    associated_data: str


@dataclass(frozen=True, slots=True, repr=False)
class OpenBaoDecryptResponse:
    status_code: int
    body: Mapping[str, object]


class OpenBaoDecryptTransport(Protocol):
    """Explicit bounded transport; must not log material, retry or redirect.

    Implementations must enforce timeout_seconds across connect/read, verify
    reviewed instance routing (UDS ownership or TLS identity), bound response
    bytes, reject duplicate JSON keys and omit request/response/error logging.
    These obligations are not established by a fake test transport.
    """

    def decrypt(
        self, *, request: OpenBaoDecryptRequest, timeout_seconds: float
    ) -> OpenBaoDecryptResponse: ...


class OpenBaoTransitCandidate:
    """Decrypt-only candidate. No active-version selection or production use."""

    def __init__(
        self,
        *,
        environment: str,
        provider_instance_id: str,
        entries: tuple[OpenBaoWrappedKey, ...],
        reviewed_pins: tuple[OpenBaoReviewedPin, ...],
        transport: OpenBaoDecryptTransport,
    ) -> None:
        # Validate expected deployment scope independently of the registry.
        OpenBaoKeyCoordinate("authentication_idempotency", environment, provider_instance_id, 1)
        if (
            type(entries) is not tuple or not 1 <= len(entries) <= 64
            or type(reviewed_pins) is not tuple or len(reviewed_pins) != len(entries)
            or not callable(getattr(transport, "decrypt", None))
        ):
            raise OpenBaoCandidateConfigurationError("OpenBao candidate inputs are invalid")
        pins: dict[OpenBaoKeyCoordinate, OpenBaoReviewedPin] = {}
        for pin in reviewed_pins:
            if type(pin) is not OpenBaoReviewedPin or pin.coordinate in pins:
                raise OpenBaoCandidateConfigurationError("OpenBao pins are ambiguous")
            pins[pin.coordinate] = pin
        self._entries: dict[tuple[str, int], OpenBaoWrappedKey] = {}
        fingerprints: set[str] = set()
        for entry in entries:
            if type(entry) is not OpenBaoWrappedKey:
                raise OpenBaoCandidateConfigurationError("OpenBao entry is invalid")
            coordinate = entry.coordinate
            index = (coordinate.purpose, coordinate.application_key_version)
            if (
                coordinate.environment != environment
                or coordinate.provider_instance_id != provider_instance_id
                or index in self._entries
            ):
                raise OpenBaoCandidateConfigurationError("OpenBao registry scope is ambiguous")
            pin = pins.get(coordinate)
            fingerprint = hashlib.sha256(entry.ciphertext.encode("ascii")).hexdigest()
            expected_context = hashlib.sha256(base64.b64decode(context_b64(coordinate))).hexdigest()
            expected_aad = hashlib.sha256(base64.b64decode(associated_data_b64(coordinate))).hexdigest()
            if (
                pin is None or pin.transit_key_version != entry.transit_key_version
                or not hmac.compare_digest(pin.ciphertext_sha256, fingerprint)
                or not hmac.compare_digest(pin.context_sha256, expected_context)
                or not hmac.compare_digest(pin.associated_data_sha256, expected_aad)
                or fingerprint in fingerprints
            ):
                raise OpenBaoCandidateConfigurationError("OpenBao reviewed pin mismatch")
            self._entries[index] = entry
            fingerprints.add(fingerprint)
        self._transport = transport

    def resolve(self, purpose: Purpose, application_key_version: int) -> bytes:
        """Resolve exact purpose/version to request-memory AES-256 key bytes.

        Transit plaintext replies do not attest Aliyun KeyId/KeyVersionId.
        Identity instead depends on prevalidated pins, the original ciphertext
        prefix, exact request path/context/AAD and authenticated transport.
        """
        if type(purpose) is not str or purpose not in _KEY_NAMES:
            raise OpenBaoCandidateUnavailable("OpenBao data key is unavailable")
        if type(application_key_version) is not int or application_key_version < 1:
            raise OpenBaoCandidateUnavailable("OpenBao data key is unavailable")
        entry = self._entries.get((purpose, application_key_version))
        if entry is None:
            raise OpenBaoCandidateUnavailable("OpenBao data key is unavailable")
        request = OpenBaoDecryptRequest(
            path=entry.coordinate.decrypt_path,
            ciphertext=entry.ciphertext,
            context=context_b64(entry.coordinate),
            associated_data=associated_data_b64(entry.coordinate),
        )
        # Catch inside, raise outside: no raw transport exception is retained
        # in __context__/__cause__ for ordinary API exception logging to leak.
        plaintext: bytes | None = None
        try:
            response = self._transport.decrypt(
                request=request, timeout_seconds=_DECRYPT_TIMEOUT_SECONDS
            )
            if (
                type(response) is not OpenBaoDecryptResponse
                or type(response.status_code) is not int or response.status_code != 200
                or not isinstance(response.body, Mapping)
                or response.body.get("errors") or response.body.get("warnings")
            ):
                raise ValueError("unavailable")
            data = response.body.get("data")
            if not isinstance(data, Mapping) or set(data) != {"plaintext"}:
                raise ValueError("unavailable")
            decoded = _canonical_base64(data["plaintext"])
            if len(decoded) != 32:
                raise ValueError("unavailable")
            plaintext = decoded
        except Exception:
            pass
        if plaintext is None:
            raise OpenBaoCandidateUnavailable("OpenBao data key is unavailable")
        return plaintext
