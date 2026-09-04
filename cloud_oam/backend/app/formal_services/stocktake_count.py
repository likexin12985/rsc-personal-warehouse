"""Formal V1.0 initial-count submission for non-opening stocktakes.

This service is deliberately independent from ``opening_stocktake_count``.
Normal stocktakes consume the cutoff snapshot created by ``stocktake_task``;
they do not inherit the opening/OAM-control-total or opening-establishment
semantics.

The caller owns the transaction.  This module flushes but never commits or
rolls back, never calls an external system, and never writes inventory
transactions, movements, balances, difference approvals, postings,
notifications or reconciliation facts.  One call seals exactly one assigned
scope.  When every scope is sealed it advances only the initial round and task
to ``submitted`` so a later, separately reviewed difference service can decide
whether review or recount is required.
"""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import hmac
import json
import re
from typing import Final, Mapping, Sequence
import uuid

from sqlalchemy import func, or_, select, text
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
from ..inventory_models import (
    FormalMaterial,
    InventoryLedgerHead,
    InventoryLot,
    InventorySerial,
    MaterialInventoryPolicy,
    QrCode,
    StockAccount,
    StockLocation,
)
from ..stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeCountSerial,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)
from .audit_chain import AuditChainError, append_audit_event, lock_audit_chain_head
from . import formal_files as formal_file_service
from .inventory_posting import INVENTORY_LEDGER_HEAD_ID, INVENTORY_STREAM_KEY
from .postgresql_lock_graph import (
    lock_inventory_reference_graph,
    lock_inventory_serial_graph,
    lock_opening_stocktake_start_reference,
)
from . import stocktake_task as task_service


_NON_OPENING_TYPES: Final[frozenset[str]] = frozenset(
    {"full", "sample", "ad_hoc", "personal", "termination"}
)
_COUNT_METHODS: Final[frozenset[str]] = frozenset({"scan", "manual"})
_CONDITIONS: Final[frozenset[str]] = frozenset(
    {"new", "used", "damaged", "scrapped"}
)
_BUCKETS: Final[frozenset[str]] = frozenset(
    {
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
)
_MATERIAL_IDENTIFIER_TYPES: Final[frozenset[str]] = frozenset(
    {"sku_code", "qr_code", "external_code", "unknown"}
)
_SERIAL_IDENTIFIER_TYPES: Final[frozenset[str]] = frozenset(
    {"serial_no", "qr_code", "unknown"}
)
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


class StocktakeCountError(RuntimeError):
    """Stable, database-detail-free command failure."""

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
class StocktakeSnapshotCountInput:
    """A complete physical count for one account in the cutoff snapshot."""

    stock_account_id: uuid.UUID
    counted_qty: Decimal
    count_method: str = "manual"
    serial_ids: tuple[uuid.UUID, ...] = ()
    # Open counts must echo the exact cutoff quantity; blind counts must omit it.
    book_qty_confirmation: Decimal | None = None
    reason_code: str | None = None
    remark: str = ""


@dataclass(frozen=True, slots=True)
class StocktakePhysicalObservationInput:
    """Verified physical dimension that had no account in the cutoff snapshot."""

    material_id: uuid.UUID | None
    material_identifier_raw: str
    material_identifier_type: str
    condition_code: str
    availability_bucket: str
    counted_qty: Decimal
    lot_id: uuid.UUID | None = None
    lot_no_raw: str | None = None
    serial_id: uuid.UUID | None = None
    serial_no_raw: str | None = None
    serial_identifier_type: str | None = None
    count_method: str = "manual"
    reason_code: str | None = None
    remark: str = ""


@dataclass(frozen=True, slots=True)
class SubmitStocktakeInitialScopeCountCommand:
    task_id: uuid.UUID
    round_id: uuid.UUID
    scope_id: uuid.UUID
    count_mode: str
    account_counts: tuple[StocktakeSnapshotCountInput, ...] = ()
    physical_observations: tuple[StocktakePhysicalObservationInput, ...] = ()
    evidence_file_ids: tuple[uuid.UUID, ...] = ()
    zero_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class StocktakeInitialScopeCountResult:
    task_id: uuid.UUID
    round_id: uuid.UUID
    scope_id: uuid.UUID
    task_status: str
    round_status: str
    task_version: int
    scope_completed: bool
    round_submitted: bool
    evidence_file_count: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _PreparedCount:
    value: StocktakeSnapshotCountInput
    snapshot: StocktakeSnapshotLine
    account: StockAccount
    policy: MaterialInventoryPolicy
    serials: tuple[InventorySerial, ...]
    snapshot_serial_ids: frozenset[uuid.UUID]


@dataclass(frozen=True, slots=True)
class _PreparedObservation:
    value: StocktakePhysicalObservationInput
    material: FormalMaterial | None
    policy: MaterialInventoryPolicy | None
    lot: InventoryLot | None
    serial: InventorySerial | None
    verification_status: str
    dimension_sha256: str


@dataclass(frozen=True, slots=True)
class _CountReferencePlan:
    """Bounded request-reference candidates captured around the owner lock."""

    owner_lock_material_ids: tuple[uuid.UUID, ...]
    resolved_material_ids: tuple[uuid.UUID, ...]
    lot_ids: tuple[uuid.UUID, ...]
    owner_lock_serial_ids: tuple[uuid.UUID, ...]
    material_qr_code_ids: tuple[uuid.UUID, ...]
    serial_qr_code_ids: tuple[uuid.UUID, ...]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class _SnapshotReferencePlan:
    """Task-local snapshot coordinates probed before shared owner locks."""

    account_ids: tuple[uuid.UUID, ...]
    serial_ids: tuple[uuid.UUID, ...]
    manifest_sha256: str


@dataclass(frozen=True, slots=True)
class _PreparedResolution:
    """Canonical, write-ready resolution captured on one database read."""

    counts: tuple[_PreparedCount, ...]
    observations: tuple[_PreparedObservation, ...]
    sha256: str


def _capture_snapshot_reference_plan(
    db: Session,
    task_id: uuid.UUID,
) -> _SnapshotReferencePlan:
    rows = tuple(
        db.scalars(
            select(StocktakeSnapshotLine)
            .where(StocktakeSnapshotLine.task_id == task_id)
            .order_by(
                StocktakeSnapshotLine.scope_id,
                StocktakeSnapshotLine.stock_account_id,
                StocktakeSnapshotLine.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    serial_ids = tuple(
        sorted(
            {
                serial_id
                for row in rows
                for serial_id in _snapshot_serial_ids(row)
            },
            key=str,
        )
    )
    return _SnapshotReferencePlan(
        account_ids=tuple(
            sorted({row.stock_account_id for row in rows}, key=str)
        ),
        serial_ids=serial_ids,
        manifest_sha256=_sha256(
            {
                "rows": [
                    {
                        "account_dimension_sha256": row.account_dimension_sha256,
                        "book_qty": _canonical_quantity(row.book_qty),
                        "created_at": _timestamp(row.created_at),
                        "id": str(row.id),
                        "ledger_cursor": row.ledger_cursor,
                        "scope_id": str(row.scope_id),
                        "serial_count": row.serial_count,
                        "serial_snapshot_jsonb": row.serial_snapshot_jsonb,
                        "serial_snapshot_sha256": row.serial_snapshot_sha256,
                        "stock_account_id": str(row.stock_account_id),
                        "task_id": str(row.task_id),
                    }
                    for row in rows
                ],
                "schema": "cloud_oam.stocktake.count_snapshot_reference_probe.v1",
                "task_id": str(task_id),
            }
        ),
    )


def _capture_count_reference_plan(
    db: Session,
    task: FormalStocktakeTask,
    command: SubmitStocktakeInitialScopeCountCommand,
    scopes: Sequence[FormalStocktakeScope],
) -> _CountReferencePlan:
    """Capture every request candidate and exact 0027 owner-lock union.

    Owner-lock ids deliberately retain explicit ids and QR ``object_id`` values
    even when no referenced master row exists.  PostgreSQL's migration-owned
    helpers then reject a dangling reference instead of silently shrinking the
    lock graph to the rows that happened to resolve during this pre-read.
    """

    observations = command.physical_observations
    sku_values = tuple(
        sorted(
            {
                row.material_identifier_raw
                for row in observations
                if row.material_identifier_type in {"sku_code", "unknown"}
            }
        )
    )
    external_values: set[uuid.UUID] = set()
    for row in observations:
        if row.material_identifier_type not in {"external_code", "unknown"}:
            continue
        try:
            external_values.add(uuid.UUID(row.material_identifier_raw))
        except (ValueError, TypeError, AttributeError):
            pass
    qr_values = tuple(
        sorted(
            {
                row.material_identifier_raw
                for row in observations
                if row.material_identifier_type in {"qr_code", "unknown"}
            }
        )
    )
    material_qr_rows = tuple(
        db.scalars(
            select(QrCode)
            .where(
                QrCode.code.in_(qr_values),
                QrCode.object_type == "material",
                QrCode.status == "active",
            )
            .order_by(QrCode.id)
            .execution_options(populate_existing=True)
        ).all()
    ) if qr_values else ()

    explicit_material_ids = {
        row.material_id for row in observations if row.material_id is not None
    }
    scope_material_ids = {
        row.material_id for row in scopes if row.material_id is not None
    }
    material_conditions = []
    if sku_values:
        material_conditions.append(
            (FormalMaterial.sku_code.in_(sku_values))
            & (FormalMaterial.status == "active")
        )
    if external_values:
        material_conditions.append(
            (FormalMaterial.external_object_id.in_(tuple(external_values)))
            & (FormalMaterial.status == "active")
        )
    qr_object_ids = {row.object_id for row in material_qr_rows}
    if qr_object_ids:
        material_conditions.append(
            (FormalMaterial.id.in_(tuple(qr_object_ids)))
            & (FormalMaterial.status == "active")
        )
    if explicit_material_ids:
        material_conditions.append(FormalMaterial.id.in_(tuple(explicit_material_ids)))
    material_rows = tuple(
        db.scalars(
            select(FormalMaterial)
            .where(or_(*material_conditions))
            .order_by(FormalMaterial.id)
            .execution_options(populate_existing=True)
        ).all()
    ) if material_conditions else ()
    resolved_material_ids = tuple(sorted({row.id for row in material_rows}, key=str))
    owner_lock_material_ids = tuple(
        sorted(
            scope_material_ids
            | explicit_material_ids
            | qr_object_ids
            | set(resolved_material_ids),
            key=str,
        )
    )

    lot_numbers = tuple(
        sorted({row.lot_no_raw for row in observations if row.lot_no_raw is not None})
    )
    lot_rows = tuple(
        db.scalars(
            select(InventoryLot)
            .where(
                InventoryLot.material_id.in_(resolved_material_ids),
                InventoryLot.lot_no.in_(lot_numbers),
            )
            .order_by(InventoryLot.id)
            .execution_options(populate_existing=True)
        ).all()
    ) if resolved_material_ids and lot_numbers else ()

    serial_number_values = tuple(
        sorted(
            {
                row.serial_no_raw
                for row in observations
                if row.serial_no_raw is not None
                and row.serial_identifier_type in {"serial_no", "unknown"}
            }
        )
    )
    serial_qr_values = tuple(
        sorted(
            {
                row.serial_no_raw
                for row in observations
                if row.serial_no_raw is not None
                and row.serial_identifier_type in {"qr_code", "unknown"}
            }
        )
    )
    serial_qr_rows = tuple(
        db.scalars(
            select(QrCode)
            .where(
                QrCode.code.in_(serial_qr_values),
                QrCode.object_type == "serial",
                QrCode.status == "active",
            )
            .order_by(QrCode.id)
            .execution_options(populate_existing=True)
        ).all()
    ) if serial_qr_values else ()
    explicit_serial_ids = {
        serial_id
        for row in command.account_counts
        for serial_id in row.serial_ids
    }
    explicit_serial_ids.update(
        row.serial_id for row in observations if row.serial_id is not None
    )
    serial_qr_object_ids = {row.object_id for row in serial_qr_rows}
    serial_conditions = []
    if explicit_serial_ids or serial_qr_object_ids:
        serial_conditions.append(
            InventorySerial.id.in_(tuple(explicit_serial_ids | serial_qr_object_ids))
        )
    if serial_number_values:
        serial_conditions.append(InventorySerial.serial_no.in_(serial_number_values))
    if serial_qr_values:
        serial_conditions.append(InventorySerial.qr_code.in_(serial_qr_values))
    serial_rows = tuple(
        db.scalars(
            select(InventorySerial)
            .where(or_(*serial_conditions))
            .order_by(InventorySerial.id)
            .execution_options(populate_existing=True)
        ).all()
    ) if serial_conditions else ()
    owner_lock_serial_ids = tuple(
        sorted(
            explicit_serial_ids
            | serial_qr_object_ids
            | {row.id for row in serial_rows},
            key=str,
        )
    )

    policy_rows = tuple(
        db.scalars(
            select(MaterialInventoryPolicy)
            .where(
                MaterialInventoryPolicy.material_id.in_(resolved_material_ids),
                MaterialInventoryPolicy.effective_from <= task.cutoff_at,
                or_(
                    MaterialInventoryPolicy.effective_to.is_(None),
                    MaterialInventoryPolicy.effective_to > task.cutoff_at,
                ),
            )
            .order_by(
                MaterialInventoryPolicy.material_id,
                MaterialInventoryPolicy.effective_from,
                MaterialInventoryPolicy.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if resolved_material_ids and task.cutoff_at is not None else ()

    manifest = _sha256(
        {
            "lots": [
                {
                    "expiry_date": row.expiry_date.isoformat() if row.expiry_date else None,
                    "id": str(row.id),
                    "lot_no": row.lot_no,
                    "manufacture_date": (
                        row.manufacture_date.isoformat() if row.manufacture_date else None
                    ),
                    "material_id": str(row.material_id),
                    "updated_at": _timestamp(row.updated_at),
                }
                for row in lot_rows
            ],
            "materials": [
                {
                    "base_unit": row.base_unit,
                    "external_object_id": str(row.external_object_id),
                    "id": str(row.id),
                    "sku_code": row.sku_code,
                    "source_updated_at": _timestamp(row.source_updated_at),
                    "status": row.status,
                    "updated_at": _timestamp(row.updated_at),
                }
                for row in material_rows
            ],
            "policies": [
                {
                    "allow_fraction": row.allow_fraction,
                    "effective_from": _timestamp(row.effective_from),
                    "effective_to": _timestamp(row.effective_to),
                    "id": str(row.id),
                    "material_id": str(row.material_id),
                    "quantity_scale": row.quantity_scale,
                    "tracking_mode": row.tracking_mode,
                    "updated_at": _timestamp(row.updated_at),
                }
                for row in policy_rows
            ],
            "qr_codes": [
                {
                    "code": row.code,
                    "id": str(row.id),
                    "object_id": str(row.object_id),
                    "object_type": row.object_type,
                    "printed_at": _timestamp(row.printed_at),
                    "status": row.status,
                    "updated_at": _timestamp(row.updated_at),
                }
                for row in material_qr_rows
            ],
            "serial_qr_codes": [
                {
                    "code": row.code,
                    "id": str(row.id),
                    "object_id": str(row.object_id),
                    "object_type": row.object_type,
                    "printed_at": _timestamp(row.printed_at),
                    "status": row.status,
                    "updated_at": _timestamp(row.updated_at),
                }
                for row in serial_qr_rows
            ],
            "schema": "cloud_oam.stocktake.count_input_reference_probe.v1",
            "serials": [
                {
                    "id": str(row.id),
                    "lifecycle_status": row.lifecycle_status,
                    "lot_id": str(row.lot_id) if row.lot_id is not None else None,
                    "material_id": str(row.material_id),
                    "qr_code": row.qr_code,
                    "serial_no": row.serial_no,
                    "updated_at": _timestamp(row.updated_at),
                }
                for row in serial_rows
            ],
        }
    )
    return _CountReferencePlan(
        owner_lock_material_ids=owner_lock_material_ids,
        resolved_material_ids=resolved_material_ids,
        lot_ids=tuple(sorted({row.id for row in lot_rows}, key=str)),
        owner_lock_serial_ids=owner_lock_serial_ids,
        material_qr_code_ids=tuple(
            sorted({row.id for row in material_qr_rows}, key=str)
        ),
        serial_qr_code_ids=tuple(
            sorted({row.id for row in serial_qr_rows}, key=str)
        ),
        manifest_sha256=manifest,
    )


def submit_stocktake_initial_scope_count(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitStocktakeInitialScopeCountCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeInitialScopeCountResult:
    """Submit one complete non-opening initial-round scope without commit."""

    try:
        return _submit_stocktake_initial_scope_count(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            idempotency_hmac_secret=idempotency_hmac_secret,
            trace_request_id=trace_request_id,
        )
    except StocktakeCountError:
        raise
    except task_service.StocktakeTaskError as exc:
        raise StocktakeCountError(
            "stocktake_count_reference_graph_invalid",
            exc.category,
            "盘点任务的冻结范围或主数据引用图无效",
        ) from None
    except AuditChainError:
        raise StocktakeCountError(
            "stocktake_count_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，本次盘点提交未完成",
        ) from None
    except IntegrityError:
        raise StocktakeCountError(
            "stocktake_count_concurrent_conflict",
            "conflict",
            "盘点提交发生并发冲突，请回滚并重新读取",
        ) from None
    except DBAPIError:
        raise StocktakeCountError(
            "stocktake_count_database_unavailable",
            "service_unavailable",
            "数据库暂时不可用，本次盘点提交未完成",
        ) from None


def _submit_stocktake_initial_scope_count(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitStocktakeInitialScopeCountCommand,
    idempotency_key: str,
    idempotency_hmac_secret: bytes | str,
    trace_request_id: str,
) -> StocktakeInitialScopeCountResult:
    supplied = _validate_supplied_actor(actor)
    checked = _validate_command(command)
    raw_key = _require_idempotency_key(idempotency_key)
    secret = _require_hmac_secret(idempotency_hmac_secret)
    trace_id = _require_trace_request_id(trace_request_id)
    path = (
        f"/api/v1/stocktakes/{checked.task_id}/rounds/"
        f"{checked.round_id}/scopes/{checked.scope_id}/initial-count"
    )
    key_hash = _idempotency_hmac(secret, supplied.user_id, path, raw_key)
    request_hash = _request_hmac(secret, supplied, checked)
    _take_advisory_locks(
        db,
        (
            _lock_coordinate("stocktake-count-idempotency", key_hash),
            _lock_coordinate("stocktake-count-task", str(checked.task_id)),
            _lock_coordinate("stocktake-count-round", str(checked.round_id)),
        ),
    )

    # Inventory writers take the global ledger head before every account,
    # reference, freeze and audit row.  A physical-count boundary must follow
    # the identical first lock so it cannot deadlock with a concurrent posting
    # and so the sealed cursor is a deterministic commit boundary rather than
    # a wall-clock guess.
    count_ledger_cursor = _lock_current_ledger_cursor(db)

    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked.task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None or task.task_type not in _NON_OPENING_TYPES:
        _fail("stocktake_count_task_not_found", "not_found", "非期初盘点任务不存在")
    if (
        task.cutoff_ledger_cursor is None
        or task.cutoff_ledger_cursor < 0
        or count_ledger_cursor < task.cutoff_ledger_cursor
    ):
        _fail(
            "stocktake_count_ledger_boundary_invalid",
            "service_unavailable",
            "盘点截止游标与实盘封印游标不一致",
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
    scope_by_id = {row.id: row for row in scopes}
    scope = scope_by_id.get(checked.scope_id)
    if scope is None:
        _fail("stocktake_count_scope_not_found", "not_found", "盘点范围不存在")

    lock_formal_principal_graph(
        db,
        tuple(sorted({supplied.user_id, *(row.assignee_user_id for row in scopes)})),
    )
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
        _fail("stocktake_count_round_not_found", "not_found", "盘点轮次不存在")

    reference_plan = _capture_count_reference_plan(db, task, checked, scopes)
    snapshot_probe = _capture_snapshot_reference_plan(db, task.id)
    if task.cutoff_at is None:
        _fail(
            "stocktake_count_cutoff_missing",
            "service_unavailable",
            "盘点截止时点缺失",
        )
    lock_opening_stocktake_start_reference(
        db,
        task.region_org_id,
        tuple(row.owner_org_id for row in scopes),
        tuple(row.location_id for row in scopes),
        reference_plan.owner_lock_material_ids,
        task.cutoff_at,
    )
    lock_inventory_reference_graph(db, snapshot_probe.account_ids, task.cutoff_at)
    lock_inventory_serial_graph(
        db,
        tuple(
            sorted(
                set(reference_plan.owner_lock_serial_ids).union(
                    snapshot_probe.serial_ids
                ),
                key=str,
            )
        ),
    )
    if (
        _capture_count_reference_plan(db, task, checked, scopes) != reference_plan
        or _capture_snapshot_reference_plan(db, task.id) != snapshot_probe
    ):
        _fail(
            "stocktake_count_reference_graph_changed",
            "conflict",
            "盘点输入引用在锁定期间发生变化，请回滚并重新读取",
        )

    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    grant = _authorize_exact_scope_actor(db, current, task, scope)
    assignment = _lock_current_assignment(db, current, grant, now)
    plans = task_service._load_and_validate_scope_plans(db, task, scopes, now=now)
    plan_by_scope = {row.scope_id: row for row in plans}
    plan = plan_by_scope.get(scope.id)
    if plan is None:
        _fail(
            "stocktake_count_scope_integrity_invalid",
            "service_unavailable",
            "盘点范围无法与创建证据唯一对应",
        )

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
    _validate_task_round_and_freeze(task, round_row, scope, plan.freeze_mode, freeze, now)
    expected_count_mode = "blind" if task.blind_count else "open"
    if checked.count_mode != expected_count_mode:
        _fail(
            "stocktake_count_mode_task_mismatch",
            "precondition_failed",
            "提交盘点模式与任务冻结的明盘/盲盘契约不一致",
        )

    snapshots, accounts, scope_snapshots = _load_count_snapshot_graph(
        db,
        task=task,
        plans=plans,
        scope=scope,
    )

    files = _lock_evidence_files(db, checked.evidence_file_ids, current.user_id)
    existing = db.scalar(
        _select_only_reference_statement(
            db,
            select(StocktakeScopeCountCompletion).where(
                StocktakeScopeCountCompletion.idempotency_key_hash == key_hash
            ),
        )
        .execution_options(populate_existing=True)
    )
    if existing is not None:
        return replace(
            _validate_replay(
                db,
                task=task,
                round_row=round_row,
                scope=scope,
                actor=current,
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
            "stocktake_count_scope_already_completed",
            "conflict",
            "该盘点范围已经提交且不可修改",
        )
    _require_new_submission_state(task, round_row, scopes, now)
    if _scope_has_partial_evidence(db, task.id, round_row.id, scope.id):
        _fail(
            "stocktake_count_partial_evidence_exists",
            "conflict",
            "该范围存在未封印的盘点证据，请回滚并重新读取",
        )

    prepared_resolution = _prepare_count_resolution(
        db,
        task=task,
        scope=scope,
        snapshots=scope_snapshots,
        accounts=accounts,
        command=checked,
        round_id=round_row.id,
    )
    if (
        _capture_count_reference_plan(db, task, checked, scopes) != reference_plan
        or _capture_snapshot_reference_plan(db, task.id) != snapshot_probe
    ):
        _fail(
            "stocktake_count_reference_graph_changed",
            "conflict",
            "盘点输入引用在处理期间发生变化，请回滚并重新读取",
        )

    # The audit head is the final shared mutable lock.  Principal, task,
    # round, scope, snapshot, policy, SN and file rows are already locked.
    lock_audit_chain_head(db, stream_key=INVENTORY_STREAM_KEY)
    now = _database_now(db)
    current = _require_current_actor(db, supplied, now)
    grant = _authorize_exact_scope_actor(db, current, task, scope)
    assignment = _lock_current_assignment(db, current, grant, now, lock_rows=False)
    _validate_task_round_and_freeze(task, round_row, scope, plan.freeze_mode, freeze, now)
    _require_new_submission_state(task, round_row, scopes, now)

    # The audit-head wait is the final point at which this transaction can be
    # delayed by another writer.  Re-read every owner-locked reference and
    # rebuild the resolution from fresh ORM state.  Compare only a canonical
    # scalar digest: ORM identity/equality is not a concurrency proof.
    if (
        _capture_count_reference_plan(db, task, checked, scopes) != reference_plan
        or _capture_snapshot_reference_plan(db, task.id) != snapshot_probe
    ):
        _fail(
            "stocktake_count_reference_graph_changed",
            "conflict",
            "盘点输入引用在审计锁等待期间发生变化，请回滚并重新读取",
        )
    _fresh_snapshots, fresh_accounts, fresh_scope_snapshots = (
        _load_count_snapshot_graph(
            db,
            task=task,
            plans=plans,
            scope=scope,
        )
    )
    fresh_resolution = _prepare_count_resolution(
        db,
        task=task,
        scope=scope,
        snapshots=fresh_scope_snapshots,
        accounts=fresh_accounts,
        command=checked,
        round_id=round_row.id,
    )
    if fresh_resolution.sha256 != prepared_resolution.sha256:
        _fail(
            "stocktake_count_prepared_resolution_changed",
            "conflict",
            "盘点解析结果在审计锁等待期间发生变化，请回滚并重新读取",
        )

    return _write_scope_count(
        db,
        actor=current,
        grant=grant,
        assignment=assignment,
        task=task,
        round_row=round_row,
        scopes=scopes,
        scope=scope,
        prepared_counts=fresh_resolution.counts,
        prepared_observations=fresh_resolution.observations,
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
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    scope: FormalStocktakeScope,
    prepared_counts: Sequence[_PreparedCount],
    prepared_observations: Sequence[_PreparedObservation],
    files: Sequence[FileObject],
    zero_confirmed: bool,
    key_hash: str,
    request_hash: str,
    count_ledger_cursor: int,
    secret: bytes,
    trace_request_id: str,
    now: datetime,
) -> StocktakeInitialScopeCountResult:
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
            idempotency_key_hash=_child_hmac(secret, key_hash, prepared.dimension_sha256),
            created_at=now,
        )
        db.add(row)
        observations.append(row)
        if prepared.serial is not None:
            serial_count += 1
    db.flush()

    authorization_hash = _authorization_sha256(actor, assignment, grant, now)
    completion_id = uuid.uuid4()
    evidence_manifest = _scope_evidence_manifest(
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
    total = _quantity_sum(
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
    sealed = len(completions) == len(scopes)
    if sealed:
        _seal_initial_round(
            db,
            actor=actor,
            assignment=assignment,
            task=task,
            round_row=round_row,
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
            reason="stocktake_initial_scope_count_completed",
            actor_id=actor.user_id,
            idempotency_key=_event_key("scope", completion.id),
            occurred_at=now,
            metadata_jsonb={
                "count_ledger_cursor": count_ledger_cursor,
                "count_mode": "blind" if task.blind_count else "open",
                "evidence_file_count": len(files),
                "round_id": str(round_row.id),
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
                    reason="stocktake_initial_round_submitted",
                    actor_id=actor.user_id,
                    idempotency_key=_event_key("round", round_row.id),
                    occurred_at=now,
                    metadata_jsonb={
                        "difference_status": "not_evaluated",
                        "round_no": 1,
                        "task_id": str(task.id),
                    },
                    created_at=now,
                ),
                StateTransitionEvent(
                    aggregate_type="stocktake_task",
                    aggregate_id=str(task.id),
                    from_status="counting",
                    to_status="submitted",
                    reason="stocktake_initial_round_submitted",
                    actor_id=actor.user_id,
                    idempotency_key=_event_key("task", round_row.id),
                    occurred_at=now,
                    metadata_jsonb={
                        "difference_status": "not_evaluated",
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
        action="stocktake.scope_count.submitted",
        aggregate_type="stocktake_scope",
        aggregate_id=str(scope.id),
        before_jsonb=None,
        after_jsonb={
            "count_ledger_cursor": count_ledger_cursor,
            "count_line_count": len(lines),
            "evidence_file_count": len(files),
            "observation_line_count": len(observations),
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
            action="stocktake.initial_round.submitted",
            aggregate_type="stocktake_round",
            aggregate_id=str(round_row.id),
            before_jsonb={"status": "counting"},
            after_jsonb={
                "difference_status": "not_evaluated",
                "status": "submitted",
                "task_id": str(task.id),
                "task_status": "submitted",
                "task_version": task.version,
            },
            request_id=_request_reference(trace_request_id),
            occurred_at=now,
        )
    db.flush()
    return StocktakeInitialScopeCountResult(
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        task_status=task.status,
        round_status=round_row.status,
        task_version=task.version,
        scope_completed=True,
        round_submitted=sealed,
        evidence_file_count=len(files),
    )


def _seal_initial_round(
    db: Session,
    *,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    completions: Sequence[StocktakeScopeCountCompletion],
    sealing_completion_id: uuid.UUID,
    now: datetime,
) -> None:
    count_manifest = _persisted_count_manifest(
        db, task, round_row, completions=completions
    )
    round_manifest = _round_manifest(
        task.id,
        round_row.id,
        completions,
        sealing_completion_id,
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
        total_counted_qty=_quantity_sum(
            tuple(row.total_counted_qty for row in completions)
        ),
        round_manifest_sha256=round_manifest,
        count_manifest_sha256=count_manifest,
        request_sha256=_sha256(
            {
                "count_manifest_sha256": count_manifest,
                "round_manifest_sha256": round_manifest,
                "schema": "cloud_oam.stocktake.initial_round_submission.v1",
                "sealing_completion_id": str(sealing_completion_id),
            }
        ),
        idempotency_key_hash=_sha256(
            {
                "round_id": str(round_row.id),
                "schema": "cloud_oam.stocktake.initial_round_idempotency.v1",
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
    db.flush()
    task.status = "submitted"
    task.submitted_at = now
    task.version += 1
    task.updated_at = now
    db.flush()


def _load_count_snapshot_graph(
    db: Session,
    *,
    task: FormalStocktakeTask,
    plans: Sequence[task_service._ScopePlan],
    scope: FormalStocktakeScope,
) -> tuple[
    tuple[StocktakeSnapshotLine, ...],
    Mapping[uuid.UUID, StockAccount],
    tuple[StocktakeSnapshotLine, ...],
]:
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
    accounts = _lock_snapshot_accounts(db, snapshots)
    _validate_snapshot_manifest(task, plans, snapshots, accounts)
    return (
        snapshots,
        accounts,
        tuple(row for row in snapshots if row.scope_id == scope.id),
    )


def _prepare_count_resolution(
    db: Session,
    *,
    task: FormalStocktakeTask,
    scope: FormalStocktakeScope,
    snapshots: Sequence[StocktakeSnapshotLine],
    accounts: Mapping[uuid.UUID, StockAccount],
    command: SubmitStocktakeInitialScopeCountCommand,
    round_id: uuid.UUID,
) -> _PreparedResolution:
    counts = _prepare_snapshot_counts(
        db,
        task=task,
        scope=scope,
        snapshots=snapshots,
        accounts=accounts,
        values=command.account_counts,
        count_mode=command.count_mode,
    )
    observations = _prepare_observations(
        db,
        task=task,
        scope=scope,
        snapshots=snapshots,
        accounts=accounts,
        values=command.physical_observations,
    )
    if not snapshots and not observations and not command.zero_confirmed:
        _fail(
            "stocktake_count_zero_confirmation_required",
            "invalid_request",
            "空盘点范围必须显式零确认",
        )
    if command.zero_confirmed and (snapshots or observations or counts):
        _fail(
            "stocktake_count_zero_confirmation_conflict",
            "invalid_request",
            "零确认不能与账面账户或实盘记录同时提交",
        )
    _validate_round_serial_uniqueness(db, round_id, counts, observations)
    return _PreparedResolution(
        counts=counts,
        observations=observations,
        sha256=_prepared_resolution_sha256(counts, observations),
    )


def _prepared_resolution_sha256(
    counts: Sequence[_PreparedCount],
    observations: Sequence[_PreparedObservation],
) -> str:
    """Freeze only canonical scalar resolution state, never ORM equality."""

    count_documents = [
        {
            "account": {
                "availability_bucket": row.account.availability_bucket,
                "condition_code": row.account.condition_code,
                "created_at": _timestamp(row.account.created_at),
                "custodian_person_id": (
                    str(row.account.custodian_person_id)
                    if row.account.custodian_person_id is not None
                    else None
                ),
                "id": str(row.account.id),
                "location_id": str(row.account.location_id),
                "lot_id": (
                    str(row.account.lot_id) if row.account.lot_id is not None else None
                ),
                "material_id": str(row.account.material_id),
                "owner_org_id": str(row.account.owner_org_id),
                "updated_at": _timestamp(row.account.updated_at),
            },
            "input": {
                "book_qty_confirmation": _canonical_quantity(
                    row.value.book_qty_confirmation
                ),
                "count_method": row.value.count_method,
                "counted_qty": _canonical_quantity(row.value.counted_qty),
                "reason_code": row.value.reason_code,
                "remark": row.value.remark,
                "serial_ids": [str(value) for value in row.value.serial_ids],
                "stock_account_id": str(row.value.stock_account_id),
            },
            "policy": _policy_resolution_document(row.policy),
            "resolved_serials": [
                _serial_resolution_document(value)
                for value in sorted(row.serials, key=lambda value: str(value.id))
            ],
            "snapshot": {
                "account_dimension_sha256": row.snapshot.account_dimension_sha256,
                "book_qty": _canonical_quantity(row.snapshot.book_qty),
                "created_at": _timestamp(row.snapshot.created_at),
                "id": str(row.snapshot.id),
                "ledger_cursor": row.snapshot.ledger_cursor,
                "scope_id": str(row.snapshot.scope_id),
                "serial_count": row.snapshot.serial_count,
                "serial_snapshot_jsonb": row.snapshot.serial_snapshot_jsonb,
                "serial_snapshot_sha256": row.snapshot.serial_snapshot_sha256,
                "stock_account_id": str(row.snapshot.stock_account_id),
                "task_id": str(row.snapshot.task_id),
            },
            "snapshot_serial_ids": [
                str(value) for value in sorted(row.snapshot_serial_ids, key=str)
            ],
        }
        for row in sorted(counts, key=lambda value: str(value.account.id))
    ]
    observation_documents = [
        {
            "dimension_sha256": row.dimension_sha256,
            "input": {
                "availability_bucket": row.value.availability_bucket,
                "condition_code": row.value.condition_code,
                "count_method": row.value.count_method,
                "counted_qty": _canonical_quantity(row.value.counted_qty),
                "lot_id": str(row.value.lot_id) if row.value.lot_id else None,
                "lot_no_raw": row.value.lot_no_raw,
                "material_id": (
                    str(row.value.material_id)
                    if row.value.material_id is not None
                    else None
                ),
                "material_identifier_raw": row.value.material_identifier_raw,
                "material_identifier_type": row.value.material_identifier_type,
                "reason_code": row.value.reason_code,
                "remark": row.value.remark,
                "serial_id": (
                    str(row.value.serial_id)
                    if row.value.serial_id is not None
                    else None
                ),
                "serial_identifier_type": row.value.serial_identifier_type,
                "serial_no_raw": row.value.serial_no_raw,
            },
            "lot": (
                {
                    "expiry_date": (
                        row.lot.expiry_date.isoformat()
                        if row.lot.expiry_date is not None
                        else None
                    ),
                    "id": str(row.lot.id),
                    "lot_no": row.lot.lot_no,
                    "manufacture_date": (
                        row.lot.manufacture_date.isoformat()
                        if row.lot.manufacture_date is not None
                        else None
                    ),
                    "material_id": str(row.lot.material_id),
                    "updated_at": _timestamp(row.lot.updated_at),
                }
                if row.lot is not None
                else None
            ),
            "material": (
                {
                    "base_unit": row.material.base_unit,
                    "external_object_id": str(row.material.external_object_id),
                    "id": str(row.material.id),
                    "sku_code": row.material.sku_code,
                    "source_updated_at": _timestamp(row.material.source_updated_at),
                    "status": row.material.status,
                    "updated_at": _timestamp(row.material.updated_at),
                }
                if row.material is not None
                else None
            ),
            "policy": (
                _policy_resolution_document(row.policy)
                if row.policy is not None
                else None
            ),
            "serial": (
                _serial_resolution_document(row.serial)
                if row.serial is not None
                else None
            ),
            "verification_status": row.verification_status,
        }
        for row in sorted(observations, key=lambda value: value.dimension_sha256)
    ]
    return _sha256(
        {
            "counts": count_documents,
            "observations": observation_documents,
            "schema": "cloud_oam.stocktake.prepared_resolution.v1",
        }
    )


def _policy_resolution_document(
    policy: MaterialInventoryPolicy,
) -> Mapping[str, object]:
    return {
        "allow_fraction": policy.allow_fraction,
        "effective_from": _timestamp(policy.effective_from),
        "effective_to": _timestamp(policy.effective_to),
        "id": str(policy.id),
        "material_id": str(policy.material_id),
        "quantity_scale": policy.quantity_scale,
        "tracking_mode": policy.tracking_mode,
        "updated_at": _timestamp(policy.updated_at),
    }


def _serial_resolution_document(serial: InventorySerial) -> Mapping[str, object]:
    return {
        "id": str(serial.id),
        "lifecycle_status": serial.lifecycle_status,
        "lot_id": str(serial.lot_id) if serial.lot_id is not None else None,
        "material_id": str(serial.material_id),
        "qr_code": serial.qr_code,
        "serial_no": serial.serial_no,
        "updated_at": _timestamp(serial.updated_at),
    }


def _prepare_snapshot_counts(
    db: Session,
    *,
    task: FormalStocktakeTask,
    scope: FormalStocktakeScope,
    snapshots: Sequence[StocktakeSnapshotLine],
    accounts: Mapping[uuid.UUID, StockAccount],
    values: Sequence[StocktakeSnapshotCountInput],
    count_mode: str,
) -> tuple[_PreparedCount, ...]:
    value_by_account = {row.stock_account_id: row for row in values}
    snapshot_ids = {row.stock_account_id for row in snapshots}
    if set(value_by_account) != snapshot_ids:
        _fail(
            "stocktake_count_snapshot_coverage_invalid",
            "invalid_request",
            "盘点范围必须逐一完整提交全部截止快照账户",
        )
    serial_ids = tuple(
        sorted(
            {serial_id for value in values for serial_id in value.serial_ids},
            key=str,
        )
    )
    serials = _lock_serials(db, serial_ids)
    prepared: list[_PreparedCount] = []
    for snapshot in snapshots:
        value = value_by_account[snapshot.stock_account_id]
        account = accounts.get(snapshot.stock_account_id)
        if account is None:
            _fail(
                "stocktake_count_snapshot_account_missing",
                "service_unavailable",
                "截止快照账户引用不完整",
            )
        if count_mode == "blind" and value.book_qty_confirmation is not None:
            _fail(
                "stocktake_count_blind_book_qty_forbidden",
                "invalid_request",
                "盲盘提交不得携带账面数量",
            )
        if count_mode == "open" and value.book_qty_confirmation != snapshot.book_qty:
            _fail(
                "stocktake_count_open_book_qty_mismatch",
                "precondition_failed",
                "明盘账面数量确认与截止快照不一致",
            )
        _require_account_in_scope(account, scope)
        policy = _load_policy(db, account.material_id, task.cutoff_at)
        _validate_account_policy(account, policy)
        _validate_quantity(value.counted_qty, policy, positive=False)
        selected = tuple(serials[serial_id] for serial_id in value.serial_ids)
        if policy.tracking_mode in {"serial", "lot_and_serial"}:
            if value.counted_qty != Decimal(len(selected)):
                _fail(
                    "stocktake_count_serial_quantity_mismatch",
                    "invalid_request",
                    "SN 物料必须逐件盘点且数量与 SN 数一致",
                )
            for serial in selected:
                if (
                    serial.lifecycle_status != "active"
                    or serial.material_id != account.material_id
                    or serial.lot_id != account.lot_id
                ):
                    _fail(
                        "stocktake_count_serial_binding_invalid",
                        "invalid_request",
                        "SN 与盘点账户的物料、批次或生命周期不一致",
                    )
        elif selected:
            _fail(
                "stocktake_count_serial_forbidden",
                "invalid_request",
                "非 SN 追踪物料不得提交 SN",
            )
        snapshot_serial_ids = _snapshot_serial_ids(snapshot)
        prepared.append(
            _PreparedCount(
                value=value,
                snapshot=snapshot,
                account=account,
                policy=policy,
                serials=selected,
                snapshot_serial_ids=snapshot_serial_ids,
            )
        )
    return tuple(prepared)


def _prepare_observations(
    db: Session,
    *,
    task: FormalStocktakeTask,
    scope: FormalStocktakeScope,
    snapshots: Sequence[StocktakeSnapshotLine],
    accounts: Mapping[uuid.UUID, StockAccount],
    values: Sequence[StocktakePhysicalObservationInput],
) -> tuple[_PreparedObservation, ...]:
    prepared: list[_PreparedObservation] = []
    seen_dimensions: set[str] = set()
    for value in values:
        material = _resolve_observation_material(db, value)
        if scope.material_id is not None and (
            material is None or scope.material_id != material.id
        ):
            _fail("stocktake_count_observation_outside_scope", "forbidden", "现场物料不在盘点范围内")
        if scope.condition_code is not None and scope.condition_code != value.condition_code:
            _fail("stocktake_count_observation_outside_scope", "forbidden", "现场成色不在盘点范围内")
        if (
            scope.availability_bucket is not None
            and scope.availability_bucket != value.availability_bucket
        ):
            _fail("stocktake_count_observation_outside_scope", "forbidden", "现场库存状态不在盘点范围内")
        policy = _load_policy(db, material.id, task.cutoff_at) if material else None
        if policy is None:
            if value.lot_id is not None or value.serial_id is not None:
                _fail(
                    "stocktake_count_pending_resolved_child_forbidden",
                    "invalid_request",
                    "物料未解析时不得绑定已解析批次或 SN",
                )
            if value.serial_no_raw is not None and value.counted_qty != Decimal("1"):
                _fail(
                    "stocktake_count_observation_serial_quantity",
                    "invalid_request",
                    "带现场 SN 的待核实观察也必须逐件且数量为 1",
                )
            lot = None
            serial = None
            verification_status = "pending_verification"
        else:
            _validate_quantity(value.counted_qty, policy, positive=True)
            lot, lot_resolved = _resolve_observation_lot(db, material, policy, value)
            serial, serial_resolved = _resolve_observation_serial(
                db, material, policy, lot, value
            )
            verification_status = (
                "verified"
                if lot_resolved and serial_resolved
                else "pending_verification"
            )

        matching = [
            account
            for account in accounts.values()
            if material is not None
            and _account_matches_observation(account, scope, value, material, lot)
        ]
        if any(row.stock_account_id in {account.id for account in matching} for row in snapshots):
            _fail(
                "stocktake_count_observation_requires_snapshot_line",
                "invalid_request",
                "截止快照已存在该实物维度，必须使用账户盘点行提交",
            )
        # A verified observation is only for a physical dimension that had no
        # account at cutoff; it must never be used to bypass an omitted account.
        if any(_as_utc(account.created_at) <= _as_utc(task.cutoff_at) for account in matching):
            _fail(
                "stocktake_count_observation_requires_cutoff_account",
                "precondition_failed",
                "截止时点已存在该库存账户，禁止改作现场新增观察",
            )
        dimension = _observation_dimension_sha256(
            scope,
            value,
            material=material,
            lot=lot,
            serial=serial,
            verification_status=verification_status,
        )
        if dimension in seen_dimensions:
            _fail(
                "stocktake_count_observation_duplicate",
                "invalid_request",
                "同一现场实物维度必须合并后提交",
            )
        seen_dimensions.add(dimension)
        prepared.append(
            _PreparedObservation(
                value=value,
                material=material,
                policy=policy,
                lot=lot,
                serial=serial,
                verification_status=verification_status,
                dimension_sha256=dimension,
            )
        )
    return tuple(prepared)


def _validate_snapshot_manifest(
    task: FormalStocktakeTask,
    plans: Sequence[task_service._ScopePlan],
    snapshots: Sequence[StocktakeSnapshotLine],
    accounts: Mapping[uuid.UUID, StockAccount],
) -> None:
    if (
        task.cutoff_at is None
        or task.cutoff_ledger_cursor is None
        or task.scope_manifest_sha256 is None
        or task.snapshot_manifest_sha256 is None
    ):
        _fail(
            "stocktake_count_snapshot_anchor_missing",
            "precondition_failed",
            "盘点截止快照锚点不完整",
        )
    plan_by_scope = {row.scope_id: row for row in plans}
    reconstructed: list[task_service._Snapshot] = []
    for snapshot in snapshots:
        plan = plan_by_scope.get(snapshot.scope_id)
        account = accounts.get(snapshot.stock_account_id)
        if (
            plan is None
            or account is None
            or snapshot.ledger_cursor != task.cutoff_ledger_cursor
        ):
            _fail(
                "stocktake_count_snapshot_graph_invalid",
                "service_unavailable",
                "盘点截止快照引用图不完整",
            )
        if (
            not isinstance(snapshot.book_qty, Decimal)
            or not snapshot.book_qty.is_finite()
            or snapshot.book_qty < _ZERO
            or _decimal_scale(snapshot.book_qty) > 3
            or not isinstance(snapshot.serial_count, int)
            or isinstance(snapshot.serial_count, bool)
            or snapshot.serial_count < 0
        ):
            _fail(
                "stocktake_count_snapshot_quantity_invalid",
                "service_unavailable",
                "盘点截止快照数量证据无效",
            )
        if not task_service._account_matches_plan(account, plan):
            _fail(
                "stocktake_count_snapshot_scope_mismatch",
                "service_unavailable",
                "截止快照账户超出冻结范围",
            )
        expected_account_hash = task_service._canonical_sha256(
            task_service._account_dimension_document(account)
        )
        serials = snapshot.serial_snapshot_jsonb
        if (
            not isinstance(serials, list)
            or snapshot.serial_count != len(serials)
            or snapshot.account_dimension_sha256 != expected_account_hash
            or snapshot.serial_snapshot_sha256
            != task_service._canonical_sha256(
                {
                    "schema": "cloud_oam.stocktake.serial_snapshot.v1",
                    "serials": serials,
                    "stock_account_id": str(account.id),
                }
            )
        ):
            _fail(
                "stocktake_count_snapshot_integrity_invalid",
                "service_unavailable",
                "盘点截止快照 hash 无法重算",
            )
        reconstructed.append(
            task_service._Snapshot(
                scope=plan,
                account=account,
                book_qty=snapshot.book_qty,
                account_dimension_sha256=snapshot.account_dimension_sha256,
                serials=tuple(serials),
                serial_sha256=snapshot.serial_snapshot_sha256,
            )
        )
    reconstructed.sort(key=lambda row: (row.scope.scope_no, str(row.account.id)))
    expected = task_service._snapshot_manifest_sha256(
        task=task,
        cutoff_cursor=task.cutoff_ledger_cursor,
        plans=plans,
        snapshots=tuple(reconstructed),
    )
    if expected != task.snapshot_manifest_sha256:
        _fail(
            "stocktake_count_snapshot_manifest_mismatch",
            "service_unavailable",
            "盘点截止快照清单已漂移",
        )


def _validate_task_round_and_freeze(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scope: FormalStocktakeScope,
    expected_freeze_mode: str,
    freeze: InventoryFreeze | None,
    now: datetime,
) -> None:
    if (
        task.task_type not in _NON_OPENING_TYPES
        or task.cutoff_at is None
        or task.cutoff_ledger_cursor is None
        or task.current_round_no != 1
        or round_row.round_no != 1
        or round_row.round_type != "initial"
        or _as_utc(task.cutoff_at) > now
        or _as_utc(round_row.started_at) > now
    ):
        _fail(
            "stocktake_count_state_invalid",
            "precondition_failed",
            "非期初盘点任务或初盘轮次状态无效",
        )
    if freeze is None or (
        freeze.scope_key != scope.scope_key
        or freeze.freeze_mode != expected_freeze_mode
        or freeze.status != "active"
        or freeze.valid_to is not None
        or _as_utc(freeze.valid_from) > now
    ):
        _fail(
            "stocktake_count_scope_not_frozen",
            "precondition_failed",
            "盘点范围没有与启动证据一致的活动冻结",
        )


def _require_new_submission_state(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    now: datetime,
) -> None:
    if (
        task.status != "counting"
        or round_row.status != "counting"
        or not scopes
        or task.current_round_no != round_row.round_no
        or round_row.round_no != 1
        or round_row.round_type != "initial"
        or task.submitted_at is not None
        or round_row.submitted_at is not None
        or round_row.submitted_by_user_id is not None
        or round_row.count_manifest_sha256 is not None
        or (task.deadline is not None and now >= _as_utc(task.deadline))
    ):
        _fail(
            "stocktake_count_state_invalid",
            "precondition_failed",
            "盘点任务或初盘轮次当前不可提交",
        )


def _authorize_exact_scope_actor(
    db: Session,
    actor: FormalPrincipal,
    task: FormalStocktakeTask,
    scope: FormalStocktakeScope,
) -> ScopeGrant:
    if actor.user_id != scope.assignee_user_id:
        _fail(
            "stocktake_count_not_assignee",
            "forbidden",
            "只有冻结范围指定执行人可以提交实盘",
        )
    location = db.scalar(
        _select_only_reference_statement(
            db,
            select(StockLocation).where(StockLocation.id == scope.location_id),
        )
        .execution_options(populate_existing=True)
    )
    if location is None or location.status != "active":
        _fail("stocktake_count_location_invalid", "precondition_failed", "盘点库位已失效")
    candidates: list[tuple[ScopeGrant, str, str, int]] = []
    if location.location_type == "personal":
        if scope.custodian_person_id_snapshot is None:
            _fail(
                "stocktake_count_personal_custodian_missing",
                "precondition_failed",
                "个人仓冻结范围缺少保管责任人快照",
            )
        for row in actor.assignments:
            if (
                row.role_code == "technician"
                and row.scope_type == "person"
                and actor.person_id == scope.custodian_person_id_snapshot
                and _same_uuid(row.scope_id, scope.custodian_person_id_snapshot)
            ):
                candidates.append(
                    (
                        row,
                        "person",
                        str(scope.custodian_person_id_snapshot),
                        0,
                    )
                )
            elif (
                task.task_type != "personal"
                and row.role_code == "provincial_manager"
                and row.scope_type == "organization"
                and _same_uuid(row.scope_id, scope.owner_org_id)
            ):
                candidates.append((row, "organization", str(scope.owner_org_id), 1))
            elif (
                task.task_type != "personal"
                and row.role_code == "admin"
                and row.scope_type == "national"
                and row.scope_id == "*"
            ):
                candidates.append((row, "organization", str(scope.owner_org_id), 2))
    else:
        for row in actor.assignments:
            if (
                row.role_code == "provincial_manager"
                and row.scope_type == "organization"
                and _same_uuid(row.scope_id, scope.owner_org_id)
            ):
                candidates.append((row, "organization", str(scope.owner_org_id), 0))
            elif (
                row.role_code == "admin"
                and row.scope_type == "national"
                and row.scope_id == "*"
            ):
                candidates.append((row, "organization", str(scope.owner_org_id), 1))
    candidates.sort(key=lambda item: (item[3], str(item[0].assignment_id)))
    for grant, target_type, target_id, _rank in candidates:
        if _grant_allows_count(
            db,
            actor,
            grant,
            target_scope_type=target_type,
            target_scope_id=target_id,
        ):
            return grant
    _fail(
        "stocktake_count_scope_forbidden",
        "forbidden",
        "当前正式授权未覆盖该盘点范围",
    )


def _grant_allows_count(
    db: Session,
    actor: FormalPrincipal,
    grant: ScopeGrant,
    *,
    target_scope_type: str,
    target_scope_id: str,
) -> bool:
    """Require both the whole principal and the selected grant to allow count."""

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
            "stocktake",
            "count",
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
        ) and selected.allows(
            db,
            "stocktake",
            "count",
            target_scope_type=target_scope_type,
            target_scope_id=target_scope_id,
        )
    except FormalAccessError:
        return False


def _lock_current_assignment(
    db: Session,
    actor: FormalPrincipal,
    grant: ScopeGrant,
    now: datetime,
    *,
    lock_rows: bool = True,
) -> RoleAssignment:
    statement = select(RoleAssignment).where(RoleAssignment.id == grant.assignment_id)
    if lock_rows:
        statement = statement.with_for_update()
    row = db.scalar(statement.execution_options(populate_existing=True))
    role = db.get(Role, row.role_id) if row is not None else None
    if (
        row is None
        or role is None
        or row.user_id != actor.user_id
        or row.status != "active"
        or row.revoked_at is not None
        or _as_utc(row.valid_from) > now
        or (row.valid_to is not None and now >= _as_utc(row.valid_to))
        or role.status != "active"
        or role.is_external
        or role.code != grant.role_code
        or row.scope_type != grant.scope_type
        or row.scope_id != grant.scope_id
    ):
        _fail(
            "stocktake_count_assignment_not_current",
            "precondition_failed",
            "盘点授权已变化，请重新读取后再提交",
        )
    return row


def _lock_snapshot_accounts(
    db: Session,
    snapshots: Sequence[StocktakeSnapshotLine],
) -> dict[uuid.UUID, StockAccount]:
    ids = tuple(sorted({row.stock_account_id for row in snapshots}, key=str))
    rows = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(StockAccount)
                .where(StockAccount.id.in_(ids))
                .order_by(StockAccount.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if ids else ()
    if len(rows) != len(ids):
        _fail(
            "stocktake_count_snapshot_account_missing",
            "service_unavailable",
            "截止快照引用的库存账户不完整",
        )
    return {row.id: row for row in rows}


def _lock_serials(
    db: Session,
    serial_ids: Sequence[uuid.UUID],
) -> dict[uuid.UUID, InventorySerial]:
    ids = tuple(sorted(set(serial_ids), key=str))
    rows = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(InventorySerial)
                .where(InventorySerial.id.in_(ids))
                .order_by(InventorySerial.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    ) if ids else ()
    if len(rows) != len(ids):
        _fail("stocktake_count_serial_not_found", "invalid_request", "一个或多个 SN 不存在")
    return {row.id: row for row in rows}


def _lock_evidence_files(
    db: Session,
    file_ids: Sequence[uuid.UUID],
    actor_user_id: str,
) -> tuple[FileObject, ...]:
    ids = tuple(sorted(set(file_ids), key=str))
    rows = tuple(
        db.scalars(
            select(FileObject)
            .where(FileObject.id.in_(ids))
            .order_by(FileObject.id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    ) if ids else ()
    if len(rows) != len(ids) or any(
        not formal_file_service.is_available_formal_file_for_purpose(
            row,
            purpose="stocktake_evidence",
            uploader_user_id=actor_user_id,
        )
        or not isinstance(row.sha256, str)
        or len(row.sha256) != 64
        or row.size_bytes < 0
        for row in rows
    ):
        _fail(
            "stocktake_count_evidence_file_invalid",
            "precondition_failed",
            "盘点附件必须是当前提交人已上传且可用的正式文件",
        )
    return rows


def _load_policy(
    db: Session,
    material_id: uuid.UUID,
    cutoff_at: datetime | None,
) -> MaterialInventoryPolicy:
    if cutoff_at is None:
        _fail("stocktake_count_cutoff_missing", "service_unavailable", "盘点截止时点缺失")
    rows = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(MaterialInventoryPolicy)
                .where(
                    MaterialInventoryPolicy.material_id == material_id,
                    MaterialInventoryPolicy.effective_from <= cutoff_at,
                    or_(
                        MaterialInventoryPolicy.effective_to.is_(None),
                        MaterialInventoryPolicy.effective_to > cutoff_at,
                    ),
                )
                .order_by(MaterialInventoryPolicy.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(rows) != 1:
        _fail(
            "stocktake_count_policy_ambiguous",
            "precondition_failed",
            "截止时点物料库存策略不唯一",
        )
    return rows[0]


def _validate_account_policy(
    account: StockAccount,
    policy: MaterialInventoryPolicy,
) -> None:
    if policy.tracking_mode in {"none", "serial"} and account.lot_id is not None:
        _fail("stocktake_count_account_lot_forbidden", "precondition_failed", "账户批次维度违反截止策略")
    if policy.tracking_mode in {"lot", "lot_and_serial"} and account.lot_id is None:
        _fail("stocktake_count_account_lot_required", "precondition_failed", "账户缺少截止策略要求的批次")


def _resolve_observation_material(
    db: Session,
    value: StocktakePhysicalObservationInput,
) -> FormalMaterial | None:
    candidates: tuple[FormalMaterial, ...]
    if value.material_identifier_type == "sku_code":
        candidates = tuple(
            db.scalars(
                _select_only_reference_statement(
                    db,
                    select(FormalMaterial)
                    .where(
                        FormalMaterial.sku_code == value.material_identifier_raw,
                        FormalMaterial.status == "active",
                    )
                    .order_by(FormalMaterial.id),
                )
                .execution_options(populate_existing=True)
            ).all()
        )
    elif value.material_identifier_type == "external_code":
        try:
            external_id = uuid.UUID(value.material_identifier_raw)
        except (ValueError, TypeError, AttributeError):
            candidates = ()
        else:
            candidates = tuple(
                db.scalars(
                    _select_only_reference_statement(
                        db,
                        select(FormalMaterial)
                        .where(
                            FormalMaterial.external_object_id == external_id,
                            FormalMaterial.status == "active",
                        )
                        .order_by(FormalMaterial.id),
                    )
                    .execution_options(populate_existing=True)
                ).all()
            )
    elif value.material_identifier_type == "qr_code":
        mappings = tuple(
            db.scalars(
                _select_only_reference_statement(
                    db,
                    select(QrCode)
                    .where(
                        QrCode.code == value.material_identifier_raw,
                        QrCode.object_type == "material",
                        QrCode.status == "active",
                    )
                    .order_by(QrCode.id),
                )
                .execution_options(populate_existing=True)
            ).all()
        )
        object_ids = tuple(sorted({row.object_id for row in mappings}, key=str))
        candidates = tuple(
            db.scalars(
                _select_only_reference_statement(
                    db,
                    select(FormalMaterial)
                    .where(
                        FormalMaterial.id.in_(object_ids),
                        FormalMaterial.status == "active",
                    )
                    .order_by(FormalMaterial.id),
                )
                .execution_options(populate_existing=True)
            ).all()
        ) if object_ids else ()
    else:
        # Unknown scanner input is matched across exact supported identifiers.
        # Zero matches remains pending; more than one distinct material fails.
        by_id: dict[uuid.UUID, FormalMaterial] = {}
        for row in db.scalars(
            _select_only_reference_statement(
                db,
                select(FormalMaterial)
                .where(
                    FormalMaterial.sku_code == value.material_identifier_raw,
                    FormalMaterial.status == "active",
                )
                .order_by(FormalMaterial.id),
            )
            .execution_options(populate_existing=True)
        ).all():
            by_id[row.id] = row
        try:
            external_id = uuid.UUID(value.material_identifier_raw)
        except (ValueError, TypeError, AttributeError):
            external_id = None
        if external_id is not None:
            for row in db.scalars(
                _select_only_reference_statement(
                    db,
                    select(FormalMaterial)
                    .where(
                        FormalMaterial.external_object_id == external_id,
                        FormalMaterial.status == "active",
                    )
                    .order_by(FormalMaterial.id),
                )
                .execution_options(populate_existing=True)
            ).all():
                by_id[row.id] = row
        mappings = tuple(
            db.scalars(
                _select_only_reference_statement(
                    db,
                    select(QrCode)
                    .where(
                        QrCode.code == value.material_identifier_raw,
                        QrCode.object_type == "material",
                        QrCode.status == "active",
                    )
                    .order_by(QrCode.id),
                )
                .execution_options(populate_existing=True)
            ).all()
        )
        object_ids = tuple(sorted({row.object_id for row in mappings}, key=str))
        if object_ids:
            for row in db.scalars(
                _select_only_reference_statement(
                    db,
                    select(FormalMaterial)
                    .where(
                        FormalMaterial.id.in_(object_ids),
                        FormalMaterial.status == "active",
                    )
                    .order_by(FormalMaterial.id),
                )
                .execution_options(populate_existing=True)
            ).all():
                by_id[row.id] = row
        candidates = tuple(by_id[key] for key in sorted(by_id, key=str))
    if len(candidates) > 1:
        _fail(
            "stocktake_count_material_identifier_ambiguous",
            "precondition_failed",
            "现场物料标识匹配多个正式物料，禁止猜测",
        )
    resolved = candidates[0] if candidates else None
    if value.material_id is not None:
        selected = db.scalar(
            _select_only_reference_statement(
                db,
                select(FormalMaterial).where(FormalMaterial.id == value.material_id),
            )
            .execution_options(populate_existing=True)
        )
        if selected is None or selected.status != "active":
            _fail("stocktake_count_material_invalid", "precondition_failed", "现场物料不是有效正式物料")
        if resolved is None or resolved.id != selected.id:
            _fail(
                "stocktake_count_material_identifier_mismatch",
                "invalid_request",
                "现场物料标识与所选正式物料不一致",
            )
        return selected
    return resolved


def _resolve_observation_lot(
    db: Session,
    material: FormalMaterial,
    policy: MaterialInventoryPolicy,
    value: StocktakePhysicalObservationInput,
) -> tuple[InventoryLot | None, bool]:
    requires_lot = policy.tracking_mode in {"lot", "lot_and_serial"}
    if not requires_lot:
        if value.lot_id is not None or value.lot_no_raw is not None:
            _fail("stocktake_count_observation_lot_forbidden", "invalid_request", "非批次物料不得携带批次")
        return None, True
    if value.lot_no_raw is None:
        _fail("stocktake_count_observation_lot_invalid", "invalid_request", "批次物料必须保留现场批次标识")
    matches = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(InventoryLot)
                .where(
                    InventoryLot.material_id == material.id,
                    InventoryLot.lot_no == value.lot_no_raw,
                )
                .order_by(InventoryLot.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(matches) > 1:
        _fail("stocktake_count_observation_lot_ambiguous", "precondition_failed", "现场批次匹配不唯一，禁止猜测")
    resolved = matches[0] if matches else None
    if value.lot_id is not None and (resolved is None or resolved.id != value.lot_id):
        _fail("stocktake_count_observation_lot_mismatch", "invalid_request", "现场批次与所选正式批次不一致")
    return resolved, resolved is not None


def _resolve_observation_serial(
    db: Session,
    material: FormalMaterial,
    policy: MaterialInventoryPolicy,
    lot: InventoryLot | None,
    value: StocktakePhysicalObservationInput,
) -> tuple[InventorySerial | None, bool]:
    requires_serial = policy.tracking_mode in {"serial", "lot_and_serial"}
    if not requires_serial:
        if any(
            item is not None
            for item in (value.serial_id, value.serial_no_raw, value.serial_identifier_type)
        ):
            _fail("stocktake_count_observation_serial_forbidden", "invalid_request", "非 SN 物料不得携带 SN")
        return None, True
    if value.serial_no_raw is None or value.serial_identifier_type is None:
        _fail("stocktake_count_observation_serial_invalid", "invalid_request", "SN 物料必须逐件保留现场 SN 标识")
    if value.counted_qty != Decimal("1"):
        _fail("stocktake_count_observation_serial_quantity", "invalid_request", "每条 SN 观察数量必须为 1")
    clauses = []
    if value.serial_identifier_type in {"serial_no", "unknown"}:
        clauses.append(InventorySerial.serial_no == value.serial_no_raw)
    if value.serial_identifier_type in {"qr_code", "unknown"}:
        clauses.append(InventorySerial.qr_code == value.serial_no_raw)
    direct_matches = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(InventorySerial)
                .where(or_(*clauses))
                .order_by(InventorySerial.id),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    mapped_matches: tuple[InventorySerial, ...] = ()
    if value.serial_identifier_type in {"qr_code", "unknown"}:
        mappings = tuple(
            db.scalars(
                _select_only_reference_statement(
                    db,
                    select(QrCode)
                    .where(
                        QrCode.code == value.serial_no_raw,
                        QrCode.object_type == "serial",
                        QrCode.status == "active",
                    )
                    .order_by(QrCode.id),
                )
                .execution_options(populate_existing=True)
            ).all()
        )
        mapped_ids = tuple(sorted({row.object_id for row in mappings}, key=str))
        if mapped_ids:
            mapped_matches = tuple(
                db.scalars(
                    _select_only_reference_statement(
                        db,
                        select(InventorySerial)
                        .where(InventorySerial.id.in_(mapped_ids))
                        .order_by(InventorySerial.id),
                    )
                    .execution_options(populate_existing=True)
                ).all()
            )
            if len(mapped_matches) != len(mapped_ids):
                _fail(
                    "stocktake_count_observation_serial_mismatch",
                    "precondition_failed",
                    "现场 SN 二维码映射引用了不存在的正式 SN",
                )
    unique = {row.id: row for row in (*direct_matches, *mapped_matches)}
    if len(unique) > 1:
        _fail("stocktake_count_observation_serial_ambiguous", "precondition_failed", "现场 SN 标识匹配多个序列号，禁止猜测")
    serial = next(iter(unique.values()), None)
    if value.serial_id is not None and (serial is None or serial.id != value.serial_id):
        _fail("stocktake_count_observation_serial_mismatch", "invalid_request", "现场 SN 与所选正式 SN 不一致")
    if serial is None:
        return None, False
    if (
        serial.lifecycle_status != "active"
        or serial.material_id != material.id
        or (lot is not None and serial.lot_id != lot.id)
    ):
        _fail("stocktake_count_observation_serial_mismatch", "invalid_request", "现场 SN 未唯一匹配物料和批次")
    return serial, True


def _validate_round_serial_uniqueness(
    db: Session,
    round_id: uuid.UUID,
    counts: Sequence[_PreparedCount],
    observations: Sequence[_PreparedObservation],
) -> None:
    command_ids = [serial.id for row in counts for serial in row.serials]
    command_ids.extend(row.serial.id for row in observations if row.serial is not None)
    if len(command_ids) != len(set(command_ids)):
        _fail("stocktake_count_serial_duplicate", "invalid_request", "同一 SN 在一轮盘点中只能出现一次")
    raw_serials = [
        row.value.serial_no_raw.casefold()
        for row in observations
        if row.value.serial_no_raw is not None
    ]
    if len(raw_serials) != len(set(raw_serials)):
        _fail("stocktake_count_serial_duplicate", "invalid_request", "同一现场 SN 标识只能出现一次")
    existing_line = (
        db.scalar(
            select(StocktakeCountSerial.serial_id)
            .where(
                StocktakeCountSerial.round_id == round_id,
                StocktakeCountSerial.serial_id.in_(tuple(command_ids)),
            )
            .limit(1)
        )
        if command_ids
        else None
    )
    existing_observation = (
        db.scalar(
            select(StocktakeCountObservation.serial_id)
            .where(
                StocktakeCountObservation.round_id == round_id,
                StocktakeCountObservation.serial_id.in_(tuple(command_ids)),
            )
            .limit(1)
        )
        if command_ids
        else None
    )
    existing_raw = (
        db.scalar(
            select(StocktakeCountObservation.id)
            .where(
                StocktakeCountObservation.round_id == round_id,
                func.lower(StocktakeCountObservation.serial_no_raw).in_(tuple(raw_serials)),
            )
            .limit(1)
        )
        if raw_serials
        else None
    )
    if existing_line is not None or existing_observation is not None or existing_raw is not None:
        _fail("stocktake_count_serial_duplicate", "conflict", "该 SN 已在本轮其他范围提交")


def _validate_replay(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scope: FormalStocktakeScope,
    actor: FormalPrincipal,
    completion: StocktakeScopeCountCompletion,
    request_hash: str,
    files: Sequence[FileObject],
    current_ledger_cursor: int,
) -> StocktakeInitialScopeCountResult:
    if (
        completion.task_id != task.id
        or completion.round_id != round_row.id
        or completion.scope_id != scope.id
        or completion.completed_by_user_id != actor.user_id
        or completion.completed_by_person_id != actor.person_id
        or completion.authorization_version != actor.authorization_version
        or completion.request_sha256 != request_hash
        or task.status not in {"counting", "submitted"}
        or round_row.status not in {"counting", "submitted"}
    ):
        _fail("stocktake_count_idempotency_conflict", "conflict", "幂等键已绑定不同的盘点提交")
    if (
        type(completion.count_ledger_cursor) is not int
        or task.cutoff_ledger_cursor is None
        or completion.count_ledger_cursor < task.cutoff_ledger_cursor
        or completion.count_ledger_cursor > current_ledger_cursor
    ):
        _fail(
            "stocktake_count_replay_cursor_invalid",
            "service_unavailable",
            "盘点提交缺少可信账本游标封印，禁止重放",
        )
    attachments = tuple(
        db.scalars(
            _select_only_reference_statement(
                db,
                select(DocumentAttachment)
                .where(
                    DocumentAttachment.document_type
                    == "stocktake_scope_count_completion",
                    DocumentAttachment.document_id == str(completion.id),
                    DocumentAttachment.attachment_type == "stocktake_evidence",
                    DocumentAttachment.status == "active",
                )
                .order_by(DocumentAttachment.file_id),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if tuple(row.file_id for row in attachments) != tuple(row.id for row in files):
        _fail("stocktake_count_replay_evidence_invalid", "service_unavailable", "盘点附件重放证据不完整")
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
    expected = _scope_evidence_manifest(
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
        expected != completion.evidence_manifest_sha256
        or completion.count_line_count != len(lines)
        or completion.observation_line_count != len(observations)
    ):
        _fail("stocktake_count_replay_evidence_invalid", "service_unavailable", "盘点提交重放证据无法重算")
    return StocktakeInitialScopeCountResult(
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        task_status=task.status,
        round_status=round_row.status,
        task_version=task.version,
        scope_completed=True,
        round_submitted=round_row.status == "submitted",
        evidence_file_count=len(files),
    )


def _scope_evidence_manifest(
    db: Session,
    *,
    completion_id: uuid.UUID,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    scope_id: uuid.UUID,
    lines: Sequence[StocktakeCountLine],
    observations: Sequence[StocktakeCountObservation],
    files: Sequence[FileObject],
    authorization_sha256: str,
    count_ledger_cursor: int,
) -> str:
    line_ids = tuple(row.id for row in lines)
    serials_by_line: dict[uuid.UUID, list[StocktakeCountSerial]] = defaultdict(list)
    if line_ids:
        for serial in db.scalars(
            select(StocktakeCountSerial)
            .where(StocktakeCountSerial.count_line_id.in_(line_ids))
            .order_by(StocktakeCountSerial.count_line_id, StocktakeCountSerial.serial_id)
        ).all():
            serials_by_line[serial.count_line_id].append(serial)
    return _sha256(
        {
            "authorization_sha256": authorization_sha256,
            "completion_id": str(completion_id),
            "count_ledger_cursor": count_ledger_cursor,
            "count_lines": [
                {
                    "count_line_id": str(row.id),
                    "count_method": row.count_method,
                    "counted_at": _timestamp(row.counted_at),
                    "counted_by_user_id": row.counted_by_user_id,
                    "counted_qty": _canonical_quantity(row.counted_qty),
                    "reason_code": row.reason_code,
                    "remark": row.remark,
                    "serials": [
                        {"result": serial.result, "serial_id": str(serial.serial_id)}
                        for serial in serials_by_line.get(row.id, ())
                    ],
                    "stock_account_id": str(row.stock_account_id),
                }
                for row in sorted(lines, key=lambda value: str(value.stock_account_id))
            ],
            "evidence_files": [
                {
                    "file_id": str(row.id),
                    "mime_type": row.mime_type,
                    "sha256": row.sha256,
                    "size_bytes": row.size_bytes,
                }
                for row in sorted(files, key=lambda value: str(value.id))
            ],
            "observations": [
                {
                    "availability_bucket": row.availability_bucket,
                    "condition_code": row.condition_code,
                    "count_method": row.count_method,
                    "counted_at": _timestamp(row.counted_at),
                    "counted_by_user_id": row.counted_by_user_id,
                    "counted_qty": _canonical_quantity(row.counted_qty),
                    "dimension_sha256": row.dimension_sha256,
                    "lot_id": str(row.lot_id) if row.lot_id is not None else None,
                    "material_id": (
                        str(row.material_id) if row.material_id is not None else None
                    ),
                    "material_identifier_raw": row.material_identifier_raw,
                    "material_identifier_type": row.material_identifier_type,
                    "observation_id": str(row.id),
                    "reason_code": row.reason_code,
                    "remark": row.remark,
                    "serial_id": str(row.serial_id) if row.serial_id is not None else None,
                    "serial_identifier_type": row.serial_identifier_type,
                    "serial_no_raw": row.serial_no_raw,
                    "verification_status": row.verification_status,
                }
                for row in sorted(observations, key=lambda value: value.dimension_sha256)
            ],
            "round_id": str(round_id),
            "schema": "cloud_oam.stocktake.scope_count_evidence.v2",
            "scope_id": str(scope_id),
            "task_id": str(task_id),
        }
    )


def _persisted_count_manifest(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    completions: Sequence[StocktakeScopeCountCompletion] | None = None,
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
    sealed_completions = tuple(
        sorted(
            completions
            if completions is not None
            else db.scalars(
                select(StocktakeScopeCountCompletion).where(
                    StocktakeScopeCountCompletion.task_id == task.id,
                    StocktakeScopeCountCompletion.round_id == round_row.id,
                )
            ).all(),
            key=lambda value: str(value.scope_id),
        )
    )
    return _sha256(
        {
            "count_boundaries": [
                {
                    "count_ledger_cursor": row.count_ledger_cursor,
                    "evidence_manifest_sha256": row.evidence_manifest_sha256,
                    "scope_id": str(row.scope_id),
                }
                for row in sealed_completions
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
                        {"result": serial.result, "serial_id": str(serial.serial_id)}
                        for serial in serials_by_line.get(row.id, ())
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
            "round_id": str(round_row.id),
            "schema": "cloud_oam.stocktake.initial_count_manifest.v2",
            "scope_manifest_sha256": task.scope_manifest_sha256,
            "snapshot_manifest_sha256": task.snapshot_manifest_sha256,
            "task_id": str(task.id),
        }
    )


def _round_manifest(
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    completions: Sequence[StocktakeScopeCountCompletion],
    sealing_completion_id: uuid.UUID,
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
            "round_id": str(round_id),
            "schema": "cloud_oam.stocktake.initial_round_manifest.v2",
            "sealing_completion_id": str(sealing_completion_id),
            "task_id": str(task_id),
        }
    )


def _validate_command(
    command: SubmitStocktakeInitialScopeCountCommand,
) -> SubmitStocktakeInitialScopeCountCommand:
    if not isinstance(command, SubmitStocktakeInitialScopeCountCommand):
        _fail("stocktake_count_command_invalid", "invalid_request", "盘点提交命令无效")
    task_id = _require_uuid("task_id", command.task_id)
    round_id = _require_uuid("round_id", command.round_id)
    scope_id = _require_uuid("scope_id", command.scope_id)
    if command.count_mode not in {"blind", "open"}:
        _fail("stocktake_count_mode_invalid", "invalid_request", "盘点模式必须为明盘或盲盘")
    if type(command.zero_confirmed) is not bool:
        _fail("stocktake_count_zero_confirmation_invalid", "invalid_request", "零确认值无效")
    if len(command.account_counts) > 10000 or len(command.physical_observations) > 10000:
        _fail("stocktake_count_too_many_lines", "invalid_request", "单范围盘点明细过多")
    if len(command.evidence_file_ids) > 50:
        _fail("stocktake_count_too_many_files", "invalid_request", "盘点附件数量超过上限")
    account_counts: list[StocktakeSnapshotCountInput] = []
    seen_accounts: set[uuid.UUID] = set()
    seen_serials: set[uuid.UUID] = set()
    for row in command.account_counts:
        if not isinstance(row, StocktakeSnapshotCountInput):
            _fail("stocktake_count_line_invalid", "invalid_request", "盘点账户明细无效")
        account_id = _require_uuid("stock_account_id", row.stock_account_id)
        if account_id in seen_accounts:
            _fail("stocktake_count_account_duplicate", "invalid_request", "同一截止账户只能提交一次")
        seen_accounts.add(account_id)
        quantity = _require_quantity(row.counted_qty, positive=False)
        confirmation = (
            _require_quantity(row.book_qty_confirmation, positive=False)
            if row.book_qty_confirmation is not None
            else None
        )
        if row.count_method not in _COUNT_METHODS:
            _fail("stocktake_count_method_invalid", "invalid_request", "盘点方式无效")
        serial_ids = tuple(_require_uuid("serial_id", value) for value in row.serial_ids)
        if len(serial_ids) != len(set(serial_ids)):
            _fail("stocktake_count_serial_duplicate", "invalid_request", "同一 SN 不得重复提交")
        if seen_serials.intersection(serial_ids):
            _fail("stocktake_count_serial_duplicate", "invalid_request", "同一 SN 不得跨账户重复提交")
        seen_serials.update(serial_ids)
        account_counts.append(
            replace(
                row,
                stock_account_id=account_id,
                counted_qty=quantity,
                serial_ids=serial_ids,
                book_qty_confirmation=confirmation,
                reason_code=_optional_text("reason_code", row.reason_code, 80),
                remark=_text("remark", row.remark, 10000, allow_empty=True),
            )
        )
    observations: list[StocktakePhysicalObservationInput] = []
    for row in command.physical_observations:
        if not isinstance(row, StocktakePhysicalObservationInput):
            _fail("stocktake_count_observation_invalid", "invalid_request", "现场观察明细无效")
        if row.condition_code not in _CONDITIONS or row.availability_bucket not in _BUCKETS:
            _fail("stocktake_count_observation_dimension_invalid", "invalid_request", "现场成色或库存状态无效")
        if row.material_identifier_type not in _MATERIAL_IDENTIFIER_TYPES:
            _fail("stocktake_count_material_identifier_type_invalid", "invalid_request", "现场物料标识类型无效")
        if row.serial_identifier_type is not None and row.serial_identifier_type not in _SERIAL_IDENTIFIER_TYPES:
            _fail("stocktake_count_serial_identifier_type_invalid", "invalid_request", "现场 SN 标识类型无效")
        if (row.serial_no_raw is None) != (row.serial_identifier_type is None):
            _fail(
                "stocktake_count_serial_identifier_pair_invalid",
                "invalid_request",
                "现场 SN 原始值和标识类型必须同时提供",
            )
        if row.count_method not in _COUNT_METHODS:
            _fail("stocktake_count_method_invalid", "invalid_request", "盘点方式无效")
        material_id = _optional_uuid("material_id", row.material_id)
        lot_id = _optional_uuid("lot_id", row.lot_id)
        serial_id = _optional_uuid("serial_id", row.serial_id)
        if serial_id is not None:
            if serial_id in seen_serials:
                _fail("stocktake_count_serial_duplicate", "invalid_request", "同一 SN 在一轮提交中只能出现一次")
            seen_serials.add(serial_id)
        observations.append(
            replace(
                row,
                material_id=material_id,
                material_identifier_raw=_text(
                    "material_identifier_raw", row.material_identifier_raw, 300
                ),
                counted_qty=_require_quantity(row.counted_qty, positive=True),
                lot_id=lot_id,
                lot_no_raw=_optional_text("lot_no_raw", row.lot_no_raw, 160),
                serial_id=serial_id,
                serial_no_raw=_optional_text("serial_no_raw", row.serial_no_raw, 200),
                reason_code=_optional_text("reason_code", row.reason_code, 80),
                remark=_text("remark", row.remark, 10000, allow_empty=True),
            )
        )
    evidence_ids = tuple(
        _require_uuid("evidence_file_id", row) for row in command.evidence_file_ids
    )
    if len(evidence_ids) != len(set(evidence_ids)):
        _fail("stocktake_count_evidence_file_duplicate", "invalid_request", "盘点附件不得重复")
    return SubmitStocktakeInitialScopeCountCommand(
        task_id=task_id,
        round_id=round_id,
        scope_id=scope_id,
        count_mode=command.count_mode,
        account_counts=tuple(account_counts),
        physical_observations=tuple(observations),
        evidence_file_ids=tuple(sorted(evidence_ids, key=str)),
        zero_confirmed=command.zero_confirmed,
    )


def _request_hmac(
    secret: bytes,
    actor: FormalPrincipal,
    command: SubmitStocktakeInitialScopeCountCommand,
) -> str:
    return _hmac_hex(
        secret,
        {
            "account_counts": [
                {
                    "book_qty_confirmation": (
                        _canonical_quantity(row.book_qty_confirmation)
                        if row.book_qty_confirmation is not None
                        else None
                    ),
                    "count_method": row.count_method,
                    "counted_qty": _canonical_quantity(row.counted_qty),
                    "reason_code": row.reason_code,
                    "remark": row.remark,
                    "serial_ids": [str(value) for value in row.serial_ids],
                    "stock_account_id": str(row.stock_account_id),
                }
                for row in command.account_counts
            ],
            "actor_authorization_version": actor.authorization_version,
            "actor_person_id": str(actor.person_id),
            "actor_user_id": actor.user_id,
            "count_mode": command.count_mode,
            "evidence_file_ids": [str(value) for value in command.evidence_file_ids],
            "physical_observations": [
                {
                    "availability_bucket": row.availability_bucket,
                    "condition_code": row.condition_code,
                    "count_method": row.count_method,
                    "counted_qty": _canonical_quantity(row.counted_qty),
                    "lot_id": str(row.lot_id) if row.lot_id is not None else None,
                    "lot_no_raw": row.lot_no_raw,
                    "material_id": (
                        str(row.material_id) if row.material_id is not None else None
                    ),
                    "material_identifier_raw": row.material_identifier_raw,
                    "material_identifier_type": row.material_identifier_type,
                    "reason_code": row.reason_code,
                    "remark": row.remark,
                    "serial_id": str(row.serial_id) if row.serial_id is not None else None,
                    "serial_identifier_type": row.serial_identifier_type,
                    "serial_no_raw": row.serial_no_raw,
                }
                for row in command.physical_observations
            ],
            "round_id": str(command.round_id),
            "schema": "cloud_oam.stocktake.initial_scope_count_request.v1",
            "scope_id": str(command.scope_id),
            "task_id": str(command.task_id),
            "zero_confirmed": command.zero_confirmed,
        },
    )


def _idempotency_hmac(secret: bytes, actor_user_id: str, path: str, raw_key: str) -> str:
    return _hmac_hex(
        secret,
        {
            "actor_user_id": actor_user_id,
            "idempotency_key": raw_key,
            "method": "POST",
            "path": path,
            "schema": "cloud_oam.stocktake.initial_scope_count_idempotency.v1",
        },
    )


def _authorization_sha256(
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    grant: ScopeGrant,
    now: datetime,
) -> str:
    return _sha256(
        {
            "assignment_id": str(assignment.id),
            "authorization_version": actor.authorization_version,
            "completed_at": _timestamp(now),
            "person_id": str(actor.person_id),
            "role_code": grant.role_code,
            "schema": "cloud_oam.stocktake.scope_count_authorization.v1",
            "scope_id": grant.scope_id,
            "scope_type": grant.scope_type,
            "user_id": actor.user_id,
        }
    )


def _observation_dimension_sha256(
    scope: FormalStocktakeScope,
    value: StocktakePhysicalObservationInput,
    *,
    material: FormalMaterial | None,
    lot: InventoryLot | None,
    serial: InventorySerial | None,
    verification_status: str,
) -> str:
    return _sha256(
        {
            "availability_bucket": value.availability_bucket,
            "condition_code": value.condition_code,
            "custodian_person_id": (
                str(scope.custodian_person_id_snapshot)
                if scope.custodian_person_id_snapshot is not None
                else None
            ),
            "location_id": str(scope.location_id),
            "lot_id": str(lot.id) if lot is not None else None,
            "lot_no_raw": value.lot_no_raw,
            "material_id": str(material.id) if material is not None else None,
            "material_identifier_raw": value.material_identifier_raw,
            "material_identifier_type": value.material_identifier_type,
            "owner_org_id": str(scope.owner_org_id),
            "schema": "cloud_oam.stocktake.physical_observation_dimension.v1",
            "serial_id": str(serial.id) if serial is not None else None,
            "serial_identifier_type": value.serial_identifier_type,
            "serial_no_raw": value.serial_no_raw,
            "verification_status": verification_status,
        }
    )


def _snapshot_serial_ids(snapshot: StocktakeSnapshotLine) -> frozenset[uuid.UUID]:
    values: set[uuid.UUID] = set()
    for document in snapshot.serial_snapshot_jsonb:
        if not isinstance(document, dict) or not isinstance(document.get("serial_id"), str):
            _fail("stocktake_count_snapshot_serial_invalid", "service_unavailable", "截止快照 SN 证据无效")
        try:
            value = uuid.UUID(document["serial_id"])
        except (ValueError, TypeError, AttributeError):
            _fail("stocktake_count_snapshot_serial_invalid", "service_unavailable", "截止快照 SN 证据无效")
        if value in values:
            _fail("stocktake_count_snapshot_serial_duplicate", "service_unavailable", "截止快照包含重复 SN")
        values.add(value)
    return frozenset(values)


def _require_account_in_scope(account: StockAccount, scope: FormalStocktakeScope) -> None:
    if (
        account.owner_org_id != scope.owner_org_id
        or account.location_id != scope.location_id
        or (
            account.custodian_person_id is not None
            and account.custodian_person_id != scope.custodian_person_id_snapshot
        )
        or (scope.material_id is not None and account.material_id != scope.material_id)
        or (scope.condition_code is not None and account.condition_code != scope.condition_code)
        or (
            scope.availability_bucket is not None
            and account.availability_bucket != scope.availability_bucket
        )
    ):
        _fail("stocktake_count_account_outside_scope", "forbidden", "盘点账户不在冻结范围内")


def _account_matches_observation(
    account: StockAccount,
    scope: FormalStocktakeScope,
    value: StocktakePhysicalObservationInput,
    material: FormalMaterial,
    lot: InventoryLot | None,
) -> bool:
    return (
        account.owner_org_id == scope.owner_org_id
        and account.location_id == scope.location_id
        and (
            account.custodian_person_id is None
            or account.custodian_person_id == scope.custodian_person_id_snapshot
        )
        and account.material_id == material.id
        and account.condition_code == value.condition_code
        and account.availability_bucket == value.availability_bucket
        and account.lot_id == (lot.id if lot is not None else None)
    )


def _scope_has_partial_evidence(
    db: Session,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    scope_id: uuid.UUID,
) -> bool:
    return any(
        db.scalar(select(func.count()).select_from(model).where(*conditions))
        for model, conditions in (
            (
                StocktakeCountLine,
                (
                    StocktakeCountLine.task_id == task_id,
                    StocktakeCountLine.round_id == round_id,
                    StocktakeCountLine.scope_id == scope_id,
                ),
            ),
            (
                StocktakeCountObservation,
                (
                    StocktakeCountObservation.task_id == task_id,
                    StocktakeCountObservation.round_id == round_id,
                    StocktakeCountObservation.scope_id == scope_id,
                ),
            ),
        )
    )


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail("formal_principal_required", "forbidden", "盘点提交必须使用正式权限主体")
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
        or not isinstance(actor.authorization_version, int)
        or actor.authorization_version <= 0
    ):
        _fail("stocktake_count_actor_inactive", "forbidden", "当前账号或人员状态不允许提交盘点")
    return actor


def _require_current_actor(
    db: Session,
    supplied: FormalPrincipal,
    now: datetime,
) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError:
        _fail("stocktake_count_actor_not_current", "forbidden", "正式权限上下文已失效")
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail(
            "stocktake_count_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取后再提交",
        )
    return current


def _validate_quantity(
    value: Decimal,
    policy: MaterialInventoryPolicy,
    *,
    positive: bool,
) -> None:
    if (
        _decimal_scale(value) > policy.quantity_scale
        or (not policy.allow_fraction and value != value.to_integral_value())
        or (positive and value <= _ZERO)
        or (not positive and value < _ZERO)
    ):
        _fail("stocktake_count_quantity_policy_invalid", "invalid_request", "盘点数量不符合截止物料策略")


def _require_quantity(value: object, *, positive: bool) -> Decimal:
    if isinstance(value, bool) or not isinstance(value, Decimal):
        _fail("stocktake_count_quantity_invalid", "invalid_request", "盘点数量必须使用 Decimal")
    try:
        if not value.is_finite() or _decimal_scale(value) > 3:
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        _fail("stocktake_count_quantity_invalid", "invalid_request", "盘点数量必须是最多三位小数的有限值")
    if (positive and value <= _ZERO) or (not positive and value < _ZERO):
        _fail("stocktake_count_quantity_invalid", "invalid_request", "盘点数量范围无效")
    return value


def _quantity_sum(values: Sequence[Decimal]) -> Decimal:
    total = sum(values, start=_ZERO)
    if not total.is_finite() or total > Decimal("999999999999999.999"):
        _fail("stocktake_count_quantity_overflow", "invalid_request", "盘点数量合计超出范围")
    return total


def _require_uuid(field: str, value: object) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail("stocktake_count_uuid_invalid", "invalid_request", f"{field} 必须是非零 UUID")
    return value


def _optional_uuid(field: str, value: object | None) -> uuid.UUID | None:
    return None if value is None else _require_uuid(field, value)


def _text(field: str, value: object, limit: int, *, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or value != value.strip() or len(value) > limit:
        _fail("stocktake_count_text_invalid", "invalid_request", f"{field} 文本无效")
    if not allow_empty and not value:
        _fail("stocktake_count_text_invalid", "invalid_request", f"{field} 不能为空")
    return value


def _optional_text(field: str, value: object | None, limit: int) -> str | None:
    return None if value is None else _text(field, value, limit)


def _require_idempotency_key(value: object) -> str:
    if not isinstance(value, str) or _PRINTABLE.fullmatch(value) is None:
        _fail("stocktake_count_idempotency_key_invalid", "invalid_request", "幂等键格式无效")
    return value


def _require_hmac_secret(value: bytes | str) -> bytes:
    if isinstance(value, str):
        value = value.encode("utf-8")
    if (
        not isinstance(value, bytes)
        or len(value) < 32
        or any(marker in value.lower() for marker in (item.encode() for item in _PLACEHOLDERS))
    ):
        _fail("stocktake_count_hmac_secret_invalid", "service_unavailable", "盘点幂等 HMAC 密钥未安全配置")
    return value


def _require_trace_request_id(value: object) -> str:
    if not isinstance(value, str) or _SAFE_TRACE.fullmatch(value) is None:
        _fail("stocktake_count_trace_id_invalid", "invalid_request", "请求追踪标识格式无效")
    return value


def _child_hmac(secret: bytes, key_hash: str, dimension_hash: str) -> str:
    return hmac.new(
        secret,
        f"cloud_oam.stocktake.count.child.v1\0{key_hash}\0{dimension_hash}".encode(),
        hashlib.sha256,
    ).hexdigest()


def _hmac_hex(secret: bytes, document: Mapping[str, object]) -> str:
    return hmac.new(secret, _canonical_bytes(document), hashlib.sha256).hexdigest()


def _sha256(document: Mapping[str, object]) -> str:
    return hashlib.sha256(_canonical_bytes(document)).hexdigest()


def _canonical_bytes(document: Mapping[str, object]) -> bytes:
    return json.dumps(
        document,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")


def _canonical_quantity(value: Decimal | None) -> str | None:
    return None if value is None else format(value.quantize(Decimal("0.001")), "f")


def _timestamp(value: datetime | None) -> str | None:
    if value is None:
        return None
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decimal_scale(value: Decimal) -> int:
    return max(0, -value.as_tuple().exponent)


def _event_key(kind: str, value: uuid.UUID) -> str:
    return f"stocktake-count:{kind}:{value}"


def _request_reference(raw: str) -> str:
    return "stocktake-count:" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _lock_coordinate(namespace: str, value: str) -> int:
    raw = hashlib.sha256(f"{namespace}\0{value}".encode("utf-8")).digest()[:8]
    coordinate = int.from_bytes(raw, byteorder="big", signed=False)
    return coordinate - (1 << 64) if coordinate >= (1 << 63) else coordinate


def _lock_current_ledger_cursor(db: Session) -> int:
    """Seal the current committed inventory cursor under the writer lock."""

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
    return head.next_cursor - 1


def _take_advisory_locks(db: Session, coordinates: Sequence[int]) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for coordinate in sorted(set(coordinates)):
        db.execute(text("SELECT pg_advisory_xact_lock(:coordinate)"), {"coordinate": coordinate})


def _select_only_reference_statement(db: Session, statement):
    """Use direct row locks locally; PostgreSQL relies on owner helpers."""

    if db.get_bind().dialect.name != "postgresql":
        return statement.with_for_update()
    return statement


def _database_now(db: Session) -> datetime:
    value = db.scalar(select(func.current_timestamp()))
    if not isinstance(value, datetime):
        _fail("stocktake_count_database_clock_invalid", "service_unavailable", "数据库时钟不可用")
    return _as_utc(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_uuid(left: object, right: object) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (ValueError, TypeError, AttributeError):
        return False


def _fail(code: str, category: str, message: str) -> None:
    raise StocktakeCountError(code, category, message)


__all__ = [
    "StocktakeCountError",
    "StocktakeInitialScopeCountResult",
    "StocktakePhysicalObservationInput",
    "StocktakeSnapshotCountInput",
    "SubmitStocktakeInitialScopeCountCommand",
    "submit_stocktake_initial_scope_count",
]
