"""Append acceptance facts atomically; inventory posting is a separate command."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from ..formal_access import lock_formal_principal_graph
from ..foundation_models import OutboxEvent, StateTransitionEvent
from ..inventory_models import Receipt
from ..stock_operation_models import (StockOperationReceipt, StockOperationReceiptLine,
    StockOperationReceiptSerial, StockOperationReceiptException, StockOperationShipmentLine,
    StockOperationOutboundLine)
from ..stock_return_receipt_schemas import StockReturnReceiptSubmitIn
from . import inventory_posting as posting, stock_return_commands as returns
from . import stock_return_receipt_plan as planning, stock_return_receipt_facts as facts
from .audit_chain import append_audit_event, lock_audit_chain_head
from .formal_files import _lock_file
from .postgresql_lock_graph import (lock_material_request_work_order, lock_inventory_serial_graph,
    lock_inventory_reference_graph)
from .work_order_return_sources import _hash, _fail


def _record(db, actor, fact, header, package):
    kind = 'stock_return_received'; aggregate = 'stock_operation_receipt'
    body = facts.payload(fact, header, package); now = fact.created_at
    event = append_audit_event(db, stream_key='material_request', actor_user_id=actor.user_id, action=kind,
        aggregate_type=aggregate, aggregate_id=str(fact.id), before_jsonb={}, after_jsonb=body,
        request_id=fact.request_id, occurred_at=now, created_at=now)
    if event.stream_version != fact.audit_version: facts.invalid()
    db.add(OutboxEvent(event_type=kind, aggregate_type=aggregate, aggregate_id=str(fact.id), payload_jsonb=body,
        idempotency_key=kind + ':' + str(fact.id), available_at=now, created_at=now, updated_at=now))
    db.add(StateTransitionEvent(aggregate_type=aggregate, aggregate_id=str(fact.id), from_status=None,
        to_status=header.status, actor_id=actor.user_id, reason=kind, idempotency_key=kind + ':' + str(fact.id),
        occurred_at=now, metadata_jsonb=body, created_at=now))
    db.flush()


def _lock_references(db, package, request):
    accounts = tuple(db.scalars(select(StockOperationOutboundLine.transit_stock_account_id)
        .join(StockOperationShipmentLine, StockOperationShipmentLine.outbound_line_id == StockOperationOutboundLine.id)
        .where(StockOperationShipmentLine.shipment_id == package.shipment_id)))
    lock_inventory_reference_graph(db, accounts, datetime.now(timezone.utc))
    serials = {sn.serial_id for row in package.lines for sn in row.serials}
    lock_inventory_serial_graph(db, tuple(serials))
    for identifier in sorted({item.evidence_file_id for line in request.lines for item in line.exceptions}, key=str):
        _lock_file(db, identifier)


def execute_receipt(db, *, actor, shipment_id, request):
    request = StockReturnReceiptSubmitIn.model_validate(request.model_dump())
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    current, detail = planning.authorize(db, actor, shipment_id)
    if request.operator_person_id != current.person_id: _fail('operator_mismatch', '验收人必须是当前登录人员', 403)
    key = posting._storage_hash(posting._require_idempotency_key(request.idempotency_key))
    posting._require_request_id(request.request_id); value = planning.intent(shipment_id, request)
    previous = db.scalar(select(Receipt).where(Receipt.idempotency_key_hash == key).execution_options(populate_existing=True))
    if previous is not None:
        fact = db.get(StockOperationReceipt, previous.id, populate_existing=True)
        if fact is None or fact.shipment_id != shipment_id or fact.actor_user_id != current.user_id:
            _fail('stock_return_receipt_not_found', '本人原验收记录不存在', 404)
        if fact.command_jsonb != value or fact.request_id != request.request_id or fact.plan_hash != request.expected_plan_hash:
            _fail('idempotency_conflict', '原验收请求键已绑定其他内容，请回读原请求')
        return facts.receipt_result(db, actor=current, fact=fact)
    returns._fresh_request(db, actor=current, key=key, request_id=request.request_id)
    lock_material_request_work_order(db, detail.package.work_order_id)
    _lock_references(db, detail.package, request)
    lock_audit_chain_head(db, stream_key='material_request')
    checked, plan = planning.preview_receipt(db, actor=current, shipment_id=shipment_id, request=request)
    if checked.plan_hash != request.expected_plan_hash:
        _fail('stock_return_receipt_plan_changed', '验收方案已变化，请重新核验整组明细')
    now = datetime.now(timezone.utc); identifier = uuid4()
    header = Receipt(id=identifier, receipt_no='RET-RCV-' + key[:24].upper(), shipment_id=shipment_id,
        status='exception' if any(line.exceptions for line in request.lines) else 'accepted', received_at=request.received_at,
        receiver_person_id=current.person_id, request_hash=_hash(value), idempotency_key_hash=key, created_at=now)
    fact = StockOperationReceipt(id=identifier, shipment_id=shipment_id, actor_user_id=current.user_id,
        operator_person_id=current.person_id, authorization_version=current.authorization_version,
        target_custody_assignment_id=checked.package.custody_assignment_id, request_id=request.request_id,
        reason=request.reason, plan_hash=checked.plan_hash, audit_version=plan['audit_cursor'] + 1,
        command_jsonb=value, plan_jsonb=plan, created_at=now)
    db.add(header); db.flush(); db.add(fact); db.flush()
    for number, chosen in enumerate(sorted(request.lines, key=lambda row: str(row.shipment_line_id)), 1):
        line = StockOperationReceiptLine(id=uuid4(), receipt_id=identifier, shipment_line_id=chosen.shipment_line_id,
            line_no=number, **{key: getattr(chosen, key) for key in ('accepted_qty', 'rejected_qty', 'damaged_qty', 'shortage_qty')}, created_at=now)
        db.add(line); db.flush()
        for ids, outcome in ((tuple(proof.serial_id for proof in chosen.accepted_serial_verifications), 'accepted'),
                (chosen.rejected_serial_ids, 'rejected'), (chosen.shortage_serial_ids, 'shortage')):
            db.add_all(StockOperationReceiptSerial(id=uuid4(), line_id=line.id, shipment_line_id=line.shipment_line_id,
                serial_id=serial_id, result=outcome, damaged=serial_id in chosen.damaged_serial_ids,
                sku_verified=outcome == 'accepted', qr_verified=outcome == 'accepted', created_at=now)
                for serial_id in sorted(ids, key=str))
        db.add_all(StockOperationReceiptException(id=uuid4(), line_id=line.id, receipt_id=identifier,
            **item.model_dump(), created_at=now) for item in chosen.exceptions)
    db.flush(); _record(db, current, fact, header, checked.package)
    return facts.receipt_result(db, actor=current, fact=fact)
