"""Real scrap and independent recovery reviews; SQLite is not PG COMMIT proof."""
from dataclasses import replace
from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4
import pytest
from sqlalchemy import select, text
from app.formal_access import load_formal_principal
from app.foundation_models import AuthIdentity, Permission, Role, RolePermission
from app.inventory_models import InventorySerial, SerialCurrentPosition
from app.stock_scrap_recovery_schemas import ScrapRecoveryApply, ScrapRecoveryRegionalReview, ScrapRecoveryHeadquartersReview
from app.formal_services.stock_scrap import recovery_approval as service, recovery_authority as authority, recovery_events as events, recovery_facts as facts
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from app.formal_services.stock_scrap.tables import tables
from test_stock_scrap_execution import db, world, stock, allowed, evidence, regional, headquarters, approved, request, execute_command
from app.formal_services.stock_scrap.execution import execute
from test_stock_scrap_plan import upload
from test_work_order_removed_registration import inventory
from test_formal_access import make_role, assign


def coordinates():
    return dict(request_id=uuid4().hex, idempotency_key=uuid4().hex)


@pytest.fixture
def found(db, allowed, regional, approved, monkeypatch):
    command, _ = execute_command(db, approved.actor, request(db, approved, monkeypatch))
    result = execute(db, actor=approved.actor, request=command)
    db.commit()
    technician = make_role(db, 'technician')
    assign(db, allowed.world.user, technician, scope_type='person', scope_id=str(allowed.actor.person_id))
    db.add(AuthIdentity(id=uuid4(), user_id=allowed.actor.user_id, identity_type='mobile', provider_key='test',
        identifier_hash=uuid4().hex + uuid4().hex, hash_version=1, verified_at=datetime.now(timezone.utc), status='active'))
    for stage, role_code in [('apply', 'technician'), ('regional', 'provincial_manager'), ('headquarters', 'admin')]:
        role = db.scalar(select(Role).where(Role.code == role_code))
        permission = Permission(resource='stock_operation', action=authority.ACTIONS[stage], field_code='', description='Synthetic recovery')
        db.add(permission); db.flush()
        db.add(RolePermission(role_id=role.id, permission_id=permission.id, effect='allow'))
    db.commit()
    actors = {stage: load_formal_principal(db, actor.user_id) for stage, actor in
        [('apply', allowed.actor), ('regional', regional.actor), ('headquarters', approved.actor)]}
    allowed.world.current_principal = actors['apply']
    file = upload(db, actors['apply'], monkeypatch)
    value = ScrapRecoveryApply(action='apply_scrap_recovery', source=dict(scrap_line_id=result['scrap_line_id'],
        expected_scrap_request_hash=result['request_hash']), reason='找到原报废物料，申请恢复原冻结仓',
        evidence_file_ids=(file.id,), **coordinates())
    return SimpleNamespace(actors=actors, world=allowed.world, application=value, scrap=result)


def submit(db, found, stage, command):
    found.world.current_principal = found.actors[stage]
    result = service.submit(db, actor=found.actors[stage], request=command)
    db.commit()
    return result


def region_request(found, applied, decision='verified'):
    return ScrapRecoveryRegionalReview(action='review_scrap_recovery_region', source=found.application.source,
        recovery_request_id=applied['fact_id'], expected_request_hash=applied['request_hash'],
        decision=decision, reason='核验准确原报废及找回实物证据', **coordinates())


def hq_request(found, applied, regional, decision='approve'):
    return ScrapRecoveryHeadquartersReview(action='review_scrap_recovery_headquarters', source=found.application.source,
        recovery_request_id=applied['fact_id'], expected_request_hash=applied['request_hash'],
        regional_review_id=regional['fact_id'], expected_regional_hash=regional['request_hash'],
        decision=decision, reason='核实独立申请和区域复核，实物恢复另行执行', **coordinates())


def stock_state(db):
    return inventory(db), tuple(db.execute(select(InventorySerial.id, InventorySerial.lifecycle_status).order_by(InventorySerial.id))), tuple(db.execute(select(SerialCurrentPosition.serial_id, SerialCurrentPosition.stock_account_id,
        SerialCurrentPosition.last_movement_id).order_by(SerialCurrentPosition.serial_id)))


def all_state(db):
    names = list(facts.NAMES.values()) + ['stock_scrap_recovery_files', 'audit_events', 'state_transition_events',
        'outbox_events', 'notification_events', 'notification_person_targets']
    return tuple((name, tuple(db.execute(text('SELECT * FROM '+name+' ORDER BY 1')))) for name in names)


def test_independent_three_stage_approval_keeps_scrapped_stock_unchanged(db, found):
    before = stock_state(db)
    applied = submit(db, found, 'apply', found.application)
    region = submit(db, found, 'regional', region_request(found, applied))
    final = submit(db, found, 'headquarters', hq_request(found, applied, region))
    assert [r['status'] for r in (applied, region, final)] == ['awaiting_regional', 'awaiting_headquarters', 'approved_pending_execution']
    assert all(r['stock_effect'] == 'none' for r in (applied, region, final))
    assert stock_state(db) == before
    assert db.execute(select(tables()['stock_scrap_recovery_executions'])).all() == []


@pytest.mark.parametrize('attack', ['self', 'deny', 'scope', 'borrowed_allow', 'stale', 'source', 'evidence'])
def test_independent_current_authority_and_exact_evidence_are_required(db, found, attack):
    applied = submit(db, found, 'apply', found.application)
    value, actor = region_request(found, applied), found.actors['regional']
    if attack == 'self':
        actor = replace(actor, user_id=found.actors['apply'].user_id, person_id=found.actors['apply'].person_id,
            authorization_version=found.actors['apply'].authorization_version)
    elif attack == 'deny':
        grant = next(e for e in actor.entitlements if e.action == authority.ACTIONS['regional'])
        actor = replace(actor, entitlements=actor.entitlements + (replace(grant, effect='deny'),))
    elif attack == 'scope':
        actor = replace(actor, assignments=tuple(replace(g, scope_id=str(uuid4())) for g in actor.assignments))
    elif attack == 'borrowed_allow':
        actor = replace(actor, entitlements=tuple(replace(g, role_code='admin') for g in actor.entitlements))
    elif attack == 'source':
        value = value.model_copy(update={'source': value.source.model_copy(update={'expected_scrap_request_hash': 'f'*64})})
    elif attack == 'evidence':
        table = tables()['stock_scrap_recovery_files']
        db.execute(table.update().values(metadata_sha256='f'*64)); db.commit()
    found.world.current_principal = replace(actor, authorization_version=actor.authorization_version+1) if attack == 'stale' else actor
    before = all_state(db), stock_state(db)
    with pytest.raises((InventoryReadError, InventoryPostingError, ValueError)):
        service.submit(db, actor=actor, request=value)
    db.rollback()
    assert (all_state(db), stock_state(db)) == before


def test_returned_hq_requires_new_region_and_rejects_old_approval(db, found):
    applied = submit(db, found, 'apply', found.application)
    first = submit(db, found, 'regional', region_request(found, applied))
    returned = submit(db, found, 'headquarters', hq_request(found, applied, first, 'request_regional_review'))
    assert returned['status'] == 'awaiting_regional'
    second = submit(db, found, 'regional', region_request(found, applied))
    found.world.current_principal = found.actors['headquarters']
    with pytest.raises(InventoryReadError, match='当前准确的区域核实'):
        service.submit(db, actor=found.actors['headquarters'], request=hq_request(found, applied, first))
    db.rollback()
    final = submit(db, found, 'headquarters', hq_request(found, applied, second))
    assert final['status'] == 'approved_pending_execution'


def test_reject_replay_and_conflicting_pending_application(db, found):
    submit(db, found, 'apply', found.application)
    before = all_state(db), stock_state(db)
    for value in (found.application, found.application.model_copy(update=coordinates())):
        with pytest.raises(InventoryReadError):
            service.submit(db, actor=found.actors['apply'], request=value)
        db.rollback()
        assert (all_state(db), stock_state(db)) == before


def test_event_failure_and_late_revocation_rollback_application(db, found, monkeypatch):
    original = events.record
    before = all_state(db), stock_state(db)
    def revoke(*args, **kwargs):
        original(*args, **kwargs)
        found.world.current_principal = replace(found.actors['apply'], authorization_version=found.actors['apply'].authorization_version+1)
    monkeypatch.setattr(events, 'record', revoke)
    with pytest.raises(InventoryPostingError) as error:
        service.submit(db, actor=found.actors['apply'], request=found.application)
    assert error.value.code == 'actor_principal_stale'
    db.rollback()
    assert (all_state(db), stock_state(db)) == before
