"""Candidate correction audit/state/outbox/notification-intent bundles.

Private transaction component only. Callers must already have proved/posted
the complete business operation and retain responsibility for rollback. No
channel is called, no delivery is claimed, and no commit is performed here.
"""
from types import SimpleNamespace

from sqlalchemy import select

from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent, NotificationEvent, NotificationPersonTarget
from app.formal_services import stock_loss_facts as original
from app.formal_services import stock_loss_sources as sources
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.notification_events import record_business_notification, target_manifest_hash
from app.formal_services.work_order_query import _aware
from .correction_models import StockLossDispositionReversal as Inverse
from .correction_models import StockLossCorrectionDecision as Decision, StockLossCorrectionExecution as Execution
from .chain_projection import InvalidChain


KINDS = {
    Inverse: ('stock_loss_disposition_reversal','stock_loss.disposition_reversed','pending','posted'),
    Decision: ('stock_loss_correction_decision','stock_loss.correction_approved','awaiting_headquarters','approved'),
    Execution: ('stock_loss_correction_execution','stock_loss.correction_posted','pending','posted'),
}


def _need(condition):
    if not condition:
        raise InvalidChain('correction_business_event_evidence_invalid')


def payload(row, *, root, order):
    _need(type(row) in KINDS and row.root_disposition_id==root.id and root.operation_id==order.id
        and order.operation_type=='loss_report')
    common=dict(root_disposition_id=str(root.id),operation_id=str(order.id),line_id=str(root.line_id),
        original_headquarters_decision_id=str(root.headquarters_decision_id),
        requester_person_id=str(order.requester_id),actor_user_id=row.actor_user_id,
        actor_person_id=str(row.actor_person_id),authorization_version=row.authorization_version,
        reason=row.reason,request_id=row.request_id,request_hash=row.request_hash)
    if type(row) is Decision:
        return dict(common,correction_decision_id=str(row.id),reversal_id=str(row.reversal_id),
            expected_reversal_hash=row.expected_reversal_hash,disposition=row.disposition,
            approval_stage='approved',stock_effect='none')
    common.update(posting_transaction_id=str(row.posting_transaction_id),posting_movement_id=str(row.posting_movement_id),
        source_account_id=str(row.source_account_id) if row.source_account_id else None,
        target_account_id=str(row.target_account_id) if row.target_account_id else None,
        quantity=format(row.quantity,'.3f'),plan_hash=row.plan_hash,status='posted')
    if type(row) is Inverse:
        return dict(common,reversal_id=str(row.id),original_execution_id=str(row.reversed_correction_id or root.id),
            reversed_correction_id=str(row.reversed_correction_id) if row.reversed_correction_id else None,
            original_transaction_id=str(row.original_transaction_id),original_movement_id=str(row.original_movement_id),
            stock_effect='restores_original_frozen_share')
    effect={'restore_available':'frozen_to_available','convert_used':'frozen_to_used',
        'convert_damaged':'frozen_to_damaged','return_to_region':'frozen_to_return_pending',
        'scrap':'removed_from_managed_assets'}[row.disposition]
    return dict(common,correction_execution_id=str(row.id),correction_decision_id=str(row.correction_decision_id),
        reversal_id=str(row.reversal_id),disposition=row.disposition,
        return_operation_id=str(row.return_operation_id) if row.return_operation_id else None,
        return_fulfillment_required=row.disposition=='return_to_region',stock_effect=effect)


def record(db, *, row, root, order):
    body=payload(row,root=root,order=order)
    aggregate,kind,from_status,to_status=KINDS[type(row)]
    key=kind+':'+str(row.id);at=_aware(row.created_at)
    append_audit_event(db,stream_key='inventory',actor_user_id=row.actor_user_id,action=kind,
        aggregate_type=aggregate,aggregate_id=str(row.id),before_jsonb={},after_jsonb=body,
        request_id=row.request_id,occurred_at=at,created_at=at)
    db.add(OutboxEvent(event_type=kind,aggregate_type=aggregate,aggregate_id=str(row.id),
        payload_jsonb=body,idempotency_key=key,available_at=at,created_at=at,updated_at=at))
    db.add(StateTransitionEvent(aggregate_type=aggregate,aggregate_id=str(row.id),from_status=from_status,
        to_status=to_status,actor_id=row.actor_user_id,reason=kind,idempotency_key=key,
        occurred_at=at,metadata_jsonb=body,created_at=at))
    record_business_notification(db,event_type=kind,business_type=aggregate,business_id=row.id,
        dedup_key=key,payload=body,recipient_person_id=order.requester_id,occurred_at=at,now=at)
    db.flush()


def verify(db, *, row, root, order):
    """Historical intent verification, independent of current delivery state."""
    with db.no_autoflush:
        body=payload(row,root=root,order=order)
        aggregate,kind,from_status,to_status=KINDS[type(row)]
        key=kind+':'+str(row.id)
        # Count by object identity first. One correct event cannot hide another
        # conflicting event with a different key, payload, action or stream.
        audit=original.single(db,AuditEvent,aggregate_type=aggregate,aggregate_id=str(row.id))
        original.audit(db,actor=SimpleNamespace(user_id=row.actor_user_id),stream='inventory',
            aggregate_type=aggregate,identifier=row.id,action=kind,request_id=row.request_id,
            before={},after=body)
        _need(audit.stream_key=='inventory' and _aware(audit.created_at)==_aware(row.created_at)
            and _aware(audit.occurred_at)==_aware(row.created_at))
        for model in (OutboxEvent,StateTransitionEvent):
            original.single(db,model,aggregate_type=aggregate,aggregate_id=str(row.id))
        original.single(db,NotificationEvent,business_type=aggregate,business_id=str(row.id))
        outbox=original.single(db,OutboxEvent,event_type=kind,aggregate_type=aggregate,aggregate_id=str(row.id),
            idempotency_key=key,payload_jsonb=body)
        transition=original.single(db,StateTransitionEvent,aggregate_type=aggregate,aggregate_id=str(row.id),
            from_status=from_status,to_status=to_status,actor_id=row.actor_user_id,reason=kind,
            idempotency_key=key,metadata_jsonb=body)
        notification=original.single(db,NotificationEvent,event_type=kind,business_type=aggregate,
            business_id=str(row.id),dedup_key=key,payload_jsonb=body)
        # JSON/Python numeric equality admits 1.0 as 1. Prove the precise
        # canonical business payload independently of a valid audit hash chain
        # and of SQLAlchemy's identity-map/query equality behavior.
        expected_hash=sources._hash(body)
        _need(all(sources._hash(value)==expected_hash for value in (
            audit.after_jsonb,outbox.payload_jsonb,transition.metadata_jsonb,notification.payload_jsonb)))
        _need(all(_aware(event.created_at)==_aware(row.created_at) for event in (outbox,transition,notification))
            and _aware(transition.occurred_at)==_aware(row.created_at)
            and _aware(notification.occurred_at)==_aware(row.created_at))
        targets=tuple(db.scalars(select(NotificationPersonTarget.person_id).where(
            NotificationPersonTarget.event_id==notification.id)))
        _need(targets==(order.requester_id,) and notification.target_manifest_sha256==target_manifest_hash(targets))
        return body
