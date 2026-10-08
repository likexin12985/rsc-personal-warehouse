"""Forward quantity partition; never reinterpret the seven-bucket 0171 evidence."""
from decimal import Decimal
from uuid import UUID
from app.material_request_return_compensation_schemas import ReturnedRemainderLineOut, ReturnedRemainderAssessmentOut
from .material_request_remainder import partition, _invalid


def partition_returned(*, returned, return_compensated, **original):
    # 'cancelled' in original is exclusively the unfulfilled cancellation amount.
    old = partition(**original)
    return apply_returned(old, returned=returned, return_compensated=return_compensated)


def apply_returned(old, *, returned, return_compensated):
    if any(not isinstance(v, Decimal) or not v.is_finite() or v < 0
           or v != v.quantize(Decimal('.001')) for v in (returned, return_compensated)):
        _invalid()
    if not return_compensated <= returned <= Decimal(old.rejected_unsettled_qty):
        _invalid('退回补偿不得超过实际仓库入账，仓库入账不得超过原拒收')
    return ReturnedRemainderLineOut(**(old.model_dump() | dict(
        unfulfilled_cancelled_qty=old.cancelled_qty,
        return_compensated_qty=format(return_compensated, '.3f'),
        cancelled_qty=format(Decimal(old.cancelled_qty) + return_compensated, '.3f'),
        rejected_unsettled_qty=format(Decimal(old.rejected_unsettled_qty) - returned, '.3f'),
        returned_pending_compensation_qty=format(returned - return_compensated, '.3f'))))


def remaining_with_returns(db, *, actor, request_id):
    """Forward authorized assessment. Original 0171 evidence still uses v1."""
    from . import material_request_remaining_cancel as original
    from . import material_request_query as query
    from .material_request_return_compensation import verified_compensations
    from .material_request_return_posted_history import verified_posted_returns
    with db.no_autoflush:
        actor, request = original._context(db, actor, request_id)
        snapshot = query._snapshots((request,))
        old = original.remaining_fulfillment(db, actor=actor, request_id=request.id)
        sources = verified_posted_returns(db, request=request)
        compensations = verified_compensations(db, request=request, sources=sources)
        returned, compensated = {}, {}
        for source in sources.values():
            key = UUID(source.origin['request_line_id'])
            returned[key] = returned.get(key, Decimal(0)) + source.row['accepted_qty']
        for fact in compensations:
            key = fact.request_line_id
            compensated[key] = compensated.get(key, Decimal(0)) + Decimal(fact.cancelled_qty)
        if (set(returned) | set(compensated)) - {r.request_line_id for r in old.lines}:
            _invalid('退回或补偿不属于当前批准明细')
        result = ReturnedRemainderAssessmentOut(**(old.model_dump(exclude={'schema_version', 'lines'}) | dict(
            lines=tuple(apply_returned(line, returned=returned.get(line.request_line_id, Decimal(0)),
                return_compensated=compensated.get(line.request_line_id, Decimal(0))) for line in old.lines))))
        original._context(db, actor, request_id)
        query._ensure_requests_current(db, snapshot)
        return result
