"""Derive the personal-warehouse state from immutable fulfillment facts.

The request column is only a read projection.  Quantities are compared per
request line so different materials can never cancel one another out.
"""
from decimal import Decimal

from sqlalchemy import String, case, cast, exists, func, select

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
    quantity = ReceiptLine.accepted_qty
    if posted_only:
        posted_receipt = exists(
            select(1)
            .select_from(InboundOrder)
            .join(InboundPosting, InboundPosting.inbound_order_id == InboundOrder.id)
            .join(
                InventoryTransaction,
                InventoryTransaction.id == InboundPosting.inventory_transaction_id,
            )
            .where(
                InboundOrder.receipt_id == ReceiptLine.receipt_id,
                InventoryTransaction.status == "posted",
                InventoryTransaction.movement_type == "transfer",
                InventoryTransaction.source_document_type == "personal_inbound",
                func.replace(cast(InboundOrder.id, String), "-", "")
                == func.replace(InventoryTransaction.source_document_id, "-", ""),
            )
        )
        # EXISTS preserves one receipt-line quantity even if a malformed
        # historical receipt has multiple order/posting bindings.
        quantity = case((posted_receipt, quantity), else_=0)
    query = (
        select(func.coalesce(func.sum(quantity), 0))
        .select_from(ReceiptLine)
        .join(Receipt, Receipt.id == ReceiptLine.receipt_id)
        .join(ShipmentLine, ShipmentLine.id == ReceiptLine.shipment_line_id)
        .join(OutboundPosting, OutboundPosting.id == ShipmentLine.outbound_posting_id)
        .where(
            OutboundPosting.request_id == request_id,
            OutboundPosting.request_line_id == request_line_id,
            Receipt.status.in_(("accepted", "exception")),
        )
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

    line_totals = []
    shipped = False
    for line in lines:
        net = Decimal(line.final_approved_qty) - Decimal(line.cancelled_qty)
        accepted_line = _accepted_total(
            db, request_id=request.id, request_line_id=line.id
        )
        posted_line = _accepted_total(
            db, request_id=request.id, request_line_id=line.id, posted_only=True
        )
        line_totals.append((net, accepted_line, posted_line))
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

    if all(posted_line >= net for net, _, posted_line in line_totals):
        return "posted"
    if all(accepted_line >= net for net, accepted_line, _ in line_totals):
        return "accepted"
    if any(accepted_line > _ZERO for _, accepted_line, _ in line_totals):
        return "partially_accepted"
    return "pending_acceptance" if shipped else "not_started"


def refresh_personal_inbound_status(db, request: MaterialRequest) -> str:
    """Update the projection and advance request version only on a change."""
    status = personal_inbound_state(db, request)
    if request.personal_inbound_status != status:
        request.personal_inbound_status = status
        request.version += 1
    return status
