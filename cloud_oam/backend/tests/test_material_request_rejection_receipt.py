"""Warehouse acceptance composition; formal database admission remains separate."""
from datetime import timedelta
from decimal import Decimal
from uuid import uuid4
import pytest
from sqlalchemy import event, select, func
from app.foundation_models import Permission, Role, RolePermission
from app.formal_access import load_formal_principal
from app.inventory_models import InventorySerial, InventoryTransaction, InventoryMovement, StockBalance, SerialCurrentPosition
from app.material_request_rejection_receipt_schema import receipts, serials, exceptions
from app.material_request_rejection_receipt_schemas import RejectionReceiptIn
from app.formal_services import material_request_rejection_receipt as service
from app.formal_services.material_request_query import MaterialRequestReadError
from test_material_request_rejection_receiving import world as receiving_case, progress_world, registration_world, receipt_world, receiving_world, outbound_world
from test_material_request_my_receipt import evidence
from test_material_request_draft_service import SECRET
pytest_plugins=('test_material_request_picking',)
KEY='warehouse-acceptance-original-0001'


@pytest.fixture
def world(receiving_case):
    db,actor,*_=receiving_case
    for table in (receipts,serials,exceptions): table.create(db.get_bind(),checkfirst=True)
    role=db.scalar(select(Role.id).where(Role.code=='admin'))
    permission=db.scalar(select(Permission).where(Permission.resource=='stock_operation',Permission.action=='receive_return',Permission.field_code==''))
    if permission is None:
        permission=Permission(id=uuid4(),resource='stock_operation',action='receive_return',field_code='');db.add(permission);db.flush()
    link=db.scalar(select(RolePermission).where(RolePermission.role_id==role,RolePermission.permission_id==permission.id))
    if link is None: db.add(RolePermission(role_id=role,permission_id=permission.id,effect='allow'))
    else: link.effect='allow'
    db.flush()
    return (db,load_formal_principal(db,actor.user_id),*receiving_case[2:])


def payload(world,**changes):
    db,actor,_,request,original,_,handed,_=world
    view=service.receiving.rejection_return_receiving_detail(db,actor=actor,return_id=original.return_id)
    proofs=[]
    for sn in view.serials:
        row=db.get(InventorySerial,sn.serial_id)
        proofs.append(dict(serial_id=sn.serial_id,sku_code=view.sku_code,serial_no=sn.serial_no,qr_code=row.qr_code))
    return RejectionReceiptIn(**(dict(expected_request_version=request.version,registration_request_hash=original.request_hash,
        handover_id=handed.event_id,handover_request_hash=handed.request_hash,custody_assignment_id=view.custody_assignment_id,
        received_at=handed.physical_at,observed_sku_code=view.sku_code,amounts=dict(accepted_qty=original.quantity,accepted_serial_verifications=proofs),reason='来源仓独立实物验收')|changes))


def create(world,value=None,key=KEY):
    db,actor,_,_,original,*_=world
    return service.record_rejection_receipt(db,actor=actor,return_id=original.return_id,payload=value or payload(world),
        idempotency_key=key,secret=SECRET,trace_request_id='trace-'+key)


def recover(world,**kwargs):
    db,actor,_,_,original,*_=world
    return service.rejection_receipt_command_status(db,actor=actor,return_id=original.return_id,**kwargs)


def stock(db):
    return (tuple(db.execute(select(StockBalance.stock_account_id,StockBalance.quantity,StockBalance.version).order_by(StockBalance.stock_account_id))),
        tuple(db.execute(select(SerialCurrentPosition.serial_id,SerialCurrentPosition.stock_account_id).order_by(SerialCurrentPosition.serial_id))),
        db.scalar(select(func.count()).select_from(InventoryTransaction)),db.scalar(select(func.count()).select_from(InventoryMovement)))


def test_acceptance_is_independent_from_inventory_and_exact_recovery(world):
    db,_,_,request,*_=world; before=stock(db);version=request.version
    result=create(world); assert not result.replayed and create(world).receipt_id==result.receipt_id
    assert stock(db)==before and request.version==version
    statements=[]
    def capture(_conn,_cursor,sql,*_): statements.append(sql)
    event.listen(db.bind,'before_cursor_execute',capture)
    try:
        a=recover(world,idempotency_key=KEY,secret=SECRET);b=recover(world,trace_request_id='trace-'+KEY)
    finally: event.remove(db.bind,'before_cursor_execute',capture)
    assert a==b and a.replayed and a.receipt_id==result.receipt_id
    assert all(sql.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in sql.upper() for sql in statements)
    with pytest.raises(MaterialRequestReadError): create(world,key=KEY+'-extra')
    with pytest.raises(MaterialRequestReadError): create(world,payload(world,reason='不同内容'))


def test_shortage_is_observation_then_actual_acceptance_can_follow(world):
    db,actor,_,_,original,*_=world
    file,_=evidence((db,actor),key='warehouse-shortage-evidence-0001')
    command=payload(world,observed_sku_code=None,amounts=dict(shortage_qty=original.quantity,shortage_serial_ids=original.serial_ids,
        exceptions=[dict(exception_type='shortage',description='本次尚未收到实物',evidence_file_id=file.id)]))
    short=create(world,command)
    accepted=create(world,key=KEY+'-received')
    assert short.amounts.accepted_qty==0 and accepted.amounts.accepted_qty==Decimal(original.quantity)


def test_damaged_acceptance_is_accepted_subset_without_stock_conversion(world):
    db,actor,*_=world; original=payload(world);data=original.model_dump()
    file,_=evidence((db,actor),key='warehouse-damage-evidence-0001')
    data['amounts'].update(damaged_qty=data['amounts']['accepted_qty'],damaged_serial_ids=tuple(p['serial_id'] for p in data['amounts']['accepted_serial_verifications']),
        exceptions=[dict(exception_type='damaged',description='已接收实物破损，待独立坏件入账',evidence_file_id=file.id)])
    before=stock(db); result=create(world,RejectionReceiptIn(**data))
    assert result.amounts.damaged_qty==result.amounts.accepted_qty and stock(db)==before


def test_recovery_survives_receive_permission_revocation(world):
    db,actor,*_=world; result=create(world)
    permission=db.scalar(select(Permission.id).where(Permission.resource=='stock_operation',Permission.action=='receive_return'))
    db.query(RolePermission).filter(RolePermission.permission_id==permission).update({'effect':'deny'});db.flush()
    assert recover(world,trace_request_id='trace-'+KEY).receipt_id==result.receipt_id
    assert create(world).receipt_id==result.receipt_id
    with pytest.raises(MaterialRequestReadError) as error: create(world,key=KEY+'-new')
    assert error.value.category=='forbidden'


@pytest.mark.parametrize('kind',['handover','custody','version','future','past','over','scan','sku'])
def test_invalid_acceptance_is_rejected_before_fact_or_stock_write(world,kind):
    db,_,_,request,original,_,handed,_=world; data=payload(world).model_dump()
    if kind=='handover': data['handover_request_hash']='0'*64
    if kind=='custody': data['custody_assignment_id']=uuid4()
    if kind=='version': data['expected_request_version']=request.version-1
    if kind=='future': data['received_at']=handed.physical_at+timedelta(days=2)
    if kind=='past': data['received_at']=handed.physical_at-timedelta(days=2)
    if kind=='sku': data['observed_sku_code']='WRONG-SKU'
    if kind=='over': data['amounts']['accepted_qty']=Decimal(original.quantity)+1
    if kind=='scan':
        if data['amounts']['accepted_serial_verifications']: data['amounts']['accepted_serial_verifications'][0]['qr_code']='wrong-qr'
        else: data['amounts']['accepted_serial_verifications']=[dict(serial_id=uuid4(),sku_code='wrong',serial_no='wrong',qr_code='wrong')]
    before=stock(db)
    with pytest.raises(MaterialRequestReadError): create(world,RejectionReceiptIn(**data))
    assert stock(db)==before and db.scalar(select(func.count()).select_from(receipts))==0


def test_partial_receipts_accumulate_without_rewriting_original(world):
    if world[4].serial_ids: pytest.skip('single-SN fixture; partial quantity covered separately')
    first=create(world,payload(world,amounts=dict(accepted_qty='0.050')))
    second=create(world,payload(world,amounts=dict(accepted_qty=format(Decimal(world[4].quantity)-Decimal('.050'),'.3f'))),key=KEY+'-part2')
    assert first.receipt_id!=second.receipt_id and recover(world,idempotency_key=KEY,secret=SECRET).receipt_id==first.receipt_id


@pytest.mark.parametrize('kind',['hash','quantity','scan'])
def test_corrupt_warehouse_fact_is_not_recovered(world,kind):
    db,*_=world; result=create(world)
    if kind=='hash': db.execute(receipts.update().where(receipts.c.id==result.receipt_id).values(evidence_sha256='0'*64))
    if kind=='quantity': db.execute(receipts.update().where(receipts.c.id==result.receipt_id).values(accepted_qty=result.amounts.accepted_qty+1))
    if kind=='scan':
        if not result.amounts.accepted_serial_verifications: pytest.skip('SN corruption applies to tracked stock')
        db.execute(serials.update().where(serials.c.receipt_id==result.receipt_id).values(qr_code='tampered-scan'))
    db.flush()
    with pytest.raises(MaterialRequestReadError) as error: recover(world,trace_request_id='trace-'+KEY)
    assert error.value.category=='service_unavailable'


def test_audit_failure_rolls_back_acceptance_and_children(world,monkeypatch):
    db,*_=world; db.commit(); before=stock(db)
    def broken(*_args,**_kwargs): raise service.AuditChainError('injected acceptance audit failure')
    monkeypatch.setattr(service,'append_audit_event',broken)
    with pytest.raises(service.AuditChainError): create(world)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(receipts))==0
    assert db.scalar(select(func.count()).select_from(serials))==0
    assert stock(db)==before
