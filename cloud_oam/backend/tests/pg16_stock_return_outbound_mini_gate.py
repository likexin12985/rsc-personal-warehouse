"""Shipped departure SDK traverses actual API-role HTTP and SQL guards."""
from contextlib import nullcontext
from datetime import datetime,timezone
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID,uuid4
from fastapi.testclient import TestClient
from sqlalchemy import event,select
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.demand_models import WorkOrderReplacement,WorkOrderMaterialLine,WorkOrderMaterialSerial
from app.inventory_models import StockAccount,FormalMaterial,InventorySerial
from app.stock_operation_models import StockOperationLine
from app.stock_return_schemas import StockReturnPreviewIn,StockReturnSubmitIn
from app.stock_return_outbound_schemas import StockReturnOutboundPreviewIn
from app.formal_services.stock_return_commands import submit_return
from app.formal_services.stock_return_plan import preview_return
from app.formal_services.stock_return_outbound_plan import intent
from app.formal_services.work_order_return_sources import _hash
from pg16_work_order_reversal_submit_gate import _original
from pg16_work_order_material_gate import _checkpoint
from pg16_stock_return_transport_gate import _app
from pg16_stock_return_outbound_gate import departure_snapshot
from work_order_fixtures import add_order


def _fresh_world(fixture_engine,prepared):
    account_id,user_id,_orders,line=prepared['world']
    with Session(fixture_engine) as db:
        account=db.get(StockAccount,account_id)
        context=SimpleNamespace(person=SimpleNamespace(id=account.custodian_person_id),organization=SimpleNamespace(id=account.owner_org_id))
        orders=tuple(add_order(db,context).id for _ in range(2));db.commit()
    return {**prepared,'world':(account_id,user_id,orders,line)}


def _fixture(db,prepared,kind):
    world=prepared['world'];_account,user_id,orders,_line=world
    original=_original(db,world,'replace')
    parent=db.get(WorkOrderReplacement,UUID(original['original_replacement_id']))
    origin=db.scalars(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id==parent.recover_operation_id)).one()
    account=db.get(StockAccount,origin.stock_account_id);sku=db.get(FormalMaterial,account.material_id)
    serials=tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(select(WorkOrderMaterialSerial.serial_id).where(WorkOrderMaterialSerial.operation_line_id==origin.id)))))
    proofs=[dict(serial_id=sn.id,sku_code=sku.sku_code,serial_no=sn.serial_no,qr_code=sn.qr_code) for sn in serials]
    actor=load_formal_principal(db,user_id)
    request=StockReturnPreviewIn(operator_person_id=actor.person_id,target_location_id=prepared['target_id'],transit_location_id=prepared['transit_id'],
        reason='Synthetic original return for mini departure',lines=[dict(source_recovery_line_id=origin.id,stock_account_id=account.id,quantity='1',serial_verifications=proofs)])
    checked,_=preview_return(db,actor=actor,work_order_id=orders[0],request=request)
    submitted=submit_return(db,actor=actor,work_order_id=orders[0],request=StockReturnSubmitIn(**request.model_dump(),
        expected_plan_hash=checked.plan_hash,idempotency_key=uuid4().hex,request_id=uuid4().hex));_checkpoint(db)
    line=db.scalars(select(StockOperationLine).where(StockOperationLine.operation_id==submitted.operation_id)).one()
    value=StockReturnOutboundPreviewIn(operator_person_id=actor.person_id,outbound_at=datetime.now(timezone.utc),reason='核验实物后发出🔧\n接收责任独立处理',
        lines=[dict(operation_line_id=line.id,quantity='1.000' if kind=='serial' else '0.375',serial_verifications=proofs)])
    command=intent(submitted.operation_id,value)
    return dict(workOrderId=str(orders[0]),personId=str(actor.person_id),authorizationVersion=actor.authorization_version,
        operationId=str(submitted.operation_id),outboundAt=command['outbound_at'],reason=value.reason,lines=command['lines'],
        tracked=kind=='serial',pythonRequestHash=_hash(command))


def assert_departure_mini_gate(api_engine,fixture_engine,worlds):
    node=shutil.which('node');assert node,'Node is required for the departure mini SDK gate'
    script=Path(__file__).resolve().parents[2]/'miniprogram/tests/fixtures/stock-return-outbound-submit-pg16.cjs'
    for kind,prepared in worlds.items():
        prepared=_fresh_world(fixture_engine,prepared)
        baseline=departure_snapshot(api_engine)
        for mode in ('post','lost_after','lost_before'):
            with Session(api_engine) as db:
                fixture={**_fixture(db,prepared,kind),'mode':mode}
                local=SimpleNamespace(connect=lambda:nullcontext(db.connection()))
                process=subprocess.Popen([node,str(script)],text=True,bufsize=1,stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,stderr=subprocess.PIPE,env={'PATH':os.environ.get('PATH','')})
                try:
                    process.stdin.write(json.dumps(fixture)+'\n');process.stdin.flush()
                    with selectors.DefaultSelector() as ready,TestClient(_app(db,prepared['world'][1])) as client,patch.object(db,'commit',side_effect=lambda:_checkpoint(db)):
                        ready.register(process.stdout,selectors.EVENT_READ)
                        complete=False;posts=0
                        for _ in range(15):
                            assert ready.select(timeout=30),'Departure mini SDK stopped producing requests'
                            message=process.stdout.readline();assert message,'Departure mini SDK exited: '+process.stderr.read()
                            data=json.loads(message)
                            if data.get('complete'):
                                assert data==dict(complete=True,posts=1,previews=1,reads=3 if mode=='lost_before' else 1,
                                    seals=1 if mode=='lost_before' else 0,confirmations=1,status='sealed' if mode=='lost_before' else 'confirmed')
                                complete=True;break
                            request=data['request'];write=request['method']=='POST' and not request['path'].endswith('/preview')
                            command=write and not request['path'].endswith('/seal')
                            if command:posts+=1;assert posts==1
                            before=departure_snapshot(local);statements=[]
                            def capture(_c,_cu,statement,_p,_ctx,_m):statements.append(statement)
                            connection=db.connection();event.listen(connection,'before_cursor_execute',capture)
                            try:
                                if command and mode=='lost_before':reply={'transportLost':True}
                                else:
                                    response=client.request(request['method'],request['path'],json=request.get('data'),headers=request['headers'])
                                    assert response.status_code in (200,404),response.text
                                    assert 'no-store' in response.headers['cache-control']
                                    reply={'transportLost':True} if command and mode=='lost_after' else {'status':response.status_code,'body':response.json()}
                            finally:event.remove(connection,'before_cursor_execute',capture)
                            if not write:
                                assert statements and all(sql.lstrip().upper().startswith('SELECT') for sql in statements)
                                assert departure_snapshot(local)==before
                            process.stdin.write(json.dumps(reply)+'\n');process.stdin.flush()
                        assert complete
                    process.stdin.close();process.wait(timeout=10);assert process.returncode==0,process.stderr.read()
                finally:
                    if process.poll() is None:process.kill();process.wait(timeout=10)
                    for stream in (process.stdin,process.stdout,process.stderr):stream.close()
                    db.rollback()
            assert departure_snapshot(api_engine)==baseline
            print(f'PG16 {kind} mini departure {mode}: original catalog, exact digest, one POST, original GET, no read mutation and full rollback PASS',flush=True)
