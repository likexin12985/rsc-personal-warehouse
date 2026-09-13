"""Atomic formal return reservation and full pre-dispatch cancellation."""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from ..foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from ..inventory_models import InventoryTransaction, StockAccount
from ..stock_operation_models import StockOperationOrder as Order, StockOperationLine as Line, StockOperationSerial as Serial, StockOperationCancellation as Cancellation
from ..stock_return_schemas import StockReturnSubmitIn, StockReturnCancelIn
from . import inventory_posting as posting, work_order_material as material
from .audit_chain import append_audit_event
from . import stock_return_facts as facts
from .stock_return_plan import authorize, intent, preview_return
from .work_order_return_sources import _hash, _fail


def _fresh_request(db, *, actor, key, request_id):
    from .stock_return_recovery import require_unsealed
    require_unsealed(db, actor=actor, request_id=request_id)
    for model in (Order, Cancellation):
        if db.scalar(select(model.id).where(model.actor_user_id == actor.user_id, model.request_id == request_id)):
            _fail("stock_return_request_conflict", "请求标识已有退回操作，请先回读原请求")
    if (db.scalar(select(InventoryTransaction.id).where(InventoryTransaction.idempotency_key_hash == key))
            or db.scalar(select(AuditEvent.id).where(AuditEvent.stream_key == "material_request",
                AuditEvent.actor_user_id == actor.user_id, AuditEvent.request_id == request_id,
                AuditEvent.action.in_(("stock_return_submitted", "stock_return_cancelled"))))):
        facts.invalid()


def _record(db, *, actor, order, fact, cancel=False):
    kind = "stock_return_cancelled" if cancel else "stock_return_submitted"
    aggregate = "stock_operation_cancellation" if cancel else "stock_operation_order"
    body = facts.payload(order, fact.posting_transaction_id, fact.request_hash, cancellation=fact if cancel else None)
    now = fact.created_at
    append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id, action=kind,
        aggregate_type=aggregate, aggregate_id=str(fact.id), before_jsonb={}, after_jsonb=body,
        request_id=fact.request_id, occurred_at=now, created_at=now)
    db.add(OutboxEvent(event_type=kind, aggregate_type=aggregate, aggregate_id=str(fact.id), payload_jsonb=body,
        idempotency_key=kind + ":" + str(fact.id), available_at=now, created_at=now, updated_at=now))
    db.add(StateTransitionEvent(aggregate_type=aggregate, aggregate_id=str(fact.id), from_status=None,
        to_status="cancelled" if cancel else "submitted", actor_id=actor.user_id, reason=kind,
        idempotency_key=kind + ":" + str(fact.id), occurred_at=now, metadata_jsonb=body, created_at=now))
    db.flush()


def _reserved_accounts(db, lines, at):
    result = {}
    for line in lines:
        if line.source.stock_account_id in result: continue
        source = db.get(StockAccount, line.source.stock_account_id, populate_existing=True)
        dimensions = {name: getattr(source, name) for name in (
            "owner_org_id", "custodian_person_id", "location_id", "material_id", "condition_code", "lot_id")}
        account = db.scalar(select(StockAccount).filter_by(**dimensions, availability_bucket="return_pending"))
        if account is None:
            account = StockAccount(id=uuid4(), **dimensions, availability_bucket="return_pending", created_at=at, updated_at=at)
            db.add(account); db.flush()
        result[source.id] = account
    return result


def submit_return(db, *, actor, work_order_id, request):
    request = StockReturnSubmitIn.model_validate(request.model_dump())
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    current = authorize(db, actor, "submit_return")
    if request.operator_person_id != current.person_id: _fail("operator_mismatch", "操作人必须是当前登录人员", 403)
    key = posting._storage_hash(posting._require_idempotency_key(request.idempotency_key))
    posting._require_request_id(request.request_id)
    value = intent(work_order_id, request)
    existing = db.scalar(select(Order).where(Order.idempotency_key_hash == key))
    if existing is not None:
        if existing.actor_user_id != current.user_id or existing.requester_id != current.person_id:
            _fail("stock_return_not_found", "本人原退回单不存在", 404)
        if existing.command_jsonb != value or existing.request_id != request.request_id or existing.plan_hash != request.expected_plan_hash:
            _fail("idempotency_conflict", "原请求键已绑定其他退回内容，请回读原单")
        return facts.order_result(db, actor=current, order=existing)
    _fresh_request(db, actor=current, key=key, request_id=request.request_id)
    # A closed work order may still be returned. This locks its source and
    # principal while requiring independent return-write permission below.
    _, current = material.authorize_work_order(db, actor=current, work_order_id=work_order_id, action="read", lock_rows=True)
    prepared, _ = preview_return(db, actor=current, work_order_id=work_order_id, request=request)
    source_graph = posting.InventoryPostingCommand(transaction_no="return-graph", movement_type="reserve",
        source_document_type="stock_operation_return", source_document_id=str(work_order_id), posting_key="return-graph",
        effective_at=datetime.now(timezone.utc), movements=tuple(posting.InventoryMovementCommand(
            from_account_id=row.source.stock_account_id, to_account_id=None, quantity=next(
                line.quantity for line in request.lines if line.source_recovery_line_id == row.source.source_recovery_line_id),
            serial_ids=tuple(sn.serial_id for sn in row.selected_serials)) for row in prepared.lines))
    posting._plan_and_lock_terminal_opening_graphs(db, command=source_graph, current_actor_user_id=current.user_id)
    prepared, plan = preview_return(db, actor=current, work_order_id=work_order_id, request=request)
    if prepared.plan_hash != request.expected_plan_hash:
        _fail("stock_return_plan_changed", "退回方案已变化，请重新预检并确认")
    now = datetime.now(timezone.utc)
    targets = _reserved_accounts(db, prepared.lines, now)
    parent = Order(id=uuid4(), operation_no="RET-" + key[:24].upper(), operation_type="return", status="submitted",
        oam_work_order_id=work_order_id, source_location_id=prepared.destination.source_location_id,
        target_location_id=request.target_location_id, transit_location_id=request.transit_location_id,
        target_custody_assignment_id=prepared.destination.custody_assignment_id, requester_id=current.person_id,
        actor_user_id=current.user_id, authorization_version=current.authorization_version, reason=request.reason,
        request_id=request.request_id, idempotency_key_hash=key, request_hash=_hash(value), plan_hash=prepared.plan_hash,
        command_jsonb=value, plan_jsonb=plan, created_at=now)
    lines = []
    for index, selected in enumerate(sorted(request.lines, key=lambda row: str(row.source_recovery_line_id)), 1):
        target = targets[selected.stock_account_id]
        line = Line(id=uuid4(), operation_id=parent.id, line_no=index, source_recovery_line_id=selected.source_recovery_line_id,
            stock_account_id=selected.stock_account_id, reserved_account_id=target.id, material_id=target.material_id,
            quantity=selected.quantity, target_condition=target.condition_code, reason=request.reason, created_at=now)
        lines.append((line, selected))
    moves = tuple(posting.InventoryMovementCommand(from_account_id=line.stock_account_id, to_account_id=line.reserved_account_id,
        quantity=line.quantity, serial_ids=tuple(sorted((proof.serial_id for proof in selected.serial_verifications), key=str)))
        for line, selected in lines)
    command = facts.posting_command(parent, key=key, at=now, movements=moves)
    posted = posting.post_inventory_transaction(db, actor=current, command=command,
        idempotency_key=request.idempotency_key, request_id=request.request_id, permission_resource="stock_operation", permission_action="submit_return")
    parent.posting_transaction_id = posted.transaction_id
    db.add(parent); db.flush()
    for line, selected in lines:
        db.add(line); db.flush()
        db.add_all(Serial(line_id=line.id, serial_id=proof.serial_id, sku_verified=True, qr_verified=True, created_at=now)
            for proof in selected.serial_verifications)
    db.flush(); _record(db, actor=current, order=parent, fact=parent)
    return facts.order_result(db, actor=current, order=parent)


def cancel_return(db, *, actor, operation_id, request, work_order_id=None):
    request = StockReturnCancelIn.model_validate(request.model_dump())
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    current = authorize(db, actor, "cancel_return")
    if request.operator_person_id != current.person_id: _fail("operator_mismatch", "操作人必须是当前登录人员", 403)
    order = db.get(Order, operation_id, populate_existing=True)
    if (order is None or order.actor_user_id != current.user_id or order.requester_id != current.person_id
            or (work_order_id is not None and order.oam_work_order_id != work_order_id)):
        _fail("stock_return_not_found", "本人退回单不存在", 404)
    key = posting._storage_hash(posting._require_idempotency_key(request.idempotency_key))
    posting._require_request_id(request.request_id)
    value = {"operation_id": str(operation_id), "operator_person_id": str(current.person_id), "reason": request.reason}
    existing = db.scalar(select(Cancellation).where(Cancellation.idempotency_key_hash == key))
    if existing:
        if existing.actor_user_id != current.user_id or existing.operation_id != order.id:
            _fail("stock_return_not_found", "本人原取消记录不存在", 404)
        if existing.command_jsonb != value or existing.request_id != request.request_id:
            _fail("idempotency_conflict", "原请求键已绑定其他取消内容，请回读原记录")
        return facts.cancellation_result(db, actor=current, order=order, cancellation=existing)
    _fresh_request(db, actor=current, key=key, request_id=request.request_id)
    facts.order_result(db, actor=current, order=order)
    if db.scalar(select(Cancellation.id).where(Cancellation.operation_id == order.id)):
        _fail("stock_return_already_cancelled", "该退回单已经取消，请回读原取消记录")
    now = datetime.now(timezone.utc)
    command = facts.posting_command(order, key=key, at=now, movements=facts.movements(db, order, cancel=True), cancel=True)
    posted = posting.post_inventory_transaction(db, actor=current, command=command, idempotency_key=request.idempotency_key,
        request_id=request.request_id, permission_resource="stock_operation", permission_action="cancel_return")
    row = Cancellation(id=uuid4(), operation_id=order.id, actor_user_id=current.user_id, operator_person_id=current.person_id,
        authorization_version=current.authorization_version, reason=request.reason, request_id=request.request_id,
        idempotency_key_hash=key, request_hash=_hash(value), command_jsonb=value, posting_transaction_id=posted.transaction_id, created_at=now)
    db.add(row); db.flush(); _record(db, actor=current, order=order, fact=row, cancel=True)
    return facts.cancellation_result(db, actor=current, order=order, cancellation=row)
