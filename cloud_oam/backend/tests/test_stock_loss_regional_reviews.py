"""Regional service composition over actual submission facts; PG16 is separate."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.formal_access import load_formal_principal
from app.foundation_models import Permission, RolePermission, Organization, NotificationEvent
from app.models import User
from app.stock_operation_models import StockOperationOrder, StockLossRegionalReview
from app.stock_loss_schemas import StockLossRegionalReviewIn
from app.formal_services import stock_loss_regional_reviews as reviews, stock_loss_facts as facts
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from test_formal_access import make_user, assign
from test_stock_loss_commands import db, world, stock, allowed, evidence, submission, commands, snapshot
from test_work_order_removed_registration import inventory


@pytest.fixture
def regional(db, allowed, evidence):
    result=commands.submit_loss(db,actor=allowed.actor,request=submission(db,allowed,evidence));db.commit()
    owner=allowed.account.owner_org_id
    reviewer,person=make_user(db,db.get(Organization,owner),name='Synthetic regional reviewer')
    assign(db,reviewer,allowed.world.regional_role,scope_type='organization',scope_id=str(owner))
    permission=Permission(resource='stock_operation',action=reviews.ACTION,field_code='',description='Synthetic review')
    db.add(permission);db.flush()
    db.add(RolePermission(role_id=allowed.world.regional_role.id,permission_id=permission.id,effect='allow'))
    db.commit()
    actor=load_formal_principal(db,reviewer.id);allowed.world.current_principal=actor
    request=StockLossRegionalReviewIn(operation_id=result.operation_id,expected_submission_plan_hash=result.plan_hash,
        comment='已核实原报损明细与照片，待总部审批',request_id=uuid4().hex,idempotency_key=uuid4().hex)
    return SimpleNamespace(actor=actor,request=request,submission=result)


def review_snapshot(db):
    return snapshot(db),tuple(db.execute(text('SELECT * FROM stock_loss_regional_reviews ORDER BY id')))


def test_independent_regional_fact_preserves_original_stock_and_idempotent_replay(db,allowed,regional):
    before=inventory(db)
    result=reviews.verify_regional_loss(db,actor=regional.actor,request=regional.request);db.commit()
    assert result.approval_stage=='awaiting_headquarters' and result.stock_effect=='none'
    assert inventory(db)==before
    order=db.get(StockOperationOrder,result.operation_id)
    assert order.status=='submitted' and facts.submission_evidence(db,order=order)==regional.submission
    event=db.scalar(select(NotificationEvent).where(NotificationEvent.business_id==str(result.review_id)))
    assert event.status=='pending' and event.event_type==reviews.KIND
    before=review_snapshot(db)
    assert reviews.verify_regional_loss(db,actor=regional.actor,request=regional.request)==result
    db.commit();assert review_snapshot(db)==before


@pytest.mark.parametrize('field',['comment','request_id','idempotency_key'])
def test_second_request_cannot_rewrite_original_review(db,regional,field):
    reviews.verify_regional_loss(db,actor=regional.actor,request=regional.request);db.commit()
    before=review_snapshot(db)
    value=regional.request.model_copy(update={field:uuid4().hex})
    with pytest.raises(InventoryReadError) as caught:
        reviews.verify_regional_loss(db,actor=regional.actor,request=value)
    assert caught.value.code=='stock_loss_regional_review_conflict'
    db.rollback();assert review_snapshot(db)==before


@pytest.mark.parametrize('change',['self','other_region','deny','role','version','inactive','legacy_disabled','submission'])
def test_current_region_authority_and_exact_submission_are_required(db,allowed,regional,change):
    actor=regional.actor;request=regional.request
    if change=='self':
        actor=replace(actor,user_id=allowed.actor.user_id,person_id=allowed.actor.person_id,
            authorization_version=allowed.actor.authorization_version)
    elif change=='other_region':
        actor=replace(actor,assignments=tuple(replace(g,scope_id=str(uuid4())) for g in actor.assignments))
    elif change=='deny':
        actor=replace(actor,entitlements=actor.entitlements+(replace(actor.entitlements[-1],effect='deny'),))
    elif change=='role':
        actor=replace(actor,assignments=tuple(replace(g,role_code='admin') for g in actor.assignments))
    elif change=='version':
        allowed.world.current_principal=replace(actor,authorization_version=actor.authorization_version+1)
    elif change=='inactive':
        actor=replace(actor,account_status='suspended',access_mode='restricted_handover')
    elif change=='legacy_disabled':
        db.get(User,actor.user_id).is_active=False;db.commit()
    else:request=request.model_copy(update={'expected_submission_plan_hash':'f'*64})
    if change!='version':allowed.world.current_principal=actor
    before=review_snapshot(db)
    with pytest.raises((InventoryReadError,InventoryPostingError)) as caught:
        reviews.verify_regional_loss(db,actor=actor,request=request)
    expected={'self':'stock_loss_self_review_forbidden','other_region':'stock_loss_regional_forbidden',
        'deny':'stock_loss_regional_forbidden','role':'stock_loss_regional_forbidden',
        'version':'actor_principal_stale','inactive':'actor_inactive','legacy_disabled':'stock_loss_regional_forbidden',
        'submission':'stock_loss_submission_changed'}
    assert caught.value.code==expected[change]
    db.rollback();assert review_snapshot(db)==before


def test_suspended_requester_does_not_prevent_current_regional_review(db,allowed,regional):
    allowed.world.user.account_status='suspended';db.commit()
    result=reviews.verify_regional_loss(db,actor=regional.actor,request=regional.request);db.commit()
    assert result.approval_stage=='awaiting_headquarters'
    assert facts.submission_evidence(db,order=db.get(StockOperationOrder,result.operation_id))==regional.submission


def test_partial_review_failure_rolls_back_fact_audit_and_notifications(db,regional,monkeypatch):
    before=review_snapshot(db)
    def fail(*args,**kwargs):
        assert db.scalar(select(StockLossRegionalReview.id)) is not None
        raise RuntimeError('synthetic regional notification failure')
    monkeypatch.setattr(reviews,'record_business_notification',fail)
    with pytest.raises(RuntimeError,match='synthetic regional notification failure'):
        reviews.verify_regional_loss(db,actor=regional.actor,request=regional.request)
    db.rollback();assert review_snapshot(db)==before
