from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import event, func, select, update
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy import create_engine

import app.formal_services.opening_stocktake_count as opening_count_service
import app.formal_services.opening_stocktake as opening_stocktake_service
from app.database import Base
from app.formal_access import FormalPrincipal, load_formal_principal
from app.formal_services.inventory_posting import (
    canonical_opening_count_manifest_sha256,
)
from app.formal_services.opening_stocktake import (
    INVENTORY_LEDGER_HEAD_ID,
    OPENING_CONTROL_ENTITY_TYPE,
    OpeningControlLineInput,
    OpeningStocktakeError,
    OpeningStocktakeScopeInput,
    StartOpeningStocktakeCommand,
    canonical_opening_manifest_sha256,
    opening_control_batch_body_sha256,
    opening_control_manifest_sha256,
    opening_control_projection_payload,
    start_opening_stocktake,
)
from app.formal_services.opening_stocktake_count import (
    OpeningPhysicalObservationInput,
    OpeningStocktakeCountError,
    SubmitOpeningStocktakeScopeCountCommand,
    submit_opening_stocktake_scope_count,
)
from app.foundation_models import (
    AuditChainHead,
    AuditEvent,
    AuthIdentity,
    ExternalObject,
    ExternalObjectVersion,
    Organization,
    OutboxEvent,
    Permission,
    Person,
    Role,
    RoleAssignment,
    RolePermission,
    SourceSystem,
    StateTransitionEvent,
    SyncBatch,
    SyncInboxEvent,
    SyncRun,
)
from app.inventory_models import (
    FormalMaterial,
    InventoryLot,
    InventoryLedgerHead,
    InventoryMovement,
    InventoryMovementSerial,
    InventorySerial,
    InventoryTransaction,
    MaterialInventoryPolicy,
    QrCode,
    SerialCurrentPosition,
    StockAccount,
    StockBalance,
    StockLocation,
    CustodyAssignment,
)
from app.models import User
from app.opening_stocktake_schemas import (
    OpeningPhysicalObservationIn,
    OpeningStocktakeCountIn,
)
from app.stocktake_models import (
    FormalStocktakeScope,
    FormalStocktakeTask,
    InventoryFreeze,
    InventoryOpeningEstablishment,
    StocktakeControlSnapshotLine,
    StocktakeCountLine,
    StocktakeCountObservation,
    StocktakeCountSerial,
    StocktakeDifference,
    StocktakeDifferenceSetCompletion,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)
LARGE_LEGAL_QUANTITY = Decimal("600000000000000.000")


@pytest.fixture(autouse=True)
def _fixed_opening_stocktake_database_now(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opening_stocktake_service, "_database_now", lambda _db: NOW)


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def world(db: Session) -> SimpleNamespace:
    hq = _organization(db, "HQ", "总部", "headquarters")
    region_x = _organization(db, "REG-X", "区域 X", "region_company", parent=hq)
    region_y = _organization(db, "REG-Y", "区域 Y", "region_company", parent=hq)
    source = SourceSystem(
        id=uuid.uuid4(),
        code="OAM",
        name="OAM 只读镜像",
        mode="read_only",
        enabled=True,
        configuration_jsonb={},
    )
    db.add(source)
    db.flush()

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

    admin = _user_with_role(db, hq, roles["admin"], "national", "*", "Admin")
    manager_x = _user_with_role(
        db,
        region_x,
        roles["provincial_manager"],
        "organization",
        str(region_x.id),
        "Manager-X",
    )
    manager_y = _user_with_role(
        db,
        region_y,
        roles["provincial_manager"],
        "organization",
        str(region_y.id),
        "Manager-Y",
    )
    technician = _user_with_role(
        db,
        region_x,
        roles["technician"],
        "person",
        None,
        "Technician-X",
    )

    material = _material(db, source, "none")
    region_location = StockLocation(
        id=uuid.uuid4(),
        code="REG-X-WH",
        name="区域 X 仓",
        location_type="region",
        owner_org_id=region_x.id,
        parent_id=None,
        custodian_person_id=None,
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
    custody = CustodyAssignment(
        id=uuid.uuid4(),
        location_id=personal_location.id,
        custodian_person_id=technician.person.id,
        valid_from=NOW - timedelta(days=10),
        valid_to=None,
        handover_case_id=None,
    )
    account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=region_x.id,
        custodian_person_id=None,
        location_id=region_location.id,
        material_id=material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
    )
    db.add_all([custody, account])
    db.flush()
    db.add(
        StockBalance(
            stock_account_id=account.id,
            quantity=Decimal("0.000"),
            ledger_cursor=0,
            version=1,
        )
    )
    db.add_all(
        [
            InventoryLedgerHead(
                id=INVENTORY_LEDGER_HEAD_ID,
                stream_key="inventory",
                next_cursor=1,
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
    db.flush()

    control = _install_control_sync(
        db,
        source=source,
        region=region_x,
        material=material,
        rows=(
            {
                "external_business_key": "CTRL-RESOLVED-1",
                "material_id": material.id,
                "condition_code": "new",
                "control_qty": Decimal("999.000"),
                "mapping_status": "resolved",
                "mapping_note": "",
            },
        ),
    )
    db.commit()
    principals = {
        name: load_formal_principal(db, entry.user.id, now=NOW)
        for name, entry in (
            ("admin", admin),
            ("manager_x", manager_x),
            ("manager_y", manager_y),
            ("technician", technician),
        )
    }
    command = StartOpeningStocktakeCommand(
        task_no="OPEN-X-001",
        region_org_id=region_x.id,
        control_source_system_id=source.id,
        control_sync_run_id=control.sync_run.id,
        control_sync_scope_key=control.sync_run.scope_key,
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=region_x.id,
                location_id=region_location.id,
                assignee_user_id=manager_x.user.id,
                freeze_mode="hard",
            ),
        ),
        control_lines=control.lines,
        blind_count=True,
        deadline=NOW + timedelta(days=2),
        note="本地专项测试",
    )
    return SimpleNamespace(
        db=db,
        hq=hq,
        region_x=region_x,
        region_y=region_y,
        source=source,
        roles=roles,
        admin=admin,
        manager_x=manager_x,
        manager_y=manager_y,
        technician=technician,
        principals=principals,
        material=material,
        region_location=region_location,
        personal_location=personal_location,
        custody=custody,
        account=account,
        control=control,
        command=command,
    )


def _organization(
    db: Session,
    code: str,
    name: str,
    org_type: str,
    *,
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


def _user_with_role(
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
    assignment = RoleAssignment(
        id=uuid.uuid4(),
        user_id=user.id,
        role_id=role.id,
        scope_type=scope_type,
        scope_id=scope_id or "*",
        valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        valid_to=None,
        status="active",
        assigned_by=user.id,
        revoked_at=None,
        revoked_by=None,
        reason="test",
    )
    identity = AuthIdentity(
        id=uuid.uuid4(),
        user_id=user.id,
        identity_type="mobile",
        provider_key="test",
        identifier_hash=uuid.uuid4().hex + uuid.uuid4().hex,
        hash_version=1,
        verified_at=NOW - timedelta(days=1),
        status="active",
        revoked_at=None,
    )
    db.add_all([assignment, identity])
    db.flush()
    return SimpleNamespace(person=person, user=user, assignment=assignment)


def _material(db: Session, source: SourceSystem, tracking_mode: str) -> FormalMaterial:
    external = ExternalObject(
        id=uuid.uuid4(),
        source_system_id=source.id,
        entity_type="material",
        external_id=f"MAT-{uuid.uuid4().hex}",
        current_version_id=None,
        deleted_at=None,
    )
    material = FormalMaterial(
        id=uuid.uuid4(),
        external_object_id=external.id,
        sku_code=f"SKU-{uuid.uuid4().hex[:10]}",
        name="测试物料",
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
        effective_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        effective_to=None,
    )
    db.add(external)
    db.flush()
    db.add(material)
    db.flush()
    db.add(policy)
    db.flush()
    return material


def _install_control_sync(
    db: Session,
    *,
    source: SourceSystem,
    region: Organization,
    material: FormalMaterial,
    rows: tuple[dict[str, object], ...],
) -> SimpleNamespace:
    sync_run_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    source_updated_at = NOW - timedelta(hours=2)
    line_inputs: list[OpeningControlLineInput] = []
    evidence: list[dict[str, object]] = []
    for index, spec in enumerate(sorted(rows, key=lambda row: str(row["external_business_key"])), start=1):
        object_id = uuid.uuid4()
        version_id = uuid.uuid4()
        event_id = uuid.uuid4()
        business_key = str(spec["external_business_key"])
        payload = opening_control_projection_payload(
            external_business_key=business_key,
            region_org_id=region.id,
            material_id=spec["material_id"],
            condition_code=spec["condition_code"],
            control_qty=spec["control_qty"],
            mapping_status=str(spec["mapping_status"]),
            mapping_note=str(spec["mapping_note"]),
        )
        payload_hash = canonical_opening_manifest_sha256(payload)
        line = OpeningControlLineInput(
            sync_inbox_event_id=event_id,
            external_object_version_id=version_id,
            external_business_key=business_key,
            material_id=spec["material_id"],
            condition_code=spec["condition_code"],
            control_qty=spec["control_qty"],
            mapping_status=str(spec["mapping_status"]),
            source_updated_at=source_updated_at,
            payload_sha256=payload_hash,
            mapping_note=str(spec["mapping_note"]),
        )
        line_inputs.append(line)
        evidence.append(
            {
                "object_id": object_id,
                "version_id": version_id,
                "event_id": event_id,
                "business_key": business_key,
                "payload": payload,
                "payload_hash": payload_hash,
                "source_version": f"v{index}",
            }
        )
    scope_key = f"oam_inventory_control:region:{region.id}"
    manifest = opening_control_manifest_sha256(
        source_system_id=source.id,
        sync_run_id=sync_run_id,
        sync_scope_key=scope_key,
        region_org_id=region.id,
        lines=line_inputs,
    )
    batch_hash = opening_control_batch_body_sha256(
        sequence=1,
        events=[
            {
                "event_sort_key": str(row["event_id"]),
                "external_event_id": f"event-{row['business_key']}",
                "external_id": row["business_key"],
                "payload_sha256": row["payload_hash"],
                "source_updated_at": source_updated_at.isoformat(
                    timespec="microseconds"
                ).replace("+00:00", "Z"),
                "source_version": row["source_version"],
            }
            for row in evidence
        ],
    )
    sync_run = SyncRun(
        id=sync_run_id,
        source_system_id=source.id,
        run_key=f"opening-{uuid.uuid4().hex}",
        scope_key=scope_key,
        mode="full",
        watermark_from=None,
        watermark_to=None,
        status="completed",
        manifest_sha256=manifest,
        started_at=NOW - timedelta(hours=3),
        completed_at=NOW - timedelta(hours=1),
        failure_code=None,
        failure_detail=None,
    )
    batch = SyncBatch(
        id=batch_id,
        run_id=sync_run.id,
        entity_type=OPENING_CONTROL_ENTITY_TYPE,
        sequence=1,
        record_count=len(evidence),
        body_sha256=batch_hash,
        status="applied",
        received_at=NOW - timedelta(hours=2),
        validated_at=NOW - timedelta(hours=1, minutes=30),
    )
    db.add(sync_run)
    db.flush()
    db.add(batch)
    db.flush()
    for row in evidence:
        external = ExternalObject(
            id=row["object_id"],
            source_system_id=source.id,
            entity_type=OPENING_CONTROL_ENTITY_TYPE,
            external_id=row["business_key"],
            current_version_id=row["version_id"],
            deleted_at=None,
        )
        db.add(external)
        db.flush()
        version = ExternalObjectVersion(
            id=row["version_id"],
            external_object_id=external.id,
            source_version=row["source_version"],
            source_updated_at=source_updated_at,
            valid_from=NOW - timedelta(hours=2),
            valid_to=None,
            payload_jsonb=row["payload"],
            payload_sha256=row["payload_hash"],
            is_current=True,
        )
        inbox = SyncInboxEvent(
            id=row["event_id"],
            batch_id=batch.id,
            source_system_id=source.id,
            external_event_id=f"event-{row['business_key']}",
            entity_type=OPENING_CONTROL_ENTITY_TYPE,
            external_id=row["business_key"],
            source_version=row["source_version"],
            source_updated_at=source_updated_at,
            payload_jsonb=row["payload"],
            payload_sha256=row["payload_hash"],
            status="applied",
            error_code=None,
            error_detail=None,
            processed_at=NOW - timedelta(hours=1),
        )
        db.add_all([version, inbox])
        db.flush()
    return SimpleNamespace(sync_run=sync_run, batch=batch, lines=tuple(line_inputs))


def _start(
    world: SimpleNamespace,
    *,
    actor: FormalPrincipal | None = None,
    command: StartOpeningStocktakeCommand | None = None,
    key: str = "opening-idempotency-0001",
):
    return start_opening_stocktake(
        world.db,
        actor=actor or world.principals["manager_x"],
        command=command or world.command,
        idempotency_key=key,
        request_id="opening-request-0001",
    )


def _error_code(callable_) -> str:
    with pytest.raises(OpeningStocktakeError) as captured:
        callable_()
    return captured.value.code


def test_start_builds_atomic_evidence_without_using_oam_quantity_as_stock(
    world: SimpleNamespace,
):
    db = world.db
    before_accounts = db.scalar(select(func.count()).select_from(StockAccount))
    before_balances = db.scalar(select(func.count()).select_from(StockBalance))

    result = _start(world)

    assert result.status == "counting"
    assert result.cutoff_ledger_cursor == 0
    assert result.scope_count == 1
    assert result.snapshot_line_count == 1
    assert result.control_line_count == 1
    assert db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor == 1
    task = db.get(FormalStocktakeTask, result.task_id)
    assert task.status == "counting"
    assert task.current_round_no == 1
    assert task.issued_at == task.frozen_at == task.cutoff_at
    assert db.get(StocktakeRound, result.initial_round_id).status == "counting"
    snapshot = db.scalar(
        select(StocktakeSnapshotLine).where(
            StocktakeSnapshotLine.task_id == result.task_id
        )
    )
    assert snapshot.book_qty == Decimal("0.000")
    assert snapshot.ledger_cursor == 0
    assert snapshot.serial_snapshot_jsonb == []
    control = db.scalar(
        select(StocktakeControlSnapshotLine).where(
            StocktakeControlSnapshotLine.task_id == result.task_id
        )
    )
    assert control.control_qty == Decimal("999.000")
    assert db.scalar(select(func.count()).select_from(StockAccount)) == before_accounts
    assert db.scalar(select(func.count()).select_from(StockBalance)) == before_balances
    transitions = db.scalars(
        select(StateTransitionEvent)
        .where(StateTransitionEvent.aggregate_id == str(result.task_id))
        .order_by(StateTransitionEvent.occurred_at, StateTransitionEvent.id)
    ).all()
    assert len(transitions) == 4
    assert {row.to_status for row in transitions} == {
        "draft",
        "issued",
        "frozen",
        "counting",
    }
    assert db.scalar(
        select(OutboxEvent).where(OutboxEvent.aggregate_id == str(result.task_id))
    ).event_type == "stocktake.opening.started"
    assert db.scalar(
        select(AuditEvent).where(AuditEvent.aggregate_id == str(result.task_id))
    ).action == "stocktake.opening.started"

    db.rollback()
    assert db.get(FormalStocktakeTask, result.task_id) is None
    assert db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor == 1


def test_start_flushes_all_sealed_evidence_before_initial_round(
    world: SimpleNamespace,
):
    flush_batches: list[set[str]] = []

    def _capture_flush(session: Session, _context, _instances) -> None:
        if session is world.db:
            flush_batches.append({type(row).__name__ for row in session.new})

    event.listen(world.db, "before_flush", _capture_flush)
    try:
        _start(world, key="opening-idempotency-flush-order")
    finally:
        event.remove(world.db, "before_flush", _capture_flush)

    required_before_round = {
        "FormalStocktakeScope",
        "StocktakeControlSnapshotLine",
        "InventoryFreeze",
        "StocktakeSnapshotLine",
    }
    round_index = next(
        index
        for index, batch in enumerate(flush_batches)
        if "StocktakeRound" in batch
    )
    for model_name in required_before_round:
        assert any(
            model_name in batch for batch in flush_batches[:round_index]
        ), model_name
    assert all(
        model_name not in flush_batches[round_index]
        for model_name in required_before_round
    )


def test_cutoff_uses_current_ledger_head_without_advancing_it(
    world: SimpleNamespace,
):
    db = world.db
    location_y = StockLocation(
        id=uuid.uuid4(),
        code="REG-Y-CUTOFF",
        name="区域 Y 截止游标仓",
        location_type="region",
        owner_org_id=world.region_y.id,
        parent_id=None,
        custodian_person_id=None,
        status="active",
    )
    account_y = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=world.region_y.id,
        custodian_person_id=None,
        location_id=location_y.id,
        material_id=world.material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
    )
    transaction = InventoryTransaction(
        id=uuid.uuid4(),
        transaction_no="TX-OTHER-SCOPE-CUTOFF",
        movement_type="inbound",
        source_document_type="test_seed",
        source_document_id="other-scope-cutoff",
        posting_key="other-scope-cutoff",
        idempotency_key_hash="3" * 64,
        request_hash="4" * 64,
        status="posted",
        effective_at=NOW - timedelta(days=1),
        posted_at=NOW - timedelta(days=1),
        ledger_cursor=1,
        reversed_transaction_id=None,
        actor_user_id=world.admin.user.id,
    )
    db.add(location_y)
    db.flush()
    db.add_all([account_y, transaction])
    db.flush()
    db.add_all(
        [
            StockBalance(
                stock_account_id=account_y.id,
                quantity=Decimal("1"),
                ledger_cursor=1,
                version=1,
            ),
            InventoryMovement(
                id=uuid.uuid4(),
                transaction_id=transaction.id,
                line_no=1,
                from_account_id=None,
                to_account_id=account_y.id,
                external_boundary_code="test-seed",
                quantity=Decimal("1"),
            ),
        ]
    )
    db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor = 2
    db.commit()

    result = _start(world, key="opening-idempotency-cutoff-cursor")

    snapshot = db.scalar(
        select(StocktakeSnapshotLine).where(
            StocktakeSnapshotLine.task_id == result.task_id
        )
    )
    assert result.cutoff_ledger_cursor == 1
    assert snapshot.ledger_cursor == 1
    assert db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor == 2


def test_zero_balance_scope_is_explicit_with_no_fake_snapshot_or_movement(
    world: SimpleNamespace,
):
    empty_location = StockLocation(
        id=uuid.uuid4(),
        code="EMPTY-X",
        name="空区域仓",
        location_type="region",
        owner_org_id=world.region_x.id,
        parent_id=None,
        custodian_person_id=None,
        status="active",
    )
    world.db.add(empty_location)
    world.db.commit()
    command = replace(
        world.command,
        task_no="OPEN-X-EMPTY",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=empty_location.id,
                assignee_user_id=world.manager_x.user.id,
                freeze_mode="cutoff_replay",
            ),
        ),
    )

    result = _start(world, command=command, key="opening-idempotency-empty")

    assert result.snapshot_line_count == 0
    assert world.db.scalar(
        select(StocktakeSnapshotLine.id).where(
            StocktakeSnapshotLine.task_id == result.task_id
        )
    ) is None
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(FormalStocktakeScope.task_id == result.task_id)
    )
    freeze = world.db.scalar(
        select(InventoryFreeze).where(InventoryFreeze.task_id == result.task_id)
    )
    assert scope.scope_mode == "location_all"
    assert freeze.freeze_mode == "cutoff_replay"


def test_existing_formal_serial_movement_blocks_opening_start(world: SimpleNamespace):
    db = world.db
    policy = db.scalar(
        select(MaterialInventoryPolicy).where(
            MaterialInventoryPolicy.material_id == world.material.id
        )
    )
    policy.tracking_mode = "serial"
    policy.quantity_scale = 0
    policy.allow_fraction = False
    balance = db.get(StockBalance, world.account.id)
    balance.quantity = Decimal("0")
    ledger_head = db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID)
    ledger_head.next_cursor = 4
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material.id,
        serial_no="SN-OPEN-001",
        qr_code="QR-OPEN-001",
        lot_id=None,
        lifecycle_status="active",
    )
    transaction = InventoryTransaction(
        id=uuid.uuid4(),
        transaction_no="TX-SEED-SN",
        movement_type="inbound",
        source_document_type="test_seed",
        source_document_id="seed-sn",
        posting_key="seed-sn",
        idempotency_key_hash="1" * 64,
        request_hash="2" * 64,
        status="posted",
        effective_at=NOW - timedelta(days=1),
        posted_at=NOW - timedelta(days=1),
        ledger_cursor=3,
        reversed_transaction_id=None,
        actor_user_id=world.manager_x.user.id,
    )
    db.add_all([serial, transaction])
    db.flush()
    movement = InventoryMovement(
        id=uuid.uuid4(),
        transaction_id=transaction.id,
        line_no=1,
        from_account_id=None,
        to_account_id=world.account.id,
        external_boundary_code="test-seed",
        quantity=Decimal("1"),
    )
    db.add(movement)
    db.flush()
    db.add_all(
        [
            InventoryMovementSerial(
                movement_id=movement.id,
                transaction_id=transaction.id,
                serial_id=serial.id,
            ),
            SerialCurrentPosition(
                serial_id=serial.id,
                stock_account_id=world.account.id,
                last_movement_id=movement.id,
                updated_at=NOW - timedelta(days=1),
            ),
        ]
    )
    db.commit()

    assert _error_code(
        lambda: _start(world, key="opening-idempotency-serial-history")
    ) == "opening_scope_formal_ledger_not_empty"
    db.rollback()
    assert db.scalar(select(func.count()).select_from(FormalStocktakeTask)) == 0


def test_empty_serial_policy_scope_gets_canonical_empty_sn_snapshot(
    world: SimpleNamespace,
):
    policy = world.db.scalar(
        select(MaterialInventoryPolicy).where(
            MaterialInventoryPolicy.material_id == world.material.id
        )
    )
    policy.tracking_mode = "serial"
    policy.quantity_scale = 0
    policy.allow_fraction = False
    world.db.commit()

    result = _start(world, key="opening-idempotency-serial-empty")
    snapshot = world.db.scalar(
        select(StocktakeSnapshotLine).where(
            StocktakeSnapshotLine.task_id == result.task_id
        )
    )
    assert snapshot.book_qty == Decimal("0")
    assert snapshot.serial_count == 0
    assert snapshot.serial_snapshot_jsonb == []
    assert len(snapshot.serial_snapshot_sha256) == 64


def test_nonzero_formal_balance_blocks_opening_and_does_not_advance_cursor(
    world: SimpleNamespace,
):
    balance = world.db.get(StockBalance, world.account.id)
    balance.quantity = Decimal("1")
    world.db.commit()

    assert _error_code(
        lambda: _start(world, key="opening-idempotency-nonzero-ledger")
    ) == "opening_scope_formal_ledger_not_empty"
    world.db.rollback()
    assert world.db.scalar(select(func.count()).select_from(FormalStocktakeTask)) == 0
    assert world.db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor == 1


def test_completed_zero_row_control_sync_is_valid_zero_total(
    world: SimpleNamespace,
):
    db = world.db
    control = _install_control_sync(
        db,
        source=world.source,
        region=world.region_x,
        material=world.material,
        rows=(),
    )
    db.commit()
    command = replace(
        world.command,
        task_no="OPEN-X-ZERO-CONTROL",
        control_sync_run_id=control.sync_run.id,
        control_sync_scope_key=control.sync_run.scope_key,
        control_lines=control.lines,
    )

    result = _start(world, command=command, key="opening-idempotency-zero-control")

    task = db.get(FormalStocktakeTask, result.task_id)
    assert result.control_line_count == 0
    assert task.control_manifest_sha256 == control.sync_run.manifest_sha256
    assert db.scalar(
        select(func.count())
        .select_from(StocktakeControlSnapshotLine)
        .where(StocktakeControlSnapshotLine.task_id == result.task_id)
    ) == 0
    assert db.get(StockBalance, world.account.id).quantity == Decimal("0")


def test_unresolved_control_line_is_preserved_only_as_control_evidence(
    world: SimpleNamespace,
):
    db = world.db
    control = _install_control_sync(
        db,
        source=world.source,
        region=world.region_x,
        material=world.material,
        rows=(
            {
                "external_business_key": "CTRL-UNRESOLVED",
                "material_id": None,
                "condition_code": None,
                "control_qty": Decimal("7"),
                "mapping_status": "unresolved",
                "mapping_note": "OAM 物料映射待总部核实",
            },
        ),
    )
    db.commit()
    command = replace(
        world.command,
        task_no="OPEN-X-UNRESOLVED",
        control_sync_run_id=control.sync_run.id,
        control_sync_scope_key=control.sync_run.scope_key,
        control_lines=control.lines,
    )
    before_accounts = db.scalar(select(func.count()).select_from(StockAccount))

    result = _start(world, command=command, key="opening-idempotency-unresolved")

    row = db.scalar(
        select(StocktakeControlSnapshotLine).where(
            StocktakeControlSnapshotLine.task_id == result.task_id
        )
    )
    assert row.mapping_status == "unresolved"
    assert row.material_id is None
    assert row.mapping_note == "OAM 物料映射待总部核实"
    assert db.scalar(select(func.count()).select_from(StockAccount)) == before_accounts


def test_technician_cannot_create_batch(world: SimpleNamespace):
    assert _error_code(
        lambda: _start(
            world,
            actor=world.principals["technician"],
            key="opening-idempotency-technician",
        )
    ) == "opening_manager_forbidden"


def test_stale_principal_is_rejected_after_authorization_version_changes(
    world: SimpleNamespace,
):
    stale = world.principals["manager_x"]
    world.manager_x.user.authorization_version += 1
    world.db.commit()
    assert _error_code(
        lambda: _start(world, actor=stale, key="opening-idempotency-stale")
    ) == "actor_principal_stale"


@pytest.mark.parametrize(
    ("statement", "sqlstate", "expected_code", "expected_status"),
    [
        (
            "UPDATE protected_table SET value = :secret",
            "P0001",
            "opening_stocktake_database_guard_rejected",
            412,
        ),
        (
            "SELECT secret_column FROM protected_table",
            "P0001",
            "opening_stocktake_database_unavailable",
            503,
        ),
        (
            "UPDATE protected_table SET value = :secret",
            "55000",
            "opening_stocktake_database_unavailable",
            503,
        ),
        (
            None,
            "08006",
            "opening_stocktake_database_unavailable",
            503,
        ),
    ],
)
def test_start_database_failures_are_classified_and_sanitized(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    statement: str | None,
    sqlstate: str,
    expected_code: str,
    expected_status: int,
):
    class _DatabaseFailure(Exception):
        pass

    original = _DatabaseFailure("server=secret-host password=secret")
    original.sqlstate = sqlstate

    def _raise_database_failure(*_args, **_kwargs):
        raise DBAPIError(
            statement,
            {"secret": "database detail"} if statement is not None else None,
            original,
            sqlstate == "08006",
        )

    monkeypatch.setattr(
        opening_stocktake_service,
        "_take_advisory_locks",
        _raise_database_failure,
    )
    with pytest.raises(OpeningStocktakeError) as captured:
        _start(world, key=f"opening-idempotency-dbapi-{sqlstate}-{expected_status}")
    assert captured.value.code == expected_code
    assert captured.value.http_status_code == expected_status
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert captured.value.__suppress_context__ is True
    public_detail = str(captured.value.as_detail()).lower()
    assert "secret" not in public_detail
    assert sqlstate.lower() not in public_detail


def test_start_integrity_failure_is_a_sanitized_conflict(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    def _raise_integrity(*_args, **_kwargs):
        raise IntegrityError(
            "INSERT INTO secret_table VALUES (:secret)",
            {"secret": "database detail"},
            Exception("sensitive unique constraint"),
        )

    monkeypatch.setattr(
        opening_stocktake_service,
        "_take_advisory_locks",
        _raise_integrity,
    )
    with pytest.raises(OpeningStocktakeError) as captured:
        _start(world, key="opening-idempotency-integrity-boundary")
    assert captured.value.code == "opening_stocktake_concurrent_conflict"
    assert captured.value.http_status_code == 409
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "secret" not in str(captured.value.as_detail()).lower()


def test_start_refreshes_cached_global_deny_before_authorizing(
    world: SimpleNamespace,
):
    role_permission = world.db.scalar(
        select(RolePermission)
        .join(Permission, Permission.id == RolePermission.permission_id)
        .where(
            RolePermission.role_id == world.roles["provincial_manager"].id,
            Permission.resource == "stocktake",
            Permission.action == "manage",
        )
    )
    assert role_permission is not None and role_permission.effect == "allow"
    world.db.execute(
        update(RolePermission)
        .where(RolePermission.id == role_permission.id)
        .values(effect="deny")
        .execution_options(synchronize_session=False)
    )
    assert role_permission.effect == "allow"

    assert _error_code(
        lambda: _start(
            world,
            key="opening-idempotency-refresh-global-deny",
        )
    ) == "opening_manager_forbidden"
    assert world.db.scalar(
        select(func.count()).select_from(FormalStocktakeTask)
    ) == 0


def test_start_rechecks_authorization_after_inventory_audit_head_lock(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    before_expiry = NOW + timedelta(hours=1)
    after_expiry = NOW + timedelta(hours=3)
    world.manager_x.assignment.valid_to = NOW + timedelta(hours=2)
    world.db.flush()
    clock = {"value": before_expiry}
    original_lock = opening_stocktake_service.lock_audit_chain_head

    def _delayed_audit_lock(*args, **kwargs):
        head = original_lock(*args, **kwargs)
        clock["value"] = after_expiry
        return head

    monkeypatch.setattr(
        opening_stocktake_service,
        "_database_now",
        lambda _db: clock["value"],
    )
    monkeypatch.setattr(
        opening_stocktake_service,
        "lock_audit_chain_head",
        _delayed_audit_lock,
    )

    with pytest.raises(OpeningStocktakeError) as captured:
        _start(world, key="opening-idempotency-audit-lock-expiry")
    assert captured.value.code in {
        "actor_not_current",
        "actor_principal_stale",
        "opening_manager_forbidden",
        "opening_assignment_not_current",
    }
    assert world.db.scalar(
        select(func.count()).select_from(FormalStocktakeTask)
    ) == 0


def test_cross_region_physical_location_is_rejected(world: SimpleNamespace):
    location_y = StockLocation(
        id=uuid.uuid4(),
        code="REG-Y-WH",
        name="区域 Y 仓",
        location_type="region",
        owner_org_id=world.region_y.id,
        parent_id=None,
        custodian_person_id=None,
        status="active",
    )
    world.db.add(location_y)
    world.db.commit()
    command = replace(
        world.command,
        task_no="OPEN-X-WRONG-LOCATION",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=location_y.id,
                assignee_user_id=world.manager_x.user.id,
            ),
        ),
    )
    assert _error_code(
        lambda: _start(world, command=command, key="opening-idempotency-location")
    ) == "stock_location_outside_region"


def test_personal_location_must_remain_a_leaf(world: SimpleNamespace):
    child = StockLocation(
        id=uuid.uuid4(),
        code="PERSONAL-CHILD",
        name="个人仓非法子库位",
        location_type="personal",
        owner_org_id=world.region_x.id,
        parent_id=world.personal_location.id,
        custodian_person_id=world.technician.person.id,
        status="active",
    )
    world.db.add(child)
    world.db.flush()
    command = replace(
        world.command,
        task_no="OPEN-X-PERSONAL-NOT-LEAF",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
            ),
        ),
    )

    assert _error_code(
        lambda: _start(
            world,
            command=command,
            key="opening-idempotency-personal-not-leaf",
        )
    ) == "personal_location_not_leaf"
    assert world.db.scalar(
        select(func.count()).select_from(FormalStocktakeTask)
    ) == 0


@pytest.mark.parametrize("parent_mode", ("inactive", "wrong_owner"))
def test_personal_location_requires_same_owner_region_parent(
    world: SimpleNamespace,
    parent_mode: str,
):
    if parent_mode == "inactive":
        world.region_location.status = "inactive"
    else:
        world.region_location.owner_org_id = world.region_y.id
    world.db.flush()
    command = replace(
        world.command,
        task_no=f"OPEN-X-PERSONAL-PARENT-{parent_mode.upper()}",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
            ),
        ),
    )

    assert _error_code(
        lambda: _start(
            world,
            command=command,
            key=f"opening-idempotency-personal-parent-{parent_mode}",
        )
    ) == "personal_location_parent_invalid"
    assert world.db.scalar(
        select(func.count()).select_from(FormalStocktakeTask)
    ) == 0


def test_personal_location_owner_must_be_an_active_region_company(
    world: SimpleNamespace,
):
    department = _organization(
        world.db,
        "REG-X-DEPT-PERSONAL",
        "区域 X 个人仓部门",
        "department",
        parent=world.region_x,
    )
    world.region_location.owner_org_id = department.id
    world.personal_location.owner_org_id = department.id
    world.db.flush()
    command = replace(
        world.command,
        task_no="OPEN-X-PERSONAL-OWNER-TYPE",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
            ),
        ),
    )

    assert _error_code(
        lambda: _start(
            world,
            command=command,
            key="opening-idempotency-personal-owner-type",
        )
    ) == "personal_location_owner_invalid"
    assert world.db.scalar(
        select(func.count()).select_from(FormalStocktakeTask)
    ) == 0


def test_manager_cannot_cross_owner_even_with_second_region_manage_grant(
    world: SimpleNamespace,
):
    world.db.add(
        RoleAssignment(
            id=uuid.uuid4(),
            user_id=world.manager_x.user.id,
            role_id=world.roles["provincial_manager"].id,
            scope_type="organization",
            scope_id=str(world.region_y.id),
            valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
            valid_to=None,
            status="active",
            assigned_by=world.admin.user.id,
            revoked_at=None,
            revoked_by=None,
            reason="prove cross-owner remains national-admin only",
        )
    )
    world.db.commit()
    manager = load_formal_principal(world.db, world.manager_x.user.id, now=NOW)
    command = replace(
        world.command,
        task_no="OPEN-X-OWNER-Y",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_y.id,
                location_id=world.region_location.id,
                assignee_user_id=world.manager_x.user.id,
            ),
        ),
    )
    assert _error_code(
        lambda: _start(
            world,
            actor=manager,
            command=command,
            key="opening-idempotency-owner-deny",
        )
    ) == "asset_owner_outside_region"


def test_national_admin_cannot_start_with_cross_region_asset_owner(
    world: SimpleNamespace,
):
    command = replace(
        world.command,
        task_no="OPEN-X-CROSS-OWNER-ADMIN-DENIED",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_y.id,
                location_id=world.region_location.id,
                assignee_user_id=world.admin.user.id,
            ),
        ),
    )
    assert _error_code(
        lambda: _start(
            world,
            actor=world.principals["admin"],
            command=command,
            key="opening-idempotency-cross-owner-admin-denied",
        )
    ) == "asset_owner_outside_region"
    assert world.db.scalar(
        select(func.count()).select_from(FormalStocktakeTask)
    ) == 0


def test_same_region_descendant_owner_starts_and_replay_reproves_tree(
    world: SimpleNamespace,
):
    descendant = _organization(
        world.db,
        "REG-X-SUB",
        "区域 X 子组织",
        "region_company",
        parent=world.region_x,
    )
    command = replace(
        world.command,
        task_no="OPEN-X-DESCENDANT-OWNER",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=descendant.id,
                location_id=world.region_location.id,
                assignee_user_id=world.manager_x.user.id,
            ),
        ),
    )
    key = "opening-idempotency-descendant-owner"
    started = _start(world, command=command, key=key)
    assert started.scope_count == 1
    assert _start(world, command=command, key=key).replayed is True

    descendant.parent_id = world.region_y.id
    world.db.flush()
    assert _error_code(
        lambda: _start(world, command=command, key=key)
    ) == "opening_idempotency_record_invalid"


@pytest.mark.parametrize("mode", ["wrong_assignee", "wrong_custody"])
def test_personal_scope_requires_exact_current_custodian_engineer(
    world: SimpleNamespace, mode: str
):
    assignee = world.technician.user.id
    if mode == "wrong_assignee":
        assignee = world.manager_x.user.id
    else:
        world.custody.custodian_person_id = world.manager_x.person.id
        world.db.commit()
    command = replace(
        world.command,
        task_no=f"OPEN-X-PERSONAL-{mode}",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=assignee,
            ),
        ),
    )
    expected = (
        "personal_assignee_not_custodian"
        if mode == "wrong_assignee"
        else "personal_custody_invalid"
    )
    assert _error_code(
        lambda: _start(
            world,
            command=command,
            key=f"opening-idempotency-personal-{mode}",
        )
    ) == expected


def test_personal_scope_accepts_current_custodian_with_own_count_permission(
    world: SimpleNamespace,
):
    command = replace(
        world.command,
        task_no="OPEN-X-PERSONAL-OK",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
            ),
        ),
    )
    result = _start(world, command=command, key="opening-idempotency-personal-ok")
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(FormalStocktakeScope.task_id == result.task_id)
    )
    assert scope.custodian_person_id_snapshot == world.technician.person.id


@pytest.mark.parametrize(
    ("mutation", "expected_code"),
    [
        ("unfinished", "control_sync_run_invalid"),
        ("wrong_source", "control_sync_run_invalid"),
        ("wrong_payload", "control_line_evidence_mismatch"),
    ],
)
def test_sync_evidence_must_be_completed_same_source_and_exact(
    world: SimpleNamespace, mutation: str, expected_code: str
):
    command = world.command
    if mutation == "unfinished":
        world.control.sync_run.status = "validating"
        world.control.sync_run.completed_at = None
    elif mutation == "wrong_source":
        other = SourceSystem(
            id=uuid.uuid4(),
            code="OTHER",
            name="其他只读源",
            mode="read_only",
            enabled=True,
            configuration_jsonb={},
        )
        world.db.add(other)
        world.db.flush()
        world.control.sync_run.source_system_id = other.id
    else:
        bad_line = replace(command.control_lines[0], control_qty=Decimal("998"))
        command = replace(command, control_lines=(bad_line,))
    world.db.commit()
    assert _error_code(
        lambda: _start(
            world,
            command=command,
            key=f"opening-idempotency-sync-{mutation}",
        )
    ) == expected_code


def test_same_key_same_request_is_read_only_replay_and_different_request_conflicts(
    world: SimpleNamespace,
):
    first = _start(world, key="opening-idempotency-replay")
    state_count = world.db.scalar(
        select(func.count()).select_from(StateTransitionEvent)
    )
    audit_count = world.db.scalar(select(func.count()).select_from(AuditEvent))
    outbox_count = world.db.scalar(select(func.count()).select_from(OutboxEvent))

    replay = _start(world, key="opening-idempotency-replay")

    assert replay.replayed is True
    assert replay.task_id == first.task_id
    assert replay.initial_round_id == first.initial_round_id
    assert world.db.scalar(
        select(func.count()).select_from(StateTransitionEvent)
    ) == state_count
    assert world.db.scalar(select(func.count()).select_from(AuditEvent)) == audit_count
    assert world.db.scalar(select(func.count()).select_from(OutboxEvent)) == outbox_count
    assert world.db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor == 1

    different = replace(world.command, note="different request")
    assert _error_code(
        lambda: _start(
            world,
            command=different,
            key="opening-idempotency-replay",
        )
    ) == "opening_idempotency_conflict"


def test_start_replay_shares_task_and_round_advisory_coordinates_with_count(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    key = "opening-idempotency-shared-replay-locks"
    first = _start(world, key=key)
    observed: list[tuple[int, ...]] = []
    original = opening_stocktake_service._take_advisory_locks

    def _capture(db: Session, coordinates: tuple[int, ...]) -> None:
        observed.append(coordinates)
        original(db, coordinates)

    monkeypatch.setattr(
        opening_stocktake_service,
        "_take_advisory_locks",
        _capture,
    )
    replay = _start(world, key=key)
    expected = {
        opening_stocktake_service._advisory_coordinate(
            "opening-count-task", str(first.task_id)
        ),
        opening_stocktake_service._advisory_coordinate(
            "opening-count-round", str(first.initial_round_id)
        ),
    }
    assert replay.replayed is True
    assert any(set(coordinates) == expected for coordinates in observed)


def test_replay_uses_version_valid_at_control_snapshot_after_later_oam_change(
    world: SimpleNamespace,
):
    first = _start(world, key="opening-idempotency-historical-control")
    original_version = world.db.get(
        ExternalObjectVersion,
        world.command.control_lines[0].external_object_version_id,
    )
    external_object = world.db.get(
        ExternalObject,
        original_version.external_object_id,
    )
    later_valid_from = NOW - timedelta(minutes=30)
    original_version.is_current = False
    original_version.valid_to = later_valid_from
    world.db.flush()

    later_payload = {"schema": "later-oam-control-version", "quantity": "1001.000"}
    later_version = ExternalObjectVersion(
        id=uuid.uuid4(),
        external_object_id=external_object.id,
        source_version="v-later",
        source_updated_at=later_valid_from,
        valid_from=later_valid_from,
        valid_to=None,
        payload_jsonb=later_payload,
        payload_sha256=canonical_opening_manifest_sha256(later_payload),
        is_current=True,
    )
    external_object.current_version_id = later_version.id
    external_object.deleted_at = NOW - timedelta(minutes=15)
    world.db.add(later_version)
    world.db.flush()

    replay = _start(world, key="opening-idempotency-historical-control")

    assert replay.replayed is True
    assert replay.task_id == first.task_id
    assert replay.initial_round_id == first.initial_round_id


def test_first_start_still_requires_the_exact_current_control_version(
    world: SimpleNamespace,
):
    original_version = world.db.get(
        ExternalObjectVersion,
        world.command.control_lines[0].external_object_version_id,
    )
    original_version.is_current = False
    original_version.valid_to = NOW - timedelta(minutes=30)
    world.db.flush()

    assert _error_code(
        lambda: _start(world, key="opening-idempotency-noncurrent-control")
    ) == "control_line_evidence_mismatch"


@pytest.mark.parametrize(
    ("dimension", "expected_code"),
    [
        ("owner", "asset_owner_invalid"),
        ("location_hierarchy", "stock_location_outside_region"),
        ("custody", "personal_custody_invalid"),
    ],
)
def test_start_reloads_locked_scope_dimensions_before_authorization(
    world: SimpleNamespace,
    dimension: str,
    expected_code: str,
):
    command = world.command
    actor = world.principals["manager_x"]
    if dimension == "owner":
        command = replace(
            command,
            task_no="OPEN-X-STALE-OWNER",
            scopes=(
                OpeningStocktakeScopeInput(
                    owner_org_id=world.region_y.id,
                    location_id=world.region_location.id,
                    assignee_user_id=world.admin.user.id,
                ),
            ),
        )
        actor = world.principals["admin"]
        world.db.execute(
            update(Organization)
            .where(Organization.id == world.region_y.id)
            .values(status="inactive")
            .execution_options(synchronize_session=False)
        )
        assert world.region_y.status == "active"
    elif dimension == "location_hierarchy":
        world.db.execute(
            update(StockLocation)
            .where(StockLocation.id == world.region_location.id)
            .values(owner_org_id=world.region_y.id)
            .execution_options(synchronize_session=False)
        )
        assert world.region_location.owner_org_id == world.region_x.id
    else:
        command = replace(
            command,
            task_no="OPEN-X-STALE-CUSTODY",
            scopes=(
                OpeningStocktakeScopeInput(
                    owner_org_id=world.region_x.id,
                    location_id=world.personal_location.id,
                    assignee_user_id=world.technician.user.id,
                ),
            ),
        )
        world.db.execute(
            update(CustodyAssignment)
            .where(CustodyAssignment.id == world.custody.id)
            .values(custodian_person_id=world.manager_x.person.id)
            .execution_options(synchronize_session=False)
        )
        assert world.custody.custodian_person_id == world.technician.person.id

    assert _error_code(
        lambda: _start(
            world,
            actor=actor,
            command=command,
            key=f"opening-idempotency-stale-{dimension}",
        )
    ) == expected_code


def test_replay_fails_closed_if_initial_freeze_evidence_was_mutated(
    world: SimpleNamespace,
):
    first = _start(world, key="opening-idempotency-freeze-integrity")
    freeze = world.db.scalar(
        select(InventoryFreeze).where(InventoryFreeze.task_id == first.task_id)
    )
    freeze.status = "released"
    freeze.valid_to = NOW + timedelta(minutes=1)
    freeze.released_by_user_id = world.manager_x.user.id
    freeze.release_reason = "tampered"
    world.db.flush()

    assert _error_code(
        lambda: _start(world, key="opening-idempotency-freeze-integrity")
    ) == "opening_idempotency_record_invalid"


@pytest.mark.parametrize(
    "tamper",
    [
        "state_missing",
        "state_extra",
        "state_payload",
        "outbox_missing",
        "outbox_extra",
        "outbox_payload",
        "audit_extra",
        "audit_payload",
        "audit_chain_head",
    ],
)
def test_start_replay_rejects_incomplete_or_tampered_event_graph(
    world: SimpleNamespace,
    tamper: str,
):
    key = f"opening-idempotency-start-graph-{tamper}"
    first = _start(world, key=key)
    task_id = str(first.task_id)
    state = world.db.scalar(
        select(StateTransitionEvent).where(
            StateTransitionEvent.aggregate_type == "stocktake_task",
            StateTransitionEvent.aggregate_id == task_id,
        )
    )
    outbox = world.db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "stocktake_task",
            OutboxEvent.aggregate_id == task_id,
        )
    )
    audit = world.db.scalar(
        select(AuditEvent).where(
            AuditEvent.aggregate_type == "stocktake_task",
            AuditEvent.aggregate_id == task_id,
        )
    )
    assert state is not None and outbox is not None and audit is not None

    if tamper == "state_missing":
        world.db.delete(state)
    elif tamper == "state_extra":
        world.db.add(
            StateTransitionEvent(
                aggregate_type="stocktake_task",
                aggregate_id=task_id,
                from_status="counting",
                to_status="submitted",
                reason="opening_stocktake_created",
                actor_id=world.manager_x.user.id,
                idempotency_key=f"opening-state-extra-{uuid.uuid4().hex}",
                occurred_at=NOW,
                metadata_jsonb={},
                created_at=NOW,
            )
        )
    elif tamper == "state_payload":
        state.metadata_jsonb = {**state.metadata_jsonb, "tampered": True}
    elif tamper == "outbox_missing":
        world.db.delete(outbox)
    elif tamper == "outbox_extra":
        world.db.add(
            OutboxEvent(
                event_type="stocktake.opening.started",
                aggregate_type="stocktake_task",
                aggregate_id=task_id,
                payload_jsonb=outbox.payload_jsonb,
                status="pending",
                attempts=0,
                idempotency_key=f"opening-outbox-extra-{uuid.uuid4().hex}",
                available_at=NOW,
                locked_at=None,
                locked_by=None,
                published_at=None,
                last_error=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    elif tamper == "outbox_payload":
        outbox.payload_jsonb = {**outbox.payload_jsonb, "tampered": True}
    elif tamper == "audit_extra":
        world.db.add(
            AuditEvent(
                stream_key="inventory",
                stream_version=audit.stream_version + 1,
                actor_user_id=world.manager_x.user.id,
                action="stocktake.opening.started",
                aggregate_type="stocktake_task",
                aggregate_id=task_id,
                before_jsonb=None,
                after_jsonb=audit.after_jsonb,
                request_id="opening-request-extra-audit",
                previous_hash=None,
                event_hash="f" * 64,
                occurred_at=NOW,
                created_at=NOW,
            )
        )
    elif tamper == "audit_payload":
        audit.after_jsonb = {**audit.after_jsonb, "tampered": True}
    else:
        head = world.db.scalar(
            select(AuditChainHead).where(AuditChainHead.stream_key == "inventory")
        )
        assert head is not None
        head.last_hash = "f" * 64
    world.db.flush()

    assert _error_code(lambda: _start(world, key=key)) == (
        "opening_idempotency_record_invalid"
    )


def test_existing_active_region_opening_and_active_scope_freeze_are_rejected(
    world: SimpleNamespace,
):
    first = _start(world, key="opening-idempotency-active-first")
    world.db.commit()
    second = replace(world.command, task_no="OPEN-X-SECOND")
    assert _error_code(
        lambda: _start(
            world,
            command=second,
            key="opening-idempotency-active-second",
        )
    ) == "opening_region_active_task_exists"
    world.db.rollback()

    task = world.db.get(FormalStocktakeTask, first.task_id)
    task.status = "closed"
    world.db.commit()
    assert _error_code(
        lambda: _start(
            world,
            command=second,
            key="opening-idempotency-active-freeze",
        )
    ) == "opening_scope_already_frozen"


def _scope_count_command(
    world: SimpleNamespace,
    started,
    *,
    scope_id: uuid.UUID | None = None,
    observations: tuple[OpeningPhysicalObservationInput, ...] = (),
    zero_confirmed: bool = False,
) -> SubmitOpeningStocktakeScopeCountCommand:
    statement = select(FormalStocktakeScope).where(
        FormalStocktakeScope.task_id == started.task_id
    )
    if scope_id is not None:
        statement = statement.where(FormalStocktakeScope.id == scope_id)
    scope = world.db.scalar(statement)
    assert scope is not None
    return SubmitOpeningStocktakeScopeCountCommand(
        task_id=started.task_id,
        round_id=started.initial_round_id,
        scope_id=scope.id,
        physical_observations=observations,
        zero_confirmed=zero_confirmed,
    )


def _submit_scope_count(
    world: SimpleNamespace,
    *,
    actor: FormalPrincipal,
    command: SubmitOpeningStocktakeScopeCountCommand,
    key: str,
):
    return submit_opening_stocktake_scope_count(
        world.db,
        actor=actor,
        command=command,
        idempotency_key=key,
        request_id="opening-count-request-0001",
    )


def _count_error_code(callable_) -> str:
    with pytest.raises(OpeningStocktakeCountError) as captured:
        callable_()
    return captured.value.code


def test_initial_scope_count_seals_round_without_creating_inventory_facts(
    world: SimpleNamespace,
):
    db = world.db
    started = _start(world, key="opening-idempotency-count-success")
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("2.000"),
                count_method="manual",
            ),
        ),
    )
    before = {
        model: db.scalar(select(func.count()).select_from(model))
        for model in (
            StockAccount,
            StockBalance,
            StockLocation,
            InventorySerial,
            InventoryTransaction,
            InventoryMovement,
            InventoryOpeningEstablishment,
        )
    }

    result = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key="opening-count-idempotency-success",
    )

    assert result.scope_completed is True
    assert result.round_sealed is True
    assert result.task_status == "submitted"
    assert result.round_status == "submitted"
    assert result.has_pending_verification is False
    line = db.scalar(
        select(StocktakeCountLine).where(
            StocktakeCountLine.round_id == started.initial_round_id
        )
    )
    assert line is not None
    assert line.stock_account_id == world.account.id
    assert line.counted_qty == Decimal("2.000")
    completion = db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None
    expected_request = opening_count_service._request_document(
        world.principals["manager_x"],
        command,
    )
    assert completion.request_jsonb == expected_request
    assert completion.request_sha256 == opening_count_service._hash_document(
        expected_request
    )
    assert expected_request["physical_observations"][0]["material_id"] is None
    assert db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion)
    ) == 1
    assert db.scalar(
        select(func.count()).select_from(StocktakeRoundSubmission)
    ) == 1
    assert db.scalar(
        select(func.count()).select_from(StocktakeDifferenceSetCompletion)
    ) == 1
    task_row = db.get(FormalStocktakeTask, started.task_id)
    round_row = db.get(StocktakeRound, started.initial_round_id)
    count_lines = db.scalars(
        select(StocktakeCountLine).where(
            StocktakeCountLine.round_id == started.initial_round_id
        )
    ).all()
    count_serials = db.scalars(
        select(StocktakeCountSerial).where(
            StocktakeCountSerial.round_id == started.initial_round_id
        )
    ).all()
    assert task_row is not None
    assert round_row is not None
    assert round_row.count_manifest_sha256 == (
        canonical_opening_count_manifest_sha256(
            task_row,
            round_row,
            count_lines,
            count_serials,
        )
    )
    submission = db.scalar(select(StocktakeRoundSubmission))
    assert submission is not None
    assert submission.count_manifest_sha256 == round_row.count_manifest_sha256
    assert submission.round_manifest_sha256 != submission.count_manifest_sha256
    differences = db.scalars(
        select(StocktakeDifference).order_by(StocktakeDifference.difference_no)
    ).all()
    assert [row.difference_type for row in differences] == [
        "excess",
        "control_unassigned",
    ]
    assert differences[0].observed_account_id == world.account.id
    difference_completion = db.scalar(
        select(StocktakeDifferenceSetCompletion)
    )
    assert difference_completion is not None
    assert difference_completion.round_submission_id == submission.id
    assert difference_completion.difference_count == 2
    assert difference_completion.physical_difference_count == 1
    assert difference_completion.control_difference_count == 1
    assert difference_completion.pending_observation_difference_count == 0
    summary = opening_count_service._difference_set_summary(
        task=task_row,
        round_row=round_row,
        submission=submission,
        differences=differences,
    )
    assert (
        difference_completion.difference_manifest_sha256
        == summary["difference_manifest_sha256"]
    )
    assert (
        difference_completion.request_sha256
        == opening_count_service._difference_set_request_sha256(summary)
    )
    assert all(
        db.scalar(select(func.count()).select_from(model)) == count
        for model, count in before.items()
    )


def test_scope_aggregate_overflow_rolls_back_every_count_fact(
    world: SimpleNamespace,
):
    started = _start(world, key="opening-idempotency-scope-total-overflow")
    world.db.commit()
    task_before = world.db.get(FormalStocktakeTask, started.task_id)
    assert task_before is not None
    version_before = task_before.version
    command = _scope_count_command(
        world,
        started,
        observations=tuple(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code=condition,
                availability_bucket="available",
                counted_qty=LARGE_LEGAL_QUANTITY,
            )
            for condition in ("new", "used")
        ),
    )

    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key="opening-count-scope-total-overflow",
        )
    ) == "opening_count_aggregate_quantity_invalid"
    world.db.rollback()

    task = world.db.get(FormalStocktakeTask, started.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and task.status == "counting"
    assert task.version == version_before
    assert round_row is not None and round_row.status == "counting"
    assert all(
        world.db.scalar(select(func.count()).select_from(model)) == 0
        for model in (
            StocktakeCountLine,
            StocktakeCountObservation,
            StocktakeScopeCountCompletion,
            StocktakeRoundSubmission,
            StocktakeDifference,
            StocktakeDifferenceSetCompletion,
            InventoryTransaction,
            InventoryOpeningEstablishment,
        )
    )


def test_round_aggregate_overflow_rolls_back_all_scope_completions(
    world: SimpleNamespace,
):
    command = replace(
        world.command,
        task_no="OPEN-X-ROUND-TOTAL-OVERFLOW",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.region_location.id,
                assignee_user_id=world.manager_x.user.id,
                freeze_mode="hard",
            ),
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        command=command,
        key="opening-idempotency-round-total-overflow",
    )
    world.db.commit()
    scopes = {
        row.location_id: row
        for row in world.db.scalars(
            select(FormalStocktakeScope).where(
                FormalStocktakeScope.task_id == started.task_id
            )
        ).all()
    }
    task_before = world.db.get(FormalStocktakeTask, started.task_id)
    assert task_before is not None
    version_before = task_before.version

    first = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=_scope_count_command(
            world,
            started,
            scope_id=scopes[world.region_location.id].id,
            observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw=world.material.sku_code,
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=LARGE_LEGAL_QUANTITY,
                ),
            ),
        ),
        key="opening-count-round-overflow-first-scope",
    )
    assert first.scope_completed is True and first.round_sealed is False
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["technician"],
            command=_scope_count_command(
                world,
                started,
                scope_id=scopes[world.personal_location.id].id,
                observations=(
                    OpeningPhysicalObservationInput(
                        material_identifier_raw=world.material.sku_code,
                        material_identifier_type="sku_code",
                        condition_code="new",
                        availability_bucket="available",
                        counted_qty=LARGE_LEGAL_QUANTITY,
                    ),
                ),
            ),
            key="opening-count-round-overflow-final-scope",
        )
    ) == "opening_count_aggregate_quantity_invalid"
    world.db.rollback()

    task = world.db.get(FormalStocktakeTask, started.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and task.status == "counting"
    assert task.version == version_before
    assert round_row is not None and round_row.status == "counting"
    assert all(
        world.db.scalar(select(func.count()).select_from(model)) == 0
        for model in (
            StocktakeCountLine,
            StocktakeCountObservation,
            StocktakeScopeCountCompletion,
            StocktakeRoundSubmission,
            StocktakeDifference,
            StocktakeDifferenceSetCompletion,
            InventoryTransaction,
            InventoryOpeningEstablishment,
        )
    )


def test_difference_aggregate_overflow_rolls_back_round_submission(
    world: SimpleNamespace,
):
    control = _install_control_sync(
        world.db,
        source=world.source,
        region=world.region_x,
        material=world.material,
        rows=(
            {
                "external_business_key": "CTRL-UNRESOLVED-LARGE",
                "material_id": None,
                "condition_code": None,
                "control_qty": LARGE_LEGAL_QUANTITY,
                "mapping_status": "unresolved",
                "mapping_note": "待核实的大额 OAM 控制行",
            },
        ),
    )
    command = replace(
        world.command,
        task_no="OPEN-X-DIFFERENCE-TOTAL-OVERFLOW",
        control_sync_run_id=control.sync_run.id,
        control_sync_scope_key=control.sync_run.scope_key,
        control_lines=control.lines,
    )
    started = _start(
        world,
        command=command,
        key="opening-idempotency-difference-total-overflow",
    )
    world.db.commit()
    task_before = world.db.get(FormalStocktakeTask, started.task_id)
    assert task_before is not None
    version_before = task_before.version

    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=_scope_count_command(
                world,
                started,
                observations=(
                    OpeningPhysicalObservationInput(
                        material_identifier_raw=world.material.sku_code,
                        material_identifier_type="sku_code",
                        condition_code="new",
                        availability_bucket="available",
                        counted_qty=LARGE_LEGAL_QUANTITY,
                    ),
                ),
            ),
            key="opening-count-difference-total-overflow",
        )
    ) == "opening_count_aggregate_quantity_invalid"
    world.db.rollback()

    task = world.db.get(FormalStocktakeTask, started.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and task.status == "counting"
    assert task.version == version_before
    assert round_row is not None and round_row.status == "counting"
    assert all(
        world.db.scalar(select(func.count()).select_from(model)) == 0
        for model in (
            StocktakeCountLine,
            StocktakeCountObservation,
            StocktakeScopeCountCompletion,
            StocktakeRoundSubmission,
            StocktakeDifference,
            StocktakeDifferenceSetCompletion,
            InventoryTransaction,
            InventoryOpeningEstablishment,
        )
    )


def test_start_same_key_replays_original_result_after_round_is_submitted(
    world: SimpleNamespace,
):
    start_key = "opening-idempotency-permanent-start-replay"
    started = _start(world, key=start_key)
    _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=_scope_count_command(
            world,
            started,
            observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw=world.material.sku_code,
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1"),
                ),
            ),
        ),
        key="opening-count-idempotency-permanent-start-replay",
    )
    task = world.db.get(FormalStocktakeTask, started.task_id)
    assert task is not None and task.status == "submitted"
    before = {
        model: world.db.scalar(select(func.count()).select_from(model))
        for model in (StateTransitionEvent, OutboxEvent, AuditEvent)
    }

    replay = _start(world, key=start_key)

    assert replay.replayed is True
    assert replay.task_id == started.task_id
    assert replay.initial_round_id == started.initial_round_id
    assert replay.status == "counting"
    assert world.db.get(FormalStocktakeTask, started.task_id).status == "submitted"
    assert all(
        world.db.scalar(select(func.count()).select_from(model)) == count
        for model, count in before.items()
    )


def test_empty_scope_requires_and_accepts_explicit_zero_confirmation(
    world: SimpleNamespace,
):
    personal_command = replace(
        world.command,
        task_no="OPEN-X-PERSONAL-ZERO",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        command=personal_command,
        key="opening-idempotency-personal-zero-count",
    )
    command = _scope_count_command(world, started, zero_confirmed=True)

    result = _submit_scope_count(
        world,
        actor=world.principals["technician"],
        command=command,
        key="opening-count-idempotency-zero",
    )

    assert result.round_sealed is True
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None
    assert completion.zero_confirmed is True
    assert completion.count_line_count == 0
    assert completion.observation_line_count == 0
    assert completion.request_resolution_jsonb == {
        "items": [],
        "request_sha256": completion.request_sha256,
        "round_id": str(completion.round_id),
        "schema": (
            "cloud_oam.opening_stocktake."
            "scope_count_request_resolution.v1"
        ),
        "scope_id": str(completion.scope_id),
        "task_id": str(completion.task_id),
    }
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeCountLine)
    ) == 0


def test_pending_observation_seals_round_as_non_postable_physical_difference(
    world: SimpleNamespace,
):
    personal_command = replace(
        world.command,
        task_no="OPEN-X-PENDING-OBSERVATION",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        command=personal_command,
        key="opening-idempotency-pending-observation",
    )
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw="SITE-UNRESOLVED-001",
                material_identifier_type="unknown",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("2.000"),
                count_method="manual",
                remark="现场无法唯一识别物料",
            ),
        ),
    )
    before_accounts = world.db.scalar(
        select(func.count()).select_from(StockAccount)
    )

    result = _submit_scope_count(
        world,
        actor=world.principals["technician"],
        command=command,
        key="opening-count-idempotency-pending",
    )

    assert result.round_sealed is True
    assert result.has_pending_verification is True
    observation = world.db.scalar(select(StocktakeCountObservation))
    assert observation is not None
    assert observation.verification_status == "pending_verification"
    assert observation.material_id is None
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None
    resolution_item = completion.request_resolution_jsonb["items"][0]
    assert resolution_item["target_type"] == "observation"
    assert resolution_item["target_id"] == str(observation.id)
    assert resolution_item["resolved_material_id"] is None
    assert resolution_item["policy"] is None
    difference = world.db.scalar(
        select(StocktakeDifference).where(
            StocktakeDifference.observed_line_id == observation.id
        )
    )
    assert difference is not None
    assert difference.difference_type == "excess"
    assert difference.observed_account_id is None
    assert difference.reason_code == "opening_pending_verification"
    assert world.db.scalar(
        select(func.count()).select_from(StockAccount)
    ) == before_accounts
    assert world.db.scalar(
        select(func.count()).select_from(InventoryOpeningEstablishment)
    ) == 0


def test_pending_observation_scope_dimension_tamper_breaks_replay(
    world: SimpleNamespace,
):
    personal_command = replace(
        world.command,
        task_no="OPEN-X-PENDING-OBSERVATION-SCOPE-TAMPER",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        command=personal_command,
        key="opening-idempotency-pending-scope-tamper",
    )
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw="SITE-UNRESOLVED-SCOPE-TAMPER",
                material_identifier_type="unknown",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    key = "opening-count-idempotency-pending-scope-tamper"
    _submit_scope_count(
        world,
        actor=world.principals["technician"],
        command=command,
        key=key,
    )
    observation = world.db.scalar(select(StocktakeCountObservation))
    assert observation is not None
    observation.owner_org_id = world.region_y.id
    world.db.flush()

    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["technician"],
            command=command,
            key=key,
        )
    ) == "opening_count_replay_evidence_invalid"


def test_last_of_multiple_scopes_mechanically_seals_initial_round(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    monkeypatch.setattr(opening_count_service, "_database_now", lambda _db: NOW)
    command = replace(
        world.command,
        task_no="OPEN-X-MULTI-SCOPE-COUNT",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.region_location.id,
                assignee_user_id=world.manager_x.user.id,
                freeze_mode="hard",
            ),
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        command=command,
        key="opening-idempotency-multi-scope-count",
    )
    scopes = world.db.scalars(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == started.task_id
        )
    ).all()
    by_location = {row.location_id: row for row in scopes}

    first_command = _scope_count_command(
        world,
        started,
        scope_id=by_location[world.region_location.id].id,
        observations=(
            OpeningPhysicalObservationInput(
                material_id=world.material.id,
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1.000"),
            ),
        ),
    )
    first_key = "opening-count-idempotency-multi-region"
    first = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=first_command,
        key=first_key,
    )
    assert first.round_sealed is False
    assert first.task_status == "counting"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeRoundSubmission)
    ) == 0
    assert world.db.scalar(
        select(func.count())
        .select_from(StateTransitionEvent)
        .where(
            StateTransitionEvent.aggregate_type.in_(
                ("stocktake_round", "stocktake_task")
            ),
            StateTransitionEvent.reason == "opening_initial_round_submitted",
        )
    ) == 0
    assert world.db.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.event_type == "stocktake.opening.round_submitted")
    ) == 0
    assert world.db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == "stocktake.opening.round_submitted")
    ) == 0

    last_command = _scope_count_command(
        world,
        started,
        scope_id=by_location[world.personal_location.id].id,
        zero_confirmed=True,
    )
    last = _submit_scope_count(
        world,
        actor=world.principals["technician"],
        command=last_command,
        key="opening-count-idempotency-multi-personal",
    )
    assert last.round_sealed is True
    assert last.task_status == "submitted"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion)
    ) == 2
    submission = world.db.scalar(select(StocktakeRoundSubmission))
    assert submission is not None
    assert submission.scope_count == 2
    assert submission.zero_scope_count == 1
    completions = {
        row.scope_id: row
        for row in world.db.scalars(select(StocktakeScopeCountCompletion)).all()
    }
    first_completion = completions[by_location[world.region_location.id].id]
    sealing_completion = completions[by_location[world.personal_location.id].id]
    assert first_completion.completed_at == sealing_completion.completed_at
    assert submission.sealing_completion_id == sealing_completion.id
    assert world.db.scalar(
        select(func.count())
        .select_from(StateTransitionEvent)
        .where(
            StateTransitionEvent.aggregate_type.in_(
                ("stocktake_round", "stocktake_task")
            ),
            StateTransitionEvent.reason == "opening_initial_round_submitted",
        )
    ) == 2
    assert world.db.scalar(
        select(func.count())
        .select_from(OutboxEvent)
        .where(OutboxEvent.event_type == "stocktake.opening.round_submitted")
    ) == 1
    assert world.db.scalar(
        select(func.count())
        .select_from(AuditEvent)
        .where(AuditEvent.action == "stocktake.opening.round_submitted")
    ) == 1

    replay = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=first_command,
        key=first_key,
    )
    assert replay.replayed is True
    assert replay.round_sealed is True

    submission.sealing_completion_id = first_completion.id
    world.db.flush()
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=first_command,
            key=first_key,
        )
    ) == "opening_count_replay_evidence_invalid"


def test_scope_count_idempotency_replays_only_same_canonical_request(
    world: SimpleNamespace,
):
    started = _start(world, key="opening-idempotency-count-replay-start")
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_id=world.material.id,
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("3.000"),
            ),
        ),
    )
    first = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key="opening-count-idempotency-replay",
    )
    before = {
        model: world.db.scalar(select(func.count()).select_from(model))
        for model in (
            StocktakeCountLine,
            StocktakeScopeCountCompletion,
            StocktakeRoundSubmission,
            StocktakeDifferenceSetCompletion,
            StocktakeDifference,
            StateTransitionEvent,
            OutboxEvent,
            AuditEvent,
        )
    }

    replay = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key="opening-count-idempotency-replay",
    )
    assert replay == replace(first, replayed=True)
    assert all(
        world.db.scalar(select(func.count()).select_from(model)) == count
        for model, count in before.items()
    )

    changed = replace(
        command,
        physical_observations=(
            replace(
                command.physical_observations[0], counted_qty=Decimal("4.000")
            ),
        ),
    )
    with pytest.raises(OpeningStocktakeCountError) as conflict:
        _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=changed,
            key="opening-count-idempotency-replay",
        )
    assert conflict.value.code == "opening_count_idempotency_conflict"

    with pytest.raises(OpeningStocktakeCountError) as completed:
        _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key="opening-count-idempotency-different-key",
        )
    assert completed.value.code == "opening_count_scope_already_completed"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeCountObservation)
    ) == 0


def test_scope_count_replay_rejects_tampered_difference_completion(
    world: SimpleNamespace,
):
    started = _start(world, key="opening-idempotency-difference-seal-start")
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_id=world.material.id,
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1.000"),
            ),
        ),
    )
    key = "opening-count-idempotency-difference-seal"
    _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key=key,
    )
    completion = world.db.scalar(select(StocktakeDifferenceSetCompletion))
    assert completion is not None
    completion.difference_manifest_sha256 = "f" * 64
    world.db.flush()

    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key=key,
        )
    ) == "opening_count_replay_evidence_invalid"


def test_scope_count_requires_exact_frozen_assignee_and_current_principal(
    world: SimpleNamespace,
):
    personal_command = replace(
        world.command,
        task_no="OPEN-X-PERSONAL-PERMISSION",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        command=personal_command,
        key="opening-idempotency-personal-permission",
    )
    command = _scope_count_command(world, started, zero_confirmed=True)

    with pytest.raises(OpeningStocktakeCountError) as captured:
        _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key="opening-count-idempotency-wrong-assignee",
        )
    assert captured.value.code == "opening_count_not_frozen_assignee"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion)
    ) == 0
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeCountLine)
    ) == 0


def test_material_qr_and_lot_number_are_resolved_by_server_master_data(
    world: SimpleNamespace,
):
    db = world.db
    policy = db.scalar(
        select(MaterialInventoryPolicy).where(
            MaterialInventoryPolicy.material_id == world.material.id
        )
    )
    lot = InventoryLot(
        id=uuid.uuid4(),
        material_id=world.material.id,
        lot_no="LOT-OPEN-001",
        manufacture_date=None,
        expiry_date=None,
    )
    material_qr = QrCode(
        id=uuid.uuid4(),
        code="QR-MATERIAL-OPEN-001",
        object_type="material",
        object_id=world.material.id,
        status="active",
        printed_at=NOW,
    )
    policy.tracking_mode = "lot"
    policy.quantity_scale = 3
    policy.allow_fraction = True
    world.account.lot_id = lot.id
    db.add_all([lot, material_qr])
    db.commit()

    started = _start(world, key="opening-idempotency-count-material-qr-lot")
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=material_qr.code,
                material_identifier_type="qr_code",
                condition_code="new",
                availability_bucket="available",
                lot_no_raw=lot.lot_no,
                counted_qty=Decimal("2.000"),
            ),
        ),
    )
    result = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key="opening-count-idempotency-material-qr-lot",
    )

    assert result.round_sealed is True
    line = db.scalar(select(StocktakeCountLine))
    assert line is not None
    assert line.stock_account_id == world.account.id
    assert line.counted_qty == Decimal("2.000")
    assert db.scalar(select(func.count()).select_from(StocktakeCountObservation)) == 0
    completion = db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None
    request_item = completion.request_jsonb["physical_observations"][0]
    assert request_item["material_identifier_raw"] == material_qr.code
    assert request_item["material_identifier_type"] == "qr_code"
    assert request_item["material_id"] is None
    assert request_item["lot_id"] is None
    assert request_item["lot_no_raw"] == lot.lot_no
    resolution_item = completion.request_resolution_jsonb["items"][0]
    assert resolution_item["target_type"] == "count_line"
    assert resolution_item["target_id"] == str(line.id)
    assert resolution_item["material_qr_mapping_id"] == str(material_qr.id)
    assert resolution_item["resolved_material_id"] == str(world.material.id)
    assert resolution_item["resolved_lot_id"] == str(lot.id)
    assert resolution_item["policy"] == {
        "allow_fraction": True,
        "effective_from": opening_count_service._canonical_timestamp(
            policy.effective_from
        ),
        "id": str(policy.id),
        "quantity_scale": 3,
        "tracking_mode": "lot",
    }


def test_serial_number_and_serial_qr_are_piece_counted_and_manifest_tamper_fails(
    world: SimpleNamespace,
):
    db = world.db
    policy = db.scalar(
        select(MaterialInventoryPolicy).where(
            MaterialInventoryPolicy.material_id == world.material.id
        )
    )
    policy.tracking_mode = "serial"
    policy.quantity_scale = 0
    policy.allow_fraction = False
    first_serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material.id,
        serial_no="SN-OPEN-COUNT-001",
        qr_code="QR-SN-OPEN-COUNT-001",
        lot_id=None,
        lifecycle_status="active",
    )
    second_serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material.id,
        serial_no="SN-OPEN-COUNT-002",
        qr_code="QR-SN-OPEN-COUNT-002",
        lot_id=None,
        lifecycle_status="active",
    )
    serial_qr_mapping = QrCode(
        id=uuid.uuid4(),
        code=second_serial.qr_code,
        object_type="serial",
        object_id=second_serial.id,
        status="active",
        printed_at=NOW,
    )
    db.add_all([first_serial, second_serial, serial_qr_mapping])
    db.commit()
    started = _start(world, key="opening-idempotency-count-serials")
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                serial_no_raw=first_serial.serial_no,
                serial_identifier_type="serial_no",
                counted_qty=Decimal("1"),
            ),
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                serial_no_raw=second_serial.qr_code,
                serial_identifier_type="qr_code",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    key = "opening-count-idempotency-serial-manifest"
    result = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key=key,
    )
    assert result.round_sealed is True
    line = db.scalar(select(StocktakeCountLine))
    assert line is not None and line.counted_qty == Decimal("2")
    serial_rows = db.scalars(
        select(StocktakeCountSerial).order_by(StocktakeCountSerial.serial_id)
    ).all()
    assert {row.serial_id for row in serial_rows} == {
        first_serial.id,
        second_serial.id,
    }
    completion = db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None
    resolution_items = completion.request_resolution_jsonb["items"]
    assert len(resolution_items) == 2
    assert {row["target_id"] for row in resolution_items} == {str(line.id)}
    assert {row["resolved_serial_id"] for row in resolution_items} == {
        str(first_serial.id),
        str(second_serial.id),
    }
    assert {row["serial_qr_mapping_id"] for row in resolution_items} == {
        None,
        str(serial_qr_mapping.id),
    }
    assert all(row["policy"]["tracking_mode"] == "serial" for row in resolution_items)

    serial_rows[0].result = "present"
    db.flush()
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key=key,
        )
    ) == "opening_count_replay_evidence_invalid"


def test_replay_rejects_serial_shared_by_count_line_and_observation(
    world: SimpleNamespace,
):
    db = world.db
    policy = db.scalar(
        select(MaterialInventoryPolicy).where(
            MaterialInventoryPolicy.material_id == world.material.id
        )
    )
    policy.tracking_mode = "serial"
    policy.quantity_scale = 0
    policy.allow_fraction = False
    line_serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material.id,
        serial_no="SN-REPLAY-CROSS-TABLE-001",
        qr_code="QR-REPLAY-CROSS-TABLE-001",
        lot_id=None,
        lifecycle_status="active",
    )
    observation_serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material.id,
        serial_no="SN-REPLAY-CROSS-TABLE-002",
        qr_code="QR-REPLAY-CROSS-TABLE-002",
        lot_id=None,
        lifecycle_status="active",
    )
    db.add_all([line_serial, observation_serial])
    db.commit()

    started = _start(world, key="opening-idempotency-replay-cross-table-serial")
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                serial_no_raw=line_serial.serial_no,
                serial_identifier_type="serial_no",
                counted_qty=Decimal("1"),
            ),
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="used",
                availability_bucket="available",
                serial_no_raw=observation_serial.serial_no,
                serial_identifier_type="serial_no",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    key = "opening-count-idempotency-replay-cross-table-serial"
    _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key=key,
    )

    completion = db.scalar(select(StocktakeScopeCountCompletion))
    line = db.scalar(select(StocktakeCountLine))
    observation = db.scalar(select(StocktakeCountObservation))
    count_serial = db.scalar(select(StocktakeCountSerial))
    assert completion is not None
    assert line is not None
    assert observation is not None
    assert count_serial is not None
    assert count_serial.serial_id == line_serial.id
    assert observation.serial_id == observation_serial.id
    assert opening_count_service._persisted_request_resolution_document_valid(
        db,
        completion,
        lines=(line,),
        observations=(observation,),
        count_serials=(count_serial,),
    )

    # Forge a document that remains internally consistent at the observation
    # target: both the relational observation and its immutable resolution now
    # claim the count-line serial. Only round-wide/cross-table uniqueness makes
    # this evidence invalid.
    observation.serial_id = line_serial.id
    resolution = completion.request_resolution_jsonb
    completion.request_resolution_jsonb = {
        **resolution,
        "items": [
            (
                {
                    **item,
                    "resolved_serial_id": str(line_serial.id),
                }
                if item["target_type"] == "observation"
                else item
            )
            for item in resolution["items"]
        ],
    }
    db.flush()

    assert not opening_count_service._persisted_request_resolution_document_valid(
        db,
        completion,
        lines=(line,),
        observations=(observation,),
        count_serials=(count_serial,),
    )
    assert not opening_count_service._persisted_round_serial_uniqueness_valid(
        db,
        task_id=completion.task_id,
        round_id=completion.round_id,
        completions=(completion,),
    )
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key=key,
        )
    ) == "opening_count_replay_evidence_invalid"


def test_round_serial_alias_replay_fold_is_ascii_only(
    world: SimpleNamespace,
):
    task_id = uuid.uuid4()
    round_id = uuid.uuid4()

    def completion_with_aliases(*aliases: tuple[str, str]) -> SimpleNamespace:
        return SimpleNamespace(
            request_jsonb={
                "physical_observations": [
                    {
                        "serial_identifier_type": identifier_type,
                        "serial_no_raw": raw,
                    }
                    for identifier_type, raw in aliases
                ]
            },
            request_resolution_jsonb={
                "items": [
                    {
                        "request_ordinal": ordinal,
                        "serial_alias_keys": [
                            opening_count_service._fold_serial_alias(raw)
                        ],
                    }
                    for ordinal, (_identifier_type, raw) in enumerate(
                        aliases,
                        start=1,
                    )
                ]
            },
        )

    # Unicode case folding is intentionally not part of the persisted key:
    # Python and PostgreSQL must both leave non-ASCII code points unchanged.
    assert opening_count_service._persisted_round_serial_uniqueness_valid(
        world.db,
        task_id=task_id,
        round_id=round_id,
        completions=(
            completion_with_aliases(
                ("serial_no", "SN-STRAßE"),
                ("qr_code", "sn-strasse"),
            ),
        ),
    )
    # ASCII aliases remain case-insensitive even across identifier types.
    assert not opening_count_service._persisted_round_serial_uniqueness_valid(
        world.db,
        task_id=task_id,
        round_id=round_id,
        completions=(
            completion_with_aliases(
                ("serial_no", "SN-Ascii-Alias-001"),
                ("qr_code", "sn-aSCII-aLIAS-001"),
            ),
        ),
    )


def test_ambiguous_global_serial_reference_is_rejected_before_count(
    world: SimpleNamespace,
):
    first = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material.id,
        serial_no="SN-AMBIGUOUS-RAW",
        qr_code="QR-AMBIGUOUS-FIRST",
        lot_id=None,
        lifecycle_status="active",
    )
    second = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material.id,
        serial_no="SN-AMBIGUOUS-SECOND",
        qr_code="SN-AMBIGUOUS-RAW",
        lot_id=None,
        lifecycle_status="active",
    )
    world.db.add_all([first, second])
    world.db.commit()
    started = _start(world, key="opening-idempotency-ambiguous-serial-reference")
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw="UNRESOLVED-AMBIGUOUS-MATERIAL",
                material_identifier_type="unknown",
                condition_code="new",
                availability_bucket="available",
                serial_no_raw="SN-AMBIGUOUS-RAW",
                serial_identifier_type="unknown",
                counted_qty=Decimal("1"),
            ),
        ),
    )

    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key="opening-count-idempotency-ambiguous-serial-reference",
        )
    ) == "opening_count_serial_reference_ambiguous"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion)
    ) == 0


def test_unknown_serial_without_any_master_candidate_remains_observable(
    world: SimpleNamespace,
):
    started = _start(world, key="opening-idempotency-zero-serial-candidate")
    raw_serial = "SN-NOT-YET-IN-MASTER"
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw="UNRESOLVED-ZERO-CANDIDATE-MATERIAL",
                material_identifier_type="unknown",
                condition_code="new",
                availability_bucket="available",
                serial_no_raw=raw_serial,
                serial_identifier_type="unknown",
                counted_qty=Decimal("1"),
            ),
        ),
    )

    result = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key="opening-count-idempotency-zero-serial-candidate",
    )

    assert result.has_pending_verification
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None
    assert completion.request_resolution_jsonb["items"][0][
        "serial_alias_keys"
    ] == [opening_count_service._fold_serial_alias(raw_serial)]


def test_opening_count_physical_observation_limit_is_enforced_in_schema_and_service(
    world: SimpleNamespace,
):
    schema_observation = OpeningPhysicalObservationIn(
        material_identifier_raw="UNKNOWN-LIMIT-MATERIAL",
        material_identifier_type="unknown",
        condition_code="new",
        availability_bucket="available",
        counted_qty="1",
    )
    accepted_schema = OpeningStocktakeCountIn(
        physical_observations=(schema_observation,) * 10_000,
    )
    assert len(accepted_schema.physical_observations) == 10_000
    with pytest.raises(ValueError):
        OpeningStocktakeCountIn(
            physical_observations=(schema_observation,) * 10_001,
        )

    service_observation = OpeningPhysicalObservationInput(
        material_identifier_raw="UNKNOWN-LIMIT-MATERIAL",
        material_identifier_type="unknown",
        condition_code="new",
        availability_bucket="available",
        counted_qty=Decimal("1"),
    )
    accepted_command = opening_count_service._validate_command(
        SubmitOpeningStocktakeScopeCountCommand(
            task_id=uuid.uuid4(),
            round_id=uuid.uuid4(),
            scope_id=uuid.uuid4(),
            physical_observations=(service_observation,) * 10_000,
        )
    )
    assert len(accepted_command.physical_observations) == 10_000

    oversized_command = SubmitOpeningStocktakeScopeCountCommand(
        task_id=uuid.uuid4(),
        round_id=uuid.uuid4(),
        scope_id=uuid.uuid4(),
        physical_observations=(service_observation,) * 10_001,
    )
    assert _count_error_code(
        lambda: submit_opening_stocktake_scope_count(
            world.db,
            actor=world.principals["manager_x"],
            command=oversized_command,
            idempotency_key="opening-count-idempotency-oversized-direct-service",
            request_id="opening-count-request-oversized-direct-service",
        )
    ) == "opening_count_too_many_lines"


def test_unresolved_serial_alias_snapshot_is_immutable_and_blocks_forged_qr_replay(
    world: SimpleNamespace,
):
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material.id,
        serial_no="SN-UNRESOLVED-SNAPSHOT-001",
        qr_code="QR-UNRESOLVED-SNAPSHOT-001",
        lot_id=None,
        lifecycle_status="active",
    )
    world.db.add(serial)
    world.db.commit()
    started = _start(
        world,
        key="opening-idempotency-unresolved-alias-snapshot",
    )
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw="UNKNOWN-MATERIAL-ALIAS-SNAPSHOT",
                material_identifier_type="unknown",
                condition_code="new",
                availability_bucket="available",
                serial_no_raw=serial.serial_no,
                serial_identifier_type="serial_no",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    key = "opening-count-idempotency-unresolved-alias-snapshot"
    first = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key=key,
    )
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None
    alias_keys = sorted(
        {
            opening_count_service._fold_serial_alias(serial.serial_no),
            opening_count_service._fold_serial_alias(serial.qr_code),
        }
    )
    assert completion.request_resolution_jsonb["items"][0][
        "serial_alias_keys"
    ] == alias_keys

    forged_qr_completion = SimpleNamespace(
        request_jsonb={
            "physical_observations": [
                {
                    "serial_identifier_type": "qr_code",
                    "serial_no_raw": serial.qr_code,
                }
            ]
        },
        request_resolution_jsonb={
            "items": [
                {
                    "request_ordinal": 1,
                    "serial_alias_keys": alias_keys,
                }
            ]
        },
    )
    assert not opening_count_service._persisted_round_serial_uniqueness_valid(
        world.db,
        task_id=completion.task_id,
        round_id=completion.round_id,
        completions=(completion, forged_qr_completion),
    )

    serial.serial_no = "SN-UNRESOLVED-SNAPSHOT-RENAMED"
    serial.qr_code = "QR-UNRESOLVED-SNAPSHOT-RENAMED"
    world.db.flush()
    replay = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key=key,
    )
    assert replay == replace(first, replayed=True)
    assert completion.request_resolution_jsonb["items"][0][
        "serial_alias_keys"
    ] == alias_keys


def test_resolved_serial_cannot_be_recounted_in_another_scope_by_qr_alias(
    world: SimpleNamespace,
):
    db = world.db
    policy = db.scalar(
        select(MaterialInventoryPolicy).where(
            MaterialInventoryPolicy.material_id == world.material.id
        )
    )
    policy.tracking_mode = "serial"
    policy.quantity_scale = 0
    policy.allow_fraction = False
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material.id,
        serial_no="SN-CROSS-SCOPE-001",
        qr_code="QR-CROSS-SCOPE-001",
        lot_id=None,
        lifecycle_status="active",
    )
    db.add(serial)
    db.commit()
    command = replace(
        world.command,
        task_no="OPEN-X-CROSS-SCOPE-SERIAL",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.region_location.id,
                assignee_user_id=world.manager_x.user.id,
                freeze_mode="hard",
            ),
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        command=command,
        key="opening-idempotency-cross-scope-serial",
    )
    scopes = db.scalars(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == started.task_id
        )
    ).all()
    by_location = {row.location_id: row for row in scopes}
    first = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=_scope_count_command(
            world,
            started,
            scope_id=by_location[world.region_location.id].id,
            observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw=world.material.sku_code,
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    serial_no_raw=serial.serial_no,
                    serial_identifier_type="serial_no",
                    counted_qty=Decimal("1"),
                ),
            ),
        ),
        key="opening-count-idempotency-cross-scope-serial-first",
    )
    assert first.round_sealed is False
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["technician"],
            command=_scope_count_command(
                world,
                started,
                scope_id=by_location[world.personal_location.id].id,
                observations=(
                    OpeningPhysicalObservationInput(
                        material_identifier_raw=world.material.sku_code,
                        material_identifier_type="sku_code",
                        condition_code="new",
                        availability_bucket="available",
                        serial_no_raw=serial.qr_code,
                        serial_identifier_type="qr_code",
                        counted_qty=Decimal("1"),
                    ),
                ),
            ),
            key="opening-count-idempotency-cross-scope-serial-last",
        )
    ) == "opening_count_serial_duplicate"
    assert db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion)
    ) == 1
    assert db.get(StocktakeRound, started.initial_round_id).status == "counting"


def test_pending_serial_sn_and_qr_aliases_cannot_double_count_across_scopes(
    world: SimpleNamespace,
):
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=world.material.id,
        serial_no="SN-PENDING-ALIAS-001",
        qr_code="QR-PENDING-ALIAS-001",
        lot_id=None,
        lifecycle_status="active",
    )
    world.db.add(serial)
    world.db.commit()
    command = replace(
        world.command,
        task_no="OPEN-X-PENDING-SERIAL-ALIAS",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.region_location.id,
                assignee_user_id=world.manager_x.user.id,
                freeze_mode="hard",
            ),
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        command=command,
        key="opening-idempotency-pending-serial-alias",
    )
    scopes = world.db.scalars(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == started.task_id
        )
    ).all()
    by_location = {row.location_id: row for row in scopes}
    _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=_scope_count_command(
            world,
            started,
            scope_id=by_location[world.region_location.id].id,
            observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw="UNRESOLVED-PENDING-MATERIAL",
                    material_identifier_type="unknown",
                    condition_code="new",
                    availability_bucket="available",
                    serial_no_raw=serial.serial_no,
                    serial_identifier_type="serial_no",
                    counted_qty=Decimal("1"),
                ),
            ),
        ),
        key="opening-count-idempotency-pending-serial-first",
    )
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["technician"],
            command=_scope_count_command(
                world,
                started,
                scope_id=by_location[world.personal_location.id].id,
                observations=(
                    OpeningPhysicalObservationInput(
                        material_identifier_raw="UNRESOLVED-PENDING-MATERIAL",
                        material_identifier_type="unknown",
                        condition_code="new",
                        availability_bucket="available",
                        serial_no_raw=serial.qr_code,
                        serial_identifier_type="qr_code",
                        counted_qty=Decimal("1"),
                    ),
                ),
            ),
            key="opening-count-idempotency-pending-serial-last",
        )
    ) == "opening_count_serial_duplicate"


@pytest.mark.parametrize("identifier_type", ["external_code", "unknown"])
def test_unproven_material_identifier_cannot_carry_guessed_internal_id(
    world: SimpleNamespace,
    identifier_type: str,
):
    started = _start(
        world,
        key=f"opening-idempotency-unproven-material-{identifier_type}",
    )
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_id=world.material.id,
                material_identifier_raw=f"UNPROVEN-{identifier_type}",
                material_identifier_type=identifier_type,
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key=f"opening-count-unproven-material-{identifier_type}",
        )
    ) == "opening_count_material_identifier_unproven"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion)
    ) == 0


@pytest.mark.parametrize("field", ["lot_id", "serial_id"])
def test_internal_tracking_id_without_raw_identifier_is_rejected(
    world: SimpleNamespace,
    field: str,
):
    value = OpeningPhysicalObservationInput(
        material_identifier_raw=world.material.sku_code,
        material_identifier_type="sku_code",
        condition_code="new",
        availability_bucket="available",
        counted_qty=Decimal("1"),
        **{field: uuid.uuid4()},
    )
    started = _start(world, key=f"opening-idempotency-orphan-{field}")
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=_scope_count_command(
                world,
                started,
                observations=(value,),
            ),
            key=f"opening-count-idempotency-orphan-{field}",
        )
    ) == f"opening_count_{'lot' if field == 'lot_id' else 'serial'}_identifier_invalid"


def test_same_verified_dimension_cannot_be_double_counted_by_sku_qr_alias(
    world: SimpleNamespace,
):
    material_qr = QrCode(
        id=uuid.uuid4(),
        code="QR-MATERIAL-ALIAS-001",
        object_type="material",
        object_id=world.material.id,
        status="active",
        printed_at=NOW,
    )
    world.db.add(material_qr)
    world.db.commit()
    personal_command = replace(
        world.command,
        task_no="OPEN-X-NO-SNAPSHOT-ALIAS-DUPLICATE",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        command=personal_command,
        key="opening-idempotency-alias-duplicate",
    )
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
                remark="SKU scan",
            ),
            OpeningPhysicalObservationInput(
                material_identifier_raw=material_qr.code,
                material_identifier_type="qr_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("2"),
                remark="QR scan with different quantity",
            ),
        ),
    )
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["technician"],
            command=command,
            key="opening-count-idempotency-alias-duplicate",
        )
    ) == "opening_count_observation_duplicate"


def test_exact_duplicate_request_items_fail_with_stable_domain_error(
    world: SimpleNamespace,
):
    started = _start(world, key="opening-idempotency-exact-duplicate")
    observation = OpeningPhysicalObservationInput(
        material_identifier_raw=world.material.sku_code,
        material_identifier_type="sku_code",
        condition_code="new",
        availability_bucket="available",
        counted_qty=Decimal("1"),
    )
    command = _scope_count_command(
        world,
        started,
        observations=(observation, observation),
    )

    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key="opening-count-idempotency-exact-duplicate",
        )
    ) == "opening_count_observation_duplicate"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion)
    ) == 0


def test_physical_observation_order_is_ignored_for_same_key_replay(
    world: SimpleNamespace,
):
    started = _start(world, key="opening-idempotency-unordered-request")
    observations = (
        OpeningPhysicalObservationInput(
            material_identifier_raw=world.material.sku_code,
            material_identifier_type="sku_code",
            condition_code="new",
            availability_bucket="available",
            counted_qty=Decimal("1"),
        ),
        OpeningPhysicalObservationInput(
            material_identifier_raw=world.material.sku_code,
            material_identifier_type="sku_code",
            condition_code="used",
            availability_bucket="available",
            counted_qty=Decimal("1"),
        ),
    )
    command = _scope_count_command(world, started, observations=observations)
    key = "opening-count-idempotency-unordered-request"
    first = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key=key,
    )
    replay = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=replace(command, physical_observations=tuple(reversed(observations))),
        key=key,
    )
    assert replay == replace(first, replayed=True)
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None
    persisted_observations = completion.request_jsonb["physical_observations"]
    assert persisted_observations == sorted(
        persisted_observations,
        key=opening_count_service._canonical_json,
    )
    resolution_items = completion.request_resolution_jsonb["items"]
    assert [row["request_ordinal"] for row in resolution_items] == [1, 2]
    assert [row["request_item_sha256"] for row in resolution_items] == [
        opening_count_service._hash_document(row)
        for row in persisted_observations
    ]


def test_scope_count_replay_rejects_tampered_request_resolution_target(
    world: SimpleNamespace,
):
    started = _start(world, key="opening-idempotency-resolution-tamper")
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    key = "opening-count-idempotency-resolution-tamper"
    _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key=key,
    )
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None
    document = completion.request_resolution_jsonb
    completion.request_resolution_jsonb = {
        **document,
        "items": [
            {
                **document["items"][0],
                "target_id": str(uuid.uuid4()),
            }
        ],
    }
    world.db.flush()

    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key=key,
        )
    ) == "opening_count_replay_evidence_invalid"


def test_scope_count_replay_rejects_unhashable_resolution_enum(
    world: SimpleNamespace,
):
    started = _start(world, key="opening-idempotency-resolution-enum")
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    key = "opening-count-idempotency-resolution-enum"
    _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key=key,
    )
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion is not None
    request_item = completion.request_jsonb["physical_observations"][0]
    assert opening_count_service._persisted_request_observation_quantity(
        {**request_item, "availability_bucket": []}
    ) is None
    document = completion.request_resolution_jsonb
    completion.request_resolution_jsonb = {
        **document,
        "items": [
            {
                **document["items"][0],
                "target_type": [],
            }
        ],
    }
    world.db.flush()

    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key=key,
        )
    ) == "opening_count_replay_evidence_invalid"


def test_region_location_custody_does_not_turn_scope_into_personal_count(
    world: SimpleNamespace,
):
    world.db.add(
        CustodyAssignment(
            id=uuid.uuid4(),
            location_id=world.region_location.id,
            custodian_person_id=world.manager_x.person.id,
            valid_from=NOW - timedelta(days=1),
            valid_to=None,
            handover_case_id=None,
        )
    )
    world.db.commit()
    started = _start(world, key="opening-idempotency-region-custody")
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == started.task_id
        )
    )
    assert scope is not None
    assert scope.custodian_person_id_snapshot == world.manager_x.person.id
    assert world.account.custodian_person_id is None

    result = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=_scope_count_command(
            world,
            started,
            observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw=world.material.sku_code,
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1"),
                ),
            ),
        ),
        key="opening-count-idempotency-region-custody",
    )
    assert result.round_sealed is True


def test_db_trigger_exception_is_mapped_without_database_detail(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    started = _start(world, key="opening-idempotency-dbapi-guard")
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
            ),
        ),
    )

    class _TriggerRejection(Exception):
        sqlstate = "P0001"

    def _raise_dbapi(*_args, **_kwargs):
        raise DBAPIError(
            "INSERT INTO secret_table VALUES (:secret)",
            {"secret": "database detail"},
            _TriggerRejection("P0001 sensitive trigger detail"),
            False,
        )

    monkeypatch.setattr(opening_count_service, "_write_scope_count", _raise_dbapi)
    with pytest.raises(OpeningStocktakeCountError) as captured:
        _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key="opening-count-idempotency-dbapi-guard",
        )
    assert captured.value.code == "opening_count_database_guard_rejected"
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert captured.value.__suppress_context__ is True
    assert "secret" not in str(captured.value.as_detail()).lower()
    assert "p0001" not in str(captured.value.as_detail()).lower()


def test_preflight_select_p0001_is_not_misclassified_as_trigger_rejection(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    started = _start(world, key="opening-idempotency-dbapi-preflight")
    command = _scope_count_command(world, started, zero_confirmed=False)

    class _UnexpectedSelectFailure(Exception):
        sqlstate = "P0001"

    def _raise_preflight(*_args, **_kwargs):
        raise DBAPIError(
            "SELECT secret_column FROM secret_table",
            {"secret": "database detail"},
            _UnexpectedSelectFailure("P0001 is not proof of a write trigger"),
            False,
        )

    monkeypatch.setattr(world.db, "scalar", _raise_preflight)
    with pytest.raises(OpeningStocktakeCountError) as captured:
        _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key="opening-count-idempotency-dbapi-preflight",
        )
    assert captured.value.code == "opening_count_database_unavailable"
    assert captured.value.http_status_code == 503
    assert captured.value.__cause__ is None
    assert captured.value.__context__ is None
    assert "secret" not in str(captured.value.as_detail()).lower()


def test_connection_dbapi_and_integrity_have_distinct_sanitized_boundaries(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    started = _start(world, key="opening-idempotency-dbapi-connection")
    command = _scope_count_command(world, started, zero_confirmed=False)

    class _ConnectionFailure(Exception):
        sqlstate = "08006"

    def _raise_connection(*_args, **_kwargs):
        raise DBAPIError(
            None,
            None,
            _ConnectionFailure("server=secret-host password=secret"),
            True,
        )

    monkeypatch.setattr(
        opening_count_service,
        "_take_advisory_locks",
        _raise_connection,
    )
    with pytest.raises(OpeningStocktakeCountError) as unavailable:
        _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key="opening-count-idempotency-dbapi-connection",
        )
    assert unavailable.value.code == "opening_count_database_unavailable"
    assert unavailable.value.http_status_code == 503
    assert unavailable.value.__cause__ is None
    assert unavailable.value.__context__ is None

    monkeypatch.setattr(
        opening_count_service,
        "_take_advisory_locks",
        lambda *_args, **_kwargs: None,
    )

    def _raise_integrity(*_args, **_kwargs):
        raise IntegrityError(
            "INSERT INTO secret_table VALUES (:secret)",
            {"secret": "database detail"},
            Exception("unique secret violated"),
        )

    monkeypatch.setattr(opening_count_service, "_write_scope_count", _raise_integrity)
    with pytest.raises(OpeningStocktakeCountError) as conflict:
        _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key="opening-count-idempotency-integrity-boundary",
        )
    assert conflict.value.code == "opening_count_concurrent_conflict"
    assert conflict.value.http_status_code == 409
    assert conflict.value.__cause__ is None
    assert conflict.value.__context__ is None


def test_pending_from_earlier_scope_remains_visible_after_last_scope_seals(
    world: SimpleNamespace,
):
    command = replace(
        world.command,
        task_no="OPEN-X-CROSS-SCOPE-PENDING",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.region_location.id,
                assignee_user_id=world.manager_x.user.id,
                freeze_mode="hard",
            ),
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        command=command,
        key="opening-idempotency-cross-scope-pending",
    )
    scopes = world.db.scalars(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == started.task_id
        )
    ).all()
    by_location = {row.location_id: row for row in scopes}
    first_command = _scope_count_command(
        world,
        started,
        scope_id=by_location[world.region_location.id].id,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw="UNRESOLVED-CROSS-SCOPE-001",
                material_identifier_type="unknown",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    first_key = "opening-count-idempotency-cross-scope-first"
    first = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=first_command,
        key=first_key,
    )
    assert first.round_sealed is False
    assert first.has_pending_verification is True

    last = _submit_scope_count(
        world,
        actor=world.principals["technician"],
        command=_scope_count_command(
            world,
            started,
            scope_id=by_location[world.personal_location.id].id,
            zero_confirmed=True,
        ),
        key="opening-count-idempotency-cross-scope-last",
    )
    assert last.round_sealed is True
    assert last.has_pending_verification is True
    replay = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=first_command,
        key=first_key,
    )
    assert replay.round_sealed is True
    assert replay.has_pending_verification is True
    assert replay.replayed is True
    scope_audits = {
        row.aggregate_id: row.after_jsonb["has_pending_verification"]
        for row in world.db.scalars(
            select(AuditEvent).where(
                AuditEvent.action == "stocktake.opening.scope_count_completed"
            )
        ).all()
    }
    assert scope_audits[str(by_location[world.region_location.id].id)] is True
    assert scope_audits[str(by_location[world.personal_location.id].id)] is False


def test_ambiguous_control_allocation_rolls_back_without_sealing_or_differences(
    world: SimpleNamespace,
):
    db = world.db
    control = _install_control_sync(
        db,
        source=world.source,
        region=world.region_x,
        material=world.material,
        rows=(
            {
                "external_business_key": "CTRL-AMBIGUOUS-1",
                "material_id": world.material.id,
                "condition_code": "new",
                "control_qty": Decimal("500"),
                "mapping_status": "resolved",
                "mapping_note": "",
            },
            {
                "external_business_key": "CTRL-AMBIGUOUS-2",
                "material_id": world.material.id,
                "condition_code": "new",
                "control_qty": Decimal("499"),
                "mapping_status": "resolved",
                "mapping_note": "",
            },
        ),
    )
    start_command = replace(
        world.command,
        task_no="OPEN-X-AMBIGUOUS-CONTROL",
        control_sync_run_id=control.sync_run.id,
        control_sync_scope_key=control.sync_run.scope_key,
        control_lines=control.lines,
    )
    started = _start(
        world,
        command=start_command,
        key="opening-idempotency-ambiguous-control",
    )
    db.commit()
    count_command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=count_command,
            key="opening-count-idempotency-ambiguous-control",
        )
    ) == "opening_control_allocation_ambiguous"
    db.rollback()

    assert db.get(StocktakeRound, started.initial_round_id).status == "counting"
    for model in (
        StocktakeCountLine,
        StocktakeScopeCountCompletion,
        StocktakeRoundSubmission,
        StocktakeDifferenceSetCompletion,
        StocktakeDifference,
    ):
        assert db.scalar(
            select(func.count()).select_from(model).where(model.task_id == started.task_id)
        ) == 0


@pytest.mark.parametrize(
    ("tamper", "expected_code"),
    [
        ("scope", "opening_count_scope_anchor_invalid"),
        ("account", "opening_count_snapshot_anchor_invalid"),
        ("snapshot", "opening_count_snapshot_anchor_invalid"),
        ("snapshot_manifest", "opening_count_snapshot_manifest_mismatch"),
        ("control", "opening_count_control_manifest_mismatch"),
    ],
)
def test_count_revalidates_immutable_start_anchors(
    world: SimpleNamespace,
    tamper: str,
    expected_code: str,
):
    started = _start(world, key=f"opening-idempotency-anchor-{tamper}")
    if tamper == "scope":
        row = world.db.scalar(
            select(FormalStocktakeScope).where(
                FormalStocktakeScope.task_id == started.task_id
            )
        )
        row.scope_sha256 = "f" * 64
    elif tamper == "account":
        world.account.condition_code = "used"
    elif tamper == "snapshot":
        row = world.db.scalar(
            select(StocktakeSnapshotLine).where(
                StocktakeSnapshotLine.task_id == started.task_id
            )
        )
        row.account_dimension_sha256 = "f" * 64
    elif tamper == "snapshot_manifest":
        world.db.get(FormalStocktakeTask, started.task_id).snapshot_manifest_sha256 = (
            "f" * 64
        )
    else:
        row = world.db.scalar(
            select(StocktakeControlSnapshotLine).where(
                StocktakeControlSnapshotLine.task_id == started.task_id
            )
        )
        row.control_qty += Decimal("1")
    world.db.flush()
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=_scope_count_command(
                world,
                started,
                observations=(
                    OpeningPhysicalObservationInput(
                        material_identifier_raw=world.material.sku_code,
                        material_identifier_type="sku_code",
                        condition_code="new",
                        availability_bucket="available",
                        counted_qty=Decimal("1"),
                    ),
                ),
            ),
            key=f"opening-count-idempotency-anchor-{tamper}",
        )
    ) == expected_code


@pytest.mark.parametrize(
    "tamper",
    [
        "scope_state",
        "round_state",
        "task_state",
        "scope_outbox",
        "round_outbox",
        "scope_audit",
        "round_audit",
        "count_line_timestamp",
        "difference_timestamp",
        "difference",
    ],
)
def test_replay_rejects_tampered_state_outbox_audit_or_difference_graph(
    world: SimpleNamespace,
    tamper: str,
):
    started = _start(world, key=f"opening-idempotency-graph-{tamper}")
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    key = f"opening-count-idempotency-graph-{tamper}"
    _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key=key,
    )
    if tamper == "count_line_timestamp":
        row = world.db.scalar(
            select(StocktakeCountLine).where(
                StocktakeCountLine.task_id == started.task_id
            )
        )
        row.updated_at = row.updated_at + timedelta(seconds=1)
    elif tamper == "difference_timestamp":
        row = world.db.scalar(
            select(StocktakeDifference).where(
                StocktakeDifference.task_id == started.task_id
            )
        )
        row.created_at = row.created_at + timedelta(seconds=1)
    elif tamper.endswith("state"):
        aggregate_type = {
            "scope_state": "stocktake_scope",
            "round_state": "stocktake_round",
            "task_state": "stocktake_task",
        }[tamper]
        row = world.db.scalar(
            select(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == aggregate_type,
                StateTransitionEvent.from_status == "counting",
            )
        )
        row.metadata_jsonb = {**row.metadata_jsonb, "tampered": True}
    elif tamper.endswith("outbox"):
        aggregate_type = (
            "stocktake_scope" if tamper == "scope_outbox" else "stocktake_round"
        )
        row = world.db.scalar(
            select(OutboxEvent).where(OutboxEvent.aggregate_type == aggregate_type)
        )
        row.payload_jsonb = {**row.payload_jsonb, "tampered": True}
    elif tamper.endswith("audit"):
        aggregate_type = (
            "stocktake_scope" if tamper == "scope_audit" else "stocktake_round"
        )
        row = world.db.scalar(
            select(AuditEvent).where(AuditEvent.aggregate_type == aggregate_type)
        )
        row.after_jsonb = {**row.after_jsonb, "tampered": True}
    else:
        row = world.db.scalar(
            select(StocktakeDifference).where(
                StocktakeDifference.task_id == started.task_id
            )
        )
        row.reason_text = "tampered"
    world.db.flush()
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key=key,
        )
    ) == "opening_count_replay_evidence_invalid"


@pytest.mark.parametrize("extra", ["state", "outbox", "audit"])
def test_replay_rejects_extra_business_event_in_exact_set(
    world: SimpleNamespace,
    extra: str,
):
    started = _start(world, key=f"opening-idempotency-extra-{extra}")
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    key = f"opening-count-idempotency-extra-{extra}"
    _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key=key,
    )
    if extra == "state":
        world.db.add(
            StateTransitionEvent(
                aggregate_type="stocktake_scope",
                aggregate_id=str(command.scope_id),
                from_status="counting",
                to_status="completed",
                reason="opening_initial_scope_count_completed",
                actor_id=world.manager_x.user.id,
                idempotency_key=f"extra-state-{started.initial_round_id}",
                occurred_at=NOW + timedelta(seconds=1),
                metadata_jsonb={"extra": True},
                created_at=NOW + timedelta(seconds=1),
            )
        )
    elif extra == "outbox":
        world.db.add(
            OutboxEvent(
                event_type="stocktake.opening.scope_count_completed",
                aggregate_type="stocktake_scope",
                aggregate_id=str(command.scope_id),
                payload_jsonb={"extra": True},
                status="pending",
                attempts=0,
                idempotency_key=f"extra-outbox-{started.initial_round_id}",
                available_at=NOW + timedelta(seconds=1),
                locked_at=None,
                locked_by=None,
                published_at=None,
                last_error=None,
                created_at=NOW + timedelta(seconds=1),
                updated_at=NOW + timedelta(seconds=1),
            )
        )
    else:
        opening_count_service.append_audit_event(
            world.db,
            stream_key=opening_count_service.INVENTORY_STREAM_KEY,
            actor_user_id=world.manager_x.user.id,
            action="stocktake.opening.scope_count_completed",
            aggregate_type="stocktake_scope",
            aggregate_id=str(command.scope_id),
            before_jsonb=None,
            after_jsonb={"extra": True},
            request_id=f"extra-audit-{started.initial_round_id}",
            occurred_at=NOW + timedelta(seconds=1),
        )
    world.db.flush()

    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key=key,
        )
    ) == "opening_count_replay_evidence_invalid"


def test_forged_recount_round_is_failed_closed_before_any_count_fact(
    world: SimpleNamespace,
):
    started = _start(world, key="opening-idempotency-recount-closed")
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    round_row.round_no = 2
    round_row.round_type = "recount"
    world.db.flush()
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=_scope_count_command(
                world,
                started,
                observations=(
                    OpeningPhysicalObservationInput(
                        material_identifier_raw=world.material.sku_code,
                        material_identifier_type="sku_code",
                        condition_code="new",
                        availability_bucket="available",
                        counted_qty=Decimal("1"),
                    ),
                ),
            ),
            key="opening-count-idempotency-recount-closed",
        )
    ) == "opening_count_recount_evidence_invalid"
    assert all(
        world.db.scalar(select(func.count()).select_from(model)) == 0
        for model in (
            StocktakeCountLine,
            StocktakeScopeCountCompletion,
            StocktakeDifference,
            StocktakeDifferenceSetCompletion,
        )
    )


def test_historical_control_evidence_survives_source_disable_for_count_and_replay(
    world: SimpleNamespace,
):
    started = _start(world, key="opening-idempotency-source-disable-history")
    world.db.commit()
    world.source.enabled = False
    world.db.commit()
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    key = "opening-count-idempotency-source-disable-history"
    first = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key=key,
    )
    replay = _submit_scope_count(
        world,
        actor=world.principals["manager_x"],
        command=command,
        key=key,
    )
    assert first.round_sealed is True
    assert replay == replace(first, replayed=True)


def test_disabled_source_cannot_start_new_opening_stocktake(
    world: SimpleNamespace,
):
    world.source.enabled = False
    world.db.commit()
    assert _error_code(
        lambda: _start(
            world,
            key="opening-idempotency-source-disabled-new-start",
        )
    ) == "control_source_not_read_only"


def test_location_tree_and_personal_custody_drift_fail_closed(
    world: SimpleNamespace,
):
    started = _start(world, key="opening-idempotency-location-tree-drift")
    world.region_location.owner_org_id = world.region_y.id
    world.db.flush()
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=_scope_count_command(
                world,
                started,
                observations=(
                    OpeningPhysicalObservationInput(
                        material_identifier_raw=world.material.sku_code,
                        material_identifier_type="sku_code",
                        condition_code="new",
                        availability_bucket="available",
                        counted_qty=Decimal("1"),
                    ),
                ),
            ),
            key="opening-count-idempotency-location-tree-drift",
        )
    ) == "opening_count_location_tree_changed"


def test_personal_custody_drift_fails_before_count_facts(
    world: SimpleNamespace,
):
    command = replace(
        world.command,
        task_no="OPEN-X-PERSONAL-CUSTODY-DRIFT",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.personal_location.id,
                assignee_user_id=world.technician.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        command=command,
        key="opening-idempotency-personal-custody-drift",
    )
    world.custody.custodian_person_id = world.manager_x.person.id
    world.db.flush()
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["technician"],
            command=_scope_count_command(world, started, zero_confirmed=True),
            key="opening-count-idempotency-personal-custody-drift",
        )
    ) == "opening_count_custody_changed"


def test_authorization_is_rechecked_with_fresh_server_time_after_locking(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    started = _start(world, key="opening-idempotency-lock-time-auth")
    before_expiry = NOW + timedelta(hours=1)
    after_expiry = NOW + timedelta(hours=3)
    world.manager_x.assignment.valid_to = NOW + timedelta(hours=2)
    world.db.flush()
    clock_values = iter(
        (
            before_expiry,
            before_expiry,
            before_expiry,
            after_expiry,
        )
    )
    monkeypatch.setattr(
        opening_count_service,
        "_database_now",
        lambda _db: next(clock_values, after_expiry),
    )
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
            ),
        ),
    )
    with pytest.raises(OpeningStocktakeCountError) as captured:
        _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key="opening-count-idempotency-lock-time-auth",
        )
    assert captured.value.code in {
        "opening_count_actor_not_current",
        "opening_count_scope_forbidden",
        "opening_count_assignment_not_current",
    }
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion)
    ) == 0


def test_count_rechecks_authorization_after_inventory_audit_head_lock(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
):
    started = _start(world, key="opening-idempotency-count-audit-lock-auth")
    before_expiry = NOW + timedelta(hours=1)
    after_expiry = NOW + timedelta(hours=3)
    world.manager_x.assignment.valid_to = NOW + timedelta(hours=2)
    world.db.flush()
    clock = {"value": before_expiry}
    original_lock = opening_count_service._lock_audit_chain_head_with_proof

    def _delayed_audit_lock(*args, **kwargs):
        head_and_proof = original_lock(*args, **kwargs)
        clock["value"] = after_expiry
        return head_and_proof

    monkeypatch.setattr(
        opening_count_service,
        "_database_now",
        lambda _db: clock["value"],
    )
    monkeypatch.setattr(
        opening_count_service,
        "_lock_audit_chain_head_with_proof",
        _delayed_audit_lock,
    )
    command = _scope_count_command(
        world,
        started,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=world.material.sku_code,
                material_identifier_type="sku_code",
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1"),
            ),
        ),
    )

    with pytest.raises(OpeningStocktakeCountError) as captured:
        _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=command,
            key="opening-count-idempotency-audit-lock-auth",
        )
    assert captured.value.code in {
        "opening_count_actor_not_current",
        "opening_count_actor_principal_stale",
        "opening_count_scope_forbidden",
        "opening_count_assignment_not_current",
    }
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion)
    ) == 0


def test_dual_role_same_owner_prefers_manager_assignment_for_completion(
    world: SimpleNamespace,
):
    manager_assignment = RoleAssignment(
        id=uuid.uuid4(),
        user_id=world.admin.user.id,
        role_id=world.roles["provincial_manager"].id,
        scope_type="organization",
        scope_id=str(world.region_x.id),
        valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        valid_to=None,
        status="active",
        assigned_by=world.admin.user.id,
        revoked_at=None,
        revoked_by=None,
        reason="dual-role selection test",
    )
    world.db.add(manager_assignment)
    world.admin.user.authorization_version += 1
    world.db.commit()
    dual_actor = load_formal_principal(
        world.db,
        world.admin.user.id,
        now=NOW,
    )
    start_command = replace(
        world.command,
        task_no="OPEN-X-DUAL-ROLE-SAME-OWNER",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.region_location.id,
                assignee_user_id=world.admin.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        actor=dual_actor,
        command=start_command,
        key="opening-idempotency-dual-role-same-owner",
    )
    _submit_scope_count(
        world,
        actor=dual_actor,
        command=_scope_count_command(
            world,
            started,
            observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw=world.material.sku_code,
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1"),
                ),
            ),
        ),
        key="opening-count-idempotency-dual-role-same-owner",
    )
    completion = world.db.scalar(select(StocktakeScopeCountCompletion))
    assert completion.role_code == "provincial_manager"
    assert completion.completed_role_assignment_id == manager_assignment.id


def test_dual_role_national_admin_still_cannot_start_cross_region_owner(
    world: SimpleNamespace,
):
    manager_assignment = RoleAssignment(
        id=uuid.uuid4(),
        user_id=world.admin.user.id,
        role_id=world.roles["provincial_manager"].id,
        scope_type="organization",
        scope_id=str(world.region_x.id),
        valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        valid_to=None,
        status="active",
        assigned_by=world.admin.user.id,
        revoked_at=None,
        revoked_by=None,
        reason="cross-owner dual-role selection test",
    )
    world.db.add(manager_assignment)
    world.admin.user.authorization_version += 1
    world.db.commit()
    dual_actor = load_formal_principal(world.db, world.admin.user.id, now=NOW)
    start_command = replace(
        world.command,
        task_no="OPEN-X-DUAL-ROLE-CROSS-OWNER",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_y.id,
                location_id=world.region_location.id,
                assignee_user_id=world.admin.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    assert _error_code(
        lambda: _start(
            world,
            actor=dual_actor,
            command=start_command,
            key="opening-idempotency-dual-role-cross-owner",
        )
    ) == "asset_owner_outside_region"
    assert world.db.scalar(
        select(func.count()).select_from(FormalStocktakeTask)
    ) == 0


def test_count_reproves_asset_owner_remains_in_task_region_tree(
    world: SimpleNamespace,
):
    descendant = _organization(
        world.db,
        "REG-X-COUNT-SUB",
        "区域 X 计数子组织",
        "region_company",
        parent=world.region_x,
    )
    command = replace(
        world.command,
        task_no="OPEN-X-COUNT-OWNER-TREE-REPROOF",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=descendant.id,
                location_id=world.region_location.id,
                assignee_user_id=world.manager_x.user.id,
            ),
        ),
    )
    started = _start(
        world,
        command=command,
        key="opening-idempotency-owner-tree-count-reproof",
    )
    descendant.parent_id = world.region_y.id
    world.db.flush()
    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=world.principals["manager_x"],
            command=_scope_count_command(
                world,
                started,
                zero_confirmed=True,
            ),
            key="opening-count-idempotency-owner-tree-reproof",
        )
    ) == "opening_count_scope_owner_outside_region"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion)
    ) == 0


def test_global_admin_deny_blocks_manager_allow_on_selected_assignment(
    world: SimpleNamespace,
):
    manager_assignment = RoleAssignment(
        id=uuid.uuid4(),
        user_id=world.admin.user.id,
        role_id=world.roles["provincial_manager"].id,
        scope_type="organization",
        scope_id=str(world.region_x.id),
        valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
        valid_to=None,
        status="active",
        assigned_by=world.admin.user.id,
        revoked_at=None,
        revoked_by=None,
        reason="prove global deny remains effective across assignments",
    )
    world.db.add(manager_assignment)
    world.admin.user.authorization_version += 1
    world.db.commit()
    dual_actor = load_formal_principal(world.db, world.admin.user.id, now=NOW)
    start_command = replace(
        world.command,
        task_no="OPEN-X-DUAL-ROLE-GLOBAL-DENY",
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=world.region_x.id,
                location_id=world.region_location.id,
                assignee_user_id=world.admin.user.id,
                freeze_mode="hard",
            ),
        ),
    )
    started = _start(
        world,
        actor=dual_actor,
        command=start_command,
        key="opening-idempotency-dual-role-global-deny",
    )
    count_permission = world.db.scalar(
        select(Permission).where(
            Permission.resource == "stocktake",
            Permission.action == "count",
            Permission.field_code == "",
        )
    )
    admin_count = world.db.scalar(
        select(RolePermission).where(
            RolePermission.role_id == world.roles["admin"].id,
            RolePermission.permission_id == count_permission.id,
        )
    )
    admin_count.effect = "deny"
    world.admin.user.authorization_version += 1
    world.db.commit()
    denied_actor = load_formal_principal(world.db, world.admin.user.id, now=NOW)
    assert denied_actor.allows(
        world.db,
        "stocktake",
        "count",
        target_scope_type="organization",
        target_scope_id=str(world.region_x.id),
    ) is False

    assert _count_error_code(
        lambda: _submit_scope_count(
            world,
            actor=denied_actor,
            command=_scope_count_command(
                world,
                started,
                observations=(
                    OpeningPhysicalObservationInput(
                        material_identifier_raw=world.material.sku_code,
                        material_identifier_type="sku_code",
                        condition_code="new",
                        availability_bucket="available",
                        counted_qty=Decimal("1"),
                    ),
                ),
            ),
            key="opening-count-idempotency-dual-role-global-deny",
        )
    ) == "opening_count_scope_forbidden"
    assert world.db.scalar(
        select(func.count()).select_from(StocktakeScopeCountCompletion)
    ) == 0
