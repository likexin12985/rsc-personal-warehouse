"""Formal V1.0 non-opening stocktake draft and atomic-start service.

This module covers full, sample, ad-hoc, personal and termination stocktakes.
Opening establishment remains in its separately reviewed service because it
has an OAM-control-total boundary that normal stocktakes must never inherit.

The caller owns the surrounding transaction.  Public functions flush but
never commit or roll back, never call an external system, and never create or
modify inventory transactions, movements or balance projections.  Starting a
task pins the current inventory-ledger cursor and writes only stocktake scopes,
active freezes, immutable book/SN snapshots, an initial counting round, state
transitions and the inventory audit stream.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import hmac
import json
import re
from typing import Any, Final, Mapping, Sequence
import uuid

from pydantic import ValidationError
from sqlalchemy import func, or_, select, text
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
    Organization,
    Person,
    StateTransitionEvent,
)
from ..inventory_models import (
    FormalMaterial,
    InventoryLedgerHead,
    InventoryLot,
    InventoryMovement,
    InventorySerial,
    InventoryTransaction,
    MaterialInventoryPolicy,
    SerialCurrentPosition,
    StockAccount,
    StockBalance,
    StockLocation,
    CustodyAssignment,
)
from ..models import User
from ..stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    StocktakeRound,
    StocktakeSnapshotLine,
)
from ..stocktake_task_schemas import (
    PersonalStocktakeCreateIn,
    StocktakeScopeSelectionIn,
    StocktakeTaskCreateIn,
    StocktakeTaskStartIn,
)
from .audit_chain import (
    AuditChainError,
    append_audit_event,
    lock_audit_chain_head,
)
from .inventory_posting import INVENTORY_LEDGER_HEAD_ID, INVENTORY_STREAM_KEY
from .stocktake_task_policy import (
    StocktakeTaskPolicyError,
    require_atomic_start_path,
    require_stocktake_task_type,
)


STOCKTAKE_AGGREGATE: Final[str] = "stocktake_task"
_CREATE_COMMAND_SCHEMA: Final[str] = "cloud_oam.stocktake.command.v1"
_SNAPSHOT_SCHEMA: Final[str] = "cloud_oam.stocktake.snapshot.v1"
_ZERO: Final[Decimal] = Decimal("0.000")
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


class StocktakeTaskError(RuntimeError):
    """Stable database-detail-free error for the formal command boundary."""

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
class StocktakeTaskDraftResult:
    task_id: uuid.UUID
    task_no: str
    task_type: str
    status: str
    version: int
    scope_count: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class StocktakeTaskStartResult:
    task_id: uuid.UUID
    task_type: str
    status: str
    version: int
    cutoff_ledger_cursor: int
    initial_round_id: uuid.UUID
    scope_count: int
    snapshot_line_count: int
    active_freeze_count: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _ScopePlan:
    scope_id: uuid.UUID
    scope_no: int
    scope_mode: str
    owner_org_id: uuid.UUID
    location_id: uuid.UUID
    assignee_user_id: str
    custodian_person_id: uuid.UUID | None
    material_id: uuid.UUID | None
    condition_code: str | None
    availability_bucket: str | None
    freeze_mode: str
    scope_key: str
    scope_sha256: str


@dataclass(frozen=True, slots=True)
class _Snapshot:
    scope: _ScopePlan
    account: StockAccount
    book_qty: Decimal
    account_dimension_sha256: str
    serials: tuple[dict[str, object], ...]
    serial_sha256: str


def derive_stocktake_task_create_id(
    *,
    actor: FormalPrincipal,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    command_kind: str,
) -> uuid.UUID:
    """Return a server-only stable UUID for one create idempotency coordinate."""

    supplied = _validate_supplied_actor(actor)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    if command_kind not in {"managed", "personal"}:
        _fail("stocktake_command_kind_invalid", "invalid_request", "盘点创建命令类型无效")
    digest = hmac.new(
        secret,
        (
            "cloud_oam.stocktake.create_id.v1\0"
            f"actor={supplied.user_id}\0kind={command_kind}\0key={raw_key}"
        ).encode("utf-8"),
        hashlib.sha256,
    ).digest()
    coordinate = bytearray(digest[:16])
    coordinate[6] = (coordinate[6] & 0x0F) | 0x40
    coordinate[8] = (coordinate[8] & 0x3F) | 0x80
    return uuid.UUID(bytes=bytes(coordinate))


def create_stocktake_task_draft(
    db: Session,
    *,
    actor: FormalPrincipal,
    draft: StocktakeTaskCreateIn,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeTaskDraftResult:
    """Create a manager-owned full/sample/ad-hoc/termination draft."""

    return _public_boundary(
        lambda: _create_managed_draft_impl(
            db,
            actor=actor,
            draft=draft,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def create_personal_stocktake_draft(
    db: Session,
    *,
    actor: FormalPrincipal,
    draft: PersonalStocktakeCreateIn,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeTaskDraftResult:
    """Create a self-stocktake with identity, region and location derived locally."""

    return _public_boundary(
        lambda: _create_personal_draft_impl(
            db,
            actor=actor,
            draft=draft,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def start_stocktake_task(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
    command: StocktakeTaskStartIn,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeTaskStartResult:
    """Atomically record issued -> frozen -> counting without posting stock."""

    return _public_boundary(
        lambda: _start_stocktake_task_impl(
            db,
            actor=actor,
            task_id=task_id,
            command=command,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    )


def _public_boundary(operation):
    try:
        return operation()
    except StocktakeTaskError:
        raise
    except (ValidationError, StocktakeTaskPolicyError):
        raise StocktakeTaskError(
            "stocktake_command_invalid",
            "invalid_request",
            "盘点命令不符合正式数据契约",
        ) from None
    except AuditChainError:
        raise StocktakeTaskError(
            "stocktake_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，本次盘点操作未完成",
        ) from None
    except IntegrityError:
        raise StocktakeTaskError(
            "stocktake_concurrent_conflict",
            "conflict",
            "盘点任务发生并发冲突，请回滚并重新读取后再操作",
        ) from None
    except DBAPIError:
        raise StocktakeTaskError(
            "stocktake_database_unavailable",
            "service_unavailable",
            "数据库暂时不可用，本次盘点操作未完成",
        ) from None


def _create_managed_draft_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    draft: StocktakeTaskCreateIn,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeTaskDraftResult:
    supplied = _validate_supplied_actor(actor)
    checked = _validate_managed_draft(draft)
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    task_id = derive_stocktake_task_create_id(
        actor=supplied,
        idempotency_key=raw_key,
        idempotency_hmac_secret=secret,
        command_kind="managed",
    )
    key_hash = _idempotency_hmac(
        secret,
        supplied.user_id,
        "POST",
        "/api/v1/stocktakes",
        raw_key,
    )
    request_hash = _request_hmac(
        secret,
        {
            "actor_user_id": supplied.user_id,
            "command": checked.model_dump(mode="json"),
            "operation": "create_managed",
            "task_id": str(task_id),
        },
    )
    _take_advisory_locks(
        db,
        (
            _lock_coordinate("idempotency", key_hash),
            _lock_coordinate("stocktake-task", str(task_id)),
        ),
    )
    lock_formal_principal_graph(db, (supplied.user_id,))
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now=now)
    _authorize_manager(db, current, checked.region_org_id)
    replay = _load_create_replay(
        db,
        idempotency_key=_create_event_key(key_hash),
        request_hash=request_hash,
        expected_task_id=task_id,
    )
    if replay is not None:
        return replace(replay, replayed=True)
    _require_deadline(checked.deadline, now)
    plans = _prepare_managed_scope_plans(db, current, checked, now=now)
    return _write_draft(
        db,
        actor=current,
        task_id=task_id,
        task_type=checked.task_type,
        region_org_id=checked.region_org_id,
        blind_count=checked.blind_count,
        deadline=checked.deadline,
        note=checked.note,
        plans=plans,
        key_hash=key_hash,
        request_hash=request_hash,
        trace_request_id=trace_id,
        now=now,
    )


def _create_personal_draft_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    draft: PersonalStocktakeCreateIn,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeTaskDraftResult:
    supplied = _validate_supplied_actor(actor)
    checked = _validate_personal_draft(draft)
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    task_id = derive_stocktake_task_create_id(
        actor=supplied,
        idempotency_key=raw_key,
        idempotency_hmac_secret=secret,
        command_kind="personal",
    )
    key_hash = _idempotency_hmac(
        secret,
        supplied.user_id,
        "POST",
        "/api/v1/stocktakes/personal",
        raw_key,
    )
    request_hash = _request_hmac(
        secret,
        {
            "actor_user_id": supplied.user_id,
            "command": checked.model_dump(mode="json"),
            "operation": "create_personal",
            "task_id": str(task_id),
        },
    )
    _take_advisory_locks(
        db,
        (
            _lock_coordinate("idempotency", key_hash),
            _lock_coordinate("stocktake-task", str(task_id)),
        ),
    )
    lock_formal_principal_graph(db, (supplied.user_id,))
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now=now)
    _authorize_self_count(db, current)
    replay = _load_create_replay(
        db,
        idempotency_key=_create_event_key(key_hash),
        request_hash=request_hash,
        expected_task_id=task_id,
    )
    if replay is not None:
        return replace(replay, replayed=True)
    plan, region_org_id = _derive_personal_scope_plan(
        db,
        current,
        freeze_mode=checked.freeze_mode,
        now=now,
    )
    return _write_draft(
        db,
        actor=current,
        task_id=task_id,
        task_type="personal",
        region_org_id=region_org_id,
        blind_count=checked.blind_count,
        deadline=None,
        note=checked.note,
        plans=(plan,),
        key_hash=key_hash,
        request_hash=request_hash,
        trace_request_id=trace_id,
        now=now,
    )


def _write_draft(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
    task_type: str,
    region_org_id: uuid.UUID,
    blind_count: bool,
    deadline: datetime | None,
    note: str,
    plans: tuple[_ScopePlan, ...],
    key_hash: str,
    request_hash: str,
    trace_request_id: str,
    now: datetime,
) -> StocktakeTaskDraftResult:
    if db.get(FormalStocktakeTask, task_id) is not None:
        _fail("stocktake_task_id_conflict", "conflict", "盘点任务标识已被其他命令占用")
    task_no = f"STK-{task_id.hex[:20].upper()}"
    if db.scalar(
        select(FormalStocktakeTask.id).where(FormalStocktakeTask.task_no == task_no)
    ) is not None:
        _fail("stocktake_task_number_conflict", "conflict", "盘点任务号已被占用")
    scope_manifest = _scope_manifest_sha256(task_type, region_org_id, plans)
    task = FormalStocktakeTask(
        id=task_id,
        task_no=task_no,
        task_type=task_type,
        region_org_id=region_org_id,
        status="draft",
        blind_count=blind_count,
        cutoff_ledger_cursor=None,
        cutoff_at=None,
        scope_manifest_sha256=scope_manifest,
        snapshot_manifest_sha256=None,
        control_source_system_id=None,
        control_sync_run_id=None,
        control_snapshot_at=None,
        control_manifest_sha256=None,
        current_round_no=0,
        created_by_user_id=actor.user_id,
        deadline=deadline,
        issued_at=None,
        frozen_at=None,
        submitted_at=None,
        posted_at=None,
        closed_at=None,
        cancelled_at=None,
        version=0,
        note=note,
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.flush()
    for plan in plans:
        db.add(
            FormalStocktakeScope(
                id=plan.scope_id,
                task_id=task_id,
                scope_no=plan.scope_no,
                scope_mode=plan.scope_mode,
                location_id=plan.location_id,
                owner_org_id=plan.owner_org_id,
                custodian_person_id_snapshot=plan.custodian_person_id,
                assignee_user_id=plan.assignee_user_id,
                material_id=plan.material_id,
                condition_code=plan.condition_code,
                availability_bucket=plan.availability_bucket,
                scope_key=plan.scope_key,
                scope_sha256=plan.scope_sha256,
                created_at=now,
            )
        )
    result = StocktakeTaskDraftResult(
        task_id=task_id,
        task_no=task_no,
        task_type=task_type,
        status="draft",
        version=0,
        scope_count=len(plans),
    )
    db.add(
        StateTransitionEvent(
            aggregate_type=STOCKTAKE_AGGREGATE,
            aggregate_id=str(task_id),
            from_status=None,
            to_status="draft",
            reason="stocktake_task_created",
            actor_id=actor.user_id,
            idempotency_key=_create_event_key(key_hash),
            occurred_at=now,
            metadata_jsonb={
                "authorization_version": actor.authorization_version,
                "operation": "create",
                "request_hash": request_hash,
                "result": _draft_result_document(result),
                "schema": _CREATE_COMMAND_SCHEMA,
                "scope_manifest_sha256": scope_manifest,
                "scope_plan": [_scope_plan_document(row) for row in plans],
            },
            created_at=now,
        )
    )
    db.flush()
    append_audit_event(
        db,
        stream_key=INVENTORY_STREAM_KEY,
        actor_user_id=actor.user_id,
        action="stocktake.task.created",
        aggregate_type=STOCKTAKE_AGGREGATE,
        aggregate_id=str(task_id),
        before_jsonb=None,
        after_jsonb={
            "region_org_id": str(region_org_id),
            "scope_count": len(plans),
            "scope_manifest_sha256": scope_manifest,
            "status": "draft",
            "task_no": task_no,
            "task_type": task_type,
            "version": 0,
        },
        request_id=_request_reference(trace_request_id),
        occurred_at=now,
    )
    db.flush()
    return result


def _start_stocktake_task_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
    command: StocktakeTaskStartIn,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeTaskStartResult:
    supplied = _validate_supplied_actor(actor)
    checked_task_id = _require_uuid("task_id", task_id)
    checked = _validate_start_command(command)
    trace_id = _require_trace_request_id(trace_request_id)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    path = f"/api/v1/stocktakes/{checked_task_id}/start"
    key_hash = _idempotency_hmac(
        secret, supplied.user_id, "POST", path, raw_key
    )
    request_hash = _request_hmac(
        secret,
        {
            "actor_user_id": supplied.user_id,
            "expected_version": checked.expected_version,
            "operation": "start",
            "task_id": str(checked_task_id),
        },
    )
    _take_advisory_locks(
        db,
        (
            _lock_coordinate("idempotency", key_hash),
            _lock_coordinate("stocktake-task", str(checked_task_id)),
        ),
    )
    task_probe = db.get(FormalStocktakeTask, checked_task_id)
    if task_probe is None or task_probe.task_type == "opening":
        _fail("stocktake_task_not_found", "not_found", "非期初盘点任务不存在")
    lock_formal_principal_graph(db, (supplied.user_id,))
    preflight_at = _database_now(db)
    current = _require_current_actor(db, supplied, now=preflight_at)
    _authorize_task_start(db, current, task_probe)
    replay = _load_start_replay(
        db,
        idempotency_key=_start_event_key(key_hash, "counting"),
        request_hash=request_hash,
        expected_task_id=checked_task_id,
    )
    if replay is not None:
        return replace(replay, replayed=True)

    scope_probe = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == checked_task_id)
            .order_by(FormalStocktakeScope.scope_no)
        ).all()
    )
    if not scope_probe:
        _fail("stocktake_scopes_missing", "service_unavailable", "盘点草稿没有正式范围")
    _take_advisory_locks(
        db,
        tuple(
            _lock_coordinate(
                "stocktake-freeze-location",
                f"{row.owner_org_id}:{row.location_id}",
            )
            for row in scope_probe
        ),
    )

    head = db.scalar(
        select(InventoryLedgerHead)
        .where(
            InventoryLedgerHead.id == INVENTORY_LEDGER_HEAD_ID,
            InventoryLedgerHead.stream_key == INVENTORY_STREAM_KEY,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if head is None or not isinstance(head.next_cursor, int) or head.next_cursor <= 0:
        _fail(
            "inventory_ledger_head_unavailable",
            "service_unavailable",
            "库存账本游标未正确初始化",
        )
    cutoff_cursor = head.next_cursor - 1
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked_task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None or task.task_type == "opening":
        _fail("stocktake_task_not_found", "not_found", "非期初盘点任务不存在")
    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == checked_task_id)
            .order_by(FormalStocktakeScope.scope_no)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    assignee_user_ids = tuple(row.assignee_user_id for row in scopes)
    lock_formal_principal_graph(
        db,
        (supplied.user_id, *assignee_user_ids),
    )
    now = _database_now(db)
    cutoff_at = now
    current = _require_current_actor(db, supplied, now=now)
    _authorize_task_start(db, current, task)
    _require_startable_draft(db, task, scopes, checked.expected_version, now)
    plans = _load_and_validate_scope_plans(db, task, scopes, now=now)
    _require_no_active_overlapping_freeze(db, plans)
    snapshots = _build_snapshots(
        db,
        plans,
        cutoff_ledger_cursor=cutoff_cursor,
        cutoff_at=cutoff_at,
    )
    snapshot_manifest = _snapshot_manifest_sha256(
        task=task,
        cutoff_cursor=cutoff_cursor,
        plans=plans,
        snapshots=snapshots,
    )
    # The audit head is the final shared row in the inventory writer graph:
    # ledger -> task/principal -> locations/accounts/policies/SN/balances ->
    # audit.  Nothing below discovers or locks another mutable reference row.
    lock_audit_chain_head(db, stream_key=INVENTORY_STREAM_KEY)
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now=now)
    _authorize_task_start(db, current, task)
    if task.deadline is not None and _as_utc(task.deadline) <= now:
        _fail("stocktake_deadline_elapsed", "precondition_failed", "盘点截止时间已到，禁止启动")
    require_atomic_start_path("draft", ("issued", "frozen", "counting"))
    round_id = uuid.uuid4()

    task.status = "counting"
    task.cutoff_ledger_cursor = cutoff_cursor
    task.cutoff_at = cutoff_at
    task.snapshot_manifest_sha256 = snapshot_manifest
    task.current_round_no = 1
    task.issued_at = now
    task.frozen_at = now
    task.version = checked.expected_version + 1
    task.updated_at = now

    for plan in plans:
        db.add(
            InventoryFreeze(
                id=uuid.uuid4(),
                task_id=task.id,
                stocktake_scope_id=plan.scope_id,
                scope_key=plan.scope_key,
                freeze_mode=plan.freeze_mode,
                status="active",
                valid_from=now,
                valid_to=None,
                created_by_user_id=current.user_id,
                released_by_user_id=None,
                release_reason="",
                version=0,
                created_at=now,
                updated_at=now,
            )
        )
    for row in snapshots:
        db.add(
            StocktakeSnapshotLine(
                id=uuid.uuid4(),
                task_id=task.id,
                scope_id=row.scope.scope_id,
                stock_account_id=row.account.id,
                book_qty=row.book_qty,
                ledger_cursor=cutoff_cursor,
                account_dimension_sha256=row.account_dimension_sha256,
                serial_snapshot_jsonb=list(row.serials),
                serial_snapshot_sha256=row.serial_sha256,
                serial_count=len(row.serials),
                created_at=now,
            )
        )
    # Persist the complete freeze/snapshot boundary before round 1 makes the
    # task countable.  PostgreSQL guards can therefore reject an incomplete
    # start without relying on SQLAlchemy insert ordering.
    db.flush()
    db.add(
        StocktakeRound(
            id=round_id,
            task_id=task.id,
            round_no=1,
            round_type="initial",
            status="counting",
            submitted_by_user_id=None,
            started_at=now,
            submitted_at=None,
            count_manifest_sha256=None,
            idempotency_key_hash=key_hash,
            recount_case_id=None,
            created_at=now,
            updated_at=now,
        )
    )
    result = StocktakeTaskStartResult(
        task_id=task.id,
        task_type=task.task_type,
        status="counting",
        version=task.version,
        cutoff_ledger_cursor=cutoff_cursor,
        initial_round_id=round_id,
        scope_count=len(plans),
        snapshot_line_count=len(snapshots),
        active_freeze_count=len(plans),
    )
    transitions = (
        ("draft", "issued", "stocktake_task_issued"),
        ("issued", "frozen", "stocktake_task_frozen"),
        ("frozen", "counting", "stocktake_initial_round_started"),
    )
    for from_status, to_status, reason in transitions:
        metadata: dict[str, Any] = {
            "authorization_version": current.authorization_version,
            "cutoff_ledger_cursor": cutoff_cursor,
            "operation": "start",
            "request_hash": request_hash,
            "schema": _CREATE_COMMAND_SCHEMA,
            "scope_manifest_sha256": task.scope_manifest_sha256,
            "snapshot_manifest_sha256": snapshot_manifest,
        }
        if to_status == "counting":
            metadata["result"] = _start_result_document(result)
        db.add(
            StateTransitionEvent(
                aggregate_type=STOCKTAKE_AGGREGATE,
                aggregate_id=str(task.id),
                from_status=from_status,
                to_status=to_status,
                reason=reason,
                actor_id=current.user_id,
                idempotency_key=_start_event_key(key_hash, to_status),
                occurred_at=now,
                metadata_jsonb=metadata,
                created_at=now,
            )
        )
    db.flush()
    append_audit_event(
        db,
        stream_key=INVENTORY_STREAM_KEY,
        actor_user_id=current.user_id,
        action="stocktake.task.started",
        aggregate_type=STOCKTAKE_AGGREGATE,
        aggregate_id=str(task.id),
        before_jsonb={
            "status": "draft",
            "version": checked.expected_version,
        },
        after_jsonb={
            "active_freeze_count": len(plans),
            "cutoff_ledger_cursor": cutoff_cursor,
            "initial_round_id": str(round_id),
            "scope_count": len(plans),
            "snapshot_line_count": len(snapshots),
            "snapshot_manifest_sha256": snapshot_manifest,
            "status": "counting",
            "task_type": task.task_type,
            "version": task.version,
        },
        request_id=_request_reference(trace_id),
        occurred_at=now,
    )
    db.flush()
    return result


def _prepare_managed_scope_plans(
    db: Session,
    actor: FormalPrincipal,
    draft: StocktakeTaskCreateIn,
    *,
    now: datetime,
) -> tuple[_ScopePlan, ...]:
    region = _require_region(db, draft.region_org_id)
    _authorize_manager(db, actor, region.id)
    plans: list[_ScopePlan] = []
    assignee_cache: dict[uuid.UUID, tuple[User, FormalPrincipal]] = {}
    for scope_no, value in enumerate(draft.scopes, start=1):
        owner = _require_asset_owner(db, value.owner_org_id, region.id)
        location = _require_location(db, value.location_id, region.id)
        if draft.task_type == "termination":
            if location.location_type != "personal" or value.scope_mode != "location_all":
                _fail(
                    "termination_scope_invalid",
                    "invalid_request",
                    "离职盘点必须覆盖完整个人仓库位",
                )
        if draft.task_type == "full" and value.scope_mode != "location_all":
            _fail(
                "full_scope_filter_forbidden",
                "invalid_request",
                "全盘只能使用整库位范围",
            )
        if value.material_id is not None:
            _require_active_material(db, value.material_id)
        custodian = _require_location_custody(db, location, now=now)
        cached = assignee_cache.get(value.assignee_person_id)
        if cached is None:
            cached = _resolve_active_user_for_person(
                db,
                value.assignee_person_id,
                now=now,
            )
            assignee_cache[value.assignee_person_id] = cached
        assignee_user, assignee = cached
        _authorize_assignee_count(
            db,
            assignee,
            location=location,
            custodian_person_id=custodian,
            owner_org_id=owner.id,
        )
        _authorize_manager_scope_dimensions(
            db,
            actor,
            region_org_id=region.id,
            owner_org_id=owner.id,
            location_owner_org_id=location.owner_org_id,
        )
        plans.append(
            _new_scope_plan(
                scope_no=scope_no,
                value=value,
                assignee_user_id=assignee_user.id,
                custodian_person_id=custodian,
            )
        )
    if draft.task_type == "termination":
        _require_complete_termination_scope(db, region.id, tuple(plans))
    _require_non_overlapping_plans(tuple(plans))
    return tuple(plans)


def _require_complete_termination_scope(
    db: Session,
    region_org_id: uuid.UUID,
    plans: tuple[_ScopePlan, ...],
) -> None:
    custodians = {plan.custodian_person_id for plan in plans}
    if None in custodians or len(custodians) != 1:
        _fail(
            "termination_person_not_unique",
            "invalid_request",
            "一张离职盘点必须且只能覆盖一名人员的全部个人仓",
        )
    custodian_person_id = next(iter(custodians))
    locations = tuple(
        db.scalars(
            select(StockLocation)
            .where(
                StockLocation.location_type == "personal",
                StockLocation.custodian_person_id == custodian_person_id,
                StockLocation.status == "active",
            )
            .order_by(StockLocation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    expected_location_ids = {
        location.id
        for location in locations
        if _organization_descends_from(
            db,
            location.owner_org_id,
            region_org_id,
        )
    }
    selected_location_ids = {plan.location_id for plan in plans}
    if not expected_location_ids or selected_location_ids != expected_location_ids:
        _fail(
            "termination_scope_incomplete",
            "precondition_failed",
            "离职盘点必须覆盖该人员在本区域的全部启用个人仓",
        )


def _derive_personal_scope_plan(
    db: Session,
    actor: FormalPrincipal,
    *,
    freeze_mode: str,
    now: datetime,
) -> tuple[_ScopePlan, uuid.UUID]:
    person = db.get(Person, actor.person_id)
    if person is None or person.employment_status != "active":
        _fail("personal_stocktake_person_invalid", "forbidden", "当前人员不能创建个人盘点")
    region = _find_region_ancestor(db, person.organization_id)
    locations = tuple(
        db.scalars(
            select(StockLocation)
            .where(
                StockLocation.location_type == "personal",
                StockLocation.custodian_person_id == actor.person_id,
                StockLocation.status == "active",
            )
            .order_by(StockLocation.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(locations) != 1:
        _fail(
            "personal_stocktake_location_not_unique",
            "precondition_failed",
            "当前人员必须且只能绑定一个启用的个人仓",
        )
    location = locations[0]
    _require_location(db, location.id, region.id)
    custodian = _require_location_custody(db, location, now=now)
    if custodian != actor.person_id:
        _fail(
            "personal_stocktake_custody_mismatch",
            "precondition_failed",
            "个人仓当前保管责任与登录人员不一致",
        )
    owner = _require_asset_owner(db, location.owner_org_id, region.id)
    selection = StocktakeScopeSelectionIn(
        owner_org_id=owner.id,
        location_id=location.id,
        assignee_person_id=actor.person_id,
        scope_mode="location_all",
        freeze_mode=freeze_mode,
    )
    plan = _new_scope_plan(
        scope_no=1,
        value=selection,
        assignee_user_id=actor.user_id,
        custodian_person_id=actor.person_id,
    )
    return plan, region.id


def _load_and_validate_scope_plans(
    db: Session,
    task: FormalStocktakeTask,
    scopes: tuple[FormalStocktakeScope, ...],
    *,
    now: datetime,
) -> tuple[_ScopePlan, ...]:
    create_event = db.scalar(
        select(StateTransitionEvent)
        .where(
            StateTransitionEvent.aggregate_type == STOCKTAKE_AGGREGATE,
            StateTransitionEvent.aggregate_id == str(task.id),
            StateTransitionEvent.from_status.is_(None),
            StateTransitionEvent.to_status == "draft",
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    metadata = create_event.metadata_jsonb if create_event is not None else None
    if (
        not isinstance(metadata, dict)
        or metadata.get("schema") != _CREATE_COMMAND_SCHEMA
        or not isinstance(metadata.get("scope_plan"), list)
    ):
        _fail(
            "stocktake_create_evidence_invalid",
            "service_unavailable",
            "盘点草稿缺少可验证的创建证据",
        )
    plan_documents = metadata["scope_plan"]
    if len(plan_documents) != len(scopes):
        _fail(
            "stocktake_scope_plan_mismatch",
            "service_unavailable",
            "盘点草稿范围与创建证据不一致",
        )
    plans: list[_ScopePlan] = []
    for scope, document in zip(scopes, plan_documents, strict=True):
        freeze_mode = document.get("freeze_mode") if isinstance(document, dict) else None
        if freeze_mode not in {"hard", "cutoff_replay"}:
            _fail(
                "stocktake_freeze_mode_invalid",
                "service_unavailable",
                "盘点冻结方式证据无效",
            )
        if (
            not isinstance(document, dict)
            or document.get("scope_id") != str(scope.id)
            or document.get("scope_sha256") != scope.scope_sha256
        ):
            _fail(
                "stocktake_scope_plan_mismatch",
                "service_unavailable",
                "盘点草稿范围与创建证据不一致",
            )
        location = _require_location(db, scope.location_id, task.region_org_id)
        _require_asset_owner(db, scope.owner_org_id, task.region_org_id)
        custodian = _require_location_custody(db, location, now=now)
        if custodian != scope.custodian_person_id_snapshot:
            _fail(
                "stocktake_custody_changed",
                "conflict",
                "盘点库位保管责任已变化，请取消草稿后重新创建",
            )
        user = db.get(User, scope.assignee_user_id)
        if user is None or user.person_id is None:
            _fail("stocktake_assignee_invalid", "precondition_failed", "盘点执行账号无效")
        resolved_user, assignee = _resolve_active_user_for_person(
            db,
            user.person_id,
            now=now,
        )
        if resolved_user.id != scope.assignee_user_id:
            _fail(
                "stocktake_assignee_changed",
                "conflict",
                "盘点执行人员账号绑定已变化，请重新创建任务",
            )
        _authorize_assignee_count(
            db,
            assignee,
            location=location,
            custodian_person_id=custodian,
            owner_org_id=scope.owner_org_id,
        )
        if scope.material_id is not None:
            _require_active_material(db, scope.material_id)
        plan = _ScopePlan(
            scope_id=scope.id,
            scope_no=scope.scope_no,
            scope_mode=scope.scope_mode,
            owner_org_id=scope.owner_org_id,
            location_id=scope.location_id,
            assignee_user_id=scope.assignee_user_id,
            custodian_person_id=scope.custodian_person_id_snapshot,
            material_id=scope.material_id,
            condition_code=scope.condition_code,
            availability_bucket=scope.availability_bucket,
            freeze_mode=freeze_mode,
            scope_key=scope.scope_key,
            scope_sha256=scope.scope_sha256,
        )
        if _scope_hash(plan) != scope.scope_sha256 or _scope_key(plan) != scope.scope_key:
            _fail(
                "stocktake_scope_integrity_invalid",
                "service_unavailable",
                "盘点范围完整性校验失败",
            )
        if task.task_type == "full" and scope.scope_mode != "location_all":
            _fail("full_scope_filter_forbidden", "service_unavailable", "全盘范围证据无效")
        if task.task_type == "termination" and (
            location.location_type != "personal" or scope.scope_mode != "location_all"
        ):
            _fail("termination_scope_invalid", "service_unavailable", "离职盘点范围证据无效")
        plans.append(plan)
    checked = tuple(plans)
    if task.task_type == "termination":
        _require_complete_termination_scope(db, task.region_org_id, checked)
    _require_non_overlapping_plans(checked)
    if _scope_manifest_sha256(task.task_type, task.region_org_id, checked) != task.scope_manifest_sha256:
        _fail(
            "stocktake_scope_manifest_mismatch",
            "service_unavailable",
            "盘点范围清单 hash 无法重算",
        )
    return checked


def _build_snapshots(
    db: Session,
    plans: tuple[_ScopePlan, ...],
    *,
    cutoff_ledger_cursor: int,
    cutoff_at: datetime,
) -> tuple[_Snapshot, ...]:
    selected: list[tuple[_ScopePlan, StockAccount]] = []
    for plan in plans:
        location = db.get(StockLocation, plan.location_id)
        if location is None:
            _fail("stocktake_location_lost", "conflict", "盘点库位在启动期间失效")
        all_accounts = tuple(
            db.scalars(
                select(StockAccount)
                .where(
                    StockAccount.owner_org_id == plan.owner_org_id,
                    StockAccount.location_id == plan.location_id,
                )
                .order_by(StockAccount.id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        if location.location_type == "personal" and any(
            row.custodian_person_id != plan.custodian_person_id
            for row in all_accounts
        ):
            _fail(
                "personal_account_custodian_mismatch",
                "precondition_failed",
                "个人仓库存账户保管人与当前责任人不一致",
            )
        for account in all_accounts:
            if _account_matches_plan(account, plan):
                selected.append((plan, account))

    account_ids = tuple(row.id for _, row in selected)
    balances: dict[uuid.UUID, StockBalance] = {}
    if account_ids:
        balance_rows = tuple(
            db.scalars(
                select(StockBalance)
                .where(StockBalance.stock_account_id.in_(account_ids))
                .order_by(StockBalance.stock_account_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        balances = {row.stock_account_id: row for row in balance_rows}
        if any(
            row.quantity is None
            or not row.quantity.is_finite()
            or row.quantity < _ZERO
            or row.ledger_cursor < 0
            or row.ledger_cursor > cutoff_ledger_cursor
            for row in balance_rows
        ):
            _fail(
                "stocktake_balance_projection_invalid",
                "service_unavailable",
                "库存余额投影与截止游标不一致",
            )

    material_ids = tuple(
        sorted(
            {row.material_id for _, row in selected}.union(
                plan.material_id
                for plan in plans
                if plan.material_id is not None
            ),
            key=str,
        )
    )
    materials = {
        row.id: row
        for row in db.scalars(
            select(FormalMaterial)
            .where(FormalMaterial.id.in_(material_ids))
            .order_by(FormalMaterial.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    } if material_ids else {}
    if len(materials) != len(material_ids) or any(
        row.status != "active" for row in materials.values()
    ):
        _fail("stocktake_material_invalid", "precondition_failed", "盘点账户包含停用或缺失物料")
    policies = {
        material_id: _require_inventory_policy(db, material_id, cutoff_at)
        for material_id in material_ids
    }
    lot_ids = tuple(
        sorted({row.lot_id for _, row in selected if row.lot_id is not None}, key=str)
    )
    lots = {
        row.id: row
        for row in db.scalars(
            select(InventoryLot)
            .where(InventoryLot.id.in_(lot_ids))
            .order_by(InventoryLot.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    } if lot_ids else {}
    if len(lots) != len(lot_ids):
        _fail("stocktake_lot_invalid", "precondition_failed", "盘点账户引用的批次不存在")

    positions: dict[uuid.UUID, list[SerialCurrentPosition]] = {
        account_id: [] for account_id in account_ids
    }
    if account_ids:
        position_rows = tuple(
            db.scalars(
                select(SerialCurrentPosition)
                .where(SerialCurrentPosition.stock_account_id.in_(account_ids))
                .order_by(SerialCurrentPosition.serial_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        for position in position_rows:
            if position.stock_account_id is None:
                _fail("stocktake_serial_position_invalid", "service_unavailable", "SN 当前归属无效")
            positions[position.stock_account_id].append(position)
    serial_ids = tuple(
        sorted(
            {row.serial_id for rows in positions.values() for row in rows},
            key=str,
        )
    )
    serials = {
        row.id: row
        for row in db.scalars(
            select(InventorySerial)
            .where(InventorySerial.id.in_(serial_ids))
            .order_by(InventorySerial.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    } if serial_ids else {}
    if len(serials) != len(serial_ids):
        _fail("stocktake_serial_master_invalid", "service_unavailable", "SN 主数据不完整")
    movement_ids = tuple(
        sorted(
            {row.last_movement_id for rows in positions.values() for row in rows},
            key=str,
        )
    )
    movements = {
        row.id: row
        for row in db.scalars(
            select(InventoryMovement)
            .where(InventoryMovement.id.in_(movement_ids))
            .order_by(InventoryMovement.id)
        ).all()
    } if movement_ids else {}
    transaction_ids = tuple(
        sorted({row.transaction_id for row in movements.values()}, key=str)
    )
    transactions = {
        row.id: row
        for row in db.scalars(
            select(InventoryTransaction)
            .where(InventoryTransaction.id.in_(transaction_ids))
            .order_by(InventoryTransaction.id)
        ).all()
    } if transaction_ids else {}
    if len(movements) != len(movement_ids) or len(transactions) != len(transaction_ids):
        _fail("stocktake_serial_ledger_invalid", "service_unavailable", "SN 流水归属证据不完整")

    prepared: list[_Snapshot] = []
    for plan, account in selected:
        policy = policies[account.material_id]
        _validate_account_lot(account, policy, lots)
        balance = balances.get(account.id)
        book_qty = balance.quantity if balance is not None else _ZERO
        if _decimal_scale(book_qty) > policy.quantity_scale or (
            not policy.allow_fraction and book_qty != book_qty.to_integral_value()
        ):
            _fail(
                "stocktake_quantity_policy_mismatch",
                "service_unavailable",
                "账面数量精度与截止时点物料策略不一致",
            )
        serial_documents: list[dict[str, object]] = []
        for position in positions.get(account.id, []):
            serial = serials[position.serial_id]
            movement = movements.get(position.last_movement_id)
            transaction = transactions.get(movement.transaction_id) if movement is not None else None
            if (
                movement is None
                or transaction is None
                or movement.to_account_id != account.id
                or transaction.ledger_cursor > cutoff_ledger_cursor
                or serial.lifecycle_status != "active"
                or serial.material_id != account.material_id
                or serial.lot_id != account.lot_id
            ):
                _fail(
                    "stocktake_serial_position_invalid",
                    "service_unavailable",
                    "SN 当前归属与截止流水、物料或批次不一致",
                )
            serial_documents.append(
                {
                    "last_movement_id": str(movement.id),
                    "ledger_cursor": transaction.ledger_cursor,
                    "lot_id": str(serial.lot_id) if serial.lot_id is not None else None,
                    "qr_code": serial.qr_code,
                    "serial_id": str(serial.id),
                    "serial_no": serial.serial_no,
                }
            )
        serial_documents.sort(key=lambda row: str(row["serial_id"]))
        serial_tracking = policy.tracking_mode in {"serial", "lot_and_serial"}
        if not serial_tracking and serial_documents:
            _fail("stocktake_serial_forbidden", "service_unavailable", "非 SN 物料存在 SN 归属")
        if serial_tracking and (
            book_qty != book_qty.to_integral_value()
            or len(serial_documents) != int(book_qty)
        ):
            _fail(
                "stocktake_serial_quantity_mismatch",
                "service_unavailable",
                "SN 数量必须与截止账面数量一致",
            )
        account_hash = _canonical_sha256(_account_dimension_document(account))
        serial_hash = _canonical_sha256(
            {
                "schema": "cloud_oam.stocktake.serial_snapshot.v1",
                "serials": serial_documents,
                "stock_account_id": str(account.id),
            }
        )
        prepared.append(
            _Snapshot(
                scope=plan,
                account=account,
                book_qty=book_qty,
                account_dimension_sha256=account_hash,
                serials=tuple(serial_documents),
                serial_sha256=serial_hash,
            )
        )
    prepared.sort(key=lambda row: (row.scope.scope_no, str(row.account.id)))
    return tuple(prepared)


def _require_startable_draft(
    db: Session,
    task: FormalStocktakeTask,
    scopes: tuple[FormalStocktakeScope, ...],
    expected_version: int,
    now: datetime,
) -> None:
    require_stocktake_task_type(task.task_type)
    if task.status != "draft" or task.version != expected_version:
        _fail(
            "stocktake_version_conflict",
            "conflict",
            "盘点任务状态或版本已变化，请重新读取",
        )
    if task.cutoff_ledger_cursor is not None or task.cutoff_at is not None:
        _fail("stocktake_draft_cutoff_exists", "service_unavailable", "盘点草稿已存在截止游标")
    if task.current_round_no != 0 or any(
        value is not None for value in (task.issued_at, task.frozen_at)
    ):
        _fail("stocktake_draft_state_invalid", "service_unavailable", "盘点草稿状态证据无效")
    if task.deadline is not None and _as_utc(task.deadline) <= now:
        _fail("stocktake_deadline_elapsed", "precondition_failed", "盘点截止时间已到，禁止启动")
    if not scopes or tuple(row.scope_no for row in scopes) != tuple(range(1, len(scopes) + 1)):
        _fail("stocktake_scope_sequence_invalid", "service_unavailable", "盘点范围序号不连续")
    if db.scalar(
        select(InventoryFreeze.id).where(InventoryFreeze.task_id == task.id).limit(1)
    ) is not None:
        _fail("stocktake_draft_freeze_exists", "service_unavailable", "盘点草稿意外存在冻结记录")
    if db.scalar(
        select(StocktakeSnapshotLine.id)
        .where(StocktakeSnapshotLine.task_id == task.id)
        .limit(1)
    ) is not None:
        _fail("stocktake_draft_snapshot_exists", "service_unavailable", "盘点草稿意外存在快照")
    if db.scalar(
        select(StocktakeRound.id).where(StocktakeRound.task_id == task.id).limit(1)
    ) is not None:
        _fail("stocktake_draft_round_exists", "service_unavailable", "盘点草稿意外存在轮次")


def _require_no_active_overlapping_freeze(
    db: Session,
    plans: tuple[_ScopePlan, ...],
) -> None:
    rows = db.execute(
        select(InventoryFreeze, FormalStocktakeScope)
        .join(
            FormalStocktakeScope,
            FormalStocktakeScope.id == InventoryFreeze.stocktake_scope_id,
        )
        .where(InventoryFreeze.status == "active")
        .order_by(InventoryFreeze.id)
        .with_for_update(of=InventoryFreeze)
        .execution_options(populate_existing=True)
    ).all()
    for _freeze, scope in rows:
        existing = _ScopePlan(
            scope_id=scope.id,
            scope_no=scope.scope_no,
            scope_mode=scope.scope_mode,
            owner_org_id=scope.owner_org_id,
            location_id=scope.location_id,
            assignee_user_id=scope.assignee_user_id,
            custodian_person_id=scope.custodian_person_id_snapshot,
            material_id=scope.material_id,
            condition_code=scope.condition_code,
            availability_bucket=scope.availability_bucket,
            freeze_mode="hard",
            scope_key=scope.scope_key,
            scope_sha256=scope.scope_sha256,
        )
        if any(_plans_overlap(candidate, existing) for candidate in plans):
            _fail(
                "stocktake_scope_already_frozen",
                "conflict",
                "一个或多个盘点范围已存在活动冻结",
            )


def _authorize_task_start(
    db: Session,
    actor: FormalPrincipal,
    task: FormalStocktakeTask,
) -> None:
    if task.task_type == "personal":
        if task.created_by_user_id != actor.user_id:
            _fail("personal_stocktake_owner_forbidden", "forbidden", "个人自盘只能由本人启动")
        _authorize_self_count(db, actor)
        return
    _authorize_manager(db, actor, task.region_org_id)


def _authorize_manager(
    db: Session,
    actor: FormalPrincipal,
    region_org_id: uuid.UUID,
) -> ScopeGrant:
    for grant in _manager_grants(actor, region_org_id):
        if _grant_allows(
            db,
            actor,
            grant,
            "stocktake",
            "manage",
            target_scope_type="organization",
            target_scope_id=str(region_org_id),
        ):
            return grant
    _fail(
        "stocktake_manager_forbidden",
        "forbidden",
        "只有全国总部管理员或本区域负责人可以管理该盘点任务",
    )


def _authorize_manager_scope_dimensions(
    db: Session,
    actor: FormalPrincipal,
    *,
    region_org_id: uuid.UUID,
    owner_org_id: uuid.UUID,
    location_owner_org_id: uuid.UUID,
) -> None:
    targets = tuple(dict.fromkeys((region_org_id, owner_org_id, location_owner_org_id)))
    for grant in _manager_grants(actor, region_org_id):
        if all(
            _grant_allows(
                db,
                actor,
                grant,
                "stocktake",
                "manage",
                target_scope_type="organization",
                target_scope_id=str(target),
            )
            for target in targets
        ):
            return
    _fail(
        "stocktake_scope_forbidden",
        "forbidden",
        "当前授权未覆盖盘点区域、资产组织和库位组织",
    )


def _authorize_self_count(db: Session, actor: FormalPrincipal) -> ScopeGrant:
    candidates = sorted(
        (
            grant
            for grant in actor.assignments
            if grant.role_code == "technician"
            and grant.scope_type == "person"
            and _same_uuid(grant.scope_id, actor.person_id)
        ),
        key=lambda row: str(row.assignment_id),
    )
    for grant in candidates:
        if _grant_allows(
            db,
            actor,
            grant,
            "stocktake",
            "count",
            target_scope_type="person",
            target_scope_id=str(actor.person_id),
        ):
            return grant
    _fail("personal_stocktake_count_forbidden", "forbidden", "当前人员没有本人盘点权限")


def _authorize_assignee_count(
    db: Session,
    assignee: FormalPrincipal,
    *,
    location: StockLocation,
    custodian_person_id: uuid.UUID | None,
    owner_org_id: uuid.UUID,
) -> None:
    if location.location_type == "personal":
        if custodian_person_id is None:
            _fail("personal_custody_invalid", "precondition_failed", "个人仓保管责任无效")
        target_type = "person"
        target_id = str(custodian_person_id)
    else:
        target_type = "organization"
        target_id = str(owner_org_id)
    try:
        allowed = assignee.allows(
            db,
            "stocktake",
            "count",
            target_scope_type=target_type,
            target_scope_id=target_id,
        )
    except FormalAccessError:
        allowed = False
    if not allowed:
        _fail("stocktake_assignee_forbidden", "forbidden", "盘点执行人员没有该范围盘点权限")


def _manager_grants(
    actor: FormalPrincipal,
    region_org_id: uuid.UUID,
) -> tuple[ScopeGrant, ...]:
    values = [
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
            and _same_uuid(grant.scope_id, region_org_id)
        )
    ]
    values.sort(key=lambda row: (row.role_code == "admin", str(row.assignment_id)))
    return tuple(values)


def _grant_allows(
    db: Session,
    actor: FormalPrincipal,
    grant: ScopeGrant,
    resource: str,
    action: str,
    *,
    target_scope_type: str,
    target_scope_id: str,
) -> bool:
    selected = replace(
        actor,
        assignments=(grant,),
        entitlements=tuple(
            row for row in actor.entitlements if row.assignment_id == grant.assignment_id
        ),
    )
    try:
        return actor.allows(
            db,
            resource,
            action,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
        ) and selected.allows(
            db,
            resource,
            action,
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
        )
    except FormalAccessError:
        return False


def _resolve_active_user_for_person(
    db: Session,
    person_id: uuid.UUID,
    *,
    now: datetime,
) -> tuple[User, FormalPrincipal]:
    users = tuple(
        db.scalars(
            select(User)
            .where(
                User.person_id == person_id,
                User.account_status == "active",
                User.is_active.is_(True),
            )
            .order_by(User.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(users) != 1:
        _fail(
            "stocktake_assignee_not_unique",
            "precondition_failed",
            "盘点执行人员必须唯一映射到一个启用账号",
        )
    try:
        principal = load_formal_principal(db, users[0].id, now=now)
    except FormalAccessError:
        _fail(
            "stocktake_assignee_principal_invalid",
            "precondition_failed",
            "盘点执行人员没有当前有效的正式身份与授权",
        )
    if (
        principal.person_id != person_id
        or principal.account_status != "active"
        or principal.employment_status != "active"
        or principal.access_mode != "active"
    ):
        _fail("stocktake_assignee_inactive", "precondition_failed", "盘点执行人员不是启用状态")
    return users[0], principal


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail("formal_principal_required", "forbidden", "盘点操作必须使用正式权限主体")
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
        or not isinstance(actor.authorization_version, int)
        or actor.authorization_version <= 0
    ):
        _fail("stocktake_actor_inactive", "forbidden", "当前账号或人员状态不允许盘点操作")
    return actor


def _require_current_actor(
    db: Session,
    supplied: FormalPrincipal,
    *,
    now: datetime,
) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError:
        _fail("stocktake_actor_not_current", "forbidden", "正式权限上下文已失效")
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail(
            "stocktake_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取后再操作",
        )
    if (
        current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail("stocktake_actor_inactive", "forbidden", "当前账号或人员状态不允许盘点操作")
    return current


def _require_region(db: Session, region_org_id: uuid.UUID) -> Organization:
    region = db.scalar(
        select(Organization)
        .where(Organization.id == region_org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if region is None or region.status != "active" or region.org_type != "region_company":
        _fail("stocktake_region_invalid", "precondition_failed", "盘点区域必须是启用的区域公司")
    return region


def _require_asset_owner(
    db: Session,
    owner_org_id: uuid.UUID,
    region_org_id: uuid.UUID,
) -> Organization:
    owner = db.scalar(
        select(Organization)
        .where(Organization.id == owner_org_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if (
        owner is None
        or owner.status != "active"
        or owner.org_type != "region_company"
        or not _organization_descends_from(db, owner.id, region_org_id)
    ):
        _fail("stocktake_owner_invalid", "precondition_failed", "资产所有组织不在盘点区域内")
    return owner


def _require_location(
    db: Session,
    location_id: uuid.UUID,
    region_org_id: uuid.UUID,
) -> StockLocation:
    location = db.scalar(
        select(StockLocation)
        .where(StockLocation.id == location_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if location is None:
        _fail("stocktake_location_not_found", "not_found", "盘点库位不存在")
    if location.status != "active" or location.location_type not in {"region", "personal"}:
        _fail("stocktake_location_invalid", "precondition_failed", "盘点仅接受启用的区域仓或个人仓")
    current: StockLocation | None = location
    seen: set[uuid.UUID] = set()
    while current is not None:
        if current.id in seen:
            _fail("stocktake_location_tree_cycle", "service_unavailable", "库存位置树存在循环")
        seen.add(current.id)
        if current.status != "active" or not _organization_descends_from(
            db, current.owner_org_id, region_org_id
        ):
            _fail("stocktake_location_outside_region", "forbidden", "盘点库位不在任务区域内")
        current = db.get(StockLocation, current.parent_id) if current.parent_id else None
    return location


def _require_location_custody(
    db: Session,
    location: StockLocation,
    *,
    now: datetime,
) -> uuid.UUID | None:
    rows = tuple(
        db.scalars(
            select(CustodyAssignment)
            .where(
                CustodyAssignment.location_id == location.id,
                CustodyAssignment.valid_from <= now,
                or_(
                    CustodyAssignment.valid_to.is_(None),
                    CustodyAssignment.valid_to > now,
                ),
            )
            .order_by(CustodyAssignment.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(rows) > 1:
        _fail("stocktake_custody_ambiguous", "service_unavailable", "库位当前保管责任不唯一")
    custody = rows[0].custodian_person_id if rows else None
    if location.location_type == "personal":
        if (
            custody is None
            or location.custodian_person_id is None
            or custody != location.custodian_person_id
        ):
            _fail("personal_custody_invalid", "precondition_failed", "个人仓当前保管责任无效")
    elif location.custodian_person_id is not None and custody != location.custodian_person_id:
        _fail("regional_custody_invalid", "precondition_failed", "区域仓保管责任字段不一致")
    return custody


def _find_region_ancestor(db: Session, organization_id: uuid.UUID) -> Organization:
    current_id: uuid.UUID | None = organization_id
    seen: set[uuid.UUID] = set()
    while current_id is not None:
        if current_id in seen:
            _fail("stocktake_organization_tree_cycle", "service_unavailable", "组织树存在循环")
        seen.add(current_id)
        row = db.scalar(
            select(Organization)
            .where(Organization.id == current_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
        if row is None or row.status != "active":
            _fail("stocktake_person_region_invalid", "precondition_failed", "当前人员区域归属无效")
        if row.org_type == "region_company":
            return row
        current_id = row.parent_id
    _fail("stocktake_person_region_missing", "precondition_failed", "当前人员未归属区域公司")


def _organization_descends_from(
    db: Session,
    organization_id: uuid.UUID,
    ancestor_id: uuid.UUID,
) -> bool:
    current_id: uuid.UUID | None = organization_id
    seen: set[uuid.UUID] = set()
    while current_id is not None:
        if current_id in seen:
            _fail("stocktake_organization_tree_cycle", "service_unavailable", "组织树存在循环")
        seen.add(current_id)
        row = db.scalar(
            select(Organization)
            .where(Organization.id == current_id)
            .execution_options(populate_existing=True)
        )
        if row is None or row.status != "active":
            return False
        if row.id == ancestor_id:
            return True
        current_id = row.parent_id
    return False


def _require_active_material(db: Session, material_id: uuid.UUID) -> FormalMaterial:
    row = db.scalar(
        select(FormalMaterial)
        .where(FormalMaterial.id == material_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if row is None or row.status != "active":
        _fail("stocktake_material_invalid", "precondition_failed", "筛选物料不存在或已停用")
    return row


def _require_inventory_policy(
    db: Session,
    material_id: uuid.UUID,
    cutoff_at: datetime,
) -> MaterialInventoryPolicy:
    rows = tuple(
        db.scalars(
            select(MaterialInventoryPolicy)
            .where(
                MaterialInventoryPolicy.material_id == material_id,
                MaterialInventoryPolicy.effective_from <= cutoff_at,
                or_(
                    MaterialInventoryPolicy.effective_to.is_(None),
                    MaterialInventoryPolicy.effective_to > cutoff_at,
                ),
            )
            .order_by(MaterialInventoryPolicy.effective_from, MaterialInventoryPolicy.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(rows) != 1 or rows[0].tracking_mode not in {
        "none", "lot", "serial", "lot_and_serial"
    }:
        _fail(
            "stocktake_inventory_policy_invalid",
            "precondition_failed",
            "截止时点物料必须且只能命中一条有效库存策略",
        )
    return rows[0]


def _validate_account_lot(
    account: StockAccount,
    policy: MaterialInventoryPolicy,
    lots: Mapping[uuid.UUID, InventoryLot],
) -> None:
    if policy.tracking_mode in {"none", "serial"} and account.lot_id is not None:
        _fail("stocktake_account_lot_forbidden", "precondition_failed", "非批次物料账户不得绑定批次")
    if policy.tracking_mode in {"lot", "lot_and_serial"}:
        lot = lots.get(account.lot_id) if account.lot_id is not None else None
        if lot is None or lot.material_id != account.material_id:
            _fail("stocktake_account_lot_required", "precondition_failed", "批次物料账户必须绑定同物料批次")


def _new_scope_plan(
    *,
    scope_no: int,
    value: StocktakeScopeSelectionIn,
    assignee_user_id: str,
    custodian_person_id: uuid.UUID | None,
) -> _ScopePlan:
    provisional = _ScopePlan(
        scope_id=uuid.uuid4(),
        scope_no=scope_no,
        scope_mode=value.scope_mode,
        owner_org_id=value.owner_org_id,
        location_id=value.location_id,
        assignee_user_id=assignee_user_id,
        custodian_person_id=custodian_person_id,
        material_id=value.material_id,
        condition_code=value.condition_code,
        availability_bucket=value.availability_bucket,
        freeze_mode=value.freeze_mode,
        scope_key="",
        scope_sha256="",
    )
    keyed = replace(provisional, scope_key=_scope_key(provisional))
    return replace(keyed, scope_sha256=_scope_hash(keyed))


def _scope_key(plan: _ScopePlan) -> str:
    return ":".join(
        (
            "stocktake-v1",
            str(plan.owner_org_id),
            str(plan.location_id),
            str(plan.material_id) if plan.material_id is not None else "*",
            plan.condition_code or "*",
            plan.availability_bucket or "*",
        )
    )


def _scope_hash(plan: _ScopePlan) -> str:
    return _canonical_sha256(
        {
            "assignee_user_id": plan.assignee_user_id,
            "availability_bucket": plan.availability_bucket,
            "condition_code": plan.condition_code,
            "custodian_person_id": (
                str(plan.custodian_person_id)
                if plan.custodian_person_id is not None
                else None
            ),
            "freeze_mode": plan.freeze_mode,
            "location_id": str(plan.location_id),
            "material_id": str(plan.material_id) if plan.material_id is not None else None,
            "owner_org_id": str(plan.owner_org_id),
            "schema": "cloud_oam.stocktake.scope.v1",
            "scope_key": plan.scope_key,
            "scope_mode": plan.scope_mode,
            "scope_no": plan.scope_no,
        }
    )


def _scope_manifest_sha256(
    task_type: str,
    region_org_id: uuid.UUID,
    plans: Sequence[_ScopePlan],
) -> str:
    return _canonical_sha256(
        {
            "region_org_id": str(region_org_id),
            "schema": "cloud_oam.stocktake.scope_manifest.v1",
            "scopes": [
                {
                    "scope_id": str(row.scope_id),
                    "scope_no": row.scope_no,
                    "scope_sha256": row.scope_sha256,
                }
                for row in plans
            ],
            "task_type": task_type,
        }
    )


def _snapshot_manifest_sha256(
    *,
    task: FormalStocktakeTask,
    cutoff_cursor: int,
    plans: Sequence[_ScopePlan],
    snapshots: Sequence[_Snapshot],
) -> str:
    return _canonical_sha256(
        {
            "cutoff_ledger_cursor": cutoff_cursor,
            "lines": [
                {
                    "account_dimension_sha256": row.account_dimension_sha256,
                    "book_qty": _canonical_decimal(row.book_qty),
                    "scope_id": str(row.scope.scope_id),
                    "serial_snapshot_sha256": row.serial_sha256,
                    "stock_account_id": str(row.account.id),
                }
                for row in snapshots
            ],
            "schema": _SNAPSHOT_SCHEMA,
            "scope_sha256": [row.scope_sha256 for row in plans],
            "task_id": str(task.id),
        }
    )


def _account_dimension_document(account: StockAccount) -> dict[str, object]:
    return {
        "availability_bucket": account.availability_bucket,
        "condition_code": account.condition_code,
        "custodian_person_id": (
            str(account.custodian_person_id)
            if account.custodian_person_id is not None
            else None
        ),
        "location_id": str(account.location_id),
        "lot_id": str(account.lot_id) if account.lot_id is not None else None,
        "material_id": str(account.material_id),
        "owner_org_id": str(account.owner_org_id),
        "schema": "cloud_oam.stocktake.account_dimension.v1",
        "stock_account_id": str(account.id),
    }


def _account_matches_plan(account: StockAccount, plan: _ScopePlan) -> bool:
    return (
        (plan.material_id is None or account.material_id == plan.material_id)
        and (plan.condition_code is None or account.condition_code == plan.condition_code)
        and (
            plan.availability_bucket is None
            or account.availability_bucket == plan.availability_bucket
        )
    )


def _require_non_overlapping_plans(plans: tuple[_ScopePlan, ...]) -> None:
    for index, left in enumerate(plans):
        for right in plans[index + 1 :]:
            if _plans_overlap(left, right):
                _fail(
                    "stocktake_scope_overlap",
                    "invalid_request",
                    "同一任务的盘点范围不能相互重叠",
                )


def _plans_overlap(left: _ScopePlan, right: _ScopePlan) -> bool:
    if (
        left.owner_org_id != right.owner_org_id
        or left.location_id != right.location_id
    ):
        return False
    return all(
        a is None or b is None or a == b
        for a, b in (
            (left.material_id, right.material_id),
            (left.condition_code, right.condition_code),
            (left.availability_bucket, right.availability_bucket),
        )
    )


def _load_create_replay(
    db: Session,
    *,
    idempotency_key: str,
    request_hash: str,
    expected_task_id: uuid.UUID,
) -> StocktakeTaskDraftResult | None:
    event = db.scalar(
        select(StateTransitionEvent)
        .where(StateTransitionEvent.idempotency_key == idempotency_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event is None:
        return None
    metadata = event.metadata_jsonb
    if (
        event.aggregate_type != STOCKTAKE_AGGREGATE
        or event.aggregate_id != str(expected_task_id)
        or not isinstance(metadata, dict)
        or metadata.get("schema") != _CREATE_COMMAND_SCHEMA
    ):
        _fail("stocktake_idempotency_record_invalid", "service_unavailable", "盘点幂等证据无效")
    if not hmac.compare_digest(str(metadata.get("request_hash", "")), request_hash):
        _fail("stocktake_idempotency_conflict", "conflict", "幂等键已用于不同的盘点命令")
    return _parse_draft_result(metadata.get("result"), expected_task_id)


def _load_start_replay(
    db: Session,
    *,
    idempotency_key: str,
    request_hash: str,
    expected_task_id: uuid.UUID,
) -> StocktakeTaskStartResult | None:
    event = db.scalar(
        select(StateTransitionEvent)
        .where(StateTransitionEvent.idempotency_key == idempotency_key)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if event is None:
        return None
    metadata = event.metadata_jsonb
    if (
        event.aggregate_type != STOCKTAKE_AGGREGATE
        or event.aggregate_id != str(expected_task_id)
        or not isinstance(metadata, dict)
        or metadata.get("schema") != _CREATE_COMMAND_SCHEMA
    ):
        _fail("stocktake_idempotency_record_invalid", "service_unavailable", "盘点幂等证据无效")
    if not hmac.compare_digest(str(metadata.get("request_hash", "")), request_hash):
        _fail("stocktake_idempotency_conflict", "conflict", "幂等键已用于不同的盘点命令")
    return _parse_start_result(metadata.get("result"), expected_task_id)


def _draft_result_document(result: StocktakeTaskDraftResult) -> dict[str, object]:
    return {
        "scope_count": result.scope_count,
        "status": result.status,
        "task_id": str(result.task_id),
        "task_no": result.task_no,
        "task_type": result.task_type,
        "version": result.version,
    }


def _start_result_document(result: StocktakeTaskStartResult) -> dict[str, object]:
    return {
        "active_freeze_count": result.active_freeze_count,
        "cutoff_ledger_cursor": result.cutoff_ledger_cursor,
        "initial_round_id": str(result.initial_round_id),
        "scope_count": result.scope_count,
        "snapshot_line_count": result.snapshot_line_count,
        "status": result.status,
        "task_id": str(result.task_id),
        "task_type": result.task_type,
        "version": result.version,
    }


def _parse_draft_result(value: object, expected_task_id: uuid.UUID) -> StocktakeTaskDraftResult:
    try:
        if not isinstance(value, dict):
            raise ValueError
        result = StocktakeTaskDraftResult(
            task_id=uuid.UUID(str(value["task_id"])),
            task_no=str(value["task_no"]),
            task_type=str(value["task_type"]),
            status=str(value["status"]),
            version=int(value["version"]),
            scope_count=int(value["scope_count"]),
        )
    except (KeyError, TypeError, ValueError):
        _fail("stocktake_idempotency_record_invalid", "service_unavailable", "盘点幂等结果无效")
    if (
        result.task_id != expected_task_id
        or result.status != "draft"
        or result.version != 0
        or result.scope_count <= 0
        or result.task_type not in {"full", "sample", "ad_hoc", "personal", "termination"}
    ):
        _fail("stocktake_idempotency_record_invalid", "service_unavailable", "盘点幂等结果无效")
    return result


def _parse_start_result(value: object, expected_task_id: uuid.UUID) -> StocktakeTaskStartResult:
    try:
        if not isinstance(value, dict):
            raise ValueError
        result = StocktakeTaskStartResult(
            task_id=uuid.UUID(str(value["task_id"])),
            task_type=str(value["task_type"]),
            status=str(value["status"]),
            version=int(value["version"]),
            cutoff_ledger_cursor=int(value["cutoff_ledger_cursor"]),
            initial_round_id=uuid.UUID(str(value["initial_round_id"])),
            scope_count=int(value["scope_count"]),
            snapshot_line_count=int(value["snapshot_line_count"]),
            active_freeze_count=int(value["active_freeze_count"]),
        )
    except (KeyError, TypeError, ValueError):
        _fail("stocktake_idempotency_record_invalid", "service_unavailable", "盘点幂等结果无效")
    if (
        result.task_id != expected_task_id
        or result.status != "counting"
        or result.version <= 0
        or result.cutoff_ledger_cursor < 0
        or result.scope_count <= 0
        or result.active_freeze_count != result.scope_count
    ):
        _fail("stocktake_idempotency_record_invalid", "service_unavailable", "盘点幂等结果无效")
    return result


def _scope_plan_document(plan: _ScopePlan) -> dict[str, object]:
    return {
        "freeze_mode": plan.freeze_mode,
        "scope_id": str(plan.scope_id),
        "scope_no": plan.scope_no,
        "scope_sha256": plan.scope_sha256,
    }


def _validate_managed_draft(value: object) -> StocktakeTaskCreateIn:
    if not isinstance(value, StocktakeTaskCreateIn):
        _fail("stocktake_draft_invalid", "invalid_request", "盘点草稿类型无效")
    try:
        checked = StocktakeTaskCreateIn.model_validate(
            value.model_dump(),
            strict=True,
        )
    except ValidationError:
        _fail("stocktake_draft_invalid", "invalid_request", "盘点草稿字段无效")
    require_stocktake_task_type(checked.task_type)
    return checked


def _validate_personal_draft(value: object) -> PersonalStocktakeCreateIn:
    if not isinstance(value, PersonalStocktakeCreateIn):
        _fail("personal_stocktake_draft_invalid", "invalid_request", "个人盘点草稿类型无效")
    try:
        return PersonalStocktakeCreateIn.model_validate(value.model_dump(), strict=True)
    except ValidationError:
        _fail("personal_stocktake_draft_invalid", "invalid_request", "个人盘点草稿字段无效")


def _validate_start_command(value: object) -> StocktakeTaskStartIn:
    if not isinstance(value, StocktakeTaskStartIn):
        _fail("stocktake_start_invalid", "invalid_request", "盘点启动命令类型无效")
    try:
        return StocktakeTaskStartIn.model_validate(value.model_dump(), strict=True)
    except ValidationError:
        _fail("stocktake_start_invalid", "invalid_request", "盘点启动命令字段无效")


def _require_deadline(value: datetime | None, now: datetime) -> None:
    if value is not None and _as_utc(value) <= now:
        _fail("stocktake_deadline_invalid", "invalid_request", "盘点截止时间必须晚于当前时间")


def _require_uuid(name: str, value: object) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail("stocktake_uuid_invalid", "invalid_request", f"{name} 无效")
    return value


def _require_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not _PRINTABLE.fullmatch(value):
        _fail("stocktake_idempotency_key_invalid", "invalid_request", "Idempotency-Key 无效")
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    secret = value.encode("utf-8") if isinstance(value, str) else value
    if (
        not isinstance(secret, bytes)
        or len(secret) < 32
        or any(marker in secret.lower().decode("utf-8", errors="ignore") for marker in _PLACEHOLDERS)
    ):
        _fail(
            "stocktake_idempotency_hmac_unavailable",
            "service_unavailable",
            "盘点幂等 HMAC 配置不可用",
        )
    return secret


def _require_trace_request_id(value: object) -> str:
    if not isinstance(value, str) or value != value.strip() or not _SAFE_TRACE.fullmatch(value):
        _fail("stocktake_trace_id_invalid", "invalid_request", "请求追踪标识无效")
    return value


def _idempotency_hmac(
    secret: bytes,
    actor_user_id: str,
    method: str,
    path: str,
    raw_key: str,
) -> str:
    document = (
        "cloud_oam.stocktake.idempotency.v1\0"
        f"actor={actor_user_id}\0method={method}\0path={path}\0key={raw_key}"
    ).encode("utf-8")
    return hmac.new(secret, document, hashlib.sha256).hexdigest()


def _request_hmac(secret: bytes, document: Mapping[str, object]) -> str:
    return hmac.new(
        secret,
        b"cloud_oam.stocktake.request.v1\0" + _canonical_json_bytes(document),
        hashlib.sha256,
    ).hexdigest()


def _canonical_sha256(document: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_json_bytes(document)).hexdigest()


def _canonical_json_bytes(document: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError):
        _fail("stocktake_document_invalid", "invalid_request", "盘点命令无法规范化")


def _canonical_decimal(value: Decimal) -> str:
    return format(value.quantize(Decimal("0.001")), "f")


def _decimal_scale(value: Decimal) -> int:
    normalized = value.normalize()
    return max(0, -normalized.as_tuple().exponent)


def _create_event_key(key_hash: str) -> str:
    return f"stocktake:create:{key_hash}"


def _start_event_key(key_hash: str, state: str) -> str:
    return f"stocktake:start:{key_hash}:{state}"


def _request_reference(raw_value: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.stocktake.trace.v1\0{raw_value}".encode("utf-8")
    ).hexdigest()
    return f"stocktake-request-{digest}"


def _lock_coordinate(namespace: str, value: str) -> int:
    digest = hashlib.sha256(
        f"cloud_oam.inventory.lock.v1\0{namespace}\0{value}".encode("utf-8")
    ).digest()
    return int.from_bytes(digest[:8], byteorder="big", signed=True)


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
            _fail("stocktake_database_time_unavailable", "service_unavailable", "数据库时间不可用")
        return _as_utc(value)
    return datetime.now(timezone.utc)


def _same_uuid(left: str | uuid.UUID, right: str | uuid.UUID) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fail(code: str, category: str, message: str) -> None:
    raise StocktakeTaskError(code, category, message)


__all__ = [
    "StocktakeTaskDraftResult",
    "StocktakeTaskError",
    "StocktakeTaskStartResult",
    "create_personal_stocktake_draft",
    "create_stocktake_task_draft",
    "derive_stocktake_task_create_id",
    "start_stocktake_task",
]
