"""Forward allowlists preserve predecessor checks and publish atomically."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import database_security as old
from app import stock_scrap_security as scrap
from stock_scrap_registration_fixture import predecessor


def namespace():
    return predecessor('scrap')


def test_runtime_catalog_is_exact_readonly_projection_of_frozen_migration():
    path = Path(__file__).resolve().parents[1] / 'alembic/stock_scrap_0165/catalog.json'
    raw = path.read_bytes()
    expected = json.loads(raw)
    del expected['statements']
    expected['migrationCatalogSha256'] = sha256(raw).hexdigest()
    assert json.loads(scrap.RAW) == expected
    # Runtime keeps the immutable 0165 source but applies the exact later fence.
    from app.scrap_authentication_fence_security import DATA as fence
    patch = fence['patches'][0]
    key = patch['before']['signature']
    assert expected['functions'][key]['after'] == patch['before']
    expected['functions'][key]['after'] = deepcopy(patch['after'])
    assert scrap.DATA == expected


def test_forward_allowlists_and_independent_old_verifiers_remain_complete():
    scope = namespace()
    previous_public = set(scope['RUNTIME_EXECUTE_FUNCTIONS'])
    scrap.register(scope)
    assert set(scrap.TABLES) <= scope['RUNTIME_READ_TABLES']
    assert len(set(scrap.TABLES) & scope['RUNTIME_INSERT_TABLES']) == 8
    assert not {'stock_scrap_request_seals', 'stock_scrap_request_key_bindings'} & scope['RUNTIME_INSERT_TABLES']
    new_public = set(scope['RUNTIME_EXECUTE_FUNCTIONS']) - previous_public
    assert new_public == {('rsc_register_scrap_request_binding_0165', 'text, uuid, text'),
        ('rsc_register_scrap_seal_0165', 'text, bigint, uuid, text, jsonb, text')}
    assert scope['FORMAL_FILE_INTERNAL_FUNCTION_SHAPES'][('rsc_scrap_recovery_source_0165', 'uuid')] == ('f', 'record', False)
    assert scope['FORMAL_FILE_INTERNAL_SET_FUNCTIONS'] == {('rsc_scrap_recovery_source_0165', 'uuid'): ('i', 't', 't', 't', 't')}
    assert scope['FORMAL_FILE_INTERNAL_FUNCTIONS'][('rsc_scrap_seal_payload_0165', 'uuid')][2] == 'sql'
    for name in scrap.CATALOGS:
        assert len(scope[name].DATA['candidateTables']) == len(getattr(old, name).DATA['candidateTables'])
        assert len(scope[name].DATA['triggers']) >= len(getattr(old, name).DATA['triggers'])
        for family in ('newFunctions', 'replacedFunctions'):
            for row in scope[name].DATA[family]:
                change = scrap.DATA['functions'].get(scrap.signature_key(row['signature']))
                if change is not None:
                    assert row['definition'] == change['after']['definition']
                    assert row['prosrc'] == change['after']['prosrc']
    # Forward registration is now the ordinary application import behavior.
    assert set(scrap.TABLES) <= old.RUNTIME_READ_TABLES
    assert scope['RUNTIME_INSERT_TABLES'] == old.RUNTIME_INSERT_TABLES


@pytest.mark.parametrize('tamper', ['digest', 'definition', 'column', 'trigger', 'repeat'])
def test_unknown_predecessor_does_not_partially_publish_permissions(tamper):
    scope = namespace()
    if tamper == 'digest':
        scope['FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256'][('rsc_check_loss_history_graph_0159', 'uuid')] = '0' * 64
    elif tamper == 'definition':
        row = next(row for row in scope['_loss_correction_catalog'].DATA['newFunctions']
            if row['signature'] in scrap.DATA['functions'])
        row['definition'] += '\n'
    elif tamper == 'column':
        table = next(row for row in scope['_loss_correction_catalog'].DATA['candidateTables']
            if row['name'] in scrap.DATA['tables'])
        table['columns'][0]['not_null'] = not table['columns'][0]['not_null']
    elif tamper == 'trigger':
        row = next(row for row in scope['_loss_correction_catalog'].DATA['triggers']
            if row['table_name'] in scrap.DATA['tables'])
        row['tgenabled'] = 'D'
    else:
        scrap.register(scope)
    before = {key: deepcopy(value) for key, value in scope.items() if isinstance(value, (dict, frozenset))}
    catalogs = {key: deepcopy(scope[key].DATA) for key in scrap.CATALOGS}
    with pytest.raises(ValueError):
        scrap.register(scope)
    assert {key: value for key, value in scope.items() if key in before} == before
    assert {key: scope[key].DATA for key in scrap.CATALOGS} == catalogs
