"""Current detail hints use the same grants and independent-review rule as writes."""
from dataclasses import replace
from datetime import timedelta
from uuid import UUID,uuid4

import pytest
from sqlalchemy import event, text

from app.daily_reconciliation import query_service as service,review_core as core
from app.daily_reconciliation.review_models import DailyReviewBinding
from test_daily_reconciliation_queries import database,principal,NOW,REGION
from test_daily_reconciliation_review_core import snapshot,opened,accepted,explain,principal as review_principal


def writer(role='provincial_manager',*,person_id=None):
    p=principal(role,'national' if role=='admin' else 'organization','*' if role=='admin' else str(REGION))
    e=p.entitlements[0]
    return replace(p,user_id=str(uuid4()),person_id=person_id or p.person_id,
        entitlements=tuple(replace(e,action=action) for action in ('read','create_daily','explain_daily','approve_daily')))


def read_as(monkeypatch,p):
    monkeypatch.setattr(service,'authorize_context',lambda *args:((REGION,),p,NOW))


def detail(database):
    return service.detail(database,actor=None,session_id='test-session',cutoff_id=UUID(int=1))


def bind(database,*,status='pending_review',version=2,authors=(),items=None):
    items=items if items is not None else [core.ItemState(ordinal=n,version=1,explanation='真实差异证据已核验',explained_by_person_id=uuid4()).model_dump(mode='json') for n in range(1,4)]
    database.execute(DailyReviewBinding.__table__.insert().values(run_id=uuid4(),cutoff_id=UUID(int=1),version=version,last_event_id=uuid4(),
        item_ids=[],state_jsonb=dict(review_status=status,explanation_authors=[str(p) for p in authors],items=items),created_at=NOW,updated_at=NOW))
    database.commit()


@pytest.mark.parametrize('role,status,expected',[
    ('provincial_manager',None,['open']),('admin',None,['open']),
    ('provincial_manager','awaiting_explanations',['explain']),('admin','awaiting_explanations',['request_changes']),
    ('provincial_manager','pending_review',['explain']),('admin','pending_review',['approve','request_changes']),
    ('provincial_manager','changes_requested',['explain']),('admin','changes_requested',['request_changes']),
    ('provincial_manager','approved',[]),('admin','approved',[]),
])
def test_live_role_and_review_state(database,monkeypatch,role,status,expected):
    p=writer(role);read_as(monkeypatch,p)
    if status:bind(database,status=status)
    value=detail(database)
    assert value.allowed_actions==expected
    assert value.action_context.person_id==p.person_id and value.action_context.authorization_version==p.authorization_version
    assert 'explanation_authors' not in value.model_dump_json()


@pytest.mark.parametrize('kind',['read_only','action_deny','unrelated_scope_deny','field_only','expired_action','split_grants','ambiguous_grants','role_scope_mismatch'])
def test_readable_does_not_imply_action_authority(database,monkeypatch,kind):
    p=writer();g=p.assignments[0];action=next(e for e in p.entitlements if e.action=='create_daily')
    if kind=='read_only':p=replace(p,entitlements=tuple(e for e in p.entitlements if e.action=='read'))
    elif kind in {'action_deny','unrelated_scope_deny'}:
        denial=replace(action,effect='deny',scope_id=str(uuid4()) if kind=='unrelated_scope_deny' else action.scope_id)
        p=replace(p,entitlements=p.entitlements+(denial,))
    elif kind=='field_only':p=replace(p,entitlements=tuple(replace(e,field_code='quantity') if e.action=='create_daily' else e for e in p.entitlements))
    elif kind=='expired_action':p=replace(p,assignments=(replace(g,valid_to=NOW),))
    elif kind in {'split_grants','ambiguous_grants'}:
        second=replace(g,assignment_id=uuid4())
        extra=tuple(replace(e,assignment_id=second.assignment_id) for e in p.entitlements if kind=='ambiguous_grants' or e.action!='read')
        original=p.entitlements if kind=='ambiguous_grants' else tuple(e for e in p.entitlements if e.action=='read')
        p=replace(p,assignments=(g,second),entitlements=original+extra)
    else:p=replace(p,entitlements=tuple(replace(e,scope_id=str(uuid4())) if e.action=='create_daily' else e for e in p.entitlements))
    read_as(monkeypatch,p)
    assert detail(database).allowed_actions==[]


def test_mixed_roles_follow_operation_grant_not_user_label(database,monkeypatch):
    regional=writer();hq=writer('admin')
    p=replace(regional,assignments=regional.assignments+hq.assignments,entitlements=regional.entitlements+hq.entitlements)
    read_as(monkeypatch,p)
    assert detail(database).allowed_actions==['open']
    bind(database)
    assert detail(database).allowed_actions==['explain','approve','request_changes']


def test_old_author_cannot_review_after_all_current_explanations_replaced(database,monkeypatch,snapshot):
    original=review_principal();replacement=review_principal();history=opened(snapshot)
    request,evidence=explain(snapshot);history=accepted(snapshot,history,request,original,(evidence,))
    request,_=explain(snapshot,2,item_version=1,f=evidence);history=accepted(snapshot,history,request,replacement,(evidence,))
    state=core.prove_history(snapshot,history)
    assert all(i.explained_by_person_id!=original.person_id for i in state.items)
    bind(database,authors=state.explanation_authors,items=[i.model_dump(mode='json') for i in state.items])
    read_as(monkeypatch,writer('admin',person_id=original.person_id))
    assert detail(database).allowed_actions==[]
    read_as(monkeypatch,writer('admin'))
    assert detail(database).allowed_actions==['approve','request_changes']


@pytest.mark.parametrize('kind',['unexplained','already_returned','matched_only'])
def test_return_requires_current_explained_difference(database,monkeypatch,kind):
    read_as(monkeypatch,writer('admin'))
    items=[core.ItemState(ordinal=n,explanation='' if kind=='unexplained' else '已核验差异原因',revision_requested=kind=='already_returned').model_dump(mode='json') for n in range(1,4)]
    bind(database,status='awaiting_explanations',items=items)
    if kind=='matched_only':
        database.execute(text("UPDATE daily_reconciliation_cutoffs SET payload_jsonb=json_set(payload_jsonb,'$.comparison.comparison.items[0].status','matched','$.comparison.comparison.items[1].status','matched','$.comparison.comparison.items[2].status','matched')"));database.commit()
    assert detail(database).allowed_actions==[]


def test_matched_report_can_be_independently_approved_but_not_explained_or_returned(database,monkeypatch):
    database.execute(text("UPDATE daily_reconciliation_cutoffs SET payload_jsonb=json_set(payload_jsonb,'$.comparison.comparison.status','matched','$.comparison.comparison.items',json('[]'))"));database.commit()
    bind(database,items=[])
    read_as(monkeypatch,writer('admin'));assert detail(database).allowed_actions==['approve']
    read_as(monkeypatch,writer());assert detail(database).allowed_actions==[]


@pytest.mark.parametrize('change',['version','person','entitlements','scope'])
def test_mid_read_authority_change_blocks_detail(database,monkeypatch,change):
    p=writer();altered=p;scope=(REGION,)
    if change=='version':altered=replace(p,authorization_version=2)
    elif change=='person':altered=replace(p,person_id=uuid4())
    elif change=='entitlements':altered=replace(p,entitlements=tuple(e for e in p.entitlements if e.action=='read'))
    else:scope=None
    contexts=iter([((REGION,),p,NOW),(scope,altered,NOW)])
    monkeypatch.setattr(service,'authorize_context',lambda *args:next(contexts))
    with pytest.raises(service.DailyQueryError,match='authorization_changed'):detail(database)


def test_grant_expiry_between_checks_removes_action_without_reusing_old_stamp(database,monkeypatch):
    p=writer();g=replace(p.assignments[0],valid_to=NOW+timedelta(seconds=1));p=replace(p,assignments=(g,))
    contexts=iter([((REGION,),p,NOW),((REGION,),p,NOW+timedelta(seconds=1))])
    monkeypatch.setattr(service,'authorize_context',lambda *args:next(contexts))
    assert detail(database).allowed_actions==[]


@pytest.mark.parametrize('initial',['absent','existing'])
def test_review_version_is_rechecked_after_authorization(database,monkeypatch,initial):
    p=writer();read_as(monkeypatch,p)
    if initial=='existing':bind(database)
    original=service.ensure_review_version
    def race(db,cutoff_id,version):
        if version:db.execute(DailyReviewBinding.__table__.update().values(version=version+1))
        else:bind(db)
        original(db,cutoff_id,version)
    monkeypatch.setattr(service,'ensure_review_version',race)
    with pytest.raises(service.DailyQueryError,match='version_changed'):detail(database)


def test_detail_does_not_select_full_binding_or_event_payload(database,monkeypatch):
    read_as(monkeypatch,writer('admin'));bind(database)
    statements=[]
    def capture(conn,cursor,statement,parameters,context,executemany):statements.append(statement)
    event.listen(database.bind,'before_cursor_execute',capture)
    try:result=detail(database)
    finally:event.remove(database.bind,'before_cursor_execute',capture)
    assert result.allowed_actions==['approve','request_changes']
    assert not any('daily_review_events' in sql or 'SELECT daily_review_bindings.state_jsonb' in sql for sql in statements)
    assert 'is_explanation_author' not in result.model_dump() and 'has_returnable_item' not in result.model_dump()


@pytest.mark.parametrize('field',['explanation_authors','items'])
@pytest.mark.parametrize('value',['missing',None,{},'not-an-array',False,1])
def test_corrupt_projection_cannot_suggest_review(database,monkeypatch,field,value):
    read_as(monkeypatch,writer('admin'));bind(database)
    state=database.execute(service.select(DailyReviewBinding.state_jsonb)).scalar_one()
    if value=='missing':state.pop(field)
    else:state[field]=value
    database.execute(DailyReviewBinding.__table__.update().values(state_jsonb=state));database.commit()
    with pytest.raises(service.DailyQueryError,match='projection_invalid') as error:detail(database)
    assert error.value.status==503
