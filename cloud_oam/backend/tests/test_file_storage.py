from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from app.formal_services.file_storage import (
    AliyunOssV2StorageAdapter,
    FileStorageError,
)


class _FakeCredentialsProvider:
    def __init__(self) -> None:
        self.created = True


class _FakeConfig:
    def __init__(self) -> None:
        self.credentials_provider = None
        self.region = None
        self.signature_version = None


class _FakePresignOptions:
    def __init__(self, *, expires) -> None:
        self.expires = expires


class _FakePutObjectRequest:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class _FakeGetObjectRequest:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class _FakeHeadObjectRequest:
    def __init__(self, **kwargs) -> None:
        self.__dict__.update(kwargs)


class _FakeClient:
    def __init__(self, config) -> None:
        self.config = config
        self.presign_calls: list[tuple[object, object]] = []
        self.head_calls: list[object] = []
        self.upload_headers = {
            "Content-Type": "image/jpeg",
            "x-oss-meta-sha256": "a" * 64,
            "x-oss-meta-file-id": "file-1",
            "x-oss-forbid-overwrite": "true",
        }
        self.head_result = SimpleNamespace(
            content_length=128,
            content_type="image/jpeg",
            metadata={"sha256": "a" * 64, "file-id": "file-1"},
            etag='"etag-1"',
        )

    def presign(self, request, options):
        self.presign_calls.append((request, options))
        return SimpleNamespace(
            url="https://example.invalid/signed",
            signed_headers=self.upload_headers,
        )

    def head_object(self, request):
        self.head_calls.append(request)
        return self.head_result


@pytest.fixture
def fake_oss(monkeypatch):
    config = _FakeConfig()
    client_box: dict[str, _FakeClient] = {}

    def load_default():
        return config

    def client_factory(loaded):
        client = _FakeClient(loaded)
        client_box["client"] = client
        return client

    module = SimpleNamespace(
        credentials=SimpleNamespace(
            EnvironmentVariableCredentialsProvider=_FakeCredentialsProvider
        ),
        config=SimpleNamespace(load_default=load_default),
        Client=client_factory,
        PutObjectRequest=_FakePutObjectRequest,
        GetObjectRequest=_FakeGetObjectRequest,
        HeadObjectRequest=_FakeHeadObjectRequest,
        PresignOptions=_FakePresignOptions,
    )
    monkeypatch.setitem(__import__("sys").modules, "alibabacloud_oss_v2", module)
    return config, client_box


def test_adapter_uses_environment_credentials_and_v4_bound_upload(fake_oss):
    config, client_box = fake_oss
    adapter = AliyunOssV2StorageAdapter(
        region="cn-hangzhou",
        bucket="rsc-private-files",
    )

    assert isinstance(config.credentials_provider, _FakeCredentialsProvider)
    assert config.region == "cn-hangzhou"
    assert config.signature_version == "v4"

    intent = adapter.create_upload_intent(
        storage_key="formal-files/v1/request_attachment/aa/" + "1" * 32,
        file_id="file-1",
        sha256="a" * 64,
        size_bytes=128,
        mime_type="image/jpeg",
        ttl_seconds=600,
    )

    assert intent.url == "https://example.invalid/signed"
    assert intent.storage_key.endswith("/" + "1" * 32)
    assert intent.headers == {
        "Content-Type": "image/jpeg",
        "x-oss-meta-sha256": "a" * 64,
        "x-oss-meta-file-id": "file-1",
        "x-oss-forbid-overwrite": "true",
    }
    assert intent.expires_at > datetime.now(timezone.utc)
    request, options = client_box["client"].presign_calls[0]
    assert request.bucket == "rsc-private-files"
    assert request.key == intent.storage_key
    assert request.content_type == "image/jpeg"
    assert request.metadata == {"sha256": "a" * 64, "file-id": "file-1"}
    assert request.forbid_overwrite is True
    assert options.expires.total_seconds() == 600


def test_adapter_rejects_upload_signature_missing_required_header(fake_oss):
    _config, client_box = fake_oss
    adapter = AliyunOssV2StorageAdapter(region="cn-hangzhou", bucket="rsc-private-files")
    client_box["client"].upload_headers.pop("x-oss-meta-file-id")

    with pytest.raises(FileStorageError, match="required bound header"):
        adapter.create_upload_intent(
            storage_key="formal-files/v1/request_attachment/aa/" + "2" * 32,
            file_id="file-1",
            sha256="a" * 64,
            size_bytes=128,
            mime_type="image/jpeg",
            ttl_seconds=600,
        )


def test_adapter_maps_head_and_download_intents_to_private_keys(fake_oss):
    _config, client_box = fake_oss
    adapter = AliyunOssV2StorageAdapter(region="cn-hangzhou", bucket="rsc-private-files")
    key = "formal-files/v1/request_attachment/aa/" + "3" * 32

    head = adapter.head_object(storage_key=key)
    assert head.storage_key == key
    assert head.size_bytes == 128
    assert head.mime_type == "image/jpeg"
    assert head.metadata == {"sha256": "a" * 64, "file-id": "file-1"}
    assert head.etag == '"etag-1"'
    assert client_box["client"].head_calls[0].bucket == "rsc-private-files"
    assert client_box["client"].head_calls[0].key == key

    intent = adapter.create_download_intent(storage_key=key, ttl_seconds=300)
    assert intent.storage_key == key
    assert intent.url == "https://example.invalid/signed"
    assert intent.expires_at > datetime.now(timezone.utc)
    request, options = client_box["client"].presign_calls[-1]
    assert request.bucket == "rsc-private-files"
    assert request.key == key
    assert options.expires.total_seconds() == 300
