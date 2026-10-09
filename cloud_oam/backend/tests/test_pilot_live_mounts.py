"""Focused projection/snapshot guards; no daemon, cloud or real credential."""
import copy
import hashlib
import json
import os
from pathlib import Path
import socket
import stat

import pytest

from scripts import pilot_release as release
from pilot_projection_test_support import projection_test_base, safe_projection_root


def document():
    environment = {
        'OAM_ENVIRONMENT': 'production',
        'OAM_AUTH_IDEMPOTENCY_ENCRYPTION_PROVIDER': 'openbao_transit_v1',
        'OAM_AUTH_IDEMPOTENCY_ENCRYPTION_KEY_VERSION': '1',
        'OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_PROVIDER': 'openbao_transit_v1',
        'OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_KEY_VERSION': '1',
        'RSC_OPENBAO_CONTAINER_ID': 'b' * 64,
        'RSC_OPENBAO_IMAGE_ID': 'sha256:' + 'c' * 64,
        **dict(zip(release.OPENBAO_ENV_FIELDS, (
            'synthetic-pilot', '/run/rsc-test/registry/keys.json', '/run/rsc-test/socket/api.sock',
            '/run/rsc-test/token/token', '41001', '41002', '41002', '41004', '41003'))),
    }
    volumes = [dict(type='bind', source='/run/rsc-test/'+name, target='/run/rsc-test/'+name,
                    read_only=True, bind={'create_host_path': False}) for name in ('registry', 'socket', 'token')]
    api = dict(environment=copy.deepcopy(environment), user='41001:41001', group_add=['41004'],
        volumes=copy.deepcopy(volumes), pid='container:' + 'b' * 64,
        cap_drop=['ALL'], cap_add=[], privileged=False, security_opt=['no-new-privileges:true'])
    gate = copy.deepcopy(api)
    api['environment'].update(OAM_FILE_STORAGE_CREDENTIAL_MODE='oidc_role_arn',
        OAM_FILE_STORAGE_OIDC_ROLE_ARN='acs:ram::1234567890123456:role/rsc-files',
        OAM_FILE_STORAGE_OIDC_PROVIDER_ARN='acs:ram::1234567890123456:oidc-provider/rsc-pilot',
        OAM_FILE_STORAGE_OIDC_TOKEN_FILE='/run/rsc-test/oidc/oidc.jwt',
        RSC_OSS_OIDC_PROJECTOR_UID='41005', RSC_OSS_OIDC_SHARED_GID='41004')
    api['volumes'].append(dict(type='bind', source='/run/rsc-test/oidc', target='/run/rsc-test/oidc',
        read_only=True, bind={'create_host_path': False}))
    return dict(services={'api': api, 'kms-pin-gate': gate})


def test_only_exact_declared_live_paths_are_classified_and_registry_stays_static():
    value = document()
    result = release.live_bind_specs(value)
    assert set(result) == {('api', '/run/rsc-test/socket'), ('api', '/run/rsc-test/token'),
        ('api', '/run/rsc-test/oidc'), ('kms-pin-gate', '/run/rsc-test/socket'), ('kms-pin-gate', '/run/rsc-test/token')}
    assert release.registry_bind_specs(value) == {'/run/rsc-test/registry': {'owner': 41001, 'group': 41001, 'leaf': 'keys.json'}}


@pytest.mark.parametrize('mutation', [
    'root-user', 'named-user', 'different-api-uid', 'missing-group', 'missing-oidc-owner',
    'oidc-api-owner', 'gate-path', 'gate-version', 'gate-provider', 'rw', 'auto-create',
    'single-file', 'wrong-source', 'nested-mount', 'second-reader', 'shared-oidc-token',
])
def test_unsafe_or_unreviewed_live_mount_declarations_are_not_exempted(mutation):
    value = document(); api = value['services']['api']; gate = value['services']['kms-pin-gate']
    if mutation == 'root-user': api['user'] = '0:0'
    elif mutation == 'named-user': api['user'] = 'api:api'
    elif mutation == 'different-api-uid': api['user'] = '42001:41001'
    elif mutation == 'missing-group': api['group_add'] = []
    elif mutation == 'missing-oidc-owner': del api['environment']['RSC_OSS_OIDC_PROJECTOR_UID']
    elif mutation == 'oidc-api-owner': api['environment']['RSC_OSS_OIDC_PROJECTOR_UID'] = '41001'
    elif mutation == 'gate-path': gate['volumes'][2]['source'] = '/run/rsc-other/token'
    elif mutation == 'gate-version': gate['environment']['OAM_AUTH_IDEMPOTENCY_ENCRYPTION_KEY_VERSION'] = '2'
    elif mutation == 'gate-provider': gate['environment']['OAM_AUTH_IDEMPOTENCY_ENCRYPTION_PROVIDER'] = 'aliyun_kms'
    elif mutation == 'rw': api['volumes'][2]['read_only'] = False
    elif mutation == 'auto-create': api['volumes'][2]['bind']['create_host_path'] = True
    elif mutation == 'single-file': api['volumes'][2]['target'] += '/token'
    elif mutation == 'wrong-source': api['volumes'][2]['source'] = '/tmp/token'
    elif mutation == 'nested-mount': api['volumes'].append(dict(type='bind',source='/run',target='/run',read_only=True))
    elif mutation == 'second-reader': value['services']['rogue'] = dict(volumes=[copy.deepcopy(api['volumes'][2])])
    else:
        api['volumes'][-1]['source'] = '/run/rsc-test/token'
    with pytest.raises((release.Refused, ValueError)):
        release.live_bind_specs(value)


@pytest.fixture
def projected(safe_projection_root, monkeypatch):
    directory = safe_projection_root / 'projection'; directory.mkdir(mode=0o750)
    directory.chmod(0o750)
    token = directory / 'token'; token.write_bytes(b'synthetic-sensitive-token-one'); token.chmod(0o440)
    row = dict(kind='openbao_token', owner=os.geteuid(), group=os.getegid(), leaf='token',
               source=str(directory.resolve()), target='/run/rsc-test/token')
    monkeypatch.setattr(release, 'linux_tmpfs_mount', lambda path: ['synthetic-tmpfs', 'nosuid,nodev'])
    return row, directory, token


@pytest.mark.parametrize('mode', [0o770, 0o777])
def test_real_writable_ancestor_is_rejected_before_reading_leaf(projected, monkeypatch, mode):
    row, directory, token = projected
    parent = directory.parent
    original_mode = stat.S_IMODE(parent.stat().st_mode)
    original = os.stat
    seen_leaf = []
    def observe(path, *args, **kwargs):
        if path == token.name and kwargs.get('dir_fd') is not None:
            seen_leaf.append(path)
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', observe)
    parent.chmod(mode)
    try:
        with pytest.raises(release.Refused, match='^runtime_projection_ancestor_invalid$'):
            release.live_directory_metadata(row)
        assert seen_leaf == []
    finally:
        parent.chmod(original_mode)


def test_real_file_rotation_keeps_fingerprint_without_opening_or_hashing_token(projected, monkeypatch):
    row, directory, token = projected
    old_inode = token.stat().st_ino
    opened = []
    original_open = os.open
    def metadata_only(path, flags, *args, **kwargs):
        opened.append(str(path))
        assert str(path) not in (str(token), 'token')
        return original_open(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', metadata_only)
    before = release.live_directory_metadata(row)
    replacement = directory / 'replacement'
    replacement.write_bytes(b'synthetic-sensitive-token-with-a-new-size'); replacement.chmod(0o440)
    replacement.replace(token)
    assert token.stat().st_ino != old_inode
    assert release.live_directory_metadata(row) == before
    assert b'synthetic-sensitive' not in release.encoded(before)
    assert 'token' not in opened


@pytest.mark.parametrize('mutation', ['directory-mode', 'token-mode', 'symlink', 'hardlink', 'extra-file', 'short-file'])
def test_real_projection_metadata_rejects_unsafe_directory_or_leaf(projected, mutation):
    row, directory, token = projected
    if mutation == 'directory-mode': directory.chmod(0o770)
    elif mutation == 'token-mode': token.chmod(0o640)
    elif mutation == 'symlink':
        target = directory.parent / 'outside'; token.rename(target); token.symlink_to(target)
    elif mutation == 'hardlink': os.link(token, directory.parent / 'second-name')
    elif mutation == 'extra-file': (directory / 'unexpected-secret').write_text('synthetic only')
    else: token.chmod(0o640); token.write_bytes(b'x'); token.chmod(0o440)
    with pytest.raises(release.Refused): release.live_directory_metadata(row)


def test_parent_replacement_changes_bound_directory_identity(projected):
    row, directory, token = projected
    before = release.live_directory_metadata(row)
    directory.rename(directory.with_name('old-projection'))
    directory.mkdir(mode=0o750); directory.chmod(0o750)
    token.write_bytes(b'synthetic-replacement-token'); token.chmod(0o440)
    assert release.live_directory_metadata(row) != before


def test_parent_replacement_during_metadata_walk_is_rejected(projected, monkeypatch):
    row, directory, token = projected
    original = os.stat; replaced = False
    def replace_after_leaf(path, *args, **kwargs):
        nonlocal replaced
        value = original(path, *args, **kwargs)
        if path == 'token' and kwargs.get('dir_fd') is not None and not replaced:
            replaced = True
            directory.rename(directory.with_name('old-projection'))
            directory.mkdir(mode=0o750); directory.chmod(0o750)
            token.write_bytes(b'synthetic-replacement-token'); token.chmod(0o440)
        return value
    monkeypatch.setattr(os, 'stat', replace_after_leaf)
    with pytest.raises(release.Refused, match='directory_changed'):
        release.live_directory_metadata(row)


def test_real_unix_socket_shape_checked_without_connecting(projected, monkeypatch):
    row, directory, token = projected
    token.unlink(); row['kind'] = 'openbao_socket'
    listener = socket.socket(socket.AF_UNIX)
    try:
        monkeypatch.chdir(directory)
        listener.bind('token'); token.chmod(0o660)
        assert release.live_directory_metadata(row)['leafKind'] == 'openbao_socket'
    finally:
        listener.close()


@pytest.mark.parametrize('kind,options,accepted', [('tmpfs','rw,nosuid,nodev',True),
    ('overlay','rw,nosuid,nodev',False), ('tmpfs','rw,nodev',False)])
def test_linux_mount_parser_requires_tmpfs_and_safe_mount_attributes(monkeypatch, kind, options, accepted):
    monkeypatch.setattr(release.sys, 'platform', 'linux')
    original = Path.read_text
    def read(path, *args, **kwargs):
        if str(path) == '/proc/self/mountinfo':
            return f'10 1 0:5 / / rw - ext4 /dev/test rw\n11 10 0:7 / /run {options} - {kind} tmpfs rw\n'
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, 'read_text', read)
    if accepted: assert release.linux_tmpfs_mount(Path('/run/rsc-test'))[0] == '11'
    else:
        with pytest.raises(release.Refused): release.linux_tmpfs_mount(Path('/run/rsc-test'))


def coordinator(tmp_path, value):
    instance = release.Release.__new__(release.Release)
    instance.root = tmp_path / 'source'; instance.root.mkdir()
    (instance.root / 'app.py').write_text('synthetic_source = True\n')
    instance.env_file = tmp_path / 'candidate.env'; instance.env_file.write_text('# synthetic\n')
    instance.compose_file = tmp_path / 'compose.yml'; instance.compose_file.write_text('# synthetic\n')
    instance.origin = 'https://rscwz.cn'; instance.private_path = '/xx/'; instance.smoke_target = {}
    instance.document = value; instance.project = 'rsc-pilot-test'
    instance.project_state = release.private_directory(tmp_path / 'state')
    instance.images = {name: 'sha256:' + hashlib.sha256(name.encode()).hexdigest() for name in ('api','web','db')}
    instance.resolve = lambda: instance.document
    return instance


def test_live_tokens_never_enter_static_hash_snapshot_or_frozen_source(tmp_path, monkeypatch):
    value = document()
    static = tmp_path / 'static'; static.mkdir(); (static / 'keys.json').write_text('synthetic-wrapped-only')
    for service in value['services'].values():
        service['volumes'][0]['source'] = str(static)
    instance = coordinator(tmp_path, value)
    # This test isolates snapshot dispatch; real projection metadata has its
    # own tests above and real Linux proof is separate from this local harness.
    monkeypatch.setattr(release, 'live_directory_metadata', lambda row: dict(owner=row['owner'], directoryInode=123))
    monkeypatch.setattr(release, 'registry_bind_specs', lambda document: {})
    monkeypatch.setattr(instance, 'openbao_peer_metadata', lambda document: {'syntheticPeer': True})
    old_digest = release.tree_digest
    hashed = []
    def no_token_digest(path, **kwargs):
        assert not str(path).startswith('/run/')
        hashed.append(str(path)); return old_digest(path, **kwargs)
    monkeypatch.setattr(release, 'tree_digest', no_token_digest)
    instance.fingerprints, instance.mounts = instance.fingerprint(value)
    assert instance.mounts.keys() == {str(static)}
    instance.capture_mounts()
    assert instance.static_mounts.keys() == {str(static)}
    frozen = instance.frozen_document()
    assert frozen['services']['api']['volumes'][0]['source'].startswith(str(instance.project_state))
    assert [m['source'] for m in frozen['services']['api']['volumes'][1:]] == [
        '/run/rsc-test/socket', '/run/rsc-test/token', '/run/rsc-test/oidc']
    monkeypatch.setattr(release, 'live_directory_metadata', lambda row: dict(owner=row['owner'], directoryInode=456))
    with pytest.raises(release.Refused, match='prepared_inputs_changed'): instance.stable()


@pytest.mark.parametrize('mutation', ['rw', 'source', 'target', 'propagation', 'user', 'group', 'duplicate'])
def test_actual_container_mount_and_identity_mismatch_is_rejected(tmp_path, mutation):
    value = document(); instance = coordinator(tmp_path, value)
    bindings = release.live_bind_specs(value)
    mounts = [dict(Type='bind', Source=row['source'], Destination=target, RW=False, Propagation='rprivate')
              for (name,target),row in bindings.items() if name=='api']
    row = dict(Id='a'*64, Image=instance.images['api'], State=dict(Running=True),
        Config=dict(User='41001:41001', Labels={'com.docker.compose.project':instance.project,
                    'com.docker.compose.service':'api'}), HostConfig=dict(GroupAdd=['41004'],
                    CapDrop=['ALL'], CapAdd=[], Privileged=False, SecurityOpt=['no-new-privileges=true'],
                    PidMode='container:'+'b'*64), Mounts=mounts)
    if mutation == 'rw': mounts[0]['RW'] = True
    elif mutation == 'source': mounts[0]['Source'] = '/run/wrong'
    elif mutation == 'target': mounts[0]['Destination'] = '/run/other'
    elif mutation == 'propagation': mounts[0]['Propagation'] = 'rshared'
    elif mutation == 'user': row['Config']['User'] = '0:0'
    elif mutation == 'group': row['HostConfig']['GroupAdd'] = []
    else: mounts.append(copy.deepcopy(mounts[0]))
    instance.run = lambda args, **kwargs: json.dumps([row]) if args[:2]==['docker','inspect'] else 'a'*64
    with pytest.raises(release.Refused, match='container_runtime_'):
        instance.container('api')


@pytest.mark.parametrize('ownership_failure', [False, True])
def test_registry_copy_preserves_private_owner_or_fails_without_fallback(tmp_path, monkeypatch, ownership_failure):
    source = tmp_path / 'registry'; source.mkdir(mode=0o700)
    leaf = source / 'keys.json'; leaf.write_text('wrapped-synthetic-only'); leaf.chmod(0o600)
    value = dict(services={'api':dict(volumes=[dict(type='bind',source=str(source),target='/registry',read_only=True)])})
    instance = coordinator(tmp_path, value)
    spec = {str(source): dict(owner=os.getuid(), group=os.getgid(), leaf='keys.json')}
    monkeypatch.setattr(release, 'registry_bind_specs', lambda document: spec)
    instance.fingerprints, instance.mounts = instance.fingerprint(value)
    if ownership_failure:
        def denied(*args, **kwargs): raise PermissionError('synthetic ownership permission denied')
        monkeypatch.setattr(os, 'chown', denied)
        with pytest.raises(PermissionError): instance.capture_mounts()
        assert instance.static_mounts == {}
    else:
        instance.capture_mounts()
        snapshot = Path(instance.static_mounts[str(source)]['snapshot'])
        assert release.registry_owner_metadata(snapshot, spec[str(source)]) == [[os.getuid(),os.getgid(),0o700], [os.getuid(),os.getgid(),0o600]]
        (snapshot/'keys.json').chmod(0o640)
        with pytest.raises(release.Refused): instance.stable()
