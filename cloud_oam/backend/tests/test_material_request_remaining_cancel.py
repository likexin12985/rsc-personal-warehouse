"""Forward compensation service tests; no activated PostgreSQL DDL claim."""
from decimal import Decimal
from uuid import uuid4
import copy

import pytest
from pydantic import ValidationError
from sqlalchemy import select, func, event

from app.demand_models import MaterialRequestCommand, MaterialRequestLine
from app.foundation_models import AuditEvent, Permission, RolePermission, Role
from app.inventory_models import InventoryTransaction
from app.models import User
from app.material_request_closure_schema import closures
from app.material_request_remaining_cancel_schema import cancellations, cancellation_lines
from app.material_request_remaining_cancel_schemas import (
    MaterialRequestCancelRemainingIn, require_settled_remainder)
from app.material_request_remainder_schemas import BUCKETS
from app.formal_services import material_request_remaining_cancel as service
from app.formal_services.material_request_query import MaterialRequestReadError
from app.formal_services.material_request_lifecycle import MaterialRequestLifecycleError
from app.formal_services.material_request_remainder import remaining_fulfillment
from test_material_request_approval_service import approval_db, _principal
from test_material_request_lifecycle_service import _approved_request, _current_actor
from test_material_request_draft_service import SECRET
from test_material_request_reservation_release import release_world, _create as release, _input as release_input


@pytest.fixture
def remaining_db(approval_db):
    for table in (closures, cancellations, cancellation_lines):
        table.create(approval_db.get_bind(), checkfirst=True)
    return approval_db


def setup(db):
    world, request, line, _ = _approved_request(db, key='remaining-cancel-approved')
    return world, request, line, _current_actor(db, world)


def payload(db, actor, request):
    result = remaining_fulfillment(db, actor=actor, request_id=request.id)
    return dict(expected_request_version=request.version, reason='剩余需求不再需要，库存补偿已核对',
        lines=[dict(request_line_id=str(row.request_line_id), cancelled_qty=row.unreserved_qty)
               for row in result.lines if Decimal(row.unreserved_qty) > 0])


def cancel(db, actor, request, value=None, **overrides):
    args = dict(actor=actor, request_id=request.id, payload=value or payload(db, actor, request),
        idempotency_key='cancel-remaining-key-0001', secret=SECRET, trace_request_id='cancel-remaining-trace-0001')
    args.update(overrides)
    return service.cancel_remaining_demand(db, **args)


def recover(db, actor, request, **overrides):
    args = dict(actor=actor, request_id=request.id, idempotency_key='cancel-remaining-key-0001', secret=SECRET)
    args.update(overrides)
    return service.remaining_cancellation_command_status(db, **args)


def counts(db):
    return tuple(db.scalar(select(func.count()).select_from(table)) for table in
        (MaterialRequestCommand, InventoryTransaction))


def test_exact_cancellation_records_lines_without_rewriting_approval_or_inventory(remaining_db):
    db = remaining_db
    _, request, line, actor = setup(db)
    before = (request.status, request.version, line.final_approved_qty, line.cancelled_qty, counts(db))
    value = payload(db, actor, request)
    first = cancel(db, actor, request, value)
    assert first.lines[0].cancelled_qty == '2.000'
    assert not first.replayed
    assert (request.status, request.version, line.final_approved_qty, line.cancelled_qty, counts(db)) == before
    replay = cancel(db, actor, request, value)
    assert replay.replayed and replay.cancellation_id == first.cancellation_id
    assert db.scalar(select(func.count()).select_from(cancellations)) == 1
    assert db.scalar(select(func.count()).select_from(AuditEvent).where(AuditEvent.action == service.ACTION)) == 1
    assert db.scalar(select(cancellation_lines.c.cancelled_qty)) == Decimal('2.000')


def test_released_reservation_remains_a_separate_immutable_fact(remaining_db, release_world):
    db, _, request, original, serials, calls = release_world
    actor = _principal(db, request.requester_user_id)
    release(release_world, release_input(db, original, str(original.reserved_qty), serials))
    before = counts(db)
    first = cancel(db, actor, request)
    assert Decimal(first.lines[0].cancelled_qty) == db.get(MaterialRequestLine, original.request_line_id).final_approved_qty
    assert original.released_qty == 0 and original.status == 'reserved'
    assert counts(db) == before and len(calls) == 1


def test_unreleased_reservation_blocks_cancellation(remaining_db, release_world):
    db, _, request, original, _, calls = release_world
    actor = _principal(db, request.requester_user_id)
    line = db.get(MaterialRequestLine, original.request_line_id)
    value = dict(expected_request_version=request.version, reason='取消剩余',
        lines=[dict(request_line_id=str(line.id), cancelled_qty=format(line.final_approved_qty, '.3f'))])
    with pytest.raises(MaterialRequestReadError, match='占用'):
        cancel(db, actor, request, value)
    assert db.scalar(select(func.count()).select_from(cancellations)) == 0 and not calls


@pytest.mark.parametrize('change', ['less', 'more', 'other_line', 'version'])
def test_stale_or_inexact_quantities_write_nothing(remaining_db, change):
    db = remaining_db
    _, request, _, actor = setup(db)
    value = payload(db, actor, request)
    if change == 'version': value['expected_request_version'] -= 1
    elif change == 'other_line': value['lines'][0]['request_line_id'] = str(uuid4())
    else: value['lines'][0]['cancelled_qty'] = '1.000' if change == 'less' else '3.000'
    with pytest.raises(MaterialRequestReadError): cancel(db, actor, request, value)
    assert db.scalar(select(func.count()).select_from(cancellations)) == 0


@pytest.mark.parametrize('change', ['key', 'reason', 'quantity'])
def test_conflicting_cancellation_never_replays_as_success(remaining_db, change):
    db = remaining_db
    _, request, _, actor = setup(db)
    value = payload(db, actor, request)
    cancel(db, actor, request, value)
    changed = copy.deepcopy(value)
    kwargs = {}
    if change == 'key': kwargs['idempotency_key'] = 'another-remaining-key-0001'
    elif change == 'reason': changed['reason'] = '内容变化'
    else: changed['lines'][0]['cancelled_qty'] = '1.000'
    with pytest.raises(MaterialRequestReadError) as error: cancel(db, actor, request, changed, **kwargs)
    assert error.value.http_status_code == 409
    assert db.scalar(select(func.count()).select_from(cancellations)) == 1


def test_only_original_requester_with_current_cancel_permission_can_write(remaining_db):
    db = remaining_db
    world, request, _, actor = setup(db)
    value = payload(db, actor, request)
    with pytest.raises((MaterialRequestReadError, MaterialRequestLifecycleError)):
        cancel(db, _principal(db, world.admin_users[0].id), request, value)
    permission = db.scalar(select(Permission).where(Permission.resource == 'material_request', Permission.action == 'cancel'))
    grant = db.scalar(select(RolePermission).join(Role).where(Role.code == 'technician', RolePermission.permission_id == permission.id))
    grant.effect = 'deny'; db.flush()
    with pytest.raises(MaterialRequestLifecycleError): cancel(db, _principal(db, actor.user_id), request, value)
    assert db.scalar(select(func.count()).select_from(cancellations)) == 0


def test_read_only_recovery_survives_cancel_permission_revocation(remaining_db):
    db = remaining_db
    _, request, _, actor = setup(db)
    value = payload(db, actor, request)
    first = cancel(db, actor, request, value)
    permission = db.scalar(select(Permission).where(Permission.resource == 'material_request', Permission.action == 'cancel'))
    grant = db.scalar(select(RolePermission).join(Role).where(Role.code == 'technician', RolePermission.permission_id == permission.id))
    grant.effect = 'deny'
    db.get(User, actor.user_id).authorization_version += 1; db.flush()
    current = _principal(db, actor.user_id)
    def readonly(execution):
        assert execution.is_select and getattr(execution.statement, '_for_update_arg', None) is None
    event.listen(db, 'do_orm_execute', readonly)
    try:
        recovered = recover(db, current, request, request_fingerprint=service.lifecycle._canonical_hash(value))
    finally: event.remove(db, 'do_orm_execute', readonly)
    assert recovered.cancellation_id == first.cancellation_id and recovered.replayed
    assert cancel(db, current, request, value).cancellation_id == first.cancellation_id
    with pytest.raises(MaterialRequestReadError, match='指纹'):
        recover(db, current, request, request_fingerprint='f' * 64)


@pytest.mark.parametrize('change', ['line', 'reason', 'input_hash', 'audit', 'evidence', 'request_version'])
def test_tampered_cancellation_history_is_never_recovered(remaining_db, change):
    db = remaining_db
    _, request, _, actor = setup(db)
    cancel(db, actor, request)
    if change == 'line': db.execute(cancellation_lines.update().values(cancelled_qty=Decimal('1.000')))
    elif change == 'reason': db.execute(cancellations.update().values(reason='tampered'))
    elif change == 'input_hash': db.execute(cancellations.update().values(request_hash='a' * 64))
    elif change == 'evidence': db.execute(cancellations.update().values(evidence_jsonb={}))
    elif change == 'request_version': request.version += 1
    else: db.scalar(select(AuditEvent).where(AuditEvent.action == service.ACTION)).event_hash = 'b' * 64
    db.flush()
    with pytest.raises(MaterialRequestReadError): recover(db, actor, request)


@pytest.mark.parametrize('stage', BUCKETS[1:])
def test_any_unsettled_stage_blocks_remaining_cancellation(remaining_db, stage):
    db = remaining_db
    _, request, _, actor = setup(db)
    assessment = remaining_fulfillment(db, actor=actor, request_id=request.id).model_dump(mode='json')
    value = MaterialRequestCancelRemainingIn.model_validate(payload(db, actor, request))
    assessment['lines'][0]['unreserved_qty'] = '1.000'
    assessment['lines'][0][stage] = '1.000'
    with pytest.raises(ValueError, match='占用'):
        require_settled_remainder(assessment, value.lines)


def test_duplicate_or_zero_cancellation_line_is_invalid(remaining_db):
    _, request, _, actor = setup(remaining_db)
    value = payload(remaining_db, actor, request)
    with pytest.raises(ValidationError):
        MaterialRequestCancelRemainingIn.model_validate({**value, 'lines': value['lines'] * 2})
    value['lines'][0]['cancelled_qty'] = '0.000'
    with pytest.raises(ValidationError): MaterialRequestCancelRemainingIn.model_validate(value)


def test_effective_cancellation_drives_read_only_coverage_and_separate_closure(remaining_db):
    from app.formal_services.material_request_completion import completion_quantities
    from test_material_request_closure import grant, close
    db = remaining_db
    world, request, line, actor = setup(db)
    cancel(db, actor, request)
    def readonly(execution):
        assert execution.is_select and getattr(execution.statement, '_for_update_arg', None) is None
    event.listen(db, 'do_orm_execute', readonly)
    try:
        complete = completion_quantities(db, actor=actor, request_id=request.id)
        remaining = remaining_fulfillment(db, actor=actor, request_id=request.id)
    finally:
        event.remove(db, 'do_orm_execute', readonly)
    assert complete.quantity_coverage_complete and complete.lines[0].cancelled_qty == '2.000'
    assert remaining.lines[0].unreserved_qty == '0.000' and line.cancelled_qty == 0
    grant(db, world, action='read'); grant(db, world)
    closed = close(db, _principal(db, world.admin_users[0].id), request)
    assert closed.business_status == 'closed' and closed.lines[0].cancelled_qty == '2.000'


def test_released_cumulative_reservation_does_not_reappear_after_cancellation(remaining_db, release_world):
    db, _, request, original, serials, _ = release_world
    actor = _principal(db, request.requester_user_id)
    release(release_world, release_input(db, original, str(original.reserved_qty), serials))
    cancel(db, actor, request)
    remaining = remaining_fulfillment(db, actor=actor, request_id=request.id)
    assert remaining.lines[0].unreserved_qty == '0.000'
    assert all(getattr(remaining.lines[0], bucket) == '0.000' for bucket in BUCKETS)


def test_detail_uses_verified_effective_quantity_and_removes_fulfillment_actions(remaining_db):
    from app.formal_services.material_request_query import material_request_detail, list_material_requests
    db = remaining_db
    _, request, line, actor = setup(db)
    cancel(db, actor, request)
    detail = material_request_detail(db, actor=actor, request_id=request.id)
    assert Decimal(detail.lines[0].cancelled_qty) == Decimal('2.000')
    assert not detail.allowed_actions and line.cancelled_qty == 0
    listing = list_material_requests(db, actor=actor, limit=20)
    assert not next(row for row in listing.items if row.request_id == request.id).allowed_actions
    db.execute(cancellation_lines.update().values(cancelled_qty=Decimal('1.000')))
    with pytest.raises(MaterialRequestReadError):
        material_request_detail(db, actor=actor, request_id=request.id)
    with pytest.raises(MaterialRequestReadError):
        list_material_requests(db, actor=actor, limit=20)


def test_current_state_permission_and_cancellation_remain_separate(remaining_db):
    db=remaining_db
    world,request,_,actor=setup(db)
    state=service.read_remaining_cancellation(db,actor=actor,request_id=request.id)
    assert state.cancel_permitted and state.cancellation is None
    from test_material_request_closure import grant
    grant(db,world,action="read")
    manager=_principal(db,world.admin_users[0].id)
    state=service.read_remaining_cancellation(db,actor=manager,request_id=request.id)
    assert not state.cancel_permitted and state.cancellation is None
    first=cancel(db,actor,request)
    state=service.read_remaining_cancellation(db,actor=actor,request_id=request.id)
    assert not state.cancel_permitted and state.cancellation.cancellation_id==first.cancellation_id
