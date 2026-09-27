"""HQ composition over real original/regional facts; database guards separately."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.formal_access import load_formal_principal
from app.foundation_models import Organization, Permission, Role, RolePermission, NotificationEvent
from app.models import User
from app.stock_operation_models import StockOperationOrder, StockOperationLine, StockLossHeadquartersReview
from app.stock_loss_schemas import StockLossHeadquartersReviewIn, StockLossHeadquartersDecisionIn
from app.formal_services import stock_loss_headquarters_reviews as reviews, stock_loss_regional_reviews as regional_reviews
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from test_formal_access import make_user, assign
from test_stock_loss_regional_reviews import db, world, stock, allowed, evidence, regional, review_snapshot
from test_work_order_removed_registration import inventory


@pytest.fixture
def headquarters(db, allowed, regional):
    reviewed = regional_reviews.verify_regional_loss(db, actor=regional.actor, request=regional.request)
    db.commit()
    organization = db.scalar(select(Organization).where(Organization.org_type == 'headquarters'))
    user, person = make_user(db, organization, name='Synthetic loss HQ reviewer')
    role = db.scalar(select(Role).where(Role.code == 'admin'))
    assign(db, user, role, scope_type='national', scope_id='*')
    permission = Permission(resource='stock_operation', action=reviews.ACTION, field_code='', description='Synthetic HQ review')
    db.add(permission); db.flush()
    db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
    db.commit()
    actor = load_formal_principal(db, user.id); allowed.world.current_principal = actor
    line = db.scalar(select(StockOperationLine).where(StockOperationLine.operation_id == reviewed.operation_id))
    request = StockLossHeadquartersReviewIn(operation_id=reviewed.operation_id,
        expected_submission_plan_hash=reviewed.submission_plan_hash, regional_review_id=reviewed.review_id,
        expected_regional_review_hash=reviewed.request_hash, comment='终审依据原报损及区域核实，库存处置另行执行',
        request_id=uuid4().hex, idempotency_key=uuid4().hex,
        decisions=(dict(line_id=line.id, disposition='restore_available', reason='核实可恢复'),))
    return SimpleNamespace(actor=actor, request=request, regional=reviewed)


def state(db):
    return review_snapshot(db), tuple(tuple(db.execute(text('SELECT * FROM '+table+' ORDER BY id')))
        for table in ('stock_loss_headquarters_reviews', 'stock_loss_headquarters_decisions'))


@pytest.mark.parametrize('kind', ['restore_available', 'convert_used', 'convert_damaged', 'return_to_region', 'scrap'])
def test_each_approval_is_pending_disposition_without_stock_effect(db, headquarters, kind):
    value = headquarters.request.model_copy(update={'decisions': (headquarters.request.decisions[0].model_copy(update={'disposition':kind}),)})
    before = inventory(db)
    result = reviews.approve_headquarters_loss(db, actor=headquarters.actor, request=value); db.commit()
    assert result.approval_stage == 'approved' and result.disposition_stage == 'pending' and result.stock_effect == 'none'
    assert result.decisions[0].disposition == kind and inventory(db) == before
    assert db.get(StockOperationOrder, result.operation_id).status == 'submitted'
    notification = db.scalar(select(NotificationEvent).where(NotificationEvent.business_id == str(result.review_id)))
    assert notification.status == 'pending' and notification.event_type == reviews.KIND
    before = state(db)
    assert reviews.approve_headquarters_loss(db, actor=headquarters.actor, request=value) == result
    db.commit(); assert state(db) == before


@pytest.mark.parametrize('field', ['comment', 'request_id', 'idempotency_key', 'decision', 'reason'])
def test_approved_decision_cannot_be_rebound(db, headquarters, field):
    reviews.approve_headquarters_loss(db, actor=headquarters.actor, request=headquarters.request); db.commit()
    before = state(db)
    value = headquarters.request
    if field in ('decision','reason'):
        item = value.decisions[0].model_copy(update={'disposition':'scrap'} if field=='decision' else {'reason':'different reason'})
        value = value.model_copy(update={'decisions':(item,)})
    else:value = value.model_copy(update={field:uuid4().hex})
    with pytest.raises(InventoryReadError) as caught:
        reviews.approve_headquarters_loss(db, actor=headquarters.actor, request=value)
    assert caught.value.code == 'stock_loss_headquarters_review_conflict'
    db.rollback(); assert state(db) == before


@pytest.mark.parametrize('change', ['self', 'scope', 'role', 'deny', 'borrowed_allow', 'version', 'inactive', 'legacy_disabled', 'submission', 'regional', 'regional_hash', 'line'])
def test_authority_and_exact_prior_facts_required(db, allowed, headquarters, change):
    actor = headquarters.actor; value = headquarters.request
    if change=='self':actor=replace(actor,user_id=allowed.actor.user_id,person_id=allowed.actor.person_id,authorization_version=allowed.actor.authorization_version)
    elif change=='scope':actor=replace(actor,assignments=tuple(replace(g,scope_type='organization',scope_id=str(allowed.account.owner_org_id)) for g in actor.assignments))
    elif change=='role':actor=replace(actor,assignments=tuple(replace(g,role_code='provincial_manager') for g in actor.assignments))
    elif change=='deny':actor=replace(actor,entitlements=actor.entitlements+(replace(next(g for g in actor.entitlements if g.action==reviews.ACTION),effect='deny'),))
    elif change=='borrowed_allow':actor=replace(actor,entitlements=tuple(replace(g,role_code='provincial_manager') if g.action==reviews.ACTION else g for g in actor.entitlements))
    elif change=='version':allowed.world.current_principal=replace(actor,authorization_version=actor.authorization_version+1)
    elif change=='inactive':actor=replace(actor,account_status='suspended',access_mode='restricted_handover')
    elif change=='legacy_disabled':db.get(User,actor.user_id).is_active=False;db.commit()
    elif change=='submission':value=value.model_copy(update={'expected_submission_plan_hash':'f'*64})
    elif change=='regional':value=value.model_copy(update={'regional_review_id':uuid4()})
    elif change=='regional_hash':value=value.model_copy(update={'expected_regional_review_hash':'f'*64})
    else:value=value.model_copy(update={'decisions':(value.decisions[0].model_copy(update={'line_id':uuid4()}),)})
    if change!='version':allowed.world.current_principal=actor
    before=state(db)
    with pytest.raises((InventoryReadError,InventoryPostingError)) as caught:
        reviews.approve_headquarters_loss(db,actor=actor,request=value)
    expected={'self':'stock_loss_self_review_forbidden','version':'actor_principal_stale','inactive':'actor_inactive',
        'submission':'stock_loss_submission_changed','regional':'stock_loss_regional_review_changed',
        'regional_hash':'stock_loss_regional_review_changed','line':'stock_loss_headquarters_line_coverage'}
    assert caught.value.code==expected.get(change,'stock_loss_headquarters_forbidden')
    db.rollback();assert state(db)==before


def test_departed_applicant_and_regional_reviewer_do_not_block_hq(db, allowed, regional, headquarters):
    for identifier in (allowed.actor.user_id, regional.actor.user_id):
        user=db.get(User,identifier);user.account_status='suspended';user.is_active=False
    db.commit()
    result=reviews.approve_headquarters_loss(db,actor=headquarters.actor,request=headquarters.request);db.commit()
    assert result.approval_stage=='approved' and result.stock_effect=='none'


def test_failure_after_decisions_rolls_back_full_approval(db, headquarters, monkeypatch):
    before=state(db)
    def fail(*args,**kwargs):
        assert db.scalar(select(StockLossHeadquartersReview.id)) is not None
        raise RuntimeError('synthetic HQ notification failure')
    monkeypatch.setattr(reviews,'record_business_notification',fail)
    with pytest.raises(RuntimeError,match='synthetic HQ notification failure'):
        reviews.approve_headquarters_loss(db,actor=headquarters.actor,request=headquarters.request)
    db.rollback();assert state(db)==before


def test_decision_contract_refuses_duplicate_lines_and_client_stock_claims(headquarters):
    value=headquarters.request.model_dump()
    with pytest.raises(ValidationError):StockLossHeadquartersReviewIn.model_validate(value|{'decisions':value['decisions']*2})
    with pytest.raises(ValidationError):StockLossHeadquartersReviewIn.model_validate(value|{'stock_effect':'posted'})
    for reason in ('', ' ', ' leading', 'trailing ', 'bad\x00value'):
        with pytest.raises(ValidationError):StockLossHeadquartersDecisionIn(line_id=uuid4(),disposition='scrap',reason=reason)
