"""Immutable difference evaluation for a submitted non-opening recount round.

The evaluator considers exactly the scopes selected by the round's immutable
recount case.  Each selected scope is rebuilt from the original cutoff through
its own server-sealed count cursor.  New differences supersede older
differences for those scopes by append-only round causality; no predecessor
row is updated or deleted.

The caller owns the transaction.  This module never commits or rolls back and
never writes inventory, balance, movement, review, recount, posting,
notification, outbox, or reconciliation facts.
"""

from __future__ import annotations

from .stocktake_count_history import CountHistoryContext

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import re
from typing import Final, Mapping, Sequence
import uuid

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..formal_access import (
    FormalPrincipal,
    ScopeGrant,
    lock_formal_principal_graph,
)
from ..foundation_models import AuditEvent, DocumentAttachment, FileObject, RoleAssignment
from ..inventory_models import StockAccount
from ..stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    StocktakeControlSnapshotLine,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeCountSerial,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakePosting,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)
from . import stocktake_count as count_service
from . import stocktake_difference as difference_service
from . import stocktake_recount_count as recount_count_service
from . import stocktake_recount as recount_service
from . import stocktake_review as review_service
from . import stocktake_task as task_service
from .audit_chain import (
    AuditChainError,
    _lock_audit_chain_head_with_proof,
    append_audit_event,
)
from .inventory_posting import INVENTORY_STREAM_KEY
from .postgresql_lock_graph import lock_nonopening_stocktake_review_graph


_NON_OPENING_TYPES: Final[frozenset[str]] = difference_service._NON_OPENING_TYPES
_ZERO: Final[Decimal] = Decimal("0.000")
_SAFE_TRACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,159}$", re.ASCII)
_PRINTABLE = re.compile(r"^[\x21-\x7e]{1,200}$", re.ASCII)
_PLACEHOLDERS = ("replace-with", "replace_me", "replace-me", "change-me", "changeme")
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class StocktakeRecountDifferenceError(RuntimeError):
    """Stable database-detail-free recount-difference failure."""

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
        return {"code": self.code, "category": self.category, "message": self.message}


@dataclass(frozen=True, slots=True)
class GenerateStocktakeRecountDifferenceCommand:
    task_id: uuid.UUID
    round_id: uuid.UUID
    expected_task_version: int


@dataclass(frozen=True, slots=True)
class StocktakeRecountDifferenceResult:
    task_id: uuid.UUID
    round_id: uuid.UUID
    recount_case_id: uuid.UUID
    completion_id: uuid.UUID
    task_status: str
    round_status: str
    task_version: int
    difference_status: str
    difference_count: int
    physical_difference_count: int
    pending_observation_difference_count: int
    total_affected_qty: Decimal
    difference_manifest_sha256: str
    selected_scope_count: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class SealedRecountDifferenceEvidence:
    task: FormalStocktakeTask
    round_row: StocktakeRound
    scopes: tuple[FormalStocktakeScope, ...]
    selected_scopes: tuple[FormalStocktakeScope, ...]
    assignment_graph: recount_count_service.SealedRecountAssignmentGraph
    submission: StocktakeRoundSubmission
    completion: StocktakeDifferenceSetCompletion
    differences: tuple[StocktakeDifference, ...]
    source_audit_event: AuditEvent


@dataclass(frozen=True, slots=True)
class _EvaluationInputs:
    scopes: tuple[FormalStocktakeScope, ...]
    selected_scopes: tuple[FormalStocktakeScope, ...]
    graph: recount_count_service.SealedRecountAssignmentGraph
    submission: StocktakeRoundSubmission
    plans: tuple[task_service._ScopePlan, ...]
    selected_plans: tuple[task_service._ScopePlan, ...]
    selected_snapshots: tuple[StocktakeSnapshotLine, ...]
    accounts: Mapping[uuid.UUID, StockAccount]
    count_lines: tuple[StocktakeCountLine, ...]
    count_serials: tuple[StocktakeCountSerial, ...]
    observations: tuple[StocktakeCountObservation, ...]
    completions: tuple[StocktakeScopeCountCompletion, ...]
    replay_evidence: difference_service._ReplayEvidence
    planned: tuple[difference_service._DifferencePlan, ...]


def generate_stocktake_recount_differences(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: GenerateStocktakeRecountDifferenceCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeRecountDifferenceResult:
    """Generate and seal one current recount round's selected-scope differences."""

    try:
        return _generate_stocktake_recount_differences(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    except StocktakeRecountDifferenceError:
        raise
    except (
        count_service.StocktakeCountError,
        difference_service.StocktakeDifferenceError,
        recount_count_service.StocktakeRecountCountError,
        recount_service.StocktakeRecountError,
        review_service.StocktakeReviewError,
        task_service.StocktakeTaskError,
    ):
        _fail(
            "stocktake_recount_difference_reference_graph_invalid",
            "service_unavailable",
            "复盘任务、计数边界或来源因果证据无法重证",
        )
    except AuditChainError:
        _fail(
            "stocktake_recount_difference_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，本次复盘差异评估未完成",
        )
    except IntegrityError:
        _fail(
            "stocktake_recount_difference_concurrent_conflict",
            "conflict",
            "复盘差异评估发生并发冲突，请回滚并重新读取",
        )
    except DBAPIError:
        _fail(
            "stocktake_recount_difference_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了复盘差异评估，请回滚并重新读取",
        )
    raise AssertionError("unreachable recount-difference boundary")


def _generate_stocktake_recount_differences(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: GenerateStocktakeRecountDifferenceCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeRecountDifferenceResult:
    supplied = difference_service._validate_supplied_actor(actor)
    checked = _validate_command(command)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    trace_id = _require_trace_request_id(trace_request_id)
    path = (
        f"/api/v1/stocktakes/{checked.task_id}/rounds/"
        f"{checked.round_id}/recount-differences"
    )
    key_hash = _idempotency_hmac(secret, supplied.user_id, path, raw_key)
    request_hash = _request_hmac(secret, supplied, checked)
    _take_advisory_locks(
        db,
        (
            _lock_coordinate("stocktake-recount-difference-idempotency", key_hash),
            _lock_coordinate("stocktake-recount-difference-task", str(checked.task_id)),
            _lock_coordinate("stocktake-recount-difference-round", str(checked.round_id)),
        ),
    )
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked.task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None or task.task_type not in _NON_OPENING_TYPES:
        _fail("stocktake_recount_difference_task_not_found", "not_found", "非期初盘点任务不存在")
    round_row = db.scalar(
        select(StocktakeRound)
        .where(StocktakeRound.id == checked.round_id, StocktakeRound.task_id == task.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if round_row is None:
        _fail("stocktake_recount_difference_round_not_found", "not_found", "复盘轮次不存在")
    lock_nonopening_stocktake_review_graph(db, task.id, round_row.id)
    principal_ids = {supplied.user_id}
    if round_row.recount_case_id is not None:
        principal_ids.update(
            db.scalars(
                select(StocktakeRecountScopeAssignment.assignee_user_id)
                .where(
                    StocktakeRecountScopeAssignment.recount_case_id
                    == round_row.recount_case_id
                )
            ).all()
        )
    lock_formal_principal_graph(db, tuple(sorted(principal_ids)))
    now = _database_now(db)
    current = difference_service._require_current_actor(db, supplied, now)
    evaluator_assignment, evaluator_grant = difference_service._authorize_evaluator(
        db, current, task, now
    )
    _validate_submitted_state(task, round_row, checked)
    inputs = _load_evaluation_inputs(db, task=task, round_row=round_row, now=now)

    existing_differences = tuple(
        db.scalars(
            count_service._select_only_reference_statement(
                db,
                select(StocktakeDifference)
                .where(
                    StocktakeDifference.task_id == task.id,
                    StocktakeDifference.round_id == round_row.id,
            )
            .order_by(StocktakeDifference.difference_no)
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    existing_completions = tuple(
        db.scalars(
            count_service._select_only_reference_statement(
                db,
                select(StocktakeDifferenceSetCompletion)
                .where(
                    or_(
                        StocktakeDifferenceSetCompletion.idempotency_key_hash == key_hash,
                        (
                            (StocktakeDifferenceSetCompletion.task_id == task.id)
                            & (StocktakeDifferenceSetCompletion.round_id == round_row.id)
                        ),
                    )
            )
            .order_by(StocktakeDifferenceSetCompletion.id)
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if existing_completions:
        if len(existing_completions) != 1:
            _evidence_invalid("复盘差异集封印不唯一")
        completion = existing_completions[0]
        if completion.idempotency_key_hash != key_hash or completion.request_sha256 != request_hash:
            _idempotency_conflict()
        _validate_stored_difference_set(
            task=task,
            round_row=round_row,
            inputs=inputs,
            completion=completion,
            differences=existing_differences,
            actor=current,
            assignment=evaluator_assignment,
            grant=evaluator_grant,
        )
        return replace(_result(task, round_row, inputs, completion), replayed=True)
    if existing_differences:
        _fail(
            "stocktake_recount_difference_partial_set_exists",
            "conflict",
            "当前复盘轮次存在未封印差异，请回滚并重新读取",
        )
    if db.scalar(
        select(StocktakeReview.id).where(
            StocktakeReview.task_id == task.id,
            StocktakeReview.round_id == round_row.id,
        )
    ) is not None or db.scalar(
        select(StocktakePosting.id).where(
            StocktakePosting.task_id == task.id,
            StocktakePosting.round_id == round_row.id,
        )
    ) is not None:
        _fail(
            "stocktake_recount_difference_downstream_started",
            "conflict",
            "当前复盘轮次的复核或过账已经开始",
        )

    _head, audit_proof = _lock_audit_chain_head_with_proof(
        db, stream_key=INVENTORY_STREAM_KEY
    )
    recount_count_service._verify_recount_source_audits(
        db, graph=inputs.graph, proof=audit_proof
    )
    now = _database_now(db)
    current = difference_service._require_current_actor(db, supplied, now)
    evaluator_assignment, evaluator_grant = difference_service._authorize_evaluator(
        db, current, task, now, lock_rows=False
    )
    _validate_submitted_state(task, round_row, checked)

    differences: list[StocktakeDifference] = []
    for number, plan in enumerate(inputs.planned, start=1):
        row = StocktakeDifference(
            id=uuid.uuid4(),
            task_id=task.id,
            round_id=round_row.id,
            scope_id=plan.scope_id,
            control_snapshot_line_id=None,
            difference_no=number,
            difference_type=plan.difference_type,
            material_id=plan.material_id,
            expected_account_id=plan.expected_account_id,
            observed_account_id=plan.observed_account_id,
            observed_line_id=plan.observed_line_id,
            serial_id=plan.serial_id,
            book_qty=plan.book_qty,
            counted_qty=plan.counted_qty,
            difference_qty=plan.difference_qty,
            affected_qty=plan.affected_qty,
            reason_code=plan.reason_code,
            reason_text=plan.reason_text,
            evidence_required=True,
            created_at=now,
        )
        db.add(row)
        differences.append(row)
    db.flush()
    summary = _difference_summary(task, round_row, inputs, differences)
    authorization_hash = difference_service._difference_authorization_sha256(
        current, evaluator_assignment, evaluator_grant, now
    )
    completion = StocktakeDifferenceSetCompletion(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        round_submission_id=inputs.submission.id,
        difference_count=summary["difference_count"],
        physical_difference_count=summary["physical_difference_count"],
        control_difference_count=0,
        pending_observation_difference_count=summary[
            "pending_observation_difference_count"
        ],
        total_affected_qty=summary["total_affected_qty"],
        difference_manifest_sha256=summary["difference_manifest_sha256"],
        request_sha256=request_hash,
        idempotency_key_hash=key_hash,
        completed_by_user_id=current.user_id,
        completed_by_person_id=current.person_id,
        completed_role_assignment_id=evaluator_assignment.id,
        authorization_version=current.authorization_version,
        role_code=evaluator_grant.role_code,
        scope_type=evaluator_grant.scope_type,
        scope_id_snapshot=evaluator_grant.scope_id,
        authorization_sha256=authorization_hash,
        completed_at=now,
        created_at=now,
    )
    db.add(completion)
    db.flush()
    append_audit_event(
        db,
        stream_key=INVENTORY_STREAM_KEY,
        actor_user_id=current.user_id,
        action="stocktake.recount_difference_set.evaluated",
        aggregate_type="stocktake_round",
        aggregate_id=str(round_row.id),
        before_jsonb={"difference_status": "not_evaluated", "status": "submitted"},
        after_jsonb={
            "control_difference_count": 0,
            "difference_count": completion.difference_count,
            "difference_manifest_sha256": completion.difference_manifest_sha256,
            "difference_status": "evaluated",
            "pending_observation_difference_count": completion.pending_observation_difference_count,
            "recount_case_id": str(inputs.graph.case.id),
            "round_status": "submitted",
            "selected_scope_count": len(inputs.selected_scopes),
            "source_difference_completion_id": str(
                inputs.graph.case.source_difference_completion_id
            ),
            "task_id": str(task.id),
            "task_status": "submitted",
            "task_version": task.version,
            "total_affected_qty": _canonical_quantity(completion.total_affected_qty),
        },
        request_id=_request_reference(trace_id),
        occurred_at=now,
    )
    db.flush()
    return _result(task, round_row, inputs, completion)


def _load_evaluation_inputs(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    now: datetime,
    history: CountHistoryContext | None = None,
) -> _EvaluationInputs:
    if history is not None:
        history.require(db, task, round_row)
    # Callers hold the 0032 task-local owner graph. Its immutable evidence is
    # SELECT-only for the API role; taking FOR UPDATE again would violate ACLs.
    scopes = tuple(
        db.scalars(
            count_service._select_only_reference_statement(
                db,
                select(FormalStocktakeScope)
                .where(FormalStocktakeScope.task_id == task.id)
                .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id)
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if not scopes:
        _evidence_invalid("盘点任务范围证据缺失")
    graph = recount_count_service._load_and_validate_recount_assignment_graph(
        db, task=task, round_row=round_row, scopes=scopes, history=history,
    )
    selected_scopes = graph.selected_scopes
    submissions = tuple(
        db.scalars(
            count_service._select_only_reference_statement(
                db,
                select(StocktakeRoundSubmission)
                .where(
                    StocktakeRoundSubmission.task_id == task.id,
                    StocktakeRoundSubmission.round_id == round_row.id,
            )
            .order_by(StocktakeRoundSubmission.id)
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(submissions) != 1:
        _evidence_invalid("已提交复盘轮次必须且只能存在一个轮次封印")
    submission = submissions[0]
    plans = history.plans if history is not None else tuple(task_service._load_and_validate_scope_plans(db, task, scopes, now=now))
    plan_by_scope = {row.scope_id: row for row in plans}
    selected_plans = tuple(plan_by_scope[row.id] for row in selected_scopes)
    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id == task.id)
            .order_by(InventoryFreeze.stocktake_scope_id, InventoryFreeze.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    freeze_by_scope = {row.stocktake_scope_id: row for row in freezes}
    for scope in selected_scopes:
        if history is None:
            recount_count_service._validate_recount_freeze(
                scope, plan_by_scope[scope.id].freeze_mode, freeze_by_scope.get(scope.id), now
            )
    if any(
        value is not None
        for value in (
            task.control_source_system_id,
            task.control_sync_run_id,
            task.control_snapshot_at,
            task.control_manifest_sha256,
        )
    ) or db.scalar(
        select(func.count())
        .select_from(StocktakeControlSnapshotLine)
        .where(StocktakeControlSnapshotLine.task_id == task.id)
    ):
        _evidence_invalid("非期初复盘禁止携带 OAM 控制总账证据")

    all_snapshots = tuple(
        db.scalars(
            count_service._select_only_reference_statement(
                db,
                select(StocktakeSnapshotLine)
                .where(StocktakeSnapshotLine.task_id == task.id)
                .order_by(StocktakeSnapshotLine.scope_id, StocktakeSnapshotLine.stock_account_id)
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    all_accounts = count_service._lock_snapshot_accounts(db, all_snapshots)
    count_service._validate_snapshot_manifest(task, plans, all_snapshots, all_accounts)
    selected_scope_ids = graph.selected_scope_ids
    selected_snapshots = tuple(
        row for row in all_snapshots if row.scope_id in selected_scope_ids
    )
    selected_accounts = {
        row.stock_account_id: all_accounts[row.stock_account_id]
        for row in selected_snapshots
    }
    count_lines = tuple(
        db.scalars(
            count_service._select_only_reference_statement(
                db,
                select(StocktakeCountLine)
                .where(
                    StocktakeCountLine.task_id == task.id,
                    StocktakeCountLine.round_id == round_row.id,
            )
            .order_by(StocktakeCountLine.scope_id, StocktakeCountLine.stock_account_id)
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    line_ids = tuple(row.id for row in count_lines)
    count_serials = tuple(
        db.scalars(
            count_service._select_only_reference_statement(
                db,
                select(StocktakeCountSerial)
                .where(StocktakeCountSerial.count_line_id.in_(line_ids))
                .order_by(StocktakeCountSerial.count_line_id, StocktakeCountSerial.serial_id)
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if line_ids else ()
    observations = tuple(
        db.scalars(
            count_service._select_only_reference_statement(
                db,
                select(StocktakeCountObservation)
                .where(
                    StocktakeCountObservation.task_id == task.id,
                    StocktakeCountObservation.round_id == round_row.id,
            )
            .order_by(StocktakeCountObservation.scope_id, StocktakeCountObservation.observation_no)
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    completions = tuple(
        db.scalars(
            count_service._select_only_reference_statement(
                db,
                select(StocktakeScopeCountCompletion)
                .where(
                    StocktakeScopeCountCompletion.task_id == task.id,
                    StocktakeScopeCountCompletion.round_id == round_row.id,
            )
            .order_by(StocktakeScopeCountCompletion.scope_id)
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    _validate_count_and_submission_manifests(
        db,
        task=task,
        round_row=round_row,
        graph=graph,
        selected_snapshots=selected_snapshots,
        accounts=selected_accounts,
        count_lines=count_lines,
        count_serials=count_serials,
        observations=observations,
        completions=completions,
        submission=submission,
        history=history,
    )
    replay_evidence = difference_service._replay_scope_expected_states(
        db,
        task=task,
        scopes=selected_scopes,
        plans=selected_plans,
        snapshots=selected_snapshots,
        snapshot_accounts=selected_accounts,
        observations=observations,
        completions=completions,
    )
    accounts = dict(replay_evidence.accounts)
    difference_service._lock_dimension_graph(
        db,
        task,
        selected_scopes,
        accounts,
        selected_snapshots,
        count_lines,
        count_serials,
        observations,
        replay_evidence=replay_evidence,
    )
    planned = difference_service._plan_difference_set(
        task=task,
        scopes=selected_scopes,
        snapshots=selected_snapshots,
        accounts=accounts,
        count_lines=count_lines,
        count_serials=count_serials,
        observations=observations,
        replay_evidence=replay_evidence,
    )
    return _EvaluationInputs(
        scopes=scopes,
        selected_scopes=selected_scopes,
        graph=graph,
        submission=submission,
        plans=plans,
        selected_plans=selected_plans,
        selected_snapshots=selected_snapshots,
        accounts=accounts,
        count_lines=count_lines,
        count_serials=count_serials,
        observations=observations,
        completions=completions,
        replay_evidence=replay_evidence,
        planned=planned,
    )


def _validate_count_and_submission_manifests(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    graph: recount_count_service.SealedRecountAssignmentGraph,
    selected_snapshots: Sequence[StocktakeSnapshotLine],
    accounts: Mapping[uuid.UUID, StockAccount],
    count_lines: Sequence[StocktakeCountLine],
    count_serials: Sequence[StocktakeCountSerial],
    observations: Sequence[StocktakeCountObservation],
    completions: Sequence[StocktakeScopeCountCompletion],
    submission: StocktakeRoundSubmission,
    history: CountHistoryContext | None = None,
) -> None:
    if history is not None:
        history.require(db, task, round_row, graph.selected_scopes)
    selected_scope_ids = graph.selected_scope_ids
    if (
        len(completions) != len(selected_scope_ids)
        or {row.scope_id for row in completions} != selected_scope_ids
        or any(row.scope_id not in selected_scope_ids for row in (*count_lines, *observations))
    ):
        _evidence_invalid("复盘计数证据必须且只能覆盖案例选中的范围")
    if task.cutoff_ledger_cursor is None or any(
        type(row.count_ledger_cursor) is not int
        or row.count_ledger_cursor < task.cutoff_ledger_cursor
        for row in completions
    ):
        _evidence_invalid("复盘范围缺少可信账本游标封印")
    snapshot_account_ids = {row.stock_account_id for row in selected_snapshots}
    if (
        len(count_lines) != len(selected_snapshots)
        or {row.stock_account_id for row in count_lines} != snapshot_account_ids
        or any(row.stock_account_id not in accounts for row in count_lines)
    ):
        _evidence_invalid("复盘未完整覆盖选中范围的截止快照账户")
    line_ids = {row.id for row in count_lines}
    if any(
        row.count_line_id not in line_ids or row.round_id != round_row.id
        for row in count_serials
    ):
        _evidence_invalid("复盘 SN 引用图不完整")
    resolved_observation_serials = [
        row.serial_id for row in observations if row.serial_id is not None
    ]
    count_serial_ids = [row.serial_id for row in count_serials]
    if (
        len(set(count_serial_ids)) != len(count_serial_ids)
        or len(set(resolved_observation_serials)) != len(resolved_observation_serials)
        or set(resolved_observation_serials).intersection(count_serial_ids)
    ):
        _evidence_invalid("同一 SN 在复盘轮次中重复出现")

    lines_by_scope: dict[uuid.UUID, list[StocktakeCountLine]] = defaultdict(list)
    observations_by_scope: dict[uuid.UUID, list[StocktakeCountObservation]] = defaultdict(list)
    serials_by_line: dict[uuid.UUID, list[StocktakeCountSerial]] = defaultdict(list)
    for row in count_lines:
        lines_by_scope[row.scope_id].append(row)
    for row in observations:
        observations_by_scope[row.scope_id].append(row)
    for row in count_serials:
        serials_by_line[row.count_line_id].append(row)
    completion_ids = tuple(str(row.id) for row in completions)
    # 0057 serializes binding insertion on the task owner and old bindings are
    # immutable. Preserve exact manifest validation and real FileObject locks.
    attachments = tuple(
        db.scalars(
            count_service._select_only_reference_statement(
                db,
                select(DocumentAttachment)
                .where(
                    DocumentAttachment.document_type == "stocktake_scope_count_completion",
                    DocumentAttachment.document_id.in_(completion_ids),
                    DocumentAttachment.attachment_type == "stocktake_evidence",
            )
            .order_by(DocumentAttachment.document_id, DocumentAttachment.file_id)
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if completion_ids else ()
    attachments_by_completion: dict[str, list[DocumentAttachment]] = defaultdict(list)
    for row in attachments:
        if row.status != "active":
            _evidence_invalid("复盘附件证据已失效")
        attachments_by_completion[row.document_id].append(row)
    file_ids = tuple(sorted({row.file_id for row in attachments}, key=str))
    files = history.files_for(db, task, round_row, file_ids) if history is not None else tuple(
        db.scalars(
            select(FileObject)
            .where(FileObject.id.in_(file_ids))
            .order_by(FileObject.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    ) if file_ids else ()
    if len(files) != len(file_ids):
        _evidence_invalid("复盘附件文件缺失")
    files_by_id = {row.id: row for row in files}
    for completion in completions:
        scope_lines = tuple(
            sorted(lines_by_scope[completion.scope_id], key=lambda row: str(row.stock_account_id))
        )
        scope_observations = tuple(
            sorted(observations_by_scope[completion.scope_id], key=lambda row: row.observation_no)
        )
        scope_files = tuple(
            files_by_id[row.file_id]
            for row in attachments_by_completion.get(str(completion.id), ())
        )
        if any(
            row.status != "available"
            or row.uploaded_by != completion.completed_by_user_id
            or not _SHA256.fullmatch(row.sha256)
            or row.size_bytes < 0
            for row in scope_files
        ):
            _evidence_invalid("复盘附件文件证据无效")
        serial_count = sum(len(serials_by_line[row.id]) for row in scope_lines) + sum(
            1 for row in scope_observations if row.serial_id is not None
        )
        total = count_service._quantity_sum(
            tuple(row.counted_qty for row in scope_lines)
            + tuple(row.counted_qty for row in scope_observations)
        )
        expected_manifest = count_service._scope_evidence_manifest(
            db,
            completion_id=completion.id,
            task_id=task.id,
            round_id=round_row.id,
            scope_id=completion.scope_id,
            lines=scope_lines,
            observations=scope_observations,
            files=scope_files,
            authorization_sha256=completion.authorization_sha256,
            count_ledger_cursor=completion.count_ledger_cursor,
        )
        difference_service._validate_historical_authorization(db, completion)
        if (
            completion.count_line_count != len(scope_lines)
            or completion.observation_line_count != len(scope_observations)
            or completion.serial_count != serial_count
            or completion.total_counted_qty != total
            or completion.zero_confirmed != (not scope_lines and not scope_observations)
            or completion.evidence_manifest_sha256 != expected_manifest
        ):
            _evidence_invalid("复盘范围实盘封印无法重算")
    sealed_submission = recount_count_service._validate_sealed_recount_submission(
        db, task, round_row, graph
    )
    if sealed_submission.id != submission.id:
        _evidence_invalid("复盘轮次封印身份不一致")


def _validate_submitted_state(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    command: GenerateStocktakeRecountDifferenceCommand,
) -> None:
    if task.version != command.expected_task_version:
        _fail(
            "stocktake_recount_difference_version_conflict",
            "conflict",
            "盘点任务版本已变化，请重新读取",
        )
    if (
        task.task_type not in _NON_OPENING_TYPES
        or task.status != "submitted"
        or round_row.status != "submitted"
        or round_row.round_no <= 1
        or round_row.round_type != "recount"
        or round_row.recount_case_id is None
        or task.current_round_no != round_row.round_no
        or task.submitted_at is None
        or round_row.submitted_at is None
        or round_row.submitted_by_user_id is None
        or round_row.count_manifest_sha256 is None
        or _as_utc(task.submitted_at) != _as_utc(round_row.submitted_at)
    ):
        _fail(
            "stocktake_recount_difference_state_invalid",
            "precondition_failed",
            "当前复盘轮次尚未完整提交或已进入后续状态",
        )


def _difference_summary(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    inputs: _EvaluationInputs,
    differences: Sequence[StocktakeDifference],
) -> dict[str, object]:
    ordered = tuple(sorted(differences, key=lambda row: row.difference_no))
    predecessor_ids = [
        str(row.id)
        for row in inputs.graph.source_evidence.differences
        if row.scope_id in inputs.graph.selected_scope_ids
    ]
    evidence = {
        "differences": [difference_service._difference_manifest_row(row) for row in ordered],
        "recount_case_id": str(inputs.graph.case.id),
        "round_id": str(round_row.id),
        "round_submission_id": str(inputs.submission.id),
        "schema": "cloud_oam.stocktake.recount_difference_set.v1",
        "selected_scope_ids": [str(row.id) for row in inputs.selected_scopes],
        "source_difference_completion_id": str(
            inputs.graph.case.source_difference_completion_id
        ),
        "source_round_id": str(inputs.graph.case.source_round_id),
        "superseded_predecessor_difference_ids": predecessor_ids,
        "task_id": str(task.id),
    }
    return {
        "difference_count": len(ordered),
        "physical_difference_count": len(ordered),
        "pending_observation_difference_count": sum(
            1 for row in ordered if row.reason_code == "stocktake_pending_verification"
        ),
        "total_affected_qty": sum((row.affected_qty for row in ordered), start=_ZERO),
        "difference_manifest_sha256": _sha256(evidence),
    }


def _validate_stored_difference_set(
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    inputs: _EvaluationInputs,
    completion: StocktakeDifferenceSetCompletion,
    differences: Sequence[StocktakeDifference],
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    grant: ScopeGrant,
) -> None:
    if len(differences) != len(inputs.planned):
        _evidence_invalid("已封印复盘差异集与实盘证据不一致")
    for number, (row, plan) in enumerate(
        zip(differences, inputs.planned, strict=True), start=1
    ):
        if row.difference_no != number or difference_service._stored_plan(row) != plan:
            _evidence_invalid("已封印复盘差异明细无法从账本和实盘重算")
    summary = _difference_summary(task, round_row, inputs, differences)
    if (
        completion.task_id != task.id
        or completion.round_id != round_row.id
        or completion.round_submission_id != inputs.submission.id
        or completion.difference_count != summary["difference_count"]
        or completion.physical_difference_count != summary["physical_difference_count"]
        or completion.control_difference_count != 0
        or completion.pending_observation_difference_count
        != summary["pending_observation_difference_count"]
        or completion.total_affected_qty != summary["total_affected_qty"]
        or completion.difference_manifest_sha256 != summary["difference_manifest_sha256"]
        or completion.completed_by_user_id != actor.user_id
        or completion.completed_by_person_id != actor.person_id
        or completion.completed_role_assignment_id != assignment.id
        or completion.authorization_version != actor.authorization_version
        or completion.role_code != grant.role_code
        or completion.scope_type != grant.scope_type
        or completion.scope_id_snapshot != grant.scope_id
        or completion.authorization_sha256
        != difference_service._difference_authorization_sha256(
            actor, assignment, grant, completion.completed_at
        )
        or _as_utc(completion.completed_at) < _as_utc(inputs.submission.submitted_at)
        or _as_utc(completion.created_at) != _as_utc(completion.completed_at)
    ):
        _evidence_invalid("已封印复盘差异集汇总或授权无法重算")


def _load_and_validate_sealed_recount_difference_evidence(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    now: datetime,
    history: CountHistoryContext | None = None,
) -> SealedRecountDifferenceEvidence:
    """Reprove a recount difference seal for review/recount consumers."""

    if (
        task.task_type not in _NON_OPENING_TYPES
        or round_row.task_id != task.id
        or round_row.round_no <= 1
        or round_row.round_type != "recount"
        or round_row.recount_case_id is None
        or round_row.status != "submitted"
        or round_row.submitted_at is None
        or round_row.submitted_by_user_id is None
        or round_row.count_manifest_sha256 is None
        or task.submitted_at is None
        or task.current_round_no < round_row.round_no
        or (
            task.current_round_no == round_row.round_no
            and _as_utc(task.submitted_at) != _as_utc(round_row.submitted_at)
        )
    ):
        _fail(
            "stocktake_recount_difference_source_round_invalid",
            "precondition_failed",
            "复盘复核必须基于任务当前已提交复盘轮次",
        )
    if history is not None:
        history.require(db, task, round_row)
    if history is None and db.scalar(
        select(func.count())
        .select_from(StocktakePosting)
        .where(StocktakePosting.task_id == task.id, StocktakePosting.round_id == round_row.id)
    ):
        _fail(
            "stocktake_recount_difference_source_already_posted",
            "precondition_failed",
            "该复盘轮次已经存在过账事实",
        )
    inputs = _load_evaluation_inputs(db, task=task, round_row=round_row, now=now, history=history)
    completions = tuple(
        db.scalars(
            select(StocktakeDifferenceSetCompletion)
            .where(
                StocktakeDifferenceSetCompletion.task_id == task.id,
                StocktakeDifferenceSetCompletion.round_id == round_row.id,
            )
            .order_by(StocktakeDifferenceSetCompletion.id)
        ).all()
    )
    differences = tuple(
        db.scalars(
            select(StocktakeDifference)
            .where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.round_id == round_row.id,
            )
            .order_by(StocktakeDifference.difference_no)
        ).all()
    )
    if len(completions) != 1 or [row.difference_no for row in differences] != list(
        range(1, len(differences) + 1)
    ):
        _evidence_invalid("复盘差异集封印缺失、不唯一或序号不连续")
    completion = completions[0]
    if len(differences) != len(inputs.planned) or any(
        row.difference_no != number or difference_service._stored_plan(row) != plan
        for number, (row, plan) in enumerate(
            zip(differences, inputs.planned, strict=True), start=1
        )
    ):
        _evidence_invalid("复盘差异无法从选中范围实盘证据完整重算")
    summary = _difference_summary(task, round_row, inputs, differences)
    if (
        completion.round_submission_id != inputs.submission.id
        or completion.difference_count != summary["difference_count"]
        or completion.physical_difference_count != summary["physical_difference_count"]
        or completion.control_difference_count != 0
        or completion.pending_observation_difference_count
        != summary["pending_observation_difference_count"]
        or completion.total_affected_qty != summary["total_affected_qty"]
        or completion.difference_manifest_sha256 != summary["difference_manifest_sha256"]
        or not _SHA256.fullmatch(completion.request_sha256)
        or not _SHA256.fullmatch(completion.idempotency_key_hash)
        or _as_utc(completion.created_at) != _as_utc(completion.completed_at)
        or _as_utc(completion.completed_at) < _as_utc(inputs.submission.submitted_at)
    ):
        _evidence_invalid("复盘差异集封印汇总或时间顺序无效")
    review_service._validate_difference_evaluator_authorization(
        db, task, completion
    )
    source_events = tuple(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.stream_key == INVENTORY_STREAM_KEY,
                AuditEvent.action == "stocktake.recount_difference_set.evaluated",
                AuditEvent.aggregate_type == "stocktake_round",
                AuditEvent.aggregate_id == str(round_row.id),
            )
            .order_by(AuditEvent.stream_version)
        ).all()
    )
    if len(source_events) != 1:
        _evidence_invalid("复盘差异评估审计事实缺失或不唯一")
    after = source_events[0].after_jsonb
    if (
        not isinstance(after, dict)
        or after.get("difference_manifest_sha256")
        != completion.difference_manifest_sha256
        or after.get("difference_count") != completion.difference_count
        or after.get("recount_case_id") != str(inputs.graph.case.id)
        or after.get("selected_scope_count") != len(inputs.selected_scopes)
        or after.get("round_status") != "submitted"
        or after.get("task_id") != str(task.id)
    ):
        _evidence_invalid("复盘差异评估审计摘要与封印不一致")
    return SealedRecountDifferenceEvidence(
        task=task,
        round_row=round_row,
        scopes=inputs.scopes,
        selected_scopes=inputs.selected_scopes,
        assignment_graph=inputs.graph,
        submission=inputs.submission,
        completion=completion,
        differences=differences,
        source_audit_event=source_events[0],
    )


def _result(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    inputs: _EvaluationInputs,
    completion: StocktakeDifferenceSetCompletion,
) -> StocktakeRecountDifferenceResult:
    return StocktakeRecountDifferenceResult(
        task_id=task.id,
        round_id=round_row.id,
        recount_case_id=inputs.graph.case.id,
        completion_id=completion.id,
        task_status=task.status,
        round_status=round_row.status,
        task_version=task.version,
        difference_status="evaluated",
        difference_count=completion.difference_count,
        physical_difference_count=completion.physical_difference_count,
        pending_observation_difference_count=completion.pending_observation_difference_count,
        total_affected_qty=completion.total_affected_qty,
        difference_manifest_sha256=completion.difference_manifest_sha256,
        selected_scope_count=len(inputs.selected_scopes),
    )


def _validate_command(
    command: GenerateStocktakeRecountDifferenceCommand,
) -> GenerateStocktakeRecountDifferenceCommand:
    if not isinstance(command, GenerateStocktakeRecountDifferenceCommand):
        _fail("stocktake_recount_difference_command_invalid", "invalid_request", "复盘差异评估命令无效")
    task_id = difference_service._require_uuid("task_id", command.task_id)
    round_id = difference_service._require_uuid("round_id", command.round_id)
    if (
        not isinstance(command.expected_task_version, int)
        or isinstance(command.expected_task_version, bool)
        or command.expected_task_version < 0
    ):
        _fail("stocktake_recount_difference_version_invalid", "invalid_request", "任务期望版本无效")
    return GenerateStocktakeRecountDifferenceCommand(
        task_id=task_id,
        round_id=round_id,
        expected_task_version=command.expected_task_version,
    )


def _request_hmac(
    secret: bytes,
    actor: FormalPrincipal,
    command: GenerateStocktakeRecountDifferenceCommand,
) -> str:
    return _hmac_hex(
        secret,
        {
            "actor_authorization_version": actor.authorization_version,
            "actor_person_id": str(actor.person_id),
            "actor_user_id": actor.user_id,
            "expected_task_version": command.expected_task_version,
            "round_id": str(command.round_id),
            "schema": "cloud_oam.stocktake.recount_difference_request.v1",
            "task_id": str(command.task_id),
        },
    )


def _idempotency_hmac(
    secret: bytes, actor_user_id: str, path: str, raw_key: str
) -> str:
    return _hmac_hex(
        secret,
        {
            "actor_user_id": actor_user_id,
            "idempotency_key": raw_key,
            "method": "POST",
            "path": path,
            "schema": "cloud_oam.stocktake.recount_difference_idempotency.v1",
        },
    )


def _require_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not _PRINTABLE.fullmatch(value):
        _fail("stocktake_recount_difference_idempotency_key_invalid", "invalid_request", "Idempotency-Key 无效")
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    secret = value.encode("utf-8") if isinstance(value, str) else value
    if (
        not isinstance(secret, bytes)
        or len(secret) < 32
        or any(marker in secret.lower() for marker in (item.encode() for item in _PLACEHOLDERS))
    ):
        _fail(
            "stocktake_recount_difference_hmac_unavailable",
            "service_unavailable",
            "复盘差异幂等 HMAC 配置不可用",
        )
    return secret


def _require_trace_request_id(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not _SAFE_TRACE.fullmatch(value):
        _fail("stocktake_recount_difference_trace_id_invalid", "invalid_request", "请求追踪标识无效")
    return value


def _lock_coordinate(namespace: str, value: str) -> int:
    digest = hashlib.sha256(
        f"cloud_oam.inventory.lock.v1\0{namespace}\0{value}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _take_advisory_locks(db: Session, coordinates: Sequence[int]) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for coordinate in sorted(set(coordinates)):
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": coordinate})


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(func.current_timestamp()))
    if not isinstance(value, datetime):
        _fail("stocktake_recount_difference_clock_unavailable", "service_unavailable", "数据库时间不可用")
    return _as_utc(value)


def _request_reference(raw: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.stocktake.recount_difference.trace.v1\0{raw}".encode("utf-8")
    ).hexdigest()
    return f"stocktake-recount-difference-{digest}"


def _canonical_quantity(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.001")), "f")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _hmac_hex(secret: bytes, document: Mapping[str, object]) -> str:
    return hmac.new(secret, _canonical_bytes(document), hashlib.sha256).hexdigest()


def _sha256(document: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_bytes(document)).hexdigest()


def _canonical_bytes(document: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        _fail("stocktake_recount_difference_manifest_invalid", "invalid_request", "复盘差异清单无法规范化")


def _idempotency_conflict() -> None:
    _fail(
        "stocktake_recount_difference_idempotency_conflict",
        "conflict",
        "幂等键或复盘轮次已绑定不同差异评估",
    )


def _evidence_invalid(message: str) -> None:
    _fail("stocktake_recount_difference_evidence_invalid", "service_unavailable", message)


def _fail(code: str, category: str, message: str) -> None:
    raise StocktakeRecountDifferenceError(code, category, message)


__all__ = [
    "GenerateStocktakeRecountDifferenceCommand",
    "SealedRecountDifferenceEvidence",
    "StocktakeRecountDifferenceError",
    "StocktakeRecountDifferenceResult",
    "generate_stocktake_recount_differences",
]
