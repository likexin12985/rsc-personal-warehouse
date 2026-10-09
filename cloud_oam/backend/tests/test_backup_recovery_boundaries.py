"""New read-only recovery boundaries with pinned SDK and synthetic transports."""
from backup_test_support import CLOUD, WORKER
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit
import json
import os
import socket
import subprocess
import sys
import time

import alibabacloud_oss_v2 as oss
import pytest
import yaml

import archive_joint as archive
import backup_bucket_preflight as bucket
import backup_reader_identity
import backup_recovery_identity as identity
import pilot_preflight
import recover_joint as recovery
from test_backup_archive_oidc import Response, Transport
from test_oss_bucket_preflight import Transport as BucketTransport


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    def blocked(*args, **kwargs): raise AssertionError('network forbidden')
    monkeypatch.setattr(socket, 'create_connection', blocked)
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setenv('RSC_BACKUP_DEADLINE_MONOTONIC', str(time.monotonic() + 60))


def environment():
    values = dict(ACCOUNT_ID=archive.ACCOUNT,
        ROLE_ARN='acs:ram::'+archive.ACCOUNT+':role/rsc-pilot-backup-recovery',
        PROVIDER_ARN='acs:ram::'+archive.ACCOUNT+':oidc-provider/synthetic',
        SESSION_NAME='synthetic-backup-recovery', TOKEN_FILE='/run/rsc-backup-recovery/oidc.jwt',
        WRITER='openbao_agent_template_v1', PROJECTOR_UID='23210', SHARED_GID='23220',
        WORKER_UID='23211', WORKER_GID='23221')
    return {'RSC_BACKUP_RECOVERY_'+key: value for key, value in values.items()}


@pytest.mark.parametrize('key,value', [
    ('ROLE_ARN', archive.ROLE), ('ROLE_ARN', 'acs:ram::'+archive.ACCOUNT+':role/rsc-pilot-backup-reader'),
    ('ROLE_ARN', 'acs:ram::'+archive.ACCOUNT+':role/rsc-pilot-files'),
    ('ACCOUNT_ID', '0000000000000000'), ('PROVIDER_ARN', 'acs:ram::0000000000000000:oidc-provider/synthetic'),
    ('SESSION_NAME', ''), ('TOKEN_FILE', '/run/rsc-backup-writer/oidc.jwt'),
    ('WRITER', ''), ('PROJECTOR_UID', '23208'), ('SHARED_GID', '23218'),
    ('WORKER_UID', '0'), ('WORKER_GID', '23219')])
def test_recovery_cannot_reuse_writer_reader_or_application_identity(key, value):
    env = environment(); env['RSC_BACKUP_RECOVERY_'+key] = value
    with pytest.raises(archive.ArchiveRefused): identity.credentials(env)


@pytest.mark.parametrize('key', [*pilot_preflight.PNVS_STATIC_FIELDS,
    'RSC_OSS_BACKUP_ACCESS_KEY_ID', 'RSC_OSS_BACKUP_ACCESS_KEY_SECRET',
    'RSC_OSS_BACKUP_SESSION_TOKEN', 'PGPASSWORD', 'OAM_TOKEN'])
def test_recovery_rejects_static_keys_and_database_environment(key):
    env = environment(); env[key] = 'synthetic-do-not-output'
    with pytest.raises(archive.ArchiveRefused): identity.credentials(env)


def test_recovery_reuses_metadata_guard_and_explicit_oidc(monkeypatch):
    from app import oss_runtime_credentials
    rows = []; providers = []
    monkeypatch.setattr(backup_reader_identity, 'projection_metadata', lambda row: rows.append(row) or {'stable': True})
    monkeypatch.setattr(oss_runtime_credentials, 'OssOidcCredentialsProvider', lambda value: providers.append(value) or object())
    result = identity.credentials(environment())
    assert type(result) is backup_reader_identity.GuardedBackupCredentials
    assert rows == [dict(token='/run/rsc-backup-recovery/oidc.jwt', owner=23210, shared=23220, uid=23211, gid=23221)]
    assert providers[0].role_arn.endswith(':role/rsc-pilot-backup-recovery')


class SettingsTransport(BucketTransport):
    def send(self, request, **kwargs):
        url = urlsplit(request.url)
        assert request.method == 'GET' and url.hostname == archive.BUCKET+'.oss-'+archive.REGION+'.aliyuncs.com'
        action = next(iter(parse_qs(url.query, keep_blank_values=True))); self.calls.append(action)
        spec = dict(body=self.bodies[action])
        if self.failure == action: spec = dict(status=403, body=b'<Error><Code>AccessDenied</Code></Error>')
        return Response(request, spec)


def inspect_settings(**changes):
    changes.setdefault('version_body', b'<VersioningConfiguration><Status>Enabled</Status></VersioningConfiguration>')
    transport = SettingsTransport(owner=archive.ACCOUNT, **changes)
    def factory(sdk, region, wrap):
        return sdk.Client(sdk.Config(region=region, signature_version='v4', http_client=wrap(transport),
            retry_max_attempts=1, enabled_redirect=False, disable_ssl=False,
            credentials_provider=sdk.credentials.StaticCredentialsProvider('synthetic', 'synthetic-never-live')))
    return bucket.inspect(client_factory=factory), transport


def test_versioned_backup_bucket_exact_four_sdk_reads():
    checks, transport = inspect_settings()
    assert set(checks) == set(bucket.CHECKS) and all(value == 'passed' for value in checks.values())
    assert transport.calls == ['acl', 'versioning', 'encryption', 'publicAccessBlock']
    assert 'versioning_never_enabled' not in checks


@pytest.mark.parametrize('body,state', [
    (b'<VersioningConfiguration/>', 'failed'),
    (b'<VersioningConfiguration><Status>Suspended</Status></VersioningConfiguration>', 'failed'),
    (b'<VersioningConfiguration><Status/></VersioningConfiguration>', 'failed'),
    (b'<VersioningConfiguration><Status>Enabled</Status><Status>Enabled</Status></VersioningConfiguration>', 'unknown'),
    (b'<VersioningConfiguration xmlns="http://doc.oss-cn-hangzhou.aliyuncs.com"><Status>Enabled</Status></VersioningConfiguration>', 'unknown'),
    (b'<!DOCTYPE x><VersioningConfiguration><Status>Enabled</Status></VersioningConfiguration>', 'unknown'),
    (b'bad xml', 'unknown'), (b'', 'unknown')])
def test_versioned_bucket_does_not_turn_absence_suspension_or_sdk_failure_green(body, state):
    checks, transport = inspect_settings(version_body=body)
    assert checks['versioning_enabled'] == state and len(transport.calls) == 4


@pytest.mark.parametrize('changes,key', [({'acl': 'public-read'}, 'private_acl'),
    ({'encryption': 'KMS'}, 'sse_oss_aes256'), ({'block': 'false'}, 'bucket_public_access_block'),
    ({'failure': 'acl'}, 'bucket_owner')])
def test_versioned_bucket_retains_other_guards(changes, key):
    checks, _ = inspect_settings(**changes)
    assert checks[key] in ('failed', 'unknown') and checks['versioning_enabled'] == 'passed'


@pytest.mark.parametrize('change', [dict(method='PUT'), dict(query='versioning=x'),
    dict(query='versioning=&acl='), dict(host='other.invalid'), dict(scheme='http'), dict(query='list-type=2')])
def test_bucket_transport_rejects_every_unapproved_action(change):
    calls = []; inner = SimpleNamespace(send=lambda *a, **kw: calls.append(True))
    values = dict(method='GET', scheme='https', host=archive.BUCKET+'.oss-'+archive.REGION+'.aliyuncs.com', query='versioning=')
    values.update(change)
    request = SimpleNamespace(method=values['method'], url=values['scheme']+'://'+values['host']+'/?'+values['query'])
    with pytest.raises(archive.ArchiveRefused): bucket.EnabledTransport(inner).send(request)
    assert not calls


def test_bucket_runtime_reconstructs_only_fixed_states_and_never_restore():
    def runner(*args, **kwargs):
        assert kwargs['timeout'] == 45 and kwargs['check'] is False
        return SimpleNamespace(returncode=0, stdout=json.dumps(dict.fromkeys(bucket.CHECKS, 'passed')))
    result = bucket.runtime(runner=runner)
    assert result['bucketConfigurationVerified'] and not result['restoreVerified'] and not result['releaseReady']
    for output in ('synthetic-sensitive', '{}', json.dumps({**dict.fromkeys(bucket.CHECKS, 'passed'), 'secret':'synthetic'})):
        result = bucket.runtime(runner=lambda *a, **kw: SimpleNamespace(returncode=0, stdout=output))
        assert not result['bucketConfigurationVerified'] and 'synthetic' not in json.dumps(result)
    def timeout(*a, **kw): raise subprocess.TimeoutExpired('synthetic', 45, output='synthetic-sensitive')
    assert bucket.runtime(runner=timeout)['probeStatus'] == 'timeout_unknown'


def download_fixture(tmp_path, monkeypatch, **options):
    tmp_path.chmod(0o700)
    if os.geteuid() == 0:
        def nonroot_fixture(path):
            assert path.is_absolute() and path == path.resolve() and path.stat().st_mode & 0o777 == 0o700
        monkeypatch.setattr(archive, 'private_directory', nonroot_fixture)
    body = b'synthetic verified backup bytes' * 5000
    directory = tmp_path/'approved'; directory.mkdir(mode=0o700)
    value = dict(schema=archive.SCHEMA, backup_id='a'*32, key='joint/v1/'+'a'*32+'/verified-joint.tar',
        bucket=archive.BUCKET, region=archive.REGION, sha256=sha256(body).hexdigest(), size_bytes=len(body),
        state='verified', version_id='synthetic-version+/=')
    archive.save(directory, value)
    digest = sha256((directory/'journal.json').read_bytes()).hexdigest()
    transport = Transport(directory, body, **options); verified = []
    destination = tmp_path/'recovered'
    def factory(value):
        return oss.Client(oss.Config(region=archive.REGION, signature_version='v4', retry_max_attempts=1,
            enabled_redirect=False, additional_headers=['if-match', 'accept-encoding'],
            credentials_provider=oss.credentials.StaticCredentialsProvider('synthetic', 'synthetic-never-live'),
            http_client=recovery.RecoveryTransport(transport, value['key'], value['version_id'])))
    def verifier(source, target, *, maximum_bytes):
        assert source.read_bytes() == body and maximum_bytes == 1024**2
        assert source.stat().st_mode & 0o777 == 0o600
        assert json.loads((destination/'journal.json').read_text())['state'] == 'read_started'
        verified.append(source); target.mkdir(mode=0o700)
    def execute(**kwargs):
        return recovery.download(directory, digest, destination, 1024**2, client_factory=factory, verifier=verifier, **kwargs)
    return execute, transport, directory, destination, value, digest, verified


def test_exact_version_download_private_publication_and_verifier_handoff(tmp_path, monkeypatch):
    execute, transport, directory, destination, value, digest, verified = download_fixture(tmp_path, monkeypatch)
    result = execute()
    assert result['downloadedPackageVerified'] and not result['databaseRestored'] and not result['objectsRestored']
    assert not result['releaseReady'] and not result['autoRetry'] and len(verified) == 1
    assert [call['method'] for call in transport.calls] == ['HEAD', 'GET'] and transport.puts == 0
    assert all(call['query'] == {'versionId': [value['version_id']]} for call in transport.calls)
    assert transport.calls[-1]['if_match'] == '"opaque-etag-3"' and transport.calls[-1]['encoding'] == 'identity'
    assert all(response.closed for response in transport.responses)
    assert (destination/'verified-joint.tar').read_bytes() == transport.body
    assert (destination/'verified-joint.tar').stat().st_mode & 0o777 == 0o600
    assert (destination/'verified-joint.tar').stat().st_nlink == 1 and not (destination/'download.partial').exists()
    assert (destination/'journal.json').stat().st_mode & 0o777 == 0o600
    assert sha256((directory/'journal.json').read_bytes()).hexdigest() == digest
    with pytest.raises(FileExistsError): execute()
    assert len(transport.calls) == 2


@pytest.mark.parametrize('field,value', [('state','unknown'), ('version_id',None), ('version_id','null'),
    ('bucket','other-bucket'), ('key','joint/v1/other/verified-joint.tar')])
def test_unverified_or_changed_journal_never_contacts_cloud(tmp_path, monkeypatch, field, value):
    execute, transport, directory, _, data, _, _ = download_fixture(tmp_path, monkeypatch)
    data[field] = value; archive.save(directory, data)
    with pytest.raises(archive.ArchiveRefused): execute()
    assert not transport.calls


@pytest.mark.parametrize('change', ['public_file', 'symlink', 'hardlink', 'digest', 'not_verified'])
def test_recovery_journal_requires_independently_approved_private_source(tmp_path, monkeypatch, change):
    _, _, directory, _, value, digest, _ = download_fixture(tmp_path, monkeypatch)
    path = directory/'journal.json'
    if change == 'public_file': path.chmod(0o644)
    elif change == 'symlink': path.rename(directory/'real'); path.symlink_to(directory/'real')
    elif change == 'hardlink': os.link(path, directory/'copy')
    elif change == 'digest': digest = '0'*64
    else:
        value['state'] = 'unknown'; archive.save(directory, value); digest = sha256(path.read_bytes()).hexdigest()
    with pytest.raises((archive.ArchiveRefused, OSError)): recovery.trusted_journal(directory, digest)


@pytest.mark.parametrize('phase,change', [
    ('head', lambda s: s['headers'].update({'x-oss-version-id':'different'})),
    ('head', lambda s: s['headers'].update({'x-oss-server-side-encryption':'KMS'})),
    ('get', lambda s: s['headers'].update({'ETag':'"different"'})),
    ('get', lambda s: s['headers'].update({'Content-Encoding':'gzip'})),
    ('get', lambda s: s.update(body=b'X'*len(s['body']))),
    ('get', lambda s: s.update(body=s['body'][:-1])),
    ('get', lambda s: s.update(body=s['body']+b'extra')),
    ('get', lambda s: s.update(stream_error=True)),
    ('get', lambda s: s.update(close_error=True)),
])
def test_failed_or_incomplete_read_keeps_private_evidence_without_publication(tmp_path, monkeypatch, phase, change):
    execute, transport, _, destination, _, _, verified = download_fixture(tmp_path, monkeypatch, **{phase+'_change':change})
    with pytest.raises(Exception): execute()
    assert not verified and not (destination/'verified-joint.tar').exists()
    assert json.loads((destination/'journal.json').read_text())['state'] == 'read_started'
    calls = len(transport.calls)
    with pytest.raises(FileExistsError): execute()
    assert len(transport.calls) == calls and transport.puts == 0


def test_package_verifier_failure_preserves_download_and_does_not_publish(tmp_path, monkeypatch):
    execute, transport, _, destination, _, _, _ = download_fixture(tmp_path, monkeypatch)
    def refuse(*a, **kw): raise ValueError('synthetic package rejected')
    # Only inject the verifier failure into the new handoff, not any backup core.
    original = recovery.download
    def replacement(*a, **kw): kw['verifier'] = refuse; return original(*a, **kw)
    monkeypatch.setattr(recovery, 'download', replacement)
    with pytest.raises(ValueError): execute()
    assert (destination/'download.partial').exists() and not (destination/'verified-joint.tar').exists()
    assert len(transport.calls) == 2


@pytest.mark.parametrize('method,query', [('PUT','versionId=v'), ('HEAD',''), ('GET','versionId=other'),
    ('GET','versionId=v&versionId=v'), ('GET','versionId=v&partNumber=1')])
def test_recovery_transport_requires_only_exact_version_reads(method, query):
    transport = recovery.RecoveryTransport(SimpleNamespace(send=lambda *a, **kw: pytest.fail('unexpected send')), 'joint/v1/key', 'v')
    request = SimpleNamespace(method=method, url='https://'+archive.BUCKET+'.oss-'+archive.REGION+'.aliyuncs.com/joint/v1/key?'+query)
    with pytest.raises(archive.ArchiveRefused): transport.send(request)


def test_recovery_overlay_binds_worker_and_guards_without_api_or_writer_permissions():
    value = yaml.safe_load((CLOUD/'deployment/backup-recovery-oidc.compose.yml.example').read_text())
    assert set(value['services']) == {'joint-backup-recovery'}
    service = value['services']['joint-backup-recovery']
    assert service['user'] == '23211:23221' and service['group_add'] == ['23220']
    assert service['cap_drop'] == ['ALL'] and service['read_only'] is True
    assert '/opt/rsc-backup-worker' in service['environment']['PYTHONPATH'].split(':')
    mounts = {item['target']:item for item in service['volumes']}
    assert mounts['/run/rsc-backup-recovery']['read_only'] and mounts['/recovery-journal']['read_only']
    assert mounts['/recovery-output']['read_only'] is False
    assert all(item['bind']['create_host_path'] is False for item in mounts.values())
    assert not any(key.startswith(('PG','OAM_','RSC_BACKUP_WRITER_')) for key in service['environment'])


@pytest.mark.parametrize('relative', ['scripts/backup_bucket_preflight.py', 'deployment/backup-worker/recover_joint.py'])
def test_help_is_cold_without_sdk_or_files(tmp_path, relative):
    # -S excludes site-packages: help cannot accidentally import the SDK/provider.
    env = dict(PATH=os.environ['PATH'], PYTHONPATH=str(CLOUD/'scripts')+':'+str(WORKER))
    result = subprocess.run([sys.executable, '-S', str(CLOUD/relative), '--help'],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=5)
    assert result.returncode == 0 and 'usage:' in result.stdout and result.stderr == ''
