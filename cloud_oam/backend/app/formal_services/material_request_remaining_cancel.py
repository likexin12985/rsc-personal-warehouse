"""Remaining-demand cancellation backed by the formal 0171 migration.

The caller owns the transaction. PostgreSQL activation must independently bind
authority, immutable lines, full compensation proof and terminal write barriers.
No old approval state, allocated quantity or inventory transaction is rewritten.
"""
from datetime import timezone
from decimal import Decimal
from uuid import uuid4

from sqlalchemy import select, func

from app.demand_models import MaterialRequest
from app.formal_access import lock_formal_principal_graph
from app.foundation_models import AuditEvent
from app.material_request_closure_schema import closures
from app.material_request_remaining_cancel_schema import cancellations, cancellation_lines
from app.material_request_remaining_cancel_schemas import (
    MaterialRequestCancelRemainingIn, MaterialRequestRemainingCancellationOut,
    RemainingCancelLine, require_settled_remainder)
from . import material_request_lifecycle as lifecycle
from . import material_request_query as query
from .material_request_remainder import remaining_fulfillment
from .material_request_completion import completion_quantities
from .material_request_closure import _settled_fulfillment
from .audit_chain import append_audit_event, verify_audit_event_in_read_snapshot, AuditChainError

ACTION = 'material_request.cancel_remaining'
SCHEMA = 'rsc.material_request_remaining_cancellation.v1'
RETURN_SCHEMA = 'rsc.material_request_remaining_cancellation.v2'


def _fail(code, category, message):
    raise query.MaterialRequestReadError('material_request_remaining_cancel_' + code, category, message)


def _key(actor, request_id, key, secret):
    return lifecycle._idempotency_hmac(lifecycle._require_hmac_secret(secret), actor.user_id,
        f'/api/v1/material-requests/{request_id}/cancel-remaining', lifecycle._require_idempotency_key(key))


def _payload_hash(actor, request_id, payload):
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


def _authority(db, actor, request):
    requester = lifecycle._require_requester_context(db, actor, 'cancel', lifecycle._database_now(db))
    lifecycle._require_owned_request(request, requester)
    return requester


def cancel_remaining_demand(db, *, actor, request_id, payload, idempotency_key, secret, trace_request_id,
                           _include_returns=False):
    payload = MaterialRequestCancelRemainingIn.model_validate(payload)
    lifecycle._require_uuid('request_id', request_id)
    trace = lifecycle._require_trace_request_id(trace_request_id)
    key = _key(actor, request_id, idempotency_key, secret)
    digest = _payload_hash(actor, request_id, payload)
    actor, request = _context(db, actor, request_id, lock=True)
    lock_formal_principal_graph(db, (actor.user_id,))
    actor, request = _context(db, actor, request_id)
    prior = db.execute(select(cancellations).where(cancellations.c.idempotency_key_hash == key)).mappings().one_or_none()
    if prior is not None:
        if prior['request_hash'] != digest or prior['actor_user_id'] != actor.user_id or prior['request_id'] != request.id:
            _fail('key_reused', 'conflict', '原幂等键对应不同取消内容，保留原请求继续核验')
        return _result(db, request, prior, replayed=True)
    _authority(db, actor, request)
    if db.scalar(select(cancellations.c.id).where(cancellations.c.request_id == request.id)) is not None:
        _fail('already_cancelled', 'conflict', '需求已有剩余取消记录，请先读取原结果')
    if db.scalar(select(cancellations.c.id).where(cancellations.c.actor_user_id == actor.user_id,
            cancellations.c.trace_request_id == trace)) is not None:
        _fail('trace_reused', 'conflict', '请求编号已有取消记录，请先核验原结果')
    if db.scalar(select(closures.c.id).where(closures.c.request_id == request.id)) is not None:
        _fail('closed', 'conflict', '需求已关闭')
    if request.version != payload.expected_request_version:
        _fail('version_changed', 'conflict', '需求版本已变化，请重新核对取消数量')
    if request.status not in ('approved', 'partially_approved'):
        _fail('state_invalid', 'precondition_failed', '仅最终批准的需求可取消剩余未履约数量')
    coverage = completion_quantities(db, actor=actor, request_id=request.id, _include_returns=_include_returns)
    if coverage.pending_inbound_orders:
        _fail('inbound_pending', 'precondition_failed', '仍有待过账入账单，不能取消剩余需求')
    assessment = remaining_fulfillment(db, actor=actor, request_id=request.id)
    schema = SCHEMA
    if _include_returns:
        from .material_request_return_quantities import remaining_with_returns
        candidate = remaining_with_returns(db, actor=actor, request_id=request.id)
        if any(Decimal(line.return_compensated_qty) for line in candidate.lines):
            assessment, schema = candidate, RETURN_SCHEMA
    try:
        _require_settled(assessment, payload.lines, schema)
    except ValueError as exc:
        _fail('unsettled', 'precondition_failed', str(exc))
    settled = _settled_fulfillment(db, request, include_returns=_include_returns)
    actor, request = _context(db, actor, request_id)
    requester = _authority(db, actor, request)
    if request.version != payload.expected_request_version:
        _fail('version_changed', 'conflict', '补偿核验期间需求版本已变化')
    evidence = {'schema': schema, 'before': assessment.model_dump(mode='json'),
        'settled_fulfillment': settled, 'input': payload.model_dump(mode='json')}
    now = lifecycle._database_now(db)
    row = dict(id=uuid4(), request_id=request.id, revision_id=assessment.revision_id,
        request_version=request.version, actor_user_id=actor.user_id, actor_person_id=actor.person_id,
        actor_role_assignment_id=requester.technician_grant.assignment_id,
        authorization_version=actor.authorization_version, idempotency_key_hash=key,
        request_hash=digest, evidence_sha256=lifecycle._canonical_hash(evidence), trace_request_id=trace,
        reason=payload.reason, evidence_jsonb=evidence, occurred_at=now, created_at=now)
    db.execute(cancellations.insert().values(**row))
    db.execute(cancellation_lines.insert(), [dict(cancellation_id=row['id'], request_id=request.id,
        revision_id=assessment.revision_id, request_line_id=line.request_line_id,
        cancelled_qty=Decimal(line.cancelled_qty)) for line in payload.lines])
    append_audit_event(db, stream_key='material_request', actor_user_id=actor.user_id, action=ACTION,
        aggregate_type='material_request_remaining_cancellation', aggregate_id=str(row['id']),
        before_jsonb={'remaining_cancellation': 'not_recorded'}, after_jsonb=_audit_document(row),
        request_id=trace, occurred_at=now, created_at=now)
    db.flush()
    return _result(db, request, row, replayed=False)


def _audit_document(row):
    return {key: str(row[key]) if key in ('id', 'request_id', 'revision_id', 'actor_person_id',
            'actor_role_assignment_id') else row[key] for key in ('id', 'request_id', 'revision_id',
        'request_version', 'actor_person_id', 'actor_role_assignment_id', 'authorization_version',
        'idempotency_key_hash', 'request_hash', 'evidence_sha256')} | {'reason_sha256': lifecycle._text_hash(row['reason'])}


def _require_settled(assessment, requested, schema):
    if schema == SCHEMA:
        return require_settled_remainder(assessment, requested)
    if schema == RETURN_SCHEMA:
        from app.material_request_return_compensation_schemas import require_settled_returned_remainder
        return require_settled_returned_remainder(assessment, requested)
    raise ValueError('unknown remaining cancellation evidence version')


def _verify_returned_before(db, request, assessment, evidence):
    """Rebuild pre-cancellation proof without recursively reading this record."""
    from .material_request_approval_history import verified_final_approved_quantities
    from .material_request_completion import posted_evidence
    from .material_request_closure_coverage import line_coverage
    from .material_request_return_compensation import verified_return_compensated_quantities
    approved = verified_final_approved_quantities(db, request=request)
    returned = verified_return_compensated_quantities(db, request=request)
    posted, pending = posted_evidence(db, request=request)
    if pending:
        raise ValueError('pending personal inbound after cancellation')
    expected = line_coverage(approved_by_line=approved, cancelled_by_line=returned, posted_receipt_lines=posted)
    before = {line.request_line_id: line for line in assessment.lines}
    if set(before) != set(approved):
        raise ValueError('cancelled revision lines changed')
    for line in expected:
        fact = before[line.request_line_id]
        if any(Decimal(getattr(fact, target)) != getattr(line, source) for source, target in (
                ('approved_qty', 'approved_qty'), ('cancelled_qty', 'return_compensated_qty'),
                ('posted_qty', 'posted_qty'), ('remaining_qty', 'unreserved_qty'))):
            raise ValueError('cancelled quantity history changed')
    settled = _settled_fulfillment(db, request, include_returns=True, lock_audit=False)
    if not settled.get('return_compensations') or settled != evidence['settled_fulfillment']:
        raise ValueError('cancelled return settlement changed')


def _result(db, request, row, *, replayed):
    try:
        evidence = row['evidence_jsonb']
        payload = MaterialRequestCancelRemainingIn.model_validate(evidence['input'])
        assessment = _require_settled(evidence['before'], payload.lines, evidence['schema'])
        if (request.status not in ('approved', 'partially_approved') or lifecycle._canonical_hash(evidence) != row['evidence_sha256']
                or row['request_id'] != request.id or row['request_version'] != request.version
                or row['actor_user_id'] != request.requester_user_id or row['actor_person_id'] != request.requester_person_id
                or assessment.request_id != request.id or assessment.revision_id != row['revision_id']
                or assessment.request_version != row['request_version'] or payload.expected_request_version != row['request_version']
                or payload.reason != row['reason'] or not lifecycle._same_timestamp(row['created_at'], row['occurred_at'])):
            raise ValueError('identity or source proof mismatch')
        if evidence['schema'] == RETURN_SCHEMA:
            _verify_returned_before(db, request, assessment, evidence)
        from types import SimpleNamespace
        if _payload_hash(SimpleNamespace(user_id=row['actor_user_id'], person_id=row['actor_person_id']),
                         request.id, payload) != row['request_hash']:
            raise ValueError('original input hash mismatch')
        records = tuple(db.execute(select(cancellation_lines).where(
            cancellation_lines.c.cancellation_id == row['id']).order_by(cancellation_lines.c.request_line_id)).mappings())
        if (len(records) != len(payload.lines) or any(r['request_id'] != request.id or r['revision_id'] != row['revision_id'] for r in records)
                or {r['request_line_id']: format(r['cancelled_qty'], '.3f') for r in records}
                != {r.request_line_id: r.cancelled_qty for r in payload.lines}):
            raise ValueError('line facts mismatch')
        audits = tuple(db.scalars(select(AuditEvent).where(AuditEvent.stream_key == 'material_request',
            AuditEvent.action == ACTION, AuditEvent.aggregate_type == 'material_request_remaining_cancellation',
            AuditEvent.aggregate_id == str(row['id']))))
        if (len(audits) != 1 or audits[0].actor_user_id != row['actor_user_id']
                or audits[0].request_id != row['trace_request_id'] or audits[0].after_jsonb != _audit_document(row)
                or audits[0].before_jsonb != {'remaining_cancellation': 'not_recorded'}
                or not lifecycle._same_timestamp(audits[0].occurred_at, row['occurred_at'])):
            raise ValueError('audit mismatch')
        verify_audit_event_in_read_snapshot(db, stream_key='material_request', event_id=audits[0].id)
        return MaterialRequestRemainingCancellationOut(cancellation_id=row['id'], request_id=request.id,
            revision_id=row['revision_id'], request_version=row['request_version'], evidence_sha256=row['evidence_sha256'],
            cancelled_at=row['occurred_at'].replace(tzinfo=timezone.utc) if row['occurred_at'].tzinfo is None else row['occurred_at'].astimezone(timezone.utc),
            lines=tuple(RemainingCancelLine(request_line_id=r['request_line_id'], cancelled_qty=format(r['cancelled_qty'], '.3f')) for r in records),
            replayed=replayed)
    except (ValueError, KeyError, TypeError, AuditChainError):
        _fail('history_invalid', 'service_unavailable', '取消原请求、逐行事实或审计证据不一致')


def remaining_cancellation_command_status(db, *, actor, request_id, idempotency_key, secret, request_fingerprint=None):
    with db.no_autoflush:
        actor, request = _context(db, actor, request_id)
        snapshot = query._snapshots((request,))
        key = _key(actor, request_id, idempotency_key, secret)
        row = db.execute(select(cancellations).where(cancellations.c.idempotency_key_hash == key,
            cancellations.c.actor_user_id == actor.user_id, cancellations.c.request_id == request.id)).mappings().one_or_none()
        if row is not None and request_fingerprint is not None:
            if request_fingerprint != lifecycle._canonical_hash(row['evidence_jsonb']['input']):
                _fail('fingerprint_mismatch', 'conflict', '原取消内容指纹不一致，保留请求停止重试')
        result = _result(db, request, row, replayed=True) if row else None
        _context(db, actor, request_id)
        query._ensure_requests_current(db, snapshot)
        return result


def verified_remaining_cancelled_quantities(db, *, request):
    """Internal read-only evidence; caller must authorize current request access."""
    row = db.execute(select(cancellations).where(
        cancellations.c.request_id == request.id)).mappings().one_or_none()
    if row is None:
        # A missing header is not evidence of zero when orphan lines exist.
        if db.scalar(select(cancellation_lines.c.request_line_id).where(
                cancellation_lines.c.request_id == request.id).limit(1)) is not None:
            _fail('history_invalid', 'service_unavailable', '取消明细缺少对应原始命令')
        return {}
    result = _result(db, request, row, replayed=True)
    return {line.request_line_id: Decimal(line.cancelled_qty) for line in result.lines}


def verified_remaining_cancellations_for_page(db, *, requests):
    """Batch existence reads for the scoped page; verify every returned fact."""
    requests = {request.id: request for request in requests}
    if not requests:
        return {}
    counts = select(cancellation_lines.c.request_id, func.count().label('fact_count')).where(
        cancellation_lines.c.request_id.in_(requests)).group_by(cancellation_lines.c.request_id).subquery()
    rows = tuple(db.execute(select(cancellations, counts.c.fact_count).select_from(MaterialRequest)
        .outerjoin(cancellations, cancellations.c.request_id == MaterialRequest.id)
        .outerjoin(counts, counts.c.request_id == MaterialRequest.id)
        .where(MaterialRequest.id.in_(requests))).mappings())
    if len(rows) != len(requests) or any(bool(row['fact_count']) != (row['id'] is not None) for row in rows):
        _fail('history_invalid', 'service_unavailable', '取消命令与逐行记录不完整')
    result = {}
    for row in rows:
        if row['id'] is None:
            continue
        verified = _result(db, requests[row['request_id']], row, replayed=True)
        result[row['request_id']] = {line.request_line_id: Decimal(line.cancelled_qty) for line in verified.lines}
    return result


def read_remaining_cancellation(db, *, actor, request_id):
    """Current scope and command permission; this is not a settled-stock verdict."""
    from app.material_request_remaining_cancel_schemas import MaterialRequestRemainingCancellationStateOut
    with db.no_autoflush:
        actor, request = _context(db, actor, request_id)
        snapshot = query._snapshots((request,))
        row = db.execute(select(cancellations).where(cancellations.c.request_id == request.id)).mappings().one_or_none()
        result = _result(db, request, row, replayed=True) if row else None
        allowed = False
        if result is None and request.status in ('approved', 'partially_approved') and not db.scalar(
                select(closures.c.id).where(closures.c.request_id == request.id)):
            try:
                _authority(db, actor, request)
                allowed = True
            except lifecycle.MaterialRequestLifecycleError as exc:
                if exc.category != 'forbidden':
                    raise
        _context(db, actor, request_id)
        query._ensure_requests_current(db, snapshot)
        return MaterialRequestRemainingCancellationStateOut(request_id=request.id, request_version=request.version,
            cancel_permitted=allowed, cancellation=result)
