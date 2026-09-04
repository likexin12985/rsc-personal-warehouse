from __future__ import annotations

from dataclasses import replace
from datetime import date, timedelta
from decimal import Decimal
import json
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.demand_models import (
    MaterialRequestCommand,
    MaterialSubstitution,
    SubstitutionDecision,
    SupplyTask,
)
from app.formal_access import load_formal_principal
from app.formal_services.material_request_supply import (
    MaterialRequestSupplyError,
    SupplyTaskCreateInput,
    SupplyTaskUpdateInput,
    create_supply_task,
    update_supply_task,
    validate_supply_task_audit_event,
    validate_supply_task_command_result,
)
from app.foundation_models import (
    AuditEvent,
    NotificationEvent,
    OutboxEvent,
    Permission,
    RoleAssignment,
    RolePermission,
    StateTransitionEvent,
)
from app.inventory_models import InventoryTransaction

from test_material_request_approval_service import _principal, approval_db
from test_material_request_draft_service import NOW, SECRET
from test_material_request_lifecycle_service import _approved_request


def _grant_supply_manage(db: Session, world, *, role_code: str = "admin") -> None:
    permission = db.scalar(
        select(Permission).where(
            Permission.resource == "supply_task",
            Permission.action == "manage",
            Permission.field_code == "",
        )
    )
    if permission is None:
        permission = Permission(
            resource="supply_task",
            action="manage",
            field_code="",
            description="supply service test",
        )
        db.add(permission)
        db.flush()
    role = world.roles[role_code]
    if db.scalar(
        select(RolePermission.id).where(
            RolePermission.role_id == role.id,
            RolePermission.permission_id == permission.id,
        )
    ) is None:
        db.add(
            RolePermission(
                role_id=role.id,
                permission_id=permission.id,
                effect="allow",
            )
        )
    db.flush()


def _create(
    db: Session,
    *,
    actor,
    request,
    line,
    request_version: int,
    key: str,
    quantity: str = "1.250",
    reference_no: str | None = None,
    note: str = "",
):
    return create_supply_task(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_request_version=request_version,
        plan=SupplyTaskCreateInput(
            request_line_id=line.id,
            supply_type="headquarters_replenishment",
            reference_no=reference_no,
            expected_qty=Decimal(quantity),
            expected_date=None,
            note=note,
        ),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def _assert_error(caught, code: str, status: int) -> None:
    assert caught.value.code == code
    assert caught.value.http_status_code == status
    assert "sql" not in caught.value.message.lower()


def _downstream_counts(db: Session) -> tuple[int, int, int]:
    return (
        db.scalar(select(func.count()).select_from(InventoryTransaction)),
        db.scalar(select(func.count()).select_from(NotificationEvent)),
        db.scalar(select(func.count()).select_from(OutboxEvent)),
    )


def test_supply_reads_substitutions_under_parent_lock_without_update_privilege(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(db, key="supply-parent-lock")
    _grant_supply_manage(db, world)
    actor = _principal(db, world.admin_users[0].id)
    statements: list[str] = []
    original_scalar, original_scalars = db.scalar, db.scalars

    def scalar(statement, *args, **kwargs):
        statements.append(str(statement.compile(dialect=postgresql.dialect())))
        return original_scalar(statement, *args, **kwargs)

    def scalars(statement, *args, **kwargs):
        statements.append(str(statement.compile(dialect=postgresql.dialect())))
        return original_scalars(statement, *args, **kwargs)

    with patch.object(db, "scalar", side_effect=scalar), patch.object(
        db, "scalars", side_effect=scalars
    ):
        _create(db, actor=actor, request=request, line=line,
                request_version=version, key="supply-select-only-substitutions")

    parent_locks = [index for index, sql in enumerate(statements)
                    if "FROM material_requests " in sql and "FOR UPDATE" in sql]
    substitution_reads = [index for index, sql in enumerate(statements)
                          if "FROM substitution_decisions" in sql]
    assert parent_locks and substitution_reads
    assert min(parent_locks) < min(substitution_reads)
    assert all("FOR UPDATE" not in statements[index] for index in substitution_reads)


def test_create_supply_plan_is_final_approval_bounded_and_exactly_replayable(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(
        db, key="supply-create-approved"
    )
    _grant_supply_manage(db, world)
    actor = _principal(db, world.admin_users[0].id)
    downstream_before = _downstream_counts(db)

    with patch.object(
        db, "commit", side_effect=AssertionError("service committed")
    ), patch.object(
        db, "rollback", side_effect=AssertionError("service rolled back")
    ):
        result = _create(
            db,
            actor=actor,
            request=request,
            line=line,
            request_version=version,
            key="supply-create-idempotency-0001",
            note="总部补货计划说明",
        )
        replay = _create(
            db,
            actor=actor,
            request=request,
            line=line,
            request_version=version,
            key="supply-create-idempotency-0001",
            note="总部补货计划说明",
        )

    task = db.get(SupplyTask, result.supply_task_id)
    command = db.scalar(
        select(MaterialRequestCommand).where(
            MaterialRequestCommand.request_id == request.id,
            MaterialRequestCommand.operation == "create_supply_task",
        )
    )
    audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.aggregate_id == str(request.id),
            AuditEvent.action == "material_request.supply_task.create",
        )
    )
    state = db.scalar(
        select(StateTransitionEvent).where(
            StateTransitionEvent.aggregate_type == "supply_task",
            StateTransitionEvent.aggregate_id == str(result.supply_task_id),
        )
    )
    assert task is not None and command is not None and audit is not None
    assert state is not None
    assert replay.replayed is True
    assert replace(replay, replayed=False) == result
    assert result.action == "create_supply_task"
    assert result.request_status == request.status == "approved"
    assert result.request_version == request.version == version + 1
    assert result.task_status == task.status == "open"
    assert result.task_version == task.version == 0
    assert result.request_line_id == task.request_line_id == line.id
    assert task.substitution_decision_id is None
    assert task.expected_qty == task.original_equivalent_qty == Decimal("1.250")
    assert result.state_axes == {
        "request_status": "approved",
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
    assert set(command.request_jsonb) == {
        "schema",
        "operation",
        "request_id",
        "revision_id",
        "revision_no",
        "request_line_id",
        "supply_task_id",
        "task_no",
        "target_version",
        "target_task_version",
        "payload_sha256",
        "comment_sha256",
        "sensitive_fields",
    }
    assert len(command.result_jsonb) == 24
    assert command.result_jsonb["expected_qty"] == "1.250"
    assert command.result_jsonb["substitution_decision_id"] is None
    assert "总部补货计划说明" not in json.dumps(
        command.request_jsonb, ensure_ascii=False
    )
    assert "总部补货计划说明" not in json.dumps(
        audit.after_jsonb, ensure_ascii=False
    )
    assert audit.before_jsonb == {}
    assert audit.after_jsonb["command_id"] == str(command.id)
    assert audit.after_jsonb["supply_task_id"] == str(task.id)
    assert audit.after_jsonb["task_status"] == "open"
    assert state.from_status is None and state.to_status == "open"
    assert state.metadata_jsonb["command_id"] == str(command.id)
    assert validate_supply_task_command_result(command) == result
    validate_supply_task_audit_event(audit, command=command, result=result)
    assert _downstream_counts(db) == downstream_before

    audit.event_hash = "0" * 64
    db.flush()
    with pytest.raises(MaterialRequestSupplyError) as audit_tamper:
        _create(
            db,
            actor=actor,
            request=request,
            line=line,
            request_version=version,
            key="supply-create-idempotency-0001",
            note="总部补货计划说明",
        )
    _assert_error(
        audit_tamper,
        "material_request_supply_idempotency_record_invalid",
        503,
    )


def test_update_same_state_transition_and_cancel_keep_fulfillment_separate(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(
        db, key="supply-update-cancel"
    )
    _grant_supply_manage(db, world)
    actor = _principal(db, world.admin_users[0].id)
    created = _create(
        db,
        actor=actor,
        request=request,
        line=line,
        request_version=version,
        key="supply-update-create-0001",
        quantity="2.000",
    )
    registered = update_supply_task(
        db,
        actor=actor,
        material_request_id=request.id,
        supply_task_id=created.supply_task_id,
        expected_request_version=created.request_version,
        expected_task_version=created.task_version,
        update=SupplyTaskUpdateInput(
            status="reference_registered",
            reference_no="HQ-REF-20260905-001",
            expected_date=date(2026, 9, 12),
            comment="已登记总部补货参考号",
        ),
        idempotency_key="supply-update-register-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-supply-update-register",
    )
    same_state = update_supply_task(
        db,
        actor=actor,
        material_request_id=request.id,
        supply_task_id=created.supply_task_id,
        expected_request_version=registered.request_version,
        expected_task_version=registered.task_version,
        update=SupplyTaskUpdateInput(
            status="reference_registered",
            reference_no="HQ-REF-20260905-001",
            expected_date=date(2026, 9, 13),
            comment="预计到货日期调整",
        ),
        idempotency_key="supply-update-date-only-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-supply-update-date-only",
    )
    replay = update_supply_task(
        db,
        actor=actor,
        material_request_id=request.id,
        supply_task_id=created.supply_task_id,
        expected_request_version=registered.request_version,
        expected_task_version=registered.task_version,
        update=SupplyTaskUpdateInput(
            status="reference_registered",
            reference_no="HQ-REF-20260905-001",
            expected_date=date(2026, 9, 13),
            comment="预计到货日期调整",
        ),
        idempotency_key="supply-update-date-only-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-supply-update-date-replay",
    )
    with pytest.raises(MaterialRequestSupplyError) as cancel_metadata:
        update_supply_task(
            db,
            actor=actor,
            material_request_id=request.id,
            supply_task_id=created.supply_task_id,
            expected_request_version=same_state.request_version,
            expected_task_version=same_state.task_version,
            update=SupplyTaskUpdateInput(
                status="cancelled",
                reference_no="HQ-REF-CHANGED-WHILE-CANCELLING",
                expected_date=date(2026, 9, 14),
                comment="不能借取消动作修改计划元数据",
            ),
            idempotency_key="supply-cancel-metadata-change-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-supply-cancel-metadata-change",
        )
    _assert_error(
        cancel_metadata,
        "supply_task_cancel_metadata_changed",
        409,
    )
    cancelled = update_supply_task(
        db,
        actor=actor,
        material_request_id=request.id,
        supply_task_id=created.supply_task_id,
        expected_request_version=same_state.request_version,
        expected_task_version=same_state.task_version,
        update=SupplyTaskUpdateInput(
            status="cancelled",
            reference_no="HQ-REF-20260905-001",
            expected_date=date(2026, 9, 13),
            comment="供给计划取消，未发生实物履约",
        ),
        idempotency_key="supply-cancel-idempotency-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-supply-cancel",
    )
    cancel_replay = update_supply_task(
        db,
        actor=actor,
        material_request_id=request.id,
        supply_task_id=created.supply_task_id,
        expected_request_version=same_state.request_version,
        expected_task_version=same_state.task_version,
        update=SupplyTaskUpdateInput(
            status="cancelled",
            reference_no="HQ-REF-20260905-001",
            expected_date=date(2026, 9, 13),
            comment="供给计划取消，未发生实物履约",
        ),
        idempotency_key="supply-cancel-idempotency-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-supply-cancel-replay",
    )

    task = db.get(SupplyTask, created.supply_task_id)
    assert task is not None
    assert registered.task_version == 1
    assert same_state.task_version == 2
    assert replay.replayed is True
    assert cancel_replay.replayed is True
    assert cancelled.action == "cancel_supply_task"
    assert cancelled.task_status == task.status == "cancelled"
    assert cancelled.task_version == task.version == 3
    assert task.cancelled_by_user_id == actor.user_id
    assert task.cancelled_at is not None
    assert task.reference_no == "HQ-REF-20260905-001"
    assert task.expected_date == date(2026, 9, 13)
    assert request.status == "approved"
    assert request.version == version + 4
    events = tuple(
        db.scalars(
            select(StateTransitionEvent)
            .where(StateTransitionEvent.aggregate_id == str(task.id))
            .order_by(StateTransitionEvent.occurred_at, StateTransitionEvent.id)
        ).all()
    )
    events = tuple(
        sorted(events, key=lambda row: row.metadata_jsonb["task_version"])
    )
    assert [(row.from_status, row.to_status) for row in events] == [
        (None, "open"),
        ("open", "reference_registered"),
        ("reference_registered", "cancelled"),
    ]
    assert db.scalar(
        select(func.count()).select_from(MaterialRequestCommand).where(
            MaterialRequestCommand.operation.in_(
                (
                    "create_supply_task",
                    "update_supply_task",
                    "cancel_supply_task",
                )
            )
        )
    ) == 4
    assert _downstream_counts(db) == (0, 0, 0)

    with pytest.raises(MaterialRequestSupplyError) as terminal:
        update_supply_task(
            db,
            actor=actor,
            material_request_id=request.id,
            supply_task_id=task.id,
            expected_request_version=request.version,
            expected_task_version=task.version,
            update=SupplyTaskUpdateInput(
                status="open",
                reference_no=None,
                expected_date=None,
                comment="",
            ),
            idempotency_key="supply-terminal-reopen-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-supply-terminal-reopen",
        )
    _assert_error(terminal, "supply_task_terminal", 409)


def test_create_rejects_stale_version_overplan_and_changed_idempotency_payload(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(
        db, key="supply-create-guards"
    )
    _grant_supply_manage(db, world)
    actor = _principal(db, world.admin_users[0].id)
    first = _create(
        db,
        actor=actor,
        request=request,
        line=line,
        request_version=version,
        key="supply-create-guard-key-0001",
        quantity="1.500",
    )

    with pytest.raises(MaterialRequestSupplyError) as idempotency:
        _create(
            db,
            actor=actor,
            request=request,
            line=line,
            request_version=version,
            key="supply-create-guard-key-0001",
            quantity="1.250",
        )
    _assert_error(
        idempotency, "material_request_supply_idempotency_conflict", 409
    )

    with pytest.raises(MaterialRequestSupplyError) as stale:
        _create(
            db,
            actor=actor,
            request=request,
            line=line,
            request_version=version,
            key="supply-create-stale-version-0001",
            quantity="0.250",
        )
    _assert_error(stale, "material_request_version_conflict", 409)

    with pytest.raises(MaterialRequestSupplyError) as quantity:
        _create(
            db,
            actor=actor,
            request=request,
            line=line,
            request_version=first.request_version,
            key="supply-create-overplan-0001",
            quantity="0.501",
        )
    _assert_error(
        quantity, "material_request_supply_quantity_exceeds_approved", 409
    )
    assert db.scalar(select(func.count()).select_from(SupplyTask)) == 1


def test_manage_permission_does_not_replace_headquarters_admin_scope(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(
        db, key="supply-permission-scope"
    )
    admin_without_manage = _principal(db, world.admin_users[0].id)
    with pytest.raises(MaterialRequestSupplyError) as missing:
        _create(
            db,
            actor=admin_without_manage,
            request=request,
            line=line,
            request_version=version,
            key="supply-admin-missing-manage-0001",
        )
    _assert_error(missing, "material_request_supply_manage_forbidden", 403)

    _grant_supply_manage(db, world, role_code="technician")
    technician = load_formal_principal(db, world.actor_user.id, now=NOW)
    with pytest.raises(MaterialRequestSupplyError) as role:
        _create(
            db,
            actor=technician,
            request=request,
            line=line,
            request_version=version,
            key="supply-technician-manage-0001",
        )
    _assert_error(role, "material_request_supply_manage_forbidden", 403)

    _grant_supply_manage(db, world)
    current_admin = _principal(db, world.admin_users[0].id)
    world.admin_users[0].authorization_version += 1
    db.flush()
    with pytest.raises(MaterialRequestSupplyError) as stale:
        _create(
            db,
            actor=current_admin,
            request=request,
            line=line,
            request_version=version,
            key="supply-admin-stale-principal-0001",
        )
    _assert_error(
        stale, "material_request_supply_actor_principal_stale", 412
    )


def test_create_requires_current_final_approval_and_replay_fails_closed_on_tamper(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(
        db, key="supply-final-and-tamper"
    )
    _grant_supply_manage(db, world)
    actor = _principal(db, world.admin_users[0].id)
    with pytest.raises(MaterialRequestSupplyError) as wrong_line:
        create_supply_task(
            db,
            actor=actor,
            material_request_id=request.id,
            expected_request_version=version,
            plan=SupplyTaskCreateInput(
                request_line_id=uuid.uuid4(),
                supply_type="headquarters_replenishment",
                reference_no=None,
                expected_qty=Decimal("1.000"),
                expected_date=None,
                note="",
            ),
            idempotency_key="supply-wrong-current-line-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-supply-wrong-current-line",
        )
    _assert_error(wrong_line, "material_request_supply_line_not_current", 409)

    created = _create(
        db,
        actor=actor,
        request=request,
        line=line,
        request_version=version,
        key="supply-tamper-replay-key-0001",
        quantity="1.000",
    )
    command = db.scalar(
        select(MaterialRequestCommand).where(
            MaterialRequestCommand.request_id == request.id,
            MaterialRequestCommand.operation == "create_supply_task",
        )
    )
    assert command is not None
    command.result_jsonb = {
        **command.result_jsonb,
        "task_status": "awaiting_supply",
    }
    db.flush()
    with pytest.raises(MaterialRequestSupplyError) as tampered:
        _create(
            db,
            actor=actor,
            request=request,
            line=line,
            request_version=version,
            key="supply-tamper-replay-key-0001",
            quantity="1.000",
        )
    _assert_error(
        tampered, "material_request_supply_idempotency_record_invalid", 503
    )
    assert created.task_status == "open"


def test_original_supply_plan_fails_closed_while_substitution_is_active(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(
        db, key="supply-active-substitution"
    )
    _grant_supply_manage(db, world)
    actor = _principal(db, world.admin_users[0].id)
    created = _create(
        db,
        actor=actor,
        request=request,
        line=line,
        request_version=version,
        key="supply-before-active-substitution-0001",
        quantity="0.500",
    )
    assignment = db.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == world.actor_user.id,
            RoleAssignment.role_id == world.roles["technician"].id,
            RoleAssignment.status == "active",
        )
    )
    assert assignment is not None
    substitute_material = next(
        material for material in world.materials if material.id != line.material_id
    )
    governed = MaterialSubstitution(
        material_id=line.material_id,
        substitute_material_id=substitute_material.id,
        ratio=Decimal("1.000000"),
        valid_from=NOW - timedelta(days=1),
        valid_to=None,
        status="active",
        created_at=NOW,
        updated_at=NOW,
    )
    db.add(governed)
    db.flush()
    db.add(
        SubstitutionDecision(
            request_line_id=line.id,
            substitution_id=governed.id,
            original_approved_qty=line.final_approved_qty,
            ratio=governed.ratio,
            substitute_qty=line.final_approved_qty,
            status="proposed",
            proposed_by_user_id=world.actor_user.id,
            proposed_by_person_id=world.actor_person.id,
            proposed_role_assignment_id=assignment.id,
            authorization_version=world.actor_user.authorization_version,
            proposed_at=NOW,
            decided_by_user_id=None,
            decided_by_person_id=None,
            decided_role_assignment_id=None,
            decided_authorization_version=None,
            decided_at=None,
            reason="申请人正在确认替代料",
            version=0,
            created_at=NOW,
            updated_at=NOW,
        )
    )
    db.flush()

    with pytest.raises(MaterialRequestSupplyError) as active_substitution:
        _create(
            db,
            actor=actor,
            request=request,
            line=line,
            request_version=created.request_version,
            key="supply-active-substitution-0001",
            quantity="1.000",
        )
    _assert_error(
        active_substitution,
        "material_request_supply_active_substitution_exists",
        412,
    )
    with pytest.raises(MaterialRequestSupplyError) as active_progress:
        update_supply_task(
            db,
            actor=actor,
            material_request_id=request.id,
            supply_task_id=created.supply_task_id,
            expected_request_version=created.request_version,
            expected_task_version=created.task_version,
            update=SupplyTaskUpdateInput(
                status="awaiting_supply",
                reference_no=None,
                expected_date=None,
                comment="原料计划继续推进",
            ),
            idempotency_key="supply-active-substitution-progress-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-supply-active-substitution-progress",
        )
    _assert_error(
        active_progress,
        "material_request_supply_active_substitution_exists",
        412,
    )

    cancelled = update_supply_task(
        db,
        actor=actor,
        material_request_id=request.id,
        supply_task_id=created.supply_task_id,
        expected_request_version=created.request_version,
        expected_task_version=created.task_version,
        update=SupplyTaskUpdateInput(
            status="cancelled",
            reference_no=None,
            expected_date=None,
            comment="替代料决定已生效，取消原料计划",
        ),
        idempotency_key="supply-active-substitution-cancel-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-supply-active-substitution-cancel",
    )
    assert cancelled.action == "cancel_supply_task"
    assert cancelled.task_status == "cancelled"
    assert db.scalar(select(func.count()).select_from(SupplyTask)) == 1
