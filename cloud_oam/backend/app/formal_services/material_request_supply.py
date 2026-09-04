"""Final-approval-bounded shortage supply-plan commands.

This service deliberately stops at a supply *plan*.  It does not allocate or
reserve inventory, create an outbound/shipment/receipt fact, enqueue a
notification, or claim reconciliation.  Those state axes remain owned by
their later bounded services.

The caller owns the transaction.  Public commands flush their complete
command/audit projection but never commit or roll back.  PostgreSQL migration
guards remain the final fail-closed authority; the checks here provide stable
API failures and make the same invariants testable on SQLite.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re
from typing import Any, Final, Mapping
import uuid

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..demand_models import (
    ApprovalInstance,
    MaterialRequest,
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
from ..foundation_models import AuditEvent, StateTransitionEvent
from .audit_chain import (
    AuditChainError,
    append_audit_event,
    calculate_audit_event_hash,
)


MATERIAL_REQUEST_AUDIT_STREAM: Final[str] = "material_request"
MATERIAL_REQUEST_AGGREGATE: Final[str] = "material_request"
SUPPLY_TASK_AGGREGATE: Final[str] = "supply_task"

_SUPPLY_TYPES: Final[frozenset[str]] = frozenset(
    {
        "cross_region_transfer",
        "headquarters_replenishment",
        "star_replenishment",
        "external_procurement_reference",
    }
)
_ACTIVE_TASK_STATUSES: Final[frozenset[str]] = frozenset(
    {"open", "reference_registered", "awaiting_supply"}
)
_TERMINAL_TASK_STATUSES: Final[frozenset[str]] = frozenset(
    {"cancelled", "closed_no_supply"}
)
_TASK_STATUSES: Final[frozenset[str]] = (
    _ACTIVE_TASK_STATUSES | _TERMINAL_TASK_STATUSES
)
_TASK_TRANSITIONS: Final[dict[str, frozenset[str]]] = {
    "open": frozenset(
        {
            "open",
            "reference_registered",
            "awaiting_supply",
            "cancelled",
            "closed_no_supply",
        }
    ),
    "reference_registered": frozenset(
        {
            "reference_registered",
            "awaiting_supply",
            "cancelled",
            "closed_no_supply",
        }
    ),
    "awaiting_supply": frozenset(
        {"awaiting_supply", "cancelled", "closed_no_supply"}
    ),
    "cancelled": frozenset(),
    "closed_no_supply": frozenset(),
}
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
_MAX_QUANTITY: Final[Decimal] = Decimal("1000000000000000")
_TRACE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,159}$", re.ASCII
)
_IDEMPOTENCY_PATTERN = re.compile(r"^[\x21-\x7e]{16,128}$", re.ASCII)
_REFERENCE_PATTERN = re.compile(
    r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,159}$", re.ASCII
)
_PLACEHOLDERS = (
    "replace-with",
    "replace_me",
    "replace-me",
    "change-me",
    "changeme",
)
_COMMAND_SCHEMA: Final[str] = "rsc.material_request_supply_command.v1"
_RESULT_KIND: Final[str] = "supply_task"
_RESULT_SCHEMA_VERSION: Final[str] = "1.0"
_AUDIT_SCHEMA: Final[str] = "rsc.material_request_supply_audit.v1"
_STATE_SCHEMA: Final[str] = "rsc.supply_task_state_transition.v1"

_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class MaterialRequestSupplyError(RuntimeError):
    """Stable, database-detail-free failure for a supply-plan command."""

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
class SupplyTaskCreateInput:
    request_line_id: uuid.UUID
    supply_type: str
    reference_no: str | None
    expected_qty: Decimal
    expected_date: date | None
    note: str = ""


@dataclass(frozen=True, slots=True)
class SupplyTaskUpdateInput:
    status: str
    reference_no: str | None
    expected_date: date | None
    comment: str = ""


@dataclass(frozen=True, slots=True)
class SupplyTaskCommandResult:
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
    supply_task_id: uuid.UUID
    task_no: str
    task_status: str
    task_version: int
    request_line_id: uuid.UUID
    substitution_decision_id: uuid.UUID | None
    supply_type: str
    reference_no: str | None
    expected_qty: Decimal
    original_equivalent_qty: Decimal
    expected_date: date | None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _SupplyActorContext:
    principal: FormalPrincipal
    grant: ScopeGrant


@dataclass(frozen=True, slots=True)
class _LockedSupplyGraph:
    request: MaterialRequest
    revision: MaterialRequestRevision
    lines: tuple[MaterialRequestLine, ...]
    approval_instance: ApprovalInstance
    substitutions: tuple[SubstitutionDecision, ...]
    supply_tasks: tuple[SupplyTask, ...]


def create_supply_task(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_request_version: int,
    plan: SupplyTaskCreateInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> SupplyTaskCommandResult:
    """Create one original-material supply plan within approved quantity."""

    return _public_boundary(
        lambda: _create_supply_task_impl(
            db,
            actor=actor,
            material_request_id=material_request_id,
            expected_request_version=expected_request_version,
            plan=plan,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def update_supply_task(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    supply_task_id: uuid.UUID,
    expected_request_version: int,
    expected_task_version: int,
    update: SupplyTaskUpdateInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> SupplyTaskCommandResult:
    """Update or terminally cancel an existing original-material plan."""

    return _public_boundary(
        lambda: _update_supply_task_impl(
            db,
            actor=actor,
            material_request_id=material_request_id,
            supply_task_id=supply_task_id,
            expected_request_version=expected_request_version,
            expected_task_version=expected_task_version,
            update=update,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def validate_supply_task_command_result(
    command: MaterialRequestCommand,
) -> SupplyTaskCommandResult:
    """Validate one persisted supply command/result without database writes.

    This helper intentionally performs no query, lock, flush, commit or
    rollback.  Read-only command-status recovery can use it after selecting a
    candidate command, then independently verify the matching audit event and
    later task-version history.
    """

    if not isinstance(command, MaterialRequestCommand):
        _fail_replay_invalid()
    operation = command.operation
    if operation not in {
        "create_supply_task",
        "update_supply_task",
        "cancel_supply_task",
    }:
        _fail_replay_invalid()
    if (
        not isinstance(command.result_jsonb, Mapping)
        or not isinstance(command.request_jsonb, Mapping)
        or not isinstance(command.request_hash, str)
        or not isinstance(command.result_hash, str)
        or not hmac.compare_digest(
            command.result_hash,
            _canonical_hash(command.result_jsonb),
        )
    ):
        _fail_replay_invalid()
    result = _result_from_document(
        command.result_jsonb, expected_operation=operation
    )
    expected_path = (
        f"/api/v1/material-requests/{result.request_id}/supply-tasks"
        if operation == "create_supply_task"
        else (
            f"/api/v1/material-requests/{result.request_id}/supply-tasks/"
            f"{result.supply_task_id}"
        )
    )
    request_document = command.request_jsonb
    comment_sha256 = request_document.get("comment_sha256")
    if (
        not isinstance(comment_sha256, str)
        or re.fullmatch(r"[0-9a-f]{64}", comment_sha256) is None
        or command.request_id != result.request_id
        or command.target_version != result.request_version
        or command.request_reference != expected_path
        or command.actor_user_id == ""
        or command.authorization_version <= 0
        or not _same_timestamp(command.occurred_at, command.created_at)
        or request_document
        != _request_document_from_result(
            result=result,
            operation=operation,
            payload_sha256=command.request_hash,
            comment_sha256=comment_sha256,
        )
    ):
        _fail_replay_invalid()
    return result


def validate_supply_task_audit_event(
    audit: AuditEvent,
    *,
    command: MaterialRequestCommand,
    result: SupplyTaskCommandResult,
) -> None:
    """Validate the immutable audit evidence for one historical command.

    Like :func:`validate_supply_task_command_result`, this function is pure:
    it performs no database access or state mutation.  The caller must select
    the candidate event and may separately walk the full audit chain.
    """

    if not isinstance(audit, AuditEvent):
        _fail_replay_invalid()
    request_document = command.request_jsonb
    if not isinstance(request_document, Mapping):
        _fail_replay_invalid()
    comment_sha256 = request_document.get("comment_sha256")
    if not isinstance(comment_sha256, str):
        _fail_replay_invalid()
    expected_after = _audit_after_document_from_result(
        result=result,
        command=command,
        key_hash=command.idempotency_key_hash,
        comment_sha256=comment_sha256,
    )
    if (
        audit.stream_key != MATERIAL_REQUEST_AUDIT_STREAM
        or audit.action != _audit_action(command.operation)
        or audit.aggregate_type != MATERIAL_REQUEST_AGGREGATE
        or audit.aggregate_id != str(result.request_id)
        or audit.actor_user_id != command.actor_user_id
        or audit.after_jsonb != expected_after
        or not isinstance(audit.before_jsonb, Mapping)
        or audit.request_id == ""
        or not _same_timestamp(audit.occurred_at, command.occurred_at)
        or not _same_timestamp(audit.created_at, command.created_at)
    ):
        _fail_replay_invalid()
    calculated_event_hash = calculate_audit_event_hash(
        stream_key=audit.stream_key,
        event_id=audit.id,
        actor_user_id=audit.actor_user_id,
        action=audit.action,
        aggregate_type=audit.aggregate_type,
        aggregate_id=audit.aggregate_id,
        before_jsonb=audit.before_jsonb,
        after_jsonb=audit.after_jsonb,
        request_id=audit.request_id,
        previous_hash=audit.previous_hash,
        occurred_at=audit.occurred_at,
    )
    if not hmac.compare_digest(audit.event_hash or "", calculated_event_hash):
        _fail_replay_invalid()
    before = audit.before_jsonb
    if command.operation == "create_supply_task":
        if before != {} or result.task_version != 0:
            _fail_replay_invalid()
        return
    if (
        set(before)
        != {
            "schema",
            "request_id",
            "supply_task_id",
            "task_no",
            "task_status",
            "task_version",
            "reference_no",
            "expected_date",
            "sensitive_fields",
        }
        or before.get("schema") != _AUDIT_SCHEMA
        or before.get("request_id") != str(result.request_id)
        or before.get("supply_task_id") != str(result.supply_task_id)
        or before.get("task_no") != result.task_no
        or before.get("sensitive_fields") != "excluded"
        or isinstance(before.get("task_version"), bool)
        or not isinstance(before.get("task_version"), int)
        or before.get("task_version") + 1 != result.task_version
    ):
        _fail_replay_invalid()


def _public_boundary(operation):
    try:
        return operation()
    except MaterialRequestSupplyError:
        raise
    except FormalAccessError:
        raise MaterialRequestSupplyError(
            "material_request_supply_actor_not_current",
            "forbidden",
            "正式供给权限上下文已失效，请重新读取后再操作",
        ) from None
    except AuditChainError:
        raise MaterialRequestSupplyError(
            "material_request_supply_audit_unavailable",
            "service_unavailable",
            "供给任务审计链不可用，本次操作未完成",
        ) from None
    except IntegrityError:
        raise MaterialRequestSupplyError(
            "material_request_supply_concurrent_conflict",
            "conflict",
            "供给任务发生并发冲突，请回滚并重新读取后再操作",
        ) from None
    except DBAPIError:
        raise MaterialRequestSupplyError(
            "material_request_supply_database_unavailable",
            "service_unavailable",
            "数据库暂时不可用，本次供给任务操作未完成",
        ) from None


def _create_supply_task_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    expected_request_version: int,
    plan: SupplyTaskCreateInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> SupplyTaskCommandResult:
    supplied = _validate_supplied_actor(actor)
    request_id = _require_uuid("material_request_id", material_request_id)
    request_version = _require_version(
        "expected_request_version", expected_request_version
    )
    prepared = _validate_create_input(plan)
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    path = f"/api/v1/material-requests/{request_id}/supply-tasks"
    key_hash = _idempotency_hmac(
        secret, supplied.user_id, "POST", path, raw_key
    )
    payload_hash = _canonical_hash(
        {
            "operation": "create_supply_task",
            "request_id": str(request_id),
            "expected_request_version": request_version,
            "request_line_id": str(prepared.request_line_id),
            "supply_type": prepared.supply_type,
            "reference_no": prepared.reference_no,
            "expected_qty": _quantity_token(prepared.expected_qty),
            "expected_date": _date_token(prepared.expected_date),
            "note": prepared.note,
            "actor_user_id": supplied.user_id,
            "actor_person_id": str(supplied.person_id),
            "authorization_version": supplied.authorization_version,
        }
    )

    _take_advisory_locks(db, key_hash, request_id)
    request = _lock_request(db, request_id)
    lock_formal_principal_graph(db, (supplied.user_id,))
    now = _database_now(db)
    actor_context = _require_supply_actor_context(
        db, supplied, request=request, now=now
    )
    graph = _lock_supply_graph(db, request=request)
    replay = _load_replay(
        db,
        graph=graph,
        actor=actor_context,
        key_hash=key_hash,
        request_hash=payload_hash,
        operation="create_supply_task",
        request_reference=path,
        comment_sha256=_text_hash(prepared.note),
        expected_create=prepared,
        expected_update=None,
    )
    if replay is not None:
        return replace(replay, replayed=True)

    _require_exact_request_version(graph.request, request_version)
    _require_final_approval_graph(graph)
    line = _require_current_approved_line(graph, prepared.request_line_id)
    _require_no_active_substitution(graph, line.id)
    _require_original_quantity_capacity(
        line, graph.supply_tasks, prepared.expected_qty
    )

    task_id = uuid.uuid4()
    task_no = _task_number(now, task_id)
    initial_status = (
        "reference_registered" if prepared.reference_no is not None else "open"
    )
    task = SupplyTask(
        id=task_id,
        task_no=task_no,
        request_line_id=line.id,
        substitution_decision_id=None,
        supply_type=prepared.supply_type,
        reference_no=prepared.reference_no,
        expected_qty=prepared.expected_qty,
        original_equivalent_qty=prepared.expected_qty,
        expected_date=prepared.expected_date,
        status=initial_status,
        created_by_user_id=actor_context.principal.user_id,
        created_by_person_id=actor_context.principal.person_id,
        created_role_assignment_id=actor_context.grant.assignment_id,
        authorization_version=actor_context.principal.authorization_version,
        cancelled_by_user_id=None,
        cancelled_at=None,
        version=0,
        created_at=now,
        updated_at=now,
    )
    target_request_version = graph.request.version + 1
    result = _result(
        graph,
        task=task,
        action="create_supply_task",
        request_version=target_request_version,
    )
    command = _command_fact(
        graph=graph,
        task=task,
        result=result,
        actor=actor_context,
        operation="create_supply_task",
        key_hash=key_hash,
        request_reference=path,
        request_hash=payload_hash,
        comment_sha256=_text_hash(prepared.note),
        occurred_at=now,
    )
    before: dict[str, Any] = {}

    db.add(command)
    db.flush()
    db.add(task)
    db.flush()
    _advance_request_projection(
        graph.request, target_version=target_request_version, now=now
    )
    db.flush()
    db.add(
        _state_event(
            graph=graph,
            task=task,
            command=command,
            key_hash=key_hash,
            from_status=None,
            occurred_at=now,
        )
    )
    db.flush()
    _append_supply_audit(
        db,
        graph=graph,
        task=task,
        command=command,
        operation="create_supply_task",
        key_hash=key_hash,
        comment_sha256=_text_hash(prepared.note),
        before=before,
        trace_request_id=trace_id,
        occurred_at=now,
    )
    _assert_live_projection(graph, task, result)
    db.flush()
    return result


def _update_supply_task_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    material_request_id: uuid.UUID,
    supply_task_id: uuid.UUID,
    expected_request_version: int,
    expected_task_version: int,
    update: SupplyTaskUpdateInput,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> SupplyTaskCommandResult:
    supplied = _validate_supplied_actor(actor)
    request_id = _require_uuid("material_request_id", material_request_id)
    task_id = _require_uuid("supply_task_id", supply_task_id)
    request_version = _require_version(
        "expected_request_version", expected_request_version
    )
    task_version = _require_version("expected_task_version", expected_task_version)
    prepared = _validate_update_input(update)
    operation = (
        "cancel_supply_task"
        if prepared.status == "cancelled"
        else "update_supply_task"
    )
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    path = (
        f"/api/v1/material-requests/{request_id}/supply-tasks/{task_id}"
    )
    key_hash = _idempotency_hmac(
        secret, supplied.user_id, "POST", path, raw_key
    )
    payload_hash = _canonical_hash(
        {
            "operation": operation,
            "request_id": str(request_id),
            "supply_task_id": str(task_id),
            "expected_request_version": request_version,
            "expected_task_version": task_version,
            "status": prepared.status,
            "reference_no": prepared.reference_no,
            "expected_date": _date_token(prepared.expected_date),
            "comment": prepared.comment,
            "actor_user_id": supplied.user_id,
            "actor_person_id": str(supplied.person_id),
            "authorization_version": supplied.authorization_version,
        }
    )

    _take_advisory_locks(db, key_hash, request_id)
    request = _lock_request(db, request_id)
    lock_formal_principal_graph(db, (supplied.user_id,))
    now = _database_now(db)
    actor_context = _require_supply_actor_context(
        db, supplied, request=request, now=now
    )
    graph = _lock_supply_graph(db, request=request)
    replay = _load_replay(
        db,
        graph=graph,
        actor=actor_context,
        key_hash=key_hash,
        request_hash=payload_hash,
        operation=operation,
        request_reference=path,
        comment_sha256=_text_hash(prepared.comment),
        expected_create=None,
        expected_update=prepared,
        expected_task_id=task_id,
    )
    if replay is not None:
        return replace(replay, replayed=True)

    _require_exact_request_version(graph.request, request_version)
    _require_final_approval_graph(graph)
    task = _require_current_task(db, graph, task_id)
    _require_original_task_integrity(task)
    if task.version != task_version:
        _fail(
            "supply_task_version_conflict",
            "conflict",
            "供给任务版本已变化，请重新读取后再操作",
        )
    if task.status in _TERMINAL_TASK_STATUSES:
        _fail(
            "supply_task_terminal",
            "conflict",
            "供给任务已终结，不能重新打开或修改",
        )
    if prepared.status not in _TASK_TRANSITIONS.get(task.status, frozenset()):
        _fail(
            "supply_task_status_transition_invalid",
            "conflict",
            "供给任务状态迁移无效",
        )
    if operation == "cancel_supply_task" and (
        prepared.reference_no != task.reference_no
        or prepared.expected_date != task.expected_date
    ):
        _fail(
            "supply_task_cancel_metadata_changed",
            "conflict",
            "取消供给任务不得同时修改参考号或预计日期",
        )
    line = _require_current_approved_line(graph, task.request_line_id)
    if prepared.status in _ACTIVE_TASK_STATUSES:
        _require_no_active_substitution(graph, line.id)
    _require_existing_capacity(line, graph.supply_tasks)

    before_status = task.status
    before = _audit_before_document(graph, task)
    target_request_version = graph.request.version + 1
    target_task_version = task.version + 1
    result = _result_for_values(
        graph,
        task=task,
        action=operation,
        request_version=target_request_version,
        task_status=prepared.status,
        task_version=target_task_version,
        reference_no=prepared.reference_no,
        expected_date=prepared.expected_date,
    )
    command = _command_fact_from_result(
        graph=graph,
        result=result,
        actor=actor_context,
        operation=operation,
        key_hash=key_hash,
        request_reference=path,
        request_hash=payload_hash,
        comment_sha256=_text_hash(prepared.comment),
        occurred_at=now,
    )

    db.add(command)
    db.flush()
    task.status = prepared.status
    task.reference_no = prepared.reference_no
    task.expected_date = prepared.expected_date
    task.version = target_task_version
    task.updated_at = now
    if task.status == "cancelled":
        task.cancelled_by_user_id = actor_context.principal.user_id
        task.cancelled_at = now
    else:
        task.cancelled_by_user_id = None
        task.cancelled_at = None
    db.flush()
    _advance_request_projection(
        graph.request, target_version=target_request_version, now=now
    )
    db.flush()
    if task.status != before_status:
        db.add(
            _state_event(
                graph=graph,
                task=task,
                command=command,
                key_hash=key_hash,
                from_status=before_status,
                occurred_at=now,
            )
        )
        db.flush()
    _append_supply_audit(
        db,
        graph=graph,
        task=task,
        command=command,
        operation=operation,
        key_hash=key_hash,
        comment_sha256=_text_hash(prepared.comment),
        before=before,
        trace_request_id=trace_id,
        occurred_at=now,
    )
    _assert_live_projection(graph, task, result)
    db.flush()
    return result


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail(
            "formal_principal_required",
            "forbidden",
            "供给任务必须使用正式权限主体",
        )
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _fail(
            "material_request_supply_actor_inactive",
            "forbidden",
            "当前账号或人员不可管理供给任务",
        )
    return actor


def _require_supply_actor_context(
    db: Session,
    supplied: FormalPrincipal,
    *,
    request: MaterialRequest,
    now: datetime,
) -> _SupplyActorContext:
    current = load_formal_principal(db, supplied.user_id, now=now)
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
        or current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail(
            "material_request_supply_actor_principal_stale",
            "precondition_failed",
            "供给权限版本已变化，请重新读取后再操作",
        )
    candidates = tuple(
        grant
        for grant in current.assignments
        if grant.role_code == "admin"
        and grant.scope_type == "national"
        and grant.scope_id == "*"
    )
    eligible = tuple(
        grant
        for grant in candidates
        if _grant_allows_supply_manage(
            db,
            current,
            grant,
            requester_org_id=request.requester_org_id,
        )
    )
    if len(eligible) != 1:
        _fail(
            "material_request_supply_manage_forbidden",
            "forbidden",
            "仅具有唯一全国范围供给管理授权的总部管理员可以操作",
        )
    return _SupplyActorContext(current, eligible[0])


def _grant_allows_supply_manage(
    db: Session,
    principal: FormalPrincipal,
    grant: ScopeGrant,
    *,
    requester_org_id: uuid.UUID,
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
            "supply_task",
            "manage",
            target_scope_type="organization",
            target_scope_id=str(requester_org_id),
        ) and selected.allows(
            db,
            "supply_task",
            "manage",
            target_scope_type="organization",
            target_scope_id=str(requester_org_id),
        )
    except FormalAccessError:
        _fail(
            "material_request_supply_scope_graph_invalid",
            "forbidden",
            "供给权限范围图无效",
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


def _lock_supply_graph(
    db: Session, *, request: MaterialRequest
) -> _LockedSupplyGraph:
    revisions = tuple(
        db.scalars(
            select(MaterialRequestRevision)
            .where(
                MaterialRequestRevision.request_id == request.id,
                MaterialRequestRevision.revision_no == request.revision_no,
            )
            .order_by(MaterialRequestRevision.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(revisions) != 1:
        _fail(
            "material_request_supply_revision_invalid",
            "precondition_failed",
            "需求单当前版本无法唯一核验",
        )
    revision = revisions[0]
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
            .where(ApprovalInstance.request_id == request.id)
            .order_by(ApprovalInstance.attempt_no, ApprovalInstance.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if not instances:
        _fail(
            "material_request_supply_approval_missing",
            "precondition_failed",
            "需求单缺少可核验的最终审批实例",
        )
    line_ids = tuple(line.id for line in lines)
    # The parent request is already locked. Every substitution INSERT/UPDATE
    # takes that same parent lock through the ALWAYS 0037 trigger. Read the
    # stable decision set without requiring UPDATE authority on this table.
    substitutions = (
        tuple(
            db.scalars(
                select(SubstitutionDecision)
                .where(SubstitutionDecision.request_line_id.in_(line_ids))
                .order_by(
                    SubstitutionDecision.request_line_id,
                    SubstitutionDecision.id,
                )
                .execution_options(populate_existing=True)
            ).all()
        )
        if line_ids
        else ()
    )
    tasks = (
        tuple(
            db.scalars(
                select(SupplyTask)
                .where(SupplyTask.request_line_id.in_(line_ids))
                .order_by(SupplyTask.request_line_id, SupplyTask.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        if line_ids
        else ()
    )
    return _LockedSupplyGraph(
        request=request,
        revision=revision,
        lines=lines,
        approval_instance=instances[-1],
        substitutions=substitutions,
        supply_tasks=tasks,
    )


def _require_final_approval_graph(graph: _LockedSupplyGraph) -> None:
    request = graph.request
    revision = graph.revision
    instance = graph.approval_instance
    if request.status not in {"approved", "partially_approved"}:
        _fail(
            "material_request_supply_final_approval_required",
            "precondition_failed",
            "只有已完成最终批准的需求单可以建立供给任务",
        )
    if (
        revision.request_id != request.id
        or revision.revision_no != request.revision_no
        or revision.status != "sealed"
        or not graph.lines
    ):
        _fail(
            "material_request_supply_revision_invalid",
            "precondition_failed",
            "需求单当前封存版本无效",
        )
    if (
        instance.request_id != request.id
        or instance.request_revision_id != revision.id
        or instance.revision_no != revision.revision_no
        or instance.status != "completed"
        or instance.current_step_id is not None
        or instance.current_step_no is not None
        or instance.completed_at is None
        or request.decided_at is None
        or not _same_timestamp(instance.completed_at, request.decided_at)
    ):
        _fail(
            "material_request_supply_approval_invalid",
            "precondition_failed",
            "需求单最终审批因果锚点无效",
        )
    axes = _fulfillment_axes(request)
    if axes != _NEUTRAL_AXES:
        _fail(
            "material_request_supply_fulfillment_started",
            "precondition_failed",
            "需求单已进入其他履约状态，不能由供给计划命令改写",
        )


def _require_current_approved_line(
    graph: _LockedSupplyGraph, request_line_id: uuid.UUID
) -> MaterialRequestLine:
    matches = tuple(line for line in graph.lines if line.id == request_line_id)
    if len(matches) != 1:
        _fail(
            "material_request_supply_line_not_current",
            "conflict",
            "供给任务必须绑定需求单当前版本明细",
        )
    line = matches[0]
    if (
        line.status not in {"approved", "partially_approved"}
        or line.final_approved_qty <= Decimal("0.000")
        or line.cancelled_qty < Decimal("0.000")
        or line.final_approved_qty - line.cancelled_qty <= Decimal("0.000")
    ):
        _fail(
            "material_request_supply_line_not_approved",
            "precondition_failed",
            "当前明细没有可供给的最终批准数量",
        )
    return line


def _require_current_task(
    db: Session,
    graph: _LockedSupplyGraph,
    task_id: uuid.UUID,
) -> SupplyTask:
    matches = tuple(task for task in graph.supply_tasks if task.id == task_id)
    if len(matches) == 1:
        return matches[0]
    owner_request_id = db.scalar(
        select(MaterialRequestLine.request_id)
        .join(SupplyTask, SupplyTask.request_line_id == MaterialRequestLine.id)
        .where(SupplyTask.id == task_id)
        .execution_options(populate_existing=True)
    )
    if owner_request_id == graph.request.id:
        _fail(
            "supply_task_not_current_revision",
            "conflict",
            "供给任务不属于需求单当前版本",
        )
    _fail("supply_task_not_found", "not_found", "供给任务不存在")


def _require_no_active_substitution(
    graph: _LockedSupplyGraph, request_line_id: uuid.UUID
) -> None:
    if any(
        row.request_line_id == request_line_id
        and row.status in {"proposed", "confirmed"}
        for row in graph.substitutions
    ):
        _fail(
            "material_request_supply_active_substitution_exists",
            "precondition_failed",
            "当前明细已有活动替代料决定，不能建立或推进原料供给计划",
        )


def _require_original_task_integrity(task: SupplyTask) -> None:
    if (
        task.substitution_decision_id is not None
        or task.supply_type not in _SUPPLY_TYPES
        or task.status not in _TASK_STATUSES
        or task.expected_qty <= Decimal("0.000")
        or task.original_equivalent_qty <= Decimal("0.000")
        or task.expected_qty != task.original_equivalent_qty
        or task.version < 0
        or task.authorization_version <= 0
    ):
        _fail(
            "supply_task_projection_invalid",
            "service_unavailable",
            "供给任务原料计划投影无效",
        )
    if task.status == "cancelled":
        cancellation_invalid = (
            task.cancelled_by_user_id is None or task.cancelled_at is None
        )
    else:
        cancellation_invalid = (
            task.cancelled_by_user_id is not None or task.cancelled_at is not None
        )
    if cancellation_invalid:
        _fail(
            "supply_task_projection_invalid",
            "service_unavailable",
            "供给任务取消投影无效",
        )


def _require_original_quantity_capacity(
    line: MaterialRequestLine,
    tasks: tuple[SupplyTask, ...],
    new_quantity: Decimal,
) -> None:
    for task in tasks:
        _require_capacity_task_integrity(task)
    active_total = sum(
        (
            task.original_equivalent_qty
            for task in tasks
            if task.request_line_id == line.id
            and task.status in _ACTIVE_TASK_STATUSES
        ),
        Decimal("0.000"),
    )
    remaining = line.final_approved_qty - line.cancelled_qty
    if active_total < Decimal("0.000") or active_total + new_quantity > remaining:
        _fail(
            "material_request_supply_quantity_exceeds_approved",
            "conflict",
            "活动供给计划数量不得超过当前未取消的最终批准数量",
        )


def _require_existing_capacity(
    line: MaterialRequestLine, tasks: tuple[SupplyTask, ...]
) -> None:
    for task in tasks:
        _require_capacity_task_integrity(task)
    active_total = sum(
        (
            task.original_equivalent_qty
            for task in tasks
            if task.request_line_id == line.id
            and task.status in _ACTIVE_TASK_STATUSES
        ),
        Decimal("0.000"),
    )
    if active_total > line.final_approved_qty - line.cancelled_qty:
        _fail(
            "material_request_supply_quantity_projection_invalid",
            "service_unavailable",
            "活动供给计划数量投影超过最终批准余量",
        )


def _require_capacity_task_integrity(task: SupplyTask) -> None:
    if (
        task.supply_type not in _SUPPLY_TYPES
        or task.status not in _TASK_STATUSES
        or task.expected_qty <= Decimal("0.000")
        or task.original_equivalent_qty <= Decimal("0.000")
        or task.version < 0
        or task.authorization_version <= 0
    ):
        _fail(
            "supply_task_quantity_projection_invalid",
            "service_unavailable",
            "供给任务数量投影无效",
        )


def _require_exact_request_version(
    request: MaterialRequest, expected_version: int
) -> None:
    if request.version != expected_version:
        _fail(
            "material_request_version_conflict",
            "conflict",
            "需求单版本已变化，请重新读取后再操作",
        )


def _result(
    graph: _LockedSupplyGraph,
    *,
    task: SupplyTask,
    action: str,
    request_version: int,
) -> SupplyTaskCommandResult:
    return _result_for_values(
        graph,
        task=task,
        action=action,
        request_version=request_version,
        task_status=task.status,
        task_version=task.version,
        reference_no=task.reference_no,
        expected_date=task.expected_date,
    )


def _result_for_values(
    graph: _LockedSupplyGraph,
    *,
    task: SupplyTask,
    action: str,
    request_version: int,
    task_status: str,
    task_version: int,
    reference_no: str | None,
    expected_date: date | None,
) -> SupplyTaskCommandResult:
    return SupplyTaskCommandResult(
        request_id=graph.request.id,
        request_no=graph.request.request_no,
        action=action,
        request_status=graph.request.status,
        request_version=request_version,
        revision_id=graph.revision.id,
        revision_no=graph.revision.revision_no,
        approval_instance_id=graph.approval_instance.id,
        approval_attempt_no=graph.approval_instance.attempt_no,
        current_step_id=None,
        state_axes={
            "request_status": graph.request.status,
            **_fulfillment_axes(graph.request),
        },
        supply_task_id=task.id,
        task_no=task.task_no,
        task_status=task_status,
        task_version=task_version,
        request_line_id=task.request_line_id,
        substitution_decision_id=task.substitution_decision_id,
        supply_type=task.supply_type,
        reference_no=reference_no,
        expected_qty=_quantity(task.expected_qty),
        original_equivalent_qty=_quantity(task.original_equivalent_qty),
        expected_date=expected_date,
    )


def _command_fact(
    *,
    graph: _LockedSupplyGraph,
    task: SupplyTask,
    result: SupplyTaskCommandResult,
    actor: _SupplyActorContext,
    operation: str,
    key_hash: str,
    request_reference: str,
    request_hash: str,
    comment_sha256: str,
    occurred_at: datetime,
) -> MaterialRequestCommand:
    if task.id != result.supply_task_id:
        _fail(
            "supply_task_command_projection_invalid",
            "service_unavailable",
            "供给任务命令绑定无效",
        )
    return _command_fact_from_result(
        graph=graph,
        result=result,
        actor=actor,
        operation=operation,
        key_hash=key_hash,
        request_reference=request_reference,
        request_hash=request_hash,
        comment_sha256=comment_sha256,
        occurred_at=occurred_at,
    )


def _command_fact_from_result(
    *,
    graph: _LockedSupplyGraph,
    result: SupplyTaskCommandResult,
    actor: _SupplyActorContext,
    operation: str,
    key_hash: str,
    request_reference: str,
    request_hash: str,
    comment_sha256: str,
    occurred_at: datetime,
) -> MaterialRequestCommand:
    result_document = _result_document(result)
    return MaterialRequestCommand(
        id=uuid.uuid4(),
        operation=operation,
        request_id=graph.request.id,
        target_version=result.request_version,
        idempotency_key_hash=key_hash,
        request_reference=request_reference,
        request_hash=request_hash,
        result_hash=_canonical_hash(result_document),
        request_jsonb=_request_document(
            graph=graph,
            result=result,
            operation=operation,
            payload_sha256=request_hash,
            comment_sha256=comment_sha256,
        ),
        result_jsonb=result_document,
        actor_user_id=actor.principal.user_id,
        actor_person_id=actor.principal.person_id,
        actor_role_assignment_id=actor.grant.assignment_id,
        authorization_version=actor.principal.authorization_version,
        occurred_at=occurred_at,
        created_at=occurred_at,
    )


def _request_document(
    *,
    graph: _LockedSupplyGraph,
    result: SupplyTaskCommandResult,
    operation: str,
    payload_sha256: str,
    comment_sha256: str,
) -> dict[str, Any]:
    if (
        graph.request.id != result.request_id
        or graph.revision.id != result.revision_id
    ):
        _fail_replay_invalid()
    return _request_document_from_result(
        result=result,
        operation=operation,
        payload_sha256=payload_sha256,
        comment_sha256=comment_sha256,
    )


def _request_document_from_result(
    *,
    result: SupplyTaskCommandResult,
    operation: str,
    payload_sha256: str,
    comment_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": _COMMAND_SCHEMA,
        "operation": operation,
        "request_id": str(result.request_id),
        "revision_id": str(result.revision_id),
        "revision_no": result.revision_no,
        "request_line_id": str(result.request_line_id),
        "supply_task_id": str(result.supply_task_id),
        "task_no": result.task_no,
        "target_version": result.request_version,
        "target_task_version": result.task_version,
        "payload_sha256": payload_sha256,
        "comment_sha256": comment_sha256,
        "sensitive_fields": "excluded",
    }


def _result_document(result: SupplyTaskCommandResult) -> dict[str, Any]:
    state_axes = dict(result.state_axes)
    state_axes.pop("request_status", None)
    return {
        "kind": _RESULT_KIND,
        "schema_version": _RESULT_SCHEMA_VERSION,
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
        "state_axes": state_axes,
        "supply_task_id": str(result.supply_task_id),
        "task_no": result.task_no,
        "task_status": result.task_status,
        "task_version": result.task_version,
        "request_line_id": str(result.request_line_id),
        "substitution_decision_id": None,
        "supply_type": result.supply_type,
        "reference_no": result.reference_no,
        "expected_qty": _quantity_token(result.expected_qty),
        "original_equivalent_qty": _quantity_token(
            result.original_equivalent_qty
        ),
        "expected_date": _date_token(result.expected_date),
    }


def _load_replay(
    db: Session,
    *,
    graph: _LockedSupplyGraph,
    actor: _SupplyActorContext,
    key_hash: str,
    request_hash: str,
    operation: str,
    request_reference: str,
    comment_sha256: str,
    expected_create: SupplyTaskCreateInput | None,
    expected_update: SupplyTaskUpdateInput | None,
    expected_task_id: uuid.UUID | None = None,
) -> SupplyTaskCommandResult | None:
    command = db.scalar(
        select(MaterialRequestCommand)
        .where(MaterialRequestCommand.idempotency_key_hash == key_hash)
        .execution_options(populate_existing=True)
    )
    if command is None:
        return None
    if command.operation != operation or command.request_hash != request_hash:
        _fail(
            "material_request_supply_idempotency_conflict",
            "conflict",
            "幂等键已用于不同的供给任务命令",
        )
    if (
        command.request_id != graph.request.id
        or command.request_reference != request_reference
    ):
        _fail_replay_invalid()
    if (
        command.actor_user_id != actor.principal.user_id
        or command.actor_person_id != actor.principal.person_id
        or command.actor_role_assignment_id != actor.grant.assignment_id
        or command.authorization_version
        != actor.principal.authorization_version
    ):
        _fail(
            "material_request_supply_idempotency_actor_mismatch",
            "forbidden",
            "供给任务幂等记录不属于当前有效总部管理员",
        )
    result = validate_supply_task_command_result(command)
    if command.request_jsonb.get("comment_sha256") != comment_sha256:
        _fail_replay_invalid()
    if (
        (expected_task_id is not None
            and result.supply_task_id != expected_task_id)
    ):
        _fail_replay_invalid()
    task = _task_from_graph(graph, result.supply_task_id)
    _assert_live_projection(graph, task, result)
    _assert_expected_replay_target(
        task,
        result,
        expected_create=expected_create,
        expected_update=expected_update,
    )
    _require_replay_evidence(
        db,
        graph=graph,
        task=task,
        command=command,
        key_hash=key_hash,
        operation=operation,
        result=result,
    )
    return result


def _result_from_document(
    value: Mapping[str, Any], *, expected_operation: str
) -> SupplyTaskCommandResult:
    expected_keys = {
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
        "supply_task_id",
        "task_no",
        "task_status",
        "task_version",
        "request_line_id",
        "substitution_decision_id",
        "supply_type",
        "reference_no",
        "expected_qty",
        "original_equivalent_qty",
        "expected_date",
    }
    try:
        if set(value) != expected_keys:
            raise ValueError
        if (
            value.get("kind") != _RESULT_KIND
            or value.get("schema_version") != _RESULT_SCHEMA_VERSION
            or value.get("action") != expected_operation
            or value.get("request_status")
            not in {"approved", "partially_approved"}
            or value.get("current_step_id") is not None
            or value.get("substitution_decision_id") is not None
        ):
            raise ValueError
        task_status = _strict_string(value, "task_status")
        if task_status not in _TASK_STATUSES:
            raise ValueError
        if (expected_operation == "cancel_supply_task") != (
            task_status == "cancelled"
        ):
            raise ValueError
        supply_type = _strict_string(value, "supply_type")
        if supply_type not in _SUPPLY_TYPES:
            raise ValueError
        reference_no = _strict_optional_reference(value, "reference_no")
        if task_status == "reference_registered" and reference_no is None:
            raise ValueError
        request_status = _strict_string(value, "request_status")
        axes = _strict_fulfillment_axes(value.get("state_axes"))
        expected_qty = _strict_quantity_token(value, "expected_qty")
        original_qty = _strict_quantity_token(
            value, "original_equivalent_qty"
        )
        if expected_qty != original_qty:
            raise ValueError
        return SupplyTaskCommandResult(
            request_id=_strict_uuid(value, "request_id"),
            request_no=_strict_string(value, "request_no"),
            action=expected_operation,
            request_status=request_status,
            request_version=_strict_nonnegative_int(
                value, "request_version"
            ),
            revision_id=_strict_uuid(value, "revision_id"),
            revision_no=_strict_positive_int(value, "revision_no"),
            approval_instance_id=_strict_uuid(
                value, "approval_instance_id"
            ),
            approval_attempt_no=_strict_positive_int(
                value, "approval_attempt_no"
            ),
            current_step_id=None,
            state_axes={"request_status": request_status, **axes},
            supply_task_id=_strict_uuid(value, "supply_task_id"),
            task_no=_strict_string(value, "task_no"),
            task_status=task_status,
            task_version=_strict_nonnegative_int(value, "task_version"),
            request_line_id=_strict_uuid(value, "request_line_id"),
            substitution_decision_id=None,
            supply_type=supply_type,
            reference_no=reference_no,
            expected_qty=expected_qty,
            original_equivalent_qty=original_qty,
            expected_date=_strict_date_token(value, "expected_date"),
        )
    except (InvalidOperation, KeyError, TypeError, ValueError):
        _fail_replay_invalid()


def _assert_expected_replay_target(
    task: SupplyTask,
    result: SupplyTaskCommandResult,
    *,
    expected_create: SupplyTaskCreateInput | None,
    expected_update: SupplyTaskUpdateInput | None,
) -> None:
    if expected_create is not None:
        expected_status = (
            "reference_registered"
            if expected_create.reference_no is not None
            else "open"
        )
        valid = (
            result.action == "create_supply_task"
            and task.request_line_id == expected_create.request_line_id
            and task.supply_type == expected_create.supply_type
            and task.reference_no == expected_create.reference_no
            and task.expected_qty == expected_create.expected_qty
            and task.original_equivalent_qty == expected_create.expected_qty
            and task.expected_date == expected_create.expected_date
            and task.status == expected_status
            and task.version == 0
        )
    elif expected_update is not None:
        valid = (
            task.status == expected_update.status
            and task.reference_no == expected_update.reference_no
            and task.expected_date == expected_update.expected_date
            and task.version == result.task_version
        )
    else:
        valid = False
    if not valid:
        _fail(
            "material_request_supply_idempotency_projection_changed",
            "conflict",
            "供给任务投影已变化，不能重放该命令",
        )


def _assert_live_projection(
    graph: _LockedSupplyGraph,
    task: SupplyTask,
    result: SupplyTaskCommandResult,
) -> None:
    _require_final_approval_graph(graph)
    if (
        graph.request.id != result.request_id
        or graph.request.request_no != result.request_no
        or graph.request.status != result.request_status
        or graph.request.version != result.request_version
        or graph.revision.id != result.revision_id
        or graph.revision.revision_no != result.revision_no
        or graph.approval_instance.id != result.approval_instance_id
        or graph.approval_instance.attempt_no != result.approval_attempt_no
        or result.current_step_id is not None
        or dict(result.state_axes)
        != {
            "request_status": graph.request.status,
            **_fulfillment_axes(graph.request),
        }
        or task.id != result.supply_task_id
        or task.task_no != result.task_no
        or task.status != result.task_status
        or task.version != result.task_version
        or task.request_line_id != result.request_line_id
        or task.substitution_decision_id is not None
        or result.substitution_decision_id is not None
        or task.supply_type != result.supply_type
        or task.reference_no != result.reference_no
        or task.expected_qty != result.expected_qty
        or task.original_equivalent_qty != result.original_equivalent_qty
        or task.expected_date != result.expected_date
    ):
        _fail(
            "material_request_supply_projection_invalid",
            "service_unavailable",
            "供给任务命令结果与当前投影不一致",
        )


def _task_from_graph(
    graph: _LockedSupplyGraph, task_id: uuid.UUID
) -> SupplyTask:
    matches = tuple(task for task in graph.supply_tasks if task.id == task_id)
    if len(matches) != 1:
        _fail_replay_invalid()
    return matches[0]


def _advance_request_projection(
    request: MaterialRequest, *, target_version: int, now: datetime
) -> None:
    if target_version != request.version + 1:
        _fail(
            "material_request_supply_target_version_invalid",
            "service_unavailable",
            "供给命令目标版本无效",
        )
    request.version = target_version
    request.updated_at = now


def _state_event(
    *,
    graph: _LockedSupplyGraph,
    task: SupplyTask,
    command: MaterialRequestCommand,
    key_hash: str,
    from_status: str | None,
    occurred_at: datetime,
) -> StateTransitionEvent:
    if from_status is None:
        reason = "supply_task_created"
    elif task.status == "cancelled":
        reason = "supply_task_cancelled"
    else:
        reason = "supply_task_status_changed"
    return StateTransitionEvent(
        id=uuid.uuid4(),
        aggregate_type=SUPPLY_TASK_AGGREGATE,
        aggregate_id=str(task.id),
        from_status=from_status,
        to_status=task.status,
        reason=reason,
        actor_id=command.actor_user_id,
        idempotency_key=_state_event_key(key_hash, task.version),
        occurred_at=occurred_at,
        metadata_jsonb={
            "schema": _STATE_SCHEMA,
            "request_id": str(graph.request.id),
            "request_no": graph.request.request_no,
            "revision_id": str(graph.revision.id),
            "revision_no": graph.revision.revision_no,
            "request_line_id": str(task.request_line_id),
            "supply_task_id": str(task.id),
            "task_no": task.task_no,
            "request_version": command.target_version,
            "task_version": task.version,
            "command_id": str(command.id),
            "idempotency_key_hash": key_hash,
        },
        created_at=occurred_at,
    )


def _append_supply_audit(
    db: Session,
    *,
    graph: _LockedSupplyGraph,
    task: SupplyTask,
    command: MaterialRequestCommand,
    operation: str,
    key_hash: str,
    comment_sha256: str,
    before: dict[str, Any],
    trace_request_id: str,
    occurred_at: datetime,
) -> AuditEvent:
    after = _audit_after_document(
        graph=graph,
        task=task,
        command=command,
        operation=operation,
        key_hash=key_hash,
        comment_sha256=comment_sha256,
    )
    return append_audit_event(
        db,
        stream_key=MATERIAL_REQUEST_AUDIT_STREAM,
        actor_user_id=command.actor_user_id,
        action=_audit_action(operation),
        aggregate_type=MATERIAL_REQUEST_AGGREGATE,
        aggregate_id=str(graph.request.id),
        before_jsonb=before,
        after_jsonb=after,
        request_id=trace_request_id,
        occurred_at=occurred_at,
        created_at=occurred_at,
    )


def _audit_before_document(
    graph: _LockedSupplyGraph,
    task: SupplyTask,
) -> dict[str, Any]:
    return {
        "schema": _AUDIT_SCHEMA,
        "request_id": str(graph.request.id),
        "supply_task_id": str(task.id),
        "task_no": task.task_no,
        "task_status": task.status,
        "task_version": task.version,
        "reference_no": task.reference_no,
        "expected_date": _date_token(task.expected_date),
        "sensitive_fields": "excluded",
    }


def _audit_after_document(
    *,
    graph: _LockedSupplyGraph,
    task: SupplyTask,
    command: MaterialRequestCommand,
    operation: str,
    key_hash: str,
    comment_sha256: str,
) -> dict[str, Any]:
    result = _result(
        graph,
        task=task,
        action=operation,
        request_version=command.target_version,
    )
    return _audit_after_document_from_result(
        result=result,
        command=command,
        key_hash=key_hash,
        comment_sha256=comment_sha256,
    )


def _audit_after_document_from_result(
    *,
    result: SupplyTaskCommandResult,
    command: MaterialRequestCommand,
    key_hash: str,
    comment_sha256: str,
) -> dict[str, Any]:
    return {
        "schema": _AUDIT_SCHEMA,
        "operation": result.action,
        "command_id": str(command.id),
        "idempotency_key_hash": key_hash,
        "comment_sha256": comment_sha256,
        "request_id": str(result.request_id),
        "request_no": result.request_no,
        "request_status": result.request_status,
        "request_version": command.target_version,
        "revision_id": str(result.revision_id),
        "revision_no": result.revision_no,
        "approval_instance_id": str(result.approval_instance_id),
        "approval_attempt_no": result.approval_attempt_no,
        "state_axes": {
            key: value
            for key, value in result.state_axes.items()
            if key != "request_status"
        },
        "supply_task_id": str(result.supply_task_id),
        "task_no": result.task_no,
        "task_status": result.task_status,
        "task_version": result.task_version,
        "request_line_id": str(result.request_line_id),
        "substitution_decision_id": (
            str(result.substitution_decision_id)
            if result.substitution_decision_id is not None
            else None
        ),
        "supply_type": result.supply_type,
        "reference_no": result.reference_no,
        "expected_qty": _quantity_token(result.expected_qty),
        "original_equivalent_qty": _quantity_token(
            result.original_equivalent_qty
        ),
        "expected_date": _date_token(result.expected_date),
        "sensitive_fields": "excluded",
    }


def _require_replay_evidence(
    db: Session,
    *,
    graph: _LockedSupplyGraph,
    task: SupplyTask,
    command: MaterialRequestCommand,
    key_hash: str,
    operation: str,
    result: SupplyTaskCommandResult,
) -> None:
    audit_candidates = tuple(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.stream_key == MATERIAL_REQUEST_AUDIT_STREAM,
                AuditEvent.action == _audit_action(operation),
                AuditEvent.aggregate_type == MATERIAL_REQUEST_AGGREGATE,
                AuditEvent.aggregate_id == str(graph.request.id),
                AuditEvent.actor_user_id == command.actor_user_id,
            )
            .order_by(AuditEvent.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    audit_rows = tuple(
        row
        for row in audit_candidates
        if isinstance(row.after_jsonb, Mapping)
        and row.after_jsonb.get("command_id") == str(command.id)
    )
    if len(audit_rows) != 1:
        _fail_replay_invalid()
    audit = audit_rows[0]
    validate_supply_task_audit_event(
        audit,
        command=command,
        result=result,
    )
    before = audit.before_jsonb
    if operation == "create_supply_task":
        expected_from_status = None
        requires_event = True
        if before != {} or task.version != 0:
            _fail_replay_invalid()
    else:
        if (
            set(before)
            != {
                "schema",
                "request_id",
                "supply_task_id",
                "task_no",
                "task_status",
                "task_version",
                "reference_no",
                "expected_date",
                "sensitive_fields",
            }
            or before.get("schema") != _AUDIT_SCHEMA
            or before.get("request_id") != str(graph.request.id)
            or before.get("supply_task_id") != str(task.id)
            or before.get("task_no") != task.task_no
            or before.get("sensitive_fields") != "excluded"
        ):
            _fail_replay_invalid()
        expected_from_status = before.get("task_status")
        before_version = before.get("task_version")
        if (
            not isinstance(expected_from_status, str)
            or isinstance(before_version, bool)
            or not isinstance(before_version, int)
            or before_version + 1 != task.version
        ):
            _fail_replay_invalid()
        requires_event = expected_from_status != task.status
    event_rows = tuple(
        db.scalars(
            select(StateTransitionEvent)
            .where(
                StateTransitionEvent.idempotency_key
                == _state_event_key(key_hash, task.version)
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if not requires_event:
        if event_rows:
            _fail_replay_invalid()
        return
    if len(event_rows) != 1:
        _fail_replay_invalid()
    event = event_rows[0]
    if (
        event.aggregate_type != SUPPLY_TASK_AGGREGATE
        or event.aggregate_id != str(task.id)
        or event.from_status != expected_from_status
        or event.to_status != task.status
        or event.actor_id != command.actor_user_id
        or not _same_timestamp(event.occurred_at, command.occurred_at)
        or not _same_timestamp(event.created_at, command.created_at)
        or event.metadata_jsonb
        != {
            "schema": _STATE_SCHEMA,
            "request_id": str(graph.request.id),
            "request_no": graph.request.request_no,
            "revision_id": str(graph.revision.id),
            "revision_no": graph.revision.revision_no,
            "request_line_id": str(task.request_line_id),
            "supply_task_id": str(task.id),
            "task_no": task.task_no,
            "request_version": command.target_version,
            "task_version": task.version,
            "command_id": str(command.id),
            "idempotency_key_hash": key_hash,
        }
    ):
        _fail_replay_invalid()


def _validate_create_input(value: SupplyTaskCreateInput) -> SupplyTaskCreateInput:
    if not isinstance(value, SupplyTaskCreateInput):
        _fail(
            "supply_task_create_input_invalid",
            "invalid_request",
            "供给任务创建参数无效",
        )
    request_line_id = _require_uuid("request_line_id", value.request_line_id)
    supply_type = _require_choice(
        "supply_type", value.supply_type, _SUPPLY_TYPES
    )
    reference_no = _require_optional_reference(value.reference_no)
    expected_qty = _require_quantity(value.expected_qty)
    expected_date = _require_optional_date(value.expected_date)
    note = _require_text("note", value.note, 4000, required=False)
    return SupplyTaskCreateInput(
        request_line_id=request_line_id,
        supply_type=supply_type,
        reference_no=reference_no,
        expected_qty=expected_qty,
        expected_date=expected_date,
        note=note,
    )


def _validate_update_input(value: SupplyTaskUpdateInput) -> SupplyTaskUpdateInput:
    if not isinstance(value, SupplyTaskUpdateInput):
        _fail(
            "supply_task_update_input_invalid",
            "invalid_request",
            "供给任务更新参数无效",
        )
    status = _require_choice("status", value.status, _TASK_STATUSES)
    reference_no = _require_optional_reference(value.reference_no)
    expected_date = _require_optional_date(value.expected_date)
    comment = _require_text(
        "comment", value.comment, 4000, required=False
    )
    if status == "reference_registered" and reference_no is None:
        _fail(
            "supply_task_reference_required",
            "invalid_request",
            "登记供给参考号时 reference_no 不能为空",
        )
    if status in _TERMINAL_TASK_STATUSES and not comment:
        _fail(
            "supply_task_terminal_comment_required",
            "invalid_request",
            "终结供给任务必须填写说明",
        )
    return SupplyTaskUpdateInput(
        status=status,
        reference_no=reference_no,
        expected_date=expected_date,
        comment=comment,
    )


def _require_uuid(field_name: str, value: object) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail(
            f"material_request_{field_name}_invalid",
            "invalid_request",
            f"{field_name} 无效",
        )
    return value


def _require_version(field_name: str, value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(
            f"material_request_{field_name}_invalid",
            "invalid_request",
            f"{field_name} 无效",
        )
    return value


def _require_quantity(value: object) -> Decimal:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value <= 0
        or value >= _MAX_QUANTITY
        or value.as_tuple().exponent < -3
    ):
        _fail(
            "supply_task_expected_qty_invalid",
            "invalid_request",
            "供给计划数量必须是最多三位小数的正数",
        )
    return value.quantize(Decimal("0.001"))


def _require_optional_date(value: object) -> date | None:
    if value is None:
        return None
    if not isinstance(value, date) or isinstance(value, datetime):
        _fail(
            "supply_task_expected_date_invalid",
            "invalid_request",
            "预计日期无效",
        )
    return value


def _require_optional_reference(value: object) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or _REFERENCE_PATTERN.fullmatch(value) is None:
        _fail(
            "supply_task_reference_no_invalid",
            "invalid_request",
            "供给参考号格式无效",
        )
    return value


def _require_choice(
    field_name: str, value: object, choices: frozenset[str]
) -> str:
    if not isinstance(value, str) or value not in choices:
        _fail(
            f"supply_task_{field_name}_invalid",
            "invalid_request",
            f"{field_name} 无效",
        )
    return value


def _require_text(
    field_name: str,
    value: object,
    maximum: int,
    *,
    required: bool,
) -> str:
    if (
        not isinstance(value, str)
        or value != value.strip()
        or len(value) > maximum
        or (required and not value)
    ):
        _fail(
            f"supply_task_{field_name}_invalid",
            "invalid_request",
            f"{field_name} 格式无效",
        )
    return value


def _require_trace_request_id(value: object) -> str:
    if not isinstance(value, str) or _TRACE_PATTERN.fullmatch(value) is None:
        _fail(
            "material_request_trace_id_invalid",
            "invalid_request",
            "X-Request-ID 无效",
        )
    return value


def _require_idempotency_key(value: object) -> str:
    if (
        not isinstance(value, str)
        or _IDEMPOTENCY_PATTERN.fullmatch(value) is None
    ):
        _fail(
            "material_request_idempotency_key_invalid",
            "invalid_request",
            "Idempotency-Key 无效",
        )
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    if isinstance(value, str):
        encoded = value.strip().encode("utf-8")
    elif isinstance(value, bytes):
        encoded = value
    else:
        encoded = b""
    lowered = encoded.lower()
    if len(encoded) < 32 or any(
        marker.encode("ascii") in lowered for marker in _PLACEHOLDERS
    ):
        _fail(
            "material_request_idempotency_hmac_unavailable",
            "service_unavailable",
            "需求单幂等 HMAC 密钥不可用",
        )
    return encoded


def _idempotency_hmac(
    secret: bytes,
    actor_user_id: str,
    method: str,
    path: str,
    raw_key: str,
) -> str:
    document = (
        "cloud_oam.material_request.idempotency.v1\0"
        f"actor={actor_user_id}\0method={method}\0path={path}\0key={raw_key}"
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
        _fail(
            "material_request_database_clock_invalid",
            "service_unavailable",
            "数据库时间不可用",
        )
    return _as_utc(value)


def _task_number(now: datetime, task_id: uuid.UUID) -> str:
    return f"SUP-{_as_utc(now):%Y%m%d}-{task_id.hex.upper()}"


def _state_event_key(key_hash: str, task_version: int) -> str:
    return f"mr-supply:{key_hash}:{task_version}"


def _audit_action(operation: str) -> str:
    suffix = {
        "create_supply_task": "create",
        "update_supply_task": "update",
        "cancel_supply_task": "cancel",
    }.get(operation)
    if suffix is None:
        _fail_replay_invalid()
    return f"material_request.supply_task.{suffix}"


def _fulfillment_axes(request: MaterialRequest) -> dict[str, str]:
    return {key: getattr(request, key) for key in _NEUTRAL_AXES}


def _strict_fulfillment_axes(value: object) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != set(_NEUTRAL_AXES):
        raise ValueError
    axes = {key: _strict_string(value, key) for key in _NEUTRAL_AXES}
    if axes != _NEUTRAL_AXES:
        raise ValueError
    return axes


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


def _quantity(value: Decimal) -> Decimal:
    return Decimal(value).quantize(Decimal("0.001"))


def _quantity_token(value: Decimal) -> str:
    return format(_quantity(value), "f")


def _date_token(value: date | None) -> str | None:
    return value.isoformat() if value is not None else None


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


def _strict_optional_reference(
    value: Mapping[str, Any], key: str
) -> str | None:
    item = value[key]
    if item is None:
        return None
    if not isinstance(item, str) or _REFERENCE_PATTERN.fullmatch(item) is None:
        raise ValueError
    return item


def _strict_quantity_token(
    value: Mapping[str, Any], key: str
) -> Decimal:
    item = _strict_string(value, key)
    if re.fullmatch(r"(?:0|[1-9]\d*)\.\d{3}", item) is None:
        raise ValueError
    parsed = Decimal(item)
    if parsed <= 0 or parsed >= _MAX_QUANTITY:
        raise ValueError
    return parsed


def _strict_date_token(
    value: Mapping[str, Any], key: str
) -> date | None:
    item = value[key]
    if item is None:
        return None
    if not isinstance(item, str):
        raise ValueError
    parsed = date.fromisoformat(item)
    if parsed.isoformat() != item:
        raise ValueError
    return parsed


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp_token(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds")


def _same_timestamp(left: datetime, right: datetime) -> bool:
    return _timestamp_token(left) == _timestamp_token(right)


def _fail_replay_invalid() -> None:
    _fail(
        "material_request_supply_idempotency_record_invalid",
        "service_unavailable",
        "供给任务幂等记录无法安全核验",
    )


def _fail(code: str, category: str, message: str):
    raise MaterialRequestSupplyError(code, category, message)


__all__ = [
    "MaterialRequestSupplyError",
    "SupplyTaskCommandResult",
    "SupplyTaskCreateInput",
    "SupplyTaskUpdateInput",
    "create_supply_task",
    "update_supply_task",
    "validate_supply_task_audit_event",
    "validate_supply_task_command_result",
]
