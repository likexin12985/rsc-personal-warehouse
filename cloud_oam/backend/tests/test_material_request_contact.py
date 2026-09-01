from __future__ import annotations

from dataclasses import dataclass
import json
import uuid

import pytest

from app.formal_services.material_request_contact import (
    MaterialRequestContactProtectionError,
    protect_material_request_contact,
    reveal_material_request_contact,
    validate_material_request_contact_envelope,
)


REQUEST_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
PERSON_ID = uuid.UUID("20000000-0000-4000-8000-000000000001")
HMAC_SECRET = b"demand-contact-hmac-test-secret-32-bytes-minimum"


@dataclass(frozen=True)
class _Encrypted:
    ciphertext: bytes
    nonce: bytes
    key_version: int


class _Cipher:
    def __init__(self) -> None:
        self.plaintext: bytes | None = None
        self.aad: bytes | None = None

    def active_key_version(self) -> int:
        return 7

    def encrypt(self, plaintext: bytes, *, aad: bytes, key_version: int) -> _Encrypted:
        self.plaintext = plaintext
        self.aad = aad
        return _Encrypted(b"authenticated-ciphertext", b"0123456789ab", key_version)

    def decrypt(
        self,
        ciphertext: bytes,
        *,
        nonce: bytes,
        aad: bytes,
        key_version: int,
    ) -> bytes:
        assert ciphertext == b"authenticated-ciphertext"
        assert nonce == b"0123456789ab"
        assert aad == self.aad
        assert key_version == 7
        assert self.plaintext is not None
        return self.plaintext


def _protected() -> tuple[dict[str, object], _Cipher]:
    cipher = _Cipher()
    envelope = protect_material_request_contact(
        cipher=cipher,
        kms_key_id="kms-rsc-demand-contact",
        mobile_hmac_secret=HMAC_SECRET,
        mobile_hash_version=3,
        request_id=REQUEST_ID,
        requester_person_id=PERSON_ID,
        name="工程师甲",
        mobile="+8613860013800",
    )
    return envelope, cipher


def test_contact_is_encrypted_and_envelope_contains_no_plaintext() -> None:
    envelope, cipher = _protected()
    serialized = json.dumps(envelope, ensure_ascii=False, sort_keys=True)
    assert "工程师甲" not in serialized
    assert "+8613860013800" not in serialized
    assert set(envelope) == {
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
    assert envelope["provider"] == "aliyun_kms"
    assert envelope["key_version"] == 7
    assert str(envelope["mobile_hmac"]).startswith("hmac:3:")
    assert str(envelope["contact_hmac"]).startswith("hmac:3:")
    assert cipher.plaintext == (
        '{"mobile":"+8613860013800","name":"工程师甲"}'.encode("utf-8")
    )
    assert cipher.aad is not None
    assert str(REQUEST_ID).encode("ascii") in cipher.aad
    assert str(PERSON_ID).encode("ascii") in cipher.aad


def test_mobile_is_normalized_before_encryption_and_stable_hmac() -> None:
    formatted_cipher = _Cipher()
    formatted = protect_material_request_contact(
        cipher=formatted_cipher,
        kms_key_id="kms-rsc-demand-contact",
        mobile_hmac_secret=HMAC_SECRET,
        mobile_hash_version=3,
        request_id=REQUEST_ID,
        requester_person_id=PERSON_ID,
        name="工程师甲",
        mobile="+86 138-6001-3800",
    )
    canonical_cipher = _Cipher()
    canonical = protect_material_request_contact(
        cipher=canonical_cipher,
        kms_key_id="kms-rsc-demand-contact",
        mobile_hmac_secret=HMAC_SECRET,
        mobile_hash_version=3,
        request_id=REQUEST_ID,
        requester_person_id=PERSON_ID,
        name="工程师甲",
        mobile="+8613860013800",
    )

    assert formatted["mobile_hmac"] == canonical["mobile_hmac"]
    assert formatted["contact_hmac"] == canonical["contact_hmac"]
    assert formatted_cipher.plaintext == canonical_cipher.plaintext
    assert b"+8613860013800" in formatted_cipher.plaintext


def test_contact_reveal_reproves_request_person_and_integrity_bindings() -> None:
    envelope, cipher = _protected()

    assert reveal_material_request_contact(
        cipher=cipher,
        kms_key_id="kms-rsc-demand-contact",
        mobile_hmac_secret=HMAC_SECRET,
        mobile_hash_version=3,
        request_id=REQUEST_ID,
        requester_person_id=PERSON_ID,
        envelope=envelope,
    ) == {"name": "工程师甲", "mobile": "+8613860013800"}

    for overrides in (
        {"request_id": uuid.uuid4()},
        {"requester_person_id": uuid.uuid4()},
        {"kms_key_id": "kms-rsc-demand-contact-other"},
        {"mobile_hash_version": 4},
        {"mobile_hmac_secret": b"another-contact-hmac-secret-32-bytes-minimum"},
    ):
        arguments = {
            "cipher": cipher,
            "kms_key_id": "kms-rsc-demand-contact",
            "mobile_hmac_secret": HMAC_SECRET,
            "mobile_hash_version": 3,
            "request_id": REQUEST_ID,
            "requester_person_id": PERSON_ID,
            "envelope": envelope,
            **overrides,
        }
        with pytest.raises(MaterialRequestContactProtectionError):
            reveal_material_request_contact(**arguments)


def test_contact_reveal_uses_exact_historical_kms_key_ring_binding() -> None:
    envelope, source_cipher = _protected()

    class _HistoricalKeyRing(_Cipher):
        def __init__(self) -> None:
            super().__init__()
            self.plaintext = source_cipher.plaintext
            self.aad = source_cipher.aad
            self.key_ids: list[str] = []

        def decrypt_for_kms_key_id(
            self,
            kms_key_id: str,
            ciphertext: bytes,
            *,
            nonce: bytes,
            aad: bytes,
            key_version: int,
        ) -> bytes:
            self.key_ids.append(kms_key_id)
            return self.decrypt(
                ciphertext,
                nonce=nonce,
                aad=aad,
                key_version=key_version,
            )

    ring = _HistoricalKeyRing()
    revealed = reveal_material_request_contact(
        cipher=ring,
        kms_key_id="kms-rsc-demand-contact-rotated",
        mobile_hmac_secret=HMAC_SECRET,
        mobile_hash_version=3,
        request_id=REQUEST_ID,
        requester_person_id=PERSON_ID,
        envelope=envelope,
    )

    assert revealed == {"name": "工程师甲", "mobile": "+8613860013800"}
    assert ring.key_ids == ["kms-rsc-demand-contact"]


def test_envelope_validation_rejects_plaintext_or_noncanonical_shapes() -> None:
    envelope, _cipher = _protected()
    with_plaintext = dict(envelope, mobile="+8613860013800")
    with pytest.raises(MaterialRequestContactProtectionError):
        validate_material_request_contact_envelope(with_plaintext)

    bad_nonce = dict(envelope, nonce_b64="YQ==")
    with pytest.raises(MaterialRequestContactProtectionError):
        validate_material_request_contact_envelope(bad_nonce)

    bad_hash = dict(envelope, mobile_hmac="hmac:0:" + "a" * 64)
    with pytest.raises(MaterialRequestContactProtectionError):
        validate_material_request_contact_envelope(bad_hash)


def test_protection_fails_closed_on_key_or_cipher_errors() -> None:
    with pytest.raises(MaterialRequestContactProtectionError):
        protect_material_request_contact(
            cipher=_Cipher(),
            kms_key_id="replace-with-kms-key",
            mobile_hmac_secret=HMAC_SECRET,
            mobile_hash_version=1,
            request_id=REQUEST_ID,
            requester_person_id=PERSON_ID,
            name="工程师甲",
            mobile="13860013800",
        )

    class _UnavailableCipher(_Cipher):
        def active_key_version(self) -> int:
            raise RuntimeError("kms unavailable")

    with pytest.raises(MaterialRequestContactProtectionError):
        protect_material_request_contact(
            cipher=_UnavailableCipher(),
            kms_key_id="kms-rsc-demand-contact",
            mobile_hmac_secret=HMAC_SECRET,
            mobile_hash_version=1,
            request_id=REQUEST_ID,
            requester_person_id=PERSON_ID,
            name="工程师甲",
            mobile="13860013800",
        )
