"""PII envelope boundary for formal material-request contact snapshots.

The demand service must never persist a plaintext contact name or mobile in a
request, command ledger, audit event or outbox payload.  This module accepts a
request-scoped cipher supplied by the application composition root, encrypts a
canonical contact document with AAD bound to the exact request/person, and
returns only a strictly validated envelope plus a domain-separated mobile
HMAC.  It does not load KMS keys or read secrets from settings itself.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import hmac
import json
import re
from collections.abc import Mapping
from typing import Any, Protocol
import uuid


CONTACT_ENVELOPE_SCHEMA = "rsc.material_request_contact.v1"
CONTACT_ENCRYPTION_PROVIDER = "aliyun_kms"
_EXPECTED_KEYS = frozenset(
    {
        "schema",
        "provider",
        "kms_key_id",
        "key_version",
        "ciphertext_b64",
        "nonce_b64",
        "aad_sha256",
        "mobile_hmac",
        "contact_hmac",
    }
)
_SAFE_KMS_KEY_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_./:@+-]{2,255}$", re.ASCII)
_HASH_EVIDENCE = re.compile(r"^hmac:([1-9][0-9]{0,9}):([0-9a-f]{64})$")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CANONICAL_MOBILE = re.compile(r"^\+?[0-9]{6,20}$")
_PLACEHOLDERS = ("replace-with", "replace_me", "replace-me", "change-me", "changeme")


class MaterialRequestContactProtectionError(RuntimeError):
    """The contact snapshot cannot cross the persistence boundary safely."""


class _EncryptedContact(Protocol):
    ciphertext: bytes
    nonce: bytes
    key_version: int


class MaterialRequestContactCipher(Protocol):
    """Request-scoped, version-aware authenticated encryption adapter."""

    def active_key_version(self) -> int: ...

    def encrypt(
        self,
        plaintext: bytes,
        *,
        aad: bytes,
        key_version: int,
    ) -> _EncryptedContact: ...

    def decrypt(
        self,
        ciphertext: bytes,
        *,
        nonce: bytes,
        aad: bytes,
        key_version: int,
    ) -> bytes: ...


def protect_material_request_contact(
    *,
    cipher: MaterialRequestContactCipher,
    kms_key_id: str,
    mobile_hmac_secret: bytes | str,
    mobile_hash_version: int,
    request_id: uuid.UUID,
    requester_person_id: uuid.UUID,
    name: str,
    mobile: str,
) -> dict[str, Any]:
    """Return an encryption-only JSON envelope for one contact snapshot."""

    checked_key_id = _kms_key_id(kms_key_id)
    checked_secret = _hmac_secret(mobile_hmac_secret)
    checked_hash_version = _positive_integer(
        "mobile_hash_version", mobile_hash_version
    )
    checked_request_id = _uuid("request_id", request_id)
    checked_person_id = _uuid("requester_person_id", requester_person_id)
    checked_name = _text("name", name, 120)
    checked_mobile = _mobile(mobile)
    aad = _contact_aad(checked_request_id, checked_person_id)
    plaintext = json.dumps(
        {"mobile": checked_mobile, "name": checked_name},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    try:
        key_version = _positive_integer(
            "key_version", cipher.active_key_version()
        )
        encrypted = cipher.encrypt(
            plaintext,
            aad=aad,
            key_version=key_version,
        )
    except MaterialRequestContactProtectionError:
        raise
    except Exception as exc:
        raise MaterialRequestContactProtectionError(
            "material request contact encryption is unavailable"
        ) from exc
    if (
        not isinstance(encrypted.ciphertext, bytes)
        or len(encrypted.ciphertext) < 17
        or not isinstance(encrypted.nonce, bytes)
        or len(encrypted.nonce) != 12
        or encrypted.key_version != key_version
    ):
        raise MaterialRequestContactProtectionError(
            "material request contact encryption returned an invalid envelope"
        )

    digest = hmac.new(
        checked_secret,
        (
            "cloud_oam.material_request.contact.mobile.v1\0"
            + checked_mobile
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    contact_digest = hmac.new(
        checked_secret,
        b"cloud_oam.material_request.contact.identity.v1\0" + plaintext,
        hashlib.sha256,
    ).hexdigest()
    envelope = {
        "schema": CONTACT_ENVELOPE_SCHEMA,
        "provider": CONTACT_ENCRYPTION_PROVIDER,
        "kms_key_id": checked_key_id,
        "key_version": key_version,
        "ciphertext_b64": base64.b64encode(encrypted.ciphertext).decode("ascii"),
        "nonce_b64": base64.b64encode(encrypted.nonce).decode("ascii"),
        "aad_sha256": hashlib.sha256(aad).hexdigest(),
        "mobile_hmac": f"hmac:{checked_hash_version}:{digest}",
        "contact_hmac": f"hmac:{checked_hash_version}:{contact_digest}",
    }
    return validate_material_request_contact_envelope(envelope)


def reveal_material_request_contact(
    *,
    cipher: MaterialRequestContactCipher,
    kms_key_id: str,
    mobile_hmac_secret: bytes | str,
    mobile_hash_version: int,
    request_id: uuid.UUID,
    requester_person_id: uuid.UUID,
    envelope: Mapping[str, Any],
) -> dict[str, str]:
    """Decrypt one owner-authorized draft snapshot and reprove its binding.

    The caller remains responsible for authorizing the exact current requester
    and for preventing caching of the plaintext response.  This boundary only
    accepts the canonical envelope emitted by
    :func:`protect_material_request_contact`; it verifies the request/person
    AAD and both independent HMAC facts after authenticated decryption.
    """

    checked_envelope = validate_material_request_contact_envelope(envelope)
    checked_key_id = _kms_key_id(kms_key_id)
    checked_secret = _hmac_secret(mobile_hmac_secret)
    checked_hash_version = _positive_integer(
        "mobile_hash_version", mobile_hash_version
    )
    checked_request_id = _uuid("request_id", request_id)
    checked_person_id = _uuid("requester_person_id", requester_person_id)
    historical_decrypt = None
    if checked_envelope["kms_key_id"] != checked_key_id:
        historical_decrypt = getattr(cipher, "decrypt_for_kms_key_id", None)
        if not callable(historical_decrypt):
            raise MaterialRequestContactProtectionError(
                "material request contact KMS binding is invalid"
            )
    mobile_match = _HASH_EVIDENCE.fullmatch(checked_envelope["mobile_hmac"])
    contact_match = _HASH_EVIDENCE.fullmatch(checked_envelope["contact_hmac"])
    if (
        mobile_match is None
        or contact_match is None
        or int(mobile_match.group(1)) != checked_hash_version
        or int(contact_match.group(1)) != checked_hash_version
    ):
        raise MaterialRequestContactProtectionError(
            "material request contact hash version is unavailable"
        )

    aad = _contact_aad(checked_request_id, checked_person_id)
    if not hmac.compare_digest(
        checked_envelope["aad_sha256"], hashlib.sha256(aad).hexdigest()
    ):
        raise MaterialRequestContactProtectionError(
            "material request contact AAD binding is invalid"
        )
    ciphertext = _strict_base64(
        "ciphertext_b64", checked_envelope["ciphertext_b64"]
    )
    nonce = _strict_base64("nonce_b64", checked_envelope["nonce_b64"])
    try:
        if historical_decrypt is None:
            plaintext = cipher.decrypt(
                ciphertext,
                nonce=nonce,
                aad=aad,
                key_version=checked_envelope["key_version"],
            )
        else:
            plaintext = historical_decrypt(
                checked_envelope["kms_key_id"],
                ciphertext,
                nonce=nonce,
                aad=aad,
                key_version=checked_envelope["key_version"],
            )
    except MaterialRequestContactProtectionError:
        raise
    except Exception as exc:
        raise MaterialRequestContactProtectionError(
            "material request contact decryption is unavailable"
        ) from exc
    if not isinstance(plaintext, bytes) or not 2 <= len(plaintext) <= 512:
        raise MaterialRequestContactProtectionError(
            "material request contact plaintext is invalid"
        )
    try:
        document = json.loads(plaintext.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MaterialRequestContactProtectionError(
            "material request contact plaintext is invalid"
        ) from exc
    if not isinstance(document, dict) or set(document) != {"mobile", "name"}:
        raise MaterialRequestContactProtectionError(
            "material request contact plaintext shape is invalid"
        )
    name = _text("name", document.get("name"), 120)
    mobile = _mobile(document.get("mobile"))
    canonical = json.dumps(
        {"mobile": mobile, "name": name},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    if not hmac.compare_digest(plaintext, canonical):
        raise MaterialRequestContactProtectionError(
            "material request contact plaintext is not canonical"
        )
    expected_mobile_hmac = hmac.new(
        checked_secret,
        ("cloud_oam.material_request.contact.mobile.v1\0" + mobile).encode(
            "utf-8"
        ),
        hashlib.sha256,
    ).hexdigest()
    expected_contact_hmac = hmac.new(
        checked_secret,
        b"cloud_oam.material_request.contact.identity.v1\0" + canonical,
        hashlib.sha256,
    ).hexdigest()
    if not hmac.compare_digest(
        mobile_match.group(2), expected_mobile_hmac
    ) or not hmac.compare_digest(contact_match.group(2), expected_contact_hmac):
        raise MaterialRequestContactProtectionError(
            "material request contact integrity evidence is invalid"
        )
    return {"name": name, "mobile": mobile}


def validate_material_request_contact_envelope(
    value: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate and detach an envelope before it is assigned to an ORM row."""

    if not isinstance(value, Mapping) or set(value) != _EXPECTED_KEYS:
        raise MaterialRequestContactProtectionError(
            "material request contact envelope shape is invalid"
        )
    if value.get("schema") != CONTACT_ENVELOPE_SCHEMA:
        raise MaterialRequestContactProtectionError(
            "material request contact envelope schema is invalid"
        )
    if value.get("provider") != CONTACT_ENCRYPTION_PROVIDER:
        raise MaterialRequestContactProtectionError(
            "material request contact envelope provider is invalid"
        )
    key_id = _kms_key_id(value.get("kms_key_id"))
    key_version = _positive_integer("key_version", value.get("key_version"))
    ciphertext = _strict_base64("ciphertext_b64", value.get("ciphertext_b64"))
    nonce = _strict_base64("nonce_b64", value.get("nonce_b64"))
    if len(ciphertext) < 17 or len(nonce) != 12:
        raise MaterialRequestContactProtectionError(
            "material request contact envelope ciphertext is invalid"
        )
    aad_sha256 = value.get("aad_sha256")
    mobile_hmac = value.get("mobile_hmac")
    contact_hmac = value.get("contact_hmac")
    if not isinstance(aad_sha256, str) or _SHA256.fullmatch(aad_sha256) is None:
        raise MaterialRequestContactProtectionError(
            "material request contact envelope AAD hash is invalid"
        )
    if not isinstance(mobile_hmac, str) or _HASH_EVIDENCE.fullmatch(mobile_hmac) is None:
        raise MaterialRequestContactProtectionError(
            "material request contact envelope mobile hash is invalid"
        )
    if not isinstance(contact_hmac, str) or _HASH_EVIDENCE.fullmatch(contact_hmac) is None:
        raise MaterialRequestContactProtectionError(
            "material request contact envelope identity hash is invalid"
        )
    # Construct a fresh object so a mutable caller mapping cannot change the
    # already-validated document after it is assigned to the persistence row.
    return {
        "schema": CONTACT_ENVELOPE_SCHEMA,
        "provider": CONTACT_ENCRYPTION_PROVIDER,
        "kms_key_id": key_id,
        "key_version": key_version,
        "ciphertext_b64": base64.b64encode(ciphertext).decode("ascii"),
        "nonce_b64": base64.b64encode(nonce).decode("ascii"),
        "aad_sha256": aad_sha256,
        "mobile_hmac": mobile_hmac,
        "contact_hmac": contact_hmac,
    }


def _contact_aad(request_id: uuid.UUID, person_id: uuid.UUID) -> bytes:
    return (
        "cloud_oam.material_request.contact.envelope.v1\0"
        f"request_id={request_id}\0requester_person_id={person_id}"
    ).encode("ascii")


def _strict_base64(field: str, value: Any) -> bytes:
    if not isinstance(value, str) or not value:
        raise MaterialRequestContactProtectionError(f"{field} is invalid")
    try:
        decoded = base64.b64decode(value, validate=True)
    except (binascii.Error, ValueError) as exc:
        raise MaterialRequestContactProtectionError(f"{field} is invalid") from exc
    if base64.b64encode(decoded).decode("ascii") != value:
        raise MaterialRequestContactProtectionError(f"{field} is not canonical")
    return decoded


def _kms_key_id(value: Any) -> str:
    checked = value.strip() if isinstance(value, str) else ""
    if (
        _SAFE_KMS_KEY_ID.fullmatch(checked) is None
        or any(marker in checked.lower() for marker in _PLACEHOLDERS)
    ):
        raise MaterialRequestContactProtectionError("KMS key identifier is invalid")
    return checked


def _hmac_secret(value: bytes | str) -> bytes:
    encoded = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(encoded, bytes) or len(encoded) < 32:
        raise MaterialRequestContactProtectionError(
            "material request contact HMAC secret is unavailable"
        )
    return encoded


def _positive_integer(field: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise MaterialRequestContactProtectionError(f"{field} must be positive")
    return value


def _uuid(field: str, value: Any) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        raise MaterialRequestContactProtectionError(f"{field} must be a UUID")
    return value


def _text(field: str, value: Any, maximum: int) -> str:
    checked = value.strip() if isinstance(value, str) else ""
    if not checked or len(checked) > maximum or any(ord(char) < 32 for char in checked):
        raise MaterialRequestContactProtectionError(f"{field} is invalid")
    return checked


def _mobile(value: Any) -> str:
    checked = _text("mobile", value, 32)
    canonical = checked.replace(" ", "").replace("-", "")
    if _CANONICAL_MOBILE.fullmatch(canonical) is None:
        raise MaterialRequestContactProtectionError("mobile is invalid")
    return canonical


__all__ = [
    "CONTACT_ENCRYPTION_PROVIDER",
    "CONTACT_ENVELOPE_SCHEMA",
    "MaterialRequestContactCipher",
    "MaterialRequestContactProtectionError",
    "protect_material_request_contact",
    "reveal_material_request_contact",
    "validate_material_request_contact_envelope",
]
