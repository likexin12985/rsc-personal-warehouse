"""Public application wiring for the pinned three-Agent pilot runtime.

This module never starts Compose, reads projected credentials or claims live
identity. Supply independently inspected public coordinates; shape checks do
not authenticate them. Merge its YAML with the reviewed business Compose via
Docker Compose >=2.24.4, then validate the resolved JSON in memory. Pass the
reviewed single merged candidate to pilot_release; its live guards still apply.
No general-purpose YAML merge or credential environment forwarding is provided.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import pilot_preflight as preflight, pilot_release as release

IMAGE = 'sha256:bb684823763f0af1a33d7ccc81c9335d328e0e23b28c324088e9bec2a00d8575'
ISSUER = 'https://rscwz.cn/v1/identity/oidc'
REGISTRY = '/etc/rsc-openbao-registry'
FIELDS = frozenset(('container_id', 'image_id', 'instance_id', 'registry_host_dir',
    'account_id', 'provider_arn', 'oss_role_arn', 'pnvs_role_arn', 'oss_subject',
    'pnvs_subject', 'oss_audience', 'pnvs_audience', 'auth_key_version', 'contact_key_version'))
OVERRIDE_LISTS = frozenset(('cap_drop', 'cap_add', 'security_opt', 'group_add'))


class Rejected(ValueError):
    """Stable codes only; never reflect supplied configuration values."""


def require(ok, code):
    if not ok:
        raise Rejected(code)


def public_config(value):
    require(type(value) is dict and set(value) == FIELDS, 'public_fields_invalid')
    require(all(type(v) is str for k, v in value.items()
                if k not in ('auth_key_version', 'contact_key_version')), 'public_type_invalid')
    require(re.fullmatch(r'[0-9a-f]{64}', value['container_id']) is not None,
            'bao_container_id_invalid')
    require(value['image_id'] == IMAGE, 'bao_image_invalid')
    require(re.fullmatch(r'rsc-[a-z0-9][a-z0-9-]{2,55}', value['instance_id']) is not None,
            'instance_invalid')
    require(value['registry_host_dir'] == REGISTRY, 'registry_source_invalid')
    require(re.fullmatch(r'[0-9]{16}', value['account_id']) is not None, 'account_invalid')
    account = value['account_id']
    require(re.fullmatch(r'acs:ram::' + account + r':oidc-provider/[A-Za-z0-9._-]{1,128}',
                        value['provider_arn']) is not None, 'oidc_provider_invalid')
    for purpose in ('oss', 'pnvs'):
        require(re.fullmatch(r'acs:ram::' + account + r':role/[A-Za-z0-9.@_-]{1,64}',
                            value[purpose + '_role_arn']) is not None, 'oidc_role_invalid')
        # Keep interpolation/control characters out of emitted Compose YAML.
        require(re.fullmatch(r'[A-Za-z0-9._:/-]{1,256}', value[purpose + '_audience']) is not None,
                'oidc_audience_invalid')
    for key in ('auth_key_version', 'contact_key_version'):
        require(type(value[key]) is int and 1 <= value[key] <= preflight.MAX_APPLICATION_KEY_VERSION,
                'application_key_version_invalid')
    return dict(value)


def bind(source, target):
    return dict(type='bind', source=source, target=target, read_only=True,
                bind=dict(create_host_path=False, propagation='rprivate'))


def _document(c):
    common = dict(OAM_ENVIRONMENT='production',
        RSC_OPENBAO_CONTAINER_ID=c['container_id'], RSC_OPENBAO_IMAGE_ID=c['image_id'],
        OAM_OPENBAO_PROVIDER_INSTANCE_ID=c['instance_id'],
        OAM_OPENBAO_ENCRYPTED_DATA_KEY_REGISTRY_PATH='/run/rsc-bao-registry/registry.json',
        OAM_OPENBAO_SOCKET_PATH='/run/rsc-bao/api.sock',
        OAM_OPENBAO_TOKEN_FILE='/run/rsc-bao-token/api.token',
        OAM_OPENBAO_API_UID='23204', OAM_OPENBAO_BAO_UID='23101', OAM_OPENBAO_BAO_GID='23101',
        OAM_OPENBAO_SHARED_GID='23110', OAM_OPENBAO_TOKEN_PROJECTOR_UID='23102',
        OAM_AUTH_IDEMPOTENCY_ENCRYPTION_PROVIDER='openbao_transit_v1',
        OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_PROVIDER='openbao_transit_v1',
        OAM_AUTH_IDEMPOTENCY_ENCRYPTION_KEY_VERSION=str(c['auth_key_version']),
        OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_KEY_VERSION=str(c['contact_key_version']))
    services = {}
    for name in ('api', 'kms-pin-gate'):
        services[name] = dict(user='23204:23204', pid='container:' + c['container_id'],
            cap_drop=['ALL'], cap_add=[], privileged=False,
            security_opt=['no-new-privileges:true'],
            group_add=['23110', '23212', '23213'] if name == 'api' else ['23110'],
            environment=dict(common), volumes=[
                bind(c['registry_host_dir'], '/run/rsc-bao-registry'),
                bind('/run/rsc-openbao/socket', '/run/rsc-bao'),
                bind('/run/rsc-openbao/token-transit', '/run/rsc-bao-token')])
    api = services['api']; env = api['environment']
    env.update(OAM_FILE_STORAGE_CREDENTIAL_MODE='oidc_role_arn',
        OAM_FILE_STORAGE_OIDC_ROLE_ARN=c['oss_role_arn'],
        OAM_FILE_STORAGE_OIDC_PROVIDER_ARN=c['provider_arn'],
        OAM_FILE_STORAGE_OIDC_TOKEN_FILE='/run/rsc-identity/files/oidc.jwt',
        OAM_FILE_STORAGE_OIDC_SESSION_NAME='rsc-pilot-files',
        RSC_OSS_OIDC_PROJECTOR_UID='23202', RSC_OSS_OIDC_SHARED_GID='23212',
        RSC_OSS_OIDC_WRITER='openbao_agent_template_v1',
        RSC_OSS_OIDC_SUBJECT=c['oss_subject'], RSC_OSS_OIDC_AUDIENCE=c['oss_audience'],
        RSC_OIDC_EXPECTED_ACCOUNT_ID=c['account_id'], RSC_OIDC_ISSUER_URL=ISSUER,
        OAM_SMS_CREDENTIAL_MODE='default_chain',
        RSC_PNVS_OIDC_ROLE_ARN=c['pnvs_role_arn'], RSC_PNVS_OIDC_PROVIDER_ARN=c['provider_arn'],
        RSC_PNVS_OIDC_TOKEN_FILE='/run/rsc-identity/pnvs/oidc.jwt',
        RSC_PNVS_OIDC_SESSION_NAME='rsc-pilot-pnvs',
        RSC_PNVS_OIDC_PROJECTOR_UID='23203', RSC_PNVS_OIDC_SHARED_GID='23213',
        RSC_PNVS_OIDC_WRITER='openbao_agent_template_v1',
        RSC_PNVS_OIDC_SUBJECT=c['pnvs_subject'], RSC_PNVS_OIDC_AUDIENCE=c['pnvs_audience'],
        ALIBABA_CLOUD_CLI_PROFILE_DISABLED='true', ALIBABA_CLOUD_CREDENTIALS_FILE='/dev/null',
        ALIBABA_CLOUD_PROFILE='', ALIBABA_CLOUD_ECS_METADATA_DISABLED='true',
        ALIBABA_CLOUD_ECS_METADATA='', ALIBABA_CLOUD_CREDENTIALS_URI='',
        ALIBABA_CLOUD_STS_REGION='cn-hangzhou', ALIBABA_CLOUD_VPC_ENDPOINT_ENABLED='false')
    for left, right in preflight.PNVS_OIDC_COORDINATES:
        env[right] = env[left]
    api['volumes'] += [bind('/run/rsc-openbao/token-oss', '/run/rsc-identity/files'),
                       bind('/run/rsc-openbao/token-pnvs', '/run/rsc-identity/pnvs')]
    return dict(services=services)


def build(value):
    config = public_config(value)
    result = _document(config)
    validate_resolved(result, config)
    return result


def validate_resolved(document, value):
    """Only public wiring; preserve mandatory later live release checks."""
    expected = _document(public_config(value))
    try:
        require(type(document) is dict and type(document.get('services')) is dict,
                'resolved_document_invalid')
        services = document['services']
        for name, wanted in expected['services'].items():
            actual = services[name]
            env = actual['environment']
            require(type(env) is dict and all(env.get(k) == v for k, v in wanted['environment'].items()),
                    'resolved_environment_mismatch')
            require(all(not env.get(k) for k in preflight.PNVS_STATIC_FIELDS), 'static_credentials_forbidden')
            if name == 'kms-pin-gate':
                require(not any(v for k, v in env.items() if k.startswith((
                    'OAM_FILE_STORAGE_OIDC_', 'RSC_OSS_OIDC_', 'RSC_PNVS_OIDC_'))),
                    'gate_oidc_forbidden')
            for key in ('user', 'pid', 'cap_drop', 'cap_add', 'privileged', 'security_opt', 'group_add'):
                # Compose may normalize NNP spelling and group numbers.
                if key == 'security_opt':
                    require(preflight.restricted_process_configuration(actual), 'process_isolation_invalid')
                elif key == 'group_add':
                    groups = actual.get(key)
                    require(type(groups) is list and len(groups) == len(wanted[key])
                            and {str(g) for g in groups} == set(wanted[key]), 'runtime_groups_invalid')
                elif key == 'cap_add':
                    require(actual.get(key, []) == [], 'process_isolation_invalid')
                elif key == 'privileged':
                    require(actual.get(key) is None or actual.get(key) is False,
                            'process_isolation_invalid')
                else:
                    require(actual.get(key) == wanted[key], 'process_identity_mismatch')
            require(not actual.get('devices') and actual.get('ipc', 'private') == 'private'
                    and actual.get('userns_mode', '') == '', 'process_namespace_invalid')
            for wanted_mount in wanted['volumes']:
                matching = [m for m in actual.get('volumes', []) if m.get('target') == wanted_mount['target']]
                require(len(matching) == 1, 'runtime_mount_missing_or_duplicate')
                mount = matching[0]
                require(all(mount.get(k) == wanted_mount[k] for k in ('type', 'source', 'target', 'read_only'))
                        and preflight.bind_disables_host_path_creation(mount)
                        and mount['bind'].get('propagation', 'rprivate') == 'rprivate', 'runtime_mount_mismatch')
        live = release.live_bind_specs(document)
        registry = release.registry_bind_specs(document)
        require(len(live) == 6 and registry == {REGISTRY: dict(owner=23204, group=23204, leaf='registry.json')},
                'runtime_bindings_invalid')
        # The existing live-source guard covers all services. The static wrapped
        # registry must likewise stay exclusive to these two read-only readers.
        for name, service in services.items():
            for mount in service.get('volumes', []):
                if mount.get('type') != 'bind':
                    continue
                source = mount.get('source', '')
                if source == REGISTRY or source.startswith(REGISTRY + '/') or REGISTRY.startswith(source.rstrip('/') + '/'):
                    require(name in expected['services'] and source == REGISTRY
                            and mount.get('target') == '/run/rsc-bao-registry', 'registry_binding_unbound')
        return dict(publicConfigurationValid=True, liveIdentityVerified=False,
                    composeMergeVerified=False, deploymentReady=False)
    except (KeyError, TypeError, AttributeError, ValueError, release.Refused) as exc:
        if isinstance(exc, Rejected):
            raise
        raise Rejected('resolved_contract_invalid') from None


def render(value):
    document = build(value)
    lines = ['# Public coordinates only; Docker Compose >=2.24.4 required.',
             '# Merge with the business candidate; validate resolved JSON before pilot_release.', 'services:']
    for name, service in document['services'].items():
        lines.append('  ' + name + ':')
        for key, item in service.items():
            tag = '!override ' if key in OVERRIDE_LISTS else ''
            lines.append('    ' + key + ': ' + tag + json.dumps(item, sort_keys=True))
    return '\n'.join(lines) + '\n'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--public-config', required=True, type=Path)
    parser.add_argument('--output', type=Path)
    parser.add_argument('--resolved-compose', type=Path)
    args = parser.parse_args()
    try:
        config = json.loads(args.public_config.read_text(), object_pairs_hook=preflight.strict_object)
        payload = render(config)
        if args.resolved_compose:
            # Resolved business Compose may contain secrets: consume in memory,
            # never copy, echo or include it in a receipt.
            resolved = json.loads(args.resolved_compose.read_text(), object_pairs_hook=preflight.strict_object)
            validate_resolved(resolved, config)
        if args.output:
            with args.output.open('x', encoding='utf-8') as stream:
                stream.write(payload)
        print(json.dumps(dict(publicConfigurationValid=True, resolvedConfigurationChecked=bool(args.resolved_compose),
                              liveIdentityVerified=False, deploymentReady=False)))
        return 0
    except (OSError, ValueError, TypeError):
        print('{"status":"configuration_rejected"}')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
