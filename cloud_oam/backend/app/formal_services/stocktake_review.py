"""Two independent review facts for a sealed non-opening stocktake difference set.

This module is intentionally not a wrapper around the opening-stocktake API.
It replays the current non-opening initial or selected-scope recount evidence,
records one immutable regional fact and one later immutable headquarters fact,
and changes only the stocktake task state.  It never creates inventory
accounts, ledger transactions, postings, movements, balances, notification
events or outbox rows.  The caller owns the transaction; public functions
flush but never commit or roll back.
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

from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    ScopeGrant,
    lock_formal_principal_graph,
    load_formal_principal,
)
from ..foundation_models import (
    AuditEvent,
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
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
    StocktakeEffectiveApprovalCompletion,
    StocktakeEffectiveApprovalItem,
    StocktakeEffectiveApprovalScope,
    StocktakePosting,
    StocktakeRecountCase,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeReviewItem,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)
from . import stocktake_count as count_service
from . import stocktake_difference as difference_service
from . import stocktake_posting as posting_service
from . import stocktake_task as task_service
from .audit_chain import (
    AuditChainError,
    _lock_audit_chain_head_with_proof,
    _verify_audit_event_with_prelocked_proof,
    append_audit_event,
)
from .postgresql_lock_graph import lock_nonopening_stocktake_review_graph


INVENTORY_STREAM_KEY: Final[str] = "inventory"
REGION_STAGE: Final[str] = "region"
HEADQUARTERS_STAGE: Final[str] = "headquarters"
NON_OPENING_TYPES: Final[frozenset[str]] = frozenset(
    {"full", "sample", "ad_hoc", "personal", "termination"}
)
TOP_LEVEL_DECISIONS: Final[frozenset[str]] = frozenset(
    {"approve", "recount", "reject"}
)
ITEM_DECISIONS: Final[frozenset[str]] = frozenset(
    {
        "accept_for_posting",
        "pending_verification",
        "no_adjustment",
        "recount",
        "reject",
    }
)
POSTABLE_ITEM_DECISIONS: Final[frozenset[str]] = frozenset(
    {"accept_for_posting", "no_adjustment"}
)
RECOUNT_ITEM_DECISIONS: Final[frozenset[str]] = frozenset(
    {"pending_verification", "recount"}
)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_PRINTABLE = re.compile(r"^[\x21-\x7e]+$", re.ASCII)
_ZERO = Decimal("0.000")
_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class StocktakeReviewError(RuntimeError):
    """Stable database-detail-free error for non-opening review commands."""

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
class StocktakeReviewItemInput:
    difference_id: uuid.UUID
    decision: str
    comment: str


@dataclass(frozen=True, slots=True)
class StocktakeEffectiveApprovalItemInput:
    """Explicit terminal-HQ confirmation for an older effective scope item."""

    difference_id: uuid.UUID
    decision: str
    comment: str


@dataclass(frozen=True, slots=True)
class SubmitStocktakeReviewCommand:
    task_id: uuid.UUID
    round_id: uuid.UUID
    expected_task_version: int
    decision: str
    items: tuple[StocktakeReviewItemInput, ...]
    comment: str = ""
    effective_scope_ids: tuple[uuid.UUID, ...] = ()
    effective_items: tuple[StocktakeEffectiveApprovalItemInput, ...] = ()


@dataclass(frozen=True, slots=True)
class StocktakeReviewResult:
    review_id: uuid.UUID
    task_id: uuid.UUID
    round_id: uuid.UUID
    review_stage: str
    decision: str
    resulting_task_status: str
    expected_task_version: int
    resulting_task_version: int
    task_version: int
    item_count: int
    pending_verification_count: int
    ready_for_posting: bool
    effective_approval_completion_id: uuid.UUID | None = None
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class SealedNonOpeningDifferenceEvidence:
    task: FormalStocktakeTask
    round_row: StocktakeRound
    scopes: tuple[FormalStocktakeScope, ...]
    submission: StocktakeRoundSubmission
    completion: StocktakeDifferenceSetCompletion
    differences: tuple[StocktakeDifference, ...]
    source_audit_event: AuditEvent
    ancestor_graph: object | None = None


def submit_stocktake_region_review(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitStocktakeReviewCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeReviewResult:
    """Append the exact-region manager review without posting inventory."""

    return _public_review_boundary(
        db,
        actor=actor,
        command=command,
        stage=REGION_STAGE,
        idempotency_key=idempotency_key,
        idempotency_hmac_secret=idempotency_hmac_secret,
        trace_request_id=trace_request_id,
    )


def submit_stocktake_headquarters_review(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitStocktakeReviewCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeReviewResult:
    """Append the later nationwide-admin review without posting inventory."""

    return _public_review_boundary(
        db,
        actor=actor,
        command=command,
        stage=HEADQUARTERS_STAGE,
        idempotency_key=idempotency_key,
        idempotency_hmac_secret=idempotency_hmac_secret,
        trace_request_id=trace_request_id,
    )


def _public_review_boundary(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitStocktakeReviewCommand,
    stage: str,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeReviewResult:
    try:
        return _submit_review(
            db,
            actor=actor,
            command=command,
            stage=stage,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    except StocktakeReviewError:
        raise
    except AuditChainError as exc:
        _fail(
            "stocktake_review_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，盘点复核未完成",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "stocktake_review_concurrent_conflict",
            "conflict",
            "盘点复核发生并发冲突，请回滚并重新读取",
            cause=exc,
        )
    except DBAPIError as exc:
        _fail(
            "stocktake_review_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了盘点复核，请回滚并重新读取",
            cause=exc,
        )
    raise AssertionError("unreachable stocktake review boundary")


def _submit_review(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitStocktakeReviewCommand,
    stage: str,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeReviewResult:
    supplied = _validate_supplied_actor(actor)
    checked = _validate_command(command)
    if stage not in {REGION_STAGE, HEADQUARTERS_STAGE}:
        _fail("stocktake_review_stage_invalid", "invalid_request", "盘点复核阶段无效")
    if (
        (stage != HEADQUARTERS_STAGE or checked.decision != "approve")
        and (checked.effective_scope_ids or checked.effective_items)
    ):
        _fail(
            "stocktake_review_effective_manifest_forbidden",
            "invalid_request",
            "任务级有效范围清单仅允许随最终总部通过动作提交",
        )
    secret = _require_hmac_secret(idempotency_hmac_secret)
    raw_key = _require_idempotency_key(idempotency_key)
    trace_id = _require_trace_request_id(trace_request_id)
    path = (
        f"/api/v1/stocktakes/{checked.task_id}/rounds/"
        f"{checked.round_id}/reviews/{stage}"
    )
    key_hash = _idempotency_hmac(secret, supplied.user_id, path, raw_key)
    _take_advisory_locks(
        db,
        (
            _lock_coordinate("stocktake-review-idempotency", key_hash),
            _lock_coordinate("stocktake-review-task", str(checked.task_id)),
            _lock_coordinate("stocktake-review-round", str(checked.round_id)),
            _lock_coordinate(
                "stocktake-review-stage", f"{checked.task_id}:{checked.round_id}:{stage}"
            ),
        ),
    )

    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked.task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None or task.task_type not in NON_OPENING_TYPES:
        _fail("stocktake_review_task_not_found", "not_found", "非期初盘点任务不存在")

    principal_user_ids = _task_principal_user_ids(
        db, task.id, supplied_user_ids=(supplied.user_id,)
    )
    lock_formal_principal_graph(db, principal_user_ids)
    round_row = db.scalar(
        select(StocktakeRound)
        .where(
            StocktakeRound.id == checked.round_id,
            StocktakeRound.task_id == task.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if round_row is None:
        _fail("stocktake_review_round_not_found", "not_found", "盘点轮次不存在")
    lock_nonopening_stocktake_review_graph(db, task.id, round_row.id)

    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    assignment, grant = _authorize_reviewer(db, current, task, stage, now)
    evidence = _load_and_validate_sealed_difference_evidence(
        db, task=task, round_row=round_row, now=now
    )
    existing_reviews = _load_reviews(db, task.id, round_row.id)
    for prior_review in existing_reviews:
        _require_review_version_coordinates(prior_review)
    existing_by_key = db.scalar(
        select(StocktakeReview)
        .where(StocktakeReview.idempotency_key_hash == key_hash)
        .execution_options(populate_existing=True)
    )
    expected_decisions = _validate_item_decisions(
        checked, evidence.differences, stage=stage, existing_reviews=existing_reviews
    )
    manifest = _review_manifest(
        evidence=evidence,
        stage=stage,
        command=checked,
        decisions=expected_decisions,
    )

    if existing_by_key is not None:
        effective_completion_id = _validate_review_replay(
            db,
            evidence=evidence,
            review=existing_by_key,
            actor=current,
            command=checked,
            stage=stage,
            manifest=manifest,
        )
        _head, proof = _lock_audit_chain_head_with_proof(
            db, stream_key=INVENTORY_STREAM_KEY
        )
        _verify_evidence_audit(db, evidence.source_audit_event, proof)
        _verify_review_audit(db, existing_by_key, proof)
        return replace(
            _result(
                existing_by_key,
                evidence.differences,
                task.version,
                effective_approval_completion_id=effective_completion_id,
            ),
            replayed=True,
        )

    if any(row.review_stage == stage for row in existing_reviews):
        _fail(
            "stocktake_review_stage_already_completed",
            "conflict",
            "该层级盘点复核已经形成不可变事实",
        )
    _validate_new_stage_state(
        task, round_row, checked, stage=stage, existing_reviews=existing_reviews
    )
    _validate_separation_and_prior_items(
        db,
        actor=current,
        stage=stage,
        command=checked,
        decisions=expected_decisions,
        existing_reviews=existing_reviews,
        now=now,
    )

    # Audit head is the final shared lock.  Re-read the database clock and the
    # current principal without acquiring a new graph after this point.
    _head, proof = _lock_audit_chain_head_with_proof(
        db, stream_key=INVENTORY_STREAM_KEY
    )
    _verify_evidence_audit(db, evidence.source_audit_event, proof)
    for prior in existing_reviews:
        _verify_review_audit(db, prior, proof)
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    assignment, grant = _authorize_reviewer(
        db, current, task, stage, now, lock_rows=False
    )
    _validate_new_stage_state(
        task, round_row, checked, stage=stage, existing_reviews=existing_reviews
    )
    _validate_separation_and_prior_items(
        db,
        actor=current,
        stage=stage,
        command=checked,
        decisions=expected_decisions,
        existing_reviews=existing_reviews,
        now=now,
    )
    return _write_review(
        db,
        evidence=evidence,
        actor=current,
        assignment=assignment,
        grant=grant,
        command=checked,
        stage=stage,
        key_hash=key_hash,
        manifest=manifest,
        trace_request_id=trace_id,
        now=now,
    )


def _write_review(
    db: Session,
    *,
    evidence: SealedNonOpeningDifferenceEvidence,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    grant: ScopeGrant,
    command: SubmitStocktakeReviewCommand,
    stage: str,
    key_hash: str,
    manifest: str,
    trace_request_id: str,
    now: datetime,
) -> StocktakeReviewResult:
    del grant
    task = evidence.task
    round_row = evidence.round_row
    expected_task_version = command.expected_task_version
    resulting_task_version = expected_task_version + 1
    if task.version != expected_task_version:
        _fail(
            "stocktake_review_version_conflict",
            "conflict",
            "盘点任务版本已变化，请重新读取后提交",
        )
    review = StocktakeReview(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        review_stage=stage,
        reviewer_user_id=actor.user_id,
        reviewer_person_id=actor.person_id,
        reviewer_role_assignment_id=assignment.id,
        authorization_version=actor.authorization_version,
        expected_task_version=expected_task_version,
        resulting_task_version=resulting_task_version,
        decision=command.decision,
        comment=command.comment,
        decision_manifest_sha256=manifest,
        idempotency_key_hash=key_hash,
        reviewed_at=now,
        created_at=now,
    )
    db.add(review)
    db.flush()
    input_by_id = {row.difference_id: row for row in command.items}
    for difference in evidence.differences:
        value = input_by_id[difference.id]
        db.add(
            StocktakeReviewItem(
                review_id=review.id,
                difference_id=difference.id,
                task_id=task.id,
                round_id=round_row.id,
                decision=value.decision,
                comment=value.comment,
                created_at=now,
            )
        )
    db.flush()

    effective_completion: StocktakeEffectiveApprovalCompletion | None = None
    if stage == HEADQUARTERS_STAGE and command.decision == "approve":
        effective_completion = _seal_effective_approval_completion(
            db,
            task=task,
            terminal_round=round_row,
            terminal_review=review,
            actor=actor,
            assignment=assignment,
            command=command,
            review_manifest=manifest,
            now=now,
        )

    previous_status = task.status
    resulting_status = _resulting_status(stage, command.decision)
    task.status = resulting_status
    task.version += 1
    if task.version != resulting_task_version:
        _fail(
            "stocktake_review_version_integrity_error",
            "service_unavailable",
            "盘点复核任务版本证据不连续",
        )
    task.updated_at = now
    metadata = {
        "decision": command.decision,
        "decision_manifest_sha256": manifest,
        "difference_completion_id": str(evidence.completion.id),
        "difference_manifest_sha256": evidence.completion.difference_manifest_sha256,
        "expected_task_version": expected_task_version,
        "item_count": len(evidence.differences),
        "pending_verification_count": sum(
            1
            for row in evidence.differences
            if row.reason_code == "stocktake_pending_verification"
        ),
        "review_id": str(review.id),
        "round_id": str(round_row.id),
        "resulting_task_version": resulting_task_version,
        "schema": "cloud_oam.stocktake.nonopening_review_event.v2",
        "stage": stage,
    }
    if effective_completion is not None:
        metadata.update(
            {
                "effective_approval_completion_id": str(
                    effective_completion.id
                ),
                "effective_approval_manifest_sha256": (
                    effective_completion.approval_manifest_sha256
                ),
                "effective_scope_count": effective_completion.scope_count,
            }
        )
    db.add(
        StateTransitionEvent(
            aggregate_type="stocktake_task",
            aggregate_id=str(task.id),
            from_status=previous_status,
            to_status=resulting_status,
            reason=f"nonopening_{stage}_review_{command.decision}",
            actor_id=actor.user_id,
            idempotency_key=_event_key("state", review.id),
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
        action=f"stocktake.nonopening.{stage}_reviewed",
        aggregate_type="stocktake_review",
        aggregate_id=str(review.id),
        before_jsonb=None,
        after_jsonb={
            **metadata,
            "authorization_version": actor.authorization_version,
            "reviewer_person_id": str(actor.person_id),
            "reviewer_role_assignment_id": str(assignment.id),
            "reviewer_user_id": actor.user_id,
        },
        request_id=_request_reference(trace_request_id),
        occurred_at=now,
    )
    db.flush()
    return _result(
        review,
        evidence.differences,
        task.version,
        effective_approval_completion_id=(
            effective_completion.id if effective_completion is not None else None
        ),
    )


def _seal_effective_approval_completion(
    db: Session,
    *,
    task: FormalStocktakeTask,
    terminal_round: StocktakeRound,
    terminal_review: StocktakeReview,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    command: SubmitStocktakeReviewCommand,
    review_manifest: str,
    now: datetime,
) -> StocktakeEffectiveApprovalCompletion:
    """Freeze the task-wide latest-causal scope and item approval manifest."""

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

    ordered_scopes = posting_service._validate_scopes(task, scopes)
    ordered_rounds = posting_service._validate_rounds(task, rounds)
    if (
        ordered_rounds[-1].id != terminal_round.id
        or terminal_review.round_id != terminal_round.id
        or terminal_review.review_stage != HEADQUARTERS_STAGE
        or terminal_review.decision != "approve"
        or command.expected_task_version != task.version
    ):
        _fail(
            "stocktake_review_effective_approval_terminal_invalid",
            "precondition_failed",
            "任务级有效审批必须由当前最终总部通过动作形成",
        )
    submission_by_round, completion_by_round, differences_by_round = (
        posting_service._validate_round_evidence(
            task,
            ordered_rounds,
            submissions,
            difference_completions,
            differences,
            scope_ids={scope.id for scope in ordered_scopes},
        )
    )
    reviews_by_round, items_by_review = posting_service._validate_review_rows(
        task,
        ordered_rounds,
        differences_by_round,
        reviews,
        review_items,
    )
    effective_round_by_scope = posting_service._select_effective_rounds(
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
    posting_service._require_terminal_approvals(
        terminal_round,
        reviews_by_round[terminal_round.id],
    )

    all_scope_ids = tuple(scope.id for scope in ordered_scopes)
    if command.effective_scope_ids:
        supplied_scope_ids = command.effective_scope_ids
    elif len(ordered_rounds) == 1:
        # An initial-round HQ command already covers the complete task scope;
        # retain compatibility while still persisting every explicit scope row.
        supplied_scope_ids = all_scope_ids
    else:
        _fail(
            "stocktake_review_effective_scopes_required",
            "invalid_request",
            "复盘后的最终总部通过必须显式确认任务全部盘点范围",
        )
    if (
        len(set(supplied_scope_ids)) != len(supplied_scope_ids)
        or set(supplied_scope_ids) != set(all_scope_ids)
    ):
        _fail(
            "stocktake_review_effective_scopes_incomplete",
            "precondition_failed",
            "最终总部有效审批必须逐范围覆盖完整任务",
        )

    old_inputs = {row.difference_id: row for row in command.effective_items}
    if len(old_inputs) != len(command.effective_items):
        _fail(
            "stocktake_review_effective_item_duplicate",
            "invalid_request",
            "同一历史有效差异不得重复复核",
        )
    completion_id = uuid.uuid4()
    scope_rows: list[StocktakeEffectiveApprovalScope] = []
    item_rows: list[StocktakeEffectiveApprovalItem] = []
    planner_scope_rows: list[posting_service.EffectiveApprovalScopeEvidence] = []
    planner_item_rows: list[posting_service.EffectiveApprovalItemEvidence] = []
    approval_manifest_scopes: list[dict[str, object]] = []
    expected_old_ids: set[uuid.UUID] = set()
    accepted_count = 0
    no_adjustment_count = 0

    for scope in sorted(ordered_scopes, key=lambda row: str(row.id)):
        source_round = effective_round_by_scope[scope.id]
        source_completion = completion_by_round[source_round.id]
        source_reviews = reviews_by_round[source_round.id]
        regional = source_reviews.get(REGION_STAGE)
        if regional is None or now <= _as_utc(regional.reviewed_at):
            _fail(
                "stocktake_review_effective_region_invalid",
                "precondition_failed",
                "每个有效范围必须有更早且可追溯的地区逐项终态决定",
            )
        source_differences = tuple(
            row
            for row in differences_by_round[source_round.id]
            if row.scope_id == scope.id
        )
        region_items = items_by_review[regional.id]
        terminal_items = items_by_review[terminal_review.id]
        manifest_items: list[dict[str, object]] = []
        scope_item_documents: list[dict[str, object]] = []
        for difference in source_differences:
            regional_item = region_items.get(difference.id)
            if regional_item is None:
                _evidence_invalid("有效范围缺少地区逐项复核事实")
            if source_round.id == terminal_round.id:
                hq_item = terminal_items.get(difference.id)
                if hq_item is None:
                    _evidence_invalid("最终轮次缺少总部逐项复核事实")
                headquarters_decision = hq_item.decision
                headquarters_comment = hq_item.comment
            else:
                expected_old_ids.add(difference.id)
                supplied_item = old_inputs.get(difference.id)
                if supplied_item is None:
                    _fail(
                        "stocktake_review_effective_item_missing",
                        "invalid_request",
                        "最终总部通过必须显式逐项复核旧轮仍有效的差异",
                    )
                headquarters_decision = supplied_item.decision
                headquarters_comment = supplied_item.comment
            if (
                regional_item.decision not in POSTABLE_ITEM_DECISIONS
                or headquarters_decision != regional_item.decision
                or not headquarters_comment.strip()
            ):
                _fail(
                    "stocktake_review_effective_item_not_postable",
                    "precondition_failed",
                    "有效差异必须由地区和本次最终总部逐项一致通过或明确不调整",
                )
            if headquarters_decision == "accept_for_posting":
                accepted_count += 1
            else:
                no_adjustment_count += 1
            item_document = {
                "completion_id": str(completion_id),
                "difference_id": str(difference.id),
                "headquarters_comment": headquarters_comment,
                "headquarters_decision": headquarters_decision,
                "regional_decision": regional_item.decision,
                "regional_review_id": str(regional.id),
                "schema": "cloud_oam.stocktake.effective_approval_item.v1",
                "scope_id": str(scope.id),
                "source_round_id": str(source_round.id),
                "task_id": str(task.id),
            }
            item_rows.append(
                StocktakeEffectiveApprovalItem(
                    completion_id=completion_id,
                    difference_id=difference.id,
                    task_id=task.id,
                    scope_id=scope.id,
                    source_round_id=source_round.id,
                    regional_review_id=regional.id,
                    regional_decision=regional_item.decision,
                    headquarters_decision=headquarters_decision,
                    headquarters_comment=headquarters_comment,
                    item_manifest_sha256=_sha256(item_document),
                    created_at=now,
                )
            )
            planner_item_rows.append(
                posting_service.EffectiveApprovalItemEvidence(
                    completion_id=completion_id,
                    scope_id=scope.id,
                    source_round_id=source_round.id,
                    difference_id=difference.id,
                    regional_review_id=regional.id,
                    regional_decision=regional_item.decision,
                    headquarters_decision=headquarters_decision,
                )
            )
            scope_item_documents.append(item_document)
            manifest_items.append(
                {
                    "difference_id": str(difference.id),
                    "headquarters_decision": headquarters_decision,
                    "regional_decision": regional_item.decision,
                }
            )
        scope_document = {
            "completion_id": str(completion_id),
            "difference_count": len(source_differences),
            "items": scope_item_documents,
            "regional_review_id": str(regional.id),
            "schema": "cloud_oam.stocktake.effective_approval_scope.v1",
            "scope_id": str(scope.id),
            "source_difference_completion_id": str(source_completion.id),
            "source_round_id": str(source_round.id),
            "task_id": str(task.id),
        }
        scope_rows.append(
            StocktakeEffectiveApprovalScope(
                completion_id=completion_id,
                scope_id=scope.id,
                task_id=task.id,
                source_round_id=source_round.id,
                source_difference_completion_id=source_completion.id,
                regional_review_id=regional.id,
                difference_count=len(source_differences),
                scope_manifest_sha256=_sha256(scope_document),
                created_at=now,
            )
        )
        planner_scope_rows.append(
            posting_service.EffectiveApprovalScopeEvidence(
                completion_id=completion_id,
                scope_id=scope.id,
                source_round_id=source_round.id,
                source_difference_completion_id=source_completion.id,
                regional_review_id=regional.id,
                difference_count=len(source_differences),
            )
        )
        approval_manifest_scopes.append(
            {
                "difference_count": len(source_differences),
                "items": manifest_items,
                "regional_review_id": str(regional.id),
                "scope_id": str(scope.id),
                "source_difference_completion_id": str(source_completion.id),
                "source_round_id": str(source_round.id),
            }
        )

    if set(old_inputs) != expected_old_ids:
        _fail(
            "stocktake_review_effective_item_extraneous",
            "invalid_request",
            "历史有效差异复核清单包含缺失或任务外坐标",
        )
    difference_count = accepted_count + no_adjustment_count
    approval_manifest = _sha256(
        {
            "completion_id": str(completion_id),
            "difference_count": difference_count,
            "schema": "cloud_oam.stocktake.effective_approval.v1",
            "scope_count": len(ordered_scopes),
            "scopes": approval_manifest_scopes,
            "task_id": str(task.id),
            "terminal_headquarters_review_id": str(terminal_review.id),
            "terminal_round_id": str(terminal_round.id),
        }
    )
    authorization_sha256 = _sha256(
        {
            "assignment_id": str(assignment.id),
            "authorization_version": actor.authorization_version,
            "completed_at": _timestamp(now),
            "person_id": str(actor.person_id),
            "role_code": "admin",
            "schema": "cloud_oam.stocktake.effective_approval_authorization.v1",
            "scope_id": "*",
            "scope_type": "national",
            "user_id": actor.user_id,
        }
    )
    request_sha256 = _sha256(
        {
            "approval_manifest_sha256": approval_manifest,
            "effective_items": [
                {
                    "comment": row.comment,
                    "decision": row.decision,
                    "difference_id": str(row.difference_id),
                }
                for row in sorted(
                    command.effective_items, key=lambda row: str(row.difference_id)
                )
            ],
            "effective_scope_ids": [
                str(value) for value in sorted(supplied_scope_ids, key=str)
            ],
            "review_manifest_sha256": review_manifest,
            "schema": "cloud_oam.stocktake.effective_approval_request.v1",
        }
    )
    planner_completion = posting_service.EffectiveApprovalCompletionEvidence(
        id=completion_id,
        task_id=task.id,
        terminal_round_id=terminal_round.id,
        terminal_headquarters_review_id=terminal_review.id,
        scope_count=len(ordered_scopes),
        difference_count=difference_count,
        approval_manifest_sha256=approval_manifest,
    )
    posting_service._validate_effective_approval(
        task=task,
        rounds=ordered_rounds,
        scopes=ordered_scopes,
        completion_by_round=completion_by_round,
        differences_by_round=differences_by_round,
        reviews_by_round=reviews_by_round,
        items_by_review=items_by_review,
        effective_round_by_scope=effective_round_by_scope,
        approval=planner_completion,
        approval_scopes=planner_scope_rows,
        approval_items=planner_item_rows,
    )
    completion = StocktakeEffectiveApprovalCompletion(
        id=completion_id,
        task_id=task.id,
        terminal_round_id=terminal_round.id,
        terminal_headquarters_review_id=terminal_review.id,
        expected_task_version=task.version,
        approved_task_version=task.version + 1,
        scope_count=len(ordered_scopes),
        difference_count=difference_count,
        accepted_difference_count=accepted_count,
        no_adjustment_count=no_adjustment_count,
        approval_manifest_sha256=approval_manifest,
        request_sha256=request_sha256,
        completed_by_user_id=actor.user_id,
        completed_by_person_id=actor.person_id,
        completed_role_assignment_id=assignment.id,
        authorization_version=actor.authorization_version,
        role_code="admin",
        scope_type="national",
        scope_id_snapshot="*",
        authorization_sha256=authorization_sha256,
        completed_at=now,
        created_at=now,
    )
    db.add(completion)
    db.flush()
    db.add_all(scope_rows)
    db.flush()
    db.add_all(item_rows)
    db.flush()
    return completion


def _load_and_validate_sealed_difference_evidence(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    now: datetime,
    history: CountHistoryContext | None = None,
) -> SealedNonOpeningDifferenceEvidence:
    if history is not None:
        history.require(db, task, round_row)
    if round_row.round_no > 1 or round_row.round_type == "recount":
        # Import lazily: recount counting itself reuses this review evidence
        # boundary to reprove the immutable source round before accepting the
        # next round's selected-scope counts.
        from . import stocktake_recount_difference as recount_difference_service

        try:
            evidence = (
                recount_difference_service._load_and_validate_sealed_recount_difference_evidence(
                    db,
                    task=task,
                    round_row=round_row,
                    now=now,
                    history=history,
                )
            )
        except recount_difference_service.StocktakeRecountDifferenceError as exc:
            _fail(
                "stocktake_review_recount_source_evidence_invalid",
                "service_unavailable",
                "复盘轮次的计数、差异或因果证据无法重证",
                cause=exc,
            )
        return SealedNonOpeningDifferenceEvidence(
            task=evidence.task,
            round_row=evidence.round_row,
            scopes=evidence.selected_scopes,
            submission=evidence.submission,
            completion=evidence.completion,
            differences=evidence.differences,
            source_audit_event=evidence.source_audit_event,
            ancestor_graph=evidence.assignment_graph,
        )
    if (
        task.task_type not in NON_OPENING_TYPES
        or round_row.task_id != task.id
        or round_row.round_no != 1
        or round_row.round_type != "initial"
        or round_row.recount_case_id is not None
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
            "stocktake_review_source_round_invalid",
            "precondition_failed",
            "盘点复核必须基于已提交且不可变的非期初初盘轮次",
        )
    if history is None and db.scalar(
        select(func.count())
        .select_from(StocktakePosting)
        .where(
            StocktakePosting.task_id == task.id,
            StocktakePosting.round_id == round_row.id,
        )
    ):
        _fail(
            "stocktake_review_source_already_posted",
            "precondition_failed",
            "该盘点轮次已经存在过账事实",
        )

    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task.id)
            .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if not scopes or [row.scope_no for row in scopes] != list(
        range(1, len(scopes) + 1)
    ):
        _evidence_invalid("盘点范围缺失或序号不连续")
    submissions = tuple(
        db.scalars(
            select(StocktakeRoundSubmission)
            .where(
                StocktakeRoundSubmission.task_id == task.id,
                StocktakeRoundSubmission.round_id == round_row.id,
            )
            .order_by(StocktakeRoundSubmission.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    completions = tuple(
        db.scalars(
            select(StocktakeDifferenceSetCompletion)
            .where(
                StocktakeDifferenceSetCompletion.task_id == task.id,
                StocktakeDifferenceSetCompletion.round_id == round_row.id,
            )
            .order_by(StocktakeDifferenceSetCompletion.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(submissions) != 1 or len(completions) != 1:
        _evidence_invalid("盘点轮次封印或差异集封印缺失、不唯一")
    submission = submissions[0]
    completion = completions[0]
    differences = tuple(
        db.scalars(
            select(StocktakeDifference)
            .where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.round_id == round_row.id,
            )
            .order_by(StocktakeDifference.difference_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    if [row.difference_no for row in differences] != list(
        range(1, len(differences) + 1)
    ):
        _evidence_invalid("盘点差异序号不连续")

    _recompute_initial_count_and_difference_graph(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        submission=submission,
        completion=completion,
        differences=differences,
        now=now,
        history=history,
    )
    source_events = tuple(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.stream_key == INVENTORY_STREAM_KEY,
                AuditEvent.action == "stocktake.initial_difference_set.evaluated",
                AuditEvent.aggregate_type == "stocktake_round",
                AuditEvent.aggregate_id == str(round_row.id),
            )
            .order_by(AuditEvent.stream_version)
        ).all()
    )
    if len(source_events) != 1:
        _evidence_invalid("盘点差异评估审计事实缺失或不唯一")
    source_event = source_events[0]
    after = source_event.after_jsonb
    if (
        not isinstance(after, dict)
        or after.get("difference_manifest_sha256")
        != completion.difference_manifest_sha256
        or after.get("difference_count") != completion.difference_count
        or after.get("round_status") != "submitted"
        or after.get("task_id") != str(task.id)
    ):
        _evidence_invalid("盘点差异评估审计摘要与封印不一致")
    return SealedNonOpeningDifferenceEvidence(
        task=task,
        round_row=round_row,
        scopes=scopes,
        submission=submission,
        completion=completion,
        differences=differences,
        source_audit_event=source_event,
    )


def _recompute_initial_count_and_difference_graph(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: tuple[FormalStocktakeScope, ...],
    submission: StocktakeRoundSubmission,
    completion: StocktakeDifferenceSetCompletion,
    differences: tuple[StocktakeDifference, ...],
    now: datetime,
    history: CountHistoryContext | None = None,
) -> None:
    try:
        if history is not None:
            history.require(db, task, round_row, scopes)
        plans = history.plans if history is not None else task_service._load_and_validate_scope_plans(
            db, task, scopes, now=now
        )
        freezes = tuple(
            db.scalars(
                select(InventoryFreeze)
                .where(InventoryFreeze.task_id == task.id)
                .order_by(InventoryFreeze.stocktake_scope_id, InventoryFreeze.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        freeze_by_scope = {row.stocktake_scope_id: row for row in freezes}
        plan_by_scope = {row.scope_id: row for row in plans}
        if len(freeze_by_scope) != len(scopes):
            _evidence_invalid("盘点冻结证据不完整")
        for scope in scopes:
            freeze = freeze_by_scope.get(scope.id)
            plan = plan_by_scope.get(scope.id)
            if (
                plan is None
                or freeze is None
                or freeze.scope_key != scope.scope_key
                or freeze.freeze_mode != plan.freeze_mode
                or (history is None and (freeze.status != "active" or freeze.valid_to is not None))
                or _as_utc(freeze.valid_from) > now
            ):
                _evidence_invalid("盘点冻结证据与范围计划不一致")
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
            _evidence_invalid("非期初盘点混入 OAM 控制总账证据")

        snapshots = tuple(
            db.scalars(
                select(StocktakeSnapshotLine)
                .where(StocktakeSnapshotLine.task_id == task.id)
                .order_by(
                    StocktakeSnapshotLine.scope_id,
                    StocktakeSnapshotLine.stock_account_id,
                )
                .execution_options(populate_existing=True)
            ).all()
        )
        accounts = count_service._lock_snapshot_accounts(db, snapshots)
        count_service._validate_snapshot_manifest(task, plans, snapshots, accounts)
        count_lines = tuple(
            db.scalars(
                select(StocktakeCountLine)
                .where(
                    StocktakeCountLine.task_id == task.id,
                    StocktakeCountLine.round_id == round_row.id,
                )
                .order_by(
                    StocktakeCountLine.scope_id,
                    StocktakeCountLine.stock_account_id,
                )
                .execution_options(populate_existing=True)
            ).all()
        )
        line_ids = tuple(row.id for row in count_lines)
        count_serials = (
            tuple(
                db.scalars(
                    select(StocktakeCountSerial)
                    .where(StocktakeCountSerial.count_line_id.in_(line_ids))
                    .order_by(
                        StocktakeCountSerial.count_line_id,
                        StocktakeCountSerial.serial_id,
                    )
                    .execution_options(populate_existing=True)
                ).all()
            )
            if line_ids
            else ()
        )
        observations = tuple(
            db.scalars(
                select(StocktakeCountObservation)
                .where(
                    StocktakeCountObservation.task_id == task.id,
                    StocktakeCountObservation.round_id == round_row.id,
                )
                .order_by(
                    StocktakeCountObservation.scope_id,
                    StocktakeCountObservation.observation_no,
                )
                .execution_options(populate_existing=True)
            ).all()
        )
        count_completions = tuple(
            db.scalars(
                select(StocktakeScopeCountCompletion)
                .where(
                    StocktakeScopeCountCompletion.task_id == task.id,
                    StocktakeScopeCountCompletion.round_id == round_row.id,
                )
                .order_by(StocktakeScopeCountCompletion.scope_id)
                .execution_options(populate_existing=True)
            ).all()
        )
        difference_service._validate_completion_and_submission_manifests(
            db,
            task=task,
            round_row=round_row,
            scopes=scopes,
            snapshots=snapshots,
            accounts=accounts,
            count_lines=count_lines,
            count_serials=count_serials,
            observations=observations,
            completions=count_completions,
            submission=submission,
            history=history,
        )
        replay_evidence = difference_service._replay_scope_expected_states(
            db,
            task=task,
            scopes=scopes,
            plans=plans,
            snapshots=snapshots,
            snapshot_accounts=accounts,
            observations=observations,
            completions=count_completions,
        )
        accounts = dict(replay_evidence.accounts)
        difference_service._lock_dimension_graph(
            db,
            task,
            scopes,
            accounts,
            snapshots,
            count_lines,
            count_serials,
            observations,
            replay_evidence=replay_evidence,
        )
        planned = difference_service._plan_difference_set(
            task=task,
            scopes=scopes,
            snapshots=snapshots,
            accounts=accounts,
            count_lines=count_lines,
            count_serials=count_serials,
            observations=observations,
            replay_evidence=replay_evidence,
        )
        if len(planned) != len(differences) or any(
            row.difference_no != number
            or difference_service._stored_plan(row) != plan
            for number, (row, plan) in enumerate(
                zip(differences, planned, strict=True), start=1
            )
        ):
            _evidence_invalid("已封印差异无法从初盘证据完整重算")
        summary = difference_service._difference_summary(
            task, round_row, submission, differences
        )
    except StocktakeReviewError:
        raise
    except Exception as exc:
        _fail(
            "stocktake_review_source_evidence_invalid",
            "service_unavailable",
            "盘点初盘或差异证据无法完整重算",
            cause=exc,
        )

    completed_at = _as_utc(completion.completed_at)
    created_at = _as_utc(completion.created_at)
    if (
        completion.round_submission_id != submission.id
        or completion.difference_count != summary["difference_count"]
        or completion.physical_difference_count
        != summary["physical_difference_count"]
        or completion.control_difference_count != 0
        or completion.pending_observation_difference_count
        != summary["pending_observation_difference_count"]
        or completion.total_affected_qty != summary["total_affected_qty"]
        or completion.difference_manifest_sha256
        != summary["difference_manifest_sha256"]
        or not _SHA256.fullmatch(completion.request_sha256)
        or not _SHA256.fullmatch(completion.idempotency_key_hash)
        or created_at != completed_at
        or completed_at < _as_utc(submission.submitted_at)
    ):
        _evidence_invalid("盘点差异集封印汇总或时间顺序无效")
    _validate_difference_evaluator_authorization(db, task, completion)


def _validate_difference_evaluator_authorization(
    db: Session,
    task: FormalStocktakeTask,
    completion: StocktakeDifferenceSetCompletion,
) -> None:
    assignment = db.scalar(
        select(RoleAssignment)
        .where(RoleAssignment.id == completion.completed_role_assignment_id)
        .execution_options(populate_existing=True)
    )
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    completed_at = _as_utc(completion.completed_at)
    expected_hash = _sha256(
        {
            "assignment_id": str(completion.completed_role_assignment_id),
            "authorization_version": completion.authorization_version,
            "completed_at": _timestamp(completed_at),
            "person_id": str(completion.completed_by_person_id),
            "role_code": completion.role_code,
            "schema": "cloud_oam.stocktake.difference_authorization.v1",
            "scope_id": completion.scope_id_snapshot,
            "scope_type": completion.scope_type,
            "user_id": completion.completed_by_user_id,
        }
    )
    role_scope_valid = (
        completion.role_code == "admin"
        and completion.scope_type == "national"
        and completion.scope_id_snapshot == "*"
    ) or (
        completion.role_code == "provincial_manager"
        and completion.scope_type == "organization"
        and _same_uuid(completion.scope_id_snapshot, task.region_org_id)
    )
    if (
        assignment is None
        or role is None
        or assignment.user_id != completion.completed_by_user_id
        or role.code != completion.role_code
        or role.status != "active"
        or role.is_external
        or assignment.scope_type != completion.scope_type
        or assignment.scope_id != completion.scope_id_snapshot
        or _as_utc(assignment.valid_from) > completed_at
        or (
            assignment.valid_to is not None
            and completed_at >= _as_utc(assignment.valid_to)
        )
        or (
            assignment.revoked_at is not None
            and completed_at >= _as_utc(assignment.revoked_at)
        )
        or not role_scope_valid
        or completion.authorization_sha256 != expected_hash
    ):
        _evidence_invalid("盘点差异评估授权封印无法重证")


def _validate_item_decisions(
    command: SubmitStocktakeReviewCommand,
    differences: Sequence[StocktakeDifference],
    *,
    stage: str,
    existing_reviews: Sequence[StocktakeReview],
) -> dict[uuid.UUID, str]:
    del stage, existing_reviews
    by_id = {row.difference_id: row for row in command.items}
    difference_by_id = {row.id: row for row in differences}
    if set(by_id) != set(difference_by_id):
        _fail(
            "stocktake_review_items_incomplete",
            "invalid_request",
            "复核必须逐项覆盖该轮全部差异且不得夹带其他差异",
        )
    decisions: dict[uuid.UUID, str] = {}
    for difference in differences:
        item = by_id[difference.id]
        if not item.comment.strip():
            _fail(
                "stocktake_review_item_comment_required",
                "invalid_request",
                "每条盘点差异必须填写独立复核说明",
            )
        is_pending = difference.reason_code == "stocktake_pending_verification"
        if is_pending and item.decision not in RECOUNT_ITEM_DECISIONS:
            _fail(
                "stocktake_review_pending_verification_not_postable",
                "precondition_failed",
                "待核实现场观察只能保留待核实或要求复盘",
            )
        decisions[difference.id] = item.decision

    values = tuple(decisions.values())
    if command.decision == "approve":
        valid = all(value in POSTABLE_ITEM_DECISIONS for value in values)
    elif command.decision == "recount":
        valid = bool(values) and any(
            value in RECOUNT_ITEM_DECISIONS for value in values
        ) and all(value != "reject" for value in values)
    else:
        valid = bool(values) and all(value == "reject" for value in values)
    if not valid:
        _fail(
            "stocktake_review_decision_items_mismatch",
            "invalid_request",
            "整单复核结论与逐项差异决定不一致",
        )
    if command.decision == "approve" and any(
        row.reason_code == "stocktake_pending_verification" for row in differences
    ):
        _fail(
            "stocktake_review_pending_verification_not_postable",
            "precondition_failed",
            "存在待核实现场观察，盘点任务不得进入可过账状态",
        )
    return decisions


def _validate_new_stage_state(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    command: SubmitStocktakeReviewCommand,
    *,
    stage: str,
    existing_reviews: Sequence[StocktakeReview],
) -> None:
    if task.version != command.expected_task_version:
        _fail(
            "stocktake_review_version_conflict",
            "conflict",
            "盘点任务版本已变化，请重新读取",
        )
    if task.current_round_no != round_row.round_no:
        _fail(
            "stocktake_review_stale_round",
            "conflict",
            "该轮次已不是任务当前轮次",
        )
    if stage == REGION_STAGE:
        valid = task.status == "submitted" and not existing_reviews
    else:
        region = next(
            (row for row in existing_reviews if row.review_stage == REGION_STAGE),
            None,
        )
        valid = (
            task.status == "hq_review"
            and len(existing_reviews) == 1
            and region is not None
            and region.decision == "approve"
        )
    if not valid:
        _fail(
            "stocktake_review_state_invalid",
            "precondition_failed",
            "盘点任务当前状态不允许该层级复核",
        )


def _validate_separation_and_prior_items(
    db: Session,
    *,
    actor: FormalPrincipal,
    stage: str,
    command: SubmitStocktakeReviewCommand,
    decisions: Mapping[uuid.UUID, str],
    existing_reviews: Sequence[StocktakeReview],
    now: datetime,
) -> None:
    if stage != HEADQUARTERS_STAGE:
        return
    region = next(
        (row for row in existing_reviews if row.review_stage == REGION_STAGE), None
    )
    if region is None or region.decision != "approve":
        _fail(
            "stocktake_review_region_approval_required",
            "precondition_failed",
            "总部复核必须基于更早的区域通过事实",
        )
    if (
        region.reviewer_user_id == actor.user_id
        or region.reviewer_person_id == actor.person_id
        or now <= _as_utc(region.reviewed_at)
    ):
        _fail(
            "stocktake_review_separation_of_duties_required",
            "forbidden",
            "区域复核与总部复核必须由不同人员且按真实时间顺序完成",
        )
    if command.decision == "approve":
        region_items = {
            row.difference_id: row.decision
            for row in db.scalars(
                select(StocktakeReviewItem)
                .where(StocktakeReviewItem.review_id == region.id)
                .order_by(StocktakeReviewItem.difference_id)
            ).all()
        }
        if region_items != dict(decisions):
            _fail(
                "stocktake_review_headquarters_approval_mismatch",
                "precondition_failed",
                "总部通过不得放宽或改写区域逐项通过决定",
            )


def _authorize_reviewer(
    db: Session,
    actor: FormalPrincipal,
    task: FormalStocktakeTask,
    stage: str,
    now: datetime,
    *,
    lock_rows: bool = True,
) -> tuple[RoleAssignment, ScopeGrant]:
    if stage == REGION_STAGE:
        role_code = "provincial_manager"
        scope_type = "organization"
        scope_id = str(task.region_org_id)
        action = "review_region"
        target_scope_type = "organization"
        target_scope_id = str(task.region_org_id)
    else:
        role_code = "admin"
        scope_type = "national"
        scope_id = "*"
        action = "review_headquarters"
        target_scope_type = "national"
        target_scope_id = "*"
    candidates = tuple(
        row
        for row in actor.assignments
        if row.role_code == role_code
        and row.scope_type == scope_type
        and (
            row.scope_id == scope_id
            if scope_type == "national"
            else _same_uuid(row.scope_id, scope_id)
        )
        and task_service._grant_allows(
            db,
            actor,
            row,
            "stocktake",
            action,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
        )
    )
    if len(candidates) != 1:
        _fail(
            "stocktake_review_forbidden",
            "forbidden",
            "当前人员没有唯一有效的该层级盘点复核授权",
        )
    grant = candidates[0]
    statement = select(RoleAssignment).where(RoleAssignment.id == grant.assignment_id)
    if lock_rows:
        statement = statement.with_for_update()
    assignment = db.scalar(statement.execution_options(populate_existing=True))
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    if (
        assignment is None
        or role is None
        or assignment.user_id != actor.user_id
        or assignment.status != "active"
        or assignment.revoked_at is not None
        or _as_utc(assignment.valid_from) > now
        or (
            assignment.valid_to is not None
            and now >= _as_utc(assignment.valid_to)
        )
        or role.status != "active"
        or role.is_external
        or role.code != role_code
        or assignment.scope_type != scope_type
        or (
            assignment.scope_id != scope_id
            if scope_type == "national"
            else not _same_uuid(assignment.scope_id, scope_id)
        )
    ):
        _fail(
            "stocktake_review_assignment_changed",
            "forbidden",
            "盘点复核角色授权已失效或发生变化",
        )
    return assignment, grant


def _validate_review_replay(
    db: Session,
    *,
    evidence: SealedNonOpeningDifferenceEvidence,
    review: StocktakeReview,
    actor: FormalPrincipal,
    command: SubmitStocktakeReviewCommand,
    stage: str,
    manifest: str,
) -> uuid.UUID | None:
    items = tuple(
        db.scalars(
            select(StocktakeReviewItem)
            .where(StocktakeReviewItem.review_id == review.id)
            .order_by(StocktakeReviewItem.difference_id)
        ).all()
    )
    expected_items = {
        row.difference_id: (row.decision, row.comment) for row in command.items
    }
    actual_items = {
        row.difference_id: (row.decision, row.comment) for row in items
    }
    expected_status = _resulting_status(stage, command.decision)
    expected_task_version = command.expected_task_version
    resulting_task_version = expected_task_version + 1
    state_rows = tuple(
        db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id == str(evidence.task.id),
                StateTransitionEvent.idempotency_key == _event_key("state", review.id),
            )
        ).all()
    )
    if (
        review.task_id != evidence.task.id
        or review.round_id != evidence.round_row.id
        or review.review_stage != stage
        or review.reviewer_user_id != actor.user_id
        or review.reviewer_person_id != actor.person_id
        or review.authorization_version != actor.authorization_version
        or review.expected_task_version != expected_task_version
        or review.resulting_task_version != resulting_task_version
        or review.decision != command.decision
        or review.comment != command.comment
        or review.decision_manifest_sha256 != manifest
        or actual_items != expected_items
        or len(items) != len(evidence.differences)
        or any(_as_utc(row.created_at) != _as_utc(review.reviewed_at) for row in items)
        or len(state_rows) != 1
        or state_rows[0].to_status != expected_status
        or not isinstance(state_rows[0].metadata_jsonb, dict)
        or state_rows[0].metadata_jsonb.get("expected_task_version")
        != expected_task_version
        or state_rows[0].metadata_jsonb.get("resulting_task_version")
        != resulting_task_version
    ):
        _fail(
            "stocktake_review_idempotency_conflict",
            "conflict",
            "幂等键已绑定不同复核请求或历史复核证据无法重证",
        )
    if stage == HEADQUARTERS_STAGE and command.decision == "approve":
        completion = _validate_effective_approval_replay(
            db,
            evidence=evidence,
            review=review,
            actor=actor,
            command=command,
            review_manifest=manifest,
        )
        return completion.id
    if db.scalar(
        select(StocktakeEffectiveApprovalCompletion.id).where(
            StocktakeEffectiveApprovalCompletion.terminal_headquarters_review_id
            == review.id
        )
    ) is not None:
        _fail(
            "stocktake_review_idempotency_conflict",
            "conflict",
            "非最终总部通过复核不得绑定任务级有效审批事实",
        )
    return None


def _validate_effective_approval_replay(
    db: Session,
    *,
    evidence: SealedNonOpeningDifferenceEvidence,
    review: StocktakeReview,
    actor: FormalPrincipal,
    command: SubmitStocktakeReviewCommand,
    review_manifest: str,
) -> StocktakeEffectiveApprovalCompletion:
    completions = tuple(
        db.scalars(
            select(StocktakeEffectiveApprovalCompletion).where(
                StocktakeEffectiveApprovalCompletion.task_id == evidence.task.id
            )
        ).all()
    )
    if len(completions) != 1:
        _fail(
            "stocktake_review_idempotency_conflict",
            "conflict",
            "最终总部复核缺少唯一任务级有效审批完成事实",
        )
    completion = completions[0]
    scopes = tuple(
        db.scalars(
            select(StocktakeEffectiveApprovalScope)
            .where(StocktakeEffectiveApprovalScope.completion_id == completion.id)
            .order_by(StocktakeEffectiveApprovalScope.scope_id)
        ).all()
    )
    items = tuple(
        db.scalars(
            select(StocktakeEffectiveApprovalItem)
            .where(StocktakeEffectiveApprovalItem.completion_id == completion.id)
            .order_by(StocktakeEffectiveApprovalItem.difference_id)
        ).all()
    )
    supplied_scope_ids = (
        command.effective_scope_ids
        if command.effective_scope_ids
        else tuple(row.scope_id for row in scopes)
    )
    expected_request_sha256 = _sha256(
        {
            "approval_manifest_sha256": completion.approval_manifest_sha256,
            "effective_items": [
                {
                    "comment": row.comment,
                    "decision": row.decision,
                    "difference_id": str(row.difference_id),
                }
                for row in sorted(
                    command.effective_items, key=lambda row: str(row.difference_id)
                )
            ],
            "effective_scope_ids": [
                str(value) for value in sorted(supplied_scope_ids, key=str)
            ],
            "review_manifest_sha256": review_manifest,
            "expected_task_version": command.expected_task_version,
            "schema": "cloud_oam.stocktake.effective_approval_request.v1",
        }
    )
    expected_old_items = {
        row.difference_id: (row.decision, row.comment)
        for row in command.effective_items
    }
    actual_old_items = {
        row.difference_id: (row.headquarters_decision, row.headquarters_comment)
        for row in items
        if row.source_round_id != review.round_id
    }
    if (
        completion.terminal_headquarters_review_id != review.id
        or completion.terminal_round_id != review.round_id
        or completion.expected_task_version != command.expected_task_version
        or completion.approved_task_version != command.expected_task_version + 1
        or completion.completed_by_user_id != actor.user_id
        or completion.completed_by_person_id != actor.person_id
        or completion.authorization_version != actor.authorization_version
        or completion.request_sha256 != expected_request_sha256
        or completion.scope_count != len(scopes)
        or {row.scope_id for row in scopes} != set(supplied_scope_ids)
        or completion.difference_count != len(items)
        or completion.accepted_difference_count
        != sum(row.headquarters_decision == "accept_for_posting" for row in items)
        or completion.no_adjustment_count
        != sum(row.headquarters_decision == "no_adjustment" for row in items)
        or actual_old_items != expected_old_items
    ):
        _fail(
            "stocktake_review_idempotency_conflict",
            "conflict",
            "任务级有效审批完成事实与幂等复核请求不一致",
        )
    return completion


def _review_manifest(
    *,
    evidence: SealedNonOpeningDifferenceEvidence,
    stage: str,
    command: SubmitStocktakeReviewCommand,
    decisions: Mapping[uuid.UUID, str],
) -> str:
    input_by_id = {row.difference_id: row for row in command.items}
    return _sha256(
        {
            "comment": command.comment,
            "decision": command.decision,
            "difference_completion_id": str(evidence.completion.id),
            "difference_manifest_sha256": evidence.completion.difference_manifest_sha256,
            "effective_items": [
                {
                    "comment": row.comment,
                    "decision": row.decision,
                    "difference_id": str(row.difference_id),
                }
                for row in command.effective_items
            ],
            "effective_scope_ids": [str(value) for value in command.effective_scope_ids],
            "items": [
                {
                    "comment": input_by_id[row.id].comment,
                    "decision": decisions[row.id],
                    "difference_id": str(row.id),
                    "difference_no": row.difference_no,
                }
                for row in evidence.differences
            ],
            "expected_task_version": command.expected_task_version,
            "round_id": str(evidence.round_row.id),
            "resulting_task_version": command.expected_task_version + 1,
            "schema": "cloud_oam.stocktake.nonopening_review.v2",
            "stage": stage,
            "task_id": str(evidence.task.id),
        }
    )


def _verify_evidence_audit(db: Session, event: AuditEvent, proof: object) -> None:
    try:
        verified = _verify_audit_event_with_prelocked_proof(
            db,
            proof=proof,
            stream_key=INVENTORY_STREAM_KEY,
            event_id=event.id,
        )
    except AuditChainError as exc:
        _fail(
            "stocktake_review_source_audit_invalid",
            "service_unavailable",
            "盘点差异评估审计链无法重证",
            cause=exc,
        )
    if verified.id != event.id:
        _evidence_invalid("盘点差异评估审计坐标不一致")


def _verify_review_audit(db: Session, review: StocktakeReview, proof: object) -> None:
    events = tuple(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.stream_key == INVENTORY_STREAM_KEY,
                AuditEvent.action
                == f"stocktake.nonopening.{review.review_stage}_reviewed",
                AuditEvent.aggregate_type == "stocktake_review",
                AuditEvent.aggregate_id == str(review.id),
            )
        ).all()
    )
    if len(events) != 1:
        _evidence_invalid("盘点复核审计事实缺失或不唯一")
    try:
        verified = _verify_audit_event_with_prelocked_proof(
            db,
            proof=proof,
            stream_key=INVENTORY_STREAM_KEY,
            event_id=events[0].id,
        )
    except AuditChainError as exc:
        _fail(
            "stocktake_review_audit_invalid",
            "service_unavailable",
            "盘点复核审计链无法重证",
            cause=exc,
        )
    after = verified.after_jsonb
    if (
        not isinstance(after, dict)
        or after.get("decision_manifest_sha256")
        != review.decision_manifest_sha256
        or after.get("review_id") != str(review.id)
        or after.get("expected_task_version") != review.expected_task_version
        or after.get("resulting_task_version") != review.resulting_task_version
    ):
        _evidence_invalid("盘点复核审计摘要与复核事实不一致")


def _load_reviews(
    db: Session, task_id: uuid.UUID, round_id: uuid.UUID
) -> tuple[StocktakeReview, ...]:
    rows = tuple(
        db.scalars(
            select(StocktakeReview)
            .where(
                StocktakeReview.task_id == task_id,
                StocktakeReview.round_id == round_id,
            )
            .order_by(StocktakeReview.reviewed_at, StocktakeReview.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    stages = [row.review_stage for row in rows]
    if stages not in ([], [REGION_STAGE], [REGION_STAGE, HEADQUARTERS_STAGE]):
        _evidence_invalid("盘点复核层级链不完整或顺序无效")
    return rows


def _require_review_version_coordinates(review: StocktakeReview) -> None:
    """Reject legacy NULL coordinates instead of guessing historical state."""

    expected = review.expected_task_version
    resulting = review.resulting_task_version
    if (
        type(expected) is not int
        or expected < 0
        or type(resulting) is not int
        or resulting != expected + 1
    ):
        _fail(
            "stocktake_review_version_history_unavailable",
            "service_unavailable",
            "历史盘点复核缺少连续任务版本证据",
        )


def _task_principal_user_ids(
    db: Session,
    task_id: uuid.UUID,
    *,
    supplied_user_ids: Sequence[str],
) -> tuple[str, ...]:
    values = {value for value in supplied_user_ids if value}
    values.update(
        db.scalars(
            select(FormalStocktakeScope.assignee_user_id).where(
                FormalStocktakeScope.task_id == task_id
            )
        ).all()
    )
    values.update(
        db.scalars(
            select(StocktakeRoundSubmission.submitted_by_user_id).where(
                StocktakeRoundSubmission.task_id == task_id
            )
        ).all()
    )
    values.update(
        db.scalars(
            select(StocktakeDifferenceSetCompletion.completed_by_user_id).where(
                StocktakeDifferenceSetCompletion.task_id == task_id
            )
        ).all()
    )
    values.update(
        db.scalars(
            select(StocktakeReview.reviewer_user_id).where(
                StocktakeReview.task_id == task_id
            )
        ).all()
    )
    return tuple(sorted(value for value in values if value))


def _result(
    review: StocktakeReview,
    differences: Sequence[StocktakeDifference],
    task_version: int | None = None,
    *,
    effective_approval_completion_id: uuid.UUID | None = None,
) -> StocktakeReviewResult:
    pending = sum(
        1
        for row in differences
        if row.reason_code == "stocktake_pending_verification"
    )
    status = _resulting_status(review.review_stage, review.decision)
    if review.expected_task_version is None or review.resulting_task_version is None:
        _fail(
            "stocktake_review_version_history_unavailable",
            "service_unavailable",
            "盘点复核缺少连续任务版本证据",
        )
    _require_review_version_coordinates(review)
    return StocktakeReviewResult(
        review_id=review.id,
        task_id=review.task_id,
        round_id=review.round_id,
        review_stage=review.review_stage,
        decision=review.decision,
        resulting_task_status=status,
        expected_task_version=review.expected_task_version,
        resulting_task_version=review.resulting_task_version,
        task_version=review.resulting_task_version,
        item_count=len(differences),
        pending_verification_count=pending,
        ready_for_posting=(
            review.review_stage == HEADQUARTERS_STAGE
            and review.decision == "approve"
            and pending == 0
        ),
        effective_approval_completion_id=effective_approval_completion_id,
    )


def _resulting_status(stage: str, decision: str) -> str:
    if decision != "approve":
        return "recount_required"
    return "hq_review" if stage == REGION_STAGE else "approved"


def _validate_command(command: SubmitStocktakeReviewCommand) -> SubmitStocktakeReviewCommand:
    if not isinstance(command, SubmitStocktakeReviewCommand):
        _fail("stocktake_review_command_required", "invalid_request", "盘点复核命令类型无效")
    task_id = _require_uuid("task_id", command.task_id)
    round_id = _require_uuid("round_id", command.round_id)
    if not isinstance(command.expected_task_version, int) or command.expected_task_version < 0:
        _fail("stocktake_review_version_invalid", "invalid_request", "任务版本无效")
    if command.decision not in TOP_LEVEL_DECISIONS:
        _fail("stocktake_review_decision_invalid", "invalid_request", "复核结论无效")
    comment = _require_text("comment", command.comment, 10000, allow_empty=True)
    if command.decision != "approve" and not comment.strip():
        _fail(
            "stocktake_review_comment_required",
            "invalid_request",
            "复盘或驳回必须填写整单复核说明",
        )
    if not isinstance(command.items, tuple):
        _fail("stocktake_review_items_invalid", "invalid_request", "复核明细必须使用不可变元组")
    items: list[StocktakeReviewItemInput] = []
    seen: set[uuid.UUID] = set()
    for item in command.items:
        if not isinstance(item, StocktakeReviewItemInput):
            _fail("stocktake_review_item_invalid", "invalid_request", "复核明细类型无效")
        difference_id = _require_uuid("difference_id", item.difference_id)
        if difference_id in seen:
            _fail("stocktake_review_item_duplicate", "invalid_request", "同一差异不得重复复核")
        seen.add(difference_id)
        if item.decision not in ITEM_DECISIONS:
            _fail("stocktake_review_item_decision_invalid", "invalid_request", "逐项复核决定无效")
        items.append(
            StocktakeReviewItemInput(
                difference_id=difference_id,
                decision=item.decision,
                comment=_require_text("item.comment", item.comment, 10000, allow_empty=True),
            )
        )
    if not isinstance(command.effective_scope_ids, tuple):
        _fail(
            "stocktake_review_effective_scopes_invalid",
            "invalid_request",
            "有效审批范围必须使用不可变元组",
        )
    effective_scope_ids = tuple(
        _require_uuid("effective_scope_id", value)
        for value in command.effective_scope_ids
    )
    if len(set(effective_scope_ids)) != len(effective_scope_ids):
        _fail(
            "stocktake_review_effective_scope_duplicate",
            "invalid_request",
            "有效审批范围不得重复",
        )
    if not isinstance(command.effective_items, tuple):
        _fail(
            "stocktake_review_effective_items_invalid",
            "invalid_request",
            "历史有效差异复核必须使用不可变元组",
        )
    effective_items: list[StocktakeEffectiveApprovalItemInput] = []
    seen_effective_items: set[uuid.UUID] = set()
    for item in command.effective_items:
        if not isinstance(item, StocktakeEffectiveApprovalItemInput):
            _fail(
                "stocktake_review_effective_item_invalid",
                "invalid_request",
                "历史有效差异复核明细类型无效",
            )
        difference_id = _require_uuid("effective_difference_id", item.difference_id)
        if difference_id in seen_effective_items:
            _fail(
                "stocktake_review_effective_item_duplicate",
                "invalid_request",
                "同一历史有效差异不得重复复核",
            )
        seen_effective_items.add(difference_id)
        if item.decision not in POSTABLE_ITEM_DECISIONS:
            _fail(
                "stocktake_review_effective_item_decision_invalid",
                "invalid_request",
                "历史有效差异只能明确通过过账或明确不调整",
            )
        effective_items.append(
            StocktakeEffectiveApprovalItemInput(
                difference_id=difference_id,
                decision=item.decision,
                comment=_require_text(
                    "effective_item.comment",
                    item.comment,
                    10000,
                    allow_empty=False,
                ),
            )
        )
    return SubmitStocktakeReviewCommand(
        task_id=task_id,
        round_id=round_id,
        expected_task_version=command.expected_task_version,
        decision=command.decision,
        items=tuple(items),
        comment=comment,
        effective_scope_ids=tuple(sorted(effective_scope_ids, key=str)),
        effective_items=tuple(
            sorted(effective_items, key=lambda row: str(row.difference_id))
        ),
    )


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal) or not actor.user_id:
        _fail("stocktake_review_actor_invalid", "forbidden", "正式操作人上下文无效")
    return actor


def _require_current_actor(
    db: Session, supplied: FormalPrincipal, now: datetime
) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError as exc:
        _fail("stocktake_review_actor_not_current", "forbidden", "正式操作人授权已失效", cause=exc)
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
        or current.access_mode != "active"
    ):
        _fail("stocktake_review_actor_principal_stale", "forbidden", "正式操作人权限版本已变化")
    return current


def _idempotency_hmac(
    secret: bytes, actor_user_id: str, path: str, raw_key: str
) -> str:
    return _hmac_hex(
        secret,
        {
            "actor_user_id": actor_user_id,
            "idempotency_key": raw_key,
            "path": path,
            "schema": "cloud_oam.stocktake.nonopening_review_idempotency.v1",
        },
    )


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
    except (TypeError, ValueError) as exc:
        _fail("stocktake_review_manifest_invalid", "invalid_request", "复核清单无法规范化", cause=exc)
    raise AssertionError("unreachable canonical document")


def _timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _event_key(kind: str, review_id: uuid.UUID) -> str:
    return f"stocktake-nonopening-review:{kind}:{review_id}"


def _request_reference(raw: str) -> str:
    return f"stocktake-review:{hashlib.sha256(raw.encode('utf-8')).hexdigest()}"


def _require_uuid(field: str, value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        _fail("stocktake_review_uuid_invalid", "invalid_request", f"{field} 无效", cause=exc)
    raise AssertionError("unreachable uuid")


def _require_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or not (8 <= len(value) <= 200) or not _PRINTABLE.fullmatch(value):
        _fail("stocktake_review_idempotency_key_invalid", "invalid_request", "Idempotency-Key 无效")
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    secret = value.encode("utf-8") if isinstance(value, str) else value
    if not isinstance(secret, bytes) or len(secret) < 32:
        _fail("stocktake_review_hmac_secret_invalid", "service_unavailable", "幂等摘要密钥未安全配置")
    return secret


def _require_trace_request_id(value: object) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > 160:
        _fail("stocktake_review_trace_id_invalid", "invalid_request", "请求追踪号无效")
    return value.strip()


def _require_text(
    field: str, value: object, limit: int, *, allow_empty: bool
) -> str:
    if not isinstance(value, str) or len(value) > limit or (not allow_empty and not value.strip()):
        _fail("stocktake_review_text_invalid", "invalid_request", f"{field} 无效")
    return value


def _lock_coordinate(namespace: str, value: str) -> int:
    raw = hashlib.sha256(f"{namespace}\0{value}".encode("utf-8")).digest()[:8]
    return int.from_bytes(raw, byteorder="big", signed=True)


def _take_advisory_locks(db: Session, coordinates: Sequence[int]) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for coordinate in sorted(set(coordinates)):
        db.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": coordinate})


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(func.current_timestamp()))
    if value is None:
        _fail("stocktake_review_clock_unavailable", "service_unavailable", "数据库时间不可用")
    return _as_utc(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_uuid(left: object, right: object) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _evidence_invalid(message: str) -> None:
    _fail(
        "stocktake_review_source_evidence_invalid",
        "service_unavailable",
        message,
    )


def _fail(
    code: str,
    category: str,
    message: str,
    *,
    cause: Exception | None = None,
) -> None:
    error = StocktakeReviewError(code, category, message)
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "HEADQUARTERS_STAGE",
    "NON_OPENING_TYPES",
    "REGION_STAGE",
    "SealedNonOpeningDifferenceEvidence",
    "StocktakeReviewError",
    "StocktakeEffectiveApprovalItemInput",
    "StocktakeReviewItemInput",
    "StocktakeReviewResult",
    "SubmitStocktakeReviewCommand",
    "submit_stocktake_headquarters_review",
    "submit_stocktake_region_review",
]
