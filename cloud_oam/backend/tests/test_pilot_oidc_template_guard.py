"""Template-only transient metadata guards; no real credentials or Linux proof."""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scripts import pilot_release as release
from test_pnvs_oidc_deployment import candidate
from pilot_projection_test_support import projection_test_base, safe_projection_root


@pytest.mark.parametrize('purpose', ['OSS', 'PNVS'])
def test_writer_declaration_binds_only_the_exact_purpose_source_identity(purpose):
    document = candidate()
    document['services']['api']['environment']['RSC_' + purpose + '_OIDC_WRITER'] = release.OIDC_TEMPLATE_WRITER
    bindings = release.live_bind_specs(document)
    target = '/run/synthetic/' + ('files' if purpose == 'OSS' else 'pnvs')
    row = bindings['api', target]
    assert row == dict(kind=purpose.lower() + '_oidc', source=target, target=target,
                      leaf='oidc.jwt', owner=41003 if purpose == 'OSS' else 41005,
                      group=41004 if purpose == 'OSS' else 41006, writer=release.OIDC_TEMPLATE_WRITER)
    assert all('writer' not in value for key, value in bindings.items() if key != ('api', target))


@pytest.mark.parametrize('value', ['unknown', 'openbao_file_sink', True, None])
def test_unknown_writer_declarations_fail_closed(value):
    document = candidate()
    document['services']['api']['environment']['RSC_OSS_OIDC_WRITER'] = value
    with pytest.raises(release.Refused, match='runtime_oidc_writer_invalid'):
        release.live_bind_specs(document)


@pytest.mark.parametrize('purpose', ['OSS', 'PNVS'])
@pytest.mark.parametrize('service', ['api', 'kms-pin-gate'])
def test_writer_cannot_be_declared_without_its_oidc_reader(purpose, service):
    document = {'services': {service: {'environment': {
        'RSC_' + purpose + '_OIDC_WRITER': release.OIDC_TEMPLATE_WRITER}}}}
    with pytest.raises(release.Refused, match='runtime_oidc_writer_unbound'):
        release.live_bind_specs(document)


@pytest.fixture
def projection(safe_projection_root, monkeypatch):
    directory = safe_projection_root / 'projection'
    directory.mkdir(mode=0o750); directory.chmod(0o750)
    token = directory / 'oidc.jwt'
    token.write_bytes(b'synthetic-final-token'); token.chmod(0o440)
    row = dict(kind='oss_oidc', owner=os.geteuid(), group=os.getegid(), leaf=token.name,
               source=str(directory.resolve()), target='/run/synthetic/files', writer=release.OIDC_TEMPLATE_WRITER)
    monkeypatch.setattr(release, 'linux_tmpfs_mount', lambda _: ['synthetic-tmpfs', 'nosuid,nodev'])
    return row, directory, token


def temporary(directory, name='4294967295', mode=0o600, size=30):
    path = directory / name
    path.write_bytes(b'x' * size); path.chmod(mode)
    return path


@pytest.mark.parametrize('kind', ['oss_oidc', 'pnvs_oidc'])
@pytest.mark.parametrize('mode,size', [(0o600, 0), (0o600, 16384), (0o440, 30)])
def test_template_transient_wait_is_metadata_only_and_keeps_fingerprint(projection, monkeypatch, kind, mode, size):
    row, directory, token = projection; row['kind'] = kind
    before = release.live_directory_metadata(row)
    pending = temporary(directory, mode=mode, size=size)
    sleeps = []
    def rotate(delay):
        sleeps.append(delay)
        if size:
            pending.chmod(0o440); pending.replace(token)
        else:
            pending.unlink()
    monkeypatch.setattr(release.time, 'sleep', rotate)
    original = os.open
    def directories_only(path, flags, *args, **kwargs):
        assert flags & os.O_DIRECTORY
        return original(path, flags, *args, **kwargs)
    with monkeypatch.context() as guard:
        guard.setattr(os, 'open', directories_only)
        guard.setattr(Path, 'read_bytes', lambda *_: pytest.fail('token content read'))
        guard.setattr(Path, 'read_text', lambda *_: pytest.fail('token content read'))
        assert release.live_directory_metadata(row) == before
    assert sleeps == [0.025] and not pending.exists()
    assert b'synthetic-final-token' not in release.encoded(before)


@pytest.mark.parametrize('name', ['0', '1', '3070093984'])
def test_exact_decimal_name_edges_are_only_transient(projection, monkeypatch, name):
    row, directory, _ = projection; pending = temporary(directory, name=name)
    monkeypatch.setattr(release.time, 'sleep', lambda _: pending.unlink())
    assert release.live_directory_metadata(row)['leafKind'] == 'oss_oidc'


@pytest.mark.parametrize('name', ['00', '01', '-1', '4294967296', '10000000000',
                                 '١٢', '123.tmp', 'oidc.jwt.tmp.0123abcd', 'oidc.jwt.bak'])
def test_wrong_name_is_not_a_template_rotation(projection, name):
    row, directory, _ = projection; pending = temporary(directory, name=name)
    with pytest.raises(release.Refused, match='runtime_projection_extra_file'):
        release.live_directory_metadata(row)
    assert pending.exists()


@pytest.mark.parametrize('mutation', ['undeclared', 'wrong-writer', 'filesink', 'socket'])
def test_numeric_extra_is_not_allowed_for_another_or_undeclared_writer(projection, mutation):
    row, directory, _ = projection; temporary(directory)
    if mutation == 'undeclared': row.pop('writer')
    elif mutation == 'wrong-writer': row['writer'] = 'any-projector'
    elif mutation == 'filesink': row['kind'] = 'openbao_token'
    else: row['kind'] = 'openbao_socket'
    with pytest.raises(release.Refused): release.live_directory_metadata(row)


@pytest.mark.parametrize('mutation', ['two', 'symlink', 'hardlink', 'directory', 'mode',
                                     'oversize', 'owner', 'group', 'persistent'])
def test_unsafe_or_persistent_numeric_extra_is_rejected_without_cleanup(projection, monkeypatch, mutation):
    row, directory, token = projection
    pending = directory / '123456'
    if mutation == 'symlink': pending.symlink_to(token)
    elif mutation == 'hardlink': os.link(token, pending)
    elif mutation == 'directory': pending.mkdir()
    else: temporary(directory, pending.name, mode=0o640 if mutation == 'mode' else 0o600,
                    size=16385 if mutation == 'oversize' else 30)
    if mutation == 'two': temporary(directory, '987654')
    if mutation in ('owner', 'group'):
        original = os.stat
        def wrong_identity(path, *args, **kwargs):
            info = original(path, *args, **kwargs)
            if path == pending.name:
                return SimpleNamespace(st_mode=info.st_mode, st_nlink=info.st_nlink, st_size=info.st_size,
                    st_uid=info.st_uid + (mutation == 'owner'), st_gid=info.st_gid + (mutation == 'group'))
            return info
        monkeypatch.setattr(os, 'stat', wrong_identity)
    sleeps = []; monkeypatch.setattr(release.time, 'sleep', sleeps.append)
    with pytest.raises(release.Refused): release.live_directory_metadata(row)
    assert pending.exists() and len(sleeps) <= 3
    if mutation == 'persistent': assert sleeps == [0.025] * 3


@pytest.mark.parametrize('mutation', ['mode', 'short', 'hardlink', 'parent', 'mount'])
def test_rotation_revalidates_final_leaf_parent_and_mount(projection, monkeypatch, mutation):
    row, directory, token = projection; pending = temporary(directory)
    def rotate(_):
        if mutation == 'short': pending.write_bytes(b'x')
        pending.chmod(0o600 if mutation == 'mode' else 0o440)
        if mutation == 'hardlink': os.link(pending, directory.parent / 'second-link')
        pending.replace(token)
        if mutation == 'parent': directory.rename(directory.with_name('replaced'))
        if mutation == 'mount': monkeypatch.setattr(release, 'linux_tmpfs_mount', lambda _: ['changed'])
    monkeypatch.setattr(release.time, 'sleep', rotate)
    with pytest.raises((release.Refused, FileNotFoundError)): release.live_directory_metadata(row)


def test_temp_disappearing_between_list_and_lstat_is_rechecked(projection, monkeypatch):
    row, directory, _ = projection; pending = temporary(directory)
    original = os.stat
    def remove_before_stat(path, *args, **kwargs):
        if path == pending.name: pending.unlink()
        return original(path, *args, **kwargs)
    monkeypatch.setattr(os, 'stat', remove_before_stat)
    assert release.live_directory_metadata(row)['leafKind'] == 'oss_oidc'


def test_filesink_does_not_gain_numeric_template_names(projection):
    row, directory, _ = projection; row.pop('writer'); row['kind'] = 'openbao_token'
    temporary(directory, mode=0o440)
    with pytest.raises(release.Refused, match='runtime_projection_extra_file'):
        release.live_directory_metadata(row)
