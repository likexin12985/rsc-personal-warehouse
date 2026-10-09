"""Exercise the installed OSS SDK signer without an HTTP or credential call."""
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlsplit

import pytest

from app.formal_services.file_storage import AliyunOssV2StorageAdapter


@pytest.mark.parametrize("operation", ("upload", "download"))
def test_real_sdk_presign_contract_and_expiry(monkeypatch, operation):
    # These are synthetic signer inputs, never live cloud credentials.
    monkeypatch.setenv("OSS_ACCESS_KEY_ID", "synthetic-oss-signing-id")
    monkeypatch.setenv("OSS_ACCESS_KEY_SECRET", "synthetic-oss-signing-secret")
    monkeypatch.setenv("OSS_SESSION_TOKEN", "synthetic-oss-session")
    import alibabacloud_oss_v2 as oss

    def no_http(*args, **kwargs):
        pytest.fail("an offline presign must not make a network request")

    # The SDK uses a dedicated no-op HTTP client for signing; any actual
    # transport attempt is a failure, including accidental HEAD/STS access.
    import requests
    monkeypatch.setattr(requests.sessions.Session, "request", no_http)
    monkeypatch.setattr(requests.sessions.Session, "send", no_http)
    adapter = AliyunOssV2StorageAdapter(region="cn-hangzhou", bucket="rsc-synthetic-private")
    assert not hasattr(oss, "PresignOptions")
    before = datetime.now(timezone.utc)
    key = "formal-files/v1/request_attachment/00/00000000000000000000000000000001"
    if operation == "upload":
        result = adapter.create_upload_intent(
            storage_key=key, file_id="00000000-0000-0000-0000-000000000001",
            sha256="a" * 64, size_bytes=12, mime_type="application/pdf", ttl_seconds=300,
        )
        assert result.headers["x-oss-forbid-overwrite"] == "true"
        assert result.headers["x-oss-meta-sha256"] == "a" * 64
    else:
        result = adapter.create_download_intent(storage_key=key, ttl_seconds=300)
    after = datetime.now(timezone.utc)
    parsed = urlsplit(result.url)
    query = parse_qs(parsed.query)
    assert parsed.scheme == "https"
    assert parsed.hostname == "rsc-synthetic-private.oss-cn-hangzhou.aliyuncs.com"
    assert len(query["x-oss-signature"][0]) == 64
    assert query["x-oss-security-token"] == ["synthetic-oss-session"]
    # The signer truncates its remaining duration to an integer second.
    assert 299 <= int(query["x-oss-expires"][0]) <= 300
    # Return the SDK's actual expiry, rather than inventing a later timestamp
    # after signing has completed.
    assert before + timedelta(seconds=299) <= result.expires_at <= after + timedelta(seconds=300)
