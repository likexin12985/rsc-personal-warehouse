"""Append a stock-neutral return parcel and its evidence in one transaction."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select
from ..inventory_models import Shipment
from ..foundation_models import OutboxEvent, StateTransitionEvent
from ..stock_operation_models import StockOperationShipment, StockOperationShipmentLine, StockOperationShipmentSerial
from ..inventory_models import CustodyAssignment
from ..stock_return_shipment_schemas import StockReturnShipmentSubmitIn
from . import inventory_posting as posting, stock_return_commands as returns, stock_return_shipment_facts as facts
from .stock_return_shipment_plan import preview_shipment, intent
from .stock_return_outbound_plan import original
from .stock_return_plan import authorize
from .work_order_return_sources import _hash, _fail
from .audit_chain import append_audit_event, lock_audit_chain_head
from . import work_order_material as material
from .notification_events import record_stock_return_notification


def _record(db, actor, order, header, fact):
    kind = "stock_return_shipped"; aggregate = "stock_operation_shipment"; body = facts.payload(order, header)
    event = append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id, action=kind,
        aggregate_type=aggregate, aggregate_id=str(fact.id), before_jsonb={}, after_jsonb=body,
        request_id=fact.request_id, occurred_at=fact.created_at, created_at=fact.created_at)
    if event.stream_version != fact.audit_version: facts.invalid()
    db.add(OutboxEvent(event_type=kind, aggregate_type=aggregate, aggregate_id=str(fact.id), payload_jsonb=body,
        idempotency_key=kind + ":" + str(fact.id), available_at=fact.created_at, created_at=fact.created_at, updated_at=fact.created_at))
    db.add(StateTransitionEvent(aggregate_type=aggregate, aggregate_id=str(fact.id), from_status=None, to_status="shipped",
        actor_id=actor.user_id, reason=kind, idempotency_key=kind + ":" + str(fact.id), occurred_at=fact.created_at,
        metadata_jsonb=body, created_at=fact.created_at))
    assignment = db.get(CustodyAssignment, fact.target_custody_assignment_id)
    record_stock_return_notification(
        db,
        event_type=kind,
        business_type=aggregate,
        business_id=fact.id,
        payload={**body, "target_custody_assignment_id": str(fact.target_custody_assignment_id)},
        recipient_person_id=assignment.custodian_person_id if assignment is not None else None,
        occurred_at=fact.created_at,
        now=fact.created_at,
    )
    db.flush()


def execute_shipment(db, *, actor, work_order_id, operation_id, request):
    request = StockReturnShipmentSubmitIn.model_validate(request.model_dump())
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    current = authorize(db, actor, "ship_return")
    if request.operator_person_id != current.person_id: _fail("operator_mismatch", "操作人必须是当前登录人员", 403)
    key = posting._storage_hash(posting._require_idempotency_key(request.idempotency_key))
    posting._require_request_id(request.request_id)
    value = intent(operation_id, request)
    previous = db.scalar(select(Shipment).where(Shipment.idempotency_key_hash == key).execution_options(populate_existing=True))
    if previous:
        fact = db.get(StockOperationShipment, previous.id, populate_existing=True)
        if fact is None or fact.actor_user_id != current.user_id or fact.operation_id != operation_id:
            _fail("stock_return_shipment_not_found", "本人原退回运单不存在", 404)
        result = facts.shipment_result(db, actor=current, fact=fact)
        if result.work_order_id != work_order_id: _fail("stock_return_shipment_not_found", "本人原退回运单不存在", 404)
        if fact.command_jsonb != value or fact.request_id != request.request_id or fact.plan_hash != request.expected_plan_hash:
            _fail("idempotency_conflict", "原运单请求键已绑定其他内容，请回读原请求")
        return result
    returns._fresh_request(db, actor=current, key=key, request_id=request.request_id)
    _, current = material.authorize_work_order(db, actor=current, work_order_id=work_order_id, action="read", lock_rows=True)
    order, _ = original(db, current, operation_id, work_order_id)
    # No inventory posting occurs; serialize parcels with the material audit head
    # as well as the existing ledger/work-order lock protocol.
    lock_audit_chain_head(db, stream_key="material_request")
    checked, plan = preview_shipment(db, actor=current, work_order_id=work_order_id, operation_id=operation_id, request=request)
    if checked.plan_hash != request.expected_plan_hash:
        _fail("stock_return_shipment_plan_changed", "分包方案已变化，请重新核验整组明细")
    now = datetime.now(timezone.utc); identifier = uuid4()
    header = Shipment(id=identifier, shipment_no="RET-SHP-" + key[:24].upper(), source_location_id=order.source_location_id,
        target_location_id=order.target_location_id, target_person_id=checked.destination.custodian_person_id,
        carrier=request.carrier, tracking_no=request.tracking_no, status="shipped", shipped_at=request.shipped_at,
        idempotency_key_hash=key, request_hash=_hash(value), actor_user_id=current.user_id,
        actor_person_id=current.person_id, authorization_version=current.authorization_version, created_at=now)
    fact = StockOperationShipment(id=identifier, operation_id=order.id, actor_user_id=current.user_id,
        target_custody_assignment_id=checked.destination.custody_assignment_id, request_id=request.request_id,
        reason=request.reason, plan_hash=checked.plan_hash, audit_version=plan["audit_cursor"] + 1,
        command_jsonb=value, plan_jsonb=plan, created_at=now)
    db.add(header); db.flush(); db.add(fact); db.flush()
    for number, selected in enumerate(sorted(request.lines, key=lambda row: str(row.outbound_line_id)), 1):
        line = StockOperationShipmentLine(id=uuid4(), shipment_id=identifier, line_no=number,
            outbound_line_id=selected.outbound_line_id, quantity=selected.quantity, created_at=now)
        db.add(line); db.flush()
        db.add_all(StockOperationShipmentSerial(id=uuid4(), line_id=line.id, outbound_line_id=line.outbound_line_id,
            serial_id=serial, created_at=now) for serial in sorted(selected.serial_ids, key=str))
    db.flush(); _record(db, current, order, header, fact)
    return facts.shipment_result(db, actor=current, fact=fact)
