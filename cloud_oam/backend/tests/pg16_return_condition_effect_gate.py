"""Commit-level business effects checks; only owned API sessions, no disabled guard."""
from contextlib import ExitStack
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import UUID, uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import OutboxEvent, StateTransitionEvent, NotificationEvent, NotificationPersonTarget
from app.formal_services.stock_loss_corrections import return_condition_business_events as business
from app.formal_services.stock_loss_corrections import return_condition_submission as writer
from app.formal_services.stock_loss_corrections import return_condition_recovery as recovery
from pg16_return_condition_key_gate import refused


def before_commit(api, *, command, original):
    report=[]
    for missing, message in (
        ('audit','condition exact business audit required'),
        ('state','condition exact business state required'),
        ('outbox','condition exact business outbox required'),
        ('notification','condition exact business notification targets required'),
        ('target','condition exact business notification targets required'),
    ):
        with Session(api) as db, ExitStack() as patches:
            # Omit only the selected effect at its real creation point. Leave
            # inventory, audit chain, registration and every SQL trigger active.
            if missing=='audit':
                patches.enter_context(patch.object(business,'append_audit_event',return_value=None))
            elif missing=='notification':
                patches.enter_context(patch.object(business,'record_business_notification',return_value=None))
            elif missing=='target':
                real=business.record_business_notification
                def no_target(db, **kwargs):
                    kwargs['recipient_person_id']=None
                    return real(db,**kwargs)
                patches.enter_context(patch.object(business,'record_business_notification',no_target))
            else:
                add=db.add; skipped=StateTransitionEvent if missing=='state' else OutboxEvent
                def omit(instance,*args,**kwargs):
                    if not (isinstance(instance,skipped) and instance.aggregate_type=='stock_condition_event'):
                        return add(instance,*args,**kwargs)
                patches.enter_context(patch.object(db,'add',omit))
            patches.enter_context(patch.object(business,'verify',side_effect=lambda db,**kw:business.payload(kw['case'],kw['event'])))
            writer.submit(db,actor=load_formal_principal(db,original['receiverUserId']),request=command)
            refused(db,db.commit,message)
        report.append(missing+'_missing_actual_commit_refused')
    return report


def after_commit(owner, api, *, command, original, result):
    event_id=UUID(result['event_id']); report=[]
    # All four namespaces reject an extra business row via the union count.
    # The audit example uses the real chained writer, not a fabricated hash.
    for name in ('audit','state','outbox','notification'):
        with Session(api) as db:
            at=datetime.now(timezone.utc)
            if name=='audit':
                from app.formal_services.inventory_posting import _lock_inventory_ledger_head_for_atomic_batch
                _lock_inventory_ledger_head_for_atomic_batch(db)
                business.append_audit_event(db,stream_key='inventory',actor_user_id=original['receiverUserId'],
                    action='stock_condition.submit',aggregate_type='stock_condition_event',aggregate_id=str(event_id),
                    before_jsonb={},after_jsonb=result,request_id='stock_condition.submit:'+str(uuid4()),
                    occurred_at=at,created_at=at)
            else:
                model={'state':StateTransitionEvent,'outbox':OutboxEvent,'notification':NotificationEvent}[name]
                table=model.__table__
                clause=(table.c.business_type=='stock_condition_event')&(table.c.business_id==str(event_id)) if name=='notification' else (
                    (table.c.aggregate_type=='stock_condition_event')&(table.c.aggregate_id==str(event_id)))
                row=dict(db.execute(select(table).where(clause)).mappings().one())
                row['id']=uuid4();row['dedup_key' if name=='notification' else 'idempotency_key']='stock_condition.submit:'+str(uuid4())
                db.execute(table.insert(),row)
            label={'audit':'audit','state':'state','outbox':'outbox','notification':'notification targets'}[name]
            refused(db,db.commit,'condition exact business '+label+' required')
        report.append(name+'_extra_actual_commit_refused')
    with Session(api) as db:
        row=db.scalar(select(OutboxEvent).where(OutboxEvent.aggregate_id==str(event_id),
            OutboxEvent.aggregate_type=='stock_condition_event'))
        box_id=row.id
        note=db.scalar(select(NotificationEvent).where(NotificationEvent.business_id==str(event_id),
            NotificationEvent.business_type=='stock_condition_event'))
        note_id=note.id
        target_id=db.scalar(select(NotificationPersonTarget.id).where(NotificationPersonTarget.event_id==note_id))
    for label,statement in (
        ('payload',OutboxEvent.__table__.update().where(OutboxEvent.id==box_id).values(payload_jsonb={})),
        ('move_namespace',OutboxEvent.__table__.update().where(OutboxEvent.id==box_id)
            .values(aggregate_type='hidden',event_type='hidden',idempotency_key=uuid4().hex,payload_jsonb={})),
        ('delete_outbox',OutboxEvent.__table__.delete().where(OutboxEvent.id==box_id)),
        ('delete_target',NotificationPersonTarget.__table__.delete().where(NotificationPersonTarget.id==target_id)),
    ):
        # The owned migrator can issue the mutation; this demonstrates the
        # immutable trigger even when runtime table privileges already deny it.
        with Session(owner) as db:
            refused(db,lambda:db.execute(statement),'condition business effect facts are immutable')
        report.append(label+'_owner_mutation_refused')
    # Runtime outbox updates are not granted by the current role policy.
    # Prove that boundary; use the owned migrator only to test the candidate's
    # mutable-field contract, never describe it as a production dispatcher.
    with Session(api) as db:
        refused(db,lambda:db.execute(OutboxEvent.__table__.update().where(OutboxEvent.id==box_id)
            .values(status='failed')),'permission denied',code='42501')
    with Session(owner) as db:
        t=OutboxEvent.__table__
        before=dict(db.execute(select(t).where(t.c.id==box_id)).mappings().one())
        fields=('status','attempts','available_at','locked_at','locked_by','published_at','last_error','updated_at')
        db.execute(t.update().where(t.c.id==box_id).values(status='failed',attempts=before['attempts']+1,
            last_error='owned retry metadata probe',updated_at=datetime.now(timezone.utc)))
        db.commit()
    def original_lookup():
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            answer=recovery.lookup(db,actor=load_formal_principal(db,original['receiverUserId']),request=command)
            assert answer['request_state']=='found' and answer['result']==result
    original_lookup()
    with Session(owner) as db:
        db.execute(t.update().where(t.c.id==box_id).values(**{k:before[k] for k in fields}))
        db.commit()
    # Use the existing real API expansion service. An expanded notification
    # cannot be reset to pending without its independently proved new target.
    # Preserve the legitimate new status/deliveries in the final test snapshot.
    from app.formal_services.notification_expansion import expand_notification_event
    from app.foundation_models import NotificationDelivery, NotificationRecipient
    with Session(api) as db:
        recipients=tuple(db.scalars(select(NotificationRecipient.id).where(
            NotificationRecipient.event_id==note_id,NotificationRecipient.status=='active')))
        old_deliveries=set(db.scalars(select(NotificationDelivery.id).where(NotificationDelivery.recipient_id.in_(recipients))))
        expanded=expand_notification_event(db,event_id=note_id)
        assert db.scalar(text('SELECT current_user'))=='star_oam_api'
        assert expanded.event_status=='expanded'
        db.commit()
    with Session(api) as db:
        repeated=expand_notification_event(db,event_id=note_id)
        assert repeated.created_delivery_count==0 and repeated.event_status=='expanded'
        db.commit()
        created=tuple(db.scalars(select(NotificationDelivery).where(NotificationDelivery.recipient_id.in_(recipients),
            NotificationDelivery.id.not_in(old_deliveries))))
        assert len(created)==expanded.created_delivery_count
        assert all(row.status=='queued' and row.attempts==0 for row in created)
        created_ids=[str(row.id) for row in created]
    original_lookup()
    return dict(refusals=report,runtimeOutboxUpdateDenied=True,ownerOutboxMetadataCommit=True,
        outboxMetadataRestored=True,outboxDispatcherVerified=False,
        actualApiNotificationExpansion=True,expansionReplayCreatesNoDuplicates=True,
        notificationEventId=str(note_id),notificationStatus='expanded',createdDeliveryIds=created_ids,
        originalLookupAfterMetadataCommit=True,providerDelivery=False)
