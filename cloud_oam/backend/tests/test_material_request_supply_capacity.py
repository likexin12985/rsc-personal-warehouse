"""Remaining plans use actual approval/allocation history, not stock balances."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from unittest.mock import patch

import pytest
from sqlalchemy import event, select
from app.demand_models import MaterialRequestLine, MaterialRequestCommand
from app.foundation_models import Permission, Role, RolePermission
from app.formal_services import material_request_supply as supply
from app.formal_services.material_request_supply_capacity import planning_capacity, quantity_capacity
from test_material_request_supply_allocation_recovery import allocated, _allocated_world
from test_material_request_approval_service import approval_db, _principal
from test_material_request_supply_service import _approved_request, _grant_supply_manage
from test_material_request_draft_service import SECRET
from test_material_request_reservation_release import release_world, _create as create_release, _input as release_input


def _read(db, actor, request):
    request_id, version = request.id, request.version
    statements = []
    def capture(_connection, _cursor, sql, *_): statements.append(sql)
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        with patch.object(db, 'flush', side_effect=AssertionError('capacity read flushed')):
            result = planning_capacity(db, actor=actor, request_id=request_id)
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    assert statements and all(sql.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in sql.upper() for sql in statements)
    assert result.request_version == version and len(result.lines) == 1
    return result.lines[0].quantities


def test_old_plan_overlap_is_visible_and_cancellation_restores_only_unallocated_capacity(allocated, monkeypatch):
    db, reader, demand, _, original, _, balance, serial = allocated
    before = (balance.quantity, balance.version, balance.ledger_cursor, demand.allocation_status)
    planned = _read(db, reader, demand)
    assert (planned.approved_qty, planned.active_planned_qty) == (Decimal(2), Decimal("1.250"))
    assert planned.allocated_qty == Decimal(2 if serial else 1)
    assert planned.new_plan_qty == 0
    assert planned.existing_overlap_qty == Decimal("1.250" if serial else ".250")
    monkeypatch.setattr(supply, '_database_now', lambda db: datetime.now(timezone.utc))
    supply.update_supply_task(db, actor=reader, material_request_id=demand.id,
        supply_task_id=original.supply_task_id, expected_request_version=demand.version,
        expected_task_version=original.task_version,
        update=supply.SupplyTaskUpdateInput(status='cancelled', reference_no=original.reference_no,
            expected_date=original.expected_date, comment='已分配部分不得重复预计补货'),
        idempotency_key='capacity-cancel-plan', idempotency_hmac_secret=SECRET,
        trace_request_id='capacity-cancel-plan-trace')
    db.commit()
    after = _read(db, reader, demand)
    assert after.active_planned_qty == after.existing_overlap_qty == 0
    assert after.new_plan_qty == after.unallocated_qty == Decimal(0 if serial else 1)
    assert (balance.quantity, balance.version, balance.ledger_cursor, demand.allocation_status) == before


@pytest.mark.parametrize('serial', [False, True])
def test_first_plan_capacity_after_real_allocations_without_any_prior_plan(approval_db, monkeypatch, serial):
    db, reader, demand, _, _, _, balance, _ = _allocated_world(approval_db, monkeypatch, serial=serial, planned=False)
    result = _read(db, reader, demand)
    assert result.active_planned_qty == 0
    assert result.allocated_qty == Decimal(2 if serial else 1)
    assert result.new_plan_qty == Decimal(0 if serial else 1)
    assert balance.quantity == 4  # Allocation is not reservation or inbound.


def test_approved_request_without_any_plan_or_allocation_has_full_capacity(approval_db):
    db = approval_db
    world, demand, _, _ = _approved_request(db, key='first-plan-capacity')
    _grant_supply_manage(db, world)
    actor = _principal(db, world.admin_users[0].id)
    db.commit()
    result = _read(db, actor, demand)
    assert result.approved_qty == result.new_plan_qty == Decimal(2)
    assert result.allocated_qty == result.active_planned_qty == 0


@pytest.mark.parametrize('tamper', ['final_approval', 'allocation_hash', 'version', 'reserved', 'released', 'shipped'])
def test_incomplete_or_later_fulfillment_does_not_masquerade_as_zero_capacity(allocated, tamper):
    db, reader, demand, _, _, results, *_ = allocated
    if tamper == 'final_approval':
        line = db.scalar(select(MaterialRequestLine).where(MaterialRequestLine.request_id == demand.id))
        line.final_approved_qty -= Decimal(".500")
    elif tamper == 'allocation_hash':
        row = db.scalar(select(MaterialRequestCommand).where(MaterialRequestCommand.request_id == demand.id,
            MaterialRequestCommand.target_version == results[-1].request_version))
        row.result_hash = '0' * 64
    elif tamper == 'version':
        demand.version += 1
    elif tamper == 'shipped':
        demand.shipment_status = 'shipped'
    else:
        demand.reservation_status = tamper
    db.flush()
    from app.formal_services.material_request_query import MaterialRequestReadError
    with pytest.raises((supply.MaterialRequestSupplyError, MaterialRequestReadError)):
        planning_capacity(db, actor=reader, request_id=demand.id)


@pytest.mark.parametrize('release_world', [False, True], indirect=True)
def test_verified_release_keeps_unallocated_supply_capacity(release_world, monkeypatch):
    db, actor, demand, original, serials, _ = release_world
    create_release(release_world, release_input(db, original, '1.000' if serials else '0.125', serials[:1]))
    permission = db.scalar(select(Permission).where(
        Permission.resource == 'supply_task', Permission.action == 'manage', Permission.field_code == ''))
    if permission is None:
        permission = Permission(resource='supply_task', action='manage', field_code='', description='capacity test')
        db.add(permission); db.flush()
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    if db.scalar(select(RolePermission.id).where(
            RolePermission.role_id == role.id, RolePermission.permission_id == permission.id)) is None:
        db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow')); db.flush()
    db.commit()
    actor = _principal(db, actor.user_id)
    result = planning_capacity(db, actor=actor, request_id=demand.id).lines[0].quantities
    assert result.approved_qty == Decimal('2.000')
    assert result.cancelled_qty == Decimal('0.000')
    assert result.allocated_qty == (Decimal('2.000') if serials else Decimal('0.500'))
    assert result.new_plan_qty == (Decimal('0.000') if serials else Decimal('1.500'))
    if result.new_plan_qty:
        monkeypatch.setattr(supply, '_database_now', lambda db: datetime.now(timezone.utc) + timedelta(seconds=2))
        line = db.scalar(select(MaterialRequestLine).where(MaterialRequestLine.request_id == demand.id))
        created = supply.create_supply_task(
            db, actor=actor, material_request_id=demand.id,
            expected_request_version=demand.version,
            plan=supply.SupplyTaskCreateInput(
                request_line_id=line.id, supply_type='headquarters_replenishment',
                expected_qty=result.new_plan_qty, reference_no=None, expected_date=None,
            ), idempotency_key='post-fulfillment-capacity-plan',
            idempotency_hmac_secret=SECRET, trace_request_id='post-fulfillment-capacity-plan-trace',
        )
        db.commit()
        assert created.request_version == demand.version
        assert planning_capacity(db, actor=actor, request_id=demand.id).lines[0].quantities.new_plan_qty == Decimal('0.000')


def test_stale_or_disabled_actor_cannot_read_planning_capacity(allocated):
    db, reader, demand, *_ = allocated
    with pytest.raises(supply.MaterialRequestSupplyError) as caught:
        planning_capacity(db, actor=replace(reader, account_status='disabled'), request_id=demand.id)
    assert caught.value.http_status_code == 403


@pytest.mark.parametrize('values', [
    (Decimal(2), Decimal(0), Decimal(3), Decimal(0)),
    (Decimal(2), Decimal(0), Decimal(0), Decimal(3)),
    (Decimal(2), Decimal(3), Decimal(0), Decimal(0)),
    (Decimal(2), Decimal(0), Decimal('-1'), Decimal(0)),
    (Decimal(2), Decimal(0), Decimal('.0001'), Decimal(0)),
    (Decimal(2), Decimal(0), 0.0, Decimal(0)),
])
def test_invalid_quantity_facts_are_rejected_instead_of_clamped(values):
    with pytest.raises(ValueError):
        quantity_capacity(**dict(zip(('approved', 'cancelled', 'allocated', 'planned'), values)))
