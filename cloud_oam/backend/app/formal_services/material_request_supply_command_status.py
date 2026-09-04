"""Read-only, trace-based recovery of an observed supply-plan command.

The result is historical evidence, not a claim that the task still has that
status.  This reader never accepts an idempotency key and never retries a write.
It checks the current original-material planning projection and every supply
command leading to it.  A new, unsupported fulfillment path fails closed until
its own evidence reader is implemented; it is not inferred from a larger version.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
import hmac
import re
from types import MappingProxyType
from typing import Mapping
import uuid

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from ..demand_models import (
    ApprovalAction,
    ApprovalInstance,
    MaterialRequest,
    MaterialRequestCommand,
    MaterialRequestLine,
    MaterialRequestRevision,
    SupplyTask,
)
from ..formal_access import FormalPrincipal, load_formal_principal
from ..foundation_models import AuditChainHead, AuditEvent, Role, RoleAssignment, StateTransitionEvent
from . import material_request_command_status as lifecycle_status
from . import material_request_lifecycle as lifecycle
from . import material_request_supply as supply


_OPERATIONS = frozenset(
    {"create_supply_task", "update_supply_task", "cancel_supply_task"}
)
_HASH = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


@dataclass(frozen=True, slots=True)
class MaterialRequestSupplyCommandStatusResult:
    lookup_status: str
    command: supply.SupplyTaskCommandResult | None
    occurred_at: datetime | None


@dataclass(frozen=True, slots=True)
class _HistoricalProjection:
    """Detached immutable values, never an ORM object or a current projection."""

    values: Mapping[str, object]

    def __getattr__(self, name):
        return self.values[name]


def material_request_supply_command_status(
    db: Session, *, actor: FormalPrincipal, trace_request_id: str
) -> MaterialRequestSupplyCommandStatusResult:
    """Resolve exactly one actor/trace; incomplete evidence is never absent."""

    def lookup():
        try:
            return _lookup(db, actor=actor, trace_request_id=trace_request_id)
        except (AttributeError, TypeError, ValueError, OverflowError):
            # A malformed persisted JSON/time value is incomplete evidence,
            # never an unhandled 500 or permission to submit a new command.
            _invalid()
        except lifecycle.MaterialRequestLifecycleError:
            _invalid()

    return supply._public_boundary(lookup)


def _lookup(db: Session, *, actor: FormalPrincipal, trace_request_id: str):
    supplied = supply._validate_supplied_actor(actor)
    trace = supply._require_trace_request_id(trace_request_id)
    # Even an unrelated pending ORM object must not turn this GET into a write.
    with db.no_autoflush:
        now = supply._database_now(db)
        current = _fresh_manager(db, supplied, now)
        audits = _rows(
            db,
            select(AuditEvent).where(
                AuditEvent.actor_user_id == current.user_id,
                AuditEvent.request_id == trace,
            ),
        )
        if not audits:
            return MaterialRequestSupplyCommandStatusResult("not_observed", None, None)
        if len(audits) != 1 or not isinstance(audits[0].after_jsonb, Mapping):
            _invalid()
        audit = audits[0]
        command_id = _uuid(audit.after_jsonb.get("command_id"))
        commands = _rows(db, select(MaterialRequestCommand).where(MaterialRequestCommand.id == command_id))
        if len(commands) != 1:
            _invalid()
        command = commands[0]
        result = _command_result(command)
        supply.validate_supply_task_audit_event(audit, command=command, result=result)
        if (
            command.actor_user_id != current.user_id
            or command.actor_person_id != current.person_id
            or command.authorization_version != current.authorization_version
        ):
            supply._fail(
                "material_request_supply_command_status_authorization_changed",
                "precondition_failed",
                "原供给命令的人员或授权版本已变化，不能解除恢复阻塞",
            )
        requests = _rows(db, select(MaterialRequest).where(MaterialRequest.id == result.request_id))
        if len(requests) != 1:
            _invalid()
        request = requests[0]
        context = supply._require_supply_actor_context(db, current, request=request, now=now)
        if command.actor_role_assignment_id != context.grant.assignment_id:
            supply._fail(
                "material_request_supply_command_status_authorization_changed",
                "precondition_failed",
                "原供给命令的授权范围已变化，不能解除恢复阻塞",
            )
        verified_request_version = _verify_current_history(db, request=request, requested_command=command, requested_result=result)
        # Recheck after the multi-statement read.  A concurrent version advance
        # is not guessed safe: the next GET may establish a coherent snapshot.
        _fresh_manager(db, current, supply._database_now(db))
        latest = _rows(db, select(MaterialRequest).where(MaterialRequest.id == request.id))
        if len(latest) != 1 or latest[0].version != verified_request_version:
            _invalid()
        return MaterialRequestSupplyCommandStatusResult("confirmed", result, supply._as_utc(command.occurred_at))


def _fresh_manager(db: Session, supplied: FormalPrincipal, now: datetime) -> FormalPrincipal:
    current = load_formal_principal(db, supplied.user_id, now=now)
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
        or current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        supply._fail(
            "material_request_supply_actor_principal_stale",
            "precondition_failed",
            "供给权限版本已变化，请重新读取后再恢复",
        )
    grants = tuple(
        row for row in current.assignments
        if row.role_code == "admin" and row.scope_type == "national" and row.scope_id == "*"
    )
    if len(grants) != 1:
        _forbidden()
    selected = replace(
        current,
        assignments=grants,
        entitlements=tuple(row for row in current.entitlements if row.assignment_id == grants[0].assignment_id),
    )
    for principal in (current, selected):
        if not principal.allows(db, "supply_task", "manage", target_scope_type="national", target_scope_id="*"):
            _forbidden()
    return current


def _verify_current_history(db: Session, *, request, requested_command, requested_result) -> int:
    current_version = request.version
    revisions = _rows(db, select(MaterialRequestRevision).where(
        MaterialRequestRevision.request_id == request.id,
        MaterialRequestRevision.revision_no == request.revision_no,
    ))
    instances = _rows(db, select(ApprovalInstance).where(ApprovalInstance.request_id == request.id).order_by(ApprovalInstance.attempt_no))
    if len(revisions) != 1 or not instances:
        _invalid()
    revision = revisions[0]
    lines = _rows(db, select(MaterialRequestLine).where(MaterialRequestLine.request_id == request.id))
    current_lines = tuple(row for row in lines if row.revision_id == revision.id and row.revision_no == revision.revision_no)
    tasks = _rows(db, select(SupplyTask).where(SupplyTask.request_line_id.in_(tuple(row.id for row in lines))))
    commands = _rows(db, select(MaterialRequestCommand).where(
        MaterialRequestCommand.request_id == request.id,
        MaterialRequestCommand.operation.in_(_OPERATIONS),
    ).order_by(MaterialRequestCommand.target_version, MaterialRequestCommand.id))
    if not commands or requested_command.id not in {row.id for row in commands}:
        _invalid()
    cancellation_audit = None
    if request.status == "cancelled":
        request, current_lines, cancellation_audit = _verified_cancellation_predecessor(
            db, request=request, last_supply_command=commands[-1],
        )
    graph = supply._LockedSupplyGraph(request, revision, current_lines, instances[-1], (), tasks)
    try:
        supply._require_final_approval_graph(graph)
    except supply.MaterialRequestSupplyError:
        _invalid()
    versions = tuple(row.target_version for row in commands)
    if len(versions) != request.version - versions[0] + 1 or any(version != versions[0] + index for index, version in enumerate(versions)):
        _invalid()
    audits = _rows(db, select(AuditEvent).where(
        AuditEvent.aggregate_id == str(request.id),
        AuditEvent.aggregate_type == supply.MATERIAL_REQUEST_AGGREGATE,
    ))
    task_ids = tuple(str(row.id) for row in tasks)
    events = _rows(db, select(StateTransitionEvent).where(or_(
        (StateTransitionEvent.aggregate_type == supply.SUPPLY_TASK_AGGREGATE)
        & StateTransitionEvent.aggregate_id.in_(task_ids),
        StateTransitionEvent.idempotency_key.in_(tuple(
            supply._state_event_key(row.idempotency_key_hash, row.result_jsonb.get("task_version"))
            for row in commands if isinstance(row.result_jsonb, Mapping)
        )),
    )))
    previous_by_task = {}
    first_by_task = {}
    last_commands = {}
    used_audits, used_events = set(), set()
    previous_time = None
    previous_audit_version = None
    for command in commands:
        result = _command_result(command)
        if (
            result.request_id != request.id
            or result.request_no != request.request_no
            or result.request_status != request.status
            or result.revision_id != revision.id
            or result.revision_no != revision.revision_no
            or result.approval_instance_id != instances[-1].id
            or result.approval_attempt_no != instances[-1].attempt_no
            or dict(result.state_axes) != {"request_status": request.status, **supply._fulfillment_axes(request)}
            or result.request_line_id not in {row.id for row in current_lines}
            or (previous_time is not None and supply._as_utc(command.occurred_at) < previous_time)
        ):
            _invalid()
        try:
            approved_line = supply._require_current_approved_line(graph, result.request_line_id)
            supply._require_existing_capacity(approved_line, tasks)
        except supply.MaterialRequestSupplyError:
            _invalid()
        matching = tuple(row for row in audits if isinstance(row.after_jsonb, Mapping) and row.after_jsonb.get("command_id") == str(command.id))
        if len(matching) != 1:
            _invalid()
        audit = matching[0]
        supply.validate_supply_task_audit_event(audit, command=command, result=result)
        if not isinstance(audit.stream_version, int) or isinstance(audit.stream_version, bool) or audit.stream_version <= 0:
            _invalid()
        if previous_audit_version is not None and audit.stream_version <= previous_audit_version:
            _invalid()
        previous = previous_by_task.get(result.supply_task_id)
        _verify_predecessor(result, audit, previous)
        if previous is None:
            first_by_task[result.supply_task_id] = (command, result)
        _verify_transition(command, result, previous, events, used_events)
        previous_by_task[result.supply_task_id] = result
        last_commands[result.supply_task_id] = command
        used_audits.add(audit.id)
        previous_time = supply._as_utc(command.occurred_at)
        previous_audit_version = audit.stream_version
    if any(row.action.startswith("material_request.supply_task.") and row.id not in used_audits for row in audits):
        _invalid()
    verified_audits = tuple(row for row in audits if row.id in used_audits)
    if cancellation_audit is not None:
        if cancellation_audit.stream_version <= previous_audit_version:
            _invalid()
        verified_audits += (cancellation_audit,)
    _verify_audit_links(db, verified_audits)
    if {row.id for row in events} != used_events or {row.id for row in tasks} != set(previous_by_task):
        _invalid()
    for task in tasks:
        result = previous_by_task[task.id]
        first_command, first = first_by_task[task.id]
        last_command = last_commands[task.id]
        _verify_task(task, first_command, first, last_command, result)
    if not supply._same_timestamp(request.updated_at, commands[-1].occurred_at):
        _invalid()
    if requested_result != _command_result(requested_command):
        _invalid()
    return current_version


def _verified_cancellation_predecessor(db, *, request, last_supply_command):
    """Verify the existing requester cancellation as an exact terminal suffix.

    Historical authorization is proved by the immutable command/action/facts
    and its original owner/scope binding.  The person who cancelled need not
    still be allowed to write today; the caller's fresh supply-admin authority
    was independently checked before this historical evidence is read.
    """

    commands = _rows(db, select(MaterialRequestCommand).where(
        MaterialRequestCommand.request_id == request.id,
        MaterialRequestCommand.operation == "cancel",
    ))
    if len(commands) != 1:
        _invalid()
    command = commands[0]
    if (
        command.target_version != request.version
        or command.target_version != last_supply_command.target_version + 1
        or command.actor_user_id != request.requester_user_id
        or command.actor_user_id != request.created_by_user_id
        or command.actor_person_id != request.requester_person_id
        or command.authorization_version <= 0
        or command.request_reference != f"/api/v1/material-requests/{request.id}/cancel"
        or not supply._same_timestamp(command.occurred_at, command.created_at)
        or supply._as_utc(command.occurred_at) < supply._as_utc(last_supply_command.occurred_at)
    ):
        _invalid()
    for value in (command.idempotency_key_hash, command.request_hash, command.result_hash):
        if not isinstance(value, str) or _HASH.fullmatch(value) is None:
            _invalid()
    if not isinstance(command.result_jsonb, dict) or not hmac.compare_digest(
        command.result_hash, supply._canonical_hash(command.result_jsonb),
    ):
        _invalid()
    result = lifecycle._result_from_document(command.result_jsonb, operation="cancel")
    if command.target_version != result.request_version or command.request_id != result.request_id:
        _invalid()
    actions = _rows(db, select(ApprovalAction).where(ApprovalAction.command_id == command.id))
    if len(actions) != 1:
        _invalid()
    action = actions[0]
    if (
        action.instance_id != result.approval_instance_id
        or action.step_id is not None
        or action.action != "cancel"
        or action.actor_user_id != command.actor_user_id
        or action.actor_person_id != command.actor_person_id
        or action.actor_role_assignment_id != command.actor_role_assignment_id
        or action.authorization_version != command.authorization_version
        or action.source_mode != "internal"
        or not isinstance(action.comment, str) or not action.comment.strip()
        or not supply._same_timestamp(action.occurred_at, command.occurred_at)
        or not supply._same_timestamp(action.created_at, command.created_at)
    ):
        _invalid()
    _verify_historical_requester_grant(db, command)
    graph = lifecycle._read_graph(db, request.id, request=request)
    expected_document = {
        "schema": "rsc.material_request_lifecycle_command.v1",
        "operation": "cancel",
        "request_id": str(request.id),
        "revision_id": str(result.revision_id),
        "revision_no": result.revision_no,
        "target_version": result.request_version,
        "payload_sha256": command.request_hash,
        "sensitive_fields": "excluded",
        "cancellation_fact_count": len(graph.cancellation_facts),
        "cancellation_fact_manifest_sha256": lifecycle._cancellation_fact_manifest(graph.cancellation_facts),
    }
    if command.request_jsonb != expected_document:
        _invalid()
    audits = _rows(db, select(AuditEvent).where(
        AuditEvent.aggregate_type == supply.MATERIAL_REQUEST_AGGREGATE,
        AuditEvent.aggregate_id == str(request.id),
        AuditEvent.action == "material_request.cancel",
    ))
    if len(audits) != 1:
        _invalid()
    audit = audits[0]
    if audit.stream_key != supply.MATERIAL_REQUEST_AUDIT_STREAM:
        _invalid()
    lifecycle_status._require_audit_row_hash(audit)
    replay = lifecycle._ReplayBundle(result=result, command=command, action=action)
    lifecycle._require_replay_projection(graph, result)
    lifecycle._require_neutral_axes(request)
    lifecycle_status._require_command_request_hash(command, action, graph, audit)
    lifecycle_status._require_terminal_projection(db, graph=graph, replay=replay, audit=audit)
    lifecycle_status._require_state_transition(db, graph=graph, command=command, action="cancel", audit=audit)
    lifecycle_status._require_audit_projection(graph=graph, replay=replay, audit=audit)
    before = audit.before_jsonb
    last = _command_result(last_supply_command)
    if (
        before["status"] != last.request_status
        or before["version"] != last.request_version
        or result.request_no != last.request_no
        or result.revision_id != last.revision_id
        or result.revision_no != last.revision_no
        or result.approval_instance_id != last.approval_instance_id
        or result.approval_attempt_no != last.approval_attempt_no
        or dict(result.state_axes) != {key: value for key, value in last.state_axes.items() if key != "request_status"}
        or not supply._same_timestamp(request.updated_at, command.occurred_at)
    ):
        _invalid()
    # Existing cancellation evidence independently reconstructs and hashes this
    # pre-cancel line manifest.  Do not mutate or attach these snapshots to db.
    previous_lines = tuple(
        _historical_projection(
            line,
            status=("rejected" if line.final_approved_qty == 0 else (
                "approved" if line.final_approved_qty == line.requested_qty else "partially_approved"
            )),
            cancelled_qty=Decimal("0.000"),
            version=line.version - 1,
        )
        for line in graph.lines
    )
    previous_request = _historical_projection(
        request, status=last.request_status, version=last.request_version,
        updated_at=last_supply_command.occurred_at, cancelled_at=None,
    )
    return previous_request, previous_lines, audit


def _verify_historical_requester_grant(db, command):
    grants = _rows(db, select(RoleAssignment).where(RoleAssignment.id == command.actor_role_assignment_id))
    if len(grants) != 1:
        _invalid()
    grant = grants[0]
    roles = _rows(db, select(Role).where(Role.id == grant.role_id))
    when = supply._as_utc(command.occurred_at)
    if (
        len(roles) != 1 or roles[0].code != "technician"
        or grant.user_id != command.actor_user_id
        or grant.scope_type != "person" or grant.scope_id != str(command.actor_person_id)
        or supply._as_utc(grant.valid_from) > when
        or (grant.valid_to is not None and supply._as_utc(grant.valid_to) <= when)
        or (grant.revoked_at is not None and supply._as_utc(grant.revoked_at) <= when)
    ):
        _invalid()


def _historical_projection(row, **changes):
    values = {column.key: getattr(row, column.key) for column in row.__table__.columns}
    return _HistoricalProjection(MappingProxyType({**values, **changes}))


def _verify_audit_links(db, audits) -> None:
    """Check membership edges without scanning an unbounded global stream."""

    heads = _rows(db, select(AuditChainHead).where(
        AuditChainHead.stream_key == supply.MATERIAL_REQUEST_AUDIT_STREAM,
    ))
    if len(heads) != 1 or heads[0].version < 1:
        _invalid()
    head = heads[0]
    versions = {head.version}
    for audit in audits:
        if audit.stream_version > head.version:
            _invalid()
        versions.add(audit.stream_version)
        if audit.stream_version > 1:
            versions.add(audit.stream_version - 1)
        if audit.stream_version < head.version:
            versions.add(audit.stream_version + 1)
    neighbors = _rows(db, select(AuditEvent).where(
        AuditEvent.stream_key == supply.MATERIAL_REQUEST_AUDIT_STREAM,
        AuditEvent.stream_version.in_(versions),
    ))
    by_version = {row.stream_version: row for row in neighbors}
    if len(neighbors) != len(versions) or set(by_version) != versions:
        _invalid()
    tail = by_version[head.version]
    if tail.id != head.last_event_id or tail.event_hash != head.last_hash:
        _invalid()
    for audit in audits:
        if by_version[audit.stream_version].id != audit.id:
            _invalid()
        previous_hash = None if audit.stream_version == 1 else by_version[audit.stream_version - 1].event_hash
        if audit.previous_hash != previous_hash:
            _invalid()
        if audit.stream_version < head.version and by_version[audit.stream_version + 1].previous_hash != audit.event_hash:
            _invalid()


def _verify_predecessor(result, audit, previous) -> None:
    if previous is None:
        expected_status = "open" if result.reference_no is None else "reference_registered"
        if result.action != "create_supply_task" or result.task_version != 0 or result.task_status != expected_status:
            _invalid()
        return
    if (
        result.action == "create_supply_task"
        or result.task_version != previous.task_version + 1
        or result.task_status not in supply._TASK_TRANSITIONS.get(previous.task_status, ())
        or _immutable(result) != _immutable(previous)
        or (result.action == "cancel_supply_task" and (
            result.reference_no != previous.reference_no
            or result.expected_date != previous.expected_date
        ))
        or audit.before_jsonb != {
            "schema": supply._AUDIT_SCHEMA,
            "request_id": str(previous.request_id),
            "supply_task_id": str(previous.supply_task_id),
            "task_no": previous.task_no,
            "task_status": previous.task_status,
            "task_version": previous.task_version,
            "reference_no": previous.reference_no,
            "expected_date": supply._date_token(previous.expected_date),
            "sensitive_fields": "excluded",
        }
    ):
        _invalid()


def _verify_transition(command, result, previous, events, used) -> None:
    key = supply._state_event_key(command.idempotency_key_hash, result.task_version)
    matches = tuple(row for row in events if row.idempotency_key == key or (
        isinstance(row.metadata_jsonb, Mapping) and row.metadata_jsonb.get("command_id") == str(command.id)
    ))
    before = previous.task_status if previous is not None else None
    if before == result.task_status:
        if matches:
            _invalid()
        return
    if len(matches) != 1:
        _invalid()
    event = matches[0]
    reason = "supply_task_created" if previous is None else (
        "supply_task_cancelled" if result.task_status == "cancelled" else "supply_task_status_changed"
    )
    if (
        event.id in used
        or event.aggregate_type != supply.SUPPLY_TASK_AGGREGATE
        or event.aggregate_id != str(result.supply_task_id)
        or event.from_status != before
        or event.to_status != result.task_status
        or event.reason != reason
        or event.idempotency_key != key
        or event.actor_id != command.actor_user_id
        or not supply._same_timestamp(event.occurred_at, command.occurred_at)
        or not supply._same_timestamp(event.created_at, command.created_at)
        or event.metadata_jsonb != {
            "schema": supply._STATE_SCHEMA,
            "request_id": str(result.request_id),
            "request_no": result.request_no,
            "revision_id": str(result.revision_id),
            "revision_no": result.revision_no,
            "request_line_id": str(result.request_line_id),
            "supply_task_id": str(result.supply_task_id),
            "task_no": result.task_no,
            "request_version": result.request_version,
            "task_version": result.task_version,
            "command_id": str(command.id),
            "idempotency_key_hash": command.idempotency_key_hash,
        }
    ):
        _invalid()
    used.add(event.id)


def _verify_task(task, first_command, first, last_command, last) -> None:
    if (
        (task.id, task.task_no, task.request_line_id, task.substitution_decision_id, task.supply_type, task.expected_qty, task.original_equivalent_qty) != _immutable(last)
        or (task.version, task.status, task.reference_no, task.expected_date) != (last.task_version, last.task_status, last.reference_no, last.expected_date)
        or task.created_by_user_id != first_command.actor_user_id
        or task.created_by_person_id != first_command.actor_person_id
        or task.created_role_assignment_id != first_command.actor_role_assignment_id
        or task.authorization_version != first_command.authorization_version
        or not supply._same_timestamp(task.created_at, first_command.occurred_at)
        or not supply._same_timestamp(task.updated_at, last_command.occurred_at)
    ):
        _invalid()
    if last.task_status == "cancelled":
        if task.cancelled_by_user_id != last_command.actor_user_id or not supply._same_timestamp(task.cancelled_at, last_command.occurred_at):
            _invalid()
    elif task.cancelled_at is not None or task.cancelled_by_user_id is not None:
        _invalid()


def _immutable(result):
    return (result.supply_task_id, result.task_no, result.request_line_id, result.substitution_decision_id, result.supply_type, result.expected_qty, result.original_equivalent_qty)


def _command_result(command):
    for value in (command.idempotency_key_hash, command.request_hash, command.result_hash):
        if not isinstance(value, str) or _HASH.fullmatch(value) is None:
            _invalid()
    return supply.validate_supply_task_command_result(command)


def _rows(db: Session, statement):
    return tuple(db.scalars(statement.execution_options(populate_existing=True)).all())


def _uuid(value):
    try:
        result = uuid.UUID(value) if isinstance(value, str) else None
        if result is None or result.int == 0 or str(result) != value:
            _invalid()
        return result
    except (ValueError, TypeError, AttributeError):
        _invalid()


def _forbidden():
    supply._fail("material_request_supply_manage_forbidden", "forbidden", "仅具有唯一全国范围供给管理授权的总部管理员可以恢复供给命令")


def _invalid():
    supply._fail("material_request_supply_command_status_evidence_invalid", "service_unavailable", "已观察到原请求，但供给命令证据或当前版本链不完整；请保持阻塞并重新查询")


__all__ = ["MaterialRequestSupplyCommandStatusResult", "material_request_supply_command_status"]
