"""Private opening-count XLSX reads are bounded and pinned to one OSS object."""

import hashlib
from types import SimpleNamespace
import uuid

import pytest

from app.formal_services.file_storage import AliyunOssV2StorageAdapter, FileStorageError


MIME = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
FILE_ID = str(uuid.UUID(int=23))
KEY = f"formal-files/v1/opening_count_import/00/{uuid.UUID(FILE_ID).hex}"
BODY = b"xlsx source body"
SHA = hashlib.sha256(BODY).hexdigest()


class Stream:
    def __init__(self, chunks):
        self.chunks = chunks
        self.closed = False
        self.yielded = 0

    def iter_bytes(self, *, block_size):
        assert block_size == 64 * 1024
        for chunk in self.chunks:
            self.yielded += 1
            yield chunk

    def close(self):
        self.closed = True


def _adapter(*, chunks=(BODY,), head_size=len(BODY), head_sha=SHA,
             get_size=len(BODY), get_etag='"etag"', get_type=MIME):
    calls = []
    stream = Stream(chunks)

    def head(request):
        calls.append(("head", request))
        return SimpleNamespace(
            content_length=head_size,
            content_type=MIME,
            metadata={"sha256": head_sha, "file-id": FILE_ID},
            etag='"etag"',
        )

    def get(request):
        calls.append(("get", request))
        return SimpleNamespace(
            body=stream, content_length=get_size, content_type=get_type,
            content_encoding=None, etag=get_etag,
        )

    adapter = AliyunOssV2StorageAdapter.__new__(AliyunOssV2StorageAdapter)
    adapter._bucket = "private-test"
    adapter._oss = SimpleNamespace(
        HeadObjectRequest=lambda **kwargs: SimpleNamespace(**kwargs),
        GetObjectRequest=lambda **kwargs: SimpleNamespace(**kwargs),
    )
    adapter._client = SimpleNamespace(head_object=head, get_object=get)
    return adapter, calls, stream


def _read(adapter, **overrides):
    args = dict(storage_key=KEY, file_id=FILE_ID, sha256=SHA, size_bytes=len(BODY))
    args.update(overrides)
    return adapter.read_opening_count_source(**args)


def test_source_read_checks_head_pins_get_and_closes_stream():
    adapter, calls, stream = _adapter(chunks=(BODY[:4], BODY[4:]))
    assert _read(adapter) == BODY
    assert [kind for kind, _ in calls] == ["head", "get"]
    assert calls[1][1].bucket == "private-test"
    assert calls[1][1].key == KEY
    assert calls[1][1].if_match == '"etag"'
    assert calls[1][1].accept_encoding == "identity"
    assert stream.closed


@pytest.mark.parametrize("overrides", [
    {"storage_key": KEY.replace("opening_count_import", "inventory_report_export")},
    {"file_id": str(uuid.UUID(int=24))},
    {"sha256": "A" * 64},
    {"size_bytes": 8 * 1024 * 1024 + 1},
    {"size_bytes": True},
])
def test_bad_binding_rejected_before_network(overrides):
    adapter, calls, _ = _adapter()
    with pytest.raises(FileStorageError, match="binding is invalid"):
        _read(adapter, **overrides)
    assert calls == []


@pytest.mark.parametrize("factory", [
    lambda: _adapter(head_size=len(BODY) + 1),
    lambda: _adapter(head_sha="0" * 64),
])
def test_head_drift_rejected_before_get(factory):
    adapter, calls, _ = factory()
    with pytest.raises(FileStorageError, match="HEAD verification failed"):
        _read(adapter)
    assert [kind for kind, _ in calls] == ["head"]


@pytest.mark.parametrize("factory, message", [
    (lambda: _adapter(get_size=len(BODY) + 1), "GET verification failed"),
    (lambda: _adapter(get_etag='"new"'), "GET verification failed"),
    (lambda: _adapter(get_type="application/pdf"), "GET verification failed"),
    (lambda: _adapter(chunks=(b"0" * len(BODY),)), "content verification failed"),
    (lambda: _adapter(chunks=(BODY, b"extra", b"not-read")), "exceeds declared size"),
    (lambda: _adapter(chunks=(BODY[:-1],)), "content verification failed"),
])
def test_get_drift_or_extra_bytes_rejected_and_closed(factory, message):
    adapter, calls, stream = factory()
    with pytest.raises(FileStorageError, match=message):
        _read(adapter)
    assert [kind for kind, _ in calls] == ["head", "get"]
    assert stream.closed
    if message == "exceeds declared size":
        assert stream.yielded == 2
