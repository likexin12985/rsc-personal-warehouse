"""Formal returns reserve/release stock without claiming shipment or acceptance."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.inventory_models import StockAccount, StockBalance, StockLocation, CustodyAssignment
from app.stock_operation_models import StockOperationOrder, StockOperationCancellation
from app.stock_return_schemas import StockReturnPreviewIn, StockReturnSubmitIn, StockReturnCancelIn
from app.formal_services.stock_return_plan import preview_return
from app.formal_services.stock_return_commands import submit_return, cancel_return
from app.formal_services.stock_return_facts import order_result, cancellation_result
from test_work_order_return_sources import db, world, stock, recovered, request, sources
from test_work_order_return_sources import counts, inventory, change_status
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services import stock_return_commands as commands, inventory_posting as posting


@pytest.fixture
def destination(db, stock, recovered):
    source = db.get(StockLocation, recovered.account.location_id)
    target = db.get(StockLocation, source.parent_id)
    target.custodian_person_id = stock.world.headquarters_reviewer_person.id
    now = datetime.now(timezone.utc) - timedelta(minutes=1)
    assignment = CustodyAssignment(location_id=target.id, custodian_person_id=target.custodian_person_id, valid_from=now)
    transit = StockLocation(code="RETURN-TRANSIT-" + uuid4().hex, name="退回在途", location_type="transit",
        owner_org_id=target.owner_org_id, parent_id=target.id, status="active")
    db.add_all((assignment, transit)); db.commit()
    stock.actor = replace(stock.actor, entitlements=stock.actor.entitlements + tuple(
        replace(stock.actor.entitlements[0], resource="stock_operation", action=action) for action in ("read", "submit_return", "cancel_return")))
    stock.world.current_principal = stock.actor
    return target, transit


def submission(db, stock, recovered, destination, *, reason="拆回旧件退回区域仓"):
    target, transit = destination
    value = StockReturnPreviewIn(**request(stock, recovered).model_dump(), target_location_id=target.id,
        transit_location_id=transit.id, reason=reason)
    preview, _ = preview_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value)
    return StockReturnSubmitIn(**value.model_dump(), expected_plan_hash=preview.plan_hash,
        idempotency_key=uuid4().hex, request_id=uuid4().hex)


def test_submit_cancel_and_exact_replays_preserve_original_responsibility(db, stock, recovered, destination):
    value = submission(db, stock, recovered, destination)
    posted = submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value); db.commit()
    original = db.get(StockOperationOrder, posted.operation_id)
    assert order_result(db, actor=stock.actor, order=original) == posted
    held = db.scalar(select(StockAccount).where(StockAccount.location_id == recovered.account.location_id,
        StockAccount.material_id == recovered.account.material_id, StockAccount.availability_bucket == "return_pending"))
    assert held is not None and held.custodian_person_id == stock.actor.person_id
    assert db.get(StockBalance, held.id).quantity == 1
    observed = sources(db, stock).items[0]
    assert observed.owed_quantity == observed.committed_quantity == "1.000" and observed.selectable_quantity == "0.000"
    assert submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value) == posted
    db.commit()
    cancel = StockReturnCancelIn(operator_person_id=stock.actor.person_id, reason="尚未寄出，重新核对退回安排",
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    cancelled = cancel_return(db, actor=stock.actor, operation_id=original.id, request=cancel); db.commit()
    assert cancellation_result(db, actor=stock.actor, order=original,
        cancellation=db.get(StockOperationCancellation, cancelled.cancellation_id)) == cancelled
    assert db.get(StockBalance, held.id).quantity == 0
    assert cancel_return(db, actor=stock.actor, operation_id=original.id, request=cancel) == cancelled
    assert order_result(db, actor=stock.actor, order=original) == posted
    db.execute(text("PRAGMA query_only=ON"))
    after = sources(db, stock).items[0]
    assert after.owed_quantity == after.selectable_quantity == "1.000" and after.committed_quantity == "0.000"


def _snapshot(db):
    return counts(db), inventory(db), tuple(tuple(db.execute(text(f"SELECT * FROM {table} ORDER BY id")))
        for table in ("stock_operation_orders", "stock_operation_lines", "stock_operation_serials", "stock_operation_cancellations"))


@pytest.mark.parametrize("action", ["submit_return", "cancel_return"])
def test_separate_current_permission_is_required(db, stock, recovered, destination, action):
    value = submission(db, stock, recovered, destination)
    if action == "cancel_return":
        result = submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value); db.commit()
        value = StockReturnCancelIn(operator_person_id=stock.actor.person_id, reason="权限撤销后不准取消",
            idempotency_key=uuid4().hex, request_id=uuid4().hex)
    stock.world.current_principal = replace(stock.actor, entitlements=tuple(row for row in stock.actor.entitlements
        if not (row.resource == "stock_operation" and row.action == action)))
    before = _snapshot(db)
    with pytest.raises(InventoryReadError) as exc:
        if action == "submit_return": submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value)
        else: cancel_return(db, actor=stock.actor, operation_id=result.operation_id, request=value)
    assert exc.value.code == "stock_return_forbidden"
    assert _snapshot(db) == before


@pytest.mark.parametrize("damage", ["target", "transit", "custody", "plan"])
def test_changed_destination_or_plan_cannot_reserve_stock(db, stock, recovered, destination, damage):
    value = submission(db, stock, recovered, destination)
    target, transit = destination
    if damage == "target": target.parent_id, target.status = None, "inactive"
    elif damage == "transit": transit.parent_id = stock.account.location_id
    elif damage == "custody":
        assignment = db.scalar(select(CustodyAssignment).where(CustodyAssignment.location_id == target.id))
        assignment.custodian_person_id = stock.actor.person_id
    else: value = value.model_copy(update={"expected_plan_hash": "0" * 64})
    db.commit(); before = _snapshot(db)
    with pytest.raises(InventoryReadError):
        submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value)
    assert _snapshot(db) == before


def test_closed_work_order_keeps_return_and_original_cancel_history(db, stock, recovered, destination):
    change_status(db, stock.orders[0], "closed")
    value = submission(db, stock, recovered, destination)
    posted = submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value); db.commit()
    cancelled = cancel_return(db, actor=stock.actor, operation_id=posted.operation_id,
        request=StockReturnCancelIn(operator_person_id=stock.actor.person_id, reason="原退回尚未发出",
            idempotency_key=uuid4().hex, request_id=uuid4().hex)); db.commit()
    assert cancelled.status == "cancelled"
    assert order_result(db, actor=stock.actor, order=db.get(StockOperationOrder, posted.operation_id)) == posted


@pytest.mark.parametrize("stage", ["submit", "cancel"])
def test_posting_and_document_evidence_roll_back_together(db, stock, recovered, destination, monkeypatch, stage):
    value = submission(db, stock, recovered, destination)
    if stage == "cancel":
        posted = submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value); db.commit()
    before = _snapshot(db)
    def fail_after_posting(*_args, **_kwargs): raise RuntimeError("synthetic domain audit failure")
    monkeypatch.setattr(commands, "_record", fail_after_posting)
    with pytest.raises(RuntimeError, match="synthetic domain audit failure"):
        with db.begin_nested():
            if stage == "submit": submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value)
            else: cancel_return(db, actor=stock.actor, operation_id=posted.operation_id,
                request=StockReturnCancelIn(operator_person_id=stock.actor.person_id, reason="失败事务应保留占用",
                    idempotency_key=uuid4().hex, request_id=uuid4().hex))
    db.expire_all()
    assert _snapshot(db) == before


def test_active_return_prevents_recovery_inverse_and_generic_reversal(db, stock, recovered, destination):
    from app.formal_services.work_order_reversal_plan import preview_reversal
    from app.work_order_reversal_schemas import WorkOrderReversalPreviewIn
    from app.formal_services.work_order_material import WorkOrderMaterialPreflightError
    value = submission(db, stock, recovered, destination)
    posted = submit_return(db, actor=stock.actor, work_order_id=stock.orders[0].id, request=value); db.commit()
    before = _snapshot(db)
    with pytest.raises(WorkOrderMaterialPreflightError) as exc:
        preview_reversal(db, actor=stock.actor, work_order_id=stock.orders[0].id,
            request=WorkOrderReversalPreviewIn(operator_person_id=stock.actor.person_id, reason="原更换已有退回",
                original_replacement_id=recovered.parent.id))
    assert exc.value.code == "work_order_recovery_has_active_return"
    from types import SimpleNamespace
    with pytest.raises(posting.InventoryPostingError) as exc:
        posting._require_generic_reversal_origin(db, SimpleNamespace(original_transaction_id=posted.posting_transaction_id,
            source_document_type="renamed_adjustment"))
    assert exc.value.code == "stock_return_reversal_requires_command"
    assert _snapshot(db) == before
