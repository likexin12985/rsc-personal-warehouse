"""Deterministic read-window drift simulations against isolated SQLite rows.

These hooks change real identity/reference rows between successive service
reads. They are not two-session PostgreSQL concurrency evidence. Every test
uses the in-memory fixture, preserves business facts, and restores its hooks.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import delete, event, insert, select, update

from app.database import Base
from app.foundation_models import AuthIdentity, Organization, Permission, Person, RoleAssignment, RolePermission
from app.inventory_models import CustodyAssignment, StockLocation
from app.models import User
from test_opening_start_options import NOW, STAGES, _call, _organization, db, service, world  # noqa: F401


def _business_snapshot(world):
    """Check stored facts, balances and control evidence, not just row counts."""
    prefixes = ("stocktake_", "inventory_", "sync_", "external_object")
    exact = {"stock_accounts", "stock_balances", "serial_current_positions",
             "audit_chain_heads", "audit_events", "state_transition_events", "outbox_events"}
    result = []
    with world.db.no_autoflush:
        for name, table in sorted(Base.metadata.tables.items()):
            if name.startswith(prefixes) or name in exact:
                rows = world.db.execute(select(table)).all()
                result.append((name, tuple(sorted(repr(tuple(row)) for row in rows))))
    return tuple(result)


def _assert_blocked(world, call):
    before = _business_snapshot(world)
    with pytest.raises(service.OpeningStartOptionError) as error:
        call()
    assert error.value.http_status_code in {403, 503}
    # A broken fixture UPDATE must not masquerade as a successful drift guard.
    assert error.value.code != "opening_start_options_database_unavailable"
    assert _business_snapshot(world) == before
    assert not world.db.new and not world.db.dirty and not world.db.deleted
    return error.value


def _after_scan(monkeypatch, mutation):
    original = service._scan_options
    observed = []

    def read_then_mutate(*args, **kwargs):
        snapshots = original(*args, **kwargs)
        assert snapshots, "fixture must reach a non-empty authorized scan"
        observed.append(snapshots)
        mutation(snapshots)
        return snapshots

    monkeypatch.setattr(service, "_scan_options", read_then_mutate)
    return observed


def _new_counter(world, number, *, national_admin=False):
    """Low, distinct IDs put two non-actor candidates ahead of fixture users."""
    person = Person(
        id=uuid.UUID(int=number),
        organization_id=world.hq.id if national_admin else world.region_x.id,
        employee_no=f"READ-WINDOW-{number}", name=f"只读窗口执行人{number}",
        employment_status="active", source_updated_at=NOW,
    )
    user = User(
        id=f"read-window-counter-{number}", person_id=person.id,
        account_status="active", authorization_version=1,
        mobile=f"13900000{number:03d}", name=person.name,
        password_hash="formal-password-disabled",
        role="admin" if national_admin else "provincial_manager",
        is_active=True, require_password_change=False,
    )
    assignment = RoleAssignment(
        id=uuid.uuid4(), user_id=user.id,
        role_id=world.roles["admin" if national_admin else "provincial_manager"].id,
        scope_type="national" if national_admin else "organization",
        scope_id="*" if national_admin else str(world.region_x.id),
        valid_from=NOW - timedelta(days=1), status="active",
        assigned_by=world.admin.user.id, reason="isolated read-window fixture",
    )
    identity = AuthIdentity(
        id=uuid.uuid4(), user_id=user.id, identity_type="mobile", provider_key="test",
        identifier_hash=f"{number:064x}", hash_version=1,
        verified_at=NOW - timedelta(hours=1), status="active",
    )
    world.db.add(person)
    world.db.flush()
    world.db.add(user)
    world.db.flush()
    world.db.add_all([assignment, identity])
    world.db.flush()
    return SimpleNamespace(person=person, user=user, assignment=assignment, identity=identity)


@pytest.mark.parametrize("position", ("returned", "lookahead"))
@pytest.mark.parametrize("mutation", (
    "count_grant_revoked", "person_binding", "authorization_version",
    "verified_identity_revoked", "verified_identity_replaced",
))
def test_candidate_and_private_lookahead_drift_withhold_the_whole_page(
    world, monkeypatch, position, mutation,
):
    first, second = _new_counter(world, 1), _new_counter(world, 2)
    candidate = first if position == "returned" else second
    replacement_person = Person(
        id=uuid.uuid4(), organization_id=world.region_x.id,
        employee_no="READ-WINDOW-REBOUND", name="重新绑定人员",
        employment_status="active", source_updated_at=NOW,
    )
    world.db.add(replacement_person)
    world.db.flush()

    def change(snapshots):
        assert [row.user_id for row in snapshots] == [first.user.id, second.user.id]
        if mutation == "count_grant_revoked":
            statement = update(RoleAssignment.__table__).where(
                RoleAssignment.id == candidate.assignment.id,
            ).values(status="revoked", revoked_at=NOW, revoked_by=world.admin.user.id)
        elif mutation == "person_binding":
            statement = update(User.__table__).where(User.id == candidate.user.id).values(
                person_id=replacement_person.id,
            )
        elif mutation == "authorization_version":
            statement = update(User.__table__).where(User.id == candidate.user.id).values(
                authorization_version=2,
            )
        else:
            statement = update(AuthIdentity.__table__).where(
                AuthIdentity.id == candidate.identity.id,
            ).values(status="revoked", revoked_at=NOW)
        world.db.execute(statement)
        if mutation == "verified_identity_replaced":
            world.db.execute(insert(AuthIdentity.__table__).values(
                id=uuid.uuid4(), user_id=candidate.user.id, identity_type="mobile",
                provider_key="replacement-test", identifier_hash="a" * 64, hash_version=1,
                verified_at=NOW, status="active",
            ))

    observed = _after_scan(monkeypatch, change)
    error = _assert_blocked(world, lambda: _call(
        world, "assignees", location_id=world.region_location.id, limit=1,
    ))
    assert observed and error.http_status_code == 503


@pytest.mark.parametrize("stage", STAGES)
def test_actor_catalog_deny_without_authorization_version_bump_is_rechecked(
    world, monkeypatch, stage,
):
    original_version = world.admin.user.authorization_version
    manage_id = world.db.scalar(select(Permission.id).where(
        Permission.resource == "stocktake", Permission.action == "manage",
    ))

    def revoke(_snapshots):
        world.db.execute(update(RolePermission.__table__).where(
            RolePermission.role_id == world.roles["admin"].id,
            RolePermission.permission_id == manage_id,
        ).values(effect="deny"))

    observed = _after_scan(monkeypatch, revoke)
    _assert_blocked(world, lambda: _call(world, stage))
    assert observed
    assert world.db.scalar(select(User.authorization_version).where(
        User.id == world.admin.user.id,
    )) == original_version


def test_actor_revoked_after_last_candidate_revalidation_cannot_receive_page(world, monkeypatch):
    original = service._revalidate_options
    checked = []

    def revoke_after_candidates(*args, **kwargs):
        original(*args, **kwargs)
        checked.extend(kwargs["snapshots"])
        world.db.execute(update(RoleAssignment.__table__).where(
            RoleAssignment.id == world.admin.assignment.id,
        ).values(status="revoked", revoked_at=NOW, revoked_by=world.admin.user.id))

    monkeypatch.setattr(service, "_revalidate_options", revoke_after_candidates)
    error = _assert_blocked(world, lambda: _call(world, "assignees"))
    assert checked and error.http_status_code == 403


def test_legal_personal_parent_rebinding_is_not_hidden_by_still_valid_scope(world, monkeypatch):
    alternate = StockLocation(
        id=uuid.uuid4(), code="READ-WINDOW-PARENT", name="同组织另一区域仓",
        location_type="region", owner_org_id=world.region_x.id, status="active",
    )
    world.db.add(alternate)
    world.db.flush()

    def move(_snapshots):
        world.db.execute(update(StockLocation.__table__).where(
            StockLocation.id == world.personal_location.id,
        ).values(parent_id=alternate.id))

    with monkeypatch.context() as patch:
        observed = _after_scan(patch, move)
        error = _assert_blocked(world, lambda: _call(world, "assignees"))
        assert observed and error.http_status_code == 503
    # Both parent bindings are eligible individually; only the mixed read fails.
    assert _call(world, "assignees").items


@pytest.mark.parametrize("stage", ("locations", "assignees"))
def test_asset_ancestor_reparenting_within_region_still_invalidates_read(world, monkeypatch, stage):
    branch_a = _organization(world.db, "READ-BRANCH-A", "原资产父组织", "region_company", parent=world.region_x)
    branch_b = _organization(world.db, "READ-BRANCH-B", "新资产父组织", "region_company", parent=world.region_x)
    intermediate = _organization(world.db, "READ-INTERMEDIATE", "中间资产组织", "region_company", parent=branch_a)
    owner = _organization(world.db, "READ-ASSET", "资产组织", "region_company", parent=intermediate)
    assert _call(world, stage, owner_org_id=owner.id).items

    def move(_snapshots):
        # The selected asset owner's own parent_id is unchanged. A full ancestor
        # snapshot is needed; merely comparing the selected row cannot see this.
        world.db.execute(update(Organization.__table__).where(
            Organization.id == intermediate.id,
        ).values(parent_id=branch_b.id))

    with monkeypatch.context() as patch:
        _after_scan(patch, move)
        error = _assert_blocked(world, lambda: _call(world, stage, owner_org_id=owner.id))
        assert error.http_status_code == 503
    assert _call(world, stage, owner_org_id=owner.id).items


@pytest.mark.parametrize("mutation", ("record_replaced", "ends_at_read_time"))
def test_custody_record_identity_and_validity_boundary_are_part_of_snapshot(world, monkeypatch, mutation):
    original_id = world.custody.id

    def change(_snapshots):
        if mutation == "record_replaced":
            world.db.execute(delete(CustodyAssignment.__table__).where(
                CustodyAssignment.id == original_id,
            ))
            world.db.execute(insert(CustodyAssignment.__table__).values(
                id=uuid.uuid4(), location_id=world.personal_location.id,
                custodian_person_id=world.technician.person.id,
                valid_from=NOW - timedelta(days=10), valid_to=None,
            ))
        else:
            world.db.execute(update(CustodyAssignment.__table__).where(
                CustodyAssignment.id == original_id,
            ).values(valid_to=NOW))

    with monkeypatch.context() as patch:
        observed = _after_scan(patch, change)
        error = _assert_blocked(world, lambda: _call(world, "assignees"))
        assert observed and error.http_status_code == 503
    if mutation == "record_replaced":
        assert _call(world, "assignees").items


def _clock_for_read_window(monkeypatch, *, on_return_clock=None):
    ticks = []

    class ClockType(type):
        def __instancecheck__(cls, value):
            return isinstance(value, datetime)

    class Clock(metaclass=ClockType):
        @classmethod
        def now(cls, tz=None):
            value = NOW + timedelta(seconds=min(len(ticks), 2))
            ticks.append(value)
            if len(ticks) == 4 and on_return_clock is not None:
                on_return_clock()
            return value if tz is None else value.astimezone(tz)

    monkeypatch.setattr(service, "datetime", Clock)
    return ticks


@pytest.mark.parametrize("expires", ("actor", "candidate", "lookahead", "custody"))
def test_final_wall_clock_expiry_withholds_a_page_qualified_earlier(world, monkeypatch, expires):
    first, second = _new_counter(world, 1), _new_counter(world, 2)
    deadline = NOW + timedelta(seconds=2)
    if expires == "custody":
        world.db.execute(update(CustodyAssignment.__table__).where(
            CustodyAssignment.id == world.custody.id,
        ).values(valid_to=deadline))
        args = {}
    else:
        assignment = (world.admin.assignment if expires == "actor"
                      else first.assignment if expires == "candidate" else second.assignment)
        world.db.execute(update(RoleAssignment.__table__).where(
            RoleAssignment.id == assignment.id,
        ).values(valid_to=deadline))
        args = {"location_id": world.region_location.id, "limit": 1}
    ticks = _clock_for_read_window(monkeypatch)
    _assert_blocked(world, lambda: _call(world, "assignees", now=None, **args))
    assert len(ticks) >= 3 and ticks[-1] == deadline


@pytest.mark.parametrize("position", ("actor", "returned", "lookahead"))
def test_future_grant_activation_cannot_invalidate_eligibility_after_recheck(
    world, monkeypatch, position,
):
    first = _new_counter(world, 1, national_admin=True)
    second = _new_counter(world, 2, national_admin=True)
    subject = world.admin if position == "actor" else first if position == "returned" else second
    deadline = NOW + timedelta(seconds=2)
    # The role binding is valid for this headquarters person, but is not yet
    # effective at initial scan or candidate revalidation. Its explicit deny
    # must take precedence as soon as the scheduled assignment becomes active.
    permission_id = world.db.scalar(select(Permission.id).where(
        Permission.resource == "stocktake",
        Permission.action == ("manage" if position == "actor" else "count"),
    ))
    world.db.execute(update(RolePermission.__table__).where(
        RolePermission.role_id == world.roles["provincial_manager"].id,
        RolePermission.permission_id == permission_id,
    ).values(effect="deny"))
    world.db.add(RoleAssignment(
        id=uuid.uuid4(), user_id=subject.user.id,
        role_id=world.roles["provincial_manager"].id,
        scope_type="organization", scope_id=str(world.region_x.id),
        valid_from=deadline, status="scheduled", assigned_by=world.admin.user.id,
        reason="future deny read-window fixture",
    ))
    world.db.flush()
    early = _call(world, "assignees", location_id=world.region_location.id, limit=1)
    assert early.items[0].assignee_user_id == first.user.id
    assert early.next_after_person_id == first.person.id
    ticks = _clock_for_read_window(monkeypatch)
    _assert_blocked(world, lambda: _call(
        world, "assignees", location_id=world.region_location.id, limit=1, now=None,
    ))
    assert len(ticks) >= 3 and ticks[-1] == deadline


@pytest.mark.parametrize("stage", ("locations", "assignees"))
def test_future_custody_activation_is_not_lost_when_region_has_no_current_custody(
    world, monkeypatch, stage,
):
    deadline = NOW + timedelta(seconds=2)
    world.db.add(CustodyAssignment(
        id=uuid.uuid4(), location_id=world.region_location.id,
        custodian_person_id=world.technician.person.id,
        valid_from=deadline, valid_to=None,
    ))
    world.db.flush()
    if stage == "locations":
        args = {}
        early = _call(world, stage)
        region_option = next(row for row in early.items if row.location_id == world.region_location.id)
        assert region_option.custodian_person_id is None
    else:
        args = {"location_id": world.region_location.id}
        assert _call(world, stage, **args).items
    ticks = _clock_for_read_window(monkeypatch)
    _assert_blocked(world, lambda: _call(world, stage, now=None, **args))
    assert len(ticks) >= 3 and ticks[-1] == deadline


@pytest.mark.parametrize("position", ("returned", "lookahead"))
def test_location_page_horizon_covers_returned_and_private_lookahead_custody(
    world, monkeypatch, position,
):
    early = _call(world, "locations")
    assert len(early.items) == 2
    location_id = early.items[0 if position == "returned" else 1].location_id
    deadline = NOW + timedelta(seconds=2)
    if location_id == world.personal_location.id:
        world.db.execute(update(CustodyAssignment.__table__).where(
            CustodyAssignment.id == world.custody.id,
        ).values(valid_to=deadline))
    else:
        assert location_id == world.region_location.id
        world.db.add(CustodyAssignment(
            id=uuid.uuid4(), location_id=location_id,
            custodian_person_id=world.technician.person.id,
            valid_from=NOW - timedelta(days=1), valid_to=deadline,
        ))
        world.db.flush()
    assert _call(world, "locations", limit=1).next_after_id == early.items[0].location_id
    ticks = _clock_for_read_window(monkeypatch)
    _assert_blocked(world, lambda: _call(world, "locations", limit=1, now=None))
    assert len(ticks) >= 3 and ticks[-1] == deadline


def test_final_horizon_clock_is_followed_by_no_database_read_or_write(world, monkeypatch):
    before = _business_snapshot(world)
    final = []
    ticks = _clock_for_read_window(monkeypatch, on_return_clock=lambda: final.append(True))
    statements = []

    def observe_sql(_connection, _cursor, statement, _params, _context, _many):
        assert not final, "the final eligibility clock must be after all database work"
        statements.append(statement)

    event.listen(world.db.bind, "before_cursor_execute", observe_sql)
    try:
        page = _call(world, "assignees", now=None)
        assert page.items and page.start_ready is False
    finally:
        event.remove(world.db.bind, "before_cursor_execute", observe_sql)
    assert final and len(ticks) == 4
    assert statements and all(row.lstrip().upper().startswith("SELECT") for row in statements)
    assert _business_snapshot(world) == before


def test_equivalent_role_query_order_does_not_create_false_actor_drift(world, monkeypatch):
    world.db.add(RoleAssignment(
        id=uuid.uuid4(), user_id=world.admin.user.id,
        role_id=world.roles["provincial_manager"].id,
        scope_type="organization", scope_id=str(world.region_x.id),
        valid_from=NOW - timedelta(days=1), status="active",
        assigned_by=world.admin.user.id, reason="multiple-role ordering fixture",
    ))
    world.db.flush()
    expected = _call(world, "regions")
    original = service.load_formal_principal
    calls = []

    def permute(*args, **kwargs):
        principal = original(*args, **kwargs)
        if len(calls) % 2:
            principal = replace(
                principal, assignments=tuple(reversed(principal.assignments)),
                entitlements=tuple(reversed(principal.entitlements)),
            )
        calls.append(tuple(row.assignment_id for row in principal.assignments))
        return principal

    monkeypatch.setattr(service, "load_formal_principal", permute)
    before = _business_snapshot(world)
    assert _call(world, "regions") == expected
    assert len(calls) >= 2 and len(set(calls)) >= 2
    assert _business_snapshot(world) == before
