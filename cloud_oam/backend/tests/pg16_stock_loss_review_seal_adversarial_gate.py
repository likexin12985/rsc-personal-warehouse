"""Raw API-role seal attempts cannot borrow authority or another review's facts."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.foundation_models import (
    Organization, Permission, RoleAssignment, RolePermission,
    OutboxEvent, StateTransitionEvent, NotificationEvent,
)
from app.inventory_models import StockLocation
from app.models import User
from app.stock_operation_models import (
    StockOperationOrder as Order, StockOperationLine as Line,
    StockLossRegionalReview as Regional, StockLossReviewRequestSeal as Seal,
)
from app.formal_services import stock_loss_review_seals as seals
from app.formal_services import stock_loss_sources as sources, inventory_posting as posting
from app.formal_services.audit_chain import append_audit_event


def raw_seal_refusals(engines, *, stage, reviewer_id, assignment_id, command, service, state):
    owner, api = (engines[key] for key in ('star_oam_migrator', 'star_oam_api'))
    with Session(owner) as db:
        assignment = db.get(RoleAssignment, assignment_id)
        permission_id = db.scalar(select(Permission.id).where(
            Permission.resource == 'stock_operation', Permission.action == service.ACTION,
            Permission.field_code == ''))
        grant_id = db.scalar(select(RolePermission.id).where(
            RolePermission.role_id == assignment.role_id, RolePermission.permission_id == permission_id))
        prior_scope = (assignment.scope_type, assignment.scope_id)
        prior_effect = db.get(RolePermission, grant_id).effect
        prior_active = db.get(User, reviewer_id).is_active
    faults = ['self', 'person', 'owner', 'deny', 'inactive', 'scope', 'extra_intent', 'unicode_comment']
    if stage == 'headquarters':
        faults += ['regional_parent', 'regional_hash', 'cross_line', 'missing_lines', 'duplicate_line', 'reason_control', 'unicode_reason']
    for fault in faults:
        try:
            with Session(owner) as db:
                if fault == 'deny': db.get(RolePermission, grant_id).effect = 'deny'
                if fault == 'inactive': db.get(User, reviewer_id).is_active = False
                if fault == 'scope':
                    assignment=db.get(RoleAssignment, assignment_id)
                    assignment.scope_type='organization'
                    assignment.scope_id=str(db.scalar(select(Organization.id).where(Organization.org_type=='headquarters')))
                db.commit()
            before = state()
            with Session(api) as db:
                parent = db.get(Order, command.operation_id)
                user = db.get(User, reviewer_id)
                # No service authorization/lookup is invoked: the database must
                # validate an otherwise complete seal and audit at COMMIT.
                reviewer_person = user.person_id
                assert reviewer_person is not None
                intent = service.intent(command)
                if fault == 'extra_intent': intent = dict(intent, stock_effect='none')
                if fault == 'unicode_comment': intent = dict(intent, comment='\u3000unexpected leading space')
                if fault in ('reason_control', 'unicode_reason'):
                    reason='before\rafter' if fault=='reason_control' else 'unexpected trailing space\u00a0'
                    intent=dict(intent, decisions=[dict(item, reason=reason) for item in intent['decisions']])
                if fault == 'regional_parent':
                    other = db.scalar(select(Regional).where(Regional.operation_id != parent.id))
                    assert other is not None
                    intent = dict(intent, regional_review_id=str(other.id), expected_regional_review_hash=other.request_hash)
                if fault == 'regional_hash': intent = dict(intent, expected_regional_review_hash='f'*64)
                if fault == 'cross_line':
                    other_line = db.scalar(select(Line.id).where(Line.operation_id != parent.id))
                    assert other_line is not None
                    intent = dict(intent, decisions=[dict(item, line_id=str(other_line)) for item in intent['decisions']])
                if fault == 'missing_lines': intent = dict(intent, decisions=[])
                if fault == 'duplicate_line': intent = dict(intent, decisions=[intent['decisions'][0]]*2)
                row = Seal(id=uuid4(), operation_id=parent.id, operation_type='loss_report', stage=stage,
                    owner_org_id=db.get(StockLocation, parent.source_location_id).owner_org_id,
                    actor_user_id=reviewer_id, reviewer_person_id=reviewer_person,
                    authorization_version=user.authorization_version, request_id=uuid4().hex,
                    idempotency_key_hash=posting._storage_hash('stock-loss-'+stage+'-review:'+uuid4().hex),
                    request_hash=sources._hash(intent), submission_plan_hash=parent.plan_hash,
                    command_intent_jsonb=intent, created_at=datetime.now(timezone.utc))
                if fault == 'self': row.actor_user_id=parent.actor_user_id; row.reviewer_person_id=parent.requester_id
                if fault == 'person': row.reviewer_person_id=parent.requester_id
                if fault == 'owner':
                    row.owner_org_id=db.scalar(select(Organization.id).where(Organization.org_type=='headquarters'))
                db.add(row); db.flush()
                append_audit_event(db, stream_key='inventory', actor_user_id=row.actor_user_id,
                    action=seals.KIND, aggregate_type=seals.AGGREGATE, aggregate_id=str(row.id),
                    before_jsonb={}, after_jsonb=seals.payload(row), request_id='stock-loss-review-seal:'+str(row.id),
                    occurred_at=row.created_at, created_at=row.created_at)
                with pytest.raises(DBAPIError) as caught: db.commit()
                assert caught.value.orig.sqlstate == '23514'
                db.rollback()
            assert state() == before, fault
        finally:
            with Session(owner) as db:
                db.get(RolePermission, grant_id).effect=prior_effect
                db.get(User, reviewer_id).is_active=prior_active
                assignment=db.get(RoleAssignment, assignment_id)
                assignment.scope_type, assignment.scope_id=prior_scope
                db.commit()
    return faults


def raw_fragment_refusals(engines, *, stage, actor, command, state):
    """Isolate each new database fence from older complete-review constraints."""
    api = engines['star_oam_api']
    aggregate = 'stock_loss_'+stage+'_review'
    kind = 'stock_loss.regionally_verified' if stage == 'regional' else 'stock_loss.headquarters_approved'
    tables = ('audit_events', 'outbox_events', 'state_transition_events', 'notification_events')
    for table in tables:
        before=state(); identifier=str(uuid4()); at=datetime.now(timezone.utc)
        body={'request_id':command.request_id, 'reviewer_person_id':str(actor.person_id)}
        with Session(api) as db:
            if table == 'audit_events':
                append_audit_event(db, stream_key='inventory', actor_user_id=actor.user_id,
                    action=kind, aggregate_type=aggregate, aggregate_id=identifier,
                    before_jsonb={}, after_jsonb=body, request_id='synthetic-fragment:'+identifier,
                    occurred_at=at, created_at=at)
            elif table == 'outbox_events':
                db.add(OutboxEvent(event_type=kind, aggregate_type=aggregate, aggregate_id=identifier,
                    payload_jsonb=body, idempotency_key=identifier, available_at=at, created_at=at, updated_at=at))
            elif table == 'state_transition_events':
                db.add(StateTransitionEvent(aggregate_type=aggregate, aggregate_id=identifier,
                    from_status='awaiting', to_status='approved', actor_id=actor.user_id,
                    reason=kind, idempotency_key=identifier, occurred_at=at, metadata_jsonb=body, created_at=at))
            else:
                db.add(NotificationEvent(event_type=kind, business_type=aggregate, business_id=identifier,
                    dedup_key=identifier, payload_jsonb=body, occurred_at=at, created_at=at))
            db.flush()
            with pytest.raises(DBAPIError, match='0156 sealed approval request') as caught:
                db.execute(text('SET CONSTRAINTS trg_'+table+'_review_seal_0156 IMMEDIATE'))
            assert caught.value.orig.sqlstate == '23514'
            db.rollback()
        assert state() == before, table
    return list(tables)
