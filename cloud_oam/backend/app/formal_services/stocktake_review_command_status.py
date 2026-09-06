"""Fail-closed, read-only recovery for non-opening review commands.

The review POST stores only a derived request reference in its immutable audit
event.  This reader accepts the original non-sensitive ``X-Request-ID``
sentinel, never accepts or reconstructs an idempotency key, and never flushes,
commits, rolls back, appends audit or replays a command.  It deliberately
keeps the review writer's owner-lock order and obtains the audit-chain proof
before returning a confirmed result.
"""

from __future__ import annotations

import re
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..formal_access import FormalPrincipal, lock_formal_principal_graph
from ..foundation_models import AuditEvent
from ..stocktake_models import (
    FormalStocktakeTask,
    StocktakeRound,
)
from ..stocktake_review_command_status_schemas import (
    StocktakeReviewCommandStatusOut,
    StocktakeReviewHistoricalCommandOut,
)
from . import stocktake_query as query
from . import stocktake_review as review
from .audit_chain import (
    _lock_audit_chain_head_with_proof,
    _verify_audit_event_with_prelocked_proof,
)
from .postgresql_lock_graph import lock_nonopening_stocktake_review_graph

_TRACE = re.compile(r"[A-Za-z0-9._:-]{8,160}", re.ASCII)
_STAGES = frozenset({review.REGION_STAGE, review.HEADQUARTERS_STAGE})
_NON_OPENING_TYPES = frozenset(review.NON_OPENING_TYPES)


class StocktakeReviewCommandStatusError(RuntimeError):
    def __init__(self, code: str, category: str, message: str):
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


def stocktake_review_command_status(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    review_stage: str,
    actor_person_id: uuid.UUID,
    actor_authorization_version: int,
    trace_request_id: str,
) -> StocktakeReviewCommandStatusOut:
    """Resolve one prior region/HQ review from its exact request sentinel."""

    _validate_input(
        task_id,
        round_id,
        review_stage,
        actor_person_id,
        actor_authorization_version,
        trace_request_id,
    )
    _validate_actor(actor, actor_person_id, actor_authorization_version)
    try:
        with db.no_autoflush:
            now = review._database_now(db)
            context = query._load_read_context(db, actor=actor, now=now)
            task = _visible_task(db, context, task_id)

            # Match the writer's order: task row, complete principal graph,
            # round row, then the migration-owned review graph.  This GET is
            # intentionally not a SQL READ ONLY transaction because these
            # locks are the concurrency boundary shared with the POST.
            task = db.scalar(
                select(FormalStocktakeTask)
                .where(FormalStocktakeTask.id == task_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if task is None or task.task_type not in _NON_OPENING_TYPES:
                _error("not_found", "not_found", "非期初盘点任务不存在")
            principal_ids = review._task_principal_user_ids(
                db, task.id, supplied_user_ids=(actor.user_id,)
            )
            lock_formal_principal_graph(db, principal_ids)
            round_row = db.scalar(
                select(StocktakeRound)
                .where(
                    StocktakeRound.id == round_id,
                    StocktakeRound.task_id == task.id,
                )
                .with_for_update()
                .execution_options(populate_existing=True)
            )
            if round_row is None:
                _error("not_found", "not_found", "盘点轮次不存在")
            lock_nonopening_stocktake_review_graph(db, task.id, round_row.id)

            current = review._require_current_actor(db, actor, review._database_now(db))
            context = query._load_read_context(db, actor=current, now=review._database_now(db))
            _visible_task(db, context, task_id)
            review._authorize_reviewer(
                db, current, task, review_stage, review._database_now(db), lock_rows=False
            )

            graph = query._load_task_graphs(db, (task,))[0]
            query._validate_graph(graph)
            target = tuple(
                row
                for row in graph.reviews
                if row.round_id == round_id and row.review_stage == review_stage
            )
            if len(target) > 1:
                _evidence("盘点复核命令事实重复")

            # The chain head is the final shared lock.  Nothing below may
            # acquire a new owner lock or issue a write.
            _head, proof = _lock_audit_chain_head_with_proof(
                db, stream_key=review.INVENTORY_STREAM_KEY
            )
            request_ref = review._request_reference(trace_request_id)
            action = f"stocktake.nonopening.{review_stage}_reviewed"
            audits = tuple(
                row
                for row in graph.review_audit_events
                if row.stream_key == review.INVENTORY_STREAM_KEY
                and row.action == action
                and row.aggregate_type == "stocktake_review"
                and row.request_id == request_ref
                and row.actor_user_id == current.user_id
            )
            target_audits = tuple(
                row
                for row in graph.review_audit_events
                if target
                and row.stream_key == review.INVENTORY_STREAM_KEY
                and row.action == action
                and row.aggregate_type == "stocktake_review"
                and row.aggregate_id == str(target[0].id)
                and row.actor_user_id == current.user_id
            )
            if not audits and (
                not target
                or (
                    len(target_audits) == 1
                    and target_audits[0].request_id != request_ref
                )
            ):
                _reread_actor_or_error(db, actor=current, initial=context)
                return _result(
                    task_id=task_id,
                    round_id=round_id,
                    review_stage=review_stage,
                    actor=current,
                    trace_request_id=trace_request_id,
                    command=None,
                )
            if len(target) != 1 or len(audits) != 1:
                _evidence("盘点复核命令审计事实缺失或不唯一")
            review_row = target[0]
            audit = audits[0]
            if audit.aggregate_id != str(review_row.id):
                _evidence("盘点复核命令审计坐标不一致")
            _verify_review_audit_with_proof(db, review_row, audit, proof)
            _verify_state_transition(graph, task, review_row)
            source_audit = _verify_source_difference_audit(
                db, graph, round_row, proof
            )
            _verify_review_manifest(graph, review_row, source_audit)
            _reread_actor_or_error(db, actor=current, initial=context)
            return _result(
                task_id=task_id,
                round_id=round_id,
                review_stage=review_stage,
                actor=current,
                trace_request_id=trace_request_id,
                command=_historical(review_row, graph),
            )
    except StocktakeReviewCommandStatusError:
        raise
    except (query.StocktakeReadError, review.StocktakeReviewError) as exc:
        if exc.category == "forbidden":
            _error("forbidden", "forbidden", "当前权限不允许查询该盘点复核命令")
        if exc.category == "not_found":
            _error("not_found", "not_found", "非期初盘点任务不存在")
        if exc.category == "precondition_failed":
            _error(
                "authorization_changed",
                "precondition_failed",
                "当前权限或盘点证据已变化，请重新查询",
            )
        _evidence("盘点复核证据无法完整核验")
    except Exception:  # noqa: BLE001 - all unexpected evidence failures fail closed
        _evidence("盘点复核证据不完整或存在歧义")


def _validate_input(task_id, round_id, stage, person_id, version, trace):
    for value in (task_id, round_id, person_id):
        if not isinstance(value, uuid.UUID) or value.int == 0:
            _error("input_invalid", "invalid_request", "盘点复核查询坐标无效")
    if stage not in _STAGES or type(version) is not int or version < 1:
        _error("input_invalid", "invalid_request", "盘点复核查询坐标无效")
    if not isinstance(trace, str) or _TRACE.fullmatch(trace) is None:
        _error("input_invalid", "invalid_request", "请求追踪号无效")


def _validate_actor(actor, person_id, version):
    if (
        not isinstance(actor, FormalPrincipal)
        or actor.person_id != person_id
        or actor.authorization_version != version
    ):
        _error(
            "authorization_changed",
            "precondition_failed",
            "当前人员或授权版本已变化，请重新查询",
        )
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _error(
            "forbidden", "forbidden", "当前账号或人员状态不允许查询盘点复核命令"
        )


def _visible_task(db, context, task_id):
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(
            FormalStocktakeTask.id == task_id,
            FormalStocktakeTask.task_type.in_(tuple(sorted(_NON_OPENING_TYPES))),
            query._visible_task_predicate(context),
        )
        .execution_options(populate_existing=True)
    )
    if task is None:
        _error("not_found", "not_found", "非期初盘点任务不存在")
    return task


def _verify_review_audit_with_proof(db, review_row, audit, proof):
    verified = _verify_audit_event_with_prelocked_proof(
        db,
        proof=proof,
        stream_key=review.INVENTORY_STREAM_KEY,
        event_id=audit.id,
    )
    if (
        verified.id != audit.id
        or audit.before_jsonb is not None
        or audit.actor_user_id != review_row.reviewer_user_id
        or _aware(audit.occurred_at) != _aware(review_row.reviewed_at)
        or audit.after_jsonb is None
        or audit.after_jsonb.get("review_id") != str(review_row.id)
        or audit.after_jsonb.get("decision_manifest_sha256")
        != review_row.decision_manifest_sha256
        or audit.after_jsonb.get("expected_task_version") != review_row.expected_task_version
        or audit.after_jsonb.get("resulting_task_version") != review_row.resulting_task_version
        or audit.after_jsonb.get("schema") != "cloud_oam.stocktake.nonopening_review_event.v2"
    ):
        _evidence("盘点复核审计摘要与复核事实不一致")


def _verify_state_transition(graph, task, review_row):
    rows = tuple(
        row
        for row in graph.state_transition_events
        if row.idempotency_key == f"stocktake-nonopening-review:state:{review_row.id}"
    )
    if len(rows) != 1:
        _evidence("盘点复核状态转换事实缺失或不唯一")
    row = rows[0]
    expected_status = review._resulting_status(review_row.review_stage, review_row.decision)
    previous_status = "submitted" if review_row.review_stage == review.REGION_STAGE else "hq_review"
    if (
        row.aggregate_type != "stocktake_task"
        or row.aggregate_id != str(task.id)
        or row.from_status != previous_status
        or row.to_status != expected_status
        or row.reason != f"nonopening_{review_row.review_stage}_review_{review_row.decision}"
        or row.actor_id != review_row.reviewer_user_id
        or _aware(row.occurred_at) != _aware(review_row.reviewed_at)
        or not isinstance(row.metadata_jsonb, dict)
        or row.metadata_jsonb.get("expected_task_version") != review_row.expected_task_version
        or row.metadata_jsonb.get("resulting_task_version") != review_row.resulting_task_version
        or row.metadata_jsonb.get("schema") != "cloud_oam.stocktake.nonopening_review_event.v2"
    ):
        _evidence("盘点复核状态转换摘要与复核事实不一致")


def _verify_source_difference_audit(db, graph, round_row, proof):
    action = (
        "stocktake.recount_difference_set.evaluated"
        if round_row.round_type == "recount"
        else "stocktake.initial_difference_set.evaluated"
    )
    events = tuple(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.stream_key == review.INVENTORY_STREAM_KEY,
                AuditEvent.action == action,
                AuditEvent.aggregate_type == "stocktake_round",
                AuditEvent.aggregate_id == str(round_row.id),
            )
            .order_by(AuditEvent.stream_version, AuditEvent.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(events) != 1:
        _evidence("盘点差异评估审计事实缺失或不唯一")
    event = events[0]
    _verify_audit_event_with_prelocked_proof(
        db,
        proof=proof,
        stream_key=review.INVENTORY_STREAM_KEY,
        event_id=event.id,
    )
    completion = next(
        (row for row in graph.difference_completions if row.round_id == round_row.id),
        None,
    )
    if completion is None or not isinstance(event.after_jsonb, dict):
        _evidence("盘点差异评估封印缺失")
    if (
        event.after_jsonb.get("difference_manifest_sha256")
        != completion.difference_manifest_sha256
        or event.after_jsonb.get("difference_count") != completion.difference_count
        or event.after_jsonb.get("round_status") != "submitted"
        or event.after_jsonb.get("task_id") != str(graph.task.id)
    ):
        _evidence("盘点差异评估审计摘要与封印不一致")
    return event


def _verify_review_manifest(graph, review_row, source_audit_event):
    differences = tuple(
        row for row in graph.differences if row.round_id == review_row.round_id
    )
    completion = next(
        (row for row in graph.difference_completions if row.round_id == review_row.round_id),
        None,
    )
    submission = next(
        (row for row in graph.submissions if row.round_id == review_row.round_id),
        None,
    )
    items = tuple(row for row in graph.review_items if row.review_id == review_row.id)
    if completion is None or submission is None or len(items) != len(differences):
        _evidence("盘点复核逐项事实或差异封印缺失")
    by_id = {row.difference_id: row for row in items}
    if set(by_id) != {row.id for row in differences}:
        _evidence("盘点复核逐项事实覆盖范围不完整")
    effective_scope_ids: tuple[uuid.UUID, ...] = ()
    effective_items: tuple[review.StocktakeEffectiveApprovalItemInput, ...] = ()
    if review_row.review_stage == review.HEADQUARTERS_STAGE and review_row.decision == "approve":
        approval = next(
            (
                row
                for row in graph.effective_approval_completions
                if row.terminal_headquarters_review_id == review_row.id
            ),
            None,
        )
        if approval is None:
            _evidence("总部通过缺少有效审批封印")
        approval_scope_ids = tuple(
            sorted(
                (
                    row.scope_id
                    for row in graph.effective_approval_scopes
                    if row.completion_id == approval.id
                ),
                key=str,
            )
        )
        # The writer permits an empty scope list only for a single initial
        # round; it expands that compatibility form to all persisted scopes
        # when sealing the effective approval.  For recounts, the original
        # command must have carried the complete explicit scope list.
        effective_scope_ids = (
            () if len(graph.rounds) == 1 else approval_scope_ids
        )
        effective_items = tuple(
            sorted(
                (
                    review.StocktakeEffectiveApprovalItemInput(
                        difference_id=row.difference_id,
                        decision=row.headquarters_decision,
                        comment=row.headquarters_comment,
                    )
                    for row in graph.effective_approval_items
                    if row.completion_id == approval.id
                    and row.source_round_id != review_row.round_id
                ),
                key=lambda row: str(row.difference_id),
            )
        )
    command = review.SubmitStocktakeReviewCommand(
        task_id=review_row.task_id,
        round_id=review_row.round_id,
        expected_task_version=review_row.expected_task_version,
        decision=review_row.decision,
        items=tuple(
            review.StocktakeReviewItemInput(
                difference_id=row.id,
                decision=by_id[row.id].decision,
                comment=by_id[row.id].comment,
            )
            for row in differences
        ),
        comment=review_row.comment,
        effective_scope_ids=effective_scope_ids,
        effective_items=effective_items,
    )
    expected = review._review_manifest(
        evidence=review.SealedNonOpeningDifferenceEvidence(
            task=graph.task,
            round_row=next(row for row in graph.rounds if row.id == review_row.round_id),
            scopes=tuple(graph.scopes),
            submission=submission,
            completion=completion,
            differences=differences,
            source_audit_event=source_audit_event,
        ),
        stage=review_row.review_stage,
        command=command,
        decisions={row.id: by_id[row.id].decision for row in differences},
    )
    if expected != review_row.decision_manifest_sha256:
        _evidence("盘点复核清单摘要无法重算")


def _historical(review_row, graph):
    differences = tuple(row for row in graph.differences if row.round_id == review_row.round_id)
    pending = sum(row.reason_code == "stocktake_pending_verification" for row in differences)
    return StocktakeReviewHistoricalCommandOut(
        review_id=review_row.id,
        task_id=review_row.task_id,
        round_id=review_row.round_id,
        review_stage=review_row.review_stage,
        decision=review_row.decision,
        resulting_task_status=review._resulting_status(
            review_row.review_stage, review_row.decision
        ),
        expected_task_version=review_row.expected_task_version,
        resulting_task_version=review_row.resulting_task_version,
        task_version=review_row.resulting_task_version,
        item_count=len(differences),
        pending_verification_count=pending,
        ready_for_posting=(
            review_row.review_stage == review.HEADQUARTERS_STAGE
            and review_row.decision == "approve"
            and pending == 0
        ),
        reviewed_at=_aware(review_row.reviewed_at),
    )


def _result(*, task_id, round_id, review_stage, actor, trace_request_id, command):
    return StocktakeReviewCommandStatusOut(
        task_id=task_id,
        round_id=round_id,
        review_stage=review_stage,
        actor_person_id=actor.person_id,
        actor_authorization_version=actor.authorization_version,
        trace_request_id=trace_request_id,
        lookup_status="confirmed" if command is not None else "not_observed",
        command=command,
    )


def _reread_actor_or_error(db, *, actor, initial):
    current = query._load_read_context(db, actor=actor, now=review._database_now(db))
    if (
        current.principal.user_id != initial.principal.user_id
        or current.principal.person_id != initial.principal.person_id
        or current.principal.authorization_version
        != initial.principal.authorization_version
        or current.principal.account_status != initial.principal.account_status
        or current.principal.employment_status != initial.principal.employment_status
        or current.principal.access_mode != initial.principal.access_mode
    ):
        _error(
            "authorization_changed",
            "precondition_failed",
            "当前权限版本已变化，请重新查询",
        )


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _evidence(message: str):
    _error("evidence_invalid", "service_unavailable", message)


def _error(code: str, category: str, message: str):
    raise StocktakeReviewCommandStatusError(
        "stocktake_review_command_status_" + code,
        category,
        message,
    )


__all__ = [
    "StocktakeReviewCommandStatusError",
    "stocktake_review_command_status",
]
