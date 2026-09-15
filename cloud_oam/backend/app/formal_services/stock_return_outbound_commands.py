"""Atomic physical departure from return_pending to the selected transit location."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import select
from ..inventory_models import StockAccount, CustodyAssignment
from ..foundation_models import StateTransitionEvent, OutboxEvent, AuditEvent
from ..stock_operation_models import StockOperationOutbound, StockOperationOutboundLine, StockOperationOutboundSerial
from ..stock_return_outbound_schemas import StockReturnOutboundSubmitIn
from . import inventory_posting as posting, stock_return_outbound_facts as facts, stock_return_commands as returns
from .stock_return_outbound_plan import preview_outbound, original, intent, transit_dimensions
from .stock_return_plan import authorize
from .work_order_return_sources import _hash, _fail
from .audit_chain import append_audit_event
from .notification_events import record_stock_return_notification


def _record(db, actor, order, fact):
    kind = "stock_return_outbound"; aggregate = "stock_operation_outbound"; body = facts.payload(order, fact)
    append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id, action=kind, aggregate_type=aggregate,
        aggregate_id=str(fact.id), before_jsonb={}, after_jsonb=body, request_id=fact.request_id, occurred_at=fact.created_at, created_at=fact.created_at)
    db.add(OutboxEvent(event_type=kind, aggregate_type=aggregate, aggregate_id=str(fact.id), payload_jsonb=body,
        idempotency_key=kind + ":" + str(fact.id), available_at=fact.created_at, created_at=fact.created_at, updated_at=fact.created_at))
    db.add(StateTransitionEvent(aggregate_type=aggregate, aggregate_id=str(fact.id), from_status=None, to_status="outbound",
        actor_id=actor.user_id, reason=kind, idempotency_key=kind + ":" + str(fact.id), occurred_at=fact.created_at, metadata_jsonb=body, created_at=fact.created_at))
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


def execute_outbound(db, *, actor, work_order_id, operation_id, request):
    request = StockReturnOutboundSubmitIn.model_validate(request.model_dump())
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    current = authorize(db, actor, "outbound_return")
    if request.operator_person_id != current.person_id: _fail("operator_mismatch", "操作人必须是当前登录人员", 403)
    key = posting._storage_hash(posting._require_idempotency_key(request.idempotency_key))
    posting._require_request_id(request.request_id)
    value = intent(operation_id, request)
    previous = db.scalar(select(StockOperationOutbound).where(StockOperationOutbound.idempotency_key_hash == key))
    if previous:
        if previous.actor_user_id != current.user_id or previous.operation_id != operation_id:
            _fail("stock_return_outbound_not_found", "本人原退回发出记录不存在", 404)
        result = facts.outbound_result(db, actor=current, fact=previous)
        if result.work_order_id != work_order_id: _fail("stock_return_outbound_not_found", "本人原退回发出记录不存在", 404)
        if previous.command_jsonb != value or previous.plan_hash != request.expected_plan_hash or previous.request_id != request.request_id:
            _fail("idempotency_conflict", "原发出请求键已绑定其他内容，请回读原请求")
        return result
    returns._fresh_request(db, actor=current, key=key, request_id=request.request_id)
    if (db.scalar(select(StockOperationOutbound.id).where(StockOperationOutbound.actor_user_id == current.user_id, StockOperationOutbound.request_id == request.request_id))
            or db.scalar(select(AuditEvent.id).where(AuditEvent.stream_key == "material_request", AuditEvent.actor_user_id == current.user_id,
                AuditEvent.request_id == request.request_id, AuditEvent.action == "stock_return_outbound"))):
        _fail("stock_return_request_conflict", "原请求已有退回发出记录，请先回读原请求")
    order, _ = original(db, current, operation_id, work_order_id)
    checked, _ = preview_outbound(db, actor=current, work_order_id=work_order_id, operation_id=operation_id, request=request)
    if checked.plan_hash != request.expected_plan_hash: _fail("stock_return_outbound_plan_changed", "发出方案已变化，请重新核验整组明细")
    now = datetime.now(timezone.utc); targets = {}
    for line in checked.lines:
        source = db.get(StockAccount, line.source_stock_account_id, populate_existing=True)
        dimensions = transit_dimensions(source, order)
        target = db.scalar(select(StockAccount).filter_by(**dimensions))
        if target is None:
            target = StockAccount(id=uuid4(), **dimensions, created_at=now, updated_at=now); db.add(target); db.flush()
        targets[source.id] = target
    fact = StockOperationOutbound(id=uuid4(), outbound_no="RET-OUT-" + key[:24].upper(), operation_id=order.id, status="outbound",
        actor_user_id=current.user_id, operator_person_id=current.person_id, authorization_version=current.authorization_version,
        target_custody_assignment_id=checked.destination.custody_assignment_id, reason=request.reason, outbound_at=request.outbound_at,
        request_id=request.request_id, idempotency_key_hash=key, request_hash=_hash(value), plan_hash=checked.plan_hash, command_jsonb=value, created_at=now)
    moves = tuple(posting.InventoryMovementCommand(from_account_id=line.source_stock_account_id,
        to_account_id=targets[line.source_stock_account_id].id, quantity=next(row.quantity for row in request.lines if row.operation_line_id == line.operation_line_id),
        serial_ids=tuple(sn.serial_id for sn in line.selected_serials)) for line in checked.lines)
    command = facts.command(fact, moves)
    posting._plan_and_lock_terminal_opening_graphs(db, command=command, current_actor_user_id=current.user_id)
    checked, plan = preview_outbound(db, actor=current, work_order_id=work_order_id, operation_id=operation_id, request=request)
    if checked.plan_hash != request.expected_plan_hash: _fail("stock_return_outbound_plan_changed", "发出方案已变化，请重新核验整组明细")
    fact.plan_jsonb = plan
    posted = posting.post_inventory_transaction(db, actor=current, command=command, idempotency_key=request.idempotency_key,
        request_id=request.request_id, permission_resource="stock_operation", permission_action="outbound_return")
    fact.posting_transaction_id = posted.transaction_id; db.add(fact); db.flush()
    for number, (view, move) in enumerate(zip(checked.lines, moves), 1):
        line = StockOperationOutboundLine(id=uuid4(), outbound_id=fact.id, line_no=number, operation_line_id=view.operation_line_id,
            source_stock_account_id=move.from_account_id, transit_stock_account_id=move.to_account_id, quantity=move.quantity, created_at=now)
        db.add(line); db.flush()
        db.add_all(StockOperationOutboundSerial(id=uuid4(), line_id=line.id, serial_id=identifier, sku_verified=True, qr_verified=True, created_at=now)
            for identifier in move.serial_ids)
    db.flush(); _record(db, current, order, fact)
    return facts.outbound_result(db, actor=current, fact=fact)
