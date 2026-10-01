"""Exact downstream coordinates for loss-return compensation preparation.

Presence means that separate business stage must be accounted for; it is not
proof of a completed compensation. No cancellation, inverse or replay occurs.
Authorization and complete historical proofs remain the caller's obligation.
"""
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select

from app.foundation_models import AuditChainHead
from app.inventory_models import InventoryTransaction
from app.stock_operation_models import (
    StockLossDisposition, StockOperationOrder, StockOperationCancellation,
    StockOperationOutbound, StockOperationShipment, StockOperationReceipt, StockOperationReturnInbound,
)
from app.formal_services import stock_loss_sources as sources


@dataclass(frozen=True)
class ReturnDependencies:
    disposition_id: UUID
    operation_id: UUID
    cancellations: tuple[UUID, ...]
    outbounds: tuple[UUID, ...]
    shipments: tuple[UUID, ...]
    receipts: tuple[UUID, ...]
    inbounds: tuple[UUID, ...]

    @property
    def has_downstream_facts(self):
        return any((self.cancellations, self.outbounds, self.shipments, self.receipts, self.inbounds))

    def document(self):
        return dict(disposition_id=str(self.disposition_id), operation_id=str(self.operation_id),
            **{name:list(map(str, getattr(self, name))) for name in
                ('cancellations', 'outbounds', 'shipments', 'receipts', 'inbounds')})


def _bound(db):
    return (db.scalar(select(func.max(InventoryTransaction.ledger_cursor))), tuple(db.execute(select(
        AuditChainHead.stream_key, AuditChainHead.version, AuditChainHead.last_event_id, AuditChainHead.last_hash)
        .where(AuditChainHead.stream_key.in_(('inventory', 'material_request'))).order_by(AuditChainHead.stream_key))))


def read(db, *, disposition_id):
    if type(disposition_id) is not UUID or disposition_id.int == 0:
        raise ValueError('exact loss disposition id required')
    with db.no_autoflush:
        before = _bound(db)
        root = db.get(StockLossDisposition, disposition_id, populate_existing=True)
        child = db.get(StockOperationOrder, root.return_operation_id, populate_existing=True) if root and root.return_operation_id else None
        if (root is None or root.disposition != 'return_to_region' or child is None
                or child.operation_type != 'return' or child.oam_work_order_id is not None
                or child.loss_headquarters_decision_id != root.headquarters_decision_id
                or child.posting_transaction_id != root.posting_transaction_id):
            sources._fail('loss_return_dependency_origin_invalid', '原报损派生退回关系不完整', 503)
        def capture():
            packages = select(StockOperationShipment.id).where(StockOperationShipment.operation_id == child.id)
            coordinates = (
                (StockOperationCancellation, StockOperationCancellation.operation_id == child.id),
                (StockOperationOutbound, StockOperationOutbound.operation_id == child.id),
                (StockOperationShipment, StockOperationShipment.operation_id == child.id),
                (StockOperationReceipt, StockOperationReceipt.shipment_id.in_(packages)),
                (StockOperationReturnInbound, StockOperationReturnInbound.shipment_id.in_(packages)),
            )
            return tuple(tuple(db.scalars(select(model.id).where(where).order_by(model.id)))
                for model, where in coordinates)
        stages = capture()
        if capture() != stages or _bound(db) != before:
            sources._fail('loss_return_dependency_read_changed', '读取期间退回履约已变化，请重新核验', 409)
        return ReturnDependencies(root.id, child.id, *stages)
