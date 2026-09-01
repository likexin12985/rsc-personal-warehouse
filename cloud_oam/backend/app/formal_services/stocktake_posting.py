"""Fail-closed planning and atomic posting for non-opening stocktakes.

The module contains two deliberately distinct boundaries.  The private planner
is pure: it writes no balance, transaction, posting, task, freeze, notification
or reconciliation row.  The public posting command re-proves that plan under
the canonical ledger-first lock order and performs the caller-owned local
database transaction.  Neither boundary calls an external system.

Keeping the planner separate is important for two reasons:

* a partial recount makes the effective round a per-scope fact rather than a
  single ``task.current_round_no`` shortcut; and
* one approved task can require four independent immutable inventory
  transaction types (gain, loss, transfer and status change), while
  ``no_adjustment`` and a zero-difference task still require a durable
  completion fact without inventing a movement.

The public boundary installed by revision 0035 consumes only the immutable
task-wide headquarters approval seal.  It posts one union-locked inventory
batch, records a separate immutable posting completion, releases the task's
exact freezes and moves only ``approved -> posted``.  Notification,
reconciliation and ``posted -> closed`` deliberately remain separate.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import re
from typing import Final, Mapping, NoReturn, Sequence
import uuid

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    lock_formal_principal_graph,
)
from ..foundation_models import AuditEvent, RoleAssignment, StateTransitionEvent
from ..inventory_models import (
    InventoryMovement,
    InventoryMovementSerial,
    InventoryTransaction,
    StockAccount,
)
from ..stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    StocktakeCountObservation,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakeEffectiveApprovalCompletion,
    StocktakeEffectiveApprovalItem,
    StocktakeEffectiveApprovalScope,
    StocktakePosting,
    StocktakePostingCompletion,
    StocktakePostingCompletionItem,
    StocktakePostingItem,
    StocktakeRecountCase,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeReviewItem,
    StocktakeRound,
    StocktakeRoundSubmission,
)
from . import inventory_posting as inventory_service
from .audit_chain import (
    AuditChainError,
    _lock_audit_chain_head_with_proof,
    _verify_audit_event_with_prelocked_proof,
    append_audit_event,
)
from .postgresql_lock_graph import lock_nonopening_stocktake_posting_graph


NON_OPENING_TYPES: Final[frozenset[str]] = frozenset(
    {"full", "sample", "ad_hoc", "personal", "termination"}
)
POSTABLE_ITEM_DECISIONS: Final[frozenset[str]] = frozenset(
    {"accept_for_posting", "no_adjustment"}
)
RECOUNT_ITEM_DECISIONS: Final[frozenset[str]] = frozenset(
    {"pending_verification", "recount"}
)
MOVEMENT_TYPES: Final[tuple[str, ...]] = (
    "stocktake_gain",
    "stocktake_loss",
    "transfer",
    "status_change",
)
EXTERNAL_BOUNDARY_CODE: Final[str] = "stocktake-difference"
INVENTORY_STREAM_KEY: Final[str] = "inventory"
FREEZE_RELEASE_REASON: Final[str] = "非期初盘点差异已安全过账并释放冻结"
POSTING_KIND_BY_MOVEMENT: Final[Mapping[str, str]] = {
    "stocktake_gain": "difference_gain",
    "stocktake_loss": "difference_loss",
    "transfer": "difference_transfer",
    "status_change": "difference_status_change",
}

_ZERO = Decimal("0.000")
_ONE = Decimal("1.000")
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_PRINTABLE = re.compile(r"^[\x21-\x7e]+$", re.ASCII)
_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class StocktakeDifferencePostingError(RuntimeError):
    """Stable failure for the non-opening posting planner."""

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
class EffectiveScopeRound:
    """The latest causally selected, independently approved round for a scope."""

    scope_id: uuid.UUID
    round_id: uuid.UUID
    round_no: int
    difference_completion_id: uuid.UUID
    regional_review_id: uuid.UUID
    effective_approval_completion_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class EffectiveApprovalCompletionEvidence:
    """Task-wide terminal seal created by the final HQ review action.

    These fields intentionally mirror the minimum coordinates required from
    the future immutable database table.  The planner recomputes the manifest;
    a caller cannot make an old top-level ``recount`` look like HQ approval by
    merely supplying a source-round review row.
    """

    id: uuid.UUID
    task_id: uuid.UUID
    terminal_round_id: uuid.UUID
    terminal_headquarters_review_id: uuid.UUID
    scope_count: int
    difference_count: int
    approval_manifest_sha256: str


@dataclass(frozen=True, slots=True)
class EffectiveApprovalScopeEvidence:
    """One scope's latest causal round selected by the task-wide HQ seal."""

    completion_id: uuid.UUID
    scope_id: uuid.UUID
    source_round_id: uuid.UUID
    source_difference_completion_id: uuid.UUID
    regional_review_id: uuid.UUID
    difference_count: int


@dataclass(frozen=True, slots=True)
class EffectiveApprovalItemEvidence:
    """Explicit regional and final-HQ decisions for one effective difference."""

    completion_id: uuid.UUID
    scope_id: uuid.UUID
    source_round_id: uuid.UUID
    difference_id: uuid.UUID
    regional_review_id: uuid.UUID
    regional_decision: str
    headquarters_decision: str


@dataclass(frozen=True, slots=True)
class PlannedStocktakeMovement:
    """One immutable movement bound one-to-one to an accepted difference."""

    difference_id: uuid.UUID
    scope_id: uuid.UUID
    round_id: uuid.UUID
    movement_type: str
    from_account_id: uuid.UUID | None
    to_account_id: uuid.UUID | None
    quantity: Decimal
    serial_ids: tuple[uuid.UUID, ...]
    external_boundary_code: str | None


@dataclass(frozen=True, slots=True)
class StocktakeDifferencePostingPlan:
    """Canonical plan to be sealed by the future atomic posting completion."""

    task_id: uuid.UUID
    expected_task_version: int
    effective_scope_rounds: tuple[EffectiveScopeRound, ...]
    movements: tuple[PlannedStocktakeMovement, ...]
    no_adjustment_difference_ids: tuple[uuid.UUID, ...]
    effective_difference_count: int
    plan_manifest_sha256: str

    def movements_for(self, movement_type: str) -> tuple[PlannedStocktakeMovement, ...]:
        if movement_type not in MOVEMENT_TYPES:
            raise ValueError("unsupported stocktake movement type")
        return tuple(row for row in self.movements if row.movement_type == movement_type)


@dataclass(frozen=True, slots=True)
class PostApprovedStocktakeDifferencesCommand:
    """One task/version coordinate for the independent posting command."""

    task_id: uuid.UUID
    expected_task_version: int


@dataclass(frozen=True, slots=True)
class StocktakeDifferencePostingResult:
    completion_id: uuid.UUID
    task_id: uuid.UUID
    terminal_round_id: uuid.UUID
    resulting_task_status: str
    task_version: int
    scope_count: int
    difference_count: int
    accepted_difference_count: int
    no_adjustment_count: int
    transaction_count: int
    movement_count: int
    total_quantity: Decimal
    first_ledger_cursor: int | None
    last_ledger_cursor: int | None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _PostingGroup:
    round_id: uuid.UUID
    round_no: int
    movement_type: str
    posting_kind: str
    movements: tuple[PlannedStocktakeMovement, ...]
    entry: inventory_service._StocktakeInventoryBatchEntry


@dataclass(frozen=True, slots=True)
class _PostingBinding:
    difference_id: uuid.UUID
    scope_id: uuid.UUID
    source_round_id: uuid.UUID
    decision: str
    posting_kind: str | None
    posting_id: uuid.UUID | None
    inventory_transaction_id: uuid.UUID | None
    inventory_movement_id: uuid.UUID | None
    quantity: Decimal


@dataclass(frozen=True, slots=True)
class _ApprovedTaskView:
    id: uuid.UUID
    task_type: str
    status: str
    version: int
    cutoff_ledger_cursor: int | None
    cutoff_at: datetime | None
    submitted_at: datetime | None
    posted_at: datetime | None
    closed_at: datetime | None
    current_round_no: int


def post_approved_stocktake_differences(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: PostApprovedStocktakeDifferencesCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeDifferencePostingResult:
    """Atomically post one approved non-opening task without committing.

    The caller owns the surrounding transaction and must roll it back after
    any exception.  This boundary never calls an external system and never
    creates notification, reconciliation or close facts.
    """

    try:
        return _post_approved_stocktake_differences(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    except StocktakeDifferencePostingError:
        raise
    except inventory_service.InventoryPostingError as exc:
        _fail(
            "stocktake_posting_inventory_evidence_invalid",
            exc.category,
            "盘点差异账户、批次、SN、余额或冻结证据未通过正式库存校验",
            cause=exc,
        )
    except AuditChainError as exc:
        _fail(
            "stocktake_posting_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，盘点差异未过账",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "stocktake_posting_concurrent_conflict",
            "conflict",
            "盘点差异过账发生并发冲突，请回滚并重新读取",
            cause=exc,
        )
    except DBAPIError as exc:
        _fail(
            "stocktake_posting_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了盘点差异过账，请回滚并重新读取",
            cause=exc,
        )
    raise AssertionError("unreachable stocktake posting boundary")


def _post_approved_stocktake_differences(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: PostApprovedStocktakeDifferencesCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeDifferencePostingResult:
    supplied = _validate_supplied_actor(actor)
    checked = _validate_post_command(command)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    trace_id = _require_trace_request_id(trace_request_id)
    path = f"/api/v1/stocktakes/{checked.task_id}/post-differences"
    key_hash = _idempotency_hmac(secret, supplied.user_id, path, raw_key)
    _take_advisory_locks(
        db,
        (
            _lock_coordinate("stocktake-posting-idempotency", key_hash),
            _lock_coordinate("stocktake-posting-task", str(checked.task_id)),
        ),
    )

    # Canonical writer order: global ledger head -> complete task graph ->
    # current principal -> union inventory references/SN/balances -> audit.
    ledger_proof = inventory_service._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_nonopening_stocktake_posting_graph(db, checked.task_id)
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked.task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None or task.task_type not in NON_OPENING_TYPES:
        _fail(
            "stocktake_posting_task_not_found",
            "not_found",
            "非期初盘点任务不存在",
        )
    lock_formal_principal_graph(db, (supplied.user_id,))
    now = _database_now(db)
    current = inventory_service._require_current_stocktake_difference_finalizer(
        db, supplied
    )
    assignment = _current_admin_assignment(db, current)

    approval_rows = tuple(
        db.scalars(
            select(StocktakeEffectiveApprovalCompletion)
            .where(StocktakeEffectiveApprovalCompletion.task_id == task.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(approval_rows) != 1:
        _fail(
            "stocktake_posting_effective_approval_missing",
            "precondition_failed",
            "盘点任务缺少唯一、完整的总部最终有效审批封印",
        )
    approval = approval_rows[0]
    request_hash = _posting_request_sha256(current, checked, approval)

    existing_by_key = tuple(
        db.scalars(
            select(StocktakePostingCompletion)
            .where(StocktakePostingCompletion.idempotency_key_hash == key_hash)
            .execution_options(populate_existing=True)
        ).all()
    )
    existing_by_task = tuple(
        db.scalars(
            select(StocktakePostingCompletion)
            .where(StocktakePostingCompletion.task_id == task.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(existing_by_key) > 1 or len(existing_by_task) > 1:
        _evidence_invalid("盘点过账完成事实不唯一")
    if existing_by_key:
        completion = existing_by_key[0]
        if not existing_by_task or existing_by_task[0].id != completion.id:
            _fail(
                "stocktake_posting_idempotency_conflict",
                "conflict",
                "幂等键已绑定其他盘点过账请求",
            )
        if (
            completion.task_id != task.id
            or completion.effective_approval_completion_id != approval.id
            or completion.expected_task_version != checked.expected_task_version
            or completion.request_sha256 != request_hash
            or completion.posted_by_user_id != current.user_id
            or completion.posted_by_person_id != current.person_id
            or completion.posted_role_assignment_id != assignment.id
            or completion.authorization_version != current.authorization_version
        ):
            _fail(
                "stocktake_posting_idempotency_conflict",
                "conflict",
                "幂等键已绑定不同的盘点、版本、审批或权限上下文",
            )
        plan = _load_and_build_posting_plan(
            db,
            task=task,
            expected_task_version=completion.expected_task_version,
            approval=approval,
            posted_replay=True,
        )
        _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
            db, stream_key=INVENTORY_STREAM_KEY
        )
        _verify_source_audits(db, task=task, approval=approval, proof=audit_proof)
        _validate_persisted_posting_completion(
            db,
            task=task,
            approval=approval,
            completion=completion,
            plan=plan,
            audit_proof=audit_proof,
        )
        return _posting_result(task, completion, replayed=True)

    if existing_by_task:
        _fail(
            "stocktake_posting_already_completed",
            "conflict",
            "该盘点任务已由其他幂等请求形成过账完成事实",
        )
    if (
        task.status != "approved"
        or task.version != checked.expected_task_version
        or task.posted_at is not None
        or task.closed_at is not None
        or approval.approved_task_version != task.version
        or approval.expected_task_version + 1 != task.version
        or approval.approval_manifest_sha256 is None
        or _SHA256.fullmatch(approval.approval_manifest_sha256) is None
    ):
        _fail(
            "stocktake_posting_task_not_postable",
            "precondition_failed" if task.version == checked.expected_task_version else "conflict",
            "盘点任务不是当前版本的已批准待过账状态",
        )
    if now <= _as_utc(approval.completed_at):
        _fail(
            "stocktake_posting_clock_not_monotonic",
            "service_unavailable",
            "数据库时间未晚于总部最终审批时间，禁止形成过账顺序",
        )

    plan = _load_and_build_posting_plan(
        db,
        task=task,
        expected_task_version=checked.expected_task_version,
        approval=approval,
        posted_replay=False,
    )
    if (
        approval.scope_count != len(plan.effective_scope_rounds)
        or approval.difference_count != plan.effective_difference_count
        or approval.accepted_difference_count != len(plan.movements)
        or approval.no_adjustment_count != len(plan.no_adjustment_difference_ids)
    ):
        _evidence_invalid("总部有效审批汇总与可过账计划不一致")

    # Even a zero/no-adjustment completion consumes and releases the exact
    # task freeze graph.  The private batch repeats the account-level proof for
    # positive movements after taking the union reference lock.
    inventory_service._require_owned_stocktake_freeze_scopes(
        db,
        task_id=task.id,
        accounts={},
        effective_at=now,
    )
    groups = _posting_groups(
        task=task,
        plan=plan,
        key_hash=key_hash,
        request_hash=request_hash,
        effective_at=now,
    )
    batch = inventory_service._post_prelocked_stocktake_inventory_batch(
        db,
        actor=current,
        task_id=task.id,
        entries=tuple(group.entry for group in groups),
        request_reference=_request_reference(trace_id),
        occurred_at=now,
        ledger_proof=ledger_proof,
    )
    if len(batch.entries) != len(groups):
        _evidence_invalid("库存批次返回的交易分组数量无效")

    _verify_source_audits(
        db,
        task=task,
        approval=approval,
        proof=batch.audit_proof,
    )
    completion = _persist_posting_completion(
        db,
        task=task,
        approval=approval,
        actor=current,
        assignment=assignment,
        expected_task_version=checked.expected_task_version,
        key_hash=key_hash,
        request_hash=request_hash,
        trace_request_id=trace_id,
        now=now,
        plan=plan,
        groups=groups,
        batch=batch,
    )
    _validate_persisted_posting_completion(
        db,
        task=task,
        approval=approval,
        completion=completion,
        plan=plan,
        audit_proof=batch.audit_proof,
    )
    return _posting_result(task, completion, replayed=False)


def _load_and_build_posting_plan(
    db: Session,
    *,
    task: FormalStocktakeTask,
    expected_task_version: int,
    approval: StocktakeEffectiveApprovalCompletion,
    posted_replay: bool,
) -> StocktakeDifferencePostingPlan:
    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task.id)
            .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    rounds = tuple(
        db.scalars(
            select(StocktakeRound)
            .where(StocktakeRound.task_id == task.id)
            .order_by(StocktakeRound.round_no, StocktakeRound.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    recount_cases = tuple(
        db.scalars(
            select(StocktakeRecountCase)
            .where(StocktakeRecountCase.task_id == task.id)
            .order_by(StocktakeRecountCase.next_round_no, StocktakeRecountCase.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    recount_assignments = tuple(
        db.scalars(
            select(StocktakeRecountScopeAssignment)
            .where(StocktakeRecountScopeAssignment.task_id == task.id)
            .order_by(
                StocktakeRecountScopeAssignment.recount_case_id,
                StocktakeRecountScopeAssignment.scope_id,
                StocktakeRecountScopeAssignment.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    submissions = tuple(
        db.scalars(
            select(StocktakeRoundSubmission)
            .where(StocktakeRoundSubmission.task_id == task.id)
            .order_by(StocktakeRoundSubmission.round_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    difference_completions = tuple(
        db.scalars(
            select(StocktakeDifferenceSetCompletion)
            .where(StocktakeDifferenceSetCompletion.task_id == task.id)
            .order_by(StocktakeDifferenceSetCompletion.round_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    differences = tuple(
        db.scalars(
            select(StocktakeDifference)
            .where(StocktakeDifference.task_id == task.id)
            .order_by(
                StocktakeDifference.round_id,
                StocktakeDifference.difference_no,
                StocktakeDifference.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    reviews = tuple(
        db.scalars(
            select(StocktakeReview)
            .where(StocktakeReview.task_id == task.id)
            .order_by(
                StocktakeReview.round_id,
                StocktakeReview.review_stage,
                StocktakeReview.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    review_items = tuple(
        db.scalars(
            select(StocktakeReviewItem)
            .where(StocktakeReviewItem.task_id == task.id)
            .order_by(
                StocktakeReviewItem.round_id,
                StocktakeReviewItem.review_id,
                StocktakeReviewItem.difference_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(StocktakeCountObservation.task_id == task.id)
            .order_by(
                StocktakeCountObservation.round_id,
                StocktakeCountObservation.scope_id,
                StocktakeCountObservation.observation_no,
                StocktakeCountObservation.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    approval_scopes = tuple(
        db.scalars(
            select(StocktakeEffectiveApprovalScope)
            .where(StocktakeEffectiveApprovalScope.completion_id == approval.id)
            .order_by(StocktakeEffectiveApprovalScope.scope_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    approval_items = tuple(
        db.scalars(
            select(StocktakeEffectiveApprovalItem)
            .where(StocktakeEffectiveApprovalItem.completion_id == approval.id)
            .order_by(
                StocktakeEffectiveApprovalItem.scope_id,
                StocktakeEffectiveApprovalItem.difference_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )

    accounts, observation_account_ids = _load_posting_accounts(
        db,
        differences=differences,
        observations=observations,
    )
    planning_task: FormalStocktakeTask | _ApprovedTaskView
    if posted_replay:
        if (
            task.status != "posted"
            or task.version != expected_task_version + 1
            or task.posted_at is None
            or task.closed_at is not None
        ):
            _evidence_invalid("幂等重放的盘点任务不是独立 posted 终态")
        planning_task = _ApprovedTaskView(
            id=task.id,
            task_type=task.task_type,
            status="approved",
            version=expected_task_version,
            cutoff_ledger_cursor=task.cutoff_ledger_cursor,
            cutoff_at=task.cutoff_at,
            submitted_at=task.submitted_at,
            posted_at=None,
            closed_at=None,
            current_round_no=task.current_round_no,
        )
    else:
        planning_task = task
    return _build_nonopening_stocktake_posting_plan(
        task=planning_task,  # type: ignore[arg-type]
        expected_task_version=expected_task_version,
        scopes=scopes,
        rounds=rounds,
        recount_cases=recount_cases,
        recount_assignments=recount_assignments,
        submissions=submissions,
        completions=difference_completions,
        differences=differences,
        reviews=reviews,
        review_items=review_items,
        observations=observations,
        accounts=accounts,
        observation_account_ids=observation_account_ids,
        effective_approval_completion=EffectiveApprovalCompletionEvidence(
            id=approval.id,
            task_id=approval.task_id,
            terminal_round_id=approval.terminal_round_id,
            terminal_headquarters_review_id=(
                approval.terminal_headquarters_review_id
            ),
            scope_count=approval.scope_count,
            difference_count=approval.difference_count,
            approval_manifest_sha256=approval.approval_manifest_sha256,
        ),
        effective_approval_scopes=tuple(
            EffectiveApprovalScopeEvidence(
                completion_id=row.completion_id,
                scope_id=row.scope_id,
                source_round_id=row.source_round_id,
                source_difference_completion_id=row.source_difference_completion_id,
                regional_review_id=row.regional_review_id,
                difference_count=row.difference_count,
            )
            for row in approval_scopes
        ),
        effective_approval_items=tuple(
            EffectiveApprovalItemEvidence(
                completion_id=row.completion_id,
                scope_id=row.scope_id,
                source_round_id=row.source_round_id,
                difference_id=row.difference_id,
                regional_review_id=row.regional_review_id,
                regional_decision=row.regional_decision,
                headquarters_decision=row.headquarters_decision,
            )
            for row in approval_items
        ),
    )


def _load_posting_accounts(
    db: Session,
    *,
    differences: Sequence[StocktakeDifference],
    observations: Sequence[StocktakeCountObservation],
) -> tuple[dict[uuid.UUID, StockAccount], dict[uuid.UUID, uuid.UUID]]:
    direct_ids = {
        account_id
        for row in differences
        for account_id in (row.expected_account_id, row.observed_account_id)
        if account_id is not None
    }
    referenced_observation_ids = {
        row.observed_line_id
        for row in differences
        if row.observed_line_id is not None
    }
    referenced_observations = tuple(
        row for row in observations if row.id in referenced_observation_ids
    )
    conditions = []
    if direct_ids:
        conditions.append(StockAccount.id.in_(tuple(sorted(direct_ids, key=str))))
    for observation in referenced_observations:
        if observation.material_id is None:
            continue
        conditions.append(
            and_(
                StockAccount.owner_org_id == observation.owner_org_id,
                StockAccount.location_id == observation.location_id,
                StockAccount.custodian_person_id
                == observation.custodian_person_id_snapshot,
                StockAccount.material_id == observation.material_id,
                StockAccount.condition_code == observation.condition_code,
                StockAccount.availability_bucket
                == observation.availability_bucket,
                StockAccount.lot_id == observation.lot_id,
            )
        )
    account_rows = (
        tuple(
            db.scalars(
                select(StockAccount)
                .where(or_(*conditions))
                .order_by(StockAccount.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        if conditions
        else ()
    )
    accounts = {row.id: row for row in account_rows}
    if len(accounts) != len(account_rows):
        _evidence_invalid("盘点差异库存账户主键不唯一")
    observation_account_ids: dict[uuid.UUID, uuid.UUID] = {}
    for observation in referenced_observations:
        matching = tuple(
            account.id
            for account in account_rows
            if _observation_matches_account(observation, account)
        )
        if len(matching) > 1:
            _fail(
                "stocktake_posting_observation_account_ambiguous",
                "precondition_failed",
                "现场新增观察映射到多个库存账户，禁止过账",
            )
        if matching:
            observation_account_ids[observation.id] = matching[0]
    return accounts, observation_account_ids


def _observation_matches_account(
    observation: StocktakeCountObservation,
    account: StockAccount,
) -> bool:
    return bool(
        observation.verification_status == "verified"
        and observation.material_id is not None
        and observation.owner_org_id == account.owner_org_id
        and observation.location_id == account.location_id
        and observation.custodian_person_id_snapshot == account.custodian_person_id
        and observation.material_id == account.material_id
        and observation.condition_code == account.condition_code
        and observation.availability_bucket == account.availability_bucket
        and observation.lot_id == account.lot_id
    )


def _posting_groups(
    *,
    task: FormalStocktakeTask,
    plan: StocktakeDifferencePostingPlan,
    key_hash: str,
    request_hash: str,
    effective_at: datetime,
) -> tuple[_PostingGroup, ...]:
    round_no = {row.round_id: row.round_no for row in plan.effective_scope_rounds}
    grouped: dict[tuple[uuid.UUID, str], list[PlannedStocktakeMovement]] = (
        defaultdict(list)
    )
    for movement in plan.movements:
        grouped[(movement.round_id, movement.movement_type)].append(movement)
    result: list[_PostingGroup] = []
    for (round_id, movement_type), movements in sorted(
        grouped.items(),
        key=lambda item: (
            round_no[item[0][0]],
            MOVEMENT_TYPES.index(item[0][1]),
            str(item[0][0]),
        ),
    ):
        posting_kind = POSTING_KIND_BY_MOVEMENT[movement_type]
        ordered = tuple(sorted(movements, key=lambda row: str(row.difference_id)))
        group_coordinate = f"{round_id}:{movement_type}"
        command = inventory_service.InventoryPostingCommand(
            transaction_no=(
                f"STK-{task.id.hex[:16]}-{round_no[round_id]:03d}-"
                f"{posting_kind.removeprefix('difference_')}"
            ),
            movement_type=movement_type,
            source_document_type="stocktake_difference",
            source_document_id=str(task.id),
            posting_key=f"stocktake-difference:{task.id}:{group_coordinate}",
            effective_at=effective_at,
            movements=tuple(
                inventory_service.InventoryMovementCommand(
                    from_account_id=row.from_account_id,
                    to_account_id=row.to_account_id,
                    quantity=row.quantity,
                    serial_ids=row.serial_ids,
                    external_boundary_code=row.external_boundary_code,
                )
                for row in ordered
            ),
        )
        result.append(
            _PostingGroup(
                round_id=round_id,
                round_no=round_no[round_id],
                movement_type=movement_type,
                posting_kind=posting_kind,
                movements=ordered,
                entry=inventory_service._StocktakeInventoryBatchEntry(
                    command=command,
                    idempotency_key_hash=_derived_sha256(
                        "inventory-idempotency", key_hash, group_coordinate
                    ),
                    request_hash=_derived_sha256(
                        "inventory-request", request_hash, group_coordinate
                    ),
                ),
            )
        )
    return tuple(result)


def _persist_posting_completion(
    db: Session,
    *,
    task: FormalStocktakeTask,
    approval: StocktakeEffectiveApprovalCompletion,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    expected_task_version: int,
    key_hash: str,
    request_hash: str,
    trace_request_id: str,
    now: datetime,
    plan: StocktakeDifferencePostingPlan,
    groups: Sequence[_PostingGroup],
    batch: inventory_service._StocktakeInventoryBatchCommit,
) -> StocktakePostingCompletion:
    completion_id = uuid.uuid4()
    posting_by_group: dict[tuple[uuid.UUID, str], StocktakePosting] = {}
    accepted_bindings: dict[uuid.UUID, _PostingBinding] = {}
    transaction_rows: list[dict[str, object]] = []

    for group, committed in zip(groups, batch.entries, strict=True):
        if len(group.movements) != len(committed.movement_ids):
            _evidence_invalid("盘点差异与库存移动无法逐项绑定")
        posting = StocktakePosting(
            id=uuid.uuid4(),
            task_id=task.id,
            round_id=group.round_id,
            posting_kind=group.posting_kind,
            effective_approval_completion_id=approval.id,
            inventory_transaction_id=committed.result.transaction_id,
            total_quantity=sum(
                (row.quantity for row in group.movements), start=_ZERO
            ),
            idempotency_key_hash=_derived_sha256(
                "posting-idempotency",
                key_hash,
                f"{group.round_id}:{group.movement_type}",
            ),
            request_hash=_derived_sha256(
                "posting-request",
                request_hash,
                f"{group.round_id}:{group.movement_type}",
            ),
            posted_by_user_id=actor.user_id,
            posted_at=now,
            created_at=now,
        )
        posting_by_group[(group.round_id, group.movement_type)] = posting
        db.add(posting)
        transaction_rows.append(
            {
                "inventory_transaction_id": str(committed.result.transaction_id),
                "ledger_cursor": committed.result.ledger_cursor,
                "movement_type": group.movement_type,
                "posting_id": str(posting.id),
                "posting_kind": group.posting_kind,
                "round_id": str(group.round_id),
            }
        )
    db.flush()

    for group, committed in zip(groups, batch.entries, strict=True):
        posting = posting_by_group[(group.round_id, group.movement_type)]
        for movement, movement_id in zip(
            group.movements, committed.movement_ids, strict=True
        ):
            db.add(
                StocktakePostingItem(
                    posting_id=posting.id,
                    inventory_movement_id=movement_id,
                    task_id=task.id,
                    round_id=group.round_id,
                    count_line_id=None,
                    difference_id=movement.difference_id,
                    quantity=movement.quantity,
                    created_at=now,
                )
            )
            accepted_bindings[movement.difference_id] = _PostingBinding(
                difference_id=movement.difference_id,
                scope_id=movement.scope_id,
                source_round_id=movement.round_id,
                decision="accept_for_posting",
                posting_kind=group.posting_kind,
                posting_id=posting.id,
                inventory_transaction_id=committed.result.transaction_id,
                inventory_movement_id=movement_id,
                quantity=movement.quantity,
            )
    db.flush()

    no_adjustment_ids = set(plan.no_adjustment_difference_ids)
    effective_items = tuple(
        db.scalars(
            select(StocktakeEffectiveApprovalItem)
            .where(StocktakeEffectiveApprovalItem.completion_id == approval.id)
            .order_by(
                StocktakeEffectiveApprovalItem.scope_id,
                StocktakeEffectiveApprovalItem.difference_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    bindings: list[_PostingBinding] = []
    for approved_item in effective_items:
        if approved_item.headquarters_decision == "accept_for_posting":
            binding = accepted_bindings.get(approved_item.difference_id)
            if binding is None:
                _evidence_invalid("通过差异缺少唯一库存移动绑定")
        elif (
            approved_item.headquarters_decision == "no_adjustment"
            and approved_item.difference_id in no_adjustment_ids
        ):
            binding = _PostingBinding(
                difference_id=approved_item.difference_id,
                scope_id=approved_item.scope_id,
                source_round_id=approved_item.source_round_id,
                decision="no_adjustment",
                posting_kind=None,
                posting_id=None,
                inventory_transaction_id=None,
                inventory_movement_id=None,
                quantity=_ZERO,
            )
        else:
            _evidence_invalid("有效审批逐项决定与过账计划不一致")
        if (
            binding.scope_id != approved_item.scope_id
            or binding.source_round_id != approved_item.source_round_id
        ):
            _evidence_invalid("过账逐项绑定超出有效审批范围或轮次")
        bindings.append(binding)
    if set(accepted_bindings) != {
        row.difference_id
        for row in effective_items
        if row.headquarters_decision == "accept_for_posting"
    }:
        _evidence_invalid("库存移动包含未获有效审批的差异")

    item_documents = tuple(
        _posting_item_document(completion_id, task.id, row) for row in bindings
    )
    first_cursor = min(
        (row.result.ledger_cursor for row in batch.entries), default=None
    )
    last_cursor = max(
        (row.result.ledger_cursor for row in batch.entries), default=None
    )
    total_quantity = sum((row.quantity for row in bindings), start=_ZERO)
    posting_manifest = _sha256(
        {
            "approval_manifest_sha256": approval.approval_manifest_sha256,
            "completion_id": str(completion_id),
            "expected_task_version": expected_task_version,
            "first_ledger_cursor": first_cursor,
            "items": item_documents,
            "last_ledger_cursor": last_cursor,
            "plan_manifest_sha256": plan.plan_manifest_sha256,
            "posted_task_version": expected_task_version + 1,
            "schema": "cloud_oam.stocktake.nonopening_posting_completion.v1",
            "task_id": str(task.id),
            "transactions": sorted(
                transaction_rows,
                key=lambda row: (
                    str(row["round_id"]),
                    str(row["movement_type"]),
                    str(row["inventory_transaction_id"]),
                ),
            ),
        }
    )
    authorization_sha256 = _posting_authorization_sha256(
        actor=actor,
        assignment=assignment,
        posted_at=now,
    )
    completion = StocktakePostingCompletion(
        id=completion_id,
        task_id=task.id,
        effective_approval_completion_id=approval.id,
        terminal_round_id=approval.terminal_round_id,
        expected_task_version=expected_task_version,
        posted_task_version=expected_task_version + 1,
        scope_count=len(plan.effective_scope_rounds),
        difference_count=plan.effective_difference_count,
        accepted_difference_count=len(plan.movements),
        no_adjustment_count=len(plan.no_adjustment_difference_ids),
        transaction_count=len(batch.entries),
        movement_count=len(plan.movements),
        total_quantity=total_quantity,
        first_ledger_cursor=first_cursor,
        last_ledger_cursor=last_cursor,
        approval_manifest_sha256=approval.approval_manifest_sha256,
        posting_manifest_sha256=posting_manifest,
        request_sha256=request_hash,
        idempotency_key_hash=key_hash,
        posted_by_user_id=actor.user_id,
        posted_by_person_id=actor.person_id,
        posted_role_assignment_id=assignment.id,
        authorization_version=actor.authorization_version,
        role_code="admin",
        scope_type="national",
        scope_id_snapshot="*",
        authorization_sha256=authorization_sha256,
        posted_at=now,
        created_at=now,
    )
    db.add(completion)
    db.flush()
    for binding, document in zip(bindings, item_documents, strict=True):
        db.add(
            StocktakePostingCompletionItem(
                completion_id=completion.id,
                difference_id=binding.difference_id,
                task_id=task.id,
                scope_id=binding.scope_id,
                source_round_id=binding.source_round_id,
                decision=binding.decision,
                posting_kind=binding.posting_kind,
                posting_id=binding.posting_id,
                inventory_transaction_id=binding.inventory_transaction_id,
                inventory_movement_id=binding.inventory_movement_id,
                quantity=binding.quantity,
                item_manifest_sha256=_sha256(document),
                created_at=now,
            )
        )
    db.flush()

    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id == task.id)
            .order_by(InventoryFreeze.stocktake_scope_id, InventoryFreeze.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    for freeze in freezes:
        freeze.status = "released"
        freeze.valid_to = now
        freeze.released_by_user_id = actor.user_id
        freeze.release_reason = FREEZE_RELEASE_REASON
        freeze.version += 1
        freeze.updated_at = now
    db.flush()

    metadata = _posting_event_metadata(completion)
    db.add(
        StateTransitionEvent(
            aggregate_type="stocktake_task",
            aggregate_id=str(task.id),
            from_status="approved",
            to_status="posted",
            reason="nonopening_stocktake_difference_posted",
            actor_id=actor.user_id,
            idempotency_key=_event_key("state", completion.id),
            occurred_at=now,
            metadata_jsonb=metadata,
            created_at=now,
        )
    )
    db.flush()
    append_audit_event(
        db,
        stream_key=INVENTORY_STREAM_KEY,
        actor_user_id=actor.user_id,
        action="stocktake.nonopening.difference_posted",
        aggregate_type="stocktake_posting_completion",
        aggregate_id=str(completion.id),
        before_jsonb={"status": "approved", "task_version": expected_task_version},
        after_jsonb=metadata,
        request_id=_request_reference(trace_request_id),
        occurred_at=now,
    )
    db.flush()

    # Evidence, release, state and audit rows are flushed first because SQLite
    # enforces the terminal graph immediately; PostgreSQL re-proves it at
    # commit through the deferred 0035 constraint trigger.
    task.status = "posted"
    task.posted_at = now
    task.version = expected_task_version + 1
    task.updated_at = now
    db.flush()
    return completion


def _validate_persisted_posting_completion(
    db: Session,
    *,
    task: FormalStocktakeTask,
    approval: StocktakeEffectiveApprovalCompletion,
    completion: StocktakePostingCompletion,
    plan: StocktakeDifferencePostingPlan,
    audit_proof: object,
) -> None:
    if (
        task.status != "posted"
        or task.posted_at is None
        or task.closed_at is not None
        or task.version != completion.posted_task_version
        or _as_utc(task.posted_at) != _as_utc(completion.posted_at)
        or completion.task_id != task.id
        or completion.effective_approval_completion_id != approval.id
        or completion.terminal_round_id != approval.terminal_round_id
        or completion.posted_task_version != completion.expected_task_version + 1
        or completion.approval_manifest_sha256
        != approval.approval_manifest_sha256
        or completion.scope_count != len(plan.effective_scope_rounds)
        or completion.difference_count != plan.effective_difference_count
        or completion.accepted_difference_count != len(plan.movements)
        or completion.no_adjustment_count
        != len(plan.no_adjustment_difference_ids)
        or completion.movement_count != len(plan.movements)
        or completion.role_code != "admin"
        or completion.scope_type != "national"
        or completion.scope_id_snapshot != "*"
        or _SHA256.fullmatch(completion.request_sha256 or "") is None
        or _SHA256.fullmatch(completion.idempotency_key_hash or "") is None
        or _SHA256.fullmatch(completion.posting_manifest_sha256 or "") is None
        or completion.authorization_sha256
        != _posting_authorization_document_sha256(
            user_id=completion.posted_by_user_id,
            person_id=completion.posted_by_person_id,
            assignment_id=completion.posted_role_assignment_id,
            authorization_version=completion.authorization_version,
            posted_at=completion.posted_at,
        )
    ):
        _evidence_invalid("盘点过账完成事实的任务、版本、审批或权限快照无效")

    expected_movement = {row.difference_id: row for row in plan.movements}
    expected_no_adjustment = set(plan.no_adjustment_difference_ids)
    completion_items = tuple(
        db.scalars(
            select(StocktakePostingCompletionItem)
            .where(StocktakePostingCompletionItem.completion_id == completion.id)
            .order_by(
                StocktakePostingCompletionItem.scope_id,
                StocktakePostingCompletionItem.difference_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if (
        len(completion_items) != plan.effective_difference_count
        or {row.difference_id for row in completion_items}
        != set(expected_movement).union(expected_no_adjustment)
    ):
        _evidence_invalid("盘点过账完成逐项事实未完整覆盖有效差异")

    postings = tuple(
        db.scalars(
            select(StocktakePosting)
            .where(
                StocktakePosting.task_id == task.id,
                StocktakePosting.posting_kind.in_(
                    tuple(POSTING_KIND_BY_MOVEMENT.values())
                ),
            )
            .order_by(
                StocktakePosting.round_id,
                StocktakePosting.posting_kind,
                StocktakePosting.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    posting_by_id = {row.id: row for row in postings}
    if len(posting_by_id) != len(postings):
        _evidence_invalid("盘点差异过账分组事实不唯一")
    transactions: dict[uuid.UUID, InventoryTransaction] = {}
    movements_by_id: dict[uuid.UUID, InventoryMovement] = {}
    posting_items_by_difference: dict[uuid.UUID, StocktakePostingItem] = {}
    transaction_documents: list[dict[str, object]] = []
    cursors: list[int] = []

    expected_group_keys = {
        (row.round_id, row.movement_type) for row in plan.movements
    }
    actual_group_keys: set[tuple[uuid.UUID, str]] = set()
    for posting in postings:
        expected_movement_type = next(
            (
                movement_type
                for movement_type, kind in POSTING_KIND_BY_MOVEMENT.items()
                if kind == posting.posting_kind
            ),
            None,
        )
        if expected_movement_type is None:
            _evidence_invalid("盘点差异过账类型无效")
        group_key = (posting.round_id, expected_movement_type)
        if group_key in actual_group_keys:
            _evidence_invalid("同一轮次和差异类型出现重复过账分组")
        actual_group_keys.add(group_key)
        transaction = (
            db.get(
                InventoryTransaction,
                posting.inventory_transaction_id,
                populate_existing=True,
            )
            if posting.inventory_transaction_id is not None
            else None
        )
        if transaction is None:
            _evidence_invalid("正数量盘点差异过账缺少库存交易")
        expected_group_rows = tuple(
            row
            for row in plan.movements
            if row.round_id == posting.round_id
            and row.movement_type == expected_movement_type
        )
        coordinate = f"{posting.round_id}:{expected_movement_type}"
        if (
            not expected_group_rows
            or posting.effective_approval_completion_id != approval.id
            or posting.total_quantity
            != sum((row.quantity for row in expected_group_rows), start=_ZERO)
            or posting.posted_by_user_id != completion.posted_by_user_id
            or _as_utc(posting.posted_at) != _as_utc(completion.posted_at)
            or posting.idempotency_key_hash
            != _derived_sha256(
                "posting-idempotency", completion.idempotency_key_hash, coordinate
            )
            or posting.request_hash
            != _derived_sha256(
                "posting-request", completion.request_sha256, coordinate
            )
            or transaction.movement_type != expected_movement_type
            or transaction.source_document_type != "stocktake_difference"
            or transaction.source_document_id != str(task.id)
            or transaction.posting_key
            != f"stocktake-difference:{task.id}:{coordinate}"
            or transaction.idempotency_key_hash
            != _derived_sha256(
                "inventory-idempotency",
                completion.idempotency_key_hash,
                coordinate,
            )
            or transaction.request_hash
            != _derived_sha256(
                "inventory-request", completion.request_sha256, coordinate
            )
            or transaction.status != "posted"
            or transaction.actor_user_id != completion.posted_by_user_id
            or _as_utc(transaction.posted_at) != _as_utc(completion.posted_at)
            or not isinstance(transaction.ledger_cursor, int)
            or isinstance(transaction.ledger_cursor, bool)
            or transaction.ledger_cursor <= 0
        ):
            _evidence_invalid("盘点过账分组与库存交易坐标不一致")
        transactions[transaction.id] = transaction
        cursors.append(transaction.ledger_cursor)
        movement_rows = tuple(
            db.scalars(
                select(InventoryMovement)
                .where(InventoryMovement.transaction_id == transaction.id)
                .order_by(InventoryMovement.line_no, InventoryMovement.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        posting_item_rows = tuple(
            db.scalars(
                select(StocktakePostingItem)
                .where(StocktakePostingItem.posting_id == posting.id)
                .order_by(StocktakePostingItem.inventory_movement_id)
                .execution_options(populate_existing=True)
            ).all()
        )
        item_by_movement = {row.inventory_movement_id: row for row in posting_item_rows}
        if (
            len(movement_rows) != len(expected_group_rows)
            or len(item_by_movement) != len(posting_item_rows)
            or set(item_by_movement) != {row.id for row in movement_rows}
        ):
            _evidence_invalid("盘点库存移动与过账逐项绑定数量不一致")
        for expected, movement in zip(
            sorted(expected_group_rows, key=lambda row: str(row.difference_id)),
            movement_rows,
            strict=True,
        ):
            posting_item = item_by_movement[movement.id]
            serial_ids = tuple(
                db.scalars(
                    select(InventoryMovementSerial.serial_id)
                    .where(
                        InventoryMovementSerial.movement_id == movement.id,
                        InventoryMovementSerial.transaction_id == transaction.id,
                    )
                    .order_by(InventoryMovementSerial.serial_id)
                ).all()
            )
            if (
                movement.transaction_id != transaction.id
                or movement.from_account_id != expected.from_account_id
                or movement.to_account_id != expected.to_account_id
                or movement.external_boundary_code
                != expected.external_boundary_code
                or movement.quantity != expected.quantity
                or serial_ids != tuple(sorted(expected.serial_ids, key=str))
                or posting_item.task_id != task.id
                or posting_item.round_id != expected.round_id
                or posting_item.count_line_id is not None
                or posting_item.difference_id != expected.difference_id
                or posting_item.quantity != expected.quantity
            ):
                _evidence_invalid("盘点差异与库存端点、数量、批次或 SN 绑定无效")
            if expected.difference_id in posting_items_by_difference:
                _evidence_invalid("同一盘点差异重复绑定库存移动")
            posting_items_by_difference[expected.difference_id] = posting_item
            movements_by_id[movement.id] = movement
        transaction_documents.append(
            {
                "inventory_transaction_id": str(transaction.id),
                "ledger_cursor": transaction.ledger_cursor,
                "movement_type": expected_movement_type,
                "posting_id": str(posting.id),
                "posting_kind": posting.posting_kind,
                "round_id": str(posting.round_id),
            }
        )
        _verify_inventory_transaction_audit(db, transaction, audit_proof)

    if actual_group_keys != expected_group_keys:
        _evidence_invalid("盘点差异过账交易分组未精确覆盖计划")

    item_documents: list[dict[str, object]] = []
    total_quantity = _ZERO
    for item in completion_items:
        expected = expected_movement.get(item.difference_id)
        if expected is not None:
            posting_item = posting_items_by_difference.get(item.difference_id)
            if posting_item is None:
                _evidence_invalid("通过差异缺少过账逐项事实")
            posting = posting_by_id.get(posting_item.posting_id)
            movement = movements_by_id.get(posting_item.inventory_movement_id)
            if (
                posting is None
                or movement is None
                or item.scope_id != expected.scope_id
                or item.source_round_id != expected.round_id
                or item.decision != "accept_for_posting"
                or item.posting_kind != posting.posting_kind
                or item.posting_id != posting.id
                or item.inventory_transaction_id != posting.inventory_transaction_id
                or item.inventory_movement_id != movement.id
                or item.quantity != expected.quantity
            ):
                _evidence_invalid("过账完成逐项事实与库存移动不一致")
        elif item.difference_id in expected_no_adjustment:
            if (
                item.decision != "no_adjustment"
                or item.posting_kind is not None
                or item.posting_id is not None
                or item.inventory_transaction_id is not None
                or item.inventory_movement_id is not None
                or item.quantity != _ZERO
            ):
                _evidence_invalid("不调整差异缺少纯证据型完成事实")
        else:
            _evidence_invalid("过账完成逐项事实包含任务外差异")
        binding = _PostingBinding(
            difference_id=item.difference_id,
            scope_id=item.scope_id,
            source_round_id=item.source_round_id,
            decision=item.decision,
            posting_kind=item.posting_kind,
            posting_id=item.posting_id,
            inventory_transaction_id=item.inventory_transaction_id,
            inventory_movement_id=item.inventory_movement_id,
            quantity=item.quantity,
        )
        document = _posting_item_document(completion.id, task.id, binding)
        if item.item_manifest_sha256 != _sha256(document):
            _evidence_invalid("盘点过账逐项完成摘要不一致")
        item_documents.append(document)
        total_quantity += item.quantity

    sorted_cursors = sorted(cursors)
    if sorted_cursors and sorted_cursors != list(
        range(sorted_cursors[0], sorted_cursors[-1] + 1)
    ):
        _evidence_invalid("盘点库存批次未占用连续账本游标")
    first_cursor = sorted_cursors[0] if sorted_cursors else None
    last_cursor = sorted_cursors[-1] if sorted_cursors else None
    expected_manifest = _sha256(
        {
            "approval_manifest_sha256": approval.approval_manifest_sha256,
            "completion_id": str(completion.id),
            "expected_task_version": completion.expected_task_version,
            "first_ledger_cursor": first_cursor,
            "items": item_documents,
            "last_ledger_cursor": last_cursor,
            "plan_manifest_sha256": plan.plan_manifest_sha256,
            "posted_task_version": completion.posted_task_version,
            "schema": "cloud_oam.stocktake.nonopening_posting_completion.v1",
            "task_id": str(task.id),
            "transactions": sorted(
                transaction_documents,
                key=lambda row: (
                    str(row["round_id"]),
                    str(row["movement_type"]),
                    str(row["inventory_transaction_id"]),
                ),
            ),
        }
    )
    if (
        completion.transaction_count != len(transactions)
        or completion.total_quantity != total_quantity
        or completion.first_ledger_cursor != first_cursor
        or completion.last_ledger_cursor != last_cursor
        or completion.posting_manifest_sha256 != expected_manifest
    ):
        _evidence_invalid("盘点过账完成汇总、游标或任务级摘要不一致")

    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id == task.id)
            .order_by(InventoryFreeze.stocktake_scope_id, InventoryFreeze.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if (
        len(freezes) != completion.scope_count
        or any(
            row.status != "released"
            or row.valid_to is None
            or _as_utc(row.valid_to) != _as_utc(completion.posted_at)
            or row.released_by_user_id != completion.posted_by_user_id
            or row.release_reason != FREEZE_RELEASE_REASON
            for row in freezes
        )
    ):
        _evidence_invalid("盘点过账未精确释放任务全部冻结")

    metadata = _posting_event_metadata(completion)
    state_rows = tuple(
        db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id == str(task.id),
                StateTransitionEvent.reason
                == "nonopening_stocktake_difference_posted",
            )
        ).all()
    )
    audit_rows = tuple(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.stream_key == INVENTORY_STREAM_KEY,
                AuditEvent.action == "stocktake.nonopening.difference_posted",
                AuditEvent.aggregate_type == "stocktake_posting_completion",
                AuditEvent.aggregate_id == str(completion.id),
            )
        ).all()
    )
    if (
        len(state_rows) != 1
        or state_rows[0].from_status != "approved"
        or state_rows[0].to_status != "posted"
        or state_rows[0].actor_id != completion.posted_by_user_id
        or _as_utc(state_rows[0].occurred_at) != _as_utc(completion.posted_at)
        or state_rows[0].metadata_jsonb != metadata
        or len(audit_rows) != 1
        or audit_rows[0].actor_user_id != completion.posted_by_user_id
        or _as_utc(audit_rows[0].occurred_at) != _as_utc(completion.posted_at)
        or audit_rows[0].after_jsonb != metadata
    ):
        _evidence_invalid("盘点过账的独立状态或审计事实缺失、不唯一或不一致")
    _verify_audit_event_with_prelocked_proof(
        db,
        proof=audit_proof,
        stream_key=INVENTORY_STREAM_KEY,
        event_id=audit_rows[0].id,
    )


def _verify_source_audits(
    db: Session,
    *,
    task: FormalStocktakeTask,
    approval: StocktakeEffectiveApprovalCompletion,
    proof: object,
) -> None:
    from . import stocktake_review as review_service

    rounds = tuple(
        db.scalars(
            select(StocktakeRound)
            .where(StocktakeRound.task_id == task.id)
            .order_by(StocktakeRound.round_no, StocktakeRound.id)
        ).all()
    )
    completion_by_round = {
        row.round_id: row
        for row in db.scalars(
            select(StocktakeDifferenceSetCompletion).where(
                StocktakeDifferenceSetCompletion.task_id == task.id
            )
        ).all()
    }
    for round_row in rounds:
        completion = completion_by_round.get(round_row.id)
        if completion is None:
            _evidence_invalid("盘点轮次缺少差异集审计来源")
        action = (
            "stocktake.initial_difference_set.evaluated"
            if round_row.round_type == "initial"
            else "stocktake.recount_difference_set.evaluated"
        )
        events = tuple(
            db.scalars(
                select(AuditEvent).where(
                    AuditEvent.stream_key == INVENTORY_STREAM_KEY,
                    AuditEvent.action == action,
                    AuditEvent.aggregate_type == "stocktake_round",
                    AuditEvent.aggregate_id == str(round_row.id),
                )
            ).all()
        )
        if len(events) != 1:
            _evidence_invalid("盘点差异集审计事实缺失或不唯一")
        verified = _verify_audit_event_with_prelocked_proof(
            db,
            proof=proof,
            stream_key=INVENTORY_STREAM_KEY,
            event_id=events[0].id,
        )
        if (
            not isinstance(verified.after_jsonb, dict)
            or verified.after_jsonb.get("difference_manifest_sha256")
            != completion.difference_manifest_sha256
            or verified.after_jsonb.get("difference_count")
            != completion.difference_count
            or verified.after_jsonb.get("task_id") != str(task.id)
        ):
            _evidence_invalid("盘点差异集审计摘要与封印不一致")

    reviews = tuple(
        db.scalars(
            select(StocktakeReview)
            .where(StocktakeReview.task_id == task.id)
            .order_by(
                StocktakeReview.round_id,
                StocktakeReview.review_stage,
                StocktakeReview.id,
            )
        ).all()
    )
    for review in reviews:
        review_service._verify_review_audit(db, review, proof)
    terminal_events = tuple(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.stream_key == INVENTORY_STREAM_KEY,
                AuditEvent.action == "stocktake.nonopening.headquarters_reviewed",
                AuditEvent.aggregate_type == "stocktake_review",
                AuditEvent.aggregate_id
                == str(approval.terminal_headquarters_review_id),
            )
        ).all()
    )
    if len(terminal_events) != 1:
        _evidence_invalid("总部最终有效审批审计事实缺失或不唯一")
    after = terminal_events[0].after_jsonb
    if (
        not isinstance(after, dict)
        or after.get("effective_approval_completion_id") != str(approval.id)
        or after.get("effective_approval_manifest_sha256")
        != approval.approval_manifest_sha256
        or after.get("effective_scope_count") != approval.scope_count
    ):
        _evidence_invalid("总部最终审计未绑定任务级有效审批封印")


def _verify_inventory_transaction_audit(
    db: Session,
    transaction: InventoryTransaction,
    proof: object,
) -> None:
    events = tuple(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.stream_key == INVENTORY_STREAM_KEY,
                AuditEvent.action
                == "inventory.transaction.stocktake_difference_posted",
                AuditEvent.aggregate_type == "inventory_transaction",
                AuditEvent.aggregate_id == str(transaction.id),
            )
        ).all()
    )
    if len(events) != 1:
        _evidence_invalid("盘点库存交易审计事实缺失或不唯一")
    verified = _verify_audit_event_with_prelocked_proof(
        db,
        proof=proof,
        stream_key=INVENTORY_STREAM_KEY,
        event_id=events[0].id,
    )
    if (
        not isinstance(verified.after_jsonb, dict)
        or verified.after_jsonb.get("ledger_cursor") != transaction.ledger_cursor
        or verified.after_jsonb.get("movement_type") != transaction.movement_type
        or verified.after_jsonb.get("posting_key") != transaction.posting_key
        or verified.after_jsonb.get("status") != "posted"
    ):
        _evidence_invalid("盘点库存交易审计摘要与流水不一致")


def _build_nonopening_stocktake_posting_plan(
    *,
    task: FormalStocktakeTask,
    expected_task_version: int,
    scopes: Sequence[FormalStocktakeScope],
    rounds: Sequence[StocktakeRound],
    recount_cases: Sequence[StocktakeRecountCase],
    recount_assignments: Sequence[StocktakeRecountScopeAssignment],
    submissions: Sequence[StocktakeRoundSubmission],
    completions: Sequence[StocktakeDifferenceSetCompletion],
    differences: Sequence[StocktakeDifference],
    reviews: Sequence[StocktakeReview],
    review_items: Sequence[StocktakeReviewItem],
    observations: Sequence[StocktakeCountObservation],
    accounts: Mapping[uuid.UUID, StockAccount],
    observation_account_ids: Mapping[uuid.UUID, uuid.UUID],
    effective_approval_completion: EffectiveApprovalCompletionEvidence,
    effective_approval_scopes: Sequence[EffectiveApprovalScopeEvidence],
    effective_approval_items: Sequence[EffectiveApprovalItemEvidence],
) -> StocktakeDifferencePostingPlan:
    """Derive the exact effective-scope adjustment plan without any writes.

    Every effective scope must have an explicit regional item decision and an
    explicit final-HQ item decision in one task-wide terminal approval seal.
    This supports partial recount without silently turning an old top-level
    ``recount`` into approval for untouched scopes.
    """

    _validate_task_root(task, expected_task_version)
    ordered_scopes = _validate_scopes(task, scopes)
    ordered_rounds = _validate_rounds(task, rounds)
    submission_by_round, completion_by_round, differences_by_round = (
        _validate_round_evidence(
            task,
            ordered_rounds,
            submissions,
            completions,
            differences,
            scope_ids={row.id for row in ordered_scopes},
        )
    )
    reviews_by_round, items_by_review = _validate_review_rows(
        task,
        ordered_rounds,
        differences_by_round,
        reviews,
        review_items,
    )
    effective_round_by_scope = _select_effective_rounds(
        task=task,
        scopes=ordered_scopes,
        rounds=ordered_rounds,
        recount_cases=recount_cases,
        recount_assignments=recount_assignments,
        submissions_by_round=submission_by_round,
        completions_by_round=completion_by_round,
        differences_by_round=differences_by_round,
        reviews_by_round=reviews_by_round,
        items_by_review=items_by_review,
    )
    approval_scope_by_id, approval_items_by_scope = _validate_effective_approval(
        task=task,
        rounds=ordered_rounds,
        scopes=ordered_scopes,
        completion_by_round=completion_by_round,
        differences_by_round=differences_by_round,
        reviews_by_round=reviews_by_round,
        items_by_review=items_by_review,
        effective_round_by_scope=effective_round_by_scope,
        approval=effective_approval_completion,
        approval_scopes=effective_approval_scopes,
        approval_items=effective_approval_items,
    )

    observations_by_id = _unique_mapping(
        observations,
        key=lambda row: row.id,
        code="stocktake_posting_observation_duplicate",
        message="盘点现场观察事实不唯一",
    )
    effective_scope_rows: list[EffectiveScopeRound] = []
    planned_movements: list[PlannedStocktakeMovement] = []
    no_adjustment_ids: list[uuid.UUID] = []
    effective_difference_ids: set[uuid.UUID] = set()
    seen_serials: set[uuid.UUID] = set()
    scope_by_id = {row.id: row for row in ordered_scopes}

    for scope in ordered_scopes:
        round_row = effective_round_by_scope[scope.id]
        completion = completion_by_round[round_row.id]
        # Membership was already proved by the task-wide completion validator.
        assert approval_scope_by_id[scope.id].scope_id == scope.id
        regional = reviews_by_round[round_row.id]["region"]
        approval_items = approval_items_by_scope[scope.id]
        scope_differences = tuple(
            row
            for row in differences_by_round[round_row.id]
            if row.scope_id == scope.id
        )
        for difference in scope_differences:
            approval_item = approval_items[difference.id]
            regional_decision = approval_item.regional_decision
            headquarters_decision = approval_item.headquarters_decision
            if (
                regional_decision not in POSTABLE_ITEM_DECISIONS
                or headquarters_decision != regional_decision
            ):
                _fail(
                    "stocktake_posting_scope_not_approved",
                    "precondition_failed",
                    "有效盘点范围仍包含待核实、复盘、驳回或不一致决定",
                )
            if difference.reason_code == "stocktake_pending_verification":
                _fail(
                    "stocktake_posting_pending_observation",
                    "precondition_failed",
                    "待核实现场观察不得生成或跳过库存调整",
                )
            if difference.id in effective_difference_ids:
                _invalid_graph("同一有效差异被多个范围重复选择")
            effective_difference_ids.add(difference.id)
            if regional_decision == "no_adjustment":
                no_adjustment_ids.append(difference.id)
                continue
            movement = _plan_difference_movement(
                task=task,
                scope=scope_by_id[difference.scope_id],
                difference=difference,
                observations_by_id=observations_by_id,
                accounts=accounts,
                observation_account_ids=observation_account_ids,
                task_scopes=ordered_scopes,
            )
            overlap = seen_serials.intersection(movement.serial_ids)
            if overlap:
                _fail(
                    "stocktake_posting_serial_reused",
                    "service_unavailable",
                    "同一 SN 被多个有效差异重复调整",
                )
            seen_serials.update(movement.serial_ids)
            planned_movements.append(movement)
        effective_scope_rows.append(
            EffectiveScopeRound(
                scope_id=scope.id,
                round_id=round_row.id,
                round_no=round_row.round_no,
                difference_completion_id=completion.id,
                regional_review_id=regional.id,
                effective_approval_completion_id=(
                    effective_approval_completion.id
                ),
            )
        )

    ordered_effective_scopes = tuple(
        sorted(effective_scope_rows, key=lambda row: str(row.scope_id))
    )
    ordered_movements = tuple(
        sorted(
            planned_movements,
            key=lambda row: (
                MOVEMENT_TYPES.index(row.movement_type),
                str(row.round_id),
                str(row.difference_id),
            ),
        )
    )
    ordered_no_adjustment = tuple(sorted(no_adjustment_ids, key=str))
    effective_count = len(effective_difference_ids)
    if effective_count != len(ordered_movements) + len(ordered_no_adjustment):
        _invalid_graph("有效差异未被逐项绑定为调整或不调整证据")
    manifest = _plan_manifest(
        task_id=task.id,
        expected_task_version=expected_task_version,
        scopes=ordered_effective_scopes,
        movements=ordered_movements,
        no_adjustment_ids=ordered_no_adjustment,
        effective_difference_count=effective_count,
    )
    return StocktakeDifferencePostingPlan(
        task_id=task.id,
        expected_task_version=expected_task_version,
        effective_scope_rounds=ordered_effective_scopes,
        movements=ordered_movements,
        no_adjustment_difference_ids=ordered_no_adjustment,
        effective_difference_count=effective_count,
        plan_manifest_sha256=manifest,
    )


def _validate_task_root(task: FormalStocktakeTask, expected_version: int) -> None:
    if (
        task.task_type not in NON_OPENING_TYPES
        or task.status != "approved"
        or not isinstance(expected_version, int)
        or isinstance(expected_version, bool)
        or expected_version < 0
    ):
        _fail(
            "stocktake_posting_task_not_postable",
            "precondition_failed",
            "仅已完成总部复核的非期初盘点任务可规划差异过账",
        )
    if task.version != expected_version:
        _fail(
            "stocktake_posting_version_conflict",
            "conflict",
            "盘点任务版本已变化，请重新读取",
        )
    if (
        task.cutoff_ledger_cursor is None
        or task.cutoff_ledger_cursor < 0
        or task.cutoff_at is None
        or task.submitted_at is None
        or task.posted_at is not None
        or task.closed_at is not None
    ):
        _fail(
            "stocktake_posting_task_evidence_invalid",
            "service_unavailable",
            "盘点任务截止、提交或终态坐标无效",
        )


def _validate_scopes(
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
) -> tuple[FormalStocktakeScope, ...]:
    ordered = tuple(sorted(scopes, key=lambda row: (row.scope_no, str(row.id))))
    if (
        not ordered
        or [row.scope_no for row in ordered] != list(range(1, len(ordered) + 1))
        or len({row.id for row in ordered}) != len(ordered)
        or any(row.task_id != task.id for row in ordered)
    ):
        _invalid_graph("盘点范围缺失、重复或序号不连续")
    return ordered


def _validate_rounds(
    task: FormalStocktakeTask,
    rounds: Sequence[StocktakeRound],
) -> tuple[StocktakeRound, ...]:
    ordered = tuple(sorted(rounds, key=lambda row: (row.round_no, str(row.id))))
    if (
        not ordered
        or len({row.id for row in ordered}) != len(ordered)
        or [row.round_no for row in ordered]
        != list(range(1, task.current_round_no + 1))
        or any(
            row.task_id != task.id
            or row.status != "submitted"
            or row.submitted_at is None
            or row.count_manifest_sha256 is None
            or not _SHA256.fullmatch(row.count_manifest_sha256)
            for row in ordered
        )
        or ordered[0].round_type != "initial"
        or ordered[0].recount_case_id is not None
        or any(
            row.round_type != "recount" or row.recount_case_id is None
            for row in ordered[1:]
        )
    ):
        _invalid_graph("盘点轮次链缺失、不连续或未完整提交")
    return ordered


def _validate_round_evidence(
    task: FormalStocktakeTask,
    rounds: Sequence[StocktakeRound],
    submissions: Sequence[StocktakeRoundSubmission],
    completions: Sequence[StocktakeDifferenceSetCompletion],
    differences: Sequence[StocktakeDifference],
    *,
    scope_ids: set[uuid.UUID],
) -> tuple[
    dict[uuid.UUID, StocktakeRoundSubmission],
    dict[uuid.UUID, StocktakeDifferenceSetCompletion],
    dict[uuid.UUID, tuple[StocktakeDifference, ...]],
]:
    round_ids = {row.id for row in rounds}
    submission_by_round = _unique_mapping(
        submissions,
        key=lambda row: row.round_id,
        code="stocktake_posting_submission_duplicate",
        message="盘点轮次提交封印不唯一",
    )
    completion_by_round = _unique_mapping(
        completions,
        key=lambda row: row.round_id,
        code="stocktake_posting_completion_duplicate",
        message="盘点差异集封印不唯一",
    )
    if set(submission_by_round) != round_ids or set(completion_by_round) != round_ids:
        _invalid_graph("每个盘点轮次必须且只能有一个提交及差异集封印")

    differences_by_round: dict[uuid.UUID, list[StocktakeDifference]] = defaultdict(list)
    seen_difference_ids: set[uuid.UUID] = set()
    for difference in differences:
        if difference.id in seen_difference_ids:
            _invalid_graph("盘点差异主键重复")
        seen_difference_ids.add(difference.id)
        if (
            difference.task_id != task.id
            or difference.round_id not in round_ids
            or difference.scope_id not in scope_ids
            or difference.control_snapshot_line_id is not None
            or not difference.evidence_required
        ):
            _invalid_graph("非期初差异混入任务外范围或控制总账证据")
        differences_by_round[difference.round_id].append(difference)

    frozen: dict[uuid.UUID, tuple[StocktakeDifference, ...]] = {}
    for round_row in rounds:
        rows = tuple(
            sorted(
                differences_by_round.get(round_row.id, ()),
                key=lambda row: (row.difference_no, str(row.id)),
            )
        )
        if [row.difference_no for row in rows] != list(range(1, len(rows) + 1)):
            _invalid_graph("盘点差异序号不连续")
        submission = submission_by_round[round_row.id]
        completion = completion_by_round[round_row.id]
        pending_count = sum(
            row.reason_code == "stocktake_pending_verification" for row in rows
        )
        total = sum((row.affected_qty for row in rows), start=_ZERO)
        if (
            submission.task_id != task.id
            or completion.task_id != task.id
            or completion.round_submission_id != submission.id
            or completion.difference_count != len(rows)
            or completion.physical_difference_count != len(rows)
            or completion.control_difference_count != 0
            or completion.pending_observation_difference_count != pending_count
            or completion.total_affected_qty != total
            or not _SHA256.fullmatch(completion.difference_manifest_sha256 or "")
        ):
            _invalid_graph("盘点差异集汇总与轮次封印不一致")
        frozen[round_row.id] = rows
    return submission_by_round, completion_by_round, frozen


def _validate_review_rows(
    task: FormalStocktakeTask,
    rounds: Sequence[StocktakeRound],
    differences_by_round: Mapping[uuid.UUID, tuple[StocktakeDifference, ...]],
    reviews: Sequence[StocktakeReview],
    review_items: Sequence[StocktakeReviewItem],
) -> tuple[
    dict[uuid.UUID, dict[str, StocktakeReview]],
    dict[uuid.UUID, dict[uuid.UUID, StocktakeReviewItem]],
]:
    round_ids = {row.id for row in rounds}
    reviews_by_round: dict[uuid.UUID, dict[str, StocktakeReview]] = {
        row.id: {} for row in rounds
    }
    review_by_id: dict[uuid.UUID, StocktakeReview] = {}
    for review in reviews:
        if (
            review.id in review_by_id
            or review.task_id != task.id
            or review.round_id not in round_ids
            or review.review_stage not in {"region", "headquarters"}
            or review.review_stage in reviews_by_round[review.round_id]
        ):
            _invalid_graph("盘点复核事实重复或坐标无效")
        review_by_id[review.id] = review
        reviews_by_round[review.round_id][review.review_stage] = review

    items_by_review: dict[uuid.UUID, dict[uuid.UUID, StocktakeReviewItem]] = {
        review.id: {} for review in reviews
    }
    for item in review_items:
        review = review_by_id.get(item.review_id)
        if (
            review is None
            or item.task_id != task.id
            or item.round_id != review.round_id
            or item.difference_id in items_by_review[review.id]
        ):
            _invalid_graph("盘点逐项复核事实重复或坐标无效")
        items_by_review[review.id][item.difference_id] = item
    for review in reviews:
        expected_ids = {row.id for row in differences_by_round[review.round_id]}
        if set(items_by_review[review.id]) != expected_ids:
            _invalid_graph("每个复核必须逐项覆盖所在轮次的完整差异集")
    return reviews_by_round, items_by_review


def _select_effective_rounds(
    *,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    rounds: Sequence[StocktakeRound],
    recount_cases: Sequence[StocktakeRecountCase],
    recount_assignments: Sequence[StocktakeRecountScopeAssignment],
    submissions_by_round: Mapping[uuid.UUID, StocktakeRoundSubmission],
    completions_by_round: Mapping[uuid.UUID, StocktakeDifferenceSetCompletion],
    differences_by_round: Mapping[uuid.UUID, tuple[StocktakeDifference, ...]],
    reviews_by_round: Mapping[uuid.UUID, Mapping[str, StocktakeReview]],
    items_by_review: Mapping[uuid.UUID, Mapping[uuid.UUID, StocktakeReviewItem]],
) -> dict[uuid.UUID, StocktakeRound]:
    scope_ids = {row.id for row in scopes}
    case_by_id = _unique_mapping(
        recount_cases,
        key=lambda row: row.id,
        code="stocktake_posting_recount_case_duplicate",
        message="盘点复盘因果事实不唯一",
    )
    expected_case_ids = {row.recount_case_id for row in rounds[1:]}
    if None in expected_case_ids or set(case_by_id) != expected_case_ids:
        _invalid_graph("盘点轮次与复盘因果事实不是一一对应")
    assignments_by_case: dict[uuid.UUID, list[StocktakeRecountScopeAssignment]] = (
        defaultdict(list)
    )
    for assignment in recount_assignments:
        if assignment.recount_case_id not in case_by_id:
            _invalid_graph("复盘范围分配不属于当前轮次链")
        assignments_by_case[assignment.recount_case_id].append(assignment)

    effective = {scope_id: rounds[0] for scope_id in scope_ids}
    previous = rounds[0]
    for current in rounds[1:]:
        case = case_by_id[current.recount_case_id]
        source_reviews = reviews_by_round[previous.id]
        trigger = _terminal_recount_trigger(source_reviews)
        trigger_items = items_by_review[trigger.id]
        source_differences = differences_by_round[previous.id]
        required_scope_ids = _recount_scope_ids(
            trigger,
            trigger_items,
            source_differences,
        )
        assignments = tuple(
            sorted(
                assignments_by_case.get(case.id, ()),
                key=lambda row: (str(row.scope_id), str(row.id)),
            )
        )
        assigned_scope_ids = {row.scope_id for row in assignments}
        if (
            case.task_id != task.id
            or case.source_round_id != previous.id
            or case.source_round_submission_id != submissions_by_round[previous.id].id
            or case.source_difference_completion_id
            != completions_by_round[previous.id].id
            or case.trigger_review_id != trigger.id
            or case.next_round_no != current.round_no
            or current.recount_case_id != case.id
            or case.scope_count != len(assignments)
            or len(assigned_scope_ids) != len(assignments)
            or assigned_scope_ids != required_scope_ids
            or not assigned_scope_ids.issubset(scope_ids)
            or any(
                row.task_id != task.id
                or row.source_round_id != previous.id
                for row in assignments
            )
        ):
            _invalid_graph("复盘因果、范围分配或封印坐标不一致")
        if any(
            row.scope_id not in assigned_scope_ids
            for row in differences_by_round[current.id]
        ):
            _invalid_graph("复盘轮次差异超出该轮精确分配范围")
        for scope_id in assigned_scope_ids:
            effective[scope_id] = current
        previous = current
    return effective


def _validate_effective_approval(
    *,
    task: FormalStocktakeTask,
    rounds: Sequence[StocktakeRound],
    scopes: Sequence[FormalStocktakeScope],
    completion_by_round: Mapping[uuid.UUID, StocktakeDifferenceSetCompletion],
    differences_by_round: Mapping[uuid.UUID, tuple[StocktakeDifference, ...]],
    reviews_by_round: Mapping[uuid.UUID, Mapping[str, StocktakeReview]],
    items_by_review: Mapping[uuid.UUID, Mapping[uuid.UUID, StocktakeReviewItem]],
    effective_round_by_scope: Mapping[uuid.UUID, StocktakeRound],
    approval: EffectiveApprovalCompletionEvidence,
    approval_scopes: Sequence[EffectiveApprovalScopeEvidence],
    approval_items: Sequence[EffectiveApprovalItemEvidence],
) -> tuple[
    dict[uuid.UUID, EffectiveApprovalScopeEvidence],
    dict[uuid.UUID, dict[uuid.UUID, EffectiveApprovalItemEvidence]],
]:
    """Recompute the task-wide final-HQ seal from exact causal coordinates."""

    terminal_round = rounds[-1]
    _regional, terminal_hq = _require_terminal_approvals(
        terminal_round,
        reviews_by_round[terminal_round.id],
    )
    if (
        not isinstance(approval, EffectiveApprovalCompletionEvidence)
        or approval.task_id != task.id
        or approval.terminal_round_id != terminal_round.id
        or approval.terminal_headquarters_review_id != terminal_hq.id
        or not _SHA256.fullmatch(approval.approval_manifest_sha256 or "")
    ):
        _fail(
            "stocktake_posting_effective_approval_missing",
            "precondition_failed",
            "缺少与最终总部复核绑定的任务级有效审批封印",
        )

    scope_by_id = _unique_mapping(
        approval_scopes,
        key=lambda row: row.scope_id,
        code="stocktake_posting_effective_scope_duplicate",
        message="任务级有效审批范围不唯一",
    )
    expected_scope_ids = {row.id for row in scopes}
    if (
        set(scope_by_id) != expected_scope_ids
        or approval.scope_count != len(scopes)
        or any(row.completion_id != approval.id for row in approval_scopes)
    ):
        _invalid_graph("任务级有效审批未显式覆盖全部盘点范围")

    items_by_scope: dict[
        uuid.UUID, dict[uuid.UUID, EffectiveApprovalItemEvidence]
    ] = {scope_id: {} for scope_id in expected_scope_ids}
    for item in approval_items:
        if (
            item.completion_id != approval.id
            or item.scope_id not in items_by_scope
            or item.difference_id in items_by_scope[item.scope_id]
        ):
            _invalid_graph("任务级有效审批逐项事实重复或坐标无效")
        items_by_scope[item.scope_id][item.difference_id] = item

    manifest_scopes: list[dict[str, object]] = []
    total_differences = 0
    for scope in sorted(scopes, key=lambda row: str(row.id)):
        source_round = effective_round_by_scope[scope.id]
        source_completion = completion_by_round[source_round.id]
        source_reviews = reviews_by_round[source_round.id]
        regional = source_reviews.get("region")
        if regional is None:
            _invalid_graph("有效范围来源轮次缺少地区逐项复核事实")
        assert regional is not None
        source_scope = scope_by_id[scope.id]
        source_differences = tuple(
            row
            for row in differences_by_round[source_round.id]
            if row.scope_id == scope.id
        )
        source_items = items_by_scope[scope.id]
        expected_difference_ids = {row.id for row in source_differences}
        if (
            source_scope.completion_id != approval.id
            or source_scope.source_round_id != source_round.id
            or source_scope.source_difference_completion_id
            != source_completion.id
            or source_scope.regional_review_id != regional.id
            or source_scope.difference_count != len(source_differences)
            or set(source_items) != expected_difference_ids
        ):
            _invalid_graph("有效审批范围与最新因果轮次或差异集不一致")
        regional_items = items_by_review[regional.id]
        source_hq = source_reviews.get("headquarters")
        source_hq_items = (
            items_by_review[source_hq.id] if source_hq is not None else {}
        )
        manifest_items: list[dict[str, object]] = []
        for difference in source_differences:
            item = source_items[difference.id]
            persisted_region_item = regional_items.get(difference.id)
            persisted_hq_item = source_hq_items.get(difference.id)
            if (
                item.source_round_id != source_round.id
                or item.regional_review_id != regional.id
                or persisted_region_item is None
                or item.regional_decision != persisted_region_item.decision
                or item.regional_decision not in POSTABLE_ITEM_DECISIONS
                or item.headquarters_decision != item.regional_decision
                or (
                    persisted_hq_item is not None
                    and persisted_hq_item.decision != item.headquarters_decision
                )
            ):
                _fail(
                    "stocktake_posting_scope_not_approved",
                    "precondition_failed",
                    "最终总部有效审批不得放宽、改写或猜测地区逐项终态决定",
                )
            manifest_items.append(
                {
                    "difference_id": str(difference.id),
                    "headquarters_decision": item.headquarters_decision,
                    "regional_decision": item.regional_decision,
                }
            )
        total_differences += len(source_differences)
        manifest_scopes.append(
            {
                "difference_count": len(source_differences),
                "items": manifest_items,
                "regional_review_id": str(regional.id),
                "scope_id": str(scope.id),
                "source_difference_completion_id": str(source_completion.id),
                "source_round_id": str(source_round.id),
            }
        )

    expected_manifest = _sha256(
        {
            "completion_id": str(approval.id),
            "difference_count": total_differences,
            "schema": "cloud_oam.stocktake.effective_approval.v1",
            "scope_count": len(scopes),
            "scopes": manifest_scopes,
            "task_id": str(task.id),
            "terminal_headquarters_review_id": str(terminal_hq.id),
            "terminal_round_id": str(terminal_round.id),
        }
    )
    if (
        approval.difference_count != total_differences
        or approval.approval_manifest_sha256 != expected_manifest
    ):
        _invalid_graph("任务级有效审批汇总或 manifest 无法从逐项事实重算")
    return scope_by_id, items_by_scope


def _terminal_recount_trigger(
    reviews: Mapping[str, StocktakeReview],
) -> StocktakeReview:
    regional = reviews.get("region")
    headquarters = reviews.get("headquarters")
    if regional is None:
        _invalid_graph("复盘来源轮次缺少区域复核")
    assert regional is not None
    if regional.decision == "approve":
        if headquarters is None or headquarters.decision not in {"recount", "reject"}:
            _invalid_graph("复盘来源轮次缺少总部复盘或驳回终态")
        return headquarters
    if regional.decision not in {"recount", "reject"} or headquarters is not None:
        _invalid_graph("区域非通过后的复盘终态链无效")
    return regional


def _recount_scope_ids(
    trigger: StocktakeReview,
    items: Mapping[uuid.UUID, StocktakeReviewItem],
    differences: Sequence[StocktakeDifference],
) -> set[uuid.UUID]:
    difference_by_id = {row.id: row for row in differences}
    selected = tuple(items.values())
    if trigger.decision == "recount":
        selected = tuple(
            row for row in selected if row.decision in RECOUNT_ITEM_DECISIONS
        )
    scope_ids: set[uuid.UUID] = set()
    for item in selected:
        difference = difference_by_id.get(item.difference_id)
        if difference is None or difference.scope_id is None:
            _invalid_graph("复盘逐项决定缺少精确差异范围")
        scope_ids.add(difference.scope_id)
    if not scope_ids:
        _invalid_graph("复盘终态没有可追溯的精确范围")
    return scope_ids


def _require_terminal_approvals(
    round_row: StocktakeRound,
    reviews: Mapping[str, StocktakeReview],
) -> tuple[StocktakeReview, StocktakeReview]:
    regional = reviews.get("region")
    headquarters = reviews.get("headquarters")
    if (
        regional is None
        or headquarters is None
        or regional.round_id != round_row.id
        or headquarters.round_id != round_row.id
        or regional.decision != "approve"
        or headquarters.decision != "approve"
        or headquarters.reviewed_at <= regional.reviewed_at
        or headquarters.reviewer_user_id == regional.reviewer_user_id
        or headquarters.reviewer_person_id == regional.reviewer_person_id
    ):
        _fail(
            "stocktake_posting_scope_not_terminally_approved",
            "precondition_failed",
            "每个有效盘点范围必须来自职责分离且地区、总部均通过的终态轮次",
        )
    return regional, headquarters


def _plan_difference_movement(
    *,
    task: FormalStocktakeTask,
    scope: FormalStocktakeScope,
    difference: StocktakeDifference,
    observations_by_id: Mapping[uuid.UUID, StocktakeCountObservation],
    accounts: Mapping[uuid.UUID, StockAccount],
    observation_account_ids: Mapping[uuid.UUID, uuid.UUID],
    task_scopes: Sequence[FormalStocktakeScope],
) -> PlannedStocktakeMovement:
    expected = _account(accounts, difference.expected_account_id)
    observed_id = difference.observed_account_id
    if observed_id is None and difference.observed_line_id is not None:
        observed_id = observation_account_ids.get(difference.observed_line_id)
    observed = _account(accounts, observed_id)

    if expected is not None:
        _validate_account_material_and_scope(expected, difference, task_scopes)
    if observed is not None:
        _validate_account_material_and_scope(observed, difference, (scope,))
    if difference.observed_line_id is not None:
        _validate_observation_account(
            task=task,
            scope=scope,
            difference=difference,
            observation=observations_by_id.get(difference.observed_line_id),
            account=observed,
        )

    serial_ids = (difference.serial_id,) if difference.serial_id is not None else ()
    if serial_ids and difference.affected_qty != _ONE:
        _invalid_graph("SN 差异调整数量必须精确为 1")

    if difference.difference_type == "missing":
        if (
            expected is None
            or observed is not None
            or difference.difference_qty >= _ZERO
            or -difference.difference_qty != difference.affected_qty
        ):
            _invalid_graph("盘亏差异端点或数量无效")
        movement_type = "stocktake_loss"
        from_id, to_id, boundary = expected.id, None, EXTERNAL_BOUNDARY_CODE
    elif difference.difference_type == "excess":
        if (
            expected is not None
            or observed is None
            or difference.difference_qty <= _ZERO
            or difference.difference_qty != difference.affected_qty
        ):
            _invalid_graph("盘盈差异端点或数量无效")
        movement_type = "stocktake_gain"
        from_id, to_id, boundary = None, observed.id, EXTERNAL_BOUNDARY_CODE
    elif difference.difference_type in {
        "wrong_location",
        "wrong_condition",
        "wrong_lot",
    }:
        if (
            expected is None
            or observed is None
            or expected.id == observed.id
            or difference.difference_qty != _ZERO
            or difference.book_qty != difference.affected_qty
            or difference.counted_qty != difference.affected_qty
        ):
            _invalid_graph("错位置、错成色或错批次差异端点或数量无效")
        _validate_dimension_transition(difference.difference_type, expected, observed)
        movement_type = (
            "status_change"
            if difference.difference_type == "wrong_condition"
            else "transfer"
        )
        from_id, to_id, boundary = expected.id, observed.id, None
    elif difference.difference_type == "wrong_serial":
        if expected is None and observed is not None:
            if difference.difference_qty != difference.affected_qty:
                _invalid_graph("多余 SN 差异数量无效")
            movement_type = "stocktake_gain"
            from_id, to_id, boundary = None, observed.id, EXTERNAL_BOUNDARY_CODE
        elif expected is not None and observed is None:
            if difference.difference_qty != -difference.affected_qty:
                _invalid_graph("缺失 SN 差异数量无效")
            movement_type = "stocktake_loss"
            from_id, to_id, boundary = expected.id, None, EXTERNAL_BOUNDARY_CODE
        elif expected is not None and observed is not None and expected.id != observed.id:
            if difference.difference_qty != _ZERO:
                _invalid_graph("错位 SN 差异数量无效")
            movement_type = "transfer"
            from_id, to_id, boundary = expected.id, observed.id, None
        else:
            _invalid_graph("错 SN 差异缺少唯一可过账端点")
    else:
        _fail(
            "stocktake_posting_difference_type_forbidden",
            "precondition_failed",
            "非期初过账包含不可转换为库存流水的差异类型",
        )

    return PlannedStocktakeMovement(
        difference_id=difference.id,
        scope_id=scope.id,
        round_id=difference.round_id,
        movement_type=movement_type,
        from_account_id=from_id,
        to_account_id=to_id,
        quantity=difference.affected_qty,
        serial_ids=serial_ids,
        external_boundary_code=boundary,
    )


def _validate_observation_account(
    *,
    task: FormalStocktakeTask,
    scope: FormalStocktakeScope,
    difference: StocktakeDifference,
    observation: StocktakeCountObservation | None,
    account: StockAccount | None,
) -> None:
    if (
        observation is None
        or account is None
        or observation.task_id != task.id
        or observation.round_id != difference.round_id
        or observation.scope_id != scope.id
        or observation.verification_status != "verified"
        or observation.material_id is None
        or observation.material_id != difference.material_id
        or observation.owner_org_id != account.owner_org_id
        or observation.location_id != account.location_id
        or observation.custodian_person_id_snapshot != account.custodian_person_id
        or observation.material_id != account.material_id
        or observation.condition_code != account.condition_code
        or observation.availability_bucket != account.availability_bucket
        or observation.lot_id != account.lot_id
        or (
            observation.serial_id is not None
            and observation.serial_id != difference.serial_id
        )
    ):
        _fail(
            "stocktake_posting_observation_account_invalid",
            "precondition_failed",
            "现场新增观察未唯一解析为完全相同维度的库存账户",
        )


def _validate_account_material_and_scope(
    account: StockAccount,
    difference: StocktakeDifference,
    scopes: Sequence[FormalStocktakeScope],
) -> None:
    if account.material_id != difference.material_id or not any(
        _account_matches_scope(account, scope) for scope in scopes
    ):
        _invalid_graph("差异账户物料或盘点范围绑定无效")


def _account_matches_scope(account: StockAccount, scope: FormalStocktakeScope) -> bool:
    return bool(
        account.owner_org_id == scope.owner_org_id
        and account.location_id == scope.location_id
        and (
            scope.scope_mode == "location_all"
            or (
                scope.scope_mode == "filtered"
                and (scope.material_id is None or scope.material_id == account.material_id)
                and (
                    scope.condition_code is None
                    or scope.condition_code == account.condition_code
                )
                and (
                    scope.availability_bucket is None
                    or scope.availability_bucket == account.availability_bucket
                )
            )
        )
    )


def _validate_dimension_transition(
    difference_type: str,
    expected: StockAccount,
    observed: StockAccount,
) -> None:
    same = {
        "owner": expected.owner_org_id == observed.owner_org_id,
        "custodian": expected.custodian_person_id == observed.custodian_person_id,
        "location": expected.location_id == observed.location_id,
        "material": expected.material_id == observed.material_id,
        "condition": expected.condition_code == observed.condition_code,
        "availability": expected.availability_bucket == observed.availability_bucket,
        "lot": expected.lot_id == observed.lot_id,
    }
    if difference_type == "wrong_location":
        valid = (
            same["owner"]
            and same["material"]
            and same["condition"]
            and same["availability"]
            and same["lot"]
            and not same["location"]
        )
    elif difference_type == "wrong_condition":
        valid = all(
            same[key]
            for key in (
                "owner",
                "custodian",
                "location",
                "material",
                "availability",
                "lot",
            )
        ) and not same["condition"]
    else:
        valid = all(
            same[key]
            for key in (
                "owner",
                "custodian",
                "location",
                "material",
                "condition",
                "availability",
            )
        ) and not same["lot"]
    if not valid:
        _invalid_graph("差异类型与库存账户实际维度变化不一致")


def _account(
    accounts: Mapping[uuid.UUID, StockAccount],
    account_id: uuid.UUID | None,
) -> StockAccount | None:
    if account_id is None:
        return None
    account = accounts.get(account_id)
    if account is None or account.id != account_id:
        _fail(
            "stocktake_posting_account_missing",
            "precondition_failed",
            "差异关联的库存账户不存在或尚未完成安全物化",
        )
    return account


def _plan_manifest(
    *,
    task_id: uuid.UUID,
    expected_task_version: int,
    scopes: Sequence[EffectiveScopeRound],
    movements: Sequence[PlannedStocktakeMovement],
    no_adjustment_ids: Sequence[uuid.UUID],
    effective_difference_count: int,
) -> str:
    return _sha256(
        {
            "effective_difference_count": effective_difference_count,
            "effective_scope_rounds": [
                {
                    "difference_completion_id": str(row.difference_completion_id),
                    "effective_approval_completion_id": str(
                        row.effective_approval_completion_id
                    ),
                    "regional_review_id": str(row.regional_review_id),
                    "round_id": str(row.round_id),
                    "round_no": row.round_no,
                    "scope_id": str(row.scope_id),
                }
                for row in scopes
            ],
            "expected_task_version": expected_task_version,
            "movements": [
                {
                    "difference_id": str(row.difference_id),
                    "external_boundary_code": row.external_boundary_code,
                    "from_account_id": (
                        str(row.from_account_id)
                        if row.from_account_id is not None
                        else None
                    ),
                    "movement_type": row.movement_type,
                    "quantity": _canonical_quantity(row.quantity),
                    "round_id": str(row.round_id),
                    "scope_id": str(row.scope_id),
                    "serial_ids": [str(value) for value in row.serial_ids],
                    "to_account_id": (
                        str(row.to_account_id)
                        if row.to_account_id is not None
                        else None
                    ),
                }
                for row in movements
            ],
            "no_adjustment_difference_ids": [
                str(value) for value in no_adjustment_ids
            ],
            "schema": "cloud_oam.stocktake.nonopening_posting_plan.v1",
            "task_id": str(task_id),
        }
    )


def _posting_item_document(
    completion_id: uuid.UUID,
    task_id: uuid.UUID,
    binding: _PostingBinding,
) -> dict[str, object]:
    return {
        "completion_id": str(completion_id),
        "decision": binding.decision,
        "difference_id": str(binding.difference_id),
        "inventory_movement_id": (
            str(binding.inventory_movement_id)
            if binding.inventory_movement_id is not None
            else None
        ),
        "inventory_transaction_id": (
            str(binding.inventory_transaction_id)
            if binding.inventory_transaction_id is not None
            else None
        ),
        "posting_id": str(binding.posting_id) if binding.posting_id is not None else None,
        "posting_kind": binding.posting_kind,
        "quantity": _canonical_quantity(binding.quantity),
        "schema": "cloud_oam.stocktake.nonopening_posting_item.v1",
        "scope_id": str(binding.scope_id),
        "source_round_id": str(binding.source_round_id),
        "task_id": str(task_id),
    }


def _posting_event_metadata(
    completion: StocktakePostingCompletion,
) -> dict[str, object]:
    return {
        "accepted_difference_count": completion.accepted_difference_count,
        "approval_manifest_sha256": completion.approval_manifest_sha256,
        "difference_count": completion.difference_count,
        "effective_approval_completion_id": str(
            completion.effective_approval_completion_id
        ),
        "first_ledger_cursor": completion.first_ledger_cursor,
        "last_ledger_cursor": completion.last_ledger_cursor,
        "movement_count": completion.movement_count,
        "no_adjustment_count": completion.no_adjustment_count,
        "posting_completion_id": str(completion.id),
        "posting_manifest_sha256": completion.posting_manifest_sha256,
        "request_sha256": completion.request_sha256,
        "schema": "cloud_oam.stocktake.nonopening_posted_event.v1",
        "scope_count": completion.scope_count,
        "status": "posted",
        "task_id": str(completion.task_id),
        "task_version": completion.posted_task_version,
        "terminal_round_id": str(completion.terminal_round_id),
        "total_quantity": _canonical_quantity(completion.total_quantity),
        "transaction_count": completion.transaction_count,
    }


def _posting_result(
    task: FormalStocktakeTask,
    completion: StocktakePostingCompletion,
    *,
    replayed: bool,
) -> StocktakeDifferencePostingResult:
    return StocktakeDifferencePostingResult(
        completion_id=completion.id,
        task_id=completion.task_id,
        terminal_round_id=completion.terminal_round_id,
        resulting_task_status=task.status,
        task_version=task.version,
        scope_count=completion.scope_count,
        difference_count=completion.difference_count,
        accepted_difference_count=completion.accepted_difference_count,
        no_adjustment_count=completion.no_adjustment_count,
        transaction_count=completion.transaction_count,
        movement_count=completion.movement_count,
        total_quantity=completion.total_quantity,
        first_ledger_cursor=completion.first_ledger_cursor,
        last_ledger_cursor=completion.last_ledger_cursor,
        replayed=replayed,
    )


def _posting_request_sha256(
    actor: FormalPrincipal,
    command: PostApprovedStocktakeDifferencesCommand,
    approval: StocktakeEffectiveApprovalCompletion,
) -> str:
    return _sha256(
        {
            "actor": {
                "authorization_version": actor.authorization_version,
                "person_id": str(actor.person_id),
                "user_id": actor.user_id,
            },
            "approval_manifest_sha256": approval.approval_manifest_sha256,
            "effective_approval_completion_id": str(approval.id),
            "expected_task_version": command.expected_task_version,
            "schema": "cloud_oam.stocktake.nonopening_posting_request.v1",
            "task_id": str(command.task_id),
        }
    )


def _posting_authorization_sha256(
    *,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    posted_at: datetime,
) -> str:
    return _posting_authorization_document_sha256(
        user_id=actor.user_id,
        person_id=actor.person_id,
        assignment_id=assignment.id,
        authorization_version=actor.authorization_version,
        posted_at=posted_at,
    )


def _posting_authorization_document_sha256(
    *,
    user_id: str,
    person_id: uuid.UUID,
    assignment_id: uuid.UUID,
    authorization_version: int,
    posted_at: datetime,
) -> str:
    return _sha256(
        {
            "assignment_id": str(assignment_id),
            "authorization_version": authorization_version,
            "person_id": str(person_id),
            "posted_at": _timestamp(posted_at),
            "role_code": "admin",
            "schema": "cloud_oam.stocktake.nonopening_posting_authorization.v1",
            "scope_id": "*",
            "scope_type": "national",
            "user_id": user_id,
        }
    )


def _current_admin_assignment(
    db: Session,
    actor: FormalPrincipal,
) -> RoleAssignment:
    grants = tuple(
        row
        for row in actor.assignments
        if row.role_code == "admin"
        and row.scope_type == "national"
        and row.scope_id == "*"
    )
    if len(grants) != 1:
        _fail(
            "stocktake_posting_admin_assignment_ambiguous",
            "forbidden",
            "盘点差异过账要求唯一有效的总部管理员授权",
        )
    assignment = db.scalar(
        select(RoleAssignment)
        .where(RoleAssignment.id == grants[0].assignment_id)
        .with_for_update(of=RoleAssignment)
        .execution_options(populate_existing=True)
    )
    if (
        assignment is None
        or assignment.user_id != actor.user_id
        or assignment.status != "active"
        or assignment.revoked_at is not None
        or assignment.scope_type != "national"
        or assignment.scope_id != "*"
    ):
        _fail(
            "stocktake_posting_admin_assignment_not_current",
            "forbidden",
            "总部管理员授权已失效",
        )
    return assignment


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail(
            "stocktake_posting_formal_principal_required",
            "forbidden",
            "盘点差异过账必须使用正式权限主体",
        )
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _fail(
            "stocktake_posting_actor_inactive",
            "forbidden",
            "当前账号或人员状态不允许盘点差异过账",
        )
    return actor


def _validate_post_command(
    command: PostApprovedStocktakeDifferencesCommand,
) -> PostApprovedStocktakeDifferencesCommand:
    if not isinstance(command, PostApprovedStocktakeDifferencesCommand):
        _fail(
            "stocktake_posting_command_required",
            "invalid_request",
            "盘点差异过账命令类型无效",
        )
    if not isinstance(command.task_id, uuid.UUID) or command.task_id.int == 0:
        _fail(
            "stocktake_posting_task_id_invalid",
            "invalid_request",
            "盘点任务标识无效",
        )
    if (
        not isinstance(command.expected_task_version, int)
        or isinstance(command.expected_task_version, bool)
        or command.expected_task_version < 0
    ):
        _fail(
            "stocktake_posting_version_invalid",
            "invalid_request",
            "盘点任务预期版本无效",
        )
    return command


def _require_idempotency_key(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 200
        or _PRINTABLE.fullmatch(value) is None
    ):
        _fail(
            "stocktake_posting_idempotency_key_invalid",
            "invalid_request",
            "幂等键必须为 16 至 200 位可打印 ASCII 字符",
        )
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    result = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(result, bytes) or len(result) < 32:
        _fail(
            "stocktake_posting_hmac_secret_invalid",
            "invalid_request",
            "盘点差异过账幂等摘要密钥无效",
        )
    return result


def _require_trace_request_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not 8 <= len(value) <= 160
        or _PRINTABLE.fullmatch(value) is None
    ):
        _fail(
            "stocktake_posting_trace_request_id_invalid",
            "invalid_request",
            "请求标识必须为 8 至 160 位可打印 ASCII 字符",
        )
    return value


def _idempotency_hmac(
    secret: bytes,
    actor_user_id: str,
    path: str,
    raw_key: str,
) -> str:
    return hmac.new(
        secret,
        (
            "cloud_oam.stocktake.nonopening_posting.idempotency.v1\0"
            f"{actor_user_id}\0{path}\0{raw_key}"
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _derived_sha256(kind: str, anchor: str, coordinate: str) -> str:
    return hashlib.sha256(
        (
            "cloud_oam.stocktake.nonopening_posting.derived.v1\0"
            f"{kind}\0{anchor}\0{coordinate}"
        ).encode("utf-8")
    ).hexdigest()


def _event_key(kind: str, completion_id: uuid.UUID) -> str:
    digest = _derived_sha256(kind, str(completion_id), "event")
    return f"stocktake-posting-{kind}-{digest}"


def _request_reference(raw: str) -> str:
    digest = hashlib.sha256(
        (
            "cloud_oam.stocktake.nonopening_posting.request.v1\0" + raw
        ).encode("utf-8")
    ).hexdigest()
    return f"stocktake-posting-request-{digest}"


def _lock_coordinate(namespace: str, value: str) -> int:
    digest = hashlib.sha256(
        f"cloud_oam.inventory.lock.v1\0{namespace}\0{value}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _take_advisory_locks(db: Session, coordinates: Sequence[int]) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for coordinate in sorted(set(coordinates)):
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": coordinate},
        )


def _database_now(db: Session) -> datetime:
    if db.get_bind().dialect.name == "postgresql":
        value = db.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            _fail(
                "stocktake_posting_database_time_unavailable",
                "service_unavailable",
                "数据库服务端时间不可用",
            )
        return _as_utc(value)
    return datetime.now(timezone.utc)


def _as_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        _evidence_invalid("盘点证据时间坐标无效")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat().replace("+00:00", "Z")


def _unique_mapping(values, *, key, code: str, message: str):
    result = {}
    for value in values:
        coordinate = key(value)
        if coordinate in result:
            _fail(code, "service_unavailable", message)
        result[coordinate] = value
    return result


def _canonical_quantity(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        _invalid_graph("差异数量不是有限 Decimal")
    quantized = value.quantize(Decimal("0.001"))
    if value != quantized:
        _invalid_graph("差异数量超过 numeric(18,3) 精度")
    return format(quantized, "f")


def _sha256(document: dict[str, object]) -> str:
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _invalid_graph(message: str) -> None:
    _fail(
        "stocktake_posting_evidence_invalid",
        "service_unavailable",
        message,
    )


def _evidence_invalid(message: str) -> NoReturn:
    _invalid_graph(message)
    raise AssertionError("unreachable invalid stocktake posting graph")


def _fail(
    code: str,
    category: str,
    message: str,
    *,
    cause: Exception | None = None,
) -> NoReturn:
    error = StocktakeDifferencePostingError(code, category, message)
    if cause is not None:
        raise error from cause
    raise error


__all__ = [
    "EffectiveApprovalCompletionEvidence",
    "EffectiveApprovalItemEvidence",
    "EffectiveApprovalScopeEvidence",
    "EffectiveScopeRound",
    "MOVEMENT_TYPES",
    "PlannedStocktakeMovement",
    "PostApprovedStocktakeDifferencesCommand",
    "StocktakeDifferencePostingError",
    "StocktakeDifferencePostingPlan",
    "StocktakeDifferencePostingResult",
    "post_approved_stocktake_differences",
]
