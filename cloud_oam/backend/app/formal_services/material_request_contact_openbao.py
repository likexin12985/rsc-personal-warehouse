"""Explicit contact-v2 cipher; no Settings, default transport or key registry.

The composition root must obtain reviewed pins from independent immutable DB
pins/claims. Registry entries supply wrapped data only. This candidate does not
establish that external provenance, enable a provider or read credentials.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from ..openbao_transit_candidate import (
    OpenBaoDecryptTransport,
    OpenBaoKeyCoordinate,
    OpenBaoReviewedPin,
    OpenBaoTransitCandidate,
    OpenBaoWrappedKey,
)


V2_SCHEMA = "rsc.material_request_contact.v2"
V2_PROVIDER = "openbao_transit_v1"
V2_PURPOSE = "material_request_contact"
V2_KEY_PATH = "transit/keys/rsc-material-request-contact"
V2_ENVELOPE_KEYS = frozenset({
    "schema", "provider", "purpose", "environment", "provider_instance_id",
    "key_path", "application_key_version", "transit_key_version",
    "ciphertext_b64", "nonce_b64", "aad_sha256", "mobile_hmac", "contact_hmac",
})


class OpenBaoContactUnavailable(RuntimeError):
    """Static error without transport, plaintext or key material."""


@dataclass(frozen=True, slots=True)
class OpenBaoContactBinding:
    coordinate: OpenBaoKeyCoordinate
    transit_key_version: int

    def __post_init__(self) -> None:
        if (
            type(self.coordinate) is not OpenBaoKeyCoordinate
            or self.coordinate.purpose != V2_PURPOSE
            or type(self.transit_key_version) is not int
            or not 1 <= self.transit_key_version <= 2_147_483_647
        ):
            raise OpenBaoContactUnavailable("contact key binding is invalid")

    def envelope_metadata(self) -> dict[str, object]:
        return {
            "schema": V2_SCHEMA,
            "provider": V2_PROVIDER,
            "purpose": V2_PURPOSE,
            "environment": self.coordinate.environment,
            "provider_instance_id": self.coordinate.provider_instance_id,
            "key_path": V2_KEY_PATH,
            "application_key_version": self.coordinate.application_key_version,
            "transit_key_version": self.transit_key_version,
        }


def contact_v2_aad(
    binding: OpenBaoContactBinding, request_id: uuid.UUID, person_id: uuid.UUID,
) -> bytes:
    """Frozen v2 business AAD, distinct from Transit derivation/wrap AAD.

    ASCII, literal NUL separators, decimal integer versions, canonical lowercase
    hyphenated UUIDs, and no trailing NUL. SQL must concatenate bytea NULs.
    """
    if (
        type(binding) is not OpenBaoContactBinding
        or not isinstance(request_id, uuid.UUID) or request_id.int == 0
        or not isinstance(person_id, uuid.UUID) or person_id.int == 0
    ):
        raise OpenBaoContactUnavailable("contact AAD binding is invalid")
    c = binding.coordinate
    return (
        "cloud_oam.material_request.contact.envelope.v2\0"
        f"provider={V2_PROVIDER}\0purpose={V2_PURPOSE}\0"
        f"environment={c.environment}\0provider_instance_id={c.provider_instance_id}\0"
        f"key_path={V2_KEY_PATH}\0application_key_version={c.application_key_version}\0"
        f"transit_key_version={binding.transit_key_version}\0"
        f"request_id={request_id}\0requester_person_id={person_id}"
    ).encode("ascii")


@dataclass(frozen=True, slots=True, repr=False)
class _EncryptedContact:
    ciphertext: bytes
    nonce: bytes
    key_version: int


class OpenBaoMaterialRequestContactCipher:
    """Explicit request-local v2 writes/historical v2 reads and v1 read delegate.

    Every historical coordinate must be configured and independently pinned;
    no latest-version selection, cross-provider fallback or response-derived
    version assertion exists. The optional legacy pair serves v1 reads only.
    """

    def __init__(
        self, *, environment: str, provider_instance_id: str,
        active_application_key_version: int,
        entries: tuple[OpenBaoWrappedKey, ...],
        reviewed_pins: tuple[OpenBaoReviewedPin, ...],
        transport: OpenBaoDecryptTransport,
        legacy_cipher: object | None = None,
        legacy_kms_key_id: str | None = None,
    ) -> None:
        self._resolver = OpenBaoTransitCandidate(
            environment=environment, provider_instance_id=provider_instance_id,
            entries=entries, reviewed_pins=reviewed_pins, transport=transport,
        )
        if any(entry.coordinate.purpose != V2_PURPOSE for entry in entries):
            raise OpenBaoContactUnavailable("contact key purpose is invalid")
        active = OpenBaoKeyCoordinate(
            V2_PURPOSE, environment, provider_instance_id, active_application_key_version,
        )
        self._bindings = {
            entry.coordinate: OpenBaoContactBinding(entry.coordinate, entry.transit_key_version)
            for entry in entries
        }
        if active not in self._bindings:
            raise OpenBaoContactUnavailable("active contact key is unavailable")
        if (legacy_cipher is None) != (legacy_kms_key_id is None):
            raise OpenBaoContactUnavailable("legacy contact reader is incomplete")
        self._active = self._bindings[active]
        self.legacy_cipher = legacy_cipher
        self.legacy_kms_key_id = legacy_kms_key_id

    def active_binding(self) -> OpenBaoContactBinding:
        return self._active

    def active_key_version(self) -> int:
        return self._active.coordinate.application_key_version

    def preflight_active_key(self) -> int:
        """Resolve the pinned active key before a guarded request mutation.

        Construction and version inspection remain network-free. This explicit
        request preflight retains no plaintext key and cannot reuse a previous
        successful probe after provider access becomes unavailable.
        """
        version = None
        try:
            self._key(self._active)
            version = self._active.coordinate.application_key_version
        except Exception:
            pass
        if version is None:
            # Raise outside the handler so raw transport errors cannot survive
            # in the public exception's cause or context chain.
            raise OpenBaoContactUnavailable("contact key preflight is unavailable")
        return version

    def _key(self, binding: OpenBaoContactBinding) -> bytes:
        if (
            type(binding) is not OpenBaoContactBinding
            or self._bindings.get(binding.coordinate) != binding
        ):
            raise OpenBaoContactUnavailable("contact key binding is unavailable")
        key = self._resolver.resolve(V2_PURPOSE, binding.coordinate.application_key_version)
        if type(key) is not bytes or len(key) != 32:
            raise OpenBaoContactUnavailable("contact key is unavailable")
        return key

    def encrypt(self, plaintext: bytes, *, aad: bytes, key_version: int) -> _EncryptedContact:
        result = None
        try:
            if type(key_version) is not int or key_version != self.active_key_version():
                raise ValueError("unavailable")
            nonce = os.urandom(12)
            result = _EncryptedContact(
                AESGCM(self._key(self._active)).encrypt(nonce, plaintext, aad), nonce, key_version,
            )
        except Exception:
            pass
        if result is None:
            raise OpenBaoContactUnavailable("contact encryption is unavailable")
        return result

    def decrypt_for_binding(
        self, binding: OpenBaoContactBinding, ciphertext: bytes, *, nonce: bytes, aad: bytes,
    ) -> bytes:
        plaintext = None
        try:
            plaintext = AESGCM(self._key(binding)).decrypt(nonce, ciphertext, aad)
        except Exception:
            pass
        if plaintext is None:
            raise OpenBaoContactUnavailable("contact decryption is unavailable")
        return plaintext
