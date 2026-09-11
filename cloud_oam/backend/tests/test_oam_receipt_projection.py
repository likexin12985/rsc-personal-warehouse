from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import uuid

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.database import Base
from app.foundation_models import ExternalObject, ExternalObjectMapping, SourceSystem
from app.formal_services.oam_receipt_projection import (
    OamReceiptProjectionError,
    project_oam_receipt_record,
    publish_completed_oam_receipt_snapshot,
)
from app.inventory_models import OamReceiptEvidence, Shipment
from app.models import ExternalSyncCurrentRecord, ExternalSyncSnapshot, User
from app.routers.integrations import _validate_snapshot_entity_boundary, _validate_snapshot_manifest_boundary
from app.schemas import EdgeSyncEntityManifestIn, EdgeSyncSnapshotBatchIn, EdgeSyncSnapshotCompleteIn


SOURCE_TIME = datetime(2026, 9, 12, 2, 0, tzinfo=timezone.utc)


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


def _canonical(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _setup(db: Session, *, with_mapping: bool = True):
    source = SourceSystem(code="starcharge_oam", name="StarCharge OAM", mode="read_only", enabled=True, configuration_jsonb={}, created_at=SOURCE_TIME, updated_at=SOURCE_TIME)
    user = User(id=str(uuid.uuid4()), person_id=None, account_status="active", last_login_at=None, authorization_version=1, mobile=f"1{uuid.uuid4().int % 10**10:010d}", name="映射审批人", password_hash="test", role="admin", province=None, is_active=True, require_password_change=False, created_at=SOURCE_TIME, updated_at=SOURCE_TIME)
    shipment = Shipment(id=uuid.uuid4(), shipment_no="SHP-RECEIPT-001", source_location_id=uuid.uuid4(), target_location_id=uuid.uuid4(), target_person_id=None, carrier="SF", tracking_no="SF123", status="shipped", shipped_at=SOURCE_TIME, idempotency_key_hash="a" * 64, request_hash="b" * 64, actor_user_id=user.id, actor_person_id=uuid.uuid4(), authorization_version=1, created_at=SOURCE_TIME)
    db.add_all([source, user, shipment])
    db.flush()
    external = ExternalObject(source_system_id=source.id, entity_type="oam_receipt", external_id="oam-receipt-001", current_version_id=None, deleted_at=None, created_at=SOURCE_TIME, updated_at=SOURCE_TIME)
    db.add(external)
    db.flush()
    if with_mapping:
        db.add(ExternalObjectMapping(external_object_id=external.id, local_object_type="shipment", local_object_id=str(shipment.id), status="approved", approved_by=user.id, approved_at=SOURCE_TIME, reason="精确业务键绑定", created_at=SOURCE_TIME, updated_at=SOURCE_TIME))
    payload = {"id": "oam-receipt-001", "status": "synced", "sourceTime": "2026-09-12T02:00:00Z", "sourceVersion": "receipt-v1:001"}
    db.flush()
    row = ExternalSyncCurrentRecord(source_system="starcharge_oam", source_instance="edge-receipt-1", scope_key="oam-receipts:company-1", entity_type="oam_receipt", business_key="oam-receipt:oam-receipt-001", source_updated_at=SOURCE_TIME, payload_json=_canonical(payload), payload_sha256=hashlib.sha256(_canonical(payload).encode()).hexdigest(), last_snapshot_id="snapshot-1", created_at=SOURCE_TIME, updated_at=SOURCE_TIME)
    db.add(row)
    db.flush()
    return source, shipment, row


def test_projects_only_after_explicit_approved_shipment_mapping(db):
    source, shipment, row = _setup(db)
    result = project_oam_receipt_record(db, source=source, record=row)
    assert result.shipment_id == shipment.id
    assert result.duplicate is False
    assert db.scalar(select(OamReceiptEvidence).where(OamReceiptEvidence.external_object_id == result.external_object_id)) is not None


def test_replay_is_idempotent_and_conflict_is_rejected(db):
    source, _, row = _setup(db)
    first = project_oam_receipt_record(db, source=source, record=row)
    replay = project_oam_receipt_record(db, source=source, record=row)
    assert replay.duplicate is True
    row.payload_json = row.payload_json.replace("synced", "exception")
    with pytest.raises(OamReceiptProjectionError) as exc:
        project_oam_receipt_record(db, source=source, record=row)
    assert exc.value.code == "oam_receipt_payload_hash_mismatch"
    assert first.evidence_id == replay.evidence_id


def test_missing_mapping_fails_closed_without_evidence(db):
    source, _, row = _setup(db, with_mapping=False)
    with pytest.raises(OamReceiptProjectionError) as exc:
        project_oam_receipt_record(db, source=source, record=row)
    assert exc.value.code == "oam_receipt_shipment_mapping_ambiguous"
    assert db.scalar(select(OamReceiptEvidence)) is None


@pytest.mark.parametrize(
    "mutator,code",
    [
        (lambda payload: payload.update({"unexpected": True}), "oam_receipt_payload_not_minimal"),
        (lambda payload: payload.update({"status": "received"}), "oam_receipt_status_invalid"),
        (lambda payload: payload.update({"sourceTime": "2026-09-12T02:00:00"}), "oam_receipt_source_time_invalid"),
    ],
)
def test_payload_contract_is_strict(db, mutator, code):
    source, _, row = _setup(db)
    payload = json.loads(row.payload_json)
    mutator(payload)
    row.payload_json = _canonical(payload)
    row.payload_sha256 = hashlib.sha256(row.payload_json.encode()).hexdigest()
    with pytest.raises(OamReceiptProjectionError) as exc:
        project_oam_receipt_record(db, source=source, record=row)
    assert exc.value.code == code


def test_receipt_snapshot_scope_is_separate_from_other_entities():
    batch = EdgeSyncSnapshotBatchIn(
        snapshot_id="receipt-snapshot-1",
        scope_key="oam-receipts:company-1",
        sync_mode="incremental",
        company_id="company-1",
        org_code="org-1",
        entity_type="oam_receipt",
        snapshot_at=SOURCE_TIME,
        sequence=1,
        total_sequences=1,
        records=[{
            "business_key": "oam-receipt:oam-receipt-001",
            "source_updated_at": SOURCE_TIME,
            "data": {"id": "oam-receipt-001", "status": "synced", "sourceTime": "2026-09-12T02:00:00Z", "sourceVersion": "receipt-v1:001"},
            "operation": "upsert",
        }],
    )
    _validate_snapshot_entity_boundary(batch)
    manifest = EdgeSyncSnapshotCompleteIn(
        snapshot_id="receipt-snapshot-1",
        scope_key="oam-receipts:company-1",
        sync_mode="incremental",
        company_id="company-1",
        org_code="org-1",
        snapshot_at=SOURCE_TIME,
        entities=[EdgeSyncEntityManifestIn(entity_type="oam_receipt", final_record_count=0, final_sha256="a" * 64, delta_record_count=0, delta_sha256="b" * 64, batch_count=0)],
    )
    _validate_snapshot_manifest_boundary(manifest)


def test_completed_snapshot_projects_current_mirror_and_replays(db):
    source, shipment, row = _setup(db)
    snapshot = ExternalSyncSnapshot(
        id="snapshot-1",
        source_system="starcharge_oam",
        source_instance=row.source_instance,
        snapshot_id="snapshot-1",
        scope_key=row.scope_key,
        sync_mode="incremental",
        company_id="company-1",
        org_code="org-1",
        snapshot_at=SOURCE_TIME,
        status="complete",
        manifest_json="",
        manifest_sha256="",
        received_at=SOURCE_TIME,
        completed_at=SOURCE_TIME,
    )
    db.add(snapshot)
    db.flush()
    wire = [{
        "business_key": row.business_key,
        "source_updated_at": SOURCE_TIME.isoformat(),
        "data": json.loads(row.payload_json),
    }]
    manifest = EdgeSyncSnapshotCompleteIn(
        snapshot_id="snapshot-1",
        scope_key=row.scope_key,
        sync_mode="incremental",
        company_id="company-1",
        org_code="org-1",
        snapshot_at=SOURCE_TIME,
        entities=[EdgeSyncEntityManifestIn(entity_type="oam_receipt", final_record_count=1, final_sha256=hashlib.sha256(_canonical(wire).encode()).hexdigest(), delta_record_count=1, delta_sha256="b" * 64, batch_count=1)],
    )
    snapshot.manifest_json = _canonical(manifest.model_dump(mode="json"))
    snapshot.manifest_sha256 = hashlib.sha256(snapshot.manifest_json.encode()).hexdigest()
    db.flush()
    first = publish_completed_oam_receipt_snapshot(db, snapshot_id=snapshot.id, source=source)
    replay = publish_completed_oam_receipt_snapshot(db, snapshot_id=snapshot.id, source=source)
    assert first.projected_records == replay.projected_records == 1
    assert first.duplicate_records == 0
    assert replay.duplicate_records == 1
    evidence = db.scalar(select(OamReceiptEvidence).where(OamReceiptEvidence.shipment_id == shipment.id))
    assert evidence is not None
