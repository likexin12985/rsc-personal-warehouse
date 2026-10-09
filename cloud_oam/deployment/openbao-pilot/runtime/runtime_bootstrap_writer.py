"""One-purpose private tmpfs writer; no Bao socket, network or root token."""
import json
import os
from pathlib import Path
import re
import resource
import stat
import sys

DIRECTORY = Path('/run/bootstrap')
IDENTITIES = {'transit': (23102, 23110), 'oss': (23202, 23212), 'pnvs': (23203, 23213)}


class Rejected(ValueError):
    pass


def require(value, code):
    if not value:
        raise Rejected(code)


def entries(directory, uid, gid):
    info = directory.lstat()
    require(stat.S_ISDIR(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode)) ==
            (uid, gid, 0o700), 'bootstrap_directory_identity')
    names = os.listdir(directory)
    require(set(names) <= {'role-id', 'secret-id'}, 'bootstrap_unexpected_entries')
    for name in names:
        info = (directory / name).lstat()
        require(stat.S_ISREG(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink) ==
                (uid, gid, 0o600, 1) and info.st_size == 36, 'bootstrap_file_identity')
    return {'roleIdPresent': 'role-id' in names, 'secretIdPresent': 'secret-id' in names}


def write_once(directory, uid, gid, role_id, secret_id):
    require(all(type(value) is str and re.fullmatch(r'[0-9a-f-]{36}', value)
                for value in (role_id, secret_id)), 'bootstrap_identifier_shape')
    require(not any(entries(directory, uid, gid).values()), 'bootstrap_not_empty_no_overwrite')
    parent = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for name, value in (('role-id', role_id), ('secret-id', secret_id)):
            fd = os.open(name, os.O_RDWR | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=parent)
            try:
                os.fchmod(fd, 0o600); raw = value.encode('ascii')
                require(os.write(fd, raw) == len(raw), 'bootstrap_short_write')
                os.fsync(fd); os.lseek(fd, 0, os.SEEK_SET)
                require(os.read(fd, 37) == raw, 'bootstrap_readback_mismatch')
            finally:
                os.close(fd)
            os.fsync(parent)
        require(all(entries(directory, uid, gid).values()), 'bootstrap_postwrite_metadata')
    finally:
        os.close(parent)
    return {'status': 'private_bootstrap_written', 'bothFilesFsyncReadbackVerified': True,
            'credentialValuesEmitted': False, 'automaticReplayAllowed': False}


def handle(value):
    require(type(value) is dict and value.get('purpose') in IDENTITIES
            and value.get('operation') in ('status', 'write'), 'bootstrap_request_shape')
    fields = {'operation', 'purpose'} | ({'roleId', 'secretId'} if value['operation'] == 'write' else set())
    require(set(value) == fields and sys.platform == 'linux', 'bootstrap_closed_shape')
    uid, gid = IDENTITIES[value['purpose']]
    require((os.getuid(), os.geteuid(), os.getgid(), os.getegid()) == (uid, uid, gid, gid)
            and set(os.getgroups()) <= {gid}, 'bootstrap_process_identity')
    require(all(stat.S_ISFIFO(os.fstat(n).st_mode) or stat.S_ISSOCK(os.fstat(n).st_mode) for n in (0, 1)),
            'bootstrap_private_pipe_required')
    status = Path('/proc/self/status').read_text()
    for name, expected in (('CapEff', '0000000000000000'), ('NoNewPrivs', '1'), ('Seccomp', '2')):
        found = re.search('^' + name + r':\s*(\S+)$', status, re.M)
        require(found and found[1] == expected, 'bootstrap_process_isolation')
    require(Path('/sys/fs/cgroup/memory.swap.max').read_text().strip() == '0', 'bootstrap_swap_enabled')
    require(Path('/sys/fs/cgroup/memory.max').read_text().strip() == str(32 * 1024 ** 2), 'bootstrap_memory_limit')
    mounts = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    selected = [row for row in mounts if len(row) >= 10 and row[4] == str(DIRECTORY)]
    require(len(selected) == 1 and '-' in selected[0]
            and selected[0][selected[0].index('-') + 1] == 'tmpfs'
            and {'rw', 'nosuid', 'nodev'} <= set(selected[0][5].split(',')), 'bootstrap_tmpfs_required')
    roots = [row for row in mounts if len(row) >= 10 and row[4] == '/']
    require(len(roots) == 1 and 'ro' in roots[0][5].split(','), 'bootstrap_readonly_root_required')
    require(not Path('/state').exists() and not Path('/run/rsc-bao').exists(), 'bootstrap_forbidden_mount')
    if value['operation'] == 'status':
        return {'status': 'private_bootstrap_metadata', **entries(DIRECTORY, uid, gid),
                'credentialContentsRead': False, 'automaticReplayAllowed': False}
    return write_once(DIRECTORY, uid, gid, value['roleId'], value['secretId'])


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); os.umask(0o077)
    try:
        require(all(stat.S_ISFIFO(os.fstat(n).st_mode) or stat.S_ISSOCK(os.fstat(n).st_mode) for n in (0, 1)),
                'bootstrap_private_pipe_required')
        raw = sys.stdin.buffer.readline(2049)
        require(len(raw) <= 2048 and raw.endswith(b'\n'), 'bootstrap_input_bound')
        result = handle(json.loads(raw))
    except BaseException as error:
        result = {'status': 'failed_or_unknown', 'safeCode': str(error) if isinstance(error, Rejected)
                  else 'bootstrap_writer_failed_or_interrupted', 'automaticReplayAllowed': False}
    if stat.S_ISFIFO(os.fstat(1).st_mode) or stat.S_ISSOCK(os.fstat(1).st_mode):
        sys.stdout.buffer.write(json.dumps(result).encode() + b'\n'); sys.stdout.buffer.flush()
    return 0 if result['status'] in ('private_bootstrap_metadata', 'private_bootstrap_written') else 1


if __name__ == '__main__':
    raise SystemExit(main())
