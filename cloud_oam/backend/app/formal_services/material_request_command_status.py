"""Read-only recovery for requester lifecycle commands.

The caller supplies only the previously persisted ``X-Request-ID`` sentinel.
This service never accepts or reconstructs an idempotency key, never acquires
an advisory/row lock, and never flushes, commits, rolls back or appends audit.

``not_observed`` means only that no material-request audit row was visible for
the exact sentinel in the current database read.  Once any row is observed,
every audit, command, action, transition and current-projection edge must agree
or the lookup fails closed; an incomplete graph is never downgraded to
``not_observed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hmac
import re
from typing import Final, Mapping
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..demand_models import ApprovalAction, MaterialRequestCommand
from ..formal_access import FormalPrincipal
from ..foundation_models import AuditEvent, StateTransitionEvent
from .audit_chain import calculate_audit_event_hash
from . import material_request_lifecycle as lifecycle


_SHA256: Final[re.Pattern[str]] = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_TERMINAL_STATUS: Final[dict[str, str]] = {
    "withdraw": "withdrawn",
    "cancel": "cancelled",
}
_TRANSITION_REASON: Final[dict[str, str]] = {
    "withdraw": "material_request_withdrawn_by_requester",
    "cancel": "material_request_safely_cancelled_by_requester",
}


@dataclass(frozen=True, slots=True)
class MaterialRequestLifecycleCommandStatusCommand:
    action: str
    request_id: uuid.UUID
    request_version: int
    revision_id: uuid.UUID
    revision_no: int
    approval_instance_id: uuid.UUID
    approval_attempt_no: int
    current_step_id: None
    states: Mapping[str, str]
    occurred_at: datetime


@dataclass(frozen=True, slots=True)
class MaterialRequestLifecycleCommandStatusResult:
    lookup_status: str
    command: MaterialRequestLifecycleCommandStatusCommand | None


def material_request_lifecycle_command_status(
    db: Session,
    *,
    actor: FormalPrincipal,
    trace_request_id: str,
) -> MaterialRequestLifecycleCommandStatusResult:
    """Resolve one prior withdraw/cancel from its exact audit sentinel."""

    return lifecycle._public_boundary(
        lambda: _lookup_impl(
            db,
            actor=actor,
            trace_request_id=trace_request_id,
        )
    )


def _lookup_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    trace_request_id: str,
) -> MaterialRequestLifecycleCommandStatusResult:
    supplied = lifecycle._validate_supplied_actor(actor)
    trace_id = lifecycle._require_trace_request_id(trace_request_id)

    # Prevent an unrelated dirty ORM object from turning this GET into an
    # implicit autoflush write.  Every statement below is an ordinary SELECT.
    with db.no_autoflush:
        now = lifecycle._database_now(db)
        requester = lifecycle._require_requester_context(db, supplied, "read", now)
        audit_rows = tuple(
            db.scalars(
                select(AuditEvent)
                .where(
                    AuditEvent.stream_key == lifecycle.MATERIAL_REQUEST_AUDIT_STREAM,
                    AuditEvent.action.in_(
                        ("material_request.withdraw", "material_request.cancel")
                    ),
                    AuditEvent.request_id == trace_id,
                    AuditEvent.actor_user_id == requester.principal.user_id,
                )
                .order_by(AuditEvent.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        if not audit_rows:
            return MaterialRequestLifecycleCommandStatusResult(
                lookup_status="not_observed",
                command=None,
            )
        if len(audit_rows) != 1:
            _fail(
                "material_request_command_status_audit_duplicate",
                "service_unavailable",
                "生命周期命令查询坐标对应了重复审计事实",
            )
        audit = audit_rows[0]
        operation = _audit_operation(audit)
        action_requester = lifecycle._require_requester_context(
            db,
            requester.principal,
            operation,
            now,
        )
        if (
            action_requester.technician_grant.assignment_id
            != requester.technician_grant.assignment_id
        ):
            _fail(
                "material_request_command_status_action_forbidden",
                "forbidden",
                "当前工程师本人权限不再允许查询该生命周期动作结果",
            )
        requester = action_requester
        if audit.actor_user_id != requester.principal.user_id:
            _fail(
                "material_request_command_status_actor_mismatch",
                "forbidden",
                "生命周期命令不属于当前有效申请人",
            )
        request_id = _audit_request_id(audit)
        _require_audit_row_hash(audit)

        command_rows = tuple(
            db.scalars(
                select(MaterialRequestCommand)
                .where(
                    MaterialRequestCommand.request_id == request_id,
                    MaterialRequestCommand.operation == operation,
                )
                .order_by(
                    MaterialRequestCommand.occurred_at,
                    MaterialRequestCommand.id,
                )
                .execution_options(populate_existing=True)
            ).all()
        )
        command_rows = tuple(
            row
            for row in command_rows
            if lifecycle._same_timestamp(row.occurred_at, audit.occurred_at)
        )
        if len(command_rows) != 1:
            _fail(
                "material_request_command_status_command_invalid",
                "service_unavailable",
                "生命周期命令事实无法唯一核验",
            )
        command = command_rows[0]
        _require_command_actor(command, requester)
        _require_sha256(command.request_hash, "request")
        _require_sha256(command.result_hash, "result")

        action_rows = tuple(
            db.scalars(
                select(ApprovalAction)
                .where(ApprovalAction.command_id == command.id)
                .order_by(ApprovalAction.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        if len(action_rows) != 1:
            _fail(
                "material_request_lifecycle_action_invalid",
                "service_unavailable",
                "需求单生命周期动作事实无法唯一核验",
            )
        action = action_rows[0]
        replay = lifecycle._load_replay(
            db,
            key_hash=command.idempotency_key_hash,
            request_hash=command.request_hash,
            operation=operation,
            request_id=request_id,
            request_reference=(
                f"/api/v1/material-requests/{request_id}/{operation}"
            ),
            requester=requester,
            expected_comment=action.comment,
        )
        if (
            replay is None
            or replay.command.id != command.id
            or replay.action.id != action.id
        ):
            _fail(
                "material_request_command_status_command_invalid",
                "service_unavailable",
                "生命周期命令与审批动作绑定无效",
            )

        graph = lifecycle._read_graph(db, request_id)
        lifecycle._require_owned_request(graph.request, requester)
        lifecycle._require_replay_projection(graph, replay.result)
        lifecycle._require_neutral_axes(graph.request)
        _require_command_request_hash(command, action, graph, audit)
        _require_terminal_projection(
            db,
            graph=graph,
            replay=replay,
            audit=audit,
        )
        _require_state_transition(
            db,
            graph=graph,
            command=command,
            action=operation,
            audit=audit,
        )
        _require_audit_projection(
            graph=graph,
            replay=replay,
            audit=audit,
        )

        result = replay.result
        return MaterialRequestLifecycleCommandStatusResult(
            lookup_status="confirmed",
            command=MaterialRequestLifecycleCommandStatusCommand(
                action=operation,
                request_id=result.request_id,
                request_version=result.request_version,
                revision_id=result.revision_id,
                revision_no=result.revision_no,
                approval_instance_id=result.approval_instance_id,
                approval_attempt_no=result.approval_attempt_no,
                current_step_id=None,
                states={
                    "request_status": result.request_status,
                    **dict(result.state_axes),
                },
                occurred_at=_aware_utc(command.occurred_at),
            ),
        )


def _audit_operation(audit: AuditEvent) -> str:
    if audit.action == "material_request.withdraw":
        return "withdraw"
    if audit.action == "material_request.cancel":
        return "cancel"
    _fail(
        "material_request_command_status_audit_invalid",
        "service_unavailable",
        "查询坐标已被其他需求单动作占用",
    )


def _audit_request_id(audit: AuditEvent) -> uuid.UUID:
    if audit.aggregate_type != lifecycle.MATERIAL_REQUEST_AGGREGATE:
        _fail(
            "material_request_command_status_audit_invalid",
            "service_unavailable",
            "生命周期审计对象类型无效",
        )
    try:
        value = uuid.UUID(audit.aggregate_id)
    except (AttributeError, TypeError, ValueError):
        value = uuid.UUID(int=0)
    if value.int == 0:
        _fail(
            "material_request_command_status_audit_invalid",
            "service_unavailable",
            "生命周期审计对象坐标无效",
        )
    return value


def _require_audit_row_hash(audit: AuditEvent) -> None:
    calculated = calculate_audit_event_hash(
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
    if not _SHA256.fullmatch(audit.event_hash or "") or not hmac.compare_digest(
        audit.event_hash,
        calculated,
    ):
        _fail(
            "material_request_command_status_audit_hash_invalid",
            "service_unavailable",
            "生命周期审计摘要无法核验",
        )


def _require_command_actor(command, requester) -> None:
    if (
        command.actor_user_id != requester.principal.user_id
        or command.actor_person_id != requester.person.id
        or command.actor_role_assignment_id
        != requester.technician_grant.assignment_id
        or command.authorization_version
        != requester.principal.authorization_version
    ):
        _fail(
            "material_request_command_status_actor_mismatch",
            "forbidden",
            "生命周期命令不属于当前有效申请人或授权版本已变化",
        )


def _require_sha256(value: object, name: str) -> str:
    if not isinstance(value, str) or _SHA256.fullmatch(value) is None:
        _fail(
            "material_request_command_status_hash_invalid",
            "service_unavailable",
            f"生命周期命令的{name}摘要无效",
        )
    return value


def _require_command_request_hash(command, action, graph, audit) -> None:
    document = command.request_jsonb
    if not isinstance(document, dict):
        _fail(
            "material_request_command_status_request_invalid",
            "service_unavailable",
            "生命周期命令请求事实无效",
        )
    payload_hash = _require_sha256(document.get("payload_sha256"), "payload")
    if not hmac.compare_digest(payload_hash, command.request_hash):
        _fail(
            "material_request_command_status_request_hash_invalid",
            "service_unavailable",
            "生命周期命令请求摘要不一致",
        )

    # Lifecycle inputs are persisted as immutable action/fact evidence.  Since
    # 0039, cancel audit evidence also seals the original non-sensitive line
    # UUID order, preserving 0037 request-hash/replay semantics while making
    # the hash independently reproducible for recovery.
    if command.operation == "withdraw":
        payload = {
            "operation": "withdraw",
            "request_id": str(graph.request.id),
            "expected_version": command.target_version - 1,
            "reason": action.comment,
            "actor_user_id": command.actor_user_id,
            "actor_person_id": str(command.actor_person_id),
            "authorization_version": command.authorization_version,
        }
    else:
        line_order = _cancel_request_line_order(audit, graph)
        facts_by_line = {
            str(fact.request_line_id): fact
            for fact in graph.cancellation_facts
        }
        line_documents = tuple(
            {
                "request_line_id": str(fact.request_line_id),
                "cancelled_qty": format(fact.cancelled_qty, "f"),
                "reason": fact.reason,
            }
            for fact in (facts_by_line[line_id] for line_id in line_order)
        )
        payload = {
            "operation": "cancel",
            "request_id": str(graph.request.id),
            "expected_version": command.target_version - 1,
            "reason": action.comment,
            "lines": line_documents,
            "actor_user_id": command.actor_user_id,
            "actor_person_id": str(command.actor_person_id),
            "authorization_version": command.authorization_version,
        }
    expected = lifecycle._canonical_hash(payload)
    if not hmac.compare_digest(expected, command.request_hash):
        _fail(
            "material_request_command_status_request_hash_invalid",
            "service_unavailable",
            "生命周期命令请求摘要无法复算",
        )


def _require_terminal_projection(db: Session, *, graph, replay, audit) -> None:
    command = replay.command
    action = command.operation
    request = graph.request
    instance = graph.latest_instance
    if instance.id != replay.result.approval_instance_id:
        _fail_projection("审批实例坐标不一致")
    if (
        request.version != command.target_version
        or request.status != _TERMINAL_STATUS[action]
        or instance.current_step_id is not None
        or instance.current_step_no is not None
    ):
        _fail_projection("生命周期终态投影不一致")

    if action == "withdraw":
        if (
            request.withdrawn_at is None
            or not lifecycle._same_timestamp(request.withdrawn_at, command.occurred_at)
            or request.cancelled_at is not None
            or instance.status != "withdrawn"
            or instance.completed_at is None
            or not lifecycle._same_timestamp(instance.completed_at, command.occurred_at)
            or graph.cancellation_facts
        ):
            _fail_projection("撤回终态或审批实例投影无效")
        steps = graph.steps_by_instance.get(instance.id, ())
        cancelled = tuple(step for step in steps if step.status == "cancelled")
        if (
            not steps
            or not cancelled
            or any(step.status in lifecycle._ACTIVE_STEP_STATUSES for step in steps)
            or any(
                step.decided_at is not None
                or step.decision_manifest_sha256 is not None
                for step in cancelled
            )
        ):
            _fail_projection("撤回后的审批步骤未完整封存")
        lifecycle._require_no_compensation_facts(db, graph)
        return

    before = _audit_snapshot(audit.before_jsonb, "before")
    source_status = before.get("status")
    expected_instance_status = "returned" if source_status == "returned" else "completed"
    if (
        request.cancelled_at is None
        or not lifecycle._same_timestamp(request.cancelled_at, command.occurred_at)
        or request.withdrawn_at is not None
        or instance.status != expected_instance_status
        or instance.completed_at is None
    ):
        _fail_projection("取消终态或审批实例投影无效")
    requested = tuple(
        lifecycle.MaterialRequestCancellationLineInput(
            request_line_id=fact.request_line_id,
            cancelled_qty=fact.cancelled_qty,
            reason=fact.reason,
        )
        for fact in graph.cancellation_facts
    )
    lifecycle._require_cancellation_replay_facts(
        graph=graph,
        replay=replay,
        requested=requested,
    )
    lifecycle._require_no_compensation_facts(db, graph)


def _require_state_transition(
    db: Session,
    *,
    graph,
    command,
    action: str,
    audit: AuditEvent,
) -> None:
    terminal = _TERMINAL_STATUS[action]
    rows = tuple(
        db.scalars(
            select(StateTransitionEvent)
            .where(
                StateTransitionEvent.aggregate_type
                == lifecycle.MATERIAL_REQUEST_AGGREGATE,
                StateTransitionEvent.aggregate_id == str(graph.request.id),
                StateTransitionEvent.to_status == terminal,
            )
            .order_by(StateTransitionEvent.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(rows) != 1:
        _fail_projection("生命周期状态转换事实无法唯一核验")
    row = rows[0]
    before = _audit_snapshot(audit.before_jsonb, "before")
    expected_metadata = {
        "request_id": str(graph.request.id),
        "request_no": graph.request.request_no,
        "revision_id": str(graph.revision.id),
        "revision_no": graph.revision.revision_no,
        "idempotency_key_hash": command.idempotency_key_hash,
    }
    if (
        row.from_status != before.get("status")
        or row.reason != _TRANSITION_REASON[action]
        or row.actor_id != command.actor_user_id
        or row.idempotency_key
        != f"mr:{command.idempotency_key_hash}:{terminal}"
        or row.metadata_jsonb != expected_metadata
        or not lifecycle._same_timestamp(row.occurred_at, command.occurred_at)
        or not lifecycle._same_timestamp(row.created_at, command.created_at)
    ):
        _fail_projection("生命周期状态转换事实绑定无效")


def _require_audit_projection(*, graph, replay, audit: AuditEvent) -> None:
    command = replay.command
    action = command.operation
    if (
        audit.request_id != audit.request_id.strip()
        or audit.actor_user_id != command.actor_user_id
        or audit.aggregate_id != str(graph.request.id)
        or not lifecycle._same_timestamp(audit.occurred_at, command.occurred_at)
    ):
        _fail_projection("生命周期审计坐标绑定无效")
    before = _audit_snapshot(audit.before_jsonb, "before")
    after = _audit_snapshot(audit.after_jsonb, "after")
    current = lifecycle._safe_snapshot(graph)
    expected_before_keys = set(current)
    if set(before) != expected_before_keys:
        _fail_projection("生命周期审计前态字段无效")
    common_before = {
        key: value
        for key, value in current.items()
        if key not in {"status", "version", "line_manifest_sha256"}
    }
    if any(before.get(key) != value for key, value in common_before.items()):
        _fail_projection("生命周期审计前态对象坐标无效")
    allowed_sources = (
        lifecycle._WITHDRAWABLE_STATUSES
        if action == "withdraw"
        else lifecycle._DIRECTLY_CANCELLABLE_STATUSES
    )
    if (
        before.get("status") not in allowed_sources
        or before.get("version") != command.target_version - 1
        or _SHA256.fullmatch(str(before.get("line_manifest_sha256", ""))) is None
    ):
        _fail_projection("生命周期审计前态版本或摘要无效")
    expected_before_line_manifest = _before_line_manifest(
        graph,
        action=action,
        source_status=before["status"],
    )
    if not hmac.compare_digest(
        before["line_manifest_sha256"],
        expected_before_line_manifest,
    ):
        _fail_projection("生命周期审计前态明细摘要无法复算")

    reason_hash = lifecycle._text_hash(replay.action.comment)
    if action == "withdraw":
        steps = graph.steps_by_instance.get(graph.latest_instance.id, ())
        cancelled_ids = [str(step.id) for step in steps if step.status == "cancelled"]
        expected_after = {
            **current,
            "approval_instance_id": str(graph.latest_instance.id),
            "cancelled_open_step_ids": cancelled_ids,
            "reason_sha256": reason_hash,
        }
    else:
        request_line_order = _cancel_request_line_order(audit, graph)
        expected_after = {
            **current,
            "approval_instance_id": str(graph.latest_instance.id),
            "cancellation_fact_count": len(graph.cancellation_facts),
            "cancellation_fact_manifest_sha256": (
                lifecycle._cancellation_fact_manifest(graph.cancellation_facts)
            ),
            "request_payload_schema": lifecycle._CANCEL_AUDIT_REQUEST_SCHEMA,
            "request_line_order": list(request_line_order),
            "reason_sha256": reason_hash,
        }
    if after != expected_after:
        _fail_projection("生命周期审计后态与当前完整投影不一致")


def _cancel_request_line_order(
    audit: AuditEvent,
    graph,
) -> tuple[str, ...]:
    after = _audit_snapshot(audit.after_jsonb, "after")
    raw_order = after.get("request_line_order")
    if (
        after.get("request_payload_schema")
        != lifecycle._CANCEL_AUDIT_REQUEST_SCHEMA
        or not isinstance(raw_order, list)
        or len(raw_order) > 200
    ):
        _fail(
            "material_request_command_status_cancel_order_missing",
            "service_unavailable",
            "取消命令缺少可独立核验的请求明细顺序",
        )
    checked: list[str] = []
    for value in raw_order:
        if not isinstance(value, str):
            _fail_projection("取消命令请求明细顺序无效")
        try:
            parsed = uuid.UUID(value)
        except (AttributeError, TypeError, ValueError):
            _fail_projection("取消命令请求明细顺序无效")
        if parsed.int == 0 or str(parsed) != value:
            _fail_projection("取消命令请求明细顺序无效")
        checked.append(value)
    expected = {str(fact.request_line_id) for fact in graph.cancellation_facts}
    if (
        len(checked) != len(set(checked))
        or set(checked) != expected
    ):
        _fail_projection("取消命令请求明细顺序与逐行事实不一致")
    return tuple(checked)


def _before_line_manifest(graph, *, action: str, source_status: str) -> str:
    if action == "withdraw":
        documents = tuple(
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
        return lifecycle._canonical_hash(documents)

    documents = []
    for line in graph.lines:
        if line.version < 1:
            _fail_projection("取消后的明细版本无法还原前态")
        if source_status == "returned":
            previous_status = "approval_pending"
        elif line.final_approved_qty == 0:
            previous_status = "rejected"
        elif line.final_approved_qty == line.requested_qty:
            previous_status = "approved"
        else:
            previous_status = "partially_approved"
        documents.append(
            {
                "line_id": str(line.id),
                "line_no": line.line_no,
                "status": previous_status,
                "final_approved_qty": format(line.final_approved_qty, "f"),
                "cancelled_qty": "0.000",
                "version": line.version - 1,
            }
        )
    return lifecycle._canonical_hash(tuple(documents))


def _audit_snapshot(value: object, name: str) -> dict:
    if not isinstance(value, dict):
        _fail(
            "material_request_command_status_audit_invalid",
            "service_unavailable",
            f"生命周期审计{name}快照无效",
        )
    return value


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fail_projection(message: str) -> None:
    _fail(
        "material_request_command_status_projection_invalid",
        "service_unavailable",
        message,
    )


def _fail(code: str, category: str, message: str):
    raise lifecycle.MaterialRequestLifecycleError(code, category, message)


__all__ = [
    "MaterialRequestLifecycleCommandStatusCommand",
    "MaterialRequestLifecycleCommandStatusResult",
    "material_request_lifecycle_command_status",
]
