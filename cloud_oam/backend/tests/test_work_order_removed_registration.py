"""Unknown removed identities never become balance or fake consumption facts."""
from decimal import Decimal
from uuid import uuid4
import pytest
from pydantic import ValidationError
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select, func, text
from app.database import get_db
from app.demand_models import WorkOrderRemovedSerialRegistration, WorkOrderMaterialOperation
from app.foundation_models import AuditEvent, OutboxEvent
from app.inventory_models import InventorySerial, QrCode, StockBalance, SerialCurrentPosition, InventoryTransaction, InventoryLedgerHead, StockAccount
from app.work_order_material_schemas import WorkOrderRemovedScanIn
from app.formal_services import work_order_removed_registration as registration
from app.formal_services import work_order_replacement_preview as preview, work_order_replacements as paired, work_order_material as material
from app.formal_services.inventory_posting import InventoryPostingError
from app.routers import formal_work_order_material as router
from test_work_order_material_options import db, world, stock
pytestmark=pytest.mark.parametrize("stock",["serial"],indirect=True)


def scan(stock, **changes):
    return WorkOrderRemovedScanIn(**{**dict(operator_person_id=stock.actor.person_id,basis_stock_account_id=stock.reserved.id,
        sku_code=stock.world.material.sku_code,condition_before="damaged",serial_no="REMOVED-NEW-IDENTITY",qr_code="PHYSICAL-REMOVED-QR"),**changes})


def create(db,stock,*,value=None,key=None,trace=None):
    return registration.register_removed_serial(db,actor=stock.world.current_principal,work_order_id=stock.orders[0].id,
        scan=value or scan(stock),idempotency_key=key or uuid4().hex,request_id=trace or uuid4().hex)


def counts(db):
    return tuple(db.scalar(select(func.count()).select_from(model)) for model in (InventorySerial,QrCode,WorkOrderRemovedSerialRegistration,
        AuditEvent,InventoryTransaction,WorkOrderMaterialOperation,OutboxEvent,StockAccount,SerialCurrentPosition))


def inventory(db):
    return (tuple(db.execute(select(StockBalance.stock_account_id,StockBalance.quantity,StockBalance.version).order_by(StockBalance.stock_account_id))),
            tuple(db.scalars(select(InventoryLedgerHead.next_cursor))))


def test_preview_and_registration_admit_only_identity_and_original_idempotency_proof(db,stock):
    baseline=counts(db),inventory(db)
    db.execute(text("PRAGMA query_only=ON"))
    prepared=registration.preview_registration(db,actor=stock.actor,work_order_id=stock.orders[0].id,scan=scan(stock))
    assert prepared.status=="registration_validated" and prepared.serial_id is None
    assert prepared.request_hash==paired._hash(registration.command_payload(stock.orders[0].id,scan(stock)))
    assert "PHYSICAL-REMOVED-QR" not in prepared.model_dump_json() and "qr_code" not in prepared.model_dump_json()
    assert (counts(db),inventory(db))==baseline
    db.execute(text("PRAGMA query_only=OFF"))
    trace,key=uuid4().hex,uuid4().hex
    result=create(db,stock,key=key,trace=trace);db.commit()
    assert result.status=="registered" and result.request_hash==prepared.request_hash
    after=counts(db)
    assert after[:4]==tuple(n+1 for n in baseline[0][:4]) and after[4:]==baseline[0][4:]
    assert inventory(db)==baseline[1]
    assert db.get(InventorySerial,result.serial_id).lifecycle_status=="active"
    assert db.get(SerialCurrentPosition,result.serial_id) is None
    assert registration.lookup_registration(db,actor=stock.actor,work_order_id=stock.orders[0].id,request_id=trace)==result
    assert create(db,stock,key=key,trace=trace)==result
    db.commit();assert counts(db)==after
    with pytest.raises(InventoryPostingError):create(db,stock,key=key,trace=trace,value=scan(stock,serial_no="DIFFERENT"))


@pytest.mark.parametrize("change",["same_sn","same_qr","qr_object","missing_code","bad_utf8","bad_lot","other_operator","other_basis"])
def test_invalid_or_conflicting_registration_never_creates_identities(db,stock,change):
    value=scan(stock)
    if change=="same_sn":value=scan(stock,serial_no=stock.serials[0].serial_no)
    elif change=="same_qr":value=scan(stock,qr_code=stock.serials[0].qr_code)
    elif change=="qr_object":
        db.add(QrCode(id=uuid4(),code=value.qr_code,object_type="location",object_id=stock.account.location_id,status="active"));db.commit()
    elif change=="missing_code":value=scan(stock,serial_no=None)
    elif change=="bad_utf8":
        before=counts(db),inventory(db)
        with pytest.raises(ValidationError):scan(stock,qr_code="bad\ud800")
        assert (counts(db),inventory(db))==before
        return
    elif change=="bad_lot":value=scan(stock,lot_no="invented")
    elif change=="other_operator":value=scan(stock,operator_person_id=uuid4())
    else:value=scan(stock,basis_stock_account_id=stock.account.id)
    before=counts(db),inventory(db)
    with pytest.raises(InventoryPostingError):create(db,stock,value=value)
    db.rollback();assert (counts(db),inventory(db))==before


def replacement_input(stock,registered):
    consumed=stock.line("1",stock.serials[1:2],identifier=stock.reserved.id)
    recovered=paired.RecoveryLineInput(stock.reserved.id,stock.world.material.id,Decimal(1),"damaged",
        serial_ids=(registered.serial_id,),serial_verifications=(material.SerialVerificationInput(registered.serial_id,
            stock.world.material.sku_code,scan(stock).serial_no,scan(stock).qr_code),))
    return dict(consume_lines=(consumed,),recover_lines=(recovered,),pairs=(material.WorkOrderReplacementPairInput(consumed.serial_ids[0],registered.serial_id),))


def test_registered_unknown_serial_can_first_enter_only_its_exact_paired_recovery(db,stock):
    registered=create(db,stock);db.commit()
    resolved=preview.lookup_removed_part(db,actor=stock.actor,work_order_id=stock.orders[0].id,scan=scan(stock))
    assert resolved.serial_id==registered.serial_id
    with pytest.raises(InventoryPostingError) as exc:preview.lookup_removed_part(db,actor=stock.actor,work_order_id=stock.orders[1].id,scan=scan(stock))
    assert exc.value.code=="removed_registration_scope_mismatch"
    args=replacement_input(stock,registered)
    preview.preview_replacement(db,actor=stock.actor,work_order_id=stock.orders[0].id,**args)
    result=paired.execute_replacement(db,actor=stock.actor,work_order_id=stock.orders[0].id,**args,idempotency_key=uuid4().hex,request_id=uuid4().hex)
    db.commit()
    position=db.get(SerialCurrentPosition,registered.serial_id)
    assert position is not None and position.stock_account_id is not None
    account=db.get(StockAccount,position.stock_account_id)
    assert account.condition_code=="damaged" and account.custodian_person_id==stock.actor.person_id
    assert result.consume_operation_id!=result.recover_operation_id
    assert registration.lookup_registration(db,actor=stock.actor,work_order_id=stock.orders[0].id,request_id=registered.request_id)==registered


def test_registered_identity_cannot_enter_by_unpaired_recovery(db,stock):
    registered=create(db,stock);db.commit();before=inventory(db)
    resolved=paired.resolve_recovery_lines(db,operator_person_id=stock.actor.person_id,lines=replacement_input(stock,registered)["recover_lines"],create=True)
    with pytest.raises(InventoryPostingError) as exc:
        material.execute_recover_operation(db,actor=stock.actor,work_order_id=stock.orders[0].id,lines=resolved,idempotency_key=uuid4().hex,request_id=uuid4().hex)
    assert exc.value.code=="removed_registration_origin_invalid"
    db.rollback();assert inventory(db)==before


def test_first_origin_binding_does_not_prevent_a_later_verified_work_order_cycle(db, stock):
    from dataclasses import replace
    registered = create(db, stock); db.commit()
    args = replacement_input(stock, registered)
    paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[0].id, **args,
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    position = db.get(SerialCurrentPosition, registered.serial_id)
    held = db.get(StockAccount, position.stock_account_id)
    recovered = args["recover_lines"][0]
    reused = material.WorkOrderMaterialLineInput(held.material_id, held.id, Decimal(1),
        recovered.serial_ids, held.condition_code, recovered.serial_verifications)
    operation, _ = material.execute_occupy_operation(db, actor=stock.actor, work_order_id=stock.orders[1].id,
        lines=(reused,), idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    from app.inventory_models import InventoryMovement
    reserved = db.scalar(select(InventoryMovement.to_account_id).where(
        InventoryMovement.transaction_id == operation.posting_transaction_id))
    material.execute_consume_operation(db, actor=stock.actor, work_order_id=stock.orders[1].id,
        lines=(replace(reused, stock_account_id=reserved),), idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    # Only the first inbound uses the immutable registration's original order.
    # A later removed-part cycle still proves its current consume and new pair.
    resolved = preview.lookup_removed_part(db, actor=stock.actor, work_order_id=stock.orders[1].id, scan=scan(stock))
    assert resolved.serial_id == registered.serial_id
    consumed = stock.line("1", stock.serials[2:3], identifier=stock.reserved.id)
    paired.execute_replacement(db, actor=stock.actor, work_order_id=stock.orders[1].id,
        consume_lines=(consumed,), recover_lines=(recovered,),
        pairs=(material.WorkOrderReplacementPairInput(consumed.serial_ids[0], registered.serial_id),),
        idempotency_key=uuid4().hex, request_id=uuid4().hex)
    db.commit()
    assert db.get(SerialCurrentPosition, registered.serial_id, populate_existing=True).stock_account_id == held.id
    assert registration.lookup_registration(db, actor=stock.actor, work_order_id=stock.orders[0].id,
        request_id=registered.request_id) == registered


def test_failed_audit_rolls_back_both_identity_masters_and_registration(db,stock,monkeypatch):
    before=counts(db),inventory(db)
    def broken(*args,**kwargs):raise RuntimeError("synthetic audit failure")
    monkeypatch.setattr(registration,"append_audit_event",broken)
    with pytest.raises(RuntimeError):create(db,stock)
    db.rollback();assert (counts(db),inventory(db))==before


def test_registered_history_survives_reassignment_and_unexecuted_seal_never_admits_identity(db,stock):
    result=create(db,stock);db.commit()
    stock.orders[0].engineer_person_id=stock.world.headquarters_reviewer_person.id;stock.orders[0].status="closed";db.commit()
    assert registration.lookup_registration(db,actor=stock.actor,work_order_id=stock.orders[0].id,request_id=result.request_id)==result
    assert registration.seal_registration(db,actor=stock.actor,work_order_id=stock.orders[0].id,request_id=result.request_id,request_hash=result.request_hash)==result
    trace=uuid4().hex;value=scan(stock,serial_no="SECOND-SN",qr_code="SECOND-QR")
    digest=paired._hash(registration.command_payload(stock.orders[0].id,value));before=counts(db)
    sealed=registration.seal_registration(db,actor=stock.actor,work_order_id=stock.orders[0].id,request_id=trace,request_hash=digest);db.commit()
    assert sealed.lookup_status=="sealed_not_executed" and sealed.seal.operation_type=="register_removed"
    assert counts(db)[:3]==before[:3]
    with pytest.raises(InventoryPostingError) as exc:create(db,stock,trace=trace,value=value)
    assert exc.value.code=="work_order_request_sealed"
    assert registration.lookup_registration(db,actor=stock.actor,work_order_id=stock.orders[0].id,request_id=trace)==sealed


def test_http_requires_current_operator_and_returns_only_original_safe_proof(db,stock):
    app=FastAPI();app.include_router(router.router,prefix="/api");app.dependency_overrides[get_db]=lambda:db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name=="principal":app.dependency_overrides[dependency.call]=lambda:stock.actor
    path=f"/api/v1/work-orders/{stock.orders[0].id}/material-replacements/removed-registrations"
    body=scan(stock).model_dump(mode="json");trace=uuid4().hex
    with TestClient(app) as client:
        prepared=client.post(path+"/preview",json=body);assert prepared.status_code==200,prepared.text
        assert prepared.headers["cache-control"]=="private, no-store"
        assert client.post(path+"/preview",json={**body,"operator_person_id":str(uuid4())}).status_code==403
        request={**body,"idempotency_key":uuid4().hex,"request_id":trace}
        assert client.post(path,json=request,headers={"X-Request-ID":"different-original"}).status_code==400
        written=client.post(path,json=request,headers={"X-Request-ID":trace});assert written.status_code==200,written.text
        assert written.json()["status"]=="registered" and "qr_code" not in written.text
        original=client.get(path+"/by-request/"+trace);assert original.json()==written.json()
        assert original.headers["cache-control"]=="private, no-store"
        assert client.post(path+"/by-request/"+trace+"/seal",json={"operator_person_id":str(stock.actor.person_id),"request_hash":written.json()["request_hash"]}).json()==written.json()


@pytest.mark.parametrize('mode',['quantity','lot','serial','lot_and_serial'])
def test_only_existing_tracked_sku_and_exact_lot_can_be_registered(db,stock,mode):
    from app.inventory_models import InventoryLot
    from test_inventory_posting import make_material
    sku=make_material(db,stock.world.source,tracking_mode='none' if mode=='quantity' else mode,quantity_scale=0,allow_fraction=False)
    lot=InventoryLot(id=uuid4(),material_id=sku.id,lot_no='REMOVED-EXACT-LOT')
    db.add(lot);db.commit()
    value=scan(stock,sku_code=sku.sku_code,lot_no=lot.lot_no if mode in {'lot','lot_and_serial'} else None)
    before=counts(db),inventory(db)
    if mode in {'quantity','lot'}:
        with pytest.raises(InventoryPostingError) as exc:create(db,stock,value=value)
        assert exc.value.code=='removed_registration_policy_invalid'
        db.rollback();assert (counts(db),inventory(db))==before
    else:
        if mode=='lot_and_serial':
            for incorrect in (None,'UNREGISTERED-LOT'):
                with pytest.raises(InventoryPostingError):create(db,stock,value=value.model_copy(update={'lot_no':incorrect}))
                db.rollback()
        result=create(db,stock,value=value);db.commit()
        assert result.material_id==sku.id and result.lot_id==(lot.id if mode=='lot_and_serial' else None)
        assert inventory(db)==before[1]
        recovered=paired.RecoveryLineInput(stock.reserved.id,sku.id,Decimal(1),'used',lot_id=result.lot_id,
            serial_ids=(result.serial_id,),serial_verifications=(material.SerialVerificationInput(result.serial_id,sku.sku_code,value.serial_no,value.qr_code),))
        consumed=stock.line('1',stock.serials[1:2],identifier=stock.reserved.id)
        paired.execute_replacement(db,actor=stock.actor,work_order_id=stock.orders[0].id,consume_lines=(consumed,),recover_lines=(recovered,),
            pairs=(material.WorkOrderReplacementPairInput(consumed.serial_ids[0],result.serial_id),),idempotency_key=uuid4().hex,request_id=uuid4().hex)
        db.commit()
        position=db.get(SerialCurrentPosition,result.serial_id);account=db.get(StockAccount,position.stock_account_id)
        assert account.material_id==sku.id and account.lot_id==result.lot_id and account.condition_code=='used'


@pytest.mark.parametrize('change',['stale_order','inactive_sku','released_basis','frozen_basis'])
def test_register_rechecks_live_eligibility_after_preview(db,stock,change):
    from datetime import datetime,timedelta,timezone
    from test_inventory_posting import freeze_account_scope
    registration.preview_registration(db,actor=stock.actor,work_order_id=stock.orders[0].id,scan=scan(stock))
    if change=='stale_order':stock.orders[0].updated_at=datetime.now(timezone.utc)-timedelta(hours=1)
    elif change=='inactive_sku':stock.world.material.status='inactive'
    elif change=='released_basis':
        material.execute_release_operation(db,actor=stock.actor,work_order_id=stock.orders[0].id,
            lines=(stock.line('1',stock.serials[1:2],identifier=stock.reserved.id,target=stock.account.id),),idempotency_key=uuid4().hex,request_id=uuid4().hex)
    else:freeze_account_scope(db,stock.world,stock.reserved,freeze_mode='hard',scope_mode='filtered')
    db.commit();before=counts(db),inventory(db)
    with pytest.raises(InventoryPostingError):create(db,stock)
    db.rollback();assert (counts(db),inventory(db))==before


def test_commit_failure_is_unknown_and_does_not_publish_uncommitted_identity(db,stock,monkeypatch):
    from sqlalchemy.exc import SQLAlchemyError
    app=FastAPI();app.include_router(router.router,prefix='/api');app.dependency_overrides[get_db]=lambda:db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name=='principal':app.dependency_overrides[dependency.call]=lambda:stock.actor
    def fail_commit():raise SQLAlchemyError('synthetic commit failure')
    monkeypatch.setattr(db,'commit',fail_commit)
    trace=uuid4().hex;before=counts(db),inventory(db)
    path=f'/api/v1/work-orders/{stock.orders[0].id}/material-replacements/removed-registrations'
    with TestClient(app) as client:
        response=client.post(path,json={**scan(stock).model_dump(mode='json'),'idempotency_key':uuid4().hex,'request_id':trace})
        assert response.status_code==503 and response.json()['detail']['code']=='removed_registration_unavailable'
        assert 'synthetic' not in response.text and 'qr_code' not in response.text
        assert client.get(path+'/by-request/'+trace).status_code==404
    assert (counts(db),inventory(db))==before


def test_projection_failure_returns_stable_http_error_without_identity_write(db,stock):
    app=FastAPI();app.include_router(router.router,prefix='/api');app.dependency_overrides[get_db]=lambda:db
    for route in router.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name=='principal':app.dependency_overrides[dependency.call]=lambda:stock.actor
    db.get(StockBalance,stock.reserved.id).quantity+=Decimal(9);db.commit();before=counts(db)
    path=f'/api/v1/work-orders/{stock.orders[0].id}/material-replacements/removed-registrations'
    with TestClient(app) as client:
        for suffix in ('/preview',''):
            body=scan(stock).model_dump(mode='json')
            if not suffix:body.update(idempotency_key=uuid4().hex,request_id=uuid4().hex)
            response=client.post(path+suffix,json=body)
            assert response.status_code==503 and response.json()['detail']['code']=='inventory_projection_integrity_invalid'
    assert counts(db)==before
