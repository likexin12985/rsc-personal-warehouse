"""Read-only Linux host installation gate. No credential reads or remote writes."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import subprocess
import sys

sys.dont_write_bytecode = True

from runtime_bundle import (API_GID, API_UID, BAO_GID, BAO_SHA256, BAO_UID, BINARY,
                            CADDY_BINARY, CADDY_IMAGE, CADDY_SHA256, CONFIG, IMAGE, PURPOSES, RUN, SOCKET_GID,
                            STATE, Rejected, build, require)


def directory(path, uid, gid, mode):
    value = Path(path)
    require(value.is_absolute(), 'absolute_path_required')
    for parent in [*reversed(value.parents), value]:
        info = parent.lstat()
        require(stat.S_ISDIR(info.st_mode) and not stat.S_ISLNK(info.st_mode), 'directory_symlink_or_type')
        if parent == value:
            require((info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) == (uid, gid, mode), 'directory_identity')
        else:
            require(info.st_uid == 0 and not stat.S_IMODE(info.st_mode) & 0o022, 'directory_ancestor')
    return value.stat()


def tmpfs(path, text):
    rows = []
    for line in text.splitlines():
        fields = line.split(); require('-' in fields and len(fields) >= 10, 'mountinfo_shape')
        point = fields[4]
        if '\\' not in point and (path == point or path.startswith(point.rstrip('/') + '/')):
            rows.append((len(point), fields))
    require(rows, 'tmpfs_mount_missing')
    _, row = max(rows, key=lambda item: item[0]); split = row.index('-')
    require(row[split + 1] == 'tmpfs' and {'nosuid', 'nodev'} <= set(row[5].split(',')), 'tmpfs_boundary')


def public_file(path, expected=None, mode=0o444):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_CLOEXEC)
    try:
        info = os.fstat(fd)
        require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and (info.st_uid, info.st_gid) == (0, 0)
                and stat.S_IMODE(info.st_mode) == mode and info.st_size <= 200_000_000,
                'installed_file_identity')
        digest = hashlib.sha256()
        while part := os.read(fd, 1024 * 1024):
            digest.update(part)
        after = os.fstat(fd)
        require((info.st_size, info.st_mtime_ns, info.st_ctime_ns) ==
                (after.st_size, after.st_mtime_ns, after.st_ctime_ns), 'installed_file_changed')
        require(expected is None or digest.hexdigest() == expected, 'installed_source_hash_mismatch')
        return digest.hexdigest()
    finally:
        os.close(fd)


def host_preflight(instance):
    require(sys.platform == 'linux' and os.geteuid() == 0, 'linux_root_preflight_required')
    directory(CONFIG, 0, 0, 0o755)
    expected = build(instance)
    require(set(os.listdir(CONFIG)) == set(expected), 'installed_bundle_entries')
    for name, body in expected.items():
        public_file(str(Path(CONFIG) / name), hashlib.sha256(body).hexdigest())
    directory(str(Path(BINARY).parent), 0, 0, 0o755)
    public_file(BINARY, BAO_SHA256, mode=0o555)
    directory(str(Path(CADDY_BINARY).parent), 0, 0, 0o755)
    public_file(CADDY_BINARY, CADDY_SHA256, mode=0o555)
    require(os.listxattr(CADDY_BINARY, follow_symlinks=False) == [], 'caddy_file_attributes_forbidden')
    for image in (IMAGE, CADDY_IMAGE):
        output = subprocess.run(['/usr/bin/docker', 'image', 'inspect', image, '--format={{.Id}}'],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            timeout=10, env={'PATH': '/usr/bin:/bin'})
        require(output.returncode == 0 and output.stdout.strip().decode('ascii') == image, 'pinned_image_unavailable')
    directory(STATE, BAO_UID, BAO_GID, 0o700)
    directory(RUN, 0, 0, 0o755)
    text = Path('/proc/self/mountinfo').read_text(); require(len(text) < 1024 * 1024, 'mountinfo_bound')
    tmpfs(RUN, text)
    directory(RUN + '/socket', BAO_UID, SOCKET_GID, 0o750)
    for purpose, (uid, gid) in PURPOSES.items():
        directory(RUN + '/token-' + purpose, uid, gid, 0o750)
        directory(RUN + '/bootstrap-' + purpose, uid, gid, 0o700)
    mem = re.search(r'^MemAvailable:\s+(\d+) kB$', Path('/proc/meminfo').read_text(), re.M)
    require(mem and int(mem[1]) >= 1024 * 1024, 'host_memory_headroom')
    disk = os.statvfs(STATE)
    require(disk.f_bavail * disk.f_frsize >= 2 * 1024 ** 3, 'state_disk_headroom')
    return {'status': 'host_files_preflight_passed', 'credentialsRead': False,
            'containerRuntimeVerified': False, 'initPerformed': False, 'productionReady': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--instance-id', required=True)
    args = parser.parse_args()
    try:
        result = host_preflight(args.instance_id)
    except Exception as error:
        result = {'status': 'rejected', 'code': str(error) if isinstance(error, Rejected) else 'host_preflight_failed'}
    print(json.dumps(result)); return 0 if result['status'] == 'host_files_preflight_passed' else 1


if __name__ == '__main__':
    raise SystemExit(main())
