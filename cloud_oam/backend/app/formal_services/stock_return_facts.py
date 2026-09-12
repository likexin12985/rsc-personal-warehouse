"""Re-prove original return documents before reading commitments or cancelling."""
from dataclasses import replace
from decimal import Decimal, InvalidOperation
import re

from sqlalchemy import select

from ..demand_models import WorkOrderMaterialLine, WorkOrderMaterialOperation, WorkOrderMaterialSerial, WorkOrderReplacement
from ..foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from ..inventory_models import CustodyAssignment, InventoryMovement, InventoryMovementSerial, InventoryTransaction, StockAccount
from ..stock_operation_models import StockOperationOrder as Order, StockOperationLine as Line, StockOperationSerial as Serial, StockOperationCancellation as Cancellation
from ..stock_return_schemas import StockReturnOut, StockReturnCancellationOut, StockReturnPreviewIn
from . import inventory_posting as posting
from .audit_chain import AuditChainError, verify_audit_event_in_read_snapshot
from .stock_return_plan import intent, authorize
from .work_order_query import _aware
from .work_order_return_sources import _hash, _fail
from .work_order_operation_read import verify_operation_history
from .work_order_replacement_read import replacement_result

MAX_RETURN_HISTORY = 1000


def invalid():
    _fail("stock_return_evidence_invalid", "原退回单、占用/取消流水或责任明细证据不一致，请保留原单核验", 503)


def single(db, model, **fields):
    rows = tuple(db.scalars(select(model).filter_by(**fields).limit(2).execution_options(populate_existing=True)))
    if len(rows) != 1: invalid()
    return rows[0]


def rows(db, order):
    result = tuple(db.scalars(select(Line).where(Line.operation_id == order.id).order_by(Line.line_no)
        .execution_options(populate_existing=True)))
    if not 1 <= len(result) <= 100: invalid()
    return result


def serial_ids(db, line):
    serials = tuple(db.scalars(select(Serial).where(Serial.line_id == line.id).order_by(Serial.serial_id)
        .execution_options(populate_existing=True)))
    if any(not row.sku_verified or not row.qr_verified for row in serials): invalid()
    return tuple(row.serial_id for row in serials)


def movements(db, order, *, cancel=False):
    return tuple(posting.InventoryMovementCommand(from_account_id=line.reserved_account_id if cancel else line.stock_account_id,
        to_account_id=line.stock_account_id if cancel else line.reserved_account_id, quantity=line.quantity,
        serial_ids=serial_ids(db, line)) for line in rows(db, order))


def posting_command(order, *, key, at, movements, cancel=False):
    kind = "cancel_return" if cancel else "submit_return"
    return posting.InventoryPostingCommand(transaction_no="INV-RETURN-" + ("C-" if cancel else "S-") + key[:20].upper(),
        movement_type="release" if cancel else "reserve", source_document_type="stock_operation_return",
        source_document_id=str(order.id), posting_key=f"stock-return:{kind}:{order.id}:{key}",
        effective_at=at, movements=movements)


def payload(order, transaction_id, request_hash, *, cancellation=None):
    return {"operation_id": str(order.id), "work_order_id": str(order.oam_work_order_id),
        "requester_id": str(order.requester_id), "posting_transaction_id": str(transaction_id),
        "request_hash": request_hash, "cancellation_id": str(cancellation.id) if cancellation else None}


def audit(db, *, actor, stream, aggregate_type, identifier, action, request_id, before, after):
    row = single(db, AuditEvent, stream_key=stream, aggregate_type=aggregate_type, aggregate_id=str(identifier))
    if (row.actor_user_id != actor.user_id or row.action != action or row.request_id != request_id
            or row.before_jsonb != before or row.after_jsonb != after): invalid()
    verify_audit_event_in_read_snapshot(db, stream_key=stream, event_id=row.id)


def verify_posting(db, *, actor, order, fact, cancel=False):
    tx = db.get(InventoryTransaction, fact.posting_transaction_id, populate_existing=True)
    if tx is None: invalid()
    expected = movements(db, order, cancel=cancel)
    command = posting_command(order, key=fact.idempotency_key_hash, at=_aware(tx.effective_at), movements=expected, cancel=cancel)
    if (tx.actor_user_id != actor.user_id or tx.status != "posted" or tx.reversed_transaction_id is not None
            or tx.idempotency_key_hash != fact.idempotency_key_hash or tx.transaction_no != command.transaction_no
            or tx.source_document_type != command.source_document_type or tx.source_document_id != command.source_document_id
            or tx.movement_type != command.movement_type or tx.posting_key != command.posting_key
            or tx.request_hash != posting._posting_request_hash(replace(actor, authorization_version=fact.authorization_version), command)
            or _aware(fact.created_at) > _aware(tx.created_at)
            or db.scalar(select(InventoryTransaction.id).where(InventoryTransaction.reversed_transaction_id == tx.id).limit(1))): invalid()
    actual = tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == tx.id).order_by(InventoryMovement.line_no)))
    if len(actual) != len(expected): invalid()
    for number, (movement, wanted) in enumerate(zip(actual, expected), 1):
        ids = tuple(db.scalars(select(InventoryMovementSerial.serial_id).where(InventoryMovementSerial.movement_id == movement.id).order_by(InventoryMovementSerial.serial_id)))
        if (movement.line_no != number or movement.quantity != wanted.quantity or movement.from_account_id != wanted.from_account_id
                or movement.to_account_id != wanted.to_account_id or movement.external_boundary_code is not None or ids != wanted.serial_ids): invalid()
    audit(db, actor=actor, stream="inventory", aggregate_type="inventory_transaction", identifier=tx.id,
        action="inventory.transaction.posted", request_id=posting._request_reference(fact.request_id), before=None,
        after={"ledger_cursor": tx.ledger_cursor, "movement_count": len(expected), "movement_type": tx.movement_type,
            "posting_key": tx.posting_key, "reversed_transaction_id": None, "status": "posted"})
    single(db, StateTransitionEvent, aggregate_type="inventory_transaction", aggregate_id=str(tx.id), from_status=None,
        to_status="posted", reason="inventory_transaction_posted", actor_id=actor.user_id,
        idempotency_key=posting._derived_evidence_key("state", tx.id, "posted"),
        metadata_jsonb={"ledger_cursor": tx.ledger_cursor, "movement_type": tx.movement_type, "request_reference": posting._request_reference(fact.request_id)})
    single(db, OutboxEvent, aggregate_type="inventory_transaction", aggregate_id=str(tx.id), event_type="inventory.transaction.posted",
        idempotency_key=posting._derived_evidence_key("outbox", tx.id, "posted"), payload_jsonb={"transaction_id": str(tx.id),
            "transaction_no": tx.transaction_no, "movement_type": tx.movement_type, "ledger_cursor": tx.ledger_cursor, "reversed_transaction_id": None})
    return tx


def verify_domain_event(db, *, actor, order, fact, cancel=False):
    kind = "stock_return_cancelled" if cancel else "stock_return_submitted"
    aggregate = "stock_operation_cancellation" if cancel else "stock_operation_order"
    body = payload(order, fact.posting_transaction_id, fact.request_hash, cancellation=fact if cancel else None)
    audit(db, actor=actor, stream="material_request", aggregate_type=aggregate, identifier=fact.id,
        action=kind, request_id=fact.request_id, before={}, after=body)
    single(db, OutboxEvent, aggregate_type=aggregate, aggregate_id=str(fact.id), event_type=kind,
        idempotency_key=kind + ":" + str(fact.id), payload_jsonb=body)
    single(db, StateTransitionEvent, aggregate_type=aggregate, aggregate_id=str(fact.id), from_status=None,
        to_status="cancelled" if cancel else "submitted", actor_id=actor.user_id, reason=kind,
        idempotency_key=kind + ":" + str(fact.id), metadata_jsonb=body)


def _result(db, *, actor, order):
    if order.actor_user_id != actor.user_id or order.requester_id != actor.person_id:
        _fail("stock_return_not_found", "本人退回单不存在", 404)
    if (order.operation_type != "return" or order.status != "submitted" or order.authorization_version < 1
            or not re.fullmatch(r"[0-9a-f]{64}", order.idempotency_key_hash)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", order.request_id)
            or order.operation_no != "RET-" + order.idempotency_key_hash[:24].upper()
            or order.request_hash != _hash(order.command_jsonb) or order.plan_hash != _hash(order.plan_jsonb)
            or order.plan_jsonb["intent"] != order.command_jsonb
            or not re.fullmatch(r"[0-9a-f]{64}", order.plan_jsonb["source_basis_hash"])
            or order.plan_jsonb["authorization_version"] != order.authorization_version): invalid()
    request = StockReturnPreviewIn.model_validate({key: order.command_jsonb[key] for key in StockReturnPreviewIn.model_fields})
    if (intent(order.oam_work_order_id, request) != order.command_jsonb or request.reason != order.reason
            or request.operator_person_id != order.requester_id or request.target_location_id != order.target_location_id
            or request.transit_location_id != order.transit_location_id): invalid()
    expected = order.plan_jsonb["lines"]; actual = rows(db, order)
    if len(expected) != len(actual) or len(request.lines) != len(actual): invalid()
    for index, (line, selected) in enumerate(zip(actual, sorted(request.lines, key=lambda row: str(row.source_recovery_line_id))), 1):
        source = db.get(StockAccount, line.stock_account_id, populate_existing=True)
        reserved = db.get(StockAccount, line.reserved_account_id, populate_existing=True)
        origin = db.get(WorkOrderMaterialLine, line.source_recovery_line_id, populate_existing=True)
        recovery = db.get(WorkOrderMaterialOperation, origin.operation_id, populate_existing=True) if origin else None
        ids = serial_ids(db, line)
        if (source is None or reserved is None or origin is None or recovery is None or line.line_no != index
                or line.source_recovery_line_id != selected.source_recovery_line_id or line.stock_account_id != selected.stock_account_id
                or line.quantity != selected.quantity or line.quantity > origin.quantity or line.reason != order.reason
                or line.material_id != source.material_id or origin.material_id != line.material_id
                or line.target_condition != source.condition_code or source.condition_code != origin.condition_before
                or source.availability_bucket != "available" or reserved.availability_bucket != "return_pending"
                or any(getattr(source, name) != getattr(reserved, name) for name in ("owner_org_id", "custodian_person_id", "location_id", "material_id", "lot_id", "condition_code"))
                or source.custodian_person_id != actor.person_id or source.location_id != order.source_location_id
                or origin.stock_account_id != source.id or recovery.oam_work_order_id != order.oam_work_order_id
                or recovery.operator_person_id != actor.person_id or recovery.operation_type != "recover"
                or ids != tuple(sorted((proof.serial_id for proof in selected.serial_verifications), key=str))): invalid()
        original_ids = set(db.scalars(select(WorkOrderMaterialSerial.serial_id).where(WorkOrderMaterialSerial.operation_line_id == origin.id)))
        if not set(ids) <= original_ids: invalid()
        if recovery.replacement_id:
            pair = db.get(WorkOrderReplacement, recovery.replacement_id)
            if pair is None: invalid()
            replacement_result(db, actor=actor, replacement=pair)
        else: verify_operation_history(db, actor=actor, operation=recovery)
        view = expected[index - 1]
        basis = view["source"]
        coordinates = {"source_recovery_line_id": origin.id, "recovery_operation_id": recovery.id,
            "stock_account_id": source.id, "material_id": source.material_id, "owner_org_id": source.owner_org_id,
            "custodian_person_id": source.custodian_person_id, "location_id": source.location_id, "lot_id": source.lot_id}
        if (any(basis[name] != (str(value) if value is not None else None) for name, value in coordinates.items())
                or basis["condition_code"] != source.condition_code or basis["recovery_operation_no"] != recovery.operation_no
                or basis["owed_quantity"] != format(origin.quantity, ".3f")
                or view["selected_quantity"] != format(line.quantity, ".3f")
                or view["selected_serials"] != [{"serial_id": str(proof.serial_id), "serial_no": proof.serial_no}
                    for proof in sorted(selected.serial_verifications, key=lambda proof: str(proof.serial_id))]): invalid()
        amounts = [Decimal(basis[name]) for name in ("owed_quantity", "committed_quantity", "available_quantity", "selectable_quantity")]
        if any(not value.is_finite() or value < 0 for value in amounts): invalid()
        owed, committed, available, selectable = amounts
        if committed > owed or selectable != min(owed - committed, available) or line.quantity > selectable: invalid()
    destination = order.plan_jsonb["destination"]
    assignment = db.get(CustodyAssignment, order.target_custody_assignment_id, populate_existing=True)
    if (destination["source_location_id"] != str(order.source_location_id) or destination["target_location_id"] != str(order.target_location_id)
            or destination["transit_location_id"] != str(order.transit_location_id)
            or destination["custody_assignment_id"] != str(order.target_custody_assignment_id)
            or assignment is None or assignment.location_id != order.target_location_id
            or destination["custodian_person_id"] != str(assignment.custodian_person_id)
            or _aware(assignment.valid_from) > _aware(order.created_at)
            or (assignment.valid_to is not None and _aware(assignment.valid_to) <= _aware(order.created_at))): invalid()
    tx = verify_posting(db, actor=actor, order=order, fact=order)
    if tx.ledger_cursor != order.plan_jsonb["ledger_cursor"] + 1: invalid()
    verify_domain_event(db, actor=actor, order=order, fact=order)
    return StockReturnOut(operation_id=order.id, operation_no=order.operation_no, work_order_id=order.oam_work_order_id,
        requester_id=order.requester_id, reason=order.reason, request_id=order.request_id, request_hash=order.request_hash,
        plan_hash=order.plan_hash, posting_transaction_id=tx.id, submitted_at=_aware(order.created_at), destination=destination, lines=expected)


def order_result(db, *, actor, order):
    try: return _result(db, actor=actor, order=order)
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation, AuditChainError): invalid()


def cancellation_result(db, *, actor, order, cancellation):
    try:
        row = cancellation
        if (row.actor_user_id != actor.user_id or row.operator_person_id != actor.person_id or row.operation_id != order.id
                or row.authorization_version < 1 or not re.fullmatch(r"[0-9a-f]{64}", row.idempotency_key_hash)
                or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", row.request_id)
                or row.command_jsonb != {"operation_id": str(order.id), "operator_person_id": str(actor.person_id), "reason": row.reason}
                or row.request_hash != _hash(row.command_jsonb)): invalid()
        order_result(db, actor=actor, order=order)
        tx = verify_posting(db, actor=actor, order=order, fact=row, cancel=True)
        original = db.get(InventoryTransaction, order.posting_transaction_id)
        if tx.ledger_cursor <= original.ledger_cursor: invalid()
        verify_domain_event(db, actor=actor, order=order, fact=row, cancel=True)
        return StockReturnCancellationOut(cancellation_id=row.id, operation_id=order.id, operator_person_id=actor.person_id,
            reason=row.reason, request_id=row.request_id, request_hash=row.request_hash, posting_transaction_id=tx.id,
            cancelled_at=_aware(row.created_at))
    except (KeyError, TypeError, ValueError, AttributeError, InvalidOperation, AuditChainError): invalid()


def commitments(db, *, actor, recovery_line_ids):
    result = {identifier: (Decimal(0), frozenset()) for identifier in recovery_line_ids}
    orders = tuple(db.scalars(select(Order).where(Order.id.in_(select(Line.operation_id).where(
        Line.source_recovery_line_id.in_(recovery_line_ids)))).order_by(Order.id).limit(MAX_RETURN_HISTORY + 1)))
    if len(orders) > MAX_RETURN_HISTORY:
        _fail("stock_return_history_too_large", "退回历史超过完整核验范围，不能使用部分数量判断责任", 503)
    for order in orders:
        order_result(db, actor=actor, order=order)
        cancelled = db.scalar(select(Cancellation).where(Cancellation.operation_id == order.id))
        if cancelled:
            cancellation_result(db, actor=actor, order=order, cancellation=cancelled)
            continue
        for line in rows(db, order):
            if line.source_recovery_line_id not in result: continue
            quantity, serials = result[line.source_recovery_line_id]
            ids = frozenset(serial_ids(db, line))
            if serials & ids: invalid()
            result[line.source_recovery_line_id] = quantity + line.quantity, serials | ids
    return result
