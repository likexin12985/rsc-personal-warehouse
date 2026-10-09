"""Audit OIDC uses the pinned SDK without real tokens or cloud requests."""
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
import socket
import subprocess
import sys
from types import SimpleNamespace

import alibabacloud_oss_v2 as oss
import pytest

from test_oss_bucket_preflight import probe, Transport, REGION, BUCKET, OWNER, MARKER
from app import oss_runtime_credentials as runtime


@pytest.fixture(autouse=True)
def offline(monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("test must not contact cloud or local services")
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    monkeypatch.setattr(socket, "create_connection", forbidden)
    for key in (*runtime._STATIC_ENV, "OAM_FILE_STORAGE_ACCESS_KEY_ID",
                "OAM_FILE_STORAGE_ACCESS_KEY_SECRET", "OAM_FILE_STORAGE_SESSION_TOKEN"):
        monkeypatch.delenv(key, raising=False)


@pytest.fixture
def oidc(monkeypatch):
    environment = dict(OAM_FILE_STORAGE_CREDENTIAL_MODE="oidc_role_arn",
        OAM_FILE_STORAGE_OIDC_ROLE_ARN=f"acs:ram::{OWNER}:role/rsc-bucket-audit",
        OAM_FILE_STORAGE_OIDC_PROVIDER_ARN=f"acs:ram::{OWNER}:oidc-provider/rsc-pilot",
        OAM_FILE_STORAGE_OIDC_TOKEN_FILE="/run/rsc-audit-identity/oidc.jwt",
        OAM_FILE_STORAGE_OIDC_SESSION_NAME="rsc-bucket-audit")
    for key, value in environment.items(): monkeypatch.setenv(key, value)
    return environment


def synthetic_provider(monkeypatch, *, expires=None, failure=False):
    calls = []
    def provider_factory(identity):
        calls.append(identity)
        class Provider:
            def get_credentials(self):
                if failure:
                    raise RuntimeError(MARKER)
                return SimpleNamespace(get_expiration=lambda: int((expires or
                    datetime.now(timezone.utc) + timedelta(minutes=20)).timestamp()),
                    get_access_key_id=lambda: "synthetic-id", get_access_key_secret=lambda: "synthetic-secret",
                    get_security_token=lambda: "synthetic-token", get_provider_name=lambda: "oidc_role_arn")
        return Provider()
    original = runtime.OssOidcCredentialsProvider
    monkeypatch.setattr(runtime, "OssOidcCredentialsProvider",
                        lambda identity: original(identity, provider_factory=provider_factory))
    return calls


def test_oidc_default_worker_uses_real_sdk_exact_four_bucket_gets(probe, monkeypatch, oidc):
    calls = synthetic_provider(monkeypatch)
    transport = Transport()
    def factory(**kwargs):
        assert kwargs == dict(connect_timeout=3, readwrite_timeout=5,
            enabled_redirect=False, insecure_skip_verify=False)
        return transport
    monkeypatch.setattr(oss.transport, "RequestsHttpClient", factory)
    result = probe.inspect_bucket(REGION, BUCKET, OWNER)
    assert all(value == "passed" for value in result.values())
    assert transport.calls == ["acl", "versioning", "encryption", "publicAccessBlock"]
    assert len(calls) == 1
    assert calls[0].role_arn == oidc["OAM_FILE_STORAGE_OIDC_ROLE_ARN"]
    assert calls[0].token_file == oidc["OAM_FILE_STORAGE_OIDC_TOKEN_FILE"]


@pytest.mark.parametrize("key", [*runtime._STATIC_ENV, "OAM_FILE_STORAGE_ACCESS_KEY_ID",
    "OAM_FILE_STORAGE_ACCESS_KEY_SECRET", "OAM_FILE_STORAGE_SESSION_TOKEN"])
def test_oidc_inspect_never_falls_back_to_injected_static_credentials(probe, monkeypatch, oidc, key):
    calls = synthetic_provider(monkeypatch)
    monkeypatch.setenv(key, MARKER)
    result = probe.inspect_bucket(REGION, BUCKET, OWNER)
    assert result["oidc_identity_configuration"] == "unknown"
    assert result["bucket_owner"] == "unknown"
    assert calls == []
    assert MARKER not in json.dumps(result)


@pytest.mark.parametrize("key,value", [
    ("OAM_FILE_STORAGE_CREDENTIAL_MODE", "environment"),
    ("OAM_FILE_STORAGE_OIDC_ROLE_ARN", "acs:ram::9999999999999999:role/wrong-account"),
    ("OAM_FILE_STORAGE_OIDC_PROVIDER_ARN", "acs:ram::9999999999999999:oidc-provider/wrong-account"),
    ("OAM_FILE_STORAGE_OIDC_TOKEN_FILE", "/tmp/oidc.jwt"),
    ("OAM_FILE_STORAGE_OIDC_TOKEN_FILE", "/run/../secret"),
])
def test_incomplete_or_wrong_account_identity_blocks_before_sdk(probe, monkeypatch, oidc, key, value):
    calls = synthetic_provider(monkeypatch)
    monkeypatch.setenv(key, value)
    result = probe.inspect_bucket(REGION, BUCKET, OWNER)
    assert result["oidc_identity_configuration"] == "unknown"
    assert result["private_acl"] == "unknown"
    assert calls == []


@pytest.mark.parametrize("failure", [False, True])
def test_expired_or_unavailable_oidc_identity_sends_no_bucket_request(probe, monkeypatch, oidc, failure, capsys):
    synthetic_provider(monkeypatch, expires=datetime.now(timezone.utc) - timedelta(seconds=10), failure=failure)
    transport = Transport()
    monkeypatch.setattr(oss.transport, "RequestsHttpClient", lambda **kwargs: transport)
    result = probe.inspect_bucket(REGION, BUCKET, OWNER)
    assert result["oidc_identity_configuration"] == "passed"  # only structural
    assert result["private_acl"] == "unknown"
    assert transport.calls == []
    assert MARKER not in json.dumps(result)
    assert capsys.readouterr() == ("", "")


def test_oidc_worker_report_does_not_claim_renewal_uat_or_release(probe):
    report = probe.inspect_runtime(REGION, BUCKET, OWNER,
        runner=lambda *a, **kw: SimpleNamespace(returncode=0,
            stdout=json.dumps(dict.fromkeys(probe.CHECKS, "passed"))))
    assert report["bucketConfigurationVerified"]
    assert all(report[name] is False for name in ("credentialRefreshVerified", "objectRoundTripVerified",
        "httpsPolicyVerified", "corsVerified", "backupRestoreVerified", "releaseReady"))


def test_oss_help_stays_cold_without_loading_identity_or_reading_tokens():
    path = Path(__file__).resolve().parents[2] / "scripts" / "oss_bucket_preflight.py"
    program = """import runpy,sys
def guard(event,args):
 if event=='import' and str(args[0]).startswith(('app.','alibabacloud')):
  raise AssertionError('unexpected identity import')
 if event=='open' and '/run/' in str(args[0]):
  raise AssertionError('unexpected token access')
sys.addaudithook(guard)
sys.argv=[sys.argv[1],'--help']
runpy.run_path(sys.argv[0],run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-c", program, str(path)],
                            capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "--inspect" in result.stdout
