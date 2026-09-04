"""Open one append-only successor round for a reviewed opening stocktake.

This domain service is called by the formal recount HTTP command.  The caller
owns the surrounding transaction; this module flushes but never commits or
rolls back.  A recount preserves the submitted predecessor, records one
immutable causal case plus a complete per-scope authorization snapshot, then
atomically advances the task and creates one contiguous ``recount`` round.

No inventory account, balance, transaction, movement, posting or opening
establishment is created here.  PostgreSQL advisory locks serialize the exact
idempotency, task and source-round coordinates.  SQLite ``create_all`` tests do
not install migration 0018's triggers, so every graph invariant is also
enforced by this service.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
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
    Organization,
    OutboxEvent,
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
from ..models import User
from ..inventory_models import CustodyAssignment, StockLocation
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
    StocktakeRecountCase,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeReviewItem,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)
from .audit_chain import (
    AuditChainError,
    _lock_audit_chain_head_with_proof,
    _require_prelocked_audit_stream_proof,
    _verify_audit_event_with_prelocked_proof,
    append_audit_event,
)
from .inventory_posting import (
    InventoryPostingError,
    canonical_opening_count_manifest_sha256,
    canonical_opening_decision_manifest_sha256,
)
from .opening_stocktake import canonical_opening_manifest_sha256
from .opening_stocktake_count import (
    OpeningStocktakeCountError,
    _event_hash as _count_event_hash,
    _hash_document as _count_hash_document,
    _round_manifest_sha256,
    _plan_opening_count_replay_evidence,
    _validate_opening_count_replay_evidence_from_prelocked_task_graph,
    _validate_completion_evidence,
    _validate_difference_set_completion,
)
from .opening_stocktake_review import (
    OpeningStocktakeReviewError,
    _plan_opening_recount_trigger_review_graph_from_prelocked_reference_graph,
    _plan_opening_review_evidence_from_prelocked_reference_graph,
    _validate_opening_recount_trigger_review_graph_from_prelocked_task_graph,
    _validate_opening_review_evidence_from_prelocked_task_graph,
)
from . import opening_observation_disposition as observation_disposition_service
from .postgresql_lock_graph import (
    lock_opening_stocktake_task_evidence,
)


INVENTORY_STREAM_KEY: Final[str] = "inventory"
_PRINTABLE = re.compile(r"^[\x21-\x7e]+$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_OPENING_RECOUNT_ASSIGNMENT_PLAN_SEAL: Final[object] = object()
_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class OpeningStocktakeRecountError(RuntimeError):
    """Stable, database-detail-free failure for the recount boundary."""

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
class OpeningStocktakeRecountScopeAssignmentInput:
    scope_id: uuid.UUID
    assignee_user_id: str


@dataclass(frozen=True, slots=True)
class OpenOpeningStocktakeRecountCommand:
    task_id: uuid.UUID
    source_round_id: uuid.UUID
    assignments: tuple[OpeningStocktakeRecountScopeAssignmentInput, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class OpeningStocktakeRecountResult:
    recount_case_id: uuid.UUID
    task_id: uuid.UUID
    source_round_id: uuid.UUID
    next_round_id: uuid.UUID
    next_round_no: int
    scope_count: int
    resulting_task_status: str = "counting"
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _PreparedAssignment:
    scope: FormalStocktakeScope
    principal: FormalPrincipal
    assignment: RoleAssignment
    grant: ScopeGrant
    authorization_sha256: str
    assignment_sha256: str


@dataclass(frozen=True, slots=True)
class _OpeningRecountRoundAssignmentPlan:
    """Transaction-bound structural plan consumed after the audit lock."""

    session: Session
    transaction: object
    task_id: uuid.UUID
    round_id: uuid.UUID
    scope_ids: tuple[uuid.UUID, ...]
    freeze_ids: tuple[uuid.UUID, ...]
    allow_downstream: bool
    disposition_resolutions: tuple[tuple[uuid.UUID, object], ...]
    assignment_ids: tuple[tuple[uuid.UUID, uuid.UUID], ...]
    review_plans: tuple[object, ...]
    count_plans: tuple[object, ...]
    audit_event_ids: tuple[uuid.UUID, ...]
    seal: object


@dataclass(slots=True)
class _RecountEvidencePlanCollector:
    review_plans: list[object]
    count_plans: list[object]
    audit_event_ids: list[uuid.UUID]


def open_opening_stocktake_recount(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: OpenOpeningStocktakeRecountCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeRecountResult:
    """Create or read one exact recount case without owning the transaction."""

    try:
        return _open_recount(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except OpeningStocktakeRecountError:
        raise
    except AuditChainError as exc:
        _fail(
            "opening_recount_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，期初复盘轮次未打开",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "opening_recount_concurrent_conflict",
            "conflict",
            "期初复盘发生并发冲突，请回滚并重新读取",
            cause=exc,
        )
    except DBAPIError as exc:
        _fail(
            "opening_recount_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了期初复盘，请回滚并重新读取",
            cause=exc,
        )
    raise AssertionError("unreachable opening recount boundary")


def validate_opening_recount_round_assignment_evidence(
    db: Session,
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
) -> dict[uuid.UUID, StocktakeRecountScopeAssignment]:
    """Standalone recount proof in task -> principals -> evidence -> audit order.

    Callers that already own the task/reference graph must use the private
    structural-plan plus audit-proof pair below.  This public entry point takes
    UUID coordinates only, so an ORM object or caller assertion can never
    masquerade as proof that the canonical owner locks are held.
    """

    checked_task_id = _require_uuid("task_id", task_id)
    checked_round_id = _require_uuid("round_id", round_id)
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked_task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        _fail("opening_recount_task_not_found", "not_found", "期初盘点任务不存在")
    user_ids = _task_principal_user_ids(
        db,
        task_id=task.id,
        supplied_user_ids=(),
    )
    lock_formal_principal_graph(db, tuple(sorted(user_ids)))
    round_row = db.scalar(
        select(StocktakeRound)
        .where(
            StocktakeRound.id == checked_round_id,
            StocktakeRound.task_id == task.id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if round_row is None:
        _fail(
            "opening_recount_round_not_found",
            "not_found",
            "期初盘点复盘轮次不存在",
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
    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id == task.id)
            .order_by(InventoryFreeze.stocktake_scope_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    disposition_resolutions = _lock_recount_reference_graph(
        db,
        task=task,
        scopes=scopes,
    )
    plan = _plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        disposition_resolutions=disposition_resolutions,
    )
    _head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    return _validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph(
        db,
        plan=plan,
        audit_proof=audit_proof,
    )


def _plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    freezes: Sequence[InventoryFreeze],
    disposition_resolutions: Mapping[uuid.UUID, object],
) -> _OpeningRecountRoundAssignmentPlan:
    """Capture a sealed recount graph after references and before audit."""

    allow_downstream = bool(
        task.current_round_no > round_row.round_no
        or task.status in {"posted", "closed"}
    )
    collector = _RecountEvidencePlanCollector([], [], [])
    assignments = _capture_opening_recount_round_assignment_evidence(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        allow_downstream=allow_downstream,
        disposition_resolutions=disposition_resolutions,
        collector=collector,
    )
    transaction = db.get_transaction()
    if transaction is None:
        _chain_invalid("复盘结构计划要求当前事务")
    return _OpeningRecountRoundAssignmentPlan(
        session=db,
        transaction=transaction,
        task_id=task.id,
        round_id=round_row.id,
        scope_ids=tuple(row.id for row in scopes),
        freeze_ids=tuple(row.id for row in freezes),
        allow_downstream=allow_downstream,
        disposition_resolutions=tuple(
            sorted(disposition_resolutions.items(), key=lambda item: str(item[0]))
        ),
        assignment_ids=tuple(
            sorted(
                ((scope_id, row.id) for scope_id, row in assignments.items()),
                key=lambda item: str(item[0]),
            )
        ),
        review_plans=tuple(collector.review_plans),
        count_plans=tuple(collector.count_plans),
        audit_event_ids=tuple(collector.audit_event_ids),
        seal=_OPENING_RECOUNT_ASSIGNMENT_PLAN_SEAL,
    )


def _require_opening_recount_round_assignment_plan(
    db: Session,
    plan: object,
) -> _OpeningRecountRoundAssignmentPlan:
    transaction = db.get_transaction()
    if (
        not isinstance(plan, _OpeningRecountRoundAssignmentPlan)
        or plan.seal is not _OPENING_RECOUNT_ASSIGNMENT_PLAN_SEAL
        or plan.session is not db
        or transaction is None
        or plan.transaction is not transaction
    ):
        _chain_invalid("复盘结构计划不属于当前事务")
    return plan


def _opening_recount_assignments_from_plan(
    db: Session,
    *,
    plan: object,
) -> dict[uuid.UUID, StocktakeRecountScopeAssignment]:
    checked = _require_opening_recount_round_assignment_plan(db, plan)
    assignment_ids = tuple(row_id for _scope_id, row_id in checked.assignment_ids)
    rows = tuple(
        db.scalars(
            select(StocktakeRecountScopeAssignment)
            .where(StocktakeRecountScopeAssignment.id.in_(assignment_ids))
            .execution_options(populate_existing=True)
        ).all()
        if assignment_ids
        else ()
    )
    rows_by_id = {row.id: row for row in rows}
    if len(rows_by_id) != len(checked.assignment_ids):
        _chain_invalid("复盘结构计划中的执行人快照缺失")
    result = {
        scope_id: rows_by_id[assignment_id]
        for scope_id, assignment_id in checked.assignment_ids
    }
    if any(row.scope_id != scope_id for scope_id, row in result.items()):
        _chain_invalid("复盘结构计划中的执行人坐标不一致")
    return result


def _validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph(
    db: Session,
    *,
    plan: object,
    audit_proof: object,
) -> dict[uuid.UUID, StocktakeRecountScopeAssignment]:
    """Purely re-prove one structural plan under a transaction audit proof."""

    checked = _require_opening_recount_round_assignment_plan(db, plan)
    try:
        _require_prelocked_audit_stream_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
        )
    except AuditChainError as exc:
        _chain_invalid("复盘审计预锁证明不属于当前事务", cause=exc)
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
    scope_rows = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.id.in_(checked.scope_ids))
            .execution_options(populate_existing=True)
        ).all()
    )
    freeze_rows = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.id.in_(checked.freeze_ids))
            .execution_options(populate_existing=True)
        ).all()
    )
    scopes_by_id = {row.id: row for row in scope_rows}
    freezes_by_id = {row.id: row for row in freeze_rows}
    if (
        task is None
        or round_row is None
        or len(scopes_by_id) != len(checked.scope_ids)
        or len(freezes_by_id) != len(checked.freeze_ids)
    ):
        _chain_invalid("复盘结构计划的任务、轮次或范围证据缺失")
    scopes = tuple(scopes_by_id[row_id] for row_id in checked.scope_ids)
    freezes = tuple(freezes_by_id[row_id] for row_id in checked.freeze_ids)
    collector = _RecountEvidencePlanCollector([], [], [])
    assignments = _capture_opening_recount_round_assignment_evidence(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        allow_downstream=checked.allow_downstream,
        disposition_resolutions=dict(checked.disposition_resolutions),
        collector=collector,
    )
    recaptured_assignment_ids = tuple(
        sorted(
            ((scope_id, row.id) for scope_id, row in assignments.items()),
            key=lambda item: str(item[0]),
        )
    )
    if (
        recaptured_assignment_ids != checked.assignment_ids
        or tuple(collector.review_plans) != checked.review_plans
        or tuple(collector.count_plans) != checked.count_plans
        or tuple(collector.audit_event_ids) != checked.audit_event_ids
    ):
        _chain_invalid("复盘结构计划在审计锁定后发生漂移")
    for review_plan in checked.review_plans:
        if getattr(review_plan, "trigger_review_id", None) is not None:
            _validate_opening_recount_trigger_review_graph_from_prelocked_task_graph(
                db,
                plan=review_plan,
                audit_proof=audit_proof,
            )
        else:
            _validate_opening_review_evidence_from_prelocked_task_graph(
                db,
                plan=review_plan,
                audit_proof=audit_proof,
            )
    for count_plan in checked.count_plans:
        _validate_opening_count_replay_evidence_from_prelocked_task_graph(
            db,
            plan=count_plan,
            audit_proof=audit_proof,
        )
    try:
        for event_id in checked.audit_event_ids:
            verified = _verify_audit_event_with_prelocked_proof(
                db,
                proof=audit_proof,
                stream_key=INVENTORY_STREAM_KEY,
                event_id=event_id,
            )
            if verified.id != event_id:
                _chain_invalid("复盘审计事件坐标不一致")
    except AuditChainError as exc:
        _chain_invalid("复盘审计链无法从预锁证明重证", cause=exc)
    return assignments


def _capture_opening_recount_round_assignment_evidence(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    freezes: Sequence[InventoryFreeze],
    allow_downstream: bool,
    disposition_resolutions: Mapping[uuid.UUID, object] | None,
    collector: _RecountEvidencePlanCollector,
) -> dict[uuid.UUID, StocktakeRecountScopeAssignment]:
    if (
        task.task_type != "opening"
        or round_row.task_id != task.id
        or round_row.round_no <= 1
        or round_row.round_type != "recount"
        or round_row.recount_case_id is None
        or task.current_round_no < round_row.round_no
        or (not allow_downstream and task.current_round_no != round_row.round_no)
        or not scopes
        or [row.scope_no for row in scopes] != list(range(1, len(scopes) + 1))
        or any(row.task_id != task.id for row in scopes)
    ):
        _chain_invalid("复盘轮次、任务当前指针或范围清单不一致")

    all_rounds = tuple(
        db.scalars(
            select(StocktakeRound)
            .where(StocktakeRound.task_id == task.id)
            .order_by(StocktakeRound.round_no)
        ).all()
    )
    if (
        len(all_rounds) != task.current_round_no
        or [row.round_no for row in all_rounds]
        != list(range(1, task.current_round_no + 1))
        or all_rounds[round_row.round_no - 1].id != round_row.id
        or any(row.task_id != task.id or row.status == "superseded" for row in all_rounds)
        or all_rounds[0].round_type != "initial"
        or all_rounds[0].recount_case_id is not None
        or any(
            row.round_type != "recount" or row.recount_case_id is None
            for row in all_rounds[1:]
        )
        or any(row.status != "submitted" for row in all_rounds[:-1])
        or all_rounds[-1].status not in {"counting", "submitted"}
    ):
        _chain_invalid("期初盘点轮次不是从首轮开始的唯一连续链")

    scope_by_id = {row.id: row for row in scopes}
    freeze_by_scope = {row.stocktake_scope_id: row for row in freezes}
    released = task.status in {"posted", "closed"}
    if (
        len(scope_by_id) != len(scopes)
        or len(freezes) != len(scopes)
        or len(freeze_by_scope) != len(scopes)
        or set(freeze_by_scope) != set(scope_by_id)
        or any(
            (freeze := freeze_by_scope[scope.id]).task_id != task.id
            or freeze.scope_key != scope.scope_key
            or (
                released
                and (
                    freeze.status != "released"
                    or freeze.valid_to is None
                    or freeze.released_by_user_id is None
                    or not freeze.release_reason.strip()
                )
            )
            or (
                not released
                and (freeze.status != "active" or freeze.valid_to is not None)
            )
            for scope in scopes
        )
    ):
        _chain_invalid("复盘冻结范围不完整或与任务生命周期不一致")
    try:
        scope_manifest = canonical_opening_recount_scope_manifest_sha256(
            task.region_org_id, scopes, freezes
        )
    except (KeyError, TypeError, ValueError) as exc:
        _chain_invalid("复盘范围清单无法规范化", cause=exc)
    if task.scope_manifest_sha256 != scope_manifest:
        _chain_invalid("复盘范围清单摘要无法从冻结事实重算")

    cases = tuple(
        db.scalars(
            select(StocktakeRecountCase)
            .where(StocktakeRecountCase.task_id == task.id)
            .order_by(StocktakeRecountCase.next_round_no)
        ).all()
    )
    assignments = tuple(
        db.scalars(
            select(StocktakeRecountScopeAssignment)
            .where(StocktakeRecountScopeAssignment.task_id == task.id)
            .order_by(
                StocktakeRecountScopeAssignment.recount_case_id,
                StocktakeRecountScopeAssignment.scope_id,
            )
        ).all()
    )
    case_by_id = {row.id: row for row in cases}
    case_by_next = {row.next_round_no: row for row in cases}
    assignments_by_case: dict[
        uuid.UUID, list[StocktakeRecountScopeAssignment]
    ] = defaultdict(list)
    for row in assignments:
        assignments_by_case[row.recount_case_id].append(row)
    if (
        len(cases) != len(all_rounds) - 1
        or len(case_by_id) != len(cases)
        or len(case_by_next) != len(cases)
        or set(case_by_next) != set(range(2, len(all_rounds) + 1))
        or {row.recount_case_id for row in all_rounds[1:]} != set(case_by_id)
        or set(assignments_by_case) != set(case_by_id)
    ):
        _chain_invalid("复盘 case、后继轮次或执行人集合缺失、重复或分叉")

    assignments_for_round: dict[
        int, dict[uuid.UUID, StocktakeRecountScopeAssignment]
    ] = {}
    expected_assignments_for_source: Mapping[
        uuid.UUID, StocktakeRecountScopeAssignment
    ] | None = None
    for successor in all_rounds[1:]:
        source = all_rounds[successor.round_no - 2]
        case = case_by_next[successor.round_no]
        case_assignments = tuple(assignments_by_case[case.id])
        assignment_map = _validate_recount_edge_evidence(
            db,
            task=task,
            source_round=source,
            successor_round=successor,
            case=case,
            assignments=case_assignments,
            scopes=scopes,
            scope_by_id=scope_by_id,
            freezes=freezes,
            scope_manifest=scope_manifest,
            source_assignments=expected_assignments_for_source,
            disposition_resolutions=disposition_resolutions,
            collector=collector,
        )
        assignments_for_round[successor.round_no] = assignment_map
        expected_assignments_for_source = assignment_map

    assert expected_assignments_for_source is not None
    _validate_recount_task_tip(
        db,
        task=task,
        round_row=all_rounds[-1],
        scopes=scopes,
        source_assignments=expected_assignments_for_source,
        disposition_resolutions=disposition_resolutions,
        collector=collector,
    )
    return assignments_for_round[round_row.round_no]


def _validate_recount_edge_evidence(
    db: Session,
    *,
    task: FormalStocktakeTask,
    source_round: StocktakeRound,
    successor_round: StocktakeRound,
    case: StocktakeRecountCase,
    assignments: Sequence[StocktakeRecountScopeAssignment],
    scopes: Sequence[FormalStocktakeScope],
    scope_by_id: Mapping[uuid.UUID, FormalStocktakeScope],
    freezes: Sequence[InventoryFreeze],
    scope_manifest: str,
    source_assignments: Mapping[
        uuid.UUID, StocktakeRecountScopeAssignment
    ] | None,
    disposition_resolutions: Mapping[uuid.UUID, object] | None,
    collector: _RecountEvidencePlanCollector,
) -> dict[uuid.UUID, StocktakeRecountScopeAssignment]:
    if (
        source_round.status != "submitted"
        or source_round.submitted_at is None
        or source_round.count_manifest_sha256 is None
        or not _SHA256.fullmatch(source_round.count_manifest_sha256)
        or successor_round.round_no != source_round.round_no + 1
        or successor_round.recount_case_id != case.id
        or successor_round.idempotency_key_hash != _round_key_hash(case.id)
        or case.task_id != task.id
        or case.source_round_id != source_round.id
        or case.next_round_no != successor_round.round_no
        or case.scope_count != len(scopes)
        or case.scope_manifest_sha256 != scope_manifest
    ):
        _chain_invalid("复盘来源、case 与后继轮次业务坐标不一致")

    scope_completions = tuple(
        db.scalars(
            select(StocktakeScopeCountCompletion)
            .where(
                StocktakeScopeCountCompletion.task_id == task.id,
                StocktakeScopeCountCompletion.round_id == source_round.id,
            )
            .order_by(StocktakeScopeCountCompletion.scope_id)
        ).all()
    )
    submissions = tuple(
        db.scalars(
            select(StocktakeRoundSubmission).where(
                StocktakeRoundSubmission.task_id == task.id,
                StocktakeRoundSubmission.round_id == source_round.id,
            )
        ).all()
    )
    difference_completions = tuple(
        db.scalars(
            select(StocktakeDifferenceSetCompletion).where(
                StocktakeDifferenceSetCompletion.task_id == task.id,
                StocktakeDifferenceSetCompletion.round_id == source_round.id,
            )
        ).all()
    )
    count_lines = tuple(
        db.scalars(
            select(StocktakeCountLine)
            .where(
                StocktakeCountLine.task_id == task.id,
                StocktakeCountLine.round_id == source_round.id,
            )
            .order_by(StocktakeCountLine.stock_account_id, StocktakeCountLine.id)
        ).all()
    )
    count_line_ids = tuple(row.id for row in count_lines)
    count_serials = tuple(
        db.scalars(
            select(StocktakeCountSerial)
            .where(StocktakeCountSerial.count_line_id.in_(count_line_ids))
            .order_by(
                StocktakeCountSerial.count_line_id,
                StocktakeCountSerial.serial_id,
            )
        ).all()
        if count_line_ids
        else ()
    )
    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.round_id == source_round.id,
            )
            .order_by(StocktakeCountObservation.observation_no)
        ).all()
    )
    reviews = tuple(
        db.scalars(
            select(StocktakeReview)
            .where(
                StocktakeReview.task_id == task.id,
                StocktakeReview.round_id == source_round.id,
            )
            .order_by(StocktakeReview.reviewed_at, StocktakeReview.id)
        ).all()
    )
    postings = tuple(
        db.scalars(
            select(StocktakePosting).where(
                StocktakePosting.task_id == task.id,
                StocktakePosting.round_id == source_round.id,
            )
        ).all()
    )
    establishments = tuple(
        db.scalars(
            select(InventoryOpeningEstablishment).where(
                InventoryOpeningEstablishment.task_id == task.id,
                InventoryOpeningEstablishment.round_id == source_round.id,
            )
        ).all()
    )
    if (
        len(submissions) != 1
        or len(difference_completions) != 1
        or postings
        or establishments
    ):
        _chain_invalid("复盘来源封印缺失、重复或已经形成过账事实")
    submission = submissions[0]
    completion = difference_completions[0]
    if source_assignments is not None and set(source_assignments) != set(scope_by_id):
        _chain_invalid("复盘来源轮次的前一 edge 执行人清单不完整")
    try:
        _validate_submitted_source_round_seal(
            db,
            task=task,
            source_round=source_round,
            scopes=scopes,
            scope_completions=scope_completions,
            submission=submission,
            count_lines=count_lines,
            count_serials=count_serials,
            observations=observations,
            require_task_submission_pointer=False,
            source_assignments=source_assignments,
        )
        _validate_difference_set_completion(
            db,
            task=task,
            round_row=source_round,
            submission=submission,
            completion=completion,
        )
    except (OpeningStocktakeCountError, TypeError, ValueError) as exc:
        _chain_invalid("复盘来源提交或差异封印无法重算", cause=exc)

    try:
        if disposition_resolutions is None:
            _chain_invalid("复盘来源处置解析全集未预锁")
        trigger_plan = (
            _plan_opening_recount_trigger_review_graph_from_prelocked_reference_graph(
                db,
                task=task,
                round_row=source_round,
                reviews=reviews,
                disposition_resolutions=disposition_resolutions,
            )
        )
        collector.review_plans.append(trigger_plan)
        trigger = db.get(
            StocktakeReview,
            getattr(trigger_plan, "trigger_review_id", None),
        )
        if trigger is None:
            _chain_invalid("复盘来源终端复核事实不存在")
    except OpeningStocktakeReviewError as exc:
        _chain_invalid("复盘来源没有唯一合法的终端复核图", cause=exc)
    _plan_count_round_side_effects(
        db,
        task=task,
        round_row=source_round,
        scopes=scopes,
        scope_completions=scope_completions,
        round_assignments=source_assignments,
        collector=collector,
    )
    opened_at = _as_utc(case.opened_at)
    trigger_at = _as_utc(trigger.reviewed_at)
    completion_at = _as_utc(completion.completed_at)
    successor_started = _as_utc(successor_round.started_at)
    if (
        case.source_round_submission_id != submission.id
        or case.source_difference_completion_id != completion.id
        or case.trigger_review_id != trigger.id
        or _as_utc(case.created_at) != opened_at
        or completion_at > trigger_at
        or trigger_at > opened_at
        or opened_at > successor_started
    ):
        _chain_invalid("复盘来源封印、终端复核与开轮时间顺序不一致")

    _validate_historical_authorization(
        db,
        user_id=case.opened_by_user_id,
        person_id=case.opened_by_person_id,
        assignment_id=case.opened_role_assignment_id,
        authorization_version=case.authorization_version,
        role_code=case.role_code,
        scope_type=case.scope_type,
        scope_id=case.scope_id_snapshot,
        occurred_at=opened_at,
    )
    if not (
        (
            case.role_code == "admin"
            and case.scope_type == "national"
            and case.scope_id_snapshot == "*"
        )
        or (
            case.role_code == "provincial_manager"
            and case.scope_type == "organization"
            and _same_uuid(case.scope_id_snapshot, task.region_org_id)
        )
    ):
        _chain_invalid("复盘开启人的历史范围授权不匹配")

    location_ids = tuple(sorted({row.location_id for row in scopes}, key=str))
    locations = {
        row.id: row
        for row in db.scalars(
            select(StockLocation).where(StockLocation.id.in_(location_ids))
        ).all()
    }
    assignment_map = {row.scope_id: row for row in assignments}
    if (
        len(assignments) != len(scopes)
        or len(assignment_map) != len(assignments)
        or set(assignment_map) != set(scope_by_id)
        or set(locations) != set(location_ids)
    ):
        _chain_invalid("复盘执行人历史快照不完整或重复")
    try:
        opener_auth = canonical_opening_recount_authorization_sha256(
            authorization_kind="opener",
            user_id=case.opened_by_user_id,
            person_id=case.opened_by_person_id,
            assignment_id=case.opened_role_assignment_id,
            authorization_version=case.authorization_version,
            role_code=case.role_code,
            scope_type=case.scope_type,
            scope_id=case.scope_id_snapshot,
            occurred_at=opened_at,
        )
        for row in assignments:
            assigned_at = _as_utc(row.assigned_at)
            scope = scope_by_id[row.scope_id]
            if (
                row.recount_case_id != case.id
                or row.task_id != task.id
                or row.source_round_id != source_round.id
                or assigned_at != opened_at
                or _as_utc(row.created_at) != assigned_at
            ):
                _chain_invalid("复盘执行人快照与 case 坐标或时间不一致")
            _validate_historical_authorization(
                db,
                user_id=row.assignee_user_id,
                person_id=row.assignee_person_id,
                assignment_id=row.assignee_role_assignment_id,
                authorization_version=row.authorization_version,
                role_code=row.role_code,
                scope_type=row.scope_type,
                scope_id=row.scope_id_snapshot,
                occurred_at=assigned_at,
            )
            if not _assignment_scope_exact(row, scope, locations[scope.location_id]):
                _chain_invalid("复盘执行人历史范围授权与库存范围不匹配")
            assignment_auth = canonical_opening_recount_authorization_sha256(
                authorization_kind="assignee",
                user_id=row.assignee_user_id,
                person_id=row.assignee_person_id,
                assignment_id=row.assignee_role_assignment_id,
                authorization_version=row.authorization_version,
                role_code=row.role_code,
                scope_type=row.scope_type,
                scope_id=row.scope_id_snapshot,
                occurred_at=assigned_at,
            )
            assignment_hash = canonical_opening_recount_assignment_sha256(
                recount_case_id=case.id,
                task_id=task.id,
                source_round_id=source_round.id,
                scope_id=row.scope_id,
                assignee_user_id=row.assignee_user_id,
                assignee_person_id=row.assignee_person_id,
                assignee_role_assignment_id=row.assignee_role_assignment_id,
                authorization_version=row.authorization_version,
                role_code=row.role_code,
                scope_type=row.scope_type,
                scope_id_snapshot=row.scope_id_snapshot,
                authorization_sha256=assignment_auth,
                assigned_at=assigned_at,
            )
            if (
                row.authorization_sha256 != assignment_auth
                or row.assignment_sha256 != assignment_hash
            ):
                _chain_invalid("复盘执行人授权或行摘要无法重算")
        assignment_manifest = canonical_opening_recount_assignment_manifest_sha256(
            assignments
        )
        request_sha = canonical_opening_recount_request_sha256(
            actor_user_id=case.opened_by_user_id,
            actor_person_id=case.opened_by_person_id,
            task_id=task.id,
            source_round_id=source_round.id,
            assignments=tuple(
                OpeningStocktakeRecountScopeAssignmentInput(
                    scope_id=row.scope_id,
                    assignee_user_id=row.assignee_user_id,
                )
                for row in assignments
            ),
            reason=case.reason,
        )
        recount_manifest = canonical_opening_recount_manifest_sha256(
            recount_case_id=case.id,
            task_id=task.id,
            source_round_id=source_round.id,
            source_round_submission_id=submission.id,
            source_round_manifest_sha256=submission.round_manifest_sha256,
            source_count_manifest_sha256=source_round.count_manifest_sha256 or "",
            source_difference_completion_id=completion.id,
            source_difference_manifest_sha256=completion.difference_manifest_sha256,
            trigger_review_id=trigger.id,
            trigger_decision_manifest_sha256=trigger.decision_manifest_sha256,
            next_round_no=case.next_round_no,
            scope_count=case.scope_count,
            scope_manifest_sha256=scope_manifest,
            assignment_manifest_sha256=assignment_manifest,
            request_sha256=request_sha,
            authorization_sha256=opener_auth,
            reason=case.reason,
            opened_at=opened_at,
        )
    except (TypeError, ValueError) as exc:
        _chain_invalid("复盘 case 或执行人清单无法规范化", cause=exc)
    if (
        case.authorization_sha256 != opener_auth
        or case.assignment_manifest_sha256 != assignment_manifest
        or case.request_sha256 != request_sha
        or case.recount_manifest_sha256 != recount_manifest
    ):
        _chain_invalid("复盘 case、授权或执行人清单摘要无法重算")
    collector.audit_event_ids.append(
        _capture_side_effects(
            db,
            task=task,
            case=case,
            next_round_id=successor_round.id,
        )
    )
    return assignment_map


def _validate_recount_task_tip(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    source_assignments: Mapping[
        uuid.UUID, StocktakeRecountScopeAssignment
    ],
    disposition_resolutions: Mapping[uuid.UUID, object] | None,
    collector: _RecountEvidencePlanCollector,
) -> None:
    if round_row.round_no != task.current_round_no:
        _chain_invalid("任务当前轮次指针没有指向复盘链末端")
    if round_row.status == "counting":
        if task.status != "counting":
            _chain_invalid("复盘末轮 counting 状态与任务状态不一致")
        return
    submitted_at = _as_utc(round_row.submitted_at)
    if task.submitted_at is None or _as_utc(task.submitted_at) != submitted_at:
        _chain_invalid("复盘末轮提交时间与任务当前提交指针不一致")
    scope_completions = tuple(
        db.scalars(
            select(StocktakeScopeCountCompletion)
            .where(
                StocktakeScopeCountCompletion.task_id == task.id,
                StocktakeScopeCountCompletion.round_id == round_row.id,
            )
            .order_by(StocktakeScopeCountCompletion.scope_id)
        ).all()
    )
    submissions = tuple(
        db.scalars(
            select(StocktakeRoundSubmission).where(
                StocktakeRoundSubmission.task_id == task.id,
                StocktakeRoundSubmission.round_id == round_row.id,
            )
        ).all()
    )
    difference_completions = tuple(
        db.scalars(
            select(StocktakeDifferenceSetCompletion).where(
                StocktakeDifferenceSetCompletion.task_id == task.id,
                StocktakeDifferenceSetCompletion.round_id == round_row.id,
            )
        ).all()
    )
    count_lines = tuple(
        db.scalars(
            select(StocktakeCountLine)
            .where(
                StocktakeCountLine.task_id == task.id,
                StocktakeCountLine.round_id == round_row.id,
            )
            .order_by(StocktakeCountLine.stock_account_id, StocktakeCountLine.id)
        ).all()
    )
    count_line_ids = tuple(row.id for row in count_lines)
    count_serials = tuple(
        db.scalars(
            select(StocktakeCountSerial)
            .where(StocktakeCountSerial.count_line_id.in_(count_line_ids))
            .order_by(
                StocktakeCountSerial.count_line_id,
                StocktakeCountSerial.serial_id,
            )
        ).all()
        if count_line_ids
        else ()
    )
    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.round_id == round_row.id,
            )
            .order_by(StocktakeCountObservation.observation_no)
        ).all()
    )
    if len(submissions) != 1 or len(difference_completions) != 1:
        _chain_invalid("复盘末轮提交或差异完成封印缺失、重复")
    submission = submissions[0]
    difference_completion = difference_completions[0]
    try:
        _validate_submitted_source_round_seal(
            db,
            task=task,
            source_round=round_row,
            scopes=scopes,
            scope_completions=scope_completions,
            submission=submission,
            count_lines=count_lines,
            count_serials=count_serials,
            observations=observations,
            require_task_submission_pointer=True,
            source_assignments=source_assignments,
        )
        _validate_difference_set_completion(
            db,
            task=task,
            round_row=round_row,
            submission=submission,
            completion=difference_completion,
        )
    except OpeningStocktakeCountError as exc:
        _chain_invalid("复盘末轮提交或差异封印无法重算", cause=exc)
    reviews = tuple(
        db.scalars(
            select(StocktakeReview)
            .where(
                StocktakeReview.task_id == task.id,
                StocktakeReview.round_id == round_row.id,
            )
            .order_by(StocktakeReview.reviewed_at, StocktakeReview.id)
        ).all()
    )
    regions = [row for row in reviews if row.review_stage == "region"]
    headquarters = [row for row in reviews if row.review_stage == "headquarters"]
    if len(regions) > 1 or len(headquarters) > 1 or len(reviews) != len(regions) + len(headquarters):
        _chain_invalid("复盘末轮复核事实缺失、重复或不受支持")
    expected = "submitted"
    if headquarters:
        if len(regions) != 1 or regions[0].decision != "approve":
            _chain_invalid("总部复核没有唯一的区域通过前置事实")
        hq = headquarters[0]
        if (
            hq.decision not in {"approve", "reject"}
            or hq.reviewer_user_id == regions[0].reviewer_user_id
            or hq.reviewer_person_id == regions[0].reviewer_person_id
            or _as_utc(hq.reviewed_at) <= _as_utc(regions[0].reviewed_at)
        ):
            _chain_invalid("总部复核结论、顺序或职责分离无效")
        expected = "approved" if hq.decision == "approve" else "recount_required"
    elif regions:
        region = regions[0]
        expected = "hq_review" if region.decision == "approve" else "recount_required"
    if task.status not in ({expected, "posted", "closed"} if expected == "approved" else {expected}):
        _chain_invalid("复盘末轮复核图与任务当前状态不一致")
    for review in reviews:
        try:
            if disposition_resolutions is None:
                _chain_invalid("复盘末轮处置解析全集未预锁")
            collector.review_plans.append(
                _plan_opening_review_evidence_from_prelocked_reference_graph(
                    db,
                    task_id=task.id,
                    round_id=round_row.id,
                    review_id=review.id,
                    expected_stage=review.review_stage,
                    expected_decision=review.decision,
                    disposition_resolutions=disposition_resolutions,
                )
            )
        except OpeningStocktakeReviewError as exc:
            _chain_invalid("复盘末轮复核事实无法重证", cause=exc)
    _plan_count_round_side_effects(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        scope_completions=scope_completions,
        round_assignments=source_assignments,
        collector=collector,
    )
    # Do not re-enter the public terminal opening replay here.  Recount owns
    # only its immutable task-local chain; the ledger-first inventory/finalize
    # boundary proves posted/closed movement and establishment facts.


def _chain_invalid(message: str, *, cause: Exception | None = None) -> None:
    _fail(
        "opening_recount_chain_invalid",
        "precondition_failed",
        message,
        cause=cause,
    )


def _task_principal_user_ids(
    db: Session,
    *,
    task_id: uuid.UUID,
    supplied_user_ids: Sequence[str],
) -> set[str]:
    """Pre-read every task-local actor coordinate before principal locking.

    The caller already owns the mutable task row.  These values are used only
    to choose principal locks; all business evidence is reread after the 0027
    task-evidence helper has acquired the owner-side row locks.
    """

    values: set[str | None] = set(supplied_user_ids)
    task_creator = db.scalar(
        select(FormalStocktakeTask.created_by_user_id).where(
            FormalStocktakeTask.id == task_id
        )
    )
    values.add(task_creator)

    columns = (
        (FormalStocktakeScope.assignee_user_id, FormalStocktakeScope.task_id),
        (InventoryFreeze.created_by_user_id, InventoryFreeze.task_id),
        (InventoryFreeze.released_by_user_id, InventoryFreeze.task_id),
        (StocktakeRound.submitted_by_user_id, StocktakeRound.task_id),
        (StocktakeCountLine.counted_by_user_id, StocktakeCountLine.task_id),
        (
            StocktakeCountObservation.counted_by_user_id,
            StocktakeCountObservation.task_id,
        ),
        (
            StocktakeScopeCountCompletion.completed_by_user_id,
            StocktakeScopeCountCompletion.task_id,
        ),
        (
            StocktakeRoundSubmission.submitted_by_user_id,
            StocktakeRoundSubmission.task_id,
        ),
        (
            StocktakeObservationDisposition.decided_by_user_id,
            StocktakeObservationDisposition.task_id,
        ),
        (
            StocktakeDifferenceSetCompletion.completed_by_user_id,
            StocktakeDifferenceSetCompletion.task_id,
        ),
        (StocktakeReview.reviewer_user_id, StocktakeReview.task_id),
        (StocktakeRecountCase.opened_by_user_id, StocktakeRecountCase.task_id),
        (
            StocktakeRecountScopeAssignment.assignee_user_id,
            StocktakeRecountScopeAssignment.task_id,
        ),
        (StocktakePosting.posted_by_user_id, StocktakePosting.task_id),
        (
            InventoryOpeningEstablishment.established_by_user_id,
            InventoryOpeningEstablishment.task_id,
        ),
    )
    for user_column, task_column in columns:
        values.update(
            db.scalars(select(user_column).where(task_column == task_id)).all()
        )
    return {value for value in values if isinstance(value, str) and value}


def _lock_recount_reference_graph(
    db: Session,
    *,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    proposed_commands: Sequence[
        tuple[
            StocktakeCountObservation,
            observation_disposition_service.RecordOpeningObservationDispositionCommand,
        ]
    ] = (),
    extra_material_ids: Sequence[uuid.UUID] = (),
    extra_account_ids: Sequence[uuid.UUID] = (),
    extra_serial_ids: Sequence[uuid.UUID] = (),
) -> dict[uuid.UUID, object]:
    try:
        return observation_disposition_service.lock_and_prove_opening_task_observation_resolutions(
            db,
            task=task,
            scopes=scopes,
            proposed_commands=proposed_commands,
            extra_material_ids=extra_material_ids,
            extra_account_ids=extra_account_ids,
            extra_serial_ids=extra_serial_ids,
        )
    except observation_disposition_service.OpeningObservationDispositionError as exc:
        _chain_invalid("复盘任务的共享主数据引用图无法安全重证", cause=exc)


def _lock_terminal_replay_root(db: Session, *, task_id: uuid.UUID) -> object:
    """Enter the ledger-first terminal replay boundary without a module cycle."""

    from . import opening_stocktake_finalize as finalize_service

    try:
        return finalize_service._lock_opening_terminal_task_root(
            db,
            task_id=task_id,
        )
    except finalize_service.OpeningStocktakeFinalizeError as exc:
        _invalid_replay("复盘幂等重放的终态根图无法预锁", cause=exc)


def _lock_terminal_replay_graph(
    db: Session,
    *,
    root: object,
    principal_graph: object,
) -> object:
    """Lock one complete terminal opening graph after principal rows."""

    from . import opening_stocktake_finalize as finalize_service

    try:
        return finalize_service._lock_opening_terminal_task_graph_for_task(
            db,
            root=root,
            principal_graph=principal_graph,
        )
    except finalize_service.OpeningStocktakeFinalizeError as exc:
        _invalid_replay("复盘幂等重放的终态证据图无法预锁", cause=exc)


def _lock_terminal_replay_principal_graph(
    db: Session,
    *,
    task_id: uuid.UUID,
    supplied_user_ids: Sequence[str],
) -> object:
    """Seal one complete terminal-task principal union before evidence."""

    from . import inventory_posting as posting_service

    try:
        return posting_service._lock_opening_task_principal_graph(
            db,
            task_ids=(task_id,),
            supplied_user_ids=supplied_user_ids,
        )
    except posting_service.InventoryPostingError as exc:
        _invalid_replay("复盘幂等重放的历史人员图无法预锁", cause=exc)


def _terminal_replay_disposition_resolutions(
    proof: object,
) -> dict[uuid.UUID, object]:
    """Flatten the sealed per-round resolution maps without widening them."""

    resolutions_by_round = getattr(proof, "disposition_resolutions_by_round", None)
    if not isinstance(resolutions_by_round, Mapping):
        _invalid_replay("复盘幂等重放缺少已预锁的处置解析全集")
    combined: dict[uuid.UUID, object] = {}
    for round_id in sorted(resolutions_by_round, key=str):
        round_resolutions = resolutions_by_round[round_id]
        if not isinstance(round_id, uuid.UUID) or not isinstance(
            round_resolutions, Mapping
        ):
            _invalid_replay("复盘幂等重放的处置解析结构无效")
        for observation_id in sorted(round_resolutions, key=str):
            if (
                not isinstance(observation_id, uuid.UUID)
                or observation_id in combined
            ):
                _invalid_replay("复盘幂等重放的处置解析坐标重复或无效")
            combined[observation_id] = round_resolutions[observation_id]
    return combined


def _validate_terminal_replay_graph(
    db: Session,
    *,
    proof: object,
    audit_proof: object,
) -> None:
    """Purely re-prove terminal inventory facts after the audit lock."""

    from . import opening_stocktake_finalize as finalize_service

    try:
        finalize_service._validate_opening_inventory_evidence_from_prelocked_graph(
            db,
            proof=proof,
            audit_proof=audit_proof,
        )
    except (
        finalize_service.OpeningStocktakeFinalizeError,
        InventoryPostingError,
    ) as exc:
        _invalid_replay("复盘幂等重放的终态证据无法从预锁图重证", cause=exc)


def _open_recount(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: OpenOpeningStocktakeRecountCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeRecountResult:
    supplied_actor = _validate_supplied_actor(actor)
    checked = _validate_command(command)
    checked_key = _require_idempotency_key(idempotency_key)
    checked_request_id = _require_request_id(request_id)
    key_hash = _storage_hash(checked_key)

    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("opening-recount-idempotency", key_hash),
            _advisory_coordinate("opening-recount-task", str(checked.task_id)),
            _advisory_coordinate(
                "opening-recount-source-round", str(checked.source_round_id)
            ),
        ),
    )

    # A plain coordinate probe is used only to choose between two canonical
    # lock orders.  Exact posted/closed retries must enter ledger -> task;
    # normal recount commands stay task-first and never acquire the ledger.
    task_status_probe = db.scalar(
        select(FormalStocktakeTask.status).where(
            FormalStocktakeTask.id == checked.task_id
        )
    )
    if task_status_probe is None:
        _fail("opening_recount_task_not_found", "not_found", "期初盘点任务不存在")

    # A globally unique key bound to another business coordinate is a stable
    # conflict even when the requested source graph is itself damaged.  Check
    # it before any common/source-seal validation so retries never leak a
    # changing precondition error for the same conflicting key.
    existing_by_key = db.scalar(
        select(StocktakeRecountCase)
        .where(StocktakeRecountCase.idempotency_key_hash == key_hash)
        .execution_options(populate_existing=True)
    )
    if existing_by_key is not None and (
        existing_by_key.task_id != checked.task_id
        or existing_by_key.source_round_id != checked.source_round_id
    ):
        _idempotency_conflict()

    terminal_replay = bool(
        existing_by_key is not None
        and task_status_probe in {"posted", "closed"}
    )
    terminal_root: object | None = None
    terminal_graph: object | None = None
    if terminal_replay:
        terminal_root = _lock_terminal_replay_root(db, task_id=checked.task_id)
        task = getattr(terminal_root, "task", None)
        if not isinstance(task, FormalStocktakeTask) or task.status not in {
            "posted",
            "closed",
        }:
            _invalid_replay("复盘幂等重放的终态任务根已变化")
    else:
        task = db.scalar(
            select(FormalStocktakeTask)
            .where(FormalStocktakeTask.id == checked.task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if task is None:
            _fail("opening_recount_task_not_found", "not_found", "期初盘点任务不存在")
        if existing_by_key is not None and task.status in {"posted", "closed"}:
            _fail(
                "opening_recount_terminal_replay_retry_required",
                "conflict",
                "期初盘点已进入终态，请回滚后按终态锁序重试复盘幂等请求",
            )

    # The task row serializes every legitimate mutation of this evidence.  A
    # coordinate-only pre-read therefore lets us lock the complete historical
    # principal graph before any round/evidence lock, without trusting those
    # rows as business evidence until the owner helper has pinned the graph.
    user_ids = _task_principal_user_ids(
        db,
        task_id=task.id,
        supplied_user_ids=(
            supplied_actor.user_id,
            *(row.assignee_user_id for row in checked.assignments),
        ),
    )
    terminal_principal_graph: object | None = None
    if terminal_root is not None:
        terminal_principal_graph = _lock_terminal_replay_principal_graph(
            db,
            task_id=task.id,
            supplied_user_ids=tuple(sorted(user_ids)),
        )
        terminal_graph = _lock_terminal_replay_graph(
            db,
            root=terminal_root,
            principal_graph=terminal_principal_graph,
        )
    else:
        lock_formal_principal_graph(db, tuple(sorted(user_ids)))

    source_round_stmt = (
        select(StocktakeRound)
        .where(
            StocktakeRound.id == checked.source_round_id,
            StocktakeRound.task_id == checked.task_id,
        )
    )
    if terminal_graph is None:
        source_round_stmt = source_round_stmt.with_for_update()
    source_round = db.scalar(
        source_round_stmt.execution_options(populate_existing=True)
    )
    if source_round is None:
        _fail("opening_recount_source_round_not_found", "not_found", "期初盘点来源轮次不存在")
    if terminal_graph is None:
        lock_opening_stocktake_task_evidence(db, task.id, source_round.id)

    # Refresh the idempotency fact only after the canonical task-local graph is
    # locked.  The key advisory plus the unique constraint protects an absent
    # row; PostgreSQL formal roles must not issue a direct row lock here.
    existing_by_key = db.scalar(
        select(StocktakeRecountCase)
        .where(StocktakeRecountCase.idempotency_key_hash == key_hash)
        .execution_options(populate_existing=True)
    )
    if existing_by_key is not None and (
        existing_by_key.task_id != checked.task_id
        or existing_by_key.source_round_id != checked.source_round_id
    ):
        _idempotency_conflict()
    if terminal_graph is not None and existing_by_key is None:
        _invalid_replay("复盘幂等事实在终态预锁后缺失")

    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task.id)
            .order_by(FormalStocktakeScope.scope_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    location_ids = tuple(sorted({row.location_id for row in scopes}, key=str))
    disposition_resolutions = (
        _terminal_replay_disposition_resolutions(terminal_graph)
        if terminal_graph is not None
        else _lock_recount_reference_graph(
            db,
            task=task,
            scopes=scopes,
        )
    )
    location_stmt = (
        select(StockLocation)
        .where(StockLocation.id.in_(location_ids))
        .order_by(StockLocation.id)
    )
    custody_stmt = (
        select(CustodyAssignment)
        .where(CustodyAssignment.location_id.in_(location_ids))
        .order_by(CustodyAssignment.location_id, CustodyAssignment.id)
    )
    if db.get_bind().dialect.name != "postgresql":
        location_stmt = location_stmt.with_for_update()
        custody_stmt = custody_stmt.with_for_update()
    locations = {
        row.id: row
        for row in db.scalars(
            location_stmt.execution_options(populate_existing=True)
        ).all()
    }
    custodies = tuple(
        db.scalars(
            custody_stmt.execution_options(populate_existing=True)
        ).all()
    )
    freezes_stmt = (
        select(InventoryFreeze)
        .where(InventoryFreeze.task_id == task.id)
        .order_by(InventoryFreeze.stocktake_scope_id)
    )
    rounds_stmt = (
        select(StocktakeRound)
        .where(StocktakeRound.task_id == task.id)
        .order_by(StocktakeRound.round_no)
    )
    if terminal_graph is None:
        freezes_stmt = freezes_stmt.with_for_update()
        rounds_stmt = rounds_stmt.with_for_update()
    freezes = tuple(
        db.scalars(
            freezes_stmt.execution_options(populate_existing=True)
        ).all()
    )
    all_rounds = tuple(
        db.scalars(
            rounds_stmt.execution_options(populate_existing=True)
        ).all()
    )
    scope_completions = tuple(
        db.scalars(
            select(StocktakeScopeCountCompletion)
            .where(
                StocktakeScopeCountCompletion.task_id == task.id,
                StocktakeScopeCountCompletion.round_id == source_round.id,
            )
            .order_by(StocktakeScopeCountCompletion.scope_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    submissions = tuple(
        db.scalars(
            select(StocktakeRoundSubmission)
            .where(
                StocktakeRoundSubmission.task_id == task.id,
                StocktakeRoundSubmission.round_id == source_round.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    completions = tuple(
        db.scalars(
            select(StocktakeDifferenceSetCompletion)
            .where(
                StocktakeDifferenceSetCompletion.task_id == task.id,
                StocktakeDifferenceSetCompletion.round_id == source_round.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    reviews = tuple(
        db.scalars(
            select(StocktakeReview)
            .where(
                StocktakeReview.task_id == task.id,
                StocktakeReview.round_id == source_round.id,
            )
            .order_by(StocktakeReview.reviewed_at, StocktakeReview.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    differences = tuple(
        db.scalars(
            select(StocktakeDifference)
            .where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.round_id == source_round.id,
            )
            .order_by(StocktakeDifference.difference_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    count_lines = tuple(
        db.scalars(
            select(StocktakeCountLine)
            .where(
                StocktakeCountLine.task_id == task.id,
                StocktakeCountLine.round_id == source_round.id,
            )
            .order_by(StocktakeCountLine.stock_account_id, StocktakeCountLine.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    count_line_ids = tuple(row.id for row in count_lines)
    count_serials = tuple(
        db.scalars(
            select(StocktakeCountSerial)
            .where(StocktakeCountSerial.count_line_id.in_(count_line_ids))
            .order_by(
                StocktakeCountSerial.count_line_id,
                StocktakeCountSerial.serial_id,
            )
            .execution_options(populate_existing=True)
        ).all()
        if count_line_ids
        else ()
    )
    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.round_id == source_round.id,
            )
            .order_by(StocktakeCountObservation.observation_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    review_ids = tuple(row.id for row in reviews)
    review_items = tuple(
        db.scalars(
            select(StocktakeReviewItem)
            .where(StocktakeReviewItem.review_id.in_(review_ids))
            .order_by(StocktakeReviewItem.review_id, StocktakeReviewItem.difference_id)
            .execution_options(populate_existing=True)
        ).all()
        if review_ids
        else ()
    )
    postings = tuple(
        db.scalars(
            select(StocktakePosting)
            .where(
                StocktakePosting.task_id == task.id,
                StocktakePosting.round_id == source_round.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    establishments = tuple(
        db.scalars(
            select(InventoryOpeningEstablishment)
            .where(
                InventoryOpeningEstablishment.task_id == task.id,
                InventoryOpeningEstablishment.round_id == source_round.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )

    existing_for_source = tuple(
        db.scalars(
            select(StocktakeRecountCase)
            .where(StocktakeRecountCase.source_round_id == source_round.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    stored_assignments = tuple(
        db.scalars(
            select(StocktakeRecountScopeAssignment)
            .where(
                StocktakeRecountScopeAssignment.recount_case_id
                == existing_by_key.id
            )
            .order_by(StocktakeRecountScopeAssignment.scope_id)
            .execution_options(populate_existing=True)
        ).all()
        if existing_by_key is not None
        else ()
    )

    assignment_input_by_scope = {row.scope_id: row for row in checked.assignments}
    source_recount_assignments: dict[
        uuid.UUID, StocktakeRecountScopeAssignment
    ] | None = None
    source_recount_plan: object | None = None
    successor_exists = len(all_rounds) > source_round.round_no
    deferred_replay_chain_target: StocktakeRound | None = None
    deferred_replay_chain_plan: object | None = None
    _validate_source_round_structure(
        task=task,
        source_round=source_round,
        scopes=scopes,
        locations=locations,
        all_rounds=all_rounds,
        allow_downstream=existing_by_key is not None,
    )
    _validate_source_freeze_structure(
        task=task,
        scopes=scopes,
        freezes=freezes,
        allow_downstream=existing_by_key is not None,
    )
    if not reviews:
        _fail(
            "opening_recount_review_required",
            "precondition_failed",
            "来源轮次尚无明确复盘结论",
        )
    if source_round.round_no > 1:
        try:
            source_recount_plan = (
                _plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph(
                    db,
                    task=task,
                    round_row=source_round,
                    scopes=scopes,
                    freezes=freezes,
                    disposition_resolutions=disposition_resolutions,
                )
            )
            source_recount_assignments = _opening_recount_assignments_from_plan(
                db,
                plan=source_recount_plan,
            )
        except OpeningStocktakeRecountError as exc:
            if existing_by_key is not None:
                _invalid_replay(
                    "复盘幂等事实的完整前驱或后继链无法重证",
                    cause=exc,
                )
            raise
    elif existing_by_key is not None and successor_exists:
        # Validate the original source first so its established public error
        # contract is preserved, then re-prove the complete successor chain.
        deferred_replay_chain_target = all_rounds[-1]
    try:
        trigger_review_plan = (
            _plan_opening_recount_trigger_review_graph_from_prelocked_reference_graph(
                db,
                task=task,
                round_row=source_round,
                reviews=reviews,
                disposition_resolutions=disposition_resolutions,
            )
        )
        if not scope_completions:
            _source_seal_invalid()
        count_replay_plan = _plan_opening_count_replay_evidence(
            db,
            task,
            source_round,
            scopes,
            scope_completions[0],
            round_assignments=source_recount_assignments,
        )
    except OpeningStocktakeReviewError as exc:
        _fail(
            "opening_recount_trigger_review_invalid",
            "precondition_failed",
            "触发复盘的终端复核图无法重证",
            cause=exc,
        )
    except OpeningStocktakeCountError as exc:
        _source_seal_invalid(cause=exc)

    if deferred_replay_chain_target is not None:
        try:
            deferred_replay_chain_plan = (
                _plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph(
                    db,
                    task=task,
                    round_row=deferred_replay_chain_target,
                    scopes=scopes,
                    freezes=freezes,
                    disposition_resolutions=disposition_resolutions,
                )
            )
        except OpeningStocktakeRecountError as exc:
            _invalid_replay(
                "复盘幂等事实的完整后继链无法重证",
                cause=exc,
            )

    _head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    if terminal_graph is not None:
        _validate_terminal_replay_graph(
            db,
            proof=terminal_graph,
            audit_proof=audit_proof,
        )
    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    if source_recount_plan is not None:
        try:
            source_recount_assignments = (
                _validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph(
                    db,
                    plan=source_recount_plan,
                    audit_proof=audit_proof,
                )
            )
        except OpeningStocktakeRecountError as exc:
            if existing_by_key is not None:
                _invalid_replay(
                    "复盘幂等事实的完整前驱或后继链无法重证",
                    cause=exc,
                )
            raise
    _validate_common_graph(
        db,
        task=task,
        source_round=source_round,
        scopes=scopes,
        locations=locations,
        custodies=custodies,
        freezes=freezes,
        all_rounds=all_rounds,
        scope_completions=scope_completions,
        submissions=submissions,
        completions=completions,
        reviews=reviews,
        differences=differences,
        count_lines=count_lines,
        count_serials=count_serials,
        observations=observations,
        review_items=review_items,
        postings=postings,
        establishments=establishments,
        now=now,
        allow_downstream=existing_by_key is not None,
        source_recount_assignments=source_recount_assignments,
        disposition_resolutions=disposition_resolutions,
        trigger_review_plan=trigger_review_plan,
        count_replay_plan=count_replay_plan,
        audit_proof=audit_proof,
    )
    if deferred_replay_chain_target is not None:
        try:
            if deferred_replay_chain_plan is None:
                _invalid_replay("复盘幂等事实缺少后继链结构计划")
            _validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph(
                db,
                plan=deferred_replay_chain_plan,
                audit_proof=audit_proof,
            )
        except OpeningStocktakeRecountError as exc:
            _invalid_replay(
                "复盘幂等事实的完整后继链无法重证",
                cause=exc,
            )
    try:
        trigger_review = _validate_opening_recount_trigger_review_graph_from_prelocked_task_graph(
            db,
            plan=trigger_review_plan,
            audit_proof=audit_proof,
        )
    except OpeningStocktakeReviewError as exc:
        _fail(
            "opening_recount_trigger_review_invalid",
            "precondition_failed",
            "触发复盘的终端复核图无法重证",
            cause=exc,
        )
    submission = submissions[0]
    difference_completion = completions[0]
    scope_manifest = canonical_opening_recount_scope_manifest_sha256(
        task.region_org_id, scopes, freezes
    )
    if task.scope_manifest_sha256 != scope_manifest:
        _fail(
            "opening_recount_scope_manifest_invalid",
            "precondition_failed",
            "期初复盘范围清单无法从锁定事实重算",
        )
    request_sha256 = canonical_opening_recount_request_sha256(
        actor_user_id=current_actor.user_id,
        actor_person_id=current_actor.person_id,
        task_id=task.id,
        source_round_id=source_round.id,
        assignments=checked.assignments,
        reason=checked.reason,
    )

    if existing_by_key is not None:
        _validate_replay(
            db,
            case=existing_by_key,
            stored_assignments=stored_assignments,
            command=checked,
            actor=current_actor,
            task=task,
            source_round=source_round,
            scopes=scopes,
            locations=locations,
            submission=submission,
            completion=difference_completion,
            trigger_review=trigger_review,
            scope_manifest=scope_manifest,
            request_sha256=request_sha256,
            all_rounds=all_rounds,
            audit_proof=audit_proof,
        )
        replay_round = next(
            row for row in all_rounds if row.round_no == existing_by_key.next_round_no
        )
        return _result(existing_by_key, next_round_id=replay_round.id, replayed=True)

    if existing_for_source:
        _fail(
            "opening_recount_source_already_opened",
            "conflict",
            "该来源轮次已经打开不可变复盘轮次",
        )
    if task.status != "recount_required" or task.current_round_no != source_round.round_no:
        _fail(
            "opening_recount_state_invalid",
            "precondition_failed",
            "期初盘点当前状态不允许打开下一轮复盘",
        )
    if set(assignment_input_by_scope) != {row.id for row in scopes}:
        _fail(
            "opening_recount_assignment_scope_set_invalid",
            "invalid_request",
            "复盘必须为每个且仅每个盘点范围指定执行人",
        )

    opener_assignment, opener_grant = _authorize_opener(
        db,
        actor=current_actor,
        task=task,
        now=now,
        lock_rows=False,
    )
    opener_authorization_sha256 = canonical_opening_recount_authorization_sha256(
        authorization_kind="opener",
        user_id=current_actor.user_id,
        person_id=current_actor.person_id,
        assignment_id=opener_assignment.id,
        authorization_version=current_actor.authorization_version,
        role_code=opener_grant.role_code,
        scope_type=opener_grant.scope_type,
        scope_id=opener_grant.scope_id,
        occurred_at=now,
    )
    case_id = uuid.uuid4()
    prepared_assignments: list[_PreparedAssignment] = []
    for scope in scopes:
        value = assignment_input_by_scope[scope.id]
        principal, assignment, grant = _authorize_scope_assignee(
            db,
            user_id=value.assignee_user_id,
            scope=scope,
            location=locations[scope.location_id],
            now=now,
            lock_rows=False,
        )
        authorization_sha256 = canonical_opening_recount_authorization_sha256(
            authorization_kind="assignee",
            user_id=principal.user_id,
            person_id=principal.person_id,
            assignment_id=assignment.id,
            authorization_version=principal.authorization_version,
            role_code=grant.role_code,
            scope_type=grant.scope_type,
            scope_id=grant.scope_id,
            occurred_at=now,
        )
        assignment_sha256 = canonical_opening_recount_assignment_sha256(
            recount_case_id=case_id,
            task_id=task.id,
            source_round_id=source_round.id,
            scope_id=scope.id,
            assignee_user_id=principal.user_id,
            assignee_person_id=principal.person_id,
            assignee_role_assignment_id=assignment.id,
            authorization_version=principal.authorization_version,
            role_code=grant.role_code,
            scope_type=grant.scope_type,
            scope_id_snapshot=grant.scope_id,
            authorization_sha256=authorization_sha256,
            assigned_at=now,
        )
        prepared_assignments.append(
            _PreparedAssignment(
                scope=scope,
                principal=principal,
                assignment=assignment,
                grant=grant,
                authorization_sha256=authorization_sha256,
                assignment_sha256=assignment_sha256,
            )
        )
    assignment_manifest = canonical_opening_recount_assignment_manifest_sha256(
        prepared_assignments
    )
    next_round_no = source_round.round_no + 1
    recount_manifest = canonical_opening_recount_manifest_sha256(
        recount_case_id=case_id,
        task_id=task.id,
        source_round_id=source_round.id,
        source_round_submission_id=submission.id,
        source_round_manifest_sha256=submission.round_manifest_sha256,
        source_count_manifest_sha256=source_round.count_manifest_sha256 or "",
        source_difference_completion_id=difference_completion.id,
        source_difference_manifest_sha256=(
            difference_completion.difference_manifest_sha256
        ),
        trigger_review_id=trigger_review.id,
        trigger_decision_manifest_sha256=trigger_review.decision_manifest_sha256,
        next_round_no=next_round_no,
        scope_count=len(scopes),
        scope_manifest_sha256=scope_manifest,
        assignment_manifest_sha256=assignment_manifest,
        request_sha256=request_sha256,
        authorization_sha256=opener_authorization_sha256,
        reason=checked.reason,
        opened_at=now,
    )
    case = StocktakeRecountCase(
        id=case_id,
        task_id=task.id,
        source_round_id=source_round.id,
        source_round_submission_id=submission.id,
        source_difference_completion_id=difference_completion.id,
        trigger_review_id=trigger_review.id,
        next_round_no=next_round_no,
        scope_count=len(scopes),
        scope_manifest_sha256=scope_manifest,
        assignment_manifest_sha256=assignment_manifest,
        recount_manifest_sha256=recount_manifest,
        request_sha256=request_sha256,
        idempotency_key_hash=key_hash,
        reason=checked.reason,
        opened_by_user_id=current_actor.user_id,
        opened_by_person_id=current_actor.person_id,
        opened_role_assignment_id=opener_assignment.id,
        authorization_version=current_actor.authorization_version,
        role_code=opener_grant.role_code,
        scope_type=opener_grant.scope_type,
        scope_id_snapshot=opener_grant.scope_id,
        authorization_sha256=opener_authorization_sha256,
        opened_at=now,
        created_at=now,
    )
    db.add(case)
    db.flush()

    assignment_rows: list[StocktakeRecountScopeAssignment] = []
    for prepared in prepared_assignments:
        row = StocktakeRecountScopeAssignment(
            id=uuid.uuid4(),
            recount_case_id=case.id,
            task_id=task.id,
            source_round_id=source_round.id,
            scope_id=prepared.scope.id,
            assignee_user_id=prepared.principal.user_id,
            assignee_person_id=prepared.principal.person_id,
            assignee_role_assignment_id=prepared.assignment.id,
            authorization_version=prepared.principal.authorization_version,
            role_code=prepared.grant.role_code,
            scope_type=prepared.grant.scope_type,
            scope_id_snapshot=prepared.grant.scope_id,
            authorization_sha256=prepared.authorization_sha256,
            assignment_sha256=prepared.assignment_sha256,
            assigned_at=now,
            created_at=now,
        )
        db.add(row)
        assignment_rows.append(row)
    db.flush()
    if canonical_opening_recount_assignment_manifest_sha256(assignment_rows) != assignment_manifest:
        _fail(
            "opening_recount_assignment_manifest_invalid",
            "service_unavailable",
            "复盘执行人清单在写入后无法重算",
        )

    previous_status = task.status
    task.status = "counting"
    task.current_round_no = next_round_no
    task.version += 1
    task.updated_at = now
    db.flush()

    next_round = StocktakeRound(
        id=uuid.uuid4(),
        task_id=task.id,
        round_no=next_round_no,
        round_type="recount",
        status="counting",
        submitted_by_user_id=None,
        started_at=now,
        submitted_at=None,
        count_manifest_sha256=None,
        idempotency_key_hash=_round_key_hash(case.id),
        recount_case_id=case.id,
        created_at=now,
        updated_at=now,
    )
    db.add(next_round)
    db.flush()

    metadata = _side_effect_metadata(case, next_round.id)
    db.add(
        StateTransitionEvent(
            aggregate_type="stocktake_task",
            aggregate_id=str(task.id),
            from_status=previous_status,
            to_status="counting",
            reason="opening_recount_opened",
            actor_id=current_actor.user_id,
            idempotency_key=_event_key("state", case.id),
            occurred_at=now,
            metadata_jsonb=metadata,
            created_at=now,
        )
    )
    db.add(
        OutboxEvent(
            event_type="stocktake.opening.recount_opened",
            aggregate_type="stocktake_task",
            aggregate_id=str(task.id),
            payload_jsonb=metadata,
            status="pending",
            attempts=0,
            idempotency_key=_event_key("outbox", case.id),
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
        actor_user_id=current_actor.user_id,
        action="stocktake.opening.recount_opened",
        aggregate_type="stocktake_recount_case",
        aggregate_id=str(case.id),
        before_jsonb=None,
        after_jsonb={
            **metadata,
            "authorization_sha256": case.authorization_sha256,
            "authorization_version": case.authorization_version,
            "opened_by_person_id": str(case.opened_by_person_id),
            "opened_by_role_assignment_id": str(case.opened_role_assignment_id),
            "opened_by_user_id": case.opened_by_user_id,
            "role_code": case.role_code,
            "scope_id_snapshot": case.scope_id_snapshot,
            "scope_type": case.scope_type,
        },
        request_id=_request_reference(checked_request_id),
        occurred_at=now,
        created_at=now,
    )
    db.flush()
    return OpeningStocktakeRecountResult(
        recount_case_id=case.id,
        task_id=task.id,
        source_round_id=source_round.id,
        next_round_id=next_round.id,
        next_round_no=next_round_no,
        scope_count=len(scopes),
    )


def _validate_common_graph(
    db: Session,
    *,
    task: FormalStocktakeTask,
    source_round: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    locations: Mapping[uuid.UUID, StockLocation],
    custodies: Sequence[CustodyAssignment],
    freezes: Sequence[InventoryFreeze],
    all_rounds: Sequence[StocktakeRound],
    scope_completions: Sequence[StocktakeScopeCountCompletion],
    submissions: Sequence[StocktakeRoundSubmission],
    completions: Sequence[StocktakeDifferenceSetCompletion],
    reviews: Sequence[StocktakeReview],
    differences: Sequence[StocktakeDifference],
    count_lines: Sequence[StocktakeCountLine],
    count_serials: Sequence[StocktakeCountSerial],
    observations: Sequence[StocktakeCountObservation],
    review_items: Sequence[StocktakeReviewItem],
    postings: Sequence[StocktakePosting],
    establishments: Sequence[InventoryOpeningEstablishment],
    now: datetime,
    allow_downstream: bool,
    source_recount_assignments: Mapping[
        uuid.UUID, StocktakeRecountScopeAssignment
    ] | None,
    disposition_resolutions: Mapping[uuid.UUID, object] | None,
    trigger_review_plan: object,
    count_replay_plan: object,
    audit_proof: object,
) -> None:
    _validate_source_round_structure(
        task=task,
        source_round=source_round,
        scopes=scopes,
        locations=locations,
        all_rounds=all_rounds,
        allow_downstream=allow_downstream,
    )
    _validate_source_freeze_structure(
        task=task,
        scopes=scopes,
        freezes=freezes,
        allow_downstream=allow_downstream,
    )
    active_custodies: dict[uuid.UUID, list[CustodyAssignment]] = {
        location_id: [] for location_id in locations
    }
    for custody in custodies:
        if custody.location_id not in active_custodies:
            _fail(
                "opening_recount_custody_graph_invalid",
                "precondition_failed",
                "期初复盘库位保管责任与范围不一致",
            )
        valid_from = _as_utc(custody.valid_from)
        valid_to = _as_utc(custody.valid_to) if custody.valid_to is not None else None
        if valid_from <= now and (valid_to is None or now < valid_to):
            active_custodies[custody.location_id].append(custody)
    if any(len(rows) > 1 for rows in active_custodies.values()):
        _fail(
            "opening_recount_custody_graph_invalid",
            "service_unavailable",
            "期初复盘库位当前保管责任不唯一",
        )
    if any(
        (
            (current_rows := active_custodies[scope.location_id])
            and current_rows[0].custodian_person_id
            or None
        )
        != scope.custodian_person_id_snapshot
        or locations[scope.location_id].custodian_person_id
        not in {
            None,
            current_rows[0].custodian_person_id if current_rows else None,
        }
        or (
            locations[scope.location_id].location_type == "personal"
            and (
                not current_rows
                or locations[scope.location_id].custodian_person_id
                != current_rows[0].custodian_person_id
            )
        )
        for scope in scopes
    ):
        _fail(
            "opening_recount_custody_graph_invalid",
            "precondition_failed",
            "期初复盘库位保管责任已变化或未同步",
        )
    if postings or establishments:
        _fail(
            "opening_recount_source_already_posted",
            "conflict",
            "来源轮次已存在过账或期初成立事实，禁止复盘",
        )
    if len(submissions) != 1 or len(completions) != 1:
        _fail(
            "opening_recount_source_seal_invalid",
            "precondition_failed",
            "来源轮次缺少唯一提交或差异完成封印",
        )
    submission = submissions[0]
    completion = completions[0]
    _validate_submitted_source_round_seal(
        db,
        task=task,
        source_round=source_round,
        scopes=scopes,
        scope_completions=scope_completions,
        submission=submission,
        count_lines=count_lines,
        count_serials=count_serials,
        observations=observations,
        require_task_submission_pointer=(
            task.current_round_no == source_round.round_no
        ),
        source_assignments=source_recount_assignments,
    )
    submitted_at = _as_utc(source_round.submitted_at)
    if (
        submission.task_id != task.id
        or submission.round_id != source_round.id
        or submission.scope_count != len(scopes)
        or submission.count_manifest_sha256 != source_round.count_manifest_sha256
        or _as_utc(submission.submitted_at) != submitted_at
        or _as_utc(submission.created_at) != submitted_at
        or not all(
            _SHA256.fullmatch(value or "")
            for value in (
                submission.round_manifest_sha256,
                submission.count_manifest_sha256,
                submission.request_sha256,
                submission.idempotency_key_hash,
            )
        )
        or completion.task_id != task.id
        or completion.round_id != source_round.id
        or completion.round_submission_id != submission.id
        or _as_utc(completion.completed_at) < submitted_at
        or _as_utc(completion.created_at) != _as_utc(completion.completed_at)
    ):
        _fail(
            "opening_recount_source_seal_invalid",
            "precondition_failed",
            "来源轮次提交或差异完成封印与轮次不一致",
        )
    try:
        _validate_difference_set_completion(
            db,
            task=task,
            round_row=source_round,
            submission=submission,
            completion=completion,
        )
    except OpeningStocktakeCountError as exc:
        _fail(
            "opening_recount_difference_manifest_invalid",
            "precondition_failed",
            "来源轮次差异集合无法从锁定事实重算",
            cause=exc,
        )
    if not reviews:
        _fail(
            "opening_recount_review_required",
            "precondition_failed",
            "来源轮次尚无明确复盘结论",
        )
    try:
        trigger = _validate_opening_recount_trigger_review_graph_from_prelocked_task_graph(
            db,
            plan=trigger_review_plan,
            audit_proof=audit_proof,
        )
    except OpeningStocktakeReviewError as exc:
        _fail(
            "opening_recount_trigger_review_invalid",
            "precondition_failed",
            "触发复盘的终端复核图无法重证",
            cause=exc,
        )
    if _as_utc(trigger.reviewed_at) < _as_utc(completion.completed_at) or _as_utc(trigger.reviewed_at) > now:
        _fail(
            "opening_recount_review_chronology_invalid",
            "precondition_failed",
            "复盘结论与来源封印的数据库时间顺序无效",
        )
    trigger_items = [row for row in review_items if row.review_id == trigger.id]
    decisions = {row.difference_id: row.decision for row in trigger_items}
    if (
        len(trigger_items) != len(differences)
        or len(decisions) != len(trigger_items)
        or set(decisions) != {row.id for row in differences}
    ):
        _fail(
            "opening_recount_trigger_review_invalid",
            "precondition_failed",
            "触发复盘的逐项复核事实不完整",
        )
    try:
        review_manifest = canonical_opening_decision_manifest_sha256(
            task_id=task.id,
            round_id=source_round.id,
            differences=differences,
            decisions=decisions,
        )
    except (TypeError, ValueError):
        review_manifest = ""
    if trigger.decision_manifest_sha256 != review_manifest:
        _fail(
            "opening_recount_trigger_review_manifest_invalid",
            "precondition_failed",
            "触发复盘的复核清单无法从逐项事实重算",
        )
    try:
        _validate_opening_count_replay_evidence_from_prelocked_task_graph(
            db,
            plan=count_replay_plan,
            audit_proof=audit_proof,
        )
    except OpeningStocktakeCountError as exc:
        _fail(
            "opening_recount_source_submitted_seal_invalid",
            "precondition_failed",
            "来源初盘副作用证据无法从审计预锁图重证",
            cause=exc,
        )


def _validate_source_round_structure(
    *,
    task: FormalStocktakeTask,
    source_round: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    locations: Mapping[uuid.UUID, StockLocation],
    all_rounds: Sequence[StocktakeRound],
    allow_downstream: bool,
) -> None:
    if (
        task.task_type != "opening"
        or source_round.status != "submitted"
        or source_round.started_at is None
        or source_round.submitted_at is None
        or source_round.count_manifest_sha256 is None
        or not _SHA256.fullmatch(source_round.count_manifest_sha256)
        or not scopes
        or [row.scope_no for row in scopes] != list(range(1, len(scopes) + 1))
        or any(row.task_id != task.id for row in scopes)
        or set(locations) != {row.location_id for row in scopes}
        or any(
            (location := locations[row.location_id]).status != "active"
            or location.location_type not in {"region", "personal"}
            for row in scopes
        )
        or not all_rounds
        or [row.round_no for row in all_rounds]
        != list(range(1, len(all_rounds) + 1))
        or task.current_round_no != len(all_rounds)
        or any(row.status == "superseded" for row in all_rounds)
        or any(row.status != "submitted" for row in all_rounds[:-1])
        or all_rounds[-1].status not in {"counting", "submitted"}
        or (all_rounds[-1].status == "counting" and task.status != "counting")
        or all_rounds[source_round.round_no - 1].id != source_round.id
        or (source_round.round_no == 1 and (
            source_round.round_type != "initial" or source_round.recount_case_id is not None
        ))
        or (source_round.round_no > 1 and (
            source_round.round_type != "recount" or source_round.recount_case_id is None
        ))
        or (
            not allow_downstream
            and len(all_rounds) != source_round.round_no
        )
        or (
            allow_downstream
            and len(all_rounds) < source_round.round_no + 1
        )
    ):
        _fail(
            "opening_recount_source_graph_invalid",
            "precondition_failed",
            "期初复盘来源轮次图不完整或不是不可变连续链",
        )


def _validate_source_freeze_structure(
    *,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    freezes: Sequence[InventoryFreeze],
    allow_downstream: bool,
) -> None:
    freeze_by_scope = {row.stocktake_scope_id: row for row in freezes}
    released_freezes = allow_downstream and task.status in {"posted", "closed"}
    if (
        len(freezes) != len(scopes)
        or len(freeze_by_scope) != len(scopes)
        or any(
            (freeze := freeze_by_scope.get(scope.id)) is None
            or freeze.task_id != task.id
            or freeze.scope_key != scope.scope_key
            or (
                released_freezes
                and (
                    freeze.status != "released"
                    or freeze.valid_to is None
                    or freeze.released_by_user_id is None
                    or not freeze.release_reason.strip()
                )
            )
            or (
                not released_freezes
                and (freeze.status != "active" or freeze.valid_to is not None)
            )
            for scope in scopes
        )
    ):
        _fail(
            "opening_recount_freeze_graph_invalid",
            "precondition_failed",
            "期初复盘冻结范围不完整或已失效",
        )


def _validate_submitted_source_round_seal(
    db: Session,
    *,
    task: FormalStocktakeTask,
    source_round: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    scope_completions: Sequence[StocktakeScopeCountCompletion],
    submission: StocktakeRoundSubmission,
    count_lines: Sequence[StocktakeCountLine],
    count_serials: Sequence[StocktakeCountSerial],
    observations: Sequence[StocktakeCountObservation],
    require_task_submission_pointer: bool,
    source_assignments: Mapping[
        uuid.UUID, StocktakeRecountScopeAssignment
    ] | None,
) -> None:
    """Distrustfully recompute one submitted source round's full count seal.

    Review and ``recount_required`` are downstream task states, so this helper
    intentionally validates the immutable round/submission evidence rather
    than requiring ``task.status == 'submitted'``.  Every child row was locked
    by the caller before this recomputation.
    """

    completion_by_scope = {row.scope_id: row for row in scope_completions}
    scope_ids = {row.id for row in scopes}
    count_line_ids = {row.id for row in count_lines}
    if (
        len(scope_completions) != len(scopes)
        or len(completion_by_scope) != len(scope_completions)
        or set(completion_by_scope) != scope_ids
        or any(row.scope_id not in scope_ids for row in count_lines)
        or any(row.scope_id not in scope_ids for row in observations)
        or any(row.count_line_id not in count_line_ids for row in count_serials)
    ):
        _source_seal_invalid()
    try:
        for scope in scopes:
            _validate_completion_evidence(
                db,
                task,
                source_round,
                scope,
                completion_by_scope[scope.id],
                expected_recount_assignment=(
                    source_assignments.get(scope.id)
                    if source_assignments is not None
                    else None
                ),
            )
    except OpeningStocktakeCountError as exc:
        _source_seal_invalid(cause=exc)

    completion_ids = {row.id for row in scope_completions}
    sealing = next(
        (
            row
            for row in scope_completions
            if row.id == submission.sealing_completion_id
        ),
        None,
    )
    if sealing is None or submission.sealing_completion_id not in completion_ids:
        _source_seal_invalid()
    expected_round_manifest = _round_manifest_sha256(
        task.id,
        source_round.id,
        scope_completions,
        submission.sealing_completion_id,
    )
    try:
        expected_count_manifest = canonical_opening_count_manifest_sha256(
            task,
            source_round,
            count_lines,
            count_serials,
        )
    except (InventoryPostingError, TypeError, ValueError) as exc:
        _source_seal_invalid(cause=exc)
    submitted_at = _as_utc(source_round.submitted_at)  # validated non-null above
    started_at = _as_utc(source_round.started_at)
    task_submitted_at = (
        _as_utc(task.submitted_at) if task.submitted_at is not None else None
    )
    expected_submission_request = _count_hash_document(
        {
            "count_manifest_sha256": expected_count_manifest,
            "round_id": str(source_round.id),
            "round_manifest_sha256": expected_round_manifest,
            "schema": "cloud_oam.opening_stocktake.round_submission_request.v3",
            "sealing_completion_id": str(submission.sealing_completion_id),
        }
    )
    if (
        submission.task_id != task.id
        or submission.round_id != source_round.id
        or submission.scope_count != len(scope_completions)
        or submission.zero_scope_count
        != sum(1 for row in scope_completions if row.zero_confirmed)
        or submission.count_line_count
        != sum(row.count_line_count for row in scope_completions)
        or submission.observation_line_count
        != sum(row.observation_line_count for row in scope_completions)
        or submission.serial_count
        != sum(row.serial_count for row in scope_completions)
        or submission.total_counted_qty
        != sum(
            (row.total_counted_qty for row in scope_completions),
            start=Decimal("0.000"),
        )
        or submission.round_manifest_sha256 != expected_round_manifest
        or submission.count_manifest_sha256 != expected_count_manifest
        or source_round.count_manifest_sha256 != expected_count_manifest
        or submission.request_sha256 != expected_submission_request
        or submission.idempotency_key_hash
        != _count_event_hash("round-submission", source_round.id, task.id)
        or submission.submitted_by_user_id != sealing.completed_by_user_id
        or submission.submitted_by_person_id != sealing.completed_by_person_id
        or submission.submitted_role_assignment_id
        != sealing.completed_role_assignment_id
        or submission.authorization_version != sealing.authorization_version
        or any(
            not started_at <= _as_utc(row.completed_at) <= submitted_at
            for row in scope_completions
        )
        or _as_utc(sealing.completed_at) != submitted_at
        or source_round.submitted_by_user_id != submission.submitted_by_user_id
        or _as_utc(submission.submitted_at) != submitted_at
        or _as_utc(submission.created_at) != submitted_at
        or (
            require_task_submission_pointer
            and task_submitted_at != submitted_at
        )
    ):
        _source_seal_invalid()


def _source_seal_invalid(*, cause: Exception | None = None) -> None:
    _fail(
        "opening_recount_source_submitted_seal_invalid",
        "precondition_failed",
        "来源初盘的逐范围实盘、轮次提交或历史授权封印无法重算",
        cause=cause,
    )


def _plan_count_round_side_effects(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    scope_completions: Sequence[StocktakeScopeCountCompletion],
    round_assignments: Mapping[
        uuid.UUID, StocktakeRecountScopeAssignment
    ] | None,
    collector: _RecountEvidencePlanCollector,
) -> None:
    if not scope_completions:
        _source_seal_invalid()
    try:
        collector.count_plans.append(
            _plan_opening_count_replay_evidence(
                db,
                task,
                round_row,
                scopes,
                scope_completions[0],
                round_assignments=round_assignments,
            )
        )
    except OpeningStocktakeCountError as exc:
        _source_seal_invalid(cause=exc)


def _latest_recount_review(reviews: Sequence[StocktakeReview]) -> StocktakeReview:
    if not reviews:
        _fail(
            "opening_recount_review_required",
            "precondition_failed",
            "来源轮次尚无明确复盘结论",
        )
    latest_time = max(_as_utc(row.reviewed_at) for row in reviews)
    latest = [row for row in reviews if _as_utc(row.reviewed_at) == latest_time]
    if len(latest) != 1 or latest[0].decision != "recount":
        _fail(
            "opening_recount_latest_review_invalid",
            "precondition_failed",
            "来源轮次最新且唯一的复核结论必须明确为复盘",
        )
    return latest[0]


def _authorize_opener(
    db: Session,
    *,
    actor: FormalPrincipal,
    task: FormalStocktakeTask,
    now: datetime,
    lock_rows: bool = True,
) -> tuple[RoleAssignment, ScopeGrant]:
    candidates = sorted(
        (
            grant
            for grant in actor.assignments
            if (
                grant.role_code == "admin"
                and grant.scope_type == "national"
                and grant.scope_id == "*"
            )
            or (
                grant.role_code == "provincial_manager"
                and grant.scope_type == "organization"
                and _same_uuid(grant.scope_id, task.region_org_id)
            )
        ),
        key=lambda row: (row.role_code == "admin", str(row.assignment_id)),
    )
    selected = next(
        (
            grant
            for grant in candidates
            if _grant_allows(
                db,
                actor,
                grant,
                action="manage",
                target_scope_type="organization",
                target_scope_id=str(task.region_org_id),
            )
        ),
        None,
    )
    if selected is None:
        _fail(
            "opening_recount_opener_forbidden",
            "forbidden",
            "只有全国总部管理员或任务区域负责人可以打开期初复盘",
        )
    return (
        _lock_selected_assignment(
            db,
            actor,
            selected,
            now,
            lock_rows=lock_rows,
        ),
        selected,
    )


def _authorize_scope_assignee(
    db: Session,
    *,
    user_id: str,
    scope: FormalStocktakeScope,
    location: StockLocation,
    now: datetime,
    lock_rows: bool = True,
) -> tuple[FormalPrincipal, RoleAssignment, ScopeGrant]:
    try:
        principal = load_formal_principal(db, user_id, now=now)
    except FormalAccessError as exc:
        _fail(
            "opening_recount_assignee_not_current",
            "precondition_failed",
            "复盘执行账号没有当前有效的正式身份和授权",
            cause=exc,
        )
    if (
        principal.account_status != "active"
        or principal.employment_status != "active"
        or principal.access_mode != "active"
    ):
        _fail(
            "opening_recount_assignee_inactive",
            "precondition_failed",
            "复盘执行账号或人员不是启用状态",
        )
    candidates = sorted(
        (
            grant
            for grant in principal.assignments
            if (
                grant.role_code == "admin"
                and grant.scope_type == "national"
                and grant.scope_id == "*"
            )
            or (
                grant.role_code == "provincial_manager"
                and grant.scope_type == "organization"
                and _same_uuid(grant.scope_id, scope.owner_org_id)
            )
            or (
                grant.role_code == "technician"
                and grant.scope_type == "person"
                and location.location_type == "personal"
                and scope.custodian_person_id_snapshot is not None
                and location.custodian_person_id
                == scope.custodian_person_id_snapshot
                and principal.person_id == scope.custodian_person_id_snapshot
                and _same_uuid(grant.scope_id, scope.custodian_person_id_snapshot)
            )
        ),
        key=lambda row: (
            {"technician": 0, "provincial_manager": 1, "admin": 2}.get(
                row.role_code, 9
            ),
            str(row.assignment_id),
        ),
    )
    selected: ScopeGrant | None = None
    for grant in candidates:
        target_type = "person" if grant.role_code == "technician" else "organization"
        target_id = (
            str(scope.custodian_person_id_snapshot)
            if grant.role_code == "technician"
            else str(scope.owner_org_id)
        )
        if _grant_allows(
            db,
            principal,
            grant,
            action="count",
            target_scope_type=target_type,
            target_scope_id=target_id,
        ):
            selected = grant
            break
    if selected is None:
        _fail(
            "opening_recount_assignee_scope_forbidden",
            "forbidden",
            "复盘执行人必须是全国管理员、资产组织负责人或该范围保管工程师",
        )
    assignment = _lock_selected_assignment(
        db,
        principal,
        selected,
        now,
        lock_rows=lock_rows,
    )
    return principal, assignment, selected


def _grant_allows(
    db: Session,
    principal: FormalPrincipal,
    grant: ScopeGrant,
    *,
    action: str,
    target_scope_type: str,
    target_scope_id: str,
) -> bool:
    selected = replace(
        principal,
        assignments=(grant,),
        entitlements=tuple(
            row for row in principal.entitlements if row.assignment_id == grant.assignment_id
        ),
    )
    try:
        return principal.allows(
            db,
            "stocktake",
            action,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
        ) and selected.allows(
            db,
            "stocktake",
            action,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
        )
    except FormalAccessError as exc:
        _fail(
            "opening_recount_authorization_invalid",
            "forbidden",
            "期初复盘权限图无效",
            cause=exc,
        )


def _lock_selected_assignment(
    db: Session,
    principal: FormalPrincipal,
    grant: ScopeGrant,
    now: datetime,
    *,
    lock_rows: bool = True,
) -> RoleAssignment:
    assignment_statement = select(RoleAssignment).where(
        RoleAssignment.id == grant.assignment_id
    )
    if lock_rows:
        assignment_statement = assignment_statement.with_for_update()
    assignment = db.scalar(
        assignment_statement.execution_options(populate_existing=True)
    )
    role = (
        db.scalar(
            select(Role)
            .where(Role.id == assignment.role_id)
            .execution_options(populate_existing=True)
        )
        if assignment is not None
        else None
    )
    if (
        assignment is None
        or role is None
        or assignment.user_id != principal.user_id
        or assignment.status != "active"
        or assignment.revoked_at is not None
        or _as_utc(assignment.valid_from) > now
        or (assignment.valid_to is not None and now >= _as_utc(assignment.valid_to))
        or role.status != "active"
        or role.is_external
        or role.code != grant.role_code
        or assignment.scope_type != grant.scope_type
        or assignment.scope_id != grant.scope_id
    ):
        _fail(
            "opening_recount_assignment_not_current",
            "precondition_failed",
            "期初复盘授权在锁定时已变化，请重新读取",
        )
    return assignment


def _validate_replay(
    db: Session,
    *,
    case: StocktakeRecountCase,
    stored_assignments: Sequence[StocktakeRecountScopeAssignment],
    command: OpenOpeningStocktakeRecountCommand,
    actor: FormalPrincipal,
    task: FormalStocktakeTask,
    source_round: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    locations: Mapping[uuid.UUID, StockLocation],
    submission: StocktakeRoundSubmission,
    completion: StocktakeDifferenceSetCompletion,
    trigger_review: StocktakeReview,
    scope_manifest: str,
    request_sha256: str,
    all_rounds: Sequence[StocktakeRound],
    audit_proof: object,
) -> None:
    command_assignments = {
        row.scope_id: row.assignee_user_id for row in command.assignments
    }
    if (
        case.opened_by_user_id != actor.user_id
        or case.opened_by_person_id != actor.person_id
        or case.reason != command.reason
        or case.request_sha256 != request_sha256
        or case.source_round_submission_id != submission.id
        or case.source_difference_completion_id != completion.id
        or case.trigger_review_id != trigger_review.id
        or case.next_round_no != source_round.round_no + 1
        or case.scope_count != len(scopes)
        or case.scope_manifest_sha256 != scope_manifest
        or command_assignments
        != {row.scope_id: row.assignee_user_id for row in stored_assignments}
    ):
        _idempotency_conflict()
    if (
        len(stored_assignments) != len(scopes)
        or {row.scope_id for row in stored_assignments} != {row.id for row in scopes}
    ):
        _invalid_replay("复盘执行人历史快照不完整")
    _validate_historical_authorization(
        db,
        user_id=case.opened_by_user_id,
        person_id=case.opened_by_person_id,
        assignment_id=case.opened_role_assignment_id,
        authorization_version=case.authorization_version,
        role_code=case.role_code,
        scope_type=case.scope_type,
        scope_id=case.scope_id_snapshot,
        occurred_at=_as_utc(case.opened_at),
    )
    expected_opener_auth = canonical_opening_recount_authorization_sha256(
        authorization_kind="opener",
        user_id=case.opened_by_user_id,
        person_id=case.opened_by_person_id,
        assignment_id=case.opened_role_assignment_id,
        authorization_version=case.authorization_version,
        role_code=case.role_code,
        scope_type=case.scope_type,
        scope_id=case.scope_id_snapshot,
        occurred_at=_as_utc(case.opened_at),
    )
    scope_by_id = {row.id: row for row in scopes}
    for row in stored_assignments:
        scope = scope_by_id[row.scope_id]
        _validate_historical_authorization(
            db,
            user_id=row.assignee_user_id,
            person_id=row.assignee_person_id,
            assignment_id=row.assignee_role_assignment_id,
            authorization_version=row.authorization_version,
            role_code=row.role_code,
            scope_type=row.scope_type,
            scope_id=row.scope_id_snapshot,
            occurred_at=_as_utc(row.assigned_at),
        )
        if not _assignment_scope_exact(row, scope, locations[scope.location_id]):
            _invalid_replay("复盘执行人历史范围授权不再可验证")
        expected_auth = canonical_opening_recount_authorization_sha256(
            authorization_kind="assignee",
            user_id=row.assignee_user_id,
            person_id=row.assignee_person_id,
            assignment_id=row.assignee_role_assignment_id,
            authorization_version=row.authorization_version,
            role_code=row.role_code,
            scope_type=row.scope_type,
            scope_id=row.scope_id_snapshot,
            occurred_at=_as_utc(row.assigned_at),
        )
        expected_assignment = canonical_opening_recount_assignment_sha256(
            recount_case_id=case.id,
            task_id=case.task_id,
            source_round_id=case.source_round_id,
            scope_id=row.scope_id,
            assignee_user_id=row.assignee_user_id,
            assignee_person_id=row.assignee_person_id,
            assignee_role_assignment_id=row.assignee_role_assignment_id,
            authorization_version=row.authorization_version,
            role_code=row.role_code,
            scope_type=row.scope_type,
            scope_id_snapshot=row.scope_id_snapshot,
            authorization_sha256=expected_auth,
            assigned_at=_as_utc(row.assigned_at),
        )
        if (
            row.authorization_sha256 != expected_auth
            or row.assignment_sha256 != expected_assignment
            or _as_utc(row.created_at) != _as_utc(row.assigned_at)
            or _as_utc(row.assigned_at) < _as_utc(case.opened_at)
        ):
            _invalid_replay("复盘执行人清单或授权摘要已损坏")
    assignment_manifest = canonical_opening_recount_assignment_manifest_sha256(
        stored_assignments
    )
    expected_recount_manifest = canonical_opening_recount_manifest_sha256(
        recount_case_id=case.id,
        task_id=case.task_id,
        source_round_id=case.source_round_id,
        source_round_submission_id=submission.id,
        source_round_manifest_sha256=submission.round_manifest_sha256,
        source_count_manifest_sha256=source_round.count_manifest_sha256 or "",
        source_difference_completion_id=completion.id,
        source_difference_manifest_sha256=completion.difference_manifest_sha256,
        trigger_review_id=trigger_review.id,
        trigger_decision_manifest_sha256=trigger_review.decision_manifest_sha256,
        next_round_no=case.next_round_no,
        scope_count=case.scope_count,
        scope_manifest_sha256=scope_manifest,
        assignment_manifest_sha256=assignment_manifest,
        request_sha256=request_sha256,
        authorization_sha256=expected_opener_auth,
        reason=case.reason,
        opened_at=_as_utc(case.opened_at),
    )
    if (
        case.authorization_sha256 != expected_opener_auth
        or case.assignment_manifest_sha256 != assignment_manifest
        or case.recount_manifest_sha256 != expected_recount_manifest
        or _as_utc(case.created_at) != _as_utc(case.opened_at)
    ):
        _invalid_replay("复盘因果清单或历史授权摘要已损坏")
    next_rows = [row for row in all_rounds if row.round_no == case.next_round_no]
    if len(next_rows) != 1:
        _invalid_replay("复盘后继轮次缺失或重复")
    next_round = next_rows[0]
    if (
        next_round.task_id != task.id
        or next_round.round_type != "recount"
        or next_round.recount_case_id != case.id
        or next_round.idempotency_key_hash != _round_key_hash(case.id)
        or _as_utc(next_round.started_at) < _as_utc(case.opened_at)
    ):
        _invalid_replay("复盘后继轮次与因果事实不一致")
    audit_event_id = _capture_side_effects(
        db,
        task=task,
        case=case,
        next_round_id=next_round.id,
    )
    try:
        verified = _verify_audit_event_with_prelocked_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
            event_id=audit_event_id,
        )
    except AuditChainError as exc:
        _invalid_replay("复盘库存审计链无法从预锁证明重证", cause=exc)
    if verified.id != audit_event_id:
        _invalid_replay("复盘审计事件坐标不一致")


def _validate_historical_authorization(
    db: Session,
    *,
    user_id: str,
    person_id: uuid.UUID,
    assignment_id: uuid.UUID,
    authorization_version: int,
    role_code: str,
    scope_type: str,
    scope_id: str,
    occurred_at: datetime,
) -> None:
    user = db.scalar(select(User).where(User.id == user_id).execution_options(populate_existing=True))
    assignment = db.scalar(
        select(RoleAssignment)
        .where(RoleAssignment.id == assignment_id)
        .execution_options(populate_existing=True)
    )
    role = (
        db.scalar(select(Role).where(Role.id == assignment.role_id).execution_options(populate_existing=True))
        if assignment is not None
        else None
    )
    if (
        user is None
        or assignment is None
        or role is None
        or user.person_id != person_id
        or user.authorization_version < authorization_version
        or assignment.user_id != user_id
        or assignment.status not in {"active", "expired", "revoked"}
        or _as_utc(assignment.valid_from) > occurred_at
        or (assignment.valid_to is not None and occurred_at >= _as_utc(assignment.valid_to))
        or (assignment.revoked_at is not None and occurred_at >= _as_utc(assignment.revoked_at))
        or role.is_external
        or role.code != role_code
        or assignment.scope_type != scope_type
        or assignment.scope_id != scope_id
    ):
        _invalid_replay("复盘历史授权无法从锁定角色图验证")


def _assignment_scope_exact(
    row: StocktakeRecountScopeAssignment,
    scope: FormalStocktakeScope,
    location: StockLocation,
) -> bool:
    return bool(
        (
            row.role_code == "admin"
            and row.scope_type == "national"
            and row.scope_id_snapshot == "*"
        )
        or (
            row.role_code == "provincial_manager"
            and row.scope_type == "organization"
            and _same_uuid(row.scope_id_snapshot, scope.owner_org_id)
        )
        or (
            row.role_code == "technician"
            and row.scope_type == "person"
            and location.location_type == "personal"
            and scope.custodian_person_id_snapshot is not None
            and location.custodian_person_id
            == scope.custodian_person_id_snapshot
            and row.assignee_person_id == scope.custodian_person_id_snapshot
            and _same_uuid(row.scope_id_snapshot, scope.custodian_person_id_snapshot)
        )
    )


def _capture_side_effects(
    db: Session,
    *,
    task: FormalStocktakeTask,
    case: StocktakeRecountCase,
    next_round_id: uuid.UUID,
) -> uuid.UUID:
    metadata = _side_effect_metadata(case, next_round_id)
    task_cases = tuple(
        db.scalars(
            select(StocktakeRecountCase).where(
                StocktakeRecountCase.task_id == task.id
            )
        ).all()
    )
    cases_by_id = {str(row.id): row for row in task_cases}
    cases_by_edge = {
        (str(row.source_round_id), row.next_round_no): row for row in task_cases
    }
    state_candidates = tuple(
        db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id == str(task.id),
                StateTransitionEvent.reason == "opening_recount_opened",
            )
        ).all()
    )
    outbox_candidates = tuple(
        db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "stocktake_task",
                OutboxEvent.aggregate_id == str(task.id),
                OutboxEvent.event_type == "stocktake.opening.recount_opened",
            )
        ).all()
    )
    state_rows: list[StateTransitionEvent] = []
    for row in state_candidates:
        owner = _recount_effect_owner(
            row.metadata_jsonb,
            cases_by_id=cases_by_id,
            cases_by_edge=cases_by_edge,
        )
        if row.idempotency_key != _event_key("state", owner.id):
            _invalid_replay("复盘状态副作用使用了非规范或重复幂等键")
        if owner.id == case.id:
            state_rows.append(row)
    outbox_rows: list[OutboxEvent] = []
    for row in outbox_candidates:
        owner = _recount_effect_owner(
            row.payload_jsonb,
            cases_by_id=cases_by_id,
            cases_by_edge=cases_by_edge,
        )
        if row.idempotency_key != _event_key("outbox", owner.id):
            _invalid_replay("复盘 Outbox 副作用使用了非规范或重复幂等键")
        if owner.id == case.id:
            outbox_rows.append(row)
    audit_rows = tuple(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.aggregate_type == "stocktake_recount_case",
                AuditEvent.aggregate_id == str(case.id),
            )
        ).all()
    )
    if len(state_rows) != 1 or len(outbox_rows) != 1 or len(audit_rows) != 1:
        _invalid_replay("复盘状态、Outbox 或审计证据不完整")
    state, outbox, audit = state_rows[0], outbox_rows[0], audit_rows[0]
    if (
        state.aggregate_type != "stocktake_task"
        or state.aggregate_id != str(task.id)
        or state.from_status != "recount_required"
        or state.to_status != "counting"
        or state.reason != "opening_recount_opened"
        or state.actor_id != case.opened_by_user_id
        or state.idempotency_key != _event_key("state", case.id)
        or state.metadata_jsonb != metadata
        or _as_utc(state.occurred_at) != _as_utc(case.opened_at)
        or _as_utc(state.created_at) != _as_utc(case.opened_at)
        or outbox.event_type != "stocktake.opening.recount_opened"
        or outbox.aggregate_type != "stocktake_task"
        or outbox.aggregate_id != str(task.id)
        or outbox.payload_jsonb != metadata
        or outbox.idempotency_key != _event_key("outbox", case.id)
        or _as_utc(outbox.available_at) != _as_utc(case.opened_at)
        or _as_utc(outbox.created_at) != _as_utc(case.opened_at)
        or _as_utc(outbox.updated_at) < _as_utc(case.opened_at)
        or (
            outbox.locked_at is not None
            and _as_utc(outbox.locked_at) < _as_utc(case.opened_at)
        )
        or (
            outbox.published_at is not None
            and _as_utc(outbox.published_at) < _as_utc(case.opened_at)
        )
        or audit.stream_key != INVENTORY_STREAM_KEY
        or audit.actor_user_id != case.opened_by_user_id
        or audit.action != "stocktake.opening.recount_opened"
        or audit.aggregate_type != "stocktake_recount_case"
        or audit.aggregate_id != str(case.id)
        or audit.before_jsonb is not None
        or audit.after_jsonb
        != {
            **metadata,
            "authorization_sha256": case.authorization_sha256,
            "authorization_version": case.authorization_version,
            "opened_by_person_id": str(case.opened_by_person_id),
            "opened_by_role_assignment_id": str(case.opened_role_assignment_id),
            "opened_by_user_id": case.opened_by_user_id,
            "role_code": case.role_code,
            "scope_id_snapshot": case.scope_id_snapshot,
            "scope_type": case.scope_type,
        }
        or _as_utc(audit.occurred_at) != _as_utc(case.opened_at)
        or _as_utc(audit.created_at) < _as_utc(case.opened_at)
    ):
        _invalid_replay("复盘状态、Outbox 或审计证据内容不一致")
    return audit.id


def _recount_effect_owner(
    payload: object,
    *,
    cases_by_id: Mapping[str, StocktakeRecountCase],
    cases_by_edge: Mapping[tuple[str, int], StocktakeRecountCase],
) -> StocktakeRecountCase:
    values = payload if isinstance(payload, dict) else {}
    case_id = values.get("recount_case_id")
    source_round_id = values.get("source_round_id")
    next_round_no = values.get("next_round_no")
    owner_by_id = cases_by_id.get(case_id) if isinstance(case_id, str) else None
    owner_by_edge = (
        cases_by_edge.get((source_round_id, next_round_no))
        if isinstance(source_round_id, str)
        and isinstance(next_round_no, int)
        and not isinstance(next_round_no, bool)
        else None
    )
    if (
        owner_by_id is None
        or owner_by_edge is None
        or owner_by_id.id != owner_by_edge.id
    ):
        _invalid_replay("复盘状态或事件业务坐标无法映射唯一 case")
    return owner_by_id


def _side_effect_metadata(
    case: StocktakeRecountCase, next_round_id: uuid.UUID
) -> dict[str, object]:
    return {
        "assignment_manifest_sha256": case.assignment_manifest_sha256,
        "next_round_id": str(next_round_id),
        "next_round_no": case.next_round_no,
        "recount_case_id": str(case.id),
        "recount_manifest_sha256": case.recount_manifest_sha256,
        "scope_count": case.scope_count,
        "source_round_id": str(case.source_round_id),
    }


def canonical_opening_recount_scope_manifest_sha256(
    region_org_id: uuid.UUID,
    scopes: Sequence[FormalStocktakeScope],
    freezes: Sequence[InventoryFreeze],
) -> str:
    """Recompute the immutable opening scope manifest from persisted rows."""

    freeze_by_scope = {row.stocktake_scope_id: row for row in freezes}
    # Keep byte-for-byte ordering parity with
    # inventory_posting.canonical_opening_scope_manifest_sha256.  Scope number
    # alone is not the canonical order for cross-owner/multi-location tasks.
    ordered = sorted(
        scopes,
        key=lambda row: (
            str(row.owner_org_id),
            str(row.location_id),
            row.scope_no,
            str(row.id),
        ),
    )
    return canonical_opening_manifest_sha256(
        {
            "region_org_id": str(region_org_id),
            "schema": "cloud_oam.opening_stocktake.scope_manifest.v1",
            "scopes": [
                {
                    "assignee_user_id": row.assignee_user_id,
                    "custodian_person_id_snapshot": (
                        str(row.custodian_person_id_snapshot)
                        if row.custodian_person_id_snapshot is not None
                        else None
                    ),
                    "freeze_mode": freeze_by_scope[row.id].freeze_mode,
                    "location_id": str(row.location_id),
                    "owner_org_id": str(row.owner_org_id),
                    "scope_key": row.scope_key,
                    "scope_mode": row.scope_mode,
                    "scope_no": row.scope_no,
                    "scope_sha256": row.scope_sha256,
                }
                for row in ordered
            ],
        }
    )


def canonical_opening_recount_authorization_sha256(
    *,
    authorization_kind: str,
    user_id: str,
    person_id: uuid.UUID,
    assignment_id: uuid.UUID,
    authorization_version: int,
    role_code: str,
    scope_type: str,
    scope_id: str,
    occurred_at: datetime,
) -> str:
    if authorization_kind not in {"opener", "assignee"}:
        raise ValueError("authorization_kind must be opener or assignee")
    return _hash_document(
        {
            "assignment_id": str(assignment_id),
            "authorization_kind": authorization_kind,
            "authorization_version": authorization_version,
            "occurred_at": _canonical_timestamp(occurred_at),
            "person_id": str(person_id),
            "role_code": role_code,
            "schema": "cloud_oam.opening_stocktake.recount_authorization.v1",
            "scope_id": scope_id,
            "scope_type": scope_type,
            "user_id": user_id,
        }
    )


def canonical_opening_recount_assignment_sha256(
    *,
    recount_case_id: uuid.UUID,
    task_id: uuid.UUID,
    source_round_id: uuid.UUID,
    scope_id: uuid.UUID,
    assignee_user_id: str,
    assignee_person_id: uuid.UUID,
    assignee_role_assignment_id: uuid.UUID,
    authorization_version: int,
    role_code: str,
    scope_type: str,
    scope_id_snapshot: str,
    authorization_sha256: str,
    assigned_at: datetime,
) -> str:
    return _hash_document(
        {
            "assigned_at": _canonical_timestamp(assigned_at),
            "assignee_person_id": str(assignee_person_id),
            "assignee_role_assignment_id": str(assignee_role_assignment_id),
            "assignee_user_id": assignee_user_id,
            "authorization_sha256": authorization_sha256,
            "authorization_version": authorization_version,
            "recount_case_id": str(recount_case_id),
            "role_code": role_code,
            "schema": "cloud_oam.opening_stocktake.recount_scope_assignment.v1",
            "scope_id": str(scope_id),
            "scope_id_snapshot": scope_id_snapshot,
            "scope_type": scope_type,
            "source_round_id": str(source_round_id),
            "task_id": str(task_id),
        }
    )


def canonical_opening_recount_assignment_manifest_sha256(
    assignments: Sequence[object],
) -> str:
    documents: list[dict[str, object]] = []
    for value in assignments:
        if isinstance(value, _PreparedAssignment):
            documents.append(
                {
                    "assignment_sha256": value.assignment_sha256,
                    "authorization_sha256": value.authorization_sha256,
                    "scope_id": str(value.scope.id),
                }
            )
        elif isinstance(value, StocktakeRecountScopeAssignment):
            documents.append(
                {
                    "assignment_sha256": value.assignment_sha256,
                    "authorization_sha256": value.authorization_sha256,
                    "scope_id": str(value.scope_id),
                }
            )
        else:
            raise TypeError("unsupported recount assignment manifest row")
    documents.sort(key=lambda row: str(row["scope_id"]))
    return _hash_document(
        {
            "assignments": documents,
            "schema": "cloud_oam.opening_stocktake.recount_assignment_manifest.v1",
        }
    )


def canonical_opening_recount_request_sha256(
    *,
    actor_user_id: str,
    actor_person_id: uuid.UUID,
    task_id: uuid.UUID,
    source_round_id: uuid.UUID,
    assignments: Sequence[OpeningStocktakeRecountScopeAssignmentInput],
    reason: str,
) -> str:
    return _hash_document(
        {
            "actor_person_id": str(actor_person_id),
            "actor_user_id": actor_user_id,
            "assignments": [
                {
                    "assignee_user_id": row.assignee_user_id,
                    "scope_id": str(row.scope_id),
                }
                for row in sorted(assignments, key=lambda row: str(row.scope_id))
            ],
            "reason": reason,
            "schema": "cloud_oam.opening_stocktake.recount_request.v1",
            "source_round_id": str(source_round_id),
            "task_id": str(task_id),
        }
    )


def canonical_opening_recount_manifest_sha256(
    *,
    recount_case_id: uuid.UUID,
    task_id: uuid.UUID,
    source_round_id: uuid.UUID,
    source_round_submission_id: uuid.UUID,
    source_round_manifest_sha256: str,
    source_count_manifest_sha256: str,
    source_difference_completion_id: uuid.UUID,
    source_difference_manifest_sha256: str,
    trigger_review_id: uuid.UUID,
    trigger_decision_manifest_sha256: str,
    next_round_no: int,
    scope_count: int,
    scope_manifest_sha256: str,
    assignment_manifest_sha256: str,
    request_sha256: str,
    authorization_sha256: str,
    reason: str,
    opened_at: datetime,
) -> str:
    return _hash_document(
        {
            "assignment_manifest_sha256": assignment_manifest_sha256,
            "authorization_sha256": authorization_sha256,
            "next_round_no": next_round_no,
            "opened_at": _canonical_timestamp(opened_at),
            "reason": reason,
            "recount_case_id": str(recount_case_id),
            "request_sha256": request_sha256,
            "schema": "cloud_oam.opening_stocktake.recount_manifest.v1",
            "scope_count": scope_count,
            "scope_manifest_sha256": scope_manifest_sha256,
            "source_count_manifest_sha256": source_count_manifest_sha256,
            "source_difference_completion_id": str(source_difference_completion_id),
            "source_difference_manifest_sha256": source_difference_manifest_sha256,
            "source_round_id": str(source_round_id),
            "source_round_manifest_sha256": source_round_manifest_sha256,
            "source_round_submission_id": str(source_round_submission_id),
            "task_id": str(task_id),
            "trigger_decision_manifest_sha256": trigger_decision_manifest_sha256,
            "trigger_review_id": str(trigger_review_id),
        }
    )


def _validate_command(
    command: OpenOpeningStocktakeRecountCommand,
) -> OpenOpeningStocktakeRecountCommand:
    if not isinstance(command, OpenOpeningStocktakeRecountCommand):
        _fail("opening_recount_command_required", "invalid_request", "复盘命令类型无效")
    task_id = _require_uuid("task_id", command.task_id)
    source_round_id = _require_uuid("source_round_id", command.source_round_id)
    reason = _require_text("reason", command.reason, 4000, allow_empty=False)
    if not isinstance(command.assignments, tuple) or not command.assignments:
        _fail(
            "opening_recount_assignments_invalid",
            "invalid_request",
            "复盘执行人清单必须是非空不可变元组",
        )
    rows: list[OpeningStocktakeRecountScopeAssignmentInput] = []
    seen: set[uuid.UUID] = set()
    for row in command.assignments:
        if not isinstance(row, OpeningStocktakeRecountScopeAssignmentInput):
            _fail(
                "opening_recount_assignment_invalid",
                "invalid_request",
                "复盘执行人明细类型无效",
            )
        scope_id = _require_uuid("scope_id", row.scope_id)
        user_id = _require_user_id(row.assignee_user_id)
        if scope_id in seen:
            _fail(
                "opening_recount_assignment_duplicate",
                "invalid_request",
                "同一复盘范围不能重复指定执行人",
            )
        seen.add(scope_id)
        rows.append(
            OpeningStocktakeRecountScopeAssignmentInput(
                scope_id=scope_id,
                assignee_user_id=user_id,
            )
        )
    rows.sort(key=lambda row: str(row.scope_id))
    return OpenOpeningStocktakeRecountCommand(
        task_id=task_id,
        source_round_id=source_round_id,
        assignments=tuple(rows),
        reason=reason,
    )


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail("formal_principal_required", "forbidden", "期初复盘必须使用正式权限主体")
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _fail("opening_recount_actor_inactive", "forbidden", "当前账号或人员不可打开复盘")
    return actor


def _require_current_actor(
    db: Session, supplied: FormalPrincipal, now: datetime
) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError as exc:
        _fail(
            "opening_recount_actor_not_current",
            "forbidden",
            "正式权限上下文已失效，请重新读取",
            cause=exc,
        )
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail(
            "opening_recount_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取后再打开复盘",
        )
    return current


def _result(
    case: StocktakeRecountCase,
    *,
    next_round_id: uuid.UUID,
    replayed: bool,
) -> OpeningStocktakeRecountResult:
    # The unique case-to-round binding has already been validated by replay.
    return OpeningStocktakeRecountResult(
        recount_case_id=case.id,
        task_id=case.task_id,
        source_round_id=case.source_round_id,
        next_round_id=next_round_id,
        next_round_no=case.next_round_no,
        scope_count=case.scope_count,
        replayed=replayed,
    )


def _storage_hash(raw_key: str) -> str:
    return hashlib.sha256(
        f"cloud_oam.opening_stocktake.recount.idempotency.v1\0{raw_key}".encode()
    ).hexdigest()


def _round_key_hash(case_id: uuid.UUID) -> str:
    return hashlib.sha256(
        f"cloud_oam.opening_stocktake.recount.round.v1\0{case_id}".encode()
    ).hexdigest()


def _event_key(kind: str, case_id: uuid.UUID) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.opening_stocktake.recount.{kind}.v1\0{case_id}".encode()
    ).hexdigest()
    return f"opening-recount-{kind}-{digest}"


def _request_reference(raw: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.opening_stocktake.recount.request-reference.v1\0{raw}".encode()
    ).hexdigest()
    return f"opening-recount-request-{digest}"


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
                "opening_recount_server_time_unavailable",
                "service_unavailable",
                "数据库服务端时间不可用",
            )
        return _as_utc(value)
    return datetime.now(timezone.utc)


def _hash_document(document: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()


def _canonical_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_idempotency_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 200
        or _PRINTABLE.fullmatch(value) is None
    ):
        _fail(
            "opening_recount_idempotency_key_invalid",
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
            "opening_recount_request_id_invalid",
            "invalid_request",
            "请求标识必须为 8 至 160 位可打印 ASCII 字符",
        )
    return value


def _require_user_id(value: object) -> str:
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 36
        or value != value.strip()
        or any(ord(character) < 33 or ord(character) > 126 for character in value)
    ):
        _fail(
            "opening_recount_assignee_user_id_invalid",
            "invalid_request",
            "复盘执行账号标识格式无效",
        )
    return value


def _require_uuid(field: str, value: object) -> uuid.UUID:
    try:
        checked = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        _fail(f"opening_recount_{field}_invalid", "invalid_request", f"{field} 必须为 UUID")
    if checked.int == 0:
        _fail(f"opening_recount_{field}_invalid", "invalid_request", f"{field} 不能为零 UUID")
    return checked


def _require_text(field: str, value: object, limit: int, *, allow_empty: bool) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or value != value.strip()
        or (not allow_empty and not value)
    ):
        _fail(f"opening_recount_{field}_invalid", "invalid_request", f"{field} 格式无效")
    return value


def _same_uuid(left: object, right: object) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _idempotency_conflict() -> None:
    _fail(
        "opening_recount_idempotency_conflict",
        "conflict",
        "幂等键已绑定不同的期初复盘请求",
    )


def _invalid_replay(message: str, *, cause: Exception | None = None) -> None:
    _fail(
        "opening_recount_idempotency_record_invalid",
        "service_unavailable",
        message,
        cause=cause,
    )


def _fail(
    code: str,
    category: str,
    message: str,
    *,
    cause: Exception | None = None,
) -> None:
    error = OpeningStocktakeRecountError(code, category, message)
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "OpenOpeningStocktakeRecountCommand",
    "OpeningStocktakeRecountError",
    "OpeningStocktakeRecountResult",
    "OpeningStocktakeRecountScopeAssignmentInput",
    "canonical_opening_recount_assignment_manifest_sha256",
    "canonical_opening_recount_assignment_sha256",
    "canonical_opening_recount_authorization_sha256",
    "canonical_opening_recount_manifest_sha256",
    "canonical_opening_recount_request_sha256",
    "canonical_opening_recount_scope_manifest_sha256",
    "open_opening_stocktake_recount",
    "validate_opening_recount_round_assignment_evidence",
]
