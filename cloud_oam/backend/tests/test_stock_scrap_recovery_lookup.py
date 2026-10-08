"""Exact, side-effect-free approval recovery; native stock binding is separate."""
from dataclasses import replace
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission, OutboxEvent
from app.stock_scrap_recovery_schemas import ScrapRecoveryRequestLookup
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import recovery_lookup as service
from test_stock_scrap_recovery_approval import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, found,
    submit as raw_submit, region_request, hq_request, coordinates, all_state, stock_state,
)
from scrap_lookup_binding_fixture import create, seed, REGISTRY


@pytest.fixture
def readable(db, found):
    # Isolated SQLite candidate schema, never a substitute for native registry
    # ownership or a production migration. Approvals have no inverse binding.
    create(db)
    permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
        Permission.action == 'read', Permission.field_code == ''))
    if permission is None:
        permission = Permission(resource='stock_operation', action='read', field_code='', description='Synthetic lookup')
        db.add(permission); db.flush()
    for role in db.scalars(select(Role).where(Role.code.in_(('technician', 'provincial_manager', 'admin')))):
        if db.scalar(select(RolePermission).where(RolePermission.role_id == role.id,
                RolePermission.permission_id == permission.id)) is None:
            db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
    db.commit()
    found.actors = {k: load_formal_principal(db, v.user_id) for k, v in found.actors.items()}
    return found


def submit(db, found, stage, command):
    result = raw_submit(db, found, stage, command)
    seed(db, kind=stage, command=command, identifier=result['fact_id'], root=found.scrap['root_disposition_id'])
    db.commit()
    return result


def lookup(db, w, stage, command, *, actor=None, person=None):
    actor = actor or w.actors[stage]
    w.world.current_principal = actor
    return service.lookup(db, actor=actor, request=ScrapRecoveryRequestLookup(
        operator_person_id=person or actor.person_id, original=command))


def snapshot(db):
    return all_state(db), stock_state(db), tuple(db.execute(select(REGISTRY)))


def test_all_approval_results_survive_successor_reviews_and_use_only_reads(db, readable):
    w = readable
    applied = submit(db, w, 'apply', w.application)
    r = region_request(w, applied)
    region = submit(db, w, 'regional', r)
    h = hq_request(w, applied, region)
    final = submit(db, w, 'headquarters', h)
    before = snapshot(db)
    statements = []
    def query_only(conn, cursor, statement, params, context, many):
        statements.append(statement)
        assert statement.lstrip().upper().startswith('SELECT'), statement
    event.listen(db.bind, 'before_cursor_execute', query_only)
    try:
        for stage, command, result in (('apply', w.application, applied), ('regional', r, region), ('headquarters', h, final)):
            actual = lookup(db, w, stage, command)
            assert actual['request_state'] == 'found' and actual['result'] == result
            assert actual['retry_allowed'] is False and actual['result_scope'] == 'historical_original_outcome'
            missing = lookup(db, w, stage, command.model_copy(update=coordinates()))
            assert missing['request_state'] == 'not_found' and missing['result'] is None and missing['retry_allowed'] is False
    finally:
        event.remove(db.bind, 'before_cursor_execute', query_only)
    assert statements and snapshot(db) == before


@pytest.mark.parametrize('field', ['reason', 'idempotency_key', 'request_id'])
def test_original_command_mismatch_cannot_return_a_result(db, readable, field):
    w = readable
    submit(db, w, 'apply', w.application)
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        lookup(db, w, 'apply', w.application.model_copy(update={field: uuid4().hex}))
    assert error.value.code == 'stock_scrap_recovery_request_conflict'
    assert snapshot(db) == before


def test_read_permission_suffices_after_write_grant_removed(db, readable):
    w = readable
    expected = submit(db, w, 'apply', w.application)
    actor = w.actors['apply']
    actor = replace(actor, entitlements=tuple(g for g in actor.entitlements if g.action != 'apply_scrap_recovery'))
    assert lookup(db, w, 'apply', w.application, actor=actor)['result'] == expected


@pytest.mark.parametrize('attack', ['person', 'scope', 'deny'])
def test_original_person_and_current_read_authority_required(db, readable, attack):
    w = readable
    submit(db, w, 'apply', w.application)
    actor = w.actors['apply']
    person = uuid4() if attack == 'person' else actor.person_id
    if attack == 'scope':
        actor = replace(actor, entitlements=tuple(replace(g, scope_id=str(uuid4())) for g in actor.entitlements))
    elif attack == 'deny':
        grant = next(g for g in actor.entitlements if g.resource == 'stock_operation' and g.action == 'read')
        actor = replace(actor, entitlements=actor.entitlements + (replace(grant, effect='deny'),))
    with pytest.raises(InventoryReadError) as error:
        lookup(db, w, 'apply', w.application, actor=actor, person=person)
    assert error.value.status_code == 403


@pytest.mark.parametrize('found_result', [False, True])
def test_detached_outbox_is_unknown_for_missing_and_found_requests(db, readable, found_result):
    w = readable
    if found_result:
        submit(db, w, 'apply', w.application)
    db.add(OutboxEvent(event_type='stock_scrap.detached', aggregate_type='unknown_scrap_request',
        aggregate_id=str(uuid4()), idempotency_key=uuid4().hex,
        available_at=datetime.now(timezone.utc),
        payload_jsonb=dict(request_id=w.application.request_id, actor_person_id=str(w.actors['apply'].person_id))))
    db.commit()
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        lookup(db, w, 'apply', w.application)
    assert error.value.code == 'stock_scrap_recovery_request_outcome_unknown'
    assert snapshot(db) == before


def test_mid_read_changed_snapshot_is_unknown(db, readable, monkeypatch):
    w = readable
    submit(db, w, 'apply', w.application)
    original = service._bound
    calls = []
    def changed(db):
        value = original(db)
        calls.append(value)
        return value if len(calls) == 1 else (value[0] + 1, value[1])
    monkeypatch.setattr(service, '_bound', changed)
    with pytest.raises(InventoryReadError) as error:
        lookup(db, w, 'apply', w.application)
    assert error.value.code == 'stock_scrap_recovery_request_outcome_unknown'
