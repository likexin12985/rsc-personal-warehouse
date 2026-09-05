"""Bounded, read-only opening-stocktake eligibility directories.

Every response remains explicitly not ready to start: no control inventory is
read, no SyncRun is selected, and no task, command coordinate, scope UUID, hash,
or lock is created.  The write service revalidates all eligibility under its
existing lock protocol.  Primitive snapshots prevent SQLAlchemy identity-map
refreshes from hiding authorization or projection drift during these reads.
"""

from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal
import uuid

from sqlalchemy import or_, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from ..formal_access import FormalAccessError, FormalPrincipal, load_formal_principal
from ..foundation_models import AuthIdentity, Organization, Person, Role, RoleAssignment
from ..inventory_models import CustodyAssignment, StockLocation
from ..models import User
from ..opening_start_option_schemas import (
    OpeningStartAssetOwnerOptionOut,
    OpeningStartAssetOwnerOptionPageOut,
    OpeningStartAssigneeOptionOut,
    OpeningStartAssigneeOptionPageOut,
    OpeningStartLocationOptionOut,
    OpeningStartLocationOptionPageOut,
    OpeningStartRegionOptionOut,
    OpeningStartRegionOptionPageOut,
)
from . import opening_stocktake as opening


_MAX_RAW_OPTIONS_SCANNED = 1000
_RAW_SCAN_BATCH_SIZE = 100
_MAX_GRAPH_DEPTH = 1000
_Kind = Literal["regions", "asset_owners", "locations", "assignees"]
_Option = (
    OpeningStartRegionOptionOut | OpeningStartAssetOwnerOptionOut
    | OpeningStartLocationOptionOut | OpeningStartAssigneeOptionOut
)
_HTTP_STATUS = {"invalid_request": 422, "forbidden": 403, "service_unavailable": 503}
_INELIGIBLE_SCOPE_CODES = frozenset({
    "asset_owner_outside_region", "stock_location_outside_region",
    "opening_scope_dimension_forbidden", "stock_location_invalid",
    "asset_owner_invalid", "personal_location_owner_invalid",
})
_INELIGIBLE_ASSIGNEE_CODES = frozenset({
    "personal_assignee_not_custodian", "personal_assignee_count_forbidden",
    "regional_assignee_count_forbidden", "assignee_inactive",
})


class OpeningStartOptionError(RuntimeError):
    def __init__(self, code: str, category: str, message: str) -> None:
        if category not in _HTTP_STATUS:
            raise ValueError("unsupported opening option error category")
        super().__init__(message)
        self.code, self.category, self.message = code, category, message

    @property
    def http_status_code(self) -> int:
        return _HTTP_STATUS[self.category]

    def as_detail(self) -> dict[str, str]:
        return {"code": self.code, "category": self.category, "message": self.message}


@dataclass(frozen=True)
class _IdentitySnapshot:
    principal: FormalPrincipal
    name: str
    signature: tuple[object, ...]


@dataclass(frozen=True)
class _OptionSnapshot:
    cursor_id: uuid.UUID
    user_id: str | None
    option: _Option
    signature: tuple[object, ...]


@dataclass(frozen=True)
class _Selection:
    region_org_id: uuid.UUID | None = None
    owner_org_id: uuid.UUID | None = None
    location_id: uuid.UUID | None = None


@dataclass(frozen=True)
class _EligibilityHorizon:
    """The earliest known time at which a saved eligibility graph can change."""

    valid_before: datetime | None


def _page(model, **values):
    # Page validation runs after the read context exits, so it needs the same
    # sanitized boundary as item validation (including corrupt actor fields).
    try:
        return model(**values)
    except (TypeError, ValueError):
        _projection_invalid()


def list_region_options(
    db: Session, *, actor: FormalPrincipal, limit: int,
    after_id: uuid.UUID | None = None, now: datetime | None = None,
) -> OpeningStartRegionOptionPageOut:
    rows, current, next_id = _read_options(
        db, actor=actor, kind="regions", selection=_Selection(),
        limit=limit, after_id=after_id, now=now,
    )
    return _page(OpeningStartRegionOptionPageOut,
        actor_person_id=current.person_id, authorization_version=current.authorization_version,
        items=tuple(row.option for row in rows), next_after_id=next_id,
    )


def list_asset_owner_options(
    db: Session, *, actor: FormalPrincipal, region_org_id: uuid.UUID, limit: int,
    after_id: uuid.UUID | None = None, now: datetime | None = None,
) -> OpeningStartAssetOwnerOptionPageOut:
    region = _required_uuid(region_org_id)
    rows, current, next_id = _read_options(
        db, actor=actor, kind="asset_owners", selection=_Selection(region),
        limit=limit, after_id=after_id, now=now,
    )
    return _page(OpeningStartAssetOwnerOptionPageOut,
        actor_person_id=current.person_id, authorization_version=current.authorization_version,
        region_org_id=region, items=tuple(row.option for row in rows), next_after_id=next_id,
    )


def list_location_options(
    db: Session, *, actor: FormalPrincipal, region_org_id: uuid.UUID,
    owner_org_id: uuid.UUID, limit: int, after_id: uuid.UUID | None = None,
    now: datetime | None = None,
) -> OpeningStartLocationOptionPageOut:
    region, owner = _required_uuid(region_org_id), _required_uuid(owner_org_id)
    rows, current, next_id = _read_options(
        db, actor=actor, kind="locations", selection=_Selection(region, owner),
        limit=limit, after_id=after_id, now=now,
    )
    return _page(OpeningStartLocationOptionPageOut,
        actor_person_id=current.person_id, authorization_version=current.authorization_version,
        region_org_id=region, owner_org_id=owner,
        items=tuple(row.option for row in rows), next_after_id=next_id,
    )


def list_assignee_options(
    db: Session, *, actor: FormalPrincipal, region_org_id: uuid.UUID,
    owner_org_id: uuid.UUID, location_id: uuid.UUID, limit: int,
    after_person_id: uuid.UUID | None = None, now: datetime | None = None,
) -> OpeningStartAssigneeOptionPageOut:
    region, owner, location = (
        _required_uuid(region_org_id), _required_uuid(owner_org_id), _required_uuid(location_id)
    )
    rows, current, next_id = _read_options(
        db, actor=actor, kind="assignees", selection=_Selection(region, owner, location),
        limit=limit, after_id=after_person_id, now=now,
    )
    return _page(OpeningStartAssigneeOptionPageOut,
        actor_person_id=current.person_id, authorization_version=current.authorization_version,
        region_org_id=region, owner_org_id=owner, location_id=location,
        items=tuple(row.option for row in rows), next_after_person_id=next_id,
    )


def _read_options(
    db: Session, *, actor: FormalPrincipal, kind: _Kind, selection: _Selection,
    limit: int, after_id: uuid.UUID | None, now: datetime | None,
) -> tuple[tuple[_OptionSnapshot, ...], FormalPrincipal, uuid.UUID | None]:
    checked_limit = _limit(limit)
    checked_after = None if after_id is None else _required_uuid(after_id)
    effective_now = _aware(datetime.now(timezone.utc) if now is None else now)
    with _read_boundary(db):
        original_actor = _require_current_actor(db, actor, now=effective_now)
        target = _selection_signature(db, original_actor.principal, selection, effective_now)
        snapshots = _scan_options(
            db, actor=original_actor.principal, kind=kind, selection=selection,
            after_id=checked_after, limit=checked_limit, now=effective_now,
        )
        final_now = effective_now if now is not None else _aware(datetime.now(timezone.utc))
        # Recheck the returned candidates AND the private eligible lookahead.
        _revalidate_options(
            db, actor=original_actor.principal, kind=kind, selection=selection,
            snapshots=snapshots, now=final_now,
        )
        # Candidate checks may be slow.  The caller and all selected anchors
        # are checked last, using the real wall clock unless a test supplied it.
        final_now = effective_now if now is not None else _aware(datetime.now(timezone.utc))
        current = _require_current_actor(db, original_actor.principal, now=final_now)
        current_target = _selection_signature(db, current.principal, selection, final_now)
        if current.signature != original_actor.signature or current_target != target:
            _read_conflict()
        # There must be no DB reads after this last clock sample.  An assignee
        # or private lookahead can expire while later candidates/actor/target
        # are being reread.  Future grants (including denies) and future custody
        # starts are transitions too, even when no custody currently exists.
        # Immutable horizons close that known time window without an unbounded
        # cycle of "one more" revalidation queries.
        returned_at = effective_now if now is not None else _aware(datetime.now(timezone.utc))
        _validate_horizons(
            (original_actor.signature, current.signature, target, current_target,
             tuple(row.signature for row in snapshots)), now=returned_at,
        )
        page = snapshots[:checked_limit]
        next_id = page[-1].cursor_id if len(snapshots) > checked_limit and page else None
        return page, current.principal, next_id


@contextmanager
def _read_boundary(db: Session):
    try:
        with db.no_autoflush:
            yield
    except OpeningStartOptionError:
        raise
    except DBAPIError:
        _fail("opening_start_options_database_unavailable", "service_unavailable",
              "期初盘点准备选项暂时不可用")
    except (FormalAccessError, opening.OpeningStocktakeError, TypeError, ValueError):
        _projection_invalid()


def _scan_options(
    db: Session, *, actor: FormalPrincipal, kind: _Kind, selection: _Selection,
    after_id: uuid.UUID | None, limit: int, now: datetime,
) -> tuple[_OptionSnapshot, ...]:
    snapshots: list[_OptionSnapshot] = []
    scanned, raw_after = 0, after_id
    while len(snapshots) < limit + 1:
        remaining = _MAX_RAW_OPTIONS_SCANNED - scanned
        batch_size = min(_RAW_SCAN_BATCH_SIZE, remaining + 1)
        rows = _raw_batch(db, kind=kind, after_id=raw_after, limit=batch_size)
        if not rows:
            break
        if len(rows) > remaining:
            _fail("opening_start_options_scan_limit_exceeded", "service_unavailable",
                  "期初盘点选项目录超过受控扫描上限")
        if len({row[0] for row in rows}) != len(rows):
            _projection_invalid()
        scanned += len(rows)
        for cursor_id, user_id in rows:
            snapshot = _candidate_snapshot(
                db, actor=actor, kind=kind, selection=selection,
                cursor_id=cursor_id, user_id=user_id, now=now,
            )
            if snapshot is not None:
                snapshots.append(snapshot)
                if len(snapshots) == limit + 1:
                    break
        raw_after = rows[-1][0]
        if len(rows) < batch_size:
            break
    return tuple(snapshots)


def _raw_batch(
    db: Session, *, kind: _Kind, after_id: uuid.UUID | None, limit: int,
) -> tuple[tuple[uuid.UUID, str | None], ...]:
    if kind == "assignees":
        statement = (
            select(Person.id, User.id).join(User, User.person_id == Person.id)
            .where(Person.employment_status == "active", User.account_status == "active",
                   User.is_active.is_(True)).order_by(Person.id, User.id)
        )
        if after_id is not None:
            statement = statement.where(Person.id > after_id)
        return tuple(tuple(row) for row in db.execute(
            statement.limit(limit).execution_options(populate_existing=True)
        ).all())
    model = StockLocation if kind == "locations" else Organization
    statement = select(model.id).where(model.status == "active").order_by(model.id)
    if kind == "locations":
        statement = statement.where(StockLocation.location_type.in_(("region", "personal")))
    else:
        statement = statement.where(Organization.org_type == "region_company")
    if after_id is not None:
        statement = statement.where(model.id > after_id)
    return tuple((row_id, None) for row_id in db.scalars(
        statement.limit(limit).execution_options(populate_existing=True)
    ).all())


def _candidate_snapshot(
    db: Session, *, actor: FormalPrincipal, kind: _Kind, selection: _Selection,
    cursor_id: uuid.UUID, user_id: str | None, now: datetime,
) -> _OptionSnapshot | None:
    if kind == "regions":
        value = _region_option(db, actor, cursor_id, now)
    elif kind == "asset_owners":
        value = _owner_option(db, actor, selection.region_org_id, cursor_id, now)
    elif kind == "locations":
        value = _location_option(db, actor, selection, cursor_id, now)
    else:
        value = _assignee_option(db, actor, selection, cursor_id, user_id, now)
    if value is None:
        return None
    option, signature = value
    return _OptionSnapshot(cursor_id, user_id, option, signature)


def _revalidate_options(
    db: Session, *, actor: FormalPrincipal, kind: _Kind, selection: _Selection,
    snapshots: tuple[_OptionSnapshot, ...], now: datetime,
) -> None:
    for expected in snapshots:
        observed = _candidate_snapshot(
            db, actor=actor, kind=kind, selection=selection,
            cursor_id=expected.cursor_id, user_id=expected.user_id, now=now,
        )
        if observed != expected:
            _read_conflict()


def _region_option(db: Session, actor: FormalPrincipal, region_id: uuid.UUID, now: datetime):
    region = db.get(Organization, region_id, populate_existing=True)
    if region is None or region.status != "active" or region.org_type != "region_company":
        return None
    signature = _organization_signature(region)
    path = _organization_path(db, region.id)
    try:
        grant = opening._authorize_batch_manager(db, actor, region.id)
        opening._lock_selected_grant(db, actor, grant, now, lock_rows=False)
    except opening.OpeningStocktakeError as exc:
        if exc.code == "opening_manager_forbidden":
            return None
        raise
    return (
        OpeningStartRegionOptionOut(
            region_org_id=region.id, code=region.code, name=region.name,
            province_code=region.province_code,
        ),
        (signature, path, grant),
    )


def _owner_option(
    db: Session, actor: FormalPrincipal, region_id: uuid.UUID, owner_id: uuid.UUID, now: datetime,
):
    owner = db.get(Organization, owner_id, populate_existing=True)
    if owner is None or owner.status != "active" or owner.org_type != "region_company":
        return None
    signature = _organization_signature(owner)
    path = _organization_path(db, owner.id)
    if not _path_in_region(path, region_id):
        return None
    try:
        grant = opening._authorize_scope_dimensions(
            db, actor=actor, task_region_org_id=region_id,
            owner_org_id=owner.id, location_owner_org_id=owner.id,
        )
        opening._lock_selected_grant(db, actor, grant, now, lock_rows=False)
    except opening.OpeningStocktakeError as exc:
        if exc.code == "opening_scope_dimension_forbidden":
            return None
        raise
    return (
        OpeningStartAssetOwnerOptionOut(owner_org_id=owner.id, code=owner.code, name=owner.name),
        (signature, path, grant),
    )


def _location_option(
    db: Session, actor: FormalPrincipal, selection: _Selection,
    location_id: uuid.UUID, now: datetime,
):
    value = _scope_snapshot(db, actor, selection, location_id, now)
    if value is None:
        return None
    qualified, signature, physical_owner, custodian = value
    location = qualified.location
    return (
        OpeningStartLocationOptionOut(
            location_id=location.id, code=location.code, name=location.name,
            location_type=location.location_type,
            physical_owner_org_id=physical_owner.id, physical_owner_name=physical_owner.name,
            custodian_person_id=qualified.custodian_person_id,
            custodian_name=custodian.name if custodian is not None else None,
        ),
        signature,
    )


def _scope_snapshot(
    db: Session, actor: FormalPrincipal, selection: _Selection,
    location_id: uuid.UUID, now: datetime,
):
    owner = db.get(Organization, selection.owner_org_id, populate_existing=True)
    location = db.get(StockLocation, location_id, populate_existing=True)
    if (owner is None or location is None or owner.status != "active"
            or owner.org_type != "region_company" or location.status != "active"
            or location.location_type not in {"region", "personal"}):
        return None
    owner_signature, location_signature = _organization_signature(owner), _location_signature(location)
    owner_path = _organization_path(db, owner.id)
    physical_path = _organization_path(db, location.owner_org_id)
    if (not _path_in_region(owner_path, selection.region_org_id)
            or not _path_in_region(physical_path, selection.region_org_id)):
        return None
    location_path = _location_path(db, location.id)
    custody_timeline = tuple(db.scalars(
        select(CustodyAssignment).where(
            CustodyAssignment.location_id == location.id,
            or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > now),
        ).order_by(CustodyAssignment.id).limit(_MAX_GRAPH_DEPTH + 1)
        .execution_options(populate_existing=True)
    ).all())
    if len(custody_timeline) > _MAX_GRAPH_DEPTH:
        _projection_invalid()
    custody_signature = tuple(_custody_signature(row) for row in custody_timeline)
    custody_horizon = _temporal_horizon(
        tuple((row[3], row[4]) for row in custody_signature), now=now,
    )
    custody_rows = tuple(row for row in custody_timeline if _aware(row.valid_from) <= now)
    try:
        qualified = opening._qualify_opening_scope(
            db, actor=actor, region_org_id=selection.region_org_id, owner=owner,
            location=location, effective_custodies=custody_rows, now=now, lock_rows=False,
        )
    except opening.OpeningStocktakeError as exc:
        if exc.code in _INELIGIBLE_SCOPE_CODES:
            return None
        raise
    physical_owner = db.get(Organization, location.owner_org_id, populate_existing=True)
    if physical_owner is None:
        _projection_invalid()
    custodian = None
    if qualified.custodian_person_id is not None:
        custodian = db.get(Person, qualified.custodian_person_id, populate_existing=True)
        if custodian is None:
            _projection_invalid()
    return (
        qualified,
        (owner_signature, location_signature, owner_path, physical_path, location_path,
         custody_signature, custody_horizon, qualified.manager_grant,
         _person_signature(custodian) if custodian is not None else None),
        physical_owner, custodian,
    )


def _assignee_option(
    db: Session, actor: FormalPrincipal, selection: _Selection,
    person_id: uuid.UUID, user_id: str | None, now: datetime,
):
    identity = _identity_snapshot(db, user_id, now=now)
    if identity is None:
        return None
    if identity.principal.person_id != person_id:
        _read_conflict()
    scope = _scope_snapshot(db, actor, selection, selection.location_id, now)
    if scope is None:
        _target_forbidden()
    qualified, scope_signature, _owner, _custodian = scope
    try:
        if qualified.location.location_type == "personal":
            opening._authorize_personal_assignee(
                db, assignee_user_id=identity.principal.user_id,
                custodian_person_id=qualified.custodian_person_id, now=now, lock_rows=False,
            )
        else:
            opening._authorize_regional_assignee(
                db, assignee_user_id=identity.principal.user_id,
                region_org_id=selection.region_org_id, owner_org_id=selection.owner_org_id,
                now=now, lock_rows=False,
            )
    except opening.OpeningStocktakeError as exc:
        if exc.code in _INELIGIBLE_ASSIGNEE_CODES:
            return None
        raise
    return (
        OpeningStartAssigneeOptionOut(
            assignee_user_id=identity.principal.user_id, person_id=person_id, name=identity.name,
        ),
        (identity.signature, scope_signature),
    )


def _selection_signature(
    db: Session, actor: FormalPrincipal, selection: _Selection, now: datetime,
) -> tuple[object, ...]:
    signature = []
    if selection.region_org_id is not None:
        region = _region_option(db, actor, selection.region_org_id, now)
        if region is None:
            _target_forbidden()
        signature.append(region)
    if selection.owner_org_id is not None:
        owner = _owner_option(db, actor, selection.region_org_id, selection.owner_org_id, now)
        if owner is None:
            _target_forbidden()
        signature.append(owner)
    if selection.location_id is not None:
        location = _location_option(db, actor, selection, selection.location_id, now)
        if location is None:
            _target_forbidden()
        signature.append(location)
    return tuple(signature)


def _identity_snapshot(db: Session, user_id: str | None, *, now: datetime) -> _IdentitySnapshot | None:
    user = db.get(User, user_id, populate_existing=True) if user_id else None
    if (user is None or user.person_id is None or user.account_status != "active"
            or not user.is_active):
        return None
    user_signature = (user.id, user.person_id, user.account_status,
                      user.is_active, user.authorization_version)
    person = db.get(Person, user.person_id, populate_existing=True)
    if person is None:
        _projection_invalid()
    if person.employment_status != "active":
        return None
    person_signature = _person_signature(person)
    # Do not let a corrupt one-person/multiple-user projection silently turn
    # a person cursor into an ambiguous login principal.
    linked_users = tuple(db.scalars(select(User.id).where(
        User.person_id == person.id
    ).order_by(User.id).limit(2).execution_options(populate_existing=True)).all())
    if linked_users != (user.id,):
        _projection_invalid()
    identities = tuple(tuple(row) for row in db.execute(select(
        AuthIdentity.id, AuthIdentity.user_id, AuthIdentity.identity_type,
        AuthIdentity.provider_key, AuthIdentity.status,
        AuthIdentity.verified_at, AuthIdentity.revoked_at,
    ).where(
        AuthIdentity.user_id == user.id, AuthIdentity.status == "active",
        AuthIdentity.verified_at.is_not(None), AuthIdentity.revoked_at.is_(None),
    ).order_by(AuthIdentity.id).execution_options(populate_existing=True)).all())
    if not identities:
        return None
    assignment_timeline = tuple(tuple(row) for row in db.execute(select(
        RoleAssignment.id, RoleAssignment.user_id, RoleAssignment.role_id,
        RoleAssignment.scope_type, RoleAssignment.scope_id, RoleAssignment.status,
        RoleAssignment.valid_from, RoleAssignment.valid_to,
        Role.code, Role.status, Role.is_external,
    ).join(
        Role, Role.id == RoleAssignment.role_id,
    ).where(
        RoleAssignment.user_id == user.id,
        RoleAssignment.status.in_(("active", "scheduled")),
        RoleAssignment.revoked_at.is_(None),
        or_(RoleAssignment.valid_to.is_(None), RoleAssignment.valid_to > now),
        Role.status == "active",
    ).order_by(RoleAssignment.id).limit(_MAX_GRAPH_DEPTH + 1)
      .execution_options(populate_existing=True)).all())
    if len(assignment_timeline) > _MAX_GRAPH_DEPTH:
        _projection_invalid()
    assignment_horizon = _temporal_horizon(
        tuple((row[6], row[7]) for row in assignment_timeline), now=now,
    )
    if not any(_aware(row[6]) <= now for row in assignment_timeline):
        return None
    # Ordinary ineligible identities were filtered above.  A malformed formal
    # graph here is a service error, not an empty/partially correct directory.
    principal = load_formal_principal(db, user.id, now=now)
    if (principal.user_id != user_signature[0] or principal.person_id != user_signature[1]
            or principal.authorization_version != user_signature[4]
            or principal.account_status != "active" or principal.employment_status != "active"
            or principal.access_mode != "active"):
        _read_conflict()
    organization_ids = {person.organization_id}
    for grant in principal.assignments:
        if grant.scope_type == "organization":
            organization_ids.add(uuid.UUID(grant.scope_id))
    organization_paths = tuple(
        _organization_path(db, value) for value in sorted(organization_ids, key=str)
    )
    signature = (user_signature, person_signature, identities,
                 assignment_timeline, assignment_horizon,
                 _principal_signature(principal), organization_paths)
    return _IdentitySnapshot(principal, person_signature[3], signature)


def _require_current_actor(
    db: Session, supplied: FormalPrincipal, *, now: datetime,
) -> _IdentitySnapshot:
    if not isinstance(supplied, FormalPrincipal) or not supplied.user_id:
        _actor_forbidden()
    current = _identity_snapshot(db, supplied.user_id, now=now)
    if (current is None or current.principal.person_id != supplied.person_id
            or current.principal.authorization_version != supplied.authorization_version
            or not set(current.principal.role_codes).intersection({"admin", "provincial_manager"})):
        _actor_forbidden()
    return current


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


def _temporal_horizon(
    intervals: tuple[tuple[datetime, datetime | None], ...], *, now: datetime,
) -> _EligibilityHorizon:
    boundaries = []
    for starts_at, ends_at in intervals:
        start = _aware(starts_at)
        end = _aware(ends_at) if ends_at is not None else None
        if end is not None and end <= start:
            _projection_invalid()
        if start > now:
            boundaries.append(start)
        if end is not None and end > now:
            boundaries.append(end)
    return _EligibilityHorizon(min(boundaries) if boundaries else None)


def _validate_horizons(value: object, *, now: datetime) -> None:
    """Pure in-memory final check; equality with a deadline is already stale."""
    if isinstance(value, _EligibilityHorizon):
        if value.valid_before is not None and now >= value.valid_before:
            _read_conflict()
    elif isinstance(value, tuple):
        for child in value:
            _validate_horizons(child, now=now)


def _organization_signature(row: Organization) -> tuple[object, ...]:
    return row.id, row.parent_id, row.status, row.org_type, row.code, row.name, row.province_code


def _person_signature(row: Person) -> tuple[object, ...]:
    return row.id, row.organization_id, row.employment_status, row.name, row.employee_no


def _location_signature(row: StockLocation) -> tuple[object, ...]:
    return (row.id, row.parent_id, row.owner_org_id, row.location_type,
            row.status, row.custodian_person_id, row.code, row.name)


def _custody_signature(row: CustodyAssignment) -> tuple[object, ...]:
    return (row.id, row.location_id, row.custodian_person_id, _aware(row.valid_from),
            _aware(row.valid_to) if row.valid_to is not None else None, row.handover_case_id)


def _organization_path(db: Session, organization_id: uuid.UUID) -> tuple[tuple[object, ...], ...]:
    """Snapshot ancestry without inventing a new active-root requirement.

    Permission coverage may depend on an ancestor deny beyond the task region,
    so we snapshot it too.  A missing ancestor is recorded (the formal evaluator
    treats it as not covering), rather than changing valid region-local rules.
    Cycles and oversized graphs are always unsafe.
    """
    result, seen = [], set()
    current_id = organization_id
    while current_id is not None:
        if current_id in seen or len(seen) >= _MAX_GRAPH_DEPTH:
            _projection_invalid()
        seen.add(current_id)
        row = db.get(Organization, current_id, populate_existing=True)
        if row is None:
            result.append((current_id, None, "missing", None, None, None, None))
            break
        result.append(_organization_signature(row))
        current_id = row.parent_id
    return tuple(result)


def _path_in_region(path: tuple[tuple[object, ...], ...], region_id: uuid.UUID) -> bool:
    for row in path:
        if row[2] == "missing":
            _projection_invalid()
        if row[2] != "active":
            return False
        if row[0] == region_id:
            return True
    return False


def _location_path(db: Session, location_id: uuid.UUID) -> tuple[object, ...]:
    result, seen = [], set()
    current_id = location_id
    while current_id is not None:
        if current_id in seen or len(seen) >= _MAX_GRAPH_DEPTH:
            _projection_invalid()
        seen.add(current_id)
        row = db.get(StockLocation, current_id, populate_existing=True)
        if row is None:
            _projection_invalid()
        result.append((_location_signature(row), _organization_path(db, row.owner_org_id)))
        current_id = row.parent_id
    return tuple(result)


def _required_uuid(value: object) -> uuid.UUID:
    if not isinstance(value, uuid.UUID) or value.int == 0:
        _fail("opening_start_options_identifier_invalid", "invalid_request", "期初盘点选项标识无效")
    return value


def _limit(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 100:
        _fail("opening_start_options_limit_invalid", "invalid_request", "期初盘点选项分页大小无效")
    return value


def _aware(value: datetime) -> datetime:
    if not isinstance(value, datetime):
        _projection_invalid()
    if value.tzinfo is None or value.utcoffset() is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _actor_forbidden() -> None:
    _fail("opening_start_options_actor_forbidden", "forbidden", "当前人员无权读取期初盘点准备选项")


def _target_forbidden() -> None:
    # Missing, inactive and outside-scope selected coordinates have the same
    # public response, so identifiers cannot be probed as a foreign directory.
    _fail("opening_start_options_target_forbidden", "forbidden", "所选范围不可用于当前期初盘点准备")


def _projection_invalid() -> None:
    _fail("opening_start_options_projection_invalid", "service_unavailable", "期初盘点选项身份或范围数据无效")


def _read_conflict() -> None:
    _fail("opening_start_options_read_conflict", "service_unavailable", "期初盘点选项在读取期间发生变化，请重新读取")


def _fail(code: str, category: str, message: str):
    raise OpeningStartOptionError(code, category, message)


__all__ = [
    "OpeningStartOptionError", "list_region_options", "list_asset_owner_options",
    "list_location_options", "list_assignee_options",
]
