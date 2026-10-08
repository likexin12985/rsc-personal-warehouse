"""Actual original scrap outcomes; corrected native bindings are tested on PG16."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4
from types import SimpleNamespace

import pytest
from sqlalchemy import event, select, text
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission, OutboxEvent
from app.stock_scrap_schemas import ScrapRequestLookup
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import request_lookup as service, execution as raw_execution
from scrap_lookup_binding_fixture import create, seed, REGISTRY
from test_stock_scrap_execution import db, world, stock, allowed, evidence, regional, headquarters, approved, request, execute_command
from test_stock_scrap_recovery_approval import all_state, stock_state, coordinates


@pytest.fixture
def original(db, approved, allowed, monkeypatch):
    create(db)
    permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
        Permission.action == 'read', Permission.field_code == ''))
    if permission is None:
        permission = Permission(resource='stock_operation', action='read', field_code='', description='Synthetic lookup')
        db.add(permission); db.flush()
    role_id = db.scalar(select(Role.id).where(Role.code == 'admin'))
    if db.scalar(select(RolePermission).where(RolePermission.role_id == role_id, RolePermission.permission_id == permission.id)) is None:
        db.add(RolePermission(role_id=role_id, permission_id=permission.id, effect='allow'))
    db.commit()
    actor = load_formal_principal(db, approved.actor.user_id)
    allowed.world.current_principal = actor
    command, _ = execute_command(db, actor, request(db, approved, monkeypatch))
    return SimpleNamespace(actor=actor, command=command, world=allowed.world)


def execute(db, *, actor, request):
    result = raw_execution.execute(db, actor=actor, request=request)
    seed(db, kind='original', command=request, identifier=result['root_disposition_id'], root=result['root_disposition_id'])
    return result


def lookup(db, w, command=None, actor=None, person=None):
    actor = actor or w.actor
    w.world.current_principal = actor
    return service.lookup(db, actor=actor, request=ScrapRequestLookup(
        operator_person_id=person or actor.person_id, original=command or w.command))


def test_original_absence_and_posted_outcome_are_read_only_without_replay(db, original):
    w = original
    before = all_state(db), stock_state(db)
    missing = lookup(db, w)
    assert missing['request_state'] == 'not_found' and missing['result'] is None and missing['retry_allowed'] is False
    assert (all_state(db), stock_state(db)) == before
    result = execute(db, actor=w.actor, request=w.command)
    db.commit()
    before = all_state(db), stock_state(db)
    actor = replace(w.actor, entitlements=tuple(g for g in w.actor.entitlements if g.action != 'dispose_loss'))
    def read_only(conn, cursor, statement, parameters, context, many):
        assert statement.lstrip().upper().startswith('SELECT'), statement
    event.listen(db.bind, 'before_cursor_execute', read_only)
    try:
        actual = lookup(db, w, actor=actor)
        assert actual['request_state'] == 'found' and actual['result'] == result and actual['retry_allowed'] is False
        assert lookup(db, w, w.command.model_copy(update=coordinates()), actor=actor)['request_state'] == 'not_found'
    finally:
        event.remove(db.bind, 'before_cursor_execute', read_only)
    assert (all_state(db), stock_state(db)) == before


@pytest.mark.parametrize('field', ['execution_reason', 'idempotency_key', 'request_id', 'expected_plan_hash'])
def test_changed_original_request_does_not_recover_posted_scrap(db, original, field):
    w = original
    execute(db, actor=w.actor, request=w.command)
    db.commit()
    value = 'f' * 64 if field == 'expected_plan_hash' else uuid4().hex
    with pytest.raises(InventoryReadError) as error:
        lookup(db, w, w.command.model_copy(update={field: value}))
    assert error.value.code == 'stock_scrap_request_conflict'


def test_wrong_person_cannot_read_original_outcome(db, original):
    with pytest.raises(InventoryReadError) as error:
        lookup(db, original, person=uuid4())
    assert error.value.status_code == 403


def test_detached_event_cannot_be_a_clean_original_scrap_miss(db, original):
    w = original
    db.add(OutboxEvent(event_type='stock_scrap.detached', aggregate_type='unknown_scrap',
        aggregate_id=str(uuid4()), idempotency_key=uuid4().hex, available_at=datetime.now(timezone.utc),
        payload_jsonb=dict(request_id=w.command.request_id, actor_user_id=w.actor.user_id)))
    db.commit()
    with pytest.raises(InventoryReadError) as error:
        lookup(db, w)
    assert error.value.status_code == 503
