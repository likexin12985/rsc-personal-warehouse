"""0178 source chain and tooling retention; committed proofs run on native PG16."""
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
REVISION = ROOT / 'alembic/versions/20261227_0178_supply_remaining_capacity.py'


def test_forward_catalog_matches_runtime_and_exact_predecessor():
    revision = runpy.run_path(str(REVISION))
    from app.material_request_supply_capacity_security import DATA
    from app import database_security as security
    assert (revision['revision'], revision['down_revision']) == ('20261227_0178', '20261226_0177')
    assert revision['DATA'] == DATA
    previous = json.loads((ROOT / 'alembic/supply_allocation_0177/functions.json').read_text())
    signature = 'rsc_validate_material_request_supply_causality_0059(uuid, bigint)'
    assert DATA['functions'][signature]['before'] == previous['functions'][signature]['after']
    previous_ready = json.loads((ROOT / 'alembic/supply_allocation_0177/functions.json').read_text())
    assert DATA['readiness']['before'] == previous_ready['readiness']['after']
    from app.return_receipt_routing_security import DATA as routing
    assert routing['readiness']['before'] == DATA['readiness']['after']
    assert security._stock_scrap_readiness.DATA['after'] == routing['readiness']['after']
    assert set(DATA['functions']) == {signature, 'rsc_oam_runtime_binding_ready_0044()',
                                    'rsc_guard_material_request_supply_task_0059()'}
    for signature, row in DATA['functions'].items():
        for side in ('before', 'after'):
            assert sha256(row[side].encode()).hexdigest() == row[side+'Sha256']
        assert revision['_sources']()['public.'+signature] == (row['before'], row['after'])


@pytest.mark.parametrize('operation,allocation_version,retained', [
    ('create_supply_task', 2, True), ('update_supply_task', 4, False),
    ('update_supply_task', 2, False), ('cancel_supply_task', 2, False),
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
                with pytest.raises(ValueError, match='late supply creation facts exist'):
                    revision['downgrade']()
            else:
                revision['downgrade']()
        assert db.scalar(text('SELECT count(*) FROM material_request_commands')) == 1
        assert db.scalar(text('SELECT count(*) FROM stock_allocations')) == 1
