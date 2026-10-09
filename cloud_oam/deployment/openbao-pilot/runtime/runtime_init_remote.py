"""Private-pipe init endpoint inside the exact Bao container; never unseals.

Invoke only through runtime_init.py. Its stdout is a private SSH pipe, never a
terminal/log/file. Both shares/root token use native PGP wrapping. Only the
encrypted response and a public attempt marker are persisted on the server.
"""
from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
from pathlib import Path
import re
import resource
import socket
import stat
import struct
import sys
import time

SOCKET = '/run/rsc-bao/api.sock'
MARKER = '/state/initialization-attempt.json'
CIPHERTEXT = '/state/initialization-ciphertext.json'


class Rejected(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise Rejected(code)


def identity():
    require(sys.platform == 'linux' and (os.getuid(), os.geteuid(), os.getgid(), os.getegid()) == (23101,) * 4,
            'init_identity')
    require(all(stat.S_ISFIFO(os.fstat(n).st_mode) or stat.S_ISSOCK(os.fstat(n).st_mode) for n in (0, 1)),
            'private_pipes_required')
    require(Path('/sys/fs/cgroup/memory.swap.max').read_text().strip() == '0', 'container_swap_must_be_disabled')
    state = os.stat('/state', follow_symlinks=False)
    require(stat.S_ISDIR(state.st_mode) and (state.st_uid, state.st_gid, stat.S_IMODE(state.st_mode)) ==
            (23101, 23101, 0o700), 'state_directory_identity')
    parent = os.stat('/run/rsc-bao', follow_symlinks=False)
    require(stat.S_ISDIR(parent.st_mode) and (parent.st_uid, parent.st_gid, stat.S_IMODE(parent.st_mode)) ==
            (23101, 23110, 0o750), 'socket_directory_identity')
    info = os.stat(SOCKET, follow_symlinks=False)
    require(stat.S_ISSOCK(info.st_mode) and (info.st_uid, info.st_gid, stat.S_IMODE(info.st_mode), info.st_nlink) ==
            (23101, 23110, 0o660, 1), 'socket_identity')


class UnixConnection(http.client.HTTPConnection):
    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(self.timeout); self.sock.connect(SOCKET)
        pid, uid, gid = struct.unpack('3i', self.sock.getsockopt(socket.SOL_SOCKET, socket.SO_PEERCRED, 12))
        require(pid > 0 and (uid, gid) == (23101, 23101), 'init_peer_identity')


def rpc(method, path, body=None):
    require((method, path) in {('GET', '/v1/sys/init'), ('GET', '/v1/sys/seal-status'), ('PUT', '/v1/sys/init')},
            'init_operation_denied')
    conn = UnixConnection('localhost', timeout=40 if method == 'PUT' else 5)
    try:
        raw = None if body is None else json.dumps(body).encode()
        conn.request(method, path, body=raw, headers={'Content-Type': 'application/json'})
        response = conn.getresponse(); raw = response.read(65537)
        require(response.status == 200 and len(raw) <= 65536, 'init_rpc_failed_or_unknown')
        result = json.loads(raw); require(type(result) is dict, 'init_response_shape')
        return result
    finally:
        conn.close()


def marker_exists():
    try:
        info = os.lstat(MARKER)
    except FileNotFoundError:
        return False
    require(stat.S_ISREG(info.st_mode) and info.st_nlink == 1 and info.st_uid == 23101
            and stat.S_IMODE(info.st_mode) == 0o600, 'attempt_marker_identity')
    return True


def reserve_attempt(run_id, fingerprint):
    fd = os.open(MARKER, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as output:
        json.dump({'schema': 'rsc.openbao.init-attempt.v1', 'runId': run_id, 'pgpFingerprint': fingerprint,
                   'requestMayHaveBeenSent': True, 'recordedAt': int(time.time()),
                   'automaticReplayAllowed': False}, output)
        output.flush(); os.fsync(output.fileno())
    fd = os.open('/state', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def encrypted_materials(value):
    require(type(value) is dict and set(value) == {'keys', 'keys_base64', 'root_token'}, 'ciphertext_response_shape')
    require(type(value['keys']) is list and type(value['keys_base64']) is list
            and len(value['keys']) == len(value['keys_base64']) == 1, 'ciphertext_share_count')
    for item in (value['keys_base64'][0], value['root_token']):
        require(type(item) is str and 256 <= len(item) <= 32768, 'encrypted_packet_bound')
        raw = base64.b64decode(item, validate=True)
        # OpenPGP public-key encrypted session packet must lead; never persist raw
        # shares/service tokens under a misleading ciphertext name.
        require(len(raw) >= 256 and raw[0] & 0x80 and
                ((raw[0] & 63) if raw[0] & 64 else (raw[0] >> 2) & 15) == 1, 'encrypted_packet_shape')
    require(bytes.fromhex(value['keys'][0]) == base64.b64decode(value['keys_base64'][0], validate=True),
            'ciphertext_share_encodings')
    return value


def public_fingerprint(raw):
    require(len(raw) >= 3 and raw[0] & 0x80, 'public_pgp_packet_required')
    if raw[0] & 0x40:
        require(raw[0] & 63 == 6, 'public_pgp_packet_required')
        first = raw[1]
        if first < 192:
            size, start = first, 2
        elif first <= 223:
            size, start = ((first - 192) << 8) + raw[2] + 192, 3
        else:
            require(first == 255 and len(raw) >= 6, 'public_packet_partial_length_denied')
            size, start = int.from_bytes(raw[2:6], 'big'), 6
    else:
        require((raw[0] >> 2) & 15 == 6 and raw[0] & 3 != 3, 'public_pgp_packet_required')
        length_size = (1, 2, 4)[raw[0] & 3]
        size, start = int.from_bytes(raw[1:1 + length_size], 'big'), 1 + length_size
    body = raw[start:start + size]
    require(len(body) == size and 200 <= size <= 65535 and body[0] == 4, 'public_v4_rsa_packet_required')
    require(body[5] == 1, 'public_rsa_encrypt_sign_required')
    # RFC 4880 v4 public-key fingerprint, not a secret/material digest.
    return hashlib.sha1(b'\x99' + size.to_bytes(2, 'big') + body).hexdigest().upper()


def save_ciphertext(run_id, fingerprint, materials):
    result = {'status': 'initialized_pending_custody', 'runId': run_id,
              'pgpFingerprint': fingerprint, 'materials': encrypted_materials(materials)}
    with os.fdopen(os.open(CIPHERTEXT, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as stream:
        json.dump(result, stream); stream.flush(); os.fsync(stream.fileno())
    fd = os.open('/state', os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
    return result


def handle(request):
    require(type(request) is dict and set(request) <= {'operation', 'runId', 'pgpFingerprint', 'pgpPublicKey'}
            and {'operation', 'runId'} <= set(request)
            and request['operation'] in ('status', 'initialize', 'recover')
            and re.fullmatch(r'[0-9a-f]{12}', request['runId']) is not None, 'init_request_shape')
    current = rpc('GET', '/v1/sys/init')
    require(type(current.get('initialized')) is bool, 'init_status_shape')
    marker = marker_exists()
    if request['operation'] == 'status':
        require(set(request) == {'operation', 'runId'}, 'init_request_shape')
        seal = rpc('GET', '/v1/sys/seal-status')
        require(type(seal.get('sealed')) is bool and seal.get('version') == '2.7.1', 'seal_status_shape')
        return {'status': 'read_only', 'initialized': current['initialized'], 'sealed': seal['sealed'],
                'attemptMarkerPresent': marker, 'automaticReplayAllowed': False}
    fingerprint = request.get('pgpFingerprint', '')
    require(type(fingerprint) is str and re.fullmatch(r'[A-F0-9]{40}', fingerprint), 'pgp_fingerprint_shape')
    if request['operation'] == 'recover':
        require(set(request) == {'operation', 'runId', 'pgpFingerprint'} and current['initialized'] and marker,
                'recover_requires_initialized_attempt')
        with os.fdopen(os.open(CIPHERTEXT, os.O_RDONLY | os.O_NOFOLLOW), 'rb') as stream:
            info = os.fstat(stream.fileno())
            require(stat.S_ISREG(info.st_mode) and info.st_uid == 23101 and stat.S_IMODE(info.st_mode) == 0o600
                    and info.st_nlink == 1 and info.st_size <= 65536, 'ciphertext_file_identity')
            result = json.loads(stream.read(65537))
        require(result.get('runId') == request['runId'] and result.get('pgpFingerprint') == fingerprint,
                'recover_binding_mismatch')
        encrypted_materials(result.get('materials')); return result
    require(set(request) == {'operation', 'runId', 'pgpFingerprint', 'pgpPublicKey'}, 'init_request_shape')
    public = request['pgpPublicKey']
    require(type(public) is str and 256 <= len(public) <= 16384, 'pgp_public_key_bound')
    raw = base64.b64decode(public, validate=True)
    require(public_fingerprint(raw) == fingerprint, 'public_pgp_fingerprint_mismatch')
    require(current['initialized'] is False and marker is False, 'init_already_attempted_do_not_replay')
    reserve_attempt(request['runId'], fingerprint)
    # OpenBao encrypts BOTH the share and root token before returning them.
    materials = rpc('PUT', '/v1/sys/init', {'secret_shares': 1, 'secret_threshold': 1,
                    'pgp_keys': [public], 'root_token_pgp_key': public})
    return save_ciphertext(request['runId'], fingerprint, materials)


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); os.umask(0o077)
    try:
        identity()
        raw = sys.stdin.buffer.readline(32769)
        require(len(raw) <= 32768 and raw.endswith(b'\n'), 'init_input_bound')
        result = handle(json.loads(raw))
    except BaseException as error:
        result = {'status': 'failed_or_unknown', 'code': str(error) if isinstance(error, Rejected)
                  else 'init_transport_failed_or_interrupted', 'automaticReplayAllowed': False}
    # This is the sole secret-bearing transport, checked as a pipe above.
    # If identity() rejected stdout, do not risk printing even a status to it.
    mode = os.fstat(1).st_mode
    if stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode):
        sys.stdout.buffer.write(json.dumps(result).encode() + b'\n'); sys.stdout.buffer.flush()
    return 0 if result['status'] in ('read_only', 'initialized_pending_custody') else 1


if __name__ == '__main__':
    raise SystemExit(main())
