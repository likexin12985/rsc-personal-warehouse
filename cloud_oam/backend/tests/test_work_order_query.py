"""Own formal work-order selection, source integrity and literal search."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest
from sqlalchemy import text

from app.demand_models import OamWorkOrder
from app.foundation_models import ExternalObject, ExternalObjectVersion, SourceSystem
from app.formal_services import oam_work_order_projection as projection
from app.formal_services import work_order_query as query
from app.formal_services.inventory_posting import InventoryPostingError
from test_work_order_material_evidence import db, evidence, world
from work_order_fixtures import canonical_source


@pytest.fixture
def choices(db, evidence):
    source = canonical_source(db)
    now = datetime.now(timezone.utc) - timedelta(minutes=1)
    rows=[]
    # Remove the older evidence fixture from this person's choices without
    # altering its historical stock; the query must only select current owner.
    evidence.order.engineer_person_id = evidence.world.headquarters_reviewer_person.id
    for number,(status,person) in enumerate((("active",evidence.world.person),("closed",evidence.world.person),
            ("active",evidence.world.headquarters_reviewer_person)),1):
        external=ExternalObject(id=uuid4(),source_system_id=source.id,entity_type="work_order",external_id=f"WO-QUERY-{number}")
        db.add(external);db.flush()
        payload={"work_order_no":f"WO-QUERY-{number}%", "organization_id":str(evidence.world.organization.id),
            "engineer_person_id":str(person.id),"status":status}
        version=ExternalObjectVersion(id=uuid4(),external_object_id=external.id,
            source_version=projection._projection_source_version(str(number)*64,"b"*64),source_updated_at=now,
            valid_from=now,valid_to=None,payload_jsonb=payload,payload_sha256=projection._sha256(payload),is_current=True,created_at=now)
        db.add(version);db.flush();external.current_version_id=version.id
        order=OamWorkOrder(id=uuid4(),external_object_id=external.id,work_order_no=payload["work_order_no"],
            organization_id=evidence.world.organization.id,engineer_person_id=person.id,status=status,
            source_updated_at=now,created_at=now,updated_at=now)
        db.add(order);rows.append(SimpleNamespace(order=order,external=external,version=version))
    db.commit()
    return SimpleNamespace(actor=evidence.world.current_principal,source=source,rows=rows,world=evidence.world)


def test_only_own_work_orders_and_verified_source_metadata_are_returned_without_writes(db,choices):
    db.execute(text("PRAGMA query_only=ON"))
    active=query.list_my_work_orders(db,actor=choices.actor)
    assert [row.work_order_id for row in active.items]==[choices.rows[0].order.id]
    assert active.items[0].can_operate and active.items[0].freshness=="fresh"
    all_rows=query.list_my_work_orders(db,actor=choices.actor,status=None)
    assert {row.work_order_id for row in all_rows.items}=={row.order.id for row in choices.rows[:2]}
    assert not next(row for row in all_rows.items if row.status=="closed").can_operate
    assert all(row.source_system=="starcharge_oam" and row.source_version for row in all_rows.items)
    assert not db.new and not db.dirty and not db.deleted


def test_pagination_is_complete_and_search_treats_wildcards_as_literal(db,choices):
    first=query.list_my_work_orders(db,actor=choices.actor,status=None,limit=1)
    second=query.list_my_work_orders(db,actor=choices.actor,status=None,limit=1,after_id=first.next_after_id)
    assert first.next_after_id is not None and second.next_after_id is None
    assert {first.items[0].work_order_id,second.items[0].work_order_id}=={row.order.id for row in choices.rows[:2]}
    assert len(query.list_my_work_orders(db,actor=choices.actor,status=None,search="%").items)==2
    assert query.list_my_work_orders(db,actor=choices.actor,status=None,search="_").items==()


@pytest.mark.parametrize("corruption",["missing_version","wrong_payload","wrong_source","deleted"])
def test_broken_source_is_reported_instead_of_hiding_or_guessing_formal_binding(db,choices,corruption):
    row=choices.rows[0]
    if corruption=="missing_version":row.external.current_version_id=None
    elif corruption=="wrong_payload":row.version.payload_jsonb={**row.version.payload_jsonb,"work_order_no":"OTHER"}
    elif corruption=="wrong_source":choices.source.code="prototype-source"
    else:row.external.deleted_at=datetime.now(timezone.utc)
    db.commit()
    with pytest.raises(InventoryPostingError) as exc:
        query.list_my_work_orders(db,actor=choices.actor)
    assert exc.value.code in {"work_order_source_invalid","work_order_projection_invalid"}


def test_stale_snapshot_remains_explicit_without_inventing_a_new_sync_time(db,choices):
    row=choices.rows[0];old=datetime.now(timezone.utc)-timedelta(hours=2)
    row.order.source_updated_at=old;row.order.updated_at=old
    row.version.source_updated_at=old;row.version.valid_from=old;row.version.created_at=old
    db.commit()
    item=query.list_my_work_orders(db,actor=choices.actor).items[0]
    assert item.freshness=="stale" and item.synced_at==old and not item.can_operate


def test_current_authorization_must_match_supplied_version_before_any_selection(db,choices):
    choices.world.current_principal=replace(choices.actor,authorization_version=choices.actor.authorization_version+1)
    with pytest.raises(InventoryPostingError) as exc:
        query.list_my_work_orders(db,actor=choices.actor)
    assert exc.value.code=="actor_principal_stale"
    choices.world.current_principal=replace(choices.actor,entitlements=())
    with pytest.raises(InventoryPostingError) as exc:
        query.list_my_work_orders(db,actor=choices.actor)
    assert exc.value.code=="work_order_forbidden"


def test_http_own_work_order_query_has_no_cache_and_explicit_all_status(db,choices):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.database import get_db
    from app.routers import formal_work_order_query as api
    app=FastAPI();app.include_router(api.router,prefix="/api")
    app.dependency_overrides[get_db]=lambda:db
    for route in api.router.routes:
        for dependency in route.dependant.dependencies:
            if dependency.name=="principal":
                app.dependency_overrides[dependency.call]=lambda:choices.actor
    with TestClient(app) as client:
        response=client.get("/api/v1/work-orders/mine?status=all")
        assert response.status_code==200,response.text
        assert response.headers["cache-control"]=="private, no-store"
        assert {row["work_order_id"] for row in response.json()["items"]}=={str(row.order.id) for row in choices.rows[:2]}
        assert response.json()["schema_version"]=="1.0"
        assert client.get("/api/v1/work-orders/mine?status=arbitrary").status_code==422
        assert client.get("/api/v1/work-orders/mine?limit=101").status_code==422


def test_permission_change_during_read_discards_the_entire_response(db,choices,monkeypatch):
    original=projection._validate_current_projection_evidence
    def revoke(**kwargs):
        original(**kwargs)
        choices.world.current_principal=replace(choices.actor,authorization_version=choices.actor.authorization_version+1)
    monkeypatch.setattr(projection,"_validate_current_projection_evidence",revoke)
    with pytest.raises(InventoryPostingError) as exc:
        query.list_my_work_orders(db,actor=choices.actor)
    assert exc.value.code=="actor_principal_stale"
