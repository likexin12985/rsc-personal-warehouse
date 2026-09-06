"""Fail-closed, read-only status lookup for non-opening stocktake posting.

The original POST stores only a derived request reference in the immutable
posting audit.  A caller can therefore recover a result with the exact
``X-Request-ID`` without ever resubmitting (or reconstructing) an
idempotency key.  Any incomplete graph is an evidence failure, never
``not_observed``.
"""

from __future__ import annotations

from datetime import datetime, timezone
import re
import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..formal_access import FormalPrincipal, lock_formal_principal_graph
from ..foundation_models import AuditEvent, StateTransitionEvent
from ..stocktake_models import (
    FormalStocktakeTask,
    StocktakeEffectiveApprovalCompletion,
    StocktakePostingCompletion,
    StocktakePostingCommandOutcome,
)
from ..stocktake_posting_command_status_schemas import (
    StocktakePostingCommandStatusOut,
    StocktakePostingHistoricalCommandOut,
    StocktakePostingSealedCommandOut,
)
from . import inventory_posting as inventory_service
from . import stocktake_close as close_service
from . import stocktake_posting as posting
from . import stocktake_posting_command_seal as seal_service
from . import stocktake_query as query
from .audit_chain import (
    _lock_audit_chain_head_with_proof,
    _verify_audit_event_with_prelocked_proof,
    calculate_audit_event_hash,
)
from .postgresql_lock_graph import lock_nonopening_stocktake_posting_graph


_TRACE = re.compile(r"[A-Za-z0-9._:-]{8,160}", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)


class StocktakePostingCommandStatusError(RuntimeError):
    def __init__(self, code: str, category: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.category = category
        self.message = message
        self.http_status_code = {
            "invalid_request": 400,
            "forbidden": 403,
            "not_found": 404,
            "conflict": 409,
            "precondition_failed": 412,
            "service_unavailable": 503,
        }[category]

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "category": self.category, "message": self.message}


def stocktake_posting_command_status(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
    actor_person_id: uuid.UUID,
    actor_authorization_version: int,
    trace_request_id: str,
) -> StocktakePostingCommandStatusOut:
    _validate_input(task_id, actor_person_id, actor_authorization_version, trace_request_id)
    _validate_actor(actor, actor_person_id, actor_authorization_version)
    try:
        with db.no_autoflush:
            now = posting._database_now(db)
            context = query._load_read_context(db, actor=actor, now=now)
            if not context.principal.allows(
                db,
                "stocktake",
                "post_difference",
                target_scope_type="national",
                target_scope_id="*",
            ):
                _error("forbidden", "forbidden", "当前正式权限不允许查询盘点过账命令")
            task = db.scalar(
                select(FormalStocktakeTask)
                .where(
                    FormalStocktakeTask.id == task_id,
                    FormalStocktakeTask.task_type.in_(tuple(posting.NON_OPENING_TYPES)),
                    query._visible_task_predicate(context),
                )
                .execution_options(populate_existing=True)
            )
            if task is None:
                _error("not_found", "not_found", "非期初盘点任务不存在")

            # Match the writer's owner order.  The lock graph is a SECURITY
            # DEFINER helper on PostgreSQL and a documented no-op on SQLite.
            inventory_service._lock_inventory_ledger_head_for_atomic_batch(db)
            lock_nonopening_stocktake_posting_graph(db, task_id)
            task = db.scalar(
                select(FormalStocktakeTask)
                .where(FormalStocktakeTask.id == task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if task is None or task.task_type not in posting.NON_OPENING_TYPES:
                _error("not_found", "not_found", "非期初盘点任务不存在")
            lock_formal_principal_graph(db, (actor.user_id,))

            request_ref = posting._request_reference(trace_request_id)
            outcomes = tuple(db.scalars(
                select(StocktakePostingCommandOutcome)
                .where(
                    StocktakePostingCommandOutcome.task_id == task_id,
                    StocktakePostingCommandOutcome.request_reference == request_ref,
                )
                .execution_options(populate_existing=True)
            ).all())
            if len(outcomes) > 1:
                _evidence("盘点过账命令封存事实不唯一")
            if outcomes:
                outcome = outcomes[0]
                if outcome.disposition != "sealed_not_executed":
                    _evidence("盘点过账命令封存事实类型无效")
                if (
                    outcome.expected_task_version < 0
                    or outcome.sealed_by_user_id != actor.user_id
                    or outcome.sealed_by_person_id != actor.person_id
                    or outcome.authorization_version != actor.authorization_version
                    or outcome.request_sha256 != seal_service._request_sha256(
                        task_id, outcome.expected_task_version, trace_request_id
                    )
                    or outcome.sealed_at is None
                    or outcome.created_at != outcome.sealed_at
                ):
                    _evidence("盘点过账封存事实绑定无效")
                _reread_current_actor_or_error(db, actor=actor, initial_context=context)
                return _result(
                    task_id=task_id,
                    actor_person_id=actor_person_id,
                    actor_authorization_version=actor_authorization_version,
                    trace_request_id=trace_request_id,
                    command=StocktakePostingSealedCommandOut(
                        seal_id=outcome.id,
                        task_id=outcome.task_id,
                        expected_task_version=outcome.expected_task_version,
                        actor_person_id=outcome.sealed_by_person_id,
                        actor_authorization_version=outcome.authorization_version,
                        trace_request_id=trace_request_id,
                        sealed_at=_aware(outcome.sealed_at),
                    ),
                    lookup_status="sealed_not_executed",
                )
            audits = tuple(
                db.scalars(
                    select(AuditEvent)
                    .where(
                        AuditEvent.stream_key == posting.INVENTORY_STREAM_KEY,
                        AuditEvent.action == "stocktake.nonopening.difference_posted",
                        AuditEvent.aggregate_type == "stocktake_posting_completion",
                        AuditEvent.request_id == request_ref,
                        AuditEvent.actor_user_id == actor.user_id,
                    )
                    .order_by(AuditEvent.id)
                    .execution_options(populate_existing=True)
                ).all()
            )
            if not audits:
                # An absent audit is only an unknown command result after the
                # same post-lock actor/principal revalidation used by the
                # confirmed path.  Returning here without this check would
                # allow a concurrent authorization revocation to be reported
                # as a clean ``not_observed`` result.
                _reread_current_actor_or_error(
                    db,
                    actor=actor,
                    initial_context=context,
                )
                return _result(
                    task_id=task_id,
                    actor_person_id=actor_person_id,
                    actor_authorization_version=actor_authorization_version,
                    trace_request_id=trace_request_id,
                    command=None,
                )
            if len(audits) != 1:
                _evidence("过账命令查询坐标对应了重复审计事实")
            audit = audits[0]
            completion_id = _uuid_from_text(audit.aggregate_id)
            completion_rows = tuple(
                db.scalars(
                    select(StocktakePostingCompletion)
                    .where(
                        StocktakePostingCompletion.id == completion_id,
                        StocktakePostingCompletion.task_id == task_id,
                    )
                    .execution_options(populate_existing=True)
                ).all()
            )
            if len(completion_rows) != 1:
                _evidence("盘点过账完成事实无法唯一核验")
            completion = completion_rows[0]
            if (
                completion.posted_by_user_id != actor.user_id
                or completion.posted_by_person_id != actor.person_id
                or completion.authorization_version != actor.authorization_version
            ):
                _error("authorization_changed", "precondition_failed", "原过账命令的人员或授权版本已变化")
            _verify_posting_audit(audit, completion, task)

            approvals = tuple(
                db.scalars(
                    select(StocktakeEffectiveApprovalCompletion)
                    .where(StocktakeEffectiveApprovalCompletion.task_id == task_id)
                    .execution_options(populate_existing=True)
                ).all()
            )
            if len(approvals) != 1:
                _evidence("总部最终审批封印无法唯一核验")
            approval = approvals[0]
            if completion.effective_approval_completion_id != approval.id:
                _evidence("过账与最终审批封印未绑定")

            _head, proof = _lock_audit_chain_head_with_proof(
                db, stream_key=posting.INVENTORY_STREAM_KEY
            )
            posting._verify_source_audits(db, task=task, approval=approval, proof=proof)
            # Reuse the close/reconciliation proof's immutable posted proxy.
            # A closed task has advanced its current version, but the original
            # posting completion remains anchored to its own posted version.
            # Replaying against the live closed task would confuse later
            # reconciliation/close increments with the posting command.
            close_service._reprove_posting(
                db,
                task=task,
                posting=completion,
                approval=approval,
                audit_proof=proof,
            )
            _verify_posting_transition(db, task, completion)
            _verify_audit_event_with_prelocked_proof(
                db,
                proof=proof,
                stream_key=posting.INVENTORY_STREAM_KEY,
                event_id=audit.id,
            )

            _reread_current_actor_or_error(
                db,
                actor=actor,
                initial_context=context,
            )
            return _result(
                task_id=task_id,
                actor_person_id=actor_person_id,
                actor_authorization_version=actor_authorization_version,
                trace_request_id=trace_request_id,
                command=StocktakePostingHistoricalCommandOut(
                    completion_id=completion.id,
                    task_id=completion.task_id,
                    terminal_round_id=completion.terminal_round_id,
                    resulting_task_status="posted",
                    task_version=completion.posted_task_version,
                    scope_count=completion.scope_count,
                    difference_count=completion.difference_count,
                    accepted_difference_count=completion.accepted_difference_count,
                    no_adjustment_count=completion.no_adjustment_count,
                    transaction_count=completion.transaction_count,
                    movement_count=completion.movement_count,
                    total_quantity=posting._canonical_quantity(completion.total_quantity),
                    first_ledger_cursor=completion.first_ledger_cursor,
                    last_ledger_cursor=completion.last_ledger_cursor,
                    posted_at=_aware(completion.posted_at),
                ),
            )
    except StocktakePostingCommandStatusError:
        raise
    except query.StocktakeReadError as exc:
        if exc.category == "forbidden":
            _error("forbidden", "forbidden", "当前正式权限不允许查询盘点过账命令")
        if exc.category == "not_found":
            _error("not_found", "not_found", "非期初盘点任务不存在")
        if exc.category == "precondition_failed":
            _error("authorization_changed", "precondition_failed", "当前权限版本已变化，请重新查询")
        _evidence("盘点过账任务读取期间发生变化，请保持阻塞并重新查询")
    except Exception:
        _evidence("盘点过账证据不完整或存在歧义，请保持恢复阻塞并重新查询")


def _validate_input(task_id, person_id, version, trace):
    if not isinstance(task_id, uuid.UUID) or task_id.int == 0 or not isinstance(person_id, uuid.UUID) or person_id.int == 0:
        _error("input_invalid", "invalid_request", "盘点过账查询坐标无效")
    if type(version) is not int or version < 1 or not isinstance(trace, str) or _TRACE.fullmatch(trace) is None:
        _error("input_invalid", "invalid_request", "盘点过账查询坐标无效")


def _validate_actor(actor, person_id, version):
    if not isinstance(actor, FormalPrincipal) or actor.person_id != person_id or actor.authorization_version != version:
        _error("authorization_changed", "precondition_failed", "当前人员或授权版本已变化，请重新查询")
    if actor.account_status != "active" or actor.employment_status != "active" or actor.access_mode != "active":
        _error("forbidden", "forbidden", "当前账号或人员状态不允许查询盘点过账命令")


def _reread_current_actor_or_error(db, *, actor, initial_context) -> None:
    """Revalidate identity and access after the writer-compatible lock graph."""

    fresh = query._load_read_context(db, actor=actor, now=posting._database_now(db))
    if (
        fresh.principal.user_id != initial_context.principal.user_id
        or fresh.principal.person_id != initial_context.principal.person_id
        or fresh.principal.authorization_version != initial_context.principal.authorization_version
        or fresh.principal.account_status != initial_context.principal.account_status
        or fresh.principal.employment_status != initial_context.principal.employment_status
        or fresh.principal.access_mode != initial_context.principal.access_mode
    ):
        _error("authorization_changed", "precondition_failed", "当前权限版本已变化，请重新查询")


def _verify_posting_audit(audit, completion, task):
    expected_after = posting._posting_event_metadata(completion)
    if (
        audit.aggregate_id != str(completion.id)
        or audit.actor_user_id != completion.posted_by_user_id
        or audit.before_jsonb != {"status": "approved", "task_version": completion.expected_task_version}
        or audit.after_jsonb != expected_after
        or _aware(audit.occurred_at) != _aware(completion.posted_at)
        or not _SHA256.fullmatch(audit.event_hash or "")
    ):
        _evidence("盘点过账审计事实绑定无效")
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
    if audit.event_hash != calculated:
        _evidence("盘点过账审计摘要无法核验")


def _verify_posting_transition(db, task, completion):
    rows = tuple(
        db.scalars(
            select(StateTransitionEvent)
            .where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id == str(task.id),
                StateTransitionEvent.idempotency_key == posting._event_key("state", completion.id),
            ).execution_options(populate_existing=True)
        ).all()
    )
    if len(rows) != 1:
        _evidence("盘点过账状态转换事实不唯一")
    row = rows[0]
    if (
        row.from_status != "approved" or row.to_status != "posted"
        or row.actor_id != completion.posted_by_user_id
        or row.reason != "nonopening_stocktake_difference_posted"
        or row.metadata_jsonb != posting._posting_event_metadata(completion)
        or _aware(row.occurred_at) != _aware(completion.posted_at)
    ):
        _evidence("盘点过账状态转换绑定无效")


def _result(*, task_id, actor_person_id, actor_authorization_version, trace_request_id, command, lookup_status=None):
    return StocktakePostingCommandStatusOut(
        task_id=task_id,
        actor_person_id=actor_person_id,
        actor_authorization_version=actor_authorization_version,
        trace_request_id=trace_request_id,
        operation="post_differences",
        lookup_status=lookup_status or ("confirmed" if command is not None else "not_observed"),
        command=command,
    )


def _uuid_from_text(value):
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        _evidence("盘点过账完成标识无效")
    if parsed.int == 0:
        _evidence("盘点过账完成标识无效")
    return parsed


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _evidence(message):
    _error("evidence_invalid", "service_unavailable", message)


def _error(code, category, message):
    raise StocktakePostingCommandStatusError(
        "stocktake_posting_command_status_" + code,
        category,
        message,
    )


__all__ = ["StocktakePostingCommandStatusError", "stocktake_posting_command_status"]
