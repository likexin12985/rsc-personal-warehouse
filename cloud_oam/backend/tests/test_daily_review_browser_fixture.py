"""Boundary tests only: no listening socket, PG process or trusted-CA change."""
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import ipaddress
import os
from pathlib import Path
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID
from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest

import daily_review_browser_fixture as fixture
from app.formal_services.file_storage import FileStorageError

APP = 'https://127.0.0.1:49152'
OBJECT = 'https://localhost:49152'
BODY = b'\x89PNG\r\n\x1a\nsynthetic-test-evidence'


@pytest.fixture
def storage():
    now = [1_800_000_000]
    store = fixture.HttpsObjects(app_origin=APP, object_origin=OBJECT, clock=lambda: now[0])
    app = FastAPI(); store.mount(app)
    with TestClient(app, base_url=OBJECT) as client:
        yield store, client, now


def intent(store, body=BODY, **overrides):
    values = dict(storage_key='formal-files/v1/daily_reconciliation_evidence/ab/' + uuid4().hex,
        file_id=str(uuid4()), sha256=hashlib.sha256(body).hexdigest(), size_bytes=len(body),
        mime_type='image/png', ttl_seconds=60)
    values.update(overrides)
    return store.create_upload_intent(**values)


def put(client, signed, body=BODY, **headers):
    return client.put(signed.url, content=body, headers={**signed.headers, 'Origin': APP, **headers})


def test_real_bytes_required_before_head_or_get(storage, monkeypatch):
    store, client, _ = storage
    signed = intent(store)
    with pytest.raises(FileStorageError):
        store.create_download_intent(storage_key=signed.storage_key, ttl_seconds=30)
    assert put(client, signed).status_code == 200
    assert put(client, signed).status_code == 409
    download = store.create_download_intent(storage_key=signed.storage_key, ttl_seconds=30)
    response = client.get(download.url)
    assert response.content == BODY and response.headers['cache-control'] == 'no-store'
    calls = []
    @contextmanager
    def head_transport(request):
        calls.append(request.get_method())
        reply = client.request(request.get_method(), request.full_url)
        assert reply.status_code == 200 and reply.content == b''
        yield reply
    monkeypatch.setattr(fixture, 'tls_open', head_transport)
    head = store.head_object(storage_key=signed.storage_key)
    assert calls == ['HEAD']
    assert head.metadata['sha256'] == hashlib.sha256(BODY).hexdigest()
    assert head.size_bytes == len(BODY)
    assert head.metadata['file-id'] == signed.headers['x-oss-meta-file-id']
    assert store.evidence()['counts'] == {'put': 1, 'head': 1, 'get': 1, 'rejected': 0}
    assert signed.url not in str(store.evidence()) and download.url not in str(store.evidence())


@pytest.mark.parametrize('bad', ['tamper', 'expired', 'method', 'other_token'])
def test_grants_fail_closed(storage, bad):
    store, client, now = storage
    signed = intent(store)
    if bad == 'tamper':
        response = client.put(signed.url[:-1] + ('0' if signed.url[-1] != '0' else '1'), content=BODY, headers=signed.headers)
    elif bad == 'expired':
        now[0] += 61
        response = put(client, signed)
    elif bad == 'method':
        response = client.get(signed.url)
    else:
        response = client.get(OBJECT + '/__objects/unregistered.token')
    assert response.status_code == 403 and store.evidence()['objects'] == []


@pytest.mark.parametrize('headers', [
    {'Cookie': 'access_token=synthetic'}, {'Authorization': 'Bearer synthetic'},
    {'Referer': APP + '/xx'}, {'Origin': 'https://foreign.invalid'}, {'Host': '127.0.0.1:49152'},
    {'x-oss-meta-sha256': 'a' * 64}, {'x-oss-forbid-overwrite': 'false'},
])
def test_upload_rejects_credential_leak_wrong_host_origin_or_signed_headers(storage, headers):
    store, client, _ = storage
    assert put(client, intent(store), **headers).status_code == 403
    assert store.evidence()['objects'] == []


@pytest.mark.parametrize('body,code', [(b'x', 422), (BODY + b'x', 413), (b'x' * len(BODY), 422)])
def test_stored_facts_cannot_be_claimed_without_matching_bytes(storage, body, code):
    store, client, _ = storage
    signed = intent(store)
    assert put(client, signed, body=body).status_code == code
    assert store.evidence()['objects'] == []


def test_exact_cors_preflight_without_credentials(storage):
    store, client, _ = storage
    signed = intent(store)
    headers = {'Origin': APP, 'Access-Control-Request-Method': 'PUT',
               'Access-Control-Request-Headers': ', '.join(signed.headers)}
    response = client.options(signed.url, headers=headers)
    assert response.status_code == 204
    assert response.headers['access-control-allow-origin'] == APP
    assert 'access-control-allow-credentials' not in response.headers
    assert client.options(signed.url, headers={**headers, 'Access-Control-Request-Headers': 'authorization'}).status_code == 403
    assert client.options(signed.url, headers={**headers, 'Access-Control-Request-Method': 'DELETE'}).status_code == 403


@pytest.mark.parametrize('header', ['Cookie', 'Authorization', 'Referer'])
def test_download_rejects_app_credentials_and_referrer(storage, header):
    store, client, _ = storage
    signed = intent(store); assert put(client, signed).status_code == 200
    download = store.create_download_intent(storage_key=signed.storage_key, ttl_seconds=30)
    assert client.get(download.url, headers={header: 'synthetic'}).status_code == 403


def test_grants_and_object_bytes_have_finite_capacity(storage, monkeypatch):
    store, client, now = storage
    monkeypatch.setattr(fixture, 'MAX_GRANTS', 2)
    first = intent(store); intent(store)
    with pytest.raises(FileStorageError):
        intent(store)
    assert put(client, first).status_code == 200
    now[0] += 61
    second = intent(store)
    monkeypatch.setattr(fixture, 'MAX_TOTAL_BYTES', len(BODY))
    assert put(client, second).status_code == 507
    assert len(store.evidence()['objects']) == 1


def make_certificate(folder: Path, *, days=1, dns=('localhost',), ca=False):
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'local synthetic test')])
    now = datetime.now(timezone.utc)
    cert = x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key()) \
        .serial_number(x509.random_serial_number()).not_valid_before(now - timedelta(days=2)) \
        .not_valid_after(now + timedelta(days=days)).add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True) \
        .add_extension(x509.SubjectAlternativeName([*[x509.DNSName(name) for name in dns],
            x509.IPAddress(ipaddress.ip_address('127.0.0.1'))]), critical=False).sign(key, hashes.SHA256())
    certificate, private_key = folder / 'localhost.crt', folder / 'localhost.key'
    certificate.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    private_key.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                             serialization.NoEncryption()))
    os.chmod(private_key, 0o600)
    return certificate, private_key


def test_local_leaf_certificate_metadata_is_not_browser_trust(tmp_path):
    cert, key = make_certificate(tmp_path)
    result = fixture.certificate_metadata(cert, key, root=tmp_path)
    assert result['hostnames'] == ['localhost', '127.0.0.1']
    assert len(result['certificateSha256']) == 64
    assert 'browserTrustVerified' not in result and 'privateKey' not in result


@pytest.mark.parametrize('fault', ['domain', 'wildcard', 'expired', 'ca', 'key_mode', 'outside', 'symlink', 'mismatched_key'])
def test_rejects_nonlocal_or_unsafe_tls_before_any_listener(tmp_path, fault, monkeypatch):
    options = {'domain': {'dns': ('localhost', 'production.example')}, 'wildcard': {'dns': ('*.localhost',)},
               'expired': {'days': -1}, 'ca': {'ca': True}}.get(fault, {})
    cert, key = make_certificate(tmp_path, **options)
    root = tmp_path
    if fault == 'key_mode':
        os.chmod(key, 0o644)
    elif fault == 'outside':
        root = tmp_path / 'different'
    elif fault == 'symlink':
        link = tmp_path / 'key-link'; link.symlink_to(key); key = link
    elif fault == 'mismatched_key':
        other = tmp_path / 'other'; other.mkdir(); _, key = make_certificate(other)
    monkeypatch.setattr(fixture.socket, 'socket', lambda *a, **k: pytest.fail('must not bind a socket'))
    with pytest.raises((ValueError, fixture.ssl.SSLError)):
        fixture.certificate_metadata(cert, key, root=root)


def test_signed_url_failure_is_sanitized(storage, monkeypatch):
    store, _, _ = storage
    def fail(request):
        raise fixture.URLError(request.full_url)
    monkeypatch.setattr(fixture, 'tls_open', fail)
    with pytest.raises(FileStorageError, match='synthetic HTTPS HEAD verification failed') as caught:
        store.head_object(storage_key='formal-files/v1/daily_reconciliation_evidence/ab/' + uuid4().hex)
    assert '__objects' not in str(caught.value) and 'https' not in str(caught.value)


@pytest.mark.parametrize('body_failure', [False, True])
def test_owned_server_stops_before_returning_to_database_owner(monkeypatch, body_failure):
    import threading
    import time
    import uvicorn
    instances = []
    class FakeServer:
        def __init__(self, config):
            assert config.proxy_headers is False and config.access_log is False
            assert config.ssl_certfile == 'test.crt' and config.ssl_keyfile == 'test.key'
            self.started = False
            self.should_exit = False
            self.stopped = threading.Event()
            instances.append(self)
        def run(self, sockets):
            assert sockets == ['owned-socket']
            self.started = True
            while not self.should_exit:
                time.sleep(.001)
            self.stopped.set()
    monkeypatch.setattr(uvicorn, 'Server', FakeServer)
    def exercise():
        with fixture.owned_https(FastAPI(), 'owned-socket', 'test.crt', 'test.key') as (server, thread):
            assert thread.is_alive() and server.started
            if body_failure:
                raise ValueError('synthetic body failure')
    if body_failure:
        with pytest.raises(ValueError, match='synthetic body failure'):
            exercise()
    else:
        exercise()
    assert instances[0].stopped.is_set() and instances[0].should_exit


def test_listener_failure_is_not_ready_and_stops_owned_thread(monkeypatch):
    import uvicorn
    class FakeServer:
        started = False
        should_exit = False
        def __init__(self, config):
            pass
        def run(self, sockets):
            return
    monkeypatch.setattr(uvicorn, 'Server', FakeServer)
    with pytest.raises(RuntimeError, match='failed to start'):
        with fixture.owned_https(FastAPI(), 'owned-socket', 'test.crt', 'test.key'):
            pytest.fail('a dead listener cannot become ready')


@pytest.mark.parametrize('change', ['edit', 'add', 'remove'])
def test_frozen_input_set_detects_every_drift(tmp_path, monkeypatch, change):
    cloud = tmp_path / 'cloud_oam'
    (cloud / 'backend').mkdir(parents=True)
    (cloud / 'frontend').mkdir()
    (cloud / 'alembic.ini').write_text('synthetic')
    source = cloud / 'backend' / 'source.py'; source.write_text('initial')
    monkeypatch.setattr(fixture, 'CLOUD', cloud)
    monkeypatch.setattr(fixture, 'ROOT', tmp_path)
    snapshot = fixture.freeze_inputs()
    fixture.verify_inputs(snapshot)
    if change == 'edit':
        source.write_text('changed')
    elif change == 'add':
        (cloud / 'backend' / 'new.py').write_text('new')
    else:
        source.unlink()
    with pytest.raises(RuntimeError, match='input changed'):
        fixture.verify_inputs(snapshot)


def test_cli_help_and_missing_certificate_do_not_load_production_settings_or_start(tmp_path):
    import json
    import subprocess
    import sys
    environment = {key: value for key, value in os.environ.items() if not key.startswith('OAM_')}
    environment['PYTHONPATH'] = str(fixture.CLOUD / 'backend')
    executable = [sys.executable, str(Path(fixture.__file__))]
    help_result = subprocess.run(executable + ['--help'], env=environment, capture_output=True, text=True, timeout=10)
    assert help_result.returncode == 0 and '--certificate' in help_result.stdout
    artifact = tmp_path / 'must-not-exist'
    result = subprocess.run(executable + ['--artifact-dir', str(artifact), '--certificate', str(tmp_path / 'missing.crt'),
        '--private-key', str(tmp_path / 'missing.key')], env=environment, capture_output=True, text=True, timeout=10)
    assert result.returncode == 1 and not artifact.exists()
    assert json.loads(result.stdout) == {'status': 'preflight_failed', 'errorType': 'FileNotFoundError'}
    assert not result.stderr and 'ValidationError' not in result.stdout


def test_identity_surface_keeps_formal_reads_and_excludes_every_auth_and_grant_mutation():
    from app.routers import auth, access
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    fixture.mount_identity_reads(app, auth.router, access.router)
    assert {route.path for route in app.routes} == {'/api/auth/me', '/api/access/context'}
    for route in app.routes:
        original = next(candidate for router in (auth.router, access.router) for candidate in router.routes
                        if '/api' + candidate.path == route.path)
        assert route.endpoint is original.endpoint and route.methods == {'GET'} and route.dependant.dependencies
    with TestClient(app) as client:
        for router in (auth.router, access.router):
            for route in router.routes:
                if route.path not in ('/auth/me', '/access/context'):
                    for method in route.methods:
                        assert client.request(method, '/api' + route.path).status_code == 404


def test_missing_identity_contract_is_not_replaced_with_a_fallback():
    from fastapi import APIRouter
    with pytest.raises(RuntimeError, match='exact formal identity'):
        fixture.mount_identity_reads(FastAPI(), APIRouter(), APIRouter())


def test_pre_ready_stuck_listener_records_not_stopped_without_claiming_cleanup(monkeypatch):
    import uvicorn
    from types import SimpleNamespace
    state = {'stopped': True}
    server = SimpleNamespace(started=True, should_exit=False, force_exit=False, run=lambda **kw: None)
    monkeypatch.setattr(uvicorn, 'Server', lambda config: server)
    joins = []
    class StuckThread:
        ident = 1
        def __init__(self, **kwargs):
            pass
        def start(self):
            pass
        def is_alive(self):
            return True
        def join(self, seconds):
            joins.append(seconds)
    monkeypatch.setattr(fixture.threading, 'Thread', StuckThread)
    closed = []
    sock = SimpleNamespace(close=lambda: closed.append(True))
    with pytest.raises(RuntimeError, match='did not stop'):
        with fixture.owned_https(FastAPI(), sock, 'test.crt', 'test.key', state=state):
            raise ValueError('synthetic ready probe failed')
    assert state == {'stopped': False}
    assert server.should_exit and server.force_exit and joins == [10, 5] and closed == [True]
