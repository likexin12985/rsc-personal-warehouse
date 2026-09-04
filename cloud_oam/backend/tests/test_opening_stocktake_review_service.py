from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import inspect
from itertools import count
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine, event, func, select
from sqlalchemy.orm import Session

import app.formal_services.inventory_posting as posting_service
import app.formal_services.opening_stocktake as opening_service
import app.formal_services.opening_stocktake_count as count_service
import app.formal_services.opening_observation_disposition as disposition_service
import app.formal_services.opening_stocktake_review as review_service
from app.database import Base
from app.formal_access import FormalPrincipal, load_formal_principal
from app.formal_services.inventory_posting import (
    canonical_opening_count_manifest_sha256,
    canonical_opening_decision_manifest_sha256,
)
from app.formal_services.opening_stocktake import (
    INVENTORY_LEDGER_HEAD_ID,
    OPENING_CONTROL_ENTITY_TYPE,
    OpeningControlLineInput,
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
    SubmitOpeningStocktakeScopeCountCommand,
    submit_opening_stocktake_scope_count,
)
from app.formal_services.opening_observation_disposition import (
    RecordOpeningObservationDispositionCommand,
    record_opening_observation_disposition,
)
from app.formal_services.opening_stocktake_review import (
    OpeningStocktakeReviewError,
    OpeningStocktakeReviewItemInput,
    SubmitOpeningStocktakeReviewCommand,
    submit_opening_headquarters_review,
    submit_opening_region_review,
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
    InventoryLedgerHead,
    InventoryMovement,
    InventoryTransaction,
    MaterialInventoryPolicy,
    StockAccount,
    StockBalance,
    StockLocation,
)
from app.models import User
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
    StocktakeObservationDisposition,
    StocktakePosting,
    StocktakePostingItem,
    StocktakeReview,
    StocktakeReviewItem,
    StocktakeRound,
)


NOW = datetime(2026, 8, 30, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fixed_database_times(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(opening_service, "_database_now", lambda _db: NOW)
    monkeypatch.setattr(
        count_service,
        "_database_now",
        lambda _db: NOW + timedelta(hours=1),
    )
    monkeypatch.setattr(
        disposition_service,
        "_database_now",
        lambda _db: NOW + timedelta(hours=1, minutes=30),
    )
    review_ticks = count()
    monkeypatch.setattr(
        review_service,
        "_database_now",
        lambda _db: NOW
        + timedelta(hours=2, microseconds=next(review_ticks)),
    )


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record) -> None:
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
    permissions = {
        action: Permission(
            id=uuid.uuid4(),
            resource="stocktake",
            action=action,
            field_code="",
            description=action,
        )
        for action in (
            "manage",
            "count",
            "review_region",
            "review_headquarters",
        )
    }
    db.add_all([*roles.values(), *permissions.values()])
    db.flush()
    grants = {
        "admin": ("manage", "count", "review_headquarters"),
        "provincial_manager": ("manage", "count", "review_region"),
        "technician": ("count",),
    }
    db.add_all(
        [
            RolePermission(
                role_id=roles[role_code].id,
                permission_id=permissions[action].id,
                effect="allow",
            )
            for role_code, actions in grants.items()
            for action in actions
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

    material = _material(db, source)
    location = StockLocation(
        id=uuid.uuid4(),
        code="REG-X-WH",
        name="区域 X 仓",
        location_type="region",
        owner_org_id=region_x.id,
        parent_id=None,
        custodian_person_id=None,
        status="active",
    )
    account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=region_x.id,
        custodian_person_id=None,
        location_id=location.id,
        material_id=material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
    )
    db.add(location)
    db.flush()
    db.add(account)
    db.flush()
    db.add(
        StockBalance(
            stock_account_id=account.id,
            quantity=Decimal("0.000"),
            ledger_cursor=0,
            version=0,
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
        control_qty=Decimal("5.000"),
    )
    db.commit()
    principals = {
        "admin": load_formal_principal(db, admin.user.id, now=NOW),
        "manager_x": load_formal_principal(db, manager_x.user.id, now=NOW),
        "manager_y": load_formal_principal(db, manager_y.user.id, now=NOW),
        "technician": load_formal_principal(db, technician.user.id, now=NOW),
    }
    command = StartOpeningStocktakeCommand(
        task_no="OPEN-REVIEW-X-001",
        region_org_id=region_x.id,
        control_source_system_id=source.id,
        control_sync_run_id=control.sync_run.id,
        control_sync_scope_key=control.sync_run.scope_key,
        scopes=(
            OpeningStocktakeScopeInput(
                owner_org_id=region_x.id,
                location_id=location.id,
                assignee_user_id=manager_x.user.id,
                freeze_mode="hard",
            ),
        ),
        control_lines=control.lines,
        blind_count=True,
        deadline=NOW + timedelta(days=2),
        note="两级复核专项测试",
    )
    return SimpleNamespace(
        db=db,
        hq=hq,
        region_x=region_x,
        region_y=region_y,
        source=source,
        roles=roles,
        permissions=permissions,
        admin=admin,
        manager_x=manager_x,
        manager_y=manager_y,
        technician=technician,
        principals=principals,
        material=material,
        location=location,
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
        parent_id=parent.id if parent is not None else None,
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


def _material(db: Session, source: SourceSystem) -> FormalMaterial:
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
        tracking_mode="none",
        quantity_scale=3,
        allow_fraction=True,
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
    control_qty: Decimal,
) -> SimpleNamespace:
    sync_run_id = uuid.uuid4()
    batch_id = uuid.uuid4()
    object_id = uuid.uuid4()
    version_id = uuid.uuid4()
    event_id = uuid.uuid4()
    business_key = "CTRL-REVIEW-1"
    source_updated_at = NOW - timedelta(hours=2)
    payload = opening_control_projection_payload(
        external_business_key=business_key,
        region_org_id=region.id,
        material_id=material.id,
        condition_code="new",
        control_qty=control_qty,
        mapping_status="resolved",
        mapping_note="",
    )
    payload_hash = canonical_opening_manifest_sha256(payload)
    line = OpeningControlLineInput(
        sync_inbox_event_id=event_id,
        external_object_version_id=version_id,
        external_business_key=business_key,
        material_id=material.id,
        condition_code="new",
        control_qty=control_qty,
        mapping_status="resolved",
        source_updated_at=source_updated_at,
        payload_sha256=payload_hash,
        mapping_note="",
    )
    scope_key = f"oam_inventory_control:region:{region.id}"
    manifest = opening_control_manifest_sha256(
        source_system_id=source.id,
        sync_run_id=sync_run_id,
        sync_scope_key=scope_key,
        region_org_id=region.id,
        lines=(line,),
    )
    batch_hash = opening_control_batch_body_sha256(
        sequence=1,
        events=(
            {
                "event_sort_key": str(event_id),
                "external_event_id": "event-CTRL-REVIEW-1",
                "external_id": business_key,
                "payload_sha256": payload_hash,
                "source_updated_at": source_updated_at.isoformat(
                    timespec="microseconds"
                ).replace("+00:00", "Z"),
                "source_version": "v1",
            },
        ),
    )
    sync_run = SyncRun(
        id=sync_run_id,
        source_system_id=source.id,
        run_key=f"opening-review-{uuid.uuid4().hex}",
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
        record_count=1,
        body_sha256=batch_hash,
        status="applied",
        received_at=NOW - timedelta(hours=2),
        validated_at=NOW - timedelta(hours=1, minutes=30),
    )
    db.add(sync_run)
    db.flush()
    db.add(batch)
    db.flush()
    external = ExternalObject(
        id=object_id,
        source_system_id=source.id,
        entity_type=OPENING_CONTROL_ENTITY_TYPE,
        external_id=business_key,
        current_version_id=version_id,
        deleted_at=None,
    )
    db.add(external)
    db.flush()
    db.add_all(
        [
            ExternalObjectVersion(
                id=version_id,
                external_object_id=external.id,
                source_version="v1",
                source_updated_at=source_updated_at,
                valid_from=NOW - timedelta(hours=2),
                valid_to=None,
                payload_jsonb=payload,
                payload_sha256=payload_hash,
                is_current=True,
            ),
            SyncInboxEvent(
                id=event_id,
                batch_id=batch.id,
                source_system_id=source.id,
                external_event_id="event-CTRL-REVIEW-1",
                entity_type=OPENING_CONTROL_ENTITY_TYPE,
                external_id=business_key,
                source_version="v1",
                source_updated_at=source_updated_at,
                payload_jsonb=payload,
                payload_sha256=payload_hash,
                status="applied",
                error_code=None,
                error_detail=None,
                processed_at=NOW - timedelta(hours=1),
            ),
        ]
    )
    db.flush()
    return SimpleNamespace(sync_run=sync_run, batch=batch, lines=(line,))


def _prepare_submitted(
    world: SimpleNamespace,
    *,
    start_actor: FormalPrincipal | None = None,
    count_actor: FormalPrincipal | None = None,
    command: StartOpeningStocktakeCommand | None = None,
    observations: tuple[OpeningPhysicalObservationInput, ...] | None = None,
) -> SimpleNamespace:
    started = start_opening_stocktake(
        world.db,
        actor=start_actor or world.principals["manager_x"],
        command=command or world.command,
        idempotency_key=f"opening-review-start-{uuid.uuid4().hex}",
        request_id="opening-review-start-request",
    )
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == started.task_id
        )
    )
    assert scope is not None
    counted = submit_opening_stocktake_scope_count(
        world.db,
        actor=count_actor or world.principals["manager_x"],
        command=SubmitOpeningStocktakeScopeCountCommand(
            task_id=started.task_id,
            round_id=started.initial_round_id,
            scope_id=scope.id,
            physical_observations=observations
            or (
                OpeningPhysicalObservationInput(
                    material_identifier_raw=world.material.sku_code,
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("2.000"),
                    count_method="manual",
                ),
            ),
            zero_confirmed=False,
        ),
        idempotency_key=f"opening-review-count-{uuid.uuid4().hex}",
        request_id="opening-review-count-request",
    )
    assert counted.round_sealed is True
    world.db.commit()
    task = world.db.get(FormalStocktakeTask, started.task_id)
    round_row = world.db.get(StocktakeRound, started.initial_round_id)
    assert task is not None and task.status == "submitted"
    assert round_row is not None and round_row.status == "submitted"
    return SimpleNamespace(started=started, scope=scope, task=task, round=round_row)


def _prepare_observation(
    world: SimpleNamespace,
    *,
    material_identifier_raw: str,
    material_identifier_type: str = "sku_code",
) -> SimpleNamespace:
    prepared = _prepare_submitted(
        world,
        observations=(
            OpeningPhysicalObservationInput(
                material_identifier_raw=material_identifier_raw,
                material_identifier_type=material_identifier_type,
                condition_code="new",
                availability_bucket="available",
                counted_qty=Decimal("1.000"),
                count_method="manual",
                remark="观察处置复核集成测试",
            ),
        ),
    )
    observation = world.db.scalar(
        select(StocktakeCountObservation).where(
            StocktakeCountObservation.task_id == prepared.task.id,
            StocktakeCountObservation.round_id == prepared.round.id,
            StocktakeCountObservation.material_identifier_raw
            == material_identifier_raw,
        )
    )
    assert observation is not None
    difference = world.db.scalar(
        select(StocktakeDifference).where(
            StocktakeDifference.task_id == prepared.task.id,
            StocktakeDifference.round_id == prepared.round.id,
            StocktakeDifference.observed_line_id == observation.id,
        )
    )
    assert difference is not None
    return SimpleNamespace(
        **prepared.__dict__,
        observation=observation,
        observation_difference=difference,
    )


def _record_observation_disposition(
    world: SimpleNamespace,
    prepared: SimpleNamespace,
    *,
    disposition: str,
    actor_name: str = "manager_x",
    resolved_material_id: uuid.UUID | None = None,
) -> StocktakeObservationDisposition:
    result = record_opening_observation_disposition(
        world.db,
        actor=world.principals[actor_name],
        command=RecordOpeningObservationDispositionCommand(
            task_id=prepared.task.id,
            round_id=prepared.round.id,
            observation_id=prepared.observation.id,
            disposition=disposition,
            reason_code=f"test_{disposition}",
            comment=(
                ""
                if disposition == "resolved_existing_master"
                else "保留证据并按处置要求继续"
            ),
            resolved_material_id=resolved_material_id,
        ),
        idempotency_key=f"opening-observation-disposition-{uuid.uuid4().hex}",
        request_id=f"observation-disposition-{uuid.uuid4().hex}",
    )
    row = world.db.get(StocktakeObservationDisposition, result.disposition_id)
    assert row is not None
    return row


def _review_command(
    db: Session,
    prepared: SimpleNamespace,
    *,
    decision: str = "approve",
    comment: str = "复核通过",
) -> SubmitOpeningStocktakeReviewCommand:
    differences = db.scalars(
        select(StocktakeDifference)
        .where(
            StocktakeDifference.task_id == prepared.task.id,
            StocktakeDifference.round_id == prepared.round.id,
        )
        .order_by(StocktakeDifference.difference_no)
    ).all()
    normal_decision = {
        "approve": "accept_for_posting",
        "recount": "recount",
        "reject": "reject",
    }[decision]
    observations = {
        row.id: row
        for row in db.scalars(
            select(StocktakeCountObservation).where(
                StocktakeCountObservation.task_id == prepared.task.id,
                StocktakeCountObservation.round_id == prepared.round.id,
            )
        ).all()
    }
    dispositions = {
        row.observation_id: row
        for row in db.scalars(
            select(StocktakeObservationDisposition).where(
                StocktakeObservationDisposition.task_id == prepared.task.id,
                StocktakeObservationDisposition.round_id == prepared.round.id,
            )
        ).all()
    }

    def _item_decision(row: StocktakeDifference) -> str:
        if row.difference_type == "control_unassigned":
            return "pending_verification"
        if row.observed_line_id is not None:
            observation = observations[row.observed_line_id]
            disposition = dispositions.get(observation.id)
            if (
                disposition is not None
                and disposition.disposition == "pending_verification"
            ):
                return "pending_verification"
        return normal_decision

    return SubmitOpeningStocktakeReviewCommand(
        task_id=prepared.task.id,
        round_id=prepared.round.id,
        decision=decision,
        items=tuple(
            OpeningStocktakeReviewItemInput(
                difference_id=row.id,
                decision=_item_decision(row),
                comment=(
                    "OAM 省级控制差异继续待核实"
                    if row.difference_type == "control_unassigned"
                    else (
                        "现场观察继续待核实"
                        if _item_decision(row) == "pending_verification"
                        else ""
                    )
                ),
            )
            for row in differences
        ),
        comment=comment,
    )


def _review_error(callable_) -> str:
    with pytest.raises(OpeningStocktakeReviewError) as captured:
        callable_()
    return captured.value.code


def _stock_fact_counts(db: Session) -> dict[type, int]:
    return {
        model: db.scalar(select(func.count()).select_from(model))
        for model in (
            StockAccount,
            StockBalance,
            InventoryTransaction,
            InventoryMovement,
            StocktakePosting,
            InventoryOpeningEstablishment,
        )
    }


def _review_effect_counts(db: Session) -> dict[type, int]:
    return {
        model: db.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            StocktakeReview,
            StocktakeReviewItem,
            StateTransitionEvent,
            OutboxEvent,
            AuditEvent,
            StockAccount,
            StockBalance,
            InventoryTransaction,
            InventoryMovement,
            StocktakePosting,
            InventoryOpeningEstablishment,
        )
    }


def _establish_posted_opening_for_review_replay(
    world: SimpleNamespace,
    prepared: SimpleNamespace,
    *,
    status: str,
) -> SimpleNamespace:
    """Build the real immutable opening graph expected by the replay guard."""

    assert status in {"posted", "closed"}
    db = world.db
    task = db.get(FormalStocktakeTask, prepared.task.id)
    round_row = db.get(StocktakeRound, prepared.round.id)
    assert task is not None and round_row is not None
    reviews = db.scalars(
        select(StocktakeReview)
        .where(StocktakeReview.task_id == task.id)
        .order_by(StocktakeReview.reviewed_at)
    ).all()
    assert [row.review_stage for row in reviews] == ["region", "headquarters"]
    regional_review, headquarters_review = reviews
    count_lines = db.scalars(
        select(StocktakeCountLine)
        .where(
            StocktakeCountLine.task_id == task.id,
            StocktakeCountLine.round_id == round_row.id,
        )
        .order_by(StocktakeCountLine.scope_id, StocktakeCountLine.stock_account_id)
    ).all()
    assert count_lines
    total_quantity = sum(
        (row.counted_qty for row in count_lines if row.counted_qty > 0),
        start=Decimal("0.000"),
    )
    assert total_quantity > 0

    posted_at = NOW + timedelta(hours=3)
    task.status = status
    task.posted_at = posted_at
    task.closed_at = posted_at + timedelta(minutes=1) if status == "closed" else None
    task.updated_at = task.closed_at or posted_at

    freeze = db.scalar(
        select(InventoryFreeze).where(InventoryFreeze.task_id == task.id)
    )
    assert freeze is not None
    freeze.status = "released"
    freeze.valid_to = posted_at
    freeze.released_by_user_id = world.admin.user.id
    freeze.release_reason = "期初入账已完成"
    freeze.updated_at = posted_at

    # The fixture creates the OAM mirror immediately.  Move only its metadata
    # timestamps into the already-frozen business timeline so that the replay
    # guard can prove the same historical ordering required in PostgreSQL.
    sync_run = db.get(SyncRun, task.control_sync_run_id)
    assert sync_run is not None
    sync_run.created_at = sync_run.started_at
    batches = db.scalars(
        select(SyncBatch).where(SyncBatch.run_id == sync_run.id)
    ).all()
    for batch in batches:
        batch.created_at = batch.received_at
    control_lines = db.scalars(
        select(StocktakeControlSnapshotLine).where(
            StocktakeControlSnapshotLine.task_id == task.id
        )
    ).all()
    for line in control_lines:
        assert line.external_object_version_id is not None
        version = db.get(ExternalObjectVersion, line.external_object_version_id)
        assert version is not None
        external = db.get(ExternalObject, version.external_object_id)
        assert external is not None
        external.created_at = sync_run.started_at
        version.created_at = version.valid_from
    inbox_events = db.scalars(
        select(SyncInboxEvent).join(
            SyncBatch, SyncBatch.id == SyncInboxEvent.batch_id
        ).where(SyncBatch.run_id == sync_run.id)
    ).all()
    for inbox_event in inbox_events:
        inbox_event.created_at = inbox_event.source_updated_at
    world.account.created_at = task.cutoff_at

    head = db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID)
    assert head is not None
    ledger_cursor = head.next_cursor
    head.next_cursor += 1
    token = uuid.uuid4().hex
    transaction = InventoryTransaction(
        id=uuid.uuid4(),
        transaction_no=f"OPEN-REVIEW-TX-{token}",
        movement_type="opening",
        source_document_type="opening_stocktake",
        source_document_id=str(task.id),
        posting_key=f"opening-stocktake:{task.id}",
        idempotency_key_hash=hashlib.sha256(
            f"opening-review-transaction:{token}".encode()
        ).hexdigest(),
        request_hash=hashlib.sha256(
            f"opening-review-request:{token}".encode()
        ).hexdigest(),
        status="posted",
        effective_at=task.cutoff_at,
        posted_at=posted_at,
        ledger_cursor=ledger_cursor,
        reversed_transaction_id=None,
        actor_user_id=world.admin.user.id,
        created_at=posted_at,
    )
    db.add(transaction)
    db.flush()

    movements: list[InventoryMovement] = []
    for line_no, count_line in enumerate(count_lines, start=1):
        if count_line.counted_qty <= 0:
            continue
        movement = InventoryMovement(
            id=uuid.uuid4(),
            transaction_id=transaction.id,
            line_no=len(movements) + 1,
            from_account_id=None,
            to_account_id=count_line.stock_account_id,
            external_boundary_code="approved-opening-stocktake",
            quantity=count_line.counted_qty,
            created_at=posted_at,
        )
        movements.append(movement)
        db.add(movement)
        db.flush()
    assert movements

    posting = StocktakePosting(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        posting_kind="opening",
        inventory_transaction_id=transaction.id,
        total_quantity=total_quantity,
        idempotency_key_hash=hashlib.sha256(
            f"opening-review-posting:{token}".encode()
        ).hexdigest(),
        request_hash=hashlib.sha256(
            f"opening-review-posting-request:{token}".encode()
        ).hexdigest(),
        posted_by_user_id=world.admin.user.id,
        posted_at=posted_at,
        created_at=posted_at,
    )
    db.add(posting)
    db.flush()
    positive_lines = [row for row in count_lines if row.counted_qty > 0]
    assert len(positive_lines) == len(movements)
    db.add_all(
        [
            StocktakePostingItem(
                posting_id=posting.id,
                inventory_movement_id=movement.id,
                task_id=task.id,
                round_id=round_row.id,
                count_line_id=count_line.id,
                difference_id=None,
                quantity=count_line.counted_qty,
                created_at=posted_at,
            )
            for count_line, movement in zip(positive_lines, movements, strict=True)
        ]
    )

    balance = db.get(StockBalance, world.account.id)
    assert balance is not None
    balance.quantity = total_quantity
    balance.ledger_cursor = ledger_cursor
    balance.version += 1
    balance.updated_at = posted_at
    differences = db.scalars(
        select(StocktakeDifference).where(
            StocktakeDifference.task_id == task.id,
            StocktakeDifference.round_id == round_row.id,
        )
    ).all()
    establishment = InventoryOpeningEstablishment(
        id=uuid.uuid4(),
        task_id=task.id,
        scope_id=prepared.scope.id,
        owner_org_id=prepared.scope.owner_org_id,
        location_id=prepared.scope.location_id,
        round_id=round_row.id,
        posting_id=posting.id,
        regional_review_id=regional_review.id,
        headquarters_review_id=headquarters_review.id,
        cutoff_ledger_cursor=task.cutoff_ledger_cursor,
        cutoff_at=task.cutoff_at,
        established_ledger_cursor=ledger_cursor,
        scope_manifest_sha256=task.scope_manifest_sha256,
        snapshot_manifest_sha256=task.snapshot_manifest_sha256,
        count_manifest_sha256=round_row.count_manifest_sha256,
        control_manifest_sha256=task.control_manifest_sha256,
        has_pending_control_difference=any(
            row.difference_type == "control_unassigned" for row in differences
        ),
        established_by_user_id=world.admin.user.id,
        established_at=posted_at,
        created_at=posted_at,
    )
    db.add(establishment)
    db.commit()
    return SimpleNamespace(
        posting=posting,
        establishment=establishment,
        transaction=transaction,
        movements=tuple(movements),
    )


def test_two_level_approval_is_separate_and_never_posts_inventory(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_submitted(world)
    db = world.db
    command = _review_command(db, prepared)
    before_stock = _stock_fact_counts(db)
    before_state = db.scalar(select(func.count()).select_from(StateTransitionEvent))
    before_outbox = db.scalar(select(func.count()).select_from(OutboxEvent))
    before_audit = db.scalar(select(func.count()).select_from(AuditEvent))

    region = submit_opening_region_review(
        db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key="opening-region-review-0001",
        request_id="region-review-request-0001",
    )
    assert region.review_stage == "region"
    assert region.resulting_task_status == "hq_review"
    assert region.pending_control_count == 1
    assert db.get(FormalStocktakeTask, prepared.task.id).status == "hq_review"

    headquarters = submit_opening_headquarters_review(
        db,
        actor=world.principals["admin"],
        command=command,
        idempotency_key="opening-headquarters-review-0001",
        request_id="headquarters-review-request-0001",
    )
    assert headquarters.review_stage == "headquarters"
    assert headquarters.resulting_task_status == "approved"
    assert db.get(FormalStocktakeTask, prepared.task.id).status == "approved"

    reviews = db.scalars(
        select(StocktakeReview)
        .where(StocktakeReview.task_id == prepared.task.id)
        .order_by(StocktakeReview.reviewed_at)
    ).all()
    assert [row.review_stage for row in reviews] == ["region", "headquarters"]
    assert reviews[0].reviewer_user_id != reviews[1].reviewer_user_id
    assert reviews[0].reviewer_person_id != reviews[1].reviewer_person_id
    assert reviews[0].reviewed_at < reviews[1].reviewed_at
    for review in reviews:
        verified = review_service.validate_opening_review_evidence_for_replay(
            db,
            task_id=prepared.task.id,
            round_id=prepared.round.id,
            review_id=review.id,
            expected_stage=review.review_stage,
            expected_decision=review.decision,
        )
        assert verified.id == review.id
    differences = db.scalars(
        select(StocktakeDifference)
        .where(StocktakeDifference.task_id == prepared.task.id)
        .order_by(StocktakeDifference.difference_no)
    ).all()
    for review in reviews:
        items = db.scalars(
            select(StocktakeReviewItem)
            .where(StocktakeReviewItem.review_id == review.id)
            .order_by(StocktakeReviewItem.difference_id)
        ).all()
        decisions = {row.difference_id: row.decision for row in items}
        assert set(decisions) == {row.id for row in differences}
        assert review.decision_manifest_sha256 == (
            canonical_opening_decision_manifest_sha256(
                task_id=prepared.task.id,
                round_id=prepared.round.id,
                differences=differences,
                decisions=decisions,
            )
        )
        control_items = [
            row
            for row in items
            if next(
                value for value in differences if value.id == row.difference_id
            ).difference_type
            == "control_unassigned"
        ]
        assert len(control_items) == 1
        assert control_items[0].decision == "pending_verification"
        assert control_items[0].comment

    count_lines = db.scalars(
        select(StocktakeCountLine).where(
            StocktakeCountLine.task_id == prepared.task.id
        )
    ).all()
    count_serials = db.scalars(
        select(StocktakeCountSerial).where(
            StocktakeCountSerial.round_id == prepared.round.id
        )
    ).all()
    assert prepared.round.count_manifest_sha256 == (
        canonical_opening_count_manifest_sha256(
            prepared.task,
            prepared.round,
            count_lines,
            count_serials,
        )
    )
    assert _stock_fact_counts(db) == before_stock
    assert db.scalar(select(func.count()).select_from(StateTransitionEvent)) == (
        before_state + 2
    )
    assert db.scalar(select(func.count()).select_from(OutboxEvent)) == (
        before_outbox + 2
    )
    assert db.scalar(select(func.count()).select_from(AuditEvent)) == before_audit + 2
    assert db.scalars(
        select(StateTransitionEvent)
        .where(
            StateTransitionEvent.aggregate_id == str(prepared.task.id),
            StateTransitionEvent.reason.like("opening_%_review_approve"),
        )
        .order_by(StateTransitionEvent.occurred_at)
    ).all()[0].to_status == "hq_review"


def test_region_review_flushes_without_commit_and_replay_is_read_only(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_submitted(world)
    command = _review_command(world.db, prepared)
    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key="opening-region-review-replay",
        request_id="region-review-replay-request",
    )
    assert result.replayed is False
    assert world.db.get(StocktakeReview, result.review_id) is not None
    world.db.rollback()
    assert world.db.get(StocktakeReview, result.review_id) is None
    assert world.db.get(FormalStocktakeTask, prepared.task.id).status == "submitted"

    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key="opening-region-review-replay",
        request_id="region-review-replay-request",
    )
    world.db.commit()
    manager = world.db.get(User, world.manager_x.user.id)
    manager.authorization_version += 1
    world.db.commit()
    current = load_formal_principal(
        world.db,
        manager.id,
        now=NOW + timedelta(hours=2),
    )
    before = {
        model: world.db.scalar(select(func.count()).select_from(model))
        for model in (StocktakeReview, StocktakeReviewItem, StateTransitionEvent, OutboxEvent, AuditEvent)
    }
    replay = submit_opening_region_review(
        world.db,
        actor=current,
        command=command,
        idempotency_key="opening-region-review-replay",
        request_id="region-review-replay-request-new-transport",
    )
    assert replay.review_id == result.review_id
    assert replay.replayed is True
    assert {
        model: world.db.scalar(select(func.count()).select_from(model))
        for model in before
    } == before
    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=current,
            command=replace(command, comment="另一请求"),
            idempotency_key="opening-region-review-replay",
            request_id="region-review-conflict-request",
        )
    ) == "opening_review_idempotency_conflict"


def test_effective_scheduled_reviewer_assignment_survives_historical_replay(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_submitted(world)
    assignment = world.db.get(RoleAssignment, world.manager_x.assignment.id)
    assert assignment is not None
    assert review_service._as_optional_utc(assignment.valid_from) < NOW + timedelta(
        hours=2
    )
    assignment.status = "scheduled"
    world.db.flush()
    scheduled_actor = load_formal_principal(
        world.db,
        world.manager_x.user.id,
        now=NOW + timedelta(hours=2),
    )

    result = submit_opening_region_review(
        world.db,
        actor=scheduled_actor,
        command=_review_command(world.db, prepared),
        idempotency_key="opening-region-review-effective-scheduled",
        request_id="region-review-effective-scheduled-request",
    )
    review = world.db.get(StocktakeReview, result.review_id)
    assert review is not None
    assert assignment.status == "scheduled"
    assert review_service._as_optional_utc(
        assignment.valid_from
    ) <= review_service._as_optional_utc(review.reviewed_at)

    verified = review_service.validate_opening_review_evidence_for_replay(
        world.db,
        task_id=prepared.task.id,
        round_id=prepared.round.id,
        review_id=review.id,
        expected_stage=review.review_stage,
        expected_decision=review.decision,
    )

    assert verified.id == review.id


def test_future_scheduled_reviewer_assignment_fails_historical_replay(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_submitted(world)
    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(world.db, prepared),
        idempotency_key="opening-region-review-future-scheduled",
        request_id="region-review-future-scheduled-request",
    )
    review = world.db.get(StocktakeReview, result.review_id)
    assignment = world.db.get(RoleAssignment, world.manager_x.assignment.id)
    assert review is not None
    assert assignment is not None
    assignment.status = "scheduled"
    assignment.valid_from = review_service._as_optional_utc(
        review.reviewed_at
    ) + timedelta(microseconds=1)
    world.db.flush()

    assert _review_error(
        lambda: review_service.validate_opening_review_evidence_for_replay(
            world.db,
            task_id=prepared.task.id,
            round_id=prepared.round.id,
            review_id=review.id,
            expected_stage=review.review_stage,
            expected_decision=review.decision,
        )
    ) == "opening_review_replay_evidence_invalid"


def test_review_locks_one_complete_0027_reference_graph_in_order(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare_submitted(world)
    calls: list[str] = []
    monkeypatch.setattr(
        disposition_service,
        "lock_opening_stocktake_start_reference",
        lambda *_args, **_kwargs: calls.append("start"),
    )
    monkeypatch.setattr(
        disposition_service,
        "lock_inventory_reference_graph",
        lambda *_args, **_kwargs: calls.append("inventory"),
    )
    monkeypatch.setattr(
        disposition_service,
        "lock_inventory_serial_graph",
        lambda *_args, **_kwargs: calls.append("serial"),
    )

    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(world.db, prepared),
        idempotency_key="opening-region-review-single-reference-graph",
        request_id="region-review-single-reference-graph-request",
    )

    assert result.replayed is False
    assert calls == ["start", "inventory", "serial"]


def test_review_locks_historical_disposer_principal_before_evidence_and_references(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare_observation(
        world,
        material_identifier_raw="REVIEW-HISTORICAL-DISPOSER-LOCK",
    )
    _record_observation_disposition(
        world,
        prepared,
        disposition="requires_recount",
        actor_name="admin",
    )
    events: list[str] = []
    locked_user_ids: set[str] = set()
    real_principal = review_service.lock_formal_principal_graph
    real_evidence = review_service.lock_opening_stocktake_task_evidence
    real_references = review_service._lock_review_reference_graph

    def record_principal(session, user_ids):
        events.append("principal")
        locked_user_ids.update(user_ids)
        return real_principal(session, user_ids)

    def record_evidence(*args, **kwargs):
        events.append("evidence")
        return real_evidence(*args, **kwargs)

    def record_references(*args, **kwargs):
        events.append("references")
        return real_references(*args, **kwargs)

    monkeypatch.setattr(
        review_service,
        "lock_formal_principal_graph",
        record_principal,
    )
    monkeypatch.setattr(
        review_service,
        "lock_opening_stocktake_task_evidence",
        record_evidence,
    )
    monkeypatch.setattr(
        review_service,
        "_lock_review_reference_graph",
        record_references,
    )

    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            prepared,
            decision="recount",
            comment="历史处置权限锁序复核",
        ),
        idempotency_key="opening-region-review-historical-disposer-lock",
        request_id="region-review-historical-disposer-lock-request",
    )

    assert result.resulting_task_status == "recount_required"
    assert world.principals["admin"].user_id in locked_user_ids
    assert world.principals["manager_x"].user_id in locked_user_ids
    assert events.index("principal") < events.index("evidence") < events.index(
        "references"
    )


def test_review_reuses_final_audit_proof_for_disposition_replay(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare_observation(
        world,
        material_identifier_raw="REVIEW-DISPOSITION-AUDIT-PROOF",
    )
    _record_observation_disposition(
        world,
        prepared,
        disposition="requires_recount",
        actor_name="admin",
    )
    audit_calls: list[str] = []
    real_audit = review_service._lock_audit_chain_head_with_proof

    def record_final_audit(*args, **kwargs):
        audit_calls.append("audit")
        return real_audit(*args, **kwargs)

    def forbidden_disposition_audit_lock(*_args, **_kwargs):
        pytest.fail(
            "review disposition planning/pure replay must reuse the review audit proof"
        )

    monkeypatch.setattr(
        review_service,
        "_lock_audit_chain_head_with_proof",
        record_final_audit,
    )
    monkeypatch.setattr(
        disposition_service,
        "_lock_audit_chain_head_with_proof",
        forbidden_disposition_audit_lock,
    )

    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            prepared,
            decision="recount",
            comment="复用复核最终审计证明",
        ),
        idempotency_key="opening-region-disposition-audit-proof",
        request_id="region-disposition-audit-proof-request",
    )

    assert result.resulting_task_status == "recount_required"
    assert audit_calls == ["audit"]
    planner_source = inspect.getsource(
        review_service._plan_opening_review_evidence
    )
    observation_source = inspect.getsource(
        review_service._validate_observation_review_graph
    )
    assert "_lock_audit_chain_head_with_proof" not in planner_source
    assert "_lock_audit_chain_head_with_proof" not in observation_source
    assert (
        "_validate_opening_observation_disposition_replay_from_prelocked_audit_graph"
        in observation_source
    )


def test_public_review_replay_locks_task_principals_evidence_refs_then_audit(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare_submitted(world)
    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(world.db, prepared),
        idempotency_key="opening-region-public-replay-lock-order",
        request_id="region-public-replay-lock-order-request",
    )
    review = world.db.get(StocktakeReview, result.review_id)
    assert review is not None
    events: list[str] = []
    real_principal = posting_service._lock_opening_task_principal_graph
    real_evidence = review_service.lock_opening_stocktake_task_evidence
    real_references = review_service._lock_review_reference_graph
    real_audit = review_service._lock_audit_chain_head_with_proof

    def record_principal(*args, **kwargs):
        events.append("principal")
        return real_principal(*args, **kwargs)

    def record_evidence(*args, **kwargs):
        events.append("evidence")
        return real_evidence(*args, **kwargs)

    def record_references(*args, **kwargs):
        events.append("references")
        return real_references(*args, **kwargs)

    def record_audit(*args, **kwargs):
        events.append("audit")
        return real_audit(*args, **kwargs)

    monkeypatch.setattr(
        posting_service,
        "_lock_opening_task_principal_graph",
        record_principal,
    )
    monkeypatch.setattr(
        review_service,
        "lock_opening_stocktake_task_evidence",
        record_evidence,
    )
    monkeypatch.setattr(
        review_service,
        "_lock_review_reference_graph",
        record_references,
    )
    monkeypatch.setattr(
        review_service,
        "_lock_audit_chain_head_with_proof",
        record_audit,
    )

    verified = review_service.validate_opening_review_evidence_for_replay(
        world.db,
        task_id=prepared.task.id,
        round_id=prepared.round.id,
        review_id=review.id,
        expected_stage=review.review_stage,
        expected_decision=review.decision,
    )

    assert verified.id == review.id
    assert events[:4] == ["principal", "evidence", "references", "audit"]
    source = inspect.getsource(
        review_service.validate_opening_review_evidence_for_replay
    )
    assert tuple(
        inspect.signature(
            review_service.validate_opening_recount_trigger_review_graph
        ).parameters
    ) == ("db", "task_id", "round_id")
    trigger_source = inspect.getsource(
        review_service.validate_opening_recount_trigger_review_graph
    )
    assert trigger_source.index(
        "_plan_opening_recount_trigger_review_graph_from_prelocked_reference_graph"
    ) < trigger_source.index(
        "_lock_audit_chain_head_with_proof"
    ) < trigger_source.index(
        "_validate_opening_recount_trigger_review_graph_from_prelocked_task_graph"
    )
    assert "audit_head_prelocked" not in inspect.getsource(review_service)
    assert "verify_audit_event_in_stream" not in inspect.getsource(review_service)
    assert source.index(".with_for_update()") < source.index(
        "_lock_opening_task_principal_graph"
    )


def test_internal_prelocked_review_replay_cannot_reenter_owner_or_audit_locks(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    prepared = _prepare_submitted(world)
    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(world.db, prepared),
        idempotency_key="opening-region-review-prelocked-replay",
        request_id="region-review-prelocked-replay-request",
    )
    review = world.db.get(StocktakeReview, result.review_id)
    assert review is not None

    plan = review_service._plan_opening_review_evidence_from_prelocked_reference_graph(
        world.db,
        task_id=prepared.task.id,
        round_id=prepared.round.id,
        review_id=review.id,
        expected_stage=review.review_stage,
        expected_decision=review.decision,
        disposition_resolutions={},
    )
    _head, audit_proof = review_service._lock_audit_chain_head_with_proof(
        world.db,
        stream_key=review_service.INVENTORY_STREAM_KEY,
    )

    def _unexpected_lock(*_args, **_kwargs):
        pytest.fail("prelocked review replay must not reacquire an owner lock")

    monkeypatch.setattr(
        review_service,
        "lock_opening_stocktake_task_evidence",
        _unexpected_lock,
    )
    monkeypatch.setattr(
        review_service,
        "_lock_review_reference_graph",
        _unexpected_lock,
    )
    monkeypatch.setattr(
        review_service,
        "_lock_audit_chain_head_with_proof",
        _unexpected_lock,
    )
    verified = (
        review_service._validate_opening_review_evidence_from_prelocked_task_graph(
            world.db,
            plan=plan,
            audit_proof=audit_proof,
        )
    )
    assert verified.id == review.id

    world.db.commit()
    with pytest.raises(OpeningStocktakeReviewError) as invalid:
        review_service._validate_opening_review_evidence_from_prelocked_task_graph(
            world.db,
            plan=plan,
            audit_proof=audit_proof,
        )
    assert invalid.value.code == "opening_review_replay_evidence_invalid"


@pytest.mark.parametrize("effect_kind", ("state", "outbox"))
def test_review_replay_rejects_alternate_key_duplicate_side_effect(
    world: SimpleNamespace,
    effect_kind: str,
) -> None:
    prepared = _prepare_submitted(world)
    command = _review_command(world.db, prepared)
    key = f"opening-region-review-duplicate-{effect_kind}"
    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=key,
        request_id=f"region-review-duplicate-{effect_kind}-request",
    )
    review = world.db.get(StocktakeReview, result.review_id)
    assert review is not None

    if effect_kind == "state":
        canonical = world.db.scalar(
            select(StateTransitionEvent).where(
                StateTransitionEvent.idempotency_key
                == review_service._event_key("state", review.id)
            )
        )
        assert canonical is not None
        world.db.add(
            StateTransitionEvent(
                aggregate_type=canonical.aggregate_type,
                aggregate_id=canonical.aggregate_id,
                from_status=canonical.from_status,
                to_status=canonical.to_status,
                reason=canonical.reason,
                actor_id=canonical.actor_id,
                idempotency_key=f"alternate-state-{uuid.uuid4().hex}",
                occurred_at=canonical.occurred_at,
                metadata_jsonb=dict(canonical.metadata_jsonb),
                created_at=canonical.created_at,
            )
        )
    else:
        canonical = world.db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.idempotency_key
                == review_service._event_key("outbox", review.id)
            )
        )
        assert canonical is not None
        world.db.add(
            OutboxEvent(
                event_type=canonical.event_type,
                aggregate_type=canonical.aggregate_type,
                aggregate_id=canonical.aggregate_id,
                payload_jsonb=dict(canonical.payload_jsonb),
                status=canonical.status,
                attempts=canonical.attempts,
                idempotency_key=f"alternate-outbox-{uuid.uuid4().hex}",
                available_at=canonical.available_at,
                locked_at=canonical.locked_at,
                locked_by=canonical.locked_by,
                published_at=canonical.published_at,
                last_error=canonical.last_error,
                created_at=canonical.created_at,
                updated_at=canonical.updated_at,
            )
        )
    world.db.flush()

    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key=key,
            request_id=f"region-review-duplicate-{effect_kind}-replay",
        )
    ) == "opening_review_idempotency_record_invalid"


@pytest.mark.parametrize("downstream_status", ["posted", "closed"])
def test_review_replay_uses_one_reference_pass_then_defers_terminal_proof(
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
    downstream_status: str,
) -> None:
    prepared = _prepare_submitted(world)
    command = _review_command(world.db, prepared)
    region_key = f"opening-region-review-{downstream_status}-replay"
    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=region_key,
        request_id=f"region-{downstream_status}-review-request",
    )
    submit_opening_headquarters_review(
        world.db,
        actor=world.principals["admin"],
        command=command,
        idempotency_key=f"opening-headquarters-review-{downstream_status}",
        request_id=f"headquarters-{downstream_status}-review-request",
    )
    posted_at = NOW + timedelta(hours=3)
    prepared.task.status = downstream_status
    prepared.task.posted_at = posted_at
    prepared.task.closed_at = (
        posted_at + timedelta(minutes=1)
        if downstream_status == "closed"
        else None
    )
    freeze = world.db.scalar(
        select(InventoryFreeze).where(InventoryFreeze.task_id == prepared.task.id)
    )
    assert freeze is not None
    freeze.status = "released"
    freeze.valid_to = posted_at
    freeze.released_by_user_id = world.admin.user.id
    freeze.release_reason = "期初入账已完成"
    world.db.commit()

    calls: list[str] = []
    real_start = disposition_service.lock_opening_stocktake_start_reference
    real_inventory = disposition_service.lock_inventory_reference_graph
    real_serial = disposition_service.lock_inventory_serial_graph
    real_audit = review_service._lock_audit_chain_head_with_proof

    def record_start(*args, **kwargs):
        calls.append("start")
        return real_start(*args, **kwargs)

    def record_inventory(*args, **kwargs):
        calls.append("inventory")
        return real_inventory(*args, **kwargs)

    def record_serial(*args, **kwargs):
        calls.append("serial")
        return real_serial(*args, **kwargs)

    def record_audit(*args, **kwargs):
        calls.append("audit")
        return real_audit(*args, **kwargs)

    def unexpected_terminal_replay(*_args, **_kwargs):
        pytest.fail(
            "review replay must defer the ledger-first terminal proof to "
            "finalize/close"
        )

    monkeypatch.setattr(
        disposition_service,
        "lock_opening_stocktake_start_reference",
        record_start,
    )
    monkeypatch.setattr(
        disposition_service,
        "lock_inventory_reference_graph",
        record_inventory,
    )
    monkeypatch.setattr(
        disposition_service,
        "lock_inventory_serial_graph",
        record_serial,
    )
    monkeypatch.setattr(
        review_service,
        "_lock_audit_chain_head_with_proof",
        record_audit,
    )
    monkeypatch.setattr(
        posting_service,
        "validate_opening_task_evidence_for_replay",
        unexpected_terminal_replay,
    )
    before = _review_effect_counts(world.db)
    replay = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=region_key,
        request_id=f"region-{downstream_status}-review-retry",
    )
    assert replay.review_id == result.review_id
    assert replay.replayed is True
    assert calls == ["start", "inventory", "serial", "audit"]
    assert _review_effect_counts(world.db) == before


@pytest.mark.parametrize(
    ("downstream_status", "tamper_target"),
    (("posted", "posting"), ("closed", "establishment")),
)
def test_review_replay_does_not_reenter_terminal_inventory_domain(
    world: SimpleNamespace,
    downstream_status: str,
    tamper_target: str,
) -> None:
    prepared = _prepare_submitted(world)
    command = _review_command(world.db, prepared)
    region_key = f"opening-region-review-real-{downstream_status}-replay"
    original = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=region_key,
        request_id=f"region-real-{downstream_status}-review-request",
    )
    submit_opening_headquarters_review(
        world.db,
        actor=world.principals["admin"],
        command=command,
        idempotency_key=f"opening-headquarters-review-real-{downstream_status}",
        request_id=f"headquarters-real-{downstream_status}-review-request",
    )
    established = _establish_posted_opening_for_review_replay(
        world,
        prepared,
        status=downstream_status,
    )

    before = _review_effect_counts(world.db)
    replay = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=region_key,
        request_id=f"region-real-{downstream_status}-review-retry",
    )
    assert replay.review_id == original.review_id
    assert replay.replayed is True
    assert _review_effect_counts(world.db) == before

    if tamper_target == "posting":
        established.posting.total_quantity += Decimal("1.000")
    else:
        established.establishment.count_manifest_sha256 = "f" * 64
    world.db.commit()
    before_second_replay = _review_effect_counts(world.db)
    second_replay = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=region_key,
        request_id=f"region-real-{downstream_status}-review-tampered-retry",
    )
    assert second_replay.review_id == original.review_id
    assert second_replay.replayed is True
    assert _review_effect_counts(world.db) == before_second_replay


def test_control_difference_can_only_remain_pending_with_comment(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_submitted(world)
    command = _review_command(world.db, prepared)
    control_id = next(
        row.id
        for row in world.db.scalars(
            select(StocktakeDifference).where(
                StocktakeDifference.task_id == prepared.task.id
            )
        ).all()
        if row.difference_type == "control_unassigned"
    )

    wrong_decision = replace(
        command,
        items=tuple(
            replace(row, decision="accept_for_posting")
            if row.difference_id == control_id
            else row
            for row in command.items
        ),
    )
    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=wrong_decision,
            idempotency_key="opening-region-control-wrong",
            request_id="region-control-wrong-request",
        )
    ) == "opening_review_item_decision_invalid"
    no_comment = replace(
        command,
        items=tuple(
            replace(row, comment="") if row.difference_id == control_id else row
            for row in command.items
        ),
    )
    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=no_comment,
            idempotency_key="opening-region-control-no-comment",
            request_id="region-control-no-comment-request",
        )
    ) == "opening_review_control_pending_comment_required"
    assert _stock_fact_counts(world.db)[InventoryTransaction] == 0


def test_review_item_coverage_and_count_manifest_fail_closed(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_submitted(world)
    command = _review_command(world.db, prepared)
    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=replace(command, items=command.items[:-1]),
            idempotency_key="opening-region-incomplete-items",
            request_id="region-incomplete-request",
        )
    ) == "opening_review_items_incomplete"

    prepared.round.count_manifest_sha256 = "f" * 64
    world.db.flush()
    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key="opening-region-tampered-count",
            request_id="region-tampered-count-request",
        )
    ) == "opening_review_count_manifest_mismatch"
    assert world.db.scalar(select(func.count()).select_from(StocktakeReview)) == 0


def test_review_recomputes_the_difference_set_completion_seal(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_submitted(world)
    command = _review_command(world.db, prepared)
    completion = world.db.scalar(
        select(StocktakeDifferenceSetCompletion).where(
            StocktakeDifferenceSetCompletion.task_id == prepared.task.id,
            StocktakeDifferenceSetCompletion.round_id == prepared.round.id,
        )
    )
    assert completion is not None
    completion.difference_manifest_sha256 = "f" * 64
    world.db.flush()

    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key="opening-region-tampered-difference-seal",
            request_id="region-tampered-difference-seal-request",
        )
    ) == "opening_review_difference_completion_invalid"
    assert world.db.scalar(select(func.count()).select_from(StocktakeReview)) == 0


def test_stage_permissions_and_order_fail_closed(world: SimpleNamespace) -> None:
    prepared = _prepare_submitted(world)
    command = _review_command(world.db, prepared)
    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["technician"],
            command=command,
            idempotency_key="opening-region-engineer-denied",
            request_id="region-engineer-denied-request",
        )
    ) == "opening_review_stage_forbidden"
    assert _review_error(
        lambda: submit_opening_headquarters_review(
            world.db,
            actor=world.principals["admin"],
            command=command,
            idempotency_key="opening-hq-before-region",
            request_id="hq-before-region-request",
        )
    ) == "opening_review_stage_state_invalid"
    assert _review_error(
        lambda: submit_opening_headquarters_review(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key="opening-hq-manager-denied",
            request_id="hq-manager-denied-request",
        )
    ) == "opening_review_stage_forbidden"
    assert world.db.scalar(select(func.count()).select_from(StocktakeReview)) == 0


def test_headquarters_cannot_be_the_same_user_or_person(
    world: SimpleNamespace,
) -> None:
    world.manager_x.person.organization_id = world.hq.id
    world.db.add(
        RoleAssignment(
            id=uuid.uuid4(),
            user_id=world.manager_x.user.id,
            role_id=world.roles["admin"].id,
            scope_type="national",
            scope_id="*",
            valid_from=datetime(2020, 1, 1, tzinfo=timezone.utc),
            valid_to=None,
            status="active",
            assigned_by=world.manager_x.user.id,
            revoked_at=None,
            revoked_by=None,
            reason="separation test",
        )
    )
    world.manager_x.user.authorization_version += 1
    world.db.commit()
    dual = load_formal_principal(world.db, world.manager_x.user.id, now=NOW)
    prepared = _prepare_submitted(
        world,
        start_actor=dual,
        count_actor=dual,
    )
    command = _review_command(world.db, prepared)
    submit_opening_region_review(
        world.db,
        actor=dual,
        command=command,
        idempotency_key="opening-region-dual-role",
        request_id="region-dual-role-request",
    )
    assert _review_error(
        lambda: submit_opening_headquarters_review(
            world.db,
            actor=dual,
            command=command,
            idempotency_key="opening-hq-dual-role",
            request_id="hq-dual-role-request",
        )
    ) == "opening_review_separation_of_duties_required"
    assert world.db.scalar(select(func.count()).select_from(StocktakeReview)) == 1
    assert _stock_fact_counts(world.db)[StocktakePosting] == 0


def test_review_reproves_asset_owner_remains_in_task_region_tree(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_submitted(world)
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == prepared.started.task_id
        )
    )
    scope.owner_org_id = world.region_y.id
    world.db.flush()
    command = _review_command(world.db, prepared)
    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key="opening-region-owner-tree-reproof",
            request_id="region-owner-tree-reproof-request",
        )
    ) == "opening_review_owner_outside_region"
    assert world.db.scalar(select(func.count()).select_from(StocktakeReview)) == 0


def test_region_review_does_not_union_two_regional_role_assignments(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_submitted(world)
    second_assignment = RoleAssignment(
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
        reason="prove regional review never unions assignments",
    )
    world.db.add(second_assignment)
    world.manager_x.user.authorization_version += 1
    world.db.flush()
    dual_region_actor = load_formal_principal(
        world.db,
        world.manager_x.user.id,
        now=NOW + timedelta(hours=2),
    )
    task = world.db.get(FormalStocktakeTask, prepared.started.task_id)
    scope = world.db.scalar(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == prepared.started.task_id
        )
    )
    scope.owner_org_id = world.region_y.id
    world.db.flush()

    assert dual_region_actor.allows(
        world.db,
        "stocktake",
        "review_region",
        target_scope_type="organization",
        target_scope_id=str(world.region_x.id),
    )
    assert dual_region_actor.allows(
        world.db,
        "stocktake",
        "review_region",
        target_scope_type="organization",
        target_scope_id=str(world.region_y.id),
    )
    assert _review_error(
        lambda: review_service._authorize_reviewer(
            world.db,
            actor=dual_region_actor,
            stage=review_service.REGION_STAGE,
            task=task,
            scopes=(scope,),
            locations={world.location.id: world.location},
            now=NOW + timedelta(hours=2),
        )
    ) == "opening_review_scope_forbidden"


def test_recount_is_nonterminal_and_cannot_reach_headquarters(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_submitted(world)
    command = _review_command(
        world.db,
        prepared,
        decision="recount",
        comment="需要重新盘点",
    )
    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key="opening-region-recount",
        request_id="region-recount-request",
    )
    assert result.resulting_task_status == "recount_required"
    assert world.db.get(FormalStocktakeTask, prepared.task.id).status == (
        "recount_required"
    )
    assert _review_error(
        lambda: submit_opening_headquarters_review(
            world.db,
            actor=world.principals["admin"],
            command=command,
            idempotency_key="opening-hq-after-recount",
            request_id="hq-after-recount-request",
        )
    ) == "opening_review_headquarters_recount_forbidden"
    assert _stock_fact_counts(world.db)[InventoryOpeningEstablishment] == 0


def test_pending_observation_requires_disposition_before_any_review_write(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_observation(
        world,
        material_identifier_raw="PENDING-REVIEW-NO-DISPOSITION",
    )
    assert prepared.observation.verification_status == "pending_verification"
    command = _review_command(
        world.db,
        prepared,
        decision="recount",
        comment="待核实观察必须先处置",
    )
    before = _review_effect_counts(world.db)

    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key="opening-region-missing-disposition",
            request_id="region-missing-disposition-request",
        )
    ) == "opening_review_observation_disposition_required"
    assert _review_effect_counts(world.db) == before
    assert world.db.get(FormalStocktakeTask, prepared.task.id).status == "submitted"


@pytest.mark.parametrize("decision", ["recount", "reject"])
def test_pending_disposition_only_allows_regional_nonapproval_and_stays_pending(
    world: SimpleNamespace,
    decision: str,
) -> None:
    prepared = _prepare_observation(
        world,
        material_identifier_raw=f"PENDING-REVIEW-{decision.upper()}",
    )
    _record_observation_disposition(
        world,
        prepared,
        disposition="pending_verification",
    )
    command = _review_command(
        world.db,
        prepared,
        decision=decision,
        comment="保留待核实观察并结束本轮",
    )
    blank_observation_comment = replace(
        command,
        items=tuple(
            replace(row, comment="")
            if row.difference_id == prepared.observation_difference.id
            else row
            for row in command.items
        ),
    )
    before_invalid = _review_effect_counts(world.db)
    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=blank_observation_comment,
            idempotency_key=f"opening-region-pending-{decision}-blank",
            request_id=f"region-pending-{decision}-blank-request",
        )
    ) == "opening_review_observation_pending_comment_required"
    assert _review_effect_counts(world.db) == before_invalid
    stock_before = _stock_fact_counts(world.db)

    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=f"opening-region-pending-{decision}",
        request_id=f"region-pending-{decision}-request",
    )
    item = world.db.scalar(
        select(StocktakeReviewItem).where(
            StocktakeReviewItem.review_id == result.review_id,
            StocktakeReviewItem.difference_id
            == prepared.observation_difference.id,
        )
    )
    assert item is not None
    assert item.decision == "pending_verification"
    assert item.comment
    assert result.resulting_task_status == "recount_required"
    assert world.db.get(FormalStocktakeTask, prepared.task.id).status == (
        "recount_required"
    )
    assert _stock_fact_counts(world.db) == stock_before


def test_pending_disposition_cannot_approve_or_reach_headquarters(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_observation(
        world,
        material_identifier_raw="PENDING-REVIEW-NO-APPROVAL",
    )
    _record_observation_disposition(
        world,
        prepared,
        disposition="pending_verification",
    )
    command = _review_command(world.db, prepared, decision="approve")
    before = _review_effect_counts(world.db)

    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key="opening-region-pending-approve",
            request_id="region-pending-approve-request",
        )
    ) == "opening_review_observation_disposition_incompatible"
    assert _review_error(
        lambda: submit_opening_headquarters_review(
            world.db,
            actor=world.principals["admin"],
            command=command,
            idempotency_key="opening-hq-pending-approve",
            request_id="hq-pending-approve-request",
        )
    ) == "opening_review_observation_headquarters_forbidden"
    assert _review_effect_counts(world.db) == before


@pytest.mark.parametrize(
    "disposition",
    ["requires_recount", "resolved_existing_master"],
)
def test_recount_and_resolved_dispositions_only_allow_regional_recount(
    world: SimpleNamespace,
    disposition: str,
) -> None:
    raw = f"OBSERVATION-{disposition.upper()}"
    prepared = _prepare_observation(world, material_identifier_raw=raw)
    resolved_material_id: uuid.UUID | None = None
    if disposition == "resolved_existing_master":
        material = _material(world.db, world.source)
        material.sku_code = raw
        world.db.flush()
        resolved_material_id = material.id
    _record_observation_disposition(
        world,
        prepared,
        disposition=disposition,
        actor_name="admin",
        resolved_material_id=resolved_material_id,
    )
    invalid = _review_command(
        world.db,
        prepared,
        decision=("reject" if disposition == "requires_recount" else "approve"),
        comment="不得绕过受控复盘",
    )
    before_invalid = _review_effect_counts(world.db)
    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=invalid,
            idempotency_key=f"opening-region-{disposition}-invalid",
            request_id=f"region-{disposition}-invalid-request",
        )
    ) == "opening_review_observation_disposition_incompatible"
    assert _review_effect_counts(world.db) == before_invalid

    command = _review_command(
        world.db,
        prepared,
        decision="recount",
        comment="按既有处置证据执行受控复盘",
    )
    stock_before = _stock_fact_counts(world.db)
    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=f"opening-region-{disposition}-recount",
        request_id=f"region-{disposition}-recount-request",
    )
    item = world.db.scalar(
        select(StocktakeReviewItem).where(
            StocktakeReviewItem.review_id == result.review_id,
            StocktakeReviewItem.difference_id
            == prepared.observation_difference.id,
        )
    )
    assert item is not None and item.decision == "recount"
    assert result.resulting_task_status == "recount_required"
    assert _stock_fact_counts(world.db) == stock_before


def test_verified_observation_without_cutoff_account_only_allows_recount(
    world: SimpleNamespace,
) -> None:
    material = _material(world.db, world.source)
    prepared = _prepare_observation(
        world,
        material_identifier_raw=material.sku_code,
    )
    assert prepared.observation.verification_status == "verified"
    assert world.db.scalar(
        select(func.count())
        .select_from(StocktakeObservationDisposition)
        .where(StocktakeObservationDisposition.task_id == prepared.task.id)
    ) == 0
    before = _review_effect_counts(world.db)
    for invalid_decision in ("approve", "reject"):
        assert _review_error(
            lambda invalid_decision=invalid_decision: submit_opening_region_review(
                world.db,
                actor=world.principals["manager_x"],
                command=_review_command(
                    world.db,
                    prepared,
                    decision=invalid_decision,
                    comment="无截止账户不得结束本轮",
                ),
                idempotency_key=f"opening-region-verified-{invalid_decision}",
                request_id=f"region-verified-{invalid_decision}-request",
            )
        ) == "opening_review_observation_disposition_incompatible"
        assert _review_effect_counts(world.db) == before

    result = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=_review_command(
            world.db,
            prepared,
            decision="recount",
            comment="无截止账户，必须复盘",
        ),
        idempotency_key="opening-region-verified-recount",
        request_id="region-verified-recount-request",
    )
    item = world.db.scalar(
        select(StocktakeReviewItem).where(
            StocktakeReviewItem.review_id == result.review_id,
            StocktakeReviewItem.difference_id
            == prepared.observation_difference.id,
        )
    )
    assert item is not None and item.decision == "recount"
    assert result.resulting_task_status == "recount_required"


def test_disposition_manifest_and_audit_are_reproved_on_review_replay(
    world: SimpleNamespace,
) -> None:
    prepared = _prepare_observation(
        world,
        material_identifier_raw="PENDING-REVIEW-REPLAY-TAMPER",
    )
    disposition = _record_observation_disposition(
        world,
        prepared,
        disposition="pending_verification",
    )
    command = _review_command(
        world.db,
        prepared,
        decision="recount",
        comment="保留待核实并复盘",
    )
    key = "opening-region-disposition-replay"
    first = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=key,
        request_id="region-disposition-replay-request",
    )
    before_replay = _review_effect_counts(world.db)
    replay = submit_opening_region_review(
        world.db,
        actor=world.principals["manager_x"],
        command=command,
        idempotency_key=key,
        request_id="region-disposition-replay-transport",
    )
    assert replay.review_id == first.review_id
    assert replay.replayed is True
    assert _review_effect_counts(world.db) == before_replay

    original_manifest = disposition.disposition_manifest_sha256
    disposition.disposition_manifest_sha256 = "f" * 64
    world.db.flush()
    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key=key,
            request_id="region-disposition-replay-tampered-manifest",
        )
    ) == "opening_review_observation_disposition_evidence_invalid"
    assert _review_effect_counts(world.db) == before_replay

    disposition.disposition_manifest_sha256 = original_manifest
    audit = world.db.scalar(
        select(AuditEvent).where(
            AuditEvent.action == "stocktake.opening.observation_disposed",
            AuditEvent.aggregate_id == str(disposition.id),
        )
    )
    assert audit is not None
    audit.after_jsonb = {**audit.after_jsonb, "disposition": "requires_recount"}
    world.db.flush()
    assert _review_error(
        lambda: submit_opening_region_review(
            world.db,
            actor=world.principals["manager_x"],
            command=command,
            idempotency_key=key,
            request_id="region-disposition-replay-tampered-audit",
        )
    ) == "opening_review_observation_disposition_evidence_invalid"
    assert _review_effect_counts(world.db) == before_replay
