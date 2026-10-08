"""Private writer on full candidate SQLite schema and real old service history.

The inherited old writer emulates 1.0; native predecessor, triggers, HTTP and
request seals have separate gates. Current principal and inventory writer are
real application calls. Object storage alone is fake.
"""
from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import create_engine, event, select, text
from sqlalchemy.orm import Session

from app.foundation_models import FileObject, NotificationEvent
from app.inventory_models import StockAccount, StockBalance, InventoryTransaction, InventoryMovement
from app.return_condition_complete_schema import build_schema
from app.return_condition_requests import ConditionSubmit
from app.formal_services import inventory_posting as posting, formal_files
from app.formal_access import load_formal_principal, lock_formal_principal_graph
from app.formal_services.stock_loss_corrections import return_condition_submission as subject
from app.formal_services.stock_loss_corrections import return_condition_submission_source as preparation
from app.formal_services.stock_loss_corrections import return_condition_business_events as business
from app.formal_services.stock_loss_corrections import return_condition_posting_authority as permits
from test_return_condition_submission_source import (
    world,stock,allowed,evidence,regional,headquarters,approved,route,derived,ready,parcel,
    acceptance,prepared,authority_template,regional_opening,context,regional_source,ERRORS,
)
from test_return_condition_evidence import create,finish
from test_formal_files_service import FakeStorage

pytestmark=pytest.mark.parametrize('stock',['quantity'],indirect=True)


@pytest.fixture
def db(monkeypatch):
    engine=create_engine('sqlite+pysqlite:///:memory:')
    @event.listens_for(engine,'connect')
    def fks(connection,_): connection.execute('PRAGMA foreign_keys=ON')
    build_schema()[0].create_all(engine)
    # SQLite exercises the service only. The product registrar has no SQLite
    # fallback; native role/locking/COMMIT proof uses the real PG16 function.
    from app.formal_services.stock_loss_corrections import return_condition_keys as keys
    def register(session, *, event, request):
        values=dict(event_id=event['id'],case_id=event['case_id'],created_at=event['created_at'],
            actor_user_id=event['actor_user_id'],actor_person_id=event['actor_person_id'],
            request_id=event['request_id'],request_hash=event['request_hash'],**keys.aliases(request.idempotency_key))
        session.execute(keys.table().insert(),values)
        return keys.match_original(session,event=event,request=request)
    monkeypatch.setattr(keys,'record',register)
    with Session(engine) as session: yield session
    engine.dispose()


@pytest.fixture
def command(db,regional_source,monkeypatch):
    c=regional_source
    # Older receipt fixtures used an in-memory identity. New upload admission
    # must call the real identity loader and graph lock for this regional user.
    monkeypatch.setattr(formal_files,'load_formal_principal',load_formal_principal)
    monkeypatch.setattr(formal_files,'lock_formal_principal_graph',lock_formal_principal_graph)
    upload=SimpleNamespace(actor=c.actor,storage=FakeStorage())
    row=db.get(FileObject,create(db,upload).file_id); finish(db,upload,row);db.commit()
    proof=preparation.inspect_submission_source(db,actor=c.actor,inbound_line_id=c.line)
    result=ConditionSubmit(action='submit_return_condition',inbound_line_id=c.line,
        expected_source_hash=proof.evidence_hash,quantity='0.125',evidence_file_ids=(row.id,),
        reason='独立核实原破损验收成色',request_id=uuid4().hex,idempotency_key=uuid4().hex)
    db.commit()
    return result


def snapshot(db):
    # Includes candidate tables, inventory, audits, request identities and all
    # original history. A failed transaction must restore the complete graph.
    names=db.scalars(text("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name")).all()
    return {name:tuple(sorted((tuple(row) for row in db.execute(text('SELECT * FROM "'+name+'"'))),key=repr)) for name in names}


def test_complete_initial_freeze_binds_source_files_events_and_pending_notification(db,regional_source,command):
    c=regional_source;before=db.get(StockBalance,c.source.id).quantity
    result=subject.submit(db,actor=c.actor,request=command);db.commit()
    cases=subject.tables()['stock_condition_cases'];events=subject.tables()['stock_condition_events']
    case=db.execute(select(cases).where(cases.c.id==UUID(result['case_id']))).mappings().one()
    persisted=db.execute(select(events).where(events.c.id==UUID(result['event_id']))).mappings().one()
    assert case['quantity']==Decimal('.125') and case['affected_quantity']==Decimal('.375')
    assert result['status']=='awaiting_regional' and result['stock_effect']=='freeze'
    assert db.get(StockBalance,c.source.id,populate_existing=True).quantity==before-Decimal('.125')
    assert db.get(StockBalance,case['frozen_account_id']).quantity==Decimal('.125')
    transaction=db.get(InventoryTransaction,case['freeze_transaction_id'])
    move=db.get(InventoryMovement,case['freeze_movement_id'])
    assert transaction.source_document_type=='stock_condition_event'
    assert transaction.source_document_id==str(persisted['id'])
    assert move.from_account_id==c.source.id and move.to_account_id==case['frozen_account_id']
    assert business.verify(db,case=case,event=persisted,recipient=c.actor.person_id)==result
    note=db.scalar(select(NotificationEvent).where(NotificationEvent.business_id==str(persisted['id'])))
    assert note.status=='pending' and note.event_type=='stock_condition.submit'
    assert permits.KEY not in db.info
    before=snapshot(db)
    with pytest.raises(ERRORS,match='原请求已有事实'):
        subject.submit(db,actor=c.actor,request=command)
    db.rollback();assert snapshot(db)==before


@pytest.mark.parametrize('where',['before_business_effects','after_business_effects'])
def test_failure_after_posting_rolls_back_entire_graph(db,regional_source,command,monkeypatch,where):
    before=snapshot(db);record=business.record
    def broken(db,**kwargs):
        assert db.scalar(select(InventoryTransaction.id).where(InventoryTransaction.source_document_type=='stock_condition_event'))
        if where=='after_business_effects': record(db,**kwargs)
        raise RuntimeError('synthetic condition effect failure')
    monkeypatch.setattr(business,'record',broken)
    with pytest.raises(RuntimeError,match='synthetic condition effect failure'):
        subject.submit(db,actor=regional_source.actor,request=command)
    db.rollback()
    assert snapshot(db)==before and permits.KEY not in db.info


@pytest.mark.parametrize('change',['source','quantity','missing_file','other_actor'])
def test_invalid_application_never_freezes_stock(db,regional_source,command,change):
    actor=regional_source.actor
    if change=='source': command=command.model_copy(update={'expected_source_hash':'a'*64})
    elif change=='quantity': command=command.model_copy(update={'quantity':Decimal('.376')})
    elif change=='missing_file': command=command.model_copy(update={'evidence_file_ids':(uuid4(),)})
    else: actor=regional_source.reviewer
    before=snapshot(db)
    from formal_file_integrity import FormalFileError
    with pytest.raises((*ERRORS,FormalFileError)):
        subject.submit(db,actor=actor,request=command)
    db.rollback();assert snapshot(db)==before


def test_forged_permit_cannot_use_generic_writer(db,regional_source,command):
    before=snapshot(db)
    graph=posting.InventoryPostingCommand(transaction_no='COND-UNAUTHORIZED',movement_type='freeze',
        source_document_type='stock_condition_event',source_document_id=str(uuid4()),posting_key=uuid4().hex,
        effective_at=datetime.now(timezone.utc),movements=(
            posting.InventoryMovementCommand(regional_source.source.id,None,Decimal('.125')),))
    with pytest.raises(posting.InventoryPostingError,match='成色纠正冻结'):
        permits.require(db,actor=regional_source.actor,command=graph,permit=object(),permission_resource='stock_operation',
            permission_action='submit_return_condition',reversed_transaction_id=None,opening_task_id=None,
            current_cursor=1,idempotency_key_hash='a'*64,request_hash='a'*64,request_reference='test',
            occurred_at=graph.effective_at,event_suffix='posted',receipt_authority=None,scrap_authority=None,scrap_recovery_authority=None)
    assert snapshot(db)==before
