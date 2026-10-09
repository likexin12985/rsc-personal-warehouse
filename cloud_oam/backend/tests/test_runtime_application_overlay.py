"""New public configuration checks only; no Docker, identities or cloud I/O."""
import copy
import importlib.util
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location('runtime_application_overlay',
    ROOT / 'deployment/openbao-pilot/runtime/runtime_application_overlay.py')
overlay = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(overlay)


def public():
    return dict(container_id='a' * 64, image_id=overlay.IMAGE, instance_id='rsc-synthetic-overlay',
        registry_host_dir='/etc/rsc-openbao-registry', account_id='1234567890123456',
        provider_arn='acs:ram::1234567890123456:oidc-provider/synthetic',
        oss_role_arn='acs:ram::1234567890123456:role/synthetic-files',
        pnvs_role_arn='acs:ram::1234567890123456:role/synthetic-pnvs',
        oss_subject='00000000-0000-4000-8000-000000000001',
        pnvs_subject='00000000-0000-4000-8000-000000000002',
        oss_audience='synthetic-files', pnvs_audience='synthetic-pnvs',
        auth_key_version=1, contact_key_version=2)


def test_public_wiring_reuses_all_three_existing_guard_contracts_without_live_claim():
    value = overlay.build(public())
    result = overlay.validate_resolved(value, public())
    assert result == dict(publicConfigurationValid=True, liveIdentityVerified=False,
                          composeMergeVerified=False, deploymentReady=False)
    api, gate = (value['services'][name] for name in ('api', 'kms-pin-gate'))
    assert api['group_add'] == ['23110', '23212', '23213']
    assert gate['group_add'] == ['23110']
    assert api['pid'] == gate['pid'] == 'container:' + 'a' * 64
    assert len(overlay.release.live_bind_specs(value)) == 6
    assert overlay.release.registry_bind_specs(value) == {
        '/etc/rsc-openbao-registry': dict(owner=23204, group=23204, leaf='registry.json')}
    assert all(overlay.preflight.pnvs_oidc_configuration_checks(value).values())
    assert not any(k.startswith(('RSC_PNVS_', 'RSC_OSS_', 'ALIBABA_CLOUD_')) for k in gate['environment'])
    assert not any(k in api['environment'] for k in overlay.preflight.PNVS_STATIC_FIELDS)


@pytest.mark.parametrize('key,value', [
    ('container_id', 'rsc-bao-server'), ('container_id', 'a' * 63),
    ('image_id', 'openbao:latest'), ('image_id', 'sha256:' + 'b' * 64),
    ('instance_id', ''), ('instance_id', 'rsc-$(bad)'),
    ('registry_host_dir', '/etc/rsc-openbao/registry'), ('registry_host_dir', '/run'),
    ('account_id', '123'), ('provider_arn', 'acs:ram::9999999999999999:oidc-provider/synthetic'),
    ('oss_role_arn', 'acs:ram::9999999999999999:role/files'),
    ('pnvs_role_arn', 'acs:ram::1234567890123456:role/synthetic-files'),
    ('oss_subject', 'not-a-uuid'), ('pnvs_subject', '00000000-0000-4000-8000-000000000001'),
    ('oss_audience', '${SECRET}'), ('pnvs_audience', 'synthetic-files'),
    ('auth_key_version', True), ('contact_key_version', 0), ('contact_key_version', 2147483648),
])
def test_invalid_or_cross_bound_public_coordinates_fail_closed(key, value):
    config = public(); config[key] = value
    with pytest.raises(overlay.Rejected):
        overlay.build(config)


def test_unknown_fields_cannot_forward_credentials_or_arbitrary_environment():
    config = public(); config['token'] = 'synthetic-never-output'
    with pytest.raises(overlay.Rejected, match='^public_fields_invalid$') as caught:
        overlay.build(config)
    assert 'synthetic-never-output' not in str(caught.value)


@pytest.mark.parametrize('mutation', [
    'container', 'pid', 'gate-instance', 'gate-version', 'uid', 'gate-group', 'extra-api-group',
    'cap-add', 'missing-cap-drop', 'privileged', 'nnp', 'devices', 'ipc', 'userns',
    'rw', 'source', 'create-path', 'propagation', 'duplicate-target', 'mount-missing',
    'writer', 'gate-writer', 'gate-oss', 'gate-pnvs', 'role', 'profile', 'metadata',
    'credential-uri', 'static', 'other-live-reader', 'other-registry-reader',
    'registry-ancestor', 'dev-null',
])
def test_resolved_candidate_drift_is_rejected(mutation):
    value = overlay.build(public()); services = value['services']
    api, gate = (services[name] for name in ('api', 'kms-pin-gate'))
    env = api['environment']; mount = api['volumes'][1]
    if mutation == 'container': env['RSC_OPENBAO_CONTAINER_ID'] = 'b' * 64
    elif mutation == 'pid': api['pid'] = 'host'
    elif mutation == 'gate-instance': gate['environment']['OAM_OPENBAO_PROVIDER_INSTANCE_ID'] += '-wrong'
    elif mutation == 'gate-version': gate['environment']['OAM_MATERIAL_REQUEST_CONTACT_ENCRYPTION_KEY_VERSION'] = '1'
    elif mutation == 'uid': api['user'] = '0:0'
    elif mutation == 'gate-group': gate['group_add'].append('23212')
    elif mutation == 'extra-api-group': api['group_add'].append('999')
    elif mutation == 'cap-add': api['cap_add'] = ['SYS_ADMIN']
    elif mutation == 'missing-cap-drop': api.pop('cap_drop')
    elif mutation == 'privileged': api['privileged'] = True
    elif mutation == 'nnp': api['security_opt'] = []
    elif mutation == 'devices': api['devices'] = ['/dev/sda']
    elif mutation == 'ipc': api['ipc'] = 'host'
    elif mutation == 'userns': api['userns_mode'] = 'host'
    elif mutation == 'rw': mount['read_only'] = False
    elif mutation == 'source': mount['source'] = '/run/other'
    elif mutation == 'create-path': mount['bind']['create_host_path'] = True
    elif mutation == 'propagation': mount['bind']['propagation'] = 'shared'
    elif mutation == 'duplicate-target': api['volumes'].append(copy.deepcopy(mount))
    elif mutation == 'mount-missing': api['volumes'].pop(1)
    elif mutation == 'writer': env['RSC_OSS_OIDC_WRITER'] = 'other'
    elif mutation == 'gate-writer': gate['environment']['RSC_OSS_OIDC_WRITER'] = 'openbao_agent_template_v1'
    elif mutation == 'gate-oss': gate['environment']['OAM_FILE_STORAGE_OIDC_ROLE_ARN'] = env['OAM_FILE_STORAGE_OIDC_ROLE_ARN']
    elif mutation == 'gate-pnvs': gate['environment']['ALIBABA_CLOUD_ROLE_ARN'] = env['ALIBABA_CLOUD_ROLE_ARN']
    elif mutation == 'role': env['ALIBABA_CLOUD_ROLE_ARN'] = env['OAM_FILE_STORAGE_OIDC_ROLE_ARN']
    elif mutation == 'profile': env['ALIBABA_CLOUD_CLI_PROFILE_DISABLED'] = 'false'
    elif mutation == 'metadata': env['ALIBABA_CLOUD_ECS_METADATA_DISABLED'] = 'false'
    elif mutation == 'credential-uri': env['ALIBABA_CLOUD_CREDENTIALS_URI'] = 'http://localhost/creds'
    elif mutation == 'static': gate['environment']['OSS_ACCESS_KEY_ID'] = 'synthetic-never-output'
    elif mutation == 'other-live-reader': services['other'] = dict(volumes=[copy.deepcopy(mount)])
    elif mutation == 'other-registry-reader': services['other'] = dict(volumes=[copy.deepcopy(api['volumes'][0])])
    elif mutation == 'registry-ancestor': services['other'] = dict(volumes=[overlay.bind('/etc', '/elsewhere')])
    elif mutation == 'dev-null': api['volumes'].append(overlay.bind('/tmp/null', '/dev/null'))
    else: raise AssertionError(mutation)
    with pytest.raises(overlay.Rejected) as caught:
        overlay.validate_resolved(value, public())
    assert 'synthetic-never-output' not in str(caught.value)


def test_compose_canonical_false_bind_and_security_spelling_are_accepted():
    value = overlay.build(public())
    for service in value['services'].values():
        service['security_opt'] = ['no-new-privileges=true']
        service['group_add'] = list(map(int, service['group_add']))
        service.pop('cap_add')
        service.pop('privileged')  # Compose canonical output omits explicit false.
        for mount in service['volumes']:
            mount['bind'] = {}
    assert overlay.validate_resolved(value, public())['publicConfigurationValid']


@pytest.mark.parametrize('value', [0, 'false'])
def test_privileged_canonical_false_does_not_accept_coercible_non_boolean(value):
    document = overlay.build(public())
    document['services']['api']['privileged'] = value
    with pytest.raises(overlay.Rejected):
        overlay.validate_resolved(document, public())


def test_renderer_uses_exact_list_override_and_target_based_volume_merge():
    text = overlay.render(public())
    assert text.count('!override ') == 8
    assert text.count('volumes: [') == 2
    assert 'volumes: !override' not in text  # Preserve existing unrelated business mounts.
    assert '${' not in text
    assert 'OAM_SMS_ACCESS_KEY_ID' not in text  # Never clear a static key to disguise base drift.
    assert 'RSC_OSS_OIDC_WRITER' in text


def test_configuration_calls_do_not_read_live_sources(monkeypatch):
    config = public(); value = overlay.build(config)  # Load pure modules before denying file reads.
    with monkeypatch.context() as patch:
        patch.setattr(Path, 'read_text', lambda *_a, **_k: pytest.fail('unexpected file read'))
        patch.setattr(Path, 'read_bytes', lambda *_a, **_k: pytest.fail('unexpected file read'))
        assert overlay.build(config) == value
        assert overlay.validate_resolved(value, config)['deploymentReady'] is False


def test_cli_writes_only_public_overlay_and_refuses_overwrite(tmp_path, monkeypatch, capsys):
    source = tmp_path / 'public.json'; source.write_text(json.dumps(public()))
    output = tmp_path / 'overlay.yml'
    monkeypatch.setattr(sys, 'argv', ['overlay', '--public-config', str(source), '--output', str(output)])
    assert overlay.main() == 0
    result = json.loads(capsys.readouterr().out)
    assert result['deploymentReady'] is False and result['resolvedConfigurationChecked'] is False
    before = output.read_bytes()
    assert overlay.main() == 1
    assert json.loads(capsys.readouterr().out) == {'status': 'configuration_rejected'}
    assert output.read_bytes() == before


def test_cli_rejects_resolved_secret_without_echo_or_output(tmp_path, monkeypatch, capsys):
    config = tmp_path / 'public.json'; config.write_text(json.dumps(public()))
    value = overlay.build(public())
    value['services']['api']['environment']['ALIBABA_CLOUD_ACCESS_KEY_ID'] = 'synthetic-do-not-echo'
    resolved = tmp_path / 'resolved.json'; resolved.write_text(json.dumps(value))
    output = tmp_path / 'overlay.yml'
    monkeypatch.setattr(sys, 'argv', ['overlay', '--public-config', str(config), '--output', str(output),
                                     '--resolved-compose', str(resolved)])
    assert overlay.main() == 1 and not output.exists()
    assert capsys.readouterr().out == '{"status":"configuration_rejected"}\n'
