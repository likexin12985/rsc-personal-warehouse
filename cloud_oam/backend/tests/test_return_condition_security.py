"""Full staged import plus exact forward registration and atomic refusal."""
from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
import pytest
from app import database_security as runtime
from app import return_condition_security as condition
from app import return_condition_readiness as ready
from app import oam_sync_scope_security as oam
from stock_scrap_registration_fixture import predecessor


def test_runtime_is_exact_readonly_projection_and_full_import_succeeds():
    raw = (Path(__file__).resolve().parents[1] / 'alembic/return_condition_0167/catalog.json').read_bytes()
    expected = json.loads(raw)
    del expected['statements']
    expected['migrationCatalogSha256'] = sha256(raw).hexdigest()
    from forward_catalog_expectations import current_catalog
    assert condition.DATA == current_catalog('20261216_0167', expected)
    assert len(condition.TABLES) == 10
    assert set(condition.TABLES) <= runtime.RUNTIME_READ_TABLES
    assert len(condition.DATA['functions']) == 60
    for signature, change in condition.DATA['functions'].items():
        row = change['after']
        coordinate = row['proname'], signature.split('(', 1)[1][:-1]
        matches = [getattr(runtime, key)[coordinate] for key in
                   ('MATERIAL_REQUEST_APPROVAL_FUNCTION_BODY_SHA256',
                    'FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256', 'RUNTIME_FUNCTION_BODY_SHA256')
                   if coordinate in getattr(runtime, key)]
        assert matches == [sha256(row['prosrc'].encode()).hexdigest()]
    from migration_source_expectations import current_source_body
    assert runtime._stock_scrap_readiness.DATA['after']['prosrc'] == current_source_body(
        ready.DATA['revision'], 'public.'+ready.DATA['after']['signature'], ready.DATA['after']['prosrc'])
    assert "20261216_0167" in ready.DATA['after']['prosrc']
    assert oam.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0166 != oam.OAM_SYNC_FUNCTION_MANIFEST


@pytest.mark.parametrize('tamper', ['digest', 'scrap_table', 'old_table', 'repeat'])
def test_forward_registration_refuses_drift_without_partial_publication(tamper):
    scope = predecessor('condition')
    if tamper == 'digest':
        coordinate = ('rsc_check_loss_receipt_0155', 'uuid, boolean')
        found = [key for key, value in scope.items() if isinstance(value, dict) and coordinate in value
                 and key.endswith('BODY_SHA256')]
        assert len(found) == 1
        scope[found[0]][coordinate] = '0' * 64
    elif tamper == 'scrap_table':
        table = next(name for name in condition.DATA['tables'] if name in scope['_stock_scrap_catalog'].DATA['tables'])
        scope['_stock_scrap_catalog'].DATA['tables'][table]['after']['columns'][0]['not_null'] ^= True
    elif tamper == 'old_table':
        table = next(row for row in scope['_loss_correction_catalog'].DATA['candidateTables']
                     if row['name'] in condition.DATA['tables'])
        table['columns'][0]['not_null'] ^= True
    else:
        condition.register(scope)
    before = deepcopy(scope)
    with pytest.raises(ValueError):
        condition.register(scope)
    assert scope == before
