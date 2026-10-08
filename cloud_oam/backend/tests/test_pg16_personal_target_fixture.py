from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import event, select

from app.inventory_models import CustodyAssignment, StockLocation
from pg16_personal_target_fixture import existing_personal_target
from test_material_request_my_receiving import receiving_world, outbound_world

pytest_plugins = ('test_material_request_picking',)


@pytest.mark.parametrize('fault', [None, 'duplicate', 'other_target', 'other_region', 'expired_custody'])
def test_nested_fulfillment_reuses_one_exact_target_without_writes(receiving_world, fault):
    db, actor, _, location, *_ = receiving_world
    now = datetime.now(timezone.utc)
    if fault == 'duplicate':
        db.add(StockLocation(id=uuid4(), code='DUPLICATE-'+uuid4().hex, name='Synthetic duplicate',
            location_type='personal', owner_org_id=location.owner_org_id, parent_id=location.parent_id,
            custodian_person_id=actor.person_id, status='active'))
    if fault == 'expired_custody':
        row = db.scalars(select(CustodyAssignment).where(CustodyAssignment.location_id == location.id)).one()
        row.valid_to = now - timedelta(seconds=1)
    db.flush()
    args = dict(person_id=actor.person_id, region_id=uuid4() if fault == 'other_region' else location.owner_org_id,
        location_id=uuid4() if fault == 'other_target' else location.id, now=now)
    expected_id = location.id
    statements = []
    def query_only(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)
        assert statement.lstrip().upper().startswith('SELECT ')
    engine = db.get_bind()
    event.listen(engine, 'before_cursor_execute', query_only)
    try:
        if fault:
            with pytest.raises(AssertionError, match='fixture'):
                existing_personal_target(db, **args)
        else:
            assert existing_personal_target(db, **args) == expected_id
        assert statements
    finally:
        event.remove(engine, 'before_cursor_execute', query_only)
