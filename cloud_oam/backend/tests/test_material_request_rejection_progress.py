"""Real registration/receipt composition; PG16 guards are a separate release gate."""
from datetime import timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, select

from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission
from app.material_request_rejection_progress_schema import progress
from app.material_request_rejection_progress_schemas import RejectionProgressIn
from app.formal_services import material_request_rejection_progress as service
from app.formal_services.material_request_query import MaterialRequestReadError
from test_material_request_rejection_return import (
    world as registration_world, receipt_world, receiving_world, outbound_world,
    create as register, recover as recover_registration, KEY as REGISTRATION_KEY,
)
from test_material_request_draft_service import SECRET
from test_material_request_my_inbound import facts

pytest_plugins = ('test_material_request_picking',)
KEY = 'rejection-progress-command-0001'


@pytest.fixture
def world(registration_world):
    db, actor, request, command = registration_world
    progress.create(db.get_bind(), checkfirst=True)
    role_id = db.scalar(select(Role.id).where(Role.code == 'technician'))
    for action in service.PERMISSIONS.values():
        permission = db.scalar(select(Permission).where(Permission.resource == 'stock_operation',
            Permission.action == action, Permission.field_code == ''))
        if permission is None:
            permission = Permission(id=uuid4(), resource='stock_operation', action=action, field_code='')
            db.add(permission); db.flush()
        grant = db.scalar(select(RolePermission).where(RolePermission.role_id == role_id,
            RolePermission.permission_id == permission.id))
        if grant is None:
            db.add(RolePermission(role_id=role_id, permission_id=permission.id, effect='allow'))
        else:
            grant.effect = 'allow'
    db.flush()
    actor = load_formal_principal(db, actor.user_id)
    original = register((db, actor, request, command))
    return db, actor, request, original, (db, actor, request, command)


def payload(world, action='cancel_registration', previous=None, **changes):
    _, _, request, original, _ = world
    data = dict(expected_request_version=request.version, action=action,
        registration_request_hash=original.request_hash, reason='原拒收退回办理记录')
    if action != 'cancel_registration':
        data['physical_at'] = original.registered_at
    if previous:
        data.update(previous_event_id=previous.event_id, previous_request_hash=previous.request_hash)
    if action == 'handover':
        data.update(carrier='顺丰', tracking_no='SF-REJECTION-001')
    return RejectionProgressIn(**(data | changes))


def create(world, command=None, key=KEY, **changes):
    db, actor, request, original, _ = world
    options = dict(actor=actor, request_id=request.id, return_id=original.return_id,
        payload=command or payload(world), idempotency_key=key, secret=SECRET, trace_request_id='trace-' + key)
    return service.record_rejection_progress(db, **(options | changes))


def state(world):
    db, actor, request, original, _ = world
    return service.rejection_progress_state(db, actor=actor, request_id=request.id, return_id=original.return_id)


def recover(world, **changes):
    db, actor, request, original, _ = world
    return service.rejection_progress_command_status(db, actor=actor, request_id=request.id,
        return_id=original.return_id, **changes)


def inventory_unchanged(before, after):
    # This fixture includes the audit count at index 7; only that fact may grow.
    assert before[:7] == after[:7] and before[8:] == after[8:]


def test_cancel_preserves_registration_and_inventory_with_independent_state(world):
    before = facts(world[0]); version = world[2].version
    assert state(world).status == 'registered'
    cancelled = create(world)
    assert cancelled.action == 'cancel_registration' and not cancelled.replayed
    assert cancelled.physical_at is None and state(world).status == 'cancelled'
    assert create(world).event_id == cancelled.event_id
    original = recover_registration(world[4], idempotency_key=REGISTRATION_KEY, secret=SECRET)
    assert original.status == 'registered' and original.return_id == world[3].return_id
    assert world[2].version == version
    inventory_unchanged(before, facts(world[0]))
    with pytest.raises(MaterialRequestReadError, match='已有退回进展'):
        create(world, payload(world, 'depart'), key=KEY + '-depart')
    with pytest.raises(MaterialRequestReadError, match='已有退回进展'):
        create(world, key=KEY + '-duplicate')


def test_departure_then_handover_are_separate_nonstock_facts(world):
    before = facts(world[0]); version = world[2].version
    departure = create(world, payload(world, 'depart'))
    assert state(world).status == 'departed'
    assert departure.carrier is None and departure.tracking_no is None
    handed = create(world, payload(world, 'handover', departure), key=KEY + '-handover')
    current = state(world)
    assert current.status == 'handed_over' and len(current.events) == 2
    assert handed.previous_event_id == departure.event_id and handed.previous_request_hash == departure.request_hash
    assert recover(world, idempotency_key=KEY, secret=SECRET).action == 'depart'
    assert create(world, payload(world, 'depart')).event_id == departure.event_id
    inventory_unchanged(before, facts(world[0]))
    assert world[2].version == version
    with pytest.raises(MaterialRequestReadError, match='已有退回进展'):
        create(world, key=KEY + '-cancel')
    with pytest.raises(MaterialRequestReadError, match='实物发出事实'):
        create(world, payload(world, 'handover', departure), key=KEY + '-handover2')


def test_key_and_trace_recovery_are_select_only_even_after_write_permission_revoked(world):
    db, actor, _, original, _ = world
    departure = create(world, payload(world, 'depart'))
    permission = db.scalar(select(Permission.id).where(Permission.resource == 'stock_operation', Permission.action == 'outbound_return'))
    role = db.scalar(select(Role.id).where(Role.code == 'technician'))
    db.query(RolePermission).filter(RolePermission.role_id == role, RolePermission.permission_id == permission).update({'effect': 'deny'})
    db.flush()
    before = facts(db); statements = []
    def capture(_conn, _cursor, sql, *_): statements.append(sql)
    event.listen(db.bind, 'before_cursor_execute', capture)
    try:
        by_key = recover(world, idempotency_key=KEY, secret=SECRET)
        by_trace = recover(world, trace_request_id='trace-' + KEY)
        assert by_key == by_trace and by_key.event_id == departure.event_id and by_key.replayed
        assert state(world).status == 'departed'
        assert recover(world, trace_request_id='unknown-trace') is None
    finally:
        event.remove(db.bind, 'before_cursor_execute', capture)
    assert all(sql.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in sql.upper() for sql in statements)
    assert facts(db) == before
    assert create(world, payload(world, 'depart')).event_id == departure.event_id
    with pytest.raises(MaterialRequestReadError) as denied:
        create(world, payload(world, 'depart'), key=KEY + '-new')
    assert denied.value.category == 'forbidden'
    with pytest.raises(MaterialRequestReadError, match='原请求键'):
        create(world, payload(world, 'depart', reason='变更原内容'))


@pytest.mark.parametrize('kind', ['foreign_parent', 'wrong_hash', 'old_version', 'future', 'past', 'foreign_predecessor', 'skip_departure'])
def test_invalid_progress_is_rejected_without_writes(world, kind):
    db, _, request, original, _ = world
    command = payload(world)
    kw = {}
    if kind == 'foreign_parent': kw['return_id'] = uuid4()
    if kind == 'wrong_hash': command = payload(world, registration_request_hash='f' * 64)
    if kind == 'old_version': command = payload(world, expected_request_version=request.version - 1)
    if kind in ('future', 'past'):
        delta = timedelta(days=3650 if kind == 'future' else -1)
        command = payload(world, 'depart', physical_at=original.registered_at + delta)
    if kind in ('foreign_predecessor', 'skip_departure'):
        previous = create(world, payload(world, 'depart')) if kind == 'foreign_predecessor' else None
        command = payload(world, 'handover', previous, previous_event_id=uuid4(), previous_request_hash='b' * 64)
    before = facts(db); ids = tuple(db.scalars(select(progress.c.id)))
    with pytest.raises(MaterialRequestReadError): create(world, command, key=KEY + '-invalid', **kw)
    assert tuple(db.scalars(select(progress.c.id))) == ids and facts(db) == before


@pytest.mark.parametrize('field,value', [('evidence_sha256', '0'*64), ('request_hash', '0'*64), ('reason', '改过的原因'), ('registration_request_hash', '0'*64)])
def test_tampered_progress_cannot_recover_or_advance(world, field, value):
    db, *_ = world
    departure = create(world, payload(world, 'depart'))
    db.execute(progress.update().where(progress.c.id == departure.event_id).values(**{field: value}))
    with pytest.raises(MaterialRequestReadError, match='不一致'):
        recover(world, idempotency_key=KEY, secret=SECRET)
    with pytest.raises(MaterialRequestReadError, match='不一致'):
        create(world, payload(world, 'handover', departure), key=KEY + '-handover')


@pytest.mark.parametrize('action', ['cancel_registration', 'depart', 'handover'])
def test_each_action_requires_its_own_permission(world, action):
    db, *_ = world
    previous = create(world, payload(world, 'depart')) if action == 'handover' else None
    permission = db.scalar(select(Permission.id).where(Permission.resource == 'stock_operation',
        Permission.action == service.PERMISSIONS[action]))
    role = db.scalar(select(Role.id).where(Role.code == 'technician'))
    db.query(RolePermission).filter(RolePermission.role_id == role, RolePermission.permission_id == permission).update({'effect': 'deny'})
    db.flush(); before = facts(db)
    with pytest.raises(MaterialRequestReadError) as error:
        create(world, payload(world, action, previous), key=KEY + '-denied')
    assert error.value.category == 'forbidden' and facts(db) == before


def test_same_trace_cannot_bind_departure_and_handover(world):
    departure = create(world, payload(world, 'depart'))
    with pytest.raises(MaterialRequestReadError, match='请求编号'):
        create(world, payload(world, 'handover', departure), key=KEY + '-other', trace_request_id='trace-' + KEY)


@pytest.mark.parametrize('changes', [
    {'physical_at': '2026-10-06T00:00:00Z'}, {'carrier': 'SF'}, {'previous_event_id': uuid4()},
    {'action': 'depart'}, {'action': 'depart', 'physical_at': '2026-10-06T00:00:00'},
    {'action': 'depart', 'physical_at': 123}, {'action': 'handover', 'physical_at': '2026-10-06T00:00:00Z'},
    {'reason': '含\n换行'}, {'expected_request_version': True},
])
def test_contract_rejects_ambiguous_progress(changes):
    base = dict(expected_request_version=3, action='cancel_registration', reason='误登记取消', registration_request_hash='a'*64)
    with pytest.raises(ValidationError): RejectionProgressIn(**(base | changes))


def test_mutated_model_is_revalidated(world):
    command = payload(world).model_copy(update={'carrier': 'unexpected'})
    with pytest.raises(ValidationError): create(world, command)


def test_cancelled_quantity_and_serials_can_register_again_without_erasing_history(world):
    from app.material_request_rejection_return_schema import returns, return_serials
    db, _, _, original, registration_world = world
    before = facts(db)
    cancelled = create(world)
    replacement = register(registration_world, key=REGISTRATION_KEY + '-replacement')
    assert replacement.return_id != original.return_id
    assert replacement.quantity == original.quantity and replacement.serial_ids == original.serial_ids
    assert set(db.scalars(select(returns.c.id))) == {original.return_id, replacement.return_id}
    if original.serial_ids:
        assert set(db.scalars(select(return_serials.c.return_id))) == {original.return_id, replacement.return_id}
    assert recover_registration(registration_world, idempotency_key=REGISTRATION_KEY, secret=SECRET).return_id == original.return_id
    assert recover(world, idempotency_key=KEY, secret=SECRET).event_id == cancelled.event_id
    assert state(world).status == 'cancelled'
    with pytest.raises(MaterialRequestReadError, match='累计退回登记'):
        register(registration_world, key=REGISTRATION_KEY + '-duplicate')
    inventory_unchanged(before, facts(db))


def test_damaged_cancellation_cannot_release_registration_budget(world):
    db, _, _, _, registration_world = world
    cancelled = create(world)
    db.execute(progress.update().where(progress.c.id == cancelled.event_id).values(evidence_sha256='0' * 64))
    with pytest.raises(MaterialRequestReadError, match='不一致'):
        register(registration_world, key=REGISTRATION_KEY + '-forged-release')
