"""Historical reader against real service postings; SQLite corruption is read-side evidence."""
from datetime import timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select, update, delete

from app.foundation_models import AuditEvent, OutboxEvent, StateTransitionEvent
from app.inventory_models import InventoryTransaction, InventoryMovement, InventoryMovementSerial
from app.formal_services.stock_loss_corrections import return_condition_submission as writer
from app.formal_services.stock_loss_corrections import return_condition_ledger_facts as subject
from app.formal_services.stock_loss_corrections import return_condition_business_events as business
from test_return_condition_submission import db, snapshot, command as quantity_command
from test_return_condition_submission_serial import command as serial_command
from test_return_condition_submission_source import (
    world,stock,allowed,evidence,regional,headquarters,approved,route,derived,ready,parcel,
    acceptance,prepared,regional_opening,context,regional_source,ERRORS,
)


@pytest.fixture
def authority_template():
    # The upstream module cache assumes a single tracking mode. This module
    # deliberately exercises both, so each case owns its historical snapshot.
    value = {}
    yield value
    if 'database' in value:
        value['database'].close()


@pytest.mark.parametrize('stock,command_name', [('quantity','quantity_command'),('serial','serial_command')], indirect=['stock'])
def test_historical_posting_and_corrupt_edges(db,regional_source,request,command_name):
    command=request.getfixturevalue(command_name)
    result=writer.submit(db,actor=regional_source.actor,request=command);db.commit()
    cases=writer.tables()['stock_condition_cases'];events=writer.tables()['stock_condition_events']
    case=db.execute(select(cases).where(cases.c.id==UUID(result['case_id']))).mappings().one()
    event=db.execute(select(events).where(events.c.id==UUID(result['event_id']))).mappings().one()
    serials=tuple(s.serial_id for s in command.serial_verifications)
    before=snapshot(db)
    edge=subject.verify_event_posting(db,event=event,serial_ids=serials)
    assert edge.transaction_id==case['freeze_transaction_id']
    assert edge.movement_id==case['freeze_movement_id']
    assert edge.quantity==command.quantity and edge.serial_ids==serials
    assert edge.ledger_cursor>case['original_ledger_cursor']
    assert business.verify(db,case=case,event=event,recipient=regional_source.actor.person_id)==result
    assert snapshot(db)==before
    # The isolated SQLite fixture permits corrupt persistence; no PostgreSQL
    # guard is disabled, and these are not native anti-tampering claims.
    tx=InventoryTransaction;move=InventoryMovement
    tx_filter=tx.id==edge.transaction_id
    move_filter=move.id==edge.movement_id
    def effect_filter(model):
        return (model.aggregate_type=='inventory_transaction') & (model.aggregate_id==str(edge.transaction_id))
    corruptions=[
        update(tx).where(tx_filter).values(request_hash='0'*64),
        update(tx).where(tx_filter).values(source_document_id=str(case['id'])),
        update(tx).where(tx_filter).values(effective_at=event['created_at']+timedelta(seconds=1)),
        update(move).where(move_filter).values(quantity=command.quantity+Decimal('1')),
        update(move).where(move_filter).values(line_no=2),
        update(move).where(move_filter).values(from_account_id=event['to_account_id'],to_account_id=event['from_account_id']),
        update(move).where(move_filter).values(created_at=event['created_at']+timedelta(seconds=1)),
        update(OutboxEvent).where(effect_filter(OutboxEvent)).values(payload_jsonb={'forged':True}),
        update(OutboxEvent).where(effect_filter(OutboxEvent)).values(aggregate_id=str(case['id'])),
        delete(OutboxEvent).where(effect_filter(OutboxEvent)),
        update(StateTransitionEvent).where(effect_filter(StateTransitionEvent)).values(metadata_jsonb={'forged':True}),
        delete(StateTransitionEvent).where(effect_filter(StateTransitionEvent)),
        update(AuditEvent).where(effect_filter(AuditEvent)).values(after_jsonb={'forged':True}),
    ]
    if serials:
        corruptions += [delete(InventoryMovementSerial).where(InventoryMovementSerial.movement_id==edge.movement_id),
            update(InventoryMovementSerial).where(InventoryMovementSerial.movement_id==edge.movement_id)
                .values(created_at=event['created_at']+timedelta(seconds=1))]
    for statement in corruptions:
        db.execute(statement);db.flush()
        with pytest.raises(ERRORS):
            subject.verify_event_posting(db,event=event,serial_ids=serials)
        db.rollback()
        assert snapshot(db)==before
    # Queue processing can advance after the original commit without changing
    # that historical outcome. This proves no delivery-success assertion.
    db.execute(update(OutboxEvent).where(effect_filter(OutboxEvent)).values(attempts=2,
        available_at=event['created_at']+timedelta(minutes=5),updated_at=event['created_at']+timedelta(minutes=5)))
    db.flush()
    assert subject.verify_event_posting(db,event=event,serial_ids=serials)==edge
    # A review must never conceal the already-present posting of this event.
    review=dict(event,kind='verify_region',posting_transaction_id=None,posting_movement_id=None,
                movement_type=None,from_account_id=None,to_account_id=None)
    with pytest.raises(ERRORS):
        subject.verify_event_posting(db,event=review,serial_ids=serials)
    db.rollback();assert snapshot(db)==before
