from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import event, select
from sqlalchemy.orm import Session

from app.demand_models import ApprovalAction, MaterialRequestCancellationLineFact, MaterialRequestCommand, SupplyTask
from app.formal_services import material_request_lifecycle as lifecycle
from app.formal_services import material_request_supply as supply
from app.formal_services.material_request_supply_command_status import (
    material_request_supply_command_status,
)
from app.foundation_models import AuditChainHead, AuditEvent, Permission, RolePermission, StateTransitionEvent

from test_material_request_approval_service import _principal, approval_db
from test_material_request_draft_service import SECRET
from test_material_request_supply_service import _approved_request, _create, _grant_supply_manage
from test_material_request_lifecycle_service import _cancel_input


def _seed(db, key="supply-recovery"):
    world, request, line, version = _approved_request(db, key=key)
    _grant_supply_manage(db, world)
    actor = _principal(db, world.admin_users[0].id)
    key = f"{key}-create-0001"
    result = _create(db, actor=actor, request=request, line=line, request_version=version, key=key)
    return world, actor, request, line, f"trace-{key}", result


def _update(db, actor, previous, *, key, status, reference=None, day=12):
    return supply.update_supply_task(
        db,
        actor=actor,
        material_request_id=previous.request_id,
        supply_task_id=previous.supply_task_id,
        expected_request_version=previous.request_version,
        expected_task_version=previous.task_version,
        update=supply.SupplyTaskUpdateInput(
            status=status,
            reference_no=previous.reference_no if status == "cancelled" else reference,
            expected_date=previous.expected_date if status == "cancelled" else date(2026, 9, day),
            comment="受控供给测试说明",
        ),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def _lookup(db, actor, trace):
    return material_request_supply_command_status(db, actor=actor, trace_request_id=trace)


def _audit(db, trace):
    row = db.scalar(select(AuditEvent).where(AuditEvent.request_id == trace))
    assert row is not None
    return row


def _command(db, result):
    row = db.scalar(select(MaterialRequestCommand).where(
        MaterialRequestCommand.request_id == result.request_id,
        MaterialRequestCommand.target_version == result.request_version,
    ))
    assert row is not None
    return row


def _transition(db, result):
    row = db.scalar(select(StateTransitionEvent).where(
        StateTransitionEvent.aggregate_type == "supply_task",
        StateTransitionEvent.aggregate_id == str(result.supply_task_id),
    ))
    assert row is not None
    return row


def _reject(db, actor, trace, status=503):
    with pytest.raises(supply.MaterialRequestSupplyError) as caught:
        _lookup(db, actor, trace)
    assert caught.value.http_status_code == status
    assert "sql" not in caught.value.message.lower()


def test_exact_create_lookup_is_select_only_and_does_not_autoflush_dirty_state(approval_db):
    db = approval_db
    _world, actor, _request, _line, trace, expected = _seed(db)
    permission = db.scalar(select(Permission).where(Permission.resource == "supply_task"))
    assert permission is not None
    # This unrelated object is deliberately pending and must not be inserted.
    pending = Permission(resource="unrelated_pending", action="read", field_code="", description="pending")
    db.add(pending)
    statements = []

    def observed(_connection, _cursor, statement, _parameters, _context, _executemany):
        statements.append(statement)

    event.listen(db.get_bind(), "before_cursor_execute", observed)
    try:
        with patch.object(db, "flush", side_effect=AssertionError("read flushed")), patch.object(db, "commit", side_effect=AssertionError("read committed")), patch.object(db, "rollback", side_effect=AssertionError("read rolled back")), patch.object(supply, "_take_advisory_locks", side_effect=AssertionError("read locked")), patch.object(supply, "_lock_request", side_effect=AssertionError("read locked")), patch.object(supply, "_lock_supply_graph", side_effect=AssertionError("read locked")):
            result = _lookup(db, actor, trace)
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observed)
    assert result.lookup_status == "confirmed"
    assert result.command == expected
    assert result.occurred_at is not None
    assert pending in db.new
    assert statements and all(statement.lstrip().upper().startswith("SELECT") for statement in statements)
    assert all("FOR UPDATE" not in statement.upper() and "ADVISORY" not in statement.upper() for statement in statements)


def test_not_observed_is_exact_actor_trace_only(approval_db):
    db = approval_db
    world, actor, _request, _line, trace, _created = _seed(db)
    for principal, sentinel in (
        (actor, "trace-unobserved-supply-command"),
        (_principal(db, world.admin_users[1].id), trace),
    ):
        result = _lookup(db, principal, sentinel)
        assert result.lookup_status == "not_observed"
        assert result.command is None and result.occurred_at is None


def test_historical_create_register_and_same_state_recover_after_cancel(approval_db):
    db = approval_db
    _world, actor, request, _line, trace, created = _seed(db)
    registered = _update(db, actor, created, key="supply-recovery-register-0001", status="reference_registered", reference="HQ-20260905-1")
    changed = _update(db, actor, registered, key="supply-recovery-change-date-0001", status="reference_registered", reference="HQ-20260905-1", day=13)
    cancelled = _update(db, actor, changed, key="supply-recovery-cancel-0001", status="cancelled", reference="HQ-20260905-1", day=13)
    for sentinel, expected in (
        (trace, created),
        ("trace-supply-recovery-register-0001", registered),
        ("trace-supply-recovery-change-date-0001", changed),
        ("trace-supply-recovery-cancel-0001", cancelled),
    ):
        result = _lookup(db, actor, sentinel)
        assert result.lookup_status == "confirmed"
        assert result.command == expected
    assert created.task_status == "open" and created.task_version == 0
    task = db.get(SupplyTask, created.supply_task_id)
    assert task.status == "cancelled" and task.version == 3
    assert request.version == cancelled.request_version
    assert len(set(tuple(result.state_axes.items()) for result in (created, registered, changed, cancelled))) == 1


def test_interleaved_tasks_and_other_admin_successors_preserve_historical_evidence(approval_db):
    db = approval_db
    world, actor, request, line, trace, first = _seed(db)
    second_actor = _principal(db, world.admin_users[1].id)
    second = _create(db, actor=second_actor, request=request, line=line, request_version=first.request_version, key="supply-recovery-second-task-0001", quantity="0.500")
    first_cancelled = _update(db, second_actor, replace(first, request_version=second.request_version), key="supply-recovery-other-admin-cancel-0001", status="cancelled")
    result = _lookup(db, actor, trace)
    assert result.command == first
    assert result.command.task_version == 0
    assert first_cancelled.task_version == 1


def _cancelled_request(db, task_status="cancelled"):
    world, actor, request, line, trace, created = _seed(db)
    closed = _update(db, actor, created, key="supply-recovery-terminal-task-0001", status=task_status)
    cancelled = lifecycle.cancel_material_request(
        db, actor=_principal(db, world.actor_user.id),
        material_request_id=request.id, expected_version=closed.request_version,
        cancellation=_cancel_input(line),
        idempotency_key="supply-recovery-request-cancel-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-supply-recovery-request-cancel-0001",
    )
    assert cancelled.request_version == closed.request_version + 1
    return world, actor, request, line, trace, created, closed, cancelled


@pytest.mark.parametrize("terminal", ("cancelled", "closed_no_supply"))
def test_historical_supply_commands_recover_after_verified_request_cancellation(approval_db, terminal):
    db = approval_db
    _world, actor, request, _line, trace, created, closed, cancelled = _cancelled_request(db, terminal)
    with patch.object(db, "flush", side_effect=AssertionError("reader flushed")), patch.object(db, "commit", side_effect=AssertionError("reader committed")), patch.object(db, "rollback", side_effect=AssertionError("reader rolled back")), patch.object(lifecycle, "_lock_graph", side_effect=AssertionError("reader locked")), patch.object(lifecycle, "_load_replay", side_effect=AssertionError("reader used write replay")):
        for sentinel, expected in (
            (trace, created), ("trace-supply-recovery-terminal-task-0001", closed),
        ):
            recovered = _lookup(db, actor, sentinel)
            assert recovered.lookup_status == "confirmed"
            assert recovered.command == expected
            assert recovered.command.request_status == "approved"
    assert request.status == "cancelled" and request.version == cancelled.request_version
    assert db.get(SupplyTask, closed.supply_task_id).status == terminal


def test_historical_requester_need_not_keep_current_cancel_permission(approval_db):
    db = approval_db
    world, actor, _request, _line, trace, created, _closed, _cancelled = _cancelled_request(db)
    permission = db.scalar(select(Permission).where(Permission.resource == "material_request", Permission.action == "cancel"))
    grant = db.scalar(select(RolePermission).where(
        RolePermission.permission_id == permission.id,
        RolePermission.role_id == world.roles["technician"].id,
    ))
    db.delete(grant)
    world.actor_user.authorization_version += 1
    db.flush()
    assert _lookup(db, actor, trace).command == created


@pytest.mark.parametrize("tamper", (
    "cancel_command_missing", "cancel_result_hash", "cancel_action", "cancel_fact",
    "cancel_audit", "cancel_transition", "cancel_version", "cancel_request_hash",
    "cancel_before", "cancel_line_projection", "cancel_timestamp", "cancel_role",
))
def test_request_cancel_suffix_must_have_complete_independent_causality(approval_db, tamper):
    db = approval_db
    world, actor, request, line, trace, _created, _closed, cancelled = _cancelled_request(db)
    command = _command(db, cancelled)
    if tamper == "cancel_command_missing":
        # Preserve FK integrity but remove the required cancel operation edge.
        command.operation = "update_draft"
    elif tamper == "cancel_result_hash":
        command.result_hash = "0" * 64
    elif tamper == "cancel_action":
        action = db.scalar(select(ApprovalAction).where(ApprovalAction.command_id == command.id))
        action.comment = "篡改了取消原因"
    elif tamper == "cancel_fact":
        fact = db.scalar(select(MaterialRequestCancellationLineFact).where(MaterialRequestCancellationLineFact.cancel_command_id == command.id))
        fact.reason = "篡改逐行原因"
    elif tamper == "cancel_audit":
        _audit(db, "trace-supply-recovery-request-cancel-0001").event_hash = "e" * 64
    elif tamper == "cancel_transition":
        transition = db.scalar(select(StateTransitionEvent).where(
            StateTransitionEvent.aggregate_type == "material_request",
            StateTransitionEvent.aggregate_id == str(request.id), StateTransitionEvent.to_status == "cancelled",
        ))
        transition.reason = "非正式取消原因"
    elif tamper == "cancel_version":
        request.version += 1
    elif tamper == "cancel_request_hash":
        command.request_hash = "f" * 64
        command.request_jsonb = {**command.request_jsonb, "payload_sha256": command.request_hash}
    elif tamper == "cancel_before":
        audit = _audit(db, "trace-supply-recovery-request-cancel-0001")
        audit.before_jsonb = {**audit.before_jsonb, "version": audit.before_jsonb["version"] - 1}
    elif tamper == "cancel_line_projection":
        line.cancelled_qty = Decimal("0.000")
    elif tamper == "cancel_timestamp":
        request.updated_at += timedelta(seconds=1)
    else:
        command.actor_role_assignment_id = actor.assignments[0].assignment_id
    db.flush()
    _reject(db, actor, trace)


@pytest.mark.parametrize("role", ("technician", "region_manager"))
def test_non_admin_cannot_lookup_even_unobserved_trace(approval_db, role):
    db = approval_db
    world, _actor, _request, _line, trace, _created = _seed(db)
    user = world.actor_user if role == "technician" else world.manager_users[0]
    _reject(db, _principal(db, user.id), trace, 403)


def test_stale_actor_and_command_authorization_remain_blocked(approval_db):
    db = approval_db
    _world, actor, _request, _line, trace, created = _seed(db)
    _reject(db, replace(actor, authorization_version=actor.authorization_version + 1), trace, 412)
    _reject(db, replace(actor, person_id=uuid.uuid4()), trace, 412)
    command = _command(db, created)
    command.authorization_version += 1
    db.flush()
    _reject(db, actor, trace, 412)


def test_manage_permission_cannot_be_replaced_by_read_permission(approval_db):
    db = approval_db
    world, actor, _request, _line, trace, _created = _seed(db)
    permission = db.scalar(select(Permission).where(
        Permission.resource == "supply_task", Permission.action == "manage",
    ))
    grant = db.scalar(select(RolePermission).where(
        RolePermission.role_id == world.roles["admin"].id,
        RolePermission.permission_id == permission.id,
    ))
    db.delete(grant)
    read = Permission(resource="supply_task", action="read", field_code="", description="read only")
    db.add(read)
    db.flush()
    db.add(RolePermission(role_id=world.roles["admin"].id, permission_id=read.id, effect="allow"))
    db.flush()
    _reject(db, actor, trace, 403)
    _reject(db, actor, "trace-never-observed-without-manage", 403)


def test_duplicate_actor_trace_is_ambiguous_not_confirmed(approval_db):
    db = approval_db
    _world, actor, request, line, trace, created = _seed(db)
    _create(db, actor=actor, request=request, line=line, request_version=created.request_version, key="supply-recovery-duplicate-trace-0001", quantity="0.500")
    other = _audit(db, "trace-supply-recovery-duplicate-trace-0001")
    other.request_id = trace
    db.flush()
    _reject(db, actor, trace)


@pytest.mark.parametrize("field,value", (
    ("allocation_status", "partial"),
    ("reservation_status", "reserved"),
    ("outbound_status", "partial"),
    ("shipment_status", "shipped"),
    ("logistics_signature_status", "signed"),
    ("oam_receipt_status", "received"),
    ("personal_inbound_status", "partial"),
    ("notification_status", "delivered"),
    ("reconciliation_status", "matched"),
))
def test_unknown_fulfillment_successor_does_not_masquerade_as_supply_history(approval_db, field, value):
    db = approval_db
    _world, actor, request, _line, trace, _created = _seed(db)
    # This is deliberately an in-memory read projection override, not a forged
    # database write that would have to bypass each axis's CHECK constraints.
    original = supply._fulfillment_axes

    def advanced(row):
        return {**original(row), field: value} if row.id == request.id else original(row)

    with patch.object(supply, "_fulfillment_axes", side_effect=advanced):
        _reject(db, actor, trace)


@pytest.mark.parametrize("tamper", ("head_hash", "head_version", "previous_hash", "stream_version"))
def test_rehashed_or_unanchored_audit_does_not_confirm(approval_db, tamper):
    db = approval_db
    _world, actor, _request, _line, trace, _created = _seed(db)
    audit = _audit(db, trace)
    head = db.scalar(select(AuditChainHead).where(AuditChainHead.stream_key == "material_request"))
    if tamper == "head_hash":
        head.last_hash = "f" * 64
    elif tamper == "head_version":
        head.version += 1
    elif tamper == "stream_version":
        audit.stream_version += 1
    else:
        audit.previous_hash = "f" * 64
        audit.event_hash = supply.calculate_audit_event_hash(
            stream_key=audit.stream_key, event_id=audit.id, actor_user_id=audit.actor_user_id,
            action=audit.action, aggregate_type=audit.aggregate_type, aggregate_id=audit.aggregate_id,
            before_jsonb=audit.before_jsonb, after_jsonb=audit.after_jsonb, request_id=audit.request_id,
            previous_hash=audit.previous_hash, occurred_at=audit.occurred_at,
        )
        head.last_hash = audit.event_hash
    db.flush()
    _reject(db, actor, trace)


@pytest.mark.parametrize("trace", (None, "", "bad trace", "x" * 161, 42, "\ntrace"))
def test_invalid_trace_is_rejected_without_write(approval_db, trace):
    db = approval_db
    _world, actor, _request, _line, _trace, _created = _seed(db)
    _reject(db, actor, trace, 422)


@pytest.mark.parametrize("tamper", (
    "audit_hash", "audit_after", "audit_action", "audit_stream", "command_missing",
    "command_hash", "command_payload", "command_path", "command_result",
    "transition_missing", "transition_reason", "transition_metadata", "transition_time",
    "task_version", "task_quantity", "task_reference", "request_version", "task_updated_at",
))
def test_seen_but_tampered_evidence_never_becomes_not_observed(approval_db, tamper):
    db = approval_db
    _world, actor, request, _line, trace, created = _seed(db)
    audit, command = _audit(db, trace), _command(db, created)
    task, transition = db.get(SupplyTask, created.supply_task_id), _transition(db, created)
    if tamper == "audit_hash":
        audit.event_hash = "f" * 64
    elif tamper == "audit_after":
        audit.after_jsonb = None
    elif tamper == "audit_action":
        audit.action = "material_request.supply_task.cancel"
    elif tamper == "audit_stream":
        db.add(AuditChainHead(stream_key="authentication", version=0))
        db.flush()
        audit.stream_key = "authentication"
    elif tamper == "command_missing":
        db.delete(command)
    elif tamper == "command_hash":
        command.result_hash = "0" * 64
    elif tamper == "command_payload":
        command.request_jsonb = {**command.request_jsonb, "unexpected": "evidence"}
    elif tamper == "command_path":
        command.request_reference += "/wrong"
    elif tamper == "command_result":
        command.result_jsonb = {**command.result_jsonb, "reference_no": "FORGED"}
        command.result_hash = supply._canonical_hash(command.result_jsonb)
    elif tamper == "transition_missing":
        db.delete(transition)
    elif tamper == "transition_reason":
        transition.reason = "forged_state"
    elif tamper == "transition_metadata":
        transition.metadata_jsonb = {**transition.metadata_jsonb, "request_version": 999}
    elif tamper == "transition_time":
        transition.occurred_at += timedelta(seconds=1)
    elif tamper == "task_version":
        task.version += 1
    elif tamper == "task_quantity":
        task.expected_qty += Decimal("0.001")
    elif tamper == "task_reference":
        task.reference_no = "FORGED"
    elif tamper == "request_version":
        request.version += 1
    elif tamper == "task_updated_at":
        task.updated_at += timedelta(seconds=1)
    db.flush()
    _reject(db, actor, trace)


@pytest.mark.parametrize("tamper", ("before_reference", "history_gap", "task_immutable", "extra_same_state_event"))
def test_later_history_tampering_blocks_original_create_recovery(approval_db, tamper):
    db = approval_db
    _world, actor, _request, _line, trace, created = _seed(db)
    registered = _update(db, actor, created, key="supply-recovery-later-register-0001", status="reference_registered", reference="HQ-HISTORY-001")
    same = _update(db, actor, registered, key="supply-recovery-later-same-0001", status="reference_registered", reference="HQ-HISTORY-001", day=14)
    audit = _audit(db, "trace-supply-recovery-later-same-0001")
    command = _command(db, same)
    if tamper == "before_reference":
        audit.before_jsonb = {**audit.before_jsonb, "reference_no": "FORGED"}
        audit.event_hash = supply.calculate_audit_event_hash(
            stream_key=audit.stream_key, event_id=audit.id, actor_user_id=audit.actor_user_id,
            action=audit.action, aggregate_type=audit.aggregate_type, aggregate_id=audit.aggregate_id,
            before_jsonb=audit.before_jsonb, after_jsonb=audit.after_jsonb, request_id=audit.request_id,
            previous_hash=audit.previous_hash, occurred_at=audit.occurred_at,
        )
    elif tamper == "history_gap":
        db.delete(_command(db, registered))
    elif tamper == "task_immutable":
        task = db.get(SupplyTask, created.supply_task_id)
        task.supply_type = "cross_region_transfer"
    else:
        db.add(StateTransitionEvent(
            aggregate_type="supply_task", aggregate_id=str(same.supply_task_id),
            from_status="open", to_status="reference_registered", reason="supply_task_status_changed",
            actor_id=actor.user_id, idempotency_key=supply._state_event_key(command.idempotency_key_hash, same.task_version),
            occurred_at=command.occurred_at, created_at=command.occurred_at,
            metadata_jsonb={"command_id": str(command.id)},
        ))
    db.flush()
    _reject(db, actor, trace)
