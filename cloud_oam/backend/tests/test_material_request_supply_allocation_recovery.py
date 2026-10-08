"""Actual supply and allocation services; synthetic opening is not PG16 proof."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from unittest.mock import patch

import pytest
from sqlalchemy import event, select

from app.demand_models import MaterialRequestCommand
from app.foundation_models import AuditEvent, StateTransitionEvent
from app.inventory_models import (InventorySerial, InventoryTransaction, InventoryMovement,
    SerialCurrentPosition, MaterialInventoryPolicy, StockAccount, StockAllocationSerial, StockBalance, StockLocation)
from app.formal_services import inventory_query, material_request_supply as supply
from app.formal_services.material_request_allocation import AllocationCreateInput, create_allocation
from test_material_request_approval_service import approval_db, _principal
from test_material_request_draft_service import NOW, SECRET
from test_material_request_query_service import _grant
from test_material_request_supply_command_status_service import _seed, _lookup


@pytest.fixture(params=[False, True], ids=['quantity', 'serial'])
def allocated(approval_db, monkeypatch, request):
    return _allocated_world(approval_db, monkeypatch, serial=request.param)


def _allocated_world(db, monkeypatch, *, serial, planned=True, allocation_count=2):
    if planned:
        world, reader, demand, line, trace, original = _seed(db, key='supply-allocation-recovery')
    else:
        from test_material_request_supply_service import _approved_request, _grant_supply_manage
        world, demand, line, _ = _approved_request(db, key='unplanned-allocation-capacity')
        _grant_supply_manage(db, world)
        reader = _principal(db, world.admin_users[0].id)
        trace = original = None
    _grant(db, world, action='read', roles=('admin',))
    reader = _principal(db, reader.user_id)
    actor = _principal(db, world.admin_users[1].id)
    location = StockLocation(id=uuid4(), code='RECOVERY-SOURCE', name='Synthetic source',
        location_type='headquarters', owner_org_id=world.headquarters.id, status='active')
    db.add(location); db.flush()
    account = StockAccount(id=uuid4(), owner_org_id=world.headquarters.id, location_id=location.id,
        material_id=line.material_id, condition_code='new', availability_bucket='available')
    db.add(account); db.flush()
    balance = StockBalance(stock_account_id=account.id, quantity=Decimal('4.000'), ledger_cursor=1, version=1)
    policy = MaterialInventoryPolicy(material_id=line.material_id,
        tracking_mode='serial' if serial else 'none', quantity_scale=3,
        allow_fraction=not serial, effective_from=NOW-timedelta(days=1))
    db.add_all((balance, policy)); db.flush()
    serial_ids = []
    if serial:
        tx = InventoryTransaction(id=uuid4(), transaction_no='SYNTHETIC-RECOVERY-OPEN', movement_type='opening',
            source_document_type='opening_stocktake', source_document_id=str(uuid4()),
            posting_key='synthetic-recovery-opening', idempotency_key_hash='1'*64, request_hash='2'*64,
            status='posted', effective_at=NOW, posted_at=NOW, ledger_cursor=1, actor_user_id=actor.user_id)
        db.add(tx); db.flush()
        movement = InventoryMovement(id=uuid4(), transaction_id=tx.id, line_no=1, to_account_id=account.id,
            external_boundary_code='opening', quantity=Decimal('4.000'))
        db.add(movement); db.flush()
        for index in range(2):
            serial = InventorySerial(id=uuid4(), material_id=line.material_id, serial_no=f'RECOVERY-{index}',
                qr_code=f'SYNTHETIC-RECOVERY-{index}', lifecycle_status='active')
            db.add(serial); db.flush()
            db.add(SerialCurrentPosition(serial_id=serial.id, stock_account_id=account.id, last_movement_id=movement.id))
            serial_ids.append(serial.id)
        db.flush()
    source = SimpleNamespace(account=account, location=location, material=world.materials[0],
        owner_org=world.headquarters, location_owner_org=world.headquarters, custodian=None, lot=None, balance=balance)
    monkeypatch.setattr(inventory_query, '_require_inventory_read', lambda *a, **k: None)
    monkeypatch.setattr(inventory_query, '_projection_snapshot', lambda *a, **k: SimpleNamespace(ledger_cursor=1, projected_at=NOW))
    monkeypatch.setattr(inventory_query, '_authorized_account_rows', lambda *a, **k: [source])
    monkeypatch.setattr(inventory_query, '_validate_current_projection_integrity', lambda *a, **k: None)
    monkeypatch.setattr(inventory_query, '_validated_opening_evidence', lambda *a, **k: SimpleNamespace(complete=True))
    results = []
    for index in range(allocation_count):
        results.append(create_allocation(db, actor=actor, material_request_id=demand.id,
            expected_request_version=demand.version, allocation=AllocationCreateInput(request_line_id=line.id,
                source_stock_account_id=account.id, allocated_qty=Decimal('1.000' if serial else '.500'),
                source_balance_version=1, source_ledger_cursor=1,
                serial_ids=(serial_ids[index],) if serial else ()),
            idempotency_key=f'supply-followup-allocation-{index}', idempotency_hmac_secret=SECRET,
            trace_request_id=f'supply-followup-allocation-trace-{index}'))
    db.commit()
    return db, reader, demand, trace, original, results, balance, bool(serial)


def test_supply_recovers_after_another_admin_allocates_without_rewriting_any_state(allocated):
    db, reader, demand, trace, original, results, balance, serial = allocated
    before = (demand.version, demand.updated_at, demand.allocation_status, balance.quantity)
    assert demand.allocation_status == ('allocated' if serial else 'partially_allocated')
    statements = []
    def capture(_connection, _cursor, sql, *_): statements.append(sql)
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        with patch.object(db, 'flush', side_effect=AssertionError('recovery flushed')):
            result = _lookup(db, reader, trace)
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    assert result.lookup_status == 'confirmed' and result.command == original
    assert result.command.request_version < results[-1].request_version == demand.version
    assert (demand.version, demand.updated_at, demand.allocation_status, balance.quantity) == before
    assert statements and all(sql.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in sql.upper() for sql in statements)


@pytest.mark.parametrize('tamper', ['command', 'audit', 'transition', 'unproved_version', 'other_axis'])
def test_incomplete_allocation_suffix_does_not_clear_supply_recovery(allocated, tamper):
    db, reader, demand, trace, _, results, *_ = allocated
    latest = results[-1]
    if tamper == 'command':
        row = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.request_id == demand.id,
            MaterialRequestCommand.target_version == latest.request_version))
        row.result_hash = '0' * 64
    elif tamper == 'audit':
        row = db.scalar(select(AuditEvent).where(AuditEvent.aggregate_id == str(latest.allocation_id),
            AuditEvent.action == 'material_request_allocation_created'))
        row.event_hash = '0' * 64
    elif tamper == 'transition':
        row = db.scalar(select(StateTransitionEvent).where(StateTransitionEvent.aggregate_id == str(demand.id),
            StateTransitionEvent.metadata_jsonb['allocation_id'].as_string() == str(results[0].allocation_id)))
        row.reason = 'wrong-allocation-transition'
    elif tamper == 'unproved_version':
        demand.version += 1
    else:
        demand.shipment_status = 'shipped'
    db.flush()
    with pytest.raises(supply.MaterialRequestSupplyError) as caught:
        _lookup(db, reader, trace)
    assert caught.value.http_status_code == 503


def test_missing_serial_binding_blocks_recovery(allocated):
    db, reader, _, trace, _, results, _, serial = allocated
    if not serial:
        pytest.skip('serial-only corruption')
    db.query(StockAllocationSerial).filter(StockAllocationSerial.allocation_id == results[0].allocation_id).delete()
    db.flush()
    with pytest.raises(supply.MaterialRequestSupplyError):
        _lookup(db, reader, trace)


def test_plan_update_and_cancel_after_allocations_keep_original_commands_recoverable(allocated, monkeypatch):
    # SQLite CURRENT_TIMESTAMP has second precision; match the microsecond
    # allocation clock here. Native PG16 uses its actual database clock.
    monkeypatch.setattr(supply, "_database_now", lambda db: datetime.now(timezone.utc))
    db, reader, demand, trace, original, _, balance, _ = allocated
    before = (demand.allocation_status, balance.quantity, balance.version, balance.ledger_cursor)
    updated = supply.update_supply_task(db, actor=reader, material_request_id=demand.id,
        supply_task_id=original.supply_task_id, expected_request_version=demand.version,
        expected_task_version=original.task_version,
        update=supply.SupplyTaskUpdateInput(status='awaiting_supply', reference_no=None,
            expected_date=original.expected_date, comment='分配后核对原计划'),
        idempotency_key='late-plan-update', idempotency_hmac_secret=SECRET,
        trace_request_id='late-plan-update-trace')
    db.commit()
    assert _lookup(db, reader, trace).command == original
    assert _lookup(db, reader, 'late-plan-update-trace').command == updated
    cancelled = supply.update_supply_task(db, actor=reader, material_request_id=demand.id,
        supply_task_id=original.supply_task_id, expected_request_version=demand.version,
        expected_task_version=updated.task_version,
        update=supply.SupplyTaskUpdateInput(status='cancelled', reference_no=updated.reference_no,
            expected_date=updated.expected_date, comment='已有货源，撤销剩余预计计划'),
        idempotency_key='late-plan-cancel', idempotency_hmac_secret=SECRET,
        trace_request_id='late-plan-cancel-trace')
    db.commit()
    assert _lookup(db, reader, trace).command == original
    assert _lookup(db, reader, 'late-plan-update-trace').command == updated
    assert _lookup(db, reader, 'late-plan-cancel-trace').command == cancelled
    assert (demand.allocation_status, balance.quantity, balance.version, balance.ledger_cursor) == before
