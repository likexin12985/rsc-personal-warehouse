"""Internal two-stage review boundary for a submitted opening stocktake.

Regional and NIO-headquarters review are deliberately separate immutable
facts, permissions, state transitions, outbox events and audit events.  The
service is called by two separate formal HTTP commands.  The caller owns the
transaction; the service flushes but never commits or rolls back.

This boundary never creates an inventory account, balance, movement,
transaction, posting or opening-establishment fact.  OAM provincial control
differences remain ``pending_verification`` review items and can never be
treated as local stock.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
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
    Organization,
    OutboxEvent,
    Person,
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
from ..inventory_models import StockLocation
from ..models import User
from ..stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    InventoryOpeningEstablishment,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeCountSerial,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakeObservationDisposition,
    StocktakePosting,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeReviewItem,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)
from . import opening_observation_disposition as observation_disposition_service
from .audit_chain import (
    AuditChainError,
    _lock_audit_chain_head_with_proof,
    _require_prelocked_audit_stream_proof,
    _verify_audit_event_with_prelocked_proof,
    append_audit_event,
)
from .inventory_posting import (
    canonical_opening_count_manifest_sha256,
    canonical_opening_decision_manifest_sha256,
)
from .opening_stocktake_count import (
    OpeningStocktakeCountError,
    _event_hash as _count_event_hash,
    _hash_document as _count_hash_document,
    _round_manifest_sha256,
    _validate_completion_evidence,
    _validate_difference_set_completion,
    _validate_initial_difference_set,
)
from .postgresql_lock_graph import (
    lock_opening_stocktake_task_evidence,
)


INVENTORY_STREAM_KEY: Final[str] = "inventory"
REGION_STAGE: Final[str] = "region"
HEADQUARTERS_STAGE: Final[str] = "headquarters"
_PRINTABLE = re.compile(r"^[\x21-\x7e]+$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_ZERO: Final[Decimal] = Decimal("0.000")
_TOP_LEVEL_DECISIONS = frozenset({"approve", "recount", "reject"})
_ITEM_DECISIONS = frozenset(
    {"accept_for_posting", "pending_verification", "recount", "reject"}
)
_OPENING_REVIEW_EVIDENCE_PLAN_SEAL: Final[object] = object()
_OPENING_RECOUNT_TRIGGER_PLAN_SEAL: Final[object] = object()
_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class OpeningStocktakeReviewError(RuntimeError):
    """Stable, database-detail-free failure for the review boundary."""

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
class OpeningStocktakeReviewItemInput:
    difference_id: uuid.UUID
    decision: str
    comment: str = ""


@dataclass(frozen=True, slots=True)
class SubmitOpeningStocktakeReviewCommand:
    task_id: uuid.UUID
    round_id: uuid.UUID
    decision: str
    items: tuple[OpeningStocktakeReviewItemInput, ...]
    comment: str = ""


@dataclass(frozen=True, slots=True)
class OpeningStocktakeReviewResult:
    review_id: uuid.UUID
    task_id: uuid.UUID
    round_id: uuid.UUID
    review_stage: str
    decision: str
    resulting_task_status: str
    item_count: int
    pending_control_count: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _ObservationReviewGraph:
    observations_by_id: Mapping[uuid.UUID, StocktakeCountObservation]
    dispositions_by_observation_id: Mapping[
        uuid.UUID, StocktakeObservationDisposition
    ]


@dataclass(frozen=True, slots=True)
class _ObservationReviewEvidence:
    outcomes: Mapping[uuid.UUID, str]
    disposition_replay_plans: tuple[tuple[uuid.UUID, object], ...]


@dataclass(frozen=True, slots=True)
class _OpeningReviewEvidencePlan:
    session: Session
    transaction: object
    task_id: uuid.UUID
    round_id: uuid.UUID
    review_id: uuid.UUID
    expected_stage: str
    expected_decision: str
    disposition_resolutions: tuple[tuple[uuid.UUID, object], ...]
    disposition_replay_plans: tuple[tuple[uuid.UUID, object], ...]
    allow_unpersisted_resolution_ids: bool
    audit_event_id: uuid.UUID
    seal: object


@dataclass(frozen=True, slots=True)
class _OpeningRecountTriggerReviewPlan:
    session: Session
    transaction: object
    task_id: uuid.UUID
    round_id: uuid.UUID
    reviews: tuple[tuple[uuid.UUID, str, str], ...]
    review_plans: tuple[_OpeningReviewEvidencePlan, ...]
    trigger_review_id: uuid.UUID
    seal: object


def submit_opening_region_review(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitOpeningStocktakeReviewCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeReviewResult:
    """Record the regional review without committing or posting inventory."""

    return _public_review_boundary(
        db,
        actor=actor,
        command=command,
        stage=REGION_STAGE,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )


def submit_opening_headquarters_review(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitOpeningStocktakeReviewCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeReviewResult:
    """Record the NIO-headquarters review without commit/finalize/posting."""

    return _public_review_boundary(
        db,
        actor=actor,
        command=command,
        stage=HEADQUARTERS_STAGE,
        idempotency_key=idempotency_key,
        request_id=request_id,
    )


def validate_opening_review_evidence_for_replay(
    db: Session,
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    review_id: uuid.UUID,
    expected_stage: str,
    expected_decision: str,
) -> StocktakeReview:
    """Standalone strong replay in task -> principals -> evidence lock order."""

    checked_task_id = _require_uuid("task_id", task_id)
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked_task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        _replay_evidence_invalid("期初盘点复核任务不存在")
    from . import inventory_posting as posting_service

    try:
        principal_graph = posting_service._lock_opening_task_principal_graph(
            db,
            task_ids=(checked_task_id,),
        )
        plan = _plan_opening_review_evidence(
            db,
            task_id=checked_task_id,
            round_id=round_id,
            review_id=review_id,
            expected_stage=expected_stage,
            expected_decision=expected_decision,
            disposition_resolutions=None,
            task_evidence_prelocked=False,
            allow_unpersisted_resolution_ids=False,
        )
        _head, audit_proof = _lock_audit_chain_head_with_proof(
            db,
            stream_key=INVENTORY_STREAM_KEY,
        )
        verified = _validate_opening_review_evidence_from_prelocked_task_graph(
            db,
            plan=plan,
            audit_proof=audit_proof,
        )
        posting_service._validate_prelocked_opening_task_principal_graph(
            db,
            proof=principal_graph,
        )
        return verified
    except posting_service.InventoryPostingError as exc:
        _replay_evidence_invalid(
            "期初盘点复核的历史人员授权图无法安全重证",
            cause=exc,
        )


def _plan_opening_review_evidence_from_prelocked_reference_graph(
    db: Session,
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    review_id: uuid.UUID,
    expected_stage: str,
    expected_decision: str,
    disposition_resolutions: Mapping[uuid.UUID, object],
) -> _OpeningReviewEvidencePlan:
    """Capture review structure after reference owners, before audit."""

    return _plan_opening_review_evidence(
        db,
        task_id=task_id,
        round_id=round_id,
        review_id=review_id,
        expected_stage=expected_stage,
        expected_decision=expected_decision,
        disposition_resolutions=disposition_resolutions,
        task_evidence_prelocked=True,
        allow_unpersisted_resolution_ids=True,
    )


def _validate_opening_review_evidence_from_prelocked_task_graph(
    db: Session,
    *,
    plan: object,
    audit_proof: object,
) -> StocktakeReview:
    """Pure review replay guarded by transaction-bound plan and audit proof."""

    checked = _require_opening_review_evidence_plan(db, plan)
    try:
        _require_prelocked_audit_stream_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
        )
    except AuditChainError as exc:
        _replay_evidence_invalid(
            "期初盘点复核审计预锁证明不属于当前事务",
            cause=exc,
        )
    recaptured = _plan_opening_review_evidence(
        db,
        task_id=checked.task_id,
        round_id=checked.round_id,
        review_id=checked.review_id,
        expected_stage=checked.expected_stage,
        expected_decision=checked.expected_decision,
        disposition_resolutions=dict(checked.disposition_resolutions),
        task_evidence_prelocked=True,
        allow_unpersisted_resolution_ids=(
            checked.allow_unpersisted_resolution_ids
        ),
        expected_disposition_replay_plans=dict(
            checked.disposition_replay_plans
        ),
        audit_proof=audit_proof,
    )
    if recaptured != checked:
        _replay_evidence_invalid("期初盘点复核预锁结构在审计后发生漂移")
    try:
        verified = _verify_audit_event_with_prelocked_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
            event_id=checked.audit_event_id,
        )
    except AuditChainError as exc:
        _replay_evidence_invalid("期初盘点复核审计链无法验证", cause=exc)
    if verified.id != checked.audit_event_id:
        _replay_evidence_invalid("期初盘点复核审计事件坐标不一致")
    review = db.get(StocktakeReview, checked.review_id)
    if review is None:
        _replay_evidence_invalid("期初盘点复核事实不存在")
    return review


def _plan_opening_review_evidence(
    db: Session,
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    review_id: uuid.UUID,
    expected_stage: str,
    expected_decision: str,
    disposition_resolutions: Mapping[uuid.UUID, object] | None,
    task_evidence_prelocked: bool,
    allow_unpersisted_resolution_ids: bool,
    expected_disposition_replay_plans: Mapping[uuid.UUID, object] | None = None,
    audit_proof: object | None = None,
) -> _OpeningReviewEvidencePlan:
    """Capture one complete review structure without touching audit locks.

    Downstream recount and posting services must not trust the review row or
    its digest in isolation.  This validator recomputes the complete item and
    observation/disposition graph, revalidates the historical reviewer
    identity and exact scoped assignment at ``reviewed_at``, and proves the
    state, outbox and hash-chained audit facts created by the review service.

    The caller must later bind the returned plan to a transaction-bound audit
    proof.  This phase performs no writes, commits, rollbacks or audit locks.
    """

    if expected_stage not in {REGION_STAGE, HEADQUARTERS_STAGE}:
        _fail(
            "opening_review_replay_expectation_invalid",
            "invalid_request",
            "期初盘点复核重证阶段无效",
        )
    if expected_decision not in _TOP_LEVEL_DECISIONS:
        _fail(
            "opening_review_replay_expectation_invalid",
            "invalid_request",
            "期初盘点复核重证结论无效",
        )
    _validate_stage_decision(expected_stage, expected_decision)
    checked_task_id = _require_uuid("task_id", task_id)
    checked_round_id = _require_uuid("round_id", round_id)
    checked_review_id = _require_uuid("review_id", review_id)
    task = db.get(FormalStocktakeTask, checked_task_id)
    round_row = db.get(StocktakeRound, checked_round_id)
    review = db.get(StocktakeReview, checked_review_id)
    if (
        task is None
        or round_row is None
        or review is None
        or round_row.task_id != task.id
        or review.task_id != task.id
        or review.round_id != round_row.id
        or review.review_stage != expected_stage
        or review.decision != expected_decision
    ):
        _replay_evidence_invalid("期初盘点复核主体、轮次、阶段或结论不一致")

    assert task is not None
    assert round_row is not None
    assert review is not None
    # Downstream replay callers may invoke this validator directly.  Pin the
    # same task-local evidence graph before issuing the ordinary SELECTs below;
    # callers that already hold it simply reacquire the same transaction locks.
    if not task_evidence_prelocked:
        lock_opening_stocktake_task_evidence(db, task.id, round_row.id)
    elif disposition_resolutions is None:
        _replay_evidence_invalid("期初盘点预锁处置解析集合缺失")
    else:
        persisted_ids = set(
            db.scalars(
                select(StocktakeObservationDisposition.observation_id).where(
                    StocktakeObservationDisposition.task_id == task.id
                )
            ).all()
        )
        resolution_ids = set(disposition_resolutions)
        if allow_unpersisted_resolution_ids:
            task_observation_ids = set(
                db.scalars(
                    select(StocktakeCountObservation.id).where(
                        StocktakeCountObservation.task_id == task.id
                    )
                ).all()
            )
            valid_resolution_set = (
                persisted_ids.issubset(resolution_ids)
                and resolution_ids.issubset(task_observation_ids)
            )
        else:
            valid_resolution_set = resolution_ids == persisted_ids
        if not valid_resolution_set:
            _replay_evidence_invalid(
                "期初盘点预锁处置解析集合与任务证据全集不一致"
            )
    same_stage_reviews = tuple(
        db.scalars(
            select(StocktakeReview.id).where(
                StocktakeReview.task_id == task.id,
                StocktakeReview.round_id == round_row.id,
                StocktakeReview.review_stage == expected_stage,
            )
        ).all()
    )
    if same_stage_reviews != (review.id,):
        _replay_evidence_invalid("期初盘点同层级复核事实缺失或不唯一")
    reviewed_at = _as_optional_utc(review.reviewed_at)
    created_at = _as_optional_utc(review.created_at)
    submitted_at = _as_optional_utc(round_row.submitted_at)
    if (
        reviewed_at is None
        or created_at != reviewed_at
        or submitted_at is None
        or reviewed_at < submitted_at
    ):
        _replay_evidence_invalid("期初盘点复核事实时间无法从提交轮次重证")
    _validate_historical_reviewer_authorization(
        db,
        task=task,
        review=review,
        reviewed_at=reviewed_at,
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
    if [row.difference_no for row in differences] != list(
        range(1, len(differences) + 1)
    ):
        _replay_evidence_invalid("期初盘点复核差异序号不连续")
    items = tuple(
        db.scalars(
            select(StocktakeReviewItem)
            .where(StocktakeReviewItem.review_id == review.id)
            .order_by(StocktakeReviewItem.difference_id)
        ).all()
    )
    if (
        len(items) != len(differences)
        or any(
            row.task_id != task.id
            or row.round_id != round_row.id
            or _as_optional_utc(row.created_at) != reviewed_at
            for row in items
        )
    ):
        _replay_evidence_invalid("期初盘点逐项复核事实不完整或时间不一致")
    try:
        command = _validate_command(
            SubmitOpeningStocktakeReviewCommand(
                task_id=task.id,
                round_id=round_row.id,
                decision=review.decision,
                comment=review.comment,
                items=tuple(
                    OpeningStocktakeReviewItemInput(
                        difference_id=row.difference_id,
                        decision=row.decision,
                        comment=row.comment,
                    )
                    for row in items
                ),
            )
        )
        observation_graph = _lock_observation_review_graph(
            db,
            task_id=task.id,
            round_id=round_row.id,
        )
        replay_scopes = tuple(
            db.scalars(
                select(FormalStocktakeScope)
                .where(FormalStocktakeScope.task_id == task.id)
                .order_by(FormalStocktakeScope.scope_no)
                .execution_options(populate_existing=True)
            ).all()
        )
        if disposition_resolutions is None:
            disposition_resolutions = _lock_review_reference_graph(
                db,
                task=task,
                round_row=round_row,
                scopes=replay_scopes,
                graph=observation_graph,
            )
        observation_evidence = _validate_observation_review_graph(
            db,
            task=task,
            round_row=round_row,
            differences=differences,
            graph=observation_graph,
            reviewed_at=reviewed_at,
            disposition_resolutions=disposition_resolutions,
            expected_disposition_replay_plans=(
                expected_disposition_replay_plans
            ),
            audit_proof=audit_proof,
        )
        decisions = _validate_review_items(
            command,
            differences,
            round_row=round_row,
            stage=review.review_stage,
            existing_reviews=(),
            observation_evidence=observation_evidence.outcomes,
        )
        expected_manifest = canonical_opening_decision_manifest_sha256(
            task_id=task.id,
            round_id=round_row.id,
            differences=differences,
            decisions=decisions,
        )
    except OpeningStocktakeReviewError:
        raise
    except (TypeError, ValueError) as exc:
        _replay_evidence_invalid(
            "期初盘点逐项复核清单无法规范化",
            cause=exc,
        )
    if review.decision_manifest_sha256 != expected_manifest:
        _replay_evidence_invalid("期初盘点逐项复核摘要无法重算")
    audit_event_id = _capture_review_side_effects(
        db,
        task,
        review,
        differences,
    )
    transaction = db.get_transaction()
    if transaction is None:
        _replay_evidence_invalid("期初盘点复核结构计划缺少活动事务")
    return _OpeningReviewEvidencePlan(
        session=db,
        transaction=transaction,
        task_id=task.id,
        round_id=round_row.id,
        review_id=review.id,
        expected_stage=expected_stage,
        expected_decision=expected_decision,
        disposition_resolutions=tuple(
            sorted(disposition_resolutions.items(), key=lambda item: str(item[0]))
        ),
        disposition_replay_plans=(
            observation_evidence.disposition_replay_plans
        ),
        allow_unpersisted_resolution_ids=allow_unpersisted_resolution_ids,
        audit_event_id=audit_event_id,
        seal=_OPENING_REVIEW_EVIDENCE_PLAN_SEAL,
    )


def _require_opening_review_evidence_plan(
    db: Session,
    plan: object,
) -> _OpeningReviewEvidencePlan:
    transaction = db.get_transaction()
    if (
        not isinstance(plan, _OpeningReviewEvidencePlan)
        or plan.seal is not _OPENING_REVIEW_EVIDENCE_PLAN_SEAL
        or plan.session is not db
        or transaction is None
        or plan.transaction is not transaction
    ):
        _replay_evidence_invalid(
            "期初盘点复核结构计划不属于当前事务"
        )
    return plan


def validate_opening_recount_trigger_review_graph(
    db: Session,
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
) -> StocktakeReview:
    """Standalone trigger replay in task -> principal -> evidence -> audit order."""

    checked_task_id = _require_uuid("task_id", task_id)
    checked_round_id = _require_uuid("round_id", round_id)
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked_task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    round_row = db.scalar(
        select(StocktakeRound)
        .where(
            StocktakeRound.id == checked_round_id,
            StocktakeRound.task_id == checked_task_id,
        )
        .execution_options(populate_existing=True)
    )
    if task is None or round_row is None:
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "not_found",
            "复盘来源任务或轮次不存在",
        )
    from . import inventory_posting as posting_service

    try:
        principal_graph = posting_service._lock_opening_task_principal_graph(
            db,
            task_ids=(checked_task_id,),
        )
        lock_opening_stocktake_task_evidence(db, task.id, round_row.id)
        scopes = tuple(
            db.scalars(
                select(FormalStocktakeScope)
                .where(FormalStocktakeScope.task_id == task.id)
                .order_by(FormalStocktakeScope.scope_no)
                .execution_options(populate_existing=True)
            ).all()
        )
        graph = _lock_observation_review_graph(
            db,
            task_id=task.id,
            round_id=round_row.id,
        )
        resolutions = _lock_review_reference_graph(
            db,
            task=task,
            round_row=round_row,
            scopes=scopes,
            graph=graph,
        )
        reviews = tuple(
            db.scalars(
                select(StocktakeReview)
                .where(
                    StocktakeReview.task_id == task.id,
                    StocktakeReview.round_id == round_row.id,
                )
                .order_by(StocktakeReview.reviewed_at, StocktakeReview.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        plan = _plan_opening_recount_trigger_review_graph_from_prelocked_reference_graph(
            db,
            task=task,
            round_row=round_row,
            reviews=reviews,
            disposition_resolutions=resolutions,
        )
        _head, audit_proof = _lock_audit_chain_head_with_proof(
            db,
            stream_key=INVENTORY_STREAM_KEY,
        )
        trigger = _validate_opening_recount_trigger_review_graph_from_prelocked_task_graph(
            db,
            plan=plan,
            audit_proof=audit_proof,
        )
        posting_service._validate_prelocked_opening_task_principal_graph(
            db,
            proof=principal_graph,
        )
        return trigger
    except posting_service.InventoryPostingError as exc:
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "service_unavailable",
            "复盘来源历史人员授权图无法重证",
            cause=exc,
        )


def _plan_opening_recount_trigger_review_graph_from_prelocked_reference_graph(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    reviews: Sequence[StocktakeReview],
    disposition_resolutions: Mapping[uuid.UUID, object],
) -> _OpeningRecountTriggerReviewPlan:
    """Capture the legal terminal review graph without locking audit.

    A source round may continue only after either a regional ``recount`` or
    ``reject`` with no headquarters fact, or after a regional ``approve``
    followed by a headquarters ``reject``.  Every participating review is
    fully re-proved; callers must not infer causality from the latest row.
    """

    if (
        task.task_type != "opening"
        or round_row.task_id != task.id
        or round_row.status != "submitted"
        or round_row.submitted_at is None
    ):
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "precondition_failed",
            "复盘来源轮次不是已提交的期初盘点轮次",
        )
    rows = tuple(reviews)
    if any(row.task_id != task.id or row.round_id != round_row.id for row in rows):
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "precondition_failed",
            "复盘复核事实夹带了其他任务或轮次",
        )
    regions = [row for row in rows if row.review_stage == REGION_STAGE]
    headquarters = [
        row for row in rows if row.review_stage == HEADQUARTERS_STAGE
    ]
    if (
        len(regions) != 1
        or len(headquarters) > 1
        or len(rows) != len(regions) + len(headquarters)
    ):
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "precondition_failed",
            "复盘来源轮次的区域或总部复核事实缺失、重复或不受支持",
        )
    region = regions[0]
    hq = headquarters[0] if headquarters else None
    trigger: StocktakeReview
    if region.decision in {"recount", "reject"} and hq is None:
        trigger = region
    elif region.decision == "approve" and hq is not None and hq.decision == "reject":
        region_at = _as_optional_utc(region.reviewed_at)
        hq_at = _as_optional_utc(hq.reviewed_at)
        if (
            region_at is None
            or hq_at is None
            or hq_at <= region_at
            or hq.reviewer_user_id == region.reviewer_user_id
            or hq.reviewer_person_id == region.reviewer_person_id
        ):
            _fail(
                "opening_review_recount_trigger_graph_invalid",
                "precondition_failed",
                "总部驳回与区域通过不满足时间顺序或职责分离",
            )
        trigger = hq
    else:
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "precondition_failed",
            "来源轮次没有唯一合法的复盘终端复核图",
        )

    try:
        review_plans = tuple(
            _plan_opening_review_evidence_from_prelocked_reference_graph(
                db,
                task_id=task.id,
                round_id=round_row.id,
                review_id=review.id,
                expected_stage=review.review_stage,
                expected_decision=review.decision,
                disposition_resolutions=disposition_resolutions,
            )
            for review in rows
        )
    except OpeningStocktakeReviewError as exc:
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "precondition_failed",
            "复盘终端复核的身份、明细或副作用证据无法重证",
            cause=exc,
        )
    transaction = db.get_transaction()
    if transaction is None:
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "service_unavailable",
            "复盘终端复核结构计划缺少活动事务",
        )
    return _OpeningRecountTriggerReviewPlan(
        session=db,
        transaction=transaction,
        task_id=task.id,
        round_id=round_row.id,
        reviews=tuple(
            (row.id, row.review_stage, row.decision) for row in rows
        ),
        review_plans=review_plans,
        trigger_review_id=trigger.id,
        seal=_OPENING_RECOUNT_TRIGGER_PLAN_SEAL,
    )


def _require_opening_recount_trigger_review_plan(
    db: Session,
    plan: object,
) -> _OpeningRecountTriggerReviewPlan:
    transaction = db.get_transaction()
    if (
        not isinstance(plan, _OpeningRecountTriggerReviewPlan)
        or plan.seal is not _OPENING_RECOUNT_TRIGGER_PLAN_SEAL
        or plan.session is not db
        or transaction is None
        or plan.transaction is not transaction
    ):
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "service_unavailable",
            "复盘终端复核结构计划不属于当前事务",
        )
    return plan


def _validate_opening_recount_trigger_review_graph_from_prelocked_task_graph(
    db: Session,
    *,
    plan: object,
    audit_proof: object,
) -> StocktakeReview:
    """Purely validate a planned trigger graph after the final audit lock."""

    checked = _require_opening_recount_trigger_review_plan(db, plan)
    try:
        _require_prelocked_audit_stream_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
        )
    except AuditChainError as exc:
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "service_unavailable",
            "复盘终端复核审计预锁证明不属于当前事务",
            cause=exc,
        )
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked.task_id)
        .execution_options(populate_existing=True)
    )
    round_row = db.scalar(
        select(StocktakeRound)
        .where(StocktakeRound.id == checked.round_id)
        .execution_options(populate_existing=True)
    )
    reviews = tuple(
        db.scalars(
            select(StocktakeReview)
            .where(
                StocktakeReview.id.in_(
                    tuple(review_id for review_id, _stage, _decision in checked.reviews)
                )
            )
            .order_by(StocktakeReview.reviewed_at, StocktakeReview.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if task is None or round_row is None:
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "service_unavailable",
            "复盘终端复核任务或轮次漂移",
        )
    resolutions = (
        dict(checked.review_plans[0].disposition_resolutions)
        if checked.review_plans
        else {}
    )
    recaptured = _plan_opening_recount_trigger_review_graph_from_prelocked_reference_graph(
        db,
        task=task,
        round_row=round_row,
        reviews=reviews,
        disposition_resolutions=resolutions,
    )
    if recaptured != checked:
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "service_unavailable",
            "复盘终端复核结构在审计后发生漂移",
        )
    for review_plan in checked.review_plans:
        _validate_opening_review_evidence_from_prelocked_task_graph(
            db,
            plan=review_plan,
            audit_proof=audit_proof,
        )
    trigger = db.get(StocktakeReview, checked.trigger_review_id)
    if trigger is None:
        _fail(
            "opening_review_recount_trigger_graph_invalid",
            "service_unavailable",
            "复盘终端复核事实不存在",
        )
    return trigger


def _public_review_boundary(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitOpeningStocktakeReviewCommand,
    stage: str,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeReviewResult:
    try:
        return _submit_review(
            db,
            actor=actor,
            command=command,
            stage=stage,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except OpeningStocktakeReviewError:
        raise
    except AuditChainError as exc:
        _fail(
            "opening_review_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，期初盘点复核未完成",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "opening_review_concurrent_conflict",
            "conflict",
            "期初盘点复核发生并发冲突，请回滚并重新读取",
            cause=exc,
        )
    except DBAPIError as exc:
        _fail(
            "opening_review_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了期初盘点复核，请回滚并重新读取",
            cause=exc,
        )
    raise AssertionError("unreachable opening review boundary")


def _submit_review(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitOpeningStocktakeReviewCommand,
    stage: str,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeReviewResult:
    supplied_actor = _validate_supplied_actor(actor)
    checked = _validate_command(command)
    _validate_stage_decision(stage, checked.decision)
    checked_key = _require_idempotency_key(idempotency_key)
    checked_request_id = _require_request_id(request_id)
    key_hash = _storage_hash(stage, checked_key)

    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("opening-review-idempotency", key_hash),
            _advisory_coordinate("opening-review-task", str(checked.task_id)),
            _advisory_coordinate("opening-review-round", str(checked.round_id)),
            _advisory_coordinate("opening-review-stage", f"{checked.task_id}:{stage}"),
        ),
    )

    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked.task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        _fail("opening_review_task_not_found", "not_found", "期初盘点任务不存在")

    # Every formal task writer takes the task row before one complete task
    # principal graph.  Historical disposition/review/recount authorization is
    # replayed after reference locks, so all of those user coordinates must be
    # discovered and locked here rather than first locking their assignments
    # from nested replay.
    principal_user_ids = observation_disposition_service._task_principal_user_ids(
        db,
        task_id=task.id,
        supplied_user_ids=(supplied_actor.user_id,),
    )
    lock_formal_principal_graph(db, tuple(sorted(principal_user_ids)))
    round_row = db.scalar(
        select(StocktakeRound)
        .where(
            StocktakeRound.id == checked.round_id,
            StocktakeRound.task_id == checked.task_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if round_row is None:
        _fail("opening_review_round_not_found", "not_found", "期初盘点轮次不存在")

    # PostgreSQL formal roles have SELECT-only access to immutable task-local
    # evidence.  The owner helper locks that graph in one canonical order;
    # SQLite intentionally needs no corresponding lock operation.
    lock_opening_stocktake_task_evidence(db, task.id, round_row.id)

    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task.id)
            .order_by(FormalStocktakeScope.scope_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    observation_graph = _lock_observation_review_graph(
        db,
        task_id=task.id,
        round_id=round_row.id,
    )
    disposition_resolutions = _lock_review_reference_graph(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        graph=observation_graph,
    )
    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id == task.id)
            .order_by(InventoryFreeze.stocktake_scope_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    locations = _lock_and_validate_scope_masters(db, task, scopes)
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
    existing_reviews = tuple(
        db.scalars(
            select(StocktakeReview)
            .where(
                StocktakeReview.task_id == task.id,
                StocktakeReview.round_id == round_row.id,
            )
            .order_by(StocktakeReview.review_stage)
            .execution_options(populate_existing=True)
        ).all()
    )
    global_review_by_key = db.scalar(
        select(StocktakeReview)
        .where(StocktakeReview.idempotency_key_hash == key_hash)
        .execution_options(populate_existing=True)
    )
    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    assignment, grant = _authorize_reviewer(
        db,
        actor=current_actor,
        stage=stage,
        task=task,
        scopes=scopes,
        locations=locations,
        now=now,
    )

    evidence_reviewed_at = (
        _as_utc(global_review_by_key.reviewed_at)
        if global_review_by_key is not None
        and global_review_by_key.task_id == task.id
        and global_review_by_key.round_id == round_row.id
        and global_review_by_key.review_stage == stage
        else now
    )
    observation_evidence = _validate_observation_review_graph(
        db,
        task=task,
        round_row=round_row,
        differences=differences,
        graph=observation_graph,
        reviewed_at=evidence_reviewed_at,
        disposition_resolutions=disposition_resolutions,
    )
    expected_decisions = _validate_review_items(
        checked,
        differences,
        round_row=round_row,
        stage=stage,
        existing_reviews=existing_reviews,
        observation_evidence=observation_evidence.outcomes,
    )
    try:
        decision_manifest = canonical_opening_decision_manifest_sha256(
            task_id=task.id,
            round_id=round_row.id,
            differences=differences,
            decisions=expected_decisions,
        )
    except (TypeError, ValueError) as exc:
        _fail(
            "opening_review_manifest_invalid",
            "precondition_failed",
            "期初盘点复核清单无法规范化",
            cause=exc,
        )

    existing_by_key = global_review_by_key
    if existing_by_key is not None:
        if (
            existing_by_key.task_id != task.id
            or existing_by_key.round_id != round_row.id
            or existing_by_key.review_stage != stage
        ):
            _fail(
                "opening_review_idempotency_conflict",
                "conflict",
                "幂等键已绑定其他期初盘点复核请求",
            )
        successor_recount_plan = _validate_replay(
            db,
            task=task,
            round_row=round_row,
            review=existing_by_key,
            actor=current_actor,
            command=checked,
            stage=stage,
            differences=differences,
            decision_manifest=decision_manifest,
            disposition_resolutions=disposition_resolutions,
        )
        current_recount_plan = _plan_count_and_difference_evidence(
            db,
            task=task,
            round_row=round_row,
            scopes=scopes,
            freezes=freezes,
            allow_downstream=True,
            disposition_resolutions=disposition_resolutions,
        )
        review_plan = _plan_opening_review_evidence_from_prelocked_reference_graph(
            db,
            task_id=task.id,
            round_id=round_row.id,
            review_id=existing_by_key.id,
            expected_stage=stage,
            expected_decision=existing_by_key.decision,
            disposition_resolutions=disposition_resolutions,
        )
        _head, audit_proof = _lock_audit_chain_head_with_proof(
            db,
            stream_key=INVENTORY_STREAM_KEY,
        )
        _validate_recount_plans_after_audit(
            db,
            plans=(current_recount_plan, successor_recount_plan),
            audit_proof=audit_proof,
        )
        _validate_opening_review_evidence_from_prelocked_task_graph(
            db,
            plan=review_plan,
            audit_proof=audit_proof,
        )
        return replace(
            _result(existing_by_key, differences),
            replayed=True,
        )

    if any(row.review_stage == stage for row in existing_reviews):
        _fail(
            "opening_review_stage_already_completed",
            "conflict",
            "该层级复核已经形成不可变事实",
        )
    _validate_new_stage_state(task, round_row, stage, existing_reviews)
    recount_plan = _plan_count_and_difference_evidence(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        allow_downstream=False,
        disposition_resolutions=disposition_resolutions,
    )
    _require_no_unpostable_approval(
        checked,
        differences,
        round_row=round_row,
        observation_evidence=observation_evidence.outcomes,
    )
    regional_review = next(
        (row for row in existing_reviews if row.review_stage == REGION_STAGE),
        None,
    )
    if stage == HEADQUARTERS_STAGE:
        if regional_review is None or regional_review.decision != "approve":
            _fail(
                "opening_review_region_approval_required",
                "precondition_failed",
                "总部复核必须基于更早的区域通过事实",
            )
        if (
            regional_review.reviewer_user_id == current_actor.user_id
            or regional_review.reviewer_person_id == current_actor.person_id
        ):
            _fail(
                "opening_review_separation_of_duties_required",
                "forbidden",
                "区域复核与总部复核必须由不同人员完成",
            )
        if now <= _as_utc(regional_review.reviewed_at):
            _fail(
                "opening_review_clock_not_monotonic",
                "service_unavailable",
                "数据库时间未晚于区域复核时间，禁止伪造总部复核顺序",
            )
        if checked.decision == "approve":
            regional_items = {
                row.difference_id: row.decision
                for row in db.scalars(
                    select(StocktakeReviewItem)
                    .where(StocktakeReviewItem.review_id == regional_review.id)
                    .order_by(StocktakeReviewItem.difference_id)
                ).all()
            }
            if regional_items != expected_decisions:
                _fail(
                    "opening_review_headquarters_decision_mismatch",
                    "precondition_failed",
                    "总部通过必须逐项确认与区域复核相同的不可变决策",
                )

    _head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    _validate_recount_plans_after_audit(
        db,
        plans=(recount_plan,),
        audit_proof=audit_proof,
    )
    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    assignment, grant = _authorize_reviewer(
        db,
        actor=current_actor,
        stage=stage,
        task=task,
        scopes=scopes,
        locations=locations,
        now=now,
        lock_rows=False,
    )
    if stage == HEADQUARTERS_STAGE and regional_review is not None:
        if (
            regional_review.reviewer_user_id == current_actor.user_id
            or regional_review.reviewer_person_id == current_actor.person_id
            or now <= _as_utc(regional_review.reviewed_at)
        ):
            _fail(
                "opening_review_separation_of_duties_required",
                "forbidden",
                "区域与总部复核的人员及时间顺序不满足职责分离",
            )
    _validate_new_stage_state(task, round_row, stage, existing_reviews)

    # Re-prove every observation/disposition anchor after the final audit-head
    # lock and database-clock sample.  The task/round rows stay locked for the
    # full transaction, while this second pass prevents a waited-on review
    # from using evidence that is not yet valid at its persisted review time.
    observation_evidence = _validate_observation_review_graph(
        db,
        task=task,
        round_row=round_row,
        differences=differences,
        graph=observation_graph,
        reviewed_at=now,
        disposition_resolutions=disposition_resolutions,
        expected_disposition_replay_plans=dict(
            observation_evidence.disposition_replay_plans
        ),
        audit_proof=audit_proof,
    )
    final_expected_decisions = _validate_review_items(
        checked,
        differences,
        round_row=round_row,
        stage=stage,
        existing_reviews=existing_reviews,
        observation_evidence=observation_evidence.outcomes,
    )
    if final_expected_decisions != expected_decisions:
        _fail(
            "opening_review_observation_disposition_evidence_invalid",
            "service_unavailable",
            "期初盘点观察处置证据在复核锁定期间发生变化",
        )

    return _write_review(
        db,
        task=task,
        round_row=round_row,
        actor=current_actor,
        assignment=assignment,
        grant=grant,
        command=checked,
        stage=stage,
        key_hash=key_hash,
        decision_manifest=decision_manifest,
        differences=differences,
        request_reference=_request_reference(checked_request_id),
        now=now,
    )


def _write_review(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    grant: ScopeGrant,
    command: SubmitOpeningStocktakeReviewCommand,
    stage: str,
    key_hash: str,
    decision_manifest: str,
    differences: Sequence[StocktakeDifference],
    request_reference: str,
    now: datetime,
) -> OpeningStocktakeReviewResult:
    del grant  # The exact assignment is persisted and revalidated by the posting guard.
    review = StocktakeReview(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        review_stage=stage,
        reviewer_user_id=actor.user_id,
        reviewer_person_id=actor.person_id,
        reviewer_role_assignment_id=assignment.id,
        authorization_version=actor.authorization_version,
        decision=command.decision,
        comment=command.comment,
        decision_manifest_sha256=decision_manifest,
        idempotency_key_hash=key_hash,
        reviewed_at=now,
        created_at=now,
    )
    db.add(review)
    db.flush()

    input_by_id = {row.difference_id: row for row in command.items}
    for difference in differences:
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

    previous_status = task.status
    resulting_status = _resulting_status(stage, command.decision)
    task.status = resulting_status
    task.version += 1
    task.updated_at = now
    reason = f"opening_{stage}_review_{command.decision}"
    metadata = {
        "decision": command.decision,
        "decision_manifest_sha256": decision_manifest,
        "pending_control_count": sum(
            1 for row in differences if row.difference_type == "control_unassigned"
        ),
        "review_id": str(review.id),
        "round_id": str(round_row.id),
        "stage": stage,
    }
    db.add(
        StateTransitionEvent(
            aggregate_type="stocktake_task",
            aggregate_id=str(task.id),
            from_status=previous_status,
            to_status=resulting_status,
            reason=reason,
            actor_id=actor.user_id,
            idempotency_key=_event_key("state", review.id),
            occurred_at=now,
            metadata_jsonb=metadata,
            created_at=now,
        )
    )
    db.add(
        OutboxEvent(
            event_type=f"stocktake.opening.{stage}_reviewed",
            aggregate_type="stocktake_task",
            aggregate_id=str(task.id),
            payload_jsonb=metadata,
            status="pending",
            attempts=0,
            idempotency_key=_event_key("outbox", review.id),
            available_at=now,
            locked_at=None,
            locked_by=None,
            published_at=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
    )
    db.flush()
    append_audit_event(
        db,
        stream_key=INVENTORY_STREAM_KEY,
        actor_user_id=actor.user_id,
        action=f"stocktake.opening.{stage}_reviewed",
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
        request_id=request_reference,
        occurred_at=now,
        created_at=now,
    )
    db.flush()
    return _result(review, differences)


def _validate_recount_plans_after_audit(
    db: Session,
    *,
    plans: Sequence[object | None],
    audit_proof: object,
) -> None:
    concrete = tuple(plan for plan in plans if plan is not None)
    if not concrete:
        return
    try:
        from .opening_stocktake_recount import (
            OpeningStocktakeRecountError,
            _validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph,
        )

        seen: set[int] = set()
        for plan in concrete:
            if id(plan) in seen:
                continue
            seen.add(id(plan))
            _validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph(
                db,
                plan=plan,
                audit_proof=audit_proof,
            )
    except (ImportError, AttributeError, OpeningStocktakeRecountError) as exc:
        _fail(
            "opening_review_recount_chain_invalid",
            "precondition_failed",
            "复盘轮次的因果、执行人或副作用证据无法从审计预锁图重证",
            cause=exc,
        )


def _plan_count_and_difference_evidence(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    freezes: Sequence[InventoryFreeze],
    allow_downstream: bool,
    disposition_resolutions: Mapping[uuid.UUID, object],
) -> object | None:
    submitted_at = _as_optional_utc(round_row.submitted_at)
    task_submitted_at = _as_optional_utc(task.submitted_at)
    freeze_by_scope = {row.stocktake_scope_id: row for row in freezes}
    posted_or_closed_replay = allow_downstream and task.status in {
        "posted",
        "closed",
    }

    def _freeze_anchor_invalid(scope: FormalStocktakeScope) -> bool:
        freeze = freeze_by_scope.get(scope.id)
        if (
            freeze is None
            or freeze.task_id != task.id
            or freeze.scope_key != scope.scope_key
        ):
            return True
        if posted_or_closed_replay:
            posted_at = _as_optional_utc(task.posted_at)
            released_at = _as_optional_utc(freeze.valid_to)
            return bool(
                freeze.status != "released"
                or posted_at is None
                or released_at is None
                or released_at < posted_at
                or freeze.released_by_user_id is None
                or not freeze.release_reason.strip()
            )
        return freeze.status != "active" or freeze.valid_to is not None

    if (
        task.task_type != "opening"
        or task.current_round_no < round_row.round_no
        or (not allow_downstream and task.current_round_no != round_row.round_no)
        or task.cutoff_at is None
        or task.cutoff_ledger_cursor is None
        or task.scope_manifest_sha256 is None
        or task.snapshot_manifest_sha256 is None
        or task.control_manifest_sha256 is None
        or round_row.round_no <= 0
        or round_row.round_type
        != ("initial" if round_row.round_no == 1 else "recount")
        or (round_row.round_no == 1 and round_row.recount_case_id is not None)
        or (round_row.round_no > 1 and round_row.recount_case_id is None)
        or round_row.status != "submitted"
        or submitted_at is None
        or (
            task.current_round_no == round_row.round_no
            and task_submitted_at != submitted_at
        )
        or round_row.count_manifest_sha256 is None
        or not _SHA256.fullmatch(round_row.count_manifest_sha256)
        or not scopes
        or [row.scope_no for row in scopes] != list(range(1, len(scopes) + 1))
        or len(freezes) != len(scopes)
        or len(freeze_by_scope) != len(scopes)
        or any(_freeze_anchor_invalid(scope) for scope in scopes)
        or (not allow_downstream and (task.posted_at is not None or task.closed_at is not None))
    ):
        _fail(
            "opening_review_submission_anchor_invalid",
            "precondition_failed",
            "期初盘点提交、轮次或冻结锚点不完整",
        )

    recount_assignments: dict[
        uuid.UUID, StocktakeRecountScopeAssignment
    ] = {}
    recount_plan: object | None = None
    if round_row.round_no > 1:
        # Local import avoids the review -> posting -> recount initialization
        # cycle while keeping one authoritative recount-chain validator.
        from .opening_stocktake_recount import (
            OpeningStocktakeRecountError,
            _opening_recount_assignments_from_plan,
            _plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph,
        )

        try:
            recount_plan = (
                _plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph(
                    db,
                    task=task,
                    round_row=round_row,
                    scopes=scopes,
                    freezes=freezes,
                    disposition_resolutions=disposition_resolutions,
                )
            )
            recount_assignments = _opening_recount_assignments_from_plan(
                db,
                plan=recount_plan,
            )
        except OpeningStocktakeRecountError as exc:
            _fail(
                "opening_review_recount_chain_invalid",
                "precondition_failed",
                "复盘轮次的因果、执行人或副作用证据无法重证",
                cause=exc,
            )

    postings = db.scalars(
        select(StocktakePosting).where(
            StocktakePosting.task_id == task.id,
            StocktakePosting.round_id == round_row.id,
        )
    ).all()
    establishments = db.scalars(
        select(InventoryOpeningEstablishment).where(
            InventoryOpeningEstablishment.task_id == task.id,
            InventoryOpeningEstablishment.round_id == round_row.id,
        )
    ).all()
    if not posted_or_closed_replay and (postings or establishments):
        _fail(
            "opening_review_already_posted",
            "conflict",
            "期初盘点已存在过账或建立事实，禁止补写复核",
        )

    completions = tuple(
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
    completion_by_scope = {row.scope_id: row for row in completions}
    if len(completions) != len(scopes) or set(completion_by_scope) != {
        row.id for row in scopes
    }:
        _fail(
            "opening_review_scope_completion_invalid",
            "precondition_failed",
            "期初盘点并非每个范围都有唯一封印完成证据",
        )
    try:
        for scope in scopes:
            _validate_completion_evidence(
                db,
                task,
                round_row,
                scope,
                completion_by_scope[scope.id],
                expected_recount_assignment=recount_assignments.get(scope.id),
            )
    except OpeningStocktakeCountError as exc:
        _fail(
            "opening_review_count_evidence_invalid",
            "precondition_failed",
            "期初盘点实盘完成证据无法重算",
            cause=exc,
        )
    submission_rows = tuple(
        db.scalars(
            select(StocktakeRoundSubmission)
            .where(
                StocktakeRoundSubmission.task_id == task.id,
                StocktakeRoundSubmission.round_id == round_row.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(submission_rows) != 1:
        _fail(
            "opening_review_round_submission_invalid",
            "precondition_failed",
            "期初盘点轮次提交封印缺失或重复",
        )
    submission = submission_rows[0]
    expected_round_manifest = _round_manifest_sha256(
        task.id,
        round_row.id,
        completions,
        submission.sealing_completion_id,
    )
    if (
        submission.sealing_completion_id not in {row.id for row in completions}
        or submission.scope_count != len(completions)
        or submission.zero_scope_count
        != sum(1 for row in completions if row.zero_confirmed)
        or submission.count_line_count
        != sum(row.count_line_count for row in completions)
        or submission.observation_line_count
        != sum(row.observation_line_count for row in completions)
        or submission.serial_count != sum(row.serial_count for row in completions)
        or submission.total_counted_qty
        != sum((row.total_counted_qty for row in completions), start=_ZERO)
        or submission.round_manifest_sha256 != expected_round_manifest
        or _as_utc(submission.submitted_at) != submitted_at
        or _as_utc(submission.created_at) != submitted_at
    ):
        _fail(
            "opening_review_round_submission_invalid",
            "precondition_failed",
            "期初盘点轮次提交汇总与范围证据不一致",
        )

    count_lines = tuple(
        db.scalars(
            select(StocktakeCountLine)
            .where(
                StocktakeCountLine.task_id == task.id,
                StocktakeCountLine.round_id == round_row.id,
            )
            .order_by(StocktakeCountLine.stock_account_id, StocktakeCountLine.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    line_ids = tuple(row.id for row in count_lines)
    count_serials = tuple(
        db.scalars(
            select(StocktakeCountSerial)
            .where(StocktakeCountSerial.count_line_id.in_(line_ids))
            .order_by(StocktakeCountSerial.count_line_id, StocktakeCountSerial.serial_id)
            .execution_options(populate_existing=True)
        ).all()
        if line_ids
        else ()
    )
    tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.round_id == round_row.id,
            )
            .order_by(StocktakeCountObservation.observation_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    try:
        expected_manifest = canonical_opening_count_manifest_sha256(
            task,
            round_row,
            count_lines,
            count_serials,
        )
    except (TypeError, ValueError) as exc:
        _fail(
            "opening_review_count_manifest_invalid",
            "precondition_failed",
            "期初盘点实盘清单无法规范化",
            cause=exc,
        )
    if (
        round_row.count_manifest_sha256 != expected_manifest
        or submission.count_manifest_sha256 != expected_manifest
        or submission.request_sha256
        != _count_hash_document(
            {
                "count_manifest_sha256": expected_manifest,
                "round_id": str(round_row.id),
                "round_manifest_sha256": expected_round_manifest,
                "schema": "cloud_oam.opening_stocktake.round_submission_request.v3",
                "sealing_completion_id": str(
                    submission.sealing_completion_id
                ),
            }
        )
        or submission.idempotency_key_hash
        != _count_event_hash("round-submission", round_row.id, task.id)
    ):
        _fail(
            "opening_review_count_manifest_mismatch",
            "precondition_failed",
            "期初盘点实盘清单与最终过账 guard 口径不一致",
        )
    try:
        _validate_initial_difference_set(db, task, round_row)
    except OpeningStocktakeCountError as exc:
        _fail(
            "opening_review_difference_evidence_invalid",
            "precondition_failed",
            "期初盘点差异集合无法从实盘与 OAM 控制证据重算",
            cause=exc,
        )
    difference_completion_rows = tuple(
        db.scalars(
            select(StocktakeDifferenceSetCompletion)
            .where(
                StocktakeDifferenceSetCompletion.task_id == task.id,
                StocktakeDifferenceSetCompletion.round_id == round_row.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(difference_completion_rows) != 1:
        _fail(
            "opening_review_difference_completion_invalid",
            "precondition_failed",
            "期初盘点差异集合尚未形成唯一完成封印",
        )
    try:
        _validate_difference_set_completion(
            db,
            task=task,
            round_row=round_row,
            submission=submission,
            completion=difference_completion_rows[0],
        )
    except OpeningStocktakeCountError as exc:
        _fail(
            "opening_review_difference_completion_invalid",
            "precondition_failed",
            "期初盘点差异集合完成封印无法重算",
            cause=exc,
        )
    return recount_plan


def _validate_new_stage_state(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    stage: str,
    existing_reviews: Sequence[StocktakeReview],
) -> None:
    expected_status = "submitted" if stage == REGION_STAGE else "hq_review"
    if round_row.status != "submitted" or task.status != expected_status:
        _fail(
            "opening_review_stage_state_invalid",
            "precondition_failed",
            "期初盘点当前状态不允许执行该层级复核",
        )
    if stage == REGION_STAGE and existing_reviews:
        _fail(
            "opening_review_order_invalid",
            "conflict",
            "区域复核必须是该轮次第一个复核事实",
        )
    if stage == HEADQUARTERS_STAGE:
        region_rows = [row for row in existing_reviews if row.review_stage == REGION_STAGE]
        if len(region_rows) != 1 or region_rows[0].decision != "approve":
            _fail(
                "opening_review_region_approval_required",
                "precondition_failed",
                "总部复核必须基于唯一且已通过的区域复核",
            )


def _lock_observation_review_graph(
    db: Session,
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
) -> _ObservationReviewGraph:
    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == task_id,
                StocktakeCountObservation.round_id == round_id,
            )
            .order_by(StocktakeCountObservation.observation_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    dispositions = tuple(
        db.scalars(
            select(StocktakeObservationDisposition)
            .where(
                StocktakeObservationDisposition.task_id == task_id,
                StocktakeObservationDisposition.round_id == round_id,
            )
            .order_by(StocktakeObservationDisposition.observation_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    observations_by_id = {row.id: row for row in observations}
    dispositions_by_observation_id = {
        row.observation_id: row for row in dispositions
    }
    if (
        len(observations_by_id) != len(observations)
        or len(dispositions_by_observation_id) != len(dispositions)
    ):
        _fail(
            "opening_review_observation_disposition_evidence_invalid",
            "service_unavailable",
            "期初盘点观察或处置证据存在重复主键",
        )
    return _ObservationReviewGraph(
        observations_by_id=observations_by_id,
        dispositions_by_observation_id=dispositions_by_observation_id,
    )


def _lock_review_reference_graph(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    graph: _ObservationReviewGraph,
) -> dict[uuid.UUID, object]:
    for observation_id, disposition in graph.dispositions_by_observation_id.items():
        observation = graph.observations_by_id.get(observation_id)
        if (
            observation is None
            or disposition.task_id != task.id
            or disposition.round_id != round_row.id
            or disposition.observation_id != observation.id
        ):
            _fail(
                "opening_review_observation_disposition_evidence_invalid",
                "service_unavailable",
                "现场观察处置缺少对应观察证据",
            )
    try:
        return observation_disposition_service.lock_and_prove_opening_task_observation_resolutions(
            db,
            task=task,
            scopes=scopes,
        )
    except observation_disposition_service.OpeningObservationDispositionError as exc:
        _fail(
            "opening_review_observation_disposition_evidence_invalid",
            "service_unavailable",
            "现场观察处置的主数据引用图无法安全重证",
            cause=exc,
        )


def _validate_observation_review_graph(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    differences: Sequence[StocktakeDifference],
    graph: _ObservationReviewGraph,
    reviewed_at: datetime,
    disposition_resolutions: Mapping[uuid.UUID, object],
    expected_disposition_replay_plans: Mapping[uuid.UUID, object] | None = None,
    audit_proof: object | None = None,
) -> _ObservationReviewEvidence:
    """Plan dispositions pre-audit or validate them with one caller proof."""

    if (expected_disposition_replay_plans is None) != (audit_proof is None):
        _fail(
            "opening_review_observation_disposition_evidence_invalid",
            "service_unavailable",
            "现场观察处置审计计划与预锁证明必须同时提供",
        )

    review_time = _as_utc(reviewed_at)
    round_submitted_at = _as_optional_utc(round_row.submitted_at)
    if round_submitted_at is None:
        _fail(
            "opening_review_observation_disposition_evidence_invalid",
            "precondition_failed",
            "期初盘点观察处置缺少轮次提交时间锚点",
        )

    differences_by_observation: dict[uuid.UUID, StocktakeDifference] = {}
    for difference in differences:
        if difference.observed_line_id is None:
            continue
        if difference.observed_line_id in differences_by_observation:
            _fail(
                "opening_review_observation_disposition_evidence_invalid",
                "service_unavailable",
                "同一现场观察绑定了多个差异事实",
            )
        differences_by_observation[difference.observed_line_id] = difference
    if set(differences_by_observation) != set(graph.observations_by_id):
        _fail(
            "opening_review_observation_disposition_evidence_invalid",
            "service_unavailable",
            "现场观察与封印差异集合不能一一对应",
        )

    pending_ids = {
        row.id
        for row in graph.observations_by_id.values()
        if row.verification_status == "pending_verification"
    }
    if set(graph.dispositions_by_observation_id) != pending_ids:
        if not pending_ids.issubset(graph.dispositions_by_observation_id):
            _fail(
                "opening_review_observation_disposition_required",
                "precondition_failed",
                "每条待核实现场观察必须先形成唯一规范处置事实",
            )
        _fail(
            "opening_review_observation_disposition_evidence_invalid",
            "service_unavailable",
            "已核实现场观察不得夹带待核实处置事实",
        )

    submission_rows = tuple(
        db.scalars(
            select(StocktakeRoundSubmission)
            .where(
                StocktakeRoundSubmission.task_id == task.id,
                StocktakeRoundSubmission.round_id == round_row.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    difference_completion_rows = tuple(
        db.scalars(
            select(StocktakeDifferenceSetCompletion)
            .where(
                StocktakeDifferenceSetCompletion.task_id == task.id,
                StocktakeDifferenceSetCompletion.round_id == round_row.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(submission_rows) != 1 or len(difference_completion_rows) != 1:
        _fail(
            "opening_review_observation_disposition_evidence_invalid",
            "precondition_failed",
            "期初盘点观察处置缺少唯一轮次或差异完成封印",
        )
    submission = submission_rows[0]
    difference_completion = difference_completion_rows[0]
    completion_time = _as_utc(difference_completion.completed_at)

    outcomes: dict[uuid.UUID, str] = {}
    replay_plans: dict[uuid.UUID, object] = {}
    for observation_id, observation in graph.observations_by_id.items():
        difference = differences_by_observation[observation_id]
        expected_reason = (
            "opening_pending_verification"
            if observation.verification_status == "pending_verification"
            else "opening_unexpected_dimension"
        )
        if (
            observation.verification_status
            not in {"verified", "pending_verification"}
            or observation.task_id != task.id
            or observation.round_id != round_row.id
            or difference.task_id != task.id
            or difference.round_id != round_row.id
            or difference.scope_id != observation.scope_id
            or difference.difference_type != "excess"
            or difference.control_snapshot_line_id is not None
            or difference.expected_account_id is not None
            or difference.observed_account_id is not None
            or difference.observed_line_id != observation.id
            or difference.material_id != observation.material_id
            or difference.serial_id != observation.serial_id
            or difference.book_qty != _ZERO
            or difference.counted_qty != observation.counted_qty
            or difference.difference_qty != observation.counted_qty
            or difference.affected_qty != observation.counted_qty
            or difference.reason_code != expected_reason
            or not difference.evidence_required
        ):
            _fail(
                "opening_review_observation_disposition_evidence_invalid",
                "service_unavailable",
                "现场观察与差异事实锚点不完整或相互矛盾",
            )

        if observation.verification_status == "verified":
            outcomes[observation_id] = "verified_no_cutoff_account"
            continue

        disposition = graph.dispositions_by_observation_id[observation_id]
        disposition_time = _as_utc(disposition.decided_at)
        difference_time = _as_utc(difference.created_at)
        if (
            disposition.task_id != task.id
            or disposition.round_id != round_row.id
            or disposition.scope_id != observation.scope_id
            or disposition.observation_id != observation.id
            or disposition.disposition
            not in {
                "pending_verification",
                "requires_recount",
                "resolved_existing_master",
            }
            or difference_time > disposition_time
            or round_submitted_at > disposition_time
            or completion_time > disposition_time
            or disposition_time > review_time
        ):
            _fail(
                "opening_review_observation_disposition_evidence_invalid",
                "precondition_failed",
                "现场观察处置的任务、轮次或时间顺序无效",
            )

        historical_actor = FormalPrincipal(
            user_id=disposition.decided_by_user_id,
            person_id=disposition.decided_by_person_id,
            account_status="active",
            employment_status="active",
            authorization_version=disposition.authorization_version,
            access_mode="active",
            assignments=(),
            entitlements=(),
        )
        disposition_command = (
            observation_disposition_service.RecordOpeningObservationDispositionCommand(
                task_id=task.id,
                round_id=round_row.id,
                observation_id=observation.id,
                disposition=disposition.disposition,
                reason_code=disposition.reason_code,
                comment=disposition.comment,
                resolved_material_id=disposition.resolved_material_id,
                resolved_lot_id=disposition.resolved_lot_id,
                resolved_serial_id=disposition.resolved_serial_id,
            )
        )
        try:
            expected_request_sha256 = observation_disposition_service._request_sha256(
                historical_actor,
                disposition_command,
            )
            replay_plan = observation_disposition_service._plan_opening_observation_disposition_replay(
                db,
                actor=historical_actor,
                command=disposition_command,
                row=disposition,
                task=task,
                observation=observation,
                difference=difference,
                submission=submission,
                difference_completion=difference_completion,
                key_hash=disposition.idempotency_key_hash,
                request_sha256=expected_request_sha256,
                resolution=disposition_resolutions.get(observation_id),
            )
            if expected_disposition_replay_plans is not None:
                expected_plan = expected_disposition_replay_plans.get(
                    observation_id
                )
                if expected_plan != replay_plan:
                    _fail(
                        "opening_review_observation_disposition_evidence_invalid",
                        "service_unavailable",
                        "现场观察处置结构在审计锁定期间发生变化",
                    )
                observation_disposition_service._validate_opening_observation_disposition_replay_from_prelocked_audit_graph(
                    db,
                    plan=expected_plan,
                    audit_proof=audit_proof,
                )
            replay_plans[observation_id] = replay_plan
        except observation_disposition_service.OpeningObservationDispositionError as exc:
            _fail(
                "opening_review_observation_disposition_evidence_invalid",
                "service_unavailable",
                "现场观察处置清单或唯一审计证据无法重算",
                cause=exc,
            )
        outcomes[observation_id] = disposition.disposition
    if (
        expected_disposition_replay_plans is not None
        and set(expected_disposition_replay_plans) != set(replay_plans)
    ):
        _fail(
            "opening_review_observation_disposition_evidence_invalid",
            "service_unavailable",
            "现场观察处置审计计划集合发生变化",
        )
    return _ObservationReviewEvidence(
        outcomes=outcomes,
        disposition_replay_plans=tuple(
            sorted(replay_plans.items(), key=lambda item: str(item[0]))
        ),
    )


def _validate_review_items(
    command: SubmitOpeningStocktakeReviewCommand,
    differences: Sequence[StocktakeDifference],
    *,
    round_row: StocktakeRound,
    stage: str,
    existing_reviews: Sequence[StocktakeReview],
    observation_evidence: Mapping[uuid.UUID, str],
) -> dict[uuid.UUID, str]:
    del existing_reviews
    _validate_stage_decision(stage, command.decision)
    by_id = {row.difference_id: row for row in command.items}
    difference_by_id = {row.id: row for row in differences}
    if set(by_id) != set(difference_by_id):
        _fail(
            "opening_review_items_incomplete",
            "invalid_request",
            "复核必须逐项覆盖该轮次全部差异且不得夹带其他差异",
        )
    expected: dict[uuid.UUID, str] = {}
    for difference in differences:
        value = by_id[difference.id]
        if difference.difference_type == "control_unassigned":
            required = "pending_verification"
            if not value.comment.strip():
                _fail(
                    "opening_review_control_pending_comment_required",
                    "invalid_request",
                    "OAM 控制差异必须明确保留待核实说明",
                )
        elif difference.observed_line_id is not None:
            outcome = observation_evidence.get(difference.observed_line_id)
            if outcome is None:
                _fail(
                    "opening_review_observation_disposition_evidence_invalid",
                    "service_unavailable",
                    "现场观察缺少已复核的处置证据",
                )
            terminal_recount_observation = bool(
                round_row.round_no > 1
                and round_row.round_type == "recount"
                and outcome == "verified_no_cutoff_account"
            )
            if stage != REGION_STAGE and not terminal_recount_observation:
                _fail(
                    "opening_review_observation_headquarters_forbidden",
                    "precondition_failed",
                    "含现场观察的轮次必须先受控复盘，不得进入总部通过复核",
                )
            if terminal_recount_observation:
                required = {
                    "approve": "accept_for_posting",
                    "recount": "recount",
                    "reject": "reject",
                }[command.decision]
            elif outcome == "pending_verification":
                if command.decision not in {"recount", "reject"}:
                    _fail(
                        "opening_review_observation_disposition_incompatible",
                        "precondition_failed",
                        "待核实现场观察只能保留待核实并进入复盘或驳回本轮",
                    )
                required = "pending_verification"
                if not value.comment.strip():
                    _fail(
                        "opening_review_observation_pending_comment_required",
                        "invalid_request",
                        "待核实现场观察必须明确保留待核实说明",
                    )
            elif outcome in {
                "requires_recount",
                "resolved_existing_master",
                "verified_no_cutoff_account",
            }:
                if command.decision != "recount":
                    _fail(
                        "opening_review_observation_disposition_incompatible",
                        "precondition_failed",
                        "已处置或无截止账户的现场观察必须先受控复盘",
                    )
                required = "recount"
            else:
                _fail(
                    "opening_review_observation_disposition_evidence_invalid",
                    "service_unavailable",
                    "现场观察处置结论不受支持",
                )
        else:
            required = {
                "approve": "accept_for_posting",
                "recount": "recount",
                "reject": "reject",
            }[command.decision]
        if value.decision != required:
            _fail(
                "opening_review_item_decision_invalid",
                "invalid_request",
                "逐项复核决策与差异类型或复核结论不一致",
            )
        expected[difference.id] = value.decision
    return expected


def _require_no_unpostable_approval(
    command: SubmitOpeningStocktakeReviewCommand,
    differences: Sequence[StocktakeDifference],
    *,
    round_row: StocktakeRound,
    observation_evidence: Mapping[uuid.UUID, str],
) -> None:
    if command.decision != "approve":
        return
    observed_ids = {
        row.observed_line_id
        for row in differences
        if row.observed_line_id is not None
    }
    if not observed_ids:
        return
    if (
        round_row.round_no <= 1
        or round_row.round_type != "recount"
        or observed_ids != set(observation_evidence)
        or any(
            observation_evidence.get(observation_id)
            != "verified_no_cutoff_account"
            for observation_id in observed_ids
        )
    ):
        _fail(
            "opening_review_unresolved_observation_cannot_approve",
            "precondition_failed",
            "现场观察必须经过受控复盘并在当前复盘轮次唯一解析后才能通过",
        )


def _authorize_reviewer(
    db: Session,
    *,
    actor: FormalPrincipal,
    stage: str,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    locations: Mapping[uuid.UUID, StockLocation],
    now: datetime,
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
    grants = [
        row
        for row in actor.assignments
        if row.role_code == role_code
        and row.scope_type == scope_type
        and row.scope_id == scope_id
    ]
    if len(grants) != 1:
        _fail(
            "opening_review_stage_forbidden",
            "forbidden",
            "当前人员没有唯一有效的该层级期初盘点复核授权",
        )
    grant = grants[0]
    selected_actor = replace(
        actor,
        assignments=(grant,),
        entitlements=tuple(
            row
            for row in actor.entitlements
            if row.assignment_id == grant.assignment_id
        ),
    )

    def _selected_grant_allows(
        target_type: str,
        target_id: str,
    ) -> bool:
        return actor.allows(
            db,
            "stocktake",
            action,
            target_scope_type=target_type,
            target_scope_id=target_id,
        ) and selected_actor.allows(
            db,
            "stocktake",
            action,
            target_scope_type=target_type,
            target_scope_id=target_id,
        )

    try:
        allowed = _selected_grant_allows(target_scope_type, target_scope_id)
    except FormalAccessError as exc:
        _fail(
            "opening_review_authorization_invalid",
            "forbidden",
            "期初盘点复核权限图无效",
            cause=exc,
        )
    if not allowed:
        _fail(
            "opening_review_stage_forbidden",
            "forbidden",
            "当前人员没有该层级期初盘点复核权限",
        )
    if stage == REGION_STAGE:
        for scope in scopes:
            location = locations[scope.location_id]
            for organization_id in dict.fromkeys(
                (scope.owner_org_id, location.owner_org_id)
            ):
                try:
                    dimension_allowed = _selected_grant_allows(
                        "organization",
                        str(organization_id),
                    )
                except FormalAccessError as exc:
                    _fail(
                        "opening_review_scope_authorization_invalid",
                        "forbidden",
                        "区域复核的资产与物理范围授权无效",
                        cause=exc,
                    )
                if not dimension_allowed:
                    _fail(
                        "opening_review_scope_forbidden",
                        "forbidden",
                        "区域负责人未同时覆盖资产所有组织与库位物理组织",
                    )
    assignment_statement = select(RoleAssignment).where(
        RoleAssignment.id == grant.assignment_id
    )
    if lock_rows:
        assignment_statement = assignment_statement.with_for_update()
    assignment = db.scalar(
        assignment_statement.execution_options(populate_existing=True)
    )
    if (
        assignment is None
        or assignment.user_id != actor.user_id
        or assignment.scope_type != scope_type
        or assignment.scope_id != scope_id
        or assignment.status not in {"scheduled", "active"}
        or _as_utc(assignment.valid_from) > now
        or (assignment.valid_to is not None and now >= _as_utc(assignment.valid_to))
        or (assignment.revoked_at is not None and now >= _as_utc(assignment.revoked_at))
    ):
        _fail(
            "opening_review_assignment_not_current",
            "forbidden",
            "期初盘点复核角色授权已失效",
        )
    role = db.get(Role, assignment.role_id)
    if role is None or role.code != role_code or role.status != "active" or role.is_external:
        _fail(
            "opening_review_role_invalid",
            "forbidden",
            "期初盘点复核角色定义无效",
        )
    return assignment, grant


def _lock_and_validate_scope_masters(
    db: Session,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
) -> dict[uuid.UUID, StockLocation]:
    if not scopes:
        _fail("opening_review_scopes_missing", "precondition_failed", "期初盘点范围缺失")

    location_ids = {row.location_id for row in scopes}
    discovered_locations: dict[uuid.UUID, StockLocation] = {}
    pending_locations = set(location_ids)
    while pending_locations:
        current_ids = tuple(sorted(pending_locations, key=str))
        pending_locations.clear()
        rows = db.scalars(select(StockLocation).where(StockLocation.id.in_(current_ids))).all()
        for row in rows:
            discovered_locations[row.id] = row
            if row.parent_id is not None and row.parent_id not in discovered_locations:
                pending_locations.add(row.parent_id)
    location_stmt = (
        select(StockLocation)
        .where(StockLocation.id.in_(tuple(discovered_locations)))
        .order_by(StockLocation.id)
    )
    if db.get_bind().dialect.name != "postgresql":
        location_stmt = location_stmt.with_for_update()
    locked_locations = {
        row.id: row
        for row in db.scalars(
            location_stmt.execution_options(populate_existing=True)
        ).all()
    }
    if not location_ids.issubset(locked_locations):
        _fail("opening_review_location_invalid", "precondition_failed", "盘点库位缺失")

    org_ids = {task.region_org_id, *(row.owner_org_id for row in scopes)}
    for location in locked_locations.values():
        org_ids.add(location.owner_org_id)
    discovered_orgs: dict[uuid.UUID, Organization] = {}
    pending_orgs = set(org_ids)
    while pending_orgs:
        current_ids = tuple(sorted(pending_orgs, key=str))
        pending_orgs.clear()
        rows = db.scalars(select(Organization).where(Organization.id.in_(current_ids))).all()
        for row in rows:
            discovered_orgs[row.id] = row
            if row.parent_id is not None and row.parent_id not in discovered_orgs:
                pending_orgs.add(row.parent_id)
    organization_stmt = (
        select(Organization)
        .where(Organization.id.in_(tuple(discovered_orgs)))
        .order_by(Organization.id)
    )
    if db.get_bind().dialect.name != "postgresql":
        organization_stmt = organization_stmt.with_for_update()
    locked_orgs = {
        row.id: row
        for row in db.scalars(
            organization_stmt.execution_options(populate_existing=True)
        ).all()
    }
    region = locked_orgs.get(task.region_org_id)
    if region is None or region.status != "active" or region.org_type != "region_company":
        _fail(
            "opening_review_region_invalid",
            "precondition_failed",
            "期初盘点区域不是启用的区域公司",
        )

    for scope in scopes:
        owner = locked_orgs.get(scope.owner_org_id)
        location = locked_locations.get(scope.location_id)
        if (
            owner is None
            or owner.status != "active"
            or owner.org_type != "region_company"
            or location is None
            or location.status != "active"
            or location.location_type not in {"region", "personal"}
        ):
            _fail(
                "opening_review_scope_master_invalid",
                "precondition_failed",
                "盘点资产所有组织或物理库位已失效",
            )
        if not _org_descends_from(
            locked_orgs,
            owner.id,
            task.region_org_id,
        ):
            _fail(
                "opening_review_owner_outside_region",
                "precondition_failed",
                "盘点资产所有组织已不在任务区域的有效组织树内",
            )
        current_location = location
        seen_locations: set[uuid.UUID] = set()
        while True:
            if current_location.id in seen_locations:
                _fail(
                    "opening_review_location_tree_cycle",
                    "service_unavailable",
                    "库存位置树存在循环",
                )
            seen_locations.add(current_location.id)
            if current_location.status != "active" or not _org_descends_from(
                locked_orgs,
                current_location.owner_org_id,
                task.region_org_id,
            ):
                _fail(
                    "opening_review_location_outside_region",
                    "forbidden",
                    "盘点库位物理归属不在任务区域树内",
                )
            if current_location.parent_id is None:
                break
            parent = locked_locations.get(current_location.parent_id)
            if parent is None:
                _fail(
                    "opening_review_location_tree_changed",
                    "conflict",
                    "库存位置树在复核锁定期间发生变化",
                )
            current_location = parent
    return {row_id: locked_locations[row_id] for row_id in location_ids}


def _org_descends_from(
    organizations: Mapping[uuid.UUID, Organization],
    organization_id: uuid.UUID,
    ancestor_id: uuid.UUID,
) -> bool:
    current_id: uuid.UUID | None = organization_id
    seen: set[uuid.UUID] = set()
    while current_id is not None:
        if current_id in seen:
            _fail(
                "opening_review_organization_tree_cycle",
                "service_unavailable",
                "组织树存在循环",
            )
        seen.add(current_id)
        row = organizations.get(current_id)
        if row is None or row.status != "active":
            return False
        if row.id == ancestor_id:
            return True
        current_id = row.parent_id
    return False


def _validate_replay(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    review: StocktakeReview,
    actor: FormalPrincipal,
    command: SubmitOpeningStocktakeReviewCommand,
    stage: str,
    differences: Sequence[StocktakeDifference],
    decision_manifest: str,
    disposition_resolutions: Mapping[uuid.UUID, object],
) -> object | None:
    successor_recount_plan: object | None = None
    if (
        review.task_id != task.id
        or review.round_id != round_row.id
        or review.review_stage != stage
        or review.reviewer_user_id != actor.user_id
        or review.reviewer_person_id != actor.person_id
        or review.decision != command.decision
        or review.comment != command.comment
        or review.decision_manifest_sha256 != decision_manifest
    ):
        _fail(
            "opening_review_idempotency_conflict",
            "conflict",
            "幂等键已绑定不同的期初盘点复核请求",
        )
    actual_items = tuple(
        db.scalars(
            select(StocktakeReviewItem)
            .where(StocktakeReviewItem.review_id == review.id)
            .order_by(StocktakeReviewItem.difference_id)
        ).all()
    )
    expected_items = tuple(
        sorted(
            (
                (row.difference_id, row.decision, row.comment)
                for row in command.items
            ),
            key=lambda row: str(row[0]),
        )
    )
    if tuple(
        (row.difference_id, row.decision, row.comment) for row in actual_items
    ) != expected_items or len(actual_items) != len(differences):
        _fail(
            "opening_review_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点复核幂等明细不完整",
        )
    downstream_reviews = tuple(
        db.scalars(
            select(StocktakeReview)
            .where(
                StocktakeReview.task_id == task.id,
                StocktakeReview.round_id == round_row.id,
            )
            .order_by(StocktakeReview.review_stage)
        ).all()
    )
    expected_task_status = _resulting_status(review.review_stage, review.decision)
    if review.review_stage == REGION_STAGE:
        headquarters = next(
            (
                row
                for row in downstream_reviews
                if row.review_stage == HEADQUARTERS_STAGE
            ),
            None,
        )
        if headquarters is not None:
            expected_task_status = _resulting_status(
                headquarters.review_stage,
                headquarters.decision,
            )
    if task.current_round_no < round_row.round_no:
        _fail(
            "opening_review_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点复核轮次晚于任务当前轮次",
        )
    if task.current_round_no > round_row.round_no:
        current_round = db.scalar(
            select(StocktakeRound).where(
                StocktakeRound.task_id == task.id,
                StocktakeRound.round_no == task.current_round_no,
            )
        )
        scopes = tuple(
            db.scalars(
                select(FormalStocktakeScope)
                .where(FormalStocktakeScope.task_id == task.id)
                .order_by(FormalStocktakeScope.scope_no)
            ).all()
        )
        freezes = tuple(
            db.scalars(
                select(InventoryFreeze)
                .where(InventoryFreeze.task_id == task.id)
                .order_by(InventoryFreeze.stocktake_scope_id)
            ).all()
        )
        if current_round is None:
            _fail(
                "opening_review_idempotency_record_invalid",
                "service_unavailable",
                "期初盘点复核后继轮次缺失",
            )
        try:
            from .opening_stocktake_recount import (
                OpeningStocktakeRecountError,
                _plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph,
            )

            successor_recount_plan = (
                _plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph(
                    db,
                    task=task,
                    round_row=current_round,
                    scopes=scopes,
                    freezes=freezes,
                    disposition_resolutions=disposition_resolutions,
                )
            )
        except OpeningStocktakeRecountError as exc:
            _fail(
                "opening_review_idempotency_record_invalid",
                "service_unavailable",
                "期初盘点复核的后继复盘链无法重证",
                cause=exc,
            )
    elif task.status != expected_task_status:
        if task.status not in {"posted", "closed"} or expected_task_status != "approved":
            _fail(
                "opening_review_idempotency_record_invalid",
                "service_unavailable",
                "期初盘点复核状态事实与当前任务状态不一致",
            )
    # Review idempotency owns only the immutable review/count/recount domain
    # facts already pinned above.  A posted/closed opening's ledger, balances,
    # posting, establishment and close facts are re-proved by the canonical
    # ledger-first finalize/close boundary.  Calling that public replay here
    # would reacquire task/reference owner locks after review audit locks.
    return successor_recount_plan


def _validate_historical_reviewer_authorization(
    db: Session,
    *,
    task: FormalStocktakeTask,
    review: StocktakeReview,
    reviewed_at: datetime,
) -> None:
    if review.review_stage == REGION_STAGE:
        expected_role_code = "provincial_manager"
        expected_scope_type = "organization"
        expected_scope_id = str(task.region_org_id)
    elif review.review_stage == HEADQUARTERS_STAGE:
        expected_role_code = "admin"
        expected_scope_type = "national"
        expected_scope_id = "*"
    else:
        _replay_evidence_invalid("期初盘点复核阶段不受支持")

    assignment = db.get(RoleAssignment, review.reviewer_role_assignment_id)
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    user = db.get(User, review.reviewer_user_id)
    person = db.get(Person, review.reviewer_person_id)
    valid_from = (
        _as_optional_utc(assignment.valid_from) if assignment is not None else None
    )
    valid_to = (
        _as_optional_utc(assignment.valid_to)
        if assignment is not None and assignment.valid_to is not None
        else None
    )
    revoked_at = (
        _as_optional_utc(assignment.revoked_at)
        if assignment is not None and assignment.revoked_at is not None
        else None
    )
    if (
        assignment is None
        or role is None
        or user is None
        or person is None
        or valid_from is None
        or valid_from > reviewed_at
        or (valid_to is not None and reviewed_at >= valid_to)
        or (revoked_at is not None and reviewed_at >= revoked_at)
        or assignment.status not in {
            "scheduled",
            "active",
            "expired",
            "revoked",
        }
        or assignment.user_id != review.reviewer_user_id
        or assignment.scope_type != expected_scope_type
        or assignment.scope_id != expected_scope_id
        or role.code != expected_role_code
        or role.is_external
        or user.person_id != review.reviewer_person_id
        or review.authorization_version <= 0
        or user.authorization_version < review.authorization_version
        or person.id != review.reviewer_person_id
    ):
        _replay_evidence_invalid("期初盘点复核历史身份或范围授权无法重证")


def _review_effect_owner(
    *,
    payload: object,
    reviews_by_id: Mapping[str, StocktakeReview],
    reviews_by_round_stage: Mapping[tuple[str, str], StocktakeReview],
) -> StocktakeReview:
    """Resolve one task effect to its immutable review business coordinate.

    ``StateTransitionEvent`` and ``OutboxEvent`` only have global idempotency
    keys.  Looking up the expected key therefore proves that one canonical row
    exists, but does not prove that a second row with a different key was not
    written for the same review.  Review effects carry both the immutable
    review id and its unique task/round/stage coordinate, so both coordinates
    must resolve to the same persisted review.

    Rows for another legitimate round resolve to that review and are not
    counted as duplicates of the target review.  A row selected by the exact
    review reason/event-type coordinate that cannot be assigned this way is a
    contradictory side effect and fails closed.
    """

    values = payload if isinstance(payload, dict) else {}
    review_id = values.get("review_id")
    round_id = values.get("round_id")
    stage = values.get("stage")
    decision = values.get("decision")
    owner_by_id = (
        reviews_by_id.get(review_id) if isinstance(review_id, str) else None
    )
    owner_by_round_stage = (
        reviews_by_round_stage.get((round_id, stage))
        if isinstance(round_id, str) and isinstance(stage, str)
        else None
    )
    if (
        owner_by_id is None
        or owner_by_round_stage is None
        or owner_by_id.id != owner_by_round_stage.id
        or decision != owner_by_id.decision
    ):
        _fail(
            "opening_review_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点复核状态或事件业务坐标矛盾",
        )
    return owner_by_id


def _review_side_effect_sets(
    db: Session,
    *,
    task: FormalStocktakeTask,
    target_review: StocktakeReview,
) -> tuple[tuple[StateTransitionEvent, ...], tuple[OutboxEvent, ...]]:
    """Return the exact State/Outbox sets owned by ``target_review``.

    The database queries deliberately use the stable task aggregate plus the
    canonical state reason / outbox event type rather than the expected
    idempotency key.  Payload coordinates distinguish another legal round
    using the same discriminator.  Every row in those complete sets must map
    to a real review and use that review's canonical key before the target set
    may be accepted as unique.
    """

    task_reviews = tuple(
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
    reviews_by_id = {str(row.id): row for row in task_reviews}
    reviews_by_round_stage = {
        (str(row.round_id), row.review_stage): row for row in task_reviews
    }
    if (
        reviews_by_id.get(str(target_review.id)) is None
        or reviews_by_round_stage.get(
            (str(target_review.round_id), target_review.review_stage)
        )
        is None
    ):
        _fail(
            "opening_review_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点复核业务坐标缺失",
        )

    state_candidates = tuple(
        db.scalars(
            select(StateTransitionEvent)
            .where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id == str(task.id),
                StateTransitionEvent.reason.in_(
                    tuple(
                        f"opening_{target_review.review_stage}_review_{decision}"
                        for decision in sorted(_TOP_LEVEL_DECISIONS)
                    )
                ),
            )
            .order_by(StateTransitionEvent.id)
        ).all()
    )
    outbox_candidates = tuple(
        db.scalars(
            select(OutboxEvent)
            .where(
                OutboxEvent.aggregate_type == "stocktake_task",
                OutboxEvent.aggregate_id == str(task.id),
                OutboxEvent.event_type
                == f"stocktake.opening.{target_review.review_stage}_reviewed",
            )
            .order_by(OutboxEvent.id)
        ).all()
    )

    target_states: list[StateTransitionEvent] = []
    for row in state_candidates:
        owner = _review_effect_owner(
            payload=row.metadata_jsonb,
            reviews_by_id=reviews_by_id,
            reviews_by_round_stage=reviews_by_round_stage,
        )
        if (
            row.reason != f"opening_{owner.review_stage}_review_{owner.decision}"
            or row.idempotency_key != _event_key("state", owner.id)
        ):
            _fail(
                "opening_review_idempotency_record_invalid",
                "service_unavailable",
                "期初盘点复核状态副作用不唯一或不规范",
            )
        if owner.id == target_review.id:
            target_states.append(row)

    target_outbox: list[OutboxEvent] = []
    for row in outbox_candidates:
        owner = _review_effect_owner(
            payload=row.payload_jsonb,
            reviews_by_id=reviews_by_id,
            reviews_by_round_stage=reviews_by_round_stage,
        )
        if (
            row.event_type != f"stocktake.opening.{owner.review_stage}_reviewed"
            or row.idempotency_key != _event_key("outbox", owner.id)
        ):
            _fail(
                "opening_review_idempotency_record_invalid",
                "service_unavailable",
                "期初盘点复核 Outbox 副作用不唯一或不规范",
            )
        if owner.id == target_review.id:
            target_outbox.append(row)

    return tuple(target_states), tuple(target_outbox)


def _capture_review_side_effects(
    db: Session,
    task: FormalStocktakeTask,
    review: StocktakeReview,
    differences: Sequence[StocktakeDifference],
) -> uuid.UUID:
    resulting_status = _resulting_status(review.review_stage, review.decision)
    expected_from_status = (
        "submitted" if review.review_stage == REGION_STAGE else "hq_review"
    )
    reason = f"opening_{review.review_stage}_review_{review.decision}"
    reviewed_at = _as_optional_utc(review.reviewed_at)
    if reviewed_at is None:
        _replay_evidence_invalid("期初盘点复核事实时间缺失")
    pending_count = sum(
        1 for row in differences if row.difference_type == "control_unassigned"
    )
    metadata = {
        "decision": review.decision,
        "decision_manifest_sha256": review.decision_manifest_sha256,
        "pending_control_count": pending_count,
        "review_id": str(review.id),
        "round_id": str(review.round_id),
        "stage": review.review_stage,
    }
    state_rows, outbox_rows = _review_side_effect_sets(
        db,
        task=task,
        target_review=review,
    )
    audit_rows = db.scalars(
        select(AuditEvent).where(
            AuditEvent.aggregate_type == "stocktake_review",
            AuditEvent.aggregate_id == str(review.id),
        )
    ).all()
    if len(state_rows) != 1 or len(outbox_rows) != 1 or len(audit_rows) != 1:
        _fail(
            "opening_review_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点复核状态、事件或审计证据不完整",
        )
    state = state_rows[0]
    outbox = outbox_rows[0]
    audit = audit_rows[0]
    if (
        state.aggregate_type != "stocktake_task"
        or state.aggregate_id != str(task.id)
        or state.from_status != expected_from_status
        or state.to_status != resulting_status
        or state.reason != reason
        or state.actor_id != review.reviewer_user_id
        or state.idempotency_key != _event_key("state", review.id)
        or _as_optional_utc(state.occurred_at) != reviewed_at
        or _as_optional_utc(state.created_at) != reviewed_at
        or state.metadata_jsonb != metadata
        or outbox.event_type != f"stocktake.opening.{review.review_stage}_reviewed"
        or outbox.aggregate_type != "stocktake_task"
        or outbox.aggregate_id != str(task.id)
        or outbox.idempotency_key != _event_key("outbox", review.id)
        or _as_optional_utc(outbox.available_at) != reviewed_at
        or _as_optional_utc(outbox.created_at) != reviewed_at
        or _as_optional_utc(outbox.updated_at) is None
        or (_as_optional_utc(outbox.updated_at) or reviewed_at) < reviewed_at
        or (
            outbox.locked_at is not None
            and (_as_optional_utc(outbox.locked_at) or reviewed_at) < reviewed_at
        )
        or (
            outbox.published_at is not None
            and (_as_optional_utc(outbox.published_at) or reviewed_at) < reviewed_at
        )
        or outbox.payload_jsonb != metadata
        or audit.stream_key != INVENTORY_STREAM_KEY
        or audit.actor_user_id != review.reviewer_user_id
        or audit.action != f"stocktake.opening.{review.review_stage}_reviewed"
        or audit.aggregate_type != "stocktake_review"
        or audit.aggregate_id != str(review.id)
        or _as_optional_utc(audit.occurred_at) != reviewed_at
        or _as_optional_utc(audit.created_at) is None
        or (_as_optional_utc(audit.created_at) or reviewed_at) < reviewed_at
        or audit.before_jsonb is not None
        or audit.after_jsonb
        != {
            **metadata,
            "authorization_version": review.authorization_version,
            "reviewer_person_id": str(review.reviewer_person_id),
            "reviewer_role_assignment_id": str(review.reviewer_role_assignment_id),
            "reviewer_user_id": review.reviewer_user_id,
        }
    ):
        _fail(
            "opening_review_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点复核证据内容不一致",
        )
    return audit.id


def _replay_evidence_invalid(
    message: str,
    *,
    cause: Exception | None = None,
) -> None:
    _fail(
        "opening_review_replay_evidence_invalid",
        "service_unavailable",
        message,
        cause=cause,
    )


def _result(
    review: StocktakeReview,
    differences: Sequence[StocktakeDifference],
) -> OpeningStocktakeReviewResult:
    return OpeningStocktakeReviewResult(
        review_id=review.id,
        task_id=review.task_id,
        round_id=review.round_id,
        review_stage=review.review_stage,
        decision=review.decision,
        resulting_task_status=_resulting_status(review.review_stage, review.decision),
        item_count=len(differences),
        pending_control_count=sum(
            1 for row in differences if row.difference_type == "control_unassigned"
        ),
    )


def _resulting_status(stage: str, decision: str) -> str:
    if decision != "approve":
        return "recount_required"
    return "hq_review" if stage == REGION_STAGE else "approved"


def _validate_stage_decision(stage: str, decision: str) -> None:
    if stage not in {REGION_STAGE, HEADQUARTERS_STAGE}:
        _fail(
            "opening_review_stage_invalid",
            "invalid_request",
            "期初盘点复核阶段无效",
        )
    if stage == HEADQUARTERS_STAGE and decision == "recount":
        _fail(
            "opening_review_headquarters_recount_forbidden",
            "invalid_request",
            "总部复核只允许通过或驳回，不得直接要求复盘",
        )


def _validate_command(
    command: SubmitOpeningStocktakeReviewCommand,
) -> SubmitOpeningStocktakeReviewCommand:
    if not isinstance(command, SubmitOpeningStocktakeReviewCommand):
        _fail("opening_review_command_required", "invalid_request", "复核命令类型无效")
    task_id = _require_uuid("task_id", command.task_id)
    round_id = _require_uuid("round_id", command.round_id)
    if command.decision not in _TOP_LEVEL_DECISIONS:
        _fail("opening_review_decision_invalid", "invalid_request", "复核结论无效")
    comment = _require_text("comment", command.comment, 10000, allow_empty=True)
    if command.decision != "approve" and not comment.strip():
        _fail(
            "opening_review_comment_required",
            "invalid_request",
            "复盘或拒绝必须填写复核说明",
        )
    if not isinstance(command.items, tuple):
        _fail("opening_review_items_invalid", "invalid_request", "复核明细必须使用不可变元组")
    items: list[OpeningStocktakeReviewItemInput] = []
    seen: set[uuid.UUID] = set()
    for row in command.items:
        if not isinstance(row, OpeningStocktakeReviewItemInput):
            _fail("opening_review_item_invalid", "invalid_request", "复核明细类型无效")
        difference_id = _require_uuid("difference_id", row.difference_id)
        if difference_id in seen:
            _fail("opening_review_item_duplicate", "invalid_request", "复核差异重复")
        seen.add(difference_id)
        if row.decision not in _ITEM_DECISIONS:
            _fail("opening_review_item_decision_invalid", "invalid_request", "逐项复核结论无效")
        item_comment = _require_text("item_comment", row.comment, 4000, allow_empty=True)
        items.append(
            OpeningStocktakeReviewItemInput(
                difference_id=difference_id,
                decision=row.decision,
                comment=item_comment,
            )
        )
    items.sort(key=lambda row: str(row.difference_id))
    return SubmitOpeningStocktakeReviewCommand(
        task_id=task_id,
        round_id=round_id,
        decision=command.decision,
        items=tuple(items),
        comment=comment,
    )


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail("formal_principal_required", "forbidden", "期初盘点复核必须使用正式权限主体")
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _fail("opening_review_actor_inactive", "forbidden", "当前账号或人员不可执行复核")
    return actor


def _require_current_actor(
    db: Session,
    supplied: FormalPrincipal,
    now: datetime,
) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError as exc:
        _fail(
            "opening_review_actor_not_current",
            "forbidden",
            "正式权限上下文已失效，请重新读取",
            cause=exc,
        )
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail(
            "opening_review_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取后再复核",
        )
    if (
        current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail("opening_review_actor_inactive", "forbidden", "当前账号或人员不可执行复核")
    return current


def _storage_hash(stage: str, raw_key: str) -> str:
    return hashlib.sha256(
        f"cloud_oam.opening_stocktake.review.{stage}.idempotency.v1\0{raw_key}".encode()
    ).hexdigest()


def _event_key(kind: str, review_id: uuid.UUID) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.opening_stocktake.review.{kind}.v1\0{review_id}".encode()
    ).hexdigest()
    return f"opening-review-{kind}-{digest}"


def _request_reference(raw: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.opening_stocktake.review.request.v1\0{raw}".encode()
    ).hexdigest()
    return f"opening-review-request-{digest}"


def _advisory_coordinate(namespace: str, value: str) -> int:
    digest = hashlib.sha256(
        f"cloud_oam.inventory.lock.v1\0{namespace}\0{value}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _take_advisory_locks(db: Session, coordinates: tuple[int, ...]) -> None:
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
                "opening_review_server_time_unavailable",
                "service_unavailable",
                "数据库服务端时间不可用",
            )
        return _as_utc(value)
    return datetime.now(timezone.utc)


def _require_idempotency_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 200
        or _PRINTABLE.fullmatch(value) is None
    ):
        _fail(
            "opening_review_idempotency_key_invalid",
            "invalid_request",
            "幂等键必须为 16 至 200 位可打印 ASCII 字符",
        )
    return value


def _require_request_id(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 8 <= len(value) <= 160
        or _PRINTABLE.fullmatch(value) is None
    ):
        _fail(
            "opening_review_request_id_invalid",
            "invalid_request",
            "请求标识必须为 8 至 160 位可打印 ASCII 字符",
        )
    return value


def _require_uuid(field: str, value: object) -> uuid.UUID:
    try:
        checked = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        _fail(f"opening_review_{field}_invalid", "invalid_request", f"{field} 必须为 UUID")
    if checked.int == 0:
        _fail(f"opening_review_{field}_invalid", "invalid_request", f"{field} 不能为零 UUID")
    return checked


def _require_text(
    field: str,
    value: object,
    limit: int,
    *,
    allow_empty: bool,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or value != value.strip()
        or (not allow_empty and not value)
    ):
        _fail(f"opening_review_{field}_invalid", "invalid_request", f"{field} 格式无效")
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


def _fail(
    code: str,
    category: str,
    message: str,
    *,
    cause: Exception | None = None,
) -> None:
    error = OpeningStocktakeReviewError(code, category, message)
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "OpeningStocktakeReviewError",
    "OpeningStocktakeReviewItemInput",
    "OpeningStocktakeReviewResult",
    "SubmitOpeningStocktakeReviewCommand",
    "submit_opening_headquarters_review",
    "submit_opening_region_review",
    "validate_opening_recount_trigger_review_graph",
    "validate_opening_review_evidence_for_replay",
]
