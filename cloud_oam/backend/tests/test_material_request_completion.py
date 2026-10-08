"""Current authorization and actual three-level approval/cancellation services."""
from decimal import Decimal
from datetime import timedelta
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.demand_models import MaterialRequestCancellationLineFact, MaterialRequestCommand
from app.foundation_models import AuditEvent
from app.models import User
from app.formal_services import material_request_completion as service
from app.formal_services.material_request_cancellation_history import verified_cancelled_quantities
from app.formal_services.material_request_lifecycle import cancel_material_request
from app.formal_services.material_request_query import MaterialRequestReadError
from test_material_request_approval_service import approval_db, _principal
from test_material_request_lifecycle_service import _approved_request, _cancel_input, _current_actor
from test_material_request_draft_service import SECRET, _person, _user, _assignment


def cancelled(db):
    world, request, line, version = _approved_request(db, key='completion-cancel')
    actor = _current_actor(db, world)
    cancel_material_request(db, actor=actor, material_request_id=request.id,
        expected_version=version, cancellation=_cancel_input(line),
        idempotency_key='completion-cancel-key', idempotency_hmac_secret=SECRET,
        trace_request_id='completion-cancel-trace')
    return world, request, line, actor


@pytest.mark.parametrize('is_cancelled', [False, True])
def test_actual_approval_and_cancellation_select_only(approval_db, is_cancelled):
    db = approval_db
    if is_cancelled:
        world, request, line, actor = cancelled(db)
    else:
        world, request, line, _ = _approved_request(db, key='completion-approved')
        actor = _current_actor(db, world)
    statements = []
    def observe(_conn, _cursor, sql, *args):
        statements.append(sql)
        assert sql.lstrip().upper().startswith('SELECT') and 'FOR UPDATE' not in sql.upper()
    event.listen(db.bind, 'before_cursor_execute', observe)
    try:
        result = service.completion_quantities(db, actor=actor, request_id=request.id)
    finally:
        event.remove(db.bind, 'before_cursor_execute', observe)
    assert result.quantity_coverage_complete is is_cancelled
    assert result.pending_inbound_orders == 0
    assert Decimal(result.lines[0].remaining_qty) == (Decimal(0) if is_cancelled else line.requested_qty)
    assert Decimal(result.lines[0].cancelled_qty) == (line.requested_qty if is_cancelled else Decimal(0))
    assert result.lines[0].posted_qty == '0.000'
    assert 'business_closed' not in result.model_dump() and 'can_close' not in result.model_dump()
    assert statements


@pytest.mark.parametrize('change', ['reason', 'command_payload', 'audit_chain', 'audit_order', 'cancelled_at'])
def test_cancellation_tampering_is_not_coverage(approval_db, change):
    db = approval_db
    _, request, _, _ = cancelled(db)
    fact = db.scalar(select(MaterialRequestCancellationLineFact))
    command = db.get(MaterialRequestCommand, fact.cancel_command_id)
    audit = db.scalar(select(AuditEvent).where(AuditEvent.action == 'material_request.cancel'))
    if change == 'reason': fact.reason = 'changed without original command'
    elif change == 'command_payload': command.request_hash = 'd'*64
    elif change == 'audit_chain': audit.event_hash = 'c'*64
    elif change == 'audit_order': audit.after_jsonb = {**audit.after_jsonb, 'request_line_order': [str(uuid4())]}
    else: request.cancelled_at += timedelta(seconds=1)
    db.flush()
    with pytest.raises(MaterialRequestReadError):
        verified_cancelled_quantities(db, request=request)


def test_projection_without_cancellation_fact_is_not_coverage(approval_db):
    db = approval_db
    _, request, line, _ = _approved_request(db, key='completion-fake-cancel')
    line.cancelled_qty = Decimal('1.000')
    db.flush()
    with pytest.raises(MaterialRequestReadError):
        verified_cancelled_quantities(db, request=request)


def test_stale_identity_rejected_before_history_reads(approval_db, monkeypatch):
    db = approval_db
    world, request, _, _ = _approved_request(db, key='completion-stale')
    actor = _current_actor(db, world)
    db.get(User, actor.user_id).authorization_version += 1
    db.flush()
    monkeypatch.setattr(service, 'verified_final_approved_quantities', lambda *args, **kwargs: pytest.fail('unauthorized history read'))
    with pytest.raises(MaterialRequestReadError, match='权限版本'):
        service.completion_quantities(db, actor=actor, request_id=request.id)


def test_unknown_request_does_not_read_history(approval_db, monkeypatch):
    db = approval_db
    world, _, _, _ = _approved_request(db, key='completion-unknown')
    monkeypatch.setattr(service, 'verified_final_approved_quantities', lambda *args, **kwargs: pytest.fail('invisible history read'))
    with pytest.raises(MaterialRequestReadError) as error:
        service.completion_quantities(db, actor=_current_actor(db, world), request_id=uuid4())
    assert error.value.http_status_code == 404


def test_other_engineer_cannot_read_quantity_evidence(approval_db, monkeypatch):
    db = approval_db
    world, request, _, _ = _approved_request(db, key='completion-other-person')
    person = _person(db, world.department, '其他工程师')
    user = _user(db, person)
    _assignment(db, user, world.roles['technician'], scope_type='person', scope_id=str(person.id),
        assigned_by=world.actor_user.id)
    db.flush()
    actor = _principal(db, user.id)
    monkeypatch.setattr(service, 'verified_final_approved_quantities', lambda *args, **kwargs: pytest.fail('cross-person history read'))
    with pytest.raises(MaterialRequestReadError) as error:
        service.completion_quantities(db, actor=actor, request_id=request.id)
    assert error.value.http_status_code == 404


@pytest.mark.parametrize('change', ['request', 'authorization'])
def test_mid_read_change_discards_result(approval_db, monkeypatch, change):
    db = approval_db
    world, request, _, _ = _approved_request(db, key='completion-read-race')
    actor = _current_actor(db, world)
    original = service.verified_final_approved_quantities
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if change == 'request': request.version += 1
        else: db.get(User, actor.user_id).authorization_version += 1
        db.flush()
        return result
    monkeypatch.setattr(service, 'verified_final_approved_quantities', changed)
    with pytest.raises(MaterialRequestReadError) as error:
        service.completion_quantities(db, actor=actor, request_id=request.id)
    assert error.value.http_status_code in (409, 412)
