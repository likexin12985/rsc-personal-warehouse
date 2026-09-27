"""Submission service composition on SQLite, not PG16 activation evidence.

The existing fixture supplies synthetic current identity and opening proofs.
Inventory posting, immutable facts, file lifecycle, audit and notifications
are the application implementations. Real SQL/COMMIT guards are separate.
"""
from decimal import Decimal
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.foundation_models import NotificationEvent
from app.inventory_models import StockBalance, StockAccount, SerialCurrentPosition
from app.stock_operation_models import StockOperationOrder, StockOperationLine, StockLossFile
from app.stock_loss_schemas import StockLossSubmitIn
from app.formal_services import stock_loss_commands as commands, stock_loss_facts as facts, stock_loss_plan as plan
from app.formal_services.inventory_query import InventoryReadError
from test_stock_loss_plan import db, world, stock, allowed, evidence, command
from test_work_order_removed_registration import counts, inventory


def submission(db, allowed, evidence):
    value=command(allowed,evidence)
    preview,_=plan.preview_loss(db,actor=allowed.actor,request=value)
    return StockLossSubmitIn(**value.model_dump(),expected_plan_hash=preview.plan_hash,
        idempotency_key=uuid4().hex,request_id=uuid4().hex)


def snapshot(db):
    return counts(db),inventory(db),tuple(tuple(db.execute(text(f'SELECT * FROM {table} ORDER BY id')))
        for table in ('stock_operation_orders','stock_operation_lines','stock_operation_serials','stock_loss_files',
                      'notification_events','notification_person_targets','notification_recipients'))


def test_atomic_freeze_own_files_original_replay_and_separate_notification(db, allowed, evidence):
    value=submission(db,allowed,evidence)
    before=db.get(StockBalance,allowed.account.id).quantity
    result=commands.submit_loss(db,actor=allowed.actor,request=value);db.commit()
    order=db.get(StockOperationOrder,result.operation_id)
    assert order.operation_type=='loss_report' and order.oam_work_order_id is None
    line=db.scalar(select(StockOperationLine).where(StockOperationLine.operation_id==order.id))
    held=db.get(StockAccount,line.reserved_account_id)
    assert held.availability_bucket=='frozen' and held.custodian_person_id==allowed.actor.person_id
    assert db.get(StockBalance,held.id).quantity==Decimal('1')
    assert db.get(StockBalance,allowed.account.id).quantity==before-Decimal('1')
    assert set(db.scalars(select(StockLossFile.file_id).where(StockLossFile.operation_id==order.id)))=={r.id for r in evidence}
    event=db.scalar(select(NotificationEvent).where(NotificationEvent.business_id==str(order.id)))
    assert event.event_type=='stock_loss_submitted' and event.status=='pending'
    if allowed.tracked:
        assert db.get(SerialCurrentPosition,allowed.serials[3].id).stock_account_id==held.id
    before=snapshot(db)
    assert commands.submit_loss(db,actor=allowed.actor,request=value)==result
    assert facts.order_result(db,actor=allowed.actor,order=order)==result
    db.commit()
    assert snapshot(db)==before
    assert result.status=='submitted' and 'qr_code' not in result.model_dump_json()
    assert 'storage_key' not in result.model_dump_json()


@pytest.mark.parametrize('change',['plan','reason','request','key'])
def test_changed_original_request_does_not_create_second_freeze(db,allowed,evidence,change):
    value=submission(db,allowed,evidence)
    commands.submit_loss(db,actor=allowed.actor,request=value);db.commit()
    before=snapshot(db)
    fields={'plan':('expected_plan_hash','f'*64),'reason':('reason','修改了已提交的报损原因'),
            'request':('request_id',uuid4().hex),'key':('idempotency_key',uuid4().hex)}
    key,new=fields[change]
    with pytest.raises(InventoryReadError):
        commands.submit_loss(db,actor=allowed.actor,request=value.model_copy(update={key:new}))
    db.rollback()
    assert snapshot(db)==before


def test_failure_after_real_posting_rolls_back_whole_submission(db,allowed,evidence,monkeypatch):
    value=submission(db,allowed,evidence);db.commit();before=snapshot(db)
    def failed(*args,**kwargs):
        assert db.scalar(select(StockOperationOrder.id)) is not None
        assert tuple(db.scalars(select(StockLossFile.id)))
        raise RuntimeError('synthetic loss domain audit failure')
    monkeypatch.setattr(commands,'_record',failed)
    with pytest.raises(RuntimeError,match='synthetic loss domain audit failure'):
        commands.submit_loss(db,actor=allowed.actor,request=value)
    db.rollback()
    assert snapshot(db)==before


def test_new_preview_cannot_rebind_another_reports_evidence(db,allowed,evidence):
    value=submission(db,allowed,evidence)
    commands.submit_loss(db,actor=allowed.actor,request=value);db.commit()
    # Use the remaining quantity/SN, but repeat the bound photo IDs.
    next_value=command(allowed,evidence)
    if allowed.tracked:
        sn=allowed.serials[4]
        next_value=next_value.model_copy(update={'lines':(next_value.lines[0].model_copy(update={
            'serial_verifications':(next_value.lines[0].serial_verifications[0].model_copy(update={
                'serial_id':sn.id,'serial_no':sn.serial_no,'qr_code':sn.qr_code}),)}),)})
    preview,_=plan.preview_loss(db,actor=allowed.actor,request=next_value)
    new=StockLossSubmitIn(**next_value.model_dump(),expected_plan_hash=preview.plan_hash,
        idempotency_key=uuid4().hex,request_id=uuid4().hex)
    before=snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        commands.submit_loss(db,actor=allowed.actor,request=new)
    assert error.value.code=='stock_loss_file_already_bound'
    db.rollback();assert snapshot(db)==before
