"""Immutable difference generation for submitted non-opening stocktakes.

This service evaluates the initial round against its cutoff snapshot.  It is
deliberately separate from both the count submission path and the opening
stocktake/OAM-control path.  The caller owns the transaction; this module
flushes but never commits or rolls back and never writes inventory, balance,
movement, outbox, review, recount, posting, notification, or reconciliation
facts.
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

from sqlalchemy import and_, func, or_, select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from ..formal_access import (
    FormalAccessError,
    FormalPrincipal,
    ScopeGrant,
    load_formal_principal,
    lock_formal_principal_graph,
)
from ..foundation_models import (
    DocumentAttachment,
    FileObject,
    Organization,
    Person,
    Role,
    RoleAssignment,
)
from ..inventory_models import (
    FormalMaterial,
    InventoryLedgerHead,
    InventoryLot,
    InventoryMovement,
    InventoryMovementSerial,
    InventorySerial,
    InventoryTransaction,
    MaterialInventoryPolicy,
    StockAccount,
    StockLocation,
)
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
    StocktakeReview,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)
from . import stocktake_count as count_service
from . import stocktake_task as task_service
from .audit_chain import AuditChainError, append_audit_event, lock_audit_chain_head
from .inventory_posting import (
    INVENTORY_LEDGER_HEAD_ID,
    INVENTORY_STREAM_KEY,
)
from .postgresql_lock_graph import (
    lock_nonopening_stocktake_difference_replay_graph,
)


_NON_OPENING_TYPES: Final[frozenset[str]] = frozenset(
    {"full", "sample", "ad_hoc", "personal", "termination"}
)
_ZERO: Final[Decimal] = Decimal("0.000")
_ONE: Final[Decimal] = Decimal("1.000")
_SAFE_TRACE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+\-]{0,159}$", re.ASCII)
_PRINTABLE = re.compile(r"^[\x21-\x7e]{1,200}$", re.ASCII)
_PLACEHOLDERS = ("replace-with", "replace_me", "replace-me", "change-me", "changeme")
_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}
_TYPE_ORDER = {
    "missing": 0,
    "excess": 1,
    "wrong_location": 2,
    "wrong_condition": 3,
    "wrong_lot": 4,
    "wrong_serial": 5,
}


class StocktakeDifferenceError(RuntimeError):
    """Stable, database-detail-free difference-generation failure."""

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
class GenerateStocktakeDifferenceCommand:
    task_id: uuid.UUID
    round_id: uuid.UUID
    expected_task_version: int


@dataclass(frozen=True, slots=True)
class StocktakeDifferenceResult:
    task_id: uuid.UUID
    round_id: uuid.UUID
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
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _DifferencePlan:
    scope_id: uuid.UUID
    difference_type: str
    material_id: uuid.UUID | None
    expected_account_id: uuid.UUID | None
    observed_account_id: uuid.UUID | None
    observed_line_id: uuid.UUID | None
    serial_id: uuid.UUID | None
    book_qty: Decimal
    counted_qty: Decimal
    difference_qty: Decimal
    affected_qty: Decimal
    reason_code: str
    reason_text: str


@dataclass(slots=True)
class _QuantitySide:
    scope_id: uuid.UUID
    account: StockAccount | None
    observation: StocktakeCountObservation | None
    material_id: uuid.UUID
    owner_org_id: uuid.UUID
    location_id: uuid.UUID
    custodian_person_id: uuid.UUID | None
    condition_code: str
    availability_bucket: str
    lot_id: uuid.UUID | None
    remaining_qty: Decimal


@dataclass(frozen=True, slots=True)
class _ScopeExpectedState:
    quantities: Mapping[uuid.UUID, Decimal]
    serial_accounts: Mapping[uuid.UUID, uuid.UUID]


@dataclass(frozen=True, slots=True)
class _ReplayEvidence:
    accounts: Mapping[uuid.UUID, StockAccount]
    states: Mapping[uuid.UUID, _ScopeExpectedState]
    observation_accounts: Mapping[uuid.UUID, uuid.UUID]
    movements: tuple[tuple[InventoryMovement, tuple[uuid.UUID, ...]], ...]


def generate_stocktake_initial_differences(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: GenerateStocktakeDifferenceCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeDifferenceResult:
    """Generate and seal one submitted non-opening initial-round difference set."""

    try:
        return _generate_stocktake_initial_differences(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    except StocktakeDifferenceError:
        raise
    except (task_service.StocktakeTaskError, count_service.StocktakeCountError):
        raise StocktakeDifferenceError(
            "stocktake_difference_reference_graph_invalid",
            "service_unavailable",
            "盘点任务、截止快照或实盘证据图无效",
        ) from None
    except AuditChainError:
        raise StocktakeDifferenceError(
            "stocktake_difference_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，本次差异评估未完成",
        ) from None
    except IntegrityError:
        raise StocktakeDifferenceError(
            "stocktake_difference_concurrent_conflict",
            "conflict",
            "盘点差异评估发生并发冲突，请回滚并重新读取",
        ) from None
    except DBAPIError:
        raise StocktakeDifferenceError(
            "stocktake_difference_database_unavailable",
            "service_unavailable",
            "数据库暂时不可用，本次差异评估未完成",
        ) from None


def _generate_stocktake_initial_differences(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: GenerateStocktakeDifferenceCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeDifferenceResult:
    supplied = _validate_supplied_actor(actor)
    checked = _validate_command(command)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    trace_id = _require_trace_request_id(trace_request_id)
    path = f"/api/v1/stocktakes/{checked.task_id}/rounds/{checked.round_id}/differences"
    key_hash = _idempotency_hmac(secret, supplied.user_id, path, raw_key)
    request_hash = _request_hmac(secret, supplied, checked)
    _take_advisory_locks(
        db,
        (
            _lock_coordinate("stocktake-difference-idempotency", key_hash),
            _lock_coordinate("stocktake-difference-task", str(checked.task_id)),
            _lock_coordinate("stocktake-difference-round", str(checked.round_id)),
        ),
    )

    is_postgresql = db.get_bind().dialect.name == "postgresql"
    task_statement = select(FormalStocktakeTask).where(
        FormalStocktakeTask.id == checked.task_id
    )
    if not is_postgresql:
        task_statement = task_statement.with_for_update()
    task = db.scalar(task_statement.execution_options(populate_existing=True))
    if task is None or task.task_type not in _NON_OPENING_TYPES:
        _fail("stocktake_difference_task_not_found", "not_found", "非期初盘点任务不存在")

    round_statement = select(StocktakeRound).where(
        StocktakeRound.id == checked.round_id,
        StocktakeRound.task_id == task.id,
    )
    if not is_postgresql:
        round_statement = round_statement.with_for_update()
    round_row = db.scalar(
        round_statement.execution_options(populate_existing=True)
    )
    if round_row is None:
        _fail("stocktake_difference_round_not_found", "not_found", "盘点初盘轮次不存在")
    if is_postgresql:
        lock_nonopening_stocktake_difference_replay_graph(
            db, task.id, round_row.id, supplied.user_id
        )
        # The preflight above preserves stable not-found errors.  Re-read after
        # the owner boundary acquires the inventory-head-first graph so every
        # later validation observes the locked rows.
        task = db.scalar(
            select(FormalStocktakeTask)
            .where(FormalStocktakeTask.id == checked.task_id)
            .execution_options(populate_existing=True)
        )
        round_row = db.scalar(
            select(StocktakeRound)
            .where(
                StocktakeRound.id == checked.round_id,
                StocktakeRound.task_id == checked.task_id,
            )
            .execution_options(populate_existing=True)
        )
        if task is None or round_row is None:
            _fail(
                "stocktake_difference_lock_graph_changed",
                "conflict",
                "盘点任务在锁定期间发生变化，请重新读取",
            )
    scopes = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(FormalStocktakeScope)
                .where(FormalStocktakeScope.task_id == task.id)
                .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if not scopes:
        _fail("stocktake_difference_scope_missing", "service_unavailable", "盘点范围证据缺失")

    lock_formal_principal_graph(
        db,
        tuple(sorted({supplied.user_id, *(row.assignee_user_id for row in scopes)})),
    )
    submissions = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeRoundSubmission)
                .where(
                    StocktakeRoundSubmission.task_id == task.id,
                    StocktakeRoundSubmission.round_id == round_row.id,
                )
                .order_by(StocktakeRoundSubmission.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(submissions) != 1:
        _fail(
            "stocktake_difference_submission_invalid",
            "service_unavailable",
            "已提交初盘必须且只能存在一个轮次封印",
        )
    submission = submissions[0]
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    assignment, grant = _authorize_evaluator(db, current, task, now)
    _validate_submitted_state(task, round_row, checked, submission)

    plans = task_service._load_and_validate_scope_plans(db, task, scopes, now=now)
    freezes = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(InventoryFreeze)
                .where(InventoryFreeze.task_id == task.id)
                .order_by(
                    InventoryFreeze.stocktake_scope_id,
                    InventoryFreeze.id,
                ),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    freeze_by_scope = {row.stocktake_scope_id: row for row in freezes}
    if len(freeze_by_scope) != len(scopes):
        _fail("stocktake_difference_freeze_invalid", "service_unavailable", "盘点冻结证据不完整")
    plan_by_scope = {row.scope_id: row for row in plans}
    for scope in scopes:
        plan = plan_by_scope.get(scope.id)
        if plan is None:
            _fail("stocktake_difference_scope_invalid", "service_unavailable", "盘点范围证据无法重算")
        count_service._validate_task_round_and_freeze(
            task,
            round_row,
            scope,
            plan.freeze_mode,
            freeze_by_scope.get(scope.id),
            now,
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
        _fail(
            "stocktake_difference_control_evidence_forbidden",
            "service_unavailable",
            "非期初盘点禁止携带 OAM 控制总账差异",
        )

    snapshots = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeSnapshotLine)
                .where(StocktakeSnapshotLine.task_id == task.id)
                .order_by(
                    StocktakeSnapshotLine.scope_id,
                    StocktakeSnapshotLine.stock_account_id,
                ),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    accounts = count_service._lock_snapshot_accounts(db, snapshots)
    count_service._validate_snapshot_manifest(task, plans, snapshots, accounts)
    count_lines = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeCountLine)
                .where(
                    StocktakeCountLine.task_id == task.id,
                    StocktakeCountLine.round_id == round_row.id,
                )
                .order_by(
                    StocktakeCountLine.scope_id,
                    StocktakeCountLine.stock_account_id,
                ),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    count_line_ids = tuple(row.id for row in count_lines)
    count_serials = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeCountSerial)
                .where(StocktakeCountSerial.count_line_id.in_(count_line_ids))
                .order_by(
                    StocktakeCountSerial.count_line_id,
                    StocktakeCountSerial.serial_id,
                ),
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if count_line_ids else ()
    observations = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeCountObservation)
                .where(
                    StocktakeCountObservation.task_id == task.id,
                    StocktakeCountObservation.round_id == round_row.id,
                )
                .order_by(
                    StocktakeCountObservation.scope_id,
                    StocktakeCountObservation.observation_no,
                ),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    completions = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeScopeCountCompletion)
                .where(
                    StocktakeScopeCountCompletion.task_id == task.id,
                    StocktakeScopeCountCompletion.round_id == round_row.id,
                )
                .order_by(StocktakeScopeCountCompletion.scope_id),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    _validate_completion_and_submission_manifests(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        snapshots=snapshots,
        accounts=accounts,
        count_lines=count_lines,
        count_serials=count_serials,
        observations=observations,
        completions=completions,
        submission=submission,
    )
    replay_evidence = _replay_scope_expected_states(
        db,
        task=task,
        scopes=scopes,
        plans=plans,
        snapshots=snapshots,
        snapshot_accounts=accounts,
        observations=observations,
        completions=completions,
    )
    accounts = dict(replay_evidence.accounts)
    _lock_dimension_graph(
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

    planned = _plan_difference_set(
        task=task,
        scopes=scopes,
        snapshots=snapshots,
        accounts=accounts,
        count_lines=count_lines,
        count_serials=count_serials,
        observations=observations,
        replay_evidence=replay_evidence,
    )
    existing_differences = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeDifference)
                .where(
                    StocktakeDifference.task_id == task.id,
                    StocktakeDifference.round_id == round_row.id,
                )
                .order_by(StocktakeDifference.difference_no),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    existing_completions = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StocktakeDifferenceSetCompletion)
                .where(
                    or_(
                        StocktakeDifferenceSetCompletion.idempotency_key_hash == key_hash,
                        (
                            (StocktakeDifferenceSetCompletion.task_id == task.id)
                            & (StocktakeDifferenceSetCompletion.round_id == round_row.id)
                        ),
                    ),
                )
                .order_by(StocktakeDifferenceSetCompletion.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if existing_completions:
        if len(existing_completions) != 1:
            _fail("stocktake_difference_completion_ambiguous", "service_unavailable", "差异集封印不唯一")
        completion = existing_completions[0]
        if completion.idempotency_key_hash != key_hash or completion.request_sha256 != request_hash:
            _fail("stocktake_difference_idempotency_conflict", "conflict", "幂等键或轮次已绑定不同差异评估")
        _validate_stored_difference_set(
            task=task,
            round_row=round_row,
            submission=submission,
            completion=completion,
            differences=existing_differences,
            planned=planned,
            actor=current,
            assignment=assignment,
            grant=grant,
        )
        return replace(
            _result(task, round_row, completion),
            replayed=True,
        )
    if existing_differences:
        _fail("stocktake_difference_partial_set_exists", "conflict", "存在未封印的盘点差异，请回滚并重新读取")
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
        _fail("stocktake_difference_downstream_started", "conflict", "差异审核或过账已经开始")

    lock_audit_chain_head(db, stream_key=INVENTORY_STREAM_KEY)
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    assignment, grant = _authorize_evaluator(
        db, current, task, now, lock_rows=False
    )
    _validate_submitted_state(task, round_row, checked, submission)

    differences: list[StocktakeDifference] = []
    for number, plan in enumerate(planned, start=1):
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

    summary = _difference_summary(task, round_row, submission, differences)
    authorization_hash = _difference_authorization_sha256(
        current,
        assignment,
        grant,
        now,
    )
    completion = StocktakeDifferenceSetCompletion(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        round_submission_id=submission.id,
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
        completed_role_assignment_id=assignment.id,
        authorization_version=current.authorization_version,
        role_code=grant.role_code,
        scope_type=grant.scope_type,
        scope_id_snapshot=grant.scope_id,
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
        action="stocktake.initial_difference_set.evaluated",
        aggregate_type="stocktake_round",
        aggregate_id=str(round_row.id),
        before_jsonb={"difference_status": "not_evaluated", "status": "submitted"},
        after_jsonb={
            "control_difference_count": 0,
            "difference_count": completion.difference_count,
            "difference_manifest_sha256": completion.difference_manifest_sha256,
            "difference_status": "evaluated",
            "pending_observation_difference_count": completion.pending_observation_difference_count,
            "round_status": "submitted",
            "task_id": str(task.id),
            "task_status": "submitted",
            "task_version": task.version,
            "total_affected_qty": _canonical_quantity(completion.total_affected_qty),
        },
        request_id=_request_reference(trace_id),
        occurred_at=now,
    )
    db.flush()
    return _result(task, round_row, completion)


def _validate_submitted_state(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    command: GenerateStocktakeDifferenceCommand,
    submission: StocktakeRoundSubmission,
) -> None:
    if task.version != command.expected_task_version:
        _fail("stocktake_difference_version_conflict", "conflict", "盘点任务版本已变化，请重新读取")
    if (
        task.task_type not in _NON_OPENING_TYPES
        or task.status != "submitted"
        or round_row.status != "submitted"
        or task.current_round_no != 1
        or round_row.round_no != 1
        or round_row.round_type != "initial"
        or task.current_round_no != round_row.round_no
        or task.submitted_at is None
        or round_row.submitted_at is None
        or round_row.submitted_by_user_id is None
        or round_row.count_manifest_sha256 is None
        or submission.sealing_completion_id is None
        or submission.submitted_at != round_row.submitted_at
        or task.submitted_at != round_row.submitted_at
        or round_row.submitted_by_user_id != submission.submitted_by_user_id
    ):
        _fail("stocktake_difference_state_invalid", "precondition_failed", "盘点初盘尚未完整提交或已进入后续状态")


def _authorize_evaluator(
    db: Session,
    actor: FormalPrincipal,
    task: FormalStocktakeTask,
    now: datetime,
    *,
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
    grant = next(
        (
            candidate
            for candidate in candidates
            if task_service._grant_allows(
                db,
                actor,
                candidate,
                "stocktake",
                "manage",
                target_scope_type="organization",
                target_scope_id=str(task.region_org_id),
            )
        ),
        None,
    )
    if grant is None:
        _fail("stocktake_difference_forbidden", "forbidden", "当前正式授权不能评估该盘点差异")
    statement = select(RoleAssignment).where(RoleAssignment.id == grant.assignment_id)
    if lock_rows:
        statement = _select_only_reference_statement(db, statement)
    assignment = db.scalar(statement.execution_options(populate_existing=True))
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    if (
        assignment is None
        or role is None
        or assignment.user_id != actor.user_id
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
        _fail("stocktake_difference_assignment_changed", "precondition_failed", "原轮次提交授权已变化")
    return assignment, grant


def _lock_dimension_graph(
    db: Session,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    accounts: Mapping[uuid.UUID, StockAccount],
    snapshots: Sequence[StocktakeSnapshotLine],
    count_lines: Sequence[StocktakeCountLine],
    count_serials: Sequence[StocktakeCountSerial],
    observations: Sequence[StocktakeCountObservation],
    replay_evidence: _ReplayEvidence | None = None,
) -> None:
    if task.cutoff_at is None:
        _fail("stocktake_difference_cutoff_missing", "service_unavailable", "盘点截止时点缺失")
    scope_ids = {row.id for row in scopes}
    if any(row.scope_id not in scope_ids for row in (*snapshots, *count_lines, *observations)):
        _fail("stocktake_difference_scope_graph_invalid", "service_unavailable", "盘点证据引用了范围外对象")
    location_ids = tuple(
        sorted(
            {row.location_id for row in scopes}
            .union(row.location_id for row in observations)
            .union(row.location_id for row in accounts.values()),
            key=str,
        )
    )
    locations = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StockLocation)
                .where(StockLocation.id.in_(location_ids))
                .order_by(StockLocation.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if location_ids else ()
    if len(locations) != len(location_ids):
        _fail("stocktake_difference_location_missing", "service_unavailable", "盘点库位维度缺失")
    org_ids = tuple(
        sorted(
            {row.owner_org_id for row in accounts.values()}.union(
                row.owner_org_id for row in observations
            ),
            key=str,
        )
    )
    if org_ids:
        organizations = tuple(
            db.scalars(
                _select_only_reference_statement(
                    db,
                    select(Organization)
                    .where(Organization.id.in_(org_ids))
                    .order_by(Organization.id),
                )
                .execution_options(populate_existing=True)
            ).all()
        )
        if len(organizations) != len(org_ids):
            _fail("stocktake_difference_owner_missing", "service_unavailable", "资产组织维度缺失")
    person_ids = tuple(
        sorted(
            {
                value
                for value in (
                    *(row.custodian_person_id for row in accounts.values()),
                    *(row.custodian_person_id_snapshot for row in observations),
                )
                if value is not None
            },
            key=str,
        )
    )
    if person_ids:
        people = tuple(
            db.scalars(
                _select_only_reference_statement(
                    db,
                    select(Person)
                    .where(Person.id.in_(person_ids))
                    .order_by(Person.id),
                )
                .execution_options(populate_existing=True)
            ).all()
        )
        if len(people) != len(person_ids):
            _fail("stocktake_difference_custodian_missing", "service_unavailable", "保管责任人维度缺失")

    material_ids = tuple(
        sorted(
            {row.material_id for row in accounts.values()}.union(
                row.material_id for row in observations if row.material_id is not None
            ),
            key=str,
        )
    )
    materials = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(FormalMaterial)
                .where(FormalMaterial.id.in_(material_ids))
                .order_by(FormalMaterial.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if material_ids else ()
    if len(materials) != len(material_ids):
        _fail("stocktake_difference_material_missing", "service_unavailable", "盘点物料维度缺失")
    policies: dict[uuid.UUID, MaterialInventoryPolicy] = {}
    for material_id in material_ids:
        policies[material_id] = count_service._load_policy(db, material_id, task.cutoff_at)
    for account in accounts.values():
        count_service._validate_account_policy(account, policies[account.material_id])

    lot_ids = tuple(
        sorted(
            {row.lot_id for row in accounts.values() if row.lot_id is not None}.union(
                row.lot_id for row in observations if row.lot_id is not None
            ),
            key=str,
        )
    )
    lots = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(InventoryLot)
                .where(InventoryLot.id.in_(lot_ids))
                .order_by(InventoryLot.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if lot_ids else ()
    if len(lots) != len(lot_ids):
        _fail("stocktake_difference_lot_missing", "service_unavailable", "盘点批次维度缺失")
    lots_by_id = {row.id: row for row in lots}
    for account in accounts.values():
        if account.lot_id is not None and lots_by_id[account.lot_id].material_id != account.material_id:
            _fail("stocktake_difference_lot_mismatch", "service_unavailable", "库存账户批次与物料不一致")

    expected_serial_ids = _snapshot_serial_ids(snapshots)
    replay_serial_account: dict[uuid.UUID, uuid.UUID] = {}
    if replay_evidence is not None:
        for state in replay_evidence.states.values():
            for serial_id, account_id in state.serial_accounts.items():
                existing = replay_serial_account.get(serial_id)
                if existing is not None and existing != account_id:
                    _fail(
                        "stocktake_difference_replay_serial_boundary_ambiguous",
                        "service_unavailable",
                        "同一 SN 在多个实盘边界归属不一致",
                    )
                replay_serial_account[serial_id] = account_id
        expected_serial_ids.update(replay_serial_account)
    movement_serial_ids = {
        serial_id
        for _movement, serial_ids in (
            replay_evidence.movements if replay_evidence is not None else ()
        )
        for serial_id in serial_ids
    }
    observed_serial_ids = {row.serial_id for row in count_serials}.union(
        row.serial_id for row in observations if row.serial_id is not None
    )
    serial_ids = tuple(
        sorted(
            expected_serial_ids.union(observed_serial_ids).union(
                movement_serial_ids
            ),
            key=str,
        )
    )
    serials = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(InventorySerial)
                .where(InventorySerial.id.in_(serial_ids))
                .order_by(InventorySerial.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if serial_ids else ()
    if len(serials) != len(serial_ids):
        _fail("stocktake_difference_serial_missing", "service_unavailable", "盘点 SN 主数据缺失")
    serial_by_id = {row.id: row for row in serials}
    for serial in serials:
        if serial.lifecycle_status != "active":
            _fail("stocktake_difference_serial_inactive", "service_unavailable", "盘点 SN 已失效")
        replay_account_id = replay_serial_account.get(serial.id)
        replay_account = accounts.get(replay_account_id) if replay_account_id else None
        if replay_account_id is not None and (
            replay_account is None
            or serial.material_id != replay_account.material_id
            or serial.lot_id != replay_account.lot_id
        ):
            _fail(
                "stocktake_difference_replay_serial_binding_invalid",
                "service_unavailable",
                "实盘游标回放 SN 与库存账户物料或批次不一致",
            )
    if replay_evidence is not None:
        for movement, replay_serial_ids in replay_evidence.movements:
            if len(replay_serial_ids) != len(set(replay_serial_ids)):
                _fail(
                    "stocktake_difference_replay_serial_graph_invalid",
                    "service_unavailable",
                    "截止后 SN 流水包含重复明细",
                )
            endpoint_accounts: list[StockAccount] = []
            for account_id in (
                movement.from_account_id,
                movement.to_account_id,
            ):
                if account_id is None:
                    continue
                account = accounts.get(account_id)
                if account is None:
                    _fail(
                        "stocktake_difference_replay_account_missing",
                        "service_unavailable",
                        "截止后库存流水端点账户缺失",
                    )
                endpoint_accounts.append(account)
            endpoint_material_ids = {
                account.material_id for account in endpoint_accounts
            }
            if len(endpoint_material_ids) != 1:
                _fail(
                    "stocktake_difference_replay_movement_material_mismatch",
                    "service_unavailable",
                    "截止后库存流水两端物料不一致",
                )
            material_id = next(iter(endpoint_material_ids))
            policy = policies[material_id]
            serial_tracking = policy.tracking_mode in {
                "serial",
                "lot_and_serial",
            }
            if serial_tracking:
                if (
                    movement.quantity != movement.quantity.to_integral_value()
                    or len(replay_serial_ids) != int(movement.quantity)
                ):
                    _fail(
                        "stocktake_difference_replay_serial_quantity_mismatch",
                        "service_unavailable",
                        "截止后 SN 流水数量与 SN 明细不一致",
                    )
            elif replay_serial_ids:
                _fail(
                    "stocktake_difference_replay_serial_not_allowed",
                    "service_unavailable",
                    "非 SN 追踪物料的截止后流水包含 SN 明细",
                )
            endpoint_lot_ids = {
                account.lot_id for account in endpoint_accounts
            }
            if policy.tracking_mode in {"lot", "lot_and_serial"}:
                if None in endpoint_lot_ids or len(endpoint_lot_ids) != 1:
                    _fail(
                        "stocktake_difference_replay_movement_lot_mismatch",
                        "service_unavailable",
                        "截止后批次流水两端批次不一致",
                    )
            elif endpoint_lot_ids != {None}:
                _fail(
                    "stocktake_difference_replay_movement_lot_invalid",
                    "service_unavailable",
                    "截止后非批次物料流水绑定了批次",
                )
            for serial_id in replay_serial_ids:
                serial = serial_by_id.get(serial_id)
                if serial is None or any(
                    serial.material_id != account.material_id
                    or serial.lot_id != account.lot_id
                    for account in endpoint_accounts
                ):
                    _fail(
                        "stocktake_difference_replay_serial_binding_invalid",
                        "service_unavailable",
                        "截止后流水 SN 与端点账户物料或批次不一致",
                    )
    line_by_id = {row.id: row for row in count_lines}
    for row in count_serials:
        line = line_by_id.get(row.count_line_id)
        account = accounts.get(line.stock_account_id) if line is not None else None
        serial = serial_by_id.get(row.serial_id)
        if (
            line is None
            or row.round_id != line.round_id
            or account is None
            or serial is None
            or serial.material_id != account.material_id
            or serial.lot_id != account.lot_id
        ):
            _fail("stocktake_difference_serial_binding_invalid", "service_unavailable", "实盘 SN 与账户维度不一致")
    scope_by_id = {row.id: row for row in scopes}
    for observation in observations:
        scope = scope_by_id[observation.scope_id]
        if (
            observation.owner_org_id != scope.owner_org_id
            or observation.location_id != scope.location_id
            or observation.custodian_person_id_snapshot
            != scope.custodian_person_id_snapshot
        ):
            _fail("stocktake_difference_observation_scope_mismatch", "service_unavailable", "现场观察超出冻结范围")
        if observation.serial_id is not None:
            serial = serial_by_id[observation.serial_id]
            if serial.material_id != observation.material_id or serial.lot_id != observation.lot_id:
                _fail("stocktake_difference_observation_serial_mismatch", "service_unavailable", "现场观察 SN 与物料批次不一致")


def _validate_completion_and_submission_manifests(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    snapshots: Sequence[StocktakeSnapshotLine],
    accounts: Mapping[uuid.UUID, StockAccount],
    count_lines: Sequence[StocktakeCountLine],
    count_serials: Sequence[StocktakeCountSerial],
    observations: Sequence[StocktakeCountObservation],
    completions: Sequence[StocktakeScopeCountCompletion],
    submission: StocktakeRoundSubmission,
    history: CountHistoryContext | None = None,
) -> None:
    if history is not None:
        history.require(db, task, round_row, scopes)
    scope_ids = {row.id for row in scopes}
    if len(completions) != len(scopes) or {row.scope_id for row in completions} != scope_ids:
        _fail("stocktake_difference_scope_completion_missing", "service_unavailable", "初盘范围未全部封印")
    if task.cutoff_ledger_cursor is None or any(
        type(row.count_ledger_cursor) is not int
        or row.count_ledger_cursor < task.cutoff_ledger_cursor
        for row in completions
    ):
        _fail(
            "stocktake_difference_count_cursor_missing",
            "service_unavailable",
            "实盘范围缺少可信账本游标封印，旧证据禁止推断",
        )
    snapshot_accounts = {row.stock_account_id for row in snapshots}
    if len(count_lines) != len(snapshots) or {row.stock_account_id for row in count_lines} != snapshot_accounts:
        _fail("stocktake_difference_count_coverage_invalid", "service_unavailable", "初盘未完整覆盖截止快照账户")
    if any(row.stock_account_id not in accounts for row in count_lines):
        _fail("stocktake_difference_count_account_invalid", "service_unavailable", "初盘账户引用不完整")
    line_ids = {row.id for row in count_lines}
    if any(row.count_line_id not in line_ids or row.round_id != round_row.id for row in count_serials):
        _fail("stocktake_difference_count_serial_invalid", "service_unavailable", "初盘 SN 引用不完整")
    if len({row.serial_id for row in count_serials}) != len(count_serials):
        _fail("stocktake_difference_serial_duplicate", "service_unavailable", "同一 SN 在初盘中重复出现")
    resolved_observation_serials = [row.serial_id for row in observations if row.serial_id is not None]
    if len(set(resolved_observation_serials)) != len(resolved_observation_serials) or set(
        resolved_observation_serials
    ).intersection(row.serial_id for row in count_serials):
        _fail("stocktake_difference_serial_duplicate", "service_unavailable", "同一 SN 在初盘中重复出现")
    raw_serials = [
        (row.serial_identifier_type, row.serial_no_raw)
        for row in observations
        if row.serial_no_raw is not None
    ]
    if len(set(raw_serials)) != len(raw_serials):
        _fail("stocktake_difference_raw_serial_duplicate", "service_unavailable", "同一现场 SN 标识重复出现")

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
    attachments = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(DocumentAttachment)
                .where(
                    DocumentAttachment.document_type
                    == "stocktake_scope_count_completion",
                    DocumentAttachment.document_id.in_(completion_ids),
                    DocumentAttachment.attachment_type == "stocktake_evidence",
                )
                .order_by(
                    DocumentAttachment.document_id,
                    DocumentAttachment.file_id,
                ),
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if completion_ids else ()
    attachments_by_completion: dict[str, list[DocumentAttachment]] = defaultdict(list)
    for row in attachments:
        if row.status != "active":
            _fail("stocktake_difference_attachment_invalid", "service_unavailable", "盘点附件证据已失效")
        attachments_by_completion[row.document_id].append(row)
    file_ids = tuple(sorted({row.file_id for row in attachments}, key=str))
    files = history.files_for(db, task, round_row, file_ids) if history is not None else tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(FileObject)
                .where(FileObject.id.in_(file_ids))
                .order_by(FileObject.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if file_ids else ()
    if len(files) != len(file_ids):
        _fail("stocktake_difference_file_missing", "service_unavailable", "盘点附件文件缺失")
    files_by_id = {row.id: row for row in files}

    completion_by_id = {row.id: row for row in completions}
    sealing = completion_by_id.get(submission.sealing_completion_id)
    if sealing is None:
        _fail("stocktake_difference_sealing_completion_invalid", "service_unavailable", "轮次最终范围封印缺失")
    for completion in completions:
        scope_lines = tuple(sorted(lines_by_scope[completion.scope_id], key=lambda row: str(row.stock_account_id)))
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
            or len(row.sha256) != 64
            or row.size_bytes < 0
            for row in scope_files
        ):
            _fail("stocktake_difference_file_invalid", "service_unavailable", "盘点附件文件证据无效")
        serial_count = sum(len(serials_by_line[row.id]) for row in scope_lines) + sum(
            1 for row in scope_observations if row.serial_id is not None
        )
        total = _quantity_sum(
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
        _validate_historical_authorization(db, completion)
        if (
            completion.task_id != task.id
            or completion.round_id != round_row.id
            or completion.count_line_count != len(scope_lines)
            or completion.observation_line_count != len(scope_observations)
            or completion.serial_count != serial_count
            or completion.total_counted_qty != total
            or completion.zero_confirmed
            != (not scope_lines and not scope_observations)
            or completion.evidence_manifest_sha256 != expected_manifest
        ):
            _fail("stocktake_difference_scope_manifest_invalid", "service_unavailable", "盘点范围实盘封印无法重算")

    count_manifest = count_service._persisted_count_manifest(
        db, task, round_row, completions=completions
    )
    round_manifest = count_service._round_manifest(
        task.id,
        round_row.id,
        completions,
        submission.sealing_completion_id,
    )
    expected_submission_request = count_service._sha256(
        {
            "count_manifest_sha256": count_manifest,
            "round_manifest_sha256": round_manifest,
            "schema": "cloud_oam.stocktake.initial_round_submission.v1",
            "sealing_completion_id": str(submission.sealing_completion_id),
        }
    )
    expected_submission_idempotency = count_service._sha256(
        {
            "round_id": str(round_row.id),
            "schema": "cloud_oam.stocktake.initial_round_idempotency.v1",
            "task_id": str(task.id),
        }
    )
    total = _quantity_sum(tuple(row.total_counted_qty for row in completions))
    if (
        submission.scope_count != len(scopes)
        or submission.zero_scope_count != sum(1 for row in completions if row.zero_confirmed)
        or submission.count_line_count != len(count_lines)
        or submission.observation_line_count != len(observations)
        or submission.serial_count != len(count_serials)
        + sum(1 for row in observations if row.serial_id is not None)
        or submission.total_counted_qty != total
        or submission.round_manifest_sha256 != round_manifest
        or submission.count_manifest_sha256 != count_manifest
        or submission.request_sha256 != expected_submission_request
        or submission.idempotency_key_hash != expected_submission_idempotency
        or round_row.count_manifest_sha256 != count_manifest
    ):
        _fail("stocktake_difference_round_manifest_invalid", "service_unavailable", "初盘轮次封印无法重算")


def _validate_historical_authorization(
    db: Session,
    completion: StocktakeScopeCountCompletion,
) -> None:
    assignment = db.scalar(
        _select_only_reference_statement(
            db,
            select(RoleAssignment).where(
                RoleAssignment.id
                == completion.completed_role_assignment_id
            ),
        )
        .execution_options(populate_existing=True)
    )
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    completed_at = _as_utc(completion.completed_at)
    expected_hash = count_service._sha256(
        {
            "assignment_id": str(completion.completed_role_assignment_id),
            "authorization_version": completion.authorization_version,
            "completed_at": count_service._timestamp(completed_at),
            "person_id": str(completion.completed_by_person_id),
            "role_code": completion.role_code,
            "schema": "cloud_oam.stocktake.scope_count_authorization.v1",
            "scope_id": completion.scope_id_snapshot,
            "scope_type": completion.scope_type,
            "user_id": completion.completed_by_user_id,
        }
    )
    if (
        assignment is None
        or role is None
        or assignment.user_id != completion.completed_by_user_id
        or role.code != completion.role_code
        or role.is_external
        or assignment.scope_type != completion.scope_type
        or assignment.scope_id != completion.scope_id_snapshot
        or _as_utc(assignment.valid_from) > completed_at
        or (assignment.valid_to is not None and completed_at >= _as_utc(assignment.valid_to))
        or (assignment.revoked_at is not None and completed_at >= _as_utc(assignment.revoked_at))
        or expected_hash != completion.authorization_sha256
    ):
        _fail("stocktake_difference_authorization_manifest_invalid", "service_unavailable", "盘点范围授权封印无法重算")


def _replay_scope_expected_states(
    db: Session,
    *,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    plans: Sequence[task_service._ScopePlan],
    snapshots: Sequence[StocktakeSnapshotLine],
    snapshot_accounts: Mapping[uuid.UUID, StockAccount],
    observations: Sequence[StocktakeCountObservation],
    completions: Sequence[StocktakeScopeCountCompletion],
) -> _ReplayEvidence:
    """Rebuild each scope's book at its own server-sealed count cursor.

    ``cutoff_replay`` scopes may continue operating while people count.  Their
    physical result must therefore be compared with the immutable movement
    graph after the cutoff and through that scope's completion cursor.  Hard
    scopes use the same graph as a proof that no in-scope mutation escaped the
    freeze.  Cursor bounds, never timestamps, choose the replay window.
    """

    cutoff_cursor = task.cutoff_ledger_cursor
    if type(cutoff_cursor) is not int or cutoff_cursor < 0:
        _fail("stocktake_difference_cutoff_missing", "service_unavailable", "盘点截止游标缺失")
    completion_by_scope = {row.scope_id: row for row in completions}
    if len(completion_by_scope) != len(scopes):
        _fail("stocktake_difference_count_cursor_missing", "service_unavailable", "实盘范围游标封印不完整")
    count_cursors: dict[uuid.UUID, int] = {}
    for scope in scopes:
        value = completion_by_scope[scope.id].count_ledger_cursor
        if type(value) is not int or value < cutoff_cursor:
            _fail(
                "stocktake_difference_count_cursor_missing",
                "service_unavailable",
                "实盘范围缺少可信账本游标封印，旧证据禁止推断",
            )
        count_cursors[scope.id] = value
    head = db.scalar(
        select(InventoryLedgerHead)
        .where(
            InventoryLedgerHead.id == INVENTORY_LEDGER_HEAD_ID,
            InventoryLedgerHead.stream_key == INVENTORY_STREAM_KEY,
        )
        .execution_options(populate_existing=True)
    )
    current_cursor = (
        head.next_cursor - 1
        if head is not None
        and head.stream_key == INVENTORY_STREAM_KEY
        and type(head.next_cursor) is int
        and head.next_cursor > 0
        else None
    )
    if current_cursor is None or any(value > current_cursor for value in count_cursors.values()):
        _fail(
            "stocktake_difference_count_cursor_ahead",
            "service_unavailable",
            "实盘范围游标超出当前不可变账本",
        )

    plan_by_scope = {row.scope_id: row for row in plans}
    coordinate_predicates = tuple(
        and_(
            StockAccount.owner_org_id == scope.owner_org_id,
            StockAccount.location_id == scope.location_id,
        )
        for scope in scopes
    )
    scoped_accounts = tuple(
        db.scalars(
            select(StockAccount)
            .where(or_(*coordinate_predicates))
            .order_by(StockAccount.id)
            .execution_options(populate_existing=True)
        ).all()
    ) if coordinate_predicates else ()
    accounts: dict[uuid.UUID, StockAccount] = dict(snapshot_accounts)
    accounts.update((row.id, row) for row in scoped_accounts)
    account_scope: dict[uuid.UUID, uuid.UUID] = {}
    for account in accounts.values():
        matching = tuple(
            scope.id
            for scope in scopes
            if _account_matches_scope(account, scope, plan_by_scope.get(scope.id))
        )
        if len(matching) > 1:
            _fail(
                "stocktake_difference_replay_scope_ambiguous",
                "service_unavailable",
                "账本交易端点无法唯一归入冻结盘点范围",
            )
        if matching:
            account_scope[account.id] = matching[0]

    for snapshot in snapshots:
        if account_scope.get(snapshot.stock_account_id) != snapshot.scope_id:
            _fail(
                "stocktake_difference_snapshot_scope_mismatch",
                "service_unavailable",
                "截止快照账户无法归入原盘点范围",
            )
    max_cursor = max(count_cursors.values(), default=cutoff_cursor)
    scoped_account_ids = tuple(sorted(account_scope, key=str))
    movement_rows = tuple(
        db.execute(
            select(InventoryTransaction, InventoryMovement)
            .join(
                InventoryMovement,
                InventoryMovement.transaction_id == InventoryTransaction.id,
            )
            .where(
                InventoryTransaction.ledger_cursor > cutoff_cursor,
                InventoryTransaction.ledger_cursor <= max_cursor,
                or_(
                    InventoryMovement.from_account_id.in_(scoped_account_ids),
                    InventoryMovement.to_account_id.in_(scoped_account_ids),
                ),
            )
            .order_by(
                InventoryTransaction.ledger_cursor,
                InventoryMovement.line_no,
                InventoryMovement.id,
            )
        ).all()
    ) if scoped_account_ids and max_cursor > cutoff_cursor else ()
    movement_endpoint_ids = {
        account_id
        for _transaction, movement in movement_rows
        for account_id in (movement.from_account_id, movement.to_account_id)
        if account_id is not None
    }
    missing_endpoint_ids = tuple(
        sorted(movement_endpoint_ids.difference(accounts), key=str)
    )
    if missing_endpoint_ids:
        endpoint_accounts = tuple(
            db.scalars(
                select(StockAccount)
                .where(StockAccount.id.in_(missing_endpoint_ids))
                .order_by(StockAccount.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        if len(endpoint_accounts) != len(missing_endpoint_ids):
            _fail(
                "stocktake_difference_replay_account_missing",
                "service_unavailable",
                "截止后库存流水端点账户缺失",
            )
        accounts.update((row.id, row) for row in endpoint_accounts)
    movement_ids = tuple(row[1].id for row in movement_rows)
    serial_rows = tuple(
        db.scalars(
            select(InventoryMovementSerial)
            .where(InventoryMovementSerial.movement_id.in_(movement_ids))
            .order_by(
                InventoryMovementSerial.movement_id,
                InventoryMovementSerial.serial_id,
            )
        ).all()
    ) if movement_ids else ()
    serials_by_movement: dict[uuid.UUID, list[uuid.UUID]] = defaultdict(list)
    movement_by_id = {row[1].id: row[1] for row in movement_rows}
    for row in serial_rows:
        movement = movement_by_id.get(row.movement_id)
        if movement is None or row.transaction_id != movement.transaction_id:
            _fail(
                "stocktake_difference_replay_serial_graph_invalid",
                "service_unavailable",
                "截止后 SN 流水引用图不完整",
            )
        serials_by_movement[row.movement_id].append(row.serial_id)

    rows_by_cursor: dict[int, list[tuple[InventoryTransaction, InventoryMovement]]] = defaultdict(list)
    transaction_by_cursor: dict[int, uuid.UUID] = {}
    for transaction, movement in movement_rows:
        cursor = transaction.ledger_cursor
        if (
            type(cursor) is not int
            or transaction.status != "posted"
            or movement.transaction_id != transaction.id
            or movement.quantity <= _ZERO
            or (
                cursor in transaction_by_cursor
                and transaction_by_cursor[cursor] != transaction.id
            )
        ):
            _fail(
                "stocktake_difference_replay_ledger_invalid",
                "service_unavailable",
                "截止后库存流水证据不完整或相互矛盾",
            )
        transaction_by_cursor[cursor] = transaction.id
        rows_by_cursor[cursor].append((transaction, movement))

    snapshots_by_scope: dict[uuid.UUID, list[StocktakeSnapshotLine]] = defaultdict(list)
    for row in snapshots:
        snapshots_by_scope[row.scope_id].append(row)
    states: dict[uuid.UUID, _ScopeExpectedState] = {}
    for scope in scopes:
        scope_account_ids = {
            account_id
            for account_id, assigned_scope_id in account_scope.items()
            if assigned_scope_id == scope.id
        }
        quantities = {account_id: _ZERO for account_id in scope_account_ids}
        serial_accounts: dict[uuid.UUID, uuid.UUID] = {}
        for snapshot in snapshots_by_scope.get(scope.id, ()):
            quantities[snapshot.stock_account_id] = snapshot.book_qty
            for serial_id in _serial_ids_from_snapshot(snapshot):
                if serial_id in serial_accounts:
                    _fail(
                        "stocktake_difference_snapshot_serial_duplicate",
                        "service_unavailable",
                        "截止快照包含重复 SN",
                    )
                serial_accounts[serial_id] = snapshot.stock_account_id

        for cursor in sorted(rows_by_cursor):
            if cursor > count_cursors[scope.id]:
                break
            touching = tuple(
                (transaction, movement)
                for transaction, movement in rows_by_cursor[cursor]
                if movement.from_account_id in scope_account_ids
                or movement.to_account_id in scope_account_ids
            )
            if not touching:
                continue
            plan = plan_by_scope.get(scope.id)
            if plan is None:
                _fail("stocktake_difference_scope_invalid", "service_unavailable", "盘点范围证据无法重算")
            if plan.freeze_mode == "hard":
                _fail(
                    "stocktake_difference_hard_freeze_ledger_changed",
                    "service_unavailable",
                    "硬冻结范围在截止后出现库存流水，禁止继续评估",
                )
            if plan.freeze_mode != "cutoff_replay":
                _fail("stocktake_difference_freeze_invalid", "service_unavailable", "盘点冻结模式无效")

            deltas: dict[uuid.UUID, Decimal] = defaultdict(lambda: _ZERO)
            for _transaction, movement in touching:
                if movement.from_account_id in scope_account_ids:
                    assert movement.from_account_id is not None
                    deltas[movement.from_account_id] -= movement.quantity
                if movement.to_account_id in scope_account_ids:
                    assert movement.to_account_id is not None
                    deltas[movement.to_account_id] += movement.quantity
            for account_id, delta in deltas.items():
                quantities[account_id] += delta
            if any(value < _ZERO for value in quantities.values()):
                _fail(
                    "stocktake_difference_replay_negative_book",
                    "service_unavailable",
                    "截止后流水回放产生负账面数量",
                )

            for _transaction, movement in touching:
                for serial_id in serials_by_movement.get(movement.id, ()):
                    from_in_scope = movement.from_account_id in scope_account_ids
                    to_in_scope = movement.to_account_id in scope_account_ids
                    if from_in_scope:
                        if serial_accounts.get(serial_id) != movement.from_account_id:
                            _fail(
                                "stocktake_difference_replay_serial_source_invalid",
                                "service_unavailable",
                                "截止后 SN 流水来源与回放位置不一致",
                            )
                        serial_accounts.pop(serial_id)
                    if to_in_scope:
                        if not from_in_scope and serial_id in serial_accounts:
                            _fail(
                                "stocktake_difference_replay_serial_duplicate",
                                "service_unavailable",
                                "截止后 SN 流水造成范围内重复归属",
                            )
                        assert movement.to_account_id is not None
                        serial_accounts[serial_id] = movement.to_account_id
        states[scope.id] = _ScopeExpectedState(
            quantities=dict(quantities),
            serial_accounts=dict(serial_accounts),
        )

    observation_accounts: dict[uuid.UUID, uuid.UUID] = {}
    scope_by_id = {row.id: row for row in scopes}
    for observation in observations:
        if observation.verification_status != "verified":
            continue
        scope = scope_by_id.get(observation.scope_id)
        if scope is None:
            _fail("stocktake_difference_observation_scope_mismatch", "service_unavailable", "现场观察超出冻结范围")
        matching = tuple(
            account.id
            for account in accounts.values()
            if account_scope.get(account.id) == scope.id
            and _account_matches_observation(account, observation)
        )
        if len(matching) > 1:
            _fail(
                "stocktake_difference_observation_account_ambiguous",
                "service_unavailable",
                "现场观察无法唯一映射到截止后库存账户",
            )
        if matching:
            observation_accounts[observation.id] = matching[0]
    return _ReplayEvidence(
        accounts=accounts,
        states=states,
        observation_accounts=observation_accounts,
        movements=tuple(
            (
                movement,
                tuple(serials_by_movement.get(movement.id, ())),
            )
            for _transaction, movement in movement_rows
        ),
    )


def _account_matches_scope(
    account: StockAccount,
    scope: FormalStocktakeScope,
    plan: task_service._ScopePlan | None,
) -> bool:
    return bool(
        plan is not None
        and account.owner_org_id == scope.owner_org_id
        and account.location_id == scope.location_id
        and task_service._account_matches_plan(account, plan)
    )


def _account_matches_observation(
    account: StockAccount,
    observation: StocktakeCountObservation,
) -> bool:
    return (
        observation.material_id is not None
        and account.owner_org_id == observation.owner_org_id
        and account.location_id == observation.location_id
        and (
            account.custodian_person_id is None
            or account.custodian_person_id
            == observation.custodian_person_id_snapshot
        )
        and account.material_id == observation.material_id
        and account.condition_code == observation.condition_code
        and account.availability_bucket == observation.availability_bucket
        and account.lot_id == observation.lot_id
    )


def _plan_difference_set(
    *,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    snapshots: Sequence[StocktakeSnapshotLine],
    accounts: Mapping[uuid.UUID, StockAccount],
    count_lines: Sequence[StocktakeCountLine],
    count_serials: Sequence[StocktakeCountSerial],
    observations: Sequence[StocktakeCountObservation],
    replay_evidence: _ReplayEvidence,
) -> tuple[_DifferencePlan, ...]:
    if task.cutoff_at is None:
        _fail("stocktake_difference_cutoff_missing", "service_unavailable", "盘点截止时点缺失")
    scope_no = {row.id: row.scope_no for row in scopes}
    snapshot_by_account = {row.stock_account_id: row for row in snapshots}
    line_by_account = {row.stock_account_id: row for row in count_lines}
    line_by_id = {row.id: row for row in count_lines}
    expected_serial_account: dict[uuid.UUID, uuid.UUID] = {}
    expected_serial_scope: dict[uuid.UUID, uuid.UUID] = {}
    expected_quantities: dict[uuid.UUID, Decimal] = {}
    for scope_id, state in replay_evidence.states.items():
        for account_id, quantity in state.quantities.items():
            if account_id in expected_quantities:
                _fail("stocktake_difference_replay_scope_ambiguous", "service_unavailable", "回放账户重复归入盘点范围")
            expected_quantities[account_id] = quantity
        for serial_id, account_id in state.serial_accounts.items():
            if serial_id in expected_serial_account:
                _fail(
                    "stocktake_difference_replay_serial_boundary_ambiguous",
                    "service_unavailable",
                    "同一 SN 在不同范围实盘边界重复出现，必须复盘",
                )
            expected_serial_account[serial_id] = account_id
            expected_serial_scope[serial_id] = scope_id
    observed_serial: dict[uuid.UUID, tuple[uuid.UUID, uuid.UUID | None, uuid.UUID | None]] = {}
    serials_by_line: dict[uuid.UUID, list[StocktakeCountSerial]] = defaultdict(list)
    for row in count_serials:
        serials_by_line[row.count_line_id].append(row)
        line = line_by_id[row.count_line_id]
        snapshot = snapshot_by_account.get(line.stock_account_id)
        if snapshot is None:
            _fail("stocktake_difference_count_serial_invalid", "service_unavailable", "实盘 SN 未绑定截止账户")
        expected_here = row.serial_id in _serial_ids_from_snapshot(
            snapshot
        )
        if row.result != ("present" if expected_here else "unexpected"):
            _fail("stocktake_difference_count_serial_result_invalid", "service_unavailable", "初盘 SN 原始结果与截止快照不一致")
        observed_serial[row.serial_id] = (line.scope_id, line.stock_account_id, None)

    planned: list[_DifferencePlan] = []
    deficits: list[_QuantitySide] = []
    excesses: list[_QuantitySide] = []
    physical_quantities: dict[uuid.UUID, Decimal] = {
        row.stock_account_id: row.counted_qty for row in count_lines
    }
    physical_sides: dict[uuid.UUID, _QuantitySide] = {
        row.stock_account_id: _account_side(
            row.scope_id, accounts[row.stock_account_id], row.counted_qty
        )
        for row in count_lines
    }
    for observation in observations:
        if observation.verification_status == "pending_verification":
            planned.append(
                _plan(
                    scope_id=observation.scope_id,
                    difference_type="excess",
                    material_id=observation.material_id,
                    expected_account_id=None,
                    observed_account_id=None,
                    observed_line_id=observation.id,
                    serial_id=observation.serial_id,
                    book_qty=_ZERO,
                    counted_qty=observation.counted_qty,
                    affected_qty=observation.counted_qty,
                    reason_code="stocktake_pending_verification",
                    reason_text="现场实物标识未唯一解析，保留为不可过账待核实多余差异",
                )
            )
            continue
        if observation.material_id is None:
            _fail("stocktake_difference_verified_material_missing", "service_unavailable", "已核实现场观察缺少物料")
        observation_account_id = replay_evidence.observation_accounts.get(observation.id)
        if observation.serial_id is not None:
            observed_serial[observation.serial_id] = (
                observation.scope_id,
                observation_account_id,
                observation.id,
            )
        if observation_account_id is not None:
            if observation_account_id in physical_quantities:
                _fail(
                    "stocktake_difference_physical_dimension_duplicate",
                    "service_unavailable",
                    "同一库存账户同时存在账户实盘与现场新增观察",
                )
            physical_quantities[observation_account_id] = observation.counted_qty
            physical_sides[observation_account_id] = _observation_side(
                observation,
                account=accounts[observation_account_id],
            )
        elif observation.serial_id is None:
            excesses.append(_observation_side(observation))

    expected_serials_by_account: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    observed_serials_by_account: dict[uuid.UUID, set[uuid.UUID]] = defaultdict(set)
    for serial_id, account_id in expected_serial_account.items():
        expected_serials_by_account[account_id].add(serial_id)
    for serial_id, (_scope_id, account_id, _observation_id) in observed_serial.items():
        if account_id is not None:
            observed_serials_by_account[account_id].add(serial_id)

    for account_id in sorted(
        set(expected_quantities).union(physical_quantities), key=str
    ):
        account = accounts.get(account_id)
        if account is None:
            _fail("stocktake_difference_replay_account_missing", "service_unavailable", "回放库存账户缺失")
        expected_qty = expected_quantities.get(account_id, _ZERO)
        counted_qty = physical_quantities.get(account_id, _ZERO)
        expected_serials = expected_serials_by_account.get(account_id, set())
        observed_serials = observed_serials_by_account.get(account_id, set())
        serial_tracked = bool(expected_serials or observed_serials)
        if serial_tracked:
            if expected_qty != Decimal(len(expected_serials)) or counted_qty != Decimal(
                len(observed_serials)
            ):
                _fail(
                    "stocktake_difference_serial_quantity_invalid",
                    "service_unavailable",
                    "SN 数量与实盘游标回放账面或逐件证据不一致",
                )
            continue
        delta = counted_qty - expected_qty
        scope_id = _scope_for_account_in_replay(replay_evidence, account_id)
        if delta < _ZERO:
            deficits.append(_account_side(scope_id, account, -delta))
        elif delta > _ZERO:
            side = physical_sides.get(account_id)
            if side is None:
                _fail("stocktake_difference_physical_side_missing", "service_unavailable", "实盘差异维度缺失")
            side.remaining_qty = delta
            excesses.append(side)

    _pair_quantity_misplacements(deficits, excesses, planned)
    for side in deficits:
        if side.remaining_qty > _ZERO:
            planned.append(
                _plan(
                    scope_id=side.scope_id,
                    difference_type="missing",
                    material_id=side.material_id,
                    expected_account_id=side.account.id if side.account else None,
                    observed_account_id=None,
                    observed_line_id=None,
                    serial_id=None,
                    book_qty=side.remaining_qty,
                    counted_qty=_ZERO,
                    affected_qty=side.remaining_qty,
                    reason_code="stocktake_quantity_missing",
                    reason_text="截止快照数量高于初盘实物数量",
                )
            )
    for side in excesses:
        if side.remaining_qty > _ZERO:
            planned.append(
                _plan(
                    scope_id=side.scope_id,
                    difference_type="excess",
                    material_id=side.material_id,
                    expected_account_id=None,
                    observed_account_id=side.account.id if side.account else None,
                    observed_line_id=side.observation.id if side.observation else None,
                    serial_id=None,
                    book_qty=_ZERO,
                    counted_qty=side.remaining_qty,
                    affected_qty=side.remaining_qty,
                    reason_code="stocktake_quantity_excess",
                    reason_text="初盘实物数量高于截止快照数量",
                )
            )

    for serial_id in sorted(expected_serial_account, key=str):
        expected_account = accounts[expected_serial_account[serial_id]]
        expected_scope = expected_serial_scope[serial_id]
        observed = observed_serial.get(serial_id)
        if observed is None:
            planned.append(
                _plan(
                    scope_id=expected_scope,
                    difference_type="missing",
                    material_id=expected_account.material_id,
                    expected_account_id=expected_account.id,
                    observed_account_id=None,
                    observed_line_id=None,
                    serial_id=serial_id,
                    book_qty=_ONE,
                    counted_qty=_ZERO,
                    affected_qty=_ONE,
                    reason_code="stocktake_serial_missing",
                    reason_text="截止快照 SN 未在初盘中出现",
                )
            )
            continue
        observed_scope, observed_account_id, observed_line_id = observed
        if observed_account_id == expected_account.id:
            continue
        observed_side = (
            _account_side(observed_scope, accounts[observed_account_id], _ONE)
            if observed_account_id is not None
            else _observation_side(
                next(row for row in observations if row.id == observed_line_id)
            )
        )
        expected_side = _account_side(expected_scope, expected_account, _ONE)
        kind = _misplacement_kind(expected_side, observed_side) or "wrong_serial"
        planned.append(
            _plan(
                scope_id=observed_scope,
                difference_type=kind,
                material_id=expected_account.material_id,
                expected_account_id=expected_account.id,
                observed_account_id=observed_account_id,
                observed_line_id=observed_line_id,
                serial_id=serial_id,
                book_qty=_ONE,
                counted_qty=_ONE,
                affected_qty=_ONE,
                reason_code=f"stocktake_serial_{kind}",
                reason_text="截止快照 SN 与初盘所在实物维度不一致",
            )
        )
    for serial_id in sorted(set(observed_serial).difference(expected_serial_account), key=str):
        observed_scope, observed_account_id, observed_line_id = observed_serial[serial_id]
        material_id = (
            accounts[observed_account_id].material_id
            if observed_account_id is not None
            else next(row.material_id for row in observations if row.id == observed_line_id)
        )
        planned.append(
            _plan(
                scope_id=observed_scope,
                difference_type="wrong_serial",
                material_id=material_id,
                expected_account_id=None,
                observed_account_id=observed_account_id,
                observed_line_id=observed_line_id,
                serial_id=serial_id,
                book_qty=_ZERO,
                counted_qty=_ONE,
                affected_qty=_ONE,
                reason_code="stocktake_serial_unexpected",
                reason_text="初盘出现截止快照未包含的 SN",
            )
        )

    planned.sort(key=lambda row: _plan_sort_key(row, scope_no))
    expected_delta = _quantity_sum(
        tuple(row.counted_qty for row in count_lines)
        + tuple(row.counted_qty for row in observations)
    ) - _quantity_sum(tuple(expected_quantities.values()))
    actual_delta = sum((row.difference_qty for row in planned), start=_ZERO)
    if actual_delta != expected_delta or any(
        row.affected_qty <= _ZERO
        or row.difference_type == "control_unassigned"
        or row.difference_qty != row.counted_qty - row.book_qty
        for row in planned
    ):
        _fail("stocktake_difference_quantity_conservation_failed", "service_unavailable", "盘点差异数量不守恒")
    return tuple(planned)


def _pair_quantity_misplacements(
    deficits: Sequence[_QuantitySide],
    excesses: Sequence[_QuantitySide],
    planned: list[_DifferencePlan],
) -> None:
    while True:
        candidates: list[tuple[int, int, str]] = []
        for left_index, left in enumerate(deficits):
            if left.remaining_qty <= _ZERO:
                continue
            for right_index, right in enumerate(excesses):
                if right.remaining_qty <= _ZERO:
                    continue
                kind = _misplacement_kind(left, right)
                if kind is not None:
                    candidates.append((left_index, right_index, kind))
        selected: tuple[int, int, str] | None = None
        for candidate in candidates:
            left_index, right_index, _kind = candidate
            if (
                sum(1 for value in candidates if value[0] == left_index) == 1
                and sum(1 for value in candidates if value[1] == right_index) == 1
            ):
                selected = candidate
                break
        if selected is None:
            return
        left = deficits[selected[0]]
        right = excesses[selected[1]]
        quantity = min(left.remaining_qty, right.remaining_qty)
        left.remaining_qty -= quantity
        right.remaining_qty -= quantity
        planned.append(
            _plan(
                scope_id=right.scope_id,
                difference_type=selected[2],
                material_id=left.material_id,
                expected_account_id=left.account.id if left.account else None,
                observed_account_id=right.account.id if right.account else None,
                observed_line_id=right.observation.id if right.observation else None,
                serial_id=None,
                book_qty=quantity,
                counted_qty=quantity,
                affected_qty=quantity,
                reason_code=f"stocktake_quantity_{selected[2]}",
                reason_text="截止快照与初盘实物维度不一致",
            )
        )


def _misplacement_kind(left: _QuantitySide, right: _QuantitySide) -> str | None:
    if (
        left.material_id != right.material_id
        or left.owner_org_id != right.owner_org_id
        or left.availability_bucket != right.availability_bucket
    ):
        return None
    location_diff = left.location_id != right.location_id
    condition_diff = left.condition_code != right.condition_code
    lot_diff = left.lot_id != right.lot_id
    if location_diff and not condition_diff and not lot_diff:
        return "wrong_location"
    if (
        condition_diff
        and not location_diff
        and not lot_diff
        and left.custodian_person_id == right.custodian_person_id
    ):
        return "wrong_condition"
    if (
        lot_diff
        and not location_diff
        and not condition_diff
        and left.custodian_person_id == right.custodian_person_id
    ):
        return "wrong_lot"
    return None


def _account_side(scope_id: uuid.UUID, account: StockAccount, quantity: Decimal) -> _QuantitySide:
    return _QuantitySide(
        scope_id=scope_id,
        account=account,
        observation=None,
        material_id=account.material_id,
        owner_org_id=account.owner_org_id,
        location_id=account.location_id,
        custodian_person_id=account.custodian_person_id,
        condition_code=account.condition_code,
        availability_bucket=account.availability_bucket,
        lot_id=account.lot_id,
        remaining_qty=quantity,
    )


def _scope_for_account_in_replay(
    replay_evidence: _ReplayEvidence,
    account_id: uuid.UUID,
) -> uuid.UUID:
    matching = tuple(
        scope_id
        for scope_id, state in replay_evidence.states.items()
        if account_id in state.quantities
    )
    if len(matching) != 1:
        _fail(
            "stocktake_difference_replay_scope_ambiguous",
            "service_unavailable",
            "回放库存账户无法唯一归入盘点范围",
        )
    return matching[0]


def _observation_side(
    observation: StocktakeCountObservation,
    *,
    account: StockAccount | None = None,
) -> _QuantitySide:
    if observation.material_id is None:
        _fail("stocktake_difference_observation_material_missing", "service_unavailable", "现场观察物料未解析")
    return _QuantitySide(
        scope_id=observation.scope_id,
        account=account,
        observation=observation,
        material_id=observation.material_id,
        owner_org_id=observation.owner_org_id,
        location_id=observation.location_id,
        custodian_person_id=observation.custodian_person_id_snapshot,
        condition_code=observation.condition_code,
        availability_bucket=observation.availability_bucket,
        lot_id=observation.lot_id,
        remaining_qty=observation.counted_qty,
    )


def _plan(
    *,
    scope_id: uuid.UUID,
    difference_type: str,
    material_id: uuid.UUID | None,
    expected_account_id: uuid.UUID | None,
    observed_account_id: uuid.UUID | None,
    observed_line_id: uuid.UUID | None,
    serial_id: uuid.UUID | None,
    book_qty: Decimal,
    counted_qty: Decimal,
    affected_qty: Decimal,
    reason_code: str,
    reason_text: str,
) -> _DifferencePlan:
    return _DifferencePlan(
        scope_id=scope_id,
        difference_type=difference_type,
        material_id=material_id,
        expected_account_id=expected_account_id,
        observed_account_id=observed_account_id,
        observed_line_id=observed_line_id,
        serial_id=serial_id,
        book_qty=book_qty,
        counted_qty=counted_qty,
        difference_qty=counted_qty - book_qty,
        affected_qty=affected_qty,
        reason_code=reason_code,
        reason_text=reason_text,
    )


def _plan_sort_key(row: _DifferencePlan, scope_no: Mapping[uuid.UUID, int]) -> tuple[object, ...]:
    return (
        scope_no.get(row.scope_id, 0),
        _TYPE_ORDER[row.difference_type],
        str(row.material_id or ""),
        str(row.expected_account_id or ""),
        str(row.observed_account_id or ""),
        str(row.observed_line_id or ""),
        str(row.serial_id or ""),
    )


def _difference_summary(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    submission: StocktakeRoundSubmission,
    differences: Sequence[StocktakeDifference],
) -> dict[str, object]:
    ordered = tuple(sorted(differences, key=lambda row: row.difference_no))
    evidence = {
        "differences": [_difference_manifest_row(row) for row in ordered],
        "round_id": str(round_row.id),
        "round_submission_id": str(submission.id),
        "schema": "cloud_oam.stocktake.initial_difference_set.v1",
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


def _difference_manifest_row(row: StocktakeDifference) -> dict[str, object]:
    return {
        "affected_qty": _canonical_quantity(row.affected_qty),
        "book_qty": _canonical_quantity(row.book_qty),
        "control_snapshot_line_id": None,
        "counted_qty": _canonical_quantity(row.counted_qty),
        "created_at": _timestamp(row.created_at),
        "difference_id": str(row.id),
        "difference_no": row.difference_no,
        "difference_qty": _canonical_quantity(row.difference_qty),
        "difference_type": row.difference_type,
        "evidence_required": row.evidence_required,
        "expected_account_id": str(row.expected_account_id) if row.expected_account_id else None,
        "material_id": str(row.material_id) if row.material_id else None,
        "observed_account_id": str(row.observed_account_id) if row.observed_account_id else None,
        "observed_line_id": str(row.observed_line_id) if row.observed_line_id else None,
        "reason_code": row.reason_code,
        "reason_text": row.reason_text,
        "scope_id": str(row.scope_id) if row.scope_id else None,
        "serial_id": str(row.serial_id) if row.serial_id else None,
    }


def _validate_stored_difference_set(
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    submission: StocktakeRoundSubmission,
    completion: StocktakeDifferenceSetCompletion,
    differences: Sequence[StocktakeDifference],
    planned: Sequence[_DifferencePlan],
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    grant: ScopeGrant,
) -> None:
    if len(differences) != len(planned):
        _fail("stocktake_difference_replay_invalid", "service_unavailable", "已封印差异集与盘点证据不一致")
    for number, (row, plan) in enumerate(zip(differences, planned, strict=True), start=1):
        if row.difference_no != number or _stored_plan(row) != plan:
            _fail("stocktake_difference_replay_invalid", "service_unavailable", "已封印差异明细无法从盘点证据重算")
    summary = _difference_summary(task, round_row, submission, differences)
    if (
        completion.task_id != task.id
        or completion.round_id != round_row.id
        or completion.round_submission_id != submission.id
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
        != _difference_authorization_sha256(
            actor,
            assignment,
            grant,
            completion.completed_at,
        )
        or _as_utc(completion.completed_at) < _as_utc(submission.submitted_at)
        or completion.created_at != completion.completed_at
    ):
        _fail("stocktake_difference_replay_invalid", "service_unavailable", "已封印差异集汇总无法重算")


def _stored_plan(row: StocktakeDifference) -> _DifferencePlan:
    if row.control_snapshot_line_id is not None or not row.evidence_required:
        _fail("stocktake_difference_control_row_forbidden", "service_unavailable", "非期初差异集包含控制总账差异")
    return _DifferencePlan(
        scope_id=row.scope_id,
        difference_type=row.difference_type,
        material_id=row.material_id,
        expected_account_id=row.expected_account_id,
        observed_account_id=row.observed_account_id,
        observed_line_id=row.observed_line_id,
        serial_id=row.serial_id,
        book_qty=row.book_qty,
        counted_qty=row.counted_qty,
        difference_qty=row.difference_qty,
        affected_qty=row.affected_qty,
        reason_code=row.reason_code or "",
        reason_text=row.reason_text,
    )


def _result(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    completion: StocktakeDifferenceSetCompletion,
) -> StocktakeDifferenceResult:
    return StocktakeDifferenceResult(
        task_id=task.id,
        round_id=round_row.id,
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
    )


def _snapshot_serial_ids(snapshots: Sequence[StocktakeSnapshotLine]) -> set[uuid.UUID]:
    values: set[uuid.UUID] = set()
    for snapshot in snapshots:
        for value in _serial_ids_from_snapshot(snapshot):
            if value in values:
                _fail("stocktake_difference_snapshot_serial_duplicate", "service_unavailable", "截止快照包含重复 SN")
            values.add(value)
    return values


def _serial_ids_from_snapshot(snapshot: StocktakeSnapshotLine) -> frozenset[uuid.UUID]:
    values: set[uuid.UUID] = set()
    if not isinstance(snapshot.serial_snapshot_jsonb, list):
        _fail("stocktake_difference_snapshot_serial_invalid", "service_unavailable", "截止快照 SN 证据无效")
    for document in snapshot.serial_snapshot_jsonb:
        if not isinstance(document, dict) or not isinstance(document.get("serial_id"), str):
            _fail("stocktake_difference_snapshot_serial_invalid", "service_unavailable", "截止快照 SN 证据无效")
        try:
            value = uuid.UUID(document["serial_id"])
        except (TypeError, ValueError, AttributeError):
            _fail("stocktake_difference_snapshot_serial_invalid", "service_unavailable", "截止快照 SN 证据无效")
        if value in values:
            _fail("stocktake_difference_snapshot_serial_duplicate", "service_unavailable", "截止快照账户包含重复 SN")
        values.add(value)
    return frozenset(values)


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail("formal_principal_required", "forbidden", "盘点差异评估必须使用正式权限主体")
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
        or not isinstance(actor.authorization_version, int)
        or actor.authorization_version <= 0
    ):
        _fail("stocktake_difference_actor_inactive", "forbidden", "当前账号或人员状态不允许评估盘点差异")
    return actor


def _require_current_actor(db: Session, supplied: FormalPrincipal, now: datetime) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError:
        _fail("stocktake_difference_actor_not_current", "forbidden", "正式权限上下文已失效")
    if current.person_id != supplied.person_id or current.authorization_version != supplied.authorization_version:
        _fail("stocktake_difference_actor_principal_stale", "precondition_failed", "权限版本已变化，请重新读取后再操作")
    if current.account_status != "active" or current.employment_status != "active" or current.access_mode != "active":
        _fail("stocktake_difference_actor_inactive", "forbidden", "当前账号或人员状态不允许评估盘点差异")
    return current


def _validate_command(command: GenerateStocktakeDifferenceCommand) -> GenerateStocktakeDifferenceCommand:
    if not isinstance(command, GenerateStocktakeDifferenceCommand):
        _fail("stocktake_difference_command_invalid", "invalid_request", "盘点差异评估命令无效")
    task_id = _require_uuid("task_id", command.task_id)
    round_id = _require_uuid("round_id", command.round_id)
    if not isinstance(command.expected_task_version, int) or isinstance(command.expected_task_version, bool) or command.expected_task_version < 0:
        _fail("stocktake_difference_version_invalid", "invalid_request", "盘点任务期望版本无效")
    return GenerateStocktakeDifferenceCommand(task_id, round_id, command.expected_task_version)


def _request_hmac(
    secret: bytes,
    actor: FormalPrincipal,
    command: GenerateStocktakeDifferenceCommand,
) -> str:
    return _hmac_hex(
        secret,
        {
            "actor_authorization_version": actor.authorization_version,
            "actor_person_id": str(actor.person_id),
            "actor_user_id": actor.user_id,
            "expected_task_version": command.expected_task_version,
            "round_id": str(command.round_id),
            "schema": "cloud_oam.stocktake.initial_difference_request.v1",
            "task_id": str(command.task_id),
        },
    )


def _difference_authorization_sha256(
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    grant: ScopeGrant,
    completed_at: datetime,
) -> str:
    return _sha256(
        {
            "assignment_id": str(assignment.id),
            "authorization_version": actor.authorization_version,
            "completed_at": _timestamp(completed_at),
            "person_id": str(actor.person_id),
            "role_code": grant.role_code,
            "schema": "cloud_oam.stocktake.difference_authorization.v1",
            "scope_id": grant.scope_id,
            "scope_type": grant.scope_type,
            "user_id": actor.user_id,
        }
    )


def _idempotency_hmac(secret: bytes, actor_user_id: str, path: str, raw_key: str) -> str:
    return _hmac_hex(
        secret,
        {
            "actor_user_id": actor_user_id,
            "idempotency_key": raw_key,
            "method": "POST",
            "path": path,
            "schema": "cloud_oam.stocktake.initial_difference_idempotency.v1",
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
    except (TypeError, ValueError):
        _fail("stocktake_difference_document_invalid", "invalid_request", "盘点差异评估文档无法规范化")


def _canonical_quantity(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.001")), "f")


def _quantity_sum(values: Sequence[Decimal]) -> Decimal:
    total = sum(values, start=_ZERO)
    if not isinstance(total, Decimal) or not total.is_finite() or total < _ZERO:
        _fail("stocktake_difference_quantity_invalid", "service_unavailable", "盘点差异数量证据无效")
    return total.quantize(Decimal("0.001"))


def _timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_uuid(field: str, value: object) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail("stocktake_difference_uuid_invalid", "invalid_request", f"{field} 无效")
    return value


def _require_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not _PRINTABLE.fullmatch(value):
        _fail("stocktake_difference_idempotency_key_invalid", "invalid_request", "Idempotency-Key 无效")
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    secret = value.encode("utf-8") if isinstance(value, str) else value
    if (
        not isinstance(secret, bytes)
        or len(secret) < 32
        or any(marker in secret.lower().decode("utf-8", errors="ignore") for marker in _PLACEHOLDERS)
    ):
        _fail("stocktake_difference_hmac_unavailable", "service_unavailable", "盘点差异幂等 HMAC 配置不可用")
    return secret


def _require_trace_request_id(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not _SAFE_TRACE.fullmatch(value):
        _fail("stocktake_difference_trace_id_invalid", "invalid_request", "请求追踪标识无效")
    return value


def _request_reference(raw: str) -> str:
    digest = hashlib.sha256(f"cloud_oam.stocktake.difference.trace.v1\0{raw}".encode("utf-8")).hexdigest()
    return f"stocktake-difference-request-{digest}"


def _lock_coordinate(namespace: str, value: str) -> int:
    digest = hashlib.sha256(
        f"cloud_oam.inventory.lock.v1\0{namespace}\0{value}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


def _take_advisory_locks(db: Session, coordinates: Sequence[int]) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for coordinate in sorted(set(coordinates)):
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": coordinate})


def _select_only_reference_statement(db: Session, statement):
    """Use the 0032 owner lock on PostgreSQL; retain local lock semantics."""

    if db.get_bind().dialect.name != "postgresql":
        return statement.with_for_update()
    return statement


def _database_now(db: Session) -> datetime:
    if db.get_bind().dialect.name == "postgresql":
        value = db.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            _fail("stocktake_difference_database_time_unavailable", "service_unavailable", "数据库时间不可用")
        return _as_utc(value)
    return datetime.now(timezone.utc)


def _same_uuid(left: object, right: object) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fail(code: str, category: str, message: str) -> None:
    raise StocktakeDifferenceError(code, category, message)


__all__ = [
    "GenerateStocktakeDifferenceCommand",
    "StocktakeDifferenceError",
    "StocktakeDifferenceResult",
    "generate_stocktake_initial_differences",
]
