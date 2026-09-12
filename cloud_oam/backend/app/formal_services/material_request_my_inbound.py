"""Own-receipt posting and read-only recovery through the common ledger."""
from datetime import datetime, timezone
import hashlib
import hmac
from uuid import UUID

from sqlalchemy import select

from ..demand_models import MaterialRequestCommand
from ..foundation_models import AuditEvent
from ..inventory_models import (InboundOrder, InboundPosting, InventoryMovement,
    InventoryMovementSerial, InventoryTransaction, Receipt)
from ..material_request_my_inbound_schemas import MyInboundIn, MyInboundOut
from . import inventory_posting as inventory
from . import material_request_inbound as inbound
from . import material_request_my_receipt as receipts
from .audit_chain import append_audit_event
from .material_request_fulfillment_command import verify_fulfillment_command
from .material_request_inbound_authority import receipt_inbound_authority
from .material_request_query import MaterialRequestReadError


def _fail(code, category, message):
    raise MaterialRequestReadError(f"my_inbound_{code}", category, message)


def _time(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


def _reference(request_id):
    return f"/api/v1/material-requests/{request_id}/my-inbounds"


def _key(actor, request_id, key, secret):
    # Reuse validation, but use an independent action/route namespace.
    receipts._key(actor, request_id, key, secret)
    secret = secret.encode() if isinstance(secret, str) else secret
    return hmac.new(secret, f"{actor.user_id}:POST:{_reference(request_id)}:{key}".encode(), hashlib.sha256).hexdigest()


def request_hash(request_id, person_id, payload):
    return inventory._canonical_hash({"request_id": str(request_id), "person_id": str(person_id),
        "command": payload.model_dump(mode="json")})


def _audits(db, *, actor, request_id, order_id=None, trace=None):
    statement = select(AuditEvent).where(AuditEvent.stream_key == "material_request",
        AuditEvent.action == "my_inbound_posted", AuditEvent.aggregate_type == "inbound_order",
        AuditEvent.actor_user_id == actor.user_id,
        AuditEvent.after_jsonb["request_id"].as_string() == str(request_id))
    if order_id is not None:
        statement = statement.where(AuditEvent.aggregate_id == str(order_id))
    if trace is not None:
        statement = statement.where(AuditEvent.request_id == receipts._trace(trace))
    return tuple(db.scalars(statement.limit(2)).all())


def create_my_inbound(db, *, actor, request_id, payload, idempotency_key, secret, trace_request_id):
    payload = MyInboundIn.model_validate(payload)
    receipts._trace(trace_request_id)
    key = _key(actor, request_id, idempotency_key, secret)
    inventory._lock_inventory_ledger_head_for_atomic_batch(db)
    context, request = receipts._context(db, actor, request_id, write=True)
    actor = context.principal
    receipt = db.get(Receipt, payload.receipt_id, populate_existing=True)
    if receipt is None:
        _fail("receipt_not_found", "not_found", "本人验收记录不存在")
    accepted = receipts._result(db, context, request, receipt, replayed=True)
    if accepted.request_hash != payload.receipt_request_hash:
        _fail("receipt_changed", "conflict", "验收内容与确认时不一致，请重新核对")
    previous = db.scalar(select(InventoryTransaction).where(
        InventoryTransaction.idempotency_key_hash == inventory._storage_hash(key)))
    if previous is None:
        if _audits(db, actor=actor, request_id=request.id, trace=trace_request_id):
            _fail("trace_reused", "conflict", "原请求已有入账记录，请先核验结果")
        if request.version != payload.expected_request_version:
            _fail("version_conflict", "conflict", "需求版本已变化，请刷新后重新核对")
        if request.status not in {"approved", "partially_approved"}:
            _fail("state_invalid", "precondition_failed", "需求当前状态不能新增入账")
    if not any(line.accepted_qty > 0 for line in accepted.lines):
        _fail("empty", "precondition_failed", "本次验收没有可入账的合格数量")
    order = db.scalar(select(InboundOrder).where(InboundOrder.receipt_id == receipt.id))
    created = order is None
    if created:
        if previous is not None:
            _fail("history_invalid", "service_unavailable", "原入账缺少对应单据，请保留原请求核验")
        shipment, _ = receipts._package(db, context, request, accepted.shipment_id)
        order = inbound._new_inbound_order(db, receipt_id=receipt.id,
            target_location_id=shipment.target_location_id, target_person_id=actor.person_id)
    if previous is not None:
        original = _result(db, context, request, order, replayed=True)
        if (original.inventory_transaction_id != previous.id
                or original.request_hash != request_hash(request.id, actor.person_id, payload)):
            _fail("key_reused", "conflict", "原幂等键已绑定其他入账内容")
    command = inbound._order_posting_command(db, order, create_missing=previous is None)
    authority = receipt_inbound_authority(db, actor=actor, request_id=request.id, order_id=order.id, command=command)
    posted = inbound._post_inbound_order(db, actor=actor, inbound_order_id=order.id,
        material_request_id=request.id, idempotency_key=key, request_id=trace_request_id,
        authority=authority, new_order=created)
    if previous is None:
        now = datetime.now(timezone.utc)
        append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id,
            action="my_inbound_posted", aggregate_type="inbound_order", aggregate_id=str(order.id),
            before_jsonb={}, after_jsonb={"request_id": str(request.id),
                "request_hash": request_hash(request.id, actor.person_id, payload),
                "command": payload.model_dump(mode="json"),
                "inventory_transaction_id": str(posted["inventory_transaction_id"])},
            request_id=trace_request_id, occurred_at=now, created_at=now)
        db.flush()
    return _result(db, context, request, order, replayed=previous is not None)


def _result(db, context, request, order, *, replayed):
    actor = context.principal
    audits = _audits(db, actor=actor, request_id=request.id, order_id=order.id)
    if len(audits) != 1:
        _fail("history_invalid", "service_unavailable", "原入账请求证据不完整，请保留恢复坐标")
    audit = audits[0]
    try:
        payload = MyInboundIn.model_validate(audit.after_jsonb["command"])
    except (ValueError, TypeError, KeyError):
        _fail("history_invalid", "service_unavailable", "原入账请求证据不完整")
    receipt = db.get(Receipt, order.receipt_id)
    if receipt is None or receipt.id != payload.receipt_id or receipt.request_hash != payload.receipt_request_hash:
        _fail("history_invalid", "service_unavailable", "原入账与本人验收不一致")
    accepted = receipts._result(db, context, request, receipt, replayed=True)
    shipment, _ = receipts._package(db, context, request, accepted.shipment_id)
    inbound._validate_inbound_target(shipment, order.target_location_id, order.target_person_id)
    binding = db.scalar(select(InboundPosting).where(InboundPosting.inbound_order_id == order.id))
    transaction = db.get(InventoryTransaction, binding.inventory_transaction_id) if binding else None
    if transaction is None or transaction.status != "posted" or transaction.ledger_cursor <= 0 or transaction.posted_at is None:
        _fail("history_invalid", "service_unavailable", "原入账缺少已完成库存流水")
    digest = request_hash(request.id, actor.person_id, payload)
    if audit.after_jsonb != {"request_id": str(request.id), "request_hash": digest,
            "command": payload.model_dump(mode="json"), "inventory_transaction_id": str(transaction.id)}:
        _fail("history_invalid", "service_unavailable", "原入账请求指纹不一致")
    version_command = verify_fulfillment_command(db, request=request, actor=actor, operation="personal_inbound",
        fact=transaction, request_reference=_reference(request.id), expected_version=payload.expected_request_version + 1)
    expected = inventory._validate_posting_command(inbound._order_posting_command(db, order))
    historic_hash = inventory._canonical_hash({"operation": "post", "actor": {
        "authorization_version": version_command.authorization_version, "person_id": str(actor.person_id),
        "user_id": actor.user_id}, "command": inventory._posting_document(expected)})
    movements = tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == transaction.id)
        .order_by(InventoryMovement.line_no)).all())
    actual = inventory.InventoryPostingCommand(transaction.transaction_no, transaction.movement_type,
        transaction.source_document_type, transaction.source_document_id, transaction.posting_key,
        _time(transaction.effective_at), tuple(inventory.InventoryMovementCommand(row.from_account_id,
            row.to_account_id, row.quantity, tuple(db.scalars(select(InventoryMovementSerial.serial_id).where(
                InventoryMovementSerial.movement_id == row.id, InventoryMovementSerial.transaction_id == transaction.id)
                .order_by(InventoryMovementSerial.serial_id))), row.external_boundary_code) for row in movements))
    if (transaction.actor_user_id != actor.user_id or historic_hash != transaction.request_hash
            or inventory._posting_document(actual) != inventory._posting_document(expected)):
        _fail("history_invalid", "service_unavailable", "原入账流水与本次合格验收不一致")
    return MyInboundOut(request_id=request.id, request_version=version_command.target_version,
        current_request_version=request.version, person_id=actor.person_id, receipt_id=receipt.id,
        receipt_request_hash=receipt.request_hash, shipment_id=receipt.shipment_id, inbound_order_id=order.id,
        inbound_no=order.inbound_no, inventory_transaction_id=transaction.id, posted_at=_time(transaction.posted_at),
        request_hash=digest, idempotency_replayed=replayed)


def my_inbound_command_status(db, *, actor, request_id, idempotency_key, secret):
    with db.no_autoflush:
        context, request = receipts._context(db, actor, request_id)
        key = _key(actor, request_id, idempotency_key, secret)
        transaction = db.scalar(select(InventoryTransaction).where(
            InventoryTransaction.idempotency_key_hash == inventory._storage_hash(key)))
        if transaction is None:
            return None
        binding = db.scalar(select(InboundPosting).where(InboundPosting.inventory_transaction_id == transaction.id))
        order = db.get(InboundOrder, binding.inbound_order_id) if binding else None
        if order is None:
            _fail("history_invalid", "service_unavailable", "原入账缺少对应单据")
        return _result(db, context, request, order, replayed=True)


def my_inbound_trace_status(db, *, actor, request_id, trace_request_id):
    with db.no_autoflush:
        context, request = receipts._context(db, actor, request_id)
        audits = _audits(db, actor=context.principal, request_id=request.id, trace=trace_request_id)
        if not audits:
            return None
        if len(audits) != 1:
            _fail("history_invalid", "service_unavailable", "原请求对应多笔入账，请保留恢复坐标")
        try:
            order_id = UUID(audits[0].aggregate_id)
        except (ValueError, TypeError):
            _fail("history_invalid", "service_unavailable", "原入账审计坐标无效")
        order = db.get(InboundOrder, order_id)
        if order is None:
            _fail("history_invalid", "service_unavailable", "原入账单据不存在")
        return _result(db, context, request, order, replayed=True)
