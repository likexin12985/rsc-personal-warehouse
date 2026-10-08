"""Readiness version, least privilege and atomic independent-catalog overlays."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import runpy

import pytest
from app import database_security as security
from app import stock_scrap_readiness as live_readiness
from app import scrap_authentication_fence_security as current_fence
from app import return_condition_readiness as current_readiness
import importlib.util
from stock_scrap_registration_fixture import predecessor
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0165


def historical_scope():
    scope = predecessor('readiness')
    coordinate = ('rsc_oam_runtime_binding_ready_0044', '')
    assert scope['OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256'][coordinate] == live_readiness.DATA['afterSha256']
    # The live import selects 0167 after both forward readiness overlays.
    # Reproduce the historical 0165 selection from its immutable manifest.
    scope['OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256'][coordinate] = OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0165[
        'rsc_oam_runtime_binding_ready_0044()'][-1]
    return scope


@pytest.fixture
def ready():
    # Later overlays legitimately advance the live module DATA. Load its
    # immutable 0165 artifact in an isolated module for this historical test.
    spec = importlib.util.spec_from_file_location('app._test_frozen_readiness', live_readiness.__file__)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.RAW == live_readiness.RAW
    return module


def test_readiness_is_exact_frozen_revision_only_patch_and_default_registration(ready):
    folder = Path(__file__).parents[1] / 'alembic/stock_scrap_0165'
    assert ready.RAW == (folder / 'readiness.json').read_bytes()
    data = ready.DATA
    before, after = data['before'], data['after']
    assert set(before) == set(after)
    for key in before:
        if key in ('definition', 'prosrc'):
            assert before[key].count("'20261213_0164'") == 1
            assert after[key] == before[key].replace("'20261213_0164'", "'20261214_0165'")
        else:
            assert before[key] == after[key]
    assert data['afterSha256'] == sha256(after['prosrc'].encode()).hexdigest()
    scope = historical_scope()
    ready.register(scope)
    assert scope['OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256'][(after['proname'], '')] == data['afterSha256']
    runtime = next(p for p in current_fence.DATA['patches'] if p['after']['signature'] == after['signature'])
    assert current_readiness.DATA['previousRevision'] == '20261215_0166'
    assert current_readiness.DATA['revision'] == '20261216_0167'
    assert current_readiness.DATA['before'] == runtime['after']
    assert current_readiness.DATA['beforeSha256'] == runtime['afterSha256']
    from migration_source_expectations import current_source_body
    expected = deepcopy(current_readiness.DATA['after'])
    old_body = expected['prosrc']
    expected['prosrc'] = current_source_body(current_readiness.DATA['revision'], 'public.'+expected['signature'], old_body)
    assert expected['definition'].count(old_body) == 1
    expected['definition'] = expected['definition'].replace(old_body, expected['prosrc'])
    assert live_readiness.DATA['after'] == expected
    assert live_readiness.DATA['afterSha256'] == sha256(expected['prosrc'].encode()).hexdigest()
    stop = scope['_loss_return_stop_catalog'].DATA
    assert next(r for r in stop['replacedFunctions'] if r['signature'] == after['signature'])['prosrc'] == after['prosrc']
    patches = scope['_authentication_fence_catalog'].DATA['patches']
    assert next(p for p in patches if p['after']['signature'] == after['signature'])['after'] == after
    migration = runpy.run_path(str(folder.parent / 'versions/20261214_0165_stock_scrap_and_recovery.py'))
    assert migration['down_revision'] == data['previousRevision']
    assert migration['revision'] == data['revision']
    assert len(migration['_sources']()) == 16
    for kind in ('approval', 'execution'):
        prior, current = migration['_sources']()['public.rsc_check_loss_'+kind+'_seal_0161(uuid)']
        old_root = "OR root.disposition NOT IN ('restore_available','convert_used','convert_damaged')"
        assert prior.count(old_root) == 1
        assert current == prior.replace(old_root, old_root[:-1]+",'scrap')")
    assert migration['_sources']()['public.' + after['signature']] == (before['prosrc'], after['prosrc'])


@pytest.mark.parametrize('tamper', ['manifest', 'stop_definition', 'authentication_acl', 'repeat'])
def test_readiness_rejects_unknown_predecessor_without_partial_publication(tamper, ready):
    scope = historical_scope()
    signature = ready.DATA['before']['signature']
    if tamper == 'manifest':
        scope['OAM_SYNC_RUNTIME_FUNCTION_BODY_SHA256'][(ready.DATA['before']['proname'], '')] = '0' * 64
    elif tamper == 'stop_definition':
        next(row for row in scope['_loss_return_stop_catalog'].DATA['replacedFunctions']
             if row['signature'] == signature)['definition'] += '\n'
    elif tamper == 'authentication_acl':
        next(p for p in scope['_authentication_fence_catalog'].DATA['patches']
             if p['after']['signature'] == signature)['after']['acl'].append(
                 dict(grantee='PUBLIC', privilege='EXECUTE', grantable=False))
    else:
        ready.register(scope)
    names = ('_loss_correction_catalog', '_loss_return_stop_catalog', '_authentication_fence_catalog')
    before = {name: deepcopy(scope[name].DATA) for name in names}
    expected = {'manifest': 'forward OAM readiness manifest',
        'stop_definition': 'predecessor full readiness definition',
        'authentication_acl': 'exact authentication readiness predecessor',
        'repeat': 'predecessor full readiness definition'}
    with pytest.raises(ValueError, match=expected[tamper]):
        ready.register(scope)
    assert {name: scope[name].DATA for name in names} == before
