"""Read-only selection of an already established synthetic personal warehouse.

The loss gate already creates and opens the engineer's warehouse. A nested
ordinary shipment must reuse it; creating a second one would correctly fail
the production recipient's unique-warehouse guard. Actual receipt/inbound
commands still verify opening history and all business permissions.
"""
from sqlalchemy import or_, select

from app.inventory_models import CustodyAssignment, StockLocation


def existing_personal_target(db, *, person_id, region_id, location_id, now):
    with db.no_autoflush:
        rows = tuple(db.scalars(select(StockLocation).where(
            StockLocation.location_type == 'personal',
            StockLocation.custodian_person_id == person_id,
            StockLocation.status == 'active',
        )))
        assert len(rows) == 1, 'fixture requires exactly one active personal warehouse'
        location = rows[0]
        assert location.id == location_id and location.owner_org_id == region_id, 'fixture target binding differs'
        assert db.scalar(select(StockLocation.id).where(StockLocation.parent_id == location.id).limit(1)) is None, 'fixture target must be a leaf'
        custody = tuple(db.scalars(select(CustodyAssignment).where(
            CustodyAssignment.location_id == location.id,
            CustodyAssignment.valid_from <= now,
            or_(CustodyAssignment.valid_to.is_(None), CustodyAssignment.valid_to > now),
        )))
        assert len(custody) == 1 and custody[0].custodian_person_id == person_id, 'fixture target custody differs'
        return location.id
