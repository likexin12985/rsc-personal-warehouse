"""Recover an own command from original request evidence without replaying it.

No-result is only an observation. It never authorizes resending a command.
The request reference is already stored in the inventory audit and state fact;
the exact command is proved by the separate immutable work-order audit.
"""
from dataclasses import replace
from datetime import timezone
import hashlib
import json
import re
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import select

from ..demand_models import WorkOrderMaterialLine, WorkOrderMaterialOperation, WorkOrderMaterialSerial
from ..foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from ..inventory_models import InventoryMovement, InventoryTransaction, StockAccount
from ..work_order_material_schemas import (
    WorkOrderMaterialOccupyLineIn, WorkOrderMaterialRecoveredOut, WorkOrderMaterialLookupOut,
)
from . import work_order_material as material
from .audit_chain import AuditChainError, verify_audit_event_in_read_snapshot
from .inventory_posting import _require_current_actor, _request_reference, _derived_evidence_key
from .work_order_reservations import require_work_order_reservations


def _invalid():
    raise material.WorkOrderMaterialPreflightError("work_order_recovery_evidence_invalid",
        "原工单命令、库存流水或请求证据不一致，请保留原记录核验", "service_unavailable")


def _single(db, model, **values):
    rows = tuple(db.scalars(select(model).filter_by(**values).limit(2)))
    if len(rows) != 1:
        _invalid()
    return rows[0]


def client_request_hash(*, operation_type, work_order_id, operator_person_id, lines):
    # Occupy targets are derived by the server from the source dimensions. A
    # supplied target is only an assertion; it must not change recovery intent.
    if operation_type == "occupy":
        lines = tuple(replace(line, target_stock_account_id=None) for line in lines)
    value = material.operation_request_payload(operation_type=operation_type, work_order_id=work_order_id,
        operator_person_id=operator_person_id, lines=lines)
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def lookup_operation(db, *, actor, work_order_id, operation_type, request_id):
    if operation_type not in {"occupy", "consume", "release"} or not isinstance(request_id, str) or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", request_id):
        raise material.WorkOrderMaterialPreflightError("work_order_lookup_invalid", "原请求查询条件无效", "invalid_request")
    current = _require_current_actor(db, actor)
    if not current.allows(db, "work_order_material", "read", target_scope_type="person", target_scope_id=str(current.person_id)):
        raise material.WorkOrderMaterialPreflightError("work_order_forbidden", "没有本人工单操作读取权限", "forbidden")
    with db.no_autoflush:
        events = tuple(db.scalars(select(AuditEvent).where(
            AuditEvent.stream_key=="inventory", AuditEvent.action=="inventory.transaction.posted",
            AuditEvent.actor_user_id==current.user_id, AuditEvent.aggregate_type=="inventory_transaction",
            AuditEvent.request_id==_request_reference(request_id)).limit(101)))
        states = tuple(db.scalars(select(StateTransitionEvent).where(
            StateTransitionEvent.aggregate_type=="inventory_transaction", StateTransitionEvent.reason=="inventory_transaction_posted",
            StateTransitionEvent.actor_id==current.user_id,
            StateTransitionEvent.metadata_jsonb["request_reference"].as_string()==_request_reference(request_id)).limit(101)))
        if len(events) > 100 or len(states) > 100:
            _invalid()
        matches = []
        for transaction_id in {event.aggregate_id for event in (*events, *states)}:
            try:
                transaction = db.get(InventoryTransaction, UUID(transaction_id), populate_existing=True)
            except (ValueError, TypeError):
                _invalid()
            if transaction is None:
                _invalid()
            if transaction.source_document_type != "work_order_material" or transaction.source_document_id != str(work_order_id):
                continue
            operation = _single(db, WorkOrderMaterialOperation, posting_transaction_id=transaction.id)
            if operation.operation_type != operation_type or operation.replacement_id is not None:
                continue
            event = _single(db, AuditEvent, stream_key="inventory", action="inventory.transaction.posted",
                actor_user_id=current.user_id, aggregate_type="inventory_transaction", aggregate_id=str(transaction.id),
                request_id=_request_reference(request_id))
            matches.append((operation, transaction, event))
        if len(matches) > 1:
            raise material.WorkOrderMaterialPreflightError("work_order_request_ambiguous", "原请求对应多笔操作，禁止自动恢复或再次提交", "conflict")
        if not matches:
            _require_current_actor(db, current)
            return WorkOrderMaterialLookupOut(lookup_status="not_observed", command=None)
        try:
            result = _verified_result(db, actor=current, request_id=request_id, operation_type=operation_type,
                                      work_order_id=work_order_id, operation=matches[0][0], transaction=matches[0][1], inventory_audit=matches[0][2])
        except (KeyError, TypeError, ValueError, AttributeError, ValidationError, AuditChainError, material.InventoryPostingError):
            _invalid()
        _require_current_actor(db, current)
        return WorkOrderMaterialLookupOut(lookup_status="confirmed", command=result)


def _verified_result(db, *, actor, request_id, operation_type, work_order_id, operation, transaction, inventory_audit):
    digest = operation.idempotency_key_hash
    if (not re.fullmatch(r"[0-9a-f]{64}", digest) or operation.operator_person_id != actor.person_id
            or operation.oam_work_order_id != work_order_id or operation.operation_type != operation_type
            or operation.status != "posted" or operation.operation_no != f"WOM-{work_order_id.hex[:12].upper()}-{digest[:12].upper()}"
            or transaction.actor_user_id != actor.user_id or transaction.status != "posted" or transaction.ledger_cursor <= 0
            or transaction.reversed_transaction_id is not None
            or transaction.movement_type != material.expected_posting_movement_type(operation_type)
            or transaction.posting_key != f"work-order-material:{operation_type}:{work_order_id}:{digest}"
            or transaction.transaction_no != f"INV-WO-{operation_type.upper()}-{digest[:20].upper()}"):
        _invalid()
    audit = _single(db, AuditEvent, stream_key="material_request", action="work_order_material."+operation_type,
        aggregate_type="work_order_material_operation", aggregate_id=str(operation.id))
    value = audit.after_jsonb["command"]
    if not isinstance(value, dict) or not isinstance(value.get("lines"), list) or not 1 <= len(value["lines"]) <= 100:
        _invalid()
    lines = []
    for row in value["lines"]:
        parsed = WorkOrderMaterialOccupyLineIn.model_validate(row).model_dump()
        parsed["serial_verifications"] = tuple(material.SerialVerificationInput(**proof) for proof in parsed["serial_verifications"])
        lines.append(material.WorkOrderMaterialLineInput(**parsed))
    lines = tuple(lines)
    command = material.operation_request_payload(operation_type=operation_type, work_order_id=work_order_id,
        operator_person_id=actor.person_id, lines=lines)
    if (value != command or operation.request_hash != material.operation_request_hash(operation_type=operation_type,
            work_order_id=work_order_id, operator_person_id=actor.person_id, lines=lines)
            or audit.actor_user_id != actor.user_id or audit.request_id != "work-order-material:"+digest
            or audit.before_jsonb != {} or audit.after_jsonb != {"work_order_id":str(work_order_id), "operation_type":operation_type,
                "posting_transaction_id":str(transaction.id), "line_count":len(lines), "request_hash":operation.request_hash, "command":command}):
        _invalid()
    material._verify_posted_lines(db, transaction=transaction, operation_type=operation_type, operator_person_id=actor.person_id, lines=lines)
    movements = tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id==transaction.id).order_by(InventoryMovement.line_no)))
    facts = tuple(db.scalars(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id==operation.id).order_by(WorkOrderMaterialLine.line_no)))
    if len(facts) != len(lines) or len(movements) != len(lines):
        _invalid()
    for index, (line, fact, movement) in enumerate(zip(lines, facts, movements), 1):
        if (fact.line_no != index or fact.material_id != line.material_id or fact.stock_account_id != line.stock_account_id
                or fact.quantity != line.quantity or fact.condition_before != line.condition_before or fact.condition_after is not None
                or movement.line_no != index or movement.from_account_id != line.stock_account_id
                or movement.to_account_id != line.target_stock_account_id
                or movement.external_boundary_code != ("work_order_material_consume" if operation_type=="consume" else None)):
            _invalid()
        if operation_type == "consume" and line.target_stock_account_id is not None:
            _invalid()
        if operation_type in {"occupy", "release"}:
            source = db.get(StockAccount, line.stock_account_id)
            target = db.get(StockAccount, line.target_stock_account_id)
            if (target is None or target.availability_bucket != ("reserved" if operation_type=="occupy" else "available")
                    or any(getattr(source, key) != getattr(target, key) for key in (
                        "owner_org_id", "location_id", "custodian_person_id", "material_id", "condition_code", "lot_id"))):
                _invalid()
        serials = tuple(db.execute(select(WorkOrderMaterialSerial.serial_id, WorkOrderMaterialSerial.sku_verified,
            WorkOrderMaterialSerial.qr_verified).where(WorkOrderMaterialSerial.operation_line_id==fact.id)))
        if {row[0] for row in serials} != set(line.serial_ids) or any(not row[1] or not row[2] for row in serials):
            _invalid()
    if operation_type in {"consume", "release"}:
        require_work_order_reservations(db, work_order_id=work_order_id, lines=lines, before_cursor=transaction.ledger_cursor)
    _single(db, OutboxEvent, event_type="work_order_material_operation_posted", aggregate_type="work_order_material_operation",
        aggregate_id=str(operation.id), idempotency_key="work-order-material-operation:"+str(operation.id),
        payload_jsonb={"work_order_id":str(work_order_id), "operation_type":operation_type,
                      "operation_no":operation.operation_no, "posting_transaction_id":str(transaction.id)})
    _single(db, OutboxEvent, event_type="inventory.transaction.posted", aggregate_type="inventory_transaction",
        aggregate_id=str(transaction.id), idempotency_key=_derived_evidence_key("outbox", transaction.id, "posted"),
        payload_jsonb={"transaction_id":str(transaction.id), "transaction_no":transaction.transaction_no,
                      "movement_type":transaction.movement_type, "ledger_cursor":transaction.ledger_cursor, "reversed_transaction_id":None})
    _single(db, StateTransitionEvent, aggregate_type="inventory_transaction", aggregate_id=str(transaction.id),
        from_status=None, to_status="posted", reason="inventory_transaction_posted", actor_id=actor.user_id,
        idempotency_key=_derived_evidence_key("state", transaction.id, "posted"),
        metadata_jsonb={"ledger_cursor":transaction.ledger_cursor, "movement_type":transaction.movement_type,
                        "request_reference":_request_reference(request_id)})
    if (inventory_audit.before_jsonb is not None or inventory_audit.after_jsonb != {
            "ledger_cursor":transaction.ledger_cursor, "movement_count":len(lines), "movement_type":transaction.movement_type,
            "posting_key":transaction.posting_key, "reversed_transaction_id":None, "status":"posted"}):
        _invalid()
    verify_audit_event_in_read_snapshot(db, stream_key="inventory", event_id=inventory_audit.id)
    verify_audit_event_in_read_snapshot(db, stream_key="material_request", event_id=audit.id)
    return WorkOrderMaterialRecoveredOut(operation_id=operation.id, operation_no=operation.operation_no,
        work_order_id=work_order_id, operator_person_id=actor.person_id, operation_type=operation_type,
        posting_transaction_id=transaction.id, status="posted", request_id=request_id,
        posted_at=transaction.posted_at if transaction.posted_at.tzinfo else transaction.posted_at.replace(tzinfo=timezone.utc),
        request_hash=client_request_hash(operation_type=operation_type, work_order_id=work_order_id, operator_person_id=actor.person_id, lines=lines))
