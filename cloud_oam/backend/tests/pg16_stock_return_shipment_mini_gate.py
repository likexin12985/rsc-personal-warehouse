"""The mini parcel SDK traverses actual API-role HTTP and immutable SQL guards."""
from contextlib import nullcontext
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import patch
from uuid import UUID, uuid4
from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.stock_operation_models import StockOperationOutboundLine
from app.stock_return_outbound_schemas import StockReturnOutboundPreviewIn, StockReturnOutboundSubmitIn
from app.stock_return_shipment_schemas import StockReturnShipmentPreviewIn
from app.formal_services.stock_return_outbound_plan import preview_outbound
from app.formal_services.stock_return_outbound_commands import execute_outbound
from app.formal_services.stock_return_shipment_plan import intent
from app.formal_services.work_order_return_sources import _hash
from pg16_work_order_material_gate import _checkpoint
from pg16_stock_return_transport_gate import _app
from pg16_stock_return_shipment_gate import parcel_snapshot
from pg16_stock_return_outbound_mini_gate import _fresh_world, _fixture as departure_fixture


def _fixture(db, prepared, kind):
    original = departure_fixture(db, prepared, kind)
    actor = load_formal_principal(db, prepared['world'][1])
    coordinates = dict(actor=actor, work_order_id=UUID(original['workOrderId']), operation_id=UUID(original['operationId']))
    request = StockReturnOutboundPreviewIn(operator_person_id=actor.person_id, outbound_at=original['outboundAt'],
        reason=original['reason'], lines=original['lines'])
    checked, _ = preview_outbound(db, **coordinates, request=request)
    departed = execute_outbound(db, **coordinates, request=StockReturnOutboundSubmitIn(**request.model_dump(),
        expected_plan_hash=checked.plan_hash, request_id=uuid4().hex, idempotency_key=uuid4().hex))
    _checkpoint(db)
    line = db.scalars(select(StockOperationOutboundLine).where(StockOperationOutboundLine.outbound_id == departed.outbound_id)).one()
    parcel = StockReturnShipmentPreviewIn(operator_person_id=actor.person_id, shipped_at=datetime.now(timezone.utc),
        carrier='Synthetic carrier 🔧', tracking_no='SYNTHETIC-'+uuid4().hex, reason='已交承运🔧\n接收仓验收与入账独立处理',
        lines=[dict(outbound_line_id=line.id, quantity='1' if kind=='serial' else '.125',
            serial_ids=tuple(proof.serial_id for proof in departed.lines[0].selected_serials))])
    command = intent(departed.operation_id, parcel)
    return dict(workOrderId=original['workOrderId'], personId=original['personId'], authorizationVersion=original['authorizationVersion'],
        operationId=original['operationId'], shippedAt=command['shipped_at'], carrier=parcel.carrier, trackingNo=parcel.tracking_no,
        reason=parcel.reason, lines=command['lines'], tracked=kind=='serial', pythonRequestHash=_hash(command))


def assert_shipment_mini_gate(api_engine,fixture_engine,worlds):
    node=shutil.which('node');assert node,'Node is required for the parcel mini SDK gate'
    script=Path(__file__).resolve().parents[2]/'miniprogram/tests/fixtures/stock-return-shipment-submit-pg16.cjs'
    for kind,prepared in worlds.items():
        prepared=_fresh_world(fixture_engine,prepared)
        baseline=parcel_snapshot(api_engine)
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
                            assert ready.select(timeout=30),'Parcel mini SDK stopped producing requests'
                            message=process.stdout.readline();assert message,'Parcel mini SDK exited: '+process.stderr.read()
                            data=json.loads(message)
                            if data.get('complete'):
                                assert data==dict(complete=True,posts=1,previews=1,reads=3 if mode=='lost_before' else 1,
                                    seals=1 if mode=='lost_before' else 0,confirmations=1,status='sealed' if mode=='lost_before' else 'confirmed')
                                complete=True;break
                            request=data['request'];write=request['method']=='POST' and not request['path'].endswith('/preview')
                            command=write and not request['path'].endswith('/seal')
                            if command:posts+=1;assert posts==1
                            before=parcel_snapshot(local);statements=[]
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
                                assert parcel_snapshot(local)==before
                            process.stdin.write(json.dumps(reply)+'\n');process.stdin.flush()
                        assert complete
                    process.stdin.close();process.wait(timeout=10);assert process.returncode==0,process.stderr.read()
                finally:
                    if process.poll() is None:process.kill();process.wait(timeout=10)
                    for stream in (process.stdin,process.stdout,process.stderr):stream.close()
                    db.rollback()
            assert parcel_snapshot(api_engine)==baseline
            print(f'PG16 {kind} mini parcel {mode}: original catalog, exact digest, one POST, original GET, no read mutation and full rollback PASS',flush=True)
