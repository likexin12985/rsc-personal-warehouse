"""Provision a synthetic personal location, then establish it through real APIs."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session


def establish_recipient_location(api_engine, *, fixture, admin_user_id, manager_user_id, recipient_user_id):
    from app.models import User
    from app.foundation_models import Person, Organization
    from app.inventory_models import StockLocation, CustodyAssignment
    from test_postgresql16_release_gate import (
        _sqlalchemy_url, _role_password, _establish_multiround_stocktake_location,
    )
    engine = create_engine(_sqlalchemy_url(role='star_oam_migrator', password=_role_password('star_oam_migrator')))
    try:
        with Session(engine) as db:
            person = db.get(Person, db.get(User, recipient_user_id).person_id)
            organization = db.get(Organization, person.organization_id)
            seen = set()
            while organization.id != fixture['region_org_id']:
                assert organization.id not in seen and organization.parent_id is not None
                seen.add(organization.id)
                organization = db.get(Organization, organization.parent_id)
                assert organization is not None
            assert organization.org_type == 'region_company' and organization.status == 'active'
            assert db.scalar(select(StockLocation.id).where(StockLocation.custodian_person_id == person.id,
                StockLocation.location_type == 'personal', StockLocation.status == 'active')) is None
            parent = StockLocation(id=uuid4(), code=f'PG16-INBOUND-REGION-{uuid4().hex}',
                name='PG16 recipient regional location', location_type='region',
                owner_org_id=organization.id, status='active')
            db.add(parent); db.flush()
            location = StockLocation(id=uuid4(), code=f'PG16-INBOUND-PERSON-{uuid4().hex}',
                name='PG16 recipient personal warehouse', location_type='personal',
                owner_org_id=organization.id, parent_id=parent.id,
                custodian_person_id=person.id, status='active')
            db.add(location); db.flush()
            db.add(CustodyAssignment(id=uuid4(), location_id=location.id,
                custodian_person_id=person.id, valid_from=datetime.now(timezone.utc) - timedelta(days=1)))
            location_id = location.id
            db.commit()
    finally:
        engine.dispose()
    # The technician counts their own empty warehouse; the regional manager
    # and headquarters reviewer remain separate identities. Zero confirmation
    # creates qualification, never fake stock or a synthetic inventory move.
    _establish_multiround_stocktake_location(api_engine,
        fixture={**fixture, 'recount_location_id': location_id}, actor_user_id=admin_user_id,
        assignee_user_id=manager_user_id, count_user_id=recipient_user_id, expected_snapshot_line_count=0)
    print('PG16 recipient: real zero opening, regional/HQ review and close PASS', flush=True)
    return location_id
