"""New stock commands require current formal OAM evidence; replay is historical."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.foundation_models import ExternalObject, ExternalObjectVersion, SourceSystem
from app.formal_services import work_order_material as material
from app.formal_services import work_order_replacements as replacements
from app.formal_services.work_order_operation_read import lookup_operation
from test_work_order_material_options import db, world, stock


def invalidate(db, stock, mode):
    order=stock.orders[0]
    external=db.get(ExternalObject,order.external_object_id)
    version=db.get(ExternalObjectVersion,external.current_version_id)
    source=db.get(SourceSystem,external.source_system_id)
    if mode=='stale':
        past=datetime.now(timezone.utc)-timedelta(hours=2)
        order.source_updated_at=past;order.updated_at=past;order.created_at=past
        version.source_updated_at=past;version.valid_from=past;version.created_at=past
    elif mode=='disabled':source.enabled=False
    elif mode=='missing_version':external.current_version_id=None
    elif mode=='payload':version.payload_sha256='0'*64
    else:source.code='untrusted-work-order-source'
    db.commit()


def line(stock, kind):
    return stock.line('1',stock.serials[3:4] if kind=='occupy' else stock.serials[1:2],
        identifier=stock.account.id if kind=='occupy' else stock.reserved.id,
        target=stock.account.id if kind=='release' else None)


@pytest.mark.parametrize('kind',['occupy','consume','release'])
@pytest.mark.parametrize('mode',['stale','disabled','missing_version','payload','source'])
def test_new_command_rejects_invalid_source_before_any_inventory_or_target_write(db,stock,kind,mode):
    invalidate(db,stock,mode)
    db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(material.InventoryPostingError) as exc:
        getattr(material,f'execute_{kind}_operation')(db,actor=stock.actor,work_order_id=stock.orders[0].id,
            lines=(line(stock,kind),),idempotency_key=uuid4().hex,request_id=uuid4().hex)
    assert exc.value.code in {'work_order_source_not_current','work_order_source_invalid','work_order_projection_invalid'}
    assert not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('mode',['stale','disabled','missing_version'])
def test_original_command_can_replay_and_recover_after_source_stops_being_current(db,stock,mode):
    key,trace=uuid4().hex,uuid4().hex
    args=dict(actor=stock.actor,work_order_id=stock.orders[0].id,lines=(line(stock,'occupy'),),
        idempotency_key=key,request_id=trace)
    operation,posted=material.execute_occupy_operation(db,**args);db.commit()
    invalidate(db,stock,mode)
    replay,replay_post=material.execute_occupy_operation(db,**args)
    assert replay.id==operation.id and replay_post.transaction_id==posted.transaction_id
    recovered=lookup_operation(db,actor=stock.actor,work_order_id=stock.orders[0].id,operation_type='occupy',request_id=trace)
    assert recovered.lookup_status=='confirmed' and recovered.command.operation_id==operation.id
    with pytest.raises(material.InventoryPostingError) as exc:
        material.execute_occupy_operation(db,**{**args,'idempotency_key':uuid4().hex,'request_id':uuid4().hex})
    assert exc.value.code in {'work_order_source_not_current','work_order_source_invalid'}


def test_current_source_validation_does_not_require_an_extra_read_permission(db,stock):
    actor=replace(stock.actor,entitlements=tuple(row for row in stock.actor.entitlements if row.action!='read'))
    stock.world.current_principal=actor
    operation,_=material.execute_occupy_operation(db,actor=actor,work_order_id=stock.orders[0].id,
        lines=(line(stock,'occupy'),),idempotency_key=uuid4().hex,request_id=uuid4().hex)
    assert operation.status=='posted'
    db.rollback()


def test_replacement_checks_current_source_before_recovery_dimension_creation(db,stock,monkeypatch):
    invalidate(db,stock,'disabled')
    def unexpected(*args,**kwargs):raise AssertionError('disabled source cannot create recovery accounts')
    monkeypatch.setattr(replacements,'resolve_recovery_lines',unexpected)
    consumed=line(stock,'consume')
    # A quantity replacement has no serial pairs; the source boundary rejects
    # it before account resolution. The serial case supplies distinct known IDs.
    removed=stock.serials[0] if stock.tracked else None
    recovered=replacements.RecoveryLineInput(stock.reserved.id,consumed.material_id,consumed.quantity,'used',
        serial_ids=(removed.id,) if removed else (),serial_verifications=(material.SerialVerificationInput(
            removed.id,stock.world.material.sku_code,removed.serial_no,removed.qr_code),) if removed else ())
    pairs=(material.WorkOrderReplacementPairInput(consumed.serial_ids[0],removed.id),) if removed else ()
    with pytest.raises(material.InventoryPostingError) as exc:
        replacements.execute_replacement(db,actor=stock.actor,work_order_id=stock.orders[0].id,
            consume_lines=(consumed,),recover_lines=(recovered,),pairs=pairs,idempotency_key=uuid4().hex,request_id=uuid4().hex)
    assert exc.value.code=='work_order_source_not_current'


def test_original_request_read_does_not_depend_on_later_work_order_assignment(db,stock):
    trace=uuid4().hex
    operation,_=material.execute_occupy_operation(db,actor=stock.actor,work_order_id=stock.orders[0].id,
        lines=(line(stock,'occupy'),),idempotency_key=uuid4().hex,request_id=trace)
    db.commit();stock.orders[0].engineer_person_id=stock.world.headquarters_reviewer_person.id;db.commit()
    assert lookup_operation(db,actor=stock.actor,work_order_id=stock.orders[0].id,
        operation_type='occupy',request_id=trace).command.operation_id==operation.id
