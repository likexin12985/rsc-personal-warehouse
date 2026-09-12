"""Append a complete work-order compensation through the unified stock writer.

The caller owns rollback/commit. This service is not exposed as a public write
route until PostgreSQL, lost-result recovery and sealing are accepted together.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import select

from ..demand_models import WorkOrderReversal, WorkOrderReversalItem, WorkOrderMaterialOperation, WorkOrderMaterialLine, WorkOrderMaterialSerial
from ..foundation_models import OutboxEvent
from ..inventory_models import InventoryTransaction, StockAccount
from ..work_order_reversal_schemas import WorkOrderReversalPreviewIn
from . import inventory_posting as posting, work_order_material as material
from .audit_chain import append_audit_event
from .work_order_reversal_plan import _hash, _preview_with_document
from .work_order_reversal_proof import child_digest, inverse_movements


def child_document(parent, original, movements):
    return {"operation_type": "reverse", "work_order_id": str(parent.oam_work_order_id),
        "operator_person_id": str(parent.operator_person_id), "reversal_id": str(parent.id),
        "original_operation_id": str(original.id), "original_transaction_id": str(original.posting_transaction_id),
        "plan_hash": parent.plan_hash, "movements": [dict(line_no=index,
            from_account_id=str(row.from_account_id) if row.from_account_id else None,
            to_account_id=str(row.to_account_id) if row.to_account_id else None,
            quantity=format(row.quantity, ".3f"), external_boundary_code=row.external_boundary_code,
            serial_ids=[str(identifier) for identifier in row.serial_ids]) for index, row in enumerate(movements, 1)]}


def parent_payload(parent, items):
    return {"work_order_id": str(parent.oam_work_order_id), "request_hash": parent.request_hash,
        "plan_hash": parent.plan_hash, "command": parent.command_jsonb,
        "items": [{"ordinal": item.ordinal, "original_operation_id": str(item.original_operation_id),
            "inverse_operation_id": str(item.inverse_operation_id)} for item in items]}


def _record_child(db, *, actor, parent, item, original, command, transaction):
    posted_at = transaction.created_at if transaction.created_at.tzinfo else transaction.created_at.replace(tzinfo=timezone.utc)
    digest = child_digest(parent.idempotency_key_hash, original.id)
    value = child_document(parent, original, command.movements)
    operation = WorkOrderMaterialOperation(id=item.inverse_operation_id,
        operation_no=f"WOM-{parent.oam_work_order_id.hex[:12].upper()}-{digest[:12].upper()}",
        oam_work_order_id=parent.oam_work_order_id, operator_person_id=actor.person_id,
        operation_type="reverse", status="posted", posting_transaction_id=transaction.id,
        idempotency_key_hash=digest, request_hash=_hash(value), created_at=transaction.created_at)
    db.add(operation); db.flush()
    for index, movement in enumerate(command.movements, 1):
        account = db.get(StockAccount, movement.from_account_id or movement.to_account_id)
        line = WorkOrderMaterialLine(id=uuid4(), operation_id=operation.id, line_no=index,
            material_id=account.material_id, stock_account_id=account.id, quantity=movement.quantity,
            condition_before=account.condition_code, condition_after=None, created_at=transaction.created_at)
        db.add(line); db.flush()
        db.add_all(WorkOrderMaterialSerial(operation_line_id=line.id, serial_id=identifier,
            sku_verified=True, qr_verified=True, created_at=transaction.created_at) for identifier in movement.serial_ids)
    append_audit_event(db, stream_key="material_request", actor_user_id=actor.user_id,
        action="work_order_material.reverse", aggregate_type="work_order_material_operation", aggregate_id=str(operation.id),
        before_jsonb={}, after_jsonb={"work_order_id": str(parent.oam_work_order_id), "operation_type": "reverse",
            "posting_transaction_id": str(transaction.id), "line_count": len(command.movements),
            "request_hash": operation.request_hash, "command": value},
        request_id="work-order-material:" + digest, occurred_at=posted_at, created_at=posted_at)
    db.add(OutboxEvent(event_type="work_order_material_operation_posted", aggregate_type="work_order_material_operation",
        aggregate_id=str(operation.id), payload_jsonb={"work_order_id": str(parent.oam_work_order_id),
            "operation_type": "reverse", "operation_no": operation.operation_no, "posting_transaction_id": str(transaction.id)},
        idempotency_key="work-order-material-operation:" + str(operation.id), available_at=transaction.created_at))
    db.flush()


def execute_reversal(db, *, actor, work_order_id, request):
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    _, current = material.authorize_work_order(db, actor=actor, work_order_id=work_order_id, action="operate", lock_rows=True)
    if request.operator_person_id != current.person_id:
        raise material.WorkOrderMaterialPreflightError("operator_mismatch", "操作人必须是当前登录人员", "forbidden")
    key = posting._storage_hash(posting._require_idempotency_key(request.idempotency_key))
    posting._require_request_id(request.request_id)
    selection = WorkOrderReversalPreviewIn.model_validate(request.model_dump(exclude={"idempotency_key", "request_id", "expected_plan_hash"}))
    intent = {"work_order_id": str(work_order_id), **selection.model_dump(mode="json")}
    existing = db.scalar(select(WorkOrderReversal).where(WorkOrderReversal.idempotency_key_hash == key))
    from .work_order_reversal_read import reversal_result
    if existing is not None:
        if existing.actor_user_id != current.user_id or existing.oam_work_order_id != work_order_id:
            raise material.WorkOrderMaterialPreflightError("work_order_reversal_not_found", "原冲销记录不存在", "not_found")
        if (existing.command_jsonb != intent or existing.request_id != request.request_id
                or existing.plan_hash != request.expected_plan_hash):
            raise material.WorkOrderMaterialPreflightError("idempotency_conflict", "原请求键已绑定其他冲销内容", "conflict")
        return reversal_result(db, actor=current, parent=existing)
    if db.scalar(select(WorkOrderReversal.id).where(WorkOrderReversal.actor_user_id == current.user_id,
            WorkOrderReversal.request_id == request.request_id)) is not None:
        raise material.WorkOrderMaterialPreflightError("request_id_conflict", "请求标识已绑定原冲销，请先回读", "conflict")
    from .work_order_command_seal import require_unsealed_request
    require_unsealed_request(db, actor=current, work_order_id=work_order_id, operation_type="reverse", request_id=request.request_id)
    plan, _ = _preview_with_document(db, actor=current, work_order_id=work_order_id, request=selection)
    originals = [db.get(WorkOrderMaterialOperation, row.original_operation_id) for row in plan.children]
    graph = posting.InventoryPostingCommand(transaction_no="reversal-graph", movement_type="reversal",
        source_document_type="work_order_material", source_document_id=str(work_order_id), posting_key="reversal-graph",
        effective_at=datetime.now(timezone.utc), movements=tuple(m for original in originals for m in inverse_movements(db, original.posting_transaction_id)))
    posting._plan_and_lock_terminal_opening_graphs(db, command=graph, current_actor_user_id=current.user_id)
    plan, document = _preview_with_document(db, actor=current, work_order_id=work_order_id, request=selection)
    if plan.plan_hash != request.expected_plan_hash:
        raise material.WorkOrderMaterialPreflightError("work_order_reversal_plan_changed", "反向方案已变化，请重新预检并确认", "conflict")
    now = datetime.now(timezone.utc)
    parent = WorkOrderReversal(id=uuid4(), reversal_no="WOV-" + key[:24].upper(),
        oam_work_order_id=work_order_id, actor_user_id=current.user_id, operator_person_id=current.person_id,
        authorization_version=current.authorization_version, original_operation_id=selection.original_operation_id,
        original_replacement_id=selection.original_replacement_id, reason=selection.reason,
        request_id=request.request_id, idempotency_key_hash=key, request_hash=plan.request_hash,
        plan_hash=plan.plan_hash, command_jsonb=intent, plan_jsonb=document, created_at=now)
    db.add(parent); db.flush()
    items = [WorkOrderReversalItem(id=uuid4(), reversal_id=parent.id, ordinal=index,
        original_operation_id=original.id, inverse_operation_id=uuid4(), created_at=now)
        for index, original in enumerate(originals, 1)]
    db.add_all(items); db.flush()
    for item, original in zip(items, originals):
        digest = child_digest(key, original.id)
        command = posting.InventoryPostingCommand(transaction_no="INV-WO-REVERSE-" + digest[:20].upper(),
            movement_type="reversal", source_document_type="work_order_material", source_document_id=str(work_order_id),
            posting_key=f"work-order-material:reverse:{work_order_id}:{digest}", effective_at=now,
            movements=inverse_movements(db, original.posting_transaction_id))
        posting._require_unused_business_keys(db, command)
        result = posting._post_new_transaction(db, actor=current, command=command,
            idempotency_key_hash=digest, request_hash=posting._posting_request_hash(current, command),
            request_reference=posting._request_reference(request.request_id), permission_resource="work_order_material",
            permission_action="operate", reversed_transaction_id=original.posting_transaction_id, event_suffix="reversed")
        transaction = db.get(InventoryTransaction, result.result.transaction_id)
        _record_child(db, actor=current, parent=parent, item=item, original=original, command=command, transaction=transaction)
    payload = parent_payload(parent, items)
    append_audit_event(db, stream_key="material_request", actor_user_id=current.user_id,
        action="work_order_material.reversal", aggregate_type="work_order_material_reversal", aggregate_id=str(parent.id),
        before_jsonb={}, after_jsonb=payload, request_id=request.request_id, occurred_at=now, created_at=now)
    db.add(OutboxEvent(event_type="work_order_material_reversal_posted", aggregate_type="work_order_material_reversal",
        aggregate_id=str(parent.id), payload_jsonb=payload, idempotency_key="work-order-material-reversal:" + str(parent.id), available_at=now))
    db.flush()
    return reversal_result(db, actor=current, parent=parent)
