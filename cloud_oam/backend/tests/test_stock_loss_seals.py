"""Loss tombstones close original coordinates without any stock transition."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.foundation_models import AuditEvent
from app.stock_operation_models import StockLossRequestSeal
from app.stock_loss_schemas import StockLossSealIn
from app.formal_services import stock_loss_seals as seals, stock_loss_recovery as recovery, stock_loss_commands as commands
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.stock_return_commands import _fresh_request
from app.formal_services.stock_return_recovery import lookup_return_request
from test_stock_loss_recovery import db,world,stock,allowed,readable,evidence,submission,lookup,snapshot
from test_stock_loss_source_routes import client,private,PATH


def seal_input(allowed,value):
    return StockLossSealIn(**lookup(value).model_dump(),source_location_id=allowed.account.location_id)


def stock_facts(db):
    return tuple(tuple(db.execute(text('SELECT * FROM '+table+' ORDER BY '+key))) for table,key in (
        ('inventory_transactions','id'),('inventory_movements','id'),('stock_balances','stock_account_id'),
        ('serial_current_positions','serial_id'),('stock_operation_orders','id'),('notification_events','id')))


def test_seal_repeat_lookup_and_late_coordinate_variants_never_post(db,readable,evidence):
    value=submission(db,readable,evidence);db.commit();before=stock_facts(db)
    result=seals.seal_loss_request(db,actor=readable.actor,request=seal_input(readable,value));db.commit()
    assert result.lookup_status=='sealed' and result.retry_permitted is False
    assert stock_facts(db)==before
    assert len(tuple(db.scalars(select(StockLossRequestSeal))))==1
    captured=snapshot(db)
    assert seals.seal_loss_request(db,actor=readable.actor,request=seal_input(readable,value))==result
    db.commit();assert snapshot(db)==captured
    for changed in ({},{'idempotency_key':uuid4().hex},{'request_id':uuid4().hex}):
        with pytest.raises(InventoryReadError) as error:
            commands.submit_loss(db,actor=readable.actor,request=value.model_copy(update=changed))
        assert error.value.code=='stock_loss_request_sealed'
        db.rollback();assert snapshot(db)==captured and stock_facts(db)==before
    db.execute(text('PRAGMA query_only=ON'))
    assert recovery.lookup_loss_request(db,actor=readable.actor,request=lookup(value))==result
    assert snapshot(db)==captured


def test_sealing_an_already_committed_request_returns_original(db,readable,evidence):
    value=submission(db,readable,evidence)
    result=commands.submit_loss(db,actor=readable.actor,request=value);db.commit();before=snapshot(db)
    recovered=seals.seal_loss_request(db,actor=readable.actor,request=seal_input(readable,value))
    assert recovered.lookup_status=='found' and recovered.submission==result
    db.commit();assert snapshot(db)==before and not tuple(db.scalars(select(StockLossRequestSeal)))


@pytest.mark.parametrize('coordinate',['request_id','idempotency_key','request_hash','expected_plan_hash','source_location_id'])
def test_changed_seal_coordinate_is_conflict_without_another_tombstone(db,readable,evidence,coordinate):
    value=submission(db,readable,evidence);request=seal_input(readable,value)
    seals.seal_loss_request(db,actor=readable.actor,request=request);db.commit();before=snapshot(db)
    changed=uuid4() if coordinate=='source_location_id' else uuid4().hex if coordinate in ('request_id','idempotency_key') else 'f'*64
    with pytest.raises(InventoryReadError) as error:
        seals.seal_loss_request(db,actor=readable.actor,request=request.model_copy(update={coordinate:changed}))
    assert error.value.code=='stock_loss_request_conflict'
    db.rollback();assert snapshot(db)==before and len(tuple(db.scalars(select(StockLossRequestSeal))))==1


def test_read_permission_cannot_create_a_tombstone(db,readable,evidence):
    value=submission(db,readable,evidence);db.commit();before=snapshot(db)
    readable.world.current_principal=replace(readable.actor,entitlements=tuple(row for row in readable.actor.entitlements
        if not (row.resource=='stock_operation' and row.action=='submit_loss')))
    assert recovery.lookup_loss_request(db,actor=readable.actor,request=lookup(value)).lookup_status=='not_found'
    with pytest.raises(InventoryReadError) as error:
        seals.seal_loss_request(db,actor=readable.actor,request=seal_input(readable,value))
    assert error.value.code=='stock_loss_forbidden'
    db.rollback();assert snapshot(db)==before and not tuple(db.scalars(select(StockLossRequestSeal)))


def test_failure_after_seal_audit_rolls_back_entire_tombstone(db,readable,evidence,monkeypatch):
    value=submission(db,readable,evidence);db.commit();before=snapshot(db)
    append=seals.append_audit_event
    def revoke(*args,**kwargs):
        append(*args,**kwargs)
        readable.world.current_principal=replace(readable.actor,entitlements=())
    monkeypatch.setattr(seals,'append_audit_event',revoke)
    with pytest.raises(InventoryReadError) as error:
        seals.seal_loss_request(db,actor=readable.actor,request=seal_input(readable,value))
    assert error.value.code=='stock_loss_forbidden'
    db.rollback();assert snapshot(db)==before and not tuple(db.scalars(select(StockLossRequestSeal)))


def test_orphan_seal_audit_is_unknown_and_cannot_be_resealed(db,readable,evidence):
    value=submission(db,readable,evidence)
    append_audit_event(db,stream_key='inventory',actor_user_id=readable.actor.user_id,action=seals.KIND,
        aggregate_type=seals.AGGREGATE,aggregate_id=str(uuid4()),before_jsonb={},after_jsonb={'request_id':value.request_id},
        request_id='stock-loss-seal:'+uuid4().hex,occurred_at=datetime.now(timezone.utc))
    db.commit();before=snapshot(db)
    for call in (lambda: recovery.lookup_loss_request(db,actor=readable.actor,request=lookup(value)),
            lambda: seals.seal_loss_request(db,actor=readable.actor,request=seal_input(readable,value))):
        with pytest.raises(InventoryReadError) as error:call()
        assert error.value.code=='stock_loss_evidence_invalid'
    db.rollback();assert snapshot(db)==before and not tuple(db.scalars(select(StockLossRequestSeal)))


def test_shared_return_namespace_and_http_recover_the_same_tombstone(db,readable,evidence,client):
    value=submission(db,readable,evidence)
    result=seals.seal_loss_request(db,actor=readable.actor,request=seal_input(readable,value));db.commit()
    before=snapshot(db)
    from app.formal_services import inventory_posting as posting
    for request_id,key in ((value.request_id,uuid4().hex),(uuid4().hex,posting._storage_hash(value.idempotency_key))):
        with pytest.raises(InventoryReadError) as error:
            _fresh_request(db,actor=readable.actor,request_id=request_id,key=key)
        assert error.value.code=='stock_loss_request_sealed'
    with pytest.raises(InventoryReadError) as error:
        lookup_return_request(db,actor=readable.actor,work_order_id=readable.orders[0].id,
            operation_type='submit_return',request_id=value.request_id)
    assert error.value.code=='stock_return_request_conflict'
    db.execute(text('PRAGMA query_only=ON'))
    response=client.post(PATH+'/request-lookup',json=lookup(value).model_dump(mode='json'))
    assert response.status_code==200,response.text
    assert response.json()['seal']['seal_id']==str(result.seal.seal_id)
    assert response.json()['retry_permitted'] is False and value.idempotency_key not in response.text
    private(response)
    assert snapshot(db)==before
