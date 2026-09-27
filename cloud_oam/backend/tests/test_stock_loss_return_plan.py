"""Loss-derived return plans preserve approval, custody and stock without writes."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.inventory_models import StockAccount, StockLocation, CustodyAssignment
from app.formal_services.inventory_query import InventoryReadError
from test_stock_loss_dispositions import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    prepare, snapshot, second_report,
)


from app import stock_loss_return_schemas as schemas
from app.formal_services import stock_loss_return_plan as plan

@pytest.fixture
def route(db, allowed, approved):
    source = db.get(StockLocation, allowed.account.location_id)
    target = db.get(StockLocation, source.parent_id)
    target.custodian_person_id = allowed.world.headquarters_reviewer_person.id
    assignment = CustodyAssignment(location_id=target.id,
        custodian_person_id=target.custodian_person_id,
        valid_from=datetime.now(timezone.utc) - timedelta(minutes=1))
    transit = StockLocation(code='TEST-LOSS-RETURN-' + uuid4().hex, name='合成退回在途',
        location_type='transit', owner_org_id=target.owner_org_id, parent_id=target.id, status='active')
    db.add_all((assignment, transit)); db.commit()
    return target, transit


def request(db, approved, route, kind='return_to_region'):
    origin = prepare(db, approved, kind)
    return schemas.StockLossReturnPreviewIn(**origin.model_dump(),
        target_location_id=route[0].id, transit_location_id=route[1].id)


def test_approved_new_stock_keeps_exact_origin_and_custody_without_work_order_read(db, allowed, approved, route, monkeypatch):
    value = request(db, approved, route)
    from app.formal_services import work_order_material
    def no_work_order(*args, **kwargs):
        raise AssertionError('loss-derived return must not invoke a work-order authorization path')
    monkeypatch.setattr(work_order_material, 'authorize_work_order', no_work_order)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    first = plan.preview_loss_return(db, actor=approved.actor, request=value)
    second = plan.preview_loss_return(db, actor=approved.actor, request=value)
    assert first['plan_hash'] == second['plan_hash']
    assert first['planning_status'] == 'preview_only' and first['stock_effect'] == 'none'
    assert first['condition_code'] == 'new' and first['quantity'] == '1.000'
    assert first['requester_id'] == first['custodian_person_id'] == str(allowed.actor.person_id)
    assert first['executor_person_id'] == str(approved.actor.person_id) != first['requester_id']
    assert first['movement_type'] == 'reserve' and first['return_fulfillment_required'] is True
    assert first['source_document_type'] == 'stock_operation_return'
    assert len(first['serial_ids']) == int(allowed.tracked)
    assert 'work_order_id' not in first and 'source_recovery_line_id' not in first
    assert db.get(StockAccount, UUID(first['pending_account_id'])) is None
    assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted


def test_another_approved_outcome_cannot_become_a_return(db, approved, route):
    value = request(db, approved, route, kind='convert_used'); before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        plan.preview_loss_return(db, actor=approved.actor, request=value)
    assert error.value.code == 'stock_loss_return_decision_required'
    assert snapshot(db) == before


def test_mismatched_transit_parent_is_refused_without_any_reservation(db, approved, route):
    value = request(db, approved, route)
    route[1].parent_id = None; db.commit(); before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        plan.preview_loss_return(db, actor=approved.actor, request=value)
    assert error.value.code == 'stock_return_destination_invalid'
    assert snapshot(db) == before


def test_only_current_hq_dispose_permission_can_prepare_the_return(db, allowed, approved, route):
    value = request(db, approved, route)
    actor = replace(approved.actor, entitlements=tuple(
        grant for grant in approved.actor.entitlements if grant.action != 'dispose_loss'))
    allowed.world.current_principal = actor; before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        plan.preview_loss_return(db, actor=actor, request=value)
    assert error.value.code == 'stock_loss_disposition_forbidden'
    assert snapshot(db) == before


@pytest.mark.parametrize('field', [
    'quantity', 'serial_ids', 'condition_code', 'requester_id', 'owner_org_id',
    'lot_id', 'source_account_id', 'pending_account_id', 'derived_return_operation_id',
    'work_order_id', 'source_recovery_line_id',
])
def test_request_cannot_replace_approved_inventory_dimensions(field):
    value = dict(headquarters_decision_id=uuid4(), expected_headquarters_review_hash='a' * 64,
        expected_submission_plan_hash='b' * 64, target_location_id=uuid4(), transit_location_id=uuid4())
    value[field] = 'client-selected'
    with pytest.raises(ValidationError):
        schemas.StockLossReturnPreviewIn.model_validate(value)


def test_shared_frozen_account_keeps_each_reports_quantity_and_serials(db, allowed, approved, regional, route, monkeypatch):
    value = request(db, approved, route)
    second_report(db, allowed, regional, monkeypatch)
    allowed.world.current_principal = approved.actor
    before = snapshot(db)
    result = plan.preview_loss_return(db, actor=approved.actor, request=value)
    assert result['quantity'] == '1.000' and result['source_balance_quantity'] == '2.000'
    assert len(result['holds']) == 2
    assert result['serial_ids'] == ([str(allowed.serials[3].id)] if allowed.tracked else [])
    assert snapshot(db) == before


@pytest.mark.parametrize('side', ['source', 'receiver'])
def test_expired_custody_refuses_the_plan_without_changing_stock(db, allowed, approved, route, side):
    value = request(db, approved, route)
    location_id = allowed.account.location_id if side == 'source' else route[0].id
    assignment = db.scalar(select(CustodyAssignment).where(CustodyAssignment.location_id == location_id))
    assignment.valid_to = datetime.now(timezone.utc) - timedelta(seconds=1)
    db.commit()
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        plan.preview_loss_return(db, actor=approved.actor, request=value)
    assert error.value.code == ('stock_loss_return_custody_changed' if side == 'source' else 'stock_return_receiver_unresolved')
    assert snapshot(db) == before


@pytest.mark.parametrize('field', ['expected_headquarters_review_hash', 'expected_submission_plan_hash'])
def test_stale_original_approval_cannot_prepare_a_new_return(db, approved, route, field):
    value = request(db, approved, route).model_copy(update={field: 'f' * 64})
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        plan.preview_loss_return(db, actor=approved.actor, request=value)
    assert error.value.code == 'stock_loss_disposition_approval_changed'
    assert snapshot(db) == before


def test_receiver_change_between_reads_invalidates_whole_preview(db, approved, route, monkeypatch):
    value = request(db, approved, route)
    actual = plan.destination
    calls = 0
    def changed(*args, **kwargs):
        nonlocal calls
        result = actual(*args, **kwargs)
        calls += 1
        # Simulate a newer valid receiver binding observed under READ COMMITTED.
        return result if calls == 1 else result.model_copy(update={'custody_assignment_id': uuid4()})
    monkeypatch.setattr(plan, 'destination', changed)
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        plan.preview_loss_return(db, actor=approved.actor, request=value)
    assert calls == 2 and error.value.code == 'stock_loss_return_plan_changed'
    assert snapshot(db) == before


def test_plan_hash_binds_the_exact_valid_transit_route(db, approved, route):
    value = request(db, approved, route)
    alternate = StockLocation(code='TEST-ALTERNATE-' + uuid4().hex, name='另一合成在途位置',
        location_type='transit', owner_org_id=route[0].owner_org_id, parent_id=route[0].id, status='active')
    db.add(alternate)
    db.commit()
    before = snapshot(db)
    first = plan.preview_loss_return(db, actor=approved.actor, request=value)
    second = plan.preview_loss_return(db, actor=approved.actor,
        request=value.model_copy(update={'transit_location_id': alternate.id}))
    assert first['plan_hash'] != second['plan_hash']
    assert first['derived_return_operation_id'] == second['derived_return_operation_id']
    assert first['pending_account_id'] == second['pending_account_id']
    assert snapshot(db) == before


def test_overlapping_other_custodian_cannot_be_ignored(db, allowed, approved, route):
    value = request(db, approved, route)
    now = datetime.now(timezone.utc)
    # The unique open-ended assignment index does not cover an overlapping
    # finite interval assigned to another person. Check the whole location.
    db.add(CustodyAssignment(location_id=allowed.account.location_id,
        custodian_person_id=allowed.world.headquarters_reviewer_person.id,
        valid_from=now, valid_to=now + timedelta(hours=1)))
    db.commit()
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as caught:
        plan.preview_loss_return(db, actor=approved.actor, request=value)
    assert caught.value.code == 'stock_loss_return_custody_changed'
    assert snapshot(db) == before
