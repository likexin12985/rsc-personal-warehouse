"""Verify posted returns as demand evidence without granting warehouse access.

Internal only: callers authorize their current request scope. Historical
warehouse identities are evidence coordinates, never current principals.
"""
from dataclasses import dataclass
from decimal import Decimal
from sqlalchemy import or_, select

from app.demand_models import MaterialRequestLine, MaterialRequestRevision
from app.inventory_models import StockAccount, StockLocation, OutboundPosting
from app.material_request_rejection_return_schema import returns
from app.material_request_rejection_inbound_schema import inbounds
from . import material_request_rejection_inbound_facts as facts
from .material_request_rejection_history import verified_rejection_history
from .material_request_query import MaterialRequestReadError


def invalid():
    raise MaterialRequestReadError('material_request_return_posted_history_invalid', 'service_unavailable',
        '退回入账与原需求、批准明细或不可变库存证据不一致')


@dataclass(frozen=True)
class PostedReturn:
    row: dict
    origin: dict


def verified_posted_returns(db, *, request):
    """Full request evidence, bounded and all-or-nothing; no projection sums."""
    with db.no_autoflush:
        try:
            return _read(db, request)
        except (ValueError, TypeError, KeyError, AttributeError, ArithmeticError):
            invalid()


def _read(db, request):
    rows = tuple(db.execute(select(inbounds).outerjoin(returns, returns.c.id == inbounds.c.return_id)
        .where(or_(inbounds.c.request_id == request.id, returns.c.request_id == request.id))
        .order_by(inbounds.c.recorded_at, inbounds.c.id).limit(1001)).mappings())
    if len(rows) > 1000:
        raise MaterialRequestReadError('material_request_return_posted_history_limit', 'precondition_failed',
            '退回入账超出完整核验上限，未返回部分补偿数量')
    contexts = {}; originals = {}; totals = {}; used_serials = set(); result = {}
    for row in rows:
        parent_id = row['return_id']
        if parent_id not in contexts:
            parent = db.execute(select(returns).where(returns.c.id == parent_id)).mappings().one_or_none()
            if parent is None or parent['request_id'] != request.id:
                invalid()
            checked = verified_rejection_history(db, request=request, parent=parent)
            source = db.get(StockAccount, parent['return_source_account_id'], populate_existing=True)
            location = db.get(StockLocation, source.location_id, populate_existing=True) if source else None
            outbound = db.get(OutboundPosting, parent['outbound_posting_id'], populate_existing=True)
            line = db.get(MaterialRequestLine, outbound.request_line_id, populate_existing=True) if outbound else None
            revision = db.get(MaterialRequestRevision, parent['revision_id'], populate_existing=True)
            if (location is None or line is None or revision is None
                    or line.request_id != request.id or line.revision_id != parent['revision_id']
                    or revision.request_id != request.id or revision.revision_no != request.revision_no
                    or revision.status != 'sealed' or outbound.request_id != request.id
                    or outbound.revision_id != revision.id
                    or source.material_id != line.material_id):
                invalid()
            # Only indices 1,2,3,5 are consumed by historical receipt/inbound
            # verification. No call to warehouse _authorized or _authority.
            context = (None, parent, source, location, None, request, None)
            facts.acceptance._history(db, context)
            contexts[parent_id] = (context, line)
            originals[parent_id] = checked.origin
        context, line = contexts[parent_id]
        facts.verify(db, context, row)
        origin = originals[parent_id]
        receipt_line_id = origin['receipt_line_id']
        totals[receipt_line_id] = totals.get(receipt_line_id, Decimal(0)) + row['accepted_qty']
        if totals[receipt_line_id] > Decimal(origin['rejected_qty']):
            invalid()
        serial_ids = {s for piece in row['plan_jsonb']['parts'] for s in piece['serial_ids']}
        if serial_ids & used_serials:
            invalid()
        used_serials.update(serial_ids)
        result[row['id']] = PostedReturn(dict(row), dict(
            request_id=str(request.id), revision_id=str(line.revision_id), request_line_id=str(line.id),
            return_id=str(parent_id), original_receipt_line_id=receipt_line_id,
            warehouse_receipt_id=str(row['receipt_id']), inbound_id=str(row['id']),
            posting_transaction_id=str(row['posting_transaction_id']),
            inbound_request_hash=row['request_hash'], inbound_plan_hash=row['plan_hash'],
            accepted_qty=format(row['accepted_qty'], '.3f')))
    return result
