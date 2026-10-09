"""No SDK error graph or signed credential may leave the OSS adapter."""

import hashlib
from types import SimpleNamespace
import traceback
import uuid

import pytest

from app.formal_services.file_storage import AliyunOssV2StorageAdapter, FileStorageError


MARKER = "synthetic-signed-url-security-token-must-not-escape"
BODY = b"synthetic workbook"
FILE_ID = str(uuid.UUID(int=17))
DIGEST = hashlib.sha256(BODY).hexdigest()
MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"


def key(purpose):
    return f"formal-files/v1/{purpose}/00/{uuid.UUID(FILE_ID).hex}"


def raise_sdk_error(*args, **kwargs):
    try:
        raise ValueError(MARKER)
    except ValueError as original:
        raise RuntimeError(MARKER) from original


def assert_sanitized(action, expected=None):
    with pytest.raises(FileStorageError) as failure:
        action()
    error = failure.value
    assert error.__cause__ is None and error.__context__ is None
    assert MARKER not in str(error)
    assert MARKER not in "".join(traceback.format_exception(error))
    if expected:
        assert str(error) == expected


def adapter(failure):
    calls = []

    def head(request):
        calls.append("HEAD")
        if failure == "head":
            raise_sdk_error()
        return SimpleNamespace(
            content_length=len(BODY), content_type=MIME,
            metadata={"sha256": DIGEST, "file-id": FILE_ID},
            etag=hashlib.md5(BODY).hexdigest(),
        )

    def put(request, **kwargs):
        calls.append("PUT")
        assert kwargs == {"retry_max_attempts": 1}
        raise_sdk_error()  # Unknown acknowledgement always requires HEAD.

    class Body:
        def iter_bytes(self, **kwargs):
            calls.append("STREAM")
            if failure == "stream":
                raise_sdk_error()
            yield BODY

        def close(self):
            calls.append("CLOSE")
            if failure == "close":
                raise_sdk_error()

    def get(request):
        calls.append("GET")
        if failure == "get":
            raise_sdk_error()
        return SimpleNamespace(
            body=Body(), content_length=len(BODY), content_type=MIME,
            etag=hashlib.md5(BODY).hexdigest(), content_encoding=None,
        )

    result = AliyunOssV2StorageAdapter.__new__(AliyunOssV2StorageAdapter)
    result._oss = SimpleNamespace(**{
        name: lambda **kwargs: SimpleNamespace(**kwargs)
        for name in ("PutObjectRequest", "GetObjectRequest", "HeadObjectRequest")
    })
    result._client = SimpleNamespace(
        presign=raise_sdk_error, head_object=head, put_object=put, get_object=get,
    )
    result._bucket = "synthetic-private"
    result._runtime_credentials = None
    return result, calls


@pytest.mark.parametrize("operation", ["upload", "download", "head"])
def test_sdk_errors_cannot_cross_public_signing_or_head_boundary(operation):
    target, _ = adapter("head")
    if operation == "upload":
        action = lambda: target.create_upload_intent(
            storage_key="synthetic/key", file_id=FILE_ID, sha256=DIGEST,
            size_bytes=len(BODY), mime_type=MIME, ttl_seconds=300,
        )
    elif operation == "download":
        action = lambda: target.create_download_intent(storage_key="synthetic/key", ttl_seconds=300)
    else:
        action = lambda: target.head_object(storage_key="synthetic/key")
    assert_sanitized(action)


def test_sdk_constructor_error_is_sanitized(monkeypatch):
    import alibabacloud_oss_v2 as oss
    monkeypatch.setattr(oss, "Client", raise_sdk_error)
    assert_sanitized(
        lambda: AliyunOssV2StorageAdapter(region="cn-hangzhou", bucket="synthetic-private"),
        "OSS storage adapter is unavailable",
    )


@pytest.mark.parametrize("failure", ["get", "stream", "close"])
def test_download_body_and_close_failures_drop_all_original_context(failure):
    target, calls = adapter(failure)
    expected = "OSS opening count source close failed" if failure == "close" else "OSS opening count source is unavailable"
    assert_sanitized(lambda: target.read_opening_count_source(
        storage_key=key("opening_count_import"), file_id=FILE_ID,
        sha256=DIGEST, size_bytes=len(BODY),
    ), expected)
    assert calls.count("GET") == 1
    if failure != "get":
        assert calls.count("CLOSE") == 1


@pytest.mark.parametrize("purpose,method", [
    ("inventory_report_export", "put_report_object"),
    ("opening_count_import_error", "put_opening_count_error"),
])
def test_lost_write_and_failed_head_never_retry_or_leak(purpose, method):
    target, calls = adapter("head")
    assert_sanitized(lambda: getattr(target, method)(
        storage_key=key(purpose), file_id=FILE_ID, sha256=DIGEST, payload=BODY,
    ), "OSS object verification is unavailable")
    assert calls == ["PUT", "HEAD"]


def test_unknown_put_still_recovers_exact_head_once():
    target, calls = adapter("none")
    result = target.put_report_object(
        storage_key=key("inventory_report_export"), file_id=FILE_ID,
        sha256=DIGEST, payload=BODY,
    )
    assert result.metadata["sha256"] == DIGEST
    assert calls == ["PUT", "HEAD"]


def test_sdk_cannot_smuggle_text_through_file_storage_error():
    target, _ = adapter("none")

    def fail(*args, **kwargs):
        raise FileStorageError(MARKER)

    target._client.presign = fail
    assert_sanitized(lambda: target.create_upload_intent(
        storage_key="synthetic/key", file_id=FILE_ID, sha256=DIGEST,
        size_bytes=len(BODY), mime_type=MIME, ttl_seconds=300,
    ), "OSS upload intent is unavailable")


def test_validation_message_survives_without_original_uuid_exception():
    target, calls = adapter("none")
    assert_sanitized(lambda: target.put_report_object(
        storage_key=key("inventory_report_export"), file_id=MARKER,
        sha256=DIGEST, payload=BODY,
    ), "report object identity is invalid")
    assert calls == []
