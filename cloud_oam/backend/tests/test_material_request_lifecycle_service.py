from __future__ import annotations

from dataclasses import replace
from datetime import timedelta
from decimal import Decimal
import hashlib
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.demand_models import (
    ApprovalAction,
    ApprovalExternalRegistration,
    ApprovalInstance,
    ApprovalStep,
    MaterialRequest,
    MaterialRequestCancellationLineFact,
    MaterialRequestCommand,
    MaterialRequestLine,
    MaterialSubstitution,
    SubstitutionDecision,
    SupplyTask,
)
from app.formal_access import load_formal_principal
from app.formal_services.audit_chain import AuditChainError
from app.formal_services import material_request_lifecycle as lifecycle_service
from app.formal_services.material_request_lifecycle import (
    MaterialRequestCancelInput,
    MaterialRequestCancellationLineInput,
    MaterialRequestLifecycleError,
    cancel_material_request,
    withdraw_material_request,
)
from app.formal_services.material_request_query import material_request_detail
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
    NotificationEvent,
    OutboxEvent,
    Permission,
    RoleAssignment,
    RolePermission,
    StateTransitionEvent,
)
from app.inventory_models import InventoryTransaction

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
    _create,
    _create_id,
    _assignment,
    _person,
    _user,
    make_world,
)


def _grant_requester_lifecycle_permissions(db: Session, world) -> None:
    technician = world.roles["technician"]
    for action in ("read", "withdraw", "cancel"):
        permission = db.scalar(
            select(Permission).where(
                Permission.resource == "material_request",
                Permission.action == action,
                Permission.field_code == "",
            )
        )
        if permission is None:
            permission = Permission(
                resource="material_request",
                action=action,
                field_code="",
                description="lifecycle service test",
            )
            db.add(permission)
            db.flush()
        if db.scalar(
            select(RolePermission.id).where(
                RolePermission.role_id == technician.id,
                RolePermission.permission_id == permission.id,
            )
        ) is None:
            db.add(
                RolePermission(
                    role_id=technician.id,
                    permission_id=permission.id,
                    effect="allow",
                )
            )
    db.flush()


def _current_actor(db: Session, world):
    return load_formal_principal(db, world.actor_user.id, now=NOW)


def _approved_request(db: Session, *, key: str):
    world, request, lines, version = _submitted(db, key=key)
    _grant_requester_lifecycle_permissions(db, world)
    line = lines[0]
    manager = _principal(db, world.manager_users[0].id)
    admin0 = _principal(db, world.admin_users[0].id)
    admin1 = _principal(db, world.admin_users[1].id)
    _, regional = _approve(
        db,
        actor=manager,
        request=request,
        request_version=version,
        quantities={line.id: line.requested_qty},
        key=f"{key}-region",
    )
    _, headquarters = _approve(
        db,
        actor=admin0,
        request=request,
        request_version=regional.request_version,
        quantities={line.id: line.requested_qty},
        key=f"{key}-headquarters",
    )
    evidence = _evidence(db, uploaded_by=admin0.user_id, marker=f"{key}-evidence")
    step3, registration = _register_external(
        db,
        actor=admin0,
        request=request,
        request_version=headquarters.request_version,
        evidence=evidence,
        key=f"{key}-register",
        action="approve",
        lines=(ApprovalLineDecision(line.id, line.requested_qty, "同意"),),
    )
    final = _verify_external(
        db,
        actor=admin1,
        request=request,
        step=step3,
        registration_id=registration.registration_id,
        request_version=registration.request_version,
        step_version=registration.step_version,
        key=f"{key}-verify",
    )
    assert final.request_status == "approved"
    return world, request, line, final.request_version


def _cancel_input(line: MaterialRequestLine) -> MaterialRequestCancelInput:
    return MaterialRequestCancelInput(
        reason="现场需求已确认不再需要",
        lines=(
            MaterialRequestCancellationLineInput(
                request_line_id=line.id,
                cancelled_qty=line.final_approved_qty,
                reason="取消全部已批准数量",
            ),
        ),
    )


def _assert_lifecycle_error(exc, code: str, status: int) -> None:
    assert exc.value.code == code
    assert exc.value.http_status_code == status
    assert "sql" not in exc.value.message.lower()


def test_withdraw_seals_only_open_approval_and_replays_exactly(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(db, key="lifecycle-withdraw")
    _grant_requester_lifecycle_permissions(db, world)
    actor = _current_actor(db, world)
    before_submitted_at = request.submitted_at
    instance = db.scalar(
        select(ApprovalInstance).where(ApprovalInstance.request_id == request.id)
    )
    assert instance is not None
    step_ids = tuple(
        db.scalars(
            select(ApprovalStep.id)
            .where(ApprovalStep.instance_id == instance.id)
            .order_by(ApprovalStep.step_no)
        ).all()
    )

    with patch.object(db, "commit", side_effect=AssertionError("service committed")), patch.object(
        db, "rollback", side_effect=AssertionError("service rolled back")
    ):
        result = withdraw_material_request(
            db,
            actor=actor,
            material_request_id=request.id,
            expected_version=version,
            reason="审批中发现需求已取消",
            idempotency_key="lifecycle-withdraw-key-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-lifecycle-withdraw-0001",
        )
        replay = withdraw_material_request(
            db,
            actor=actor,
            material_request_id=request.id,
            expected_version=version,
            reason="审批中发现需求已取消",
            idempotency_key="lifecycle-withdraw-key-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-lifecycle-withdraw-replay",
        )

    db.refresh(request)
    db.refresh(instance)
    steps = tuple(
        db.scalars(
            select(ApprovalStep)
            .where(ApprovalStep.instance_id == instance.id)
            .order_by(ApprovalStep.step_no)
        ).all()
    )
    assert result.request_status == request.status == "withdrawn"
    assert replay.replayed is True
    assert replace(replay, replayed=False) == result
    assert request.submitted_at == before_submitted_at
    assert request.submitted_at is not None
    assert request.decided_at is None and request.cancelled_at is None
    assert request.withdrawn_at is not None
    assert instance.status == "withdrawn"
    assert instance.current_step_id is None and instance.current_step_no is None
    assert instance.completed_at is not None
    assert tuple(step.id for step in steps) == step_ids
    assert {step.status for step in steps} == {"cancelled"}
    assert all(step.decided_at is None for step in steps)
    assert all(step.decision_manifest_sha256 is None for step in steps)
    assert lines[0].status == "approval_pending"
    assert db.scalar(select(func.count()).select_from(ApprovalAction)) == 2
    action = db.scalar(
        select(ApprovalAction).where(ApprovalAction.action == "withdraw")
    )
    assert action is not None and action.step_id is None
    assert db.scalar(select(func.count()).select_from(NotificationEvent)) == 0
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == 0


def test_withdraw_rejects_stale_version_wrong_owner_and_key_conflict(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, _lines, version = _submitted(db, key="lifecycle-withdraw-guards")
    _grant_requester_lifecycle_permissions(db, world)
    actor = _current_actor(db, world)
    with pytest.raises(MaterialRequestLifecycleError) as stale:
        withdraw_material_request(
            db,
            actor=actor,
            material_request_id=request.id,
            expected_version=version + 1,
            reason="版本不匹配",
            idempotency_key="lifecycle-withdraw-stale-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-withdraw-stale",
        )
    _assert_lifecycle_error(stale, "material_request_version_conflict", 409)

    other_person = _person(db, world.department, "其他工程师")
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
    with pytest.raises(MaterialRequestLifecycleError) as owner:
        withdraw_material_request(
            db,
            actor=other_actor,
            material_request_id=request.id,
            expected_version=version,
            reason="越权尝试",
            idempotency_key="lifecycle-withdraw-owner-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-withdraw-owner",
        )
    _assert_lifecycle_error(owner, "material_request_requester_mismatch", 403)

    result = withdraw_material_request(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_version=version,
        reason="正式撤回",
        idempotency_key="lifecycle-withdraw-conflict-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-withdraw-conflict",
    )
    assert result.request_status == "withdrawn"
    with pytest.raises(MaterialRequestLifecycleError) as conflict:
        withdraw_material_request(
            db,
            actor=actor,
            material_request_id=request.id,
            expected_version=version,
            reason="不同负载",
            idempotency_key="lifecycle-withdraw-conflict-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-withdraw-conflict-2",
        )
    _assert_lifecycle_error(conflict, "material_request_idempotency_conflict", 409)


def test_withdraw_blocks_pending_external_evidence_without_writes_then_allows_after_review_reject(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(
        db, key="lifecycle-withdraw-pending-evidence"
    )
    _grant_requester_lifecycle_permissions(db, world)
    line = lines[0]
    manager = _principal(db, world.manager_users[0].id)
    registrar = _principal(db, world.admin_users[0].id)
    verifier = _principal(db, world.admin_users[1].id)
    requester = _current_actor(db, world)
    _, regional = _approve(
        db,
        actor=manager,
        request=request,
        request_version=version,
        quantities={line.id: line.requested_qty},
        key="lifecycle-withdraw-pending-evidence-region",
    )
    _, headquarters = _approve(
        db,
        actor=registrar,
        request=request,
        request_version=regional.request_version,
        quantities={line.id: line.requested_qty},
        key="lifecycle-withdraw-pending-evidence-headquarters",
    )
    evidence = _evidence(
        db,
        uploaded_by=registrar.user_id,
        marker="lifecycle-withdraw-pending-evidence-file",
    )
    step3, registered = _register_external(
        db,
        actor=registrar,
        request=request,
        request_version=headquarters.request_version,
        evidence=evidence,
        key="lifecycle-withdraw-pending-evidence-register",
        action="approve",
        lines=(ApprovalLineDecision(line.id, line.requested_qty, "同意"),),
    )
    instance = db.get(ApprovalInstance, registered.instance_id)
    registration = db.get(
        ApprovalExternalRegistration,
        registered.registration_id,
    )
    assert instance is not None and instance.status == "active"
    assert registration is not None and registration.status == "pending_verification"
    assert step3.status == "evidence_pending_verification"

    before_counts = {
        model: db.scalar(select(func.count()).select_from(model))
        for model in (
            MaterialRequestCommand,
            ApprovalAction,
            StateTransitionEvent,
            AuditEvent,
        )
    }
    projection_before = (
        request.status,
        request.version,
        request.withdrawn_at,
        instance.status,
        instance.version,
        instance.current_step_id,
        step3.status,
        step3.version,
        registration.status,
        registration.version,
    )
    with pytest.raises(MaterialRequestLifecycleError) as blocked:
        withdraw_material_request(
            db,
            actor=requester,
            material_request_id=request.id,
            expected_version=registered.request_version,
            reason="待复核期间尝试撤回",
            idempotency_key="lifecycle-withdraw-pending-blocked-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-lifecycle-withdraw-pending-blocked",
        )
    _assert_lifecycle_error(
        blocked,
        "material_request_external_evidence_pending_verification",
        412,
    )
    assert blocked.value.message == "外部审批证据待复核时不能撤回，请先完成复核"

    db.refresh(request)
    db.refresh(instance)
    db.refresh(step3)
    db.refresh(registration)
    assert {
        model: db.scalar(select(func.count()).select_from(model))
        for model in before_counts
    } == before_counts
    assert (
        request.status,
        request.version,
        request.withdrawn_at,
        instance.status,
        instance.version,
        instance.current_step_id,
        step3.status,
        step3.version,
        registration.status,
        registration.version,
    ) == projection_before
    pending_detail = material_request_detail(
        db,
        actor=requester,
        request_id=request.id,
        now=NOW,
    )
    assert "withdraw" not in pending_detail.allowed_actions

    review_rejected = _verify_external(
        db,
        actor=verifier,
        request=request,
        step=step3,
        registration_id=registration.id,
        request_version=registered.request_version,
        step_version=registered.step_version,
        key="lifecycle-withdraw-pending-evidence-review-reject",
        decision="reject",
    )
    db.refresh(registration)
    db.refresh(step3)
    assert registration.status == "rejected"
    assert step3.status == "awaiting_external_evidence"
    available_detail = material_request_detail(
        db,
        actor=requester,
        request_id=request.id,
        now=NOW,
    )
    assert available_detail.allowed_actions == ("withdraw",)

    withdrawn = withdraw_material_request(
        db,
        actor=requester,
        material_request_id=request.id,
        expected_version=review_rejected.request_version,
        reason="证据复核驳回后撤回需求",
        idempotency_key="lifecycle-withdraw-after-review-reject-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-lifecycle-withdraw-after-review-reject",
    )
    assert withdrawn.request_status == "withdrawn"
    assert withdrawn.request_version == review_rejected.request_version + 1


def test_safe_cancel_approved_request_preserves_approval_history_and_replays(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(db, key="lifecycle-cancel")
    actor = _current_actor(db, world)
    submitted_at = request.submitted_at
    decided_at = request.decided_at
    instance = db.scalar(
        select(ApprovalInstance).where(ApprovalInstance.request_id == request.id)
    )
    assert instance is not None and instance.status == "completed"
    step_facts_before = tuple(
        (
            step.id,
            step.status,
            step.decided_at,
            step.decision_manifest_sha256,
            step.version,
        )
        for step in db.scalars(
            select(ApprovalStep)
            .where(ApprovalStep.instance_id == instance.id)
            .order_by(ApprovalStep.step_no)
        ).all()
    )
    payload = _cancel_input(line)
    result = cancel_material_request(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_version=version,
        cancellation=payload,
        idempotency_key="lifecycle-cancel-approved-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-cancel-approved-0001",
    )
    replay = cancel_material_request(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_version=version,
        cancellation=payload,
        idempotency_key="lifecycle-cancel-approved-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-cancel-approved-replay",
    )

    db.refresh(request)
    db.refresh(line)
    db.refresh(instance)
    assert result.request_status == request.status == "cancelled"
    assert replay.replayed is True
    assert request.submitted_at == submitted_at and submitted_at is not None
    assert decided_at is not None and request.decided_at is not None
    assert request.decided_at.replace(tzinfo=None) == decided_at.replace(tzinfo=None)
    assert request.cancelled_at is not None and request.withdrawn_at is None
    assert line.status == "cancelled"
    assert line.cancelled_qty == line.final_approved_qty == Decimal("2.000")
    assert instance.status == "completed"
    step_facts_after = tuple(
        (
            step.id,
            step.status,
            step.decided_at,
            step.decision_manifest_sha256,
            step.version,
        )
        for step in db.scalars(
            select(ApprovalStep)
            .where(ApprovalStep.instance_id == instance.id)
            .order_by(ApprovalStep.step_no)
        ).all()
    )
    assert step_facts_after == step_facts_before
    action = db.scalar(select(ApprovalAction).where(ApprovalAction.action == "cancel"))
    assert action is not None and action.step_id is None
    fact = db.scalar(
        select(MaterialRequestCancellationLineFact).where(
            MaterialRequestCancellationLineFact.request_id == request.id
        )
    )
    assert fact is not None
    assert fact.cancel_action_id == action.id
    assert fact.instance_id == instance.id
    assert fact.request_revision_id == result.revision_id
    assert fact.request_line_id == line.id
    assert fact.final_approved_qty_before == Decimal("2.000")
    assert fact.cancelled_qty == Decimal("2.000")
    assert fact.reason == payload.lines[0].reason
    command = db.get(MaterialRequestCommand, fact.cancel_command_id)
    assert command is not None and command.id == action.command_id
    assert command.request_jsonb["cancellation_fact_count"] == 1
    assert len(
        command.request_jsonb["cancellation_fact_manifest_sha256"]
    ) == 64
    assert payload.reason not in str(command.request_jsonb)
    assert payload.lines[0].reason not in str(command.request_jsonb)
    assert db.scalar(select(func.count()).select_from(NotificationEvent)) == 0
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 0


def test_draft_cancel_is_fail_closed_without_fabricating_submission_time(
    approval_db: Session,
) -> None:
    db = approval_db
    world = make_world(db)
    _grant_requester_lifecycle_permissions(db, world)
    request_id = _create_id(world, "lifecycle-draft-cancel")
    created = _create(db, world, request_id, key="lifecycle-draft-cancel")
    actor = _current_actor(db, world)
    request = db.get(MaterialRequest, request_id)
    assert request is not None and request.submitted_at is None

    with pytest.raises(MaterialRequestLifecycleError) as blocked:
        cancel_material_request(
            db,
            actor=actor,
            material_request_id=request_id,
            expected_version=created.request_version,
            cancellation=MaterialRequestCancelInput(reason="取消草稿"),
            idempotency_key="lifecycle-draft-cancel-key-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-draft-cancel",
        )
    _assert_lifecycle_error(
        blocked,
        "material_request_draft_cancel_schema_migration_required",
        412,
    )
    assert request.status == "draft"
    assert request.submitted_at is None and request.cancelled_at is None
    assert db.scalar(
        select(func.count())
        .select_from(MaterialRequestCommand)
        .where(MaterialRequestCommand.operation == "cancel")
    ) == 0


def test_cancel_lock_order_is_aggregate_then_parent_principal_and_children(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(
        db, key="lifecycle-cancel-lock-order"
    )
    order: list[str] = []
    advisory = lifecycle_service._take_advisory_locks
    lock_request = lifecycle_service._lock_request
    lock_principal = lifecycle_service.lock_formal_principal_graph
    lock_graph = lifecycle_service._lock_graph

    def record_advisory(*args, **kwargs):
        order.append("aggregate_advisory")
        return advisory(*args, **kwargs)

    def record_request(*args, **kwargs):
        order.append("parent_request")
        return lock_request(*args, **kwargs)

    def record_principal(*args, **kwargs):
        order.append("principal_graph")
        return lock_principal(*args, **kwargs)

    def record_graph(*args, **kwargs):
        order.append("child_graph")
        return lock_graph(*args, **kwargs)

    with patch.object(
        lifecycle_service, "_take_advisory_locks", side_effect=record_advisory
    ), patch.object(
        lifecycle_service, "_lock_request", side_effect=record_request
    ), patch.object(
        lifecycle_service,
        "lock_formal_principal_graph",
        side_effect=record_principal,
    ), patch.object(
        lifecycle_service, "_lock_graph", side_effect=record_graph
    ):
        cancel_material_request(
            db,
            actor=_current_actor(db, world),
            material_request_id=request.id,
            expected_version=version,
            cancellation=_cancel_input(line),
            idempotency_key="lifecycle-cancel-lock-order-key-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-lifecycle-cancel-lock-order-command",
        )

    assert order[:4] == [
        "aggregate_advisory",
        "parent_request",
        "principal_graph",
        "child_graph",
    ]


@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    (
        ("missing", "material_request_cancellation_fact_set_invalid"),
        ("quantity", "material_request_cancellation_fact_invalid"),
        ("reason", "material_request_cancellation_fact_invalid"),
        ("manifest", "material_request_cancellation_fact_manifest_invalid"),
    ),
)
def test_cancel_replay_fails_closed_when_line_facts_are_tampered(
    approval_db: Session,
    tamper: str,
    expected_code: str,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(
        db, key=f"lifecycle-replay-tamper-{tamper}"
    )
    actor = _current_actor(db, world)
    payload = _cancel_input(line)
    idempotency_key = f"lifecycle-replay-tamper-{tamper}-key-0001"
    cancel_material_request(
        db,
        actor=actor,
        material_request_id=request.id,
        expected_version=version,
        cancellation=payload,
        idempotency_key=idempotency_key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-replay-tamper-{tamper}",
    )
    fact = db.scalar(
        select(MaterialRequestCancellationLineFact).where(
            MaterialRequestCancellationLineFact.request_id == request.id
        )
    )
    assert fact is not None
    command = db.get(MaterialRequestCommand, fact.cancel_command_id)
    assert command is not None
    if tamper == "missing":
        db.delete(fact)
    elif tamper == "quantity":
        fact.final_approved_qty_before = Decimal("1.000")
        fact.cancelled_qty = Decimal("1.000")
    elif tamper == "reason":
        fact.reason = "篡改后的逐行原因"
    else:
        command.request_jsonb = {
            **command.request_jsonb,
            "cancellation_fact_manifest_sha256": "0" * 64,
        }
    db.flush()

    with pytest.raises(MaterialRequestLifecycleError) as replay_failure:
        cancel_material_request(
            db,
            actor=actor,
            material_request_id=request.id,
            expected_version=version,
            cancellation=payload,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=SECRET,
            trace_request_id=f"trace-replay-tamper-{tamper}-retry",
        )
    _assert_lifecycle_error(replay_failure, expected_code, 503)


def test_returned_request_can_cancel_without_fabricating_decision_time(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(db, key="lifecycle-return-cancel")
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
        idempotency_key="lifecycle-return-before-cancel-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-return-before-cancel",
    )
    assert request.status == "returned" and request.decided_at is None
    submitted_at = request.submitted_at
    result = cancel_material_request(
        db,
        actor=_current_actor(db, world),
        material_request_id=request.id,
        expected_version=returned.request_version,
        cancellation=MaterialRequestCancelInput(
            reason="退回后确认无需继续申请",
            lines=(),
        ),
        idempotency_key="lifecycle-return-cancel-key-0001",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-return-cancel",
    )
    assert result.request_status == request.status == "cancelled"
    assert request.submitted_at == submitted_at and submitted_at is not None
    assert request.decided_at is None
    assert request.cancelled_at is not None
    assert lines[0].status == "cancelled"
    assert lines[0].final_approved_qty == lines[0].cancelled_qty == Decimal("0.000")
    instance = db.get(ApprovalInstance, result.approval_instance_id)
    assert instance is not None and instance.status == "returned"


def test_safe_cancel_requires_exact_full_approved_line_manifest(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(
        db, key="lifecycle-cancel-lines"
    )
    actor = _current_actor(db, world)
    with pytest.raises(MaterialRequestLifecycleError) as partial:
        cancel_material_request(
            db,
            actor=actor,
            material_request_id=request.id,
            expected_version=version,
            cancellation=MaterialRequestCancelInput(
                reason="尝试部分取消",
                lines=(
                    MaterialRequestCancellationLineInput(
                        line.id, Decimal("1.000"), "仅取消一件"
                    ),
                ),
            ),
            idempotency_key="lifecycle-cancel-lines-key-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-cancel-lines",
        )
    _assert_lifecycle_error(
        partial, "material_request_cancellation_lines_not_exact", 422
    )
    db.refresh(request)
    db.refresh(line)
    assert request.status == "approved"
    assert line.cancelled_qty == Decimal("0.000")


@pytest.mark.parametrize(
    ("blocker", "expected_code"),
    (
        ("axis", "material_request_compensation_required"),
        ("supply", "material_request_active_supply_task_exists"),
        ("substitution", "material_request_active_substitution_exists"),
        ("inventory", "material_request_inventory_fact_exists"),
        ("notification", "material_request_notification_fact_exists"),
        ("outbox", "material_request_outbox_fact_exists"),
    ),
)
def test_safe_cancel_fails_closed_for_each_existing_downstream_fact(
    approval_db: Session,
    blocker: str,
    expected_code: str,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(
        db, key=f"lifecycle-block-{blocker}"
    )
    actor = _current_actor(db, world)
    assignment = db.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == world.actor_user.id,
            RoleAssignment.role_id == world.roles["technician"].id,
            RoleAssignment.status == "active",
        )
    )
    assert assignment is not None
    if blocker == "axis":
        request.reservation_status = "reserved"
    elif blocker == "supply":
        db.add(
            SupplyTask(
                task_no=f"SUPPLY-{uuid.uuid4()}",
                request_line_id=line.id,
                substitution_decision_id=None,
                supply_type="headquarters_replenishment",
                reference_no=None,
                expected_qty=Decimal("1.000"),
                original_equivalent_qty=Decimal("1.000"),
                expected_date=None,
                status="open",
                created_by_user_id=world.actor_user.id,
                created_by_person_id=world.actor_person.id,
                created_role_assignment_id=assignment.id,
                authorization_version=actor.authorization_version,
                cancelled_by_user_id=None,
                cancelled_at=None,
                version=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    elif blocker == "substitution":
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
                authorization_version=actor.authorization_version,
                proposed_at=NOW,
                decided_by_user_id=None,
                decided_by_person_id=None,
                decided_role_assignment_id=None,
                decided_authorization_version=None,
                decided_at=None,
                reason="测试活动替代料",
                version=0,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    elif blocker == "inventory":
        marker = uuid.uuid4().hex
        db.add(
            InventoryTransaction(
                transaction_no=f"TX-{marker}",
                movement_type="reserve",
                source_document_type="material_request",
                source_document_id=str(request.id),
                posting_key=f"posting-{marker}",
                idempotency_key_hash=hashlib.sha256(f"idem-{marker}".encode()).hexdigest(),
                request_hash=hashlib.sha256(f"request-{marker}".encode()).hexdigest(),
                status="posted",
                effective_at=NOW,
                posted_at=NOW,
                ledger_cursor=int(marker[:8], 16) + 1,
                reversed_transaction_id=None,
                actor_user_id=world.actor_user.id,
                created_at=NOW,
            )
        )
    elif blocker == "notification":
        db.add(
            NotificationEvent(
                event_type="material_request_test",
                business_type="material_request",
                business_id=str(request.id),
                dedup_key=f"notification-{uuid.uuid4()}",
                payload_jsonb={},
                status="pending",
                occurred_at=NOW,
                created_at=NOW,
            )
        )
    else:
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

    with pytest.raises(MaterialRequestLifecycleError) as blocked:
        cancel_material_request(
            db,
            actor=actor,
            material_request_id=request.id,
            expected_version=version,
            cancellation=_cancel_input(line),
            idempotency_key=f"lifecycle-block-{blocker}-key-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id=f"trace-block-{blocker}",
        )
    _assert_lifecycle_error(blocked, expected_code, 412)
    assert request.status == "approved"


def test_lifecycle_audit_failure_requires_caller_rollback_and_leaves_no_fact(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, _lines, version = _submitted(db, key="lifecycle-audit")
    _grant_requester_lifecycle_permissions(db, world)
    actor = _current_actor(db, world)
    db.commit()
    before_commands = db.scalar(select(func.count()).select_from(MaterialRequestCommand))
    before_actions = db.scalar(select(func.count()).select_from(ApprovalAction))
    before_audits = db.scalar(select(func.count()).select_from(AuditEvent))
    with patch(
        "app.formal_services.material_request_lifecycle.append_audit_event",
        side_effect=AuditChainError("audit unavailable"),
    ):
        with pytest.raises(MaterialRequestLifecycleError) as failure:
            withdraw_material_request(
                db,
                actor=actor,
                material_request_id=request.id,
                expected_version=version,
                reason="审计故障测试",
                idempotency_key="lifecycle-audit-failure-key-0001",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-lifecycle-audit-failure",
            )
    _assert_lifecycle_error(
        failure, "material_request_lifecycle_audit_unavailable", 503
    )
    db.rollback()
    request = db.get(MaterialRequest, request.id)
    assert request is not None and request.status == "approval_in_progress"
    assert db.scalar(select(func.count()).select_from(MaterialRequestCommand)) == before_commands
    assert db.scalar(select(func.count()).select_from(ApprovalAction)) == before_actions
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == before_audits
    assert db.scalar(
        select(func.count())
        .select_from(StateTransitionEvent)
        .where(StateTransitionEvent.to_status == "withdrawn")
    ) == 0


def test_cancel_audit_failure_rolls_back_command_action_line_facts_and_projection(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, line, version = _approved_request(
        db, key="lifecycle-cancel-audit"
    )
    actor = _current_actor(db, world)
    request_id = request.id
    line_id = line.id
    payload = _cancel_input(line)
    db.commit()
    before_commands = db.scalar(
        select(func.count()).select_from(MaterialRequestCommand)
    )
    before_actions = db.scalar(select(func.count()).select_from(ApprovalAction))
    before_facts = db.scalar(
        select(func.count()).select_from(MaterialRequestCancellationLineFact)
    )
    before_audits = db.scalar(select(func.count()).select_from(AuditEvent))
    with patch(
        "app.formal_services.material_request_lifecycle.append_audit_event",
        side_effect=AuditChainError("audit unavailable"),
    ):
        with pytest.raises(MaterialRequestLifecycleError) as failure:
            cancel_material_request(
                db,
                actor=actor,
                material_request_id=request_id,
                expected_version=version,
                cancellation=payload,
                idempotency_key="lifecycle-cancel-audit-failure-key-0001",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-lifecycle-cancel-audit-failure",
            )
    _assert_lifecycle_error(
        failure, "material_request_lifecycle_audit_unavailable", 503
    )
    db.rollback()
    request = db.get(MaterialRequest, request_id)
    line = db.get(MaterialRequestLine, line_id)
    assert request is not None and request.status == "approved"
    assert request.cancelled_at is None
    assert line is not None and line.status == "approved"
    assert line.cancelled_qty == Decimal("0.000")
    assert (
        db.scalar(select(func.count()).select_from(MaterialRequestCommand))
        == before_commands
    )
    assert (
        db.scalar(select(func.count()).select_from(ApprovalAction))
        == before_actions
    )
    assert (
        db.scalar(
            select(func.count()).select_from(
                MaterialRequestCancellationLineFact
            )
        )
        == before_facts
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == before_audits
    assert db.scalar(
        select(func.count())
        .select_from(StateTransitionEvent)
        .where(StateTransitionEvent.to_status == "cancelled")
    ) == 0


def test_read_allowed_actions_match_safe_lifecycle_preconditions(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, _lines, _version = _submitted(db, key="lifecycle-read-withdraw")
    _grant_requester_lifecycle_permissions(db, world)
    actor = _current_actor(db, world)
    pending = material_request_detail(db, actor=actor, request_id=request.id, now=NOW)
    assert pending.allowed_actions == ("withdraw",)


def test_read_cancel_action_disappears_when_downstream_fact_exists(
    approval_db: Session,
) -> None:
    db = approval_db
    approved_world, approved_request, _line, _approved_version = _approved_request(
        db, key="lifecycle-read-cancel"
    )
    approved_actor = _current_actor(db, approved_world)
    approved = material_request_detail(
        db,
        actor=approved_actor,
        request_id=approved_request.id,
        now=NOW,
    )
    assert approved.allowed_actions == ("cancel",)

    db.add(
        NotificationEvent(
            event_type="material_request_test",
            business_type="material_request",
            business_id=str(approved_request.id),
            dedup_key=f"notification-read-{uuid.uuid4()}",
            payload_jsonb={},
            status="pending",
            occurred_at=NOW,
            created_at=NOW,
        )
    )
    db.flush()
    blocked = material_request_detail(
        db,
        actor=approved_actor,
        request_id=approved_request.id,
        now=NOW,
    )
    assert "cancel" not in blocked.allowed_actions


def test_read_never_advertises_schema_blocked_draft_cancel(
    approval_db: Session,
) -> None:
    db = approval_db
    world = make_world(db)
    _grant_requester_lifecycle_permissions(db, world)
    request_id = _create_id(world, "lifecycle-read-draft")
    _create(db, world, request_id, key="lifecycle-read-draft")
    detail = material_request_detail(
        db,
        actor=_current_actor(db, world),
        request_id=request_id,
        now=NOW,
    )
    assert detail.allowed_actions == ("update", "submit")
    assert "cancel" not in detail.allowed_actions
