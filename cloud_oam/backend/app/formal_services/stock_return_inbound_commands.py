"""Post accepted return quantities into the verified local stock account.

This command is intentionally independent from parcel acceptance and from the
legacy ``InboundOrder`` projection.  It creates one immutable inbound fact and
one unified inventory transfer in the same database transaction.
"""

from __future__ import annotations

from datetime import datetime, timezone
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..foundation_models import OutboxEvent, StateTransitionEvent
from ..inventory_models import InventoryTransaction, CustodyAssignment
from ..stock_operation_models import (
    StockOperationReceipt,
    StockOperationReceiptSerial,
    StockOperationReturnInbound,
    StockOperationReturnInboundLine,
    StockOperationReturnInboundPosting,
    StockOperationReturnInboundSerial,
)
from . import inventory_posting as posting
from .audit_chain import append_audit_event, lock_audit_chain_head
from .stock_return_inbound_contract import ReturnInboundLine, build_return_inbound_command
from .stock_return_inbound_plan import plan_return_inbound
from .work_order_return_sources import _fail, _hash
from .notification_events import record_stock_return_notification


def _json_plan(plan: dict) -> dict:
    return {
        "schema_version": plan["schema_version"],
        "receipt_id": str(plan["receipt_id"]),
        "shipment_id": str(plan["shipment_id"]),
        "operator_person_id": str(plan["operator_person_id"]),
        "authorization_version": plan["authorization_version"],
        "target_location_id": str(plan["target_location_id"]),
        "target_custody_assignment_id": str(plan["target_custody_assignment_id"]),
        "receipt_plan_hash": plan["receipt_plan_hash"],
        "ledger_cursor": plan["ledger_cursor"],
        "lines": plan["lines"],
    }


def _result(row: StockOperationReturnInbound, *, replayed: bool = False) -> dict:
    return {
        "schema_version": "1.0",
        "inbound_id": row.id,
        "inbound_no": row.inbound_no,
        "receipt_id": row.receipt_id,
        "shipment_id": row.shipment_id,
        "target_location_id": row.target_location_id,
        "target_custody_assignment_id": row.target_custody_assignment_id,
        "status": row.status,
        "posting_transaction_id": row.posting_transaction_id,
        "request_id": row.request_id,
        "request_hash": row.request_hash,
        "plan_hash": row.plan_hash,
        "replayed": replayed,
    }


def _request_hash(*, receipt_id: uuid.UUID, request_id: str, plan_hash: str) -> str:
    """Return the pre-write recovery digest shared with the client contract."""
    return _hash({"receipt_id": str(receipt_id), "request_id": request_id, "plan_hash": plan_hash})


def preview_return_inbound(db: Session, *, actor, receipt_id: uuid.UUID) -> dict:
    plan = plan_return_inbound(db, actor=actor, receipt_id=receipt_id)
    document = _json_plan(plan)
    return {
        **document,
        "planning_status": "inbound_preview_only",
        "plan_hash": _hash(document),
        "checked_at": plan["checked_at"],
    }


def execute_return_inbound(
    db: Session,
    *,
    actor,
    receipt_id: uuid.UUID,
    expected_plan_hash: str,
    request_id: str,
    idempotency_key: str,
) -> dict:
    """Commit one exact return inbound, or replay its identical request."""

    current = posting._require_current_actor(db, actor)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    posting._require_request_id(request_id)
    key = posting._require_idempotency_key(idempotency_key)
    key_hash = posting._storage_hash(key)

    existing = db.scalar(
        select(StockOperationReturnInbound)
        .where(StockOperationReturnInbound.idempotency_key_hash == key_hash)
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        if existing.actor_user_id != current.user_id or existing.request_id != request_id:
            _fail("stock_return_inbound_idempotency_conflict", "conflict", "原入账请求键已绑定其他请求")
        if existing.plan_hash != expected_plan_hash:
            _fail("stock_return_inbound_plan_changed", "conflict", "原入账方案与当前请求不一致，请回读原请求")
        return _result(existing, replayed=True)

    locked_receipt = db.scalar(
        select(StockOperationReceipt)
        .where(StockOperationReceipt.id == receipt_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_receipt is None:
        _fail("stock_return_inbound_receipt_not_found", "not_found", "退回验收事实不存在")
    existing_receipt = db.scalar(
        select(StockOperationReturnInbound)
        .where(StockOperationReturnInbound.receipt_id == receipt_id)
        .execution_options(populate_existing=True)
    )
    if existing_receipt is not None:
        if existing_receipt.actor_user_id != current.user_id or existing_receipt.plan_hash != expected_plan_hash:
            _fail("stock_return_inbound_receipt_already_posted", "conflict", "该退回验收已完成入账")
        return _result(existing_receipt, replayed=True)

    plan = plan_return_inbound(db, actor=current, receipt_id=receipt_id)
    plan_json = _json_plan(plan)
    plan_hash = _hash(plan_json)
    if plan_hash != expected_plan_hash:
        _fail("stock_return_inbound_plan_changed", "conflict", "入账方案已变化，请重新核验")

    inbound_id = uuid.uuid4()
    movements = tuple(
        ReturnInboundLine(
            receipt_line_id=uuid.UUID(line["receipt_line_id"]),
            source_account_id=uuid.UUID(line["source_account_id"]),
            target_account_id=uuid.UUID(line["target_account_id"]),
            material_id=uuid.UUID(line["material_id"]),
            condition_code=line["condition_code"],
            lot_id=uuid.UUID(line["lot_id"]) if line.get("lot_id") else None,
            accepted_quantity=line["accepted_qty"],
            serial_ids=tuple(uuid.UUID(value) for value in line.get("serial_ids", ())),
        )
        for line in plan["lines"]
    )
    command = build_return_inbound_command(
        receipt_id=receipt_id,
        inbound_id=inbound_id,
        effective_at=plan["checked_at"],
        lines=movements,
    )
    command_json = {
        "source_document_type": command.source_document_type,
        "source_document_id": command.source_document_id,
        "posting_key": command.posting_key,
        "movement_type": command.movement_type,
        "effective_at": command.effective_at.isoformat(),
        "movements": [
            {
                "from_account_id": str(movement.from_account_id),
                "to_account_id": str(movement.to_account_id),
                "quantity": format(movement.quantity, ".3f"),
                "serial_ids": [str(value) for value in movement.serial_ids],
            }
            for movement in command.movements
        ],
    }
    # The recovery marker must be computable before the write.  Do not include
    # the randomly allocated inbound id or the wall-clock checked_at value in
    # this digest: both are only known inside this transaction.  The plan hash
    # already binds the exact receipt, target accounts, quantities and SNs.
    request_hash = _request_hash(receipt_id=receipt_id, request_id=request_id, plan_hash=plan_hash)
    head = lock_audit_chain_head(db, stream_key="material_request")
    now = datetime.now(timezone.utc)

    result = posting.post_inventory_transaction(
        db,
        actor=current,
        command=command,
        idempotency_key=key,
        request_id=request_id,
        permission_action="receive_return",
        permission_resource="stock_operation",
    )
    row = StockOperationReturnInbound(
        id=inbound_id,
        inbound_no=f"RET-IN-{inbound_id.hex[:20].upper()}",
        receipt_id=receipt_id,
        shipment_id=plan["shipment_id"],
        actor_user_id=current.user_id,
        operator_person_id=current.person_id,
        target_location_id=plan["target_location_id"],
        target_custody_assignment_id=plan["target_custody_assignment_id"],
        authorization_version=current.authorization_version,
        status="posted",
        reason=plan["reason"],
        request_id=request_id,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        receipt_plan_hash=plan["receipt_plan_hash"],
        plan_hash=plan_hash,
        audit_version=head.version + 1,
        command_jsonb=command_json,
        plan_jsonb=plan_json,
        posting_transaction_id=result.transaction_id,
        created_at=now,
    )
    db.add(row)
    db.flush()
    for number, movement in enumerate(movements, 1):
        line_id = uuid.uuid4()
        db.add(StockOperationReturnInboundLine(
            id=line_id, inbound_id=inbound_id, receipt_line_id=movement.receipt_line_id,
            line_no=number, source_account_id=movement.source_account_id,
            target_account_id=movement.target_account_id, material_id=movement.material_id,
            lot_id=movement.lot_id, condition_code=movement.condition_code,
            accepted_qty=movement.accepted_quantity,
            created_at=now,
        ))
        db.flush()
        for serial_id in movement.serial_ids:
            receipt_serial = db.scalar(select(StockOperationReceiptSerial).where(
                StockOperationReceiptSerial.line_id == movement.receipt_line_id,
                StockOperationReceiptSerial.serial_id == serial_id,
                StockOperationReceiptSerial.result == "accepted",
            ))
            if receipt_serial is None:
                _fail("stock_return_inbound_serial_missing", "precondition_failed", "入账 SN 未在验收事实中确认")
            db.add(StockOperationReturnInboundSerial(
                id=uuid.uuid4(), line_id=line_id, inbound_id=inbound_id,
                receipt_serial_id=receipt_serial.id, serial_id=serial_id,
                created_at=now,
            ))
    db.add(StockOperationReturnInboundPosting(
        id=uuid.uuid4(), inbound_id=inbound_id,
        inventory_transaction_id=result.transaction_id, created_at=now,
    ))
    body = {
        key: (str(value) if isinstance(value, uuid.UUID) else value)
        for key, value in _result(row).items()
    }
    append_audit_event(
        db, stream_key="material_request", actor_user_id=current.user_id,
        action="stock_return_inbound_posted", aggregate_type="stock_operation_return_inbound",
        aggregate_id=str(inbound_id), before_jsonb={}, after_jsonb=body,
        request_id=request_id, occurred_at=now, created_at=now,
    )
    db.add(OutboxEvent(
        event_type="stock_return_inbound_posted", aggregate_type="stock_operation_return_inbound",
        aggregate_id=str(inbound_id), payload_jsonb=body, idempotency_key=f"stock-return-inbound:{inbound_id}",
        available_at=now, created_at=now, updated_at=now,
    ))
    db.add(StateTransitionEvent(
        aggregate_type="stock_operation_return_inbound", aggregate_id=str(inbound_id),
        from_status=None, to_status="posted", actor_id=current.user_id,
        reason="stock_return_inbound_posted", idempotency_key=f"stock-return-inbound:{inbound_id}",
        occurred_at=now, metadata_jsonb=body, created_at=now,
    ))
    assignment = db.get(CustodyAssignment, row.target_custody_assignment_id)
    record_stock_return_notification(
        db,
        event_type="stock_return_inbound_posted",
        business_type="stock_operation_return_inbound",
        business_id=row.id,
        payload={**body, "target_custody_assignment_id": str(row.target_custody_assignment_id)},
        recipient_person_id=assignment.custodian_person_id if assignment is not None else None,
        occurred_at=now,
        now=now,
    )
    db.flush()
    return _result(row)


__all__ = ["execute_return_inbound", "preview_return_inbound"]
