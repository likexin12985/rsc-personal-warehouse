"""Only new identity/projection wiring; no backup core, cloud or Linux proof."""
from backup_test_support import CLOUD
from pathlib import Path
from types import SimpleNamespace
import os

import pytest
import yaml

import backup_reader_identity as identity
import formal_object_backup as objects
import pilot_preflight
import pilot_release


def environment():
    return dict(RSC_BACKUP_READER_ACCOUNT_ID='1234567890123456',
        RSC_BACKUP_READER_ROLE_ARN='acs:ram::1234567890123456:role/rsc-pilot-backup-reader',
        RSC_BACKUP_READER_PROVIDER_ARN='acs:ram::1234567890123456:oidc-provider/synthetic',
        RSC_BACKUP_READER_SESSION_NAME='synthetic-backup-reader',
        RSC_BACKUP_READER_SOURCE_BUCKET='rsc-pilot-attachments-1234567890123456',
        RSC_BACKUP_READER_PREFIX='formal-files/v1/',
        RSC_BACKUP_READER_TOKEN_FILE='/run/rsc-backup-reader/oidc.jwt',
        RSC_BACKUP_READER_WRITER='openbao_agent_template_v1',
        RSC_BACKUP_READER_PROJECTOR_UID='23206', RSC_BACKUP_READER_SHARED_GID='23216',
        RSC_BACKUP_READER_WORKER_UID='23207', RSC_BACKUP_READER_WORKER_GID='23217')


def configured(env=None, **kwargs):
    return identity.coordinates(environment() if env is None else env,
        region=kwargs.get('region', 'cn-hangzhou'),
        bucket=kwargs.get('bucket', 'rsc-pilot-attachments-1234567890123456'))


def test_declared_source_and_independent_reader_role():
    value = configured()
    assert value['role'].endswith(':role/rsc-pilot-backup-reader')
    assert value['owner'] != value['uid'] and value['shared'] != value['gid']


@pytest.mark.parametrize('key', [*pilot_preflight.PNVS_STATIC_FIELDS,
    'RSC_OSS_BACKUP_ACCESS_KEY_ID', 'RSC_OSS_BACKUP_ACCESS_KEY_SECRET', 'RSC_OSS_BACKUP_SESSION_TOKEN'])
def test_all_static_namespaces_are_rejected(key):
    env = environment(); env[key] = 'DO_NOT_OUTPUT'
    with pytest.raises(identity.BackupIdentityUnavailable) as error: configured(env)
    assert str(error.value) == 'backup_reader_identity_unavailable'


@pytest.mark.parametrize('key,value', [
    ('RSC_BACKUP_READER_ROLE_ARN', 'acs:ram::1234567890123456:role/rsc-pilot-files'),
    ('RSC_BACKUP_READER_ROLE_ARN', 'acs:ram::1234567890123456:role/rsc-pilot-backup-writer'),
    ('RSC_BACKUP_READER_PROVIDER_ARN', 'acs:ram::0000000000000000:oidc-provider/synthetic'),
    ('RSC_BACKUP_READER_ACCOUNT_ID', 'root'), ('RSC_BACKUP_READER_SESSION_NAME', ''),
    ('RSC_BACKUP_READER_SOURCE_BUCKET', 'rsc-pilot-backups-1234567890123456'),
    ('RSC_BACKUP_READER_PREFIX', ''), ('RSC_BACKUP_READER_PREFIX', 'formal-files/'),
    ('RSC_BACKUP_READER_TOKEN_FILE', '/run/rsc-api/oidc.jwt'),
    ('RSC_BACKUP_READER_WRITER', ''), ('RSC_BACKUP_READER_WRITER', 'unreviewed'),
    ('RSC_BACKUP_READER_PROJECTOR_UID', '23207'), ('RSC_BACKUP_READER_SHARED_GID', '23217'),
    ('RSC_BACKUP_READER_WORKER_UID', '0'), ('RSC_BACKUP_READER_WORKER_GID', '023217'),
    ('RSC_BACKUP_READER_PROJECTOR_UID', '2147483648'), ('PGPASSWORD', 'DO_NOT_OUTPUT')])
def test_wrong_or_incomplete_identity_fails_closed(key, value):
    env = environment(); env[key] = value
    with pytest.raises(identity.BackupIdentityUnavailable): configured(env)


@pytest.mark.parametrize('coordinates', [dict(region='cn-shanghai'), dict(bucket='unreviewed')])
def test_cli_coordinates_cannot_select_another_source(coordinates):
    with pytest.raises(identity.BackupIdentityUnavailable): configured(**coordinates)


def metadata_fixture(monkeypatch):
    value = configured(); rows = []
    mount = ['1', '0', '0:1', '/', '/run/rsc-backup-reader', 'ro,nosuid,nodev', '-', 'tmpfs', 'tmpfs', 'rw']
    metadata = dict(mount=mount, ancestors=['synthetic-stable-directory'])
    monkeypatch.setattr(identity.os, 'geteuid', lambda: 23207)
    monkeypatch.setattr(identity.os, 'getegid', lambda: 23217)
    monkeypatch.setattr(identity.os, 'getgroups', lambda: [23216])
    monkeypatch.setattr(pilot_release, 'live_directory_metadata', lambda row: rows.append(row) or metadata)
    monkeypatch.setattr(pilot_release, 'linux_tmpfs_mount', lambda _: mount)
    return value, rows, mount, metadata


def test_projection_reuses_exact_metadata_guard_and_requires_readonly_mount(monkeypatch):
    value, rows, _, metadata = metadata_fixture(monkeypatch)
    assert identity.projection_metadata(value) == metadata
    assert rows == [dict(source='/run/rsc-backup-reader', leaf='oidc.jwt', kind='oss_oidc',
        owner=23206, group=23216, writer='openbao_agent_template_v1')]


@pytest.mark.parametrize('kind', ['root', 'wrong-gid', 'missing-read-group', 'rw', 'ancestor-mount', 'guard-reject'])
def test_runtime_identity_or_mount_rejected(monkeypatch, kind):
    value, _, mount, _ = metadata_fixture(monkeypatch)
    if kind == 'root': monkeypatch.setattr(identity.os, 'geteuid', lambda: 0)
    elif kind == 'wrong-gid': monkeypatch.setattr(identity.os, 'getegid', lambda: 23216)
    elif kind == 'missing-read-group': monkeypatch.setattr(identity.os, 'getgroups', lambda: [])
    elif kind == 'rw': mount[5] = 'rw,nosuid,nodev'
    elif kind == 'ancestor-mount': mount[4] = '/run'
    else:
        monkeypatch.setattr(pilot_release, 'live_directory_metadata', lambda _: (_ for _ in ()).throw(pilot_release.Refused('guard rejected')))
    with pytest.raises((identity.BackupIdentityUnavailable, pilot_release.Refused)):
        identity.projection_metadata(value)


def test_actual_provider_composition_is_lazy_and_guarded(monkeypatch):
    from app import oss_runtime_credentials
    for key in list(os.environ):
        if key.startswith(('OAM_', 'PG', 'POSTGRES_', 'OSS_', 'ALIBABA_CLOUD_', 'RSC_OSS_BACKUP_')):
            monkeypatch.delenv(key)
    for key, value in environment().items(): monkeypatch.setenv(key, value)
    _, _, _, metadata = metadata_fixture(monkeypatch)
    seen = []; calls = []
    def provider(value):
        seen.append(value)
        return SimpleNamespace(get_credentials=lambda: calls.append(1) or 'synthetic-credential')
    monkeypatch.setattr(oss_runtime_credentials, 'OssOidcCredentialsProvider', provider)
    result = identity.credentials_from_environment(region='cn-hangzhou', bucket=environment()['RSC_BACKUP_READER_SOURCE_BUCKET'])
    assert calls == [] and result.get_credentials() == 'synthetic-credential'
    assert seen[0].role_arn == environment()['RSC_BACKUP_READER_ROLE_ARN']
    assert seen[0].token_file == '/run/rsc-backup-reader/oidc.jwt'
    assert calls == [1] and result.initial == metadata


@pytest.mark.parametrize('phase', ['before', 'after', 'provider'])
def test_directory_drift_or_provider_error_is_fixed_and_has_no_exception_context(monkeypatch, phase):
    values = iter(([{'changed': True}] if phase == 'before' else [{}, {'changed': True}]))
    monkeypatch.setattr(identity, 'projection_metadata', lambda _: next(values))
    calls = []
    def getter():
        calls.append(1)
        if phase == 'provider': raise RuntimeError('DO_NOT_OUTPUT')
        return 'synthetic-credential'
    provider = identity.GuardedBackupCredentials(SimpleNamespace(get_credentials=getter), {}, {})
    with pytest.raises(identity.BackupIdentityUnavailable) as error: provider.get_credentials()
    assert str(error.value) == 'backup_reader_identity_unavailable' and error.value.__context__ is None
    assert calls == ([] if phase == 'before' else [1])


@pytest.mark.parametrize('key', ['joint/v1/test', 'formal-files/v1/../test', 'formal-files/v1/request_attachment/not-a-key'])
@pytest.mark.parametrize('method', ['head', 'download'])
def test_reader_cannot_escape_exact_source_key_shape(key, method):
    reader = objects.OssBackupReader(SimpleNamespace(), region='cn-hangzhou', bucket='synthetic')
    with pytest.raises(objects.ObjectBackupError, match='backup_reader_prefix_forbidden'):
        if method == 'head': reader.head(key)
        else:
            with reader.download(key, etag='"synthetic"', version_id=None): pass


def test_overlay_is_independent_readonly_projection_without_secret_fallback():
    base = yaml.safe_load((CLOUD / 'docker-compose.yml').read_text())
    overlay = yaml.safe_load((CLOUD / 'deployment/backup-reader-oidc.compose.yml.example').read_text())
    assert set(overlay['services']) == {'object-backup'}
    service = overlay['services']['object-backup']
    assert set(base['services']['object-backup']['environment']) == {'PYTHONPATH'}
    assert set(service['environment']) == {'PYTHONPATH', *environment()}
    assert all(not key.startswith(('OAM_', 'PG', 'POSTGRES_', 'OSS_', 'ALIBABA_CLOUD_')) for key in service['environment'])
    assert all(volume['read_only'] is True and volume['bind'] == {'create_host_path': False} for volume in service['volumes'])
    assert service['volumes'][0]['target'] == '/run/rsc-backup-reader'
    assert {Path(v['target']).name for v in service['volumes'][1:]} == {
        'pilot_release.py', 'pilot_preflight.py', 'pilot_network_preflight.py'}
    assert service['group_add'] == ['${RSC_BACKUP_READER_SHARED_GID:-65534}']
    assert all(':?' not in value for value in service['environment'].values())
