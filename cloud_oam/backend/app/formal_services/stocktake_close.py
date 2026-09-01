"""Independent local reconciliation and close for non-opening stocktakes.

The two public commands are deliberately separate.  ``reconcile`` appends an
immutable proof and advances only the optimistic task version while the task
remains ``posted``.  ``close`` accepts only the latest proof, re-proves it at
the exact current global ledger cursor, appends a separate close fact and then
moves ``posted -> closed``.  Neither command emits notifications, creates an
outbox event, calls an external client or commits the caller's transaction.
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

from sqlalchemy import func, or_, select, text
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
    Organization,
    Person,
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
from ..models import User
from ..inventory_models import (
    InventoryLedgerHead,
    InventoryMovement,
    InventoryMovementSerial,
    InventoryTransaction,
    SerialCurrentPosition,
    StockAccount,
    StockBalance,
)
from ..stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    StocktakeCloseCompletion,
    StocktakeCloseReconciliationAccount,
    StocktakeCloseReconciliationCompletion,
    StocktakeCloseReconciliationSerial,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeCountSerial,
    StocktakeDifference,
    StocktakeEffectiveApprovalCompletion,
    StocktakePostingCompletion,
    StocktakePostingCompletionItem,
    StocktakeRound,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)
from . import inventory_posting as inventory_service
from . import stocktake_posting as posting_service
from .audit_chain import (
    AuditChainError,
    _lock_audit_chain_head_with_proof,
    _verify_audit_event_with_prelocked_proof,
    append_audit_event,
)
from .postgresql_lock_graph import (
    lock_inventory_reference_graph,
    lock_inventory_serial_graph,
    lock_nonopening_stocktake_close_graph,
)


NON_OPENING_TYPES: Final[frozenset[str]] = posting_service.NON_OPENING_TYPES
INVENTORY_STREAM_KEY: Final[str] = "inventory"
_ZERO = Decimal("0.000")
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


class StocktakeCloseError(RuntimeError):
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
class ReconcileStocktakeForCloseCommand:
    task_id: uuid.UUID
    expected_task_version: int


@dataclass(frozen=True, slots=True)
class CloseReconciledStocktakeCommand:
    task_id: uuid.UUID
    expected_task_version: int


@dataclass(frozen=True, slots=True)
class StocktakeCloseReconciliationResult:
    completion_id: uuid.UUID
    task_id: uuid.UUID
    posting_completion_id: uuid.UUID
    reconciliation_no: int
    reconciliation_ledger_cursor: int
    resulting_task_status: str
    task_version: int
    scope_count: int
    account_count: int
    scoped_account_count: int
    serial_count: int
    transaction_count: int
    movement_count: int
    book_total_qty: Decimal
    physical_total_qty: Decimal
    reconciled_at: datetime
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class StocktakeCloseResult:
    completion_id: uuid.UUID
    task_id: uuid.UUID
    reconciliation_completion_id: uuid.UUID
    reconciliation_no: int
    reconciliation_ledger_cursor: int
    resulting_task_status: str
    task_version: int
    closed_at: datetime
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _AccountProof:
    account: StockAccount
    scope_id: uuid.UUID | None
    effective_round_id: uuid.UUID | None
    account_role: str
    count_ledger_cursor: int | None
    book_qty_at_count: Decimal | None
    physical_qty_at_count: Decimal | None
    ledger_delta_after_count: Decimal | None
    physical_delta_after_count: Decimal | None
    expected_physical_qty: Decimal | None
    ledger_qty: Decimal
    balance_qty: Decimal
    last_touch_ledger_cursor: int
    balance_ledger_cursor: int | None


@dataclass(frozen=True, slots=True)
class _SerialProof:
    serial_id: uuid.UUID
    evidence_scope_id: uuid.UUID | None
    effective_round_id: uuid.UUID | None
    count_ledger_cursor: int | None
    physical_present_at_count: bool | None
    physical_account_id_at_count: uuid.UUID | None
    expected_current_account_id: uuid.UUID | None
    ledger_last_movement_id: uuid.UUID
    current_position_account_id: uuid.UUID | None
    current_position_last_movement_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class _ReconciliationPlan:
    account_rows: tuple[_AccountProof, ...]
    serial_rows: tuple[_SerialProof, ...]
    transaction_count: int
    movement_count: int
    book_total_qty: Decimal
    physical_total_qty: Decimal


def reconcile_posted_stocktake_for_close(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: ReconcileStocktakeForCloseCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeCloseReconciliationResult:
    """Append one current local reconciliation proof without closing."""

    try:
        return _reconcile(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    except StocktakeCloseError:
        raise
    except (
        posting_service.StocktakeDifferencePostingError,
        inventory_service.InventoryPostingError,
        AuditChainError,
    ) as exc:
        _fail(
            "stocktake_close_reconciliation_source_invalid",
            "precondition_failed",
            "盘点审批、过账或审计来源无法重证",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "stocktake_close_reconciliation_concurrent_conflict",
            "conflict",
            "盘点内部对账发生并发冲突，请回滚并重新读取",
            cause=exc,
        )
    except DBAPIError as exc:
        _fail(
            "stocktake_close_reconciliation_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了盘点内部对账",
            cause=exc,
        )
    raise AssertionError("unreachable stocktake reconciliation boundary")


def close_reconciled_stocktake(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: CloseReconciledStocktakeCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeCloseResult:
    """Close only against the exact latest, still-current local proof."""

    try:
        return _close(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    except StocktakeCloseError:
        raise
    except (
        posting_service.StocktakeDifferencePostingError,
        inventory_service.InventoryPostingError,
        AuditChainError,
    ) as exc:
        _fail(
            "stocktake_close_source_invalid",
            "precondition_failed",
            "盘点过账或内部对账来源无法重证，任务未关闭",
            cause=exc,
        )
    except IntegrityError as exc:
        _fail(
            "stocktake_close_concurrent_conflict",
            "conflict",
            "盘点关闭发生并发冲突，请回滚并重新读取",
            cause=exc,
        )
    except DBAPIError as exc:
        _fail(
            "stocktake_close_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了盘点关闭",
            cause=exc,
        )
    raise AssertionError("unreachable stocktake close boundary")


def _reconcile(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: ReconcileStocktakeForCloseCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeCloseReconciliationResult:
    supplied = _validate_actor(actor)
    checked = _validate_command(command, ReconcileStocktakeForCloseCommand)
    raw_key, secret, trace_id = _validate_transport(
        idempotency_key, idempotency_hmac_secret, trace_request_id
    )
    path = f"/api/v1/stocktakes/{checked.task_id}/reconcile"
    key_hash = _idempotency_hmac(secret, supplied.user_id, path, raw_key)
    _take_advisory_locks(
        db,
        (
            _lock_coordinate("stocktake-close-reconcile-idempotency", key_hash),
            _lock_coordinate("stocktake-close-task", str(checked.task_id)),
        ),
    )
    ledger = inventory_service._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_nonopening_stocktake_close_graph(db, checked.task_id)
    task = _lock_task(db, checked.task_id)
    lock_formal_principal_graph(db, (supplied.user_id,))
    now = _database_now(db)
    current = _require_current_admin(db, supplied, action="reconcile")
    assignment = _current_admin_assignment(db, current)
    posting, approval = _posting_root(db, task.id)
    request_hash = _request_sha256(
        kind="reconcile",
        actor=current,
        task_id=task.id,
        expected_version=checked.expected_task_version,
        posting=posting,
    )
    existing_by_key = tuple(
        db.scalars(
            select(StocktakeCloseReconciliationCompletion).where(
                StocktakeCloseReconciliationCompletion.idempotency_key_hash
                == key_hash
            )
        ).all()
    )
    reconciliations = _load_reconciliations(db, task.id)
    if len(existing_by_key) > 1:
        _evidence_invalid("盘点内部对账幂等事实不唯一")
    if existing_by_key:
        completion = existing_by_key[0]
        if (
            completion.task_id != task.id
            or completion.posting_completion_id != posting.id
            or completion.expected_task_version != checked.expected_task_version
            or completion.request_sha256 != request_hash
            or completion.reconciled_by_user_id != current.user_id
            or completion.reconciled_by_person_id != current.person_id
            or completion.reconciled_role_assignment_id != assignment.id
            or completion.authorization_version != current.authorization_version
        ):
            _fail(
                "stocktake_close_reconciliation_idempotency_conflict",
                "conflict",
                "幂等键已绑定不同盘点、版本或权限上下文",
            )
        if completion not in reconciliations:
            _evidence_invalid("盘点内部对账幂等事实不属于任务链")
        account_ids, serial_ids = _stored_coordinates(db, completion.id)
        lock_inventory_reference_graph(db, account_ids, now)
        lock_inventory_serial_graph(db, serial_ids)
        _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
            db, stream_key=INVENTORY_STREAM_KEY
        )
        _reprove_posting(db, task=task, posting=posting, approval=approval, audit_proof=audit_proof)
        _validate_reconciliation_chain(
            db,
            task=task,
            posting=posting,
            rows=reconciliations,
            audit_proof=audit_proof,
        )
        return _reconciliation_result(completion, replayed=True)

    if task.status != "posted" or task.closed_at is not None:
        _fail(
            "stocktake_close_reconciliation_task_not_posted",
            "precondition_failed",
            "仅已过账且未关闭的非期初盘点可执行内部对账",
        )
    if task.version != checked.expected_task_version:
        _fail(
            "stocktake_close_reconciliation_version_conflict",
            "conflict",
            "盘点任务版本已变化，请重新读取",
        )
    previous = reconciliations[-1] if reconciliations else None
    if previous is None:
        if task.version != posting.posted_task_version:
            _evidence_invalid("首次内部对账版本未直接承接过账完成版本")
    elif previous.reconciled_task_version != task.version:
        _evidence_invalid("盘点内部对账链尾版本与任务版本不一致")

    coordinates = _plan_coordinates(db, task=task, posting=posting, approval=approval)
    lock_inventory_reference_graph(db, coordinates[0], now)
    lock_inventory_serial_graph(db, coordinates[1])
    plan = _build_reconciliation_plan(
        db,
        task=task,
        posting=posting,
        approval=approval,
        reconciliation_cursor=ledger.current_ledger_cursor,
        expected_account_ids=coordinates[0],
        expected_serial_ids=coordinates[1],
        require_current_projection=True,
    )
    _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
        db, stream_key=INVENTORY_STREAM_KEY
    )
    _reprove_posting(db, task=task, posting=posting, approval=approval, audit_proof=audit_proof)
    _validate_reconciliation_chain(
        db,
        task=task,
        posting=posting,
        rows=reconciliations,
        audit_proof=audit_proof,
    )
    now = _database_now(db)
    current = _require_current_admin(db, supplied, action="reconcile")
    assignment = _current_admin_assignment(db, current, lock_row=False)
    if task.status != "posted" or task.version != checked.expected_task_version:
        _fail(
            "stocktake_close_reconciliation_version_conflict",
            "conflict",
            "盘点任务版本已变化，请重新读取",
        )
    if ledger.head.next_cursor - 1 != ledger.current_ledger_cursor:
        _fail(
            "stocktake_close_reconciliation_ledger_changed",
            "conflict",
            "库存总账在对账期间变化，请重新执行内部对账",
        )
    if task.posted_at is None or now <= _as_utc(task.posted_at) or (
        previous is not None and now <= _as_utc(previous.reconciled_at)
    ):
        _fail(
            "stocktake_close_reconciliation_clock_not_monotonic",
            "service_unavailable",
            "内部对账时间必须晚于过账及前一份对账证明",
        )
    completion = _persist_reconciliation(
        db,
        task=task,
        posting=posting,
        previous=previous,
        actor=current,
        assignment=assignment,
        plan=plan,
        ledger_cursor=ledger.current_ledger_cursor,
        key_hash=key_hash,
        request_hash=request_hash,
        trace_request_id=trace_id,
        now=now,
    )
    _validate_reconciliation_completion(
        db,
        task=task,
        posting=posting,
        completion=completion,
        audit_proof=audit_proof,
        require_current=True,
        current_ledger_cursor=ledger.current_ledger_cursor,
    )
    return _reconciliation_result(completion, replayed=False)


def _close(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: CloseReconciledStocktakeCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeCloseResult:
    supplied = _validate_actor(actor)
    checked = _validate_command(command, CloseReconciledStocktakeCommand)
    raw_key, secret, trace_id = _validate_transport(
        idempotency_key, idempotency_hmac_secret, trace_request_id
    )
    path = f"/api/v1/stocktakes/{checked.task_id}/close"
    key_hash = _idempotency_hmac(secret, supplied.user_id, path, raw_key)
    _take_advisory_locks(
        db,
        (
            _lock_coordinate("stocktake-close-idempotency", key_hash),
            _lock_coordinate("stocktake-close-task", str(checked.task_id)),
        ),
    )
    ledger = inventory_service._lock_inventory_ledger_head_for_atomic_batch(db)
    lock_nonopening_stocktake_close_graph(db, checked.task_id)
    task = _lock_task(db, checked.task_id)
    lock_formal_principal_graph(db, (supplied.user_id,))
    now = _database_now(db)
    current = _require_current_admin(db, supplied, action="close")
    assignment = _current_admin_assignment(db, current)
    posting, approval = _posting_root(db, task.id)
    reconciliations = _load_reconciliations(db, task.id)
    latest = reconciliations[-1] if reconciliations else None
    request_hash = _request_sha256(
        kind="close",
        actor=current,
        task_id=task.id,
        expected_version=checked.expected_task_version,
        posting=posting,
        reconciliation=latest,
    )
    closes = tuple(
        db.scalars(
            select(StocktakeCloseCompletion)
            .where(StocktakeCloseCompletion.task_id == task.id)
            .order_by(StocktakeCloseCompletion.id)
        ).all()
    )
    by_key = tuple(
        db.scalars(
            select(StocktakeCloseCompletion).where(
                StocktakeCloseCompletion.idempotency_key_hash == key_hash
            )
        ).all()
    )
    if len(closes) > 1 or len(by_key) > 1:
        _evidence_invalid("盘点关闭完成事实不唯一")
    replaying = bool(by_key)
    if replaying:
        close = by_key[0]
        if (
            not closes
            or closes[0].id != close.id
            or close.task_id != task.id
            or close.expected_task_version != checked.expected_task_version
            or close.request_sha256 != request_hash
            or close.closed_by_user_id != current.user_id
            or close.closed_by_person_id != current.person_id
            or close.closed_role_assignment_id != assignment.id
            or close.authorization_version != current.authorization_version
        ):
            _fail(
                "stocktake_close_idempotency_conflict",
                "conflict",
                "幂等键已绑定不同盘点、版本、对账或权限上下文",
            )
        latest = next(
            (row for row in reconciliations if row.id == close.reconciliation_completion_id),
            None,
        )
        if latest is None:
            _evidence_invalid("盘点关闭引用的内部对账事实缺失")
    else:
        if closes:
            _fail("stocktake_close_already_completed", "conflict", "盘点任务已由其他请求关闭")
        if task.status != "posted" or task.closed_at is not None:
            _fail("stocktake_close_task_not_posted", "precondition_failed", "仅已过账任务可关闭")
        if task.version != checked.expected_task_version:
            _fail("stocktake_close_version_conflict", "conflict", "盘点任务版本已变化，请重新读取")
        if latest is None or latest.reconciled_task_version != task.version:
            _fail(
                "stocktake_close_reconciliation_required",
                "precondition_failed",
                "任务必须先完成与当前版本对应的独立内部对账",
            )

    assert latest is not None
    account_ids, serial_ids = _stored_coordinates(db, latest.id)
    lock_inventory_reference_graph(db, account_ids, now)
    lock_inventory_serial_graph(db, serial_ids)
    _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
        db, stream_key=INVENTORY_STREAM_KEY
    )
    _reprove_posting(db, task=task, posting=posting, approval=approval, audit_proof=audit_proof)
    _validate_reconciliation_chain(
        db,
        task=task,
        posting=posting,
        rows=reconciliations,
        audit_proof=audit_proof,
    )
    if replaying:
        close = closes[0]
        _validate_close_completion(db, task=task, posting=posting, reconciliation=latest, close=close, audit_proof=audit_proof)
        return _close_result(close, replayed=True)

    if ledger.current_ledger_cursor != latest.reconciliation_ledger_cursor:
        _fail(
            "stocktake_close_reconciliation_stale",
            "precondition_failed",
            "内部对账后库存总账已变化，请重新执行内部对账",
        )
    _validate_reconciliation_completion(
        db,
        task=task,
        posting=posting,
        completion=latest,
        audit_proof=audit_proof,
        require_current=True,
        current_ledger_cursor=ledger.current_ledger_cursor,
    )
    now = _database_now(db)
    current = _require_current_admin(db, supplied, action="close")
    assignment = _current_admin_assignment(db, current, lock_row=False)
    if task.status != "posted" or task.version != checked.expected_task_version:
        _fail("stocktake_close_version_conflict", "conflict", "盘点任务版本已变化，请重新读取")
    if now <= _as_utc(latest.reconciled_at):
        _fail("stocktake_close_clock_not_monotonic", "service_unavailable", "关闭时间必须晚于独立内部对账")
    close = _persist_close(
        db,
        task=task,
        posting=posting,
        reconciliation=latest,
        actor=current,
        assignment=assignment,
        key_hash=key_hash,
        request_hash=request_hash,
        trace_request_id=trace_id,
        now=now,
    )
    _validate_close_completion(db, task=task, posting=posting, reconciliation=latest, close=close, audit_proof=audit_proof)
    return _close_result(close, replayed=False)


def _posting_root(
    db: Session, task_id: uuid.UUID
) -> tuple[StocktakePostingCompletion, StocktakeEffectiveApprovalCompletion]:
    postings = tuple(
        db.scalars(
            select(StocktakePostingCompletion)
            .where(StocktakePostingCompletion.task_id == task_id)
            .order_by(StocktakePostingCompletion.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(postings) != 1:
        _evidence_invalid("已过账盘点缺少唯一 0035 过账完成事实")
    posting = postings[0]
    approval = db.get(
        StocktakeEffectiveApprovalCompletion,
        posting.effective_approval_completion_id,
        populate_existing=True,
    )
    if approval is None or approval.task_id != task_id:
        _evidence_invalid("盘点过账完成事实未绑定有效审批封印")
    return posting, approval


def _reprove_posting(
    db: Session,
    *,
    task: FormalStocktakeTask,
    posting: StocktakePostingCompletion,
    approval: StocktakeEffectiveApprovalCompletion,
    audit_proof: object,
) -> None:
    if (
        task.task_type not in NON_OPENING_TYPES
        or task.status not in {"posted", "closed"}
        or task.posted_at is None
        or posting.posted_task_version > task.version
        or _as_utc(task.posted_at) != _as_utc(posting.posted_at)
    ):
        _evidence_invalid("盘点任务终态未承接 0035 过账完成事实")
    proxy = posting_service._ApprovedTaskView(
        id=task.id,
        task_type=task.task_type,
        status="posted",
        version=posting.posted_task_version,
        cutoff_ledger_cursor=task.cutoff_ledger_cursor,
        cutoff_at=task.cutoff_at,
        submitted_at=task.submitted_at,
        posted_at=task.posted_at,
        closed_at=None,
        current_round_no=task.current_round_no,
    )
    plan = posting_service._load_and_build_posting_plan(
        db,
        task=proxy,  # type: ignore[arg-type]
        expected_task_version=posting.expected_task_version,
        approval=approval,
        posted_replay=True,
    )
    posting_service._verify_source_audits(
        db, task=task, approval=approval, proof=audit_proof
    )
    posting_service._validate_persisted_posting_completion(
        db,
        task=proxy,  # type: ignore[arg-type]
        approval=approval,
        completion=posting,
        plan=plan,
        audit_proof=audit_proof,
    )


def _plan_coordinates(
    db: Session,
    *,
    task: FormalStocktakeTask,
    posting: StocktakePostingCompletion,
    approval: StocktakeEffectiveApprovalCompletion,
) -> tuple[tuple[uuid.UUID, ...], tuple[uuid.UUID, ...]]:
    del approval
    scopes = _load_scopes(db, task.id)
    scope_accounts = _scope_accounts(db, scopes)
    snapshot_rows = tuple(
        db.scalars(
            select(StocktakeSnapshotLine).where(
                StocktakeSnapshotLine.task_id == task.id
            )
        ).all()
    )
    account_ids = {
        *(row.id for row in scope_accounts),
        *(row.stock_account_id for row in snapshot_rows),
    }
    completion_items = tuple(
        db.scalars(
            select(StocktakePostingCompletionItem).where(
                StocktakePostingCompletionItem.completion_id == posting.id
            )
        ).all()
    )
    movement_ids = tuple(
        sorted(
            {
                row.inventory_movement_id
                for row in completion_items
                if row.inventory_movement_id is not None
            },
            key=str,
        )
    )
    posting_movements = tuple(
        db.scalars(
            select(InventoryMovement).where(InventoryMovement.id.in_(movement_ids))
        ).all()
    ) if movement_ids else ()
    if len(posting_movements) != len(movement_ids):
        _evidence_invalid("盘点过账移动端点缺失")
    for movement in posting_movements:
        if movement.from_account_id is not None:
            account_ids.add(movement.from_account_id)
        if movement.to_account_id is not None:
            account_ids.add(movement.to_account_id)

    # One immutable pass adds every counterpart of a movement touching the
    # required union.  The ledger head is already held by both callers, so the
    # coordinate set cannot expand concurrently before the locked reread.
    if account_ids:
        touching = tuple(
            db.scalars(
                select(InventoryMovement).where(
                    or_(
                        InventoryMovement.from_account_id.in_(tuple(account_ids)),
                        InventoryMovement.to_account_id.in_(tuple(account_ids)),
                    )
                )
            ).all()
        )
        for movement in touching:
            if movement.from_account_id is not None:
                account_ids.add(movement.from_account_id)
            if movement.to_account_id is not None:
                account_ids.add(movement.to_account_id)
    ordered_accounts = tuple(sorted(account_ids, key=str))
    accounts = tuple(
        db.scalars(
            select(StockAccount)
            .where(StockAccount.id.in_(ordered_accounts))
            .order_by(StockAccount.id)
        ).all()
    ) if ordered_accounts else ()
    if len(accounts) != len(ordered_accounts):
        _evidence_invalid("盘点对账账户坐标缺失")

    serial_ids: set[uuid.UUID] = set()
    for snapshot in snapshot_rows:
        serial_ids.update(_snapshot_serial_ids(snapshot))
    serial_ids.update(
        db.scalars(
            select(StocktakeCountSerial.serial_id)
            .join(
                StocktakeCountLine,
                StocktakeCountLine.id == StocktakeCountSerial.count_line_id,
            )
            .where(StocktakeCountLine.task_id == task.id)
        ).all()
    )
    serial_ids.update(
        value
        for value in db.scalars(
            select(StocktakeCountObservation.serial_id).where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.serial_id.is_not(None),
            )
        ).all()
        if value is not None
    )
    serial_ids.update(
        value
        for value in db.scalars(
            select(StocktakeDifference.serial_id).where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.serial_id.is_not(None),
            )
        ).all()
        if value is not None
    )
    if movement_ids:
        serial_ids.update(
            db.scalars(
                select(InventoryMovementSerial.serial_id).where(
                    InventoryMovementSerial.movement_id.in_(movement_ids)
                )
            ).all()
        )
    scoped_ids = tuple(row.id for row in scope_accounts)
    if scoped_ids:
        serial_ids.update(
            db.scalars(
                select(SerialCurrentPosition.serial_id).where(
                    SerialCurrentPosition.stock_account_id.in_(scoped_ids)
                )
            ).all()
        )
    if ordered_accounts:
        serial_ids.update(
            db.scalars(
                select(InventoryMovementSerial.serial_id)
                .join(
                    InventoryMovement,
                    InventoryMovement.id == InventoryMovementSerial.movement_id,
                )
                .where(
                    or_(
                        InventoryMovement.from_account_id.in_(ordered_accounts),
                        InventoryMovement.to_account_id.in_(ordered_accounts),
                    )
                )
            ).all()
        )
    return ordered_accounts, tuple(sorted(serial_ids, key=str))


def _build_reconciliation_plan(
    db: Session,
    *,
    task: FormalStocktakeTask,
    posting: StocktakePostingCompletion,
    approval: StocktakeEffectiveApprovalCompletion,
    reconciliation_cursor: int,
    expected_account_ids: Sequence[uuid.UUID],
    expected_serial_ids: Sequence[uuid.UUID],
    require_current_projection: bool,
) -> _ReconciliationPlan:
    del approval
    scopes = _load_scopes(db, task.id)
    scope_by_id = {row.id: row for row in scopes}
    accounts = tuple(
        db.scalars(
            select(StockAccount)
            .where(StockAccount.id.in_(tuple(expected_account_ids)))
            .order_by(StockAccount.id)
            .execution_options(populate_existing=True)
        ).all()
    ) if expected_account_ids else ()
    if tuple(row.id for row in accounts) != tuple(sorted(set(expected_account_ids), key=str)):
        _evidence_invalid("盘点对账账户坐标在锁定前后发生变化")
    account_by_id = {row.id: row for row in accounts}
    scoped_account_map = _map_accounts_to_scopes(accounts, scopes)

    effective = tuple(
        db.scalars(
            select(posting_service.StocktakeEffectiveApprovalScope)
            .where(
                posting_service.StocktakeEffectiveApprovalScope.completion_id
                == posting.effective_approval_completion_id
            )
            .order_by(posting_service.StocktakeEffectiveApprovalScope.scope_id)
        ).all()
    )
    effective_by_scope = {row.scope_id: row for row in effective}
    if set(effective_by_scope) != set(scope_by_id):
        _evidence_invalid("盘点有效审批未覆盖全部对账范围")
    count_completions = tuple(
        db.scalars(
            select(StocktakeScopeCountCompletion).where(
                StocktakeScopeCountCompletion.task_id == task.id
            )
        ).all()
    )
    completion_by_coordinate = {
        (row.scope_id, row.round_id): row for row in count_completions
    }
    if len(completion_by_coordinate) != len(count_completions):
        _evidence_invalid("盘点实盘完成游标不唯一")
    scope_count_cursor: dict[uuid.UUID, int] = {}
    for scope_id, selected in effective_by_scope.items():
        completion = completion_by_coordinate.get((scope_id, selected.source_round_id))
        if (
            completion is None
            or type(completion.count_ledger_cursor) is not int
            or completion.count_ledger_cursor < 0
            or completion.count_ledger_cursor > reconciliation_cursor
        ):
            _evidence_invalid("盘点有效范围缺少可重放的实盘账本游标")
        scope_count_cursor[scope_id] = completion.count_ledger_cursor

    count_lines = tuple(
        db.scalars(
            select(StocktakeCountLine)
            .where(StocktakeCountLine.task_id == task.id)
            .order_by(StocktakeCountLine.round_id, StocktakeCountLine.id)
        ).all()
    )
    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(StocktakeCountObservation.task_id == task.id)
            .order_by(StocktakeCountObservation.round_id, StocktakeCountObservation.id)
        ).all()
    )
    physical_qty: dict[uuid.UUID, Decimal] = {
        account_id: _ZERO for account_id in scoped_account_map
    }
    seen_physical_accounts: set[uuid.UUID] = set()
    physical_serial_account: dict[uuid.UUID, uuid.UUID] = {}
    physical_serial_scope: dict[uuid.UUID, uuid.UUID] = {}
    count_serial_rows = tuple(
        db.execute(
            select(StocktakeCountSerial, StocktakeCountLine)
            .join(
                StocktakeCountLine,
                StocktakeCountLine.id == StocktakeCountSerial.count_line_id,
            )
            .where(StocktakeCountLine.task_id == task.id)
            .order_by(StocktakeCountSerial.serial_id)
        ).all()
    )
    for line in count_lines:
        selected = effective_by_scope.get(line.scope_id)
        if selected is None or line.round_id != selected.source_round_id:
            continue
        if scoped_account_map.get(line.stock_account_id) != line.scope_id:
            _evidence_invalid("有效实盘账户超出对账范围")
        if line.stock_account_id in seen_physical_accounts:
            _evidence_invalid("同一对账账户存在重复实盘数量")
        seen_physical_accounts.add(line.stock_account_id)
        physical_qty[line.stock_account_id] = line.counted_qty
    for serial_row, line in count_serial_rows:
        selected = effective_by_scope.get(line.scope_id)
        if selected is None or line.round_id != selected.source_round_id:
            continue
        if serial_row.serial_id in physical_serial_account:
            _evidence_invalid("同一 SN 在有效实盘中重复出现")
        physical_serial_account[serial_row.serial_id] = line.stock_account_id
        physical_serial_scope[serial_row.serial_id] = line.scope_id
    for observation in observations:
        selected = effective_by_scope.get(observation.scope_id)
        if selected is None or observation.round_id != selected.source_round_id:
            continue
        if observation.verification_status != "verified" or observation.material_id is None:
            _evidence_invalid("有效盘点仍包含未核实现场观察")
        matches = tuple(
            row.id
            for row in accounts
            if scoped_account_map.get(row.id) == observation.scope_id
            and _account_matches_observation(row, observation)
        )
        if len(matches) != 1:
            _evidence_invalid("已核实现场观察无法唯一映射对账账户")
        account_id = matches[0]
        if account_id in seen_physical_accounts:
            _evidence_invalid("同一账户同时存在实盘行与现场观察")
        seen_physical_accounts.add(account_id)
        physical_qty[account_id] = observation.counted_qty
        if observation.serial_id is not None:
            if observation.serial_id in physical_serial_account:
                _evidence_invalid("同一 SN 在有效实盘中重复出现")
            physical_serial_account[observation.serial_id] = account_id
            physical_serial_scope[observation.serial_id] = observation.scope_id

    ledger_rows = _ledger_rows(db, tuple(account_by_id), reconciliation_cursor)
    own_transaction_ids = {
        row.inventory_transaction_id
        for row in db.scalars(
            select(StocktakePostingCompletionItem).where(
                StocktakePostingCompletionItem.completion_id == posting.id,
                StocktakePostingCompletionItem.inventory_transaction_id.is_not(None),
            )
        ).all()
        if row is not None
    }
    balances = {
        row.stock_account_id: row
        for row in db.scalars(
            select(StockBalance)
            .where(StockBalance.stock_account_id.in_(tuple(account_by_id)))
            .order_by(StockBalance.stock_account_id)
            .execution_options(populate_existing=True)
        ).all()
    } if account_by_id else {}
    account_proofs: list[_AccountProof] = []
    for account in accounts:
        scope_id = scoped_account_map.get(account.id)
        ledger_qty = _ZERO
        last_cursor = 0
        for transaction, movement in ledger_rows:
            delta = _movement_delta(movement, account.id)
            if delta:
                ledger_qty += delta
                last_cursor = transaction.ledger_cursor
        if ledger_qty < _ZERO:
            _evidence_invalid("不可变流水重算产生负库存")
        balance = balances.get(account.id)
        balance_qty = balance.quantity if balance is not None else _ZERO
        balance_cursor = balance.ledger_cursor if balance is not None else None
        if require_current_projection and (
            balance_qty != ledger_qty
            or (balance is None and (ledger_qty != _ZERO or last_cursor != 0))
            or (balance is not None and balance_cursor != last_cursor)
        ):
            _fail(
                "stocktake_close_reconciliation_balance_mismatch",
                "precondition_failed",
                "库存余额投影无法由不可变流水精确重建",
            )
        if scope_id is None:
            account_proofs.append(
                _AccountProof(
                    account=account,
                    scope_id=None,
                    effective_round_id=None,
                    account_role="posting_counterpart",
                    count_ledger_cursor=None,
                    book_qty_at_count=None,
                    physical_qty_at_count=None,
                    ledger_delta_after_count=None,
                    physical_delta_after_count=None,
                    expected_physical_qty=None,
                    ledger_qty=ledger_qty,
                    balance_qty=balance_qty,
                    last_touch_ledger_cursor=last_cursor,
                    balance_ledger_cursor=balance_cursor,
                )
            )
            continue
        count_cursor = scope_count_cursor[scope_id]
        book_at_count = _ZERO
        ledger_after = _ZERO
        physical_after = _ZERO
        for transaction, movement in ledger_rows:
            delta = _movement_delta(movement, account.id)
            if not delta:
                continue
            if transaction.ledger_cursor <= count_cursor:
                book_at_count += delta
            else:
                ledger_after += delta
                if transaction.id not in own_transaction_ids:
                    physical_after += delta
        expected_physical = physical_qty[account.id] + physical_after
        if (
            book_at_count < _ZERO
            or expected_physical < _ZERO
            or book_at_count + ledger_after != ledger_qty
            or expected_physical != ledger_qty
            or (require_current_projection and expected_physical != balance_qty)
        ):
            _fail(
                "stocktake_close_reconciliation_physical_mismatch",
                "precondition_failed",
                "盘点实物经合法后续流水推演后与当前账面不一致",
            )
        selected = effective_by_scope[scope_id]
        account_proofs.append(
            _AccountProof(
                account=account,
                scope_id=scope_id,
                effective_round_id=selected.source_round_id,
                account_role="scope",
                count_ledger_cursor=count_cursor,
                book_qty_at_count=book_at_count,
                physical_qty_at_count=physical_qty[account.id],
                ledger_delta_after_count=ledger_after,
                physical_delta_after_count=physical_after,
                expected_physical_qty=expected_physical,
                ledger_qty=ledger_qty,
                balance_qty=balance_qty,
                last_touch_ledger_cursor=last_cursor,
                balance_ledger_cursor=balance_cursor,
            )
        )

    serial_proofs = _build_serial_proofs(
        db,
        task=task,
        reconciliation_cursor=reconciliation_cursor,
        expected_serial_ids=expected_serial_ids,
        scoped_account_map=scoped_account_map,
        effective_by_scope=effective_by_scope,
        scope_count_cursor=scope_count_cursor,
        physical_serial_account=physical_serial_account,
        physical_serial_scope=physical_serial_scope,
        own_transaction_ids=own_transaction_ids,
        require_current=require_current_projection,
    )
    scoped_rows = tuple(row for row in account_proofs if row.scope_id is not None)
    return _ReconciliationPlan(
        account_rows=tuple(account_proofs),
        serial_rows=serial_proofs,
        transaction_count=len({row[0].id for row in ledger_rows}),
        movement_count=len({row[1].id for row in ledger_rows}),
        book_total_qty=sum((row.ledger_qty for row in scoped_rows), start=_ZERO),
        physical_total_qty=sum(
            (row.expected_physical_qty or _ZERO for row in scoped_rows), start=_ZERO
        ),
    )


def _build_serial_proofs(
    db: Session,
    *,
    task: FormalStocktakeTask,
    reconciliation_cursor: int,
    expected_serial_ids: Sequence[uuid.UUID],
    scoped_account_map: Mapping[uuid.UUID, uuid.UUID],
    effective_by_scope: Mapping[uuid.UUID, object],
    scope_count_cursor: Mapping[uuid.UUID, int],
    physical_serial_account: Mapping[uuid.UUID, uuid.UUID],
    physical_serial_scope: Mapping[uuid.UUID, uuid.UUID],
    own_transaction_ids: set[uuid.UUID],
    require_current: bool,
) -> tuple[_SerialProof, ...]:
    del task
    serial_ids = tuple(sorted(set(expected_serial_ids), key=str))
    if not serial_ids:
        return ()
    history = tuple(
        db.execute(
            select(InventoryTransaction, InventoryMovement, InventoryMovementSerial)
            .join(
                InventoryMovement,
                InventoryMovement.transaction_id == InventoryTransaction.id,
            )
            .join(
                InventoryMovementSerial,
                InventoryMovementSerial.movement_id == InventoryMovement.id,
            )
            .where(
                InventoryMovementSerial.serial_id.in_(serial_ids),
                InventoryTransaction.status == "posted",
                InventoryTransaction.ledger_cursor <= reconciliation_cursor,
            )
            .order_by(
                InventoryMovementSerial.serial_id,
                InventoryTransaction.ledger_cursor,
                InventoryMovement.line_no,
                InventoryMovement.id,
            )
        ).all()
    )
    by_serial: dict[
        uuid.UUID,
        list[tuple[InventoryTransaction, InventoryMovement]],
    ] = defaultdict(list)
    for transaction, movement, binding in history:
        if binding.transaction_id != transaction.id:
            _evidence_invalid("SN 流水绑定的交易坐标无效")
        by_serial[binding.serial_id].append((transaction, movement))
    positions = {
        row.serial_id: row
        for row in db.scalars(
            select(SerialCurrentPosition)
            .where(SerialCurrentPosition.serial_id.in_(serial_ids))
            .order_by(SerialCurrentPosition.serial_id)
            .execution_options(populate_existing=True)
        ).all()
    }
    proofs: list[_SerialProof] = []
    for serial_id in serial_ids:
        rows = by_serial.get(serial_id, [])
        if not rows:
            _evidence_invalid("对账 SN 缺少不可变库存流水")
        last_transaction, last_movement = rows[-1]
        ledger_account = last_movement.to_account_id
        position = positions.get(serial_id)
        if (
            position is None
            or position.last_movement_id != last_movement.id
            or position.stock_account_id != ledger_account
        ):
            if require_current:
                _fail(
                    "stocktake_close_reconciliation_serial_projection_mismatch",
                    "precondition_failed",
                    "SN 当前归属与最后已过账移动不一致",
                )
            _evidence_invalid("对账时封存的 SN 投影与流水不一致")

        scope_candidates = {
            physical_serial_scope[serial_id]
            for _ in (0,)
            if serial_id in physical_serial_scope
        }
        for transaction, movement in rows:
            if transaction.ledger_cursor > reconciliation_cursor:
                continue
            for account_id in (movement.from_account_id, movement.to_account_id):
                scope_id = scoped_account_map.get(account_id) if account_id else None
                if scope_id is not None and transaction.ledger_cursor <= scope_count_cursor[scope_id]:
                    scope_candidates.add(scope_id)
        if len(scope_candidates) > 1:
            _evidence_invalid("同一 SN 在有效实盘边界跨多个范围，禁止关闭")
        evidence_scope_id = next(iter(scope_candidates), None)
        effective_round_id: uuid.UUID | None = None
        count_cursor: int | None = None
        physical_present: bool | None = None
        physical_account: uuid.UUID | None = None
        expected_current = ledger_account
        if evidence_scope_id is not None:
            selected = effective_by_scope[evidence_scope_id]
            effective_round_id = selected.source_round_id  # type: ignore[attr-defined]
            count_cursor = scope_count_cursor[evidence_scope_id]
            physical_account = physical_serial_account.get(serial_id)
            if physical_account is not None and scoped_account_map.get(physical_account) != evidence_scope_id:
                _evidence_invalid("SN 实盘账户超出有效范围")
            physical_present = physical_account is not None
            expected_current = physical_account
            for transaction, movement in rows:
                if transaction.ledger_cursor <= count_cursor:
                    continue
                if transaction.id in own_transaction_ids:
                    continue
                if movement.from_account_id != expected_current:
                    _fail(
                        "stocktake_close_reconciliation_serial_physical_mismatch",
                        "precondition_failed",
                        "SN 实盘位置无法按合法后续流水连续推演",
                    )
                expected_current = movement.to_account_id
            if expected_current != ledger_account:
                _fail(
                    "stocktake_close_reconciliation_serial_physical_mismatch",
                    "precondition_failed",
                    "SN 实盘推演位置与当前账本位置不一致",
                )
        proofs.append(
            _SerialProof(
                serial_id=serial_id,
                evidence_scope_id=evidence_scope_id,
                effective_round_id=effective_round_id,
                count_ledger_cursor=count_cursor,
                physical_present_at_count=physical_present,
                physical_account_id_at_count=physical_account,
                expected_current_account_id=expected_current,
                ledger_last_movement_id=last_movement.id,
                current_position_account_id=position.stock_account_id,
                current_position_last_movement_id=position.last_movement_id,
            )
        )
    return tuple(proofs)


def _persist_reconciliation(
    db: Session,
    *,
    task: FormalStocktakeTask,
    posting: StocktakePostingCompletion,
    previous: StocktakeCloseReconciliationCompletion | None,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    plan: _ReconciliationPlan,
    ledger_cursor: int,
    key_hash: str,
    request_hash: str,
    trace_request_id: str,
    now: datetime,
) -> StocktakeCloseReconciliationCompletion:
    completion_id = uuid.uuid4()
    reconciliation_no = 1 if previous is None else previous.reconciliation_no + 1
    account_documents = tuple(
        _account_document(completion_id, task.id, row) for row in plan.account_rows
    )
    serial_documents = tuple(
        _serial_document(completion_id, task.id, row) for row in plan.serial_rows
    )
    account_manifest = _sha256(
        {
            "accounts": account_documents,
            "schema": "cloud_oam.stocktake.close_reconciliation.accounts.v1",
            "task_id": str(task.id),
        }
    )
    serial_manifest = _sha256(
        {
            "schema": "cloud_oam.stocktake.close_reconciliation.serials.v1",
            "serials": serial_documents,
            "task_id": str(task.id),
        }
    )
    manifest = _reconciliation_manifest(
        completion_id=completion_id,
        task_id=task.id,
        posting=posting,
        previous=previous,
        reconciliation_no=reconciliation_no,
        expected_task_version=task.version,
        reconciled_task_version=task.version + 1,
        ledger_cursor=ledger_cursor,
        plan=plan,
        account_manifest=account_manifest,
        serial_manifest=serial_manifest,
    )
    authorization_hash = _authorization_sha256(
        kind="reconcile",
        actor=actor,
        assignment=assignment,
        occurred_at=now,
    )
    completion = StocktakeCloseReconciliationCompletion(
        id=completion_id,
        task_id=task.id,
        posting_completion_id=posting.id,
        previous_reconciliation_id=previous.id if previous else None,
        reconciliation_no=reconciliation_no,
        expected_task_version=task.version,
        reconciled_task_version=task.version + 1,
        reconciliation_ledger_cursor=ledger_cursor,
        scope_count=posting.scope_count,
        account_count=len(plan.account_rows),
        scoped_account_count=sum(row.scope_id is not None for row in plan.account_rows),
        serial_count=len(plan.serial_rows),
        transaction_count=plan.transaction_count,
        movement_count=plan.movement_count,
        book_total_qty=plan.book_total_qty,
        physical_total_qty=plan.physical_total_qty,
        posting_manifest_sha256=posting.posting_manifest_sha256,
        account_manifest_sha256=account_manifest,
        serial_manifest_sha256=serial_manifest,
        reconciliation_manifest_sha256=manifest,
        request_sha256=request_hash,
        idempotency_key_hash=key_hash,
        reconciled_by_user_id=actor.user_id,
        reconciled_by_person_id=actor.person_id,
        reconciled_role_assignment_id=assignment.id,
        authorization_version=actor.authorization_version,
        role_code="admin",
        scope_type="national",
        scope_id_snapshot="*",
        authorization_sha256=authorization_hash,
        reconciled_at=now,
        created_at=now,
    )
    db.add(completion)
    db.flush()
    for proof, document in zip(plan.account_rows, account_documents, strict=True):
        db.add(
            StocktakeCloseReconciliationAccount(
                completion_id=completion.id,
                stock_account_id=proof.account.id,
                task_id=task.id,
                scope_id=proof.scope_id,
                effective_round_id=proof.effective_round_id,
                account_role=proof.account_role,
                count_ledger_cursor=proof.count_ledger_cursor,
                book_qty_at_count=proof.book_qty_at_count,
                physical_qty_at_count=proof.physical_qty_at_count,
                ledger_delta_after_count=proof.ledger_delta_after_count,
                physical_delta_after_count=proof.physical_delta_after_count,
                expected_physical_qty=proof.expected_physical_qty,
                ledger_qty=proof.ledger_qty,
                balance_qty=proof.balance_qty,
                last_touch_ledger_cursor=proof.last_touch_ledger_cursor,
                balance_ledger_cursor=proof.balance_ledger_cursor,
                account_dimension_sha256=_account_dimension_sha256(proof.account),
                item_manifest_sha256=_sha256(document),
                created_at=now,
            )
        )
    for proof, document in zip(plan.serial_rows, serial_documents, strict=True):
        db.add(
            StocktakeCloseReconciliationSerial(
                completion_id=completion.id,
                serial_id=proof.serial_id,
                task_id=task.id,
                evidence_scope_id=proof.evidence_scope_id,
                effective_round_id=proof.effective_round_id,
                count_ledger_cursor=proof.count_ledger_cursor,
                physical_present_at_count=proof.physical_present_at_count,
                physical_account_id_at_count=proof.physical_account_id_at_count,
                expected_current_account_id=proof.expected_current_account_id,
                ledger_last_movement_id=proof.ledger_last_movement_id,
                current_position_account_id=proof.current_position_account_id,
                current_position_last_movement_id=proof.current_position_last_movement_id,
                item_manifest_sha256=_sha256(document),
                created_at=now,
            )
        )
    db.flush()
    metadata = _reconciliation_event_metadata(completion)
    append_audit_event(
        db,
        stream_key=INVENTORY_STREAM_KEY,
        actor_user_id=actor.user_id,
        action="stocktake.nonopening.close_reconciliation_recorded",
        aggregate_type="stocktake_close_reconciliation",
        aggregate_id=str(completion.id),
        before_jsonb={"status": "posted", "task_version": task.version},
        after_jsonb=metadata,
        request_id=_request_reference("reconcile", trace_request_id),
        occurred_at=now,
    )
    task.version += 1
    task.updated_at = now
    db.flush()
    return completion


def _persist_close(
    db: Session,
    *,
    task: FormalStocktakeTask,
    posting: StocktakePostingCompletion,
    reconciliation: StocktakeCloseReconciliationCompletion,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    key_hash: str,
    request_hash: str,
    trace_request_id: str,
    now: datetime,
) -> StocktakeCloseCompletion:
    close_id = uuid.uuid4()
    close_manifest = _sha256(
        {
            "close_id": str(close_id),
            "closed_task_version": task.version + 1,
            "expected_task_version": task.version,
            "posting_completion_id": str(posting.id),
            "posting_manifest_sha256": posting.posting_manifest_sha256,
            "reconciliation_completion_id": str(reconciliation.id),
            "reconciliation_ledger_cursor": reconciliation.reconciliation_ledger_cursor,
            "reconciliation_manifest_sha256": reconciliation.reconciliation_manifest_sha256,
            "reconciliation_no": reconciliation.reconciliation_no,
            "schema": "cloud_oam.stocktake.nonopening_close_completion.v1",
            "task_id": str(task.id),
        }
    )
    close = StocktakeCloseCompletion(
        id=close_id,
        task_id=task.id,
        posting_completion_id=posting.id,
        reconciliation_completion_id=reconciliation.id,
        reconciliation_no=reconciliation.reconciliation_no,
        reconciliation_ledger_cursor=reconciliation.reconciliation_ledger_cursor,
        expected_task_version=task.version,
        closed_task_version=task.version + 1,
        posting_manifest_sha256=posting.posting_manifest_sha256,
        reconciliation_manifest_sha256=reconciliation.reconciliation_manifest_sha256,
        close_manifest_sha256=close_manifest,
        request_sha256=request_hash,
        idempotency_key_hash=key_hash,
        closed_by_user_id=actor.user_id,
        closed_by_person_id=actor.person_id,
        closed_role_assignment_id=assignment.id,
        authorization_version=actor.authorization_version,
        role_code="admin",
        scope_type="national",
        scope_id_snapshot="*",
        authorization_sha256=_authorization_sha256(
            kind="close", actor=actor, assignment=assignment, occurred_at=now
        ),
        closed_at=now,
        created_at=now,
    )
    db.add(close)
    db.flush()
    metadata = _close_event_metadata(close)
    db.add(
        StateTransitionEvent(
            aggregate_type="stocktake_task",
            aggregate_id=str(task.id),
            from_status="posted",
            to_status="closed",
            reason="nonopening_stocktake_closed_after_internal_reconciliation",
            actor_id=actor.user_id,
            idempotency_key=_event_key("close-state", close.id),
            occurred_at=now,
            metadata_jsonb=metadata,
            created_at=now,
        )
    )
    append_audit_event(
        db,
        stream_key=INVENTORY_STREAM_KEY,
        actor_user_id=actor.user_id,
        action="stocktake.nonopening.closed",
        aggregate_type="stocktake_close_completion",
        aggregate_id=str(close.id),
        before_jsonb={"status": "posted", "task_version": task.version},
        after_jsonb=metadata,
        request_id=_request_reference("close", trace_request_id),
        occurred_at=now,
    )
    task.status = "closed"
    task.closed_at = now
    task.version += 1
    task.updated_at = now
    db.flush()
    return close


def _validate_reconciliation_chain(
    db: Session,
    *,
    task: FormalStocktakeTask,
    posting: StocktakePostingCompletion,
    rows: Sequence[StocktakeCloseReconciliationCompletion],
    audit_proof: object,
) -> None:
    previous: StocktakeCloseReconciliationCompletion | None = None
    expected_version = posting.posted_task_version
    for index, row in enumerate(rows, start=1):
        if (
            row.task_id != task.id
            or row.posting_completion_id != posting.id
            or row.posting_manifest_sha256 != posting.posting_manifest_sha256
            or row.reconciliation_no != index
            or row.previous_reconciliation_id
            != (previous.id if previous is not None else None)
            or row.expected_task_version != expected_version
            or row.reconciled_task_version != expected_version + 1
            or (previous is not None and row.reconciliation_ledger_cursor < previous.reconciliation_ledger_cursor)
        ):
            _evidence_invalid("盘点内部对账链序号、前驱、版本或游标不连续")
        _validate_reconciliation_completion(
            db,
            task=task,
            posting=posting,
            completion=row,
            audit_proof=audit_proof,
            require_current=False,
            current_ledger_cursor=None,
        )
        previous = row
        expected_version = row.reconciled_task_version
    if rows:
        latest = rows[-1]
        if task.status == "posted" and latest.reconciled_task_version != task.version:
            _evidence_invalid("盘点任务版本未承接内部对账链尾")
        if task.status == "closed" and latest.reconciled_task_version >= task.version:
            _evidence_invalid("盘点关闭版本未后继内部对账链尾")
    elif task.version != posting.posted_task_version:
        _evidence_invalid("无内部对账时盘点任务版本不得偏离过账版本")


def _validate_reconciliation_completion(
    db: Session,
    *,
    task: FormalStocktakeTask,
    posting: StocktakePostingCompletion,
    completion: StocktakeCloseReconciliationCompletion,
    audit_proof: object,
    require_current: bool,
    current_ledger_cursor: int | None,
) -> None:
    accounts = tuple(
        db.scalars(
            select(StocktakeCloseReconciliationAccount)
            .where(
                StocktakeCloseReconciliationAccount.completion_id == completion.id
            )
            .order_by(StocktakeCloseReconciliationAccount.stock_account_id)
        ).all()
    )
    serials = tuple(
        db.scalars(
            select(StocktakeCloseReconciliationSerial)
            .where(
                StocktakeCloseReconciliationSerial.completion_id == completion.id
            )
            .order_by(StocktakeCloseReconciliationSerial.serial_id)
        ).all()
    )
    account_master = {
        row.id: row
        for row in db.scalars(
            select(StockAccount)
            .where(StockAccount.id.in_(tuple(row.stock_account_id for row in accounts)))
            .order_by(StockAccount.id)
        ).all()
    } if accounts else {}
    if len(account_master) != len(accounts):
        _evidence_invalid("盘点内部对账账户主数据缺失")
    account_docs: list[dict[str, object]] = []
    for row in accounts:
        account = account_master[row.stock_account_id]
        proof = _AccountProof(
            account=account,
            scope_id=row.scope_id,
            effective_round_id=row.effective_round_id,
            account_role=row.account_role,
            count_ledger_cursor=row.count_ledger_cursor,
            book_qty_at_count=row.book_qty_at_count,
            physical_qty_at_count=row.physical_qty_at_count,
            ledger_delta_after_count=row.ledger_delta_after_count,
            physical_delta_after_count=row.physical_delta_after_count,
            expected_physical_qty=row.expected_physical_qty,
            ledger_qty=row.ledger_qty,
            balance_qty=row.balance_qty,
            last_touch_ledger_cursor=row.last_touch_ledger_cursor,
            balance_ledger_cursor=row.balance_ledger_cursor,
        )
        document = _account_document(completion.id, task.id, proof)
        if (
            row.account_dimension_sha256 != _account_dimension_sha256(account)
            or row.item_manifest_sha256 != _sha256(document)
            or row.ledger_qty != row.balance_qty
            or (
                row.scope_id is not None
                and row.expected_physical_qty != row.ledger_qty
            )
        ):
            _evidence_invalid("盘点内部对账账户事实摘要或守恒关系无效")
        ledger_qty, last_cursor = _rebuild_account_ledger(
            db, account.id, completion.reconciliation_ledger_cursor
        )
        if ledger_qty != row.ledger_qty or last_cursor != row.last_touch_ledger_cursor:
            _evidence_invalid("盘点内部对账账户无法由封存游标前流水重建")
        account_docs.append(document)
    serial_docs: list[dict[str, object]] = []
    for row in serials:
        proof = _SerialProof(
            serial_id=row.serial_id,
            evidence_scope_id=row.evidence_scope_id,
            effective_round_id=row.effective_round_id,
            count_ledger_cursor=row.count_ledger_cursor,
            physical_present_at_count=row.physical_present_at_count,
            physical_account_id_at_count=row.physical_account_id_at_count,
            expected_current_account_id=row.expected_current_account_id,
            ledger_last_movement_id=row.ledger_last_movement_id,
            current_position_account_id=row.current_position_account_id,
            current_position_last_movement_id=row.current_position_last_movement_id,
        )
        document = _serial_document(completion.id, task.id, proof)
        last = _last_serial_movement(
            db, row.serial_id, completion.reconciliation_ledger_cursor
        )
        if (
            last is None
            or last.id != row.ledger_last_movement_id
            or last.to_account_id != row.expected_current_account_id
            or row.current_position_last_movement_id != row.ledger_last_movement_id
            or row.current_position_account_id != row.expected_current_account_id
            or row.item_manifest_sha256 != _sha256(document)
        ):
            _evidence_invalid("盘点内部对账 SN 事实无法由封存游标前流水重建")
        serial_docs.append(document)
    account_manifest = _sha256(
        {
            "accounts": tuple(account_docs),
            "schema": "cloud_oam.stocktake.close_reconciliation.accounts.v1",
            "task_id": str(task.id),
        }
    )
    serial_manifest = _sha256(
        {
            "schema": "cloud_oam.stocktake.close_reconciliation.serials.v1",
            "serials": tuple(serial_docs),
            "task_id": str(task.id),
        }
    )
    plan = _ReconciliationPlan(
        account_rows=(),
        serial_rows=(),
        transaction_count=completion.transaction_count,
        movement_count=completion.movement_count,
        book_total_qty=completion.book_total_qty,
        physical_total_qty=completion.physical_total_qty,
    )
    expected_manifest = _reconciliation_manifest(
        completion_id=completion.id,
        task_id=task.id,
        posting=posting,
        previous=(
            db.get(
                StocktakeCloseReconciliationCompletion,
                completion.previous_reconciliation_id,
            )
            if completion.previous_reconciliation_id is not None
            else None
        ),
        reconciliation_no=completion.reconciliation_no,
        expected_task_version=completion.expected_task_version,
        reconciled_task_version=completion.reconciled_task_version,
        ledger_cursor=completion.reconciliation_ledger_cursor,
        plan=plan,
        account_manifest=account_manifest,
        serial_manifest=serial_manifest,
        account_count=len(accounts),
        scoped_account_count=sum(row.scope_id is not None for row in accounts),
        serial_count=len(serials),
    )
    if (
        completion.task_id != task.id
        or completion.posting_completion_id != posting.id
        or completion.scope_count != posting.scope_count
        or completion.account_count != len(accounts)
        or completion.scoped_account_count
        != sum(row.scope_id is not None for row in accounts)
        or completion.serial_count != len(serials)
        or completion.account_manifest_sha256 != account_manifest
        or completion.serial_manifest_sha256 != serial_manifest
        or completion.reconciliation_manifest_sha256 != expected_manifest
        or completion.book_total_qty != completion.physical_total_qty
        or completion.role_code != "admin"
        or completion.scope_type != "national"
        or completion.scope_id_snapshot != "*"
        or completion.authorization_sha256
        != _authorization_document_sha256(
            kind="reconcile",
            user_id=completion.reconciled_by_user_id,
            person_id=completion.reconciled_by_person_id,
            assignment_id=completion.reconciled_role_assignment_id,
            authorization_version=completion.authorization_version,
            occurred_at=completion.reconciled_at,
        )
    ):
        _evidence_invalid("盘点内部对账完成事实汇总、权限或摘要无效")
    events = tuple(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.stream_key == INVENTORY_STREAM_KEY,
                AuditEvent.action
                == "stocktake.nonopening.close_reconciliation_recorded",
                AuditEvent.aggregate_type == "stocktake_close_reconciliation",
                AuditEvent.aggregate_id == str(completion.id),
            )
        ).all()
    )
    if len(events) != 1 or events[0].after_jsonb != _reconciliation_event_metadata(completion):
        _evidence_invalid("盘点内部对账审计事实缺失、不唯一或摘要不一致")
    _verify_audit_event_with_prelocked_proof(
        db,
        proof=audit_proof,
        stream_key=INVENTORY_STREAM_KEY,
        event_id=events[0].id,
    )
    if require_current:
        if current_ledger_cursor != completion.reconciliation_ledger_cursor:
            _fail(
                "stocktake_close_reconciliation_stale",
                "precondition_failed",
                "内部对账后库存总账已变化，请重新执行内部对账",
            )
        account_ids = tuple(row.stock_account_id for row in accounts)
        serial_ids = tuple(row.serial_id for row in serials)
        current_plan = _build_reconciliation_plan(
            db,
            task=task,
            posting=posting,
            approval=db.get(
                StocktakeEffectiveApprovalCompletion,
                posting.effective_approval_completion_id,
            ),
            reconciliation_cursor=completion.reconciliation_ledger_cursor,
            expected_account_ids=account_ids,
            expected_serial_ids=serial_ids,
            require_current_projection=True,
        )
        if (
            tuple(
                _account_document(completion.id, task.id, row)
                for row in current_plan.account_rows
            )
            != tuple(account_docs)
            or tuple(
                _serial_document(completion.id, task.id, row)
                for row in current_plan.serial_rows
            )
            != tuple(serial_docs)
            or current_plan.transaction_count != completion.transaction_count
            or current_plan.movement_count != completion.movement_count
        ):
            _fail(
                "stocktake_close_reconciliation_projection_changed",
                "precondition_failed",
                "内部对账封存后账、物、流水或 SN 投影已变化",
            )


def _validate_close_completion(
    db: Session,
    *,
    task: FormalStocktakeTask,
    posting: StocktakePostingCompletion,
    reconciliation: StocktakeCloseReconciliationCompletion,
    close: StocktakeCloseCompletion,
    audit_proof: object,
) -> None:
    expected_manifest = _sha256(
        {
            "close_id": str(close.id),
            "closed_task_version": close.closed_task_version,
            "expected_task_version": close.expected_task_version,
            "posting_completion_id": str(posting.id),
            "posting_manifest_sha256": posting.posting_manifest_sha256,
            "reconciliation_completion_id": str(reconciliation.id),
            "reconciliation_ledger_cursor": reconciliation.reconciliation_ledger_cursor,
            "reconciliation_manifest_sha256": reconciliation.reconciliation_manifest_sha256,
            "reconciliation_no": reconciliation.reconciliation_no,
            "schema": "cloud_oam.stocktake.nonopening_close_completion.v1",
            "task_id": str(task.id),
        }
    )
    metadata = _close_event_metadata(close)
    states = tuple(
        db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id == str(task.id),
                StateTransitionEvent.reason
                == "nonopening_stocktake_closed_after_internal_reconciliation",
            )
        ).all()
    )
    audits = tuple(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.stream_key == INVENTORY_STREAM_KEY,
                AuditEvent.action == "stocktake.nonopening.closed",
                AuditEvent.aggregate_type == "stocktake_close_completion",
                AuditEvent.aggregate_id == str(close.id),
            )
        ).all()
    )
    if (
        task.status != "closed"
        or task.closed_at is None
        or task.version != close.closed_task_version
        or _as_utc(task.closed_at) != _as_utc(close.closed_at)
        or close.task_id != task.id
        or close.posting_completion_id != posting.id
        or close.reconciliation_completion_id != reconciliation.id
        or close.reconciliation_no != reconciliation.reconciliation_no
        or close.reconciliation_ledger_cursor != reconciliation.reconciliation_ledger_cursor
        or close.expected_task_version != reconciliation.reconciled_task_version
        or close.closed_task_version != close.expected_task_version + 1
        or close.posting_manifest_sha256 != posting.posting_manifest_sha256
        or close.reconciliation_manifest_sha256
        != reconciliation.reconciliation_manifest_sha256
        or close.close_manifest_sha256 != expected_manifest
        or close.authorization_sha256
        != _authorization_document_sha256(
            kind="close",
            user_id=close.closed_by_user_id,
            person_id=close.closed_by_person_id,
            assignment_id=close.closed_role_assignment_id,
            authorization_version=close.authorization_version,
            occurred_at=close.closed_at,
        )
        or len(states) != 1
        or states[0].from_status != "posted"
        or states[0].to_status != "closed"
        or states[0].actor_id != close.closed_by_user_id
        or states[0].metadata_jsonb != metadata
        or len(audits) != 1
        or audits[0].after_jsonb != metadata
    ):
        _evidence_invalid("盘点关闭完成、状态转换或审计事实无效")
    _verify_audit_event_with_prelocked_proof(
        db,
        proof=audit_proof,
        stream_key=INVENTORY_STREAM_KEY,
        event_id=audits[0].id,
    )


def _load_scopes(db: Session, task_id: uuid.UUID) -> tuple[FormalStocktakeScope, ...]:
    rows = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task_id)
            .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if not rows or len({row.id for row in rows}) != len(rows):
        _evidence_invalid("盘点对账范围缺失或不唯一")
    return rows


def _scope_accounts(
    db: Session, scopes: Sequence[FormalStocktakeScope]
) -> tuple[StockAccount, ...]:
    predicates = []
    for scope in scopes:
        values = [
            StockAccount.owner_org_id == scope.owner_org_id,
            StockAccount.location_id == scope.location_id,
        ]
        if scope.material_id is not None:
            values.append(StockAccount.material_id == scope.material_id)
        if scope.condition_code is not None:
            values.append(StockAccount.condition_code == scope.condition_code)
        if scope.availability_bucket is not None:
            values.append(
                StockAccount.availability_bucket == scope.availability_bucket
            )
        from sqlalchemy import and_

        predicates.append(and_(*values))
    rows = tuple(
        db.scalars(
            select(StockAccount)
            .where(or_(*predicates))
            .order_by(StockAccount.id)
            .execution_options(populate_existing=True)
        ).all()
    ) if predicates else ()
    _map_accounts_to_scopes(rows, scopes)
    return rows


def _map_accounts_to_scopes(
    accounts: Sequence[StockAccount],
    scopes: Sequence[FormalStocktakeScope],
) -> dict[uuid.UUID, uuid.UUID]:
    result: dict[uuid.UUID, uuid.UUID] = {}
    for account in accounts:
        matches = tuple(
            scope.id
            for scope in scopes
            if account.owner_org_id == scope.owner_org_id
            and account.location_id == scope.location_id
            and (scope.material_id is None or account.material_id == scope.material_id)
            and (
                scope.condition_code is None
                or account.condition_code == scope.condition_code
            )
            and (
                scope.availability_bucket is None
                or account.availability_bucket == scope.availability_bucket
            )
        )
        if len(matches) > 1:
            _evidence_invalid("同一库存账户重复落入多个盘点范围")
        if matches:
            result[account.id] = matches[0]
    return result


def _account_matches_observation(
    account: StockAccount, observation: StocktakeCountObservation
) -> bool:
    return bool(
        observation.material_id is not None
        and account.owner_org_id == observation.owner_org_id
        and account.location_id == observation.location_id
        and account.custodian_person_id
        == observation.custodian_person_id_snapshot
        and account.material_id == observation.material_id
        and account.condition_code == observation.condition_code
        and account.availability_bucket == observation.availability_bucket
        and account.lot_id == observation.lot_id
    )


def _ledger_rows(
    db: Session,
    account_ids: Sequence[uuid.UUID],
    cursor: int,
) -> tuple[tuple[InventoryTransaction, InventoryMovement], ...]:
    if not account_ids:
        return ()
    rows = tuple(
        db.execute(
            select(InventoryTransaction, InventoryMovement)
            .join(
                InventoryMovement,
                InventoryMovement.transaction_id == InventoryTransaction.id,
            )
            .where(
                InventoryTransaction.status == "posted",
                InventoryTransaction.ledger_cursor <= cursor,
                or_(
                    InventoryMovement.from_account_id.in_(tuple(account_ids)),
                    InventoryMovement.to_account_id.in_(tuple(account_ids)),
                ),
            )
            .order_by(
                InventoryTransaction.ledger_cursor,
                InventoryMovement.line_no,
                InventoryMovement.id,
            )
        ).all()
    )
    seen_cursor: dict[int, uuid.UUID] = {}
    for transaction, movement in rows:
        if (
            type(transaction.ledger_cursor) is not int
            or transaction.ledger_cursor <= 0
            or movement.quantity <= _ZERO
            or (
                transaction.ledger_cursor in seen_cursor
                and seen_cursor[transaction.ledger_cursor] != transaction.id
            )
        ):
            _evidence_invalid("库存账本游标、交易或移动证据无效")
        seen_cursor[transaction.ledger_cursor] = transaction.id
    return rows


def _movement_delta(movement: InventoryMovement, account_id: uuid.UUID) -> Decimal:
    value = _ZERO
    if movement.from_account_id == account_id:
        value -= movement.quantity
    if movement.to_account_id == account_id:
        value += movement.quantity
    return value


def _rebuild_account_ledger(
    db: Session, account_id: uuid.UUID, cursor: int
) -> tuple[Decimal, int]:
    rows = _ledger_rows(db, (account_id,), cursor)
    quantity = _ZERO
    last_cursor = 0
    for transaction, movement in rows:
        delta = _movement_delta(movement, account_id)
        if delta:
            quantity += delta
            last_cursor = transaction.ledger_cursor
    if quantity < _ZERO:
        _evidence_invalid("封存游标前流水重算产生负库存")
    return quantity, last_cursor


def _last_serial_movement(
    db: Session, serial_id: uuid.UUID, cursor: int
) -> InventoryMovement | None:
    return db.scalar(
        select(InventoryMovement)
        .join(
            InventoryTransaction,
            InventoryTransaction.id == InventoryMovement.transaction_id,
        )
        .join(
            InventoryMovementSerial,
            InventoryMovementSerial.movement_id == InventoryMovement.id,
        )
        .where(
            InventoryMovementSerial.serial_id == serial_id,
            InventoryTransaction.status == "posted",
            InventoryTransaction.ledger_cursor <= cursor,
        )
        .order_by(
            InventoryTransaction.ledger_cursor.desc(),
            InventoryMovement.line_no.desc(),
            InventoryMovement.id.desc(),
        )
        .limit(1)
    )


def _snapshot_serial_ids(row: StocktakeSnapshotLine) -> tuple[uuid.UUID, ...]:
    document = row.serial_snapshot_jsonb
    if not isinstance(document, list) or len(document) != row.serial_count:
        _evidence_invalid("盘点截止 SN 快照数量无效")
    values: list[uuid.UUID] = []
    for item in document:
        try:
            value = uuid.UUID(item["serial_id"]) if isinstance(item, dict) else None
        except (KeyError, TypeError, ValueError):
            value = None
        if value is None or value.int == 0:
            _evidence_invalid("盘点截止 SN 快照标识无效")
        values.append(value)
    if len(values) != len(set(values)):
        _evidence_invalid("盘点截止 SN 快照包含重复标识")
    return tuple(sorted(values, key=str))


def _stored_coordinates(
    db: Session, completion_id: uuid.UUID
) -> tuple[tuple[uuid.UUID, ...], tuple[uuid.UUID, ...]]:
    return (
        tuple(
            db.scalars(
                select(StocktakeCloseReconciliationAccount.stock_account_id)
                .where(
                    StocktakeCloseReconciliationAccount.completion_id
                    == completion_id
                )
                .order_by(StocktakeCloseReconciliationAccount.stock_account_id)
            ).all()
        ),
        tuple(
            db.scalars(
                select(StocktakeCloseReconciliationSerial.serial_id)
                .where(
                    StocktakeCloseReconciliationSerial.completion_id
                    == completion_id
                )
                .order_by(StocktakeCloseReconciliationSerial.serial_id)
            ).all()
        ),
    )


def _load_reconciliations(
    db: Session, task_id: uuid.UUID
) -> tuple[StocktakeCloseReconciliationCompletion, ...]:
    rows = tuple(
        db.scalars(
            select(StocktakeCloseReconciliationCompletion)
            .where(StocktakeCloseReconciliationCompletion.task_id == task_id)
            .order_by(StocktakeCloseReconciliationCompletion.reconciliation_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len({row.id for row in rows}) != len(rows):
        _evidence_invalid("盘点内部对账完成事实主键不唯一")
    return rows


def _account_dimension_sha256(account: StockAccount) -> str:
    return _sha256(
        {
            "availability_bucket": account.availability_bucket,
            "condition_code": account.condition_code,
            "custodian_person_id": str(account.custodian_person_id) if account.custodian_person_id else None,
            "location_id": str(account.location_id),
            "lot_id": str(account.lot_id) if account.lot_id else None,
            "material_id": str(account.material_id),
            "owner_org_id": str(account.owner_org_id),
            "schema": "cloud_oam.stocktake.close_reconciliation.account_dimension.v1",
            "stock_account_id": str(account.id),
        }
    )


def _account_document(
    completion_id: uuid.UUID, task_id: uuid.UUID, row: _AccountProof
) -> dict[str, object]:
    return {
        "account_dimension_sha256": _account_dimension_sha256(row.account),
        "account_role": row.account_role,
        "balance_ledger_cursor": row.balance_ledger_cursor,
        "balance_qty": _decimal(row.balance_qty),
        "book_qty_at_count": _decimal_or_none(row.book_qty_at_count),
        "completion_id": str(completion_id),
        "count_ledger_cursor": row.count_ledger_cursor,
        "effective_round_id": str(row.effective_round_id) if row.effective_round_id else None,
        "expected_physical_qty": _decimal_or_none(row.expected_physical_qty),
        "last_touch_ledger_cursor": row.last_touch_ledger_cursor,
        "ledger_delta_after_count": _decimal_or_none(row.ledger_delta_after_count),
        "ledger_qty": _decimal(row.ledger_qty),
        "physical_delta_after_count": _decimal_or_none(row.physical_delta_after_count),
        "physical_qty_at_count": _decimal_or_none(row.physical_qty_at_count),
        "schema": "cloud_oam.stocktake.close_reconciliation.account.v1",
        "scope_id": str(row.scope_id) if row.scope_id else None,
        "stock_account_id": str(row.account.id),
        "task_id": str(task_id),
    }


def _serial_document(
    completion_id: uuid.UUID, task_id: uuid.UUID, row: _SerialProof
) -> dict[str, object]:
    return {
        "completion_id": str(completion_id),
        "count_ledger_cursor": row.count_ledger_cursor,
        "current_position_account_id": str(row.current_position_account_id) if row.current_position_account_id else None,
        "current_position_last_movement_id": str(row.current_position_last_movement_id),
        "effective_round_id": str(row.effective_round_id) if row.effective_round_id else None,
        "evidence_scope_id": str(row.evidence_scope_id) if row.evidence_scope_id else None,
        "expected_current_account_id": str(row.expected_current_account_id) if row.expected_current_account_id else None,
        "ledger_last_movement_id": str(row.ledger_last_movement_id),
        "physical_account_id_at_count": str(row.physical_account_id_at_count) if row.physical_account_id_at_count else None,
        "physical_present_at_count": row.physical_present_at_count,
        "schema": "cloud_oam.stocktake.close_reconciliation.serial.v1",
        "serial_id": str(row.serial_id),
        "task_id": str(task_id),
    }


def _reconciliation_manifest(
    *,
    completion_id: uuid.UUID,
    task_id: uuid.UUID,
    posting: StocktakePostingCompletion,
    previous: StocktakeCloseReconciliationCompletion | None,
    reconciliation_no: int,
    expected_task_version: int,
    reconciled_task_version: int,
    ledger_cursor: int,
    plan: _ReconciliationPlan,
    account_manifest: str,
    serial_manifest: str,
    account_count: int | None = None,
    scoped_account_count: int | None = None,
    serial_count: int | None = None,
) -> str:
    return _sha256(
        {
            "account_count": len(plan.account_rows) if account_count is None else account_count,
            "account_manifest_sha256": account_manifest,
            "book_total_qty": _decimal(plan.book_total_qty),
            "completion_id": str(completion_id),
            "expected_task_version": expected_task_version,
            "movement_count": plan.movement_count,
            "physical_total_qty": _decimal(plan.physical_total_qty),
            "posting_completion_id": str(posting.id),
            "posting_manifest_sha256": posting.posting_manifest_sha256,
            "previous_reconciliation_id": str(previous.id) if previous else None,
            "reconciled_task_version": reconciled_task_version,
            "reconciliation_ledger_cursor": ledger_cursor,
            "reconciliation_no": reconciliation_no,
            "schema": "cloud_oam.stocktake.nonopening_close_reconciliation.v1",
            "scope_count": posting.scope_count,
            "scoped_account_count": sum(row.scope_id is not None for row in plan.account_rows)
            if scoped_account_count is None
            else scoped_account_count,
            "serial_count": len(plan.serial_rows) if serial_count is None else serial_count,
            "serial_manifest_sha256": serial_manifest,
            "task_id": str(task_id),
            "transaction_count": plan.transaction_count,
        }
    )


def _reconciliation_event_metadata(
    row: StocktakeCloseReconciliationCompletion,
) -> dict[str, object]:
    return {
        "account_count": row.account_count,
        "book_total_qty": _decimal(row.book_total_qty),
        "completion_id": str(row.id),
        "physical_total_qty": _decimal(row.physical_total_qty),
        "posting_completion_id": str(row.posting_completion_id),
        "reconciled_task_version": row.reconciled_task_version,
        "reconciliation_ledger_cursor": row.reconciliation_ledger_cursor,
        "reconciliation_manifest_sha256": row.reconciliation_manifest_sha256,
        "reconciliation_no": row.reconciliation_no,
        "schema": "cloud_oam.stocktake.nonopening_close_reconciled_event.v1",
        "serial_count": row.serial_count,
        "status": "posted",
        "task_id": str(row.task_id),
    }


def _close_event_metadata(row: StocktakeCloseCompletion) -> dict[str, object]:
    return {
        "close_completion_id": str(row.id),
        "close_manifest_sha256": row.close_manifest_sha256,
        "closed_task_version": row.closed_task_version,
        "posting_completion_id": str(row.posting_completion_id),
        "reconciliation_completion_id": str(row.reconciliation_completion_id),
        "reconciliation_ledger_cursor": row.reconciliation_ledger_cursor,
        "reconciliation_manifest_sha256": row.reconciliation_manifest_sha256,
        "reconciliation_no": row.reconciliation_no,
        "schema": "cloud_oam.stocktake.nonopening_closed_event.v1",
        "status": "closed",
        "task_id": str(row.task_id),
    }


def _authorization_sha256(
    *,
    kind: str,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    occurred_at: datetime,
) -> str:
    return _authorization_document_sha256(
        kind=kind,
        user_id=actor.user_id,
        person_id=actor.person_id,
        assignment_id=assignment.id,
        authorization_version=actor.authorization_version,
        occurred_at=occurred_at,
    )


def _authorization_document_sha256(
    *,
    kind: str,
    user_id: str,
    person_id: uuid.UUID,
    assignment_id: uuid.UUID,
    authorization_version: int,
    occurred_at: datetime,
) -> str:
    return _sha256(
        {
            "assignment_id": str(assignment_id),
            "authorization_version": authorization_version,
            "kind": kind,
            "occurred_at": _timestamp(occurred_at),
            "person_id": str(person_id),
            "role_code": "admin",
            "schema": "cloud_oam.stocktake.nonopening_close_authorization.v1",
            "scope_id": "*",
            "scope_type": "national",
            "user_id": user_id,
        }
    )


def _request_sha256(
    *,
    kind: str,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
    expected_version: int,
    posting: StocktakePostingCompletion,
    reconciliation: StocktakeCloseReconciliationCompletion | None = None,
) -> str:
    return _sha256(
        {
            "actor": {
                "authorization_version": actor.authorization_version,
                "person_id": str(actor.person_id),
                "user_id": actor.user_id,
            },
            "expected_task_version": expected_version,
            "kind": kind,
            "posting_completion_id": str(posting.id),
            "posting_manifest_sha256": posting.posting_manifest_sha256,
            "reconciliation_completion_id": str(reconciliation.id) if reconciliation else None,
            "reconciliation_manifest_sha256": reconciliation.reconciliation_manifest_sha256 if reconciliation else None,
            "schema": "cloud_oam.stocktake.nonopening_close_request.v1",
            "task_id": str(task_id),
        }
    )


def _lock_task(db: Session, task_id: uuid.UUID) -> FormalStocktakeTask:
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == task_id)
        .with_for_update(of=FormalStocktakeTask)
        .execution_options(populate_existing=True)
    )
    if task is None or task.task_type not in NON_OPENING_TYPES:
        _fail("stocktake_close_task_not_found", "not_found", "非期初盘点任务不存在")
    return task


def _require_current_admin(
    db: Session,
    supplied: FormalPrincipal,
    *,
    action: str,
) -> FormalPrincipal:
    if action not in {"reconcile", "close"}:
        _fail(
            "stocktake_close_permission_action_invalid",
            "invalid_request",
            "盘点内部对账或关闭权限动作无效",
        )
    try:
        current = load_formal_principal(db, supplied.user_id)
    except FormalAccessError as exc:
        _fail(
            "stocktake_close_actor_not_current",
            "forbidden",
            "总部管理员权限已失效",
            cause=exc,
        )
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail(
            "stocktake_close_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取",
        )
    if (
        current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail(
            "stocktake_close_actor_inactive",
            "forbidden",
            "当前账号或人员状态不允许盘点内部对账或关闭",
        )
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
            action,
            target_scope_type="national",
            target_scope_id="*",
        )
    except FormalAccessError as exc:
        _fail(
            "stocktake_close_authorization_invalid",
            "forbidden",
            "盘点内部对账或关闭权限图无效",
            cause=exc,
        )
    assignment = (
        db.get(RoleAssignment, grants[0].assignment_id, populate_existing=True)
        if len(grants) == 1
        else None
    )
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    user = db.get(User, current.user_id)
    person = db.get(Person, current.person_id)
    organization = (
        db.get(Organization, person.organization_id)
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
            f"stocktake_close_{action}_forbidden",
            "forbidden",
            "盘点内部对账及关闭仅允许具备精确权限的当前总部管理员执行",
        )
    return current


def _current_admin_assignment(
    db: Session, actor: FormalPrincipal, *, lock_row: bool = True
) -> RoleAssignment:
    grants = tuple(
        row
        for row in actor.assignments
        if row.role_code == "admin"
        and row.scope_type == "national"
        and row.scope_id == "*"
    )
    if len(grants) != 1:
        _fail("stocktake_close_admin_assignment_ambiguous", "forbidden", "盘点内部对账及关闭要求唯一总部管理员授权")
    statement = select(RoleAssignment).where(
        RoleAssignment.id == grants[0].assignment_id
    )
    if lock_row:
        statement = statement.with_for_update(of=RoleAssignment)
    assignment = db.scalar(statement.execution_options(populate_existing=True))
    if (
        assignment is None
        or assignment.user_id != actor.user_id
        or assignment.status != "active"
        or assignment.revoked_at is not None
        or assignment.scope_type != "national"
        or assignment.scope_id != "*"
    ):
        _fail("stocktake_close_admin_assignment_not_current", "forbidden", "总部管理员授权已失效")
    return assignment


def _validate_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail("stocktake_close_formal_principal_required", "forbidden", "盘点内部对账及关闭必须使用正式权限主体")
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _fail("stocktake_close_actor_inactive", "forbidden", "当前账号或人员状态不允许盘点内部对账及关闭")
    return actor


def _validate_command(command, expected_type):
    if not isinstance(command, expected_type):
        _fail("stocktake_close_command_invalid", "invalid_request", "盘点内部对账或关闭命令类型无效")
    if not isinstance(command.task_id, uuid.UUID) or command.task_id.int == 0:
        _fail("stocktake_close_task_id_invalid", "invalid_request", "盘点任务标识无效")
    if (
        not isinstance(command.expected_task_version, int)
        or isinstance(command.expected_task_version, bool)
        or command.expected_task_version < 0
    ):
        _fail("stocktake_close_version_invalid", "invalid_request", "盘点任务预期版本无效")
    return command


def _validate_transport(
    key: object, secret: bytes | str, trace_id: object
) -> tuple[str, bytes, str]:
    if (
        not isinstance(key, str)
        or not 16 <= len(key) <= 128
        or _PRINTABLE.fullmatch(key) is None
    ):
        _fail("stocktake_close_idempotency_key_invalid", "invalid_request", "幂等键必须为 16 至 128 位可打印 ASCII 字符")
    checked_secret = secret.encode("utf-8") if isinstance(secret, str) else secret
    if not isinstance(checked_secret, bytes) or len(checked_secret) < 32:
        _fail("stocktake_close_hmac_secret_invalid", "invalid_request", "盘点内部对账幂等摘要密钥无效")
    if (
        not isinstance(trace_id, str)
        or not 8 <= len(trace_id) <= 160
        or _PRINTABLE.fullmatch(trace_id) is None
    ):
        _fail("stocktake_close_trace_request_id_invalid", "invalid_request", "请求标识必须为 8 至 160 位可打印 ASCII 字符")
    return key, checked_secret, trace_id


def _reconciliation_result(
    row: StocktakeCloseReconciliationCompletion, *, replayed: bool
) -> StocktakeCloseReconciliationResult:
    return StocktakeCloseReconciliationResult(
        completion_id=row.id,
        task_id=row.task_id,
        posting_completion_id=row.posting_completion_id,
        reconciliation_no=row.reconciliation_no,
        reconciliation_ledger_cursor=row.reconciliation_ledger_cursor,
        resulting_task_status="posted",
        task_version=row.reconciled_task_version,
        scope_count=row.scope_count,
        account_count=row.account_count,
        scoped_account_count=row.scoped_account_count,
        serial_count=row.serial_count,
        transaction_count=row.transaction_count,
        movement_count=row.movement_count,
        book_total_qty=row.book_total_qty,
        physical_total_qty=row.physical_total_qty,
        reconciled_at=_as_utc(row.reconciled_at),
        replayed=replayed,
    )


def _close_result(row: StocktakeCloseCompletion, *, replayed: bool) -> StocktakeCloseResult:
    return StocktakeCloseResult(
        completion_id=row.id,
        task_id=row.task_id,
        reconciliation_completion_id=row.reconciliation_completion_id,
        reconciliation_no=row.reconciliation_no,
        reconciliation_ledger_cursor=row.reconciliation_ledger_cursor,
        resulting_task_status="closed",
        task_version=row.closed_task_version,
        closed_at=_as_utc(row.closed_at),
        replayed=replayed,
    )


def _idempotency_hmac(secret: bytes, actor_user_id: str, path: str, raw_key: str) -> str:
    return hmac.new(
        secret,
        (
            "cloud_oam.stocktake.nonopening_close.idempotency.v1\0"
            f"{actor_user_id}\0{path}\0{raw_key}"
        ).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _request_reference(kind: str, raw: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.stocktake.nonopening_close.request.v1\0{kind}\0{raw}".encode("utf-8")
    ).hexdigest()
    return f"stocktake-close-{kind}-request-{digest}"


def _event_key(kind: str, value: uuid.UUID) -> str:
    return f"stocktake-close-{kind}-{hashlib.sha256((kind + str(value)).encode()).hexdigest()}"


def _lock_coordinate(namespace: str, value: str) -> int:
    digest = hashlib.sha256(
        f"cloud_oam.inventory.lock.v1\0{namespace}\0{value}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _take_advisory_locks(db: Session, coordinates: Sequence[int]) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for coordinate in sorted(set(coordinates)):
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": coordinate})


def _database_now(db: Session) -> datetime:
    if db.get_bind().dialect.name == "postgresql":
        value = db.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            _fail("stocktake_close_database_time_unavailable", "service_unavailable", "数据库服务端时间不可用")
        return _as_utc(value)
    return datetime.now(timezone.utc)


def _decimal(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.001")), "f")


def _decimal_or_none(value: Decimal | None) -> str | None:
    return None if value is None else _decimal(value)


def _timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _as_utc(value: object) -> datetime:
    if not isinstance(value, datetime):
        _evidence_invalid("盘点终态时间坐标无效")
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _sha256(document: object) -> str:
    return hashlib.sha256(
        json.dumps(
            document,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _evidence_invalid(message: str) -> NoReturn:
    _fail("stocktake_close_evidence_invalid", "service_unavailable", message)


def _fail(
    code: str,
    category: str,
    message: str,
    *,
    cause: BaseException | None = None,
) -> NoReturn:
    error = StocktakeCloseError(code, category, message)
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "CloseReconciledStocktakeCommand",
    "ReconcileStocktakeForCloseCommand",
    "StocktakeCloseError",
    "StocktakeCloseReconciliationResult",
    "StocktakeCloseResult",
    "close_reconciled_stocktake",
    "reconcile_posted_stocktake_for_close",
]
