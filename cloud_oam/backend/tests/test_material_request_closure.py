"""Forward close service; not a migration/runtime-admission acceptance gate."""
from datetime import datetime, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import event, func, select

from app.demand_models import MaterialRequestCommand
from app.foundation_models import AuditEvent, Permission, RolePermission
from app.inventory_models import InventoryTransaction
from app.models import User
from app.material_request_closure_schema import closures
from app.material_request_closure_schemas import MaterialRequestClosureOut
from app.formal_services import material_request_closure as service
from app.formal_services.material_request_query import MaterialRequestReadError
from test_material_request_approval_service import approval_db, _principal
from test_material_request_completion import cancelled
from test_material_request_lifecycle_service import _approved_request
from test_material_request_draft_service import SECRET


@pytest.fixture
def closure_db(approval_db):
    closures.create(approval_db.get_bind(), checkfirst=True)
    return approval_db


def grant(db, world, role='admin', action='close', effect='allow'):
    permission = db.scalar(select(Permission).where(Permission.resource == 'material_request',
        Permission.action == action, Permission.field_code == ''))
    if permission is None:
        permission = Permission(resource='material_request', action=action, field_code='', description='close service test')
        db.add(permission)
        db.flush()
    binding = db.scalar(select(RolePermission).where(RolePermission.role_id == world.roles[role].id,
        RolePermission.permission_id == permission.id))
    if binding is None:
        binding = RolePermission(role_id=world.roles[role].id, permission_id=permission.id, effect=effect)
        db.add(binding)
    else:
        binding.effect = effect
    db.flush()
    return binding


def setup(db, *, complete=True, role='admin'):
    if complete:
        world, request, line, _ = cancelled(db)
    else:
        world, request, line, _ = _approved_request(db, key='close-unfulfilled')
    grant(db, world, role, 'read')
    grant(db, world, role)
    user = world.admin_users[0] if role == 'admin' else world.manager_users[0]
    return world, request, line, _principal(db, user.id)


def close(db, actor, request, **overrides):
    args = dict(actor=actor, request_id=request.id,
        payload={'expected_request_version': request.version, 'reason': '逐项核对完成'},
        idempotency_key='close-service-key-v1', secret=SECRET, trace_request_id='close-service-trace-v1')
    args.update(overrides)
    return service.close_material_request(db, **args)


def recover(db, actor, request, **overrides):
    args = dict(actor=actor, request_id=request.id, idempotency_key='close-service-key-v1', secret=SECRET)
    args.update(overrides)
    return service.closure_command_status(db, **args)


@pytest.mark.parametrize('role', ['admin', 'provincial_manager'])
def test_cancelled_request_closes_independently_with_one_audit_and_idempotent_replay(closure_db, role):
    db = closure_db
    _, request, _, actor = setup(db, role=role)
    approval_state = (request.status, request.version, request.revision_no)
    commands = db.scalar(select(func.count()).select_from(MaterialRequestCommand))
    inventory = db.scalar(select(func.count()).select_from(InventoryTransaction))
    first = close(db, actor, request)
    assert first.business_status == 'closed' and not first.replayed
    assert first.lines[0].cancelled_qty == '2.000'
    assert first.lines[0].posted_qty == '0.000'
    assert (request.status, request.version, request.revision_no) == approval_state
    assert db.scalar(select(func.count()).select_from(MaterialRequestCommand)) == commands
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == inventory
    replay = close(db, actor, request)
    assert replay.closure_id == first.closure_id and replay.replayed
    assert db.scalar(select(func.count()).select_from(closures)) == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == 'material_request.close')) == 1


def test_approved_unfulfilled_cannot_close(closure_db):
    db = closure_db
    _, request, _, actor = setup(db, complete=False)
    with pytest.raises(MaterialRequestReadError, match='尚未全部'):
        close(db, actor, request)
    assert db.scalar(select(func.count()).select_from(closures)) == 0


@pytest.mark.parametrize('change', ['reason', 'key', 'version'])
def test_conflicting_second_close_is_refused(closure_db, change):
    db = closure_db
    _, request, _, actor = setup(db)
    close(db, actor, request)
    kwargs = {'idempotency_key': 'close-service-different-key'} if change == 'key' else {
        'payload': {'expected_request_version': request.version + (change == 'version'),
                    'reason': '不同内容' if change == 'reason' else '逐项核对完成'}}
    with pytest.raises(MaterialRequestReadError) as error:
        close(db, actor, request, **kwargs)
    assert error.value.http_status_code == 409
    assert db.scalar(select(func.count()).select_from(closures)) == 1


def test_stale_version_writes_nothing(closure_db):
    db = closure_db
    _, request, _, actor = setup(db)
    with pytest.raises(MaterialRequestReadError, match='版本已变化'):
        close(db, actor, request, payload={'expected_request_version': request.version - 1, 'reason': '核对完成'})
    assert db.scalar(select(func.count()).select_from(closures)) == 0


def test_explicit_deny_blocks_close(closure_db):
    db = closure_db
    world, request, _, actor = setup(db)
    grant(db, world, effect='deny')
    with pytest.raises(MaterialRequestReadError) as error:
        close(db, _principal(db, actor.user_id), request)
    assert error.value.http_status_code == 403
    assert db.scalar(select(func.count()).select_from(closures)) == 0


def test_revoked_close_permission_keeps_current_read_only_recovery(closure_db):
    db = closure_db
    world, request, _, actor = setup(db)
    original = close(db, actor, request)
    grant(db, world, effect='deny')
    db.get(User, actor.user_id).authorization_version += 1
    db.flush()
    current = _principal(db, actor.user_id)
    def observe(_conn, _cursor, statement, *args):
        assert statement.lstrip().upper().startswith('SELECT')
        assert 'FOR UPDATE' not in statement.upper()
    event.listen(db.bind, 'before_cursor_execute', observe)
    try:
        result = recover(db, current, request)
    finally:
        event.remove(db.bind, 'before_cursor_execute', observe)
    assert result.closure_id == original.closure_id and result.replayed
    assert close(db, current, request).closure_id == original.closure_id


def test_recovery_needs_current_read_permission_and_actor_binding(closure_db):
    db = closure_db
    world, request, _, actor = setup(db)
    close(db, actor, request)
    assert recover(db, _principal(db, world.admin_users[1].id), request) is None
    assert recover(db, actor, request, idempotency_key='close-service-unknown-key') is None
    grant(db, world, action='read', effect='deny')
    with pytest.raises(MaterialRequestReadError):
        recover(db, _principal(db, actor.user_id), request)


@pytest.mark.parametrize('change', ['evidence', 'reason', 'actor', 'audit', 'revision'])
def test_tampered_closure_cannot_be_recovered(closure_db, change):
    db = closure_db
    world, request, _, actor = setup(db)
    result = close(db, actor, request)
    if change == 'audit':
        audit = db.scalar(select(AuditEvent).where(AuditEvent.action == 'material_request.close'))
        audit.event_hash = 'f' * 64
        db.flush()
    else:
        changes = {'evidence': {'evidence_sha256': 'f' * 64}, 'reason': {'reason': '篡改原因'},
            'actor': {'actor_person_id': world.actor_person.id},
            'revision': {'request_version': request.version + 1}}[change]
        db.execute(closures.update().where(closures.c.id == result.closure_id).values(**changes))
    with pytest.raises(MaterialRequestReadError, match='审计链不一致'):
        recover(db, actor, request)


def test_invisible_request_rejected_before_closure_history(closure_db, monkeypatch):
    db = closure_db
    _, request, _, actor = setup(db)
    monkeypatch.setattr(service, '_result', lambda *args, **kwargs: pytest.fail('unauthorized history'))
    with pytest.raises(MaterialRequestReadError) as error:
        recover(db, actor, request, request_id=uuid4())
    assert error.value.http_status_code == 404


def test_audit_failure_rolls_back_independent_close_fact(closure_db, monkeypatch):
    db = closure_db
    _, request, _, actor = setup(db)
    version = request.version
    db.commit()
    def fail_audit(*args, **kwargs):
        raise RuntimeError('audit unavailable')
    monkeypatch.setattr(service, 'append_audit_event', fail_audit)
    with pytest.raises(RuntimeError, match='audit unavailable'):
        close(db, actor, request)
    db.rollback()
    assert db.scalar(select(func.count()).select_from(closures)) == 0
    assert request.version == version and request.status == 'cancelled'


@pytest.mark.parametrize('change', ['version', 'permission'])
def test_recovery_discards_result_if_current_read_access_changes(closure_db, monkeypatch, change):
    db = closure_db
    world, request, _, actor = setup(db)
    close(db, actor, request)
    original = service._result
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == 'version':
            db.get(User, actor.user_id).authorization_version += 1
            db.flush()
        else:
            grant(db, world, action='read', effect='deny')
        return result
    monkeypatch.setattr(service, '_result', changed)
    with pytest.raises(MaterialRequestReadError):
        recover(db, actor, request)


@pytest.mark.parametrize('change', ['empty', 'remaining', 'duplicate', 'naive_time', 'zero_identity', 'coerced_bool'])
def test_closed_response_requires_complete_unambiguous_evidence(change):
    line = dict(request_line_id=uuid4(), approved_qty='1.000', cancelled_qty='0.000',
        posted_qty='1.000', remaining_qty='0.000')
    value = dict(closure_id=uuid4(), request_id=uuid4(), revision_id=uuid4(), request_version=1,
        closed_at=datetime.now(timezone.utc), evidence_sha256='a' * 64, lines=[line])
    if change == 'empty': value['lines'] = []
    elif change == 'remaining': line.update(posted_qty='0.000', remaining_qty='1.000')
    elif change == 'duplicate': value['lines'] = [line, line]
    elif change == 'naive_time': value['closed_at'] = datetime(2026, 10, 6)
    elif change == 'zero_identity': value['closure_id'] = '00000000-0000-0000-0000-000000000000'
    else: value['replayed'] = 'false'
    with pytest.raises(ValidationError):
        MaterialRequestClosureOut.model_validate(value)


def test_public_closure_state_and_recovery_bind_original_content(closure_db):
    db = closure_db
    world, request, line, actor = setup(db)
    observed = service.read_closure(db, actor=actor, request_id=request.id)
    assert observed.business_status == 'open' and observed.close_permitted and observed.closure is None
    first = close(db, actor, request)
    state = service.read_closure(db, actor=actor, request_id=request.id)
    assert state.business_status == 'closed' and not state.close_permitted
    assert state.closure.closure_id == first.closure_id
    fingerprint=service.lifecycle._canonical_hash({'expected_request_version':request.version,'reason':'逐项核对完成'})
    recovered=service.closure_command_status(db,actor=actor,request_id=request.id,
        idempotency_key='close-service-key-v1',secret=SECRET,request_fingerprint=fingerprint)
    assert recovered.closure_id == first.closure_id
    with pytest.raises(MaterialRequestReadError, match='指纹不一致'):
        service.closure_command_status(db,actor=actor,request_id=request.id,
            idempotency_key='close-service-key-v1',secret=SECRET,request_fingerprint='b'*64)


def test_public_state_does_not_grant_close_to_read_only_actor(closure_db):
    db=closure_db
    world,request,line,actor=setup(db)
    grant(db,world,action='close',effect='deny')
    result=service.read_closure(db,actor=actor,request_id=request.id)
    assert result.business_status=='open' and not result.close_permitted


def test_open_state_accepts_initial_draft_version_zero():
    from app.material_request_closure_schemas import MaterialRequestClosureStateOut
    state = MaterialRequestClosureStateOut(request_id=uuid4(), request_version=0,
        business_status='open', close_permitted=False, closure=None)
    assert state.request_version == 0 and state.closure is None
