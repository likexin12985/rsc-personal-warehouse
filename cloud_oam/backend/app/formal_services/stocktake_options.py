"""Read-only, scope-safe option lists for managed stocktake creation.

These queries expose labels for identifiers that the command service already
revalidates under lock.  They never create a task, freeze stock, read an
external system, or infer a provincial manager when no such assignment exists.
"""

from __future__ import annotations

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
    effective_now = _aware(now or datetime.now(timezone.utc))
    try:
        with db.no_autoflush:
            principal = _require_current_actor(db, actor, now=effective_now)
            _require_authorized_region(db, principal, checked_region_id)
            location = db.get(StockLocation, checked_location_id)
            if location is None:
                _fail(
                    "stocktake_location_not_found",
                    "not_found",
                    "盘点库位不存在",
                )
            owner = _location_owner_in_region(db, location, checked_region_id)
            if owner is None or location.status != "active" or location.location_type not in {
                "region",
                "personal",
            }:
                _fail(
                    "stocktake_location_forbidden",
                    "forbidden",
                    "盘点库位不在当前授权区域",
                )
            _validate_location_chain(db, location, checked_region_id)
            custodian_person_id = _current_custodian(
                db,
                location,
                now=effective_now,
            )
            if location.location_type == "personal":
                if custodian_person_id is None:
                    _fail(
                        "stocktake_personal_custody_invalid",
                        "service_unavailable",
                        "个人仓保管责任无效",
                    )
                target_scope_type = "person"
                target_scope_id = str(custodian_person_id)
            else:
                target_scope_type = "organization"
                target_scope_id = str(owner.id)

            statement = (
                select(User, Person)
                .join(Person, Person.id == User.person_id)
                .where(
                    User.account_status == "active",
                    User.is_active.is_(True),
                    Person.employment_status == "active",
                )
                .order_by(Person.id)
                .execution_options(populate_existing=True)
            )
            if checked_after is not None:
                statement = statement.where(Person.id >= checked_after)
            # The cursor advances across raw active identities. Invalid or
            # unentitled identities are omitted rather than made selectable.
            raw_rows = tuple(db.execute(statement.limit(checked_limit + 1)).all())
            page_rows = raw_rows[:checked_limit]
            items: list[StocktakeAssigneeOptionOut] = []
            for user, person in page_rows:
                try:
                    candidate = load_formal_principal(db, user.id, now=effective_now)
                except FormalAccessError:
                    continue
                role_codes = tuple(
                    sorted(set(candidate.role_codes).intersection(_COUNT_ROLES))
                )
                if (
                    candidate.person_id != person.id
                    or candidate.account_status != "active"
                    or candidate.employment_status != "active"
                    or candidate.access_mode != "active"
                    or not role_codes
                ):
                    continue
                try:
                    allowed = candidate.allows(
                        db,
                        "stocktake",
                        "count",
                        target_scope_type=target_scope_type,
                        target_scope_id=target_scope_id,
                    )
                except FormalAccessError:
                    allowed = False
                if allowed:
                    items.append(
                        StocktakeAssigneeOptionOut(
                            assignee_user_id=user.id,
                            person_id=person.id,
                            name=person.name,
                            employee_no=person.employee_no,
                            role_codes=role_codes,
                        )
                    )
            next_after_person_id = (
                raw_rows[checked_limit][1].id
                if len(raw_rows) > checked_limit
                else None
            )
            _require_current_actor(db, principal, now=effective_now)
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
            if selected.allows(
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
) -> Organization:
    region = db.get(Organization, region_org_id)
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
) -> Organization | None:
    owner = db.get(Organization, location.owner_org_id)
    if (
        owner is None
        or owner.status != "active"
        or owner.org_type != "region_company"
    ):
        return None
    return owner if _organization_descends_from(db, owner.id, region_org_id) else None


def _validate_location_chain(
    db: Session,
    location: StockLocation,
    region_org_id: uuid.UUID,
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
        ):
            _fail(
                "stocktake_location_tree_invalid",
                "service_unavailable",
                "盘点库位层级或区域归属无效",
            )
        if current.parent_id is None:
            return
        current = db.get(StockLocation, current.parent_id)
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
        row = db.get(Organization, current_id)
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
