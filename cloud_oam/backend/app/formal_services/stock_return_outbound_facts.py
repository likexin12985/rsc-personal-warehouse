"""Immutable physical departures retain original responsibility and stock proof."""
from dataclasses import replace
from decimal import Decimal, InvalidOperation
import re
from sqlalchemy import select, func
from ..stock_operation_models import (StockOperationOrder, StockOperationLine, StockOperationCancellation,
    StockOperationOutbound, StockOperationOutboundLine, StockOperationOutboundSerial)
from ..inventory_models import StockAccount, InventoryTransaction, InventoryMovement, InventoryMovementSerial, CustodyAssignment
from ..foundation_models import StateTransitionEvent, OutboxEvent
from ..stock_return_outbound_schemas import StockReturnOutboundPreviewIn, StockReturnOutboundOut
from . import inventory_posting as posting, stock_return_facts as returns
from .stock_return_outbound_plan import intent, transit_dimensions
from .work_order_query import _aware
from .work_order_return_sources import _hash, _fail
from .audit_chain import AuditChainError


def invalid(): _fail("stock_return_outbound_evidence_invalid", "原退回发出、流水或实物明细证据不一致，请保留原请求核验", 503)


def lines(db, fact):
    result = tuple(db.scalars(select(StockOperationOutboundLine).where(StockOperationOutboundLine.outbound_id == fact.id)
        .order_by(StockOperationOutboundLine.line_no).execution_options(populate_existing=True)))
    if not 1 <= len(result) <= 100: invalid()
    return result


def serial_ids(db, line):
    result = tuple(db.scalars(select(StockOperationOutboundSerial).where(StockOperationOutboundSerial.line_id == line.id)
        .order_by(StockOperationOutboundSerial.serial_id).execution_options(populate_existing=True)))
    if any(not row.sku_verified or not row.qr_verified for row in result): invalid()
    return tuple(row.serial_id for row in result)


def command(fact, moves):
    return posting.InventoryPostingCommand(transaction_no="INV-RETURN-O-" + fact.idempotency_key_hash[:20].upper(),
        movement_type="transfer", source_document_type="stock_operation_return_outbound", source_document_id=str(fact.id),
        posting_key=f"stock-return:outbound_return:{fact.operation_id}:{fact.idempotency_key_hash}",
        effective_at=_aware(fact.outbound_at), movements=tuple(moves))


def payload(order, fact):
    return {"operation_id": str(order.id), "outbound_id": str(fact.id), "work_order_id": str(order.oam_work_order_id),
        "operator_person_id": str(fact.operator_person_id), "posting_transaction_id": str(fact.posting_transaction_id),
        "request_hash": fact.request_hash}


def outbound_result(db, *, actor, fact):
    try: return _result(db, actor, fact)
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, AuditChainError): invalid()


def _result(db, actor, fact):
    order = db.get(StockOperationOrder, fact.operation_id, populate_existing=True)
    if order is None or fact.actor_user_id != actor.user_id or fact.operator_person_id != actor.person_id:
        _fail("stock_return_outbound_not_found", "本人原退回发出记录不存在", 404)
    original = returns.order_result(db, actor=actor, order=order)
    request = StockReturnOutboundPreviewIn.model_validate({key: fact.command_jsonb[key] for key in StockReturnOutboundPreviewIn.model_fields})
    tx = db.get(InventoryTransaction, fact.posting_transaction_id, populate_existing=True)
    if (tx is None or fact.status != "outbound" or fact.authorization_version < 1
            or not re.fullmatch(r"[0-9a-f]{64}", fact.idempotency_key_hash)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", fact.request_id)
            or fact.outbound_no != "RET-OUT-" + fact.idempotency_key_hash[:24].upper()
            or intent(order.id, request) != fact.command_jsonb or request.operator_person_id != actor.person_id
            or request.reason != fact.reason or request.outbound_at != _aware(fact.outbound_at)
            or fact.request_hash != _hash(fact.command_jsonb) or fact.plan_hash != _hash(fact.plan_jsonb)
            or fact.plan_jsonb["intent"] != fact.command_jsonb or fact.plan_jsonb["authorization_version"] != fact.authorization_version
            or fact.plan_jsonb["original_request_hash"] != original.request_hash
            or fact.plan_jsonb["ledger_cursor"] + 1 != tx.ledger_cursor
            or _aware(fact.outbound_at) < _aware(order.created_at) or _aware(fact.outbound_at) > _aware(fact.created_at)
            or _aware(fact.created_at) > _aware(tx.created_at)
            or db.scalar(select(StockOperationCancellation.id).where(StockOperationCancellation.operation_id == order.id))
            or db.scalar(select(InventoryTransaction.id).where(InventoryTransaction.reversed_transaction_id == tx.id))): invalid()
    route = fact.plan_jsonb["destination"]
    assignment = db.get(CustodyAssignment, fact.target_custody_assignment_id, populate_existing=True)
    if (assignment is None or assignment.location_id != order.target_location_id
            or route["source_location_id"] != str(order.source_location_id) or route["target_location_id"] != str(order.target_location_id)
            or route["transit_location_id"] != str(order.transit_location_id) or route["custody_assignment_id"] != str(assignment.id)
            or route["custodian_person_id"] != str(assignment.custodian_person_id)
            or _aware(assignment.valid_from) > _aware(fact.created_at)
            or assignment.valid_to is not None and _aware(assignment.valid_to) <= _aware(fact.created_at)): invalid()
    actual = lines(db, fact); requested = sorted(request.lines, key=lambda row: str(row.operation_line_id))
    views = fact.plan_jsonb["lines"]; moves = []
    if len(actual) != len(requested) or len(actual) != len(views): invalid()
    for number, (row, selected, view) in enumerate(zip(actual, requested, views), 1):
        source_line = db.get(StockOperationLine, row.operation_line_id, populate_existing=True)
        source = db.get(StockAccount, row.source_stock_account_id, populate_existing=True)
        target = db.get(StockAccount, row.transit_stock_account_id, populate_existing=True)
        ids = serial_ids(db, row)
        if (source_line is None or source is None or target is None or source_line.operation_id != order.id
                or row.line_no != number or source_line.reserved_account_id != source.id
                or row.operation_line_id != selected.operation_line_id or row.quantity != selected.quantity
                or source.availability_bucket != "return_pending" or source.location_id != order.source_location_id
                or source.custodian_person_id != actor.person_id
                or any(getattr(target, key) != value for key, value in transit_dimensions(source, order).items())
                or ids != tuple(sorted((proof.serial_id for proof in selected.serial_verifications), key=str))
                or not set(ids) <= set(returns.serial_ids(db, source_line))): invalid()
        departed = db.scalar(select(func.coalesce(func.sum(StockOperationOutboundLine.quantity), 0))
            .join(StockOperationOutbound, StockOperationOutbound.id == StockOperationOutboundLine.outbound_id)
            .join(InventoryTransaction, InventoryTransaction.id == StockOperationOutbound.posting_transaction_id)
            .where(StockOperationOutboundLine.operation_line_id == source_line.id, InventoryTransaction.ledger_cursor < tx.ledger_cursor))
        available = db.scalar(select(func.coalesce(func.sum(InventoryMovement.quantity), 0)).join(InventoryTransaction)
            .where(InventoryMovement.to_account_id == source.id, InventoryTransaction.ledger_cursor < tx.ledger_cursor))
        spent = db.scalar(select(func.coalesce(func.sum(InventoryMovement.quantity), 0)).join(InventoryTransaction)
            .where(InventoryMovement.from_account_id == source.id, InventoryTransaction.ledger_cursor < tx.ledger_cursor))
        if (row.quantity > source_line.quantity - departed or row.quantity > available - spent
                or view["operation_line_id"] != str(source_line.id) or view["source_recovery_line_id"] != str(source_line.source_recovery_line_id)
                or view["source_stock_account_id"] != str(source.id) or view["material_id"] != str(source.material_id)
                or view["condition_code"] != source.condition_code or view["lot_id"] != (str(source.lot_id) if source.lot_id else None)
                or view["return_quantity"] != format(source_line.quantity, ".3f") or view["departed_quantity"] != format(departed, ".3f")
                or view["remaining_quantity"] != format(source_line.quantity - departed, ".3f")
                or view["held_quantity"] != format(available - spent, ".3f") or view["selected_quantity"] != format(row.quantity, ".3f")
                or view["selected_serials"] != [{"serial_id": str(proof.serial_id), "serial_no": proof.serial_no}
                    for proof in sorted(selected.serial_verifications, key=lambda item: str(item.serial_id))]): invalid()
        moves.append(posting.InventoryMovementCommand(from_account_id=source.id, to_account_id=target.id, quantity=row.quantity, serial_ids=ids))
    expected = command(fact, moves)
    if (tx.status != "posted" or tx.actor_user_id != actor.user_id or tx.reversed_transaction_id is not None
            or tx.idempotency_key_hash != fact.idempotency_key_hash or tx.transaction_no != expected.transaction_no
            or tx.source_document_type != expected.source_document_type or tx.source_document_id != expected.source_document_id
            or tx.posting_key != expected.posting_key or tx.movement_type != expected.movement_type
            or _aware(tx.effective_at) != expected.effective_at
            or tx.request_hash != posting._posting_request_hash(replace(actor, authorization_version=fact.authorization_version), expected)): invalid()
    actual_moves = tuple(db.scalars(select(InventoryMovement).where(InventoryMovement.transaction_id == tx.id).order_by(InventoryMovement.line_no)))
    if len(actual_moves) != len(moves): invalid()
    for number, (move, wanted) in enumerate(zip(actual_moves, moves), 1):
        ids = tuple(db.scalars(select(InventoryMovementSerial.serial_id).where(InventoryMovementSerial.movement_id == move.id).order_by(InventoryMovementSerial.serial_id)))
        if (move.line_no != number or move.from_account_id != wanted.from_account_id or move.to_account_id != wanted.to_account_id
                or move.quantity != wanted.quantity or move.external_boundary_code is not None or ids != wanted.serial_ids): invalid()
    returns.audit(db, actor=actor, stream="inventory", aggregate_type="inventory_transaction", identifier=tx.id,
        action="inventory.transaction.posted", request_id=posting._request_reference(fact.request_id), before=None,
        after={"ledger_cursor": tx.ledger_cursor, "movement_count": len(moves), "movement_type": tx.movement_type,
            "posting_key": tx.posting_key, "reversed_transaction_id": None, "status": "posted"})
    returns.single(db, StateTransitionEvent, aggregate_type="inventory_transaction", aggregate_id=str(tx.id), from_status=None,
        to_status="posted", reason="inventory_transaction_posted", actor_id=actor.user_id,
        idempotency_key=posting._derived_evidence_key("state", tx.id, "posted"),
        metadata_jsonb={"ledger_cursor": tx.ledger_cursor, "movement_type": tx.movement_type, "request_reference": posting._request_reference(fact.request_id)})
    returns.single(db, OutboxEvent, aggregate_type="inventory_transaction", aggregate_id=str(tx.id), event_type="inventory.transaction.posted",
        idempotency_key=posting._derived_evidence_key("outbox", tx.id, "posted"), payload_jsonb={"transaction_id": str(tx.id),
            "transaction_no": tx.transaction_no, "movement_type": tx.movement_type, "ledger_cursor": tx.ledger_cursor, "reversed_transaction_id": None})
    kind = "stock_return_outbound"; aggregate = "stock_operation_outbound"; body = payload(order, fact)
    returns.audit(db, actor=actor, stream="material_request", aggregate_type=aggregate, identifier=fact.id,
        action=kind, request_id=fact.request_id, before={}, after=body)
    returns.single(db, OutboxEvent, aggregate_type=aggregate, aggregate_id=str(fact.id), event_type=kind,
        idempotency_key=kind + ":" + str(fact.id), payload_jsonb=body)
    returns.single(db, StateTransitionEvent, aggregate_type=aggregate, aggregate_id=str(fact.id), from_status=None,
        to_status="outbound", actor_id=actor.user_id, reason=kind, idempotency_key=kind + ":" + str(fact.id), metadata_jsonb=body)
    return StockReturnOutboundOut(outbound_id=fact.id, outbound_no=fact.outbound_no, operation_id=order.id,
        work_order_id=order.oam_work_order_id, operator_person_id=actor.person_id, outbound_at=_aware(fact.outbound_at), recorded_at=_aware(fact.created_at),
        reason=fact.reason, request_id=fact.request_id, request_hash=fact.request_hash, plan_hash=fact.plan_hash,
        posting_transaction_id=tx.id, destination=route, lines=views)
