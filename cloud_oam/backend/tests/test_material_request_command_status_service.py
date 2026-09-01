from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import event, func, select, text
from sqlalchemy.orm import Session

from app.demand_models import (
    ApprovalInstance,
    ApprovalStep,
    MaterialRequestCancellationLineFact,
    MaterialRequestCommand,
)
from app.formal_access import load_formal_principal
from app.formal_services import material_request_command_status as status_service
from app.formal_services.audit_chain import calculate_audit_event_hash
from app.formal_services.material_request_lifecycle import (
    MaterialRequestCancelInput,
    MaterialRequestCancellationLineInput,
    MaterialRequestLifecycleError,
    cancel_material_request,
    withdraw_material_request,
)
from app.formal_services.material_request_approval import (
    MaterialRequestApprovalInput,
    decide_material_request_approval,
)
from app.formal_services.material_request_policy import (
    ApprovalLineDecision,
    ApprovalReturnInstruction,
)
from app.foundation_models import (
    AuditEvent,
    OutboxEvent,
    Permission,
    RoleAssignment,
    RolePermission,
    StateTransitionEvent,
)
from app.models import User

from test_material_request_approval_service import (
    _approve,
    _evidence,
    _principal,
    _register_external,
    _submitted,
    _verify_external,
    approval_db,
)
from test_material_request_draft_service import (
    NOW,
    SECRET,
    _assignment,
    _person,
    _user,
)
from test_material_request_lifecycle_service import (
    _approved_request,
    _cancel_input,
    _current_actor,
    _grant_requester_lifecycle_permissions,
)


def _lookup(db: Session, actor, trace_id: str):
    return status_service.material_request_lifecycle_command_status(
        db,
        actor=actor,
        trace_request_id=trace_id,
    )


def _assert_error(exc, code: str | None = None, status: int = 503) -> None:
    assert isinstance(exc.value, MaterialRequestLifecycleError)
    assert exc.value.http_status_code == status
    if code is not None:
        assert exc.value.code == code
    assert "sql" not in exc.value.message.lower()


def _withdrawn(db: Session, *, key: str):
    world, request, _lines, version = _submitted(db, key=key)
    _grant_requester_lifecycle_permissions(db, world)
    actor = _current_actor(db, world)
    trace_id = f"trace-{key}-withdraw-command"
    expected = withdraw_material_request(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_version=version,
        reason="申请人确认需求已撤销",
        idempotency_key=f"{key}-withdraw-command-key",
        idempotency_hmac_secret=SECRET,
        trace_request_id=trace_id,
    )
    return world, actor, request, trace_id, expected


def _cancelled(db: Session, *, key: str):
    world, request, line, version = _approved_request(db, key=key)
    actor = _current_actor(db, world)
    trace_id = f"trace-{key}-cancel-command"
    expected = cancel_material_request(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_version=version,
        cancellation=_cancel_input(line),
        idempotency_key=f"{key}-cancel-command-key",
        idempotency_hmac_secret=SECRET,
        trace_request_id=trace_id,
    )
    return world, actor, request, trace_id, expected


def test_confirmed_withdraw_is_exact_and_emits_selects_only(
    approval_db: Session,
) -> None:
    db = approval_db
    _world, actor, request, trace_id, expected = _withdrawn(
        db,
        key="command-status-withdraw",
    )
    statements: list[str] = []
    engine = db.get_bind()

    def capture(_connection, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", capture)
    try:
        observed = _lookup(db, actor, trace_id)
    finally:
        event.remove(engine, "before_cursor_execute", capture)

    assert observed.lookup_status == "confirmed"
    assert observed.command is not None
    assert observed.command.action == "withdraw"
    assert observed.command.request_id == request.id == expected.request_id
    assert observed.command.request_version == expected.request_version
    assert observed.command.revision_id == expected.revision_id
    assert observed.command.approval_instance_id == expected.approval_instance_id
    assert observed.command.current_step_id is None
    assert observed.command.states["request_status"] == "withdrawn"
    assert observed.command.occurred_at.tzinfo is not None
    assert statements
    assert all(statement.lstrip().upper().startswith("SELECT") for statement in statements)


def test_confirmed_cancel_reconstructs_facts_and_returns_no_internal_json(
    approval_db: Session,
) -> None:
    db = approval_db
    _world, actor, request, trace_id, expected = _cancelled(
        db,
        key="command-status-cancel",
    )
    observed = _lookup(db, actor, trace_id)

    assert observed.lookup_status == "confirmed"
    assert observed.command is not None
    assert observed.command.action == "cancel"
    assert observed.command.request_id == request.id
    assert observed.command.request_version == expected.request_version
    assert observed.command.states == {
        "request_status": "cancelled",
        "allocation_status": "not_allocated",
        "reservation_status": "not_reserved",
        "outbound_status": "not_started",
        "shipment_status": "not_started",
        "logistics_signature_status": "not_signed",
        "oam_receipt_status": "not_occurred",
        "personal_inbound_status": "not_started",
        "notification_status": "not_started",
        "reconciliation_status": "not_started",
    }
    assert not hasattr(observed.command, "result_jsonb")
    assert not hasattr(observed.command, "idempotency_key_hash")


def test_confirmed_returned_cancel_accepts_empty_line_fact_set(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(
        db,
        key="command-status-returned-empty-cancel",
    )
    _grant_requester_lifecycle_permissions(db, world)
    step = db.scalar(
        select(ApprovalStep)
        .join(ApprovalInstance, ApprovalInstance.id == ApprovalStep.instance_id)
        .where(
            ApprovalInstance.request_id == request.id,
            ApprovalInstance.current_step_id == ApprovalStep.id,
        )
    )
    assert step is not None
    returned = decide_material_request_approval(
        db,
        actor=_principal(db, world.manager_users[0].id),
        material_request_id=request.id,
        approval_step_id=step.id,
        expected_request_version=version,
        expected_step_version=step.version,
        decision=MaterialRequestApprovalInput(
            action="return",
            return_lines=(
                ApprovalReturnInstruction(
                    lines[0].id,
                    lines[0].requested_qty,
                    "请补充需求依据",
                ),
            ),
            comment="退回申请人补充",
        ),
        idempotency_key="command-status-returned-empty-return-key",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-command-status-returned-empty-return",
    )
    actor = _current_actor(db, world)
    trace_id = "trace-command-status-returned-empty-cancel"
    expected = cancel_material_request(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_version=returned.request_version,
        cancellation=MaterialRequestCancelInput(
            reason="退回后确认无需继续申请",
            lines=(),
        ),
        idempotency_key="command-status-returned-empty-cancel-key",
        idempotency_hmac_secret=SECRET,
        trace_request_id=trace_id,
    )

    observed = _lookup(db, actor, trace_id)
    assert observed.lookup_status == "confirmed"
    assert observed.command is not None
    assert observed.command.action == "cancel"
    assert observed.command.request_id == expected.request_id
    assert observed.command.request_version == expected.request_version
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.request_id == trace_id,
            AuditEvent.action == "material_request.cancel",
        )
    )
    assert audit is not None and isinstance(audit.after_jsonb, dict)
    assert audit.after_jsonb["request_line_order"] == []


def test_not_observed_is_non_terminal_and_cross_user_does_not_enumerate(
    approval_db: Session,
) -> None:
    db = approval_db
    world, actor, _request, trace_id, _expected = _withdrawn(
        db,
        key="command-status-not-observed",
    )
    missing = _lookup(db, actor, "trace-command-status-never-observed")
    assert missing.lookup_status == "not_observed"
    assert missing.command is None
    non_lifecycle = _lookup(db, actor, "trace-command-status-not-observed")
    assert non_lifecycle.lookup_status == "not_observed"
    assert non_lifecycle.command is None

    other_person = _person(db, world.department, "状态查询其他工程师")
    other_user = _user(db, other_person)
    _assignment(
        db,
        other_user,
        world.roles["technician"],
        scope_type="person",
        scope_id=str(other_person.id),
        assigned_by=world.actor_user.id,
    )
    other_actor = load_formal_principal(db, other_user.id, now=NOW)
    cross_user = _lookup(db, other_actor, trace_id)
    assert cross_user.lookup_status == "not_observed"
    assert cross_user.command is None


def test_revoked_assignment_and_changed_authorization_version_fail_closed(
    approval_db: Session,
) -> None:
    db = approval_db
    world, actor, _request, trace_id, _expected = _withdrawn(
        db,
        key="command-status-revoked",
    )
    command = db.scalar(
        select(MaterialRequestCommand).where(
            MaterialRequestCommand.operation == "withdraw"
        )
    )
    assert command is not None
    assignment = db.get(RoleAssignment, command.actor_role_assignment_id)
    assert assignment is not None
    assignment.status = "revoked"
    assignment.revoked_at = NOW
    assignment.revoked_by = world.admin_users[0].id
    db.flush()
    with pytest.raises(MaterialRequestLifecycleError) as revoked:
        _lookup(db, actor, trace_id)
    assert revoked.value.http_status_code == 403


def test_command_authorization_version_change_fails_closed(
    approval_db: Session,
) -> None:
    db = approval_db
    world, _actor, _request, trace_id, _expected = _withdrawn(
        db,
        key="command-status-auth-version",
    )
    user = db.get(User, world.actor_user.id)
    assert user is not None
    user.authorization_version += 1
    db.flush()
    current = load_formal_principal(db, user.id, now=NOW)
    with pytest.raises(MaterialRequestLifecycleError) as changed:
        _lookup(db, current, trace_id)
    _assert_error(
        changed,
        "material_request_command_status_actor_mismatch",
        status=403,
    )


def test_action_permission_revoked_while_read_remains_fails_closed(
    approval_db: Session,
) -> None:
    db = approval_db
    world, actor, _request, trace_id, _expected = _withdrawn(
        db,
        key="command-status-withdraw-permission-revoked",
    )
    permission = db.scalar(
        select(Permission).where(
            Permission.resource == "material_request",
            Permission.action == "withdraw",
            Permission.field_code == "",
        )
    )
    assert permission is not None
    role_permission = db.scalar(
        select(RolePermission).where(
            RolePermission.role_id == world.roles["technician"].id,
            RolePermission.permission_id == permission.id,
        )
    )
    assert role_permission is not None
    db.delete(role_permission)
    db.flush()
    current = load_formal_principal(db, actor.user_id, now=NOW)
    assert current.authorization_version == actor.authorization_version
    assert current.allows(
        db,
        "material_request",
        "read",
        target_scope_type="person",
        target_scope_id=str(actor.person_id),
    )

    with pytest.raises(MaterialRequestLifecycleError) as revoked:
        _lookup(db, current, trace_id)
    assert revoked.value.http_status_code == 403


def test_read_and_action_permissions_must_use_same_technician_grant(
    approval_db: Session,
) -> None:
    db = approval_db
    _world, actor, _request, trace_id, _expected = _withdrawn(
        db,
        key="command-status-action-grant-mismatch",
    )
    original = status_service.lifecycle._require_requester_context

    def mismatched_action_grant(session, supplied, action, now):
        context = original(session, supplied, action, now)
        if action != "withdraw":
            return context
        return replace(
            context,
            technician_grant=replace(
                context.technician_grant,
                assignment_id=uuid.uuid4(),
            ),
        )

    with patch.object(
        status_service.lifecycle,
        "_require_requester_context",
        side_effect=mismatched_action_grant,
    ), pytest.raises(MaterialRequestLifecycleError) as mismatched:
        _lookup(db, actor, trace_id)
    _assert_error(
        mismatched,
        "material_request_command_status_action_forbidden",
        status=403,
    )


@pytest.mark.parametrize(
    "tamper",
    ("audit", "command_request", "transition", "cancellation_fact"),
)
def test_tampered_fact_graph_never_confirms(
    approval_db: Session,
    tamper: str,
) -> None:
    db = approval_db
    _world, actor, request, trace_id, _expected = _cancelled(
        db,
        key=f"command-status-tamper-{tamper}",
    )
    if tamper == "audit":
        row = db.scalar(select(AuditEvent).where(AuditEvent.request_id == trace_id))
        assert row is not None and isinstance(row.after_jsonb, dict)
        row.after_jsonb = {**row.after_jsonb, "reason_sha256": "0" * 64}
    elif tamper == "command_request":
        row = db.scalar(
            select(MaterialRequestCommand).where(
                MaterialRequestCommand.request_id == request.id,
                MaterialRequestCommand.operation == "cancel",
            )
        )
        assert row is not None and isinstance(row.request_jsonb, dict)
        row.request_hash = "a" * 64
        row.request_jsonb = {**row.request_jsonb, "payload_sha256": "a" * 64}
    elif tamper == "transition":
        row = db.scalar(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_id == str(request.id),
                StateTransitionEvent.to_status == "cancelled",
            )
        )
        assert row is not None
        row.reason = "tampered_transition"
    else:
        row = db.scalar(
            select(MaterialRequestCancellationLineFact).where(
                MaterialRequestCancellationLineFact.request_id == request.id
            )
        )
        assert row is not None
        row.reason = "被篡改的逐行原因"
    db.flush()

    with pytest.raises(MaterialRequestLifecycleError) as corrupted:
        _lookup(db, actor, trace_id)
    assert corrupted.value.http_status_code in {409, 503}


def test_duplicate_observed_audit_rows_fail_closed(
    approval_db: Session,
) -> None:
    db = approval_db
    _world, actor, _request, trace_id, _expected = _withdrawn(
        db,
        key="command-status-duplicate-audit",
    )
    original = db.scalar(select(AuditEvent).where(AuditEvent.request_id == trace_id))
    assert original is not None
    db.execute(text("DROP INDEX uq_audit_events_material_request_request_id_0039"))
    next_version = (
        db.scalar(
            select(func.max(AuditEvent.stream_version)).where(
                AuditEvent.stream_key == "material_request"
            )
        )
        or 0
    ) + 1
    duplicate_id = uuid.uuid4()
    duplicate_hash = calculate_audit_event_hash(
        stream_key=original.stream_key,
        event_id=duplicate_id,
        actor_user_id=original.actor_user_id,
        action=original.action,
        aggregate_type=original.aggregate_type,
        aggregate_id=original.aggregate_id,
        before_jsonb=original.before_jsonb,
        after_jsonb=original.after_jsonb,
        request_id=original.request_id,
        previous_hash=original.event_hash,
        occurred_at=original.occurred_at,
    )
    db.add(
        AuditEvent(
            id=duplicate_id,
            stream_key=original.stream_key,
            stream_version=next_version,
            actor_user_id=original.actor_user_id,
            action=original.action,
            aggregate_type=original.aggregate_type,
            aggregate_id=original.aggregate_id,
            before_jsonb=original.before_jsonb,
            after_jsonb=original.after_jsonb,
            request_id=original.request_id,
            previous_hash=original.event_hash,
            event_hash=duplicate_hash,
            occurred_at=original.occurred_at,
            created_at=datetime.now(timezone.utc),
        )
    )
    db.flush()

    with pytest.raises(MaterialRequestLifecycleError) as duplicate:
        _lookup(db, actor, trace_id)
    _assert_error(
        duplicate,
        "material_request_command_status_audit_duplicate",
    )


def test_withdraw_polluted_by_downstream_fact_never_confirms(
    approval_db: Session,
) -> None:
    db = approval_db
    _world, actor, request, trace_id, _expected = _withdrawn(
        db,
        key="command-status-withdraw-outbox-pollution",
    )
    db.add(
        OutboxEvent(
            event_type="material_request.test",
            aggregate_type="material_request",
            aggregate_id=str(request.id),
            payload_jsonb={},
            status="pending",
            attempts=0,
            idempotency_key=f"outbox-{uuid.uuid4()}",
            available_at=NOW,
            locked_at=None,
            locked_by=None,
            published_at=None,
            last_error=None,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db.flush()

    with pytest.raises(MaterialRequestLifecycleError) as polluted:
        _lookup(db, actor, trace_id)
    _assert_error(
        polluted,
        "material_request_outbox_fact_exists",
        status=412,
    )


def test_cancel_reversed_input_order_is_recoverable_and_replays_exact_body(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(
        db,
        key="command-status-cancel-order",
        two_lines=True,
    )
    _grant_requester_lifecycle_permissions(db, world)
    manager = _principal(db, world.manager_users[0].id)
    admin0 = _principal(db, world.admin_users[0].id)
    admin1 = _principal(db, world.admin_users[1].id)
    quantities = {line.id: line.requested_qty for line in lines}
    _, regional = _approve(
        db,
        actor=manager,
        request=request,
        request_version=version,
        quantities=quantities,
        key="command-status-cancel-order-region",
    )
    _, headquarters = _approve(
        db,
        actor=admin0,
        request=request,
        request_version=regional.request_version,
        quantities=quantities,
        key="command-status-cancel-order-headquarters",
    )
    evidence = _evidence(
        db,
        uploaded_by=admin0.user_id,
        marker="command-status-cancel-order-evidence",
    )
    step3, registration = _register_external(
        db,
        actor=admin0,
        request=request,
        request_version=headquarters.request_version,
        evidence=evidence,
        key="command-status-cancel-order-register",
        action="approve",
        lines=tuple(
            ApprovalLineDecision(line.id, line.requested_qty, "同意")
            for line in lines
        ),
    )
    approved = _verify_external(
        db,
        actor=admin1,
        request=request,
        step=step3,
        registration_id=registration.registration_id,
        request_version=registration.request_version,
        step_version=registration.step_version,
        key="command-status-cancel-order-verify",
    )
    actor = _current_actor(db, world)
    trace_id = "trace-command-status-cancel-order-command"
    reversed_lines = tuple(
        MaterialRequestCancellationLineInput(
            request_line_id=line.id,
            cancelled_qty=line.final_approved_qty,
            reason=f"取消第 {line.line_no} 行",
        )
        for line in reversed(lines)
    )
    cancelled = cancel_material_request(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_version=approved.request_version,
        cancellation=MaterialRequestCancelInput(
            reason="取消全部已批准明细",
            lines=reversed_lines,
        ),
        idempotency_key="command-status-cancel-order-command-key",
        idempotency_hmac_secret=SECRET,
        trace_request_id=trace_id,
    )
    replay = cancel_material_request(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_version=approved.request_version,
        cancellation=MaterialRequestCancelInput(
            reason="取消全部已批准明细",
            lines=reversed_lines,
        ),
        idempotency_key="command-status-cancel-order-command-key",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-command-status-cancel-order-replay",
    )
    assert replay.replayed is True
    assert replay.request_id == cancelled.request_id
    observed = _lookup(db, actor, trace_id)
    assert observed.lookup_status == "confirmed"
    assert observed.command is not None
    assert observed.command.action == "cancel"

    # Simulate an immutable 0037 audit event: its self-hash is valid, but it
    # predates the non-sensitive input-order recovery evidence introduced by
    # 0039.  Command replay remains exactly backward compatible while status
    # lookup refuses to guess an unrecoverable request hash.
    audit = db.scalar(select(AuditEvent).where(AuditEvent.request_id == trace_id))
    assert audit is not None and isinstance(audit.after_jsonb, dict)
    legacy_after = {
        key: value
        for key, value in audit.after_jsonb.items()
        if key not in {"request_payload_schema", "request_line_order"}
    }
    audit.after_jsonb = legacy_after
    audit.event_hash = calculate_audit_event_hash(
        stream_key=audit.stream_key,
        event_id=audit.id,
        actor_user_id=audit.actor_user_id,
        action=audit.action,
        aggregate_type=audit.aggregate_type,
        aggregate_id=audit.aggregate_id,
        before_jsonb=audit.before_jsonb,
        after_jsonb=legacy_after,
        request_id=audit.request_id,
        previous_hash=audit.previous_hash,
        occurred_at=audit.occurred_at,
    )
    db.flush()

    legacy_replay = cancel_material_request(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_version=approved.request_version,
        cancellation=MaterialRequestCancelInput(
            reason="取消全部已批准明细",
            lines=reversed_lines,
        ),
        idempotency_key="command-status-cancel-order-command-key",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-command-status-cancel-order-legacy-replay",
    )
    assert legacy_replay.replayed is True

    with pytest.raises(MaterialRequestLifecycleError) as reordered:
        cancel_material_request(
            db,
            actor=actor,
            material_request_id=request.id,
            expected_version=approved.request_version,
            cancellation=MaterialRequestCancelInput(
                reason="取消全部已批准明细",
                lines=tuple(reversed(reversed_lines)),
            ),
            idempotency_key="command-status-cancel-order-command-key",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-command-status-cancel-order-reordered",
        )
    _assert_error(
        reordered,
        "material_request_idempotency_conflict",
        status=409,
    )

    with pytest.raises(MaterialRequestLifecycleError) as legacy_status:
        _lookup(db, actor, trace_id)
    _assert_error(
        legacy_status,
        "material_request_command_status_cancel_order_missing",
    )
