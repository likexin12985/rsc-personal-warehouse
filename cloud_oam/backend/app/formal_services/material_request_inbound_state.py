"""Derive the personal-warehouse state from immutable fulfillment facts.

The request column is only a read projection.  Quantities are compared per
request line so different materials can never cancel one another out.
"""
from decimal import Decimal

from sqlalchemy import func, select

from ..demand_models import MaterialRequest, MaterialRequestLine
from ..inventory_models import (
    InboundOrder,
    InboundPosting,
    InventoryTransaction,
    OutboundPosting,
    Receipt,
    ReceiptLine,
    ShipmentLine,
)

_ZERO = Decimal("0.000")


def _accepted_total(db, *, request_id, request_line_id, posted_only=False):
    query = (
        select(func.coalesce(func.sum(ReceiptLine.accepted_qty), 0))
        .join(Receipt, Receipt.id == ReceiptLine.receipt_id)
        .join(ShipmentLine, ShipmentLine.id == ReceiptLine.shipment_line_id)
        .join(OutboundPosting, OutboundPosting.id == ShipmentLine.outbound_posting_id)
        .where(
            OutboundPosting.request_id == request_id,
            OutboundPosting.request_line_id == request_line_id,
            Receipt.status.in_(("accepted", "exception")),
        )
    )
    if posted_only:
        query = query.join(
            InboundOrder, InboundOrder.receipt_id == Receipt.id
        ).join(
            InboundPosting, InboundPosting.inbound_order_id == InboundOrder.id
        ).join(
            InventoryTransaction,
            InventoryTransaction.id == InboundPosting.inventory_transaction_id,
        ).where(
            InventoryTransaction.status == "posted",
            InventoryTransaction.movement_type == "transfer",
            InventoryTransaction.source_document_type == "personal_inbound",
        )
    return Decimal(db.scalar(query) or _ZERO)


def personal_inbound_state(db, request: MaterialRequest) -> str:
    """Return the V1.0 state for the current approved request lines.

    ``posted`` requires every positive net-approved line to be fully posted;
    ``accepted`` means all lines are accepted but at least one remains to post;
    ``partially_accepted`` means some accepted quantity exists; and
    ``pending_acceptance`` means the request has shipped quantity with no
    accepted quantity yet.  A fully cancelled request is not vacuously posted.
    """
    lines = tuple(
        db.scalars(
            select(MaterialRequestLine).where(
                MaterialRequestLine.request_id == request.id,
                MaterialRequestLine.revision_no == request.revision_no,
                MaterialRequestLine.final_approved_qty > MaterialRequestLine.cancelled_qty,
            )
        ).all()
    )
    if not lines:
        return "not_started"

    expected = _ZERO
    accepted = _ZERO
    posted = _ZERO
    shipped = False
    for line in lines:
        net = Decimal(line.final_approved_qty) - Decimal(line.cancelled_qty)
        expected += net
        accepted_line = _accepted_total(
            db, request_id=request.id, request_line_id=line.id
        )
        posted_line = _accepted_total(
            db, request_id=request.id, request_line_id=line.id, posted_only=True
        )
        accepted += accepted_line
        posted += posted_line
        if db.scalar(
            select(ShipmentLine.id)
            .join(OutboundPosting, OutboundPosting.id == ShipmentLine.outbound_posting_id)
            .where(
                OutboundPosting.request_id == request.id,
                OutboundPosting.request_line_id == line.id,
                ShipmentLine.shipped_qty > 0,
            )
            .limit(1)
        ) is not None:
            shipped = True

    if expected <= _ZERO:
        return "not_started"
    if posted == expected:
        return "posted"
    if accepted == expected:
        return "accepted"
    if accepted > _ZERO:
        return "partially_accepted"
    return "pending_acceptance" if shipped else "not_started"


def refresh_personal_inbound_status(db, request: MaterialRequest) -> str:
    """Update the projection and advance request version only on a change."""
    status = personal_inbound_state(db, request)
    if request.personal_inbound_status != status:
        request.personal_inbound_status = status
        request.version += 1
    return status
