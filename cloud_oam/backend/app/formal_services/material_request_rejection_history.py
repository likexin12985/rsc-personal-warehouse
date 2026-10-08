"""Original refusal/return facts, independent of the sender's current login.

Internal verifier only: callers authorize their own current read/write scope.
The historical subject contains identity coordinates, never current grants.
"""
from dataclasses import dataclass
from datetime import timezone
from sqlalchemy import or_, select

from app.inventory_models import MaterialInventoryPolicy, Receipt, ShipmentLine, OutboundPosting, StockAccount
from app.material_request_rejection_return_schemas import RejectionReturnIn, RejectionReturnOut
from app.material_request_rejection_progress_schemas import RejectionProgressOut
from . import material_request_my_receipt as receipts
from . import material_request_rejection_return as registration
from . import material_request_rejection_progress as progress


@dataclass(frozen=True)
class VerifiedRejectionHistory:
    registration: RejectionReturnOut
    events: tuple[RejectionProgressOut, ...]
    origin: dict


def verified_rejection_history(db, *, request, parent):
    with db.no_autoflush:
        try:
            payload = RejectionReturnIn.model_validate(parent['evidence_jsonb']['input'])
            if parent['request_id'] != request.id:
                raise ValueError('request mismatch')
            receipt = db.get(Receipt, payload.receipt_id, populate_existing=True)
            if receipt is None:
                raise ValueError('receipt missing')
            original = receipts.verified_receipt_history(db, request=request, receipt=receipt)
            line = next(x for x in original.lines if x.receipt_line_id == payload.receipt_line_id)
            shipment_line = db.get(ShipmentLine, line.shipment_line_id)
            outbound = db.get(OutboundPosting, shipment_line.outbound_posting_id)
            transit = db.get(StockAccount, outbound.target_stock_account_id)
            at = receipt.received_at
            at = at.replace(tzinfo=timezone.utc) if at.tzinfo is None else at.astimezone(timezone.utc)
            policies = tuple(db.scalars(select(MaterialInventoryPolicy).where(
                MaterialInventoryPolicy.material_id == transit.material_id,
                MaterialInventoryPolicy.effective_from <= at,
                or_(MaterialInventoryPolicy.effective_to.is_(None), MaterialInventoryPolicy.effective_to > at),
            ).limit(2)))
            if len(policies) != 1:
                raise ValueError('historical policy ambiguous')
            origin = registration._origin_facts(db, request, payload, original, policies[0].tracking_mode)
            subject = receipts._ReceiptSubject(request.requester_user_id, request.requester_person_id)
            result = registration._registration_result(db, subject, request, parent, origin, replayed=True)
            events = progress._verified_chain(db, subject, request, parent)
            return VerifiedRejectionHistory(result, events, origin)
        except (ValueError, TypeError, KeyError, StopIteration, AttributeError):
            registration._fail('history_invalid', 'service_unavailable', '原拒收、退回登记或退运历史证据不完整')
