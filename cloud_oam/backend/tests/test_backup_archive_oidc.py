"""New archive wiring only: installed OSS SDK, synthetic transport, no network."""
from backup_test_support import CLOUD, WORKER
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import io
import base64
import json
import os
import socket
import subprocess
import sys
import time

import alibabacloud_oss_v2 as oss
from alibabacloud_oss_v2.types import HttpClient, HttpResponse
from requests.structures import CaseInsensitiveDict
import pytest
import yaml

import archive_joint as archive
import backup_reader_identity
import pilot_preflight


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def blocked(*args, **kwargs): raise AssertionError('network forbidden')
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setenv('RSC_BACKUP_DEADLINE_MONOTONIC', str(time.monotonic() + 60))


def environment():
    values = dict(ACCOUNT_ID=archive.ACCOUNT, ROLE_ARN=archive.ROLE,
        PROVIDER_ARN='acs:ram::'+archive.ACCOUNT+':oidc-provider/synthetic',
        SESSION_NAME='synthetic-backup-writer', TOKEN_FILE='/run/rsc-backup-writer/oidc.jwt',
        WRITER='openbao_agent_template_v1', PROJECTOR_UID='23208', SHARED_GID='23218',
        WORKER_UID='23209', WORKER_GID='23219')
    return {'RSC_BACKUP_WRITER_'+key: value for key, value in values.items()}


@pytest.mark.parametrize('key,value', [
    ('ROLE_ARN', 'acs:ram::'+archive.ACCOUNT+':role/rsc-pilot-backup-reader'),
    ('ROLE_ARN', 'acs:ram::'+archive.ACCOUNT+':role/rsc-pilot-files'),
    ('ACCOUNT_ID', '0000000000000000'), ('PROVIDER_ARN', 'acs:ram::0000000000000000:oidc-provider/synthetic'),
    ('SESSION_NAME', ''), ('TOKEN_FILE', '/run/rsc-backup-reader/oidc.jwt'),
    ('WRITER', ''), ('PROJECTOR_UID', '23206'), ('SHARED_GID', '23212'),
    ('WORKER_UID', '0'), ('WORKER_GID', '23217')])
def test_writer_never_borrows_another_identity(key, value):
    env = environment(); env['RSC_BACKUP_WRITER_'+key] = value
    with pytest.raises(archive.ArchiveRefused): archive.writer_credentials(env)


@pytest.mark.parametrize('key', [*pilot_preflight.PNVS_STATIC_FIELDS,
    'RSC_OSS_BACKUP_ACCESS_KEY_ID', 'RSC_OSS_BACKUP_ACCESS_KEY_SECRET',
    'RSC_OSS_BACKUP_SESSION_TOKEN', 'PGPASSWORD', 'OAM_TOKEN'])
def test_writer_static_or_database_environment_refused(key):
    env = environment(); env[key] = 'synthetic-do-not-output'
    with pytest.raises(archive.ArchiveRefused): archive.writer_credentials(env)


def test_writer_reuses_live_guard_and_explicit_provider_without_network(monkeypatch):
    from app import oss_runtime_credentials
    rows = []; providers = []
    monkeypatch.setattr(backup_reader_identity, 'projection_metadata', lambda row: rows.append(row) or {'stable': True})
    monkeypatch.setattr(oss_runtime_credentials, 'OssOidcCredentialsProvider', lambda value: providers.append(value) or object())
    result = archive.writer_credentials(environment())
    assert type(result) is backup_reader_identity.GuardedBackupCredentials
    assert rows == [dict(token='/run/rsc-backup-writer/oidc.jwt', owner=23208, shared=23218, uid=23209, gid=23219)]
    assert providers[0].role_arn == archive.ROLE and providers[0].region == archive.REGION


class Response(HttpResponse):
    def __init__(self, request, spec):
        self._request, self.spec, self.closed = request, spec, False
        self._headers = CaseInsensitiveDict({'Date': 'Fri, 09 Oct 2026 12:00:00 GMT', **spec.get('headers', {})})
    request = property(lambda self: self._request)
    status_code = property(lambda self: self.spec.get('status', 200))
    headers = property(lambda self: self._headers)
    reason = property(lambda self: 'Synthetic')
    is_closed = property(lambda self: self.closed)
    is_stream_consumed = property(lambda self: self.closed)
    content = property(lambda self: self.spec.get('body', b''))
    def __enter__(self): return self
    def __exit__(self, *args): self.close()
    def close(self):
        self.closed = True
        if self.spec.get('close_error'): raise IOError('synthetic-do-not-output')
    def read(self): self.close(); return self.content
    def iter_bytes(self, **kwargs):
        assert kwargs == {'block_size': 64 * 1024}
        for position in range(0, len(self.content), 64 * 1024):
            yield self.content[position:position + 64 * 1024]
        if self.spec.get('stream_error'): raise IOError('synthetic-do-not-output')


class Transport(HttpClient):
    def __init__(self, attempt, body, *, preflight=None, put_error=False, head_change=None, get_change=None):
        self.attempt, self.body = attempt, body
        self.preflight, self.put_error = preflight, put_error
        self.head_change, self.get_change = head_change, get_change
        self.calls, self.responses, self.puts = [], [], 0
    def open(self): pass
    def close(self): pass
    def send(self, request, **kwargs):
        url = urlsplit(request.url)
        assert request.headers['authorization'].startswith('OSS4-HMAC-SHA256 ')
        self.calls.append(dict(method=request.method, path=url.path, query=parse_qs(url.query),
            if_match=request.headers.get('if-match'), encoding=request.headers.get('accept-encoding')))
        journal = archive.load(self.attempt)
        if request.method == 'HEAD' and len(self.calls) == 1 and not self.puts and journal['state'] == 'prepared':
            spec = self.preflight or dict(status=404, headers={'x-oss-err': base64.b64encode(
                b'<Error><Code>NoSuchKey</Code><Message>synthetic</Message></Error>').decode()})
        elif request.method == 'PUT':
            assert journal['state'] == 'put_started'  # durable before network write
            assert request.headers.get('x-oss-forbid-overwrite') is None
            assert int(request.headers['Content-Length']) == len(self.body)
            assert request.headers['x-oss-server-side-encryption'] == 'AES256'
            assert request.headers['Content-Type'] == 'application/x-tar'
            assert request.headers['x-oss-meta-sha256'] == sha256(self.body).hexdigest()
            assert request.headers['x-oss-meta-backup-id'] == journal['backup_id']
            received = bytearray()
            # OSS wraps the IO source in its public iterable CRC tee.
            for chunk in request.body:
                assert len(chunk) <= 64 * 1024
                received.extend(chunk)
            assert bytes(received) == self.body
            self.puts += 1
            if self.put_error: raise TimeoutError('synthetic-do-not-output')
            spec = dict(headers={'x-oss-version-id': 'synthetic-version+/='})
        else:
            spec = dict(headers={'Content-Length': str(len(self.body)), 'Content-Type': 'application/x-tar',
                'ETag': '"opaque-etag-3"', 'x-oss-server-side-encryption': 'AES256',
                'x-oss-version-id': 'synthetic-version+/=', 'x-oss-meta-sha256': journal['sha256'],
                'x-oss-meta-backup-id': journal['backup_id']})
            if request.method == 'HEAD' and self.head_change: self.head_change(spec)
            if request.method == 'GET':
                spec['body'] = self.body
                if self.get_change: self.get_change(spec)
        response = Response(request, spec); self.responses.append(response); return response


def sdk_factory(transport):
    def create(key, reconcile):
        return oss.Client(oss.Config(region=archive.REGION, signature_version='v4', retry_max_attempts=1,
            enabled_redirect=False, additional_headers=['if-match', 'accept-encoding'],
            credentials_provider=oss.credentials.StaticCredentialsProvider('SYNTHETIC', 'synthetic-never-live'),
            http_client=archive.ExactTransport(transport, key, reconcile)))
    return create


def fixture(tmp_path, monkeypatch, **options):
    # Filesystem ownership checks remain real on non-root developer/CI hosts.
    # Root-only runners simulate the non-root check; a separate node rejects root.
    if os.geteuid() == 0:
        original = archive.private_directory
        def nonroot_fixture(path):
            assert path.is_absolute() and path == path.resolve() and path.stat().st_mode & 0o777 == 0o700
        monkeypatch.setattr(archive, 'private_directory', nonroot_fixture)
    tmp_path.chmod(0o700)
    body = b'synthetic verified joint bytes' * 5000
    bundle = tmp_path / 'verified-joint.tar'; bundle.write_bytes(body); bundle.chmod(0o600)
    attempt = tmp_path / 'attempt'
    transport = Transport(attempt, body, **options); verified = []
    def verifier(source, destination, *, maximum_bytes):
        assert source == bundle and maximum_bytes == 1024**2
        verified.append(source); destination.mkdir(mode=0o700)
        (destination / 'synthetic').write_bytes(b'owned scratch')
    def execute(**kwargs):
        return archive.archive(bundle, attempt, 1024**2, client_factory=sdk_factory(transport), verifier=verifier, **kwargs)
    return execute, transport, attempt, bundle, verified


def test_real_sdk_once_upload_exact_version_stream_and_durable_readback(tmp_path, monkeypatch):
    execute, transport, attempt, _, verified = fixture(tmp_path, monkeypatch)
    result = execute()
    assert result['state'] == 'verified' and result['remoteArchiveVerified'] is True
    assert result['restoreVerified'] is False and result['releaseReady'] is False and result['autoRetry'] is False
    assert len(verified) == 1 and not (attempt / 'verified').exists()
    assert [row['method'] for row in transport.calls] == ['HEAD', 'PUT', 'HEAD', 'GET']
    assert transport.calls[-1]['query'] == {'versionId': ['synthetic-version+/=']}
    assert transport.calls[-1]['if_match'] == '"opaque-etag-3"'
    assert transport.calls[-1]['encoding'] == 'identity'
    assert all(response.closed for response in transport.responses)
    assert archive.load(attempt)['version_id'] == 'synthetic-version+/='
    with pytest.raises(FileExistsError): execute()
    assert transport.puts == 1


def test_timeout_never_reputs_reconcile_only_exact_head_get(tmp_path, monkeypatch):
    execute, transport, attempt, _, _ = fixture(tmp_path, monkeypatch, put_error=True)
    first = execute(); assert first['state'] == 'unknown' and transport.puts == 1
    assert archive.load(attempt)['version_id'] is None
    with pytest.raises(FileExistsError): execute()
    transport.calls.clear()
    recovered = execute(reconcile=True)
    assert recovered['remoteArchiveVerified'] is True
    assert [row['method'] for row in transport.calls] == ['HEAD', 'GET'] and transport.puts == 1


def test_unknown_reconcile_missing_object_stays_unknown_without_put(tmp_path, monkeypatch):
    execute, transport, attempt, _, _ = fixture(tmp_path, monkeypatch, put_error=True)
    assert execute()['state'] == 'unknown'
    transport.calls.clear()
    transport.head_change = lambda spec: spec.update(status=404, body=b'<Error><Code>NoSuchKey</Code></Error>')
    result = execute(reconcile=True)
    assert result['state'] == 'unknown' and result['versionId'] is None
    assert [row['method'] for row in transport.calls] == ['HEAD'] and transport.puts == 1
    assert archive.load(attempt)['state'] == 'unknown'


def test_same_size_wrong_remote_bytes_refuse_actual_sha(tmp_path, monkeypatch):
    execute, transport, _, _, _ = fixture(tmp_path, monkeypatch,
        get_change=lambda spec: spec.update(body=b'X' * len(spec['body'])))
    result = execute()
    assert result['state'] == 'unknown' and not result['remoteArchiveVerified']
    assert [row['method'] for row in transport.calls] == ['HEAD', 'PUT', 'HEAD', 'GET']
    assert all(response.closed for response in transport.responses)


@pytest.mark.parametrize('spec,state', [
    (dict(status=200), 'key_exists'),
    (dict(status=403, body=b'<Error><Code>AccessDenied</Code></Error>'), 'preflight_unknown'),
    (dict(status=404, body=b'<Error><Code>NoSuchBucket</Code></Error>'), 'preflight_unknown'),
    (dict(status=404), 'preflight_unknown'),
    (dict(status=302, headers={'Location': 'https://untrusted.invalid/'}), 'preflight_unknown')])
def test_preflight_failure_or_existing_key_never_puts(tmp_path, monkeypatch, spec, state):
    execute, transport, attempt, _, _ = fixture(tmp_path, monkeypatch, preflight=spec)
    assert execute()['state'] == state
    assert len(transport.calls) == 1 and transport.puts == 0
    with pytest.raises(archive.ArchiveRefused): execute(reconcile=True)


@pytest.mark.parametrize('phase,change', [
    ('head', lambda s: s['headers'].update({'x-oss-server-side-encryption': 'KMS'})),
    ('head', lambda s: s['headers'].update({'x-oss-version-id': 'null'})),
    ('head', lambda s: s['headers'].update({'Content-Length': '3'})),
    ('head', lambda s: s['headers'].update({'x-oss-meta-sha256': '0'*64})),
    ('head', lambda s: s['headers'].update({'x-oss-meta-extra': 'unexpected'})),
    ('head', lambda s: s['headers'].update({'Content-Range': 'bytes 0-2/3'})),
    ('head', lambda s: s['headers'].update({'Content-Encoding': 'gzip'})),
    ('head', lambda s: s['headers'].update({'x-oss-delete-marker': 'true'})),
    ('head', lambda s: s['headers'].update({'Content-Type': 'text/plain'})),
    ('get', lambda s: s['headers'].update({'x-oss-version-id': 'changed'})),
    ('get', lambda s: s['headers'].update({'ETag': '"changed"'})),
    ('get', lambda s: s.update(status=206)),
    ('get', lambda s: s.update(body=b'wrong bytes')),
    ('get', lambda s: s.update(stream_error=True)),
    ('get', lambda s: s.update(close_error=True)),
])
def test_remote_mismatch_stays_unknown_without_restore_claim(tmp_path, monkeypatch, phase, change):
    execute, transport, attempt, _, _ = fixture(tmp_path, monkeypatch, **{phase+'_change': change})
    result = execute()
    assert result['state'] == 'unknown' and not result['remoteArchiveVerified']
    assert transport.puts == 1 and archive.load(attempt)['state'] == 'unknown'
    assert all(response.closed for response in transport.responses)


@pytest.mark.parametrize('method,url', [
    ('DELETE', None), ('GET', 'http://'+archive.BUCKET+'.oss-cn-hangzhou.aliyuncs.com/key'),
    ('GET', 'https://evil.invalid/key'), ('GET', 'https://'+archive.BUCKET+'.oss-cn-hangzhou.aliyuncs.com/other'),
    ('GET', 'https://'+archive.BUCKET+'.oss-cn-hangzhou.aliyuncs.com/key?list-type=2'),
    ('PUT', 'https://'+archive.BUCKET+'.oss-cn-hangzhou.aliyuncs.com/key?versionId=v')])
def test_transport_rejects_unapproved_action_target_query(method, url):
    inner = SimpleNamespace(send=lambda *a, **kw: pytest.fail('unapproved network action'))
    guard = archive.ExactTransport(inner, 'key', False)
    with pytest.raises(archive.ArchiveRefused):
        guard.send(SimpleNamespace(method=method, url=url or 'https://'+archive.BUCKET+'.oss-cn-hangzhou.aliyuncs.com/key'))


def test_no_second_put_even_if_sdk_retries_and_none_for_reconcile():
    inner = SimpleNamespace(send=lambda *a, **kw: 'ok')
    request = SimpleNamespace(method='PUT', url='https://'+archive.BUCKET+'.oss-cn-hangzhou.aliyuncs.com/key')
    guard = archive.ExactTransport(inner, 'key', False)
    assert guard.send(request) == 'ok'
    with pytest.raises(archive.ArchiveRefused): guard.send(request)
    with pytest.raises(archive.ArchiveRefused): archive.ExactTransport(inner, 'key', True).send(request)


def test_stream_read_and_seek_budget_and_deadline(monkeypatch):
    stream = io.BytesIO(b'x' * 100_000); body = archive.BoundedBody(stream, 70_000)
    assert len(body.read()) == 65536 and len(body.read()) == 4464 and body.read() == b''
    with pytest.raises(archive.ArchiveRefused): body.seek(70_001)
    monkeypatch.setenv('RSC_BACKUP_DEADLINE_MONOTONIC', str(time.monotonic() - 1))
    with pytest.raises(archive.ArchiveRefused): body.read(1)


@pytest.mark.parametrize('fault', ['symlink', 'mode', 'hardlink', 'capacity', 'verifier', 'durable-save'])
def test_local_boundary_stops_before_put(tmp_path, monkeypatch, fault):
    execute, transport, attempt, bundle, _ = fixture(tmp_path, monkeypatch)
    if fault == 'symlink':
        target = bundle.with_name('other'); bundle.rename(target); bundle.symlink_to(target)
    elif fault == 'mode': bundle.chmod(0o644)
    elif fault == 'hardlink': os.link(bundle, bundle.with_name('extra'))
    elif fault == 'capacity': monkeypatch.setattr(archive.shutil, 'disk_usage', lambda _: SimpleNamespace(free=0))
    elif fault == 'verifier':
        def fail(*a, **kw): raise RuntimeError('synthetic-do-not-output')
        monkeypatch.setattr(archive, 'BoundedBody', fail)
    else:
        original = archive.save
        def refuse(attempt, value):
            if value['state'] == 'put_started': raise OSError('synthetic-do-not-output')
            return original(attempt, value)
        monkeypatch.setattr(archive, 'save', refuse)
    with pytest.raises(Exception): execute()
    assert transport.puts == 0


def test_root_private_directory_refused(tmp_path, monkeypatch):
    tmp_path.chmod(0o700); monkeypatch.setattr(archive.os, 'geteuid', lambda: 0)
    with pytest.raises(archive.ArchiveRefused): archive.private_directory(tmp_path)


def test_real_sdk_configuration_disables_retries_redirects_and_keeps_verification(monkeypatch):
    transports = []; configs = []
    monkeypatch.setattr(archive, 'writer_credentials', lambda _: object())
    monkeypatch.setattr(oss.transport, 'RequestsHttpClient', lambda **kw: transports.append(kw) or object())
    monkeypatch.setattr(oss, 'Client', lambda config: configs.append(config) or object())
    archive.make_client('key', False)
    assert transports == [dict(connect_timeout=3, readwrite_timeout=30, enabled_redirect=False, insecure_skip_verify=False)]
    assert configs[0].retry_max_attempts == 1 and configs[0].enabled_redirect is False
    assert configs[0].disable_ssl is False and configs[0].insecure_skip_verify is False
    assert configs[0].signature_version == 'v4'


def test_existing_verifier_is_the_default_and_refusal_prevents_cloud(tmp_path, monkeypatch):
    import joint_backup
    _, transport, attempt, bundle, _ = fixture(tmp_path, monkeypatch)
    calls = []
    def refuse(source, destination, **kwargs):
        calls.append((source, destination, kwargs)); raise joint_backup.JointBackupError('synthetic refusal')
    monkeypatch.setattr(joint_backup, 'verify_joint', refuse)
    with pytest.raises(joint_backup.JointBackupError):
        archive.archive(bundle, attempt, 1024**2, client_factory=sdk_factory(transport))
    assert calls == [(bundle, attempt/'verified', {'maximum_bytes': 1024**2})]
    assert transport.calls == []


def test_help_cold_and_main_error_redacted(tmp_path, monkeypatch, capsys):
    run = subprocess.run([sys.executable, str(WORKER/'archive_joint.py'), '--help'],
        env={'PATH': os.environ['PATH']}, capture_output=True, timeout=5)
    assert run.returncode == 0 and b'--reconcile' in run.stdout and run.stderr == b''
    def fail(*a, **kw):
        print('synthetic-do-not-output'); raise RuntimeError('synthetic-do-not-output')
    monkeypatch.setattr(archive, 'archive', fail)
    monkeypatch.setattr(sys, 'argv', ['archive', '--reconcile', '--attempt-dir', str(tmp_path), '--maximum-bytes', '1048576'])
    assert archive.main() == 1
    output = capsys.readouterr(); assert 'synthetic-do-not-output' not in output.out + output.err
    assert json.loads(output.out)['state'] == 'blocked_or_unknown'


def test_writer_overlay_is_separate_readonly_and_has_no_app_or_db_identity():
    value = yaml.safe_load((CLOUD/'deployment/backup-writer-oidc.compose.yml.example').read_text())
    assert set(value['services']) == {'joint-backup-archive'}
    service = value['services']['joint-backup-archive']
    assert service['cap_drop'] == ['ALL'] and service['read_only'] is True
    assert service['security_opt'] == ['no-new-privileges:true']
    assert service['user'] == '23209:23219' and service['group_add'] == ['23218']
    assert service['environment']['ALIBABA_CLOUD_CREDENTIALS_FILE'] == '/dev/null'
    assert not any(k.startswith(('OAM_', 'PG', 'RSC_BACKUP_READER_')) for k in service['environment'])
    for volume in service['volumes']:
        assert volume['type'] == 'bind' and volume['bind']['create_host_path'] is False
        assert volume['read_only'] is (volume['target'] != '/archive-state')
    assert service['entrypoint'][1].endswith('/deadline_runner.py')
    assert service['command'] == ['--help']
