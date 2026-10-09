"""Single explicit 1/1 unseal through a private pipe; no configuration/token IO."""
import json
import os
from pathlib import Path
import re
import resource
import stat
import sys

sys.path.insert(0, '/runtime')
import runtime_init_remote as transport

require = transport.require
STATE = Path('/state')


def seal_status():
    result = transport.rpc('GET', '/v1/sys/seal-status')
    require(result.get('type') == 'shamir' and result.get('version') == '2.7.1'
            and result.get('initialized') is True and type(result.get('sealed')) is bool
            and result.get('n') == 1 and result.get('t') == 1, 'unseal_shamir_contract')
    return result


def unseal_once(share):
    connection = transport.UnixConnection('localhost', timeout=40)
    try:
        connection.request('PUT', '/v1/sys/unseal', body=json.dumps({'key': share}).encode(),
                           headers={'Content-Type': 'application/json'})
        response = connection.getresponse(); body = response.read(65537)
        require(response.status == 200 and len(body) <= 65536, 'unseal_result_unknown')
        result = json.loads(body)
        require(type(result) is dict and result.get('sealed') is False, 'unseal_not_confirmed')
    finally:
        connection.close()


def handle(request):
    require(type(request) is dict and set(request) == {'operation', 'runId', 'pgpFingerprint', 'share'}
            and request['operation'] == 'unseal'
            and type(request['runId']) is str and re.fullmatch(r'[0-9a-f]{12}', request['runId'])
            and type(request['pgpFingerprint']) is str and re.fullmatch(r'[A-F0-9]{40}', request['pgpFingerprint'])
            and type(request['share']) is str and re.fullmatch(r'[0-9a-f]{64}', request['share']), 'unseal_request_shape')
    before = seal_status()
    if before['sealed'] is False:
        return {'status': 'unsealed_verified', 'alreadyUnsealed': True, 'writeAttempted': False,
                'initialized': True, 'sealed': False, 'automaticReplayAllowed': False}
    require(before.get('progress') == 0, 'unseal_partial_progress_requires_review')
    # An existing marker with sealed=true is unresolved, even if a prior call
    # may have failed before sending. No automatic replay of an unknown write.
    path = STATE / ('unseal-attempt-' + request['runId'] + '.json')
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump({'runId': request['runId'], 'pgpFingerprint': request['pgpFingerprint'],
                   'requestMayHaveBeenSent': True, 'automaticReplayAllowed': False}, stream)
        stream.flush(); os.fsync(stream.fileno())
    directory = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(directory)
    finally:
        os.close(directory)
    unseal_once(request['share'])
    after = seal_status(); require(after['sealed'] is False, 'unseal_readback_unknown')
    return {'status': 'unsealed_verified', 'alreadyUnsealed': False, 'writeAttempted': True,
            'initialized': True, 'sealed': False, 'automaticReplayAllowed': False}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); os.umask(0o077)
    try:
        transport.identity()
        raw = sys.stdin.buffer.readline(4097)
        require(len(raw) <= 4096 and raw.endswith(b'\n'), 'unseal_input_bound')
        result = handle(json.loads(raw))
    except BaseException as error:
        result = {'status': 'failed_or_unknown', 'safeCode': str(error) if isinstance(error, transport.Rejected)
                  else 'unseal_interrupted_or_failed', 'automaticReplayAllowed': False}
    if stat.S_ISFIFO(os.fstat(1).st_mode) or stat.S_ISSOCK(os.fstat(1).st_mode):
        sys.stdout.buffer.write(json.dumps(result).encode() + b'\n'); sys.stdout.buffer.flush()
    return 0 if result['status'] == 'unsealed_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
