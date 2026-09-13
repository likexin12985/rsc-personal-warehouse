"""Real zero-opening for a physical transit location, with durable history."""
from uuid import uuid4
from sqlalchemy import update
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.stocktake_models import FormalStocktakeTask
from app.inventory_models import StockLocation


def establish_transit_opening(api_engine, fixture_engine, *, fixture, admin_user_id, manager_user_id):
    from test_postgresql16_release_gate import _establish_multiround_stocktake_location
    with Session(fixture_engine) as db:
        parent = StockLocation(id=uuid4(), code=f'PG16-TRANSIT-PARENT-{uuid4().hex}', name='Synthetic transit regional parent',
            location_type='region', owner_org_id=fixture["region_org_id"], status='active')
        db.add(parent); db.flush()
        transit = StockLocation(id=uuid4(), code=f'PG16-TRANSIT-{uuid4().hex}', name='Synthetic physical transit location',
            location_type='transit', owner_org_id=fixture["region_org_id"], parent_id=parent.id, status='active')
        db.add(transit); db.flush()
        fixture = {**fixture, 'recount_location_id': transit.id}
        location_id, parent_id = transit.id, parent.id
        db.commit()
    task_id = _establish_multiround_stocktake_location(api_engine, fixture=fixture,
        actor_user_id=admin_user_id, assignee_user_id=manager_user_id, expected_snapshot_line_count=0)
    # Even the migrator cannot relabel a used location to bypass the downgrade
    # refusal. Harmless labels remain editable; structural history is retained.
    for location, changes in ((location_id, {'location_type': 'region'}), (location_id, {'parent_id': None}),
            (parent_id, {'status': 'inactive'}), (parent_id, {'location_type': 'transit'})):
        with Session(fixture_engine) as db:
            try:
                db.execute(update(StockLocation).where(StockLocation.id == location).values(**changes)); db.commit()
            except DBAPIError as exc:
                assert '0102 established transit' in str(exc.orig); db.rollback()
            else: raise AssertionError('used transit coordinates were changed')
    with Session(api_engine) as db:
        assert db.get(FormalStocktakeTask, task_id).status == 'closed'
        assert db.get(StockLocation, location_id).location_type == 'transit'
    print('PG16 transit: zero count, both reviews, posting, close and immutable coordinates PASS', flush=True)
    return task_id, location_id


def assert_transit_opening_gate(api_engine, fixture_engine):
    """Use a fresh synthetic region, retaining older unfinished test tasks."""
    from sqlalchemy import select
    from app.foundation_models import Role
    from test_opening_stocktake_service import _organization, _user_with_role
    from test_postgresql16_release_gate import _seed_0047_stocktake_inventory
    with Session(fixture_engine) as db:
        headquarters = _organization(db, 'PG16-TRANSIT-HQ-' + uuid4().hex, 'Synthetic transit HQ', 'headquarters')
        region = _organization(db, 'PG16-TRANSIT-REGION-' + uuid4().hex, 'Synthetic transit region', 'region_company', parent=headquarters)
        roles = {row.code: row for row in db.scalars(select(Role))}
        admin = _user_with_role(db, headquarters, roles['admin'], 'national', '*', 'Synthetic transit reviewer').user.id
        manager = _user_with_role(db, region, roles['provincial_manager'], 'organization', str(region.id), 'Synthetic transit manager').user.id
        technician = _user_with_role(db, region, roles['technician'], 'person', None, 'Synthetic transit technician').user.id
        db.commit()
    reference = _seed_0047_stocktake_inventory(api_engine, actor_user_id=admin,
        assignee_user_id=manager, recipient_user_id=technician, prepare_only=True)
    return establish_transit_opening(api_engine, fixture_engine, fixture=reference,
        admin_user_id=admin, manager_user_id=manager)
