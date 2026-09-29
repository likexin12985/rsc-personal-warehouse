"""Exact regional/HQ recovery stays read-only after write permission removal."""
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import delete, text
from sqlalchemy.exc import OperationalError

from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent, NotificationEvent
from app.models import User
from app.stock_loss_schemas import StockLossReviewRequestLookupIn
from app.formal_services import stock_loss_review_recovery as recovery
from app.formal_services import stock_loss_regional_reviews as regional_service
from app.formal_services import stock_loss_headquarters_reviews as headquarters_service
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from test_stock_loss_headquarters_reviews import db, world, stock, allowed, evidence, regional, headquarters, state
from test_stock_loss_source_routes import client, private, PATH


@pytest.fixture(params=['regional', 'headquarters'])
def review_world(request, db, allowed):
    stage = request.param
    fixture = request.getfixturevalue(stage)
    service = regional_service if stage == 'regional' else headquarters_service
    entitlement = next(g for g in fixture.actor.entitlements if g.action == service.ACTION)
    actor = replace(fixture.actor, entitlements=fixture.actor.entitlements + (
        replace(entitlement, action='read'),))
    allowed.world.current_principal = actor
    lookup = StockLossReviewRequestLookupIn(operation_id=fixture.request.operation_id,
        operator_person_id=actor.person_id, request_id=fixture.request.request_id,
        idempotency_key=fixture.request.idempotency_key,
        request_hash=recovery.sources._hash(service.intent(fixture.request)),
        expected_submission_plan_hash=fixture.request.expected_submission_plan_hash)
    command = service.verify_regional_loss if stage == 'regional' else service.approve_headquarters_loss
    def commit_review():
        result = command(db, actor=actor, request=fixture.request)
        db.commit()
        return result
    return SimpleNamespace(stage=stage, actor=actor, request=lookup, service=service,
        commit=commit_review, path=PATH+'/'+stage+'-reviews/request-lookup')


def read(db, w, request=None):
    return recovery.lookup_review_request(db, actor=w.actor, request=request or w.request, stage=w.stage)


def test_absence_then_lost_response_recovery_is_read_only_without_write_permission(db, allowed, review_world):
    w = review_world
    before = state(db)
    db.execute(text('PRAGMA query_only=ON'))
    assert read(db,w).model_dump() == {'lookup_status':'not_found','retry_permitted':False}
    assert state(db) == before
    db.execute(text('PRAGMA query_only=OFF'))
    result = w.commit()
    allowed.world.current_principal = replace(w.actor, entitlements=tuple(g for g in w.actor.entitlements
        if g.action != w.service.ACTION))
    before = state(db)
    db.execute(text('PRAGMA query_only=ON'))
    found = read(db,w)
    assert found.lookup_status == 'found' and found.review == result and not found.retry_permitted
    assert found.review.stock_effect == 'none'
    if w.stage == 'headquarters':
        assert found.review.disposition_stage == 'pending'
    assert read(db,w) == found and state(db) == before
    assert not db.new and not db.dirty and not db.deleted
    assert w.request.idempotency_key not in found.model_dump_json()


@pytest.mark.parametrize('field', ['request_id','idempotency_key','request_hash','expected_submission_plan_hash'])
def test_changed_coordinates_never_recover_a_different_command(db, review_world, field):
    w=review_world; w.commit(); before=state(db)
    value = uuid4().hex if field in ('request_id','idempotency_key') else 'f'*64
    with pytest.raises(InventoryReadError) as caught:
        read(db,w,w.request.model_copy(update={field:value}))
    assert caught.value.code == 'stock_loss_review_request_conflict'
    assert state(db)==before


@pytest.mark.parametrize('fault', ['read','scope','role','borrowed','self','inactive','legacy_disabled','version','operator'])
def test_current_read_scope_identity_and_original_operator_are_required(db, allowed, review_world, fault):
    w=review_world; w.commit(); actor=w.actor; request=w.request
    if fault=='read':actor=replace(actor,entitlements=tuple(g for g in actor.entitlements if g.action!='read'))
    elif fault=='scope':actor=replace(actor,assignments=tuple(replace(g,scope_id=str(uuid4())) for g in actor.assignments))
    elif fault=='role':actor=replace(actor,assignments=())
    elif fault=='borrowed':actor=replace(actor,entitlements=tuple(replace(g,role_code='technician') if g.action=='read' else g for g in actor.entitlements))
    elif fault=='self':
        actor=replace(actor,user_id=allowed.actor.user_id,person_id=allowed.actor.person_id,
            authorization_version=allowed.actor.authorization_version)
        w.actor=actor
    elif fault=='inactive':actor=replace(actor,account_status='suspended')
    elif fault=='legacy_disabled':db.get(User,actor.user_id).is_active=False;db.commit()
    elif fault=='version':actor=replace(actor,authorization_version=actor.authorization_version+1)
    else:request=request.model_copy(update={'operator_person_id':uuid4()})
    allowed.world.current_principal=actor
    before=state(db)
    with pytest.raises((InventoryReadError,InventoryPostingError)):
        read(db,w,request)
    assert state(db)==before


def test_another_reviewer_cannot_recover_original_results(db, allowed, review_world):
    w=review_world; w.commit()
    # An actual different active account with otherwise equivalent current
    # synthetic read grants must still not receive the original actor's result.
    from test_formal_access import make_user
    from app.foundation_models import Organization
    from sqlalchemy import select
    user, person=make_user(db,db.scalar(select(Organization)),name='Synthetic other reviewer')
    db.commit()
    w.actor=replace(w.actor,user_id=user.id,person_id=person.id,authorization_version=user.authorization_version)
    allowed.world.current_principal=w.actor
    request=w.request.model_copy(update={'operator_person_id':person.id})
    before=state(db)
    with pytest.raises(InventoryReadError) as caught:read(db,w,request)
    assert caught.value.code=='stock_loss_review_not_found'
    assert state(db)==before


@pytest.mark.parametrize('fragment',['audit','outbox','state','notification'])
def test_orphan_evidence_is_not_a_clean_miss(db, review_world, fragment):
    w=review_world; identifier=uuid4(); at=datetime.now(timezone.utc)
    body={'request_id':w.request.request_id,'reviewer_person_id':str(w.actor.person_id)}
    if fragment=='audit':
        append_audit_event(db,stream_key='inventory',actor_user_id=w.actor.user_id,
            action=w.service.KIND,aggregate_type=w.service.AGGREGATE,aggregate_id=str(identifier),
            before_jsonb={},after_jsonb=body,request_id='synthetic-orphan:'+str(identifier),occurred_at=at,created_at=at)
    elif fragment=='outbox':
        db.add(OutboxEvent(event_type=w.service.KIND,aggregate_type=w.service.AGGREGATE,
            aggregate_id=str(identifier),payload_jsonb=body,idempotency_key=uuid4().hex,
            available_at=at,created_at=at,updated_at=at))
    elif fragment=='state':
        db.add(StateTransitionEvent(aggregate_type=w.service.AGGREGATE,aggregate_id=str(identifier),
            from_status=None,to_status='synthetic',actor_id=w.actor.user_id,reason='synthetic',
            idempotency_key=uuid4().hex,occurred_at=at,metadata_jsonb=body,created_at=at))
    else:
        db.add(NotificationEvent(event_type=w.service.KIND,business_type=w.service.AGGREGATE,
            business_id=str(identifier),dedup_key=uuid4().hex,payload_jsonb=body,occurred_at=at,created_at=at))
    db.commit(); before=state(db)
    with pytest.raises(InventoryReadError):read(db,w)
    assert state(db)==before


def test_missing_committed_outbox_cannot_be_reported_as_success(db, review_world):
    w=review_world; result=w.commit()
    db.execute(delete(OutboxEvent).where(OutboxEvent.aggregate_type==w.service.AGGREGATE,
        OutboxEvent.aggregate_id==str(result.review_id)))
    db.commit(); before=state(db)
    with pytest.raises(InventoryReadError):read(db,w)
    assert state(db)==before


@pytest.mark.parametrize('fault',['cursor','permission'])
def test_mixed_snapshot_or_revocation_during_read_discards_result(db, allowed, review_world, monkeypatch, fault):
    w=review_world; w.commit(); original=recovery._request_events
    def changed(*args,**kwargs):
        original(*args,**kwargs)
        if fault=='cursor':monkeypatch.setattr(recovery,'_cursor',lambda db:('changed',))
        else:allowed.world.current_principal=replace(w.actor,entitlements=())
    monkeypatch.setattr(recovery,'_request_events',changed)
    before=state(db)
    with pytest.raises(InventoryReadError) as caught:read(db,w)
    assert caught.value.code==('stock_loss_review_lookup_changed' if fault=='cursor' else 'stock_loss_review_read_forbidden')
    assert state(db)==before


def test_http_recovers_after_write_revocation_without_disclosing_internal_fields(db, allowed, review_world, client):
    w=review_world; result=w.commit()
    allowed.world.current_principal=replace(w.actor,entitlements=tuple(g for g in w.actor.entitlements if g.action!=w.service.ACTION))
    before=state(db);db.execute(text('PRAGMA query_only=ON'))
    response=client.post(w.path,json=w.request.model_dump(mode='json'))
    assert response.status_code==200,response.text
    assert response.json()['review']['review_id']==str(result.review_id)
    assert response.json()['retry_permitted'] is False
    assert w.request.idempotency_key not in response.text and 'idempotency_key_hash' not in response.text
    private(response);assert state(db)==before


@pytest.mark.parametrize('fault,status',[('database',503),('schema',422),('permission',403)])
def test_http_failures_preserve_private_boundary(db, allowed, review_world, client, monkeypatch, fault, status):
    w=review_world;body=w.request.model_dump(mode='json');before=state(db)
    if fault=='database':
        def fail(*args,**kwargs):raise OperationalError('PRIVATE-SQL',{},Exception('PRIVATE-ERROR'))
        monkeypatch.setattr(recovery,'lookup_review_request',fail)
    elif fault=='schema':body['request_hash']='invalid'
    else:allowed.world.current_principal=replace(w.actor,entitlements=())
    response=client.post(w.path,json=body)
    assert response.status_code==status,response.text
    assert 'PRIVATE-' not in response.text
    private(response);assert state(db)==before
