"""Actual sender HTTP commands on a caller-owned synthetic PostgreSQL 16 world.

Only authentication is injected. SQL uses star_oam_api and current real grants;
no command, database constraint, service proof, or commit is substituted.
"""
from contextlib import contextmanager
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi import Depends
from fastapi.testclient import TestClient
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_formal_principal
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission
from app.loss_return_outbound_schemas import LossReturnOutboundOut
from app.stock_operation_models import StockOperationOutboundLine
from pg16_loss_return_sender_read_gate import BASE
import pg16_stock_loss_return_shipment_gate as fixture


def snapshot(owner):
    result=fixture.snapshot(owner)
    with owner.connect() as db:
        for table in ('stock_operation_outbounds','stock_operation_outbound_lines',
                      'stock_operation_outbound_serials','stock_operation_command_seals',
                      'stock_operation_receipts','stock_operation_return_inbounds','receipts','inbound_orders'):
            result[table]=tuple(sorted(repr(dict(row)) for row in db.execute(text('SELECT * FROM '+table)).mappings()))
    return result


@contextmanager
def http(db, user_id):
    from app.main import app
    def database():
        assert db.scalar(text('SELECT current_user'))=='star_oam_api'
        yield db
    def principal(session=Depends(get_db)):
        return load_formal_principal(session,user_id)
    # The single caller session is borrowed serially by TestClient. Routes own
    # their usual commit/rollback boundaries; no parallel caller uses it.
    with patch.object(app,'dependency_overrides',{get_db:database,get_formal_principal:principal}):
        client=TestClient(app,raise_server_exceptions=False)
        try:yield client
        finally:client.close()


def post(client, path, body, status=200, *, headers=None):
    response=client.post(BASE+path,json=body,headers=headers or {})
    assert response.status_code==status,(path,response.status_code,response.text)
    assert 'no-store' in response.headers['cache-control']
    assert response.headers['referrer-policy']=='no-referrer'
    assert all(value not in response.text for value in ('qr_code','PRIVATE-SCAN','source_recovery_line_id','work_order_id'))
    return response.json()


def exercise(context):
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    recorded={}
    def depart(db, *, actor, work_order_id, operation_id, request):
        assert work_order_id is None
        path='/'+str(operation_id)+'/outbounds'
        body=request.model_dump(mode='json')
        preview={k:v for k,v in body.items() if k not in ('expected_plan_hash','request_id','idempotency_key')}
        with http(db,context['engineer_id']) as client:
            before=snapshot(owner)
            plan=post(client,path+'/preview',preview)
            body['expected_plan_hash']=plan['plan_hash']
            assert snapshot(owner)==before
            assert post(client,path+'/requests/lookup',body)=={'lookup_status':'not_observed','retry_allowed':False}
            assert snapshot(owner)==before
            post(client,path,body,status=400,headers={'X-Request-ID':'different-request'})
            assert snapshot(owner)==before
            result=post(client,path,body,headers={'X-Request-ID':body['request_id'],'Idempotency-Key':body['idempotency_key']})
        recorded['outbound_return']=(path,body,result)
        return LossReturnOutboundOut.model_validate(result)
    world=fixture.prepare_departures(context,departure=depart)
    with Session(api) as db:
        line=db.scalars(select(StockOperationOutboundLine).where(
            StockOperationOutboundLine.outbound_id==world['first'].outbound_id)).one()
        preview=dict(operator_person_id=str(context['person_id']),carrier='Synthetic carrier',
            tracking_no='SYNTHETIC-'+uuid4().hex,shipped_at=datetime.now(timezone.utc).isoformat(),
            reason='Synthetic HTTP handover',lines=[dict(outbound_line_id=str(line.id),
                quantity=str(world['first'].lines[0].selected_quantity),
                serial_ids=[str(s.serial_id) for s in world['first'].lines[0].selected_serials])])
        path='/'+str(world['order_id'])+'/shipments'
        with http(db,context['engineer_id']) as client:
            before=snapshot(owner)
            plan=post(client,path+'/preview',preview)
            assert snapshot(owner)==before
            body=dict(**preview,expected_plan_hash=plan['plan_hash'],request_id=uuid4().hex,idempotency_key=uuid4().hex)
            assert post(client,path+'/requests/lookup',body)=={'lookup_status':'not_observed','retry_allowed':False}
            assert snapshot(owner)==before
            result=post(client,path,body,headers={'X-Request-ID':body['request_id'],'Idempotency-Key':body['idempotency_key']})
            recorded['ship_return']=(path,body,result)
    outcomes={}
    for kind,(path,body,result) in recorded.items():
        assert result['origin']['requester_id']==str(context['person_id'])
        assert result['origin']['submitted_by_user_id']==str(context['admin_id'])
        assert result['origin']['submitted_by_user_id']!=str(context['engineer_id'])
        before=snapshot(owner)
        with Session(api) as db,http(db,context['engineer_id']) as client:
            # A fresh API session has only the original command, not a response ID.
            found=post(client,path+'/requests/lookup',body)
            assert found==dict(lookup_status='found',retry_allowed=False,operation_type=kind,result=result)
            changed=dict(body,reason='Different original body')
            post(client,path+'/requests/lookup',changed,status=409)
            assert snapshot(owner)==before
            missing=dict(body,request_id=uuid4().hex,idempotency_key=uuid4().hex)
            seal=post(client,path+'/requests/seal',missing,headers={'X-Request-ID':missing['request_id']})
            assert seal['lookup_status']=='sealed' and seal['retry_allowed'] is False
            assert seal['seal']['seal_scope']=='actor_request_id'
            after=snapshot(owner)
            assert len(after['stock_operation_command_seals'])==len(before['stock_operation_command_seals'])+1
            for table in before.keys()-{'stock_operation_command_seals','audit_events','audit_chain_heads'}:
                assert before[table]==after[table],table
            assert post(client,path+'/requests/lookup',missing)==seal
            error=post(client,path,missing,status=409,headers={'X-Request-ID':missing['request_id'],'Idempotency-Key':missing['idempotency_key']})
            assert error['detail']['code']=='stock_return_request_sealed'
            assert snapshot(owner)==after
        outcomes[kind]=(path,body,missing,seal,result)
    with Session(owner) as db:
        grants=tuple(db.scalars(select(RolePermission).join(Permission,Permission.id==RolePermission.permission_id)
            .join(Role,Role.id==RolePermission.role_id).where(Role.code=='technician',
                Permission.resource=='stock_operation',Permission.action.in_(('outbound_return','ship_return')),
                Permission.field_code=='')))
        assert len(grants)==2
        saved={g.id:g.effect for g in grants}
        for grant in grants:grant.effect='deny'
        db.commit()
    try:
        before=snapshot(owner)
        for kind,(path,body,missing,seal,result) in outcomes.items():
            with Session(api) as db,http(db,context['engineer_id']) as client:
                assert post(client,path+'/requests/lookup',body)['result']==result
                assert post(client,path+'/requests/lookup',missing)==seal
                post(client,path+'/requests/seal',missing,status=403,headers={'X-Request-ID':missing['request_id']})
                post(client,path,missing,status=403,headers={'X-Request-ID':missing['request_id'],'Idempotency-Key':missing['idempotency_key']})
        assert snapshot(owner)==before
    finally:
        with Session(owner) as db:
            for key,effect in saved.items():db.get(RolePermission,key).effect=effect
            db.commit()
    final=snapshot(owner)
    assert not final['stock_operation_receipts'] and not final['stock_operation_return_inbounds']
    assert not final['receipts'] and not final['inbound_orders']
    print('PG16 sender HTTP '+context['tracking']+': actual route commits, fresh-session lookup, seals and real grant revocation PASS',flush=True)
    return dict(passed=True,tracking=context['tracking'],actualHttpCommands=True,actualRuntimeRole=True,
        freshSessionOriginalCommandRecovery=True,lateSealedExecutionDenied=True,
        readOnlyGrantRecovery=True,noReceiptOrInboundInference=True,productionAcceptance=False)


def release(engines, *, tracking, migrate, provision):
    if tracking not in ('quantity','serial'):
        raise ValueError('tracking must be quantity or serial')
    from app.database_security import validate_production_database_security
    from pg16_stock_loss_derived_return_gate import catalog
    migrate('initial-upgrade','upgrade','head')
    migrate('empty-sender-downgrade','downgrade','20261205_0156')
    migrate('empty-sender-reupgrade','upgrade','head')
    provision()
    def security():
        validate_production_database_security(engines['star_oam_api'],
            expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    security()
    result=fixture.run_sources(engines,tracking=tracking,after_preview=exercise)
    assert result['passed'] and result['submission']['passed']
    owner=engines['star_oam_migrator']
    before=snapshot(owner);before_catalog=catalog(owner)
    migrate('retained-sender-downgrade','downgrade','20261205_0156',
        '0159 immutable business history requires retention')
    assert snapshot(owner)==before and catalog(owner)==before_catalog
    with owner.connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='20261213_0164'
    security()
    result.update(emptyRoundtrip=True,retainedSenderSealsBlockDowngrade=True,
        runtimeSecurityBeforeAndAfter=True,formalMigrationRegistered=True,productionAcceptance=False)
    return result
