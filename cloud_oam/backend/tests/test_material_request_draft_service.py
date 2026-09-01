from __future__ import annotations

import base64
from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import itertools
import json
from unittest.mock import patch
import uuid

import pytest
from sqlalchemy import create_engine, event, select
from sqlalchemy.orm import Session

from app.database import Base
from app.demand_models import (
    ApprovalAction,
    ApprovalInstance,
    ApprovalRouteStepDef,
    ApprovalRouteVersion,
    ApprovalStep,
    ApprovalStepCandidate,
    MaterialRequest,
    MaterialRequestCommand,
    MaterialRequestFile,
    MaterialRequestLine,
    MaterialRequestRevision,
)
from app.formal_access import FormalPrincipal, load_formal_principal
from app.formal_services import formal_files
from app.formal_services import material_request_draft as draft_service
from app.formal_services.material_request_draft import (
    MATERIAL_REQUEST_AUDIT_STREAM,
    MaterialRequestDraftError,
    MaterialRequestDraftInput,
    MaterialRequestDraftLineInput,
    amend_material_request_draft,
    create_material_request_draft,
    derive_material_request_create_id,
    mask_material_request_contact,
    submit_material_request,
)
from app.foundation_models import (
    AuditChainHead,
    AuditEvent,
    AuthIdentity,
    ExternalObject,
    FileObject,
    Organization,
    OutboxEvent,
    Permission,
    Person,
    Role,
    RoleAssignment,
    RolePermission,
    SourceSystem,
    StateTransitionEvent,
)
from app.inventory_models import FormalMaterial, InventoryTransaction
from app.models import User


NOW = datetime(2026, 8, 31, 16, 0, tzinfo=timezone.utc)
SECRET = b"material-request-idempotency-test-secret-v1"
AUDIT_HEAD_ID = uuid.UUID("30000000-0000-4000-8000-000000000004")
_MOBILES = itertools.count(13810000000)


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


@dataclass
class World:
    headquarters: Organization
    region: Organization
    department: Organization
    roles: dict[str, Role]
    actor_user: User
    actor_person: Person
    actor: FormalPrincipal
    manager_users: tuple[User, ...]
    admin_users: tuple[User, ...]
    materials: tuple[FormalMaterial, FormalMaterial]
    attachment: FileObject


def make_world(db: Session, *, manager_count: int = 1, admin_count: int = 2) -> World:
    headquarters = _organization(db, "HQ", "headquarters")
    region = _organization(db, "REGION", "region_company", parent=headquarters)
    department = _organization(db, "DEPT", "department", parent=region)
    roles = {
        code: Role(
            code=code,
            name=code,
            is_external=code == "star_headquarters_approver",
            status="active",
        )
        for code in (
            "admin",
            "provincial_manager",
            "technician",
            "star_headquarters_approver",
        )
    }
    db.add_all(roles.values())
    db.flush()
    _seed_permissions(db, roles)

    actor_person = _person(db, department, "申请工程师")
    actor_user = _user(db, actor_person)
    _assignment(
        db,
        actor_user,
        roles["technician"],
        scope_type="person",
        scope_id=str(actor_person.id),
        assigned_by=actor_user.id,
    )

    manager_users: list[User] = []
    for index in range(manager_count):
        person = _person(db, department, f"区域负责人{index}")
        user = _user(db, person)
        _assignment(
            db,
            user,
            roles["provincial_manager"],
            scope_type="organization",
            scope_id=str(region.id),
            assigned_by=actor_user.id,
        )
        manager_users.append(user)

    admin_users: list[User] = []
    for index in range(admin_count):
        person = _person(db, headquarters, f"总部管理员{index}")
        user = _user(db, person)
        _assignment(
            db,
            user,
            roles["admin"],
            scope_type="national",
            scope_id="*",
            assigned_by=actor_user.id,
        )
        admin_users.append(user)

    source = SourceSystem(
        code=f"TEST-{uuid.uuid4().hex[:8]}",
        name="test source",
        mode="read_only",
        enabled=True,
        configuration_jsonb={},
    )
    db.add(source)
    db.flush()
    materials: list[FormalMaterial] = []
    for index in range(2):
        external = ExternalObject(
            source_system_id=source.id,
            entity_type="material",
            external_id=f"MAT-{uuid.uuid4()}",
            current_version_id=None,
            deleted_at=None,
        )
        db.add(external)
        db.flush()
        material = FormalMaterial(
            external_object_id=external.id,
            sku_code=f"SKU-{uuid.uuid4().hex[:10]}",
            name=f"物料{index}",
            specification="",
            base_unit="件",
            status="active",
            source_updated_at=NOW,
        )
        db.add(material)
        db.flush()
        materials.append(material)

    attachment_id = uuid.uuid4()
    attachment_storage_key = (
        "formal-files/v1/request_attachment/"
        f"{attachment_id.hex[:2]}/{attachment_id.hex}"
    )
    attachment = FileObject(
        id=attachment_id,
        storage_key=attachment_storage_key,
        sha256="a" * 64,
        size_bytes=128,
        mime_type="image/png",
        original_filename="proof.png",
        uploaded_by=actor_user.id,
        status="available",
        metadata_jsonb={
            "authorization_version": actor_user.authorization_version,
            "file_id": str(attachment_id),
            "idempotency_key_hash": "b" * 64,
            "provider": "test_formal_storage",
            "purpose": "request_attachment",
            "request_sha256": formal_files._upload_request_hash(
                formal_files._PreparedUpload(
                    purpose="request_attachment",
                    original_filename="proof.png",
                    size_bytes=128,
                    mime_type="image/png",
                    sha256="a" * 64,
                )
            ),
            "schema": "cloud_oam.formal_file_upload_intent.v1",
            "storage_key": attachment_storage_key,
            "uploader_person_id": str(actor_person.id),
            "uploader_user_id": actor_user.id,
            "completion": {
                "etag_sha256": "d" * 64,
                "head_manifest_sha256": "e" * 64,
                "verified_at": NOW.isoformat(),
            },
        },
    )
    db.add(attachment)
    db.add(
        AuditChainHead(
            id=AUDIT_HEAD_ID,
            stream_key=MATERIAL_REQUEST_AUDIT_STREAM,
            last_event_id=None,
            last_hash=None,
            version=0,
        )
    )
    route = ApprovalRouteVersion(
        route_code="material_request_three_stage",
        version=1,
        approval_mode="external_registration",
        effective_from=NOW - timedelta(days=1),
        effective_to=None,
        status="active",
    )
    db.add(route)
    db.flush()
    db.add_all(
        (
            ApprovalRouteStepDef(
                route_version_id=route.id,
                step_no=1,
                role_code="provincial_manager",
                source_mode="internal",
                scope_type="organization",
            ),
            ApprovalRouteStepDef(
                route_version_id=route.id,
                step_no=2,
                role_code="admin",
                source_mode="internal",
                scope_type="national",
            ),
            ApprovalRouteStepDef(
                route_version_id=route.id,
                step_no=3,
                role_code="star_headquarters_approver",
                source_mode="external_registration",
                scope_type="document",
            ),
        )
    )
    db.commit()
    actor = load_formal_principal(db, actor_user.id, now=NOW)
    return World(
        headquarters=headquarters,
        region=region,
        department=department,
        roles=roles,
        actor_user=actor_user,
        actor_person=actor_person,
        actor=actor,
        manager_users=tuple(manager_users),
        admin_users=tuple(admin_users),
        materials=(materials[0], materials[1]),
        attachment=attachment,
    )


def _organization(
    db: Session,
    code: str,
    org_type: str,
    *,
    parent: Organization | None = None,
) -> Organization:
    row = Organization(
        code=f"{code}-{uuid.uuid4().hex[:8]}",
        name=code,
        parent_id=parent.id if parent else None,
        org_type=org_type,
        province_code="320000" if org_type == "region_company" else None,
        status="active",
    )
    db.add(row)
    db.flush()
    return row


def _person(db: Session, organization: Organization, name: str) -> Person:
    row = Person(
        organization_id=organization.id,
        employee_no=f"E-{uuid.uuid4().hex[:10]}",
        name=name,
        mobile_encrypted=None,
        mobile_hash=None,
        employment_status="active",
        source_updated_at=NOW,
    )
    db.add(row)
    db.flush()
    return row


def _user(db: Session, person: Person) -> User:
    row = User(
        person_id=person.id,
        account_status="active",
        authorization_version=1,
        mobile=str(next(_MOBILES)),
        name=person.name,
        password_hash="password-login-disabled",
        role="technician",
        province="legacy-must-not-authorize",
        is_active=True,
        require_password_change=False,
    )
    db.add(row)
    db.flush()
    db.add(
        AuthIdentity(
            user_id=row.id,
            identity_type="mobile",
            provider_key="test",
            identifier_hash=hashlib.sha256(row.id.encode()).hexdigest(),
            hash_version=1,
            verified_at=NOW,
            status="active",
            revoked_at=None,
        )
    )
    db.flush()
    return row


def _assignment(
    db: Session,
    user: User,
    role: Role,
    *,
    scope_type: str,
    scope_id: str,
    assigned_by: str,
) -> RoleAssignment:
    row = RoleAssignment(
        user_id=user.id,
        role_id=role.id,
        scope_type=scope_type,
        scope_id=scope_id,
        valid_from=NOW - timedelta(days=1),
        valid_to=None,
        status="active",
        assigned_by=assigned_by,
        revoked_at=None,
        revoked_by=None,
        reason="test",
    )
    db.add(row)
    db.flush()
    return row


def _seed_permissions(db: Session, roles: dict[str, Role]) -> None:
    matrix = {
        "technician": (
            ("create", ""),
            ("update_draft", ""),
            ("submit", ""),
        ),
        "provincial_manager": (("approve_region", "approval_decision"),),
        "admin": (
            ("approve_headquarters", "approval_decision"),
            ("register_external", "approval_evidence"),
            ("verify_external", "approval_evidence"),
        ),
    }
    for role_code, values in matrix.items():
        for action, field_code in values:
            permission = Permission(
                resource="material_request",
                action=action,
                field_code=field_code,
                description="test",
            )
            db.add(permission)
            db.flush()
            db.add(
                RolePermission(
                    role_id=roles[role_code].id,
                    permission_id=permission.id,
                    effect="allow",
                )
            )
    db.flush()


def _contact_envelope(request_id: uuid.UUID, person_id: uuid.UUID) -> dict[str, object]:
    aad = (
        "cloud_oam.material_request.contact.envelope.v1\0"
        f"request_id={request_id}\0requester_person_id={person_id}"
    ).encode("ascii")
    return {
        "schema": "rsc.material_request_contact.v1",
        "provider": "aliyun_kms",
        "kms_key_id": "kms/rsc/material-request/test",
        "key_version": 1,
        "ciphertext_b64": base64.b64encode(b"\x91" * 32).decode("ascii"),
        "nonce_b64": base64.b64encode(b"\x72" * 12).decode("ascii"),
        "aad_sha256": hashlib.sha256(aad).hexdigest(),
        "mobile_hmac": f"hmac:1:{'b' * 64}",
        "contact_hmac": f"hmac:1:{'c' * 64}",
    }


def _draft(world: World, request_id: uuid.UUID, *, material_index: int = 0):
    return MaterialRequestDraftInput(
        work_order_id=None,
        purpose="维修工单备件需求",
        urgency="normal",
        expected_date=None,
        address_snapshot={
            "province_code": "320000",
            "province_name": "江苏省",
            "city_name": "南京市",
            "district_name": "鼓楼区",
            "detail": "敏感测试地址 1 号",
        },
        contact_envelope=_contact_envelope(request_id, world.actor_person.id),
        contact_masked=mask_material_request_contact(
            name="张工程师", mobile="13800138000"
        ),
        attachment_file_ids=(world.attachment.id,),
        lines=(
            MaterialRequestDraftLineInput(
                material_id=world.materials[material_index].id,
                requested_qty=Decimal("2.000"),
                note="",
            ),
        ),
        note="",
    )


def _create(db: Session, world: World, request_id: uuid.UUID, *, key: str = "create-1"):
    return create_material_request_draft(
        db,
        actor=world.actor,
        material_request_id=request_id,
        draft=_draft(world, request_id),
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
        trace_request_id=f"trace-{key}",
    )


def _create_id(world: World, key: str = "create-1") -> uuid.UUID:
    return derive_material_request_create_id(
        actor=world.actor,
        idempotency_key=key,
        idempotency_hmac_secret=SECRET,
    )


def _assert_error(exc: pytest.ExceptionInfo[MaterialRequestDraftError], code: str, status: int):
    assert exc.value.code == code
    assert exc.value.http_status_code == status


def test_create_derives_owner_protects_contact_and_replays_exactly(db: Session) -> None:
    world = make_world(db)
    request_id = _create_id(world, "same-create")
    with patch.object(db, "commit", side_effect=AssertionError("service committed")), patch.object(
        db, "rollback", side_effect=AssertionError("service rolled back")
    ):
        first = _create(db, world, request_id, key="same-create")
        # A replay returns the immutable recorded result even if a mutable
        # reference has since changed; randomized KMS output is intentionally
        # excluded from the semantic request hash.
        world.attachment.status = "quarantined"
        db.flush()
        randomized = dict(_contact_envelope(request_id, world.actor_person.id))
        randomized.update(
            {
                "kms_key_id": "kms/rsc/material-request/rotated-test",
                "key_version": 2,
                "ciphertext_b64": base64.b64encode(b"\x92" * 32).decode("ascii"),
                "nonce_b64": base64.b64encode(b"\x73" * 12).decode("ascii"),
            }
        )
        replay = create_material_request_draft(
            db,
            actor=world.actor,
            material_request_id=request_id,
            draft=replace(_draft(world, request_id), contact_envelope=randomized),
            idempotency_key="same-create",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-same-create-randomized-ciphertext",
        )

    assert first.schema_version == "1.0"
    assert first.action == "create"
    assert first.request_version == 0
    assert first.revision_no == 1
    assert replay.idempotency_replayed is True
    assert replace(replay, idempotency_replayed=False) == first
    request = db.get(MaterialRequest, request_id)
    assert request is not None
    assert request.requester_user_id == world.actor_user.id
    assert request.requester_person_id == world.actor_person.id
    assert request.requester_org_id == world.region.id
    revision = db.get(MaterialRequestRevision, first.revision_id)
    assert revision is not None
    assert (revision.request_id, revision.revision_no, revision.status) == (
        request.id,
        1,
        "draft",
    )
    assert request.revision_no == revision.revision_no
    assert request.address_masked_jsonb == {
        "province_code": "320000",
        "province_name": "江苏省",
        "city_name": "南京市",
        "district_name": "鼓楼区",
        "detail_masked": "******",
    }
    assert request.contact_masked_jsonb == {
        "name_masked": "张***",
        "mobile_masked": "*******8000",
    }
    assert request.address_masked_jsonb == revision.address_masked_jsonb
    assert request.contact_masked_jsonb == revision.contact_masked_jsonb
    assert request.contact_snapshot_jsonb["provider"] == "aliyun_kms"
    assert set(request.contact_snapshot_jsonb) == {
        "schema",
        "provider",
        "kms_key_id",
        "key_version",
        "ciphertext_b64",
        "nonce_b64",
        "aad_sha256",
        "mobile_hmac",
        "contact_hmac",
    }
    assert request.allocation_status == "not_allocated"
    assert request.reservation_status == "not_reserved"
    assert request.outbound_status == "not_started"
    assert request.shipment_status == "not_started"
    assert request.logistics_signature_status == "not_signed"
    assert request.oam_receipt_status == "not_occurred"
    assert request.personal_inbound_status == "not_started"
    assert request.notification_status == "not_started"
    assert request.reconciliation_status == "not_started"

    commands = tuple(db.scalars(select(MaterialRequestCommand)).all())
    audits = tuple(db.scalars(select(AuditEvent)).all())
    transitions = tuple(db.scalars(select(StateTransitionEvent)).all())
    assert len(commands) == len(audits) == len(transitions) == 1
    assert commands[0].idempotency_key_hash != "same-create"
    assert len(commands[0].idempotency_key_hash) == 64
    persisted_evidence = json.dumps(
        {
            "command_request": commands[0].request_jsonb,
            "command_result": commands[0].result_jsonb,
            "audit_before": audits[0].before_jsonb,
            "audit_after": audits[0].after_jsonb,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "敏感测试地址" not in persisted_evidence
    assert "张***" not in persisted_evidence
    assert "*******8000" not in persisted_evidence
    assert request.contact_snapshot_jsonb["ciphertext_b64"] not in persisted_evidence
    assert "mobile" not in persisted_evidence.lower()
    assert audits[0].stream_key == "material_request"
    assert db.scalar(select(AuditChainHead.version).where(AuditChainHead.stream_key == "material_request")) == 1
    assert db.scalar(select(OutboxEvent.id)) is None
    assert db.scalar(select(InventoryTransaction.id)) is None


def test_create_same_key_conflicts_when_stable_contact_identity_changes(
    db: Session,
) -> None:
    world = make_world(db)
    request_id = _create_id(world, "contact-conflict")
    _create(db, world, request_id, key="contact-conflict")
    changed = dict(_contact_envelope(request_id, world.actor_person.id))
    changed["contact_hmac"] = f"hmac:1:{'d' * 64}"
    with pytest.raises(MaterialRequestDraftError) as failure:
        create_material_request_draft(
            db,
            actor=world.actor,
            material_request_id=request_id,
            draft=replace(_draft(world, request_id), contact_envelope=changed),
            idempotency_key="contact-conflict",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-contact-conflict",
        )
    _assert_error(failure, "material_request_idempotency_conflict", 409)


def test_create_id_is_stable_actor_scoped_hmac_coordinate(db: Session) -> None:
    world = make_world(db)
    first = _create_id(world, "stable-key")
    second = _create_id(world, "stable-key")
    other_key = _create_id(world, "different-key")
    other_actor = replace(world.actor, user_id=str(uuid.uuid4()))
    other_user = derive_material_request_create_id(
        actor=other_actor,
        idempotency_key="stable-key",
        idempotency_hmac_secret=SECRET,
    )
    assert first == second
    assert len({first, other_key, other_user}) == 3
    assert first.version == 4
    assert first.variant == uuid.RFC_4122
    assert "stable-key" not in str(first)
    assert SECRET.decode("ascii") not in str(first)

    injected = uuid.uuid4()
    with pytest.raises(MaterialRequestDraftError) as failure:
        create_material_request_draft(
            db,
            actor=world.actor,
            material_request_id=injected,
            draft=_draft(world, injected),
            idempotency_key="stable-key",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-injected-create-id",
        )
    _assert_error(failure, "material_request_create_id_invalid", 422)
    assert db.get(MaterialRequest, injected) is None


def test_amend_replaces_only_current_mutable_revision_and_replays_random_ciphertext(
    db: Session,
) -> None:
    world = make_world(db)
    request_id = _create_id(world)
    created = _create(db, world, request_id)
    amended_input = replace(
        _draft(world, request_id, material_index=1),
        purpose="更新后的用途",
        urgency="urgent",
    )
    amended = amend_material_request_draft(
        db,
        actor=world.actor,
        material_request_id=request_id,
        expected_version=created.request_version,
        draft=amended_input,
        idempotency_key="amend-1",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-amend-1",
    )
    randomized = dict(amended_input.contact_envelope)
    randomized.update(
        {
            "kms_key_id": "kms/rsc/material-request/rotated-amend-test",
            "key_version": 3,
            "ciphertext_b64": base64.b64encode(b"\x94" * 32).decode("ascii"),
            "nonce_b64": base64.b64encode(b"\x75" * 12).decode("ascii"),
        }
    )
    replay = amend_material_request_draft(
        db,
        actor=world.actor,
        material_request_id=request_id,
        expected_version=created.request_version,
        draft=replace(amended_input, contact_envelope=randomized),
        idempotency_key="amend-1",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-amend-replay",
    )
    assert amended.version == 1
    assert amended.revision_no == 1
    assert amended.revision_id == created.revision_id
    assert replay.replayed is True
    assert replace(replay, replayed=False) == amended
    assert amended.line_ids != created.line_ids
    request = db.get(MaterialRequest, request_id)
    revisions = tuple(
        db.scalars(
            select(MaterialRequestRevision).where(
                MaterialRequestRevision.request_id == request_id
            )
        ).all()
    )
    lines = tuple(
        db.scalars(
            select(MaterialRequestLine).where(
                MaterialRequestLine.request_id == request_id,
                MaterialRequestLine.revision_id == amended.revision_id,
            )
        ).all()
    )
    assert request.purpose == "更新后的用途"
    assert request.urgency == "urgent"
    assert len(revisions) == 1
    assert revisions[0].status == "draft"
    assert len(lines) == 1
    assert lines[0].material_id == world.materials[1].id
    assert len(tuple(db.scalars(select(MaterialRequestCommand)).all())) == 2
    assert len(tuple(db.scalars(select(AuditEvent)).all())) == 2
    assert len(tuple(db.scalars(select(StateTransitionEvent)).all())) == 1


def test_submit_lock_order_is_aggregate_then_parent_principal_and_children(
    db: Session,
) -> None:
    world = make_world(db)
    request_id = _create_id(world, "submit-lock-order")
    created = _create(db, world, request_id, key="submit-lock-order")
    order: list[str] = []
    advisory = draft_service._take_advisory_locks
    lock_request = draft_service._lock_request
    lock_principal = draft_service.lock_formal_principal_graph
    lock_revision = draft_service._lock_current_revision

    def record_advisory(*args, **kwargs):
        order.append("aggregate_advisory")
        return advisory(*args, **kwargs)

    def record_request(*args, **kwargs):
        order.append("parent_request")
        return lock_request(*args, **kwargs)

    def record_principal(*args, **kwargs):
        order.append("principal_graph")
        return lock_principal(*args, **kwargs)

    def record_revision(*args, **kwargs):
        order.append("child_graph")
        return lock_revision(*args, **kwargs)

    with patch.object(
        draft_service, "_take_advisory_locks", side_effect=record_advisory
    ), patch.object(
        draft_service, "_lock_request", side_effect=record_request
    ), patch.object(
        draft_service,
        "lock_formal_principal_graph",
        side_effect=record_principal,
    ), patch.object(
        draft_service, "_lock_current_revision", side_effect=record_revision
    ):
        submit_material_request(
            db,
            actor=world.actor,
            material_request_id=request_id,
            expected_version=created.request_version,
            idempotency_key="submit-lock-order-key-0001",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-submit-lock-order",
        )

    assert order[:4] == [
        "aggregate_advisory",
        "parent_request",
        "principal_graph",
        "child_graph",
    ]


def test_submit_rechecks_candidate_set_after_principal_locks(db: Session) -> None:
    world = make_world(db)
    request_id = _create_id(world, "submit-candidate-race")
    created = _create(db, world, request_id, key="submit-candidate-race")
    discover = draft_service._discover_candidate_user_ids
    calls = 0

    def changing_candidates(*args, **kwargs):
        nonlocal calls
        calls += 1
        rows = discover(*args, **kwargs)
        if calls == 2:
            return tuple((*rows, "concurrent-candidate"))
        return rows

    with patch.object(
        draft_service,
        "_discover_candidate_user_ids",
        side_effect=changing_candidates,
    ):
        with pytest.raises(MaterialRequestDraftError) as failure:
            submit_material_request(
                db,
                actor=world.actor,
                material_request_id=request_id,
                expected_version=created.request_version,
                idempotency_key="submit-candidate-race-key-0001",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-submit-candidate-race",
            )
    _assert_error(failure, "material_request_candidate_set_changed", 409)
    request = db.get(MaterialRequest, request_id)
    revision = db.get(MaterialRequestRevision, created.revision_id)
    assert request is not None and request.status == "draft"
    assert revision is not None and revision.status == "draft"
    assert db.scalar(
        select(ApprovalInstance.id).where(ApprovalInstance.request_id == request_id)
    ) is None


def test_submit_freezes_exact_three_stage_candidates_and_never_advances_other_axes(db: Session) -> None:
    world = make_world(db)
    request_id = _create_id(world)
    created = _create(db, world, request_id)
    with patch.object(db, "commit", side_effect=AssertionError("service committed")), patch.object(
        db, "rollback", side_effect=AssertionError("service rolled back")
    ):
        submitted = submit_material_request(
            db,
            actor=world.actor,
            material_request_id=request_id,
            expected_version=created.request_version,
            idempotency_key="submit-1",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-submit-1",
        )
        replay = submit_material_request(
            db,
            actor=world.actor,
            material_request_id=request_id,
            expected_version=created.request_version,
            idempotency_key="submit-1",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-submit-replay",
        )

    assert submitted.status == "approval_in_progress"
    assert submitted.revision_id == created.revision_id
    assert submitted.revision_no == 1
    assert submitted.approval_attempt_no == 1
    assert replay.replayed is True
    assert replace(replay, replayed=False) == submitted
    assert submitted.regional_approver_user_id == world.manager_users[0].id
    assert submitted.headquarters_candidate_user_ids == tuple(
        sorted(user.id for user in world.admin_users)
    )
    assert submitted.state_axes == {
        "allocation_status": "not_allocated",
        "reservation_status": "not_reserved",
        "outbound_status": "not_started",
        "shipment_status": "not_started",
        "logistics_signature_status": "not_signed",
        "oam_receipt_status": "not_occurred",
        "personal_inbound_status": "not_started",
        "notification_status": "not_started",
        "reconciliation_status": "not_started",
    }
    instance = db.get(ApprovalInstance, submitted.approval_instance_id)
    revision = db.get(MaterialRequestRevision, submitted.revision_id)
    steps = tuple(
        db.scalars(
            select(ApprovalStep)
            .where(ApprovalStep.instance_id == instance.id)
            .order_by(ApprovalStep.step_no)
        ).all()
    )
    assert instance.status == "active"
    assert instance.request_revision_id == revision.id
    assert instance.revision_no == revision.revision_no
    assert instance.attempt_no == 1
    assert revision.status == "sealed"
    assert revision.content_manifest_sha256 is not None
    assert len(revision.content_manifest_sha256) == 64
    assert revision.sealed_by_user_id == world.actor_user.id
    assert instance.current_step_no == 1
    assert [(row.step_no, row.status, row.source_mode) for row in steps] == [
        (1, "open", "internal"),
        (2, "pending", "internal"),
        (3, "pending", "external_registration"),
    ]
    candidates = tuple(
        db.scalars(
            select(ApprovalStepCandidate).order_by(
                ApprovalStepCandidate.step_id,
                ApprovalStepCandidate.user_id,
                ApprovalStepCandidate.candidate_kind,
            )
        ).all()
    )
    assert len(candidates) == 1 + 2 + 4
    assert all(row.user_id != world.actor_user.id for row in candidates)
    assert len(tuple(db.scalars(select(ApprovalAction)).all())) == 1
    assert len(tuple(db.scalars(select(MaterialRequestCommand)).all())) == 2
    assert len(tuple(db.scalars(select(AuditEvent)).all())) == 2
    transitions = tuple(
        db.scalars(
            select(StateTransitionEvent).order_by(StateTransitionEvent.created_at)
        ).all()
    )
    assert [(row.from_status, row.to_status) for row in transitions] == [
        (None, "draft"),
        ("draft", "submitted"),
        ("submitted", "approval_in_progress"),
    ]

    original_line_ids = tuple(
        db.scalars(
            select(MaterialRequestLine.id).where(MaterialRequestLine.request_id == request_id)
        ).all()
    )
    original_file_ids = tuple(
        db.scalars(
            select(MaterialRequestFile.id).where(MaterialRequestFile.request_id == request_id)
        ).all()
    )
    with pytest.raises(MaterialRequestDraftError) as frozen:
        amend_material_request_draft(
            db,
            actor=world.actor,
            material_request_id=request_id,
            expected_version=submitted.version,
            draft=_draft(world, request_id, material_index=1),
            idempotency_key="amend-after-submit",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-amend-after-submit",
        )
    _assert_error(frozen, "material_request_not_editable_draft", 409)
    assert tuple(
        db.scalars(
            select(MaterialRequestLine.id).where(MaterialRequestLine.request_id == request_id)
        ).all()
    ) == original_line_ids
    assert tuple(
        db.scalars(
            select(MaterialRequestFile.id).where(MaterialRequestFile.request_id == request_id)
        ).all()
    ) == original_file_ids


@pytest.mark.parametrize(
    ("manager_count", "expected_code", "expected_status"),
    (
        (0, "request_region_approver_unavailable", 412),
        (2, "request_region_approver_ambiguous", 409),
    ),
)
def test_submit_fails_closed_for_missing_or_ambiguous_regional_manager(
    db: Session,
    manager_count: int,
    expected_code: str,
    expected_status: int,
) -> None:
    world = make_world(db, manager_count=manager_count)
    request_id = _create_id(world)
    created = _create(db, world, request_id)
    with pytest.raises(MaterialRequestDraftError) as failure:
        submit_material_request(
            db,
            actor=world.actor,
            material_request_id=request_id,
            expected_version=created.request_version,
            idempotency_key="submit-manager-gate",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-submit-manager-gate",
        )
    _assert_error(failure, expected_code, expected_status)
    request = db.get(MaterialRequest, request_id)
    assert request.status == "draft"
    assert request.submitted_at is None
    assert db.scalar(select(ApprovalInstance.id)) is None
    assert len(tuple(db.scalars(select(MaterialRequestCommand)).all())) == 1


def test_submit_requires_two_non_requester_headquarters_admins(db: Session) -> None:
    world = make_world(db, admin_count=1)
    request_id = _create_id(world)
    created = _create(db, world, request_id)
    with pytest.raises(MaterialRequestDraftError) as failure:
        submit_material_request(
            db,
            actor=world.actor,
            material_request_id=request_id,
            expected_version=created.request_version,
            idempotency_key="submit-admin-gate",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-submit-admin-gate",
        )
    _assert_error(
        failure,
        "request_external_registration_reviewer_pool_insufficient",
        412,
    )
    assert db.get(MaterialRequest, request_id).status == "draft"
    assert db.scalar(select(ApprovalInstance.id)) is None


def test_cross_person_scope_and_stale_principal_fail_before_mutation(db: Session) -> None:
    world = make_world(db)
    request_id = _create_id(world)
    created = _create(db, world, request_id)
    other_person = _person(db, world.department, "其他工程师")
    other_user = _user(db, other_person)
    _assignment(
        db,
        other_user,
        world.roles["technician"],
        scope_type="person",
        scope_id=str(other_person.id),
        assigned_by=world.actor_user.id,
    )
    db.commit()
    other = load_formal_principal(db, other_user.id, now=NOW)
    with pytest.raises(MaterialRequestDraftError) as forbidden:
        amend_material_request_draft(
            db,
            actor=other,
            material_request_id=request_id,
            expected_version=created.request_version,
            draft=replace(
                _draft(world, request_id),
                contact_envelope=_contact_envelope(request_id, other_person.id),
            ),
            idempotency_key="other-amend",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-other-amend",
        )
    _assert_error(forbidden, "material_request_owner_forbidden", 403)
    assert db.get(MaterialRequest, request_id).version == 0

    stale = replace(world.actor, authorization_version=world.actor.authorization_version - 1)
    new_request_id = derive_material_request_create_id(
        actor=stale,
        idempotency_key="stale-create",
        idempotency_hmac_secret=SECRET,
    )
    with pytest.raises(MaterialRequestDraftError) as stale_failure:
        create_material_request_draft(
            db,
            actor=stale,
            material_request_id=new_request_id,
            draft=_draft(world, new_request_id),
            idempotency_key="stale-create",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-stale-create",
        )
    _assert_error(
        stale_failure,
        "material_request_actor_principal_stale",
        412,
    )
    assert db.get(MaterialRequest, new_request_id) is None


def test_plaintext_or_wrongly_bound_contact_is_rejected(db: Session) -> None:
    world = make_world(db)
    request_id = _create_id(world, "contact-plaintext")
    plaintext = dict(_contact_envelope(request_id, world.actor_person.id))
    plaintext["mobile"] = "13800138000"
    with pytest.raises(MaterialRequestDraftError) as shape_failure:
        create_material_request_draft(
            db,
            actor=world.actor,
            material_request_id=request_id,
            draft=replace(_draft(world, request_id), contact_envelope=plaintext),
            idempotency_key="contact-plaintext",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-contact-plaintext",
        )
    _assert_error(shape_failure, "material_request_contact_envelope_invalid", 422)
    assert db.get(MaterialRequest, request_id) is None

    other_id = _create_id(world, "contact-binding")
    with pytest.raises(MaterialRequestDraftError) as binding_failure:
        create_material_request_draft(
            db,
            actor=world.actor,
            material_request_id=other_id,
            draft=replace(
                _draft(world, other_id),
                contact_envelope=_contact_envelope(uuid.uuid4(), world.actor_person.id),
            ),
            idempotency_key="contact-binding",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-contact-binding",
        )
    _assert_error(binding_failure, "material_request_contact_binding_invalid", 422)
    assert db.get(MaterialRequest, other_id) is None

    masked_id = _create_id(world, "contact-mask-plaintext")
    with pytest.raises(MaterialRequestDraftError) as masked_failure:
        create_material_request_draft(
            db,
            actor=world.actor,
            material_request_id=masked_id,
            draft=replace(
                _draft(world, masked_id),
                contact_masked={
                    "name_masked": "张工程师",
                    "mobile_masked": "13800138000",
                },
            ),
            idempotency_key="contact-mask-plaintext",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-contact-mask-plaintext",
        )
    _assert_error(masked_failure, "material_request_contact_masked_invalid", 422)
    assert db.get(MaterialRequest, masked_id) is None


def test_returned_amend_creates_linked_revision_and_resubmit_supersedes_attempt(
    db: Session,
) -> None:
    world = make_world(db)
    request_id = _create_id(world, "returned-chain")
    created = _create(db, world, request_id, key="returned-chain")
    first_submit = submit_material_request(
        db,
        actor=world.actor,
        material_request_id=request_id,
        expected_version=created.request_version,
        idempotency_key="returned-submit-1",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-returned-submit-1",
    )
    request = db.get(MaterialRequest, request_id)
    old_revision = db.get(MaterialRequestRevision, first_submit.revision_id)
    old_instance = db.get(ApprovalInstance, first_submit.approval_instance_id)
    first_step = db.get(ApprovalStep, first_submit.approval_step_ids[0])
    returned_at = NOW + timedelta(hours=1)
    first_step.status = "returned"
    first_step.decided_at = returned_at
    first_step.updated_at = returned_at
    first_step.version += 1
    old_instance.status = "returned"
    old_instance.current_step_no = None
    old_instance.current_step_id = None
    old_instance.updated_at = returned_at
    old_instance.version += 1
    request.status = "returned"
    request.updated_at = returned_at
    request.version += 1
    db.flush()

    old_header_evidence = json.dumps(
        {
            "purpose": old_revision.purpose,
            "address": old_revision.address_snapshot_jsonb,
            "address_masked": old_revision.address_masked_jsonb,
            "contact": old_revision.contact_snapshot_jsonb,
            "contact_masked": old_revision.contact_masked_jsonb,
            "manifest": old_revision.content_manifest_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    old_line_ids = tuple(
        db.scalars(
            select(MaterialRequestLine.id)
            .where(MaterialRequestLine.revision_id == old_revision.id)
            .order_by(MaterialRequestLine.line_no)
        ).all()
    )
    old_file_ids = tuple(
        db.scalars(
            select(MaterialRequestFile.id)
            .where(MaterialRequestFile.revision_id == old_revision.id)
            .order_by(MaterialRequestFile.id)
        ).all()
    )

    supplement = replace(
        _draft(world, request_id, material_index=1),
        purpose="退回后补充用途与证明",
        urgency="urgent",
    )
    amended = amend_material_request_draft(
        db,
        actor=world.actor,
        material_request_id=request_id,
        expected_version=request.version,
        draft=supplement,
        idempotency_key="returned-amend-2",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-returned-amend-2",
    )
    randomized = dict(supplement.contact_envelope)
    randomized.update(
        {
            "kms_key_id": "kms/rsc/material-request/returned-rotated",
            "key_version": 8,
            "ciphertext_b64": base64.b64encode(b"\x98" * 32).decode("ascii"),
            "nonce_b64": base64.b64encode(b"\x79" * 12).decode("ascii"),
        }
    )
    replay = amend_material_request_draft(
        db,
        actor=world.actor,
        material_request_id=request_id,
        expected_version=request.version - 1,
        draft=replace(supplement, contact_envelope=randomized),
        idempotency_key="returned-amend-2",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-returned-amend-2-replay",
    )
    assert replay.replayed is True
    assert replace(replay, replayed=False) == amended
    assert amended.status == "returned"
    assert amended.revision_no == 2
    assert amended.revision_id != old_revision.id
    new_revision = db.get(MaterialRequestRevision, amended.revision_id)
    assert new_revision.previous_revision_id == old_revision.id
    assert new_revision.status == "draft"
    assert request.revision_no == 2
    assert old_instance.status == "returned"
    assert tuple(
        db.scalars(
            select(MaterialRequestRevision.revision_no)
            .where(MaterialRequestRevision.request_id == request_id)
            .order_by(MaterialRequestRevision.revision_no)
        ).all()
    ) == (1, 2)

    second_submit = submit_material_request(
        db,
        actor=world.actor,
        material_request_id=request_id,
        expected_version=amended.version,
        idempotency_key="returned-submit-2",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-returned-submit-2",
    )
    second_replay = submit_material_request(
        db,
        actor=world.actor,
        material_request_id=request_id,
        expected_version=amended.version,
        idempotency_key="returned-submit-2",
        idempotency_hmac_secret=SECRET,
        trace_request_id="trace-returned-submit-2-replay",
    )
    assert second_replay.replayed is True
    assert replace(second_replay, replayed=False) == second_submit
    assert second_submit.revision_id == new_revision.id
    assert second_submit.revision_no == 2
    assert second_submit.approval_attempt_no == 2
    assert old_instance.status == "superseded"
    assert old_instance.current_step_no is None
    second_instance = db.get(ApprovalInstance, second_submit.approval_instance_id)
    assert second_instance.request_revision_id == new_revision.id
    assert second_instance.attempt_no == 2
    assert second_instance.status == "active"
    second_steps = tuple(
        db.scalars(
            select(ApprovalStep)
            .where(ApprovalStep.instance_id == second_instance.id)
            .order_by(ApprovalStep.step_no)
        ).all()
    )
    assert tuple(step.attempt_no for step in second_steps) == (1, 1, 1)
    assert new_revision.status == "sealed"

    db.expire(old_revision)
    assert json.dumps(
        {
            "purpose": old_revision.purpose,
            "address": old_revision.address_snapshot_jsonb,
            "address_masked": old_revision.address_masked_jsonb,
            "contact": old_revision.contact_snapshot_jsonb,
            "contact_masked": old_revision.contact_masked_jsonb,
            "manifest": old_revision.content_manifest_sha256,
        },
        ensure_ascii=False,
        sort_keys=True,
    ) == old_header_evidence
    assert tuple(
        db.scalars(
            select(MaterialRequestLine.id)
            .where(MaterialRequestLine.revision_id == old_revision.id)
            .order_by(MaterialRequestLine.line_no)
        ).all()
    ) == old_line_ids
    assert tuple(
        db.scalars(
            select(MaterialRequestFile.id)
            .where(MaterialRequestFile.revision_id == old_revision.id)
            .order_by(MaterialRequestFile.id)
        ).all()
    ) == old_file_ids
    persisted_evidence = json.dumps(
        {
            "commands": [
                {"request": row.request_jsonb, "result": row.result_jsonb}
                for row in db.scalars(select(MaterialRequestCommand)).all()
            ],
            "audits": [
                {"before": row.before_jsonb, "after": row.after_jsonb}
                for row in db.scalars(select(AuditEvent)).all()
            ],
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    assert "敏感测试地址" not in persisted_evidence
    assert "张***" not in persisted_evidence
    assert "*******8000" not in persisted_evidence
    assert old_revision.contact_snapshot_jsonb["ciphertext_b64"] not in persisted_evidence
    assert new_revision.contact_snapshot_jsonb["ciphertext_b64"] not in persisted_evidence
    assert db.scalar(select(OutboxEvent.id)) is None
    assert db.scalar(select(InventoryTransaction.id)) is None


def test_missing_current_revision_coordinate_fails_closed(db: Session) -> None:
    world = make_world(db)
    request_id = _create_id(world, "missing-current-revision")
    created = _create(db, world, request_id, key="missing-current-revision")
    request = db.get(MaterialRequest, request_id)
    request.revision_no = 99
    db.flush()
    with pytest.raises(MaterialRequestDraftError) as failure:
        submit_material_request(
            db,
            actor=world.actor,
            material_request_id=request_id,
            expected_version=created.request_version,
            idempotency_key="missing-current-submit",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-missing-current-submit",
        )
    _assert_error(
        failure,
        "material_request_current_revision_invalid",
        503,
    )


def test_submit_requires_exact_fixed_external_registration_route(db: Session) -> None:
    world = make_world(db)
    request_id = _create_id(world)
    created = _create(db, world, request_id)
    route = db.scalar(select(ApprovalRouteVersion))
    route.approval_mode = "direct_star"
    db.flush()
    with pytest.raises(MaterialRequestDraftError) as failure:
        submit_material_request(
            db,
            actor=world.actor,
            material_request_id=request_id,
            expected_version=created.request_version,
            idempotency_key="submit-route-gate",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-submit-route-gate",
        )
    _assert_error(failure, "material_request_route_unavailable", 412)
    assert db.get(MaterialRequest, request_id).status == "draft"
    assert db.scalar(select(ApprovalInstance.id)) is None
