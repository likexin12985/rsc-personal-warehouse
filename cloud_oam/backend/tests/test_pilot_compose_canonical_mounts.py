"""Verified Compose false-elision forms keep strict no-auto-create admission."""
import copy
from pathlib import Path

import pytest

from scripts import pilot_preflight as preflight, pilot_release as release
from test_pilot_live_mounts import document, coordinator


@pytest.mark.parametrize('bind,present,accepted', [
    ({'create_host_path': False}, True, True),
    ({}, True, True),
    ({'create_host_path': True}, True, False),
    (None, False, False),
    (None, True, False),
    ([], True, False),
    ('', True, False),
    ({'create_host_path': 'false'}, True, False),
    ({'create_host_path': None}, True, False),
    ({'create_host_path': 0}, True, False),
])
def test_preflight_and_release_agree_on_canonical_false_without_accepting_omission(bind, present, accepted):
    value = document(); service = value['services']['api']; mount = service['volumes'][-1]
    if present: mount['bind'] = bind
    else: mount.pop('bind')
    target = service['environment']['OAM_FILE_STORAGE_OIDC_TOKEN_FILE']
    if accepted:
        assert preflight._readonly_source(service, target, directory=True) == Path('/run/rsc-test/oidc/oidc.jwt')
        assert release.live_bind_specs(value)['api', '/run/rsc-test/oidc']['kind'] == 'oss_oidc'
    else:
        with pytest.raises(ValueError): preflight._readonly_source(service, target, directory=True)
        with pytest.raises((ValueError, release.Refused)): release.live_bind_specs(value)


def test_canonical_bind_objects_survive_decoding_and_frozen_document_unchanged(tmp_path):
    value = document()
    for service in value['services'].values():
        service.pop('privileged')  # Real config omits explicit privileged:false.
        for mount in service['volumes']: mount['bind'] = {}
    original = copy.deepcopy(value)
    instance = coordinator(tmp_path, value)
    instance.static_mounts = {'/run/rsc-test/registry': {'snapshot':str(tmp_path/'static-wrapped'), 'sha256':'synthetic'}}
    runtime = release.decoded_config(value)
    assert runtime == original and len(release.live_bind_specs(runtime)) == 5
    frozen = instance.frozen_document()
    assert value == original
    for name, service in frozen['services'].items():
        for mount in service['volumes']:
            assert mount['bind'] == {}
            if mount['target'] == '/run/rsc-test/registry':
                assert mount['source'] == str(tmp_path/'static-wrapped')
            else:
                assert mount['source'] == mount['target']


@pytest.mark.parametrize('change', ['rw', 'propagation', 'outside-run'])
def test_false_elision_does_not_relax_other_live_projection_guards(change):
    value = document(); mount = value['services']['api']['volumes'][-1]; mount['bind'] = {}
    if change == 'rw': mount['read_only'] = False
    elif change == 'propagation': mount['bind']['propagation'] = 'rshared'
    else: mount['source'] = '/tmp/oidc'
    with pytest.raises((ValueError, release.Refused)): release.live_bind_specs(value)
