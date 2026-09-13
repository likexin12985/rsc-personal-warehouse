"""Transit opening selection retains exact region and count authority."""
from dataclasses import replace
from uuid import uuid4
import pytest
from sqlalchemy import event
from app.inventory_models import StockLocation
from app.formal_services import opening_stocktake as opening
from test_opening_start_options import db, world, _call
from app.formal_services.opening_start_options import OpeningStartOptionError
from test_opening_stocktake_service import _start, _fixed_opening_stocktake_database_now


@pytest.fixture
def transit(world):
    row = StockLocation(id=uuid4(), code='SYNTHETIC-TRANSIT', name='测试在途位置', location_type='transit',
        owner_org_id=world.region_x.id, parent_id=world.region_location.id, status='active')
    world.db.add(row); world.db.commit()
    return row


def test_manager_can_select_transit_and_only_real_managers_can_count(world, transit):
    statements = []
    def capture(_c, _cursor, sql, *_): statements.append(sql.lstrip().split()[0].upper())
    event.listen(world.db.bind, 'before_cursor_execute', capture)
    try:
        rows = _call(world, 'locations', actor='manager_x').items
        item = next(row for row in rows if row.location_id == transit.id)
        assert item.location_type == 'transit' and item.custodian_person_id is None
        people = _call(world, 'assignees', actor='manager_x', location_id=transit.id).items
        assert {row.assignee_user_id for row in people} == {world.admin.user.id, world.manager_x.user.id}
        assert statements and set(statements) == {'SELECT'}
    finally: event.remove(world.db.bind, 'before_cursor_execute', capture)


@pytest.mark.parametrize('bad', ['missing', 'personal', 'owner', 'inactive'])
def test_invalid_transit_parent_never_becomes_start_candidate(world, transit, bad):
    if bad == 'missing': transit.parent_id = None
    elif bad == 'personal': transit.parent_id = world.personal_location.id
    elif bad == 'owner': transit.owner_org_id = world.region_y.id
    else: world.region_location.status = 'inactive'
    world.db.commit()
    if bad in {'personal', 'inactive'}:
        with pytest.raises(OpeningStartOptionError): _call(world, 'locations')
    else:
        assert transit.id not in {row.location_id for row in _call(world, 'locations').items}


def test_transit_start_creates_count_task_without_implying_opening(world, transit):
    command = replace(world.command, scopes=(replace(world.command.scopes[0], location_id=transit.id),))
    result = _start(world, command=command)
    assert result.status == 'counting' and result.snapshot_line_count == 0
    assert result.scope_count == 1 and result.control_line_count == 1


def test_technician_cannot_replace_transit_count_manager(world, transit):
    command = replace(world.command, scopes=(replace(world.command.scopes[0], location_id=transit.id,
        assignee_user_id=world.technician.user.id),))
    with pytest.raises(opening.OpeningStocktakeError) as error: _start(world, command=command)
    assert error.value.code == 'regional_assignee_count_forbidden'
