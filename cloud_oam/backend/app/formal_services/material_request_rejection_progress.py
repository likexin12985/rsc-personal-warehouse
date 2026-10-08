"""Append-only refusal-return progress. Formal 0173 with a transaction-owning public HTTP boundary.

Caller owns the transaction. Current principal/request locks serialize the exact
return, and read recovery validates its complete chain without requiring today's
write permission. No stock or original registration is updated here.
"""
from dataclasses import replace
from uuid import uuid4

from sqlalchemy import select

from app.foundation_models import AuditEvent
from app.material_request_rejection_progress_schema import progress
from app.material_request_rejection_progress_schemas import RejectionProgressIn, RejectionProgressOut, RejectionProgressStateOut
from . import material_request_rejection_return as registration
from . import material_request_lifecycle as lifecycle
from .audit_chain import append_audit_event, verify_audit_event_in_read_snapshot, AuditChainError

SCHEMA = 'rsc.material_request_rejection_progress.v1'
AGGREGATE = 'material_request_rejection_progress'
PERMISSIONS = {'cancel_registration': 'cancel_return', 'depart': 'outbound_return', 'handover': 'ship_return'}
STATES = {'cancel_registration': 'cancelled', 'depart': 'departed', 'handover': 'handed_over'}


def _fail(code, category, message):
    registration._fail('progress_' + code, category, message)


def _authority(db, actor, request, action):
    requester = lifecycle._require_requester_context(db, actor, 'read', lifecycle._database_now(db))
    lifecycle._require_owned_request(request, requester)
    grant = requester.technician_grant
    selected = replace(actor, assignments=(grant,), entitlements=tuple(
        e for e in actor.entitlements if e.assignment_id == grant.assignment_id))
    for principal in (actor, selected):
        if not principal.allows(db, 'stock_operation', PERMISSIONS[action],
                target_scope_type='person', target_scope_id=str(actor.person_id)):
            _fail('forbidden', 'forbidden', '没有该本人退回动作的权限')
    return grant


def _key(actor, request_id, return_id, key, secret):
    return lifecycle._idempotency_hmac(lifecycle._require_hmac_secret(secret), actor.user_id,
        f'/api/v1/material-requests/{request_id}/rejection-returns/{return_id}/progress',
        lifecycle._require_idempotency_key(key))


def _digest(actor, request_id, return_id, payload):
    return lifecycle._canonical_hash(dict(request_id=str(request_id), return_id=str(return_id),
        actor_user_id=actor.user_id, actor_person_id=str(actor.person_id), input=payload.model_dump(mode='json')))


def _parent(db, context, request, return_id):
    row = db.execute(select(registration.returns).where(registration.returns.c.id == return_id,
        registration.returns.c.request_id == request.id,
        registration.returns.c.actor_user_id == context.principal.user_id)).mappings().one_or_none()
    if row is None:
        _fail('not_found', 'not_found', '本人原退回登记不存在')
    registration._result(db, context, request, row, replayed=True)
    return row


def _evidence(parent, payload):
    return dict(schema=SCHEMA, input=payload.model_dump(mode='json'), origin=dict(
        return_id=str(parent['id']), registration_request_hash=parent['request_hash'],
        registration_evidence_sha256=parent['evidence_sha256'], request_id=str(parent['request_id']),
        receipt_id=str(parent['receipt_id']), receipt_line_id=str(parent['receipt_line_id']),
        quantity=format(parent['quantity'], '.3f'), serial_ids=parent['evidence_jsonb']['input']['serial_ids']))


def _audit(row):
    return {k: str(row[k]) for k in ('id', 'return_id', 'request_id', 'actor_person_id', 'actor_role_assignment_id')} | {
        k: row[k] for k in ('action', 'request_version', 'authorization_version', 'registration_request_hash',
            'idempotency_key_hash', 'request_hash', 'evidence_sha256')}


def _time(value):
    return registration.history.versions._time(value)


def _transition(parent, prior, payload, recorded_at):
    if payload.registration_request_hash != parent['request_hash']:
        _fail('registration_changed', 'conflict', '原退回登记指纹不一致')
    if payload.action == 'handover':
        if (len(prior) != 1 or prior[0].action != 'depart'
                or payload.previous_event_id != prior[0].event_id
                or payload.previous_request_hash != prior[0].request_hash):
            _fail('departure_required', 'precondition_failed', '承运交接必须绑定本单唯一的实物发出事实')
    elif prior:
        _fail('already_progressed', 'precondition_failed', '已有退回进展，不能取消登记或再次发出')
    floor = prior[-1].physical_at if prior else _time(parent['occurred_at'])
    if payload.physical_at is not None and not floor <= payload.physical_at <= _time(recorded_at):
        _fail('time_invalid', 'precondition_failed', '实物时间不能早于前置事实或晚于当前登记时间')
    if prior and _time(recorded_at) < prior[-1].recorded_at:
        _fail('time_invalid', 'precondition_failed', '进展登记时间顺序不一致')


def _checked_chain(db, context, request, parent):
    return _verified_chain(db, context.principal, request, parent)


def _verified_chain(db, subject, request, parent):
    rows = tuple(db.execute(select(progress).where(progress.c.return_id == parent['id'])
        .order_by(progress.c.recorded_at, progress.c.id).limit(4)).mappings())
    if len(rows) > 2:
        _fail('history_invalid', 'service_unavailable', '退回进展记录不符合允许的状态顺序')
    # Two events may share the same transaction timestamp. The predecessor link,
    # not UUID order or a client timestamp, determines their causal order.
    rows = sorted(rows, key=lambda row: row['previous_event_id'] is not None)
    result = []
    try:
        for row in rows:
            payload = RejectionProgressIn.model_validate(row['evidence_jsonb']['input'])
            evidence = _evidence(parent, payload)
            if (row['request_id'] != request.id or row['actor_user_id'] != subject.user_id
                    or row['actor_person_id'] != subject.person_id
                    or row['request_version'] != payload.expected_request_version
                    or row['request_version'] > request.version
                    or row['request_version'] < parent['request_version']
                    or row['evidence_jsonb'] != evidence or row['evidence_sha256'] != lifecycle._canonical_hash(evidence)
                    or row['request_hash'] != _digest(subject, request.id, parent['id'], payload)
                    or _time(row['recorded_at']) < _time(parent['occurred_at'])):
                raise ValueError('progress origin or evidence mismatch')
            for field in ('action', 'reason', 'registration_request_hash', 'previous_event_id', 'previous_request_hash', 'carrier', 'tracking_no'):
                if row[field] != getattr(payload, field):
                    raise ValueError('progress input mismatch')
            actual_time = _time(row['physical_at']) if row['physical_at'] is not None else None
            if actual_time != payload.physical_at:
                raise ValueError('physical time mismatch')
            _transition(parent, result, payload, row['recorded_at'])
            audits = tuple(db.scalars(select(AuditEvent).where(AuditEvent.aggregate_type == AGGREGATE,
                AuditEvent.aggregate_id == str(row['id']))))
            before = {'rejection_return': STATES[result[-1].action] if result else 'registered'}
            if (len(audits) != 1 or audits[0].action != 'material_request.rejection_return.' + row['action']
                    or audits[0].actor_user_id != row['actor_user_id'] or audits[0].request_id != row['trace_request_id']
                    or audits[0].before_jsonb != before or audits[0].after_jsonb != _audit(row)
                    or not lifecycle._same_timestamp(audits[0].occurred_at, row['recorded_at'])
                    or not lifecycle._same_timestamp(audits[0].created_at, row['recorded_at'])):
                raise ValueError('progress audit mismatch')
            verify_audit_event_in_read_snapshot(db, stream_key='material_request', event_id=audits[0].id)
            result.append(RejectionProgressOut(event_id=row['id'], return_id=parent['id'], request_id=request.id,
                request_version=row['request_version'], recorded_at=_time(row['recorded_at']),
                request_hash=row['request_hash'], replayed=True,
                **{name: getattr(payload, name) for name in ('action', 'reason', 'registration_request_hash',
                    'previous_event_id', 'previous_request_hash', 'physical_at', 'carrier', 'tracking_no')}))
    except (ValueError, KeyError, TypeError, AuditChainError, registration.query.MaterialRequestReadError):
        _fail('history_invalid', 'service_unavailable', '退回进展、前置事实或审计不一致')
    return tuple(result)


def record_rejection_progress(db, *, actor, request_id, return_id, payload, idempotency_key, secret, trace_request_id):
    payload = RejectionProgressIn.model_validate(payload)
    trace = lifecycle._require_trace_request_id(trace_request_id)
    context, request = registration._context(db, actor, request_id, write=True)
    actor = context.principal
    parent = _parent(db, context, request, return_id)
    key = _key(actor, request_id, return_id, idempotency_key, secret)
    digest = _digest(actor, request_id, return_id, payload)
    existing = db.execute(select(progress).where(progress.c.idempotency_key_hash == key)).mappings().one_or_none()
    chain = _checked_chain(db, context, request, parent)
    if existing is not None:
        if existing['request_hash'] != digest:
            _fail('key_reused', 'conflict', '原请求键已经绑定其他退回进展')
        found = next((event for event in chain if event.event_id == existing['id']), None)
        if found is None:
            _fail('history_invalid', 'service_unavailable', '原命令不属于完整的退回进展链')
        return found
    grant = _authority(db, actor, request, payload.action)
    if request.version != payload.expected_request_version:
        _fail('version_changed', 'conflict', '需求版本已变化，请重新核对')
    if (request.status not in ('approved', 'partially_approved')
            or db.scalar(select(registration.closures.c.id).where(registration.closures.c.request_id == request.id))
            or db.scalar(select(registration.cancellations.c.id).where(registration.cancellations.c.request_id == request.id))):
        _fail('closed', 'precondition_failed', '当前需求不能新增退回进展')
    if db.scalar(select(progress.c.id).where(progress.c.actor_user_id == actor.user_id, progress.c.trace_request_id == trace)):
        _fail('trace_reused', 'conflict', '请求编号已经绑定其他退回进展')
    now = lifecycle._database_now(db)
    _transition(parent, chain, payload, now)
    evidence = _evidence(parent, payload)
    row = dict(id=uuid4(), return_id=return_id, request_id=request_id, request_version=request.version,
        actor_user_id=actor.user_id, actor_person_id=actor.person_id, actor_role_assignment_id=grant.assignment_id,
        authorization_version=actor.authorization_version, idempotency_key_hash=key, request_hash=digest,
        trace_request_id=trace, evidence_sha256=lifecycle._canonical_hash(evidence), evidence_jsonb=evidence,
        recorded_at=now, **{name: getattr(payload, name) for name in ('action', 'reason', 'registration_request_hash',
            'previous_event_id', 'previous_request_hash', 'physical_at', 'carrier', 'tracking_no')})
    _authority(db, actor, request, payload.action)
    db.execute(progress.insert().values(**row))
    append_audit_event(db, stream_key='material_request', actor_user_id=actor.user_id,
        action='material_request.rejection_return.' + payload.action, aggregate_type=AGGREGATE, aggregate_id=str(row['id']),
        before_jsonb={'rejection_return': STATES[chain[-1].action] if chain else 'registered'},
        after_jsonb=_audit(row), request_id=trace, occurred_at=now, created_at=now)
    db.flush()
    return next(event.model_copy(update={'replayed': False}) for event in _checked_chain(db, context, request, parent)
        if event.event_id == row['id'])


def _read(db, actor, request_id, return_id, *, lookup=None):
    with db.no_autoflush:
        context, request = registration._context(db, actor, request_id)
        version = request.version
        parent = _parent(db, context, request, return_id)
        chain = _checked_chain(db, context, request, parent)
        result = (RejectionProgressStateOut(return_id=return_id, request_id=request_id,
            registration_request_hash=parent['request_hash'], status=STATES[chain[-1].action] if chain else 'registered',
            events=chain) if lookup is None else lookup(context, chain))
        latest, current = registration._context(db, actor, request_id)
        if latest.principal != context.principal or current.version != version:
            _fail('context_changed', 'precondition_failed', '读取期间身份或需求发生变化')
        return result


def rejection_progress_state(db, *, actor, request_id, return_id):
    return _read(db, actor, request_id, return_id)


def rejection_progress_command_status(db, *, actor, request_id, return_id, idempotency_key=None, secret=None, trace_request_id=None, request_fingerprint=None):
    if (idempotency_key is None) == (trace_request_id is None):
        _fail('lookup_invalid', 'invalid_request', '请提供唯一的原请求查询坐标')
    def lookup(context, chain):
        statement = select(progress.c.id, progress.c.evidence_jsonb).where(progress.c.return_id == return_id,
            progress.c.request_id == request_id, progress.c.actor_user_id == context.principal.user_id)
        if idempotency_key is not None:
            statement = statement.where(progress.c.idempotency_key_hash == _key(context.principal, request_id, return_id, idempotency_key, secret))
        else:
            statement = statement.where(progress.c.trace_request_id == lifecycle._require_trace_request_id(trace_request_id))
        row = db.execute(statement).mappings().one_or_none()
        if row is not None and request_fingerprint is not None and request_fingerprint != lifecycle._canonical_hash(row['evidence_jsonb']['input']):
            _fail('fingerprint_mismatch', 'conflict', '原退回进展请求指纹不一致')
        return next((event for event in chain if row is not None and event.event_id == row['id']), None)
    return _read(db, actor, request_id, return_id, lookup=lookup)
