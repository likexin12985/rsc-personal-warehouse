"""Read-only, scope-safe option lists for managed stocktake creation.

These queries expose labels for identifiers that the command service already
revalidates under lock.  They never create a task, freeze stock, read an
external system, or infer a provincial manager when no such assignment exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import uuid

from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, FormalPrincipal, load_formal_principal
from ..foundation_models import Organization, Person
from ..inventory_models import CustodyAssignment, StockLocation
from ..models import User
from ..stocktake_option_schemas import (
    StocktakeAssigneeOptionOut,
    StocktakeAssigneeOptionPageOut,
    StocktakeLocationOptionOut,
    StocktakeLocationOptionPageOut,
    StocktakeRegionOptionOut,
    StocktakeRegionOptionPageOut,
)


_HTTP_STATUS_BY_CATEGORY = {
    "invalid_request": 422,
    "forbidden": 403,
    "not_found": 404,
    "service_unavailable": 503,
}
_MANAGER_ROLES = frozenset({"admin", "provincial_manager"})
_COUNT_ROLES = frozenset({"admin", "provincial_manager", "technician"})
_MAX_RAW_IDENTITIES_SCANNED = 1000
_RAW_SCAN_BATCH_SIZE = 100


@dataclass(frozen=True)
class _AssigneeSnapshot:
    option: StocktakeAssigneeOptionOut
    identity_signature: tuple[object, ...]
    principal_signature: tuple[object, ...]


class StocktakeOptionError(RuntimeError):
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


def list_region_options(
    db: Session,
    *,
    actor: FormalPrincipal,
    limit: int,
    after_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> StocktakeRegionOptionPageOut:
    """Return only active region companies the current manager may manage."""

    checked_limit = _limit(limit)
    checked_after = _cursor(after_id, "stocktake_region_cursor_invalid")
    effective_now = _aware(now or datetime.now(timezone.utc))
    try:
        with db.no_autoflush:
            principal = _require_current_actor(db, actor, now=effective_now)
            statement = (
                select(Organization)
                .where(
                    Organization.status == "active",
                    Organization.org_type == "region_company",
                )
                .order_by(Organization.id)
                .execution_options(populate_existing=True)
            )
            if checked_after is not None:
                statement = statement.where(Organization.id >= checked_after)
            candidates = tuple(db.scalars(statement).all())
            eligible = tuple(
                row for row in candidates if _manager_can_manage_region(db, principal, row.id)
            )
            page = eligible[:checked_limit]
            next_after_id = (
                eligible[checked_limit].id if len(eligible) > checked_limit else None
            )
            _require_current_actor(db, principal, now=effective_now)
            return StocktakeRegionOptionPageOut(
                items=tuple(
                    StocktakeRegionOptionOut(
                        region_org_id=row.id,
                        code=row.code,
                        name=row.name,
                        province_code=row.province_code,
                    )
                    for row in page
                ),
                next_after_id=next_after_id,
                authorization_version=principal.authorization_version,
            )
    except StocktakeOptionError:
        raise
    except DBAPIError:
        _fail(
            "stocktake_options_database_unavailable",
            "service_unavailable",
            "盘点创建选项暂时不可用",
        )
    except Exception as exc:
        if isinstance(exc, (TypeError, ValueError)):
            _fail(
                "stocktake_options_projection_invalid",
                "service_unavailable",
                "盘点创建选项数据不完整",
            )
        raise
    raise AssertionError("unreachable stocktake region options boundary")


def list_location_options(
    db: Session,
    *,
    actor: FormalPrincipal,
    region_org_id: uuid.UUID,
    limit: int,
    after_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> StocktakeLocationOptionPageOut:
    """Return active region/personal locations with unambiguous custody."""

    checked_region_id = _required_uuid(region_org_id, "stocktake_region_invalid")
    checked_limit = _limit(limit)
    checked_after = _cursor(after_id, "stocktake_location_cursor_invalid")
    effective_now = _aware(now or datetime.now(timezone.utc))
    try:
        with db.no_autoflush:
            principal = _require_current_actor(db, actor, now=effective_now)
            _require_authorized_region(db, principal, checked_region_id)
            statement = (
                select(StockLocation)
                .where(
                    StockLocation.status == "active",
                    StockLocation.location_type.in_(("region", "personal")),
                )
                .order_by(StockLocation.id)
                .execution_options(populate_existing=True)
            )
            if checked_after is not None:
                statement = statement.where(StockLocation.id >= checked_after)
            eligible: list[tuple[StockLocation, Organization, Person | None]] = []
            for location in db.scalars(statement).all():
                owner = _location_owner_in_region(db, location, checked_region_id)
                if owner is None:
                    continue
                _validate_location_chain(db, location, checked_region_id)
                custodian = _current_custodian(db, location, now=effective_now)
                person = None
                if custodian is not None:
                    person = db.get(Person, custodian)
                    if person is None:
                        _fail(
                            "stocktake_location_custodian_missing",
                            "service_unavailable",
                            "盘点库位保管人员数据不完整",
                        )
                eligible.append((location, owner, person))
            page = tuple(eligible[:checked_limit])
            next_after_id = (
                eligible[checked_limit][0].id
                if len(eligible) > checked_limit
                else None
            )
            _require_current_actor(db, principal, now=effective_now)
            return StocktakeLocationOptionPageOut(
                region_org_id=checked_region_id,
                items=tuple(
                    StocktakeLocationOptionOut(
                        location_id=location.id,
                        code=location.code,
                        name=location.name,
                        location_type=location.location_type,
                        owner_org_id=owner.id,
                        owner_org_name=owner.name,
                        custodian_person_id=(person.id if person is not None else None),
                        custodian_name=(person.name if person is not None else None),
                    )
                    for location, owner, person in page
                ),
                next_after_id=next_after_id,
                authorization_version=principal.authorization_version,
            )
    except StocktakeOptionError:
        raise
    except DBAPIError:
        _fail(
            "stocktake_options_database_unavailable",
            "service_unavailable",
            "盘点创建选项暂时不可用",
        )
    except Exception as exc:
        if isinstance(exc, (TypeError, ValueError)):
            _fail(
                "stocktake_options_projection_invalid",
                "service_unavailable",
                "盘点创建选项数据不完整",
            )
        raise
    raise AssertionError("unreachable stocktake location options boundary")


def list_assignee_options(
    db: Session,
    *,
    actor: FormalPrincipal,
    region_org_id: uuid.UUID,
    location_id: uuid.UUID,
    limit: int,
    after_person_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> StocktakeAssigneeOptionPageOut:
    """Return active people whose current formal role may count the location."""

    checked_region_id = _required_uuid(region_org_id, "stocktake_region_invalid")
    checked_location_id = _required_uuid(location_id, "stocktake_location_invalid")
    checked_limit = _limit(limit)
    checked_after = _cursor(
        after_person_id,
        "stocktake_assignee_cursor_invalid",
    )
    fixed_now = now is not None
    effective_now = _aware(now or datetime.now(timezone.utc))
    try:
        with db.no_autoflush:
            principal = _require_current_actor(db, actor, now=effective_now)
            _require_authorized_region(db, principal, checked_region_id, refresh=True)
            target = _assignee_target(db, checked_region_id, checked_location_id, effective_now)
            principal_signature = _principal_signature(principal)
            snapshots = _scan_assignee_snapshots(
                db, target=target[:2], after_person_id=checked_after,
                limit=checked_limit, now=effective_now,
            )
            final_now = effective_now if fixed_now else _aware(datetime.now(timezone.utc))
            current = _require_current_actor(db, principal, now=final_now)
            _require_authorized_region(db, current, checked_region_id, refresh=True)
            if (
                _principal_signature(current) != principal_signature
                or _assignee_target(db, checked_region_id, checked_location_id, final_now) != target
            ):
                _assignee_read_conflict()
            snapshots = _revalidate_assignee_snapshots(
                db, snapshots=snapshots, target=target[:2], now=final_now,
            )
            # Candidate revalidation can itself span many reads.  Recheck the
            # initiator and target after it, with the actual final clock.
            final_now = effective_now if fixed_now else _aware(datetime.now(timezone.utc))
            current = _require_current_actor(db, principal, now=final_now)
            _require_authorized_region(db, current, checked_region_id, refresh=True)
            if (
                _principal_signature(current) != principal_signature
                or _assignee_target(db, checked_region_id, checked_location_id, final_now) != target
            ):
                _assignee_read_conflict()
            items = tuple(row.option for row in snapshots[:checked_limit])
            next_after_person_id = (
                items[-1].person_id
                if len(snapshots) > checked_limit and items
                else None
            )
            return StocktakeAssigneeOptionPageOut(
                region_org_id=checked_region_id,
                location_id=checked_location_id,
                items=tuple(items),
                next_after_person_id=next_after_person_id,
                authorization_version=principal.authorization_version,
            )
    except StocktakeOptionError:
        raise
    except DBAPIError:
        _fail(
            "stocktake_options_database_unavailable",
            "service_unavailable",
            "盘点创建选项暂时不可用",
        )
    except Exception as exc:
        if isinstance(exc, (TypeError, ValueError)):
            _fail(
                "stocktake_options_projection_invalid",
                "service_unavailable",
                "盘点创建选项数据不完整",
            )
        raise
    raise AssertionError("unreachable stocktake assignee options boundary")


def _assignee_target(db: Session, region_id: uuid.UUID, location_id: uuid.UUID, now: datetime):
    location = db.get(StockLocation, location_id, populate_existing=True)
    if location is None:
        _fail("stocktake_location_not_found", "not_found", "盘点库位不存在")
    owner = _location_owner_in_region(db, location, region_id, refresh=True)
    if owner is None or location.status != "active" or location.location_type not in {"region", "personal"}:
        _fail("stocktake_location_forbidden", "forbidden", "盘点库位不在当前授权区域")
    _validate_location_chain(db, location, region_id, refresh=True)
    custodian = _current_custodian(db, location, now=now)
    if location.location_type == "personal":
        if custodian is None:
            _fail("stocktake_personal_custody_invalid", "service_unavailable", "个人仓保管责任无效")
        target = ("person", str(custodian))
    else:
        target = ("organization", str(owner.id))
    return (*target, location.id, location.owner_org_id, location.parent_id,
            location.location_type, location.status, location.custodian_person_id, custodian)


def _scan_assignee_snapshots(
    db: Session, *, target: tuple[str, str], after_person_id: uuid.UUID | None,
    limit: int, now: datetime,
) -> tuple[_AssigneeSnapshot, ...]:
    snapshots: list[_AssigneeSnapshot] = []
    raw_after = after_person_id
    scanned = 0
    while len(snapshots) < limit + 1:
        remaining = _MAX_RAW_IDENTITIES_SCANNED - scanned
        batch_limit = min(_RAW_SCAN_BATCH_SIZE, remaining + 1)
        statement = (
            select(User, Person).join(Person, Person.id == User.person_id)
            .where(User.account_status == "active", User.is_active.is_(True),
                   Person.employment_status == "active")
            .order_by(Person.id, User.id).execution_options(populate_existing=True)
        )
        if raw_after is not None:
            statement = statement.where(Person.id > raw_after)
        rows = tuple(db.execute(statement.limit(batch_limit)).all())
        if not rows:
            break
        if len(rows) > remaining:
            _fail(
                "stocktake_assignee_scan_limit_exceeded", "service_unavailable",
                "盘点候选人员目录超过受控扫描上限",
            )
        _unique_assignee_rows(rows)
        scanned += len(rows)
        for user, person in rows:
            snapshot = _assignee_snapshot(db, user=user, person=person, target=target, now=now)
            if snapshot is not None:
                snapshots.append(snapshot)
                if len(snapshots) == limit + 1:
                    break
        raw_after = rows[-1][1].id
        if len(rows) < batch_limit:
            break
    return tuple(snapshots)


def _assignee_snapshot(
    db: Session, *, user: User, person: Person, target: tuple[str, str], now: datetime,
) -> _AssigneeSnapshot | None:
    try:
        candidate = load_formal_principal(db, user.id, now=now)
    except FormalAccessError:
        return None
    role_codes = tuple(sorted(set(candidate.role_codes).intersection(_COUNT_ROLES)))
    if (
        candidate.user_id != user.id or candidate.person_id != person.id
        or user.person_id != person.id or not user.is_active
        or candidate.account_status != "active" or candidate.employment_status != "active"
        or candidate.access_mode != "active" or not role_codes
    ):
        return None
    try:
        allowed = candidate.allows(
            db, "stocktake", "count", target_scope_type=target[0], target_scope_id=target[1],
        )
    except FormalAccessError:
        allowed = False
    if not allowed:
        return None
    return _AssigneeSnapshot(
        option=StocktakeAssigneeOptionOut(
            assignee_user_id=user.id, person_id=person.id, name=person.name,
            employee_no=person.employee_no, role_codes=role_codes,
        ),
        identity_signature=(
            user.id, user.person_id, user.account_status, user.is_active,
            user.authorization_version, person.id, person.organization_id,
            person.name, person.employee_no, person.employment_status,
        ),
        principal_signature=_principal_signature(candidate),
    )


def _revalidate_assignee_snapshots(
    db: Session, *, snapshots: tuple[_AssigneeSnapshot, ...], target: tuple[str, str], now: datetime,
) -> tuple[_AssigneeSnapshot, ...]:
    if not snapshots:
        return ()
    user_ids = tuple(row.option.assignee_user_id for row in snapshots)
    rows = tuple(db.execute(
        select(User, Person).join(Person, Person.id == User.person_id)
        .where(User.id.in_(user_ids)).order_by(Person.id, User.id)
        .execution_options(populate_existing=True)
    ).all())
    _unique_assignee_rows(rows)
    by_user = {user.id: (user, person) for user, person in rows}
    if set(by_user) != set(user_ids):
        _assignee_read_conflict()
    current = []
    for expected in snapshots:
        user, person = by_user[expected.option.assignee_user_id]
        observed = _assignee_snapshot(db, user=user, person=person, target=target, now=now)
        if observed is None or observed != expected:
            _assignee_read_conflict()
        current.append(observed)
    return tuple(current)


def _unique_assignee_rows(rows) -> None:
    if (
        len({user.id for user, _person in rows}) != len(rows)
        or len({person.id for _user, person in rows}) != len(rows)
        or any(user.person_id != person.id for user, person in rows)
    ):
        _assignee_read_conflict()


def _principal_signature(principal: FormalPrincipal) -> tuple[object, ...]:
    return (
        principal.user_id, principal.person_id, principal.account_status,
        principal.employment_status, principal.authorization_version, principal.access_mode,
        tuple(sorted(principal.assignments, key=lambda row: str(row.assignment_id))),
        tuple(sorted(principal.entitlements, key=lambda row: (
            str(row.assignment_id), row.role_code, row.scope_type, row.scope_id,
            row.resource, row.action, row.field_code, row.effect,
        ))),
    )


def _assignee_read_conflict() -> None:
    _fail("stocktake_assignee_read_conflict", "service_unavailable", "盘点候选人员或授权在读取期间发生变化")


def _require_current_actor(
    db: Session,
    supplied: FormalPrincipal,
    *,
    now: datetime,
) -> FormalPrincipal:
    if not isinstance(supplied, FormalPrincipal) or not supplied.user_id:
        _fail("stocktake_options_actor_invalid", "forbidden", "正式操作人上下文无效")
    try:
        current = load_formal_principal(db, supplied.user_id, now=now)
    except FormalAccessError:
        _fail("stocktake_options_actor_not_current", "forbidden", "正式操作人授权已失效")
    if (
        current.person_id != supplied.person_id
        or current.authorization_version != supplied.authorization_version
        or current.account_status != "active"
        or current.employment_status != "active"
        or current.access_mode != "active"
    ):
        _fail("stocktake_options_actor_stale", "forbidden", "正式操作人权限版本已变化")
    if not set(current.role_codes).intersection(_MANAGER_ROLES):
        _fail(
            "stocktake_options_manager_required",
            "forbidden",
            "只有盘点管理员可读取创建选项",
        )
    return current


def _manager_can_manage_region(
    db: Session,
    actor: FormalPrincipal,
    region_org_id: uuid.UUID,
) -> bool:
    grants = tuple(
        row
        for row in actor.assignments
        if (
            row.role_code == "admin"
            and row.scope_type == "national"
            and row.scope_id == "*"
        )
        or (
            row.role_code == "provincial_manager"
            and row.scope_type == "organization"
            and _same_uuid(row.scope_id, region_org_id)
        )
    )
    for grant in grants:
        selected = FormalPrincipal(
            user_id=actor.user_id,
            person_id=actor.person_id,
            account_status=actor.account_status,
            employment_status=actor.employment_status,
            authorization_version=actor.authorization_version,
            access_mode=actor.access_mode,
            assignments=(grant,),
            entitlements=tuple(
                row for row in actor.entitlements if row.assignment_id == grant.assignment_id
            ),
        )
        try:
            # A selected manager grant must allow the region without dropping
            # a matching deny from any other effective grant on this actor.
            if actor.allows(
                db,
                "stocktake",
                "manage",
                target_scope_type="organization",
                target_scope_id=str(region_org_id),
            ) and selected.allows(
                db,
                "stocktake",
                "manage",
                target_scope_type="organization",
                target_scope_id=str(region_org_id),
            ):
                return True
        except FormalAccessError:
            return False
    return False


def _require_authorized_region(
    db: Session,
    actor: FormalPrincipal,
    region_org_id: uuid.UUID,
    *,
    refresh: bool = False,
) -> Organization:
    region = db.get(Organization, region_org_id, populate_existing=refresh)
    if (
        region is None
        or region.status != "active"
        or region.org_type != "region_company"
    ):
        _fail("stocktake_region_not_found", "not_found", "盘点区域不存在")
    if not _manager_can_manage_region(db, actor, region.id):
        _fail("stocktake_region_forbidden", "forbidden", "无权管理该区域盘点")
    return region


def _location_owner_in_region(
    db: Session,
    location: StockLocation,
    region_org_id: uuid.UUID,
    *,
    refresh: bool = False,
) -> Organization | None:
    owner = db.get(Organization, location.owner_org_id, populate_existing=refresh)
    if (
        owner is None
        or owner.status != "active"
        or owner.org_type != "region_company"
    ):
        return None
    return owner if _organization_descends_from(db, owner.id, region_org_id, refresh=refresh) else None


def _validate_location_chain(
    db: Session,
    location: StockLocation,
    region_org_id: uuid.UUID,
    *,
    refresh: bool = False,
) -> None:
    current: StockLocation | None = location
    seen: set[uuid.UUID] = set()
    while current is not None:
        if current.id in seen:
            _fail(
                "stocktake_location_tree_cycle",
                "service_unavailable",
                "库存位置树存在循环",
            )
        seen.add(current.id)
        if current.status != "active" or not _organization_descends_from(
            db,
            current.owner_org_id,
            region_org_id,
            refresh=refresh,
        ):
            _fail(
                "stocktake_location_tree_invalid",
                "service_unavailable",
                "盘点库位层级或区域归属无效",
            )
        if current.parent_id is None:
            return
        current = db.get(StockLocation, current.parent_id, populate_existing=refresh)
        if current is None:
            _fail(
                "stocktake_location_parent_missing",
                "service_unavailable",
                "盘点库位父级不存在",
            )


def _current_custodian(
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
            .execution_options(populate_existing=True)
        ).all()
    )
    if len(rows) > 1:
        _fail(
            "stocktake_location_custody_ambiguous",
            "service_unavailable",
            "盘点库位当前保管责任不唯一",
        )
    custody_person_id = rows[0].custodian_person_id if rows else None
    if location.location_type == "personal":
        if (
            custody_person_id is None
            or location.custodian_person_id is None
            or custody_person_id != location.custodian_person_id
        ):
            _fail(
                "stocktake_personal_custody_invalid",
                "service_unavailable",
                "个人仓当前保管责任无效",
            )
    elif (
        location.custodian_person_id is not None
        and custody_person_id != location.custodian_person_id
    ):
        _fail(
            "stocktake_region_custody_invalid",
            "service_unavailable",
            "区域仓保管责任数据不一致",
        )
    return custody_person_id


def _organization_descends_from(
    db: Session,
    organization_id: uuid.UUID,
    ancestor_id: uuid.UUID,
    *,
    refresh: bool = False,
) -> bool:
    current_id: uuid.UUID | None = organization_id
    seen: set[uuid.UUID] = set()
    while current_id is not None:
        if current_id in seen:
            _fail(
                "stocktake_organization_tree_cycle",
                "service_unavailable",
                "组织树存在循环",
            )
        seen.add(current_id)
        row = db.get(Organization, current_id, populate_existing=refresh)
        if row is None or row.status != "active":
            return False
        if row.id == ancestor_id:
            return True
        current_id = row.parent_id
    return False


def _limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        _fail(
            "stocktake_options_limit_invalid",
            "invalid_request",
            "盘点选项分页大小无效",
        )
    return value


def _cursor(value: object, code: str) -> uuid.UUID | None:
    if value is None:
        return None
    return _required_uuid(value, code)


def _required_uuid(value: object, code: str) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail(code, "invalid_request", "盘点选项标识无效")
    return value


def _same_uuid(value: str, expected: uuid.UUID) -> bool:
    try:
        return uuid.UUID(value) == expected
    except (TypeError, ValueError):
        return False


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        _fail(
            "stocktake_options_time_invalid",
            "service_unavailable",
            "盘点选项时间上下文无效",
        )
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _fail(code: str, category: str, message: str):
    raise StocktakeOptionError(code, category, message)


__all__ = [
    "StocktakeOptionError",
    "list_assignee_options",
    "list_location_options",
    "list_region_options",
]
