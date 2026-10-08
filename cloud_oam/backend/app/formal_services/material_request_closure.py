"""Explicit closure command; never rewrites approval or inventory history.

The caller owns the transaction. Formal 0169 guards enforce the same closure
requirements independently in PostgreSQL. Reads never acquire write locks.
"""
from decimal import Decimal
from datetime import timezone
from uuid import uuid4

from sqlalchemy import select

from app.demand_models import MaterialRequest, MaterialRequestLine, SupplyTask, SubstitutionDecision
from app.formal_access import lock_formal_principal_graph, _scope_covers
from app.foundation_models import AuditEvent
from app.inventory_models import (StockReservation, StockReservationRelease, StockReservationPick,
    OutboundPosting, ShipmentLine, ReceiptLine)
from app.material_request_closure_schema import closures
from app.material_request_closure_schemas import (MaterialRequestCloseIn, MaterialRequestClosureOut,
    MaterialRequestClosureStateOut)
from app.material_request_completion_schemas import MaterialRequestCompletionOut
from . import material_request_query as query
from . import material_request_lifecycle as lifecycle
from .material_request_completion import completion_quantities
from .material_request_reservation_release import verified_release_history
from .audit_chain import append_audit_event, verify_audit_event_in_read_snapshot, AuditChainError


def _fail(code, category, message):
    raise query.MaterialRequestReadError('material_request_closure_' + code, category, message)


def _reference(request_id):
    return f'/api/v1/material-requests/{request_id}/close'


def _key(actor, request_id, key, secret):
    return lifecycle._idempotency_hmac(lifecycle._require_hmac_secret(secret), actor.user_id,
        _reference(request_id), lifecycle._require_idempotency_key(key))


def _payload_hash(actor, request_id, payload):
    # The command identity survives later authorization-version changes, but
    # every read/write still independently validates current authorization.
    return lifecycle._canonical_hash({'request_id': str(request_id), 'actor_user_id': actor.user_id,
        'actor_person_id': str(actor.person_id), **payload.model_dump(mode='json')})


def _context(db, actor, request_id, *, lock=False):
    context = query._load_read_context(db, actor=actor, now=None)
    stmt = select(MaterialRequest).where(MaterialRequest.id == request_id,
        query._visible_request_predicate(context)).execution_options(populate_existing=True)
    request = db.scalar(stmt.with_for_update() if lock else stmt)
    if request is None:
        _fail('not_found', 'not_found', '需求单不存在或不在当前授权范围内')
    return context.principal, request


def _close_grant(db, actor, request):
    if not actor.allows(db, 'material_request', 'close', target_scope_type='organization',
                        target_scope_id=str(request.requester_org_id)):
        _fail('forbidden', 'forbidden', '当前账号没有该需求的业务关闭权限')
    grants = sorted((grant for grant in actor.assignments
        if grant.role_code in {'admin', 'provincial_manager'}
        and _scope_covers(db, grant.scope_type, grant.scope_id, 'organization', str(request.requester_org_id))
        and any(e.assignment_id == grant.assignment_id and e.resource == 'material_request'
                and e.action == 'close' and e.field_code == '' and e.effect == 'allow' for e in actor.entitlements)),
        key=lambda row: str(row.assignment_id))
    if not grants:
        _fail('role_forbidden', 'forbidden', '仅总部或本区域负责人可执行业务关闭')
    return grants[0]


def close_material_request(db, *, actor, request_id, payload, idempotency_key, secret, trace_request_id,
                           _include_returns=False):
    payload = MaterialRequestCloseIn.model_validate(payload)
    lifecycle._require_uuid('request_id', request_id)
    trace = lifecycle._require_trace_request_id(trace_request_id)
    key = _key(actor, request_id, idempotency_key, secret)
    digest = _payload_hash(actor, request_id, payload)
    actor, request = _context(db, actor, request_id, lock=True)
    lock_formal_principal_graph(db, (actor.user_id,))
    actor, request = _context(db, actor, request_id)
    prior = db.execute(select(closures).where(closures.c.idempotency_key_hash == key)).mappings().one_or_none()
    if prior is not None:
        if prior['request_hash'] != digest or prior['actor_user_id'] != actor.user_id or prior['request_id'] != request.id:
            _fail('key_reused', 'conflict', '原幂等键已用于不同的关闭内容，请先核验原请求')
        return _result(db, request, prior, replayed=True)
    _close_grant(db, actor, request)
    if db.scalar(select(closures.c.id).where(closures.c.request_id == request.id)) is not None:
        _fail('already_closed', 'conflict', '需求已有业务关闭记录，请读取原结果')
    if db.scalar(select(closures.c.id).where(closures.c.actor_user_id == actor.user_id,
            closures.c.trace_request_id == trace)) is not None:
        _fail('trace_reused', 'conflict', '原请求编号已有关闭记录，请先核验结果')
    if request.version != payload.expected_request_version:
        _fail('version_changed', 'conflict', '需求版本已变化，请重新核对后关闭')
    coverage = completion_quantities(db, actor=actor, request_id=request.id, _include_returns=_include_returns)
    if not coverage.quantity_coverage_complete or coverage.pending_inbound_orders:
        _fail('quantity_incomplete', 'precondition_failed', '批准明细尚未全部入账或取消，或仍有入账单待过账')
    pending = _settled_fulfillment(db, request, include_returns=_include_returns)
    # Re-evaluate after evidence verification, while holding both request and
    # authorization locks. No permission is inherited from historic actors.
    actor, request = _context(db, actor, request_id)
    grant = _close_grant(db, actor, request)
    if request.version != payload.expected_request_version:
        _fail('version_changed', 'conflict', '结单核对期间需求版本已变化')
    now = lifecycle._database_now(db)
    evidence = {'schema': 'rsc.material_request_closure_evidence.v2' if pending.get('return_compensations')
                          else 'rsc.material_request_closure_evidence.v1',
        'coverage': coverage.model_dump(mode='json'), 'settled_fulfillment': pending}
    row = dict(id=uuid4(), request_id=request.id, revision_id=coverage.revision_id,
        request_version=request.version, actor_user_id=actor.user_id, actor_person_id=actor.person_id,
        actor_role_assignment_id=grant.assignment_id, authorization_version=actor.authorization_version,
        idempotency_key_hash=key, request_hash=digest, evidence_sha256=lifecycle._canonical_hash(evidence),
        trace_request_id=trace, reason=payload.reason, evidence_jsonb=evidence, occurred_at=now, created_at=now)
    db.execute(closures.insert().values(**row))
    append_audit_event(db, stream_key='material_request', actor_user_id=actor.user_id,
        action='material_request.close', aggregate_type='material_request_closure', aggregate_id=str(row['id']),
        before_jsonb={'business_status': 'open'}, after_jsonb=_audit_document(row),
        request_id=trace, occurred_at=now, created_at=now)
    db.flush()
    return _result(db, request, row, replayed=False)


def _settled_fulfillment(db, request, *, include_returns=False, lock_audit=True):
    returned = _return_settlement(db, request) if include_returns else []
    compensated_by_receipt = {}
    for fact in returned:
        key = fact['original_receipt_line_id']
        compensated_by_receipt[key] = compensated_by_receipt.get(key, Decimal(0)) + Decimal(fact['cancelled_qty'])
    def rows(model):
        return tuple(db.scalars(select(model).where(model.request_id == request.id).order_by(model.id)
            .execution_options(populate_existing=True)))
    reservations, releases, picks, outbound = (rows(model) for model in
        (StockReservation, StockReservationRelease, StockReservationPick, OutboundPosting))
    for release in releases:
        verified_release_history(db, fact=release, request=request, lock_audit=lock_audit)
    for reservation in reservations:
        released = sum((r.released_qty for r in releases if r.reservation_id == reservation.id), Decimal(0))
        picked = sum((r.picked_qty for r in picks if r.reservation_id == reservation.id), Decimal(0))
        if reservation.reserved_qty != released + picked:
            _fail('reservation_open', 'precondition_failed', '仍有未释放或未履约的库存占用')
    for pick in picks:
        if pick.picked_qty != sum((r.outbound_qty for r in outbound if r.pick_id == pick.id), Decimal(0)):
            _fail('pick_open', 'precondition_failed', '仍有已拣货但未完成出库的数量')
    shipped = tuple(db.scalars(select(ShipmentLine).where(ShipmentLine.outbound_posting_id.in_(
        tuple(r.id for r in outbound))).order_by(ShipmentLine.id))) if outbound else ()
    for posting in outbound:
        if posting.outbound_qty != sum((r.shipped_qty for r in shipped if r.outbound_posting_id == posting.id), Decimal(0)):
            _fail('outbound_open', 'precondition_failed', '仍有已出库但未完成发运的数量')
    received = tuple(db.scalars(select(ReceiptLine).where(ReceiptLine.shipment_line_id.in_(
        tuple(r.id for r in shipped))).order_by(ReceiptLine.id))) if shipped else ()
    if set(compensated_by_receipt) - {str(r.id) for r in received}:
        _fail('return_source_invalid', 'service_unavailable', '退回补偿无法对应当前需求的原始拒收明细')
    for receipt in received:
        if compensated_by_receipt.get(str(receipt.id), Decimal(0)) != receipt.rejected_qty:
            _fail('rejection_unsettled', 'precondition_failed', '每笔拒收均需完成实际退回入账和需求补偿后关闭')
    for shipment in shipped:
        accepted = sum((r.accepted_qty for r in received if r.shipment_line_id == shipment.id), Decimal(0))
        rejected = sum((r.rejected_qty for r in received if r.shipment_line_id == shipment.id), Decimal(0))
        if shipment.shipped_qty != accepted + rejected:
            _fail('receipt_open', 'precondition_failed', '仍有发运数量未完成验收')
    if db.scalar(select(SupplyTask.id).join(MaterialRequestLine, MaterialRequestLine.id == SupplyTask.request_line_id).where(MaterialRequestLine.request_id == request.id,
            SupplyTask.status.not_in(('cancelled', 'closed_no_supply'))).limit(1)) is not None:
        _fail('supply_open', 'precondition_failed', '仍有开放的供给计划，请先处理')
    if db.scalar(select(SubstitutionDecision.id).join(MaterialRequestLine, MaterialRequestLine.id == SubstitutionDecision.request_line_id).where(MaterialRequestLine.request_id == request.id,
            SubstitutionDecision.status == 'proposed').limit(1)) is not None:
        _fail('substitution_open', 'precondition_failed', '仍有待确认的替代料建议')
    settled = {name: [str(r.id) for r in records] for name, records in
        (('reservations', reservations), ('releases', releases), ('picks', picks),
         ('outbound', outbound), ('shipment_lines', shipped), ('receipt_lines', received))}
    if returned:
        settled['return_compensations'] = returned
    return settled


def _return_settlement(db, request):
    """Exact immutable sources, independent of historic writers' current grants."""
    from .material_request_return_posted_history import verified_posted_returns
    from .material_request_return_compensation import verified_compensations
    sources = verified_posted_returns(db, request=request)
    facts = verified_compensations(db, request=request, sources=sources)
    return [dict(compensation_id=str(f.compensation_id), inbound_id=str(f.inbound_id),
        request_line_id=str(f.request_line_id),
        original_receipt_line_id=sources[f.inbound_id].origin['original_receipt_line_id'],
        cancelled_qty=f.cancelled_qty, request_hash=f.request_hash, evidence_sha256=f.evidence_sha256)
        for f in sorted(facts, key=lambda f: str(f.compensation_id))]


def _audit_document(row):
    return {'business_status': 'closed', 'closure_id': str(row['id']), 'request_id': str(row['request_id']),
        'revision_id': str(row['revision_id']), 'request_version': row['request_version'],
        'actor_person_id': str(row['actor_person_id']), 'actor_role_assignment_id': str(row['actor_role_assignment_id']),
        'authorization_version': row['authorization_version'], 'idempotency_key_hash': row['idempotency_key_hash'],
        'request_hash': row['request_hash'], 'evidence_sha256': row['evidence_sha256'],
        'reason_sha256': lifecycle._text_hash(row['reason'])}


def _result(db, request, row, *, replayed):
    try:
        evidence = row['evidence_jsonb']
        if (evidence['schema'] not in {'rsc.material_request_closure_evidence.v1', 'rsc.material_request_closure_evidence.v2'}
                or lifecycle._canonical_hash(evidence) != row['evidence_sha256']
                or row['request_id'] != request.id or row['request_version'] > request.version
                or not lifecycle._same_timestamp(row['created_at'], row['occurred_at'])):
            raise ValueError
        coverage = MaterialRequestCompletionOut.model_validate(evidence['coverage'])
        if (not coverage.quantity_coverage_complete or coverage.pending_inbound_orders
                or coverage.request_id != request.id or coverage.revision_id != row['revision_id']
                or coverage.request_version != row['request_version']):
            raise ValueError
        if evidence['schema'] == 'rsc.material_request_closure_evidence.v2':
            from .material_request_completion import quantity_evidence
            current = quantity_evidence(db, request=request, include_returns=True)
            settled = _settled_fulfillment(db, request, include_returns=True, lock_audit=False)
            if (not settled.get('return_compensations') or settled != evidence['settled_fulfillment']
                    or current.model_dump(exclude={'request_version'}) != coverage.model_dump(exclude={'request_version'})):
                raise ValueError
        audits = tuple(db.scalars(select(AuditEvent).where(AuditEvent.stream_key == 'material_request',
            AuditEvent.action == 'material_request.close', AuditEvent.aggregate_type == 'material_request_closure',
            AuditEvent.aggregate_id == str(row['id']))))
        if (len(audits) != 1 or audits[0].actor_user_id != row['actor_user_id']
                or audits[0].request_id != row['trace_request_id'] or audits[0].after_jsonb != _audit_document(row)
                or audits[0].before_jsonb != {'business_status': 'open'}
                or not lifecycle._same_timestamp(audits[0].occurred_at, row['occurred_at'])):
            raise ValueError
        verify_audit_event_in_read_snapshot(db, stream_key='material_request', event_id=audits[0].id)
        return MaterialRequestClosureOut(closure_id=row['id'], request_id=request.id, revision_id=row['revision_id'],
            request_version=row['request_version'], closed_at=row['occurred_at'].replace(tzinfo=timezone.utc) if row['occurred_at'].tzinfo is None else row['occurred_at'].astimezone(timezone.utc),
            evidence_sha256=row['evidence_sha256'], lines=coverage.lines, replayed=replayed)
    except (ValueError, KeyError, TypeError, AuditChainError):
        _fail('history_invalid', 'service_unavailable', '原关闭记录与数量依据或审计链不一致')


def closure_command_status(db, *, actor, request_id, idempotency_key, secret, request_fingerprint=None):
    """Read-only recovery works with current read scope after write revocation."""
    with db.no_autoflush:
        actor, request = _context(db, actor, request_id)
        snapshot = query._snapshots((request,))
        key = _key(actor, request_id, idempotency_key, secret)
        row = db.execute(select(closures).where(closures.c.idempotency_key_hash == key,
            closures.c.request_id == request.id, closures.c.actor_user_id == actor.user_id)).mappings().one_or_none()
        if row is not None and request_fingerprint is not None:
            expected = lifecycle._canonical_hash({'expected_request_version': row['request_version'], 'reason': row['reason']})
            if request_fingerprint != expected:
                _fail('fingerprint_mismatch', 'conflict', '原关闭内容指纹不一致，保留请求并停止重试')
        result = _result(db, request, row, replayed=True) if row else None
        _context(db, actor, request_id)
        query._ensure_requests_current(db, snapshot)
        return result


def read_closure(db, *, actor, request_id):
    """Current scoped read; absence is an observed open state, not close approval."""
    with db.no_autoflush:
        actor, request = _context(db, actor, request_id)
        snapshot = query._snapshots((request,))
        row = db.execute(select(closures).where(closures.c.request_id == request.id)).mappings().one_or_none()
        closed = _result(db, request, row, replayed=True) if row else None
        allowed = False
        if closed is None:
            try:
                _close_grant(db, actor, request)
                allowed = True
            except query.MaterialRequestReadError as exc:
                if exc.category != 'forbidden':
                    raise
        _context(db, actor, request_id)
        query._ensure_requests_current(db, snapshot)
        return MaterialRequestClosureStateOut(request_id=request.id, request_version=request.version,
            business_status='closed' if closed else 'open', close_permitted=allowed, closure=closed)
