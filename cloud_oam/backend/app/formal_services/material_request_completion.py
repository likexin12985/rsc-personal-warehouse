"""Authorized, read-only final-approval/posted/cancelled quantity assessment.

No close permission or business-closure fact is returned. A close command must
also lock the request, validate unfinished fulfillment and append an immutable
closure fact with database enforcement. Reading this endpoint never does that.
"""
from decimal import Decimal
from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.demand_models import MaterialRequest, MaterialRequestRevision
from app.inventory_models import InboundOrder, InboundPosting, Receipt, ShipmentLine, OutboundPosting
from app.material_request_completion_schemas import MaterialRequestCompletionOut
from . import material_request_query as query
from .material_request_approval_history import verified_final_approved_quantities
from .material_request_cancellation_history import verified_cancelled_quantities
from .material_request_inbound_history import verified_posted_receipt_lines
from .material_request_closure_coverage import line_coverage


def completion_quantities(db, *, actor, request_id, _include_returns=False):
    with db.no_autoflush:
        try:
            return _read(db, actor, request_id, include_returns=_include_returns)
        except query.MaterialRequestReadError:
            raise
        except (DBAPIError, ValueError, TypeError):
            raise query.MaterialRequestReadError('completion_evidence_unavailable', 'service_unavailable',
                '结单数量证据暂时无法完整核验，请重新读取') from None


def _read(db, actor, request_id, *, include_returns=False):
    context = query._load_read_context(db, actor=actor, now=None)
    request = db.scalar(select(MaterialRequest).where(MaterialRequest.id == request_id,
        query._visible_request_predicate(context)).execution_options(populate_existing=True))
    if request is None:
        raise query.MaterialRequestReadError('material_request_not_found', 'not_found', '需求单不存在或不在当前读取范围内')
    if request.status not in {'approved', 'partially_approved', 'cancelled'} or request.decided_at is None:
        raise query.MaterialRequestReadError('completion_final_approval_required', 'precondition_failed',
            '需求尚未形成最终批准数量')
    snapshot = query._snapshots((request,))
    result = quantity_evidence(db, request=request, include_returns=include_returns)
    query._ensure_requests_current(db, snapshot)
    # Re-evaluate role expiry, explicit deny and organization ancestry too.
    fresh = query._load_read_context(db, actor=context.principal, now=None)
    if db.scalar(select(MaterialRequest.id).where(MaterialRequest.id == request_id,
            query._visible_request_predicate(fresh))) is None:
        raise query.MaterialRequestReadError('completion_scope_changed', 'forbidden', '读取期间需求授权范围已变化')
    return result


def quantity_evidence(db, *, request, include_returns=False):
    """Internal history proof after caller authorization, also used for recovery.

    The forward mode requires the compensation schema; missing tables fail
    closed. Public routes remain on the formal 0175 contract until activation.
    """
    revision = db.scalar(select(MaterialRequestRevision).where(
        MaterialRequestRevision.request_id == request.id,
        MaterialRequestRevision.revision_no == request.revision_no))
    approved = verified_final_approved_quantities(db, request=request)
    cancelled = verified_cancelled_quantities(db, request=request)
    if include_returns:
        from .material_request_return_compensation import verified_return_compensated_quantities
        compensated = verified_return_compensated_quantities(db, request=request)
        if (request.status == 'cancelled' and compensated) or set(compensated) - set(approved):
            raise query.MaterialRequestReadError('completion_cancellation_conflict', 'service_unavailable',
                '退回补偿与整单取消或批准明细不一致')
        cancelled = {key: cancelled.get(key, Decimal(0)) + compensated.get(key, Decimal(0)) for key in approved}
    posted, pending = posted_evidence(db, request=request)
    coverage = line_coverage(approved_by_line=approved, cancelled_by_line=cancelled,
        posted_receipt_lines=posted)
    return MaterialRequestCompletionOut(request_id=request.id, request_version=request.version,
        revision_id=revision.id, quantity_coverage_complete=all(row.remaining_qty == 0 for row in coverage),
        pending_inbound_orders=pending,
        lines=tuple({key: getattr(row, key) if key == 'request_line_id' else format(getattr(row, key), '.3f')
            for key in ('request_line_id', 'approved_qty', 'cancelled_qty', 'posted_qty', 'remaining_qty')}
            for row in coverage))


def posted_evidence(db, *, request):
    """Independent personal-inbound proof; does not read cancellation history."""
    shipment_ids = select(ShipmentLine.shipment_id).join(OutboundPosting,
        OutboundPosting.id == ShipmentLine.outbound_posting_id).where(OutboundPosting.request_id == request.id)
    orders = tuple(db.scalars(select(InboundOrder).join(Receipt, Receipt.id == InboundOrder.receipt_id)
        .where(Receipt.shipment_id.in_(shipment_ids)).order_by(InboundOrder.id).limit(1001)
        .execution_options(populate_existing=True)))
    if len(orders) > 1000:
        raise query.MaterialRequestReadError('completion_limit_exceeded', 'precondition_failed',
            '入账记录超过单次完整核验上限，未返回部分数量')
    posted, pending = [], 0
    for order in orders:
        if db.scalar(select(InboundPosting.id).where(InboundPosting.inbound_order_id == order.id)) is None:
            if order.status != 'pending' or order.posting_transaction_id is not None:
                raise query.MaterialRequestReadError('completion_posting_missing', 'service_unavailable',
                    '入账状态缺少对应的库存过账事实')
            pending += 1
        else:
            posted.extend(verified_posted_receipt_lines(db, request=request, order=order))
    return posted, pending
