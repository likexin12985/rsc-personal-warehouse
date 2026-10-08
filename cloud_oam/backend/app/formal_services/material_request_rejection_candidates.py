"""Selectable original refusal sources and independently verified return history."""
from decimal import Decimal
from sqlalchemy import select

from app.material_request_rejection_http_schemas import (RejectionReturnCandidatesOut,
    RejectionReturnCandidateOut, RejectionReturnLineOut, RejectionReturnHistoryOut)
from app.material_request_rejection_progress_schemas import RejectionProgressStateOut
from . import material_request_rejection_return as registration
from . import material_request_rejection_progress as progress
from . import material_request_lifecycle as lifecycle
from . import material_request_query as query
from .work_order_evidence_snapshot import material_audit_cursor


def _permitted(check):
    try:
        check()
        return True
    except (query.MaterialRequestReadError, lifecycle.MaterialRequestLifecycleError) as exc:
        if exc.category != 'forbidden':
            raise
        return False


def candidates(db, *, actor, request_id, limit=5, after_id=None):
    with db.no_autoflush:
        context, request = registration._context(db, actor, request_id)
        actor = context.principal
        snapshot = query._snapshots((request,))
        cursor = material_audit_cursor(db)
        page = registration.history.list_my_inbound_candidates(db, actor=actor, request_id=request_id,
            limit=limit, after_id=after_id)
        open_request = (request.status in ('approved', 'partially_approved')
            and db.scalar(select(registration.closures.c.id).where(registration.closures.c.request_id == request.id)) is None
            and db.scalar(select(registration.cancellations.c.id).where(registration.cancellations.c.request_id == request.id)) is None)
        register_allowed = open_request and _permitted(lambda: registration._authority(db, actor, request))
        allowed = {action: open_request and _permitted(lambda: progress._authority(db, actor, request, action))
            for action in progress.PERMISSIONS}
        items = []
        for item in page.items:
            if item.status == 'blocked':
                items.append(RejectionReturnCandidateOut(receipt_id=item.receipt_id, status='blocked',
                    message=item.message, detail=None, lines=()))
                continue
            lines = []
            for line in item.detail.lines:
                if Decimal(line.rejected_qty) <= 0:
                    continue
                rows = tuple(db.execute(select(registration.returns).where(
                    registration.returns.c.receipt_line_id == line.receipt_line_id)
                    .order_by(registration.returns.c.id).limit(1001)).mappings())
                if len(rows) > 1000:
                    registration._fail('history_limit', 'precondition_failed', '该拒收行的退回记录超过完整核验上限')
                histories = []
                used_qty, used_ids = Decimal(0), set()
                for row in rows:
                    original = registration._result(db, context, request, row, replayed=True)
                    events = progress._checked_chain(db, context, request, row)
                    status = progress.STATES[events[-1].action] if events else 'registered'
                    actions = ('cancel_registration', 'depart') if status == 'registered' else ('handover',) if status == 'departed' else ()
                    histories.append(RejectionReturnHistoryOut(registration=original,
                        progress=RejectionProgressStateOut(return_id=original.return_id, request_id=request.id,
                            registration_request_hash=original.request_hash, status=status, events=events),
                        permitted_actions=tuple(action for action in actions if allowed[action])))
                    if status != 'cancelled':
                        used_qty += Decimal(original.quantity)
                        if used_ids.intersection(original.serial_ids):
                            registration._fail('history_invalid', 'service_unavailable', '有效退回记录重复占用同一SN')
                        used_ids.update(original.serial_ids)
                available = Decimal(line.rejected_qty) - used_qty
                if available < 0 or not used_ids <= {s.serial_id for s in line.rejected_serials}:
                    registration._fail('history_invalid', 'service_unavailable', '退回登记累计量与原拒收不一致')
                lines.append(RejectionReturnLineOut(receipt_line_id=line.receipt_line_id,
                    available_qty=format(available, '.3f'), available_serials=tuple(s for s in line.rejected_serials if s.serial_id not in used_ids),
                    register_permitted=register_allowed and available > 0, registrations=tuple(histories)))
            items.append(RejectionReturnCandidateOut(receipt_id=item.receipt_id, status='verified',
                message='原拒收及退回进展已核验', detail=item.detail, lines=tuple(lines)))
        latest, _ = registration._context(db, actor, request_id)
        if latest.principal != actor or material_audit_cursor(db) != cursor:
            registration._fail('context_changed', 'precondition_failed', '读取期间身份或退回记录发生变化')
        query._ensure_requests_current(db, snapshot)
        return RejectionReturnCandidatesOut(request_id=request.id, request_version=request.version,
            items=tuple(items), next_after_id=page.next_after_id)
