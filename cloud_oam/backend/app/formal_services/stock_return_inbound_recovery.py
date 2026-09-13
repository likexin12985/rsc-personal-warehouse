"""Read or permanently seal an uncertain return-inbound request."""

from __future__ import annotations

from datetime import datetime, timezone
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..foundation_models import AuditEvent
from ..stock_operation_models import (
    StockOperationCommandSeal,
    StockOperationOrder,
    StockOperationReceipt,
    StockOperationReturnInbound,
    StockOperationShipment,
)
from . import inventory_posting as posting
from .audit_chain import append_audit_event, lock_audit_chain_head
from .stock_return_inbound_commands import _result
from .work_order_return_sources import _fail


def _coordinate(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", value):
        _fail("stock_return_inbound_lookup_invalid", "原入账请求标识无效", 422)
    return value


def _sealed(row: StockOperationCommandSeal, *, receipt_id: uuid.UUID) -> dict:
    return {
        "schema_version": "1.0",
        "lookup_status": "sealed",
        "seal_id": row.id,
        "receipt_id": receipt_id,
        "shipment_id": row.shipment_id,
        "request_id": row.request_id,
        "request_hash": row.request_hash,
        "sealed_at": row.created_at,
    }


def lookup_return_inbound_request(
    db: Session,
    *,
    actor,
    receipt_id: uuid.UUID,
    request_id: str,
) -> dict | None:
    request_id = _coordinate(request_id)
    current = posting._require_current_actor(db, actor)
    receipt = db.get(StockOperationReceipt, receipt_id, populate_existing=True)
    if receipt is None or receipt.operator_person_id != current.person_id:
        _fail("stock_return_inbound_receipt_not_found", "退回验收事实不存在或不属于当前接收人", 404)
    row = db.scalar(select(StockOperationReturnInbound).where(
        StockOperationReturnInbound.receipt_id == receipt_id,
        StockOperationReturnInbound.actor_user_id == current.user_id,
        StockOperationReturnInbound.request_id == request_id,
    ).execution_options(populate_existing=True))
    if row is not None:
        return _result(row)
    if db.scalar(select(AuditEvent.id).where(
        AuditEvent.stream_key == "inventory",
        AuditEvent.actor_user_id == current.user_id,
        AuditEvent.request_id == posting._request_reference(request_id),
    ).limit(1)):
        _fail("stock_return_inbound_recovery_unavailable", "原库存过账已出现但入账事实未完成，请保留原请求人工核验", 503)
    seal = db.scalar(select(StockOperationCommandSeal).where(
        StockOperationCommandSeal.actor_user_id == current.user_id,
        StockOperationCommandSeal.request_id == request_id,
        StockOperationCommandSeal.operation_type == "receive_return",
    ).execution_options(populate_existing=True))
    if seal is not None:
        if seal.shipment_id != receipt.shipment_id:
            _fail("stock_return_inbound_request_conflict", "原请求已绑定其他退回包裹")
        return _sealed(seal, receipt_id=receipt_id)
    return None


def seal_return_inbound_request(
    db: Session,
    *,
    actor,
    receipt_id: uuid.UUID,
    request_id: str,
    request_hash: str,
) -> dict:
    request_id = _coordinate(request_id)
    if not isinstance(request_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", request_hash):
        _fail("stock_return_inbound_seal_invalid", "原入账请求摘要无效", 422)
    current = posting._require_current_actor(db, actor)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    receipt = db.scalar(select(StockOperationReceipt).where(
        StockOperationReceipt.id == receipt_id,
    ).with_for_update().execution_options(populate_existing=True))
    if receipt is None or receipt.operator_person_id != current.person_id:
        _fail("stock_return_inbound_receipt_not_found", "退回验收事实不存在或不属于当前接收人", 404)
    previous = lookup_return_inbound_request(
        db, actor=current, receipt_id=receipt_id, request_id=request_id,
    )
    if previous is not None:
        bound_hash = previous.get("request_hash") if previous.get("lookup_status") == "sealed" else previous.get("plan_hash")
        if bound_hash != request_hash:
            _fail("stock_return_inbound_seal_conflict", "原请求已绑定其他内容，请保留恢复记录核验")
        return previous
    shipment = db.get(StockOperationShipment, receipt.shipment_id, populate_existing=True)
    order = db.get(StockOperationOrder, shipment.operation_id, populate_existing=True) if shipment else None
    if shipment is None or order is None:
        _fail("stock_return_inbound_recovery_unavailable", "原退回包裹履约关系暂时无法核验", 503)
    lock_audit_chain_head(db, stream_key="material_request")
    now = datetime.now(timezone.utc)
    row = StockOperationCommandSeal(
        id=uuid.uuid4(), operation_type="receive_return", shipment_id=receipt.shipment_id,
        operation_id=shipment.operation_id, oam_work_order_id=order.oam_work_order_id,
        actor_user_id=current.user_id, operator_person_id=current.person_id,
        authorization_version=current.authorization_version, request_id=request_id,
        request_reference=posting._request_reference(request_id), request_hash=request_hash,
        created_at=now,
    )
    db.add(row)
    body = _sealed(row, receipt_id=receipt_id)
    append_audit_event(
        db, stream_key="material_request", actor_user_id=current.user_id,
        action="stock_return_inbound.command_sealed", aggregate_type="stock_operation_command_seal",
        aggregate_id=str(row.id), before_jsonb={}, after_jsonb=body,
        request_id=f"stock-return-inbound-seal:{row.id}", occurred_at=now, created_at=now,
    )
    db.flush()
    return body


__all__ = ["lookup_return_inbound_request", "seal_return_inbound_request"]
