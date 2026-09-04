"""Scope-safe, read-only queries over sealed opening-stocktake evidence.

The service deliberately keeps authorization scope and business-state scope
separate.  It never commits, never reads legacy/v0.9 stocktake tables, and
never exposes snapshot/control quantities while a blind round is counting.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final
import uuid

from sqlalchemy import exists, or_, select
from sqlalchemy.orm import Session

from ..formal_access import Entitlement, FormalAccessError, FormalPrincipal, ScopeGrant
from ..foundation_models import Organization, Person
from ..inventory_models import InventoryLedgerHead, StockLocation
from ..opening_stocktake_read_schemas import (
    OpeningAllowedAction,
    OpeningEvidenceStatus,
    OpeningObservationDispositionSummaryOut,
    OpeningStocktakeDifferenceOut,
    OpeningStocktakeObservationOut,
    OpeningStocktakeRoundOut,
    OpeningStocktakeReviewSummaryOut,
    OpeningStocktakeScopeOut,
    OpeningStocktakeTaskDetailOut,
    OpeningStocktakeTaskPageOut,
    OpeningStocktakeTaskSummaryOut,
)
from ..stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    InventoryOpeningEstablishment,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakeObservationDisposition,
    StocktakeRecountCase,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
    StocktakePosting,
)
from . import opening_control_reconciliation as reconciliation_service
from . import inventory_posting as posting_service
from . import opening_stocktake_finalize as finalize_service
from .audit_chain import _lock_audit_chain_head_with_proof
from .postgresql_lock_graph import lock_opening_stocktake_task_evidence


_ZERO = Decimal("0.000")
_TASK_STATUSES = frozenset(
    {
        "draft",
        "issued",
        "frozen",
        "counting",
        "submitted",
        "region_review",
        "hq_review",
        "approved",
        "recount_required",
        "posted",
        "closed",
        "cancelled",
    }
)
_TERMINAL_STATUSES = frozenset({"posted", "closed"})
_SEALED_TASK_STATUSES = frozenset(
    {
        "submitted",
        "region_review",
        "hq_review",
        "approved",
        "recount_required",
        "posted",
        "closed",
    }
)
_OPENING_READ_BATCH_ROOT_SEAL: Final[object] = object()
_OPENING_READ_BATCH_GRAPH_SEAL: Final[object] = object()


class OpeningStocktakeReadError(RuntimeError):
    """Stable, non-sensitive formal opening-stocktake read failure."""

    def __init__(self, *, code: str, status_code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status_code = status_code
        self.public_message = message

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "message": self.public_message}


@dataclass(frozen=True, slots=True)
class _ReadScope:
    grants: tuple[ScopeGrant, ...]
    admin: bool
    region_org_ids: frozenset[uuid.UUID]
    technician: bool


@dataclass(frozen=True, slots=True)
class _ReadSnapshot:
    ledger_next_cursor: int
    tasks: dict[uuid.UUID, tuple[object, ...]]


@dataclass(frozen=True, slots=True)
class _TaskGraph:
    task: FormalStocktakeTask
    round_row: StocktakeRound | None
    scopes: tuple[FormalStocktakeScope, ...]
    locations: tuple[StockLocation, ...]
    visible_scopes: tuple[FormalStocktakeScope, ...]
    recount_assignments: tuple[StocktakeRecountScopeAssignment, ...]
    observations: tuple[StocktakeCountObservation, ...]
    dispositions: tuple[StocktakeObservationDisposition, ...]
    completions: tuple[StocktakeScopeCountCompletion, ...]
    submissions: tuple[StocktakeRoundSubmission, ...]
    difference_completions: tuple[StocktakeDifferenceSetCompletion, ...]
    differences: tuple[StocktakeDifference, ...]
    reviews: tuple[StocktakeReview, ...]
    postings: tuple[StocktakePosting, ...]
    establishments: tuple[InventoryOpeningEstablishment, ...]


@dataclass(frozen=True, slots=True)
class _LockedOpeningReadBatchRoot:
    """Ledger-first, UUID-ordered task roots bound to one read transaction."""

    session: Session
    transaction: object
    tasks: tuple[FormalStocktakeTask, ...]
    ledger_next_cursor: int
    terminal_root: object | None
    seal: object


@dataclass(frozen=True, slots=True)
class _PrelockedOpeningReadBatchGraphProof:
    """No-audit proof for one complete list/detail opening-read batch."""

    session: Session
    transaction: object
    root: _LockedOpeningReadBatchRoot
    principal_graph: object
    round_plans: tuple[
        tuple[uuid.UUID, tuple[tuple[uuid.UUID, object], ...]], ...
    ]
    disposition_resolutions: tuple[
        tuple[uuid.UUID, Mapping[uuid.UUID, object]], ...
    ]
    serial_ids: tuple[uuid.UUID, ...]
    serial_graph: object
    terminal_graph: object | None
    reconciliation_graph: object | None
    roundless_reconciliation_task_ids: tuple[uuid.UUID, ...]
    seal: object


@dataclass(frozen=True, slots=True)
class _OpeningReadDetailEvidencePlan:
    task_id: uuid.UUID
    recount_plan: object | None
    count_plan: object | None
    disposition_replay_plans: tuple[tuple[uuid.UUID, object], ...]
    review_plans: tuple[object, ...]


def list_opening_stocktakes(
    db: Session,
    *,
    actor: FormalPrincipal,
    limit: int,
    after_id: uuid.UUID | None = None,
) -> OpeningStocktakeTaskPageOut:
    read_scope = _require_stocktake_read(db, actor)
    if isinstance(limit, bool) or not 1 <= limit <= 20:
        raise OpeningStocktakeReadError(
            code="opening_stocktake_page_limit_invalid",
            status_code=422,
            message="期初盘点分页大小无效",
        )
    statement = (
        select(FormalStocktakeTask)
        .where(
            FormalStocktakeTask.task_type == "opening",
            _visible_task_predicate(actor, read_scope),
        )
        .order_by(FormalStocktakeTask.id)
        .limit(limit + 1)
        .execution_options(populate_existing=True)
    )
    if after_id is not None:
        statement = statement.where(FormalStocktakeTask.id > after_id)
    rows = tuple(db.scalars(statement).all())
    has_more = len(rows) > limit
    tasks = rows[:limit]
    ledger_cursor = _ledger_snapshot(db)
    if not tasks:
        return OpeningStocktakeTaskPageOut(items=[], next_after_id=None)
    snapshot = _snapshot(ledger_cursor, tasks)
    graphs, reconciliation_statuses = _load_prelocked_opening_read_batch(
        db,
        actor=actor,
        read_scope=read_scope,
        snapshot=snapshot,
    )
    items = [
        _summary(
            db,
            actor=actor,
            graph=graph,
            reconciliation=reconciliation_statuses[graph.task.id],
        )
        for graph in graphs
    ]
    _ensure_observation_facts_current(db, graphs)
    _ensure_snapshot_current(db, snapshot)
    return OpeningStocktakeTaskPageOut(
        items=items,
        next_after_id=(tasks[-1].id if has_more and tasks else None),
    )


def opening_stocktake_detail(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
) -> OpeningStocktakeTaskDetailOut:
    read_scope = _require_stocktake_read(db, actor)
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(
            FormalStocktakeTask.id == task_id,
            FormalStocktakeTask.task_type == "opening",
            _visible_task_predicate(actor, read_scope),
        )
        .execution_options(populate_existing=True)
    )
    # Deliberately conflate a missing task with a task outside the caller's
    # formal scope so the endpoint cannot be used as a task-id oracle.
    if task is None:
        raise OpeningStocktakeReadError(
            code="opening_stocktake_not_found",
            status_code=404,
            message="期初盘点任务不存在",
        )
    ledger_cursor = _ledger_snapshot(db)
    snapshot = _snapshot(ledger_cursor, (task,))
    graphs, reconciliation_statuses = _load_prelocked_opening_read_batch(
        db,
        actor=actor,
        read_scope=read_scope,
        snapshot=snapshot,
    )
    graph = graphs[0]
    reconciliation = reconciliation_statuses[graph.task.id]
    output = _detail(
        db,
        actor=actor,
        graph=graph,
        reconciliation=reconciliation,
    )
    _ensure_observation_facts_current(db, (graph,))
    _ensure_snapshot_current(db, snapshot)
    return output


def _load_prelocked_opening_read_batch(
    db: Session,
    *,
    actor: FormalPrincipal,
    read_scope: _ReadScope,
    snapshot: _ReadSnapshot,
) -> tuple[
    tuple[_TaskGraph, ...],
    dict[uuid.UUID, reconciliation_service.OpeningControlReconciliationStatus],
]:
    """Execute the canonical page-wide lock phase, then only pure reproof."""

    try:
        root = _lock_opening_read_batch_root(db, snapshot=snapshot)
        proof = _lock_opening_read_batch_graph(db, root=root, actor=actor)
        graphs = _load_task_graphs(
            db,
            actor=actor,
            read_scope=read_scope,
            tasks=root.tasks,
        )
        detail_plans = tuple(
            _plan_detail_evidence(db, graph, proof=proof) for graph in graphs
        )
        _head, audit_proof = _lock_audit_chain_head_with_proof(
            db,
            stream_key=posting_service.INVENTORY_STREAM_KEY,
        )
        _validate_opening_read_batch_graph(
            db,
            proof=proof,
            audit_proof=audit_proof,
        )
        for graph, detail_plan in zip(graphs, detail_plans, strict=True):
            _reprove_detail_evidence(
                db,
                graph,
                proof=proof,
                detail_plan=detail_plan,
                audit_proof=audit_proof,
            )
        reconciliation_statuses = _load_reconciliation_statuses(
            db,
            graphs,
            proof=proof,
            audit_proof=audit_proof,
        )
        return graphs, reconciliation_statuses
    except OpeningStocktakeReadError:
        raise
    except Exception:
        _invalid_evidence()
    raise AssertionError("unreachable opening read batch boundary")


def _lock_opening_read_batch_root(
    db: Session,
    *,
    snapshot: _ReadSnapshot,
) -> _LockedOpeningReadBatchRoot:
    """Lock ledger then every already-selected task and reject plain-read drift."""

    task_ids = tuple(sorted(snapshot.tasks, key=str))
    if not task_ids:
        raise OpeningStocktakeReadError(
            code="opening_stocktake_read_batch_empty",
            status_code=503,
            message="期初盘点读取批次坐标为空",
        )
    head = db.scalar(
        select(InventoryLedgerHead)
        .where(InventoryLedgerHead.stream_key == posting_service.INVENTORY_STREAM_KEY)
        .with_for_update(of=InventoryLedgerHead)
        .execution_options(populate_existing=True)
    )
    if head is None or not isinstance(head.next_cursor, int) or head.next_cursor <= 0:
        raise OpeningStocktakeReadError(
            code="opening_stocktake_ledger_unavailable",
            status_code=503,
            message="正式库存账本尚未安全初始化",
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
    if (
        tuple(row.id for row in tasks) != task_ids
        or head.next_cursor != snapshot.ledger_next_cursor
        or {row.id: _task_read_signature(row) for row in tasks} != snapshot.tasks
    ):
        _read_changed()
    terminal_ids = tuple(
        row.id for row in tasks if row.status in _TERMINAL_STATUSES
    )
    terminal_root = (
        finalize_service._lock_opening_terminal_task_batch_root(
            db,
            task_ids=terminal_ids,
        )
        if terminal_ids
        else None
    )
    if terminal_root is not None and (
        tuple(row.id for row in terminal_root.tasks) != terminal_ids
        or terminal_root.current_ledger_cursor != head.next_cursor - 1
    ):
        _read_changed()
    transaction = db.get_transaction()
    if transaction is None:
        _invalid_evidence()
    return _LockedOpeningReadBatchRoot(
        session=db,
        transaction=transaction,
        tasks=tasks,
        ledger_next_cursor=head.next_cursor,
        terminal_root=terminal_root,
        seal=_OPENING_READ_BATCH_ROOT_SEAL,
    )


def _require_opening_read_batch_root(
    db: Session,
    proof: object,
) -> _LockedOpeningReadBatchRoot:
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _LockedOpeningReadBatchRoot)
        or proof.seal is not _OPENING_READ_BATCH_ROOT_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
    ):
        _invalid_evidence()
    return proof


def _lock_opening_read_batch_graph(
    db: Session,
    *,
    root: _LockedOpeningReadBatchRoot,
    actor: FormalPrincipal,
) -> _PrelockedOpeningReadBatchGraphProof:
    """Prelock one complete mixed-status read page without touching audit."""

    checked_root = _require_opening_read_batch_root(db, root)
    task_ids = tuple(row.id for row in checked_root.tasks)
    reconciliation_user_ids = (
        reconciliation_service._opening_control_reconciliation_historical_user_ids(
            db,
            task_ids=task_ids,
        )
    )
    principal_graph = posting_service._lock_opening_task_principal_graph(
        db,
        task_ids=task_ids,
        supplied_user_ids=(actor.user_id, *reconciliation_user_ids),
    )

    round_rows = tuple(
        db.scalars(
            select(StocktakeRound)
            .where(StocktakeRound.task_id.in_(task_ids))
            .order_by(
                StocktakeRound.task_id,
                StocktakeRound.round_no,
                StocktakeRound.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    rounds_by_task: dict[uuid.UUID, list[StocktakeRound]] = {
        task_id: [] for task_id in task_ids
    }
    for round_row in round_rows:
        if round_row.task_id not in rounds_by_task:
            _invalid_evidence()
        rounds_by_task[round_row.task_id].append(round_row)

    terminal_plan = (
        finalize_service._plan_opening_terminal_task_batch_graph(
            db,
            root=checked_root.terminal_root,
            principal_graph=principal_graph,
        )
        if checked_root.terminal_root is not None
        else None
    )
    terminal_task_ids = {
        row.id for row in checked_root.tasks if row.status in _TERMINAL_STATUSES
    }
    terminal_plans_by_task = (
        dict(terminal_plan.plans_by_task) if terminal_plan is not None else {}
    )
    terminal_rounds_by_task = (
        dict(terminal_plan.rounds_by_task) if terminal_plan is not None else {}
    )
    if set(terminal_plans_by_task) != terminal_task_ids or set(
        terminal_rounds_by_task
    ) != terminal_task_ids:
        _invalid_evidence()
    for task_id in terminal_task_ids:
        if tuple(row.id for row in terminal_rounds_by_task[task_id]) != tuple(
            row.id for row in rounds_by_task[task_id]
        ):
            _invalid_evidence()

    # A owns each terminal task helper call.  Query calls the same 0027 owner
    # helper exactly once only for the non-terminal remainder, still before
    # any page-wide serial owner is entered.
    for task in checked_root.tasks:
        if task.id in terminal_task_ids:
            continue
        task_rounds = rounds_by_task[task.id]
        if task_rounds:
            lock_opening_stocktake_task_evidence(
                db,
                task.id,
                task_rounds[-1].id,
            )

    plans_by_task: dict[
        uuid.UUID, tuple[tuple[uuid.UUID, object], ...]
    ] = {}
    for task in checked_root.tasks:
        plans_by_task[task.id] = (
            terminal_plans_by_task[task.id]
            if task.id in terminal_task_ids
            else tuple(
                (
                    round_row.id,
                    finalize_service._opening_start_reference_coordinates(
                    db,
                    task=task,
                    round_id=round_row.id,
                    require_complete_dispositions=False,
                ),
                )
                for round_row in rounds_by_task[task.id]
            )
        )
    serial_ids = tuple(
        sorted(
            {
                serial_id
                for task_plans in plans_by_task.values()
                for _round_id, plan in task_plans
                for serial_id in plan.serial_ids
            }.union(terminal_plan.serial_ids if terminal_plan is not None else ()),
            key=str,
        )
    )
    serial_graph = finalize_service._lock_opening_serial_union_graph(
        db,
        serial_ids=serial_ids,
    )

    disposition_resolutions: dict[uuid.UUID, Mapping[uuid.UUID, object]] = {}
    for task in checked_root.tasks:
        for round_id, plan in plans_by_task[task.id]:
            current_plan = finalize_service._opening_start_reference_coordinates(
                db,
                task=task,
                round_id=round_id,
                require_complete_dispositions=plan.require_complete_dispositions,
            )
            if current_plan != plan:
                _invalid_evidence()
            disposition_resolutions[round_id] = (
                finalize_service._prove_reference_plan_disposition_resolutions(
                    db,
                    task=task,
                    round_id=round_id,
                    reference_plan=plan,
                    locked_account_ids=plan.account_ids,
                    locked_serial_ids=serial_ids,
                )
            )

    terminal_serial_ids = {
        serial_id
        for task_id, task_plans in plans_by_task.items()
        if task_id in terminal_task_ids
        for _round_id, plan in task_plans
        for serial_id in plan.serial_ids
    }
    if not terminal_serial_ids.issubset(serial_ids):
        _invalid_evidence()

    terminal_graph = (
        finalize_service._seal_opening_terminal_task_batch_graph(
            db,
            plan=terminal_plan,
            serial_graph=serial_graph,
        )
        if terminal_plan is not None
        else None
    )
    reconciliation_task_ids = tuple(
        task.id
        for task in checked_root.tasks
        if rounds_by_task[task.id]
    )
    roundless_reconciliation_task_ids = tuple(
        task.id
        for task in checked_root.tasks
        if not rounds_by_task[task.id]
    )
    _validate_roundless_reconciliation_tasks(
        db,
        task_ids=roundless_reconciliation_task_ids,
    )
    reconciliation_graph = (
        reconciliation_service._lock_opening_control_reconciliation_batch_graph(
            db,
            task_ids=reconciliation_task_ids,
            principal_graph=principal_graph,
        )
        if reconciliation_task_ids
        else None
    )
    transaction = db.get_transaction()
    if transaction is None:
        _invalid_evidence()
    return _PrelockedOpeningReadBatchGraphProof(
        session=db,
        transaction=transaction,
        root=checked_root,
        principal_graph=principal_graph,
        round_plans=tuple(
            (task.id, plans_by_task[task.id]) for task in checked_root.tasks
        ),
        disposition_resolutions=tuple(
            sorted(disposition_resolutions.items(), key=lambda item: str(item[0]))
        ),
        serial_ids=serial_ids,
        serial_graph=serial_graph,
        terminal_graph=terminal_graph,
        reconciliation_graph=reconciliation_graph,
        roundless_reconciliation_task_ids=roundless_reconciliation_task_ids,
        seal=_OPENING_READ_BATCH_GRAPH_SEAL,
    )


def _require_opening_read_batch_graph(
    db: Session,
    proof: object,
) -> _PrelockedOpeningReadBatchGraphProof:
    transaction = db.get_transaction()
    if (
        not isinstance(proof, _PrelockedOpeningReadBatchGraphProof)
        or proof.seal is not _OPENING_READ_BATCH_GRAPH_SEAL
        or proof.session is not db
        or transaction is None
        or proof.transaction is not transaction
    ):
        _invalid_evidence()
    root = _require_opening_read_batch_root(db, proof.root)
    posting_service._require_opening_task_principal_graph_proof(
        db,
        proof.principal_graph,
        required_task_ids=tuple(row.id for row in root.tasks),
    )
    if tuple(task_id for task_id, _plans in proof.round_plans) != tuple(
        row.id for row in root.tasks
    ):
        _invalid_evidence()
    planned_serial_ids = tuple(
        sorted(
            {
                serial_id
                for _task_id, plans in proof.round_plans
                for _round_id, plan in plans
                for serial_id in plan.serial_ids
            },
            key=str,
        )
    )
    if proof.serial_ids != planned_serial_ids:
        _invalid_evidence()
    checked_serial_graph = (
        finalize_service._require_opening_serial_union_graph_proof(
            db,
            proof.serial_graph,
            required_serial_ids=proof.serial_ids,
        )
    )
    if checked_serial_graph.serial_ids != proof.serial_ids:
        _invalid_evidence()
    roundful_task_ids = tuple(
        task_id for task_id, plans in proof.round_plans if plans
    )
    if set(roundful_task_ids).intersection(
        proof.roundless_reconciliation_task_ids
    ) or set(roundful_task_ids).union(
        proof.roundless_reconciliation_task_ids
    ) != set(row.id for row in root.tasks):
        _invalid_evidence()
    if (proof.reconciliation_graph is None) != (not roundful_task_ids):
        _invalid_evidence()
    terminal_task_ids = tuple(
        row.id for row in root.tasks if row.status in _TERMINAL_STATUSES
    )
    if (proof.terminal_graph is None) != (not terminal_task_ids):
        _invalid_evidence()
    terminal_serial_ids = {
        serial_id
        for task_id, plans in proof.round_plans
        if task_id in set(terminal_task_ids)
        for _round_id, plan in plans
        for serial_id in plan.serial_ids
    }
    if not terminal_serial_ids.issubset(proof.serial_ids):
        _invalid_evidence()
    return proof


def _validate_roundless_reconciliation_tasks(
    db: Session,
    *,
    task_ids: tuple[uuid.UUID, ...],
) -> None:
    """Prove draft/cancelled no-round tasks have no reconciliation facts."""

    if not task_ids:
        return
    round_task_ids = set(
        db.scalars(
            select(StocktakeRound.task_id)
            .where(StocktakeRound.task_id.in_(task_ids))
            .distinct()
        ).all()
    )
    difference_task_ids = set(
        db.scalars(
            select(StocktakeDifference.task_id)
            .where(StocktakeDifference.task_id.in_(task_ids))
            .distinct()
        ).all()
    )
    binding_task_ids = set(
        db.scalars(
            select(
                reconciliation_service.OpeningControlReconciliationRun.task_id
            )
            .where(
                reconciliation_service.OpeningControlReconciliationRun.task_id.in_(
                    task_ids
                )
            )
            .distinct()
        ).all()
    )
    if round_task_ids or difference_task_ids or binding_task_ids:
        _invalid_evidence()


def _roundless_reconciliation_statuses(
    db: Session,
    *,
    task_ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, reconciliation_service.OpeningControlReconciliationStatus]:
    _validate_roundless_reconciliation_tasks(db, task_ids=task_ids)
    return {
        task_id: reconciliation_service.OpeningControlReconciliationStatus(
            status="not_required",
            reconciliation_run_id=None,
            pending_control_difference_count=0,
        )
        for task_id in task_ids
    }


def _validate_opening_read_batch_graph(
    db: Session,
    *,
    proof: _PrelockedOpeningReadBatchGraphProof,
    audit_proof: object,
) -> None:
    """Plainly re-prove the pinned graph after the one final audit lock."""

    checked = _require_opening_read_batch_graph(db, proof)
    posting_service._validate_prelocked_opening_task_principal_graph(
        db,
        proof=checked.principal_graph,
    )
    task_by_id = {row.id: row for row in checked.root.tasks}
    expected_resolutions = dict(checked.disposition_resolutions)
    locked_serial_ids = checked.serial_ids
    seen_round_ids: set[uuid.UUID] = set()
    for task_id, task_plans in checked.round_plans:
        task = task_by_id.get(task_id)
        if task is None:
            _invalid_evidence()
        for round_id, plan in task_plans:
            seen_round_ids.add(round_id)
            current_plan = finalize_service._opening_start_reference_coordinates(
                db,
                task=task,
                round_id=round_id,
                require_complete_dispositions=plan.require_complete_dispositions,
            )
            if current_plan != plan:
                _invalid_evidence()
            current_resolutions = (
                finalize_service._prove_reference_plan_disposition_resolutions(
                    db,
                    task=task,
                    round_id=round_id,
                    reference_plan=plan,
                    locked_account_ids=plan.account_ids,
                    locked_serial_ids=locked_serial_ids,
                )
            )
            if dict(expected_resolutions.get(round_id, {})) != current_resolutions:
                _invalid_evidence()
    if seen_round_ids != set(expected_resolutions):
        _invalid_evidence()
    _validate_roundless_reconciliation_tasks(
        db,
        task_ids=checked.roundless_reconciliation_task_ids,
    )
    if checked.terminal_graph is not None:
        terminal_ids = tuple(
            row.id
            for row in checked.root.tasks
            if row.status in _TERMINAL_STATUSES
        )
        finalize_service._validate_opening_terminal_batch_from_prelocked_graph(
            db,
            proof=checked.terminal_graph,
            audit_proof=audit_proof,
            require_closed_task_ids=tuple(
                row.id for row in checked.root.tasks if row.status == "closed"
            ),
            expected_ledger_cursor=checked.root.ledger_next_cursor - 1,
        )
        if not terminal_ids:
            _invalid_evidence()


def _round_disposition_resolutions(
    proof: _PrelockedOpeningReadBatchGraphProof,
    round_id: uuid.UUID,
) -> Mapping[uuid.UUID, object]:
    for candidate_round_id, resolutions in proof.disposition_resolutions:
        if candidate_round_id == round_id:
            return resolutions
    _invalid_evidence()
    raise AssertionError("unreachable opening read resolution lookup")


def _task_disposition_resolutions(
    db: Session,
    proof: _PrelockedOpeningReadBatchGraphProof,
    task_id: uuid.UUID,
) -> Mapping[uuid.UUID, object]:
    """Join only one task's already-proved round maps, without new owners.

    Recount replay traverses predecessor reviews. Those reviews require the
    whole task's disposition evidence, even while its current round is empty.
    The individual current-round disposition replay must retain its own map.
    """

    checked = _require_opening_read_batch_graph(db, proof)
    task_ids = tuple(row.id for row in checked.root.tasks)
    if task_id not in task_ids:
        _invalid_evidence()
    planned = {}
    for owner_id, plans in checked.round_plans:
        for round_id, plan in plans:
            if round_id in planned:
                _invalid_evidence()
            planned[round_id] = (owner_id, plan)
    actual = dict(db.execute(select(StocktakeRound.id, StocktakeRound.task_id).where(
        StocktakeRound.task_id.in_(task_ids),
    )).all())
    if actual != {round_id: owner_id for round_id, (owner_id, _plan) in planned.items()}:
        _invalid_evidence()
    by_round = dict(checked.disposition_resolutions)
    if len(by_round) != len(checked.disposition_resolutions) or set(by_round) != set(planned):
        _invalid_evidence()
    merged = {}
    seen_observations = set()
    for round_id, (owner_id, plan) in planned.items():
        resolutions = by_round[round_id]
        candidate_ids = tuple(row.observation_id for row in plan.disposition_candidates)
        if (
            not isinstance(resolutions, Mapping)
            or len(set(candidate_ids)) != len(candidate_ids)
            or set(resolutions) != set(candidate_ids)
            or seen_observations.intersection(candidate_ids)
            or any(row.observation_signature[:3] != (row.observation_id, owner_id, round_id)
                   for row in plan.disposition_candidates)
        ):
            _invalid_evidence()
        seen_observations.update(candidate_ids)
        if owner_id == task_id:
            merged.update(resolutions)
    return merged


def _load_task_graphs(
    db: Session,
    *,
    actor: FormalPrincipal,
    read_scope: _ReadScope,
    tasks: tuple[FormalStocktakeTask, ...],
) -> tuple[_TaskGraph, ...]:
    if not tasks:
        return ()
    task_ids = tuple(row.id for row in tasks)
    rounds = tuple(
        db.scalars(
            select(StocktakeRound)
            .where(StocktakeRound.task_id.in_(task_ids))
            .order_by(StocktakeRound.task_id, StocktakeRound.round_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id.in_(task_ids))
            .order_by(FormalStocktakeScope.task_id, FormalStocktakeScope.scope_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    location_ids = tuple({row.location_id for row in scopes})
    location_tree_by_id = _load_location_tree(db, location_ids)
    locations = tuple(
        sorted(
            (
                location_tree_by_id[location_id]
                for location_id in location_ids
                if location_id in location_tree_by_id
            ),
            key=lambda row: str(row.id),
        )
    )
    location_by_id = {row.id: row for row in locations}
    organization_tree_by_id = _load_organization_tree(
        db,
        {
            *(row.region_org_id for row in tasks),
            *(row.owner_org_id for row in scopes),
            *(row.owner_org_id for row in location_tree_by_id.values()),
        },
    )
    current_round_by_task: dict[uuid.UUID, StocktakeRound] = {}
    round_count_by_task: dict[uuid.UUID, int] = {}
    task_by_id = {row.id: row for row in tasks}
    for row in rounds:
        round_count_by_task[row.task_id] = round_count_by_task.get(row.task_id, 0) + 1
        task = task_by_id[row.task_id]
        if row.round_no == task.current_round_no:
            if row.task_id in current_round_by_task:
                _invalid_evidence()
            current_round_by_task[row.task_id] = row
    current_round_ids = tuple(row.id for row in current_round_by_task.values())
    assignments = (
        tuple(
            db.scalars(
                select(StocktakeRecountScopeAssignment)
                .join(
                    StocktakeRecountCase,
                    StocktakeRecountCase.id
                    == StocktakeRecountScopeAssignment.recount_case_id,
                )
                .join(
                    StocktakeRound,
                    StocktakeRound.recount_case_id == StocktakeRecountCase.id,
                )
                .where(StocktakeRound.id.in_(current_round_ids))
                .order_by(
                    StocktakeRecountScopeAssignment.task_id,
                    StocktakeRecountScopeAssignment.scope_id,
                )
                .execution_options(populate_existing=True)
            ).all()
        )
        if current_round_ids
        else ()
    )
    observations = _current_round_rows(
        db,
        StocktakeCountObservation,
        current_round_ids,
        StocktakeCountObservation.round_id,
        StocktakeCountObservation.observation_no,
    )
    dispositions = _current_round_rows(
        db,
        StocktakeObservationDisposition,
        current_round_ids,
        StocktakeObservationDisposition.round_id,
        StocktakeObservationDisposition.observation_id,
    )
    completions = _current_round_rows(
        db,
        StocktakeScopeCountCompletion,
        current_round_ids,
        StocktakeScopeCountCompletion.round_id,
        StocktakeScopeCountCompletion.scope_id,
    )
    submissions = _current_round_rows(
        db,
        StocktakeRoundSubmission,
        current_round_ids,
        StocktakeRoundSubmission.round_id,
        StocktakeRoundSubmission.id,
    )
    difference_completions = _current_round_rows(
        db,
        StocktakeDifferenceSetCompletion,
        current_round_ids,
        StocktakeDifferenceSetCompletion.round_id,
        StocktakeDifferenceSetCompletion.id,
    )
    differences = _current_round_rows(
        db,
        StocktakeDifference,
        current_round_ids,
        StocktakeDifference.round_id,
        StocktakeDifference.difference_no,
    )
    reviews = _current_round_rows(
        db,
        StocktakeReview,
        current_round_ids,
        StocktakeReview.round_id,
        StocktakeReview.reviewed_at,
    )
    postings = _current_round_rows(
        db,
        StocktakePosting,
        current_round_ids,
        StocktakePosting.round_id,
        StocktakePosting.id,
    )
    establishments = _current_round_rows(
        db,
        InventoryOpeningEstablishment,
        current_round_ids,
        InventoryOpeningEstablishment.round_id,
        InventoryOpeningEstablishment.id,
    )

    def by_task(values):
        result: dict[uuid.UUID, list[object]] = {}
        for row in values:
            result.setdefault(row.task_id, []).append(row)
        return result

    scopes_by_task = by_task(scopes)
    assignments_by_task = by_task(assignments)
    observations_by_task = by_task(observations)
    dispositions_by_task = by_task(dispositions)
    completions_by_task = by_task(completions)
    submissions_by_task = by_task(submissions)
    difference_completions_by_task = by_task(difference_completions)
    differences_by_task = by_task(differences)
    reviews_by_task = by_task(reviews)
    postings_by_task = by_task(postings)
    establishments_by_task = by_task(establishments)
    graphs: list[_TaskGraph] = []
    for task in tasks:
        task_scopes = tuple(scopes_by_task.get(task.id, ()))
        _validate_task_scope_region_tree(
            task=task,
            scopes=task_scopes,
            location_tree_by_id=location_tree_by_id,
            organization_tree_by_id=organization_tree_by_id,
        )
        task_locations = tuple(
            location_by_id[row.location_id]
            for row in task_scopes
            if row.location_id in location_by_id
        )
        round_row = current_round_by_task.get(task.id)
        task_assignments = tuple(assignments_by_task.get(task.id, ()))
        visible_scopes = _visible_scopes(
            actor,
            read_scope,
            task_region_org_id=task.region_org_id,
            round_row=round_row,
            scopes=task_scopes,
            locations_by_id=location_by_id,
            assignments=task_assignments,
        )
        if not visible_scopes:
            _invalid_evidence()
        graph = _TaskGraph(
            task=task,
            round_row=round_row,
            scopes=task_scopes,
            locations=task_locations,
            visible_scopes=visible_scopes,
            recount_assignments=task_assignments,
            observations=tuple(observations_by_task.get(task.id, ())),
            dispositions=tuple(dispositions_by_task.get(task.id, ())),
            completions=tuple(completions_by_task.get(task.id, ())),
            submissions=tuple(submissions_by_task.get(task.id, ())),
            difference_completions=tuple(
                difference_completions_by_task.get(task.id, ())
            ),
            differences=tuple(differences_by_task.get(task.id, ())),
            reviews=tuple(reviews_by_task.get(task.id, ())),
            postings=tuple(postings_by_task.get(task.id, ())),
            establishments=tuple(establishments_by_task.get(task.id, ())),
        )
        _validate_graph_shape(
            graph,
            stored_round_count=round_count_by_task.get(task.id, 0),
        )
        graphs.append(graph)
    return tuple(graphs)


def _current_round_rows(db, model, round_ids, round_column, order_column):
    if not round_ids:
        return ()
    return tuple(
        db.scalars(
            select(model)
            .where(round_column.in_(round_ids))
            .order_by(round_column, order_column)
            .execution_options(populate_existing=True)
        ).all()
    )


def _load_location_tree(
    db: Session,
    location_ids: tuple[uuid.UUID, ...],
) -> dict[uuid.UUID, StockLocation]:
    discovered: dict[uuid.UUID, StockLocation] = {}
    pending = set(location_ids)
    while pending:
        current_ids = tuple(sorted(pending, key=str))
        pending.clear()
        rows = tuple(
            db.scalars(
                select(StockLocation)
                .where(StockLocation.id.in_(current_ids))
                .order_by(StockLocation.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        for row in rows:
            discovered[row.id] = row
            if row.parent_id is not None and row.parent_id not in discovered:
                pending.add(row.parent_id)
    return discovered


def _load_organization_tree(
    db: Session,
    organization_ids: set[uuid.UUID],
) -> dict[uuid.UUID, Organization]:
    discovered: dict[uuid.UUID, Organization] = {}
    pending = set(organization_ids)
    while pending:
        current_ids = tuple(sorted(pending, key=str))
        pending.clear()
        rows = tuple(
            db.scalars(
                select(Organization)
                .where(Organization.id.in_(current_ids))
                .order_by(Organization.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        for row in rows:
            discovered[row.id] = row
            if row.parent_id is not None and row.parent_id not in discovered:
                pending.add(row.parent_id)
    return discovered


def _validate_task_scope_region_tree(
    *,
    task: FormalStocktakeTask,
    scopes: tuple[FormalStocktakeScope, ...],
    location_tree_by_id: dict[uuid.UUID, StockLocation],
    organization_tree_by_id: dict[uuid.UUID, Organization],
) -> None:
    region = organization_tree_by_id.get(task.region_org_id)
    if (
        region is None
        or region.status != "active"
        or region.org_type != "region_company"
    ):
        _invalid_evidence()

    def organization_in_task_region(organization_id: uuid.UUID) -> bool:
        current_id: uuid.UUID | None = organization_id
        seen: set[uuid.UUID] = set()
        while current_id is not None:
            if current_id in seen:
                _invalid_evidence()
            seen.add(current_id)
            organization = organization_tree_by_id.get(current_id)
            if organization is None or organization.status != "active":
                return False
            if organization.id == task.region_org_id:
                return True
            current_id = organization.parent_id
        return False

    for scope in scopes:
        owner = organization_tree_by_id.get(scope.owner_org_id)
        location = location_tree_by_id.get(scope.location_id)
        if (
            owner is None
            or owner.status != "active"
            or owner.org_type != "region_company"
            or not organization_in_task_region(owner.id)
            or location is None
            or location.status != "active"
            or location.location_type not in {"region", "personal"}
        ):
            _invalid_evidence()
        current_location: StockLocation | None = location
        seen_locations: set[uuid.UUID] = set()
        while current_location is not None:
            if current_location.id in seen_locations:
                _invalid_evidence()
            seen_locations.add(current_location.id)
            if current_location.status != "active" or not organization_in_task_region(
                current_location.owner_org_id
            ):
                _invalid_evidence()
            if current_location.parent_id is None:
                current_location = None
            else:
                current_location = location_tree_by_id.get(
                    current_location.parent_id
                )
                if current_location is None:
                    _invalid_evidence()


def _validate_graph_shape(graph: _TaskGraph, *, stored_round_count: int) -> None:
    task = graph.task
    round_row = graph.round_row
    scope_ids = {row.id for row in graph.scopes}
    observation_ids = {row.id for row in graph.observations}
    observation_nos = {row.observation_no for row in graph.observations}
    disposition_observation_ids = {
        row.observation_id for row in graph.dispositions
    }
    completion_scope_ids = {row.scope_id for row in graph.completions}
    if task.status not in _TASK_STATUSES:
        _invalid_evidence()
    if (
        not graph.scopes
        or len(scope_ids) != len(graph.scopes)
        or {row.id for row in graph.locations}
        != {row.location_id for row in graph.scopes}
    ):
        _invalid_evidence()
    if task.current_round_no == 0:
        if round_row is not None or stored_round_count or any(
            (
                graph.recount_assignments,
                graph.observations,
                graph.dispositions,
                graph.completions,
                graph.submissions,
                graph.difference_completions,
                graph.differences,
                graph.reviews,
                graph.postings,
                graph.establishments,
            )
        ):
            _invalid_evidence()
        return
    if (
        round_row is None
        or stored_round_count != task.current_round_no
        or round_row.task_id != task.id
        or round_row.round_no != task.current_round_no
        or round_row.status == "superseded"
        or (round_row.round_no == 1) != (round_row.round_type == "initial")
    ):
        _invalid_evidence()
    if round_row.round_type == "recount":
        assignment_scope_ids = {row.scope_id for row in graph.recount_assignments}
        if (
            round_row.recount_case_id is None
            or len(assignment_scope_ids) != len(graph.recount_assignments)
            or assignment_scope_ids != scope_ids
            or any(
                row.task_id != task.id
                or row.recount_case_id != round_row.recount_case_id
                or row.source_round_id == round_row.id
                for row in graph.recount_assignments
            )
        ):
            _invalid_evidence()
    elif graph.recount_assignments or round_row.recount_case_id is not None:
        _invalid_evidence()
    observation_by_id = {row.id: row for row in graph.observations}
    if (
        len(observation_ids) != len(graph.observations)
        or len(observation_nos) != len(graph.observations)
        or any(
            row.task_id != task.id
            or row.round_id != round_row.id
            or row.scope_id not in scope_ids
            or row.observation_no <= 0
            or row.material_identifier_type
            not in {"sku_code", "qr_code", "external_code", "unknown"}
            or not row.material_identifier_raw.strip()
            or row.condition_code not in {"new", "used", "damaged", "scrapped"}
            or row.availability_bucket
            not in {
                "available",
                "reserved",
                "picking",
                "outbound",
                "in_transit",
                "arrived_pending",
                "frozen",
                "return_pending",
                "scrap_pending",
            }
            or row.verification_status not in {"verified", "pending_verification"}
            for row in graph.observations
        )
        or len(disposition_observation_ids) != len(graph.dispositions)
        or not disposition_observation_ids.issubset(observation_ids)
        or any(
            row.task_id != task.id
            or row.round_id != round_row.id
            or row.scope_id != observation_by_id[row.observation_id].scope_id
            or observation_by_id[row.observation_id].verification_status
            != "pending_verification"
            or row.disposition
            not in {
                "resolved_existing_master",
                "pending_verification",
                "requires_recount",
            }
            for row in graph.dispositions
        )
    ):
        _invalid_evidence()
    if (
        len(completion_scope_ids) != len(graph.completions)
        or not completion_scope_ids.issubset(scope_ids)
    ):
        _invalid_evidence()
    if round_row.status == "counting":
        if (
            task.status != "counting"
            or len(graph.completions) >= len(graph.scopes)
            or any(
                (
                    graph.submissions,
                    graph.difference_completions,
                    graph.differences,
                    graph.reviews,
                    graph.dispositions,
                    graph.postings,
                    graph.establishments,
                )
            )
        ):
            _invalid_evidence()
        return
    if round_row.status != "submitted" or task.status not in _SEALED_TASK_STATUSES:
        _invalid_evidence()
    if (
        len(graph.completions) != len(graph.scopes)
        or len(graph.submissions) != 1
        or len(graph.difference_completions) != 1
    ):
        _invalid_evidence()
    observed_differences = tuple(
        row for row in graph.differences if row.observed_line_id is not None
    )
    observed_difference_ids = {
        row.observed_line_id for row in observed_differences
    }
    if (
        len(observed_difference_ids) != len(observed_differences)
        or observed_difference_ids != observation_ids
        or any(
            row.observed_line_id not in observation_by_id
            or row.scope_id != observation_by_id[row.observed_line_id].scope_id
            for row in observed_differences
        )
        or (
            (graph.postings or graph.establishments)
            and task.status not in _TERMINAL_STATUSES
        )
    ):
        _invalid_evidence()
    submission = graph.submissions[0]
    difference_completion = graph.difference_completions[0]
    if (
        submission.task_id != task.id
        or submission.round_id != round_row.id
        or submission.scope_count != len(graph.completions)
        or submission.zero_scope_count
        != sum(1 for row in graph.completions if row.zero_confirmed)
        or submission.count_line_count
        != sum(row.count_line_count for row in graph.completions)
        or submission.observation_line_count
        != sum(row.observation_line_count for row in graph.completions)
        or submission.serial_count
        != sum(row.serial_count for row in graph.completions)
        or submission.total_counted_qty
        != sum((row.total_counted_qty for row in graph.completions), start=_ZERO)
        or difference_completion.round_submission_id != submission.id
        or difference_completion.difference_count != len(graph.differences)
        or difference_completion.physical_difference_count
        != sum(1 for row in graph.differences if row.difference_type != "control_unassigned")
        or difference_completion.control_difference_count
        != sum(1 for row in graph.differences if row.difference_type == "control_unassigned")
        or difference_completion.total_affected_qty
        != sum((row.affected_qty for row in graph.differences), start=_ZERO)
    ):
        _invalid_evidence()
    _validate_review_shape(graph)


def _validate_review_shape(graph: _TaskGraph) -> None:
    reviews_by_stage = {row.review_stage: row for row in graph.reviews}
    if len(reviews_by_stage) != len(graph.reviews) or not set(reviews_by_stage).issubset(
        {"region", "headquarters"}
    ):
        _invalid_evidence()
    region = reviews_by_stage.get("region")
    headquarters = reviews_by_stage.get("headquarters")
    if headquarters is not None and (
        region is None
        or region.decision != "approve"
        or headquarters.reviewed_at <= region.reviewed_at
    ):
        _invalid_evidence()
    expected_status = "submitted"
    if region is not None:
        expected_status = "hq_review" if region.decision == "approve" else "recount_required"
    if headquarters is not None:
        expected_status = "approved" if headquarters.decision == "approve" else "recount_required"
    if graph.task.status not in _TERMINAL_STATUSES and graph.task.status != expected_status:
        _invalid_evidence()
    if graph.task.status in _TERMINAL_STATUSES and expected_status != "approved":
        _invalid_evidence()


def _plan_detail_evidence(
    db: Session,
    graph: _TaskGraph,
    *,
    proof: _PrelockedOpeningReadBatchGraphProof,
) -> _OpeningReadDetailEvidencePlan:
    round_row = graph.round_row
    if round_row is None or graph.task.status in _TERMINAL_STATUSES:
        return _OpeningReadDetailEvidencePlan(
            task_id=graph.task.id,
            recount_plan=None,
            count_plan=None,
            disposition_replay_plans=(),
            review_plans=(),
        )
    checked_proof = _require_opening_read_batch_graph(db, proof)
    resolutions = _round_disposition_resolutions(checked_proof, round_row.id)
    task_resolutions = _task_disposition_resolutions(db, checked_proof, graph.task.id)
    from . import opening_stocktake_count as count_service
    from . import opening_observation_disposition as disposition_service
    from . import opening_stocktake_recount as recount_service
    from . import opening_stocktake_review as review_service

    recount_plan: object | None = None
    round_assignments: dict[uuid.UUID, StocktakeRecountScopeAssignment] = {}
    if round_row.round_type == "recount":
        freezes = tuple(
            db.scalars(
                select(InventoryFreeze)
                .where(InventoryFreeze.task_id == graph.task.id)
                .order_by(InventoryFreeze.stocktake_scope_id)
                .execution_options(populate_existing=True)
            ).all()
        )
        recount_plan = (
            recount_service._plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph(
                db,
                task=graph.task,
                round_row=round_row,
                scopes=graph.scopes,
                freezes=freezes,
                disposition_resolutions=task_resolutions,
            )
        )
        round_assignments = recount_service._opening_recount_assignments_from_plan(
            db,
            plan=recount_plan,
        )
    count_plan = (
        count_service._plan_opening_count_replay_evidence(
            db,
            graph.task,
            round_row,
            graph.scopes,
            graph.completions[0],
            round_assignments=round_assignments,
        )
        if graph.completions
        else None
    )
    disposition_replay_plans = _plan_detail_disposition_replays(
        db,
        graph=graph,
        round_row=round_row,
        resolutions=resolutions,
        disposition_service=disposition_service,
    )
    review_plans = tuple(
        review_service._plan_opening_review_evidence_from_prelocked_reference_graph(
            db,
            task_id=graph.task.id,
            round_id=round_row.id,
            review_id=review.id,
            expected_stage=review.review_stage,
            expected_decision=review.decision,
            disposition_resolutions=task_resolutions,
        )
        for review in graph.reviews
    )
    return _OpeningReadDetailEvidencePlan(
        task_id=graph.task.id,
        recount_plan=recount_plan,
        count_plan=count_plan,
        disposition_replay_plans=disposition_replay_plans,
        review_plans=review_plans,
    )


def _plan_detail_disposition_replays(
    db: Session,
    *,
    graph: _TaskGraph,
    round_row: StocktakeRound,
    resolutions: Mapping[uuid.UUID, object],
    disposition_service: object,
) -> tuple[tuple[uuid.UUID, object], ...]:
    """Capture all disposition cores and unique audit coordinates pre-audit."""

    if not graph.dispositions:
        return ()
    if len(graph.submissions) != 1 or len(graph.difference_completions) != 1:
        _invalid_evidence()
    difference_by_observation = {
        row.observed_line_id: row
        for row in graph.differences
        if row.observed_line_id is not None
    }
    observation_by_id = {row.id: row for row in graph.observations}
    plans: list[tuple[uuid.UUID, object]] = []
    for disposition in graph.dispositions:
        observation = observation_by_id.get(disposition.observation_id)
        difference = difference_by_observation.get(disposition.observation_id)
        resolution = resolutions.get(disposition.observation_id)
        if observation is None or difference is None or resolution is None:
            _invalid_evidence()
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
        command = disposition_service.RecordOpeningObservationDispositionCommand(
            task_id=graph.task.id,
            round_id=round_row.id,
            observation_id=observation.id,
            disposition=disposition.disposition,
            reason_code=disposition.reason_code,
            comment=disposition.comment,
            resolved_material_id=disposition.resolved_material_id,
            resolved_lot_id=disposition.resolved_lot_id,
            resolved_serial_id=disposition.resolved_serial_id,
        )
        request_sha256 = disposition_service._request_sha256(
            historical_actor,
            command,
        )
        plans.append(
            (
                disposition.observation_id,
                disposition_service._plan_opening_observation_disposition_replay(
                    db,
                    actor=historical_actor,
                    command=command,
                    row=disposition,
                    task=graph.task,
                    observation=observation,
                    difference=difference,
                    submission=graph.submissions[0],
                    difference_completion=graph.difference_completions[0],
                    key_hash=disposition.idempotency_key_hash,
                    request_sha256=request_sha256,
                    resolution=resolution,
                ),
            )
        )
    return tuple(sorted(plans, key=lambda item: str(item[0])))


def _reprove_detail_evidence(
    db: Session,
    graph: _TaskGraph,
    *,
    proof: _PrelockedOpeningReadBatchGraphProof,
    detail_plan: _OpeningReadDetailEvidencePlan,
    audit_proof: object,
) -> None:
    round_row = graph.round_row
    if round_row is None:
        if detail_plan.task_id != graph.task.id or any(
            (
                detail_plan.recount_plan,
                detail_plan.count_plan,
                detail_plan.disposition_replay_plans,
                detail_plan.review_plans,
            )
        ):
            _invalid_evidence()
        return
    # Terminal tasks are re-proved once by A's page-wide sealed batch proof.
    # Re-entering the public standalone terminal replay here would take ledger
    # and task roots after the shared audit owner and recreate the P0 inversion.
    if graph.task.status in _TERMINAL_STATUSES:
        if detail_plan.task_id != graph.task.id or any(
            (
                detail_plan.recount_plan,
                detail_plan.count_plan,
                detail_plan.disposition_replay_plans,
                detail_plan.review_plans,
            )
        ):
            _invalid_evidence()
        return
    checked_proof = _require_opening_read_batch_graph(db, proof)
    resolutions = _round_disposition_resolutions(checked_proof, round_row.id)
    # The remaining internal validators are pure over the already-pinned task,
    # principal, reference and serial graphs and the caller's one final audit
    # proof.  No nested validator may reacquire the audit head.
    try:
        from . import opening_stocktake_count as count_service
        from . import opening_observation_disposition as disposition_service
        from . import opening_stocktake_recount as recount_service
        from . import opening_stocktake_review as review_service

        if detail_plan.task_id != graph.task.id:
            _invalid_evidence()
        if round_row.round_type == "recount":
            if detail_plan.recount_plan is None:
                _invalid_evidence()
            recount_service._validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph(
                db,
                plan=detail_plan.recount_plan,
                audit_proof=audit_proof,
            )
        elif detail_plan.recount_plan is not None:
            _invalid_evidence()
        if graph.completions:
            if detail_plan.count_plan is None:
                _invalid_evidence()
            count_service._validate_opening_count_replay_evidence_from_prelocked_task_graph(
                db,
                plan=detail_plan.count_plan,
                audit_proof=audit_proof,
            )
        else:
            if detail_plan.count_plan is not None:
                _invalid_evidence()
            orphan_count = db.scalar(
                select(StocktakeCountLine.id)
                .where(StocktakeCountLine.round_id == round_row.id)
                .limit(1)
            )
            orphan_observation = db.scalar(
                select(StocktakeCountObservation.id)
                .where(StocktakeCountObservation.round_id == round_row.id)
                .limit(1)
            )
            if orphan_count is not None or orphan_observation is not None:
                _invalid_evidence()
        if {
            row.observation_id for row in graph.dispositions
        } != {
            observation_id
            for observation_id, _plan in detail_plan.disposition_replay_plans
        }:
            _invalid_evidence()
        for _observation_id, disposition_plan in (
            detail_plan.disposition_replay_plans
        ):
            disposition_service._validate_opening_observation_disposition_replay_from_prelocked_audit_graph(
                db,
                plan=disposition_plan,
                audit_proof=audit_proof,
            )
        if len(detail_plan.review_plans) != len(graph.reviews):
            _invalid_evidence()
        for review, review_plan in zip(
            graph.reviews,
            detail_plan.review_plans,
            strict=True,
        ):
            review_service._validate_opening_review_evidence_from_prelocked_task_graph(
                db,
                plan=review_plan,
                audit_proof=audit_proof,
            )
    except OpeningStocktakeReadError:
        raise
    except Exception:
        _invalid_evidence()


def _summary(
    db: Session,
    *,
    actor: FormalPrincipal,
    graph: _TaskGraph,
    reconciliation: reconciliation_service.OpeningControlReconciliationStatus,
) -> OpeningStocktakeTaskSummaryOut:
    evidence_status = _evidence_status(graph)
    visible_ids = {row.id for row in graph.visible_scopes}
    visible_completions = [
        row for row in graph.completions if row.scope_id in visible_ids
    ]
    return OpeningStocktakeTaskSummaryOut(
        task_id=graph.task.id,
        task_no=graph.task.task_no,
        region_org_id=graph.task.region_org_id,
        status=graph.task.status,
        blind_count=graph.task.blind_count,
        current_round_no=graph.task.current_round_no,
        current_round_status=(graph.round_row.status if graph.round_row else None),
        visible_scope_count=len(graph.visible_scopes),
        completed_scope_count=len(visible_completions),
        evidence_status=evidence_status,
        difference_count=(
            len(_visible_differences(graph)) if evidence_status == "sealed" else None
        ),
        reconciliation_status=reconciliation.status,
        reconciliation_run_id=reconciliation.reconciliation_run_id,
        pending_control_difference_count=(
            reconciliation.pending_control_difference_count
        ),
        task_version=graph.task.version,
        deadline=graph.task.deadline,
        allowed_actions=_allowed_actions(
            db,
            actor=actor,
            graph=graph,
            reconciliation=reconciliation,
        ),
    )


def _detail(
    db: Session,
    *,
    actor: FormalPrincipal,
    graph: _TaskGraph,
    reconciliation: reconciliation_service.OpeningControlReconciliationStatus,
) -> OpeningStocktakeTaskDetailOut:
    evidence_status = _evidence_status(graph)
    completion_by_scope = {row.scope_id: row for row in graph.completions}
    scopes: list[OpeningStocktakeScopeOut] = []
    for scope in graph.visible_scopes:
        completion = completion_by_scope.get(scope.id)
        reveal = evidence_status == "sealed" and completion is not None
        scopes.append(
            OpeningStocktakeScopeOut(
                scope_id=scope.id,
                scope_no=scope.scope_no,
                location_id=scope.location_id,
                owner_org_id=scope.owner_org_id,
                assigned_to_me=_scope_assigned_to_actor(
                    actor, graph.round_row, scope, graph.recount_assignments
                ),
                completion_status="completed" if completion else "pending",
                zero_confirmed=(completion.zero_confirmed if reveal else None),
                count_line_count=(completion.count_line_count if reveal else None),
                observation_line_count=(
                    completion.observation_line_count if reveal else None
                ),
                serial_count=(completion.serial_count if reveal else None),
                total_counted_qty=(
                    _quantity_text(completion.total_counted_qty) if reveal else None
                ),
                completed_at=(completion.completed_at if completion else None),
            )
        )
    differences = (
        [
            OpeningStocktakeDifferenceOut(
                difference_id=row.id,
                difference_no=row.difference_no,
                scope_id=row.scope_id,
                difference_type=row.difference_type,
                material_id=row.material_id,
                book_qty=_quantity_text(row.book_qty),
                counted_qty=_quantity_text(row.counted_qty),
                difference_qty=_quantity_text(row.difference_qty),
                affected_qty=_quantity_text(row.affected_qty),
                reason_code=row.reason_code,
                evidence_required=row.evidence_required,
            )
            for row in _visible_differences(graph)
        ]
        if evidence_status == "sealed"
        else []
    )
    observations: list[OpeningStocktakeObservationOut] = []
    if evidence_status == "sealed":
        visible_scope_ids = {row.id for row in graph.visible_scopes}
        disposition_by_observation = {
            row.observation_id: row for row in graph.dispositions
        }
        difference_by_observation = {
            row.observed_line_id: row
            for row in graph.differences
            if row.observed_line_id is not None
        }
        for row in graph.observations:
            if row.scope_id not in visible_scope_ids:
                continue
            difference = difference_by_observation.get(row.id)
            if difference is None:
                _invalid_evidence()
            disposition = disposition_by_observation.get(row.id)
            disposition_out = None
            if disposition is not None:
                disposition_out = OpeningObservationDispositionSummaryOut(
                    disposition_id=disposition.id,
                    disposition=disposition.disposition,
                    resolved_material_id=disposition.resolved_material_id,
                    resolved_lot_id=disposition.resolved_lot_id,
                    resolved_serial_id=disposition.resolved_serial_id,
                    reason_code=disposition.reason_code,
                    decided_at=disposition.decided_at,
                )
            observations.append(
                OpeningStocktakeObservationOut(
                    observation_id=row.id,
                    difference_id=difference.id,
                    observation_no=row.observation_no,
                    scope_id=row.scope_id,
                    material_identifier_type=row.material_identifier_type,
                    material_identifier_raw=row.material_identifier_raw,
                    condition_code=row.condition_code,
                    availability_bucket=row.availability_bucket,
                    counted_qty=_quantity_text(row.counted_qty),
                    verification_status=row.verification_status,
                    material_id=row.material_id,
                    lot_id=row.lot_id,
                    lot_no_raw=row.lot_no_raw,
                    serial_id=row.serial_id,
                    serial_no_raw=row.serial_no_raw,
                    serial_identifier_type=row.serial_identifier_type,
                    disposition=disposition_out,
                    allowed_dispositions=_allowed_observation_dispositions(
                        actor=actor,
                        graph=graph,
                        observation=row,
                        disposition=disposition,
                    ),
                )
            )
    round_out = None
    if graph.round_row is not None:
        round_out = OpeningStocktakeRoundOut(
            round_id=graph.round_row.id,
            round_no=graph.round_row.round_no,
            round_type=graph.round_row.round_type,
            status=graph.round_row.status,
            started_at=graph.round_row.started_at,
            submitted_at=graph.round_row.submitted_at,
        )
    return OpeningStocktakeTaskDetailOut(
        task_id=graph.task.id,
        task_no=graph.task.task_no,
        region_org_id=graph.task.region_org_id,
        status=graph.task.status,
        blind_count=graph.task.blind_count,
        task_version=graph.task.version,
        deadline=graph.task.deadline,
        cutoff_at=graph.task.cutoff_at,
        current_round=round_out,
        evidence_status=evidence_status,
        reconciliation_status=reconciliation.status,
        reconciliation_run_id=reconciliation.reconciliation_run_id,
        pending_control_difference_count=(
            reconciliation.pending_control_difference_count
        ),
        scopes=scopes,
        observations=observations,
        differences=differences,
        reviews=[
            OpeningStocktakeReviewSummaryOut(
                stage=row.review_stage,
                decision=row.decision,
                reviewed_at=row.reviewed_at,
            )
            for row in graph.reviews
        ],
        allowed_actions=_allowed_actions(
            db,
            actor=actor,
            graph=graph,
            reconciliation=reconciliation,
        ),
    )


def _allowed_observation_dispositions(
    *,
    actor: FormalPrincipal,
    graph: _TaskGraph,
    observation: StocktakeCountObservation,
    disposition: StocktakeObservationDisposition | None,
) -> list[str]:
    round_row = graph.round_row
    if (
        disposition is not None
        or observation.verification_status != "pending_verification"
        or round_row is None
        or graph.task.task_type != "opening"
        or graph.task.status != "submitted"
        or round_row.task_id != graph.task.id
        or round_row.round_no != graph.task.current_round_no
        or round_row.status != "submitted"
        or graph.reviews
        or graph.postings
        or graph.establishments
    ):
        return []
    if _has_exact_role_action(
        actor,
        action="manage",
        role_code="admin",
        scope_type="national",
        scope_id="*",
    ):
        return [
            "resolved_existing_master",
            "pending_verification",
            "requires_recount",
        ]
    if _has_exact_role_action(
        actor,
        action="manage",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(graph.task.region_org_id),
    ):
        return ["pending_verification", "requires_recount"]
    return []


def _visible_differences(graph: _TaskGraph) -> tuple[StocktakeDifference, ...]:
    if len(graph.visible_scopes) == len(graph.scopes):
        return graph.differences
    visible_ids = {row.id for row in graph.visible_scopes}
    return tuple(row for row in graph.differences if row.scope_id in visible_ids)


def _evidence_status(graph: _TaskGraph) -> OpeningEvidenceStatus:
    if graph.round_row is None:
        return "not_started"
    if graph.round_row.status == "counting":
        return "counting_hidden"
    return "sealed"


def _allowed_actions(
    db: Session,
    *,
    actor: FormalPrincipal,
    graph: _TaskGraph,
    reconciliation: reconciliation_service.OpeningControlReconciliationStatus,
) -> list[OpeningAllowedAction]:
    del db  # Entitlements were fully scope-bound by _require_stocktake_read.
    actions: list[OpeningAllowedAction] = []
    task = graph.task
    if task.status == "counting" and any(
        row.id not in {value.scope_id for value in graph.completions}
        and _scope_assigned_to_actor(
            actor, graph.round_row, row, graph.recount_assignments
        )
        for row in graph.visible_scopes
    ) and _has_action(actor, "count"):
        actions.append("count")
    if _region_review_preconditions_met(graph) and _has_exact_role_action(
        actor,
        action="review_region",
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(task.region_org_id),
    ):
        actions.append("review_region")
    if task.status == "hq_review" and _has_exact_role_action(
        actor,
        action="review_headquarters",
        role_code="admin",
        scope_type="national",
        scope_id="*",
    ):
        actions.append("review_headquarters")
    if task.status == "recount_required" and _has_manager_action(
        actor, action="manage", region_org_id=task.region_org_id
    ):
        actions.append("open_recount")
    if task.status == "approved" and _has_exact_role_action(
        actor,
        action="post_opening",
        role_code="admin",
        scope_type="national",
        scope_id="*",
    ):
        actions.append("post")
    if (
        task.status == "posted"
        and graph.establishments
        and reconciliation.status in {"not_required", "approved"}
        and _has_exact_role_action(
            actor,
            action="post_opening",
            role_code="admin",
            scope_type="national",
            scope_id="*",
        )
    ):
        actions.append("close")
    return actions


def _load_reconciliation_statuses(
    db: Session,
    graphs: tuple[_TaskGraph, ...],
    *,
    proof: _PrelockedOpeningReadBatchGraphProof,
    audit_proof: object,
) -> dict[uuid.UUID, reconciliation_service.OpeningControlReconciliationStatus]:
    """Purely load the task-wide statuses from the sealed batch proof."""

    if not graphs:
        return {}
    task_ids = tuple(graph.task.id for graph in graphs)
    checked = _require_opening_read_batch_graph(db, proof)
    if set(task_ids) != {row.id for row in checked.root.tasks}:
        _invalid_evidence()
    closed_task_ids = {
        row.id for row in checked.root.tasks if row.status == "closed"
    }
    try:
        statuses = (
            reconciliation_service._opening_control_reconciliation_statuses_from_prelocked_batch(
                db,
                proof=checked.reconciliation_graph,
                audit_proof=audit_proof,
            )
            if checked.reconciliation_graph is not None
            else {}
        )
        statuses.update(
            _roundless_reconciliation_statuses(
                db,
                task_ids=checked.roundless_reconciliation_task_ids,
            )
        )
    except Exception:
        _invalid_evidence()
    if set(statuses) != set(task_ids):
        _invalid_evidence()
    for task_id, value in statuses.items():
        if (
            task_id not in task_ids
            or not isinstance(
                value,
                reconciliation_service.OpeningControlReconciliationStatus,
            )
            or value.status not in {"not_required", "pending", "approved"}
            or (
                task_id in closed_task_ids
                and value.status not in {"not_required", "approved"}
            )
            or isinstance(value.pending_control_difference_count, bool)
            or not isinstance(value.pending_control_difference_count, int)
            or value.pending_control_difference_count < 0
            or (
                value.status == "not_required"
                and (
                    value.reconciliation_run_id is not None
                    or value.pending_control_difference_count != 0
                )
            )
            or (
                value.status == "approved"
                and (
                    not isinstance(value.reconciliation_run_id, uuid.UUID)
                    or value.pending_control_difference_count != 0
                )
            )
            or (
                value.status == "pending"
                and (
                    value.pending_control_difference_count <= 0
                    or (
                        value.reconciliation_run_id is not None
                        and not isinstance(
                            value.reconciliation_run_id,
                            uuid.UUID,
                        )
                    )
                )
            )
        ):
            _invalid_evidence()
    return statuses


def _region_review_preconditions_met(graph: _TaskGraph) -> bool:
    """Mirror the regional review service's task-wide observation gate.

    ``visible_scopes`` is intentionally not consulted here.  A regional
    review is one immutable decision over the complete current-round
    difference set, so one undisposed pending observation anywhere in the
    task must withhold the action even when that observation is outside the
    reader's visible scopes.  Graph validation proves disposition uniqueness
    and :func:`_reprove_detail_evidence` proves every returned disposition's
    manifest/audit chain before this helper is reached.
    """

    round_row = graph.round_row
    if (
        graph.task.status != "submitted"
        or round_row is None
        or round_row.task_id != graph.task.id
        or round_row.round_no != graph.task.current_round_no
        or round_row.status != "submitted"
        or graph.reviews
        or graph.postings
        or graph.establishments
    ):
        return False
    pending_observation_ids = {
        row.id
        for row in graph.observations
        if row.verification_status == "pending_verification"
    }
    disposition_observation_ids = {
        row.observation_id for row in graph.dispositions
    }
    return disposition_observation_ids == pending_observation_ids


def _has_manager_action(
    actor: FormalPrincipal, *, action: str, region_org_id: uuid.UUID
) -> bool:
    return _has_exact_role_action(
        actor,
        action=action,
        role_code="admin",
        scope_type="national",
        scope_id="*",
    ) or _has_exact_role_action(
        actor,
        action=action,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(region_org_id),
    )


def _has_action(actor: FormalPrincipal, action: str) -> bool:
    return any(
        entitlement.resource == "stocktake"
        and entitlement.action == action
        and entitlement.field_code == ""
        and entitlement.effect == "allow"
        and any(
            grant.assignment_id == entitlement.assignment_id
            and grant.role_code == entitlement.role_code
            and grant.scope_type == entitlement.scope_type
            and grant.scope_id == entitlement.scope_id
            for grant in actor.assignments
        )
        for entitlement in actor.entitlements
    ) and not any(
        row.resource == "stocktake"
        and row.action == action
        and row.field_code == ""
        and row.effect == "deny"
        for row in actor.entitlements
    )


def _has_exact_role_action(
    actor: FormalPrincipal,
    *,
    action: str,
    role_code: str,
    scope_type: str,
    scope_id: str,
) -> bool:
    grant_ids = {
        row.assignment_id
        for row in actor.assignments
        if row.role_code == role_code
        and row.scope_type == scope_type
        and row.scope_id == scope_id
    }
    return bool(grant_ids) and _has_action(actor, action) and any(
        row.assignment_id in grant_ids
        and row.role_code == role_code
        and row.scope_type == scope_type
        and row.scope_id == scope_id
        and row.resource == "stocktake"
        and row.action == action
        and row.field_code == ""
        and row.effect == "allow"
        for row in actor.entitlements
    )


def _require_stocktake_read(db: Session, actor: FormalPrincipal) -> _ReadScope:
    if (
        actor.access_mode != "active"
        or actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.authorization_version <= 0
    ):
        _read_denied()
    assignments = {row.assignment_id: row for row in actor.assignments}
    grants: list[ScopeGrant] = []
    for entitlement in actor.entitlements:
        if (
            entitlement.resource != "stocktake"
            or entitlement.action != "read"
            or entitlement.field_code != ""
            or entitlement.effect != "allow"
        ):
            continue
        assignment = assignments.get(entitlement.assignment_id)
        if assignment is None or not _same_grant(assignment, entitlement):
            continue
        if assignment.role_code == "admin":
            valid = assignment.scope_type == "national" and assignment.scope_id == "*"
        elif assignment.role_code == "provincial_manager":
            valid = assignment.scope_type == "organization" and _is_uuid(
                assignment.scope_id
            )
        elif assignment.role_code == "technician":
            valid = (
                assignment.scope_type == "person"
                and assignment.scope_id == str(actor.person_id)
            )
        else:
            valid = False
        if valid:
            grants.append(assignment)
    try:
        generally_allowed = actor.allows(db, "stocktake", "read")
    except FormalAccessError:
        _read_denied()
    if not grants or not generally_allowed:
        _read_denied()
    person = db.get(Person, actor.person_id)
    home_org = db.get(Organization, person.organization_id) if person else None
    if (
        person is None
        or person.employment_status != "active"
        or home_org is None
        or home_org.status != "active"
        or home_org.org_type
        not in {"headquarters", "region_company", "department"}
    ):
        _read_denied()
    admin = any(row.role_code == "admin" for row in grants)
    if admin and home_org.org_type != "headquarters":
        _read_denied()
    region_org_ids = frozenset(
        uuid.UUID(row.scope_id)
        for row in grants
        if row.role_code == "provincial_manager"
    )
    if region_org_ids:
        regions = tuple(
            db.scalars(
                select(Organization).where(Organization.id.in_(region_org_ids))
            ).all()
        )
        if len(regions) != len(region_org_ids) or any(
            row.status != "active" or row.org_type != "region_company"
            for row in regions
        ):
            _read_denied()
    return _ReadScope(
        grants=tuple(grants),
        admin=admin,
        region_org_ids=region_org_ids,
        technician=any(row.role_code == "technician" for row in grants),
    )


def _visible_task_predicate(actor: FormalPrincipal, scope: _ReadScope):
    predicates = []
    if scope.admin:
        predicates.append(FormalStocktakeTask.id.is_not(None))
    if scope.region_org_ids:
        predicates.append(
            FormalStocktakeTask.region_org_id.in_(scope.region_org_ids)
        )
    if scope.technician:
        initial_assignment = exists(
            select(FormalStocktakeScope.id)
            .join(
                StockLocation,
                StockLocation.id == FormalStocktakeScope.location_id,
            )
            .where(
                FormalStocktakeScope.task_id == FormalStocktakeTask.id,
                FormalStocktakeScope.assignee_user_id == actor.user_id,
                FormalStocktakeScope.custodian_person_id_snapshot
                == actor.person_id,
                StockLocation.location_type == "personal",
                StockLocation.custodian_person_id == actor.person_id,
            )
        )
        recount_assignment = exists(
            select(StocktakeRecountScopeAssignment.id)
            .join(
                StocktakeRound,
                StocktakeRound.recount_case_id
                == StocktakeRecountScopeAssignment.recount_case_id,
            )
            .join(
                FormalStocktakeScope,
                FormalStocktakeScope.id
                == StocktakeRecountScopeAssignment.scope_id,
            )
            .join(
                StockLocation,
                StockLocation.id == FormalStocktakeScope.location_id,
            )
            .where(
                StocktakeRound.task_id == FormalStocktakeTask.id,
                StocktakeRound.round_no == FormalStocktakeTask.current_round_no,
                StocktakeRound.round_type == "recount",
                StocktakeRecountScopeAssignment.task_id == FormalStocktakeTask.id,
                StocktakeRecountScopeAssignment.assignee_user_id == actor.user_id,
                StocktakeRecountScopeAssignment.assignee_person_id == actor.person_id,
                FormalStocktakeScope.task_id == FormalStocktakeTask.id,
                FormalStocktakeScope.custodian_person_id_snapshot
                == actor.person_id,
                StockLocation.location_type == "personal",
                StockLocation.custodian_person_id == actor.person_id,
            )
        )
        predicates.append(
            or_(
                FormalStocktakeTask.current_round_no <= 1,
                recount_assignment,
            )
            & or_(
                FormalStocktakeTask.current_round_no > 1,
                initial_assignment,
            )
        )
    if not predicates:
        _read_denied()
    return or_(*predicates)


def _visible_scopes(
    actor: FormalPrincipal,
    read_scope: _ReadScope,
    *,
    task_region_org_id: uuid.UUID,
    round_row: StocktakeRound | None,
    scopes: tuple[FormalStocktakeScope, ...],
    locations_by_id: dict[uuid.UUID, StockLocation],
    assignments: tuple[StocktakeRecountScopeAssignment, ...],
) -> tuple[FormalStocktakeScope, ...]:
    if read_scope.admin:
        return scopes
    if task_region_org_id in read_scope.region_org_ids:
        # _validate_task_scope_region_tree has already proved that every scope
        # owner and every physical-location ancestor belongs to this exact
        # task-region tree.  Use only the assignment selected by task region;
        # never union another provincial assignment to make a cross-region
        # scope visible.
        return scopes
    if not read_scope.technician:
        return ()
    if round_row is not None and round_row.round_type == "recount":
        visible_ids = {
            row.scope_id
            for row in assignments
            if row.assignee_user_id == actor.user_id
            and row.assignee_person_id == actor.person_id
        }
    else:
        visible_ids = {
            row.id
            for row in scopes
            if row.assignee_user_id == actor.user_id
            and row.custodian_person_id_snapshot == actor.person_id
        }
    return tuple(
        row
        for row in scopes
        if row.id in visible_ids
        and (location := locations_by_id.get(row.location_id)) is not None
        and location.location_type == "personal"
        and location.custodian_person_id == actor.person_id
    )


def _scope_assigned_to_actor(
    actor: FormalPrincipal,
    round_row: StocktakeRound | None,
    scope: FormalStocktakeScope,
    assignments: tuple[StocktakeRecountScopeAssignment, ...],
) -> bool:
    if round_row is not None and round_row.round_type == "recount":
        return any(
            row.scope_id == scope.id
            and row.assignee_user_id == actor.user_id
            and row.assignee_person_id == actor.person_id
            for row in assignments
        )
    return scope.assignee_user_id == actor.user_id


def _ledger_snapshot(db: Session) -> int:
    next_cursor = db.scalar(
        select(InventoryLedgerHead.next_cursor).where(
            InventoryLedgerHead.stream_key == "inventory"
        )
    )
    if not isinstance(next_cursor, int) or next_cursor <= 0:
        raise OpeningStocktakeReadError(
            code="opening_stocktake_ledger_unavailable",
            status_code=503,
            message="正式库存账本尚未安全初始化",
        )
    return next_cursor


def _snapshot(
    ledger_next_cursor: int, tasks: tuple[FormalStocktakeTask, ...]
) -> _ReadSnapshot:
    return _ReadSnapshot(
        ledger_next_cursor=ledger_next_cursor,
        tasks={row.id: _task_read_signature(row) for row in tasks},
    )


def _task_read_signature(task: FormalStocktakeTask) -> tuple[object, ...]:
    """Freeze every persisted task field used by visibility or rendering."""

    return (
        task.id,
        task.task_no,
        task.task_type,
        task.region_org_id,
        task.status,
        task.blind_count,
        task.cutoff_ledger_cursor,
        task.cutoff_at,
        task.scope_manifest_sha256,
        task.snapshot_manifest_sha256,
        task.control_source_system_id,
        task.control_sync_run_id,
        task.control_snapshot_at,
        task.control_manifest_sha256,
        task.current_round_no,
        task.created_by_user_id,
        task.deadline,
        task.issued_at,
        task.frozen_at,
        task.submitted_at,
        task.posted_at,
        task.closed_at,
        task.cancelled_at,
        task.version,
        task.note,
        task.created_at,
        task.updated_at,
    )


def _ensure_snapshot_current(db: Session, snapshot: _ReadSnapshot) -> None:
    next_cursor = db.scalar(
        select(InventoryLedgerHead.next_cursor).where(
            InventoryLedgerHead.stream_key == "inventory"
        )
    )
    current = {
        row.id: _task_read_signature(row)
        for row in db.scalars(
            select(FormalStocktakeTask)
            .where(FormalStocktakeTask.id.in_(tuple(snapshot.tasks)))
            .execution_options(populate_existing=True)
        ).all()
    }
    if next_cursor != snapshot.ledger_next_cursor or current != snapshot.tasks:
        _read_changed()


def _read_changed() -> None:
    raise OpeningStocktakeReadError(
        code="opening_stocktake_read_changed",
        status_code=409,
        message="期初盘点或库存账本在读取期间发生变化，请重新读取",
    )


def _ensure_observation_facts_current(
    db: Session,
    graphs: tuple[_TaskGraph, ...],
) -> None:
    """Reject a mixed snapshot if an observation/disposition changed mid-read.

    These facts are append-only, but recording a disposition intentionally does
    not advance the stock ledger or task version.  A second bulk fingerprint is
    therefore required before returning ``allowed_dispositions``; otherwise a
    concurrent immutable disposition could leave a stale action in the payload.
    """

    round_ids = tuple(
        graph.round_row.id for graph in graphs if graph.round_row is not None
    )
    if not round_ids:
        return
    expected_observations = {
        (
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
            row.count_method,
            row.reason_code,
            row.remark,
            row.counted_by_user_id,
            row.dimension_sha256,
            row.request_sha256,
            row.idempotency_key_hash,
            row.counted_at,
            row.created_at,
        )
        for graph in graphs
        for row in graph.observations
    }
    observation_columns = (
        StocktakeCountObservation.id,
        StocktakeCountObservation.task_id,
        StocktakeCountObservation.round_id,
        StocktakeCountObservation.scope_id,
        StocktakeCountObservation.observation_no,
        StocktakeCountObservation.owner_org_id,
        StocktakeCountObservation.location_id,
        StocktakeCountObservation.custodian_person_id_snapshot,
        StocktakeCountObservation.material_id,
        StocktakeCountObservation.material_identifier_raw,
        StocktakeCountObservation.material_identifier_type,
        StocktakeCountObservation.condition_code,
        StocktakeCountObservation.availability_bucket,
        StocktakeCountObservation.lot_id,
        StocktakeCountObservation.lot_no_raw,
        StocktakeCountObservation.serial_id,
        StocktakeCountObservation.serial_no_raw,
        StocktakeCountObservation.serial_identifier_type,
        StocktakeCountObservation.counted_qty,
        StocktakeCountObservation.verification_status,
        StocktakeCountObservation.count_method,
        StocktakeCountObservation.reason_code,
        StocktakeCountObservation.remark,
        StocktakeCountObservation.counted_by_user_id,
        StocktakeCountObservation.dimension_sha256,
        StocktakeCountObservation.request_sha256,
        StocktakeCountObservation.idempotency_key_hash,
        StocktakeCountObservation.counted_at,
        StocktakeCountObservation.created_at,
    )
    actual_observations = {
        tuple(row)
        for row in db.execute(
            select(*observation_columns).where(
                StocktakeCountObservation.round_id.in_(round_ids)
            )
        )
    }
    expected_dispositions = {
        (
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
            row.created_at,
        )
        for graph in graphs
        for row in graph.dispositions
    }
    disposition_columns = (
        StocktakeObservationDisposition.id,
        StocktakeObservationDisposition.task_id,
        StocktakeObservationDisposition.round_id,
        StocktakeObservationDisposition.scope_id,
        StocktakeObservationDisposition.observation_id,
        StocktakeObservationDisposition.disposition,
        StocktakeObservationDisposition.resolved_material_id,
        StocktakeObservationDisposition.resolved_lot_id,
        StocktakeObservationDisposition.resolved_serial_id,
        StocktakeObservationDisposition.reason_code,
        StocktakeObservationDisposition.comment,
        StocktakeObservationDisposition.disposition_manifest_sha256,
        StocktakeObservationDisposition.request_sha256,
        StocktakeObservationDisposition.idempotency_key_hash,
        StocktakeObservationDisposition.decided_by_user_id,
        StocktakeObservationDisposition.decided_by_person_id,
        StocktakeObservationDisposition.decided_role_assignment_id,
        StocktakeObservationDisposition.authorization_version,
        StocktakeObservationDisposition.role_code,
        StocktakeObservationDisposition.scope_type,
        StocktakeObservationDisposition.scope_id_snapshot,
        StocktakeObservationDisposition.authorization_sha256,
        StocktakeObservationDisposition.decided_at,
        StocktakeObservationDisposition.created_at,
    )
    actual_dispositions = {
        tuple(row)
        for row in db.execute(
            select(*disposition_columns).where(
                StocktakeObservationDisposition.round_id.in_(round_ids)
            )
        )
    }
    if (
        actual_observations != expected_observations
        or actual_dispositions != expected_dispositions
    ):
        raise OpeningStocktakeReadError(
            code="opening_stocktake_read_changed",
            status_code=409,
            message="期初盘点或库存账本在读取期间发生变化，请重新读取",
        )


def _quantity_text(value: Decimal) -> str:
    try:
        checked = Decimal(value)
        quantized = checked.quantize(Decimal("0.001"))
    except Exception:
        _invalid_evidence()
    if not checked.is_finite() or quantized != checked:
        _invalid_evidence()
    return format(quantized, ".3f")


def _same_grant(grant: ScopeGrant, entitlement: Entitlement) -> bool:
    return (
        grant.role_code == entitlement.role_code
        and grant.scope_type == entitlement.scope_type
        and grant.scope_id == entitlement.scope_id
    )


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (TypeError, ValueError, AttributeError):
        return False
    return True


def _read_denied() -> None:
    raise OpeningStocktakeReadError(
        code="opening_stocktake_read_denied",
        status_code=403,
        message="没有期初盘点读取权限",
    )


def _invalid_evidence() -> None:
    raise OpeningStocktakeReadError(
        code="opening_stocktake_evidence_invalid",
        status_code=503,
        message="期初盘点封存证据不完整或相互矛盾，已停止读取",
    )


__all__ = [
    "OpeningStocktakeReadError",
    "list_opening_stocktakes",
    "opening_stocktake_detail",
]
