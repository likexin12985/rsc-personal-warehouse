from __future__ import annotations

from datetime import datetime, timedelta, timezone
from decimal import Decimal
from itertools import count
from types import SimpleNamespace
import hashlib
import uuid

import pytest
from sqlalchemy import create_engine, event, func, select, update
from sqlalchemy.orm import Session

import app.formal_services.inventory_posting as posting_service
import app.formal_services.opening_stocktake_review as review_service
from app.database import Base
from app.formal_access import (
    Entitlement,
    FormalPrincipal,
    ScopeGrant,
    load_formal_principal,
)
import app.formal_services.opening_stocktake_finalize as finalize_service
from app.formal_services.opening_stocktake_finalize import (
    CloseOpeningStocktakeCommand,
    PostOpeningStocktakeCommand,
    close_posted_opening_stocktake,
    post_approved_opening_stocktake,
)
from app.formal_services.inventory_posting import (
    INVENTORY_LEDGER_HEAD_ID,
    InventoryMovementCommand,
    InventoryPostingCommand,
    InventoryPostingError,
    InventoryReversalCommand,
    post_inventory_transaction,
    reverse_inventory_transaction,
)
from app.formal_services.audit_chain import (
    _lock_audit_chain_head_with_proof,
    append_audit_event,
    calculate_audit_event_hash,
)
from app.foundation_models import (
    AuditChainHead,
    AuditEvent,
    AuthIdentity,
    ExternalObject,
    ExternalObjectVersion,
    Organization,
    OutboxEvent,
    Person,
    Permission,
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
    CustodyAssignment,
    FormalMaterial,
    InventoryLedgerHead,
    InventoryMovement,
    InventoryMovementSerial,
    InventorySerial,
    InventoryTransaction,
    MaterialInventoryPolicy,
    SerialCurrentPosition,
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
    StocktakePosting,
    StocktakePostingItem,
    StocktakeRecountScopeAssignment,
    StocktakeReview,
    StocktakeReviewItem,
    StocktakeRound,
    StocktakeRoundSubmission,
    StocktakeScopeCountCompletion,
    StocktakeSnapshotLine,
)


NOW = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)
AUDIT_HEAD_ID = uuid.UUID("30000000-0000-4000-8000-000000000003")


@pytest.fixture
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")

    @event.listens_for(engine, "connect")
    def enable_foreign_keys(connection, _record):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    with Session(engine) as session:
        yield session
    engine.dispose()


@pytest.fixture
def world(db: Session, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    finalize_ticks = count()
    monkeypatch.setattr(
        finalize_service,
        "_database_now",
        lambda _db: NOW
        - timedelta(hours=1)
        + timedelta(microseconds=next(finalize_ticks) + 1),
    )
    source = SourceSystem(
        id=uuid.uuid4(),
        code="OAM",
        name="测试只读主数据源",
        mode="read_only",
        enabled=True,
        configuration_jsonb={},
    )
    organization = make_organization(db, "测试区域公司")
    headquarters_organization = make_organization(
        db,
        "测试蔚来总部",
        org_type="headquarters",
    )
    person = Person(
        id=uuid.uuid4(),
        organization_id=organization.id,
        employee_no=f"E-{uuid.uuid4().hex[:8]}",
        name="库存工程师",
        employment_status="active",
        source_updated_at=NOW,
    )
    user = User(
        id=str(uuid.uuid4()),
        person_id=person.id,
        account_status="active",
        authorization_version=7,
        mobile=f"1{uuid.uuid4().int % 10**10:010d}",
        name="库存工程师",
        password_hash="formal-password-login-disabled",
        role="technician",
        province=None,
        is_active=True,
        require_password_change=False,
    )
    headquarters_reviewer_person = Person(
        id=uuid.uuid4(),
        organization_id=headquarters_organization.id,
        employee_no=f"E-{uuid.uuid4().hex[:8]}",
        name="总部期初复核人",
        employment_status="active",
        source_updated_at=NOW,
    )
    headquarters_reviewer_user = User(
        id=str(uuid.uuid4()),
        person_id=headquarters_reviewer_person.id,
        account_status="active",
        authorization_version=11,
        mobile=f"1{uuid.uuid4().int % 10**10:010d}",
        name="总部期初复核人",
        password_hash="formal-password-login-disabled",
        role="admin",
        province=None,
        is_active=True,
        require_password_change=False,
    )
    db.add_all(
        [
            source,
            person,
            user,
            headquarters_reviewer_person,
            headquarters_reviewer_user,
        ]
    )
    db.flush()
    db.add(
        AuthIdentity(
            id=uuid.uuid4(),
            user_id=headquarters_reviewer_user.id,
            identity_type="mobile",
            provider_key="test",
            identifier_hash=uuid.uuid4().hex + uuid.uuid4().hex,
            hash_version=1,
            verified_at=NOW - timedelta(days=1),
            status="active",
            revoked_at=None,
        )
    )
    regional_role = Role(
        id=uuid.uuid4(),
        code="provincial_manager",
        name="区域公司负责人",
        is_external=False,
        status="active",
    )
    headquarters_role = Role(
        id=uuid.uuid4(),
        code="admin",
        name="蔚来总部管理员",
        is_external=False,
        status="active",
    )
    post_opening_permission = Permission(
        id=uuid.uuid4(),
        resource="stocktake",
        action="post_opening",
        field_code="",
        description="库存过账测试期初终结",
    )
    db.add_all(
        [regional_role, headquarters_role, post_opening_permission]
    )
    db.flush()
    db.add(
        RolePermission(
            role_id=headquarters_role.id,
            permission_id=post_opening_permission.id,
            effect="allow",
        )
    )
    regional_assignment = RoleAssignment(
        id=uuid.uuid4(),
        user_id=user.id,
        role_id=regional_role.id,
        scope_type="organization",
        scope_id=str(organization.id),
        valid_from=NOW - timedelta(days=30),
        valid_to=None,
        status="active",
        assigned_by=headquarters_reviewer_user.id,
        revoked_at=None,
        revoked_by=None,
        reason="库存过账专项测试",
    )
    headquarters_assignment = RoleAssignment(
        id=uuid.uuid4(),
        user_id=headquarters_reviewer_user.id,
        role_id=headquarters_role.id,
        scope_type="national",
        scope_id="*",
        valid_from=NOW - timedelta(days=30),
        valid_to=None,
        status="active",
        assigned_by=headquarters_reviewer_user.id,
        revoked_at=None,
        revoked_by=None,
        reason="库存过账专项测试",
    )
    sync_run = SyncRun(
        id=uuid.uuid4(),
        source_system_id=source.id,
        run_key=f"opening-control-{uuid.uuid4().hex}",
        scope_key="opening-test",
        mode="full",
        watermark_from=None,
        watermark_to=None,
        status="completed",
        manifest_sha256="c" * 64,
        started_at=NOW - timedelta(hours=6),
        completed_at=NOW - timedelta(hours=5),
        failure_code=None,
        failure_detail=None,
    )
    db.add_all([regional_assignment, headquarters_assignment, sync_run])
    db.flush()
    material = make_material(
        db,
        source,
        tracking_mode="none",
        quantity_scale=3,
        allow_fraction=True,
    )
    db.add_all(
        [
            InventoryLedgerHead(
                id=INVENTORY_LEDGER_HEAD_ID,
                stream_key="inventory",
                next_cursor=1,
            ),
            AuditChainHead(
                id=AUDIT_HEAD_ID,
                stream_key="inventory",
                last_event_id=None,
                last_hash=None,
                version=0,
            ),
        ]
    )
    principal = make_principal(user, person, scope_type="national", scope_id="*")
    result = SimpleNamespace(
        source=source,
        organization=organization,
        person=person,
        user=user,
        regional_assignment=regional_assignment,
        regional_role=regional_role,
        headquarters_organization=headquarters_organization,
        headquarters_reviewer_person=headquarters_reviewer_person,
        headquarters_reviewer_user=headquarters_reviewer_user,
        headquarters_assignment=headquarters_assignment,
        sync_run=sync_run,
        material=material,
        principal=principal,
        current_principal=principal,
    )
    monkeypatch.setattr(
        posting_service,
        "load_formal_principal",
        lambda _db, _user_id: result.current_principal,
    )
    db.info["inventory_posting_world"] = result
    db.info["opening_facts_by_account"] = {}
    db.commit()
    return result


def make_organization(
    db: Session,
    name: str,
    *,
    org_type: str = "region_company",
    parent_id: uuid.UUID | None = None,
) -> Organization:
    organization = Organization(
        id=uuid.uuid4(),
        code=f"ORG-{uuid.uuid4().hex[:10]}",
        name=name,
        parent_id=parent_id,
        org_type=org_type,
        province_code=None,
        status="active",
    )
    db.add(organization)
    db.flush()
    return organization


def make_principal(
    user: User,
    person: Person,
    *,
    scope_type: str,
    scope_id: str,
    actions: tuple[str, ...] = ("post", "reverse"),
) -> FormalPrincipal:
    assignment_id = uuid.uuid4()
    grant = ScopeGrant(
        assignment_id=assignment_id,
        role_code="technician",
        scope_type=scope_type,
        scope_id=scope_id,
        valid_from=NOW - timedelta(days=1),
        valid_to=None,
    )
    return FormalPrincipal(
        user_id=user.id,
        person_id=person.id,
        account_status="active",
        employment_status="active",
        authorization_version=user.authorization_version,
        access_mode="active",
        assignments=(grant,),
        entitlements=tuple(
            Entitlement(
                assignment_id=assignment_id,
                role_code="technician",
                scope_type=scope_type,
                scope_id=scope_id,
                resource="inventory_transaction",
                action=action,
                field_code="",
                effect="allow",
            )
            for action in actions
        ),
    )


def make_material(
    db: Session,
    source: SourceSystem,
    *,
    tracking_mode: str,
    quantity_scale: int,
    allow_fraction: bool,
) -> FormalMaterial:
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
        sku_code=f"SKU-{uuid.uuid4().hex[:12]}",
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
        quantity_scale=quantity_scale,
        allow_fraction=allow_fraction,
        effective_from=NOW - timedelta(days=30),
        effective_to=None,
    )
    db.add_all([external, material, policy])
    db.flush()
    return material


def make_account(
    db: Session,
    *,
    organization: Organization,
    material: FormalMaterial,
    custodian: Person | None = None,
    location_organization: Organization | None = None,
    initial: Decimal | None = None,
    established: bool = True,
) -> StockAccount:
    location = StockLocation(
        id=uuid.uuid4(),
        code=f"LOC-{uuid.uuid4().hex[:12]}",
        name="测试库位",
        location_type="region",
        owner_org_id=(location_organization or organization).id,
        parent_id=None,
        custodian_person_id=custodian.id if custodian else None,
        status="active",
        created_at=NOW - timedelta(days=2),
        updated_at=NOW - timedelta(days=2),
    )
    account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=organization.id,
        custodian_person_id=custodian.id if custodian else None,
        location_id=location.id,
        material_id=material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
        created_at=NOW - timedelta(days=1),
        updated_at=NOW - timedelta(days=1),
    )
    # No ORM relationship is declared between the formal schema rows.  Flush
    # the FK parent explicitly so SQLite's immediate FK checks see it first.
    db.add(location)
    db.flush()
    db.add(account)
    db.flush()
    if initial is not None:
        db.add(
            StockBalance(
                stock_account_id=account.id,
                quantity=Decimal("0.000"),
                ledger_cursor=0,
                version=0,
            )
        )
        db.flush()
    if established:
        fixture_world = db.info.get("inventory_posting_world")
        if fixture_world is None:
            raise AssertionError("opening evidence fixture is not initialized")
        facts = establish_account_for_posting(db, fixture_world, account)
        db.info["opening_facts_by_account"][account.id] = facts
        if initial is not None and initial > 0:
            token = uuid.uuid4().hex
            post_inventory_transaction(
                db,
                actor=fixture_world.current_principal,
                command=InventoryPostingCommand(
                    transaction_no=f"SEED-{token}",
                    movement_type="inbound",
                    source_document_type="inventory_posting_test_seed",
                    source_document_id=f"SEED-DOC-{token}",
                    posting_key=f"inventory:test-seed:{token}",
                    effective_at=NOW,
                    movements=(
                        InventoryMovementCommand(
                            from_account_id=None,
                            to_account_id=account.id,
                            quantity=initial,
                            external_boundary_code="test-fixture-seed",
                        ),
                    ),
                ),
                idempotency_key=f"inventory-test-seed-{token}",
                request_id=f"inventory-test-seed-request-{token}",
            )
    return account


def establish_account_for_posting(
    db: Session,
    world: SimpleNamespace,
    account: StockAccount,
) -> SimpleNamespace:
    """Create one internally consistent, reviewed zero-opening fixture.

    Production opening facts are created only by the dedicated stocktake
    service.  Posting tests need an already-established prerequisite, so this
    helper builds the minimum relational evidence without invoking the generic
    posting API that intentionally rejects ``opening``.
    """

    head = db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID)
    assert head is not None
    current_cursor = head.next_cursor - 1
    token = uuid.uuid4().hex
    cutoff_at = NOW - timedelta(hours=4)
    submitted_at = NOW - timedelta(hours=3)
    regional_reviewed_at = NOW - timedelta(hours=2)
    headquarters_reviewed_at = NOW - timedelta(minutes=90)
    posted_at = NOW - timedelta(hours=1)
    established_at = NOW - timedelta(minutes=30)
    location = db.get(StockLocation, account.location_id)
    assert location is not None

    region = db.get(Organization, location.owner_org_id)
    seen_organizations: set[uuid.UUID] = set()
    while region is not None and region.org_type != "region_company":
        assert region.id not in seen_organizations
        seen_organizations.add(region.id)
        region = (
            db.get(Organization, region.parent_id)
            if region.parent_id is not None
            else None
        )
    assert region is not None

    regional_assignment = world.regional_assignment
    if regional_assignment.scope_id != str(region.id):
        regional_assignment = RoleAssignment(
            id=uuid.uuid4(),
            user_id=world.user.id,
            role_id=world.regional_role.id,
            scope_type="organization",
            scope_id=str(region.id),
            valid_from=NOW - timedelta(days=30),
            valid_to=None,
            status="active",
            assigned_by=world.headquarters_reviewer_user.id,
            revoked_at=None,
            revoked_by=None,
            reason="跨组织库存过账专项测试",
        )
        db.add(regional_assignment)
        db.flush()

    sync_run = SyncRun(
        id=uuid.uuid4(),
        source_system_id=world.source.id,
        run_key=f"opening-control-{token}",
        scope_key=f"oam_inventory_control:region:{region.id}",
        mode="full",
        watermark_from=None,
        watermark_to=None,
        status="completed",
        manifest_sha256="0" * 64,
        started_at=cutoff_at - timedelta(hours=2),
        completed_at=cutoff_at - timedelta(hours=1),
        failure_code=None,
        failure_detail=None,
        created_at=cutoff_at - timedelta(hours=2),
        updated_at=cutoff_at - timedelta(hours=1),
    )
    db.add(sync_run)
    db.flush()
    sync_batch = SyncBatch(
        id=uuid.uuid4(),
        run_id=sync_run.id,
        entity_type=posting_service.OPENING_CONTROL_ENTITY_TYPE,
        sequence=1,
        record_count=0,
        body_sha256=posting_service.opening_control_batch_body_sha256(
            sequence=1,
            events=[],
        ),
        status="applied",
        received_at=cutoff_at - timedelta(hours=1),
        validated_at=cutoff_at - timedelta(hours=1),
        created_at=cutoff_at - timedelta(hours=1),
    )
    db.add(sync_batch)
    db.flush()

    task = FormalStocktakeTask(
        id=uuid.uuid4(),
        task_no=f"OPEN-{token}",
        task_type="opening",
        region_org_id=region.id,
        status="approved",
        blind_count=True,
        cutoff_ledger_cursor=current_cursor,
        cutoff_at=cutoff_at,
        scope_manifest_sha256="0" * 64,
        snapshot_manifest_sha256="0" * 64,
        control_source_system_id=world.source.id,
        control_sync_run_id=sync_run.id,
        control_snapshot_at=sync_run.completed_at,
        control_manifest_sha256="0" * 64,
        current_round_no=1,
        created_by_user_id=world.headquarters_reviewer_user.id,
        deadline=None,
        issued_at=cutoff_at,
        frozen_at=cutoff_at,
        submitted_at=submitted_at,
        posted_at=None,
        closed_at=None,
        cancelled_at=None,
        version=1,
        note="库存过账专项测试期初事实",
        created_at=cutoff_at,
        updated_at=headquarters_reviewed_at,
    )
    db.add(task)
    db.flush()
    scope = FormalStocktakeScope(
        id=uuid.uuid4(),
        task_id=task.id,
        scope_no=1,
        scope_mode="location_all",
        location_id=account.location_id,
        owner_org_id=account.owner_org_id,
        custodian_person_id_snapshot=account.custodian_person_id,
        assignee_user_id=world.user.id,
        material_id=None,
        condition_code=None,
        availability_bucket=None,
        scope_key=f"opening:{account.owner_org_id}:{account.location_id}",
        scope_sha256="0" * 64,
        created_at=cutoff_at,
    )
    scope.scope_sha256 = posting_service.canonical_opening_scope_line_sha256(
        scope
    )
    db.add(scope)
    db.flush()
    freeze = InventoryFreeze(
        id=uuid.uuid4(),
        task_id=task.id,
        stocktake_scope_id=scope.id,
        scope_key=scope.scope_key,
        freeze_mode="hard",
        status="active",
        valid_from=cutoff_at,
        valid_to=None,
        created_by_user_id=world.headquarters_reviewer_user.id,
        released_by_user_id=None,
        release_reason="",
        version=0,
        created_at=cutoff_at,
        updated_at=cutoff_at,
    )
    db.add(freeze)
    db.flush()
    snapshot = StocktakeSnapshotLine(
        id=uuid.uuid4(),
        task_id=task.id,
        scope_id=scope.id,
        stock_account_id=account.id,
        book_qty=Decimal("0.000"),
        ledger_cursor=current_cursor,
        account_dimension_sha256=(
            posting_service.canonical_opening_account_dimension_sha256(account)
        ),
        serial_snapshot_jsonb=[],
        serial_snapshot_sha256=(
            posting_service.canonical_opening_serial_snapshot_sha256(
                stock_account_id=account.id,
                serials=(),
            )
        ),
        serial_count=0,
        created_at=cutoff_at,
    )
    db.add(snapshot)
    db.flush()
    round_row = StocktakeRound(
        id=uuid.uuid4(),
        task_id=task.id,
        round_no=1,
        round_type="initial",
        status="submitted",
        submitted_by_user_id=world.user.id,
        started_at=NOW - timedelta(hours=4),
        submitted_at=submitted_at,
        count_manifest_sha256="0" * 64,
        idempotency_key_hash=hashlib.sha256(
            f"round:{token}".encode()
        ).hexdigest(),
        created_at=cutoff_at,
        updated_at=submitted_at,
    )
    db.add(round_row)
    db.flush()
    count_line = StocktakeCountLine(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        stock_account_id=account.id,
        counted_qty=Decimal("0.000"),
        count_method="manual",
        reason_code="scope_full_set_zero",
        remark="",
        counted_by_user_id=world.user.id,
        counted_at=submitted_at,
        created_at=submitted_at,
        updated_at=submitted_at,
    )
    db.add(count_line)
    db.flush()

    task.scope_manifest_sha256 = (
        posting_service.canonical_opening_scope_manifest_sha256(
            task,
            (scope,),
            {scope.id: freeze},
        )
    )
    task.snapshot_manifest_sha256 = (
        posting_service.canonical_opening_snapshot_manifest_sha256(
            task,
            (scope,),
            (snapshot,),
        )
    )
    task.control_manifest_sha256 = (
        posting_service.canonical_opening_control_manifest_sha256(
            task,
            sync_run,
            (),
        )
    )
    sync_run.manifest_sha256 = task.control_manifest_sha256
    round_row.count_manifest_sha256 = (
        posting_service.canonical_opening_count_manifest_sha256(
            task,
            round_row,
            (count_line,),
            (),
        )
    )
    import app.formal_services.opening_stocktake_count as count_service

    authorization_sha256 = count_service._hash_document(
        {
            "assignment_id": str(regional_assignment.id),
            "authorization_version": world.user.authorization_version,
            "completed_at": count_service._canonical_timestamp(submitted_at),
            "person_id": str(world.person.id),
            "role_code": "provincial_manager",
            "schema": "cloud_oam.opening_stocktake.scope_authorization.v1",
            "scope_id": str(region.id),
            "scope_type": "organization",
            "user_id": world.user.id,
        }
    )
    scope_request_document = {
        "actor_person_id": str(world.person.id),
        "actor_user_id": world.user.id,
        "physical_observations": [],
        "round_id": str(round_row.id),
        "schema": "cloud_oam.opening_stocktake.scope_count_request.v1",
        "scope_id": str(scope.id),
        "task_id": str(task.id),
        "zero_confirmed": False,
    }
    scope_request_sha256 = count_service._hash_document(scope_request_document)
    scope_request_resolution = {
        "items": [],
        "request_sha256": scope_request_sha256,
        "round_id": str(round_row.id),
        "schema": (
            "cloud_oam.opening_stocktake."
            "scope_count_request_resolution.v1"
        ),
        "scope_id": str(scope.id),
        "task_id": str(task.id),
    }
    scope_completion = StocktakeScopeCountCompletion(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        scope_id=scope.id,
        count_line_count=1,
        observation_line_count=0,
        serial_count=0,
        total_counted_qty=Decimal("0.000"),
        zero_confirmed=False,
        evidence_manifest_sha256="0" * 64,
        request_sha256=scope_request_sha256,
        request_jsonb=scope_request_document,
        request_resolution_jsonb=scope_request_resolution,
        idempotency_key_hash=hashlib.sha256(
            f"scope-completion:{token}".encode()
        ).hexdigest(),
        completed_by_user_id=world.user.id,
        completed_by_person_id=world.person.id,
        completed_role_assignment_id=regional_assignment.id,
        authorization_version=world.user.authorization_version,
        role_code="provincial_manager",
        scope_type="organization",
        scope_id_snapshot=str(region.id),
        authorization_sha256=authorization_sha256,
        completed_at=submitted_at,
        created_at=submitted_at,
    )
    db.add(scope_completion)
    db.flush()
    scope_completion.evidence_manifest_sha256 = (
        count_service._scope_evidence_manifest(
            db,
            task.id,
            round_row.id,
            scope.id,
            (count_line,),
            (),
            authorization_sha256,
        )
    )
    round_manifest_sha256 = count_service._round_manifest_sha256(
        task.id,
        round_row.id,
        (scope_completion,),
        scope_completion.id,
    )
    submission = StocktakeRoundSubmission(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        sealing_completion_id=scope_completion.id,
        scope_count=1,
        zero_scope_count=0,
        count_line_count=1,
        observation_line_count=0,
        serial_count=0,
        total_counted_qty=Decimal("0.000"),
        round_manifest_sha256=round_manifest_sha256,
        count_manifest_sha256=round_row.count_manifest_sha256,
        request_sha256=count_service._hash_document(
            {
                "count_manifest_sha256": round_row.count_manifest_sha256,
                "round_id": str(round_row.id),
                "round_manifest_sha256": round_manifest_sha256,
                "schema": "cloud_oam.opening_stocktake.round_submission_request.v3",
                "sealing_completion_id": str(scope_completion.id),
            }
        ),
        idempotency_key_hash=count_service._event_hash(
            "round-submission", round_row.id, task.id
        ),
        submitted_by_user_id=world.user.id,
        submitted_by_person_id=world.person.id,
        submitted_role_assignment_id=regional_assignment.id,
        authorization_version=world.user.authorization_version,
        submitted_at=submitted_at,
        created_at=submitted_at,
    )
    db.add(submission)
    db.flush()
    difference_summary = count_service._difference_set_summary(
        task=task,
        round_row=round_row,
        submission=submission,
        differences=(),
    )
    completion = StocktakeDifferenceSetCompletion(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        round_submission_id=submission.id,
        difference_count=difference_summary["difference_count"],
        physical_difference_count=difference_summary[
            "physical_difference_count"
        ],
        control_difference_count=difference_summary[
            "control_difference_count"
        ],
        pending_observation_difference_count=difference_summary[
            "pending_observation_difference_count"
        ],
        total_affected_qty=difference_summary["total_affected_qty"],
        difference_manifest_sha256=difference_summary[
            "difference_manifest_sha256"
        ],
        request_sha256=count_service._difference_set_request_sha256(
            difference_summary
        ),
        idempotency_key_hash=count_service._event_hash(
            "difference-set-completion", round_row.id, submission.id
        ),
        completed_by_user_id=world.user.id,
        completed_by_person_id=world.person.id,
        completed_role_assignment_id=regional_assignment.id,
        authorization_version=world.user.authorization_version,
        role_code=scope_completion.role_code,
        scope_type=scope_completion.scope_type,
        scope_id_snapshot=scope_completion.scope_id_snapshot,
        authorization_sha256=scope_completion.authorization_sha256,
        completed_at=submitted_at,
        created_at=submitted_at,
    )
    db.add(completion)
    db.flush()
    count_context = count_service._round_event_context(task, round_row)
    scope_state_reason, round_state_reason = count_service._round_state_reasons(
        round_row
    )
    db.add_all(
        [
            StateTransitionEvent(
                aggregate_type="stocktake_scope",
                aggregate_id=str(scope.id),
                from_status="counting",
                to_status="completed",
                reason=scope_state_reason,
                actor_id=world.user.id,
                idempotency_key=count_service._event_key(
                    "scope-state", round_row.id, scope.id
                ),
                occurred_at=submitted_at,
                metadata_jsonb={
                    **count_context,
                    "zero_confirmed": False,
                },
                created_at=submitted_at,
            ),
            StateTransitionEvent(
                aggregate_type="stocktake_round",
                aggregate_id=str(round_row.id),
                from_status="counting",
                to_status="submitted",
                reason=round_state_reason,
                actor_id=world.user.id,
                idempotency_key=count_service._event_key(
                    "round-state", round_row.id, task.id
                ),
                occurred_at=submitted_at,
                metadata_jsonb=count_service._round_submission_state_metadata(
                    task,
                    round_row,
                    aggregate_type="stocktake_round",
                ),
                created_at=submitted_at,
            ),
            StateTransitionEvent(
                aggregate_type="stocktake_task",
                aggregate_id=str(task.id),
                from_status="counting",
                to_status="submitted",
                reason=round_state_reason,
                actor_id=world.user.id,
                idempotency_key=count_service._event_key(
                    "task-state", round_row.id, task.id
                ),
                occurred_at=submitted_at,
                metadata_jsonb=count_service._round_submission_state_metadata(
                    task,
                    round_row,
                    aggregate_type="stocktake_task",
                ),
                created_at=submitted_at,
            ),
            OutboxEvent(
                event_type="stocktake.opening.scope_count_completed",
                aggregate_type="stocktake_scope",
                aggregate_id=str(scope.id),
                payload_jsonb={
                    **count_context,
                    "round_sealed": True,
                    "scope_id": str(scope.id),
                },
                status="pending",
                attempts=0,
                idempotency_key=count_service._event_key(
                    "scope-outbox", round_row.id, scope.id
                ),
                available_at=submitted_at,
                locked_at=None,
                locked_by=None,
                published_at=None,
                last_error=None,
                created_at=submitted_at,
                updated_at=submitted_at,
            ),
            OutboxEvent(
                event_type="stocktake.opening.round_submitted",
                aggregate_type="stocktake_round",
                aggregate_id=str(round_row.id),
                payload_jsonb=count_context,
                status="pending",
                attempts=0,
                idempotency_key=count_service._event_key(
                    "round-outbox", round_row.id, task.id
                ),
                available_at=submitted_at,
                locked_at=None,
                locked_by=None,
                published_at=None,
                last_error=None,
                created_at=submitted_at,
                updated_at=submitted_at,
            ),
        ]
    )
    db.flush()
    count_request_id = (
        "opening-count-request-"
        + hashlib.sha256(f"opening-count-fixture:{token}".encode()).hexdigest()
    )
    append_audit_event(
        db,
        stream_key=posting_service.INVENTORY_STREAM_KEY,
        actor_user_id=world.user.id,
        action="stocktake.opening.scope_count_completed",
        aggregate_type="stocktake_scope",
        aggregate_id=str(scope.id),
        before_jsonb=None,
        after_jsonb={
            **count_context,
            "has_pending_verification": False,
            "round_sealed": True,
            "zero_confirmed": False,
        },
        request_id=count_request_id,
        occurred_at=submitted_at,
    )
    append_audit_event(
        db,
        stream_key=posting_service.INVENTORY_STREAM_KEY,
        actor_user_id=world.user.id,
        action="stocktake.opening.round_submitted",
        aggregate_type="stocktake_round",
        aggregate_id=str(round_row.id),
        before_jsonb=None,
        after_jsonb=count_context,
        request_id=count_request_id,
        occurred_at=submitted_at,
    )
    db.flush()
    decision_manifest = posting_service.canonical_opening_decision_manifest_sha256(
        task_id=task.id,
        round_id=round_row.id,
        differences=(),
        decisions={},
    )
    db.flush()
    regional_review = StocktakeReview(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        review_stage="region",
        reviewer_user_id=world.user.id,
        reviewer_person_id=world.person.id,
        reviewer_role_assignment_id=regional_assignment.id,
        authorization_version=world.user.authorization_version,
        decision="approve",
        comment="区域复核通过",
        decision_manifest_sha256=decision_manifest,
        idempotency_key_hash=hashlib.sha256(
            f"region-review:{token}".encode()
        ).hexdigest(),
        reviewed_at=regional_reviewed_at,
        created_at=regional_reviewed_at,
    )
    headquarters_review = StocktakeReview(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=round_row.id,
        review_stage="headquarters",
        reviewer_user_id=world.headquarters_reviewer_user.id,
        reviewer_person_id=world.headquarters_reviewer_person.id,
        reviewer_role_assignment_id=world.headquarters_assignment.id,
        authorization_version=world.headquarters_reviewer_user.authorization_version,
        decision="approve",
        comment="总部复核通过",
        decision_manifest_sha256=decision_manifest,
        idempotency_key_hash=hashlib.sha256(
            f"headquarters-review:{token}".encode()
        ).hexdigest(),
        reviewed_at=headquarters_reviewed_at,
        created_at=headquarters_reviewed_at,
    )
    db.add_all([regional_review, headquarters_review])
    db.flush()

    def add_review_effects(
        review: StocktakeReview,
        *,
        from_status: str,
        to_status: str,
    ) -> None:
        reviewed_at = review.reviewed_at
        reason = f"opening_{review.review_stage}_review_{review.decision}"
        metadata = {
            "decision": review.decision,
            "decision_manifest_sha256": review.decision_manifest_sha256,
            "pending_control_count": 0,
            "review_id": str(review.id),
            "round_id": str(round_row.id),
            "stage": review.review_stage,
        }
        db.add(
            StateTransitionEvent(
                aggregate_type="stocktake_task",
                aggregate_id=str(task.id),
                from_status=from_status,
                to_status=to_status,
                reason=reason,
                actor_id=review.reviewer_user_id,
                idempotency_key=review_service._event_key("state", review.id),
                occurred_at=reviewed_at,
                metadata_jsonb=metadata,
                created_at=reviewed_at,
            )
        )
        db.add(
            OutboxEvent(
                event_type=(
                    f"stocktake.opening.{review.review_stage}_reviewed"
                ),
                aggregate_type="stocktake_task",
                aggregate_id=str(task.id),
                payload_jsonb=metadata,
                status="pending",
                attempts=0,
                idempotency_key=review_service._event_key("outbox", review.id),
                available_at=reviewed_at,
                locked_at=None,
                locked_by=None,
                published_at=None,
                last_error=None,
                created_at=reviewed_at,
                updated_at=reviewed_at,
            )
        )
        db.flush()
        append_audit_event(
            db,
            stream_key=posting_service.INVENTORY_STREAM_KEY,
            actor_user_id=review.reviewer_user_id,
            action=f"stocktake.opening.{review.review_stage}_reviewed",
            aggregate_type="stocktake_review",
            aggregate_id=str(review.id),
            before_jsonb=None,
            after_jsonb={
                **metadata,
                "authorization_version": review.authorization_version,
                "reviewer_person_id": str(review.reviewer_person_id),
                "reviewer_role_assignment_id": str(
                    review.reviewer_role_assignment_id
                ),
                "reviewer_user_id": review.reviewer_user_id,
            },
            request_id=f"opening-review-fixture-{review.id}",
            occurred_at=reviewed_at,
        )

    add_review_effects(
        regional_review,
        from_status="submitted",
        to_status="hq_review",
    )
    add_review_effects(
        headquarters_review,
        from_status="hq_review",
        to_status="approved",
    )
    headquarters_principal = load_formal_principal(
        db,
        world.headquarters_reviewer_user.id,
        now=NOW - timedelta(minutes=45),
    )
    posted = post_approved_opening_stocktake(
        db,
        actor=headquarters_principal,
        command=PostOpeningStocktakeCommand(
            task_id=task.id,
            expected_version=task.version,
        ),
        idempotency_key=f"opening-posting-fixture-post-{token}",
        request_id=f"opening-posting-fixture-post-request-{token}",
    )
    closed = close_posted_opening_stocktake(
        db,
        actor=headquarters_principal,
        command=CloseOpeningStocktakeCommand(
            task_id=task.id,
            expected_version=posted.task_version,
        ),
        idempotency_key=f"opening-posting-fixture-close-{token}",
        request_id=f"opening-posting-fixture-close-request-{token}",
    )
    assert closed.resulting_task_status == "closed"
    posting = db.get(StocktakePosting, posted.posting_id)
    establishment = db.scalar(
        select(InventoryOpeningEstablishment).where(
            InventoryOpeningEstablishment.posting_id == posted.posting_id,
            InventoryOpeningEstablishment.scope_id == scope.id,
        )
    )
    assert posting is not None and establishment is not None
    task = db.get(FormalStocktakeTask, task.id)
    freeze = db.get(InventoryFreeze, freeze.id)
    assert task is not None and freeze is not None
    return SimpleNamespace(
        task=task,
        scope=scope,
        freeze=freeze,
        snapshot=snapshot,
        round=round_row,
        scope_completion=scope_completion,
        submission=submission,
        completion=completion,
        count_line=count_line,
        sync_run=sync_run,
        sync_batch=sync_batch,
        regional_assignment=regional_assignment,
        regional_review=regional_review,
        headquarters_review=headquarters_review,
        posting=posting,
        establishment=establishment,
    )


def freeze_account_scope(
    db: Session,
    world: SimpleNamespace,
    account: StockAccount,
    *,
    freeze_mode: str,
    scope_mode: str = "location_all",
    matches_account: bool = True,
) -> InventoryFreeze:
    token = uuid.uuid4().hex
    head = db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID)
    assert head is not None
    freeze_task = FormalStocktakeTask(
        id=uuid.uuid4(),
        task_no=f"FREEZE-{token}",
        task_type="ad_hoc",
        region_org_id=world.organization.id,
        status="frozen",
        blind_count=False,
        cutoff_ledger_cursor=head.next_cursor - 1,
        cutoff_at=NOW - timedelta(minutes=1),
        scope_manifest_sha256=None,
        snapshot_manifest_sha256=None,
        control_source_system_id=None,
        control_sync_run_id=None,
        control_snapshot_at=None,
        control_manifest_sha256=None,
        current_round_no=0,
        created_by_user_id=world.user.id,
        deadline=None,
        issued_at=NOW - timedelta(minutes=1),
        frozen_at=NOW - timedelta(minutes=1),
        submitted_at=None,
        posted_at=None,
        closed_at=None,
        cancelled_at=None,
        version=0,
        note="库存冻结专项测试",
        created_at=NOW - timedelta(minutes=1),
        updated_at=NOW - timedelta(minutes=1),
    )
    db.add(freeze_task)
    db.flush()
    scope = FormalStocktakeScope(
        id=uuid.uuid4(),
        task_id=freeze_task.id,
        scope_no=1,
        scope_mode=scope_mode,
        location_id=account.location_id,
        owner_org_id=account.owner_org_id,
        custodian_person_id_snapshot=account.custodian_person_id,
        assignee_user_id=world.user.id,
        material_id=(account.material_id if scope_mode == "filtered" else None),
        condition_code=(
            account.condition_code
            if scope_mode == "filtered" and matches_account
            else ("used" if scope_mode == "filtered" else None)
        ),
        availability_bucket=(
            account.availability_bucket if scope_mode == "filtered" else None
        ),
        scope_key=f"freeze:{account.owner_org_id}:{account.location_id}:{token}",
        scope_sha256=hashlib.sha256(
            f"freeze-scope:{token}".encode()
        ).hexdigest(),
        created_at=NOW - timedelta(minutes=1),
    )
    db.add(scope)
    db.flush()
    freeze = InventoryFreeze(
        id=uuid.uuid4(),
        task_id=freeze_task.id,
        stocktake_scope_id=scope.id,
        scope_key=scope.scope_key,
        freeze_mode=freeze_mode,
        status="active",
        valid_from=NOW - timedelta(minutes=1),
        valid_to=None,
        created_by_user_id=world.user.id,
        released_by_user_id=None,
        release_reason="",
        version=0,
    )
    db.add(freeze)
    db.flush()
    return freeze


def refresh_opening_review_effects(
    db: Session,
    facts: SimpleNamespace,
) -> None:
    """Keep the hand-built review effects sealed after fixture expansion."""

    pending_count = db.scalar(
        select(func.count())
        .select_from(StocktakeDifference)
        .where(
            StocktakeDifference.task_id == facts.task.id,
            StocktakeDifference.round_id == facts.round.id,
            StocktakeDifference.difference_type == "control_unassigned",
        )
    ) or 0
    for review in (facts.regional_review, facts.headquarters_review):
        metadata = {
            "decision": review.decision,
            "decision_manifest_sha256": review.decision_manifest_sha256,
            "pending_control_count": pending_count,
            "review_id": str(review.id),
            "round_id": str(facts.round.id),
            "stage": review.review_stage,
        }
        state = db.scalar(
            select(StateTransitionEvent).where(
                StateTransitionEvent.idempotency_key
                == review_service._event_key("state", review.id)
            )
        )
        outbox = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.idempotency_key
                == review_service._event_key("outbox", review.id)
            )
        )
        audit = db.scalar(
            select(AuditEvent).where(
                AuditEvent.action
                == f"stocktake.opening.{review.review_stage}_reviewed",
                AuditEvent.aggregate_type == "stocktake_review",
                AuditEvent.aggregate_id == str(review.id),
            )
        )
        assert state is not None and outbox is not None and audit is not None
        state.metadata_jsonb = metadata
        outbox.payload_jsonb = metadata
        audit.after_jsonb = {
            **metadata,
            "authorization_version": review.authorization_version,
            "reviewer_person_id": str(review.reviewer_person_id),
            "reviewer_role_assignment_id": str(
                review.reviewer_role_assignment_id
            ),
            "reviewer_user_id": review.reviewer_user_id,
        }

    transaction = (
        db.get(InventoryTransaction, facts.posting.inventory_transaction_id)
        if facts.posting.inventory_transaction_id is not None
        else None
    )
    establishments = db.scalars(
        select(InventoryOpeningEstablishment).where(
            InventoryOpeningEstablishment.posting_id == facts.posting.id
        )
    ).all()
    post_state = db.scalar(
        select(StateTransitionEvent).where(
            StateTransitionEvent.aggregate_type == "stocktake_task",
            StateTransitionEvent.aggregate_id == str(facts.task.id),
            StateTransitionEvent.reason == "opening_stocktake_posted",
        )
    )
    post_outbox = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "stocktake_task",
            OutboxEvent.aggregate_id == str(facts.task.id),
            OutboxEvent.event_type == "stocktake.opening.posted",
        )
    )
    post_audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.aggregate_type == "stocktake_posting",
            AuditEvent.aggregate_id == str(facts.posting.id),
            AuditEvent.action == "stocktake.opening.posted",
        )
    )
    assert (
        post_state is not None
        and post_outbox is not None
        and post_audit is not None
        and isinstance(post_state.metadata_jsonb, dict)
        and isinstance(post_audit.after_jsonb, dict)
    )
    post_metadata = finalize_service._post_metadata(
        posting=facts.posting,
        task_version=post_state.metadata_jsonb["task_version"],
        ledger_cursor=(
            transaction.ledger_cursor
            if transaction is not None
            else facts.task.cutoff_ledger_cursor or 0
        ),
        scope_count=len(establishments),
        pending_count=pending_count,
    )
    authorization_snapshot_keys = (
        "assignment_revoked_at",
        "assignment_scope_id",
        "assignment_scope_type",
        "assignment_valid_from",
        "assignment_valid_to",
        "authorization_version",
        "finalizer_organization_id",
        "finalizer_organization_type",
        "finalizer_person_id",
        "finalizer_role_assignment_id",
        "finalizer_role_code",
        "finalizer_role_is_external",
        "finalizer_user_id",
    )
    post_snapshot = {
        key: post_audit.after_jsonb[key]
        for key in authorization_snapshot_keys
    }
    post_state.metadata_jsonb = post_metadata
    post_outbox.payload_jsonb = post_metadata
    post_audit.after_jsonb = {
        **post_metadata,
        **post_snapshot,
        "status": "posted",
    }

    close_state = db.scalar(
        select(StateTransitionEvent).where(
            StateTransitionEvent.aggregate_type == "stocktake_task",
            StateTransitionEvent.aggregate_id == str(facts.task.id),
            StateTransitionEvent.reason == "opening_stocktake_closed",
        )
    )
    close_outbox = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "stocktake_task",
            OutboxEvent.aggregate_id == str(facts.task.id),
            OutboxEvent.event_type == "stocktake.opening.closed",
        )
    )
    close_audit = db.scalar(
        select(AuditEvent).where(
            AuditEvent.aggregate_type == "stocktake_task",
            AuditEvent.aggregate_id == str(facts.task.id),
            AuditEvent.action == "stocktake.opening.closed",
        )
    )
    assert (
        close_state is not None
        and close_outbox is not None
        and close_audit is not None
        and isinstance(close_state.metadata_jsonb, dict)
        and isinstance(close_audit.after_jsonb, dict)
    )
    close_metadata = {
        "idempotency_key_hash": close_state.metadata_jsonb[
            "idempotency_key_hash"
        ],
        "inventory_transaction_id": (
            str(facts.posting.inventory_transaction_id)
            if facts.posting.inventory_transaction_id is not None
            else None
        ),
        "posting_id": str(facts.posting.id),
        "request_hash": close_state.metadata_jsonb["request_hash"],
        "task_version": facts.task.version,
    }
    close_snapshot = {
        key: close_audit.after_jsonb[key]
        for key in authorization_snapshot_keys
    }
    close_state.metadata_jsonb = close_metadata
    close_outbox.payload_jsonb = close_metadata
    close_audit.after_jsonb = {
        **close_metadata,
        **close_snapshot,
        "status": "closed",
    }

    previous_hash: str | None = None
    events = tuple(
        db.scalars(
            select(AuditEvent)
            .where(AuditEvent.stream_key == posting_service.INVENTORY_STREAM_KEY)
            .order_by(AuditEvent.stream_version)
        ).all()
    )
    for event_row in events:
        event_row.previous_hash = previous_hash
        event_row.event_hash = calculate_audit_event_hash(
            stream_key=event_row.stream_key,
            event_id=event_row.id,
            actor_user_id=event_row.actor_user_id,
            action=event_row.action,
            aggregate_type=event_row.aggregate_type,
            aggregate_id=event_row.aggregate_id,
            before_jsonb=event_row.before_jsonb,
            after_jsonb=event_row.after_jsonb,
            request_id=event_row.request_id,
            previous_hash=previous_hash,
            occurred_at=event_row.occurred_at,
        )
        previous_hash = event_row.event_hash
    head = db.scalar(
        select(AuditChainHead).where(
            AuditChainHead.stream_key == posting_service.INVENTORY_STREAM_KEY
        )
    )
    assert head is not None
    head.version = len(events)
    head.last_event_id = events[-1].id if events else None
    head.last_hash = events[-1].event_hash if events else None
    db.flush()


def add_reviewed_pending_control_difference(
    db: Session,
    facts: SimpleNamespace,
    *,
    include_headquarters_item: bool = True,
) -> StocktakeDifference:
    """Attach one control-only difference explicitly held by both reviews."""

    import app.formal_services.opening_stocktake_count as count_service

    token = uuid.uuid4().hex
    business_key = f"CONTROL-{token}"
    source_updated_at = NOW - timedelta(hours=5)
    payload = posting_service.opening_control_projection_payload(
        external_business_key=business_key,
        region_org_id=facts.task.region_org_id,
        material_id=None,
        condition_code=None,
        control_qty=Decimal("1.000"),
        mapping_status="unresolved",
        mapping_note="OAM 控制账行暂未映射到正式物料",
    )
    payload_sha256 = posting_service.canonical_opening_manifest_sha256(payload)
    external_object = ExternalObject(
        id=uuid.uuid4(),
        source_system_id=facts.sync_run.source_system_id,
        entity_type=posting_service.OPENING_CONTROL_ENTITY_TYPE,
        external_id=business_key,
        current_version_id=None,
        deleted_at=None,
        created_at=source_updated_at,
        updated_at=source_updated_at,
    )
    db.add(external_object)
    db.flush()
    source_version = f"v-{token}"
    external_version = ExternalObjectVersion(
        id=uuid.uuid4(),
        external_object_id=external_object.id,
        source_version=source_version,
        source_updated_at=source_updated_at,
        valid_from=source_updated_at,
        valid_to=None,
        payload_jsonb=payload,
        payload_sha256=payload_sha256,
        is_current=True,
        created_at=source_updated_at,
    )
    db.add(external_version)
    db.flush()
    external_object.current_version_id = external_version.id
    inbox_event = SyncInboxEvent(
        id=uuid.uuid4(),
        batch_id=facts.sync_batch.id,
        source_system_id=facts.sync_run.source_system_id,
        external_event_id=f"EVENT-{token}",
        entity_type=posting_service.OPENING_CONTROL_ENTITY_TYPE,
        external_id=business_key,
        source_version=source_version,
        source_updated_at=source_updated_at,
        payload_jsonb=payload,
        payload_sha256=payload_sha256,
        status="applied",
        error_code=None,
        error_detail=None,
        processed_at=facts.sync_run.completed_at,
        created_at=source_updated_at,
    )
    db.add(inbox_event)
    db.flush()
    facts.sync_batch.record_count = 1
    facts.sync_batch.body_sha256 = (
        posting_service.opening_control_batch_body_sha256(
            sequence=facts.sync_batch.sequence,
            events=(
                {
                    "event_sort_key": str(inbox_event.id),
                    "external_event_id": inbox_event.external_event_id,
                    "external_id": inbox_event.external_id,
                    "payload_sha256": inbox_event.payload_sha256,
                    "source_updated_at": posting_service._canonical_datetime(
                        source_updated_at
                    ),
                    "source_version": inbox_event.source_version,
                },
            ),
        )
    )
    control_line = StocktakeControlSnapshotLine(
        id=uuid.uuid4(),
        task_id=facts.task.id,
        line_no=1,
        external_business_key=business_key,
        external_object_version_id=external_version.id,
        material_id=None,
        condition_code=None,
        control_qty=Decimal("1.000"),
        mapping_status="unresolved",
        source_updated_at=source_updated_at,
        payload_sha256=payload_sha256,
        mapping_note="OAM 控制账行暂未映射到正式物料",
        created_at=facts.task.frozen_at,
    )
    db.add(control_line)
    db.flush()
    difference = StocktakeDifference(
        id=uuid.uuid4(),
        task_id=facts.task.id,
        round_id=facts.round.id,
        scope_id=None,
        control_snapshot_line_id=control_line.id,
        difference_no=1,
        difference_type="control_unassigned",
        material_id=None,
        expected_account_id=None,
        observed_account_id=None,
        serial_id=None,
        book_qty=Decimal("1.000"),
        counted_qty=Decimal("0.000"),
        difference_qty=Decimal("-1.000"),
        affected_qty=Decimal("1.000"),
        reason_code="opening_control_reconciliation",
        reason_text="OAM 控制行无法唯一映射到本地实物维度",
        evidence_required=True,
        created_at=facts.round.submitted_at,
    )
    db.add(difference)
    db.flush()
    review_items = [
        StocktakeReviewItem(
            review_id=facts.regional_review.id,
            difference_id=difference.id,
            task_id=facts.task.id,
            round_id=facts.round.id,
            decision="pending_verification",
            comment="区域确认待核实",
            created_at=facts.regional_review.reviewed_at,
        )
    ]
    if include_headquarters_item:
        review_items.append(
            StocktakeReviewItem(
                review_id=facts.headquarters_review.id,
                difference_id=difference.id,
                task_id=facts.task.id,
                round_id=facts.round.id,
                decision="pending_verification",
                comment="总部确认待核实",
                created_at=facts.headquarters_review.reviewed_at,
            )
        )
    db.add_all(review_items)
    facts.establishment.has_pending_control_difference = True
    facts.task.control_manifest_sha256 = (
        posting_service.canonical_opening_control_manifest_sha256(
            facts.task,
            facts.sync_run,
            (control_line,),
        )
    )
    facts.sync_run.manifest_sha256 = facts.task.control_manifest_sha256
    facts.round.count_manifest_sha256 = (
        posting_service.canonical_opening_count_manifest_sha256(
            facts.task,
            facts.round,
            (facts.count_line,),
            (),
        )
    )
    facts.submission.count_manifest_sha256 = facts.round.count_manifest_sha256
    facts.submission.request_sha256 = count_service._hash_document(
        {
            "count_manifest_sha256": facts.round.count_manifest_sha256,
            "round_id": str(facts.round.id),
            "round_manifest_sha256": facts.submission.round_manifest_sha256,
            "schema": "cloud_oam.opening_stocktake.round_submission_request.v3",
            "sealing_completion_id": str(
                facts.submission.sealing_completion_id
            ),
        }
    )
    facts.establishment.control_manifest_sha256 = (
        facts.task.control_manifest_sha256
    )
    facts.establishment.count_manifest_sha256 = (
        facts.round.count_manifest_sha256
    )
    decisions = {difference.id: "pending_verification"}
    facts.regional_review.decision_manifest_sha256 = (
        posting_service.canonical_opening_decision_manifest_sha256(
            task_id=facts.task.id,
            round_id=facts.round.id,
            differences=(difference,),
            decisions=decisions,
        )
    )
    if include_headquarters_item:
        facts.headquarters_review.decision_manifest_sha256 = (
            posting_service.canonical_opening_decision_manifest_sha256(
                task_id=facts.task.id,
                round_id=facts.round.id,
                differences=(difference,),
                decisions=decisions,
            )
        )
    difference_summary = count_service._difference_set_summary(
        task=facts.task,
        round_row=facts.round,
        submission=facts.submission,
        differences=(difference,),
    )
    facts.completion.difference_count = difference_summary["difference_count"]
    facts.completion.physical_difference_count = difference_summary[
        "physical_difference_count"
    ]
    facts.completion.control_difference_count = difference_summary[
        "control_difference_count"
    ]
    facts.completion.pending_observation_difference_count = difference_summary[
        "pending_observation_difference_count"
    ]
    facts.completion.total_affected_qty = difference_summary[
        "total_affected_qty"
    ]
    facts.completion.difference_manifest_sha256 = difference_summary[
        "difference_manifest_sha256"
    ]
    facts.completion.request_sha256 = (
        count_service._difference_set_request_sha256(difference_summary)
    )
    db.flush()
    refresh_opening_review_effects(db, facts)
    facts.control_line = control_line
    facts.external_object = external_object
    facts.external_version = external_version
    facts.inbox_event = inbox_event
    return difference


def make_positive_opening_facts(
    db: Session,
    facts: SimpleNamespace,
    *,
    quantity: Decimal,
    serials: tuple[InventorySerial, ...] = (),
) -> SimpleNamespace:
    """Turn one reviewed zero fixture into an exact positive opening posting."""

    import app.formal_services.opening_stocktake_count as count_service

    assert quantity > 0
    assert not serials or Decimal(len(serials)) == quantity
    account = db.get(StockAccount, facts.count_line.stock_account_id)
    assert account is not None
    material = db.get(FormalMaterial, account.material_id)
    assert material is not None and facts.task.cutoff_at is not None
    policies = [
        row
        for row in db.scalars(
            select(MaterialInventoryPolicy).where(
                MaterialInventoryPolicy.material_id == account.material_id
            )
        ).all()
        if count_service._as_utc(row.effective_from)
        <= count_service._as_utc(facts.task.cutoff_at)
        and (
            row.effective_to is None
            or count_service._as_utc(facts.task.cutoff_at)
            < count_service._as_utc(row.effective_to)
        )
    ]
    assert len(policies) == 1
    policy = policies[0]
    if policy.tracking_mode in {"serial", "lot_and_serial"}:
        assert serials and Decimal(len(serials)) == quantity
    else:
        assert not serials
    facts.count_line.counted_qty = quantity
    facts.count_line.count_method = "manual"
    facts.count_line.reason_code = None
    facts.count_line.remark = ""
    facts.count_line.updated_at = facts.round.submitted_at
    count_serials = tuple(
        StocktakeCountSerial(
            count_line_id=facts.count_line.id,
            round_id=facts.round.id,
            serial_id=serial.id,
            result="unexpected",
            created_at=facts.count_line.counted_at,
        )
        for serial in serials
    )
    db.add_all(count_serials)

    existing_differences = db.scalars(
        select(StocktakeDifference)
        .where(
            StocktakeDifference.task_id == facts.task.id,
            StocktakeDifference.round_id == facts.round.id,
        )
        .order_by(StocktakeDifference.difference_no)
    ).all()
    for existing_difference in existing_differences:
        existing_difference.difference_no += 1
    excess = StocktakeDifference(
        id=uuid.uuid4(),
        task_id=facts.task.id,
        round_id=facts.round.id,
        scope_id=facts.scope.id,
        control_snapshot_line_id=None,
        difference_no=1,
        difference_type="excess",
        material_id=account.material_id,
        expected_account_id=None,
        observed_account_id=facts.count_line.stock_account_id,
        serial_id=None,
        book_qty=Decimal("0.000"),
        counted_qty=quantity,
        difference_qty=quantity,
        affected_qty=quantity,
        reason_code="opening_physical_excess",
        reason_text="期初实物盘点数量，仅待复核后建立个人仓库存",
        evidence_required=True,
        created_at=facts.round.submitted_at,
    )
    db.add(excess)
    db.flush()
    db.execute(
        update(StocktakeCountLine)
        .where(StocktakeCountLine.id == facts.count_line.id)
        .values(updated_at=facts.round.submitted_at)
    )
    db.refresh(facts.count_line)
    for review in (facts.regional_review, facts.headquarters_review):
        db.add(
            StocktakeReviewItem(
                review_id=review.id,
                difference_id=excess.id,
                task_id=facts.task.id,
                round_id=facts.round.id,
                decision="accept_for_posting",
                comment="确认期初实盘入账",
                created_at=review.reviewed_at,
            )
        )

    head = db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID)
    assert head is not None
    ledger_cursor = head.next_cursor
    head.next_cursor += 1
    posted_at = posting_service._persisted_timestamp_utc(
        facts.posting.posted_at
    )
    assert posted_at is not None
    transaction = InventoryTransaction(
        id=uuid.uuid4(),
        transaction_no=f"OPEN-{facts.task.id.hex}",
        movement_type="opening",
        source_document_type="opening_stocktake",
        source_document_id=str(facts.task.id),
        posting_key=f"opening-stocktake:{facts.task.id}",
        idempotency_key_hash=finalize_service._derived_hash(
            "transaction-idempotency", facts.posting.idempotency_key_hash
        ),
        request_hash=finalize_service._derived_hash(
            "transaction-request", facts.posting.request_hash
        ),
        status="posted",
        effective_at=facts.task.cutoff_at,
        posted_at=posted_at,
        ledger_cursor=ledger_cursor,
        reversed_transaction_id=None,
        actor_user_id=facts.posting.posted_by_user_id,
        created_at=posted_at,
    )
    db.add(transaction)
    db.flush()
    movement = InventoryMovement(
        id=uuid.uuid4(),
        transaction_id=transaction.id,
        line_no=1,
        from_account_id=None,
        to_account_id=facts.count_line.stock_account_id,
        external_boundary_code="approved-opening-stocktake",
        quantity=quantity,
        created_at=posted_at,
    )
    db.add(movement)
    db.flush()
    movement_serials = tuple(
        InventoryMovementSerial(
            movement_id=movement.id,
            transaction_id=transaction.id,
            serial_id=serial.id,
            created_at=posted_at,
        )
        for serial in serials
    )
    db.add_all(movement_serials)
    db.flush()
    for serial in serials:
        db.add(
            SerialCurrentPosition(
                serial_id=serial.id,
                stock_account_id=facts.count_line.stock_account_id,
                last_movement_id=movement.id,
                updated_at=posted_at,
            )
        )
    balance = db.get(StockBalance, facts.count_line.stock_account_id)
    if balance is None:
        balance = StockBalance(
            stock_account_id=facts.count_line.stock_account_id,
            quantity=quantity,
            ledger_cursor=ledger_cursor,
            version=1,
            created_at=posted_at,
            updated_at=posted_at,
        )
        db.add(balance)
    else:
        assert (
            balance.quantity == Decimal("0.000")
            and balance.ledger_cursor == 0
            and balance.version == 0
        )
        balance.quantity = quantity
        balance.ledger_cursor = ledger_cursor
        balance.version = 1
        balance.updated_at = posted_at
    facts.posting.inventory_transaction_id = transaction.id
    facts.posting.total_quantity = quantity
    posting_item = StocktakePostingItem(
        posting_id=facts.posting.id,
        inventory_movement_id=movement.id,
        task_id=facts.task.id,
        round_id=facts.round.id,
        count_line_id=facts.count_line.id,
        difference_id=None,
        quantity=quantity,
        created_at=posted_at,
    )
    db.add(posting_item)
    facts.establishment.established_ledger_cursor = ledger_cursor

    request_reference = finalize_service._request_reference(
        "inventory", f"positive-opening-fixture:{facts.task.id}"
    )
    db.add_all(
        [
            StateTransitionEvent(
                aggregate_type="inventory_transaction",
                aggregate_id=str(transaction.id),
                from_status=None,
                to_status="posted",
                reason="inventory_transaction_posted",
                actor_id=transaction.actor_user_id,
                idempotency_key=posting_service._derived_evidence_key(
                    "state", transaction.id, "posted"
                ),
                occurred_at=posted_at,
                metadata_jsonb={
                    "ledger_cursor": ledger_cursor,
                    "movement_type": "opening",
                    "request_reference": request_reference,
                },
                created_at=posted_at,
            ),
            OutboxEvent(
                event_type="inventory.transaction.posted",
                aggregate_type="inventory_transaction",
                aggregate_id=str(transaction.id),
                payload_jsonb={
                    "transaction_id": str(transaction.id),
                    "transaction_no": transaction.transaction_no,
                    "movement_type": "opening",
                    "ledger_cursor": ledger_cursor,
                    "reversed_transaction_id": None,
                },
                status="pending",
                attempts=0,
                idempotency_key=posting_service._derived_evidence_key(
                    "outbox", transaction.id, "posted"
                ),
                available_at=posted_at,
                locked_at=None,
                locked_by=None,
                published_at=None,
                last_error=None,
                created_at=posted_at,
                updated_at=posted_at,
            ),
        ]
    )
    db.flush()
    append_audit_event(
        db,
        stream_key=posting_service.INVENTORY_STREAM_KEY,
        actor_user_id=transaction.actor_user_id,
        action="inventory.transaction.posted",
        aggregate_type="inventory_transaction",
        aggregate_id=str(transaction.id),
        before_jsonb=None,
        after_jsonb={
            "ledger_cursor": ledger_cursor,
            "movement_count": 1,
            "movement_type": "opening",
            "posting_key": transaction.posting_key,
            "reversed_transaction_id": None,
            "status": "posted",
        },
        request_id=request_reference,
        occurred_at=posted_at,
    )

    all_differences = (excess, *existing_differences)
    decisions = {
        difference.id: (
            "pending_verification"
            if difference.difference_type == "control_unassigned"
            else "accept_for_posting"
        )
        for difference in all_differences
    }
    decision_manifest = posting_service.canonical_opening_decision_manifest_sha256(
        task_id=facts.task.id,
        round_id=facts.round.id,
        differences=all_differences,
        decisions=decisions,
    )
    facts.regional_review.decision_manifest_sha256 = decision_manifest
    facts.headquarters_review.decision_manifest_sha256 = decision_manifest
    facts.round.count_manifest_sha256 = (
        posting_service.canonical_opening_count_manifest_sha256(
            facts.task,
            facts.round,
            (facts.count_line,),
            count_serials,
        )
    )
    facts.scope_completion.serial_count = len(count_serials)
    facts.scope_completion.total_counted_qty = quantity
    lot_no = None
    if account.lot_id is not None:
        from app.inventory_models import InventoryLot

        lot = db.get(InventoryLot, account.lot_id)
        assert lot is not None
        lot_no = lot.lot_no
    if policy.tracking_mode in {"lot", "lot_and_serial"}:
        assert lot_no is not None
    else:
        assert lot_no is None
    request_pairs = []
    for serial in serials or (None,):
        request_item = {
            "availability_bucket": account.availability_bucket,
            "condition_code": account.condition_code,
            "count_method": "manual",
            "counted_qty": count_service._canonical_decimal(
                Decimal("1") if serial is not None else quantity
            ),
            "lot_id": None,
            "lot_no_raw": lot_no,
            "material_id": None,
            "material_identifier_raw": material.sku_code,
            "material_identifier_type": "sku_code",
            "reason_code": None,
            "remark": "",
            "serial_id": None,
            "serial_identifier_type": (
                "serial_no" if serial is not None else None
            ),
            "serial_no_raw": serial.serial_no if serial is not None else None,
        }
        request_pairs.append((request_item, serial))
    request_pairs.sort(key=lambda row: count_service._canonical_json(row[0]))
    scope_request_document = {
        "actor_person_id": str(facts.scope_completion.completed_by_person_id),
        "actor_user_id": facts.scope_completion.completed_by_user_id,
        "physical_observations": [row[0] for row in request_pairs],
        "round_id": str(facts.round.id),
        "schema": "cloud_oam.opening_stocktake.scope_count_request.v1",
        "scope_id": str(facts.scope.id),
        "task_id": str(facts.task.id),
        "zero_confirmed": False,
    }
    scope_request_sha256 = count_service._hash_document(scope_request_document)
    facts.scope_completion.request_jsonb = scope_request_document
    facts.scope_completion.request_sha256 = scope_request_sha256
    facts.scope_completion.request_resolution_jsonb = {
        "items": [
            {
                "material_qr_mapping_id": None,
                "policy": {
                    "allow_fraction": policy.allow_fraction,
                    "effective_from": count_service._canonical_timestamp(
                        policy.effective_from
                    ),
                    "id": str(policy.id),
                    "quantity_scale": policy.quantity_scale,
                    "tracking_mode": policy.tracking_mode,
                },
                "request_item_sha256": count_service._hash_document(
                    request_item
                ),
                "request_ordinal": ordinal,
                "resolved_lot_id": (
                    str(account.lot_id) if account.lot_id is not None else None
                ),
                "resolved_material_id": str(account.material_id),
                "resolved_serial_id": (
                    str(serial.id) if serial is not None else None
                ),
                "serial_alias_keys": (
                    sorted(
                        {
                            count_service._fold_serial_alias(serial.serial_no),
                            count_service._fold_serial_alias(serial.qr_code),
                        }
                    )
                    if serial is not None
                    else []
                ),
                "serial_qr_mapping_id": None,
                "target_id": str(facts.count_line.id),
                "target_type": "count_line",
            }
            for ordinal, (request_item, serial) in enumerate(
                request_pairs,
                start=1,
            )
        ],
        "request_sha256": scope_request_sha256,
        "round_id": str(facts.round.id),
        "schema": (
            "cloud_oam.opening_stocktake."
            "scope_count_request_resolution.v1"
        ),
        "scope_id": str(facts.scope.id),
        "task_id": str(facts.task.id),
    }
    facts.scope_completion.evidence_manifest_sha256 = (
        count_service._scope_evidence_manifest(
            db,
            facts.task.id,
            facts.round.id,
            facts.scope.id,
            (facts.count_line,),
            (),
            facts.scope_completion.authorization_sha256,
        )
    )
    facts.submission.serial_count = len(count_serials)
    facts.submission.total_counted_qty = quantity
    facts.submission.round_manifest_sha256 = (
        count_service._round_manifest_sha256(
            facts.task.id,
            facts.round.id,
            (facts.scope_completion,),
            facts.scope_completion.id,
        )
    )
    facts.submission.count_manifest_sha256 = facts.round.count_manifest_sha256
    facts.submission.request_sha256 = count_service._hash_document(
        {
            "count_manifest_sha256": facts.round.count_manifest_sha256,
            "round_id": str(facts.round.id),
            "round_manifest_sha256": facts.submission.round_manifest_sha256,
            "schema": "cloud_oam.opening_stocktake.round_submission_request.v3",
            "sealing_completion_id": str(
                facts.submission.sealing_completion_id
            ),
        }
    )
    difference_summary = count_service._difference_set_summary(
        task=facts.task,
        round_row=facts.round,
        submission=facts.submission,
        differences=all_differences,
    )
    facts.completion.difference_count = difference_summary["difference_count"]
    facts.completion.physical_difference_count = difference_summary[
        "physical_difference_count"
    ]
    facts.completion.control_difference_count = difference_summary[
        "control_difference_count"
    ]
    facts.completion.pending_observation_difference_count = difference_summary[
        "pending_observation_difference_count"
    ]
    facts.completion.total_affected_qty = difference_summary[
        "total_affected_qty"
    ]
    facts.completion.difference_manifest_sha256 = difference_summary[
        "difference_manifest_sha256"
    ]
    facts.completion.request_sha256 = (
        count_service._difference_set_request_sha256(difference_summary)
    )
    facts.establishment.count_manifest_sha256 = facts.round.count_manifest_sha256
    db.flush()
    refresh_opening_review_effects(db, facts)
    return SimpleNamespace(
        transaction=transaction,
        movement=movement,
        movement_serials=movement_serials,
        posting_item=posting_item,
        excess_difference=excess,
        count_serials=count_serials,
    )


def add_zero_scope_to_opening_task(
    db: Session,
    facts: SimpleNamespace,
    account: StockAccount,
) -> SimpleNamespace:
    """Add a second exact zero scope to exercise task-atomic rereads."""

    import app.formal_services.opening_stocktake_count as count_service
    round_submitted_at = posting_service._persisted_timestamp_utc(
        facts.round.submitted_at
    )
    assert round_submitted_at is not None

    scope = FormalStocktakeScope(
        id=uuid.uuid4(),
        task_id=facts.task.id,
        scope_no=2,
        scope_mode="location_all",
        location_id=account.location_id,
        owner_org_id=account.owner_org_id,
        custodian_person_id_snapshot=account.custodian_person_id,
        assignee_user_id=facts.scope.assignee_user_id,
        material_id=None,
        condition_code=None,
        availability_bucket=None,
        scope_key=f"opening:{account.owner_org_id}:{account.location_id}",
        scope_sha256="0" * 64,
        created_at=facts.task.cutoff_at,
    )
    db.add(scope)
    db.flush()
    scope.scope_sha256 = posting_service.canonical_opening_scope_line_sha256(scope)
    freeze = InventoryFreeze(
        id=uuid.uuid4(),
        task_id=facts.task.id,
        stocktake_scope_id=scope.id,
        scope_key=scope.scope_key,
        freeze_mode="hard",
        status="released",
        valid_from=facts.task.cutoff_at,
        valid_to=facts.posting.posted_at,
        created_by_user_id=facts.task.created_by_user_id,
        released_by_user_id=facts.posting.posted_by_user_id,
        release_reason="期初入账已完成",
        version=1,
        created_at=facts.task.cutoff_at,
        updated_at=facts.posting.posted_at,
    )
    snapshot = StocktakeSnapshotLine(
        id=uuid.uuid4(),
        task_id=facts.task.id,
        scope_id=scope.id,
        stock_account_id=account.id,
        book_qty=Decimal("0.000"),
        ledger_cursor=facts.task.cutoff_ledger_cursor,
        account_dimension_sha256=(
            posting_service.canonical_opening_account_dimension_sha256(account)
        ),
        serial_snapshot_jsonb=[],
        serial_snapshot_sha256=(
            posting_service.canonical_opening_serial_snapshot_sha256(
                stock_account_id=account.id,
                serials=(),
            )
        ),
        serial_count=0,
        created_at=facts.task.cutoff_at,
    )
    count_line = StocktakeCountLine(
        id=uuid.uuid4(),
        task_id=facts.task.id,
        round_id=facts.round.id,
        scope_id=scope.id,
        stock_account_id=account.id,
        counted_qty=Decimal("0.000"),
        count_method="manual",
        reason_code="scope_full_set_zero",
        remark="",
        counted_by_user_id=scope.assignee_user_id,
        counted_at=round_submitted_at,
        created_at=round_submitted_at,
        updated_at=round_submitted_at,
    )
    db.add_all([freeze, snapshot, count_line])
    db.flush()
    scope_request_document = {
        "actor_person_id": str(
            facts.scope_completion.completed_by_person_id
        ),
        "actor_user_id": facts.scope_completion.completed_by_user_id,
        "physical_observations": [],
        "round_id": str(facts.round.id),
        "schema": "cloud_oam.opening_stocktake.scope_count_request.v1",
        "scope_id": str(scope.id),
        "task_id": str(facts.task.id),
        "zero_confirmed": False,
    }
    scope_request_sha256 = count_service._hash_document(scope_request_document)
    scope_request_resolution = {
        "items": [],
        "request_sha256": scope_request_sha256,
        "round_id": str(facts.round.id),
        "schema": (
            "cloud_oam.opening_stocktake."
            "scope_count_request_resolution.v1"
        ),
        "scope_id": str(scope.id),
        "task_id": str(facts.task.id),
    }
    scope_completion = StocktakeScopeCountCompletion(
        id=uuid.uuid4(),
        task_id=facts.task.id,
        round_id=facts.round.id,
        scope_id=scope.id,
        count_line_count=1,
        observation_line_count=0,
        serial_count=0,
        total_counted_qty=Decimal("0.000"),
        zero_confirmed=False,
        evidence_manifest_sha256="0" * 64,
        request_sha256=scope_request_sha256,
        request_jsonb=scope_request_document,
        request_resolution_jsonb=scope_request_resolution,
        idempotency_key_hash=hashlib.sha256(
            f"scope-completion:{scope.id}".encode()
        ).hexdigest(),
        completed_by_user_id=facts.scope_completion.completed_by_user_id,
        completed_by_person_id=facts.scope_completion.completed_by_person_id,
        completed_role_assignment_id=(
            facts.scope_completion.completed_role_assignment_id
        ),
        authorization_version=facts.scope_completion.authorization_version,
        role_code=facts.scope_completion.role_code,
        scope_type=facts.scope_completion.scope_type,
        scope_id_snapshot=facts.scope_completion.scope_id_snapshot,
        authorization_sha256=facts.scope_completion.authorization_sha256,
        completed_at=round_submitted_at,
        created_at=round_submitted_at,
    )
    db.add(scope_completion)
    db.flush()
    scope_completion.evidence_manifest_sha256 = (
        count_service._scope_evidence_manifest(
            db,
            facts.task.id,
            facts.round.id,
            scope.id,
            (count_line,),
            (),
            scope_completion.authorization_sha256,
        )
    )
    scopes = (facts.scope, scope)
    snapshots = (facts.snapshot, snapshot)
    counts = (facts.count_line, count_line)
    count_serials = tuple(
        db.scalars(
            select(StocktakeCountSerial)
            .where(StocktakeCountSerial.round_id == facts.round.id)
            .order_by(
                StocktakeCountSerial.count_line_id,
                StocktakeCountSerial.serial_id,
            )
        ).all()
    )
    facts.task.scope_manifest_sha256 = (
        posting_service.canonical_opening_scope_manifest_sha256(
            facts.task,
            scopes,
            {facts.scope.id: facts.freeze, scope.id: freeze},
        )
    )
    facts.task.snapshot_manifest_sha256 = (
        posting_service.canonical_opening_snapshot_manifest_sha256(
            facts.task,
            scopes,
            snapshots,
        )
    )
    facts.round.count_manifest_sha256 = (
        posting_service.canonical_opening_count_manifest_sha256(
            facts.task,
            facts.round,
            counts,
            count_serials,
        )
    )
    round_completions = (facts.scope_completion, scope_completion)
    facts.submission.scope_count = len(round_completions)
    facts.submission.zero_scope_count = sum(
        1 for row in round_completions if row.zero_confirmed
    )
    facts.submission.count_line_count = sum(
        row.count_line_count for row in round_completions
    )
    facts.submission.observation_line_count = sum(
        row.observation_line_count for row in round_completions
    )
    facts.submission.serial_count = sum(
        row.serial_count for row in round_completions
    )
    facts.submission.total_counted_qty = sum(
        (row.total_counted_qty for row in round_completions),
        start=Decimal("0.000"),
    )
    facts.submission.round_manifest_sha256 = (
        count_service._round_manifest_sha256(
            facts.task.id,
            facts.round.id,
            round_completions,
            facts.submission.sealing_completion_id,
        )
    )
    facts.submission.count_manifest_sha256 = facts.round.count_manifest_sha256
    facts.submission.request_sha256 = count_service._hash_document(
        {
            "count_manifest_sha256": facts.round.count_manifest_sha256,
            "round_id": str(facts.round.id),
            "round_manifest_sha256": facts.submission.round_manifest_sha256,
            "schema": "cloud_oam.opening_stocktake.round_submission_request.v3",
            "sealing_completion_id": str(
                facts.submission.sealing_completion_id
            ),
        }
    )
    facts.establishment.scope_manifest_sha256 = facts.task.scope_manifest_sha256
    facts.establishment.snapshot_manifest_sha256 = (
        facts.task.snapshot_manifest_sha256
    )
    facts.establishment.count_manifest_sha256 = facts.round.count_manifest_sha256
    count_context = count_service._round_event_context(facts.task, facts.round)
    scope_state_reason, _ = count_service._round_state_reasons(facts.round)
    db.add_all(
        [
            StateTransitionEvent(
                aggregate_type="stocktake_scope",
                aggregate_id=str(scope.id),
                from_status="counting",
                to_status="completed",
                reason=scope_state_reason,
                actor_id=scope_completion.completed_by_user_id,
                idempotency_key=count_service._event_key(
                    "scope-state", facts.round.id, scope.id
                ),
                occurred_at=scope_completion.completed_at,
                metadata_jsonb={
                    **count_context,
                    "zero_confirmed": False,
                },
                created_at=scope_completion.completed_at,
            ),
            OutboxEvent(
                event_type="stocktake.opening.scope_count_completed",
                aggregate_type="stocktake_scope",
                aggregate_id=str(scope.id),
                payload_jsonb={
                    **count_context,
                    "round_sealed": False,
                    "scope_id": str(scope.id),
                },
                status="pending",
                attempts=0,
                idempotency_key=count_service._event_key(
                    "scope-outbox", facts.round.id, scope.id
                ),
                available_at=scope_completion.completed_at,
                locked_at=None,
                locked_by=None,
                published_at=None,
                last_error=None,
                created_at=scope_completion.completed_at,
                updated_at=scope_completion.completed_at,
            ),
        ]
    )
    db.flush()
    append_audit_event(
        db,
        stream_key=posting_service.INVENTORY_STREAM_KEY,
        actor_user_id=scope_completion.completed_by_user_id,
        action="stocktake.opening.scope_count_completed",
        aggregate_type="stocktake_scope",
        aggregate_id=str(scope.id),
        before_jsonb=None,
        after_jsonb={
            **count_context,
            "has_pending_verification": False,
            "round_sealed": False,
            "zero_confirmed": False,
        },
        request_id=(
            "opening-count-request-"
            + hashlib.sha256(
                f"opening-zero-scope-fixture:{scope.id}".encode()
            ).hexdigest()
        ),
        occurred_at=scope_completion.completed_at,
    )
    db.flush()
    establishment = InventoryOpeningEstablishment(
        id=uuid.uuid4(),
        task_id=facts.task.id,
        scope_id=scope.id,
        owner_org_id=account.owner_org_id,
        location_id=account.location_id,
        round_id=facts.round.id,
        posting_id=facts.posting.id,
        regional_review_id=facts.regional_review.id,
        headquarters_review_id=facts.headquarters_review.id,
        cutoff_ledger_cursor=facts.task.cutoff_ledger_cursor,
        cutoff_at=facts.task.cutoff_at,
        established_ledger_cursor=facts.establishment.established_ledger_cursor,
        scope_manifest_sha256=facts.task.scope_manifest_sha256,
        snapshot_manifest_sha256=facts.task.snapshot_manifest_sha256,
        count_manifest_sha256=facts.round.count_manifest_sha256,
        control_manifest_sha256=facts.task.control_manifest_sha256,
        has_pending_control_difference=(
            facts.establishment.has_pending_control_difference
        ),
        established_by_user_id=facts.establishment.established_by_user_id,
        established_at=facts.establishment.established_at,
        created_at=facts.establishment.created_at,
    )
    db.add(establishment)
    db.flush()
    refresh_opening_review_effects(db, facts)
    return SimpleNamespace(
        scope=scope,
        freeze=freeze,
        snapshot=snapshot,
        count_line=count_line,
        scope_completion=scope_completion,
        establishment=establishment,
    )


def assert_opening_guard_rejects(
    db: Session,
    world: SimpleNamespace,
    account: StockAccount,
) -> None:
    db.commit()
    with pytest.raises(InventoryPostingError) as captured:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account.id,
                        quantity=Decimal("1"),
                        external_boundary_code="external-source",
                    ),
                ),
            ),
        )
    assert captured.value.code == "inventory_opening_establishment_invalid"
    db.rollback()


def command(
    movement_type: str,
    movements: tuple[InventoryMovementCommand, ...],
    *,
    suffix: str | None = None,
    effective_at: datetime = NOW,
) -> InventoryPostingCommand:
    token = suffix or uuid.uuid4().hex
    return InventoryPostingCommand(
        transaction_no=f"TX-{token}",
        movement_type=movement_type,
        source_document_type="test_case",
        source_document_id=f"DOC-{token}",
        posting_key=f"inventory:test:{token}",
        effective_at=effective_at,
        movements=movements,
    )


def post(
    db: Session,
    world: SimpleNamespace,
    posting_command: InventoryPostingCommand,
    *,
    key: str | None = None,
):
    return post_inventory_transaction(
        db,
        actor=world.current_principal,
        command=posting_command,
        idempotency_key=key or f"idempotency-{uuid.uuid4().hex}",
        request_id=f"request-{uuid.uuid4().hex}",
    )


def test_internal_transfer_posts_one_immutable_transaction_and_evidence(
    db: Session, world: SimpleNamespace
):
    source = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("10"),
    )
    destination = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("0"),
    )
    db.commit()
    posting_command = command(
        "transfer",
        (
            InventoryMovementCommand(
                from_account_id=source.id,
                to_account_id=destination.id,
                quantity=Decimal("3.250"),
            ),
        ),
    )
    cursor_before = db.get(
        InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID
    ).next_cursor
    movement_count_before = db.scalar(
        select(func.count()).select_from(InventoryMovement)
    )

    result = post(db, world, posting_command)

    assert result.replayed is False
    assert result.no == result.transaction_no
    assert result.ledger_cursor == cursor_before
    assert db.get(StockBalance, source.id).quantity == Decimal("6.750")
    assert db.get(StockBalance, destination.id).quantity == Decimal("3.250")
    transaction = db.get(InventoryTransaction, result.transaction_id)
    assert transaction.status == "posted"
    assert transaction.movement_type == "transfer"
    assert db.scalar(
        select(func.count()).select_from(InventoryMovement)
    ) == movement_count_before + 1
    assert db.scalar(
        select(func.count()).select_from(StateTransitionEvent).where(
            StateTransitionEvent.aggregate_type == "inventory_transaction",
            StateTransitionEvent.aggregate_id == str(result.transaction_id),
        )
    ) == 1
    assert db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "inventory.transaction.posted",
            AuditEvent.aggregate_id == str(result.transaction_id),
        )
    ) == 1
    assert db.scalar(
        select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.event_type == "inventory.transaction.posted",
            OutboxEvent.aggregate_id == str(result.transaction_id),
        )
    ) == 1
    outbox = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.event_type == "inventory.transaction.posted",
            OutboxEvent.aggregate_id == str(result.transaction_id),
        )
    )
    assert outbox.event_type == "inventory.transaction.posted"
    assert "idempotency-" not in outbox.idempotency_key


def test_multi_terminal_shared_material_uses_only_one_current_owner_graph(
    db: Session,
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Overlapping historical SKU graphs never use per-task owner helpers.

    The plain historical signatures are safe only while the production ACL
    denies API/edge online writes to those masters.  An online writer requires
    a 0028 multi-task union helper before that ACL may be widened.
    """

    source = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("2.000"),
    )
    destination = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("0.000"),
    )
    db.commit()
    facts_by_account = db.info["opening_facts_by_account"]
    expected_task_ids = tuple(
        sorted(
            {
                facts_by_account[source.id].task.id,
                facts_by_account[destination.id].task.id,
            },
            key=str,
        )
    )
    assert len(expected_task_ids) == 2

    events: list[tuple[str, tuple[object, ...]]] = []
    principal_calls: list[tuple[str, ...]] = []
    real_principal = posting_service.lock_formal_principal_graph
    real_task = posting_service.lock_opening_stocktake_task_evidence
    real_inventory = posting_service.lock_inventory_reference_graph
    real_serial = posting_service.lock_inventory_serial_graph

    def principal_helper(_db, user_ids):
        checked = tuple(sorted(set(user_ids)))
        principal_calls.append(checked)
        events.append(("principal", checked))
        return real_principal(_db, user_ids)

    def task_helper(_db, task_id, round_id):
        events.append(("task", (task_id,)))
        return real_task(_db, task_id, round_id)

    def inventory_helper(_db, account_ids, effective_at):
        checked = tuple(sorted(set(account_ids), key=str))
        events.append(("inventory", checked))
        return real_inventory(_db, account_ids, effective_at)

    def serial_helper(_db, serial_ids):
        checked = tuple(sorted(set(serial_ids), key=str))
        events.append(("serial", checked))
        return real_serial(_db, serial_ids)

    monkeypatch.setattr(
        posting_service,
        "lock_formal_principal_graph",
        principal_helper,
    )
    monkeypatch.setattr(
        posting_service,
        "lock_opening_stocktake_task_evidence",
        task_helper,
    )
    monkeypatch.setattr(
        posting_service,
        "lock_inventory_reference_graph",
        inventory_helper,
    )
    monkeypatch.setattr(
        posting_service,
        "lock_inventory_serial_graph",
        serial_helper,
    )
    from app.formal_services import opening_stocktake_finalize as finalize_service

    monkeypatch.setattr(
        finalize_service,
        "lock_opening_stocktake_start_reference",
        lambda *_args, **_kwargs: pytest.fail(
            "generic terminal planner must not lock historical start graphs"
        ),
    )

    post(
        db,
        world,
        command(
            "transfer",
            (
                InventoryMovementCommand(
                    from_account_id=source.id,
                    to_account_id=destination.id,
                    quantity=Decimal("1.000"),
                ),
            ),
            suffix="multi-terminal-shared-material-owner-graph",
        ),
        key="multi-terminal-shared-material-owner-graph",
    )

    assert [kind for kind, _coordinates in events] == [
        "principal",
        "task",
        "task",
        "inventory",
        "serial",
    ]
    assert tuple(value[0] for kind, value in events if kind == "task") == (
        expected_task_ids
    )
    assert events[-2][1] == tuple(sorted((source.id, destination.id), key=str))
    assert events[-1][1] == ()
    assert len(principal_calls) == 1
    assert {
        world.user.id,
        world.headquarters_reviewer_user.id,
    }.issubset(principal_calls[0])


def test_terminal_batch_query_graph_locks_union_then_validates_purely(
    db: Session,
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("2.000"),
    )
    destination = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("0.000"),
    )
    db.commit()
    facts_by_account = db.info["opening_facts_by_account"]
    task_ids = tuple(
        {
            facts_by_account[source.id].task.id,
            facts_by_account[destination.id].task.id,
        }
    )
    assert len(task_ids) == 2

    events: list[str] = []
    audit_taken = False
    real_principal = posting_service.lock_formal_principal_graph
    real_task = finalize_service.lock_opening_stocktake_task_evidence
    real_reference_union = (
        finalize_service.lock_opening_terminal_reference_union
    )
    real_serial = finalize_service.lock_inventory_serial_graph

    def principal_helper(_db, user_ids):
        if audit_taken:
            pytest.fail("principal helper entered after the audit head")
        events.append("principal")
        return real_principal(_db, user_ids)

    def task_helper(_db, task_id, round_id):
        if audit_taken:
            pytest.fail("task-evidence helper entered after the audit head")
        events.append(f"task:{task_id}")
        return real_task(_db, task_id, round_id)

    def serial_helper(_db, serial_ids):
        if audit_taken:
            pytest.fail("serial helper entered after the audit head")
        events.append("serial")
        return real_serial(_db, serial_ids)

    def reference_union_helper(
        _db,
        requested_task_ids,
        owner_org_ids,
        location_ids,
        material_ids,
        account_ids,
    ):
        if audit_taken:
            pytest.fail("terminal reference union entered after the audit head")
        assert tuple(requested_task_ids) == tuple(sorted(task_ids, key=str))
        assert len(owner_org_ids) == len(location_ids)
        assert tuple(material_ids) == tuple(sorted(set(material_ids), key=str))
        assert tuple(account_ids) == tuple(sorted(set(account_ids), key=str))
        events.append("reference-union")
        return real_reference_union(
            _db,
            requested_task_ids,
            owner_org_ids,
            location_ids,
            material_ids,
            account_ids,
        )

    monkeypatch.setattr(
        posting_service,
        "lock_formal_principal_graph",
        principal_helper,
    )
    monkeypatch.setattr(
        finalize_service,
        "lock_opening_stocktake_task_evidence",
        task_helper,
    )
    monkeypatch.setattr(
        finalize_service,
        "lock_opening_terminal_reference_union",
        reference_union_helper,
    )
    monkeypatch.setattr(
        finalize_service,
        "lock_inventory_serial_graph",
        serial_helper,
    )
    monkeypatch.setattr(
        finalize_service,
        "lock_opening_stocktake_start_reference",
        lambda *_args, **_kwargs: pytest.fail(
            "batch query must not lock historical masters task-by-task"
        ),
    )
    monkeypatch.setattr(
        finalize_service,
        "lock_inventory_reference_graph",
        lambda *_args, **_kwargs: pytest.fail(
            "batch query must use frozen historical master signatures"
        ),
    )

    root = finalize_service._lock_opening_terminal_task_batch_root(
        db,
        task_ids=tuple(reversed(task_ids)),
    )
    graph = finalize_service._lock_opening_terminal_task_batch_graph(
        db,
        root=root,
        supplied_user_ids=(world.user.id,),
    )
    assert tuple(row.id for row in root.tasks) == tuple(
        sorted(task_ids, key=str)
    )
    assert events == [
        "principal",
        *(f"task:{task_id}" for task_id in sorted(task_ids, key=str)),
        "reference-union",
        "serial",
    ]

    _audit_head, audit_proof = _lock_audit_chain_head_with_proof(
        db,
        stream_key="inventory",
    )
    audit_taken = True
    validated = finalize_service._validate_opening_terminal_batch_from_prelocked_graph(
        db,
        proof=graph,
        audit_proof=audit_proof,
        expected_ledger_cursor=root.current_ledger_cursor,
    )
    assert tuple(task.id for task, _posting in validated) == tuple(
        sorted(task_ids, key=str)
    )

    with pytest.raises(finalize_service.OpeningStocktakeFinalizeError) as drift:
        finalize_service._validate_opening_terminal_batch_from_prelocked_graph(
            db,
            proof=graph,
            audit_proof=audit_proof,
            expected_ledger_cursor=root.current_ledger_cursor + 1,
        )
    assert drift.value.code == "opening_finalize_replay_evidence_invalid"

    second_task = root.tasks[1]
    original_manifest = second_task.scope_manifest_sha256
    second_task.scope_manifest_sha256 = "f" * 64
    with pytest.raises(finalize_service.OpeningStocktakeFinalizeError):
        finalize_service._validate_opening_terminal_batch_from_prelocked_graph(
            db,
            proof=graph,
            audit_proof=audit_proof,
            expected_ledger_cursor=root.current_ledger_cursor,
        )
    second_task.scope_manifest_sha256 = original_manifest


def test_terminal_batch_rechecks_master_signature_before_serial_union(
    db: Session,
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("1.000"),
    )
    db.commit()
    facts = db.info["opening_facts_by_account"][account.id]
    real_reference_union = (
        finalize_service.lock_opening_terminal_reference_union
    )

    def drift_after_reference_union(*args, **kwargs):
        real_reference_union(*args, **kwargs)
        world.material.name = f"{world.material.name}-drift"
        db.flush()

    monkeypatch.setattr(
        finalize_service,
        "lock_opening_terminal_reference_union",
        drift_after_reference_union,
    )
    monkeypatch.setattr(
        finalize_service,
        "_lock_opening_serial_union_graph",
        lambda *_args, **_kwargs: pytest.fail(
            "serial union must not run after reference signature drift"
        ),
    )

    root = finalize_service._lock_opening_terminal_task_batch_root(
        db,
        task_ids=(facts.task.id,),
    )
    with pytest.raises(
        finalize_service.OpeningStocktakeFinalizeError
    ) as caught:
        finalize_service._lock_opening_terminal_task_batch_graph(
            db,
            root=root,
            supplied_user_ids=(world.user.id,),
        )
    assert caught.value.code == "opening_finalize_replay_evidence_invalid"


def test_query_reference_plan_explicitly_allows_undisposed_pending_observation(
    db: Session,
    world: SimpleNamespace,
) -> None:
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    observation = StocktakeCountObservation(
        id=uuid.uuid4(),
        task_id=facts.task.id,
        round_id=facts.round.id,
        scope_id=facts.scope.id,
        observation_no=1,
        owner_org_id=facts.scope.owner_org_id,
        location_id=facts.scope.location_id,
        custodian_person_id_snapshot=facts.scope.custodian_person_id_snapshot,
        material_id=None,
        material_identifier_raw=f"UNRESOLVED-{uuid.uuid4().hex}",
        material_identifier_type="unknown",
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
        lot_no_raw=None,
        serial_id=None,
        serial_no_raw=None,
        serial_identifier_type=None,
        counted_qty=Decimal("1.000"),
        verification_status="pending_verification",
        count_method="manual",
        reason_code="awaiting_disposition",
        remark="正式查询允许处置前证据签名",
        counted_by_user_id=world.user.id,
        counted_at=NOW,
        dimension_sha256="d" * 64,
        request_sha256="e" * 64,
        idempotency_key_hash="f" * 64,
        created_at=NOW,
    )
    db.add(observation)
    db.flush()

    with pytest.raises(finalize_service.OpeningStocktakeFinalizeError):
        finalize_service._opening_start_reference_coordinates(
            db,
            task=facts.task,
            round_id=facts.round.id,
        )

    plan = finalize_service._opening_start_reference_coordinates(
        db,
        task=facts.task,
        round_id=facts.round.id,
        require_complete_dispositions=False,
    )
    assert plan.require_complete_dispositions is False
    assert len(plan.observation_signatures) == 1
    assert plan.disposition_candidates == ()
    assert finalize_service._opening_start_reference_coordinates(
        db,
        task=facts.task,
        round_id=facts.round.id,
        require_complete_dispositions=plan.require_complete_dispositions,
    ) == plan


def test_posting_enters_reference_lock_graph_after_the_ledger_lock(
    db: Session,
    world: SimpleNamespace,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("0"),
    )
    db.commit()
    observed: list[object] = []

    def observe_sql(
        _connection,
        _cursor,
        statement,
        _parameters,
        _context,
        _executemany,
    ) -> None:
        if "inventory_ledger_heads" in statement:
            observed.append("ledger")

    def observe_reference_lock(
        _db: Session,
        account_ids,
        effective_at: datetime,
    ) -> None:
        observed.append(("reference", tuple(account_ids), effective_at))

    event.listen(db.get_bind(), "before_cursor_execute", observe_sql)
    monkeypatch.setattr(
        posting_service,
        "lock_inventory_reference_graph",
        observe_reference_lock,
    )
    try:
        result = post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account.id,
                        quantity=Decimal("1"),
                        external_boundary_code="external-source",
                    ),
                ),
            ),
        )
    finally:
        event.remove(db.get_bind(), "before_cursor_execute", observe_sql)

    reference_call = ("reference", (account.id,), NOW)
    assert result.replayed is False
    assert "ledger" in observed
    assert reference_call in observed
    assert observed.index("ledger") < observed.index(reference_call)


def test_ledger_head_proof_keeps_exact_transaction_object_alive(
    db: Session,
    world: SimpleNamespace,
) -> None:
    del world
    proof = posting_service._lock_inventory_ledger_head_for_atomic_batch(db)
    original_transaction = db.get_transaction()
    assert original_transaction is not None
    assert proof.session is db
    assert proof.transaction is original_transaction

    db.commit()
    db.scalar(select(InventoryLedgerHead).limit(1))
    assert db.get_transaction() is not proof.transaction
    with pytest.raises(InventoryPostingError) as error:
        posting_service._require_prelocked_inventory_ledger_head(db, proof)
    assert error.value.code == "inventory_batch_ledger_proof_invalid"


def test_generic_posting_rejects_opening_before_any_ledger_write(
    db: Session, world: SimpleNamespace
):
    blocked = command(
        "opening",
        (
            InventoryMovementCommand(
                from_account_id=None,
                to_account_id=uuid.uuid4(),
                quantity=Decimal("1"),
                external_boundary_code="reviewed-opening",
            ),
        ),
    )

    with pytest.raises(InventoryPostingError) as captured:
        post(db, world, blocked)

    assert captured.value.code == "inventory_opening_requires_approved_stocktake"
    assert db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor == 1
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == 0


def test_external_inbound_creates_zero_balance_then_posts(
    db: Session, world: SimpleNamespace
):
    account = make_account(
        db, organization=world.organization, material=world.material
    )
    db.commit()

    result = post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=account.id,
                    quantity=Decimal("4.125"),
                    external_boundary_code="reviewed-opening",
                ),
            ),
        ),
    )

    balance = db.get(StockBalance, account.id)
    assert balance.quantity == Decimal("4.125")
    assert balance.version == 1
    assert balance.ledger_cursor == result.ledger_cursor


def test_new_post_requires_exact_opening_establishment_and_failure_rolls_back(
    db: Session, world: SimpleNamespace
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
        established=False,
    )
    account_id = account.id
    db.commit()

    with pytest.raises(InventoryPostingError) as captured:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account_id,
                        quantity=Decimal("1"),
                        external_boundary_code="external-source",
                    ),
                ),
            ),
        )

    assert captured.value.code == "inventory_opening_not_established"
    db.rollback()
    assert db.get(StockBalance, account_id) is None
    assert db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor == 1
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == 0
    assert db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "inventory.transaction.posted"
        )
    ) == 0
    assert db.scalar(
        select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.event_type == "inventory.transaction.posted"
        )
    ) == 0


def test_location_establishment_for_another_asset_owner_is_not_reused(
    db: Session, world: SimpleNamespace
):
    established_account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    other_owner = make_organization(db, "其他资产所有组织")
    account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=other_owner.id,
        custodian_person_id=None,
        location_id=established_account.location_id,
        material_id=world.material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
    )
    db.add(account)
    db.commit()

    with pytest.raises(InventoryPostingError) as captured:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account.id,
                        quantity=Decimal("1"),
                        external_boundary_code="external-source",
                    ),
                ),
            ),
        )

    assert captured.value.code == "inventory_opening_not_established"


@pytest.mark.parametrize("broken_fact", ["headquarters_review", "future_cursor"])
def test_new_post_rejects_forged_or_incomplete_opening_evidence(
    db: Session,
    world: SimpleNamespace,
    broken_fact: str,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    if broken_fact == "headquarters_review":
        facts.headquarters_review.decision = "recount"
    else:
        facts.establishment.established_ledger_cursor = 1
    db.commit()

    with pytest.raises(InventoryPostingError) as captured:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account.id,
                        quantity=Decimal("1"),
                        external_boundary_code="external-source",
                    ),
                ),
            ),
        )

    assert captured.value.code == "inventory_opening_establishment_invalid"
    db.rollback()
    assert db.get(StockBalance, account.id) is None
    assert db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor == 1
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == 0


def test_reviewed_pending_control_establishment_allows_normal_post(
    db: Session, world: SimpleNamespace
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    add_reviewed_pending_control_difference(db, facts)
    db.commit()

    result = post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=account.id,
                    quantity=Decimal("1"),
                    external_boundary_code="external-source",
                ),
            ),
        ),
    )

    assert result.replayed is False
    assert db.get(StockBalance, account.id).quantity == Decimal("1.000")
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == 1


def test_pending_control_establishment_requires_both_review_item_decisions(
    db: Session, world: SimpleNamespace
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    add_reviewed_pending_control_difference(
        db,
        facts,
        include_headquarters_item=False,
    )
    db.commit()

    with pytest.raises(InventoryPostingError) as captured:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account.id,
                        quantity=Decimal("1"),
                        external_boundary_code="external-source",
                    ),
                ),
            ),
        )

    assert captured.value.code == "inventory_opening_establishment_invalid"
    db.rollback()
    assert db.get(StockBalance, account.id) is None
    assert db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor == 1
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == 0


def test_new_post_rejects_effective_time_before_opening_cutoff(
    db: Session, world: SimpleNamespace
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    backfill_at = posting_service._persisted_timestamp_utc(
        facts.task.cutoff_at
    ) - timedelta(microseconds=1)
    db.commit()

    with pytest.raises(InventoryPostingError) as captured:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account.id,
                        quantity=Decimal("1"),
                        external_boundary_code="external-source",
                    ),
                ),
                effective_at=backfill_at,
            ),
        )

    assert captured.value.code == "inventory_effective_at_before_opening_cutoff"
    db.rollback()
    assert db.get(StockBalance, account.id) is None
    assert db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor == 1
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == 0


def test_canonical_empty_opening_scope_and_snapshot_manifests_are_explicit():
    region_id = uuid.uuid4()
    task = SimpleNamespace(region_org_id=region_id, cutoff_ledger_cursor=0)

    assert posting_service.canonical_opening_scope_manifest_sha256(
        task, (), {}
    ) == posting_service.canonical_opening_manifest_sha256(
        {
            "region_org_id": str(region_id),
            "schema": "cloud_oam.opening_stocktake.scope_manifest.v1",
            "scopes": [],
        }
    )
    assert posting_service.canonical_opening_snapshot_manifest_sha256(
        task, (), ()
    ) == posting_service.canonical_opening_manifest_sha256(
        {
            "cutoff_ledger_cursor": 0,
            "schema": "cloud_oam.opening_stocktake.snapshot_manifest.v1",
            "scopes": [],
        }
    )


@pytest.mark.parametrize("missing_fact", ["snapshot", "count", "extra_count"])
def test_opening_guard_rejects_missing_or_extra_physical_evidence_even_if_rehashed(
    db: Session,
    world: SimpleNamespace,
    missing_fact: str,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    snapshots: tuple[StocktakeSnapshotLine, ...] = (facts.snapshot,)
    counts: tuple[StocktakeCountLine, ...] = (facts.count_line,)
    if missing_fact == "snapshot":
        db.delete(facts.snapshot)
        snapshots = ()
        facts.task.snapshot_manifest_sha256 = (
            posting_service.canonical_opening_snapshot_manifest_sha256(
                facts.task,
                (facts.scope,),
                snapshots,
            )
        )
        facts.establishment.snapshot_manifest_sha256 = (
            facts.task.snapshot_manifest_sha256
        )
    elif missing_fact == "count":
        db.delete(facts.count_line)
        counts = ()
    else:
        extra_material = make_material(
            db,
            world.source,
            tracking_mode="none",
            quantity_scale=3,
            allow_fraction=True,
        )
        extra_account = StockAccount(
            id=uuid.uuid4(),
            owner_org_id=account.owner_org_id,
            custodian_person_id=account.custodian_person_id,
            location_id=account.location_id,
            material_id=extra_material.id,
            condition_code="new",
            availability_bucket="available",
            lot_id=None,
            created_at=facts.task.cutoff_at + timedelta(microseconds=1),
            updated_at=facts.task.cutoff_at + timedelta(microseconds=1),
        )
        db.add(extra_account)
        db.flush()
        extra_count = StocktakeCountLine(
            id=uuid.uuid4(),
            task_id=facts.task.id,
            round_id=facts.round.id,
            scope_id=facts.scope.id,
            stock_account_id=extra_account.id,
            counted_qty=Decimal("0.000"),
            count_method="manual",
            reason_code=None,
            remark="伪造的范围外计数行",
            counted_by_user_id=facts.scope.assignee_user_id,
            counted_at=facts.count_line.counted_at,
            created_at=facts.count_line.created_at,
            updated_at=facts.round.submitted_at,
        )
        db.add(extra_count)
        counts = (facts.count_line, extra_count)
    facts.round.count_manifest_sha256 = (
        posting_service.canonical_opening_count_manifest_sha256(
            facts.task,
            facts.round,
            counts,
            (),
        )
    )
    facts.establishment.count_manifest_sha256 = facts.round.count_manifest_sha256
    db.flush()

    assert_opening_guard_rejects(db, world, account)


@pytest.mark.parametrize(
    "manifest_name",
    ["scope", "snapshot", "count", "control", "decision"],
)
def test_opening_guard_recomputes_every_manifest(
    db: Session,
    world: SimpleNamespace,
    manifest_name: str,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    forged = "f" * 64
    if manifest_name == "scope":
        facts.task.scope_manifest_sha256 = forged
        facts.establishment.scope_manifest_sha256 = forged
        facts.round.count_manifest_sha256 = (
            posting_service.canonical_opening_count_manifest_sha256(
                facts.task, facts.round, (facts.count_line,), ()
            )
        )
        facts.establishment.count_manifest_sha256 = (
            facts.round.count_manifest_sha256
        )
    elif manifest_name == "snapshot":
        facts.task.snapshot_manifest_sha256 = forged
        facts.establishment.snapshot_manifest_sha256 = forged
        facts.round.count_manifest_sha256 = (
            posting_service.canonical_opening_count_manifest_sha256(
                facts.task, facts.round, (facts.count_line,), ()
            )
        )
        facts.establishment.count_manifest_sha256 = (
            facts.round.count_manifest_sha256
        )
    elif manifest_name == "count":
        facts.round.count_manifest_sha256 = forged
        facts.establishment.count_manifest_sha256 = forged
    elif manifest_name == "control":
        facts.task.control_manifest_sha256 = forged
        facts.sync_run.manifest_sha256 = forged
        facts.establishment.control_manifest_sha256 = forged
        facts.round.count_manifest_sha256 = (
            posting_service.canonical_opening_count_manifest_sha256(
                facts.task, facts.round, (facts.count_line,), ()
            )
        )
        facts.establishment.count_manifest_sha256 = (
            facts.round.count_manifest_sha256
        )
    else:
        facts.regional_review.decision_manifest_sha256 = forged
        facts.headquarters_review.decision_manifest_sha256 = forged

    assert_opening_guard_rejects(db, world, account)


@pytest.mark.parametrize(
    "forgery",
    [
        "regional_scope",
        "regional_after_review",
        "headquarters_scope",
        "reviewer_person",
        "review_order",
        "non_admin_finalizer",
    ],
)
def test_opening_guard_rejects_review_role_time_and_identity_forgery(
    db: Session,
    world: SimpleNamespace,
    forgery: str,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    if forgery == "regional_scope":
        facts.regional_assignment.scope_id = str(uuid.uuid4())
    elif forgery == "regional_after_review":
        facts.regional_assignment.valid_from = (
            facts.regional_review.reviewed_at + timedelta(microseconds=1)
        )
    elif forgery == "headquarters_scope":
        world.headquarters_assignment.scope_type = "organization"
        world.headquarters_assignment.scope_id = str(world.organization.id)
    elif forgery == "reviewer_person":
        facts.regional_review.reviewer_person_id = (
            world.headquarters_reviewer_person.id
        )
    elif forgery == "review_order":
        facts.headquarters_review.reviewed_at = (
            facts.regional_review.reviewed_at - timedelta(microseconds=1)
        )
    else:
        facts.posting.posted_by_user_id = world.user.id
        facts.establishment.established_by_user_id = world.user.id

    assert_opening_guard_rejects(db, world, account)


@pytest.mark.parametrize("flag_value", [True, False])
def test_pending_control_flag_must_equal_the_actual_control_difference_set(
    db: Session,
    world: SimpleNamespace,
    flag_value: bool,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    if flag_value is False:
        add_reviewed_pending_control_difference(db, facts)
    facts.establishment.has_pending_control_difference = flag_value

    assert_opening_guard_rejects(db, world, account)


def test_unresolved_control_difference_uses_control_minus_physical_direction(
    db: Session,
    world: SimpleNamespace,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    difference = add_reviewed_pending_control_difference(db, facts)

    assert difference.book_qty == Decimal("1.000")
    assert difference.counted_qty == Decimal("0.000")
    assert difference.difference_qty == Decimal("-1.000")
    assert difference.affected_qty == Decimal("1.000")
    db.commit()
    result = post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=account.id,
                    quantity=Decimal("1"),
                    external_boundary_code="external-source",
                ),
            ),
        ),
    )
    assert result.replayed is False


def test_released_hard_freeze_blocks_backfill_inside_its_historical_window(
    db: Session,
    world: SimpleNamespace,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    backfill_at = posting_service._persisted_timestamp_utc(
        facts.task.cutoff_at
    ) + timedelta(minutes=1)
    db.commit()

    with pytest.raises(InventoryPostingError) as captured:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account.id,
                        quantity=Decimal("1"),
                        external_boundary_code="external-source",
                    ),
                ),
                effective_at=backfill_at,
            ),
        )

    assert captured.value.code == "inventory_scope_hard_frozen"


def test_resolved_control_total_mismatch_cannot_be_omitted_from_review(
    db: Session,
    world: SimpleNamespace,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    difference = add_reviewed_pending_control_difference(db, facts)
    review_items = db.scalars(
        select(StocktakeReviewItem).where(
            StocktakeReviewItem.difference_id == difference.id
        )
    ).all()
    for item in review_items:
        db.delete(item)
    db.flush()
    db.delete(difference)
    db.flush()

    line = facts.control_line
    line.material_id = account.material_id
    line.condition_code = account.condition_code
    line.control_qty = Decimal("100.000")
    line.mapping_status = "resolved"
    line.mapping_note = ""
    payload = posting_service.opening_control_projection_payload(
        external_business_key=line.external_business_key,
        region_org_id=facts.task.region_org_id,
        material_id=line.material_id,
        condition_code=line.condition_code,
        control_qty=line.control_qty,
        mapping_status=line.mapping_status,
        mapping_note=line.mapping_note,
    )
    payload_sha256 = posting_service.canonical_opening_manifest_sha256(payload)
    line.payload_sha256 = payload_sha256
    facts.external_version.payload_jsonb = payload
    facts.external_version.payload_sha256 = payload_sha256
    facts.inbox_event.payload_jsonb = payload
    facts.inbox_event.payload_sha256 = payload_sha256
    facts.sync_batch.body_sha256 = (
        posting_service.opening_control_batch_body_sha256(
            sequence=facts.sync_batch.sequence,
            events=(
                {
                    "event_sort_key": str(facts.inbox_event.id),
                    "external_event_id": facts.inbox_event.external_event_id,
                    "external_id": facts.inbox_event.external_id,
                    "payload_sha256": payload_sha256,
                    "source_updated_at": posting_service._canonical_datetime(
                        facts.inbox_event.source_updated_at
                    ),
                    "source_version": facts.inbox_event.source_version,
                },
            ),
        )
    )
    facts.task.control_manifest_sha256 = (
        posting_service.canonical_opening_control_manifest_sha256(
            facts.task, facts.sync_run, (line,)
        )
    )
    facts.sync_run.manifest_sha256 = facts.task.control_manifest_sha256
    facts.establishment.control_manifest_sha256 = (
        facts.task.control_manifest_sha256
    )
    facts.round.count_manifest_sha256 = (
        posting_service.canonical_opening_count_manifest_sha256(
            facts.task, facts.round, (facts.count_line,), ()
        )
    )
    facts.establishment.count_manifest_sha256 = facts.round.count_manifest_sha256
    empty_decision_manifest = (
        posting_service.canonical_opening_decision_manifest_sha256(
            task_id=facts.task.id,
            round_id=facts.round.id,
            differences=(),
            decisions={},
        )
    )
    facts.regional_review.decision_manifest_sha256 = empty_decision_manifest
    facts.headquarters_review.decision_manifest_sha256 = empty_decision_manifest
    facts.establishment.has_pending_control_difference = False
    db.flush()

    assert_opening_guard_rejects(db, world, account)


def test_historical_oam_version_source_disable_and_later_delete_do_not_break_opening(
    db: Session,
    world: SimpleNamespace,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    add_reviewed_pending_control_difference(db, facts)
    later_at = facts.establishment.established_at + timedelta(minutes=1)
    facts.external_version.is_current = False
    facts.external_version.valid_to = later_at
    later_version = ExternalObjectVersion(
        id=uuid.uuid4(),
        external_object_id=facts.external_object.id,
        source_version=f"later-{uuid.uuid4().hex}",
        source_updated_at=later_at,
        valid_from=later_at,
        valid_to=None,
        payload_jsonb=facts.external_version.payload_jsonb,
        payload_sha256=facts.external_version.payload_sha256,
        is_current=True,
        created_at=later_at,
    )
    db.add(later_version)
    db.flush()
    facts.external_object.current_version_id = later_version.id
    facts.external_object.deleted_at = later_at + timedelta(minutes=1)
    world.source.mode = "mirror_only"
    world.source.enabled = False
    db.commit()

    result = post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=account.id,
                    quantity=Decimal("1"),
                    external_boundary_code="external-source",
                ),
            ),
        ),
    )

    assert result.replayed is False


def test_non_oam_control_source_identity_is_rejected(
    db: Session,
    world: SimpleNamespace,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    world.source.code = f"NOT-OAM-{uuid.uuid4().hex[:8]}"

    assert_opening_guard_rejects(db, world, account)


def test_positive_opening_full_set_allows_later_normal_post(
    db: Session,
    world: SimpleNamespace,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    make_positive_opening_facts(db, facts, quantity=Decimal("2.000"))
    db.commit()

    result = post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=account.id,
                    quantity=Decimal("1"),
                    external_boundary_code="external-source",
                ),
            ),
        ),
    )

    assert result.ledger_cursor == 2
    assert db.get(StockBalance, account.id).quantity == Decimal("3.000")


def test_posted_opening_replay_validator_recomputes_the_complete_graph(
    db: Session,
    world: SimpleNamespace,
) -> None:
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    db.commit()

    posting_service.validate_opening_task_evidence_for_replay(
        db,
        task_id=facts.task.id,
    )

    facts.establishment.count_manifest_sha256 = "f" * 64
    db.flush()
    with pytest.raises(InventoryPostingError) as captured:
        posting_service.validate_opening_task_evidence_for_replay(
            db,
            task_id=facts.task.id,
        )
    assert captured.value.code == "inventory_opening_establishment_invalid"


def test_single_round_opening_without_seal_chain_fails_closed(
    db: Session,
    world: SimpleNamespace,
) -> None:
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    db.delete(facts.completion)
    db.delete(facts.submission)
    db.delete(facts.scope_completion)
    db.flush()

    with pytest.raises(InventoryPostingError) as captured:
        posting_service.validate_opening_task_evidence_for_replay(
            db,
            task_id=facts.task.id,
        )

    assert captured.value.code == "inventory_opening_establishment_invalid"


def _service_built_submitted_recount_chain(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> SimpleNamespace:
    """Build round 1 -> round 2 through the production count/review services."""

    import app.formal_services.opening_observation_disposition as disposition_service
    import app.formal_services.opening_stocktake as opening_service
    import app.formal_services.opening_stocktake_count as count_service
    import app.formal_services.opening_stocktake_recount as recount_service
    import app.formal_services.opening_stocktake_review as review_service
    import test_opening_stocktake_review_service as review_fixtures
    from app.formal_services.opening_stocktake_recount import (
        OpenOpeningStocktakeRecountCommand,
        OpeningStocktakeRecountScopeAssignmentInput,
        open_opening_stocktake_recount,
    )
    from app.formal_services.opening_stocktake_review import (
        submit_opening_region_review,
    )

    service_now = review_fixtures.NOW
    monkeypatch.setattr(opening_service, "_database_now", lambda _db: service_now)
    def count_clock(_db):
        current_round_no = db.scalar(
            select(func.max(FormalStocktakeTask.current_round_no))
        ) or 1
        return service_now + timedelta(
            hours=1 if current_round_no == 1 else 4
        )

    monkeypatch.setattr(count_service, "_database_now", count_clock)
    monkeypatch.setattr(
        disposition_service,
        "_database_now",
        lambda _db: service_now + timedelta(hours=1, minutes=30),
    )
    review_tick = {"value": 0}

    def review_clock(_db):
        current_round_no = db.scalar(
            select(func.max(FormalStocktakeTask.current_round_no))
        ) or 1
        base_hours = 2 if current_round_no == 1 else 5
        value = service_now + timedelta(
            hours=base_hours,
            microseconds=review_tick["value"],
        )
        review_tick["value"] += 1
        return value

    monkeypatch.setattr(review_service, "_database_now", review_clock)
    def recount_clock(_db):
        has_terminal_task = bool(
            db.scalar(
                select(FormalStocktakeTask.id)
                .where(FormalStocktakeTask.status.in_(("posted", "closed")))
                .limit(1)
            )
        )
        return service_now + timedelta(hours=7 if has_terminal_task else 3)

    monkeypatch.setattr(recount_service, "_database_now", recount_clock)
    service_world = review_fixtures.world.__wrapped__(db)
    # Regional warehouse responsibility belongs to the location/custody
    # history.  Its pooled stock account remains valid without a personal
    # custodian dimension; finalization must not apply the personal-warehouse
    # account rule to this shape.
    assert service_world.account.custodian_person_id is None
    service_world.location.custodian_person_id = service_world.manager_x.person.id
    db.add(
        CustodyAssignment(
            id=uuid.uuid4(),
            location_id=service_world.location.id,
            custodian_person_id=service_world.manager_x.person.id,
            valid_from=service_now - timedelta(days=1),
            valid_to=None,
            handover_case_id=None,
            created_at=service_now - timedelta(days=1),
            updated_at=service_now - timedelta(days=1),
        )
    )
    db.flush()
    prepared = review_fixtures._prepare_submitted(service_world)
    assert service_world.location.location_type == "region"
    assert (
        prepared.scope.custodian_person_id_snapshot
        == service_world.manager_x.person.id
    )
    trigger_region = submit_opening_region_review(
        db,
        actor=service_world.principals["manager_x"],
        command=review_fixtures._review_command(
            db,
            prepared,
            decision="recount",
            comment="区域明确要求复盘",
        ),
        idempotency_key=f"posting-recount-review-{uuid.uuid4().hex}",
        request_id="posting-recount-review-request",
    )
    assert trigger_region.resulting_task_status == "recount_required"
    source_round = db.get(StocktakeRound, prepared.round.id)
    task = db.get(FormalStocktakeTask, prepared.task.id)
    scope = db.get(FormalStocktakeScope, prepared.scope.id)
    assert task is not None and source_round is not None and scope is not None
    recount_command = OpenOpeningStocktakeRecountCommand(
        task_id=task.id,
        source_round_id=source_round.id,
        assignments=(
            OpeningStocktakeRecountScopeAssignmentInput(
                scope_id=scope.id,
                assignee_user_id=service_world.admin.user.id,
            ),
        ),
        reason="来源轮次复核结论要求重新实盘",
    )
    recount_key = f"posting-recount-open-{uuid.uuid4().hex}"
    opened = open_opening_stocktake_recount(
        db,
        actor=service_world.principals["manager_x"],
        command=recount_command,
        idempotency_key=recount_key,
        request_id="posting-recount-open-request",
    )
    final_round = db.get(StocktakeRound, opened.next_round_id)
    assert final_round is not None
    from app.formal_services.opening_stocktake_count import (
        SubmitOpeningStocktakeScopeCountCommand,
        submit_opening_stocktake_scope_count,
    )
    from app.formal_services.opening_stocktake_review import (
        submit_opening_headquarters_review,
    )

    counted = submit_opening_stocktake_scope_count(
        db,
        actor=service_world.principals["admin"],
        command=SubmitOpeningStocktakeScopeCountCommand(
            task_id=task.id,
            round_id=final_round.id,
            scope_id=scope.id,
            physical_observations=(),
            zero_confirmed=False,
        ),
        idempotency_key=f"posting-recount-count-{uuid.uuid4().hex}",
        request_id="posting-recount-count-request",
    )
    assert counted.round_sealed is True and counted.round_status == "submitted"
    task = db.get(FormalStocktakeTask, task.id)
    final_round = db.get(StocktakeRound, final_round.id)
    assert task is not None and final_round is not None
    final_prepared = SimpleNamespace(task=task, round=final_round, scope=scope)
    final_region = submit_opening_region_review(
        db,
        actor=service_world.principals["manager_x"],
        command=review_fixtures._review_command(
            db,
            final_prepared,
            decision="approve",
            comment="区域复核第二轮通过",
        ),
        idempotency_key=f"posting-recount-final-region-{uuid.uuid4().hex}",
        request_id="posting-recount-final-region-request",
    )
    final_headquarters = submit_opening_headquarters_review(
        db,
        actor=service_world.principals["admin"],
        command=review_fixtures._review_command(
            db,
            final_prepared,
            decision="approve",
            comment="总部复核第二轮通过",
        ),
        idempotency_key=f"posting-recount-final-headquarters-{uuid.uuid4().hex}",
        request_id="posting-recount-final-headquarters-request",
    )
    service_world.account.created_at = task.cutoff_at
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
        select(SyncInboxEvent)
        .join(SyncBatch, SyncBatch.id == SyncInboxEvent.batch_id)
        .where(SyncBatch.run_id == sync_run.id)
    ).all()
    for inbox_event in inbox_events:
        inbox_event.created_at = inbox_event.source_updated_at
    posted_at = service_now + timedelta(hours=6)
    posting = StocktakePosting(
        id=uuid.uuid4(),
        task_id=task.id,
        round_id=final_round.id,
        posting_kind="opening",
        inventory_transaction_id=None,
        total_quantity=Decimal("0.000"),
        idempotency_key_hash=hashlib.sha256(
            f"posting-recount-final:{task.id}".encode()
        ).hexdigest(),
        request_hash=hashlib.sha256(
            f"posting-recount-final-request:{task.id}".encode()
        ).hexdigest(),
        posted_by_user_id=service_world.admin.user.id,
        posted_at=posted_at,
        created_at=posted_at,
    )
    db.add(posting)
    db.flush()
    freeze = db.scalar(
        select(InventoryFreeze).where(
            InventoryFreeze.task_id == task.id,
            InventoryFreeze.stocktake_scope_id == scope.id,
        )
    )
    assert freeze is not None
    freeze.status = "released"
    freeze.valid_to = posted_at
    freeze.released_by_user_id = service_world.admin.user.id
    freeze.release_reason = "第二轮区域与总部通过后期初过账"
    freeze.updated_at = posted_at
    freeze.version += 1
    pending_control = bool(
        db.scalar(
            select(StocktakeDifference.id)
            .where(
                StocktakeDifference.task_id == task.id,
                StocktakeDifference.round_id == final_round.id,
                StocktakeDifference.difference_type == "control_unassigned",
            )
            .limit(1)
        )
    )
    establishment = InventoryOpeningEstablishment(
        id=uuid.uuid4(),
        task_id=task.id,
        scope_id=scope.id,
        owner_org_id=scope.owner_org_id,
        location_id=scope.location_id,
        round_id=final_round.id,
        posting_id=posting.id,
        regional_review_id=final_region.review_id,
        headquarters_review_id=final_headquarters.review_id,
        cutoff_ledger_cursor=task.cutoff_ledger_cursor,
        cutoff_at=task.cutoff_at,
        established_ledger_cursor=task.cutoff_ledger_cursor,
        scope_manifest_sha256=task.scope_manifest_sha256,
        snapshot_manifest_sha256=task.snapshot_manifest_sha256,
        count_manifest_sha256=final_round.count_manifest_sha256,
        control_manifest_sha256=task.control_manifest_sha256,
        has_pending_control_difference=pending_control,
        established_by_user_id=service_world.admin.user.id,
        established_at=posted_at,
        created_at=posted_at,
    )
    db.add(establishment)
    task.status = "posted"
    task.posted_at = posted_at
    task.updated_at = posted_at
    task.version += 1
    db.commit()
    db.expire_all()
    posting_service.validate_opening_task_evidence_for_replay(
        db,
        task_id=task.id,
    )
    return SimpleNamespace(
        world=service_world,
        task=db.get(FormalStocktakeTask, task.id),
        source_round=db.get(StocktakeRound, source_round.id),
        final_round=db.get(StocktakeRound, opened.next_round_id),
        scope=db.get(FormalStocktakeScope, scope.id),
        posting=db.get(StocktakePosting, posting.id),
        establishment=db.get(InventoryOpeningEstablishment, establishment.id),
        recount_command=recount_command,
        recount_key=recount_key,
    )


def test_append_only_recount_chain_uses_current_assignment_and_reproves_old_count(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = _service_built_submitted_recount_chain(db, monkeypatch)
    scopes = db.scalars(
        select(FormalStocktakeScope).where(
            FormalStocktakeScope.task_id == facts.task.id
        )
    ).all()
    freezes = db.scalars(
        select(InventoryFreeze).where(InventoryFreeze.task_id == facts.task.id)
    ).all()
    rounds = db.scalars(
        select(StocktakeRound)
        .where(StocktakeRound.task_id == facts.task.id)
        .order_by(StocktakeRound.round_no)
    ).all()

    final_round, assignment_map = posting_service._validate_opening_recount_chain(
        db,
        task=facts.task,
        scopes=scopes,
        freezes=freezes,
        rounds=rounds,
    )

    assert final_round.id == facts.final_round.id
    assert assignment_map == {facts.scope.id: facts.world.admin.user.id}
    assert facts.source_round.status == "submitted"
    final_count_lines = db.scalars(
        select(StocktakeCountLine).where(
            StocktakeCountLine.round_id == final_round.id
        )
    ).all()
    posting_service._validate_opening_count_evidence(
        db,
        task=facts.task,
        scopes=scopes,
        scope_by_id={row.id: row for row in scopes},
        round_row=final_round,
        snapshot_lines=db.scalars(
            select(StocktakeSnapshotLine).where(
                StocktakeSnapshotLine.task_id == facts.task.id
            )
        ).all(),
        count_lines=final_count_lines,
        count_serials=db.scalars(
            select(StocktakeCountSerial).where(
                StocktakeCountSerial.round_id == final_round.id
            )
        ).all(),
        expected_assignee_by_scope=assignment_map,
    )

    personal_parent = StockLocation(
        id=uuid.uuid4(),
        code=f"RECOUNT-PERSONAL-PARENT-{uuid.uuid4().hex[:12]}",
        name="复盘个人仓负例父级区域仓",
        location_type="region",
        owner_org_id=facts.world.region_x.id,
        parent_id=None,
        custodian_person_id=None,
        status="active",
    )
    db.add(personal_parent)
    db.flush()
    facts.world.location.parent_id = personal_parent.id
    facts.world.location.location_type = "personal"
    db.flush()
    with pytest.raises(InventoryPostingError) as personal_mismatch:
        posting_service._validate_opening_count_evidence(
            db,
            task=facts.task,
            scopes=scopes,
            scope_by_id={row.id: row for row in scopes},
            round_row=final_round,
            snapshot_lines=db.scalars(
                select(StocktakeSnapshotLine).where(
                    StocktakeSnapshotLine.task_id == facts.task.id
                )
            ).all(),
            count_lines=final_count_lines,
            count_serials=db.scalars(
                select(StocktakeCountSerial).where(
                    StocktakeCountSerial.round_id == final_round.id
                )
            ).all(),
            expected_assignee_by_scope=assignment_map,
        )
    assert (
        personal_mismatch.value.code
        == "inventory_opening_establishment_invalid"
    )
    facts.world.location.location_type = "region"
    facts.world.location.parent_id = None
    db.flush()

    source_count = db.scalar(
        select(StocktakeCountLine).where(
            StocktakeCountLine.round_id == facts.source_round.id
        )
    )
    assert source_count is not None
    source_count.remark = "篡改历史来源轮次实盘证据"
    db.flush()
    with pytest.raises(InventoryPostingError) as captured:
        posting_service._validate_opening_recount_chain(
            db,
            task=facts.task,
            scopes=scopes,
            freezes=freezes,
            rounds=rounds,
        )
    assert captured.value.code == "inventory_opening_establishment_invalid"


def test_posted_recount_replay_rejects_tampered_historical_assignment(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = _service_built_submitted_recount_chain(db, monkeypatch)
    assert facts.task.status == "posted"
    assert facts.final_round.recount_case_id is not None
    assignment = db.scalar(
        select(StocktakeRecountScopeAssignment).where(
            StocktakeRecountScopeAssignment.recount_case_id
            == facts.final_round.recount_case_id,
            StocktakeRecountScopeAssignment.scope_id == facts.scope.id,
        )
    )
    assert assignment is not None
    assignment.assignment_sha256 = "f" * 64
    db.flush()

    with pytest.raises(InventoryPostingError) as captured:
        posting_service.validate_opening_task_evidence_for_replay(
            db,
            task_id=facts.task.id,
        )

    assert captured.value.code == "inventory_opening_establishment_invalid"


@pytest.mark.parametrize("terminal_status", ["posted", "closed"])
def test_first_recount_opener_replay_reproves_terminal_evidence(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
    terminal_status: str,
) -> None:
    from app.formal_services.opening_stocktake_recount import (
        OpeningStocktakeRecountError,
        open_opening_stocktake_recount,
    )

    facts = _service_built_submitted_recount_chain(db, monkeypatch)
    if terminal_status == "closed":
        facts.task.status = "closed"
        facts.task.closed_at = facts.task.posted_at + timedelta(minutes=1)
        facts.task.updated_at = facts.task.closed_at
        facts.task.version += 1
        db.commit()
    before = {
        model: db.scalar(select(func.count()).select_from(model)) or 0
        for model in (
            StocktakeRecountScopeAssignment,
            StocktakeRound,
            StateTransitionEvent,
            OutboxEvent,
            AuditEvent,
        )
    }
    replay = open_opening_stocktake_recount(
        db,
        actor=facts.world.principals["manager_x"],
        command=facts.recount_command,
        idempotency_key=facts.recount_key,
        request_id="posting-recount-open-posted-retry",
    )
    assert replay.replayed is True
    assert before == {
        model: db.scalar(select(func.count()).select_from(model)) or 0
        for model in before
    }

    facts.establishment.count_manifest_sha256 = "f" * 64
    db.flush()
    with pytest.raises(OpeningStocktakeRecountError) as invalid:
        open_opening_stocktake_recount(
            db,
            actor=facts.world.principals["manager_x"],
            command=facts.recount_command,
            idempotency_key=facts.recount_key,
            request_id="posting-recount-open-posted-tampered-retry",
        )
    assert invalid.value.code == "opening_recount_idempotency_record_invalid"


def test_terminal_recount_replay_uses_ledger_first_prelocked_pure_order(
    db: Session,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import app.formal_services.opening_stocktake_recount as recount_service

    facts = _service_built_submitted_recount_chain(db, monkeypatch)
    events: list[str] = []
    real_advisory = recount_service._take_advisory_locks
    real_root = recount_service._lock_terminal_replay_root
    real_principal = recount_service._lock_terminal_replay_principal_graph
    real_graph = recount_service._lock_terminal_replay_graph
    real_audit = recount_service._lock_audit_chain_head_with_proof
    real_opening_pure = recount_service._validate_terminal_replay_graph
    real_recount_pure = recount_service._validate_replay

    def record_advisory(*args, **kwargs):
        events.append("advisory")
        return real_advisory(*args, **kwargs)

    def record_root(*args, **kwargs):
        events.append("ledger-task")
        return real_root(*args, **kwargs)

    def record_principal(*args, **kwargs):
        events.append("principal")
        return real_principal(*args, **kwargs)

    def record_graph(*args, **kwargs):
        events.append("opening")
        return real_graph(*args, **kwargs)

    def record_audit(*args, **kwargs):
        events.append("audit")
        return real_audit(*args, **kwargs)

    def record_opening_pure(*args, **kwargs):
        events.append("opening-pure")
        return real_opening_pure(*args, **kwargs)

    def record_recount_pure(*args, **kwargs):
        events.append("recount-pure")
        return real_recount_pure(*args, **kwargs)

    def unexpected_reentry(*_args, **_kwargs):
        pytest.fail("terminal replay must not re-enter a task/reference owner")

    monkeypatch.setattr(recount_service, "_take_advisory_locks", record_advisory)
    monkeypatch.setattr(recount_service, "_lock_terminal_replay_root", record_root)
    monkeypatch.setattr(
        recount_service,
        "_lock_terminal_replay_principal_graph",
        record_principal,
    )
    monkeypatch.setattr(recount_service, "_lock_terminal_replay_graph", record_graph)
    monkeypatch.setattr(
        recount_service,
        "_lock_audit_chain_head_with_proof",
        record_audit,
    )
    monkeypatch.setattr(
        recount_service,
        "_validate_terminal_replay_graph",
        record_opening_pure,
    )
    monkeypatch.setattr(recount_service, "_validate_replay", record_recount_pure)
    monkeypatch.setattr(
        recount_service,
        "lock_opening_stocktake_task_evidence",
        unexpected_reentry,
    )
    monkeypatch.setattr(
        recount_service,
        "_lock_recount_reference_graph",
        unexpected_reentry,
    )
    monkeypatch.setattr(
        recount_service,
        "lock_formal_principal_graph",
        unexpected_reentry,
    )

    replay = recount_service.open_opening_stocktake_recount(
        db,
        actor=facts.world.principals["manager_x"],
        command=facts.recount_command,
        idempotency_key=facts.recount_key,
        request_id="posting-recount-terminal-lock-order-retry",
    )

    assert replay.replayed is True
    assert events[:7] == [
        "advisory",
        "ledger-task",
        "principal",
        "opening",
        "audit",
        "opening-pure",
        "recount-pure",
    ]
    assert not {"ledger-task", "principal", "opening"}.intersection(
        events[events.index("audit") + 1 :]
    )


def test_posted_recount_replay_rejects_tampered_count_outbox(
    db: Session, monkeypatch: pytest.MonkeyPatch
) -> None:
    facts = _service_built_submitted_recount_chain(db, monkeypatch)
    rows = db.scalars(
        select(OutboxEvent).where(
            OutboxEvent.aggregate_type == "stocktake_scope",
            OutboxEvent.aggregate_id == str(facts.scope.id),
            OutboxEvent.event_type == "stocktake.opening.scope_count_completed",
        )
    ).all()
    matching = [
        row
        for row in rows
        if row.payload_jsonb.get("round_id") == str(facts.final_round.id)
    ]
    assert len(matching) == 1
    matching[0].available_at = matching[0].available_at + timedelta(seconds=1)
    db.flush()

    with pytest.raises(InventoryPostingError) as invalid:
        posting_service.validate_opening_task_evidence_for_replay(
            db,
            task_id=facts.task.id,
        )

    assert invalid.value.code == "inventory_opening_establishment_invalid"


def test_recount_technician_assignment_rejects_non_personal_location(
    db: Session, world: SimpleNamespace
) -> None:
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
        custodian=world.person,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    forged = SimpleNamespace(
        role_code="technician",
        scope_type="person",
        scope_id_snapshot=str(world.person.id),
        assignee_person_id=world.person.id,
    )

    assert not posting_service._opening_recount_assignment_scope_exact(
        db, forged, facts.scope
    )


@pytest.mark.parametrize(
    "posting_forgery",
    ["missing_item", "extra_item", "cross_location", "wrong_total", "late_item"],
)
def test_positive_opening_requires_exact_movement_and_posting_item_full_set(
    db: Session,
    world: SimpleNamespace,
    posting_forgery: str,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    positive = make_positive_opening_facts(
        db, facts, quantity=Decimal("2.000")
    )
    if posting_forgery == "missing_item":
        db.delete(positive.posting_item)
    elif posting_forgery == "extra_item":
        extra_movement = InventoryMovement(
            id=uuid.uuid4(),
            transaction_id=positive.transaction.id,
            line_no=2,
            from_account_id=None,
            to_account_id=account.id,
            external_boundary_code="approved-opening-stocktake",
            quantity=Decimal("1.000"),
            created_at=facts.posting.posted_at,
        )
        db.add(extra_movement)
        db.flush()
        db.add(
            StocktakePostingItem(
                posting_id=facts.posting.id,
                inventory_movement_id=extra_movement.id,
                task_id=facts.task.id,
                round_id=facts.round.id,
                count_line_id=facts.count_line.id,
                difference_id=None,
                quantity=Decimal("1.000"),
                created_at=facts.posting.posted_at,
            )
        )
    elif posting_forgery == "cross_location":
        other_account = make_account(
            db,
            organization=world.organization,
            material=world.material,
            established=False,
        )
        positive.movement.to_account_id = other_account.id
    elif posting_forgery == "wrong_total":
        facts.posting.total_quantity = Decimal("3.000")
    else:
        positive.posting_item.created_at = (
            facts.posting.posted_at + timedelta(microseconds=1)
        )

    assert_opening_guard_rejects(db, world, account)


def test_control_difference_can_never_be_used_as_opening_posting_item(
    db: Session,
    world: SimpleNamespace,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    control_difference = add_reviewed_pending_control_difference(db, facts)
    positive = make_positive_opening_facts(
        db, facts, quantity=Decimal("1.000")
    )
    positive.posting_item.count_line_id = None
    positive.posting_item.difference_id = control_difference.id

    assert_opening_guard_rejects(db, world, account)


def test_count_evidence_predating_the_round_is_rejected_even_if_rehashed(
    db: Session,
    world: SimpleNamespace,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    facts.count_line.counted_at = facts.round.started_at - timedelta(
        microseconds=1
    )
    facts.round.count_manifest_sha256 = (
        posting_service.canonical_opening_count_manifest_sha256(
            facts.task, facts.round, (facts.count_line,), ()
        )
    )
    facts.establishment.count_manifest_sha256 = facts.round.count_manifest_sha256

    assert_opening_guard_rejects(db, world, account)


def test_review_item_inserted_after_review_is_rejected_even_if_manifest_is_rehashed(
    db: Session,
    world: SimpleNamespace,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    difference = add_reviewed_pending_control_difference(db, facts)
    item = db.get(
        StocktakeReviewItem,
        (facts.regional_review.id, difference.id),
    )
    assert item is not None
    item.created_at = facts.regional_review.reviewed_at + timedelta(
        microseconds=1
    )

    assert_opening_guard_rejects(db, world, account)


def test_snapshot_inserted_after_establishment_is_rejected_with_all_hashes_rewritten(
    db: Session,
    world: SimpleNamespace,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    extra_material = make_material(
        db,
        world.source,
        tracking_mode="none",
        quantity_scale=3,
        allow_fraction=True,
    )
    extra_account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=account.owner_org_id,
        custodian_person_id=account.custodian_person_id,
        location_id=account.location_id,
        material_id=extra_material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
        created_at=facts.task.cutoff_at - timedelta(microseconds=1),
        updated_at=facts.task.cutoff_at - timedelta(microseconds=1),
    )
    db.add(extra_account)
    db.flush()
    late_snapshot = StocktakeSnapshotLine(
        id=uuid.uuid4(),
        task_id=facts.task.id,
        scope_id=facts.scope.id,
        stock_account_id=extra_account.id,
        book_qty=Decimal("0.000"),
        ledger_cursor=facts.task.cutoff_ledger_cursor,
        account_dimension_sha256=(
            posting_service.canonical_opening_account_dimension_sha256(
                extra_account
            )
        ),
        serial_snapshot_jsonb=[],
        serial_snapshot_sha256=(
            posting_service.canonical_opening_serial_snapshot_sha256(
                stock_account_id=extra_account.id,
                serials=(),
            )
        ),
        serial_count=0,
        created_at=facts.establishment.established_at + timedelta(
            microseconds=1
        ),
    )
    extra_count = StocktakeCountLine(
        id=uuid.uuid4(),
        task_id=facts.task.id,
        round_id=facts.round.id,
        scope_id=facts.scope.id,
        stock_account_id=extra_account.id,
        counted_qty=Decimal("0.000"),
        count_method="manual",
        reason_code=None,
        remark="事后伪造的零计数",
        counted_by_user_id=facts.scope.assignee_user_id,
        counted_at=facts.count_line.counted_at,
        created_at=facts.count_line.created_at,
        updated_at=facts.round.submitted_at,
    )
    db.add_all([late_snapshot, extra_count])
    db.flush()
    facts.task.snapshot_manifest_sha256 = (
        posting_service.canonical_opening_snapshot_manifest_sha256(
            facts.task,
            (facts.scope,),
            (facts.snapshot, late_snapshot),
        )
    )
    facts.establishment.snapshot_manifest_sha256 = (
        facts.task.snapshot_manifest_sha256
    )
    facts.round.count_manifest_sha256 = (
        posting_service.canonical_opening_count_manifest_sha256(
            facts.task,
            facts.round,
            (facts.count_line, extra_count),
            (),
        )
    )
    facts.establishment.count_manifest_sha256 = facts.round.count_manifest_sha256

    assert_opening_guard_rejects(db, world, account)


def test_control_line_without_immutable_external_version_is_rejected_after_rehash(
    db: Session,
    world: SimpleNamespace,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    add_reviewed_pending_control_difference(db, facts)
    facts.control_line.external_object_version_id = None
    facts.task.control_manifest_sha256 = (
        posting_service.canonical_opening_control_manifest_sha256(
            facts.task, facts.sync_run, (facts.control_line,)
        )
    )
    facts.sync_run.manifest_sha256 = facts.task.control_manifest_sha256
    facts.establishment.control_manifest_sha256 = (
        facts.task.control_manifest_sha256
    )
    facts.round.count_manifest_sha256 = (
        posting_service.canonical_opening_count_manifest_sha256(
            facts.task, facts.round, (facts.count_line,), ()
        )
    )
    facts.establishment.count_manifest_sha256 = facts.round.count_manifest_sha256

    assert_opening_guard_rejects(db, world, account)


def test_positive_opening_serial_movement_set_must_equal_count_evidence(
    db: Session,
    world: SimpleNamespace,
):
    serial_material = make_material(
        db,
        world.source,
        tracking_mode="serial",
        quantity_scale=0,
        allow_fraction=False,
    )
    account = make_account(
        db,
        organization=world.organization,
        material=serial_material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=serial_material.id,
        serial_no=f"SN-{uuid.uuid4().hex}",
        qr_code=f"QR-{uuid.uuid4().hex}",
        lot_id=None,
        lifecycle_status="active",
        created_at=facts.count_line.counted_at,
        updated_at=facts.count_line.counted_at,
    )
    db.add(serial)
    db.flush()
    positive = make_positive_opening_facts(
        db,
        facts,
        quantity=Decimal("1.000"),
        serials=(serial,),
    )
    db.delete(positive.movement_serials[0])

    assert_opening_guard_rejects(db, world, account)


def test_historical_opening_serial_lifecycle_change_does_not_break_other_scope(
    db: Session,
    world: SimpleNamespace,
):
    serial_material = make_material(
        db,
        world.source,
        tracking_mode="serial",
        quantity_scale=0,
        allow_fraction=False,
    )
    serial_account = make_account(
        db,
        organization=world.organization,
        material=serial_material,
    )
    facts = db.info["opening_facts_by_account"][serial_account.id]
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=serial_material.id,
        serial_no=f"SN-{uuid.uuid4().hex}",
        qr_code=f"QR-{uuid.uuid4().hex}",
        lot_id=None,
        lifecycle_status="active",
        created_at=facts.count_line.counted_at,
        updated_at=facts.count_line.counted_at,
    )
    db.add(serial)
    db.flush()
    make_positive_opening_facts(
        db,
        facts,
        quantity=Decimal("1.000"),
        serials=(serial,),
    )
    ordinary_account = make_account(
        db,
        organization=world.organization,
        material=world.material,
        established=False,
    )
    add_zero_scope_to_opening_task(db, facts, ordinary_account)
    serial.lifecycle_status = "consumed"
    db.commit()

    result = post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=ordinary_account.id,
                    quantity=Decimal("1"),
                    external_boundary_code="external-source",
                ),
            ),
        ),
    )

    assert result.replayed is False


def test_other_opening_scope_later_handover_or_inactivation_does_not_break_post(
    db: Session,
    world: SimpleNamespace,
):
    active_account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][active_account.id]
    historical_account = make_account(
        db,
        organization=world.organization,
        material=world.material,
        custodian=world.person,
        established=False,
    )
    historical_location = db.get(StockLocation, historical_account.location_id)
    assert historical_location is not None
    add_zero_scope_to_opening_task(db, facts, historical_account)
    historical_location.custodian_person_id = None
    historical_location.status = "inactive"
    db.commit()

    result = post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=active_account.id,
                    quantity=Decimal("1"),
                    external_boundary_code="external-source",
                ),
            ),
        ),
    )

    assert result.replayed is False


def test_all_location_all_scopes_must_be_established_atomically(
    db: Session,
    world: SimpleNamespace,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    second_account = make_account(
        db,
        organization=world.organization,
        material=world.material,
        established=False,
    )
    second = add_zero_scope_to_opening_task(db, facts, second_account)
    db.delete(second.establishment)

    assert_opening_guard_rejects(db, world, account)


@pytest.mark.parametrize("scope_mode", ["location_all", "filtered"])
def test_matching_active_hard_freeze_blocks_new_post(
    db: Session,
    world: SimpleNamespace,
    scope_mode: str,
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    freeze_account_scope(
        db,
        world,
        account,
        freeze_mode="hard",
        scope_mode=scope_mode,
    )
    db.commit()

    with pytest.raises(InventoryPostingError) as captured:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account.id,
                        quantity=Decimal("1"),
                        external_boundary_code="external-source",
                    ),
                ),
            ),
        )

    assert captured.value.code == "inventory_scope_hard_frozen"
    db.rollback()
    assert db.get(StockBalance, account.id) is None
    assert db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor == 1


def test_nonmatching_filtered_hard_freeze_does_not_widen_its_scope(
    db: Session, world: SimpleNamespace
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    freeze_account_scope(
        db,
        world,
        account,
        freeze_mode="hard",
        scope_mode="filtered",
        matches_account=False,
    )
    db.commit()

    result = post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=account.id,
                    quantity=Decimal("1"),
                    external_boundary_code="external-source",
                ),
            ),
        ),
    )

    assert result.replayed is False
    assert db.get(StockBalance, account.id).quantity == Decimal("1.000")


def test_cutoff_replay_freeze_allows_post_against_frozen_cursor(
    db: Session, world: SimpleNamespace
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    freeze_account_scope(
        db,
        world,
        account,
        freeze_mode="cutoff_replay",
    )
    db.commit()

    result = post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=account.id,
                    quantity=Decimal("2"),
                    external_boundary_code="external-source",
                ),
            ),
        ),
    )

    assert result.replayed is False
    assert db.get(StockBalance, account.id).quantity == Decimal("2.000")


def test_exact_replay_reauthorizes_but_ignores_a_later_hard_freeze(
    db: Session, world: SimpleNamespace
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    posting_command = command(
        "inbound",
        (
            InventoryMovementCommand(
                from_account_id=None,
                to_account_id=account.id,
                quantity=Decimal("3"),
                external_boundary_code="external-source",
            ),
        ),
        suffix="freeze-after-post",
    )
    idempotency_key = "idempotency-freeze-after-post"
    first = post(db, world, posting_command, key=idempotency_key)
    db.commit()
    freeze_account_scope(db, world, account, freeze_mode="hard")
    db.commit()

    replay = post_inventory_transaction(
        db,
        actor=world.current_principal,
        command=posting_command,
        idempotency_key=idempotency_key,
        request_id="request-freeze-after-post-replay",
    )

    assert replay.replayed is True
    assert replay.transaction_id == first.transaction_id
    assert db.get(StockBalance, account.id).quantity == Decimal("3.000")
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == 1


def test_negative_balance_failure_requires_whole_caller_rollback(
    db: Session, world: SimpleNamespace
):
    source = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("1"),
    )
    destination = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("0"),
    )
    source_id = source.id
    destination_id = destination.id
    db.commit()
    next_cursor_before = db.get(
        InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID
    ).next_cursor
    transactions_before = db.scalar(
        select(func.count()).select_from(InventoryTransaction)
    )
    audits_before = db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "inventory.transaction.posted"
        )
    )
    outboxes_before = db.scalar(
        select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.event_type == "inventory.transaction.posted"
        )
    )

    with pytest.raises(InventoryPostingError) as captured:
        post(
            db,
            world,
            command(
                "transfer",
                (
                    InventoryMovementCommand(
                        from_account_id=source_id,
                        to_account_id=destination_id,
                        quantity=Decimal("2"),
                    ),
                ),
            ),
        )
    assert captured.value.code == "insufficient_stock"
    db.rollback()

    assert db.get(StockBalance, source_id).quantity == Decimal("1.000")
    assert db.get(StockBalance, destination_id).quantity == Decimal("0.000")
    assert (
        db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor
        == next_cursor_before
    )
    assert (
        db.scalar(select(func.count()).select_from(InventoryTransaction))
        == transactions_before
    )
    assert db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "inventory.transaction.posted"
        )
    ) == audits_before
    assert db.scalar(
        select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.event_type == "inventory.transaction.posted"
        )
    ) == outboxes_before


def test_duplicate_serial_is_rejected_before_any_database_access(
    world: SimpleNamespace,
):
    serial_id = uuid.uuid4()
    account_a = uuid.uuid4()
    account_b = uuid.uuid4()
    invalid = command(
        "transfer",
        (
            InventoryMovementCommand(
                from_account_id=account_a,
                to_account_id=account_b,
                quantity=Decimal("1"),
                serial_ids=(serial_id,),
            ),
            InventoryMovementCommand(
                from_account_id=account_b,
                to_account_id=account_a,
                quantity=Decimal("1"),
                serial_ids=(serial_id,),
            ),
        ),
    )

    class NoDatabaseAccess:
        def __getattribute__(self, name):
            raise AssertionError(f"database accessed during pure validation: {name}")

    with pytest.raises(InventoryPostingError) as captured:
        post_inventory_transaction(
            NoDatabaseAccess(),
            actor=world.principal,
            command=invalid,
            idempotency_key="idempotency-pure-validation",
            request_id="request-pure-validation",
        )
    assert captured.value.code == "duplicate_serial_in_transaction"


def test_serial_count_and_current_position_are_strict(
    db: Session, world: SimpleNamespace
):
    serial_material = make_material(
        db,
        world.source,
        tracking_mode="serial",
        quantity_scale=0,
        allow_fraction=False,
    )
    account_a = make_account(
        db, organization=world.organization, material=serial_material
    )
    account_b = make_account(
        db,
        organization=world.organization,
        material=serial_material,
        initial=Decimal("0"),
    )
    account_c = make_account(
        db,
        organization=world.organization,
        material=serial_material,
        initial=Decimal("0"),
    )
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=serial_material.id,
        serial_no=f"SN-{uuid.uuid4().hex}",
        qr_code=f"QR-{uuid.uuid4().hex}",
        lot_id=None,
        lifecycle_status="active",
    )
    account_b_serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=serial_material.id,
        serial_no=f"SN-{uuid.uuid4().hex}",
        qr_code=f"QR-{uuid.uuid4().hex}",
        lot_id=None,
        lifecycle_status="active",
    )
    db.add_all([serial, account_b_serial])
    db.commit()
    post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=account_b.id,
                    quantity=Decimal("1"),
                    serial_ids=(account_b_serial.id,),
                    external_boundary_code="external-source",
                ),
            ),
        ),
    )
    db.commit()

    with pytest.raises(InventoryPostingError) as count_error:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account_a.id,
                        quantity=Decimal("2"),
                        serial_ids=(serial.id,),
                        external_boundary_code="external-source",
                    ),
                ),
            ),
        )
    assert count_error.value.code == "serial_quantity_mismatch"
    # The pure tracking failure occurs before the mutable balance phase.  Even
    # if a caller catches the domain error before rolling back, no zero-balance
    # row may remain pending in the Session and become commit-able by mistake.
    assert db.get(StockBalance, account_a.id) is None
    db.rollback()

    opening = command(
        "inbound",
        (
            InventoryMovementCommand(
                from_account_id=None,
                to_account_id=account_a.id,
                quantity=Decimal("1"),
                serial_ids=(serial.id,),
                external_boundary_code="external-source",
            ),
        ),
    )
    post(db, world, opening)
    db.commit()
    assert db.get(SerialCurrentPosition, serial.id).stock_account_id == account_a.id

    serial.lifecycle_status = "consumed"
    db.commit()
    with pytest.raises(InventoryPostingError) as lifecycle_error:
        post(
            db,
            world,
            command(
                "transfer",
                (
                    InventoryMovementCommand(
                        from_account_id=account_a.id,
                        to_account_id=account_c.id,
                        quantity=Decimal("1"),
                        serial_ids=(serial.id,),
                    ),
                ),
            ),
        )
    assert lifecycle_error.value.code == "serial_lifecycle_inactive"
    db.rollback()

    serial = db.get(InventorySerial, serial.id)
    serial.lifecycle_status = "active"
    db.commit()
    with pytest.raises(InventoryPostingError) as projection_error:
        post(
            db,
            world,
            command(
                "consume",
                (
                    InventoryMovementCommand(
                        from_account_id=account_a.id,
                        to_account_id=None,
                        quantity=Decimal("1"),
                        serial_ids=(serial.id,),
                        external_boundary_code="consumption",
                    ),
                ),
            ),
        )
    assert (
        projection_error.value.code
        == "serial_lifecycle_projection_unavailable"
    )
    db.rollback()

    with pytest.raises(InventoryPostingError) as position_error:
        post(
            db,
            world,
            command(
                "transfer",
                (
                    InventoryMovementCommand(
                        from_account_id=account_b.id,
                        to_account_id=account_c.id,
                        quantity=Decimal("1"),
                        serial_ids=(serial.id,),
                    ),
                ),
            ),
        )
    assert position_error.value.code == "serial_position_mismatch"
    db.rollback()


def test_same_key_exact_replay_and_different_payload_conflict(
    db: Session, world: SimpleNamespace
):
    account = make_account(
        db, organization=world.organization, material=world.material
    )
    db.commit()
    posting_command = command(
        "inbound",
        (
            InventoryMovementCommand(
                from_account_id=None,
                to_account_id=account.id,
                quantity=Decimal("3"),
                external_boundary_code="external-source",
            ),
        ),
        suffix="replay",
    )
    key = "idempotency-exact-replay"

    first = post(db, world, posting_command, key=key)
    replay = post_inventory_transaction(
        db,
        actor=world.current_principal,
        command=posting_command,
        idempotency_key=key,
        request_id="request-replay-second",
    )

    assert replay.replayed is True
    assert replay.transaction_id == first.transaction_id
    assert db.scalar(select(func.count()).select_from(InventoryTransaction)) == 1
    assert db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "inventory.transaction.posted"
        )
    ) == 1
    assert db.scalar(
        select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.event_type == "inventory.transaction.posted"
        )
    ) == 1
    changed = replace_movement_quantity(posting_command, Decimal("2"))
    with pytest.raises(InventoryPostingError) as conflict:
        post_inventory_transaction(
            db,
            actor=world.current_principal,
            command=changed,
            idempotency_key=key,
            request_id="request-replay-conflict",
        )
    assert conflict.value.code == "idempotency_key_conflict"


def replace_movement_quantity(
    value: InventoryPostingCommand, quantity: Decimal
) -> InventoryPostingCommand:
    movement = value.movements[0]
    return InventoryPostingCommand(
        transaction_no=value.transaction_no,
        movement_type=value.movement_type,
        source_document_type=value.source_document_type,
        source_document_id=value.source_document_id,
        posting_key=value.posting_key,
        effective_at=value.effective_at,
        movements=(
            InventoryMovementCommand(
                from_account_id=movement.from_account_id,
                to_account_id=movement.to_account_id,
                quantity=quantity,
                serial_ids=movement.serial_ids,
                external_boundary_code=movement.external_boundary_code,
            ),
        ),
    )


def test_posting_key_cannot_be_reused_with_another_idempotency_key(
    db: Session, world: SimpleNamespace
):
    account = make_account(
        db, organization=world.organization, material=world.material
    )
    db.commit()
    posting_command = command(
        "inbound",
        (
            InventoryMovementCommand(
                from_account_id=None,
                to_account_id=account.id,
                quantity=Decimal("1"),
                external_boundary_code="external-source",
            ),
        ),
    )
    post(db, world, posting_command, key="idempotency-posting-key-one")

    with pytest.raises(InventoryPostingError) as captured:
        post(db, world, posting_command, key="idempotency-posting-key-two")
    assert captured.value.code == "posting_key_conflict"


def test_balances_can_be_reconstructed_from_immutable_movements(
    db: Session, world: SimpleNamespace
):
    account_a = make_account(
        db, organization=world.organization, material=world.material
    )
    account_b = make_account(
        db, organization=world.organization, material=world.material
    )
    db.commit()
    post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=account_a.id,
                    quantity=Decimal("10"),
                    external_boundary_code="opening-source",
                ),
            ),
        ),
    )
    post(
        db,
        world,
        command(
            "transfer",
            (
                InventoryMovementCommand(
                    from_account_id=account_a.id,
                    to_account_id=account_b.id,
                    quantity=Decimal("4"),
                ),
            ),
        ),
    )
    post(
        db,
        world,
        command(
            "consume",
            (
                InventoryMovementCommand(
                    from_account_id=account_b.id,
                    to_account_id=None,
                    quantity=Decimal("1"),
                    external_boundary_code="consumption",
                ),
            ),
        ),
    )

    reconstructed = {account_a.id: Decimal("0"), account_b.id: Decimal("0")}
    for movement in db.scalars(
        select(InventoryMovement).order_by(
            InventoryMovement.transaction_id, InventoryMovement.line_no
        )
    ):
        if movement.from_account_id in reconstructed:
            reconstructed[movement.from_account_id] -= movement.quantity
        if movement.to_account_id in reconstructed:
            reconstructed[movement.to_account_id] += movement.quantity
    assert reconstructed[account_a.id] == db.get(StockBalance, account_a.id).quantity
    assert reconstructed[account_b.id] == db.get(StockBalance, account_b.id).quantity
    assert reconstructed == {account_a.id: Decimal("6.000"), account_b.id: Decimal("3.000")}


def test_exact_reversal_restores_balance_and_second_reversal_is_rejected(
    db: Session, world: SimpleNamespace
):
    account_a = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("10"),
    )
    account_b = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("0"),
    )
    db.commit()
    original_command = command(
        "transfer",
        (
            InventoryMovementCommand(
                from_account_id=account_a.id,
                to_account_id=account_b.id,
                quantity=Decimal("4"),
            ),
        ),
    )
    original_key = "idempotency-original-transfer"
    original = post(db, world, original_command, key=original_key)
    cross_operation = InventoryReversalCommand(
        original_transaction_id=original.transaction_id,
        transaction_no=f"RV-{uuid.uuid4().hex}",
        source_document_type="reversal_case",
        source_document_id=f"DOC-{uuid.uuid4().hex}",
        posting_key=f"inventory:reverse:{uuid.uuid4().hex}",
        effective_at=NOW,
    )
    with pytest.raises(InventoryPostingError) as cross_conflict:
        reverse_inventory_transaction(
            db,
            actor=world.current_principal,
            command=cross_operation,
            idempotency_key=original_key,
            request_id="request-cross-operation",
        )
    assert cross_conflict.value.code == "idempotency_key_conflict"
    db.commit()
    original_row = db.get(InventoryTransaction, original.transaction_id)
    original_snapshot = (
        original_row.status,
        original_row.movement_type,
        original_row.reversed_transaction_id,
    )
    reversal = InventoryReversalCommand(
        original_transaction_id=original.transaction_id,
        transaction_no=f"RV-{uuid.uuid4().hex}",
        source_document_type="reversal_case",
        source_document_id=f"DOC-{uuid.uuid4().hex}",
        posting_key=f"inventory:reverse:{uuid.uuid4().hex}",
        effective_at=NOW,
    )

    reversed_result = reverse_inventory_transaction(
        db,
        actor=world.current_principal,
        command=reversal,
        idempotency_key="idempotency-reversal-one",
        request_id="request-reversal-one",
    )

    assert db.get(StockBalance, account_a.id).quantity == Decimal("10.000")
    assert db.get(StockBalance, account_b.id).quantity == Decimal("0.000")
    reversal_row = db.get(InventoryTransaction, reversed_result.transaction_id)
    assert reversal_row.movement_type == "reversal"
    assert reversal_row.reversed_transaction_id == original.transaction_id
    inverse = db.scalar(
        select(InventoryMovement).where(
            InventoryMovement.transaction_id == reversed_result.transaction_id
        )
    )
    assert inverse.from_account_id == account_b.id
    assert inverse.to_account_id == account_a.id
    original_row = db.get(InventoryTransaction, original.transaction_id)
    assert (
        original_row.status,
        original_row.movement_type,
        original_row.reversed_transaction_id,
    ) == original_snapshot

    second = InventoryReversalCommand(
        original_transaction_id=original.transaction_id,
        transaction_no=f"RV-{uuid.uuid4().hex}",
        source_document_type="reversal_case",
        source_document_id=f"DOC-{uuid.uuid4().hex}",
        posting_key=f"inventory:reverse:{uuid.uuid4().hex}",
        effective_at=NOW,
    )
    with pytest.raises(InventoryPostingError) as captured:
        reverse_inventory_transaction(
            db,
            actor=world.current_principal,
            command=second,
            idempotency_key="idempotency-reversal-two",
            request_id="request-reversal-two",
        )
    assert captured.value.code == "original_transaction_already_reversed"


def test_new_reversal_is_blocked_by_a_matching_active_hard_freeze(
    db: Session, world: SimpleNamespace
):
    source = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("2"),
    )
    destination = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("0"),
    )
    original = post(
        db,
        world,
        command(
            "transfer",
            (
                InventoryMovementCommand(
                    from_account_id=source.id,
                    to_account_id=destination.id,
                    quantity=Decimal("1"),
                ),
            ),
        ),
    )
    db.commit()
    freeze_account_scope(db, world, destination, freeze_mode="hard")
    db.commit()
    transaction_count_before = db.scalar(
        select(func.count()).select_from(InventoryTransaction)
    )
    next_cursor_before = db.get(
        InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID
    ).next_cursor

    with pytest.raises(InventoryPostingError) as captured:
        reverse_inventory_transaction(
            db,
            actor=world.current_principal,
            command=InventoryReversalCommand(
                original_transaction_id=original.transaction_id,
                transaction_no=f"RV-{uuid.uuid4().hex}",
                source_document_type="reversal_case",
                source_document_id=f"DOC-{uuid.uuid4().hex}",
                posting_key=f"inventory:reverse:{uuid.uuid4().hex}",
                effective_at=NOW,
            ),
            idempotency_key="idempotency-frozen-reversal",
            request_id="request-frozen-reversal",
        )

    assert captured.value.code == "inventory_scope_hard_frozen"
    db.rollback()
    assert db.get(StockBalance, source.id).quantity == Decimal("1.000")
    assert db.get(StockBalance, destination.id).quantity == Decimal("1.000")
    assert (
        db.scalar(select(func.count()).select_from(InventoryTransaction))
        == transaction_count_before
    )
    assert (
        db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor
        == next_cursor_before
    )


def test_generic_reversal_rejects_opening_transaction(
    db: Session, world: SimpleNamespace
):
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("0"),
    )
    head = db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID)
    assert head is not None
    opening_cursor = head.next_cursor
    original = InventoryTransaction(
        id=uuid.uuid4(),
        transaction_no=f"OPEN-{uuid.uuid4().hex}",
        movement_type="opening",
        source_document_type="opening_stocktake",
        source_document_id=f"TASK-{uuid.uuid4().hex}",
        posting_key=f"opening:{uuid.uuid4().hex}",
        idempotency_key_hash="a" * 64,
        request_hash="b" * 64,
        status="posted",
        effective_at=NOW,
        posted_at=NOW,
        ledger_cursor=opening_cursor,
        reversed_transaction_id=None,
        actor_user_id=world.user.id,
    )
    db.add(original)
    db.flush()
    db.add(
        InventoryMovement(
            id=uuid.uuid4(),
            transaction_id=original.id,
            line_no=1,
            from_account_id=None,
            to_account_id=account.id,
            external_boundary_code="approved-opening",
            quantity=Decimal("4"),
        )
    )
    balance = db.get(StockBalance, account.id)
    assert balance is not None
    balance.quantity = Decimal("4.000")
    balance.ledger_cursor = opening_cursor
    balance.version = 1
    head.next_cursor = opening_cursor + 1
    db.commit()

    with pytest.raises(InventoryPostingError) as captured:
        reverse_inventory_transaction(
            db,
            actor=world.current_principal,
            command=InventoryReversalCommand(
                original_transaction_id=original.id,
                transaction_no=f"RV-{uuid.uuid4().hex}",
                source_document_type="stocktake_compensation",
                source_document_id=f"CASE-{uuid.uuid4().hex}",
                posting_key=f"opening-compensation:{uuid.uuid4().hex}",
                effective_at=NOW,
            ),
            idempotency_key="idempotency-opening-reversal",
            request_id="request-opening-reversal",
        )

    assert (
        captured.value.code
        == "inventory_opening_reversal_requires_stocktake_compensation"
    )


def test_serial_transfer_reversal_restores_exact_current_position(
    db: Session, world: SimpleNamespace
):
    material = make_material(
        db,
        world.source,
        tracking_mode="serial",
        quantity_scale=0,
        allow_fraction=False,
    )
    account_a = make_account(
        db, organization=world.organization, material=material
    )
    account_b = make_account(
        db, organization=world.organization, material=material
    )
    serial = InventorySerial(
        id=uuid.uuid4(),
        material_id=material.id,
        serial_no=f"SN-{uuid.uuid4().hex}",
        qr_code=f"QR-{uuid.uuid4().hex}",
        lot_id=None,
        lifecycle_status="active",
    )
    db.add(serial)
    db.commit()
    post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=account_a.id,
                    quantity=Decimal("1"),
                    serial_ids=(serial.id,),
                    external_boundary_code="opening-source",
                ),
            ),
        ),
    )
    transfer = post(
        db,
        world,
        command(
            "transfer",
            (
                InventoryMovementCommand(
                    from_account_id=account_a.id,
                    to_account_id=account_b.id,
                    quantity=Decimal("1"),
                    serial_ids=(serial.id,),
                ),
            ),
        ),
    )
    db.commit()
    assert db.get(SerialCurrentPosition, serial.id).stock_account_id == account_b.id

    reversal = reverse_inventory_transaction(
        db,
        actor=world.current_principal,
        command=InventoryReversalCommand(
            original_transaction_id=transfer.transaction_id,
            transaction_no=f"RV-{uuid.uuid4().hex}",
            source_document_type="reversal_case",
            source_document_id=f"DOC-{uuid.uuid4().hex}",
            posting_key=f"inventory:reverse:{uuid.uuid4().hex}",
            effective_at=NOW,
        ),
        idempotency_key="idempotency-serial-reversal",
        request_id="request-serial-reversal",
    )

    assert db.get(SerialCurrentPosition, serial.id).stock_account_id == account_a.id
    assert db.get(StockBalance, account_a.id).quantity == Decimal("1.000")
    assert db.get(StockBalance, account_b.id).quantity == Decimal("0.000")
    assert db.scalar(
        select(func.count())
        .select_from(InventoryMovementSerial)
        .where(InventoryMovementSerial.transaction_id == reversal.transaction_id)
    ) == 1


def test_permission_is_checked_for_each_account_scope(
    db: Session, world: SimpleNamespace
):
    other_organization = make_organization(db, "其他组织")
    source = make_account(
        db,
        organization=world.organization,
        material=world.material,
        initial=Decimal("2"),
    )
    destination = make_account(
        db,
        organization=other_organization,
        material=world.material,
        initial=Decimal("0"),
    )
    limited = make_principal(
        world.user,
        world.person,
        scope_type="organization",
        scope_id=str(world.organization.id),
    )
    world.current_principal = limited
    db.commit()

    with pytest.raises(InventoryPostingError) as captured:
        post(
            db,
            world,
            command(
                "transfer",
                (
                    InventoryMovementCommand(
                        from_account_id=source.id,
                        to_account_id=destination.id,
                        quantity=Decimal("1"),
                    ),
                ),
            ),
        )
    assert captured.value.code == "inventory_account_forbidden"
    db.rollback()
    assert db.get(StockBalance, source.id).quantity == Decimal("2.000")
    assert db.get(StockBalance, destination.id).quantity == Decimal("0.000")


def test_personal_location_and_account_custodian_must_match(
    db: Session, world: SimpleNamespace
):
    parent = StockLocation(
        id=uuid.uuid4(),
        code=f"LOC-{uuid.uuid4().hex[:12]}",
        name="区域父库位",
        location_type="region",
        owner_org_id=world.organization.id,
        parent_id=None,
        custodian_person_id=None,
        status="active",
    )
    db.add(parent)
    db.flush()
    personal = StockLocation(
        id=uuid.uuid4(),
        code=f"LOC-{uuid.uuid4().hex[:12]}",
        name="个人仓库位",
        location_type="personal",
        owner_org_id=world.organization.id,
        parent_id=parent.id,
        custodian_person_id=world.person.id,
        status="active",
    )
    db.add(personal)
    db.flush()
    mismatched_account = StockAccount(
        id=uuid.uuid4(),
        owner_org_id=world.organization.id,
        custodian_person_id=None,
        location_id=personal.id,
        material_id=world.material.id,
        condition_code="new",
        availability_bucket="available",
        lot_id=None,
    )
    db.add(mismatched_account)
    db.commit()

    with pytest.raises(InventoryPostingError) as captured:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=mismatched_account.id,
                        quantity=Decimal("1"),
                        external_boundary_code="opening-source",
                    ),
                ),
            ),
        )
    assert captured.value.code == "personal_custodian_mismatch"
    db.rollback()


def test_cross_organization_unheld_account_requires_both_scopes(
    db: Session, world: SimpleNamespace
):
    regional_operator = make_organization(
        db,
        "区域运营组织",
        org_type="department",
        parent_id=world.organization.id,
    )
    account = make_account(
        db,
        organization=world.organization,
        location_organization=regional_operator,
        material=world.material,
    )
    regional_principal = make_principal(
        world.user,
        world.person,
        scope_type="organization",
        scope_id=str(regional_operator.id),
    )
    world.current_principal = regional_principal
    db.commit()

    with pytest.raises(InventoryPostingError) as captured:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account.id,
                        quantity=Decimal("1"),
                        external_boundary_code="opening-source",
                    ),
                ),
            ),
        )
    assert captured.value.code == "inventory_account_forbidden"
    db.rollback()

    national_principal = make_principal(
        world.user,
        world.person,
        scope_type="national",
        scope_id="*",
    )
    world.current_principal = national_principal
    result = post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=account.id,
                    quantity=Decimal("1"),
                    external_boundary_code="opening-source",
                ),
            ),
        ),
    )

    assert result.replayed is False
    assert db.get(StockBalance, account.id).quantity == Decimal("1.000")


def test_service_flushes_without_commit_and_caller_rollback_removes_everything(
    db: Session, world: SimpleNamespace
):
    account = make_account(
        db, organization=world.organization, material=world.material
    )
    account_id = account.id
    db.commit()
    result = post(
        db,
        world,
        command(
            "inbound",
            (
                InventoryMovementCommand(
                    from_account_id=None,
                    to_account_id=account_id,
                    quantity=Decimal("1"),
                    external_boundary_code="opening-source",
                ),
            ),
        ),
    )
    assert db.get(InventoryTransaction, result.transaction_id) is not None
    assert db.get(StockBalance, account_id).quantity == Decimal("1.000")

    db.rollback()

    assert db.get(InventoryTransaction, result.transaction_id) is None
    assert db.get(StockBalance, account_id) is None
    assert db.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor == 1
    assert db.scalar(
        select(func.count()).select_from(AuditEvent).where(
            AuditEvent.action == "inventory.transaction.posted"
        )
    ) == 0
    assert db.scalar(
        select(func.count()).select_from(OutboxEvent).where(
            OutboxEvent.event_type == "inventory.transaction.posted"
        )
    ) == 0


def test_final_review_missing_outbox_blocks_inventory_posting(
    db: Session,
    world: SimpleNamespace,
) -> None:
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    outbox = db.scalar(
        select(OutboxEvent).where(
            OutboxEvent.event_type
            == "stocktake.opening.headquarters_reviewed",
            OutboxEvent.aggregate_id == str(facts.task.id),
        )
    )
    assert outbox is not None
    db.delete(outbox)
    db.commit()

    with pytest.raises(InventoryPostingError) as blocked:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account.id,
                        quantity=Decimal("1"),
                        external_boundary_code="external-source",
                    ),
                ),
            ),
        )
    assert blocked.value.code == "inventory_opening_establishment_invalid"


@pytest.mark.parametrize("effect_kind", ("state", "outbox"))
def test_final_review_alternate_key_duplicate_effect_blocks_inventory_posting(
    db: Session,
    world: SimpleNamespace,
    effect_kind: str,
) -> None:
    account = make_account(
        db,
        organization=world.organization,
        material=world.material,
    )
    facts = db.info["opening_facts_by_account"][account.id]
    review = facts.headquarters_review
    if effect_kind == "state":
        canonical = db.scalar(
            select(StateTransitionEvent).where(
                StateTransitionEvent.idempotency_key
                == review_service._event_key("state", review.id)
            )
        )
        assert canonical is not None
        db.add(
            StateTransitionEvent(
                aggregate_type=canonical.aggregate_type,
                aggregate_id=canonical.aggregate_id,
                from_status=canonical.from_status,
                to_status=canonical.to_status,
                reason=canonical.reason,
                actor_id=canonical.actor_id,
                idempotency_key=f"alternate-final-state-{uuid.uuid4().hex}",
                occurred_at=canonical.occurred_at,
                metadata_jsonb=dict(canonical.metadata_jsonb),
                created_at=canonical.created_at,
            )
        )
    else:
        canonical = db.scalar(
            select(OutboxEvent).where(
                OutboxEvent.idempotency_key
                == review_service._event_key("outbox", review.id)
            )
        )
        assert canonical is not None
        db.add(
            OutboxEvent(
                event_type=canonical.event_type,
                aggregate_type=canonical.aggregate_type,
                aggregate_id=canonical.aggregate_id,
                payload_jsonb=dict(canonical.payload_jsonb),
                status=canonical.status,
                attempts=canonical.attempts,
                idempotency_key=f"alternate-final-outbox-{uuid.uuid4().hex}",
                available_at=canonical.available_at,
                locked_at=canonical.locked_at,
                locked_by=canonical.locked_by,
                published_at=canonical.published_at,
                last_error=canonical.last_error,
                created_at=canonical.created_at,
                updated_at=canonical.updated_at,
            )
        )
    db.commit()

    with pytest.raises(InventoryPostingError) as blocked:
        post(
            db,
            world,
            command(
                "inbound",
                (
                    InventoryMovementCommand(
                        from_account_id=None,
                        to_account_id=account.id,
                        quantity=Decimal("1"),
                        external_boundary_code="external-source",
                    ),
                ),
            ),
        )
    assert blocked.value.code == "inventory_opening_establishment_invalid"


def test_sqlite_state_contract_does_not_claim_postgresql_concurrency(
    db: Session,
):
    """PostgreSQL advisory/row-lock contention remains a separate release gate."""

    assert db.get_bind().dialect.name == "sqlite"
