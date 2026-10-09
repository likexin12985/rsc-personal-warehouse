"""Synthetic OIDC snapshots and the real OSS signer, without cloud access."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

from app.oss_runtime_credentials import (
    OssOidcCredentialsProvider, OssOidcIdentity, OssRuntimeCredentialUnavailable,
)
from app.formal_services.file_storage import AliyunOssV2StorageAdapter


NOW = datetime(2026, 10, 9, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def no_credentials_or_network(monkeypatch):
    from app.oss_runtime_credentials import _STATIC_ENV
    for key in _STATIC_ENV:
        monkeypatch.delenv(key, raising=False)
    import requests
    monkeypatch.setattr(requests.sessions.Session, 'send',
                        lambda *a, **kw: pytest.fail('real HTTP is forbidden'))


def identity(**overrides):
    return OssOidcIdentity(**dict(
        role_arn='acs:ram::1234567890123456:role/rsc-files',
        provider_arn='acs:ram::1234567890123456:oidc-provider/rsc-pilot',
        token_file='/run/rsc-identity/api/oidc.jwt', region='cn-hangzhou',
    ) | overrides)


def raw(now=NOW, *, lifetime=3600, name='oidc_role_arn', token='synthetic-session',
        expiration=None, secret='synthetic-secret'):
    return SimpleNamespace(
        get_access_key_id=lambda: 'synthetic-id',
        get_access_key_secret=lambda: secret,
        get_security_token=lambda: token,
        get_expiration=lambda: int(now.timestamp()) + lifetime if expiration is None else expiration,
        get_provider_name=lambda: name,
    )


def source(getter, *, clock=lambda: NOW):
    return OssOidcCredentialsProvider(identity(), provider_factory=lambda _: SimpleNamespace(
        get_credentials=getter), clock=clock)


@pytest.mark.parametrize('override', [
    {'role_arn': 'acs:ram::9999999999999999:role/rsc-files'},
    {'role_arn': '*'}, {'provider_arn': ''}, {'region': 'https://example.test'},
    {'token_file': '/tmp/token'}, {'token_file': '/run/x/../token'},
    {'token_file': '/run//token'}, {'token_file': '/run/token\n'},
    {'session_name': ''}, {'session_name': 'a' * 65},
])
def test_identity_rejects_unreviewable_or_cross_account_coordinates(override):
    with pytest.raises(OssRuntimeCredentialUnavailable):
        identity(**override)


def test_explicit_sdk_constructor_is_network_and_token_read_free():
    # The projected path intentionally does not exist in this test. The SDK
    # does not need it until a credential resolution is explicitly requested.
    provider = OssOidcCredentialsProvider(identity())
    assert provider is not None


@pytest.mark.parametrize('key', ['OSS_ACCESS_KEY_ID', 'OSS_SESSION_TOKEN',
                               'ALIBABA_CLOUD_ACCESS_KEY_ID', 'ALIBABA_CLOUD_SECURITY_TOKEN'])
def test_static_environment_cannot_coexist_with_oidc(monkeypatch, key):
    monkeypatch.setenv(key, 'synthetic-do-not-use')
    with pytest.raises(OssRuntimeCredentialUnavailable):
        source(lambda: pytest.fail('credential source must not be called'))


@pytest.mark.parametrize('params', [
    {'lifetime': -1}, {'lifetime': 60}, {'lifetime': 3661},
    {'expiration': True}, {'expiration': '2026-10-09'},
    {'name': 'environment'}, {'name': 'default'}, {'token': ''},
    {'token': 'contains space'}, {'secret': 'has\nnewline'},
])
def test_invalid_expired_or_wrong_provider_credentials_fail_closed(params):
    provider = source(lambda: raw(**params))
    with pytest.raises(OssRuntimeCredentialUnavailable) as failure:
        provider.get_credentials()
    assert failure.value.__context__ is None


def test_sdk_failure_retains_no_secret_exception_context():
    def fail():
        raise RuntimeError('synthetic-secret-must-not-escape')
    with pytest.raises(OssRuntimeCredentialUnavailable) as failure:
        source(fail).get_credentials()
    assert failure.value.__context__ is None and failure.value.__cause__ is None
    assert 'synthetic-secret' not in str(failure.value)


def test_rotation_is_observed_without_own_indefinite_cache():
    values = iter([raw(token='first'), raw(token='second')])
    provider = source(lambda: next(values))
    assert provider.get_credentials().security_token == 'first'
    assert provider.get_credentials().security_token == 'second'


def test_expired_snapshot_never_falls_back_to_previous_credentials():
    values = iter([raw(), raw(lifetime=-1)])
    provider = source(lambda: next(values))
    assert provider.get_credentials().has_keys()
    with pytest.raises(OssRuntimeCredentialUnavailable):
        provider.get_credentials()


def test_pinned_signing_credential_rejects_expiry_during_signing():
    clock = [NOW]
    provider = source(lambda: raw(lifetime=180), clock=lambda: clock[0])
    frozen, expiry = provider.signing_snapshot(300)
    assert expiry == NOW + timedelta(seconds=120)
    clock[0] += timedelta(seconds=120)
    with pytest.raises(OssRuntimeCredentialUnavailable):
        frozen.get_credentials()


@pytest.mark.parametrize('operation', ['upload', 'download'])
def test_real_signer_pins_one_temporary_identity_and_clamps_expiry(operation):
    now = datetime.now(timezone.utc)
    calls = []
    def resolve():
        calls.append(True)
        return raw(now, lifetime=180, token='synthetic-first-identity')
    provider = source(resolve, clock=lambda: datetime.now(timezone.utc))
    adapter = AliyunOssV2StorageAdapter(region='cn-hangzhou', bucket='rsc-synthetic-private',
                                      runtime_credentials=provider)
    if operation == 'upload':
        intent = adapter.create_upload_intent(storage_key='synthetic/key', file_id='synthetic-file',
            sha256='a' * 64, size_bytes=1, mime_type='application/pdf', ttl_seconds=600)
    else:
        intent = adapter.create_download_intent(storage_key='synthetic/key', ttl_seconds=600)
    assert calls == [True], 'signing must use the exact snapshot whose expiry was checked'
    query = parse_qs(urlsplit(intent.url).query)
    assert query['x-oss-security-token'] == ['synthetic-first-identity']
    assert 115 <= int(query['x-oss-expires'][0]) <= 120
    assert intent.expires_at <= now + timedelta(seconds=120)
