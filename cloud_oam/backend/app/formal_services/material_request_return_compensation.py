"""Original requester cancellation against exact posted warehouse return facts.

Forward service, not publicly mounted until its formal database boundary is
installed and verified. Caller owns the transaction. Does not post inventory,
rewrite approvals, increment request versions, or close the request.
"""
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4
from sqlalchemy import or_, select, union

from app.formal_access import lock_formal_principal_graph
from app.material_request_closure_schema import closures
from app.material_request_rejection_return_schema import returns
from app.material_request_rejection_inbound_schema import inbounds
from app.material_request_return_compensation_schema import compensations
from app.material_request_return_compensation_schemas import ReturnCompensationIn, ReturnCompensationOut
from . import material_request_lifecycle as lifecycle
from . import material_request_query as query
from . import material_request_remaining_cancel as remaining
from . import inventory_posting as posting
from . import material_request_rejection_inbound_facts as facts
from .material_request_approval_history import verified_final_approved_quantities
from .material_request_return_posted_history import verified_posted_returns
from .audit_chain import append_audit_event, AuditChainError

SCHEMA = 'rsc.material_request_return_compensation.v1'
AGGREGATE = 'material_request_return_compensation'
ACTION = 'material_request.cancel_returned'


def fail(code, category, message):
    raise query.MaterialRequestReadError('material_request_return_compensation_' + code, category, message)


def key_hash(actor, request_id, key, secret):
    return lifecycle._idempotency_hmac(lifecycle._require_hmac_secret(secret), actor.user_id,
        f'/api/v1/material-requests/{request_id}/return-compensations', lifecycle._require_idempotency_key(key))


def digest(actor, request_id, payload):
    return lifecycle._canonical_hash(dict(request_id=str(request_id), actor_user_id=actor.user_id,
        actor_person_id=str(actor.person_id), input=payload.model_dump(mode='json')))


def audit_body(row):
    return {k: str(row[k]) for k in ('id', 'inbound_id', 'request_id', 'revision_id', 'request_line_id',
        'actor_person_id', 'actor_role_assignment_id')} | {k: row[k] for k in ('request_version',
        'authorization_version', 'idempotency_key_hash', 'request_hash', 'evidence_sha256')} | dict(
        cancelled_qty=format(row['cancelled_qty'], '.3f'), reason_sha256=lifecycle._text_hash(row['reason']))


def evidence(source, payload):
    if (source.row['request_hash'] != payload.inbound_request_hash
            or source.row['plan_hash'] != payload.inbound_plan_hash
            or source.row['id'] != payload.inbound_id
            or source.row['accepted_qty'] != Decimal(payload.cancelled_qty)):
        fail('source_changed', 'precondition_failed', '补偿必须准确对应本笔仓库实际入账及全部接受数量')
    return dict(schema=SCHEMA, input=payload.model_dump(mode='json'), origin=source.origin)


def result(db, request, row, sources, *, replayed=True):
    try:
        source = sources[row['inbound_id']]
        payload = ReturnCompensationIn.model_validate(row['evidence_jsonb']['input'])
        expected = evidence(source, payload)
        subject = SimpleNamespace(user_id=row['actor_user_id'], person_id=row['actor_person_id'])
        if (row['request_id'] != request.id or row['revision_id'] != UUID(source.origin['revision_id'])
                or row['request_line_id'] != UUID(source.origin['request_line_id'])
                or not source.row['request_version'] <= row['request_version'] <= request.version
                or row['actor_user_id'] != request.requester_user_id or row['actor_person_id'] != request.requester_person_id
                or row['request_version'] != payload.expected_request_version or row['reason'] != payload.reason
                or row['cancelled_qty'] != Decimal(payload.cancelled_qty)
                or facts.acceptance._time(row['recorded_at']) < facts.acceptance._time(source.row['recorded_at'])
                or expected != row['evidence_jsonb'] or lifecycle._canonical_hash(expected) != row['evidence_sha256']
                or digest(subject, request.id, payload) != row['request_hash']):
            raise ValueError('compensation original evidence mismatch')
        audit = facts.audit(db, aggregate=AGGREGATE, identifier=row['id'], stream='material_request', action=ACTION,
            actor_id=row['actor_user_id'], trace=row['trace_request_id'], before={'return_compensation': 'not_recorded'},
            after=audit_body(row))
        if (not lifecycle._same_timestamp(audit.occurred_at, row['recorded_at'])
                or not lifecycle._same_timestamp(audit.created_at, row['recorded_at'])):
            raise ValueError('compensation audit time mismatch')
        return ReturnCompensationOut(compensation_id=row['id'], inbound_id=row['inbound_id'], request_id=request.id,
            revision_id=row['revision_id'], request_line_id=row['request_line_id'], request_version=row['request_version'],
            cancelled_qty=format(row['cancelled_qty'], '.3f'), request_hash=row['request_hash'],
            evidence_sha256=row['evidence_sha256'], cancelled_at=facts.acceptance._time(row['recorded_at']), replayed=replayed)
    except (ValueError, TypeError, KeyError, AttributeError, AuditChainError):
        fail('history_invalid', 'service_unavailable', '退回补偿的原请求、来源数量或审计不一致')


def verified_compensations(db, *, request, sources=None):
    """Internal SELECT-only evidence; caller must authorize request access."""
    with db.no_autoflush:
        sources = verified_posted_returns(db, request=request) if sources is None else sources
        rows = tuple(db.execute(select(compensations).outerjoin(inbounds, inbounds.c.id == compensations.c.inbound_id)
            .where(or_(compensations.c.request_id == request.id, inbounds.c.request_id == request.id))
            .order_by(compensations.c.recorded_at, compensations.c.id).limit(1001)).mappings())
        if len(rows) > 1000:
            fail('history_limit', 'precondition_failed', '补偿数量超出完整核验上限，未返回部分结果')
        results = tuple(result(db, request, row, sources) for row in rows)
        if len({r.inbound_id for r in results}) != len(results):
            fail('history_invalid', 'service_unavailable', '同一入账不能重复补偿')
        return results


def verified_return_compensated_quantities(db, *, request):
    totals = {}
    for row in verified_compensations(db, request=request):
        totals[row.request_line_id] = totals.get(row.request_line_id, Decimal(0)) + Decimal(row.cancelled_qty)
    return totals


def verified_return_compensations_for_page(db, *, requests):
    """Batch presence probe; fully verify related facts only for the scoped page."""
    requests = {request.id: request for request in requests}
    if not requests:
        return {}
    # Include both sides of parent bindings so corrupt request IDs cannot hide
    # inbound or compensation facts from the original request's reader.
    candidates = set(db.scalars(union(
        select(compensations.c.request_id).where(compensations.c.request_id.in_(requests)),
        select(inbounds.c.request_id).where(inbounds.c.request_id.in_(requests)),
        select(returns.c.request_id).join(inbounds, inbounds.c.return_id == returns.c.id)
            .where(returns.c.request_id.in_(requests)),
    )))
    return {key: verified_return_compensated_quantities(db, request=requests[key]) for key in candidates}


def cancel_returned_demand(db, *, actor, request_id, payload, idempotency_key, secret, trace_request_id):
    payload = ReturnCompensationIn.model_validate(payload)
    lifecycle._require_uuid('request_id', request_id)
    trace = lifecycle._require_trace_request_id(trace_request_id)
    key = key_hash(actor, request_id, idempotency_key, secret)
    posting._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_formal_principal_graph(db, (actor.user_id,))
    actor, request = remaining._context(db, actor, request_id, lock=True)
    fingerprint = digest(actor, request_id, payload)
    prior = db.execute(select(compensations).where(compensations.c.idempotency_key_hash == key)).mappings().one_or_none()
    if prior is not None:
        if (prior['actor_user_id'] != actor.user_id or prior['request_id'] != request.id
                or prior['request_hash'] != fingerprint):
            fail('key_reused', 'conflict', '原请求键对应不同退回补偿，保留原请求核验')
        return result(db, request, prior, verified_posted_returns(db, request=request))
    requester = remaining._authority(db, actor, request)
    if db.scalar(select(closures.c.id).where(closures.c.request_id == request.id)):
        fail('closed', 'conflict', '需求已关闭，不能新增退回补偿')
    if request.version != payload.expected_request_version:
        fail('version_changed', 'conflict', '需求版本已变化，请重新核对退回补偿')
    if request.status not in ('approved', 'partially_approved'):
        fail('state_invalid', 'precondition_failed', '仅最终批准的需求可办理退回补偿')
    if db.scalar(select(compensations.c.id).where(compensations.c.actor_user_id == actor.user_id,
            compensations.c.trace_request_id == trace)):
        fail('trace_reused', 'conflict', '请求编号已绑定其他退回补偿')
    approved = verified_final_approved_quantities(db, request=request)
    sources = verified_posted_returns(db, request=request)
    source = sources.get(payload.inbound_id)
    if source is None:
        fail('source_missing', 'precondition_failed', '此需求没有已核验的对应仓库入账')
    history = verified_compensations(db, request=request, sources=sources)
    if any(row.inbound_id == payload.inbound_id for row in history):
        fail('already_compensated', 'conflict', '此笔入账已有独立补偿，请回读原请求')
    document = evidence(source, payload)
    line_id = UUID(source.origin['request_line_id'])
    if line_id not in approved or sum((Decimal(r.cancelled_qty) for r in history if r.request_line_id == line_id),
            Decimal(payload.cancelled_qty)) > approved[line_id]:
        fail('quantity_exceeded', 'precondition_failed', '补偿数量超过最终批准明细')
    fresh, current = remaining._context(db, actor, request.id)
    if fresh != actor or current.version != payload.expected_request_version:
        fail('context_changed', 'conflict', '核验期间需求或身份范围变化')
    requester = remaining._authority(db, fresh, current)
    now = lifecycle._database_now(db)
    row = dict(id=uuid4(), inbound_id=payload.inbound_id, request_id=request.id,
        revision_id=UUID(source.origin['revision_id']), request_line_id=line_id, request_version=request.version,
        actor_user_id=actor.user_id, actor_person_id=actor.person_id,
        actor_role_assignment_id=requester.technician_grant.assignment_id, authorization_version=actor.authorization_version,
        cancelled_qty=Decimal(payload.cancelled_qty), reason=payload.reason, recorded_at=now,
        idempotency_key_hash=key, trace_request_id=trace, request_hash=fingerprint,
        evidence_sha256=lifecycle._canonical_hash(document), evidence_jsonb=document)
    db.execute(compensations.insert().values(**row))
    append_audit_event(db, stream_key='material_request', actor_user_id=actor.user_id, action=ACTION,
        aggregate_type=AGGREGATE, aggregate_id=str(row['id']), before_jsonb={'return_compensation': 'not_recorded'},
        after_jsonb=audit_body(row), request_id=trace, occurred_at=now, created_at=now)
    db.flush()
    return result(db, request, row, sources, replayed=False)


def command_status(db, *, actor, request_id, idempotency_key=None, secret=None, trace_request_id=None, request_fingerprint=None):
    if (idempotency_key is None) == (trace_request_id is None):
        fail('lookup_invalid', 'invalid_request', '请提供唯一原请求查询坐标')
    with db.no_autoflush:
        actor, request = remaining._context(db, actor, request_id)
        snapshot = query._snapshots((request,))
        statement = select(compensations).where(compensations.c.request_id == request.id,
            compensations.c.actor_user_id == actor.user_id)
        statement = statement.where(compensations.c.idempotency_key_hash == key_hash(actor, request_id, idempotency_key, secret)) \
            if idempotency_key is not None else statement.where(
                compensations.c.trace_request_id == lifecycle._require_trace_request_id(trace_request_id))
        row = db.execute(statement).mappings().one_or_none()
        if row is not None and request_fingerprint is not None:
            if request_fingerprint != lifecycle._canonical_hash(row['evidence_jsonb']['input']):
                fail('fingerprint_mismatch', 'conflict', '原补偿请求内容不一致，保留原请求')
        recovered = result(db, request, row, verified_posted_returns(db, request=request)) if row else None
        remaining._context(db, actor, request_id)
        query._ensure_requests_current(db, snapshot)
        return recovered


def candidates(db, *, actor, request_id):
    """Authorized posted sources for the UI; permission loss preserves history."""
    from app.demand_models import MaterialRequestLine
    from app.inventory_models import FormalMaterial
    from app.material_request_return_compensation_schemas import ReturnCompensationCandidateOut, ReturnCompensationCandidatesOut
    from .work_order_evidence_snapshot import material_audit_cursor
    with db.no_autoflush:
        actor, request = remaining._context(db, actor, request_id)
        snapshot = query._snapshots((request,))
        cursor = material_audit_cursor(db)
        sources = verified_posted_returns(db, request=request)
        history = {row.inbound_id: row for row in verified_compensations(db, request=request, sources=sources)}
        permitted = False
        if (request.requester_user_id == actor.user_id and request.requester_person_id == actor.person_id
                and request.status in ('approved', 'partially_approved')
                and db.scalar(select(closures.c.id).where(closures.c.request_id == request.id)) is None):
            try:
                remaining._authority(db, actor, request)
                permitted = True
            except lifecycle.MaterialRequestLifecycleError as exc:
                if exc.category != 'forbidden':
                    raise
        items = []
        for source in sources.values():
            line = db.get(MaterialRequestLine, UUID(source.origin['request_line_id']), populate_existing=True)
            material = db.get(FormalMaterial, line.material_id, populate_existing=True) if line else None
            if material is None:
                fail('source_missing', 'service_unavailable', '已入账退回的物料信息不完整')
            items.append(ReturnCompensationCandidateOut(inbound_id=source.row['id'], request_line_id=line.id,
                inbound_request_hash=source.row['request_hash'], inbound_plan_hash=source.row['plan_hash'],
                quantity=format(source.row['accepted_qty'], '.3f'), sku_code=material.sku_code,
                material_name=material.name, posted_at=facts.acceptance._time(source.row['recorded_at']),
                compensate_permitted=permitted and source.row['id'] not in history,
                compensation=history.get(source.row['id'])))
        current, _ = remaining._context(db, actor, request_id)
        if current != actor or material_audit_cursor(db) != cursor:
            fail('context_changed', 'precondition_failed', '读取期间身份或退回记录发生变化')
        query._ensure_requests_current(db, snapshot)
        return ReturnCompensationCandidatesOut(request_id=request.id, request_version=request.version, items=tuple(items))
