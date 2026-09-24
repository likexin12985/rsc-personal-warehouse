from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
from types import SimpleNamespace
import uuid

import pytest
from formal_file_integrity import StoredObjectHead, _validate_intent_metadata
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base
from app.formal_access import Entitlement, FormalPrincipal, ScopeGrant
from app.formal_services.inventory_query import (
    InventoryReadError,
    inventory_summary,
    inventory_transaction_detail,
    list_inventory_accounts,
    personal_warehouse,
    personal_warehouse_serials,
    personal_warehouse_transactions,
)
from app.formal_services.inventory_report_snapshot import capture_inventory_report_snapshot
from app.formal_services.inventory_report_jobs import (
    claim_inventory_report_job,
    create_inventory_report_job,
    finish_inventory_report_job,
    revalidate_inventory_report_job,
)
from app.formal_services.inventory_report_workbook import MIME_TYPE
from app.formal_services.file_storage import DownloadIntent, FileStorageError
from app.inventory_report_worker import process_one_inventory_report
from app.formal_services.inventory_report_download import (
    create_inventory_report_download_intent,
    get_inventory_report_job_status,
)
from app.foundation_models import ExternalObject, FileObject, Organization, Person, SourceSystem
from app.inventory_models import (
    CustodyAssignment,
    FormalMaterial,
    InventoryLedgerHead,
    InventoryMovement,
    InventoryTransaction,
    MaterialInventoryPolicy,
    StockAccount,
    StockBalance,
    StockLocation,
)
from app.main import app
from app.models import User


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)


@pytest.fixture()
def db() -> Session:
    engine = create_engine(
        "sqlite+pysqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    session = Session(engine, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _principal(
    *,
    user_id: str,
    person_id: uuid.UUID,
    role_code: str,
    scope_type: str,
    scope_id: str,
    access_mode: str = "active",
    include_inventory: bool = True,
) -> FormalPrincipal:
    assignment_id = uuid.uuid4()
    entitlements = ()
    if include_inventory:
        entitlements = (
            Entitlement(
                assignment_id=assignment_id,
                role_code=role_code,
                scope_type=scope_type,
                scope_id=scope_id,
                resource="inventory",
                action="read",
                field_code="",
                effect="allow",
            ),
        )
    return FormalPrincipal(
        user_id=user_id,
        person_id=person_id,
        account_status="active",
        employment_status="active",
        authorization_version=1,
        access_mode=access_mode,
        assignments=(
            ScopeGrant(
                assignment_id=assignment_id,
                role_code=role_code,
                scope_type=scope_type,
                scope_id=scope_id,
                valid_from=NOW - timedelta(days=1),
                valid_to=None,
            ),
        ),
        entitlements=entitlements,
    )


def _seed_inventory(db: Session) -> dict[str, object]:
    headquarters = Organization(
        id=uuid.uuid4(),
        code="HQ",
        name="总部",
        org_type="headquarters",
        status="active",
    )
    region = Organization(
        id=uuid.uuid4(),
        code="JS",
        name="江苏区域",
        parent_id=headquarters.id,
        org_type="region_company",
        province_code="320000",
        status="active",
    )
    other_region = Organization(
        id=uuid.uuid4(),
        code="ZJ",
        name="浙江区域",
        parent_id=headquarters.id,
        org_type="region_company",
        province_code="330000",
        status="active",
    )
    person = Person(
        id=uuid.uuid4(),
        organization_id=region.id,
        employee_no="E-001",
        name="工程师甲",
        employment_status="active",
    )
    other_person = Person(
        id=uuid.uuid4(),
        organization_id=other_region.id,
        employee_no="E-002",
        name="工程师乙",
        employment_status="active",
    )
    user = User(
        id="90000000-0000-4000-8000-000000000001",
        person_id=person.id,
        account_status="active",
        mobile="13800001001",
        name=person.name,
        password_hash="not-used-by-formal-tests",
        role="technician",
        province="江苏",
        is_active=True,
        require_password_change=False,
    )
    source = SourceSystem(
        id=uuid.uuid4(),
        code="oam-test",
        name="OAM test mirror",
        mode="read_only",
    )
    external = ExternalObject(
        id=uuid.uuid4(),
        source_system_id=source.id,
        entity_type="material",
        external_id="SKU-001",
    )
    material = FormalMaterial(
        id=uuid.uuid4(),
        external_object_id=external.id,
        sku_code="SKU-001",
        name="测试物料",
        specification="",
        base_unit="个",
        status="active",
        source_updated_at=NOW,
    )
    policy = MaterialInventoryPolicy(
        id=uuid.uuid4(),
        material_id=material.id,
        tracking_mode="none",
        quantity_scale=3,
        allow_fraction=True,
        effective_from=NOW - timedelta(days=1),
        effective_to=None,
    )
    region_location = StockLocation(
        id=uuid.uuid4(),
        code="JS-REGION",
        name="江苏区域仓",
        location_type="region",
        owner_org_id=region.id,
        status="active",
    )
    personal_location = StockLocation(
        id=uuid.uuid4(),
        code="JS-P-E001",
        name="工程师甲个人仓",
        location_type="personal",
        owner_org_id=region.id,
        parent_id=region_location.id,
        custodian_person_id=person.id,
        status="active",
    )
    other_region_location = StockLocation(
        id=uuid.uuid4(),
        code="ZJ-REGION",
        name="浙江区域仓",
        location_type="region",
        owner_org_id=other_region.id,
        status="active",
    )
    other_personal_location = StockLocation(
        id=uuid.uuid4(),
        code="ZJ-P-E002",
        name="工程师乙个人仓",
        location_type="personal",
        owner_org_id=other_region.id,
        parent_id=other_region_location.id,
        custodian_person_id=other_person.id,
        status="active",
    )
    custody = CustodyAssignment(
        id=uuid.uuid4(),
        location_id=personal_location.id,
        custodian_person_id=person.id,
        valid_from=NOW - timedelta(days=1),
    )
    other_custody = CustodyAssignment(
        id=uuid.uuid4(),
        location_id=other_personal_location.id,
        custodian_person_id=other_person.id,
        valid_from=NOW - timedelta(days=1),
    )
    account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=headquarters.id,
        custodian_person_id=person.id,
        location_id=personal_location.id,
        material_id=material.id,
        condition_code="new",
        availability_bucket="available",
    )
    other_account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=headquarters.id,
        custodian_person_id=other_person.id,
        location_id=other_personal_location.id,
        material_id=material.id,
        condition_code="new",
        availability_bucket="available",
    )
    # These stage-two models intentionally carry no ORM cascade relationships.
    # Flush each dependency layer explicitly so SQLite foreign-key enforcement
    # tests the same boundary as PostgreSQL instead of relying on mapper order.
    db.add_all([headquarters, source])
    db.flush()
    db.add_all([region, other_region, external])
    db.flush()
    db.add_all([person, other_person, material])
    db.flush()
    db.add_all([user, policy, region_location, other_region_location])
    db.flush()
    db.add_all([personal_location, other_personal_location])
    db.flush()
    db.add_all([custody, other_custody, account, other_account])
    db.flush()
    db.add_all(
        [
            InventoryLedgerHead(
                id=uuid.UUID("40000000-0000-4000-8000-000000000001"),
                stream_key="inventory",
                next_cursor=1,
            ),
            StockBalance(
                stock_account_id=account.id,
                quantity=Decimal("0.000"),
                ledger_cursor=0,
                version=0,
            ),
            StockBalance(
                stock_account_id=other_account.id,
                quantity=Decimal("99.000"),
                ledger_cursor=0,
                version=0,
            ),
        ]
    )
    db.flush()
    return {
        "headquarters": headquarters,
        "region": region,
        "other_region": other_region,
        "person": person,
        "other_person": other_person,
        "user": user,
        "material": material,
        "region_location": region_location,
        "other_region_location": other_region_location,
        "personal_location": personal_location,
        "account": account,
        "other_account": other_account,
    }


def _post_opening(db: Session, values: dict[str, object]) -> InventoryTransaction:
    transaction = InventoryTransaction(
        id=uuid.uuid4(),
        transaction_no="INV-OPEN-001",
        movement_type="opening",
        source_document_type="opening_stocktake",
        source_document_id="OPEN-001",
        posting_key="opening-stocktake:OPEN-001",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        status="posted",
        effective_at=NOW,
        posted_at=NOW,
        ledger_cursor=1,
        actor_user_id=values["user"].id,
    )
    movement = InventoryMovement(
        id=uuid.uuid4(),
        transaction_id=transaction.id,
        line_no=1,
        from_account_id=None,
        to_account_id=values["account"].id,
        external_boundary_code="opening_verified",
        quantity=Decimal("12.345"),
    )
    db.add(transaction)
    db.flush()
    db.add(movement)
    db.flush()
    balance = db.get(StockBalance, values["account"].id)
    balance.quantity = Decimal("12.345")
    balance.ledger_cursor = 1
    balance.version = 1
    head = db.get(
        InventoryLedgerHead,
        uuid.UUID("40000000-0000-4000-8000-000000000001"),
    )
    head.next_cursor = 2
    db.flush()
    return transaction


def test_personal_read_fails_closed_before_opening_and_never_leaks_other_region(db):
    values = _seed_inventory(db)
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )

    summary = inventory_summary(db, actor=actor)
    assert summary.projection_status == "ready"
    assert summary.opening_balance_status == "not_established"
    assert summary.quantity_status == "opening_not_established"
    assert summary.available_qty is None
    assert summary.expected_supply_qty is None

    warehouse = personal_warehouse(db, actor=actor)
    assert warehouse.location_id == values["personal_location"].id
    assert warehouse.items == []
    history = personal_warehouse_transactions(db, actor=actor)
    assert history.location_id == values["personal_location"].id
    assert history.items == []
    assert history.next_after_cursor is None


def test_personal_custody_uses_effective_interval_and_rejects_overlap(db):
    values = _seed_inventory(db)
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )
    assignment = db.scalar(
        select(CustodyAssignment).where(
            CustodyAssignment.location_id == values["personal_location"].id
        )
    )

    assignment.valid_from = datetime.now(timezone.utc) + timedelta(days=1)
    db.flush()
    with pytest.raises(InventoryReadError) as future_error:
        personal_warehouse(db, actor=actor)
    assert future_error.value.code == "personal_custody_not_current"

    assignment.valid_from = NOW - timedelta(days=1)
    assignment.valid_to = datetime.now(timezone.utc) + timedelta(days=1)
    db.flush()
    assert personal_warehouse(db, actor=actor).location_id == values["personal_location"].id

    db.add(
        CustodyAssignment(
            id=uuid.uuid4(),
            location_id=values["personal_location"].id,
            custodian_person_id=values["person"].id,
            valid_from=NOW,
            valid_to=None,
        )
    )
    db.flush()
    with pytest.raises(InventoryReadError) as overlap_error:
        personal_warehouse(db, actor=actor)
    assert overlap_error.value.code == "personal_custody_not_current"


def test_personal_read_rejects_mismatched_account_custodian_before_filtering(db):
    values = _seed_inventory(db)
    db.add(
        StockAccount(
            id=uuid.uuid4(),
            owner_org_id=values["headquarters"].id,
            custodian_person_id=values["other_person"].id,
            location_id=values["personal_location"].id,
            material_id=values["material"].id,
            condition_code="used",
            availability_bucket="available",
        )
    )
    db.flush()
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )

    with pytest.raises(InventoryReadError) as captured:
        personal_warehouse(db, actor=actor)
    assert captured.value.code == "personal_account_custodian_mismatch"


def test_personal_location_rejects_inactive_child(db):
    values = _seed_inventory(db)
    db.add(
        StockLocation(
            id=uuid.uuid4(),
            code="JS-P-E001-OLD",
            name="失效但仍挂接的子位置",
            location_type="quarantine",
            owner_org_id=values["region"].id,
            parent_id=values["personal_location"].id,
            status="inactive",
        )
    )
    db.flush()
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )

    with pytest.raises(InventoryReadError) as captured:
        personal_warehouse(db, actor=actor)
    assert captured.value.code == "personal_location_not_leaf"


def test_personal_location_requires_active_region_parent(db):
    values = _seed_inventory(db)
    values["region_location"].location_type = "headquarters"
    db.flush()
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )

    with pytest.raises(InventoryReadError) as captured:
        personal_warehouse(db, actor=actor)
    assert captured.value.code == "personal_location_parent_invalid"


def test_personal_location_rejects_parent_cycle(db):
    values = _seed_inventory(db)
    values["region_location"].parent_id = values["other_region_location"].id
    values["other_region_location"].parent_id = values["region_location"].id
    db.flush()
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )

    with pytest.raises(InventoryReadError) as captured:
        personal_warehouse(db, actor=actor)
    assert captured.value.code == "personal_location_cycle"


def test_partial_opening_transaction_never_claims_complete_opening(db):
    values = _seed_inventory(db)
    transaction = _post_opening(db, values)
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )

    summary = inventory_summary(db, actor=actor)
    assert summary.projection_status == "ready"
    assert summary.opening_balance_status == "not_established"
    assert summary.quantity_status == "opening_not_established"
    assert summary.ledger_cursor == 1
    assert summary.available_qty is None
    assert summary.physical_in_stock_qty is None
    assert summary.physical_in_transit_qty is None

    page = list_inventory_accounts(db, actor=actor, limit=100)
    assert page.ledger_cursor == summary.ledger_cursor
    assert len(page.items) == 1
    assert page.opening_balance_status == "not_established"
    assert page.items[0].quantity_status == "opening_not_established"
    assert page.items[0].quantity is None
    assert page.items[0].ledger_cursor == 1
    assert page.items[0].owner_org_id == values["headquarters"].id
    assert page.items[0].location_owner_org_id == values["region"].id

    with pytest.raises(InventoryReadError) as blocked_detail:
        inventory_transaction_detail(
            db,
            actor=actor,
            transaction_id=transaction.id,
        )
    assert blocked_detail.value.code == "inventory_opening_not_established"
    assert blocked_detail.value.status_code == 409


def test_region_scope_does_not_follow_asset_owner_into_sibling_region(db):
    values = _seed_inventory(db)
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(values["region"].id),
    )

    page = list_inventory_accounts(db, actor=actor, limit=100)
    assert [row.stock_account_id for row in page.items] == [values["account"].id]


def test_unbound_raw_self_entitlement_cannot_expand_regional_scope(db):
    values = _seed_inventory(db)
    cross_region_account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=values["headquarters"].id,
        custodian_person_id=values["person"].id,
        location_id=values["other_account"].location_id,
        material_id=values["material"].id,
        condition_code="used",
        availability_bucket="available",
    )
    db.add(cross_region_account)
    db.flush()
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(values["region"].id),
    )
    actor = replace(
        actor,
        entitlements=actor.entitlements
        + (
            Entitlement(
                assignment_id=uuid.uuid4(),
                role_code="technician",
                scope_type="person",
                scope_id=str(values["person"].id),
                resource="inventory",
                action="read",
                field_code="",
                effect="allow",
            ),
        ),
    )

    page = list_inventory_accounts(db, actor=actor, limit=100)
    assert [row.stock_account_id for row in page.items] == [values["account"].id]


def test_region_inventory_read_fails_closed_on_reachable_organization_cycle(db):
    values = _seed_inventory(db)
    values["region"].parent_id = values["other_region"].id
    values["other_region"].parent_id = values["region"].id
    db.flush()
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(values["region"].id),
    )

    with pytest.raises(InventoryReadError) as captured:
        list_inventory_accounts(db, actor=actor, limit=100)
    assert captured.value.code == "inventory_scope_organization_cycle"
    assert captured.value.status_code == 409


@pytest.mark.parametrize(
    ("access_mode", "include_inventory"),
    [("restricted_handover", True), ("active", False)],
)
def test_missing_or_restricted_permission_is_denied(
    db,
    access_mode,
    include_inventory,
):
    values = _seed_inventory(db)
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
        access_mode=access_mode,
        include_inventory=include_inventory,
    )

    with pytest.raises(InventoryReadError, match="没有库存读取权限") as error:
        inventory_summary(db, actor=actor)
    assert error.value.status_code == 403


def test_external_document_role_cannot_read_inventory_when_misconfigured(db):
    values = _seed_inventory(db)
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="star_headquarters_approver",
        scope_type="document",
        scope_id="approval-line:TEST-001",
    )

    with pytest.raises(InventoryReadError) as captured:
        inventory_summary(db, actor=actor)
    assert captured.value.code == "inventory_read_denied"
    assert captured.value.status_code == 403


def test_stable_balance_cursor_drift_fails_closed_as_projection_corruption(db):
    values = _seed_inventory(db)
    balance = db.get(StockBalance, values["account"].id)
    balance.ledger_cursor = 2
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )

    with pytest.raises(
        InventoryReadError,
        match="无法从不可变流水重建",
    ) as error:
        list_inventory_accounts(db, actor=actor, limit=100)
    assert error.value.code == "inventory_projection_integrity_invalid"
    assert error.value.status_code == 503


def test_ledger_head_ahead_of_latest_transaction_fails_closed(db):
    values = _seed_inventory(db)
    _post_opening(db, values)
    head = db.get(
        InventoryLedgerHead,
        uuid.UUID("40000000-0000-4000-8000-000000000001"),
    )
    head.next_cursor = 3
    db.flush()
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )

    with pytest.raises(InventoryReadError) as captured:
        inventory_summary(db, actor=actor)
    assert captured.value.code == "inventory_ledger_cursor_inconsistent"


def test_ledger_head_behind_latest_transaction_fails_closed(db):
    values = _seed_inventory(db)
    transaction = _post_opening(db, values)
    transaction.ledger_cursor = 2
    db.flush()
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )

    with pytest.raises(InventoryReadError) as captured:
        inventory_summary(db, actor=actor)
    assert captured.value.code == "inventory_ledger_cursor_inconsistent"


def test_positive_ledger_cursor_without_exact_transaction_fails_closed(db):
    values = _seed_inventory(db)
    head = db.get(
        InventoryLedgerHead,
        uuid.UUID("40000000-0000-4000-8000-000000000001"),
    )
    head.next_cursor = 2
    db.flush()
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )

    with pytest.raises(InventoryReadError) as captured:
        inventory_summary(db, actor=actor)
    assert captured.value.code == "inventory_ledger_cursor_inconsistent"


def test_ledger_cursor_gap_fails_closed_even_when_head_and_max_match(db):
    values = _seed_inventory(db)
    _post_opening(db, values)
    db.add(
        InventoryTransaction(
            id=uuid.uuid4(),
            transaction_no="INV-GAP-003",
            movement_type="inbound",
            source_document_type="test_gap",
            source_document_id="GAP-003",
            posting_key="test-gap:GAP-003",
            idempotency_key_hash="c" * 64,
            request_hash="d" * 64,
            status="posted",
            effective_at=NOW,
            posted_at=NOW + timedelta(seconds=1),
            ledger_cursor=3,
            actor_user_id=values["user"].id,
        )
    )
    head = db.get(
        InventoryLedgerHead,
        uuid.UUID("40000000-0000-4000-8000-000000000001"),
    )
    head.next_cursor = 4
    db.flush()
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )

    with pytest.raises(InventoryReadError) as captured:
        inventory_summary(db, actor=actor)
    assert captured.value.code == "inventory_ledger_cursor_inconsistent"


def test_formal_inventory_router_is_read_only_and_uses_new_namespace():
    routes = {
        route.path: set(route.methods or ())
        for route in app.routes
        if route.path.startswith("/api/v1/inventory/")
    }
    assert routes == {
        "/api/v1/inventory/summary": {"GET"},
        "/api/v1/inventory/personal/me": {"GET"},
        "/api/v1/inventory/personal/me/accounts/{stock_account_id}/serials": {"GET"},
        "/api/v1/inventory/personal/me/transactions": {"GET"},
        "/api/v1/inventory/accounts": {"GET"},
        "/api/v1/inventory/transactions/{transaction_id}": {"GET"},
    }
    scan_routes = {
        route.path: set(route.methods or ())
        for route in app.routes
        if route.path.startswith("/api/v1/scan")
    }
    assert scan_routes == {"/api/v1/scan/qr": {"GET"}}


def test_report_snapshot_refuses_unestablished_opening(db):
    values = _seed_inventory(db)
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(values["region"].id),
    )
    with pytest.raises(InventoryReadError) as missing_export:
        capture_inventory_report_snapshot(db, actor=actor)
    assert missing_export.value.code == "inventory_report_export_denied"
    actor = _with_report_export(actor)
    with pytest.raises(InventoryReadError) as captured:
        capture_inventory_report_snapshot(db, actor=actor)
    assert captured.value.code == "inventory_report_opening_not_established"


def test_report_snapshot_requires_export_and_inventory_on_same_operational_grant(db):
    values = _seed_inventory(db)
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="technician",
        scope_type="person",
        scope_id=str(values["person"].id),
    )
    separate_assignment = uuid.uuid4()
    actor = replace(actor, entitlements=actor.entitlements + (
        Entitlement(
            assignment_id=separate_assignment,
            role_code="provincial_manager",
            scope_type="organization",
            scope_id=str(values["region"].id),
            resource="report",
            action="export",
            field_code="",
            effect="allow",
        ),
    ))
    with pytest.raises(InventoryReadError) as denied:
        capture_inventory_report_snapshot(db, actor=actor)
    assert denied.value.code == "inventory_report_export_denied"


def test_report_snapshot_freezes_visible_accounts_and_cursor(db, monkeypatch):
    values = _seed_inventory(db)
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(values["region"].id),
    )
    actor = _with_report_export(actor)
    with pytest.raises(InventoryReadError) as stale:
        capture_inventory_report_snapshot(db, actor=actor, expected_cursor=1)
    assert stale.value.code == "inventory_report_cursor_changed"
    with pytest.raises(InventoryReadError) as narrower:
        capture_inventory_report_snapshot(
            db, actor=actor, expected_account_ids=(),
        )
    assert narrower.value.code == "inventory_report_scope_changed"

    # The synthetic fixture contains no approved opening. Replace only that
    # proof to exercise the rest of the real inventory reader and SQL scope.
    monkeypatch.setattr(
        "app.formal_services.inventory_query._validated_opening_evidence",
        lambda *args, **kwargs: SimpleNamespace(complete=True),
    )
    snapshot = capture_inventory_report_snapshot(db, actor=actor)
    assert snapshot.ledger_cursor == 0
    assert snapshot.account_ids == (values["account"].id,)
    assert snapshot.assignment_ids == (actor.assignments[0].assignment_id,)
    assert [(row.sku_code, row.quantity, row.serial_no) for row in snapshot.lines] == [
        ("SKU-001", "0.000", None)
    ]


def _with_report_export(actor: FormalPrincipal) -> FormalPrincipal:
    assignment = actor.assignments[0]
    return replace(actor, entitlements=actor.entitlements + (
        Entitlement(
            assignment_id=assignment.assignment_id,
            role_code=assignment.role_code,
            scope_type=assignment.scope_type,
            scope_id=assignment.scope_id,
            resource="report",
            action="export",
            field_code="",
            effect="allow",
        ),
    ))


def test_report_job_freezes_scope_and_replays_only_same_snapshot(db, monkeypatch):
    values = _seed_inventory(db)
    actor = _with_report_export(_principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(values["region"].id),
    ))
    # Existing inventory fixture has no formal login graph or approved opening.
    # Keep the real account SQL, projection, job FK and idempotency paths.
    monkeypatch.setattr(
        "app.formal_services.inventory_report_jobs.load_formal_principal",
        lambda *args, **kwargs: actor,
    )
    monkeypatch.setattr(
        "app.formal_services.inventory_query._validated_opening_evidence",
        lambda *args, **kwargs: SimpleNamespace(complete=True),
    )
    audits = []
    monkeypatch.setattr(
        "app.formal_services.inventory_report_jobs.append_audit_event",
        lambda *args, **kwargs: audits.append(kwargs),
    )

    job, replayed = create_inventory_report_job(
        db, actor=actor, idempotency_key="report-1", request_id="req-1",
    )
    assert not replayed
    assert job.status == "queued"
    assert job.parameters_jsonb == {"report": "inventory_balances", "filters": {}}
    assert job.export_scope_jsonb == {
        "version": 1,
        "account_ids": [str(values["account"].id)],
        "assignment_ids": [str(actor.assignments[0].assignment_id)],
    }
    assert job.export_ledger_cursor == 0
    assert len(audits) == 1
    same, replayed = create_inventory_report_job(
        db, actor=actor, idempotency_key="report-1", request_id="req-1-retry",
    )
    assert replayed and same.id == job.id and len(audits) == 1
    checked_actor, checked_snapshot = revalidate_inventory_report_job(db, job=job)
    assert checked_actor.user_id == actor.user_id
    assert checked_snapshot.account_ids == (values["account"].id,)

    stale_cursor_job = SimpleNamespace(**{
        name: getattr(job, name) for name in (
            "job_type", "parameters_jsonb", "parameters_hash", "export_scope_jsonb",
            "export_ledger_cursor", "export_authorization_version", "requested_by",
        )
    })
    stale_cursor_job.export_ledger_cursor = 1
    with pytest.raises(InventoryReadError) as cursor_changed:
        revalidate_inventory_report_job(db, job=stale_cursor_job)
    assert cursor_changed.value.code == "inventory_report_cursor_changed"

    malformed_job = SimpleNamespace(**vars(stale_cursor_job))
    malformed_job.export_ledger_cursor = 0
    malformed_job.export_scope_jsonb = {"version": 1, "account_ids": ["wrong"], "assignment_ids": []}
    with pytest.raises(InventoryReadError) as malformed:
        revalidate_inventory_report_job(db, job=malformed_job)
    assert malformed.value.code == "inventory_report_job_scope_invalid"

    claim = claim_inventory_report_job(db, job_id=job.id, request_id="claim-1")
    assert claim.status == "running" and claim.failure_code is None
    assert claim.snapshot is not None and claim.snapshot.account_ids == (values["account"].id,)
    with pytest.raises(InventoryReadError) as double_claim:
        claim_inventory_report_job(db, job_id=job.id, request_id="claim-1-retry")
    assert double_claim.value.code == "inventory_report_job_not_queued"

    stored = []
    heads = {}

    def put_report_object(*, storage_key, file_id, sha256, payload):
        stored.append((storage_key, file_id, sha256, len(payload)))
        head = StoredObjectHead(
            storage_key=storage_key,
            size_bytes=len(payload),
            mime_type=MIME_TYPE,
            metadata={"sha256": sha256, "file-id": file_id},
            etag=hashlib.md5(payload).hexdigest(),
        )
        heads[storage_key] = head
        return head

    storage = SimpleNamespace(
        provider_code="aliyun_oss_v2",
        put_report_object=put_report_object,
        head_object=lambda *, storage_key: heads[storage_key],
    )
    complete, replayed = finish_inventory_report_job(
        db, job_id=job.id, storage=storage, request_id="finish-1", allow_new_put=True,
    )
    assert not replayed and complete.status == "succeeded"
    assert len(stored) == 1 and complete.result_sha256 == stored[0][2]
    assert complete.result_size_bytes == stored[0][3]
    from app.foundation_models import FileObject
    file = db.get(FileObject, complete.result_file_id)
    assert file.status == "available"
    assert _validate_intent_metadata(file, allow_completed=True)["purpose"] == "inventory_report_export"
    recovered, replayed = finish_inventory_report_job(
        db, job_id=job.id, storage=storage, request_id="finish-1-retry", allow_new_put=False,
    )
    assert replayed and recovered.id == complete.id and len(stored) == 1

    stale_job, _ = create_inventory_report_job(
        db, actor=actor, idempotency_key="report-stale", request_id="req-stale-create",
    )

    values["user"].authorization_version = 2
    db.flush()
    stale_claim = claim_inventory_report_job(db, job_id=stale_job.id, request_id="claim-stale")
    assert stale_claim.status == "failed"
    assert stale_claim.failure_code == "inventory_report_actor_stale"
    assert stale_claim.snapshot is None
    assert stale_job.error_detail == "inventory_report_actor_stale"
    with pytest.raises(InventoryReadError) as stale:
        create_inventory_report_job(db, actor=actor, idempotency_key="report-1", request_id="req-stale")
    assert stale.value.code == "inventory_report_actor_stale"


def test_report_job_rejects_invalid_key_and_missing_export(db, monkeypatch):
    values = _seed_inventory(db)
    actor = _principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(values["region"].id),
    )
    with pytest.raises(InventoryReadError) as invalid:
        create_inventory_report_job(db, actor=actor, idempotency_key="bad key", request_id="req-1")
    assert invalid.value.code == "inventory_report_idempotency_invalid"
    monkeypatch.setattr(
        "app.formal_services.inventory_report_jobs.load_formal_principal",
        lambda *args, **kwargs: actor,
    )
    with pytest.raises(InventoryReadError) as denied:
        create_inventory_report_job(db, actor=actor, idempotency_key="report-2", request_id="req-2")
    assert denied.value.code == "inventory_report_export_denied"


def test_report_result_rejects_ledger_change_after_private_object_put(db, monkeypatch):
    values = _seed_inventory(db)
    actor = _with_report_export(_principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(values["region"].id),
    ))
    monkeypatch.setattr(
        "app.formal_services.inventory_report_jobs.load_formal_principal",
        lambda *args, **kwargs: actor,
    )
    monkeypatch.setattr(
        "app.formal_services.inventory_query._validated_opening_evidence",
        lambda *args, **kwargs: SimpleNamespace(complete=True),
    )
    monkeypatch.setattr(
        "app.formal_services.inventory_report_jobs.append_audit_event",
        lambda *args, **kwargs: None,
    )
    job, _ = create_inventory_report_job(
        db, actor=actor, idempotency_key="report-drift", request_id="req-drift",
    )
    claim_inventory_report_job(db, job_id=job.id, request_id="claim-drift")
    puts = []

    def change_ledger_during_put(*, storage_key, file_id, sha256, payload):
        puts.append(storage_key)
        db.scalar(select(InventoryLedgerHead)).next_cursor = 2
        return StoredObjectHead(
            storage_key=storage_key,
            size_bytes=len(payload),
            mime_type=MIME_TYPE,
            metadata={"sha256": sha256, "file-id": file_id},
            etag=hashlib.md5(payload).hexdigest(),
        )

    storage = SimpleNamespace(
        provider_code="aliyun_oss_v2",
        put_report_object=change_ledger_during_put,
    )
    result, replayed = finish_inventory_report_job(
        db, job_id=job.id, storage=storage, request_id="finish-drift", allow_new_put=True,
    )
    assert not replayed and result.status == "failed"
    assert result.result_file_id is None and len(puts) == 1
    from app.foundation_models import FileObject
    assert db.scalar(select(FileObject.id)) is None


def test_report_worker_claims_commits_and_recovers_exact_result(db, monkeypatch):
    values = _seed_inventory(db)
    actor = _with_report_export(_principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(values["region"].id),
    ))
    monkeypatch.setattr(
        "app.formal_services.inventory_report_jobs.load_formal_principal",
        lambda *args, **kwargs: actor,
    )
    monkeypatch.setattr(
        "app.formal_services.inventory_query._validated_opening_evidence",
        lambda *args, **kwargs: SimpleNamespace(complete=True),
    )
    monkeypatch.setattr(
        "app.formal_services.inventory_report_jobs.append_audit_event",
        lambda *args, **kwargs: None,
    )
    job, _ = create_inventory_report_job(
        db, actor=actor, idempotency_key="worker-job", request_id="worker-create",
    )
    job_id = job.id
    db.commit()
    heads = {}
    puts = []

    def put_report_object(*, storage_key, file_id, sha256, payload):
        puts.append(storage_key)
        head = StoredObjectHead(
            storage_key=storage_key,
            size_bytes=len(payload),
            mime_type=MIME_TYPE,
            metadata={"sha256": sha256, "file-id": file_id},
            etag=hashlib.md5(payload).hexdigest(),
        )
        heads[storage_key] = head
        return head

    storage = SimpleNamespace(
        provider_code="aliyun_oss_v2",
        put_report_object=put_report_object,
        head_object=lambda *, storage_key: heads[storage_key],
    )
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    result = process_one_inventory_report(factory, storage=storage)
    assert result.job_id == job_id and result.status == "succeeded" and not result.recovered
    assert len(puts) == 1
    replay = process_one_inventory_report(factory, storage=storage, job_id=job_id)
    assert replay.job_id == job_id and replay.status == "succeeded" and replay.recovered
    assert len(puts) == 1
    assert process_one_inventory_report(factory, storage=storage).status == "idle"


def test_report_worker_recovers_running_job_after_unknown_storage_result(db, monkeypatch):
    values = _seed_inventory(db)
    actor = _with_report_export(_principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(values["region"].id),
    ))
    monkeypatch.setattr(
        "app.formal_services.inventory_report_jobs.load_formal_principal",
        lambda *args, **kwargs: actor,
    )
    monkeypatch.setattr(
        "app.formal_services.inventory_query._validated_opening_evidence",
        lambda *args, **kwargs: SimpleNamespace(complete=True),
    )
    monkeypatch.setattr(
        "app.formal_services.inventory_report_jobs.append_audit_event",
        lambda *args, **kwargs: None,
    )
    job, _ = create_inventory_report_job(
        db, actor=actor, idempotency_key="worker-unknown", request_id="worker-unknown-create",
    )
    job_id = job.id
    db.commit()
    heads = {}
    put_calls = []

    def put_report_object(*, storage_key, file_id, sha256, payload):
        put_calls.append(storage_key)
        head = StoredObjectHead(
            storage_key=storage_key,
            size_bytes=len(payload),
            mime_type=MIME_TYPE,
            metadata={"sha256": sha256, "file-id": file_id},
            etag=hashlib.md5(payload).hexdigest(),
        )
        heads[storage_key] = head
        if len(put_calls) == 1:
            raise FileStorageError("HEAD result unknown")
        return head

    def head_report_object(*, storage_key):
        if storage_key not in heads:
            raise FileStorageError("HEAD object unavailable")
        return heads[storage_key]

    storage = SimpleNamespace(
        provider_code="aliyun_oss_v2",
        put_report_object=put_report_object,
        head_object=head_report_object,
    )
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    with pytest.raises(FileStorageError):
        process_one_inventory_report(factory, storage=storage)
    with factory() as check:
        pending = check.get(type(job), job_id)
        assert pending.status == "running" and pending.result_file_id is None
    key = put_calls[0]
    original_head = heads.pop(key)
    with pytest.raises(FileStorageError):
        process_one_inventory_report(factory, storage=storage)
    assert len(put_calls) == 1
    heads[key] = replace(original_head, etag="0" * 32)
    with pytest.raises(InventoryReadError) as mismatch:
        process_one_inventory_report(factory, storage=storage)
    assert mismatch.value.code == "inventory_report_result_unknown"
    assert len(put_calls) == 1
    heads[key] = original_head
    completed = process_one_inventory_report(factory, storage=storage)
    assert completed.job_id == job_id and completed.status == "succeeded"
    assert len(put_calls) == 1


def test_report_download_rechecks_authority_after_ledger_advances(db, monkeypatch):
    values = _seed_inventory(db)
    from app.foundation_models import AuditChainHead, AuditEvent
    db.add(AuditChainHead(id=uuid.uuid4(), stream_key="inventory", version=0))
    db.flush()
    actor = _with_report_export(_principal(
        user_id=values["user"].id,
        person_id=values["person"].id,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id=str(values["region"].id),
    ))
    monkeypatch.setattr(
        "app.formal_services.inventory_report_jobs.load_formal_principal",
        lambda *args, **kwargs: actor,
    )
    monkeypatch.setattr(
        "app.formal_services.inventory_report_download.load_formal_principal",
        lambda *args, **kwargs: actor,
    )
    monkeypatch.setattr(
        "app.formal_services.inventory_query._validated_opening_evidence",
        lambda *args, **kwargs: SimpleNamespace(complete=True),
    )
    monkeypatch.setattr(
        "app.formal_services.inventory_report_jobs.append_audit_event",
        lambda *args, **kwargs: None,
    )
    job, _ = create_inventory_report_job(
        db, actor=actor, idempotency_key="download-job", request_id="download-create",
    )
    job_id = job.id
    db.commit()
    heads = {}

    def put_report_object(*, storage_key, file_id, sha256, payload):
        head = StoredObjectHead(
            storage_key=storage_key,
            size_bytes=len(payload),
            mime_type=MIME_TYPE,
            metadata={"sha256": sha256, "file-id": file_id},
            etag=hashlib.md5(payload).hexdigest(),
        )
        heads[storage_key] = head
        return head

    storage = SimpleNamespace(
        provider_code="aliyun_oss_v2",
        put_report_object=put_report_object,
        head_object=lambda *, storage_key: heads[storage_key],
        create_download_intent=lambda *, storage_key, ttl_seconds: DownloadIntent(
            storage_key=storage_key,
            url="https://private.example.invalid/download?signature=synthetic",
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=60),
        ),
    )
    factory = sessionmaker(bind=db.get_bind(), expire_on_commit=False)
    assert process_one_inventory_report(factory, storage=storage).status == "succeeded"
    db.scalar(select(InventoryLedgerHead)).next_cursor = 2
    db.commit()
    with factory() as transaction:
        report_status = get_inventory_report_job_status(transaction, actor=actor, job_id=job_id)
    assert report_status.status == "succeeded" and report_status.file_available

    with factory() as transaction:
        signed = create_inventory_report_download_intent(
            transaction, actor=actor, job_id=job_id, storage=storage,
            request_id="download-intent-1", ttl_seconds=120,
        )
        transaction.commit()
    assert signed.download_count == 1 and signed.url.startswith("https://")
    with factory() as check:
        events = check.scalars(select(AuditEvent).where(
            AuditEvent.action == "inventory_report_download_intent",
        )).all()
        assert len(events) == 1 and events[0].after_jsonb["download_count"] == 1
        assert check.get(type(job), job_id).download_count == 1
    with factory() as transaction:
        transaction.get(FileObject, signed.file_id).status = "quarantined"
        transaction.commit()
    with factory() as transaction:
        assert not get_inventory_report_job_status(transaction, actor=actor, job_id=job_id).file_available
    with factory() as transaction:
        with pytest.raises(InventoryReadError) as replay:
            create_inventory_report_download_intent(
                transaction, actor=actor, job_id=job_id, storage=storage,
                request_id="download-intent-1", ttl_seconds=120,
            )
    assert replay.value.code == "inventory_report_download_request_replayed"
    values["user"].authorization_version = 2
    db.commit()
    with factory() as transaction:
        with pytest.raises(InventoryReadError) as revoked:
            create_inventory_report_download_intent(
                transaction, actor=actor, job_id=job_id, storage=storage,
                request_id="download-intent-2", ttl_seconds=120,
            )
    assert revoked.value.code == "inventory_report_actor_stale"
    with factory() as transaction:
        with pytest.raises(InventoryReadError) as status_denied:
            get_inventory_report_job_status(transaction, actor=actor, job_id=job_id)
    assert status_denied.value.code == "inventory_report_actor_stale"
