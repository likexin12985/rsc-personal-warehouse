"""Requester-owned withdraw and fail-closed direct cancellation commands.

The two commands in this module deliberately keep demand, approval and every
fulfilment projection separate:

* ``withdraw`` terminates only an active approval graph.  Previously decided
  steps and their immutable facts are never rewritten.
* ``cancel`` is a narrow direct-cancellation path.  It is available only when
  every fulfilment axis is still neutral and no current downstream fact can
  require release, compensation, notification or reconciliation work.

The caller owns the transaction.  Public functions flush but never commit or
roll back, and no notification/outbox event is produced here.
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
    ApprovalInstance,
    ApprovalStep,
    MaterialRequest,
    MaterialRequestCancellationLineFact,
    MaterialRequestCommand,
    MaterialRequestLine,
    MaterialRequestRevision,
    SubstitutionDecision,
    SupplyTask,
)
from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    ScopeGrant,
    lock_formal_principal_graph,
    load_formal_principal,
)
from ..foundation_models import (
    NotificationEvent,
    Organization,
    OutboxEvent,
    Person,
    StateTransitionEvent,
)
from ..inventory_models import InventoryTransaction
from .audit_chain import AuditChainError, append_audit_event
from .material_request_policy import (
    MaterialRequestPolicyError,
    require_request_status_transition,
)


MATERIAL_REQUEST_AUDIT_STREAM: Final[str] = "material_request"
MATERIAL_REQUEST_AGGREGATE: Final[str] = "material_request"
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
_WITHDRAWABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {"submitted", "approval_in_progress"}
)
_DIRECTLY_CANCELLABLE_STATUSES: Final[frozenset[str]] = frozenset(
    {"returned", "approved", "partially_approved", "cancellation_pending"}
)
_ACTIVE_STEP_STATUSES: Final[frozenset[str]] = frozenset(
    {"pending", "open", "awaiting_external_evidence", "evidence_pending_verification"}
)
_ACTIVE_SUBSTITUTION_STATUSES: Final[frozenset[str]] = frozenset(
    {"proposed", "confirmed"}
)
_ACTIVE_SUPPLY_STATUSES: Final[frozenset[str]] = frozenset(
    {"open", "reference_registered", "awaiting_supply"}
)
_MAX_QUANTITY: Final[Decimal] = Decimal("1000000000000000")
_TRACE_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,159}$", re.ASCII)
_IDEMPOTENCY_PATTERN = re.compile(r"^[\x21-\x7e]{16,128}$", re.ASCII)
_PLACEHOLDERS = ("replace-with", "replace_me", "replace-me", "change-me", "changeme")

_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class MaterialRequestLifecycleError(RuntimeError):
    """Stable, database-detail-free lifecycle command failure."""

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
class MaterialRequestCancellationLineInput:
    request_line_id: uuid.UUID
    cancelled_qty: Decimal
    reason: str


@dataclass(frozen=True, slots=True)
class MaterialRequestCancelInput:
    reason: str
    lines: tuple[MaterialRequestCancellationLineInput, ...] = ()


@dataclass(frozen=True, slots=True)
class MaterialRequestLifecycleResult:
    request_id: uuid.UUID
    request_no: str
    action: str
    request_status: str
    request_version: int
    revision_id: uuid.UUID
    revision_no: int
    approval_instance_id: uuid.UUID
    approval_attempt_no: int
    current_step_id: uuid.UUID | None
    state_axes: Mapping[str, str]
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _RequesterContext:
    principal: FormalPrincipal
    person: Person
    technician_grant: ScopeGrant


@dataclass(frozen=True, slots=True)
class _ReplayBundle:
    result: MaterialRequestLifecycleResult
    command: MaterialRequestCommand
    action: ApprovalAction


@dataclass(frozen=True, slots=True)
class _LockedGraph:
    request: MaterialRequest
    revision: MaterialRequestRevision
    lines: tuple[MaterialRequestLine, ...]
    all_revision_ids: tuple[uuid.UUID, ...]
    all_line_ids: tuple[uuid.UUID, ...]
    instances: tuple[ApprovalInstance, ...]
    steps_by_instance: Mapping[uuid.UUID, tuple[ApprovalStep, ...]]
    substitutions: tuple[SubstitutionDecision, ...]
    supply_tasks: tuple[SupplyTask, ...]
    cancellation_facts: tuple[MaterialRequestCancellationLineFact, ...]

    @property
    def latest_instance(self) -> ApprovalInstance:
        if not self.instances:
            _fail(
                "material_request_approval_instance_missing",
                "precondition_failed",
                "需求单缺少可核验的审批实例",
            )
        return self.instances[-1]


def withdraw_material_request(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_version: int,
    reason: str,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> MaterialRequestLifecycleResult:
    """Withdraw an exact owned request while sealing only its approval axis."""

    return _public_boundary(
        lambda: _withdraw_material_request_impl(
            db,
            actor=actor,
            material_request_id=material_request_id,
            expected_version=expected_version,
            reason=reason,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def cancel_material_request(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_version: int,
    cancellation: MaterialRequestCancelInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> MaterialRequestLifecycleResult:
    """Directly cancel only a request that has no compensation obligation."""

    return _public_boundary(
        lambda: _cancel_material_request_impl(
            db,
            actor=actor,
            material_request_id=material_request_id,
            expected_version=expected_version,
            cancellation=cancellation,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def _public_boundary(operation):
    try:
        return operation()
    except MaterialRequestLifecycleError:
        raise
    except MaterialRequestPolicyError as exc:
        raise MaterialRequestLifecycleError(exc.code, exc.category, exc.message) from None
    except FormalAccessError:
        raise MaterialRequestLifecycleError(
            "material_request_lifecycle_actor_not_current",
            "forbidden",
            "正式权限上下文已失效，请重新读取后再操作",
        ) from None
    except AuditChainError:
        raise MaterialRequestLifecycleError(
            "material_request_lifecycle_audit_unavailable",
            "service_unavailable",
            "需求单审计链不可用，本次操作未完成",
        ) from None
    except IntegrityError:
        raise MaterialRequestLifecycleError(
            "material_request_lifecycle_concurrent_conflict",
            "conflict",
            "需求单发生并发冲突，请回滚并重新读取后再操作",
        ) from None
    except DBAPIError:
        raise MaterialRequestLifecycleError(
            "material_request_lifecycle_database_unavailable",
            "service_unavailable",
            "数据库暂时不可用，本次需求单操作未完成",
        ) from None


def _withdraw_material_request_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_version: int,
    reason: str,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> MaterialRequestLifecycleResult:
    supplied = _validate_supplied_actor(actor)
    request_id = _require_uuid("material_request_id", material_request_id)
    version = _require_version(expected_version)
    checked_reason = _require_text("reason", reason, 4000, required=True)
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    path = f"/api/v1/material-requests/{request_id}/withdraw"
    key_hash = _idempotency_hmac(secret, supplied.user_id, path, raw_key)
    payload_hash = _canonical_hash(
        {
            "operation": "withdraw",
            "request_id": str(request_id),
            "expected_version": version,
            "reason": checked_reason,
            "actor_user_id": supplied.user_id,
            "actor_person_id": str(supplied.person_id),
            "authorization_version": supplied.authorization_version,
        }
    )
    _take_advisory_locks(db, key_hash, request_id)
    locked_request = _lock_request(db, request_id)
    lock_formal_principal_graph(db, (supplied.user_id,))
    now = _database_now(db)
    requester = _require_requester_context(db, supplied, "withdraw", now)
    graph = _lock_graph(db, request_id, request=locked_request)
    _require_owned_request(graph.request, requester)
    replay = _load_replay(
        db,
        key_hash=key_hash,
        request_hash=payload_hash,
        operation="withdraw",
        request_id=request_id,
        request_reference=path,
        requester=requester,
        expected_comment=checked_reason,
    )
    if replay is not None:
        _require_replay_projection(graph, replay.result)
        if any(
            fact.cancel_command_id == replay.command.id
            or fact.cancel_action_id == replay.action.id
            for fact in graph.cancellation_facts
        ):
            _fail(
                "material_request_withdraw_fact_invalid",
                "service_unavailable",
                "撤回命令错误绑定了取消明细事实",
            )
        return replace(replay.result, replayed=True)
    _require_exact_version(graph.request, version)
    if graph.request.status not in _WITHDRAWABLE_STATUSES:
        _fail(
            "material_request_not_withdrawable",
            "conflict",
            "当前需求单状态不允许撤回",
        )
    require_request_status_transition(graph.request.status, "withdrawn")
    _require_neutral_axes(graph.request)
    instance = _require_active_instance(graph)
    steps = graph.steps_by_instance.get(instance.id, ())
    before = _safe_snapshot(graph)
    source_status = graph.request.status

    result = _result_for_target(
        graph,
        instance=instance,
        action="withdraw",
        request_status="withdrawn",
        target_version=graph.request.version + 1,
    )
    command = _command_fact(
        graph=graph,
        result=result,
        operation="withdraw",
        key_hash=key_hash,
        request_reference=path,
        request_hash=payload_hash,
        requester=requester,
        occurred_at=now,
    )
    db.add(command)
    db.flush()
    db.add(
        _approval_action(
            command=command,
            instance=instance,
            requester=requester,
            action="withdraw",
            comment=checked_reason,
            occurred_at=now,
        )
    )
    db.flush()

    cancellable_steps = tuple(step for step in steps if step.status in _ACTIVE_STEP_STATUSES)
    if not cancellable_steps:
        _fail(
            "material_request_active_approval_step_missing",
            "precondition_failed",
            "审批实例缺少可封存的当前步骤",
        )
    for step in cancellable_steps:
        step.status = "cancelled"
        step.version += 1
        step.updated_at = now
        # A withdrawal is not an approval decision.  0030 requires these
        # values to remain NULL for cancelled steps.
        step.decided_at = None
        step.decision_manifest_sha256 = None
    db.flush()

    instance.status = "withdrawn"
    instance.current_step_no = None
    instance.current_step_id = None
    instance.completed_at = now
    instance.version += 1
    instance.updated_at = now
    graph.request.status = "withdrawn"
    graph.request.withdrawn_at = now
    graph.request.version += 1
    graph.request.updated_at = now
    db.flush()
    _require_neutral_axes(graph.request)
    _assert_result_projection(graph.request, instance, result)
    db.add(
        _state_event(
            graph=graph,
            from_status=source_status,
            to_status="withdrawn",
            reason="material_request_withdrawn_by_requester",
            actor_user_id=requester.principal.user_id,
            key_hash=key_hash,
            occurred_at=now,
        )
    )
    db.flush()
    append_audit_event(
        db,
        stream_key=MATERIAL_REQUEST_AUDIT_STREAM,
        actor_user_id=requester.principal.user_id,
        action="material_request.withdraw",
        aggregate_type=MATERIAL_REQUEST_AGGREGATE,
        aggregate_id=str(graph.request.id),
        before_jsonb=before,
        after_jsonb={
            **_safe_snapshot(graph),
            "approval_instance_id": str(instance.id),
            "cancelled_open_step_ids": [str(step.id) for step in cancellable_steps],
            "reason_sha256": _text_hash(checked_reason),
        },
        request_id=trace_id,
        occurred_at=now,
    )
    db.flush()
    return result


def _cancel_material_request_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_version: int,
    cancellation: MaterialRequestCancelInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> MaterialRequestLifecycleResult:
    supplied = _validate_supplied_actor(actor)
    request_id = _require_uuid("material_request_id", material_request_id)
    version = _require_version(expected_version)
    prepared = _validate_cancellation(cancellation)
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    path = f"/api/v1/material-requests/{request_id}/cancel"
    key_hash = _idempotency_hmac(secret, supplied.user_id, path, raw_key)
    line_documents = tuple(
        {
            "request_line_id": str(line.request_line_id),
            "cancelled_qty": format(line.cancelled_qty, "f"),
            "reason": line.reason,
        }
        for line in prepared.lines
    )
    payload_hash = _canonical_hash(
        {
            "operation": "cancel",
            "request_id": str(request_id),
            "expected_version": version,
            "reason": prepared.reason,
            "lines": line_documents,
            "actor_user_id": supplied.user_id,
            "actor_person_id": str(supplied.person_id),
            "authorization_version": supplied.authorization_version,
        }
    )
    _take_advisory_locks(db, key_hash, request_id)
    locked_request = _lock_request(db, request_id)
    lock_formal_principal_graph(db, (supplied.user_id,))
    now = _database_now(db)
    requester = _require_requester_context(db, supplied, "cancel", now)
    graph = _lock_graph(db, request_id, request=locked_request)
    _require_owned_request(graph.request, requester)
    replay = _load_replay(
        db,
        key_hash=key_hash,
        request_hash=payload_hash,
        operation="cancel",
        request_id=request_id,
        request_reference=path,
        requester=requester,
        expected_comment=prepared.reason,
    )
    if replay is not None:
        _require_replay_projection(graph, replay.result)
        _require_cancellation_replay_facts(
            graph=graph,
            replay=replay,
            requested=prepared.lines,
        )
        return replace(replay.result, replayed=True)
    _require_exact_version(graph.request, version)
    if graph.request.status == "draft":
        _fail(
            "material_request_draft_cancel_schema_migration_required",
            "precondition_failed",
            "草稿直接取消需先完成正式状态约束迁移",
        )
    if graph.request.status not in _DIRECTLY_CANCELLABLE_STATUSES:
        _fail(
            "material_request_not_directly_cancellable",
            "conflict",
            "当前需求单不能直接取消",
        )
    require_request_status_transition(graph.request.status, "cancelled")
    _require_neutral_axes(graph.request)
    _require_no_compensation_facts(db, graph)
    instance = _require_terminal_approval_instance(graph)
    if graph.cancellation_facts:
        _fail(
            "material_request_prior_cancellation_fact_exists",
            "precondition_failed",
            "需求单已存在取消逐行事实，必须先核验既有因果链",
        )
    _require_exact_cancellation_lines(graph.lines, prepared.lines)
    before = _safe_snapshot(graph)
    source_status = graph.request.status

    result = _result_for_target(
        graph,
        instance=instance,
        action="cancel",
        request_status="cancelled",
        target_version=graph.request.version + 1,
    )
    command = _command_fact(
        graph=graph,
        result=result,
        operation="cancel",
        key_hash=key_hash,
        request_reference=path,
        request_hash=payload_hash,
        requester=requester,
        occurred_at=now,
    )
    action = _approval_action(
        command=command,
        instance=instance,
        requester=requester,
        action="cancel",
        comment=prepared.reason,
        occurred_at=now,
    )
    requested_by_line = {row.request_line_id: row for row in prepared.lines}
    cancellation_facts = tuple(
        MaterialRequestCancellationLineFact(
            id=uuid.uuid4(),
            cancel_action_id=action.id,
            cancel_command_id=command.id,
            instance_id=instance.id,
            request_id=graph.request.id,
            request_revision_id=graph.revision.id,
            request_line_id=line.id,
            final_approved_qty_before=line.final_approved_qty,
            cancelled_qty=requested_by_line[line.id].cancelled_qty,
            reason=requested_by_line[line.id].reason,
            actor_user_id=requester.principal.user_id,
            actor_person_id=requester.person.id,
            actor_role_assignment_id=requester.technician_grant.assignment_id,
            authorization_version=requester.principal.authorization_version,
            occurred_at=now,
            created_at=now,
        )
        for line in graph.lines
        if line.final_approved_qty > Decimal("0.000")
    )
    command.request_jsonb = {
        **command.request_jsonb,
        "cancellation_fact_count": len(cancellation_facts),
        "cancellation_fact_manifest_sha256": _cancellation_fact_manifest(
            cancellation_facts
        ),
    }
    db.add(command)
    db.flush()
    db.add(action)
    db.flush()
    db.add_all(cancellation_facts)
    db.flush()
    _require_cancellation_facts(
        graph=graph,
        command=command,
        action=action,
        requested=prepared.lines,
        facts=cancellation_facts,
        require_projection=False,
    )

    for line in graph.lines:
        line.cancelled_qty = line.final_approved_qty
        line.status = "cancelled"
        line.version += 1
        line.updated_at = now
    # SQLite has no deferred constraint triggers.  Persist the complete line
    # projection after its immutable facts and before exposing the terminal
    # parent projection; PostgreSQL still revalidates the whole graph at commit.
    db.flush(list(graph.lines))
    graph.request.status = "cancelled"
    graph.request.cancelled_at = now
    graph.request.version += 1
    graph.request.updated_at = now
    db.flush()
    _require_neutral_axes(graph.request)
    _assert_result_projection(graph.request, instance, result)
    _require_cancellation_facts(
        graph=graph,
        command=command,
        action=action,
        requested=prepared.lines,
        facts=cancellation_facts,
        require_projection=True,
    )
    db.add(
        _state_event(
            graph=graph,
            from_status=source_status,
            to_status="cancelled",
            reason="material_request_safely_cancelled_by_requester",
            actor_user_id=requester.principal.user_id,
            key_hash=key_hash,
            occurred_at=now,
        )
    )
    db.flush()
    append_audit_event(
        db,
        stream_key=MATERIAL_REQUEST_AUDIT_STREAM,
        actor_user_id=requester.principal.user_id,
        action="material_request.cancel",
        aggregate_type=MATERIAL_REQUEST_AGGREGATE,
        aggregate_id=str(graph.request.id),
        before_jsonb=before,
        after_jsonb={
            **_safe_snapshot(graph),
            "approval_instance_id": str(instance.id),
            "cancellation_fact_count": len(cancellation_facts),
            "cancellation_fact_manifest_sha256": _cancellation_fact_manifest(
                cancellation_facts
            ),
            "reason_sha256": _text_hash(prepared.reason),
        },
        request_id=trace_id,
        occurred_at=now,
    )
    db.flush()
    return result


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail("formal_principal_required", "forbidden", "需求单必须使用正式权限主体")
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _fail("material_request_actor_inactive", "forbidden", "当前账号或人员不可操作需求单")
    return actor


def _require_requester_context(
    db: Session,
    supplied: FormalPrincipal,
    action: str,
    now: datetime,
) -> _RequesterContext:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError:
        _fail(
            "material_request_actor_not_current",
            "forbidden",
            "正式权限上下文已失效，请重新读取后再操作",
        )
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail(
            "material_request_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取后再操作",
        )
    if (
        current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail("material_request_actor_inactive", "forbidden", "当前账号或人员不可操作需求单")
    person = db.scalar(
        select(Person)
        .where(Person.id == current.person_id)
        .execution_options(populate_existing=True)
    )
    if person is None or person.employment_status != "active":
        _fail("material_request_requester_invalid", "forbidden", "申请人当前不可用")
    grants = tuple(
        grant
        for grant in current.assignments
        if grant.role_code == "technician"
        and grant.scope_type == "person"
        and _same_uuid(grant.scope_id, person.id)
    )
    eligible = tuple(
        grant
        for grant in grants
        if _selected_grant_allows(
            db,
            current,
            grant,
            action,
            target_scope_id=str(person.id),
        )
    )
    if len(eligible) != 1:
        _fail(
            "material_request_self_scope_forbidden",
            "forbidden",
            "当前人员没有唯一且覆盖本人的工程师需求权限",
        )
    return _RequesterContext(current, person, eligible[0])


def _selected_grant_allows(
    db: Session,
    principal: FormalPrincipal,
    grant: ScopeGrant,
    action: str,
    *,
    target_scope_id: str,
) -> bool:
    selected = replace(
        principal,
        assignments=(grant,),
        entitlements=tuple(
            row
            for row in principal.entitlements
            if row.assignment_id == grant.assignment_id
        ),
    )
    try:
        return principal.allows(
            db,
            "material_request",
            action,
            target_scope_type="person",
            target_scope_id=target_scope_id,
        ) and selected.allows(
            db,
            "material_request",
            action,
            target_scope_type="person",
            target_scope_id=target_scope_id,
        )
    except FormalAccessError:
        _fail(
            "material_request_scope_graph_invalid",
            "forbidden",
            "需求单权限范围图无效",
        )


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


def _lock_graph(
    db: Session,
    request_id: uuid.UUID,
    *,
    request: MaterialRequest | None = None,
) -> _LockedGraph:
    if request is None:
        request = _lock_request(db, request_id)
    elif request.id != request_id:
        _fail(
            "material_request_lock_identity_invalid",
            "service_unavailable",
            "需求单父级锁绑定无效",
        )
    revisions = tuple(
        db.scalars(
            select(MaterialRequestRevision)
            .where(MaterialRequestRevision.request_id == request_id)
            .order_by(MaterialRequestRevision.revision_no, MaterialRequestRevision.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    current = tuple(row for row in revisions if row.revision_no == request.revision_no)
    if (
        not revisions
        or len(current) != 1
        or revisions[-1].id != current[0].id
    ):
        _fail(
            "material_request_revision_projection_invalid",
            "service_unavailable",
            "需求单当前版本投影无效",
        )
    all_lines = tuple(
        db.scalars(
            select(MaterialRequestLine)
            .where(MaterialRequestLine.request_id == request_id)
            .order_by(
                MaterialRequestLine.revision_no,
                MaterialRequestLine.line_no,
                MaterialRequestLine.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    current_lines = tuple(
        db.scalars(
            select(MaterialRequestLine)
            .where(
                MaterialRequestLine.request_id == request_id,
                MaterialRequestLine.revision_id == current[0].id,
            )
            .order_by(MaterialRequestLine.line_no, MaterialRequestLine.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    preview_current_line_ids = tuple(
        row.id for row in all_lines if row.revision_id == current[0].id
    )
    if (
        not current_lines
        or len({row.id for row in current_lines}) != len(current_lines)
        or tuple(row.id for row in current_lines) != preview_current_line_ids
    ):
        _fail(
            "material_request_line_projection_invalid",
            "service_unavailable",
            "需求单当前明细投影无效",
        )
    instances = tuple(
        db.scalars(
            select(ApprovalInstance)
            .where(ApprovalInstance.request_id == request_id)
            .order_by(ApprovalInstance.attempt_no, ApprovalInstance.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    instance_ids = tuple(row.id for row in instances)
    steps = (
        tuple(
            db.scalars(
                select(ApprovalStep)
                .where(ApprovalStep.instance_id.in_(instance_ids))
                .order_by(
                    ApprovalStep.instance_id,
                    ApprovalStep.step_no,
                    ApprovalStep.attempt_no,
                    ApprovalStep.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        if instance_ids
        else ()
    )
    steps_by_instance: dict[uuid.UUID, list[ApprovalStep]] = {}
    for step in steps:
        steps_by_instance.setdefault(step.instance_id, []).append(step)
    all_line_ids = tuple(row.id for row in all_lines)
    substitutions = (
        tuple(
            db.scalars(
                select(SubstitutionDecision)
                .where(SubstitutionDecision.request_line_id.in_(all_line_ids))
                .order_by(SubstitutionDecision.request_line_id, SubstitutionDecision.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        if all_line_ids
        else ()
    )
    supply_tasks = (
        tuple(
            db.scalars(
                select(SupplyTask)
                .where(SupplyTask.request_line_id.in_(all_line_ids))
                .order_by(SupplyTask.request_line_id, SupplyTask.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        if all_line_ids
        else ()
    )
    cancellation_facts = tuple(
        db.scalars(
            select(MaterialRequestCancellationLineFact)
            .where(MaterialRequestCancellationLineFact.request_id == request_id)
            .order_by(
                MaterialRequestCancellationLineFact.cancel_command_id,
                MaterialRequestCancellationLineFact.request_line_id,
                MaterialRequestCancellationLineFact.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    return _LockedGraph(
        request=request,
        revision=current[0],
        lines=current_lines,
        all_revision_ids=tuple(row.id for row in revisions),
        all_line_ids=all_line_ids,
        instances=instances,
        steps_by_instance={key: tuple(value) for key, value in steps_by_instance.items()},
        substitutions=substitutions,
        supply_tasks=supply_tasks,
        cancellation_facts=cancellation_facts,
    )


def _require_owned_request(
    request: MaterialRequest, requester: _RequesterContext
) -> None:
    if (
        request.requester_user_id != requester.principal.user_id
        or request.requester_person_id != requester.person.id
        or request.created_by_user_id != requester.principal.user_id
    ):
        _fail(
            "material_request_requester_mismatch",
            "forbidden",
            "只能操作本人创建的需求单",
        )


def _require_exact_version(request: MaterialRequest, expected_version: int) -> None:
    if request.version != expected_version:
        _fail(
            "material_request_version_conflict",
            "conflict",
            "需求单版本已变化，请重新读取后再操作",
        )


def _require_neutral_axes(request: MaterialRequest) -> None:
    actual = _request_axes(request)
    if actual != _NEUTRAL_AXES:
        _fail(
            "material_request_compensation_required",
            "precondition_failed",
            "需求单已产生下游状态，必须进入补偿流程后再取消",
        )


def _require_active_instance(graph: _LockedGraph) -> ApprovalInstance:
    active = tuple(row for row in graph.instances if row.status == "active")
    if len(active) != 1 or graph.latest_instance.id != active[0].id:
        _fail(
            "material_request_active_approval_instance_invalid",
            "precondition_failed",
            "需求单当前审批实例无法唯一核验",
        )
    instance = active[0]
    if (
        instance.request_revision_id != graph.revision.id
        or instance.revision_no != graph.revision.revision_no
        or instance.current_step_id is None
        or instance.current_step_no is None
    ):
        _fail(
            "material_request_active_approval_instance_invalid",
            "precondition_failed",
            "需求单当前审批实例绑定无效",
        )
    steps = graph.steps_by_instance.get(instance.id, ())
    current = tuple(
        step
        for step in steps
        if step.id == instance.current_step_id
        and step.step_no == instance.current_step_no
        and step.status in _ACTIVE_STEP_STATUSES - {"pending"}
    )
    if len(current) != 1:
        _fail(
            "material_request_active_approval_step_invalid",
            "precondition_failed",
            "需求单当前审批步骤无法唯一核验",
        )
    return instance


def _require_terminal_approval_instance(graph: _LockedGraph) -> ApprovalInstance:
    if any(row.status == "active" for row in graph.instances):
        _fail(
            "material_request_approval_still_active",
            "precondition_failed",
            "需求单审批仍在进行，不能直接取消",
        )
    instance = graph.latest_instance
    expected = "returned" if graph.request.status == "returned" else "completed"
    if instance.status != expected or instance.current_step_id is not None or instance.current_step_no is not None:
        _fail(
            "material_request_terminal_approval_instance_invalid",
            "precondition_failed",
            "需求单审批终态无法安全核验",
        )
    if graph.request.status != "returned" and (
        instance.request_revision_id != graph.revision.id
        or instance.revision_no != graph.revision.revision_no
    ):
        _fail(
            "material_request_terminal_approval_instance_invalid",
            "precondition_failed",
            "需求单审批实例未绑定当前版本",
        )
    if instance.completed_at is None:
        _fail(
            "material_request_terminal_approval_instance_invalid",
            "precondition_failed",
            "需求单审批实例缺少真实终结时间",
        )
    return instance


def _require_no_compensation_facts(db: Session, graph: _LockedGraph) -> None:
    if any(row.status in _ACTIVE_SUBSTITUTION_STATUSES for row in graph.substitutions):
        _fail(
            "material_request_active_substitution_exists",
            "precondition_failed",
            "需求单仍有活动替代料决定，不能直接取消",
        )
    if any(row.status in _ACTIVE_SUPPLY_STATUSES for row in graph.supply_tasks):
        _fail(
            "material_request_active_supply_task_exists",
            "precondition_failed",
            "需求单仍有活动缺货任务，不能直接取消",
        )
    coordinates = {
        str(graph.request.id),
        graph.request.request_no,
        *(str(value) for value in graph.all_revision_ids),
        *(str(value) for value in graph.all_line_ids),
        *(str(row.id) for row in graph.substitutions),
        *(str(row.id) for row in graph.supply_tasks),
    }
    inventory_fact = db.scalar(
        select(InventoryTransaction.id)
        .where(InventoryTransaction.source_document_id.in_(tuple(sorted(coordinates))))
    )
    if inventory_fact is not None:
        _fail(
            "material_request_inventory_fact_exists",
            "precondition_failed",
            "需求单已产生库存事实，必须走释放或冲销流程",
        )
    notification_fact = db.scalar(
        select(NotificationEvent.id)
        .where(NotificationEvent.business_id.in_(tuple(sorted(coordinates))))
    )
    if notification_fact is not None:
        _fail(
            "material_request_notification_fact_exists",
            "precondition_failed",
            "需求单已产生通知事实，不能直接取消",
        )
    outbox_fact = db.scalar(
        select(OutboxEvent.id)
        .where(OutboxEvent.aggregate_id.in_(tuple(sorted(coordinates))))
    )
    if outbox_fact is not None:
        _fail(
            "material_request_outbox_fact_exists",
            "precondition_failed",
            "需求单已进入异步处理链，不能直接取消",
        )


def _validate_cancellation(value: MaterialRequestCancelInput) -> MaterialRequestCancelInput:
    if not isinstance(value, MaterialRequestCancelInput):
        _fail("material_request_cancellation_invalid", "invalid_request", "取消命令格式无效")
    reason = _require_text("reason", value.reason, 4000, required=True)
    if not isinstance(value.lines, tuple) or len(value.lines) > 200:
        _fail("material_request_cancellation_lines_invalid", "invalid_request", "取消明细数量无效")
    checked: list[MaterialRequestCancellationLineInput] = []
    seen: set[uuid.UUID] = set()
    for row in value.lines:
        if not isinstance(row, MaterialRequestCancellationLineInput):
            _fail("material_request_cancellation_line_invalid", "invalid_request", "取消明细格式无效")
        line_id = _require_uuid("request_line_id", row.request_line_id)
        if line_id in seen:
            _fail("material_request_cancellation_line_duplicate", "invalid_request", "取消明细不能重复")
        seen.add(line_id)
        quantity = _require_quantity(row.cancelled_qty)
        line_reason = _require_text("line_reason", row.reason, 4000, required=True)
        checked.append(MaterialRequestCancellationLineInput(line_id, quantity, line_reason))
    return MaterialRequestCancelInput(reason=reason, lines=tuple(checked))


def _require_exact_cancellation_lines(
    lines: Sequence[MaterialRequestLine],
    requested: Sequence[MaterialRequestCancellationLineInput],
) -> None:
    if any(line.cancelled_qty != Decimal("0.000") for line in lines):
        _fail(
            "material_request_prior_cancellation_fact_exists",
            "precondition_failed",
            "需求明细已存在取消数量，必须核验补偿链后处理",
        )
    expected = {
        line.id: line.final_approved_qty
        for line in lines
        if line.final_approved_qty > Decimal("0.000")
    }
    supplied = {line.request_line_id: line.cancelled_qty for line in requested}
    if set(expected) != set(supplied) or any(
        supplied[line_id] != quantity for line_id, quantity in expected.items()
    ):
        _fail(
            "material_request_cancellation_lines_not_exact",
            "invalid_request",
            "取消明细必须完整覆盖当前全部已批准数量",
        )


def _require_cancellation_replay_facts(
    *,
    graph: _LockedGraph,
    replay: _ReplayBundle,
    requested: Sequence[MaterialRequestCancellationLineInput],
) -> None:
    _require_cancellation_facts(
        graph=graph,
        command=replay.command,
        action=replay.action,
        requested=requested,
        facts=graph.cancellation_facts,
        require_projection=True,
    )


def _require_cancellation_facts(
    *,
    graph: _LockedGraph,
    command: MaterialRequestCommand,
    action: ApprovalAction,
    requested: Sequence[MaterialRequestCancellationLineInput],
    facts: Sequence[MaterialRequestCancellationLineFact],
    require_projection: bool,
) -> None:
    lines_by_id = {
        line.id: line
        for line in graph.lines
        if line.final_approved_qty > Decimal("0.000")
    }
    requested_by_id = {row.request_line_id: row for row in requested}
    fact_rows = tuple(facts)
    facts_by_line = {row.request_line_id: row for row in fact_rows}
    if (
        set(requested_by_id) != set(lines_by_id)
        or len(facts_by_line) != len(fact_rows)
        or set(facts_by_line) != set(lines_by_id)
    ):
        _fail(
            "material_request_cancellation_fact_set_invalid",
            "service_unavailable",
            "取消逐行事实不完整或包含多余明细",
        )
    for line_id, line in lines_by_id.items():
        supplied = requested_by_id[line_id]
        fact = facts_by_line[line_id]
        if (
            supplied.cancelled_qty != line.final_approved_qty
            or fact.cancel_action_id != action.id
            or fact.cancel_command_id != command.id
            or fact.instance_id != action.instance_id
            or fact.request_id != graph.request.id
            or fact.request_revision_id != graph.revision.id
            or fact.final_approved_qty_before != line.final_approved_qty
            or fact.cancelled_qty != line.final_approved_qty
            or fact.cancelled_qty != supplied.cancelled_qty
            or fact.reason != supplied.reason
            or fact.actor_user_id != command.actor_user_id
            or fact.actor_person_id != command.actor_person_id
            or fact.actor_role_assignment_id
            != command.actor_role_assignment_id
            or fact.authorization_version != command.authorization_version
            or not _same_timestamp(fact.occurred_at, command.occurred_at)
            or not _same_timestamp(fact.created_at, command.created_at)
        ):
            _fail(
                "material_request_cancellation_fact_invalid",
                "service_unavailable",
                "取消逐行事实的因果绑定、数量或原因无效",
            )
    request_document = command.request_jsonb
    expected_manifest = _cancellation_fact_manifest(fact_rows)
    if (
        not isinstance(request_document, dict)
        or request_document.get("cancellation_fact_count") != len(fact_rows)
        or not isinstance(
            request_document.get("cancellation_fact_manifest_sha256"), str
        )
        or not hmac.compare_digest(
            request_document["cancellation_fact_manifest_sha256"],
            expected_manifest,
        )
    ):
        _fail(
            "material_request_cancellation_fact_manifest_invalid",
            "service_unavailable",
            "取消逐行事实清单摘要无效",
        )
    if require_projection and any(
        line.status != "cancelled"
        or line.cancelled_qty != line.final_approved_qty
        for line in graph.lines
    ):
        _fail(
            "material_request_cancellation_projection_invalid",
            "conflict",
            "取消逐行事实与当前明细投影不一致",
        )


def _cancellation_fact_manifest(
    facts: Sequence[MaterialRequestCancellationLineFact],
) -> str:
    documents = tuple(
        sorted(
            (_cancellation_fact_document(row) for row in facts),
            key=lambda row: (row["request_line_id"], row["fact_id"]),
        )
    )
    return _canonical_hash(documents)


def _cancellation_fact_document(
    fact: MaterialRequestCancellationLineFact,
) -> dict[str, Any]:
    return {
        "fact_id": str(fact.id),
        "cancel_action_id": str(fact.cancel_action_id),
        "cancel_command_id": str(fact.cancel_command_id),
        "instance_id": str(fact.instance_id),
        "request_id": str(fact.request_id),
        "request_revision_id": str(fact.request_revision_id),
        "request_line_id": str(fact.request_line_id),
        "final_approved_qty_before": format(
            fact.final_approved_qty_before, "f"
        ),
        "cancelled_qty": format(fact.cancelled_qty, "f"),
        "reason_sha256": _text_hash(fact.reason),
        "actor_user_id": fact.actor_user_id,
        "actor_person_id": str(fact.actor_person_id),
        "actor_role_assignment_id": str(fact.actor_role_assignment_id),
        "authorization_version": fact.authorization_version,
        "occurred_at": _timestamp_token(fact.occurred_at),
    }


def _result_for_target(
    graph: _LockedGraph,
    *,
    instance: ApprovalInstance,
    action: str,
    request_status: str,
    target_version: int,
) -> MaterialRequestLifecycleResult:
    return MaterialRequestLifecycleResult(
        request_id=graph.request.id,
        request_no=graph.request.request_no,
        action=action,
        request_status=request_status,
        request_version=target_version,
        revision_id=graph.revision.id,
        revision_no=graph.revision.revision_no,
        approval_instance_id=instance.id,
        approval_attempt_no=instance.attempt_no,
        current_step_id=None,
        state_axes=dict(_request_axes(graph.request)),
    )


def _command_fact(
    *,
    graph: _LockedGraph,
    result: MaterialRequestLifecycleResult,
    operation: str,
    key_hash: str,
    request_reference: str,
    request_hash: str,
    requester: _RequesterContext,
    occurred_at: datetime,
) -> MaterialRequestCommand:
    document = _result_document(result)
    return MaterialRequestCommand(
        id=uuid.uuid4(),
        operation=operation,
        request_id=graph.request.id,
        target_version=result.request_version,
        idempotency_key_hash=key_hash,
        request_reference=request_reference,
        request_hash=request_hash,
        result_hash=_canonical_hash(document),
        request_jsonb={
            "schema": "rsc.material_request_lifecycle_command.v1",
            "operation": operation,
            "request_id": str(graph.request.id),
            "revision_id": str(graph.revision.id),
            "revision_no": graph.revision.revision_no,
            "target_version": result.request_version,
            "payload_sha256": request_hash,
            "sensitive_fields": "excluded",
        },
        result_jsonb=document,
        actor_user_id=requester.principal.user_id,
        actor_person_id=requester.person.id,
        actor_role_assignment_id=requester.technician_grant.assignment_id,
        authorization_version=requester.principal.authorization_version,
        occurred_at=occurred_at,
        created_at=occurred_at,
    )


def _approval_action(
    *,
    command: MaterialRequestCommand,
    instance: ApprovalInstance,
    requester: _RequesterContext,
    action: str,
    comment: str,
    occurred_at: datetime,
) -> ApprovalAction:
    return ApprovalAction(
        id=uuid.uuid4(),
        instance_id=instance.id,
        step_id=None,
        command_id=command.id,
        action=action,
        actor_user_id=requester.principal.user_id,
        actor_person_id=requester.person.id,
        actor_role_assignment_id=requester.technician_grant.assignment_id,
        authorization_version=requester.principal.authorization_version,
        source_mode="internal",
        comment=comment,
        occurred_at=occurred_at,
        created_at=occurred_at,
    )


def _state_event(
    *,
    graph: _LockedGraph,
    from_status: str,
    to_status: str,
    reason: str,
    actor_user_id: str,
    key_hash: str,
    occurred_at: datetime,
) -> StateTransitionEvent:
    return StateTransitionEvent(
        id=uuid.uuid4(),
        aggregate_type=MATERIAL_REQUEST_AGGREGATE,
        aggregate_id=str(graph.request.id),
        from_status=from_status,
        to_status=to_status,
        reason=reason,
        actor_id=actor_user_id,
        idempotency_key=f"mr:{key_hash}:{to_status}",
        occurred_at=occurred_at,
        metadata_jsonb={
            "request_id": str(graph.request.id),
            "request_no": graph.request.request_no,
            "revision_id": str(graph.revision.id),
            "revision_no": graph.revision.revision_no,
            "idempotency_key_hash": key_hash,
        },
        created_at=occurred_at,
    )


def _load_replay(
    db: Session,
    *,
    key_hash: str,
    request_hash: str,
    operation: str,
    request_id: uuid.UUID,
    request_reference: str,
    requester: _RequesterContext,
    expected_comment: str,
) -> _ReplayBundle | None:
    row = db.scalar(
        select(MaterialRequestCommand)
        .where(MaterialRequestCommand.idempotency_key_hash == key_hash)
        .execution_options(populate_existing=True)
    )
    if row is None:
        return None
    if row.request_hash != request_hash or row.operation != operation:
        _fail("material_request_idempotency_conflict", "conflict", "幂等键已用于不同的需求单命令")
    if row.request_id != request_id or row.request_reference != request_reference:
        _fail(
            "material_request_idempotency_record_invalid",
            "service_unavailable",
            "需求单幂等记录绑定无效",
        )
    if (
        row.actor_user_id != requester.principal.user_id
        or row.actor_person_id != requester.person.id
        or row.actor_role_assignment_id
        != requester.technician_grant.assignment_id
        or row.authorization_version
        != requester.principal.authorization_version
    ):
        _fail(
            "material_request_idempotency_actor_mismatch",
            "forbidden",
            "幂等记录不属于当前有效申请人",
        )
    if not isinstance(row.result_jsonb, dict) or not hmac.compare_digest(
        row.result_hash or "", _canonical_hash(row.result_jsonb)
    ):
        _fail(
            "material_request_idempotency_record_invalid",
            "service_unavailable",
            "需求单幂等结果无法安全重放",
        )
    result = _result_from_document(row.result_jsonb, operation=operation)
    expected_request_document = {
        "schema": "rsc.material_request_lifecycle_command.v1",
        "operation": operation,
        "request_id": str(request_id),
        "revision_id": str(result.revision_id),
        "revision_no": result.revision_no,
        "target_version": result.request_version,
        "payload_sha256": request_hash,
        "sensitive_fields": "excluded",
    }
    request_document = row.request_jsonb
    if not isinstance(request_document, dict):
        _fail(
            "material_request_idempotency_record_invalid",
            "service_unavailable",
            "需求单幂等命令载荷无法安全核验",
        )
    if operation == "cancel":
        expected_keys = set(expected_request_document) | {
            "cancellation_fact_count",
            "cancellation_fact_manifest_sha256",
        }
        fact_count = request_document.get("cancellation_fact_count")
        fact_manifest = request_document.get(
            "cancellation_fact_manifest_sha256"
        )
        if (
            set(request_document) != expected_keys
            or isinstance(fact_count, bool)
            or not isinstance(fact_count, int)
            or fact_count < 0
            or not isinstance(fact_manifest, str)
            or len(fact_manifest) != 64
        ):
            _fail(
                "material_request_cancellation_fact_manifest_invalid",
                "service_unavailable",
                "取消命令的逐行事实清单无法安全核验",
            )
    elif request_document != expected_request_document:
        _fail(
            "material_request_idempotency_record_invalid",
            "service_unavailable",
            "需求单幂等命令载荷无法安全核验",
        )
    if any(
        request_document.get(key) != value
        for key, value in expected_request_document.items()
    ):
        _fail(
            "material_request_idempotency_record_invalid",
            "service_unavailable",
            "需求单幂等命令载荷无法安全核验",
        )
    if (
        row.target_version != result.request_version
        or row.request_id != result.request_id
        or not _same_timestamp(row.occurred_at, row.created_at)
    ):
        _fail(
            "material_request_idempotency_record_invalid",
            "service_unavailable",
            "需求单幂等结果无法安全重放",
        )
    actions = tuple(
        db.scalars(
            select(ApprovalAction)
            .where(ApprovalAction.command_id == row.id)
            .order_by(ApprovalAction.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(actions) != 1:
        _fail(
            "material_request_lifecycle_action_invalid",
            "service_unavailable",
            "需求单生命周期动作事实无法唯一核验",
        )
    action = actions[0]
    if (
        action.instance_id != result.approval_instance_id
        or action.step_id is not None
        or action.command_id != row.id
        or action.action != operation
        or action.actor_user_id != row.actor_user_id
        or action.actor_person_id != row.actor_person_id
        or action.actor_role_assignment_id != row.actor_role_assignment_id
        or action.authorization_version != row.authorization_version
        or action.source_mode != "internal"
        or action.comment != expected_comment
        or not _same_timestamp(action.occurred_at, row.occurred_at)
        or not _same_timestamp(action.created_at, row.created_at)
    ):
        _fail(
            "material_request_lifecycle_action_invalid",
            "service_unavailable",
            "需求单生命周期动作事实绑定无效",
        )
    return _ReplayBundle(result=result, command=row, action=action)


def _result_document(result: MaterialRequestLifecycleResult) -> dict[str, Any]:
    return {
        "kind": "lifecycle",
        "schema_version": "1.0",
        "request_id": str(result.request_id),
        "request_no": result.request_no,
        "action": result.action,
        "request_status": result.request_status,
        "request_version": result.request_version,
        "revision_id": str(result.revision_id),
        "revision_no": result.revision_no,
        "approval_instance_id": str(result.approval_instance_id),
        "approval_attempt_no": result.approval_attempt_no,
        "current_step_id": None,
        "state_axes": dict(result.state_axes),
    }


def _result_from_document(
    value: Mapping[str, Any], *, operation: str
) -> MaterialRequestLifecycleResult:
    try:
        if set(value) != {
            "kind",
            "schema_version",
            "request_id",
            "request_no",
            "action",
            "request_status",
            "request_version",
            "revision_id",
            "revision_no",
            "approval_instance_id",
            "approval_attempt_no",
            "current_step_id",
            "state_axes",
        }:
            raise ValueError
        if (
            value.get("kind") != "lifecycle"
            or value.get("schema_version") != "1.0"
            or value.get("action") != operation
            or value.get("request_status")
            != ("withdrawn" if operation == "withdraw" else "cancelled")
            or value.get("current_step_id") is not None
        ):
            raise ValueError
        axes = _exact_axes(value.get("state_axes"))
        return MaterialRequestLifecycleResult(
            request_id=_strict_uuid(value, "request_id"),
            request_no=_strict_string(value, "request_no"),
            action=operation,
            request_status=_strict_string(value, "request_status"),
            request_version=_strict_nonnegative_int(value, "request_version"),
            revision_id=_strict_uuid(value, "revision_id"),
            revision_no=_strict_positive_int(value, "revision_no"),
            approval_instance_id=_strict_uuid(value, "approval_instance_id"),
            approval_attempt_no=_strict_positive_int(
                value, "approval_attempt_no"
            ),
            current_step_id=None,
            state_axes=axes,
        )
    except (KeyError, TypeError, ValueError):
        _fail(
            "material_request_idempotency_record_invalid",
            "service_unavailable",
            "需求单幂等结果无法安全重放",
        )


def _require_replay_projection(
    graph: _LockedGraph, result: MaterialRequestLifecycleResult
) -> None:
    request = graph.request
    instance = graph.latest_instance
    if (
        request.id != result.request_id
        or request.version != result.request_version
        or request.status != result.request_status
        or graph.revision.id != result.revision_id
        or graph.revision.revision_no != result.revision_no
        or instance.id != result.approval_instance_id
        or instance.attempt_no != result.approval_attempt_no
        or _request_axes(request) != dict(result.state_axes)
    ):
        _fail(
            "material_request_idempotency_projection_changed",
            "conflict",
            "需求单状态已变化，不能重放该命令",
        )


def _assert_result_projection(
    request: MaterialRequest,
    instance: ApprovalInstance,
    result: MaterialRequestLifecycleResult,
) -> None:
    if (
        request.id != result.request_id
        or request.version != result.request_version
        or request.status != result.request_status
        or instance.id != result.approval_instance_id
        or instance.attempt_no != result.approval_attempt_no
        or _request_axes(request) != dict(result.state_axes)
    ):
        _fail(
            "material_request_lifecycle_projection_invalid",
            "service_unavailable",
            "需求单生命周期结果投影无效",
        )


def _safe_snapshot(graph: _LockedGraph) -> dict[str, Any]:
    lines = tuple(
        {
            "line_id": str(line.id),
            "line_no": line.line_no,
            "status": line.status,
            "final_approved_qty": format(line.final_approved_qty, "f"),
            "cancelled_qty": format(line.cancelled_qty, "f"),
            "version": line.version,
        }
        for line in graph.lines
    )
    return {
        "request_id": str(graph.request.id),
        "request_no": graph.request.request_no,
        "requester_person_id": str(graph.request.requester_person_id),
        "requester_org_id": str(graph.request.requester_org_id),
        "status": graph.request.status,
        "version": graph.request.version,
        "revision_id": str(graph.revision.id),
        "revision_no": graph.revision.revision_no,
        "line_count": len(lines),
        "line_manifest_sha256": _canonical_hash(lines),
        "state_axes": dict(_request_axes(graph.request)),
        "sensitive_fields": "excluded",
    }


def _request_axes(request: MaterialRequest) -> dict[str, str]:
    return {key: getattr(request, key) for key in _NEUTRAL_AXES}


def _exact_axes(value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(_NEUTRAL_AXES):
        raise ValueError
    axes = {
        key: _strict_string(value, key)
        for key in _NEUTRAL_AXES
    }
    if axes != _NEUTRAL_AXES:
        raise ValueError
    return axes


def _require_uuid(field_name: str, value: object) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail(
            f"material_request_{field_name}_invalid",
            "invalid_request",
            f"{field_name} 无效",
        )
    return value


def _require_version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail("material_request_expected_version_invalid", "invalid_request", "expected_version 无效")
    return value


def _require_quantity(value: object) -> Decimal:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= 0
        or value >= _MAX_QUANTITY
        or value.as_tuple().exponent < -3
    ):
        _fail("material_request_cancelled_qty_invalid", "invalid_request", "取消数量无效")
    return value.quantize(Decimal("0.001"))


def _require_text(
    field_name: str, value: object, maximum: int, *, required: bool
) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > maximum:
        _fail(
            f"material_request_{field_name}_invalid",
            "invalid_request",
            f"{field_name} 无效",
        )
    if required and not value:
        _fail(
            f"material_request_{field_name}_required",
            "invalid_request",
            f"{field_name} 不能为空",
        )
    return value


def _require_trace_request_id(value: object) -> str:
    if not isinstance(value, str) or _TRACE_PATTERN.fullmatch(value) is None:
        _fail("material_request_trace_id_invalid", "invalid_request", "X-Request-ID 无效")
    return value


def _require_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or _IDEMPOTENCY_PATTERN.fullmatch(value) is None:
        _fail("material_request_idempotency_key_invalid", "invalid_request", "幂等键无效")
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    if isinstance(value, str):
        encoded = value.strip().encode("utf-8")
    elif isinstance(value, bytes):
        encoded = value
    else:
        encoded = b""
    lowered = encoded.lower()
    if len(encoded) < 32 or any(marker.encode() in lowered for marker in _PLACEHOLDERS):
        _fail(
            "material_request_idempotency_secret_invalid",
            "service_unavailable",
            "需求单幂等密钥不可用",
        )
    return encoded


def _idempotency_hmac(
    secret: bytes, actor_user_id: str, path: str, raw_key: str
) -> str:
    document = (
        "cloud_oam.material_request.idempotency.v1\0"
        f"actor={actor_user_id}\0method=POST\0path={path}\0key={raw_key}"
    ).encode("utf-8")
    return hmac.new(secret, document, hashlib.sha256).hexdigest()


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
    raw = hashlib.sha256(
        f"material-request:{namespace}:{value}".encode("utf-8")
    ).digest()[:8]
    return int.from_bytes(raw, "big", signed=True)


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(func.current_timestamp()))
    if not isinstance(value, datetime):
        _fail("material_request_database_clock_invalid", "service_unavailable", "数据库时间不可用")
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _canonical_hash(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _text_hash(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _timestamp_token(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _same_timestamp(left: datetime, right: datetime) -> bool:
    return _timestamp_token(left) == _timestamp_token(right)


def _strict_string(value: Mapping[str, Any], key: str) -> str:
    item = value[key]
    if not isinstance(item, str):
        raise ValueError
    return item


def _strict_uuid(value: Mapping[str, Any], key: str) -> uuid.UUID:
    parsed = uuid.UUID(_strict_string(value, key))
    if parsed.int == 0:
        raise ValueError
    return parsed


def _strict_nonnegative_int(value: Mapping[str, Any], key: str) -> int:
    item = value[key]
    if isinstance(item, bool) or not isinstance(item, int) or item < 0:
        raise ValueError
    return item


def _strict_positive_int(value: Mapping[str, Any], key: str) -> int:
    item = _strict_nonnegative_int(value, key)
    if item < 1:
        raise ValueError
    return item


def _same_uuid(left: str | uuid.UUID, right: str | uuid.UUID) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _fail(code: str, category: str, message: str):
    raise MaterialRequestLifecycleError(code, category, message)


__all__ = [
    "MaterialRequestCancelInput",
    "MaterialRequestCancellationLineInput",
    "MaterialRequestLifecycleError",
    "MaterialRequestLifecycleResult",
    "cancel_material_request",
    "withdraw_material_request",
]
