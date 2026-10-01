"""Execution seal service composition; PG16 exclusion has separate native gates."""
from dataclasses import replace
from uuid import uuid4
import pytest
from sqlalchemy import select,text

from test_stock_loss_disposition_recovery import (
    db,world,stock,allowed,evidence,regional,headquarters,approved,route,execution,
)
from test_stock_loss_seals import stock_facts
from app.formal_services.inventory_query import InventoryReadError
from app.stock_operation_models import StockLossDispositionRequestSeal as Seal
from app.stock_loss_disposition_seal_schemas import StockLossDispositionSealIn, StockLossDerivedReturnSealIn
from app.formal_services import stock_loss_disposition_seals as seals
from app.formal_services import stock_loss_disposition_recovery as recovery
from app.formal_services import stock_loss_disposition_commands as dispositions
from app.formal_services import stock_loss_return_commands as returns


def lookup(db,w):
    return recovery.lookup_disposition_request(db,actor=w.actor,request=w.command,flow=w.flow)


def execute(db,w,command=None):
    fn=dispositions.execute_disposition if w.flow=='disposition' else returns.execute_loss_return
    return fn(db,actor=w.actor,request=command or w.command)


def seal(db,w):
    schema=StockLossDispositionSealIn if w.flow=='disposition' else StockLossDerivedReturnSealIn
    return seals.seal_execution_request(db,actor=w.actor,flow=w.flow,
        request=schema(operator_person_id=w.actor.person_id,original=w.command))


def snapshot(db):
    from test_stock_loss_dispositions import snapshot as base
    return base(db),tuple(db.execute(text('SELECT * FROM stock_loss_disposition_request_seals ORDER BY id')))


def test_seal_replay_late_execution_refusal_and_read_after_write_revocation(db,allowed,execution):
    w=execution;before_stock=stock_facts(db)
    result=seal(db,w);db.commit()
    assert result['lookup_status']=='sealed' and result['retry_permitted'] is False
    assert result['seal']['stock_effect']=='none' and stock_facts(db)==before_stock
    assert w.command.idempotency_key not in str(result)
    before=snapshot(db)
    assert seal(db,w)==result;db.commit();assert snapshot(db)==before
    for change in ({},{'request_id':uuid4().hex},{'idempotency_key':uuid4().hex}):
        with pytest.raises(InventoryReadError) as error:
            execute(db,w,w.command.model_copy(update=change))
        assert error.value.code=='stock_loss_disposition_request_sealed'
        db.rollback();assert snapshot(db)==before
    allowed.world.current_principal=replace(w.actor,
        entitlements=tuple(g for g in w.actor.entitlements if g.action!='dispose_loss'))
    assert lookup(db,w)==result
    with pytest.raises(InventoryReadError):seal(db,w)
    db.rollback();assert snapshot(db)==before
    db.execute(text('PRAGMA query_only=ON'))
    assert lookup(db,w)==result and snapshot(db)==before


def test_committed_execution_returns_original_fact_without_creating_seal(db,execution):
    w=execution;posted=execute(db,w);db.commit();before=snapshot(db)
    assert seal(db,w)==dict(lookup_status='found',retry_permitted=False,disposition=posted)
    db.commit();assert snapshot(db)==before
    assert not tuple(db.scalars(select(Seal)))


def test_new_explicit_request_does_not_destroy_old_permanent_seal(db,execution):
    w=execution;old=seal(db,w);db.commit()
    new=w.command.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    posted=execute(db,w,new);db.commit()
    assert posted['request_id']!=old['seal']['request_id']
    before=snapshot(db)
    assert lookup(db,w)==old and snapshot(db)==before
    assert recovery.lookup_disposition_request(db,actor=w.actor,request=new,flow=w.flow)['disposition']==posted


def test_post_audit_authority_failure_rolls_back_the_whole_seal(db,allowed,execution,monkeypatch):
    w=execution;db.commit();before=snapshot(db)
    append=seals.append_audit_event
    def revoke(*args,**kwargs):
        result=append(*args,**kwargs)
        allowed.world.current_principal=replace(w.actor,entitlements=())
        return result
    monkeypatch.setattr(seals,'append_audit_event',revoke)
    with pytest.raises(InventoryReadError):seal(db,w)
    db.rollback();assert snapshot(db)==before


def test_sealed_read_rejects_every_original_field_change_and_current_read_revocation(db,allowed,execution):
    w=execution;answer=seal(db,w);db.commit();before=snapshot(db)
    for field in type(w.command).model_fields:
        value=uuid4() if field.endswith('_id') and field!='request_id' else (
            uuid4().hex if field in ('request_id','idempotency_key') else 'f'*64)
        with pytest.raises(InventoryReadError):
            recovery.lookup_disposition_request(db,actor=w.actor,flow=w.flow,
                request=w.command.model_copy(update={field:value}))
        db.rollback();assert snapshot(db)==before
    allowed.world.current_principal=replace(w.actor,
        entitlements=tuple(g for g in w.actor.entitlements if g.action!='read'))
    with pytest.raises(InventoryReadError) as error:lookup(db,w)
    assert error.value.code=='stock_loss_disposition_read_forbidden'
    db.rollback();assert snapshot(db)==before
    allowed.world.current_principal=w.actor
    assert lookup(db,w)==answer


def test_corrupt_seal_audit_and_spurious_events_never_become_success(db,execution):
    from app.foundation_models import AuditEvent,OutboxEvent
    from datetime import datetime,timezone
    w=execution;answer=seal(db,w);db.commit();before=snapshot(db)
    row=db.scalars(select(Seal)).one()
    audit=db.scalars(select(AuditEvent).where(AuditEvent.aggregate_type==seals.AGGREGATE,
        AuditEvent.aggregate_id==str(row.id))).one()
    # Only the disposable SQLite owner can damage this append-only evidence.
    audit.after_jsonb={**audit.after_jsonb,'stock_effect':'release'};db.flush()
    with pytest.raises(InventoryReadError):lookup(db,w)
    db.rollback();assert snapshot(db)==before
    row=db.scalars(select(Seal)).one()
    row.command_jsonb={**row.command_jsonb,'unexpected':'replacement'};db.flush()
    with pytest.raises(InventoryReadError):lookup(db,w)
    db.rollback();assert snapshot(db)==before
    row=db.scalars(select(Seal)).one()
    db.add(OutboxEvent(event_type=seals.KIND,aggregate_type=seals.AGGREGATE,
        aggregate_id=str(row.id),idempotency_key=uuid4().hex,
        available_at=datetime.now(timezone.utc),payload_jsonb={}))
    db.flush()
    with pytest.raises(InventoryReadError):lookup(db,w)
    db.rollback();assert snapshot(db)==before
    assert lookup(db,w)==answer


def test_seal_refuses_unknown_execution_and_operator_substitution(db,execution):
    from app.foundation_models import OutboxEvent
    from datetime import datetime,timezone
    w=execution;db.commit();before=snapshot(db)
    schema=StockLossDispositionSealIn if w.flow=='disposition' else StockLossDerivedReturnSealIn
    with pytest.raises(InventoryReadError) as error:
        seals.seal_execution_request(db,actor=w.actor,flow=w.flow,
            request=schema(operator_person_id=uuid4(),original=w.command))
    assert error.value.code=='operator_mismatch'
    db.rollback();assert snapshot(db)==before
    db.add(OutboxEvent(event_type=recovery.facts.KIND,aggregate_type=recovery.facts.AGGREGATE,
        aggregate_id=str(uuid4()),idempotency_key=uuid4().hex,
        available_at=datetime.now(timezone.utc),payload_jsonb={
            'executor_person_id':str(w.actor.person_id),'request_id':w.command.request_id}))
    db.flush()
    with pytest.raises(InventoryReadError):seal(db,w)
    assert not tuple(db.scalars(select(Seal)))
    db.rollback();assert snapshot(db)==before
