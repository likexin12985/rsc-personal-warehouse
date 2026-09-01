from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

import app.formal_services.stocktake_task as service
from app.database import Base
from app.formal_access import load_formal_principal
from app.formal_services.inventory_posting import INVENTORY_LEDGER_HEAD_ID
from app.formal_services.stocktake_task import (
    StocktakeTaskError,
    create_personal_stocktake_draft,
    create_stocktake_task_draft,
    start_stocktake_task,
)
from app.foundation_models import (
    AuditChainHead,
    AuditEvent,
    AuthIdentity,
    ExternalObject,
    Organization,
    Permission,
    Person,
    Role,
    RoleAssignment,
    RolePermission,
    SourceSystem,
    StateTransitionEvent,
)
from app.inventory_models import (
    FormalMaterial,
    InventoryLedgerHead,
    InventoryMovement,
    InventorySerial,
    InventoryTransaction,
    MaterialInventoryPolicy,
    SerialCurrentPosition,
    StockAccount,
    StockBalance,
    StockLocation,
    CustodyAssignment,
)
from app.models import User
from app.stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    StocktakeRound,
    StocktakeSnapshotLine,
)
from app.stocktake_task_schemas import (
    PersonalStocktakeCreateIn,
    StocktakeScopeSelectionIn,
    StocktakeTaskCreateIn,
    StocktakeTaskStartIn,
)


NOW = datetime(2026, 9, 1, 9, 0, tzinfo=timezone.utc)
SECRET = b"stocktake-test-hmac-secret-with-32-bytes-minimum"


@pytest.fixture(autouse=True)
def _fixed_database_time(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(service, "_database_now", lambda _db: NOW)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def world(db: Session) -> SimpleNamespace:
    hq = _organization(db, "HQ", "总部", "headquarters")
    region_x = _organization(db, "REG-X", "区域 X", "region_company", hq)
    region_y = _organization(db, "REG-Y", "区域 Y", "region_company", hq)
    department_x = _organization(db, "DEP-X", "区域 X 工程组", "department", region_x)

    roles = {
        code: Role(
            id=uuid.uuid4(),
            code=code,
            name=code,
            is_external=False,
            status="active",
        )
        for code in ("admin", "provincial_manager", "technician")
    }
    manage = Permission(
        id=uuid.uuid4(),
        resource="stocktake",
        action="manage",
        field_code="",
        description="manage",
    )
    count = Permission(
        id=uuid.uuid4(),
        resource="stocktake",
        action="count",
        field_code="",
        description="count",
    )
    db.add_all([*roles.values(), manage, count])
    db.flush()
    db.add_all(
        [
            RolePermission(
                role_id=roles[role_code].id,
                permission_id=permission.id,
                effect="allow",
            )
            for role_code, permissions in (
                ("admin", (manage, count)),
                ("provincial_manager", (manage, count)),
                ("technician", (count,)),
            )
            for permission in permissions
        ]
    )
    db.flush()

    admin = _user(db, hq, roles["admin"], "national", "*", "Admin")
    manager_x = _user(
        db,
        region_x,
        roles["provincial_manager"],
        "organization",
        str(region_x.id),
        "Manager-X",
    )
    manager_y = _user(
        db,
        region_y,
        roles["provincial_manager"],
        "organization",
        str(region_y.id),
        "Manager-Y",
    )
    technician = _user(
        db,
        department_x,
        roles["technician"],
        "person",
        None,
        "Technician-X",
    )

    source = SourceSystem(
        id=uuid.uuid4(),
        code="OAM",
        name="OAM read-only",
        mode="read_only",
        enabled=True,
        configuration_jsonb={},
    )
    db.add(source)
    db.flush()
    material_a = _material(db, source, "A", "none")
    material_b = _material(db, source, "B", "serial")

    region_location = StockLocation(
        id=uuid.uuid4(),
        code="REG-X-WH",
        name="区域 X 仓",
        location_type="region",
        owner_org_id=region_x.id,
        parent_id=None,
        custodian_person_id=manager_x.person.id,
        status="active",
    )
    personal_location = StockLocation(
        id=uuid.uuid4(),
        code="PERSONAL-X",
        name="工程师个人仓",
        location_type="personal",
        owner_org_id=region_x.id,
        parent_id=region_location.id,
        custodian_person_id=technician.person.id,
        status="active",
    )
    db.add(region_location)
    db.flush()
    db.add(personal_location)
    db.flush()
    db.add_all(
        [
            CustodyAssignment(
                id=uuid.uuid4(),
                location_id=region_location.id,
                custodian_person_id=manager_x.person.id,
                valid_from=NOW - timedelta(days=30),
                valid_to=None,
                handover_case_id=None,
            ),
            CustodyAssignment(
                id=uuid.uuid4(),
                location_id=personal_location.id,
                custodian_person_id=technician.person.id,
                valid_from=NOW - timedelta(days=30),
                valid_to=None,
                handover_case_id=None,
            ),
        ]
    )
    db.flush()

    region_new = _account(
        db,
        owner=region_x,
        location=region_location,
        material=material_a,
        custodian=None,
        condition="new",
        bucket="available",
        quantity="5.000",
        cursor=1,
    )
    region_used = _account(
        db,
        owner=region_x,
        location=region_location,
        material=material_a,
        custodian=None,
        condition="used",
        bucket="available",
        quantity="2.000",
        cursor=1,
    )
    region_serial = _account(
        db,
        owner=region_x,
        location=region_location,
        material=material_b,
        custodian=None,
        condition="new",
        bucket="available",
        quantity="1.000",
        cursor=1,
    )
    personal_account = _account(
        db,
        owner=region_x,
        location=personal_location,
        material=material_a,
        custodian=technician.person,
        condition="new",
        bucket="available",
        quantity="3.000",
        cursor=1,
    )

    transaction = InventoryTransaction(
        id=uuid.uuid4(),
        transaction_no="TX-BASE-1",
        movement_type="opening",
        source_document_type="test_fixture",
        source_document_id="base",
        posting_key="test-fixture-base",
        idempotency_key_hash="1" * 64,
        request_hash="2" * 64,
        status="posted",
        effective_at=NOW - timedelta(days=2),
        posted_at=NOW - timedelta(days=2),
        ledger_cursor=1,
        reversed_transaction_id=None,
        actor_user_id=admin.user.id,
    )
    movement = InventoryMovement(
        id=uuid.uuid4(),
        transaction_id=transaction.id,
        line_no=1,
        from_account_id=None,
        to_account_id=region_serial.id,
        external_boundary_code="TEST_BASE",
        quantity=Decimal("1.000"),
    )
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=material_b.id,
        serial_no="SN-001",
        qr_code="QR-SN-001",
        lot_id=None,
        lifecycle_status="active",
    )
    db.add(transaction)
    db.flush()
    db.add_all([movement, serial])
    db.flush()
    db.add(
        SerialCurrentPosition(
            serial_id=serial.id,
            stock_account_id=region_serial.id,
            last_movement_id=movement.id,
            updated_at=NOW - timedelta(days=2),
        )
    )
    db.add_all(
        [
            InventoryLedgerHead(
                id=INVENTORY_LEDGER_HEAD_ID,
                stream_key="inventory",
                next_cursor=2,
            ),
            AuditChainHead(
                id=uuid.uuid4(),
                stream_key="inventory",
                last_event_id=None,
                last_hash=None,
                version=0,
            ),
        ]
    )
    db.commit()
    principals = {
        key: load_formal_principal(db, value.user.id, now=NOW)
        for key, value in (
            ("admin", admin),
            ("manager_x", manager_x),
            ("manager_y", manager_y),
            ("technician", technician),
        )
    }
    return SimpleNamespace(
        db=db,
        hq=hq,
        region_x=region_x,
        region_y=region_y,
        manager_x=manager_x,
        manager_y=manager_y,
        technician=technician,
        principals=principals,
        material_a=material_a,
        material_b=material_b,
        region_location=region_location,
        personal_location=personal_location,
        region_new=region_new,
        region_used=region_used,
        region_serial=region_serial,
        personal_account=personal_account,
        serial=serial,
    )


def _organization(
    db: Session,
    code: str,
    name: str,
    org_type: str,
    parent: Organization | None = None,
) -> Organization:
    row = Organization(
        id=uuid.uuid4(),
        code=code,
        name=name,
        parent_id=parent.id if parent else None,
        org_type=org_type,
        province_code=None,
        status="active",
    )
    db.add(row)
    db.flush()
    return row


def _user(
    db: Session,
    organization: Organization,
    role: Role,
    scope_type: str,
    scope_id: str | None,
    name: str,
) -> SimpleNamespace:
    person = Person(
        id=uuid.uuid4(),
        organization_id=organization.id,
        employee_no=f"E-{uuid.uuid4().hex[:10]}",
        name=name,
        mobile_encrypted=None,
        mobile_hash=None,
        employment_status="active",
        source_updated_at=NOW,
    )
    user = User(
        id=str(uuid.uuid4()),
        person_id=person.id,
        account_status="active",
        authorization_version=1,
        mobile=f"1{uuid.uuid4().int % 10**10:010d}",
        name=name,
        password_hash="formal-password-disabled",
        role=role.code,
        province=None,
        is_active=True,
        require_password_change=False,
    )
    db.add(person)
    db.flush()
    db.add(user)
    db.flush()
    if role.code == "technician":
        scope_id = str(person.id)
    db.add_all(
        [
            RoleAssignment(
                id=uuid.uuid4(),
                user_id=user.id,
                role_id=role.id,
                scope_type=scope_type,
                scope_id=scope_id or "*",
                valid_from=NOW - timedelta(days=100),
                valid_to=None,
                status="active",
                assigned_by=user.id,
                revoked_at=None,
                revoked_by=None,
                reason="test",
            ),
            AuthIdentity(
                id=uuid.uuid4(),
                user_id=user.id,
                identity_type="mobile",
                provider_key="test",
                identifier_hash=uuid.uuid4().hex + uuid.uuid4().hex,
                hash_version=1,
                verified_at=NOW - timedelta(days=1),
                status="active",
                revoked_at=None,
            ),
        ]
    )
    db.flush()
    return SimpleNamespace(person=person, user=user)


def _material(
    db: Session,
    source: SourceSystem,
    suffix: str,
    tracking_mode: str,
) -> FormalMaterial:
    external = ExternalObject(
        id=uuid.uuid4(),
        source_system_id=source.id,
        entity_type="material",
        external_id=f"MAT-{suffix}",
        current_version_id=None,
        deleted_at=None,
    )
    material = FormalMaterial(
        id=uuid.uuid4(),
        external_object_id=external.id,
        sku_code=f"SKU-{suffix}",
        name=f"物料 {suffix}",
        specification="",
        base_unit="件",
        status="active",
        source_updated_at=NOW,
    )
    policy = MaterialInventoryPolicy(
        id=uuid.uuid4(),
        material_id=material.id,
        tracking_mode=tracking_mode,
        quantity_scale=0 if tracking_mode in {"serial", "lot_and_serial"} else 3,
        allow_fraction=tracking_mode not in {"serial", "lot_and_serial"},
        effective_from=NOW - timedelta(days=100),
        effective_to=None,
    )
    db.add(external)
    db.flush()
    db.add(material)
    db.flush()
    db.add(policy)
    db.flush()
    return material


def _account(
    db: Session,
    *,
    owner: Organization,
    location: StockLocation,
    material: FormalMaterial,
    custodian: Person | None,
    condition: str,
    bucket: str,
    quantity: str,
    cursor: int,
) -> StockAccount:
    row = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=owner.id,
        custodian_person_id=custodian.id if custodian else None,
        location_id=location.id,
        material_id=material.id,
        condition_code=condition,
        availability_bucket=bucket,
        lot_id=None,
    )
    db.add(row)
    db.flush()
    db.add(
        StockBalance(
            stock_account_id=row.id,
            quantity=Decimal(quantity),
            ledger_cursor=cursor,
            version=1,
        )
    )
    db.flush()
    return row


def _managed_draft(
    world: SimpleNamespace,
    *,
    task_type: str = "sample",
    material_id: uuid.UUID | None = None,
    condition_code: str | None = None,
    freeze_mode: str = "hard",
) -> StocktakeTaskCreateIn:
    filtered = material_id is not None or condition_code is not None
    return StocktakeTaskCreateIn(
        task_type=task_type,
        region_org_id=world.region_x.id,
        blind_count=True,
        scopes=(
            StocktakeScopeSelectionIn(
                owner_org_id=world.region_x.id,
                location_id=world.region_location.id,
                assignee_person_id=world.manager_x.person.id,
                scope_mode="filtered" if filtered else "location_all",
                material_id=material_id,
                condition_code=condition_code,
                freeze_mode=freeze_mode,
            ),
        ),
        deadline=NOW + timedelta(days=2),
        note="本地正式盘点测试",
    )


def _termination_draft(world: SimpleNamespace) -> StocktakeTaskCreateIn:
    return StocktakeTaskCreateIn(
        task_type="termination",
        region_org_id=world.region_x.id,
        blind_count=True,
        scopes=(
            StocktakeScopeSelectionIn(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_person_id=world.manager_x.person.id,
                scope_mode="location_all",
                freeze_mode="hard",
            ),
        ),
        deadline=NOW + timedelta(days=2),
        note="离职交接盘点",
    )


def _create_managed(
    world: SimpleNamespace,
    *,
    key: str,
    draft: StocktakeTaskCreateIn | None = None,
):
    return create_stocktake_task_draft(
        world.db,
        actor=world.principals["manager_x"],
        draft=draft or _managed_draft(world),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def _start(world: SimpleNamespace, task_id: uuid.UUID, *, key: str, expected: int = 0):
    return start_stocktake_task(
        world.db,
        actor=world.principals["manager_x"],
        task_id=task_id,
        command=StocktakeTaskStartIn(expected_version=expected),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def test_managed_create_is_exactly_replayable_and_mismatch_fails(world):
    created = _create_managed(world, key="create-1")
    replay = _create_managed(world, key="create-1")

    assert replay == replace(created, replayed=True)
    assert world.db.scalar(select(func.count()).select_from(FormalStocktakeTask)) == 1
    assert world.db.scalar(select(func.count()).select_from(FormalStocktakeScope)) == 1
    assert world.db.scalar(select(func.count()).select_from(InventoryFreeze)) == 0
    assert world.db.scalar(select(func.count()).select_from(StocktakeSnapshotLine)) == 0
    assert world.db.scalar(select(func.count()).select_from(StocktakeRound)) == 0

    changed = _managed_draft(world, material_id=world.material_a.id)
    with pytest.raises(StocktakeTaskError) as exc:
        _create_managed(world, key="create-1", draft=changed)
    assert exc.value.code == "stocktake_idempotency_conflict"


def test_manager_scope_and_latest_authorization_version_fail_closed(world):
    with pytest.raises(StocktakeTaskError) as exc:
        create_stocktake_task_draft(
            world.db,
            actor=world.principals["manager_y"],
            draft=_managed_draft(world),
            idempotency_key="wrong-region",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-wrong-region",
        )
    assert exc.value.code == "stocktake_manager_forbidden"

    stale = replace(
        world.principals["manager_x"],
        authorization_version=world.principals["manager_x"].authorization_version + 1,
    )
    with pytest.raises(StocktakeTaskError) as exc:
        create_stocktake_task_draft(
            world.db,
            actor=stale,
            draft=_managed_draft(world),
            idempotency_key="stale",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-stale",
        )
    assert exc.value.code == "stocktake_actor_principal_stale"


def test_personal_draft_derives_identity_region_location_and_custody(world):
    result = create_personal_stocktake_draft(
        world.db,
        actor=world.principals["technician"],
        draft=PersonalStocktakeCreateIn(
            blind_count=True,
            freeze_mode="cutoff_replay",
            note="本人自盘",
        ),
        idempotency_key="personal-1",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-personal-1",
    )
    task = world.db.get(FormalStocktakeTask, result.task_id)
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(FormalStocktakeScope.task_id == result.task_id)
    )

    assert task is not None and task.task_type == "personal"
    assert task.region_org_id == world.region_x.id
    assert scope is not None
    assert scope.location_id == world.personal_location.id
    assert scope.owner_org_id == world.region_x.id
    assert scope.assignee_user_id == world.technician.user.id
    assert scope.custodian_person_id_snapshot == world.technician.person.id


def test_termination_stocktake_covers_one_persons_complete_personal_warehouse(world):
    created = _create_managed(
        world,
        key="termination-create",
        draft=_termination_draft(world),
    )
    started = _start(world, created.task_id, key="termination-start")
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == created.task_id
        )
    )

    assert created.task_type == started.task_type == "termination"
    assert scope is not None
    assert scope.location_id == world.personal_location.id
    assert scope.custodian_person_id_snapshot == world.technician.person.id


def test_termination_stocktake_fails_if_an_active_personal_location_is_omitted(world):
    extra_location = StockLocation(
        id=uuid.uuid4(),
        code="PERSONAL-X-EXTRA",
        name="工程师备用个人仓",
        location_type="personal",
        owner_org_id=world.region_x.id,
        parent_id=world.region_location.id,
        custodian_person_id=world.technician.person.id,
        status="active",
    )
    world.db.add(extra_location)
    world.db.flush()
    world.db.add(
        CustodyAssignment(
            id=uuid.uuid4(),
            location_id=extra_location.id,
            custodian_person_id=world.technician.person.id,
            valid_from=NOW - timedelta(days=30),
            valid_to=None,
            handover_case_id=None,
        )
    )
    world.db.flush()

    with pytest.raises(StocktakeTaskError) as failure:
        _create_managed(
            world,
            key="termination-incomplete",
            draft=_termination_draft(world),
        )
    assert failure.value.code == "termination_scope_incomplete"


def test_atomic_start_records_three_states_snapshot_freeze_round_and_no_posting(world):
    created = _create_managed(
        world,
        key="atomic-create",
        draft=_managed_draft(
            world,
            material_id=world.material_a.id,
            condition_code="new",
        ),
    )
    tx_before = world.db.scalar(select(func.count()).select_from(InventoryTransaction))
    movement_before = world.db.scalar(select(func.count()).select_from(InventoryMovement))

    started = _start(world, created.task_id, key="atomic-start")
    task = world.db.get(FormalStocktakeTask, created.task_id)
    transitions = tuple(
        world.db.scalars(
            select(StateTransitionEvent)
            .where(StateTransitionEvent.aggregate_id == str(created.task_id))
            .order_by(StateTransitionEvent.created_at, StateTransitionEvent.id)
        ).all()
    )
    snapshots = tuple(
        world.db.scalars(
            select(StocktakeSnapshotLine).where(
                StocktakeSnapshotLine.task_id == created.task_id
            )
        ).all()
    )

    assert task is not None
    assert task.status == "counting"
    assert task.version == 1
    assert task.cutoff_ledger_cursor == 1
    assert task.issued_at.replace(tzinfo=timezone.utc) == NOW
    assert task.frozen_at.replace(tzinfo=timezone.utc) == NOW
    assert {(row.from_status, row.to_status) for row in transitions} == {
        (None, "draft"),
        ("draft", "issued"),
        ("issued", "frozen"),
        ("frozen", "counting"),
    }
    assert started.scope_count == started.active_freeze_count == 1
    assert len(snapshots) == 1
    assert snapshots[0].stock_account_id == world.region_new.id
    assert snapshots[0].book_qty == Decimal("5.000")
    assert world.db.scalar(select(func.count()).select_from(InventoryFreeze)) == 1
    assert world.db.scalar(select(func.count()).select_from(StocktakeRound)) == 1
    assert world.db.scalar(select(func.count()).select_from(InventoryTransaction)) == tx_before
    assert world.db.scalar(select(func.count()).select_from(InventoryMovement)) == movement_before
    assert world.db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.stream_key == "inventory",
            AuditEvent.aggregate_id == str(created.task_id),
        )
    ) == 2


def test_start_replay_is_exact_and_same_key_different_payload_conflicts(world):
    created = _create_managed(world, key="start-replay-create")
    started = _start(world, created.task_id, key="start-replay")
    replay = _start(world, created.task_id, key="start-replay")
    assert replay == replace(started, replayed=True)

    with pytest.raises(StocktakeTaskError) as exc:
        _start(world, created.task_id, key="start-replay", expected=1)
    assert exc.value.code == "stocktake_idempotency_conflict"


def test_overlapping_filtered_active_freeze_blocks_second_task(world):
    first = _create_managed(
        world,
        key="freeze-first-create",
        draft=_managed_draft(world, material_id=world.material_a.id),
    )
    _start(world, first.task_id, key="freeze-first-start")
    second = _create_managed(
        world,
        key="freeze-second-create",
        draft=_managed_draft(world, condition_code="new"),
    )

    with pytest.raises(StocktakeTaskError) as exc:
        _start(world, second.task_id, key="freeze-second-start")
    assert exc.value.code == "stocktake_scope_already_frozen"


def test_filtered_serial_snapshot_pins_only_matching_account_and_sn(world):
    created = _create_managed(
        world,
        key="serial-create",
        draft=_managed_draft(world, material_id=world.material_b.id),
    )
    started = _start(world, created.task_id, key="serial-start")
    rows = tuple(
        world.db.scalars(
            select(StocktakeSnapshotLine).where(
                StocktakeSnapshotLine.task_id == created.task_id
            )
        ).all()
    )

    assert started.snapshot_line_count == 1
    assert len(rows) == 1
    assert rows[0].stock_account_id == world.region_serial.id
    assert rows[0].book_qty == Decimal("1.000")
    assert rows[0].serial_count == 1
    assert rows[0].serial_snapshot_jsonb[0]["serial_id"] == str(world.serial.id)
    assert rows[0].serial_snapshot_jsonb[0]["ledger_cursor"] == 1


def test_personal_start_rechecks_account_custodian_and_does_not_write_ledger(world):
    created = create_personal_stocktake_draft(
        world.db,
        actor=world.principals["technician"],
        draft=PersonalStocktakeCreateIn(),
        idempotency_key="personal-start-create",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-personal-start-create",
    )
    world.personal_account.custodian_person_id = world.manager_x.person.id
    world.db.flush()
    tx_before = world.db.scalar(select(func.count()).select_from(InventoryTransaction))

    with pytest.raises(StocktakeTaskError) as exc:
        start_stocktake_task(
            world.db,
            actor=world.principals["technician"],
            task_id=created.task_id,
            command=StocktakeTaskStartIn(expected_version=0),
            idempotency_key="personal-start",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-personal-start",
        )
    assert exc.value.code == "personal_account_custodian_mismatch"
    assert world.db.scalar(select(func.count()).select_from(InventoryTransaction)) == tx_before
