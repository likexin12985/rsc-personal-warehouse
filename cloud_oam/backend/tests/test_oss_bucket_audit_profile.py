"""Offline boundaries for the independent bucket-audit OIDC profile."""
import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest


HERE = Path(__file__).resolve()
SCRIPT = HERE.parents[2] / "scripts" / "oss_bucket_preflight.py"
SPEC = importlib.util.spec_from_file_location("oss_bucket_preflight_audit_profile", SCRIPT)
probe = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(probe)

OWNER = "1934673129483837"
REGION = "cn-hangzhou"
BUCKET = "rsc-pilot-attachments-1934673129483837"


@pytest.fixture(autouse=True)
def clear_identity_environment(monkeypatch):
    for prefix in ("OAM_FILE_STORAGE", "RSC_BUCKET_AUDIT"):
        for suffix in (
            "_CREDENTIAL_MODE", "_OIDC_ROLE_ARN", "_OIDC_PROVIDER_ARN",
            "_OIDC_TOKEN_FILE", "_OIDC_SESSION_NAME", "_ACCESS_KEY_ID",
            "_ACCESS_KEY_SECRET", "_SESSION_TOKEN",
        ):
            monkeypatch.delenv(prefix + suffix, raising=False)


def _set_audit(monkeypatch, **overrides):
    values = {
        "RSC_BUCKET_AUDIT_CREDENTIAL_MODE": "oidc_role_arn",
        "RSC_BUCKET_AUDIT_OIDC_ROLE_ARN":
            f"acs:ram::{OWNER}:role/rsc-pilot-bucket-audit",
        "RSC_BUCKET_AUDIT_OIDC_PROVIDER_ARN":
            f"acs:ram::{OWNER}:oidc-provider/rsc-pilot-openbao",
        "RSC_BUCKET_AUDIT_OIDC_TOKEN_FILE": "/run/rsc-bucket-audit-identity/oidc.jwt",
        "RSC_BUCKET_AUDIT_OIDC_SESSION_NAME": "rsc-pilot-bucket-audit",
    }
    values.update(overrides)
    for key, value in values.items():
        monkeypatch.setenv(key, value)


def test_bucket_audit_profile_uses_only_its_role_and_projection(monkeypatch):
    _set_audit(monkeypatch)
    captured = []

    class FakeProvider:
        def __init__(self, identity):
            captured.append(identity)

    monkeypatch.setattr("app.oss_runtime_credentials.OssOidcCredentialsProvider", FakeProvider)
    value = probe.oidc_credentials(REGION, OWNER, identity_profile="bucket-audit")
    assert isinstance(value, FakeProvider)
    assert len(captured) == 1
    identity = captured[0]
    assert identity.role_arn.endswith("/rsc-pilot-bucket-audit")
    assert identity.provider_arn.endswith("/rsc-pilot-openbao")
    assert identity.token_file == "/run/rsc-bucket-audit-identity/oidc.jwt"
    assert identity.session_name == "rsc-pilot-bucket-audit"


def test_bucket_audit_profile_rejects_files_projection_and_static_fallback(monkeypatch):
    _set_audit(monkeypatch)
    monkeypatch.setenv("RSC_BUCKET_AUDIT_ACCESS_KEY_ID", "should-not-be-read")
    with pytest.raises(ValueError, match="^OSS audit identity unavailable$"):
        probe.oidc_credentials(REGION, OWNER, identity_profile="bucket-audit")


@pytest.mark.parametrize("key,value", [
    ("RSC_BUCKET_AUDIT_OIDC_ROLE_ARN", f"acs:ram::{OWNER}:role/rsc-pilot-files"),
    ("RSC_BUCKET_AUDIT_OIDC_TOKEN_FILE", "/run/rsc-identity/files/oidc.jwt"),
    ("RSC_BUCKET_AUDIT_OIDC_SESSION_NAME", "rsc-pilot-files"),
    ("RSC_BUCKET_AUDIT_OIDC_PROVIDER_ARN", f"acs:ram::9999999999999999:oidc-provider/rsc-pilot-openbao"),
])
def test_bucket_audit_profile_rejects_wrong_role_subject_coordinates(monkeypatch, key, value):
    _set_audit(monkeypatch, **{key: value})
    with pytest.raises(Exception):
        probe.oidc_credentials(REGION, OWNER, identity_profile="bucket-audit")


def test_runtime_forwards_profile_and_keeps_fixed_output(monkeypatch):
    output = json.dumps(dict.fromkeys(probe.CHECKS, "passed"))
    seen = {}

    def runner(argv, **kwargs):
        seen["argv"] = argv
        seen["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout=output, stderr="")

    value = probe.inspect_runtime(REGION, BUCKET, OWNER,
                                  identity_profile="bucket-audit", runner=runner)
    assert value["identityProfile"] == "bucket-audit"
    assert value["bucketConfigurationVerified"] is True
    assert "--identity-profile" in seen["argv"]
    assert seen["argv"][seen["argv"].index("--identity-profile") + 1] == "bucket-audit"
    assert seen["kwargs"]["timeout"] == probe.WORKER_SECONDS


def test_default_files_profile_remains_backward_compatible(monkeypatch):
    monkeypatch.setenv("OAM_FILE_STORAGE_CREDENTIAL_MODE", "oidc_role_arn")
    monkeypatch.setenv("OAM_FILE_STORAGE_OIDC_ROLE_ARN",
                       f"acs:ram::{OWNER}:role/reviewed-files")
    monkeypatch.setenv("OAM_FILE_STORAGE_OIDC_PROVIDER_ARN",
                       f"acs:ram::{OWNER}:oidc-provider/rsc-pilot-openbao")
    monkeypatch.setenv("OAM_FILE_STORAGE_OIDC_TOKEN_FILE", "/run/rsc-identity/files/oidc.jwt")
    monkeypatch.setenv("OAM_FILE_STORAGE_OIDC_SESSION_NAME", "reviewed-files")
    captured = []

    class FakeProvider:
        def __init__(self, identity):
            captured.append(identity)

    monkeypatch.setattr("app.oss_runtime_credentials.OssOidcCredentialsProvider", FakeProvider)
    probe.oidc_credentials(REGION, OWNER)
    assert captured[0].session_name == "reviewed-files"
    assert captured[0].token_file == "/run/rsc-identity/files/oidc.jwt"


def test_bucket_audit_target_defaults_are_profile_scoped(monkeypatch):
    monkeypatch.setenv("OAM_FILE_STORAGE_REGION", "cn-files")
    monkeypatch.setenv("OAM_FILE_STORAGE_BUCKET", "files-bucket")
    monkeypatch.setenv("RSC_OSS_PREFLIGHT_OWNER_ID", "1111111111111111")
    monkeypatch.setenv("RSC_BUCKET_AUDIT_REGION", REGION)
    monkeypatch.setenv("RSC_BUCKET_AUDIT_BUCKET", BUCKET)
    monkeypatch.setenv("RSC_BUCKET_AUDIT_OWNER_ID", OWNER)
    assert probe._target_environment("bucket-audit") == {
        "region": "RSC_BUCKET_AUDIT_REGION",
        "bucket": "RSC_BUCKET_AUDIT_BUCKET",
        "owner": "RSC_BUCKET_AUDIT_OWNER_ID",
    }
    # The profile mapping is explicit; files values are not consulted.
    names = probe._target_environment("bucket-audit")
    assert os.environ[names["region"]] == REGION
    assert os.environ[names["bucket"]] == BUCKET
    assert os.environ[names["owner"]] == OWNER


def test_cli_bucket_audit_does_not_mix_files_targets(monkeypatch, capsys):
    monkeypatch.setenv("OAM_FILE_STORAGE_REGION", "cn-files")
    monkeypatch.setenv("OAM_FILE_STORAGE_BUCKET", "files-bucket")
    monkeypatch.setenv("RSC_OSS_PREFLIGHT_OWNER_ID", "1111111111111111")
    monkeypatch.delenv("RSC_BUCKET_AUDIT_REGION", raising=False)
    monkeypatch.delenv("RSC_BUCKET_AUDIT_BUCKET", raising=False)
    monkeypatch.delenv("RSC_BUCKET_AUDIT_OWNER_ID", raising=False)
    seen = {}

    def fake_runtime(region, bucket, owner, *, identity_profile="files"):
        seen.update(region=region, bucket=bucket, owner=owner, identity_profile=identity_profile)
        return {"bucketConfigurationVerified": False}

    monkeypatch.setattr(probe, "inspect_runtime", fake_runtime)
    assert probe.main(["--inspect", "--identity-profile", "bucket-audit"]) == 1
    assert seen == {
        "region": "", "bucket": "", "owner": "", "identity_profile": "bucket-audit",
    }
    assert capsys.readouterr().out
