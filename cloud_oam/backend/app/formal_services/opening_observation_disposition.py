"""Internal handling boundary for pending opening-count observations.

The boundary records one immutable disposition for physical evidence that was
not uniquely attributable during the opening count.  It is called by the
formal opening HTTP adapter.  The caller owns the transaction: this module
only flushes and never commits or rolls back.

Resolution is evidence-only.  It may point at an already-existing active
material/lot/SN master when the original raw identifiers and the cutoff
tracking policy prove that binding uniquely.  It never creates or mutates a
master, stock account, balance, movement, transaction, posting or opening
establishment, and it never interprets an OAM control quantity as stock.
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
    Role,
    RoleAssignment,
    StateTransitionEvent,
)
from ..inventory_models import (
    FormalMaterial,
    InventoryTransaction,
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
    InventoryOpeningEstablishment,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeCountSerial,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakeObservationDisposition,
    StocktakePosting,
    StocktakeRecountCase,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)
from .audit_chain import (
    AuditChainError,
    _lock_audit_chain_head_with_proof,
    _verify_audit_event_with_prelocked_proof,
    append_audit_event,
)
from .opening_stocktake_count import (
    OpeningStocktakeCountError,
    _plan_opening_count_replay_evidence,
    _validate_round_assignment_evidence,
    _validate_opening_count_replay_evidence_from_prelocked_task_graph,
)
from .opening_stocktake import (
    OpeningStocktakeError,
    _organization_descends_from,
    _require_location_in_region_tree,
)
from .postgresql_lock_graph import (
    lock_inventory_reference_graph,
    lock_inventory_serial_graph,
    lock_opening_stocktake_start_reference,
    lock_opening_stocktake_task_evidence,
)


INVENTORY_STREAM_KEY: Final[str] = "inventory"
_ACTION: Final[str] = "stocktake.opening.observation_disposed"
_AGGREGATE_TYPE: Final[str] = "stocktake_observation_disposition"
_DISPOSITIONS = frozenset(
    {"resolved_existing_master", "pending_verification", "requires_recount"}
)
_PRINTABLE = re.compile(r"^[\x21-\x7e]+$", re.ASCII)
_SHA256 = re.compile(r"^[0-9a-f]{64}$")
_MAX_QUANTITY: Final[Decimal] = Decimal("1000000000000")
_OBSERVATION_DISPOSITION_REPLAY_PLAN_SEAL: Final[object] = object()
_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}


def _task_principal_user_ids(
    db: Session,
    *,
    task_id: uuid.UUID,
    supplied_user_ids: Sequence[str],
) -> set[str]:
    """Plain-read the complete task actor union while its task row is held.

    Every task writer must lock this returned principal graph before task
    evidence or shared reference owners.  Immutable replay may then reread
    historical RoleAssignment rows without acquiring a new principal lock
    after references or the audit head.
    """

    values: set[str | None] = set(supplied_user_ids)
    values.add(
        db.scalar(
            select(FormalStocktakeTask.created_by_user_id).where(
                FormalStocktakeTask.id == task_id
            )
        )
    )
    columns = (
        (FormalStocktakeScope.assignee_user_id, FormalStocktakeScope.task_id),
        (InventoryFreeze.created_by_user_id, InventoryFreeze.task_id),
        (InventoryFreeze.released_by_user_id, InventoryFreeze.task_id),
        (StocktakeRound.submitted_by_user_id, StocktakeRound.task_id),
        (StocktakeCountLine.counted_by_user_id, StocktakeCountLine.task_id),
        (
            StocktakeCountObservation.counted_by_user_id,
            StocktakeCountObservation.task_id,
        ),
        (
            StocktakeScopeCountCompletion.completed_by_user_id,
            StocktakeScopeCountCompletion.task_id,
        ),
        (
            StocktakeRoundSubmission.submitted_by_user_id,
            StocktakeRoundSubmission.task_id,
        ),
        (
            StocktakeObservationDisposition.decided_by_user_id,
            StocktakeObservationDisposition.task_id,
        ),
        (
            StocktakeDifferenceSetCompletion.completed_by_user_id,
            StocktakeDifferenceSetCompletion.task_id,
        ),
        (StocktakeReview.reviewer_user_id, StocktakeReview.task_id),
        (StocktakeRecountCase.opened_by_user_id, StocktakeRecountCase.task_id),
        (
            StocktakeRecountScopeAssignment.assignee_user_id,
            StocktakeRecountScopeAssignment.task_id,
        ),
        (StocktakePosting.posted_by_user_id, StocktakePosting.task_id),
        (
            InventoryOpeningEstablishment.established_by_user_id,
            InventoryOpeningEstablishment.task_id,
        ),
    )
    for user_column, task_column in columns:
        values.update(
            db.scalars(select(user_column).where(task_column == task_id)).all()
        )
    values.update(
        db.scalars(
            select(InventoryTransaction.actor_user_id)
            .join(
                StocktakePosting,
                StocktakePosting.inventory_transaction_id
                == InventoryTransaction.id,
            )
            .where(StocktakePosting.task_id == task_id)
        ).all()
    )
    values.update(
        db.scalars(
            select(StateTransitionEvent.actor_id).where(
                StateTransitionEvent.aggregate_type == "stocktake_task",
                StateTransitionEvent.aggregate_id == str(task_id),
            )
        ).all()
    )
    return {value for value in values if isinstance(value, str) and value}


class OpeningObservationDispositionError(RuntimeError):
    """Stable, database-detail-free failure for the disposition boundary."""

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
class RecordOpeningObservationDispositionCommand:
    task_id: uuid.UUID
    round_id: uuid.UUID
    observation_id: uuid.UUID
    disposition: str
    reason_code: str
    comment: str = ""
    resolved_material_id: uuid.UUID | None = None
    resolved_lot_id: uuid.UUID | None = None
    resolved_serial_id: uuid.UUID | None = None


@dataclass(frozen=True, slots=True)
class OpeningObservationDispositionResult:
    disposition_id: uuid.UUID
    task_id: uuid.UUID
    round_id: uuid.UUID
    scope_id: uuid.UUID
    observation_id: uuid.UUID
    disposition: str
    resolved_material_id: uuid.UUID | None
    resolved_lot_id: uuid.UUID | None
    resolved_serial_id: uuid.UUID | None
    disposition_manifest_sha256: str
    replayed: bool = False


@dataclass(frozen=True, slots=True)
class _Resolution:
    material_id: uuid.UUID | None
    lot_id: uuid.UUID | None
    serial_id: uuid.UUID | None


@dataclass(frozen=True, slots=True)
class _OpeningObservationDispositionReplayPlan:
    """Transaction-bound, pre-audit capture of one disposition replay."""

    session: Session
    transaction: object
    disposition_id: uuid.UUID
    difference_completion_id: uuid.UUID
    disposition_manifest_sha256: str
    difference_manifest_sha256: str
    audit_event_id: uuid.UUID
    seal: object


@dataclass(frozen=True, slots=True)
class _LockedResolutionReferences:
    materials: tuple[FormalMaterial, ...]
    material_mappings: tuple[QrCode, ...]
    lots: tuple[InventoryLot, ...]


def record_opening_observation_disposition(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: RecordOpeningObservationDispositionCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningObservationDispositionResult:
    """Record or read-only replay one pending-observation disposition."""

    failure: OpeningObservationDispositionError | None = None
    try:
        return _record_disposition(
            db,
            actor=actor,
            command=command,
            idempotency_key=idempotency_key,
            request_id=request_id,
        )
    except OpeningObservationDispositionError:
        raise
    except AuditChainError:
        failure = OpeningObservationDispositionError(
            "opening_observation_disposition_audit_chain_unavailable",
            "service_unavailable",
            "库存审计链不可用，待核实观察处置未完成",
        )
    except IntegrityError:
        failure = OpeningObservationDispositionError(
            "opening_observation_disposition_concurrent_conflict",
            "conflict",
            "待核实观察处置发生并发冲突，请回滚并重新读取",
        )
    except DBAPIError:
        failure = OpeningObservationDispositionError(
            "opening_observation_disposition_database_guard_rejected",
            "precondition_failed",
            "数据库安全约束拒绝了待核实观察处置，请回滚并重新读取",
        )
    if failure is not None:
        raise failure from None
    raise AssertionError("unreachable opening observation disposition boundary")


def _record_disposition(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: RecordOpeningObservationDispositionCommand,
    idempotency_key: str,
    request_id: str,
) -> OpeningObservationDispositionResult:
    supplied_actor = _validate_supplied_actor(actor)
    checked = _validate_command(command)
    checked_key = _require_idempotency_key(idempotency_key)
    checked_request_id = _require_request_id(request_id)
    key_hash = _storage_hash(checked_key)
    request_sha256 = _request_sha256(supplied_actor, checked)

    _take_advisory_locks(
        db,
        (
            _advisory_coordinate("opening-observation-disposition-key", key_hash),
            _advisory_coordinate(
                "opening-observation-disposition-task", str(checked.task_id)
            ),
            _advisory_coordinate(
                "opening-observation-disposition-round", str(checked.round_id)
            ),
            _advisory_coordinate(
                "opening-observation-disposition-observation",
                str(checked.observation_id),
            ),
        ),
    )

    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == checked.task_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    if task is None:
        _fail(
            "opening_observation_disposition_task_not_found",
            "not_found",
            "期初盘点任务不存在",
        )

    # Keep the cross-service order aligned with count/review/recount/finalize:
    # task row -> complete task principal graph -> round row -> task evidence.
    # The task row prevents the historical actor coordinate set from growing
    # between this plain discovery and the owner-side principal lock.
    principal_user_ids = _task_principal_user_ids(
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
        _fail(
            "opening_observation_disposition_round_not_found",
            "not_found",
            "期初盘点轮次不存在",
        )
    lock_opening_stocktake_task_evidence(db, task.id, round_row.id)
    observation = db.scalar(
        select(StocktakeCountObservation)
        .where(
            StocktakeCountObservation.id == checked.observation_id,
            StocktakeCountObservation.task_id == checked.task_id,
            StocktakeCountObservation.round_id == checked.round_id,
        )
        .execution_options(populate_existing=True)
    )
    if observation is None:
        _fail(
            "opening_observation_disposition_observation_not_found",
            "not_found",
            "待核实现场观察不存在",
        )
    scope = db.scalar(
        select(FormalStocktakeScope)
        .where(
            FormalStocktakeScope.id == observation.scope_id,
            FormalStocktakeScope.task_id == task.id,
        )
        .execution_options(populate_existing=True)
    )
    if scope is None:
        _evidence_invalid()
    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task.id)
            .order_by(FormalStocktakeScope.scope_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    if task.cutoff_at is None:
        _evidence_invalid()
    disposition_resolutions = lock_and_prove_opening_task_observation_resolutions(
        db,
        task=task,
        scopes=scopes,
        proposed_commands=((observation, checked),),
    )
    resolution = disposition_resolutions.get(observation.id)
    if not isinstance(resolution, _Resolution):
        _evidence_invalid()
    location_stmt = select(StockLocation).where(
        StockLocation.id == scope.location_id
    )
    if db.get_bind().dialect.name != "postgresql":
        location_stmt = location_stmt.with_for_update()
    location = db.scalar(
        location_stmt.execution_options(populate_existing=True)
    )
    if location is None:
        _evidence_invalid()
    completion = db.scalar(
        select(StocktakeScopeCountCompletion)
        .where(
            StocktakeScopeCountCompletion.task_id == task.id,
            StocktakeScopeCountCompletion.round_id == round_row.id,
            StocktakeScopeCountCompletion.scope_id == scope.id,
        )
        .execution_options(populate_existing=True)
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
    differences = tuple(
        db.scalars(
            select(StocktakeDifference)
            .where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.round_id == round_row.id,
                StocktakeDifference.observed_line_id == observation.id,
            )
            .order_by(StocktakeDifference.difference_no)
            .execution_options(populate_existing=True)
        ).all()
    )
    downstream = _lock_downstream_facts(db, task.id, round_row.id)
    existing_by_key = db.scalar(
        select(StocktakeObservationDisposition)
        .where(StocktakeObservationDisposition.idempotency_key_hash == key_hash)
        .execution_options(populate_existing=True)
    )
    existing_by_observation = db.scalar(
        select(StocktakeObservationDisposition)
        .where(StocktakeObservationDisposition.observation_id == observation.id)
        .execution_options(populate_existing=True)
    )
    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    _authorize_disposition(
        db,
        actor=current_actor,
        task=task,
        scope=scope,
        location=location,
        observation=observation,
        disposition=checked.disposition,
        now=now,
    )
    _validate_scope_dimensions(db, task, scope, location)
    _require_opening_submitted_state(task, round_row, observation, scope, now)
    _require_no_downstream(downstream)
    if completion is None or submission is None or difference_completion is None:
        _evidence_invalid()
    if len(differences) != 1:
        _evidence_invalid()
    difference = differences[0]
    if (
        difference.scope_id != scope.id
        or difference.difference_type != "excess"
        or difference.observed_account_id is not None
        or difference.reason_code != "opening_pending_verification"
    ):
        _evidence_invalid()
    count_replay_plan = _plan_count_and_difference_seals(
        db,
        task=task,
        round_row=round_row,
        scopes=scopes,
        completion=completion,
        disposition_resolutions=disposition_resolutions,
    )

    if existing_by_key is not None:
        if existing_by_key.observation_id != observation.id:
            _idempotency_conflict()
        disposition_replay_plan = _plan_opening_observation_disposition_replay(
            db,
            actor=current_actor,
            command=checked,
            row=existing_by_key,
            task=task,
            observation=observation,
            difference=difference,
            submission=submission,
            difference_completion=difference_completion,
            resolution=resolution,
            key_hash=key_hash,
            request_sha256=request_sha256,
        )
        _head, audit_proof = _lock_audit_chain_head_with_proof(
            db,
            stream_key=INVENTORY_STREAM_KEY,
        )
        _validate_count_and_difference_seals_after_audit(
            db,
            plan=count_replay_plan,
            audit_proof=audit_proof,
        )
        _validate_opening_observation_disposition_replay_from_prelocked_audit_graph(
            db,
            plan=disposition_replay_plan,
            audit_proof=audit_proof,
        )
        return _result(existing_by_key, replayed=True)
    if existing_by_observation is not None:
        _fail(
            "opening_observation_disposition_already_recorded",
            "conflict",
            "该现场观察已经形成不可变处置事实",
        )

    # The count graph was planned without touching audit.  Acquire the final
    # inventory proof once, validate that graph purely, then take a fresh clock
    # sample immediately before authorization and the append-only write.
    _head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    _validate_count_and_difference_seals_after_audit(
        db,
        plan=count_replay_plan,
        audit_proof=audit_proof,
    )
    now = _database_now(db)
    current_actor = _require_current_actor(db, supplied_actor, now)
    assignment, grant = _authorize_disposition(
        db,
        actor=current_actor,
        task=task,
        scope=scope,
        location=location,
        observation=observation,
        disposition=checked.disposition,
        now=now,
        lock_rows=False,
    )
    _validate_scope_dimensions(
        db,
        task,
        scope,
        location,
        lock_rows=False,
    )
    _require_opening_submitted_state(task, round_row, observation, scope, now)
    _require_no_downstream(_lock_downstream_facts(db, task.id, round_row.id))
    authorization_sha256 = _authorization_sha256(
        current_actor, assignment, grant, now
    )
    row_id = uuid.uuid4()
    manifest = _disposition_manifest_sha256(
        disposition_id=row_id,
        command=checked,
        observation=observation,
        difference=difference,
        submission=submission,
        difference_completion=difference_completion,
        resolution=resolution,
        authorization_sha256=authorization_sha256,
        request_sha256=request_sha256,
        key_hash=key_hash,
        decided_at=now,
    )
    row = StocktakeObservationDisposition(
        id=row_id,
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        observation_id=observation.id,
        disposition=checked.disposition,
        resolved_material_id=resolution.material_id,
        resolved_lot_id=resolution.lot_id,
        resolved_serial_id=resolution.serial_id,
        reason_code=checked.reason_code,
        comment=checked.comment,
        disposition_manifest_sha256=manifest,
        request_sha256=request_sha256,
        idempotency_key_hash=key_hash,
        decided_by_user_id=current_actor.user_id,
        decided_by_person_id=current_actor.person_id,
        decided_role_assignment_id=assignment.id,
        authorization_version=current_actor.authorization_version,
        role_code=grant.role_code,
        scope_type=grant.scope_type,
        scope_id_snapshot=grant.scope_id,
        authorization_sha256=authorization_sha256,
        decided_at=now,
        created_at=now,
    )
    db.add(row)
    db.flush()
    append_audit_event(
        db,
        stream_key=INVENTORY_STREAM_KEY,
        actor_user_id=current_actor.user_id,
        action=_ACTION,
        aggregate_type=_AGGREGATE_TYPE,
        aggregate_id=str(row.id),
        before_jsonb=None,
        after_jsonb=_audit_after_jsonb(row, difference_completion),
        request_id=_request_reference(checked_request_id),
        occurred_at=now,
        created_at=now,
    )
    db.flush()
    return _result(row, replayed=False)


def _plan_count_and_difference_seals(
    db: Session,
    *,
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    scopes: Sequence[FormalStocktakeScope],
    completion: StocktakeScopeCountCompletion,
    disposition_resolutions: Mapping[uuid.UUID, object],
) -> object:
    try:
        freezes = tuple(
            db.scalars(
                select(InventoryFreeze)
                .where(InventoryFreeze.task_id == task.id)
                .order_by(InventoryFreeze.stocktake_scope_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            ).all()
        )
        round_assignments = _validate_round_assignment_evidence(
            db,
            task=task,
            round_row=round_row,
            scopes=scopes,
            freezes=freezes,
            allow_downstream=False,
            disposition_resolutions=disposition_resolutions,
        )
        return _plan_opening_count_replay_evidence(
            db,
            task,
            round_row,
            scopes,
            completion,
            round_assignments=round_assignments,
        )
    except OpeningStocktakeCountError as exc:
        _fail(
            "opening_observation_disposition_count_evidence_invalid",
            "service_unavailable",
            "期初盘点计数或差异完成封印不完整",
            cause=exc,
        )


def _validate_count_and_difference_seals_after_audit(
    db: Session,
    *,
    plan: object,
    audit_proof: object,
) -> None:
    try:
        _validate_opening_count_replay_evidence_from_prelocked_task_graph(
            db,
            plan=plan,
            audit_proof=audit_proof,
        )
    except OpeningStocktakeCountError as exc:
        _fail(
            "opening_observation_disposition_count_evidence_invalid",
            "service_unavailable",
            "期初盘点计数或差异完成封印不完整",
            cause=exc,
        )


def _lock_downstream_facts(
    db: Session,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
) -> tuple[tuple[object, ...], tuple[object, ...], tuple[object, ...]]:
    reviews = tuple(
        db.scalars(
            select(StocktakeReview)
            .where(
                StocktakeReview.task_id == task_id,
                StocktakeReview.round_id == round_id,
            )
            .order_by(StocktakeReview.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    postings = tuple(
        db.scalars(
            select(StocktakePosting)
            .where(
                StocktakePosting.task_id == task_id,
                StocktakePosting.round_id == round_id,
            )
            .order_by(StocktakePosting.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    establishments = tuple(
        db.scalars(
            select(InventoryOpeningEstablishment)
            .where(
                InventoryOpeningEstablishment.task_id == task_id,
                InventoryOpeningEstablishment.round_id == round_id,
            )
            .order_by(InventoryOpeningEstablishment.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    return reviews, postings, establishments


def _require_no_downstream(
    downstream: tuple[tuple[object, ...], tuple[object, ...], tuple[object, ...]],
) -> None:
    if any(downstream):
        _fail(
            "opening_observation_disposition_downstream_exists",
            "precondition_failed",
            "复核、过账或期初建账事实已存在，禁止补录现场观察处置",
        )


def _require_opening_submitted_state(
    task: FormalStocktakeTask,
    round_row: StocktakeRound,
    observation: StocktakeCountObservation,
    scope: FormalStocktakeScope,
    now: datetime,
) -> None:
    if (
        task.task_type != "opening"
        or task.status != "submitted"
        or task.current_round_no != round_row.round_no
        or task.cutoff_at is None
        or task.cutoff_ledger_cursor is None
        or task.submitted_at is None
        or round_row.round_no <= 0
        or round_row.round_type
        != ("initial" if round_row.round_no == 1 else "recount")
        or (round_row.round_no == 1 and round_row.recount_case_id is not None)
        or (round_row.round_no > 1 and round_row.recount_case_id is None)
        or round_row.status != "submitted"
        or round_row.submitted_at is None
        or round_row.task_id != task.id
        or observation.task_id != task.id
        or observation.round_id != round_row.id
        or observation.scope_id != scope.id
        or observation.owner_org_id != scope.owner_org_id
        or observation.location_id != scope.location_id
        or observation.custodian_person_id_snapshot
        != scope.custodian_person_id_snapshot
        or observation.verification_status != "pending_verification"
        or _as_utc(task.cutoff_at) > now
        or _as_utc(round_row.submitted_at) > now
        or _as_utc(task.submitted_at) != _as_utc(round_row.submitted_at)
    ):
        _fail(
            "opening_observation_disposition_state_invalid",
            "precondition_failed",
            "只有当前已提交且证据完整的期初盘点轮次待核实观察可以处置",
        )


def _authorize_disposition(
    db: Session,
    *,
    actor: FormalPrincipal,
    task: FormalStocktakeTask,
    scope: FormalStocktakeScope,
    location: StockLocation,
    observation: StocktakeCountObservation,
    disposition: str,
    now: datetime,
    lock_rows: bool = True,
) -> tuple[RoleAssignment, ScopeGrant]:
    candidates = [
        grant
        for grant in actor.assignments
        if (
            grant.role_code == "admin"
            and grant.scope_type == "national"
            and grant.scope_id == "*"
        )
        or (
            disposition != "resolved_existing_master"
            and grant.role_code == "provincial_manager"
            and grant.scope_type == "organization"
            and _same_uuid(grant.scope_id, task.region_org_id)
        )
    ]
    candidates.sort(
        key=lambda grant: (grant.role_code != "admin", str(grant.assignment_id))
    )
    allowed: list[ScopeGrant] = []
    for grant in candidates:
        selected_actor = replace(
            actor,
            assignments=(grant,),
            entitlements=tuple(
                row
                for row in actor.entitlements
                if row.assignment_id == grant.assignment_id
            ),
        )
        target_ids = tuple(
            dict.fromkeys(
                (
                    task.region_org_id,
                    scope.owner_org_id,
                    location.owner_org_id,
                )
            )
        )
        try:
            covers = all(
                actor.allows(
                    db,
                    "stocktake",
                    "manage",
                    target_scope_type="organization",
                    target_scope_id=str(target_id),
                )
                and selected_actor.allows(
                    db,
                    "stocktake",
                    "manage",
                    target_scope_type="organization",
                    target_scope_id=str(target_id),
                )
                for target_id in target_ids
            )
        except FormalAccessError as exc:
            _fail(
                "opening_observation_disposition_authorization_invalid",
                "forbidden",
                "待核实观察处置权限图无效",
                cause=exc,
            )
        if covers:
            allowed.append(grant)
    if not allowed:
        _fail(
            "opening_observation_disposition_forbidden",
            "forbidden",
            "当前人员无权处置该资产所有组织的待核实观察",
        )
    grant = allowed[0]
    assignment_statement = select(RoleAssignment).where(
        RoleAssignment.id == grant.assignment_id
    )
    if lock_rows:
        assignment_statement = assignment_statement.with_for_update()
    assignment = db.scalar(
        assignment_statement.execution_options(populate_existing=True)
    )
    role = db.scalar(
        select(Role)
        .where(Role.id == assignment.role_id)
        .execution_options(populate_existing=True)
    ) if assignment is not None else None
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
            "opening_observation_disposition_assignment_not_current",
            "forbidden",
            "待核实观察处置授权已变化，请重新读取",
        )
    return assignment, grant


def _validate_scope_dimensions(
    db: Session,
    task: FormalStocktakeTask,
    scope: FormalStocktakeScope,
    location: StockLocation,
    *,
    lock_rows: bool = True,
) -> None:
    """Re-prove the persisted regional asset and physical-location boundary."""

    region_stmt = select(Organization).where(
        Organization.id == task.region_org_id
    )
    owner_stmt = select(Organization).where(
        Organization.id == scope.owner_org_id
    )
    location_stmt = select(StockLocation).where(
        StockLocation.id == scope.location_id
    )
    legacy_row_locks = (
        lock_rows and db.get_bind().dialect.name != "postgresql"
    )
    if legacy_row_locks:
        region_stmt = region_stmt.with_for_update()
        owner_stmt = owner_stmt.with_for_update()
        location_stmt = location_stmt.with_for_update()
    region = db.scalar(
        region_stmt.execution_options(populate_existing=True)
    )
    owner = db.scalar(
        owner_stmt.execution_options(populate_existing=True)
    )
    current_location = db.scalar(
        location_stmt.execution_options(populate_existing=True)
    )
    if (
        region is None
        or region.status != "active"
        or region.org_type != "region_company"
        or owner is None
        or owner.status != "active"
        or owner.org_type != "region_company"
        or location.id != scope.location_id
        or current_location is None
        or current_location.status != "active"
        or current_location.location_type not in {"region", "personal"}
    ):
        _fail(
            "opening_observation_disposition_scope_dimension_invalid",
            "precondition_failed",
            "待核实观察的任务区域、资产所有组织或库位主数据已失效",
        )
    try:
        owner_in_region = _organization_descends_from(
            db,
            owner.id,
            task.region_org_id,
            lock_rows=legacy_row_locks,
        )
        _require_location_in_region_tree(
            db,
            current_location,
            task.region_org_id,
            lock_rows=legacy_row_locks,
        )
    except OpeningStocktakeError as exc:
        _fail(
            "opening_observation_disposition_scope_dimension_invalid",
            "precondition_failed",
            "待核实观察的组织或库位范围无法安全重证",
            cause=exc,
        )
    if not owner_in_region:
        _fail(
            "opening_observation_disposition_owner_outside_region",
            "precondition_failed",
            "待核实观察的资产所有组织不在任务区域的有效组织树内",
        )


def _read_resolution_material_rows(
    db: Session,
    observation: StocktakeCountObservation,
    *,
    populate_existing: bool,
) -> tuple[tuple[FormalMaterial, ...], tuple[QrCode, ...]]:
    if observation.material_identifier_type == "sku_code":
        statement = (
            select(FormalMaterial)
            .where(
                FormalMaterial.sku_code == observation.material_identifier_raw,
                FormalMaterial.status == "active",
            )
            .order_by(FormalMaterial.id)
        )
        if populate_existing:
            statement = statement.execution_options(populate_existing=True)
        return tuple(db.scalars(statement).all()), ()
    if observation.material_identifier_type != "qr_code":
        return (), ()
    mapping_statement = (
        select(QrCode)
        .where(
            QrCode.code == observation.material_identifier_raw,
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


def _read_resolution_lots(
    db: Session,
    *,
    material_ids: Sequence[uuid.UUID],
    lot_no: str | None,
    populate_existing: bool,
) -> tuple[InventoryLot, ...]:
    if lot_no is None or not material_ids:
        return ()
    statement = (
        select(InventoryLot)
        .where(
            InventoryLot.material_id.in_(tuple(material_ids)),
            InventoryLot.lot_no == lot_no,
        )
        .order_by(InventoryLot.material_id, InventoryLot.id)
    )
    if populate_existing:
        statement = statement.execution_options(populate_existing=True)
    return tuple(db.scalars(statement).all())


def lock_and_prove_opening_observation_resolutions(
    db: Session,
    *,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    observation_commands: Sequence[
        tuple[
            StocktakeCountObservation,
            RecordOpeningObservationDispositionCommand,
        ]
    ],
    extra_material_ids: Sequence[uuid.UUID] = (),
    extra_account_ids: Sequence[uuid.UUID] = (),
    extra_serial_ids: Sequence[uuid.UUID] = (),
) -> dict[uuid.UUID, _Resolution]:
    """Lock and re-prove one task's disposition reference graph in one order."""

    if task.cutoff_at is None:
        _evidence_invalid()
    if len({row.id for row, _command in observation_commands}) != len(
        observation_commands
    ):
        _evidence_invalid()

    before: dict[
        uuid.UUID,
        tuple[
            tuple[uuid.UUID, ...],
            tuple[tuple[uuid.UUID, uuid.UUID], ...],
            tuple[tuple[uuid.UUID, uuid.UUID], ...],
        ],
    ] = {}
    material_ids = {
        row.material_id for row in scopes if row.material_id is not None
    }
    material_ids.update(extra_material_ids)
    for observation, command in observation_commands:
        materials, mappings = _read_resolution_material_rows(
            db,
            observation,
            populate_existing=False,
        )
        candidate_material_ids = {
            row.id for row in materials
        } | {row.object_id for row in mappings}
        candidate_material_ids.update(
            value
            for value in (observation.material_id, command.resolved_material_id)
            if value is not None
        )
        lots = _read_resolution_lots(
            db,
            material_ids=tuple(candidate_material_ids),
            lot_no=observation.lot_no_raw,
            populate_existing=False,
        )
        before[observation.id] = (
            tuple(row.id for row in materials),
            tuple((row.id, row.object_id) for row in mappings),
            tuple((row.id, row.material_id) for row in lots),
        )
        material_ids.update(candidate_material_ids)

    lock_opening_stocktake_start_reference(
        db,
        task.region_org_id,
        tuple(row.owner_org_id for row in scopes),
        tuple(row.location_id for row in scopes),
        tuple(material_ids),
        task.cutoff_at,
    )

    locked_by_observation: dict[uuid.UUID, _LockedResolutionReferences] = {}
    preliminary: dict[
        uuid.UUID,
        tuple[
            StocktakeCountObservation,
            RecordOpeningObservationDispositionCommand,
            FormalMaterial,
            InventoryLot | None,
            MaterialInventoryPolicy,
        ],
    ] = {}
    resolutions: dict[uuid.UUID, _Resolution] = {}
    for observation, command in observation_commands:
        materials, mappings = _read_resolution_material_rows(
            db,
            observation,
            populate_existing=True,
        )
        candidate_material_ids = set(material_ids)
        candidate_material_ids.update(row.id for row in materials)
        candidate_material_ids.update(row.object_id for row in mappings)
        lots = _read_resolution_lots(
            db,
            material_ids=tuple(candidate_material_ids),
            lot_no=observation.lot_no_raw,
            populate_existing=True,
        )
        after = (
            tuple(row.id for row in materials),
            tuple((row.id, row.object_id) for row in mappings),
            tuple((row.id, row.material_id) for row in lots),
        )
        if after != before[observation.id]:
            _evidence_invalid()
        locked = _LockedResolutionReferences(materials, mappings, lots)
        locked_by_observation[observation.id] = locked
        if command.disposition != "resolved_existing_master":
            if any(
                value is not None
                for value in (
                    command.resolved_material_id,
                    command.resolved_lot_id,
                    command.resolved_serial_id,
                )
            ):
                _fail(
                    "opening_observation_disposition_unresolved_binding_invalid",
                    "invalid_request",
                    "待核实或复盘处置不得携带推测的主数据标识",
                )
            resolutions[observation.id] = _Resolution(None, None, None)
            continue
        material = _prove_material(observation, locked)
        if command.resolved_material_id != material.id:
            _fail(
                "opening_observation_disposition_material_unproven",
                "precondition_failed",
                "原始物料标识不能唯一证明所选启用物料",
            )
        lot = _prove_lot(observation, material, locked)
        if command.resolved_lot_id != (lot.id if lot is not None else None):
            _fail(
                "opening_observation_disposition_lot_unproven",
                "precondition_failed",
                "原始批次标识不能唯一证明所选批次",
            )
        policy = _load_cutoff_policy(db, material.id, task.cutoff_at)
        preliminary[observation.id] = (
            observation,
            command,
            material,
            lot,
            policy,
        )

    def _read_accounts(
        observation: StocktakeCountObservation,
        material: FormalMaterial,
        lot: InventoryLot | None,
        *,
        populate_existing: bool,
    ) -> tuple[tuple[StockAccount, ...], tuple[StockAccount, ...]]:
        dimension = (
            StockAccount.owner_org_id == observation.owner_org_id,
            StockAccount.location_id == observation.location_id,
            StockAccount.custodian_person_id
            == observation.custodian_person_id_snapshot,
            StockAccount.material_id == material.id,
            StockAccount.condition_code == observation.condition_code,
            StockAccount.availability_bucket == observation.availability_bucket,
            StockAccount.lot_id == (lot.id if lot is not None else None),
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

    account_before: dict[
        uuid.UUID, tuple[tuple[uuid.UUID, ...], tuple[uuid.UUID, ...]]
    ] = {}
    account_ids = set(extra_account_ids)
    for observation_id, (observation, _command, material, lot, _policy) in preliminary.items():
        cutoff_rows, snapshot_rows = _read_accounts(
            observation,
            material,
            lot,
            populate_existing=False,
        )
        account_before[observation_id] = (
            tuple(row.id for row in cutoff_rows),
            tuple(row.id for row in snapshot_rows),
        )
        account_ids.update(row.id for row in (*cutoff_rows, *snapshot_rows))
    lock_inventory_reference_graph(db, tuple(account_ids), task.cutoff_at)
    for observation_id, (observation, _command, material, lot, _policy) in preliminary.items():
        cutoff_rows, snapshot_rows = _read_accounts(
            observation,
            material,
            lot,
            populate_existing=True,
        )
        after = (
            tuple(row.id for row in cutoff_rows),
            tuple(row.id for row in snapshot_rows),
        )
        if after != account_before[observation_id]:
            _evidence_invalid()
        if cutoff_rows or snapshot_rows:
            _fail(
                "opening_observation_disposition_cutoff_account_exists",
                "precondition_failed",
                "该精确维度在截止时点已有库存账户，应使用原计数行而非观察处置",
            )

    serial_before: dict[
        uuid.UUID,
        tuple[tuple[uuid.UUID, ...], tuple[tuple[uuid.UUID, uuid.UUID], ...]],
    ] = {}
    serial_ids = set(extra_serial_ids)
    for observation_id, (observation, command, material, _lot, _policy) in preliminary.items():
        serials, mappings = _read_resolution_serial_rows(
            db,
            observation=observation,
            material=material,
            populate_existing=False,
        )
        serial_before[observation_id] = (
            tuple(row.id for row in serials),
            tuple((row.id, row.object_id) for row in mappings),
        )
        serial_ids.update(row.id for row in serials)
        serial_ids.update(row.object_id for row in mappings)
        serial_ids.update(
            value
            for value in (observation.serial_id, command.resolved_serial_id)
            if value is not None
        )
    lock_inventory_serial_graph(db, tuple(serial_ids))
    for observation_id, (observation, command, material, lot, policy) in preliminary.items():
        serials, mappings = _read_resolution_serial_rows(
            db,
            observation=observation,
            material=material,
            populate_existing=True,
        )
        after = (
            tuple(row.id for row in serials),
            tuple((row.id, row.object_id) for row in mappings),
        )
        if after != serial_before[observation_id]:
            _evidence_invalid()
        serial = _prove_serial_from_locked(
            observation,
            material,
            lot,
            serials=serials,
            mappings=mappings,
        )
        if command.resolved_serial_id != (serial.id if serial is not None else None):
            _fail(
                "opening_observation_disposition_serial_unproven",
                "precondition_failed",
                "原始 SN 标识不能唯一证明所选启用 SN",
            )
        _validate_policy_binding(observation, policy, lot, serial)
        resolutions[observation_id] = _Resolution(
            material.id,
            lot.id if lot is not None else None,
            serial.id if serial is not None else None,
        )
    return resolutions


def lock_and_prove_opening_task_observation_resolutions(
    db: Session,
    *,
    task: FormalStocktakeTask,
    scopes: Sequence[FormalStocktakeScope],
    proposed_commands: Sequence[
        tuple[
            StocktakeCountObservation,
            RecordOpeningObservationDispositionCommand,
        ]
    ] = (),
    extra_material_ids: Sequence[uuid.UUID] = (),
    extra_account_ids: Sequence[uuid.UUID] = (),
    extra_serial_ids: Sequence[uuid.UUID] = (),
) -> dict[uuid.UUID, _Resolution]:
    """Lock one task's complete reference graph in the global 0027 order.

    The task-evidence owner helper must already be held by the caller.  This
    planner deliberately reads every historical observation/disposition for
    the task before taking the shared reference helpers, so nested recount
    replay cannot discover a late material, account or serial coordinate.
    """

    observations = tuple(
        db.scalars(
            select(StocktakeCountObservation)
            .where(StocktakeCountObservation.task_id == task.id)
            .order_by(
                StocktakeCountObservation.round_id,
                StocktakeCountObservation.observation_no,
                StocktakeCountObservation.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    observations_by_id = {row.id: row for row in observations}
    if len(observations_by_id) != len(observations):
        _evidence_invalid()

    dispositions = tuple(
        db.scalars(
            select(StocktakeObservationDisposition)
            .where(StocktakeObservationDisposition.task_id == task.id)
            .order_by(
                StocktakeObservationDisposition.round_id,
                StocktakeObservationDisposition.observation_id,
                StocktakeObservationDisposition.id,
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    command_by_observation: dict[
        uuid.UUID,
        tuple[
            StocktakeCountObservation,
            RecordOpeningObservationDispositionCommand,
        ],
    ] = {}
    for disposition in dispositions:
        observation = observations_by_id.get(disposition.observation_id)
        if (
            observation is None
            or disposition.round_id != observation.round_id
            or disposition.scope_id != observation.scope_id
            or disposition.observation_id in command_by_observation
        ):
            _evidence_invalid()
        command_by_observation[disposition.observation_id] = (
            observation,
            RecordOpeningObservationDispositionCommand(
                task_id=task.id,
                round_id=observation.round_id,
                observation_id=observation.id,
                disposition=disposition.disposition,
                reason_code=disposition.reason_code,
                comment=disposition.comment,
                resolved_material_id=disposition.resolved_material_id,
                resolved_lot_id=disposition.resolved_lot_id,
                resolved_serial_id=disposition.resolved_serial_id,
            ),
        )

    for observation, command in proposed_commands:
        canonical_observation = observations_by_id.get(observation.id)
        if (
            canonical_observation is None
            or canonical_observation.task_id != task.id
            or canonical_observation.round_id != observation.round_id
            or canonical_observation.scope_id != observation.scope_id
            or command.task_id != task.id
            or command.round_id != canonical_observation.round_id
            or command.observation_id != canonical_observation.id
        ):
            _evidence_invalid()
        command_by_observation[canonical_observation.id] = (
            canonical_observation,
            command,
        )

    account_ids = set(extra_account_ids)
    account_ids.update(
        db.scalars(
            select(StocktakeSnapshotLine.stock_account_id).where(
                StocktakeSnapshotLine.task_id == task.id
            )
        ).all()
    )
    account_ids.update(
        db.scalars(
            select(StocktakeCountLine.stock_account_id).where(
                StocktakeCountLine.task_id == task.id
            )
        ).all()
    )
    round_ids = tuple(
        db.scalars(
            select(StocktakeRound.id).where(StocktakeRound.task_id == task.id)
        ).all()
    )
    serial_ids = set(extra_serial_ids)
    serial_ids.update(
        db.scalars(
            select(StocktakeCountSerial.serial_id).where(
                StocktakeCountSerial.round_id.in_(round_ids)
            )
        ).all()
        if round_ids
        else ()
    )
    serial_ids.update(
        row.serial_id for row in observations if row.serial_id is not None
    )
    serial_ids.update(
        row.resolved_serial_id
        for row in dispositions
        if row.resolved_serial_id is not None
    )
    serial_ids.update(
        command.resolved_serial_id
        for _observation, command in proposed_commands
        if command.resolved_serial_id is not None
    )
    material_ids = set(extra_material_ids)
    material_ids.update(
        row.material_id for row in observations if row.material_id is not None
    )
    material_ids.update(
        row.resolved_material_id
        for row in dispositions
        if row.resolved_material_id is not None
    )
    material_ids.update(
        command.resolved_material_id
        for _observation, command in proposed_commands
        if command.resolved_material_id is not None
    )
    return lock_and_prove_opening_observation_resolutions(
        db,
        task=task,
        scopes=scopes,
        observation_commands=tuple(command_by_observation.values()),
        extra_material_ids=tuple(material_ids),
        extra_account_ids=tuple(account_ids),
        extra_serial_ids=tuple(serial_ids),
    )


def _prove_material(
    observation: StocktakeCountObservation,
    locked_references: _LockedResolutionReferences,
) -> FormalMaterial:
    rows = locked_references.materials
    if (
        observation.material_identifier_type == "qr_code"
        and len(locked_references.material_mappings) != 1
    ) or len(rows) != 1:
        _fail(
            "opening_observation_disposition_material_unproven",
            "precondition_failed",
            "外部码、未知码或非唯一标识不能解析为既有启用物料",
        )
    material = rows[0]
    if observation.material_id not in {None, material.id}:
        _evidence_invalid()
    return material


def _prove_lot(
    observation: StocktakeCountObservation,
    material: FormalMaterial,
    locked_references: _LockedResolutionReferences,
) -> InventoryLot | None:
    if observation.lot_no_raw is None:
        if observation.lot_id is not None:
            _evidence_invalid()
        return None
    rows = tuple(
        row
        for row in locked_references.lots
        if row.material_id == material.id
        and row.lot_no == observation.lot_no_raw
    )
    if len(rows) != 1:
        _fail(
            "opening_observation_disposition_lot_unproven",
            "precondition_failed",
            "现场批次号不能唯一解析为既有批次",
        )
    lot = rows[0]
    if observation.lot_id not in {None, lot.id}:
        _evidence_invalid()
    return lot


def _read_resolution_serial_rows(
    db: Session,
    *,
    observation: StocktakeCountObservation,
    material: FormalMaterial,
    populate_existing: bool,
) -> tuple[tuple[InventorySerial, ...], tuple[QrCode, ...]]:
    if observation.serial_no_raw is None:
        return (), ()
    if observation.serial_identifier_type not in {"serial_no", "qr_code"}:
        return (), ()
    identifier = (
        InventorySerial.serial_no
        if observation.serial_identifier_type == "serial_no"
        else InventorySerial.qr_code
    )
    serial_stmt = (
        select(InventorySerial)
        .where(
            InventorySerial.material_id == material.id,
            identifier == observation.serial_no_raw,
            InventorySerial.lifecycle_status == "active",
        )
        .order_by(InventorySerial.id)
    )
    mapping_stmt = (
        select(QrCode)
        .where(
            QrCode.code == observation.serial_no_raw,
            QrCode.object_type == "serial",
            QrCode.status == "active",
        )
        .order_by(QrCode.id)
        if observation.serial_identifier_type == "qr_code"
        else None
    )
    if populate_existing:
        serial_stmt = serial_stmt.execution_options(populate_existing=True)
        if mapping_stmt is not None:
            mapping_stmt = mapping_stmt.execution_options(populate_existing=True)
    return (
        tuple(db.scalars(serial_stmt).all()),
        tuple(db.scalars(mapping_stmt).all())
        if mapping_stmt is not None
        else (),
    )


def _prove_serial_from_locked(
    observation: StocktakeCountObservation,
    material: FormalMaterial,
    lot: InventoryLot | None,
    *,
    serials: Sequence[InventorySerial],
    mappings: Sequence[QrCode],
) -> InventorySerial | None:
    if observation.serial_no_raw is None:
        if observation.serial_id is not None or observation.serial_identifier_type is not None:
            _evidence_invalid()
        return None
    if observation.serial_identifier_type not in {"serial_no", "qr_code"}:
        _fail(
            "opening_observation_disposition_serial_unproven",
            "precondition_failed",
            "未知 SN 标识类型不能绑定既有 SN 主数据",
        )
    if len(serials) != 1:
        _fail(
            "opening_observation_disposition_serial_unproven",
            "precondition_failed",
            "现场 SN 不能唯一解析为既有启用 SN",
        )
    serial = serials[0]
    if observation.serial_identifier_type == "qr_code":
        if len(mappings) != 1 or mappings[0].object_id != serial.id:
            _fail(
                "opening_observation_disposition_serial_qr_conflict",
                "precondition_failed",
                "SN 二维码与 SN 主数据映射不唯一或不一致",
            )
    if observation.serial_id not in {None, serial.id}:
        _evidence_invalid()
    if serial.material_id != material.id or serial.lot_id != (
        lot.id if lot is not None else None
    ):
        _fail(
            "opening_observation_disposition_serial_binding_invalid",
            "precondition_failed",
            "既有 SN 与已证明的物料或批次不一致",
        )
    return serial


def _load_cutoff_policy(
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
            .order_by(MaterialInventoryPolicy.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(rows) != 1:
        _fail(
            "opening_observation_disposition_policy_ambiguous",
            "precondition_failed",
            "截止时点物料追踪策略缺失或不唯一",
        )
    return rows[0]


def _validate_policy_binding(
    observation: StocktakeCountObservation,
    policy: MaterialInventoryPolicy,
    lot: InventoryLot | None,
    serial: InventorySerial | None,
) -> None:
    qty = observation.counted_qty
    if (
        not isinstance(qty, Decimal)
        or not qty.is_finite()
        or qty <= 0
        or qty >= _MAX_QUANTITY
        or _decimal_scale(qty) > policy.quantity_scale
        or (not policy.allow_fraction and qty != qty.to_integral_value())
    ):
        _fail(
            "opening_observation_disposition_quantity_policy_invalid",
            "precondition_failed",
            "现场数量不符合截止时点物料数量精度策略",
        )
    actual = (lot is not None, serial is not None)
    expected = {
        "none": (False, False),
        "lot": (True, False),
        "serial": (False, True),
        "lot_and_serial": (True, True),
    }.get(policy.tracking_mode)
    if expected is None or actual != expected or (
        serial is not None and qty != Decimal("1")
    ):
        _fail(
            "opening_observation_disposition_tracking_policy_invalid",
            "precondition_failed",
            "已证明的批次/SN 维度不符合截止时点追踪策略",
        )


def _plan_opening_observation_disposition_replay(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: RecordOpeningObservationDispositionCommand,
    row: StocktakeObservationDisposition,
    task: FormalStocktakeTask,
    observation: StocktakeCountObservation,
    difference: StocktakeDifference,
    submission: StocktakeRoundSubmission,
    difference_completion: StocktakeDifferenceSetCompletion,
    key_hash: str,
    request_sha256: str,
    resolution: _Resolution | None = None,
) -> _OpeningObservationDispositionReplayPlan:
    """Validate core evidence and capture one audit coordinate without locking it.

    The caller must already hold the complete task principal/reference graph.
    This phase performs ordinary reads only and deliberately does not acquire
    the audit owner; the returned plan is later bound to one transaction-local
    audit proof.
    """

    if row.request_sha256 != request_sha256:
        _idempotency_conflict()
    # Validate immutable anchors before dereferencing either one.  A damaged
    # replay row must fail with the stable evidence error, never fall through
    # to an AttributeError while trying to re-prove a resolution.
    if (
        row.task_id != task.id
        or row.task_id != command.task_id
        or row.round_id != command.round_id
        or row.scope_id != observation.scope_id
        or row.observation_id != observation.id
        or row.observation_id != command.observation_id
    ):
        _replay_evidence_invalid()
    if resolution is None:
        if (
            command.disposition == "resolved_existing_master"
            or any(
                value is not None
                for value in (
                    command.resolved_material_id,
                    command.resolved_lot_id,
                    command.resolved_serial_id,
                    row.resolved_material_id,
                    row.resolved_lot_id,
                    row.resolved_serial_id,
                )
            )
        ):
            _replay_evidence_invalid()
        # Unresolved dispositions deliberately bind no shared master IDs, so
        # their replay proof is complete without a late reference-graph lock.
        resolution = _Resolution(None, None, None)
    decided_at = _as_utc(row.decided_at)
    assignment = db.scalar(
        select(RoleAssignment)
        .where(RoleAssignment.id == row.decided_role_assignment_id)
        .execution_options(populate_existing=True)
    )
    role = db.scalar(
        select(Role)
        .where(Role.id == assignment.role_id)
        .execution_options(populate_existing=True)
    ) if assignment is not None else None
    if (
        row.disposition != command.disposition
        or row.reason_code != command.reason_code
        or row.comment != command.comment
        or row.resolved_material_id != resolution.material_id
        or row.resolved_lot_id != resolution.lot_id
        or row.resolved_serial_id != resolution.serial_id
        or row.idempotency_key_hash != key_hash
        or row.decided_by_user_id != actor.user_id
        or row.decided_by_person_id != actor.person_id
        or row.authorization_version <= 0
        or row.authorization_version > actor.authorization_version
        or assignment is None
        or role is None
        or assignment.user_id != row.decided_by_user_id
        or assignment.status not in {"active", "expired", "revoked"}
        or role.code != row.role_code
        or role.is_external
        or assignment.scope_type != row.scope_type
        or assignment.scope_id != row.scope_id_snapshot
        or _as_utc(assignment.valid_from) > decided_at
        or (
            assignment.valid_to is not None
            and decided_at >= _as_utc(assignment.valid_to)
        )
        or (
            assignment.revoked_at is not None
            and decided_at >= _as_utc(assignment.revoked_at)
        )
        or (
            row.role_code == "admin"
            and (row.scope_type != "national" or row.scope_id_snapshot != "*")
        )
        or (
            row.role_code == "provincial_manager"
            and (
                row.scope_type != "organization"
                or not _same_uuid(row.scope_id_snapshot, task.region_org_id)
                or row.disposition == "resolved_existing_master"
            )
        )
        or row.role_code not in {"admin", "provincial_manager"}
        or _as_utc(row.created_at) != decided_at
    ):
        _replay_evidence_invalid()
    grant = ScopeGrant(
        assignment_id=assignment.id,
        role_code=row.role_code,
        scope_type=row.scope_type,
        scope_id=row.scope_id_snapshot,
        valid_from=_as_utc(assignment.valid_from),
        valid_to=(
            _as_utc(assignment.valid_to) if assignment.valid_to is not None else None
        ),
    )
    authorization_sha256 = _authorization_sha256(
        FormalPrincipal(
            user_id=row.decided_by_user_id,
            person_id=row.decided_by_person_id,
            account_status="active",
            employment_status="active",
            authorization_version=row.authorization_version,
            access_mode="active",
            assignments=(),
            entitlements=(),
        ),
        assignment,
        grant,
        decided_at,
    )
    expected_manifest = _disposition_manifest_sha256(
        disposition_id=row.id,
        command=command,
        observation=observation,
        difference=difference,
        submission=submission,
        difference_completion=difference_completion,
        resolution=resolution,
        authorization_sha256=authorization_sha256,
        request_sha256=request_sha256,
        key_hash=key_hash,
        decided_at=decided_at,
    )
    if (
        row.authorization_sha256 != authorization_sha256
        or row.disposition_manifest_sha256 != expected_manifest
    ):
        _replay_evidence_invalid()
    return _capture_audit_evidence_plan(db, row, difference_completion)


def _validate_replay(
    db: Session,
    *,
    actor: FormalPrincipal,
    command: RecordOpeningObservationDispositionCommand,
    row: StocktakeObservationDisposition,
    task: FormalStocktakeTask,
    observation: StocktakeCountObservation,
    difference: StocktakeDifference,
    submission: StocktakeRoundSubmission,
    difference_completion: StocktakeDifferenceSetCompletion,
    key_hash: str,
    request_sha256: str,
    resolution: _Resolution | None = None,
) -> None:
    """Canonical standalone replay with one final audit proof acquisition."""

    plan = _plan_opening_observation_disposition_replay(
        db,
        actor=actor,
        command=command,
        row=row,
        task=task,
        observation=observation,
        difference=difference,
        submission=submission,
        difference_completion=difference_completion,
        key_hash=key_hash,
        request_sha256=request_sha256,
        resolution=resolution,
    )
    _head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    _validate_opening_observation_disposition_replay_from_prelocked_audit_graph(
        db,
        plan=plan,
        audit_proof=audit_proof,
    )


def _validate_audit_evidence(
    db: Session,
    row: StocktakeObservationDisposition,
    difference_completion: StocktakeDifferenceSetCompletion,
) -> None:
    """Standalone compatibility boundary: plan first, then audit once."""

    plan = _capture_audit_evidence_plan(db, row, difference_completion)
    _head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key=INVENTORY_STREAM_KEY,
    )
    _validate_opening_observation_disposition_replay_from_prelocked_audit_graph(
        db,
        plan=plan,
        audit_proof=audit_proof,
    )


def _capture_audit_evidence_plan(
    db: Session,
    row: StocktakeObservationDisposition,
    difference_completion: StocktakeDifferenceSetCompletion,
) -> _OpeningObservationDispositionReplayPlan:
    """Ordinary-read the unique event before any caller acquires audit."""

    rows = tuple(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.action == _ACTION,
                AuditEvent.aggregate_type == _AGGREGATE_TYPE,
                AuditEvent.aggregate_id == str(row.id),
            )
        ).all()
    )
    if len(rows) != 1:
        _replay_evidence_invalid()
    audit = rows[0]
    if not _audit_evidence_matches(row, difference_completion, audit):
        _replay_evidence_invalid()
    transaction = db.get_transaction()
    if transaction is None:
        _replay_evidence_invalid()
    return _OpeningObservationDispositionReplayPlan(
        session=db,
        transaction=transaction,
        disposition_id=row.id,
        difference_completion_id=difference_completion.id,
        disposition_manifest_sha256=row.disposition_manifest_sha256,
        difference_manifest_sha256=(
            difference_completion.difference_manifest_sha256
        ),
        audit_event_id=audit.id,
        seal=_OBSERVATION_DISPOSITION_REPLAY_PLAN_SEAL,
    )


def _require_opening_observation_disposition_replay_plan(
    db: Session,
    plan: object,
) -> _OpeningObservationDispositionReplayPlan:
    transaction = db.get_transaction()
    if (
        not isinstance(plan, _OpeningObservationDispositionReplayPlan)
        or plan.seal is not _OBSERVATION_DISPOSITION_REPLAY_PLAN_SEAL
        or plan.session is not db
        or transaction is None
        or plan.transaction is not transaction
    ):
        _replay_evidence_invalid()
    return plan


def _validate_opening_observation_disposition_replay_from_prelocked_audit_graph(
    db: Session,
    *,
    plan: object,
    audit_proof: object,
) -> None:
    """Validate one pre-audit plan using the caller's final audit proof."""

    checked = _require_opening_observation_disposition_replay_plan(db, plan)
    row = db.get(
        StocktakeObservationDisposition,
        checked.disposition_id,
        populate_existing=True,
    )
    difference_completion = db.get(
        StocktakeDifferenceSetCompletion,
        checked.difference_completion_id,
        populate_existing=True,
    )
    audit_rows = tuple(
        db.scalars(
            select(AuditEvent)
            .where(
                AuditEvent.action == _ACTION,
                AuditEvent.aggregate_type == _AGGREGATE_TYPE,
                AuditEvent.aggregate_id == str(checked.disposition_id),
            )
            .execution_options(populate_existing=True)
        ).all()
    )
    if (
        row is None
        or difference_completion is None
        or len(audit_rows) != 1
        or audit_rows[0].id != checked.audit_event_id
        or row.disposition_manifest_sha256
        != checked.disposition_manifest_sha256
        or difference_completion.difference_manifest_sha256
        != checked.difference_manifest_sha256
        or not _audit_evidence_matches(
            row,
            difference_completion,
            audit_rows[0],
        )
    ):
        _replay_evidence_invalid()
    audit = audit_rows[0]
    try:
        verified = _verify_audit_event_with_prelocked_proof(
            db,
            proof=audit_proof,
            stream_key=INVENTORY_STREAM_KEY,
            event_id=checked.audit_event_id,
        )
    except AuditChainError as exc:
        _fail(
            "opening_observation_disposition_replay_evidence_invalid",
            "service_unavailable",
            "待核实观察处置幂等证据图不完整或相互矛盾",
            cause=exc,
        )
    if verified.id != checked.audit_event_id:
        _replay_evidence_invalid()


def _audit_evidence_matches(
    row: StocktakeObservationDisposition,
    difference_completion: StocktakeDifferenceSetCompletion,
    audit: AuditEvent,
) -> bool:
    return not (
        audit.actor_user_id != row.decided_by_user_id
        or audit.before_jsonb is not None
        or audit.after_jsonb != _audit_after_jsonb(row, difference_completion)
        or _as_utc(audit.occurred_at) != _as_utc(row.decided_at)
        or not audit.request_id.startswith("opening-observation-disposition-request-")
        or not _SHA256.fullmatch(
            audit.request_id.removeprefix(
                "opening-observation-disposition-request-"
            )
        )
    )


def _audit_after_jsonb(
    row: StocktakeObservationDisposition,
    difference_completion: StocktakeDifferenceSetCompletion,
) -> dict[str, object]:
    return {
        "authorization_sha256": row.authorization_sha256,
        "authorization_version": row.authorization_version,
        "decided_by_person_id": str(row.decided_by_person_id),
        "decided_role_assignment_id": str(row.decided_role_assignment_id),
        "difference_manifest_sha256": difference_completion.difference_manifest_sha256,
        "difference_set_completion_id": str(difference_completion.id),
        "disposition": row.disposition,
        "disposition_manifest_sha256": row.disposition_manifest_sha256,
        "observation_id": str(row.observation_id),
        "request_sha256": row.request_sha256,
        "resolved_lot_id": str(row.resolved_lot_id) if row.resolved_lot_id else None,
        "resolved_material_id": (
            str(row.resolved_material_id) if row.resolved_material_id else None
        ),
        "resolved_serial_id": (
            str(row.resolved_serial_id) if row.resolved_serial_id else None
        ),
        "role_code": row.role_code,
        "round_id": str(row.round_id),
        "scope_id": str(row.scope_id),
        "scope_id_snapshot": row.scope_id_snapshot,
        "scope_type": row.scope_type,
        "task_id": str(row.task_id),
    }


def _disposition_manifest_sha256(
    *,
    disposition_id: uuid.UUID,
    command: RecordOpeningObservationDispositionCommand,
    observation: StocktakeCountObservation,
    difference: StocktakeDifference,
    submission: StocktakeRoundSubmission,
    difference_completion: StocktakeDifferenceSetCompletion,
    resolution: _Resolution,
    authorization_sha256: str,
    request_sha256: str,
    key_hash: str,
    decided_at: datetime,
) -> str:
    return _hash_document(
        {
            "authorization_sha256": authorization_sha256,
            "decided_at": _canonical_timestamp(decided_at),
            "difference": {
                "affected_qty": _canonical_decimal(difference.affected_qty),
                "counted_qty": _canonical_decimal(difference.counted_qty),
                "difference_id": str(difference.id),
                "difference_no": difference.difference_no,
                "difference_type": difference.difference_type,
                "reason_code": difference.reason_code,
            },
            "difference_set_completion": {
                "difference_manifest_sha256": (
                    difference_completion.difference_manifest_sha256
                ),
                "id": str(difference_completion.id),
                "request_sha256": difference_completion.request_sha256,
            },
            "disposition": command.disposition,
            "disposition_id": str(disposition_id),
            "idempotency_key_hash": key_hash,
            "observation": _observation_manifest_document(observation),
            "reason_code": command.reason_code,
            "comment": command.comment,
            "request_sha256": request_sha256,
            "resolution": {
                "lot_id": str(resolution.lot_id) if resolution.lot_id else None,
                "material_id": (
                    str(resolution.material_id) if resolution.material_id else None
                ),
                "serial_id": (
                    str(resolution.serial_id) if resolution.serial_id else None
                ),
            },
            "round_submission": {
                "count_manifest_sha256": submission.count_manifest_sha256,
                "id": str(submission.id),
                "round_manifest_sha256": submission.round_manifest_sha256,
            },
            "schema": "cloud_oam.opening_stocktake.observation_disposition.v1",
            "scope_id": str(observation.scope_id),
            "round_id": str(observation.round_id),
            "task_id": str(observation.task_id),
        }
    )


def _observation_manifest_document(
    row: StocktakeCountObservation,
) -> dict[str, object]:
    return {
        "availability_bucket": row.availability_bucket,
        "condition_code": row.condition_code,
        "count_method": row.count_method,
        "counted_at": _canonical_timestamp(row.counted_at),
        "counted_by_user_id": row.counted_by_user_id,
        "counted_qty": _canonical_decimal(row.counted_qty),
        "created_at": _canonical_timestamp(row.created_at),
        "custodian_person_id_snapshot": (
            str(row.custodian_person_id_snapshot)
            if row.custodian_person_id_snapshot
            else None
        ),
        "dimension_sha256": row.dimension_sha256,
        "id": str(row.id),
        "idempotency_key_hash": row.idempotency_key_hash,
        "location_id": str(row.location_id),
        "lot_id": str(row.lot_id) if row.lot_id else None,
        "lot_no_raw": row.lot_no_raw,
        "material_id": str(row.material_id) if row.material_id else None,
        "material_identifier_raw": row.material_identifier_raw,
        "material_identifier_type": row.material_identifier_type,
        "observation_no": row.observation_no,
        "owner_org_id": str(row.owner_org_id),
        "reason_code": row.reason_code,
        "remark": row.remark,
        "request_sha256": row.request_sha256,
        "serial_id": str(row.serial_id) if row.serial_id else None,
        "serial_identifier_type": row.serial_identifier_type,
        "serial_no_raw": row.serial_no_raw,
        "verification_status": row.verification_status,
    }


def _authorization_sha256(
    actor: FormalPrincipal,
    assignment: RoleAssignment,
    grant: ScopeGrant,
    decided_at: datetime,
) -> str:
    return _hash_document(
        {
            "assignment_id": str(assignment.id),
            "authorization_version": actor.authorization_version,
            "decided_at": _canonical_timestamp(decided_at),
            "person_id": str(actor.person_id),
            "role_code": grant.role_code,
            "schema": "cloud_oam.opening_stocktake.observation_disposition_authorization.v1",
            "scope_id": grant.scope_id,
            "scope_type": grant.scope_type,
            "user_id": actor.user_id,
        }
    )


def _request_sha256(
    actor: FormalPrincipal,
    command: RecordOpeningObservationDispositionCommand,
) -> str:
    return _hash_document(
        {
            "actor_person_id": str(actor.person_id),
            "actor_user_id": actor.user_id,
            "comment": command.comment,
            "disposition": command.disposition,
            "observation_id": str(command.observation_id),
            "reason_code": command.reason_code,
            "resolved_lot_id": (
                str(command.resolved_lot_id) if command.resolved_lot_id else None
            ),
            "resolved_material_id": (
                str(command.resolved_material_id)
                if command.resolved_material_id
                else None
            ),
            "resolved_serial_id": (
                str(command.resolved_serial_id)
                if command.resolved_serial_id
                else None
            ),
            "round_id": str(command.round_id),
            "schema": "cloud_oam.opening_stocktake.observation_disposition_request.v1",
            "task_id": str(command.task_id),
        }
    )


def _validate_command(
    command: RecordOpeningObservationDispositionCommand,
) -> RecordOpeningObservationDispositionCommand:
    if not isinstance(command, RecordOpeningObservationDispositionCommand):
        _fail(
            "opening_observation_disposition_command_required",
            "invalid_request",
            "待核实观察处置命令类型无效",
        )
    task_id = _require_uuid("task_id", command.task_id)
    round_id = _require_uuid("round_id", command.round_id)
    observation_id = _require_uuid("observation_id", command.observation_id)
    if command.disposition not in _DISPOSITIONS:
        _fail(
            "opening_observation_disposition_value_invalid",
            "invalid_request",
            "待核实观察处置结论无效",
        )
    reason_code = _require_text("reason_code", command.reason_code, 80)
    comment = _require_text("comment", command.comment, 4000, allow_empty=True)
    material_id = _optional_uuid("resolved_material_id", command.resolved_material_id)
    lot_id = _optional_uuid("resolved_lot_id", command.resolved_lot_id)
    serial_id = _optional_uuid("resolved_serial_id", command.resolved_serial_id)
    if command.disposition == "resolved_existing_master":
        if material_id is None:
            _fail(
                "opening_observation_disposition_material_required",
                "invalid_request",
                "解析既有主数据必须明确物料标识",
            )
    elif any(value is not None for value in (material_id, lot_id, serial_id)):
        _fail(
            "opening_observation_disposition_unresolved_binding_invalid",
            "invalid_request",
            "待核实或复盘处置不得携带主数据标识",
        )
    if command.disposition != "resolved_existing_master" and not comment:
        _fail(
            "opening_observation_disposition_comment_required",
            "invalid_request",
            "待核实或复盘处置必须说明原因",
        )
    return RecordOpeningObservationDispositionCommand(
        task_id=task_id,
        round_id=round_id,
        observation_id=observation_id,
        disposition=command.disposition,
        reason_code=reason_code,
        comment=comment,
        resolved_material_id=material_id,
        resolved_lot_id=lot_id,
        resolved_serial_id=serial_id,
    )


def _validate_supplied_actor(actor: FormalPrincipal) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail(
            "formal_principal_required",
            "forbidden",
            "待核实观察处置必须使用正式权限主体",
        )
    if (
        actor.account_status != "active"
        or actor.employment_status != "active"
        or actor.access_mode != "active"
    ):
        _fail(
            "opening_observation_disposition_actor_inactive",
            "forbidden",
            "当前账号或人员不可执行待核实观察处置",
        )
    return actor


def _require_current_actor(
    db: Session,
    supplied: FormalPrincipal,
    now: datetime,
) -> FormalPrincipal:
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError as exc:
        _fail(
            "opening_observation_disposition_actor_not_current",
            "forbidden",
            "正式权限上下文已失效，请重新读取",
            cause=exc,
        )
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
    ):
        _fail(
            "opening_observation_disposition_actor_principal_stale",
            "precondition_failed",
            "权限版本已变化，请重新读取后再操作",
        )
    if (
        current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail(
            "opening_observation_disposition_actor_inactive",
            "forbidden",
            "当前账号或人员不可执行待核实观察处置",
        )
    return current


def _result(
    row: StocktakeObservationDisposition,
    *,
    replayed: bool,
) -> OpeningObservationDispositionResult:
    return OpeningObservationDispositionResult(
        disposition_id=row.id,
        task_id=row.task_id,
        round_id=row.round_id,
        scope_id=row.scope_id,
        observation_id=row.observation_id,
        disposition=row.disposition,
        resolved_material_id=row.resolved_material_id,
        resolved_lot_id=row.resolved_lot_id,
        resolved_serial_id=row.resolved_serial_id,
        disposition_manifest_sha256=row.disposition_manifest_sha256,
        replayed=replayed,
    )


def _storage_hash(raw_key: str) -> str:
    return hashlib.sha256(
        (
            "cloud_oam.opening_stocktake.observation_disposition."
            f"idempotency.v1\0{raw_key}"
        ).encode()
    ).hexdigest()


def _request_reference(raw: str) -> str:
    digest = hashlib.sha256(
        (
            "cloud_oam.opening_stocktake.observation_disposition."
            f"request.v1\0{raw}"
        ).encode()
    ).hexdigest()
    return f"opening-observation-disposition-request-{digest}"


def _hash_document(document: Mapping[str, object]) -> str:
    try:
        encoded = json.dumps(
            document,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    except (TypeError, ValueError) as exc:
        _fail(
            "opening_observation_disposition_manifest_invalid",
            "invalid_request",
            "待核实观察处置证据无法规范化",
            cause=exc,
        )
    return hashlib.sha256(encoded).hexdigest()


def _canonical_decimal(value: Decimal) -> str:
    return format(value.normalize(), "f")


def _canonical_timestamp(value: datetime) -> str:
    return _as_utc(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _decimal_scale(value: Decimal) -> int:
    return max(0, -value.normalize().as_tuple().exponent)


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


def _database_now(db: Session) -> datetime:
    if db.get_bind().dialect.name == "postgresql":
        value = db.scalar(select(func.clock_timestamp()))
        if not isinstance(value, datetime):
            _fail(
                "opening_observation_disposition_server_time_unavailable",
                "service_unavailable",
                "数据库服务端时间不可用",
            )
        return _as_utc(value)
    return datetime.now(timezone.utc)


def _require_idempotency_key(value: str) -> str:
    if (
        not isinstance(value, str)
        or not 16 <= len(value) <= 200
        or _PRINTABLE.fullmatch(value) is None
    ):
        _fail(
            "opening_observation_disposition_idempotency_key_invalid",
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
            "opening_observation_disposition_request_id_invalid",
            "invalid_request",
            "请求标识必须为 8 至 160 位可打印 ASCII 字符",
        )
    return value


def _require_uuid(field: str, value: object) -> uuid.UUID:
    try:
        checked = value if isinstance(value, uuid.UUID) else uuid.UUID(str(value))
    except (TypeError, ValueError):
        _fail(
            f"opening_observation_disposition_{field}_invalid",
            "invalid_request",
            f"{field} 必须为 UUID",
        )
    if checked.int == 0:
        _fail(
            f"opening_observation_disposition_{field}_invalid",
            "invalid_request",
            f"{field} 不能为零 UUID",
        )
    return checked


def _optional_uuid(field: str, value: object | None) -> uuid.UUID | None:
    return _require_uuid(field, value) if value is not None else None


def _require_text(
    field: str,
    value: object,
    limit: int,
    *,
    allow_empty: bool = False,
) -> str:
    if (
        not isinstance(value, str)
        or len(value) > limit
        or value != value.strip()
        or (not allow_empty and not value)
    ):
        _fail(
            f"opening_observation_disposition_{field}_invalid",
            "invalid_request",
            f"{field} 格式无效",
        )
    return value


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _same_uuid(left: object, right: object) -> bool:
    try:
        return uuid.UUID(str(left)) == uuid.UUID(str(right))
    except (TypeError, ValueError):
        return False


def _idempotency_conflict() -> None:
    _fail(
        "opening_observation_disposition_idempotency_conflict",
        "conflict",
        "幂等键已绑定不同的待核实观察处置请求",
    )


def _evidence_invalid() -> None:
    _fail(
        "opening_observation_disposition_evidence_invalid",
        "service_unavailable",
        "待核实观察与期初盘点证据不完整或相互矛盾",
    )


def _replay_evidence_invalid() -> None:
    _fail(
        "opening_observation_disposition_replay_evidence_invalid",
        "service_unavailable",
        "待核实观察处置幂等证据图不完整或相互矛盾",
    )


def _fail(
    code: str,
    category: str,
    message: str,
    *,
    cause: Exception | None = None,
) -> None:
    error = OpeningObservationDispositionError(code, category, message)
    if cause is None:
        raise error
    raise error from cause


__all__ = [
    "OpeningObservationDispositionError",
    "OpeningObservationDispositionResult",
    "RecordOpeningObservationDispositionCommand",
    "record_opening_observation_disposition",
]
