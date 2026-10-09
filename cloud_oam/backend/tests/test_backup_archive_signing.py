"""Actual SDK signer injection only; no cloud or backup-core reruns."""
from backup_test_support import CLOUD, WORKER
from types import SimpleNamespace
import socket
import time

import alibabacloud_oss_v2 as oss
from alibabacloud_oss_v2.types import HttpClient
import pytest

import archive_joint as archive
import backup_recovery_identity
import recover_joint as recovery
from test_backup_archive_oidc import Response


KEY = 'joint/v1/'+'b'*32+'/verified-joint.tar'
VERSION = 'synthetic-version+/='


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def blocked(*args, **kwargs): raise AssertionError('network forbidden')
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setenv('RSC_BACKUP_DEADLINE_MONOTONIC', str(time.monotonic()+60))


class SigningTransport(HttpClient):
    def __init__(self): self.calls = []
    def open(self): pass
    def close(self): pass
    def send(self, request, **kwargs):
        authorization = request.headers['Authorization']
        conditional = request.method == 'GET'
        if conditional:
            assert request.headers['If-Match'] == '"synthetic-etag"'
            assert request.headers['Accept-Encoding'] == 'identity'
            assert ',AdditionalHeaders=accept-encoding;if-match,' in authorization
        self.calls.append((request.method, conditional))  # no Authorization persists
        headers = {'x-oss-version-id': VERSION, 'Content-Length':'4',
                   'Content-Type':'application/x-tar', 'ETag':'"synthetic-etag"'}
        return Response(request, dict(headers=headers, body=b'data' if conditional else b''))


def clients(monkeypatch, kind):
    transport = SigningTransport()
    def transport_factory(**kwargs):
        assert kwargs == dict(connect_timeout=3, readwrite_timeout=30,
                              enabled_redirect=False, insecure_skip_verify=False)
        return transport
    monkeypatch.setattr(oss.transport, 'RequestsHttpClient', transport_factory)
    credentials = oss.credentials.StaticCredentialsProvider('synthetic', 'synthetic-never-live')
    if kind == 'recovery':
        monkeypatch.setattr(backup_recovery_identity, 'credentials', lambda: credentials)
        client = recovery.client_for(dict(key=KEY, version_id=VERSION))
    else:
        monkeypatch.setattr(archive, 'writer_credentials', lambda env: credentials)
        client = archive.make_client(KEY, False)
    return client, transport


@pytest.mark.parametrize('kind', ['recovery', 'archive'])
def test_production_client_uses_actual_sdk_signed_conditional_read_headers(monkeypatch, kind):
    client, transport = clients(monkeypatch, kind)
    head = client.head_object(oss.HeadObjectRequest(bucket=archive.BUCKET, key=KEY, version_id=VERSION))
    result = client.get_object(oss.GetObjectRequest(bucket=archive.BUCKET, key=KEY,
        version_id=VERSION, if_match=head.etag, accept_encoding='identity'))
    result.body.close()
    assert transport.calls == [('HEAD', False), ('GET', True)]


def test_recovery_actual_signer_refuses_put_before_transport(monkeypatch):
    client, transport = clients(monkeypatch, 'recovery')
    with pytest.raises(Exception):
        client.put_object(oss.PutObjectRequest(bucket=archive.BUCKET, key=KEY, body=b'data'))
    assert transport.calls == []


def test_archive_signer_allows_its_one_put_then_transport_blocks_second(monkeypatch):
    client, transport = clients(monkeypatch, 'archive')
    client.put_object(oss.PutObjectRequest(bucket=archive.BUCKET, key=KEY, body=b'data'))
    with pytest.raises(Exception):
        client.put_object(oss.PutObjectRequest(bucket=archive.BUCKET, key=KEY, body=b'data'))
    assert transport.calls == [('PUT', False)]


def test_archive_signer_refuses_delete_before_transport(monkeypatch):
    client, transport = clients(monkeypatch, 'archive')
    with pytest.raises(Exception):
        client.delete_object(oss.DeleteObjectRequest(bucket=archive.BUCKET, key=KEY))
    assert transport.calls == []
