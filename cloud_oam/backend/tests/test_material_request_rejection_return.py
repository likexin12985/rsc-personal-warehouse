"""Registration composition tests. Native PG16 separately enforces the write boundary."""
from decimal import Decimal
from uuid import uuid4
from dataclasses import replace
import pytest
from sqlalchemy import select, event
from app.foundation_models import Permission, Role, RolePermission
from app.formal_access import load_formal_principal
from app.material_request_rejection_return_schema import returns, return_serials
from app.material_request_rejection_progress_schema import progress
from app.material_request_rejection_return_schemas import RejectionReturnIn
from app.formal_services import material_request_rejection_return as service
from app.formal_services.material_request_query import MaterialRequestReadError
from test_material_request_my_receipt import world as receipt_world, receiving_world, outbound_world, create as accept, payload, evidence
from test_material_request_draft_service import SECRET
from test_material_request_my_inbound import facts
pytest_plugins=('test_material_request_picking',)
KEY='rejection-return-test-original-0001'


@pytest.fixture
def world(receipt_world):
    db,actor,request,*_=receipt_world
    for table in (returns,return_serials,progress): table.create(db.get_bind(),checkfirst=True)
    permission=db.scalar(select(Permission).where(Permission.resource=='stock_operation',Permission.action=='submit_return',Permission.field_code==''))
    if permission is None:
        permission=Permission(id=uuid4(),resource='stock_operation',action='submit_return',field_code='');db.add(permission);db.flush()
    role_id=db.scalar(select(Role.id).where(Role.code=='technician'))
    grant=db.scalar(select(RolePermission).where(RolePermission.role_id==role_id,RolePermission.permission_id==permission.id))
    if grant is None: db.add(RolePermission(role_id=role_id,permission_id=permission.id,effect='allow'))
    else: grant.effect='allow'
    db.flush(); actor=load_formal_principal(db,actor.user_id)
    file,_=evidence((db,actor))
    value=payload(receipt_world).model_dump();line=value['lines'][0]
    line.update(condition='rejected',rejected_qty=line['accepted_qty'],accepted_qty='0.000',
        rejected_serial_ids=line['accepted_serial_ids'],accepted_serial_ids=(),exception_evidence_file_id=file.id)
    received=accept((db,actor,request,*receipt_world[3:]),value)
    original=received.lines[0]
    command=RejectionReturnIn(expected_request_version=request.version,receipt_id=received.receipt_id,
        receipt_line_id=original.receipt_line_id,receipt_request_hash=received.request_hash,
        quantity=format(original.rejected_qty,'.3f'),serial_ids=original.rejected_serial_ids,reason='拒收后申请退回原来源仓')
    return db,actor,request,command


def create(world,payload=None,key=KEY,actor=None):
    db,current,request,command=world
    return service.register_rejection_return(db,actor=actor or current,request_id=request.id,payload=payload or command,
        idempotency_key=key,secret=SECRET,trace_request_id='trace-'+key)


def recover(world,**kw):
    db,actor,request,_=world
    return service.rejection_return_command_status(db,actor=actor,request_id=request.id,**kw)


def test_register_original_and_read_only_recovery_never_moves_stock(world):
    db,actor,request,command=world
    before=facts(db); before_version=request.version
    value=create(world)
    assert value.status=='registered' and not value.replayed and value.serial_ids==command.serial_ids
    after=facts(db)
    # facts() also counts audit; inventory facts must not change.
    assert before[:7]==after[:7] and before[8:]==after[8:] and request.version==before_version
    assert create(world).return_id==value.return_id
    statements=[]
    def capture(_conn,_cursor,sql,*_):statements.append(sql)
    event.listen(db.bind,'before_cursor_execute',capture)
    try:
        a=recover(world,idempotency_key=KEY,secret=SECRET)
        b=recover(world,trace_request_id='trace-'+KEY)
    finally:event.remove(db.bind,'before_cursor_execute',capture)
    assert a==b and a.return_id==value.return_id and a.replayed
    assert all(sql.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in sql.upper() for sql in statements)
    assert facts(db)==after


@pytest.mark.parametrize('kind',['over','accepted_serial','wrong_receipt','wrong_line','wrong_hash','stale','actor'])
def test_rejects_invalid_registration_before_writing(world,kind):
    db,actor,request,command=world
    data=command.model_dump()
    if kind=='over':data['quantity']=format(Decimal(command.quantity)+1,'.3f')
    if kind=='accepted_serial':data['serial_ids']=(uuid4(),)
    if kind=='wrong_receipt':data['receipt_id']=uuid4()
    if kind=='wrong_line':data['receipt_line_id']=uuid4()
    if kind=='wrong_hash':data['receipt_request_hash']='a'*64
    if kind=='stale':data['expected_request_version']-=1
    if kind=='actor':actor=replace(actor,authorization_version=actor.authorization_version+1)
    before=facts(db)
    with pytest.raises(MaterialRequestReadError):create(world,data,actor=actor)
    assert not tuple(db.scalars(select(returns.c.id))) and facts(db)==before


def test_partial_quantity_then_remaining_and_duplicate_refusal(world):
    db,_,_,command=world
    if command.serial_ids:
        first=create(world)
        with pytest.raises(MaterialRequestReadError):create(world,key=KEY+'-duplicate')
        assert len(tuple(db.scalars(select(returns.c.id))))==1
        return
    first=command.model_copy(update={'quantity':'0.050'})
    a=create(world,first)
    b=create(world,command.model_copy(update={'quantity':format(Decimal(command.quantity)-Decimal('.050'),'.3f')}),key=KEY+'-part2')
    assert a.return_id!=b.return_id
    with pytest.raises(MaterialRequestReadError):create(world,key=KEY+'-duplicate')


def test_original_recovers_after_return_permission_revocation_but_new_key_is_denied(world):
    db,actor,_,command=world
    created=create(world)
    role_id=db.scalar(select(Role.id).where(Role.code=='technician'))
    permission=db.scalar(select(Permission.id).where(Permission.resource=='stock_operation',Permission.action=='submit_return'))
    db.query(RolePermission).filter(RolePermission.role_id==role_id,RolePermission.permission_id==permission).update({'effect':'deny'});db.flush()
    assert recover(world,trace_request_id='trace-'+KEY).return_id==created.return_id
    assert create(world).return_id==created.return_id
    with pytest.raises(MaterialRequestReadError):create(world,key=KEY+'-new')
    with pytest.raises(MaterialRequestReadError):create(world,command.model_copy(update={'reason':'更改原因'}))


def test_changed_stored_source_cannot_be_recovered(world):
    db,*_=world
    result=create(world)
    db.execute(returns.update().where(returns.c.id==result.return_id).values(evidence_sha256='0'*64))
    with pytest.raises(MaterialRequestReadError):recover(world,idempotency_key=KEY,secret=SECRET)

@pytest.mark.parametrize('kind',['scale','integer'])
def test_registration_respects_original_quantity_policy(world,kind):
    from app.inventory_models import MaterialInventoryPolicy
    db,_,_,command=world
    for policy in db.scalars(select(MaterialInventoryPolicy)):
        if kind=='scale': policy.quantity_scale=0
        else: policy.allow_fraction=False
    db.flush()
    with pytest.raises(MaterialRequestReadError) as error:
        create(world,command.model_copy(update={'quantity':'0.001'}))
    assert error.value.code=='rejection_return_precision_invalid'
    assert not tuple(db.scalars(select(returns.c.id)))
