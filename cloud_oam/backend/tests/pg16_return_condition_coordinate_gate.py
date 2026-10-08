"""Actual API collision/orphan probes in the caller's owned candidate PG16.

Every synthetic orphan lives only in a rollback transaction. This tests service
admission/read refusal, not durable registration or native reverse constraints.
"""
from datetime import datetime, timezone
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import OutboxEvent, StateTransitionEvent, NotificationEvent
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_loss_corrections import return_condition_recovery as recovery
from app.formal_services.stock_loss_corrections import return_condition_submission as writer
from app.formal_services.inventory_posting import _request_reference


def refused(call):
    try:
        call()
    except InventoryReadError as error:
        assert error.code == 'return_condition_request_outcome_unknown', error.code
    else:
        raise AssertionError('conflicting request was accepted')


def run(api, *, command, original, found=False):
    report = []
    if not found:
        # This request is a real legacy receipt/inbound fact, not a mutated
        # fixture. Current source scope is valid; its actor/request collides.
        collision = command.model_copy(update={'request_id':original['inboundRequestId']})
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            actor = load_formal_principal(db, original['receiverUserId'])
            refused(lambda: recovery.lookup(db,actor=actor,request=collision))
            db.rollback()
        with Session(api) as db:
            actor = load_formal_principal(db, original['receiverUserId'])
            refused(lambda: writer.submit(db,actor=actor,request=collision))
            db.rollback()
        report.append('actual_legacy_inbound_request')
    for model in (OutboxEvent, StateTransitionEvent, NotificationEvent):
        for reference in (False, True):
            with Session(api) as db:
                assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
                actor = load_formal_principal(db, original['receiverUserId'])
                body = ({'request_reference':_request_reference(command.request_id)} if reference else
                        {'request_id':command.request_id})
                identifier = str(uuid4())
                if model is OutboxEvent:
                    # Reserved condition markers now fail at the database
                    # boundary. Generic residual evidence must still be
                    # detected by the separate service recovery scan.
                    row = model(event_type='unresolved.business_event',aggregate_type='detached_condition',
                        aggregate_id=identifier,payload_jsonb=body,idempotency_key=uuid4().hex,
                        available_at=datetime.now(timezone.utc))
                elif model is StateTransitionEvent:
                    row = model(aggregate_type='detached_condition',aggregate_id=identifier,
                        from_status='draft',to_status='submitted',actor_id=actor.user_id,
                        reason='owned uncertain outcome probe',idempotency_key=uuid4().hex,
                        occurred_at=datetime.now(timezone.utc),metadata_jsonb=body)
                else:
                    row = model(event_type='unresolved.business_event',business_type='detached_condition',
                        business_id=identifier,dedup_key=uuid4().hex,payload_jsonb=body,
                        occurred_at=datetime.now(timezone.utc),target_manifest_sha256='a'*64)
                db.add(row); db.flush()
                refused(lambda: recovery.lookup(db,actor=actor,request=command))
                if not found:
                    refused(lambda: writer.submit(db,actor=actor,request=command))
                db.rollback()
                report.append(model.__tablename__+(':reference' if reference else ':request'))
    return report
