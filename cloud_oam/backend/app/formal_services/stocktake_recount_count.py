"""Immutable physical-count submission for non-opening recount rounds.

Only scopes selected by the immutable ``StocktakeRecountCase`` assignment
graph participate in a recount round.  Each selected scope is sealed at a
server-derived inventory-ledger cursor under the same ledger-head-first lock
order as inventory posting.  Earlier count rounds and their evidence remain
untouched.

The caller owns the transaction.  This module flushes but never commits or
rolls back and never creates inventory, difference, review, posting,
notification, outbox, or reconciliation facts.
"""

from __future__ import annotations

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
    load_formal_principal,
    lock_formal_principal_graph,
)
from ..foundation_models import (
    DocumentAttachment,
    FileObject,
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
from ..inventory_models import StockAccount, StockLocation
from ..models import User
from ..stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeCountSerial,
    StocktakeRecountCase,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)
from . import stocktake_count as count_service
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


StocktakeSnapshotCountInput = count_service.StocktakeSnapshotCountInput
StocktakePhysicalObservationInput = count_service.StocktakePhysicalObservationInput

_NON_OPENING_TYPES: Final[frozenset[str]] = review_service.NON_OPENING_TYPES
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


class StocktakeRecountCountError(RuntimeError):
    """Stable database-detail-free recount-count failure."""

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
class SubmitStocktakeRecountScopeCountCommand:
    task_id: uuid.UUID
    round_id: uuid.UUID
    scope_id: uuid.UUID
    count_mode: str
    account_counts: tuple[StocktakeSnapshotCountInput, ...] = ()
    physical_observations: tuple[StocktakePhysicalObservationInput, ...] = ()
    evidence_file_ids: tuple[uuid.UUID, ...] = ()
    zero_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class StocktakeRecountScopeCountResult:
    task_id: uuid.UUID
    round_id: uuid.UUID
    scope_id: uuid.UUID
    recount_case_id: uuid.UUID
    task_status: str
    round_status: str
    task_version: int
    scope_completed: bool
    round_submitted: bool
    count_ledger_cursor: int
    evidence_file_count: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class SealedRecountAssignmentGraph:
    case: StocktakeRecountCase
    assignments: tuple[StocktakeRecountScopeAssignment, ...]
    scopes: tuple[FormalStocktakeScope, ...]
    selected_scopes: tuple[FormalStocktakeScope, ...]
    selected_scope_ids: frozenset[uuid.UUID]
    source_evidence: review_service.SealedNonOpeningDifferenceEvidence


def submit_stocktake_recount_scope_count(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitStocktakeRecountScopeCountCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeRecountScopeCountResult:
    """Seal one selected recount scope without committing the transaction."""

    try:
        return _submit_stocktake_recount_scope_count(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    except StocktakeRecountCountError:
        raise
    except count_service.StocktakeCountError as exc:
        code = exc.code.replace("stocktake_count_", "stocktake_recount_count_", 1)
        raise StocktakeRecountCountError(code, exc.category, exc.message) from None
    except review_service.StocktakeReviewError:
        _fail(
            "stocktake_recount_count_source_evidence_invalid",
            "service_unavailable",
            "复盘来源的差异、复核或分配证据无法重证",
        )
    except recount_service.StocktakeRecountError:
        _fail(
            "stocktake_recount_count_case_evidence_invalid",
            "service_unavailable",
            "复盘案例、触发复核或开启审计证据无法重证",
        )
    except AuditChainError:
        _fail(
            "stocktake_recount_count_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，本次复盘提交未完成",
        )
    except IntegrityError:
        _fail(
            "stocktake_recount_count_concurrent_conflict",
            "conflict",
            "复盘提交发生并发冲突，请回滚并重新读取",
        )
    except DBAPIError:
        _fail(
            "stocktake_recount_count_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了复盘提交，请回滚并重新读取",
        )
    raise AssertionError("unreachable recount-count boundary")


def _submit_stocktake_recount_scope_count(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitStocktakeRecountScopeCountCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeRecountScopeCountResult:
    supplied = _validate_supplied_actor(actor)
    checked = _validate_command(command)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    trace_id = _require_trace_request_id(trace_request_id)
    path = (
        f"/api/v1/stocktakes/{checked.task_id}/rounds/"
        f"{checked.round_id}/scopes/{checked.scope_id}/recount-count"
    )
    key_hash = _idempotency_hmac(secret, supplied.user_id, path, raw_key)
    request_hash = _request_hmac(secret, supplied, checked)
    _take_advisory_locks(
        db,
        (
            _lock_coordinate("stocktake-recount-count-idempotency", key_hash),
            _lock_coordinate("stocktake-recount-count-task", str(checked.task_id)),
            _lock_coordinate("stocktake-recount-count-round", str(checked.round_id)),
        ),
    )

    # Match the global posting order exactly: the ledger head is always the
    # first row lock and the resulting boundary is server-derived.
    count_ledger_cursor = count_service._lock_current_ledger_cursor(db)
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked.task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None or task.task_type not in _NON_OPENING_TYPES:
        _fail("stocktake_recount_count_task_not_found", "not_found", "非期初盘点任务不存在")
    round_row = db.scalar(
        select(StocktakeRound)
        .where(StocktakeRound.id == checked.round_id, StocktakeRound.task_id == task.id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if round_row is None:
        _fail("stocktake_recount_count_round_not_found", "not_found", "当前复盘轮次不存在")
    lock_nonopening_stocktake_review_graph(db, task.id, round_row.id)

    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task.id)
            .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    graph = _load_and_validate_recount_assignment_graph(
        db, task=task, round_row=round_row, scopes=scopes
    )
    scope_by_id = {row.id: row for row in graph.selected_scopes}
    scope = scope_by_id.get(checked.scope_id)
    if scope is None:
        _fail(
            "stocktake_recount_count_scope_not_selected",
            "forbidden",
            "该范围未被当前复盘案例选中",
        )
    assignment_snapshot = next(
        row for row in graph.assignments if row.scope_id == scope.id
    )
    lock_formal_principal_graph(
        db,
        tuple(
            sorted(
                {
                    supplied.user_id,
                    *(row.assignee_user_id for row in graph.assignments),
                    graph.case.opened_by_user_id,
                }
            )
        ),
    )
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    live_assignment, grant = _authorize_exact_recount_actor(
        db,
        actor=current,
        task=task,
        scope=scope,
        assignment_snapshot=assignment_snapshot,
        now=now,
    )
    plans = task_service._load_and_validate_scope_plans(db, task, scopes, now=now)
    plan_by_scope = {row.scope_id: row for row in plans}
    plan = plan_by_scope.get(scope.id)
    if plan is None:
        _evidence_invalid("复盘范围无法与任务冻结计划唯一对应")
    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id == task.id)
            .order_by(InventoryFreeze.stocktake_scope_id, InventoryFreeze.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    freeze = next((row for row in freezes if row.stocktake_scope_id == scope.id), None)
    _validate_recount_freeze(scope, plan.freeze_mode, freeze, now)
    expected_mode = "blind" if task.blind_count else "open"
    if checked.count_mode != expected_mode:
        _fail(
            "stocktake_recount_count_mode_task_mismatch",
            "precondition_failed",
            "提交盘点模式与任务冻结的明盘或盲盘契约不一致",
        )

    snapshots = tuple(
        db.scalars(
            select(StocktakeSnapshotLine)
            .where(StocktakeSnapshotLine.task_id == task.id)
            .order_by(StocktakeSnapshotLine.scope_id, StocktakeSnapshotLine.stock_account_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    accounts = count_service._lock_snapshot_accounts(db, snapshots)
    count_service._validate_snapshot_manifest(task, plans, snapshots, accounts)
    scope_snapshots = tuple(row for row in snapshots if row.scope_id == scope.id)
    files = count_service._lock_evidence_files(
        db, checked.evidence_file_ids, current.user_id
    )

    existing = db.scalar(
        select(StocktakeScopeCountCompletion)
        .where(StocktakeScopeCountCompletion.idempotency_key_hash == key_hash)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        return replace(
            _validate_replay(
                db,
                task=task,
                round_row=round_row,
                graph=graph,
                scope=scope,
                actor=current,
                assignment_snapshot=assignment_snapshot,
                completion=existing,
                request_hash=request_hash,
                files=files,
                current_ledger_cursor=count_ledger_cursor,
            ),
            replayed=True,
        )
    if db.scalar(
        select(StocktakeScopeCountCompletion.id).where(
            StocktakeScopeCountCompletion.task_id == task.id,
            StocktakeScopeCountCompletion.round_id == round_row.id,
            StocktakeScopeCountCompletion.scope_id == scope.id,
        )
    ) is not None:
        _fail(
            "stocktake_recount_count_scope_already_completed",
            "conflict",
            "该复盘范围已经提交且不可修改",
        )
    if count_service._scope_has_partial_evidence(db, task.id, round_row.id, scope.id):
        _fail(
            "stocktake_recount_count_partial_evidence_exists",
            "conflict",
            "该复盘范围存在未封印证据，请回滚并重新读取",
        )
    _validate_new_count_state(
        task=task,
        round_row=round_row,
        graph=graph,
        now=now,
        count_ledger_cursor=count_ledger_cursor,
    )

    prepared_counts = count_service._prepare_snapshot_counts(
        db,
        task=task,
        scope=scope,
        snapshots=scope_snapshots,
        accounts=accounts,
        values=checked.account_counts,
        count_mode=checked.count_mode,
    )
    prepared_observations = count_service._prepare_observations(
        db,
        task=task,
        scope=scope,
        snapshots=scope_snapshots,
        accounts=accounts,
        values=checked.physical_observations,
    )
    if not scope_snapshots and not prepared_observations and not checked.zero_confirmed:
        _fail(
            "stocktake_recount_count_zero_confirmation_required",
            "invalid_request",
            "空复盘范围必须显式零确认",
        )
    if checked.zero_confirmed and (
        scope_snapshots or prepared_observations or prepared_counts
    ):
        _fail(
            "stocktake_recount_count_zero_confirmation_conflict",
            "invalid_request",
            "零确认不能与账面账户或实盘记录同时提交",
        )
    count_service._validate_round_serial_uniqueness(
        db, round_row.id, prepared_counts, prepared_observations
    )

    _head, audit_proof = _lock_audit_chain_head_with_proof(
        db, stream_key=INVENTORY_STREAM_KEY
    )
    _verify_recount_source_audits(db, graph=graph, proof=audit_proof)
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    live_assignment, grant = _authorize_exact_recount_actor(
        db,
        actor=current,
        task=task,
        scope=scope,
        assignment_snapshot=assignment_snapshot,
        now=now,
        lock_rows=False,
    )
    _validate_new_count_state(
        task=task,
        round_row=round_row,
        graph=graph,
        now=now,
        count_ledger_cursor=count_ledger_cursor,
    )
    _validate_recount_freeze(scope, plan.freeze_mode, freeze, now)
    return _write_scope_count(
        db,
        actor=current,
        grant=grant,
        assignment=live_assignment,
        assignment_snapshot=assignment_snapshot,
        task=task,
        round_row=round_row,
        graph=graph,
        scope=scope,
        prepared_counts=prepared_counts,
        prepared_observations=prepared_observations,
        files=files,
        zero_confirmed=checked.zero_confirmed,
        key_hash=key_hash,
        request_hash=request_hash,
        count_ledger_cursor=count_ledger_cursor,
        secret=secret,
        trace_request_id=trace_id,
        now=now,
    )


def _write_scope_count(
    db: Session,
    *,
    actor: FormalPrincipal,
    grant: ScopeGrant,
    assignment: RoleAssignment,
    assignment_snapshot: StocktakeRecountScopeAssignment,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    graph: SealedRecountAssignmentGraph,
    scope: FormalStocktakeScope,
    prepared_counts: Sequence[count_service._PreparedCount],
    prepared_observations: Sequence[count_service._PreparedObservation],
    files: Sequence[FileObject],
    zero_confirmed: bool,
    key_hash: str,
    request_hash: str,
    count_ledger_cursor: int,
    secret: bytes,
    trace_request_id: str,
    now: datetime,
) -> StocktakeRecountScopeCountResult:
    lines: list[StocktakeCountLine] = []
    serial_count = 0
    for prepared in prepared_counts:
        value = prepared.value
        line = StocktakeCountLine(
            id=uuid.uuid4(),
            task_id=task.id,
            round_id=round_row.id,
            scope_id=scope.id,
            stock_account_id=prepared.account.id,
            counted_qty=value.counted_qty,
            count_method=value.count_method,
            reason_code=value.reason_code,
            remark=value.remark,
            counted_by_user_id=actor.user_id,
            counted_at=now,
            created_at=now,
            updated_at=now,
        )
        db.add(line)
        lines.append(line)
        for serial in prepared.serials:
            db.add(
                StocktakeCountSerial(
                    count_line_id=line.id,
                    round_id=round_row.id,
                    serial_id=serial.id,
                    result=(
                        "present"
                        if serial.id in prepared.snapshot_serial_ids
                        else "unexpected"
                    ),
                    created_at=now,
                )
            )
            serial_count += 1

    existing_observation_count = db.scalar(
        select(func.count())
        .select_from(StocktakeCountObservation)
        .where(StocktakeCountObservation.round_id == round_row.id)
    ) or 0
    observations: list[StocktakeCountObservation] = []
    for offset, prepared in enumerate(prepared_observations, start=1):
        value = prepared.value
        row = StocktakeCountObservation(
            id=uuid.uuid4(),
            task_id=task.id,
            round_id=round_row.id,
            scope_id=scope.id,
            observation_no=existing_observation_count + offset,
            owner_org_id=scope.owner_org_id,
            location_id=scope.location_id,
            custodian_person_id_snapshot=scope.custodian_person_id_snapshot,
            material_id=(prepared.material.id if prepared.material is not None else None),
            material_identifier_raw=value.material_identifier_raw,
            material_identifier_type=value.material_identifier_type,
            condition_code=value.condition_code,
            availability_bucket=value.availability_bucket,
            lot_id=prepared.lot.id if prepared.lot is not None else None,
            lot_no_raw=value.lot_no_raw,
            serial_id=prepared.serial.id if prepared.serial is not None else None,
            serial_no_raw=value.serial_no_raw,
            serial_identifier_type=value.serial_identifier_type,
            counted_qty=value.counted_qty,
            verification_status=prepared.verification_status,
            count_method=value.count_method,
            reason_code=value.reason_code,
            remark=value.remark,
            counted_by_user_id=actor.user_id,
            counted_at=now,
            dimension_sha256=prepared.dimension_sha256,
            request_sha256=request_hash,
            idempotency_key_hash=count_service._child_hmac(
                secret, key_hash, prepared.dimension_sha256
            ),
            created_at=now,
        )
        db.add(row)
        observations.append(row)
        if prepared.serial is not None:
            serial_count += 1
    db.flush()

    authorization_hash = count_service._authorization_sha256(
        actor, assignment, grant, now
    )
    completion_id = uuid.uuid4()
    evidence_manifest = count_service._scope_evidence_manifest(
        db,
        completion_id=completion_id,
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        lines=lines,
        observations=observations,
        files=files,
        authorization_sha256=authorization_hash,
        count_ledger_cursor=count_ledger_cursor,
    )
    total = count_service._quantity_sum(
        tuple(row.counted_qty for row in lines)
        + tuple(row.counted_qty for row in observations)
    )
    completion = StocktakeScopeCountCompletion(
        id=completion_id,
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        count_line_count=len(lines),
        observation_line_count=len(observations),
        serial_count=serial_count,
        total_counted_qty=total,
        zero_confirmed=zero_confirmed,
        evidence_manifest_sha256=evidence_manifest,
        request_sha256=request_hash,
        idempotency_key_hash=key_hash,
        completed_by_user_id=actor.user_id,
        completed_by_person_id=actor.person_id,
        completed_role_assignment_id=assignment.id,
        authorization_version=actor.authorization_version,
        role_code=grant.role_code,
        scope_type=grant.scope_type,
        scope_id_snapshot=grant.scope_id,
        authorization_sha256=authorization_hash,
        count_ledger_cursor=count_ledger_cursor,
        completed_at=now,
        created_at=now,
    )
    db.add(completion)
    db.flush()
    for file_row in files:
        db.add(
            DocumentAttachment(
                id=uuid.uuid4(),
                document_type="stocktake_scope_count_completion",
                document_id=str(completion.id),
                file_id=file_row.id,
                attachment_type="stocktake_evidence",
                status="active",
                uploaded_by=actor.user_id,
                created_at=now,
            )
        )
    db.flush()

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
    completion_scope_ids = {row.scope_id for row in completions}
    if not completion_scope_ids.issubset(graph.selected_scope_ids):
        _evidence_invalid("当前复盘轮次包含未被案例选中的范围封印")
    sealed = completion_scope_ids == graph.selected_scope_ids
    if sealed:
        _seal_recount_round(
            db,
            actor=actor,
            assignment=assignment,
            task=task,
            round_row=round_row,
            graph=graph,
            completions=completions,
            sealing_completion_id=completion.id,
            now=now,
        )

    db.add(
        StateTransitionEvent(
            aggregate_type="stocktake_scope",
            aggregate_id=str(scope.id),
            from_status="counting",
            to_status="completed",
            reason="stocktake_recount_scope_count_completed",
            actor_id=actor.user_id,
            idempotency_key=_event_key("scope", completion.id),
            occurred_at=now,
            metadata_jsonb={
                "count_ledger_cursor": count_ledger_cursor,
                "count_mode": "blind" if task.blind_count else "open",
                "evidence_file_count": len(files),
                "recount_case_id": str(graph.case.id),
                "round_id": str(round_row.id),
                "round_no": round_row.round_no,
                "task_id": str(task.id),
                "zero_confirmed": zero_confirmed,
            },
            created_at=now,
        )
    )
    if sealed:
        db.add_all(
            [
                StateTransitionEvent(
                    aggregate_type="stocktake_round",
                    aggregate_id=str(round_row.id),
                    from_status="counting",
                    to_status="submitted",
                    reason="stocktake_recount_round_submitted",
                    actor_id=actor.user_id,
                    idempotency_key=_event_key("round", round_row.id),
                    occurred_at=now,
                    metadata_jsonb={
                        "difference_status": "not_evaluated",
                        "recount_case_id": str(graph.case.id),
                        "round_no": round_row.round_no,
                        "selected_scope_count": len(graph.selected_scope_ids),
                        "task_id": str(task.id),
                    },
                    created_at=now,
                ),
                StateTransitionEvent(
                    aggregate_type="stocktake_task",
                    aggregate_id=str(task.id),
                    from_status="counting",
                    to_status="submitted",
                    reason="stocktake_recount_round_submitted",
                    actor_id=actor.user_id,
                    idempotency_key=_event_key("task", round_row.id),
                    occurred_at=now,
                    metadata_jsonb={
                        "difference_status": "not_evaluated",
                        "recount_case_id": str(graph.case.id),
                        "round_id": str(round_row.id),
                        "task_version": task.version,
                    },
                    created_at=now,
                ),
            ]
        )
    db.flush()
    append_audit_event(
        db,
        stream_key=INVENTORY_STREAM_KEY,
        actor_user_id=actor.user_id,
        action="stocktake.recount_scope_count.submitted",
        aggregate_type="stocktake_scope",
        aggregate_id=str(scope.id),
        before_jsonb=None,
        after_jsonb={
            "assignment_id": str(assignment_snapshot.id),
            "count_ledger_cursor": count_ledger_cursor,
            "count_line_count": len(lines),
            "evidence_file_count": len(files),
            "observation_line_count": len(observations),
            "recount_case_id": str(graph.case.id),
            "round_id": str(round_row.id),
            "round_submitted": sealed,
            "serial_count": serial_count,
            "task_id": str(task.id),
            "zero_confirmed": zero_confirmed,
        },
        request_id=_request_reference(trace_request_id),
        occurred_at=now,
    )
    if sealed:
        append_audit_event(
            db,
            stream_key=INVENTORY_STREAM_KEY,
            actor_user_id=actor.user_id,
            action="stocktake.recount_round.submitted",
            aggregate_type="stocktake_round",
            aggregate_id=str(round_row.id),
            before_jsonb={"status": "counting"},
            after_jsonb={
                "difference_status": "not_evaluated",
                "recount_case_id": str(graph.case.id),
                "selected_scope_count": len(graph.selected_scope_ids),
                "status": "submitted",
                "task_id": str(task.id),
                "task_status": "submitted",
                "task_version": task.version,
            },
            request_id=_request_reference(trace_request_id),
            occurred_at=now,
        )
    db.flush()
    return StocktakeRecountScopeCountResult(
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        recount_case_id=graph.case.id,
        task_status=task.status,
        round_status=round_row.status,
        task_version=task.version,
        scope_completed=True,
        round_submitted=sealed,
        count_ledger_cursor=count_ledger_cursor,
        evidence_file_count=len(files),
    )


def _seal_recount_round(
    db: Session,
    *,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    graph: SealedRecountAssignmentGraph,
    completions: Sequence[StocktakeScopeCountCompletion],
    sealing_completion_id: uuid.UUID,
    now: datetime,
) -> None:
    count_manifest = _persisted_recount_count_manifest(
        db, task=task, round_row=round_row, graph=graph, completions=completions
    )
    round_manifest = _recount_round_manifest(
        task_id=task.id,
        round_id=round_row.id,
        recount_case_id=graph.case.id,
        source_round_id=graph.case.source_round_id,
        selected_scope_manifest_sha256=graph.case.scope_manifest_sha256,
        completions=completions,
        sealing_completion_id=sealing_completion_id,
    )
    submission = StocktakeRoundSubmission(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        sealing_completion_id=sealing_completion_id,
        scope_count=len(completions),
        zero_scope_count=sum(1 for row in completions if row.zero_confirmed),
        count_line_count=sum(row.count_line_count for row in completions),
        observation_line_count=sum(row.observation_line_count for row in completions),
        serial_count=sum(row.serial_count for row in completions),
        total_counted_qty=count_service._quantity_sum(
            tuple(row.total_counted_qty for row in completions)
        ),
        round_manifest_sha256=round_manifest,
        count_manifest_sha256=count_manifest,
        request_sha256=_sha256(
            {
                "count_manifest_sha256": count_manifest,
                "recount_case_id": str(graph.case.id),
                "round_manifest_sha256": round_manifest,
                "schema": "cloud_oam.stocktake.recount_round_submission.v1",
                "sealing_completion_id": str(sealing_completion_id),
            }
        ),
        idempotency_key_hash=_sha256(
            {
                "recount_case_id": str(graph.case.id),
                "round_id": str(round_row.id),
                "schema": "cloud_oam.stocktake.recount_round_submission_idempotency.v1",
                "task_id": str(task.id),
            }
        ),
        submitted_by_user_id=actor.user_id,
        submitted_by_person_id=actor.person_id,
        submitted_role_assignment_id=assignment.id,
        authorization_version=actor.authorization_version,
        submitted_at=now,
        created_at=now,
    )
    db.add(submission)
    db.flush()
    round_row.status = "submitted"
    round_row.submitted_by_user_id = actor.user_id
    round_row.submitted_at = now
    round_row.count_manifest_sha256 = count_manifest
    round_row.updated_at = now
    task.status = "submitted"
    task.submitted_at = now
    task.version += 1
    task.updated_at = now
    db.flush()


def _load_and_validate_recount_assignment_graph(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
) -> SealedRecountAssignmentGraph:
    if (
        task.task_type not in _NON_OPENING_TYPES
        or round_row.round_no <= 1
        or round_row.round_type != "recount"
        or round_row.recount_case_id is None
        or task.current_round_no < round_row.round_no
    ):
        _fail(
            "stocktake_recount_count_round_invalid",
            "precondition_failed",
            "盘点任务当前轮次不是有效复盘轮次",
        )
    case = db.scalar(
        select(StocktakeRecountCase)
        .where(
            StocktakeRecountCase.id == round_row.recount_case_id,
            StocktakeRecountCase.task_id == task.id,
            StocktakeRecountCase.next_round_no == round_row.round_no,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if case is None:
        _evidence_invalid("当前复盘轮次缺少唯一复盘案例")
    source_round = db.scalar(
        select(StocktakeRound)
        .where(
            StocktakeRound.id == case.source_round_id,
            StocktakeRound.task_id == task.id,
            StocktakeRound.round_no == round_row.round_no - 1,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if source_round is None or source_round.status != "submitted":
        _evidence_invalid("复盘来源轮次缺失或不是不可变已提交轮次")
    source_evidence = review_service._load_and_validate_sealed_difference_evidence(
        db, task=task, round_row=source_round, now=_database_now(db)
    )
    reviews = review_service._load_reviews(db, task.id, source_round.id)
    trigger_review, trigger_items = recount_service._validate_terminal_review_graph(
        db, evidence=source_evidence, reviews=reviews
    )
    required_scope_ids = recount_service._required_recount_scope_ids(
        source_evidence.differences, trigger_review, trigger_items
    )
    assignments = tuple(
        db.scalars(
            select(StocktakeRecountScopeAssignment)
            .where(StocktakeRecountScopeAssignment.recount_case_id == case.id)
            .order_by(StocktakeRecountScopeAssignment.scope_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    scope_by_id = {row.id: row for row in scopes}
    selected_scope_ids = frozenset(row.scope_id for row in assignments)
    if (
        not assignments
        or len(assignments) != case.scope_count
        or len(selected_scope_ids) != len(assignments)
        or selected_scope_ids != required_scope_ids
        or any(scope_id not in scope_by_id for scope_id in selected_scope_ids)
        or case.source_round_submission_id != source_evidence.submission.id
        or case.source_difference_completion_id != source_evidence.completion.id
        or case.trigger_review_id != trigger_review.id
        or case.next_round_no != source_round.round_no + 1
        or _as_utc(round_row.started_at) < _as_utc(case.opened_at)
    ):
        _evidence_invalid("复盘案例、来源轮次或逐范围分配图不完整")
    selected_scopes = tuple(
        sorted((scope_by_id[value] for value in selected_scope_ids), key=lambda row: row.scope_no)
    )
    scope_manifest = _selected_scope_manifest(task.id, source_round.id, selected_scopes)
    assignment_documents = [
        _stored_assignment_document(case.id, row) for row in assignments
    ]
    assignment_manifest = _sha256(
        {
            "assignments": assignment_documents,
            "recount_case_id": str(case.id),
            "schema": "cloud_oam.stocktake.nonopening_recount_assignments.v1",
        }
    )
    recount_manifest = _sha256(
        {
            "assignment_manifest_sha256": assignment_manifest,
            "difference_completion_id": str(source_evidence.completion.id),
            "difference_manifest_sha256": source_evidence.completion.difference_manifest_sha256,
            "next_round_no": case.next_round_no,
            "recount_case_id": str(case.id),
            "schema": "cloud_oam.stocktake.nonopening_recount.v1",
            "scope_manifest_sha256": scope_manifest,
            "source_round_id": str(source_round.id),
            "trigger_review_id": str(trigger_review.id),
        }
    )
    if (
        case.scope_manifest_sha256 != scope_manifest
        or case.assignment_manifest_sha256 != assignment_manifest
        or case.recount_manifest_sha256 != recount_manifest
        or _as_utc(case.created_at) != _as_utc(case.opened_at)
    ):
        _evidence_invalid("复盘案例清单无法从不可变来源重算")
    _validate_case_opener(db, task, case)
    for row in assignments:
        _validate_historical_assignment(db, case, row, scope_by_id[row.scope_id])
    return SealedRecountAssignmentGraph(
        case=case,
        assignments=assignments,
        scopes=tuple(scopes),
        selected_scopes=selected_scopes,
        selected_scope_ids=selected_scope_ids,
        source_evidence=source_evidence,
    )


def _verify_recount_source_audits(
    db: Session,
    *,
    graph: SealedRecountAssignmentGraph,
    proof: object,
) -> None:
    review_service._verify_evidence_audit(
        db, graph.source_evidence.source_audit_event, proof
    )
    trigger_review = db.get(StocktakeReview, graph.case.trigger_review_id)
    if trigger_review is None:
        _evidence_invalid("复盘案例触发复核事实缺失")
    review_service._verify_review_audit(db, trigger_review, proof)
    recount_service._verify_recount_audit(db, graph.case, proof)


def _validate_case_opener(
    db: Session, task: FormalStocktakeTask, case: StocktakeRecountCase
) -> None:
    assignment = db.get(RoleAssignment, case.opened_role_assignment_id)
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    user = db.get(User, case.opened_by_user_id)
    expected_scope = (
        case.role_code == "admin"
        and case.scope_type == "national"
        and case.scope_id_snapshot == "*"
    ) or (
        case.role_code == "provincial_manager"
        and case.scope_type == "organization"
        and _same_uuid(case.scope_id_snapshot, task.region_org_id)
    )
    expected_hash = _sha256(
        {
            "assignment_id": str(case.opened_role_assignment_id),
            "authorization_version": case.authorization_version,
            "occurred_at": _timestamp(case.opened_at),
            "person_id": str(case.opened_by_person_id),
            "role_code": case.role_code,
            "schema": "cloud_oam.stocktake.nonopening_recount_opener_authorization.v1",
            "scope_id": case.scope_id_snapshot,
            "scope_type": case.scope_type,
            "user_id": case.opened_by_user_id,
        }
    )
    occurred = _as_utc(case.opened_at)
    if (
        assignment is None
        or role is None
        or user is None
        or assignment.user_id != case.opened_by_user_id
        or user.person_id != case.opened_by_person_id
        or user.authorization_version < case.authorization_version
        or role.code != case.role_code
        or role.is_external
        or role.status != "active"
        or assignment.scope_type != case.scope_type
        or assignment.scope_id != case.scope_id_snapshot
        or _as_utc(assignment.valid_from) > occurred
        or (assignment.valid_to is not None and occurred >= _as_utc(assignment.valid_to))
        or (assignment.revoked_at is not None and occurred >= _as_utc(assignment.revoked_at))
        or not expected_scope
        or case.authorization_sha256 != expected_hash
    ):
        _evidence_invalid("复盘案例开启授权无法重证")


def _validate_historical_assignment(
    db: Session,
    case: StocktakeRecountCase,
    row: StocktakeRecountScopeAssignment,
    scope: FormalStocktakeScope,
) -> None:
    assignment = db.get(RoleAssignment, row.assignee_role_assignment_id)
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    user = db.get(User, row.assignee_user_id)
    occurred = _as_utc(row.assigned_at)
    if row.role_code == "admin":
        expected_scope = row.scope_type == "national" and row.scope_id_snapshot == "*"
    elif row.role_code == "provincial_manager":
        expected_scope = row.scope_type == "organization" and _same_uuid(
            row.scope_id_snapshot, scope.owner_org_id
        )
    else:
        expected_scope = (
            row.role_code == "technician"
            and row.scope_type == "person"
            and scope.custodian_person_id_snapshot is not None
            and _same_uuid(row.scope_id_snapshot, scope.custodian_person_id_snapshot)
        )
    expected_hash = _sha256(
        {
            "assignment_id": str(row.assignee_role_assignment_id),
            "authorization_version": row.authorization_version,
            "occurred_at": _timestamp(row.assigned_at),
            "person_id": str(row.assignee_person_id),
            "role_code": row.role_code,
            "schema": "cloud_oam.stocktake.nonopening_recount_scope_authorization.v1",
            "scope_id": row.scope_id_snapshot,
            "scope_type": row.scope_type,
            "user_id": row.assignee_user_id,
        }
    )
    if (
        row.recount_case_id != case.id
        or row.task_id != case.task_id
        or row.source_round_id != case.source_round_id
        or assignment is None
        or role is None
        or user is None
        or assignment.user_id != row.assignee_user_id
        or user.person_id != row.assignee_person_id
        or user.authorization_version < row.authorization_version
        or assignment.scope_type != row.scope_type
        or assignment.scope_id != row.scope_id_snapshot
        or role.code != row.role_code
        or role.is_external
        or role.status != "active"
        or _as_utc(assignment.valid_from) > occurred
        or (assignment.valid_to is not None and occurred >= _as_utc(assignment.valid_to))
        or (assignment.revoked_at is not None and occurred >= _as_utc(assignment.revoked_at))
        or not expected_scope
        or row.authorization_sha256 != expected_hash
        or row.assignment_sha256 != _sha256(_stored_assignment_document(case.id, row))
        or _as_utc(row.created_at) != occurred
    ):
        _evidence_invalid("复盘逐范围分配或历史授权无法重证")


def _authorize_exact_recount_actor(
    db: Session,
    *,
    actor: FormalPrincipal,
    task: FormalStocktakeTask,
    scope: FormalStocktakeScope,
    assignment_snapshot: StocktakeRecountScopeAssignment,
    now: datetime,
    lock_rows: bool = True,
) -> tuple[RoleAssignment, ScopeGrant]:
    if (
        actor.user_id != assignment_snapshot.assignee_user_id
        or actor.person_id != assignment_snapshot.assignee_person_id
        or actor.authorization_version < assignment_snapshot.authorization_version
    ):
        _fail(
            "stocktake_recount_count_not_assignee",
            "forbidden",
            "只有当前复盘案例精确分配的执行人员可以提交该范围",
        )
    candidates = tuple(
        row
        for row in actor.assignments
        if row.assignment_id == assignment_snapshot.assignee_role_assignment_id
        and row.role_code == assignment_snapshot.role_code
        and row.scope_type == assignment_snapshot.scope_type
        and row.scope_id == assignment_snapshot.scope_id_snapshot
    )
    if len(candidates) != 1:
        _fail(
            "stocktake_recount_count_assignment_not_current",
            "precondition_failed",
            "复盘分配所绑定的角色授权已变化",
        )
    grant = candidates[0]
    if grant.role_code == "admin":
        target_type, target_id = "national", "*"
    elif grant.role_code == "provincial_manager":
        target_type, target_id = "organization", str(scope.owner_org_id)
    else:
        target_type, target_id = "person", str(scope.custodian_person_id_snapshot)
    if not count_service._grant_allows_count(
        db,
        actor,
        grant,
        target_scope_type=target_type,
        target_scope_id=target_id,
    ):
        _fail(
            "stocktake_recount_count_scope_forbidden",
            "forbidden",
            "当前正式授权不再允许执行该复盘范围",
        )
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
        or (assignment.valid_to is not None and now >= _as_utc(assignment.valid_to))
        or role.status != "active"
        or role.is_external
        or role.code != grant.role_code
        or assignment.scope_type != grant.scope_type
        or assignment.scope_id != grant.scope_id
    ):
        _fail(
            "stocktake_recount_count_assignment_not_current",
            "precondition_failed",
            "复盘分配所绑定的角色授权已失效",
        )
    location = db.scalar(
        select(StockLocation)
        .where(StockLocation.id == scope.location_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if location is None or location.status != "active":
        _fail(
            "stocktake_recount_count_location_invalid",
            "precondition_failed",
            "复盘库位已失效",
        )
    if task.task_type == "personal" and grant.role_code != "technician":
        _fail(
            "stocktake_recount_count_personal_actor_invalid",
            "forbidden",
            "个人自盘复盘仍只能由本人提交",
        )
    return assignment, grant


def _validate_new_count_state(
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    graph: SealedRecountAssignmentGraph,
    now: datetime,
    count_ledger_cursor: int,
) -> None:
    if (
        task.status != "counting"
        or round_row.status != "counting"
        or task.current_round_no != round_row.round_no
        or round_row.round_no != graph.case.next_round_no
        or round_row.round_type != "recount"
        or round_row.recount_case_id != graph.case.id
        or round_row.submitted_at is not None
        or round_row.submitted_by_user_id is not None
        or round_row.count_manifest_sha256 is not None
        or task.cutoff_ledger_cursor is None
        or count_ledger_cursor < task.cutoff_ledger_cursor
        or (task.deadline is not None and now >= _as_utc(task.deadline))
    ):
        _fail(
            "stocktake_recount_count_state_invalid",
            "precondition_failed",
            "盘点任务或当前复盘轮次不可提交",
        )


def _validate_recount_freeze(
    scope: FormalStocktakeScope,
    expected_mode: str,
    freeze: InventoryFreeze | None,
    now: datetime,
) -> None:
    if (
        freeze is None
        or freeze.stocktake_scope_id != scope.id
        or freeze.scope_key != scope.scope_key
        or freeze.freeze_mode != expected_mode
        or freeze.status != "active"
        or freeze.valid_to is not None
        or _as_utc(freeze.valid_from) > now
    ):
        _fail(
            "stocktake_recount_count_scope_not_frozen",
            "precondition_failed",
            "复盘范围没有与任务证据一致的活动冻结",
        )


def _validate_replay(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    graph: SealedRecountAssignmentGraph,
    scope: FormalStocktakeScope,
    actor: FormalPrincipal,
    assignment_snapshot: StocktakeRecountScopeAssignment,
    completion: StocktakeScopeCountCompletion,
    request_hash: str,
    files: Sequence[FileObject],
    current_ledger_cursor: int,
) -> StocktakeRecountScopeCountResult:
    if (
        completion.task_id != task.id
        or completion.round_id != round_row.id
        or completion.scope_id != scope.id
        or completion.completed_by_user_id != actor.user_id
        or completion.completed_by_person_id != actor.person_id
        or completion.completed_role_assignment_id
        != assignment_snapshot.assignee_role_assignment_id
        or completion.authorization_version != actor.authorization_version
        or completion.request_sha256 != request_hash
        or completion.role_code != assignment_snapshot.role_code
        or completion.scope_type != assignment_snapshot.scope_type
        or completion.scope_id_snapshot != assignment_snapshot.scope_id_snapshot
        or task.status not in {"counting", "submitted"}
        or round_row.status not in {"counting", "submitted"}
        or type(completion.count_ledger_cursor) is not int
        or task.cutoff_ledger_cursor is None
        or completion.count_ledger_cursor < task.cutoff_ledger_cursor
        or completion.count_ledger_cursor > current_ledger_cursor
    ):
        _idempotency_conflict()
    attachments = tuple(
        db.scalars(
            select(DocumentAttachment)
            .where(
                DocumentAttachment.document_type == "stocktake_scope_count_completion",
                DocumentAttachment.document_id == str(completion.id),
                DocumentAttachment.attachment_type == "stocktake_evidence",
                DocumentAttachment.status == "active",
            )
            .order_by(DocumentAttachment.file_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if tuple(row.file_id for row in attachments) != tuple(row.id for row in files):
        _evidence_invalid("复盘附件重放证据不完整")
    lines = tuple(
        db.scalars(
            select(StocktakeCountLine)
            .where(
                StocktakeCountLine.task_id == task.id,
                StocktakeCountLine.round_id == round_row.id,
                StocktakeCountLine.scope_id == scope.id,
            )
            .order_by(StocktakeCountLine.stock_account_id)
        ).all()
    )
    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.round_id == round_row.id,
                StocktakeCountObservation.scope_id == scope.id,
            )
            .order_by(StocktakeCountObservation.observation_no)
        ).all()
    )
    expected = count_service._scope_evidence_manifest(
        db,
        completion_id=completion.id,
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        lines=lines,
        observations=observations,
        files=files,
        authorization_sha256=completion.authorization_sha256,
        count_ledger_cursor=completion.count_ledger_cursor,
    )
    if (
        completion.evidence_manifest_sha256 != expected
        or completion.count_line_count != len(lines)
        or completion.observation_line_count != len(observations)
    ):
        _evidence_invalid("复盘提交重放证据无法重算")
    if round_row.status == "submitted":
        _validate_sealed_recount_submission(db, task, round_row, graph)
    return StocktakeRecountScopeCountResult(
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        recount_case_id=graph.case.id,
        task_status=task.status,
        round_status=round_row.status,
        task_version=task.version,
        scope_completed=True,
        round_submitted=round_row.status == "submitted",
        count_ledger_cursor=completion.count_ledger_cursor,
        evidence_file_count=len(files),
    )


def _validate_sealed_recount_submission(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    graph: SealedRecountAssignmentGraph,
) -> StocktakeRoundSubmission:
    submissions = tuple(
        db.scalars(
            select(StocktakeRoundSubmission)
            .where(
                StocktakeRoundSubmission.task_id == task.id,
                StocktakeRoundSubmission.round_id == round_row.id,
            )
            .order_by(StocktakeRoundSubmission.id)
        ).all()
    )
    completions = tuple(
        db.scalars(
            select(StocktakeScopeCountCompletion)
            .where(
                StocktakeScopeCountCompletion.task_id == task.id,
                StocktakeScopeCountCompletion.round_id == round_row.id,
            )
            .order_by(StocktakeScopeCountCompletion.scope_id)
        ).all()
    )
    if (
        len(submissions) != 1
        or {row.scope_id for row in completions} != graph.selected_scope_ids
        or len(completions) != len(graph.selected_scope_ids)
    ):
        _evidence_invalid("已提交复盘轮次的范围封印不完整或不唯一")
    submission = submissions[0]
    count_manifest = _persisted_recount_count_manifest(
        db, task=task, round_row=round_row, graph=graph, completions=completions
    )
    round_manifest = _recount_round_manifest(
        task_id=task.id,
        round_id=round_row.id,
        recount_case_id=graph.case.id,
        source_round_id=graph.case.source_round_id,
        selected_scope_manifest_sha256=graph.case.scope_manifest_sha256,
        completions=completions,
        sealing_completion_id=submission.sealing_completion_id,
    )
    expected_request = _sha256(
        {
            "count_manifest_sha256": count_manifest,
            "recount_case_id": str(graph.case.id),
            "round_manifest_sha256": round_manifest,
            "schema": "cloud_oam.stocktake.recount_round_submission.v1",
            "sealing_completion_id": str(submission.sealing_completion_id),
        }
    )
    expected_idempotency = _sha256(
        {
            "recount_case_id": str(graph.case.id),
            "round_id": str(round_row.id),
            "schema": "cloud_oam.stocktake.recount_round_submission_idempotency.v1",
            "task_id": str(task.id),
        }
    )
    if (
        submission.sealing_completion_id not in {row.id for row in completions}
        or submission.scope_count != len(completions)
        or submission.zero_scope_count != sum(1 for row in completions if row.zero_confirmed)
        or submission.count_line_count != sum(row.count_line_count for row in completions)
        or submission.observation_line_count
        != sum(row.observation_line_count for row in completions)
        or submission.serial_count != sum(row.serial_count for row in completions)
        or submission.total_counted_qty
        != count_service._quantity_sum(tuple(row.total_counted_qty for row in completions))
        or submission.round_manifest_sha256 != round_manifest
        or submission.count_manifest_sha256 != count_manifest
        or submission.request_sha256 != expected_request
        or submission.idempotency_key_hash != expected_idempotency
        or round_row.count_manifest_sha256 != count_manifest
        or round_row.submitted_by_user_id != submission.submitted_by_user_id
        or _as_utc(round_row.submitted_at) != _as_utc(submission.submitted_at)
    ):
        _evidence_invalid("复盘轮次封印无法从逐范围事实重算")
    return submission


def _persisted_recount_count_manifest(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    graph: SealedRecountAssignmentGraph,
    completions: Sequence[StocktakeScopeCountCompletion],
) -> str:
    lines = tuple(
        db.scalars(
            select(StocktakeCountLine)
            .where(
                StocktakeCountLine.task_id == task.id,
                StocktakeCountLine.round_id == round_row.id,
            )
            .order_by(StocktakeCountLine.scope_id, StocktakeCountLine.stock_account_id)
        ).all()
    )
    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.round_id == round_row.id,
            )
            .order_by(StocktakeCountObservation.scope_id, StocktakeCountObservation.observation_no)
        ).all()
    )
    if any(row.scope_id not in graph.selected_scope_ids for row in (*lines, *observations)):
        _evidence_invalid("复盘计数清单包含未被案例选中的范围")
    line_ids = tuple(row.id for row in lines)
    serials = tuple(
        db.scalars(
            select(StocktakeCountSerial)
            .where(StocktakeCountSerial.count_line_id.in_(line_ids))
            .order_by(StocktakeCountSerial.count_line_id, StocktakeCountSerial.serial_id)
        ).all()
    ) if line_ids else ()
    serials_by_line: dict[uuid.UUID, list[StocktakeCountSerial]] = defaultdict(list)
    for row in serials:
        serials_by_line[row.count_line_id].append(row)
    ordered_completions = tuple(sorted(completions, key=lambda row: str(row.scope_id)))
    return _sha256(
        {
            "count_boundaries": [
                {
                    "count_ledger_cursor": row.count_ledger_cursor,
                    "evidence_manifest_sha256": row.evidence_manifest_sha256,
                    "scope_id": str(row.scope_id),
                }
                for row in ordered_completions
            ],
            "cutoff_at": _timestamp(task.cutoff_at),
            "cutoff_ledger_cursor": task.cutoff_ledger_cursor,
            "lines": [
                {
                    "count_line_id": str(row.id),
                    "counted_at": _timestamp(row.counted_at),
                    "counted_qty": _canonical_quantity(row.counted_qty),
                    "scope_id": str(row.scope_id),
                    "serials": [
                        {"result": child.result, "serial_id": str(child.serial_id)}
                        for child in serials_by_line.get(row.id, ())
                    ],
                    "stock_account_id": str(row.stock_account_id),
                }
                for row in lines
            ],
            "observations": [
                {
                    "counted_qty": _canonical_quantity(row.counted_qty),
                    "dimension_sha256": row.dimension_sha256,
                    "observation_id": str(row.id),
                    "scope_id": str(row.scope_id),
                    "serial_id": str(row.serial_id) if row.serial_id is not None else None,
                }
                for row in observations
            ],
            "recount_case_id": str(graph.case.id),
            "round_id": str(round_row.id),
            "schema": "cloud_oam.stocktake.recount_count_manifest.v1",
            "selected_scope_manifest_sha256": graph.case.scope_manifest_sha256,
            "snapshot_manifest_sha256": task.snapshot_manifest_sha256,
            "source_difference_completion_id": str(
                graph.case.source_difference_completion_id
            ),
            "source_round_id": str(graph.case.source_round_id),
            "task_id": str(task.id),
        }
    )


def _recount_round_manifest(
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    recount_case_id: uuid.UUID,
    source_round_id: uuid.UUID,
    selected_scope_manifest_sha256: str,
    completions: Sequence[StocktakeScopeCountCompletion],
    sealing_completion_id: uuid.UUID | None,
) -> str:
    return _sha256(
        {
            "completions": [
                {
                    "authorization_sha256": row.authorization_sha256,
                    "count_ledger_cursor": row.count_ledger_cursor,
                    "evidence_manifest_sha256": row.evidence_manifest_sha256,
                    "scope_id": str(row.scope_id),
                    "zero_confirmed": row.zero_confirmed,
                }
                for row in sorted(completions, key=lambda value: str(value.scope_id))
            ],
            "recount_case_id": str(recount_case_id),
            "round_id": str(round_id),
            "schema": "cloud_oam.stocktake.recount_round_manifest.v1",
            "sealing_completion_id": str(sealing_completion_id),
            "selected_scope_manifest_sha256": selected_scope_manifest_sha256,
            "source_round_id": str(source_round_id),
            "task_id": str(task_id),
        }
    )


def _selected_scope_manifest(
    task_id: uuid.UUID,
    source_round_id: uuid.UUID,
    scopes: Sequence[FormalStocktakeScope],
) -> str:
    return _sha256(
        {
            "schema": "cloud_oam.stocktake.nonopening_recount_scopes.v1",
            "scopes": [
                {
                    "scope_id": str(row.id),
                    "scope_no": row.scope_no,
                    "scope_sha256": row.scope_sha256,
                }
                for row in sorted(scopes, key=lambda value: value.scope_no)
            ],
            "source_round_id": str(source_round_id),
            "task_id": str(task_id),
        }
    )


def _stored_assignment_document(
    case_id: uuid.UUID, row: StocktakeRecountScopeAssignment
) -> dict[str, object]:
    return {
        "assignee_person_id": str(row.assignee_person_id),
        "assignee_role_assignment_id": str(row.assignee_role_assignment_id),
        "assignee_user_id": row.assignee_user_id,
        "assigned_at": _timestamp(row.assigned_at),
        "authorization_sha256": row.authorization_sha256,
        "authorization_version": row.authorization_version,
        "recount_case_id": str(case_id),
        "role_code": row.role_code,
        "schema": "cloud_oam.stocktake.nonopening_recount_assignment.v1",
        "scope_id": str(row.scope_id),
        "scope_id_snapshot": row.scope_id_snapshot,
        "scope_type": row.scope_type,
    }


def _validate_command(
    command: SubmitStocktakeRecountScopeCountCommand,
) -> SubmitStocktakeRecountScopeCountCommand:
    if not isinstance(command, SubmitStocktakeRecountScopeCountCommand):
        _fail("stocktake_recount_count_command_invalid", "invalid_request", "复盘提交命令无效")
    checked = count_service._validate_command(
        count_service.SubmitStocktakeInitialScopeCountCommand(
            task_id=command.task_id,
            round_id=command.round_id,
            scope_id=command.scope_id,
            count_mode=command.count_mode,
            account_counts=command.account_counts,
            physical_observations=command.physical_observations,
            evidence_file_ids=command.evidence_file_ids,
            zero_confirmed=command.zero_confirmed,
        )
    )
    return SubmitStocktakeRecountScopeCountCommand(
        task_id=checked.task_id,
        round_id=checked.round_id,
        scope_id=checked.scope_id,
        count_mode=checked.count_mode,
        account_counts=checked.account_counts,
        physical_observations=checked.physical_observations,
        evidence_file_ids=checked.evidence_file_ids,
        zero_confirmed=checked.zero_confirmed,
    )


def _request_hmac(
    secret: bytes,
    actor: FormalPrincipal,
    command: SubmitStocktakeRecountScopeCountCommand,
) -> str:
    base = count_service.SubmitStocktakeInitialScopeCountCommand(
        task_id=command.task_id,
        round_id=command.round_id,
        scope_id=command.scope_id,
        count_mode=command.count_mode,
        account_counts=command.account_counts,
        physical_observations=command.physical_observations,
        evidence_file_ids=command.evidence_file_ids,
        zero_confirmed=command.zero_confirmed,
    )
    return _hmac_hex(
        secret,
        {
            "base_request_sha256": count_service._request_hmac(secret, actor, base),
            "round_id": str(command.round_id),
            "schema": "cloud_oam.stocktake.recount_scope_count_request.v1",
            "scope_id": str(command.scope_id),
            "task_id": str(command.task_id),
        },
    )


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    try:
        return count_service._validate_supplied_actor(actor)
    except count_service.StocktakeCountError:
        raise


def _require_current_actor(
    db: Session, supplied: FormalPrincipal, now: datetime
) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError:
        _fail("stocktake_recount_count_actor_not_current", "forbidden", "正式权限上下文已失效")
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
        or current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail(
            "stocktake_recount_count_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取后再提交",
        )
    return current


def _require_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not _PRINTABLE.fullmatch(value):
        _fail("stocktake_recount_count_idempotency_key_invalid", "invalid_request", "Idempotency-Key 无效")
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    secret = value.encode("utf-8") if isinstance(value, str) else value
    if (
        not isinstance(secret, bytes)
        or len(secret) < 32
        or any(marker in secret.lower() for marker in (item.encode() for item in _PLACEHOLDERS))
    ):
        _fail(
            "stocktake_recount_count_hmac_unavailable",
            "service_unavailable",
            "复盘幂等 HMAC 配置不可用",
        )
    return secret


def _require_trace_request_id(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not _SAFE_TRACE.fullmatch(value):
        _fail("stocktake_recount_count_trace_id_invalid", "invalid_request", "请求追踪标识无效")
    return value


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
            "schema": "cloud_oam.stocktake.recount_scope_count_idempotency.v1",
        },
    )


def _event_key(kind: str, value: uuid.UUID) -> str:
    return f"stocktake-recount-count:{kind}:{value}"


def _request_reference(raw: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.stocktake.recount_count.trace.v1\0{raw}".encode("utf-8")
    ).hexdigest()
    return f"stocktake-recount-count-{digest}"


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
    if not isinstance(value, datetime):
        _fail("stocktake_recount_count_clock_unavailable", "service_unavailable", "数据库时间不可用")
    return _as_utc(value)


def _canonical_quantity(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.001")), "f")


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_uuid(left: object, right: object) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


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
        _fail("stocktake_recount_count_manifest_invalid", "invalid_request", "复盘清单无法规范化")


def _idempotency_conflict() -> None:
    _fail(
        "stocktake_recount_count_idempotency_conflict",
        "conflict",
        "幂等键已绑定不同复盘提交或历史封印无法重证",
    )


def _evidence_invalid(message: str) -> None:
    _fail("stocktake_recount_count_evidence_invalid", "service_unavailable", message)


def _fail(code: str, category: str, message: str) -> None:
    raise StocktakeRecountCountError(code, category, message)


__all__ = [
    "SealedRecountAssignmentGraph",
    "StocktakePhysicalObservationInput",
    "StocktakeRecountCountError",
    "StocktakeRecountScopeCountResult",
    "StocktakeSnapshotCountInput",
    "SubmitStocktakeRecountScopeCountCommand",
    "submit_stocktake_recount_scope_count",
]
