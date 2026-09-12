"""Whole original reversal plans over real opening and immutable work-order facts."""
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.demand_models import WorkOrderMaterialOperation
from app.inventory_models import InventoryTransaction, SerialCurrentPosition, InventorySerial, StockAccount, InventoryMovement
from app.formal_services import work_order_material as material, work_order_replacements as paired
from app.formal_services import work_order_reversal_plan as plan
from app.work_order_reversal_schemas import WorkOrderReversalPreviewIn
from test_work_order_material_options import db, world, stock
from test_work_order_removed_registration import counts, inventory, create, replacement_input, scan


def operation(db, stock, kind="consume", order=None):
    return db.scalar(select(WorkOrderMaterialOperation).where(
        WorkOrderMaterialOperation.oam_work_order_id == (order or stock.orders[0]).id,
        WorkOrderMaterialOperation.operation_type == kind))


def request(stock, **values):
    return WorkOrderReversalPreviewIn(operator_person_id=stock.actor.person_id,
        reason="登记错误，核对实物后申请冲销。", **values)


def preview(db, stock, *, original=None, parent=None, order=None):
    return plan.preview_reversal(db, actor=stock.actor, work_order_id=(order or stock.orders[0]).id,
        request=request(stock, original_operation_id=original.id if original else None,
            original_replacement_id=parent.id if parent else None))


def test_consume_inverse_restores_original_reservation_and_serial_state_without_posting(db, stock):
    original = operation(db, stock)
    baseline = counts(db), inventory(db)
    db.execute(text("PRAGMA query_only=ON"))
    result = preview(db, stock, original=original)
    assert result.planning_status == "preview_only" and len(result.children) == 1
    movement = result.children[0].movements[0]
    assert movement.from_account_id is None and movement.to_account_id == stock.reserved.id
    assert movement.reservation_delta == "1.000" and movement.quantity == "1.000"
    if stock.tracked:
        assert [(row.lifecycle_before, row.lifecycle_after) for row in movement.serials] == [("consumed", "active")]
    assert "qr_code" not in result.model_dump_json()
    assert (counts(db), inventory(db)) == baseline
    assert preview(db, stock, original=original).plan_hash == result.plan_hash


def test_unconsumed_occupancy_and_release_have_opposite_reservation_deltas(db, stock):
    original = operation(db, stock, "occupy", stock.orders[1])
    response = preview(db, stock, original=original, order=stock.orders[1])
    movement = response.children[0].movements[0]
    assert movement.from_account_id == stock.reserved.id and movement.to_account_id == stock.account.id
    assert movement.reservation_delta == "-1.000"
    released, _ = material.execute_release_operation(db, actor=stock.actor, work_order_id=stock.orders[1].id,
        lines=(stock.line("1", stock.serials[2:3], identifier=stock.reserved.id, target=stock.account.id),),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    restored = preview(db, stock, original=released, order=stock.orders[1]).children[0].movements[0]
    assert restored.from_account_id == stock.account.id and restored.to_account_id == stock.reserved.id
    assert restored.reservation_delta == "1.000"


def test_partial_use_cannot_undo_full_occupancy_from_other_orders_pooled_stock(db, stock):
    with pytest.raises(material.InventoryPostingError) as caught:
        preview(db, stock, original=operation(db, stock, "occupy"))
    assert caught.value.code == "work_order_reservation_insufficient"


def test_pair_is_always_whole_and_keeps_each_original_movement(db, stock):
    from test_work_order_replacement_preview import inputs
    parent = paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id, **inputs(stock),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    result = preview(db, stock, parent=parent)
    assert [child.original_operation_id for child in result.children] == [parent.recover_operation_id, parent.consume_operation_id]
    assert [child.movements[0].reservation_delta for child in result.children] == ["0.000", "1.000"]
    assert len(result.replacement_pairs) == (1 if stock.tracked else 0)


@pytest.mark.parametrize("stock", ["serial"], indirect=True)
def test_later_serial_recovery_prevents_undoing_an_earlier_consumption(db, stock):
    from test_work_order_replacement_preview import inputs
    original = operation(db, stock)
    paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id, **inputs(stock),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    with pytest.raises(material.InventoryPostingError) as caught:
        preview(db, stock, original=original)
    assert caught.value.code == "work_order_reversal_serial_has_later_activity"


@pytest.mark.parametrize("stock", ["serial"], indirect=True)
def test_reversal_keeps_mixed_sku_and_exact_lot_serial_identity(db, stock):
    from app.inventory_models import InventoryLot
    from test_inventory_posting import make_material
    sku = make_material(db, stock.world.source, tracking_mode="lot_and_serial", quantity_scale=0, allow_fraction=False)
    lot = InventoryLot(id=uuid4(), material_id=sku.id, lot_no="EXACT-REMOVED-LOT")
    db.add(lot); db.commit()
    physical = scan(stock, sku_code=sku.sku_code, lot_no=lot.lot_no)
    identity = create(db, stock, value=physical)
    consumed = stock.line("1", stock.serials[1:2], identifier=stock.reserved.id)
    returned = paired.RecoveryLineInput(stock.reserved.id, sku.id, Decimal(1), "used", lot_id=lot.id,
        serial_ids=(identity.serial_id,), serial_verifications=(material.SerialVerificationInput(identity.serial_id,
            sku.sku_code, physical.serial_no, physical.qr_code),))
    parent = paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        consume_lines=(consumed,), recover_lines=(returned,),
        pairs=(material.WorkOrderReplacementPairInput(consumed.serial_ids[0], identity.serial_id),),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    result = preview(db, stock, parent=parent)
    original_return, original_consume = (child.movements[0] for child in result.children)
    assert original_return.material_id == sku.id and original_consume.material_id == stock.world.material.id
    assert original_return.lot_id == lot.id and original_return.lot_no == lot.lot_no
    assert original_return.serials[0].serial_id == identity.serial_id and original_consume.lot_id is None


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_standalone_quantity_recovery_inverse_has_no_reservation_effect(db, stock):
    target = StockAccount(owner_org_id=stock.account.owner_org_id, custodian_person_id=stock.actor.person_id,
        location_id=stock.account.location_id, material_id=stock.world.material.id, lot_id=None,
        condition_code="used", availability_bucket="available")
    db.add(target); db.commit()
    fact, _ = material.execute_recover_operation(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        lines=(material.WorkOrderMaterialLineInput(target.material_id, target.id, Decimal("1.125"),
            condition_before="used", target_stock_account_id=target.id),), idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    row = preview(db, stock, original=fact).children[0].movements[0]
    assert row.from_account_id == target.id and row.to_account_id is None
    assert row.quantity == "1.125" and row.reservation_delta == "0.000" and not row.serials


def test_http_returns_a_private_preview_and_never_exposes_qr_or_posts(db, stock):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.routers import formal_work_order_material as router
    app = FastAPI(); app.include_router(router.router, prefix="/api")
    app.dependency_overrides[get_db] = lambda: db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name == "principal": app.dependency_overrides[dependency.call] = lambda: stock.actor
    body = request(stock, original_operation_id=operation(db, stock).id).model_dump(mode="json")
    path = f"/api/v1/work-orders/{stock.orders[0].id}/material-reversals/preview"
    before = counts(db), inventory(db)
    with TestClient(app) as client:
        response = client.post(path, json=body)
        assert response.status_code == 200, response.text
        assert response.headers["cache-control"] == "private, no-store"
        assert response.json()["planning_status"] == "preview_only" and "qr_code" not in response.text
        assert client.post(path, json={**body, "operator_person_id": str(uuid4())}).status_code == 403
        assert client.post(path, json={**body, "original_replacement_id": str(uuid4())}).status_code == 422
    assert (counts(db), inventory(db)) == before


@pytest.mark.parametrize("stock", ["serial"], indirect=True)
def test_whole_pair_restores_terminal_removed_state_and_new_registration_stays_independent(db, stock):
    for fresh in (False, True):
        savepoint = db.begin_nested()
        try:
            consumed = stock.line("1", stock.serials[1:2], identifier=stock.reserved.id)
            if fresh:
                registration = create(db, stock)
                args = replacement_input(stock, registration)
            else:
                serial = stock.serials[0]
                recovered = paired.RecoveryLineInput(stock.reserved.id, stock.world.material.id, Decimal(1), "used",
                    serial_ids=(serial.id,), serial_verifications=(material.SerialVerificationInput(
                        serial.id, stock.world.material.sku_code, serial.serial_no, serial.qr_code),))
                args = dict(consume_lines=(consumed,), recover_lines=(recovered,),
                    pairs=(material.WorkOrderReplacementPairInput(consumed.serial_ids[0], serial.id),))
            parent = paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id, **args,
                idempotency_key=uuid4().hex, request_id=uuid4().hex)
            db.flush()
            before = counts(db), inventory(db)
            result = preview(db, stock, parent=parent)
            assert [row.original_operation_type for row in result.children] == ["recover", "consume"]
            assert len(result.replacement_pairs) == 1
            removed = result.children[0].movements[0]
            assert removed.from_account_id and removed.to_account_id is None and removed.reservation_delta == "0.000"
            assert removed.serials[0].lifecycle_after == ("active" if fresh else "consumed")
            assert (removed.serials[0].registration_id is not None) == fresh
            assert result.children[1].movements[0].serials[0].lifecycle_after == "active"
            assert (counts(db), inventory(db)) == before
            for child in (parent.consume_operation_id, parent.recover_operation_id):
                with pytest.raises(material.InventoryPostingError) as caught:
                    preview(db, stock, original=db.get(WorkOrderMaterialOperation, child))
                assert caught.value.code == "work_order_reversal_parent_required"
        finally:
            savepoint.rollback()
            db.expire_all()


@pytest.mark.parametrize("change", ["foreign_order", "missing", "foreign_operator", "closed", "stale", "inactive_sku", "frozen", "audit_missing"])
def test_unavailable_or_incomplete_original_never_produces_a_partial_plan(db, stock, change):
    original = operation(db, stock)
    order = stock.orders[0]
    if change == "foreign_order": order = stock.orders[2]
    elif change == "missing": original = type("Original", (), {"id": uuid4()})()
    elif change == "foreign_operator": original.operator_person_id = stock.world.headquarters_reviewer_person.id
    elif change == "closed": order.status = "closed"
    elif change == "stale": order.updated_at -= timedelta(hours=2)
    elif change == "inactive_sku": stock.world.material.status = "inactive"
    elif change == "frozen":
        from test_inventory_posting import freeze_account_scope
        freeze_account_scope(db, stock.world, stock.reserved, freeze_mode="hard", scope_mode="filtered")
    else:
        from app.foundation_models import OutboxEvent
        row = db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id == str(original.id)))
        db.delete(row)
    db.commit()
    before = counts(db), inventory(db)
    with pytest.raises((material.InventoryPostingError, plan.inventory.InventoryReadError)):
        preview(db, stock, original=original, order=order)
    assert (counts(db), inventory(db)) == before


@pytest.mark.parametrize("change", ["authority", "ledger", "policy"])
def test_change_during_plan_discards_the_complete_proposal(db, stock, monkeypatch, change):
    original_children = plan._children
    def changed(*args, **kwargs):
        result = original_children(*args, **kwargs)
        if change == "authority":
            stock.world.current_principal = replace(stock.actor, authorization_version=stock.actor.authorization_version + 1)
        elif change == "ledger":
            from app.inventory_models import InventoryLedgerHead
            db.scalar(select(InventoryLedgerHead)).next_cursor += 1; db.flush()
        else:
            from app.inventory_models import MaterialInventoryPolicy
            policy = db.scalar(select(MaterialInventoryPolicy).where(MaterialInventoryPolicy.material_id == stock.world.material.id))
            policy.quantity_scale = 1; db.flush()
        return result
    monkeypatch.setattr(plan, "_children", changed)
    with pytest.raises((material.InventoryPostingError, plan.inventory.InventoryReadError)):
        preview(db, stock, original=operation(db, stock))


@pytest.mark.parametrize("values", [{}, {"original_operation_id": uuid4(), "original_replacement_id": uuid4()},
    {"original_operation_id": uuid4(), "reason": "   "}, {"original_operation_id": uuid4(), "reason": "bad\ud800"},
    {"original_operation_id": uuid4(), "unexpected": "field"}])
def test_request_requires_one_exact_original_and_a_real_reason(values):
    with pytest.raises(ValidationError):
        WorkOrderReversalPreviewIn(**{"operator_person_id": uuid4(), "reason": "录入错误", **values})
