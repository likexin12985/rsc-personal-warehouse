"""Transactional scrap events; records intent, never channel delivery."""
from types import SimpleNamespace
from sqlalchemy import select
from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent, NotificationEvent, NotificationPersonTarget
from app.formal_services import stock_loss_facts as facts
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.notification_events import record_business_notification, target_manifest_hash
from app.formal_services.stock_loss_sources import _hash
from app.formal_services.work_order_query import _aware


def payload(records):
    order = records['stock_operation_orders'][0]
    line = records['stock_scrap_lines'][0]
    return dict(scrap_operation_id=str(order['id']), scrap_line_id=str(line['id']),
        source_kind=line['source_kind'], root_disposition_id=str(line['root_disposition_id']),
        correction_execution_id=str(line['correction_execution_id']) if line['correction_execution_id'] else None,
        posting_transaction_id=str(line['posting_transaction_id']), posting_movement_id=str(line['posting_movement_id']),
        quantity=format(line['quantity'], '.3f'), source_account_id=str(line['frozen_account_id']),
        target_account_id=None, status='posted', stock_effect='removed_from_managed_assets',
        request_id=order['request_id'], request_hash=order['request_hash'], plan_hash=order['plan_hash'])


def _record(db, *, aggregate, identifier, kind, body, order, notify):
    at = order['created_at']; key = kind + ':' + str(identifier)
    append_audit_event(db, stream_key='inventory', actor_user_id=order['actor_user_id'], action=kind,
        aggregate_type=aggregate, aggregate_id=str(identifier), before_jsonb={}, after_jsonb=body,
        request_id=order['request_id'], occurred_at=at, created_at=at)
    db.add(OutboxEvent(event_type=kind, aggregate_type=aggregate, aggregate_id=str(identifier),
        payload_jsonb=body, idempotency_key=key, available_at=at, created_at=at, updated_at=at))
    db.add(StateTransitionEvent(aggregate_type=aggregate, aggregate_id=str(identifier), from_status='pending',
        to_status='posted', actor_id=order['actor_user_id'], reason=kind, idempotency_key=key,
        occurred_at=at, metadata_jsonb=body, created_at=at))
    if notify:
        record_business_notification(db, event_type=kind, business_type=aggregate, business_id=identifier,
            dedup_key=key, payload=body, recipient_person_id=order['requester_id'], occurred_at=at, now=at)
    db.flush()


def _parent(db, records):
    from app.stock_operation_models import StockLossDisposition, StockOperationOrder
    from app.stock_loss_correction_models import StockLossCorrectionExecution
    from app.formal_services.stock_loss_corrections.business_events import payload as correction_payload
    from .request_facts import original_payload
    line = records['stock_scrap_lines'][0]
    root = db.get(StockLossDisposition, line['root_disposition_id'], populate_existing=True)
    order = db.get(StockOperationOrder, root.operation_id, populate_existing=True)
    if line['source_kind'] == 'original':
        return dict(aggregate='stock_loss_disposition', identifier=root.id,
            kind='stock_loss.disposition_posted', body=original_payload(root))
    correction = db.get(StockLossCorrectionExecution, line['correction_execution_id'], populate_existing=True)
    return dict(aggregate='stock_loss_correction_execution', identifier=correction.id,
        kind='stock_loss.correction_posted', body=correction_payload(correction, root=root, order=order))


def record(db, *, records):
    order = records['stock_operation_orders'][0]
    body = payload(records)
    # One physical fact produces one recipient notification through its
    # existing disposition/correction identity. The child order has separate
    # audit/state/outbox evidence, without a second alert to the same person.
    _record(db, **_parent(db, records), order=order, notify=True)
    _record(db, aggregate='stock_operation_scrap', identifier=order['id'], kind='stock_scrap.posted',
        body=body, order=order, notify=False)
    return body


def _verify(db, *, aggregate, identifier, kind, body, order, notify):
    key = kind + ':' + str(identifier)
    observed = []
    for model in (AuditEvent, OutboxEvent, StateTransitionEvent):
        observed.append(facts.single(db, model, aggregate_type=aggregate, aggregate_id=str(identifier)))
    facts.audit(db, actor=SimpleNamespace(user_id=order['actor_user_id']), stream='inventory',
        aggregate_type=aggregate, identifier=identifier, action=kind, request_id=order['request_id'], before={}, after=body)
    facts.single(db, OutboxEvent, aggregate_type=aggregate, aggregate_id=str(identifier), event_type=kind,
        payload_jsonb=body, idempotency_key=key)
    facts.single(db, StateTransitionEvent, aggregate_type=aggregate, aggregate_id=str(identifier),
        from_status='pending', to_status='posted', actor_id=order['actor_user_id'], reason=kind,
        idempotency_key=key, metadata_jsonb=body)
    if (any(_aware(e.created_at) != _aware(order['created_at']) for e in observed)
            or any(_aware(e.occurred_at) != _aware(order['created_at']) for e in (observed[0], observed[2]))
            or any(_hash(value) != _hash(body) for value in (
                observed[0].after_jsonb, observed[1].payload_jsonb, observed[2].metadata_jsonb))):
        raise ValueError('scrap event bytes or timestamps differ from the exact stock result')
    if notify:
        notification = facts.single(db, NotificationEvent, business_type=aggregate, business_id=str(identifier))
        targets = tuple(db.scalars(select(NotificationPersonTarget.person_id).where(NotificationPersonTarget.event_id == notification.id)))
        if (notification.event_type != kind or _hash(notification.payload_jsonb) != _hash(body)
                or notification.dedup_key != key or targets != (order['requester_id'],)
                or notification.target_manifest_sha256 != target_manifest_hash(targets)
                or _aware(notification.created_at) != _aware(order['created_at'])
                or _aware(notification.occurred_at) != _aware(order['created_at'])):
            raise ValueError('scrap notification intent differs from the exact stock result')
    elif db.scalar(select(NotificationEvent.id).where(NotificationEvent.business_type == aggregate,
                                                    NotificationEvent.business_id == str(identifier))) is not None:
        raise ValueError('scrap child must not duplicate the parent stock notification')


def verify(db, *, records):
    order = records['stock_operation_orders'][0]
    body = payload(records)
    _verify(db, **_parent(db, records), order=order, notify=True)
    _verify(db, aggregate='stock_operation_scrap', identifier=order['id'], kind='stock_scrap.posted',
        body=body, order=order, notify=False)
    return body
