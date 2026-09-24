"""Physical return departures use genuine opening and inventory postings."""
from datetime import datetime,timezone,timedelta
from decimal import Decimal
from types import SimpleNamespace
from uuid import uuid4
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.foundation_models import Role,SourceSystem
from app.inventory_models import StockLocation,StockAccount,CustodyAssignment,InventorySerial,FormalMaterial
from app.formal_access import load_formal_principal
from app.formal_services import inventory_posting as posting, work_order_material as material
from test_opening_stocktake_service import _organization,_user_with_role
from work_order_fixtures import add_order
from app.formal_services.oam_work_order_projection import SOURCE_SYSTEM_CODE


def prepare_departure_worlds(api_engine, fixture_engine):
    """Fresh region prevents abandoned test stocktakes from blocking real opening."""
    from test_postgresql16_release_gate import _seed_0047_stocktake_inventory,_establish_multiround_stocktake_location
    with Session(fixture_engine) as db:
        headquarters=_organization(db,'PG16-RETURN-HQ-'+uuid4().hex,'Synthetic return HQ','headquarters')
        region=_organization(db,'PG16-RETURN-REGION-'+uuid4().hex,'Synthetic return region','region_company',parent=headquarters)
        roles={row.code:row for row in db.scalars(select(Role))}
        admin=_user_with_role(db,headquarters,roles['admin'],'national','*','Synthetic return reviewer').user.id
        manager=_user_with_role(db,region,roles['provincial_manager'],'organization',str(region.id),'Synthetic return manager').user.id
        technician=_user_with_role(db,region,roles['technician'],'person',None,'Synthetic return technician').user.id
        db.commit()
    reference=_seed_0047_stocktake_inventory(api_engine,actor_user_id=admin,assignee_user_id=manager,recipient_user_id=technician,prepare_only=True)
    with Session(fixture_engine) as db:
        actor=load_formal_principal(db,technician)
        at=datetime.now(timezone.utc)
        personal=StockLocation(id=uuid4(),code='PG16-RETURN-PERSONAL-'+uuid4().hex,name='Synthetic physical personal warehouse',
            location_type='personal',parent_id=reference['location_id'],owner_org_id=reference['region_org_id'],
            custodian_person_id=actor.person_id,status='active',created_at=at,updated_at=at)
        transit=StockLocation(id=uuid4(),code='PG16-RETURN-TRANSIT-'+uuid4().hex,name='Synthetic physical return transit',
            location_type='transit',parent_id=reference['location_id'],owner_org_id=reference['region_org_id'],status='active',created_at=at,updated_at=at)
        db.add_all((personal,transit));db.flush()
        db.add(CustodyAssignment(id=uuid4(),location_id=personal.id,custodian_person_id=actor.person_id,valid_from=at))
        accounts={kind:StockAccount(id=uuid4(),owner_org_id=reference['region_org_id'],location_id=personal.id,
            custodian_person_id=actor.person_id,material_id=identifier,condition_code='new',availability_bucket='available',lot_id=None,
            created_at=at,updated_at=at) for kind,identifier in [('quantity',reference['material_id']),('serial',reference['concurrency_material_id'])]}
        db.add_all(accounts.values());db.flush()
        # Prepare the receiving accounts before their real zero-opening. A
        # later inbound must not rely on a privileged post-opening row insert.
        manager_actor=load_formal_principal(db,manager)
        db.add_all(StockAccount(id=uuid4(),owner_org_id=reference['region_org_id'],location_id=reference['location_id'],
            custodian_person_id=manager_actor.person_id,material_id=identifier,condition_code='used',availability_bucket='available',
            lot_id=None,created_at=at,updated_at=at) for identifier in (reference['material_id'],reference['concurrency_material_id']))
        db.flush()
        ids={kind:account.id for kind,account in accounts.items()};personal_id,transit_id=personal.id,transit.id
        db.commit()
    for location,expected,count_user in [(personal_id,2,technician),(transit_id,0,manager),(reference['location_id'],3,manager)]:
        _establish_multiround_stocktake_location(api_engine,fixture={**reference,'recount_location_id':location},
            actor_user_id=admin,assignee_user_id=manager,count_user_id=count_user,expected_snapshot_line_count=expected)
    return seed_departure_worlds(api_engine, fixture_engine, reference=reference, admin_user_id=admin, technician=technician, ids=ids, transit_id=transit_id)


def seed_departure_worlds(api_engine, fixture_engine, *, reference, admin_user_id, technician, ids, transit_id):
    with Session(api_engine) as db:
        actor=load_formal_principal(db,admin_user_id)
        token=uuid4().hex
        posting.post_inventory_transaction(db,actor=actor,command=posting.InventoryPostingCommand(
            transaction_no='PG16-RETURN-SEED-'+token,movement_type='inbound',source_document_type='pg16_return_fixture',source_document_id=token,
            posting_key='pg16-return-fixture:'+token,effective_at=datetime.now(timezone.utc),movements=(
                posting.InventoryMovementCommand(from_account_id=None,to_account_id=ids['quantity'],quantity=Decimal(6),external_boundary_code='PG16_RELEASE_FIXTURE'),
                posting.InventoryMovementCommand(from_account_id=None,to_account_id=ids['serial'],quantity=Decimal(1),serial_ids=(reference['concurrency_serial_id'],),external_boundary_code='PG16_RELEASE_FIXTURE'),)),
            idempotency_key=token,request_id=uuid4().hex)
        db.commit()
    return collect_departure_worlds(fixture_engine, reference=reference, technician=technician, ids=ids, transit_id=transit_id)


def collect_departure_worlds(fixture_engine, *, reference, technician, ids, transit_id):
    worlds={}
    with Session(fixture_engine) as db:
        source=db.scalar(select(SourceSystem).where(SourceSystem.code==SOURCE_SYSTEM_CODE))
        assert source is not None
        for kind,account_id in ids.items():
            account=db.get(StockAccount,account_id);sku=db.get(FormalMaterial,account.material_id)
            serials=(db.get(InventorySerial,reference['concurrency_serial_id']),) if kind=='serial' else ()
            context=SimpleNamespace(person=SimpleNamespace(id=account.custodian_person_id),organization=SimpleNamespace(id=account.owner_org_id))
            orders=tuple(add_order(db,context,source).id for _ in range(2))
            line=material.WorkOrderMaterialLineInput(account.material_id,account.id,Decimal(1),tuple(sn.id for sn in serials),account.condition_code,
                tuple(material.SerialVerificationInput(sn.id,sku.sku_code,sn.serial_no,sn.qr_code) for sn in serials))
            worlds[kind]=dict(world=(account_id,technician,orders,line),target_id=reference['location_id'],transit_id=transit_id)
        db.commit()
    print('PG16 returns: fresh quantity/SN personal stock and transit have real zero opening; stock seeded through unified posting PASS',flush=True)
    return worlds


def assert_departure_world(api_engine, prepared, kind):
    from contextlib import nullcontext
    from unittest.mock import patch
    from sqlalchemy import text,event
    from sqlalchemy.exc import DBAPIError
    from uuid import UUID
    from app.demand_models import WorkOrderReplacement,WorkOrderMaterialLine,WorkOrderMaterialSerial
    from app.stock_operation_models import StockOperationLine,StockOperationOutbound
    from app.stock_return_schemas import StockReturnPreviewIn,StockReturnSubmitIn,StockReturnCancelIn
    from app.stock_return_outbound_schemas import StockReturnOutboundPreviewIn,StockReturnOutboundSubmitIn
    from app.formal_services import stock_return_commands as returns,stock_return_outbound_commands as departures,stock_return_outbound_facts as facts,stock_return_recovery as recovery
    from app.formal_services.stock_return_plan import preview_return
    from app.formal_services.stock_return_outbound_plan import preview_outbound
    from app.formal_services.inventory_query import InventoryReadError
    from pg16_work_order_reversal_submit_gate import _original
    from pg16_work_order_material_gate import _checkpoint
    from pg16_stock_return_recovery_gate import snapshot as earlier_snapshot
    snapshot=departure_snapshot
    world=prepared['world'];_account,user_id,orders,_line=world
    baseline=snapshot(api_engine)
    with Session(api_engine) as db:
        original=_original(db,world,'replace')
        parent=db.get(WorkOrderReplacement,UUID(original['original_replacement_id']))
        origin=db.scalars(select(WorkOrderMaterialLine).where(WorkOrderMaterialLine.operation_id==parent.recover_operation_id)).one()
        account=db.get(StockAccount,origin.stock_account_id);sku=db.get(FormalMaterial,account.material_id)
        serials=tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(select(WorkOrderMaterialSerial.serial_id).where(WorkOrderMaterialSerial.operation_line_id==origin.id)))))
        proofs=[dict(serial_id=sn.id,sku_code=sku.sku_code,serial_no=sn.serial_no,qr_code=sn.qr_code) for sn in serials]
        actor=load_formal_principal(db,user_id)
        request=StockReturnPreviewIn(operator_person_id=actor.person_id,target_location_id=prepared['target_id'],transit_location_id=prepared['transit_id'],
            reason='Synthetic return before physical departure',lines=[dict(source_recovery_line_id=origin.id,stock_account_id=account.id,quantity='1',serial_verifications=proofs)])
        checked,_=preview_return(db,actor=actor,work_order_id=orders[0],request=request)
        submitted=returns.submit_return(db,actor=actor,work_order_id=orders[0],request=StockReturnSubmitIn(**request.model_dump(),
            expected_plan_hash=checked.plan_hash,idempotency_key=uuid4().hex,request_id=uuid4().hex));_checkpoint(db)
        line=db.scalars(select(StockOperationLine).where(StockOperationLine.operation_id==submitted.operation_id)).one()
        request=StockReturnOutboundPreviewIn(operator_person_id=actor.person_id,outbound_at=datetime.now(timezone.utc),reason='Synthetic exact physical departure',
            lines=[dict(operation_line_id=line.id,quantity='1' if kind=='serial' else '0.375',serial_verifications=proofs)])
        checked,_=preview_outbound(db,actor=actor,work_order_id=orders[0],operation_id=submitted.operation_id,request=request)
        value=StockReturnOutboundSubmitIn(**request.model_dump(),expected_plan_hash=checked.plan_hash,idempotency_key=uuid4().hex,request_id=uuid4().hex)
        local=SimpleNamespace(connect=lambda:nullcontext(db.connection()))
        coordinates=dict(actor=actor,work_order_id=orders[0],operation_id=submitted.operation_id,operation_type='outbound_return',request_id=value.request_id)
        def execute(): return departures.execute_outbound(db,actor=actor,work_order_id=orders[0],operation_id=submitted.operation_id,request=value)
        def read():
            statements=[]
            def capture(_c,_cu,statement,_p,_ctx,_m):statements.append(statement)
            connection=db.connection();event.listen(connection,'before_cursor_execute',capture)
            try:result=recovery.lookup_return_request(db,**coordinates)
            finally:event.remove(connection,'before_cursor_execute',capture)
            assert statements and all(statement.lstrip().upper().startswith('SELECT') for statement in statements)
            return result
        before=snapshot(local)
        assert read() is None and snapshot(local)==before
        with db.begin_nested() as boundary:
            seal=recovery.seal_return_request(db,**coordinates,request_hash=checked.request_hash);_checkpoint(db)
            assert read()==seal
            sealed=snapshot(local)
            try:execute()
            except InventoryReadError as exc:assert exc.code=='stock_return_request_sealed'
            else:raise AssertionError('Service executed a sealed physical departure')
            try:
                with db.begin_nested(),patch.object(returns,'_fresh_request',return_value=None),patch.object(facts,'outbound_result',return_value=None):
                    execute();_checkpoint(db)
            except DBAPIError as exc:assert '0101 sealed return request cannot execute' in str(exc.orig)
            else:raise AssertionError('SQL executed a sealed physical departure')
            db.expire_all();assert snapshot(local)==sealed
            boundary.rollback()
        db.expire_all();assert snapshot(local)==before
        try:
            with db.begin_nested(),patch.object(departures,'_record',return_value=None),patch.object(facts,'outbound_result',return_value=None):
                execute();_checkpoint(db)
        except DBAPIError as exc:assert '0103 complete departure audit, state and outbox required' in str(exc.orig)
        else:raise AssertionError('SQL accepted a departure without domain evidence')
        db.expire_all();assert snapshot(local)==before
        result=execute();_checkpoint(db)
        assert read()==result and execute()==result
        _checkpoint(db);posted=snapshot(local)
        assert recovery.seal_return_request(db,**coordinates,request_hash=checked.request_hash)==result
        try:
            with db.begin_nested(),patch.object(recovery,'lookup_return_request',return_value=None):
                recovery.seal_return_request(db,**coordinates,request_hash=checked.request_hash);_checkpoint(db)
        except DBAPIError as exc:assert '0101 executed return request cannot be sealed' in str(exc.orig)
        else:raise AssertionError('SQL sealed an executed departure')
        db.expire_all();assert snapshot(local)==posted
        try:
            returns.cancel_return(db,actor=actor,operation_id=submitted.operation_id,request=StockReturnCancelIn(operator_person_id=actor.person_id,
                reason='Already departed cannot cancel',idempotency_key=uuid4().hex,request_id=uuid4().hex))
        except InventoryReadError as exc:assert exc.code=='stock_return_already_outbound'
        else:raise AssertionError('Return was cancelled after physical departure')
        if kind=='quantity':
            remaining=request.model_copy(update={'lines':(request.lines[0].model_copy(update={'quantity':Decimal('0.625')}),)})
            checked_rest,_=preview_outbound(db,actor=actor,work_order_id=orders[0],operation_id=submitted.operation_id,request=remaining)
            assert checked_rest.lines[0].departed_quantity=='0.375' and checked_rest.lines[0].remaining_quantity=='0.625'
            remainder=departures.execute_outbound(db,actor=actor,work_order_id=orders[0],operation_id=submitted.operation_id,
                request=StockReturnOutboundSubmitIn(**remaining.model_dump(),expected_plan_hash=checked_rest.plan_hash,idempotency_key=uuid4().hex,request_id=uuid4().hex))
            _checkpoint(db);assert remainder.posting_transaction_id!=result.posting_transaction_id
        db.rollback()
    assert snapshot(api_engine)==baseline
    print(f'PG16 {kind} physical departure, exact replay, partial budget, SELECT-only lookup, SQL seal/audit mutual exclusion and full rollback PASS',flush=True)


def assert_stock_return_outbound_gate(api_engine, fixture_engine, *, worlds=None):
    if worlds is None:
        worlds=prepare_departure_worlds(api_engine,fixture_engine)
    for kind,prepared in worlds.items(): assert_departure_world(api_engine,prepared,kind)
    from pg16_stock_return_outbound_mini_gate import assert_departure_mini_gate
    assert_departure_mini_gate(api_engine,fixture_engine,worlds)
    from pg16_stock_return_outbound_concurrency_gate import assert_departure_commit_gate
    assert_departure_commit_gate(api_engine,fixture_engine,worlds)
    from pg16_stock_return_outbound_queries_gate import assert_departure_queries_gate
    assert_departure_queries_gate(api_engine,fixture_engine,worlds)
    return worlds


def departure_snapshot(engine):
    from sqlalchemy import text
    from pg16_stock_return_recovery_gate import snapshot as previous_snapshot
    with engine.connect() as connection:
        facts=tuple(tuple(connection.execute(text(f'SELECT * FROM {table} ORDER BY id'))) for table in (
            'stock_operation_outbounds','stock_operation_outbound_lines','stock_operation_outbound_serials'))
    return previous_snapshot(engine),facts
