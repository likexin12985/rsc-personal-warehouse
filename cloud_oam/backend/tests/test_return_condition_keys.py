"""Binding retention and exact raw-key comparison; native ownership tested separately."""
from uuid import UUID

import pytest
from sqlalchemy import delete, select, update
from app.return_condition_key_schema import ALIASES
from app.formal_services.stock_loss_corrections import return_condition_keys as keys
from app.formal_services.stock_loss_corrections import return_condition_submission as writer
from app.formal_services.stock_loss_corrections import return_condition_recovery as recovery
from test_return_condition_ledger_facts import db, quantity_command, serial_command, authority_template
from test_return_condition_submission import snapshot
from test_return_condition_submission_source import (
    world,stock,allowed,evidence,regional,headquarters,approved,route,derived,ready,parcel,
    acceptance,prepared,regional_opening,reader_tables, context,regional_source,ERRORS,
)

@pytest.mark.parametrize('stock,command_name',[('quantity','quantity_command'),('serial','serial_command')],indirect=['stock'])
def test_complete_binding_is_required_and_all_aliases_match_original_key(db,regional_source,request,command_name):
    c=regional_source; command=request.getfixturevalue(command_name)
    result=writer.submit(db,actor=c.actor,request=command); db.commit()
    events=writer.tables()['stock_condition_events']
    event=db.execute(select(events).where(events.c.id==UUID(result['event_id']))).mappings().one()
    registry=keys.table(); bound=keys.match_original(db,event=event,request=command)
    assert set(keys.aliases(command.idempotency_key).values()) <= set(bound.values())
    assert command.idempotency_key not in bound.values()
    before=snapshot(db)
    for name in ('key_token',*ALIASES):
        db.execute(update(registry).where(registry.c.event_id==event['id']).values({name:'0'*64}))
        with pytest.raises(ERRORS):
            keys.match_original(db,event=event,request=command)
        with pytest.raises(ERRORS):
            recovery.lookup(db,actor=c.actor,request=command)
        db.rollback();assert snapshot(db)==before
    for statement in (
        delete(registry).where(registry.c.event_id==event['id']),
        update(registry).where(registry.c.event_id==event['id']).values(request_hash='0'*64),
        update(registry).where(registry.c.event_id==event['id']).values(actor_user_id=c.reviewer.user_id),
    ):
        db.execute(statement)
        with pytest.raises(ERRORS): recovery.lookup(db,actor=c.actor,request=command)
        db.rollback();assert snapshot(db)==before
    assert recovery.lookup(db,actor=c.actor,request=command)['result']==result
    assert snapshot(db)==before
