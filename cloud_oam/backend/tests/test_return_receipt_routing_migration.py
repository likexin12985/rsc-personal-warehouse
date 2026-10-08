"""0179 preserves request closure and the independent typed receipt proof."""
from copy import deepcopy
from hashlib import sha256
from pathlib import Path
import json
import runpy
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_exact_function_and_readiness_chain():
    revision = runpy.run_path(str(ROOT / 'alembic/versions/20261228_0179_return_receipt_routing.py'))
    from app import database_security as security
    from app.return_receipt_routing_security import DATA
    assert revision['DATA'] == DATA
    assert (revision['revision'], revision['down_revision']) == ('20261228_0179', '20261227_0178')
    previous = json.loads((ROOT / 'alembic/remaining_cancel_0171/catalog.json').read_text())
    signature = 'rsc_guard_request_open_0169()'
    assert DATA['barrier']['before'] == previous['functions'][signature]['after']
    before, after = DATA['functions'][signature]['before'], DATA['functions'][signature]['after']
    # The old open/closed/cancelled routing remains byte-identical after the
    # narrow INSERT-only typed-header branch. No ordinary roots are optional.
    tail = before.split('BEGIN\n', 1)[1]
    assert after.endswith(tail)
    branch = after.removesuffix(tail)
    assert "TG_OP='INSERT' AND TG_TABLE_NAME='receipts'" in branch
    assert 'public.stock_operation_shipments WHERE id=NEW.shipment_id' in branch
    assert 'public.shipment_lines WHERE shipment_id=NEW.shipment_id' in branch
    assert "ERRCODE='23514'" in branch and "ERRCODE='25001'" in branch
    assert 'receipt_no' not in branch and 'command_jsonb' not in branch
    for catalog in (security._closure_catalog, security._remaining_cancel_catalog):
        assert catalog.DATA['functions'][signature]['after'] == DATA['barrier']['after']
    from app.key_provider_readiness_security import DATA as key_provider
    frozen = json.loads((ROOT / 'alembic/key_provider_bindings_0180/functions.json').read_text())
    assert key_provider == frozen
    ready = key_provider['readiness']
    assert key_provider['previousRevision'] == DATA['revision']
    assert key_provider['revision'] == ready['revision'] == '20261229_0180'
    assert ready['before'] == DATA['readiness']['after']
    assert ready['beforeSha256'] == DATA['readiness']['afterSha256']
    change = key_provider['functions']['rsc_oam_runtime_binding_ready_0044()']
    for side in ('before', 'after'):
        assert change[side] == ready[side]['prosrc']
        assert sha256(change[side].encode()).hexdigest() == change[side+'Sha256'] == ready[side+'Sha256']
    current_ready = security._stock_scrap_readiness.DATA
    assert current_ready['revision'] == ready['revision']
    assert current_ready['after'] == ready['after']
    assert current_ready['afterSha256'] == ready['afterSha256']
    for signature, change in DATA['functions'].items():
        for side in ('before', 'after'):
            assert sha256(change[side].encode()).hexdigest() == change[side+'Sha256']
        assert revision['_sources']()['public.'+signature] == (change['before'], change['after'])


@pytest.mark.parametrize('field', ['prosrc', 'owner', 'acl', 'definition'])
def test_overlay_rejects_full_predecessor_drift_without_partial_update(field):
    from app import return_receipt_routing_security as routing
    signature = 'rsc_guard_request_open_0169()'
    coordinate = ('rsc_guard_request_open_0169', '')
    namespace = {name: {} for name in ('MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256',
        'FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256', 'RUNTIME_FUNCTION_BODY_SHA256')}
    namespace['FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256'][coordinate] = routing.DATA['functions'][signature]['beforeSha256']
    for name in ('_closure_catalog', '_remaining_cancel_catalog'):
        namespace[name] = SimpleNamespace(DATA={'functions': {signature: deepcopy(routing.DATA['barrier'])}})
        namespace[name].DATA['functions'][signature]['after'] = deepcopy(routing.DATA['barrier']['before'])
    namespace['_remaining_cancel_catalog'].DATA['functions'][signature]['after'][field] = 'drift'
    original = deepcopy(namespace)
    with pytest.raises(ValueError, match='exact full barrier catalog'):
        routing.register(namespace)
    assert namespace == original
