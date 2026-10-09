"""0177 source chain and tooling retention; committed proofs run on native PG16."""
from hashlib import sha256
from pathlib import Path
import json
import runpy
from unittest.mock import patch

from alembic.migration import MigrationContext
from alembic.operations import Operations
import pytest
from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
REVISION = ROOT / 'alembic/versions/20261226_0177_supply_after_allocation.py'


def test_forward_catalog_matches_runtime_and_exact_predecessor():
    revision = runpy.run_path(str(REVISION))
    from app.material_request_supply_allocation_security import DATA
    from app import database_security as security
    assert (revision['revision'], revision['down_revision']) == ('20261226_0177', '20261225_0176')
    assert revision['DATA'] == DATA
    previous = json.loads((ROOT / 'alembic/shipment_projection_0168/functions.json').read_text())
    signature = 'rsc_validate_material_request_supply_causality_0059(uuid, bigint)'
    assert DATA['functions'][signature]['before'] == previous['functions'][signature]['after']
    previous_ready = json.loads((ROOT / 'app/material_request_return_compensation_readiness.json').read_text())
    assert DATA['readiness']['before'] == previous_ready['after']
    from app.material_request_supply_capacity_security import DATA as next_catalog
    from app.return_receipt_routing_security import DATA as routing
    from app.key_provider_readiness_security import DATA as key_provider
    predecessor = DATA['readiness']
    for catalog, directory, expected_revision in (
        (next_catalog, 'supply_capacity_0178', '20261227_0178'),
        (routing, 'return_receipt_routing_0179', '20261228_0179'),
        (key_provider, 'key_provider_bindings_0180', '20261229_0180'),
    ):
        frozen = json.loads((ROOT / 'alembic' / directory / 'functions.json').read_text())
        assert catalog == frozen
        ready = catalog['readiness']
        assert catalog['revision'] == ready['revision'] == expected_revision
        assert ready['before'] == predecessor['after']
        assert ready['beforeSha256'] == predecessor['afterSha256']
        change = catalog['functions']['rsc_oam_runtime_binding_ready_0044()']
        for side in ('before', 'after'):
            assert change[side] == ready[side]['prosrc']
            assert sha256(change[side].encode()).hexdigest() == change[side+'Sha256'] == ready[side+'Sha256']
        predecessor = ready
    assert key_provider['previousRevision'] == routing['revision']
    from app.material_request_contact_envelope_security import DATA as contact
    assert contact['previousRevision'] == predecessor['revision']
    assert contact['readiness']['before'] == predecessor['after']
    assert contact['readiness']['beforeSha256'] == predecessor['afterSha256']
    predecessor = contact['readiness']
    current_ready = security._stock_scrap_readiness.DATA
    assert current_ready['revision'] == predecessor['revision']
    assert current_ready['after'] == predecessor['after']
    assert current_ready['afterSha256'] == predecessor['afterSha256']
    assert set(DATA['functions']) == {signature, 'rsc_oam_runtime_binding_ready_0044()',
                                    'rsc_guard_material_request_supply_task_0059()'}
    for signature, row in DATA['functions'].items():
        for side in ('before', 'after'):
            assert sha256(row[side].encode()).hexdigest() == row[side+'Sha256']
        assert revision['_sources']()['public.'+signature] == (row['before'], row['after'])


@pytest.mark.parametrize('operation,allocation_version,retained', [
    ('create_supply_task', 2, False), ('update_supply_task', 4, False),
    ('update_supply_task', 2, True), ('cancel_supply_task', 2, True),
])
def test_tooling_downgrade_preserves_interleaved_plan_history(operation, allocation_version, retained):
    revision = runpy.run_path(str(REVISION))
    with create_engine('sqlite://').begin() as db:
        db.exec_driver_sql('CREATE TABLE material_request_commands(request_id TEXT,operation TEXT,target_version INTEGER)')
        db.exec_driver_sql('CREATE TABLE stock_allocations(request_id TEXT,request_version INTEGER)')
        db.execute(text("INSERT INTO material_request_commands VALUES ('r',:operation,3)"), {'operation':operation})
        db.execute(text("INSERT INTO stock_allocations VALUES ('r',:version)"), {'version':allocation_version})
        revision['downgrade'].__globals__['op'] = Operations(MigrationContext.configure(db))
        with patch.object(revision['context'], 'is_offline_mode', return_value=False):
            if retained:
                with pytest.raises(ValueError, match='late supply command facts exist'):
                    revision['downgrade']()
            else:
                revision['downgrade']()
        assert db.scalar(text('SELECT count(*) FROM material_request_commands')) == 1
        assert db.scalar(text('SELECT count(*) FROM stock_allocations')) == 1
