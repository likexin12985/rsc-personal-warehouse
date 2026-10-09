"""Private-pipe bootstrap phases: exact create-or-verify, never overwrite drift."""
import json
import os
from pathlib import Path
import re
import resource
import stat
import sys

sys.path.insert(0, '/runtime')
sys.dont_write_bytecode = True
import runtime_init_remote as transport

require = transport.require
STATE = Path('/state')
ALLOW_WRITES = False
PURPOSES = ('transit', 'oss', 'pnvs')
KEY_NAMES = ('rsc-authentication-idempotency', 'rsc-material-request-contact')
OIDC_ISSUER_WARNING = (
    'If "issuer" is set explicitly, all tokens must be '
    'validated against that address, including those issued by secondary '
    'clusters. Setting issuer to "" will restore the default behavior of '
    "using the cluster's api_addr as the issuer."
)


def allowed(method, path):
    reads = {'/v1/sys/seal-status', '/v1/auth/token/lookup-self', '/v1/sys/auth',
             '/v1/identity/oidc/config', '/v1/identity/oidc/key/rsc-runtime'}
    writes = {'/v1/sys/auth/rsc-runtime', '/v1/identity/entity-alias',
              '/v1/identity/oidc/config', '/v1/identity/oidc/key/rsc-runtime'}
    for purpose in PURPOSES:
        common = {'/v1/sys/policies/acl/rsc-' + purpose, '/v1/auth/rsc-runtime/role/rsc-' + purpose,
                  '/v1/identity/entity/name/rsc-' + purpose}
        reads |= common | {'/v1/auth/rsc-runtime/role/rsc-' + purpose + '/role-id'}
        writes |= common
    for purpose in ('oss', 'pnvs'):
        reads.add('/v1/identity/oidc/role/rsc-' + purpose); writes.add('/v1/identity/oidc/role/rsc-' + purpose)
    return (method == 'GET' and path in reads) or (method == 'POST' and path in writes)


def rpc(method, path, token, body=None):
    require(allowed(method, path), 'configure_operation_denied')
    connection = transport.UnixConnection('localhost', timeout=8)
    try:
        connection.request(method, path, body=None if body is None else json.dumps(body).encode(),
            headers={'Content-Type': 'application/json', 'X-Vault-Token': token})
        response = connection.getresponse(); raw = response.read(262145)
        require(len(raw) <= 262144, 'configure_response_bound')
        if response.status == 404 and method == 'GET':
            return None
        require(response.status in (200, 204), 'configure_rpc_failed_or_unknown')
        value = {} if not raw else json.loads(raw)
        require(type(value) is dict and not value.get('errors'), 'configure_response_error')
        require(method != 'GET' or not value.get('warnings'), 'configure_read_warning_requires_review')
        data = value.get('data', value)
        # Pinned OpenBao returns data:null plus this warning after storing issuer.
        # This acknowledges only the POST envelope; ensure still verifies a GET.
        if (data is None and response.status == 200 and method == 'POST'
                and path == '/v1/identity/oidc/config' and body == {'issuer': 'https://rscwz.cn'}
                and value.get('warnings') == [OIDC_ISSUER_WARNING]):
            data = {}
        require(type(data) is dict and 'error' not in data, 'configure_response_data')
        return data
    finally:
        connection.close()


def reserve(label, run_id):
    require(re.fullmatch(r'[a-z-]{1,64}', label), 'configure_journal_label')
    path = STATE / ('configure-' + label + '.json')
    with os.fdopen(os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'w') as stream:
        json.dump({'operation': label, 'runId': run_id, 'writeMayHaveOccurred': True,
                   'automaticReplayAllowed': False}, stream)
        stream.flush(); os.fsync(stream.fileno())
    fd = os.open(STATE, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure(label, read, valid, write, run_id, absent=lambda value: value is None):
    current = read()
    if not absent(current):
        require(valid(current), 'existing_configuration_drift')
        return current, False
    require(ALLOW_WRITES, 'configuration_missing_readonly_no_write')
    reserve(label, run_id)
    write()
    current = read()
    require(not absent(current) and valid(current), 'configuration_readback_failed_or_unknown')
    return current, True


def role_contract(purpose):
    return {'bind_secret_id': True, 'secret_id_num_uses': 1, 'secret_id_ttl': 600,
            'token_type': 'service', 'token_period': 1200, 'token_ttl': 1200,
            'token_max_ttl': 0, 'token_explicit_max_ttl': 0, 'token_num_uses': 0,
            'token_no_default_policy': True, 'token_policies': ['rsc-' + purpose]}


def policy(purpose):
    paths = {'auth/token/lookup-self': {'capabilities': ['read']},
             'auth/token/renew-self': {'capabilities': ['update']}}
    if purpose == 'transit':
        paths.update({'transit/decrypt/' + name: {'capabilities': ['update']} for name in KEY_NAMES})
    else:
        paths['identity/oidc/token/rsc-' + purpose] = {'capabilities': ['read']}
    return {'path': paths}


def role_valid(value, expected):
    return (type(value) is dict and all(type(value.get(k)) is type(v) and value[k] == v for k, v in expected.items())
        and value.get('secret_id_bound_cidrs') in (None, []) and value.get('token_bound_cidrs') in (None, [])
        and value.get('local_secret_ids') is False
        and value.get('policies') in (None, expected['token_policies'])
        and value.get('period') in (None, expected['token_period']))


def entity_valid(value, name, instance):
    return (type(value) is dict and value.get('name') == name and value.get('disabled') is False
        and value.get('namespace_id') == 'root' and value.get('policies') in (None, [])
        and value.get('group_ids') in (None, []) and value.get('direct_group_ids') in (None, [])
        and value.get('inherited_group_ids') in (None, [])
        and value.get('merged_entity_ids') in (None, [])
        and value.get('metadata') == {'rsc_instance': instance, 'purpose': name.removeprefix('rsc-')}
        and type(value.get('id')) is str and re.fullmatch(r'[0-9a-f-]{36}', value['id']) is not None
        and type(value.get('aliases')) is list)


def configure_auth(request):
    token = request['rootToken']; run = request['runId']; instance = request['instanceId']
    def auth_read():
        table = rpc('GET', '/v1/sys/auth', token)
        require(type(table) is dict, 'auth_mounts_shape'); return table.get('rsc-runtime/')
    auth, _ = ensure('approle-mount', auth_read,
        lambda value: value.get('type') == 'approle' and value.get('local') is False
            and type(value.get('accessor')) is str and value['accessor'].startswith('auth_approle_'),
        lambda: rpc('POST', '/v1/sys/auth/rsc-runtime', token, {'type': 'approle'}), run)
    entities = {}; role_ids = set()
    for purpose in PURPOSES:
        path = '/v1/sys/policies/acl/rsc-' + purpose; expected = policy(purpose)
        def valid_policy(value):
            try:
                return json.loads(value['policy']) == expected
            except (KeyError, ValueError, TypeError):
                return False
        ensure('policy-' + purpose, lambda: rpc('GET', path, token), valid_policy,
               lambda: rpc('POST', path, token, {'policy': json.dumps(expected)}), run)
        path = '/v1/auth/rsc-runtime/role/rsc-' + purpose; expected = role_contract(purpose)
        ensure('role-' + purpose, lambda: rpc('GET', path, token), lambda value: role_valid(value, expected),
               lambda: rpc('POST', path, token, expected), run)
        role = rpc('GET', path + '/role-id', token); role_id = role.get('role_id')
        require(type(role_id) is str and re.fullmatch(r'[0-9a-f-]{36}', role_id), 'role_identifier_shape')
        require(role_id not in role_ids, 'role_identifiers_not_distinct'); role_ids.add(role_id)
        name = 'rsc-' + purpose; entity_path = '/v1/identity/entity/name/' + name
        entity, _ = ensure('entity-' + purpose, lambda: rpc('GET', entity_path, token),
            lambda value: entity_valid(value, name, instance),
            lambda: rpc('POST', entity_path, token, {'name': name, 'policies': [], 'disabled': False,
                'metadata': {'rsc_instance': instance, 'purpose': purpose}}), run)
        def aliases_read():
            current = rpc('GET', entity_path, token)
            require(entity_valid(current, name, instance), 'entity_drift_during_alias')
            aliases = current['aliases']; return aliases if aliases else None
        def aliases_valid(aliases):
            return (type(aliases) is list and len(aliases) == 1
                and aliases[0].get('canonical_id') == entity['id'] and aliases[0].get('mount_accessor') == auth['accessor']
                and aliases[0].get('name') == role_id and aliases[0].get('local') is False
                and aliases[0].get('metadata') in (None, {})
                and aliases[0].get('mount_type') == 'approle' and aliases[0].get('mount_path') == 'auth/rsc-runtime/'
                and aliases[0].get('custom_metadata') in (None, {})
                and aliases[0].get('merged_from_canonical_ids') in (None, []))
        ensure('alias-' + purpose, aliases_read, aliases_valid,
               lambda: rpc('POST', '/v1/identity/entity-alias', token,
                   {'canonical_id': entity['id'], 'mount_accessor': auth['accessor'], 'name': role_id}), run)
        entities[purpose] = entity['id']
    require(len(set(entities.values())) == 3, 'entities_not_distinct')
    return {'entities': entities, 'policyBusinessAndSelfPathsVerified': True,
            'roleIdsEmitted': False, 'secretIdsCreated': False}


def configure_oidc(request):
    token = request['rootToken']; run = request['runId']
    wanted = {'issuer': 'https://rscwz.cn'}
    ensure('oidc-issuer', lambda: rpc('GET', '/v1/identity/oidc/config', token), lambda value: value == wanted,
        lambda: rpc('POST', '/v1/identity/oidc/config', token, wanted), run,
        absent=lambda value: value is None or value == {'issuer': ''})
    wanted = {'algorithm': 'RS256', 'allowed_client_ids': [request['ossAudience'], request['pnvsAudience']],
              'rotation_period': 86400, 'verification_ttl': 86400}
    ensure('oidc-signing-key', lambda: rpc('GET', '/v1/identity/oidc/key/rsc-runtime', token),
        lambda value: value == wanted,
        lambda: rpc('POST', '/v1/identity/oidc/key/rsc-runtime', token, wanted), run)
    for purpose in ('oss', 'pnvs'):
        path = '/v1/identity/oidc/role/rsc-' + purpose
        wanted = {'key': 'rsc-runtime', 'client_id': request[purpose + 'Audience'], 'ttl': 600, 'template': ''}
        ensure('oidc-role-' + purpose, lambda: rpc('GET', path, token), lambda value: value == wanted,
               lambda: rpc('POST', path, token, wanted), run)
    return {'issuer': 'https://rscwz.cn/v1/identity/oidc', 'jwtTtlSeconds': 600,
            'cloudTrustConfigured': False, 'jwtIssued': False}


def handle(request):
    global ALLOW_WRITES
    fields = {'phase', 'runId', 'rootToken', 'instanceId', 'ossAudience', 'pnvsAudience', 'apply'}
    require(type(request) is dict and set(request) == fields and request['phase'] in ('auth', 'oidc')
            and type(request['apply']) is bool, 'configure_request_shape')
    ALLOW_WRITES = request['apply']
    require(type(request['runId']) is str and re.fullmatch(r'[0-9a-f]{12}', request['runId'])
            and type(request['instanceId']) is str and re.fullmatch(r'rsc-[a-z0-9][a-z0-9-]{2,55}', request['instanceId'])
            and type(request['rootToken']) is str and re.fullmatch(r'[A-Za-z0-9._-]{10,4096}', request['rootToken']),
            'configure_identity_shape')
    require(all(type(request[key]) is str and re.fullmatch(r'[A-Za-z0-9._:/-]{1,256}', request[key])
                for key in ('ossAudience', 'pnvsAudience')), 'oidc_audience_shape')
    require(request['ossAudience'] != request['pnvsAudience'], 'oidc_audiences_not_distinct')
    status = rpc('GET', '/v1/sys/seal-status', request['rootToken'])
    require(status.get('initialized') is True and status.get('sealed') is False and status.get('version') == '2.7.1'
            and status.get('type') == 'shamir', 'configure_requires_unsealed_pinned_bao')
    token = rpc('GET', '/v1/auth/token/lookup-self', request['rootToken'])
    require(token.get('id') == request['rootToken'] and token.get('policies') == ['root'], 'configure_root_identity')
    details = configure_auth(request) if request['phase'] == 'auth' else configure_oidc(request)
    return {'status': 'phase_verified', 'phase': request['phase'], 'runId': request['runId'],
            'details': details, 'writeModeEnabled': ALLOW_WRITES, 'automaticReplayAllowed': False, 'productionReady': False}


def main():
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0)); os.umask(0o077)
    try:
        transport.identity(); raw = sys.stdin.buffer.readline(16385)
        require(len(raw) <= 16384 and raw.endswith(b'\n'), 'configure_input_bound')
        result = handle(json.loads(raw))
    except BaseException as error:
        result = {'status': 'failed_or_unknown', 'safeCode': str(error) if isinstance(error, transport.Rejected)
                  else 'configuration_interrupted_or_failed', 'automaticReplayAllowed': False}
    if stat.S_ISFIFO(os.fstat(1).st_mode) or stat.S_ISSOCK(os.fstat(1).st_mode):
        sys.stdout.buffer.write(json.dumps(result).encode() + b'\n'); sys.stdout.buffer.flush()
    return 0 if result['status'] == 'phase_verified' else 1


if __name__ == '__main__':
    raise SystemExit(main())
