"""First Transit keys and one wrapped response per explicit application version."""
import base64
import json
import os
from pathlib import Path
import re
import resource
import stat
import sys

sys.dont_write_bytecode = True
sys.path.insert(0, '/runtime')
import runtime_init_remote as transport

require = transport.require
STATE = Path('/state')
KEYS = {'authentication_idempotency': 'rsc-authentication-idempotency',
        'material_request_contact': 'rsc-material-request-contact'}
KEY_CREATE = {'type': 'aes256-gcm96', 'derived': True, 'convergent_encryption': False,
              'exportable': False, 'allow_plaintext_backup': False, 'auto_rotate_period': 0}


def allowed(method, path):
    reads = {'/v1/sys/seal-status', '/v1/auth/token/lookup-self', '/v1/sys/mounts'}
    reads |= {'/v1/transit/keys/' + key for key in KEYS.values()}
    writes = {'/v1/sys/mounts/transit'} | {'/v1/transit/keys/' + key for key in KEYS.values()}
    writes |= {'/v1/transit/datakey/wrapped/' + key for key in KEYS.values()}
    return (method == 'GET' and path in reads) or (method == 'POST' and path in writes)


def rpc(method, path, token, body=None):
    require(allowed(method, path), 'transit_operation_denied')
    connection = transport.UnixConnection('localhost', timeout=8)
    try:
        connection.request(method, path, body=None if body is None else json.dumps(body).encode(),
                           headers={'Content-Type': 'application/json', 'X-Vault-Token': token})
        response = connection.getresponse(); raw = response.read(65537)
        require(len(raw) <= 65536, 'transit_response_bound')
        if method == 'GET' and response.status == 404: return None
        require(response.status in (200, 204), 'transit_rpc_failed_or_unknown')
        value = {} if not raw else json.loads(raw)
        require(type(value) is dict and not value.get('errors') and not value.get('warnings'), 'transit_response_error')
        data = value.get('data', value)
        require(type(data) is dict and 'error' not in data and 'plaintext' not in data, 'transit_response_data')
        return data
    finally:
        connection.close()


def record(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as stream:
        json.dump(value, stream, sort_keys=True); stream.flush(); os.fsync(stream.fileno())
    descriptor = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(descriptor)
    finally: os.close(descriptor)


def read_record(path):
    try: descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError: return None
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 23101 and info.st_nlink == 1
                and stat.S_IMODE(info.st_mode) == 0o600 and 0 < info.st_size <= 16384, 'transit_journal_identity')
        return json.loads(os.read(descriptor, 16385))
    finally: os.close(descriptor)


def key_valid(value, name):
    expected = {**KEY_CREATE, 'name': name, 'deletion_allowed': False, 'latest_version': 1,
        'min_available_version': 0, 'min_decryption_version': 1, 'min_encryption_version': 0,
        'supports_encryption': True, 'supports_decryption': True, 'supports_signing': False,
        'supports_derivation': True, 'imported_key': False, 'soft_deleted': False}
    return (type(value) is dict and all(type(value.get(k)) is type(v) and value[k] == v for k, v in expected.items())
        and value.get('kdf') == 'hkdf_sha256' and type(value.get('keys')) is dict and set(value['keys']) == {'1'}
        and type(value['keys']['1']) is int and value['keys']['1'] > 0
        and not any(key in value for key in ('backup_info', 'restore_info', 'external_key_ref', 'key_size',
                                            'imported_key_allow_rotation', 'convergent_encryption_version')))


def ensure_keys(request):
    token = request['rootToken']; instance = request['instanceId']
    def ensure(label, path, read, valid, body):
        current = read()
        if current is not None:
            require(valid(current), 'transit_existing_configuration_drift'); return
        require(request['apply'] is True, 'transit_configuration_missing_readonly')
        record(STATE / ('transit-configure-' + label + '.json'),
               {'attemptId': request['attemptId'], 'instanceId': instance, 'writeMayHaveOccurred': True,
                'automaticReplayAllowed': False})
        rpc('POST', path, token, body)
        require(valid(read()), 'transit_configuration_readback_unknown')
    def mount():
        value = rpc('GET', '/v1/sys/mounts', token)
        require(type(value) is dict, 'transit_mounts_shape'); return value.get('transit/')
    ensure('mount', '/v1/sys/mounts/transit', mount,
        lambda value: type(value) is dict and value.get('type') == 'transit' and value.get('local') is False,
        {'type': 'transit'})
    for name in KEYS.values():
        path = '/v1/transit/keys/' + name
        ensure(name, path, lambda: rpc('GET', path, token), lambda value: key_valid(value, name), KEY_CREATE)


def verify_binding(request):
    contract = request['contract']
    require(type(contract) is dict and set(contract) == {'coordinate', 'keyPath', 'generatePath', 'request'},
            'transit_contract_shape')
    coordinate = contract['coordinate']
    require(type(coordinate) is dict and set(coordinate) == {'purpose', 'environment', 'provider_instance_id', 'application_key_version'}
            and coordinate['purpose'] in KEYS and coordinate['environment'] == 'production'
            and coordinate['provider_instance_id'] == request['instanceId']
            and type(coordinate['application_key_version']) is int and 1 <= coordinate['application_key_version'] <= 2147483647,
            'transit_coordinate_shape')
    name = KEYS[coordinate['purpose']]
    require(contract['keyPath'] == 'transit/keys/' + name
            and contract['generatePath'] == '/v1/transit/datakey/wrapped/' + name, 'transit_coordinate_path')
    body = contract['request']
    require(type(body) is dict and set(body) == {'bits', 'key_version', 'context', 'associated_data'}
            and type(body['bits']) is int and body['bits'] == 256
            and type(body['key_version']) is int and body['key_version'] == 1, 'transit_generation_request')
    expected = {**coordinate, 'application': 'cloud_oam', 'provider': 'openbao_transit_v1', 'key_path': contract['keyPath']}
    for name, schema in (('context', 'rsc.openbao.derivation-context.v1'), ('associated_data', 'rsc.openbao.wrap-aad.v1')):
        raw = body[name]
        require(type(raw) is str and 1 <= len(raw) <= 8192, 'transit_binding_bound')
        decoded = base64.b64decode(raw, validate=True)
        require(base64.b64encode(decoded).decode('ascii') == raw
                and json.loads(decoded) == {**expected, 'schema': schema}, 'transit_binding_mismatch')
    return coordinate


def wrapped_response(value):
    require(type(value) is dict and set(value) == {'ciphertext', 'key_version'}
            and type(value['key_version']) is int and value['key_version'] == 1
            and type(value['ciphertext']) is str, 'transit_wrapped_response_shape')
    match = re.fullmatch(r'vault:v1:([A-Za-z0-9+/]+={0,2})', value['ciphertext'])
    require(match is not None and len(value['ciphertext']) <= 8192, 'transit_wrapped_ciphertext_shape')
    raw = base64.b64decode(match[1], validate=True)
    require(len(raw) == 60 and base64.b64encode(raw).decode('ascii') == match[1], 'transit_wrapped_ciphertext_bound')
    return value


def handle(request):
    require(type(request) is dict and set(request) == {'operation', 'attemptId', 'instanceId', 'rootToken', 'apply', 'contract'}
            and request['operation'] in ('keys', 'generate', 'recover', 'status') and type(request['apply']) is bool,
            'transit_request_shape')
    require(type(request['attemptId']) is str and re.fullmatch(r'[0-9a-f]{12}', request['attemptId'])
            and type(request['instanceId']) is str and re.fullmatch(r'rsc-[a-z0-9][a-z0-9-]{2,55}', request['instanceId'])
            and type(request['rootToken']) is str and re.fullmatch(r'[A-Za-z0-9._-]{10,4096}', request['rootToken']),
            'transit_request_identity')
    coordinate = verify_binding(request)
    label = coordinate['purpose'] + '-v' + str(coordinate['application_key_version'])
    marker, ciphertext = STATE / ('transit-' + label + '-attempt.json'), STATE / ('transit-' + label + '-wrapped.json')
    binding = {'attemptId': request['attemptId'], 'contract': request['contract'], 'automaticReplayAllowed': False}
    if request['operation'] in ('recover', 'status'):
        require(request['apply'] is False, 'transit_read_mode_required')
        prior, saved = read_record(marker), read_record(ciphertext)
        require(prior is None or prior == binding, 'transit_recovery_binding_changed')
        if saved is not None:
            require(prior is not None and set(saved) == {'status', 'binding', 'response'}
                    and saved['binding'] == binding and saved['status'] == 'wrapped_pending_custody', 'transit_saved_binding')
            wrapped_response(saved['response'])
        if request['operation'] == 'status':
            return {'status': 'read_only', 'attemptPresent': prior is not None, 'wrappedResponsePresent': saved is not None,
                    'automaticReplayAllowed': False, 'productionReady': False}
        require(saved is not None, 'transit_result_unknown_no_replay'); return saved
    status = rpc('GET', '/v1/sys/seal-status', request['rootToken'])
    require(status.get('initialized') is True and status.get('sealed') is False
            and status.get('version') == '2.7.1' and status.get('type') == 'shamir', 'transit_requires_unsealed_bao')
    root = rpc('GET', '/v1/auth/token/lookup-self', request['rootToken'])
    require(root.get('id') == request['rootToken'] and root.get('policies') == ['root'], 'transit_root_identity')
    if request['operation'] == 'keys':
        ensure_keys(request)
        return {'status': 'keys_verified', 'wrappedDataKeysGenerated': False, 'automaticReplayAllowed': False,
                'productionReady': False}
    require(request['apply'] is True, 'transit_generate_requires_explicit_apply')
    ensure_keys({**request, 'apply': False})
    require(read_record(marker) is None and read_record(ciphertext) is None, 'transit_already_attempted_no_replay')
    record(marker, binding)
    value = wrapped_response(rpc('POST', request['contract']['generatePath'], request['rootToken'], request['contract']['request']))
    result = {'status': 'wrapped_pending_custody', 'binding': binding, 'response': value}
    record(ciphertext, result)
    require(read_record(ciphertext) == result, 'transit_ciphertext_readback_unknown')
    return result


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); os.umask(0o077)
    try:
        transport.identity(); raw = sys.stdin.buffer.readline(32769)
        require(len(raw) <= 32768 and raw.endswith(b'\n'), 'transit_input_bound')
        result = handle(json.loads(raw))
    except BaseException as error:
        result = {'status': 'failed_or_unknown', 'safeCode': str(error) if isinstance(error, transport.Rejected)
                  else 'transit_operation_failed_or_interrupted', 'automaticReplayAllowed': False}
    if stat.S_ISFIFO(os.fstat(1).st_mode) or stat.S_ISSOCK(os.fstat(1).st_mode):
        sys.stdout.buffer.write(json.dumps(result).encode() + b'\n'); sys.stdout.buffer.flush()
    return 0 if result['status'] in ('keys_verified', 'read_only', 'wrapped_pending_custody') else 1


if __name__ == '__main__':
    raise SystemExit(main())
