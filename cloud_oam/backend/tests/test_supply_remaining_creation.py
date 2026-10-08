"""Create additional plans only against proved unallocated/unplanned demand."""
from datetime import datetime, timezone
from decimal import Decimal
import pytest
from sqlalchemy import select

from app.demand_models import MaterialRequestLine
from app.formal_services import material_request_supply as supply
from test_material_request_approval_service import approval_db
from test_material_request_draft_service import SECRET
from test_material_request_supply_allocation_recovery import _allocated_world
from test_material_request_supply_command_status_service import _lookup
from app.formal_services.material_request_supply_capacity import planning_capacity


@pytest.mark.parametrize('serial', [False, True])
@pytest.mark.parametrize('planned', [False, True])
def test_remaining_creation_cancel_and_original_trace_recovery(approval_db, monkeypatch, serial, planned):
    db, actor, request, old_trace, old, _, balance, _ = _allocated_world(
        approval_db, monkeypatch, serial=serial, planned=planned, allocation_count=1)
    monkeypatch.setattr(supply, '_database_now', lambda db: datetime.now(timezone.utc))
    before_stock = (balance.quantity, balance.version, balance.ledger_cursor)
    line = db.scalar(select(MaterialRequestLine).where(MaterialRequestLine.request_id == request.id))
    if planned:
        supply.update_supply_task(db, actor=actor, material_request_id=request.id,
            supply_task_id=old.supply_task_id, expected_request_version=request.version,
            expected_task_version=old.task_version,
            update=supply.SupplyTaskUpdateInput(status='cancelled', reference_no=old.reference_no,
                expected_date=old.expected_date, comment='核实实际分配后重新安排未分配需求'),
            idempotency_key='remaining-old-cancel', idempotency_hmac_secret=SECRET,
            trace_request_id='remaining-old-cancel-trace')
        db.commit()
    def create(amount, key):
        return supply.create_supply_task(db, actor=actor, material_request_id=request.id,
            expected_request_version=request.version,
            plan=supply.SupplyTaskCreateInput(request_line_id=line.id,
                supply_type='headquarters_replenishment', reference_no=None, expected_date=None, expected_qty=Decimal(amount)),
            idempotency_key=key, idempotency_hmac_secret=SECRET, trace_request_id=key+'-trace')
    capacity = planning_capacity(db, actor=actor, request_id=request.id).lines[0].quantities.new_plan_qty
    assert capacity == Decimal('1.000' if serial else '1.500')
    original = create(str(capacity), 'remaining-create'); db.commit()
    assert original.state_axes['allocation_status'] == 'partially_allocated'
    assert planning_capacity(db, actor=actor, request_id=request.id).lines[0].quantities.new_plan_qty == 0
    with pytest.raises(supply.MaterialRequestSupplyError) as caught:
        create('.001', 'remaining-overflow')
    assert caught.value.http_status_code == 409
    db.rollback()
    assert _lookup(db, actor, 'remaining-create-trace').command == original
    supply.update_supply_task(db, actor=actor, material_request_id=request.id,
        supply_task_id=original.supply_task_id, expected_request_version=request.version,
        expected_task_version=original.task_version,
        update=supply.SupplyTaskUpdateInput(status='cancelled', reference_no=None,
            expected_date=None, comment='取消预计供应保留实际分配'),
        idempotency_key='remaining-cancel', idempotency_hmac_secret=SECRET,
        trace_request_id='remaining-cancel-trace'); db.commit()
    assert planning_capacity(db, actor=actor, request_id=request.id).lines[0].quantities.new_plan_qty == capacity
    replacement = create(str(capacity), 'remaining-replacement'); db.commit()
    assert _lookup(db, actor, 'remaining-create-trace').command == original
    assert _lookup(db, actor, 'remaining-replacement-trace').command == replacement
    if old_trace:
        assert _lookup(db, actor, old_trace).command == old
    assert (balance.quantity, balance.version, balance.ledger_cursor) == before_stock
