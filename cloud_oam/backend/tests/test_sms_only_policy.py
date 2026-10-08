"""Focused, fresh evidence for the SMS-only business authentication boundary."""

from fastapi import FastAPI
from fastapi.testclient import TestClient
import inspect
import pytest

from app.config import Settings
from app.models import User
from app.routers import auth


def _app() -> FastAPI:
    application = FastAPI()
    application.include_router(auth.router, prefix="/api")
    application.dependency_overrides[auth.get_current_user] = lambda: User(
        mobile="18660255681",
        name="test",
        password_hash="historical-only",
        role="technician",
    )
    return application


def test_login_options_never_advertise_retired_channels(monkeypatch):
    monkeypatch.setattr(auth.settings, "password_login_enabled", True)
    monkeypatch.setattr(auth.settings, "wechat_login_enabled", True)
    monkeypatch.setattr(auth.settings, "sms_login_enabled", False)
    with TestClient(_app()) as client:
        response = client.get("/api/auth/login-options")
    assert response.status_code == 200
    body = response.json()
    assert body["password_enabled"] is False
    assert body["wechat_enabled"] is False
    assert body["sms_enabled"] is False
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/auth/login", {"mobile": "18660255681", "password": "historical-secret"}),
        (
            "/api/auth/miniprogram/password-login",
            {
                "mobile": "18660255681",
                "password": "historical-secret",
                "device_id": "device-1234567890123456",
                "device_name": "test",
            },
        ),
        (
            "/api/auth/miniprogram/wechat-login",
            {"login_code": "provider-code", "device_id": "device-1234567890123456", "device_name": "test"},
        ),
    ],
)
def test_retired_password_and_wechat_routes_fail_before_side_effects(path, payload):
    with TestClient(_app()) as client:
        response = client.post(path, json=payload)
    assert response.status_code == 404
    assert response.json()["detail"] == "登录方式不可用"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_change_password_is_hard_disabled_even_for_authenticated_user():
    with TestClient(_app()) as client:
        response = client.post(
            "/api/auth/change-password",
            json={"current_password": "old", "new_password": "new-password"},
        )
    assert response.status_code == 404
    assert response.json()["detail"] == "登录方式不可用"
    assert response.headers["cache-control"] == "no-store, max-age=0"
    assert response.headers["pragma"] == "no-cache"
    assert response.headers["referrer-policy"] == "no-referrer"


def test_historical_admin_user_provisioning_cannot_create_temporary_passwords():
    source = inspect.getsource(auth.create_user)
    assert source.index("raise HTTPException") < source.index("mobile =")


def test_settings_reject_retired_channels_in_every_environment():
    with pytest.raises(ValueError, match="SMS-only policy"):
        Settings(_env_file=None, environment="test", password_login_enabled=True)
    with pytest.raises(ValueError, match="SMS-only policy"):
        Settings(_env_file=None, environment="test", wechat_login_enabled=True)


def test_dypns_provider_requires_real_rate_limit_secret_and_is_ready_when_complete():
    values = dict(
        _env_file=None,
        environment="test",
        sms_login_enabled=True,
        sms_provider="aliyun_dypns",
        sms_credential_mode="static",
        sms_access_key_id="test-access-key-id",
        sms_access_key_secret="test-access-key-secret",
        sms_sign_name="RSC个人仓",
        sms_template_code="100001",
        sms_scheme_name="RSC个人仓登录",
        auth_login_rate_limit_hmac_secret="a" * 32,
    )
    settings = Settings(**values)
    assert settings.sms_configuration_ready() is True
    assert settings.wechat_configuration_ready() is False

    incomplete = settings.model_copy(update={"auth_login_rate_limit_hmac_secret": ""})
    assert incomplete.sms_configuration_ready() is False
