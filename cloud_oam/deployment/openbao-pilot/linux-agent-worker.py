"""Inside-container helper for the new synthetic Linux Agent proof only.

No cloud calls, Docker socket, business writes, persisted logs or real secrets.
The host controller owns disposable containers and supplies synthetic bootstrap
through stdin. API mode uses the unchanged real production transport.
"""
from __future__ import annotations

import base64
import errno
import hashlib
import http.client
import json
import os
from pathlib import Path
import resource
import socket
import stat
import struct
import sys
import time
import types

BAO_UID, AGENT_UID, API_UID, SHARED_GID = 23101, 23102, 23103, 23110
SOCKET = Path('/run/rsc-bao/api.sock')
TOKEN = Path('/run/rsc-bao-token/api.token')
BOOTSTRAP = Path('/run/rsc-bao-bootstrap')
KEYS = ('rsc-authentication-idempotency', 'rsc-material-request-contact')


class Rejected(RuntimeError):
    pass


def require(value, code):
    if not value:
        raise Rejected(code)


def emit(value):
    print(json.dumps(value, separators=(',', ':')), flush=True)


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(str(SOCKET))


def rpc(command):
    """Bootstrap fixture RPC: raw result only to the controller's private pipe."""
    conn = UnixConnection('localhost', timeout=15)
    try:
        body = command.get('body')
        headers = {'Content-Type': 'application/json', 'Connection': 'close'}
        if command.get('token'):
            headers['X-Vault-Token'] = command['token']
        conn.request(command['method'], command['path'],
                     body=None if body is None else json.dumps(body), headers=headers)
        response = conn.getresponse()
        raw = response.read(1024 * 1024 + 1)
        require(len(raw) <= 1024 * 1024, 'rpc_response_bound')
        return {'status': response.status, 'body': json.loads(raw) if raw else {}}
    finally:
        conn.close()


def boundary(uid, gid, memory, pids, cpu):
    require(sys.platform == 'linux' and os.getuid() == os.geteuid() == uid
            and os.getgid() == os.getegid() == gid, 'runtime_identity')
    require(SHARED_GID in {os.getegid(), *os.getgroups()}, 'shared_group')
    values = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines() if ':' in line)
    require(values['NoNewPrivs'].strip() == '1' and values['Seccomp'].strip() == '2', 'process_restrictions')
    require(all(int(values[key].strip(), 16) == 0 for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')), 'no_capabilities')
    if uid in (BAO_UID, AGENT_UID):
        service = dict(line.split(':', 1) for line in Path('/proc/1/status').read_text().splitlines() if ':' in line)
        require(set(map(int, service['Uid'].split())) == {uid}
                and set(map(int, service['Gid'].split())) == {gid}
                and service['NoNewPrivs'].strip() == '1' and service['Seccomp'].strip() == '2'
                and all(int(service[key].strip(), 16) == 0 for key in ('CapInh', 'CapPrm', 'CapEff', 'CapBnd', 'CapAmb')),
                'real_service_pid_one_identity')
        require(os.readlink('/proc/1/exe') == '/proof/bao', 'real_official_process')
    require(resource.getrlimit(resource.RLIMIT_CORE) == (0, 0), 'no_core')
    require({line.split(':', 1)[0].strip() for line in Path('/proc/net/dev').read_text().splitlines()[2:]} == {'lo'}, 'network_isolation')
    rows = [line.split() for line in Path('/proc/self/mountinfo').read_text().splitlines()]
    require('ro' in next(row for row in rows if row[4] == '/')[5].split(','), 'readonly_root')
    group = Path('/sys/fs/cgroup')
    require((group / 'memory.max').read_text().strip() == str(memory)
            and (group / 'memory.swap.max').read_text().strip() == '0'
            and (group / 'pids.max').read_text().strip() == str(pids), 'cgroup_limits')
    quota, period = map(int, (group / 'cpu.max').read_text().split())
    require(quota / period == cpu, 'cpu_limit')
    for path, readonly in (('/run/rsc-bao', True), ('/run/rsc-bao-token', True)) if uid == API_UID else ():
        row = next(row for row in rows if row[4] == path)
        require(row[row.index('-') + 1] == 'tmpfs' and 'ro' in row[5].split(','), 'readonly_tmpfs_directory')
        require(os.statvfs(path).f_flag & os.ST_RDONLY, 'kernel_readonly_filesystem')
    return {'uid': uid, 'gid': gid, 'groups': os.getgroups(), 'capabilitiesEmpty': True,
            'noNewPrivileges': True, 'seccompFiltering': True, 'networkNone': True,
            'rootReadOnly': True, 'memoryLimitBytes': memory, 'swapLimitBytes': 0,
            'pidsLimit': pids, 'cpuLimitCores': cpu, 'coreDumpDisabled': True,
            'servicePidOneIdentityVerified': uid in (BAO_UID, AGENT_UID)}


def keeper():
    require(os.geteuid() == 0, 'keeper_setup_identity')
    for path, owner, group, mode in (
        (SOCKET.parent, BAO_UID, SHARED_GID, 0o750),
        (TOKEN.parent, AGENT_UID, SHARED_GID, 0o750),
        (BOOTSTRAP, AGENT_UID, SHARED_GID, 0o700),
    ):
        os.chmod(path, mode)
        os.chown(path, owner, group)
    os.setgroups([])
    os.setgid(65534)
    os.setuid(65534)
    # Keep tmpfs volumes mounted while other containers start; no credentials.
    time.sleep(180)


def bootstrap(command):
    require(os.geteuid() == AGENT_UID and os.getegid() == SHARED_GID, 'bootstrap_identity')
    for name in ('role-id', 'secret-id'):
        descriptor = os.open(BOOTSTRAP / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
        with os.fdopen(descriptor, 'w') as stream:
            stream.write(command[name])
    emit({'bootstrapWritten': True})


def snapshot():
    info = TOKEN.stat()
    require(stat.S_IMODE(info.st_mode) == 0o440 and info.st_uid == AGENT_UID
            and info.st_gid == SHARED_GID and info.st_nlink == 1, 'token_stat')
    return hashlib.sha256(TOKEN.read_bytes()).digest(), info.st_ino


def api():
    package = types.ModuleType('app')
    package.__path__ = ['/proof/app']
    sys.modules['app'] = package
    from app.openbao_runtime_transport import OpenBaoUnixDecryptTransport
    from app.openbao_transit_candidate import OpenBaoDecryptRequest
    verified = boundary(API_UID, API_UID, 64 * 1024 * 1024, 32, 0.25)
    require(not BOOTSTRAP.exists() and not Path('/state').exists(), 'bootstrap_and_raft_not_mounted')
    for target in (SOCKET.parent / 'api-must-not-write', TOKEN.parent / 'api-must-not-write'):
        try:
            with target.open('xb'):
                pass
        except OSError as error:
            require(error.errno in (errno.EROFS, errno.EACCES), 'write_denial_errno')
        else:
            raise Rejected('readonly_write_succeeded')
    endpoint = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        endpoint.connect(str(SOCKET))
        pid, uid, gid = struct.unpack('3i', endpoint.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        # PID may be zero in a sibling PID namespace; the real transport itself
        # requires positive PID, so the API must share Bao's PID namespace.
        require(pid > 0 and uid == gid == BAO_UID, 'real_peer_credentials')
        try:
            environment = os.open('/proc/' + str(pid) + '/environ', os.O_RDONLY | os.O_CLOEXEC)
        except OSError as error:
            require(error.errno in (errno.EACCES, errno.EPERM), 'peer_environment_denial')
        else:
            os.close(environment)
            raise Rejected('peer_environment_unexpectedly_readable')
    finally:
        endpoint.close()
    transport = OpenBaoUnixDecryptTransport(socket_path=str(SOCKET), token_file=str(TOKEN),
        api_uid=API_UID, bao_uid=BAO_UID, bao_gid=BAO_UID, shared_gid=SHARED_GID,
        token_projector_uid=AGENT_UID)
    first, first_inode = snapshot()
    emit({'ready': True, 'boundary': verified, 'peerUid': uid, 'peerGid': gid,
          'peerPidPositive': True, 'tokenMode': '0440', 'socketMode': '0660',
          'peerEnvironmentOpenDenied': True,
          'bootstrapAndRaftAbsent': True, 'readonlyWriteDenied': True,
          'realTransportConstructed': True})
    for line in sys.stdin:
        command = json.loads(line)
        op = command['op']
        if op == 'exit':
            emit({'exited': True})
            return
        if op == 'rotated':
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                latest, latest_inode = snapshot()
                if latest != first and latest_inode != first_inode:
                    break
                time.sleep(0.1)
            else:
                raise Rejected('rotation_deadline')
        statuses = []
        for key in KEYS:
            payload = command['wrapped'][key]
            request = OpenBaoDecryptRequest(path='/v1/transit/decrypt/' + key, **payload)
            result = transport.decrypt(request=request, timeout_seconds=3)
            statuses.append(result.status_code)
            if command['expect'] == 200:
                require(len(base64.b64decode(result.body['data']['plaintext'], validate=True)) == 32, 'decrypt_plaintext_length')
        require(statuses == [command['expect']] * 2, 'decrypt_status')
        emit({'op': op, 'statusCodes': statuses, 'sameTransportInstance': True,
              'rotatedInodeObserved': op == 'rotated'})


def main():
    try:
        operation = sys.argv[1]
        if operation == 'keeper':
            keeper()
        elif operation == 'rpc':
            emit(rpc(json.load(sys.stdin)))
        elif operation == 'bootstrap':
            bootstrap(json.load(sys.stdin))
        elif operation == 'api':
            api()
        elif operation == 'stat':
            path = Path(sys.argv[2])
            info = path.stat()
            emit({'uid': info.st_uid, 'gid': info.st_gid, 'mode': oct(stat.S_IMODE(info.st_mode)), 'exists': True})
        elif operation == 'agent-boundary':
            emit({'boundary': boundary(AGENT_UID, SHARED_GID, 128 * 1024 * 1024, 64, 0.25),
                  'secretIdRemoved': not (BOOTSTRAP / 'secret-id').exists()})
        elif operation == 'bao-boundary':
            emit({'boundary': boundary(BAO_UID, BAO_UID, 384 * 1024 * 1024, 128, 0.5)})
        else:
            raise Rejected('unknown_operation')
        return 0
    except Exception as error:
        emit({'failed': True, 'safeCode': str(error) if isinstance(error, Rejected) else 'worker_failure'})
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
