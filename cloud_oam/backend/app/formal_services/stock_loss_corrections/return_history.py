"""Authorized, read-only loss-return fulfillment evidence for compensation planning.

This internal reader proves existing facts; it neither authorizes compensation
nor claims cumulative inbound is still available stock. No route writes or
replays a command through this module.
"""
from dataclasses import dataclass
from decimal import InvalidOperation
import hashlib
import json
from uuid import UUID

from sqlalchemy import or_, select

from app import stock_operation_models as models
from app.inventory_models import Shipment, Receipt
from app.formal_services import stock_loss_disposition_facts as dispositions
from app.formal_services import stock_loss_review_query as reviews, stock_loss_sources as sources
from app.formal_services import stock_return_outbound_facts as outbound
from app.formal_services import stock_return_shipment_facts as shipment
from app.formal_services import stock_return_receipt_facts as receipt
from app.formal_services import stock_return_inbound_facts as inbound
from app.formal_services.audit_chain import AuditChainError
from .historical_original import _bound
from .inverse_recovery import _authorize
from .return_progress import LineProgress, project_lines
from .return_quality import InboundConditionException, classification_exceptions

ROW_LIMIT = 20000


def invalid():
    sources._fail('loss_return_history_invalid', '原报损退回的履约关联或历史证据不完整', 503)


def changed():
    sources._fail('loss_return_history_changed', '核验期间退回履约或当前权限已变化，请重新查询', 409)


@dataclass(frozen=True)
class _RecordedOperator:
    # Historical coordinates only. Never pass this object to authorization.
    user_id: str
    person_id: UUID
    authorization_version: int


@dataclass(frozen=True)
class ReturnHistory:
    root_disposition_id: UUID
    operation_id: UUID
    observed_ledger_cursor: int
    evidence_fingerprint: str
    coordinates: tuple[tuple[str, tuple[UUID, ...]], ...]
    lines: tuple[LineProgress, ...]
    write_authorization_provided: bool = False
    classification_exceptions: tuple[InboundConditionException, ...] = ()


def _capture(db, root):
    """Include rows reached by either end of an edge, so misbindings cannot hide."""
    groups = {}

    def rows(name, model, where):
        found = tuple(db.scalars(select(model).where(where).order_by(model.id)
            .limit(ROW_LIMIT + 1).execution_options(populate_existing=True)))
        groups[name] = found
        if sum(map(len, groups.values())) > ROW_LIMIT:
            sources._fail('loss_return_history_limit', '退回履约超过完整核验范围，请联系总部核验', 503)
        return tuple(row.id for row in found)

    rows('root', models.StockLossDisposition, models.StockLossDisposition.id == root.id)
    operations = rows('orders', models.StockOperationOrder, models.StockOperationOrder.id == root.return_operation_id)
    lines = rows('lines', models.StockOperationLine, models.StockOperationLine.operation_id.in_(operations))
    rows('serials', models.StockOperationSerial, models.StockOperationSerial.line_id.in_(lines))
    rows('cancellations', models.StockOperationCancellation, models.StockOperationCancellation.operation_id.in_(operations))
    departures = rows('outbounds', models.StockOperationOutbound, models.StockOperationOutbound.operation_id.in_(operations))
    departing = rows('outbound_lines', models.StockOperationOutboundLine, or_(
        models.StockOperationOutboundLine.outbound_id.in_(departures), models.StockOperationOutboundLine.operation_line_id.in_(lines)))
    rows('outbound_serials', models.StockOperationOutboundSerial, models.StockOperationOutboundSerial.line_id.in_(departing))
    packages = rows('shipments', models.StockOperationShipment, models.StockOperationShipment.operation_id.in_(operations))
    rows('shipment_headers', Shipment, Shipment.id.in_(packages))
    shipped = rows('shipment_lines', models.StockOperationShipmentLine, or_(
        models.StockOperationShipmentLine.shipment_id.in_(packages), models.StockOperationShipmentLine.outbound_line_id.in_(departing)))
    rows('shipment_serials', models.StockOperationShipmentSerial, or_(
        models.StockOperationShipmentSerial.line_id.in_(shipped), models.StockOperationShipmentSerial.outbound_line_id.in_(departing)))
    receipts = rows('receipts', models.StockOperationReceipt, models.StockOperationReceipt.shipment_id.in_(packages))
    rows('receipt_headers', Receipt, Receipt.id.in_(receipts))
    received = rows('receipt_lines', models.StockOperationReceiptLine, or_(
        models.StockOperationReceiptLine.receipt_id.in_(receipts), models.StockOperationReceiptLine.shipment_line_id.in_(shipped)))
    rows('receipt_serials', models.StockOperationReceiptSerial, or_(
        models.StockOperationReceiptSerial.line_id.in_(received), models.StockOperationReceiptSerial.shipment_line_id.in_(shipped)))
    rows('receipt_exceptions', models.StockOperationReceiptException, or_(
        models.StockOperationReceiptException.receipt_id.in_(receipts), models.StockOperationReceiptException.line_id.in_(received)))
    inbounds = rows('inbounds', models.StockOperationReturnInbound, or_(
        models.StockOperationReturnInbound.shipment_id.in_(packages), models.StockOperationReturnInbound.receipt_id.in_(receipts)))
    incoming = rows('inbound_lines', models.StockOperationReturnInboundLine, or_(
        models.StockOperationReturnInboundLine.inbound_id.in_(inbounds), models.StockOperationReturnInboundLine.receipt_line_id.in_(received)))
    rows('inbound_serials', models.StockOperationReturnInboundSerial, or_(
        models.StockOperationReturnInboundSerial.inbound_id.in_(inbounds), models.StockOperationReturnInboundSerial.line_id.in_(incoming)))
    rows('inbound_postings', models.StockOperationReturnInboundPosting, models.StockOperationReturnInboundPosting.inbound_id.in_(inbounds))
    raw = {name: [{column.key: getattr(row, column.key) for column in row.__table__.columns}
        for row in values] for name, values in groups.items()}
    fingerprint = hashlib.sha256(json.dumps(raw, sort_keys=True, default=str, separators=(',', ':')).encode()).hexdigest()
    return groups, fingerprint


def _prove(db, root, groups):
    # Cancellation is still unsupported for a derived return. Preserve the
    # complete original proof instead of treating a cancellation ID as success.
    from .history_chain import verify_chain
    verify_chain(db, root_disposition_id=root.id)
    if groups['cancellations'] or len(groups['orders']) != 1 or not groups['lines']:
        invalid()
    for fact in groups['outbounds']:
        outbound.outbound_result(db, actor=_RecordedOperator(
            fact.actor_user_id, fact.operator_person_id, fact.authorization_version), fact=fact)
    for fact in groups['shipments']:
        shipment.verified_shipment_history(db, fact=fact)
        proved = receipt.verified_receipts(db, shipment_id=fact.id)
        expected = {r.id for r in groups['receipts'] if r.shipment_id == fact.id}
        if {r.receipt_id for r in proved} != expected:
            invalid()
    for fact in groups['inbounds']:
        # _proof is the same historical ledger/event verifier used by the
        # authorized inbound lookup; today's receiver login is not history.
        inbound._proof(db, fact)


def _verified_graph(db, root):
    """Historical proof only; callers separately establish current authority.

    Both headquarters reads and authorized compensation preparation need the
    same bidirectional association checks. A list of downstream header IDs is
    not enough to establish the absence of fulfillment facts.
    """
    try:
        groups, fingerprint = _capture(db, root)
        _prove(db, root, groups)
        progress = project_lines(groups)
        quality = classification_exceptions(groups)
    except (ValueError, TypeError, KeyError, AttributeError, InvalidOperation, AuditChainError):
        invalid()
    return groups, fingerprint, progress, quality


def read(db, *, actor, root_disposition_id):
    if type(root_disposition_id) is not UUID or root_disposition_id.int == 0:
        sources._fail('loss_return_history_identifier_invalid', '请选择准确原报损处置', 400)
    with db.no_autoflush:
        current, owners = reviews._scope(db, actor, 'headquarters')
        before = _bound(db)
        root = db.get(models.StockLossDisposition, root_disposition_id, populate_existing=True)
        order = db.get(models.StockOperationOrder, root.operation_id, populate_existing=True) if root else None
        if root is None or order is None:
            sources._fail('loss_return_history_not_found', '准确原报损处置不存在', 404)
        current = _authorize(db, current, order)
        if root.disposition != 'return_to_region' or root.return_operation_id is None:
            sources._fail('loss_return_history_not_return', '原处置不是报损派生退回', 412)
        groups, fingerprint, progress, quality = _verified_graph(db, root)
        coordinates = tuple((name, tuple(row.id for row in groups[name])) for name in
            ('outbounds', 'shipments', 'receipts', 'inbounds'))
        latest, latest_owners = reviews._scope(db, current, 'headquarters')
        latest = _authorize(db, latest, order)
        fresh = db.get(models.StockLossDisposition, root.id, populate_existing=True)
        if (fresh is None or latest != current or latest_owners != owners
                or _capture(db, fresh)[1] != fingerprint or _bound(db) != before):
            changed()
        return ReturnHistory(root.id, root.return_operation_id, before[0], fingerprint, coordinates, progress, classification_exceptions=quality)
