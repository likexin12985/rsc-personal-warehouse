"""Re-prove immutable return parcels at their original ledger and audit cursors."""
from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
import re
from uuid import UUID
from sqlalchemy import select, func, or_

from ..foundation_models import AuditEvent, StateTransitionEvent, OutboxEvent
from ..inventory_models import (Shipment, ShipmentLine, LogisticsEvent, Receipt, StockAccount,
    CustodyAssignment, InventoryTransaction, InventoryMovement)
from ..stock_operation_models import (StockOperationOrder, StockOperationLine, StockOperationCancellation,
    StockOperationCommandSeal, StockOperationOutbound, StockOperationOutboundLine,
    StockOperationShipment, StockOperationShipmentLine, StockOperationShipmentSerial)
from ..stock_return_shipment_schemas import StockReturnShipmentPreviewIn, StockReturnShipmentOut, StockReturnShipmentLineOut
from . import inventory_posting as posting, stock_return_facts as returns, stock_return_outbound_facts as departures
from .stock_return_shipment_plan import intent
from .stock_return_plan import authorize
from .work_order_query import _aware
from .work_order_return_sources import _hash, _fail
from .audit_chain import AuditChainError


def invalid():
    _fail("stock_return_shipment_evidence_invalid", "原退回运单、分包或审计证据不一致，请保留原请求核验", 503)


def lines(db, fact):
    rows = tuple(db.scalars(select(StockOperationShipmentLine).where(StockOperationShipmentLine.shipment_id == fact.id)
        .order_by(StockOperationShipmentLine.line_no).limit(101).execution_options(populate_existing=True)))
    if not 1 <= len(rows) <= 100: invalid()
    return rows


def serial_ids(db, line):
    rows = tuple(db.scalars(select(StockOperationShipmentSerial).where(StockOperationShipmentSerial.line_id == line.id)
        .order_by(StockOperationShipmentSerial.serial_id).limit(1001).execution_options(populate_existing=True)))
    if len(rows) > 1000 or any(row.outbound_line_id != line.outbound_line_id for row in rows): invalid()
    return tuple(row.serial_id for row in rows)


def payload(order, header):
    return {"operation_id": str(order.id), "shipment_id": str(header.id), "work_order_id": str(order.oam_work_order_id),
        "operator_person_id": str(header.actor_person_id), "request_hash": header.request_hash}


def _prior_quantities(db, fact, row, account):
    prefix = (StockOperationShipment.audit_version < fact.audit_version,)
    prior = db.scalar(select(func.coalesce(func.sum(StockOperationShipmentLine.quantity), 0))
        .join(StockOperationShipment).where(*prefix, StockOperationShipmentLine.outbound_line_id == row.id))
    assigned = db.scalar(select(func.coalesce(func.sum(StockOperationShipmentLine.quantity), 0))
        .join(StockOperationShipment).join(StockOperationOutboundLine, StockOperationOutboundLine.id == StockOperationShipmentLine.outbound_line_id)
        .where(*prefix, StockOperationOutboundLine.transit_stock_account_id == account.id))
    incoming = db.scalar(select(func.coalesce(func.sum(InventoryMovement.quantity), 0)).join(InventoryTransaction)
        .where(InventoryMovement.to_account_id == account.id, InventoryTransaction.ledger_cursor <= fact.plan_jsonb["ledger_cursor"]))
    outgoing = db.scalar(select(func.coalesce(func.sum(InventoryMovement.quantity), 0)).join(InventoryTransaction)
        .where(InventoryMovement.from_account_id == account.id, InventoryTransaction.ledger_cursor <= fact.plan_jsonb["ledger_cursor"]))
    return prior, assigned, incoming - outgoing


def _namespace(db, fact, header):
    for model in (StockOperationOrder, StockOperationCancellation, StockOperationOutbound, StockOperationCommandSeal):
        if db.scalar(select(model.id).where(model.actor_user_id == fact.actor_user_id, model.request_id == fact.request_id).limit(1)):
            invalid()
    # Return receiving/logistics adapters must be implemented separately before these can attach.
    for model in (ShipmentLine, LogisticsEvent, Receipt):
        if db.scalar(select(model.id).where(model.shipment_id == fact.id).limit(1)): invalid()
    if db.scalar(select(InventoryTransaction.id).where(or_(InventoryTransaction.idempotency_key_hash == header.idempotency_key_hash,
            (InventoryTransaction.source_document_type == "stock_operation_return_shipment") & (InventoryTransaction.source_document_id == str(fact.id)))).limit(1)):
        invalid()
    domain = tuple(db.execute(select(AuditEvent.aggregate_type, AuditEvent.aggregate_id).where(
        AuditEvent.stream_key == "material_request", AuditEvent.actor_user_id == fact.actor_user_id,
        AuditEvent.request_id == fact.request_id)))
    if domain != (("stock_operation_shipment", str(fact.id)),): invalid()
    if db.scalar(select(AuditEvent.id).where(AuditEvent.stream_key == "inventory", AuditEvent.actor_user_id == fact.actor_user_id,
            AuditEvent.request_id == posting._request_reference(fact.request_id)).limit(1)):
        invalid()


def shipment_result(db, *, actor, fact):
    try: return _result(db, authorize(db, actor, "read"), fact)
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, AuditChainError): invalid()


@dataclass(frozen=True, slots=True)
class _RecordedOperator:
    """Hash/audit coordinates only; this object has no authorization methods."""
    user_id: str
    person_id: UUID
    authorization_version: int


def verified_shipment_history(db, *, fact):
    """Internal immutable proof, after the caller authorizes its own reader.

    A receiver must not impersonate the sender or depend on that sender still
    having an active login. Never return this unrestricted proof from a route;
    project only the exact recipient fields after checking current custody.
    """
    try:
        header = db.get(Shipment, fact.id, populate_existing=True)
        if header is None: invalid()
        return _result(db, _RecordedOperator(header.actor_user_id, header.actor_person_id,
            header.authorization_version), fact)
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, AuditChainError): invalid()


def _result(db, actor, fact):
    if fact is None or fact.actor_user_id != actor.user_id:
        _fail("stock_return_shipment_not_found", "本人原退回运单不存在", 404)
    order = db.get(StockOperationOrder, fact.operation_id, populate_existing=True)
    header = db.get(Shipment, fact.id, populate_existing=True)
    if order is None or header is None or header.actor_user_id != actor.user_id or header.actor_person_id != actor.person_id: invalid()
    original = returns.order_result(db, actor=actor, order=order)
    request = StockReturnShipmentPreviewIn.model_validate({key: fact.command_jsonb[key] for key in StockReturnShipmentPreviewIn.model_fields})
    plan = fact.plan_jsonb
    if (set(plan) != {"intent", "authorization_version", "ledger_cursor", "audit_cursor", "original_request_hash", "destination", "policies", "lines"}
            or header.status != "shipped" or header.authorization_version < 1 or fact.audit_version < 1
            or not re.fullmatch(r"[a-f0-9]{64}", header.idempotency_key_hash)
            or not re.fullmatch(r"[A-Za-z0-9._:-]{8,160}", fact.request_id)
            or header.shipment_no != "RET-SHP-" + header.idempotency_key_hash[:24].upper()
            or intent(order.id, request) != fact.command_jsonb or request.operator_person_id != actor.person_id
            or request.reason != fact.reason or request.carrier != header.carrier or request.tracking_no != header.tracking_no
            or request.shipped_at != _aware(header.shipped_at) or _aware(fact.created_at) != _aware(header.created_at)
            or request.shipped_at > _aware(header.created_at) or request.shipped_at < _aware(order.created_at)
            or header.request_hash != _hash(fact.command_jsonb) or fact.plan_hash != _hash(plan)
            or plan["intent"] != fact.command_jsonb or plan["authorization_version"] != header.authorization_version
            or plan["original_request_hash"] != original.request_hash or type(plan["audit_cursor"]) is not int
            or plan["audit_cursor"] + 1 != fact.audit_version or type(plan["ledger_cursor"]) is not int or plan["ledger_cursor"] < 1
            or header.source_location_id != order.source_location_id or header.target_location_id != order.target_location_id
            or db.scalar(select(StockOperationCancellation.id).where(StockOperationCancellation.operation_id == order.id))): invalid()
    route = plan["destination"]
    assignment = db.get(CustodyAssignment, fact.target_custody_assignment_id, populate_existing=True)
    if (assignment is None or assignment.location_id != order.target_location_id
            or route["source_location_id"] != str(order.source_location_id) or route["target_location_id"] != str(order.target_location_id)
            or route["transit_location_id"] != str(order.transit_location_id) or route["custody_assignment_id"] != str(assignment.id)
            or route["custodian_person_id"] != str(assignment.custodian_person_id) or header.target_person_id != assignment.custodian_person_id
            or _aware(assignment.valid_from) > request.shipped_at
            or assignment.valid_to is not None and _aware(assignment.valid_to) <= _aware(fact.created_at)): invalid()
    actual = lines(db, fact); chosen = sorted(request.lines, key=lambda row: str(row.outbound_line_id)); views = plan["lines"]
    if len(actual) != len(chosen) or len(actual) != len(views): invalid()
    seen = set(); seen_serials = set(); totals = defaultdict(Decimal)
    for number, (line, selected, view) in enumerate(zip(actual, chosen, views), 1):
        row = db.get(StockOperationOutboundLine, line.outbound_line_id, populate_existing=True)
        parent = db.get(StockOperationOutbound, row.outbound_id, populate_existing=True) if row else None
        if parent is None or parent.operation_id != order.id: invalid()
        departed = departures.outbound_result(db, actor=actor, fact=parent)
        transaction = db.get(InventoryTransaction, parent.posting_transaction_id, populate_existing=True)
        account = db.get(StockAccount, row.transit_stock_account_id, populate_existing=True)
        origin = db.get(StockOperationLine, row.operation_line_id, populate_existing=True)
        ids = serial_ids(db, line)
        if (line.line_no != number or line.outbound_line_id != selected.outbound_line_id or line.quantity != selected.quantity
                or row.id in seen or seen_serials.intersection(ids) or len(set(ids)) != len(ids)
                or ids != tuple(sorted(selected.serial_ids, key=str)) or not set(ids) <= set(departures.serial_ids(db, row))
                or request.shipped_at < _aware(parent.outbound_at) or plan["ledger_cursor"] < transaction.ledger_cursor): invalid()
        seen.add(row.id); seen_serials.update(ids)
        if db.scalar(select(StockOperationShipmentSerial.id).join(StockOperationShipmentLine,
                StockOperationShipmentLine.id == StockOperationShipmentSerial.line_id).join(StockOperationShipment)
                .where(StockOperationShipmentSerial.outbound_line_id == row.id, StockOperationShipmentSerial.serial_id.in_(ids),
                    StockOperationShipment.audit_version < fact.audit_version).limit(1)): invalid()
        prior, assigned, held = _prior_quantities(db, fact, row, account)
        totals[account.id] += line.quantity
        serial_names = {proof.serial_id: proof.serial_no for item in departed.lines if item.operation_line_id == row.operation_line_id for proof in item.selected_serials}
        if (held - assigned < totals[account.id] or row.quantity - prior < line.quantity
                or view != StockReturnShipmentLineOut.model_validate(view).model_dump(mode="json")
                or view["outbound_id"] != str(parent.id) or view["outbound_no"] != parent.outbound_no
                or view["outbound_line_id"] != str(row.id) or view["operation_line_id"] != str(origin.id)
                or view["source_recovery_line_id"] != str(origin.source_recovery_line_id)
                or view["transit_stock_account_id"] != str(account.id) or view["material_id"] != str(account.material_id)
                or view["condition_code"] != account.condition_code or view["lot_id"] != (str(account.lot_id) if account.lot_id else None)
                or view["outbound_quantity"] != format(row.quantity, ".3f") or view["shipped_quantity"] != format(prior, ".3f")
                or view["unshipped_quantity"] != format(row.quantity - prior, ".3f") or view["in_transit_quantity"] != format(held, ".3f")
                or view["unassigned_quantity"] != format(held - assigned, ".3f") or view["selected_quantity"] != format(line.quantity, ".3f")
                or view["selected_serials"] != [{"serial_id": str(identifier), "serial_no": serial_names[identifier]} for identifier in ids]): invalid()
    kind = "stock_return_shipped"; aggregate = "stock_operation_shipment"; body = payload(order, header)
    returns.audit(db, actor=actor, stream="material_request", aggregate_type=aggregate, identifier=fact.id,
        action=kind, request_id=fact.request_id, before={}, after=body)
    event = returns.single(db, AuditEvent, stream_key="material_request", aggregate_type=aggregate, aggregate_id=str(fact.id))
    if (event.stream_version != fact.audit_version or _aware(event.occurred_at) != _aware(fact.created_at)
            or _aware(event.created_at) != _aware(fact.created_at)): invalid()
    returns.single(db, OutboxEvent, aggregate_type=aggregate, aggregate_id=str(fact.id), event_type=kind,
        idempotency_key=kind + ":" + str(fact.id), payload_jsonb=body)
    returns.single(db, StateTransitionEvent, aggregate_type=aggregate, aggregate_id=str(fact.id), from_status=None,
        to_status="shipped", actor_id=actor.user_id, reason=kind, idempotency_key=kind + ":" + str(fact.id), metadata_jsonb=body)
    _namespace(db, fact, header)
    return StockReturnShipmentOut(shipment_id=fact.id, shipment_no=header.shipment_no, operation_id=order.id,
        work_order_id=order.oam_work_order_id, operator_person_id=actor.person_id, shipped_at=_aware(header.shipped_at),
        recorded_at=_aware(fact.created_at), carrier=header.carrier, tracking_no=header.tracking_no, reason=fact.reason,
        request_id=fact.request_id, request_hash=header.request_hash, plan_hash=fact.plan_hash, destination=route, lines=views)
