"""Focused SELECT-only lookup tests; native tests prove actual seal writes."""
from dataclasses import replace
from datetime import timezone
from uuid import uuid4
import pytest
from sqlalchemy import event, select
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import seal_reads
from test_stock_scrap_request_lookup import (
    db,world,stock,allowed,evidence,regional,headquarters,approved,original,lookup,
)
from test_stock_scrap_seal_schema import original_row
from test_stock_scrap_recovery_approval import all_state,stock_state


@pytest.fixture
def closed(db,original):
    row=original_row(db,original)
    table=seal_reads.table()
    db.execute(table.insert().values(**row))
    row=dict(db.execute(select(table)).mappings().one())
    append_audit_event(db,stream_key='inventory',actor_user_id=original.actor.user_id,action='seal_scrap_request',
        aggregate_type=seal_reads.AGGREGATE,aggregate_id=str(row['id']),before_jsonb={},after_jsonb=seal_reads.payload(row),
        request_id='scrap-seal:'+str(row['id']),occurred_at=row['created_at'].replace(tzinfo=timezone.utc),
        created_at=row['created_at'].replace(tzinfo=timezone.utc))
    db.commit()
    return original,row


def test_closed_original_without_root_is_query_only_after_write_grant_removed(db,closed):
    w,row=closed
    actor=replace(w.actor,entitlements=tuple(g for g in w.actor.entitlements if g.action!='dispose_loss'))
    before=all_state(db),stock_state(db),tuple(db.execute(select(seal_reads.table())))
    def only_select(conn,cursor,statement,parameters,context,many):
        assert statement.lstrip().upper().startswith('SELECT'),statement
    event.listen(db.bind,'before_cursor_execute',only_select)
    try:
        result=lookup(db,w,actor=actor)
        assert result['request_state']=='sealed' and result['result'] is None and result['retry_allowed'] is False
        assert result['seal']['seal_id']==str(row['id']) and result['seal']['root_disposition_id'] is None
    finally:
        event.remove(db.bind,'before_cursor_execute',only_select)
    assert (all_state(db),stock_state(db),tuple(db.execute(select(seal_reads.table()))))==before


@pytest.mark.parametrize('field',['execution_reason','idempotency_key','request_id','expected_plan_hash'])
def test_changed_closed_request_is_conflict_and_never_replayable(db,closed,field):
    w,_=closed
    with pytest.raises(InventoryReadError) as error:
        lookup(db,w,w.command.model_copy(update={field:'f'*64 if field=='expected_plan_hash' else uuid4().hex}))
    assert error.value.code=='stock_scrap_request_conflict'
