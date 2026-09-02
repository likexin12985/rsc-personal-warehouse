from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
from types import SimpleNamespace
import uuid

import pytest
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import Session

from app import oam_work_order_projector as projector
from app.database import Base
from app.demand_models import OamWorkOrder
from app.external_sync_scope_lock import external_sync_scope_lock_key
from app.formal_services import material_request_draft
from app.formal_services import oam_work_order_projection as service
from app.foundation_models import (
    ExternalObject,
    ExternalObjectMapping,
    ExternalObjectVersion,
    Organization,
    Person,
    SourceSystem,
    SyncBatch,
    SyncConflict,
    SyncInboxEvent,
    SyncRun,
)
from app.models import (
    ExternalSyncCurrentRecord,
    ExternalSyncSnapshot,
    ExternalSyncSnapshotBatch,
    ExternalSyncSnapshotRecord,
    User,
)
from app.schemas import EdgeSyncSnapshotCompleteIn


SOURCE_INSTANCE = "edge-oam-work-order-test"
SCOPE_KEY = "work-orders:recent-30d"
COMPANY_ID = "nio-company-id"
ORG_CODE = "nio-org-code"
SOURCE_TIME = datetime(2026, 9, 2, 1, 0, tzinfo=timezone.utc)
SNAPSHOT_TIME = SOURCE_TIME + timedelta(minutes=2)
COMPLETED_TIME = SOURCE_TIME + timedelta(minutes=3)
PUBLISHED_TIME = SOURCE_TIME + timedelta(minutes=4)


def test_external_sync_scope_lock_key_is_unambiguous():
    assert external_sync_scope_lock_key("a:b", "c") != external_sync_scope_lock_key(
        "a", "b:c"
    )


@pytest.fixture()
def db() -> Session:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = Session(engine, autoflush=False, expire_on_commit=False)
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _canonical(value) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _sha(value) -> str:
    return hashlib.sha256(_canonical(value).encode("utf-8")).hexdigest()


def _source(db: Session) -> SourceSystem:
    row = SourceSystem(
        code=service.SOURCE_SYSTEM_CODE,
        name="StarCharge OAM",
        mode="read_only",
        enabled=True,
        configuration_jsonb={
            "projection_schema": service.PROJECTION_SCHEMA,
            "edge_source_instance": SOURCE_INSTANCE,
            "work_order_company_id": COMPANY_ID,
            "work_order_org_code": ORG_CODE,
            "work_order_scope_key": SCOPE_KEY,
        },
        created_at=SOURCE_TIME,
        updated_at=SOURCE_TIME,
    )
    db.add(row)
    db.flush()
    return row


def _person_mapping(
    db: Session,
    source: SourceSystem,
    *,
    executor_id: str = "oam-account-001",
) -> tuple[Organization, Person, ExternalObject]:
    organization = Organization(
        code=ORG_CODE,
        name="浙江区域工程部门",
        parent_id=None,
        org_type="department",
        province_code="CN330000",
        status="active",
        created_at=SOURCE_TIME,
        updated_at=SOURCE_TIME,
    )
    db.add(organization)
    db.flush()
    person = Person(
        external_object_id=None,
        organization_id=organization.id,
        employee_no=f"EMP-{uuid.uuid4().hex[:8]}",
        name="正式映射工程师",
        mobile_encrypted=None,
        mobile_hash=None,
        employment_status="active",
        source_updated_at=SOURCE_TIME,
        created_at=SOURCE_TIME,
        updated_at=SOURCE_TIME,
    )
    db.add(person)
    version_id = uuid.uuid4()
    employee = ExternalObject(
        source_system_id=source.id,
        entity_type="employee",
        external_id=executor_id,
        current_version_id=version_id,
        deleted_at=None,
        created_at=SOURCE_TIME,
        updated_at=SOURCE_TIME,
    )
    db.add(employee)
    db.flush()
    employee_payload = {
        "accountId": executor_id,
        "companyId": COMPANY_ID,
        "orgCode": ORG_CODE,
        "status": "active",
    }
    db.add(
        ExternalObjectVersion(
            id=version_id,
            external_object_id=employee.id,
            source_version="employee-v1",
            source_updated_at=SOURCE_TIME,
            valid_from=SOURCE_TIME,
            valid_to=None,
            payload_jsonb=employee_payload,
            payload_sha256=_sha(employee_payload),
            is_current=True,
            created_at=SOURCE_TIME,
        )
    )
    approver = User(
        id=str(uuid.uuid4()),
        person_id=None,
        account_status="active",
        last_login_at=None,
        authorization_version=1,
        mobile=f"1{uuid.uuid4().int % 10**10:010d}",
        name="映射审批管理员",
        password_hash="not-used-in-projection-tests",
        role="admin",
        province=None,
        is_active=True,
        require_password_change=False,
        created_at=SOURCE_TIME,
        updated_at=SOURCE_TIME,
    )
    db.add(approver)
    db.flush()
    db.add(
        ExternalObjectMapping(
            external_object_id=employee.id,
            local_object_type="person",
            local_object_id=str(person.id),
            status="approved",
            approved_by=approver.id,
            approved_at=SOURCE_TIME,
            reason="测试中的显式人员映射",
            created_at=SOURCE_TIME,
            updated_at=SOURCE_TIME,
        )
    )
    db.flush()
    return organization, person, employee


def _work_order_payload(
    *,
    status: str = "processing",
    executor_id: str = "oam-account-001",
    source_id: str = "oam-work-order-id-001",
    code: str = "WT-NIO-001",
    source_time: datetime = SOURCE_TIME,
    **extra,
) -> dict[str, object]:
    payload: dict[str, object] = {
        "id": source_id,
        "code": code,
        "statusCode": status,
        "executorId": executor_id,
        "authCompanyId": COMPANY_ID,
        "province": "浙江省",
        "updateTime": source_time.astimezone(
            timezone(timedelta(hours=8))
        ).strftime("%Y-%m-%d %H:%M:%S"),
    }
    payload.update(extra)
    return payload


def _stage_snapshot(
    db: Session,
    *,
    payloads: tuple[dict[str, object], ...],
    snapshot_id: str,
    snapshot_time: datetime = SNAPSHOT_TIME,
    completed_time: datetime = COMPLETED_TIME,
) -> ExternalSyncSnapshot:
    snapshot = ExternalSyncSnapshot(
        source_system="starcharge_oam",
        source_instance=SOURCE_INSTANCE,
        snapshot_id=snapshot_id,
        scope_key=SCOPE_KEY,
        sync_mode="full",
        company_id=COMPANY_ID,
        org_code=ORG_CODE,
        snapshot_at=snapshot_time,
        status="complete",
        manifest_json="pending",
        manifest_sha256="a" * 64,
        received_at=snapshot_time,
        completed_at=completed_time,
    )
    db.add(snapshot)
    db.flush()
    final_records: list[dict[str, object]] = []
    delta_records: list[dict[str, object]] = []
    existing_current = {
        row.business_key: row
        for row in db.scalars(
            select(ExternalSyncCurrentRecord).where(
                ExternalSyncCurrentRecord.source_instance == SOURCE_INSTANCE,
                ExternalSyncCurrentRecord.scope_key == SCOPE_KEY,
                ExternalSyncCurrentRecord.entity_type == "work_order",
            )
        ).all()
    }
    incoming_keys = {f"work-order:{payload['code']}" for payload in payloads}
    for business_key, current in existing_current.items():
        if business_key not in incoming_keys:
            db.delete(current)
    if payloads:
        batch = ExternalSyncSnapshotBatch(
            snapshot_ref_id=snapshot.id,
            source_instance=SOURCE_INSTANCE,
            batch_id=f"batch-{snapshot_id}",
            entity_type="work_order",
            sequence=1,
            total_sequences=1,
            record_count=len(payloads),
            body_sha256="b" * 64,
            received_at=snapshot_time,
        )
        db.add(batch)
    for payload in payloads:
        code = str(payload["code"])
        business_key = f"work-order:{code}"
        raw = _canonical(payload)
        raw_hash = hashlib.sha256(raw.encode("utf-8")).hexdigest()
        source_updated_at = service._parse_oam_source_time(payload["updateTime"])
        db.add(
            ExternalSyncSnapshotRecord(
                snapshot_ref_id=snapshot.id,
                entity_type="work_order",
                business_key=business_key,
                operation="upsert",
                source_updated_at=source_updated_at,
                payload_json=raw,
                payload_sha256=raw_hash,
            )
        )
        current = existing_current.get(business_key)
        if current is None:
            db.add(ExternalSyncCurrentRecord(
                source_system="starcharge_oam",
                source_instance=SOURCE_INSTANCE,
                scope_key=SCOPE_KEY,
                entity_type="work_order",
                business_key=business_key,
                source_updated_at=source_updated_at,
                payload_json=raw,
                payload_sha256=raw_hash,
                last_snapshot_id=snapshot.id,
                created_at=snapshot_time,
                updated_at=snapshot_time,
            ))
        else:
            current.source_updated_at = source_updated_at
            current.payload_json = raw
            current.payload_sha256 = raw_hash
            current.last_snapshot_id = snapshot.id
            current.updated_at = snapshot_time
        final_record = {
            "business_key": business_key,
            "source_updated_at": source_updated_at.isoformat(),
            "data": payload,
        }
        final_records.append(final_record)
        delta_records.append({**final_record, "operation": "upsert"})
    manifest = EdgeSyncSnapshotCompleteIn(
        snapshot_id=snapshot_id,
        scope_key=SCOPE_KEY,
        sync_mode="full",
        company_id=COMPANY_ID,
        org_code=ORG_CODE,
        snapshot_at=snapshot_time,
        entities=[
            {
                "entity_type": "work_order",
                "final_record_count": len(final_records),
                "final_sha256": _sha(final_records),
                "delta_record_count": len(delta_records),
                "delta_sha256": _sha(delta_records),
                "batch_count": 1 if payloads else 0,
            },
        ],
    )
    snapshot.manifest_json = _canonical(manifest.model_dump(mode="json"))
    snapshot.manifest_sha256 = hashlib.sha256(
        snapshot.manifest_json.encode("utf-8")
    ).hexdigest()
    db.flush()
    return snapshot


def test_publisher_creates_complete_formal_provenance_chain(db: Session):
    source = _source(db)
    organization, person, _ = _person_mapping(db, source)
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-0001",
    )

    result = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()

    assert result.status == "completed"
    assert result.projected_records == 1
    assert result.created_records == 1
    run = db.get(SyncRun, result.sync_run_id)
    assert run is not None and run.status == "completed"
    batch = db.scalar(select(SyncBatch).where(SyncBatch.run_id == run.id))
    assert batch is not None and batch.status == "applied"
    event = db.scalar(select(SyncInboxEvent).where(SyncInboxEvent.batch_id == batch.id))
    assert event is not None and event.status == "applied"
    assert set(event.payload_jsonb) == set(service.WORK_ORDER_PAYLOAD_FIELDS)
    row = db.scalar(select(OamWorkOrder))
    assert row is not None
    assert row.work_order_no == "WT-NIO-001"
    assert row.organization_id == organization.id
    assert row.engineer_person_id == person.id
    assert row.status == "active"
    assert service._aware(row.source_updated_at) == SOURCE_TIME
    assert service._aware(row.updated_at) == PUBLISHED_TIME
    external = db.get(ExternalObject, row.external_object_id)
    assert external is not None
    assert external.external_id == "oam-work-order-id-001"
    version = db.get(ExternalObjectVersion, external.current_version_id)
    assert version is not None and version.is_current is True
    assert version.payload_jsonb == material_request_draft.material_request_work_order_projection_payload(row)
    assert version.payload_sha256 == _sha(version.payload_jsonb)
    evidence = material_request_draft.require_material_request_work_order_evidence(
        db,
        row,
        now=PUBLISHED_TIME,
    )
    assert evidence.source_system_code == "starcharge_oam"
    assert evidence.freshness_status == "fresh"


def test_duplicate_publish_is_idempotent_and_does_not_refresh_time(db: Session):
    source = _source(db)
    _person_mapping(db, source)
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-0002",
    )
    first = service.publish_completed_work_order_snapshot(
        db, snapshot_id=snapshot.id, now=PUBLISHED_TIME
    )
    db.flush()
    row = db.scalar(select(OamWorkOrder))
    assert row is not None

    replay = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=1),
    )
    db.flush()

    assert replay.sync_run_id == first.sync_run_id
    assert replay.duplicate is True
    assert service._aware(row.updated_at) == PUBLISHED_TIME
    assert db.scalar(select(func.count()).select_from(ExternalObjectVersion).where(
        ExternalObjectVersion.external_object_id == row.external_object_id
    )) == 1


def test_completed_duplicate_does_not_reapply_current_mapping_policy(db: Session):
    source = _source(db)
    _, _, employee = _person_mapping(db, source)
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-duplicate-mapping-policy",
    )
    first = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()
    mapping = db.scalar(
        select(ExternalObjectMapping).where(
            ExternalObjectMapping.external_object_id == employee.id
        )
    )
    assert mapping is not None
    mapping.updated_at = PUBLISHED_TIME + timedelta(seconds=1)
    db.flush()

    replay = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=1),
    )

    assert replay.sync_run_id == first.sync_run_id
    assert replay.duplicate is True


def test_completed_duplicate_accepts_valid_legacy_v1_projection_evidence(db: Session):
    source = _source(db)
    _person_mapping(db, source)
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-duplicate-v1-evidence",
    )
    first = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()
    event = db.scalar(select(SyncInboxEvent))
    row = db.scalar(select(OamWorkOrder))
    assert event is not None and row is not None
    external = db.get(ExternalObject, row.external_object_id)
    assert external is not None
    current = db.get(ExternalObjectVersion, external.current_version_id)
    assert current is not None
    current.source_version = event.source_version
    db.flush()

    replay = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=1),
    )

    assert replay.sync_run_id == first.sync_run_id
    assert replay.duplicate is True


@pytest.mark.parametrize("missing_field", ("approved_by", "approved_at"))
def test_person_mapping_requires_complete_approval_evidence(
    db: Session,
    missing_field: str,
):
    source = _source(db)
    _, _, employee = _person_mapping(db, source)
    mapping = db.scalar(
        select(ExternalObjectMapping).where(
            ExternalObjectMapping.external_object_id == employee.id
        )
    )
    assert mapping is not None
    setattr(mapping, missing_field, None)
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id=f"snapshot-work-order-mapping-approval-{missing_field}",
    )

    result = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()

    assert result.status == "conflict"
    assert db.scalar(select(OamWorkOrder)) is None
    conflict = db.scalar(
        select(SyncConflict).where(
            SyncConflict.conflict_type
            == "oam_work_order_person_mapping_approval_invalid"
        )
    )
    assert conflict is not None


@pytest.mark.parametrize("future_field", ("approved_at", "updated_at"))
def test_person_mapping_rejects_future_approval_evidence(
    db: Session,
    future_field: str,
):
    source = _source(db)
    _, _, employee = _person_mapping(db, source)
    mapping = db.scalar(
        select(ExternalObjectMapping).where(
            ExternalObjectMapping.external_object_id == employee.id
        )
    )
    assert mapping is not None
    future_time = PUBLISHED_TIME + timedelta(days=1)
    if future_field == "approved_at":
        mapping.approved_at = future_time
        mapping.updated_at = future_time
    else:
        mapping.updated_at = future_time
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id=f"snapshot-work-order-future-{future_field}",
    )

    result = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()

    assert result.status == "conflict"
    assert db.scalar(select(OamWorkOrder)) is None
    conflict = db.scalar(
        select(SyncConflict).where(
            SyncConflict.conflict_type
            == "oam_work_order_person_mapping_approval_invalid"
        )
    )
    assert conflict is not None and conflict.status == "open"


def test_person_source_company_and_org_must_match_snapshot_scope(db: Session):
    source = _source(db)
    _, _, employee = _person_mapping(db, source)
    version = db.get(ExternalObjectVersion, employee.current_version_id)
    assert version is not None
    version.payload_jsonb = {**version.payload_jsonb, "orgCode": "other-org"}
    version.payload_sha256 = _sha(version.payload_jsonb)
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-person-source-scope",
    )

    result = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()

    assert result.status == "conflict"
    conflict = db.scalar(select(SyncConflict))
    assert conflict is not None
    assert conflict.conflict_type == "oam_work_order_person_source_invalid"


def test_mapped_person_must_remain_under_configured_org_root(db: Session):
    source = _source(db)
    organization, _, _ = _person_mapping(db, source)
    organization.code = "unrelated-org-root"
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-person-local-scope",
    )

    result = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()

    assert result.status == "conflict"
    conflict = db.scalar(select(SyncConflict))
    assert conflict is not None
    assert conflict.conflict_type == "oam_work_order_person_scope_mismatch"


def test_approved_mapping_change_reprojects_same_raw_oam_version(db: Session):
    source = _source(db)
    first_organization, first_person, employee = _person_mapping(db, source)
    first_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-remap-0001",
    )
    service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=first_snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()
    row = db.scalar(select(OamWorkOrder))
    assert row is not None
    external = db.get(ExternalObject, row.external_object_id)
    assert external is not None
    first_version = db.get(ExternalObjectVersion, external.current_version_id)
    assert first_version is not None
    first_projection_version = first_version.source_version

    remapped_at = PUBLISHED_TIME + timedelta(minutes=5)
    first_mapping = db.scalar(
        select(ExternalObjectMapping).where(
            ExternalObjectMapping.external_object_id == employee.id,
            ExternalObjectMapping.status == "approved",
        )
    )
    assert first_mapping is not None
    first_mapping.status = "superseded"
    first_mapping.updated_at = remapped_at
    second_organization = Organization(
        code=f"ORG-{uuid.uuid4().hex[:8]}",
        name="上海区域工程部门",
        parent_id=first_organization.id,
        org_type="department",
        province_code="CN310000",
        status="active",
        created_at=remapped_at,
        updated_at=remapped_at,
    )
    db.add(second_organization)
    db.flush()
    second_person = Person(
        external_object_id=None,
        organization_id=second_organization.id,
        employee_no=f"EMP-{uuid.uuid4().hex[:8]}",
        name="重新审批映射工程师",
        mobile_encrypted=None,
        mobile_hash=None,
        employment_status="active",
        source_updated_at=remapped_at,
        created_at=remapped_at,
        updated_at=remapped_at,
    )
    second_approver = User(
        id=str(uuid.uuid4()),
        person_id=None,
        account_status="active",
        last_login_at=None,
        authorization_version=1,
        mobile=f"1{uuid.uuid4().int % 10**10:010d}",
        name="重新映射审批管理员",
        password_hash="not-used-in-projection-tests",
        role="admin",
        province=None,
        is_active=True,
        require_password_change=False,
        created_at=remapped_at,
        updated_at=remapped_at,
    )
    db.add_all((second_person, second_approver))
    db.flush()
    db.add(
        ExternalObjectMapping(
            external_object_id=employee.id,
            local_object_type="person",
            local_object_id=str(second_person.id),
            status="approved",
            approved_by=second_approver.id,
            approved_at=remapped_at,
            reason="人工审批后更换正式人员映射",
            created_at=remapped_at,
            updated_at=remapped_at,
        )
    )
    db.flush()

    second_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-remap-0002",
        snapshot_time=SNAPSHOT_TIME + timedelta(minutes=20),
        completed_time=COMPLETED_TIME + timedelta(minutes=20),
    )
    result = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=second_snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=20),
    )
    db.flush()

    assert result.status == "completed"
    assert result.updated_records == 1
    assert row.organization_id == second_organization.id
    assert row.engineer_person_id == second_person.id
    assert row.organization_id != first_organization.id
    assert row.engineer_person_id != first_person.id
    assert service._aware(row.source_updated_at) == SOURCE_TIME
    current = db.get(ExternalObjectVersion, external.current_version_id)
    assert current is not None
    assert current.source_version.startswith(service.PROJECTION_SOURCE_VERSION_PREFIX)
    assert current.source_version != first_projection_version
    assert current.source_version.split(":")[1] == first_projection_version.split(":")[1]
    assert first_version.is_current is False
    assert service._aware(first_version.valid_to) == PUBLISHED_TIME + timedelta(minutes=20)
    assert db.scalar(
        select(func.count())
        .select_from(ExternalObjectVersion)
        .where(ExternalObjectVersion.external_object_id == external.id)
    ) == 2


def test_completed_snapshot_replay_remains_idempotent_after_newer_publish(
    db: Session,
):
    source = _source(db)
    _person_mapping(db, source)
    first_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-replay-history-0001",
    )
    first = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=first_snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()
    newer_source_time = SOURCE_TIME + timedelta(minutes=10)
    newer_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(source_time=newer_source_time),),
        snapshot_id="snapshot-work-order-replay-history-0002",
        snapshot_time=SNAPSHOT_TIME + timedelta(minutes=20),
        completed_time=COMPLETED_TIME + timedelta(minutes=20),
    )
    service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=newer_snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=20),
    )
    db.flush()
    row = db.scalar(select(OamWorkOrder))
    assert row is not None
    latest_updated_at = row.updated_at

    replay = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=first_snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=21),
    )

    assert replay.sync_run_id == first.sync_run_id
    assert replay.duplicate is True
    assert row.updated_at == latest_updated_at
    assert service._aware(row.source_updated_at) == newer_source_time


def test_unchanged_new_snapshot_refreshes_freshness_without_new_version(db: Session):
    source = _source(db)
    _person_mapping(db, source)
    first_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-0003",
    )
    service.publish_completed_work_order_snapshot(
        db, snapshot_id=first_snapshot.id, now=PUBLISHED_TIME
    )
    db.flush()
    row = db.scalar(select(OamWorkOrder))
    assert row is not None
    first_version_id = db.get(ExternalObject, row.external_object_id).current_version_id
    later_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-0004",
        snapshot_time=SNAPSHOT_TIME + timedelta(minutes=20),
        completed_time=COMPLETED_TIME + timedelta(minutes=20),
    )
    later_publish = PUBLISHED_TIME + timedelta(minutes=20)

    result = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=later_snapshot.id,
        now=later_publish,
    )
    db.flush()

    assert result.unchanged_records == 1
    assert service._aware(row.updated_at) == later_publish
    assert db.get(ExternalObject, row.external_object_id).current_version_id == first_version_id
    assert db.scalar(select(func.count()).select_from(ExternalObjectVersion).where(
        ExternalObjectVersion.external_object_id == row.external_object_id
    )) == 1


def test_new_source_time_versions_even_when_projected_fields_are_unchanged(
    db: Session,
):
    source = _source(db)
    _person_mapping(db, source)
    first_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-source-time-0001",
    )
    service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=first_snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()
    row = db.scalar(select(OamWorkOrder))
    assert row is not None
    next_source_time = SOURCE_TIME + timedelta(minutes=10)
    later_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(source_time=next_source_time),),
        snapshot_id="snapshot-work-order-source-time-0002",
        snapshot_time=SNAPSHOT_TIME + timedelta(minutes=20),
        completed_time=COMPLETED_TIME + timedelta(minutes=20),
    )

    result = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=later_snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=20),
    )
    db.flush()

    assert result.updated_records == 1
    assert service._aware(row.source_updated_at) == next_source_time
    external = db.get(ExternalObject, row.external_object_id)
    assert external is not None
    current = db.get(ExternalObjectVersion, external.current_version_id)
    assert current is not None
    assert service._aware(current.source_updated_at) == next_source_time
    assert db.scalar(
        select(func.count())
        .select_from(ExternalObjectVersion)
        .where(ExternalObjectVersion.external_object_id == row.external_object_id)
    ) == 2


def test_missing_person_mapping_conflicts_without_formal_projection(db: Session):
    _source(db)
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(executor_id="unmapped-account"),),
        snapshot_id="snapshot-work-order-0005",
    )

    result = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()

    assert result.status == "conflict"
    assert result.conflict_records == 1
    assert db.scalar(select(OamWorkOrder)) is None
    run = db.get(SyncRun, result.sync_run_id)
    assert run is not None and run.status == "conflict"
    conflict = db.scalar(select(SyncConflict))
    assert conflict is not None
    assert conflict.conflict_type == "oam_work_order_person_mapping_missing"
    assert "unmapped-account" not in _canonical(conflict.external_value_jsonb)
    event = db.get(SyncInboxEvent, conflict.inbox_event_id)
    assert event is not None and event.status == "conflict"


def test_ignored_projection_conflict_reopens_when_it_recurs(db: Session):
    _source(db)
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(executor_id="unmapped-account"),),
        snapshot_id="snapshot-work-order-ignored-conflict",
    )
    first = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME,
    )
    assert first.status == "conflict"
    db.flush()
    conflict = db.scalar(select(SyncConflict))
    assert conflict is not None
    conflict.status = "ignored"
    conflict.resolution_jsonb = {"reason": "operator ignored the prior occurrence"}
    conflict.resolved_by = "operator"
    conflict.resolved_at = PUBLISHED_TIME
    db.flush()

    replay = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=1),
    )
    db.flush()

    assert replay.status == "conflict"
    assert conflict.status == "open"
    assert conflict.resolution_jsonb is None
    assert conflict.resolved_by is None
    assert conflict.resolved_at is None


def test_inbox_event_hash_replay_records_formal_conflict_without_failed_run(
    db: Session,
):
    source = _source(db)
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-event-replay-0001",
    )
    first = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME,
    )
    assert first.status == "conflict"
    db.flush()
    event = db.scalar(select(SyncInboxEvent))
    assert event is not None
    prior_error_code = event.error_code
    event.payload_sha256 = "f" * 64
    _person_mapping(db, source)
    db.flush()

    replay = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=1),
    )
    db.flush()

    assert replay.sync_run_id == first.sync_run_id
    assert replay.status == "conflict"
    assert replay.conflict_records == 1
    assert event.payload_sha256 == "f" * 64
    assert event.error_code == prior_error_code
    conflict = db.scalar(
        select(SyncConflict).where(
            SyncConflict.conflict_type == "oam_work_order_event_replay_conflict"
        )
    )
    assert conflict is not None
    assert conflict.run_id == first.sync_run_id
    assert conflict.inbox_event_id == event.id
    assert conflict.local_value_jsonb["payload_sha256"] == "f" * 64
    assert conflict.external_value_jsonb["payload_sha256"] != "f" * 64
    assert not tuple(db.scalars(select(SyncRun).where(SyncRun.status == "failed")))


def test_ignored_event_replay_conflict_reopens_when_it_recurs(db: Session):
    source = _source(db)
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-ignored-event-replay",
    )
    service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()
    event = db.scalar(select(SyncInboxEvent))
    assert event is not None
    event.payload_sha256 = "f" * 64
    _person_mapping(db, source)
    first_replay = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=1),
    )
    assert first_replay.status == "conflict"
    db.flush()
    conflict = db.scalar(
        select(SyncConflict).where(
            SyncConflict.conflict_type == "oam_work_order_event_replay_conflict"
        )
    )
    assert conflict is not None
    conflict.status = "ignored"
    conflict.resolution_jsonb = {"reason": "operator ignored the prior occurrence"}
    conflict.resolved_by = "operator"
    conflict.resolved_at = PUBLISHED_TIME + timedelta(minutes=1)
    db.flush()

    replay = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=2),
    )
    db.flush()

    assert replay.status == "conflict"
    assert conflict.status == "open"
    assert conflict.resolution_jsonb is None
    assert conflict.resolved_by is None
    assert conflict.resolved_at is None


def test_same_source_time_changed_projection_conflicts_and_preserves_old_row(db: Session):
    source = _source(db)
    _person_mapping(db, source)
    first_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(status="processing"),),
        snapshot_id="snapshot-work-order-0006",
    )
    service.publish_completed_work_order_snapshot(
        db, snapshot_id=first_snapshot.id, now=PUBLISHED_TIME
    )
    db.flush()
    row = db.scalar(select(OamWorkOrder))
    assert row is not None and row.status == "active"
    later_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(status="end"),),
        snapshot_id="snapshot-work-order-0007",
        snapshot_time=SNAPSHOT_TIME + timedelta(minutes=20),
        completed_time=COMPLETED_TIME + timedelta(minutes=20),
    )

    result = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=later_snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=20),
    )
    db.flush()

    assert result.status == "conflict"
    assert row.status == "active"
    assert service._aware(row.updated_at) == PUBLISHED_TIME
    assert db.scalar(select(func.count()).select_from(ExternalObjectVersion).where(
        ExternalObjectVersion.external_object_id == row.external_object_id
    )) == 1


def test_existing_projection_drift_conflicts_without_refresh(db: Session):
    source = _source(db)
    _person_mapping(db, source)
    first_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-drift-0001",
    )
    service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=first_snapshot.id,
        now=PUBLISHED_TIME,
    )
    db.flush()
    row = db.scalar(select(OamWorkOrder))
    assert row is not None
    row.status = "completed"
    current = db.get(ExternalObject, row.external_object_id)
    assert current is not None
    version = db.get(ExternalObjectVersion, current.current_version_id)
    assert version is not None
    version.payload_jsonb = {**version.payload_jsonb, "status": "cancelled"}
    later_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-drift-0002",
        snapshot_time=SNAPSHOT_TIME + timedelta(minutes=20),
        completed_time=COMPLETED_TIME + timedelta(minutes=20),
    )
    tampered_updated_at = row.updated_at

    result = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=later_snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=20),
    )

    assert result.status == "conflict"
    assert result.projected_records == 0
    assert row.status == "completed"
    assert row.updated_at == tampered_updated_at

    with pytest.raises(service.OamWorkOrderProjectionError) as duplicate_error:
        service.publish_completed_work_order_snapshot(
            db,
            snapshot_id=first_snapshot.id,
            now=PUBLISHED_TIME + timedelta(minutes=21),
        )
    assert duplicate_error.value.code == "oam_work_order_existing_projection_invalid"


def test_rolling_window_absence_never_tombstones_or_refreshes_old_projection(db: Session):
    source = _source(db)
    _person_mapping(db, source)
    first_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-0008",
    )
    service.publish_completed_work_order_snapshot(
        db, snapshot_id=first_snapshot.id, now=PUBLISHED_TIME
    )
    db.flush()
    row = db.scalar(select(OamWorkOrder))
    assert row is not None
    external = db.get(ExternalObject, row.external_object_id)
    empty_snapshot = _stage_snapshot(
        db,
        payloads=(),
        snapshot_id="snapshot-work-order-0009",
        snapshot_time=SNAPSHOT_TIME + timedelta(minutes=20),
        completed_time=COMPLETED_TIME + timedelta(minutes=20),
    )

    result = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=empty_snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=20),
    )
    db.flush()

    assert result.status == "completed"
    assert result.projected_records == 0
    assert row.status == "active"
    assert service._aware(row.updated_at) == PUBLISHED_TIME
    assert external.deleted_at is None


def test_nonminimal_payload_and_unknown_status_fail_before_formal_write(db: Session):
    source = _source(db)
    _person_mapping(db, source)
    extra_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(executorPhone="13800000000"),),
        snapshot_id="snapshot-work-order-0010",
    )
    with pytest.raises(service.OamWorkOrderProjectionError) as extra_error:
        service.publish_completed_work_order_snapshot(
            db,
            snapshot_id=extra_snapshot.id,
            now=PUBLISHED_TIME,
        )
    assert extra_error.value.code == "oam_work_order_payload_not_minimal"
    db.rollback()

    # Rebuild because the caller correctly rolled the failed transaction back.
    source = _source(db)
    _person_mapping(db, source)
    unknown_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(status="brand_new_unknown"),),
        snapshot_id="snapshot-work-order-0011",
    )
    with pytest.raises(service.OamWorkOrderProjectionError) as status_error:
        service.publish_completed_work_order_snapshot(
            db,
            snapshot_id=unknown_snapshot.id,
            now=PUBLISHED_TIME,
        )
    assert status_error.value.code == "oam_work_order_status_unknown"
    assert db.scalar(select(OamWorkOrder)) is None


def test_manifest_hash_is_revalidated_before_formal_writes(db: Session):
    source = _source(db)
    _person_mapping(db, source)
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-manifest-hash-0001",
    )
    snapshot.manifest_sha256 = "f" * 64

    with pytest.raises(service.OamWorkOrderProjectionError) as error:
        service.publish_completed_work_order_snapshot(
            db,
            snapshot_id=snapshot.id,
            now=PUBLISHED_TIME,
        )

    assert error.value.code == "oam_work_order_manifest_hash_mismatch"
    assert db.scalar(select(SyncRun)) is None
    assert db.scalar(select(OamWorkOrder)) is None


def test_conflict_retry_cannot_overwrite_a_newer_completed_snapshot(db: Session):
    source = _source(db)
    old_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-stale-retry-0001",
    )
    conflict = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=old_snapshot.id,
        now=PUBLISHED_TIME,
    )
    assert conflict.status == "conflict"
    _person_mapping(db, source)
    newer_snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-stale-retry-0002",
        snapshot_time=SNAPSHOT_TIME + timedelta(minutes=20),
        completed_time=COMPLETED_TIME + timedelta(minutes=20),
    )
    completed = service.publish_completed_work_order_snapshot(
        db,
        snapshot_id=newer_snapshot.id,
        now=PUBLISHED_TIME + timedelta(minutes=20),
    )
    assert completed.status == "completed"
    db.flush()

    with pytest.raises(service.OamWorkOrderProjectionError) as error:
        service.publish_completed_work_order_snapshot(
            db,
            snapshot_id=old_snapshot.id,
            now=PUBLISHED_TIME + timedelta(minutes=21),
        )

    assert error.value.code in {
        "oam_work_order_current_snapshot_mismatch",
        "oam_work_order_snapshot_stale",
    }
    row = db.scalar(select(OamWorkOrder))
    assert row is not None
    assert service._aware(row.updated_at) == PUBLISHED_TIME + timedelta(minutes=20)


def test_failed_snapshot_evidence_prevents_poison_queue_starvation(db: Session):
    source = _source(db)
    _person_mapping(db, source)
    poisoned = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-poison-0001",
    )
    poisoned.manifest_sha256 = "f" * 64
    db.commit()
    healthy = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-poison-0002",
        snapshot_time=SNAPSHOT_TIME + timedelta(minutes=20),
        completed_time=COMPLETED_TIME + timedelta(minutes=20),
    )
    db.commit()

    assert service.next_unpublished_work_order_snapshot_id(db) == poisoned.id
    with pytest.raises(service.OamWorkOrderProjectionError) as error:
        service.publish_completed_work_order_snapshot(
            db,
            snapshot_id=poisoned.id,
            now=PUBLISHED_TIME + timedelta(minutes=20),
        )
    db.rollback()
    failed_run_id = service.record_failed_work_order_snapshot(
        db,
        snapshot_id=poisoned.id,
        failure_code=error.value.code,
    )
    db.commit()

    assert failed_run_id is not None
    failed_run = db.get(SyncRun, failed_run_id)
    assert failed_run is not None
    assert failed_run.status == "failed"
    assert failed_run.failure_code == "oam_work_order_manifest_hash_mismatch"
    assert service.next_unpublished_work_order_snapshot_id(db) == healthy.id


@pytest.mark.parametrize(
    ("manifest_case", "expected_code"),
    (
        ("unparseable", "oam_work_order_manifest_invalid"),
        ("wrong_entities", "oam_work_order_manifest_mismatch"),
    ),
)
def test_queue_does_not_silently_skip_invalid_completed_manifest(
    db: Session,
    manifest_case: str,
    expected_code: str,
):
    _source(db)
    snapshot = _stage_snapshot(
        db,
        payloads=(),
        snapshot_id=f"snapshot-work-order-invalid-queue-{manifest_case}",
    )
    if manifest_case == "unparseable":
        snapshot.manifest_json = "{"
        snapshot.manifest_sha256 = hashlib.sha256(b"{").hexdigest()
    else:
        manifest = json.loads(snapshot.manifest_json)
        manifest["entities"][0]["entity_type"] = "inventory"
        snapshot.manifest_json = _canonical(manifest)
        snapshot.manifest_sha256 = hashlib.sha256(
            snapshot.manifest_json.encode("utf-8")
        ).hexdigest()
    db.commit()

    assert service.next_unpublished_work_order_snapshot_id(db) == snapshot.id
    with pytest.raises(service.OamWorkOrderProjectionError) as error:
        service.publish_completed_work_order_snapshot(
            db,
            snapshot_id=snapshot.id,
            now=PUBLISHED_TIME,
        )
    assert error.value.code == expected_code
    db.rollback()
    failed_run_id = service.record_failed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        failure_code=error.value.code,
    )
    db.commit()

    assert failed_run_id is not None
    failed_run = db.get(SyncRun, failed_run_id)
    assert failed_run is not None
    assert failed_run.status == "failed"
    assert failed_run.failure_code == expected_code
    assert service.next_unpublished_work_order_snapshot_id(db) is None


def test_invalid_staging_json_has_stable_code_and_can_be_quarantined(db: Session):
    _source(db)
    snapshot = _stage_snapshot(
        db,
        payloads=(_work_order_payload(),),
        snapshot_id="snapshot-work-order-invalid-staging-json",
    )
    staged = db.scalar(
        select(ExternalSyncSnapshotRecord).where(
            ExternalSyncSnapshotRecord.snapshot_ref_id == snapshot.id,
            ExternalSyncSnapshotRecord.entity_type == "work_order",
        )
    )
    assert staged is not None
    staged.payload_json = "{"
    db.commit()

    with pytest.raises(service.OamWorkOrderProjectionError) as error:
        service.publish_completed_work_order_snapshot(
            db,
            snapshot_id=snapshot.id,
            now=PUBLISHED_TIME,
        )
    assert error.value.code == "oam_work_order_staging_json_invalid"
    db.rollback()

    failed_run_id = service.record_failed_work_order_snapshot(
        db,
        snapshot_id=snapshot.id,
        failure_code=error.value.code,
    )
    db.commit()

    assert failed_run_id is not None
    failed_run = db.get(SyncRun, failed_run_id)
    assert failed_run is not None
    assert failed_run.status == "failed"
    assert failed_run.failure_code == "oam_work_order_staging_json_invalid"
    assert service.next_unpublished_work_order_snapshot_id(db) is None


def test_worker_selects_queue_before_starting_publication_transaction(monkeypatch):
    events: list[object] = []

    class FakeSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    monkeypatch.setattr(projector, "SessionLocal", lambda: FakeSession())

    def select_next(_db):
        events.append("queue-query")
        return None

    monkeypatch.setattr(
        projector,
        "next_unpublished_work_order_snapshot_id",
        select_next,
    )

    assert projector._process_one(None) == (False, 0)
    assert events == ["queue-query"]


def test_worker_stops_before_queue_when_production_acl_boundary_drifts(monkeypatch):
    events: list[object] = []
    monkeypatch.setattr(
        projector,
        "get_settings",
        lambda: SimpleNamespace(
            environment="production",
            database_expected_runtime_role=projector.PROJECTOR_ROLE,
            database_expected_migration_role="star_oam_migrator",
        ),
    )
    monkeypatch.setattr(
        projector,
        "verify_oam_projection_database_boundary",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("acl drift")),
    )
    monkeypatch.setattr(
        projector,
        "SessionLocal",
        lambda: (_ for _ in ()).throw(AssertionError("queue must not be read")),
    )
    monkeypatch.setattr(projector, "_emit", lambda payload: events.append(payload))

    assert projector._process_one(None) == (False, 2)
    assert events == [
        {
            "ok": False,
            "code": "oam_work_order_projection_database_boundary_invalid",
            "message": "生产投影器数据库权限边界校验失败",
        }
    ]


def test_worker_acquires_session_lock_before_repeatable_read_and_publish(monkeypatch):
    events: list[object] = []

    class FakeDialect:
        name = "postgresql"

    class SelectionSession:
        def __enter__(self):
            events.append("selection-enter")
            return self

        def __exit__(self, *_args):
            events.append("selection-exit")
            return None

        def rollback(self):
            events.append("selection-rollback")

    class FakeConnection:
        dialect = FakeDialect()

        def __enter__(self):
            events.append("connection-enter")
            return self

        def __exit__(self, *_args):
            events.append("connection-exit")
            return None

        def commit(self):
            events.append("connection-commit")

        def rollback(self):
            events.append("connection-rollback")

        def in_transaction(self):
            return False

        def execution_options(self, **options):
            events.append(("connection-isolation", options))
            return self

        def invalidate(self):
            events.append("connection-invalidate")

    connection = FakeConnection()

    class FakeEngine:
        dialect = FakeDialect()

        def connect(self):
            return connection

    class ProcessingSession:
        def __enter__(self):
            events.append("processing-enter")
            return self

        def __exit__(self, *_args):
            events.append("processing-exit")
            return None

        def get_bind(self):
            return connection

        def connection(self, *, execution_options):
            events.append(("worker-isolation", execution_options))
            return connection

        def commit(self):
            events.append("processing-commit")

        def rollback(self):
            events.append("processing-rollback")

    monkeypatch.setattr(projector, "SessionLocal", lambda: SelectionSession())
    monkeypatch.setattr(projector, "Session", lambda **_kwargs: ProcessingSession())
    monkeypatch.setattr(projector, "engine", FakeEngine())
    monkeypatch.setattr(
        projector,
        "next_unpublished_work_order_snapshot_id",
        lambda _db: events.append("queue-query") or "snapshot-selected",
    )
    monkeypatch.setattr(
        projector,
        "work_order_snapshot_lock_coordinates",
        lambda _db, *, snapshot_id: (
            events.append(("coordinates-query", snapshot_id))
            or (SOURCE_INSTANCE, SCOPE_KEY)
        ),
    )
    monkeypatch.setattr(
        projector,
        "acquire_external_sync_scope_session_lock",
        lambda _connection, **_kwargs: events.append("session-lock-acquire"),
    )
    monkeypatch.setattr(
        projector,
        "release_external_sync_scope_session_lock",
        lambda _connection, **_kwargs: events.append("session-lock-release") or True,
    )
    monkeypatch.setattr(
        projector,
        "publish_completed_work_order_snapshot",
        lambda _db, *, snapshot_id: (
            events.append(("publish-query", snapshot_id))
            or SimpleNamespace(
                status="completed",
                snapshot_id=snapshot_id,
                sync_run_id=uuid.uuid4(),
                projected_records=1,
                created_records=1,
                updated_records=0,
                unchanged_records=0,
                conflict_records=0,
                duplicate=False,
            )
        ),
    )
    monkeypatch.setattr(projector, "_emit", lambda payload: events.append(("emit", payload)))

    assert projector._process_one(None) == (True, 0)
    assert events.index("queue-query") < events.index("selection-rollback")
    assert events.index("selection-rollback") < events.index("session-lock-acquire")
    assert events.index("session-lock-acquire") < events.index(
        ("connection-isolation", {"isolation_level": "REPEATABLE READ"})
    )
    assert events.index(
        ("connection-isolation", {"isolation_level": "REPEATABLE READ"})
    ) < events.index(("publish-query", "snapshot-selected"))
    assert events.index(("publish-query", "snapshot-selected")) < events.index(
        "session-lock-release"
    )
    assert events.index("session-lock-release") < next(
        index for index, event in enumerate(events) if isinstance(event, tuple) and event[0] == "emit"
    )


def test_worker_repeatable_read_setup_is_sqlite_safe_noop(db: Session):
    assert db.in_transaction() is False
    projector._begin_worker_transaction(db)
    assert db.in_transaction() is False
    assert db.scalar(select(func.count()).select_from(SourceSystem)) == 0


def test_worker_rearms_repeatable_read_before_failure_quarantine(monkeypatch):
    events: list[object] = []

    class FakeDialect:
        name = "postgresql"

    class SelectionSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def rollback(self):
            events.append("selection-rollback")

    class FakeConnection:
        dialect = FakeDialect()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def commit(self):
            events.append("connection-commit")

        def rollback(self):
            events.append("connection-rollback")

        def in_transaction(self):
            return False

        def execution_options(self, **options):
            events.append(("connection-isolation", options))
            return self

        def invalidate(self):
            events.append("connection-invalidate")

    connection = FakeConnection()

    class FakeEngine:
        dialect = FakeDialect()

        def connect(self):
            return connection

    class ProcessingSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_bind(self):
            return connection

        def connection(self, *, execution_options):
            events.append(("isolation", execution_options))
            return connection

        def rollback(self):
            events.append("rollback")

        def commit(self):
            events.append("commit")

    monkeypatch.setattr(projector, "SessionLocal", lambda: SelectionSession())
    monkeypatch.setattr(projector, "Session", lambda **_kwargs: ProcessingSession())
    monkeypatch.setattr(projector, "engine", FakeEngine())
    monkeypatch.setattr(
        projector,
        "work_order_snapshot_lock_coordinates",
        lambda _db, *, snapshot_id: (
            events.append(("coordinates-query", snapshot_id))
            or (SOURCE_INSTANCE, SCOPE_KEY)
        ),
    )
    monkeypatch.setattr(
        projector,
        "acquire_external_sync_scope_session_lock",
        lambda _connection, **_kwargs: events.append("session-lock-acquire"),
    )
    monkeypatch.setattr(
        projector,
        "release_external_sync_scope_session_lock",
        lambda _connection, **_kwargs: events.append("session-lock-release") or True,
    )

    def fail_publish(_db, *, snapshot_id):
        events.append(("publish-query", snapshot_id))
        raise service.OamWorkOrderProjectionError("invalid_test_snapshot", "invalid")

    def quarantine(_db, *, snapshot_id, failure_code):
        events.append(("quarantine-query", snapshot_id, failure_code))
        return uuid.uuid4()

    monkeypatch.setattr(projector, "publish_completed_work_order_snapshot", fail_publish)
    monkeypatch.setattr(projector, "record_failed_work_order_snapshot", quarantine)
    monkeypatch.setattr(projector, "_emit", lambda payload: events.append(("emit", payload)))

    processed, exit_code = projector._process_one("snapshot-explicit")

    assert processed is True
    assert exit_code == 3
    first_isolation = events.index(
        ("isolation", {"isolation_level": "REPEATABLE READ"})
    )
    publish_query = events.index(("publish-query", "snapshot-explicit"))
    quarantine_query = events.index(
        ("quarantine-query", "snapshot-explicit", "invalid_test_snapshot")
    )
    isolation_indexes = [
        index for index, event in enumerate(events) if event == (
            "isolation",
            {"isolation_level": "REPEATABLE READ"},
        )
    ]
    assert len(isolation_indexes) == 2
    assert events.index("session-lock-acquire") < first_isolation < publish_query
    assert publish_query < isolation_indexes[1] < quarantine_query
    assert quarantine_query < events.index("session-lock-release")


def test_worker_invalidates_connection_when_session_lock_release_is_uncertain(
    monkeypatch,
):
    events: list[object] = []

    class FakeDialect:
        name = "postgresql"

    class SelectionSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def rollback(self):
            return None

    class FakeConnection:
        dialect = FakeDialect()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def commit(self):
            return None

        def rollback(self):
            return None

        def in_transaction(self):
            return False

        def execution_options(self, **_options):
            return self

        def invalidate(self):
            events.append("connection-invalidate")

    connection = FakeConnection()

    class FakeEngine:
        dialect = FakeDialect()

        def connect(self):
            return connection

    class ProcessingSession:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def get_bind(self):
            return connection

        def connection(self, *, execution_options):
            return connection

        def commit(self):
            return None

        def rollback(self):
            return None

    monkeypatch.setattr(projector, "SessionLocal", lambda: SelectionSession())
    monkeypatch.setattr(projector, "Session", lambda **_kwargs: ProcessingSession())
    monkeypatch.setattr(projector, "engine", FakeEngine())
    monkeypatch.setattr(
        projector,
        "work_order_snapshot_lock_coordinates",
        lambda _db, *, snapshot_id: (SOURCE_INSTANCE, SCOPE_KEY),
    )
    monkeypatch.setattr(
        projector,
        "acquire_external_sync_scope_session_lock",
        lambda _connection, **_kwargs: None,
    )
    monkeypatch.setattr(
        projector,
        "release_external_sync_scope_session_lock",
        lambda _connection, **_kwargs: False,
    )
    monkeypatch.setattr(
        projector,
        "publish_completed_work_order_snapshot",
        lambda _db, *, snapshot_id: SimpleNamespace(
            status="completed",
            snapshot_id=snapshot_id,
            sync_run_id=uuid.uuid4(),
            projected_records=1,
            created_records=1,
            updated_records=0,
            unchanged_records=0,
            conflict_records=0,
            duplicate=False,
        ),
    )
    monkeypatch.setattr(projector, "_emit", lambda payload: events.append(("emit", payload)))

    assert projector._process_one("snapshot-explicit") == (True, 2)
    assert events[0] == "connection-invalidate"
    assert events[1][1]["code"] == "oam_work_order_projection_scope_lock_failed"
