"""Exercise the installed OSS SDK retry loop without opening a socket."""
import hashlib
from types import SimpleNamespace
from uuid import UUID

import alibabacloud_oss_v2 as oss
from alibabacloud_oss_v2.types import CaseInsensitiveDict
import pytest

from app.formal_services.file_storage import AliyunOssV2StorageAdapter, FileStorageError


@pytest.mark.parametrize('purpose,method', [
    ('inventory_report_export', 'put_report_object'),
    ('opening_count_import_error', 'put_opening_count_error'),
])
@pytest.mark.parametrize('exact_head', [True, False])
def test_installed_sdk_sends_one_put_after_lost_response_and_retries_only_head(purpose, method, exact_head):
    body = b'synthetic private workbook'
    digest = hashlib.sha256(body).hexdigest()
    identifier = UUID(int=17)
    key = f'formal-files/v1/{purpose}/00/{identifier.hex}'
    calls = []

    class Transport:
        def send(self, request, **kwargs):
            calls.append(request.method)
            if request.method == 'PUT':
                assert request.headers['x-oss-forbid-overwrite'] == 'true'
                raise oss.exceptions.ResponseError(error=TimeoutError('synthetic lost response'))
            assert request.method == 'HEAD'
            if calls.count('HEAD') == 1:
                raise oss.exceptions.ResponseError(error=TimeoutError('synthetic read retry'))
            return SimpleNamespace(status_code=200, reason='OK',
                headers=CaseInsensitiveDict({
                    'Content-Length': str(len(body)),
                    'Content-Type': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
                    'x-oss-meta-sha256': digest if exact_head else '0' * 64,
                    'x-oss-meta-file-id': str(identifier),
                    'ETag': '"' + hashlib.md5(body).hexdigest() + '"',
                }), is_stream_consumed=True, content=b'', read=lambda: b'', close=lambda: None)

        def open(self):
            pass

        def close(self):
            pass

    config = oss.config.load_default()
    config.region = 'cn-hangzhou'
    config.credentials_provider = oss.credentials.StaticCredentialsProvider('synthetic-id', 'synthetic-secret')
    config.http_client = Transport()
    # Retain the real standard retry decisions and attempts; avoid wall-clock
    # backoff in the injected transport, which never opens a network socket.
    config.retryer = oss.retry.StandardRetryer(backoff_delayer=SimpleNamespace(backoff_delay=lambda *args: 0))
    adapter = AliyunOssV2StorageAdapter.__new__(AliyunOssV2StorageAdapter)
    adapter._oss, adapter._client, adapter._bucket = oss, oss.Client(config), 'synthetic-private'
    action = lambda: getattr(adapter, method)(storage_key=key, file_id=str(identifier), sha256=digest, payload=body)
    if exact_head:
        assert action().storage_key == key
    else:
        with pytest.raises(FileStorageError, match='verification failed'):
            action()
    assert calls == ['PUT', 'HEAD', 'HEAD']
