from __future__ import annotations

from datetime import timedelta
import uuid

import pytest
from sqlalchemy import event

from app.formal_services import opening_stocktake as service
from app.inventory_models import CustodyAssignment, StockLocation
from test_opening_stocktake_service import NOW, db, world, _organization  # noqa: F401


def _qualify(world, *, location=None, owner=None, custodies=None, **kwargs):
    location = location or world.personal_location
    if custodies is None:
        custodies = [world.custody] if location.id == world.personal_location.id else []
    return service._qualify_opening_scope(
        world.db, actor=world.principals["admin"], region_org_id=world.region_x.id,
        owner=owner or world.region_x, location=location,
        effective_custodies=custodies, now=NOW, lock_rows=False, **kwargs,
    )


def test_qualification_has_no_business_ids_writes_or_row_locks(world, monkeypatch):
    statements = []
    orm_locks = []

    def before_sql(_conn, _cursor, statement, _parameters, _context, _many):
        statements.append(statement)

    def before_orm(state):
        orm_locks.append(getattr(state.statement, "_for_update_arg", None))

    def forbidden_uuid():
        pytest.fail("reference qualification generated a business UUID")

    event.listen(world.db.bind, "before_cursor_execute", before_sql)
    event.listen(world.db, "do_orm_execute", before_orm)
    monkeypatch.setattr(service.uuid, "uuid4", forbidden_uuid)
    try:
        result = _qualify(world)
    finally:
        event.remove(world.db.bind, "before_cursor_execute", before_sql)
        event.remove(world.db, "do_orm_execute", before_orm)
    assert result.owner.id == world.region_x.id
    assert result.location.id == world.personal_location.id
    assert result.custodian_person_id == world.technician.person.id
    assert result.manager_grant.assignment_id == world.admin.assignment.id
    assert not hasattr(result, "scope_id") and not hasattr(result, "scope_sha256")
    assert statements and all(row.lstrip().upper().startswith("SELECT") for row in statements)
    assert all(lock is None for lock in orm_locks)
    assert not world.db.new and not world.db.deleted and not world.db.dirty


def test_region_with_personal_child_remains_eligible(world):
    result = _qualify(world, location=world.region_location)
    assert result.custodian_person_id is None


def test_region_custody_does_not_require_a_populated_location_person_field(world):
    custody = CustodyAssignment(
        id=uuid.uuid4(), location_id=world.region_location.id,
        custodian_person_id=world.manager_x.person.id,
        valid_from=NOW - timedelta(days=1), valid_to=None,
    )
    world.db.add(custody)
    world.db.flush()
    result = _qualify(world, location=world.region_location, custodies=[custody])
    assert world.region_location.custodian_person_id is None
    assert result.custodian_person_id == world.manager_x.person.id


def test_distinct_asset_and_physical_owners_in_same_region_remain_eligible(world):
    child = _organization(world.db, "SUB-ASSET", "资产子区域", "region_company", parent=world.region_x)
    result = _qualify(world, owner=child)
    assert result.owner.id != result.location.owner_org_id


@pytest.mark.parametrize("mutation,code", [
    ("owner_disabled", "asset_owner_invalid"),
    ("owner_wrong_type", "asset_owner_invalid"),
    ("owner_cross_region", "asset_owner_outside_region"),
    ("location_disabled", "stock_location_invalid"),
    ("location_wrong_type", "stock_location_invalid"),
    ("personal_no_custody", "personal_custody_invalid"),
    ("personal_wrong_custody", "personal_custody_invalid"),
    ("custody_wrong_location", "custody_assignment_not_current"),
    ("custody_future", "custody_assignment_not_current"),
    ("custody_expired", "custody_assignment_not_current"),
    ("custody_ambiguous", "custody_assignment_ambiguous"),
    ("personal_parent_disabled", "personal_location_parent_invalid"),
    ("personal_parent_wrong_owner", "personal_location_parent_invalid"),
    ("personal_not_leaf", "personal_location_not_leaf"),
])
def test_qualification_rejects_invalid_reference_graph(world, mutation, code):
    owner = world.region_x
    custodies = [world.custody]
    if mutation == "owner_disabled":
        owner.status = "inactive"
    elif mutation == "owner_wrong_type":
        owner.org_type = "headquarters"
    elif mutation == "owner_cross_region":
        owner = world.region_y
    elif mutation == "location_disabled":
        world.personal_location.status = "inactive"
    elif mutation == "location_wrong_type":
        world.personal_location.location_type = "quarantine"
    elif mutation == "personal_no_custody":
        custodies = []
    elif mutation == "personal_wrong_custody":
        world.custody.custodian_person_id = world.manager_x.person.id
    elif mutation == "custody_wrong_location":
        world.custody.location_id = world.region_location.id
    elif mutation == "custody_future":
        world.custody.valid_from = NOW + timedelta(seconds=1)
    elif mutation == "custody_expired":
        world.custody.valid_to = NOW
    elif mutation == "custody_ambiguous":
        custodies = [world.custody, world.custody]
    elif mutation == "personal_parent_disabled":
        world.region_location.status = "inactive"
    elif mutation == "personal_parent_wrong_owner":
        world.region_location.owner_org_id = world.region_y.id
    elif mutation == "personal_not_leaf":
        world.db.add(StockLocation(
            id=uuid.uuid4(), code="BROKEN-CHILD", name="不合法子库位",
            owner_org_id=world.region_x.id, parent_id=world.personal_location.id,
            location_type="region", status="inactive",
        ))
    world.db.flush()
    with pytest.raises(service.OpeningStocktakeError) as error:
        _qualify(world, owner=owner, custodies=custodies)
    assert error.value.code == code


def test_nonpersonal_dangling_parent_fails_closed_without_corrupting_database(world, monkeypatch):
    parent = StockLocation(
        id=uuid.uuid4(), code="PARENT-FOR-MISSING-READ", name="父库位",
        owner_org_id=world.region_x.id, parent_id=None, location_type="region", status="active",
    )
    world.db.add(parent)
    world.db.flush()
    world.region_location.parent_id = parent.id
    world.db.flush()
    original_scalar = world.db.scalar

    def missing_parent(statement, *args, **kwargs):
        row = original_scalar(statement, *args, **kwargs)
        return None if isinstance(row, StockLocation) and row.id == parent.id else row

    monkeypatch.setattr(world.db, "scalar", missing_parent)
    with pytest.raises(service.OpeningStocktakeError) as error:
        _qualify(world, location=world.region_location)
    assert error.value.code == "stock_location_parent_missing"
    assert error.value.http_status_code == 503
