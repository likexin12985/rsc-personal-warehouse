import hashlib
import os
from datetime import datetime, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session
from starlette.requests import Request


os.environ.setdefault("OAM_ENVIRONMENT", "test")
os.environ.setdefault("OAM_DATABASE_URL", "sqlite+pysqlite:///:memory:")
os.environ.setdefault(
    "OAM_JWT_SECRET", "edge-sync-safety-test-secret-at-least-32-characters"
)

from app.database import Base
from app.models import (
    ExternalSyncSnapshot,
    ExternalSyncSnapshotBatch,
    OamPersonnelBinding,
)
from app.routers.integrations import (
    VerifiedEdgeRequest,
    complete_snapshot,
    receive_edge_batch,
    receive_snapshot_batch,
)
from app.routers import integrations
from app.schemas import (
    EdgeSyncBatchIn,
    EdgeSyncSnapshotBatchIn,
    EdgeSyncSnapshotCompleteIn,
)


def request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/api/integrations/oam/edge/snapshots/complete",
            "headers": [],
            "client": ("127.0.0.1", 12345),
        }
    )


def verified(source: str, batch_id: str, body: bytes = b"{}") -> VerifiedEdgeRequest:
    return VerifiedEdgeRequest(
        source_instance=source,
        batch_id=batch_id,
        body=body,
        body_sha256=hashlib.sha256(body).hexdigest(),
    )


def test_legacy_batch_protocol_is_disabled_by_default(monkeypatch):
    monkeypatch.setattr(
        integrations.settings,
        "edge_sync_legacy_batches_enabled",
        False,
    )
    payload = EdgeSyncBatchIn(
        snapshot_at=datetime(2026, 8, 30, tzinfo=timezone.utc),
        entity_type="inventory",
        records=[],
    )
    with pytest.raises(HTTPException) as error:
        receive_edge_batch(
            payload,
            request(),
            verified("edge-test", "legacy-batch"),
            Session(),
        )
    assert error.value.status_code == 410


def test_older_snapshot_is_quarantined_without_replacing_current_projection():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    source = "edge-test"
    scope = "warehouse:WH-1"
    newer = ExternalSyncSnapshot(
        source_system="starcharge_oam",
        source_instance=source,
        snapshot_id="snapshot-newer",
        scope_key=scope,
        sync_mode="full",
        company_id="company-nio",
        org_code="org-nio",
        snapshot_at=datetime(2026, 8, 30, 2, tzinfo=timezone.utc),
        status="complete",
        manifest_json="{}",
        manifest_sha256="1" * 64,
        completed_at=datetime(2026, 8, 30, 2, 5, tzinfo=timezone.utc),
    )

    with Session(engine) as db:
        db.add(newer)
        db.commit()
        payload = EdgeSyncSnapshotCompleteIn(
            snapshot_id="snapshot-older",
            scope_key=scope,
            sync_mode="full",
            company_id="company-nio",
            org_code="org-nio",
            snapshot_at=datetime(2026, 8, 30, 1, tzinfo=timezone.utc),
            entities=[
                {
                    "entity_type": "inventory",
                    "final_record_count": 0,
                    "final_sha256": hashlib.sha256(b"[]").hexdigest(),
                    "delta_record_count": 0,
                    "delta_sha256": hashlib.sha256(b"[]").hexdigest(),
                    "batch_count": 0,
                }
            ],
        )
        with pytest.raises(HTTPException) as error:
            complete_snapshot(
                payload,
                request(),
                verified(source, "snapshot-older-complete"),
                db,
            )
        assert error.value.status_code == 409
        assert "已隔离" in error.value.detail

        stale = db.scalar(
            select(ExternalSyncSnapshot).where(
                ExternalSyncSnapshot.snapshot_id == "snapshot-older"
            )
        )
        assert stale is not None
        assert stale.status == "rejected_stale"
        assert db.get(ExternalSyncSnapshot, newer.id).status == "complete"


def test_employee_snapshot_cannot_project_into_user_directory_by_default(monkeypatch):
    monkeypatch.setattr(
        integrations.settings,
        "edge_sync_legacy_personnel_projection_enabled",
        False,
    )
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    snapshot_at = datetime(2026, 8, 30, 3, tzinfo=timezone.utc)
    delta = {
        "business_key": "employee:account-safe-1",
        "source_updated_at": None,
        "operation": "upsert",
        "data": {
            "accountId": "account-safe-1",
            "name": "只读人员",
            "mobile": "13800000031",
            "status": 1,
            "isDelete": 0,
            "loginEligible": True,
        },
    }
    final = {key: value for key, value in delta.items() if key != "operation"}
    batch_payload = EdgeSyncSnapshotBatchIn(
        snapshot_id="snapshot-safe-employee",
        scope_key="all",
        sync_mode="full",
        company_id="company-nio",
        org_code="org-nio",
        entity_type="employee",
        snapshot_at=snapshot_at,
        sequence=1,
        total_sequences=1,
        records=[delta],
    )
    complete_payload = EdgeSyncSnapshotCompleteIn(
        snapshot_id="snapshot-safe-employee",
        scope_key="all",
        sync_mode="full",
        company_id="company-nio",
        org_code="org-nio",
        snapshot_at=snapshot_at,
        entities=[
            {
                "entity_type": "employee",
                "final_record_count": 1,
                "final_sha256": integrations._records_sha256([final]),
                "delta_record_count": 1,
                "delta_sha256": integrations._records_sha256([delta]),
                "batch_count": 1,
            }
        ],
    )

    with Session(engine) as db:
        receive_snapshot_batch(
            batch_payload,
            request(),
            verified("edge-safe", "employee-batch"),
            db,
        )
        result = complete_snapshot(
            complete_payload,
            request(),
            verified("edge-safe", "employee-complete"),
            db,
        )
        assert result["personnel"]["status"] == "deferred"
        assert db.scalar(select(OamPersonnelBinding)) is None


def test_snapshot_sequence_cannot_be_reused_by_a_different_batch():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    snapshot_at = datetime(2026, 8, 30, 4, tzinfo=timezone.utc)

    def payload(business_key: str) -> EdgeSyncSnapshotBatchIn:
        return EdgeSyncSnapshotBatchIn(
            snapshot_id="snapshot-sequence-guard",
            scope_key="warehouse:WH-1",
            sync_mode="full",
            company_id="company-nio",
            org_code="org-nio",
            entity_type="inventory",
            snapshot_at=snapshot_at,
            sequence=1,
            total_sequences=1,
            records=[
                {
                    "business_key": business_key,
                    "source_updated_at": None,
                    "operation": "upsert",
                    "data": {"quantity": 1},
                }
            ],
        )

    with Session(engine) as db:
        receive_snapshot_batch(
            payload("inventory:first"),
            request(),
            verified("edge-sequence", "sequence-batch-first", b"first"),
            db,
        )
        with pytest.raises(HTTPException) as error:
            receive_snapshot_batch(
                payload("inventory:second"),
                request(),
                verified("edge-sequence", "sequence-batch-second", b"second"),
                db,
            )

        assert error.value.status_code == 409
        assert "序号" in error.value.detail
        assert len(list(db.scalars(select(ExternalSyncSnapshotBatch)))) == 1


def test_batch_id_cannot_be_reused_across_snapshots():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    snapshot_at = datetime(2026, 8, 30, 5, tzinfo=timezone.utc)

    def payload(snapshot_id: str) -> EdgeSyncSnapshotBatchIn:
        return EdgeSyncSnapshotBatchIn(
            snapshot_id=snapshot_id,
            scope_key="warehouse:WH-1",
            sync_mode="full",
            company_id="company-nio",
            org_code="org-nio",
            entity_type="inventory",
            snapshot_at=snapshot_at,
            sequence=1,
            total_sequences=1,
            records=[
                {
                    "business_key": "inventory:first",
                    "source_updated_at": None,
                    "operation": "upsert",
                    "data": {"quantity": 1},
                }
            ],
        )

    with Session(engine) as db:
        receive_snapshot_batch(
            payload("snapshot-batch-owner-one"),
            request(),
            verified("edge-batch-owner", "shared-batch-id", b"same-body"),
            db,
        )
        with pytest.raises(HTTPException) as error:
            receive_snapshot_batch(
                payload("snapshot-batch-owner-two"),
                request(),
                verified("edge-batch-owner", "shared-batch-id", b"same-body"),
                db,
            )

        assert error.value.status_code == 409
        assert "其他快照" in error.value.detail
        db.rollback()
        assert len(list(db.scalars(select(ExternalSyncSnapshot)))) == 1


def test_cloud_scope_namespace_cannot_be_relabeled_to_another_org():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    snapshot_at = datetime(2026, 8, 30, 6, tzinfo=timezone.utc)

    def payload(snapshot_id: str, company_id: str, org_code: str):
        return EdgeSyncSnapshotBatchIn(
            snapshot_id=snapshot_id,
            scope_key="work-orders:recent-30d",
            sync_mode="full",
            company_id=company_id,
            org_code=org_code,
            entity_type="work_order",
            snapshot_at=snapshot_at,
            sequence=1,
            total_sequences=1,
            records=[
                {
                    "business_key": f"work-order:{snapshot_id}",
                    "source_updated_at": snapshot_at,
                    "operation": "upsert",
                    "data": {
                        "id": snapshot_id,
                        "code": snapshot_id,
                        "statusCode": "processing",
                        "executorId": "executor-1",
                        "authCompanyId": company_id,
                        "province": "浙江省",
                        "updateTime": "2026-08-30 14:00:00",
                    },
                }
            ],
        )

    with Session(engine) as db:
        receive_snapshot_batch(
            payload("snapshot-bound-scope-one", "company-nio", "org-nio"),
            request(),
            verified("edge-scope-owner", "bound-scope-first", b"first"),
            db,
        )
        with pytest.raises(HTTPException) as error:
            receive_snapshot_batch(
                payload(
                    "snapshot-bound-scope-two",
                    "company-other",
                    "org-other",
                ),
                request(),
                verified("edge-scope-owner", "bound-scope-second", b"second"),
                db,
            )

        assert error.value.status_code == 409
        assert "已绑定其他企业或组织" in error.value.detail
        db.rollback()
        snapshots = tuple(db.scalars(select(ExternalSyncSnapshot)).all())
        assert len(snapshots) == 1
        assert snapshots[0].company_id == "company-nio"
        assert snapshots[0].org_code == "org-nio"


def test_receiver_rejects_retired_plaintext_work_order_details_before_staging():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    payload = EdgeSyncSnapshotBatchIn(
        snapshot_id="snapshot-retired-work-order-detail",
        scope_key="work-orders:recent-30d",
        sync_mode="full",
        company_id="company-nio",
        org_code="org-nio",
        entity_type="work_order_detail",
        snapshot_at=datetime(2026, 8, 30, 7, tzinfo=timezone.utc),
        sequence=1,
        total_sequences=1,
        records=[
            {
                "business_key": "work-order-detail:one",
                "source_updated_at": None,
                "operation": "upsert",
                "data": {"customerMobile": "13800000000"},
            }
        ],
    )

    with Session(engine) as db:
        with pytest.raises(HTTPException) as error:
            receive_snapshot_batch(
                payload,
                request(),
                verified("edge-retired-detail", "retired-detail-batch"),
                db,
            )
        assert error.value.status_code == 410
        assert db.scalar(select(ExternalSyncSnapshot)) is None


def test_receiver_rejects_work_order_fields_outside_minimal_whitelist():
    engine = create_engine("sqlite+pysqlite:///:memory:")
    Base.metadata.create_all(engine)
    payload = EdgeSyncSnapshotBatchIn(
        snapshot_id="snapshot-work-order-extra-field",
        scope_key="work-orders:recent-30d",
        sync_mode="full",
        company_id="company-nio",
        org_code="org-nio",
        entity_type="work_order",
        snapshot_at=datetime(2026, 8, 30, 8, tzinfo=timezone.utc),
        sequence=1,
        total_sequences=1,
        records=[
            {
                "business_key": "work-order:WT-EXTRA",
                "source_updated_at": datetime(
                    2026, 8, 30, 8, tzinfo=timezone.utc
                ),
                "operation": "upsert",
                "data": {
                    "id": "work-order-extra",
                    "code": "WT-EXTRA",
                    "statusCode": "processing",
                    "executorId": "executor-1",
                    "authCompanyId": "company-nio",
                    "province": "浙江省",
                    "updateTime": "2026-08-30 16:00:00",
                    "customerMobile": "13800000000",
                },
            }
        ],
    )

    with Session(engine) as db:
        with pytest.raises(HTTPException) as error:
            receive_snapshot_batch(
                payload,
                request(),
                verified("edge-extra-field", "extra-field-batch"),
                db,
            )
        assert error.value.status_code == 409
        assert "七字段白名单" in error.value.detail
        assert db.scalar(select(ExternalSyncSnapshot)) is None
