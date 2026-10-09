"""New peer/isolation/official sink guards; synthetic Docker, real tmp files."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import pilot_release as release
from scripts import pilot_preflight as preflight
from test_pilot_live_mounts import document, coordinator, projected
from pilot_projection_test_support import projection_test_base, safe_projection_root


def peer():
    return dict(Id='b'*64, Image='sha256:'+'c'*64,
        Config=dict(User='41002:41002', Env=['SYNTHETIC_SECRET=do-not-return']),
        State=dict(Running=True, Status='running', StartedAt='2026-10-09T01:00:00.123456789Z', Pid=1234),
        HostConfig=dict(GroupAdd=['41004'], CapDrop=['ALL'], CapAdd=None, Privileged=False,
            SecurityOpt=['no-new-privileges=true'], ReadonlyRootfs=True, NetworkMode='none',
            PidMode='', IpcMode='private', UsernsMode='', Devices=[], PortBindings={}),
        NetworkSettings=dict(Ports={}), Mounts=[dict(Type='bind', Source='/run/synthetic/socket',
            Destination='/run/rsc-bao', RW=True, Propagation='rprivate')])


def peer_coordinator(tmp_path):
    value = document(); instance = coordinator(tmp_path, value); row = peer(); calls = []
    def run(args, **kwargs):
        calls.append(args)
        assert args == ['docker', 'inspect', '--type', 'container', 'b'*64]
        return json.dumps([row])
    instance.run = run
    return instance, row, calls


def test_external_peer_is_exact_inspect_with_public_whitelist_only(tmp_path):
    instance, row, calls = peer_coordinator(tmp_path)
    result = instance.openbao_peer_metadata(instance.document)
    assert result['container_id'] == 'b'*64 and result['pid'] == 1234
    assert 'do-not-return' not in json.dumps(result) and 'Config' not in result
    assert len(calls) == 1


@pytest.mark.parametrize('mutation', [
    'id', 'image', 'stopped', 'restarting', 'paused', 'oom', 'pid-zero', 'pid-bool', 'start-time',
    'user', 'group', 'caps', 'cap-add', 'privileged', 'no-new-privileges', 'seccomp',
    'rootfs', 'network', 'ports', 'host-pid', 'host-ipc', 'userns', 'devices', 'docker-socket',
])
def test_external_peer_rejects_substitution_or_isolation_regression(tmp_path, mutation):
    instance, row, calls = peer_coordinator(tmp_path); host = row['HostConfig']; state = row['State']
    if mutation == 'id': row['Id'] = 'd'*64
    elif mutation == 'image': row['Image'] = 'sha256:'+'d'*64
    elif mutation == 'stopped': state['Running'] = False
    elif mutation == 'restarting': state['Restarting'] = True
    elif mutation == 'paused': state['Paused'] = True
    elif mutation == 'oom': state['OOMKilled'] = True
    elif mutation == 'pid-zero': state['Pid'] = 0
    elif mutation == 'pid-bool': state['Pid'] = True
    elif mutation == 'start-time': state['StartedAt'] = '0001-01-01T00:00:00Z'
    elif mutation == 'user': row['Config']['User'] = 'root'
    elif mutation == 'group': host['GroupAdd'] = []
    elif mutation == 'caps': host['CapDrop'] = []
    elif mutation == 'cap-add': host['CapAdd'] = ['SYS_PTRACE']
    elif mutation == 'privileged': host['Privileged'] = True
    elif mutation == 'no-new-privileges': host['SecurityOpt'] = []
    elif mutation == 'seccomp': host['SecurityOpt'].append('seccomp=unconfined')
    elif mutation == 'rootfs': host['ReadonlyRootfs'] = False
    elif mutation == 'network': host['NetworkMode'] = 'host'
    elif mutation == 'ports': row['NetworkSettings']['Ports'] = {'8200/tcp': [{'HostPort':'8200'}]}
    elif mutation == 'host-pid': host['PidMode'] = 'host'
    elif mutation == 'host-ipc': host['IpcMode'] = 'host'
    elif mutation == 'userns': host['UsernsMode'] = 'host'
    elif mutation == 'devices': host['Devices'] = ['/dev/synthetic']
    else: row['Mounts'][0]['Destination'] = '/var/run/docker.sock'
    with pytest.raises(release.Refused, match='openbao_peer_'):
        instance.openbao_peer_metadata(instance.document)


@pytest.mark.parametrize('mutation', ['restart', 'host-pid', 'mount-source'])
def test_peer_restart_or_source_drift_invalidates_prepare_fingerprint(tmp_path, monkeypatch, mutation):
    instance, row, calls = peer_coordinator(tmp_path)
    static = tmp_path/'wrapped'; static.mkdir(); (static/'keys.json').write_text('synthetic-wrapped')
    for service in instance.document['services'].values():
        service['volumes'][0]['source'] = str(static)
    monkeypatch.setattr(release, 'live_directory_metadata', lambda binding: {'syntheticMetadata':binding})
    monkeypatch.setattr(release, 'registry_bind_specs', lambda doc: {})
    instance.fingerprints, instance.mounts = instance.fingerprint(instance.document)
    instance.stable()
    if mutation == 'restart': row['State']['StartedAt'] = '2026-10-09T02:00:00.123456789Z'
    elif mutation == 'host-pid': row['State']['Pid'] += 1
    else: row['Mounts'][0]['Source'] = '/run/replaced/socket'
    with pytest.raises(release.Refused, match='prepared_inputs_changed'):
        instance.stable()


@pytest.mark.parametrize('mutation', ['name', 'short-id', 'image-tag', 'host-pid', 'gate-pid',
    'gate-id', 'cap-add', 'missing-cap-drop', 'privileged', 'no-new-privileges'])
def test_openbao_process_and_peer_declarations_are_mandatory(mutation):
    value = document(); api = value['services']['api']; gate = value['services']['kms-pin-gate']
    if mutation == 'name': api['environment']['RSC_OPENBAO_CONTAINER_ID'] = 'rsc-bao'
    elif mutation == 'short-id': api['environment']['RSC_OPENBAO_CONTAINER_ID'] = 'b'*12
    elif mutation == 'image-tag': api['environment']['RSC_OPENBAO_IMAGE_ID'] = 'openbao:latest'
    elif mutation == 'host-pid': api['pid'] = 'host'
    elif mutation == 'gate-pid': gate['pid'] = 'container:'+'d'*64
    elif mutation == 'gate-id': gate['environment']['RSC_OPENBAO_CONTAINER_ID'] = 'd'*64
    elif mutation == 'cap-add': api['cap_add'] = ['SYS_PTRACE']
    elif mutation == 'missing-cap-drop': gate['cap_drop'] = []
    elif mutation == 'privileged': gate['privileged'] = True
    else: api['security_opt'] = []
    with pytest.raises((ValueError, release.Refused)):
        release.live_bind_specs(value)


@pytest.mark.parametrize('mutation', ['pid', 'caps', 'cap-add', 'privileged', 'no-new-privileges'])
def test_actual_api_inspection_must_match_pid_and_process_restrictions(tmp_path, mutation):
    value = document(); instance = coordinator(tmp_path, value)
    mounts = [dict(Type='bind', Source=binding['source'], Destination=target, RW=False, Propagation='rprivate')
              for (name,target),binding in release.live_bind_specs(value).items() if name == 'api']
    row = dict(Id='a'*64, Image=instance.images['api'], State=dict(Running=True), Mounts=mounts,
        Config=dict(User='41001:41001', Labels={'com.docker.compose.project':instance.project,
            'com.docker.compose.service':'api'}), HostConfig=dict(GroupAdd=['41004'],
            CapDrop=['ALL'], CapAdd=[], Privileged=False, SecurityOpt=['no-new-privileges=true'],
            PidMode='container:'+'b'*64))
    if mutation == 'pid': row['HostConfig']['PidMode'] = 'container:'+'d'*64
    elif mutation == 'caps': row['HostConfig']['CapDrop'] = []
    elif mutation == 'cap-add': row['HostConfig']['CapAdd'] = ['SYS_ADMIN']
    elif mutation == 'privileged': row['HostConfig']['Privileged'] = True
    else: row['HostConfig']['SecurityOpt'] = []
    instance.run = lambda args, **kwargs: json.dumps([row]) if args[:2] == ['docker','inspect'] else 'a'*64
    with pytest.raises(release.Refused, match='container_runtime_'):
        instance.container('api')


@pytest.mark.parametrize('size', [0, 36, 100])
def test_official_sink_temporary_window_waits_for_rename_without_reading(projected, monkeypatch, size):
    row, directory, token = projected
    before = release.live_directory_metadata(row)
    temporary = directory/(row['leaf']+'.tmp.0123abcd')
    temporary.write_bytes(b'x'*size); temporary.chmod(0o440)
    sleeps = []
    def advance(delay):
        sleeps.append(delay); temporary.unlink()
    monkeypatch.setattr(release.time, 'sleep', advance)
    original = os.open
    def metadata_only(path, flags, *args, **kwargs):
        assert str(path) not in (str(token), str(temporary), token.name, temporary.name)
        return original(path, flags, *args, **kwargs)
    monkeypatch.setattr(os, 'open', metadata_only)
    assert release.live_directory_metadata(row) == before and sleeps == [0.025]


@pytest.mark.parametrize('mutation', ['stale', 'two', 'name', 'mode', 'oversize', 'symlink', 'hardlink', 'owner', 'oidc'])
def test_residual_or_unsafe_temporary_files_remain_blocked(projected, monkeypatch, mutation):
    row, directory, token = projected
    temporary = directory/(row['leaf']+'.tmp.0123abcd')
    if mutation == 'symlink': temporary.symlink_to(token)
    elif mutation == 'hardlink': os.link(token, temporary)
    else:
        temporary.write_bytes(b'x'* (4097 if mutation == 'oversize' else 36)); temporary.chmod(0o440)
    if mutation == 'two': (directory/(row['leaf']+'.tmp.abcdef01')).write_text('synthetic')
    elif mutation == 'name': temporary.rename(directory/(row['leaf']+'.tmp.secret-id'))
    elif mutation == 'mode': temporary.chmod(0o640)
    elif mutation == 'oidc': row['kind'] = 'oss_oidc'
    elif mutation == 'owner':
        original = os.stat
        def wrong_owner(path, *args, **kwargs):
            value = original(path, *args, **kwargs)
            if path == temporary.name:
                return SimpleNamespace(st_mode=value.st_mode, st_uid=value.st_uid+1,
                    st_gid=value.st_gid, st_nlink=value.st_nlink, st_size=value.st_size)
            return value
        monkeypatch.setattr(os, 'stat', wrong_owner)
    sleeps=[]; monkeypatch.setattr(release.time, 'sleep', sleeps.append)
    with pytest.raises(release.Refused): release.live_directory_metadata(row)
    assert len(sleeps) <= 3 and temporary.exists() if mutation != 'name' else True


def test_temp_removed_between_list_and_stat_is_rechecked(projected, monkeypatch):
    row, directory, token = projected
    temporary = directory/(row['leaf']+'.tmp.0123abcd')
    temporary.write_bytes(b'x'*36); temporary.chmod(0o440)
    original = os.stat
    def disappear(path, *args, **kwargs):
        if path == temporary.name: temporary.unlink()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', disappear)
    assert release.live_directory_metadata(row)['leafKind'] == 'openbao_token'
