from dataclasses import replace
from datetime import datetime,timedelta,timezone
from io import StringIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import UUID,uuid4
import json,runpy
import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import create_engine,text
from sqlalchemy.orm import Session
from fastapi import FastAPI
from fastapi.testclient import TestClient
from app.daily_reconciliation import query_service as service,query_security as security
from app.formal_access import FormalPrincipal,ScopeGrant,Entitlement
from app.routers import formal_daily_reconciliation as routes
from app.database import get_db
from app.dependencies import get_formal_principal

NOW=datetime.now(timezone.utc)
REGION=uuid4();OTHER=uuid4()
MIGRATION=Path(__file__).parents[1]/'alembic/versions/20261110_0131_daily_reconciliation_queries.py'

def principal(role='provincial_manager',scope='organization',scope_id=str(REGION)):
    grant=ScopeGrant(uuid4(),role,scope,scope_id,NOW-timedelta(days=1),None)
    permit=Entitlement(grant.assignment_id,role,scope,scope_id,'reconciliation','read','','allow')
    return FormalPrincipal('reader',uuid4(),'active','active',1,'active',(grant,),(permit,))

def test_exact_scope_and_headquarters():
    assert service.read_scope(principal(),NOW)==(REGION,)
    assert service.read_scope(principal('admin','national','*'),NOW) is None

@pytest.mark.parametrize('change',['deny','field','expired','future','mismatch','technician','external','handover','empty','zero','malformed'])
def test_no_permission_fallback(change):
    p=principal();g=p.assignments[0];e=p.entitlements[0]
    if change=='deny':p=replace(p,entitlements=(e,replace(e,effect='deny')))
    elif change=='field':p=replace(p,entitlements=(replace(e,field_code='quantity'),))
    elif change=='expired':p=replace(p,assignments=(replace(g,valid_to=NOW),))
    elif change=='future':p=replace(p,assignments=(replace(g,valid_from=NOW+timedelta(seconds=1)),))
    elif change=='mismatch':p=replace(p,entitlements=(replace(e,scope_id=str(OTHER)),))
    elif change in ('technician','external'):p=principal(change,'national','*')
    elif change=='handover':p=replace(p,access_mode='restricted_handover')
    elif change=='empty':p=replace(p,assignments=())
    else:p=principal(scope_id='0'*32 if change=='zero' else 'invalid')
    with pytest.raises(service.DailyQueryError):service.read_scope(p,NOW)

@pytest.fixture
def database(monkeypatch):
    engine=create_engine('sqlite://')
    migration=runpy.run_path(str(MIGRATION))
    with engine.begin() as c:
        c.execute(text('CREATE TABLE daily_reconciliation_cutoffs (id CHAR(32),business_date DATE,source_system_id CHAR(32),region_org_id CHAR(32),source_publication_id CHAR(32),mapping_decision_id CHAR(32),local_ledger_cursor BIGINT,source_captured_at DATETIME,local_captured_at DATETIME,created_at DATETIME,payload_sha256 TEXT,payload_jsonb JSON)'))
        with Operations.context(MigrationContext.configure(c)):migration['upgrade']()
        from app.daily_reconciliation.review_models import DailyReviewBinding,DailyReviewEvent
        DailyReviewBinding.__table__.create(c)
        DailyReviewEvent.__table__.create(c)
        for i in range(1,5):
            payload={'actor_snapshot':{'private':'SECRET'},'ledger':{'nationwide':'SECRET'},'mapping':{'warehouses':['WA'],'included_buckets':['available']},
                'comparison':{'status':'differences','report_sha256':'b'*64,'items':[dict(warehouse_code='WA',material_id=str(uuid4()),condition='new',external_qty='2.000',local_qty='1.000',difference='1.000',status='difference') for _ in range(3)],'excluded_local_quantities':[dict(warehouse_code='WA',material_id=str(uuid4()),condition='new',bucket='frozen',quantity='1.000')]}}
            payload['comparison']={'comparison':payload['comparison']}
            c.execute(text('INSERT INTO daily_reconciliation_cutoffs VALUES (:id,:day,:source,:region,:publication,:mapping,7,:at,:at,:at,:hash,:payload)'),dict(id=UUID(int=i).hex,day=NOW.date().isoformat(),source=uuid4().hex,region=(REGION if i%2 else OTHER).hex,publication=uuid4().hex,mapping=uuid4().hex,at=NOW.isoformat(),hash='a'*64,payload=json.dumps(payload)))
    monkeypatch.setattr(service,'authorize',lambda *args:(REGION,))
    actor=replace(principal(),user_id=str(uuid4()))
    monkeypatch.setattr(service,'authorize_context',lambda *args:((REGION,),actor,NOW))
    with Session(engine) as db:yield db
    engine.dispose()

def test_scope_precedes_pagination_and_json_is_bounded(database):
    args=dict(actor=None,session_id='x')
    page=service.list_reports(database,**args,limit=1)
    assert [x.cutoff_id.int for x in page.items]==[1] and page.next_after_id.int==1
    last=service.list_reports(database,**args,limit=1,after_id=page.next_after_id)
    assert [x.cutoff_id.int for x in last.items]==[3] and last.next_after_id is None
    item=service.detail(database,**args,cutoff_id=UUID(int=1))
    assert item.item_count==3 and item.excluded_quantity_count==1 and item.review_status=='not_recorded' and item.review_version==0 and item.review_updated_at is None
    assert 'SECRET' not in item.model_dump_json() and 'comparison_items' not in item.model_dump()
    one=service.item_page(database,**args,cutoff_id=UUID(int=1),limit=2)
    two=service.item_page(database,**args,cutoff_id=UUID(int=1),limit=2,after_ordinal=one.next_after_ordinal)
    assert [x.ordinal for x in one.items+two.items]==[1,2,3] and two.next_after_ordinal is None
    assert len(service.item_page(database,**args,cutoff_id=UUID(int=1),excluded=True).items)==1
    for after in (999,100001,2147483647):
        assert not service.item_page(database,**args,cutoff_id=UUID(int=1),after_ordinal=after).items

@pytest.mark.parametrize('operation',['detail','items','excluded','filter'])
def test_cross_region_and_unknown_objects_do_not_disclose(database,operation):
    args=dict(actor=None,session_id='x')
    with pytest.raises(service.DailyQueryError) as e:
        if operation=='filter':service.list_reports(database,**args,region_org_id=OTHER)
        elif operation=='detail':service.detail(database,**args,cutoff_id=UUID(int=2))
        else:service.item_page(database,**args,cutoff_id=UUID(int=2),excluded=operation=='excluded')
    assert e.value.status==(403 if operation=='filter' else 404)

def test_revocation_during_read_blocks_return(database,monkeypatch):
    scopes=iter([(REGION,),(OTHER,)])
    monkeypatch.setattr(service,'authorize',lambda *args:next(scopes))
    with pytest.raises(service.DailyQueryError,match='authorization_changed'):
        service.list_reports(database,actor=None,session_id='x')

@pytest.mark.parametrize('direction',['upgrade','downgrade'])
def test_frozen_migration_and_runtime_view_contract(direction):
    from pglast import parser
    from app import oam_sync_scope_security as scope
    m=runpy.run_path(str(MIGRATION));out=StringIO()
    assert m['COLUMNS']==security.COLUMNS and str(m['CATALOG_SQL'])==str(security.CATALOG_SQL) and m['VIEW_SHA256']==security.VIEW_SHA256
    from app.daily_reconciliation.cutoff_models import DailyCutoff
    from sqlalchemy.schema import CreateIndex
    index=next(i for i in DailyCutoff.__table__.indexes if i.name=='ix_daily_cutoff_region_id')
    assert str(CreateIndex(index))==m['INDEX_SQL']
    assert m['NEW_HASH']==scope.OAM_SYNC_FUNCTION_MANIFEST_THROUGH_0131['rsc_oam_runtime_binding_ready_0044()'][-1]
    with Operations.context(MigrationContext.configure(dialect_name='postgresql',opts={'as_sql':True,'output_buffer':out})):m[direction]()
    parser.parse_sql(out.getvalue())
    assert 'DROP TABLE' not in out.getvalue() and 'DELETE FROM' not in out.getvalue()
    assert 'daily_reconciliation_cutoffs TO star_oam_api' not in out.getvalue()

@pytest.mark.parametrize('change',['missing','shape_safe','acl_safe','rules_safe','columns'])
def test_catalog_drift_is_refused(change):
    row=dict(shape_safe=True,acl_safe=True,rules_safe=True,columns=security.COLUMNS)
    if change=='columns':row['columns']=[]
    elif change!='missing':row[change]=False
    db=Mock();db.execute.return_value.mappings.return_value.all.return_value=[] if change=='missing' else [row]
    with pytest.raises(security.DailyQuerySecurityError):security.validate_query_catalog(db)

def test_api_only_read_and_strict_parameters(monkeypatch):
    app=FastAPI();app.include_router(routes.router,prefix='/api')
    db=Mock();actor=Mock();actor.allows.return_value=True
    app.dependency_overrides[get_db]=lambda:db
    app.dependency_overrides[get_formal_principal]=lambda:actor
    fn=Mock(return_value=dict(items=[],next_after_id=None));monkeypatch.setattr(service,'list_reports',fn)
    with TestClient(app) as c:
        url='/api/v1/reconciliations/daily'
        r=c.get(url);assert r.status_code==200 and r.headers['cache-control']=='no-store'
        for suffix in ['?limit=0','?limit=21','?limit=1&limit=2','?secret=1']:
            assert c.get(url+suffix).status_code==422
        assert fn.call_count==1
        fn.side_effect=service.DailyQueryError('daily_query_forbidden')
        r=c.get(url);assert r.status_code==403 and r.headers['cache-control']=='no-store'
        assert c.post(url,json={}).status_code==405
        actor.allows.return_value=False
        assert c.get(url).status_code==403
    assert not db.commit.called


def test_repeatable_read_cannot_hide_midquery_revocation():
    db=Mock();db.new=db.dirty=db.deleted=set();db.bind.dialect.name='postgresql'
    db.scalar.return_value='repeatable read'
    with pytest.raises(service.DailyQueryError,match='current_snapshot_required'):
        service.authorize(db,principal(),'session')


def test_mutable_review_state_and_pagination_bind_version(database,monkeypatch):
    from app.daily_reconciliation.review_models import DailyReviewBinding
    from app.daily_reconciliation.review_core import ItemState
    state=dict(review_status='changes_requested',approval_comment='',approved_by_person_id=None,explanation_authors=[],
        items=[ItemState(ordinal=n,version=2,explanation='逐项差异原因与补证',revision_requested=True,review_comment='请补充截止时间凭证').model_dump(mode='json') for n in range(1,4)])
    database.execute(DailyReviewBinding.__table__.insert().values(run_id=uuid4(),cutoff_id=UUID(int=1),version=4,last_event_id=uuid4(),
        item_ids=[],state_jsonb=state,created_at=NOW,updated_at=NOW));database.commit()
    args=dict(actor=None,session_id='x',cutoff_id=UUID(int=1))
    detail=service.detail(database,**args)
    assert detail.review_status=='changes_requested' and detail.review_version==4 and detail.review_updated_at is not None
    page=service.item_page(database,**args,limit=1,expected_review_version=4)
    assert page.review_version==4 and page.items[0].review.revision_requested and page.next_after_ordinal==1
    for f in (service.item_page,service.history_page):
        with pytest.raises(service.DailyQueryError,match='version_changed'):f(database,**args,expected_review_version=3)
    original=service.ensure_review_version
    def race(db,cutoff_id,version):
        db.execute(DailyReviewBinding.__table__.update().values(version=5))
        return original(db,cutoff_id,version)
    monkeypatch.setattr(service,'ensure_review_version',race)
    with pytest.raises(service.DailyQueryError,match='version_changed'):service.item_page(database,**args)
