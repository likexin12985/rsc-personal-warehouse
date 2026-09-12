"""Synthetic formal OAM references for real command and read tests."""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import select

from app.demand_models import OamWorkOrder
from app.foundation_models import ExternalObject, ExternalObjectVersion, SourceSystem
from app.formal_services import oam_work_order_projection as projection


def canonical_source(db):
    source = db.scalar(select(SourceSystem).where(SourceSystem.code == projection.SOURCE_SYSTEM_CODE))
    if source is None:
        source = SourceSystem(id=uuid4(), code=projection.SOURCE_SYSTEM_CODE, name="Synthetic formal OAM",
            mode=projection.SOURCE_SYSTEM_MODE, enabled=True, configuration_jsonb={})
        db.add(source); db.flush()
    return source


def add_order(db, world, source=None, *, person=None):
    source = source or canonical_source(db)
    now = datetime.now(timezone.utc) - timedelta(minutes=1)
    person = person or world.person
    payload = {"work_order_no": "OPTIONS-" + uuid4().hex,
        "organization_id": str(world.organization.id), "engineer_person_id": str(person.id), "status": "active"}
    external = ExternalObject(id=uuid4(), source_system_id=source.id, entity_type="work_order", external_id=uuid4().hex)
    db.add(external); db.flush()
    version = ExternalObjectVersion(id=uuid4(), external_object_id=external.id,
        source_version=projection._projection_source_version("c" * 64, "d" * 64), source_updated_at=now,
        valid_from=now, valid_to=None, payload_jsonb=payload, payload_sha256=projection._sha256(payload),
        is_current=True, created_at=now)
    db.add(version); db.flush(); external.current_version_id=version.id
    order = OamWorkOrder(id=uuid4(), external_object_id=external.id, work_order_no=payload["work_order_no"],
        organization_id=world.organization.id, engineer_person_id=person.id, status="active",
        source_updated_at=now, created_at=now, updated_at=now)
    db.add(order); db.flush()
    return order
