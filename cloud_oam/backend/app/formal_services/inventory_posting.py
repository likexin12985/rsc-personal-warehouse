"""Formal V1.0 immutable inventory posting service.

The functions in this module are an internal domain boundary, not a generic
HTTP CRUD surface.  They append one immutable transaction and update the
balance/SN projections in the caller's database transaction.  They flush, but
never commit or roll back.  A caller that receives any exception must roll the
whole transaction back.

PostgreSQL is the production concurrency authority.  Stable transaction-level
advisory locks serialize idempotency and business posting keys before any
ledger row is locked.  SQLite tests exercise validation and atomic state, but
do not claim PostgreSQL locking semantics.
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

from sqlalchemy import and_, or_, select, text, union_all
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    load_formal_principal,
    lock_formal_principal_graph,
)
from ..foundation_models import (
    AuditEvent,
    ExternalObject,
    ExternalObjectVersion,
    Organization,
    OutboxEvent,
    Person,
    Role,
    RoleAssignment,
    SourceSystem,
    StateTransitionEvent,
    SyncBatch,
    SyncInboxEvent,
    SyncRun,
)
from ..inventory_models import (
    FormalMaterial,
    InventoryLedgerHead,
    InventoryMovement,
    InventoryMovementSerial,
    InventorySerial,
    InventoryTransaction,
    MaterialInventoryPolicy,
    SerialCurrentPosition,
    StockAccount,
    StockBalance,
    StockLocation,
)
from ..models import User
from ..stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    InventoryOpeningEstablishment,
    StocktakeControlSnapshotLine,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeCountSerial,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakeObservationDisposition,
    StocktakePosting,
    StocktakePostingItem,
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
    append_audit_event,
)
from .opening_stocktake import (
    OPENING_CONTROL_ENTITY_TYPE,
    canonical_opening_manifest_sha256,
    opening_control_batch_body_sha256,
    opening_control_projection_payload,
)
from .postgresql_lock_graph import (
    lock_inventory_reference_graph,
    lock_inventory_serial_graph,
    lock_opening_stocktake_task_evidence,
)


INVENTORY_LEDGER_HEAD_ID: Final[uuid.UUID] = uuid.UUID(
    "40000000-0000-4000-8000-000000000001"
)
INVENTORY_STREAM_KEY: Final[str] = "inventory"

MOVEMENT_TYPES: Final[frozenset[str]] = frozenset(
    {
        "opening",
        "transfer",
        "reserve",
        "release",
        "pick",
        "outbound",
        "transit",
        "inbound",
        "freeze",
        "unfreeze",
        "consume",
        "return",
        "scrap",
        "stocktake_gain",
        "stocktake_loss",
        "status_change",
        "reversal",
    }
)
EXTERNAL_INBOUND_TYPES: Final[frozenset[str]] = frozenset(
    {"opening", "inbound", "stocktake_gain"}
)
EXTERNAL_OUTBOUND_TYPES: Final[frozenset[str]] = frozenset(
    {"consume", "scrap", "stocktake_loss"}
)
TRACKING_MODES: Final[frozenset[str]] = frozenset(
    {"none", "lot", "serial", "lot_and_serial"}
)

_PRINTABLE = re.compile(r"^[\x21-\x7e]+$", re.ASCII)
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$", re.ASCII)
_MAX_QUANTITY = Decimal("1000000000000000")
_ZERO = Decimal("0.000")
_QUANTITY_QUANTUM = Decimal("0.001")

OPENING_SCOPE_LINE_SCHEMA: Final[str] = (
    "cloud_oam.opening_stocktake.scope_line.v1"
)
OPENING_SCOPE_MANIFEST_SCHEMA: Final[str] = (
    "cloud_oam.opening_stocktake.scope_manifest.v1"
)
OPENING_ACCOUNT_DIMENSION_SCHEMA: Final[str] = (
    "cloud_oam.opening_stocktake.account_dimension.v1"
)
OPENING_SERIAL_SNAPSHOT_SCHEMA: Final[str] = (
    "cloud_oam.opening_stocktake.serial_snapshot.v1"
)
OPENING_SNAPSHOT_MANIFEST_SCHEMA: Final[str] = (
    "cloud_oam.opening_stocktake.snapshot_manifest.v1"
)
OPENING_COUNT_MANIFEST_SCHEMA: Final[str] = (
    "cloud_oam.opening_stocktake.count_manifest.v1"
)
OPENING_CONTROL_MANIFEST_SCHEMA: Final[str] = (
    "cloud_oam.opening_stocktake.control_manifest.v1"
)
OPENING_DECISION_MANIFEST_SCHEMA: Final[str] = (
    "cloud_oam.opening_stocktake.decision_manifest.v1"
)

_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class InventoryPostingError(RuntimeError):
    """Stable, non-database-disclosing failure for an application boundary."""

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
class InventoryMovementCommand:
    from_account_id: uuid.UUID | None
    to_account_id: uuid.UUID | None
    quantity: Decimal
    serial_ids: tuple[uuid.UUID, ...] = ()
    external_boundary_code: str | None = None


@dataclass(frozen=True, slots=True)
class InventoryPostingCommand:
    transaction_no: str
    movement_type: str
    source_document_type: str
    source_document_id: str
    posting_key: str
    effective_at: datetime
    movements: tuple[InventoryMovementCommand, ...]


@dataclass(frozen=True, slots=True)
class InventoryReversalCommand:
    original_transaction_id: uuid.UUID
    transaction_no: str
    source_document_type: str
    source_document_id: str
    posting_key: str
    effective_at: datetime


@dataclass(frozen=True, slots=True)
class InventoryPostingResult:
    transaction_id: uuid.UUID
    transaction_no: str
    ledger_cursor: int
    replayed: bool = False

    @property
    def no(self) -> str:
        """Compatibility spelling for callers that render a generic number."""

        return self.transaction_no


@dataclass(frozen=True, slots=True)
class _InventoryPostingCommit:
    """Internal write result carrying the one transaction-bound audit proof."""

    result: InventoryPostingResult
    audit_proof: object


@dataclass(frozen=True, slots=True)
class _StocktakeInventoryBatchEntry:
    """One already-derived transaction in a task-wide stocktake batch."""

    command: InventoryPostingCommand
    idempotency_key_hash: str
    request_hash: str


@dataclass(frozen=True, slots=True)
class _StocktakeInventoryBatchEntryCommit:
    result: InventoryPostingResult
    movement_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class _StocktakeInventoryBatchCommit:
    entries: tuple[_StocktakeInventoryBatchEntryCommit, ...]
    current_ledger_cursor: int
    audit_proof: object


_PRELOCKED_INVENTORY_LEDGER_HEAD_SEAL: Final[object] = object()


@dataclass(frozen=True, slots=True)
class _PrelockedInventoryLedgerHeadProof:
    """Unforgeable proof that this transaction owns the global ledger head."""

    session: Session
    transaction: object
    head: InventoryLedgerHead
    current_ledger_cursor: int
    seal: object


@dataclass(frozen=True, slots=True)
class _OpeningFinalizationProof:
    """Exact approved-task coordinates required by the opening-only primitive.

    This is defense in depth against accidental direct internal use, not a
    replacement for the PostgreSQL deferred commit-binding constraint between
    opening transactions and their posting/establishment/task facts.
    """

    task_id: uuid.UUID
    expected_task_version: int
    round_id: uuid.UUID
    scope_manifest_sha256: str
    snapshot_manifest_sha256: str
    count_manifest_sha256: str
    control_manifest_sha256: str
    regional_review_id: uuid.UUID
    headquarters_review_id: uuid.UUID


_PRELOCKED_INVENTORY_GRAPH_SEAL: Final[object] = object()
_PRELOCKED_OPENING_PRINCIPAL_GRAPH_SEAL: Final[object] = object()
_PRELOCKED_OPENING_TERMINAL_GRAPH_SEAL: Final[object] = object()
_OPENING_TASK_AUDIT_REPLAY_PLAN_SEAL: Final[object] = object()


@dataclass(frozen=True, slots=True)
class _PrelockedOpeningPrincipalGraphProof:
    """Exact historical task-principal coordinates held in this transaction."""

    session: Session
    transaction: object
    task_ids: tuple[uuid.UUID, ...]
    supplied_user_ids: tuple[str, ...]
    user_ids: tuple[str, ...]
    assignment_signatures: tuple[tuple[object, ...], ...]
    seal: object


@dataclass(frozen=True, slots=True)
class _PrelockedInventoryGraphProof:
    """Internal transaction-bound proof for the opening finalize path only."""

    session: Session
    transaction: object
    command: InventoryPostingCommand
    account_ids: tuple[uuid.UUID, ...]
    balance_account_ids: tuple[uuid.UUID, ...]
    serial_ids: tuple[uuid.UUID, ...]
    seal: object


@dataclass(frozen=True, slots=True)
class _PrelockedOpeningTerminalTaskGraph:
    """Terminal opening coordinates locked before current inventory refs."""

    session: Session
    transaction: object
    task_id: uuid.UUID
    task: FormalStocktakeTask
    round_plans: tuple[tuple[uuid.UUID, object], ...]
    disposition_resolutions_by_round: Mapping[
        uuid.UUID, Mapping[uuid.UUID, object]
    ]
    audit_replay_plan: object
    account_signatures: tuple[tuple[object, ...], ...]
    establishment_signatures: tuple[tuple[object, ...], ...]
    seal: object


@dataclass(frozen=True, slots=True)
class _OpeningTaskAuditReplayPlan:
    """Pre-audit count/recount/review plans for one opening task graph."""

    session: Session
    transaction: object
    task_id: uuid.UUID
    round_ids: tuple[uuid.UUID, ...]
    count_plan: object | None
    recount_plan: object | None
    final_review_plans: tuple[object, ...]
    expected_assignees: tuple[tuple[uuid.UUID, str], ...]
    seal: object


def _require_prelocked_opening_terminal_task_graph(
    db: Session,
    proof: object,
) -> _PrelockedOpeningTerminalTaskGraph:
    """Require one generic terminal graph issued in this exact transaction."""

    transaction = db.get_transaction()
    if (
        not isinstance(proof, _PrelockedOpeningTerminalTaskGraph)
        or proof.seal is not _PRELOCKED_OPENING_TERMINAL_GRAPH_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
        or proof.task_id != proof.task.id
    ):
        _invalid_opening_establishment()
    return proof


def _flatten_opening_disposition_resolutions(
    values_by_round: Mapping[uuid.UUID, Mapping[uuid.UUID, object]],
) -> dict[uuid.UUID, object]:
    """Flatten sealed per-round resolutions without widening coordinates."""

    combined: dict[uuid.UUID, object] = {}
    for round_id in sorted(values_by_round, key=str):
        if not isinstance(round_id, uuid.UUID):
            _invalid_opening_establishment()
        values = values_by_round[round_id]
        if not isinstance(values, Mapping):
            _invalid_opening_establishment()
        for observation_id in sorted(values, key=str):
            if (
                not isinstance(observation_id, uuid.UUID)
                or observation_id in combined
            ):
                _invalid_opening_establishment()
            combined[observation_id] = values[observation_id]
    return combined


def _plan_opening_task_audit_replay(
    db: Session,
    *,
    task: FormalStocktakeTask,
    disposition_resolutions_by_round: Mapping[
        uuid.UUID, Mapping[uuid.UUID, object]
    ],
) -> _OpeningTaskAuditReplayPlan:
    """Capture every count/recount/review audit coordinate before audit."""

    from .opening_stocktake_count import (
        OpeningStocktakeCountError,
        _plan_opening_count_replay_evidence,
    )
    from .opening_stocktake_recount import (
        OpeningStocktakeRecountError,
        _opening_recount_assignments_from_plan,
        _plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph,
    )
    from .opening_stocktake_review import (
        OpeningStocktakeReviewError,
        _plan_opening_review_evidence_from_prelocked_reference_graph,
    )

    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task.id)
            .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id == task.id)
            .order_by(InventoryFreeze.stocktake_scope_id, InventoryFreeze.id)
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
    if (
        not scopes
        or len(freezes) != len(scopes)
        or not rounds
        or [row.round_no for row in rounds]
        != list(range(1, task.current_round_no + 1))
    ):
        _invalid_opening_establishment()
    merged_resolutions = _flatten_opening_disposition_resolutions(
        disposition_resolutions_by_round
    )
    final_round = rounds[-1]
    count_plan: object | None = None
    recount_plan: object | None = None
    try:
        if len(rounds) == 1:
            completions = tuple(
                db.scalars(
                    select(StocktakeScopeCountCompletion)
                    .where(
                        StocktakeScopeCountCompletion.task_id == task.id,
                        StocktakeScopeCountCompletion.round_id == final_round.id,
                    )
                    .order_by(
                        StocktakeScopeCountCompletion.scope_id,
                        StocktakeScopeCountCompletion.id,
                    )
                    .execution_options(populate_existing=True)
                ).all()
            )
            if not completions:
                _invalid_opening_establishment()
            count_plan = _plan_opening_count_replay_evidence(
                db,
                task,
                final_round,
                scopes,
                completions[0],
            )
            expected_assignees = {
                scope.id: scope.assignee_user_id for scope in scopes
            }
        else:
            recount_plan = (
                _plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph(
                    db,
                    task=task,
                    round_row=final_round,
                    scopes=scopes,
                    freezes=freezes,
                    disposition_resolutions=merged_resolutions,
                )
            )
            assignments = _opening_recount_assignments_from_plan(
                db,
                plan=recount_plan,
            )
            expected_assignees = {
                scope_id: row.assignee_user_id
                for scope_id, row in assignments.items()
            }
        reviews = tuple(
            db.scalars(
                select(StocktakeReview)
                .where(
                    StocktakeReview.task_id == task.id,
                    StocktakeReview.round_id == final_round.id,
                )
                .order_by(StocktakeReview.reviewed_at, StocktakeReview.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        regional = tuple(
            row
            for row in reviews
            if row.review_stage == "region" and row.decision == "approve"
        )
        headquarters = tuple(
            row
            for row in reviews
            if row.review_stage == "headquarters" and row.decision == "approve"
        )
        if len(reviews) != 2 or len(regional) != 1 or len(headquarters) != 1:
            _invalid_opening_establishment()
        final_review_plans = (
            _plan_opening_review_evidence_from_prelocked_reference_graph(
                db,
                task_id=task.id,
                round_id=final_round.id,
                review_id=regional[0].id,
                expected_stage="region",
                expected_decision="approve",
                disposition_resolutions=merged_resolutions,
            ),
            _plan_opening_review_evidence_from_prelocked_reference_graph(
                db,
                task_id=task.id,
                round_id=final_round.id,
                review_id=headquarters[0].id,
                expected_stage="headquarters",
                expected_decision="approve",
                disposition_resolutions=merged_resolutions,
            ),
        )
    except (
        OpeningStocktakeCountError,
        OpeningStocktakeRecountError,
        OpeningStocktakeReviewError,
    ):
        _invalid_opening_establishment()
    if set(expected_assignees) != {row.id for row in scopes}:
        _invalid_opening_establishment()
    transaction = db.get_transaction()
    if transaction is None:
        _invalid_opening_establishment()
    return _OpeningTaskAuditReplayPlan(
        session=db,
        transaction=transaction,
        task_id=task.id,
        round_ids=tuple(row.id for row in rounds),
        count_plan=count_plan,
        recount_plan=recount_plan,
        final_review_plans=final_review_plans,
        expected_assignees=tuple(
            sorted(expected_assignees.items(), key=lambda item: str(item[0]))
        ),
        seal=_OPENING_TASK_AUDIT_REPLAY_PLAN_SEAL,
    )


def _validate_opening_task_audit_replay_from_prelocked_graph(
    db: Session,
    *,
    plan: object,
    audit_proof: object,
) -> dict[uuid.UUID, str]:
    """Purely validate one pre-audit task plan with the final audit proof."""

    from .opening_stocktake_count import OpeningStocktakeCountError
    from .opening_stocktake_recount import OpeningStocktakeRecountError
    from .opening_stocktake_review import OpeningStocktakeReviewError

    transaction = db.get_transaction()
    if (
        not isinstance(plan, _OpeningTaskAuditReplayPlan)
        or plan.seal is not _OPENING_TASK_AUDIT_REPLAY_PLAN_SEAL
        or plan.session is not db
        or transaction is None
        or plan.transaction is not transaction
    ):
        _invalid_opening_establishment()
    try:
        _require_prelocked_audit_stream_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
        )
        if plan.recount_plan is not None:
            from .opening_stocktake_recount import (
                _validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph,
            )

            assignments = (
                _validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph(
                    db,
                    plan=plan.recount_plan,
                    audit_proof=audit_proof,
                )
            )
            expected_assignees = {
                scope_id: row.assignee_user_id
                for scope_id, row in assignments.items()
            }
        else:
            from .opening_stocktake_count import (
                _validate_opening_count_replay_evidence_from_prelocked_task_graph,
            )

            if plan.count_plan is None:
                _invalid_opening_establishment()
            _validate_opening_count_replay_evidence_from_prelocked_task_graph(
                db,
                plan=plan.count_plan,
                audit_proof=audit_proof,
            )
            expected_assignees = dict(plan.expected_assignees)
        from .opening_stocktake_review import (
            _validate_opening_review_evidence_from_prelocked_task_graph,
        )

        for review_plan in plan.final_review_plans:
            _validate_opening_review_evidence_from_prelocked_task_graph(
                db,
                plan=review_plan,
                audit_proof=audit_proof,
            )
    except (
        AuditChainError,
        OpeningStocktakeCountError,
        OpeningStocktakeRecountError,
        OpeningStocktakeReviewError,
    ):
        _invalid_opening_establishment()
    if tuple(
        sorted(expected_assignees.items(), key=lambda item: str(item[0]))
    ) != plan.expected_assignees:
        _invalid_opening_establishment()
    return expected_assignees


def _role_assignment_signature(row: RoleAssignment) -> tuple[object, ...]:
    """Seal every mutable field of one historical authorization coordinate."""

    return (
        row.id,
        row.user_id,
        row.role_id,
        row.scope_type,
        row.scope_id,
        row.valid_from,
        row.valid_to,
        row.status,
        row.assigned_by,
        row.revoked_at,
        row.revoked_by,
        row.reason,
        row.created_at,
        row.updated_at,
    )


def _opening_task_principal_user_ids_batch(
    db: Session,
    *,
    task_ids: Sequence[uuid.UUID],
) -> set[str]:
    """Read the complete historical actor union with one set query.

    Keep this field list aligned with disposition's single-task discovery
    boundary.  The set-shaped query prevents terminal read pages from issuing
    one principal-discovery query group per task while preserving every V1
    count/recount/review/disposition/finalize actor coordinate.
    """

    checked_task_ids = tuple(sorted(set(task_ids), key=str))
    if not checked_task_ids:
        return set()
    task_id_strings = tuple(str(task_id) for task_id in checked_task_ids)
    statements = (
        select(FormalStocktakeTask.created_by_user_id.label("user_id")).where(
            FormalStocktakeTask.id.in_(checked_task_ids)
        ),
        select(FormalStocktakeScope.assignee_user_id.label("user_id")).where(
            FormalStocktakeScope.task_id.in_(checked_task_ids)
        ),
        select(InventoryFreeze.created_by_user_id.label("user_id")).where(
            InventoryFreeze.task_id.in_(checked_task_ids)
        ),
        select(InventoryFreeze.released_by_user_id.label("user_id")).where(
            InventoryFreeze.task_id.in_(checked_task_ids)
        ),
        select(StocktakeRound.submitted_by_user_id.label("user_id")).where(
            StocktakeRound.task_id.in_(checked_task_ids)
        ),
        select(StocktakeCountLine.counted_by_user_id.label("user_id")).where(
            StocktakeCountLine.task_id.in_(checked_task_ids)
        ),
        select(StocktakeCountObservation.counted_by_user_id.label("user_id")).where(
            StocktakeCountObservation.task_id.in_(checked_task_ids)
        ),
        select(
            StocktakeScopeCountCompletion.completed_by_user_id.label("user_id")
        ).where(StocktakeScopeCountCompletion.task_id.in_(checked_task_ids)),
        select(StocktakeRoundSubmission.submitted_by_user_id.label("user_id")).where(
            StocktakeRoundSubmission.task_id.in_(checked_task_ids)
        ),
        select(
            StocktakeObservationDisposition.decided_by_user_id.label("user_id")
        ).where(StocktakeObservationDisposition.task_id.in_(checked_task_ids)),
        select(
            StocktakeDifferenceSetCompletion.completed_by_user_id.label("user_id")
        ).where(StocktakeDifferenceSetCompletion.task_id.in_(checked_task_ids)),
        select(StocktakeReview.reviewer_user_id.label("user_id")).where(
            StocktakeReview.task_id.in_(checked_task_ids)
        ),
        select(StocktakeRecountCase.opened_by_user_id.label("user_id")).where(
            StocktakeRecountCase.task_id.in_(checked_task_ids)
        ),
        select(
            StocktakeRecountScopeAssignment.assignee_user_id.label("user_id")
        ).where(StocktakeRecountScopeAssignment.task_id.in_(checked_task_ids)),
        select(StocktakePosting.posted_by_user_id.label("user_id")).where(
            StocktakePosting.task_id.in_(checked_task_ids)
        ),
        select(
            InventoryOpeningEstablishment.established_by_user_id.label("user_id")
        ).where(InventoryOpeningEstablishment.task_id.in_(checked_task_ids)),
        select(InventoryTransaction.actor_user_id.label("user_id"))
        .join(
            StocktakePosting,
            StocktakePosting.inventory_transaction_id == InventoryTransaction.id,
        )
        .where(StocktakePosting.task_id.in_(checked_task_ids)),
        select(StateTransitionEvent.actor_id.label("user_id")).where(
            StateTransitionEvent.aggregate_type == "stocktake_task",
            StateTransitionEvent.aggregate_id.in_(task_id_strings),
        ),
    )
    return {
        value
        for value in db.scalars(union_all(*statements)).all()
        if isinstance(value, str) and value
    }


def _lock_opening_task_principal_graph(
    db: Session,
    *,
    task_ids: Sequence[uuid.UUID],
    supplied_user_ids: Sequence[str] = (),
) -> _PrelockedOpeningPrincipalGraphProof:
    """Lock one union of all historical principals for already-held tasks.

    Callers must first lock every task row in UUID order.  The task rows make
    the following plain coordinate discovery finite; one deterministic formal
    principal helper call then owns every current and historical
    ``RoleAssignment`` for those users before any task-evidence or shared
    reference owner is entered.
    """

    checked_task_ids = tuple(sorted(set(task_ids), key=str))
    if not checked_task_ids or any(
        not isinstance(task_id, uuid.UUID) or task_id.int == 0
        for task_id in checked_task_ids
    ):
        _fail(
            "opening_principal_graph_task_coordinates_invalid",
            "precondition_failed",
            "期初历史人员图缺少有效任务坐标",
        )
    persisted_task_ids = tuple(
        db.scalars(
            select(FormalStocktakeTask.id)
            .where(FormalStocktakeTask.id.in_(checked_task_ids))
            .order_by(FormalStocktakeTask.id)
        ).all()
    )
    if persisted_task_ids != checked_task_ids:
        _fail(
            "opening_principal_graph_task_coordinates_invalid",
            "precondition_failed",
            "期初历史人员图任务坐标不完整",
        )

    checked_supplied_user_ids = tuple(
        sorted(
            {
                value
                for value in supplied_user_ids
                if isinstance(value, str) and value.strip()
            }
        )
    )
    user_ids = _opening_task_principal_user_ids_batch(
        db,
        task_ids=checked_task_ids,
    )
    user_ids.update(checked_supplied_user_ids)
    checked_user_ids = tuple(sorted(user_ids))
    if not checked_user_ids:
        _fail(
            "opening_principal_graph_empty",
            "precondition_failed",
            "期初历史人员图为空，禁止继续重证",
        )
    lock_formal_principal_graph(db, checked_user_ids)
    assignments = tuple(
        db.scalars(
            select(RoleAssignment)
            .where(RoleAssignment.user_id.in_(checked_user_ids))
            .order_by(RoleAssignment.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    transaction = db.get_transaction()
    if transaction is None:
        _fail(
            "opening_principal_graph_transaction_required",
            "precondition_failed",
            "期初历史人员图必须绑定活动事务",
        )
    return _PrelockedOpeningPrincipalGraphProof(
        session=db,
        transaction=transaction,
        task_ids=checked_task_ids,
        supplied_user_ids=checked_supplied_user_ids,
        user_ids=checked_user_ids,
        assignment_signatures=tuple(
            _role_assignment_signature(row) for row in assignments
        ),
        seal=_PRELOCKED_OPENING_PRINCIPAL_GRAPH_SEAL,
    )


def _require_opening_task_principal_graph_proof(
    db: Session,
    proof: object,
    *,
    required_task_ids: Sequence[uuid.UUID] = (),
) -> _PrelockedOpeningPrincipalGraphProof:
    transaction = db.get_transaction()
    checked_required_task_ids = set(required_task_ids)
    if (
        not isinstance(proof, _PrelockedOpeningPrincipalGraphProof)
        or proof.seal is not _PRELOCKED_OPENING_PRINCIPAL_GRAPH_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
        or not checked_required_task_ids.issubset(proof.task_ids)
    ):
        _fail(
            "opening_principal_graph_proof_invalid",
            "precondition_failed",
            "期初历史人员图证明无效或不属于当前事务",
        )
    return proof


def _validate_prelocked_opening_task_principal_graph(
    db: Session,
    *,
    proof: _PrelockedOpeningPrincipalGraphProof,
) -> None:
    """Plainly reject any post-lock principal or assignment-set expansion."""

    checked = _require_opening_task_principal_graph_proof(db, proof)
    current_user_ids = _opening_task_principal_user_ids_batch(
        db,
        task_ids=checked.task_ids,
    )
    current_user_ids.update(checked.supplied_user_ids)
    if tuple(sorted(current_user_ids)) != checked.user_ids:
        _fail(
            "opening_principal_graph_expanded",
            "precondition_failed",
            "期初历史人员坐标在预锁后发生扩展或收缩",
        )
    assignments = tuple(
        db.scalars(
            select(RoleAssignment)
            .where(RoleAssignment.user_id.in_(checked.user_ids))
            .order_by(RoleAssignment.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if tuple(
        _role_assignment_signature(row) for row in assignments
    ) != checked.assignment_signatures:
        _fail(
            "opening_principal_assignment_graph_changed",
            "precondition_failed",
            "期初历史角色分配图在预锁后发生变化",
        )


def _issue_prelocked_inventory_graph_proof(
    db: Session,
    *,
    command: InventoryPostingCommand,
    account_ids: Sequence[uuid.UUID],
    serial_ids: Sequence[uuid.UUID],
) -> _PrelockedInventoryGraphProof:
    """Bind already-held 0027 reference locks to this exact transaction.

    This private constructor does not acquire or waive a lock.  It is called
    only by opening finalize immediately after its single inventory/serial
    owner-helper calls and before mutable projections are touched.
    """

    checked_accounts = tuple(sorted(set(account_ids), key=str))
    checked_serials = tuple(sorted(set(serial_ids), key=str))
    if (
        not set(_command_account_ids(command)).issubset(checked_accounts)
        or not set(_command_serial_ids(command)).issubset(checked_serials)
    ):
        _fail(
            "inventory_prelocked_graph_coordinates_invalid",
            "precondition_failed",
            "期初预锁库存引用图未覆盖完整账户或 SN 坐标",
        )
    balance_account_ids = tuple(
        db.scalars(
            select(StockBalance.stock_account_id)
            .where(StockBalance.stock_account_id.in_(checked_accounts))
            .order_by(StockBalance.stock_account_id)
        ).all()
    )
    transaction = db.get_transaction()
    if transaction is None:
        _fail(
            "inventory_prelocked_graph_transaction_required",
            "precondition_failed",
            "期初预锁库存引用图必须绑定活动事务",
        )
    return _PrelockedInventoryGraphProof(
        session=db,
        transaction=transaction,
        command=command,
        account_ids=checked_accounts,
        balance_account_ids=balance_account_ids,
        serial_ids=checked_serials,
        seal=_PRELOCKED_INVENTORY_GRAPH_SEAL,
    )


def _require_prelocked_inventory_graph_proof(
    db: Session,
    *,
    command: InventoryPostingCommand,
    proof: object,
) -> _PrelockedInventoryGraphProof:
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _PrelockedInventoryGraphProof)
        or proof.seal is not _PRELOCKED_INVENTORY_GRAPH_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
        or proof.command != command
        or not set(_command_account_ids(command)).issubset(proof.account_ids)
        or not set(_command_serial_ids(command)).issubset(proof.serial_ids)
    ):
        _fail(
            "inventory_prelocked_graph_proof_invalid",
            "precondition_failed",
            "期初预锁库存引用图证明与当前事务或命令不一致",
        )
    return proof


def post_inventory_transaction(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: InventoryPostingCommand,
    idempotency_key: str,
    request_id: str,
    permission_action: str = "post",
    permission_resource: str = "inventory_transaction",
) -> InventoryPostingResult:
    """Append one formal inventory transaction without committing it."""

    checked_actor = _validate_supplied_actor(actor)
    checked_command = _validate_posting_command(command)
    if checked_command.movement_type == "opening":
        _fail(
            "inventory_opening_requires_approved_stocktake",
            "precondition_failed",
            "期初入账只能由完成区域及总部复核的正式盘点服务生成",
        )
    checked_idempotency = _require_idempotency_key(idempotency_key)
    checked_request_id = _require_request_id(request_id)

    current_actor = _require_current_actor(db, checked_actor)
    storage_key = _storage_hash(checked_idempotency)
    request_hash = _posting_request_hash(current_actor, checked_command)
    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("idempotency", storage_key),
            _advisory_coordinate("posting", checked_command.posting_key),
        ),
    )

    replay = _load_replay(db, storage_key, request_hash)
    if replay is not None:
        _authorize_account_ids(
            db,
            current_actor,
            _command_account_ids(checked_command),
            action=permission_action,
            lock_rows=False,
            resource=permission_resource,
        )
        return replace(replay, replayed=True)
    _require_unused_business_keys(db, checked_command)

    try:
        return _post_new_transaction(
            db,
            actor=current_actor,
            command=checked_command,
            idempotency_key_hash=storage_key,
            request_hash=request_hash,
            request_reference=_request_reference(checked_request_id),
            permission_action=permission_action,
            permission_resource=permission_resource,
            reversed_transaction_id=None,
            event_suffix="posted",
        ).result
    except IntegrityError as exc:
        _fail(
            "inventory_concurrent_conflict",
            "conflict",
            "库存交易发生并发冲突，请回滚并重新读取后再试",
            cause=exc,
        )


def reverse_inventory_transaction(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: InventoryReversalCommand,
    idempotency_key: str,
    request_id: str,
) -> InventoryPostingResult:
    """Append the exact inverse of one posted non-reversal transaction."""

    checked_actor = _validate_supplied_actor(actor)
    checked_command = _validate_reversal_command(command)
    checked_idempotency = _require_idempotency_key(idempotency_key)
    checked_request_id = _require_request_id(request_id)

    current_actor = _require_current_actor(db, checked_actor)
    storage_key = _storage_hash(checked_idempotency)
    request_hash = _reversal_request_hash(current_actor, checked_command)
    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("idempotency", storage_key),
            _advisory_coordinate("posting", checked_command.posting_key),
            _advisory_coordinate(
                "original-transaction", str(checked_command.original_transaction_id)
            ),
        ),
    )

    replay = _load_replay(db, storage_key, request_hash)
    if replay is not None:
        replay_account_ids = _transaction_account_ids(db, replay.transaction_id)
        _authorize_account_ids(
            db,
            current_actor,
            replay_account_ids,
            action="reverse",
            lock_rows=False,
        )
        return replace(replay, replayed=True)

    posting_shell = InventoryPostingCommand(
        transaction_no=checked_command.transaction_no,
        movement_type="reversal",
        source_document_type=checked_command.source_document_type,
        source_document_id=checked_command.source_document_id,
        posting_key=checked_command.posting_key,
        effective_at=checked_command.effective_at,
        movements=(),
    )
    _require_unused_business_keys(db, posting_shell)

    original = db.get(InventoryTransaction, checked_command.original_transaction_id)
    if original is None:
        _fail("original_transaction_not_found", "not_found", "原库存交易不存在")
    if original.status != "posted":
        _fail(
            "original_transaction_not_posted",
            "precondition_failed",
            "原库存交易不是可冲销的已过账状态",
        )
    if original.movement_type == "opening":
        _fail(
            "inventory_opening_reversal_requires_stocktake_compensation",
            "precondition_failed",
            "期初入账不能通过通用冲销撤回，必须走正式盘点差异补偿流程",
        )
    if original.movement_type == "reversal" or original.reversed_transaction_id:
        _fail(
            "reversal_of_reversal_forbidden",
            "conflict",
            "冲销交易不能再次作为原交易冲销",
        )
    existing_reversal = db.scalar(
        select(InventoryTransaction.id).where(
            InventoryTransaction.reversed_transaction_id == original.id
        )
    )
    if existing_reversal is not None:
        _fail(
            "original_transaction_already_reversed",
            "conflict",
            "原库存交易已经冲销",
        )

    movements = db.scalars(
        select(InventoryMovement)
        .where(InventoryMovement.transaction_id == original.id)
        .order_by(InventoryMovement.line_no)
    ).all()
    if not movements:
        _fail(
            "original_transaction_incomplete",
            "service_unavailable",
            "原库存交易缺少不可变明细",
        )
    serial_rows = db.execute(
        select(
            InventoryMovementSerial.movement_id,
            InventoryMovementSerial.serial_id,
        )
        .where(InventoryMovementSerial.transaction_id == original.id)
        .order_by(
            InventoryMovementSerial.movement_id,
            InventoryMovementSerial.serial_id,
        )
    ).all()
    serials_by_movement: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    for movement_id, serial_id in serial_rows:
        serials_by_movement[movement_id].append(serial_id)
    if original.movement_type in {"consume", "scrap"} and serial_rows:
        _fail(
            "serial_lifecycle_reversal_unavailable",
            "precondition_failed",
            "当前账本尚不能安全重建已消耗或报废 SN 的原生命周期，禁止冲销",
        )

    reversed_movements = tuple(
        InventoryMovementCommand(
            from_account_id=movement.to_account_id,
            to_account_id=movement.from_account_id,
            quantity=movement.quantity,
            serial_ids=tuple(serials_by_movement.get(movement.id, ())),
            external_boundary_code=movement.external_boundary_code,
        )
        for movement in movements
    )
    posting_command = replace(posting_shell, movements=reversed_movements)

    try:
        return _post_new_transaction(
            db,
            actor=current_actor,
            command=posting_command,
            idempotency_key_hash=storage_key,
            request_hash=request_hash,
            request_reference=_request_reference(checked_request_id),
            permission_action="reverse",
            reversed_transaction_id=original.id,
            event_suffix="reversed",
        ).result
    except IntegrityError as exc:
        _fail(
            "inventory_concurrent_conflict",
            "conflict",
            "库存交易发生并发冲突，请回滚并重新读取后再试",
            cause=exc,
        )


def _post_new_transaction(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: InventoryPostingCommand,
    idempotency_key_hash: str,
    request_hash: str,
    request_reference: str,
    permission_action: str,
    reversed_transaction_id: uuid.UUID | None,
    event_suffix: str,
    permission_resource: str = "inventory_transaction",
    opening_task_id: uuid.UUID | None = None,
    occurred_at: datetime | None = None,
    prelocked_reference_graph: _PrelockedInventoryGraphProof | None = None,
) -> _InventoryPostingCommit:
    # Production row-lock order is fixed and must remain identical for every
    # posting: ledger head -> account UUIDs -> location UUIDs -> material UUIDs
    # -> effective policies -> lot UUIDs -> serial masters -> serial positions
    # -> balance UUIDs -> audit head.  PostgreSQL reference/master locks use
    # migration-owned SECURITY DEFINER helpers because the API role has SELECT,
    # but deliberately no UPDATE, on those immutable/master tables.  Replay
    # paths are read-only and intentionally do not take this set.
    head = db.scalar(
        select(InventoryLedgerHead)
        .where(
            InventoryLedgerHead.id == INVENTORY_LEDGER_HEAD_ID,
            InventoryLedgerHead.stream_key == INVENTORY_STREAM_KEY,
        )
        .with_for_update()
    )
    if head is None or not isinstance(head.next_cursor, int) or head.next_cursor <= 0:
        _fail(
            "inventory_ledger_head_unavailable",
            "service_unavailable",
            "库存账本游标未正确初始化",
        )
    current_ledger_cursor = head.next_cursor - 1

    account_ids = _command_account_ids(command)
    (
        terminal_opening_graphs,
        planned_current_reference_graph,
        terminal_principal_graph,
    ) = (
        _plan_and_lock_terminal_opening_graphs(
            db,
            command=command,
            current_actor_user_id=actor.user_id,
        )
        if opening_task_id is None
        else ((), None, None)
    )
    active_reference_graph = (
        prelocked_reference_graph or planned_current_reference_graph
    )
    if active_reference_graph is None:
        lock_inventory_reference_graph(db, account_ids, command.effective_at)
    else:
        _require_prelocked_inventory_graph_proof(
            db,
            command=command,
            proof=active_reference_graph,
        )
    accounts = _authorize_account_ids(
        db,
        actor,
        account_ids,
        action=permission_action,
        lock_rows=True,
        resource=permission_resource,
    )
    _require_active_account_masters(db, accounts)
    if opening_task_id is None:
        _require_established_unfrozen_scopes(
            db,
            accounts,
            current_ledger_cursor=current_ledger_cursor,
            effective_at=command.effective_at,
            terminal_opening_graphs=terminal_opening_graphs,
        )
    else:
        _require_active_opening_task_scopes(
            db,
            task_id=opening_task_id,
            command=command,
            accounts=accounts,
            prelocked_reference_graph=prelocked_reference_graph,
        )
    policies = _load_effective_policies(
        db,
        {account.material_id for account in accounts.values()},
        command.effective_at,
    )
    # All command/tracking semantics must fail before a missing mutable
    # balance can be materialized.  The serial owner helper still follows this
    # pure validation and remains strictly before the balance phase.
    _validate_tracking_rules(command, accounts, policies)

    serials, positions = _lock_and_validate_serials(
        db,
        command=command,
        accounts=accounts,
        policies=policies,
        prelocked_reference_graph=active_reference_graph,
    )

    if opening_task_id is None or prelocked_reference_graph is None:
        balances = _lock_or_create_balances(db, account_ids)
    else:
        balances = _lock_existing_prelocked_balances(
            db,
            account_ids=account_ids,
            proof=prelocked_reference_graph,
            command=command,
        )
    # Historical opening evidence is verified only after every task,
    # reference, serial and balance lock has been acquired.  The audit head is
    # the final shared row lock for both generic and opening-only postings.  One
    # transaction-bound proof is issued here and is reused by every subsequent
    # post-audit validator; ``append_audit_event`` may only exact-reenter this
    # already-held stream head.
    _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    if opening_task_id is None:
        if terminal_principal_graph is not None:
            _validate_prelocked_opening_task_principal_graph(
                db,
                proof=terminal_principal_graph,
            )
        _validate_prelocked_terminal_opening_graphs(
            db,
            terminal_opening_graphs=terminal_opening_graphs,
            current_ledger_cursor=current_ledger_cursor,
            audit_proof=audit_proof,
        )
    # The mutable balance table is only a projection.  Re-prove every touched
    # row from posted immutable movements while its row lock is held and before
    # consulting it for sufficiency or applying a delta.  The ledger head lock
    # prevents a concurrent writer from adding a newer transaction; these
    # evidence reads intentionally acquire no additional locks.
    _validate_locked_balance_projections(
        db,
        account_ids=account_ids,
        balances=balances,
    )
    deltas = _aggregate_deltas(command)
    negative_accounts = tuple(
        account_id
        for account_id in account_ids
        if balances[account_id].quantity + deltas[account_id] < _ZERO
    )
    if negative_accounts:
        _fail(
            "insufficient_stock",
            "conflict",
            "库存余额不足，整笔交易未过账",
        )

    # Reserve the already locked ledger cursor only after every validation has
    # succeeded.  A mistakenly caught domain error therefore cannot leave a
    # dirty in-transaction cursor increment for a later operation.
    ledger_cursor = head.next_cursor
    head.next_cursor += 1
    now = (
        _require_aware_datetime("occurred_at", occurred_at)
        if occurred_at is not None
        else datetime.now(timezone.utc)
    )
    transaction_id = uuid.uuid4()
    transaction = InventoryTransaction(
        id=transaction_id,
        transaction_no=command.transaction_no,
        movement_type=command.movement_type,
        source_document_type=command.source_document_type,
        source_document_id=command.source_document_id,
        posting_key=command.posting_key,
        idempotency_key_hash=idempotency_key_hash,
        request_hash=request_hash,
        status="posted",
        effective_at=command.effective_at,
        posted_at=now,
        ledger_cursor=ledger_cursor,
        reversed_transaction_id=reversed_transaction_id,
        actor_user_id=actor.user_id,
        created_at=now,
    )
    db.add(transaction)
    # The schema intentionally declares no mutable ORM relationships.  Flush
    # each immutable FK parent before its children while remaining inside the
    # caller-owned transaction.
    db.flush()

    movement_ids: dict[int, uuid.UUID] = {}
    for line_no, movement_command in enumerate(command.movements, start=1):
        movement_id = uuid.uuid4()
        movement_ids[line_no] = movement_id
        db.add(
            InventoryMovement(
                id=movement_id,
                transaction_id=transaction_id,
                line_no=line_no,
                from_account_id=movement_command.from_account_id,
                to_account_id=movement_command.to_account_id,
                external_boundary_code=movement_command.external_boundary_code,
                quantity=movement_command.quantity,
                created_at=now,
            )
        )
    db.flush()

    for line_no, movement_command in enumerate(command.movements, start=1):
        for serial_id in movement_command.serial_ids:
            db.add(
                InventoryMovementSerial(
                    movement_id=movement_ids[line_no],
                    transaction_id=transaction_id,
                    serial_id=serial_id,
                    created_at=now,
                )
            )

    for account_id in account_ids:
        balance = balances[account_id]
        balance.quantity += deltas[account_id]
        balance.ledger_cursor = ledger_cursor
        balance.version += 1
        balance.updated_at = now

    for line_no, movement_command in enumerate(command.movements, start=1):
        for serial_id in movement_command.serial_ids:
            position = positions.get(serial_id)
            if position is None:
                position = SerialCurrentPosition(
                    serial_id=serial_id,
                    stock_account_id=movement_command.to_account_id,
                    last_movement_id=movement_ids[line_no],
                    updated_at=now,
                )
                db.add(position)
                positions[serial_id] = position
            else:
                position.stock_account_id = movement_command.to_account_id
                position.last_movement_id = movement_ids[line_no]
                position.updated_at = now

    state_key = _derived_evidence_key("state", transaction_id, event_suffix)
    db.add(
        StateTransitionEvent(
            aggregate_type="inventory_transaction",
            aggregate_id=str(transaction_id),
            from_status=None,
            to_status="posted",
            reason=f"inventory_transaction_{event_suffix}",
            actor_id=actor.user_id,
            idempotency_key=state_key,
            occurred_at=now,
            metadata_jsonb={
                "ledger_cursor": ledger_cursor,
                "movement_type": command.movement_type,
                "request_reference": request_reference,
            },
            created_at=now,
        )
    )
    outbox_event_type = f"inventory.transaction.{event_suffix}"
    db.add(
        OutboxEvent(
            event_type=outbox_event_type,
            aggregate_type="inventory_transaction",
            aggregate_id=str(transaction_id),
            payload_jsonb={
                "transaction_id": str(transaction_id),
                "transaction_no": command.transaction_no,
                "movement_type": command.movement_type,
                "ledger_cursor": ledger_cursor,
                "reversed_transaction_id": (
                    str(reversed_transaction_id)
                    if reversed_transaction_id is not None
                    else None
                ),
            },
            status="pending",
            attempts=0,
            idempotency_key=_derived_evidence_key(
                "outbox", transaction_id, event_suffix
            ),
            available_at=now,
            locked_at=None,
            locked_by=None,
            published_at=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
    )

    try:
        # This is deliberately the final row lock in the transaction.
        append_audit_event(
            db,
            stream_key=INVENTORY_STREAM_KEY,
            actor_user_id=actor.user_id,
            action=outbox_event_type,
            aggregate_type="inventory_transaction",
            aggregate_id=str(transaction_id),
            before_jsonb=None,
            after_jsonb={
                "ledger_cursor": ledger_cursor,
                "movement_count": len(command.movements),
                "movement_type": command.movement_type,
                "posting_key": command.posting_key,
                "reversed_transaction_id": (
                    str(reversed_transaction_id)
                    if reversed_transaction_id is not None
                    else None
                ),
                "status": "posted",
            },
            request_id=request_reference,
            occurred_at=now,
            created_at=now,
        )
        db.flush()
    except AuditChainError as exc:
        _fail(
            "inventory_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，交易未完成",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "inventory_concurrent_conflict",
            "conflict",
            "库存交易发生并发冲突，请回滚并重新读取后再试",
            cause=exc,
        )

    # Keep a strong reference until the final flush; this also makes it clear
    # that serial existence was checked before the projection changed.
    del serials
    return _InventoryPostingCommit(
        result=InventoryPostingResult(
            transaction_id=transaction_id,
            transaction_no=command.transaction_no,
            ledger_cursor=ledger_cursor,
        ),
        audit_proof=audit_proof,
    )


def _lock_inventory_ledger_head_for_atomic_batch(
    db: Session,
) -> _PrelockedInventoryLedgerHeadProof:
    """Take the one global ledger lock before any stocktake/task row lock."""

    head = db.scalar(
        select(InventoryLedgerHead)
        .where(
            InventoryLedgerHead.id == INVENTORY_LEDGER_HEAD_ID,
            InventoryLedgerHead.stream_key == INVENTORY_STREAM_KEY,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    transaction = db.get_transaction()
    if (
        head is None
        or transaction is None
        or not isinstance(head.next_cursor, int)
        or isinstance(head.next_cursor, bool)
        or head.next_cursor <= 0
    ):
        _fail(
            "inventory_ledger_head_unavailable",
            "service_unavailable",
            "库存账本游标未正确初始化",
        )
    return _PrelockedInventoryLedgerHeadProof(
        session=db,
        transaction=transaction,
        head=head,
        current_ledger_cursor=head.next_cursor - 1,
        seal=_PRELOCKED_INVENTORY_LEDGER_HEAD_SEAL,
    )


def _require_prelocked_inventory_ledger_head(
    db: Session,
    proof: object,
) -> _PrelockedInventoryLedgerHeadProof:
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _PrelockedInventoryLedgerHeadProof)
        or proof.seal is not _PRELOCKED_INVENTORY_LEDGER_HEAD_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
        or proof.head.id != INVENTORY_LEDGER_HEAD_ID
        or proof.head.stream_key != INVENTORY_STREAM_KEY
        or proof.head.next_cursor - 1 != proof.current_ledger_cursor
    ):
        _fail(
            "inventory_batch_ledger_proof_invalid",
            "precondition_failed",
            "库存批次总账预锁证明无效或已被本事务污染",
        )
    return proof


def _post_prelocked_stocktake_inventory_batch(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
    entries: tuple[_StocktakeInventoryBatchEntry, ...],
    request_reference: str,
    occurred_at: datetime,
    ledger_proof: _PrelockedInventoryLedgerHeadProof,
) -> _StocktakeInventoryBatchCommit:
    """Atomically append all inventory facts for one approved stocktake task.

    This is a deliberately private primitive.  The caller must first lock the
    ledger head, then the complete task/principal graph.  This function takes
    exactly one union reference lock, one union serial lock, one balance set and
    one audit head.  It never loops through ``post_inventory_transaction`` and
    never creates notification/outbox facts.
    """

    checked_ledger = _require_prelocked_inventory_ledger_head(db, ledger_proof)
    checked_task_id = _require_uuid("task_id", task_id)
    checked_now = _require_aware_datetime("occurred_at", occurred_at)
    _require_text("request_reference", request_reference, 160, safe=True)
    current_actor = _require_current_stocktake_difference_finalizer(db, actor)
    if not isinstance(entries, tuple):
        _fail(
            "inventory_stocktake_batch_invalid",
            "invalid_request",
            "盘点库存批次必须使用不可变元组",
        )

    checked_entries: list[_StocktakeInventoryBatchEntry] = []
    seen_transaction_nos: set[str] = set()
    seen_posting_keys: set[str] = set()
    seen_idempotency_hashes: set[str] = set()
    seen_serials: set[uuid.UUID] = set()
    effective_at: datetime | None = None
    for entry in entries:
        if not isinstance(entry, _StocktakeInventoryBatchEntry):
            _fail(
                "inventory_stocktake_batch_entry_invalid",
                "invalid_request",
                "盘点库存批次明细类型无效",
            )
        command = _validate_posting_command(entry.command)
        if (
            command.movement_type
            not in {"stocktake_gain", "stocktake_loss", "transfer", "status_change"}
            or command.source_document_type != "stocktake_difference"
            or command.source_document_id != str(checked_task_id)
            or not _SHA256_HEX.fullmatch(entry.idempotency_key_hash or "")
            or not _SHA256_HEX.fullmatch(entry.request_hash or "")
        ):
            _fail(
                "inventory_stocktake_batch_entry_invalid",
                "precondition_failed",
                "盘点库存批次明细未绑定唯一任务、类型或请求摘要",
            )
        if effective_at is None:
            effective_at = command.effective_at
        elif command.effective_at != effective_at:
            _fail(
                "inventory_stocktake_batch_time_mismatch",
                "precondition_failed",
                "同一盘点库存批次必须使用同一生效时点",
            )
        command_serials = {
            serial_id
            for movement in command.movements
            for serial_id in movement.serial_ids
        }
        if (
            command.transaction_no in seen_transaction_nos
            or command.posting_key in seen_posting_keys
            or entry.idempotency_key_hash in seen_idempotency_hashes
            or seen_serials.intersection(command_serials)
        ):
            _fail(
                "inventory_stocktake_batch_duplicate_coordinate",
                "conflict",
                "盘点库存批次包含重复交易、过账、幂等或 SN 坐标",
            )
        seen_transaction_nos.add(command.transaction_no)
        seen_posting_keys.add(command.posting_key)
        seen_idempotency_hashes.add(entry.idempotency_key_hash)
        seen_serials.update(command_serials)
        checked_entries.append(
            _StocktakeInventoryBatchEntry(
                command=command,
                idempotency_key_hash=entry.idempotency_key_hash,
                request_hash=entry.request_hash,
            )
        )

    if checked_entries:
        existing = db.scalar(
            select(InventoryTransaction.id)
            .where(
                or_(
                    InventoryTransaction.transaction_no.in_(seen_transaction_nos),
                    InventoryTransaction.posting_key.in_(seen_posting_keys),
                    InventoryTransaction.idempotency_key_hash.in_(
                        seen_idempotency_hashes
                    ),
                )
            )
            .limit(1)
        )
        if existing is not None:
            _fail(
                "inventory_stocktake_batch_business_key_conflict",
                "conflict",
                "盘点库存批次坐标已被其他库存事实占用",
            )

    union_command: InventoryPostingCommand | None = None
    terminal_opening_graphs: tuple[_PrelockedOpeningTerminalTaskGraph, ...] = ()
    terminal_principal_graph: _PrelockedOpeningPrincipalGraphProof | None = None
    accounts: dict[uuid.UUID, StockAccount] = {}
    positions: dict[uuid.UUID, SerialCurrentPosition] = {}
    balances: dict[uuid.UUID, StockBalance] = {}
    account_ids: tuple[uuid.UUID, ...] = ()
    if checked_entries:
        assert effective_at is not None
        union_command = InventoryPostingCommand(
            transaction_no=f"STOCKTAKE-BATCH-{checked_task_id}",
            movement_type="transfer",
            source_document_type="stocktake_difference",
            source_document_id=str(checked_task_id),
            posting_key=f"stocktake-difference-batch:{checked_task_id}",
            effective_at=effective_at,
            movements=tuple(
                movement
                for entry in checked_entries
                for movement in entry.command.movements
            ),
        )
        account_ids = _command_account_ids(union_command)
        (
            terminal_opening_graphs,
            reference_graph,
            terminal_principal_graph,
        ) = _plan_and_lock_terminal_opening_graphs(
            db,
            command=union_command,
            current_actor_user_id=current_actor.user_id,
        )
        if reference_graph is None:
            _fail(
                "inventory_stocktake_batch_reference_lock_missing",
                "precondition_failed",
                "盘点库存批次缺少联合引用预锁证明",
            )
        accounts = _authorize_account_ids(
            db,
            current_actor,
            account_ids,
            action="post_difference",
            lock_rows=True,
            resource="stocktake",
        )
        _require_active_account_masters(db, accounts)
        _require_valid_opening_establishments(
            db,
            accounts,
            current_ledger_cursor=checked_ledger.current_ledger_cursor,
            effective_at=effective_at,
            terminal_opening_graphs=terminal_opening_graphs,
        )
        _require_owned_stocktake_freeze_scopes(
            db,
            task_id=checked_task_id,
            accounts=accounts,
            effective_at=effective_at,
        )
        policies = _load_effective_policies(
            db,
            {account.material_id for account in accounts.values()},
            effective_at,
        )
        for entry in checked_entries:
            _validate_tracking_rules(entry.command, accounts, policies)
        _serials, positions = _lock_and_validate_serials(
            db,
            command=union_command,
            accounts=accounts,
            policies=policies,
            prelocked_reference_graph=reference_graph,
        )
        balances = _lock_or_create_balances(db, account_ids)

    # The audit head is the one final shared lock even for a zero-movement
    # completion.  No notification or reconciliation row is created here.
    _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    if terminal_principal_graph is not None:
        _validate_prelocked_opening_task_principal_graph(
            db,
            proof=terminal_principal_graph,
        )
    if terminal_opening_graphs:
        _validate_prelocked_terminal_opening_graphs(
            db,
            terminal_opening_graphs=terminal_opening_graphs,
            current_ledger_cursor=checked_ledger.current_ledger_cursor,
            audit_proof=audit_proof,
        )
    if account_ids:
        _validate_locked_balance_projections(
            db,
            account_ids=account_ids,
            balances=balances,
        )
        assert union_command is not None
        combined_deltas = _aggregate_deltas(union_command)
        if any(
            balances[account_id].quantity + combined_deltas[account_id] < _ZERO
            for account_id in account_ids
        ):
            _fail(
                "insufficient_stock",
                "conflict",
                "库存余额不足，整批盘点差异未过账",
            )

    first_cursor = checked_ledger.head.next_cursor
    checked_ledger.head.next_cursor += len(checked_entries)
    entry_commits: list[_StocktakeInventoryBatchEntryCommit] = []
    touched_cursors: dict[uuid.UUID, list[int]] = defaultdict(list)
    serial_targets: dict[uuid.UUID, tuple[uuid.UUID | None, uuid.UUID]] = {}

    for offset, entry in enumerate(checked_entries):
        command = entry.command
        ledger_cursor = first_cursor + offset
        transaction_id = uuid.uuid4()
        db.add(
            InventoryTransaction(
                id=transaction_id,
                transaction_no=command.transaction_no,
                movement_type=command.movement_type,
                source_document_type=command.source_document_type,
                source_document_id=command.source_document_id,
                posting_key=command.posting_key,
                idempotency_key_hash=entry.idempotency_key_hash,
                request_hash=entry.request_hash,
                status="posted",
                effective_at=command.effective_at,
                posted_at=checked_now,
                ledger_cursor=ledger_cursor,
                reversed_transaction_id=None,
                actor_user_id=current_actor.user_id,
                created_at=checked_now,
            )
        )
        db.flush()
        movement_ids: list[uuid.UUID] = []
        touched_in_transaction: set[uuid.UUID] = set()
        for line_no, movement_command in enumerate(command.movements, start=1):
            movement_id = uuid.uuid4()
            movement_ids.append(movement_id)
            db.add(
                InventoryMovement(
                    id=movement_id,
                    transaction_id=transaction_id,
                    line_no=line_no,
                    from_account_id=movement_command.from_account_id,
                    to_account_id=movement_command.to_account_id,
                    external_boundary_code=movement_command.external_boundary_code,
                    quantity=movement_command.quantity,
                    created_at=checked_now,
                )
            )
            for account_id in (
                movement_command.from_account_id,
                movement_command.to_account_id,
            ):
                if account_id is not None:
                    touched_in_transaction.add(account_id)
            for serial_id in movement_command.serial_ids:
                serial_targets[serial_id] = (
                    movement_command.to_account_id,
                    movement_id,
                )
        db.flush()
        for movement_id, movement_command in zip(
            movement_ids, command.movements, strict=True
        ):
            for serial_id in movement_command.serial_ids:
                db.add(
                    InventoryMovementSerial(
                        movement_id=movement_id,
                        transaction_id=transaction_id,
                        serial_id=serial_id,
                        created_at=checked_now,
                    )
                )
        for account_id in touched_in_transaction:
            touched_cursors[account_id].append(ledger_cursor)
        db.add(
            StateTransitionEvent(
                aggregate_type="inventory_transaction",
                aggregate_id=str(transaction_id),
                from_status=None,
                to_status="posted",
                reason="inventory_transaction_stocktake_difference_posted",
                actor_id=current_actor.user_id,
                idempotency_key=_derived_evidence_key(
                    "state", transaction_id, "stocktake-difference-posted"
                ),
                occurred_at=checked_now,
                metadata_jsonb={
                    "ledger_cursor": ledger_cursor,
                    "movement_type": command.movement_type,
                    "request_reference": request_reference,
                    "stocktake_task_id": str(checked_task_id),
                },
                created_at=checked_now,
            )
        )
        entry_commits.append(
            _StocktakeInventoryBatchEntryCommit(
                result=InventoryPostingResult(
                    transaction_id=transaction_id,
                    transaction_no=command.transaction_no,
                    ledger_cursor=ledger_cursor,
                ),
                movement_ids=tuple(movement_ids),
            )
        )

    if account_ids:
        assert union_command is not None
        combined_deltas = _aggregate_deltas(union_command)
        for account_id in account_ids:
            balance = balances[account_id]
            cursors = touched_cursors.get(account_id, [])
            balance.quantity += combined_deltas[account_id]
            if cursors:
                balance.ledger_cursor = max(cursors)
                balance.version += len(cursors)
            balance.updated_at = checked_now
        for serial_id, (target_account_id, movement_id) in serial_targets.items():
            position = positions.get(serial_id)
            if position is None:
                db.add(
                    SerialCurrentPosition(
                        serial_id=serial_id,
                        stock_account_id=target_account_id,
                        last_movement_id=movement_id,
                        updated_at=checked_now,
                    )
                )
            else:
                position.stock_account_id = target_account_id
                position.last_movement_id = movement_id
                position.updated_at = checked_now
    db.flush()

    try:
        for entry, committed in zip(
            checked_entries, entry_commits, strict=True
        ):
            append_audit_event(
                db,
                stream_key=INVENTORY_STREAM_KEY,
                actor_user_id=current_actor.user_id,
                action="inventory.transaction.stocktake_difference_posted",
                aggregate_type="inventory_transaction",
                aggregate_id=str(committed.result.transaction_id),
                before_jsonb=None,
                after_jsonb={
                    "ledger_cursor": committed.result.ledger_cursor,
                    "movement_count": len(entry.command.movements),
                    "movement_type": entry.command.movement_type,
                    "posting_key": entry.command.posting_key,
                    "status": "posted",
                    "stocktake_task_id": str(checked_task_id),
                },
                request_id=request_reference,
                occurred_at=checked_now,
                created_at=checked_now,
            )
        db.flush()
    except AuditChainError as exc:
        _fail(
            "inventory_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，盘点库存批次未完成",
            cause=exc,
        )

    return _StocktakeInventoryBatchCommit(
        entries=tuple(entry_commits),
        current_ledger_cursor=checked_ledger.current_ledger_cursor,
        audit_proof=audit_proof,
    )


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail(
            "formal_principal_required",
            "forbidden",
            "库存过账必须使用正式权限主体",
        )
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _fail("actor_inactive", "forbidden", "当前账号或人员状态不允许库存过账")
    _require_text("actor_user_id", actor.user_id, 36, safe=True)
    return actor


def _require_current_actor(db: Session, supplied: FormalPrincipal) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id)
    except FormalAccessError as exc:
        _fail(
            "actor_not_current",
            "forbidden",
            "正式权限上下文已失效，请重新读取后再操作",
            cause=exc,
        )
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail(
            "actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取后再操作",
        )
    if (
        current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail("actor_inactive", "forbidden", "当前账号或人员状态不允许库存过账")
    return current


def _require_current_stocktake_difference_finalizer(
    db: Session,
    supplied: FormalPrincipal,
) -> FormalPrincipal:
    """Defense-in-depth authorization for the private stocktake batch."""

    current = _require_current_actor(db, _validate_supplied_actor(supplied))
    grants = tuple(
        row
        for row in current.assignments
        if row.role_code == "admin"
        and row.scope_type == "national"
        and row.scope_id == "*"
    )
    try:
        allowed = current.allows(
            db,
            "stocktake",
            "post_difference",
            target_scope_type="national",
            target_scope_id="*",
        )
    except FormalAccessError as exc:
        _fail(
            "inventory_stocktake_finalizer_authorization_invalid",
            "forbidden",
            "盘点差异过账权限图无效",
            cause=exc,
        )
    assignment = (
        db.get(RoleAssignment, grants[0].assignment_id, populate_existing=True)
        if len(grants) == 1
        else None
    )
    # This is the low-level finalizer boundary immediately before a stocktake
    # batch can mutate balances.  Do not trust ORM identity-map values here:
    # a concurrent organization/status update must be visible to this proof.
    role = (
        db.scalar(
            select(Role)
            .where(Role.id == assignment.role_id)
            .execution_options(populate_existing=True)
        )
        if assignment is not None
        else None
    )
    user = db.scalar(
        select(User)
        .where(User.id == current.user_id)
        .execution_options(populate_existing=True)
    )
    person = db.scalar(
        select(Person)
        .where(Person.id == current.person_id)
        .execution_options(populate_existing=True)
    )
    organization = (
        db.scalar(
            select(Organization)
            .where(Organization.id == person.organization_id)
            .execution_options(populate_existing=True)
        )
        if person is not None
        else None
    )
    if (
        not allowed
        or assignment is None
        or assignment.user_id != current.user_id
        or assignment.status != "active"
        or assignment.revoked_at is not None
        or assignment.scope_type != "national"
        or assignment.scope_id != "*"
        or role is None
        or role.code != "admin"
        or role.status != "active"
        or role.is_external
        or user is None
        or user.person_id != current.person_id
        or person is None
        or person.employment_status != "active"
        or organization is None
        or organization.org_type != "headquarters"
        or organization.status != "active"
    ):
        _fail(
            "inventory_stocktake_finalizer_forbidden",
            "forbidden",
            "盘点差异过账仅允许当前有效的总部管理员执行",
        )
    return current


def lock_current_stocktake_finalizer_organization(
    db: Session,
    actor: FormalPrincipal,
) -> Organization:
    """Hold a migration-owned organization lock through the completion seal.

    ``star_oam_api`` intentionally has no ``UPDATE`` privilege on
    ``organizations``.  PostgreSQL therefore calls the 0064 SECURITY DEFINER
    capability, whose owner takes the ``FOR SHARE`` lock and validates that
    the row is an active headquarters organization.  The lock remains held
    by this caller transaction; the refreshed plain read below only hydrates
    the ORM object and repeats the domain predicate.  SQLite keeps its
    validation-only path because it has no row-locking equivalent.
    """

    supplied = _validate_supplied_actor(actor)
    person = db.scalar(
        select(Person)
        .where(Person.id == supplied.person_id)
        .execution_options(populate_existing=True)
    )
    if person is None:
        _fail(
            "inventory_stocktake_finalizer_forbidden",
            "forbidden",
            "盘点差异过账仅允许当前有效的总部管理员执行",
        )
    if db.get_bind().dialect.name == "postgresql":
        try:
            db.execute(
                text(
                    "SELECT public.rsc_lock_stocktake_finalizer_organization_0064("
                    "CAST(:organization_id AS uuid))"
                ),
                {"organization_id": str(person.organization_id)},
            )
        except DBAPIError as exc:
            _fail(
                "inventory_stocktake_finalizer_forbidden",
                "forbidden",
                "盘点差异过账仅允许当前有效的总部管理员执行",
                cause=exc,
            )
    statement = _stocktake_finalizer_organization_statement(person.organization_id)
    organization = db.scalar(statement)
    if (
        organization is None
        or organization.org_type != "headquarters"
        or organization.status != "active"
    ):
        _fail(
            "inventory_stocktake_finalizer_forbidden",
            "forbidden",
            "盘点差异过账仅允许当前有效的总部管理员执行",
        )
    return organization


def _stocktake_finalizer_organization_statement(
    organization_id: uuid.UUID,
):
    """Build the refreshed plain read used after the 0064 lock capability."""

    statement = (
        select(Organization)
        .where(Organization.id == organization_id)
        .execution_options(populate_existing=True)
    )
    return statement


def _validate_posting_command(command: InventoryPostingCommand) -> InventoryPostingCommand:
    if not isinstance(command, InventoryPostingCommand):
        _fail("posting_command_required", "invalid_request", "库存过账命令类型无效")
    transaction_no = _require_text(
        "transaction_no", command.transaction_no, 100, safe=True
    )
    movement_type = _require_text(
        "movement_type", command.movement_type, 32, safe=True
    )
    if movement_type not in MOVEMENT_TYPES or movement_type == "reversal":
        _fail("movement_type_invalid", "invalid_request", "库存变动类型无效")
    source_document_type = _require_text(
        "source_document_type", command.source_document_type, 80, safe=True
    )
    source_document_id = _require_text(
        "source_document_id", command.source_document_id, 80, safe=True
    )
    posting_key = _require_text("posting_key", command.posting_key, 200, safe=True)
    effective_at = _require_aware_datetime("effective_at", command.effective_at)
    movements = _validate_movements(command.movements, movement_type)
    return InventoryPostingCommand(
        transaction_no=transaction_no,
        movement_type=movement_type,
        source_document_type=source_document_type,
        source_document_id=source_document_id,
        posting_key=posting_key,
        effective_at=effective_at,
        movements=movements,
    )


def _validate_reversal_command(
    command: InventoryReversalCommand,
) -> InventoryReversalCommand:
    if not isinstance(command, InventoryReversalCommand):
        _fail("reversal_command_required", "invalid_request", "库存冲销命令类型无效")
    return InventoryReversalCommand(
        original_transaction_id=_require_uuid(
            "original_transaction_id", command.original_transaction_id
        ),
        transaction_no=_require_text(
            "transaction_no", command.transaction_no, 100, safe=True
        ),
        source_document_type=_require_text(
            "source_document_type", command.source_document_type, 80, safe=True
        ),
        source_document_id=_require_text(
            "source_document_id", command.source_document_id, 80, safe=True
        ),
        posting_key=_require_text(
            "posting_key", command.posting_key, 200, safe=True
        ),
        effective_at=_require_aware_datetime("effective_at", command.effective_at),
    )


def _validate_movements(
    movements: tuple[InventoryMovementCommand, ...], movement_type: str
) -> tuple[InventoryMovementCommand, ...]:
    if not isinstance(movements, tuple) or not movements:
        _fail("movements_required", "invalid_request", "库存过账必须包含明细元组")
    checked: list[InventoryMovementCommand] = []
    seen_serials: set[uuid.UUID] = set()
    for movement in movements:
        if not isinstance(movement, InventoryMovementCommand):
            _fail("movement_invalid", "invalid_request", "库存明细类型无效")
        from_id = (
            _require_uuid("from_account_id", movement.from_account_id)
            if movement.from_account_id is not None
            else None
        )
        to_id = (
            _require_uuid("to_account_id", movement.to_account_id)
            if movement.to_account_id is not None
            else None
        )
        if from_id is None and to_id is None:
            _fail("movement_endpoint_required", "invalid_request", "库存明细缺少端点")
        if from_id is not None and from_id == to_id:
            _fail(
                "movement_accounts_must_differ",
                "invalid_request",
                "库存明细的来源与目标账户必须不同",
            )
        quantity = _require_quantity(movement.quantity)
        if not isinstance(movement.serial_ids, tuple):
            _fail("serial_ids_must_be_tuple", "invalid_request", "SN 清单必须为元组")
        serial_ids = tuple(
            _require_uuid("serial_id", serial_id)
            for serial_id in movement.serial_ids
        )
        if len(set(serial_ids)) != len(serial_ids) or seen_serials.intersection(
            serial_ids
        ):
            _fail(
                "duplicate_serial_in_transaction",
                "invalid_request",
                "同一库存交易不能重复使用 SN",
            )
        seen_serials.update(serial_ids)

        boundary = movement.external_boundary_code
        if movement_type in EXTERNAL_INBOUND_TYPES:
            if from_id is not None or to_id is None:
                _fail(
                    "external_inbound_direction_invalid",
                    "invalid_request",
                    "该库存类型只允许外部进入受管账户",
                )
            boundary = _require_text(
                "external_boundary_code", boundary, 100, safe=True
            )
        elif movement_type in EXTERNAL_OUTBOUND_TYPES:
            if from_id is None or to_id is not None:
                _fail(
                    "external_outbound_direction_invalid",
                    "invalid_request",
                    "该库存类型只允许受管账户流向外部",
                )
            boundary = _require_text(
                "external_boundary_code", boundary, 100, safe=True
            )
        else:
            if from_id is None or to_id is None:
                _fail(
                    "internal_direction_invalid",
                    "invalid_request",
                    "该库存类型只允许受管账户之间流转",
                )
            if boundary is not None:
                _fail(
                    "internal_boundary_forbidden",
                    "invalid_request",
                    "内部库存流转不能设置外部边界",
                )
        checked.append(
            InventoryMovementCommand(
                from_account_id=from_id,
                to_account_id=to_id,
                quantity=quantity,
                serial_ids=tuple(sorted(serial_ids, key=str)),
                external_boundary_code=boundary,
            )
        )
    return tuple(checked)


def _require_unused_business_keys(
    db: Session, command: InventoryPostingCommand
) -> None:
    if db.scalar(
        select(InventoryTransaction.id).where(
            InventoryTransaction.posting_key == command.posting_key
        )
    ) is not None:
        _fail(
            "posting_key_conflict",
            "conflict",
            "业务过账键已被其他请求使用",
        )
    if db.scalar(
        select(InventoryTransaction.id).where(
            InventoryTransaction.transaction_no == command.transaction_no
        )
    ) is not None:
        _fail(
            "transaction_no_conflict",
            "conflict",
            "库存交易号已存在",
        )


def _load_replay(
    db: Session, idempotency_key_hash: str, request_hash: str
) -> InventoryPostingResult | None:
    transaction = db.scalar(
        select(InventoryTransaction).where(
            InventoryTransaction.idempotency_key_hash == idempotency_key_hash
        )
    )
    if transaction is None:
        return None
    if transaction.request_hash != request_hash:
        _fail(
            "idempotency_key_conflict",
            "conflict",
            "幂等键已绑定不同的库存请求",
        )
    if transaction.status != "posted" or transaction.ledger_cursor <= 0:
        _fail(
            "idempotency_record_invalid",
            "service_unavailable",
            "库存幂等记录状态异常",
        )
    return InventoryPostingResult(
        transaction_id=transaction.id,
        transaction_no=transaction.transaction_no,
        ledger_cursor=transaction.ledger_cursor,
    )


def _authorize_account_ids(
    db: Session,
    actor: FormalPrincipal,
    account_ids: tuple[uuid.UUID, ...],
    *,
    action: str,
    lock_rows: bool,
    resource: str = "inventory_transaction",
) -> dict[uuid.UUID, StockAccount]:
    statement = (
        select(StockAccount)
        .where(StockAccount.id.in_(account_ids))
        .order_by(StockAccount.id)
    )
    if lock_rows and _uses_direct_reference_row_locks(db):
        statement = statement.with_for_update()
    rows = db.scalars(statement).all()
    accounts = {row.id: row for row in rows}
    if len(accounts) != len(account_ids):
        _fail("stock_account_not_found", "not_found", "库存账户不存在")
    location_ids = tuple(
        sorted({account.location_id for account in accounts.values()}, key=str)
    )
    location_statement = (
        select(StockLocation)
        .where(StockLocation.id.in_(location_ids))
        .order_by(StockLocation.id)
    )
    if lock_rows and _uses_direct_reference_row_locks(db):
        location_statement = location_statement.with_for_update()
    locations = {
        row.id: row for row in db.scalars(location_statement).all()
    }
    if len(locations) != len(location_ids):
        _fail("stock_location_not_found", "not_found", "库存账户关联的库位不存在")
    for account_id in account_ids:
        account = accounts[account_id]
        if account.custodian_person_id is not None:
            required_scopes = (
                ("person", str(account.custodian_person_id)),
            )
        else:
            # Asset ownership and physical operation are independent V1.0
            # dimensions.  An unheld warehouse/transit account therefore
            # requires authority over both dimensions.  Usually both resolve
            # to the same region; if they differ, only a principal whose
            # formal scope covers both (normally a national administrator)
            # may post.  Checking only the location would let one region alter
            # another organization's assets.
            required_scopes = tuple(
                dict.fromkeys(
                    (
                        ("organization", str(account.owner_org_id)),
                        (
                            "organization",
                            str(locations[account.location_id].owner_org_id),
                        ),
                    )
                )
            )
        for target_scope_type, target_scope_id in required_scopes:
            try:
                allowed = actor.allows(
                    db,
                    resource,
                    action,
                    target_scope_type=target_scope_type,
                    target_scope_id=target_scope_id,
                )
            except FormalAccessError as exc:
                _fail(
                    "inventory_scope_invalid",
                    "forbidden",
                    "库存账户数据范围无效",
                    cause=exc,
                )
            if not allowed:
                _fail(
                    "inventory_account_forbidden",
                    "forbidden",
                    "当前人员无权操作一个或多个库存账户",
                )
    return accounts


def _post_approved_opening_transaction(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
    proof: _OpeningFinalizationProof,
    command: InventoryPostingCommand,
    idempotency_key_hash: str,
    request_hash: str,
    request_reference: str,
    occurred_at: datetime,
    prelocked_reference_graph: _PrelockedInventoryGraphProof,
) -> _InventoryPostingCommit:
    """Append one opening transaction for the dedicated finalize service.

    This is intentionally not a public/general posting boundary.  The caller
    must hold the opening-task and ledger locks and must have re-proved the
    complete count/review evidence graph.  Defense-in-depth checks here bind
    the only bypass of the normal establishment/freeze gate to one approved,
    actively frozen opening task.  ``post_inventory_transaction`` continues
    to reject ``movement_type='opening'`` unconditionally.
    """

    checked_task_id = _require_uuid("task_id", task_id)
    locked_task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked_task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if locked_task is None:
        _fail(
            "inventory_opening_task_not_found",
            "not_found",
            "期初专用过账任务不存在",
        )
    checked_occurred_at = _require_aware_datetime("occurred_at", occurred_at)
    current_actor = _require_current_opening_finalizer(
        db, actor, occurred_at=checked_occurred_at
    )
    _require_opening_finalization_proof(
        db,
        task_id=checked_task_id,
        proof=proof,
    )
    checked_command = _validate_posting_command(command)
    _require_prelocked_inventory_graph_proof(
        db,
        command=checked_command,
        proof=prelocked_reference_graph,
    )
    if (
        checked_command.movement_type != "opening"
        or checked_command.source_document_type != "opening_stocktake"
        or checked_command.source_document_id != str(checked_task_id)
        or checked_command.posting_key
        != f"opening-stocktake:{checked_task_id}"
    ):
        _fail(
            "inventory_opening_command_invalid",
            "precondition_failed",
            "期初专用过账命令未绑定唯一已批准盘点任务",
        )
    if not _SHA256_HEX.fullmatch(idempotency_key_hash or "") or not _SHA256_HEX.fullmatch(
        request_hash or ""
    ):
        _fail(
            "inventory_opening_hash_invalid",
            "invalid_request",
            "期初专用过账的幂等或请求摘要无效",
        )
    _require_text("request_reference", request_reference, 160, safe=True)
    _require_unused_business_keys(db, checked_command)
    return _post_new_transaction(
        db,
        actor=current_actor,
        command=checked_command,
        idempotency_key_hash=idempotency_key_hash,
        request_hash=request_hash,
        request_reference=request_reference,
        permission_action="post_opening",
        permission_resource="stocktake",
        reversed_transaction_id=None,
        event_suffix="posted",
        opening_task_id=checked_task_id,
        occurred_at=checked_occurred_at,
        prelocked_reference_graph=prelocked_reference_graph,
    )


def _require_current_opening_finalizer(
    db: Session,
    actor: FormalPrincipal,
    *,
    occurred_at: datetime,
) -> FormalPrincipal:
    """Re-read HQ identity and permission inside the low-level primitive."""

    supplied = _validate_supplied_actor(actor)
    current = _require_current_actor(db, supplied)
    grants = tuple(
        row
        for row in current.assignments
        if row.role_code == "admin"
        and row.scope_type == "national"
        and row.scope_id == "*"
    )
    try:
        allowed = current.allows(
            db,
            "stocktake",
            "post_opening",
            target_scope_type="national",
            target_scope_id="*",
        )
    except FormalAccessError as exc:
        _fail(
            "inventory_opening_finalizer_authorization_invalid",
            "forbidden",
            "期初专用过账的总部权限图无效",
            cause=exc,
        )
    if len(grants) != 1 or not allowed:
        _fail(
            "inventory_opening_finalizer_forbidden",
            "forbidden",
            "期初专用过账仅允许总部管理员执行",
        )
    grant = grants[0]
    assignment = db.scalar(
        select(RoleAssignment)
        .where(RoleAssignment.id == grant.assignment_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    user = db.get(User, current.user_id)
    person = db.get(Person, current.person_id)
    organization = (
        db.get(Organization, person.organization_id)
        if person is not None
        else None
    )
    valid_from = (
        _persisted_timestamp_utc(assignment.valid_from)
        if assignment is not None
        else None
    )
    valid_to = (
        _persisted_timestamp_utc(assignment.valid_to)
        if assignment is not None
        else None
    )
    revoked_at = (
        _persisted_timestamp_utc(assignment.revoked_at)
        if assignment is not None
        else None
    )
    if (
        assignment is None
        or assignment.user_id != current.user_id
        or assignment.scope_type != "national"
        or assignment.scope_id != "*"
        or assignment.status not in {"scheduled", "active"}
        or valid_from is None
        or valid_from > occurred_at
        or (valid_to is not None and occurred_at >= valid_to)
        or (revoked_at is not None and occurred_at >= revoked_at)
        or role is None
        or role.code != "admin"
        or role.status != "active"
        or role.is_external
        or user is None
        or user.person_id != current.person_id
        or person is None
        or person.employment_status != "active"
        or organization is None
        or organization.org_type != "headquarters"
        or organization.status != "active"
    ):
        _fail(
            "inventory_opening_finalizer_not_current",
            "forbidden",
            "期初专用过账的总部管理员身份已失效",
        )
    return current


def _require_opening_finalization_proof(
    db: Session,
    *,
    task_id: uuid.UUID,
    proof: _OpeningFinalizationProof,
) -> None:
    if not isinstance(proof, _OpeningFinalizationProof):
        _fail(
            "inventory_opening_finalization_proof_required",
            "precondition_failed",
            "期初专用过账缺少终结证据证明",
        )
    task = db.get(FormalStocktakeTask, task_id)
    round_row = db.get(StocktakeRound, proof.round_id)
    reviews = tuple(
        db.scalars(
            select(StocktakeReview)
            .where(
                StocktakeReview.task_id == task_id,
                StocktakeReview.round_id == proof.round_id,
            )
            .order_by(StocktakeReview.review_stage)
        ).all()
    )
    regional = next((row for row in reviews if row.review_stage == "region"), None)
    headquarters = next(
        (row for row in reviews if row.review_stage == "headquarters"), None
    )
    if (
        task is None
        or proof.task_id != task_id
        or task.task_type != "opening"
        or task.status != "approved"
        or task.version != proof.expected_task_version
        or task.scope_manifest_sha256 != proof.scope_manifest_sha256
        or task.snapshot_manifest_sha256 != proof.snapshot_manifest_sha256
        or task.control_manifest_sha256 != proof.control_manifest_sha256
        or round_row is None
        or round_row.task_id != task_id
        or round_row.round_no != task.current_round_no
        or round_row.status != "submitted"
        or round_row.count_manifest_sha256 != proof.count_manifest_sha256
        or len(reviews) != 2
        or regional is None
        or headquarters is None
        or regional.id != proof.regional_review_id
        or headquarters.id != proof.headquarters_review_id
        or regional.decision != "approve"
        or headquarters.decision != "approve"
    ):
        _fail(
            "inventory_opening_finalization_proof_invalid",
            "precondition_failed",
            "期初专用过账的任务、清单、轮次或两级复核证明不一致",
        )


def _require_active_opening_task_scopes(
    db: Session,
    *,
    task_id: uuid.UUID,
    command: InventoryPostingCommand,
    accounts: Mapping[uuid.UUID, StockAccount],
    prelocked_reference_graph: _PrelockedInventoryGraphProof | None = None,
) -> None:
    """Keep the opening-only gate narrower than ordinary inventory posting."""

    if prelocked_reference_graph is not None:
        proof = _require_prelocked_inventory_graph_proof(
            db,
            command=command,
            proof=prelocked_reference_graph,
        )
        if not set(accounts).issubset(proof.account_ids):
            _fail(
                "inventory_opening_prelocked_accounts_invalid",
                "precondition_failed",
                "期初专用过账账户超出已预锁引用图",
            )
        # Opening finalize already holds the task row.  A plain populate read
        # re-proves it without creating a task-after-reference inverse lock.
        task = db.get(
            FormalStocktakeTask,
            task_id,
            populate_existing=True,
        )
    else:
        task = db.scalar(
            select(FormalStocktakeTask)
            .where(FormalStocktakeTask.id == task_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    effective_at = _persisted_timestamp_utc(command.effective_at)
    cutoff_at = _persisted_timestamp_utc(task.cutoff_at) if task is not None else None
    if (
        task is None
        or task.task_type != "opening"
        or task.status != "approved"
        or cutoff_at is None
        or effective_at != cutoff_at
        or db.scalar(
            select(InventoryOpeningEstablishment.id)
            .where(InventoryOpeningEstablishment.task_id == task_id)
            .limit(1)
        )
        is not None
    ):
        _fail(
            "inventory_opening_task_not_postable",
            "precondition_failed",
            "期初任务不是唯一可过账的已批准且未建立状态",
        )

    # The task row above is the mutable serialization point.  Scope rows are
    # immutable task evidence after issuance; PostgreSQL therefore reads them
    # normally under that task lock instead of exceeding the API role's
    # SELECT/INSERT-only scope ACL.  Local SQLite tests retain their prior
    # statement-lock shape through ``_select_only_reference_statement``.
    scopes = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(FormalStocktakeScope)
                .where(FormalStocktakeScope.task_id == task_id)
                .order_by(FormalStocktakeScope.scope_no),
            )
        ).all()
    )
    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id == task_id)
            .order_by(InventoryFreeze.stocktake_scope_id)
            .with_for_update()
        ).all()
    )
    scope_by_pair = {
        (row.owner_org_id, row.location_id): row for row in scopes
    }
    scope_by_id = {row.id: row for row in scopes}
    freeze_by_scope = {row.stocktake_scope_id: row for row in freezes}
    if (
        not scopes
        or len(scope_by_pair) != len(scopes)
        or len(freeze_by_scope) != len(freezes)
        or set(freeze_by_scope) != {row.id for row in scopes}
        or any(
            row.scope_mode != "location_all"
            or row.material_id is not None
            or row.condition_code is not None
            or row.availability_bucket is not None
            for row in scopes
        )
        or any(
            freeze.status != "active"
            or freeze.valid_to is not None
            or freeze.scope_key
            != scope_by_id[freeze.stocktake_scope_id].scope_key
            for freeze in freezes
        )
        or any(
            (account.owner_org_id, account.location_id) not in scope_by_pair
            for account in accounts.values()
        )
    ):
        _fail(
            "inventory_opening_scope_not_frozen",
            "precondition_failed",
            "期初专用过账的全部账户必须仍属于该任务的活动冻结范围",
        )


def _require_active_account_masters(
    db: Session, accounts: dict[uuid.UUID, StockAccount]
) -> None:
    material_ids = tuple(sorted({row.material_id for row in accounts.values()}, key=str))
    location_ids = tuple(sorted({row.location_id for row in accounts.values()}, key=str))
    material_statement = _select_only_reference_statement(
        db,
        select(FormalMaterial)
        .where(FormalMaterial.id.in_(material_ids))
        .order_by(FormalMaterial.id),
    )
    materials = {
        row.id: row
        for row in db.scalars(material_statement).all()
    }
    location_statement = _select_only_reference_statement(
        db,
        select(StockLocation)
        .where(StockLocation.id.in_(location_ids))
        .order_by(StockLocation.id),
    )
    locations = {
        row.id: row
        for row in db.scalars(location_statement).all()
    }
    if len(materials) != len(material_ids) or any(
        row.status != "active" for row in materials.values()
    ):
        _fail(
            "material_inactive",
            "precondition_failed",
            "库存账户关联的物料不是当前有效物料",
        )
    if len(locations) != len(location_ids) or any(
        row.status != "active" for row in locations.values()
    ):
        _fail(
            "stock_location_inactive",
            "precondition_failed",
            "库存账户关联的库位不是当前有效库位",
        )
    for account in accounts.values():
        location = locations[account.location_id]
        if location.location_type == "personal" and (
            location.custodian_person_id is None
            or account.custodian_person_id != location.custodian_person_id
        ):
            _fail(
                "personal_custodian_mismatch",
                "precondition_failed",
                "个人仓库位与库存账户保管人不一致",
            )


def canonical_opening_scope_line_sha256(scope: FormalStocktakeScope) -> str:
    """Hash one opening scope using the shared V1 canonical JSON contract.

    Canonical JSON is UTF-8, ``sort_keys=True``, ``ensure_ascii=False`` and
    ``separators=(",", ":")``.  UUID values are lower-case strings, quantities
    are exact three-decimal strings and datetimes are UTC with six fractional
    digits plus ``Z``.  These public helpers are intentionally usable by the
    dedicated opening-stocktake service; the ordinary posting guard calls the
    same functions when it distrustfully rereads persisted evidence.
    """

    return canonical_opening_manifest_sha256(
        {
            "schema": OPENING_SCOPE_LINE_SCHEMA,
            "scope_mode": scope.scope_mode,
            "owner_org_id": _canonical_uuid(scope.owner_org_id),
            "location_id": _canonical_uuid(scope.location_id),
            "custodian_person_id_snapshot": _canonical_optional_uuid(
                scope.custodian_person_id_snapshot
            ),
            "assignee_user_id": scope.assignee_user_id,
        }
    )


def canonical_opening_scope_manifest_sha256(
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    freezes_by_scope: Mapping[uuid.UUID, InventoryFreeze],
) -> str:
    rows = sorted(
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
            "schema": OPENING_SCOPE_MANIFEST_SCHEMA,
            "region_org_id": _canonical_uuid(task.region_org_id),
            "scopes": [
                {
                    "assignee_user_id": row.assignee_user_id,
                    "custodian_person_id_snapshot": _canonical_optional_uuid(
                        row.custodian_person_id_snapshot
                    ),
                    "freeze_mode": freezes_by_scope[row.id].freeze_mode,
                    "location_id": _canonical_uuid(row.location_id),
                    "owner_org_id": _canonical_uuid(row.owner_org_id),
                    "scope_key": row.scope_key,
                    "scope_mode": row.scope_mode,
                    "scope_no": row.scope_no,
                    "scope_sha256": row.scope_sha256,
                }
                for row in rows
            ],
        }
    )


def canonical_opening_account_dimension_sha256(account: StockAccount) -> str:
    return canonical_opening_manifest_sha256(
        {
            "schema": OPENING_ACCOUNT_DIMENSION_SCHEMA,
            "stock_account_id": _canonical_uuid(account.id),
            "owner_org_id": _canonical_uuid(account.owner_org_id),
            "custodian_person_id": _canonical_optional_uuid(
                account.custodian_person_id
            ),
            "location_id": _canonical_uuid(account.location_id),
            "material_id": _canonical_uuid(account.material_id),
            "condition_code": account.condition_code,
            "availability_bucket": account.availability_bucket,
            "lot_id": _canonical_optional_uuid(account.lot_id),
        }
    )


def canonical_opening_serial_snapshot_sha256(
    *,
    stock_account_id: uuid.UUID,
    serials: Sequence[Mapping[str, object]],
) -> str:
    return canonical_opening_manifest_sha256(
        {
            "schema": OPENING_SERIAL_SNAPSHOT_SCHEMA,
            "serials": list(serials),
            "stock_account_id": _canonical_uuid(stock_account_id),
        }
    )


def canonical_opening_snapshot_manifest_sha256(
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    snapshot_lines: Sequence[StocktakeSnapshotLine],
) -> str:
    lines_by_scope: dict[uuid.UUID, list[StocktakeSnapshotLine]] = defaultdict(
        list
    )
    for line in snapshot_lines:
        lines_by_scope[line.scope_id].append(line)
    ordered_scopes = sorted(
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
            "schema": OPENING_SNAPSHOT_MANIFEST_SCHEMA,
            "cutoff_ledger_cursor": task.cutoff_ledger_cursor,
            "scopes": [
                {
                    "owner_org_id": _canonical_uuid(scope.owner_org_id),
                    "location_id": _canonical_uuid(scope.location_id),
                    "scope_key": scope.scope_key,
                    "lines": [
                        {
                            "stock_account_id": _canonical_uuid(
                                line.stock_account_id
                            ),
                            "book_qty": _canonical_quantity(line.book_qty),
                            "ledger_cursor": line.ledger_cursor,
                            "account_dimension_sha256": (
                                line.account_dimension_sha256
                            ),
                            "serial_snapshot_sha256": (
                                line.serial_snapshot_sha256
                            ),
                            "serial_count": line.serial_count,
                        }
                        for line in sorted(
                            lines_by_scope.get(scope.id, ()),
                            key=lambda row: str(row.stock_account_id),
                        )
                    ],
                }
                for scope in ordered_scopes
            ],
        }
    )


def canonical_opening_count_manifest_sha256(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    count_lines: Sequence[StocktakeCountLine],
    count_serials: Sequence[StocktakeCountSerial],
) -> str:
    serials_by_line: dict[uuid.UUID, list[StocktakeCountSerial]] = defaultdict(
        list
    )
    for serial_row in count_serials:
        serials_by_line[serial_row.count_line_id].append(serial_row)
    return canonical_opening_manifest_sha256(
        {
            "schema": OPENING_COUNT_MANIFEST_SCHEMA,
            "task_id": _canonical_uuid(task.id),
            "round_id": _canonical_uuid(round_row.id),
            "round_no": round_row.round_no,
            # Count submission binds the same cutoff and manifest identities
            # copied onto each immutable establishment.  Completion services
            # and this ordinary-posting guard therefore share one exact
            # document instead of reconstructing a weaker count-only hash.
            "cutoff_at": _canonical_datetime(task.cutoff_at),
            "cutoff_ledger_cursor": task.cutoff_ledger_cursor,
            "scope_manifest_sha256": task.scope_manifest_sha256,
            "snapshot_manifest_sha256": task.snapshot_manifest_sha256,
            "control_manifest_sha256": task.control_manifest_sha256,
            "control_source_system_id": _canonical_optional_uuid(
                task.control_source_system_id
            ),
            "control_sync_run_id": _canonical_optional_uuid(
                task.control_sync_run_id
            ),
            "control_snapshot_at": _canonical_datetime(
                task.control_snapshot_at
            ),
            "lines": [
                {
                    "count_line_id": _canonical_uuid(line.id),
                    "scope_id": _canonical_uuid(line.scope_id),
                    "stock_account_id": _canonical_uuid(
                        line.stock_account_id
                    ),
                    "counted_qty": _canonical_quantity(line.counted_qty),
                    "count_method": line.count_method,
                    "reason_code": line.reason_code,
                    "remark": line.remark,
                    "counted_by_user_id": line.counted_by_user_id,
                    "counted_at": _canonical_datetime(line.counted_at),
                    "serials": [
                        {
                            "serial_id": _canonical_uuid(serial_row.serial_id),
                            "result": serial_row.result,
                        }
                        for serial_row in sorted(
                            serials_by_line.get(line.id, ()),
                            key=lambda row: str(row.serial_id),
                        )
                    ],
                }
                for line in sorted(
                    count_lines,
                    key=lambda row: (str(row.stock_account_id), str(row.id)),
                )
            ],
        }
    )


def canonical_opening_control_manifest_sha256(
    task: FormalStocktakeTask,
    sync_run: SyncRun,
    control_lines: Sequence[StocktakeControlSnapshotLine],
) -> str:
    return canonical_opening_manifest_sha256(
        {
            "schema": OPENING_CONTROL_MANIFEST_SCHEMA,
            "source_system_id": _canonical_optional_uuid(
                task.control_source_system_id
            ),
            "sync_run_id": _canonical_optional_uuid(task.control_sync_run_id),
            "sync_scope_key": sync_run.scope_key,
            "region_org_id": _canonical_uuid(task.region_org_id),
            "lines": [
                {
                    "line_no": line.line_no,
                    "external_business_key": line.external_business_key,
                    "external_object_version_id": _canonical_optional_uuid(
                        line.external_object_version_id
                    ),
                    "material_id": _canonical_optional_uuid(line.material_id),
                    "condition_code": line.condition_code,
                    "control_qty": _canonical_quantity(line.control_qty),
                    "mapping_status": line.mapping_status,
                    "source_updated_at": _canonical_optional_datetime(
                        line.source_updated_at
                    ),
                    "payload_sha256": line.payload_sha256,
                    "mapping_note": line.mapping_note,
                }
                for line in sorted(
                    control_lines,
                    key=lambda row: (row.line_no, str(row.id)),
                )
            ],
        }
    )


def canonical_opening_decision_manifest_sha256(
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    differences: Sequence[StocktakeDifference],
    decisions: Mapping[uuid.UUID, str],
) -> str:
    return canonical_opening_manifest_sha256(
        {
            "schema": OPENING_DECISION_MANIFEST_SCHEMA,
            "task_id": _canonical_uuid(task_id),
            "round_id": _canonical_uuid(round_id),
            "items": [
                {
                    "difference_id": _canonical_uuid(row.id),
                    "difference_no": row.difference_no,
                    "difference_type": row.difference_type,
                    "scope_id": _canonical_optional_uuid(row.scope_id),
                    "control_snapshot_line_id": _canonical_optional_uuid(
                        row.control_snapshot_line_id
                    ),
                    "material_id": _canonical_optional_uuid(row.material_id),
                    "expected_account_id": _canonical_optional_uuid(
                        row.expected_account_id
                    ),
                    "observed_account_id": _canonical_optional_uuid(
                        row.observed_account_id
                    ),
                    "serial_id": _canonical_optional_uuid(row.serial_id),
                    "book_qty": _canonical_quantity(row.book_qty),
                    "counted_qty": _canonical_quantity(row.counted_qty),
                    "difference_qty": _canonical_quantity(
                        row.difference_qty
                    ),
                    "affected_qty": _canonical_quantity(row.affected_qty),
                    "reason_code": row.reason_code,
                    "reason_text": row.reason_text,
                    "evidence_required": row.evidence_required,
                    "decision": decisions.get(row.id),
                }
                for row in sorted(
                    differences,
                    key=lambda item: (item.difference_no, str(item.id)),
                )
            ],
        }
    )


def _canonical_uuid(value: object) -> str:
    if isinstance(value, uuid.UUID):
        return str(value)
    try:
        return str(uuid.UUID(str(value)))
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError("opening manifest UUID is invalid") from exc


def _canonical_optional_uuid(value: object | None) -> str | None:
    return None if value is None else _canonical_uuid(value)


def _canonical_quantity(value: object) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        raise ValueError("opening manifest quantity is invalid")
    quantized = value.quantize(_QUANTITY_QUANTUM)
    if quantized != value:
        raise ValueError("opening manifest quantity exceeds scale 3")
    return format(quantized.normalize(), "f")


def _canonical_datetime(value: object) -> str:
    normalized = _persisted_timestamp_utc(value)
    if normalized is None:
        raise ValueError("opening manifest datetime is invalid")
    return normalized.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _canonical_optional_datetime(value: object | None) -> str | None:
    return None if value is None else _canonical_datetime(value)


def _plan_and_lock_terminal_opening_graphs(
    db: Session,
    *,
    command: InventoryPostingCommand,
    current_actor_user_id: str,
) -> tuple[
    tuple[_PrelockedOpeningTerminalTaskGraph, ...],
    _PrelockedInventoryGraphProof | None,
    _PrelockedOpeningPrincipalGraphProof | None,
]:
    """Prelock every historical opening graph before current references.

    The command accounts and establishment pointers are first read plainly to
    discover the finite terminal-task set.  The ledger head is already held.
    All task rows are first locked in UUID order.  Their complete historical
    actor union plus the current actor is then passed to exactly one principal
    helper before any task-evidence helper.  Historical shared masters are
    captured as immutable signatures and re-proved plainly; they are
    deliberately not locked task-by-task because overlapping materials across
    terminal tasks cannot be represented safely by repeated per-task owner
    helpers.  The current command takes exactly one inventory reference helper
    and one serial helper.  No audit head or mutable balance is touched here.
    """

    account_ids = _command_account_ids(command)
    if not account_ids:
        return (), None, None
    from . import opening_stocktake_finalize as finalize_service

    account_rows = tuple(
        db.scalars(
            select(StockAccount)
            .where(StockAccount.id.in_(account_ids))
            .order_by(StockAccount.id)
        ).all()
    )
    if len(account_rows) != len(account_ids):
        _fail("stock_account_not_found", "not_found", "库存账户不存在")
    account_by_id = {row.id: row for row in account_rows}
    # Preserve the public account-master error boundary before the historical
    # establishment planner.  This is a plain read and is repeated after the
    # current reference helper has locked the same command graph.
    _require_active_account_masters(db, account_by_id)
    scope_pairs = tuple(
        sorted(
            {
                (row.owner_org_id, row.location_id)
                for row in account_rows
            },
            key=lambda pair: (str(pair[0]), str(pair[1])),
        )
    )
    establishments = tuple(
        db.scalars(
            select(InventoryOpeningEstablishment)
            .where(
                or_(
                    *(
                        and_(
                            InventoryOpeningEstablishment.owner_org_id
                            == owner_org_id,
                            InventoryOpeningEstablishment.location_id
                            == location_id,
                        )
                        for owner_org_id, location_id in scope_pairs
                    )
                )
            )
            .order_by(
                InventoryOpeningEstablishment.owner_org_id,
                InventoryOpeningEstablishment.location_id,
                InventoryOpeningEstablishment.id,
            )
        ).all()
    )
    establishment_by_pair = {
        (row.owner_org_id, row.location_id): row for row in establishments
    }
    if (
        len(establishment_by_pair) != len(establishments)
        or set(establishment_by_pair) != set(scope_pairs)
    ):
        _fail(
            "inventory_opening_not_established",
            "precondition_failed",
            "一个或多个库存账户尚未完成正式期初盘点及两级复核",
        )
    task_ids = tuple(
        sorted({row.task_id for row in establishments}, key=str)
    )
    tasks = tuple(
        db.scalars(
            select(FormalStocktakeTask)
            .where(FormalStocktakeTask.id.in_(task_ids))
            .order_by(FormalStocktakeTask.id)
            .with_for_update(of=FormalStocktakeTask)
            .execution_options(populate_existing=True)
        ).all()
    )
    if tuple(row.id for row in tasks) != task_ids:
        _invalid_opening_establishment()

    principal_graph = _lock_opening_task_principal_graph(
        db,
        task_ids=task_ids,
        supplied_user_ids=(current_actor_user_id,),
    )

    rounds_by_task: dict[uuid.UUID, tuple[StocktakeRound, ...]] = {}
    for task in tasks:
        rounds = tuple(
            db.scalars(
                select(StocktakeRound)
                .where(StocktakeRound.task_id == task.id)
                .order_by(StocktakeRound.round_no, StocktakeRound.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        if (
            not rounds
            or [row.round_no for row in rounds]
            != list(range(1, task.current_round_no + 1))
        ):
            _invalid_opening_establishment()
        rounds_by_task[task.id] = rounds
    for task in tasks:
        # The 0027 helper locks the complete task-local graph.  The requested
        # round is only a membership coordinate, so one deterministic call per
        # task is sufficient even when historical recount rounds exist.
        lock_opening_stocktake_task_evidence(
            db,
            task.id,
            rounds_by_task[task.id][-1].id,
        )

    plans_by_task: dict[uuid.UUID, tuple[tuple[uuid.UUID, object], ...]] = {}
    try:
        for task in tasks:
            plans_by_task[task.id] = tuple(
                (
                    round_row.id,
                    finalize_service._opening_start_reference_coordinates(
                        db,
                        task=task,
                        round_id=round_row.id,
                    ),
                )
                for round_row in rounds_by_task[task.id]
            )
    except finalize_service.OpeningStocktakeFinalizeError as exc:
        _invalid_opening_establishment()

    combined_by_task: dict[
        uuid.UUID,
        tuple[
            tuple[tuple[uuid.UUID, uuid.UUID], ...],
            tuple[uuid.UUID, ...],
            tuple[uuid.UUID, ...],
            tuple[uuid.UUID, ...],
        ],
    ] = {}
    for task in tasks:
        round_plans = plans_by_task[task.id]
        first_plan = round_plans[0][1]
        if any(
            plan.scope_pairs != first_plan.scope_pairs
            for _round_id, plan in round_plans
        ):
            _invalid_opening_establishment()
        combined_by_task[task.id] = (
            first_plan.scope_pairs,
            tuple(
                sorted(
                    {
                        material_id
                        for _round_id, plan in round_plans
                        for material_id in plan.material_ids
                    },
                    key=str,
                )
            ),
            tuple(
                sorted(
                    {
                        account_id
                        for _round_id, plan in round_plans
                        for account_id in plan.account_ids
                    },
                    key=str,
                )
            ),
            tuple(
                sorted(
                    {
                        serial_id
                        for _round_id, plan in round_plans
                        for serial_id in plan.serial_ids
                    },
                    key=str,
                )
            ),
        )

    # Historical master graphs remain plain signature proofs.  Only the new
    # command owns shared references, once per category and in canonical order.
    lock_inventory_reference_graph(db, account_ids, command.effective_at)
    current_serial_ids = _command_serial_ids(command)
    lock_inventory_serial_graph(db, current_serial_ids)
    current_reference_proof = _issue_prelocked_inventory_graph_proof(
        db,
        command=command,
        account_ids=account_ids,
        serial_ids=current_serial_ids,
    )

    transaction = db.get_transaction()
    if transaction is None:
        _fail(
            "inventory_opening_terminal_graph_transaction_required",
            "precondition_failed",
            "期初终态任务图必须绑定活动事务",
        )
    proofs: list[_PrelockedOpeningTerminalTaskGraph] = []
    for task in tasks:
        _pairs, _materials, locked_accounts, locked_serials = combined_by_task[
            task.id
        ]
        try:
            resolutions_by_round = {
                round_id: finalize_service._prove_reference_plan_disposition_resolutions(
                    db,
                    task=task,
                    round_id=round_id,
                    reference_plan=plan,
                    locked_account_ids=locked_accounts,
                    locked_serial_ids=locked_serials,
                )
                for round_id, plan in plans_by_task[task.id]
            }
            audit_replay_plan = _plan_opening_task_audit_replay(
                db,
                task=task,
                disposition_resolutions_by_round=resolutions_by_round,
            )
        except finalize_service.OpeningStocktakeFinalizeError:
            _invalid_opening_establishment()
        _validate_prelocked_terminal_posting_structure(
            db,
            task=task,
            final_round=rounds_by_task[task.id][-1],
            reference_plan=plans_by_task[task.id][-1][1],
        )
        task_establishments = tuple(
            db.scalars(
                select(InventoryOpeningEstablishment)
                .where(InventoryOpeningEstablishment.task_id == task.id)
                .order_by(
                    InventoryOpeningEstablishment.owner_org_id,
                    InventoryOpeningEstablishment.location_id,
                    InventoryOpeningEstablishment.id,
                )
                .execution_options(populate_existing=True)
            ).all()
        )
        task_account_rows = tuple(
            row
            for row in account_rows
            if establishment_by_pair[
                (row.owner_org_id, row.location_id)
            ].task_id
            == task.id
        )
        proofs.append(
            _PrelockedOpeningTerminalTaskGraph(
                session=db,
                transaction=transaction,
                task_id=task.id,
                task=task,
                round_plans=plans_by_task[task.id],
                disposition_resolutions_by_round=resolutions_by_round,
                audit_replay_plan=audit_replay_plan,
                account_signatures=tuple(
                    _opening_account_signature(row) for row in task_account_rows
                ),
                establishment_signatures=tuple(
                    _opening_establishment_signature(row)
                    for row in task_establishments
                ),
                seal=_PRELOCKED_OPENING_TERMINAL_GRAPH_SEAL,
            )
        )
    return tuple(proofs), current_reference_proof, principal_graph


def _validate_prelocked_terminal_posting_structure(
    db: Session,
    *,
    task: FormalStocktakeTask,
    final_round: StocktakeRound,
    reference_plan: object,
) -> None:
    """Prove the historical posting shape before any balance can be created.

    The complete audit-chain proof remains after the final audit-head lock.
    This earlier pass intentionally validates only immutable posting,
    movement, SN and establishment bindings from the task-local graph plus the
    frozen historical master signature.  It acquires no row lock.
    """

    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task.id)
            .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    establishments = tuple(
        db.scalars(
            select(InventoryOpeningEstablishment)
            .where(InventoryOpeningEstablishment.task_id == task.id)
            .order_by(
                InventoryOpeningEstablishment.owner_org_id,
                InventoryOpeningEstablishment.location_id,
                InventoryOpeningEstablishment.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    posting_ids = {row.posting_id for row in establishments}
    posting = (
        db.get(StocktakePosting, next(iter(posting_ids)))
        if len(posting_ids) == 1
        else None
    )
    count_lines = tuple(
        db.scalars(
            select(StocktakeCountLine)
            .where(
                StocktakeCountLine.task_id == task.id,
                StocktakeCountLine.round_id == final_round.id,
            )
            .order_by(
                StocktakeCountLine.scope_id,
                StocktakeCountLine.stock_account_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    count_line_ids = tuple(row.id for row in count_lines)
    count_serial_rows = tuple(
        db.scalars(
            select(StocktakeCountSerial)
            .where(StocktakeCountSerial.count_line_id.in_(count_line_ids))
            .order_by(
                StocktakeCountSerial.count_line_id,
                StocktakeCountSerial.serial_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if count_line_ids else ()
    count_serials_by_line: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for row in count_serial_rows:
        count_serials_by_line[row.count_line_id].add(row.serial_id)
    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.round_id == final_round.id,
            )
            .order_by(
                StocktakeCountObservation.scope_id,
                StocktakeCountObservation.observation_no,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    accounts = {
        row.id: row
        for row in db.scalars(
            select(StockAccount)
            .where(StockAccount.id.in_(reference_plan.account_ids))
            .order_by(StockAccount.id)
            .execution_options(populate_existing=True)
        ).all()
    }
    observation_accounts = _load_opening_observation_accounts(
        db,
        task=task,
        round_row=final_round,
        scopes=scopes,
        observations=observations,
    )
    accounts.update({row.id: row for row in observation_accounts.values()})
    if posting is None:
        _invalid_opening_establishment()
    _validate_opening_posting(
        db,
        task=task,
        round_row=final_round,
        posting=posting,
        establishments=establishments,
        scopes=scopes,
        count_lines=count_lines,
        count_serials_by_line=count_serials_by_line,
        accounts=accounts,
        observations=observations,
        observation_accounts=observation_accounts,
    )


def _opening_account_signature(row: StockAccount) -> tuple[object, ...]:
    return (
        row.id,
        row.owner_org_id,
        row.custodian_person_id,
        row.location_id,
        row.material_id,
        row.condition_code,
        row.availability_bucket,
        row.lot_id,
        row.created_at,
    )


def _opening_establishment_signature(
    row: InventoryOpeningEstablishment,
) -> tuple[object, ...]:
    return (
        row.id,
        row.task_id,
        row.scope_id,
        row.owner_org_id,
        row.location_id,
        row.round_id,
        row.posting_id,
        row.regional_review_id,
        row.headquarters_review_id,
        row.cutoff_ledger_cursor,
        row.cutoff_at,
        row.established_ledger_cursor,
        row.scope_manifest_sha256,
        row.snapshot_manifest_sha256,
        row.count_manifest_sha256,
        row.control_manifest_sha256,
        row.has_pending_control_difference,
        row.established_by_user_id,
        row.established_at,
        row.created_at,
    )


def _validate_prelocked_terminal_opening_graphs(
    db: Session,
    *,
    terminal_opening_graphs: Sequence[
        _PrelockedOpeningTerminalTaskGraph
    ],
    current_ledger_cursor: int,
    audit_proof: object,
) -> None:
    """Purely re-prove terminal openings after the final audit-head lock."""

    from . import opening_stocktake_finalize as finalize_service

    for candidate_graph in terminal_opening_graphs:
        graph = _require_prelocked_opening_terminal_task_graph(
            db,
            candidate_graph,
        )
        try:
            for round_id, plan in graph.round_plans:
                current_plan = (
                    finalize_service._opening_start_reference_coordinates(
                        db,
                        task=graph.task,
                        round_id=round_id,
                    )
                )
                if current_plan != plan:
                    _invalid_opening_establishment()
                current_resolutions = (
                    finalize_service._prove_reference_plan_disposition_resolutions(
                        db,
                        task=graph.task,
                        round_id=round_id,
                        reference_plan=plan,
                        locked_account_ids=tuple(
                            account_id
                            for _round_id, round_plan in graph.round_plans
                            for account_id in round_plan.account_ids
                        ),
                        locked_serial_ids=tuple(
                            serial_id
                            for _round_id, round_plan in graph.round_plans
                            for serial_id in round_plan.serial_ids
                        ),
                    )
                )
                if current_resolutions != graph.disposition_resolutions_by_round.get(
                    round_id
                ):
                    _invalid_opening_establishment()
        except finalize_service.OpeningStocktakeFinalizeError:
            _invalid_opening_establishment()
        task, establishments = _validate_opening_task_evidence(
            db,
            task_id=graph.task_id,
            current_ledger_cursor=current_ledger_cursor,
            disposition_resolutions_by_round=(
                graph.disposition_resolutions_by_round
            ),
            audit_replay_plan=graph.audit_replay_plan,
            audit_proof=audit_proof,
        )
        if tuple(
            sorted(
                (
                    _opening_establishment_signature(row)
                    for row in establishments.values()
                ),
                key=lambda row: (str(row[3]), str(row[4]), str(row[0])),
            )
        ) != graph.establishment_signatures:
            _invalid_opening_establishment()
        reference = next(iter(establishments.values()), None)
        posting = (
            db.get(StocktakePosting, reference.posting_id)
            if reference is not None
            else None
        )
        if posting is None:
            _invalid_opening_establishment()
        try:
            finalize_service._validate_opening_terminal_effects_from_prelocked_generic_graph(
                db,
                graph=graph,
                task=task,
                posting=posting,
                audit_proof=audit_proof,
            )
        except finalize_service.OpeningStocktakeFinalizeError:
            _invalid_opening_establishment()


def _require_established_unfrozen_scopes(
    db: Session,
    accounts: dict[uuid.UUID, StockAccount],
    *,
    current_ledger_cursor: int,
    effective_at: datetime,
    terminal_opening_graphs: Sequence[
        _PrelockedOpeningTerminalTaskGraph
    ] = (),
) -> None:
    """Require reviewed opening facts and reject matching hard freezes.

    This gate is deliberately part of the *new fact* path in
    ``_post_new_transaction``.  Exact idempotency replays return earlier after
    reauthorizing their account scopes, so a freeze created after the original
    commit cannot turn a read-only replay into a failure or create another
    transaction.

    Opening is established independently for the V1.0 asset owner and physical
    location dimensions.  A fact for the same location under another asset
    owner is never accepted.  The establishment row is only the final pointer;
    all critical task, scope, submitted round, two-review and posting evidence
    is reread and checked before a normal inventory mutation may proceed.
    """

    if not isinstance(current_ledger_cursor, int) or current_ledger_cursor < 0:
        _fail(
            "inventory_ledger_cursor_invalid",
            "service_unavailable",
            "库存账本当前游标无效",
        )
    _require_valid_opening_establishments(
        db,
        accounts,
        current_ledger_cursor=current_ledger_cursor,
        effective_at=effective_at,
        terminal_opening_graphs=terminal_opening_graphs,
    )
    _require_no_active_hard_freezes(db, accounts, effective_at=effective_at)


def _require_valid_opening_establishments(
    db: Session,
    accounts: dict[uuid.UUID, StockAccount],
    *,
    current_ledger_cursor: int,
    effective_at: datetime,
    terminal_opening_graphs: Sequence[
        _PrelockedOpeningTerminalTaskGraph
    ] = (),
) -> None:
    effective_at_utc = _require_aware_datetime("effective_at", effective_at)
    if terminal_opening_graphs:
        _require_prelocked_opening_establishment_coordinates(
            db,
            accounts=accounts,
            terminal_opening_graphs=terminal_opening_graphs,
            current_ledger_cursor=current_ledger_cursor,
            effective_at=effective_at_utc,
        )
        return
    _fail(
        "inventory_opening_prelocked_graph_required",
        "precondition_failed",
        "正式库存过账缺少当前事务封存的期初任务图，请回滚后重试",
    )


def _require_prelocked_opening_establishment_coordinates(
    db: Session,
    *,
    accounts: Mapping[uuid.UUID, StockAccount],
    terminal_opening_graphs: Sequence[_PrelockedOpeningTerminalTaskGraph],
    current_ledger_cursor: int,
    effective_at: datetime,
) -> None:
    checked_graphs = tuple(
        _require_prelocked_opening_terminal_task_graph(db, graph)
        for graph in terminal_opening_graphs
    )
    expected_account_signatures = tuple(
        sorted(
            (
                signature
                for graph in checked_graphs
                for signature in graph.account_signatures
            ),
            key=lambda row: str(row[0]),
        )
    )
    current_account_signatures = tuple(
        _opening_account_signature(accounts[account_id])
        for account_id in sorted(accounts, key=str)
    )
    if current_account_signatures != expected_account_signatures:
        _invalid_opening_establishment()
    graph_by_task = {row.task_id: row for row in checked_graphs}
    if len(graph_by_task) != len(checked_graphs):
        _invalid_opening_establishment()
    expected_establishment_signatures = tuple(
        sorted(
            (
                signature
                for graph in checked_graphs
                for signature in graph.establishment_signatures
            ),
            key=lambda row: (str(row[3]), str(row[4]), str(row[0])),
        )
    )
    establishment_ids = tuple(
        signature[0] for signature in expected_establishment_signatures
    )
    current_establishments = tuple(
        db.scalars(
            select(InventoryOpeningEstablishment)
            .where(InventoryOpeningEstablishment.id.in_(establishment_ids))
            .order_by(
                InventoryOpeningEstablishment.owner_org_id,
                InventoryOpeningEstablishment.location_id,
                InventoryOpeningEstablishment.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if tuple(
        _opening_establishment_signature(row) for row in current_establishments
    ) != expected_establishment_signatures:
        _invalid_opening_establishment()
    establishment_by_pair = {
        (row.owner_org_id, row.location_id): row for row in current_establishments
    }
    for account in accounts.values():
        establishment = establishment_by_pair.get(
            (account.owner_org_id, account.location_id)
        )
        graph = (
            graph_by_task.get(establishment.task_id)
            if establishment is not None
            else None
        )
        cutoff_at = (
            _persisted_timestamp_utc(graph.task.cutoff_at)
            if graph is not None
            else None
        )
        if (
            establishment is None
            or graph is None
            or cutoff_at is None
            or establishment.established_ledger_cursor > current_ledger_cursor
        ):
            _invalid_opening_establishment()
        if effective_at < cutoff_at:
            _fail(
                "inventory_effective_at_before_opening_cutoff",
                "precondition_failed",
                "库存交易生效时间不能早于对应期初盘点截止时间",
            )


def validate_opening_task_evidence_for_replay(
    db: Session,
    *,
    task_id: uuid.UUID,
) -> None:
    """Re-prove one terminal opening through the sealed lock boundary.

    This compatibility entry point must never perform an unplanned evidence
    walk from an unknown caller lock state.  The finalizer owns the canonical
    ledger -> task/principal/evidence/reference/serial/balance -> audit plan;
    importing it lazily avoids the module initialization cycle while keeping
    the public test/replay contract on that sealed path.
    """

    from . import opening_stocktake_finalize as finalize_service

    root = finalize_service._lock_opening_terminal_task_batch_root(
        db,
        task_ids=(task_id,),
    )
    graph = finalize_service._lock_opening_terminal_task_batch_graph(
        db,
        root=root,
    )
    _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    finalize_service._validate_opening_inventory_batch_from_prelocked_graph(
        db,
        proof=graph,
        audit_proof=audit_proof,
        expected_ledger_cursor=root.current_ledger_cursor,
    )


def _validate_opening_task_evidence(
    db: Session,
    *,
    task_id: uuid.UUID,
    current_ledger_cursor: int,
    disposition_resolutions_by_round: Mapping[
        uuid.UUID, Mapping[uuid.UUID, object]
    ] | None = None,
    audit_replay_plan: object,
    audit_proof: object,
) -> tuple[
    FormalStocktakeTask,
    dict[tuple[uuid.UUID, uuid.UUID], InventoryOpeningEstablishment],
]:
    """Reread the complete graph under one transaction-bound audit proof."""

    try:
        _require_prelocked_audit_stream_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
        )
    except AuditChainError:
        _invalid_opening_establishment()
    expected_assignees_from_plan = (
        _validate_opening_task_audit_replay_from_prelocked_graph(
            db,
            plan=audit_replay_plan,
            audit_proof=audit_proof,
        )
    )

    task = db.get(FormalStocktakeTask, task_id)
    if task is None:
        _invalid_opening_establishment()
    assert task is not None
    cutoff_at = _persisted_timestamp_utc(task.cutoff_at)
    issued_at = _persisted_timestamp_utc(task.issued_at)
    frozen_at = _persisted_timestamp_utc(task.frozen_at)
    task_created_at = _persisted_timestamp_utc(task.created_at)
    submitted_at = _persisted_timestamp_utc(task.submitted_at)
    task_posted_at = _persisted_timestamp_utc(task.posted_at)
    closed_at = _persisted_timestamp_utc(task.closed_at)
    if (
        task.task_type != "opening"
        or task.status not in {"posted", "closed"}
        or task.cutoff_ledger_cursor is None
        or task.cutoff_ledger_cursor < 0
        or cutoff_at is None
        or issued_at is None
        or frozen_at is None
        or task_created_at is None
        or submitted_at is None
        or task_posted_at is None
        or task.current_round_no <= 0
        or task.snapshot_manifest_sha256 is None
        or task.scope_manifest_sha256 is None
        or task.control_manifest_sha256 is None
        or task.control_source_system_id is None
        or task.control_sync_run_id is None
        or _persisted_timestamp_utc(task.control_snapshot_at) is None
        or issued_at != cutoff_at
        or frozen_at != cutoff_at
        or task_created_at != cutoff_at
        or submitted_at < cutoff_at
        or task_posted_at < submitted_at
        or (task.status == "closed" and (closed_at is None or closed_at < task_posted_at))
        or (task.status == "posted" and closed_at is not None)
    ):
        _invalid_opening_establishment()
    region = db.get(Organization, task.region_org_id)
    scopes = db.scalars(
        select(FormalStocktakeScope)
        .where(FormalStocktakeScope.task_id == task.id)
        .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id)
    ).all()
    if (
        region is None
        or not scopes
        or [row.scope_no for row in scopes] != list(range(1, len(scopes) + 1))
        or any(
            row.scope_mode != "location_all"
            or row.material_id is not None
            or row.condition_code is not None
            or row.availability_bucket is not None
            for row in scopes
        )
    ):
        _invalid_opening_establishment()

    scope_by_id = {row.id: row for row in scopes}
    scope_by_pair = {
        (row.owner_org_id, row.location_id): row for row in scopes
    }
    if len(scope_by_pair) != len(scopes):
        _invalid_opening_establishment()
    owners = {
        row.id: row
        for row in db.scalars(
            select(Organization).where(
                Organization.id.in_({scope.owner_org_id for scope in scopes})
            )
        ).all()
    }
    if len(owners) != len({scope.owner_org_id for scope in scopes}):
        _invalid_opening_establishment()
    locations = {
        row.id: row
        for row in db.scalars(
            select(StockLocation).where(
                StockLocation.id.in_({scope.location_id for scope in scopes})
            )
        ).all()
    }
    if len(locations) != len({scope.location_id for scope in scopes}):
        _invalid_opening_establishment()
    freezes = db.scalars(
        select(InventoryFreeze)
        .where(InventoryFreeze.task_id == task.id)
        .order_by(InventoryFreeze.stocktake_scope_id)
    ).all()
    freezes_by_scope = {row.stocktake_scope_id: row for row in freezes}
    if (
        len(freezes_by_scope) != len(freezes)
        or set(freezes_by_scope) != set(scope_by_id)
        or any(
            row.scope_key != scope_by_id[row.stocktake_scope_id].scope_key
            or row.status != "released"
            or _persisted_timestamp_utc(row.valid_from) != cutoff_at
            or _persisted_timestamp_utc(row.created_at) != cutoff_at
            or (released_at := _persisted_timestamp_utc(row.valid_to)) is None
            or released_at < task_posted_at
            or (_persisted_timestamp_utc(row.updated_at) or cutoff_at)
            < released_at
            or row.released_by_user_id is None
            for row in freezes
        )
    ):
        _invalid_opening_establishment()
    for scope in scopes:
        if _persisted_timestamp_utc(scope.created_at) != frozen_at:
            _invalid_opening_establishment()
        try:
            if scope.scope_sha256 != canonical_opening_scope_line_sha256(scope):
                _invalid_opening_establishment()
        except (TypeError, ValueError):
            _invalid_opening_establishment()
    try:
        if task.scope_manifest_sha256 != canonical_opening_scope_manifest_sha256(
            task, scopes, freezes_by_scope
        ):
            _invalid_opening_establishment()
    except (TypeError, ValueError):
        _invalid_opening_establishment()

    establishments = db.scalars(
        select(InventoryOpeningEstablishment)
        .where(InventoryOpeningEstablishment.task_id == task.id)
        .order_by(
            InventoryOpeningEstablishment.owner_org_id,
            InventoryOpeningEstablishment.location_id,
        )
    ).all()
    establishment_by_pair = {
        (row.owner_org_id, row.location_id): row for row in establishments
    }
    if (
        len(establishments) != len(establishment_by_pair)
        or set(establishment_by_pair) != set(scope_by_pair)
    ):
        _invalid_opening_establishment()

    rounds = db.scalars(
        select(StocktakeRound)
        .where(StocktakeRound.task_id == task.id)
        .order_by(StocktakeRound.round_no)
    ).all()
    round_row, expected_assignee_by_scope = _validate_opening_recount_chain(
        db,
        task=task,
        scopes=scopes,
        freezes=freezes,
        rounds=rounds,
        disposition_resolutions_by_round=disposition_resolutions_by_round,
    )
    if expected_assignee_by_scope != expected_assignees_from_plan:
        _invalid_opening_establishment()

    reference = establishments[0]
    if any(
        row.scope_id != scope_by_pair[(row.owner_org_id, row.location_id)].id
        or row.round_id != round_row.id
        or row.posting_id != reference.posting_id
        or row.regional_review_id != reference.regional_review_id
        or row.headquarters_review_id != reference.headquarters_review_id
        or row.established_ledger_cursor
        != reference.established_ledger_cursor
        or row.cutoff_ledger_cursor != task.cutoff_ledger_cursor
        or _persisted_timestamp_utc(row.cutoff_at) != cutoff_at
        or row.scope_manifest_sha256 != task.scope_manifest_sha256
        or row.snapshot_manifest_sha256 != task.snapshot_manifest_sha256
        or row.count_manifest_sha256 != round_row.count_manifest_sha256
        or row.control_manifest_sha256 != task.control_manifest_sha256
        or row.established_ledger_cursor > current_ledger_cursor
        or _persisted_timestamp_utc(row.established_at) is None
        or _persisted_timestamp_utc(row.created_at) is None
        or _persisted_timestamp_utc(row.created_at)
        != _persisted_timestamp_utc(row.established_at)
        or _persisted_timestamp_utc(row.established_at) < task_posted_at
        for row in establishments
    ):
        _invalid_opening_establishment()

    snapshot_lines = db.scalars(
        select(StocktakeSnapshotLine)
        .where(StocktakeSnapshotLine.task_id == task.id)
        .order_by(StocktakeSnapshotLine.scope_id, StocktakeSnapshotLine.stock_account_id)
    ).all()
    count_lines = db.scalars(
        select(StocktakeCountLine)
        .where(
            StocktakeCountLine.task_id == task.id,
            StocktakeCountLine.round_id == round_row.id,
        )
        .order_by(StocktakeCountLine.scope_id, StocktakeCountLine.stock_account_id)
    ).all()
    count_serials = db.scalars(
        select(StocktakeCountSerial)
        .where(StocktakeCountSerial.round_id == round_row.id)
        .order_by(StocktakeCountSerial.count_line_id, StocktakeCountSerial.serial_id)
    ).all()
    observations = db.scalars(
        select(StocktakeCountObservation)
        .where(
            StocktakeCountObservation.task_id == task.id,
            StocktakeCountObservation.round_id == round_row.id,
        )
        .order_by(
            StocktakeCountObservation.scope_id,
            StocktakeCountObservation.observation_no,
        )
    ).all()
    control_lines = db.scalars(
        select(StocktakeControlSnapshotLine)
        .where(StocktakeControlSnapshotLine.task_id == task.id)
        .order_by(StocktakeControlSnapshotLine.line_no)
    ).all()
    _validate_opening_manifests(
        db,
        task=task,
        scopes=scopes,
        round_row=round_row,
        snapshot_lines=snapshot_lines,
        count_lines=count_lines,
        count_serials=count_serials,
        control_lines=control_lines,
    )
    accounts, count_serials_by_line = _validate_opening_count_evidence(
        db,
        task=task,
        scopes=scopes,
        scope_by_id=scope_by_id,
        round_row=round_row,
        snapshot_lines=snapshot_lines,
        count_lines=count_lines,
        count_serials=count_serials,
        expected_assignee_by_scope=expected_assignee_by_scope,
    )
    observation_accounts = _load_opening_observation_accounts(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        observations=observations,
    )
    all_accounts = {
        **accounts,
        **{row.id: row for row in observation_accounts.values()},
    }
    _validate_opening_control_source(db, task, control_lines)

    regional_review = db.get(StocktakeReview, reference.regional_review_id)
    headquarters_review = db.get(
        StocktakeReview, reference.headquarters_review_id
    )
    if regional_review is None or headquarters_review is None:
        _invalid_opening_establishment()
    assert regional_review is not None
    assert headquarters_review is not None
    _validate_opening_reviews(
        db,
        task=task,
        round_row=round_row,
        regional_review=regional_review,
        headquarters_review=headquarters_review,
        establishments=establishments,
        scope_by_id=scope_by_id,
        accounts=all_accounts,
        count_lines=count_lines,
        control_lines=control_lines,
        observations=observations,
    )

    posting = db.get(StocktakePosting, reference.posting_id)
    if posting is None:
        _invalid_opening_establishment()
    assert posting is not None
    _validate_opening_posting(
        db,
        task=task,
        round_row=round_row,
        posting=posting,
        establishments=establishments,
        scopes=scopes,
        count_lines=count_lines,
        count_serials_by_line=count_serials_by_line,
        accounts=all_accounts,
        observations=observations,
        observation_accounts=observation_accounts,
    )
    return task, establishment_by_pair


def _validate_opening_recount_chain(
    db: Session,
    *,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    freezes: Sequence[InventoryFreeze],
    rounds: Sequence[StocktakeRound],
    disposition_resolutions_by_round: Mapping[
        uuid.UUID, Mapping[uuid.UUID, object]
    ] | None = None,
) -> tuple[StocktakeRound, dict[uuid.UUID, str]]:
    """Re-prove every append-only recount edge before trusting the final round."""

    # Deliberately local: opening_stocktake_recount imports this module's
    # decision-manifest helper while it is initializing.
    from .opening_stocktake_count import (
        OpeningStocktakeCountError,
        _difference_set_request_sha256,
        _difference_set_summary,
        _event_hash as count_event_hash,
        _hash_document as count_hash_document,
        _persisted_count_manifest_sha256,
        _round_manifest_sha256,
        _validate_completion_evidence,
        _validate_initial_difference_set,
    )
    from .opening_stocktake_recount import (
        OpeningStocktakeRecountScopeAssignmentInput,
        canonical_opening_recount_assignment_manifest_sha256,
        canonical_opening_recount_assignment_sha256,
        canonical_opening_recount_authorization_sha256,
        canonical_opening_recount_manifest_sha256,
        canonical_opening_recount_request_sha256,
        canonical_opening_recount_scope_manifest_sha256,
    )

    submitted_at = _persisted_timestamp_utc(task.submitted_at)
    if (
        not rounds
        or [row.round_no for row in rounds]
        != list(range(1, task.current_round_no + 1))
        or any(row.status != "submitted" for row in rounds)
        or rounds[0].round_type != "initial"
        or rounds[0].recount_case_id is not None
        or any(
            row.round_type != "recount" or row.recount_case_id is None
            for row in rounds[1:]
        )
    ):
        _invalid_opening_establishment()
    for row in rounds:
        started = _persisted_timestamp_utc(row.started_at)
        sealed = _persisted_timestamp_utc(row.submitted_at)
        created = _persisted_timestamp_utc(row.created_at)
        updated = _persisted_timestamp_utc(row.updated_at)
        if (
            row.task_id != task.id
            or row.submitted_by_user_id is None
            or not _SHA256_HEX.fullmatch(row.count_manifest_sha256 or "")
            or started is None
            or sealed is None
            or created is None
            or updated is None
            or created > started
            or sealed < started
            or updated < sealed
        ):
            _invalid_opening_establishment()
    final_round = rounds[-1]
    if _persisted_timestamp_utc(final_round.submitted_at) != submitted_at:
        _invalid_opening_establishment()

    cases = db.scalars(
        select(StocktakeRecountCase)
        .where(StocktakeRecountCase.task_id == task.id)
        .order_by(StocktakeRecountCase.next_round_no)
    ).all()
    assignments = db.scalars(
        select(StocktakeRecountScopeAssignment)
        .where(StocktakeRecountScopeAssignment.task_id == task.id)
        .order_by(
            StocktakeRecountScopeAssignment.recount_case_id,
            StocktakeRecountScopeAssignment.scope_id,
        )
    ).all()
    case_by_id = {row.id: row for row in cases}
    assignments_by_case: dict[
        uuid.UUID, list[StocktakeRecountScopeAssignment]
    ] = defaultdict(list)
    for assignment in assignments:
        assignments_by_case[assignment.recount_case_id].append(assignment)
    if (
        len(cases) != len(rounds) - 1
        or len(case_by_id) != len(cases)
        or {row.recount_case_id for row in rounds[1:]} != set(case_by_id)
        or set(assignments_by_case) != set(case_by_id)
    ):
        _invalid_opening_establishment()
    assignment_by_round: dict[
        uuid.UUID, dict[uuid.UUID, StocktakeRecountScopeAssignment]
    ] = {}
    for recount_round in rounds[1:]:
        round_assignments = assignments_by_case[recount_round.recount_case_id]
        assignment_map = {row.scope_id: row for row in round_assignments}
        if (
            len(round_assignments) != len(scopes)
            or len(assignment_map) != len(round_assignments)
            or set(assignment_map) != {row.id for row in scopes}
        ):
            _invalid_opening_establishment()
        assignment_by_round[recount_round.id] = assignment_map

    submissions = db.scalars(
        select(StocktakeRoundSubmission)
        .where(StocktakeRoundSubmission.task_id == task.id)
        .order_by(StocktakeRoundSubmission.round_id)
    ).all()
    completions = db.scalars(
        select(StocktakeDifferenceSetCompletion)
        .where(StocktakeDifferenceSetCompletion.task_id == task.id)
        .order_by(StocktakeDifferenceSetCompletion.round_id)
    ).all()
    submission_by_round = {row.round_id: row for row in submissions}
    completion_by_round = {row.round_id: row for row in completions}
    scope_completions = db.scalars(
        select(StocktakeScopeCountCompletion)
        .where(StocktakeScopeCountCompletion.task_id == task.id)
        .order_by(
            StocktakeScopeCountCompletion.round_id,
            StocktakeScopeCountCompletion.scope_id,
        )
    ).all()
    scope_completions_by_round: dict[
        uuid.UUID, list[StocktakeScopeCountCompletion]
    ] = defaultdict(list)
    for completion in scope_completions:
        scope_completions_by_round[completion.round_id].append(completion)
    if (
        len(submissions) != len(rounds)
        or len(completions) != len(rounds)
        or set(submission_by_round) != {row.id for row in rounds}
        or set(completion_by_round) != {row.id for row in rounds}
    ):
        _invalid_opening_establishment()
    scope_by_id = {row.id: row for row in scopes}
    for row in rounds:
        submission = submission_by_round[row.id]
        completion = completion_by_round[row.id]
        round_scope_completions = scope_completions_by_round.get(row.id, [])
        round_started = _persisted_timestamp_utc(row.started_at)
        round_submitted = _persisted_timestamp_utc(row.submitted_at)
        completion_at = _persisted_timestamp_utc(completion.completed_at)
        sealing_completion = next(
            (
                value
                for value in round_scope_completions
                if value.id == submission.sealing_completion_id
            ),
            None,
        )
        if (
            len(round_scope_completions) != len(scopes)
            or {value.scope_id for value in round_scope_completions}
            != set(scope_by_id)
            or sealing_completion is None
            or round_started is None
            or round_submitted is None
            or any(
                (completed_at := _persisted_timestamp_utc(value.completed_at))
                is None
                or completed_at < round_started
                or completed_at > round_submitted
                for value in round_scope_completions
            )
        ):
            _invalid_opening_establishment()
        try:
            for scope_completion in round_scope_completions:
                _validate_completion_evidence(
                    db,
                    task,
                    row,
                    scope_by_id[scope_completion.scope_id],
                    scope_completion,
                    expected_recount_assignment=assignment_by_round.get(
                        row.id, {}
                    ).get(scope_completion.scope_id),
                )
            round_manifest = _round_manifest_sha256(
                task.id,
                row.id,
                round_scope_completions,
                submission.sealing_completion_id,
            )
            persisted_count_manifest = _persisted_count_manifest_sha256(
                db, task, row
            )
            submission_request = count_hash_document(
                {
                    "count_manifest_sha256": persisted_count_manifest,
                    "round_id": str(row.id),
                    "round_manifest_sha256": round_manifest,
                    "schema": "cloud_oam.opening_stocktake.round_submission_request.v3",
                    "sealing_completion_id": str(submission.sealing_completion_id),
                }
            )
            round_differences = db.scalars(
                select(StocktakeDifference)
                .where(
                    StocktakeDifference.task_id == task.id,
                    StocktakeDifference.round_id == row.id,
                )
                .order_by(StocktakeDifference.difference_no)
            ).all()
            _validate_initial_difference_set(db, task, row)
            difference_summary = _difference_set_summary(
                task=task,
                round_row=row,
                submission=submission,
                differences=round_differences,
            )
        except (OpeningStocktakeCountError, KeyError, TypeError, ValueError):
            _invalid_opening_establishment()
        submission_assignment = db.get(
            RoleAssignment, submission.submitted_role_assignment_id
        )
        submission_user = db.get(User, submission.submitted_by_user_id)
        if (
            submission.task_id != task.id
            or submission.round_id != row.id
            or submission.scope_count != len(scopes)
            or submission.zero_scope_count
            != sum(1 for value in round_scope_completions if value.zero_confirmed)
            or submission.count_line_count
            != sum(value.count_line_count for value in round_scope_completions)
            or submission.observation_line_count
            != sum(value.observation_line_count for value in round_scope_completions)
            or submission.serial_count
            != sum(value.serial_count for value in round_scope_completions)
            or submission.total_counted_qty
            != sum(
                (value.total_counted_qty for value in round_scope_completions),
                start=_ZERO,
            )
            or submission.round_manifest_sha256 != round_manifest
            or submission.count_manifest_sha256 != persisted_count_manifest
            or row.count_manifest_sha256 != persisted_count_manifest
            or submission.request_sha256 != submission_request
            or submission.idempotency_key_hash
            != count_event_hash("round-submission", row.id, task.id)
            or _persisted_timestamp_utc(submission.submitted_at) != round_submitted
            or _persisted_timestamp_utc(submission.created_at) != round_submitted
            or row.submitted_by_user_id != submission.submitted_by_user_id
            or submission_assignment is None
            or submission_user is None
            or submission_assignment.user_id != submission.submitted_by_user_id
            or submission_user.person_id != submission.submitted_by_person_id
            or submission_user.authorization_version
            < submission.authorization_version
            or (_persisted_timestamp_utc(submission_assignment.valid_from) or round_submitted)
            > round_submitted
            or (
                submission_assignment.valid_to is not None
                and round_submitted
                >= (_persisted_timestamp_utc(submission_assignment.valid_to) or round_submitted)
            )
            or (
                submission_assignment.revoked_at is not None
                and round_submitted
                >= (_persisted_timestamp_utc(submission_assignment.revoked_at) or round_submitted)
            )
            or sealing_completion.completed_by_user_id
            != submission.submitted_by_user_id
            or sealing_completion.completed_by_person_id
            != submission.submitted_by_person_id
            or sealing_completion.completed_role_assignment_id
            != submission.submitted_role_assignment_id
            or _persisted_timestamp_utc(sealing_completion.completed_at)
            != round_submitted
            or completion.task_id != task.id
            or completion.round_id != row.id
            or completion.round_submission_id != submission.id
            or completion_at is None
            or round_submitted is None
            or completion_at < round_submitted
            or _persisted_timestamp_utc(completion.created_at) != completion_at
            or completion.difference_count
            != difference_summary["difference_count"]
            or completion.physical_difference_count
            != difference_summary["physical_difference_count"]
            or completion.control_difference_count
            != difference_summary["control_difference_count"]
            or completion.pending_observation_difference_count
            != difference_summary["pending_observation_difference_count"]
            or completion.total_affected_qty
            != difference_summary["total_affected_qty"]
            or completion.difference_manifest_sha256
            != difference_summary["difference_manifest_sha256"]
            or completion.request_sha256
            != _difference_set_request_sha256(difference_summary)
            or completion.idempotency_key_hash
            != count_event_hash("difference-set-completion", row.id, submission.id)
        ):
            _invalid_opening_establishment()

    if len(rounds) == 1:
        return final_round, {row.id: row.assignee_user_id for row in scopes}

    try:
        scope_manifest = canonical_opening_recount_scope_manifest_sha256(
            task.region_org_id, scopes, freezes
        )
    except (KeyError, TypeError, ValueError):
        _invalid_opening_establishment()
    reviews = db.scalars(
        select(StocktakeReview)
        .where(StocktakeReview.task_id == task.id)
        .order_by(StocktakeReview.round_id, StocktakeReview.reviewed_at)
    ).all()
    reviews_by_round: dict[uuid.UUID, list[StocktakeReview]] = defaultdict(list)
    for row in reviews:
        reviews_by_round[row.round_id].append(row)

    for index, successor in enumerate(rounds[1:], start=1):
        source = rounds[index - 1]
        case = case_by_id[successor.recount_case_id]
        source_submission = submission_by_round[source.id]
        source_completion = completion_by_round[source.id]
        opened_at = _persisted_timestamp_utc(case.opened_at)
        successor_started = _persisted_timestamp_utc(successor.started_at)
        source_differences = db.scalars(
            select(StocktakeDifference)
            .where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.round_id == source.id,
            )
            .order_by(StocktakeDifference.difference_no)
        ).all()
        try:
            summary = _difference_set_summary(
                task=task,
                round_row=source,
                submission=source_submission,
                differences=source_differences,
            )
            completion_valid = (
                source_completion.difference_count == summary["difference_count"]
                and source_completion.physical_difference_count
                == summary["physical_difference_count"]
                and source_completion.control_difference_count
                == summary["control_difference_count"]
                and source_completion.pending_observation_difference_count
                == summary["pending_observation_difference_count"]
                and source_completion.total_affected_qty
                == summary["total_affected_qty"]
                and source_completion.difference_manifest_sha256
                == summary["difference_manifest_sha256"]
                and source_completion.request_sha256
                == _difference_set_request_sha256(summary)
                and source_completion.idempotency_key_hash
                == count_event_hash(
                    "difference-set-completion", source.id, source_submission.id
                )
            )
        except (OpeningStocktakeCountError, KeyError, TypeError, ValueError):
            completion_valid = False
        source_reviews = reviews_by_round.get(source.id, [])
        regional_triggers = tuple(
            row for row in source_reviews if row.review_stage == "region"
        )
        headquarters_triggers = tuple(
            row
            for row in source_reviews
            if row.review_stage == "headquarters"
        )
        if len(regional_triggers) != 1 or len(headquarters_triggers) > 1:
            _invalid_opening_establishment()
        regional_trigger = regional_triggers[0]
        headquarters_trigger = (
            headquarters_triggers[0] if headquarters_triggers else None
        )
        if (
            regional_trigger.decision in {"recount", "reject"}
            and headquarters_trigger is None
        ):
            trigger = regional_trigger
        elif (
            regional_trigger.decision == "approve"
            and headquarters_trigger is not None
            and headquarters_trigger.decision == "reject"
        ):
            trigger = headquarters_trigger
        else:
            _invalid_opening_establishment()
        if (
            not completion_valid
            or case.task_id != task.id
            or case.source_round_id != source.id
            or case.source_round_submission_id != source_submission.id
            or case.source_difference_completion_id != source_completion.id
            or case.trigger_review_id != trigger.id
            or case.next_round_no != successor.round_no
            or case.scope_count != len(scopes)
            or case.scope_manifest_sha256 != scope_manifest
            or not (
                (
                    case.role_code == "admin"
                    and case.scope_type == "national"
                    and case.scope_id_snapshot == "*"
                )
                or (
                    case.role_code == "provincial_manager"
                    and case.scope_type == "organization"
                    and case.scope_id_snapshot == str(task.region_org_id)
                )
            )
            or opened_at is None
            or successor_started is None
            or _persisted_timestamp_utc(case.created_at) != opened_at
            or (_persisted_timestamp_utc(trigger.reviewed_at) or opened_at) > opened_at
            or opened_at > successor_started
            or successor.idempotency_key_hash != _opening_recount_round_key(case.id)
            or db.scalar(
                select(StocktakePosting.id).where(
                    StocktakePosting.task_id == task.id,
                    StocktakePosting.round_id == source.id,
                )
            )
            is not None
            or db.scalar(
                select(InventoryOpeningEstablishment.id).where(
                    InventoryOpeningEstablishment.task_id == task.id,
                    InventoryOpeningEstablishment.round_id == source.id,
                )
            )
            is not None
        ):
            _invalid_opening_establishment()

        trigger_items = db.scalars(
            select(StocktakeReviewItem).where(
                StocktakeReviewItem.review_id == trigger.id
            )
        ).all()
        decisions = {row.difference_id: row.decision for row in trigger_items}
        if (
            len(decisions) != len(trigger_items)
            or set(decisions) != {row.id for row in source_differences}
        ):
            _invalid_opening_establishment()
        try:
            trigger_manifest = canonical_opening_decision_manifest_sha256(
                task_id=task.id,
                round_id=source.id,
                differences=source_differences,
                decisions=decisions,
            )
        except (TypeError, ValueError):
            trigger_manifest = ""
        if trigger.decision_manifest_sha256 != trigger_manifest:
            _invalid_opening_establishment()

        case_assignments = assignments_by_case[case.id]
        if (
            len(case_assignments) != len(scopes)
            or {row.scope_id for row in case_assignments} != set(scope_by_id)
        ):
            _invalid_opening_establishment()
        _validate_opening_recount_authorization(
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
            for row in case_assignments:
                assigned_at = _persisted_timestamp_utc(row.assigned_at)
                scope = scope_by_id[row.scope_id]
                if assigned_at is None or _persisted_timestamp_utc(row.created_at) != assigned_at:
                    _invalid_opening_establishment()
                if (
                    row.recount_case_id != case.id
                    or row.task_id != task.id
                    or row.source_round_id != source.id
                    or assigned_at != opened_at
                ):
                    _invalid_opening_establishment()
                _validate_opening_recount_authorization(
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
                if not _opening_recount_assignment_scope_exact(db, row, scope):
                    _invalid_opening_establishment()
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
                    source_round_id=source.id,
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
                if row.authorization_sha256 != assignment_auth or row.assignment_sha256 != assignment_hash:
                    _invalid_opening_establishment()
            assignment_manifest = canonical_opening_recount_assignment_manifest_sha256(
                case_assignments
            )
            request_sha = canonical_opening_recount_request_sha256(
                actor_user_id=case.opened_by_user_id,
                actor_person_id=case.opened_by_person_id,
                task_id=task.id,
                source_round_id=source.id,
                assignments=tuple(
                    OpeningStocktakeRecountScopeAssignmentInput(
                        scope_id=row.scope_id,
                        assignee_user_id=row.assignee_user_id,
                    )
                    for row in case_assignments
                ),
                reason=case.reason,
            )
            recount_manifest = canonical_opening_recount_manifest_sha256(
                recount_case_id=case.id,
                task_id=task.id,
                source_round_id=source.id,
                source_round_submission_id=source_submission.id,
                source_round_manifest_sha256=source_submission.round_manifest_sha256,
                source_count_manifest_sha256=source.count_manifest_sha256 or "",
                source_difference_completion_id=source_completion.id,
                source_difference_manifest_sha256=source_completion.difference_manifest_sha256,
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
        except (TypeError, ValueError):
            _invalid_opening_establishment()
        if (
            case.authorization_sha256 != opener_auth
            or case.assignment_manifest_sha256 != assignment_manifest
            or case.request_sha256 != request_sha
            or case.recount_manifest_sha256 != recount_manifest
        ):
            _invalid_opening_establishment()
    final_assignments = assignments_by_case[final_round.recount_case_id]
    return final_round, {row.scope_id: row.assignee_user_id for row in final_assignments}


def _validate_opening_recount_authorization(
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
    user = db.get(User, user_id)
    person = db.get(Person, person_id)
    assignment = db.get(RoleAssignment, assignment_id)
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    if (
        user is None
        or person is None
        or assignment is None
        or role is None
        or user.person_id != person_id
        or user.authorization_version < authorization_version
        or assignment.user_id != user_id
        or assignment.status not in {"active", "expired", "revoked"}
        or (_persisted_timestamp_utc(assignment.valid_from) or occurred_at) > occurred_at
        or (
            assignment.valid_to is not None
            and occurred_at >= (_persisted_timestamp_utc(assignment.valid_to) or occurred_at)
        )
        or (
            assignment.revoked_at is not None
            and occurred_at >= (_persisted_timestamp_utc(assignment.revoked_at) or occurred_at)
        )
        or role.is_external
        or role.code != role_code
        or assignment.scope_type != scope_type
        or assignment.scope_id != scope_id
    ):
        _invalid_opening_establishment()


def _opening_recount_assignment_scope_exact(
    db: Session,
    row: StocktakeRecountScopeAssignment,
    scope: FormalStocktakeScope,
) -> bool:
    location = db.get(StockLocation, scope.location_id)
    return bool(
        (row.role_code == "admin" and row.scope_type == "national" and row.scope_id_snapshot == "*")
        or (
            row.role_code == "provincial_manager"
            and row.scope_type == "organization"
            and row.scope_id_snapshot == str(scope.owner_org_id)
        )
        or (
            row.role_code == "technician"
            and row.scope_type == "person"
            and location is not None
            and location.location_type == "personal"
            and scope.custodian_person_id_snapshot is not None
            and row.assignee_person_id == scope.custodian_person_id_snapshot
            and row.scope_id_snapshot == str(scope.custodian_person_id_snapshot)
        )
    )


def _opening_recount_round_key(case_id: uuid.UUID) -> str:
    return hashlib.sha256(
        f"cloud_oam.opening_stocktake.recount.round.v1\0{case_id}".encode()
    ).hexdigest()


def _opening_recount_event_key(kind: str, case_id: uuid.UUID) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.opening_stocktake.recount.{kind}.v1\0{case_id}".encode()
    ).hexdigest()
    return f"opening-recount-{kind}-{digest}"


def _validate_opening_recount_side_effects(
    db: Session,
    *,
    task: FormalStocktakeTask,
    case: StocktakeRecountCase,
    next_round_id: uuid.UUID,
) -> None:
    # One authoritative exact-set proof is shared with count/review/recount.
    # The import is local because recount imports posting's canonical manifest
    # helpers during module initialization.
    from .opening_stocktake_recount import (
        OpeningStocktakeRecountError,
        _validate_side_effects as validate_recount_side_effects,
    )

    try:
        validate_recount_side_effects(
            db,
            task=task,
            case=case,
            next_round_id=next_round_id,
        )
    except OpeningStocktakeRecountError:
        _invalid_opening_establishment()


def _validate_opening_manifests(
    db: Session,
    *,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    round_row: StocktakeRound,
    snapshot_lines: Sequence[StocktakeSnapshotLine],
    count_lines: Sequence[StocktakeCountLine],
    count_serials: Sequence[StocktakeCountSerial],
    control_lines: Sequence[StocktakeControlSnapshotLine],
) -> None:
    sync_run = db.get(SyncRun, task.control_sync_run_id)
    if sync_run is None:
        _invalid_opening_establishment()
    assert sync_run is not None
    try:
        valid = (
            task.snapshot_manifest_sha256
            == canonical_opening_snapshot_manifest_sha256(
                task, scopes, snapshot_lines
            )
            and round_row.count_manifest_sha256
            == canonical_opening_count_manifest_sha256(
                task, round_row, count_lines, count_serials
            )
            and task.control_manifest_sha256
            == canonical_opening_control_manifest_sha256(
                task, sync_run, control_lines
            )
        )
    except (TypeError, ValueError):
        valid = False
    if not valid:
        _invalid_opening_establishment()


def _validate_opening_count_evidence(
    db: Session,
    *,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    scope_by_id: Mapping[uuid.UUID, FormalStocktakeScope],
    round_row: StocktakeRound,
    snapshot_lines: Sequence[StocktakeSnapshotLine],
    count_lines: Sequence[StocktakeCountLine],
    count_serials: Sequence[StocktakeCountSerial],
    expected_assignee_by_scope: Mapping[uuid.UUID, str],
) -> tuple[
    dict[uuid.UUID, StockAccount],
    dict[uuid.UUID, set[uuid.UUID]],
]:
    cutoff_at = _persisted_timestamp_utc(task.cutoff_at)
    frozen_at = _persisted_timestamp_utc(task.frozen_at)
    round_started_at = _persisted_timestamp_utc(round_row.started_at)
    round_submitted_at = _persisted_timestamp_utc(round_row.submitted_at)
    if cutoff_at is None:
        _invalid_opening_establishment()
    if (
        frozen_at is None
        or round_started_at is None
        or round_submitted_at is None
        or round_started_at < frozen_at
        or round_submitted_at < round_started_at
    ):
        _invalid_opening_establishment()
    location_ids = tuple(sorted({row.location_id for row in scopes}, key=str))
    location_statement = _select_only_reference_statement(
        db,
        select(StockLocation)
        .where(StockLocation.id.in_(location_ids))
        .order_by(StockLocation.id),
    )
    locations = {
        row.id: row
        for row in db.scalars(
            location_statement.execution_options(populate_existing=True)
        ).all()
    }
    if (
        len(locations) != len(location_ids)
        or any(
            row.location_type not in {"region", "personal"}
            for row in locations.values()
        )
    ):
        _invalid_opening_establishment()
    all_scope_accounts = db.scalars(
        select(StockAccount).where(
            or_(
                *(
                    and_(
                        StockAccount.owner_org_id == scope.owner_org_id,
                        StockAccount.location_id == scope.location_id,
                    )
                    for scope in scopes
                )
            )
        )
    ).all()
    eligible_accounts = {
        row.id: row
        for row in all_scope_accounts
        if (created_at := _persisted_timestamp_utc(row.created_at)) is not None
        and created_at <= cutoff_at
    }
    snapshot_by_account = {row.stock_account_id: row for row in snapshot_lines}
    count_by_account = {row.stock_account_id: row for row in count_lines}
    expected_ids = set(eligible_accounts)
    if (
        len(snapshot_by_account) != len(snapshot_lines)
        or len(count_by_account) != len(count_lines)
        or set(snapshot_by_account) != expected_ids
        or set(count_by_account) != expected_ids
    ):
        _invalid_opening_establishment()

    count_line_by_id = {row.id: row for row in count_lines}
    serial_rows_by_line: dict[uuid.UUID, list[StocktakeCountSerial]] = defaultdict(
        list
    )
    for serial_row in count_serials:
        if (
            serial_row.round_id != round_row.id
            or serial_row.count_line_id not in count_line_by_id
            or _persisted_timestamp_utc(serial_row.created_at) is None
            or _persisted_timestamp_utc(serial_row.created_at)
            < round_started_at
            or _persisted_timestamp_utc(serial_row.created_at)
            > round_submitted_at
        ):
            _invalid_opening_establishment()
        serial_rows_by_line[serial_row.count_line_id].append(serial_row)

    material_ids = {row.material_id for row in eligible_accounts.values()}
    policies = _load_effective_policies(db, material_ids, cutoff_at)
    all_evidence_serial_ids: set[uuid.UUID] = set()
    for account_id in sorted(expected_ids, key=str):
        account = eligible_accounts[account_id]
        snapshot = snapshot_by_account[account_id]
        count = count_by_account[account_id]
        scope = scope_by_id.get(snapshot.scope_id)
        location = locations.get(scope.location_id) if scope is not None else None
        counted_at = _persisted_timestamp_utc(count.counted_at)
        if (
            scope is None
            or location is None
            or count.scope_id != scope.id
            or snapshot.task_id != task.id
            or count.task_id != task.id
            or count.round_id != round_row.id
            or account.owner_org_id != scope.owner_org_id
            or account.location_id != scope.location_id
            or (
                location.location_type == "personal"
                and account.custodian_person_id
                != scope.custodian_person_id_snapshot
            )
            or snapshot.ledger_cursor != task.cutoff_ledger_cursor
            or snapshot.book_qty != _ZERO
            or _persisted_timestamp_utc(snapshot.created_at) != frozen_at
            or count.counted_by_user_id
            != expected_assignee_by_scope.get(scope.id)
            or counted_at is None
            or counted_at < round_started_at
            or counted_at > round_submitted_at
            or _persisted_timestamp_utc(count.created_at) is None
            or _persisted_timestamp_utc(count.created_at) < round_started_at
            or _persisted_timestamp_utc(count.created_at) > round_submitted_at
        ):
            _invalid_opening_establishment()
        try:
            if (
                snapshot.serial_snapshot_jsonb != []
                or snapshot.serial_count != 0
                or snapshot.serial_snapshot_sha256
                != canonical_opening_serial_snapshot_sha256(
                    stock_account_id=account.id,
                    serials=(),
                )
                or snapshot.account_dimension_sha256
                != canonical_opening_account_dimension_sha256(account)
            ):
                _invalid_opening_establishment()
        except (TypeError, ValueError):
            _invalid_opening_establishment()

        serial_rows = serial_rows_by_line.get(count.id, [])
        policy = policies[account.material_id]
        serial_tracking = policy.tracking_mode in {"serial", "lot_and_serial"}
        if serial_tracking:
            if (
                count.counted_qty != count.counted_qty.to_integral_value()
                or len(serial_rows) != int(count.counted_qty)
                or any(
                    row.result not in {"present", "unexpected"}
                    for row in serial_rows
                )
            ):
                _invalid_opening_establishment()
        elif serial_rows:
            _invalid_opening_establishment()
        for serial_row in serial_rows:
            if serial_row.serial_id in all_evidence_serial_ids:
                _invalid_opening_establishment()
            all_evidence_serial_ids.add(serial_row.serial_id)

    serials = {
        row.id: row
        for row in db.scalars(
            select(InventorySerial).where(
                InventorySerial.id.in_(all_evidence_serial_ids)
            )
        ).all()
    }
    if len(serials) != len(all_evidence_serial_ids):
        _invalid_opening_establishment()
    for line_id, serial_rows in serial_rows_by_line.items():
        account = eligible_accounts[count_line_by_id[line_id].stock_account_id]
        for serial_row in serial_rows:
            serial = serials[serial_row.serial_id]
            if (
                serial.material_id != account.material_id
                or serial.lot_id != account.lot_id
            ):
                _invalid_opening_establishment()
    if expected_ids:
        pre_cutoff_movement = db.scalar(
            select(InventoryMovement.id)
            .join(
                InventoryTransaction,
                InventoryTransaction.id == InventoryMovement.transaction_id,
            )
            .where(
                InventoryTransaction.status == "posted",
                InventoryTransaction.ledger_cursor <= task.cutoff_ledger_cursor,
                or_(
                    InventoryMovement.from_account_id.in_(expected_ids),
                    InventoryMovement.to_account_id.in_(expected_ids),
                ),
            )
            .limit(1)
        )
        if pre_cutoff_movement is not None:
            _invalid_opening_establishment()
    return eligible_accounts, {
        line_id: {row.serial_id for row in rows}
        for line_id, rows in serial_rows_by_line.items()
    }


def _load_opening_observation_accounts(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    observations: Sequence[StocktakeCountObservation],
) -> dict[uuid.UUID, StockAccount]:
    if not observations:
        return {}
    cutoff_at = _persisted_timestamp_utc(task.cutoff_at)
    posted_at = _persisted_timestamp_utc(task.posted_at)
    scope_by_id = {row.id: row for row in scopes}
    if (
        cutoff_at is None
        or posted_at is None
        or round_row.task_id != task.id
        or round_row.round_no <= 1
        or round_row.round_no != task.current_round_no
        or round_row.round_type != "recount"
        or round_row.status != "submitted"
    ):
        _invalid_opening_establishment()
    result: dict[uuid.UUID, StockAccount] = {}
    for observation in observations:
        scope = scope_by_id.get(observation.scope_id)
        if (
            scope is None
            or observation.task_id != task.id
            or observation.round_id != round_row.id
            or observation.verification_status != "verified"
            or observation.material_id is None
            or observation.owner_org_id != scope.owner_org_id
            or observation.location_id != scope.location_id
            or observation.custodian_person_id_snapshot
            != scope.custodian_person_id_snapshot
            or observation.counted_qty <= _ZERO
        ):
            _invalid_opening_establishment()
        rows = tuple(
            db.scalars(
                select(StockAccount)
                .where(
                    StockAccount.owner_org_id == observation.owner_org_id,
                    StockAccount.custodian_person_id
                    == observation.custodian_person_id_snapshot,
                    StockAccount.location_id == observation.location_id,
                    StockAccount.material_id == observation.material_id,
                    StockAccount.condition_code == observation.condition_code,
                    StockAccount.availability_bucket
                    == observation.availability_bucket,
                    StockAccount.lot_id == observation.lot_id,
                )
                .order_by(StockAccount.id)
            ).all()
        )
        if len(rows) != 1:
            _invalid_opening_establishment()
        account = rows[0]
        created_at = _persisted_timestamp_utc(account.created_at)
        if (
            created_at is None
            or created_at <= cutoff_at
            or created_at > posted_at
        ):
            _invalid_opening_establishment()
        result[observation.id] = account
    if set(result) != {row.id for row in observations}:
        _invalid_opening_establishment()
    _require_active_account_masters(
        db,
        {row.id: row for row in result.values()},
    )
    return result


def _validate_opening_control_source(
    db: Session,
    task: FormalStocktakeTask,
    control_lines: Sequence[StocktakeControlSnapshotLine],
) -> None:
    source = db.get(SourceSystem, task.control_source_system_id)
    sync_run = db.get(SyncRun, task.control_sync_run_id)
    control_at = _persisted_timestamp_utc(task.control_snapshot_at)
    cutoff_at = _persisted_timestamp_utc(task.cutoff_at)
    completed_at = (
        _persisted_timestamp_utc(sync_run.completed_at)
        if sync_run is not None
        else None
    )
    started_at = (
        _persisted_timestamp_utc(sync_run.started_at)
        if sync_run is not None
        else None
    )
    if (
        source is None
        or source.code.strip().casefold() != "oam"
        or source.mode not in {"read_only", "mirror_only"}
        or sync_run is None
        or sync_run.source_system_id != source.id
        or sync_run.status != "completed"
        or sync_run.manifest_sha256 is None
        or started_at is None
        or completed_at is None
        or started_at > completed_at
        or _persisted_timestamp_utc(sync_run.created_at) is None
        or _persisted_timestamp_utc(sync_run.created_at) > completed_at
        or sync_run.failure_code is not None
        or sync_run.failure_detail is not None
        or control_at is None
        or cutoff_at is None
        or completed_at != control_at
        or control_at > cutoff_at
        or sync_run.scope_key
        != f"oam_inventory_control:region:{task.region_org_id}"
        or sync_run.manifest_sha256 != task.control_manifest_sha256
        or [row.line_no for row in control_lines]
        != list(range(1, len(control_lines) + 1))
        or any(row.task_id != task.id for row in control_lines)
        or any(
            _persisted_timestamp_utc(row.created_at)
            != (_persisted_timestamp_utc(task.frozen_at) or cutoff_at)
            for row in control_lines
        )
    ):
        _invalid_opening_establishment()

    assert sync_run is not None
    batches = db.scalars(
        select(SyncBatch)
        .where(
            SyncBatch.run_id == sync_run.id,
            SyncBatch.entity_type == OPENING_CONTROL_ENTITY_TYPE,
        )
        .order_by(SyncBatch.sequence, SyncBatch.id)
    ).all()
    if not batches:
        _invalid_opening_establishment()
    if [row.sequence for row in batches] != list(range(1, len(batches) + 1)):
        _invalid_opening_establishment()
    event_rows = db.execute(
        select(SyncInboxEvent, SyncBatch)
        .join(SyncBatch, SyncBatch.id == SyncInboxEvent.batch_id)
        .where(
            SyncBatch.run_id == sync_run.id,
            SyncInboxEvent.entity_type == OPENING_CONTROL_ENTITY_TYPE,
        )
        .order_by(SyncInboxEvent.id)
    ).all()
    if len(event_rows) != len(control_lines):
        _invalid_opening_establishment()
    events_by_batch: dict[uuid.UUID, list[SyncInboxEvent]] = {
        batch.id: [] for batch in batches
    }
    for event_row, batch in event_rows:
        events_by_batch.setdefault(batch.id, []).append(event_row)
    for batch in batches:
        batch_events = sorted(
            events_by_batch.get(batch.id, ()), key=lambda row: str(row.id)
        )
        expected_hash = opening_control_batch_body_sha256(
            sequence=batch.sequence,
            events=[
                {
                    "event_sort_key": str(event_row.id),
                    "external_event_id": event_row.external_event_id,
                    "external_id": event_row.external_id,
                    "payload_sha256": event_row.payload_sha256,
                    "source_updated_at": (
                        _canonical_datetime(event_row.source_updated_at)
                        if event_row.source_updated_at is not None
                        else None
                    ),
                    "source_version": event_row.source_version,
                }
                for event_row in batch_events
            ],
        )
        if (
            batch.status != "applied"
            or batch.record_count != len(batch_events)
            or batch.body_sha256 != expected_hash
            or _persisted_timestamp_utc(batch.created_at) is None
            or _persisted_timestamp_utc(batch.created_at) > completed_at
            or _persisted_timestamp_utc(batch.received_at) is None
            or _persisted_timestamp_utc(batch.received_at) > completed_at
            or _persisted_timestamp_utc(batch.validated_at) is None
            or _persisted_timestamp_utc(batch.validated_at) > completed_at
        ):
            _invalid_opening_establishment()

    event_candidates: dict[str, list[tuple[SyncInboxEvent, SyncBatch]]] = defaultdict(
        list
    )
    for event_row, batch in event_rows:
        event_candidates[event_row.external_id].append((event_row, batch))
    seen_versions: set[uuid.UUID] = set()
    seen_business_keys: set[str] = set()
    for line in control_lines:
        if (
            line.external_object_version_id is None
            or line.external_object_version_id in seen_versions
            or line.external_business_key in seen_business_keys
        ):
            _invalid_opening_establishment()
        seen_versions.add(line.external_object_version_id)
        seen_business_keys.add(line.external_business_key)
        version = db.get(ExternalObjectVersion, line.external_object_version_id)
        external_object = (
            db.get(ExternalObject, version.external_object_id)
            if version is not None
            else None
        )
        candidates = event_candidates.get(line.external_business_key, [])
        payload = opening_control_projection_payload(
            external_business_key=line.external_business_key,
            region_org_id=task.region_org_id,
            material_id=line.material_id,
            condition_code=line.condition_code,
            control_qty=line.control_qty,
            mapping_status=line.mapping_status,
            mapping_note=line.mapping_note,
        )
        payload_hash = canonical_opening_manifest_sha256(payload)
        version_valid_from = (
            _persisted_timestamp_utc(version.valid_from)
            if version is not None
            else None
        )
        version_valid_to = (
            _persisted_timestamp_utc(version.valid_to)
            if version is not None
            else None
        )
        object_deleted_at = (
            _persisted_timestamp_utc(external_object.deleted_at)
            if external_object is not None
            else None
        )
        if (
            version is None
            or external_object is None
            or len(candidates) != 1
            or external_object.source_system_id != source.id
            or external_object.entity_type != OPENING_CONTROL_ENTITY_TYPE
            or external_object.external_id != line.external_business_key
            or _persisted_timestamp_utc(external_object.created_at) is None
            or _persisted_timestamp_utc(external_object.created_at) > control_at
            or (object_deleted_at is not None and object_deleted_at <= control_at)
            or version_valid_from is None
            or version_valid_from > control_at
            or (version_valid_to is not None and control_at >= version_valid_to)
            or version.payload_jsonb != payload
            or version.payload_sha256 != payload_hash
            or line.payload_sha256 != payload_hash
            or _persisted_timestamp_utc(version.created_at) is None
            or _persisted_timestamp_utc(version.created_at) > completed_at
            or _persisted_timestamp_utc(version.source_updated_at)
            != _persisted_timestamp_utc(line.source_updated_at)
        ):
            _invalid_opening_establishment()
        event_row, batch = candidates[0]
        if (
            batch.status != "applied"
            or event_row.source_system_id != source.id
            or event_row.status != "applied"
            or event_row.source_version != version.source_version
            or _persisted_timestamp_utc(event_row.source_updated_at)
            != _persisted_timestamp_utc(line.source_updated_at)
            or event_row.payload_jsonb != payload
            or event_row.payload_sha256 != payload_hash
            or event_row.error_code is not None
            or event_row.error_detail is not None
            or _persisted_timestamp_utc(event_row.created_at) is None
            or _persisted_timestamp_utc(event_row.created_at) > completed_at
            or _persisted_timestamp_utc(event_row.processed_at) is None
            or _persisted_timestamp_utc(event_row.processed_at) > completed_at
        ):
            _invalid_opening_establishment()


def _validate_opening_reviews(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    regional_review: StocktakeReview,
    headquarters_review: StocktakeReview,
    establishments: Sequence[InventoryOpeningEstablishment],
    scope_by_id: Mapping[uuid.UUID, FormalStocktakeScope],
    accounts: Mapping[uuid.UUID, StockAccount],
    count_lines: Sequence[StocktakeCountLine],
    control_lines: Sequence[StocktakeControlSnapshotLine],
    observations: Sequence[StocktakeCountObservation] = (),
) -> None:
    submitted_at = _persisted_timestamp_utc(round_row.submitted_at)
    regional_at = _persisted_timestamp_utc(regional_review.reviewed_at)
    headquarters_at = _persisted_timestamp_utc(headquarters_review.reviewed_at)
    regional_created_at = _persisted_timestamp_utc(regional_review.created_at)
    headquarters_created_at = _persisted_timestamp_utc(
        headquarters_review.created_at
    )
    posting_at = _persisted_timestamp_utc(task.posted_at)
    if (
        submitted_at is None
        or regional_at is None
        or headquarters_at is None
        or regional_created_at is None
        or headquarters_created_at is None
        or regional_created_at < submitted_at
        or headquarters_created_at < submitted_at
        or regional_created_at > regional_at
        or headquarters_created_at > headquarters_at
        or posting_at is None
        or not (submitted_at <= regional_at < headquarters_at <= posting_at)
        or regional_review.task_id != task.id
        or regional_review.round_id != round_row.id
        or regional_review.review_stage != "region"
        or regional_review.decision != "approve"
        or headquarters_review.task_id != task.id
        or headquarters_review.round_id != round_row.id
        or headquarters_review.review_stage != "headquarters"
        or headquarters_review.decision != "approve"
        or regional_review.id == headquarters_review.id
        or regional_review.reviewer_user_id
        == headquarters_review.reviewer_user_id
        or regional_review.reviewer_person_id
        == headquarters_review.reviewer_person_id
        or not _reviewer_role_was_authorized(
            db,
            review=regional_review,
            expected_role_code="provincial_manager",
            expected_scope_type="organization",
            expected_scope_id=str(task.region_org_id),
        )
        or not _reviewer_role_was_authorized(
            db,
            review=headquarters_review,
            expected_role_code="admin",
            expected_scope_type="national",
            expected_scope_id="*",
        )
    ):
        _invalid_opening_establishment()
    differences = db.scalars(
        select(StocktakeDifference)
        .where(
            StocktakeDifference.task_id == task.id,
            StocktakeDifference.round_id == round_row.id,
        )
        .order_by(StocktakeDifference.difference_no)
    ).all()
    if [row.difference_no for row in differences] != list(
        range(1, len(differences) + 1)
    ):
        _invalid_opening_establishment()
    _validate_opening_difference_set(
        count_lines=count_lines,
        accounts=accounts,
        control_lines=control_lines,
        differences=differences,
        observations=observations,
    )
    difference_by_id = {row.id: row for row in differences}
    control_line_ids = {row.id for row in control_lines}
    account_ids = set(accounts)
    for difference in differences:
        if (
            _persisted_timestamp_utc(difference.created_at) is None
            or _persisted_timestamp_utc(difference.created_at) < submitted_at
            or _persisted_timestamp_utc(difference.created_at) > regional_at
        ):
            _invalid_opening_establishment()
        if difference.difference_type == "control_unassigned":
            if difference.control_snapshot_line_id not in control_line_ids:
                _invalid_opening_establishment()
            continue
        scope = scope_by_id.get(difference.scope_id)
        bound_account_ids = {
            value
            for value in (
                difference.expected_account_id,
                difference.observed_account_id,
            )
            if value is not None
        }
        if difference.observed_line_id is not None:
            observation = next(
                (
                    row
                    for row in observations
                    if row.id == difference.observed_line_id
                ),
                None,
            )
            if (
                scope is None
                or observation is None
                or observation.verification_status != "verified"
                or round_row.round_no <= 1
                or round_row.round_type != "recount"
                or bound_account_ids
            ):
                _invalid_opening_establishment()
        elif (
            scope is None
            or not bound_account_ids
            or not bound_account_ids.issubset(account_ids)
            or any(
                accounts[account_id].owner_org_id != scope.owner_org_id
                or accounts[account_id].location_id != scope.location_id
                or accounts[account_id].material_id != difference.material_id
                for account_id in bound_account_ids
            )
        ):
            _invalid_opening_establishment()

    review_ids = (regional_review.id, headquarters_review.id)
    review_items = db.scalars(
        select(StocktakeReviewItem)
        .where(StocktakeReviewItem.review_id.in_(review_ids))
        .order_by(StocktakeReviewItem.review_id, StocktakeReviewItem.difference_id)
    ).all()
    decisions_by_review: dict[uuid.UUID, dict[uuid.UUID, str]] = {
        review_id: {} for review_id in review_ids
    }
    for item in review_items:
        if (
            item.task_id != task.id
            or item.round_id != round_row.id
            or item.difference_id not in difference_by_id
            or item.difference_id in decisions_by_review[item.review_id]
            or _persisted_timestamp_utc(item.created_at) is None
            or _persisted_timestamp_utc(item.created_at)
            < (
                _persisted_timestamp_utc(
                    difference_by_id[item.difference_id].created_at
                )
                or submitted_at
            )
            or _persisted_timestamp_utc(item.created_at)
            > (
                regional_at
                if item.review_id == regional_review.id
                else headquarters_at
            )
        ):
            _invalid_opening_establishment()
        decisions_by_review[item.review_id][item.difference_id] = item.decision
    expected_difference_ids = set(difference_by_id)
    if any(
        set(decisions) != expected_difference_ids
        for decisions in decisions_by_review.values()
    ):
        _invalid_opening_establishment()
    for difference in differences:
        regional_decision = decisions_by_review[regional_review.id][difference.id]
        headquarters_decision = decisions_by_review[headquarters_review.id][
            difference.id
        ]
        if regional_decision != headquarters_decision:
            _invalid_opening_establishment()
        if difference.difference_type == "control_unassigned":
            if regional_decision != "pending_verification":
                _invalid_opening_establishment()
        elif regional_decision != "accept_for_posting":
            _invalid_opening_establishment()

    control_difference_ids = {
        row.id
        for row in differences
        if row.difference_type == "control_unassigned"
    }
    has_pending = bool(control_difference_ids)
    if any(
        row.has_pending_control_difference is not has_pending
        for row in establishments
    ):
        _invalid_opening_establishment()
    try:
        regional_manifest = canonical_opening_decision_manifest_sha256(
            task_id=task.id,
            round_id=round_row.id,
            differences=differences,
            decisions=decisions_by_review[regional_review.id],
        )
        headquarters_manifest = canonical_opening_decision_manifest_sha256(
            task_id=task.id,
            round_id=round_row.id,
            differences=differences,
            decisions=decisions_by_review[headquarters_review.id],
        )
    except (TypeError, ValueError):
        _invalid_opening_establishment()
    if (
        regional_review.decision_manifest_sha256 != regional_manifest
        or headquarters_review.decision_manifest_sha256
        != headquarters_manifest
        or regional_manifest != headquarters_manifest
    ):
        _invalid_opening_establishment()


def _validate_opening_difference_set(
    *,
    count_lines: Sequence[StocktakeCountLine],
    accounts: Mapping[uuid.UUID, StockAccount],
    control_lines: Sequence[StocktakeControlSnapshotLine],
    differences: Sequence[StocktakeDifference],
    observations: Sequence[StocktakeCountObservation] = (),
) -> None:
    """Recompute every reviewable opening difference without allocation guesses.

    The formal ledger is empty at the opening cutoff, so every positive physical
    count is one exact ``excess`` fact for its counted account.  OAM is a regional
    control total only: resolved rows reconcile by material and condition, while
    unresolved or unequal totals stay ``control_unassigned`` and can never be
    posted.  If multiple OAM rows share one resolved dimension and the aggregate
    is unequal, this schema cannot prove which immutable source row owns the
    residual; fail closed instead of distributing it arbitrarily.
    """

    physical_by_dimension: dict[tuple[uuid.UUID, str], Decimal] = defaultdict(
        lambda: _ZERO
    )
    expected_excess_by_account: dict[uuid.UUID, StocktakeCountLine] = {}
    for count_line in count_lines:
        account = accounts.get(count_line.stock_account_id)
        if account is None:
            _invalid_opening_establishment()
        physical_by_dimension[(account.material_id, account.condition_code)] += (
            count_line.counted_qty
        )
        if count_line.counted_qty > _ZERO:
            expected_excess_by_account[account.id] = count_line
    expected_excess_by_observation: dict[
        uuid.UUID, StocktakeCountObservation
    ] = {}
    for observation in observations:
        if observation.material_id is not None:
            physical_by_dimension[
                (observation.material_id, observation.condition_code)
            ] += observation.counted_qty
        if observation.counted_qty > _ZERO:
            expected_excess_by_observation[observation.id] = observation

    resolved_by_dimension: dict[
        tuple[uuid.UUID, str], list[StocktakeControlSnapshotLine]
    ] = defaultdict(list)
    expected_control: dict[
        uuid.UUID,
        tuple[Decimal, Decimal, Decimal, Decimal, uuid.UUID | None],
    ] = {}
    for control_line in control_lines:
        if (
            not control_line.control_qty.is_finite()
            or control_line.control_qty < _ZERO
        ):
            _invalid_opening_establishment()
        if control_line.mapping_status == "resolved":
            if (
                control_line.material_id is None
                or control_line.condition_code
                not in {"new", "used", "damaged", "scrapped"}
                or control_line.mapping_note != ""
            ):
                _invalid_opening_establishment()
            resolved_by_dimension[
                (control_line.material_id, control_line.condition_code)
            ].append(control_line)
        elif control_line.mapping_status == "unresolved":
            if (
                control_line.material_id is not None
                or control_line.condition_code is not None
                or not control_line.mapping_note.strip()
            ):
                _invalid_opening_establishment()
            if control_line.control_qty > _ZERO:
                expected_control[control_line.id] = (
                    control_line.control_qty,
                    _ZERO,
                    -control_line.control_qty,
                    control_line.control_qty,
                    None,
                )
        else:
            _invalid_opening_establishment()

    for dimension, dimension_lines in resolved_by_dimension.items():
        control_total = sum(
            (line.control_qty for line in dimension_lines), start=_ZERO
        )
        physical_total = physical_by_dimension.get(dimension, _ZERO)
        difference_qty = physical_total - control_total
        if difference_qty == _ZERO:
            continue
        if len(dimension_lines) != 1:
            _invalid_opening_establishment()
        control_line = dimension_lines[0]
        expected_control[control_line.id] = (
            control_total,
            physical_total,
            difference_qty,
            abs(difference_qty),
            dimension[0],
        )

    actual_excess_by_account: dict[uuid.UUID, StocktakeDifference] = {}
    actual_excess_by_observation: dict[uuid.UUID, StocktakeDifference] = {}
    actual_control_by_line: dict[uuid.UUID, StocktakeDifference] = {}
    for difference in differences:
        if difference.difference_type == "excess":
            account_id = difference.observed_account_id
            observation_id = difference.observed_line_id
            if account_id is not None and observation_id is None:
                if account_id in actual_excess_by_account:
                    _invalid_opening_establishment()
                actual_excess_by_account[account_id] = difference
            elif account_id is None and observation_id is not None:
                if observation_id in actual_excess_by_observation:
                    _invalid_opening_establishment()
                actual_excess_by_observation[observation_id] = difference
            else:
                _invalid_opening_establishment()
        elif difference.difference_type == "control_unassigned":
            control_line_id = difference.control_snapshot_line_id
            if (
                control_line_id is None
                or control_line_id in actual_control_by_line
            ):
                _invalid_opening_establishment()
            actual_control_by_line[control_line_id] = difference
        else:
            _invalid_opening_establishment()

    if (
        set(actual_excess_by_account) != set(expected_excess_by_account)
        or set(actual_excess_by_observation)
        != set(expected_excess_by_observation)
        or set(actual_control_by_line) != set(expected_control)
    ):
        _invalid_opening_establishment()

    for account_id, count_line in expected_excess_by_account.items():
        account = accounts[account_id]
        difference = actual_excess_by_account[account_id]
        if (
            difference.scope_id != count_line.scope_id
            or difference.control_snapshot_line_id is not None
            or difference.material_id != account.material_id
            or difference.expected_account_id is not None
            or difference.serial_id is not None
            or difference.book_qty != _ZERO
            or difference.counted_qty != count_line.counted_qty
            or difference.difference_qty != count_line.counted_qty
            or difference.affected_qty != count_line.counted_qty
            or difference.evidence_required is not True
        ):
            _invalid_opening_establishment()

    for observation_id, observation in expected_excess_by_observation.items():
        difference = actual_excess_by_observation[observation_id]
        expected_reason = (
            "opening_pending_verification"
            if observation.verification_status == "pending_verification"
            else "opening_unexpected_dimension"
        )
        if (
            difference.scope_id != observation.scope_id
            or difference.control_snapshot_line_id is not None
            or difference.material_id != observation.material_id
            or difference.expected_account_id is not None
            or difference.observed_account_id is not None
            or difference.observed_line_id != observation.id
            or difference.serial_id != observation.serial_id
            or difference.book_qty != _ZERO
            or difference.counted_qty != observation.counted_qty
            or difference.difference_qty != observation.counted_qty
            or difference.affected_qty != observation.counted_qty
            or difference.reason_code != expected_reason
            or difference.evidence_required is not True
        ):
            _invalid_opening_establishment()

    for control_line_id, expected in expected_control.items():
        difference = actual_control_by_line[control_line_id]
        book_qty, counted_qty, difference_qty, affected_qty, material_id = expected
        if (
            difference.scope_id is not None
            or difference.material_id != material_id
            or difference.expected_account_id is not None
            or difference.observed_account_id is not None
            or difference.serial_id is not None
            or difference.book_qty != book_qty
            or difference.counted_qty != counted_qty
            or difference.difference_qty != difference_qty
            or difference.affected_qty != affected_qty
            or difference.evidence_required is not True
            or not difference.reason_text.strip()
        ):
            _invalid_opening_establishment()
def _reviewer_role_was_authorized(
    db: Session,
    *,
    review: StocktakeReview,
    expected_role_code: str,
    expected_scope_type: str,
    expected_scope_id: str,
) -> bool:
    """Validate the assignment at review time, not mutable current org state."""
    assignment = db.get(RoleAssignment, review.reviewer_role_assignment_id)
    if assignment is None:
        return False
    role = db.get(Role, assignment.role_id)
    user = db.get(User, review.reviewer_user_id)
    person = db.get(Person, review.reviewer_person_id)
    reviewed_at = _persisted_timestamp_utc(review.reviewed_at)
    valid_from = _persisted_timestamp_utc(assignment.valid_from)
    valid_to = _persisted_timestamp_utc(assignment.valid_to)
    revoked_at = _persisted_timestamp_utc(assignment.revoked_at)
    return bool(
        reviewed_at is not None
        and valid_from is not None
        and valid_from <= reviewed_at
        and (valid_to is None or reviewed_at < valid_to)
        and (revoked_at is None or reviewed_at < revoked_at)
        and assignment.status in {"active", "expired", "revoked"}
        and assignment.user_id == review.reviewer_user_id
        and assignment.scope_type == expected_scope_type
        and assignment.scope_id == expected_scope_id
        and role is not None
        and role.code == expected_role_code
        and role.is_external is False
        and user is not None
        and user.person_id == review.reviewer_person_id
        and user.authorization_version >= review.authorization_version
        and person is not None
    )


def _user_had_headquarters_admin_role(
    db: Session,
    *,
    user_id: str,
    occurred_at: datetime | None,
) -> bool:
    if occurred_at is None:
        return False
    user = db.get(User, user_id)
    person = db.get(Person, user.person_id) if user and user.person_id else None
    if (
        user is None
        or person is None
    ):
        return False
    assignments = db.scalars(
        select(RoleAssignment).where(RoleAssignment.user_id == user_id)
    ).all()
    for assignment in assignments:
        role = db.get(Role, assignment.role_id)
        valid_from = _persisted_timestamp_utc(assignment.valid_from)
        valid_to = _persisted_timestamp_utc(assignment.valid_to)
        revoked_at = _persisted_timestamp_utc(assignment.revoked_at)
        if (
            role is not None
            and role.code == "admin"
            and role.is_external is False
            and assignment.scope_type == "national"
            and assignment.scope_id == "*"
            and assignment.status in {"active", "expired", "revoked"}
            and valid_from is not None
            and valid_from <= occurred_at
            and (valid_to is None or occurred_at < valid_to)
            and (revoked_at is None or occurred_at < revoked_at)
        ):
            return True
    return False


def _validate_opening_posting(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    posting: StocktakePosting,
    establishments: Sequence[InventoryOpeningEstablishment],
    scopes: Sequence[FormalStocktakeScope],
    count_lines: Sequence[StocktakeCountLine],
    count_serials_by_line: Mapping[uuid.UUID, set[uuid.UUID]],
    accounts: Mapping[uuid.UUID, StockAccount],
    observations: Sequence[StocktakeCountObservation] = (),
    observation_accounts: Mapping[uuid.UUID, StockAccount] | None = None,
) -> None:
    observation_account_map = dict(observation_accounts or {})
    posting_at = _persisted_timestamp_utc(posting.posted_at)
    posting_created_at = _persisted_timestamp_utc(posting.created_at)
    task_posted_at = _persisted_timestamp_utc(task.posted_at)
    establishment_times = [
        _persisted_timestamp_utc(row.established_at) for row in establishments
    ]
    if (
        posting.task_id != task.id
        or posting.round_id != round_row.id
        or posting.posting_kind != "opening"
        or posting_at is None
        or posting_created_at is None
        or posting_created_at > posting_at
        or task_posted_at != posting_at
        or any(value is None or value < posting_at for value in establishment_times)
        or any(
            row.established_by_user_id != posting.posted_by_user_id
            for row in establishments
        )
        or not _user_had_headquarters_admin_role(
            db,
            user_id=posting.posted_by_user_id,
            occurred_at=posting_at,
        )
        or any(
            not _user_had_headquarters_admin_role(
                db,
                user_id=row.established_by_user_id,
                occurred_at=_persisted_timestamp_utc(row.established_at),
            )
            for row in establishments
        )
    ):
        _invalid_opening_establishment()
    posting_items = db.scalars(
        select(StocktakePostingItem)
        .where(StocktakePostingItem.posting_id == posting.id)
        .order_by(StocktakePostingItem.inventory_movement_id)
    ).all()
    if any(
        item.task_id != task.id
        or item.round_id != round_row.id
        or ((item.count_line_id is None) == (item.difference_id is None))
        or _persisted_timestamp_utc(item.created_at) is None
        or _persisted_timestamp_utc(item.created_at) > posting_at
        for item in posting_items
    ):
        _invalid_opening_establishment()

    positive_lines = [row for row in count_lines if row.counted_qty > _ZERO]
    observation_differences = {
        row.observed_line_id: row
        for row in db.scalars(
            select(StocktakeDifference).where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.round_id == round_row.id,
                StocktakeDifference.observed_line_id.is_not(None),
            )
        ).all()
        if row.observed_line_id is not None
    }
    if (
        set(observation_differences) != {row.id for row in observations}
        or set(observation_account_map) != {row.id for row in observations}
    ):
        _invalid_opening_establishment()
    counted_total = sum(
        (row.counted_qty for row in positive_lines),
        start=_ZERO,
    ) + sum((row.counted_qty for row in observations), start=_ZERO)
    if posting.total_quantity != counted_total:
        _invalid_opening_establishment()
    task_transactions = db.scalars(
        select(InventoryTransaction)
        .where(
            InventoryTransaction.source_document_type == "opening_stocktake",
            InventoryTransaction.source_document_id == str(task.id),
        )
        .order_by(InventoryTransaction.ledger_cursor, InventoryTransaction.id)
    ).all()
    if counted_total == _ZERO:
        if (
            any(row.counted_qty != _ZERO for row in count_lines)
            or observations
            or posting.inventory_transaction_id is not None
            or posting_items
            or task_transactions
            or any(
                row.established_ledger_cursor != task.cutoff_ledger_cursor
                for row in establishments
            )
        ):
            _invalid_opening_establishment()
        return

    transaction = db.get(InventoryTransaction, posting.inventory_transaction_id)
    cutoff_at = _persisted_timestamp_utc(task.cutoff_at)
    if (
        transaction is None
        or len(task_transactions) != 1
        or task_transactions[0].id != transaction.id
        or transaction.status != "posted"
        or transaction.movement_type != "opening"
        or transaction.source_document_type != "opening_stocktake"
        or transaction.source_document_id != str(task.id)
        or transaction.actor_user_id != posting.posted_by_user_id
        or _persisted_timestamp_utc(transaction.effective_at) != cutoff_at
        or _persisted_timestamp_utc(transaction.posted_at) != posting_at
        or _persisted_timestamp_utc(transaction.created_at) is None
        or _persisted_timestamp_utc(transaction.created_at) > posting_at
        or transaction.ledger_cursor <= (task.cutoff_ledger_cursor or 0)
        or any(
            row.established_ledger_cursor != transaction.ledger_cursor
            for row in establishments
        )
    ):
        _invalid_opening_establishment()
    assert transaction is not None
    movements = db.scalars(
        select(InventoryMovement)
        .where(InventoryMovement.transaction_id == transaction.id)
        .order_by(InventoryMovement.line_no)
    ).all()
    movement_by_id = {row.id: row for row in movements}
    item_by_movement = {row.inventory_movement_id: row for row in posting_items}
    observations_by_id = {row.id: row for row in observations}
    expected_sources: dict[
        tuple[str, uuid.UUID],
        tuple[uuid.UUID, Decimal, set[uuid.UUID]],
    ] = {
        ("count", row.id): (
            row.stock_account_id,
            row.counted_qty,
            set(count_serials_by_line.get(row.id, set())),
        )
        for row in positive_lines
    }
    for observation_id, observation in observations_by_id.items():
        difference = observation_differences[observation_id]
        account = observation_account_map[observation_id]
        expected_sources[("difference", difference.id)] = (
            account.id,
            observation.counted_qty,
            {observation.serial_id} if observation.serial_id is not None else set(),
        )
    actual_sources: dict[tuple[str, uuid.UUID], StocktakePostingItem] = {}
    for item in posting_items:
        source_key = (
            ("count", item.count_line_id)
            if item.count_line_id is not None
            else ("difference", item.difference_id)
        )
        if source_key[1] is None or source_key in actual_sources:
            _invalid_opening_establishment()
        actual_sources[(source_key[0], source_key[1])] = item
    scope_pairs = {(row.owner_org_id, row.location_id) for row in scopes}
    if (
        len(movement_by_id) != len(movements)
        or [row.line_no for row in movements]
        != list(range(1, len(movements) + 1))
        or len(item_by_movement) != len(posting_items)
        or set(item_by_movement) != set(movement_by_id)
        or set(actual_sources) != set(expected_sources)
        or len(movements) != len(expected_sources)
    ):
        _invalid_opening_establishment()
    for movement_id, movement in movement_by_id.items():
        item = item_by_movement[movement_id]
        source_key = (
            ("count", item.count_line_id)
            if item.count_line_id is not None
            else ("difference", item.difference_id)
        )
        if source_key[1] is None:
            _invalid_opening_establishment()
        expected_account_id, expected_quantity, _expected_serials = (
            expected_sources[(source_key[0], source_key[1])]
        )
        account = accounts.get(movement.to_account_id)
        if (
            account is None
            or movement.from_account_id is not None
            or movement.to_account_id != expected_account_id
            or movement.external_boundary_code is None
            or _persisted_timestamp_utc(movement.created_at) is None
            or _persisted_timestamp_utc(movement.created_at) > posting_at
            or (account.owner_org_id, account.location_id) not in scope_pairs
            or movement.quantity != expected_quantity
            or item.quantity != expected_quantity
        ):
            _invalid_opening_establishment()

    observation_account_ids = {
        row.id for row in observation_account_map.values()
    }
    if observation_account_ids:
        prior_movement = db.scalar(
            select(InventoryMovement.id)
            .join(
                InventoryTransaction,
                InventoryTransaction.id == InventoryMovement.transaction_id,
            )
            .where(
                InventoryTransaction.ledger_cursor < transaction.ledger_cursor,
                or_(
                    InventoryMovement.from_account_id.in_(observation_account_ids),
                    InventoryMovement.to_account_id.in_(observation_account_ids),
                ),
            )
            .limit(1)
        )
        if prior_movement is not None:
            _invalid_opening_establishment()

    movement_serial_rows = db.scalars(
        select(InventoryMovementSerial)
        .where(InventoryMovementSerial.transaction_id == transaction.id)
        .order_by(
            InventoryMovementSerial.movement_id,
            InventoryMovementSerial.serial_id,
        )
    ).all()
    movement_serials: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for row in movement_serial_rows:
        if (
            row.movement_id not in movement_by_id
            or _persisted_timestamp_utc(row.created_at) is None
            or _persisted_timestamp_utc(row.created_at) > posting_at
        ):
            _invalid_opening_establishment()
        movement_serials[row.movement_id].add(row.serial_id)
    for movement_id, item in item_by_movement.items():
        source_key = (
            ("count", item.count_line_id)
            if item.count_line_id is not None
            else ("difference", item.difference_id)
        )
        if source_key[1] is None:
            _invalid_opening_establishment()
        expected_serials = expected_sources[(source_key[0], source_key[1])][2]
        if movement_serials.get(movement_id, set()) != expected_serials:
            _invalid_opening_establishment()
    if (
        sum((row.quantity for row in movements), start=_ZERO)
        != posting.total_quantity
        or sum((row.quantity for row in posting_items), start=_ZERO)
        != posting.total_quantity
    ):
        _invalid_opening_establishment()


def _persisted_timestamp_utc(value: object) -> datetime | None:
    """Normalize a persisted timestamptz, including SQLite's naive UTC form."""

    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _require_no_active_hard_freezes(
    db: Session,
    accounts: dict[uuid.UUID, StockAccount],
    *,
    effective_at: datetime,
) -> None:
    effective_at_utc = _require_aware_datetime("effective_at", effective_at)
    account_predicates = tuple(
        and_(
            FormalStocktakeScope.owner_org_id == account.owner_org_id,
            FormalStocktakeScope.location_id == account.location_id,
            or_(
                FormalStocktakeScope.scope_mode == "location_all",
                and_(
                    FormalStocktakeScope.scope_mode == "filtered",
                    or_(
                        FormalStocktakeScope.material_id.is_(None),
                        FormalStocktakeScope.material_id == account.material_id,
                    ),
                    or_(
                        FormalStocktakeScope.condition_code.is_(None),
                        FormalStocktakeScope.condition_code
                        == account.condition_code,
                    ),
                    or_(
                        FormalStocktakeScope.availability_bucket.is_(None),
                        FormalStocktakeScope.availability_bucket
                        == account.availability_bucket,
                    ),
                ),
            ),
        )
        for account in accounts.values()
    )
    matching_freeze_id = db.scalar(
        select(InventoryFreeze.id)
        .join(
            FormalStocktakeScope,
            and_(
                FormalStocktakeScope.id == InventoryFreeze.stocktake_scope_id,
                FormalStocktakeScope.task_id == InventoryFreeze.task_id,
            ),
        )
        .where(
            InventoryFreeze.freeze_mode == "hard",
            InventoryFreeze.status.in_(("active", "released", "cancelled")),
            InventoryFreeze.valid_from <= effective_at_utc,
            or_(
                InventoryFreeze.valid_to.is_(None),
                InventoryFreeze.valid_to > effective_at_utc,
            ),
            or_(*account_predicates),
        )
        .limit(1)
    )
    if matching_freeze_id is not None:
        _fail(
            "inventory_scope_hard_frozen",
            "conflict",
            "一个或多个库存账户处于盘点硬冻结范围，禁止新增库存事实",
        )


def _require_owned_stocktake_freeze_scopes(
    db: Session,
    *,
    task_id: uuid.UUID,
    accounts: Mapping[uuid.UUID, StockAccount],
    effective_at: datetime,
) -> None:
    """Allow only the exact active freezes owned by the posting task.

    Generic inventory posting rejects every hard freeze.  Difference posting
    is the one operation that must consume its own freeze while still rejecting
    an overlapping freeze owned by another task.  The global ledger lock held
    by the caller prevents the active-freeze graph from changing between this
    proof and task completion.
    """

    effective_at_utc = _require_aware_datetime("effective_at", effective_at)
    task = db.get(FormalStocktakeTask, task_id, populate_existing=True)
    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task_id)
            .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id == task_id)
            .order_by(InventoryFreeze.stocktake_scope_id, InventoryFreeze.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    scope_by_id = {scope.id: scope for scope in scopes}
    freeze_by_scope: dict[uuid.UUID, InventoryFreeze] = {}
    for freeze in freezes:
        if freeze.stocktake_scope_id in freeze_by_scope:
            _fail(
                "inventory_stocktake_freeze_graph_invalid",
                "service_unavailable",
                "盘点任务的范围冻结事实重复",
            )
        freeze_by_scope[freeze.stocktake_scope_id] = freeze
    if (
        task is None
        or task.task_type not in {"full", "sample", "ad_hoc", "personal", "termination"}
        or task.status != "approved"
        or not scopes
        or [scope.scope_no for scope in scopes]
        != list(range(1, len(scopes) + 1))
        or len(scope_by_id) != len(scopes)
        or set(freeze_by_scope) != set(scope_by_id)
        or any(
            freeze.status != "active"
            or freeze.valid_to is not None
            or freeze.freeze_mode not in {"hard", "cutoff_replay"}
            or freeze.scope_key != scope_by_id[scope_id].scope_key
            or _persisted_timestamp_utc(freeze.valid_from) is None
            or _persisted_timestamp_utc(freeze.valid_from) > effective_at_utc
            for scope_id, freeze in freeze_by_scope.items()
        )
    ):
        _fail(
            "inventory_stocktake_freeze_graph_invalid",
            "precondition_failed",
            "盘点差异过账要求每个任务范围仍有唯一活动冻结",
        )

    for account in accounts.values():
        matching = tuple(
            scope for scope in scopes if _stocktake_scope_matches_account(scope, account)
        )
        if len(matching) != 1:
            _fail(
                "inventory_stocktake_account_scope_invalid",
                "precondition_failed",
                "盘点差异账户必须且只能属于本任务一个冻结范围",
            )

    # A zero-movement/no-adjustment completion still has to prove and later
    # release the task's complete freeze graph, but has no foreign account
    # coordinates to test.  Avoid constructing an empty SQL OR expression.
    if not accounts:
        return

    account_predicates = tuple(
        and_(
            FormalStocktakeScope.owner_org_id == account.owner_org_id,
            FormalStocktakeScope.location_id == account.location_id,
            or_(
                FormalStocktakeScope.scope_mode == "location_all",
                and_(
                    FormalStocktakeScope.scope_mode == "filtered",
                    or_(
                        FormalStocktakeScope.material_id.is_(None),
                        FormalStocktakeScope.material_id == account.material_id,
                    ),
                    or_(
                        FormalStocktakeScope.condition_code.is_(None),
                        FormalStocktakeScope.condition_code == account.condition_code,
                    ),
                    or_(
                        FormalStocktakeScope.availability_bucket.is_(None),
                        FormalStocktakeScope.availability_bucket
                        == account.availability_bucket,
                    ),
                ),
            ),
        )
        for account in accounts.values()
    )
    foreign_hard_freeze = db.scalar(
        select(InventoryFreeze.id)
        .join(
            FormalStocktakeScope,
            and_(
                FormalStocktakeScope.id == InventoryFreeze.stocktake_scope_id,
                FormalStocktakeScope.task_id == InventoryFreeze.task_id,
            ),
        )
        .where(
            InventoryFreeze.task_id != task_id,
            InventoryFreeze.freeze_mode == "hard",
            InventoryFreeze.status == "active",
            InventoryFreeze.valid_from <= effective_at_utc,
            or_(
                InventoryFreeze.valid_to.is_(None),
                InventoryFreeze.valid_to > effective_at_utc,
            ),
            or_(*account_predicates),
        )
        .limit(1)
    )
    if foreign_hard_freeze is not None:
        _fail(
            "inventory_scope_hard_frozen",
            "conflict",
            "盘点差异账户同时命中其他任务的活动硬冻结",
        )


def _stocktake_scope_matches_account(
    scope: FormalStocktakeScope,
    account: StockAccount,
) -> bool:
    if (
        scope.owner_org_id != account.owner_org_id
        or scope.location_id != account.location_id
    ):
        return False
    if scope.scope_mode == "location_all":
        return True
    return bool(
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


def _invalid_opening_establishment() -> None:
    _fail(
        "inventory_opening_establishment_invalid",
        "service_unavailable",
        "库存期初建立证据不完整或相互矛盾，已停止过账",
    )


def _load_effective_policies(
    db: Session, material_ids: set[uuid.UUID], effective_at: datetime
) -> dict[uuid.UUID, MaterialInventoryPolicy]:
    policies: dict[uuid.UUID, MaterialInventoryPolicy] = {}
    for material_id in sorted(material_ids, key=str):
        statement = _select_only_reference_statement(
            db,
            select(MaterialInventoryPolicy)
            .where(
                MaterialInventoryPolicy.material_id == material_id,
                MaterialInventoryPolicy.effective_from <= effective_at,
                or_(
                    MaterialInventoryPolicy.effective_to.is_(None),
                    MaterialInventoryPolicy.effective_to > effective_at,
                ),
            )
            .order_by(
                MaterialInventoryPolicy.material_id,
                MaterialInventoryPolicy.effective_from,
                MaterialInventoryPolicy.id,
            ),
        )
        rows = db.scalars(statement).all()
        if len(rows) != 1 or rows[0].tracking_mode not in TRACKING_MODES:
            _fail(
                "inventory_policy_not_unique",
                "precondition_failed",
                "过账时点必须且只能命中一条有效库存策略",
            )
        policies[material_id] = rows[0]
    return policies


def _validate_tracking_rules(
    command: InventoryPostingCommand,
    accounts: dict[uuid.UUID, StockAccount],
    policies: dict[uuid.UUID, MaterialInventoryPolicy],
) -> None:
    for movement in command.movements:
        endpoint_accounts = [
            accounts[account_id]
            for account_id in (movement.from_account_id, movement.to_account_id)
            if account_id is not None
        ]
        material_ids = {account.material_id for account in endpoint_accounts}
        if len(material_ids) != 1:
            _fail(
                "movement_material_mismatch",
                "invalid_request",
                "库存明细两端账户必须属于同一物料",
            )
        material_id = next(iter(material_ids))
        policy = policies[material_id]
        scale = _decimal_scale(movement.quantity)
        if scale > policy.quantity_scale:
            _fail(
                "quantity_scale_exceeded",
                "invalid_request",
                "库存数量精度超过物料策略",
            )
        if not policy.allow_fraction and movement.quantity != movement.quantity.to_integral_value():
            _fail(
                "fraction_not_allowed",
                "invalid_request",
                "该物料不允许小数库存",
            )

        lot_ids = {account.lot_id for account in endpoint_accounts}
        if policy.tracking_mode in {"none", "serial"}:
            if lot_ids != {None}:
                _fail(
                    "lot_not_allowed",
                    "invalid_request",
                    "该物料策略不允许库存账户绑定批次",
                )
        else:
            if None in lot_ids or len(lot_ids) != 1:
                _fail(
                    "lot_required_or_mismatch",
                    "invalid_request",
                    "批次追踪物料必须在同一批次账户间流转",
                )

        serial_tracking = policy.tracking_mode in {"serial", "lot_and_serial"}
        if not serial_tracking and movement.serial_ids:
            _fail(
                "serial_not_allowed",
                "invalid_request",
                "该物料策略不允许 SN 明细",
            )
        if serial_tracking:
            if movement.quantity != movement.quantity.to_integral_value():
                _fail(
                    "serial_quantity_must_be_integer",
                    "invalid_request",
                    "SN 追踪物料数量必须为整数",
                )
            if len(movement.serial_ids) != int(movement.quantity):
                _fail(
                    "serial_quantity_mismatch",
                    "invalid_request",
                    "SN 数量必须与库存数量一致",
                )


def _lock_or_create_balances(
    db: Session, account_ids: tuple[uuid.UUID, ...]
) -> dict[uuid.UUID, StockBalance]:
    rows = db.scalars(
        select(StockBalance)
        .where(StockBalance.stock_account_id.in_(account_ids))
        .order_by(StockBalance.stock_account_id)
        .with_for_update()
    ).all()
    balances = {row.stock_account_id: row for row in rows}
    missing = [account_id for account_id in account_ids if account_id not in balances]
    for account_id in missing:
        balance = StockBalance(
            stock_account_id=account_id,
            quantity=_ZERO,
            ledger_cursor=0,
            version=0,
        )
        db.add(balance)
        balances[account_id] = balance
    if missing:
        # Account rows are already locked, so first-row creation is serialized
        # on PostgreSQL.  This is not a SQLite concurrency claim.
        db.flush()
    return balances


def _lock_existing_prelocked_balances(
    db: Session,
    *,
    account_ids: tuple[uuid.UUID, ...],
    proof: _PrelockedInventoryGraphProof,
    command: InventoryPostingCommand,
) -> dict[uuid.UUID, StockBalance]:
    """Re-enter only balance rows already created/locked by finalize."""

    checked = _require_prelocked_inventory_graph_proof(
        db,
        command=command,
        proof=proof,
    )
    if not set(account_ids).issubset(checked.balance_account_ids):
        _fail(
            "inventory_prelocked_balance_coordinates_invalid",
            "precondition_failed",
            "库存命令账户超出期初预锁余额图",
        )
    rows = tuple(
        db.scalars(
            select(StockBalance)
            .where(StockBalance.stock_account_id.in_(account_ids))
            .order_by(StockBalance.stock_account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    balances = {row.stock_account_id: row for row in rows}
    if len(balances) != len(account_ids) or set(balances) != set(account_ids):
        _fail(
            "inventory_prelocked_balance_graph_changed",
            "precondition_failed",
            "期初预锁余额图在过账前发生扩展或收缩",
        )
    return balances


def _validate_locked_balance_projections(
    db: Session,
    *,
    account_ids: tuple[uuid.UUID, ...],
    balances: Mapping[uuid.UUID, StockBalance],
) -> None:
    expected_quantities: dict[uuid.UUID, Decimal] = {
        account_id: _ZERO for account_id in account_ids
    }
    expected_cursors: dict[uuid.UUID, int] = {
        account_id: 0 for account_id in account_ids
    }
    touched_transactions: dict[uuid.UUID, set[uuid.UUID]] = {
        account_id: set() for account_id in account_ids
    }
    rows = db.execute(
        select(InventoryMovement, InventoryTransaction)
        .join(
            InventoryTransaction,
            InventoryTransaction.id == InventoryMovement.transaction_id,
        )
        .where(
            InventoryTransaction.status == "posted",
            or_(
                InventoryMovement.from_account_id.in_(account_ids),
                InventoryMovement.to_account_id.in_(account_ids),
            ),
        )
        .order_by(
            InventoryTransaction.ledger_cursor,
            InventoryMovement.line_no,
            InventoryMovement.id,
        )
    ).all()
    for movement, transaction in rows:
        for account_id, sign in (
            (movement.from_account_id, -1),
            (movement.to_account_id, 1),
        ):
            if account_id not in expected_quantities:
                continue
            expected_quantities[account_id] += sign * movement.quantity
            expected_cursors[account_id] = max(
                expected_cursors[account_id], transaction.ledger_cursor
            )
            touched_transactions[account_id].add(transaction.id)

    for account_id in account_ids:
        balance = balances.get(account_id)
        if (
            balance is None
            or balance.quantity != expected_quantities[account_id]
            or balance.ledger_cursor != expected_cursors[account_id]
            or balance.version != len(touched_transactions[account_id])
        ):
            _fail(
                "inventory_balance_projection_drift",
                "service_unavailable",
                "库存余额投影与不可变流水不一致，禁止继续过账",
            )


def _aggregate_deltas(
    command: InventoryPostingCommand,
) -> dict[uuid.UUID, Decimal]:
    deltas: dict[uuid.UUID, Decimal] = defaultdict(lambda: _ZERO)
    for movement in command.movements:
        if movement.from_account_id is not None:
            deltas[movement.from_account_id] -= movement.quantity
        if movement.to_account_id is not None:
            deltas[movement.to_account_id] += movement.quantity
    return deltas


def _lock_and_validate_serials(
    db: Session,
    *,
    command: InventoryPostingCommand,
    accounts: dict[uuid.UUID, StockAccount],
    policies: dict[uuid.UUID, MaterialInventoryPolicy],
    prelocked_reference_graph: _PrelockedInventoryGraphProof | None = None,
) -> tuple[
    dict[uuid.UUID, InventorySerial], dict[uuid.UUID, SerialCurrentPosition]
]:
    serial_ids = tuple(
        sorted(
            {
                serial_id
                for movement in command.movements
                for serial_id in movement.serial_ids
            },
            key=str,
        )
    )
    if not serial_ids:
        return {}, {}
    if prelocked_reference_graph is None:
        lock_inventory_serial_graph(db, serial_ids)
    else:
        proof = _require_prelocked_inventory_graph_proof(
            db,
            command=command,
            proof=prelocked_reference_graph,
        )
        if not set(serial_ids).issubset(proof.serial_ids):
            _fail(
                "inventory_prelocked_serial_coordinates_invalid",
                "precondition_failed",
                "库存命令 SN 超出期初预锁引用图",
            )
    serial_statement = _select_only_reference_statement(
        db,
        select(InventorySerial)
        .where(InventorySerial.id.in_(serial_ids))
        .order_by(InventorySerial.id),
    )
    serial_rows = db.scalars(serial_statement).all()
    serials = {row.id: row for row in serial_rows}
    if len(serials) != len(serial_ids):
        _fail("inventory_serial_not_found", "not_found", "一个或多个 SN 不存在")
    position_statement = (
        select(SerialCurrentPosition)
        .where(SerialCurrentPosition.serial_id.in_(serial_ids))
        .order_by(SerialCurrentPosition.serial_id)
    )
    if _uses_direct_reference_row_locks(db):
        position_statement = position_statement.with_for_update()
    position_rows = db.scalars(position_statement).all()
    positions = {row.serial_id: row for row in position_rows}
    _validate_locked_serial_projections(
        db,
        serial_ids=serial_ids,
        positions=positions,
    )

    for movement in command.movements:
        if not movement.serial_ids:
            continue
        endpoint_id = movement.from_account_id or movement.to_account_id
        assert endpoint_id is not None
        account = accounts[endpoint_id]
        policy = policies[account.material_id]
        for serial_id in movement.serial_ids:
            serial = serials[serial_id]
            if serial.lifecycle_status != "active":
                _fail(
                    "serial_lifecycle_inactive",
                    "precondition_failed",
                    "只有 active 生命周期的 SN 可参与库存过账",
                )
            if command.movement_type in {"consume", "scrap"}:
                _fail(
                    "serial_lifecycle_projection_unavailable",
                    "precondition_failed",
                    "当前账本尚不能安全记录 SN 消耗或报废生命周期，禁止该过账",
                )
            if serial.material_id != account.material_id:
                _fail(
                    "serial_material_mismatch",
                    "invalid_request",
                    "SN 与库存账户物料不一致",
                )
            if policy.tracking_mode == "serial" and serial.lot_id is not None:
                _fail(
                    "serial_lot_not_allowed",
                    "invalid_request",
                    "纯 SN 追踪物料不能绑定批次",
                )
            if (
                policy.tracking_mode == "lot_and_serial"
                and serial.lot_id != account.lot_id
            ):
                _fail(
                    "serial_lot_mismatch",
                    "invalid_request",
                    "SN 批次与库存账户批次不一致",
                )
            position = positions.get(serial_id)
            if movement.from_account_id is None:
                if position is not None and position.stock_account_id is not None:
                    _fail(
                        "serial_already_managed",
                        "conflict",
                        "外部入库 SN 已位于受管库存账户",
                    )
            elif position is None or position.stock_account_id != movement.from_account_id:
                _fail(
                    "serial_position_mismatch",
                    "conflict",
                    "SN 当前受管位置与来源账户不一致",
                )
    return serials, positions


def _validate_locked_serial_projections(
    db: Session,
    *,
    serial_ids: tuple[uuid.UUID, ...],
    positions: Mapping[uuid.UUID, SerialCurrentPosition],
) -> None:
    """Re-prove each locked SN position from its latest posted movement."""

    rows = db.execute(
        select(
            InventoryMovementSerial.serial_id,
            InventoryMovement,
            InventoryTransaction,
        )
        .join(
            InventoryMovement,
            InventoryMovement.id == InventoryMovementSerial.movement_id,
        )
        .join(
            InventoryTransaction,
            InventoryTransaction.id == InventoryMovement.transaction_id,
        )
        .where(
            InventoryMovementSerial.serial_id.in_(serial_ids),
            InventoryTransaction.status == "posted",
        )
        .order_by(
            InventoryMovementSerial.serial_id,
            InventoryTransaction.ledger_cursor,
            InventoryMovement.line_no,
            InventoryMovement.id,
        )
    ).all()
    latest: dict[uuid.UUID, InventoryMovement] = {}
    for serial_id, movement, _transaction in rows:
        latest[serial_id] = movement

    for serial_id in serial_ids:
        movement = latest.get(serial_id)
        position = positions.get(serial_id)
        if movement is None:
            valid = position is None
        else:
            valid = bool(
                position is not None
                and position.last_movement_id == movement.id
                and position.stock_account_id == movement.to_account_id
            )
        if not valid:
            _fail(
                "inventory_serial_projection_drift",
                "service_unavailable",
                "SN 当前位置投影与最后不可变流水不一致，禁止继续过账",
            )


def _command_account_ids(command: InventoryPostingCommand) -> tuple[uuid.UUID, ...]:
    return tuple(
        sorted(
            {
                account_id
                for movement in command.movements
                for account_id in (
                    movement.from_account_id,
                    movement.to_account_id,
                )
                if account_id is not None
            },
            key=str,
        )
    )


def _command_serial_ids(command: InventoryPostingCommand) -> tuple[uuid.UUID, ...]:
    return tuple(
        sorted(
            {
                serial_id
                for movement in command.movements
                for serial_id in movement.serial_ids
            },
            key=str,
        )
    )


def _transaction_account_ids(
    db: Session, transaction_id: uuid.UUID
) -> tuple[uuid.UUID, ...]:
    rows = db.execute(
        select(InventoryMovement.from_account_id, InventoryMovement.to_account_id)
        .where(InventoryMovement.transaction_id == transaction_id)
        .order_by(InventoryMovement.line_no)
    ).all()
    if not rows:
        _fail(
            "idempotency_record_invalid",
            "service_unavailable",
            "库存幂等记录缺少不可变明细",
        )
    return tuple(
        sorted(
            {
                account_id
                for from_account_id, to_account_id in rows
                for account_id in (from_account_id, to_account_id)
                if account_id is not None
            },
            key=str,
        )
    )


def _posting_request_hash(
    actor: FormalPrincipal, command: InventoryPostingCommand
) -> str:
    return _canonical_hash(
        {
            "operation": "post",
            "actor": _canonical_actor(actor),
            "command": _posting_document(command),
        }
    )


def _reversal_request_hash(
    actor: FormalPrincipal, command: InventoryReversalCommand
) -> str:
    return _canonical_hash(
        {
            "operation": "reverse",
            "actor": _canonical_actor(actor),
            "command": {
                "effective_at": _canonical_timestamp(command.effective_at),
                "original_transaction_id": str(command.original_transaction_id),
                "posting_key": command.posting_key,
                "source_document_id": command.source_document_id,
                "source_document_type": command.source_document_type,
                "transaction_no": command.transaction_no,
            },
        }
    )


def _canonical_actor(actor: FormalPrincipal) -> dict[str, object]:
    return {
        "authorization_version": actor.authorization_version,
        "person_id": str(actor.person_id),
        "user_id": actor.user_id,
    }


def _posting_document(command: InventoryPostingCommand) -> dict[str, object]:
    return {
        "effective_at": _canonical_timestamp(command.effective_at),
        "movement_type": command.movement_type,
        "movements": [
            {
                "external_boundary_code": movement.external_boundary_code,
                "from_account_id": (
                    str(movement.from_account_id)
                    if movement.from_account_id is not None
                    else None
                ),
                "quantity": _canonical_decimal(movement.quantity),
                "serial_ids": [str(serial_id) for serial_id in movement.serial_ids],
                "to_account_id": (
                    str(movement.to_account_id)
                    if movement.to_account_id is not None
                    else None
                ),
            }
            for movement in command.movements
        ],
        "posting_key": command.posting_key,
        "source_document_id": command.source_document_id,
        "source_document_type": command.source_document_type,
        "transaction_no": command.transaction_no,
    }


def _canonical_hash(document: dict[str, object]) -> str:
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _storage_hash(raw_key: str) -> str:
    return hashlib.sha256(
        f"cloud_oam.inventory.idempotency.v1\0{raw_key}".encode("utf-8")
    ).hexdigest()


def _request_reference(raw_request_id: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.inventory.request.v1\0{raw_request_id}".encode("utf-8")
    ).hexdigest()
    return f"inventory-request-{digest}"


def _derived_evidence_key(kind: str, transaction_id: uuid.UUID, suffix: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.inventory.{kind}.v1\0{transaction_id}\0{suffix}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"inventory-{kind}-{digest}"


def _advisory_coordinate(namespace: str, value: str) -> int:
    digest = hashlib.sha256(
        f"cloud_oam.inventory.lock.v1\0{namespace}\0{value}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _take_advisory_locks(db: Session, coordinates: tuple[int, ...]) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for coordinate in sorted(set(coordinates)):
        db.execute(
            text("SELECT pg_advisory_xact_lock(:lock_key)"),
            {"lock_key": coordinate},
        )


def _uses_direct_reference_row_locks(db: Session) -> bool:
    """Keep legacy local semantics without exceeding the PostgreSQL API ACL."""

    return db.get_bind().dialect.name != "postgresql"


def _select_only_reference_statement(db: Session, statement):
    """Lock locally, but rely on the PostgreSQL owner helper in production."""

    if _uses_direct_reference_row_locks(db):
        return statement.with_for_update()
    return statement


def _require_idempotency_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 200
        or _PRINTABLE.fullmatch(value) is None
    ):
        _fail(
            "idempotency_key_invalid",
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
            "request_id_invalid",
            "invalid_request",
            "请求标识必须为 8 至 160 位可打印 ASCII 字符",
        )
    return value


def _require_text(field: str, value: object, limit: int, *, safe: bool) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value) > limit
        or (safe and _SAFE_REFERENCE.fullmatch(value) is None)
    ):
        _fail(
            f"{field}_invalid",
            "invalid_request",
            f"{field} 格式无效",
        )
    return value


def _require_uuid(field: str, value: object) -> uuid.UUID:
    if isinstance(value, uuid.UUID):
        checked = value
    elif isinstance(value, str):
        try:
            checked = uuid.UUID(value)
        except ValueError:
            _fail(f"{field}_invalid", "invalid_request", f"{field} 必须为 UUID")
    else:
        _fail(f"{field}_invalid", "invalid_request", f"{field} 必须为 UUID")
    if checked.int == 0:
        _fail(f"{field}_invalid", "invalid_request", f"{field} 不能为零 UUID")
    return checked


def _require_aware_datetime(field: str, value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        _fail(
            f"{field}_invalid",
            "invalid_request",
            f"{field} 必须包含时区",
        )
    return value.astimezone(timezone.utc)


def _require_quantity(value: object) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        _fail("quantity_invalid", "invalid_request", "库存数量必须为有限 Decimal")
    if value <= 0 or value >= _MAX_QUANTITY or _decimal_scale(value) > 3:
        _fail(
            "quantity_invalid",
            "invalid_request",
            "库存数量必须大于零、最多三位小数且不超出字段范围",
        )
    return value


def _decimal_scale(value: Decimal) -> int:
    normalized = value.normalize()
    return max(0, -normalized.as_tuple().exponent)


def _canonical_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _canonical_timestamp(value: datetime) -> str:
    return (
        value.astimezone(timezone.utc)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _fail(
    code: str,
    category: str,
    message: str,
    *,
    cause: Exception | None = None,
) -> None:
    error = InventoryPostingError(code, category, message)
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "INVENTORY_LEDGER_HEAD_ID",
    "INVENTORY_STREAM_KEY",
    "InventoryMovementCommand",
    "InventoryPostingCommand",
    "InventoryPostingError",
    "InventoryPostingResult",
    "InventoryReversalCommand",
    "canonical_opening_account_dimension_sha256",
    "canonical_opening_control_manifest_sha256",
    "canonical_opening_count_manifest_sha256",
    "canonical_opening_decision_manifest_sha256",
    "canonical_opening_scope_line_sha256",
    "canonical_opening_scope_manifest_sha256",
    "canonical_opening_serial_snapshot_sha256",
    "canonical_opening_snapshot_manifest_sha256",
    "post_inventory_transaction",
    "reverse_inventory_transaction",
    "validate_opening_task_evidence_for_replay",
]
