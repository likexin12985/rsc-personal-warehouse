"""Atomic opening-stocktake posting and independent close boundaries.

The module is an internal domain service.  It never calls OAM, RSC, Workflow,
Feishu or another external system, and it never commits or rolls back the
caller's transaction.  A caller receiving any exception must roll the whole
transaction back.

``approved -> posted`` and ``posted -> closed`` are intentionally two separate
commands, state transitions, outbox facts and hash-chained audit facts.  The
posting command re-proves the complete book/physical/SN/control/review graph,
creates at most one immutable opening transaction, establishes every declared
owner/location scope and releases all task freezes in the same transaction.
OAM control differences remain pending evidence and are never converted into
ledger movements.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
import re
from typing import Final, Mapping, NoReturn, Sequence
import uuid

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    ScopeGrant,
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
from ..models import User
from ..inventory_models import (
    CustodyAssignment,
    FormalMaterial,
    InventoryLedgerHead,
    InventoryLot,
    InventoryMovement,
    InventoryMovementSerial,
    InventorySerial,
    InventoryTransaction,
    MaterialInventoryPolicy,
    QrCode,
    SerialCurrentPosition,
    StockAccount,
    StockBalance,
    StockLocation,
)
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
    StocktakeObservationDisposition,
    StocktakePosting,
    StocktakePostingItem,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeReviewItem,
    StocktakeRound,
    StocktakeSnapshotLine,
)
from . import inventory_posting as posting_service
from . import opening_control_reconciliation as reconciliation_service
from .audit_chain import (
    AuditChainError,
    _lock_audit_chain_head_with_proof,
    _require_prelocked_audit_stream_proof,
    _verify_audit_event_with_prelocked_proof,
    append_audit_event,
)
from .postgresql_lock_graph import (
    lock_inventory_reference_graph,
    lock_inventory_serial_graph,
    lock_opening_stocktake_start_reference,
    lock_opening_stocktake_task_evidence,
    lock_opening_terminal_reference_union,
)


INVENTORY_STREAM_KEY: Final[str] = "inventory"
_ZERO: Final[Decimal] = Decimal("0.000")
_MAX_QUANTITY: Final[Decimal] = Decimal("1000000000000000")
_PRINTABLE = re.compile(r"^[\x21-\x7e]+$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$", re.ASCII)
_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class OpeningStocktakeFinalizeError(RuntimeError):
    """Stable, database-detail-free failure for post/close callers."""

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
class PostOpeningStocktakeCommand:
    task_id: uuid.UUID
    expected_version: int


@dataclass(frozen=True, slots=True)
class CloseOpeningStocktakeCommand:
    task_id: uuid.UUID
    expected_version: int


@dataclass(frozen=True, slots=True)
class OpeningStocktakePostResult:
    task_id: uuid.UUID
    round_id: uuid.UUID
    posting_id: uuid.UUID
    inventory_transaction_id: uuid.UUID | None
    resulting_task_status: str
    task_version: int
    total_quantity: Decimal
    established_scope_count: int
    pending_control_difference_count: int
    ledger_cursor: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class OpeningStocktakeCloseResult:
    task_id: uuid.UUID
    posting_id: uuid.UUID
    inventory_transaction_id: uuid.UUID | None
    resulting_task_status: str
    task_version: int
    closed_at: datetime
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _ApprovedEvidence:
    task: FormalStocktakeTask
    round_row: StocktakeRound
    scopes: tuple[FormalStocktakeScope, ...]
    freezes: tuple[InventoryFreeze, ...]
    count_lines: tuple[StocktakeCountLine, ...]
    count_serials_by_line: Mapping[uuid.UUID, set[uuid.UUID]]
    accounts: Mapping[uuid.UUID, StockAccount]
    observations: tuple[StocktakeCountObservation, ...]
    observation_differences: Mapping[uuid.UUID, StocktakeDifference]
    observation_accounts: Mapping[uuid.UUID, StockAccount]
    disposition_resolutions: Mapping[uuid.UUID, object]
    regional_review: StocktakeReview
    headquarters_review: StocktakeReview
    pending_control_difference_count: int
    audit_replay_plan: object | None = None


@dataclass(frozen=True, slots=True)
class _DispositionResolutionCandidate:
    """Immutable coordinates captured before the shared reference graph locks."""

    observation_id: uuid.UUID
    observation_signature: tuple[object, ...]
    disposition_id: uuid.UUID
    disposition_signature: tuple[object, ...]
    command: object
    material_ids: tuple[uuid.UUID, ...]
    material_mapping_signature: tuple[tuple[uuid.UUID, uuid.UUID], ...]
    lot_signature: tuple[tuple[uuid.UUID, uuid.UUID], ...]
    policy_signature: tuple[object, ...] | None
    cutoff_account_ids: tuple[uuid.UUID, ...]
    snapshot_account_ids: tuple[uuid.UUID, ...]
    serial_signatures: tuple[
        tuple[
            uuid.UUID,
            tuple[uuid.UUID, ...],
            tuple[tuple[uuid.UUID, uuid.UUID], ...],
        ],
        ...,
    ]


@dataclass(frozen=True, slots=True)
class _OpeningReferencePlan:
    """Exact, fail-closed owner coordinates frozen before the first start lock."""

    require_complete_dispositions: bool
    scope_pairs: tuple[tuple[uuid.UUID, uuid.UUID], ...]
    material_ids: tuple[uuid.UUID, ...]
    account_ids: tuple[uuid.UUID, ...]
    serial_ids: tuple[uuid.UUID, ...]
    observation_signatures: tuple[tuple[object, ...], ...]
    disposition_candidates: tuple[_DispositionResolutionCandidate, ...]
    master_signatures: tuple[
        tuple[str, tuple[tuple[object, ...], ...]], ...
    ]


@dataclass(frozen=True, slots=True)
class _PostingSource:
    account_id: uuid.UUID
    quantity: Decimal
    serial_ids: tuple[uuid.UUID, ...]
    count_line_id: uuid.UUID | None
    difference_id: uuid.UUID | None


_OPENING_TASK_ROOT_SEAL: Final[object] = object()
_OPENING_TASK_GRAPH_SEAL: Final[object] = object()
_OPENING_TASK_BATCH_ROOT_SEAL: Final[object] = object()
_OPENING_TASK_BATCH_PLAN_SEAL: Final[object] = object()
_OPENING_SERIAL_UNION_SEAL: Final[object] = object()
_OPENING_TASK_BATCH_GRAPH_SEAL: Final[object] = object()


@dataclass(frozen=True, slots=True)
class _LockedOpeningTaskRoot:
    """Transaction-bound proof that ledger then task rows are already held."""

    session: Session
    transaction: object
    task: FormalStocktakeTask
    current_ledger_cursor: int
    seal: object


@dataclass(frozen=True, slots=True)
class _LockedOpeningTaskBatchRoot:
    """Ledger-first, UUID-ordered terminal task roots for batch replay."""

    session: Session
    transaction: object
    tasks: tuple[FormalStocktakeTask, ...]
    current_ledger_cursor: int
    seal: object


@dataclass(frozen=True, slots=True)
class _PrelockedOpeningTerminalTaskGraphProof:
    """Opaque proof for one complete terminal opening graph in this txn."""

    session: Session
    transaction: object
    root: _LockedOpeningTaskRoot
    principal_graph: posting_service._PrelockedOpeningPrincipalGraphProof
    round_plans: tuple[tuple[uuid.UUID, _OpeningReferencePlan], ...]
    disposition_resolutions_by_round: Mapping[
        uuid.UUID, Mapping[uuid.UUID, object]
    ]
    audit_replay_plan: object
    account_ids: tuple[uuid.UUID, ...]
    serial_ids: tuple[uuid.UUID, ...]
    balance_signatures: tuple[tuple[object, ...], ...]
    posting_id: uuid.UUID
    seal: object


@dataclass(frozen=True, slots=True)
class _PrelockedOpeningTerminalTaskBatchPlan:
    """Task-evidence/master-signature phase, deliberately before serials."""

    session: Session
    transaction: object
    root: _LockedOpeningTaskBatchRoot
    principal_graph: posting_service._PrelockedOpeningPrincipalGraphProof
    rounds_by_task: tuple[tuple[uuid.UUID, tuple[StocktakeRound, ...]], ...]
    plans_by_task: tuple[
        tuple[
            uuid.UUID,
            tuple[tuple[uuid.UUID, _OpeningReferencePlan], ...],
        ],
        ...,
    ]
    account_ids: tuple[uuid.UUID, ...]
    serial_ids: tuple[uuid.UUID, ...]
    seal: object


@dataclass(frozen=True, slots=True)
class _PrelockedOpeningSerialUnionProof:
    """Transaction-bound proof of one sorted serial/position owner call."""

    session: Session
    transaction: object
    serial_ids: tuple[uuid.UUID, ...]
    seal: object


@dataclass(frozen=True, slots=True)
class _PrelockedOpeningTerminalTaskBatchGraphProof:
    """No-audit batch plan for query/reconciliation terminal replay."""

    session: Session
    transaction: object
    root: _LockedOpeningTaskBatchRoot
    principal_graph: posting_service._PrelockedOpeningPrincipalGraphProof
    serial_graph: _PrelockedOpeningSerialUnionProof
    task_graphs: tuple[_PrelockedOpeningTerminalTaskGraphProof, ...]
    seal: object


def post_approved_opening_stocktake(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: PostOpeningStocktakeCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakePostResult:
    """Atomically establish one approved opening task without committing."""

    try:
        return _post_approved_opening_stocktake(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except OpeningStocktakeFinalizeError:
        raise
    except posting_service.InventoryPostingError as exc:
        _fail(
            "opening_finalize_inventory_evidence_invalid",
            exc.category,
            "期初盘点账、物、SN 或复核证据无法通过正式库存校验",
            cause=exc,
        )
    except AuditChainError as exc:
        _fail(
            "opening_finalize_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，期初任务未完成",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "opening_finalize_concurrent_conflict",
            "conflict",
            "期初任务发生并发冲突，请回滚并重新读取",
            cause=exc,
        )
    except DBAPIError as exc:
        _fail(
            "opening_finalize_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了期初过账，请回滚并重新读取",
            cause=exc,
        )
    raise AssertionError("unreachable opening finalize boundary")


def close_posted_opening_stocktake(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: CloseOpeningStocktakeCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeCloseResult:
    """Close one fully re-proved posted task without changing inventory."""

    try:
        return _close_posted_opening_stocktake(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except OpeningStocktakeFinalizeError:
        raise
    except posting_service.InventoryPostingError as exc:
        _fail(
            "opening_close_posting_evidence_invalid",
            exc.category,
            "期初过账证据无法重证，任务未关闭",
            cause=exc,
        )
    except AuditChainError as exc:
        _fail(
            "opening_close_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，期初任务未关闭",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "opening_close_concurrent_conflict",
            "conflict",
            "期初关闭发生并发冲突，请回滚并重新读取",
            cause=exc,
        )
    except DBAPIError as exc:
        _fail(
            "opening_close_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了期初关闭，请回滚并重新读取",
            cause=exc,
        )
    raise AssertionError("unreachable opening close boundary")


def validate_opening_finalize_evidence_for_replay(
    db: Session,
    *,
    task_id: uuid.UUID,
    require_closed: bool = False,
) -> None:
    """Read-only exact re-proof of posting and optional close side effects."""

    checked_task_id = _require_uuid("task_id", task_id)
    batch_root = _lock_opening_terminal_task_batch_root(
        db,
        task_ids=(checked_task_id,),
    )
    task = batch_root.tasks[0]
    if require_closed and task.status != "closed":
        _replay_invalid("期初任务尚未独立关闭")
    reconciliation_task_ids = (task.id,) if task.status == "closed" else ()
    reconciliation_user_ids = (
        reconciliation_service._opening_control_reconciliation_historical_user_ids(
            db,
            task_ids=reconciliation_task_ids,
        )
        if reconciliation_task_ids
        else ()
    )
    batch_graph = _lock_opening_terminal_task_batch_graph(
        db,
        root=batch_root,
        supplied_user_ids=reconciliation_user_ids,
    )
    reconciliation_batch = None
    if reconciliation_task_ids:
        try:
            reconciliation_batch = reconciliation_service._lock_opening_control_reconciliation_batch_graph(
                db,
                task_ids=reconciliation_task_ids,
                principal_graph=batch_graph.principal_graph,
            )
        except reconciliation_service.OpeningControlReconciliationError as exc:
            _map_control_reconciliation_error(exc)
    _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    try:
        _validate_opening_terminal_batch_from_prelocked_graph(
            db,
            proof=batch_graph,
            audit_proof=audit_proof,
            require_closed_task_ids=(task.id,) if task.status == "closed" else (),
        )
    except OpeningStocktakeFinalizeError as exc:
        # Preserve this established compatibility API's inventory-evidence
        # error contract while internal terminal callers retain the mapped
        # finalizer error category.
        if isinstance(exc.__cause__, posting_service.InventoryPostingError):
            raise exc.__cause__
        raise
    if reconciliation_batch is not None:
        try:
            statuses = reconciliation_service._opening_control_reconciliation_statuses_from_prelocked_batch(
                db,
                proof=reconciliation_batch,
                audit_proof=audit_proof,
            )
        except reconciliation_service.OpeningControlReconciliationError as exc:
            _map_control_reconciliation_error(exc)
        if set(statuses) != {task.id} or statuses[task.id].status not in {
            "not_required",
            "approved",
        }:
            _fail(
                "opening_close_reconciliation_pending",
                "precondition_failed",
                "期初任务仍有待核实外部控制差异，完成独立对账前禁止关闭",
            )


def validate_opening_terminal_effects_for_replay(
    db: Session,
    *,
    task_id: uuid.UUID,
    require_closed: bool = False,
) -> None:
    """Canonical standalone replay for one terminal opening task UUID.

    The public boundary never accepts caller-owned ORM objects or an asserted
    prelock state.  It delegates to the complete ledger-first finalizer replay,
    which plans the task/principal/evidence/reference/serial/balance graph and
    locks the audit head at the one canonical final phase.
    """

    validate_opening_finalize_evidence_for_replay(
        db,
        task_id=_require_uuid("task_id", task_id),
        require_closed=require_closed,
    )


def _validate_opening_terminal_effects_from_prelocked_generic_graph(
    db: Session,
    *,
    graph: object,
    task: FormalStocktakeTask,
    posting: StocktakePosting,
    audit_proof: object,
) -> None:
    """Internal post-audit proof for generic inventory posting only.

    Both the generic terminal-opening graph and the audit stream proof are
    unforgeable, session/transaction-bound values issued before this point.
    No caller can enter this boundary by passing ORM rows and claiming an
    unknown prelock state.
    """

    try:
        checked_graph = (
            posting_service._require_prelocked_opening_terminal_task_graph(
                db,
                graph,
            )
        )
        _require_prelocked_audit_stream_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
        )
    except (posting_service.InventoryPostingError, AuditChainError) as exc:
        _replay_invalid("期初终态预锁图或审计证明无效", cause=exc)
    if (
        not isinstance(task, FormalStocktakeTask)
        or not isinstance(posting, StocktakePosting)
        or task.id != checked_graph.task_id
        or checked_graph.task.id != task.id
        or posting.task_id != task.id
        or posting.posting_kind != "opening"
    ):
        _replay_invalid("期初终态过账主体不一致")
    _validate_post_side_effects(
        db,
        task=task,
        posting=posting,
        audit_proof=audit_proof,
    )
    if posting.inventory_transaction_id is not None:
        transaction = db.get(
            InventoryTransaction, posting.inventory_transaction_id
        )
        if transaction is None:
            _replay_invalid("期初不可变库存交易缺失")
        _validate_inventory_transaction_side_effects(
            db,
            transaction=transaction,
            audit_proof=audit_proof,
        )
    _validate_opening_inventory_projection(db, task=task, posting=posting)


def _lock_opening_terminal_task_root(
    db: Session,
    *,
    task_id: uuid.UUID,
) -> _LockedOpeningTaskRoot:
    """Lock the inventory ledger first and then one opening task.

    Callers may take principal/RBAC rows after this root and before expanding
    the task graph.  This two-stage private boundary makes the global order
    explicit for close and reconciliation writers.
    """

    checked_task_id = _require_uuid("task_id", task_id)
    head = _lock_ledger_head(db)
    task = _lock_task(db, checked_task_id)
    transaction = db.get_transaction()
    if transaction is None:
        _fail(
            "opening_terminal_root_transaction_required",
            "precondition_failed",
            "期初终态根锁必须绑定活动事务",
        )
    return _LockedOpeningTaskRoot(
        session=db,
        transaction=transaction,
        task=task,
        current_ledger_cursor=head.next_cursor - 1,
        seal=_OPENING_TASK_ROOT_SEAL,
    )


def _require_opening_terminal_task_root(
    db: Session,
    proof: object,
) -> _LockedOpeningTaskRoot:
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _LockedOpeningTaskRoot)
        or proof.seal is not _OPENING_TASK_ROOT_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
    ):
        _fail(
            "opening_terminal_root_proof_invalid",
            "precondition_failed",
            "期初终态根锁证明无效或不属于当前事务",
        )
    return proof


def _lock_opening_terminal_task_batch_root(
    db: Session,
    *,
    task_ids: Sequence[uuid.UUID],
) -> _LockedOpeningTaskBatchRoot:
    """Lock ledger then every terminal task row once in UUID order."""

    checked_task_ids = tuple(sorted(set(task_ids), key=str))
    if not checked_task_ids or any(
        not isinstance(task_id, uuid.UUID) or task_id.int == 0
        for task_id in checked_task_ids
    ):
        _fail(
            "opening_terminal_batch_task_coordinates_invalid",
            "precondition_failed",
            "期初终态批量重证缺少有效任务坐标",
        )
    head = _lock_ledger_head(db)
    tasks = tuple(
        db.scalars(
            select(FormalStocktakeTask)
            .where(FormalStocktakeTask.id.in_(checked_task_ids))
            .order_by(FormalStocktakeTask.id)
            .with_for_update(of=FormalStocktakeTask)
            .execution_options(populate_existing=True)
        ).all()
    )
    if (
        tuple(row.id for row in tasks) != checked_task_ids
        or any(
            row.task_type != "opening" or row.status not in {"posted", "closed"}
            for row in tasks
        )
    ):
        _replay_invalid("期初终态批量任务坐标缺失或状态无效")
    transaction = db.get_transaction()
    if transaction is None:
        _fail(
            "opening_terminal_batch_root_transaction_required",
            "precondition_failed",
            "期初终态批量根锁必须绑定活动事务",
        )
    return _LockedOpeningTaskBatchRoot(
        session=db,
        transaction=transaction,
        tasks=tasks,
        current_ledger_cursor=head.next_cursor - 1,
        seal=_OPENING_TASK_BATCH_ROOT_SEAL,
    )


def _require_opening_terminal_task_batch_root(
    db: Session,
    proof: object,
) -> _LockedOpeningTaskBatchRoot:
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _LockedOpeningTaskBatchRoot)
        or proof.seal is not _OPENING_TASK_BATCH_ROOT_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
    ):
        _fail(
            "opening_terminal_batch_root_proof_invalid",
            "precondition_failed",
            "期初终态批量根锁证明无效或不属于当前事务",
        )
    return proof


def _lock_opening_terminal_task_batch_graph(
    db: Session,
    *,
    root: _LockedOpeningTaskBatchRoot,
    supplied_user_ids: Sequence[str] = (),
    principal_graph: (
        posting_service._PrelockedOpeningPrincipalGraphProof | None
    ) = None,
) -> _PrelockedOpeningTerminalTaskBatchGraphProof:
    """Plan and lock a terminal-task batch without taking the audit head.

    The fixed phase order is all task rows -> one principal union -> all
    task-local evidence -> one exact 0028 reference-master union -> one serial
    union -> mutable balance union.  Reconciliation/run graphs may be locked
    after this function and before the caller takes the one final audit head.
    """

    plan = _plan_opening_terminal_task_batch_graph(
        db,
        root=root,
        supplied_user_ids=supplied_user_ids,
        principal_graph=principal_graph,
    )
    serial_graph = _lock_opening_serial_union_graph(
        db,
        serial_ids=plan.serial_ids,
    )
    return _seal_opening_terminal_task_batch_graph(
        db,
        plan=plan,
        serial_graph=serial_graph,
    )


def _plan_opening_terminal_task_batch_graph(
    db: Session,
    *,
    root: _LockedOpeningTaskBatchRoot,
    supplied_user_ids: Sequence[str] = (),
    principal_graph: (
        posting_service._PrelockedOpeningPrincipalGraphProof | None
    ) = None,
) -> _PrelockedOpeningTerminalTaskBatchPlan:
    """Lock task-local evidence and freeze terminal serial/account coordinates.

    The function deliberately stops before the serial owner helper.  A mixed
    opening read can therefore combine ``plan.serial_ids`` with non-terminal
    task coordinates, acquire one global serial/position union, and seal the
    terminal subgraph without re-entering that helper.
    """

    checked_root = _require_opening_terminal_task_batch_root(db, root)
    task_ids = tuple(row.id for row in checked_root.tasks)
    checked_principal_graph = (
        posting_service._lock_opening_task_principal_graph(
            db,
            task_ids=task_ids,
            supplied_user_ids=supplied_user_ids,
        )
        if principal_graph is None
        else posting_service._require_opening_task_principal_graph_proof(
            db,
            principal_graph,
            required_task_ids=task_ids,
        )
    )

    rounds_by_task: dict[uuid.UUID, tuple[StocktakeRound, ...]] = {}
    for task in checked_root.tasks:
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
            _replay_invalid("期初终态批量任务轮次图缺失或不连续")
        rounds_by_task[task.id] = rounds
    for task in checked_root.tasks:
        lock_opening_stocktake_task_evidence(
            db,
            task.id,
            rounds_by_task[task.id][-1].id,
        )

    plans_by_task: dict[
        uuid.UUID, tuple[tuple[uuid.UUID, _OpeningReferencePlan], ...]
    ] = {}
    for task in checked_root.tasks:
        plans = tuple(
            (
                round_row.id,
                _opening_start_reference_coordinates(
                    db,
                    task=task,
                    round_id=round_row.id,
                ),
            )
            for round_row in rounds_by_task[task.id]
        )
        first_plan = plans[0][1]
        if any(plan.scope_pairs != first_plan.scope_pairs for _, plan in plans):
            _replay_invalid("期初终态批量任务跨轮范围不一致")
        plans_by_task[task.id] = plans

    all_scope_pairs = tuple(
        sorted(
            {
                scope_pair
                for plans in plans_by_task.values()
                for _round_id, plan in plans
                for scope_pair in plan.scope_pairs
            },
            key=lambda pair: (str(pair[0]), str(pair[1])),
        )
    )
    all_material_ids = tuple(
        sorted(
            {
                material_id
                for plans in plans_by_task.values()
                for _round_id, plan in plans
                for material_id in plan.material_ids
            },
            key=str,
        )
    )
    all_serial_ids = tuple(
        sorted(
            {
                serial_id
                for plans in plans_by_task.values()
                for _round_id, plan in plans
                for serial_id in plan.serial_ids
            },
            key=str,
        )
    )
    all_account_ids = tuple(
        sorted(
            {
                account_id
                for plans in plans_by_task.values()
                for _round_id, plan in plans
                for account_id in plan.account_ids
            },
            key=str,
        )
    )
    lock_opening_terminal_reference_union(
        db,
        task_ids,
        tuple(owner_id for owner_id, _location_id in all_scope_pairs),
        tuple(location_id for _owner_id, location_id in all_scope_pairs),
        all_material_ids,
        all_account_ids,
    )
    for task in checked_root.tasks:
        for _round_id, reference_plan in plans_by_task[task.id]:
            if _capture_opening_reference_master_signatures(
                db,
                task=task,
                scope_pairs=reference_plan.scope_pairs,
                material_ids=reference_plan.material_ids,
                account_ids=reference_plan.account_ids,
                serial_ids=reference_plan.serial_ids,
            ) != reference_plan.master_signatures:
                _replay_invalid(
                    "期初终态批量引用主数据在并集锁定前后发生扩展或收缩"
                )
    transaction = db.get_transaction()
    if transaction is None:
        _fail(
            "opening_terminal_batch_plan_transaction_required",
            "precondition_failed",
            "期初终态批量计划必须绑定活动事务",
        )
    return _PrelockedOpeningTerminalTaskBatchPlan(
        session=db,
        transaction=transaction,
        root=checked_root,
        principal_graph=checked_principal_graph,
        rounds_by_task=tuple(rounds_by_task.items()),
        plans_by_task=tuple(plans_by_task.items()),
        account_ids=all_account_ids,
        serial_ids=all_serial_ids,
        seal=_OPENING_TASK_BATCH_PLAN_SEAL,
    )


def _require_opening_terminal_task_batch_plan(
    db: Session,
    proof: object,
) -> _PrelockedOpeningTerminalTaskBatchPlan:
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _PrelockedOpeningTerminalTaskBatchPlan)
        or proof.seal is not _OPENING_TASK_BATCH_PLAN_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
    ):
        _fail(
            "opening_terminal_batch_plan_proof_invalid",
            "precondition_failed",
            "期初终态批量计划证明无效或不属于当前事务",
        )
    root = _require_opening_terminal_task_batch_root(db, proof.root)
    posting_service._require_opening_task_principal_graph_proof(
        db,
        proof.principal_graph,
        required_task_ids=tuple(row.id for row in root.tasks),
    )
    if tuple(task_id for task_id, _rounds in proof.rounds_by_task) != tuple(
        row.id for row in root.tasks
    ) or tuple(task_id for task_id, _plans in proof.plans_by_task) != tuple(
        row.id for row in root.tasks
    ):
        _fail(
            "opening_terminal_batch_plan_coordinates_invalid",
            "precondition_failed",
            "期初终态批量计划任务坐标不完整",
        )
    return proof


def _lock_opening_serial_union_graph(
    db: Session,
    *,
    serial_ids: Sequence[uuid.UUID],
) -> _PrelockedOpeningSerialUnionProof:
    """Acquire one sorted serial -> current-position owner graph."""

    checked_serial_ids = tuple(sorted(set(serial_ids), key=str))
    if any(
        not isinstance(serial_id, uuid.UUID) or serial_id.int == 0
        for serial_id in checked_serial_ids
    ):
        _fail(
            "opening_serial_union_coordinates_invalid",
            "precondition_failed",
            "期初批量 SN 坐标无效",
        )
    lock_inventory_serial_graph(db, checked_serial_ids)
    transaction = db.get_transaction()
    if transaction is None:
        _fail(
            "opening_serial_union_transaction_required",
            "precondition_failed",
            "期初批量 SN 图必须绑定活动事务",
        )
    return _PrelockedOpeningSerialUnionProof(
        session=db,
        transaction=transaction,
        serial_ids=checked_serial_ids,
        seal=_OPENING_SERIAL_UNION_SEAL,
    )


def _require_opening_serial_union_graph_proof(
    db: Session,
    proof: object,
    *,
    required_serial_ids: Sequence[uuid.UUID] = (),
) -> _PrelockedOpeningSerialUnionProof:
    transaction = db.get_transaction()
    required = set(required_serial_ids)
    if (
        not isinstance(proof, _PrelockedOpeningSerialUnionProof)
        or proof.seal is not _OPENING_SERIAL_UNION_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
        or not required.issubset(proof.serial_ids)
    ):
        _fail(
            "opening_serial_union_proof_invalid",
            "precondition_failed",
            "期初批量 SN 图证明无效、不完整或不属于当前事务",
        )
    return proof


def _seal_opening_terminal_task_batch_graph(
    db: Session,
    *,
    plan: _PrelockedOpeningTerminalTaskBatchPlan,
    serial_graph: _PrelockedOpeningSerialUnionProof,
) -> _PrelockedOpeningTerminalTaskBatchGraphProof:
    """Seal terminal dispositions/balances after a caller's serial union."""

    checked_plan = _require_opening_terminal_task_batch_plan(db, plan)
    checked_serial_graph = _require_opening_serial_union_graph_proof(
        db,
        serial_graph,
        required_serial_ids=checked_plan.serial_ids,
    )
    checked_root = checked_plan.root
    checked_principal_graph = checked_plan.principal_graph
    plans_by_task = dict(checked_plan.plans_by_task)
    balances = tuple(
        db.scalars(
            select(StockBalance)
            .where(StockBalance.stock_account_id.in_(checked_plan.account_ids))
            .order_by(StockBalance.stock_account_id)
            .with_for_update(of=StockBalance)
            .execution_options(populate_existing=True)
        ).all()
    ) if checked_plan.account_ids else ()
    balance_by_account_id = {row.stock_account_id: row for row in balances}

    task_graphs: list[_PrelockedOpeningTerminalTaskGraphProof] = []
    transaction = db.get_transaction()
    assert transaction is not None
    for task in checked_root.tasks:
        plans = plans_by_task[task.id]
        account_ids = tuple(
            sorted(
                {
                    account_id
                    for _round_id, plan in plans
                    for account_id in plan.account_ids
                },
                key=str,
            )
        )
        serial_ids = tuple(
            sorted(
                {
                    serial_id
                    for _round_id, plan in plans
                    for serial_id in plan.serial_ids
                },
                key=str,
            )
        )
        dispositions: dict[uuid.UUID, Mapping[uuid.UUID, object]] = {}
        for round_id, plan in plans:
            current_plan = _opening_start_reference_coordinates(
                db,
                task=task,
                round_id=round_id,
                require_complete_dispositions=(
                    plan.require_complete_dispositions
                ),
            )
            if current_plan != plan:
                _replay_invalid("期初终态批量引用签名发生扩展或收缩")
            dispositions[round_id] = _prove_reference_plan_disposition_resolutions(
                db,
                task=task,
                round_id=round_id,
                reference_plan=plan,
                locked_account_ids=account_ids,
                locked_serial_ids=serial_ids,
            )
        posting = _require_unique_posting(db, task.id)
        task_root = _LockedOpeningTaskRoot(
            session=db,
            transaction=transaction,
            task=task,
            current_ledger_cursor=checked_root.current_ledger_cursor,
            seal=_OPENING_TASK_ROOT_SEAL,
        )
        task_graphs.append(
            _PrelockedOpeningTerminalTaskGraphProof(
                session=db,
                transaction=transaction,
                root=task_root,
                principal_graph=checked_principal_graph,
                round_plans=plans,
                disposition_resolutions_by_round=dispositions,
                audit_replay_plan=posting_service._plan_opening_task_audit_replay(
                    db,
                    task=task,
                    disposition_resolutions_by_round=dispositions,
                ),
                account_ids=account_ids,
                serial_ids=serial_ids,
                balance_signatures=tuple(
                    _opening_balance_signature(balance_by_account_id[account_id])
                    for account_id in account_ids
                    if account_id in balance_by_account_id
                ),
                posting_id=posting.id,
                seal=_OPENING_TASK_GRAPH_SEAL,
            )
        )
    return _PrelockedOpeningTerminalTaskBatchGraphProof(
        session=db,
        transaction=transaction,
        root=checked_root,
        principal_graph=checked_principal_graph,
        serial_graph=checked_serial_graph,
        task_graphs=tuple(task_graphs),
        seal=_OPENING_TASK_BATCH_GRAPH_SEAL,
    )


def _require_opening_terminal_task_batch_graph_proof(
    db: Session,
    proof: object,
) -> _PrelockedOpeningTerminalTaskBatchGraphProof:
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _PrelockedOpeningTerminalTaskBatchGraphProof)
        or proof.seal is not _OPENING_TASK_BATCH_GRAPH_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
    ):
        _fail(
            "opening_terminal_batch_graph_proof_invalid",
            "precondition_failed",
            "期初终态批量预锁图证明无效或不属于当前事务",
        )
    root = _require_opening_terminal_task_batch_root(db, proof.root)
    posting_service._require_opening_task_principal_graph_proof(
        db,
        proof.principal_graph,
        required_task_ids=tuple(row.id for row in root.tasks),
    )
    _require_opening_serial_union_graph_proof(
        db,
        proof.serial_graph,
        required_serial_ids=tuple(
            sorted(
                {
                    serial_id
                    for graph in proof.task_graphs
                    for serial_id in graph.serial_ids
                },
                key=str,
            )
        ),
    )
    if tuple(graph.root.task.id for graph in proof.task_graphs) != tuple(
        row.id for row in root.tasks
    ):
        _fail(
            "opening_terminal_batch_graph_coordinates_invalid",
            "precondition_failed",
            "期初终态批量任务图坐标不完整",
        )
    return proof


def _validate_opening_terminal_batch_from_prelocked_graph(
    db: Session,
    *,
    proof: _PrelockedOpeningTerminalTaskBatchGraphProof,
    audit_proof: object,
    require_closed_task_ids: Sequence[uuid.UUID] = (),
    expected_ledger_cursor: int | None = None,
) -> tuple[tuple[FormalStocktakeTask, StocktakePosting], ...]:
    """Purely validate a batch after the caller takes one final audit head."""

    checked = _require_opening_terminal_task_batch_graph_proof(db, proof)
    if expected_ledger_cursor is not None and (
        not isinstance(expected_ledger_cursor, int)
        or isinstance(expected_ledger_cursor, bool)
        or expected_ledger_cursor < 0
        or expected_ledger_cursor != checked.root.current_ledger_cursor
    ):
        _replay_invalid("期初终态批量证明与库存投影快照游标不一致")
    required_closed = set(require_closed_task_ids)
    if not required_closed.issubset(
        {graph.root.task.id for graph in checked.task_graphs}
    ):
        _replay_invalid("期初终态批量关闭任务坐标不属于预锁图")
    return tuple(
        _validate_opening_terminal_from_prelocked_graph(
            db,
            proof=graph,
            audit_proof=audit_proof,
            require_closed=graph.root.task.id in required_closed,
        )
        for graph in checked.task_graphs
    )


def _validate_opening_inventory_batch_from_prelocked_graph(
    db: Session,
    *,
    proof: _PrelockedOpeningTerminalTaskBatchGraphProof,
    audit_proof: object,
    expected_ledger_cursor: int | None = None,
) -> tuple[tuple[FormalStocktakeTask, StocktakePosting], ...]:
    """Pure inventory-evidence validation for a sealed terminal batch.

    This is the batch counterpart of the recount-only inventory proof.  It
    intentionally excludes finalize post/close and reconciliation facts, but
    it cannot be entered without the transaction-bound task/principal/
    evidence/reference/serial/balance proof assembled before the final audit
    lock.
    """

    checked = _require_opening_terminal_task_batch_graph_proof(db, proof)
    if expected_ledger_cursor is not None and (
        not isinstance(expected_ledger_cursor, int)
        or isinstance(expected_ledger_cursor, bool)
        or expected_ledger_cursor < 0
        or expected_ledger_cursor != checked.root.current_ledger_cursor
    ):
        _replay_invalid("期初库存证据批量证明与账本游标不一致")
    return tuple(
        _validate_opening_inventory_evidence_from_prelocked_graph(
            db,
            proof=graph,
            audit_proof=audit_proof,
        )
        for graph in checked.task_graphs
    )


def _lock_opening_terminal_task_graph_for_task(
    db: Session,
    *,
    root: _LockedOpeningTaskRoot,
    principal_graph: (
        posting_service._PrelockedOpeningPrincipalGraphProof | None
    ) = None,
    supplied_user_ids: Sequence[str] = (),
) -> _PrelockedOpeningTerminalTaskGraphProof:
    """Expand one root into task evidence, references, serials and balances.

    Required order is ``ledger -> task/principal -> task evidence -> start ->
    inventory reference -> serial graph -> existing balances``.  A caller may
    supply the transaction-bound union-principal proof it acquired between
    root authorization steps.  Otherwise this boundary acquires the complete
    task historical-principal union itself before beginning task evidence.  It
    never locks an audit head or reconciliation source graph.
    """

    checked_root = _require_opening_terminal_task_root(db, root)
    task = checked_root.task
    if task.task_type != "opening" or task.status not in {"posted", "closed"}:
        _replay_invalid("期初终态任务状态不支持预锁重证")
    checked_principal_graph = (
        posting_service._lock_opening_task_principal_graph(
            db,
            task_ids=(task.id,),
            supplied_user_ids=supplied_user_ids,
        )
        if principal_graph is None
        else posting_service._require_opening_task_principal_graph_proof(
            db,
            principal_graph,
            required_task_ids=(task.id,),
        )
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
        not rounds
        or [row.round_no for row in rounds]
        != list(range(1, task.current_round_no + 1))
    ):
        _replay_invalid("期初终态任务轮次图缺失或不连续")
    lock_opening_stocktake_task_evidence(db, task.id, rounds[-1].id)
    round_plans = tuple(
        (
            round_row.id,
            _opening_start_reference_coordinates(
                db,
                task=task,
                round_id=round_row.id,
            ),
        )
        for round_row in rounds
    )
    first_plan = round_plans[0][1]
    if any(plan.scope_pairs != first_plan.scope_pairs for _, plan in round_plans):
        _replay_invalid("期初终态任务跨轮 owner/location 范围不一致")
    material_ids = tuple(
        sorted(
            {
                material_id
                for _, plan in round_plans
                for material_id in plan.material_ids
            },
            key=str,
        )
    )
    account_ids = tuple(
        sorted(
            {
                account_id
                for _, plan in round_plans
                for account_id in plan.account_ids
            },
            key=str,
        )
    )
    serial_ids = tuple(
        sorted(
            {
                serial_id
                for _, plan in round_plans
                for serial_id in plan.serial_ids
            },
            key=str,
        )
    )
    cutoff_at = _as_optional_utc(task.cutoff_at)
    if cutoff_at is None:
        _replay_invalid("期初终态任务缺少有效截止时点")
    lock_opening_stocktake_start_reference(
        db,
        task.region_org_id,
        tuple(owner_id for owner_id, _ in first_plan.scope_pairs),
        tuple(location_id for _, location_id in first_plan.scope_pairs),
        material_ids,
        cutoff_at,
    )
    lock_inventory_reference_graph(db, account_ids, cutoff_at)
    lock_inventory_serial_graph(db, serial_ids)

    disposition_resolutions_by_round: dict[
        uuid.UUID, Mapping[uuid.UUID, object]
    ] = {}
    for round_id, plan in round_plans:
        current_plan = _opening_start_reference_coordinates(
            db,
            task=task,
            round_id=round_id,
            require_complete_dispositions=plan.require_complete_dispositions,
        )
        if current_plan != plan:
            _replay_invalid("期初终态引用候选在锁定前后发生扩展或收缩")
        disposition_resolutions_by_round[round_id] = (
            _prove_reference_plan_disposition_resolutions(
                db,
                task=task,
                round_id=round_id,
                reference_plan=plan,
                locked_account_ids=account_ids,
                locked_serial_ids=serial_ids,
            )
        )

    balances = tuple(
        db.scalars(
            select(StockBalance)
            .where(StockBalance.stock_account_id.in_(account_ids))
            .order_by(StockBalance.stock_account_id)
            .with_for_update(of=StockBalance)
            .execution_options(populate_existing=True)
        ).all()
    ) if account_ids else ()
    posting = _require_unique_posting(db, task.id)
    transaction = db.get_transaction()
    assert transaction is not None
    return _PrelockedOpeningTerminalTaskGraphProof(
        session=db,
        transaction=transaction,
        root=checked_root,
        principal_graph=checked_principal_graph,
        round_plans=round_plans,
        disposition_resolutions_by_round=disposition_resolutions_by_round,
        audit_replay_plan=posting_service._plan_opening_task_audit_replay(
            db,
            task=task,
            disposition_resolutions_by_round=disposition_resolutions_by_round,
        ),
        account_ids=account_ids,
        serial_ids=serial_ids,
        balance_signatures=tuple(_opening_balance_signature(row) for row in balances),
        posting_id=posting.id,
        seal=_OPENING_TASK_GRAPH_SEAL,
    )


def _require_opening_terminal_task_graph_proof(
    db: Session,
    proof: object,
) -> _PrelockedOpeningTerminalTaskGraphProof:
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _PrelockedOpeningTerminalTaskGraphProof)
        or proof.seal is not _OPENING_TASK_GRAPH_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
    ):
        _fail(
            "opening_terminal_graph_proof_invalid",
            "precondition_failed",
            "期初终态预锁图证明无效或不属于当前事务",
        )
    _require_opening_terminal_task_root(db, proof.root)
    posting_service._require_opening_task_principal_graph_proof(
        db,
        proof.principal_graph,
        required_task_ids=(proof.root.task.id,),
    )
    return proof


def _validate_opening_inventory_evidence_from_prelocked_graph(
    db: Session,
    *,
    proof: _PrelockedOpeningTerminalTaskGraphProof,
    audit_proof: object,
) -> tuple[FormalStocktakeTask, StocktakePosting]:
    """Purely re-prove opening inventory evidence from one sealed graph.

    This recount-only internal boundary deliberately excludes the finalize
    post/close State, Outbox and Audit effect sets.  It still re-proves the
    complete reference/master signature, disposition mapping, task/count/
    review evidence, establishments, immutable inventory transaction effects,
    balances and serial projections.  The caller must already hold the final
    audit head.
    """

    checked = _require_opening_terminal_task_graph_proof(db, proof)
    try:
        _require_prelocked_audit_stream_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
        )
    except AuditChainError as exc:
        _replay_invalid("期初终态审计证明无效或不属于当前事务", cause=exc)
    posting_service._validate_prelocked_opening_task_principal_graph(
        db,
        proof=checked.principal_graph,
    )
    task = checked.root.task
    for round_id, plan in checked.round_plans:
        current_plan = _opening_start_reference_coordinates(
            db,
            task=task,
            round_id=round_id,
            require_complete_dispositions=plan.require_complete_dispositions,
        )
        if current_plan != plan:
            _replay_invalid("期初终态引用签名在最终重证前发生变化")
        resolutions = _prove_reference_plan_disposition_resolutions(
            db,
            task=task,
            round_id=round_id,
            reference_plan=plan,
            locked_account_ids=checked.account_ids,
            locked_serial_ids=checked.serial_ids,
        )
        if resolutions != checked.disposition_resolutions_by_round.get(round_id):
            _replay_invalid("期初终态处置解析在最终重证前发生变化")
    # Preserve the inventory-evidence contract for callers such as recount:
    # an immutable ledger/projection contradiction remains an
    # InventoryPostingError.  Full finalize/close facts are validated by the
    # separate terminal boundary below.
    task, establishments = posting_service._validate_opening_task_evidence(
        db,
        task_id=task.id,
        current_ledger_cursor=checked.root.current_ledger_cursor,
        disposition_resolutions_by_round=(
            checked.disposition_resolutions_by_round
        ),
        audit_replay_plan=checked.audit_replay_plan,
        audit_proof=audit_proof,
    )
    posting = _require_unique_posting(db, task.id)
    if posting.id != checked.posting_id or not establishments:
        _replay_invalid("期初终态唯一过账坐标发生变化")
    current_balances = tuple(
        db.scalars(
            select(StockBalance)
            .where(StockBalance.stock_account_id.in_(checked.account_ids))
            .order_by(StockBalance.stock_account_id)
            .execution_options(populate_existing=True)
        ).all()
    ) if checked.account_ids else ()
    if tuple(
        _opening_balance_signature(row) for row in current_balances
    ) != checked.balance_signatures:
        _replay_invalid("期初终态余额投影在预锁后发生变化")
    if posting.inventory_transaction_id is not None:
        transaction = db.get(
            InventoryTransaction,
            posting.inventory_transaction_id,
        )
        if transaction is None:
            _replay_invalid("期初不可变库存交易缺失")
        _validate_inventory_transaction_side_effects(
            db,
            transaction=transaction,
            audit_proof=audit_proof,
        )
    _validate_opening_inventory_projection(db, task=task, posting=posting)
    return task, posting


def _validate_opening_terminal_from_prelocked_graph(
    db: Session,
    *,
    proof: _PrelockedOpeningTerminalTaskGraphProof,
    audit_proof: object,
    require_closed: bool = False,
) -> tuple[FormalStocktakeTask, StocktakePosting]:
    """Pure terminal re-proof after caller has locked the final audit head."""

    try:
        task, posting = _validate_opening_inventory_evidence_from_prelocked_graph(
            db,
            proof=proof,
            audit_proof=audit_proof,
        )
    except posting_service.InventoryPostingError as exc:
        _replay_invalid("期初终态任务证据无法从预锁图重证", cause=exc)
    _validate_post_side_effects(
        db,
        task=task,
        posting=posting,
        audit_proof=audit_proof,
    )
    if require_closed:
        if task.status != "closed":
            _replay_invalid("期初任务尚未独立关闭")
        _validate_close_side_effects(
            db,
            task=task,
            posting=posting,
            audit_proof=audit_proof,
        )
    return task, posting


def _opening_balance_signature(row: StockBalance) -> tuple[object, ...]:
    return (
        row.stock_account_id,
        row.quantity,
        row.ledger_cursor,
        row.version,
        row.created_at,
        row.updated_at,
    )


def _validate_new_post_from_locked_graph(
    db: Session,
    *,
    task: FormalStocktakeTask,
    posting: StocktakePosting,
    audit_proof: object,
) -> None:
    """Validate newly appended terminal effects without re-entering owners.

    The caller still holds the ledger, task/evidence/reference/mutable and
    audit locks and has just completed the full approved-evidence re-proof.
    Re-running the public posted-task replay here would acquire owner helpers
    after the audit head and invert the global order.
    """

    if (
        task.status != "posted"
        or posting.task_id != task.id
        or posting.posting_kind != "opening"
    ):
        _replay_invalid("期初新过账终态主体不一致")
    _validate_post_side_effects(
        db,
        task=task,
        posting=posting,
        audit_proof=audit_proof,
    )
    if posting.inventory_transaction_id is not None:
        transaction = db.get(
            InventoryTransaction,
            posting.inventory_transaction_id,
        )
        if transaction is None:
            _replay_invalid("期初新过账不可变库存交易缺失")
        _validate_inventory_transaction_side_effects(
            db,
            transaction=transaction,
            audit_proof=audit_proof,
        )
    _validate_opening_inventory_projection(db, task=task, posting=posting)


def _post_approved_opening_stocktake(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: PostOpeningStocktakeCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakePostResult:
    supplied_actor = _validate_supplied_actor(actor)
    checked = _validate_post_command(command)
    checked_key = _require_idempotency_key(idempotency_key)
    checked_request_id = _require_request_id(request_id)
    key_hash = _storage_hash("post", checked_key)

    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("opening-finalize-idempotency", key_hash),
            _advisory_coordinate("opening-finalize-task", str(checked.task_id)),
        ),
    )
    # The mutable ledger cursor is the first row lock in every inventory
    # writer.  Task/RBAC/evidence locks follow it; advisory coordinates above
    # do not participate in the row-lock graph.
    terminal_root = _lock_opening_terminal_task_root(
        db,
        task_id=checked.task_id,
    )
    task = terminal_root.task
    existing_by_key = db.scalar(
        _select_only_reference_statement(
            db,
            select(StocktakePosting).where(
                StocktakePosting.idempotency_key_hash == key_hash
            ),
        ).execution_options(populate_existing=True)
    )

    replay_reconciliation_user_ids = (
        reconciliation_service._opening_control_reconciliation_historical_user_ids(
            db,
            task_ids=(task.id,),
        )
        if task.status == "closed"
        else ()
    )
    principal_graph = posting_service._lock_opening_task_principal_graph(
        db,
        task_ids=(task.id,),
        supplied_user_ids=(
            supplied_actor.user_id,
            *replay_reconciliation_user_ids,
        ),
    )
    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    assignment, _grant = _authorize_finalizer(db, current_actor, now)
    request_hash = _request_hash("post", current_actor, checked)

    if existing_by_key is not None:
        if (
            existing_by_key.task_id != task.id
            or existing_by_key.request_hash != request_hash
        ):
            _fail(
                "opening_finalize_idempotency_conflict",
                "conflict",
                "幂等键已绑定其他期初过账请求",
            )
        terminal_graph = _lock_opening_terminal_task_graph_for_task(
            db,
            root=terminal_root,
            principal_graph=principal_graph,
        )
        reconciliation_graph = None
        if task.status == "closed":
            try:
                reconciliation_graph = reconciliation_service._lock_opening_control_reconciliation_graph_for_task(
                    db,
                    task_id=task.id,
                    principal_graph=principal_graph,
                )
            except reconciliation_service.OpeningControlReconciliationError as exc:
                _map_control_reconciliation_error(exc)
        _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
            db,
            stream_key=INVENTORY_STREAM_KEY,
        )
        replayed_task, replayed_posting = (
            _validate_opening_terminal_from_prelocked_graph(
                db,
                proof=terminal_graph,
                audit_proof=audit_proof,
                require_closed=task.status == "closed",
            )
        )
        if replayed_posting.id != existing_by_key.id:
            _replay_invalid("期初过账幂等坐标与唯一终态过账不一致")
        if reconciliation_graph is not None:
            try:
                reconciliation_service._require_approved_opening_control_reconciliation_from_prelocked_graph(
                    db,
                    task_id=task.id,
                    proof=reconciliation_graph,
                    audit_proof=audit_proof,
                )
            except reconciliation_service.OpeningControlReconciliationError as exc:
                _map_control_reconciliation_error(exc)
        return replace(
            _post_result(db, replayed_task, replayed_posting),
            replayed=True,
        )

    if db.scalar(
        select(StocktakePosting.id)
        .where(
            StocktakePosting.task_id == task.id,
            StocktakePosting.posting_kind == "opening",
        )
        .limit(1)
    ) is not None:
        _fail(
            "opening_finalize_already_posted",
            "conflict",
            "该期初任务已经由其他幂等请求形成过账事实",
        )
    _require_expected_state(
        task,
        expected_status="approved",
        expected_version=checked.expected_version,
        operation="过账",
    )

    # The PostgreSQL owner lock graph is fixed across every inventory writer:
    # ledger head -> task-local evidence -> complete opening references ->
    # inventory account references -> serial graph -> mutable projections ->
    # audit head.  The task/RBAC rows above are independent serialization
    # points and are already held.  SQLite retains its direct evidence locks,
    # but makes no production concurrency claim.
    current_round_id = _current_round_id(db, task)
    lock_opening_stocktake_task_evidence(db, task.id, current_round_id)
    round_ids = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeRound.id)
                .where(StocktakeRound.task_id == task.id)
                .order_by(StocktakeRound.round_no, StocktakeRound.id),
            )
        ).all()
    )
    if len(round_ids) != task.current_round_no or current_round_id not in round_ids:
        _evidence_invalid("期初任务完整轮次坐标缺失或不连续")
    reference_plans_by_round = {
        round_id: _opening_start_reference_coordinates(
            db,
            task=task,
            round_id=round_id,
        )
        for round_id in round_ids
    }
    reference_plan = reference_plans_by_round[current_round_id]
    if any(
        plan.scope_pairs != reference_plan.scope_pairs
        for plan in reference_plans_by_round.values()
    ):
        _evidence_invalid("期初任务跨轮 owner/location 范围不一致")
    all_reference_material_ids = tuple(
        sorted(
            {
                material_id
                for plan in reference_plans_by_round.values()
                for material_id in plan.material_ids
            },
            key=str,
        )
    )
    cutoff_at = _as_optional_utc(task.cutoff_at)
    if cutoff_at is None:
        _evidence_invalid("期初任务缺少有效截止时点")
    # Keep owner/location as one sorted, pair-deduplicated coordinate list.
    # The owner helper validates these parallel arrays as exact scope pairs.
    lock_opening_stocktake_start_reference(
        db,
        task.region_org_id,
        tuple(
            owner_org_id
            for owner_org_id, _location_id in reference_plan.scope_pairs
        ),
        tuple(
            location_id
            for _owner_org_id, location_id in reference_plan.scope_pairs
        ),
        all_reference_material_ids,
        cutoff_at,
    )

    # This first pass deliberately verifies only non-audit evidence:
    # count/recount/review audit membership is proved after the low-level
    # posting has acquired the audit head as its final shared lock.  Any later
    # proof failure invalidates the caller's whole transaction, including the
    # uncommitted ledger rows.
    evidence = _lock_and_validate_approved_evidence(
        db,
        task,
        actor=current_actor,
    )
    total_quantity = _opening_total_quantity(evidence)
    evidence = _materialize_observation_accounts(db, evidence, created_at=now)
    account_ids = tuple(
        sorted(
            set(
                _opening_inventory_account_ids(
                    db,
                    evidence,
                    reference_plan=reference_plan,
                )
            ).union(
                account_id
                for plan in reference_plans_by_round.values()
                for account_id in plan.account_ids
            ),
            key=str,
        )
    )
    lock_inventory_reference_graph(db, account_ids, cutoff_at)
    evidence = _reread_inventory_accounts(
        db,
        actor=current_actor,
        evidence=evidence,
    )
    evidence_serial_ids = set(_opening_serial_ids(evidence))
    serial_ids = tuple(
        sorted(
            {
                serial_id
                for plan in reference_plans_by_round.values()
                for serial_id in plan.serial_ids
            },
            key=str,
        )
    )
    if not evidence_serial_ids.issubset(serial_ids):
        _evidence_invalid("期初 SN 坐标在引用锁定前后发生扩展")
    lock_inventory_serial_graph(db, serial_ids)
    evidence = _lock_review_disposition_resolutions(
        db,
        evidence,
        reference_plan=reference_plan,
        locked_account_ids=account_ids,
        locked_serial_ids=serial_ids,
    )
    disposition_resolutions_by_round = {
        round_id: _prove_reference_plan_disposition_resolutions(
            db,
            task=task,
            round_id=round_id,
            reference_plan=plan,
            locked_account_ids=account_ids,
            locked_serial_ids=serial_ids,
        )
        for round_id, plan in reference_plans_by_round.items()
    }
    if (
        disposition_resolutions_by_round.get(evidence.round_row.id)
        != evidence.disposition_resolutions
    ):
        _evidence_invalid("期初最终轮处置解析与全轮次封存不一致")
    evidence = replace(
        evidence,
        audit_replay_plan=posting_service._plan_opening_task_audit_replay(
            db,
            task=task,
            disposition_resolutions_by_round=disposition_resolutions_by_round,
        ),
    )
    posting_command = _opening_inventory_command(evidence)
    prelocked_reference_graph = (
        posting_service._issue_prelocked_inventory_graph_proof(
            db,
            command=posting_command,
            account_ids=account_ids,
            serial_ids=serial_ids,
        )
    )
    prelocked_inventory_graph = _prelock_inventory_graph(
        db,
        actor=current_actor,
        evidence=evidence,
        posting_command=posting_command,
        account_ids=account_ids,
        prelocked_reference_graph=prelocked_reference_graph,
    )
    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    assignment, _grant = _authorize_finalizer(db, current_actor, now)
    _require_expected_state(
        task,
        expected_status="approved",
        expected_version=checked.expected_version,
        operation="过账",
    )
    if now <= _as_utc(evidence.headquarters_review.reviewed_at):
        _fail(
            "opening_finalize_clock_not_monotonic",
            "service_unavailable",
            "数据库时间未晚于总部复核时间，禁止形成期初过账顺序",
        )

    posting_sources = _opening_posting_sources(evidence)
    if sum((row.quantity for row in posting_sources), start=_ZERO) != total_quantity:
        _evidence_invalid("期初过账来源合计与已校验实盘合计不一致")
    transaction_id: uuid.UUID | None = None
    ledger_cursor = task.cutoff_ledger_cursor or 0
    audit_proof: object
    if posting_sources:
        transaction_commit = posting_service._post_approved_opening_transaction(
            db,
            actor=current_actor,
            task_id=task.id,
            proof=posting_service._OpeningFinalizationProof(
                task_id=task.id,
                expected_task_version=checked.expected_version,
                round_id=evidence.round_row.id,
                scope_manifest_sha256=task.scope_manifest_sha256,
                snapshot_manifest_sha256=task.snapshot_manifest_sha256,
                count_manifest_sha256=evidence.round_row.count_manifest_sha256,
                control_manifest_sha256=task.control_manifest_sha256,
                regional_review_id=evidence.regional_review.id,
                headquarters_review_id=evidence.headquarters_review.id,
            ),
            command=posting_command,
            idempotency_key_hash=_derived_hash("transaction-idempotency", key_hash),
            request_hash=_derived_hash("transaction-request", request_hash),
            request_reference=_request_reference("inventory", checked_request_id),
            occurred_at=now,
            prelocked_reference_graph=prelocked_inventory_graph,
        )
        transaction_result = transaction_commit.result
        audit_proof = transaction_commit.audit_proof
        transaction_id = transaction_result.transaction_id
        ledger_cursor = transaction_result.ledger_cursor
    else:
        # No balance, movement or SN projection will be written.  The audit
        # head is therefore the final lock before the zero-opening task fact.
        _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
            db,
            stream_key=INVENTORY_STREAM_KEY,
        )

    posting_service._validate_prelocked_opening_task_principal_graph(
        db,
        proof=principal_graph,
    )

    # Full count/recount/review audit proof runs only after every inventory
    # lock (and, for a positive opening, the transaction's own final audit
    # lock) is held.  Task state and freezes are still approved/active here.
    evidence = _validate_locked_approved_evidence(
        db,
        evidence,
        actor=current_actor,
        audit_proof=audit_proof,
    )

    posting = StocktakePosting(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=evidence.round_row.id,
        posting_kind="opening",
        inventory_transaction_id=transaction_id,
        total_quantity=total_quantity,
        idempotency_key_hash=key_hash,
        request_hash=request_hash,
        posted_by_user_id=current_actor.user_id,
        posted_at=now,
        created_at=now,
    )
    db.add(posting)
    db.flush()

    if transaction_id is not None:
        movements = tuple(
            db.scalars(
                select(InventoryMovement)
                .where(InventoryMovement.transaction_id == transaction_id)
                .order_by(InventoryMovement.line_no)
            ).all()
        )
        if len(movements) != len(posting_sources):
            _fail(
                "opening_finalize_movement_binding_invalid",
                "service_unavailable",
                "期初库存流水无法与实盘行逐项绑定",
            )
        for source, movement in zip(posting_sources, movements, strict=True):
            db.add(
                StocktakePostingItem(
                    posting_id=posting.id,
                    inventory_movement_id=movement.id,
                    task_id=task.id,
                    round_id=evidence.round_row.id,
                    count_line_id=source.count_line_id,
                    difference_id=source.difference_id,
                    quantity=source.quantity,
                    created_at=now,
                )
            )
        db.flush()

    for scope in evidence.scopes:
        db.add(
            InventoryOpeningEstablishment(
                id=uuid.uuid4(),
                task_id=task.id,
                scope_id=scope.id,
                owner_org_id=scope.owner_org_id,
                location_id=scope.location_id,
                round_id=evidence.round_row.id,
                posting_id=posting.id,
                regional_review_id=evidence.regional_review.id,
                headquarters_review_id=evidence.headquarters_review.id,
                cutoff_ledger_cursor=task.cutoff_ledger_cursor,
                cutoff_at=task.cutoff_at,
                established_ledger_cursor=ledger_cursor,
                scope_manifest_sha256=task.scope_manifest_sha256,
                snapshot_manifest_sha256=task.snapshot_manifest_sha256,
                count_manifest_sha256=evidence.round_row.count_manifest_sha256,
                control_manifest_sha256=task.control_manifest_sha256,
                has_pending_control_difference=(
                    evidence.pending_control_difference_count > 0
                ),
                established_by_user_id=current_actor.user_id,
                established_at=now,
                created_at=now,
            )
        )

    # SQLite's production-schema test boundary has an immediate task terminal
    # guard, while PostgreSQL proves the same graph with deferred constraint
    # triggers at commit.  Persist the append-only establishment rows before
    # the task is moved to ``posted`` so both dialects observe the same atomic
    # causal order.  This flush does not commit or expose a partial state.
    db.flush()

    for freeze in evidence.freezes:
        freeze.status = "released"
        freeze.valid_to = now
        freeze.released_by_user_id = current_actor.user_id
        freeze.release_reason = "期初实盘及两级复核已完成并原子入账"
        freeze.version += 1
        freeze.updated_at = now

    previous_status = task.status
    task.status = "posted"
    task.posted_at = now
    task.version += 1
    task.updated_at = now
    metadata = _post_metadata(
        posting=posting,
        task_version=task.version,
        ledger_cursor=ledger_cursor,
        scope_count=len(evidence.scopes),
        pending_count=evidence.pending_control_difference_count,
    )
    _append_task_effects(
        db,
        task=task,
        from_status=previous_status,
        to_status="posted",
        reason="opening_stocktake_posted",
        event_type="stocktake.opening.posted",
        actor=current_actor,
        idempotency_anchor=str(posting.id),
        metadata=metadata,
        request_reference=_request_reference("post", checked_request_id),
        audit_aggregate_type="stocktake_posting",
        audit_aggregate_id=str(posting.id),
        assignment=assignment,
        now=now,
    )
    db.flush()

    posting_service._validate_prelocked_opening_task_principal_graph(
        db,
        proof=principal_graph,
    )

    # Final in-transaction proof: a failure here invalidates the whole caller
    # transaction, so no balance, posting, establishment or release can commit
    # without the exact immutable evidence graph.
    _validate_new_post_from_locked_graph(
        db,
        task=task,
        posting=posting,
        audit_proof=audit_proof,
    )
    return _post_result(db, task, posting)


def _close_posted_opening_stocktake(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: CloseOpeningStocktakeCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeCloseResult:
    supplied_actor = _validate_supplied_actor(actor)
    checked = _validate_close_command(command)
    checked_key = _require_idempotency_key(idempotency_key)
    checked_request_id = _require_request_id(request_id)
    key_hash = _storage_hash("close", checked_key)
    state_key = _event_key("close-state", key_hash)
    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("opening-close-idempotency", key_hash),
            _advisory_coordinate("opening-finalize-task", str(checked.task_id)),
        ),
    )
    terminal_root = _lock_opening_terminal_task_root(
        db,
        task_id=checked.task_id,
    )
    task = terminal_root.task
    existing_state = db.scalar(
        _select_only_reference_statement(
            db,
            select(StateTransitionEvent).where(
                StateTransitionEvent.idempotency_key == state_key
            ),
        ).execution_options(populate_existing=True)
    )
    reconciliation_user_ids = (
        reconciliation_service._opening_control_reconciliation_historical_user_ids(
            db,
            task_ids=(task.id,),
        )
    )
    principal_graph = posting_service._lock_opening_task_principal_graph(
        db,
        task_ids=(task.id,),
        supplied_user_ids=(supplied_actor.user_id, *reconciliation_user_ids),
    )
    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    assignment, _grant = _authorize_finalizer(db, current_actor, now)
    request_hash = _request_hash("close", current_actor, checked)

    replaying = existing_state is not None
    if replaying:
        assert existing_state is not None
        metadata = existing_state.metadata_jsonb
        if (
            existing_state.aggregate_type != "stocktake_task"
            or existing_state.aggregate_id != str(task.id)
            or not isinstance(metadata, dict)
            or metadata.get("request_hash") != request_hash
        ):
            _fail(
                "opening_close_idempotency_conflict",
                "conflict",
                "幂等键已绑定其他期初关闭请求",
            )
    else:
        if task.status == "closed":
            _fail(
                "opening_close_already_completed",
                "conflict",
                "该期初任务已由其他幂等请求关闭",
            )
        _require_expected_state(
            task,
            expected_status="posted",
            expected_version=checked.expected_version,
            operation="关闭",
        )

    # The root above owns ledger -> task; principal/RBAC is now held.  Expand
    # the opening graph, then the independent reconciliation graph, and only
    # then take the final audit head.  Everything after audit is a plain proof.
    terminal_graph = _lock_opening_terminal_task_graph_for_task(
        db,
        root=terminal_root,
        principal_graph=principal_graph,
    )
    try:
        reconciliation_graph = (
            reconciliation_service._lock_opening_control_reconciliation_graph_for_task(
                db,
                task_id=task.id,
                principal_graph=principal_graph,
            )
        )
    except reconciliation_service.OpeningControlReconciliationError as exc:
        _map_control_reconciliation_error(exc)
    _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    task, posting = _validate_opening_terminal_from_prelocked_graph(
        db,
        proof=terminal_graph,
        audit_proof=audit_proof,
        require_closed=replaying,
    )
    try:
        reconciliation_proof = (
            reconciliation_service._require_approved_opening_control_reconciliation_from_prelocked_graph(
                db,
                task_id=task.id,
                proof=reconciliation_graph,
                audit_proof=audit_proof,
            )
        )
    except reconciliation_service.OpeningControlReconciliationError as exc:
        _map_control_reconciliation_error(exc)
    if replaying:
        if (
            reconciliation_proof.approved_at is not None
            and task.closed_at is not None
            and _as_utc(task.closed_at) < _as_utc(reconciliation_proof.approved_at)
        ):
            _replay_invalid("期初关闭时间早于独立对账批准时间")
        return replace(_close_result(task, posting), replayed=True)

    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    assignment, _grant = _authorize_finalizer(
        db,
        current_actor,
        now,
        lock_rows=False,
    )
    _require_expected_state(
        task,
        expected_status="posted",
        expected_version=checked.expected_version,
        operation="关闭",
    )
    if now <= _as_utc(task.posted_at):
        _fail(
            "opening_close_clock_not_monotonic",
            "service_unavailable",
            "数据库时间未晚于期初过账时间，禁止形成关闭顺序",
        )
    if (
        reconciliation_proof.approved_at is not None
        and now < _as_utc(reconciliation_proof.approved_at)
    ):
        _fail(
            "opening_close_reconciliation_clock_not_monotonic",
            "service_unavailable",
            "数据库时间早于独立对账批准时间，禁止形成关闭顺序",
        )

    previous_status = task.status
    task.status = "closed"
    task.closed_at = now
    task.version += 1
    task.updated_at = now
    metadata = {
        "idempotency_key_hash": key_hash,
        "inventory_transaction_id": (
            str(posting.inventory_transaction_id)
            if posting.inventory_transaction_id is not None
            else None
        ),
        "posting_id": str(posting.id),
        "request_hash": request_hash,
        "task_version": task.version,
    }
    _append_task_effects(
        db,
        task=task,
        from_status=previous_status,
        to_status="closed",
        reason="opening_stocktake_closed",
        event_type="stocktake.opening.closed",
        actor=current_actor,
        idempotency_anchor=key_hash,
        metadata=metadata,
        request_reference=_request_reference("close", checked_request_id),
        audit_aggregate_type="stocktake_task",
        audit_aggregate_id=str(task.id),
        assignment=assignment,
        now=now,
    )
    db.flush()
    posting_service._validate_prelocked_opening_task_principal_graph(
        db,
        proof=principal_graph,
    )
    _validate_close_side_effects(
        db,
        task=task,
        posting=posting,
        audit_proof=audit_proof,
    )
    return _close_result(task, posting)


def _require_approved_control_reconciliation(
    db: Session,
    task_id: uuid.UUID,
) -> reconciliation_service.OpeningControlReconciliationProof:
    """Require an exact approved run while preserving historical flags."""

    try:
        return reconciliation_service.require_approved_opening_control_reconciliation(
            db,
            task_id=task_id,
        )
    except reconciliation_service.OpeningControlReconciliationError as exc:
        _map_control_reconciliation_error(exc)
    raise AssertionError("unreachable opening reconciliation boundary")


def _reprove_approved_control_reconciliation(
    db: Session,
    task_id: uuid.UUID,
) -> None:
    """Read-only exact re-proof for closed graphs and idempotent replay."""

    proof = _require_approved_control_reconciliation(db, task_id)
    task = db.get(FormalStocktakeTask, task_id)
    if task is None or task.closed_at is None:
        _replay_invalid("期初关闭任务或关闭时间缺失")
    if proof.approved_at is not None and _as_utc(task.closed_at) < _as_utc(
        proof.approved_at
    ):
        _replay_invalid("期初关闭时间早于独立对账批准时间")


def _map_control_reconciliation_error(
    exc: reconciliation_service.OpeningControlReconciliationError,
) -> NoReturn:
    if exc.code == "opening_reconciliation_pending":
        _fail(
            "opening_close_reconciliation_pending",
            "precondition_failed",
            "期初任务仍有待核实外部控制差异，完成独立对账前禁止关闭",
            cause=exc,
        )
    _fail(
        "opening_close_reconciliation_evidence_invalid",
        "service_unavailable",
        "期初对账批准证据无法重证，任务未关闭",
        cause=exc,
    )


def _current_round_id(
    db: Session,
    task: FormalStocktakeTask,
) -> uuid.UUID:
    """Read the current round coordinate before the task-evidence owner lock."""

    if task.current_round_no <= 0:
        _evidence_invalid("期初任务缺少当前盘点轮次")
    statement = select(StocktakeRound.id).where(
        StocktakeRound.task_id == task.id,
        StocktakeRound.round_no == task.current_round_no,
    )
    round_ids = tuple(
        db.scalars(_select_only_reference_statement(db, statement)).all()
    )
    if len(round_ids) != 1:
        _evidence_invalid("期初任务当前轮次坐标不唯一")
    return round_ids[0]


def _opening_start_reference_coordinates(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_id: uuid.UUID,
    require_complete_dispositions: bool = True,
) -> _OpeningReferencePlan:
    """Derive the complete pre-account-creation reference-lock coordinates.

    PostgreSQL calls this only after the task-local evidence owner helper has
    locked the evidence rows.  Owner and location values remain paired all the
    way to the 0027 start helper; neither dimension is independently deduped.
    Material candidates cover scope filters, control/evidence rows, every
    existing account in an exact scope pair, and any account referenced by the
    immutable snapshot/count/difference graph.  Raw observation identifiers
    are resolved to the same material/SN candidate sets used by disposition
    replay, so its later proof cannot expand the already-held owner graph.
    """

    from . import opening_observation_disposition as disposition_service
    from .opening_observation_disposition import (
        _LockedResolutionReferences,
        _load_cutoff_policy,
        _prove_lot,
        _prove_material,
        _read_resolution_lots,
        _read_resolution_material_rows,
        _read_resolution_serial_rows,
    )

    scope_statement = (
        select(FormalStocktakeScope)
        .where(FormalStocktakeScope.task_id == task.id)
        .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id)
    )
    scopes = tuple(
        db.scalars(
            _select_only_reference_statement(db, scope_statement)
        ).all()
    )
    if not scopes:
        _evidence_invalid("期初任务缺少正式盘点范围")
    scope_pairs = tuple(
        sorted(
            ((row.owner_org_id, row.location_id) for row in scopes),
            key=lambda pair: (str(pair[0]), str(pair[1])),
        )
    )
    if len(set(scope_pairs)) != len(scope_pairs):
        _evidence_invalid("期初任务 owner/location 范围坐标重复")

    material_ids = {
        row.material_id for row in scopes if row.material_id is not None
    }
    material_ids.update(
        row
        for row in db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeControlSnapshotLine.material_id)
                .where(StocktakeControlSnapshotLine.task_id == task.id)
                .order_by(StocktakeControlSnapshotLine.line_no),
            )
        ).all()
        if row is not None
    )
    reference_serial_ids = set(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeCountSerial.serial_id)
                .where(StocktakeCountSerial.round_id == round_id)
                .order_by(
                    StocktakeCountSerial.count_line_id,
                    StocktakeCountSerial.serial_id,
                ),
            )
        ).all()
    )
    observation_statement = (
        select(StocktakeCountObservation)
        .where(
            StocktakeCountObservation.task_id == task.id,
            StocktakeCountObservation.round_id == round_id,
        )
        .order_by(
            StocktakeCountObservation.scope_id,
            StocktakeCountObservation.observation_no,
        )
    )
    observations = tuple(
        db.scalars(
            _select_only_reference_statement(db, observation_statement)
        ).all()
    )
    observation_signatures = tuple(
        _observation_resolution_signature(row) for row in observations
    )
    disposition_rows = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeObservationDisposition)
                .where(
                    StocktakeObservationDisposition.task_id == task.id,
                    StocktakeObservationDisposition.round_id == round_id,
                )
                .order_by(
                    StocktakeObservationDisposition.observation_id,
                    StocktakeObservationDisposition.id,
                ),
            )
        ).all()
    )
    disposition_by_observation = {
        row.observation_id: row for row in disposition_rows
    }
    if len(disposition_by_observation) != len(disposition_rows):
        _evidence_invalid("期初观察处置坐标重复")
    pending_ids = {
        row.id
        for row in observations
        if row.verification_status == "pending_verification"
    }
    disposition_observation_ids = set(disposition_by_observation)
    if require_complete_dispositions:
        if disposition_observation_ids != pending_ids:
            _evidence_invalid("期初待核实观察与处置集合不一致")
    elif not disposition_observation_ids.issubset(pending_ids):
        _evidence_invalid("期初观察处置包含非待核或未知观察")

    disposition_candidates: list[_DispositionResolutionCandidate] = []
    for observation in observations:
        if observation.material_id is not None:
            material_ids.add(observation.material_id)
        if observation.serial_id is not None:
            reference_serial_ids.add(observation.serial_id)
        materials, material_mappings = _read_resolution_material_rows(
            db,
            observation,
            populate_existing=False,
        )
        material_ids.update(row.id for row in materials)
        material_ids.update(row.object_id for row in material_mappings)
        candidate_material_ids = {
            row.id for row in materials
        } | {row.object_id for row in material_mappings}
        lots = _read_resolution_lots(
            db,
            material_ids=tuple(sorted(candidate_material_ids, key=str)),
            lot_no=observation.lot_no_raw,
            populate_existing=False,
        )
        serial_signatures: list[
            tuple[
                uuid.UUID,
                tuple[uuid.UUID, ...],
                tuple[tuple[uuid.UUID, uuid.UUID], ...],
            ]
        ] = []
        for material in sorted(materials, key=lambda row: str(row.id)):
            serials, serial_mappings = _read_resolution_serial_rows(
                db,
                observation=observation,
                material=material,
                populate_existing=False,
            )
            reference_serial_ids.update(row.id for row in serials)
            reference_serial_ids.update(
                row.object_id for row in serial_mappings
            )
            serial_signatures.append(
                (
                    material.id,
                    tuple(row.id for row in serials),
                    tuple((row.id, row.object_id) for row in serial_mappings),
                )
            )

        disposition = disposition_by_observation.get(observation.id)
        if disposition is None:
            continue
        command = disposition_service.RecordOpeningObservationDispositionCommand(
            task_id=task.id,
            round_id=round_id,
            observation_id=observation.id,
            disposition=disposition.disposition,
            reason_code=disposition.reason_code,
            comment=disposition.comment,
            resolved_material_id=disposition.resolved_material_id,
            resolved_lot_id=disposition.resolved_lot_id,
            resolved_serial_id=disposition.resolved_serial_id,
        )
        material_ids.update(
            value
            for value in (
                command.resolved_material_id,
                observation.material_id,
            )
            if value is not None
        )
        reference_serial_ids.update(
            value
            for value in (
                command.resolved_serial_id,
                observation.serial_id,
            )
            if value is not None
        )
        policy_signature: tuple[object, ...] | None = None
        cutoff_account_ids: tuple[uuid.UUID, ...] = ()
        snapshot_account_ids: tuple[uuid.UUID, ...] = ()
        if command.disposition == "resolved_existing_master":
            try:
                locked_references = _LockedResolutionReferences(
                    materials=materials,
                    material_mappings=material_mappings,
                    lots=lots,
                )
                material = _prove_material(observation, locked_references)
                lot = _prove_lot(observation, material, locked_references)
                policy = _load_cutoff_policy(db, material.id, task.cutoff_at)
            except disposition_service.OpeningObservationDispositionError as exc:
                _evidence_invalid(
                    "期初观察处置候选主数据无法唯一解析",
                    cause=exc,
                )
            policy_signature = _policy_resolution_signature(policy)
            cutoff_accounts, snapshot_accounts = _read_resolution_account_rows(
                db,
                task=task,
                observation=observation,
                material_id=material.id,
                lot_id=lot.id if lot is not None else None,
                populate_existing=False,
            )
            cutoff_account_ids = tuple(row.id for row in cutoff_accounts)
            snapshot_account_ids = tuple(row.id for row in snapshot_accounts)
        disposition_candidates.append(
            _DispositionResolutionCandidate(
                observation_id=observation.id,
                observation_signature=_observation_resolution_signature(
                    observation
                ),
                disposition_id=disposition.id,
                disposition_signature=_disposition_resolution_signature(
                    disposition
                ),
                command=command,
                material_ids=tuple(row.id for row in materials),
                material_mapping_signature=tuple(
                    (row.id, row.object_id) for row in material_mappings
                ),
                lot_signature=tuple(
                    (row.id, row.material_id) for row in lots
                ),
                policy_signature=policy_signature,
                cutoff_account_ids=cutoff_account_ids,
                snapshot_account_ids=snapshot_account_ids,
                serial_signatures=tuple(serial_signatures),
            )
        )

    material_ids.update(
        row.resolved_material_id
        for row in disposition_rows
        if row.resolved_material_id is not None
    )
    reference_serial_ids.update(
        row.resolved_serial_id
        for row in disposition_rows
        if row.resolved_serial_id is not None
    )

    difference_rows = tuple(
        db.execute(
            _select_only_reference_statement(
                db,
                select(
                    StocktakeDifference.material_id,
                    StocktakeDifference.expected_account_id,
                    StocktakeDifference.observed_account_id,
                    StocktakeDifference.serial_id,
                )
                .where(
                    StocktakeDifference.task_id == task.id,
                    StocktakeDifference.round_id == round_id,
                )
                .order_by(StocktakeDifference.difference_no),
            )
        ).all()
    )
    material_ids.update(
        material_id
        for material_id, _expected_id, _observed_id, _serial_id in difference_rows
        if material_id is not None
    )
    reference_serial_ids.update(
        serial_id
        for _material_id, _expected_id, _observed_id, serial_id in difference_rows
        if serial_id is not None
    )

    evidence_account_ids = set(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeSnapshotLine.stock_account_id)
                .where(StocktakeSnapshotLine.task_id == task.id)
                .order_by(StocktakeSnapshotLine.stock_account_id),
            )
        ).all()
    )
    evidence_account_ids.update(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeCountLine.stock_account_id)
                .where(
                    StocktakeCountLine.task_id == task.id,
                    StocktakeCountLine.round_id == round_id,
                )
                .order_by(StocktakeCountLine.stock_account_id),
            )
        ).all()
    )
    for (
        _material_id,
        expected_account_id,
        observed_account_id,
        _serial_id,
    ) in difference_rows:
        if expected_account_id is not None:
            evidence_account_ids.add(expected_account_id)
        if observed_account_id is not None:
            evidence_account_ids.add(observed_account_id)

    scope_account_condition = or_(
        *(
            (StockAccount.owner_org_id == owner_org_id)
            & (StockAccount.location_id == location_id)
            for owner_org_id, location_id in scope_pairs
        )
    )
    account_condition = scope_account_condition
    if evidence_account_ids:
        account_condition = or_(
            scope_account_condition,
            StockAccount.id.in_(tuple(sorted(evidence_account_ids, key=str))),
        )
    account_rows = tuple(
        db.execute(
            _select_only_reference_statement(
                db,
                select(StockAccount.id, StockAccount.material_id)
                .where(account_condition)
                .order_by(StockAccount.id),
            )
        ).all()
    )
    material_ids.update(row.material_id for row in account_rows)
    checked_material_ids = tuple(sorted(material_ids, key=str))
    checked_account_ids = tuple(row.id for row in account_rows)
    checked_serial_ids = tuple(sorted(reference_serial_ids, key=str))
    master_signatures = _capture_opening_reference_master_signatures(
        db,
        task=task,
        scope_pairs=scope_pairs,
        material_ids=checked_material_ids,
        account_ids=checked_account_ids,
        serial_ids=checked_serial_ids,
    )
    return _OpeningReferencePlan(
        require_complete_dispositions=require_complete_dispositions,
        scope_pairs=scope_pairs,
        material_ids=checked_material_ids,
        account_ids=checked_account_ids,
        serial_ids=checked_serial_ids,
        observation_signatures=observation_signatures,
        disposition_candidates=tuple(
            sorted(
                disposition_candidates,
                key=lambda candidate: (
                    str(candidate.observation_id),
                    str(candidate.disposition_id),
                ),
            )
        ),
        master_signatures=master_signatures,
    )


def _capture_opening_reference_master_signatures(
    db: Session,
    *,
    task: FormalStocktakeTask,
    scope_pairs: Sequence[tuple[uuid.UUID, uuid.UUID]],
    material_ids: Sequence[uuid.UUID],
    account_ids: Sequence[uuid.UUID],
    serial_ids: Sequence[uuid.UUID],
) -> tuple[tuple[str, tuple[tuple[object, ...], ...]], ...]:
    """Capture every historical master row consumed by terminal re-proof.

    Terminal batches acquire the exact multi-task 0028 owner union before
    comparing these signatures.  The complete plain signature remains the
    before/after seal, preserves SQLite parity, and detects any coordinate
    expansion while the owner helper is being entered.  It must not be
    weakened when another online master-data writer is introduced.
    """

    checked_material_ids = tuple(sorted(set(material_ids), key=str))
    checked_account_ids = tuple(sorted(set(account_ids), key=str))
    checked_serial_ids = tuple(sorted(set(serial_ids), key=str))
    owner_ids = tuple(
        sorted(
            {task.region_org_id, *(owner_id for owner_id, _ in scope_pairs)},
            key=str,
        )
    )
    location_ids = tuple(
        sorted({location_id for _, location_id in scope_pairs}, key=str)
    )

    def rows(model: object, statement: object) -> tuple[tuple[object, ...], ...]:
        selected = tuple(
            db.scalars(
                _select_only_reference_statement(db, statement).execution_options(
                    populate_existing=True
                )
            ).all()
        )
        columns = tuple(model.__table__.columns)
        return tuple(
            tuple(getattr(row, column.key) for column in columns)
            for row in selected
        )

    accounts = rows(
        StockAccount,
        select(StockAccount)
        .where(StockAccount.id.in_(checked_account_ids))
        .order_by(StockAccount.id),
    ) if checked_account_ids else ()
    account_location_ids = set(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StockAccount.location_id)
                .where(StockAccount.id.in_(checked_account_ids))
                .order_by(StockAccount.id),
            )
        ).all()
    ) if checked_account_ids else set()
    all_location_ids = tuple(
        sorted(set(location_ids).union(account_location_ids), key=str)
    )
    cutoff_at = _as_optional_utc(task.cutoff_at)
    if cutoff_at is None:
        _evidence_invalid("期初任务缺少有效截止时点")

    signatures: tuple[
        tuple[str, tuple[tuple[object, ...], ...]], ...
    ] = (
        (
            "organizations",
            rows(
                Organization,
                select(Organization)
                .where(Organization.id.in_(owner_ids))
                .order_by(Organization.id),
            ),
        ),
        (
            "stock_locations",
            rows(
                StockLocation,
                select(StockLocation)
                .where(StockLocation.id.in_(all_location_ids))
                .order_by(StockLocation.id),
            ) if all_location_ids else (),
        ),
        (
            "custody_assignments",
            rows(
                CustodyAssignment,
                select(CustodyAssignment)
                .where(
                    CustodyAssignment.location_id.in_(all_location_ids),
                    CustodyAssignment.valid_from <= cutoff_at,
                    or_(
                        CustodyAssignment.valid_to.is_(None),
                        CustodyAssignment.valid_to > cutoff_at,
                    ),
                )
                .order_by(CustodyAssignment.location_id, CustodyAssignment.id),
            ) if all_location_ids else (),
        ),
        ("stock_accounts", accounts),
        (
            "materials",
            rows(
                FormalMaterial,
                select(FormalMaterial)
                .where(FormalMaterial.id.in_(checked_material_ids))
                .order_by(FormalMaterial.id),
            ) if checked_material_ids else (),
        ),
        (
            "material_inventory_policies",
            rows(
                MaterialInventoryPolicy,
                select(MaterialInventoryPolicy)
                .where(
                    MaterialInventoryPolicy.material_id.in_(checked_material_ids),
                    MaterialInventoryPolicy.effective_from <= cutoff_at,
                    or_(
                        MaterialInventoryPolicy.effective_to.is_(None),
                        MaterialInventoryPolicy.effective_to > cutoff_at,
                    ),
                )
                .order_by(
                    MaterialInventoryPolicy.material_id,
                    MaterialInventoryPolicy.effective_from,
                    MaterialInventoryPolicy.id,
                ),
            ) if checked_material_ids else (),
        ),
        (
            "inventory_lots",
            rows(
                InventoryLot,
                select(InventoryLot)
                .where(InventoryLot.material_id.in_(checked_material_ids))
                .order_by(InventoryLot.material_id, InventoryLot.id),
            ) if checked_material_ids else (),
        ),
        (
            "material_qr_codes",
            rows(
                QrCode,
                select(QrCode)
                .where(
                    QrCode.object_type == "material",
                    QrCode.object_id.in_(checked_material_ids),
                )
                .order_by(QrCode.object_id, QrCode.id),
            ) if checked_material_ids else (),
        ),
        (
            "inventory_serials",
            rows(
                InventorySerial,
                select(InventorySerial)
                .where(InventorySerial.id.in_(checked_serial_ids))
                .order_by(InventorySerial.id),
            ) if checked_serial_ids else (),
        ),
        (
            "serial_current_positions",
            rows(
                SerialCurrentPosition,
                select(SerialCurrentPosition)
                .where(SerialCurrentPosition.serial_id.in_(checked_serial_ids))
                .order_by(SerialCurrentPosition.serial_id),
            ) if checked_serial_ids else (),
        ),
        (
            "serial_qr_codes",
            rows(
                QrCode,
                select(QrCode)
                .where(
                    QrCode.object_type == "serial",
                    QrCode.object_id.in_(checked_serial_ids),
                )
                .order_by(QrCode.object_id, QrCode.id),
            ) if checked_serial_ids else (),
        ),
    )
    return signatures


def _observation_resolution_signature(
    row: StocktakeCountObservation,
) -> tuple[object, ...]:
    return (
        row.id,
        row.task_id,
        row.round_id,
        row.scope_id,
        row.observation_no,
        row.owner_org_id,
        row.location_id,
        row.custodian_person_id_snapshot,
        row.material_id,
        row.material_identifier_raw,
        row.material_identifier_type,
        row.condition_code,
        row.availability_bucket,
        row.lot_id,
        row.lot_no_raw,
        row.serial_id,
        row.serial_no_raw,
        row.serial_identifier_type,
        row.counted_qty,
        row.verification_status,
        row.dimension_sha256,
        row.request_sha256,
        row.idempotency_key_hash,
    )


def _disposition_resolution_signature(
    row: StocktakeObservationDisposition,
) -> tuple[object, ...]:
    return (
        row.id,
        row.task_id,
        row.round_id,
        row.scope_id,
        row.observation_id,
        row.disposition,
        row.resolved_material_id,
        row.resolved_lot_id,
        row.resolved_serial_id,
        row.reason_code,
        row.comment,
        row.disposition_manifest_sha256,
        row.request_sha256,
        row.idempotency_key_hash,
        row.decided_by_user_id,
        row.decided_by_person_id,
        row.decided_role_assignment_id,
        row.authorization_version,
        row.role_code,
        row.scope_type,
        row.scope_id_snapshot,
        row.authorization_sha256,
        row.decided_at,
    )


def _policy_resolution_signature(row: object) -> tuple[object, ...]:
    return (
        row.id,
        row.material_id,
        row.tracking_mode,
        row.quantity_scale,
        row.allow_fraction,
        row.effective_from,
        row.effective_to,
    )


def _read_resolution_account_rows(
    db: Session,
    *,
    task: FormalStocktakeTask,
    observation: StocktakeCountObservation,
    material_id: uuid.UUID,
    lot_id: uuid.UUID | None,
    populate_existing: bool,
) -> tuple[tuple[StockAccount, ...], tuple[StockAccount, ...]]:
    """Plainly read the no-cutoff account proof; owner helpers run elsewhere."""

    dimension = (
        StockAccount.owner_org_id == observation.owner_org_id,
        StockAccount.location_id == observation.location_id,
        StockAccount.custodian_person_id
        == observation.custodian_person_id_snapshot,
        StockAccount.material_id == material_id,
        StockAccount.condition_code == observation.condition_code,
        StockAccount.availability_bucket == observation.availability_bucket,
        StockAccount.lot_id == lot_id,
    )
    cutoff_statement = (
        select(StockAccount)
        .where(*dimension, StockAccount.created_at <= task.cutoff_at)
        .order_by(StockAccount.id)
    )
    snapshot_statement = (
        select(StockAccount)
        .join(
            StocktakeSnapshotLine,
            StocktakeSnapshotLine.stock_account_id == StockAccount.id,
        )
        .where(
            StocktakeSnapshotLine.task_id == task.id,
            StocktakeSnapshotLine.scope_id == observation.scope_id,
            *dimension,
        )
        .order_by(StockAccount.id)
    )
    if populate_existing:
        cutoff_statement = cutoff_statement.execution_options(
            populate_existing=True
        )
        snapshot_statement = snapshot_statement.execution_options(
            populate_existing=True
        )
    return (
        tuple(db.scalars(cutoff_statement).all()),
        tuple(db.scalars(snapshot_statement).all()),
    )


def _reread_inventory_accounts(
    db: Session,
    *,
    actor: FormalPrincipal,
    evidence: _ApprovedEvidence,
) -> _ApprovedEvidence:
    """Reread every old/new account after the inventory owner helper locks it."""

    account_ids = tuple(sorted(evidence.accounts, key=str))
    if not account_ids:
        return evidence
    account_statement = (
        select(StockAccount)
        .where(StockAccount.id.in_(account_ids))
        .order_by(StockAccount.id)
    )
    refreshed_rows = tuple(
        db.scalars(
            _select_only_reference_statement(
                db, account_statement
            ).execution_options(populate_existing=True)
        ).all()
    )
    refreshed = {row.id: row for row in refreshed_rows}
    if len(refreshed) != len(account_ids) or set(refreshed) != set(account_ids):
        _evidence_invalid("期初过账账户在引用锁定后缺失或重复")
    authorized = posting_service._authorize_account_ids(
        db,
        actor,
        account_ids,
        action="post_opening",
        resource="stocktake",
        lock_rows=True,
    )
    if set(authorized) != set(refreshed):
        _evidence_invalid("期初过账账户授权重读集合不一致")
    posting_service._require_active_account_masters(db, authorized)
    try:
        observation_accounts = {
            observation_id: authorized[account.id]
            for observation_id, account in evidence.observation_accounts.items()
        }
    except KeyError as exc:
        _evidence_invalid("期初观察账户不在锁定账户集合内", cause=exc)
    return replace(
        evidence,
        accounts=authorized,
        observation_accounts=observation_accounts,
    )


def _opening_inventory_account_ids(
    db: Session,
    evidence: _ApprovedEvidence,
    *,
    reference_plan: _OpeningReferencePlan,
) -> tuple[uuid.UUID, ...]:
    """Return the exact prelocked accounts plus locally materialized accounts."""

    scope_pairs = tuple(
        (row.owner_org_id, row.location_id) for row in evidence.scopes
    )
    if not scope_pairs:
        _evidence_invalid("期初过账缺少账户范围坐标")
    statement = (
        select(StockAccount.id)
        .where(
            or_(
                *(
                    (StockAccount.owner_org_id == owner_org_id)
                    & (StockAccount.location_id == location_id)
                    for owner_org_id, location_id in scope_pairs
                )
            )
        )
        .order_by(StockAccount.id)
    )
    account_ids = set(
        db.scalars(_select_only_reference_statement(db, statement)).all()
    )
    expected_account_ids = set(reference_plan.account_ids)
    expected_account_ids.update(evidence.accounts)
    if account_ids != expected_account_ids:
        _evidence_invalid("期初账户候选集合在 start 锁定前后发生扩展或收缩")
    return tuple(sorted(account_ids, key=str))


def _lock_review_disposition_resolutions(
    db: Session,
    evidence: _ApprovedEvidence,
    *,
    reference_plan: _OpeningReferencePlan,
    locked_account_ids: Sequence[uuid.UUID],
    locked_serial_ids: Sequence[uuid.UUID],
) -> _ApprovedEvidence:
    resolutions = _prove_reference_plan_disposition_resolutions(
        db,
        task=evidence.task,
        round_id=evidence.round_row.id,
        reference_plan=reference_plan,
        locked_account_ids=locked_account_ids,
        locked_serial_ids=locked_serial_ids,
    )
    return replace(evidence, disposition_resolutions=resolutions)


def _prove_reference_plan_disposition_resolutions(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_id: uuid.UUID,
    reference_plan: _OpeningReferencePlan,
    locked_account_ids: Sequence[uuid.UUID],
    locked_serial_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, object]:
    """Plainly re-read and prove the frozen disposition candidate graph.

    This function intentionally imports no review owner graph and invokes no
    PostgreSQL owner helper.  Any master/QR/lot/SN/account phantom that would
    expand the coordinates captured before ``start_reference`` fails closed.
    """

    if _capture_opening_reference_master_signatures(
        db,
        task=task,
        scope_pairs=reference_plan.scope_pairs,
        material_ids=reference_plan.material_ids,
        account_ids=reference_plan.account_ids,
        serial_ids=reference_plan.serial_ids,
    ) != reference_plan.master_signatures:
        _evidence_invalid("期初历史主数据签名在引用图重证前后发生变化")

    from . import opening_observation_disposition as disposition_service
    from .opening_observation_disposition import (
        _LockedResolutionReferences,
        _Resolution,
        _load_cutoff_policy,
        _prove_lot,
        _prove_material,
        _prove_serial_from_locked,
        _read_resolution_lots,
        _read_resolution_material_rows,
        _read_resolution_serial_rows,
        _validate_policy_binding,
    )

    observation_rows = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.round_id == round_id,
            )
            .order_by(
                StocktakeCountObservation.scope_id,
                StocktakeCountObservation.observation_no,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if tuple(
        _observation_resolution_signature(row) for row in observation_rows
    ) != reference_plan.observation_signatures:
        _evidence_invalid("期初观察候选集合在引用锁定期间发生变化")
    disposition_rows = tuple(
        db.scalars(
            select(StocktakeObservationDisposition)
            .where(
                StocktakeObservationDisposition.task_id == task.id,
                StocktakeObservationDisposition.round_id == round_id,
            )
            .order_by(
                StocktakeObservationDisposition.observation_id,
                StocktakeObservationDisposition.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if tuple(
        _disposition_resolution_signature(row) for row in disposition_rows
    ) != tuple(
        candidate.disposition_signature
        for candidate in reference_plan.disposition_candidates
    ):
        _evidence_invalid("期初观察处置候选集合在引用锁定期间发生变化")

    observations = {row.id: row for row in observation_rows}
    dispositions = {row.id: row for row in disposition_rows}
    locked_accounts = set(locked_account_ids)
    locked_serials = set(locked_serial_ids)
    resolutions: dict[uuid.UUID, object] = {}
    try:
        for candidate in reference_plan.disposition_candidates:
            observation = observations.get(candidate.observation_id)
            disposition = dispositions.get(candidate.disposition_id)
            if (
                observation is None
                or disposition is None
                or disposition.observation_id != observation.id
                or _observation_resolution_signature(observation)
                != candidate.observation_signature
            ):
                _evidence_invalid("期初观察处置主体在引用锁定期间发生变化")
            command = (
                disposition_service.RecordOpeningObservationDispositionCommand(
                    task_id=task.id,
                    round_id=round_id,
                    observation_id=observation.id,
                    disposition=disposition.disposition,
                    reason_code=disposition.reason_code,
                    comment=disposition.comment,
                    resolved_material_id=disposition.resolved_material_id,
                    resolved_lot_id=disposition.resolved_lot_id,
                    resolved_serial_id=disposition.resolved_serial_id,
                )
            )
            if command != candidate.command:
                _evidence_invalid("期初观察处置命令在引用锁定期间发生变化")

            materials, mappings = _read_resolution_material_rows(
                db,
                observation,
                populate_existing=True,
            )
            if (
                tuple(row.id for row in materials) != candidate.material_ids
                or tuple((row.id, row.object_id) for row in mappings)
                != candidate.material_mapping_signature
                or not {
                    *(row.id for row in materials),
                    *(row.object_id for row in mappings),
                }.issubset(reference_plan.material_ids)
            ):
                _evidence_invalid("期初观察物料或物料二维码候选集合发生扩展")
            lots = _read_resolution_lots(
                db,
                material_ids=candidate.material_ids,
                lot_no=observation.lot_no_raw,
                populate_existing=True,
            )
            if tuple(
                (row.id, row.material_id) for row in lots
            ) != candidate.lot_signature:
                _evidence_invalid("期初观察批次候选集合发生扩展")

            serial_rows_by_material: dict[
                uuid.UUID, tuple[tuple[object, ...], tuple[object, ...]]
            ] = {}
            for material in materials:
                serials, serial_mappings = _read_resolution_serial_rows(
                    db,
                    observation=observation,
                    material=material,
                    populate_existing=True,
                )
                serial_rows_by_material[material.id] = (serials, serial_mappings)
            current_serial_signatures = tuple(
                (
                    material_id,
                    tuple(row.id for row in serials),
                    tuple((row.id, row.object_id) for row in serial_mappings),
                )
                for material_id, (serials, serial_mappings) in sorted(
                    serial_rows_by_material.items(), key=lambda item: str(item[0])
                )
            )
            if current_serial_signatures != candidate.serial_signatures:
                _evidence_invalid("期初观察 SN 或 SN 二维码候选集合发生扩展")
            current_serial_ids = {
                identifier
                for _material_id, serial_ids, mapping_signature
                in current_serial_signatures
                for identifier in (
                    *serial_ids,
                    *(object_id for _mapping_id, object_id in mapping_signature),
                )
            }
            if not current_serial_ids.issubset(locked_serials):
                _evidence_invalid("期初观察 SN 候选不在预锁坐标内")

            if command.disposition != "resolved_existing_master":
                if any(
                    value is not None
                    for value in (
                        command.resolved_material_id,
                        command.resolved_lot_id,
                        command.resolved_serial_id,
                    )
                ):
                    _evidence_invalid("未解析观察处置携带了主数据绑定")
                resolutions[observation.id] = _Resolution(None, None, None)
                continue

            locked_references = _LockedResolutionReferences(
                materials=materials,
                material_mappings=mappings,
                lots=lots,
            )
            material = _prove_material(observation, locked_references)
            lot = _prove_lot(observation, material, locked_references)
            policy = _load_cutoff_policy(
                db,
                material.id,
                task.cutoff_at,
            )
            if _policy_resolution_signature(policy) != candidate.policy_signature:
                _evidence_invalid("期初观察追踪策略在引用锁定期间发生变化")
            cutoff_accounts, snapshot_accounts = _read_resolution_account_rows(
                db,
                task=task,
                observation=observation,
                material_id=material.id,
                lot_id=lot.id if lot is not None else None,
                populate_existing=True,
            )
            if (
                tuple(row.id for row in cutoff_accounts)
                != candidate.cutoff_account_ids
                or tuple(row.id for row in snapshot_accounts)
                != candidate.snapshot_account_ids
                or not {
                    *(row.id for row in cutoff_accounts),
                    *(row.id for row in snapshot_accounts),
                }.issubset(locked_accounts)
            ):
                _evidence_invalid("期初观察截止账户候选集合发生扩展")
            if cutoff_accounts or snapshot_accounts:
                _evidence_invalid("期初观察维度已存在截止账户或快照账户")
            serials, serial_mappings = serial_rows_by_material.get(
                material.id,
                ((), ()),
            )
            serial = _prove_serial_from_locked(
                observation,
                material,
                lot,
                serials=serials,
                mappings=serial_mappings,
            )
            if (
                command.resolved_material_id != material.id
                or command.resolved_lot_id
                != (lot.id if lot is not None else None)
                or command.resolved_serial_id
                != (serial.id if serial is not None else None)
            ):
                _evidence_invalid("期初观察处置解析结果与不可变事实不一致")
            _validate_policy_binding(observation, policy, lot, serial)
            resolutions[observation.id] = _Resolution(
                material_id=material.id,
                lot_id=lot.id if lot is not None else None,
                serial_id=serial.id if serial is not None else None,
            )
    except disposition_service.OpeningObservationDispositionError as exc:
        _evidence_invalid("期初观察处置主数据引用无法安全重证", cause=exc)
    return resolutions


def _opening_serial_ids(evidence: _ApprovedEvidence) -> tuple[uuid.UUID, ...]:
    """Return every current-round count/observation serial coordinate."""

    serial_ids = {
        serial_id
        for values in evidence.count_serials_by_line.values()
        for serial_id in values
    }
    serial_ids.update(
        row.serial_id for row in evidence.observations if row.serial_id is not None
    )
    return tuple(sorted(serial_ids, key=str))


def _lock_and_validate_approved_evidence(
    db: Session,
    task: FormalStocktakeTask,
    *,
    actor: FormalPrincipal,
    disposition_resolutions: Mapping[uuid.UUID, object] | None = None,
) -> _ApprovedEvidence:
    scope_statement = (
        select(FormalStocktakeScope)
        .where(FormalStocktakeScope.task_id == task.id)
        .order_by(FormalStocktakeScope.scope_no)
    )
    scopes = tuple(
        db.scalars(
            _select_only_reference_statement(db, scope_statement).execution_options(
                populate_existing=True
            )
        ).all()
    )
    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id == task.id)
            .order_by(InventoryFreeze.stocktake_scope_id)
            .with_for_update(of=InventoryFreeze)
            .execution_options(populate_existing=True)
        ).all()
    )
    rounds = tuple(
        db.scalars(
            select(StocktakeRound)
            .where(StocktakeRound.task_id == task.id)
            .order_by(StocktakeRound.round_no)
            .with_for_update(of=StocktakeRound)
            .execution_options(populate_existing=True)
        ).all()
    )
    if not rounds:
        _evidence_invalid("期初盘点轮次缺失")
    round_row = rounds[-1]
    # Lock the evidence rows that are part of finalization.  The rows are
    # append-only by contract; row locks also make the SQLite tests mirror the
    # production transaction boundary as closely as possible.
    for model in (
        StocktakeSnapshotLine,
        StocktakeCountLine,
        StocktakeCountObservation,
        StocktakeCountSerial,
        StocktakeControlSnapshotLine,
        StocktakeDifference,
        StocktakeReview,
        StocktakeReviewItem,
    ):
        condition = (
            model.task_id == task.id
            if hasattr(model, "task_id")
            else model.round_id == round_row.id
        )
        if hasattr(model, "task_id") and hasattr(model, "round_id"):
            condition = condition & (model.round_id == round_row.id)
        tuple(
            db.scalars(
                _select_only_reference_statement(
                    db,
                    select(model).where(condition),
                )
            ).all()
        )

    scope_by_id = {row.id: row for row in scopes}
    freeze_by_scope = {row.stocktake_scope_id: row for row in freezes}
    cutoff_at = _as_optional_utc(task.cutoff_at)
    if (
        task.task_type != "opening"
        or task.status != "approved"
        or task.posted_at is not None
        or task.closed_at is not None
        or task.cancelled_at is not None
        or task.cutoff_ledger_cursor is None
        or task.cutoff_ledger_cursor < 0
        or cutoff_at is None
        or not scopes
        or [row.scope_no for row in scopes] != list(range(1, len(scopes) + 1))
        or len(scope_by_id) != len(scopes)
        or len(freeze_by_scope) != len(freezes)
        or set(freeze_by_scope) != set(scope_by_id)
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
            or freeze.released_by_user_id is not None
            or freeze.scope_key != scope_by_id[freeze.stocktake_scope_id].scope_key
            or _as_optional_utc(freeze.valid_from) != cutoff_at
            for freeze in freezes
        )
    ):
        _evidence_invalid("期初任务状态、范围或冻结事实不完整")

    if (
        [row.round_no for row in rounds]
        != list(range(1, task.current_round_no + 1))
        or any(row.status != "submitted" for row in rounds)
        or round_row.round_no != task.current_round_no
        or round_row.round_type
        != ("initial" if round_row.round_no == 1 else "recount")
        or (round_row.round_no == 1 and round_row.recount_case_id is not None)
        or (round_row.round_no > 1 and round_row.recount_case_id is None)
    ):
        _evidence_invalid("期初最终轮次状态或连续性无效")
    if round_row.round_no == 1:
        expected_assignees = {
            scope.id: scope.assignee_user_id for scope in scopes
        }
    else:
        assignment_statement = (
            select(StocktakeRecountScopeAssignment)
            .where(
                StocktakeRecountScopeAssignment.task_id == task.id,
                StocktakeRecountScopeAssignment.recount_case_id
                == round_row.recount_case_id,
            )
            .order_by(StocktakeRecountScopeAssignment.scope_id)
        )
        assignments = tuple(
            db.scalars(
                _select_only_reference_statement(db, assignment_statement)
            ).all()
        )
        expected_assignees = {
            row.scope_id: row.assignee_user_id for row in assignments
        }
        if (
            len(assignments) != len(expected_assignees)
            or set(expected_assignees) != set(scope_by_id)
        ):
            _evidence_invalid("期初复盘最终轮次的逐范围指派缺失")

    snapshot_lines = tuple(
        db.scalars(
            select(StocktakeSnapshotLine)
            .where(StocktakeSnapshotLine.task_id == task.id)
            .order_by(
                StocktakeSnapshotLine.scope_id,
                StocktakeSnapshotLine.stock_account_id,
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
            .order_by(
                StocktakeCountLine.scope_id,
                StocktakeCountLine.stock_account_id,
            )
        ).all()
    )
    count_serials = tuple(
        db.scalars(
            select(StocktakeCountSerial)
            .where(StocktakeCountSerial.round_id == round_row.id)
            .order_by(
                StocktakeCountSerial.count_line_id,
                StocktakeCountSerial.serial_id,
            )
        ).all()
    )
    observation_statement = (
        select(StocktakeCountObservation)
        .where(
            StocktakeCountObservation.task_id == task.id,
            StocktakeCountObservation.round_id == round_row.id,
        )
        .order_by(
            StocktakeCountObservation.scope_id,
            StocktakeCountObservation.observation_no,
        )
    )
    observations = tuple(
        db.scalars(
            _select_only_reference_statement(
                db, observation_statement
            ).execution_options(populate_existing=True)
        ).all()
    )
    control_lines = tuple(
        db.scalars(
            select(StocktakeControlSnapshotLine)
            .where(StocktakeControlSnapshotLine.task_id == task.id)
            .order_by(StocktakeControlSnapshotLine.line_no)
        ).all()
    )
    account_id_rows = tuple(
        db.scalars(
            select(StockAccount.id)
            .where(
                or_(
                    *(
                        (StockAccount.owner_org_id == scope.owner_org_id)
                        & (StockAccount.location_id == scope.location_id)
                        for scope in scopes
                    )
                ),
                StockAccount.created_at <= task.cutoff_at,
            )
            .order_by(StockAccount.id)
        ).all()
    )
    if account_id_rows:
        locked_accounts = posting_service._authorize_account_ids(
            db,
            actor,
            account_id_rows,
            action="post_opening",
            resource="stocktake",
            lock_rows=True,
        )
        posting_service._require_active_account_masters(db, locked_accounts)
    posting_service._validate_opening_manifests(
        db,
        task=task,
        scopes=scopes,
        round_row=round_row,
        snapshot_lines=snapshot_lines,
        count_lines=count_lines,
        count_serials=count_serials,
        control_lines=control_lines,
    )
    accounts, serials_by_line = posting_service._validate_opening_count_evidence(
        db,
        task=task,
        scopes=scopes,
        scope_by_id=scope_by_id,
        round_row=round_row,
        snapshot_lines=snapshot_lines,
        count_lines=count_lines,
        count_serials=count_serials,
        expected_assignee_by_scope=expected_assignees,
    )
    posting_service._validate_opening_control_source(db, task, control_lines)
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
    posting_service._validate_opening_difference_set(
        count_lines=count_lines,
        accounts=accounts,
        control_lines=control_lines,
        differences=differences,
        observations=observations,
    )
    observation_differences = {
        row.observed_line_id: row
        for row in differences
        if row.observed_line_id is not None
    }
    if len(observation_differences) != len(observations):
        _evidence_invalid("期初现场观察与差异未形成一一对应")
    observation_accounts = _bind_observation_accounts(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        observations=observations,
        require_all=False,
    )
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
    regional = next((row for row in reviews if row.review_stage == "region"), None)
    headquarters = next(
        (row for row in reviews if row.review_stage == "headquarters"), None
    )
    if (
        len(reviews) != 2
        or regional is None
        or headquarters is None
        or regional.decision != "approve"
        or headquarters.decision != "approve"
        or regional.reviewer_user_id == headquarters.reviewer_user_id
        or regional.reviewer_person_id == headquarters.reviewer_person_id
        or _as_utc(regional.reviewed_at) >= _as_utc(headquarters.reviewed_at)
    ):
        _evidence_invalid("区域与总部两级通过事实不完整或未职责分离")
    review_items = tuple(
        db.scalars(
            select(StocktakeReviewItem)
            .where(StocktakeReviewItem.review_id.in_((regional.id, headquarters.id)))
            .order_by(StocktakeReviewItem.review_id, StocktakeReviewItem.difference_id)
        ).all()
    )
    expected_ids = {row.id for row in differences}
    decisions: dict[uuid.UUID, dict[uuid.UUID, str]] = {
        regional.id: {},
        headquarters.id: {},
    }
    for item in review_items:
        decisions.setdefault(item.review_id, {})[item.difference_id] = item.decision
    if any(set(values) != expected_ids for values in decisions.values()):
        _evidence_invalid("两级逐项复核未覆盖完整差异集")
    for difference in differences:
        regional_decision = decisions[regional.id][difference.id]
        headquarters_decision = decisions[headquarters.id][difference.id]
        expected_decision = (
            "pending_verification"
            if difference.difference_type == "control_unassigned"
            else "accept_for_posting"
        )
        if regional_decision != expected_decision or headquarters_decision != expected_decision:
            _evidence_invalid("控制差异未保持待核实，或实盘差异未被两级一致接受")

    all_accounts = {
        **accounts,
        **{row.id: row for row in observation_accounts.values()},
    }

    return _ApprovedEvidence(
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        count_lines=count_lines,
        count_serials_by_line=serials_by_line,
        accounts=all_accounts,
        observations=observations,
        observation_differences=observation_differences,
        observation_accounts=observation_accounts,
        disposition_resolutions=dict(disposition_resolutions or {}),
        regional_review=regional,
        headquarters_review=headquarters,
        pending_control_difference_count=sum(
            1 for row in differences if row.difference_type == "control_unassigned"
        ),
    )


def _validate_locked_approved_evidence(
    db: Session,
    evidence: _ApprovedEvidence,
    *,
    actor: FormalPrincipal,
    audit_proof: object,
) -> _ApprovedEvidence:
    """Recompute evidence after ledger/account/audit locks are all held."""

    refreshed = _lock_and_validate_approved_evidence(
        db,
        evidence.task,
        actor=actor,
        disposition_resolutions=evidence.disposition_resolutions,
    )
    if evidence.audit_replay_plan is None:
        _evidence_invalid("期初终结缺少审计前封存的盘点证据计划")
    try:
        posting_service._validate_opening_task_audit_replay_from_prelocked_graph(
            db,
            plan=evidence.audit_replay_plan,
            audit_proof=audit_proof,
        )
    except posting_service.InventoryPostingError as exc:
        _evidence_invalid("期初盘点审计证据无法从封存计划重证", cause=exc)
    if (
        refreshed.round_row.id != evidence.round_row.id
        or tuple(row.id for row in refreshed.scopes)
        != tuple(row.id for row in evidence.scopes)
        or tuple(row.id for row in refreshed.count_lines)
        != tuple(row.id for row in evidence.count_lines)
        or tuple(row.id for row in refreshed.observations)
        != tuple(row.id for row in evidence.observations)
        or {
            observation_id: difference.id
            for observation_id, difference in refreshed.observation_differences.items()
        }
        != {
            observation_id: difference.id
            for observation_id, difference in evidence.observation_differences.items()
        }
        or {
            observation_id: account.id
            for observation_id, account in refreshed.observation_accounts.items()
        }
        != {
            observation_id: account.id
            for observation_id, account in evidence.observation_accounts.items()
        }
        or set(refreshed.disposition_resolutions)
        != set(evidence.disposition_resolutions)
        or refreshed.regional_review.id != evidence.regional_review.id
        or refreshed.headquarters_review.id != evidence.headquarters_review.id
    ):
        _evidence_invalid("期初证据在终结锁定期间发生变化")
    return refreshed


def _bind_observation_accounts(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    observations: Sequence[StocktakeCountObservation],
    require_all: bool,
) -> dict[uuid.UUID, StockAccount]:
    if not observations:
        return {}
    cutoff_at = _as_optional_utc(task.cutoff_at)
    scope_by_id = {row.id: row for row in scopes}
    if (
        cutoff_at is None
        or round_row.task_id != task.id
        or round_row.round_no <= 1
        or round_row.round_no != task.current_round_no
        or round_row.round_type != "recount"
        or round_row.status != "submitted"
    ):
        _evidence_invalid("只有已完整复盘的当前轮次观察可以建立期初账户")
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
            _evidence_invalid("复盘现场观察未唯一解析或超出冻结范围")
        account_statement = (
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
        )
        rows = tuple(
            db.scalars(
                _select_only_reference_statement(
                    db, account_statement
                ).execution_options(populate_existing=True)
            ).all()
        )
        if len(rows) > 1:
            _evidence_invalid("复盘现场观察对应多个库存账户")
        if not rows:
            if require_all:
                _evidence_invalid("已批准复盘观察缺少原子建立的库存账户")
            continue
        account = rows[0]
        created_at = _as_optional_utc(account.created_at)
        if (
            created_at is None
            or created_at <= cutoff_at
        ):
            _evidence_invalid("复盘观察账户不是截止后建立的精确维度")
        result[observation.id] = account
    if require_all and set(result) != {row.id for row in observations}:
        _evidence_invalid("复盘观察账户集合不完整")
    if result:
        posting_service._require_active_account_masters(
            db,
            {row.id: row for row in result.values()},
        )
    return result


def _materialize_observation_accounts(
    db: Session,
    evidence: _ApprovedEvidence,
    *,
    created_at: datetime,
) -> _ApprovedEvidence:
    if not evidence.observations:
        return evidence
    created_at = _as_utc(created_at)
    cutoff_at = _as_utc(evidence.task.cutoff_at)
    if created_at <= cutoff_at:
        _evidence_invalid("期初观察账户建立时间不得早于截止时点")
    _take_advisory_locks(
        db,
        tuple(
            _advisory_coordinate(
                "opening-observation-account-dimension",
                _observation_account_dimension_lock_value(observation),
            )
            for observation in evidence.observations
        ),
    )
    observations_by_dimension: dict[
        tuple[uuid.UUID, uuid.UUID | None, uuid.UUID, uuid.UUID, str, str, uuid.UUID | None],
        list[StocktakeCountObservation],
    ] = {}
    for observation in evidence.observations:
        observations_by_dimension.setdefault(
            _observation_account_dimension_key(observation), []
        ).append(observation)

    existing_by_dimension: dict[
        tuple[uuid.UUID, uuid.UUID | None, uuid.UUID, uuid.UUID, str, str, uuid.UUID | None],
        StockAccount,
    ] = {}
    observations_by_id = {row.id: row for row in evidence.observations}
    for observation_id, account in evidence.observation_accounts.items():
        observation = observations_by_id.get(observation_id)
        if observation is None:
            _evidence_invalid("复盘观察账户映射包含未知观察")
        dimension = _observation_account_dimension_key(observation)
        if _stock_account_dimension_key(account) != dimension:
            _evidence_invalid("复盘观察账户维度与现场证据不一致")
        previous = existing_by_dimension.get(dimension)
        if previous is not None and previous.id != account.id:
            _evidence_invalid("同一正式库存维度对应多个库存账户")
        existing_by_dimension[dimension] = account

    for dimension, dimension_observations in sorted(
        observations_by_dimension.items(), key=lambda item: repr(item[0])
    ):
        account = existing_by_dimension.get(dimension)
        if account is None:
            exemplar = dimension_observations[0]
            account = StockAccount(
                id=uuid.uuid4(),
                owner_org_id=exemplar.owner_org_id,
                custodian_person_id=exemplar.custodian_person_id_snapshot,
                location_id=exemplar.location_id,
                material_id=exemplar.material_id,
                condition_code=exemplar.condition_code,
                availability_bucket=exemplar.availability_bucket,
                lot_id=exemplar.lot_id,
                created_at=created_at,
                updated_at=created_at,
            )
            db.add(account)
            db.flush()
        for observation in dimension_observations:
            _require_pristine_observation_account(
                db, evidence.task, observation, account
            )
    bound = _bind_observation_accounts(
        db,
        task=evidence.task,
        round_row=evidence.round_row,
        scopes=evidence.scopes,
        observations=evidence.observations,
        require_all=True,
    )
    return replace(
        evidence,
        accounts={
            **evidence.accounts,
            **{row.id: row for row in bound.values()},
        },
        observation_accounts=bound,
    )


def _observation_account_dimension_key(
    observation: StocktakeCountObservation,
) -> tuple[
    uuid.UUID,
    uuid.UUID | None,
    uuid.UUID,
    uuid.UUID,
    str,
    str,
    uuid.UUID | None,
]:
    if observation.material_id is None:
        _evidence_invalid("已批准复盘观察缺少唯一物料主数据")
    return (
        observation.owner_org_id,
        observation.custodian_person_id_snapshot,
        observation.location_id,
        observation.material_id,
        observation.condition_code,
        observation.availability_bucket,
        observation.lot_id,
    )


def _stock_account_dimension_key(
    account: StockAccount,
) -> tuple[
    uuid.UUID,
    uuid.UUID | None,
    uuid.UUID,
    uuid.UUID,
    str,
    str,
    uuid.UUID | None,
]:
    return (
        account.owner_org_id,
        account.custodian_person_id,
        account.location_id,
        account.material_id,
        account.condition_code,
        account.availability_bucket,
        account.lot_id,
    )


def _observation_account_dimension_lock_value(
    observation: StocktakeCountObservation,
) -> str:
    return json.dumps(
        [
            str(value) if isinstance(value, uuid.UUID) else value
            for value in _observation_account_dimension_key(observation)
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    )


def _require_pristine_observation_account(
    db: Session,
    task: FormalStocktakeTask,
    observation: StocktakeCountObservation,
    account: StockAccount,
) -> None:
    movement_exists = db.scalar(
        select(InventoryMovement.id)
        .where(
            or_(
                InventoryMovement.from_account_id == account.id,
                InventoryMovement.to_account_id == account.id,
            )
        )
        .limit(1)
    )
    balance = db.get(StockBalance, account.id)
    serial_position = (
        db.get(SerialCurrentPosition, observation.serial_id)
        if observation.serial_id is not None
        else None
    )
    if (
        movement_exists is not None
        or serial_position is not None
        or (
            balance is not None
            and (
                balance.quantity != _ZERO
                or balance.version != 0
                or balance.ledger_cursor > (task.cutoff_ledger_cursor or 0)
            )
        )
    ):
        _evidence_invalid("复盘观察账户或 SN 已有库存事实，禁止重复建立期初")


def _opening_posting_sources(
    evidence: _ApprovedEvidence,
) -> tuple[_PostingSource, ...]:
    sources = [
        _PostingSource(
            account_id=row.stock_account_id,
            quantity=row.counted_qty,
            serial_ids=tuple(
                sorted(evidence.count_serials_by_line.get(row.id, set()), key=str)
            ),
            count_line_id=row.id,
            difference_id=None,
        )
        for row in evidence.count_lines
        if row.counted_qty > _ZERO
    ]
    for observation in evidence.observations:
        account = evidence.observation_accounts.get(observation.id)
        difference = evidence.observation_differences.get(observation.id)
        if account is None or difference is None:
            _evidence_invalid("复盘观察缺少期初账户或差异来源绑定")
        sources.append(
            _PostingSource(
                account_id=account.id,
                quantity=observation.counted_qty,
                serial_ids=(observation.serial_id,) if observation.serial_id else (),
                count_line_id=None,
                difference_id=difference.id,
            )
        )
    return tuple(sources)


def _opening_total_quantity(evidence: _ApprovedEvidence) -> Decimal:
    total = sum(
        (row.counted_qty for row in evidence.count_lines),
        start=_ZERO,
    ) + sum(
        (row.counted_qty for row in evidence.observations),
        start=_ZERO,
    )
    if (
        not isinstance(total, Decimal)
        or not total.is_finite()
        or total < _ZERO
        or total >= _MAX_QUANTITY
        or _decimal_scale(total) > 3
    ):
        _fail(
            "opening_finalize_aggregate_quantity_invalid",
            "precondition_failed",
            "期初过账实盘合计超出 Numeric(18,3) 安全范围",
        )
    return total


def _opening_inventory_command(
    evidence: _ApprovedEvidence,
) -> posting_service.InventoryPostingCommand:
    posting_sources = _opening_posting_sources(evidence)
    if not posting_sources:
        # The low-level posting primitive is never called for zero opening;
        # this shell exists only so common pre-lock code can stay uniform.
        return posting_service.InventoryPostingCommand(
            transaction_no=f"OPEN-{evidence.task.id.hex}",
            movement_type="opening",
            source_document_type="opening_stocktake",
            source_document_id=str(evidence.task.id),
            posting_key=f"opening-stocktake:{evidence.task.id}",
            effective_at=_as_utc(evidence.task.cutoff_at),
            movements=(),
        )
    return posting_service.InventoryPostingCommand(
        transaction_no=f"OPEN-{evidence.task.id.hex}",
        movement_type="opening",
        source_document_type="opening_stocktake",
        source_document_id=str(evidence.task.id),
        posting_key=f"opening-stocktake:{evidence.task.id}",
        effective_at=_as_utc(evidence.task.cutoff_at),
        movements=tuple(
            posting_service.InventoryMovementCommand(
                from_account_id=None,
                to_account_id=source.account_id,
                quantity=source.quantity,
                serial_ids=source.serial_ids,
                external_boundary_code="approved-opening-stocktake",
            )
            for source in posting_sources
        ),
    )


def _prelock_inventory_graph(
    db: Session,
    *,
    actor: FormalPrincipal,
    evidence: _ApprovedEvidence,
    posting_command: posting_service.InventoryPostingCommand,
    account_ids: tuple[uuid.UUID, ...],
    prelocked_reference_graph: object,
) -> object:
    evidence_account_ids = tuple(sorted(evidence.accounts, key=str))
    if evidence_account_ids:
        accounts = posting_service._authorize_account_ids(
            db,
            actor,
            evidence_account_ids,
            action="post_opening",
            resource="stocktake",
            lock_rows=True,
        )
        posting_service._require_active_account_masters(db, accounts)
        policies = posting_service._load_effective_policies(
            db,
            {row.material_id for row in accounts.values()},
            evidence.task.cutoff_at,
        )
        posting_service._validate_tracking_rules(posting_command, accounts, policies)
        posting_service._lock_and_validate_serials(
            db,
            command=posting_command,
            accounts=accounts,
            policies=policies,
            prelocked_reference_graph=prelocked_reference_graph,
        )
    # Every inventory writer locks the serial graph before mutable balances.
    # Create/lock the complete opening-scope projection set now; the low-level
    # opening primitive receives a transaction-bound proof and may only reenter
    # these rows, never create a new balance after this point.
    balances = {
        row.stock_account_id: row
        for row in db.scalars(
            select(StockBalance)
            .where(StockBalance.stock_account_id.in_(account_ids))
            .order_by(StockBalance.stock_account_id)
            .with_for_update(of=StockBalance)
            .execution_options(populate_existing=True)
        ).all()
    }
    posting_account_ids = posting_service._command_account_ids(posting_command)
    if posting_account_ids:
        balances.update(
            posting_service._lock_or_create_balances(db, posting_account_ids)
        )
    if balances:
        posting_service._validate_locked_balance_projections(
            db,
            account_ids=tuple(sorted(balances, key=str)),
            balances=balances,
        )
    for account_id in account_ids:
        balance = balances.get(account_id)
        if balance is not None and (
            balance.quantity != _ZERO
            or balance.ledger_cursor > (evidence.task.cutoff_ledger_cursor or 0)
        ):
            _evidence_invalid("期初截止账面不再为零或余额游标越过截止点")
    touched_after_cutoff = db.scalar(
        select(InventoryMovement.id)
        .join(
            InventoryTransaction,
            InventoryTransaction.id == InventoryMovement.transaction_id,
        )
        .where(
            InventoryTransaction.status == "posted",
            InventoryTransaction.ledger_cursor
            > (evidence.task.cutoff_ledger_cursor or 0),
            or_(
                InventoryMovement.from_account_id.in_(account_ids),
                InventoryMovement.to_account_id.in_(account_ids),
            ),
        )
        .limit(1)
    )
    if touched_after_cutoff is not None:
        _evidence_invalid("期初冻结范围在截止后已出现库存事实")
    return posting_service._issue_prelocked_inventory_graph_proof(
        db,
        command=posting_command,
        account_ids=account_ids,
        serial_ids=prelocked_reference_graph.serial_ids,
    )


def _append_task_effects(
    db: Session,
    *,
    task: FormalStocktakeTask,
    from_status: str,
    to_status: str,
    reason: str,
    event_type: str,
    actor: FormalPrincipal,
    idempotency_anchor: str,
    metadata: dict[str, object],
    request_reference: str,
    audit_aggregate_type: str,
    audit_aggregate_id: str,
    assignment: RoleAssignment,
    now: datetime,
) -> None:
    operation = "post" if to_status == "posted" else "close"
    finalizer_person = db.get(Person, actor.person_id)
    finalizer_organization = (
        db.get(Organization, finalizer_person.organization_id)
        if finalizer_person is not None
        else None
    )
    if finalizer_person is None or finalizer_organization is None:
        _fail(
            "opening_finalize_identity_snapshot_unavailable",
            "service_unavailable",
            "无法固化期初终结人的总部身份快照",
        )
    db.add(
        StateTransitionEvent(
            aggregate_type="stocktake_task",
            aggregate_id=str(task.id),
            from_status=from_status,
            to_status=to_status,
            reason=reason,
            actor_id=actor.user_id,
            idempotency_key=_event_key(f"{operation}-state", idempotency_anchor),
            occurred_at=now,
            metadata_jsonb=metadata,
            created_at=now,
        )
    )
    db.add(
        OutboxEvent(
            event_type=event_type,
            aggregate_type="stocktake_task",
            aggregate_id=str(task.id),
            payload_jsonb=metadata,
            status="pending",
            attempts=0,
            idempotency_key=_event_key(f"{operation}-outbox", idempotency_anchor),
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
        action=event_type,
        aggregate_type=audit_aggregate_type,
        aggregate_id=audit_aggregate_id,
        before_jsonb={"status": from_status, "version": task.version - 1},
        after_jsonb={
            **metadata,
            "assignment_revoked_at": _snapshot_optional_datetime(
                assignment.revoked_at
            ),
            "assignment_scope_id": assignment.scope_id,
            "assignment_scope_type": assignment.scope_type,
            "assignment_valid_from": _snapshot_datetime(assignment.valid_from),
            "assignment_valid_to": _snapshot_optional_datetime(
                assignment.valid_to
            ),
            "authorization_version": actor.authorization_version,
            "finalizer_organization_id": str(finalizer_organization.id),
            "finalizer_organization_type": finalizer_organization.org_type,
            "finalizer_person_id": str(actor.person_id),
            "finalizer_role_assignment_id": str(assignment.id),
            "finalizer_role_code": "admin",
            "finalizer_role_is_external": False,
            "finalizer_user_id": actor.user_id,
            "status": to_status,
        },
        request_id=request_reference,
        occurred_at=now,
    )


def _validate_post_side_effects(
    db: Session,
    *,
    task: FormalStocktakeTask,
    posting: StocktakePosting,
    audit_proof: object,
) -> None:
    if (
        not _SHA256.fullmatch(posting.idempotency_key_hash or "")
        or not _SHA256.fullmatch(posting.request_hash or "")
        or posting.posted_by_user_id == ""
    ):
        _replay_invalid("期初过账幂等、请求或执行人坐标无效")
    establishments = tuple(
        db.scalars(
            select(InventoryOpeningEstablishment).where(
                InventoryOpeningEstablishment.posting_id == posting.id
            )
        ).all()
    )
    transaction = (
        db.get(InventoryTransaction, posting.inventory_transaction_id)
        if posting.inventory_transaction_id is not None
        else None
    )
    if transaction is not None:
        movements = tuple(
            db.scalars(
                select(InventoryMovement)
                .where(InventoryMovement.transaction_id == transaction.id)
                .order_by(InventoryMovement.line_no)
            ).all()
        )
        if (
            transaction.transaction_no != f"OPEN-{task.id.hex}"
            or transaction.posting_key != f"opening-stocktake:{task.id}"
            or transaction.idempotency_key_hash
            != _derived_hash(
                "transaction-idempotency", posting.idempotency_key_hash
            )
            or transaction.request_hash
            != _derived_hash("transaction-request", posting.request_hash)
            or any(
                row.external_boundary_code != "approved-opening-stocktake"
                for row in movements
            )
        ):
            _replay_invalid("期初库存交易号、过账键、摘要或外部边界不规范")
    pending_count = db.scalar(
        select(func.count())
        .select_from(StocktakeDifference)
        .where(
            StocktakeDifference.task_id == task.id,
            StocktakeDifference.round_id == posting.round_id,
            StocktakeDifference.difference_type == "control_unassigned",
        )
    ) or 0
    state_rows = tuple(
        db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id == str(task.id),
                StateTransitionEvent.reason == "opening_stocktake_posted",
            )
        ).all()
    )
    if len(state_rows) != 1 or not isinstance(state_rows[0].metadata_jsonb, dict):
        _replay_invalid("期初过账状态事实缺失或重复")
    posted_task_version = state_rows[0].metadata_jsonb.get("task_version")
    if not isinstance(posted_task_version, int) or isinstance(posted_task_version, bool):
        _replay_invalid("期初过账任务版本证据无效")
    metadata = _post_metadata(
        posting=posting,
        task_version=posted_task_version,
        ledger_cursor=(
            transaction.ledger_cursor
            if transaction is not None
            else task.cutoff_ledger_cursor or 0
        ),
        scope_count=len(establishments),
        pending_count=pending_count,
    )
    _validate_task_effect_set(
        db,
        task=task,
        from_status="approved",
        to_status="posted",
        reason="opening_stocktake_posted",
        event_type="stocktake.opening.posted",
        idempotency_anchor=str(posting.id),
        metadata=metadata,
        audit_aggregate_type="stocktake_posting",
        audit_aggregate_id=str(posting.id),
        occurred_at=_as_utc(posting.posted_at),
        actor_id=posting.posted_by_user_id,
        audit_proof=audit_proof,
    )


def _validate_close_side_effects(
    db: Session,
    *,
    task: FormalStocktakeTask,
    posting: StocktakePosting,
    audit_proof: object,
) -> None:
    rows = tuple(
        db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id == str(task.id),
                StateTransitionEvent.reason == "opening_stocktake_closed",
            )
        ).all()
    )
    if len(rows) != 1 or not isinstance(rows[0].metadata_jsonb, dict):
        _replay_invalid("期初关闭状态事实缺失或重复")
    state = rows[0]
    request_hash = state.metadata_jsonb.get("request_hash")
    key_hash = state.metadata_jsonb.get("idempotency_key_hash")
    if not isinstance(request_hash, str) or not _SHA256.fullmatch(request_hash):
        _replay_invalid("期初关闭请求摘要缺失")
    if not isinstance(key_hash, str) or not _SHA256.fullmatch(key_hash):
        _replay_invalid("期初关闭幂等摘要缺失")
    metadata = {
        "idempotency_key_hash": key_hash,
        "inventory_transaction_id": (
            str(posting.inventory_transaction_id)
            if posting.inventory_transaction_id is not None
            else None
        ),
        "posting_id": str(posting.id),
        "request_hash": request_hash,
        "task_version": task.version,
    }
    _validate_task_effect_set(
        db,
        task=task,
        from_status="posted",
        to_status="closed",
        reason="opening_stocktake_closed",
        event_type="stocktake.opening.closed",
        idempotency_anchor=key_hash,
        metadata=metadata,
        audit_aggregate_type="stocktake_task",
        audit_aggregate_id=str(task.id),
        occurred_at=_as_utc(task.closed_at),
        actor_id=state.actor_id,
        audit_proof=audit_proof,
    )


def _validate_task_effect_set(
    db: Session,
    *,
    task: FormalStocktakeTask,
    from_status: str,
    to_status: str,
    reason: str,
    event_type: str,
    idempotency_anchor: str,
    metadata: dict[str, object],
    audit_aggregate_type: str,
    audit_aggregate_id: str,
    occurred_at: datetime,
    actor_id: str | None,
    audit_proof: object,
) -> None:
    operation = "post" if to_status == "posted" else "close"
    states = tuple(
        db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id == str(task.id),
                StateTransitionEvent.reason == reason,
            )
        ).all()
    )
    outboxes = tuple(
        db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "stocktake_task",
                OutboxEvent.aggregate_id == str(task.id),
                OutboxEvent.event_type == event_type,
            )
        ).all()
    )
    audits = tuple(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.aggregate_type == audit_aggregate_type,
                AuditEvent.aggregate_id == audit_aggregate_id,
                AuditEvent.action == event_type,
            )
        ).all()
    )
    if len(states) != 1 or len(outboxes) != 1 or len(audits) != 1:
        _replay_invalid("期初终结状态、Outbox 或审计事实不完整")
    state, outbox, audit = states[0], outboxes[0], audits[0]
    try:
        verified = _verify_audit_event_with_prelocked_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
            event_id=audit.id,
        )
    except AuditChainError as exc:
        _replay_invalid("期初终结审计链无法验证", cause=exc)
    expected_state_key = _event_key(f"{operation}-state", idempotency_anchor)
    expected_outbox_key = _event_key(f"{operation}-outbox", idempotency_anchor)
    after = audit.after_jsonb if isinstance(audit.after_jsonb, dict) else {}
    assignment_id = after.get("finalizer_role_assignment_id")
    assignment_valid_from = after.get("assignment_valid_from")
    assignment_valid_to = after.get("assignment_valid_to")
    assignment_revoked_at = after.get("assignment_revoked_at")
    authorization_version = after.get("authorization_version")
    finalizer_person_id = after.get("finalizer_person_id")
    finalizer_organization_id = after.get("finalizer_organization_id")
    finalizer_organization_type = after.get("finalizer_organization_type")
    finalizer_user_id = after.get("finalizer_user_id")
    if (
        state.from_status != from_status
        or state.to_status != to_status
        or state.actor_id != actor_id
        or state.idempotency_key != expected_state_key
        or state.metadata_jsonb != metadata
        or _as_utc(state.occurred_at) != occurred_at
        or _as_utc(state.created_at) != occurred_at
        or outbox.idempotency_key != expected_outbox_key
        or outbox.payload_jsonb != metadata
        or _as_utc(outbox.available_at) != occurred_at
        or _as_utc(outbox.created_at) != occurred_at
        or verified.id != audit.id
        or audit.actor_user_id != actor_id
        or _as_utc(audit.occurred_at) != occurred_at
        or audit.before_jsonb
        != {"status": from_status, "version": metadata["task_version"] - 1}
        or not audit.request_id.startswith("opening-finalize-request-")
        or not _SHA256.fullmatch(
            audit.request_id.removeprefix("opening-finalize-request-")
        )
        or after
        != {
            **metadata,
            "assignment_revoked_at": assignment_revoked_at,
            "assignment_scope_id": after.get("assignment_scope_id"),
            "assignment_scope_type": after.get("assignment_scope_type"),
            "assignment_valid_from": assignment_valid_from,
            "assignment_valid_to": assignment_valid_to,
            "authorization_version": authorization_version,
            "finalizer_organization_id": finalizer_organization_id,
            "finalizer_organization_type": finalizer_organization_type,
            "finalizer_person_id": finalizer_person_id,
            "finalizer_role_assignment_id": assignment_id,
            "finalizer_role_code": after.get("finalizer_role_code"),
            "finalizer_role_is_external": after.get(
                "finalizer_role_is_external"
            ),
            "finalizer_user_id": finalizer_user_id,
            "status": to_status,
        }
        or not isinstance(assignment_id, str)
        or not isinstance(finalizer_person_id, str)
        or not isinstance(finalizer_organization_id, str)
        or finalizer_organization_type != "headquarters"
        or not _historical_admin_snapshot_valid(
            assignment_id=assignment_id,
            actor_user_id=actor_id,
            finalizer_user_id=finalizer_user_id,
            person_id=finalizer_person_id,
            organization_id=finalizer_organization_id,
            organization_type=finalizer_organization_type,
            authorization_version=authorization_version,
            role_code=after.get("finalizer_role_code"),
            role_is_external=after.get("finalizer_role_is_external"),
            scope_type=after.get("assignment_scope_type"),
            scope_id=after.get("assignment_scope_id"),
            valid_from=assignment_valid_from,
            valid_to=assignment_valid_to,
            revoked_at=assignment_revoked_at,
            occurred_at=occurred_at,
        )
    ):
        _replay_invalid("期初终结副作用内容不一致")


def _validate_inventory_transaction_side_effects(
    db: Session,
    *,
    transaction: InventoryTransaction,
    audit_proof: object,
) -> None:
    states = tuple(
        db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "inventory_transaction",
                StateTransitionEvent.aggregate_id == str(transaction.id),
            )
        ).all()
    )
    outboxes = tuple(
        db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "inventory_transaction",
                OutboxEvent.aggregate_id == str(transaction.id),
                OutboxEvent.event_type == "inventory.transaction.posted",
            )
        ).all()
    )
    audits = tuple(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.aggregate_type == "inventory_transaction",
                AuditEvent.aggregate_id == str(transaction.id),
                AuditEvent.action == "inventory.transaction.posted",
            )
        ).all()
    )
    movements = tuple(
        db.scalars(
            select(InventoryMovement).where(
                InventoryMovement.transaction_id == transaction.id
            )
        ).all()
    )
    if len(states) != 1 or len(outboxes) != 1 or len(audits) != 1:
        _replay_invalid("期初库存交易的状态、Outbox 或审计事实不完整")
    state, outbox, audit = states[0], outboxes[0], audits[0]
    try:
        verified = _verify_audit_event_with_prelocked_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
            event_id=audit.id,
        )
    except AuditChainError as exc:
        _replay_invalid("期初库存交易审计链无法验证", cause=exc)
    posted_at = _as_utc(transaction.posted_at)
    request_reference = (
        state.metadata_jsonb.get("request_reference")
        if isinstance(state.metadata_jsonb, dict)
        else None
    )
    expected_payload = {
        "transaction_id": str(transaction.id),
        "transaction_no": transaction.transaction_no,
        "movement_type": "opening",
        "ledger_cursor": transaction.ledger_cursor,
        "reversed_transaction_id": None,
    }
    if (
        transaction.movement_type != "opening"
        or state.from_status is not None
        or state.to_status != "posted"
        or state.reason != "inventory_transaction_posted"
        or state.actor_id != transaction.actor_user_id
        or state.idempotency_key
        != posting_service._derived_evidence_key("state", transaction.id, "posted")
        or state.metadata_jsonb
        != {
            "ledger_cursor": transaction.ledger_cursor,
            "movement_type": "opening",
            "request_reference": request_reference,
        }
        or not isinstance(request_reference, str)
        or not request_reference.startswith("inventory-request-")
        or _as_utc(state.occurred_at) != posted_at
        or outbox.idempotency_key
        != posting_service._derived_evidence_key("outbox", transaction.id, "posted")
        or outbox.payload_jsonb != expected_payload
        or _as_utc(outbox.available_at) != posted_at
        or verified.id != audit.id
        or audit.actor_user_id != transaction.actor_user_id
        or audit.before_jsonb is not None
        or audit.after_jsonb
        != {
            "ledger_cursor": transaction.ledger_cursor,
            "movement_count": len(movements),
            "movement_type": "opening",
            "posting_key": transaction.posting_key,
            "reversed_transaction_id": None,
            "status": "posted",
        }
        or audit.request_id != request_reference
        or _as_utc(audit.occurred_at) != posted_at
    ):
        _replay_invalid("期初库存交易副作用内容不一致")


def _validate_opening_inventory_projection(
    db: Session,
    *,
    task: FormalStocktakeTask,
    posting: StocktakePosting,
) -> None:
    """Recompute touched balance and SN projections from immutable ledger facts.

    Immediately after the opening write this proves every positive balance is
    exactly the counted opening quantity at the opening cursor and every SN is
    positioned by its opening movement.  Close and later read-only replay use
    the same calculation across any legitimate subsequent transactions, so a
    newer projection is accepted only when it equals the complete immutable
    ledger history rather than the stale opening value.
    """

    count_lines = tuple(
        db.scalars(
            select(StocktakeCountLine).where(
                StocktakeCountLine.task_id == task.id,
                StocktakeCountLine.round_id == posting.round_id,
            )
        ).all()
    )
    account_ids = tuple(
        sorted(
            {
                *(row.stock_account_id for row in count_lines),
                *(
                    row
                    for row in db.scalars(
                        select(InventoryMovement.to_account_id).where(
                            InventoryMovement.transaction_id
                            == posting.inventory_transaction_id,
                            InventoryMovement.to_account_id.is_not(None),
                        )
                    ).all()
                    if row is not None
                ),
            },
            key=str,
        )
    )
    for account_id in account_ids:
        rows = tuple(
            db.execute(
                select(InventoryMovement, InventoryTransaction)
                .join(
                    InventoryTransaction,
                    InventoryTransaction.id == InventoryMovement.transaction_id,
                )
                .where(
                    InventoryTransaction.status == "posted",
                    or_(
                        InventoryMovement.from_account_id == account_id,
                        InventoryMovement.to_account_id == account_id,
                    ),
                )
                .order_by(
                    InventoryTransaction.ledger_cursor,
                    InventoryMovement.line_no,
                    InventoryMovement.id,
                )
            ).all()
        )
        expected_quantity = _ZERO
        expected_cursor = 0
        touched_transaction_ids: set[uuid.UUID] = set()
        for movement, transaction in rows:
            if movement.from_account_id == account_id:
                expected_quantity -= movement.quantity
            if movement.to_account_id == account_id:
                expected_quantity += movement.quantity
            expected_cursor = max(expected_cursor, transaction.ledger_cursor)
            touched_transaction_ids.add(transaction.id)
        expected_version = len(touched_transaction_ids)
        balance = db.get(StockBalance, account_id)
        if rows:
            if (
                balance is None
                or balance.quantity != expected_quantity
                or balance.ledger_cursor != expected_cursor
                or balance.version != expected_version
            ):
                _replay_invalid("期初相关库存余额与不可变流水重算结果不一致")
        elif balance is not None and (
            balance.quantity != _ZERO
            or balance.ledger_cursor > (task.cutoff_ledger_cursor or 0)
            or balance.version != 0
        ):
            _replay_invalid("零期初账户出现无流水余额或越界游标")

    if posting.inventory_transaction_id is None:
        return
    opening_serial_ids = tuple(
        db.scalars(
            select(InventoryMovementSerial.serial_id)
            .where(
                InventoryMovementSerial.transaction_id
                == posting.inventory_transaction_id
            )
            .order_by(InventoryMovementSerial.serial_id)
        ).all()
    )
    if len(opening_serial_ids) != len(set(opening_serial_ids)):
        _replay_invalid("期初 SN 流水重复")
    for serial_id in opening_serial_ids:
        serial_history = tuple(
            db.execute(
                select(InventoryMovement, InventoryTransaction)
                .join(
                    InventoryMovementSerial,
                    InventoryMovementSerial.movement_id == InventoryMovement.id,
                )
                .join(
                    InventoryTransaction,
                    InventoryTransaction.id == InventoryMovement.transaction_id,
                )
                .where(
                    InventoryMovementSerial.serial_id == serial_id,
                    InventoryTransaction.status == "posted",
                )
                .order_by(
                    InventoryTransaction.ledger_cursor,
                    InventoryMovement.line_no,
                    InventoryMovement.id,
                )
            ).all()
        )
        if not serial_history:
            _replay_invalid("期初 SN 缺少不可变流水历史")
        latest_movement, _latest_transaction = serial_history[-1]
        position = db.get(SerialCurrentPosition, serial_id)
        if (
            position is None
            or position.last_movement_id != latest_movement.id
            or position.stock_account_id != latest_movement.to_account_id
        ):
            _replay_invalid("期初 SN 当前位置与最后不可变流水不一致")


def _post_metadata(
    *,
    posting: StocktakePosting,
    task_version: int,
    ledger_cursor: int,
    scope_count: int,
    pending_count: int,
) -> dict[str, object]:
    return {
        "established_scope_count": scope_count,
        "inventory_transaction_id": (
            str(posting.inventory_transaction_id)
            if posting.inventory_transaction_id is not None
            else None
        ),
        "ledger_cursor": ledger_cursor,
        "pending_control_difference_count": pending_count,
        "posting_id": str(posting.id),
        "round_id": str(posting.round_id),
        "task_version": task_version,
        "total_quantity": _canonical_quantity(posting.total_quantity),
    }


def _post_result(
    db: Session, task: FormalStocktakeTask, posting: StocktakePosting
) -> OpeningStocktakePostResult:
    establishments = tuple(
        db.scalars(
            select(InventoryOpeningEstablishment).where(
                InventoryOpeningEstablishment.posting_id == posting.id
            )
        ).all()
    )
    transaction = (
        db.get(InventoryTransaction, posting.inventory_transaction_id)
        if posting.inventory_transaction_id is not None
        else None
    )
    pending_count = db.scalar(
        select(func.count())
        .select_from(StocktakeDifference)
        .where(
            StocktakeDifference.task_id == task.id,
            StocktakeDifference.round_id == posting.round_id,
            StocktakeDifference.difference_type == "control_unassigned",
        )
    ) or 0
    state = db.scalar(
        select(StateTransitionEvent).where(
            StateTransitionEvent.aggregate_type == "stocktake_task",
            StateTransitionEvent.aggregate_id == str(task.id),
            StateTransitionEvent.reason == "opening_stocktake_posted",
        )
    )
    posted_task_version = (
        state.metadata_jsonb.get("task_version")
        if state is not None and isinstance(state.metadata_jsonb, dict)
        else None
    )
    if not isinstance(posted_task_version, int) or isinstance(posted_task_version, bool):
        _replay_invalid("期初过账任务版本证据无效")
    return OpeningStocktakePostResult(
        task_id=task.id,
        round_id=posting.round_id,
        posting_id=posting.id,
        inventory_transaction_id=posting.inventory_transaction_id,
        resulting_task_status="posted",
        task_version=posted_task_version,
        total_quantity=posting.total_quantity,
        established_scope_count=len(establishments),
        pending_control_difference_count=pending_count,
        ledger_cursor=(
            transaction.ledger_cursor
            if transaction is not None
            else task.cutoff_ledger_cursor or 0
        ),
    )


def _close_result(
    task: FormalStocktakeTask, posting: StocktakePosting
) -> OpeningStocktakeCloseResult:
    if task.closed_at is None:
        _replay_invalid("期初关闭时间缺失")
    return OpeningStocktakeCloseResult(
        task_id=task.id,
        posting_id=posting.id,
        inventory_transaction_id=posting.inventory_transaction_id,
        resulting_task_status=task.status,
        task_version=task.version,
        closed_at=_as_utc(task.closed_at),
    )


def _require_unique_posting(db: Session, task_id: uuid.UUID) -> StocktakePosting:
    rows = tuple(
        db.scalars(
            select(StocktakePosting).where(
                StocktakePosting.task_id == task_id,
                StocktakePosting.posting_kind == "opening",
            )
        ).all()
    )
    if len(rows) != 1:
        _replay_invalid("期初唯一过账事实缺失")
    return rows[0]


def _lock_task(db: Session, task_id: uuid.UUID) -> FormalStocktakeTask:
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == task_id)
        .with_for_update(of=FormalStocktakeTask)
        .execution_options(populate_existing=True)
    )
    if task is None:
        _fail("opening_finalize_task_not_found", "not_found", "期初盘点任务不存在")
    return task


def _lock_ledger_head(db: Session) -> InventoryLedgerHead:
    head = db.scalar(
        select(InventoryLedgerHead)
        .where(
            InventoryLedgerHead.id == posting_service.INVENTORY_LEDGER_HEAD_ID,
            InventoryLedgerHead.stream_key == INVENTORY_STREAM_KEY,
        )
        .with_for_update(of=InventoryLedgerHead)
        .execution_options(populate_existing=True)
    )
    if head is None or not isinstance(head.next_cursor, int) or head.next_cursor <= 0:
        _fail(
            "opening_finalize_ledger_head_unavailable",
            "service_unavailable",
            "库存账本游标未正确初始化",
        )
    return head


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail(
            "formal_principal_required",
            "forbidden",
            "期初终结必须使用正式权限主体",
        )
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _fail("opening_finalize_actor_inactive", "forbidden", "当前账号或人员不可终结期初")
    return actor


def _require_current_actor(
    db: Session, supplied: FormalPrincipal, now: datetime
) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError as exc:
        _fail(
            "opening_finalize_actor_not_current",
            "forbidden",
            "正式权限上下文已失效，请重新读取",
            cause=exc,
        )
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail(
            "opening_finalize_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取后再终结期初",
        )
    if (
        current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail("opening_finalize_actor_inactive", "forbidden", "当前账号或人员不可终结期初")
    return current


def _authorize_finalizer(
    db: Session,
    actor: FormalPrincipal,
    now: datetime,
    *,
    lock_rows: bool = True,
) -> tuple[RoleAssignment, ScopeGrant]:
    grants = tuple(
        row
        for row in actor.assignments
        if row.role_code == "admin"
        and row.scope_type == "national"
        and row.scope_id == "*"
    )
    try:
        allowed = actor.allows(
            db,
            "stocktake",
            "post_opening",
            target_scope_type="national",
            target_scope_id="*",
        )
    except FormalAccessError as exc:
        _fail(
            "opening_finalize_authorization_invalid",
            "forbidden",
            "期初终结权限图无效",
            cause=exc,
        )
    if len(grants) != 1 or not allowed:
        _fail(
            "opening_finalize_forbidden",
            "forbidden",
            "仅具备 stocktake/post_opening 权限的总部管理员可终结期初",
        )
    grant = grants[0]
    assignment_statement = select(RoleAssignment).where(
        RoleAssignment.id == grant.assignment_id
    )
    if lock_rows:
        assignment_statement = assignment_statement.with_for_update(
            of=RoleAssignment
        )
    assignment = db.scalar(
        assignment_statement.execution_options(populate_existing=True)
    )
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    user = db.get(User, actor.user_id)
    person = db.get(Person, actor.person_id)
    organization = (
        db.get(Organization, person.organization_id)
        if person is not None
        else None
    )
    if (
        assignment is None
        or assignment.user_id != actor.user_id
        or assignment.scope_type != "national"
        or assignment.scope_id != "*"
        or assignment.status not in {"scheduled", "active"}
        or _as_utc(assignment.valid_from) > now
        or (
            assignment.valid_to is not None
            and now >= _as_utc(assignment.valid_to)
        )
        or (
            assignment.revoked_at is not None
            and now >= _as_utc(assignment.revoked_at)
        )
        or role is None
        or role.code != "admin"
        or role.status != "active"
        or role.is_external
        or user is None
        or user.person_id != actor.person_id
        or person is None
        or person.employment_status != "active"
        or organization is None
        or organization.org_type != "headquarters"
        or organization.status != "active"
    ):
        _fail(
            "opening_finalize_assignment_not_current",
            "forbidden",
            "总部管理员角色授权已失效",
        )
    return assignment, grant


def _historical_admin_snapshot_valid(
    *,
    assignment_id: str,
    actor_user_id: str | None,
    finalizer_user_id: object,
    person_id: str,
    organization_id: str,
    organization_type: object,
    authorization_version: object,
    role_code: object,
    role_is_external: object,
    scope_type: object,
    scope_id: object,
    valid_from: object,
    valid_to: object,
    revoked_at: object,
    occurred_at: datetime,
) -> bool:
    """Validate the immutable authorization snapshot at event time.

    Historical proof deliberately performs no HR/RBAC lookup.  People move,
    leave, and lose assignments after a legitimate opening finalization; those
    mutable current rows must never make an already hash-chained terminal fact
    unreadable.  Current writers are still checked against the live active-HQ
    graph before this snapshot is appended.
    """

    try:
        identifiers = (
            uuid.UUID(assignment_id),
            uuid.UUID(str(finalizer_user_id)),
            uuid.UUID(person_id),
            uuid.UUID(organization_id),
        )
    except (AttributeError, TypeError, ValueError):
        return False
    if any(identifier.int == 0 for identifier in identifiers):
        return False
    if (
        actor_user_id != finalizer_user_id
        or isinstance(authorization_version, bool)
        or not isinstance(authorization_version, int)
        or authorization_version <= 0
        or role_code != "admin"
        or role_is_external is not False
        or scope_type != "national"
        or scope_id != "*"
        or organization_type != "headquarters"
    ):
        return False
    checked_valid_from = _parse_snapshot_datetime(valid_from)
    checked_valid_to = _parse_optional_snapshot_datetime(valid_to)
    checked_revoked_at = _parse_optional_snapshot_datetime(revoked_at)
    checked_occurred_at = _as_optional_utc(occurred_at)
    if (
        checked_valid_from is None
        or checked_occurred_at is None
        or (valid_to is not None and checked_valid_to is None)
        or (revoked_at is not None and checked_revoked_at is None)
    ):
        return False
    return bool(
        checked_valid_from <= checked_occurred_at
        and (
            checked_valid_to is None
            or checked_occurred_at < checked_valid_to
        )
        and (
            checked_revoked_at is None
            or checked_occurred_at < checked_revoked_at
        )
    )


def _require_expected_state(
    task: FormalStocktakeTask,
    *,
    expected_status: str,
    expected_version: int,
    operation: str,
) -> None:
    if task.task_type != "opening":
        _fail(
            "opening_finalize_task_type_invalid",
            "precondition_failed",
            f"仅期初盘点任务可执行{operation}",
        )
    if task.version != expected_version:
        _fail(
            "opening_finalize_version_conflict",
            "conflict",
            "期初任务版本已变化，请重新读取",
        )
    if task.status != expected_status:
        _fail(
            "opening_finalize_state_invalid",
            "precondition_failed",
            f"期初任务当前状态不可执行{operation}",
        )


def _validate_post_command(
    command: PostOpeningStocktakeCommand,
) -> PostOpeningStocktakeCommand:
    if not isinstance(command, PostOpeningStocktakeCommand):
        _fail("opening_finalize_command_required", "invalid_request", "期初过账命令类型无效")
    return PostOpeningStocktakeCommand(
        task_id=_require_uuid("task_id", command.task_id),
        expected_version=_require_version(command.expected_version),
    )


def _validate_close_command(
    command: CloseOpeningStocktakeCommand,
) -> CloseOpeningStocktakeCommand:
    if not isinstance(command, CloseOpeningStocktakeCommand):
        _fail("opening_close_command_required", "invalid_request", "期初关闭命令类型无效")
    return CloseOpeningStocktakeCommand(
        task_id=_require_uuid("task_id", command.task_id),
        expected_version=_require_version(command.expected_version),
    )


def _require_version(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        _fail(
            "opening_finalize_expected_version_invalid",
            "invalid_request",
            "expected_version 必须为非负整数",
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


def _require_idempotency_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 200
        or _PRINTABLE.fullmatch(value) is None
    ):
        _fail(
            "opening_finalize_idempotency_key_invalid",
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
            "opening_finalize_request_id_invalid",
            "invalid_request",
            "请求标识必须为 8 至 160 位可打印 ASCII 字符",
        )
    return value


def _request_hash(
    operation: str,
    actor: FormalPrincipal,
    command: PostOpeningStocktakeCommand | CloseOpeningStocktakeCommand,
) -> str:
    return _hash_document(
        {
            "actor": {
                "authorization_version": actor.authorization_version,
                "person_id": str(actor.person_id),
                "user_id": actor.user_id,
            },
            "expected_version": command.expected_version,
            "operation": operation,
            "schema": "cloud_oam.opening_stocktake.finalize_request.v1",
            "task_id": str(command.task_id),
        }
    )


def _hash_document(value: Mapping[str, object]) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _storage_hash(operation: str, raw: str) -> str:
    return hashlib.sha256(
        f"cloud_oam.opening_stocktake.finalize.{operation}.idempotency.v1\0{raw}".encode()
    ).hexdigest()


def _derived_hash(kind: str, value: str) -> str:
    return hashlib.sha256(
        f"cloud_oam.opening_stocktake.finalize.{kind}.v1\0{value}".encode()
    ).hexdigest()


def _event_key(kind: str, anchor: str) -> str:
    digest = _derived_hash(f"event.{kind}", anchor)
    return f"opening-finalize-{kind}-{digest}"


def _request_reference(operation: str, raw: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.opening_stocktake.finalize.{operation}.request.v1\0{raw}".encode()
    ).hexdigest()
    prefix = "inventory-request" if operation == "inventory" else "opening-finalize-request"
    return f"{prefix}-{digest}"


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


def _uses_direct_reference_row_locks(db: Session) -> bool:
    """Keep local row-lock shape without exceeding the production API ACL."""

    return db.get_bind().dialect.name != "postgresql"


def _select_only_reference_statement(db: Session, statement):
    """Use direct locks locally; PostgreSQL relies on 0027 owner helpers."""

    if _uses_direct_reference_row_locks(db):
        return statement.with_for_update()
    return statement


def _database_now(db: Session) -> datetime:
    if db.get_bind().dialect.name == "postgresql":
        value = db.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            _fail(
                "opening_finalize_server_time_unavailable",
                "service_unavailable",
                "数据库服务端时间不可用",
            )
        return _as_utc(value)
    return datetime.now(timezone.utc)


def _as_utc(value: object) -> datetime:
    result = _as_optional_utc(value)
    if result is None:
        _evidence_invalid("期初证据时间无效")
    return result


def _as_optional_utc(value: object) -> datetime | None:
    if not isinstance(value, datetime):
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _snapshot_datetime(value: object) -> str:
    checked = _as_optional_utc(value)
    if checked is None:
        _fail(
            "opening_finalize_identity_snapshot_unavailable",
            "service_unavailable",
            "无法固化期初终结授权时间快照",
        )
    return checked.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _snapshot_optional_datetime(value: object) -> str | None:
    return None if value is None else _snapshot_datetime(value)


def _parse_snapshot_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.endswith("Z"):
        return None
    try:
        checked = datetime.fromisoformat(value[:-1] + "+00:00")
    except ValueError:
        return None
    checked = checked.astimezone(timezone.utc)
    if (
        checked.isoformat(timespec="microseconds").replace("+00:00", "Z")
        != value
    ):
        return None
    return checked


def _parse_optional_snapshot_datetime(value: object) -> datetime | None:
    return None if value is None else _parse_snapshot_datetime(value)


def _canonical_quantity(value: Decimal) -> str:
    if not isinstance(value, Decimal) or not value.is_finite():
        _evidence_invalid("期初数量无法规范化")
    return format(value.quantize(Decimal("0.001")), "f")


def _decimal_scale(value: Decimal) -> int:
    return max(0, -value.normalize().as_tuple().exponent)


def _evidence_invalid(message: str, *, cause: Exception | None = None) -> None:
    _fail(
        "opening_finalize_evidence_invalid",
        "precondition_failed",
        message,
        cause=cause,
    )


def _replay_invalid(message: str, *, cause: Exception | None = None) -> None:
    _fail(
        "opening_finalize_replay_evidence_invalid",
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
    error = OpeningStocktakeFinalizeError(code, category, message)
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "CloseOpeningStocktakeCommand",
    "OpeningStocktakeCloseResult",
    "OpeningStocktakeFinalizeError",
    "OpeningStocktakePostResult",
    "PostOpeningStocktakeCommand",
    "close_posted_opening_stocktake",
    "post_approved_opening_stocktake",
    "validate_opening_finalize_evidence_for_replay",
    "validate_opening_terminal_effects_for_replay",
]
