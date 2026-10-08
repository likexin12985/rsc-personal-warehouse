"""Source discovery over real service facts; SQLite does not prove native COMMIT."""
from dataclasses import replace
from uuid import UUID, uuid4
import pytest
from sqlalchemy import event, update, select
from app.formal_services.stock_scrap import recovery_sources as service
from app.formal_services.stock_scrap.tables import tables
from app.formal_services.inventory_query import InventoryReadError
from test_stock_scrap_recovery_lookup import (
    db, world, stock, allowed, evidence, regional, headquarters, approved, found, readable,
    submit, region_request, hq_request, snapshot,
)


def read(db, w, stage, *, actor=None, identifier=None):
    actor = actor or w.actors['headquarters' if stage == 'execute' else stage]
    w.world.current_principal = actor
    return service.read(db, actor=actor, stage=stage,
        scrap_line_id=identifier or UUID(w.scrap['scrap_line_id']))


def test_scoped_initial_source_is_query_only_and_not_a_stock_authorization(db, readable):
    w = readable; before = snapshot(db); statements = []
    def query_only(conn, cursor, statement, params, context, many):
        statements.append(statement); assert statement.lstrip().upper().startswith('SELECT')
    event.listen(db.bind, 'before_cursor_execute', query_only)
    try:
        for stage in ('apply', 'regional', 'headquarters', 'execute'):
            result = read(db, w, stage)
            assert result.state == 'awaiting_application' and result.is_current_scrap
            assert result.stock_effect == 'none' and result.write_authorization_provided is False
            assert result.scrap_reference == w.application.source
            from app.inventory_models import InventorySerial
            rows = tuple(db.scalars(select(InventorySerial).where(InventorySerial.id.in_(result.serial_ids)).order_by(InventorySerial.id)))
            assert tuple(item.serial_id for item in result.serials) == result.serial_ids
            assert [(item.serial_no, item.qr_code) for item in result.serials] == [(r.serial_no, r.qr_code) for r in rows]
            assert result.material_name and result.operation_no and result.requester_person_id == w.actors['apply'].person_id
            assert (result.next_reference is not None) == (stage == 'apply')
            body = result.model_dump_json()
            assert all(key not in body for key in ('idempotency_key', 'command_jsonb', 'idempotency_key_hash'))
    finally:
        event.remove(db.bind, 'before_cursor_execute', query_only)
    assert statements and snapshot(db) == before


def test_each_review_exposes_only_exact_current_predecessor(db, readable):
    w = readable
    applied = submit(db, w, 'apply', w.application)
    for stage in ('apply', 'regional', 'headquarters', 'execute'):
        result = read(db, w, stage)
        assert result.state == 'awaiting_regional'
        assert (result.next_reference is not None) == (stage == 'regional')
        if result.next_reference:
            assert result.next_reference.recovery_request_id == UUID(applied['fact_id'])
            assert result.next_reference.expected_request_hash == applied['request_hash']
    region = submit(db, w, 'regional', region_request(w, applied))
    hq = read(db, w, 'headquarters')
    assert hq.state == 'awaiting_headquarters' and hq.next_reference.regional_review_id == UUID(region['fact_id'])
    assert hq.next_reference.expected_regional_hash == region['request_hash']
    assert read(db, w, 'regional').next_reference is None
    final = submit(db, w, 'headquarters', hq_request(w, applied, region))
    result = read(db, w, 'execute')
    assert result.state == 'approved_pending_execution' and result.recovery_posting is None
    assert result.next_reference.headquarters_review_id == UUID(final['fact_id'])
    assert result.next_reference.expected_headquarters_hash == final['request_hash']
    assert read(db, w, 'headquarters').next_reference is None
    assert len(result.applications) == 1 and len(result.applications[0].regional_reviews) == 1
    assert len(result.applications[0].headquarters_reviews) == 1


def test_needs_evidence_is_a_new_application_reference_not_a_replay(db, readable):
    w = readable; applied = submit(db, w, 'apply', w.application)
    submit(db, w, 'regional', region_request(w, applied, 'needs_evidence'))
    result = read(db, w, 'apply')
    assert result.state == 'needs_evidence' and result.next_reference.stage == 'apply'
    assert set(result.next_reference.model_dump()) == {'stage', 'source'}
    assert read(db, w, 'regional').next_reference is None


def test_headquarters_return_to_region_uses_same_application_and_new_review(db, readable):
    w = readable; applied = submit(db, w, 'apply', w.application)
    region = submit(db, w, 'regional', region_request(w, applied))
    submit(db, w, 'headquarters', hq_request(w, applied, region, 'request_regional_review'))
    result = read(db, w, 'regional')
    assert result.state == 'awaiting_regional'
    assert result.next_reference.recovery_request_id == UUID(applied['fact_id'])
    assert read(db, w, 'headquarters').next_reference is None


@pytest.mark.parametrize('requested,actor_stage', [('apply','regional'), ('regional','apply'), ('headquarters','regional'), ('execute','apply')])
def test_wrong_role_cannot_probe_even_missing_identifiers(db, readable, requested, actor_stage):
    # The opening fixture's engineer also holds a regional assignment. Build
    # an explicitly single-role principal rather than mislabel a dual-role user.
    actor = readable.actors[actor_stage]
    role = {'apply':'technician','regional':'provincial_manager','headquarters':'admin'}[actor_stage]
    actor = replace(actor, assignments=tuple(g for g in actor.assignments if g.role_code == role),
        entitlements=tuple(g for g in actor.entitlements if g.role_code == role))
    with pytest.raises(InventoryReadError) as error:
        read(db, readable, requested, actor=actor, identifier=uuid4())
    assert error.value.status_code == 403


@pytest.mark.parametrize('stage', ['apply', 'regional', 'headquarters', 'execute'])
def test_write_revocation_keeps_scoped_read_but_read_revocation_denies_it(db, readable, stage):
    w = readable; actor = w.actors['headquarters' if stage == 'execute' else stage]
    no_write = replace(actor, entitlements=tuple(e for e in actor.entitlements if e.action == 'read'))
    assert read(db, w, stage, actor=no_write).stock_effect == 'none'
    no_read = replace(actor, entitlements=tuple(e for e in actor.entitlements if e.action != 'read'))
    with pytest.raises(InventoryReadError) as error: read(db, w, stage, actor=no_read)
    assert error.value.status_code == 403


def test_tampered_application_is_blocked_not_presented_as_pending_or_empty(db, readable):
    w = readable; applied = submit(db, w, 'apply', w.application)
    t = tables()['stock_scrap_recovery_requests']
    db.execute(update(t).where(t.c.id == UUID(applied['fact_id'])).values(request_hash='0'*64)); db.commit()
    with pytest.raises(InventoryReadError) as error: read(db, w, 'regional')
    assert error.value.status_code == 503
    w.world.current_principal = w.actors['regional']
    result = service.queue(db, actor=w.actors['regional'], stage='regional')
    assert len(result.items) == 1 and result.items[0].availability == 'blocked'


def test_queue_scope_and_cursor_require_no_guessed_identifiers(db, readable):
    w = readable
    for stage in ('apply','regional','headquarters','execute'):
        actor = w.actors['headquarters' if stage == 'execute' else stage]; w.world.current_principal = actor
        result = service.queue(db, actor=actor, stage=stage, limit=1)
        assert len(result.items) == 1 and result.items[0].scrap_reference == w.application.source
        assert result.next_after_id is None
        empty = service.queue(db, actor=actor, stage=stage, after_id=w.application.source.scrap_line_id)
        assert empty.items == () and empty.next_after_id is None


@pytest.mark.parametrize('role,foreign_region', [('regional',True), ('apply',True), ('apply',False)])
def test_other_region_or_person_cannot_read_exact_existing_scrap(db, readable, role, foreign_region):
    from app.formal_access import load_formal_principal
    from app.foundation_models import Organization, Person, Role
    from test_formal_access import make_organization, make_user, assign
    w = readable
    person = db.get(Person, w.actors['apply'].person_id)
    region = db.get(Organization, person.organization_id)
    if foreign_region:
        region = make_organization(db, name='Other recovery region',
            parent=db.get(Organization, region.parent_id) if region.parent_id else None, org_type='region_company')
    user, person = make_user(db, region, name='Other scoped recovery operator')
    code = 'provincial_manager' if role == 'regional' else 'technician'
    db_role = db.scalar(select(Role).where(Role.code == code))
    assign(db, user, db_role, scope_type='organization' if role == 'regional' else 'person',
        scope_id=str(region.id if role == 'regional' else person.id))
    db.commit(); actor = load_formal_principal(db, user.id); w.world.current_principal = actor
    before = snapshot(db)
    assert service.queue(db, actor=actor, stage=role).items == ()
    for identifier in (w.application.source.scrap_line_id, uuid4()):
        with pytest.raises(InventoryReadError) as error:
            service.read(db, actor=actor, stage=role, scrap_line_id=identifier)
        assert error.value.status_code == 404
    assert snapshot(db) == before
