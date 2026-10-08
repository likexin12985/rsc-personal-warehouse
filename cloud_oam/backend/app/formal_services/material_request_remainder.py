"""Read exact, disjoint remaining quantities before compensation.

This never grants cancellation or releases stock. Approved/cancelled/posted
coverage and fulfillment histories are checked independently. A future command
must reacquire the request lock and prove all compensation in its transaction.
"""
from decimal import Decimal
from types import SimpleNamespace

from sqlalchemy import select
from sqlalchemy.exc import DBAPIError

from app.demand_models import MaterialRequest, MaterialRequestLine, MaterialRequestCommand, SupplyTask, SubstitutionDecision
from app.foundation_models import AuditEvent
from app.inventory_models import (StockReservation, StockReservationRelease, StockReservationPick,
    OutboundPosting, Shipment, ShipmentLine, Receipt, ReceiptLine, InventoryTransaction)
from app.material_request_remainder_schemas import RemainderLineOut, RemainderAssessmentOut
from . import material_request_query as query
from . import material_request_reservation as reserve
from . import material_request_reservation_release as release
from . import material_request_picking as picking
from . import material_request_outbound as outbound
from .material_request_completion import completion_quantities
from .material_request_fulfillment_command import verify_fulfillment_command
from .audit_chain import AuditChainError, verify_audit_event_in_read_snapshot

ZERO = Decimal('0.000')


def _invalid(message='剩余履约数量与原始事实不一致，未返回部分结果'):
    raise query.MaterialRequestReadError('material_request_remainder_invalid', 'service_unavailable', message)


def partition(*, line_id, approved, cancelled, posted, reserved, released, picked, dispatched, shipped, accepted, rejected):
    """Partition one line; never clamp contradictory facts to zero."""
    values = (approved, cancelled, posted, reserved, released, picked, dispatched, shipped, accepted, rejected)
    if any(not isinstance(v, Decimal) or not v.is_finite() or v < 0 or v != v.quantize(Decimal('.001')) for v in values):
        _invalid()
    if not (released + picked <= reserved <= approved
            and reserved - released <= approved - cancelled
            and posted <= accepted and accepted + rejected <= shipped <= dispatched <= picked):
        _invalid()
    return RemainderLineOut(request_line_id=line_id, **{k: format(v, '.3f') for k, v in {
        'approved_qty': approved, 'cancelled_qty': cancelled, 'posted_qty': posted,
        'unreserved_qty': approved - cancelled - reserved + released,
        'reserved_unpicked_qty': reserved - released - picked,
        'picked_unoutbound_qty': picked - dispatched,
        'outbound_unshipped_qty': dispatched - shipped,
        'shipped_unreceived_qty': shipped - accepted - rejected,
        'accepted_unposted_qty': accepted - posted,
        'rejected_unsettled_qty': rejected,
    }.items()})


def _rows(db, model, *where):
    rows = tuple(db.scalars(select(model).where(*where).order_by(model.id).limit(1001)
        .execution_options(populate_existing=True)))
    if len(rows) > 1000:
        raise query.MaterialRequestReadError('material_request_remainder_limit', 'precondition_failed',
            '履约事实超过完整核验上限，未返回部分数量')
    return rows


def _fulfillment(db, request, fact, operation):
    commands = _rows(db, MaterialRequestCommand, MaterialRequestCommand.idempotency_key_hash == fact.idempotency_key_hash)
    references = {f'/api/v1/material-requests/{request.id}/shipments'} if operation == 'shipment' else {
        f'/api/v1/material-requests/{request.id}/receipts', f'/api/v1/material-requests/{request.id}/my-receipts'}
    if len(commands) != 1 or commands[0].request_reference not in references or not 0 < commands[0].target_version <= request.version:
        _invalid('履约事实缺少当前需求的原始版本命令')
    command = commands[0]
    verify_fulfillment_command(db, request=request, actor=SimpleNamespace(user_id=command.actor_user_id,
        person_id=command.actor_person_id), operation=operation, fact=fact,
        request_reference=command.request_reference, expected_version=command.target_version)
    audits = _rows(db, AuditEvent, AuditEvent.action == 'fulfillment_version_recorded',
        AuditEvent.aggregate_type == operation, AuditEvent.aggregate_id == str(fact.id))
    if len(audits) != 1:
        _invalid()
    verify_audit_event_in_read_snapshot(db, stream_key='material_request', event_id=audits[0].id)
    return command.target_version


def remaining_fulfillment(db, *, actor, request_id):
    with db.no_autoflush:
        try:
            return _read(db, actor, request_id)
        except query.MaterialRequestReadError:
            raise
        except (DBAPIError, AuditChainError, reserve.MaterialRequestReservationError,
                ValueError, TypeError, KeyError, ArithmeticError):
            _invalid()


def _read(db, actor, request_id):
    context = query._load_read_context(db, actor=actor, now=None)
    request = db.scalar(select(MaterialRequest).where(MaterialRequest.id == request_id,
        query._visible_request_predicate(context)).execution_options(populate_existing=True))
    if request is None:
        raise query.MaterialRequestReadError('material_request_not_found', 'not_found', '需求单不存在或不在当前范围内')
    snapshot = query._snapshots((request,))
    coverage = completion_quantities(db, actor=context.principal, request_id=request.id)
    lines = {row.request_line_id: row for row in coverage.lines}
    reservations, releases, picks, postings = (_rows(db, model, model.request_id == request.id) for model in
        (StockReservation, StockReservationRelease, StockReservationPick, OutboundPosting))
    for records, verify, tx_field in ((reservations, reserve._verified_history, 'reserve_transaction_id'),
            (releases, release.verified_release_history, 'release_transaction_id'),
            (picks, picking.verified_pick_history, 'pick_transaction_id'),
            (postings, outbound.verified_outbound_history, 'outbound_transaction_id')):
        for fact in records:
            if fact.request_line_id not in lines or fact.revision_id != coverage.revision_id:
                _invalid('履约明细不属于当前需求修订')
            verify(db, fact=fact, request=request, lock_audit=False)
            tx_id = getattr(fact, tx_field)
            if db.scalar(select(InventoryTransaction.id).where(InventoryTransaction.reversed_transaction_id == tx_id).limit(1)) is not None:
                raise query.MaterialRequestReadError('material_request_remainder_reversed', 'precondition_failed',
                    '履约已有冲销事实，需要先核验完整补偿链')
    res_by_id = {r.id: r for r in reservations}; pick_by_id = {r.id: r for r in picks}
    for records, parents, field in ((releases, res_by_id, 'reservation_id'), (picks, res_by_id, 'reservation_id'),
                                   (postings, pick_by_id, 'pick_id')):
        for fact in records:
            parent = parents.get(getattr(fact, field))
            if parent is None or parent.request_line_id != fact.request_line_id or parent.request_version >= fact.request_version:
                _invalid('履约上下游明细或版本不一致')
    for r in reservations:
        if sum((x.released_qty for x in releases if x.reservation_id == r.id), ZERO) + sum((x.picked_qty for x in picks if x.reservation_id == r.id), ZERO) > r.reserved_qty:
            _invalid()
    for p in picks:
        if sum((x.outbound_qty for x in postings if x.pick_id == p.id), ZERO) > p.picked_qty:
            _invalid()
    post_by_id = {r.id: r for r in postings}
    shipped = _rows(db, ShipmentLine, ShipmentLine.outbound_posting_id.in_(post_by_id)) if postings else ()
    ships = _rows(db, Shipment, Shipment.id.in_({s.shipment_id for s in shipped})) if shipped else ()
    ship_versions = {s.id: _fulfillment(db, request, s, 'shipment') for s in ships}
    for p in postings:
        if sum((s.shipped_qty for s in shipped if s.outbound_posting_id == p.id), ZERO) > p.outbound_qty:
            _invalid()
    for s in shipped:
        if s.shipment_id not in ship_versions or ship_versions[s.shipment_id] <= post_by_id[s.outbound_posting_id].request_version:
            _invalid()
    receipts = _rows(db, Receipt, Receipt.shipment_id.in_(ship_versions)) if ships else ()
    receipt_versions = {r.id: _fulfillment(db, request, r, 'receipt') for r in receipts}
    received = _rows(db, ReceiptLine, ReceiptLine.receipt_id.in_(receipt_versions)) if receipts else ()
    shipped_by_id = {s.id: s for s in shipped}; receipt_by_id = {r.id: r for r in receipts}
    for row in received:
        source = shipped_by_id.get(row.shipment_line_id); receipt = receipt_by_id[row.receipt_id]
        if source is None or source.shipment_id != receipt.shipment_id or receipt_versions[receipt.id] <= ship_versions[source.shipment_id]:
            _invalid('验收明细的发运来源或因果顺序不一致')
    for source in shipped:
        if sum((r.accepted_qty + r.rejected_qty for r in received if r.shipment_line_id == source.id), ZERO) > source.shipped_qty:
            _invalid()
    result = []
    for line_id, coverage_line in lines.items():
        outgoing_ids = {p.id for p in postings if p.request_line_id == line_id}
        shipment_ids = {s.id for s in shipped if s.outbound_posting_id in outgoing_ids}
        qty = lambda records, field: sum((getattr(r, field) for r in records if r.request_line_id == line_id), ZERO)
        result.append(partition(line_id=line_id, approved=Decimal(coverage_line.approved_qty),
            cancelled=Decimal(coverage_line.cancelled_qty), posted=Decimal(coverage_line.posted_qty),
            reserved=qty(reservations, 'reserved_qty'), released=qty(releases, 'released_qty'),
            picked=qty(picks, 'picked_qty'), dispatched=qty(postings, 'outbound_qty'),
            shipped=sum((s.shipped_qty for s in shipped if s.id in shipment_ids), ZERO),
            accepted=sum((r.accepted_qty for r in received if r.shipment_line_id in shipment_ids), ZERO),
            rejected=sum((r.rejected_qty for r in received if r.shipment_line_id in shipment_ids), ZERO)))
    supply = _rows(db, SupplyTask, SupplyTask.request_line_id.in_(lines), SupplyTask.status.not_in(('cancelled', 'closed_no_supply')))
    substitutions = _rows(db, SubstitutionDecision, SubstitutionDecision.request_line_id.in_(lines), SubstitutionDecision.status == 'proposed')
    query._ensure_requests_current(db, snapshot)
    fresh = query._load_read_context(db, actor=context.principal, now=None)
    if db.scalar(select(MaterialRequest.id).where(MaterialRequest.id == request_id, query._visible_request_predicate(fresh))) is None:
        raise query.MaterialRequestReadError('material_request_remainder_scope_changed', 'forbidden', '核验期间需求范围已变化')
    return RemainderAssessmentOut(request_id=request.id, revision_id=coverage.revision_id, request_version=coverage.request_version,
        open_supply_tasks=len(supply), pending_substitutions=len(substitutions), lines=tuple(result))
