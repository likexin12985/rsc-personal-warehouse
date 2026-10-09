"""Explicit fresh-host file installation only: no Docker/systemd/init commands.

All destination roots/files must be absent. Partial outcomes remain for exact
review; never overwrite, delete, chown an existing service tree, or auto-retry.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
import sys

sys.dont_write_bytecode = True

from runtime_bundle import (BAO_GID, BAO_SHA256, BAO_UID, BINARY, CADDY_BINARY, CADDY_SHA256, CONFIG, PURPOSES,
                            RUN, SOCKET_GID, STATE, Rejected, build, require)
from runtime_preflight import directory, host_preflight, tmpfs


def fresh_directory(path, uid, gid, mode):
    target = Path(path)
    require(not target.exists() and not target.is_symlink(), 'destination_already_exists')
    parent = target.parent
    info = parent.lstat()
    require(stat.S_ISDIR(info.st_mode) and info.st_uid == 0 and not stat.S_IMODE(info.st_mode) & 0o022
            and parent.resolve(strict=True) == parent, 'install_parent_uncontrolled')
    os.mkdir(target, mode=mode); os.chmod(target, mode); os.chown(target, uid, gid)


def copy_new(path, body, mode):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, mode)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(body); stream.flush(); os.fsync(stream.fileno()); os.fchmod(stream.fileno(), mode)
    require(Path(path).read_bytes() == body, 'installed_file_readback_mismatch')


def caddy_source(path):
    require(path.is_absolute() and path.resolve(strict=True) == path and path.is_file(), 'caddy_source_identity')
    info = path.lstat()
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1
            and os.listxattr(path, follow_symlinks=False) == [], 'caddy_source_attributes_forbidden')
    body = path.read_bytes()
    require(hashlib.sha256(body).hexdigest() == CADDY_SHA256, 'caddy_source_hash')
    return body


def install(source, binary, caddy_binary, instance):
    require(sys.platform == 'linux' and os.geteuid() == 0, 'linux_root_install_required')
    require(source.is_absolute() and source.resolve(strict=True) == source, 'bundle_source_identity')
    expected = build(instance)
    require(set(os.listdir(source)) == set(expected), 'source_bundle_entries')
    for name, body in expected.items():
        info = (source / name).lstat()
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and (source / name).read_bytes() == body,
                'source_bundle_changed')
    require(binary.is_absolute() and binary.resolve(strict=True) == binary and binary.is_file(), 'binary_source_identity')
    raw = binary.read_bytes(); require(hashlib.sha256(raw).hexdigest() == BAO_SHA256, 'binary_source_hash')
    caddy_bytes = caddy_source(caddy_binary)
    new_roots = [Path(CONFIG), Path(RUN), Path(STATE), Path('/opt/rsc-openbao')]
    unit = Path('/etc/systemd/system/rsc-openbao.service')
    tmpfile = Path('/etc/tmpfiles.d/rsc-openbao.conf')
    require(all(not path.exists() and not path.is_symlink() for path in [*new_roots, unit, tmpfile]),
            'fresh_install_only_existing_state_requires_review')
    for parent in ('/etc', '/run', '/var/lib', '/opt', '/etc/systemd/system', '/etc/tmpfiles.d'):
        info = Path(parent).lstat()
        require(not stat.S_IMODE(info.st_mode) & 0o022, 'install_parent_writable_by_other_identity')
        directory(parent, 0, info.st_gid, stat.S_IMODE(info.st_mode))
    tmpfs(RUN, Path('/proc/self/mountinfo').read_text())
    fresh_directory(CONFIG, 0, 0, 0o755)
    for name, body in expected.items():
        copy_new(str(Path(CONFIG) / name), body, 0o444)
    fresh_directory('/opt/rsc-openbao', 0, 0, 0o755)
    fresh_directory('/opt/rsc-openbao/2.7.1', 0, 0, 0o755)
    copy_new(BINARY, raw, 0o555)
    fresh_directory('/opt/rsc-openbao/caddy', 0, 0, 0o755)
    copy_new(CADDY_BINARY, caddy_bytes, 0o555)
    fresh_directory(STATE, BAO_UID, BAO_GID, 0o700)
    fresh_directory(RUN, 0, 0, 0o755)
    fresh_directory(RUN + '/socket', BAO_UID, SOCKET_GID, 0o750)
    for purpose, (uid, gid) in PURPOSES.items():
        fresh_directory(RUN + '/token-' + purpose, uid, gid, 0o750)
        fresh_directory(RUN + '/bootstrap-' + purpose, uid, gid, 0o700)
    copy_new(unit, expected['rsc-openbao.service'], 0o444)
    copy_new(tmpfile, expected['rsc-openbao.conf'], 0o444)
    host_preflight(instance)
    return {'status': 'fresh_files_installed_verified', 'serviceStarted': False,
            'serviceEnabled': False, 'initPerformed': False, 'productionReady': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--bundle', type=Path, required=True); parser.add_argument('--binary', type=Path, required=True)
    parser.add_argument('--caddy-binary', type=Path, required=True)
    parser.add_argument('--instance-id', required=True); args = parser.parse_args()
    try:
        result = install(args.bundle, args.binary, args.caddy_binary, args.instance_id)
    except BaseException as error:
        result = {'status': 'failed_or_incomplete_no_replay', 'code': str(error) if isinstance(error, Rejected)
                  else 'installation_failed', 'automaticCleanup': False, 'automaticReplayAllowed': False}
    print(json.dumps(result)); return 0 if result['status'] == 'fresh_files_installed_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
