"""Independent recovery approval events, with explicitly no inventory effect."""
from types import SimpleNamespace
from sqlalchemy import select
from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent, NotificationEvent, NotificationPersonTarget
from app.formal_services import stock_loss_facts as facts, stock_loss_sources as sources
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.notification_events import record_business_notification, target_manifest_hash
from app.formal_services.work_order_query import _aware


def state(row, stage):
    if stage == 'apply':
        return 'unsubmitted', 'awaiting_regional'
    if stage == 'regional':
        return 'awaiting_regional', 'awaiting_headquarters' if row['decision'] == 'verified' else 'needs_evidence'
    return 'awaiting_headquarters', 'approved_pending_execution' if row['decision'] == 'approve' else 'awaiting_regional'


def payload(row, stage):
    return dict(fact_id=str(row['id']), recovery_request_id=str(row['id'] if stage == 'apply' else row['recovery_request_id']),
        scrap_line_id=str(row['scrap_line_id']), stage=stage, status=state(row, stage)[1], stock_effect='none',
        actor_person_id=str(row['actor_person_id']), authorization_version=row['authorization_version'],
        request_id=row['request_id'], request_hash=row['request_hash'], reason=row['reason'],
        decision=row.get('decision'), regional_review_id=str(row['regional_review_id']) if stage == 'headquarters' else None)


def coordinates(row, stage):
    aggregate = 'stock_scrap_recovery_' + stage
    kind = 'stock_scrap.recovery_' + stage
    return aggregate, kind, kind + ':' + str(row['id'])


def record(db, *, row, stage, recipient):
    aggregate, kind, key = coordinates(row, stage)
    body, at = payload(row, stage), row['created_at']
    before, after = state(row, stage)
    append_audit_event(db, stream_key='inventory', actor_user_id=row['actor_user_id'], action=kind,
        aggregate_type=aggregate, aggregate_id=str(row['id']), before_jsonb={}, after_jsonb=body,
        request_id=key, occurred_at=at, created_at=at)
    db.add(OutboxEvent(event_type=kind, aggregate_type=aggregate, aggregate_id=str(row['id']),
        payload_jsonb=body, idempotency_key=key, available_at=at, created_at=at, updated_at=at))
    db.add(StateTransitionEvent(aggregate_type=aggregate, aggregate_id=str(row['id']), from_status=before,
        to_status=after, actor_id=row['actor_user_id'], reason=kind, idempotency_key=key,
        occurred_at=at, metadata_jsonb=body, created_at=at))
    record_business_notification(db, event_type=kind, business_type=aggregate, business_id=row['id'],
        dedup_key=key, payload=body, recipient_person_id=recipient, occurred_at=at, now=at)
    db.flush()


def verify(db, *, row, stage, recipient):
    aggregate, kind, key = coordinates(row, stage)
    body, at = payload(row, stage), _aware(row['created_at'])
    before, after = state(row, stage)
    audit, outbox, transition = [facts.single(db, model, aggregate_type=aggregate, aggregate_id=str(row['id']))
        for model in (AuditEvent, OutboxEvent, StateTransitionEvent)]
    facts.audit(db, actor=SimpleNamespace(user_id=row['actor_user_id']), stream='inventory',
        aggregate_type=aggregate, identifier=row['id'], action=kind, request_id=key, before={}, after=body)
    facts.single(db, OutboxEvent, aggregate_type=aggregate, aggregate_id=str(row['id']),
        event_type=kind, payload_jsonb=body, idempotency_key=key)
    facts.single(db, StateTransitionEvent, aggregate_type=aggregate, aggregate_id=str(row['id']),
        from_status=before, to_status=after, actor_id=row['actor_user_id'], reason=kind,
        idempotency_key=key, metadata_jsonb=body)
    note = facts.single(db, NotificationEvent, business_type=aggregate, business_id=str(row['id']))
    targets = tuple(db.scalars(select(NotificationPersonTarget.person_id).where(NotificationPersonTarget.event_id == note.id)))
    if (any(_aware(e.created_at) != at for e in (audit, outbox, transition, note))
            or any(_aware(e.occurred_at) != at for e in (audit, transition, note))
            or note.event_type != kind or note.dedup_key != key or sources._hash(note.payload_jsonb) != sources._hash(body)
            or targets != (recipient,) or note.target_manifest_sha256 != target_manifest_hash(targets)):
        facts.invalid()
    return body
