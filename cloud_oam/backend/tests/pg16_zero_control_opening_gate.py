"""Start and recover a proven-zero opening using the actual API database role."""
from dataclasses import dataclass
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from fastapi import Response
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.foundation_models import SyncRun, SourceSystem, Organization
from app.formal_access import load_formal_principal
from app.formal_services.postgresql_lock_graph import lock_opening_control_import
from app.inventory_models import StockLocation
from app.inventory_control_projection_models import ControlProjectionPublication
from app.opening_stocktake_schemas import OpeningStocktakeStartIn,OpeningStocktakeFromPublicationIn
from app.routers.formal_opening_stocktake import start_formal_opening_stocktake,start_formal_opening_from_publication
from pg16_inventory_control_preparation_gate import _formal_stock


@dataclass
class ZeroOpening:
    payload: OpeningStocktakeStartIn
    key: str
    request_id: str
    result: object
    publication_id: UUID


def start_zero_opening(owner, api, world, published):
    publication_id=UUID(published['publication_id'])
    with Session(owner) as db:
        publication = db.get(ControlProjectionPublication, publication_id)
        assert publication.record_count == publication.origin_count == 0
        run = db.get(SyncRun, publication.sync_run_id)
        location = StockLocation(code='ZERO-'+uuid4().hex, name='Synthetic empty regional location',
            location_type='region', owner_org_id=world.region, status='active')
        db.add(location); db.commit()
        payload = OpeningStocktakeStartIn(task_no='ZERO-'+uuid4().hex,
            region_org_id=world.region, control_source_system_id=world.source,
            control_sync_run_id=run.id, control_sync_scope_key=run.scope_key,
            scopes=[dict(owner_org_id=world.region, location_id=location.id,
                         assignee_user_id=world.actor.user_id, freeze_mode='hard')], control_lines=[])
    # A merely completed zero-row sync has no publication/coverage proof. The
    # real API role must still be rejected before the inventory ledger is held.
    from test_opening_stocktake_service import _install_control_sync
    with Session(owner) as db:
        unproven = _install_control_sync(db, source=db.get(SourceSystem,world.source),
            region=db.get(Organization,world.region), material=None, rows=())
        db.commit(); unproven_id = unproven.sync_run.id
    with Session(api) as db:
        with pytest.raises(DBAPIError) as error:
            lock_opening_control_import(db,world.source,unproven_id)
        assert error.value.orig.sqlstate == 'P0001'
        db.rollback()
    before = _formal_stock(owner)
    key, request_id = uuid4().hex, uuid4().hex
    barrier = Barrier(2)
    selected=OpeningStocktakeFromPublicationIn.model_validate(payload.model_dump(mode="json",exclude={"control_source_system_id","control_sync_run_id","control_sync_scope_key","control_lines"})|{"publication_id":str(publication_id)})
    def start(from_publication):
        with Session(api) as db:
            assert db.execute(text('SELECT current_user,session_user')).one() == ('star_oam_api', 'star_oam_api')
            actor = load_formal_principal(db, world.actor.user_id)
            barrier.wait(timeout=20)
            route=start_formal_opening_from_publication if from_publication else start_formal_opening_stocktake
            return route(selected if from_publication else payload, Response(), principal=actor, db=db,
                idempotency_key=key, request_id=request_id)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(start,choice) for choice in (False,True)]
        results = [future.result(timeout=60) for future in futures]
    assert {result.replayed for result in results} == {False, True}
    assert results[0].task_id == results[1].task_id
    result = next(value for value in results if not value.replayed)
    assert result.status == 'counting' and not result.replayed
    assert result.scope_count == 1 and result.control_line_count == result.snapshot_line_count == 0
    after = _formal_stock(owner)
    # Starting creates a task, freeze, initial round, audit/outbox facts only;
    # it is not an inventory opening establishment or an inbound posting.
    assert after[:3] == before[:3]
    assert len(after[3]) == len(before[3])+1
    assert len(after[4]) == len(before[4])+1
    with owner.connect() as db:
        task = str(result.task_id)
        assert db.scalar(text('SELECT count(*) FROM public.stocktake_scopes WHERE task_id=:id'), {'id':task}) == 1
        assert db.scalar(text('SELECT count(*) FROM public.stocktake_rounds WHERE task_id=:id'), {'id':task}) == 1
        assert db.scalar(text('SELECT count(*) FROM public.stocktake_control_snapshot_lines WHERE task_id=:id'), {'id':task}) == 0
    return ZeroOpening(payload, key, request_id, result, publication_id)


def replay_zero_opening(owner, api, world, saved):
    before = _formal_stock(owner)
    with Session(api) as db:
        actor = load_formal_principal(db, world.actor.user_id)
        result = start_formal_opening_stocktake(saved.payload, Response(), principal=actor, db=db,
            idempotency_key=saved.key, request_id=saved.request_id)
    assert result.replayed and result.task_id == saved.result.task_id
    assert result.model_dump(exclude={'replayed'}) == saved.result.model_dump(exclude={'replayed'})
    assert _formal_stock(owner) == before
    recover_zero_opening_read_only(owner,api,world,saved)
    assert_zero_opening_http(owner,api,world,saved)


def recover_zero_opening_read_only(owner,api,world,saved):
    from app.formal_services.opening_start_recovery import recover_start
    before=_formal_stock(owner)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor=load_formal_principal(db,world.actor.user_id)
        recovered=recover_start(db,actor=actor,region_org_id=world.region,publication_id=saved.publication_id,trace_request_id=saved.request_id)
        assert recovered['outcome']=='found' and recovered['automatic_retry_allowed'] is False
        assert recovered['result']['task_id']==saved.result.task_id
        missing=recover_start(db,actor=actor,region_org_id=world.region,publication_id=saved.publication_id,trace_request_id=uuid4().hex)
        assert missing['outcome']=='not_observed' and missing['result'] is None and missing['automatic_retry_allowed'] is False
    # Historical outcome remains provable when current source availability is
    # withdrawn. This owned transaction is rolled back after the pure read.
    with Session(owner) as db:
        db.execute(text('UPDATE public.source_systems SET enabled=false WHERE id=:id'),{'id':world.source})
        actor=load_formal_principal(db,world.actor.user_id)
        recovered=recover_start(db,actor=actor,region_org_id=world.region,publication_id=saved.publication_id,trace_request_id=saved.request_id)
        assert recovered['outcome']=='found' and recovered['result']['task_id']==saved.result.task_id
        db.rollback()
    assert _formal_stock(owner)==before


def assert_zero_opening_http(owner,api,world,saved):
    from unittest.mock import patch
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.config import get_settings
    from app.routers.formal_opening_stocktake import router
    before=_formal_stock(owner)
    app=FastAPI();app.include_router(router,prefix='/api')
    def session():
        with Session(api) as db:yield db
    app.dependency_overrides[get_db]=session
    client=TestClient(app)
    payload=saved.payload.model_dump(mode='json',exclude={'control_source_system_id','control_sync_run_id','control_sync_scope_key','control_lines'})|{'publication_id':str(saved.publication_id)}
    headers={'Authorization':'Bearer '+world.login['access_token'],'Idempotency-Key':saved.key,'X-Request-ID':saved.request_id}
    prefix='/api/v1/stocktakes/opening'
    with patch('app.dependencies.get_settings',return_value=get_settings().model_copy(update={'environment':'production'})):
        assert client.post(prefix+'/from-publication',json=payload).status_code==401
        assert client.post(prefix+'/from-publication',json=payload|{'control_lines':[]},headers=headers).status_code==422
        response=client.post(prefix+'/from-publication',json=payload,headers=headers)
        assert response.status_code==200,response.text
        assert response.json()['task_id']==str(saved.result.task_id) and response.json()['replayed']
        params={'region_org_id':str(world.region),'publication_id':str(saved.publication_id),'trace_request_id':saved.request_id}
        response=client.get(prefix+'/start-result',params=params,headers=headers)
        assert response.status_code==200,response.text
        assert response.json()['outcome']=='found' and not response.json()['automatic_retry_allowed']
        assert response.headers['cache-control']=='no-store'
        assert client.get(prefix+'/start-result',params=params|{'extra':'x'},headers=headers).status_code==422
    assert _formal_stock(owner)==before
    print('PG16 0125 production JWT HTTP selected-start exact replay and no-store read-only result recovery PASS',flush=True)
