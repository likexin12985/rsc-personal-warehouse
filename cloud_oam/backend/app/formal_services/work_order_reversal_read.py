"""Verify immutable whole-command compensation, independent of later stock."""
from dataclasses import replace
from datetime import timezone
import re

from sqlalchemy import select

from ..demand_models import WorkOrderReversal, WorkOrderReversalItem, WorkOrderMaterialOperation, WorkOrderMaterialLine, WorkOrderMaterialSerial, WorkOrderReplacement
from ..foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from ..inventory_models import InventoryMovement, InventoryMovementSerial, InventoryTransaction, StockAccount
from ..work_order_reversal_schemas import WorkOrderReversalOut, WorkOrderReversalResultItemOut, WorkOrderReversalChildOut
from . import inventory_posting as posting, work_order_material as material
from .audit_chain import verify_audit_event_in_read_snapshot, AuditChainError
from .work_order_operation_read import verify_operation_history
from .work_order_replacement_read import replacement_result
from .work_order_reversal_plan import _hash
from .work_order_reversal_proof import child_digest, inverse_movements


def _invalid():
    raise material.WorkOrderMaterialPreflightError("work_order_reversal_evidence_invalid",
        "原冲销命令、完整反向流水或审计不一致，请保留原请求核验", "service_unavailable")


def _single(db, model, **values):
    rows = tuple(db.scalars(select(model).filter_by(**values).limit(2)))
    if len(rows) != 1:
        _invalid()
    return rows[0]


def _utc(value):
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _audit(db, *, actor, stream, action, aggregate_type, aggregate_id, request_id, before, after):
    event = _single(db, AuditEvent, stream_key=stream, aggregate_type=aggregate_type, aggregate_id=str(aggregate_id))
    if (event.actor_user_id != actor.user_id or event.action != action or event.request_id != request_id
            or event.before_jsonb != before or event.after_jsonb != after):
        _invalid()
    verify_audit_event_in_read_snapshot(db, stream_key=stream, event_id=event.id)


def _result(db, *, actor, parent):
    from .work_order_reversal_write import child_document, parent_payload
    if (parent.actor_user_id != actor.user_id or parent.operator_person_id != actor.person_id
            or parent.reversal_no != "WOV-" + parent.idempotency_key_hash[:24].upper()
            or not re.fullmatch(r"[0-9a-f]{64}", parent.idempotency_key_hash)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", parent.request_id)
            or parent.authorization_version < 1 or parent.request_hash != _hash(parent.command_jsonb)
            or parent.plan_hash != _hash(parent.plan_jsonb)
            or parent.plan_jsonb["intent"] != parent.command_jsonb
            or parent.plan_jsonb["authorization_version"] != parent.authorization_version):
        _invalid()
    expected_intent = {"work_order_id": str(parent.oam_work_order_id), "operator_person_id": str(parent.operator_person_id),
        "original_operation_id": str(parent.original_operation_id) if parent.original_operation_id else None,
        "original_replacement_id": str(parent.original_replacement_id) if parent.original_replacement_id else None, "reason": parent.reason}
    if parent.command_jsonb != expected_intent or not parent.reason.strip() or len(parent.reason) > 500:
        _invalid()
    pair = db.get(WorkOrderReplacement, parent.original_replacement_id) if parent.original_replacement_id else None
    if (parent.original_operation_id is None) == (parent.original_replacement_id is None):
        _invalid()
    if parent.original_replacement_id:
        if pair is None or pair.oam_work_order_id != parent.oam_work_order_id or pair.operator_person_id != actor.person_id:
            _invalid()
        replacement_result(db, actor=actor, replacement=pair)
    originals = (pair.recover_operation_id, pair.consume_operation_id) if pair else (parent.original_operation_id,)
    items = tuple(db.scalars(select(WorkOrderReversalItem).where(WorkOrderReversalItem.reversal_id == parent.id).order_by(WorkOrderReversalItem.ordinal)))
    if len(items) != len(originals) or len(parent.plan_jsonb["children"]) != len(items):
        _invalid()
    result = []; cursor = parent.plan_jsonb["ledger_cursor"]
    historical_actor = replace(actor, authorization_version=parent.authorization_version)
    for index, (item, original_id, planned) in enumerate(zip(items, originals, parent.plan_jsonb["children"]), 1):
        original = db.get(WorkOrderMaterialOperation, original_id)
        operation = db.get(WorkOrderMaterialOperation, item.inverse_operation_id)
        if (item.ordinal != index or item.original_operation_id != original_id or original is None or operation is None
                or original.oam_work_order_id != parent.oam_work_order_id or original.operator_person_id != actor.person_id
                or original.operation_type == "reverse" or (not pair and original.replacement_id is not None)):
            _invalid()
        if not pair:
            verify_operation_history(db, actor=actor, operation=original)
        transaction = db.get(InventoryTransaction, operation.posting_transaction_id)
        movements = inverse_movements(db, original.posting_transaction_id)
        original_transaction = db.get(InventoryTransaction, original.posting_transaction_id)
        digest = child_digest(parent.idempotency_key_hash, original.id)
        value = child_document(parent, original, movements)
        if (operation.status != "posted" or operation.operation_type != "reverse" or operation.replacement_id is not None
                or operation.oam_work_order_id != parent.oam_work_order_id or operation.operator_person_id != actor.person_id
                or operation.idempotency_key_hash != digest or operation.request_hash != _hash(value)
                or operation.operation_no != f"WOM-{parent.oam_work_order_id.hex[:12].upper()}-{digest[:12].upper()}"
                or transaction is None or transaction.status != "posted" or transaction.movement_type != "reversal"
                or transaction.reversed_transaction_id != original.posting_transaction_id
                or transaction.actor_user_id != actor.user_id or transaction.idempotency_key_hash != digest
                or transaction.source_document_type != "work_order_material" or transaction.source_document_id != str(parent.oam_work_order_id)
                or transaction.ledger_cursor != cursor + index or _utc(parent.created_at) > _utc(transaction.created_at)
                or transaction.transaction_no != "INV-WO-REVERSE-" + digest[:20].upper()
                or transaction.posting_key != f"work-order-material:reverse:{parent.oam_work_order_id}:{digest}"
                or planned["original_operation_id"] != str(original_id)
                or planned["original_transaction_id"] != str(original.posting_transaction_id)):
            _invalid()
        command = posting.InventoryPostingCommand(transaction.transaction_no, "reversal", "work_order_material",
            str(parent.oam_work_order_id), transaction.posting_key, _utc(transaction.effective_at), movements)
        if transaction.request_hash != posting._posting_request_hash(historical_actor, command):
            _invalid()
        rows = tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == transaction.id).order_by(InventoryMovement.line_no)))
        lines = tuple(db.scalars(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id == operation.id).order_by(WorkOrderMaterialLine.line_no)))
        planned = WorkOrderReversalChildOut.model_validate(planned)
        original_rows = tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == original_transaction.id).order_by(InventoryMovement.line_no)))
        if (len(rows) != len(movements) or len(lines) != len(movements) or len(planned.movements) != len(movements)
                or planned.original_operation_type != original.operation_type or planned.original_ledger_cursor != original_transaction.ledger_cursor):
            _invalid()
        for number, (movement, row, line) in enumerate(zip(movements, rows, lines), 1):
            account = db.get(StockAccount, movement.from_account_id or movement.to_account_id)
            frozen = planned.movements[number - 1]
            reservation_delta = (-movement.quantity if original.operation_type == "occupy" else
                movement.quantity if original.operation_type in {"release", "consume"} else 0)
            serials = tuple(db.execute(select(WorkOrderMaterialSerial.serial_id, WorkOrderMaterialSerial.sku_verified,
                WorkOrderMaterialSerial.qr_verified).where(WorkOrderMaterialSerial.operation_line_id == line.id)))
            stock_serials = tuple(db.scalars(select(InventoryMovementSerial.serial_id).where(InventoryMovementSerial.movement_id == row.id)))
            if (row.line_no != number or row.quantity != movement.quantity or row.from_account_id != movement.from_account_id
                    or row.to_account_id != movement.to_account_id or row.external_boundary_code != movement.external_boundary_code
                    or set(stock_serials) != set(movement.serial_ids) or {s[0] for s in serials} != set(movement.serial_ids)
                    or any(not s[1] or not s[2] for s in serials)
                    or account is None or account.custodian_person_id != actor.person_id or line.line_no != number
                    or line.quantity != movement.quantity or line.stock_account_id != account.id or line.material_id != account.material_id
                    or line.condition_before != account.condition_code or line.condition_after is not None
                    or frozen.original_movement_id != original_rows[number - 1].id or frozen.line_no != number
                    or frozen.from_account_id != movement.from_account_id or frozen.to_account_id != movement.to_account_id
                    or frozen.material_id != account.material_id or frozen.condition_code != account.condition_code or frozen.lot_id != account.lot_id
                    or frozen.quantity != format(movement.quantity, ".3f") or frozen.reservation_delta != format(reservation_delta, ".3f")
                    or tuple(s.serial_id for s in frozen.serials) != movement.serial_ids):
                _invalid()
            from .serial_ledger import rebuild_serial_states
            before = rebuild_serial_states(db, movement.serial_ids, through_cursor=original_transaction.ledger_cursor - 1)
            after = rebuild_serial_states(db, movement.serial_ids, through_cursor=original_transaction.ledger_cursor)
            if any(proof.lifecycle_before != after[proof.serial_id].lifecycle_status
                    or proof.lifecycle_after != before[proof.serial_id].lifecycle_status
                    or proof.previous_movement_id != before[proof.serial_id].last_movement_id
                    or proof.previous_ledger_cursor != before[proof.serial_id].ledger_cursor for proof in frozen.serials):
                _invalid()
        _audit(db, actor=actor, stream="inventory", action="inventory.transaction.reversed", aggregate_type="inventory_transaction",
            aggregate_id=transaction.id, request_id=posting._request_reference(parent.request_id), before=None,
            after={"ledger_cursor": transaction.ledger_cursor, "movement_count": len(movements), "movement_type": "reversal",
                "posting_key": transaction.posting_key, "reversed_transaction_id": str(original.posting_transaction_id), "status": "posted"})
        _audit(db, actor=actor, stream="material_request", action="work_order_material.reverse", aggregate_type="work_order_material_operation",
            aggregate_id=operation.id, request_id="work-order-material:" + digest, before={}, after={
                "work_order_id": str(parent.oam_work_order_id), "operation_type": "reverse", "posting_transaction_id": str(transaction.id),
                "line_count": len(movements), "request_hash": operation.request_hash, "command": value})
        _single(db, StateTransitionEvent, aggregate_type="inventory_transaction", aggregate_id=str(transaction.id), from_status=None,
            to_status="posted", reason="inventory_transaction_reversed", actor_id=actor.user_id,
            idempotency_key=posting._derived_evidence_key("state", transaction.id, "reversed"),
            metadata_jsonb={"ledger_cursor": transaction.ledger_cursor, "movement_type": "reversal",
                "request_reference": posting._request_reference(parent.request_id)})
        _single(db, OutboxEvent, event_type="inventory.transaction.reversed", aggregate_type="inventory_transaction",
            aggregate_id=str(transaction.id), idempotency_key=posting._derived_evidence_key("outbox", transaction.id, "reversed"),
            payload_jsonb={"transaction_id": str(transaction.id), "transaction_no": transaction.transaction_no,
                "movement_type": "reversal", "ledger_cursor": transaction.ledger_cursor, "reversed_transaction_id": str(original.posting_transaction_id)})
        _single(db, OutboxEvent, event_type="work_order_material_operation_posted", aggregate_type="work_order_material_operation",
            aggregate_id=str(operation.id), idempotency_key="work-order-material-operation:" + str(operation.id),
            payload_jsonb={"work_order_id": str(parent.oam_work_order_id), "operation_type": "reverse",
                "operation_no": operation.operation_no, "posting_transaction_id": str(transaction.id)})
        result.append(WorkOrderReversalResultItemOut(original_operation_id=original.id, original_transaction_id=original.posting_transaction_id,
            inverse_operation_id=operation.id, inverse_transaction_id=transaction.id))
    payload = parent_payload(parent, items)
    _audit(db, actor=actor, stream="material_request", action="work_order_material.reversal", aggregate_type="work_order_material_reversal",
        aggregate_id=parent.id, request_id=parent.request_id, before={}, after=payload)
    _single(db, OutboxEvent, event_type="work_order_material_reversal_posted", aggregate_type="work_order_material_reversal",
        aggregate_id=str(parent.id), idempotency_key="work-order-material-reversal:" + str(parent.id), payload_jsonb=payload)
    return WorkOrderReversalOut(reversal_id=parent.id, reversal_no=parent.reversal_no, work_order_id=parent.oam_work_order_id,
        operator_person_id=parent.operator_person_id, request_id=parent.request_id, request_hash=parent.request_hash,
        plan_hash=parent.plan_hash, posted_at=_utc(parent.created_at), items=tuple(result))


def reversal_result(db, *, actor, parent):
    try:
        return _result(db, actor=actor, parent=parent)
    except (KeyError, TypeError, ValueError, AttributeError, AuditChainError):
        _invalid()
