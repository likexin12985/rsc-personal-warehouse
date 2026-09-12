"""Real API-role identity admission and original-order inventory boundaries."""
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext
from dataclasses import replace
from decimal import Decimal
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.database import get_db
from app.demand_models import WorkOrderRemovedSerialRegistration as Registration
from app.formal_access import load_formal_principal
from app.formal_services import work_order_removed_registration as registration
from app.formal_services import work_order_removed_origin as origin
from app.formal_services import work_order_replacements as paired, work_order_replacement_preview as preview
from app.formal_services import work_order_material as material
from app.foundation_models import AuditEvent
from app.inventory_models import InventoryMovement, InventorySerial, QrCode, SerialCurrentPosition, StockAccount, FormalMaterial, InventoryLot
from app.routers import formal_work_order_material as router
from app.work_order_material_schemas import WorkOrderRemovedScanIn
from pg16_work_order_material_gate import _checkpoint
from pg16_work_order_replacements_gate import replacement_snapshot
from pg16_work_order_query_gate import query_worlds


def snapshot(engine):
    with engine.connect() as db:
        identities=tuple(db.execute(text(f"SELECT * FROM {table} ORDER BY id")).all()
            for table in ("work_order_removed_serial_registrations","qr_codes"))
    return replacement_snapshot(engine),identities


def case(db,world):
    _,user_id,orders,line=world
    actor=load_formal_principal(db,user_id)
    occupied,_=material.execute_occupy_operation(db,actor=actor,work_order_id=orders[0],lines=(line,),
        idempotency_key=uuid4().hex,request_id=uuid4().hex)
    _checkpoint(db)
    basis=db.scalar(select(InventoryMovement.to_account_id).where(InventoryMovement.transaction_id==occupied.posting_transaction_id))
    account=db.get(StockAccount,basis);sku=db.get(FormalMaterial,line.material_id)
    lot=db.get(InventoryLot,account.lot_id) if account.lot_id else None
    scan=WorkOrderRemovedScanIn(operator_person_id=actor.person_id,basis_stock_account_id=basis,sku_code=sku.sku_code,
        lot_no=lot.lot_no if lot else None,condition_before="damaged",serial_no="PG16-REMOVED-"+uuid4().hex,qr_code="PG16-QR-"+uuid4().hex)
    args=dict(actor=actor,work_order_id=orders[0],scan=scan,idempotency_key=uuid4().hex,request_id=uuid4().hex)
    return args,replace(line,stock_account_id=basis)


def sealing(args):
    return {key:args[key] for key in ("actor","work_order_id","request_id")} | {
        "request_hash":paired._hash(registration.command_payload(args["work_order_id"],args["scan"]))}


def recovered(db,args,result):
    account=db.get(StockAccount,args["scan"].basis_stock_account_id)
    return paired.RecoveryLineInput(account.id,result.material_id,Decimal(1),"damaged",lot_id=result.lot_id,
        serial_ids=(result.serial_id,),serial_verifications=(material.SerialVerificationInput(result.serial_id,
            args["scan"].sku_code,args["scan"].serial_no,args["scan"].qr_code),))


def app_for(db,user_id):
    app=FastAPI();app.include_router(router.router,prefix="/api")
    app.dependency_overrides[get_db]=lambda:db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name=="principal":app.dependency_overrides[dependency.call]=lambda:load_formal_principal(db,user_id)
    return app


def _counts(db):
    return tuple(db.scalar(select(func.count()).select_from(model)) for model in (InventorySerial,QrCode,Registration,AuditEvent))


def stock_facts(db):
    tables={'inventory_transactions':'id','inventory_movements':'id','stock_accounts':'id','stock_balances':'stock_account_id',
        'serial_current_positions':'serial_id','outbox_events':'id','inventory_ledger_heads':'stream_key',
        'work_order_material_operations':'id','work_order_replacements':'id'}
    return tuple(db.execute(text(f'SELECT * FROM {table} ORDER BY {key}')).all() for table,key in tables.items())


def assert_removed_registration_atomic_gate(api_engine,fixture_engine):
    world=query_worlds(fixture_engine)["serial"]
    # Seed only an empty recovery dimension, so an unpaired write reaches the
    # new serial-origin constraint instead of the earlier new-account guard.
    with Session(fixture_engine) as db:
        source=db.get(StockAccount,world[0])
        dimensions={key:getattr(source,key) for key in ('owner_org_id','custodian_person_id','location_id','material_id','lot_id')}
        dimensions.update(condition_code='damaged',availability_bucket='available')
        if db.scalar(select(StockAccount).filter_by(**dimensions)) is None:
            db.add(StockAccount(id=uuid4(),**dimensions));db.commit()
    baseline=snapshot(api_engine)
    with Session(api_engine) as db:
        args,line=case(db,world);local=SimpleNamespace(connect=lambda:nullcontext(db.connection()))
        before=snapshot(local);counts=_counts(db);stock_before=stock_facts(db)
        path=f'/api/v1/work-orders/{args["work_order_id"]}/material-replacements/removed-registrations'
        body={**args["scan"].model_dump(mode="json"),"idempotency_key":args["idempotency_key"],"request_id":args["request_id"]}
        with TestClient(app_for(db,world[1])) as client,patch.object(db,"commit",side_effect=lambda:_checkpoint(db)):
            prepared=client.post(path+'/preview',json=args["scan"].model_dump(mode="json"))
            assert prepared.status_code==200,prepared.text
            assert prepared.headers['cache-control']=='private, no-store'
            assert snapshot(local)==before
            response=client.post(path,json=body,headers={'X-Request-ID':args['request_id']})
            assert response.status_code==200,response.text
            assert response.json()['request_hash']==prepared.json()['request_hash'] and 'qr_code' not in response.text
            read=client.get(path+'/by-request/'+args['request_id'])
            assert read.status_code==200, read.text
            assert read.json()==response.json(), (read.json(), response.json())
            assert read.headers['cache-control']=='private, no-store'
        result=registration.register_removed_serial(db,**args);_checkpoint(db)
        assert _counts(db)==tuple(value+1 for value in counts)
        assert db.get(SerialCurrentPosition,result.serial_id) is None
        assert db.get(InventorySerial,result.serial_id).lifecycle_status=='active'
        assert stock_facts(db)==stock_before
        resolved=preview.lookup_removed_part(db,actor=args['actor'],work_order_id=args['work_order_id'],scan=args['scan'])
        assert resolved.serial_id==result.serial_id
        recovery=recovered(db,args,result)
        parent=paired.execute_replacement(db,actor=args['actor'],work_order_id=args['work_order_id'],consume_lines=(line,),
            recover_lines=(recovery,),pairs=(material.WorkOrderReplacementPairInput(line.serial_ids[0],result.serial_id),),
            idempotency_key=uuid4().hex,request_id=uuid4().hex)
        _checkpoint(db)
        position=db.get(SerialCurrentPosition,result.serial_id)
        assert db.get(StockAccount,position.stock_account_id).condition_code=='damaged'
        assert parent.consume_operation_id!=parent.recover_operation_id
        db.rollback()
    assert snapshot(api_engine)==baseline
    print('PG16 registration preview/HTTP original proof and exact first paired recovery with full rollback PASS',flush=True)

    for scenario in ('unpaired','audit_missing','seal_first','register_first','duplicate_sn','duplicate_qr'):
        try:
            with Session(api_engine) as db:
                args,line=case(db,world)
                if scenario=='seal_first':registration.seal_registration(db,**sealing(args))
                if scenario=='audit_missing':
                    with patch.object(registration,'append_audit_event',lambda *a,**k:None),patch.object(registration,'verified_registration',lambda *a,**k:None):
                        registration.register_removed_serial(db,**args)
                else:
                    with patch.object(registration,'lookup_registration',lambda *a,**k:None):
                        result=registration.register_removed_serial(db,**args)
                if scenario=='register_first':
                    with patch.object(registration,'lookup_registration',lambda *a,**k:None):registration.seal_registration(db,**sealing(args))
                elif scenario=='unpaired':
                    _checkpoint(db)
                    lines=paired.resolve_recovery_lines(db,operator_person_id=args['actor'].person_id,lines=(recovered(db,args,result),),create=True)
                    with patch.object(origin,'require_registration_posting',lambda *a,**k:None):
                        material.execute_recover_operation(db,actor=args['actor'],work_order_id=args['work_order_id'],lines=lines,
                            idempotency_key=uuid4().hex,request_id=uuid4().hex)
                elif scenario.startswith('duplicate_'):
                    field='serial_no' if scenario=='duplicate_qr' else 'qr_code'
                    modified=args['scan'].model_copy(update={field:'NEW-'+uuid4().hex})
                    with patch.object(registration,'_available_identity',lambda *a,**k:None):
                        registration.register_removed_serial(db,**{**args,'scan':modified,'idempotency_key':uuid4().hex,'request_id':uuid4().hex})
                _checkpoint(db)
        except DBAPIError as exc:
            state=getattr(exc.orig,'sqlstate',None)
            assert state==('23505' if scenario.startswith('duplicate_') else '23514'),(scenario,state,str(exc.orig))
            if not scenario.startswith('duplicate_'):assert '0096' in str(exc.orig),(scenario,str(exc.orig))
        else:raise AssertionError('Database accepted '+scenario)
        assert snapshot(api_engine)==baseline
    print('PG16 bypassed application checks: unpaired inbound, missing audit, both seal orders and duplicate identities rejected PASS',flush=True)

    with Session(api_engine) as db:
        for table in ('inventory_serials','qr_codes'):
            assert not db.scalar(text('SELECT has_table_privilege(current_user,:table,\'INSERT\')'),{'table':table})
        for name in ('rsc_create_removed_identity_0096','rsc_check_removed_registration_0096','rsc_check_removed_origin_0096','rsc_check_removed_seal_0096'):
            assert not db.scalar(text('SELECT has_function_privilege(current_user,:name,\'EXECUTE\')'),{'name':name+'()'})
        args,line=case(db,world);registration.register_removed_serial(db,**args);_checkpoint(db)
        def change_master(table,key,identifier):
            try:
                with fixture_engine.begin() as connection:
                    connection.execute(text("SET LOCAL lock_timeout='250ms'"))
                    connection.execute(text(f'UPDATE public.{table} SET {key}={key} WHERE {key}=:id'),{'id':identifier})
                return 'updated'
            except DBAPIError as exc:return getattr(exc.orig,'sqlstate',None)
        with ThreadPoolExecutor(max_workers=1) as worker:
            assert worker.submit(change_master,'materials','id',line.material_id).result(timeout=5)=='55P03'
            assert worker.submit(change_master,'stock_locations','id',db.get(StockAccount,line.stock_account_id).location_id).result(timeout=5)=='55P03'
        db.rollback()
    assert snapshot(api_engine)==baseline
    print('PG16 no direct master insert/internal execute; concurrent SKU and custody mutation blocked PASS',flush=True)


def assert_removed_registration_commit_gate(api_engine,fixture_engine):
    world=query_worlds(fixture_engine)['serial']
    for outcome in ('registered','sealed'):
        with Session(api_engine) as db:
            args,line=case(db,world);db.commit()
        with Session(api_engine) as db:before_stock=stock_facts(db);before_counts=_counts(db)
        started=Event();identity={}
        def late_register():
            with Session(api_engine) as db:
                db.execute(text("SET LOCAL statement_timeout='10s'"))
                actor=load_formal_principal(db,world[1]);identity['pid']=db.scalar(text('SELECT pg_backend_pid()'));started.set()
                try:
                    result=registration.register_removed_serial(db,**{**args,'actor':actor})
                    db.commit();return result.status
                except material.InventoryPostingError as exc:return exc.code
        with Session(api_engine) as db,ThreadPoolExecutor(max_workers=1) as worker:
            current=load_formal_principal(db,world[1])
            original=(registration.register_removed_serial(db,**{**args,'actor':current}) if outcome=='registered'
                else registration.seal_registration(db,**{**sealing(args),'actor':current}))
            _checkpoint(db);future=worker.submit(late_register);assert started.wait(timeout=5)
            blocked=False;until=monotonic()+3
            with fixture_engine.connect() as connection:
                while monotonic()<until:
                    blocked=bool(connection.scalar(text('SELECT cardinality(pg_blocking_pids(:pid))>0'),identity))
                    if blocked:break
                    sleep(.02)
            assert blocked,'late registration did not reach the held inventory lock'
            db.commit()
            assert future.result(timeout=12)==('registered' if outcome=='registered' else 'work_order_request_sealed')
        with Session(api_engine) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            path=f'/api/v1/work-orders/{args["work_order_id"]}/material-replacements/removed-registrations/by-request/{args["request_id"]}'
            with TestClient(app_for(db,world[1])) as client:
                read=client.get(path);assert read.status_code==200,read.text
                assert read.json()==original.model_dump(mode='json')
                assert read.headers['cache-control']=='private, no-store' and 'qr_code' not in read.text
            db.rollback()
        with Session(api_engine) as db:
            assert stock_facts(db)==before_stock
            delta=(1,1,1,1) if outcome=='registered' else (0,0,0,1)
            assert _counts(db)==tuple(value+added for value,added in zip(before_counts,delta))
        table='work_order_removed_serial_registrations' if outcome=='registered' else 'work_order_command_seals'
        identifier=original.registration_id if outcome=='registered' else original.seal.seal_id
        for engine in (api_engine,fixture_engine):
            for sql in (f'UPDATE {table} SET request_hash=request_hash WHERE id=:id',f'DELETE FROM {table} WHERE id=:id',f'TRUNCATE {table}'):
                try:
                    with engine.begin() as connection:connection.execute(text(sql),{'id':identifier})
                except DBAPIError as exc:assert getattr(exc.orig,'sqlstate',None) in {'42501','55000'}
                else:raise AssertionError('Original registration/seal was mutable')
        # Return the synthetic reservation through its original formal release.
        with Session(api_engine) as db:
            actor=load_formal_principal(db,world[1])
            material.execute_release_operation(db,actor=actor,work_order_id=args['work_order_id'],
                lines=(replace(line,target_stock_account_id=world[0]),),idempotency_key=uuid4().hex,request_id=uuid4().hex)
            _checkpoint(db);db.commit()
        print(f'PG16 durable {outcome}, concurrent late request, new-session read-only HTTP and immutable history PASS',flush=True)


def assert_removed_registration_lot_gate(api_engine,fixture_engine):
    from app.foundation_models import SourceSystem
    from test_inventory_posting import make_material
    world=query_worlds(fixture_engine)['serial']
    with Session(fixture_engine,expire_on_commit=False) as db:
        source=db.scalar(select(SourceSystem).where(SourceSystem.code=='starcharge_oam'))
        sku=make_material(db,source,tracking_mode='lot_and_serial',quantity_scale=0,allow_fraction=False)
        lot=InventoryLot(id=uuid4(),material_id=sku.id,lot_no='PG16-REMOVED-LOT-'+uuid4().hex)
        db.add(lot);db.commit()
    baseline=snapshot(api_engine)
    with Session(api_engine) as db:
        args,line=case(db,world)
        args['scan']=args['scan'].model_copy(update={'sku_code':sku.sku_code,'lot_no':lot.lot_no})
        stock_before=stock_facts(db)
        result=registration.register_removed_serial(db,**args);_checkpoint(db)
        assert result.lot_id==lot.id and result.material_id==sku.id and stock_facts(db)==stock_before
        with ThreadPoolExecutor(max_workers=1) as worker:
            def mutate_lot():
                try:
                    with fixture_engine.begin() as connection:
                        connection.execute(text("SET LOCAL lock_timeout='250ms'"))
                        connection.execute(text('UPDATE inventory_lots SET lot_no=lot_no WHERE id=:id'),{'id':lot.id})
                except DBAPIError as exc:return getattr(exc.orig,'sqlstate',None)
                return 'updated'
            assert worker.submit(mutate_lot).result(timeout=5)=='55P03'
        paired.execute_replacement(db,actor=args['actor'],work_order_id=args['work_order_id'],consume_lines=(line,),
            recover_lines=(recovered(db,args,result),),pairs=(material.WorkOrderReplacementPairInput(line.serial_ids[0],result.serial_id),),
            idempotency_key=uuid4().hex,request_id=uuid4().hex)
        _checkpoint(db)
        position=db.get(SerialCurrentPosition,result.serial_id);account=db.get(StockAccount,position.stock_account_id)
        assert account.material_id==sku.id and account.lot_id==lot.id and account.condition_code=='damaged'
        db.rollback()
    assert snapshot(api_engine)==baseline
    print('PG16 exact lot+SN registration, concurrent lot lock and different-SKU paired recovery fully rolled back PASS',flush=True)
