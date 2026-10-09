"""Synthetic proof-input validity; does not replace its real PostgreSQL leg."""
import base64
from hashlib import sha256
from uuid import UUID

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
import pytest

from app.formal_services.material_request_contact import (
    material_request_contact_aad, validate_material_request_contact_envelope,
)
import pg16_contact_envelope_gate as proof


def test_real_pg_fixture_has_valid_aad_nonce_and_authenticated_ciphertext():
    request = UUID('10000000-0000-4000-8000-000000000001')
    person = UUID('20000000-0000-4000-8000-000000000001')
    envelope = proof.v2(request, person)
    validate_material_request_contact_envelope(envelope)
    aad = material_request_contact_aad(envelope, request_id=request, requester_person_id=person)
    assert sha256(aad).hexdigest() == envelope['aad_sha256']
    assert AESGCM(bytes(range(32))).decrypt(base64.b64decode(envelope['nonce_b64']),
        base64.b64decode(envelope['ciphertext_b64']), aad) == b'{"synthetic":"contact-only"}'


@pytest.mark.parametrize('field,value', [
    ('environment', 'staging'), ('provider_instance_id', 'isolated-other-instance'),
    ('application_key_version', proof.VERSION + 1), ('transit_key_version', 3),
])
def test_wrong_pin_fixture_recomputes_aad_so_pin_rejection_cannot_be_aad_failure(field, value):
    request = UUID('10000000-0000-4000-8000-000000000001')
    person = UUID('20000000-0000-4000-8000-000000000001')
    envelope = proof.v2(request, person, **{field: value})
    validate_material_request_contact_envelope(envelope)
    aad = material_request_contact_aad(envelope, request_id=request, requester_person_id=person)
    assert sha256(aad).hexdigest() == envelope['aad_sha256']
    assert AESGCM(bytes(range(32))).decrypt(base64.b64decode(envelope['nonce_b64']),
        base64.b64decode(envelope['ciphertext_b64']), aad) == b'{"synthetic":"contact-only"}'
    assert envelope['aad_sha256'] != proof.v2(request, person)['aad_sha256']
