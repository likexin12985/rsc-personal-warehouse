"""Read or permanently seal one exact acceptance's uncertain inbound request."""
from datetime import datetime, timezone
import re
import uuid

from sqlalchemy import select

from ..formal_access import lock_formal_principal_graph
from ..foundation_models import AuditEvent, StateTransitionEvent
from ..stock_operation_models import (StockOperationCommandSeal, StockOperationOrder,
    StockOperationCancellation, StockOperationOutbound, StockOperationReceipt,
    StockOperationReturnInbound, StockOperationReturnInboundSeal, StockOperationShipment)
from . import inventory_posting as posting, inventory_query as inventory
from . import stock_return_facts as facts, stock_return_receipt_facts as receipts
from .audit_chain import append_audit_event, lock_audit_chain_head, AuditChainError
from .stock_return_inbound_facts import inbound_result
from .stock_return_inbound_plan import authorize_receipt
from .work_order_evidence_snapshot import material_audit_cursor
from .work_order_query import _aware
from .work_order_return_sources import _fail


def _coordinate(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", value):
        _fail("stock_return_inbound_lookup_invalid", "原入账请求标识无效", 422)
    return value


def invalid():
    _fail("stock_return_inbound_recovery_unavailable", "原入账请求、封存或审计证据不一致，请保留原请求核验", 503)


def seal_payload(row):
    return dict(receipt_id=str(row.receipt_id), shipment_id=str(row.shipment_id),
        operator_person_id=str(row.operator_person_id), authorization_version=row.authorization_version,
        request_id=row.request_id, request_hash=row.request_hash)


def _sealed(db, *, actor, receipt, row):
    try:
        if (row.receipt_id != receipt.id or row.shipment_id != receipt.shipment_id
                or row.actor_user_id != actor.user_id or row.operator_person_id != actor.person_id
                or row.authorization_version < 1 or row.request_reference != posting._request_reference(row.request_id)
                or not re.fullmatch(r"[a-f0-9]{64}", row.request_hash)
                or not _aware(receipt.created_at) <= _aware(row.created_at) <= datetime.now(timezone.utc)):
            invalid()
        facts.audit(db, actor=actor, stream="material_request",
            aggregate_type="stock_operation_return_inbound_seal", identifier=row.id,
            action="stock_return_inbound.command_sealed", request_id=f"stock-return-inbound-seal:{row.id}",
            before={}, after=seal_payload(row))
        event = facts.single(db, AuditEvent, stream_key="material_request",
            aggregate_type="stock_operation_return_inbound_seal", aggregate_id=str(row.id))
        if _aware(event.occurred_at) != _aware(row.created_at) or _aware(event.created_at) != _aware(row.created_at):
            invalid()
        return {"schema_version": "1.0", "lookup_status": "sealed", "seal": {
            "seal_id": row.id, "receipt_id": row.receipt_id, "shipment_id": row.shipment_id,
            "request_id": row.request_id, "request_hash": row.request_hash, "sealed_at": _aware(row.created_at)}}
    except (AuditChainError, TypeError, ValueError, AttributeError):
        invalid()


def _other_request(db, actor, request_id):
    # Old receive_return seals describe parcel acceptance, never an inbound.
    for model in (StockOperationOrder, StockOperationCancellation, StockOperationOutbound,
            StockOperationShipment, StockOperationReceipt, StockOperationCommandSeal):
        if db.scalar(select(model.id).where(model.actor_user_id == actor.user_id, model.request_id == request_id).limit(1)):
            _fail("stock_return_inbound_request_conflict", "原请求已绑定其他退回操作，请核验准确坐标")
    if db.scalar(select(AuditEvent.id).where(AuditEvent.stream_key == "material_request",
            AuditEvent.actor_user_id == actor.user_id, AuditEvent.aggregate_type == "stock_operation_command_seal",
            AuditEvent.after_jsonb["request_id"].as_string() == request_id).limit(1)):
        # A retained acceptance-seal audit with a missing fact is unknown
        # history, never permission to recreate it as an inbound seal.
        invalid()


def lookup_return_inbound_request(db, *, actor, receipt_id, request_id):
    request_id = _coordinate(request_id)
    with db.no_autoflush:
        snapshot = inventory._projection_snapshot(db)
        cursor = material_audit_cursor(db)
        current, receipt = authorize_receipt(db, actor=actor, receipt_id=receipt_id, action="read")
        receipts.receipt_result(db, actor=current, fact=receipt)
        _other_request(db, current, request_id)
        row = db.scalar(select(StockOperationReturnInbound).where(
            StockOperationReturnInbound.actor_user_id == current.user_id,
            StockOperationReturnInbound.request_id == request_id).execution_options(populate_existing=True))
        seal = db.scalar(select(StockOperationReturnInboundSeal).where(
            StockOperationReturnInboundSeal.actor_user_id == current.user_id,
            StockOperationReturnInboundSeal.request_id == request_id).execution_options(populate_existing=True))
        if row is not None and seal is not None: invalid()
        for bound in (row, seal):
            if bound is not None and (bound.receipt_id != receipt_id or bound.shipment_id != receipt.shipment_id):
                _fail("stock_return_inbound_request_conflict", "原请求已绑定其他退回验收，请核验准确坐标")
        observed_seals = tuple(db.scalars(select(AuditEvent.aggregate_id).where(
            AuditEvent.stream_key == "material_request", AuditEvent.actor_user_id == current.user_id,
            AuditEvent.aggregate_type == "stock_operation_return_inbound_seal",
            AuditEvent.after_jsonb["request_id"].as_string() == request_id).limit(2)))
        if observed_seals != ((str(seal.id),) if seal else ()): invalid()
        if row is None:
            reference = posting._request_reference(request_id)
            if (db.scalar(select(AuditEvent.id).where(AuditEvent.actor_user_id == current.user_id,
                    ((AuditEvent.stream_key == "inventory") & (AuditEvent.request_id == reference)) |
                    ((AuditEvent.stream_key == "material_request") & (AuditEvent.request_id == request_id))).limit(1))
                    or db.scalar(select(StateTransitionEvent.id).where(
                        StateTransitionEvent.aggregate_type == "inventory_transaction",
                        StateTransitionEvent.actor_id == current.user_id,
                        StateTransitionEvent.metadata_jsonb["request_reference"].as_string() == reference).limit(1))):
                invalid()
        result = inbound_result(db, actor=current, fact=row) if row is not None else _sealed(db, actor=current, receipt=receipt, row=seal) if seal else None
        final_actor, _ = authorize_receipt(db, actor=current, receipt_id=receipt_id, action="read")
        if final_actor != current or material_audit_cursor(db) != cursor:
            _fail("stock_return_inbound_lookup_changed", "入账记录或权限在回查期间变化，请查询原请求")
        inventory._ensure_projection_snapshot_current(db, snapshot)
        return result


def seal_return_inbound_request(db, *, actor, receipt_id, request_id, request_hash):
    request_id = _coordinate(request_id)
    if not isinstance(request_hash, str) or not re.fullmatch(r"[a-f0-9]{64}", request_hash):
        _fail("stock_return_inbound_seal_invalid", "原入账请求摘要无效", 422)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    current, receipt = authorize_receipt(db, actor=actor, receipt_id=receipt_id)
    previous = lookup_return_inbound_request(db, actor=current, receipt_id=receipt_id, request_id=request_id)
    if previous is not None:
        if previous.get("seal", previous)["request_hash"] != request_hash:
            _fail("stock_return_inbound_seal_conflict", "原请求已绑定其他内容，请保留恢复记录核验")
        return previous
    lock_audit_chain_head(db, stream_key="material_request")
    now = datetime.now(timezone.utc)
    row = StockOperationReturnInboundSeal(id=uuid.uuid4(), receipt_id=receipt_id, shipment_id=receipt.shipment_id,
        actor_user_id=current.user_id, operator_person_id=current.person_id,
        authorization_version=current.authorization_version, request_id=request_id,
        request_reference=posting._request_reference(request_id), request_hash=request_hash, created_at=now)
    db.add(row)
    append_audit_event(db, stream_key="material_request", actor_user_id=current.user_id,
        action="stock_return_inbound.command_sealed", aggregate_type="stock_operation_return_inbound_seal",
        aggregate_id=str(row.id), before_jsonb={}, after_jsonb=seal_payload(row),
        request_id=f"stock-return-inbound-seal:{row.id}", occurred_at=now, created_at=now)
    db.flush()
    authorize_receipt(db, actor=current, receipt_id=receipt_id)
    return lookup_return_inbound_request(db, actor=current, receipt_id=receipt_id, request_id=request_id)


__all__ = ["lookup_return_inbound_request", "seal_return_inbound_request"]
