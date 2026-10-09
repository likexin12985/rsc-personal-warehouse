"""Render public-only, pinned pilot runtime files; never start or initialize Bao."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys

BAO_VERSION = '2.7.1'
BAO_SHA256 = '535cf827b13753046757f5ec8b97ae0ef21f10a40ffe673f0d5e75616170f5ea'
IMAGE = 'sha256:bb684823763f0af1a33d7ccc81c9335d328e0e23b28c324088e9bec2a00d8575'
CADDY_IMAGE = 'sha256:234958d6760cef50f79875faf17fd59c4b1d1d6b84eb77f3654c424efbcaa4b5'
CADDY_SHA256 = '4ef1f68c70219536b2711fd16547a79841a2dec2d6b4e56b1e3e5e9da76028e6'
CADDY_BINARY = '/opt/rsc-openbao/caddy/caddy'
CONFIG = '/etc/rsc-openbao'
RUN = '/run/rsc-openbao'
STATE = '/var/lib/rsc-openbao'
BINARY = '/opt/rsc-openbao/2.7.1/bao'
BAO_UID, BAO_GID, SOCKET_GID, API_UID, API_GID = 23101, 23101, 23110, 23204, 23204
PURPOSES = {'transit': (23102, 23110), 'oss': (23202, 23212), 'pnvs': (23203, 23213)}
SELF_POLICY = {'auth/token/lookup-self': {'capabilities': ['read']},
               'auth/token/renew-self': {'capabilities': ['update']}}


class Rejected(ValueError):
    pass


def require(value, code):
    if not value:
        raise Rejected(code)


def encoded(value):
    return (json.dumps(value, indent=2, sort_keys=True) + '\n').encode()


def bind(source, target, readonly):
    return {'type': 'bind', 'source': source, 'target': target, 'read_only': readonly,
            'bind': {'create_host_path': False, 'propagation': 'rprivate'}}


def service(name, uid, gid, memory):
    return {'image': IMAGE, 'pull_policy': 'never', 'container_name': 'rsc-bao-' + name,
            'user': f'{uid}:{gid}', 'group_add': [str(SOCKET_GID)],
            'read_only': True, 'cap_drop': ['ALL'], 'cap_add': [], 'privileged': False,
            'security_opt': ['no-new-privileges:true'], 'network_mode': 'none',
            'ipc': 'private', 'restart': 'no', 'logging': {'driver': 'none'},
            'mem_limit': memory, 'memswap_limit': memory, 'pids_limit': 128,
            'cpus': 0.5, 'ulimits': {'core': {'soft': 0, 'hard': 0}},
            'environment': {'GOMAXPROCS': '2', 'GOMEMLIMIT': '96MiB' if name != 'server' else '260MiB'},
            'tmpfs': ['/tmp:rw,noexec,nosuid,nodev,size=8388608,mode=1777'],
            'entrypoint': ['/runtime/bao'], 'volumes': [bind(BINARY, '/runtime/bao', True)]}


def gateway_config():
    # Rewrite to an exact constant after matching. Query strings never reach Bao.
    header = '''{
    admin off
    auto_https off
}
:8080 {
    @query expression `{http.request.uri.query} != ""`
    respond @query 404
'''
    for name, path in [('discovery', '/v1/identity/oidc/.well-known/openid-configuration'),
                       ('jwks', '/v1/identity/oidc/.well-known/keys')]:
        header += f'''    @{name} {{
        method GET
        path {path}
    }}
    handle @{name} {{
        rewrite * {path}
        reverse_proxy unix//run/rsc-bao/api.sock {{
            header_up -*
            header_up Host localhost
            transport http {{
                dial_timeout 2s
                response_header_timeout 3s
            }}
        }}
    }}
'''
    return header + '    handle {\n        respond 404\n    }\n}\n'


def build(instance):
    require(type(instance) is str and re.fullmatch(r'rsc-[a-z0-9][a-z0-9-]{2,55}', instance),
            'instance_id_invalid')
    files = {}
    server = {
        'ui': False, 'raw_storage_endpoint': False, 'disable_mlock': True,
        'log_level': 'error', 'api_addr': 'http://127.0.0.1:1',
        'cluster_addr': 'https://127.0.0.1:19201',
        'storage': {'raft': {'path': '/state/raft', 'node_id': instance}},
        'listener': [
            {'unix': {'address': '/run/rsc-bao/api.sock', 'socket_mode': '0660',
                      'socket_user': str(BAO_UID), 'socket_group': str(SOCKET_GID)}},
            {'tcp': {'address': '127.0.0.1:19200', 'cluster_address': '127.0.0.1:19201',
                     'tls_disable': True, 'telemetry': {'metrics_only': True,
                                                      'unauthenticated_metrics_access': False}}}],
        'unsafe_allow_api_audit_creation': False,
        # Scalar description is required by the locked HCL JSON flattener.
        'audit': [{'file': {'rsc-runtime': {'description': 'RSC pilot runtime audit',
                   'options': {'file_path': '/state/audit.jsonl', 'log_raw': 'false', 'mode': '0600'}}}}]}
    files['server.json'] = encoded(server)
    bao = service('server', BAO_UID, BAO_GID, '384m')
    bao['command'] = ['server', '-config=/runtime/server.json']
    bao['volumes'] += [bind(CONFIG + '/server.json', '/runtime/server.json', True),
                       bind(CONFIG + '/runtime_init_remote.py', '/runtime/runtime_init_remote.py', True),
                       bind(RUN + '/socket', '/run/rsc-bao', False), bind(STATE, '/state', False)]
    services = {'bao': bao}
    roles = {}
    for purpose, (uid, gid) in PURPOSES.items():
        token_dir = '/run/projection'
        agent = {'exit_after_auth': False, 'vault': {'address': 'unix:///run/rsc-bao/api.sock',
                 'retry': {'num_retries': 2}}, 'auto_auth': {'method': [{
                    'type': 'approle', 'mount_path': 'auth/rsc-runtime',
                    'min_backoff': '1s', 'max_backoff': '10s', 'config': {
                        'role_id_file_path': '/run/bootstrap/role-id',
                        'secret_id_file_path': '/run/bootstrap/secret-id',
                        'remove_secret_id_file_after_reading': True}}]}}
        policy = {'path': dict(SELF_POLICY)}
        if purpose == 'transit':
            policy['path'].update({f'transit/decrypt/{key}': {'capabilities': ['update']}
                for key in ('rsc-authentication-idempotency', 'rsc-material-request-contact')})
            agent['auto_auth']['sinks'] = [{'sink': {'type': 'file', 'config': {
                'path': token_dir + '/api.token', 'mode': 0o440, 'uid': -1, 'gid': -1}}}]
        else:
            policy['path'][f'identity/oidc/token/rsc-{purpose}'] = {'capabilities': ['read']}
            agent['template_config'] = {'static_secret_render_interval': '120s',
                                        'exit_on_retry_failure': True}
            agent['template'] = [{'contents': '{{- with secret "identity/oidc/token/rsc-' + purpose +
                '" -}}{{ .Data.token }}{{- end -}}', 'destination': token_dir + '/oidc.jwt',
                'perms': '0440', 'backup': False, 'create_dest_dirs': False,
                'error_on_missing_key': True, 'wait': {'min': '0s', 'max': '0s'}}]
        files[purpose + '-agent.json'] = encoded(agent)
        files[purpose + '-policy.json'] = encoded(policy)
        roles[purpose] = {'bind_secret_id': True, 'secret_id_num_uses': 1, 'secret_id_ttl': '600s',
                         'token_type': 'service', 'token_period': '1200s', 'token_ttl': '1200s',
                         'token_max_ttl': 0, 'token_explicit_max_ttl': 0, 'token_num_uses': 0,
                         'token_no_default_policy': True, 'token_policies': ['rsc-' + purpose]}
        child = service(purpose, uid, gid, '128m')
        child['profiles'] = ['agents']
        child['command'] = ['agent', '-log-level=error', '-config=/runtime/agent.json']
        child['volumes'] += [bind(CONFIG + '/' + purpose + '-agent.json', '/runtime/agent.json', True),
                            bind(RUN + '/socket', '/run/rsc-bao', True),
                            bind(RUN + '/token-' + purpose, token_dir, False),
                            bind(RUN + '/bootstrap-' + purpose, '/run/bootstrap', False)]
        services[purpose] = child
    files['approle-contract.json'] = encoded(roles)
    gateway = service('metadata', 23205, 23205, '32m')
    gateway.pop('network_mode'); gateway.pop('environment')
    gateway.update(image=CADDY_IMAGE, entrypoint=['/runtime/caddy'], profiles=['metadata'],
                   command=['run', '--config=/etc/caddy/Caddyfile', '--adapter=caddyfile'],
                   networks=['metadata'], cpus=0.25, pids_limit=64)
    gateway['volumes'] = [bind(CONFIG + '/gateway.Caddyfile', '/etc/caddy/Caddyfile', True),
                          bind(CADDY_BINARY, '/runtime/caddy', True),
                          bind(RUN + '/socket', '/run/rsc-bao', True)]
    gateway['tmpfs'] += ['/data:rw,noexec,nosuid,nodev,size=4194304,uid=23205,gid=23205,mode=0700',
                        '/config:rw,noexec,nosuid,nodev,size=4194304,uid=23205,gid=23205,mode=0700']
    services['metadata'] = gateway
    files['gateway.Caddyfile'] = gateway_config().encode()
    files['compose.json'] = encoded({'name': 'rsc-openbao', 'services': services,
                'networks': {'metadata': {'internal': True, 'name': 'rsc-openbao-metadata'}}})
    tmpfiles = [f'd {RUN} 0755 0 0 -', f'd {RUN}/socket 0750 {BAO_UID} {SOCKET_GID} -']
    for purpose, (uid, gid) in PURPOSES.items():
        tmpfiles += [f'd {RUN}/token-{purpose} 0750 {uid} {gid} -',
                     f'd {RUN}/bootstrap-{purpose} 0700 {uid} {gid} -']
    files['rsc-openbao.conf'] = ('\n'.join(tmpfiles) + '\n').encode()
    files['rsc-openbao.service'] = ('''[Unit]
Description=RSC pilot OpenBao (starts sealed; explicit operator bootstrap)
Requires=docker.service
After=docker.service systemd-tmpfiles-setup.service
ConditionPathExists=/etc/rsc-openbao/compose.json
[Service]
Type=oneshot
RemainAfterExit=yes
UMask=0077
LimitCORE=0
ExecStart=/usr/bin/docker compose --env-file /dev/null -f /etc/rsc-openbao/compose.json up -d --no-build --pull never bao
ExecStop=/usr/bin/docker compose --env-file /dev/null -f /etc/rsc-openbao/compose.json stop bao
StandardOutput=null
StandardError=null
TimeoutStartSec=60
TimeoutStopSec=40
[Install]
WantedBy=multi-user.target
''').replace('ExecStart=', 'ExecStartPre=/usr/bin/python3 -B /etc/rsc-openbao/runtime_preflight.py --instance-id ' + instance + '\nExecStart=').encode()
    files['public-plan.json'] = encoded({
        'schema': 'rsc.openbao.runtime-plan.v1', 'instanceId': instance,
        'officialVersion': BAO_VERSION, 'binarySha256': BAO_SHA256, 'imageId': IMAGE,
        'caddyImageId': CADDY_IMAGE, 'issuerOrigin': 'https://rscwz.cn',
        'caddyBinarySha256': CADDY_SHA256, 'caddyFileCapabilitiesAllowed': False,
        'issuer': 'https://rscwz.cn/v1/identity/oidc',
        'identities': {'bao': [BAO_UID, BAO_GID], 'api': [API_UID, API_GID], **PURPOSES},
        'memoryLimitTotalMiB': 800, 'pidBinding': 'API and gate require inspected exact Bao container ID',
        'projectionDeclarations': {
            'RSC_OSS_OIDC_WRITER': 'openbao_agent_template_v1',
            'RSC_PNVS_OIDC_WRITER': 'openbao_agent_template_v1'},
        'shamir': {'shares': 1, 'threshold': 1, 'custodians': 1, 'media': 1},
        'transitApiCanRenewOwnToken': True, 'defaultPolicyAllowed': False,
        'actualPeriodicRenewalVerified': False, 'actualRuntimeConfigured': False,
        'initPerformed': False, 'productionReady': False})
    for name in ('runtime_init_remote.py', 'runtime_preflight.py', 'runtime_bundle.py'):
        files[name] = Path(__file__).with_name(name).read_bytes()
    files['manifest.json'] = encoded({'schema': 'rsc.openbao.public-bundle.v1',
           'sha256': {name: hashlib.sha256(body).hexdigest() for name, body in files.items()},
           'productionReady': False})
    return files


def write_new_bundle(output, files):
    require(output.is_absolute() and not output.exists() and not output.is_symlink(), 'new_bundle_required')
    require(output.parent.is_dir() and output.parent.resolve() == output.parent, 'bundle_parent_invalid')
    output.mkdir(mode=0o700)
    for name, content in files.items():
        with os.fdopen(os.open(output / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600), 'wb') as stream:
            stream.write(content); stream.flush(); os.fsync(stream.fileno())
    # Configs are public, but the install directory remains root controlled.
    return {'status': 'public_bundle_created', 'fileCount': len(files), 'productionReady': False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--instance-id', required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = write_new_bundle(args.output, build(args.instance_id))
    except Exception as error:
        result = {'status': 'rejected', 'code': str(error) if isinstance(error, Rejected) else 'bundle_generation_failed'}
    print(json.dumps(result))
    return 0 if result['status'] == 'public_bundle_created' else 1


if __name__ == '__main__':
    raise SystemExit(main())
