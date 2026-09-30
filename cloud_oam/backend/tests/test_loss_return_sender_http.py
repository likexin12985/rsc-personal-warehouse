"""Real application middleware and candidate sender routes, synthetic DB only."""
from dataclasses import replace
from types import SimpleNamespace
from uuid import UUID, uuid4

from fastapi import HTTPException
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import select, text

from app.database import get_db
from app.routers import formal_loss_return_sending as routes
from app.formal_services import loss_return_sender_directory as directory
from app.formal_services import stock_loss_regional_reviews as regional_reviews
from app.formal_services import stock_loss_return_commands as return_commands
from app.stock_operation_models import StockOperationLine
from test_stock_loss_derived_returns import execute_request
from test_stock_loss_dispositions import second_report
from test_stock_loss_return_shipment import db, world, stock, allowed, evidence, regional, headquarters, approved, route, derived, ready, parcel, snapshot

BASE='/api/v1/stock-operations/loss-reports/returns'


def http_client(monkeypatch, session, principal):
    from app.main import app
    overrides={get_db:lambda:session}
    for route in routes.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name=='principal':overrides[dependency.call]=principal
    monkeypatch.setattr(app,'dependency_overrides',overrides)
    return TestClient(app,raise_server_exceptions=False)


def private(response):
    assert 'no-store' in response.headers['cache-control']
    assert response.headers['referrer-policy']=='no-referrer'
    assert 'qr_code' not in response.text and 'PRIVATE-SCAN' not in response.text


def test_real_sender_routes_serialize_loss_only_and_keep_physical_states_separate(db,parcel,allowed,monkeypatch):
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    client=http_client(monkeypatch,db,lambda:allowed.world.current_principal)
    try:
        listing=client.get(BASE+'/my-sending');private(listing)
        assert listing.status_code==200,listing.text
        assert listing.json()['items'][0]['origin']['operation_id']==str(parcel.order.id)
        for suffix in ('outbounds/options','outbounds','shipments/options','shipments'):
            response=client.get(f'{BASE}/{parcel.order.id}/{suffix}');private(response)
            assert response.status_code==200,response.text
            assert 'work_order_id' not in response.text and 'source_recovery_line_id' not in response.text
        outgoing=client.get(f'{BASE}/{parcel.order.id}/outbounds').json()
        shipping=client.get(f'{BASE}/{parcel.order.id}/shipments').json()
        assert outgoing['outbound_status']=='outbound'
        assert shipping['shipment_status']=='not_shipped' and shipping['items']==[]
        readonly=replace(parcel.actor,entitlements=tuple(e for e in parcel.actor.entitlements if e.action not in ('outbound_return','ship_return')))
        allowed.world.current_principal=readonly
        for suffix in ('outbounds','shipments'):
            response=client.get(f'{BASE}/{parcel.order.id}/{suffix}');private(response)
            assert response.status_code==200,response.text
            response=client.get(f'{BASE}/{parcel.order.id}/{suffix}/options');private(response)
            assert response.status_code==403,response.text
        wrong=client.get(f'{BASE}/{uuid4()}/shipments');private(wrong)
        assert wrong.status_code==404,wrong.text
    finally:client.close()
    assert snapshot(db)==before and not db.new and not db.dirty


def test_two_real_derived_returns_page_without_duplicates_and_invalidate_old_snapshot(db,derived,allowed,regional,approved,route,monkeypatch):
    derived.actor=replace(derived.actor,entitlements=derived.actor.entitlements+(
        replace(derived.actor.entitlements[-1],resource='stock_operation',action='read'),))
    allowed.world.current_principal=derived.actor
    old=directory.list_sender_returns(db,actor=derived.actor,limit=1)
    second=second_report(db,allowed,regional,monkeypatch)
    allowed.world.current_principal=regional.actor
    request=regional.request.model_copy(update={'operation_id':second.operation_id,'expected_submission_plan_hash':second.plan_hash,
        'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    verified=regional_reviews.verify_regional_loss(db,actor=regional.actor,request=request);db.commit()
    line=db.scalar(select(StockOperationLine).where(StockOperationLine.operation_id==second.operation_id))
    request=approved.headquarters.request.model_copy(update={'operation_id':second.operation_id,'expected_submission_plan_hash':second.plan_hash,
        'regional_review_id':verified.review_id,'expected_regional_review_hash':verified.request_hash,
        'request_id':uuid4().hex,'idempotency_key':uuid4().hex,
        'decisions':(approved.headquarters.request.decisions[0].model_copy(update={'line_id':line.id}),)})
    approved_second=SimpleNamespace(actor=approved.actor,headquarters=SimpleNamespace(request=request))
    allowed.world.current_principal=approved.actor
    command,_=execute_request(db,approved_second,route)
    created=return_commands.execute_loss_return(db,actor=approved.actor,request=command);db.commit()
    allowed.world.current_principal=derived.actor
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    client=http_client(monkeypatch,db,lambda:allowed.world.current_principal)
    try:
        stale=client.get(BASE+'/my-sending',params={'limit':1,'snapshot_hash':old.snapshot_hash});private(stale)
        assert stale.status_code==409,stale.text
        first=client.get(BASE+'/my-sending',params={'limit':1});private(first)
        assert first.status_code==200,first.text
        page=first.json();assert len(page['items'])==1 and page['next_after_id'] is not None
        second_page=client.get(BASE+'/my-sending',params={'limit':1,'snapshot_hash':page['snapshot_hash'],'after_id':page['next_after_id']});private(second_page)
        assert second_page.status_code==200,second_page.text
        last=second_page.json();assert len(last['items'])==1 and last['next_after_id'] is None
        assert {item['origin']['operation_id'] for item in page['items']+last['items']}=={str(derived.order.id),created['return_operation_id']}
        assert page['items'][0]['origin']['operation_id']<last['items'][0]['origin']['operation_id']
    finally:client.close()
    assert snapshot(db)==before


def test_original_detail_survives_departure_and_write_revocation_without_exposing_available_stock(db,parcel,allowed,monkeypatch):
    readonly=replace(parcel.actor,entitlements=tuple(e for e in parcel.actor.entitlements if e.action not in ('outbound_return','ship_return')))
    allowed.world.current_principal=readonly
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    client=http_client(monkeypatch,db,lambda:allowed.world.current_principal)
    try:
        response=client.get(f'{BASE}/{parcel.order.id}');private(response)
        assert response.status_code==200,response.text
        result=response.json();line=result['line']
        assert result['origin']['operation_id']==str(parcel.order.id)
        assert line['return_quantity']=='1.000' and line['condition_code']=='new'
        assert line['sku_code']==allowed.world.material.sku_code
        assert line['source_loss_line_id']==result['origin']['loss_line_id']
        assert len(line['selected_serials'])==int(allowed.tracked)
        assert all(key not in response.text for key in ('held_quantity','selectable_quantity','availability_bucket','source_recovery_line_id','work_order_id'))
        unknown=client.get(f'{BASE}/{uuid4()}');private(unknown)
        assert unknown.status_code==404,unknown.text
        allowed.world.current_principal=replace(readonly,entitlements=tuple(e for e in readonly.entitlements if not(e.resource=='stock_operation' and e.action=='read')))
        denied=client.get(f'{BASE}/{parcel.order.id}');private(denied)
        assert denied.status_code==403,denied.text
    finally:client.close()
    assert snapshot(db)==before


def test_original_detail_refuses_changed_evidence_and_mid_read_audit(db,parcel,allowed,monkeypatch):
    from app.formal_services import loss_return_sender_detail as detail
    from app.formal_services.inventory_query import InventoryReadError
    real=detail.material_audit_cursor;calls=0
    def changed(session):
        nonlocal calls
        calls+=1
        return real(session) if calls==1 else ()
    before=snapshot(db)
    with monkeypatch.context() as patch:
        patch.setattr(detail,'material_audit_cursor',changed)
        with pytest.raises(InventoryReadError) as error:detail.read_sender_return(db,actor=parcel.actor,operation_id=parcel.order.id)
        assert error.value.code=='stock_loss_return_detail_changed'
    assert snapshot(db)==before
    parcel.order.reason+=' corrupted fixture';db.commit()
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InventoryReadError):detail.read_sender_return(db,actor=parcel.actor,operation_id=parcel.order.id)
    assert snapshot(db)==before


@pytest.mark.parametrize('failure',['auth','query','path','method','unknown'])
def test_sender_framework_failures_are_private(monkeypatch,failure):
    def principal():
        if failure=='auth':raise HTTPException(status_code=401,detail='需要登录')
        return SimpleNamespace(person_id=uuid4())
    path=BASE+'/my-sending'
    if failure=='query':path+='?snapshot_hash=PRIVATE-SCAN'
    elif failure=='path':path=BASE+'/PRIVATE-SCAN/outbounds'
    elif failure=='unknown':path+='/unknown/route'
    client=http_client(monkeypatch,None,principal)
    try:response=client.request('DELETE' if failure=='method' else 'GET',path)
    finally:client.close()
    assert response.status_code=={'auth':401,'query':422,'path':422,'method':405,'unknown':404}[failure],response.text
    private(response)
