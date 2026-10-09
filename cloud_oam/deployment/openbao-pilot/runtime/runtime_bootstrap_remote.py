"""One initial SecretID per purpose. Secret-bearing stdout is a private pipe only."""
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
PURPOSES = ('transit', 'oss', 'pnvs')
UUID = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'


def allowed(method, path, purpose):
    base = '/v1/auth/rsc-runtime/role/rsc-' + purpose
    return ((method == 'GET' and path in {'/v1/sys/seal-status', '/v1/auth/token/lookup-self', '/v1/sys/auth',
            '/v1/sys/policies/acl/rsc-' + purpose,
            base, base + '/role-id', '/v1/identity/entity/name/rsc-' + purpose})
        or (method == 'POST' and path in {base + '/secret-id', base + '/secret-id-accessor/lookup'}))


def rpc(method, path, token, purpose, body=None):
    require(purpose in PURPOSES and allowed(method, path, purpose), 'bootstrap_operation_denied')
    connection = transport.UnixConnection('localhost', timeout=8)
    try:
        connection.request(method, path, body=None if body is None else json.dumps(body).encode(),
                           headers={'Content-Type': 'application/json', 'X-Vault-Token': token})
        response = connection.getresponse(); raw = response.read(65537)
        require(len(raw) <= 65536, 'bootstrap_response_bound')
        if response.status == 404 and path.endswith('/secret-id-accessor/lookup'):
            return None
        require(response.status == 200, 'bootstrap_rpc_failed_or_unknown')
        value = json.loads(raw)
        require(type(value) is dict and not value.get('errors') and not value.get('warnings')
                and type(value.get('data')) is dict and 'error' not in value['data'], 'bootstrap_response_shape')
        return value['data']
    finally:
        connection.close()


def seal_status(token, purpose):
    # seal-status is the one unwrapped JSON response; no arbitrary RPC escape.
    connection = transport.UnixConnection('localhost', timeout=5)
    try:
        connection.request('GET', '/v1/sys/seal-status')
        response = connection.getresponse(); raw = response.read(65537)
        require(response.status == 200 and len(raw) <= 65536, 'bootstrap_seal_status_unavailable')
        value = json.loads(raw)
        require(value.get('initialized') is True and value.get('sealed') is False
                and value.get('version') == '2.7.1' and value.get('type') == 'shamir', 'bootstrap_requires_unsealed_bao')
    finally:
        connection.close()


def new_record(path, value):
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as output:
        json.dump(value, output, sort_keys=True); output.flush(); os.fsync(output.fileno())
    descriptor = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def read_record(path):
    try:
        descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    try:
        info = os.fstat(descriptor)
        require(stat.S_ISREG(info.st_mode) and info.st_uid == 23101
                and stat.S_IMODE(info.st_mode) == 0o600 and info.st_nlink == 1
                and 0 < info.st_size <= 4096, 'bootstrap_journal_identity')
        return json.loads(os.read(descriptor, 4097))
    finally:
        os.close(descriptor)


def validate_role(value, purpose):
    expected = {'bind_secret_id': True, 'secret_id_num_uses': 1, 'secret_id_ttl': 600,
                'token_type': 'service', 'token_period': 1200, 'token_ttl': 1200,
                'token_max_ttl': 0, 'token_explicit_max_ttl': 0, 'token_num_uses': 0,
                'token_no_default_policy': True, 'token_policies': ['rsc-' + purpose], 'local_secret_ids': False}
    return (type(value) is dict and all(type(value.get(key)) is type(wanted) and value[key] == wanted
            for key, wanted in expected.items()) and value.get('secret_id_bound_cidrs') in (None, [])
            and value.get('token_bound_cidrs') in (None, [])
            and value.get('policies') in (None, expected['token_policies'])
            and value.get('period') in (None, 1200))


def expected_policy(purpose):
    paths = {'auth/token/lookup-self': {'capabilities': ['read']},
             'auth/token/renew-self': {'capabilities': ['update']}}
    if purpose == 'transit':
        paths.update({'transit/decrypt/' + name: {'capabilities': ['update']}
            for name in ('rsc-authentication-idempotency', 'rsc-material-request-contact')})
    else:
        paths['identity/oidc/token/rsc-' + purpose] = {'capabilities': ['read']}
    return {'path': paths}


def handle(request):
    require(type(request) is dict and set(request) == {'operation', 'purpose', 'rootToken', 'instanceId', 'attemptId'}
            and request['operation'] in ('status', 'issue') and request['purpose'] in PURPOSES,
            'bootstrap_request_shape')
    token, purpose = request['rootToken'], request['purpose']
    require(type(token) is str and re.fullmatch(r'[A-Za-z0-9._-]{10,4096}', token)
            and type(request['instanceId']) is str and re.fullmatch(r'rsc-[a-z0-9][a-z0-9-]{2,55}', request['instanceId'])
            and type(request['attemptId']) is str and re.fullmatch(r'[0-9a-f]{12}', request['attemptId']),
            'bootstrap_request_identity')
    seal_status(token, purpose)
    root = rpc('GET', '/v1/auth/token/lookup-self', token, purpose)
    require(root.get('id') == token and root.get('policies') == ['root'], 'bootstrap_root_identity')
    base = '/v1/auth/rsc-runtime/role/rsc-' + purpose
    policy = rpc('GET', '/v1/sys/policies/acl/rsc-' + purpose, token, purpose)
    require(json.loads(policy.get('policy', 'null')) == expected_policy(purpose), 'bootstrap_policy_drift')
    auth = rpc('GET', '/v1/sys/auth', token, purpose).get('rsc-runtime/')
    require(type(auth) is dict and auth.get('type') == 'approle' and auth.get('local') is False
            and type(auth.get('accessor')) is str and auth['accessor'].startswith('auth_approle_'),
            'bootstrap_auth_mount_drift')
    require(validate_role(rpc('GET', base, token, purpose), purpose), 'bootstrap_role_drift')
    role = rpc('GET', base + '/role-id', token, purpose).get('role_id')
    require(type(role) is str and re.fullmatch(UUID, role), 'bootstrap_role_id_shape')
    entity = rpc('GET', '/v1/identity/entity/name/rsc-' + purpose, token, purpose)
    require(entity.get('metadata') == {'rsc_instance': request['instanceId'], 'purpose': purpose}
            and entity.get('disabled') is False and entity.get('policies') in (None, [])
            and entity.get('namespace_id') == 'root'
            and all(entity.get(key) in (None, []) for key in
                    ('group_ids', 'direct_group_ids', 'inherited_group_ids', 'merged_entity_ids'))
            and type(entity.get('aliases')) is list and len(entity['aliases']) == 1
            and entity['aliases'][0].get('name') == role
            and entity['aliases'][0].get('canonical_id') == entity.get('id')
            and entity['aliases'][0].get('mount_accessor') == auth['accessor']
            and entity['aliases'][0].get('mount_path') == 'auth/rsc-runtime/'
            and entity['aliases'][0].get('mount_type') == 'approle'
            and entity['aliases'][0].get('local') is False
            and entity['aliases'][0].get('metadata') in (None, {})
            and entity['aliases'][0].get('custom_metadata') in (None, {})
            and entity['aliases'][0].get('merged_from_canonical_ids') in (None, []), 'bootstrap_entity_binding')
    marker_path = STATE / ('bootstrap-' + purpose + '-attempt.json')
    accessor_path = STATE / ('bootstrap-' + purpose + '-accessor.json')
    binding = {'attemptId': request['attemptId'], 'instanceId': request['instanceId'], 'purpose': purpose,
               'automaticReplayAllowed': False, 'requestMayHaveBeenSent': True}
    previous = read_record(marker_path)
    if previous is not None:
        require(previous == binding, 'bootstrap_prior_attempt_requires_separate_recovery')
    if request['operation'] == 'status':
        saved = read_record(accessor_path)
        if saved is None:
            return {'status': 'read_only', 'attemptPresent': previous is not None,
                    'accessorState': 'unknown' if previous else 'not_issued', 'automaticReplayAllowed': False}
        require(previous is not None and set(saved) == {'attemptId', 'accessor'}
                and saved['attemptId'] == request['attemptId'] and re.fullmatch(UUID, saved['accessor']),
                'bootstrap_accessor_binding')
        found = rpc('POST', base + '/secret-id-accessor/lookup', token, purpose,
                    {'secret_id_accessor': saved['accessor']})
        if found is not None:
            require(found.get('metadata') == {'rsc_instance': request['instanceId'], 'purpose': purpose,
                        'rsc_bootstrap_attempt': request['attemptId']}, 'bootstrap_accessor_metadata_drift')
        return {'status': 'read_only', 'attemptPresent': True,
                'accessorState': 'present' if found is not None else 'absent', 'automaticReplayAllowed': False}
    require(previous is None and read_record(accessor_path) is None, 'bootstrap_already_attempted_no_replay')
    new_record(marker_path, binding)
    metadata = {'rsc_instance': request['instanceId'], 'purpose': purpose, 'rsc_bootstrap_attempt': request['attemptId']}
    generated = rpc('POST', base + '/secret-id', token, purpose,
                    {'ttl': 600, 'num_uses': 1, 'metadata': json.dumps(metadata, sort_keys=True)})
    require(type(generated) is dict and all(type(generated.get(key)) is str and re.fullmatch(UUID, generated[key])
            for key in ('secret_id', 'secret_id_accessor')) and generated.get('secret_id_num_uses') == 1
            and type(generated.get('secret_id_num_uses')) is int and generated.get('secret_id_ttl') == 600
            and type(generated.get('secret_id_ttl')) is int, 'bootstrap_generation_response_unknown')
    new_record(accessor_path, {'attemptId': request['attemptId'], 'accessor': generated['secret_id_accessor']})
    found = rpc('POST', base + '/secret-id-accessor/lookup', token, purpose,
                {'secret_id_accessor': generated['secret_id_accessor']})
    require(found is not None and found.get('metadata') == metadata and found.get('secret_id_num_uses') == 1
            and found.get('secret_id_ttl') == 600, 'bootstrap_creation_readback_unknown')
    return {'status': 'private_credential_issued', 'roleId': role, 'secretId': generated['secret_id'],
            'automaticReplayAllowed': False}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); os.umask(0o077)
    try:
        transport.identity(); raw = sys.stdin.buffer.readline(8193)
        require(len(raw) <= 8192 and raw.endswith(b'\n'), 'bootstrap_input_bound')
        result = handle(json.loads(raw))
    except BaseException as error:
        result = {'status': 'failed_or_unknown', 'safeCode': str(error) if isinstance(error, transport.Rejected)
                  else 'bootstrap_failed_or_interrupted', 'automaticReplayAllowed': False}
    if stat.S_ISFIFO(os.fstat(1).st_mode) or stat.S_ISSOCK(os.fstat(1).st_mode):
        sys.stdout.buffer.write(json.dumps(result).encode() + b'\n'); sys.stdout.buffer.flush()
    return 0 if result['status'] in ('read_only', 'private_credential_issued') else 1


if __name__ == '__main__':
    raise SystemExit(main())
