"""A report upload is write-once and accepted only after exact OSS reread."""

import base64
import hashlib
from types import SimpleNamespace
import uuid

import pytest

from app.formal_services.file_storage import AliyunOssV2StorageAdapter, FileStorageError


MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FILE_ID = str(uuid.UUID(int=7))
KEY = f"formal-files/v1/inventory_report_export/00/{uuid.UUID(FILE_ID).hex}"
BODY = b"example xlsx body"
SHA = hashlib.sha256(BODY).hexdigest()


def _adapter(*, put_error=None, head_size=None, head_sha=None):
    calls = []

    def put(request):
        calls.append(("put", request))
        if put_error:
            raise put_error

    def head(request):
        calls.append(("head", request))
        return SimpleNamespace(
            content_length=len(BODY) if head_size is None else head_size,
            content_type=MIME,
            metadata={"sha256": SHA if head_sha is None else head_sha, "file-id": FILE_ID},
            etag='"' + hashlib.md5(BODY).hexdigest() + '"',
        )

    adapter = AliyunOssV2StorageAdapter.__new__(AliyunOssV2StorageAdapter)
    adapter._bucket = "private-test"
    adapter._oss = SimpleNamespace(
        PutObjectRequest=lambda **kwargs: SimpleNamespace(**kwargs),
        HeadObjectRequest=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    adapter._client = SimpleNamespace(put_object=put, head_object=head)
    return adapter, calls


def test_report_put_is_bound_and_verified_by_head():
    adapter, calls = _adapter()
    result = adapter.put_report_object(storage_key=KEY, file_id=FILE_ID, sha256=SHA, payload=BODY)
    assert result.storage_key == KEY
    assert [kind for kind, _ in calls] == ["put", "head"]
    request = calls[0][1]
    assert request.bucket == "private-test"
    assert request.key == KEY
    assert request.body == BODY
    assert request.content_type == MIME
    assert request.content_length == len(BODY)
    assert request.content_md5 == base64.b64encode(hashlib.md5(BODY).digest()).decode()
    assert request.metadata == {"sha256": SHA, "file-id": FILE_ID}
    assert request.forbid_overwrite is True


def test_lost_put_acknowledgement_recovers_only_exact_object():
    adapter, calls = _adapter(put_error=TimeoutError("lost ack"))
    adapter.put_report_object(storage_key=KEY, file_id=FILE_ID, sha256=SHA, payload=BODY)
    assert [kind for kind, _ in calls] == ["put", "head"]

    adapter, calls = _adapter(put_error=TimeoutError("lost ack"), head_sha="0" * 64)
    with pytest.raises(FileStorageError, match="verification failed"):
        adapter.put_report_object(storage_key=KEY, file_id=FILE_ID, sha256=SHA, payload=BODY)
    assert [kind for kind, _ in calls] == ["put", "head"]


def test_report_put_rejects_wrong_key_or_digest_before_network():
    adapter, calls = _adapter()
    for key, sha in ((KEY + "-other", SHA), (KEY, "0" * 64)):
        with pytest.raises(FileStorageError, match="content or key"):
            adapter.put_report_object(storage_key=key, file_id=FILE_ID, sha256=sha, payload=BODY)
    assert calls == []
