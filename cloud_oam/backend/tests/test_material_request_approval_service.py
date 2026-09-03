from __future__ import annotations

from dataclasses import replace
from datetime import timezone
from decimal import Decimal
import hashlib
import uuid
from unittest.mock import patch

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

from app.database import Base
from app.demand_models import (
    ApprovalAction,
    ApprovalExternalRegistration,
    ApprovalExternalRegistrationLine,
    ApprovalInstance,
    ApprovalReturnLineFact,
    ApprovalStep,
    ApprovalStepLineDecision,
    MaterialRequest,
    MaterialRequestCommand,
    MaterialRequestLine,
)
from app.formal_access import FormalPrincipal, load_formal_principal
from app.formal_services import formal_files
from app.formal_services import material_request_approval as approval_service
from app.formal_services.audit_chain import AuditChainError
from app.formal_services.material_request_approval import (
    ExternalApprovalRegistrationInput,
    MaterialRequestApprovalError,
    MaterialRequestApprovalInput,
    decide_material_request_approval,
    register_external_approval_evidence,
    verify_external_approval_evidence,
)
from app.formal_services.material_request_draft import (
    MaterialRequestDraftLineInput,
    create_material_request_draft,
    submit_material_request,
)
from app.formal_services.material_request_policy import (
    ApprovalLineDecision,
    ApprovalReturnInstruction,
)
from app.foundation_models import AuditEvent, FileObject, OutboxEvent, RoleAssignment
from app.inventory_models import InventoryTransaction
from app.models import User
from test_material_request_draft_service import (
    NOW,
    SECRET,
    World,
    _create,
    _create_id,
    _draft,
    make_world,
)


@pytest.fixture
def approval_db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


def _submitted(
    db: Session,
    *,
    key: str,
    admin_count: int = 2,
    two_lines: bool = False,
) -> tuple[World, MaterialRequest, tuple[MaterialRequestLine, ...], int]:
    world = make_world(db, admin_count=admin_count)
    request_id = _create_id(world, key)
    if two_lines:
        draft = _draft(world, request_id)
        draft = replace(
            draft,
            lines=(
                draft.lines[0],
                MaterialRequestDraftLineInput(
                    material_id=world.materials[1].id,
                    requested_qty=Decimal("3.000"),
                    note="",
                ),
            ),
        )
        created = create_material_request_draft(
            db,
            actor=world.actor,
            material_request_id=request_id,
            draft=draft,
            idempotency_key=key,
            idempotency_hmac_secret=SECRET,
            trace_request_id=f"trace-{key}",
        )
    else:
        created = _create(db, world, request_id, key=key)
    submitted = submit_material_request(
        db,
        actor=world.actor,
        material_request_id=request_id,
        expected_version=created.request_version,
        idempotency_key=f"{key}-submit",
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}-submit",
    )
    request = db.get(MaterialRequest, request_id)
    assert request is not None
    lines = tuple(
        db.scalars(
            select(MaterialRequestLine)
            .where(
                MaterialRequestLine.request_id == request_id,
                MaterialRequestLine.revision_id == submitted.revision_id,
            )
            .order_by(MaterialRequestLine.line_no)
        ).all()
    )
    return world, request, lines, submitted.version


def _principal(db: Session, user_id: str) -> FormalPrincipal:
    return load_formal_principal(db, user_id, now=NOW)


def _current_step(db: Session, request_id: uuid.UUID) -> ApprovalStep:
    row = db.scalar(
        select(ApprovalStep)
        .join(ApprovalInstance, ApprovalInstance.id == ApprovalStep.instance_id)
        .where(
            ApprovalInstance.request_id == request_id,
            ApprovalInstance.status == "active",
            ApprovalInstance.current_step_id == ApprovalStep.id,
        )
        .execution_options(populate_existing=True)
    )
    assert row is not None
    return row


def _assert_command_projection_times(
    db: Session,
    *,
    request: MaterialRequest,
    result,
    operation: str,
    rows: tuple[object, ...],
) -> MaterialRequestCommand:
    command = db.scalar(
        select(MaterialRequestCommand).where(
            MaterialRequestCommand.request_id == request.id,
            MaterialRequestCommand.operation == operation,
            MaterialRequestCommand.target_version == result.request_version,
        )
    )
    instance = db.get(ApprovalInstance, result.instance_id)
    assert command is not None and instance is not None
    command_time = approval_service._as_utc(command.occurred_at)
    for row in (request, instance, *rows):
        assert approval_service._as_utc(row.updated_at) == command_time
    return command


def _approve(
    db: Session,
    *,
    actor: FormalPrincipal,
    request: MaterialRequest,
    request_version: int,
    quantities: dict[uuid.UUID, Decimal],
    key: str,
    comment: str = "同意",
):
    step = _current_step(db, request.id)
    result = decide_material_request_approval(
        db,
        actor=actor,
        material_request_id=request.id,
        approval_step_id=step.id,
        expected_request_version=request_version,
        expected_step_version=step.version,
        decision=MaterialRequestApprovalInput(
            action="approve",
            lines=tuple(
                ApprovalLineDecision(
                    request_line_id=line_id,
                    approved_qty=quantity,
                    reason=comment,
                )
                for line_id, quantity in quantities.items()
            ),
            comment=comment,
        ),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )
    return step, result


def _evidence(db: Session, *, uploaded_by: str, marker: str) -> FileObject:
    uploader = db.get(User, uploaded_by)
    assert uploader is not None and uploader.person_id is not None
    file_id = uuid.uuid4()
    storage_key = (
        "formal-files/v1/external_approval_evidence/"
        f"{file_id.hex[:2]}/{file_id.hex}"
    )
    base_metadata = {
        "authorization_version": uploader.authorization_version,
        "file_id": str(file_id),
        "idempotency_key_hash": hashlib.sha256(
            f"idempotency:{marker}".encode("utf-8")
        ).hexdigest(),
        "provider": "aliyun_oss_v2",
        "purpose": "external_approval_evidence",
        "request_sha256": formal_files._upload_request_hash(
            formal_files._PreparedUpload(
                purpose="external_approval_evidence",
                original_filename=f"{marker}.png",
                size_bytes=256,
                mime_type="image/png",
                sha256=hashlib.sha256(marker.encode("utf-8")).hexdigest(),
            )
        ),
        "schema": "cloud_oam.formal_file_upload_intent.v1",
        "storage_key": storage_key,
        "uploader_person_id": str(uploader.person_id),
        "uploader_user_id": uploaded_by,
    }
    completion_metadata = {
        "etag_sha256": "d" * 64,
        "head_manifest_sha256": "e" * 64,
        "verified_at": NOW.isoformat(),
    }
    is_postgresql = db.get_bind().dialect.name == "postgresql"
    row = FileObject(
        id=file_id,
        storage_key=storage_key,
        sha256=hashlib.sha256(marker.encode("utf-8")).hexdigest(),
        size_bytes=256,
        mime_type="image/png",
        original_filename=f"{marker}.png",
        uploaded_by=uploaded_by,
        status="pending" if is_postgresql else "available",
        metadata_jsonb=(
            base_metadata
            if is_postgresql
            else {**base_metadata, "completion": completion_metadata}
        ),
        created_at=NOW,
    )
    db.add(row)
    if is_postgresql:
        db.flush()
        row.status = "available"
        row.metadata_jsonb = {
            **base_metadata,
            "completion": completion_metadata,
        }
    db.flush()
    return row


def _register_external(
    db: Session,
    *,
    actor: FormalPrincipal,
    request: MaterialRequest,
    request_version: int,
    evidence: FileObject,
    key: str,
    action: str,
    lines: tuple[ApprovalLineDecision, ...] = (),
    return_lines: tuple[ApprovalReturnInstruction, ...] = (),
):
    step = _current_step(db, request.id)
    assert step.step_no == 3
    opened_at = step.opened_at
    assert opened_at is not None
    if opened_at.tzinfo is None:
        opened_at = opened_at.replace(tzinfo=timezone.utc)
    result = register_external_approval_evidence(
        db,
        actor=actor,
        material_request_id=request.id,
        approval_step_id=step.id,
        expected_request_version=request_version,
        expected_step_version=step.version,
        registration=ExternalApprovalRegistrationInput(
            evidence_file_id=evidence.id,
            external_approver_name="星星总部审批人",
            external_reference_no=f"STAR-{key.upper()}",
            external_decided_at=opened_at,
            action=action,
            lines=lines,
            return_lines=return_lines,
            comment="退回重审" if action == "return" else "整单驳回" if action == "reject" else "",
        ),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )
    return step, result


def _verify_external(
    db: Session,
    *,
    actor: FormalPrincipal,
    request: MaterialRequest,
    step: ApprovalStep,
    registration_id: uuid.UUID,
    request_version: int,
    step_version: int,
    key: str,
    decision: str = "accept",
):
    return verify_external_approval_evidence(
        db,
        actor=actor,
        material_request_id=request.id,
        approval_step_id=step.id,
        registration_id=registration_id,
        expected_request_version=request_version,
        expected_step_version=step_version,
        verification_decision=decision,
        comment="证据不完整" if decision == "reject" else "",
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def test_approval_lock_order_is_aggregate_then_parent_principal_and_children(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(
        db, key="approval-lock-order"
    )
    step = _current_step(db, request.id)
    order: list[str] = []
    advisory = approval_service._take_advisory_locks
    lock_request = approval_service._lock_request
    lock_principal = approval_service.lock_formal_principal_graph
    lock_approval = approval_service._lock_current_approval

    def record_advisory(*args, **kwargs):
        order.append("aggregate_advisory")
        return advisory(*args, **kwargs)

    def record_request(*args, **kwargs):
        order.append("parent_request")
        return lock_request(*args, **kwargs)

    def record_principal(*args, **kwargs):
        order.append("principal_graph")
        return lock_principal(*args, **kwargs)

    def record_approval(*args, **kwargs):
        order.append("child_graph")
        return lock_approval(*args, **kwargs)

    with patch.object(
        approval_service, "_take_advisory_locks", side_effect=record_advisory
    ), patch.object(
        approval_service, "_lock_request", side_effect=record_request
    ), patch.object(
        approval_service,
        "lock_formal_principal_graph",
        side_effect=record_principal,
    ), patch.object(
        approval_service, "_lock_current_approval", side_effect=record_approval
    ):
        decide_material_request_approval(
            db,
            actor=_principal(db, world.manager_users[0].id),
            material_request_id=request.id,
            approval_step_id=step.id,
            expected_request_version=version,
            expected_step_version=step.version,
            decision=MaterialRequestApprovalInput(
                action="approve",
                lines=(
                    ApprovalLineDecision(
                        lines[0].id,
                        lines[0].requested_qty,
                        "同意",
                    ),
                ),
            ),
            idempotency_key="approval-lock-order-key-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-approval-lock-order-command",
        )

    assert order[:4] == [
        "aggregate_advisory",
        "parent_request",
        "principal_graph",
        "child_graph",
    ]


def test_approval_rechecks_candidate_set_after_principal_locks(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(
        db, key="approval-candidate-race"
    )
    step = _current_step(db, request.id)
    discover = approval_service._request_candidate_user_ids
    calls = 0

    def changing_candidates(*args, **kwargs):
        nonlocal calls
        calls += 1
        rows = discover(*args, **kwargs)
        if calls == 2:
            return tuple((*rows, "concurrent-candidate"))
        return rows

    with patch.object(
        approval_service,
        "_request_candidate_user_ids",
        side_effect=changing_candidates,
    ):
        with pytest.raises(MaterialRequestApprovalError) as failure:
            decide_material_request_approval(
                db,
                actor=_principal(db, world.manager_users[0].id),
                material_request_id=request.id,
                approval_step_id=step.id,
                expected_request_version=version,
                expected_step_version=step.version,
                decision=MaterialRequestApprovalInput(
                    action="approve",
                    lines=(
                        ApprovalLineDecision(
                            lines[0].id,
                            lines[0].requested_qty,
                            "同意",
                        ),
                    ),
                ),
                idempotency_key="approval-candidate-race-key-0001",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-approval-candidate-race",
            )
    assert failure.value.code == "material_request_approval_candidate_set_changed"
    assert failure.value.http_status_code == 409
    db.refresh(request)
    db.refresh(step)
    assert request.status == "approval_in_progress"
    assert step.status == "open"
    assert db.scalar(
        select(ApprovalStepLineDecision.id).where(
            ApprovalStepLineDecision.step_id == step.id
        )
    ) is None


def test_forward_chain_external_accept_replays_and_keeps_fulfillment_axes_neutral(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(db, key="approval-forward")
    line = lines[0]
    manager = _principal(db, world.manager_users[0].id)
    admin0 = _principal(db, world.admin_users[0].id)
    admin1 = _principal(db, world.admin_users[1].id)

    step1, regional = _approve(
        db,
        actor=manager,
        request=request,
        request_version=version,
        quantities={line.id: line.requested_qty},
        key="approval-forward-region",
    )
    activated_step2 = _current_step(db, request.id)
    _assert_command_projection_times(
        db,
        request=request,
        result=regional,
        operation="region_decide",
        rows=(step1, activated_step2),
    )
    replay = decide_material_request_approval(
        db,
        actor=manager,
        material_request_id=request.id,
        approval_step_id=step1.id,
        expected_request_version=version,
        expected_step_version=0,
        decision=MaterialRequestApprovalInput(
            action="approve",
            lines=(ApprovalLineDecision(line.id, line.requested_qty, "同意"),),
            comment="同意",
        ),
        idempotency_key="approval-forward-region",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-approval-forward-region-replay",
    )
    assert replay.replayed is True
    assert replace(replay, replayed=False) == regional

    step2, headquarters = _approve(
        db,
        actor=admin0,
        request=request,
        request_version=regional.request_version,
        quantities={line.id: line.requested_qty},
        key="approval-forward-hq",
    )
    activated_step3 = _current_step(db, request.id)
    _assert_command_projection_times(
        db,
        request=request,
        result=headquarters,
        operation="headquarters_decide",
        rows=(step2, activated_step3),
    )
    evidence = _evidence(db, uploaded_by=admin0.user_id, marker="evidence-a")
    step3, registration = _register_external(
        db,
        actor=admin0,
        request=request,
        request_version=headquarters.request_version,
        evidence=evidence,
        key="approval-forward-external",
        action="approve",
        lines=(ApprovalLineDecision(line.id, line.requested_qty, ""),),
    )
    registration_row = db.get(
        ApprovalExternalRegistration,
        registration.registration_id,
    )
    assert registration_row is not None
    _assert_command_projection_times(
        db,
        request=request,
        result=registration,
        operation="register_external",
        rows=(step3, registration_row),
    )
    verified = _verify_external(
        db,
        actor=admin1,
        request=request,
        step=step3,
        registration_id=registration.registration_id,
        request_version=registration.request_version,
        step_version=registration.step_version,
        key="approval-forward-verify",
    )
    _assert_command_projection_times(
        db,
        request=request,
        result=verified,
        operation="verify_external",
        rows=(step3, registration_row, *lines),
    )
    verify_replay = _verify_external(
        db,
        actor=admin1,
        request=request,
        step=step3,
        registration_id=registration.registration_id,
        request_version=registration.request_version,
        step_version=registration.step_version,
        key="approval-forward-verify",
    )

    assert verified.request_status == "approved"
    assert verified.instance_status == "completed"
    assert verify_replay.replayed is True
    assert replace(verify_replay, replayed=False) == verified
    assert db.scalar(select(func.count()).select_from(ApprovalStepLineDecision)) == 3
    # submit + regional + headquarters + external registration + verification
    assert db.scalar(select(func.count()).select_from(ApprovalAction)) == 5
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == 0
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == 0
    assert set(verified.state_axes.values()) == {
        "not_allocated",
        "not_reserved",
        "not_started",
        "not_signed",
        "not_occurred",
    }


def test_partial_zero_line_is_not_forwarded(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(
        db, key="approval-partial", two_lines=True
    )
    manager = _principal(db, world.manager_users[0].id)
    admin = _principal(db, world.admin_users[0].id)
    _, first = _approve(
        db,
        actor=manager,
        request=request,
        request_version=version,
        quantities={lines[0].id: lines[0].requested_qty, lines[1].id: Decimal("0")},
        key="approval-partial-region",
    )
    step2, second = _approve(
        db,
        actor=admin,
        request=request,
        request_version=first.request_version,
        quantities={lines[0].id: lines[0].requested_qty},
        key="approval-partial-hq",
    )
    step2_rows = tuple(
        db.scalars(
            select(ApprovalStepLineDecision).where(
                ApprovalStepLineDecision.step_id == step2.id
            )
        ).all()
    )
    assert [row.request_line_id for row in step2_rows] == [lines[0].id]
    assert second.current_step_no == 3


def test_internal_whole_reject_has_complete_zero_approved_line_fact(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(db, key="approval-reject")
    manager = _principal(db, world.manager_users[0].id)
    step = _current_step(db, request.id)
    rejected = decide_material_request_approval(
        db,
        actor=manager,
        material_request_id=request.id,
        approval_step_id=step.id,
        expected_request_version=version,
        expected_step_version=step.version,
        decision=MaterialRequestApprovalInput(action="reject", comment="不符合申请条件"),
        idempotency_key="approval-reject-region",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-approval-reject-region",
    )
    _assert_command_projection_times(
        db,
        request=request,
        result=rejected,
        operation="region_decide",
        rows=(step, *lines),
    )
    fact = db.scalar(
        select(ApprovalStepLineDecision).where(
            ApprovalStepLineDecision.step_id == step.id
        )
    )
    assert rejected.request_status == "rejected"
    assert fact is not None
    assert fact.request_line_id == lines[0].id
    assert fact.approved_qty == Decimal("0.000")
    assert fact.rejected_qty == lines[0].requested_qty


def test_step1_return_targets_requester_revision_without_opening_an_approval_step(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(db, key="approval-return-requester")
    line = lines[0]
    manager = _principal(db, world.manager_users[0].id)
    step1 = _current_step(db, request.id)
    result = decide_material_request_approval(
        db,
        actor=manager,
        material_request_id=request.id,
        approval_step_id=step1.id,
        expected_request_version=version,
        expected_step_version=step1.version,
        decision=MaterialRequestApprovalInput(
            action="return",
            return_lines=(
                ApprovalReturnInstruction(line.id, line.requested_qty, "补充申请依据"),
            ),
            comment="退回申请人",
        ),
        idempotency_key="approval-return-requester-command",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-approval-return-requester-command",
    )
    instance = db.get(ApprovalInstance, result.instance_id)
    _assert_command_projection_times(
        db,
        request=request,
        result=result,
        operation="region_decide",
        rows=(step1,),
    )
    fact = db.scalar(
        select(ApprovalReturnLineFact).where(
            ApprovalReturnLineFact.returned_from_step_id == step1.id
        )
    )
    assert result.request_status == "returned"
    assert result.current_step_id is None
    assert instance is not None and instance.status == "returned"
    assert instance.current_step_id is None
    assert fact is not None
    assert fact.target_kind == "requester_revision"
    assert fact.target_step_id is None


def test_step2_return_reopens_step1_and_continues_with_new_step2_and_step3(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(db, key="approval-return-step2")
    line = lines[0]
    manager = _principal(db, world.manager_users[0].id)
    admin = _principal(db, world.admin_users[0].id)
    _, first = _approve(
        db,
        actor=manager,
        request=request,
        request_version=version,
        quantities={line.id: line.requested_qty},
        key="approval-return-step2-region",
    )
    step2 = _current_step(db, request.id)
    original_pending_step3 = db.scalar(
        select(ApprovalStep).where(
            ApprovalStep.instance_id == first.instance_id,
            ApprovalStep.step_no == 3,
            ApprovalStep.attempt_no == 1,
        )
    )
    assert original_pending_step3 is not None
    returned = decide_material_request_approval(
        db,
        actor=admin,
        material_request_id=request.id,
        approval_step_id=step2.id,
        expected_request_version=first.request_version,
        expected_step_version=step2.version,
        decision=MaterialRequestApprovalInput(
            action="return",
            return_lines=(
                ApprovalReturnInstruction(line.id, Decimal("1.000"), "仅重审一件"),
            ),
            comment="退回区域重审",
        ),
        idempotency_key="approval-return-step2-command",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-approval-return-step2-command",
    )
    reopened_step1 = _current_step(db, request.id)
    _assert_command_projection_times(
        db,
        request=request,
        result=returned,
        operation="headquarters_decide",
        rows=(step2, reopened_step1),
    )
    assert (reopened_step1.step_no, reopened_step1.attempt_no) == (1, 2)
    with pytest.raises(MaterialRequestApprovalError) as over_limit:
        _approve(
            db,
            actor=manager,
            request=request,
            request_version=returned.request_version,
            quantities={line.id: Decimal("2.000")},
            key="approval-return-step2-over-limit",
        )
    assert over_limit.value.code == "material_request_reapproval_quantity_exceeds_instruction"

    _, regional_reapproval = _approve(
        db,
        actor=manager,
        request=request,
        request_version=returned.request_version,
        quantities={line.id: Decimal("1.000")},
        key="approval-return-step2-region-reapproval",
    )
    reopened_step2 = _current_step(db, request.id)
    assert (reopened_step2.step_no, reopened_step2.attempt_no) == (2, 2)
    _, headquarters_reapproval = _approve(
        db,
        actor=admin,
        request=request,
        request_version=regional_reapproval.request_version,
        quantities={line.id: Decimal("1.000")},
        key="approval-return-step2-hq-reapproval",
    )
    reopened_step3 = _current_step(db, request.id)
    _assert_command_projection_times(
        db,
        request=request,
        result=headquarters_reapproval,
        operation="headquarters_decide",
        rows=(reopened_step2, original_pending_step3, reopened_step3),
    )
    assert (reopened_step3.step_no, reopened_step3.attempt_no) == (3, 2)
    assert headquarters_reapproval.current_step_id == reopened_step3.id
    assert db.scalar(select(func.count()).select_from(ApprovalReturnLineFact)) == 1


def test_step3_external_return_reopens_step2_and_continues_with_new_step3(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(db, key="approval-return-step3")
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
        key="approval-return-step3-region",
    )
    _, headquarters = _approve(
        db,
        actor=admin0,
        request=request,
        request_version=regional.request_version,
        quantities={line.id: line.requested_qty},
        key="approval-return-step3-hq",
    )
    evidence = _evidence(db, uploaded_by=admin0.user_id, marker="return-evidence")
    step3, registration = _register_external(
        db,
        actor=admin0,
        request=request,
        request_version=headquarters.request_version,
        evidence=evidence,
        key="approval-return-step3-register",
        action="return",
        return_lines=(
            ApprovalReturnInstruction(line.id, Decimal("1.000"), "总部重审一件"),
        ),
    )
    registration_row = db.get(
        ApprovalExternalRegistration,
        registration.registration_id,
    )
    assert registration_row is not None
    returned = _verify_external(
        db,
        actor=admin1,
        request=request,
        step=step3,
        registration_id=registration.registration_id,
        request_version=registration.request_version,
        step_version=registration.step_version,
        key="approval-return-step3-verify",
    )
    reopened_step2 = _current_step(db, request.id)
    _assert_command_projection_times(
        db,
        request=request,
        result=returned,
        operation="verify_external",
        rows=(step3, registration_row, reopened_step2),
    )
    assert (reopened_step2.step_no, reopened_step2.attempt_no) == (2, 2)
    _, headquarters_reapproval = _approve(
        db,
        actor=admin0,
        request=request,
        request_version=returned.request_version,
        quantities={line.id: Decimal("1.000")},
        key="approval-return-step3-hq-reapproval",
    )
    reopened_step3 = _current_step(db, request.id)
    assert (reopened_step3.step_no, reopened_step3.attempt_no) == (3, 2)
    assert reopened_step3.predecessor_step_id == reopened_step2.id
    assert reopened_step3.reopened_from_step_id is None
    assert headquarters_reapproval.current_step_id == reopened_step3.id


def test_external_reviewer_separation_rejection_retry_and_whole_reject_facts(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(db, key="approval-external-reject")
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
        key="approval-external-reject-region",
    )
    _, headquarters = _approve(
        db,
        actor=admin0,
        request=request,
        request_version=regional.request_version,
        quantities={line.id: line.requested_qty},
        key="approval-external-reject-hq",
    )
    wrong_owner_evidence = _evidence(
        db, uploaded_by=admin1.user_id, marker="wrong-owner-evidence"
    )
    with pytest.raises(MaterialRequestApprovalError) as wrong_owner:
        _register_external(
            db,
            actor=admin0,
            request=request,
            request_version=headquarters.request_version,
            evidence=wrong_owner_evidence,
            key="approval-external-wrong-owner",
            action="reject",
    )
    assert wrong_owner.value.code == "material_request_external_evidence_invalid"

    wrong_purpose_evidence = _evidence(
        db, uploaded_by=admin0.user_id, marker="wrong-purpose-evidence"
    )
    wrong_purpose_evidence.metadata_jsonb = {
        **wrong_purpose_evidence.metadata_jsonb,
        "purpose": "request_attachment",
    }
    db.flush()
    with pytest.raises(MaterialRequestApprovalError) as wrong_purpose:
        _register_external(
            db,
            actor=admin0,
            request=request,
            request_version=headquarters.request_version,
            evidence=wrong_purpose_evidence,
            key="approval-external-wrong-purpose",
            action="reject",
        )
    assert wrong_purpose.value.code == "material_request_external_evidence_invalid"

    first_evidence = _evidence(db, uploaded_by=admin0.user_id, marker="first-evidence")
    step3, first_registration = _register_external(
        db,
        actor=admin0,
        request=request,
        request_version=headquarters.request_version,
        evidence=first_evidence,
        key="approval-external-first-register",
        action="approve",
        lines=(ApprovalLineDecision(line.id, line.requested_qty, ""),),
    )
    first_registration_row = db.get(
        ApprovalExternalRegistration,
        first_registration.registration_id,
    )
    assert first_registration_row is not None
    with pytest.raises(MaterialRequestApprovalError) as self_review:
        _verify_external(
            db,
            actor=admin0,
            request=request,
            step=step3,
            registration_id=first_registration.registration_id,
            request_version=first_registration.request_version,
            step_version=first_registration.step_version,
            key="approval-external-self-review",
        )
    assert self_review.value.code == "request_external_registration_self_review_forbidden"

    rejected_evidence = _verify_external(
        db,
        actor=admin1,
        request=request,
        step=step3,
        registration_id=first_registration.registration_id,
        request_version=first_registration.request_version,
        step_version=first_registration.step_version,
        key="approval-external-review-reject",
        decision="reject",
    )
    _assert_command_projection_times(
        db,
        request=request,
        result=rejected_evidence,
        operation="verify_external",
        rows=(step3, first_registration_row),
    )
    with pytest.raises(MaterialRequestApprovalError) as old_registration:
        _verify_external(
            db,
            actor=admin1,
            request=request,
            step=step3,
            registration_id=first_registration.registration_id,
            request_version=rejected_evidence.request_version,
            step_version=rejected_evidence.step_version,
            key="approval-external-old-registration",
        )
    assert old_registration.value.code == "material_request_external_step_not_verifiable"

    second_evidence = _evidence(db, uploaded_by=admin0.user_id, marker="second-evidence")
    step3, second_registration = _register_external(
        db,
        actor=admin0,
        request=request,
        request_version=rejected_evidence.request_version,
        evidence=second_evidence,
        key="approval-external-second-register",
        action="reject",
    )
    second_registration_row = db.get(
        ApprovalExternalRegistration,
        second_registration.registration_id,
    )
    assert second_registration_row is not None
    final = _verify_external(
        db,
        actor=admin1,
        request=request,
        step=step3,
        registration_id=second_registration.registration_id,
        request_version=second_registration.request_version,
        step_version=second_registration.step_version,
        key="approval-external-second-verify",
    )
    _assert_command_projection_times(
        db,
        request=request,
        result=final,
        operation="verify_external",
        rows=(step3, second_registration_row, *lines),
    )
    official = db.scalar(
        select(ApprovalStepLineDecision).where(
            ApprovalStepLineDecision.step_id == step3.id
        )
    )
    registration_line = db.scalar(
        select(ApprovalExternalRegistrationLine).where(
            ApprovalExternalRegistrationLine.registration_id
            == second_registration.registration_id
        )
    )
    assert final.request_status == "rejected"
    assert official is not None and registration_line is not None
    assert official.approved_qty == registration_line.approved_qty == Decimal("0.000")
    assert official.rejected_qty == registration_line.rejected_qty == line.requested_qty


def test_version_idempotency_conflicts_and_audit_failure_are_atomic(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(db, key="approval-atomic")
    line = lines[0]
    manager = _principal(db, world.manager_users[0].id)
    admin = _principal(db, world.admin_users[0].id)
    step = _current_step(db, request.id)
    instance = db.scalar(
        select(ApprovalInstance).where(ApprovalInstance.request_id == request.id)
    )
    assert instance is not None
    pending_step2 = db.scalar(
        select(ApprovalStep).where(
            ApprovalStep.instance_id == instance.id,
            ApprovalStep.step_no == 2,
        )
    )
    assert pending_step2 is not None
    with pytest.raises(MaterialRequestApprovalError) as out_of_order:
        decide_material_request_approval(
            db,
            actor=admin,
            material_request_id=request.id,
            approval_step_id=pending_step2.id,
            expected_request_version=version,
            expected_step_version=pending_step2.version,
            decision=MaterialRequestApprovalInput(
                action="approve",
                lines=(ApprovalLineDecision(line.id, line.requested_qty, "同意"),),
            ),
            idempotency_key="approval-atomic-out-of-order",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-approval-atomic-out-of-order",
        )
    assert out_of_order.value.code == "material_request_approval_step_not_current"

    with pytest.raises(MaterialRequestApprovalError) as unauthorized:
        decide_material_request_approval(
            db,
            actor=admin,
            material_request_id=request.id,
            approval_step_id=step.id,
            expected_request_version=version,
            expected_step_version=step.version,
            decision=MaterialRequestApprovalInput(
                action="approve",
                lines=(ApprovalLineDecision(line.id, line.requested_qty, "同意"),),
            ),
            idempotency_key="approval-atomic-wrong-role",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-approval-atomic-wrong-role",
        )
    assert unauthorized.value.code == "material_request_approval_candidate_forbidden"

    with pytest.raises(MaterialRequestApprovalError) as stale_version:
        decide_material_request_approval(
            db,
            actor=manager,
            material_request_id=request.id,
            approval_step_id=step.id,
            expected_request_version=version + 1,
            expected_step_version=step.version,
            decision=MaterialRequestApprovalInput(
                action="approve",
                lines=(ApprovalLineDecision(line.id, line.requested_qty, ""),),
            ),
            idempotency_key="approval-atomic-version",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-approval-atomic-version",
        )
    assert stale_version.value.code == "material_request_version_conflict"
    with pytest.raises(MaterialRequestApprovalError) as stale_step_version:
        decide_material_request_approval(
            db,
            actor=manager,
            material_request_id=request.id,
            approval_step_id=step.id,
            expected_request_version=version,
            expected_step_version=step.version + 1,
            decision=MaterialRequestApprovalInput(
                action="approve",
                lines=(ApprovalLineDecision(line.id, line.requested_qty, "同意"),),
            ),
            idempotency_key="approval-atomic-step-version",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-approval-atomic-step-version",
        )
    assert stale_step_version.value.code == "material_request_approval_step_version_conflict"

    _, approved = _approve(
        db,
        actor=manager,
        request=request,
        request_version=version,
        quantities={line.id: line.requested_qty},
        key="approval-atomic-idempotent",
    )
    with pytest.raises(MaterialRequestApprovalError) as key_conflict:
        decide_material_request_approval(
            db,
            actor=manager,
            material_request_id=request.id,
            approval_step_id=step.id,
            expected_request_version=version,
            expected_step_version=0,
            decision=MaterialRequestApprovalInput(
                action="approve",
                lines=(ApprovalLineDecision(line.id, Decimal("1.000"), "减少"),),
                comment="不同负载",
            ),
            idempotency_key="approval-atomic-idempotent",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-approval-atomic-idempotent-conflict",
        )
    assert key_conflict.value.code == "material_request_approval_idempotency_conflict"

    db.commit()
    step2 = _current_step(db, request.id)
    before_command_count = db.scalar(select(func.count()).select_from(MaterialRequestCommand))
    before_audit_count = db.scalar(select(func.count()).select_from(AuditEvent))
    with patch(
        "app.formal_services.material_request_approval.append_audit_event",
        side_effect=AuditChainError("audit unavailable"),
    ):
        with pytest.raises(MaterialRequestApprovalError) as audit_failure:
            decide_material_request_approval(
                db,
                actor=_principal(db, world.admin_users[0].id),
                material_request_id=request.id,
                approval_step_id=step2.id,
                expected_request_version=approved.request_version,
                expected_step_version=step2.version,
                decision=MaterialRequestApprovalInput(
                    action="approve",
                    lines=(ApprovalLineDecision(line.id, line.requested_qty, ""),),
                ),
                idempotency_key="approval-atomic-audit",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-approval-atomic-audit",
            )
    assert audit_failure.value.code == "material_request_approval_audit_unavailable"
    db.rollback()
    assert db.scalar(select(func.count()).select_from(MaterialRequestCommand)) == before_command_count
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == before_audit_count
    step2 = db.get(ApprovalStep, step2.id)
    assert step2 is not None and step2.status == "open"


def test_terminal_replay_requires_current_actor_authorization_and_active_account(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(db, key="approval-replay-auth")
    line = lines[0]
    manager = _principal(db, world.manager_users[0].id)
    admin = _principal(db, world.admin_users[0].id)
    step1, regional = _approve(
        db,
        actor=manager,
        request=request,
        request_version=version,
        quantities={line.id: line.requested_qty},
        key="approval-replay-auth-region",
    )
    manager_row = db.get(User, manager.user_id)
    assert manager_row is not None
    manager_row.authorization_version += 1
    db.flush()
    with pytest.raises(MaterialRequestApprovalError) as stale_authorization:
        decide_material_request_approval(
            db,
            actor=manager,
            material_request_id=request.id,
            approval_step_id=step1.id,
            expected_request_version=version,
            expected_step_version=0,
            decision=MaterialRequestApprovalInput(
                action="approve",
                lines=(ApprovalLineDecision(line.id, line.requested_qty, "同意"),),
                comment="同意",
            ),
            idempotency_key="approval-replay-auth-region",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-approval-replay-auth-region-replay",
        )
    assert stale_authorization.value.code == "material_request_approval_actor_not_current"

    step2 = _current_step(db, request.id)
    original_step2_version = step2.version
    _, headquarters = _approve(
        db,
        actor=admin,
        request=request,
        request_version=regional.request_version,
        quantities={line.id: line.requested_qty},
        key="approval-replay-auth-hq",
    )
    admin_row = db.get(User, admin.user_id)
    assert admin_row is not None
    admin_row.account_status = "disabled"
    db.flush()
    with pytest.raises(MaterialRequestApprovalError) as inactive_account:
        decide_material_request_approval(
            db,
            actor=admin,
            material_request_id=request.id,
            approval_step_id=step2.id,
            expected_request_version=regional.request_version,
            expected_step_version=original_step2_version,
            decision=MaterialRequestApprovalInput(
                action="approve",
                lines=(ApprovalLineDecision(line.id, line.requested_qty, "同意"),),
                comment="同意",
            ),
            idempotency_key="approval-replay-auth-hq",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-approval-replay-auth-hq-replay",
        )
    assert inactive_account.value.code == "material_request_approval_actor_not_current"
    assert headquarters.current_step_no == 3


def test_stale_pooled_member_does_not_block_but_insufficient_frozen_pool_fails_closed(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(
        db, key="approval-stale-pool", admin_count=3
    )
    line = lines[0]
    manager = _principal(db, world.manager_users[0].id)
    admin0 = _principal(db, world.admin_users[0].id)
    _, first = _approve(
        db,
        actor=manager,
        request=request,
        request_version=version,
        quantities={line.id: line.requested_qty},
        key="approval-stale-pool-region",
    )
    step2 = _current_step(db, request.id)
    returned = decide_material_request_approval(
        db,
        actor=admin0,
        material_request_id=request.id,
        approval_step_id=step2.id,
        expected_request_version=first.request_version,
        expected_step_version=step2.version,
        decision=MaterialRequestApprovalInput(
            action="return",
            return_lines=(ApprovalReturnInstruction(line.id, line.requested_qty, "重审"),),
            comment="退回",
        ),
        idempotency_key="approval-stale-pool-return",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-approval-stale-pool-return",
    )
    stale_assignment = db.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == world.admin_users[2].id,
            RoleAssignment.status == "active",
        )
    )
    assert stale_assignment is not None
    stale_assignment.status = "expired"
    stale_assignment.valid_to = NOW
    db.flush()
    _, reopened = _approve(
        db,
        actor=manager,
        request=request,
        request_version=returned.request_version,
        quantities={line.id: line.requested_qty},
        key="approval-stale-pool-reopen",
    )
    assert reopened.current_step_no == 2


def test_insufficient_frozen_external_pool_fails_closed_on_new_attempt(
    approval_db: Session,
) -> None:
    db = approval_db
    world, request, lines, version = _submitted(
        db, key="approval-insufficient-pool", admin_count=2
    )
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
        key="approval-insufficient-pool-region",
    )
    _, headquarters = _approve(
        db,
        actor=admin0,
        request=request,
        request_version=regional.request_version,
        quantities={line.id: line.requested_qty},
        key="approval-insufficient-pool-hq",
    )
    evidence = _evidence(db, uploaded_by=admin0.user_id, marker="insufficient-return")
    step3, registration = _register_external(
        db,
        actor=admin0,
        request=request,
        request_version=headquarters.request_version,
        evidence=evidence,
        key="approval-insufficient-pool-register",
        action="return",
        return_lines=(ApprovalReturnInstruction(line.id, line.requested_qty, "重审"),),
    )
    returned = _verify_external(
        db,
        actor=admin1,
        request=request,
        step=step3,
        registration_id=registration.registration_id,
        request_version=registration.request_version,
        step_version=registration.step_version,
        key="approval-insufficient-pool-verify",
    )
    stale_external_assignment = db.scalar(
        select(RoleAssignment).where(
            RoleAssignment.user_id == world.admin_users[1].id,
            RoleAssignment.status == "active",
        )
    )
    assert stale_external_assignment is not None
    stale_external_assignment.status = "expired"
    stale_external_assignment.valid_to = NOW
    db.flush()
    with pytest.raises(MaterialRequestApprovalError) as insufficient:
        _approve(
            db,
            actor=admin0,
            request=request,
            request_version=returned.request_version,
            quantities={line.id: line.requested_qty},
            key="approval-insufficient-pool-hq-reapproval",
        )
    assert insufficient.value.code == "material_request_external_candidate_pool_insufficient"
