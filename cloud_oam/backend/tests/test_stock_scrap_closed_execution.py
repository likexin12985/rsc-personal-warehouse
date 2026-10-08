"""Business admission must never turn a closed/unknown request into a write."""
from dataclasses import replace
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import delete, event, select

from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import bound_commands, seal_reads
from test_stock_scrap_seal_lookup import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, original, closed,
)
from test_stock_scrap_request_lookup import execute
from test_stock_scrap_recovery_approval import all_state, stock_state


def snapshot(db):
    return all_state(db), stock_state(db), tuple(db.execute(select(seal_reads.table())))


def reject(db, w, command, code, status=409, actor=None):
    actor = actor or w.actor
    w.world.current_principal = actor
    before = snapshot(db)
    def only_select(conn, cursor, statement, parameters, context, many):
        assert statement.lstrip().upper().startswith('SELECT'), statement
    event.listen(db.bind, 'before_cursor_execute', only_select)
    try:
        with pytest.raises(InventoryReadError) as error:
            bound_commands.original(db, actor=actor, request=command)
        assert error.value.code == code and error.value.status_code == status
    finally:
        event.remove(db.bind, 'before_cursor_execute', only_select)
    assert snapshot(db) == before


def test_closed_execution_is_explicit_and_writes_nothing(db, closed):
    w, _ = closed
    reject(db, w, w.command, 'stock_scrap_request_sealed')


@pytest.mark.parametrize('field', ['execution_reason', 'idempotency_key', 'request_id', 'expected_plan_hash'])
def test_changed_closed_command_is_conflict_not_a_new_execution(db, closed, field):
    w, _ = closed
    command = w.command.model_copy(update={field: 'f'*64 if field == 'expected_plan_hash' else uuid4().hex})
    reject(db, w, command, 'stock_scrap_request_conflict')


def test_closed_execution_can_explain_closure_after_write_right_removed(db, closed):
    w, _ = closed
    actor = replace(w.actor, entitlements=tuple(g for g in w.actor.entitlements if g.action != 'dispose_loss'))
    reject(db, w, w.command, 'stock_scrap_request_sealed', actor=actor)


def test_closure_does_not_bypass_current_read_authority(db, closed):
    w, _ = closed
    actor = replace(w.actor, entitlements=tuple(g for g in w.actor.entitlements if g.action != 'read'))
    reject(db, w, w.command, 'stock_loss_disposition_read_forbidden', 403, actor)


def test_orphan_closure_audit_blocks_execution_as_unknown(db, closed):
    w, _ = closed
    db.execute(delete(seal_reads.table())); db.commit()
    reject(db, w, w.command, 'stock_scrap_recovery_request_outcome_unknown', 503)


def test_already_executed_command_requires_result_recovery(db, original):
    execute(db, actor=original.actor, request=original.command); db.commit()
    reject(db, original, original.command, 'stock_scrap_request_requires_recovery')


def test_unvalidated_model_copy_is_reparsed_before_database_access(db, closed):
    w, _ = closed
    command = w.command.model_copy(update={'expected_plan_hash': 'not-a-hash'})
    def no_sql(*args, **kwargs):
        raise AssertionError('invalid command reached database')
    event.listen(db.bind, 'before_cursor_execute', no_sql)
    try:
        with pytest.raises(ValidationError):
            bound_commands.original(db, actor=w.actor, request=command)
    finally:
        event.remove(db.bind, 'before_cursor_execute', no_sql)
