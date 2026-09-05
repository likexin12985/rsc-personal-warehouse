"""Internal V1.0 opening-stocktake batch starter.

This domain service is called by the formal opening HTTP adapter.  It
establishes the atomic evidence boundary for an opening stocktake: one reviewed
OAM read-only control snapshot, exact physical scopes, an inventory-ledger
cutoff, book/SN snapshots, active freezes, and the initial counting round.  It
never creates an inventory account, balance, movement, transaction, or opening
establishment.

The caller owns the surrounding transaction.  The service flushes but never
commits.  PostgreSQL transaction advisory locks serialize the idempotency key,
task number, and active regional opening batch before the inventory ledger head
is locked.  The remaining lock order matches the formal posting boundary:
ledger head -> stock accounts -> locations/materials -> balances ->
serials/positions -> audit head.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
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
    lock_formal_principal_graph,
    load_formal_principal,
)
from ..foundation_models import (
    AuditEvent,
    ExternalObject,
    ExternalObjectVersion,
    Organization,
    OutboxEvent,
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
    InventoryLot,
    InventoryMovement,
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
    InventoryOpeningEstablishment,
    StocktakeControlSnapshotLine,
    StocktakeRound,
    StocktakeSnapshotLine,
)
from .audit_chain import (
    AuditChainError,
    append_audit_event,
    lock_audit_chain_head,
    verify_audit_event_in_stream,
)
from .postgresql_lock_graph import (
    lock_inventory_serial_graph,
    lock_opening_control_import,
    lock_opening_stocktake_start_reference,
)


INVENTORY_LEDGER_HEAD_ID: Final[uuid.UUID] = uuid.UUID(
    "40000000-0000-4000-8000-000000000001"
)
INVENTORY_STREAM_KEY: Final[str] = "inventory"
OPENING_CONTROL_ENTITY_TYPE: Final[str] = "oam_inventory_control"
OPENING_TASK_TYPE: Final[str] = "opening"
OPENING_TASK_STATUS: Final[str] = "counting"
_ZERO: Final[Decimal] = Decimal("0.000")
_MAX_QUANTITY: Final[Decimal] = Decimal("1000000000000000")
_PRINTABLE = re.compile(r"^[\x21-\x7e]+$", re.ASCII)
_SAFE_REFERENCE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/@+-]*$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")

_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class OpeningStocktakeError(RuntimeError):
    """Stable, database-detail-free failure for the internal boundary."""

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
class OpeningStocktakeScopeInput:
    owner_org_id: uuid.UUID
    location_id: uuid.UUID
    assignee_user_id: str
    freeze_mode: str = "hard"


@dataclass(frozen=True, slots=True)
class OpeningControlLineInput:
    sync_inbox_event_id: uuid.UUID
    external_object_version_id: uuid.UUID
    external_business_key: str
    material_id: uuid.UUID | None
    condition_code: str | None
    control_qty: Decimal
    mapping_status: str
    source_updated_at: datetime | None
    payload_sha256: str
    mapping_note: str = ""


@dataclass(frozen=True, slots=True)
class StartOpeningStocktakeCommand:
    task_no: str
    region_org_id: uuid.UUID
    control_source_system_id: uuid.UUID
    control_sync_run_id: uuid.UUID
    control_sync_scope_key: str
    scopes: tuple[OpeningStocktakeScopeInput, ...]
    control_lines: tuple[OpeningControlLineInput, ...]
    blind_count: bool = True
    deadline: datetime | None = None
    note: str = ""


@dataclass(frozen=True, slots=True)
class OpeningStocktakeStartResult:
    task_id: uuid.UUID
    task_no: str
    status: str
    cutoff_ledger_cursor: int
    initial_round_id: uuid.UUID
    scope_count: int
    snapshot_line_count: int
    control_line_count: int
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _PreparedScope:
    scope_no: int
    input: OpeningStocktakeScopeInput
    scope_id: uuid.UUID
    scope_key: str
    scope_sha256: str
    location: StockLocation
    owner: Organization
    custodian_person_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class _QualifiedOpeningScope:
    """Reference eligibility only; no task/scope identity or inventory facts."""

    owner: Organization
    location: StockLocation
    custodian_person_id: uuid.UUID | None
    manager_grant: ScopeGrant


@dataclass(frozen=True, slots=True)
class _PreparedControlLine:
    line_no: int
    input: OpeningControlLineInput
    external_object_version: ExternalObjectVersion


@dataclass(frozen=True, slots=True)
class _PreparedSnapshotLine:
    scope: _PreparedScope
    account: StockAccount
    book_qty: Decimal
    account_dimension_sha256: str
    serial_snapshot: list[dict[str, object]]
    serial_snapshot_sha256: str


@dataclass(frozen=True, slots=True)
class _SnapshotReferenceSignature:
    """Exact reference/projection set pinned before the audit-head lock."""

    account_ids: tuple[uuid.UUID, ...]
    balance_account_ids: tuple[uuid.UUID, ...]
    policy_ids: tuple[uuid.UUID, ...]
    lot_ids: tuple[uuid.UUID, ...]
    serial_position_ids: tuple[uuid.UUID, ...]


def start_opening_stocktake(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: StartOpeningStocktakeCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeStartResult:
    """Start one opening-stocktake batch without committing it.

    Every database operation is wrapped at this public boundary so driver SQL,
    bind parameters and trigger details cannot escape through the future API
    layer.  Business validation errors retain their stable public contract.
    """

    failure: OpeningStocktakeError | None = None
    try:
        return _start_opening_stocktake_impl(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except OpeningStocktakeError:
        raise
    except AuditChainError:
        failure = OpeningStocktakeError(
            "opening_stocktake_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，期初盘点未启动",
        )
    except IntegrityError:
        failure = OpeningStocktakeError(
            "opening_stocktake_concurrent_conflict",
            "conflict",
            "期初盘点启动发生并发冲突，请回滚并重新读取后再试",
        )
    except DBAPIError as exc:
        if _is_database_guard_rejection(exc):
            failure = OpeningStocktakeError(
                "opening_stocktake_database_guard_rejected",
                "precondition_failed",
                "数据库安全约束拒绝了期初盘点启动，请回滚并重新读取",
            )
        else:
            failure = OpeningStocktakeError(
                "opening_stocktake_database_unavailable",
                "service_unavailable",
                "数据库暂时不可用，期初盘点未启动，请回滚并重新读取",
            )
    if failure is not None:
        raise failure from None
    raise AssertionError("unreachable opening stocktake boundary")


def _start_opening_stocktake_impl(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: StartOpeningStocktakeCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeStartResult:
    """Execute the internal start contract; the public wrapper owns errors."""

    supplied_actor = _validate_supplied_actor(actor)
    checked_command = _validate_command(command)
    checked_idempotency = _require_idempotency_key(idempotency_key)
    checked_request_id = _require_request_id(request_id)

    storage_key = _storage_hash(checked_idempotency)
    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("idempotency", storage_key),
            _advisory_coordinate("opening-region", str(checked_command.region_org_id)),
            _advisory_coordinate("task-number", checked_command.task_no),
        ),
    )

    locked_replay = _lock_existing_replay_anchors(db, storage_key)

    # Fail unauthorized or already-expired requests before they can hold the
    # shared source/ledger locks.  This first pass is deliberately read-only;
    # the exact principal graph is locked and re-proved below before any fact
    # is written.
    preflight_at = _database_now(db)
    preflight_actor = _require_current_actor(
        db,
        supplied_actor,
        now=preflight_at,
    )
    _authorize_batch_manager(
        db,
        preflight_actor,
        checked_command.region_org_id,
    )

    principal_user_ids = (
        actor.user_id,
        *(scope.assignee_user_id for scope in checked_command.scopes),
    )
    if locked_replay is not None:
        # Replay owns an existing task/round and never enters the inventory
        # writer graph.  Keeping it task -> principal -> audit avoids taking
        # the global ledger head merely to return an immutable result.
        lock_formal_principal_graph(db, principal_user_ids)
        now = _database_now(db)
        current_actor = _require_current_actor(db, supplied_actor, now=now)
        manager_grant = _authorize_batch_manager(
            db,
            current_actor,
            checked_command.region_org_id,
        )
        _lock_selected_grant(db, current_actor, manager_grant, now)
        replay = _load_replay(
            db,
            actor=current_actor,
            command=checked_command,
            idempotency_key_hash=storage_key,
            now=now,
            locked_replay=locked_replay,
        )
        if replay is None:
            _fail(
                "opening_idempotency_record_invalid",
                "service_unavailable",
                "期初盘点幂等记录无法重放",
            )
        return replace(replay, replayed=True)

    _validate_deadline(checked_command.deadline, preflight_at)
    _require_unused_business_keys(db, checked_command, storage_key)

    # New inventory facts use one global row-lock order.  Source evidence is
    # pinned first, then the ledger cursor, then RBAC and all opening reference
    # rows.  In particular, no principal row may be held while waiting for the
    # ledger: finalize/close already use ledger -> task -> principal.
    lock_opening_control_import(
        db,
        checked_command.control_source_system_id,
        checked_command.control_sync_run_id,
    )
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
    cutoff_cursor = head.next_cursor - 1

    lock_formal_principal_graph(db, principal_user_ids)
    lock_probe_at = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now=lock_probe_at)
    manager_grant = _authorize_batch_manager(
        db, current_actor, checked_command.region_org_id
    )
    _lock_selected_grant(db, current_actor, manager_grant, lock_probe_at)
    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now=now)
    manager_grant = _authorize_batch_manager(
        db, current_actor, checked_command.region_org_id
    )
    _lock_selected_grant(db, current_actor, manager_grant, now)
    _validate_deadline(checked_command.deadline, now)
    _require_unused_business_keys(db, checked_command, storage_key)
    prepared_control, control_manifest = _prepare_control_evidence(
        db, checked_command, now=now, lock_rows=True
    )

    lock_opening_stocktake_start_reference(
        db,
        checked_command.region_org_id,
        tuple(scope.owner_org_id for scope in checked_command.scopes),
        tuple(scope.location_id for scope in checked_command.scopes),
        tuple(
            row.input.material_id
            for row in prepared_control
            if row.input.material_id is not None
        ),
        now,
    )

    prepared_scopes = _prepare_scopes(
        db,
        actor=current_actor,
        command=checked_command,
        now=now,
    )
    locked_scope_manifest = _scope_manifest_hash(
        checked_command.region_org_id, prepared_scopes
    )
    prepared_snapshots, locked_snapshot_references = _prepare_snapshots(
        db,
        prepared_scopes,
        cutoff_ledger_cursor=cutoff_cursor,
        cutoff_at=now,
        extra_material_ids=tuple(
            row.input.material_id
            for row in prepared_control
            if row.input.material_id is not None
        ),
    )
    _require_no_existing_scope_fact_or_freeze(db, prepared_scopes)

    # This is the last shared row used by the atomic write.  Lock it before
    # sampling the final clock so an audit-writer wait cannot outlive the
    # selected role assignment or any other time-sensitive authorization.
    lock_audit_chain_head(db, stream_key=INVENTORY_STREAM_KEY)

    # Location/custody/account/assignee locks above may have waited.  Discard
    # the preliminary documents, sample the database clock only after all
    # critical rows are locked, then rebuild every time-sensitive input at the
    # one immutable cutoff used for task, freeze, round and audit facts.
    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now=now)
    manager_grant = _authorize_batch_manager(
        db, current_actor, checked_command.region_org_id
    )
    _lock_selected_grant(
        db,
        current_actor,
        manager_grant,
        now,
        lock_rows=False,
    )
    _validate_deadline(checked_command.deadline, now)
    _require_unused_business_keys(db, checked_command, storage_key)
    prepared_control, refreshed_control_manifest = _prepare_control_evidence(
        db,
        checked_command,
        now=now,
        lock_rows=False,
    )
    if refreshed_control_manifest != control_manifest:
        _fail(
            "control_manifest_changed_after_lock",
            "conflict",
            "OAM 控制快照在锁定后发生变化，请重新读取",
        )
    control_manifest = refreshed_control_manifest
    head = db.scalar(
        select(InventoryLedgerHead)
        .where(
            InventoryLedgerHead.id == INVENTORY_LEDGER_HEAD_ID,
            InventoryLedgerHead.stream_key == INVENTORY_STREAM_KEY,
        )
        .execution_options(populate_existing=True)
    )
    if head is None or not isinstance(head.next_cursor, int) or head.next_cursor <= 0:
        _fail(
            "inventory_ledger_head_unavailable",
            "service_unavailable",
            "库存账本游标未正确初始化",
        )
    cutoff_cursor = head.next_cursor - 1
    prepared_scopes = _prepare_scopes(
        db,
        actor=current_actor,
        command=checked_command,
        now=now,
        lock_rows=False,
    )
    scope_manifest = _scope_manifest_hash(
        checked_command.region_org_id,
        prepared_scopes,
    )
    if scope_manifest != locked_scope_manifest:
        _fail(
            "opening_scope_changed_after_lock",
            "conflict",
            "期初盘点范围责任在锁定后发生变化，请重新读取",
        )
    prepared_snapshots, _snapshot_references = _prepare_snapshots(
        db,
        prepared_scopes,
        cutoff_ledger_cursor=cutoff_cursor,
        cutoff_at=now,
        extra_material_ids=tuple(
            row.input.material_id
            for row in prepared_control
            if row.input.material_id is not None
        ),
        lock_rows=False,
        expected_references=locked_snapshot_references,
    )
    # The audit head is already the final shared lock.  This last pass must not
    # acquire a newly appeared freeze row after it; a committed competing row
    # is rejected by the plain reread, while an uncommitted insert is still
    # caught by the database uniqueness/guard boundary when our facts flush.
    _require_no_existing_scope_fact_or_freeze(
        db, prepared_scopes, lock_freezes=False
    )
    snapshot_manifest = _snapshot_manifest_hash(
        cutoff_ledger_cursor=cutoff_cursor,
        scopes=prepared_scopes,
        snapshots=prepared_snapshots,
    )

    return _write_start_facts(
        db,
        actor=current_actor,
        command=checked_command,
        now=now,
        cutoff_cursor=cutoff_cursor,
        idempotency_key_hash=storage_key,
        request_reference=_request_reference(checked_request_id),
        prepared_scopes=prepared_scopes,
        prepared_control=prepared_control,
        prepared_snapshots=prepared_snapshots,
        scope_manifest=scope_manifest,
        control_manifest=control_manifest,
        snapshot_manifest=snapshot_manifest,
    )


def opening_control_projection_payload(
    *,
    external_business_key: str,
    region_org_id: uuid.UUID,
    material_id: uuid.UUID | None,
    condition_code: str | None,
    control_qty: Decimal,
    mapping_status: str,
    mapping_note: str,
) -> dict[str, object]:
    """Return the exact payload contract accepted from the read-only mirror."""

    return {
        "condition_code": condition_code,
        "control_qty": _canonical_decimal(control_qty),
        "external_business_key": external_business_key,
        "mapping_note": mapping_note,
        "mapping_status": mapping_status,
        "material_id": str(material_id) if material_id is not None else None,
        "region_org_id": str(region_org_id),
    }


def canonical_opening_manifest_sha256(document: Mapping[str, object]) -> str:
    """Hash one JSON-safe canonical opening-stocktake document."""

    return hashlib.sha256(_canonical_json_bytes(document)).hexdigest()


def opening_control_batch_body_sha256(
    *, sequence: int, events: Sequence[Mapping[str, object]]
) -> str:
    """Hash one already-normalized OAM control inbox batch."""

    normalized = sorted(events, key=lambda row: str(row["event_sort_key"]))
    return canonical_opening_manifest_sha256(
        {
            "events": [
                {
                    "external_event_id": row["external_event_id"],
                    "external_id": row["external_id"],
                    "payload_sha256": row["payload_sha256"],
                    "source_updated_at": row["source_updated_at"],
                    "source_version": row["source_version"],
                }
                for row in normalized
            ],
            "schema": "cloud_oam.opening_stocktake.control_batch.v1",
            "sequence": sequence,
        }
    )


def opening_control_manifest_sha256(
    *,
    source_system_id: uuid.UUID,
    sync_run_id: uuid.UUID,
    sync_scope_key: str,
    region_org_id: uuid.UUID,
    lines: Sequence[OpeningControlLineInput],
) -> str:
    """Hash the exact normalized rows accepted as an opening control total."""

    return canonical_opening_manifest_sha256(
        {
            "lines": [
                {
                    "condition_code": row.condition_code,
                    "control_qty": _canonical_decimal(row.control_qty),
                    "external_business_key": row.external_business_key,
                    "external_object_version_id": str(
                        row.external_object_version_id
                    ),
                    "line_no": line_no,
                    "mapping_note": row.mapping_note,
                    "mapping_status": row.mapping_status,
                    "material_id": (
                        str(row.material_id) if row.material_id is not None else None
                    ),
                    "payload_sha256": row.payload_sha256,
                    "source_updated_at": (
                        _canonical_timestamp(row.source_updated_at)
                        if row.source_updated_at is not None
                        else None
                    ),
                }
                for line_no, row in enumerate(lines, start=1)
            ],
            "region_org_id": str(region_org_id),
            "schema": "cloud_oam.opening_stocktake.control_manifest.v1",
            "source_system_id": str(source_system_id),
            "sync_run_id": str(sync_run_id),
            "sync_scope_key": sync_scope_key,
        }
    )


def _write_start_facts(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: StartOpeningStocktakeCommand,
    now: datetime,
    cutoff_cursor: int,
    idempotency_key_hash: str,
    request_reference: str,
    prepared_scopes: tuple[_PreparedScope, ...],
    prepared_control: tuple[_PreparedControlLine, ...],
    prepared_snapshots: tuple[_PreparedSnapshotLine, ...],
    scope_manifest: str,
    control_manifest: str,
    snapshot_manifest: str,
) -> OpeningStocktakeStartResult:
    task_id = uuid.uuid4()
    round_id = uuid.uuid4()
    sync_run = db.get(SyncRun, command.control_sync_run_id)
    if sync_run is None or sync_run.completed_at is None:
        _fail(
            "control_sync_run_lost_before_write",
            "service_unavailable",
            "OAM 控制同步批次在写入期初证据前失效",
        )
    control_snapshot_at = _as_utc(sync_run.completed_at)

    task = FormalStocktakeTask(
        id=task_id,
        task_no=command.task_no,
        task_type=OPENING_TASK_TYPE,
        region_org_id=command.region_org_id,
        status=OPENING_TASK_STATUS,
        blind_count=command.blind_count,
        cutoff_ledger_cursor=cutoff_cursor,
        cutoff_at=now,
        scope_manifest_sha256=scope_manifest,
        snapshot_manifest_sha256=snapshot_manifest,
        control_source_system_id=command.control_source_system_id,
        control_sync_run_id=command.control_sync_run_id,
        control_snapshot_at=control_snapshot_at,
        control_manifest_sha256=control_manifest,
        current_round_no=1,
        created_by_user_id=actor.user_id,
        deadline=command.deadline,
        issued_at=now,
        frozen_at=now,
        submitted_at=None,
        posted_at=None,
        closed_at=None,
        cancelled_at=None,
        version=0,
        note=command.note,
        created_at=now,
        updated_at=now,
    )
    db.add(task)
    db.flush()

    for scope in prepared_scopes:
        db.add(
            FormalStocktakeScope(
                id=scope.scope_id,
                task_id=task_id,
                scope_no=scope.scope_no,
                scope_mode="location_all",
                location_id=scope.input.location_id,
                owner_org_id=scope.input.owner_org_id,
                custodian_person_id_snapshot=scope.custodian_person_id,
                assignee_user_id=scope.input.assignee_user_id,
                material_id=None,
                condition_code=None,
                availability_bucket=None,
                scope_key=scope.scope_key,
                scope_sha256=scope.scope_sha256,
                created_at=now,
            )
        )
    db.flush()

    for line in prepared_control:
        value = line.input
        db.add(
            StocktakeControlSnapshotLine(
                id=uuid.uuid4(),
                task_id=task_id,
                line_no=line.line_no,
                external_business_key=value.external_business_key,
                external_object_version_id=value.external_object_version_id,
                material_id=value.material_id,
                condition_code=value.condition_code,
                control_qty=value.control_qty,
                mapping_status=value.mapping_status,
                source_updated_at=value.source_updated_at,
                payload_sha256=value.payload_sha256,
                mapping_note=value.mapping_note,
                created_at=now,
            )
        )

    for scope in prepared_scopes:
        db.add(
            InventoryFreeze(
                id=uuid.uuid4(),
                task_id=task_id,
                stocktake_scope_id=scope.scope_id,
                scope_key=scope.scope_key,
                freeze_mode=scope.input.freeze_mode,
                status="active",
                valid_from=now,
                valid_to=None,
                created_by_user_id=actor.user_id,
                released_by_user_id=None,
                release_reason="",
                version=0,
                created_at=now,
                updated_at=now,
            )
        )

    for line in prepared_snapshots:
        db.add(
            StocktakeSnapshotLine(
                id=uuid.uuid4(),
                task_id=task_id,
                scope_id=line.scope.scope_id,
                stock_account_id=line.account.id,
                book_qty=line.book_qty,
                ledger_cursor=cutoff_cursor,
                account_dimension_sha256=line.account_dimension_sha256,
                serial_snapshot_jsonb=line.serial_snapshot,
                serial_snapshot_sha256=line.serial_snapshot_sha256,
                serial_count=len(line.serial_snapshot),
                created_at=now,
            )
        )

    # Migration 0010 seals scope/control/freeze/snapshot inserts once the first
    # counting round exists.  Do not rely on ORM table ordering for that safety
    # boundary: persist every start-evidence row before making round 1 visible.
    db.flush()

    db.add(
        StocktakeRound(
            id=round_id,
            task_id=task_id,
            round_no=1,
            round_type="initial",
            status="counting",
            submitted_by_user_id=None,
            started_at=now,
            submitted_at=None,
            count_manifest_sha256=None,
            idempotency_key_hash=idempotency_key_hash,
            created_at=now,
            updated_at=now,
        )
    )

    transitions = (
        (None, "draft", "opening_stocktake_created"),
        ("draft", "issued", "opening_stocktake_issued"),
        ("issued", "frozen", "opening_stocktake_frozen"),
        ("frozen", "counting", "opening_stocktake_initial_round_started"),
    )
    for sequence, (from_status, to_status, reason) in enumerate(transitions, start=1):
        db.add(
            StateTransitionEvent(
                aggregate_type="stocktake_task",
                aggregate_id=str(task_id),
                from_status=from_status,
                to_status=to_status,
                reason=reason,
                actor_id=actor.user_id,
                idempotency_key=_evidence_key("state", task_id, str(sequence)),
                occurred_at=now,
                metadata_jsonb={
                    "control_manifest_sha256": control_manifest,
                    "cutoff_ledger_cursor": cutoff_cursor,
                    "request_reference": request_reference,
                    "scope_manifest_sha256": scope_manifest,
                    "snapshot_manifest_sha256": snapshot_manifest,
                },
                created_at=now,
            )
        )

    db.add(
        OutboxEvent(
            event_type="stocktake.opening.started",
            aggregate_type="stocktake_task",
            aggregate_id=str(task_id),
            payload_jsonb={
                "control_line_count": len(prepared_control),
                "cutoff_ledger_cursor": cutoff_cursor,
                "initial_round_id": str(round_id),
                "region_org_id": str(command.region_org_id),
                "scope_count": len(prepared_scopes),
                "snapshot_line_count": len(prepared_snapshots),
                "task_id": str(task_id),
                "task_no": command.task_no,
            },
            status="pending",
            attempts=0,
            idempotency_key=_evidence_key("outbox", task_id, "started"),
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
        action="stocktake.opening.started",
        aggregate_type="stocktake_task",
        aggregate_id=str(task_id),
        before_jsonb=None,
        after_jsonb={
            "control_line_count": len(prepared_control),
            "control_manifest_sha256": control_manifest,
            "cutoff_ledger_cursor": cutoff_cursor,
            "initial_round_id": str(round_id),
            "region_org_id": str(command.region_org_id),
            "scope_count": len(prepared_scopes),
            "scope_manifest_sha256": scope_manifest,
            "snapshot_line_count": len(prepared_snapshots),
            "snapshot_manifest_sha256": snapshot_manifest,
            "status": OPENING_TASK_STATUS,
            "task_no": command.task_no,
        },
        request_id=request_reference,
        occurred_at=now,
        created_at=now,
    )
    db.flush()

    return OpeningStocktakeStartResult(
        task_id=task_id,
        task_no=command.task_no,
        status=OPENING_TASK_STATUS,
        cutoff_ledger_cursor=cutoff_cursor,
        initial_round_id=round_id,
        scope_count=len(prepared_scopes),
        snapshot_line_count=len(prepared_snapshots),
        control_line_count=len(prepared_control),
    )


def _prepare_control_evidence(
    db: Session,
    command: StartOpeningStocktakeCommand,
    *,
    now: datetime,
    lock_rows: bool,
    historical_at: datetime | None = None,
) -> tuple[tuple[_PreparedControlLine, ...], str]:
    historical_at_utc = _as_utc(historical_at) if historical_at is not None else None
    lock_locally = lock_rows and db.get_bind().dialect.name != "postgresql"
    source_statement = select(SourceSystem).where(
        SourceSystem.id == command.control_source_system_id
    )
    if lock_locally:
        source_statement = source_statement.with_for_update()
    source = db.scalar(source_statement)
    if source is None:
        _fail("control_source_not_found", "not_found", "OAM 控制来源不存在")
    if (
        source.code.strip().casefold() != "oam"
        or source.mode not in {"read_only", "mirror_only"}
        # A new opening task may only bind an enabled source.  Historical
        # replay/count validation must instead trust the immutable source and
        # version evidence at ``historical_at``: disabling a connector later
        # cannot invalidate an already captured stocktake snapshot.
        or (historical_at_utc is None and source.enabled is not True)
    ):
        _fail(
            "control_source_not_read_only",
            "precondition_failed",
            "期初盘点只能使用启用的 OAM 只读镜像来源",
        )

    sync_statement = select(SyncRun).where(
        SyncRun.id == command.control_sync_run_id
    )
    if lock_locally:
        sync_statement = sync_statement.with_for_update()
    sync_run = db.scalar(sync_statement)
    if sync_run is None:
        _fail("control_sync_run_not_found", "not_found", "OAM 控制同步批次不存在")
    expected_scope_key = _control_scope_key(command.region_org_id)
    if (
        sync_run.source_system_id != source.id
        or sync_run.status != "completed"
        or sync_run.completed_at is None
        or _as_utc(sync_run.completed_at) > now
        or sync_run.scope_key != command.control_sync_scope_key
        or sync_run.scope_key != expected_scope_key
        or not _is_sha256(sync_run.manifest_sha256)
    ):
        _fail(
            "control_sync_run_invalid",
            "precondition_failed",
            "OAM 控制同步批次未完成、来源/范围错误或清单无效",
        )

    batch_statement = (
        select(SyncBatch)
        .where(
            SyncBatch.run_id == sync_run.id,
            SyncBatch.entity_type == OPENING_CONTROL_ENTITY_TYPE,
        )
        .order_by(SyncBatch.sequence, SyncBatch.id)
    )
    if lock_locally:
        batch_statement = batch_statement.with_for_update()
    batch_rows = db.scalars(batch_statement).all()
    if not batch_rows:
        _fail(
            "control_sync_batches_missing",
            "precondition_failed",
            "OAM 控制同步批次没有已落库数据批",
        )
    event_statement = (
        select(SyncInboxEvent, SyncBatch)
        .join(SyncBatch, SyncBatch.id == SyncInboxEvent.batch_id)
        .where(
            SyncBatch.run_id == sync_run.id,
            SyncInboxEvent.entity_type == OPENING_CONTROL_ENTITY_TYPE,
        )
        .order_by(SyncInboxEvent.id)
    )
    if lock_locally:
        event_statement = event_statement.with_for_update()
    event_rows = db.execute(event_statement).all()
    if len(event_rows) != len(command.control_lines):
        _fail(
            "control_sync_rows_incomplete",
            "precondition_failed",
            "传入控制行未完整覆盖该同步范围",
        )
    events_by_id = {event.id: (event, batch) for event, batch in event_rows}
    if len(events_by_id) != len(event_rows):
        _fail(
            "control_sync_rows_ambiguous",
            "service_unavailable",
            "OAM 控制同步行身份不唯一",
        )
    events_by_batch: dict[uuid.UUID, list[SyncInboxEvent]] = {
        batch.id: [] for batch in batch_rows
    }
    for event, batch in event_rows:
        events_by_batch.setdefault(batch.id, []).append(event)
    for batch in batch_rows:
        batch_events = sorted(
            events_by_batch.get(batch.id, []), key=lambda row: str(row.id)
        )
        expected_batch_hash = opening_control_batch_body_sha256(
            sequence=batch.sequence,
            events=[
                {
                    "event_sort_key": str(event.id),
                    "external_event_id": event.external_event_id,
                    "external_id": event.external_id,
                    "payload_sha256": event.payload_sha256,
                    "source_updated_at": (
                        _canonical_timestamp(_as_utc(event.source_updated_at))
                        if event.source_updated_at is not None
                        else None
                    ),
                    "source_version": event.source_version,
                }
                for event in batch_events
            ],
        )
        if (
            batch.status != "applied"
            or batch.record_count != len(batch_events)
            or batch.body_sha256 != expected_batch_hash
        ):
            _fail(
                "control_sync_batch_invalid",
                "precondition_failed",
                "OAM 控制同步数据批数量、状态或 body hash 不一致",
            )

    prepared: list[_PreparedControlLine] = []
    seen_business_keys: set[str] = set()
    seen_versions: set[uuid.UUID] = set()
    for line_no, value in enumerate(command.control_lines, start=1):
        pair = events_by_id.get(value.sync_inbox_event_id)
        if pair is None:
            _fail(
                "control_line_not_in_sync_run",
                "precondition_failed",
                "控制行不属于指定的已完成同步批次",
            )
        event, batch = pair
        if (
            batch.run_id != sync_run.id
            or batch.entity_type != OPENING_CONTROL_ENTITY_TYPE
            or batch.status != "applied"
            or event.source_system_id != source.id
            or event.status != "applied"
        ):
            _fail(
                "control_line_not_applied",
                "precondition_failed",
                "控制行尚未通过只读镜像校验并落库",
            )
        if value.external_business_key in seen_business_keys:
            _fail("control_business_key_duplicate", "invalid_request", "控制业务键重复")
        if value.external_object_version_id in seen_versions:
            _fail("control_version_duplicate", "invalid_request", "控制版本重复")
        seen_business_keys.add(value.external_business_key)
        seen_versions.add(value.external_object_version_id)

        version_statement = select(ExternalObjectVersion).where(
            ExternalObjectVersion.id == value.external_object_version_id
        )
        if lock_locally:
            version_statement = version_statement.with_for_update()
        version = db.scalar(version_statement)
        if version is None:
            _fail("control_version_not_found", "not_found", "控制行外部版本不存在")
        object_statement = select(ExternalObject).where(
            ExternalObject.id == version.external_object_id
        )
        if lock_locally:
            object_statement = object_statement.with_for_update()
        external_object = db.scalar(object_statement)
        if external_object is None:
            _fail(
                "control_external_object_not_found",
                "service_unavailable",
                "控制行外部对象证据缺失",
            )
        payload = opening_control_projection_payload(
            external_business_key=value.external_business_key,
            region_org_id=command.region_org_id,
            material_id=value.material_id,
            condition_code=value.condition_code,
            control_qty=value.control_qty,
            mapping_status=value.mapping_status,
            mapping_note=value.mapping_note,
        )
        payload_hash = canonical_opening_manifest_sha256(payload)
        version_valid_from = _as_utc(version.valid_from)
        version_valid_to = _as_optional_utc(version.valid_to)
        object_deleted_at = _as_optional_utc(external_object.deleted_at)
        if historical_at_utc is None:
            version_binding_valid = (
                external_object.deleted_at is None
                and external_object.current_version_id == version.id
                and version.is_current is True
                and version_valid_to is None
                and version_valid_from <= _as_utc(sync_run.completed_at)
            )
        else:
            # An idempotent replay verifies the immutable version that was valid
            # at the persisted control-snapshot time.  A later OAM mirror run is
            # expected to move ``current_version_id``, close ``valid_to`` or mark
            # the object deleted; none of those later facts may invalidate the
            # already-started opening stocktake.
            version_binding_valid = (
                version_valid_from <= historical_at_utc
                and (
                    version_valid_to is None
                    or historical_at_utc < version_valid_to
                )
                and (
                    object_deleted_at is None
                    or historical_at_utc < object_deleted_at
                )
            )
        if (
            external_object.source_system_id != source.id
            or external_object.entity_type != OPENING_CONTROL_ENTITY_TYPE
            or external_object.external_id != value.external_business_key
            or not version_binding_valid
            or event.external_id != external_object.external_id
            or event.source_version != version.source_version
            or _as_optional_utc(event.source_updated_at)
            != _as_optional_utc(version.source_updated_at)
            or _as_optional_utc(event.source_updated_at) != value.source_updated_at
            or event.payload_jsonb != payload
            or version.payload_jsonb != payload
            or event.payload_sha256 != payload_hash
            or version.payload_sha256 != payload_hash
            or value.payload_sha256 != payload_hash
        ):
            _fail(
                "control_line_evidence_mismatch",
                "precondition_failed",
                "控制行与同源同步事件、当前外部版本或不可变 payload 不一致",
            )
        prepared.append(
            _PreparedControlLine(
                line_no=line_no,
                input=value,
                external_object_version=version,
            )
        )

    prepared_tuple = tuple(prepared)
    manifest = _control_manifest_hash(
        source_system_id=source.id,
        sync_run=sync_run,
        region_org_id=command.region_org_id,
        lines=prepared_tuple,
    )
    if manifest != sync_run.manifest_sha256:
        _fail(
            "control_manifest_mismatch",
            "precondition_failed",
            "控制行规范清单与已完成同步批次 hash 不一致",
        )
    return prepared_tuple, manifest


def _prepare_scopes(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: StartOpeningStocktakeCommand,
    now: datetime,
    lock_rows: bool = True,
) -> tuple[_PreparedScope, ...]:
    is_postgresql = db.get_bind().dialect.name == "postgresql"
    region_statement = select(Organization).where(
        Organization.id == command.region_org_id
    )
    if lock_rows and not is_postgresql:
        region_statement = region_statement.with_for_update()
    region = db.scalar(
        region_statement.execution_options(populate_existing=True)
    )
    if region is None or region.status != "active" or region.org_type != "region_company":
        _fail(
            "region_organization_invalid",
            "precondition_failed",
            "期初盘点区域必须是启用的区域公司",
        )

    owner_ids = tuple(
        sorted({scope.owner_org_id for scope in command.scopes}, key=str)
    )
    owner_statement = (
        select(Organization)
        .where(Organization.id.in_(owner_ids))
        .order_by(Organization.id)
    )
    if lock_rows and not is_postgresql:
        owner_statement = owner_statement.with_for_update()
    owners = {
        row.id: row
        for row in db.scalars(
            owner_statement.execution_options(populate_existing=True)
        ).all()
    }
    if len(owners) != len(owner_ids) or any(
        row.status != "active" or row.org_type != "region_company"
        for row in owners.values()
    ):
        _fail(
            "asset_owner_invalid",
            "precondition_failed",
            "资产所有组织必须是启用的区域公司",
        )
    if any(
        not _organization_descends_from(
            db,
            owner_id,
            command.region_org_id,
            lock_rows=lock_rows,
        )
        for owner_id in owner_ids
    ):
        _fail(
            "asset_owner_outside_region",
            "forbidden",
            "资产所有组织必须位于期初盘点任务区域的有效组织树内",
        )

    location_ids = tuple(sorted({scope.location_id for scope in command.scopes}, key=str))
    location_statement = (
        select(StockLocation)
        .where(StockLocation.id.in_(location_ids))
        .order_by(StockLocation.id)
    )
    if lock_rows and not is_postgresql:
        location_statement = location_statement.with_for_update()
    locations = {
        row.id: row
        for row in db.scalars(
            location_statement.execution_options(populate_existing=True)
        ).all()
    }
    if len(locations) != len(location_ids):
        _fail("stock_location_not_found", "not_found", "一个或多个盘点库位不存在")

    custody_statement = (
        select(CustodyAssignment)
        .where(
            CustodyAssignment.location_id.in_(location_ids),
            CustodyAssignment.valid_from <= now,
            or_(
                CustodyAssignment.valid_to.is_(None),
                CustodyAssignment.valid_to > now,
            ),
        )
        .order_by(CustodyAssignment.location_id, CustodyAssignment.id)
    )
    if lock_rows and not is_postgresql:
        custody_statement = custody_statement.with_for_update()
    custody_rows = db.scalars(
        custody_statement.execution_options(populate_existing=True)
    ).all()
    custodies_by_location: dict[uuid.UUID, list[CustodyAssignment]] = {
        location_id: [] for location_id in location_ids
    }
    for custody in custody_rows:
        custodies_by_location[custody.location_id].append(custody)

    prepared: list[_PreparedScope] = []
    for scope_no, value in enumerate(command.scopes, start=1):
        location = locations[value.location_id]
        qualified = _qualify_opening_scope(
            db,
            actor=actor,
            region_org_id=command.region_org_id,
            owner=owners[value.owner_org_id],
            location=location,
            effective_custodies=custodies_by_location[location.id],
            now=now,
            lock_rows=lock_rows,
        )
        custodian_person_id = qualified.custodian_person_id
        if location.location_type == "personal":
            assert custodian_person_id is not None
            _authorize_personal_assignee(
                db,
                assignee_user_id=value.assignee_user_id,
                custodian_person_id=custodian_person_id,
                now=now,
                lock_rows=lock_rows,
            )
        else:
            _authorize_regional_assignee(
                db,
                assignee_user_id=value.assignee_user_id,
                region_org_id=command.region_org_id,
                owner_org_id=value.owner_org_id,
                now=now,
                lock_rows=lock_rows,
            )

        scope_key = _scope_key(value.owner_org_id, value.location_id)
        scope_hash = _scope_line_hash(
            owner_org_id=value.owner_org_id,
            location_id=value.location_id,
            custodian_person_id=custodian_person_id,
            assignee_user_id=value.assignee_user_id,
        )
        prepared.append(
            _PreparedScope(
                scope_no=scope_no,
                input=value,
                scope_id=uuid.uuid4(),
                scope_key=scope_key,
                scope_sha256=scope_hash,
                location=location,
                owner=owners[value.owner_org_id],
                custodian_person_id=custodian_person_id,
            )
        )
    return tuple(prepared)


def _qualify_opening_scope(
    db: Session,
    *,
    actor: FormalPrincipal,
    region_org_id: uuid.UUID,
    owner: Organization,
    location: StockLocation,
    effective_custodies: Sequence[CustodyAssignment],
    now: datetime,
    lock_rows: bool = False,
) -> _QualifiedOpeningScope:
    """Share the write-side reference rules with the read-only preparation list.

    Callers fresh-load the selected rows and current custody set.  The write
    service retains its batched preflight, lock graph, executor checks and
    final validation.  A directory call explicitly disables the existing row
    locks and must revalidate its complete read snapshot before returning.
    This helper neither proves control evidence nor generates business IDs.
    """

    if owner.status != "active" or owner.org_type != "region_company":
        _fail(
            "asset_owner_invalid", "precondition_failed",
            "资产所有组织必须是启用的区域公司",
        )
    if not _organization_descends_from(
        db, owner.id, region_org_id, lock_rows=lock_rows,
    ):
        _fail(
            "asset_owner_outside_region", "forbidden",
            "资产所有组织必须位于期初盘点任务区域的有效组织树内",
        )
    if location.status != "active" or location.location_type not in {"region", "personal"}:
        _fail(
            "stock_location_invalid", "precondition_failed",
            "期初盘点仅接受启用的区域仓或个人仓库位",
        )
    _require_location_in_region_tree(db, location, region_org_id, lock_rows=lock_rows)
    manager_grant = _authorize_scope_dimensions(
        db, actor=actor, task_region_org_id=region_org_id,
        owner_org_id=owner.id, location_owner_org_id=location.owner_org_id,
    )
    _lock_selected_grant(db, actor, manager_grant, now, lock_rows=lock_rows)
    if len(effective_custodies) > 1:
        _fail(
            "custody_assignment_ambiguous", "service_unavailable",
            "库位当前保管责任记录不唯一",
        )
    custody = effective_custodies[0] if effective_custodies else None
    if custody is not None and (
        custody.location_id != location.id
        or _as_utc(custody.valid_from) > now
        or (custody.valid_to is not None and now >= _as_utc(custody.valid_to))
    ):
        _fail(
            "custody_assignment_not_current", "precondition_failed",
            "库位保管责任与本次范围或校验时点不一致",
        )
    if location.location_type == "personal":
        if (
            custody is None or location.custodian_person_id is None
            or custody.custodian_person_id != location.custodian_person_id
        ):
            _fail(
                "personal_custody_invalid", "precondition_failed",
                "个人仓必须存在唯一且与库位一致的当前保管责任",
            )
    elif location.custodian_person_id is not None and (
        custody is None or custody.custodian_person_id != location.custodian_person_id
    ):
        _fail(
            "regional_custody_invalid", "precondition_failed",
            "区域仓库位保管人字段与当前保管责任不一致",
        )
    return _QualifiedOpeningScope(
        owner=owner, location=location,
        custodian_person_id=custody.custodian_person_id if custody else None,
        manager_grant=manager_grant,
    )


def _prepare_snapshots(
    db: Session,
    scopes: tuple[_PreparedScope, ...],
    *,
    cutoff_ledger_cursor: int,
    cutoff_at: datetime,
    extra_material_ids: tuple[uuid.UUID, ...],
    lock_rows: bool = True,
    expected_references: _SnapshotReferenceSignature | None = None,
) -> tuple[tuple[_PreparedSnapshotLine, ...], _SnapshotReferenceSignature]:
    if lock_rows == (expected_references is not None):
        _fail(
            "snapshot_reference_lock_mode_invalid",
            "service_unavailable",
            "截止快照引用锁模式无效",
        )
    predicates = tuple(
        and_(
            StockAccount.owner_org_id == scope.input.owner_org_id,
            StockAccount.location_id == scope.input.location_id,
        )
        for scope in scopes
    )
    is_postgresql = db.get_bind().dialect.name == "postgresql"
    account_statement = (
        select(StockAccount)
        .where(or_(*predicates))
        .order_by(StockAccount.id)
    )
    if lock_rows and not is_postgresql:
        account_statement = account_statement.with_for_update()
    accounts = db.scalars(
        account_statement.execution_options(populate_existing=True)
    ).all()
    scope_by_pair = {
        (scope.input.owner_org_id, scope.input.location_id): scope for scope in scopes
    }
    for account in accounts:
        scope = scope_by_pair.get((account.owner_org_id, account.location_id))
        if scope is None:
            _fail(
                "snapshot_account_scope_mismatch",
                "service_unavailable",
                "账面账户无法唯一绑定盘点范围",
            )
        if scope.location.location_type == "personal" and (
            account.custodian_person_id != scope.custodian_person_id
        ):
            _fail(
                "personal_account_custodian_mismatch",
                "precondition_failed",
                "个人仓库存账户保管人与当前责任人不一致",
            )

    location_ids = tuple(sorted({row.input.location_id for row in scopes}, key=str))
    location_statement = (
        select(StockLocation)
        .where(StockLocation.id.in_(location_ids))
        .order_by(StockLocation.id)
    )
    if lock_rows and not is_postgresql:
        location_statement = location_statement.with_for_update()
    locked_locations = db.scalars(
        location_statement.execution_options(populate_existing=True)
    ).all()
    if len(locked_locations) != len(location_ids) or any(
        row.status != "active" for row in locked_locations
    ):
        _fail(
            "stock_location_changed",
            "conflict",
            "盘点库位在截止快照期间发生变化",
        )

    material_ids = tuple(
        sorted(
            {account.material_id for account in accounts}.union(extra_material_ids),
            key=str,
        )
    )
    if material_ids:
        material_statement = (
            select(FormalMaterial)
            .where(FormalMaterial.id.in_(material_ids))
            .order_by(FormalMaterial.id)
        )
        if lock_rows and not is_postgresql:
            material_statement = material_statement.with_for_update()
        materials = db.scalars(
            material_statement.execution_options(populate_existing=True)
        ).all()
        if len(materials) != len(material_ids) or any(
            material.status != "active" for material in materials
        ):
            _fail(
                "control_or_snapshot_material_invalid",
                "precondition_failed",
                "控制行或盘点范围包含不存在或停用的物料",
            )

    policies: dict[uuid.UUID, MaterialInventoryPolicy] = {}
    for material_id in material_ids:
        policy_statement = (
            select(MaterialInventoryPolicy)
            .where(
                MaterialInventoryPolicy.material_id == material_id,
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
            )
        )
        if lock_rows and not is_postgresql:
            policy_statement = policy_statement.with_for_update()
        rows = db.scalars(
            policy_statement.execution_options(populate_existing=True)
        ).all()
        if len(rows) != 1 or rows[0].tracking_mode not in {
            "none",
            "lot",
            "serial",
            "lot_and_serial",
        }:
            _fail(
                "snapshot_inventory_policy_invalid",
                "precondition_failed",
                "截止时点每个控制或账面物料必须且只能命中一条有效库存策略",
            )
        policies[material_id] = rows[0]

    lot_ids = tuple(
        sorted({account.lot_id for account in accounts if account.lot_id is not None}, key=str)
    )
    lots: dict[uuid.UUID, InventoryLot] = {}
    if lot_ids:
        lot_statement = (
            select(InventoryLot)
            .where(InventoryLot.id.in_(lot_ids))
            .order_by(InventoryLot.id)
        )
        if lock_rows and not is_postgresql:
            lot_statement = lot_statement.with_for_update()
        lots = {
            row.id: row
            for row in db.scalars(
                lot_statement.execution_options(populate_existing=True)
            ).all()
        }
        if len(lots) != len(lot_ids):
            _fail(
                "snapshot_lot_invalid",
                "precondition_failed",
                "盘点账户引用的批次不存在",
            )
    for account in accounts:
        policy = policies[account.material_id]
        if policy.tracking_mode in {"none", "serial"} and account.lot_id is not None:
            _fail(
                "snapshot_account_lot_forbidden",
                "precondition_failed",
                "非批次追踪物料的盘点账户不得绑定批次",
            )
        if policy.tracking_mode in {"lot", "lot_and_serial"}:
            lot = lots.get(account.lot_id) if account.lot_id is not None else None
            if lot is None or lot.material_id != account.material_id:
                _fail(
                    "snapshot_account_lot_required",
                    "precondition_failed",
                    "批次追踪物料的盘点账户必须绑定同物料有效批次",
                )

    account_ids = tuple(account.id for account in accounts)
    serials_by_account: dict[uuid.UUID, list[dict[str, object]]] = {
        account_id: [] for account_id in account_ids
    }
    serial_ids: tuple[uuid.UUID, ...] = ()
    if account_ids:
        serial_statement = (
            select(SerialCurrentPosition.serial_id)
            .where(SerialCurrentPosition.stock_account_id.in_(account_ids))
            .order_by(SerialCurrentPosition.serial_id)
        )
        if lock_rows and not is_postgresql:
            serial_statement = serial_statement.with_for_update()
        serial_ids = tuple(
            db.scalars(
                serial_statement.execution_options(populate_existing=True)
            ).all()
        )
        if lock_rows and is_postgresql:
            # The ledger head already prevents a legitimate inventory writer
            # from changing this candidate set.  The owner helper locks the
            # immutable SN masters and mutable current positions before any
            # balance row is touched, preserving the global serial->balance
            # order.  The exact reread rejects an implementation that bypasses
            # the ledger-head contract and expands the set concurrently.
            lock_inventory_serial_graph(db, serial_ids)
            locked_serial_ids = tuple(
                db.scalars(
                    serial_statement.execution_options(populate_existing=True)
                ).all()
            )
            if locked_serial_ids != serial_ids:
                _fail(
                    "snapshot_serial_graph_expanded_during_lock",
                    "conflict",
                    "截止快照 SN 归属集合在锁定期间发生变化，请重新读取",
                )
            serial_ids = locked_serial_ids

    balance_by_account: dict[uuid.UUID, StockBalance] = {}
    if account_ids:
        balance_statement = (
            select(StockBalance)
            .where(StockBalance.stock_account_id.in_(account_ids))
            .order_by(StockBalance.stock_account_id)
        )
        if lock_rows:
            balance_statement = balance_statement.with_for_update()
        balances = db.scalars(
            balance_statement.execution_options(populate_existing=True)
        ).all()
        balance_by_account = {row.stock_account_id: row for row in balances}
        if any(
            row.quantity is None
            or not row.quantity.is_finite()
            or row.quantity < _ZERO
            or row.ledger_cursor < 0
            or row.ledger_cursor > cutoff_ledger_cursor
            for row in balances
        ):
            _fail(
                "snapshot_balance_invalid",
                "service_unavailable",
                "库存余额投影与截止游标不一致",
            )
        if any(row.quantity != _ZERO for row in balances):
            _fail(
                "opening_scope_formal_ledger_not_empty",
                "precondition_failed",
                "期初盘点范围已存在非零正式余额，必须先完成兼容迁移决策",
            )

        historical_movement = db.scalar(
            select(InventoryMovement.id)
            .where(
                or_(
                    InventoryMovement.from_account_id.in_(account_ids),
                    InventoryMovement.to_account_id.in_(account_ids),
                ),
            )
            .limit(1)
        )
        if historical_movement is not None:
            _fail(
                "opening_scope_formal_ledger_not_empty",
                "precondition_failed",
                "期初盘点范围已存在正式库存流水，禁止重复建立期初",
            )

    reference_signature = _SnapshotReferenceSignature(
        account_ids=tuple(account_ids),
        balance_account_ids=tuple(sorted(balance_by_account, key=str)),
        policy_ids=tuple(
            sorted((policy.id for policy in policies.values()), key=str)
        ),
        lot_ids=tuple(sorted(lots, key=str)),
        serial_position_ids=tuple(serial_ids),
    )
    if (
        expected_references is not None
        and reference_signature != expected_references
    ):
        _fail(
            "snapshot_reference_graph_expanded_after_lock",
            "conflict",
            "截止快照引用集合在审计锁定后发生变化，请重新读取",
        )
    if serial_ids:
        _fail(
            "opening_scope_formal_ledger_not_empty",
            "precondition_failed",
            "期初盘点范围已存在 SN 当前归属，禁止重复建立期初",
        )

    prepared: list[_PreparedSnapshotLine] = []
    for account in accounts:
        scope = scope_by_pair[(account.owner_org_id, account.location_id)]
        balance = balance_by_account.get(account.id)
        book_qty = balance.quantity if balance is not None else _ZERO
        serial_snapshot = sorted(
            serials_by_account.get(account.id, []), key=lambda item: str(item["serial_id"])
        )
        policy = policies[account.material_id]
        if _decimal_scale(book_qty) > policy.quantity_scale or (
            not policy.allow_fraction
            and book_qty != book_qty.to_integral_value()
        ):
            _fail(
                "snapshot_quantity_policy_mismatch",
                "service_unavailable",
                "账面数量精度与截止时点物料策略不一致",
            )
        serial_tracking = policy.tracking_mode in {"serial", "lot_and_serial"}
        if not serial_tracking and serial_snapshot:
            _fail(
                "snapshot_serial_forbidden",
                "service_unavailable",
                "非 SN 追踪账户不得存在 SN 当前归属",
            )
        if serial_tracking:
            if (
                book_qty != book_qty.to_integral_value()
                or len(serial_snapshot) != int(book_qty)
            ):
                _fail(
                    "snapshot_serial_quantity_mismatch",
                    "service_unavailable",
                    "SN 追踪账户的 active SN 数量必须与账面数量一致",
                )
            for serial_document in serial_snapshot:
                serial_lot = serial_document["lot_id"]
                expected_lot = (
                    str(account.lot_id) if account.lot_id is not None else None
                )
                if serial_lot != expected_lot:
                    _fail(
                        "snapshot_serial_lot_mismatch",
                        "service_unavailable",
                        "SN 与库存账户批次绑定不一致",
                    )
        account_hash = canonical_opening_manifest_sha256(
            _account_dimension_document(account)
        )
        serial_hash = canonical_opening_manifest_sha256(
            {
                "schema": "cloud_oam.opening_stocktake.serial_snapshot.v1",
                "serials": serial_snapshot,
                "stock_account_id": str(account.id),
            }
        )
        prepared.append(
            _PreparedSnapshotLine(
                scope=scope,
                account=account,
                book_qty=book_qty,
                account_dimension_sha256=account_hash,
                serial_snapshot=serial_snapshot,
                serial_snapshot_sha256=serial_hash,
            )
        )
    return tuple(prepared), reference_signature


def _require_no_existing_scope_fact_or_freeze(
    db: Session,
    scopes: tuple[_PreparedScope, ...],
    *,
    lock_freezes: bool = True,
) -> None:
    predicates = tuple(
        and_(
            FormalStocktakeScope.owner_org_id == scope.input.owner_org_id,
            FormalStocktakeScope.location_id == scope.input.location_id,
        )
        for scope in scopes
    )
    freeze_statement = (
        select(InventoryFreeze.id)
        .join(
            FormalStocktakeScope,
            FormalStocktakeScope.id == InventoryFreeze.stocktake_scope_id,
        )
        .where(InventoryFreeze.status == "active", or_(*predicates))
        .order_by(InventoryFreeze.id)
        .limit(1)
    )
    if lock_freezes:
        freeze_statement = freeze_statement.with_for_update(
            of=InventoryFreeze
        )
    existing_freeze = db.scalar(freeze_statement)
    if existing_freeze is not None:
        _fail(
            "opening_scope_already_frozen",
            "conflict",
            "一个或多个资产所有组织与库位范围已存在活动冻结",
        )
    existing_opening = db.scalar(
        select(InventoryOpeningEstablishment.id)
        .where(
            or_(
                *(
                    and_(
                        InventoryOpeningEstablishment.owner_org_id
                        == scope.input.owner_org_id,
                        InventoryOpeningEstablishment.location_id
                        == scope.input.location_id,
                    )
                    for scope in scopes
                )
            )
        )
        .limit(1)
    )
    if existing_opening is not None:
        _fail(
            "opening_scope_already_established",
            "conflict",
            "一个或多个资产所有组织与库位已经完成期初建立",
        )


def _require_unused_business_keys(
    db: Session,
    command: StartOpeningStocktakeCommand,
    idempotency_key_hash: str,
) -> None:
    if db.scalar(
        select(FormalStocktakeTask.id).where(
            FormalStocktakeTask.task_no == command.task_no
        )
    ) is not None:
        _fail("opening_task_number_conflict", "conflict", "盘点任务号已被其他请求使用")
    if db.scalar(
        select(FormalStocktakeTask.id).where(
            FormalStocktakeTask.region_org_id == command.region_org_id,
            FormalStocktakeTask.task_type == OPENING_TASK_TYPE,
            FormalStocktakeTask.status.notin_(("closed", "cancelled")),
        )
    ) is not None:
        _fail(
            "opening_region_active_task_exists",
            "conflict",
            "该区域已存在未关闭的期初盘点任务",
        )
    if db.scalar(
        select(StocktakeRound.id).where(
            StocktakeRound.idempotency_key_hash == idempotency_key_hash
        )
    ) is not None:
        _fail(
            "opening_idempotency_conflict",
            "conflict",
            "幂等键已绑定无法重放的盘点请求",
        )


def _lock_existing_replay_anchors(
    db: Session,
    idempotency_key_hash: str,
) -> tuple[FormalStocktakeTask, StocktakeRound] | None:
    """Serialize a start replay with the count service's task/round locks."""

    probe = db.execute(
        select(StocktakeRound.id, StocktakeRound.task_id).where(
            StocktakeRound.idempotency_key_hash == idempotency_key_hash
        )
    ).one_or_none()
    if probe is None:
        return None

    round_id, task_id = probe
    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("opening-count-task", str(task_id)),
            _advisory_coordinate("opening-count-round", str(round_id)),
        ),
    )
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    round_row = db.scalar(
        select(StocktakeRound)
        .where(
            StocktakeRound.id == round_id,
            StocktakeRound.task_id == task_id,
            StocktakeRound.idempotency_key_hash == idempotency_key_hash,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None or round_row is None:
        _fail(
            "opening_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点幂等记录不完整",
        )
    return task, round_row


def _load_replay(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: StartOpeningStocktakeCommand,
    idempotency_key_hash: str,
    now: datetime,
    locked_replay: tuple[FormalStocktakeTask, StocktakeRound] | None,
) -> OpeningStocktakeStartResult | None:
    if locked_replay is None:
        return None
    task, round_row = locked_replay
    if (
        round_row.idempotency_key_hash != idempotency_key_hash
        or round_row.task_id != task.id
        or task.task_type != OPENING_TASK_TYPE
    ):
        _fail(
            "opening_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点幂等记录不完整",
        )
    _authorize_batch_manager(db, actor, task.region_org_id)
    if (
        task.task_no != command.task_no
        or task.region_org_id != command.region_org_id
        or task.control_source_system_id != command.control_source_system_id
        or task.control_sync_run_id != command.control_sync_run_id
        or task.blind_count != command.blind_count
        or _as_optional_utc(task.deadline) != command.deadline
        or task.note != command.note
        or task.created_by_user_id != actor.user_id
        or task.status
        not in {
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
        or task.current_round_no < 1
        or task.cutoff_ledger_cursor is None
        or task.cutoff_at is None
        or task.issued_at is None
        or task.frozen_at is None
        or _as_utc(task.issued_at) != _as_utc(task.cutoff_at)
        or _as_utc(task.frozen_at) != _as_utc(task.cutoff_at)
        or round_row.round_no != 1
        or round_row.round_type != "initial"
        or round_row.status not in {"counting", "submitted", "superseded"}
        or (
            round_row.status == "counting"
            and (
                round_row.submitted_at is not None
                or round_row.submitted_by_user_id is not None
                or round_row.count_manifest_sha256 is not None
            )
        )
        or (
            round_row.status in {"submitted", "superseded"}
            and (
                round_row.submitted_at is None
                or round_row.submitted_by_user_id is None
                or round_row.count_manifest_sha256 is None
            )
        )
        or _as_utc(round_row.started_at) != _as_utc(task.cutoff_at)
        or _as_utc(round_row.created_at) != _as_utc(task.cutoff_at)
        or _as_utc(round_row.updated_at) < _as_utc(task.cutoff_at)
        or _as_utc(task.created_at) != _as_utc(task.cutoff_at)
        or _as_utc(task.updated_at) < _as_utc(task.cutoff_at)
        or task.version < 0
    ):
        _fail(
            "opening_idempotency_conflict",
            "conflict",
            "幂等键已绑定不同的期初盘点请求",
        )

    scope_rows = db.scalars(
        select(FormalStocktakeScope)
        .where(FormalStocktakeScope.task_id == task.id)
        .order_by(FormalStocktakeScope.scope_no)
    ).all()
    freeze_rows = db.scalars(
        select(InventoryFreeze)
        .where(InventoryFreeze.task_id == task.id)
        .order_by(InventoryFreeze.stocktake_scope_id)
    ).all()
    freeze_by_scope = {row.stocktake_scope_id: row for row in freeze_rows}
    if len(freeze_rows) != len(scope_rows) or len(freeze_by_scope) != len(scope_rows):
        _fail(
            "opening_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点活动冻结证据不完整或重复",
        )
    for scope_row in scope_rows:
        freeze = freeze_by_scope.get(scope_row.id)
        if (
            freeze is None
            or freeze.task_id != task.id
            or freeze.scope_key != scope_row.scope_key
            or _as_utc(freeze.valid_from) != _as_utc(task.cutoff_at)
            or _as_utc(freeze.created_at) != _as_utc(task.cutoff_at)
            or not _valid_replay_freeze_lifecycle(freeze)
        ):
            _fail(
                "opening_idempotency_record_invalid",
                "service_unavailable",
                "期初盘点活动冻结与范围或截止时点不一致",
            )
    expected_scope_inputs = tuple(
        (
            value.owner_org_id,
            value.location_id,
            value.assignee_user_id,
            value.freeze_mode,
        )
        for value in command.scopes
    )
    actual_scope_inputs = tuple(
        (
            row.owner_org_id,
            row.location_id,
            row.assignee_user_id,
            freeze_by_scope[row.id].freeze_mode if row.id in freeze_by_scope else None,
        )
        for row in scope_rows
    )
    if expected_scope_inputs != actual_scope_inputs:
        _fail(
            "opening_idempotency_conflict",
            "conflict",
            "幂等键已绑定不同的盘点范围或冻结模式",
        )

    control_rows = db.scalars(
        select(StocktakeControlSnapshotLine)
        .where(StocktakeControlSnapshotLine.task_id == task.id)
        .order_by(StocktakeControlSnapshotLine.line_no)
    ).all()
    expected_control = tuple(_control_input_comparison(row) for row in command.control_lines)
    actual_control = tuple(
        (
            row.external_object_version_id,
            row.external_business_key,
            row.material_id,
            row.condition_code,
            _canonical_decimal(row.control_qty),
            row.mapping_status,
            _as_optional_utc(row.source_updated_at),
            row.payload_sha256,
            row.mapping_note,
        )
        for row in control_rows
    )
    if expected_control != actual_control:
        _fail(
            "opening_idempotency_conflict",
            "conflict",
            "幂等键已绑定不同的 OAM 控制快照",
        )

    if task.control_snapshot_at is None:
        _fail(
            "opening_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点控制快照时点缺失",
        )
    replay_control, replay_control_manifest = _prepare_control_evidence(
        db,
        command,
        now=now,
        lock_rows=False,
        historical_at=_as_utc(task.control_snapshot_at),
    )
    sync_run = db.get(SyncRun, command.control_sync_run_id)
    if (
        sync_run is None
        or command.control_sync_scope_key != sync_run.scope_key
        or replay_control_manifest != task.control_manifest_sha256
        or task.control_snapshot_at is None
        or sync_run.completed_at is None
        or _as_utc(task.control_snapshot_at) != _as_utc(sync_run.completed_at)
        or tuple(row.input.external_object_version_id for row in replay_control)
        != tuple(row.external_object_version_id for row in control_rows)
    ):
        _fail(
            "opening_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点 OAM 控制同步证据无法重算",
        )

    prepared_scopes = tuple(
        _PreparedScope(
            scope_no=row.scope_no,
            input=OpeningStocktakeScopeInput(
                owner_org_id=row.owner_org_id,
                location_id=row.location_id,
                assignee_user_id=row.assignee_user_id,
                freeze_mode=freeze_by_scope[row.id].freeze_mode,
            ),
            scope_id=row.id,
            scope_key=row.scope_key,
            scope_sha256=row.scope_sha256,
            location=db.get(StockLocation, row.location_id),
            owner=db.get(Organization, row.owner_org_id),
            custodian_person_id=row.custodian_person_id_snapshot,
        )
        for row in scope_rows
    )
    if any(row.location is None or row.owner is None for row in prepared_scopes):
        _fail(
            "opening_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点范围主数据证据缺失",
        )
    region = db.get(Organization, task.region_org_id)
    if region is None or region.status != "active" or region.org_type != "region_company":
        _fail(
            "opening_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点区域主数据已失效",
        )
    for scope in prepared_scopes:
        if (
            scope.owner.status != "active"
            or scope.owner.org_type != "region_company"
            or scope.location.status != "active"
            or scope.location.location_type not in {"region", "personal"}
        ):
            _fail(
                "opening_idempotency_record_invalid",
                "service_unavailable",
                "期初盘点资产或库位主数据已失效",
            )
        if not _organization_descends_from(
            db,
            scope.owner.id,
            task.region_org_id,
        ):
            _fail(
                "opening_idempotency_record_invalid",
                "service_unavailable",
                "期初盘点资产所有组织已不在任务区域的有效组织树内",
            )
        _require_location_in_region_tree(db, scope.location, task.region_org_id)
        replay_grant = _authorize_scope_dimensions(
            db,
            actor=actor,
            task_region_org_id=task.region_org_id,
            owner_org_id=scope.input.owner_org_id,
            location_owner_org_id=scope.location.owner_org_id,
        )
        _lock_selected_grant(db, actor, replay_grant, now)
    if _scope_manifest_hash(task.region_org_id, prepared_scopes) != task.scope_manifest_sha256:
        _fail(
            "opening_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点范围清单无法重算",
        )
    snapshot_line_count = _verify_persisted_snapshot_manifest(
        db, task, prepared_scopes
    )
    _validate_start_replay_event_graph(db, task, round_row)
    return OpeningStocktakeStartResult(
        task_id=task.id,
        task_no=task.task_no,
        status=OPENING_TASK_STATUS,
        cutoff_ledger_cursor=task.cutoff_ledger_cursor,
        initial_round_id=round_row.id,
        scope_count=len(scope_rows),
        snapshot_line_count=snapshot_line_count,
        control_line_count=len(control_rows),
    )


def _validate_start_replay_event_graph(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
) -> None:
    """Require the exact immutable start state/outbox/audit evidence graph."""

    if task.cutoff_at is None:
        _fail(
            "opening_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点启动时点缺失",
        )
    occurred_at = _as_utc(task.cutoff_at)
    transitions = (
        (None, "draft", "opening_stocktake_created"),
        ("draft", "issued", "opening_stocktake_issued"),
        ("issued", "frozen", "opening_stocktake_frozen"),
        ("frozen", "counting", "opening_stocktake_initial_round_started"),
    )
    state_rows = tuple(
        db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id == str(task.id),
                StateTransitionEvent.reason.in_(
                    tuple(reason for _, _, reason in transitions)
                ),
            )
        ).all()
    )
    expected_state_keys = {
        _evidence_key("state", task.id, str(sequence))
        for sequence in range(1, len(transitions) + 1)
    }
    if (
        len(state_rows) != len(transitions)
        or {row.idempotency_key for row in state_rows} != expected_state_keys
    ):
        _invalid_start_replay()
    by_key = {row.idempotency_key: row for row in state_rows}
    request_references: set[str] = set()
    for sequence, (from_status, to_status, reason) in enumerate(
        transitions,
        start=1,
    ):
        row = by_key[_evidence_key("state", task.id, str(sequence))]
        metadata = row.metadata_jsonb
        request_reference = (
            metadata.get("request_reference")
            if isinstance(metadata, dict)
            else None
        )
        if (
            not isinstance(request_reference, str)
            or not request_reference.startswith("opening-request-")
            or not _is_sha256(
                request_reference.removeprefix("opening-request-")
            )
            or metadata
            != {
                "control_manifest_sha256": task.control_manifest_sha256,
                "cutoff_ledger_cursor": task.cutoff_ledger_cursor,
                "request_reference": request_reference,
                "scope_manifest_sha256": task.scope_manifest_sha256,
                "snapshot_manifest_sha256": task.snapshot_manifest_sha256,
            }
            or row.from_status != from_status
            or row.to_status != to_status
            or row.reason != reason
            or row.actor_id != task.created_by_user_id
            or _as_utc(row.occurred_at) != occurred_at
            or _as_utc(row.created_at) != occurred_at
        ):
            _invalid_start_replay()
        request_references.add(request_reference)
    if len(request_references) != 1:
        _invalid_start_replay()
    request_reference = next(iter(request_references))

    outbox_rows = tuple(
        db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == "stocktake_task",
                OutboxEvent.aggregate_id == str(task.id),
                OutboxEvent.event_type == "stocktake.opening.started",
            )
        ).all()
    )
    expected_outbox_payload = {
        "control_line_count": db.scalar(
            select(func.count())
            .select_from(StocktakeControlSnapshotLine)
            .where(StocktakeControlSnapshotLine.task_id == task.id)
        )
        or 0,
        "cutoff_ledger_cursor": task.cutoff_ledger_cursor,
        "initial_round_id": str(round_row.id),
        "region_org_id": str(task.region_org_id),
        "scope_count": db.scalar(
            select(func.count())
            .select_from(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task.id)
        )
        or 0,
        "snapshot_line_count": db.scalar(
            select(func.count())
            .select_from(StocktakeSnapshotLine)
            .where(StocktakeSnapshotLine.task_id == task.id)
        )
        or 0,
        "task_id": str(task.id),
        "task_no": task.task_no,
    }
    if len(outbox_rows) != 1:
        _invalid_start_replay()
    outbox = outbox_rows[0]
    if (
        outbox.event_type != "stocktake.opening.started"
        or outbox.payload_jsonb != expected_outbox_payload
        or outbox.idempotency_key
        != _evidence_key("outbox", task.id, "started")
        or _as_utc(outbox.available_at) != occurred_at
        or _as_utc(outbox.created_at) != occurred_at
    ):
        _invalid_start_replay()

    audit_rows = tuple(
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.aggregate_type == "stocktake_task",
                AuditEvent.aggregate_id == str(task.id),
                AuditEvent.action == "stocktake.opening.started",
            )
        ).all()
    )
    if len(audit_rows) != 1:
        _invalid_start_replay()
    audit = audit_rows[0]
    if (
        audit.actor_user_id != task.created_by_user_id
        or audit.action != "stocktake.opening.started"
        or audit.before_jsonb is not None
        or audit.after_jsonb
        != {
            "control_line_count": expected_outbox_payload["control_line_count"],
            "control_manifest_sha256": task.control_manifest_sha256,
            "cutoff_ledger_cursor": task.cutoff_ledger_cursor,
            "initial_round_id": str(round_row.id),
            "region_org_id": str(task.region_org_id),
            "scope_count": expected_outbox_payload["scope_count"],
            "scope_manifest_sha256": task.scope_manifest_sha256,
            "snapshot_line_count": expected_outbox_payload[
                "snapshot_line_count"
            ],
            "snapshot_manifest_sha256": task.snapshot_manifest_sha256,
            "status": OPENING_TASK_STATUS,
            "task_no": task.task_no,
        }
        or audit.request_id != request_reference
        or _as_utc(audit.occurred_at) != occurred_at
    ):
        _invalid_start_replay()
    try:
        verify_audit_event_in_stream(
            db,
            stream_key=INVENTORY_STREAM_KEY,
            event_id=audit.id,
        )
    except AuditChainError:
        _invalid_start_replay()


def _invalid_start_replay() -> None:
    _fail(
        "opening_idempotency_record_invalid",
        "service_unavailable",
        "期初盘点幂等证据图不完整或相互矛盾",
    )


def _valid_replay_freeze_lifecycle(freeze: InventoryFreeze) -> bool:
    """Validate the immutable start binding plus its one legal release step."""

    if freeze.status == "active":
        return (
            freeze.valid_to is None
            and freeze.released_by_user_id is None
            and freeze.release_reason == ""
            and freeze.version == 0
            and _as_utc(freeze.updated_at) == _as_utc(freeze.created_at)
        )
    return (
        freeze.status in {"released", "cancelled"}
        and freeze.valid_to is not None
        and _as_utc(freeze.valid_to) > _as_utc(freeze.valid_from)
        and freeze.released_by_user_id is not None
        and bool(freeze.release_reason.strip())
        and freeze.version == 1
        and _as_utc(freeze.updated_at) >= _as_utc(freeze.valid_to)
    )


def _verify_persisted_snapshot_manifest(
    db: Session,
    task: FormalStocktakeTask,
    scopes: tuple[_PreparedScope, ...],
) -> int:
    rows = db.scalars(
        select(StocktakeSnapshotLine)
        .where(StocktakeSnapshotLine.task_id == task.id)
        .order_by(StocktakeSnapshotLine.stock_account_id)
    ).all()
    account_ids = tuple(row.stock_account_id for row in rows)
    if len(account_ids) != len(set(account_ids)):
        _fail(
            "opening_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点账面快照账户重复",
        )
    accounts = {
        row.id: row
        for row in (
            db.scalars(
                select(StockAccount).where(StockAccount.id.in_(account_ids))
            ).all()
            if account_ids
            else []
        )
    }
    scope_by_id = {row.scope_id: row for row in scopes}
    prepared: list[_PreparedSnapshotLine] = []
    for row in rows:
        account = accounts.get(row.stock_account_id)
        scope = scope_by_id.get(row.scope_id)
        if account is None or scope is None:
            _fail(
                "opening_idempotency_record_invalid",
                "service_unavailable",
                "期初盘点账面快照引用不完整",
            )
        if (
            account.owner_org_id != scope.input.owner_org_id
            or account.location_id != scope.input.location_id
            or (
                scope.location.location_type == "personal"
                and account.custodian_person_id != scope.custodian_person_id
            )
            or row.book_qty is None
            or not row.book_qty.is_finite()
            or row.book_qty != _ZERO
            or not isinstance(row.serial_snapshot_jsonb, list)
            or bool(row.serial_snapshot_jsonb)
            or row.serial_count != 0
        ):
            _fail(
                "opening_idempotency_record_invalid",
                "service_unavailable",
                "期初盘点账面快照不再满足空正式账本零基线",
            )
        expected_account_hash = canonical_opening_manifest_sha256(
            _account_dimension_document(account)
        )
        expected_serial_hash = canonical_opening_manifest_sha256(
            {
                "schema": "cloud_oam.opening_stocktake.serial_snapshot.v1",
                "serials": row.serial_snapshot_jsonb,
                "stock_account_id": str(account.id),
            }
        )
        if (
            row.account_dimension_sha256 != expected_account_hash
            or row.serial_snapshot_sha256 != expected_serial_hash
            or row.serial_count != len(row.serial_snapshot_jsonb)
            or row.ledger_cursor != task.cutoff_ledger_cursor
        ):
            _fail(
                "opening_idempotency_record_invalid",
                "service_unavailable",
                "期初盘点账面或 SN 快照清单无法重算",
            )
        prepared.append(
            _PreparedSnapshotLine(
                scope=scope,
                account=account,
                book_qty=row.book_qty,
                account_dimension_sha256=row.account_dimension_sha256,
                serial_snapshot=row.serial_snapshot_jsonb,
                serial_snapshot_sha256=row.serial_snapshot_sha256,
            )
        )
    if task.cutoff_ledger_cursor is None or _snapshot_manifest_hash(
        cutoff_ledger_cursor=task.cutoff_ledger_cursor,
        scopes=scopes,
        snapshots=tuple(prepared),
    ) != task.snapshot_manifest_sha256:
        _fail(
            "opening_idempotency_record_invalid",
            "service_unavailable",
            "期初盘点总快照清单无法重算",
        )
    return len(rows)


def _authorize_batch_manager(
    db: Session, actor: FormalPrincipal, region_org_id: uuid.UUID
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
        "opening_manager_forbidden",
        "forbidden",
        "只有全国总部管理员或本区域负责人可启动期初盘点",
    )


def _authorize_scope_dimensions(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_region_org_id: uuid.UUID,
    owner_org_id: uuid.UUID,
    location_owner_org_id: uuid.UUID,
) -> ScopeGrant:
    """Require one selected grant over the task, asset and physical dimensions."""

    target_ids = tuple(
        dict.fromkeys(
            (task_region_org_id, owner_org_id, location_owner_org_id)
        )
    )
    for grant in _manager_grants(actor, task_region_org_id):
        if all(
            _grant_allows(
                db,
                actor,
                grant,
                "stocktake",
                "manage",
                target_scope_type="organization",
                target_scope_id=str(target_org_id),
            )
            for target_org_id in target_ids
        ):
            return grant
    _fail(
        "opening_scope_dimension_forbidden",
        "forbidden",
        "当前人员未以同一有效角色分配覆盖资产所有组织与库位物理组织",
    )


def _manager_grants(
    actor: FormalPrincipal,
    region_org_id: uuid.UUID,
) -> tuple[ScopeGrant, ...]:
    grants = [
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
    grants.sort(
        key=lambda grant: (
            grant.role_code == "admin",
            str(grant.assignment_id),
        )
    )
    return tuple(grants)


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
    """Require both global deny evaluation and a selected-grant allow."""

    selected = replace(
        actor,
        assignments=(grant,),
        entitlements=tuple(
            row
            for row in actor.entitlements
            if row.assignment_id == grant.assignment_id
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
    except FormalAccessError as exc:
        _fail(
            "opening_scope_authorization_invalid",
            "forbidden",
            "期初盘点授权范围图无效",
            cause=exc,
        )


def _lock_selected_grant(
    db: Session,
    actor: FormalPrincipal,
    grant: ScopeGrant,
    now: datetime,
    *,
    lock_rows: bool = True,
) -> RoleAssignment:
    is_postgresql = db.get_bind().dialect.name == "postgresql"
    assignment_statement = select(RoleAssignment).where(
        RoleAssignment.id == grant.assignment_id
    )
    if lock_rows and not is_postgresql:
        assignment_statement = assignment_statement.with_for_update()
    assignment = db.scalar(
        assignment_statement.execution_options(populate_existing=True)
    )
    role = (
        db.scalar(
            (
                select(Role).where(Role.id == assignment.role_id)
                if is_postgresql or not lock_rows
                else select(Role)
                .where(Role.id == assignment.role_id)
                .with_for_update()
            ).execution_options(populate_existing=True)
        )
        if assignment is not None
        else None
    )
    if (
        assignment is None
        or role is None
        or assignment.user_id != actor.user_id
        or assignment.status != "active"
        or assignment.revoked_at is not None
        or _as_utc(assignment.valid_from) > now
        or (
            assignment.valid_to is not None
            and now >= _as_utc(assignment.valid_to)
        )
        or role.status != "active"
        or role.is_external
        or role.code != grant.role_code
        or assignment.scope_type != grant.scope_type
        or assignment.scope_id != grant.scope_id
    ):
        _fail(
            "opening_assignment_not_current",
            "precondition_failed",
            "期初盘点授权在锁定时已变化，请重新读取",
        )
    return assignment


def _authorize_personal_assignee(
    db: Session,
    *,
    assignee_user_id: str,
    custodian_person_id: uuid.UUID,
    now: datetime,
    lock_rows: bool = True,
) -> None:
    principal = _load_assignee(db, assignee_user_id, now=now)
    if principal.person_id != custodian_person_id:
        _fail(
            "personal_assignee_not_custodian",
            "precondition_failed",
            "个人仓初盘执行人必须是该库位当前保管工程师",
        )
    candidates = sorted(
        (
            grant
            for grant in principal.assignments
            if grant.role_code == "technician"
            and grant.scope_type == "person"
            and _same_uuid(grant.scope_id, custodian_person_id)
        ),
        key=lambda grant: str(grant.assignment_id),
    )
    selected = next(
        (
            grant
            for grant in candidates
            if _grant_allows(
                db,
                principal,
                grant,
                "stocktake",
                "count",
                target_scope_type="person",
                target_scope_id=str(custodian_person_id),
            )
        ),
        None,
    )
    if selected is None:
        _fail(
            "personal_assignee_count_forbidden",
            "forbidden",
            "个人仓初盘执行人没有本人盘点权限",
        )
    _lock_selected_grant(
        db,
        principal,
        selected,
        now,
        lock_rows=lock_rows,
    )


def _authorize_regional_assignee(
    db: Session,
    *,
    assignee_user_id: str,
    region_org_id: uuid.UUID,
    owner_org_id: uuid.UUID,
    now: datetime,
    lock_rows: bool = True,
) -> None:
    principal = _load_assignee(db, assignee_user_id, now=now)
    selected = next(
        (
            grant
            for grant in _manager_grants(principal, region_org_id)
            if all(
                _grant_allows(
                    db,
                    principal,
                    grant,
                    "stocktake",
                    "count",
                    target_scope_type="organization",
                    target_scope_id=str(target_org_id),
                )
                for target_org_id in dict.fromkeys(
                    (region_org_id, owner_org_id)
                )
            )
        ),
        None,
    )
    if selected is None:
        _fail(
            "regional_assignee_count_forbidden",
            "forbidden",
            "区域仓初盘执行人没有该区域盘点权限",
        )
    _lock_selected_grant(
        db,
        principal,
        selected,
        now,
        lock_rows=lock_rows,
    )


def _load_assignee(
    db: Session,
    user_id: str,
    *,
    now: datetime,
) -> FormalPrincipal:
    user = db.get(User, user_id)
    if user is None:
        _fail("assignee_not_found", "not_found", "盘点执行账号不存在")
    try:
        principal = load_formal_principal(db, user_id, now=now)
    except FormalAccessError as exc:
        _fail(
            "assignee_not_current",
            "precondition_failed",
            "盘点执行账号没有当前有效的正式身份和授权",
            cause=exc,
        )
    if (
        principal.account_status != "active"
        or principal.employment_status != "active"
        or principal.access_mode != "active"
    ):
        _fail(
            "assignee_inactive",
            "precondition_failed",
            "盘点执行账号或人员不是启用状态",
        )
    return principal


def _require_location_in_region_tree(
    db: Session,
    location: StockLocation,
    region_org_id: uuid.UUID,
    *,
    lock_rows: bool = False,
) -> None:
    if location.location_type == "personal":
        location_owner_statement = (
            select(Organization)
            .where(Organization.id == location.owner_org_id)
            .execution_options(populate_existing=True)
        )
        if lock_rows and db.get_bind().dialect.name != "postgresql":
            location_owner_statement = location_owner_statement.with_for_update()
        location_owner = db.scalar(location_owner_statement)
        if (
            location_owner is None
            or location_owner.status != "active"
            or location_owner.org_type != "region_company"
        ):
            _fail(
                "personal_location_owner_invalid",
                "precondition_failed",
                "个人仓归属组织必须是启用的区域公司",
            )

        child_statement = (
            select(StockLocation.id)
            .where(StockLocation.parent_id == location.id)
            .limit(1)
        )
        if lock_rows and db.get_bind().dialect.name != "postgresql":
            child_statement = child_statement.with_for_update()
        if db.scalar(child_statement) is not None:
            _fail(
                "personal_location_not_leaf",
                "precondition_failed",
                "个人仓必须是位置树叶子节点",
            )

        parent_statement = (
            select(StockLocation)
            .where(StockLocation.id == location.parent_id)
            .execution_options(populate_existing=True)
        )
        if lock_rows and db.get_bind().dialect.name != "postgresql":
            parent_statement = parent_statement.with_for_update()
        parent = db.scalar(parent_statement)
        if (
            parent is None
            or parent.status != "active"
            or parent.location_type != "region"
            or parent.owner_org_id != location.owner_org_id
        ):
            _fail(
                "personal_location_parent_invalid",
                "precondition_failed",
                "个人仓必须直接挂在同归属的启用区域仓下",
            )

    current: StockLocation | None = location
    seen_locations: set[uuid.UUID] = set()
    while current is not None:
        if current.id in seen_locations:
            _fail(
                "stock_location_tree_cycle",
                "service_unavailable",
                "库存位置树存在循环引用",
            )
        seen_locations.add(current.id)
        if current.status != "active" or not _organization_descends_from(
            db,
            current.owner_org_id,
            region_org_id,
            lock_rows=lock_rows,
        ):
            _fail(
                "stock_location_outside_region",
                "forbidden",
                "盘点库位的物理组织归属不在任务区域树内",
            )
        if current.parent_id is None:
            current = None
            continue
        parent_statement = (
            select(StockLocation)
            .where(StockLocation.id == current.parent_id)
            .execution_options(populate_existing=True)
        )
        if lock_rows and db.get_bind().dialect.name != "postgresql":
            parent_statement = parent_statement.with_for_update()
        current = db.scalar(parent_statement)
        if current is None:
            _fail(
                "stock_location_parent_missing",
                "service_unavailable",
                "库存位置树缺少已引用的父位置",
            )


def _organization_descends_from(
    db: Session,
    organization_id: uuid.UUID,
    ancestor_id: uuid.UUID,
    *,
    lock_rows: bool = False,
) -> bool:
    current_id: uuid.UUID | None = organization_id
    seen: set[uuid.UUID] = set()
    while current_id is not None:
        if current_id in seen:
            _fail(
                "organization_tree_cycle",
                "service_unavailable",
                "组织树存在循环引用",
            )
        seen.add(current_id)
        organization_statement = (
            select(Organization)
            .where(Organization.id == current_id)
            .execution_options(populate_existing=True)
        )
        if lock_rows and db.get_bind().dialect.name != "postgresql":
            organization_statement = organization_statement.with_for_update()
        organization = db.scalar(organization_statement)
        if organization is None or organization.status != "active":
            return False
        if organization.id == ancestor_id:
            return True
        current_id = organization.parent_id
    return False


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail("formal_principal_required", "forbidden", "期初盘点必须使用正式权限主体")
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _fail("actor_inactive", "forbidden", "当前账号或人员状态不允许启动期初盘点")
    return actor


def _require_current_actor(
    db: Session,
    supplied: FormalPrincipal,
    *,
    now: datetime,
) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
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
        _fail("actor_inactive", "forbidden", "当前账号或人员状态不允许启动期初盘点")
    return current


def _validate_command(command: StartOpeningStocktakeCommand) -> StartOpeningStocktakeCommand:
    if not isinstance(command, StartOpeningStocktakeCommand):
        _fail("opening_command_required", "invalid_request", "期初盘点启动命令类型无效")
    task_no = _require_text("task_no", command.task_no, 100, safe=True)
    region_org_id = _require_uuid("region_org_id", command.region_org_id)
    source_id = _require_uuid(
        "control_source_system_id", command.control_source_system_id
    )
    sync_run_id = _require_uuid("control_sync_run_id", command.control_sync_run_id)
    sync_scope = _require_text(
        "control_sync_scope_key", command.control_sync_scope_key, 200, safe=True
    )
    if not isinstance(command.scopes, tuple) or not command.scopes:
        _fail("opening_scopes_required", "invalid_request", "期初盘点至少需要一个全库位范围")
    checked_scopes: list[OpeningStocktakeScopeInput] = []
    seen_pairs: set[tuple[uuid.UUID, uuid.UUID]] = set()
    for value in command.scopes:
        if not isinstance(value, OpeningStocktakeScopeInput):
            _fail("opening_scope_invalid", "invalid_request", "期初盘点范围类型无效")
        owner_id = _require_uuid("owner_org_id", value.owner_org_id)
        location_id = _require_uuid("location_id", value.location_id)
        assignee = _require_text(
            "assignee_user_id", value.assignee_user_id, 36, safe=True
        )
        if value.freeze_mode not in {"hard", "cutoff_replay"}:
            _fail("freeze_mode_invalid", "invalid_request", "盘点冻结模式无效")
        pair = (owner_id, location_id)
        if pair in seen_pairs:
            _fail("opening_scope_duplicate", "invalid_request", "资产组织与库位范围重复")
        seen_pairs.add(pair)
        checked_scopes.append(
            OpeningStocktakeScopeInput(
                owner_org_id=owner_id,
                location_id=location_id,
                assignee_user_id=assignee,
                freeze_mode=value.freeze_mode,
            )
        )
    checked_scopes.sort(key=lambda row: (str(row.owner_org_id), str(row.location_id)))

    if not isinstance(command.control_lines, tuple):
        _fail(
            "opening_control_lines_invalid",
            "invalid_request",
            "期初盘点 OAM 控制行必须使用不可变元组",
        )
    checked_lines: list[OpeningControlLineInput] = []
    for value in command.control_lines:
        if not isinstance(value, OpeningControlLineInput):
            _fail("control_line_invalid", "invalid_request", "OAM 控制行类型无效")
        event_id = _require_uuid("sync_inbox_event_id", value.sync_inbox_event_id)
        version_id = _require_uuid(
            "external_object_version_id", value.external_object_version_id
        )
        business_key = _require_text(
            "external_business_key", value.external_business_key, 300, safe=True
        )
        material_id = (
            _require_uuid("material_id", value.material_id)
            if value.material_id is not None
            else None
        )
        quantity = _require_nonnegative_quantity(value.control_qty)
        if value.mapping_status not in {"resolved", "unresolved"}:
            _fail("mapping_status_invalid", "invalid_request", "控制行映射状态无效")
        condition = value.condition_code
        note = value.mapping_note
        if not isinstance(note, str) or len(note) > 4000:
            _fail("mapping_note_invalid", "invalid_request", "控制行映射说明无效")
        if value.mapping_status == "resolved":
            if material_id is None or condition not in {
                "new",
                "used",
                "damaged",
                "scrapped",
            } or note:
                _fail(
                    "resolved_control_line_invalid",
                    "invalid_request",
                    "已解析控制行必须精确绑定物料和成色且不得伪装映射说明",
                )
        elif material_id is not None or condition is not None or not note.strip():
            _fail(
                "unresolved_control_line_invalid",
                "invalid_request",
                "未解析控制行不得猜测物料/成色且必须保留原因",
            )
        source_updated = _as_optional_aware_datetime(
            "source_updated_at", value.source_updated_at
        )
        if not _is_sha256(value.payload_sha256):
            _fail("payload_sha256_invalid", "invalid_request", "控制行 payload hash 无效")
        checked_lines.append(
            OpeningControlLineInput(
                sync_inbox_event_id=event_id,
                external_object_version_id=version_id,
                external_business_key=business_key,
                material_id=material_id,
                condition_code=condition,
                control_qty=quantity,
                mapping_status=value.mapping_status,
                source_updated_at=source_updated,
                payload_sha256=value.payload_sha256,
                mapping_note=note,
            )
        )
    checked_lines.sort(key=lambda row: row.external_business_key)

    if not isinstance(command.blind_count, bool):
        _fail("blind_count_invalid", "invalid_request", "明盘/盲盘配置无效")
    deadline = _as_optional_aware_datetime("deadline", command.deadline)
    if not isinstance(command.note, str) or len(command.note) > 10000:
        _fail("note_invalid", "invalid_request", "盘点备注无效")
    return StartOpeningStocktakeCommand(
        task_no=task_no,
        region_org_id=region_org_id,
        control_source_system_id=source_id,
        control_sync_run_id=sync_run_id,
        control_sync_scope_key=sync_scope,
        scopes=tuple(checked_scopes),
        control_lines=tuple(checked_lines),
        blind_count=command.blind_count,
        deadline=deadline,
        note=command.note,
    )


def _scope_line_hash(
    *,
    owner_org_id: uuid.UUID,
    location_id: uuid.UUID,
    custodian_person_id: uuid.UUID | None,
    assignee_user_id: str,
) -> str:
    return canonical_opening_manifest_sha256(
        {
            "assignee_user_id": assignee_user_id,
            "custodian_person_id_snapshot": (
                str(custodian_person_id) if custodian_person_id is not None else None
            ),
            "location_id": str(location_id),
            "owner_org_id": str(owner_org_id),
            "schema": "cloud_oam.opening_stocktake.scope_line.v1",
            "scope_mode": "location_all",
        }
    )


def _scope_manifest_hash(
    region_org_id: uuid.UUID, scopes: Sequence[_PreparedScope]
) -> str:
    return canonical_opening_manifest_sha256(
        {
            "region_org_id": str(region_org_id),
            "schema": "cloud_oam.opening_stocktake.scope_manifest.v1",
            "scopes": [
                {
                    "assignee_user_id": row.input.assignee_user_id,
                    "custodian_person_id_snapshot": (
                        str(row.custodian_person_id)
                        if row.custodian_person_id is not None
                        else None
                    ),
                    "freeze_mode": row.input.freeze_mode,
                    "location_id": str(row.input.location_id),
                    "owner_org_id": str(row.input.owner_org_id),
                    "scope_key": row.scope_key,
                    "scope_mode": "location_all",
                    "scope_no": row.scope_no,
                    "scope_sha256": row.scope_sha256,
                }
                for row in scopes
            ],
        }
    )


def _control_manifest_hash(
    *,
    source_system_id: uuid.UUID,
    sync_run: SyncRun,
    region_org_id: uuid.UUID,
    lines: Sequence[_PreparedControlLine],
) -> str:
    if tuple(row.line_no for row in lines) != tuple(range(1, len(lines) + 1)):
        _fail(
            "control_line_number_invalid",
            "service_unavailable",
            "控制行序号不连续",
        )
    return opening_control_manifest_sha256(
        source_system_id=source_system_id,
        sync_run_id=sync_run.id,
        sync_scope_key=sync_run.scope_key,
        region_org_id=region_org_id,
        lines=tuple(row.input for row in lines),
    )


def _snapshot_manifest_hash(
    *,
    cutoff_ledger_cursor: int,
    scopes: Sequence[_PreparedScope],
    snapshots: Sequence[_PreparedSnapshotLine],
) -> str:
    by_scope: dict[uuid.UUID, list[_PreparedSnapshotLine]] = {
        row.scope_id: [] for row in scopes
    }
    for line in snapshots:
        by_scope[line.scope.scope_id].append(line)
    return canonical_opening_manifest_sha256(
        {
            "cutoff_ledger_cursor": cutoff_ledger_cursor,
            "schema": "cloud_oam.opening_stocktake.snapshot_manifest.v1",
            "scopes": [
                {
                    "lines": [
                        {
                            "account_dimension_sha256": line.account_dimension_sha256,
                            "book_qty": _canonical_decimal(line.book_qty),
                            "ledger_cursor": cutoff_ledger_cursor,
                            "serial_count": len(line.serial_snapshot),
                            "serial_snapshot_sha256": line.serial_snapshot_sha256,
                            "stock_account_id": str(line.account.id),
                        }
                        for line in sorted(
                            by_scope[row.scope_id], key=lambda value: str(value.account.id)
                        )
                    ],
                    "location_id": str(row.input.location_id),
                    "owner_org_id": str(row.input.owner_org_id),
                    "scope_key": row.scope_key,
                }
                for row in scopes
            ],
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
        "schema": "cloud_oam.opening_stocktake.account_dimension.v1",
        "stock_account_id": str(account.id),
    }


def _control_input_comparison(value: OpeningControlLineInput) -> tuple[object, ...]:
    return (
        value.external_object_version_id,
        value.external_business_key,
        value.material_id,
        value.condition_code,
        _canonical_decimal(value.control_qty),
        value.mapping_status,
        value.source_updated_at,
        value.payload_sha256,
        value.mapping_note,
    )


def _scope_key(owner_org_id: uuid.UUID, location_id: uuid.UUID) -> str:
    return f"opening:{owner_org_id}:{location_id}"


def _control_scope_key(region_org_id: uuid.UUID) -> str:
    return f"oam_inventory_control:region:{region_org_id}"


def _storage_hash(raw_key: str) -> str:
    return hashlib.sha256(
        f"cloud_oam.opening_stocktake.idempotency.v1\0{raw_key}".encode("utf-8")
    ).hexdigest()


def _request_reference(raw_request_id: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.opening_stocktake.request.v1\0{raw_request_id}".encode("utf-8")
    ).hexdigest()
    return f"opening-request-{digest}"


def _evidence_key(kind: str, task_id: uuid.UUID, suffix: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.opening_stocktake.{kind}.v1\0{task_id}\0{suffix}".encode(
            "utf-8"
        )
    ).hexdigest()
    return f"opening-{kind}-{digest}"


def _advisory_coordinate(namespace: str, value: str) -> int:
    # Use the same domain and coordinate algorithm as inventory_posting so all
    # inventory writers sort transaction locks identically.
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


def _database_now(db: Session) -> datetime:
    """Return the post-lock database clock used for immutable facts."""

    if db.get_bind().dialect.name == "postgresql":
        value = db.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            _fail(
                "opening_server_time_unavailable",
                "service_unavailable",
                "数据库服务端时间不可用",
            )
        return _as_utc(value)
    return datetime.now(timezone.utc)


def _is_database_guard_rejection(exc: DBAPIError) -> bool:
    """Classify only explicit server guard failures without reading messages."""

    original = getattr(exc, "orig", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        original, "pgcode", None
    )
    statement = exc.statement if isinstance(exc.statement, str) else ""
    operation = statement.lstrip().partition(" ")[0].upper()
    return sqlstate == "P0001" and operation in {"INSERT", "UPDATE", "DELETE"}


def _canonical_json_bytes(document: Mapping[str, object]) -> bytes:
    try:
        return json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail(
            "opening_manifest_not_canonical",
            "invalid_request",
            "期初盘点清单包含不可规范化的值",
            cause=exc,
        )


def _canonical_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _canonical_timestamp(value: datetime) -> str:
    return (
        _as_utc(value)
        .isoformat(timespec="microseconds")
        .replace("+00:00", "Z")
    )


def _validate_deadline(deadline: datetime | None, now: datetime) -> None:
    if deadline is not None and deadline <= now:
        _fail("deadline_invalid", "invalid_request", "盘点截止时间必须晚于启动时点")


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
        _fail(f"{field}_invalid", "invalid_request", f"{field} 格式无效")
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


def _require_nonnegative_quantity(value: object) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite():
        _fail("control_qty_invalid", "invalid_request", "控制数量必须为有限 Decimal")
    if value < 0 or value >= _MAX_QUANTITY or _decimal_scale(value) > 3:
        _fail(
            "control_qty_invalid",
            "invalid_request",
            "控制数量必须非负、最多三位小数且不超出字段范围",
        )
    return value


def _decimal_scale(value: Decimal) -> int:
    normalized = value.normalize()
    return max(0, -normalized.as_tuple().exponent)


def _require_aware_datetime(field: str, value: object) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        _fail(f"{field}_invalid", "invalid_request", f"{field} 必须包含时区")
    return value.astimezone(timezone.utc)


def _as_optional_aware_datetime(
    field: str, value: datetime | None
) -> datetime | None:
    return _require_aware_datetime(field, value) if value is not None else None


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _as_optional_utc(value: datetime | None) -> datetime | None:
    return _as_utc(value) if value is not None else None


def _is_sha256(value: object) -> bool:
    return isinstance(value, str) and _SHA256.fullmatch(value) is not None


def _same_uuid(left: str | uuid.UUID, right: str | uuid.UUID) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _fail(
    code: str,
    category: str,
    message: str,
    *,
    cause: Exception | None = None,
) -> None:
    error = OpeningStocktakeError(code, category, message)
    if cause is None:
        raise error from None
    raise error from cause


__all__ = [
    "OPENING_CONTROL_ENTITY_TYPE",
    "OpeningControlLineInput",
    "OpeningStocktakeError",
    "OpeningStocktakeScopeInput",
    "OpeningStocktakeStartResult",
    "StartOpeningStocktakeCommand",
    "canonical_opening_manifest_sha256",
    "opening_control_batch_body_sha256",
    "opening_control_manifest_sha256",
    "opening_control_projection_payload",
    "start_opening_stocktake",
]
