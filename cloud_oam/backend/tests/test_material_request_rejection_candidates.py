from decimal import Decimal
import pytest
from sqlalchemy import event, select
from app.foundation_models import Permission, Role, RolePermission
from app.formal_access import load_formal_principal
from app.formal_services import material_request_rejection_candidates as service
from app.formal_services.material_request_query import MaterialRequestReadError
from test_material_request_rejection_progress import (world, registration_world, receipt_world, receiving_world,
    outbound_world, create, payload, recover, register, REGISTRATION_KEY, SECRET)

pytest_plugins = ('test_material_request_picking',)


def page(world):
    db, actor, request, *_ = world
    return service.candidates(db, actor=actor, request_id=request.id)


def test_cancel_releases_exact_source_without_erasing_history(world):
    first = page(world); line = first.items[0].lines[0]
    assert Decimal(line.available_qty) == 0 and not line.register_permitted
    assert line.registrations[0].permitted_actions == ('cancel_registration', 'depart')
    create(world)
    after = page(world); line = after.items[0].lines[0]
    assert Decimal(line.available_qty) == Decimal(world[3].quantity) and line.register_permitted
    assert line.registrations[0].progress.status == 'cancelled'
    assert line.available_serials == after.items[0].detail.lines[0].rejected_serials
    new = register(world[4], key=REGISTRATION_KEY + '-second')
    final = page(world).items[0].lines[0]
    assert Decimal(final.available_qty) == 0 and len(final.registrations) == 2
    assert {h.registration.return_id for h in final.registrations} == {new.return_id, world[3].return_id}


def test_actions_and_readonly_history_follow_current_permission(world):
    db, actor, *_ = world
    departure = create(world, payload(world, 'depart'))
    assert page(world).items[0].lines[0].registrations[0].permitted_actions == ('handover',)
    perm = db.scalar(select(Permission.id).where(Permission.resource == 'stock_operation', Permission.action == 'ship_return'))
    role = db.scalar(select(Role.id).where(Role.code == 'technician'))
    db.scalar(select(RolePermission).where(RolePermission.role_id == role, RolePermission.permission_id == perm)).effect = 'deny'
    db.flush()
    statements = []
    def collect(_conn, _cursor, statement, *_): statements.append(statement)
    event.listen(db.get_bind(), 'before_cursor_execute', collect)
    try:
        item = page(world).items[0].lines[0].registrations[0]
        assert item.progress.status == 'departed' and item.progress.events[0].event_id == departure.event_id
        assert item.permitted_actions == ()
    finally: event.remove(db.get_bind(), 'before_cursor_execute', collect)
    assert all(s.lstrip().upper().startswith('SELECT') for s in statements), statements


def test_read_detects_concurrent_progress_even_without_request_version_change(world, monkeypatch):
    values = iter(('before', 'after'))
    monkeypatch.setattr(service, 'material_audit_cursor', lambda db: next(values))
    with pytest.raises(MaterialRequestReadError, match='读取期间'):
        page(world)


def test_history_corruption_does_not_release_quota(world):
    from app.material_request_rejection_progress_schema import progress
    db = world[0]
    cancellation = create(world)
    db.execute(progress.update().where(progress.c.id == cancellation.event_id).values(reason='tampered'))
    with pytest.raises(MaterialRequestReadError, match='不一致'):
        page(world)


def test_exact_input_fingerprint_required_for_recovery(world):
    from app.formal_services import material_request_rejection_return as registration
    from app.formal_services import material_request_lifecycle as lifecycle
    db, actor, request, original, registration_world = world
    command = registration_world[3]
    kwargs = dict(actor=actor, request_id=request.id, idempotency_key=REGISTRATION_KEY, secret=SECRET)
    assert registration.rejection_return_command_status(db, **kwargs,
        request_fingerprint=lifecycle._canonical_hash(command.model_dump(mode='json'))).return_id == original.return_id
    with pytest.raises(MaterialRequestReadError, match='指纹不一致'):
        registration.rejection_return_command_status(db, **kwargs, request_fingerprint='0' * 64)
    value = payload(world, 'depart'); result = create(world, value)
    assert recover(world, trace_request_id='trace-rejection-progress-command-0001',
        request_fingerprint=lifecycle._canonical_hash(value.model_dump(mode='json'))).event_id == result.event_id
    with pytest.raises(MaterialRequestReadError, match='指纹不一致'):
        recover(world, trace_request_id='trace-rejection-progress-command-0001', request_fingerprint='0' * 64)
