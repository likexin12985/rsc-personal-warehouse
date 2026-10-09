"""Explicit identity selection, full cache scope, and fail-closed composition."""
import pytest

from app.config import Settings
from app import file_storage_composition as composition
from app.oss_runtime_credentials import OssRuntimeCredentialUnavailable


def settings(**overrides):
    values = dict(environment="test", file_storage_enabled=True,
        file_storage_provider="aliyun_oss_v2", file_storage_region="cn-hangzhou",
        file_storage_bucket="rsc-synthetic-private", file_idempotency_hmac_secret="synthetic-file-hmac-" * 3,
        file_storage_credential_mode="oidc_role_arn",
        file_storage_oidc_role_arn="acs:ram::1234567890123456:role/rsc-files",
        file_storage_oidc_provider_arn="acs:ram::1234567890123456:oidc-provider/rsc-pilot",
        file_storage_oidc_token_file="/run/rsc-identity/files/oidc.jwt")
    values.update(overrides)
    return Settings(_env_file=None, **values)


@pytest.fixture(autouse=True)
def isolated_cache():
    composition._storage_adapter.cache_clear()
    yield
    composition._storage_adapter.cache_clear()


@pytest.mark.parametrize("overrides", [
    {"file_storage_oidc_role_arn": ""}, {"file_storage_oidc_provider_arn": ""},
    {"file_storage_oidc_token_file": ""}, {"file_storage_oidc_token_file": "/tmp/unreviewed"},
    {"file_storage_oidc_role_arn": "acs:ram::9999999999999999:role/rsc-files"},
    {"file_storage_credential_mode": "environment"},
])
def test_incomplete_or_mixed_identity_is_rejected_before_provider(monkeypatch, overrides):
    monkeypatch.setattr(composition, "OssOidcCredentialsProvider", lambda *_: pytest.fail("provider touched"))
    value = settings(**overrides)
    assert not value.file_storage_configuration_ready()
    assert composition.create_file_storage_adapter(value) is None


def test_explicit_legacy_mode_is_not_oidc_fallback(monkeypatch):
    calls = []
    monkeypatch.setattr(composition, "AliyunOssV2StorageAdapter", lambda **kw: calls.append(kw) or object())
    value = settings(file_storage_credential_mode="environment", file_storage_oidc_role_arn="",
                     file_storage_oidc_provider_arn="", file_storage_oidc_token_file="")
    assert composition.create_file_storage_adapter(value) is not None
    assert calls[0]["runtime_credentials"] is None


def test_oidc_constructor_failure_never_selects_environment(monkeypatch):
    def unavailable(*_):
        raise OssRuntimeCredentialUnavailable("unavailable")
    monkeypatch.setattr(composition, "OssOidcCredentialsProvider", unavailable)
    monkeypatch.setattr(composition, "AliyunOssV2StorageAdapter", lambda **_: pytest.fail("fallback touched"))
    assert composition.create_file_storage_adapter(settings()) is None


def test_cache_is_bound_to_all_identity_coordinates(monkeypatch):
    identities, adapters = [], []
    def credentials(identity):
        identities.append(identity)
        return object()
    def adapter(**kwargs):
        result = object()
        adapters.append((result, kwargs))
        return result
    monkeypatch.setattr(composition, "OssOidcCredentialsProvider", credentials)
    monkeypatch.setattr(composition, "AliyunOssV2StorageAdapter", adapter)
    original = composition.create_file_storage_adapter(settings())
    assert composition.create_file_storage_adapter(settings()) is original
    for changed in (
        {"file_storage_oidc_role_arn": "acs:ram::1234567890123456:role/other-files"},
        {"file_storage_oidc_provider_arn": "acs:ram::1234567890123456:oidc-provider/other-pilot"},
        {"file_storage_oidc_token_file": "/run/rsc-identity/other/oidc.jwt"},
        {"file_storage_oidc_session_name": "other-session"},
        {"file_storage_region": "cn-shanghai"}, {"file_storage_bucket": "rsc-other-private"},
    ):
        assert composition.create_file_storage_adapter(settings(**changed)) is not original
    assert len(identities) == len(adapters) == 7
    assert all(item[1]["runtime_credentials"] is not None for item in adapters)


def test_http_composition_passes_the_selected_identity(monkeypatch):
    from app.routers import formal_files
    value, adapter = settings(), object()
    calls = []
    monkeypatch.setattr(formal_files, "create_file_storage_adapter", lambda item: calls.append(item) or adapter)
    assert formal_files.get_formal_file_storage_adapter(value) is adapter
    assert calls == [value]
