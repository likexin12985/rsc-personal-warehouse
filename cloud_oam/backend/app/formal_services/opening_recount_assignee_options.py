"""Read-only assignee directory for one exact opening-stocktake recount scope.

The command service remains the authority: every returned user is evaluated by
its private, production write-side scope check with ``lock_rows=False`` and is
fully revalidated under locks when the recount is opened.  This query never
opens a recount, changes an assignment, or reads an external system.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, FormalPrincipal
from ..foundation_models import Person, RoleAssignment
from ..inventory_models import CustodyAssignment, StockLocation
from ..models import User
from ..opening_recount_assignee_option_schemas import (
    OpeningRecountAssigneeOptionOut,
    OpeningRecountAssigneeOptionPageOut,
)
from ..stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    StocktakeRecountCase,
    StocktakeRound,
)
from . import opening_stocktake_recount as recount_service
from . import opening_stocktake as opening_service


_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "conflict": 409,
    "precondition_failed": 412,
    "service_unavailable": 503,
}
_CANDIDATE_OMISSION_CODES = frozenset(
    {
        "opening_recount_assignee_inactive",
        "opening_recount_assignee_scope_forbidden",
        "opening_recount_assignment_not_current",
    }
)
_LOCAL_NOT_CURRENT_CAUSES = frozenset(
    {
        "账号状态不允许建立正式权限上下文",
        "账号没有已验证的正式登录身份",
        "账号没有当前有效的角色授权",
    }
)
_RAW_SCAN_BATCH_SIZE = 64
_MAX_RAW_IDENTITIES_SCANNED = 1000


class OpeningRecountAssigneeOptionError(RuntimeError):
    """Stable, database-detail-free failure for this read boundary."""

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
class _Context:
    task: FormalStocktakeTask
    source_round: StocktakeRound
    target_scope: FormalStocktakeScope
    target_location: StockLocation
    scopes: tuple[FormalStocktakeScope, ...]
    locations: dict[uuid.UUID, StockLocation]
    custodies: tuple[CustodyAssignment, ...]
    freezes: tuple[InventoryFreeze, ...]
    rounds: tuple[StocktakeRound, ...]


@dataclass(frozen=True, slots=True)
class _CandidateSnapshot:
    user_id: str
    person_id: uuid.UUID
    display_name: str
    employee_no: str
    role_code: str
    identity_signature: tuple[object, ...]
    principal_signature: tuple[object, ...]
    assignment_signature: tuple[object, ...]
    grant_signature: tuple[object, ...]


def list_opening_recount_assignee_options(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
    source_round_id: uuid.UUID,
    scope_id: uuid.UUID,
    expected_task_version: int,
    limit: int,
    after_person_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> OpeningRecountAssigneeOptionPageOut:
    """Return current, task-bound candidates without taking any row lock."""

    checked_task_id = _required_uuid(task_id, "opening_recount_assignee_task_invalid")
    checked_round_id = _required_uuid(
        source_round_id,
        "opening_recount_assignee_round_invalid",
    )
    checked_scope_id = _required_uuid(
        scope_id,
        "opening_recount_assignee_scope_invalid",
    )
    checked_version = _version(expected_task_version)
    checked_limit = _limit(limit)
    checked_after = _cursor(after_person_id)
    fixed_now = now is not None
    effective_now = _aware(now or datetime.now(timezone.utc))

    try:
        with db.no_autoflush:
            principal = _current_actor(db, actor, now=effective_now)
            context = _load_context(
                db,
                actor=principal,
                task_id=checked_task_id,
                source_round_id=checked_round_id,
                scope_id=checked_scope_id,
                expected_task_version=checked_version,
                now=effective_now,
            )
            actor_signature = _principal_signature(principal)
            context_signature = _context_signature(context)

            candidate_snapshots = _scan_candidate_snapshots(
                db,
                context=context,
                after_person_id=checked_after,
                limit=checked_limit,
                now=effective_now,
            )

            # Re-read the complete authorization and task coordinates.  A
            # stale option page is withheld; the POST still revalidates under
            # its canonical owner locks.
            final_now = (
                effective_now
                if fixed_now
                else _aware(datetime.now(timezone.utc))
            )
            final_principal = _current_actor(db, actor, now=final_now)
            final_context = _load_context(
                db,
                actor=final_principal,
                task_id=checked_task_id,
                source_round_id=checked_round_id,
                scope_id=checked_scope_id,
                expected_task_version=checked_version,
                now=final_now,
            )
            if (
                _principal_signature(final_principal) != actor_signature
                or _context_signature(final_context) != context_signature
            ):
                _fail(
                    "opening_recount_assignee_read_conflict",
                    "conflict",
                    "期初复盘任务或权限在读取候选人员时发生变化",
                )

            candidate_snapshots = _revalidate_candidate_snapshots(
                db,
                context=final_context,
                snapshots=candidate_snapshots,
                now=final_now,
            )
            page_snapshots = candidate_snapshots[:checked_limit]

            next_after_person_id = (
                page_snapshots[-1].person_id
                if len(candidate_snapshots) > checked_limit and page_snapshots
                else None
            )
            return OpeningRecountAssigneeOptionPageOut(
                task_id=context.task.id,
                source_round_id=context.source_round.id,
                scope_id=context.target_scope.id,
                location_id=context.target_location.id,
                region_org_id=context.task.region_org_id,
                task_version=context.task.version,
                actor_person_id=final_principal.person_id,
                actor_authorization_version=final_principal.authorization_version,
                items=tuple(
                    OpeningRecountAssigneeOptionOut(
                        user_id=row.user_id,
                        person_id=row.person_id,
                        display_name=row.display_name,
                        employee_no=row.employee_no,
                        role_code=row.role_code,
                    )
                    for row in page_snapshots
                ),
                next_after_person_id=next_after_person_id,
            )
    except OpeningRecountAssigneeOptionError:
        raise
    except recount_service.OpeningStocktakeRecountError as exc:
        _translate_recount_error(exc)
    except opening_service.OpeningStocktakeError as exc:
        category = exc.category
        if exc.code == "opening_scope_authorization_invalid":
            category = "service_unavailable"
        _fail(
            exc.code,
            category if category in _HTTP_STATUS_BY_CATEGORY else "service_unavailable",
            exc.message,
        )
    except DBAPIError:
        _fail(
            "opening_recount_assignee_database_unavailable",
            "service_unavailable",
            "期初复盘候选人员暂时不可用",
        )
    except (TypeError, ValueError):
        _fail(
            "opening_recount_assignee_projection_invalid",
            "service_unavailable",
            "期初复盘候选人员数据不完整",
        )
    raise AssertionError("unreachable opening recount assignee options boundary")


def _load_context(
    db: Session,
    *,
    actor: FormalPrincipal,
    task_id: uuid.UUID,
    source_round_id: uuid.UUID,
    scope_id: uuid.UUID,
    expected_task_version: int,
    now: datetime,
) -> _Context:
    task = db.scalar(
        select(FormalStocktakeTask)
        .where(FormalStocktakeTask.id == task_id)
        .execution_options(populate_existing=True)
    )
    if task is None:
        _fail(
            "opening_recount_assignee_task_not_found",
            "not_found",
            "期初盘点任务不存在",
        )
    # Do not reveal the target task's version, state, scopes or freeze graph
    # before proving that this exact actor may manage its region.
    recount_service._authorize_opener(
        db,
        actor=actor,
        task=task,
        now=now,
        lock_rows=False,
    )
    if task.version != expected_task_version:
        _fail(
            "opening_recount_assignee_task_version_conflict",
            "conflict",
            "期初盘点任务版本已变化",
        )
    if task.task_type != "opening" or task.status != "recount_required":
        _fail(
            "opening_recount_assignee_task_state_invalid",
            "precondition_failed",
            "期初盘点任务当前不允许选择复盘人员",
        )

    scopes = tuple(
        db.scalars(
            select(FormalStocktakeScope)
            .where(FormalStocktakeScope.task_id == task.id)
            .order_by(FormalStocktakeScope.scope_no, FormalStocktakeScope.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    target_scope = next((row for row in scopes if row.id == scope_id), None)
    if target_scope is None:
        _fail(
            "opening_recount_assignee_scope_not_found",
            "not_found",
            "期初盘点范围不属于当前任务",
        )
    location_ids = tuple(sorted({row.location_id for row in scopes}, key=str))
    locations = {
        row.id: row
        for row in db.scalars(
            select(StockLocation)
            .where(StockLocation.id.in_(location_ids))
            .order_by(StockLocation.id)
            .execution_options(populate_existing=True)
        ).all()
    }
    target_location = locations.get(target_scope.location_id)
    if target_location is None:
        _fail(
            "opening_recount_assignee_location_graph_invalid",
            "precondition_failed",
            "期初盘点范围库位结构不完整",
        )
    rounds = tuple(
        db.scalars(
            select(StocktakeRound)
            .where(StocktakeRound.task_id == task.id)
            .order_by(StocktakeRound.round_no, StocktakeRound.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    source_round = next((row for row in rounds if row.id == source_round_id), None)
    if source_round is None:
        _fail(
            "opening_recount_assignee_round_not_found",
            "not_found",
            "期初复盘来源轮次不属于当前任务",
        )
    freezes = tuple(
        db.scalars(
            select(InventoryFreeze)
            .where(InventoryFreeze.task_id == task.id)
            .order_by(InventoryFreeze.stocktake_scope_id, InventoryFreeze.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    custodies = tuple(
        db.scalars(
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
            .execution_options(populate_existing=True)
        ).all()
    )
    if db.scalar(
        select(StocktakeRecountCase.id)
        .where(StocktakeRecountCase.source_round_id == source_round.id)
        .limit(1)
        .execution_options(populate_existing=True)
    ) is not None:
        _fail(
            "opening_recount_assignee_source_already_recounted",
            "conflict",
            "期初复盘来源轮次已被后续复盘轮次取代",
        )

    recount_service._validate_source_round_structure(
        task=task,
        source_round=source_round,
        scopes=scopes,
        locations=locations,
        all_rounds=rounds,
        allow_downstream=False,
    )
    recount_service._validate_source_freeze_structure(
        task=task,
        scopes=scopes,
        freezes=freezes,
        allow_downstream=False,
    )
    if task.scope_manifest_sha256 != (
        recount_service.canonical_opening_recount_scope_manifest_sha256(
            task.region_org_id, scopes, freezes
        )
    ):
        _fail(
            "opening_recount_assignee_scope_manifest_invalid",
            "precondition_failed",
            "期初复盘人员目录无法重证已封印的范围清单",
        )
    # A task-level manager grant alone does not authorize names from an asset
    # owner or physical location outside that same grant's dimensions.
    opening_service._authorize_scope_dimensions(
        db,
        actor=actor,
        task_region_org_id=task.region_org_id,
        owner_org_id=target_scope.owner_org_id,
        location_owner_org_id=target_location.owner_org_id,
    )
    opening_service._require_location_in_region_tree(
        db, target_location, task.region_org_id, lock_rows=False
    )
    _validate_custody_snapshots(
        scopes=scopes,
        locations=locations,
        custodies=custodies,
    )
    return _Context(
        task=task,
        source_round=source_round,
        target_scope=target_scope,
        target_location=target_location,
        scopes=scopes,
        locations=locations,
        custodies=custodies,
        freezes=freezes,
        rounds=rounds,
    )


def _validate_custody_snapshots(
    *,
    scopes: tuple[FormalStocktakeScope, ...],
    locations: dict[uuid.UUID, StockLocation],
    custodies: tuple[CustodyAssignment, ...],
) -> None:
    by_location: dict[uuid.UUID, list[CustodyAssignment]] = {
        location_id: [] for location_id in locations
    }
    for custody in custodies:
        rows = by_location.get(custody.location_id)
        if rows is None:
            _fail(
                "opening_recount_assignee_custody_graph_invalid",
                "precondition_failed",
                "期初复盘库位保管责任与范围不一致",
            )
        rows.append(custody)
    if any(len(rows) > 1 for rows in by_location.values()):
        _fail(
            "opening_recount_assignee_custody_graph_invalid",
            "service_unavailable",
            "期初复盘库位当前保管责任不唯一",
        )
    for scope in scopes:
        location = locations[scope.location_id]
        rows = by_location[scope.location_id]
        current_person_id = rows[0].custodian_person_id if rows else None
        if (
            current_person_id != scope.custodian_person_id_snapshot
            or location.custodian_person_id not in {None, current_person_id}
            or (
                location.location_type == "personal"
                and (not rows or location.custodian_person_id != current_person_id)
            )
        ):
            _fail(
                "opening_recount_assignee_custody_graph_invalid",
                "precondition_failed",
                "期初复盘库位保管责任已变化或未同步",
            )


def _scan_candidate_snapshots(
    db: Session,
    *,
    context: _Context,
    after_person_id: uuid.UUID | None,
    limit: int,
    now: datetime,
) -> tuple[_CandidateSnapshot, ...]:
    """Fill one authorized page without returning raw-identity coordinates."""

    snapshots: list[_CandidateSnapshot] = []
    raw_after = after_person_id
    scanned = 0
    while len(snapshots) < limit + 1:
        remaining = _MAX_RAW_IDENTITIES_SCANNED - scanned
        batch_limit = min(_RAW_SCAN_BATCH_SIZE, remaining + 1)
        statement = (
            select(User, Person)
            .join(Person, Person.id == User.person_id)
            .where(
                User.account_status == "active",
                User.is_active.is_(True),
                Person.employment_status == "active",
            )
            .order_by(Person.id, User.id)
            .execution_options(populate_existing=True)
        )
        if raw_after is not None:
            statement = statement.where(Person.id > raw_after)
        raw_rows = tuple(db.execute(statement.limit(batch_limit)).all())
        if not raw_rows:
            break
        if len(raw_rows) > remaining:
            _fail(
                "opening_recount_assignee_scan_limit_exceeded",
                "service_unavailable",
                "期初复盘候选人员目录超过受控扫描上限",
            )
        _validate_unique_identity_rows(raw_rows)
        scanned += len(raw_rows)
        for user, person in raw_rows:
            snapshot = _candidate_snapshot(
                db,
                user=user,
                person=person,
                context=context,
                now=now,
                allow_omission=True,
            )
            if snapshot is not None:
                snapshots.append(snapshot)
                if len(snapshots) == limit + 1:
                    break
        raw_after = raw_rows[-1][1].id
        if len(raw_rows) < batch_limit:
            break
    return tuple(snapshots)


def _revalidate_candidate_snapshots(
    db: Session,
    *,
    context: _Context,
    snapshots: tuple[_CandidateSnapshot, ...],
    now: datetime,
) -> tuple[_CandidateSnapshot, ...]:
    if not snapshots:
        return ()
    user_ids = tuple(row.user_id for row in snapshots)
    identity_rows = tuple(
        db.execute(
            select(User, Person)
            .join(Person, Person.id == User.person_id)
            .where(User.id.in_(user_ids))
            .order_by(Person.id, User.id)
            .execution_options(populate_existing=True)
        ).all()
    )
    _validate_unique_identity_rows(identity_rows)
    by_user_id = {user.id: (user, person) for user, person in identity_rows}
    if set(by_user_id) != set(user_ids):
        _candidate_read_conflict()

    current: list[_CandidateSnapshot] = []
    for expected in snapshots:
        user, person = by_user_id[expected.user_id]
        observed = _candidate_snapshot(
            db,
            user=user,
            person=person,
            context=context,
            now=now,
            allow_omission=False,
        )
        if observed is None or observed != expected:
            _candidate_read_conflict()
        current.append(observed)
    return tuple(current)


def _candidate_snapshot(
    db: Session,
    *,
    user: User,
    person: Person,
    context: _Context,
    now: datetime,
    allow_omission: bool,
) -> _CandidateSnapshot | None:
    try:
        candidate, assignment, grant = recount_service._authorize_scope_assignee(
            db,
            user_id=user.id,
            scope=context.target_scope,
            location=context.target_location,
            now=now,
            lock_rows=False,
        )
    except recount_service.OpeningStocktakeRecountError as exc:
        if _candidate_may_be_omitted(exc):
            if allow_omission:
                return None
            _candidate_read_conflict()
        _fail(
            "opening_recount_assignee_authorization_graph_invalid",
            "service_unavailable",
            "期初复盘候选人员权限图无效",
        )
    if candidate.person_id != person.id or candidate.user_id != user.id:
        _fail(
            "opening_recount_assignee_identity_graph_invalid",
            "service_unavailable",
            "期初复盘候选人员身份绑定不唯一",
        )
    return _CandidateSnapshot(
        user_id=user.id,
        person_id=person.id,
        display_name=person.name,
        employee_no=person.employee_no,
        role_code=grant.role_code,
        identity_signature=_candidate_identity_signature(user, person),
        principal_signature=_principal_signature(candidate),
        assignment_signature=_assignment_signature(assignment),
        grant_signature=(
            grant.assignment_id,
            grant.role_code,
            grant.scope_type,
            grant.scope_id,
            _aware(grant.valid_from),
            _optional_time(grant.valid_to),
        ),
    )


def _candidate_identity_signature(user: User, person: Person) -> tuple[object, ...]:
    return (
        user.id,
        user.person_id,
        user.account_status,
        user.is_active,
        user.authorization_version,
        person.id,
        person.organization_id,
        person.employee_no,
        person.name,
        person.employment_status,
        _optional_time(person.source_updated_at),
    )


def _assignment_signature(assignment: RoleAssignment) -> tuple[object, ...]:
    return (
        assignment.id,
        assignment.user_id,
        assignment.role_id,
        assignment.scope_type,
        assignment.scope_id,
        _aware(assignment.valid_from),
        _optional_time(assignment.valid_to),
        assignment.status,
        assignment.revoked_at,
        assignment.revoked_by,
    )


def _candidate_read_conflict() -> None:
    _fail(
        "opening_recount_assignee_read_conflict",
        "conflict",
        "期初复盘候选人员权限在读取期间发生变化",
    )


def _current_actor(
    db: Session,
    actor: FormalPrincipal,
    *,
    now: datetime,
) -> FormalPrincipal:
    if not isinstance(actor, FormalPrincipal):
        _fail(
            "opening_recount_assignee_actor_invalid",
            "forbidden",
            "期初复盘候选人员必须使用正式权限主体",
        )
    return recount_service._require_current_actor(db, actor, now=now)


def _principal_signature(principal: FormalPrincipal) -> tuple[object, ...]:
    return (
        principal.user_id,
        principal.person_id,
        principal.account_status,
        principal.employment_status,
        principal.authorization_version,
        principal.access_mode,
        tuple(
            sorted(
                (
                    row.assignment_id,
                    row.role_code,
                    row.scope_type,
                    row.scope_id,
                    _aware(row.valid_from),
                    _aware(row.valid_to) if row.valid_to is not None else None,
                )
                for row in principal.assignments
            )
        ),
        tuple(
            sorted(
                (
                    row.assignment_id,
                    row.role_code,
                    row.scope_type,
                    row.scope_id,
                    row.resource,
                    row.action,
                    row.field_code,
                    row.effect,
                )
                for row in principal.entitlements
            )
        ),
    )


def _context_signature(context: _Context) -> tuple[object, ...]:
    task = context.task
    return (
        (
            task.id,
            task.task_no,
            task.task_type,
            task.region_org_id,
            task.status,
            task.blind_count,
            task.cutoff_ledger_cursor,
            _optional_time(task.cutoff_at),
            task.control_source_system_id,
            task.control_sync_run_id,
            _optional_time(task.control_snapshot_at),
            task.current_round_no,
            task.created_by_user_id,
            _optional_time(task.deadline),
            _optional_time(task.issued_at),
            _optional_time(task.frozen_at),
            _optional_time(task.submitted_at),
            _optional_time(task.posted_at),
            _optional_time(task.closed_at),
            _optional_time(task.cancelled_at),
            task.version,
            task.scope_manifest_sha256,
            task.snapshot_manifest_sha256,
            task.control_manifest_sha256,
            task.note,
            _aware(task.created_at),
            _aware(task.updated_at),
        ),
        tuple(
            (
                row.id,
                row.task_id,
                row.scope_no,
                row.scope_mode,
                row.location_id,
                row.owner_org_id,
                row.custodian_person_id_snapshot,
                row.assignee_user_id,
                row.material_id,
                row.condition_code,
                row.availability_bucket,
                row.scope_key,
                row.scope_sha256,
                _aware(row.created_at),
            )
            for row in context.scopes
        ),
        tuple(
            (
                row.id,
                row.code,
                row.name,
                row.location_type,
                row.owner_org_id,
                row.parent_id,
                row.custodian_person_id,
                row.status,
                _aware(row.created_at),
                _aware(row.updated_at),
            )
            for row in sorted(context.locations.values(), key=lambda value: str(value.id))
        ),
        tuple(
            (
                row.id,
                row.location_id,
                row.custodian_person_id,
                _aware(row.valid_from),
                _aware(row.valid_to) if row.valid_to is not None else None,
                row.handover_case_id,
                _aware(row.created_at),
                _aware(row.updated_at),
            )
            for row in context.custodies
        ),
        tuple(
            (
                row.id,
                row.task_id,
                row.stocktake_scope_id,
                row.scope_key,
                row.freeze_mode,
                row.status,
                _aware(row.valid_from),
                _aware(row.valid_to) if row.valid_to is not None else None,
                row.version,
                row.created_by_user_id,
                row.released_by_user_id,
                row.release_reason,
                _aware(row.created_at),
                _aware(row.updated_at),
            )
            for row in context.freezes
        ),
        tuple(
            (
                row.id,
                row.task_id,
                row.round_no,
                row.round_type,
                row.status,
                row.recount_case_id,
                row.submitted_by_user_id,
                _aware(row.started_at),
                _aware(row.submitted_at) if row.submitted_at is not None else None,
                row.count_manifest_sha256,
                row.idempotency_key_hash,
                _aware(row.created_at),
                _aware(row.updated_at),
            )
            for row in context.rounds
        ),
    )


def _validate_unique_identity_rows(rows: tuple[object, ...]) -> None:
    user_ids: set[str] = set()
    person_ids: set[uuid.UUID] = set()
    for user, person in rows:
        if user.id in user_ids or person.id in person_ids or user.person_id != person.id:
            _fail(
                "opening_recount_assignee_identity_graph_invalid",
                "service_unavailable",
                "期初复盘候选人员身份绑定不唯一",
            )
        user_ids.add(user.id)
        person_ids.add(person.id)


def _candidate_may_be_omitted(
    exc: recount_service.OpeningStocktakeRecountError,
) -> bool:
    if exc.code in _CANDIDATE_OMISSION_CODES:
        return True
    if exc.code != "opening_recount_assignee_not_current":
        return False
    cause = exc.__cause__
    return isinstance(cause, FormalAccessError) and str(cause) in (
        _LOCAL_NOT_CURRENT_CAUSES
    )


def _translate_recount_error(exc: recount_service.OpeningStocktakeRecountError) -> None:
    category = exc.category
    if exc.code == "opening_recount_authorization_invalid":
        category = "service_unavailable"
    _fail(exc.code, category, exc.message)


def _required_uuid(value: object, code: str) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail(code, "invalid_request", "期初复盘候选人员坐标无效")
    return value


def _cursor(value: object) -> uuid.UUID | None:
    if value is None:
        return None
    return _required_uuid(value, "opening_recount_assignee_cursor_invalid")


def _version(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        _fail(
            "opening_recount_assignee_task_version_invalid",
            "invalid_request",
            "期初盘点任务版本无效",
        )
    return value


def _limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        _fail(
            "opening_recount_assignee_limit_invalid",
            "invalid_request",
            "期初复盘候选人员分页大小无效",
        )
    return value


def _aware(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _optional_time(value: datetime | None) -> datetime | None:
    return _aware(value) if value is not None else None


def _fail(code: str, category: str, message: str) -> None:
    raise OpeningRecountAssigneeOptionError(code, category, message)


__all__ = [
    "OpeningRecountAssigneeOptionError",
    "list_opening_recount_assignee_options",
]
