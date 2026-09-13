"""Actual API-role HTTP reads preserve separate cancellation/departure facts."""
from collections import Counter
from decimal import Decimal
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select,event
from sqlalchemy.orm import Session
from uuid import uuid4
from app.database import get_db
from app.formal_access import load_formal_principal
from app.stock_operation_models import StockOperationOrder
from app.stock_return_outbound_schemas import StockReturnOutboundSubmitIn
from app.formal_services.stock_return_outbound_plan import preview_outbound
from app.formal_services.stock_return_outbound_commands import execute_outbound
from app.routers import formal_stock_returns as router
from pg16_stock_return_outbound_concurrency_gate import _prepare_return


def assert_departure_queries_gate(api_engine,fixture_engine,worlds):
    from pg16_stock_return_outbound_gate import departure_snapshot
    pending=_prepare_return(api_engine,fixture_engine,worlds['quantity'])
    def depart(quantity):
        with Session(api_engine) as db:
            actor=load_formal_principal(db,pending['user_id'])
            original=pending['value']
            request=original.model_copy(update={'lines':(original.lines[0].model_copy(update={'quantity':Decimal(quantity)}),)})
            coordinate=dict(actor=actor,work_order_id=pending['work_order_id'],operation_id=pending['operation_id'])
            checked,_=preview_outbound(db,**coordinate,request=request)
            request=StockReturnOutboundSubmitIn(**request.model_dump(exclude={'expected_plan_hash','request_id','idempotency_key'}),
                expected_plan_hash=checked.plan_hash,request_id=uuid4().hex,idempotency_key=uuid4().hex)
            result=execute_outbound(db,**coordinate,request=request);db.commit();return result
    first=depart('.375')
    with Session(api_engine) as db:
        orders=tuple(db.execute(select(StockOperationOrder.id,StockOperationOrder.oam_work_order_id)
            .where(StockOperationOrder.actor_user_id==pending['user_id']).order_by(StockOperationOrder.id)))
    assert len(orders)==7
    def read(identifier,work_order_id):
        with Session(api_engine) as db:
            app=FastAPI();app.include_router(router.router,prefix='/api')
            app.dependency_overrides[get_db]=lambda:db
            for route in router.router.routes:
                for dependency in route.dependant.dependencies:
                    if dependency.name=='principal':
                        app.dependency_overrides[dependency.call]=lambda:load_formal_principal(db,pending['user_id'])
            statements=[];connection=db.connection()
            def capture(_c,_cu,statement,_p,_ctx,_m):statements.append(statement)
            event.listen(connection,'before_cursor_execute',capture)
            try:
                with TestClient(app) as client:
                    base=f'/api/v1/work-orders/{work_order_id}/returns/{identifier}/outbounds'
                    history=client.get(base);options=client.get(base+'/options')
            finally:event.remove(connection,'before_cursor_execute',capture)
            assert statements and all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
            assert history.status_code==200,history.text
            assert 'no-store' in history.headers['cache-control'] and 'no-store' in options.headers['cache-control']
            assert 'qr_code' not in history.text+options.text and 'serial_verifications' not in options.text
            result=history.json();assert result['original']['status']=='submitted'
            if result['cancellation'] is not None:
                assert options.status_code==409 and options.json()['detail']['code']=='stock_return_already_cancelled'
                assert result['outbound_status']=='not_outbound' and result['items']==[]
            else:
                assert options.status_code==200,options.text
                if result['outbound_status']=='outbound':
                    assert all(row['remaining_quantity']==row['selectable_quantity']=='0.000' and row['serials']==[] for row in options.json()['lines'])
                if identifier==pending['operation_id'] and len(result['items'])==1:
                    assert options.json()['lines'][0]['departed_quantity']=='0.375'
                    assert options.json()['lines'][0]['remaining_quantity']==options.json()['lines'][0]['selectable_quantity']=='0.625'
                    assert result['items'][0]['outbound_id']==str(first.outbound_id)
            return result
    before=departure_snapshot(api_engine)
    statuses=Counter(read(identifier,work_order_id)['outbound_status'] for identifier,work_order_id in orders)
    assert statuses==Counter({'outbound':4,'not_outbound':2,'partially_outbound':1})
    assert departure_snapshot(api_engine)==before
    second=depart('.625')
    before=departure_snapshot(api_engine)
    final=read(pending['operation_id'],pending['work_order_id'])
    assert final['outbound_status']=='outbound' and [row['outbound_id'] for row in final['items']]==[str(first.outbound_id),str(second.outbound_id)]
    assert departure_snapshot(api_engine)==before
    print('PG16 departure HTTP directories/history: quantity partial-to-full, original SN, cancellation separation, SELECT-only reads and zero leaked QR proof PASS',flush=True)
    assert_read_only_departure_history(api_engine,pending['user_id'])


def assert_read_only_departure_history(api_engine,user_id):
    from sqlalchemy import text
    from app.formal_services.stock_return_outbound_queries import outbound_history
    from pg16_stock_return_outbound_gate import departure_snapshot
    before=departure_snapshot(api_engine)
    with Session(api_engine) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor=load_formal_principal(db,user_id)
        orders=tuple(db.execute(select(StockOperationOrder.id,StockOperationOrder.oam_work_order_id).where(StockOperationOrder.actor_user_id==user_id)))
        assert orders
        for identifier,work_order_id in orders:
            coordinate=dict(actor=actor,work_order_id=work_order_id,operation_id=identifier)
            history=outbound_history(db,**coordinate)
            assert history.operation_id==identifier
    assert departure_snapshot(api_engine)==before
    # The directory also replays current opening evidence through the existing
    # ledger/task/audit locking protocol. Do not weaken that proof to make a
    # READ ONLY transaction pass; its no-write guarantee is tested above.
    print('PG16 departure history runs in enforced READ ONLY transaction without row locks or stock mutation PASS',flush=True)
