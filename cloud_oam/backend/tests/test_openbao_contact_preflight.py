"""An unavailable Transit key must stop contact-guarded HTTP mutations."""

from __future__ import annotations

from unittest.mock import Mock

import pytest

from app.formal_services.material_request_contact_openbao import (
    OpenBaoContactUnavailable,
    OpenBaoMaterialRequestContactCipher,
)
from app.openbao_transit_candidate import OpenBaoDecryptResponse, context_b64
from app.routers import formal_material_requests as router
from test_formal_material_request_api import (
    REQUEST_ID,
    STEP_1_ID,
    STEP_3_ID,
    _approval_body,
    _external_body,
    _headers,
    api_client,
)
from test_material_request_contact_v2 import make_cipher


def test_active_version_and_construction_stay_network_free_but_preflight_resolves():
    cipher, transport = make_cipher()
    assert type(cipher) is OpenBaoMaterialRequestContactCipher
    assert cipher.active_key_version() == 3
    assert transport.calls == []
    assert cipher.preflight_active_key() == 3
    assert len(transport.calls) == 1
    assert transport.calls[0].context == context_b64(cipher.active_binding().coordinate)
    assert transport.calls[0].path == "/v1/transit/decrypt/rsc-material-request-contact"
    assert transport.calls[0].ciphertext.startswith("vault:v2:")


def test_preflight_does_not_cache_a_dek_or_hide_a_later_provider_failure():
    cipher, transport = make_cipher()
    before_attributes = frozenset(vars(cipher))
    assert cipher.preflight_active_key() == 3
    assert frozenset(vars(cipher)) == before_attributes
    transport.failure = True
    with pytest.raises(OpenBaoContactUnavailable) as error:
        cipher.preflight_active_key()
    assert len(transport.calls) == 2
    assert str(error.value) == "contact key preflight is unavailable"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None
    assert cipher.active_key_version() == 3
    assert len(transport.calls) == 2


class _MalformedKeyTransport:
    def __init__(self):
        self.calls = []

    def decrypt(self, *, request, timeout_seconds):
        self.calls.append(request)
        return OpenBaoDecryptResponse(200, {"data": {"plaintext": "YQ=="}})


def test_preflight_rejects_invalid_dek_with_static_exception_chain():
    transport = _MalformedKeyTransport()
    cipher, _ = make_cipher(transport=transport)
    with pytest.raises(OpenBaoContactUnavailable) as error:
        cipher.preflight_active_key()
    assert len(transport.calls) == 1
    assert str(error.value) == "contact key preflight is unavailable"
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


@pytest.mark.parametrize("operation", ["submit", "approval", "external-evidence"])
@pytest.mark.parametrize("failure", ["provider-unavailable", "invalid-dek"])
def test_openbao_failure_stops_contact_guarded_mutations_before_domain_write(
    api_client, monkeypatch, operation, failure
):
    client, db, _principal, _legacy_cipher, settings = api_client
    if failure == "invalid-dek":
        cipher, transport = make_cipher(transport=_MalformedKeyTransport())
    else:
        cipher, transport = make_cipher()
        transport.failure = True
    assert type(cipher) is OpenBaoMaterialRequestContactCipher
    for field, value in {
        "material_request_contact_encryption_provider": "openbao_transit_v1",
        "openbao_provider_instance_id": "isolated-contact-test",
        "openbao_encrypted_data_key_registry_path": "/run/bao/registry.json",
        "openbao_socket_path": "/run/bao/runtime.sock",
        "openbao_token_file": "/run/bao/token",
        "openbao_api_uid": 1001,
        "openbao_bao_uid": 1002,
        "openbao_bao_gid": 1003,
        "openbao_shared_gid": 1004,
        "openbao_token_projector_uid": 1005,
    }.items():
        monkeypatch.setattr(settings, field, value)
    assert settings.material_request_contact_kms_configuration_ready()
    client.app.dependency_overrides[router.get_material_request_contact_cipher] = lambda: cipher
    assert cipher.active_key_version() == 3
    assert transport.calls == []
    service = Mock(side_effect=AssertionError("provider failure must stop before domain service"))
    if operation == "submit":
        monkeypatch.setattr(router.draft_service, "submit_material_request", service)
        path = f"/api/v1/material-requests/{REQUEST_ID}/submit"
        payload = {"expected_version": 0}
    elif operation == "approval":
        monkeypatch.setattr(router.approval_service, "decide_material_request_approval", service)
        path = f"/api/v1/material-requests/{REQUEST_ID}/approval-steps/{STEP_1_ID}/decision"
        payload = _approval_body()
    else:
        monkeypatch.setattr(router.approval_service, "register_external_approval_evidence", service)
        path = f"/api/v1/material-requests/{REQUEST_ID}/approval-steps/{STEP_3_ID}/external-evidence"
        payload = _external_body()

    response = client.post(path, json=payload, headers=_headers("bao-preflight"))
    service.assert_not_called()
    db.commit.assert_not_called()
    db.rollback.assert_called_once_with()
    assert len(transport.calls) == 1
    assert response.status_code == 503
    assert response.json()["detail"] == {
        "code": "material_request_contact_kms_unavailable",
        "category": "service_unavailable",
        "message": "联系人 KMS 加密服务不可用，本次操作未完成",
    }
    assert "synthetic-secret-marker" not in response.text
