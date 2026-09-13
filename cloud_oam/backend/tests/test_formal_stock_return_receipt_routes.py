"""Recipient HTTP acceptance, exact request recovery and transaction failure."""
from dataclasses import replace
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
import pytest
from sqlalchemy import event, select
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_db
from app.routers import formal_stock_return_receipts as router
from app.stock_operation_models import StockOperationReceipt
from test_stock_return_receipt import (db, world, stock, recovered, destination, prepared, parcel, incoming, acceptance,
    inventory, snapshot)


def application(db, actor):
    app=FastAPI();app.include_router(router.router,prefix='/api')
    app.dependency_overrides[get_db]=lambda:db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name=='principal':app.dependency_overrides[dependency.call]=lambda:actor
    return app


def prefix(context):
    return f'/api/v1/stock-returns/my-receiving/{context.package.shipment_id}/receipts'


def preview(client, context):
    response=client.post(prefix(context)+'/preview',json=context.request.model_dump(mode='json'))
    assert response.status_code==200,response.text
    assert 'no-store' in response.headers['cache-control'] and 'qr_code' not in response.text
    return response.json()


def command(context, checked):
    return dict(**context.request.model_dump(mode='json'),expected_plan_hash=checked['plan_hash'],
        request_id=uuid4().hex,idempotency_key=uuid4().hex)


def test_receipt_http_preview_commit_history_and_original_request(db, stock, acceptance):
    before=inventory(db)
    with TestClient(application(db,acceptance.actor)) as client:
        statements=[]
        def capture(_c,_cu,sql,_p,_ctx,_many):statements.append(sql.strip().split()[0].upper())
        c=db.connection();event.listen(c,'before_cursor_execute',capture)
        try:checked=preview(client,acceptance)
        finally:event.remove(c,'before_cursor_execute',capture)
        assert statements and set(statements)=={'SELECT'}
        value=command(acceptance,checked);url=prefix(acceptance)
        response=client.post(url,json=value,headers={'X-Request-ID':value['request_id'],'Idempotency-Key':value['idempotency_key']})
        assert response.status_code==200,response.text
        result=response.json();assert result['status']=='accepted' and 'qr_code' not in response.text
        original=client.get(url+'/by-request/'+value['request_id'])
        assert original.status_code==200 and original.json()==result
        history=client.get(url)
        assert history.status_code==200 and history.json()['receipts']==[result]
        assert all(line['unconfirmed_qty']=='0.000' for line in history.json()['lines'])
        assert 'posting_transaction_id' not in history.text
        sealed=client.post(url+'/by-request/'+value['request_id']+'/seal',json={
            'operator_person_id':str(acceptance.actor.person_id),'request_hash':checked['request_hash']})
        assert sealed.status_code==200 and sealed.json()==result
        for item in (response,original,history,sealed):assert 'no-store' in item.headers['cache-control']
    assert inventory(db)==before


def test_absent_receipt_request_can_be_sealed_and_late_post_is_rejected(db, stock, acceptance):
    before=snapshot(db)
    with TestClient(application(db,acceptance.actor)) as client:
        checked=preview(client,acceptance);value=command(acceptance,checked);url=prefix(acceptance)
        lookup=url+'/by-request/'+value['request_id']
        missing=client.get(lookup)
        assert missing.status_code==404 and missing.json()['detail']['code']=='stock_return_receipt_not_observed'
        assert snapshot(db)==before
        sealed=client.post(lookup+'/seal',json={'operator_person_id':str(acceptance.actor.person_id),
            'request_hash':checked['request_hash']},headers={'X-Request-ID':value['request_id']})
        assert sealed.status_code==200 and sealed.json()['lookup_status']=='sealed'
        assert client.get(lookup).json()==sealed.json()
        late=client.post(url,json=value)
        assert late.status_code==409 and 'no-store' in late.headers['cache-control']
        assert not db.scalar(select(StockOperationReceipt.id))


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_receipt_http_identity_headers_and_write_scope_are_enforced(db, stock, acceptance):
    with TestClient(application(db,acceptance.actor)) as client:
        value=command(acceptance,preview(client,acceptance));before=snapshot(db)
        for header in ({'X-Request-ID':'other-request'},{'Idempotency-Key':'other-key'}):
            response=client.post(prefix(acceptance),json=value,headers=header)
            assert response.status_code==400 and 'no-store' in response.headers['cache-control']
        response=client.post(prefix(acceptance),json={**value,'operator_person_id':str(uuid4())})
        assert response.status_code==403
        assert snapshot(db)==before
    read_actor=replace(acceptance.actor,entitlements=tuple(item for item in acceptance.actor.entitlements if item.action!='receive_return'))
    stock.world.current_principal=read_actor
    with TestClient(application(db,read_actor)) as client:
        assert client.get(prefix(acceptance)).status_code==200
        assert client.post(prefix(acceptance),json=value).status_code==403
    assert snapshot(db)==before


@pytest.mark.parametrize('stock',['quantity'],indirect=True)
def test_receipt_http_failed_commit_rolls_back_and_preserves_recovery_coordinate(db, acceptance, monkeypatch):
    with TestClient(application(db,acceptance.actor)) as client:
        value=command(acceptance,preview(client,acceptance));before=snapshot(db)
        def fail():raise SQLAlchemyError('Synthetic commit failure')
        monkeypatch.setattr(db,'commit',fail)
        response=client.post(prefix(acceptance),json=value)
        assert response.status_code==503 and 'no-store' in response.headers['cache-control']
        assert 'Synthetic' not in response.text
        assert snapshot(db)==before
        lookup=client.get(prefix(acceptance)+'/by-request/'+value['request_id'])
        assert lookup.status_code==404
