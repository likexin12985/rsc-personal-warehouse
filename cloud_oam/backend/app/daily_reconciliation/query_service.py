"""Scope every query in SQL; never load a raw daily archive into the API."""
from datetime import date, datetime, timezone
import json
from uuid import UUID
from sqlalchemy import BigInteger, Boolean, Column, Date, DateTime, JSON, MetaData, String, Table, Uuid, func, select, text, bindparam, literal_column
from sqlalchemy.orm import Session
from ..formal_access import FormalAccessError, FormalPrincipal, load_formal_principal
from ..models import AuthSession, User
from ..foundation_models import AuthIdentity
from .review_models import DailyReviewBinding,DailyReviewEvent
from .review_core import ItemState, REQUEST
from . import review_core as core
from .query_schemas import ReviewHistoryItem,ReviewHistoryPage
from .query_schemas import DailySummary, DailyDetail, DailyPage, ComparisonItem, ExcludedQuantity, ComparisonPage, ExcludedPage

# Separate metadata: create_all must never turn a read projection into a table.
REPORTS=Table('daily_reconciliation_reports',MetaData(),
    *(Column(n,Uuid) for n in ('cutoff_id','source_system_id','region_org_id','source_publication_id','mapping_decision_id')),
    Column('business_date',Date),Column('local_ledger_cursor',BigInteger),
    *(Column(n,DateTime(timezone=True)) for n in ('source_captured_at','local_captured_at','created_at')),
    *(Column(n,String) for n in ('cutoff_sha256','comparison_status','comparison_sha256')),
    *(Column(n,JSON) for n in ('covered_warehouses','included_buckets','comparison_items','excluded_quantities')))
SUMMARY_NAMES=tuple(n for n in REPORTS.c.keys() if n not in {'covered_warehouses','included_buckets','comparison_items','excluded_quantities'})

class DailyQueryError(RuntimeError):
    def __init__(self,code,status=403):
        self.code,self.status=code,status
        super().__init__(code)

def utc(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)

def read_scope(actor,now):
    if not isinstance(actor,FormalPrincipal) or actor.access_mode!='active' or actor.account_status!='active' or actor.employment_status!='active':
        raise DailyQueryError('daily_query_forbidden')
    permissions=[p for p in actor.entitlements if p.resource=='reconciliation' and p.action=='read']
    if any(p.effect=='deny' for p in permissions):raise DailyQueryError('daily_query_forbidden')
    grants={g.assignment_id:g for g in actor.assignments if utc(g.valid_from)<=now and (g.valid_to is None or now<utc(g.valid_to))}
    regions=set();national=False
    for p in permissions:
        g=grants.get(p.assignment_id)
        if g is None or p.effect!='allow' or p.field_code or (p.role_code,p.scope_type,p.scope_id)!=(g.role_code,g.scope_type,g.scope_id):continue
        if (g.role_code,g.scope_type,g.scope_id)==('admin','national','*'):national=True
        if g.role_code=='provincial_manager' and g.scope_type=='organization':
            try:region=UUID(g.scope_id)
            except (ValueError,TypeError,AttributeError):continue
            if region.int:regions.add(region)
    if national:return None
    if not regions:raise DailyQueryError('daily_query_forbidden')
    return tuple(sorted(regions,key=str))

def authorize(db,actor,session_id):
    return authorize_context(db,actor,session_id)[0]

def authorize_context(db,actor,session_id):
    if not isinstance(actor,FormalPrincipal) or db.new or db.dirty or db.deleted:
        raise DailyQueryError('daily_query_forbidden')
    if db.bind.dialect.name=='postgresql' and db.scalar(text('SHOW transaction_isolation'))!='read committed':
        raise DailyQueryError('daily_query_current_snapshot_required',503)
    now=datetime.now(timezone.utc)
    try:current=load_formal_principal(db,actor.user_id,now=now)
    except FormalAccessError:raise DailyQueryError('daily_query_forbidden') from None
    if (current.person_id,current.authorization_version)!=(actor.person_id,actor.authorization_version):
        raise DailyQueryError('daily_query_authorization_changed')
    user=db.scalar(select(User).where(User.id==current.user_id).execution_options(populate_existing=True))
    identity=db.scalar(select(AuthIdentity.id).where(AuthIdentity.user_id==current.user_id,AuthIdentity.status=='active',AuthIdentity.revoked_at.is_(None),AuthIdentity.verified_at<=now).limit(1))
    if user is None or not user.is_active or identity is None:raise DailyQueryError('daily_query_forbidden')
    session=db.scalar(select(AuthSession).where(AuthSession.id==session_id).execution_options(populate_existing=True))
    if session is None or session.user_id!=current.user_id or session.revoked_at is not None or utc(session.expires_at)<=now or utc(session.created_at)>now or not session.device_id.strip():
        raise DailyQueryError('daily_query_session_expired',401)
    return read_scope(current,now),current,now

def statement(scope,*columns):
    query=select(*columns).select_from(REPORTS.outerjoin(DailyReviewBinding,DailyReviewBinding.cutoff_id==REPORTS.c.cutoff_id))
    return query if scope is None else query.where(REPORTS.c.region_org_id.in_(scope))

def finish(db,actor,session_id,scope,result):
    if authorize(db,actor,session_id)!=scope:raise DailyQueryError('daily_query_authorization_changed')
    return result

def summaries(db):
    length=func.jsonb_array_length if db.bind.dialect.name=='postgresql' else func.json_array_length
    return [REPORTS.c[n] for n in SUMMARY_NAMES]+[
        length(REPORTS.c.comparison_items).label('item_count'),
        length(REPORTS.c.excluded_quantities).label('excluded_quantity_count'),
        func.coalesce(DailyReviewBinding.state_jsonb['review_status'].as_string(),'not_recorded').label('review_status'),
        func.coalesce(DailyReviewBinding.version,0).label('review_version'),
        DailyReviewBinding.updated_at.label('review_updated_at')]

def list_reports(db:Session,*,actor,session_id,limit=20,after_id=None,business_date=None,region_org_id=None):
    if type(limit) is not int or not 1<=limit<=20:raise DailyQueryError('daily_query_limit_invalid',422)
    scope=authorize(db,actor,session_id)
    query=statement(scope,*summaries(db))
    if region_org_id is not None:
        if scope is not None and region_org_id not in scope:raise DailyQueryError('daily_query_forbidden')
        query=query.where(REPORTS.c.region_org_id==region_org_id)
    if business_date is not None:query=query.where(REPORTS.c.business_date==business_date)
    if after_id is not None:query=query.where(REPORTS.c.cutoff_id>after_id)
    rows=db.execute(query.order_by(REPORTS.c.cutoff_id).limit(limit+1)).mappings().all()
    result=DailyPage(items=[DailySummary(**r) for r in rows[:limit]],next_after_id=rows[limit-1]['cutoff_id'] if len(rows)>limit else None)
    return finish(db,actor,session_id,scope,result)

def action_columns(db):
    """Return only scalar facts; never load a binding or private author list."""
    if db.bind.dialect.name=='postgresql':
        author="COALESCE(daily_review_bindings.state_jsonb->'explanation_authors' ? :action_person_id,FALSE)"
        valid="""daily_review_bindings.version IS NULL OR COALESCE(
            jsonb_typeof(daily_review_bindings.state_jsonb->'explanation_authors')='array'
            AND jsonb_typeof(daily_review_bindings.state_jsonb->'items')='array',FALSE)"""
        returnable="""EXISTS (SELECT 1 FROM jsonb_array_elements(daily_review_bindings.state_jsonb->'items') WITH ORDINALITY AS i(value,ordinal)
            WHERE daily_reconciliation_reports.comparison_items->CAST(i.ordinal-1 AS integer)->>'status'='difference'
            AND length(i.value->>'explanation')>0 AND i.value->>'revision_requested'='false')"""
    else:
        author="EXISTS (SELECT 1 FROM json_each(daily_review_bindings.state_jsonb,'$.explanation_authors') a WHERE a.value=:action_person_id)"
        valid="""daily_review_bindings.version IS NULL OR COALESCE(
            json_type(daily_review_bindings.state_jsonb,'$.explanation_authors')='array'
            AND json_type(daily_review_bindings.state_jsonb,'$.items')='array',0)"""
        returnable="""EXISTS (SELECT 1 FROM json_each(daily_review_bindings.state_jsonb,'$.items') i
            WHERE json_extract(daily_reconciliation_reports.comparison_items,'$['||i.key||'].status')='difference'
            AND length(json_extract(i.value,'$.explanation'))>0 AND json_extract(i.value,'$.revision_requested')=0)"""
    membership=text('SELECT '+author).bindparams(bindparam('action_person_id',type_=String)).columns(value=Boolean).scalar_subquery()
    returnable='CASE WHEN ('+valid+') THEN ('+returnable+') ELSE FALSE END'
    return [membership.label('is_explanation_author'),literal_column(returnable,type_=Boolean).label('has_returnable_item'),
        literal_column(valid,type_=Boolean).label('action_projection_valid')]

def allowed_actions(row,principal,stamp):
    """Advisory current-state actions, never permission to skip command checks."""
    candidates=[]
    if row['review_version']==0 and row['review_status']=='not_recorded':candidates.append('open')
    if row['review_version']>0 and row['review_status'] not in {'not_recorded','approved'}:
        if row['comparison_status']=='differences':candidates.append('explain')
        if not row['is_explanation_author']:
            if row['review_status']=='pending_review':candidates.append('approve')
            if row['has_returnable_item']:candidates.append('request_changes')
    result=[]
    for operation in candidates:
        try:core.authorize_region(principal,row['region_org_id'],operation,stamp)
        except core.ReviewError:continue
        result.append(operation)
    return result

def detail(db:Session,*,actor,session_id,cutoff_id):
    scope,current,_=authorize_context(db,actor,session_id)
    row=db.execute(statement(scope,*summaries(db),REPORTS.c.covered_warehouses,REPORTS.c.included_buckets,
        func.coalesce(DailyReviewBinding.state_jsonb['approval_comment'].as_string(),'').label('approval_comment'),
        DailyReviewBinding.state_jsonb['approved_by_person_id'].as_string().label('approved_by_person_id'),
        *action_columns(db)).where(REPORTS.c.cutoff_id==cutoff_id),{'action_person_id':str(current.person_id)}).mappings().one_or_none()
    if row is None:raise DailyQueryError('daily_query_not_found',404)
    if row['action_projection_valid'] is not True or any(type(row[k]) is not bool for k in ('is_explanation_author','has_returnable_item')):
        raise DailyQueryError('daily_review_projection_invalid',503)
    final_scope,final_actor,stamp=authorize_context(db,actor,session_id)
    if final_scope!=scope or final_actor!=current:raise DailyQueryError('daily_query_authorization_changed')
    ensure_review_version(db,cutoff_id,row['review_version'])
    values={k:v for k,v in row.items() if k not in {'is_explanation_author','has_returnable_item','action_projection_valid'}}
    return DailyDetail(**values,allowed_actions=allowed_actions(row,final_actor,stamp),
        action_context={'person_id':final_actor.person_id,'authorization_version':final_actor.authorization_version})

def item_page(db:Session,*,actor,session_id,cutoff_id,limit=100,after_ordinal=0,excluded=False,expected_review_version=None):
    if type(limit) is not int or not 1<=limit<=100 or type(after_ordinal) is not int or not 0<=after_ordinal<=2147483647:
        raise DailyQueryError('daily_query_page_invalid',422)
    scope=authorize(db,actor,session_id)
    # Check the exact object within the caller's regions before expanding JSON.
    row=db.execute(statement(scope,REPORTS.c.comparison_sha256,func.coalesce(DailyReviewBinding.version,0).label('review_version')).where(REPORTS.c.cutoff_id==cutoff_id)).one_or_none()
    if row is None:raise DailyQueryError('daily_query_not_found',404)
    if expected_review_version is not None and expected_review_version!=row[1]:raise DailyQueryError('daily_review_version_changed',409)
    column='excluded_quantities' if excluded else 'comparison_items'
    if db.bind.dialect.name=='postgresql':
        sql=f'SELECT n.ordinal,r.{column}->CAST(n.ordinal-1 AS integer) AS item FROM daily_reconciliation_reports r CROSS JOIN LATERAL generate_series(CAST(:after AS bigint)+1,LEAST(jsonb_array_length(r.{column}),CAST(:after AS bigint)+:limit)) n(ordinal) WHERE r.cutoff_id=:cutoff_id ORDER BY n.ordinal'
    else:
        sql=f'SELECT CAST(e.key AS integer)+1 AS ordinal,e.value AS item FROM daily_reconciliation_reports r,json_each(r.{column}) e WHERE r.cutoff_id=:cutoff_id AND CAST(e.key AS integer)+1>:after ORDER BY CAST(e.key AS integer) LIMIT :limit'
    values=db.execute(text(sql).bindparams(bindparam('cutoff_id',type_=Uuid)),{'cutoff_id':cutoff_id,'after':after_ordinal,'limit':limit+1}).mappings().all()
    model=ExcludedQuantity if excluded else ComparisonItem
    # Fetch at most one page of mutable explanations; never serialize the full
    # binding state or private actor/session history in a response.
    reviews={}
    if not excluded and values:
        ordinals=[v['ordinal'] for v in values[:limit]]
        if db.bind.dialect.name=='postgresql':
            q="SELECT n.ordinal,b.state_jsonb->'items'->CAST(n.ordinal-1 AS integer) AS item FROM daily_review_bindings b CROSS JOIN LATERAL generate_series(CAST(:first AS bigint),CAST(:last AS bigint)) n(ordinal) WHERE b.cutoff_id=:id AND b.version=:version"
        else:
            q="SELECT CAST(e.key AS integer)+1 AS ordinal,e.value AS item FROM daily_review_bindings b,json_each(b.state_jsonb,'$.items') e WHERE b.cutoff_id=:id AND b.version=:version AND CAST(e.key AS integer)+1 BETWEEN :first AND :last"
        if ordinals:
            observed=db.execute(text(q).bindparams(bindparam('id',type_=Uuid)),dict(id=cutoff_id,version=row[1],first=ordinals[0],last=ordinals[-1])).mappings().all()
            reviews={r['ordinal']:ItemState.model_validate(json.loads(r['item']) if isinstance(r['item'],str) else r['item']) for r in observed}
            if any(n!=r.ordinal for n,r in reviews.items()):raise DailyQueryError('daily_review_projection_invalid',503)
            if row[1]>0 and set(reviews)!=set(ordinals):raise DailyQueryError('daily_review_version_changed',409)
    items=[model(ordinal=r['ordinal'],**(json.loads(r['item']) if isinstance(r['item'],str) else r['item']),
        **({} if excluded else {'review':reviews.get(r['ordinal'],ItemState(ordinal=r['ordinal']))})) for r in values[:limit]]
    ensure_review_version(db,cutoff_id,row[1])
    page=ExcludedPage if excluded else ComparisonPage
    result=page(cutoff_id=cutoff_id,comparison_sha256=row[0],items=items,next_after_ordinal=items[-1].ordinal if len(values)>limit else None,**({} if excluded else {'review_version':row[1]}))
    return finish(db,actor,session_id,scope,result)


def ensure_review_version(db,cutoff_id,version):
    current=db.scalar(select(DailyReviewBinding.version).where(DailyReviewBinding.cutoff_id==cutoff_id)) or 0
    if current!=version:raise DailyQueryError('daily_review_version_changed',409)

def history_page(db,*,actor,session_id,cutoff_id,limit=50,after_version=0,expected_review_version=None):
    if type(limit) is not int or not 1<=limit<=100 or type(after_version) is not int or not 0<=after_version<=9223372036854775807:
        raise DailyQueryError('daily_query_page_invalid',422)
    scope=authorize(db,actor,session_id)
    row=db.execute(statement(scope,func.coalesce(DailyReviewBinding.version,0)).where(REPORTS.c.cutoff_id==cutoff_id)).one_or_none()
    if row is None:raise DailyQueryError('daily_query_not_found',404)
    version=row[0]
    if expected_review_version is not None and expected_review_version!=version:raise DailyQueryError('daily_review_version_changed',409)
    events=db.execute(select(DailyReviewEvent.version,DailyReviewEvent.id,DailyReviewEvent.payload_sha256,
        DailyReviewEvent.actor_person_id,DailyReviewEvent.created_at,DailyReviewEvent.payload_jsonb['request'].label('request')).where(
        DailyReviewEvent.cutoff_id==cutoff_id,DailyReviewEvent.version>after_version,DailyReviewEvent.version<=version)
        .order_by(DailyReviewEvent.version).limit(limit+1)).all()
    items=[]
    for event in events[:limit]:
        command=REQUEST.validate_python(event.request)
        items.append(ReviewHistoryItem(version=event.version,event_id=event.id,event_sha256=event.payload_sha256,
            actor_person_id=event.actor_person_id,occurred_at=event.created_at,operation=command.operation,
            explanations=command.items if command.operation=='explain' else (),comment=getattr(command,'comment',''),
            returned_ordinals=getattr(command,'ordinals',())))
    ensure_review_version(db,cutoff_id,version)
    result=ReviewHistoryPage(cutoff_id=cutoff_id,review_version=version,items=items,next_after_version=items[-1].version if len(events)>limit else None)
    return finish(db,actor,session_id,scope,result)
