"""Exact forward catalog binding and atomic runtime rejection for 0166."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import runpy
from types import SimpleNamespace

import pytest

from app import database_security, scrap_authentication_fence_security as runtime
from app.oam_sync_scope_security import OAM_SYNC_FUNCTION_MANIFEST

BACKEND = Path(__file__).resolve().parents[1]


def original_catalogs():
    return (SimpleNamespace(DATA=json.loads((BACKEND/'app/stock_scrap_security.json').read_text())),
            SimpleNamespace(DATA=json.loads((BACKEND/'app/stock_scrap_readiness.json').read_text())))


def test_exact_predecessors_and_unchanged_permissions():
    migration = runpy.run_path(str(BACKEND/'alembic/versions/20261215_0166_scrap_authentication_fence.py'))
    assert migration['revision'] == '20261215_0166'
    assert migration['down_revision'] == '20261214_0165'
    data = migration['_catalog']()
    assert data == runtime.DATA
    stock, ready = original_catalogs()
    fence, readiness = data['patches']
    assert fence['before'] == stock.DATA['functions']['rsc_fence_scrap_seals_0165()']['after']
    assert readiness['before'] == ready.DATA['after']
    expected_triggers = [dict(table_name=name, **row)
        for name, change in stock.DATA['tables'].items() for row in change['after']['triggers']
        if row['function_signature'] == 'rsc_fence_scrap_seals_0165()']
    assert data['triggers'] == expected_triggers and len(expected_triggers) == 34
    for patch in data['patches']:
        for side in ('before', 'after'):
            assert sha256(patch[side]['prosrc'].encode()).hexdigest() == patch[side+'Sha256']
        assert {k:v for k,v in patch['before'].items() if k not in ('prosrc','definition')} == {
            k:v for k,v in patch['after'].items() if k not in ('prosrc','definition')}
        assert migration['_sources']()['public.'+patch['before']['signature']] == (
            patch['before']['prosrc'], patch['after']['prosrc'])
    # The stock authority, alias scanning and retained evidence proof remain
    # byte-identical after the first inventory lock; only domain admission moves.
    suffix = "    PERFORM 1 FROM public.inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE;"
    assert fence['before']['prosrc'].split(suffix,1)[1] == fence['after']['prosrc'].split(suffix,1)[1]
    assert database_security.FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[('rsc_fence_scrap_seals_0165','')] == fence['afterSha256']
    assert OAM_SYNC_FUNCTION_MANIFEST['rsc_oam_runtime_binding_ready_0044()'][-1] == readiness['afterSha256']


@pytest.mark.parametrize('damage', ['fence_body','fence_acl','readiness_body','readiness_digest'])
def test_runtime_overlay_refuses_drift_without_partial_publication(damage):
    stock, ready = original_catalogs()
    fence = stock.DATA['functions']['rsc_fence_scrap_seals_0165()']['after']
    if damage == 'fence_body':
        fence['prosrc'] += '\n'
    elif damage == 'fence_acl':
        fence['acl'] = []
    elif damage == 'readiness_body':
        ready.DATA['after']['prosrc'] += '\n'
    else:
        ready.DATA['afterSha256'] = '0'*64
    before = deepcopy((stock.DATA,ready.DATA))
    with pytest.raises(ValueError, match='exact .* predecessor required'):
        runtime.overlay(stock,ready)
    assert (stock.DATA,ready.DATA) == before
