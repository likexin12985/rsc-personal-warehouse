"""Request-boundary coverage for the startup-reviewed provider key runtime.

These tests exercise dependency composition and real HTTP auth paths. The
runtime's independent-pin checks and provider crypto are tested separately.
"""

from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI, Request
import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.formal_services.authentication_idempotency import (
    AuthenticationEncryptionKeyUnavailable,
    StaticAuthenticationKeyProvider,
    create_authentication_response_cipher,
)
from app.foundation_models import (
    AuditEvent,
    AuthIdempotencyOperation,
    AuthLoginRateLimitBucket,
    AuthRefreshToken,
    LoginChallenge,
    SmsChallengeDispatch,
    StateTransitionEvent,
)
from app.models import AuthSession
from app.routers import auth, formal_material_requests as contacts
from test_formal_auth_api import (
    AUTH_IDEMPOTENCY_TEST_KEY,
    MOBILE,
    SMS_CODE,
    _formal_mutation_headers,
    _login_sms_miniprogram,
    _login_sms_web,
    _request_sms,
    _seed_mobile_subject,
    api_world,
)
from test_formal_material_request_api import (
    _create_result,
    _draft_body,
    _headers as contact_headers,
    _settings as contact_settings,
    api_client,
)


_REAL_AUTHENTICATION_DEPENDENCY = auth._configured_authentication_response_cipher


def _request(runtime=...) -> Request:
    app = FastAPI()
    if runtime is not ...:
        app.state.provider_key_runtime = runtime
    return Request({"type": "http", "app": app})


def _cipher():
    return create_authentication_response_cipher(
        environment="test",
        key_provider=StaticAuthenticationKeyProvider(
            key=AUTH_IDEMPOTENCY_TEST_KEY, version=1
        ),
    )


@pytest.mark.parametrize("runtime", [..., None])
def test_production_auth_dependency_never_falls_back_without_runtime(
    monkeypatch, runtime
):
    monkeypatch.setattr(auth.settings, "environment", "production")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("an unreviewed registry must never be used")

    monkeypatch.setattr(auth, "create_production_authentication_cipher", forbidden)
    with pytest.raises(AuthenticationEncryptionKeyUnavailable) as error:
        _REAL_AUTHENTICATION_DEPENDENCY(_request(runtime))
    assert str(error.value) == "authentication encryption key is unavailable"


def test_auth_dependency_uses_each_application_runtime_and_request_local_cipher(
    monkeypatch,
):
    monkeypatch.setattr(auth.settings, "environment", "production")
    first_runtime, second_runtime = object(), object()
    calls = []

    def factory(settings, *, key_runtime):
        assert settings is auth.settings
        calls.append(key_runtime)
        return _cipher()

    monkeypatch.setattr(auth, "create_production_authentication_cipher", factory)
    first_request = _request(first_runtime)
    first = _REAL_AUTHENTICATION_DEPENDENCY(first_request)
    second = _REAL_AUTHENTICATION_DEPENDENCY(first_request)
    third = _REAL_AUTHENTICATION_DEPENDENCY(_request(second_runtime))
    assert calls == [first_runtime, first_runtime, second_runtime]
    assert len({id(first), id(second), id(third)}) == 3


@pytest.mark.parametrize("runtime", [..., None])
def test_production_contact_dependency_never_falls_back_without_runtime(
    monkeypatch, runtime
):
    settings = contact_settings().model_copy(update={"environment": "production"})
    calls = []

    def forbidden(*_args, **_kwargs):
        calls.append(True)
        raise AssertionError("an unreviewed registry must never be used")

    monkeypatch.setattr(contacts, "create_production_material_request_contact_cipher", forbidden)
    assert contacts.get_material_request_contact_cipher(_request(runtime), settings) is None
    assert calls == []
    with pytest.raises(contacts._MaterialRequestAdapterError) as error:
        contacts._require_write_runtime(settings, None)
    assert error.value.http_status_code == 503
    assert error.value.code == "material_request_contact_kms_unavailable"


def test_contact_dependency_uses_each_application_runtime_and_request_local_cipher(
    monkeypatch,
):
    settings = contact_settings().model_copy(update={"environment": "production"})
    first_runtime, second_runtime = object(), object()
    calls = []

    def factory(actual_settings, *, key_runtime):
        assert actual_settings is settings
        calls.append(key_runtime)
        return object()

    monkeypatch.setattr(contacts, "create_production_material_request_contact_cipher", factory)
    request = _request(first_runtime)
    first = contacts.get_material_request_contact_cipher(request, settings)
    second = contacts.get_material_request_contact_cipher(request, settings)
    third = contacts.get_material_request_contact_cipher(_request(second_runtime), settings)
    assert calls == [first_runtime, first_runtime, second_runtime]
    assert len({id(first), id(second), id(third)}) == 3


def test_contact_runtime_failure_returns_only_the_static_unavailable_error(monkeypatch):
    settings = contact_settings().model_copy(update={"environment": "production"})

    def unavailable(*_args, **_kwargs):
        raise RuntimeError("do-not-expose-provider-detail")

    monkeypatch.setattr(contacts, "create_production_material_request_contact_cipher", unavailable)
    cipher = contacts.get_material_request_contact_cipher(_request(object()), settings)
    with pytest.raises(contacts._MaterialRequestAdapterError) as error:
        contacts._require_write_runtime(settings, cipher)
    assert error.value.http_status_code == 503
    assert "do-not-expose" not in str(error.value.as_detail())


@pytest.mark.parametrize(
    ("auth_provider", "contact_provider", "rejected"),
    [
        ("aliyun_kms", "aliyun_kms", True),
        ("openbao_transit_v1", "aliyun_kms", False),
        ("aliyun_kms", "openbao_transit_v1", False),
        ("openbao_transit_v1", "openbao_transit_v1", False),
    ],
)
def test_legacy_cmk_equality_guard_only_compares_two_aliyun_providers(
    monkeypatch, auth_provider, contact_provider, rejected
):
    # This isolates the router guard; Settings validation owns provider readiness.
    settings = contact_settings().model_copy(
        update={
            "auth_idempotency_encryption_provider": auth_provider,
            "material_request_contact_encryption_provider": contact_provider,
            "auth_idempotency_kms_key_id": "unused-legacy-key",
            "material_request_contact_kms_key_id": "unused-legacy-key",
        }
    )
    monkeypatch.setattr(Settings, "material_request_contact_kms_configuration_ready", lambda _self: True)
    cipher = SimpleNamespace(active_key_version=lambda: 7)
    if rejected:
        with pytest.raises(contacts._MaterialRequestAdapterError) as error:
            contacts._require_write_runtime(settings, cipher)
        assert error.value.code == "material_request_contact_kms_unavailable"
    else:
        assert contacts._require_write_runtime(settings, cipher) == settings.material_request_idempotency_hmac_secret


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("sms/request", {"mobile": MOBILE}),
        ("sms/login", {"mobile": MOBILE, "code": SMS_CODE}),
        ("miniprogram/sms-login", {"mobile": MOBILE, "code": SMS_CODE, "device_id": "runtime-missing-device-0001"}),
        ("refresh", {}),
        ("miniprogram/refresh", {"refresh_token": "x" * 40, "device_id": "runtime-missing-device-0001"}),
        ("logout", {}),
        ("miniprogram/logout", {"refresh_token": "x" * 40}),
    ],
)
def test_production_auth_routes_without_runtime_reject_before_mutation(
    api_world, monkeypatch, path, payload
):
    monkeypatch.setattr(auth, "_configured_authentication_response_cipher", _REAL_AUTHENTICATION_DEPENDENCY)

    def forbidden(*_args, **_kwargs):
        raise AssertionError("missing startup runtime must stop before the factory")

    monkeypatch.setattr(auth, "create_production_authentication_cipher", forbidden)
    response = api_world.client.post(
        f"/api/auth/{path}",
        json=payload,
        headers=_formal_mutation_headers(
            request_id="provider-runtime-missing-0001",
            idempotency_key="provider-runtime-missing-key-0001",
        ),
    )
    assert response.status_code == 503, response.text
    assert response.json()["detail"] == {
        "code": "authentication_idempotency_encryption_unavailable",
        "category": "service_unavailable",
        "message": "认证幂等加密服务不可用",
    }
    assert api_world.sms_provider.send_calls == []
    assert api_world.sms_provider.verify_calls == []
    with api_world.session_factory() as db:
        for model in (
            AuthLoginRateLimitBucket, AuthIdempotencyOperation, LoginChallenge,
            SmsChallengeDispatch, AuthSession, AuthRefreshToken, AuditEvent,
            StateTransitionEvent,
        ):
            assert db.scalar(select(func.count()).select_from(model)) == 0


@pytest.mark.parametrize("login", [_login_sms_web, _login_sms_miniprogram])
def test_real_login_flow_passes_runtime_and_creates_only_one_cipher_per_request(
    api_world, monkeypatch, login
):
    with api_world.session_factory() as db:
        _seed_mobile_subject(db)
    key_runtime = object()
    api_world.client.app.state.provider_key_runtime = key_runtime
    factory_calls = []

    def factory(settings, *, key_runtime):
        assert settings is auth.settings
        factory_calls.append(key_runtime)
        return _cipher()

    monkeypatch.setattr(auth, "_configured_authentication_response_cipher", _REAL_AUTHENTICATION_DEPENDENCY)
    monkeypatch.setattr(auth, "create_production_authentication_cipher", factory)
    requested = _request_sms(
        api_world,
        client_type="miniprogram" if login is _login_sms_miniprogram else "web",
    )
    assert requested.status_code == 200, requested.text
    logged_in = login(api_world)
    assert logged_in.status_code == 200, logged_in.text
    assert factory_calls == [key_runtime, key_runtime]
    assert len(api_world.sms_provider.verify_calls) == 1


def test_contact_http_dependency_passes_request_runtime_before_domain_write(
    api_client, monkeypatch
):
    client, db, _principal_box, cipher, settings = api_client
    monkeypatch.setattr(settings, "environment", "production")
    del client.app.dependency_overrides[contacts.get_material_request_contact_cipher]
    key_runtime = object()
    client.app.state.provider_key_runtime = key_runtime
    calls = []

    def factory(actual_settings, *, key_runtime):
        assert actual_settings is settings
        calls.append(key_runtime)
        return cipher

    def create(_db, **_kwargs):
        assert calls == [key_runtime]
        assert cipher.version_calls >= 1
        return _create_result()

    monkeypatch.setattr(
        contacts.draft_service,
        "derive_material_request_create_id",
        lambda **_kwargs: _create_result().request_id,
    )
    monkeypatch.setattr(contacts, "create_production_material_request_contact_cipher", factory)
    monkeypatch.setattr(contacts.draft_service, "create_material_request_draft", create)
    response = client.post("/api/v1/material-requests", json=_draft_body(), headers=contact_headers())
    assert response.status_code == 200, response.text
    assert calls == [key_runtime]
    assert len(cipher.plaintexts) == 1
    db.commit.assert_called_once_with()
    db.rollback.assert_not_called()


@pytest.mark.parametrize("runtime_present", [False, True])
def test_contact_http_missing_or_failed_runtime_stops_before_domain_write(
    api_client, monkeypatch, runtime_present
):
    client, db, _principal_box, _cipher_value, settings = api_client
    monkeypatch.setattr(settings, "environment", "production")
    del client.app.dependency_overrides[contacts.get_material_request_contact_cipher]
    calls = []
    if runtime_present:
        client.app.state.provider_key_runtime = object()

    def unavailable(*_args, **_kwargs):
        calls.append(True)
        raise RuntimeError("private-runtime-error")

    def forbidden(*_args, **_kwargs):
        raise AssertionError("an unavailable key runtime must stop domain writes")

    monkeypatch.setattr(contacts, "create_production_material_request_contact_cipher", unavailable)
    monkeypatch.setattr(contacts.draft_service, "create_material_request_draft", forbidden)
    response = client.post("/api/v1/material-requests", json=_draft_body(), headers=contact_headers())
    assert response.status_code == 503, response.text
    assert response.json()["detail"]["code"] == "material_request_contact_kms_unavailable"
    assert "private-runtime-error" not in response.text
    assert calls == ([True] if runtime_present else [])
    db.rollback.assert_called_once_with()
    db.commit.assert_not_called()
