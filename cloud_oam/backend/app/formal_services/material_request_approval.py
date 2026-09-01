"""Formal V1.0 material-request approval transaction service.

The service owns only the approval axis of one sealed material-request
revision.  It deliberately cannot allocate, reserve, pick, dispatch, ship,
record a logistics signature, record an OAM receipt, post a personal inbound,
send a notification, or advance reconciliation.

Every public command is bound to an exact request version and current approval
step version.  The caller owns the surrounding transaction: functions flush,
but never commit or roll back.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import re
from typing import Any, Final, Mapping, Sequence
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..demand_models import (
    ApprovalAction,
    ApprovalExternalRegistration,
    ApprovalExternalRegistrationLine,
    ApprovalInstance,
    ApprovalReturnLineFact as ApprovalReturnLineFactRow,
    ApprovalStep,
    ApprovalStepCandidate,
    ApprovalStepLineDecision,
    MaterialRequest,
    MaterialRequestCommand,
    MaterialRequestFile,
    MaterialRequestLine,
    MaterialRequestRevision,
)
from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    ScopeGrant,
    load_formal_principal,
    lock_formal_principal_graph,
)
from ..foundation_models import (
    FileObject,
    RoleAssignment,
    StateTransitionEvent,
)
from . import formal_files as formal_file_service
from .audit_chain import AuditChainError, append_audit_event
from .material_request_policy import (
    ApprovalLineDecision,
    ApprovalLineInput,
    ApprovalLineOutcome,
    ApprovalReturnInstruction,
    MaterialRequestPolicyError,
    NON_APPROVAL_STATE_AXES,
    assert_approval_did_not_advance_fulfillment_axes,
    final_request_approval_status_from_chain,
    reconstruct_final_approval_quantities,
    require_external_registration_review_separation,
    require_request_status_transition,
    validate_line_approval_decisions,
    validate_return_reapproval_instructions,
)


MATERIAL_REQUEST_AUDIT_STREAM: Final[str] = "material_request"
MATERIAL_REQUEST_AGGREGATE: Final[str] = "material_request"
_CURRENT_STEP_STATUSES: Final[frozenset[str]] = frozenset(
    {"open", "awaiting_external_evidence", "evidence_pending_verification"}
)
_NEUTRAL_AXES: Final[dict[str, str]] = {
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
_ROLE_BY_STEP: Final[dict[int, str]] = {
    1: "provincial_manager",
    2: "admin",
    3: "star_headquarters_approver",
}
_SAFE_TRACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,159}$", re.ASCII)
_PRINTABLE = re.compile(r"^[\x21-\x7e]{1,200}$", re.ASCII)
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,199}$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PLACEHOLDERS = ("replace-with", "replace_me", "replace-me", "change-me", "changeme")

_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class MaterialRequestApprovalError(RuntimeError):
    """Stable, database-detail-free approval command failure."""

    def __init__(self, code: str, category: str, message: str) -> None:
        if category not in _HTTP_STATUS_BY_CATEGORY:
            raise ValueError(f"unsupported error category: {category}")
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message

    @property
    def http_status_code(self) -> int:
        return _HTTP_STATUS_BY_CATEGORY[self.category]

    def as_detail(self) -> dict[str, str]:
        return {
            "code": self.code,
            "category": self.category,
            "message": self.message,
        }


@dataclass(frozen=True, slots=True)
class MaterialRequestApprovalInput:
    action: str
    lines: tuple[ApprovalLineDecision, ...] = ()
    return_lines: tuple[ApprovalReturnInstruction, ...] = ()
    comment: str = ""


@dataclass(frozen=True, slots=True)
class ExternalApprovalRegistrationInput:
    evidence_file_id: uuid.UUID
    external_approver_name: str
    external_reference_no: str
    external_decided_at: datetime
    action: str
    lines: tuple[ApprovalLineDecision, ...] = ()
    return_lines: tuple[ApprovalReturnInstruction, ...] = ()
    comment: str = ""


@dataclass(frozen=True, slots=True)
class ApprovalCommandResult:
    request_id: uuid.UUID
    request_no: str
    request_status: str
    request_version: int
    revision_id: uuid.UUID
    revision_no: int
    instance_id: uuid.UUID
    instance_status: str
    instance_version: int
    decided_step_id: uuid.UUID
    decided_step_no: int
    decided_step_attempt_no: int
    decided_step_status: str
    decided_step_version: int
    current_step_id: uuid.UUID | None
    current_step_no: int | None
    opened_step_id: uuid.UUID | None
    opened_step_attempt_no: int | None
    state_axes: Mapping[str, str]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ExternalApprovalRegistrationResult:
    request_id: uuid.UUID
    request_no: str
    request_status: str
    request_version: int
    revision_id: uuid.UUID
    revision_no: int
    instance_id: uuid.UUID
    instance_version: int
    step_id: uuid.UUID
    step_attempt_no: int
    step_status: str
    step_version: int
    registration_id: uuid.UUID
    registration_no: str
    registration_status: str
    external_action: str
    decision_manifest_sha256: str
    state_axes: Mapping[str, str]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class ExternalApprovalVerificationResult:
    request_id: uuid.UUID
    request_no: str
    request_status: str
    request_version: int
    revision_id: uuid.UUID
    revision_no: int
    instance_id: uuid.UUID
    instance_status: str
    instance_version: int
    step_id: uuid.UUID
    step_attempt_no: int
    step_status: str
    step_version: int
    registration_id: uuid.UUID
    registration_status: str
    verification_decision: str
    current_step_id: uuid.UUID | None
    current_step_no: int | None
    opened_step_id: uuid.UUID | None
    opened_step_attempt_no: int | None
    state_axes: Mapping[str, str]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _ActorContext:
    principal: FormalPrincipal
    grant: ScopeGrant
    candidate: ApprovalStepCandidate


@dataclass(frozen=True, slots=True)
class _LockedApproval:
    request: MaterialRequest
    revision: MaterialRequestRevision
    lines: tuple[MaterialRequestLine, ...]
    instance: ApprovalInstance
    step: ApprovalStep


def decide_material_request_approval(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    approval_step_id: uuid.UUID,
    expected_request_version: int,
    expected_step_version: int,
    decision: MaterialRequestApprovalInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> ApprovalCommandResult:
    """Decide the exact current internal regional or headquarters step."""

    return _public_boundary(
        lambda: _decide_material_request_approval_impl(
            db,
            actor=actor,
            material_request_id=material_request_id,
            approval_step_id=approval_step_id,
            expected_request_version=expected_request_version,
            expected_step_version=expected_step_version,
            decision=decision,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def register_external_approval_evidence(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    approval_step_id: uuid.UUID,
    expected_request_version: int,
    expected_step_version: int,
    registration: ExternalApprovalRegistrationInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> ExternalApprovalRegistrationResult:
    """Register third-stage external evidence without pretending to be Star."""

    return _public_boundary(
        lambda: _register_external_approval_evidence_impl(
            db,
            actor=actor,
            material_request_id=material_request_id,
            approval_step_id=approval_step_id,
            expected_request_version=expected_request_version,
            expected_step_version=expected_step_version,
            registration=registration,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def verify_external_approval_evidence(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    approval_step_id: uuid.UUID,
    registration_id: uuid.UUID,
    expected_request_version: int,
    expected_step_version: int,
    verification_decision: str,
    comment: str,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> ExternalApprovalVerificationResult:
    """Accept or reject one registered external decision as a second admin."""

    return _public_boundary(
        lambda: _verify_external_approval_evidence_impl(
            db,
            actor=actor,
            material_request_id=material_request_id,
            approval_step_id=approval_step_id,
            registration_id=registration_id,
            expected_request_version=expected_request_version,
            expected_step_version=expected_step_version,
            verification_decision=verification_decision,
            comment=comment,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def _public_boundary(operation):
    try:
        return operation()
    except MaterialRequestApprovalError:
        raise
    except MaterialRequestPolicyError as exc:
        raise MaterialRequestApprovalError(exc.code, exc.category, exc.message) from None
    except FormalAccessError:
        raise MaterialRequestApprovalError(
            "material_request_approval_actor_not_current",
            "forbidden",
            "正式审批权限上下文已失效，请重新读取后再操作",
        ) from None
    except AuditChainError:
        raise MaterialRequestApprovalError(
            "material_request_approval_audit_unavailable",
            "service_unavailable",
            "审批审计链不可用，本次操作未完成",
        ) from None
    except IntegrityError:
        raise MaterialRequestApprovalError(
            "material_request_approval_concurrent_conflict",
            "conflict",
            "审批发生并发冲突，请回滚并重新读取后再操作",
        ) from None
    except DBAPIError:
        raise MaterialRequestApprovalError(
            "material_request_approval_database_unavailable",
            "service_unavailable",
            "数据库暂时不可用，本次审批操作未完成",
        ) from None


def _decide_material_request_approval_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    approval_step_id: uuid.UUID,
    expected_request_version: int,
    expected_step_version: int,
    decision: MaterialRequestApprovalInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> ApprovalCommandResult:
    supplied = _validate_supplied_actor(actor)
    request_id = _require_uuid("material_request_id", material_request_id)
    step_id = _require_uuid("approval_step_id", approval_step_id)
    request_version = _require_version("expected_request_version", expected_request_version)
    step_version = _require_version("expected_step_version", expected_step_version)
    prepared = _validate_internal_decision(decision)
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    operation_hint = _internal_operation_hint(db, step_id)
    path = f"/api/v1/material-requests/{request_id}/approval-steps/{step_id}/decision"
    key_hash = _idempotency_hmac(secret, supplied.user_id, "POST", path, raw_key)
    payload_hash = _canonical_hash(
        {
            "operation": operation_hint,
            "request_id": str(request_id),
            "step_id": str(step_id),
            "actor_user_id": supplied.user_id,
            "actor_authorization_version": supplied.authorization_version,
            "expected_request_version": request_version,
            "expected_step_version": step_version,
            "action": prepared.action,
            "lines": _line_decision_documents(prepared.lines),
            "return_lines": _return_instruction_documents(prepared.return_lines),
            "comment": prepared.comment,
        }
    )
    _take_advisory_locks(db, key_hash, request_id)
    locked_request = _lock_request(db, request_id)
    candidate_user_ids = _request_candidate_user_ids(db, request_id)
    lock_formal_principal_graph(
        db, tuple(sorted({supplied.user_id, *candidate_user_ids}))
    )
    if _request_candidate_user_ids(db, request_id) != candidate_user_ids:
        _fail(
            "material_request_approval_candidate_set_changed",
            "conflict",
            "审批候选人在锁定期间发生变化，请重新读取后操作",
        )
    now = _database_now(db)
    supplied = _reload_current_actor(db, supplied, now=now)
    replay = _load_result_replay(
        db,
        key_hash=key_hash,
        request_hash=payload_hash,
        operation=operation_hint,
        request_id=request_id,
        request_reference=path,
        actor=supplied,
        kind="approval_decision",
    )
    if replay is not None:
        return replace(_approval_result_from_json(replay), replayed=True)
    locked = _lock_current_approval(
        db,
        request_id=request_id,
        step_id=step_id,
        request=locked_request,
    )
    operation = "region_decide" if locked.step.step_no == 1 else "headquarters_decide"
    if operation != operation_hint:
        _fail("material_request_approval_step_changed", "conflict", "审批步骤已变化，请重新读取")
    _require_expected_versions(locked, request_version, step_version)
    if locked.step.step_no not in {1, 2} or locked.step.source_mode != "internal":
        _fail("material_request_internal_step_required", "conflict", "当前步骤不是内部审批步骤")
    actor_context = _require_current_candidate(
        db,
        supplied=supplied,
        locked=locked,
        candidate_kind="assignee",
        permission_action=(
            "approve_region" if locked.step.step_no == 1 else "approve_headquarters"
        ),
        permission_field="approval_decision",
        now=now,
    )
    if locked.step.step_no == 1 and locked.step.assignee_user_id != supplied.user_id:
        _fail("material_request_approval_assignee_mismatch", "forbidden", "当前账号不是冻结的区域审批人")
    before_axes = _request_axes(locked.request)
    assert_approval_did_not_advance_fulfillment_axes(_NEUTRAL_AXES, before_axes)
    inputs = _current_step_inputs(db, locked)
    before = _safe_approval_snapshot(locked, inputs)

    if prepared.action == "return":
        result = _return_internal_step(
            db,
            locked=locked,
            actor=actor_context,
            instructions=prepared.return_lines,
            comment=prepared.comment,
            key_hash=key_hash,
            payload_hash=payload_hash,
            request_reference=path,
            trace_request_id=trace_id,
            before=before,
            now=now,
        )
    else:
        decisions = (
            prepared.lines
            if prepared.action == "approve"
            else tuple(
                ApprovalLineDecision(
                    request_line_id=row.request_line_id,
                    approved_qty=Decimal("0.000"),
                    reason=prepared.comment,
                )
                for row in inputs
            )
        )
        outcomes = validate_line_approval_decisions(inputs, decisions)
        result = _complete_internal_step(
            db,
            locked=locked,
            actor=actor_context,
            outcomes=outcomes,
            comment=prepared.comment,
            key_hash=key_hash,
            payload_hash=payload_hash,
            request_reference=path,
            trace_request_id=trace_id,
            before=before,
            now=now,
        )
    assert_approval_did_not_advance_fulfillment_axes(before_axes, _request_axes(locked.request))
    db.flush()
    return result


def _register_external_approval_evidence_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    approval_step_id: uuid.UUID,
    expected_request_version: int,
    expected_step_version: int,
    registration: ExternalApprovalRegistrationInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> ExternalApprovalRegistrationResult:
    supplied = _validate_supplied_actor(actor)
    request_id = _require_uuid("material_request_id", material_request_id)
    step_id = _require_uuid("approval_step_id", approval_step_id)
    request_version = _require_version("expected_request_version", expected_request_version)
    step_version = _require_version("expected_step_version", expected_step_version)
    prepared = _validate_external_registration(registration)
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    path = f"/api/v1/material-requests/{request_id}/approval-steps/{step_id}/external-evidence"
    key_hash = _idempotency_hmac(secret, supplied.user_id, "POST", path, raw_key)
    payload_hash = _canonical_hash(
        {
            "operation": "register_external",
            "request_id": str(request_id),
            "step_id": str(step_id),
            "actor_user_id": supplied.user_id,
            "actor_authorization_version": supplied.authorization_version,
            "expected_request_version": request_version,
            "expected_step_version": step_version,
            "evidence_file_id": str(prepared.evidence_file_id),
            "external_approver_name_sha256": _text_hash(prepared.external_approver_name),
            "external_reference_sha256": _text_hash(prepared.external_reference_no),
            "external_decided_at": prepared.external_decided_at.isoformat(),
            "action": prepared.action,
            "lines": _line_decision_documents(prepared.lines),
            "return_lines": _return_instruction_documents(prepared.return_lines),
            "comment": prepared.comment,
        }
    )
    _take_advisory_locks(db, key_hash, request_id)
    locked_request = _lock_request(db, request_id)
    candidate_user_ids = _request_candidate_user_ids(db, request_id)
    lock_formal_principal_graph(
        db, tuple(sorted({supplied.user_id, *candidate_user_ids}))
    )
    if _request_candidate_user_ids(db, request_id) != candidate_user_ids:
        _fail(
            "material_request_approval_candidate_set_changed",
            "conflict",
            "审批候选人在锁定期间发生变化，请重新读取后操作",
        )
    now = _database_now(db)
    supplied = _reload_current_actor(db, supplied, now=now)
    replay = _load_result_replay(
        db,
        key_hash=key_hash,
        request_hash=payload_hash,
        operation="register_external",
        request_id=request_id,
        request_reference=path,
        actor=supplied,
        kind="external_registration",
    )
    if replay is not None:
        return replace(_external_registration_result_from_json(replay), replayed=True)
    locked = _lock_current_approval(
        db,
        request_id=request_id,
        step_id=step_id,
        request=locked_request,
    )
    _require_expected_versions(locked, request_version, step_version)
    if (
        locked.step.step_no != 3
        or locked.step.source_mode != "external_registration"
        or locked.step.status != "awaiting_external_evidence"
    ):
        _fail("material_request_external_step_not_registerable", "conflict", "当前步骤不允许登记外部审批证据")
    _require_step_candidate_set_current(
        db, locked.step, request=locked.request, now=now
    )
    actor_context = _require_current_candidate(
        db,
        supplied=supplied,
        locked=locked,
        candidate_kind="registrar",
        permission_action="register_external",
        permission_field="approval_evidence",
        now=now,
    )
    _require_not_requester(locked.request, actor_context.principal)
    inputs = _current_step_inputs(db, locked)
    before_axes = _request_axes(locked.request)
    assert_approval_did_not_advance_fulfillment_axes(_NEUTRAL_AXES, before_axes)
    before = _safe_approval_snapshot(locked, inputs)
    if prepared.external_decided_at > now or (
        locked.step.opened_at is not None and prepared.external_decided_at < _as_utc(locked.step.opened_at)
    ):
        _fail(
            "material_request_external_decision_time_invalid",
            "invalid_request",
            "外部审批时间必须位于当前外部审批步骤打开后且不得晚于数据库当前时间",
        )
    evidence = _require_external_evidence_file(
        db, prepared.evidence_file_id, registrant_user_id=supplied.user_id
    )

    if prepared.action == "approve":
        outcomes = validate_line_approval_decisions(inputs, prepared.lines)
        external_action, _ = _terminal_outcome(outcomes)
    elif prepared.action == "reject":
        outcomes = validate_line_approval_decisions(
            inputs,
            tuple(
                ApprovalLineDecision(
                    request_line_id=row.request_line_id,
                    approved_qty=Decimal("0.000"),
                    reason=prepared.comment,
                )
                for row in inputs
            ),
        )
        external_action = "reject"
    else:
        outcomes = ()
        external_action = "return"
        target_max = _return_target_max_quantities(db, locked)
        validate_return_reapproval_instructions(
            returned_step_inputs=inputs,
            target_step_max_quantities=target_max,
            instructions=prepared.return_lines,
        )

    registration_id = uuid.uuid4()
    registration_no = f"EXT-{now:%Y%m%d}-{registration_id.hex[:12].upper()}"
    manifest_document = _external_manifest_document(
        locked=locked,
        evidence=evidence,
        external_approver_name=prepared.external_approver_name,
        external_reference_no=prepared.external_reference_no,
        external_decided_at=prepared.external_decided_at,
        external_action=external_action,
        outcomes=outcomes,
        return_lines=prepared.return_lines,
        comment=prepared.comment,
    )
    manifest_hash = _canonical_hash(manifest_document)
    row = ApprovalExternalRegistration(
        id=registration_id,
        step_id=locked.step.id,
        registration_no=registration_no,
        external_action=external_action,
        status="pending_verification",
        evidence_file_id=evidence.id,
        external_approver_snapshot_jsonb={
            "schema": "rsc.external_approver_snapshot.v1",
            "display_name": prepared.external_approver_name,
            "external_reference_no": prepared.external_reference_no,
            "evidence_sha256": evidence.sha256,
        },
        external_decided_at=prepared.external_decided_at,
        decision_manifest_sha256=manifest_hash,
        registered_by_user_id=actor_context.principal.user_id,
        registered_by_person_id=actor_context.principal.person_id,
        registered_role_assignment_id=actor_context.grant.assignment_id,
        authorization_version=actor_context.principal.authorization_version,
        registered_at=now,
        verified_by_user_id=None,
        verified_by_person_id=None,
        verified_role_assignment_id=None,
        verified_authorization_version=None,
        verification_comment="",
        verified_at=None,
        version=0,
        created_at=now,
        updated_at=now,
    )
    db.add(row)
    db.flush()
    if outcomes:
        db.add_all(
            ApprovalExternalRegistrationLine(
                id=uuid.uuid4(),
                registration_id=row.id,
                request_line_id=outcome.request_line_id,
                input_qty=outcome.input_qty,
                approved_qty=outcome.approved_qty,
                rejected_qty=outcome.rejected_qty,
                reason=outcome.reason,
                created_at=now,
            )
            for outcome in outcomes
        )
        db.flush()

    locked.request.version += 1
    locked.request.updated_at = now
    locked.step.status = "evidence_pending_verification"
    locked.step.version += 1
    locked.step.updated_at = now
    locked.instance.version += 1
    locked.instance.updated_at = now
    result = ExternalApprovalRegistrationResult(
        request_id=locked.request.id,
        request_no=locked.request.request_no,
        request_status=locked.request.status,
        request_version=locked.request.version,
        revision_id=locked.revision.id,
        revision_no=locked.revision.revision_no,
        instance_id=locked.instance.id,
        instance_version=locked.instance.version,
        step_id=locked.step.id,
        step_attempt_no=locked.step.attempt_no,
        step_status=locked.step.status,
        step_version=locked.step.version,
        registration_id=row.id,
        registration_no=row.registration_no,
        registration_status=row.status,
        external_action=row.external_action,
        decision_manifest_sha256=row.decision_manifest_sha256,
        state_axes=dict(_request_axes(locked.request)),
    )
    command = _command_fact(
        operation="register_external",
        locked=locked,
        actor=actor_context,
        target_version=locked.request.version,
        key_hash=key_hash,
        request_reference=path,
        request_hash=payload_hash,
        result_document=_external_registration_result_document(result),
        request_document={
            "external_action": external_action,
            "registration_id": str(row.id),
            "return_lines": _return_instruction_documents(prepared.return_lines),
            "registration_comment": prepared.comment,
            "decision_manifest_sha256": manifest_hash,
            "sensitive_fields": "excluded",
        },
        occurred_at=now,
    )
    db.add(command)
    db.flush()
    action = ApprovalAction(
        id=uuid.uuid4(),
        instance_id=locked.instance.id,
        step_id=locked.step.id,
        command_id=command.id,
        action="register_external_evidence",
        actor_user_id=actor_context.principal.user_id,
        actor_person_id=actor_context.principal.person_id,
        actor_role_assignment_id=actor_context.grant.assignment_id,
        authorization_version=actor_context.principal.authorization_version,
        source_mode="external_registration",
        comment=prepared.comment,
        occurred_at=now,
        created_at=now,
    )
    db.add_all(
        (
            action,
            _state_event(
                aggregate_type="approval_step",
                aggregate_id=locked.step.id,
                from_status="awaiting_external_evidence",
                to_status="evidence_pending_verification",
                reason="external_approval_evidence_registered",
                actor_user_id=actor_context.principal.user_id,
                key_hash=key_hash,
                suffix="evidence-pending-verification",
                occurred_at=now,
                metadata={
                    "request_id": str(locked.request.id),
                    "revision_id": str(locked.revision.id),
                    "instance_id": str(locked.instance.id),
                    "step_id": str(locked.step.id),
                    "step_attempt_no": locked.step.attempt_no,
                    "registration_id": str(row.id),
                },
            ),
        )
    )
    db.flush()
    append_audit_event(
        db,
        stream_key=MATERIAL_REQUEST_AUDIT_STREAM,
        actor_user_id=actor_context.principal.user_id,
        action="material_request.external_evidence.register",
        aggregate_type=MATERIAL_REQUEST_AGGREGATE,
        aggregate_id=str(locked.request.id),
        before_jsonb=before,
        after_jsonb={
            **_safe_approval_snapshot(locked, inputs),
            "registration_id": str(row.id),
            "registration_status": row.status,
            "external_action": row.external_action,
            "evidence_sha256": evidence.sha256,
            "decision_manifest_sha256": row.decision_manifest_sha256,
            "sensitive_fields": "excluded",
        },
        request_id=trace_id,
        occurred_at=now,
    )
    assert_approval_did_not_advance_fulfillment_axes(before_axes, _request_axes(locked.request))
    db.flush()
    return result


def _verify_external_approval_evidence_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    approval_step_id: uuid.UUID,
    registration_id: uuid.UUID,
    expected_request_version: int,
    expected_step_version: int,
    verification_decision: str,
    comment: str,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> ExternalApprovalVerificationResult:
    supplied = _validate_supplied_actor(actor)
    request_id = _require_uuid("material_request_id", material_request_id)
    step_id = _require_uuid("approval_step_id", approval_step_id)
    checked_registration_id = _require_uuid("registration_id", registration_id)
    request_version = _require_version("expected_request_version", expected_request_version)
    step_version = _require_version("expected_step_version", expected_step_version)
    decision = _require_choice("verification_decision", verification_decision, {"accept", "reject"})
    checked_comment = _require_text("verification_comment", comment, 4000, required=decision == "reject")
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    path = (
        f"/api/v1/material-requests/{request_id}/approval-steps/{step_id}"
        f"/external-evidence/{checked_registration_id}/verification"
    )
    # material_request_commands.request_reference is intentionally bounded to
    # varchar(160).  Keep the full HTTP path in the HMAC domain while storing a
    # lossless compact reference for the three UUID business keys.
    command_reference = (
        f"/mr/{request_id}/steps/{step_id}/external/"
        f"{checked_registration_id}/verify"
    )
    key_hash = _idempotency_hmac(secret, supplied.user_id, "POST", path, raw_key)
    payload_hash = _canonical_hash(
        {
            "operation": "verify_external",
            "request_id": str(request_id),
            "step_id": str(step_id),
            "registration_id": str(checked_registration_id),
            "actor_user_id": supplied.user_id,
            "actor_authorization_version": supplied.authorization_version,
            "expected_request_version": request_version,
            "expected_step_version": step_version,
            "decision": decision,
            "comment": checked_comment,
        }
    )
    _take_advisory_locks(db, key_hash, request_id)
    locked_request = _lock_request(db, request_id)
    candidate_user_ids = _request_candidate_user_ids(db, request_id)
    lock_formal_principal_graph(
        db, tuple(sorted({supplied.user_id, *candidate_user_ids}))
    )
    if _request_candidate_user_ids(db, request_id) != candidate_user_ids:
        _fail(
            "material_request_approval_candidate_set_changed",
            "conflict",
            "审批候选人在锁定期间发生变化，请重新读取后操作",
        )
    now = _database_now(db)
    supplied = _reload_current_actor(db, supplied, now=now)
    replay = _load_result_replay(
        db,
        key_hash=key_hash,
        request_hash=payload_hash,
        operation="verify_external",
        request_id=request_id,
        request_reference=command_reference,
        actor=supplied,
        kind="external_verification",
    )
    if replay is not None:
        return replace(_external_verification_result_from_json(replay), replayed=True)
    locked = _lock_current_approval(
        db,
        request_id=request_id,
        step_id=step_id,
        request=locked_request,
    )
    _require_expected_versions(locked, request_version, step_version)
    if (
        locked.step.step_no != 3
        or locked.step.source_mode != "external_registration"
        or locked.step.status != "evidence_pending_verification"
    ):
        _fail("material_request_external_step_not_verifiable", "conflict", "当前步骤不允许复核外部审批证据")
    _require_step_candidate_set_current(
        db, locked.step, request=locked.request, now=now
    )
    actor_context = _require_current_candidate(
        db,
        supplied=supplied,
        locked=locked,
        candidate_kind="verifier",
        permission_action="verify_external",
        permission_field="approval_evidence",
        now=now,
    )
    registration = db.scalar(
        select(ApprovalExternalRegistration)
        .where(
            ApprovalExternalRegistration.id == checked_registration_id,
            ApprovalExternalRegistration.step_id == locked.step.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if registration is None:
        _fail("material_request_external_registration_not_found", "not_found", "外部审批登记不存在")
    if registration.status != "pending_verification" or registration.version != 0:
        _fail("material_request_external_registration_not_pending", "conflict", "外部审批登记已被处理")
    require_external_registration_review_separation(
        requester_user_id=locked.request.requester_user_id,
        requester_person_id=locked.request.requester_person_id,
        registrant_user_id=registration.registered_by_user_id,
        registrant_person_id=registration.registered_by_person_id,
        reviewer_user_id=actor_context.principal.user_id,
        reviewer_person_id=actor_context.principal.person_id,
    )
    inputs = _current_step_inputs(db, locked)
    before_axes = _request_axes(locked.request)
    assert_approval_did_not_advance_fulfillment_axes(_NEUTRAL_AXES, before_axes)
    before = _safe_approval_snapshot(locked, inputs)
    registration_lines = _lock_registration_lines(db, registration.id)
    register_command = _require_registration_command(db, locked, registration)
    return_instructions = _return_instructions_from_registration_command(
        register_command, registration
    )
    evidence = db.scalar(
        select(FileObject)
        .where(FileObject.id == registration.evidence_file_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )

    if decision == "accept":
        if evidence is None or not formal_file_service.is_available_formal_file_for_purpose(
            evidence,
            purpose="external_approval_evidence",
        ):
            _fail("material_request_external_evidence_unavailable", "precondition_failed", "外部审批证据文件当前不可用")
        _verify_external_registration_manifest(
            locked=locked,
            registration=registration,
            registration_lines=registration_lines,
            return_instructions=return_instructions,
            register_command=register_command,
            evidence=evidence,
        )

    registration.status = "accepted" if decision == "accept" else "rejected"
    registration.verified_by_user_id = actor_context.principal.user_id
    registration.verified_by_person_id = actor_context.principal.person_id
    registration.verified_role_assignment_id = actor_context.grant.assignment_id
    registration.verified_authorization_version = actor_context.principal.authorization_version
    registration.verification_comment = checked_comment
    registration.verified_at = now
    registration.version += 1
    registration.updated_at = now
    db.flush()

    locked.request.version += 1
    locked.request.updated_at = now
    locked.step.version += 1
    locked.step.updated_at = now
    locked.instance.version += 1
    locked.instance.updated_at = now
    opened_step: ApprovalStep | None = None
    return_facts: Sequence[Any] = ()
    outcomes: tuple[ApprovalLineOutcome, ...] = ()
    terminal_action: str | None = None
    terminal_step_status: str | None = None
    decision_manifest: str | None = None
    action_name = "verify_external_accept" if decision == "accept" else "verify_external_reject"
    result_request_status = locked.request.status
    result_instance_status = locked.instance.status
    result_step_status = locked.step.status
    result_current_step_id = locked.instance.current_step_id
    result_current_step_no = locked.instance.current_step_no
    if decision == "reject":
        result_step_status = "awaiting_external_evidence"
    elif registration.external_action == "return":
        return_facts = validate_return_reapproval_instructions(
            returned_step_inputs=inputs,
            target_step_max_quantities=_return_target_max_quantities(db, locked),
            instructions=return_instructions,
        )
        opened_step = _append_return_target_step(db, locked=locked, now=now)
        result_step_status = "returned"
        result_current_step_id = opened_step.id
        result_current_step_no = opened_step.step_no
    else:
        outcomes = tuple(
            ApprovalLineOutcome(
                request_line_id=row.request_line_id,
                input_qty=row.input_qty,
                approved_qty=row.approved_qty,
                rejected_qty=row.rejected_qty,
                reason=row.reason,
            )
            for row in registration_lines
        )
        validate_line_approval_decisions(
            inputs,
            tuple(
                ApprovalLineDecision(
                    request_line_id=row.request_line_id,
                    approved_qty=row.approved_qty,
                    reason=row.reason,
                )
                for row in registration_lines
            ),
        )
        terminal_action, terminal_step_status = _terminal_outcome(outcomes)
        if terminal_action != registration.external_action:
            _fail(
                "material_request_external_registration_fact_invalid",
                "precondition_failed",
                "外部审批登记动作与逐行数量事实不一致",
            )
        # Official third-stage decisions are inserted while the step is still
        # the exact current step.  This includes a complete set of zero-approved
        # rows for an external whole-request rejection.
        _persist_step_decisions(
            db,
            locked=locked,
            actor=actor_context,
            outcomes=outcomes,
            decision_source="external_registration",
            external_registration_id=registration.id,
            now=now,
        )
        decision_manifest = _decision_manifest(
            locked=locked,
            actor=actor_context,
            outcomes=outcomes,
            terminal_action=terminal_action,
            external_registration_id=registration.id,
            occurred_at=now,
        )
        result_step_status = terminal_step_status
        result_current_step_id = None
        result_current_step_no = None
        if terminal_action == "reject":
            result_request_status = "rejected"
            result_instance_status = "rejected"
        else:
            chain = _causal_step_outcome_chain(
                db, locked, final_outcomes=outcomes
            )
            requested = {row.id: row.requested_qty for row in locked.lines}
            result_request_status = final_request_approval_status_from_chain(
                requested_quantities=requested,
                step_outcome_chain=chain,
            )
            result_instance_status = "completed"

    result = ExternalApprovalVerificationResult(
        request_id=locked.request.id,
        request_no=locked.request.request_no,
        request_status=result_request_status,
        request_version=locked.request.version,
        revision_id=locked.revision.id,
        revision_no=locked.revision.revision_no,
        instance_id=locked.instance.id,
        instance_status=result_instance_status,
        instance_version=locked.instance.version,
        step_id=locked.step.id,
        step_attempt_no=locked.step.attempt_no,
        step_status=result_step_status,
        step_version=locked.step.version,
        registration_id=registration.id,
        registration_status=registration.status,
        verification_decision=decision,
        current_step_id=result_current_step_id,
        current_step_no=result_current_step_no,
        opened_step_id=opened_step.id if opened_step is not None else None,
        opened_step_attempt_no=opened_step.attempt_no if opened_step is not None else None,
        state_axes=dict(_request_axes(locked.request)),
    )
    command = _command_fact(
        operation="verify_external",
        locked=locked,
        actor=actor_context,
        target_version=locked.request.version,
        key_hash=key_hash,
        request_reference=command_reference,
        request_hash=payload_hash,
        result_document=_external_verification_result_document(result),
        request_document={
            "registration_id": str(registration.id),
            "verification_decision": decision,
            "registration_manifest_sha256": registration.decision_manifest_sha256,
            "sensitive_fields": "excluded",
        },
        occurred_at=now,
    )
    db.add(command)
    db.flush()
    action = ApprovalAction(
        id=uuid.uuid4(),
        instance_id=locked.instance.id,
        step_id=locked.step.id,
        command_id=command.id,
        action=action_name,
        actor_user_id=actor_context.principal.user_id,
        actor_person_id=actor_context.principal.person_id,
        actor_role_assignment_id=actor_context.grant.assignment_id,
        authorization_version=actor_context.principal.authorization_version,
        source_mode="external_registration",
        comment=checked_comment,
        occurred_at=now,
        created_at=now,
    )
    db.add(action)
    db.flush()
    if decision == "reject":
        locked.step.status = "awaiting_external_evidence"
        db.flush()
    elif registration.external_action == "return":
        assert opened_step is not None
        _persist_return_facts(
            db,
            locked=locked,
            action=action,
            actor=actor_context,
            target=opened_step,
            facts=return_facts,
            now=now,
        )
        db.flush()
        locked.step.status = "returned"
        locked.step.decided_at = now
        locked.step.decision_manifest_sha256 = None
        db.flush()
        _activate_return_target(locked=locked, target=opened_step, now=now)
        db.flush()
    else:
        assert terminal_action is not None
        assert terminal_step_status is not None
        assert decision_manifest is not None
        if terminal_action == "reject":
            _finish_request_rejected(locked, now=now)
        else:
            _finish_final_approval(db, locked=locked, outcomes=outcomes, now=now)
        locked.step.status = terminal_step_status
        locked.step.decision_manifest_sha256 = decision_manifest
        locked.step.decided_at = now
        db.flush()
    db.add(
        _state_event(
            aggregate_type="approval_step",
            aggregate_id=locked.step.id,
            from_status="evidence_pending_verification",
            to_status=locked.step.status,
            reason=(
                "external_approval_evidence_rejected"
                if decision == "reject"
                else "external_approval_evidence_accepted"
            ),
            actor_user_id=actor_context.principal.user_id,
            key_hash=key_hash,
            suffix=f"external-verification-{decision}",
            occurred_at=now,
            metadata={
                "request_id": str(locked.request.id),
                "revision_id": str(locked.revision.id),
                "instance_id": str(locked.instance.id),
                "step_id": str(locked.step.id),
                "step_attempt_no": locked.step.attempt_no,
                "registration_id": str(registration.id),
            },
        )
    )
    if locked.request.status in {"approved", "partially_approved", "rejected"}:
        db.add(
            _state_event(
                aggregate_type=MATERIAL_REQUEST_AGGREGATE,
                aggregate_id=locked.request.id,
                from_status="approval_in_progress",
                to_status=locked.request.status,
                reason="external_approval_completed",
                actor_user_id=actor_context.principal.user_id,
                key_hash=key_hash,
                suffix=f"request-{locked.request.status}",
                occurred_at=now,
                metadata={
                    "request_id": str(locked.request.id),
                    "revision_id": str(locked.revision.id),
                    "instance_id": str(locked.instance.id),
                    "step_id": str(locked.step.id),
                    "registration_id": str(registration.id),
                },
            )
        )
    db.flush()
    append_audit_event(
        db,
        stream_key=MATERIAL_REQUEST_AUDIT_STREAM,
        actor_user_id=actor_context.principal.user_id,
        action=f"material_request.external_evidence.verify_{decision}",
        aggregate_type=MATERIAL_REQUEST_AGGREGATE,
        aggregate_id=str(locked.request.id),
        before_jsonb=before,
        after_jsonb={
            **_safe_approval_snapshot(locked, inputs),
            "registration_id": str(registration.id),
            "registration_status": registration.status,
            "external_action": registration.external_action,
            "verification_decision": decision,
            "opened_step_id": str(opened_step.id) if opened_step is not None else None,
            "sensitive_fields": "excluded",
        },
        request_id=trace_id,
        occurred_at=now,
    )
    assert_approval_did_not_advance_fulfillment_axes(before_axes, _request_axes(locked.request))
    db.flush()
    return result


def _complete_internal_step(
    db: Session,
    *,
    locked: _LockedApproval,
    actor: _ActorContext,
    outcomes: tuple[ApprovalLineOutcome, ...],
    comment: str,
    key_hash: str,
    payload_hash: str,
    request_reference: str,
    trace_request_id: str,
    before: dict[str, Any],
    now: datetime,
) -> ApprovalCommandResult:
    terminal_action, step_status = _terminal_outcome(outcomes)
    _enforce_reopened_review_limits(db, locked.step, outcomes)
    _persist_step_decisions(
        db,
        locked=locked,
        actor=actor,
        outcomes=outcomes,
        decision_source="internal",
        external_registration_id=None,
        now=now,
    )
    opened_step: ApprovalStep | None = None
    if terminal_action != "reject":
        # A future step is first materialised as pending.  Keeping it outside
        # the current-status set is essential: both PostgreSQL and SQLite
        # enforce exactly one current step per instance.
        opened_step = _prepare_forward_step(db, locked=locked, now=now)

    locked.request.version += 1
    locked.request.updated_at = now
    locked.step.version += 1
    locked.step.updated_at = now
    locked.instance.version += 1
    locked.instance.updated_at = now
    decision_manifest = _decision_manifest(
        locked=locked,
        actor=actor,
        outcomes=outcomes,
        terminal_action=terminal_action,
        external_registration_id=None,
        occurred_at=now,
    )
    result_request_status = "rejected" if terminal_action == "reject" else locked.request.status
    result_instance_status = "rejected" if terminal_action == "reject" else locked.instance.status
    result = ApprovalCommandResult(
        request_id=locked.request.id,
        request_no=locked.request.request_no,
        request_status=result_request_status,
        request_version=locked.request.version,
        revision_id=locked.revision.id,
        revision_no=locked.revision.revision_no,
        instance_id=locked.instance.id,
        instance_status=result_instance_status,
        instance_version=locked.instance.version,
        decided_step_id=locked.step.id,
        decided_step_no=locked.step.step_no,
        decided_step_attempt_no=locked.step.attempt_no,
        decided_step_status=step_status,
        decided_step_version=locked.step.version,
        current_step_id=opened_step.id if opened_step is not None else None,
        current_step_no=opened_step.step_no if opened_step is not None else None,
        opened_step_id=opened_step.id if opened_step is not None else None,
        opened_step_attempt_no=opened_step.attempt_no if opened_step is not None else None,
        state_axes=dict(_request_axes(locked.request)),
    )
    command = _command_fact(
        operation="region_decide" if locked.step.step_no == 1 else "headquarters_decide",
        locked=locked,
        actor=actor,
        target_version=locked.request.version,
        key_hash=key_hash,
        request_reference=request_reference,
        request_hash=payload_hash,
        result_document=_approval_result_document(result),
        request_document={
            "step_id": str(locked.step.id),
            "step_no": locked.step.step_no,
            "step_attempt_no": locked.step.attempt_no,
            "action": terminal_action,
            "decision_manifest_sha256": decision_manifest,
            "sensitive_fields": "excluded",
        },
        occurred_at=now,
    )
    db.add(command)
    db.flush()
    db.add(
        ApprovalAction(
            id=uuid.uuid4(),
            instance_id=locked.instance.id,
            step_id=locked.step.id,
            command_id=command.id,
            action=terminal_action,
            actor_user_id=actor.principal.user_id,
            actor_person_id=actor.principal.person_id,
            actor_role_assignment_id=actor.grant.assignment_id,
            authorization_version=actor.principal.authorization_version,
            source_mode="internal",
            comment=comment,
            occurred_at=now,
            created_at=now,
        )
    )
    db.flush()
    # Terminal-step guards require both immutable decisions and the action
    # before the mutable projection becomes terminal.  The old current step
    # must also leave the current set before a successor may enter it.
    locked.step.status = step_status
    locked.step.decision_manifest_sha256 = decision_manifest
    locked.step.decided_at = now
    if terminal_action == "reject":
        _finish_request_rejected(locked, now=now)
    db.flush()
    if opened_step is not None:
        _activate_forward_step(locked=locked, target=opened_step, now=now)
        db.flush()
    db.add(
        _state_event(
            aggregate_type="approval_step",
            aggregate_id=locked.step.id,
            from_status="open",
            to_status=locked.step.status,
            reason=f"internal_approval_{terminal_action}",
            actor_user_id=actor.principal.user_id,
            key_hash=key_hash,
            suffix=f"step-{locked.step.id}-{locked.step.status}",
            occurred_at=now,
            metadata={
                "request_id": str(locked.request.id),
                "revision_id": str(locked.revision.id),
                "instance_id": str(locked.instance.id),
                "step_id": str(locked.step.id),
                "step_attempt_no": locked.step.attempt_no,
                "opened_step_id": str(opened_step.id) if opened_step else None,
            },
        )
    )
    if terminal_action == "reject":
        db.add(
            _state_event(
                aggregate_type=MATERIAL_REQUEST_AGGREGATE,
                aggregate_id=locked.request.id,
                from_status="approval_in_progress",
                to_status="rejected",
                reason="approval_rejected",
                actor_user_id=actor.principal.user_id,
                key_hash=key_hash,
                suffix="request-rejected",
                occurred_at=now,
                metadata={
                    "request_id": str(locked.request.id),
                    "revision_id": str(locked.revision.id),
                    "instance_id": str(locked.instance.id),
                    "step_id": str(locked.step.id),
                },
            )
        )
    db.flush()
    append_audit_event(
        db,
        stream_key=MATERIAL_REQUEST_AUDIT_STREAM,
        actor_user_id=actor.principal.user_id,
        action=f"material_request.approval.{terminal_action}",
        aggregate_type=MATERIAL_REQUEST_AGGREGATE,
        aggregate_id=str(locked.request.id),
        before_jsonb=before,
        after_jsonb={
            **_safe_approval_snapshot(locked, _current_or_decided_inputs(outcomes)),
            "decision_manifest_sha256": decision_manifest,
            "opened_step_id": str(opened_step.id) if opened_step else None,
        },
        request_id=trace_request_id,
        occurred_at=now,
    )
    return result


def _return_internal_step(
    db: Session,
    *,
    locked: _LockedApproval,
    actor: _ActorContext,
    instructions: tuple[ApprovalReturnInstruction, ...],
    comment: str,
    key_hash: str,
    payload_hash: str,
    request_reference: str,
    trace_request_id: str,
    before: dict[str, Any],
    now: datetime,
) -> ApprovalCommandResult:
    inputs = _current_step_inputs(db, locked)
    facts = validate_return_reapproval_instructions(
        returned_step_inputs=inputs,
        target_step_max_quantities=_return_target_max_quantities(db, locked),
        instructions=instructions,
    )
    target = (
        None
        if locked.step.step_no == 1
        else _append_return_target_step(db, locked=locked, now=now)
    )
    locked.request.version += 1
    locked.request.updated_at = now
    locked.step.version += 1
    locked.step.updated_at = now
    locked.instance.version += 1
    locked.instance.updated_at = now
    result_request_status = "returned" if target is None else locked.request.status
    result_instance_status = "returned" if target is None else locked.instance.status
    result = ApprovalCommandResult(
        request_id=locked.request.id,
        request_no=locked.request.request_no,
        request_status=result_request_status,
        request_version=locked.request.version,
        revision_id=locked.revision.id,
        revision_no=locked.revision.revision_no,
        instance_id=locked.instance.id,
        instance_status=result_instance_status,
        instance_version=locked.instance.version,
        decided_step_id=locked.step.id,
        decided_step_no=locked.step.step_no,
        decided_step_attempt_no=locked.step.attempt_no,
        decided_step_status="returned",
        decided_step_version=locked.step.version,
        current_step_id=target.id if target else None,
        current_step_no=target.step_no if target else None,
        opened_step_id=target.id if target else None,
        opened_step_attempt_no=target.attempt_no if target else None,
        state_axes=dict(_request_axes(locked.request)),
    )
    command = _command_fact(
        operation="region_decide" if locked.step.step_no == 1 else "headquarters_decide",
        locked=locked,
        actor=actor,
        target_version=locked.request.version,
        key_hash=key_hash,
        request_reference=request_reference,
        request_hash=payload_hash,
        result_document=_approval_result_document(result),
        request_document={
            "step_id": str(locked.step.id),
            "step_no": locked.step.step_no,
            "step_attempt_no": locked.step.attempt_no,
            "action": "return",
            "return_lines": _return_instruction_documents(instructions),
            "sensitive_fields": "excluded",
        },
        occurred_at=now,
    )
    db.add(command)
    db.flush()
    action = ApprovalAction(
        id=uuid.uuid4(),
        instance_id=locked.instance.id,
        step_id=locked.step.id,
        command_id=command.id,
        action="return",
        actor_user_id=actor.principal.user_id,
        actor_person_id=actor.principal.person_id,
        actor_role_assignment_id=actor.grant.assignment_id,
        authorization_version=actor.principal.authorization_version,
        source_mode="internal",
        comment=comment,
        occurred_at=now,
        created_at=now,
    )
    db.add(action)
    db.flush()
    _persist_return_facts(
        db,
        locked=locked,
        action=action,
        actor=actor,
        target=target,
        facts=facts,
        now=now,
    )
    db.flush()
    # Close the source step only after its immutable action and complete set of
    # line-level return facts exist.  The successor is activated in a separate
    # flush so the one-current-step constraint can never observe two rows.
    locked.step.status = "returned"
    locked.step.decided_at = now
    locked.step.decision_manifest_sha256 = None
    if target is not None:
        db.flush()
        _activate_return_target(locked=locked, target=target, now=now)
    else:
        require_request_status_transition(locked.request.status, "returned")
        locked.request.status = "returned"
        locked.request.decided_at = None
        locked.instance.status = "returned"
        locked.instance.current_step_id = None
        locked.instance.current_step_no = None
        locked.instance.completed_at = now
    db.add(
        _state_event(
            aggregate_type="approval_step",
            aggregate_id=locked.step.id,
            from_status="open",
            to_status="returned",
            reason="approval_returned_for_rework",
            actor_user_id=actor.principal.user_id,
            key_hash=key_hash,
            suffix=f"step-{locked.step.id}-returned",
            occurred_at=now,
            metadata={
                "request_id": str(locked.request.id),
                "revision_id": str(locked.revision.id),
                "instance_id": str(locked.instance.id),
                "step_id": str(locked.step.id),
                "step_attempt_no": locked.step.attempt_no,
                "target_step_id": str(target.id) if target else None,
            },
        )
    )
    if target is None:
        db.add(
            _state_event(
                aggregate_type=MATERIAL_REQUEST_AGGREGATE,
                aggregate_id=locked.request.id,
                from_status="approval_in_progress",
                to_status="returned",
                reason="regional_approval_returned_to_requester",
                actor_user_id=actor.principal.user_id,
                key_hash=key_hash,
                suffix="request-returned",
                occurred_at=now,
                metadata={
                    "request_id": str(locked.request.id),
                    "revision_id": str(locked.revision.id),
                    "instance_id": str(locked.instance.id),
                    "step_id": str(locked.step.id),
                },
            )
        )
    db.flush()
    append_audit_event(
        db,
        stream_key=MATERIAL_REQUEST_AUDIT_STREAM,
        actor_user_id=actor.principal.user_id,
        action="material_request.approval.return",
        aggregate_type=MATERIAL_REQUEST_AGGREGATE,
        aggregate_id=str(locked.request.id),
        before_jsonb=before,
        after_jsonb={
            **_safe_approval_snapshot(locked, inputs),
            "target_kind": "approval_step" if target else "requester_revision",
            "target_step_id": str(target.id) if target else None,
            "return_line_count": len(facts),
        },
        request_id=trace_request_id,
        occurred_at=now,
    )
    return result


def _persist_step_decisions(
    db: Session,
    *,
    locked: _LockedApproval,
    actor: _ActorContext,
    outcomes: Sequence[ApprovalLineOutcome],
    decision_source: str,
    external_registration_id: uuid.UUID | None,
    now: datetime,
) -> None:
    db.add_all(
        ApprovalStepLineDecision(
            id=uuid.uuid4(),
            step_id=locked.step.id,
            request_line_id=outcome.request_line_id,
            input_qty=outcome.input_qty,
            approved_qty=outcome.approved_qty,
            rejected_qty=outcome.rejected_qty,
            reason=outcome.reason,
            decision_source=decision_source,
            external_registration_id=external_registration_id,
            decided_by_user_id=actor.principal.user_id,
            decided_by_person_id=actor.principal.person_id,
            decided_role_assignment_id=actor.grant.assignment_id,
            authorization_version=actor.principal.authorization_version,
            decided_at=now,
            created_at=now,
        )
        for outcome in outcomes
    )
    db.flush()


def _persist_return_facts(
    db: Session,
    *,
    locked: _LockedApproval,
    action: ApprovalAction,
    actor: _ActorContext,
    target: ApprovalStep | None,
    facts: Sequence[Any],
    now: datetime,
) -> None:
    db.add_all(
        ApprovalReturnLineFactRow(
            id=uuid.uuid4(),
            return_action_id=action.id,
            instance_id=locked.instance.id,
            returned_from_step_id=locked.step.id,
            target_kind="approval_step" if target is not None else "requester_revision",
            target_step_id=target.id if target is not None else None,
            request_id=locked.request.id,
            request_revision_id=locked.revision.id,
            request_line_id=fact.request_line_id,
            returned_step_input_qty=fact.returned_step_input_qty,
            target_step_max_qty=fact.target_step_max_qty,
            required_review_qty=fact.required_review_qty,
            reason=fact.reason,
            actor_user_id=actor.principal.user_id,
            actor_person_id=actor.principal.person_id,
            actor_role_assignment_id=actor.grant.assignment_id,
            authorization_version=actor.principal.authorization_version,
            occurred_at=now,
            created_at=now,
        )
        for fact in facts
    )


def _append_return_target_step(
    db: Session, *, locked: _LockedApproval, now: datetime
) -> ApprovalStep:
    target_no = locked.step.step_no - 1
    if target_no not in {1, 2}:
        _fail("material_request_return_target_invalid", "precondition_failed", "退回目标审批级别无效")
    source_target = db.scalar(
        select(ApprovalStep)
        .where(ApprovalStep.id == locked.step.predecessor_step_id)
        .execution_options(populate_existing=True)
    )
    if source_target is None or source_target.step_no != target_no:
        _fail("material_request_return_predecessor_invalid", "precondition_failed", "退回步骤缺少精确前级事实")
    attempts = _lock_step_attempts(db, locked.instance.id, target_no)
    if not attempts or attempts[-1].id != source_target.id:
        _fail("material_request_return_attempt_out_of_order", "conflict", "前级审批尝试不是当前连续版本")
    _require_step_candidate_set_current(
        db, source_target, request=locked.request, now=now
    )
    target = ApprovalStep(
        id=uuid.uuid4(),
        instance_id=locked.instance.id,
        step_no=target_no,
        attempt_no=source_target.attempt_no + 1,
        predecessor_step_id=source_target.predecessor_step_id,
        supersedes_step_id=source_target.id,
        reopened_from_step_id=locked.step.id,
        source_mode=source_target.source_mode,
        status="pending",
        assignee_user_id=source_target.assignee_user_id,
        assignee_snapshot_jsonb=dict(source_target.assignee_snapshot_jsonb),
        decision_manifest_sha256=None,
        opened_at=None,
        decided_at=None,
        version=0,
        created_at=now,
        updated_at=now,
    )
    db.add(target)
    db.flush()
    _copy_step_candidates(db, source=source_target, target=target, now=now)
    db.flush()
    return target


def _activate_return_target(
    *, locked: _LockedApproval, target: ApprovalStep, now: datetime
) -> None:
    target.status = "open"
    target.opened_at = now
    target.version += 1
    target.updated_at = now
    locked.instance.status = "active"
    locked.instance.current_step_no = target.step_no
    locked.instance.current_step_id = target.id
    locked.instance.completed_at = None


def _prepare_forward_step(
    db: Session, *, locked: _LockedApproval, now: datetime
) -> ApprovalStep:
    next_no = locked.step.step_no + 1
    if next_no not in {2, 3}:
        _fail("material_request_next_step_invalid", "precondition_failed", "审批链缺少后续步骤")
    attempts = _lock_step_attempts(db, locked.instance.id, next_no)
    if not attempts:
        _fail("material_request_next_step_missing", "precondition_failed", "审批链缺少冻结的后续步骤")
    latest = attempts[-1]
    if latest.status == "pending" and latest.predecessor_step_id == locked.step.id:
        _require_step_candidate_set_current(
            db, latest, request=locked.request, now=now
        )
        target = latest
    else:
        if latest.status not in {"pending", "returned"}:
            _fail("material_request_next_step_attempt_invalid", "conflict", "后续审批尝试状态无法连续恢复")
        _require_step_candidate_set_current(
            db, latest, request=locked.request, now=now
        )
        if latest.status == "pending":
            latest.status = "superseded"
            latest.updated_at = now
        target = ApprovalStep(
            id=uuid.uuid4(),
            instance_id=locked.instance.id,
            step_no=next_no,
            attempt_no=latest.attempt_no + 1,
            predecessor_step_id=locked.step.id,
            supersedes_step_id=latest.id,
            reopened_from_step_id=None,
            source_mode=latest.source_mode,
            status="pending",
            assignee_user_id=latest.assignee_user_id,
            assignee_snapshot_jsonb=dict(latest.assignee_snapshot_jsonb),
            decision_manifest_sha256=None,
            opened_at=None,
            decided_at=None,
            version=0,
            created_at=now,
            updated_at=now,
        )
        db.add(target)
        db.flush()
        _copy_step_candidates(db, source=latest, target=target, now=now)
        db.flush()
    return target


def _activate_forward_step(
    *, locked: _LockedApproval, target: ApprovalStep, now: datetime
) -> None:
    target.status = (
        "open" if target.source_mode == "internal" else "awaiting_external_evidence"
    )
    target.opened_at = now
    target.version += 1
    target.updated_at = now
    locked.instance.current_step_no = target.step_no
    locked.instance.current_step_id = target.id


def _finish_request_rejected(locked: _LockedApproval, *, now: datetime) -> None:
    require_request_status_transition(locked.request.status, "rejected")
    locked.request.status = "rejected"
    locked.request.decided_at = now
    locked.instance.status = "rejected"
    locked.instance.current_step_no = None
    locked.instance.current_step_id = None
    locked.instance.completed_at = now
    for line in locked.lines:
        line.final_approved_qty = Decimal("0.000")
        line.status = "rejected"
        line.version += 1
        line.updated_at = now


def _finish_final_approval(
    db: Session,
    *,
    locked: _LockedApproval,
    outcomes: Sequence[ApprovalLineOutcome],
    now: datetime,
) -> None:
    chain = _causal_step_outcome_chain(db, locked, final_outcomes=outcomes)
    requested = {row.id: row.requested_qty for row in locked.lines}
    quantities = reconstruct_final_approval_quantities(
        requested_quantities=requested,
        step_outcome_chain=chain,
    )
    final_status = final_request_approval_status_from_chain(
        requested_quantities=requested,
        step_outcome_chain=chain,
    )
    require_request_status_transition(locked.request.status, final_status)
    by_id = {row.id: row for row in locked.lines}
    for quantity in quantities:
        line = by_id[quantity.request_line_id]
        line.final_approved_qty = quantity.approved_qty
        if quantity.approved_qty == Decimal("0.000"):
            line.status = "rejected"
        elif quantity.approved_qty == quantity.requested_qty:
            line.status = "approved"
        else:
            line.status = "partially_approved"
        line.version += 1
        line.updated_at = now
    locked.request.status = final_status
    locked.request.decided_at = now
    locked.instance.status = "rejected" if final_status == "rejected" else "completed"
    locked.instance.current_step_no = None
    locked.instance.current_step_id = None
    locked.instance.completed_at = now


def _causal_step_outcome_chain(
    db: Session,
    locked: _LockedApproval,
    *,
    final_outcomes: Sequence[ApprovalLineOutcome],
) -> tuple[tuple[ApprovalLineOutcome, ...], ...]:
    if locked.step.step_no != 3:
        _fail("material_request_final_step_required", "precondition_failed", "最终批准必须来自第三级审批")
    step2 = db.get(ApprovalStep, locked.step.predecessor_step_id)
    step1 = db.get(ApprovalStep, step2.predecessor_step_id) if step2 is not None else None
    if step1 is None or step2 is None or step1.step_no != 1 or step2.step_no != 2:
        _fail("material_request_approval_chain_invalid", "precondition_failed", "最终审批因果链不完整")
    return (
        _step_outcomes(db, step1),
        _step_outcomes(db, step2),
        tuple(final_outcomes),
    )


def _step_outcomes(db: Session, step: ApprovalStep) -> tuple[ApprovalLineOutcome, ...]:
    rows = tuple(
        db.scalars(
            select(ApprovalStepLineDecision)
            .where(ApprovalStepLineDecision.step_id == step.id)
            .order_by(ApprovalStepLineDecision.request_line_id)
        ).all()
    )
    return tuple(
        ApprovalLineOutcome(
            request_line_id=row.request_line_id,
            input_qty=row.input_qty,
            approved_qty=row.approved_qty,
            rejected_qty=row.rejected_qty,
            reason=row.reason,
        )
        for row in rows
    )


def _terminal_outcome(outcomes: Sequence[ApprovalLineOutcome]) -> tuple[str, str]:
    approved = sum((row.approved_qty for row in outcomes), Decimal("0.000"))
    rejected = sum((row.rejected_qty for row in outcomes), Decimal("0.000"))
    if approved == 0:
        return "reject", "rejected"
    if rejected == 0:
        return "approve", "approved"
    return "partial_approve", "partially_approved"


def _current_step_inputs(db: Session, locked: _LockedApproval) -> tuple[ApprovalLineInput, ...]:
    if locked.step.step_no == 1:
        return tuple(
            ApprovalLineInput(request_line_id=row.id, input_qty=row.requested_qty)
            for row in locked.lines
        )
    predecessor = db.scalar(
        select(ApprovalStep)
        .where(ApprovalStep.id == locked.step.predecessor_step_id)
        .execution_options(populate_existing=True)
    )
    if predecessor is None or predecessor.status not in {"approved", "partially_approved"}:
        _fail("material_request_approval_predecessor_invalid", "precondition_failed", "当前审批步骤缺少有效前级决定")
    rows = tuple(
        db.scalars(
            select(ApprovalStepLineDecision)
            .where(
                ApprovalStepLineDecision.step_id == predecessor.id,
                ApprovalStepLineDecision.approved_qty > 0,
            )
            .order_by(ApprovalStepLineDecision.request_line_id)
        ).all()
    )
    if not rows:
        _fail("material_request_approval_inputs_required", "precondition_failed", "当前审批步骤没有正数输入")
    return tuple(
        ApprovalLineInput(request_line_id=row.request_line_id, input_qty=row.approved_qty)
        for row in rows
    )


def _return_target_max_quantities(
    db: Session, locked: _LockedApproval
) -> dict[uuid.UUID, Decimal]:
    inputs = _current_step_inputs(db, locked)
    if locked.step.step_no in {1, 2}:
        requested = {row.id: row.requested_qty for row in locked.lines}
        return {row.request_line_id: requested[row.request_line_id] for row in inputs}
    step2 = db.get(ApprovalStep, locked.step.predecessor_step_id)
    step1 = db.get(ApprovalStep, step2.predecessor_step_id) if step2 is not None else None
    if step1 is None:
        _fail("material_request_return_target_invalid", "precondition_failed", "三级退回缺少区域审批上限")
    approved = {
        row.request_line_id: row.approved_qty
        for row in db.scalars(
            select(ApprovalStepLineDecision).where(
                ApprovalStepLineDecision.step_id == step1.id,
                ApprovalStepLineDecision.approved_qty > 0,
            )
        ).all()
    }
    if set(approved) != {row.request_line_id for row in inputs}:
        _fail("material_request_return_target_invalid", "precondition_failed", "三级退回上限与当前明细不一致")
    return approved


def _enforce_reopened_review_limits(
    db: Session,
    step: ApprovalStep,
    outcomes: Sequence[ApprovalLineOutcome],
) -> None:
    if step.reopened_from_step_id is None:
        return
    facts = tuple(
        db.scalars(
            select(ApprovalReturnLineFactRow).where(
                ApprovalReturnLineFactRow.target_step_id == step.id
            )
        ).all()
    )
    limits = {row.request_line_id: row.required_review_qty for row in facts}
    if len(limits) != len(facts) or set(limits) != {row.request_line_id for row in outcomes}:
        _fail("material_request_return_review_fact_invalid", "precondition_failed", "重审步骤缺少完整退回数量事实")
    if any(row.approved_qty > limits[row.request_line_id] for row in outcomes):
        _fail("material_request_reapproval_quantity_exceeds_instruction", "precondition_failed", "重审批准数量不得超过退回指令数量")


def _lock_request(db: Session, request_id: uuid.UUID) -> MaterialRequest:
    request = db.scalar(
        select(MaterialRequest)
        .where(MaterialRequest.id == request_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if request is None:
        _fail("material_request_not_found", "not_found", "需求单不存在")
    return request


def _lock_current_approval(
    db: Session,
    *,
    request_id: uuid.UUID,
    step_id: uuid.UUID,
    request: MaterialRequest | None = None,
) -> _LockedApproval:
    if request is None:
        request = _lock_request(db, request_id)
    elif request.id != request_id:
        _fail(
            "material_request_approval_lock_identity_invalid",
            "service_unavailable",
            "审批父级锁绑定无效",
        )
    revision_rows = tuple(
        db.scalars(
            select(MaterialRequestRevision)
            .where(
                MaterialRequestRevision.request_id == request.id,
                MaterialRequestRevision.revision_no == request.revision_no,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(revision_rows) != 1 or revision_rows[0].status != "sealed":
        _fail("material_request_current_revision_invalid", "precondition_failed", "需求单当前封存版本无效")
    revision = revision_rows[0]
    lines = tuple(
        db.scalars(
            select(MaterialRequestLine)
            .where(
                MaterialRequestLine.request_id == request.id,
                MaterialRequestLine.revision_id == revision.id,
                MaterialRequestLine.revision_no == revision.revision_no,
            )
            .order_by(MaterialRequestLine.line_no, MaterialRequestLine.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    instances = tuple(
        db.scalars(
            select(ApprovalInstance)
            .where(
                ApprovalInstance.request_id == request.id,
                ApprovalInstance.request_revision_id == revision.id,
                ApprovalInstance.status == "active",
            )
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(instances) != 1:
        _fail("material_request_active_approval_instance_invalid", "conflict", "需求单当前审批实例不唯一或不存在")
    instance = instances[0]
    step = db.scalar(
        select(ApprovalStep)
        .where(
            ApprovalStep.id == step_id,
            ApprovalStep.instance_id == instance.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        step is None
        or instance.current_step_id != step.id
        or instance.current_step_no != step.step_no
        or step.status not in _CURRENT_STEP_STATUSES
    ):
        _fail("material_request_approval_step_not_current", "conflict", "审批步骤不是当前精确步骤")
    if request.status != "approval_in_progress" or not lines:
        _fail("material_request_approval_state_invalid", "conflict", "需求单当前不处于审批中")
    if instance.revision_no != revision.revision_no:
        _fail("material_request_approval_revision_mismatch", "precondition_failed", "审批实例未绑定当前精确版本")
    return _LockedApproval(request, revision, lines, instance, step)


def _require_expected_versions(
    locked: _LockedApproval, request_version: int, step_version: int
) -> None:
    if locked.request.version != request_version:
        _fail("material_request_version_conflict", "conflict", "需求单版本已变化，请重新读取")
    if locked.step.version != step_version:
        _fail("material_request_approval_step_version_conflict", "conflict", "审批步骤版本已变化，请重新读取")


def _require_current_candidate(
    db: Session,
    *,
    supplied: FormalPrincipal,
    locked: _LockedApproval,
    candidate_kind: str,
    permission_action: str,
    permission_field: str,
    now: datetime,
) -> _ActorContext:
    current = load_formal_principal(db, supplied.user_id, now=now)
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
        or current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail("material_request_approval_actor_not_current", "forbidden", "正式审批权限上下文已失效，请重新读取后再操作")
    candidates = tuple(
        db.scalars(
            select(ApprovalStepCandidate)
            .where(
                ApprovalStepCandidate.step_id == locked.step.id,
                ApprovalStepCandidate.user_id == current.user_id,
                ApprovalStepCandidate.candidate_kind == candidate_kind,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(candidates) != 1:
        _fail("material_request_approval_candidate_forbidden", "forbidden", "当前账号不在冻结审批候选集合中")
    candidate = candidates[0]
    expected_role = _ROLE_BY_STEP[locked.step.step_no] if candidate_kind == "assignee" else "admin"
    grants = tuple(
        grant
        for grant in current.assignments
        if grant.assignment_id == candidate.role_assignment_id
        and grant.role_code == expected_role
        and grant.scope_type == candidate.snapshot_jsonb.get("scope_type")
        and grant.scope_id == candidate.snapshot_jsonb.get("scope_id")
    )
    if len(grants) != 1 or candidate.person_id != current.person_id:
        _fail("material_request_approval_candidate_stale", "forbidden", "冻结审批候选授权已失效")
    grant = grants[0]
    if (
        candidate.authorization_version != current.authorization_version
        or not _candidate_snapshot_matches(candidate, current, grant)
        or not current.allows(
            db,
            "material_request",
            permission_action,
            field_code=permission_field,
            target_scope_type="organization",
            target_scope_id=str(locked.request.requester_org_id),
        )
    ):
        _fail("material_request_approval_candidate_stale", "forbidden", "冻结审批候选授权已失效")
    _require_not_requester(locked.request, current)
    return _ActorContext(current, grant, candidate)


def _candidate_snapshot_matches(
    candidate: ApprovalStepCandidate,
    principal: FormalPrincipal,
    grant: ScopeGrant,
) -> bool:
    document = {
        "user_id": principal.user_id,
        "person_id": str(principal.person_id),
        "role_assignment_id": str(grant.assignment_id),
        "role_code": grant.role_code,
        "scope_type": grant.scope_type,
        "scope_id": grant.scope_id,
        "authorization_version": principal.authorization_version,
        "permission_keys": [list(key) for key in principal.permission_keys()],
    }
    expected = {key: value for key, value in document.items() if key != "permission_keys"}
    expected["authorization_sha256"] = _canonical_hash(document)
    return candidate.snapshot_jsonb == expected


def _require_step_candidate_set_current(
    db: Session,
    step: ApprovalStep,
    *,
    request: MaterialRequest,
    now: datetime,
) -> None:
    candidates = tuple(
        db.scalars(
            select(ApprovalStepCandidate)
            .where(ApprovalStepCandidate.step_id == step.id)
            .order_by(
                ApprovalStepCandidate.user_id,
                ApprovalStepCandidate.candidate_kind,
                ApprovalStepCandidate.id,
            )
        ).all()
    )
    expected_kinds = {"assignee"} if step.step_no in {1, 2} else {"registrar", "verifier"}
    if not candidates or {row.candidate_kind for row in candidates} != expected_kinds:
        _fail("material_request_approval_candidate_set_invalid", "precondition_failed", "冻结审批候选集合不完整")
    valid: list[ApprovalStepCandidate] = []
    for candidate in candidates:
        try:
            principal = load_formal_principal(db, candidate.user_id, now=now)
        except FormalAccessError:
            continue
        grants = tuple(
            grant for grant in principal.assignments if grant.assignment_id == candidate.role_assignment_id
        )
        if (
            len(grants) != 1
            or candidate.person_id != principal.person_id
            or candidate.authorization_version != principal.authorization_version
            or not _candidate_snapshot_matches(candidate, principal, grants[0])
        ):
            continue
        permission_action = (
            "approve_region"
            if step.step_no == 1
            else "approve_headquarters"
            if step.step_no == 2
            else "register_external"
            if candidate.candidate_kind == "registrar"
            else "verify_external"
        )
        permission_field = (
            "approval_decision" if step.step_no in {1, 2} else "approval_evidence"
        )
        if principal.allows(
            db,
            "material_request",
            permission_action,
            field_code=permission_field,
            target_scope_type="organization",
            target_scope_id=str(request.requester_org_id),
        ):
            valid.append(candidate)

    if step.step_no == 1:
        if len(candidates) != 1 or len(valid) != 1:
            _fail(
                "material_request_approval_candidate_stale",
                "precondition_failed",
                "冻结区域负责人授权已失效",
            )
        return
    if step.step_no == 2:
        if not valid:
            _fail(
                "material_request_approval_candidate_pool_insufficient",
                "precondition_failed",
                "冻结总部审批候选池已无有效人员",
            )
        return

    valid_kinds_by_identity: dict[tuple[str, uuid.UUID], set[str]] = {}
    for candidate in valid:
        valid_kinds_by_identity.setdefault(
            (candidate.user_id, candidate.person_id), set()
        ).add(candidate.candidate_kind)
    separated_candidates = tuple(
        identity
        for identity, kinds in valid_kinds_by_identity.items()
        if kinds == {"registrar", "verifier"}
    )
    if len({user_id for user_id, _ in separated_candidates}) < 2 or len(
        {person_id for _, person_id in separated_candidates}
    ) < 2:
        _fail(
            "material_request_external_candidate_pool_insufficient",
            "precondition_failed",
            "冻结外部审批登记复核候选池不足两名有效人员",
        )


def _copy_step_candidates(
    db: Session, *, source: ApprovalStep, target: ApprovalStep, now: datetime
) -> None:
    rows = tuple(
        db.scalars(
            select(ApprovalStepCandidate)
            .where(ApprovalStepCandidate.step_id == source.id)
            .order_by(ApprovalStepCandidate.id)
        ).all()
    )
    db.add_all(
        ApprovalStepCandidate(
            id=uuid.uuid4(),
            step_id=target.id,
            user_id=row.user_id,
            person_id=row.person_id,
            role_assignment_id=row.role_assignment_id,
            authorization_version=row.authorization_version,
            candidate_kind=row.candidate_kind,
            snapshot_jsonb=dict(row.snapshot_jsonb),
            created_at=now,
        )
        for row in rows
    )


def _lock_step_attempts(
    db: Session, instance_id: uuid.UUID, step_no: int
) -> tuple[ApprovalStep, ...]:
    rows = tuple(
        db.scalars(
            select(ApprovalStep)
            .where(
                ApprovalStep.instance_id == instance_id,
                ApprovalStep.step_no == step_no,
            )
            .order_by(ApprovalStep.attempt_no)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if tuple(row.attempt_no for row in rows) != tuple(range(1, len(rows) + 1)):
        _fail("material_request_approval_attempt_sequence_invalid", "service_unavailable", "审批步骤尝试序号不连续")
    return rows


def _require_external_evidence_file(
    db: Session, file_id: uuid.UUID, *, registrant_user_id: str
) -> FileObject:
    row = db.scalar(
        select(FileObject)
        .where(FileObject.id == file_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        row is None
        or not formal_file_service.is_available_formal_file_for_purpose(
            row,
            purpose="external_approval_evidence",
            uploader_user_id=registrant_user_id,
        )
        or row.size_bytes <= 0
        or _SHA256.fullmatch(row.sha256 or "") is None
        or not (
            row.mime_type == "application/pdf"
            or row.mime_type.startswith("image/")
            or row.mime_type.startswith("video/")
        )
    ):
        _fail("material_request_external_evidence_invalid", "precondition_failed", "外部审批证据文件无效或不属于登记人")
    if db.scalar(select(MaterialRequestFile.id).where(MaterialRequestFile.file_id == row.id).limit(1)) is not None:
        _fail("material_request_external_evidence_reused", "precondition_failed", "需求附件不能同时作为外部审批证据")
    other = db.scalar(
        select(ApprovalExternalRegistration.id)
        .where(ApprovalExternalRegistration.evidence_file_id == row.id)
        .limit(1)
    )
    if other is not None:
        _fail("material_request_external_evidence_reused", "conflict", "外部审批证据文件已被登记")
    return row


def _lock_registration_lines(
    db: Session, registration_id: uuid.UUID
) -> tuple[ApprovalExternalRegistrationLine, ...]:
    return tuple(
        db.scalars(
            select(ApprovalExternalRegistrationLine)
            .where(ApprovalExternalRegistrationLine.registration_id == registration_id)
            .order_by(ApprovalExternalRegistrationLine.request_line_id)
        ).all()
    )


def _require_registration_command(
    db: Session,
    locked: _LockedApproval,
    registration: ApprovalExternalRegistration,
) -> MaterialRequestCommand:
    row = db.scalar(
        select(MaterialRequestCommand)
        .join(ApprovalAction, ApprovalAction.command_id == MaterialRequestCommand.id)
        .where(
            ApprovalAction.instance_id == locked.instance.id,
            ApprovalAction.step_id == locked.step.id,
            ApprovalAction.action == "register_external_evidence",
            MaterialRequestCommand.operation == "register_external",
            MaterialRequestCommand.request_id == locked.request.id,
            MaterialRequestCommand.request_jsonb["registration_id"].as_string()
            == str(registration.id),
        )
        .execution_options(populate_existing=True)
    )
    if row is None or not hmac.compare_digest(row.result_hash, _canonical_hash(row.result_jsonb)):
        _fail("material_request_external_registration_fact_invalid", "service_unavailable", "外部审批登记命令事实无效")
    return row


def _return_instructions_from_registration_command(
    command: MaterialRequestCommand,
    registration: ApprovalExternalRegistration,
) -> tuple[ApprovalReturnInstruction, ...]:
    raw = command.request_jsonb.get("return_lines")
    if registration.external_action != "return":
        if raw not in ([], None):
            _fail("material_request_external_registration_fact_invalid", "service_unavailable", "外部审批登记命令事实无效")
        return ()
    try:
        rows = tuple(
            ApprovalReturnInstruction(
                request_line_id=uuid.UUID(str(item["request_line_id"])),
                required_review_qty=Decimal(str(item["required_review_qty"])),
                reason=str(item["reason"]),
            )
            for item in raw
        )
    except (KeyError, TypeError, ValueError, ArithmeticError):
        _fail("material_request_external_registration_fact_invalid", "service_unavailable", "外部审批登记命令事实无效")
    return rows


def _verify_external_registration_manifest(
    *,
    locked: _LockedApproval,
    registration: ApprovalExternalRegistration,
    registration_lines: Sequence[ApprovalExternalRegistrationLine],
    return_instructions: Sequence[ApprovalReturnInstruction],
    register_command: MaterialRequestCommand,
    evidence: FileObject,
) -> None:
    snapshot = registration.external_approver_snapshot_jsonb
    if not isinstance(snapshot, Mapping) or set(snapshot) != {
        "schema", "display_name", "external_reference_no", "evidence_sha256"
    }:
        _fail("material_request_external_registration_manifest_invalid", "precondition_failed", "外部审批登记证据清单无效")
    outcomes = tuple(
        ApprovalLineOutcome(
            request_line_id=row.request_line_id,
            input_qty=row.input_qty,
            approved_qty=row.approved_qty,
            rejected_qty=row.rejected_qty,
            reason=row.reason,
        )
        for row in registration_lines
    )
    comment = str(register_command.request_jsonb.get("registration_comment", ""))
    expected = _canonical_hash(
        _external_manifest_document(
            locked=locked,
            evidence=evidence,
            external_approver_name=str(snapshot["display_name"]),
            external_reference_no=str(snapshot["external_reference_no"]),
            external_decided_at=_as_utc(registration.external_decided_at),
            external_action=registration.external_action,
            outcomes=outcomes,
            return_lines=return_instructions,
            comment=comment,
        )
    )
    if (
        snapshot["schema"] != "rsc.external_approver_snapshot.v1"
        or snapshot["evidence_sha256"] != evidence.sha256
        or not hmac.compare_digest(registration.decision_manifest_sha256, expected)
        or register_command.request_jsonb.get("decision_manifest_sha256") != expected
    ):
        _fail("material_request_external_registration_manifest_invalid", "precondition_failed", "外部审批登记证据清单校验失败")


def _external_manifest_document(
    *,
    locked: _LockedApproval,
    evidence: FileObject,
    external_approver_name: str,
    external_reference_no: str,
    external_decided_at: datetime,
    external_action: str,
    outcomes: Sequence[ApprovalLineOutcome],
    return_lines: Sequence[ApprovalReturnInstruction],
    comment: str,
) -> dict[str, Any]:
    return {
        "schema": "rsc.external_approval_manifest.v1",
        "request_id": str(locked.request.id),
        "revision_id": str(locked.revision.id),
        "revision_no": locked.revision.revision_no,
        "instance_id": str(locked.instance.id),
        "step_id": str(locked.step.id),
        "step_attempt_no": locked.step.attempt_no,
        "evidence": {
            "file_id": str(evidence.id),
            "sha256": evidence.sha256,
            "size_bytes": evidence.size_bytes,
            "mime_type": evidence.mime_type,
        },
        "external_approver_name": external_approver_name,
        "external_reference_no": external_reference_no,
        "external_decided_at": _as_utc(external_decided_at).isoformat(),
        "external_action": external_action,
        "outcomes": _outcome_documents(outcomes),
        "return_lines": _return_instruction_documents(return_lines),
        "comment": comment,
    }


def _decision_manifest(
    *,
    locked: _LockedApproval,
    actor: _ActorContext,
    outcomes: Sequence[ApprovalLineOutcome],
    terminal_action: str,
    external_registration_id: uuid.UUID | None,
    occurred_at: datetime,
) -> str:
    return _canonical_hash(
        {
            "schema": "rsc.approval_step_decision_manifest.v1",
            "request_id": str(locked.request.id),
            "revision_id": str(locked.revision.id),
            "instance_id": str(locked.instance.id),
            "step_id": str(locked.step.id),
            "step_no": locked.step.step_no,
            "step_attempt_no": locked.step.attempt_no,
            "terminal_action": terminal_action,
            "outcomes": _outcome_documents(outcomes),
            "actor_user_id": actor.principal.user_id,
            "actor_person_id": str(actor.principal.person_id),
            "actor_role_assignment_id": str(actor.grant.assignment_id),
            "authorization_version": actor.principal.authorization_version,
            "external_registration_id": (
                str(external_registration_id) if external_registration_id else None
            ),
            "occurred_at": occurred_at.isoformat(),
        }
    )


def _command_fact(
    *,
    operation: str,
    locked: _LockedApproval,
    actor: _ActorContext,
    target_version: int,
    key_hash: str,
    request_reference: str,
    request_hash: str,
    result_document: dict[str, Any],
    request_document: dict[str, Any],
    occurred_at: datetime,
) -> MaterialRequestCommand:
    return MaterialRequestCommand(
        id=uuid.uuid4(),
        operation=operation,
        request_id=locked.request.id,
        target_version=target_version,
        idempotency_key_hash=key_hash,
        request_reference=request_reference,
        request_hash=request_hash,
        result_hash=_canonical_hash(result_document),
        request_jsonb={
            "schema": "rsc.material_request_approval_command.v1",
            "operation": operation,
            "request_id": str(locked.request.id),
            "revision_id": str(locked.revision.id),
            "revision_no": locked.revision.revision_no,
            "instance_id": str(locked.instance.id),
            "target_version": target_version,
            "payload_sha256": request_hash,
            **request_document,
        },
        result_jsonb=result_document,
        actor_user_id=actor.principal.user_id,
        actor_person_id=actor.principal.person_id,
        actor_role_assignment_id=actor.grant.assignment_id,
        authorization_version=actor.principal.authorization_version,
        occurred_at=occurred_at,
        created_at=occurred_at,
    )


def _load_result_replay(
    db: Session,
    *,
    key_hash: str,
    request_hash: str,
    operation: str,
    request_id: uuid.UUID,
    request_reference: str,
    actor: FormalPrincipal,
    kind: str,
) -> Mapping[str, Any] | None:
    row = db.scalar(
        select(MaterialRequestCommand)
        .where(MaterialRequestCommand.idempotency_key_hash == key_hash)
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    if (
        row.operation != operation
        or row.request_id != request_id
        or row.request_reference != request_reference
        or row.request_hash != request_hash
    ):
        _fail("material_request_approval_idempotency_conflict", "conflict", "幂等键已用于不同的审批命令")
    if row.actor_user_id != actor.user_id or row.actor_person_id != actor.person_id:
        _fail("material_request_approval_idempotency_actor_mismatch", "forbidden", "审批幂等记录不属于当前账号")
    if row.authorization_version != actor.authorization_version:
        _fail(
            "material_request_approval_idempotency_authorization_stale",
            "forbidden",
            "审批幂等记录的授权版本已失效",
        )
    if (
        not isinstance(row.result_jsonb, Mapping)
        or row.result_jsonb.get("kind") != kind
        or not hmac.compare_digest(row.result_hash or "", _canonical_hash(row.result_jsonb))
    ):
        _fail("material_request_approval_idempotency_record_invalid", "service_unavailable", "审批幂等结果无法安全重放")
    return row.result_jsonb


def _approval_result_document(result: ApprovalCommandResult) -> dict[str, Any]:
    return {
        "kind": "approval_decision",
        "request_id": str(result.request_id),
        "request_no": result.request_no,
        "request_status": result.request_status,
        "request_version": result.request_version,
        "revision_id": str(result.revision_id),
        "revision_no": result.revision_no,
        "instance_id": str(result.instance_id),
        "instance_status": result.instance_status,
        "instance_version": result.instance_version,
        "decided_step_id": str(result.decided_step_id),
        "decided_step_no": result.decided_step_no,
        "decided_step_attempt_no": result.decided_step_attempt_no,
        "decided_step_status": result.decided_step_status,
        "decided_step_version": result.decided_step_version,
        "current_step_id": str(result.current_step_id) if result.current_step_id else None,
        "current_step_no": result.current_step_no,
        "opened_step_id": str(result.opened_step_id) if result.opened_step_id else None,
        "opened_step_attempt_no": result.opened_step_attempt_no,
        "state_axes": dict(result.state_axes),
    }


def _external_registration_result_document(
    result: ExternalApprovalRegistrationResult,
) -> dict[str, Any]:
    return {
        "kind": "external_registration",
        "request_id": str(result.request_id),
        "request_no": result.request_no,
        "request_status": result.request_status,
        "request_version": result.request_version,
        "revision_id": str(result.revision_id),
        "revision_no": result.revision_no,
        "instance_id": str(result.instance_id),
        "instance_version": result.instance_version,
        "step_id": str(result.step_id),
        "step_attempt_no": result.step_attempt_no,
        "step_status": result.step_status,
        "step_version": result.step_version,
        "registration_id": str(result.registration_id),
        "registration_no": result.registration_no,
        "registration_status": result.registration_status,
        "external_action": result.external_action,
        "decision_manifest_sha256": result.decision_manifest_sha256,
        "state_axes": dict(result.state_axes),
    }


def _external_verification_result_document(
    result: ExternalApprovalVerificationResult,
) -> dict[str, Any]:
    return {
        "kind": "external_verification",
        "request_id": str(result.request_id),
        "request_no": result.request_no,
        "request_status": result.request_status,
        "request_version": result.request_version,
        "revision_id": str(result.revision_id),
        "revision_no": result.revision_no,
        "instance_id": str(result.instance_id),
        "instance_status": result.instance_status,
        "instance_version": result.instance_version,
        "step_id": str(result.step_id),
        "step_attempt_no": result.step_attempt_no,
        "step_status": result.step_status,
        "step_version": result.step_version,
        "registration_id": str(result.registration_id),
        "registration_status": result.registration_status,
        "verification_decision": result.verification_decision,
        "current_step_id": str(result.current_step_id) if result.current_step_id else None,
        "current_step_no": result.current_step_no,
        "opened_step_id": str(result.opened_step_id) if result.opened_step_id else None,
        "opened_step_attempt_no": result.opened_step_attempt_no,
        "state_axes": dict(result.state_axes),
    }


def _approval_result_from_json(value: Mapping[str, Any]) -> ApprovalCommandResult:
    try:
        return ApprovalCommandResult(
            request_id=uuid.UUID(str(value["request_id"])),
            request_no=str(value["request_no"]),
            request_status=str(value["request_status"]),
            request_version=int(value["request_version"]),
            revision_id=uuid.UUID(str(value["revision_id"])),
            revision_no=int(value["revision_no"]),
            instance_id=uuid.UUID(str(value["instance_id"])),
            instance_status=str(value["instance_status"]),
            instance_version=int(value["instance_version"]),
            decided_step_id=uuid.UUID(str(value["decided_step_id"])),
            decided_step_no=int(value["decided_step_no"]),
            decided_step_attempt_no=int(value["decided_step_attempt_no"]),
            decided_step_status=str(value["decided_step_status"]),
            decided_step_version=int(value["decided_step_version"]),
            current_step_id=_optional_uuid(value.get("current_step_id")),
            current_step_no=_optional_int(value.get("current_step_no")),
            opened_step_id=_optional_uuid(value.get("opened_step_id")),
            opened_step_attempt_no=_optional_int(value.get("opened_step_attempt_no")),
            state_axes=_exact_axes(value["state_axes"]),
        )
    except (KeyError, TypeError, ValueError):
        _fail("material_request_approval_idempotency_record_invalid", "service_unavailable", "审批幂等结果无法安全重放")


def _external_registration_result_from_json(
    value: Mapping[str, Any],
) -> ExternalApprovalRegistrationResult:
    try:
        return ExternalApprovalRegistrationResult(
            request_id=uuid.UUID(str(value["request_id"])),
            request_no=str(value["request_no"]),
            request_status=str(value["request_status"]),
            request_version=int(value["request_version"]),
            revision_id=uuid.UUID(str(value["revision_id"])),
            revision_no=int(value["revision_no"]),
            instance_id=uuid.UUID(str(value["instance_id"])),
            instance_version=int(value["instance_version"]),
            step_id=uuid.UUID(str(value["step_id"])),
            step_attempt_no=int(value["step_attempt_no"]),
            step_status=str(value["step_status"]),
            step_version=int(value["step_version"]),
            registration_id=uuid.UUID(str(value["registration_id"])),
            registration_no=str(value["registration_no"]),
            registration_status=str(value["registration_status"]),
            external_action=str(value["external_action"]),
            decision_manifest_sha256=str(value["decision_manifest_sha256"]),
            state_axes=_exact_axes(value["state_axes"]),
        )
    except (KeyError, TypeError, ValueError):
        _fail("material_request_approval_idempotency_record_invalid", "service_unavailable", "审批幂等结果无法安全重放")


def _external_verification_result_from_json(
    value: Mapping[str, Any],
) -> ExternalApprovalVerificationResult:
    try:
        return ExternalApprovalVerificationResult(
            request_id=uuid.UUID(str(value["request_id"])),
            request_no=str(value["request_no"]),
            request_status=str(value["request_status"]),
            request_version=int(value["request_version"]),
            revision_id=uuid.UUID(str(value["revision_id"])),
            revision_no=int(value["revision_no"]),
            instance_id=uuid.UUID(str(value["instance_id"])),
            instance_status=str(value["instance_status"]),
            instance_version=int(value["instance_version"]),
            step_id=uuid.UUID(str(value["step_id"])),
            step_attempt_no=int(value["step_attempt_no"]),
            step_status=str(value["step_status"]),
            step_version=int(value["step_version"]),
            registration_id=uuid.UUID(str(value["registration_id"])),
            registration_status=str(value["registration_status"]),
            verification_decision=str(value["verification_decision"]),
            current_step_id=_optional_uuid(value.get("current_step_id")),
            current_step_no=_optional_int(value.get("current_step_no")),
            opened_step_id=_optional_uuid(value.get("opened_step_id")),
            opened_step_attempt_no=_optional_int(value.get("opened_step_attempt_no")),
            state_axes=_exact_axes(value["state_axes"]),
        )
    except (KeyError, TypeError, ValueError):
        _fail("material_request_approval_idempotency_record_invalid", "service_unavailable", "审批幂等结果无法安全重放")


def _state_event(
    *,
    aggregate_type: str,
    aggregate_id: uuid.UUID,
    from_status: str,
    to_status: str,
    reason: str,
    actor_user_id: str,
    key_hash: str,
    suffix: str,
    occurred_at: datetime,
    metadata: Mapping[str, Any],
) -> StateTransitionEvent:
    return StateTransitionEvent(
        id=uuid.uuid4(),
        aggregate_type=aggregate_type,
        aggregate_id=str(aggregate_id),
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        actor_id=actor_user_id,
        idempotency_key=f"mr:{key_hash}:{suffix}",
        occurred_at=occurred_at,
        metadata_jsonb={**dict(metadata), "idempotency_key_hash": key_hash},
        created_at=occurred_at,
    )


def _safe_approval_snapshot(
    locked: _LockedApproval, inputs: Sequence[ApprovalLineInput]
) -> dict[str, Any]:
    return {
        "request_id": str(locked.request.id),
        "request_no": locked.request.request_no,
        "request_status": locked.request.status,
        "request_version": locked.request.version,
        "revision_id": str(locked.revision.id),
        "revision_no": locked.revision.revision_no,
        "instance_id": str(locked.instance.id),
        "instance_attempt_no": locked.instance.attempt_no,
        "instance_status": locked.instance.status,
        "instance_version": locked.instance.version,
        "current_step_id": str(locked.instance.current_step_id) if locked.instance.current_step_id else None,
        "current_step_no": locked.instance.current_step_no,
        "step_id": str(locked.step.id),
        "step_no": locked.step.step_no,
        "step_attempt_no": locked.step.attempt_no,
        "step_status": locked.step.status,
        "step_version": locked.step.version,
        "line_input_manifest_sha256": _canonical_hash(
            [
                {"request_line_id": str(row.request_line_id), "input_qty": format(row.input_qty, "f")}
                for row in inputs
            ]
        ),
        "state_axes": dict(_request_axes(locked.request)),
        "sensitive_fields": "excluded",
    }


def _validate_internal_decision(value: MaterialRequestApprovalInput) -> MaterialRequestApprovalInput:
    if not isinstance(value, MaterialRequestApprovalInput):
        _fail("material_request_approval_payload_invalid", "invalid_request", "审批请求格式无效")
    action = _require_choice("approval_action", value.action, {"approve", "return", "reject"})
    comment = _require_text("approval_comment", value.comment, 4000, required=action in {"return", "reject"})
    if action == "approve" and (not value.lines or value.return_lines):
        _fail("material_request_approval_payload_invalid", "invalid_request", "同意审批必须提交逐行数量且不能包含退回指令")
    if action == "return" and (value.lines or not value.return_lines):
        _fail("material_request_approval_payload_invalid", "invalid_request", "退回审批必须提交逐行重审指令且不能包含批准数量")
    if action == "reject" and (value.lines or value.return_lines):
        _fail("material_request_approval_payload_invalid", "invalid_request", "整单驳回不接受逐行数量")
    return MaterialRequestApprovalInput(action, tuple(value.lines), tuple(value.return_lines), comment)


def _validate_external_registration(
    value: ExternalApprovalRegistrationInput,
) -> ExternalApprovalRegistrationInput:
    if not isinstance(value, ExternalApprovalRegistrationInput):
        _fail("material_request_external_registration_payload_invalid", "invalid_request", "外部审批登记格式无效")
    action = _require_choice("external_action", value.action, {"approve", "return", "reject"})
    file_id = _require_uuid("evidence_file_id", value.evidence_file_id)
    name = _require_text("external_approver_name", value.external_approver_name, 160, required=True)
    reference = _require_text("external_reference_no", value.external_reference_no, 200, required=True)
    if _SAFE_REFERENCE.fullmatch(reference) is None:
        _fail("material_request_external_reference_invalid", "invalid_request", "外部审批参考号格式无效")
    decided_at = _require_aware_datetime(value.external_decided_at)
    comment = _require_text("external_comment", value.comment, 4000, required=action in {"return", "reject"})
    if action == "approve" and (not value.lines or value.return_lines):
        _fail("material_request_external_registration_payload_invalid", "invalid_request", "外部同意必须提交逐行数量")
    if action == "return" and (value.lines or not value.return_lines):
        _fail("material_request_external_registration_payload_invalid", "invalid_request", "外部退回必须提交逐行重审指令")
    if action == "reject" and (value.lines or value.return_lines):
        _fail("material_request_external_registration_payload_invalid", "invalid_request", "外部整单驳回不接受逐行数量")
    return ExternalApprovalRegistrationInput(
        file_id,
        name,
        reference,
        decided_at,
        action,
        tuple(value.lines),
        tuple(value.return_lines),
        comment,
    )


def _internal_operation_hint(db: Session, step_id: uuid.UUID) -> str:
    value = db.scalar(select(ApprovalStep.step_no).where(ApprovalStep.id == step_id))
    if value == 1:
        return "region_decide"
    if value == 2:
        return "headquarters_decide"
    # The hash still remains deterministic for a missing/wrong target; the
    # authoritative error is raised only after the exact locked load.
    return "internal_decide_invalid"


def _request_candidate_user_ids(
    db: Session, request_id: uuid.UUID
) -> tuple[str, ...]:
    """Discover the immutable candidate graph before child rows are locked.

    Every current and future frozen step candidate is included so nested
    forward/return validation never needs to acquire a new principal lock
    after the approval child graph has been locked.
    """

    return tuple(
        db.scalars(
            select(ApprovalStepCandidate.user_id)
            .join(
                ApprovalStep,
                ApprovalStep.id == ApprovalStepCandidate.step_id,
            )
            .join(
                ApprovalInstance,
                ApprovalInstance.id == ApprovalStep.instance_id,
            )
            .where(ApprovalInstance.request_id == request_id)
            .distinct()
            .order_by(ApprovalStepCandidate.user_id)
        ).all()
    )


def _current_or_decided_inputs(
    outcomes: Sequence[ApprovalLineOutcome],
) -> tuple[ApprovalLineInput, ...]:
    return tuple(
        ApprovalLineInput(request_line_id=row.request_line_id, input_qty=row.input_qty)
        for row in outcomes
    )


def _line_decision_documents(rows: Sequence[ApprovalLineDecision]) -> list[dict[str, str]]:
    return [
        {
            "request_line_id": str(row.request_line_id),
            "approved_qty": format(row.approved_qty, "f"),
            "reason": row.reason,
        }
        for row in rows
    ]


def _return_instruction_documents(
    rows: Sequence[ApprovalReturnInstruction],
) -> list[dict[str, str]]:
    return [
        {
            "request_line_id": str(row.request_line_id),
            "required_review_qty": format(row.required_review_qty, "f"),
            "reason": row.reason,
        }
        for row in rows
    ]


def _outcome_documents(rows: Sequence[ApprovalLineOutcome]) -> list[dict[str, str]]:
    return [
        {
            "request_line_id": str(row.request_line_id),
            "input_qty": format(row.input_qty, "f"),
            "approved_qty": format(row.approved_qty, "f"),
            "rejected_qty": format(row.rejected_qty, "f"),
            "reason": row.reason,
        }
        for row in rows
    ]


def _request_axes(request: MaterialRequest) -> dict[str, str]:
    return {field: getattr(request, field) for field in NON_APPROVAL_STATE_AXES}


def _exact_axes(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(_NEUTRAL_AXES):
        raise ValueError("state axes invalid")
    result = {field: str(value[field]) for field in _NEUTRAL_AXES}
    if result != _NEUTRAL_AXES:
        raise ValueError("state axes advanced")
    return result


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail("formal_principal_required", "forbidden", "审批必须使用正式权限主体")
    if actor.account_status != "active" or actor.employment_status != "active" or actor.access_mode != "active":
        _fail("material_request_approval_actor_inactive", "forbidden", "当前账号或人员不可审批")
    return actor


def _reload_current_actor(
    db: Session, supplied: FormalPrincipal, *, now: datetime
) -> FormalPrincipal:
    current = load_formal_principal(db, supplied.user_id, now=now)
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
        or current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail(
            "material_request_approval_actor_not_current",
            "forbidden",
            "正式审批权限上下文已失效，请重新读取后再操作",
        )
    return current


def _require_not_requester(request: MaterialRequest, actor: FormalPrincipal) -> None:
    if request.requester_user_id == actor.user_id or request.requester_person_id == actor.person_id:
        _fail("material_request_applicant_self_approval_forbidden", "forbidden", "申请人不得审批或复核自己的需求")


def _require_uuid(field: str, value: Any) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail(f"{field}_invalid", "invalid_request", f"{field} 无效")
    return value


def _require_version(field: str, value: Any) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail(f"material_request_{field}_invalid", "invalid_request", f"{field} 无效")
    return value


def _require_choice(field: str, value: Any, choices: set[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        _fail(f"material_request_{field}_invalid", "invalid_request", f"{field} 无效")
    return value


def _require_text(field: str, value: Any, limit: int, *, required: bool) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > limit or (required and not value):
        _fail(f"material_request_{field}_invalid", "invalid_request", f"{field} 格式无效")
    return value


def _require_aware_datetime(value: Any) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        _fail("material_request_external_decided_at_invalid", "invalid_request", "外部审批时间必须包含时区")
    return value.astimezone(timezone.utc)


def _require_trace_request_id(value: Any) -> str:
    if not isinstance(value, str) or _SAFE_TRACE.fullmatch(value) is None:
        _fail("material_request_trace_id_invalid", "invalid_request", "X-Request-ID 无效")
    return value


def _require_idempotency_key(value: Any) -> str:
    if not isinstance(value, str) or _PRINTABLE.fullmatch(value) is None:
        _fail("material_request_idempotency_key_invalid", "invalid_request", "Idempotency-Key 无效")
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    encoded = value.encode("utf-8") if isinstance(value, str) else value
    if (
        not isinstance(encoded, bytes)
        or len(encoded) < 32
        or any(marker in encoded.lower().decode("utf-8", "ignore") for marker in _PLACEHOLDERS)
    ):
        _fail("material_request_idempotency_hmac_unavailable", "service_unavailable", "需求单幂等 HMAC 密钥不可用")
    return encoded


def _idempotency_hmac(
    secret: bytes, actor_user_id: str, method: str, path: str, raw_key: str
) -> str:
    document = (
        "cloud_oam.material_request.idempotency.v1\0"
        f"actor={actor_user_id}\0method={method}\0path={path}\0key={raw_key}"
    ).encode("utf-8")
    return hmac.new(secret, document, hashlib.sha256).hexdigest()


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(func.current_timestamp()))
    if not isinstance(value, datetime):
        _fail("material_request_database_clock_invalid", "service_unavailable", "数据库时间不可用")
    return _as_utc(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _take_advisory_locks(
    db: Session, key_hash: str, request_id: uuid.UUID
) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for namespace, value in (
        ("idempotency", key_hash),
        ("request", str(request_id)),
    ):
        db.execute(
            text("SELECT pg_advisory_xact_lock(:coordinate)"),
            {"coordinate": _signed_lock_coordinate(namespace, value)},
        )


def _signed_lock_coordinate(namespace: str, value: str) -> int:
    raw = hashlib.sha256(f"material-request:{namespace}:{value}".encode("utf-8")).digest()[:8]
    return int.from_bytes(raw, "big", signed=True)


def _optional_uuid(value: Any) -> uuid.UUID | None:
    return None if value is None else uuid.UUID(str(value))


def _optional_int(value: Any) -> int | None:
    return None if value is None else int(value)


def _fail(code: str, category: str, message: str) -> None:
    raise MaterialRequestApprovalError(code, category, message)


__all__ = [
    "ApprovalCommandResult",
    "ExternalApprovalRegistrationInput",
    "ExternalApprovalRegistrationResult",
    "ExternalApprovalVerificationResult",
    "MaterialRequestApprovalError",
    "MaterialRequestApprovalInput",
    "decide_material_request_approval",
    "register_external_approval_evidence",
    "verify_external_approval_evidence",
]
