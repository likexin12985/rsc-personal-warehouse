"""Synthetic contact-v2 contracts, not runtime custody or deployment evidence."""

import base64
from dataclasses import replace
import hashlib
import json
import socket
from types import SimpleNamespace
import uuid

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import pytest

from app.formal_services.material_request_contact import (
    MaterialRequestContactProtectionError,
    material_request_contact_aad,
    protect_material_request_contact,
    reveal_material_request_contact,
    validate_material_request_contact_envelope,
)
from app.formal_services.material_request_contact_openbao import (
    OpenBaoContactBinding, OpenBaoContactUnavailable,
    OpenBaoMaterialRequestContactCipher, V2_ENVELOPE_KEYS, contact_v2_aad,
)
from app.openbao_transit_candidate import (
    OpenBaoCandidateConfigurationError, OpenBaoDecryptResponse,
    OpenBaoKeyCoordinate, OpenBaoReviewedPin, OpenBaoWrappedKey,
    associated_data_b64, context_b64,
)


REQUEST = uuid.UUID("10000000-0000-4000-8000-000000000001")
PERSON = uuid.UUID("20000000-0000-4000-8000-000000000001")
SECRET = b"contact-test-secret-at-least-32-bytes"
INSTANCE = "isolated-contact-test"
V1_AAD = (
    b"cloud_oam.material_request.contact.envelope.v1\0"
    b"request_id=10000000-0000-4000-8000-000000000001\0"
    b"requester_person_id=20000000-0000-4000-8000-000000000001"
)
V2_AAD = (
    b"cloud_oam.material_request.contact.envelope.v2\0"
    b"provider=openbao_transit_v1\0purpose=material_request_contact\0"
    b"environment=test\0provider_instance_id=isolated-contact-test\0"
    b"key_path=transit/keys/rsc-material-request-contact\0"
    b"application_key_version=3\0transit_key_version=2\0"
    b"request_id=10000000-0000-4000-8000-000000000001\0"
    b"requester_person_id=20000000-0000-4000-8000-000000000001"
)
V1_VECTOR = {
    "schema": "rsc.material_request_contact.v1", "provider": "aliyun_kms",
    "kms_key_id": "kms/contact/test", "key_version": 1,
    "ciphertext_b64": "TDM/HDRO5WK1BXGKm1O6PXrBPD8n75qU0L7IaHmeRrD+ZUTk4MRlrDf6zXE/4s0p2MbV4tLUfzkdRBkJKhg=",
    "nonce_b64": "MDEyMzQ1Njc4OWFi",
    "aad_sha256": "5f5ce3b4faa354b454867a84bdcc63756fe26631d41e548eda61f11ac3c9885c",
    "mobile_hmac": "hmac:1:7862bfdcd2b16255f3643eb2592cfcfd7161bd81cfdfe745961c7ef3deb9aa49",
    "contact_hmac": "hmac:1:4e39a6d9b13ca7493beeda6e1560e7607a00ae81ac56d12864bbb2209dda362b",
}


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def deny(*args, **kwargs):
        pytest.fail("contact-v2 focused tests cannot access a network")
    monkeypatch.setattr(socket, "create_connection", deny)
    monkeypatch.setattr(socket.socket, "connect", deny)


class LegacyCipher:
    def __init__(self):
        self.calls = 0

    def active_key_version(self):
        return 1

    def encrypt(self, plaintext, *, aad, key_version):
        assert key_version == 1
        nonce = b"0123456789ab"
        return SimpleNamespace(
            ciphertext=AESGCM(bytes(range(32))).encrypt(nonce, plaintext, aad),
            nonce=nonce, key_version=1,
        )

    def decrypt(self, ciphertext, *, nonce, aad, key_version):
        self.calls += 1
        assert key_version == 1
        return AESGCM(bytes(range(32))).decrypt(nonce, ciphertext, aad)


class Transport:
    def __init__(self):
        self.calls = []
        self.failure = False

    def decrypt(self, *, request, timeout_seconds):
        self.calls.append(request)
        assert timeout_seconds == 3.0
        if self.failure:
            raise RuntimeError("synthetic-secret-marker")
        # Distinct synthetic 256-bit DEKs per application-version context.
        key = hashlib.sha256(base64.b64decode(request.context)).digest()
        return OpenBaoDecryptResponse(200, {"data": {"plaintext": base64.b64encode(key).decode()}})


def make_cipher(*, active=3, legacy=None, entries=None, pins=None, transport=None):
    if entries is None:
        entries = tuple(
            OpenBaoWrappedKey(
                OpenBaoKeyCoordinate("material_request_contact", "test", INSTANCE, version),
                f"vault:v{transit}:" + base64.b64encode(bytes([version]) * 60).decode(), transit,
            ) for version, transit in ((2, 1), (3, 2))
        )
    if pins is None:
        # Synthetic review fixture only. Production must SELECT independent C1
        # pins/claims, never compute expected pins from a registry like this.
        pins = tuple(OpenBaoReviewedPin(
            entry.coordinate, entry.transit_key_version,
            hashlib.sha256(entry.ciphertext.encode()).hexdigest(),
            hashlib.sha256(base64.b64decode(context_b64(entry.coordinate))).hexdigest(),
            hashlib.sha256(base64.b64decode(associated_data_b64(entry.coordinate))).hexdigest(),
        ) for entry in entries)
    transport = transport or Transport()
    cipher = OpenBaoMaterialRequestContactCipher(
        environment="test", provider_instance_id=INSTANCE,
        active_application_key_version=active, entries=entries, reviewed_pins=pins,
        transport=transport, legacy_cipher=legacy,
        legacy_kms_key_id="kms/contact/test" if legacy is not None else None,
    )
    return cipher, transport


def protect(cipher, *, request=REQUEST, person=PERSON):
    return protect_material_request_contact(
        cipher=cipher, kms_key_id="kms/contact/test", mobile_hmac_secret=SECRET,
        mobile_hash_version=1, request_id=request, requester_person_id=person,
        name="张工程师", mobile="13800138000",
    )


def reveal(cipher, envelope, **kwargs):
    return reveal_material_request_contact(
        cipher=cipher, kms_key_id="kms/contact/test", mobile_hmac_secret=SECRET,
        mobile_hash_version=1, request_id=REQUEST, requester_person_id=PERSON,
        envelope=envelope, **kwargs,
    )


def test_v1_fixed_vector_and_v2_fixed_business_aad_are_independent():
    assert protect(LegacyCipher()) == V1_VECTOR
    assert material_request_contact_aad(V1_VECTOR, request_id=REQUEST, requester_person_id=PERSON) == V1_AAD
    cipher, _ = make_cipher()
    aad = contact_v2_aad(cipher.active_binding(), REQUEST, PERSON)
    assert aad == V2_AAD
    assert hashlib.sha256(aad).hexdigest() == "d0aeb7a4d27a3920e42e2978357c36efa87544405637f0572898fd62981a4fbf"
    assert aad != base64.b64decode(associated_data_b64(cipher.active_binding().coordinate))
    assert aad != base64.b64decode(context_b64(cipher.active_binding().coordinate))


def test_v2_real_aes_roundtrip_exact_shape_and_retained_versions():
    legacy = LegacyCipher()
    current, transport = make_cipher(legacy=legacy)
    old, _ = make_cipher(active=2)
    envelopes = (protect(old), protect(current))
    assert transport.calls == [transport.calls[0]]
    for envelope in envelopes:
        assert set(envelope) == V2_ENVELOPE_KEYS
        assert "张工程师" not in json.dumps(envelope, ensure_ascii=False)
        assert "13800138000" not in json.dumps(envelope)
        assert reveal(current, envelope) == {"name": "张工程师", "mobile": "13800138000"}
    assert [e["application_key_version"] for e in envelopes] == [2, 3]
    assert [e["transit_key_version"] for e in envelopes] == [1, 2]
    assert reveal(current, V1_VECTOR)["name"] == "张工程师"
    assert legacy.calls == 1
    assert len(transport.calls) == 3  # v1 never uses OpenBao.


@pytest.mark.parametrize("changes", [
    {"schema": "rsc.material_request_contact.v1"}, {"schema": "unknown"},
    {"provider": "aliyun_kms"}, {"provider": "unknown"},
    {"purpose": "authentication_idempotency"}, {"key_path": "transit/keys/other"},
    {"environment": "Test"}, {"provider_instance_id": "UpperCase"},
    {"provider_instance_id": "abc\0suffix"}, {"provider_instance_id": "中文实例"},
    {"provider_instance_id": "a" * 64}, {"application_key_version": True},
    {"application_key_version": "3"}, {"application_key_version": 0},
    {"application_key_version": 2_147_483_648}, {"transit_key_version": True},
    {"transit_key_version": 0}, {"transit_key_version": 2_147_483_648},
    {"kms_key_id": "kms/mixed"}, {"key_version": 3}, {"mobile": "13800138000"},
    {"nonce_b64": "MDEyMzQ1Njc4OWFi="}, {"ciphertext_b64": "AB=="},
    {"aad_sha256": "A" * 64}, {"mobile_hmac": "hmac:0:" + "a" * 64},
])
def test_strict_v2_shape_and_encoding(changes):
    cipher, _ = make_cipher()
    bad = {**protect(cipher), **changes}
    with pytest.raises(MaterialRequestContactProtectionError):
        validate_material_request_contact_envelope(bad)


@pytest.mark.parametrize("changes", [
    {"environment": "production"}, {"provider_instance_id": "other-instance"},
    {"application_key_version": 4}, {"transit_key_version": 1},
])
def test_v2_coordinate_tampering_rejected_before_transport_even_with_rehashed_aad(changes):
    cipher, transport = make_cipher()
    envelope = {**protect(cipher), **changes}
    envelope["aad_sha256"] = hashlib.sha256(material_request_contact_aad(
        envelope, request_id=REQUEST, requester_person_id=PERSON,
    )).hexdigest()
    before = len(transport.calls)
    with pytest.raises(MaterialRequestContactProtectionError):
        reveal(cipher, envelope)
    assert len(transport.calls) == before


def test_request_person_and_aead_ciphertext_bindings_fail_closed():
    cipher, transport = make_cipher()
    for request, person in ((uuid.uuid4(), PERSON), (REQUEST, uuid.uuid4())):
        envelope = protect(cipher, request=request, person=person)
        before = len(transport.calls)
        with pytest.raises(MaterialRequestContactProtectionError):
            reveal(cipher, envelope)
        assert len(transport.calls) == before
    envelope = protect(cipher)
    raw = bytearray(base64.b64decode(envelope["ciphertext_b64"]))
    raw[0] ^= 1
    envelope["ciphertext_b64"] = base64.b64encode(raw).decode()
    with pytest.raises(MaterialRequestContactProtectionError):
        reveal(cipher, envelope)


def test_missing_legacy_and_unavailable_v2_never_fall_back():
    cipher, transport = make_cipher()
    with pytest.raises(MaterialRequestContactProtectionError):
        reveal(cipher, V1_VECTOR)
    assert transport.calls == []
    envelope = protect(cipher)
    legacy = LegacyCipher()
    with pytest.raises(MaterialRequestContactProtectionError):
        reveal(legacy, envelope)
    assert legacy.calls == 0
    dual, transport = make_cipher(legacy=legacy)
    transport.failure = True
    with pytest.raises(MaterialRequestContactProtectionError) as failure:
        reveal(dual, envelope)
    assert "synthetic-secret-marker" not in str(failure.value)
    assert legacy.calls == 0


def test_pin_mismatch_and_missing_active_version_fail_before_transport():
    original, _ = make_cipher()
    binding = original.active_binding()
    entry = OpenBaoWrappedKey(binding.coordinate, "vault:v2:" + "A" * 80, 2)
    pin = OpenBaoReviewedPin(binding.coordinate, 2, "0" * 64, "0" * 64, "0" * 64)
    transport = Transport()
    with pytest.raises(OpenBaoCandidateConfigurationError):
        make_cipher(entries=(entry,), pins=(pin,), transport=transport)
    with pytest.raises(OpenBaoContactUnavailable):
        make_cipher(active=999, transport=transport)
    assert transport.calls == []
