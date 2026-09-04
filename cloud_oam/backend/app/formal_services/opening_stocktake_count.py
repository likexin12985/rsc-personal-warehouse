"""Internal V1.0 opening-stocktake round-count submission service.

The service accepts one complete physical count for one frozen scope.  It is
called by the formal opening HTTP adapter.  The caller owns the transaction;
this module flushes but never commits or rolls back.  It records immutable
count evidence, scope completion and (for the last scope) the immutable round
submission plus reviewable differences.  Recount authorization is bound to the
immutable per-round assignment snapshot, not the base scope assignee.  The
service never creates inventory master or ledger facts and never treats an OAM
control quantity as local stock.
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
    AuditEvent,
    Organization,
    OutboxEvent,
    Role,
    RoleAssignment,
    StateTransitionEvent,
    SyncBatch,
    SyncInboxEvent,
    SyncRun,
)
from ..inventory_models import (
    FormalMaterial,
    CustodyAssignment,
    InventoryLot,
    InventorySerial,
    MaterialInventoryPolicy,
    QrCode,
    StockAccount,
    StockLocation,
)
from ..models import User
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
    StocktakeRecountScopeAssignment,
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
from .opening_stocktake import (
    OpeningControlLineInput,
    OpeningStocktakeError,
    OpeningStocktakeScopeInput,
    StartOpeningStocktakeCommand,
    _authorize_scope_dimensions,
    _organization_descends_from,
    _prepare_control_evidence,
    _require_location_in_region_tree,
)
from .inventory_posting import (
    canonical_opening_account_dimension_sha256,
    canonical_opening_count_manifest_sha256,
    canonical_opening_control_manifest_sha256,
    canonical_opening_scope_line_sha256,
    canonical_opening_scope_manifest_sha256,
    canonical_opening_serial_snapshot_sha256,
    canonical_opening_snapshot_manifest_sha256,
)
from .postgresql_lock_graph import (
    lock_opening_stocktake_task_evidence,
)


INVENTORY_STREAM_KEY: Final[str] = "inventory"
_ZERO: Final[Decimal] = Decimal("0.000")
_MAX_QUANTITY: Final[Decimal] = Decimal("1000000000000000")
_PRINTABLE = re.compile(r"^[\x21-\x7e]+$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_CONDITIONS = frozenset({"new", "used", "damaged", "scrapped"})
_AVAILABILITY = frozenset(
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
_COUNT_METHODS = frozenset({"scan", "manual", "import"})
_MAX_PHYSICAL_OBSERVATIONS: Final[int] = 10_000
_MATERIAL_IDENTIFIER_TYPES = frozenset(
    {"sku_code", "qr_code", "external_code", "unknown"}
)
_SERIAL_IDENTIFIER_TYPES = frozenset({"serial_no", "qr_code", "unknown"})
_ASCII_IDENTIFIER_FOLD_TABLE: Final[dict[int, int]] = str.maketrans(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
)
_DOWNSTREAM_REPLAY_TASK_STATUSES = frozenset(
    {
        "submitted",
        "region_review",
        "hq_review",
        "approved",
        "recount_required",
        "counting",
        "posted",
        "closed",
    }
)
_OPENING_COUNT_REPLAY_PLAN_SEAL: Final[object] = object()
_SCOPE_COUNT_REQUEST_KEYS: Final[frozenset[str]] = frozenset(
    {
        "actor_person_id",
        "actor_user_id",
        "physical_observations",
        "round_id",
        "schema",
        "scope_id",
        "task_id",
        "zero_confirmed",
    }
)
_SCOPE_COUNT_OBSERVATION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "availability_bucket",
        "condition_code",
        "count_method",
        "counted_qty",
        "lot_id",
        "lot_no_raw",
        "material_id",
        "material_identifier_raw",
        "material_identifier_type",
        "reason_code",
        "remark",
        "serial_id",
        "serial_identifier_type",
        "serial_no_raw",
    }
)
_SCOPE_COUNT_RESOLUTION_KEYS: Final[frozenset[str]] = frozenset(
    {
        "items",
        "request_sha256",
        "round_id",
        "schema",
        "scope_id",
        "task_id",
    }
)
_SCOPE_COUNT_RESOLUTION_ITEM_KEYS: Final[frozenset[str]] = frozenset(
    {
        "material_qr_mapping_id",
        "policy",
        "request_item_sha256",
        "request_ordinal",
        "resolved_lot_id",
        "resolved_material_id",
        "resolved_serial_id",
        "serial_alias_keys",
        "serial_qr_mapping_id",
        "target_id",
        "target_type",
    }
)
_SCOPE_COUNT_RESOLUTION_POLICY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "allow_fraction",
        "effective_from",
        "id",
        "quantity_scale",
        "tracking_mode",
    }
)

_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


class OpeningStocktakeCountError(RuntimeError):
    """Stable, database-detail-free failure for scope-count submission."""

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
class OpeningPhysicalObservationInput:
    material_identifier_raw: str
    material_identifier_type: str
    condition_code: str
    availability_bucket: str
    counted_qty: Decimal
    material_id: uuid.UUID | None = None
    lot_id: uuid.UUID | None = None
    lot_no_raw: str | None = None
    serial_id: uuid.UUID | None = None
    serial_no_raw: str | None = None
    serial_identifier_type: str | None = None
    count_method: str = "manual"
    reason_code: str | None = None
    remark: str = ""


@dataclass(frozen=True, slots=True)
class SubmitOpeningStocktakeScopeCountCommand:
    task_id: uuid.UUID
    round_id: uuid.UUID
    scope_id: uuid.UUID
    physical_observations: tuple[OpeningPhysicalObservationInput, ...] = ()
    zero_confirmed: bool = False


@dataclass(frozen=True, slots=True)
class OpeningStocktakeScopeCountResult:
    task_id: uuid.UUID
    round_id: uuid.UUID
    scope_id: uuid.UUID
    task_status: str
    round_status: str
    scope_completed: bool
    round_sealed: bool
    has_pending_verification: bool
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _OpeningCountReplayPlan:
    """Transaction-bound structural count proof captured before audit."""

    session: Session
    transaction: object
    task_id: uuid.UUID
    round_id: uuid.UUID
    scope_ids: tuple[uuid.UUID, ...]
    requested_completion_id: uuid.UUID
    assignment_ids: tuple[tuple[uuid.UUID, uuid.UUID], ...]
    audit_event_ids: tuple[uuid.UUID, ...]
    seal: object


@dataclass(frozen=True, slots=True)
class _PreparedAccountCount:
    account: StockAccount
    counted_qty: Decimal
    serials: tuple[InventorySerial, ...]
    count_method: str
    reason_code: str | None
    remark: str
    request_items: tuple[_PreparedObservation, ...]


@dataclass(frozen=True, slots=True)
class _PreparedObservation:
    value: OpeningPhysicalObservationInput
    material: FormalMaterial | None
    lot: InventoryLot | None
    serial: InventorySerial | None
    verification_status: str
    dimension_sha256: str
    request_ordinal: int
    request_item_sha256: str
    material_qr_mapping_id: uuid.UUID | None
    serial_qr_mapping_id: uuid.UUID | None
    serial_alias_keys: tuple[str, ...]
    policy: MaterialInventoryPolicy | None


@dataclass(frozen=True, slots=True)
class _LockedMaterialIdentifier:
    materials: tuple[FormalMaterial, ...]
    qr_mappings: tuple[QrCode, ...]


@dataclass(frozen=True, slots=True)
class _LockedSerialIdentifier:
    serials: tuple[InventorySerial, ...]
    qr_mappings: tuple[QrCode, ...]


@dataclass(frozen=True, slots=True)
class _LockedSerialReferences:
    identifiers: Mapping[
        tuple[uuid.UUID | None, str | None, str], _LockedSerialIdentifier
    ]
    serials_by_id: Mapping[uuid.UUID, InventorySerial]


@dataclass(frozen=True, slots=True)
class _CommandMaterialReferencePlan:
    signatures: Mapping[
        tuple[str, str],
        tuple[
            tuple[uuid.UUID, ...],
            tuple[tuple[uuid.UUID, uuid.UUID], ...],
        ],
    ]
    material_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class _CommandSerialReferencePlan:
    signatures: Mapping[
        tuple[str | None, str],
        tuple[
            tuple[uuid.UUID, ...],
            tuple[tuple[uuid.UUID, uuid.UUID], ...],
        ],
    ]
    serial_ids: tuple[uuid.UUID, ...]


def submit_opening_stocktake_scope_count(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitOpeningStocktakeScopeCountCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeScopeCountResult:
    """Public database-error boundary for one complete scope submission."""

    failure: OpeningStocktakeCountError | None = None
    try:
        return _submit_opening_stocktake_scope_count(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except OpeningStocktakeCountError:
        raise
    except AuditChainError:
        failure = OpeningStocktakeCountError(
            "opening_count_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，盘点提交未完成",
        )
    except IntegrityError:
        failure = OpeningStocktakeCountError(
            "opening_count_concurrent_conflict",
            "conflict",
            "盘点提交发生并发冲突，请回滚并重新读取",
        )
    except DBAPIError as exc:
        trigger_rejected = _is_explicit_database_trigger_rejection(exc)
        failure = OpeningStocktakeCountError(
            (
                "opening_count_database_guard_rejected"
                if trigger_rejected
                else "opening_count_database_unavailable"
            ),
            (
                "precondition_failed"
                if trigger_rejected
                else "service_unavailable"
            ),
            (
                "数据库安全约束拒绝了盘点提交，请回滚并重新读取"
                if trigger_rejected
                else "数据库暂时不可用，盘点提交未完成"
            ),
        )
    if failure is not None:
        raise failure from None
    raise AssertionError("unreachable opening stocktake count boundary")


def _submit_opening_stocktake_scope_count(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: SubmitOpeningStocktakeScopeCountCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningStocktakeScopeCountResult:
    """Atomically submit one complete active-round scope count without commit."""

    supplied_actor = _validate_supplied_actor(actor)
    checked = _validate_command(command)
    checked_key = _require_idempotency_key(idempotency_key)
    checked_request_id = _require_request_id(request_id)
    key_hash = _storage_hash(checked_key)

    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("opening-count-idempotency", key_hash),
            _advisory_coordinate("opening-count-task", str(checked.task_id)),
            _advisory_coordinate("opening-count-round", str(checked.round_id)),
        ),
    )

    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked.task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        _fail("opening_count_task_not_found", "not_found", "期初盘点任务不存在")

    try:
        from . import opening_observation_disposition as disposition_service
    except (ImportError, AttributeError) as exc:
        _fail(
            "opening_count_reference_graph_invalid",
            "service_unavailable",
            "盘点任务共享主数据引用图验证器不可用",
            cause=exc,
        )

    # Cross-service order is task row -> complete task principal graph ->
    # round row -> task-local evidence.  Recount/review replay later reads
    # historical dispositions, so their actors must be part of this one early
    # principal lock rather than discovered after shared reference owners.
    principal_user_ids = disposition_service._task_principal_user_ids(
        db,
        task_id=task.id,
        supplied_user_ids=(supplied_actor.user_id,),
    )
    lock_formal_principal_graph(db, tuple(sorted(principal_user_ids)))
    round_row = db.scalar(
        select(StocktakeRound)
        .where(
            StocktakeRound.id == checked.round_id,
            StocktakeRound.task_id == checked.task_id,
        )
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if round_row is None:
        _fail("opening_count_round_not_found", "not_found", "盘点轮次不存在")
    lock_opening_stocktake_task_evidence(db, task.id, round_row.id)
    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == checked.task_id)
            .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    scope_by_id = {row.id: row for row in scopes}
    scope = scope_by_id.get(checked.scope_id)
    if scope is None:
        _fail("opening_count_scope_not_found", "not_found", "盘点范围不存在")
    if task.cutoff_at is None:
        _fail(
            "opening_count_start_anchor_invalid",
            "precondition_failed",
            "期初盘点启动锚点不完整或已漂移",
        )
    material_reference_plan = _pre_read_command_material_references(
        db,
        task=task,
        scopes=scopes,
        values=checked.physical_observations,
    )
    serial_reference_plan = _pre_read_command_serial_references(
        db,
        round_id=round_row.id,
        values=checked.physical_observations,
    )
    try:
        disposition_resolutions = (
            disposition_service.lock_and_prove_opening_task_observation_resolutions(
                db,
                task=task,
                scopes=scopes,
                extra_material_ids=material_reference_plan.material_ids,
                extra_serial_ids=serial_reference_plan.serial_ids,
            )
        )
    except disposition_service.OpeningObservationDispositionError as exc:
        _fail(
            "opening_count_reference_graph_invalid",
            "service_unavailable",
            "盘点任务共享主数据引用图无法安全锁定",
            cause=exc,
        )
    locked_material_identifiers = _read_locked_command_material_references(
        db,
        material_reference_plan,
    )
    locked_serial_references = _read_locked_command_serial_references(
        db,
        round_id=round_row.id,
        values=checked.physical_observations,
        locked_material_identifiers=locked_material_identifiers,
        plan=serial_reference_plan,
    )

    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    request_document = _request_document(current_actor, checked)
    request_sha256 = _hash_document(request_document)
    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id == task.id)
            .order_by(InventoryFreeze.stocktake_scope_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        ).all()
    )
    existing_by_key = db.scalar(
        select(StocktakeScopeCountCompletion)
        .where(StocktakeScopeCountCompletion.idempotency_key_hash == key_hash)
        .execution_options(populate_existing=True)
    )
    allow_downstream = existing_by_key is not None
    _validate_start_anchors(
        db,
        task,
        round_row,
        scopes,
        freezes,
        now,
        allow_downstream=allow_downstream,
    )
    location_stmt = select(StockLocation).where(
        StockLocation.id == scope.location_id
    )
    if db.get_bind().dialect.name != "postgresql":
        location_stmt = location_stmt.with_for_update()
    location = db.scalar(
        location_stmt.execution_options(populate_existing=True)
    )
    _validate_current_scope_dimensions(db, task, scope, location, now)
    round_assignments, round_assignment_plan = _plan_round_assignment_evidence(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        allow_downstream=allow_downstream,
        disposition_resolutions=disposition_resolutions,
    )
    grant, expected_recount_assignment = _authorize_round_scope_actor(
        db,
        current_actor,
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        scope=scope,
        location=location,
        task_region_org_id=task.region_org_id,
        round_assignments=round_assignments,
        allow_downstream=allow_downstream,
    )
    assignment = _lock_current_assignment(db, current_actor, grant, now)
    # The selected assignment lock itself may wait.  Re-sample database time
    # after acquiring it so a grant that expired during that wait cannot be
    # used for either replay or a new write.
    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    round_assignments, round_assignment_plan = _plan_round_assignment_evidence(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        allow_downstream=allow_downstream,
        disposition_resolutions=disposition_resolutions,
    )
    grant, expected_recount_assignment = _authorize_round_scope_actor(
        db,
        current_actor,
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        scope=scope,
        location=location,
        task_region_org_id=task.region_org_id,
        round_assignments=round_assignments,
        allow_downstream=allow_downstream,
    )
    assignment = _lock_current_assignment(db, current_actor, grant, now)
    _validate_current_scope_dimensions(db, task, scope, location, now)

    if existing_by_key is not None:
        if (
            existing_by_key.task_id != checked.task_id
            or existing_by_key.round_id != checked.round_id
            or existing_by_key.scope_id != checked.scope_id
            or existing_by_key.completed_by_user_id != current_actor.user_id
            or existing_by_key.request_sha256 != request_sha256
            or existing_by_key.request_jsonb != request_document
        ):
            _fail(
                "opening_count_idempotency_conflict",
                "conflict",
                "幂等键已绑定不同的盘点提交",
            )
        replay_plan = _plan_opening_count_replay_evidence(
            db,
            task,
            round_row,
            scopes,
            existing_by_key,
            round_assignments=round_assignments,
        )
        _head, audit_proof = _lock_audit_chain_head_with_proof(
            db,
            stream_key=INVENTORY_STREAM_KEY,
        )
        replay_assignments = _validate_round_assignment_plan(
            db,
            plan=round_assignment_plan,
            audit_proof=audit_proof,
        )
        if {
            scope_id: row.id for scope_id, row in replay_assignments.items()
        } != {scope_id: row.id for scope_id, row in round_assignments.items()}:
            _invalid_replay()
        _validate_opening_count_replay_evidence_from_prelocked_task_graph(
            db,
            plan=replay_plan,
            audit_proof=audit_proof,
        )
        return replace(
            _replay_result(db, task, round_row, scope, existing_by_key),
            replayed=True,
        )

    existing_scope = db.scalar(
        select(StocktakeScopeCountCompletion)
        .where(
            StocktakeScopeCountCompletion.task_id == checked.task_id,
            StocktakeScopeCountCompletion.round_id == checked.round_id,
            StocktakeScopeCountCompletion.scope_id == checked.scope_id,
        )
        .execution_options(populate_existing=True)
    )
    if existing_scope is not None:
        _fail(
            "opening_count_scope_already_completed",
            "conflict",
            "该盘点范围已经提交且不可修改",
        )

    _require_round_counting_state(task, round_row, scopes, now)
    freeze = next(
        (row for row in freezes if row.stocktake_scope_id == scope.id), None
    )
    if (
        freeze is None
        or freeze.status != "active"
        or freeze.scope_key != scope.scope_key
        or freeze.valid_to is not None
    ):
        _fail(
            "opening_count_scope_not_frozen",
            "precondition_failed",
            "盘点范围没有有效的冻结证据",
        )

    snapshot_rows = tuple(
        db.scalars(
            select(StocktakeSnapshotLine)
            .where(
                StocktakeSnapshotLine.task_id == task.id,
                StocktakeSnapshotLine.scope_id == scope.id,
            )
            .order_by(StocktakeSnapshotLine.stock_account_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    account_ids = tuple(row.stock_account_id for row in snapshot_rows)
    account_stmt = (
        select(StockAccount)
        .where(StockAccount.id.in_(account_ids))
        .order_by(StockAccount.id)
    )
    if db.get_bind().dialect.name != "postgresql":
        account_stmt = account_stmt.with_for_update()
    accounts = {
        row.id: row
        for row in (
            db.scalars(
                account_stmt.execution_options(populate_existing=True)
            ).all()
            if account_ids
            else []
        )
    }
    prepared_counts, prepared_observations = _prepare_physical_set(
        db,
        task,
        round_row,
        scope,
        location.location_type,
        checked.physical_observations,
        snapshot_rows,
        accounts,
        locked_material_identifiers,
        locked_serial_references,
    )
    if checked.zero_confirmed:
        if snapshot_rows or prepared_counts or prepared_observations:
            _fail(
                "opening_count_zero_confirmation_invalid",
                "invalid_request",
                "零确认只适用于无快照且无任何实物记录的范围",
            )
    elif not snapshot_rows and not prepared_observations:
        _fail(
            "opening_count_blank_scope_requires_zero_confirmation",
            "invalid_request",
            "空盘点范围必须显式零确认",
        )

    if _scope_has_partial_evidence(db, task.id, round_row.id, scope.id):
        _fail(
            "opening_count_partial_evidence_exists",
            "conflict",
            "该范围存在未封印的盘点证据，请回滚并重新读取",
        )

    # Physical-set resolution can be long-running.  Recheck the exact round
    # snapshot and current grant once more before entering the shared audit
    # lock, then again after that lock below.
    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    round_assignments, round_assignment_plan = _plan_round_assignment_evidence(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        allow_downstream=False,
        disposition_resolutions=disposition_resolutions,
    )
    grant, expected_recount_assignment = _authorize_round_scope_actor(
        db,
        current_actor,
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        scope=scope,
        location=location,
        task_region_org_id=task.region_org_id,
        round_assignments=round_assignments,
        allow_downstream=False,
    )
    assignment = _lock_current_assignment(db, current_actor, grant, now)

    # The audit head is the final shared lock in the write order.  Acquire it
    # before the last database-clock sample so a wait cannot make the selected
    # count assignment stale after authorization was finalized.
    _head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )

    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    round_assignments = _validate_round_assignment_plan(
        db,
        plan=round_assignment_plan,
        audit_proof=audit_proof,
    )
    grant, expected_recount_assignment = _authorize_round_scope_actor(
        db,
        current_actor,
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        scope=scope,
        location=location,
        task_region_org_id=task.region_org_id,
        round_assignments=round_assignments,
        allow_downstream=False,
    )
    assignment = _lock_current_assignment(
        db,
        current_actor,
        grant,
        now,
        lock_rows=False,
    )
    _validate_current_scope_dimensions(db, task, scope, location, now)
    _require_round_counting_state(task, round_row, scopes, now)
    if (
        freeze.status != "active"
        or freeze.valid_to is not None
        or _as_utc(freeze.valid_from) > now
    ):
        _fail(
            "opening_count_scope_not_frozen",
            "precondition_failed",
            "盘点范围冻结状态在提交前已变化",
        )

    try:
        result = _write_scope_count(
            db,
            actor=current_actor,
            assignment=assignment,
            grant=grant,
            task=task,
            round_row=round_row,
            scopes=scopes,
            scope=scope,
            prepared_counts=prepared_counts,
            prepared_observations=prepared_observations,
            zero_confirmed=checked.zero_confirmed,
            key_hash=key_hash,
            request_sha256=request_sha256,
            request_document=request_document,
            request_reference=_request_reference(checked_request_id),
            now=now,
            expected_recount_assignment=expected_recount_assignment,
        )
        return result
    except OpeningStocktakeCountError:
        raise
    except AuditChainError as exc:
        _fail(
            "opening_count_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，盘点提交未完成",
            cause=exc,
        )


def _round_event_context(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
) -> dict[str, object]:
    if round_row.round_type == "initial":
        # Keep the already-published round-1 document shape immutable.  New
        # round coordinates are added only for recount events.
        return {
            "round_id": str(round_row.id),
            "task_id": str(task.id),
        }
    return {
        "recount_case_id": (
            str(round_row.recount_case_id)
            if round_row.recount_case_id is not None
            else None
        ),
        "round_id": str(round_row.id),
        "round_no": round_row.round_no,
        "round_type": round_row.round_type,
        "task_id": str(task.id),
    }


def _round_submission_state_metadata(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    *,
    aggregate_type: str,
) -> dict[str, object]:
    if round_row.round_type == "initial":
        return (
            {"task_id": str(task.id)}
            if aggregate_type == "stocktake_round"
            else {"round_id": str(round_row.id)}
        )
    return _round_event_context(task, round_row)


def _round_state_reasons(round_row: StocktakeRound) -> tuple[str, str]:
    if round_row.round_type == "initial" and round_row.round_no == 1:
        return (
            "opening_initial_scope_count_completed",
            "opening_initial_round_submitted",
        )
    if round_row.round_type == "recount" and round_row.round_no > 1:
        return (
            "opening_recount_scope_count_completed",
            "opening_recount_round_submitted",
        )
    _fail(
        "opening_count_round_shape_invalid",
        "precondition_failed",
        "盘点轮次编号与类型不一致",
    )


def _validate_selected_round_authorization(
    *,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    grant: ScopeGrant,
    round_row: StocktakeRound,
    scope: FormalStocktakeScope,
    expected_recount_assignment: StocktakeRecountScopeAssignment | None,
) -> None:
    if round_row.round_type == "initial":
        if expected_recount_assignment is not None:
            _fail(
                "opening_count_round_assignment_invalid",
                "precondition_failed",
                "初盘轮次不得绑定复盘执行人快照",
            )
        return
    row = expected_recount_assignment
    if (
        row is None
        or row.recount_case_id != round_row.recount_case_id
        or row.task_id != round_row.task_id
        or row.scope_id != scope.id
        or row.assignee_user_id != actor.user_id
        or row.assignee_person_id != actor.person_id
        or row.assignee_role_assignment_id != assignment.id
        or row.role_code != grant.role_code
        or row.scope_type != grant.scope_type
        or row.scope_id_snapshot != grant.scope_id
        or actor.authorization_version < row.authorization_version
    ):
        _fail(
            "opening_count_recount_assignment_mismatch",
            "precondition_failed",
            "复盘完成授权与轮次执行人快照不一致",
        )


def _write_scope_count(
    db: Session,
    *,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    grant: ScopeGrant,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    scope: FormalStocktakeScope,
    prepared_counts: Sequence[_PreparedAccountCount],
    prepared_observations: Sequence[_PreparedObservation],
    zero_confirmed: bool,
    key_hash: str,
    request_sha256: str,
    request_document: dict[str, object],
    request_reference: str,
    now: datetime,
    expected_recount_assignment: StocktakeRecountScopeAssignment | None,
) -> OpeningStocktakeScopeCountResult:
    _validate_selected_round_authorization(
        actor=actor,
        assignment=assignment,
        grant=grant,
        round_row=round_row,
        scope=scope,
        expected_recount_assignment=expected_recount_assignment,
    )
    event_context = _round_event_context(task, round_row)
    scope_state_reason, round_state_reason = _round_state_reasons(round_row)
    count_lines: list[StocktakeCountLine] = []
    serial_count = 0
    for value in prepared_counts:
        line = StocktakeCountLine(
            id=uuid.uuid4(),
            task_id=task.id,
            round_id=round_row.id,
            scope_id=scope.id,
            stock_account_id=value.account.id,
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
        count_lines.append(line)
        for serial in value.serials:
            db.add(
                StocktakeCountSerial(
                    count_line_id=line.id,
                    round_id=round_row.id,
                    serial_id=serial.id,
                    result="unexpected",
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
            material_id=prepared.material.id if prepared.material else None,
            material_identifier_raw=value.material_identifier_raw,
            material_identifier_type=value.material_identifier_type,
            condition_code=value.condition_code,
            availability_bucket=value.availability_bucket,
            lot_id=prepared.lot.id if prepared.lot else None,
            lot_no_raw=value.lot_no_raw,
            serial_id=prepared.serial.id if prepared.serial else None,
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
            request_sha256=request_sha256,
            idempotency_key_hash=_child_key_hash(key_hash, prepared.dimension_sha256),
            created_at=now,
        )
        db.add(row)
        observations.append(row)
        if value.serial_no_raw is not None:
            serial_count += 1
    db.flush()

    total = _require_aggregate_quantity(
        sum((row.counted_qty for row in count_lines), start=_ZERO)
        + sum((row.counted_qty for row in observations), start=_ZERO),
        field_name="盘点范围实盘合计",
    )
    authorization_sha256 = _authorization_sha256(actor, assignment, grant, now)
    evidence_manifest = _scope_evidence_manifest(
        db,
        task.id,
        round_row.id,
        scope.id,
        count_lines,
        observations,
        authorization_sha256,
    )
    request_resolution_document = _scope_count_request_resolution_document(
        task=task,
        round_row=round_row,
        scope=scope,
        request_sha256=request_sha256,
        prepared_counts=prepared_counts,
        count_lines=count_lines,
        prepared_observations=prepared_observations,
        observations=observations,
    )
    completion = StocktakeScopeCountCompletion(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        count_line_count=len(count_lines),
        observation_line_count=len(observations),
        serial_count=serial_count,
        total_counted_qty=total,
        zero_confirmed=zero_confirmed,
        evidence_manifest_sha256=evidence_manifest,
        request_sha256=request_sha256,
        request_jsonb=request_document,
        request_resolution_jsonb=request_resolution_document,
        idempotency_key_hash=key_hash,
        completed_by_user_id=actor.user_id,
        completed_by_person_id=actor.person_id,
        completed_role_assignment_id=assignment.id,
        authorization_version=actor.authorization_version,
        role_code=grant.role_code,
        scope_type=grant.scope_type,
        scope_id_snapshot=grant.scope_id,
        authorization_sha256=authorization_sha256,
        completed_at=now,
        created_at=now,
    )
    db.add(completion)
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
        for seal_scope in scopes:
            seal_location_stmt = select(StockLocation).where(
                StockLocation.id == seal_scope.location_id
            )
            if db.get_bind().dialect.name != "postgresql":
                seal_location_stmt = seal_location_stmt.with_for_update()
            seal_location = db.scalar(
                seal_location_stmt.execution_options(populate_existing=True)
            )
            _validate_current_scope_dimensions(
                db,
                task,
                seal_scope,
                seal_location,
                now,
            )
        _seal_round(
            db,
            actor=actor,
            assignment=assignment,
            task=task,
            round_row=round_row,
            scopes=scopes,
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
            reason=scope_state_reason,
            actor_id=actor.user_id,
            idempotency_key=_event_key("scope-state", round_row.id, scope.id),
            occurred_at=now,
            metadata_jsonb={
                **event_context,
                "zero_confirmed": zero_confirmed,
            },
            created_at=now,
        )
    )
    db.add(
        OutboxEvent(
            event_type="stocktake.opening.scope_count_completed",
            aggregate_type="stocktake_scope",
            aggregate_id=str(scope.id),
            payload_jsonb={
                **event_context,
                "round_sealed": sealed,
                "scope_id": str(scope.id),
            },
            status="pending",
            attempts=0,
            idempotency_key=_event_key("scope-outbox", round_row.id, scope.id),
            available_at=now,
            locked_at=None,
            locked_by=None,
            published_at=None,
            last_error=None,
            created_at=now,
            updated_at=now,
        )
    )
    if sealed:
        db.add(
            StateTransitionEvent(
                aggregate_type="stocktake_round",
                aggregate_id=str(round_row.id),
                from_status="counting",
                to_status="submitted",
                reason=round_state_reason,
                actor_id=actor.user_id,
                idempotency_key=_event_key("round-state", round_row.id, task.id),
                occurred_at=now,
                metadata_jsonb=_round_submission_state_metadata(
                    task,
                    round_row,
                    aggregate_type="stocktake_round",
                ),
                created_at=now,
            )
        )
        db.add(
            OutboxEvent(
                event_type="stocktake.opening.round_submitted",
                aggregate_type="stocktake_round",
                aggregate_id=str(round_row.id),
                payload_jsonb=event_context,
                status="pending",
                attempts=0,
                idempotency_key=_event_key(
                    "round-outbox", round_row.id, task.id
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
        db.add(
            StateTransitionEvent(
                aggregate_type="stocktake_task",
                aggregate_id=str(task.id),
                from_status="counting",
                to_status="submitted",
                reason=round_state_reason,
                actor_id=actor.user_id,
                idempotency_key=_event_key("task-state", round_row.id, task.id),
                occurred_at=now,
                metadata_jsonb=_round_submission_state_metadata(
                    task,
                    round_row,
                    aggregate_type="stocktake_task",
                ),
                created_at=now,
            )
        )
    db.flush()
    round_has_pending = bool(
        db.scalar(
            select(func.count())
            .select_from(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.round_id == round_row.id,
                StocktakeCountObservation.verification_status
                == "pending_verification",
            )
        )
    )
    scope_has_pending = bool(
        db.scalar(
            select(func.count())
            .select_from(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.round_id == round_row.id,
                StocktakeCountObservation.scope_id == scope.id,
                StocktakeCountObservation.verification_status
                == "pending_verification",
            )
        )
    )
    append_audit_event(
        db,
        stream_key=INVENTORY_STREAM_KEY,
        actor_user_id=actor.user_id,
        action="stocktake.opening.scope_count_completed",
        aggregate_type="stocktake_scope",
        aggregate_id=str(scope.id),
        before_jsonb=None,
        after_jsonb={
            **event_context,
            "has_pending_verification": scope_has_pending,
            "round_sealed": sealed,
            "zero_confirmed": zero_confirmed,
        },
        request_id=request_reference,
        occurred_at=now,
        created_at=now,
    )
    if sealed:
        append_audit_event(
            db,
            stream_key=INVENTORY_STREAM_KEY,
            actor_user_id=actor.user_id,
            action="stocktake.opening.round_submitted",
            aggregate_type="stocktake_round",
            aggregate_id=str(round_row.id),
            before_jsonb=None,
            after_jsonb=event_context,
            request_id=request_reference,
            occurred_at=now,
            created_at=now,
        )
    db.flush()
    return OpeningStocktakeScopeCountResult(
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        task_status=task.status,
        round_status=round_row.status,
        scope_completed=True,
        round_sealed=sealed,
        has_pending_verification=round_has_pending,
    )


def _seal_round(
    db: Session,
    *,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    completions: Sequence[StocktakeScopeCountCompletion],
    sealing_completion_id: uuid.UUID,
    now: datetime,
) -> None:
    round_manifest = _round_manifest_sha256(
        task.id,
        round_row.id,
        completions,
        sealing_completion_id,
    )
    total = _require_aggregate_quantity(
        sum((row.total_counted_qty for row in completions), start=_ZERO),
        field_name="盘点轮次实盘合计",
    )
    count_manifest = _persisted_count_manifest_sha256(db, task, round_row)
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
        total_counted_qty=total,
        round_manifest_sha256=round_manifest,
        count_manifest_sha256=count_manifest,
        request_sha256=_hash_document(
            {
                "count_manifest_sha256": count_manifest,
                "round_id": str(round_row.id),
                "round_manifest_sha256": round_manifest,
                "schema": "cloud_oam.opening_stocktake.round_submission_request.v3",
                "sealing_completion_id": str(sealing_completion_id),
            }
        ),
        idempotency_key_hash=_event_hash("round-submission", round_row.id, task.id),
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
    # Migration 0018's recount-round guard validates the successor while its
    # parent task is still ``counting``.  Persist the immutable round seal
    # before advancing the task pointer/status.
    db.flush()
    task.status = "submitted"
    task.submitted_at = now
    task.version += 1
    task.updated_at = now
    db.flush()
    _create_round_differences(db, task, round_row, scopes, now)
    _complete_round_difference_set(
        db,
        task=task,
        round_row=round_row,
        submission=submission,
        actor=actor,
        assignment=assignment,
        now=now,
    )


def _create_round_differences(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    now: datetime,
) -> None:
    if db.scalar(
        select(func.count())
        .select_from(StocktakeDifference)
        .where(
            StocktakeDifference.task_id == task.id,
            StocktakeDifference.round_id == round_row.id,
        )
    ):
        _fail(
            "opening_count_difference_preexists",
            "conflict",
            "盘点差异已经生成且不可覆盖",
        )
    scope_no = {row.id: row.scope_no for row in scopes}
    count_lines = tuple(
        db.scalars(
            select(StocktakeCountLine)
            .where(StocktakeCountLine.round_id == round_row.id)
            .order_by(StocktakeCountLine.scope_id, StocktakeCountLine.stock_account_id)
        ).all()
    )
    account_ids = tuple(row.stock_account_id for row in count_lines)
    accounts = {
        row.id: row
        for row in (
            db.scalars(select(StockAccount).where(StockAccount.id.in_(account_ids))).all()
            if account_ids
            else []
        )
    }
    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(StocktakeCountObservation.round_id == round_row.id)
            .order_by(StocktakeCountObservation.scope_id, StocktakeCountObservation.observation_no)
        ).all()
    )
    differences: list[StocktakeDifference] = []
    physical_by_dimension: dict[tuple[uuid.UUID, str], Decimal] = defaultdict(
        lambda: _ZERO
    )
    for line in sorted(
        count_lines,
        key=lambda row: (scope_no.get(row.scope_id, 0), str(row.stock_account_id)),
    ):
        account = accounts.get(line.stock_account_id)
        if account is None:
            _fail("opening_count_account_lost", "service_unavailable", "盘点账户证据缺失")
        physical_by_dimension[(account.material_id, account.condition_code)] += line.counted_qty
        if line.counted_qty > _ZERO:
            differences.append(
                _difference(
                    task=task,
                    round_row=round_row,
                    scope_id=line.scope_id,
                    difference_type="excess",
                    material_id=account.material_id,
                    observed_account_id=account.id,
                    observed_line_id=None,
                    serial_id=None,
                    book_qty=_ZERO,
                    counted_qty=line.counted_qty,
                    reason_code="opening_physical_excess",
                    reason_text="期初实物盘点数量，仅待复核后建立个人仓库存",
                    now=now,
                )
            )
    for row in sorted(
        observations,
        key=lambda value: (scope_no.get(value.scope_id, 0), value.observation_no),
    ):
        if row.material_id is not None:
            physical_by_dimension[(row.material_id, row.condition_code)] += row.counted_qty
        differences.append(
            _difference(
                task=task,
                round_row=round_row,
                scope_id=row.scope_id,
                difference_type="excess",
                material_id=row.material_id,
                observed_account_id=None,
                observed_line_id=row.id,
                serial_id=row.serial_id,
                book_qty=_ZERO,
                counted_qty=row.counted_qty,
                reason_code=(
                    "opening_pending_verification"
                    if row.verification_status == "pending_verification"
                    else "opening_unexpected_dimension"
                ),
                reason_text=(
                    "现场实物标识尚未唯一解析，保留为不可过账待核实差异"
                    if row.verification_status == "pending_verification"
                    else "现场实物维度在截止快照中不存在，待复核后处理"
                ),
                now=now,
            )
        )

    controls = tuple(
        db.scalars(
            select(StocktakeControlSnapshotLine)
            .where(StocktakeControlSnapshotLine.task_id == task.id)
            .order_by(StocktakeControlSnapshotLine.line_no)
        ).all()
    )
    resolved: dict[
        tuple[uuid.UUID, str], list[StocktakeControlSnapshotLine]
    ] = defaultdict(list)
    for row in controls:
        if row.mapping_status == "resolved" and row.material_id is not None and row.condition_code:
            resolved[(row.material_id, row.condition_code)].append(row)
        elif row.control_qty > _ZERO:
            differences.append(
                _control_difference(
                    task, round_row, row, _ZERO, "OAM 控制行无法唯一映射到本地实物维度", now
                )
            )
    for dimension, rows in sorted(resolved.items(), key=lambda item: (str(item[0][0]), item[0][1])):
        physical = physical_by_dimension.get(dimension, _ZERO)
        control_total = sum((row.control_qty for row in rows), start=_ZERO)
        if physical == control_total:
            continue
        if len(rows) == 1:
            differences.append(
                _control_difference(
                    task,
                    round_row,
                    rows[0],
                    physical,
                    "OAM 省级控制数量与期初实物汇总不一致，仅用于对账",
                    now,
                )
            )
        else:
            _fail(
                "opening_control_allocation_ambiguous",
                "precondition_failed",
                "同一维度存在多条 OAM 控制行且汇总不一致，禁止猜测分摊",
            )
    for difference_no, row in enumerate(differences, start=1):
        row.difference_no = difference_no
        db.add(row)
    db.flush()


def _persisted_count_manifest_sha256(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
) -> str:
    """Use the same immutable count document as the final posting guard."""

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
    return canonical_opening_count_manifest_sha256(
        task,
        round_row,
        count_lines,
        count_serials,
    )


def _complete_round_difference_set(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    submission: StocktakeRoundSubmission,
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    now: datetime,
) -> StocktakeDifferenceSetCompletion:
    sealing = db.get(StocktakeScopeCountCompletion, submission.sealing_completion_id)
    if sealing is None:
        _fail(
            "opening_count_sealing_completion_missing",
            "service_unavailable",
            "期初盘点轮次最终范围封印缺失",
        )
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
    summary = _difference_set_summary(
        task=task,
        round_row=round_row,
        submission=submission,
        differences=differences,
    )
    summary["total_affected_qty"] = _require_aggregate_quantity(
        summary["total_affected_qty"],
        field_name="盘点差异影响合计",
    )
    completion = StocktakeDifferenceSetCompletion(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        round_submission_id=submission.id,
        difference_count=summary["difference_count"],
        physical_difference_count=summary["physical_difference_count"],
        control_difference_count=summary["control_difference_count"],
        pending_observation_difference_count=summary[
            "pending_observation_difference_count"
        ],
        total_affected_qty=summary["total_affected_qty"],
        difference_manifest_sha256=summary["difference_manifest_sha256"],
        request_sha256=_difference_set_request_sha256(summary),
        idempotency_key_hash=_event_hash(
            "difference-set-completion",
            round_row.id,
            submission.id,
        ),
        completed_by_user_id=actor.user_id,
        completed_by_person_id=actor.person_id,
        completed_role_assignment_id=assignment.id,
        authorization_version=actor.authorization_version,
        role_code=sealing.role_code,
        scope_type=sealing.scope_type,
        scope_id_snapshot=sealing.scope_id_snapshot,
        authorization_sha256=sealing.authorization_sha256,
        completed_at=now,
        created_at=now,
    )
    db.add(completion)
    db.flush()
    return completion


def _difference_set_summary(
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    submission: StocktakeRoundSubmission,
    differences: Sequence[StocktakeDifference],
) -> dict[str, object]:
    ordered = tuple(sorted(differences, key=lambda row: row.difference_no))
    physical_count = sum(
        1 for row in ordered if row.difference_type != "control_unassigned"
    )
    control_count = len(ordered) - physical_count
    pending_count = sum(
        1
        for row in ordered
        if row.observed_line_id is not None
        and row.reason_code == "opening_pending_verification"
    )
    total_affected_qty = sum(
        (row.affected_qty for row in ordered),
        start=_ZERO,
    )
    evidence = {
        "differences": [_difference_manifest_row(row) for row in ordered],
        "round_id": str(round_row.id),
        "round_submission_id": str(submission.id),
        "schema": "cloud_oam.opening_stocktake.difference_set.v1",
        "task_id": str(task.id),
    }
    return {
        "control_difference_count": control_count,
        "difference_count": len(ordered),
        "difference_manifest_sha256": _hash_document(evidence),
        "pending_observation_difference_count": pending_count,
        "physical_difference_count": physical_count,
        "round_id": str(round_row.id),
        "round_submission_id": str(submission.id),
        "task_id": str(task.id),
        "total_affected_qty": total_affected_qty,
    }


def _difference_set_request_sha256(summary: Mapping[str, object]) -> str:
    return _hash_document(
        {
            "control_difference_count": summary["control_difference_count"],
            "difference_count": summary["difference_count"],
            "difference_manifest_sha256": summary[
                "difference_manifest_sha256"
            ],
            "pending_observation_difference_count": summary[
                "pending_observation_difference_count"
            ],
            "physical_difference_count": summary[
                "physical_difference_count"
            ],
            "round_id": summary["round_id"],
            "round_submission_id": summary["round_submission_id"],
            "schema": "cloud_oam.opening_stocktake.difference_set_completion_request.v1",
            "task_id": summary["task_id"],
            "total_affected_qty": _canonical_decimal(
                summary["total_affected_qty"]
            ),
        }
    )


def _difference_manifest_row(row: StocktakeDifference) -> dict[str, object]:
    return {
        "affected_qty": _canonical_decimal(row.affected_qty),
        "book_qty": _canonical_decimal(row.book_qty),
        "control_snapshot_line_id": (
            str(row.control_snapshot_line_id)
            if row.control_snapshot_line_id is not None
            else None
        ),
        "counted_qty": _canonical_decimal(row.counted_qty),
        "created_at": _canonical_timestamp(row.created_at),
        "difference_id": str(row.id),
        "difference_no": row.difference_no,
        "difference_qty": _canonical_decimal(row.difference_qty),
        "difference_type": row.difference_type,
        "evidence_required": row.evidence_required,
        "expected_account_id": (
            str(row.expected_account_id)
            if row.expected_account_id is not None
            else None
        ),
        "material_id": str(row.material_id) if row.material_id is not None else None,
        "observed_account_id": (
            str(row.observed_account_id)
            if row.observed_account_id is not None
            else None
        ),
        "observed_line_id": (
            str(row.observed_line_id) if row.observed_line_id is not None else None
        ),
        "reason_code": row.reason_code,
        "reason_text": row.reason_text,
        "scope_id": str(row.scope_id) if row.scope_id is not None else None,
        "serial_id": str(row.serial_id) if row.serial_id is not None else None,
    }


def _difference(
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scope_id: uuid.UUID | None,
    difference_type: str,
    material_id: uuid.UUID | None,
    observed_account_id: uuid.UUID | None,
    observed_line_id: uuid.UUID | None,
    serial_id: uuid.UUID | None,
    book_qty: Decimal,
    counted_qty: Decimal,
    reason_code: str,
    reason_text: str,
    now: datetime,
) -> StocktakeDifference:
    delta = counted_qty - book_qty
    return StocktakeDifference(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope_id,
        control_snapshot_line_id=None,
        difference_no=0,
        difference_type=difference_type,
        material_id=material_id,
        expected_account_id=None,
        observed_account_id=observed_account_id,
        observed_line_id=observed_line_id,
        serial_id=serial_id,
        book_qty=book_qty,
        counted_qty=counted_qty,
        difference_qty=delta,
        affected_qty=abs(delta),
        reason_code=reason_code,
        reason_text=reason_text,
        evidence_required=True,
        created_at=now,
    )


def _control_difference(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    control: StocktakeControlSnapshotLine,
    physical_qty: Decimal,
    reason_text: str,
    now: datetime,
) -> StocktakeDifference:
    row = _difference(
        task=task,
        round_row=round_row,
        scope_id=None,
        difference_type="control_unassigned",
        material_id=control.material_id,
        observed_account_id=None,
        observed_line_id=None,
        serial_id=None,
        book_qty=control.control_qty,
        counted_qty=physical_qty,
        reason_code="opening_control_reconciliation",
        reason_text=reason_text,
        now=now,
    )
    row.control_snapshot_line_id = control.id
    return row


def _validate_start_anchors(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    freezes: Sequence[InventoryFreeze],
    now: datetime,
    *,
    allow_downstream: bool = False,
) -> None:
    cutoff_at = _as_utc(task.cutoff_at) if task.cutoff_at is not None else None
    freeze_by_scope = {row.stocktake_scope_id: row for row in freezes}
    posted_or_closed_replay = allow_downstream and task.status in {
        "posted",
        "closed",
    }

    def _freeze_anchor_invalid(
        scope: FormalStocktakeScope,
        freeze: InventoryFreeze | None,
    ) -> bool:
        if (
            freeze is None
            or freeze.task_id != task.id
            or freeze.scope_key != scope.scope_key
            or cutoff_at is None
            or _as_utc(freeze.valid_from) != cutoff_at
        ):
            return True
        if posted_or_closed_replay:
            posted_at = (
                _as_utc(task.posted_at) if task.posted_at is not None else None
            )
            released_at = (
                _as_utc(freeze.valid_to) if freeze.valid_to is not None else None
            )
            return bool(
                freeze.status != "released"
                or posted_at is None
                or released_at is None
                or released_at < posted_at
                or freeze.released_by_user_id is None
                or not freeze.release_reason.strip()
            )
        return freeze.status != "active" or freeze.valid_to is not None

    round_started_at = _as_utc(round_row.started_at)
    if round_row.round_type == "recount" and (
        round_row.round_no <= 1 or round_row.recount_case_id is None
    ):
        _fail(
            "opening_count_recount_evidence_invalid",
            "precondition_failed",
            "复盘轮次缺少不可变因果案件",
        )
    round_shape_valid = bool(
        (
            round_row.round_no == 1
            and round_row.round_type == "initial"
            and round_row.recount_case_id is None
            and round_started_at == cutoff_at
        )
        or (
            round_row.round_no > 1
            and round_row.round_type == "recount"
            and round_row.recount_case_id is not None
            and cutoff_at is not None
            and round_started_at >= cutoff_at
        )
    )
    if (
        task.task_type != "opening"
        or task.status not in _DOWNSTREAM_REPLAY_TASK_STATUSES
        or task.current_round_no < round_row.round_no
        or (
            posted_or_closed_replay
            and (
                task.posted_at is None
                or _as_utc(task.posted_at) > now
                or (
                    task.status == "closed"
                    and (
                        task.closed_at is None
                        or _as_utc(task.closed_at) < _as_utc(task.posted_at)
                    )
                )
                or (task.status == "posted" and task.closed_at is not None)
            )
        )
        or task.cutoff_ledger_cursor is None
        or task.cutoff_ledger_cursor < 0
        or cutoff_at is None
        or cutoff_at > now
        or task.issued_at is None
        or task.frozen_at is None
        or _as_utc(task.issued_at) != cutoff_at
        or _as_utc(task.frozen_at) != cutoff_at
        or task.control_source_system_id is None
        or task.control_sync_run_id is None
        or task.control_snapshot_at is None
        or not all(
            isinstance(value, str) and _SHA256.fullmatch(value)
            for value in (
                task.scope_manifest_sha256,
                task.snapshot_manifest_sha256,
                task.control_manifest_sha256,
            )
        )
        or not round_shape_valid
        or not scopes
        or [row.scope_no for row in sorted(scopes, key=lambda row: row.scope_no)]
        != list(range(1, len(scopes) + 1))
        or len(freezes) != len(scopes)
        or len(freeze_by_scope) != len(scopes)
    ):
        _fail(
            "opening_count_start_anchor_invalid",
            "precondition_failed",
            "期初盘点启动锚点不完整或已漂移",
        )
    for scope in scopes:
        freeze = freeze_by_scope.get(scope.id)
        if (
            _freeze_anchor_invalid(scope, freeze)
            or scope.scope_sha256 != canonical_opening_scope_line_sha256(scope)
        ):
            _fail(
                "opening_count_scope_anchor_invalid",
                "precondition_failed",
                "期初盘点范围或冻结锚点已漂移",
            )
    if task.scope_manifest_sha256 != canonical_opening_scope_manifest_sha256(
        task, scopes, freeze_by_scope
    ):
        _fail(
            "opening_count_scope_manifest_mismatch",
            "precondition_failed",
            "期初盘点范围清单校验失败",
        )

    location_ids = tuple(scope.location_id for scope in scopes)
    location_stmt = (
        select(StockLocation)
        .where(StockLocation.id.in_(location_ids))
        .order_by(StockLocation.id)
    )
    if db.get_bind().dialect.name != "postgresql":
        location_stmt = location_stmt.with_for_update()
    locations = {
        row.id: row
        for row in db.scalars(
            location_stmt.execution_options(populate_existing=True)
        ).all()
    }
    if len(locations) != len(set(location_ids)):
        _fail(
            "opening_count_scope_location_missing",
            "service_unavailable",
            "期初盘点范围库位证据缺失",
        )

    snapshot_lines = tuple(
        db.scalars(
            select(StocktakeSnapshotLine)
            .where(StocktakeSnapshotLine.task_id == task.id)
            .order_by(
                StocktakeSnapshotLine.scope_id,
                StocktakeSnapshotLine.stock_account_id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    account_ids = tuple(row.stock_account_id for row in snapshot_lines)
    assert cutoff_at is not None
    account_stmt = (
        select(StockAccount)
        .where(StockAccount.id.in_(account_ids))
        .order_by(StockAccount.id)
    )
    if db.get_bind().dialect.name != "postgresql":
        account_stmt = account_stmt.with_for_update()
    accounts = {
        row.id: row
        for row in (
            db.scalars(
                account_stmt.execution_options(populate_existing=True)
            ).all()
            if account_ids
            else []
        )
    }
    scope_by_id = {row.id: row for row in scopes}
    if len(accounts) != len(account_ids):
        _fail(
            "opening_count_snapshot_account_missing",
            "service_unavailable",
            "期初盘点截止账户证据缺失",
        )
    for line in snapshot_lines:
        account = accounts[line.stock_account_id]
        scope = scope_by_id.get(line.scope_id)
        location = locations.get(scope.location_id) if scope is not None else None
        serial_snapshot = line.serial_snapshot_jsonb
        if (
            scope is None
            or location is None
            or account.owner_org_id != scope.owner_org_id
            or account.location_id != scope.location_id
            or (
                location.location_type == "personal"
                and account.custodian_person_id
                != scope.custodian_person_id_snapshot
            )
            or line.book_qty != _ZERO
            or line.ledger_cursor != task.cutoff_ledger_cursor
            or line.serial_count != 0
            or serial_snapshot != []
            or line.account_dimension_sha256
            != canonical_opening_account_dimension_sha256(account)
            or line.serial_snapshot_sha256
            != canonical_opening_serial_snapshot_sha256(
                stock_account_id=account.id,
                serials=serial_snapshot,
            )
        ):
            _fail(
                "opening_count_snapshot_anchor_invalid",
                "precondition_failed",
                "期初盘点截止快照或账户维度已漂移",
            )
    if task.snapshot_manifest_sha256 != canonical_opening_snapshot_manifest_sha256(
        task, scopes, snapshot_lines
    ):
        _fail(
            "opening_count_snapshot_manifest_mismatch",
            "precondition_failed",
            "期初盘点截止快照清单校验失败",
        )

    control_lines = tuple(
        db.scalars(
            select(StocktakeControlSnapshotLine)
            .where(StocktakeControlSnapshotLine.task_id == task.id)
            .order_by(StocktakeControlSnapshotLine.line_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    sync_run = db.get(SyncRun, task.control_sync_run_id)
    if (
        sync_run is None
        or task.control_manifest_sha256
        != canonical_opening_control_manifest_sha256(task, sync_run, control_lines)
    ):
        _fail(
            "opening_count_control_manifest_mismatch",
            "precondition_failed",
            "OAM 只读控制清单校验失败",
        )
    event_rows = db.scalars(
        select(SyncInboxEvent)
        .join(SyncBatch, SyncBatch.id == SyncInboxEvent.batch_id)
        .where(
            SyncBatch.run_id == sync_run.id,
            SyncInboxEvent.entity_type == "oam_inventory_control",
        )
        .order_by(SyncInboxEvent.id)
    ).all()
    events_by_external: dict[str, list[SyncInboxEvent]] = defaultdict(list)
    for event_row in event_rows:
        events_by_external[event_row.external_id].append(event_row)
    if any(
        len(events_by_external.get(row.external_business_key, ())) != 1
        for row in control_lines
    ):
        _fail(
            "opening_count_control_evidence_invalid",
            "precondition_failed",
            "OAM 只读控制历史事件不唯一",
        )
    reconstructed = StartOpeningStocktakeCommand(
        task_no=task.task_no,
        region_org_id=task.region_org_id,
        control_source_system_id=task.control_source_system_id,
        control_sync_run_id=task.control_sync_run_id,
        control_sync_scope_key=sync_run.scope_key,
        scopes=tuple(
            OpeningStocktakeScopeInput(
                owner_org_id=scope.owner_org_id,
                location_id=scope.location_id,
                assignee_user_id=scope.assignee_user_id,
                freeze_mode=freeze_by_scope[scope.id].freeze_mode,
            )
            for scope in scopes
        ),
        control_lines=tuple(
            OpeningControlLineInput(
                sync_inbox_event_id=events_by_external[
                    line.external_business_key
                ][0].id,
                external_object_version_id=line.external_object_version_id,
                external_business_key=line.external_business_key,
                material_id=line.material_id,
                condition_code=line.condition_code,
                control_qty=line.control_qty,
                mapping_status=line.mapping_status,
                source_updated_at=(
                    _as_utc(line.source_updated_at)
                    if line.source_updated_at is not None
                    else None
                ),
                payload_sha256=line.payload_sha256,
                mapping_note=line.mapping_note,
            )
            for line in control_lines
        ),
        blind_count=task.blind_count,
        deadline=task.deadline,
        note=task.note,
    )
    try:
        _prepared_control, historical_manifest = _prepare_control_evidence(
            db,
            reconstructed,
            now=now,
            lock_rows=False,
            historical_at=task.control_snapshot_at,
        )
        if historical_manifest != task.control_manifest_sha256:
            raise OpeningStocktakeError(
                "control_manifest_mismatch",
                "precondition_failed",
                "control manifest mismatch",
            )
    except OpeningStocktakeError as exc:
        _fail(
            "opening_count_control_evidence_invalid",
            "precondition_failed",
            "OAM 只读控制历史证据校验失败",
            cause=exc,
        )


def _prepare_physical_set(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scope: FormalStocktakeScope,
    location_type: str,
    values: Sequence[OpeningPhysicalObservationInput],
    snapshots: Sequence[StocktakeSnapshotLine],
    accounts: Mapping[uuid.UUID, StockAccount],
    locked_material_identifiers: Mapping[
        tuple[str, str], _LockedMaterialIdentifier
    ],
    locked_serial_references: _LockedSerialReferences,
) -> tuple[tuple[_PreparedAccountCount, ...], tuple[_PreparedObservation, ...]]:
    if len(accounts) != len(snapshots) or any(
        row.book_qty != _ZERO
        or row.ledger_cursor != task.cutoff_ledger_cursor
        or row.serial_count != 0
        or row.serial_snapshot_jsonb != []
        for row in snapshots
    ):
        _fail(
            "opening_count_nonzero_cutoff_snapshot",
            "precondition_failed",
            "期初盘点截止快照不是正式零基线",
        )
    resolved = _resolve_physical_observations(
        db,
        task,
        round_row,
        scope,
        values,
        locked_material_identifiers,
        locked_serial_references,
    )
    by_account: dict[uuid.UUID, list[_PreparedObservation]] = defaultdict(list)
    unexpected: list[_PreparedObservation] = []
    for row in resolved:
        matches = [
            account
            for account in accounts.values()
            if row.verification_status == "verified"
            and _account_matches_observation(
                account,
                scope,
                location_type,
                row.value,
            )
        ]
        if len(matches) > 1:
            _fail(
                "opening_count_snapshot_dimension_ambiguous",
                "service_unavailable",
                "冻结快照存在重复实物维度",
            )
        if matches:
            by_account[matches[0].id].append(row)
        else:
            unexpected.append(row)

    prepared: list[_PreparedAccountCount] = []
    for snapshot in snapshots:
        account = accounts[snapshot.stock_account_id]
        if (
            account.owner_org_id != scope.owner_org_id
            or account.location_id != scope.location_id
            or (
                location_type == "personal"
                and account.custodian_person_id
                != scope.custodian_person_id_snapshot
            )
        ):
            _fail("opening_count_snapshot_scope_mismatch", "service_unavailable", "截止快照维度与盘点范围不一致")
        policy = _load_policy(db, account.material_id, task.cutoff_at)
        observed = by_account.get(account.id, [])
        if policy.tracking_mode in {"serial", "lot_and_serial"}:
            if any(row.serial is None for row in observed):
                _fail(
                    "opening_count_serial_binding_invalid",
                    "invalid_request",
                    "SN 物料必须逐件唯一解析",
                )
            serial_rows = tuple(row.serial for row in observed if row.serial is not None)
            if len({row.id for row in serial_rows}) != len(serial_rows):
                _fail("opening_count_serial_duplicate", "invalid_request", "同一 SN 不得重复盘点")
            qty = sum((row.value.counted_qty for row in observed), start=_ZERO)
            methods = {
                (row.value.count_method, row.value.reason_code, row.value.remark)
                for row in observed
            }
            if len(methods) > 1:
                _fail(
                    "opening_count_serial_line_metadata_conflict",
                    "invalid_request",
                    "同一账户逐件 SN 的盘点方式和说明必须一致",
                )
            metadata = next(iter(methods), ("manual", "scope_full_set_zero", ""))
        else:
            if len(observed) > 1:
                _fail(
                    "opening_count_physical_dimension_duplicate",
                    "invalid_request",
                    "同一实物维度必须合并数量后提交",
                )
            qty = observed[0].value.counted_qty if observed else _ZERO
            serial_rows = ()
            metadata = (
                (
                    observed[0].value.count_method,
                    observed[0].value.reason_code,
                    observed[0].value.remark,
                )
                if observed
                else ("manual", "scope_full_set_zero", "")
            )
        _validate_quantity_for_policy(qty, policy)
        if policy.tracking_mode in {"serial", "lot_and_serial"}:
            if qty != Decimal(len(serial_rows)):
                _fail("opening_count_serial_quantity_mismatch", "invalid_request", "SN 物料必须逐件盘点且数量与 SN 数一致")
        for serial in serial_rows:
            if (
                serial.material_id != account.material_id
                or serial.lot_id != account.lot_id
                or serial.lifecycle_status != "active"
            ):
                _fail("opening_count_serial_binding_invalid", "invalid_request", "SN 与物料或批次维度不一致")
        prepared.append(
            _PreparedAccountCount(
                account=account,
                counted_qty=qty,
                serials=serial_rows,
                count_method=metadata[0],
                reason_code=metadata[1],
                remark=metadata[2],
                request_items=tuple(observed),
            )
        )
    return tuple(prepared), tuple(unexpected)


def _validate_current_scope_dimensions(
    db: Session,
    task: FormalStocktakeTask,
    scope: FormalStocktakeScope,
    location: StockLocation | None,
    now: datetime,
) -> None:
    if (
        location is None
        or location.status != "active"
        or location.location_type not in {"region", "personal"}
    ):
        _fail(
            "opening_count_scope_dimension_changed",
            "precondition_failed",
            "盘点范围的库位状态或类型已变化",
        )
    owner_stmt = select(Organization).where(
        Organization.id == scope.owner_org_id
    )
    legacy_row_locks = db.get_bind().dialect.name != "postgresql"
    if legacy_row_locks:
        owner_stmt = owner_stmt.with_for_update()
    owner = db.scalar(
        owner_stmt.execution_options(populate_existing=True)
    )
    if owner is None or owner.status != "active" or owner.org_type != "region_company":
        _fail(
            "opening_count_scope_owner_changed",
            "precondition_failed",
            "盘点资产所有组织已失效或类型已变化",
        )
    try:
        owner_in_region = _organization_descends_from(
            db,
            owner.id,
            task.region_org_id,
            lock_rows=legacy_row_locks,
        )
    except OpeningStocktakeError as exc:
        _fail(
            "opening_count_scope_owner_tree_invalid",
            "precondition_failed",
            "盘点资产所有组织树无法安全重证",
            cause=exc,
        )
    if not owner_in_region:
        _fail(
            "opening_count_scope_owner_outside_region",
            "precondition_failed",
            "盘点资产所有组织已不在任务区域的有效组织树内",
        )
    try:
        _require_location_in_region_tree(
            db,
            location,
            task.region_org_id,
            lock_rows=legacy_row_locks,
        )
    except OpeningStocktakeError as exc:
        _fail(
            "opening_count_location_tree_changed",
            "precondition_failed",
            "盘点库位已不在任务区域的有效组织树内",
            cause=exc,
        )
    custody_stmt = (
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
    )
    if legacy_row_locks:
        custody_stmt = custody_stmt.with_for_update()
    custodies = db.scalars(
        custody_stmt.execution_options(populate_existing=True)
    ).all()
    if len(custodies) > 1:
        _fail(
            "opening_count_custody_ambiguous",
            "service_unavailable",
            "盘点库位当前保管责任不唯一",
        )
    custody_person_id = custodies[0].custodian_person_id if custodies else None
    if (
        custody_person_id != scope.custodian_person_id_snapshot
        or location.custodian_person_id not in {None, custody_person_id}
        or (
            location.location_type == "personal"
            and (
                custody_person_id is None
                or location.custodian_person_id != custody_person_id
            )
        )
    ):
        _fail(
            "opening_count_custody_changed",
            "precondition_failed",
            "盘点库位保管责任已变化或未同步",
        )


def _read_material_identifier_rows(
    db: Session,
    *,
    identifier_type: str,
    raw: str,
    populate_existing: bool,
) -> tuple[tuple[FormalMaterial, ...], tuple[QrCode, ...]]:
    if identifier_type == "sku_code":
        statement = (
            select(FormalMaterial)
            .where(
                FormalMaterial.sku_code == raw,
                FormalMaterial.status == "active",
            )
            .order_by(FormalMaterial.id)
        )
        if populate_existing:
            statement = statement.execution_options(populate_existing=True)
        return tuple(db.scalars(statement).all()), ()
    if identifier_type != "qr_code":
        return (), ()
    mapping_statement = (
        select(QrCode)
        .where(
            QrCode.code == raw,
            QrCode.object_type == "material",
            QrCode.status == "active",
        )
        .order_by(QrCode.id)
    )
    if populate_existing:
        mapping_statement = mapping_statement.execution_options(
            populate_existing=True
        )
    mappings = tuple(db.scalars(mapping_statement).all())
    material_ids = tuple(row.object_id for row in mappings)
    if not material_ids:
        return (), mappings
    material_statement = (
        select(FormalMaterial)
        .where(
            FormalMaterial.id.in_(material_ids),
            FormalMaterial.status == "active",
        )
        .order_by(FormalMaterial.id)
    )
    if populate_existing:
        material_statement = material_statement.execution_options(
            populate_existing=True
        )
    return tuple(db.scalars(material_statement).all()), mappings


def _pre_read_command_material_references(
    db: Session,
    *,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    values: Sequence[OpeningPhysicalObservationInput],
) -> _CommandMaterialReferencePlan:
    if task.cutoff_at is None:
        _fail("opening_count_cutoff_missing", "service_unavailable", "期初盘点截止时点缺失")
    keys = tuple(
        sorted(
            {
                (row.material_identifier_type, row.material_identifier_raw)
                for row in values
                if row.material_identifier_type in {"sku_code", "qr_code"}
            }
        )
    )
    before: dict[
        tuple[str, str],
        tuple[tuple[uuid.UUID, ...], tuple[tuple[uuid.UUID, uuid.UUID], ...]],
    ] = {}
    material_ids = {
        row.material_id for row in scopes if row.material_id is not None
    }
    material_ids.update(
        row.material_id for row in values if row.material_id is not None
    )
    for key in keys:
        materials, mappings = _read_material_identifier_rows(
            db,
            identifier_type=key[0],
            raw=key[1],
            populate_existing=False,
        )
        signature = (
            tuple(row.id for row in materials),
            tuple((row.id, row.object_id) for row in mappings),
        )
        before[key] = signature
        material_ids.update(signature[0])
        material_ids.update(object_id for _mapping_id, object_id in signature[1])

    return _CommandMaterialReferencePlan(
        signatures=before,
        material_ids=tuple(sorted(material_ids, key=str)),
    )


def _read_locked_command_material_references(
    db: Session,
    plan: _CommandMaterialReferencePlan,
) -> dict[tuple[str, str], _LockedMaterialIdentifier]:
    locked: dict[tuple[str, str], _LockedMaterialIdentifier] = {}
    for key, before in plan.signatures.items():
        materials, mappings = _read_material_identifier_rows(
            db,
            identifier_type=key[0],
            raw=key[1],
            populate_existing=True,
        )
        after = (
            tuple(row.id for row in materials),
            tuple((row.id, row.object_id) for row in mappings),
        )
        if after != before:
            _fail(
                "opening_count_material_reference_changed",
                "service_unavailable",
                "现场物料主数据在锁定期间发生变化",
            )
        locked[key] = _LockedMaterialIdentifier(
            materials=materials,
            qr_mappings=mappings,
        )
    return locked


def _read_serial_identifier_rows(
    db: Session,
    *,
    material_id: uuid.UUID | None,
    identifier_type: str | None,
    raw: str,
    populate_existing: bool,
) -> tuple[tuple[InventorySerial, ...], tuple[QrCode, ...]]:
    predicate = (
        InventorySerial.serial_no == raw
        if identifier_type == "serial_no"
        else InventorySerial.qr_code == raw
        if identifier_type == "qr_code"
        else or_(InventorySerial.serial_no == raw, InventorySerial.qr_code == raw)
    )
    serial_statement = select(InventorySerial).where(
        predicate,
        InventorySerial.lifecycle_status == "active",
    )
    if material_id is not None:
        serial_statement = serial_statement.where(
            InventorySerial.material_id == material_id
        )
    serial_statement = serial_statement.order_by(InventorySerial.id)
    if populate_existing:
        serial_statement = serial_statement.execution_options(
            populate_existing=True
        )
    serials = tuple(db.scalars(serial_statement).all())

    if identifier_type != "qr_code":
        return serials, ()
    mapping_statement = (
        select(QrCode)
        .where(
            QrCode.code == raw,
            QrCode.object_type == "serial",
            QrCode.status == "active",
        )
        .order_by(QrCode.id)
    )
    if populate_existing:
        mapping_statement = mapping_statement.execution_options(
            populate_existing=True
        )
    return serials, tuple(db.scalars(mapping_statement).all())


def _pre_read_command_serial_references(
    db: Session,
    *,
    round_id: uuid.UUID,
    values: Sequence[OpeningPhysicalObservationInput],
) -> _CommandSerialReferencePlan:
    keys: set[tuple[str | None, str]] = set()
    for value in values:
        if value.serial_no_raw is None:
            continue
        keys.add((value.serial_identifier_type, value.serial_no_raw))

    existing_observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.round_id == round_id,
                StocktakeCountObservation.serial_no_raw.is_not(None),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    for row in existing_observations:
        assert row.serial_no_raw is not None
        keys.add((row.serial_identifier_type, row.serial_no_raw))

    ordered_keys = tuple(
        sorted(
            keys,
            key=lambda row: (
                row[0] or "",
                row[1],
            ),
        )
    )
    before: dict[
        tuple[str | None, str],
        tuple[tuple[uuid.UUID, ...], tuple[tuple[uuid.UUID, uuid.UUID], ...]],
    ] = {}
    serial_ids = set(
        db.scalars(
            select(StocktakeCountSerial.serial_id).where(
                StocktakeCountSerial.round_id == round_id
            )
        ).all()
    )
    serial_ids.update(
        row.serial_id
        for row in existing_observations
        if row.serial_id is not None
    )
    serial_ids.update(row.serial_id for row in values if row.serial_id is not None)
    for key in ordered_keys:
        serials, mappings = _read_serial_identifier_rows(
            db,
            material_id=None,
            identifier_type=key[0],
            raw=key[1],
            populate_existing=False,
        )
        signature = (
            tuple(row.id for row in serials),
            tuple((row.id, row.object_id) for row in mappings),
        )
        before[key] = signature
        serial_ids.update(signature[0])
        serial_ids.update(object_id for _mapping_id, object_id in signature[1])

    return _CommandSerialReferencePlan(
        signatures=before,
        serial_ids=tuple(sorted(serial_ids, key=str)),
    )


def _read_locked_command_serial_references(
    db: Session,
    *,
    round_id: uuid.UUID,
    values: Sequence[OpeningPhysicalObservationInput],
    locked_material_identifiers: Mapping[
        tuple[str, str], _LockedMaterialIdentifier
    ],
    plan: _CommandSerialReferencePlan,
) -> _LockedSerialReferences:
    base_identifiers: dict[
        tuple[str | None, str], _LockedSerialIdentifier
    ] = {}
    for key, before in plan.signatures.items():
        serials, mappings = _read_serial_identifier_rows(
            db,
            material_id=None,
            identifier_type=key[0],
            raw=key[1],
            populate_existing=True,
        )
        after = (
            tuple(row.id for row in serials),
            tuple((row.id, row.object_id) for row in mappings),
        )
        if after != before:
            _fail(
                "opening_count_serial_reference_changed",
                "service_unavailable",
                "现场 SN 主数据在锁定期间发生变化",
            )
        _require_unambiguous_serial_reference(
            locked_serials=serials,
            mappings=mappings,
        )
        base_identifiers[key] = _LockedSerialIdentifier(
            serials=serials,
            qr_mappings=mappings,
        )

    material_keys: set[tuple[uuid.UUID, str | None, str]] = set()
    for value in values:
        if value.serial_no_raw is None:
            continue
        material = _resolve_material_identifier(
            value,
            locked_material_identifiers,
        )
        if material is not None:
            material_keys.add(
                (material.id, value.serial_identifier_type, value.serial_no_raw)
            )
    existing_observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.round_id == round_id,
                StocktakeCountObservation.serial_no_raw.is_not(None),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    for row in existing_observations:
        if row.material_id is not None and row.serial_no_raw is not None:
            material_keys.add(
                (row.material_id, row.serial_identifier_type, row.serial_no_raw)
            )

    identifiers: dict[
        tuple[uuid.UUID | None, str | None, str], _LockedSerialIdentifier
    ] = {
        (None, identifier_type, raw): locked
        for (identifier_type, raw), locked in base_identifiers.items()
    }
    for material_id, identifier_type, raw in material_keys:
        base = base_identifiers.get((identifier_type, raw))
        if base is None:
            _fail(
                "opening_count_serial_reference_changed",
                "service_unavailable",
                "现场 SN 候选集合在锁定期间发生变化",
            )
        identifiers[(material_id, identifier_type, raw)] = _LockedSerialIdentifier(
            serials=tuple(
                row for row in base.serials if row.material_id == material_id
            ),
            qr_mappings=base.qr_mappings,
        )

    serial_rows = (
        tuple(
            db.scalars(
                select(InventorySerial)
                .where(InventorySerial.id.in_(plan.serial_ids))
                .order_by(InventorySerial.id)
                .execution_options(populate_existing=True)
            ).all()
        )
        if plan.serial_ids
        else ()
    )
    return _LockedSerialReferences(
        identifiers=identifiers,
        serials_by_id={row.id: row for row in serial_rows},
    )


def _resolve_physical_observations(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scope: FormalStocktakeScope,
    values: Sequence[OpeningPhysicalObservationInput],
    locked_material_identifiers: Mapping[
        tuple[str, str], _LockedMaterialIdentifier
    ],
    locked_serial_references: _LockedSerialReferences,
) -> tuple[_PreparedObservation, ...]:
    prepared: list[_PreparedObservation] = []
    seen_dimensions: set[str] = set()
    canonical_request_items = sorted(
        [
            (
                _canonical_json(_observation_evidence_document(value)),
                _observation_evidence_document(value),
            )
            for value in values
        ],
        key=lambda row: row[0],
    )
    if len({item[0] for item in canonical_request_items}) != len(
        canonical_request_items
    ):
        _fail(
            "opening_count_observation_duplicate",
            "invalid_request",
            "同一现场维度必须合并数量后提交",
        )
    request_coordinates = {
        canonical: (ordinal, _hash_document(document))
        for ordinal, (canonical, document) in enumerate(
            canonical_request_items,
            start=1,
        )
    }
    for supplied_value in values:
        request_document = _observation_evidence_document(supplied_value)
        request_ordinal, request_item_sha256 = request_coordinates[
            _canonical_json(request_document)
        ]
        material = _resolve_material_identifier(
            supplied_value,
            locked_material_identifiers,
        )
        material_qr_mapping_id = _resolved_material_qr_mapping_id(
            supplied_value,
            material,
            locked_material_identifiers,
        )
        value = replace(
            supplied_value,
            material_id=material.id if material is not None else None,
        )
        lot: InventoryLot | None = None
        serial: InventorySerial | None = None
        serial_qr_mapping_id: uuid.UUID | None = None
        policy: MaterialInventoryPolicy | None = None
        verification = "pending_verification"
        if material is not None:
            policy = _load_policy(db, material.id, task.cutoff_at)
            _validate_quantity_for_policy(value.counted_qty, policy)
            _validate_observation_tracking(value, policy)
            lot = _resolve_lot_identifier(db, material, value)
            value = replace(value, lot_id=lot.id if lot is not None else None)
            serial = _resolve_serial_identifier(
                material,
                lot,
                value,
                locked_serial_references,
            )
            serial_qr_mapping_id = _resolved_serial_qr_mapping_id(
                material,
                value,
                serial,
                locked_serial_references,
            )
            value = replace(
                value,
                serial_id=serial.id if serial is not None else None,
            )
            unresolved = (
                (value.lot_no_raw is not None and lot is None)
                or (value.serial_no_raw is not None and serial is None)
            )
            verification = "pending_verification" if unresolved else "verified"
        elif supplied_value.lot_id is not None or supplied_value.serial_id is not None:
            _fail(
                "opening_count_unresolved_master_binding_invalid",
                "invalid_request",
                "未解析物料不得猜测批次或 SN 主数据",
            )
        if value.serial_no_raw is not None:
            if value.counted_qty != Decimal("1"):
                _fail("opening_count_serial_quantity_mismatch", "invalid_request", "SN 必须逐件盘点")
        serial_alias_keys = _serial_alias_keys(
            locked_serial_references,
            identifier_type=value.serial_identifier_type,
            raw=value.serial_no_raw,
            resolved_serial=serial,
        )
        dimension = _observation_dimension_sha256(scope, value, verification)
        if dimension in seen_dimensions:
            _fail("opening_count_observation_duplicate", "invalid_request", "同一现场维度必须合并数量后提交")
        seen_dimensions.add(dimension)
        prepared.append(
            _PreparedObservation(
                value=value,
                material=material,
                lot=lot,
                serial=serial,
                verification_status=verification,
                dimension_sha256=dimension,
                request_ordinal=request_ordinal,
                request_item_sha256=request_item_sha256,
                material_qr_mapping_id=material_qr_mapping_id,
                serial_qr_mapping_id=serial_qr_mapping_id,
                serial_alias_keys=serial_alias_keys,
                policy=policy,
            )
        )
    _validate_round_serial_uniqueness(
        db,
        round_row.id,
        prepared,
        locked_serial_references,
    )
    prepared.sort(key=lambda row: row.dimension_sha256)
    return tuple(prepared)


def _resolved_material_qr_mapping_id(
    value: OpeningPhysicalObservationInput,
    material: FormalMaterial | None,
    locked_material_identifiers: Mapping[
        tuple[str, str], _LockedMaterialIdentifier
    ],
) -> uuid.UUID | None:
    if value.material_identifier_type != "qr_code":
        return None
    locked = locked_material_identifiers.get(
        (value.material_identifier_type, value.material_identifier_raw)
    )
    if locked is None:
        _fail(
            "opening_count_material_reference_changed",
            "service_unavailable",
            "现场物料主数据未进入锁定候选全集",
        )
    mappings = locked.qr_mappings
    if len(mappings) > 1:
        _fail(
            "opening_count_material_identifier_ambiguous",
            "precondition_failed",
            "现场物料标识无法唯一解析",
        )
    if not mappings:
        return None
    mapping = mappings[0]
    if material is None or mapping.object_id != material.id:
        _fail(
            "opening_count_material_qr_mapping_conflict",
            "precondition_failed",
            "现场物料二维码主数据映射冲突",
        )
    return mapping.id


def _resolved_serial_qr_mapping_id(
    material: FormalMaterial,
    value: OpeningPhysicalObservationInput,
    serial: InventorySerial | None,
    locked_serial_references: _LockedSerialReferences,
) -> uuid.UUID | None:
    if value.serial_no_raw is None or value.serial_identifier_type != "qr_code":
        return None
    locked = locked_serial_references.identifiers.get(
        (material.id, value.serial_identifier_type, value.serial_no_raw)
    )
    if locked is None:
        _fail(
            "opening_count_serial_reference_changed",
            "service_unavailable",
            "现场 SN 主数据未进入锁定候选全集",
        )
    mappings = locked.qr_mappings
    if not mappings:
        return None
    if len(mappings) != 1 or serial is None or mappings[0].object_id != serial.id:
        _fail(
            "opening_count_serial_qr_mapping_conflict",
            "precondition_failed",
            "SN 二维码主数据映射冲突",
        )
    return mappings[0].id


def _resolve_material_identifier(
    value: OpeningPhysicalObservationInput,
    locked_material_identifiers: Mapping[
        tuple[str, str], _LockedMaterialIdentifier
    ],
) -> FormalMaterial | None:
    supplied_id = value.material_id
    if value.material_identifier_type not in {"sku_code", "qr_code"}:
        if supplied_id is None:
            return None
        _fail(
            "opening_count_material_identifier_unproven",
            "invalid_request",
            "外部码或未知标识不得携带猜测的物料主数据标识",
        )
    locked = locked_material_identifiers.get(
        (value.material_identifier_type, value.material_identifier_raw)
    )
    if locked is None:
        _fail(
            "opening_count_material_reference_changed",
            "service_unavailable",
            "现场物料主数据未进入锁定候选全集",
        )
    candidates = list(locked.materials)
    if (
        value.material_identifier_type == "qr_code"
        and len(locked.qr_mappings) > 1
    ):
        _fail(
            "opening_count_material_identifier_ambiguous",
            "precondition_failed",
            "现场物料二维码映射不唯一",
        )

    if len(candidates) > 1:
        _fail(
            "opening_count_material_identifier_ambiguous",
            "precondition_failed",
            "现场物料标识无法唯一解析",
        )
    if not candidates:
        if supplied_id is not None:
            _fail(
                "opening_count_material_binding_invalid",
                "invalid_request",
                "现场物料标识与主数据不一致",
            )
        return None
    material = candidates[0]
    if supplied_id is not None and supplied_id != material.id:
        _fail(
            "opening_count_material_binding_invalid",
            "invalid_request",
            "现场物料标识与主数据不一致",
        )
    return material


def _resolve_lot_identifier(
    db: Session,
    material: FormalMaterial,
    value: OpeningPhysicalObservationInput,
) -> InventoryLot | None:
    if value.lot_no_raw is None:
        return None
    candidates = db.scalars(
        select(InventoryLot)
        .where(
            InventoryLot.material_id == material.id,
            InventoryLot.lot_no == value.lot_no_raw,
        )
        .order_by(InventoryLot.id)
        .execution_options(populate_existing=True)
    ).all()
    if len(candidates) > 1:
        _fail(
            "opening_count_lot_identifier_ambiguous",
            "precondition_failed",
            "现场批次映射不唯一",
        )
    if not candidates:
        if value.lot_id is not None:
            _fail(
                "opening_count_lot_binding_invalid",
                "invalid_request",
                "现场批次与主数据不一致",
            )
        return None
    lot = candidates[0]
    if value.lot_id is not None and value.lot_id != lot.id:
        _fail(
            "opening_count_lot_binding_invalid",
            "invalid_request",
            "现场批次与主数据不一致",
        )
    return lot


def _resolve_serial_identifier(
    material: FormalMaterial,
    lot: InventoryLot | None,
    value: OpeningPhysicalObservationInput,
    locked_serial_references: _LockedSerialReferences,
) -> InventorySerial | None:
    if value.serial_no_raw is None:
        return None
    if value.serial_identifier_type == "unknown":
        if value.serial_id is not None:
            _fail(
                "opening_count_serial_identifier_unproven",
                "invalid_request",
                "未知 SN 标识不得携带猜测的主数据标识",
            )
        return None
    locked = locked_serial_references.identifiers.get(
        (material.id, value.serial_identifier_type, value.serial_no_raw)
    )
    if locked is None:
        _fail(
            "opening_count_serial_reference_changed",
            "service_unavailable",
            "现场 SN 主数据未进入锁定候选全集",
        )
    candidates = list(locked.serials)
    mappings = list(locked.qr_mappings)
    if len(candidates) > 1:
        _fail(
            "opening_count_serial_identifier_ambiguous",
            "precondition_failed",
            "现场 SN 映射不唯一",
        )
    serial = candidates[0] if candidates else None
    if value.serial_identifier_type == "qr_code":
        if len(mappings) > 1 or (
            mappings
            and (serial is None or mappings[0].object_id != serial.id)
        ):
            _fail(
                "opening_count_serial_qr_mapping_conflict",
                "precondition_failed",
                "SN 二维码主数据映射冲突",
            )
    if serial is None:
        if value.serial_id is not None:
            _fail(
                "opening_count_serial_binding_invalid",
                "invalid_request",
                "现场 SN 与主数据不一致",
            )
        return None
    if value.serial_id is not None and value.serial_id != serial.id:
        _fail(
            "opening_count_serial_binding_invalid",
            "invalid_request",
            "现场 SN 与主数据不一致",
        )
    if serial.lot_id != (lot.id if lot is not None else None):
        _fail(
            "opening_count_serial_binding_invalid",
            "invalid_request",
            "现场 SN 与物料或批次维度不一致",
        )
    return serial


def _validate_round_serial_uniqueness(
    db: Session,
    round_id: uuid.UUID,
    prepared: Sequence[_PreparedObservation],
    locked_serial_references: _LockedSerialReferences,
) -> None:
    used_ids: set[uuid.UUID] = set()
    used_aliases: set[str] = set()
    existing_completions = tuple(
        db.scalars(
            select(StocktakeScopeCountCompletion)
            .where(StocktakeScopeCountCompletion.round_id == round_id)
            .order_by(StocktakeScopeCountCompletion.scope_id)
            .execution_options(populate_existing=True)
        ).all()
    )
    persisted_occurrences = _persisted_serial_alias_occurrences(
        existing_completions
    )
    if persisted_occurrences is None:
        _fail(
            "opening_count_serial_evidence_invalid",
            "service_unavailable",
            "已封存 SN 别名证据不完整",
        )
    for aliases in persisted_occurrences:
        if used_aliases.intersection(aliases):
            _fail(
                "opening_count_serial_evidence_invalid",
                "service_unavailable",
                "已封存 SN 别名证据重复",
            )
        used_aliases.update(aliases)
    existing_observations = db.scalars(
        select(StocktakeCountObservation)
        .where(
            StocktakeCountObservation.round_id == round_id,
            StocktakeCountObservation.serial_no_raw.is_not(None),
        )
        .execution_options(populate_existing=True)
    ).all()
    observation_serial_ids = {
        row.serial_id for row in existing_observations if row.serial_id is not None
    }
    serial_rows = db.execute(
        select(
            StocktakeCountSerial.serial_id,
            InventorySerial.serial_no,
            InventorySerial.qr_code,
        )
        .join(InventorySerial, InventorySerial.id == StocktakeCountSerial.serial_id)
        .where(StocktakeCountSerial.round_id == round_id)
        .execution_options(populate_existing=True)
    ).all()
    for serial_id, serial_no, qr_code in serial_rows:
        used_ids.add(serial_id)
        used_aliases.update(
            {_fold_serial_alias(serial_no), _fold_serial_alias(qr_code)}
        )
    existing_serials = locked_serial_references.serials_by_id
    for observation in existing_observations:
        used_aliases.update(
            _unresolved_serial_aliases(
                locked_serial_references,
                observation.serial_identifier_type,
                observation.serial_no_raw,
            )
        )
        if observation.serial_id is not None:
            used_ids.add(observation.serial_id)
            serial = existing_serials.get(observation.serial_id)
            if serial is None:
                _fail(
                    "opening_count_serial_evidence_invalid",
                    "service_unavailable",
                    "已封存 SN 证据缺失",
                )
            used_aliases.update(
                {
                    _fold_serial_alias(serial.serial_no),
                    _fold_serial_alias(serial.qr_code),
                }
            )
    for row in prepared:
        if row.value.serial_no_raw is None:
            continue
        aliases = set(row.serial_alias_keys)
        if (
            row.serial is not None
            and row.serial.id in used_ids
            or aliases & used_aliases
        ):
            _fail(
                "opening_count_serial_duplicate",
                "invalid_request",
                "同一 SN 不得跨范围或通过别名重复盘点",
            )
        if row.serial is not None:
            used_ids.add(row.serial.id)
        used_aliases.update(aliases)


def _unresolved_serial_aliases(
    locked_serial_references: _LockedSerialReferences,
    identifier_type: str | None,
    raw: str,
) -> set[str]:
    return set(
        _serial_alias_keys(
            locked_serial_references,
            identifier_type=identifier_type,
            raw=raw,
            resolved_serial=None,
        )
    )


def _serial_alias_keys(
    locked_serial_references: _LockedSerialReferences,
    *,
    identifier_type: str | None,
    raw: str | None,
    resolved_serial: InventorySerial | None,
) -> tuple[str, ...]:
    """Snapshot the exact aliases used by round-wide SN uniqueness."""

    if raw is None:
        if identifier_type is not None or resolved_serial is not None:
            _fail(
                "opening_count_serial_evidence_invalid",
                "service_unavailable",
                "现场 SN 别名证据不完整",
            )
        return ()
    locked = locked_serial_references.identifiers.get(
        (None, identifier_type, raw)
    )
    if locked is None:
        _fail(
            "opening_count_serial_reference_changed",
            "service_unavailable",
            "现场 SN 别名未进入锁定候选全集",
        )
    _require_unambiguous_serial_reference(
        locked_serials=locked.serials,
        mappings=locked.qr_mappings,
    )
    aliases = {_fold_serial_alias(raw)}
    candidate = resolved_serial
    if candidate is None and len(locked.serials) == 1:
        candidate = locked.serials[0]
    if candidate is not None:
        aliases.update(
            {
                _fold_serial_alias(candidate.serial_no),
                _fold_serial_alias(candidate.qr_code),
            }
        )
    return tuple(sorted(aliases))


def _require_unambiguous_serial_reference(
    *,
    locked_serials: Sequence[InventorySerial],
    mappings: Sequence[QrCode],
) -> None:
    """Reject one raw identifier that can denote multiple serial identities."""

    identity_ids = {row.id for row in locked_serials}
    identity_ids.update(row.object_id for row in mappings)
    if len(identity_ids) > 1:
        _fail(
            "opening_count_serial_reference_ambiguous",
            "precondition_failed",
            "现场 SN 标识对应多个主数据身份，无法安全盘点",
        )


def _fold_serial_alias(value: str) -> str:
    """Fold only ASCII A-Z so Python and PostgreSQL use identical keys."""

    return value.translate(_ASCII_IDENTIFIER_FOLD_TABLE)


def _persisted_serial_alias_keys(
    value: object,
    *,
    serial_no_raw: object,
) -> tuple[str, ...] | None:
    """Validate one immutable, canonical SN alias snapshot."""

    if not isinstance(value, list) or len(value) > 3 or any(
        not isinstance(alias, str)
        or not alias
        or len(alias) > 250
        or _fold_serial_alias(alias) != alias
        for alias in value
    ):
        return None
    aliases = tuple(value)
    if list(aliases) != sorted(set(aliases)):
        return None
    if serial_no_raw is None:
        return aliases if not aliases else None
    if not isinstance(serial_no_raw, str):
        return None
    return (
        aliases
        if _fold_serial_alias(serial_no_raw) in aliases
        else None
    )


def _persisted_serial_alias_occurrences(
    completions: Sequence[StocktakeScopeCountCompletion],
) -> tuple[tuple[str, ...], ...] | None:
    occurrences: list[tuple[str, ...]] = []
    for completion in completions:
        request = completion.request_jsonb
        resolution = completion.request_resolution_jsonb
        if not isinstance(request, dict) or not isinstance(resolution, dict):
            return None
        request_items = request.get("physical_observations")
        resolution_items = resolution.get("items")
        if (
            not isinstance(request_items, list)
            or not isinstance(resolution_items, list)
            or len(request_items) != len(resolution_items)
        ):
            return None
        for ordinal, (request_item, resolution_item) in enumerate(
            zip(request_items, resolution_items, strict=True),
            start=1,
        ):
            if (
                not isinstance(request_item, dict)
                or not isinstance(resolution_item, dict)
                or type(resolution_item.get("request_ordinal")) is not int
                or resolution_item.get("request_ordinal") != ordinal
            ):
                return None
            raw = request_item.get("serial_no_raw")
            identifier_type = request_item.get("serial_identifier_type")
            if raw is None:
                if identifier_type is not None:
                    return None
            elif (
                not isinstance(raw, str)
                or not isinstance(identifier_type, str)
                or identifier_type not in _SERIAL_IDENTIFIER_TYPES
            ):
                return None
            aliases = _persisted_serial_alias_keys(
                resolution_item.get("serial_alias_keys"),
                serial_no_raw=raw,
            )
            if aliases is None:
                return None
            occurrences.append(aliases)
    return tuple(occurrences)


def _load_policy(
    db: Session, material_id: uuid.UUID, cutoff_at: datetime | None
) -> MaterialInventoryPolicy:
    if cutoff_at is None:
        _fail("opening_count_cutoff_missing", "service_unavailable", "期初盘点截止时点缺失")
    rows = db.scalars(
        select(MaterialInventoryPolicy)
        .where(
            MaterialInventoryPolicy.material_id == material_id,
            MaterialInventoryPolicy.effective_from <= cutoff_at,
            or_(
                MaterialInventoryPolicy.effective_to.is_(None),
                MaterialInventoryPolicy.effective_to > cutoff_at,
            ),
        )
        .order_by(MaterialInventoryPolicy.id)
        .execution_options(populate_existing=True)
    ).all()
    if len(rows) != 1:
        _fail("opening_count_policy_ambiguous", "precondition_failed", "截止时点物料追踪策略不唯一")
    return rows[0]


def _validate_quantity_for_policy(qty: Decimal, policy: MaterialInventoryPolicy) -> None:
    if _decimal_scale(qty) > policy.quantity_scale or (
        not policy.allow_fraction and qty != qty.to_integral_value()
    ):
        _fail("opening_count_quantity_scale_invalid", "invalid_request", "盘点数量不符合物料数量精度策略")


def _validate_observation_tracking(
    value: OpeningPhysicalObservationInput, policy: MaterialInventoryPolicy
) -> None:
    has_lot = value.lot_no_raw is not None
    has_serial = value.serial_no_raw is not None
    expected = {
        "none": (False, False),
        "lot": (True, False),
        "serial": (False, True),
        "lot_and_serial": (True, True),
    }[policy.tracking_mode]
    if (has_lot, has_serial) != expected:
        _fail("opening_count_tracking_dimension_invalid", "invalid_request", "现场批次/SN 维度不符合截止追踪策略")


def _account_matches_observation(
    account: StockAccount,
    scope: FormalStocktakeScope,
    location_type: str,
    value: OpeningPhysicalObservationInput,
) -> bool:
    return (
        account.owner_org_id == scope.owner_org_id
        and account.location_id == scope.location_id
        and (
            location_type == "region"
            or account.custodian_person_id == scope.custodian_person_id_snapshot
        )
        and account.material_id == value.material_id
        and account.condition_code == value.condition_code
        and account.availability_bucket == value.availability_bucket
        and account.lot_id == value.lot_id
    )


def _plan_round_assignment_evidence(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    freezes: Sequence[InventoryFreeze],
    allow_downstream: bool,
    disposition_resolutions: Mapping[uuid.UUID, object],
) -> tuple[dict[uuid.UUID, StocktakeRecountScopeAssignment], object | None]:
    if round_row.round_type == "initial":
        return {}, None
    try:
        from .opening_stocktake_recount import (
            OpeningStocktakeRecountError,
            _opening_recount_assignments_from_plan,
            _plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph,
        )
    except (ImportError, AttributeError) as exc:
        _fail(
            "opening_count_recount_validator_unavailable",
            "service_unavailable",
            "复盘轮次因果验证器不可用",
            cause=exc,
        )
    try:
        plan = _plan_opening_recount_round_assignment_evidence_from_prelocked_reference_graph(
            db,
            task=task,
            round_row=round_row,
            scopes=scopes,
            freezes=freezes,
            disposition_resolutions=disposition_resolutions,
        )
        rows = _opening_recount_assignments_from_plan(db, plan=plan)
    except OpeningStocktakeRecountError as exc:
        _fail(
            "opening_count_recount_evidence_invalid",
            "precondition_failed",
            "复盘轮次因果或执行人快照无法通过验证",
            cause=exc,
        )
    if (
        not isinstance(rows, dict)
        or set(rows) != {scope.id for scope in scopes}
        or any(
            not isinstance(row, StocktakeRecountScopeAssignment)
            or row.scope_id != scope_id
            or row.task_id != task.id
            or row.recount_case_id != round_row.recount_case_id
            for scope_id, row in rows.items()
        )
    ):
        _fail(
            "opening_count_recount_evidence_invalid",
            "precondition_failed",
            "复盘轮次执行人快照集合不完整或不一致",
        )
    return rows, plan


def _validate_round_assignment_plan(
    db: Session,
    *,
    plan: object | None,
    audit_proof: object,
) -> dict[uuid.UUID, StocktakeRecountScopeAssignment]:
    if plan is None:
        return {}
    try:
        from .opening_stocktake_recount import (
            OpeningStocktakeRecountError,
            _validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph,
        )

        return _validate_opening_recount_round_assignment_evidence_from_prelocked_task_graph(
            db,
            plan=plan,
            audit_proof=audit_proof,
        )
    except (ImportError, AttributeError, OpeningStocktakeRecountError) as exc:
        _fail(
            "opening_count_recount_evidence_invalid",
            "precondition_failed",
            "复盘轮次因果或执行人快照无法从审计预锁图重证",
            cause=exc,
        )


def _validate_round_assignment_evidence(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    freezes: Sequence[InventoryFreeze],
    allow_downstream: bool,
    disposition_resolutions: Mapping[uuid.UUID, object],
) -> dict[uuid.UUID, StocktakeRecountScopeAssignment]:
    """Compatibility structural capture for pre-audit internal callers only."""

    rows, _plan = _plan_round_assignment_evidence(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        freezes=freezes,
        allow_downstream=allow_downstream,
        disposition_resolutions=disposition_resolutions,
    )
    return rows


def _authorize_round_scope_actor(
    db: Session,
    actor: FormalPrincipal,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    freezes: Sequence[InventoryFreeze],
    scope: FormalStocktakeScope,
    location: StockLocation,
    task_region_org_id: uuid.UUID,
    round_assignments: Mapping[uuid.UUID, StocktakeRecountScopeAssignment],
    allow_downstream: bool,
) -> tuple[ScopeGrant, StocktakeRecountScopeAssignment | None]:
    # ``scopes``/``freezes``/``allow_downstream`` are deliberately part of
    # this boundary so callers cannot authorize one isolated snapshot without
    # first re-proving the complete recount assignment set.
    del scopes, freezes, allow_downstream
    if round_row.round_type == "initial":
        return (
            _authorize_exact_scope_actor(
                db,
                actor,
                scope,
                location=location,
                task_region_org_id=task_region_org_id,
            ),
            None,
        )
    expected = round_assignments.get(scope.id)
    if expected is None:
        _fail(
            "opening_count_recount_assignment_missing",
            "precondition_failed",
            "复盘范围缺少冻结执行人快照",
        )
    grant = _authorize_recount_scope_actor(
        db,
        actor,
        task=task,
        scope=scope,
        location=location,
        expected=expected,
    )
    return grant, expected


def _authorize_recount_scope_actor(
    db: Session,
    actor: FormalPrincipal,
    *,
    task: FormalStocktakeTask,
    scope: FormalStocktakeScope,
    location: StockLocation,
    expected: StocktakeRecountScopeAssignment,
) -> ScopeGrant:
    if (
        actor.user_id != expected.assignee_user_id
        or actor.person_id != expected.assignee_person_id
        or actor.authorization_version < expected.authorization_version
    ):
        _fail(
            "opening_count_not_round_assignee",
            "forbidden",
            "只有本轮复盘执行人快照指定账号可以提交该范围",
        )
    if expected.role_code == "technician":
        shape_valid = bool(
            expected.scope_type == "person"
            and location.location_type == "personal"
            and scope.custodian_person_id_snapshot is not None
            and location.custodian_person_id
            == scope.custodian_person_id_snapshot
            and actor.person_id == scope.custodian_person_id_snapshot
            and _same_uuid(
                expected.scope_id_snapshot,
                scope.custodian_person_id_snapshot,
            )
        )
        target_type = "person"
        target_id = str(scope.custodian_person_id_snapshot)
    elif expected.role_code == "provincial_manager":
        shape_valid = bool(
            expected.scope_type == "organization"
            and _same_uuid(expected.scope_id_snapshot, scope.owner_org_id)
        )
        target_type = "organization"
        target_id = str(scope.owner_org_id)
    elif expected.role_code == "admin":
        shape_valid = bool(
            expected.scope_type == "national"
            and expected.scope_id_snapshot == "*"
        )
        target_type = "organization"
        target_id = str(scope.owner_org_id)
    else:
        shape_valid = False
        target_type = "organization"
        target_id = str(scope.owner_org_id)
    if not shape_valid:
        _fail(
            "opening_count_recount_assignment_scope_invalid",
            "precondition_failed",
            "复盘执行人角色或范围快照与盘点范围不一致",
        )
    if expected.role_code != "technician":
        try:
            _authorize_scope_dimensions(
                db,
                actor=actor,
                task_region_org_id=task.region_org_id,
                owner_org_id=scope.owner_org_id,
                location_owner_org_id=location.owner_org_id,
            )
        except OpeningStocktakeError as exc:
            _fail(
                "opening_count_scope_dimension_forbidden",
                "forbidden",
                "当前复盘执行人未覆盖资产与库位组织维度",
                cause=exc,
            )
    candidates = [
        row
        for row in actor.assignments
        if row.assignment_id == expected.assignee_role_assignment_id
        and row.role_code == expected.role_code
        and row.scope_type == expected.scope_type
        and row.scope_id == expected.scope_id_snapshot
    ]
    if len(candidates) != 1:
        _fail(
            "opening_count_recount_assignment_not_current",
            "precondition_failed",
            "复盘执行人快照绑定的正式授权当前无效",
        )
    grant = candidates[0]
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
        allowed = actor.allows(
            db,
            "stocktake",
            "count",
            target_scope_type=target_type,
            target_scope_id=target_id,
        ) and selected.allows(
            db,
            "stocktake",
            "count",
            target_scope_type=target_type,
            target_scope_id=target_id,
        )
    except FormalAccessError as exc:
        _fail(
            "opening_count_recount_authorization_invalid",
            "forbidden",
            "复盘执行人当前正式权限图无效",
            cause=exc,
        )
    if not allowed:
        _fail(
            "opening_count_scope_forbidden",
            "forbidden",
            "复盘执行人快照绑定授权当前没有该范围盘点许可",
        )
    return grant


def _authorize_exact_scope_actor(
    db: Session,
    actor: FormalPrincipal,
    scope: FormalStocktakeScope,
    *,
    location: StockLocation,
    task_region_org_id: uuid.UUID,
) -> ScopeGrant:
    if actor.user_id != scope.assignee_user_id:
        _fail("opening_count_not_frozen_assignee", "forbidden", "只有冻结范围指定执行人可以提交实盘")
    candidates: list[ScopeGrant] = []
    if location.location_type == "personal":
        if actor.person_id != scope.custodian_person_id_snapshot:
            _fail("opening_count_personal_actor_mismatch", "forbidden", "个人仓只能由本人执行初盘")
        candidates = [
            row
            for row in actor.assignments
            if row.role_code == "technician"
            and row.scope_type == "person"
            and _same_uuid(row.scope_id, actor.person_id)
        ]
        target_type, target_id = "person", str(actor.person_id)
    else:
        try:
            _authorize_scope_dimensions(
                db,
                actor=actor,
                task_region_org_id=task_region_org_id,
                owner_org_id=scope.owner_org_id,
                location_owner_org_id=location.owner_org_id,
            )
        except OpeningStocktakeError as exc:
            _fail(
                "opening_count_scope_dimension_forbidden",
                "forbidden",
                "当前执行人未同时覆盖资产所有组织与库位物理组织",
                cause=exc,
            )
        candidates = [
            row
            for row in actor.assignments
            if (
                (
                    row.role_code == "provincial_manager"
                    and row.scope_type == "organization"
                    and _same_uuid(row.scope_id, task_region_org_id)
                    and scope.owner_org_id == task_region_org_id
                )
                or (
                    row.role_code == "admin"
                    and row.scope_type == "national"
                    and row.scope_id == "*"
                )
            )
        ]
        candidates.sort(key=lambda row: (row.role_code == "admin", str(row.assignment_id)))
        target_type, target_id = "organization", str(task_region_org_id)
    if not actor.allows(
        db,
        "stocktake",
        "count",
        target_scope_type=target_type,
        target_scope_id=target_id,
    ):
        _fail("opening_count_scope_forbidden", "forbidden", "当前正式权限不能提交该盘点范围")
    candidates = [
        row
        for row in candidates
        if any(
            entitlement.assignment_id == row.assignment_id
            and entitlement.resource == "stocktake"
            and entitlement.action == "count"
            and entitlement.field_code == ""
            and entitlement.effect == "allow"
            for entitlement in actor.entitlements
        )
    ]
    if not candidates:
        _fail(
            "opening_count_scope_forbidden",
            "forbidden",
            "所选盘点授权自身没有该范围的盘点许可",
        )
    return candidates[0]


def _lock_current_assignment(
    db: Session,
    actor: FormalPrincipal,
    grant: ScopeGrant,
    now: datetime,
    *,
    lock_rows: bool = True,
) -> RoleAssignment:
    statement = select(RoleAssignment).where(
        RoleAssignment.id == grant.assignment_id
    )
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
        _fail("opening_count_assignment_not_current", "precondition_failed", "盘点授权已变化，请重新读取后再提交")
    return row


def _require_round_counting_state(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    now: datetime,
) -> None:
    round_shape_valid = bool(
        (round_row.round_no == 1 and round_row.round_type == "initial")
        or (round_row.round_no > 1 and round_row.round_type == "recount")
    )
    if (
        task.task_type != "opening"
        or task.status != "counting"
        or task.current_round_no != round_row.round_no
        or not round_shape_valid
        or round_row.status != "counting"
        or not scopes
        or task.cutoff_at is None
        or task.cutoff_ledger_cursor is None
        or _as_utc(round_row.started_at) > now
        or _as_utc(task.cutoff_at) > now
    ):
        _fail("opening_count_state_invalid", "precondition_failed", "期初盘点任务或当前轮次不在可提交状态")


def _scope_has_partial_evidence(
    db: Session, task_id: uuid.UUID, round_id: uuid.UUID, scope_id: uuid.UUID
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


def _capture_opening_count_replay_evidence(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    requested_completion: StocktakeScopeCountCompletion,
    *,
    round_assignments: Mapping[
        uuid.UUID, StocktakeRecountScopeAssignment
    ] | None = None,
) -> tuple[uuid.UUID, ...]:
    assignment_map = dict(round_assignments or {})
    if (
        round_row.round_type == "recount"
        and set(assignment_map) != {row.id for row in scopes}
    ) or (round_row.round_type == "initial" and assignment_map):
        _invalid_replay()
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
    if not _persisted_round_serial_uniqueness_valid(
        db,
        task_id=task.id,
        round_id=round_row.id,
        completions=completions,
    ):
        _invalid_replay()
    if (
        all(row.id != requested_completion.id for row in completions)
        or len(completions) > len(scopes)
    ):
        _invalid_replay()
    completion_scope_ids = {row.scope_id for row in completions}
    child_scope_ids = set(
        db.scalars(
            select(StocktakeCountLine.scope_id)
            .where(
                StocktakeCountLine.task_id == task.id,
                StocktakeCountLine.round_id == round_row.id,
            )
            .distinct()
        ).all()
    ).union(
        db.scalars(
            select(StocktakeCountObservation.scope_id)
            .where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.round_id == round_row.id,
            )
            .distinct()
        ).all()
    )
    if not child_scope_ids.issubset(completion_scope_ids):
        _invalid_replay()
    scope_by_id = {row.id: row for row in scopes}
    audit_event_ids: list[uuid.UUID] = []
    for completion in completions:
        scope = scope_by_id.get(completion.scope_id)
        if scope is None:
            _invalid_replay()
        _validate_completion_evidence(
            db,
            task,
            round_row,
            scope,
            completion,
            expected_recount_assignment=assignment_map.get(scope.id),
        )

    submission = db.scalar(
        select(StocktakeRoundSubmission)
        .where(
            StocktakeRoundSubmission.task_id == task.id,
            StocktakeRoundSubmission.round_id == round_row.id,
        )
        .execution_options(populate_existing=True)
    )
    difference_completion = db.scalar(
        select(StocktakeDifferenceSetCompletion)
        .where(
            StocktakeDifferenceSetCompletion.task_id == task.id,
            StocktakeDifferenceSetCompletion.round_id == round_row.id,
        )
        .execution_options(populate_existing=True)
    )
    if round_row.status == "counting":
        if (
            submission is not None
            or difference_completion is not None
            or task.status != "counting"
            or task.current_round_no != round_row.round_no
            or len(completions) >= len(scopes)
            or db.scalar(
                select(func.count())
                .select_from(StocktakeDifference)
                .where(StocktakeDifference.round_id == round_row.id)
            )
        ):
            _invalid_replay()
    elif round_row.status in {"submitted", "superseded"}:
        if (
            submission is None
            or difference_completion is None
            or task.status not in _DOWNSTREAM_REPLAY_TASK_STATUSES
            or task.current_round_no < round_row.round_no
            or len(completions) != len(scopes)
            or round_row.submitted_at is None
            or task.submitted_at is None
        ):
            _invalid_replay()
        sealing_completion = next(
            (
                row
                for row in completions
                if row.id == submission.sealing_completion_id
            ),
            None,
        )
        if sealing_completion is None:
            _invalid_replay()
        round_manifest = _round_manifest_sha256(
            task.id,
            round_row.id,
            completions,
            submission.sealing_completion_id,
        )
        submission_assignment = db.get(
            RoleAssignment, submission.submitted_role_assignment_id
        )
        submission_user = db.get(User, submission.submitted_by_user_id)
        submitted_at = _as_utc(submission.submitted_at)
        if (
            submission.scope_count != len(completions)
            or submission.zero_scope_count
            != sum(1 for row in completions if row.zero_confirmed)
            or submission.count_line_count
            != sum(row.count_line_count for row in completions)
            or submission.observation_line_count
            != sum(row.observation_line_count for row in completions)
            or submission.serial_count != sum(row.serial_count for row in completions)
            or submission.total_counted_qty
            != sum((row.total_counted_qty for row in completions), start=_ZERO)
            or submission.round_manifest_sha256 != round_manifest
            or submission.count_manifest_sha256
            != _persisted_count_manifest_sha256(db, task, round_row)
            or submission.request_sha256
            != _hash_document(
                {
                    "count_manifest_sha256": submission.count_manifest_sha256,
                    "round_id": str(round_row.id),
                    "round_manifest_sha256": round_manifest,
                    "schema": "cloud_oam.opening_stocktake.round_submission_request.v3",
                    "sealing_completion_id": str(
                        submission.sealing_completion_id
                    ),
                }
            )
            or submission.idempotency_key_hash
            != _event_hash("round-submission", round_row.id, task.id)
            or submission_assignment is None
            or submission_user is None
            or submission_assignment.user_id != submission.submitted_by_user_id
            or submission_user.person_id != submission.submitted_by_person_id
            or submission_user.authorization_version
            < submission.authorization_version
            or _as_utc(submission_assignment.valid_from) > submitted_at
            or (
                submission_assignment.valid_to is not None
                and submitted_at >= _as_utc(submission_assignment.valid_to)
            )
            or (
                submission_assignment.revoked_at is not None
                and submitted_at >= _as_utc(submission_assignment.revoked_at)
            )
            or _as_utc(submission.created_at) != submitted_at
            or sealing_completion.task_id != task.id
            or sealing_completion.round_id != round_row.id
            or sealing_completion.completed_by_user_id
            != submission.submitted_by_user_id
            or sealing_completion.completed_by_person_id
            != submission.submitted_by_person_id
            or sealing_completion.completed_role_assignment_id
            != submission.submitted_role_assignment_id
            or _as_utc(sealing_completion.completed_at) != submitted_at
            or round_row.count_manifest_sha256
            != _persisted_count_manifest_sha256(db, task, round_row)
            or round_row.submitted_by_user_id != submission.submitted_by_user_id
            or _as_utc(round_row.submitted_at) != _as_utc(submission.submitted_at)
            or _as_utc(task.submitted_at) < _as_utc(submission.submitted_at)
            or (
                task.current_round_no == round_row.round_no
                and _as_utc(task.submitted_at)
                != _as_utc(submission.submitted_at)
            )
        ):
            _invalid_replay()
        _validate_initial_difference_set(db, task, round_row)
        _validate_difference_set_completion(
            db,
            task=task,
            round_row=round_row,
            submission=submission,
            completion=difference_completion,
        )
        sealing_completion_id = sealing_completion.id
    else:
        _invalid_replay()
    if round_row.status == "counting":
        sealing_completion_id = None

    event_context = _round_event_context(task, round_row)
    scope_state_reason, round_state_reason = _round_state_reasons(round_row)
    valid_round_ids = set(
        db.scalars(
            select(StocktakeRound.id).where(StocktakeRound.task_id == task.id)
        ).all()
    )
    scope_aggregate_ids = {str(row.id) for row in scopes}
    _validate_state_event_exact_set(
        db,
        aggregate_type="stocktake_scope",
        aggregate_ids=scope_aggregate_ids,
        reason=scope_state_reason,
        round_id=round_row.id,
        valid_round_ids=valid_round_ids,
        expected_idempotency_keys={
            _event_key("scope-state", round_row.id, row.scope_id)
            for row in completions
        },
    )
    _validate_outbox_event_exact_set(
        db,
        aggregate_type="stocktake_scope",
        aggregate_ids=scope_aggregate_ids,
        event_type="stocktake.opening.scope_count_completed",
        round_id=round_row.id,
        valid_round_ids=valid_round_ids,
        expected_idempotency_keys={
            _event_key("scope-outbox", round_row.id, row.scope_id)
            for row in completions
        },
    )
    submitted = round_row.status in {"submitted", "superseded"}
    _validate_state_event_exact_set(
        db,
        aggregate_type="stocktake_round",
        aggregate_ids={str(round_row.id)},
        reason=round_state_reason,
        round_id=round_row.id,
        valid_round_ids=valid_round_ids,
        expected_idempotency_keys=(
            {_event_key("round-state", round_row.id, task.id)}
            if submitted
            else set()
        ),
    )
    _validate_state_event_exact_set(
        db,
        aggregate_type="stocktake_task",
        aggregate_ids={str(task.id)},
        reason=round_state_reason,
        round_id=round_row.id,
        valid_round_ids=valid_round_ids,
        expected_idempotency_keys=(
            {_event_key("task-state", round_row.id, task.id)}
            if submitted
            else set()
        ),
    )
    _validate_outbox_event_exact_set(
        db,
        aggregate_type="stocktake_round",
        aggregate_ids={str(round_row.id)},
        event_type="stocktake.opening.round_submitted",
        round_id=round_row.id,
        valid_round_ids=valid_round_ids,
        expected_idempotency_keys=(
            {_event_key("round-outbox", round_row.id, task.id)}
            if submitted
            else set()
        ),
    )
    _validate_audit_event_exact_set(
        db,
        aggregate_type="stocktake_scope",
        aggregate_ids=scope_aggregate_ids,
        action="stocktake.opening.scope_count_completed",
        round_id=round_row.id,
        valid_round_ids=valid_round_ids,
        expected_aggregate_ids={str(row.scope_id) for row in completions},
    )
    _validate_audit_event_exact_set(
        db,
        aggregate_type="stocktake_round",
        aggregate_ids={str(round_row.id)},
        action="stocktake.opening.round_submitted",
        round_id=round_row.id,
        valid_round_ids=valid_round_ids,
        expected_aggregate_ids={str(round_row.id)} if submitted else set(),
    )

    for completion in completions:
        completed_at = _as_utc(completion.completed_at)
        scope_is_sealing = completion.id == sealing_completion_id
        scope_has_pending = bool(
            db.scalar(
                select(func.count())
                .select_from(StocktakeCountObservation)
                .where(
                    StocktakeCountObservation.task_id == task.id,
                    StocktakeCountObservation.round_id == round_row.id,
                    StocktakeCountObservation.scope_id == completion.scope_id,
                    StocktakeCountObservation.verification_status
                    == "pending_verification",
                )
            )
        )
        _validate_state_evidence(
            db,
            idempotency_key=_event_key(
                "scope-state", round_row.id, completion.scope_id
            ),
            aggregate_type="stocktake_scope",
            aggregate_id=str(completion.scope_id),
            from_status="counting",
            to_status="completed",
            reason=scope_state_reason,
            actor_id=completion.completed_by_user_id,
            occurred_at=completed_at,
            metadata={
                **event_context,
                "zero_confirmed": completion.zero_confirmed,
            },
        )
        _validate_outbox_evidence(
            db,
            idempotency_key=_event_key(
                "scope-outbox", round_row.id, completion.scope_id
            ),
            event_type="stocktake.opening.scope_count_completed",
            aggregate_type="stocktake_scope",
            aggregate_id=str(completion.scope_id),
            payload={
                **event_context,
                "round_sealed": scope_is_sealing,
                "scope_id": str(completion.scope_id),
            },
            available_at=completed_at,
        )
        audit_event_ids.append(
            _validate_audit_evidence(
                db,
                actor_user_id=completion.completed_by_user_id,
                action="stocktake.opening.scope_count_completed",
                aggregate_type="stocktake_scope",
                aggregate_id=str(completion.scope_id),
                after_jsonb={
                    **event_context,
                    "has_pending_verification": scope_has_pending,
                    "round_sealed": scope_is_sealing,
                    "zero_confirmed": completion.zero_confirmed,
                },
                occurred_at=completed_at,
                round_id=round_row.id,
                valid_round_ids=valid_round_ids,
            )
        )
    if round_row.status in {"submitted", "superseded"}:
        _validate_state_evidence(
            db,
            idempotency_key=_event_key("round-state", round_row.id, task.id),
            aggregate_type="stocktake_round",
            aggregate_id=str(round_row.id),
            from_status="counting",
            to_status="submitted",
            reason=round_state_reason,
            actor_id=submission.submitted_by_user_id,
            occurred_at=submitted_at,
            metadata=_round_submission_state_metadata(
                task,
                round_row,
                aggregate_type="stocktake_round",
            ),
        )
        _validate_outbox_evidence(
            db,
            idempotency_key=_event_key("round-outbox", round_row.id, task.id),
            event_type="stocktake.opening.round_submitted",
            aggregate_type="stocktake_round",
            aggregate_id=str(round_row.id),
            payload=event_context,
            available_at=submitted_at,
        )
        audit_event_ids.append(
            _validate_audit_evidence(
                db,
                actor_user_id=submission.submitted_by_user_id,
                action="stocktake.opening.round_submitted",
                aggregate_type="stocktake_round",
                aggregate_id=str(round_row.id),
                after_jsonb=event_context,
                occurred_at=submitted_at,
                round_id=round_row.id,
                valid_round_ids=valid_round_ids,
            )
        )
        _validate_state_evidence(
            db,
            idempotency_key=_event_key("task-state", round_row.id, task.id),
            aggregate_type="stocktake_task",
            aggregate_id=str(task.id),
            from_status="counting",
            to_status="submitted",
            reason=round_state_reason,
            actor_id=submission.submitted_by_user_id,
            occurred_at=submitted_at,
            metadata=_round_submission_state_metadata(
                task,
                round_row,
                aggregate_type="stocktake_task",
            ),
        )
    return tuple(sorted(set(audit_event_ids), key=str))


def _persisted_round_serial_uniqueness_valid(
    db: Session,
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    completions: Sequence[StocktakeScopeCountCompletion],
) -> bool:
    """Reprove the stable, round-wide portion of scan-time SN uniqueness."""

    count_serial_ids = tuple(
        db.scalars(
            select(StocktakeCountSerial.serial_id).where(
                StocktakeCountSerial.round_id == round_id
            )
        ).all()
    )
    observation_serial_ids = tuple(
        db.scalars(
            select(StocktakeCountObservation.serial_id).where(
                StocktakeCountObservation.task_id == task_id,
                StocktakeCountObservation.round_id == round_id,
                StocktakeCountObservation.serial_id.is_not(None),
            )
        ).all()
    )
    if (
        len(count_serial_ids) != len(set(count_serial_ids))
        or len(observation_serial_ids) != len(set(observation_serial_ids))
        or not set(count_serial_ids).isdisjoint(observation_serial_ids)
    ):
        return False

    # Each item snapshots the exact ASCII-fold aliases proven while the master
    # references were locked. Replay compares those immutable sets, so later
    # serial-name or QR changes cannot either poison or weaken historical proof.
    occurrences = _persisted_serial_alias_occurrences(completions)
    if occurrences is None:
        return False
    used_aliases: set[str] = set()
    for aliases in occurrences:
        if used_aliases.intersection(aliases):
            return False
        used_aliases.update(aliases)
    return True


def _plan_opening_count_replay_evidence(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    requested_completion: StocktakeScopeCountCompletion,
    *,
    round_assignments: Mapping[
        uuid.UUID, StocktakeRecountScopeAssignment
    ] | None = None,
) -> _OpeningCountReplayPlan:
    """Capture the complete count graph without acquiring the audit owner."""

    assignment_map = dict(round_assignments or {})
    audit_event_ids = _capture_opening_count_replay_evidence(
        db,
        task,
        round_row,
        scopes,
        requested_completion,
        round_assignments=assignment_map,
    )
    transaction = db.get_transaction()
    if transaction is None:
        _invalid_replay()
    return _OpeningCountReplayPlan(
        session=db,
        transaction=transaction,
        task_id=task.id,
        round_id=round_row.id,
        scope_ids=tuple(row.id for row in scopes),
        requested_completion_id=requested_completion.id,
        assignment_ids=tuple(
            sorted(
                (
                    (scope_id, assignment.id)
                    for scope_id, assignment in assignment_map.items()
                ),
                key=lambda item: str(item[0]),
            )
        ),
        audit_event_ids=audit_event_ids,
        seal=_OPENING_COUNT_REPLAY_PLAN_SEAL,
    )


def _require_opening_count_replay_plan(
    db: Session,
    plan: object,
) -> _OpeningCountReplayPlan:
    transaction = db.get_transaction()
    if (
        not isinstance(plan, _OpeningCountReplayPlan)
        or plan.seal is not _OPENING_COUNT_REPLAY_PLAN_SEAL
        or plan.session is not db
        or transaction is None
        or plan.transaction is not transaction
    ):
        _invalid_replay()
    return plan


def _validate_opening_count_replay_evidence_from_prelocked_task_graph(
    db: Session,
    *,
    plan: object,
    audit_proof: object,
) -> None:
    """Purely re-prove a planned count graph after the final audit lock."""

    checked = _require_opening_count_replay_plan(db, plan)
    try:
        _require_prelocked_audit_stream_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
        )
    except AuditChainError as exc:
        _fail(
            "opening_count_replay_evidence_invalid",
            "service_unavailable",
            "盘点审计预锁证明不属于当前事务",
            cause=exc,
        )
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
    scope_by_id = {row.id: row for row in scope_rows}
    scopes = tuple(scope_by_id.get(scope_id) for scope_id in checked.scope_ids)
    requested_completion = db.scalar(
        select(StocktakeScopeCountCompletion)
        .where(
            StocktakeScopeCountCompletion.id == checked.requested_completion_id
        )
        .execution_options(populate_existing=True)
    )
    assignment_rows = tuple(
        db.scalars(
            select(StocktakeRecountScopeAssignment)
            .where(
                StocktakeRecountScopeAssignment.id.in_(
                    tuple(assignment_id for _scope_id, assignment_id in checked.assignment_ids)
                )
            )
            .execution_options(populate_existing=True)
        ).all()
        if checked.assignment_ids
        else ()
    )
    assignment_by_id = {row.id: row for row in assignment_rows}
    if (
        task is None
        or round_row is None
        or requested_completion is None
        or any(scope is None for scope in scopes)
        or len(scope_by_id) != len(checked.scope_ids)
        or len(assignment_by_id) != len(checked.assignment_ids)
    ):
        _invalid_replay()
    checked_scopes = tuple(scope for scope in scopes if scope is not None)
    round_assignments = {
        scope_id: assignment_by_id[assignment_id]
        for scope_id, assignment_id in checked.assignment_ids
    }
    recaptured = _capture_opening_count_replay_evidence(
        db,
        task,
        round_row,
        checked_scopes,
        requested_completion,
        round_assignments=round_assignments,
    )
    if recaptured != checked.audit_event_ids:
        _invalid_replay()
    try:
        for event_id in checked.audit_event_ids:
            verified = _verify_audit_event_with_prelocked_proof(
                db,
                proof=audit_proof,
                stream_key=INVENTORY_STREAM_KEY,
                event_id=event_id,
            )
            if verified.id != event_id:
                _invalid_replay()
    except AuditChainError as exc:
        _fail(
            "opening_count_replay_evidence_invalid",
            "service_unavailable",
            "盘点审计链无法从预锁证明重证",
            cause=exc,
        )


def _validate_replay_evidence(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    requested_completion: StocktakeScopeCountCompletion,
    *,
    round_assignments: Mapping[
        uuid.UUID, StocktakeRecountScopeAssignment
    ] | None = None,
) -> None:
    """Standalone private replay: plan, take audit proof, then validate purely."""

    plan = _plan_opening_count_replay_evidence(
        db,
        task,
        round_row,
        scopes,
        requested_completion,
        round_assignments=round_assignments,
    )
    _head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    _validate_opening_count_replay_evidence_from_prelocked_task_graph(
        db,
        plan=plan,
        audit_proof=audit_proof,
    )


def _validate_initial_difference_set(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
) -> None:
    """Recompute the only immutable difference set this opening round may prove."""

    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task.id)
            .order_by(FormalStocktakeScope.scope_no)
        ).all()
    )
    scope_no = {row.id: row.scope_no for row in scopes}
    count_lines = tuple(
        db.scalars(
            select(StocktakeCountLine)
            .where(StocktakeCountLine.round_id == round_row.id)
            .order_by(
                StocktakeCountLine.scope_id,
                StocktakeCountLine.stock_account_id,
            )
        ).all()
    )
    account_ids = tuple(row.stock_account_id for row in count_lines)
    accounts = {
        row.id: row
        for row in (
            db.scalars(select(StockAccount).where(StockAccount.id.in_(account_ids))).all()
            if account_ids
            else []
        )
    }
    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(StocktakeCountObservation.round_id == round_row.id)
            .order_by(
                StocktakeCountObservation.scope_id,
                StocktakeCountObservation.observation_no,
            )
        ).all()
    )
    controls = tuple(
        db.scalars(
            select(StocktakeControlSnapshotLine)
            .where(StocktakeControlSnapshotLine.task_id == task.id)
            .order_by(StocktakeControlSnapshotLine.line_no)
        ).all()
    )
    expected: list[dict[str, object]] = []
    physical_by_dimension: dict[tuple[uuid.UUID, str], Decimal] = defaultdict(
        lambda: _ZERO
    )

    for line in sorted(
        count_lines,
        key=lambda row: (scope_no.get(row.scope_id, 0), str(row.stock_account_id)),
    ):
        account = accounts.get(line.stock_account_id)
        if account is None:
            _invalid_replay()
        physical_by_dimension[(account.material_id, account.condition_code)] += (
            line.counted_qty
        )
        if line.counted_qty > _ZERO:
            expected.append(
                _expected_difference_document(
                    task_id=task.id,
                    round_id=round_row.id,
                    scope_id=line.scope_id,
                    control_snapshot_line_id=None,
                    difference_type="excess",
                    material_id=account.material_id,
                    observed_account_id=account.id,
                    observed_line_id=None,
                    serial_id=None,
                    book_qty=_ZERO,
                    counted_qty=line.counted_qty,
                    reason_code="opening_physical_excess",
                    reason_text="期初实物盘点数量，仅待复核后建立个人仓库存",
                )
            )

    for observation in sorted(
        observations,
        key=lambda row: (scope_no.get(row.scope_id, 0), row.observation_no),
    ):
        if observation.material_id is not None:
            physical_by_dimension[
                (observation.material_id, observation.condition_code)
            ] += observation.counted_qty
        pending = observation.verification_status == "pending_verification"
        expected.append(
            _expected_difference_document(
                task_id=task.id,
                round_id=round_row.id,
                scope_id=observation.scope_id,
                control_snapshot_line_id=None,
                difference_type="excess",
                material_id=observation.material_id,
                observed_account_id=None,
                observed_line_id=observation.id,
                serial_id=observation.serial_id,
                book_qty=_ZERO,
                counted_qty=observation.counted_qty,
                reason_code=(
                    "opening_pending_verification"
                    if pending
                    else "opening_unexpected_dimension"
                ),
                reason_text=(
                    "现场实物标识尚未唯一解析，保留为不可过账待核实差异"
                    if pending
                    else "现场实物维度在截止快照中不存在，待复核后处理"
                ),
            )
        )

    resolved: dict[
        tuple[uuid.UUID, str], list[StocktakeControlSnapshotLine]
    ] = defaultdict(list)
    for control in controls:
        if (
            control.mapping_status == "resolved"
            and control.material_id is not None
            and control.condition_code
        ):
            resolved[(control.material_id, control.condition_code)].append(control)
        elif control.control_qty > _ZERO:
            expected.append(
                _expected_control_difference_document(
                    task,
                    round_row,
                    control,
                    _ZERO,
                    "OAM 控制行无法唯一映射到本地实物维度",
                )
            )
    for dimension, rows in sorted(
        resolved.items(), key=lambda item: (str(item[0][0]), item[0][1])
    ):
        physical = physical_by_dimension.get(dimension, _ZERO)
        control_total = sum((row.control_qty for row in rows), start=_ZERO)
        if physical == control_total:
            continue
        if len(rows) != 1:
            _invalid_replay()
        expected.append(
            _expected_control_difference_document(
                task,
                round_row,
                rows[0],
                physical,
                "OAM 省级控制数量与期初实物汇总不一致，仅用于对账",
            )
        )

    actual = tuple(
        db.scalars(
            select(StocktakeDifference)
            .where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.round_id == round_row.id,
            )
            .order_by(StocktakeDifference.difference_no)
        ).all()
    )
    if len(actual) != len(expected):
        _invalid_replay()
    for number, (row, expected_row) in enumerate(zip(actual, expected), start=1):
        if (
            row.difference_no != number
            or _stored_difference_document(row) != expected_row
            or round_row.submitted_at is None
            or _as_utc(row.created_at) != _as_utc(round_row.submitted_at)
        ):
            _invalid_replay()


def _validate_difference_set_completion(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    submission: StocktakeRoundSubmission,
    completion: StocktakeDifferenceSetCompletion,
) -> None:
    sealing = db.get(StocktakeScopeCountCompletion, submission.sealing_completion_id)
    if sealing is None:
        _invalid_replay()
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
    summary = _difference_set_summary(
        task=task,
        round_row=round_row,
        submission=submission,
        differences=differences,
    )
    completed_at = _as_utc(completion.completed_at)
    if (
        completion.task_id != task.id
        or completion.round_id != round_row.id
        or completion.round_submission_id != submission.id
        or completion.difference_count != summary["difference_count"]
        or completion.physical_difference_count
        != summary["physical_difference_count"]
        or completion.control_difference_count
        != summary["control_difference_count"]
        or completion.pending_observation_difference_count
        != summary["pending_observation_difference_count"]
        or completion.total_affected_qty != summary["total_affected_qty"]
        or completion.difference_manifest_sha256
        != summary["difference_manifest_sha256"]
        or completion.request_sha256 != _difference_set_request_sha256(summary)
        or completion.idempotency_key_hash
        != _event_hash(
            "difference-set-completion",
            round_row.id,
            submission.id,
        )
        or completion.completed_by_user_id != submission.submitted_by_user_id
        or completion.completed_by_person_id != submission.submitted_by_person_id
        or completion.completed_role_assignment_id
        != submission.submitted_role_assignment_id
        or completion.authorization_version != submission.authorization_version
        or completion.role_code != sealing.role_code
        or completion.scope_type != sealing.scope_type
        or completion.scope_id_snapshot != sealing.scope_id_snapshot
        or completion.authorization_sha256 != sealing.authorization_sha256
        or completed_at != _as_utc(submission.submitted_at)
        or _as_utc(completion.created_at) != completed_at
    ):
        _invalid_replay()


def _expected_control_difference_document(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    control: StocktakeControlSnapshotLine,
    physical_qty: Decimal,
    reason_text: str,
) -> dict[str, object]:
    document = _expected_difference_document(
        task_id=task.id,
        round_id=round_row.id,
        scope_id=None,
        control_snapshot_line_id=control.id,
        difference_type="control_unassigned",
        material_id=control.material_id,
        observed_account_id=None,
        observed_line_id=None,
        serial_id=None,
        book_qty=control.control_qty,
        counted_qty=physical_qty,
        reason_code="opening_control_reconciliation",
        reason_text=reason_text,
    )
    return document


def _expected_difference_document(
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    scope_id: uuid.UUID | None,
    control_snapshot_line_id: uuid.UUID | None,
    difference_type: str,
    material_id: uuid.UUID | None,
    observed_account_id: uuid.UUID | None,
    observed_line_id: uuid.UUID | None,
    serial_id: uuid.UUID | None,
    book_qty: Decimal,
    counted_qty: Decimal,
    reason_code: str,
    reason_text: str,
) -> dict[str, object]:
    difference_qty = counted_qty - book_qty
    return {
        "task_id": task_id,
        "round_id": round_id,
        "scope_id": scope_id,
        "control_snapshot_line_id": control_snapshot_line_id,
        "difference_type": difference_type,
        "material_id": material_id,
        "expected_account_id": None,
        "observed_account_id": observed_account_id,
        "observed_line_id": observed_line_id,
        "serial_id": serial_id,
        "book_qty": book_qty,
        "counted_qty": counted_qty,
        "difference_qty": difference_qty,
        "affected_qty": abs(difference_qty),
        "reason_code": reason_code,
        "reason_text": reason_text,
        "evidence_required": True,
    }


def _stored_difference_document(row: StocktakeDifference) -> dict[str, object]:
    return {
        "task_id": row.task_id,
        "round_id": row.round_id,
        "scope_id": row.scope_id,
        "control_snapshot_line_id": row.control_snapshot_line_id,
        "difference_type": row.difference_type,
        "material_id": row.material_id,
        "expected_account_id": row.expected_account_id,
        "observed_account_id": row.observed_account_id,
        "observed_line_id": row.observed_line_id,
        "serial_id": row.serial_id,
        "book_qty": row.book_qty,
        "counted_qty": row.counted_qty,
        "difference_qty": row.difference_qty,
        "affected_qty": row.affected_qty,
        "reason_code": row.reason_code,
        "reason_text": row.reason_text,
        "evidence_required": row.evidence_required,
    }


def _validate_completion_evidence(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scope: FormalStocktakeScope,
    completion: StocktakeScopeCountCompletion,
    *,
    expected_recount_assignment: StocktakeRecountScopeAssignment | None = None,
) -> None:
    if (
        round_row.round_type == "recount"
    ) != (expected_recount_assignment is not None):
        _invalid_replay()
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
    line_ids = tuple(row.id for row in lines)
    count_serials = tuple(
        db.scalars(
            select(StocktakeCountSerial)
            .where(StocktakeCountSerial.count_line_id.in_(line_ids))
            .order_by(
                StocktakeCountSerial.count_line_id,
                StocktakeCountSerial.serial_id,
            )
        ).all()
        if line_ids
        else ()
    )
    serial_count = len(count_serials)
    serial_count += sum(1 for row in observations if row.serial_no_raw is not None)
    total = sum((row.counted_qty for row in lines), start=_ZERO) + sum(
        (row.counted_qty for row in observations), start=_ZERO
    )
    assignment = db.get(RoleAssignment, completion.completed_role_assignment_id)
    role = db.get(Role, assignment.role_id) if assignment is not None else None
    user = db.get(User, completion.completed_by_user_id)
    completed_at = _as_utc(completion.completed_at)
    authorization_sha256 = _hash_document(
        {
            "assignment_id": str(completion.completed_role_assignment_id),
            "authorization_version": completion.authorization_version,
            "completed_at": _canonical_timestamp(completed_at),
            "person_id": str(completion.completed_by_person_id),
            "role_code": completion.role_code,
            "schema": "cloud_oam.opening_stocktake.scope_authorization.v1",
            "scope_id": completion.scope_id_snapshot,
            "scope_type": completion.scope_type,
            "user_id": completion.completed_by_user_id,
        }
    )
    manifest = _scope_evidence_manifest(
        db,
        task.id,
        round_row.id,
        scope.id,
        lines,
        observations,
        authorization_sha256,
    )
    snapshots = db.scalars(
        select(StocktakeSnapshotLine).where(
            StocktakeSnapshotLine.task_id == task.id,
            StocktakeSnapshotLine.scope_id == scope.id,
        )
    ).all()
    observation_integrity = True
    for row in observations:
        value = OpeningPhysicalObservationInput(
            material_identifier_raw=row.material_identifier_raw,
            material_identifier_type=row.material_identifier_type,
            condition_code=row.condition_code,
            availability_bucket=row.availability_bucket,
            counted_qty=row.counted_qty,
            material_id=row.material_id,
            lot_id=row.lot_id,
            lot_no_raw=row.lot_no_raw,
            serial_id=row.serial_id,
            serial_no_raw=row.serial_no_raw,
            serial_identifier_type=row.serial_identifier_type,
            count_method=row.count_method,
            reason_code=row.reason_code,
            remark=row.remark,
        )
        if (
            row.verification_status not in {"verified", "pending_verification"}
            or row.owner_org_id != scope.owner_org_id
            or row.location_id != scope.location_id
            or row.custodian_person_id_snapshot
            != scope.custodian_person_id_snapshot
            or row.dimension_sha256
            != _observation_dimension_sha256(
                scope,
                value,
                row.verification_status,
            )
            or row.request_sha256 != completion.request_sha256
            or row.idempotency_key_hash
            != _child_key_hash(
                completion.idempotency_key_hash,
                row.dimension_sha256,
            )
            or _as_utc(row.counted_at) != completed_at
            or _as_utc(row.created_at) != completed_at
        ):
            observation_integrity = False
            break
    recount_assignment_integrity = True
    if expected_recount_assignment is not None:
        expected = expected_recount_assignment
        recount_assignment_integrity = bool(
            round_row.round_type == "recount"
            and round_row.recount_case_id == expected.recount_case_id
            and expected.task_id == task.id
            and expected.scope_id == scope.id
            and completion.completed_by_user_id == expected.assignee_user_id
            and completion.completed_by_person_id == expected.assignee_person_id
            and completion.completed_role_assignment_id
            == expected.assignee_role_assignment_id
            and completion.authorization_version >= expected.authorization_version
            and completion.role_code == expected.role_code
            and completion.scope_type == expected.scope_type
            and completion.scope_id_snapshot == expected.scope_id_snapshot
            and completed_at >= _as_utc(expected.assigned_at)
        )
    if (
        assignment is None
        or role is None
        or user is None
        or assignment.user_id != completion.completed_by_user_id
        or user.person_id != completion.completed_by_person_id
        or user.authorization_version < completion.authorization_version
        or role.code != completion.role_code
        or role.is_external
        or assignment.scope_type != completion.scope_type
        or assignment.scope_id != completion.scope_id_snapshot
        or _as_utc(assignment.valid_from) > completed_at
        or (
            assignment.valid_to is not None
            and completed_at >= _as_utc(assignment.valid_to)
        )
        or (
            assignment.revoked_at is not None
            and completed_at >= _as_utc(assignment.revoked_at)
        )
        or any(row.counted_by_user_id != completion.completed_by_user_id for row in lines)
        or any(row.counted_by_user_id != completion.completed_by_user_id for row in observations)
        or any(
            _as_utc(row.counted_at) != completed_at
            or _as_utc(row.created_at) != completed_at
            or _as_utc(row.updated_at) != completed_at
            for row in lines
        )
        or any(_as_utc(row.created_at) != completed_at for row in count_serials)
        or not observation_integrity
        or not recount_assignment_integrity
        or {row.stock_account_id for row in lines}
        != {row.stock_account_id for row in snapshots}
        or completion.count_line_count != len(lines)
        or completion.observation_line_count != len(observations)
        or completion.serial_count != serial_count
        or completion.total_counted_qty != total
        or completion.zero_confirmed
        != (not snapshots and not lines and not observations and total == _ZERO)
        or completion.authorization_sha256 != authorization_sha256
        or not _persisted_request_document_valid(completion)
        or not _persisted_request_resolution_document_valid(
            db,
            completion,
            lines=lines,
            observations=observations,
            count_serials=count_serials,
        )
        or completion.evidence_manifest_sha256 != manifest
        or _as_utc(completion.created_at) != completed_at
    ):
        _invalid_replay()


def _validate_state_evidence(
    db: Session,
    *,
    idempotency_key: str,
    aggregate_type: str,
    aggregate_id: str,
    from_status: str,
    to_status: str,
    reason: str,
    actor_id: str,
    occurred_at: datetime,
    metadata: dict[str, object],
) -> None:
    rows = db.scalars(
        select(StateTransitionEvent).where(
            StateTransitionEvent.idempotency_key == idempotency_key
        )
    ).all()
    if len(rows) != 1:
        _invalid_replay()
    row = rows[0]
    if (
        row.aggregate_type != aggregate_type
        or row.aggregate_id != aggregate_id
        or row.from_status != from_status
        or row.to_status != to_status
        or row.reason != reason
        or row.actor_id != actor_id
        or _as_utc(row.occurred_at) != occurred_at
        or row.metadata_jsonb != metadata
        or _as_utc(row.created_at) != occurred_at
    ):
        _invalid_replay()


def _validate_state_event_exact_set(
    db: Session,
    *,
    aggregate_type: str,
    aggregate_ids: set[str],
    reason: str,
    round_id: uuid.UUID,
    valid_round_ids: set[uuid.UUID],
    expected_idempotency_keys: set[str],
) -> None:
    related_reasons = (
        {
            "opening_initial_scope_count_completed",
            "opening_recount_scope_count_completed",
        }
        if aggregate_type == "stocktake_scope"
        else {
            "opening_initial_round_submitted",
            "opening_recount_round_submitted",
        }
    )
    rows = (
        db.scalars(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == aggregate_type,
                StateTransitionEvent.aggregate_id.in_(tuple(aggregate_ids)),
                StateTransitionEvent.reason.in_(tuple(related_reasons)),
            )
        ).all()
        if aggregate_ids
        else []
    )
    if aggregate_type != "stocktake_round":
        rows = _rows_owned_by_round(
            rows,
            document_attribute="metadata_jsonb",
            round_id=round_id,
            valid_round_ids=valid_round_ids,
        )
    if (
        len(rows) != len(expected_idempotency_keys)
        or {row.idempotency_key for row in rows} != expected_idempotency_keys
        or any(row.reason != reason for row in rows)
    ):
        _invalid_replay()


def _validate_outbox_event_exact_set(
    db: Session,
    *,
    aggregate_type: str,
    aggregate_ids: set[str],
    event_type: str,
    round_id: uuid.UUID,
    valid_round_ids: set[uuid.UUID],
    expected_idempotency_keys: set[str],
) -> None:
    rows = (
        db.scalars(
            select(OutboxEvent).where(
                OutboxEvent.aggregate_type == aggregate_type,
                OutboxEvent.aggregate_id.in_(tuple(aggregate_ids)),
                OutboxEvent.event_type == event_type,
            )
        ).all()
        if aggregate_ids
        else []
    )
    rows = _rows_owned_by_round(
        rows,
        document_attribute="payload_jsonb",
        round_id=round_id,
        valid_round_ids=valid_round_ids,
    )
    if (
        len(rows) != len(expected_idempotency_keys)
        or {row.idempotency_key for row in rows} != expected_idempotency_keys
    ):
        _invalid_replay()


def _validate_audit_event_exact_set(
    db: Session,
    *,
    aggregate_type: str,
    aggregate_ids: set[str],
    action: str,
    round_id: uuid.UUID,
    valid_round_ids: set[uuid.UUID],
    expected_aggregate_ids: set[str],
) -> None:
    rows = (
        db.scalars(
            select(AuditEvent).where(
                AuditEvent.aggregate_type == aggregate_type,
                AuditEvent.aggregate_id.in_(tuple(aggregate_ids)),
                AuditEvent.action == action,
            )
        ).all()
        if aggregate_ids
        else []
    )
    rows = _rows_owned_by_round(
        rows,
        document_attribute="after_jsonb",
        round_id=round_id,
        valid_round_ids=valid_round_ids,
    )
    if (
        len(rows) != len(expected_aggregate_ids)
        or {row.aggregate_id for row in rows} != expected_aggregate_ids
    ):
        _invalid_replay()


def _rows_owned_by_round(
    rows: Sequence[object],
    *,
    document_attribute: str,
    round_id: uuid.UUID,
    valid_round_ids: set[uuid.UUID],
) -> list[object]:
    current_round_id = str(round_id)
    valid_ids = {str(value) for value in valid_round_ids}
    selected: list[object] = []
    for row in rows:
        document = getattr(row, document_attribute, None)
        owner_round_id = (
            document.get("round_id") if isinstance(document, dict) else None
        )
        # A same-business event without a valid round owner is not unrelated
        # noise: it is an ambiguous duplicate and must fail closed.
        if owner_round_id not in valid_ids:
            _invalid_replay()
        if owner_round_id == current_round_id:
            selected.append(row)
    return selected


def _validate_outbox_evidence(
    db: Session,
    *,
    idempotency_key: str,
    event_type: str,
    aggregate_type: str,
    aggregate_id: str,
    payload: dict[str, object],
    available_at: datetime,
) -> None:
    rows = db.scalars(
        select(OutboxEvent).where(OutboxEvent.idempotency_key == idempotency_key)
    ).all()
    if len(rows) != 1:
        _invalid_replay()
    row = rows[0]
    if (
        row.event_type != event_type
        or row.aggregate_type != aggregate_type
        or row.aggregate_id != aggregate_id
        or row.payload_jsonb != payload
        or _as_utc(row.available_at) != available_at
        or _as_utc(row.created_at) != available_at
    ):
        _invalid_replay()


def _validate_audit_evidence(
    db: Session,
    *,
    actor_user_id: str,
    action: str,
    aggregate_type: str,
    aggregate_id: str,
    after_jsonb: dict[str, object],
    occurred_at: datetime,
    round_id: uuid.UUID,
    valid_round_ids: set[uuid.UUID],
) -> uuid.UUID:
    rows = db.scalars(
        select(AuditEvent).where(
            AuditEvent.action == action,
            AuditEvent.aggregate_type == aggregate_type,
            AuditEvent.aggregate_id == aggregate_id,
        )
    ).all()
    rows = _rows_owned_by_round(
        rows,
        document_attribute="after_jsonb",
        round_id=round_id,
        valid_round_ids=valid_round_ids,
    )
    if len(rows) != 1:
        _invalid_replay()
    row = rows[0]
    if (
        row.actor_user_id != actor_user_id
        or row.before_jsonb is not None
        or row.after_jsonb != after_jsonb
        or _as_utc(row.occurred_at) != occurred_at
        or not row.request_id.startswith("opening-count-request-")
        or not _SHA256.fullmatch(row.request_id.removeprefix("opening-count-request-"))
    ):
        _invalid_replay()
    return row.id


def _invalid_replay() -> None:
    _fail(
        "opening_count_replay_evidence_invalid",
        "service_unavailable",
        "盘点幂等证据图不完整或相互矛盾",
    )


def _replay_result(
    db: Session,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scope: FormalStocktakeScope,
    completion: StocktakeScopeCountCompletion,
) -> OpeningStocktakeScopeCountResult:
    has_pending = bool(
        db.scalar(
            select(func.count())
            .select_from(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == task.id,
                StocktakeCountObservation.round_id == round_row.id,
                StocktakeCountObservation.verification_status == "pending_verification",
            )
        )
    )
    if completion.completed_at is None:
        _fail("opening_count_replay_evidence_invalid", "service_unavailable", "盘点幂等证据不完整")
    return OpeningStocktakeScopeCountResult(
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        task_status=task.status,
        round_status=round_row.status,
        scope_completed=True,
        round_sealed=round_row.status == "submitted",
        has_pending_verification=has_pending,
    )


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail("formal_principal_required", "forbidden", "盘点提交必须使用正式权限主体")
    if actor.account_status != "active" or actor.employment_status != "active" or actor.access_mode != "active":
        _fail("opening_count_actor_inactive", "forbidden", "当前账号或人员状态不允许提交盘点")
    return actor


def _require_current_actor(
    db: Session, supplied: FormalPrincipal, now: datetime
) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError as exc:
        _fail("opening_count_actor_not_current", "forbidden", "正式权限上下文已失效，请重新读取后再操作", cause=exc)
    if current.person_id != supplied.person_id or current.authorization_version != supplied.authorization_version:
        _fail("opening_count_actor_principal_stale", "precondition_failed", "权限版本已变化，请重新读取后再操作")
    if current.account_status != "active" or current.employment_status != "active" or current.access_mode != "active":
        _fail("opening_count_actor_inactive", "forbidden", "当前账号或人员状态不允许提交盘点")
    return current


def _validate_command(
    command: SubmitOpeningStocktakeScopeCountCommand,
) -> SubmitOpeningStocktakeScopeCountCommand:
    if not isinstance(command, SubmitOpeningStocktakeScopeCountCommand):
        _fail("opening_count_command_required", "invalid_request", "盘点提交命令类型无效")
    task_id = _require_uuid("task_id", command.task_id)
    round_id = _require_uuid("round_id", command.round_id)
    scope_id = _require_uuid("scope_id", command.scope_id)
    if not isinstance(command.physical_observations, tuple):
        _fail("opening_count_lines_invalid", "invalid_request", "盘点实物行必须使用不可变元组")
    if len(command.physical_observations) > _MAX_PHYSICAL_OBSERVATIONS:
        _fail(
            "opening_count_too_many_lines",
            "invalid_request",
            "单范围盘点明细不能超过 10000 行",
        )
    observations: list[OpeningPhysicalObservationInput] = []
    for row in command.physical_observations:
        if not isinstance(row, OpeningPhysicalObservationInput):
            _fail("opening_count_observation_invalid", "invalid_request", "现场实物行类型无效")
        serial_raw = _optional_text("serial_no_raw", row.serial_no_raw, 200)
        serial_type = row.serial_identifier_type
        if (serial_raw is None) != (serial_type is None):
            _fail("opening_count_serial_identifier_invalid", "invalid_request", "SN 原始值与标识类型必须同时提供")
        if row.serial_id is not None and serial_raw is None:
            _fail(
                "opening_count_serial_identifier_invalid",
                "invalid_request",
                "SN 主数据标识必须同时提供现场原始值",
            )
        if row.lot_id is not None and row.lot_no_raw is None:
            _fail(
                "opening_count_lot_identifier_invalid",
                "invalid_request",
                "批次主数据标识必须同时提供现场批次号",
            )
        if serial_type is not None:
            serial_type = _require_enum("serial_identifier_type", serial_type, _SERIAL_IDENTIFIER_TYPES)
        observations.append(
            OpeningPhysicalObservationInput(
                material_identifier_raw=_text("material_identifier_raw", row.material_identifier_raw, 300),
                material_identifier_type=_require_enum("material_identifier_type", row.material_identifier_type, _MATERIAL_IDENTIFIER_TYPES),
                condition_code=_require_enum("condition_code", row.condition_code, _CONDITIONS),
                availability_bucket=_require_enum("availability_bucket", row.availability_bucket, _AVAILABILITY),
                counted_qty=_require_quantity(row.counted_qty, positive=True),
                material_id=_optional_uuid("material_id", row.material_id),
                lot_id=_optional_uuid("lot_id", row.lot_id),
                lot_no_raw=_optional_text("lot_no_raw", row.lot_no_raw, 160),
                serial_id=_optional_uuid("serial_id", row.serial_id),
                serial_no_raw=serial_raw,
                serial_identifier_type=serial_type,
                count_method=_require_enum("count_method", row.count_method, _COUNT_METHODS),
                reason_code=_optional_text("reason_code", row.reason_code, 80),
                remark=_text("remark", row.remark, 4000, empty=True),
            )
        )
    if not isinstance(command.zero_confirmed, bool):
        _fail("opening_count_zero_confirmation_invalid", "invalid_request", "零确认标志无效")
    return SubmitOpeningStocktakeScopeCountCommand(
        task_id=task_id,
        round_id=round_id,
        scope_id=scope_id,
        physical_observations=tuple(observations),
        zero_confirmed=command.zero_confirmed,
    )


def _request_document(
    actor: FormalPrincipal, command: SubmitOpeningStocktakeScopeCountCommand
) -> dict[str, object]:
    physical_documents = [
        _observation_evidence_document(row)
        for row in command.physical_observations
    ]
    physical_documents.sort(key=_canonical_json)
    return {
        "actor_person_id": str(actor.person_id),
        "actor_user_id": actor.user_id,
        "round_id": str(command.round_id),
        "schema": "cloud_oam.opening_stocktake.scope_count_request.v1",
        "scope_id": str(command.scope_id),
        "task_id": str(command.task_id),
        "physical_observations": physical_documents,
        "zero_confirmed": command.zero_confirmed,
    }


def _scope_count_request_resolution_document(
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scope: FormalStocktakeScope,
    request_sha256: str,
    prepared_counts: Sequence[_PreparedAccountCount],
    count_lines: Sequence[StocktakeCountLine],
    prepared_observations: Sequence[_PreparedObservation],
    observations: Sequence[StocktakeCountObservation],
) -> dict[str, object]:
    """Bind every canonical request item to its exact persisted result row."""

    if len(prepared_counts) != len(count_lines) or len(
        prepared_observations
    ) != len(observations):
        _fail(
            "opening_count_request_resolution_invalid",
            "service_unavailable",
            "盘点请求解析证据无法与结果行一一绑定",
        )
    items: list[dict[str, object]] = []
    for prepared, line in zip(prepared_counts, count_lines, strict=True):
        items.extend(
            _scope_count_request_resolution_item(
                source,
                target_type="count_line",
                target_id=line.id,
            )
            for source in prepared.request_items
        )
    items.extend(
        _scope_count_request_resolution_item(
            prepared,
            target_type="observation",
            target_id=observation.id,
        )
        for prepared, observation in zip(
            prepared_observations,
            observations,
            strict=True,
        )
    )
    items.sort(key=lambda row: int(row["request_ordinal"]))
    if [row["request_ordinal"] for row in items] != list(
        range(1, len(items) + 1)
    ) or len({row["request_item_sha256"] for row in items}) != len(items):
        _fail(
            "opening_count_request_resolution_invalid",
            "service_unavailable",
            "盘点请求解析证据存在缺项或重复项",
        )
    return {
        "items": items,
        "request_sha256": request_sha256,
        "round_id": str(round_row.id),
        "schema": (
            "cloud_oam.opening_stocktake."
            "scope_count_request_resolution.v1"
        ),
        "scope_id": str(scope.id),
        "task_id": str(task.id),
    }


def _scope_count_request_resolution_item(
    source: _PreparedObservation,
    *,
    target_type: str,
    target_id: uuid.UUID,
) -> dict[str, object]:
    policy = source.policy
    policy_document: dict[str, object] | None = None
    if policy is not None:
        policy_document = {
            "allow_fraction": policy.allow_fraction,
            "effective_from": _canonical_timestamp(policy.effective_from),
            "id": str(policy.id),
            "quantity_scale": policy.quantity_scale,
            "tracking_mode": policy.tracking_mode,
        }
    return {
        "material_qr_mapping_id": (
            str(source.material_qr_mapping_id)
            if source.material_qr_mapping_id is not None
            else None
        ),
        "policy": policy_document,
        "request_item_sha256": source.request_item_sha256,
        "request_ordinal": source.request_ordinal,
        "resolved_lot_id": str(source.lot.id) if source.lot is not None else None,
        "resolved_material_id": (
            str(source.material.id) if source.material is not None else None
        ),
        "resolved_serial_id": (
            str(source.serial.id) if source.serial is not None else None
        ),
        "serial_alias_keys": list(source.serial_alias_keys),
        "serial_qr_mapping_id": (
            str(source.serial_qr_mapping_id)
            if source.serial_qr_mapping_id is not None
            else None
        ),
        "target_id": str(target_id),
        "target_type": target_type,
    }


def _persisted_request_document_valid(
    completion: StocktakeScopeCountCompletion,
) -> bool:
    document = completion.request_jsonb
    if (
        not isinstance(document, dict)
        or set(document) != _SCOPE_COUNT_REQUEST_KEYS
        or document.get("actor_person_id")
        != str(completion.completed_by_person_id)
        or document.get("actor_user_id") != completion.completed_by_user_id
        or document.get("round_id") != str(completion.round_id)
        or document.get("schema")
        != "cloud_oam.opening_stocktake.scope_count_request.v1"
        or document.get("scope_id") != str(completion.scope_id)
        or document.get("task_id") != str(completion.task_id)
        or document.get("zero_confirmed") is not completion.zero_confirmed
        or not isinstance(document.get("physical_observations"), list)
    ):
        return False
    observations = document["physical_observations"]
    if len(observations) > _MAX_PHYSICAL_OBSERVATIONS:
        return False
    quantities: list[Decimal] = []
    canonical_items: list[str] = []
    for item in observations:
        quantity = _persisted_request_observation_quantity(item)
        if quantity is None:
            return False
        quantities.append(quantity)
        canonical_items.append(_canonical_json(item))
    if (
        canonical_items != sorted(canonical_items)
        or len(canonical_items) != len(set(canonical_items))
        or (
            completion.zero_confirmed
            and bool(observations)
        )
        or sum(quantities, start=_ZERO) != completion.total_counted_qty
        or sum(
            item["serial_no_raw"] is not None
            for item in observations
        )
        != completion.serial_count
    ):
        return False
    try:
        return completion.request_sha256 == _hash_document(document)
    except OpeningStocktakeCountError:
        return False


def _persisted_request_observation_quantity(
    value: object,
) -> Decimal | None:
    if not isinstance(value, dict) or set(value) != _SCOPE_COUNT_OBSERVATION_KEYS:
        return None
    if (
        not isinstance(value.get("availability_bucket"), str)
        or value.get("availability_bucket") not in _AVAILABILITY
        or not isinstance(value.get("condition_code"), str)
        or value.get("condition_code") not in _CONDITIONS
        or not isinstance(value.get("count_method"), str)
        or value.get("count_method") not in _COUNT_METHODS
        or not isinstance(value.get("material_identifier_type"), str)
        or value.get("material_identifier_type") not in _MATERIAL_IDENTIFIER_TYPES
        or not _persisted_request_text_valid(
            value.get("material_identifier_raw"),
            limit=300,
        )
        or not _persisted_request_text_valid(
            value.get("remark"),
            limit=4000,
            empty=True,
        )
        or not all(
            _persisted_request_optional_text_valid(value.get(field), limit)
            for field, limit in (
                ("lot_no_raw", 160),
                ("reason_code", 80),
                ("serial_identifier_type", 24),
                ("serial_no_raw", 200),
            )
        )
        or not all(
            _persisted_request_optional_uuid_valid(value.get(field))
            for field in ("lot_id", "material_id", "serial_id")
        )
        or (value.get("serial_no_raw") is None)
        != (value.get("serial_identifier_type") is None)
        or (
            value.get("serial_id") is not None
            and value.get("serial_no_raw") is None
        )
        or (
            value.get("lot_id") is not None
            and value.get("lot_no_raw") is None
        )
        or (
            value.get("serial_identifier_type") is not None
            and (
                not isinstance(value.get("serial_identifier_type"), str)
                or value.get("serial_identifier_type")
                not in _SERIAL_IDENTIFIER_TYPES
            )
        )
    ):
        return None
    raw_quantity = value.get("counted_qty")
    if not isinstance(raw_quantity, str):
        return None
    try:
        quantity = Decimal(raw_quantity)
    except (ArithmeticError, ValueError):
        return None
    if (
        not quantity.is_finite()
        or quantity <= _ZERO
        or quantity >= _MAX_QUANTITY
        or _decimal_scale(quantity) > 3
        or _canonical_decimal(quantity) != raw_quantity
    ):
        return None
    return quantity


def _persisted_request_resolution_document_valid(
    db: Session,
    completion: StocktakeScopeCountCompletion,
    *,
    lines: Sequence[StocktakeCountLine],
    observations: Sequence[StocktakeCountObservation],
    count_serials: Sequence[StocktakeCountSerial],
) -> bool:
    request = completion.request_jsonb
    document = completion.request_resolution_jsonb
    if (
        not isinstance(request, dict)
        or not isinstance(request.get("physical_observations"), list)
        or not isinstance(document, dict)
        or set(document) != _SCOPE_COUNT_RESOLUTION_KEYS
        or document.get("request_sha256") != completion.request_sha256
        or document.get("round_id") != str(completion.round_id)
        or document.get("schema")
        != (
            "cloud_oam.opening_stocktake."
            "scope_count_request_resolution.v1"
        )
        or document.get("scope_id") != str(completion.scope_id)
        or document.get("task_id") != str(completion.task_id)
        or not isinstance(document.get("items"), list)
    ):
        return False
    request_items = request["physical_observations"]
    resolution_items = document["items"]
    if len(resolution_items) != len(request_items):
        return False

    lines_by_id = {row.id: row for row in lines}
    observations_by_id = {row.id: row for row in observations}
    if len(lines_by_id) != len(lines) or len(observations_by_id) != len(
        observations
    ):
        return False
    serials_by_line: dict[uuid.UUID, list[StocktakeCountSerial]] = defaultdict(
        list
    )
    for serial in count_serials:
        serials_by_line[serial.count_line_id].append(serial)
    count_serial_ids = [row.serial_id for row in count_serials]
    observation_serial_ids = [
        row.serial_id for row in observations if row.serial_id is not None
    ]
    if (
        len(count_serial_ids) != len(set(count_serial_ids))
        or len(observation_serial_ids) != len(set(observation_serial_ids))
        or not set(count_serial_ids).isdisjoint(observation_serial_ids)
    ):
        return False

    task = db.get(FormalStocktakeTask, completion.task_id)
    scope = db.get(FormalStocktakeScope, completion.scope_id)
    location = db.get(StockLocation, scope.location_id) if scope is not None else None
    if task is None or scope is None or location is None:
        return False

    line_sources: dict[
        uuid.UUID,
        list[tuple[dict[str, object], dict[str, object], Decimal]],
    ] = defaultdict(list)
    seen_observation_targets: set[uuid.UUID] = set()
    seen_item_hashes: set[str] = set()
    seen_serial_aliases: set[str] = set()

    for expected_ordinal, item in enumerate(resolution_items, start=1):
        if (
            not isinstance(item, dict)
            or set(item) != _SCOPE_COUNT_RESOLUTION_ITEM_KEYS
            or type(item.get("request_ordinal")) is not int
            or item.get("request_ordinal") != expected_ordinal
            or not isinstance(item.get("target_type"), str)
            or item.get("target_type") not in {"count_line", "observation"}
        ):
            return False
        request_item = request_items[expected_ordinal - 1]
        quantity = _persisted_request_observation_quantity(request_item)
        if quantity is None:
            return False
        serial_alias_keys = _persisted_serial_alias_keys(
            item.get("serial_alias_keys"),
            serial_no_raw=request_item.get("serial_no_raw"),
        )
        if (
            serial_alias_keys is None
            or seen_serial_aliases.intersection(serial_alias_keys)
        ):
            return False
        seen_serial_aliases.update(serial_alias_keys)
        try:
            request_item_sha256 = _hash_document(request_item)
        except OpeningStocktakeCountError:
            return False
        if (
            item.get("request_item_sha256") != request_item_sha256
            or not _SHA256.fullmatch(request_item_sha256)
            or request_item_sha256 in seen_item_hashes
        ):
            return False
        seen_item_hashes.add(request_item_sha256)

        target_id = _persisted_resolution_uuid(item.get("target_id"))
        resolved_material_id = _persisted_resolution_optional_uuid(
            item.get("resolved_material_id")
        )
        resolved_lot_id = _persisted_resolution_optional_uuid(
            item.get("resolved_lot_id")
        )
        resolved_serial_id = _persisted_resolution_optional_uuid(
            item.get("resolved_serial_id")
        )
        material_qr_mapping_id = _persisted_resolution_optional_uuid(
            item.get("material_qr_mapping_id")
        )
        serial_qr_mapping_id = _persisted_resolution_optional_uuid(
            item.get("serial_qr_mapping_id")
        )
        if (
            target_id is None
            or resolved_material_id is _INVALID_RESOLUTION_UUID
            or resolved_lot_id is _INVALID_RESOLUTION_UUID
            or resolved_serial_id is _INVALID_RESOLUTION_UUID
            or material_qr_mapping_id is _INVALID_RESOLUTION_UUID
            or serial_qr_mapping_id is _INVALID_RESOLUTION_UUID
        ):
            return False

        supplied_material_id = _persisted_resolution_optional_uuid(
            request_item.get("material_id")
        )
        supplied_lot_id = _persisted_resolution_optional_uuid(
            request_item.get("lot_id")
        )
        supplied_serial_id = _persisted_resolution_optional_uuid(
            request_item.get("serial_id")
        )
        if (
            supplied_material_id is _INVALID_RESOLUTION_UUID
            or supplied_lot_id is _INVALID_RESOLUTION_UUID
            or supplied_serial_id is _INVALID_RESOLUTION_UUID
            or (
                supplied_material_id is not None
                and supplied_material_id != resolved_material_id
            )
            or (
                supplied_lot_id is not None
                and supplied_lot_id != resolved_lot_id
            )
            or (
                supplied_serial_id is not None
                and supplied_serial_id != resolved_serial_id
            )
        ):
            return False

        material_identifier_type = request_item.get("material_identifier_type")
        serial_identifier_type = request_item.get("serial_identifier_type")
        if (
            material_identifier_type in {"external_code", "unknown"}
            and (
                resolved_material_id is not None
                or material_qr_mapping_id is not None
            )
            or material_identifier_type != "qr_code"
            and material_qr_mapping_id is not None
            or material_qr_mapping_id is not None
            and resolved_material_id is None
            or serial_identifier_type != "qr_code"
            and serial_qr_mapping_id is not None
            or serial_qr_mapping_id is not None
            and resolved_serial_id is None
            or request_item.get("lot_no_raw") is None
            and resolved_lot_id is not None
            or request_item.get("serial_no_raw") is None
            and resolved_serial_id is not None
        ):
            return False

        policy = item.get("policy")
        if not _persisted_resolution_policy_valid(policy, resolved_material_id):
            return False
        if not _persisted_resolution_master_binding_valid(
            db,
            task=task,
            request_item=request_item,
            policy_document=policy,
            resolved_material_id=resolved_material_id,
            resolved_lot_id=resolved_lot_id,
            resolved_serial_id=resolved_serial_id,
            material_qr_mapping_id=material_qr_mapping_id,
            serial_qr_mapping_id=serial_qr_mapping_id,
        ):
            return False
        if isinstance(policy, dict):
            tracking_mode = policy["tracking_mode"]
            expected_tracking = {
                "none": (False, False),
                "lot": (True, False),
                "serial": (False, True),
                "lot_and_serial": (True, True),
            }[tracking_mode]
            if (
                (
                    request_item.get("lot_no_raw") is not None,
                    request_item.get("serial_no_raw") is not None,
                )
                != expected_tracking
                or _decimal_scale(quantity) > policy["quantity_scale"]
                or (
                    not policy["allow_fraction"]
                    and quantity != quantity.to_integral_value()
                )
            ):
                return False
        if request_item.get("serial_no_raw") is not None and quantity != Decimal(
            "1"
        ):
            return False

        verification_status = (
            "pending_verification"
            if resolved_material_id is None
            or (
                request_item.get("lot_no_raw") is not None
                and resolved_lot_id is None
            )
            or (
                request_item.get("serial_no_raw") is not None
                and resolved_serial_id is None
            )
            else "verified"
        )
        matching_line_ids: set[uuid.UUID] = set()
        if verification_status == "verified":
            for candidate_line in lines:
                candidate_account = db.get(
                    StockAccount,
                    candidate_line.stock_account_id,
                )
                if (
                    candidate_account is not None
                    and candidate_account.owner_org_id == scope.owner_org_id
                    and candidate_account.location_id == scope.location_id
                    and (
                        location.location_type != "personal"
                        or candidate_account.custodian_person_id
                        == scope.custodian_person_id_snapshot
                    )
                    and candidate_account.material_id == resolved_material_id
                    and candidate_account.condition_code
                    == request_item.get("condition_code")
                    and candidate_account.availability_bucket
                    == request_item.get("availability_bucket")
                    and candidate_account.lot_id == resolved_lot_id
                ):
                    matching_line_ids.add(candidate_line.id)
        if item["target_type"] == "observation":
            observation = observations_by_id.get(target_id)
            if (
                observation is None
                or target_id in seen_observation_targets
                or matching_line_ids
                or observation.owner_org_id != scope.owner_org_id
                or observation.location_id != scope.location_id
                or observation.custodian_person_id_snapshot
                != scope.custodian_person_id_snapshot
                or observation.material_identifier_raw
                != request_item.get("material_identifier_raw")
                or observation.material_identifier_type
                != material_identifier_type
                or observation.condition_code != request_item.get("condition_code")
                or observation.availability_bucket
                != request_item.get("availability_bucket")
                or observation.counted_qty != quantity
                or observation.material_id != resolved_material_id
                or observation.lot_id != resolved_lot_id
                or observation.lot_no_raw != request_item.get("lot_no_raw")
                or observation.serial_id != resolved_serial_id
                or observation.serial_no_raw != request_item.get("serial_no_raw")
                or observation.serial_identifier_type != serial_identifier_type
                or observation.verification_status != verification_status
                or observation.count_method != request_item.get("count_method")
                or observation.reason_code != request_item.get("reason_code")
                or observation.remark != request_item.get("remark")
            ):
                return False
            seen_observation_targets.add(target_id)
            continue

        line = lines_by_id.get(target_id)
        account = db.get(StockAccount, line.stock_account_id) if line else None
        if (
            line is None
            or account is None
            or verification_status != "verified"
            or matching_line_ids != {target_id}
            or account.owner_org_id != scope.owner_org_id
            or account.location_id != scope.location_id
            or (
                location.location_type == "personal"
                and account.custodian_person_id
                != scope.custodian_person_id_snapshot
            )
            or account.material_id != resolved_material_id
            or account.condition_code != request_item.get("condition_code")
            or account.availability_bucket
            != request_item.get("availability_bucket")
            or account.lot_id != resolved_lot_id
        ):
            return False
        line_sources[target_id].append((item, request_item, quantity))

    if seen_observation_targets != set(observations_by_id):
        return False
    for line in lines:
        sources = line_sources.get(line.id, [])
        serial_rows = serials_by_line.get(line.id, [])
        if not sources:
            if (
                line.counted_qty != _ZERO
                or line.count_method != "manual"
                or line.reason_code != "scope_full_set_zero"
                or line.remark != ""
                or serial_rows
            ):
                return False
            continue
        if sum((source[2] for source in sources), start=_ZERO) != line.counted_qty:
            return False
        metadata = {
            (
                source[1]["count_method"],
                source[1]["reason_code"],
                source[1]["remark"],
            )
            for source in sources
        }
        if (
            len(metadata) != 1
            or next(iter(metadata))
            != (line.count_method, line.reason_code, line.remark)
        ):
            return False
        tracking_modes = {
            source[0]["policy"]["tracking_mode"] for source in sources
        }
        if len(tracking_modes) != 1:
            return False
        tracking_mode = next(iter(tracking_modes))
        if tracking_mode not in {"serial", "lot_and_serial"} and len(sources) != 1:
            return False
        expected_serial_ids = {
            source[0]["resolved_serial_id"]
            for source in sources
            if source[0]["resolved_serial_id"] is not None
        }
        if (
            any(row.result != "unexpected" for row in serial_rows)
            or len(serial_rows) != len({row.serial_id for row in serial_rows})
            or {str(row.serial_id) for row in serial_rows} != expected_serial_ids
            or (
                tracking_mode in {"serial", "lot_and_serial"}
                and line.counted_qty != Decimal(len(serial_rows))
            )
            or (
                tracking_mode not in {"serial", "lot_and_serial"}
                and serial_rows
            )
        ):
            return False
    return set(line_sources).issubset(lines_by_id)


_INVALID_RESOLUTION_UUID: Final[object] = object()


def _persisted_resolution_optional_uuid(
    value: object,
) -> uuid.UUID | None | object:
    if value is None:
        return None
    parsed = _persisted_resolution_uuid(value)
    return parsed if parsed is not None else _INVALID_RESOLUTION_UUID


def _persisted_resolution_uuid(value: object) -> uuid.UUID | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        return None
    return parsed if parsed.int != 0 and str(parsed) == value else None


def _persisted_resolution_policy_valid(
    value: object,
    resolved_material_id: uuid.UUID | None | object,
) -> bool:
    if resolved_material_id is None:
        return value is None
    if (
        resolved_material_id is _INVALID_RESOLUTION_UUID
        or not isinstance(value, dict)
        or set(value) != _SCOPE_COUNT_RESOLUTION_POLICY_KEYS
        or _persisted_resolution_uuid(value.get("id")) is None
        or not isinstance(value.get("tracking_mode"), str)
        or value.get("tracking_mode")
        not in {"none", "lot", "serial", "lot_and_serial"}
        or type(value.get("quantity_scale")) is not int
        or not 0 <= value["quantity_scale"] <= 3
        or type(value.get("allow_fraction")) is not bool
        or not _persisted_resolution_timestamp_valid(value.get("effective_from"))
    ):
        return False
    return True


def _persisted_resolution_master_binding_valid(
    db: Session,
    *,
    task: FormalStocktakeTask,
    request_item: dict[str, object],
    policy_document: object,
    resolved_material_id: uuid.UUID | None | object,
    resolved_lot_id: uuid.UUID | None | object,
    resolved_serial_id: uuid.UUID | None | object,
    material_qr_mapping_id: uuid.UUID | None | object,
    serial_qr_mapping_id: uuid.UUID | None | object,
) -> bool:
    if any(
        value is _INVALID_RESOLUTION_UUID
        for value in (
            resolved_material_id,
            resolved_lot_id,
            resolved_serial_id,
            material_qr_mapping_id,
            serial_qr_mapping_id,
        )
    ):
        return False
    if resolved_material_id is None:
        return (
            resolved_lot_id is None
            and resolved_serial_id is None
            and material_qr_mapping_id is None
            and serial_qr_mapping_id is None
            and policy_document is None
        )
    assert isinstance(resolved_material_id, uuid.UUID)
    material = db.get(FormalMaterial, resolved_material_id)
    identifier_type = request_item.get("material_identifier_type")
    if material is None or identifier_type not in {"sku_code", "qr_code"}:
        return False
    if identifier_type == "sku_code":
        if material_qr_mapping_id is not None:
            return False
    else:
        if not isinstance(material_qr_mapping_id, uuid.UUID):
            return False
        mapping = db.get(QrCode, material_qr_mapping_id)
        if (
            mapping is None
            or mapping.object_type != "material"
            or mapping.object_id != material.id
        ):
            return False

    if resolved_lot_id is not None:
        assert isinstance(resolved_lot_id, uuid.UUID)
        lot = db.get(InventoryLot, resolved_lot_id)
        if (
            lot is None
            or lot.material_id != material.id
        ):
            return False

    serial_identifier_type = request_item.get("serial_identifier_type")
    if resolved_serial_id is not None:
        assert isinstance(resolved_serial_id, uuid.UUID)
        serial = db.get(InventorySerial, resolved_serial_id)
        if (
            serial is None
            or serial.material_id != material.id
            or serial.lot_id != resolved_lot_id
            or serial_identifier_type not in {"serial_no", "qr_code"}
        ):
            return False
    if serial_qr_mapping_id is not None:
        if not isinstance(resolved_serial_id, uuid.UUID) or not isinstance(
            serial_qr_mapping_id,
            uuid.UUID,
        ):
            return False
        mapping = db.get(QrCode, serial_qr_mapping_id)
        if (
            mapping is None
            or mapping.object_type != "serial"
            or mapping.object_id != resolved_serial_id
        ):
            return False

    if not isinstance(policy_document, dict) or task.cutoff_at is None:
        return False
    policy_id = _persisted_resolution_uuid(policy_document.get("id"))
    policy = db.get(MaterialInventoryPolicy, policy_id) if policy_id else None
    cutoff_at = _as_utc(task.cutoff_at)
    return bool(
        policy is not None
        and policy.material_id == material.id
        and policy.tracking_mode == policy_document.get("tracking_mode")
        and policy.quantity_scale == policy_document.get("quantity_scale")
        and policy.allow_fraction is policy_document.get("allow_fraction")
        and _canonical_timestamp(policy.effective_from)
        == policy_document.get("effective_from")
        and _as_utc(policy.effective_from) <= cutoff_at
    )


def _persisted_resolution_timestamp_valid(value: object) -> bool:
    if not isinstance(value, str) or not value.endswith("Z"):
        return False
    try:
        parsed = datetime.fromisoformat(value.removesuffix("Z") + "+00:00")
    except (TypeError, ValueError):
        return False
    return _canonical_timestamp(parsed) == value


def _persisted_request_text_valid(
    value: object,
    *,
    limit: int,
    empty: bool = False,
) -> bool:
    return (
        isinstance(value, str)
        and len(value) <= limit
        and value == value.strip()
        and (empty or bool(value))
    )


def _persisted_request_optional_text_valid(
    value: object,
    limit: int,
) -> bool:
    return value is None or _persisted_request_text_valid(value, limit=limit)


def _persisted_request_optional_uuid_valid(value: object) -> bool:
    if value is None:
        return True
    if not isinstance(value, str):
        return False
    try:
        parsed = uuid.UUID(value)
    except (AttributeError, TypeError, ValueError):
        return False
    return parsed.int != 0 and str(parsed) == value


def _scope_evidence_manifest(
    db: Session,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    scope_id: uuid.UUID,
    lines: Sequence[StocktakeCountLine],
    observations: Sequence[StocktakeCountObservation],
    authorization_sha256: str,
) -> str:
    line_ids = tuple(row.id for row in lines)
    serials_by_line: dict[uuid.UUID, list[StocktakeCountSerial]] = defaultdict(list)
    if line_ids:
        for serial_row in db.scalars(
            select(StocktakeCountSerial)
            .where(StocktakeCountSerial.count_line_id.in_(line_ids))
            .order_by(
                StocktakeCountSerial.count_line_id,
                StocktakeCountSerial.serial_id,
            )
        ).all():
            serials_by_line[serial_row.count_line_id].append(serial_row)
    return _hash_document(
        {
            "authorization_sha256": authorization_sha256,
            "count_lines": [
                {
                    "count_line_id": str(row.id),
                    "count_method": row.count_method,
                    "counted_qty": _canonical_decimal(row.counted_qty),
                    "counted_at": _canonical_timestamp(row.counted_at),
                    "counted_by_user_id": row.counted_by_user_id,
                    "created_at": _canonical_timestamp(row.created_at),
                    "reason_code": row.reason_code,
                    "remark": row.remark,
                    "serials": [
                        {
                            "created_at": _canonical_timestamp(serial.created_at),
                            "result": serial.result,
                            "serial_id": str(serial.serial_id),
                        }
                        for serial in serials_by_line.get(row.id, [])
                    ],
                    "stock_account_id": str(row.stock_account_id),
                    "updated_at": _canonical_timestamp(row.updated_at),
                }
                for row in sorted(lines, key=lambda value: str(value.stock_account_id))
            ],
            "observations": [
                {
                    "availability_bucket": row.availability_bucket,
                    "condition_code": row.condition_code,
                    "count_method": row.count_method,
                    "counted_qty": _canonical_decimal(row.counted_qty),
                    "counted_at": _canonical_timestamp(row.counted_at),
                    "counted_by_user_id": row.counted_by_user_id,
                    "created_at": _canonical_timestamp(row.created_at),
                    "dimension_sha256": row.dimension_sha256,
                    "idempotency_key_hash": row.idempotency_key_hash,
                    "lot_id": str(row.lot_id) if row.lot_id else None,
                    "lot_no_raw": row.lot_no_raw,
                    "material_id": str(row.material_id) if row.material_id else None,
                    "material_identifier_raw": row.material_identifier_raw,
                    "material_identifier_type": row.material_identifier_type,
                    "observation_id": str(row.id),
                    "observation_no": row.observation_no,
                    "reason_code": row.reason_code,
                    "remark": row.remark,
                    "request_sha256": row.request_sha256,
                    "serial_id": str(row.serial_id) if row.serial_id else None,
                    "serial_identifier_type": row.serial_identifier_type,
                    "serial_no_raw": row.serial_no_raw,
                    "verification_status": row.verification_status,
                }
                for row in sorted(observations, key=lambda value: value.dimension_sha256)
            ],
            "round_id": str(round_id),
            "schema": "cloud_oam.opening_stocktake.scope_evidence_manifest.v1",
            "scope_id": str(scope_id),
            "task_id": str(task_id),
        }
    )


def _round_manifest_sha256(
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    completions: Sequence[StocktakeScopeCountCompletion],
    sealing_completion_id: uuid.UUID,
) -> str:
    return _hash_document(
        {
            "completions": [
                {
                    "authorization_sha256": row.authorization_sha256,
                    "evidence_manifest_sha256": row.evidence_manifest_sha256,
                    "scope_id": str(row.scope_id),
                    "zero_confirmed": row.zero_confirmed,
                }
                for row in sorted(completions, key=lambda value: str(value.scope_id))
            ],
            "round_id": str(round_id),
            "schema": "cloud_oam.opening_stocktake.round_manifest.v2",
            "sealing_completion_id": str(sealing_completion_id),
            "task_id": str(task_id),
        }
    )


def _authorization_sha256(
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    grant: ScopeGrant,
    now: datetime,
) -> str:
    return _hash_document(
        {
            "assignment_id": str(assignment.id),
            "authorization_version": actor.authorization_version,
            "completed_at": _canonical_timestamp(now),
            "person_id": str(actor.person_id),
            "role_code": grant.role_code,
            "schema": "cloud_oam.opening_stocktake.scope_authorization.v1",
            "scope_id": grant.scope_id,
            "scope_type": grant.scope_type,
            "user_id": actor.user_id,
        }
    )


def _observation_dimension_sha256(
    scope: FormalStocktakeScope,
    value: OpeningPhysicalObservationInput,
    verification_status: str,
) -> str:
    return _hash_document(
        {
            **_observation_dimension_document(value, verification_status),
            "custodian_person_id": (
                str(scope.custodian_person_id_snapshot)
                if scope.custodian_person_id_snapshot
                else None
            ),
            "location_id": str(scope.location_id),
            "owner_org_id": str(scope.owner_org_id),
            "schema": "cloud_oam.opening_stocktake.observation_dimension.v1",
        }
    )


def _observation_dimension_document(
    value: OpeningPhysicalObservationInput,
    verification_status: str,
) -> dict[str, object]:
    document: dict[str, object] = {
        "availability_bucket": value.availability_bucket,
        "condition_code": value.condition_code,
        "lot_id": str(value.lot_id) if value.lot_id else None,
        "material_id": str(value.material_id) if value.material_id else None,
        "serial_id": str(value.serial_id) if value.serial_id else None,
    }
    if verification_status == "pending_verification":
        if value.material_id is None:
            document["material_identifier_raw"] = value.material_identifier_raw
            document["material_identifier_type"] = value.material_identifier_type
        if value.lot_no_raw is not None and value.lot_id is None:
            document["lot_no_raw"] = value.lot_no_raw
        if value.serial_no_raw is not None and value.serial_id is None:
            document["serial_no_raw"] = value.serial_no_raw
            document["serial_identifier_type"] = value.serial_identifier_type
    return document


def _observation_evidence_document(
    value: OpeningPhysicalObservationInput,
) -> dict[str, object]:
    return {
        "availability_bucket": value.availability_bucket,
        "condition_code": value.condition_code,
        "count_method": value.count_method,
        "counted_qty": _canonical_decimal(value.counted_qty),
        "lot_id": str(value.lot_id) if value.lot_id else None,
        "lot_no_raw": value.lot_no_raw,
        "material_id": str(value.material_id) if value.material_id else None,
        "material_identifier_raw": value.material_identifier_raw,
        "material_identifier_type": value.material_identifier_type,
        "reason_code": value.reason_code,
        "remark": value.remark,
        "serial_id": str(value.serial_id) if value.serial_id else None,
        "serial_identifier_type": value.serial_identifier_type,
        "serial_no_raw": value.serial_no_raw,
    }


def _hash_document(document: Mapping[str, object]) -> str:
    try:
        encoded = _canonical_json(document).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail("opening_count_manifest_invalid", "invalid_request", "盘点证据无法规范化", cause=exc)
    return hashlib.sha256(encoded).hexdigest()


def _canonical_json(document: Mapping[str, object]) -> str:
    return json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _storage_hash(raw_key: str) -> str:
    return hashlib.sha256(
        f"cloud_oam.opening_stocktake.scope_count.idempotency.v1\0{raw_key}".encode()
    ).hexdigest()


def _child_key_hash(key_hash: str, dimension_hash: str) -> str:
    return hashlib.sha256(
        f"cloud_oam.opening_stocktake.observation.idempotency.v1\0{key_hash}\0{dimension_hash}".encode()
    ).hexdigest()


def _event_hash(kind: str, left: uuid.UUID, right: uuid.UUID) -> str:
    return hashlib.sha256(
        f"cloud_oam.opening_stocktake.{kind}.v1\0{left}\0{right}".encode()
    ).hexdigest()


def _event_key(kind: str, left: uuid.UUID, right: uuid.UUID) -> str:
    return f"opening-count-{kind}-{_event_hash(kind, left, right)}"


def _request_reference(raw: str) -> str:
    digest = hashlib.sha256(
        f"cloud_oam.opening_stocktake.scope_count.request.v1\0{raw}".encode()
    ).hexdigest()
    return f"opening-count-request-{digest}"


def _advisory_coordinate(namespace: str, value: str) -> int:
    digest = hashlib.sha256(
        f"cloud_oam.inventory.lock.v1\0{namespace}\0{value}".encode()
    ).digest()
    return int.from_bytes(digest[:8], "big", signed=True)


def _take_advisory_locks(db: Session, coordinates: tuple[int, ...]) -> None:
    if db.get_bind().dialect.name != "postgresql":
        return
    for coordinate in sorted(set(coordinates)):
        db.execute(text("SELECT pg_advisory_xact_lock(:lock_key)"), {"lock_key": coordinate})


def _is_explicit_database_trigger_rejection(exc: DBAPIError) -> bool:
    original = exc.orig
    sqlstate = getattr(original, "sqlstate", None) or getattr(
        original, "pgcode", None
    )
    statement = exc.statement if isinstance(exc.statement, str) else ""
    operation = statement.lstrip().partition(" ")[0].upper()
    return sqlstate == "P0001" and operation in {"INSERT", "UPDATE", "DELETE"}


def _require_idempotency_key(value: str) -> str:
    if not isinstance(value, str) or not 16 <= len(value) <= 200 or _PRINTABLE.fullmatch(value) is None:
        _fail("opening_count_idempotency_key_invalid", "invalid_request", "幂等键必须为 16 至 200 位可打印 ASCII 字符")
    return value


def _require_request_id(value: str) -> str:
    if not isinstance(value, str) or not 8 <= len(value) <= 160 or _PRINTABLE.fullmatch(value) is None:
        _fail("opening_count_request_id_invalid", "invalid_request", "请求标识必须为 8 至 160 位可打印 ASCII 字符")
    return value


def _require_uuid(field: str, value: object) -> uuid.UUID:
    try:
        checked = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        _fail(f"opening_count_{field}_invalid", "invalid_request", f"{field} 必须为 UUID")
    if checked.int == 0:
        _fail(f"opening_count_{field}_invalid", "invalid_request", f"{field} 不能为零 UUID")
    return checked


def _optional_uuid(field: str, value: object | None) -> uuid.UUID | None:
    return _require_uuid(field, value) if value is not None else None


def _require_quantity(value: object, *, positive: bool) -> Decimal:
    if not isinstance(value, Decimal) or not value.is_finite() or value >= _MAX_QUANTITY or _decimal_scale(value) > 3 or (value <= 0 if positive else value < 0):
        _fail("opening_count_quantity_invalid", "invalid_request", "盘点数量必须为有效 Decimal 且最多三位小数")
    return value


def _require_aggregate_quantity(value: object, *, field_name: str) -> Decimal:
    if (
        not isinstance(value, Decimal)
        or not value.is_finite()
        or value < _ZERO
        or value >= _MAX_QUANTITY
        or _decimal_scale(value) > 3
    ):
        _fail(
            "opening_count_aggregate_quantity_invalid",
            "invalid_request",
            f"{field_name}超出 Numeric(18,3) 安全范围",
        )
    return value


def _decimal_scale(value: Decimal) -> int:
    return max(0, -value.normalize().as_tuple().exponent)


def _text(field: str, value: object, limit: int, *, empty: bool = False) -> str:
    if not isinstance(value, str) or len(value) > limit or value != value.strip() or (not empty and not value):
        _fail(f"opening_count_{field}_invalid", "invalid_request", f"{field} 格式无效")
    return value


def _optional_text(field: str, value: object | None, limit: int) -> str | None:
    return _text(field, value, limit) if value is not None else None


def _require_enum(field: str, value: object, choices: frozenset[str]) -> str:
    if not isinstance(value, str) or value not in choices:
        _fail(f"opening_count_{field}_invalid", "invalid_request", f"{field} 取值无效")
    return value


def _canonical_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _canonical_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _database_now(db: Session) -> datetime:
    if db.get_bind().dialect.name == "postgresql":
        value = db.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            _fail(
                "opening_count_server_time_unavailable",
                "service_unavailable",
                "数据库服务端时间不可用",
            )
        return _as_utc(value)
    return datetime.now(timezone.utc)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_uuid(left: object, right: object) -> bool:
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
    error = OpeningStocktakeCountError(code, category, message)
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "OpeningPhysicalObservationInput",
    "OpeningStocktakeCountError",
    "OpeningStocktakeScopeCountResult",
    "SubmitOpeningStocktakeScopeCountCommand",
    "submit_opening_stocktake_scope_count",
]
