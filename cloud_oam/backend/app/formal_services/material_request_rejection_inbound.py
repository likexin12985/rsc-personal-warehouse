"""Post one accepted warehouse receipt atomically; caller owns commit/rollback.

The public boundary owns the transaction. This service never changes the
original refusal or demand quantity.
"""
from uuid import UUID, uuid4
from sqlalchemy import select

from app.foundation_models import AuditEvent, OutboxEvent
from app.inventory_models import StockAccount
from app.material_request_rejection_inbound_schema import inbounds, parts, serials
from app.material_request_rejection_inbound_schemas import RejectionInboundIn
from . import material_request_rejection_inbound_plan as plans
from . import material_request_rejection_inbound_facts as facts
from .notification_events import record_business_notification
from .audit_chain import append_audit_event
from .postgresql_lock_graph import lock_inventory_reference_graph, lock_inventory_serial_graph

acceptance = plans.acceptance
posting = acceptance.posting


def key_for(actor, return_id, receipt_id, key, secret):
    return acceptance.lifecycle._idempotency_hmac(acceptance.lifecycle._require_hmac_secret(secret), actor.user_id,
        f'/api/v1/rejection-returns/{return_id}/warehouse-receipts/{receipt_id}/inbounds',
        acceptance.lifecycle._require_idempotency_key(key))


def post(db, *, actor, return_id, receipt_id, payload, idempotency_key, secret, trace_request_id):
    payload = RejectionInboundIn.model_validate(payload.model_dump() if isinstance(payload, RejectionInboundIn) else payload)
    trace = acceptance.lifecycle._require_trace_request_id(trace_request_id)
    context = acceptance._context(db, actor, return_id, write=True)
    actor, parent, source, location, custody, request, stamp = context
    key = key_for(actor, return_id, receipt_id, idempotency_key, secret)
    digest = facts.digest(actor, return_id, receipt_id, payload)
    old = db.execute(select(inbounds).where(inbounds.c.idempotency_key_hash == key)).mappings().one_or_none()
    if old is not None:
        if old['request_hash'] != digest:
            plans.fail('key_reused', 'conflict', '原请求键已绑定不同仓库入账内容')
        return facts.verify(db, context, old)
    grant = acceptance._authority(db, context)
    if db.scalar(select(inbounds.c.id).where(inbounds.c.actor_user_id == actor.user_id, inbounds.c.trace_request_id == trace)):
        plans.fail('trace_reused', 'conflict', '请求编号已绑定仓库入账')
    if db.scalar(select(AuditEvent.id).where(AuditEvent.actor_user_id == actor.user_id,
            AuditEvent.request_id.in_((trace, posting._request_reference(trace)))).limit(1)):
        plans.fail('trace_reused', 'conflict', '请求编号已绑定其他业务事实')
    plan = plans.build(db, context, receipt_id)
    if payload.expected_request_version != request.version or payload.receipt_request_hash != plan['receipt_request_hash']:
        plans.fail('context_changed', 'conflict', '原需求或验收证据发生变化')
    if payload.expected_plan_hash != acceptance.lifecycle._canonical_hash(plan):
        plans.fail('plan_changed', 'conflict', '入账方案已变化，请重新核验')
    account_ids = {source.id, UUID(plan['source_account_id'])}
    for part in plan['parts']:
        identifier = UUID(part['target_account_id'])
        if db.get(StockAccount, identifier, populate_existing=True) is not None:
            account_ids.add(identifier)
    now = acceptance.lifecycle._database_now(db)
    lock_inventory_reference_graph(db, tuple(sorted(account_ids, key=str)), now)
    lock_inventory_serial_graph(db, tuple(UUID(s) for s in sorted({s for p in plan['parts'] for s in p['serial_ids']})))
    current = acceptance._context(db, actor, return_id)
    if current[-1] != stamp or plans.build(db, current, receipt_id) != plan:
        plans.fail('context_changed', 'conflict', '入账期间权限、责任或库存发生变化')
    acceptance._authority(db, current)
    # Only exact recomputed dimensions may be materialized. Formal database
    # admission must additionally bind each new account to this first posting.
    for part in plan['parts']:
        identifier = UUID(part['target_account_id'])
        if db.get(StockAccount, identifier) is None:
            fields = {k: UUID(v) if v is not None and k.endswith('_id') else v
                for k, v in part['target_dimensions'].items()}
            db.add(StockAccount(id=identifier, **fields, created_at=now, updated_at=now))
    db.flush()
    identifier = uuid4()
    command = plans.command(plan, inbound_id=identifier, at=now)
    result = posting.post_inventory_transaction(db, actor=actor, command=command, idempotency_key=key,
        request_id=trace, permission_resource='stock_operation', permission_action='receive_return')
    original, checked = plans.receipt(db, context, receipt_id)
    row = dict(id=identifier, receipt_id=receipt_id, return_id=return_id, request_id=request.id,
        request_version=request.version, source_account_id=UUID(plan['source_account_id']), target_location_id=location.id,
        custody_assignment_id=custody.id, actor_user_id=actor.user_id, actor_person_id=actor.person_id,
        actor_role_assignment_id=grant.assignment_id, authorization_version=actor.authorization_version,
        posting_transaction_id=result.transaction_id, accepted_qty=checked.amounts.accepted_qty,
        damaged_qty=checked.amounts.damaged_qty, reason=payload.reason, recorded_at=now,
        idempotency_key_hash=key, trace_request_id=trace, request_hash=digest,
        plan_hash=payload.expected_plan_hash, plan_jsonb=plan)
    db.execute(inbounds.insert().values(**row))
    for part in plan['parts']:
        db.execute(parts.insert().values(inbound_id=identifier, condition_code=part['condition_code'],
            target_account_id=UUID(part['target_account_id']), quantity=part['quantity']))
        if part['serial_ids']:
            db.execute(serials.insert(), [dict(inbound_id=identifier, condition_code=part['condition_code'], serial_id=UUID(s))
                for s in part['serial_ids']])
    body = facts.body(row)
    append_audit_event(db, stream_key='material_request', actor_user_id=actor.user_id,
        action=facts.ACTION, aggregate_type=facts.AGGREGATE, aggregate_id=str(identifier),
        before_jsonb={'warehouse_inbound': 'not_posted'}, after_jsonb=body, request_id=trace, occurred_at=now, created_at=now)
    db.add(OutboxEvent(event_type=facts.ACTION, aggregate_type=facts.AGGREGATE, aggregate_id=str(identifier),
        payload_jsonb=body, idempotency_key='rejection-inbound:' + str(identifier), available_at=now, created_at=now, updated_at=now))
    record_business_notification(db, event_type=facts.ACTION, business_type=facts.AGGREGATE, business_id=identifier,
        dedup_key='rejection-inbound-notification:' + str(identifier), payload=body, recipient_person_id=None,
        recipient_person_ids=tuple(UUID(p) for p in plan['notification_person_ids']), occurred_at=now, now=now)
    db.flush()
    return facts.verify(db, context, row, replayed=False)


def command_status(db, *, actor, return_id, receipt_id, idempotency_key=None, secret=None, trace_request_id=None, request_fingerprint=None):
    if (idempotency_key is None) == (trace_request_id is None):
        plans.fail('lookup_invalid', 'invalid_request', '请提供唯一原请求查询坐标')
    with db.no_autoflush:
        context = acceptance._context(db, actor, return_id)
        statement = select(inbounds).where(inbounds.c.return_id == return_id,
            inbounds.c.receipt_id == receipt_id, inbounds.c.actor_user_id == context[0].user_id)
        if idempotency_key is not None:
            statement = statement.where(inbounds.c.idempotency_key_hash == key_for(context[0], return_id, receipt_id, idempotency_key, secret))
        else:
            statement = statement.where(inbounds.c.trace_request_id == acceptance.lifecycle._require_trace_request_id(trace_request_id))
        row = db.execute(statement).mappings().one_or_none()
        result = facts.verify(db, context, row) if row is not None else None
        if row is not None and request_fingerprint is not None:
            payload = RejectionInboundIn(expected_request_version=row['request_version'], reason=row['reason'],
                receipt_request_hash=row['plan_jsonb']['receipt_request_hash'], expected_plan_hash=row['plan_hash'])
            if request_fingerprint != acceptance.lifecycle._canonical_hash(payload.model_dump(mode='json')):
                plans.fail('fingerprint_mismatch', 'conflict', '原仓库入账请求指纹不一致')
        if acceptance._context(db, context[0], return_id)[-1] != context[-1]:
            plans.fail('context_changed', 'conflict', '读取期间接收责任或需求变化')
        return result
