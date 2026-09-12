"""Real application posting, all-or-none compensation and immutable recovery."""
from uuid import uuid4
from dataclasses import replace
from decimal import Decimal

import pytest

from sqlalchemy import select

from app.demand_models import WorkOrderReversal
from app.formal_services.work_order_reversal_write import execute_reversal
from app.formal_services.work_order_reversal_read import reversal_result
from app.formal_services.work_order_reservations import read_work_order_reservations
from app.work_order_reversal_schemas import WorkOrderReversalIn
from test_work_order_reversal_plan import db, world, stock, operation, preview, request
from test_work_order_removed_registration import counts, inventory


def submission(db, stock, *, original=None, parent=None, order=None):
    result = preview(db, stock, original=original, parent=parent, order=order)
    return WorkOrderReversalIn(operator_person_id=stock.actor.person_id, original_operation_id=result.original_operation_id,
        original_replacement_id=result.original_replacement_id, reason=result.reason, expected_plan_hash=result.plan_hash,
        idempotency_key=uuid4().hex, request_id=uuid4().hex)


def test_consumption_compensation_restores_exact_original_reservation(db, stock):
    source = operation(db, stock)
    value = submission(db, stock, original=source)
    result = execute_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value)
    db.commit()
    row = db.get(WorkOrderReversal, result.reversal_id)
    assert reversal_result(db, actor=stock.actor, parent=row) == result
    assert execute_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value) == result
    remaining = read_work_order_reservations(db, work_order_id=stock.orders[0].id, account_ids=(stock.reserved.id,))
    assert remaining[stock.reserved.id].quantity == 2
    if stock.tracked:
        from app.formal_services.serial_ledger import rebuild_serial_states
        state = rebuild_serial_states(db, [stock.serials[0].id])[stock.serials[0].id]
        assert state.lifecycle_status == "active" and state.stock_account_id == stock.reserved.id


def test_whole_replacement_appends_both_inverses(db, stock):
    from test_work_order_replacement_preview import inputs
    from app.formal_services.work_order_replacements import execute_replacement
    parent = execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id, **inputs(stock),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    value = submission(db, stock, parent=parent)
    result = execute_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value)
    db.commit()
    assert [item.original_operation_id for item in result.items] == [parent.recover_operation_id, parent.consume_operation_id]
    assert reversal_result(db, actor=stock.actor, parent=db.get(WorkOrderReversal, result.reversal_id)) == result


@pytest.mark.parametrize("stock", ["serial"], indirect=True)
def test_first_registered_recovery_reversal_retains_registration_and_original_scope(db, stock):
    from test_work_order_removed_registration import create, replacement_input
    from app.formal_services import work_order_replacements as paired
    from app.formal_services.work_order_completion import completion_check
    identity = create(db, stock)
    args = replacement_input(stock, identity)
    parent = paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        **args, idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    value = submission(db, stock, parent=parent)
    execute_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value)
    db.commit()
    check = completion_check(db, actor=stock.actor, work_order_id=stock.orders[0].id)
    assert any(issue.kind == "pending_recovery" for issue in check.issues)
    assert not any(issue.kind == "pending_return" for issue in check.issues)
    # Physical identity remains bound to its original work order and basis even
    # though its latest ledger movement is now an inverse, not an empty history.
    other = stock.orders[1]
    wrong = dict(args, consume_lines=(stock.line("1", stock.serials[2:3], identifier=stock.reserved.id),),
        pairs=(paired.material.WorkOrderReplacementPairInput(stock.serials[2].id, identity.serial_id),))
    with pytest.raises(paired.material.InventoryPostingError):
        paired.execute_replacement(db, actor=stock.actor, work_order_id=other.id, **wrong,
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.rollback()
    # A corrected independent command in the same original scope is valid.
    paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id, **args,
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()


def test_second_inverse_failure_rolls_back_entire_parent_and_both_stock_effects(db, stock, monkeypatch):
    from test_work_order_replacement_preview import inputs
    from app.formal_services import work_order_reversal_write as writer, work_order_replacements as paired
    parent = paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id, **inputs(stock),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    value = submission(db, stock, parent=parent)
    baseline = counts(db), inventory(db)
    original_writer = writer.posting._post_new_transaction
    calls = []
    def fail_second(*args, **kwargs):
        calls.append(kwargs["reversed_transaction_id"])
        if len(calls) == 2:
            raise RuntimeError("synthetic second inverse failure")
        return original_writer(*args, **kwargs)
    monkeypatch.setattr(writer.posting, "_post_new_transaction", fail_second)
    with pytest.raises(RuntimeError, match="synthetic second"):
        execute_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value)
    db.rollback()
    assert (counts(db), inventory(db)) == baseline
    assert db.scalar(select(WorkOrderReversal)) is None


def test_stale_plan_and_changed_request_cannot_append_compensation(db, stock):
    from app.formal_services import work_order_material as material
    original = operation(db, stock)
    value = submission(db, stock, original=original)
    baseline = counts(db), inventory(db)
    with pytest.raises(material.InventoryPostingError) as error:
        execute_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id,
            request=value.model_copy(update={"expected_plan_hash": "0" * 64}))
    assert error.value.code == "work_order_reversal_plan_changed"
    db.rollback()
    assert (counts(db), inventory(db)) == baseline
    result = execute_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value)
    db.commit()
    with pytest.raises(material.InventoryPostingError) as error:
        execute_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id,
            request=value.model_copy(update={"request_id": uuid4().hex}))
    assert error.value.code == "idempotency_conflict"
    assert db.scalar(select(WorkOrderReversal)).id == result.reversal_id


@pytest.mark.parametrize("stock", ["quantity"], indirect=True)
def test_quantity_recovery_inverse_preserves_other_material_obligations(db, stock):
    from app.inventory_models import StockAccount, StockBalance
    from app.formal_services import work_order_material as material
    target = StockAccount(owner_org_id=stock.account.owner_org_id, custodian_person_id=stock.actor.person_id,
        location_id=stock.account.location_id, material_id=stock.world.material.id, lot_id=None,
        condition_code="used", availability_bucket="available")
    db.add(target); db.commit()
    original, _ = material.execute_recover_operation(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        lines=(material.WorkOrderMaterialLineInput(target.material_id, target.id, Decimal("1.125"), condition_before="used", target_stock_account_id=target.id),),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    value = submission(db, stock, original=original)
    execute_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value)
    db.commit()
    assert db.get(StockBalance, target.id).quantity == 0
    assert read_work_order_reservations(db, work_order_id=stock.orders[0].id, account_ids=(stock.reserved.id,))[stock.reserved.id].quantity == 1


@pytest.mark.parametrize("stock", ["serial"], indirect=True)
def test_mixed_sku_lot_and_serial_pair_is_compensated_without_changing_identity(db, stock):
    from app.inventory_models import InventoryLot, SerialCurrentPosition
    from test_inventory_posting import make_material
    from test_work_order_removed_registration import create, scan
    from app.formal_services import work_order_material as material, work_order_replacements as paired
    sku = make_material(db, stock.world.source, tracking_mode="lot_and_serial", quantity_scale=0, allow_fraction=False)
    lot = InventoryLot(id=uuid4(), material_id=sku.id, lot_no="EXACT-REVERSED-REMOVED-LOT")
    db.add(lot); db.commit()
    physical = scan(stock, sku_code=sku.sku_code, lot_no=lot.lot_no)
    identity = create(db, stock, value=physical)
    consumed = stock.line("1", stock.serials[1:2], identifier=stock.reserved.id)
    removed = paired.RecoveryLineInput(stock.reserved.id, sku.id, Decimal(1), "used", lot_id=lot.id,
        serial_ids=(identity.serial_id,), serial_verifications=(material.SerialVerificationInput(identity.serial_id,
            sku.sku_code, physical.serial_no, physical.qr_code),))
    parent = paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        consume_lines=(consumed,), recover_lines=(removed,), pairs=(material.WorkOrderReplacementPairInput(consumed.serial_ids[0], identity.serial_id),),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    result = execute_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=submission(db, stock, parent=parent))
    db.commit()
    assert len(result.items) == 2 and db.get(SerialCurrentPosition, identity.serial_id).stock_account_id is None
    assert db.get(InventoryLot, lot.id).lot_no == lot.lot_no
