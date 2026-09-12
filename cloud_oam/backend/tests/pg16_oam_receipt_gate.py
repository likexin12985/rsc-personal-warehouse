"""Receipt scope and raw-SQL attacks on the gate's disposable PostgreSQL 16."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import uuid

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.foundation_models import ExternalObject, ExternalObjectMapping, SourceSystem, SyncRun
from app.inventory_models import OamReceiptEvidence, Shipment
from app.models import ExternalSyncCurrentRecord, ExternalSyncSnapshot, User
from app.formal_services.oam_receipt_projection import (
    OamReceiptProjectionError, publish_completed_oam_receipt_snapshot,
    record_failed_oam_receipt_snapshot, next_unpublished_oam_receipt_snapshot_id,
)
from app.oam_projection_security import (
    verify_oam_projection_database_boundary,
)
from app.oam_receipt_projection_security import (
    OamReceiptProjectionDatabaseBoundaryError, verify_oam_receipt_projection_database_boundary,
)
from app.oam_sync_scope_security import read_oam_sync_scope_boundary
from test_oam_receipt_projection import _stage_completed_snapshot


NOW = datetime(2026, 9, 12, 2, tzinfo=timezone.utc)


def _fixture(db, source, user, instance, scope, *, company='company-1', complete=True):
    key = uuid.uuid4().hex
    shipment = Shipment(
        id=uuid.uuid4(), shipment_no=f'PG16-R-{key}',
        source_location_id=uuid.uuid4(), target_location_id=uuid.uuid4(),
        target_person_id=None, carrier='test', tracking_no=key, status='shipped',
        shipped_at=NOW, idempotency_key_hash=hashlib.sha256(key.encode()).hexdigest(),
        request_hash='b'*64, actor_user_id=user.id, actor_person_id=uuid.uuid4(),
        authorization_version=1, created_at=NOW,
    )
    external = ExternalObject(source_system_id=source.id, entity_type='oam_receipt',
                              external_id=key, current_version_id=None, deleted_at=None)
    db.add_all([shipment, external]); db.flush()
    mapping = ExternalObjectMapping(external_object_id=external.id, local_object_type='shipment',
                                    local_object_id=str(shipment.id), status='approved',
                                    approved_by=user.id, approved_at=NOW, reason='Disposable exact fixture')
    payload = {'id': key, 'status': 'synced', 'sourceTime': '2026-09-12T02:00:00Z', 'sourceVersion': 'receipt-v1'}
    payload_json = json.dumps(payload, sort_keys=True, separators=(',', ':'))
    record = ExternalSyncCurrentRecord(
        source_system='starcharge_oam', source_instance=instance, scope_key=scope,
        entity_type='oam_receipt', business_key=f'oam-receipt:{key}', source_updated_at=NOW,
        payload_json=payload_json, payload_sha256=hashlib.sha256(payload_json.encode()).hexdigest(),
        last_snapshot_id=str(uuid.uuid4()),
    )
    db.add(mapping); db.flush()
    snapshot = _stage_completed_snapshot(db, record, snapshot_id=record.last_snapshot_id)
    db.add(record)
    snapshot.company_id = company
    if not complete:
        snapshot.status = 'receiving'; snapshot.completed_at = None
    db.flush()
    return {'shipment': shipment.id, 'external': external.id, 'mapping': mapping.id,
            'record': record.id, 'snapshot': snapshot.id, 'payload_hash': record.payload_sha256}


def assert_receipt_gate(projector_engine, migrator_engine, api_engine, backup_engine):
    """Called after historical migration probes; receipt facts intentionally remain."""
    verify_oam_projection_database_boundary(projector_engine)
    with pytest.raises(OamReceiptProjectionDatabaseBoundaryError, match='bindings.read_write_pair'):
        verify_oam_receipt_projection_database_boundary(projector_engine)
    nonce = uuid.uuid4().hex
    instance, scope = f'receipt-{nonce}', f'oam-receipts:{nonce}'
    failed_scope = f'oam-receipts:failed-{nonce}'
    with Session(migrator_engine) as db:
        source = db.scalar(select(SourceSystem).where(SourceSystem.code == 'starcharge_oam'))
        assert source is not None
        user = User(id=str(uuid.uuid4()), name='PG16 receipt mapping', mobile=f'1{uuid.uuid4().int % 10**10:010d}',
                    password_hash='test-only', role='admin', is_active=True, account_status='active',
                    authorization_version=1, require_password_change=False)
        db.add(user); db.flush()
        good = _fixture(db, source, user, instance, scope)
        other_company = _fixture(db, source, user, instance, scope, company='other-company')
        other_instance = _fixture(db, source, user, f'other-{nonce}', scope)
        incomplete = _fixture(db, source, user, instance, scope, complete=False)
        failed = _fixture(db, source, user, instance, failed_scope)
        duplicate_mapping = ExternalObjectMapping(external_object_id=failed['external'],
            local_object_type='shipment', local_object_id=str(good['shipment']), status='approved',
            approved_by=user.id, approved_at=NOW, reason='Deliberately ambiguous fixture')
        db.add(duplicate_mapping); db.flush(); duplicate_mapping_id = duplicate_mapping.id
        source_id = source.id
        db.commit()
    all_fixtures = (good, other_company, other_instance, incomplete, failed)

    def visible_ids(model):
        with Session(projector_engine) as db:
            return set(db.scalars(select(model.id)))

    # Owner joins in the helper must not expose even pre-existing objects when unbound.
    assert not ({x['external'] for x in all_fixtures} & visible_ids(ExternalObject))
    assert not ({x['shipment'] for x in all_fixtures} & visible_ids(Shipment))
    with migrator_engine.begin() as c:
        for bound_scope in (scope, failed_scope):
            c.execute(text("INSERT INTO oam_receipt_sync_scope_bindings "
                "(id,capability,source_instance,scope_key,company_id,org_code) "
                "VALUES (:id,'projector_read',:instance,:scope,'company-1','org-1')"),
                {'id':uuid.uuid4(), 'instance':instance, 'scope':bound_scope})
    with pytest.raises(OamReceiptProjectionDatabaseBoundaryError):
        verify_oam_receipt_projection_database_boundary(projector_engine)
    with migrator_engine.begin() as c:
        for bound_scope in (scope, failed_scope):
            c.execute(text("INSERT INTO oam_receipt_sync_scope_bindings "
                "(id,capability,source_instance,scope_key,company_id,org_code) "
                "VALUES (:id,'projector_write',:instance,:scope,'company-1','org-1')"),
                {'id':uuid.uuid4(), 'instance':instance, 'scope':bound_scope})
    verify_oam_receipt_projection_database_boundary(projector_engine)
    for model, key in ((ExternalObject,'external'), (ExternalObjectMapping,'mapping'),
                       (ExternalSyncCurrentRecord,'record'), (ExternalSyncSnapshot,'snapshot')):
        visible = visible_ids(model)
        assert good[key] in visible and failed[key] in visible
        assert all(x[key] not in visible for x in (other_company,other_instance,incomplete))
    assert good['shipment'] in visible_ids(Shipment)
    assert failed['shipment'] not in visible_ids(Shipment)  # ambiguous mapping is not a shipment grant

    def evidence_values():
        return dict(id=uuid.uuid4(), external_object_id=good['external'], shipment_id=good['shipment'],
                    status='synced', source_time=NOW, source_version='receipt-v1',
                    payload_sha256=good['payload_hash'], created_at=NOW)

    for mutation in ({'status':'exception'}, {'source_version':'forged'},
                     {'source_time':NOW+timedelta(seconds=1)}, {'payload_sha256':'0'*64},
                     {'shipment_id':other_company['shipment']}, {'external_object_id':other_company['external']}):
        with Session(projector_engine) as db:
            db.add(OamReceiptEvidence(**(evidence_values() | mutation)))
            with pytest.raises(DBAPIError): db.flush()
            db.rollback()
    with Session(migrator_engine) as db:
        assert db.scalar(select(func.count()).select_from(OamReceiptEvidence)) == 0

    with Session(api_engine) as db:
        db.add(OamReceiptEvidence(**evidence_values()))
        with pytest.raises(DBAPIError): db.flush()
        db.rollback()

    # Matching scope alone cannot forge a completed run or change its snapshot coordinates.
    with Session(migrator_engine) as db:
        snapshot = db.get(ExternalSyncSnapshot, good['snapshot'])
        run_values = dict(source_system_id=source_id, run_key=f"oam-receipt:{snapshot.id}",
                          scope_key=snapshot.scope_key, mode=snapshot.sync_mode,
                          watermark_to=snapshot.snapshot_at.isoformat(), manifest_sha256=snapshot.manifest_sha256,
                          status='validating', started_at=NOW, completed_at=None)
    for mutation in ({'run_key': 'oam-receipt:forged'}, {'manifest_sha256': '0'*64},
                     {'watermark_to': (NOW+timedelta(seconds=1)).isoformat()},
                     {'status': 'completed', 'completed_at': NOW}):
        with Session(projector_engine) as db:
            db.add(SyncRun(**(run_values | mutation)))
            with pytest.raises(DBAPIError): db.flush()
            db.rollback()

    # A genuine publication must complete SyncRun using only its status columns.
    with Session(projector_engine, expire_on_commit=False) as db:
        source = db.get(SourceSystem, source_id)
        first = publish_completed_oam_receipt_snapshot(db, source=source, snapshot_id=good['snapshot'])
        db.commit()
        assert first.projected_records == 1 and not first.duplicate
        replay = publish_completed_oam_receipt_snapshot(db, source=source, snapshot_id=good['snapshot'])
        db.commit()
        assert replay.duplicate and replay.sync_run_id == first.sync_run_id
    with Session(migrator_engine) as db:
        assert db.scalar(select(func.count()).select_from(OamReceiptEvidence)) == 1
        assert db.get(SyncRun, first.sync_run_id).status == 'completed'

    # Record a failed batch, skip it in the idle queue, then retry the exact batch.
    with Session(projector_engine) as db:
        source = db.get(SourceSystem, source_id)
        with pytest.raises(OamReceiptProjectionError) as ambiguous:
            publish_completed_oam_receipt_snapshot(db, source=source, snapshot_id=failed['snapshot'])
        assert ambiguous.value.code == 'oam_receipt_shipment_mapping_ambiguous'
        db.rollback()
        failed_id = record_failed_oam_receipt_snapshot(db, snapshot_id=failed['snapshot'],
                                                       failure_code='oam_receipt_shipment_mapping_ambiguous')
        db.commit()
        assert failed_id is not None
        assert next_unpublished_oam_receipt_snapshot_id(db) is None
    with Session(migrator_engine) as db:
        db.get(ExternalObjectMapping, duplicate_mapping_id).status = 'rejected'; db.commit()
    with Session(projector_engine) as db:
        result = publish_completed_oam_receipt_snapshot(db, source=db.get(SourceSystem,source_id), snapshot_id=failed['snapshot'])
        db.commit()
        assert result.sync_run_id == failed_id
    with Session(migrator_engine) as db:
        assert db.scalar(select(func.count()).select_from(OamReceiptEvidence)) == 2
        assert db.get(SyncRun, failed_id).status == 'completed'

    for statement in (
        "UPDATE oam_receipt_evidence SET status='exception'",
        "DELETE FROM oam_receipt_evidence",
        "UPDATE shipments SET status='exception'",
        "INSERT INTO oam_receipt_sync_scope_bindings (id) VALUES (gen_random_uuid())",
    ):
        with projector_engine.connect() as c:
            with pytest.raises(DBAPIError): c.execute(text(statement))
            c.rollback()
    with backup_engine.connect() as c:
        assert c.scalar(text('SELECT count(*) FROM oam_receipt_evidence')) == 2
        assert c.scalar(text('SELECT count(*) FROM shipments')) >= len(all_fixtures)

    # Every catalog change must be rejected by BOTH workers, including same-name
    # policy substitution, extra policy, helper body/ACL drift and disabled guard.
    drifts = (
        "ALTER POLICY external_objects_projector_select_receipt_0082 ON external_objects USING (true)",
        "CREATE POLICY receipt_attack ON shipments FOR SELECT TO star_oam_projector USING (true)",
        "GRANT EXECUTE ON FUNCTION rsc_oam_receipt_rls_check_0082(text,text,text,jsonb) TO edge_inbox",
        "ALTER FUNCTION rsc_oam_receipt_rls_check_0082(text,text,text,jsonb) SET search_path=public",
        "CREATE OR REPLACE FUNCTION rsc_oam_receipt_rls_check_0082(p_operation text,p_required_capability text,p_table_name text,p_row jsonb) RETURNS boolean LANGUAGE sql AS 'SELECT true'",
        "ALTER TABLE oam_receipt_sync_scope_bindings DROP CONSTRAINT ck_oam_receipt_binding_scope_0082",
        "ALTER TABLE oam_receipt_evidence DISABLE TRIGGER trg_oam_receipt_evidence_immutable_0081",
    )
    # Read catalog drift in the same transaction without changing session identity.
    # session_user remains migrator; inspect closure fields
    # independently of the separate session-binding check.
    with migrator_engine.connect() as c:
        for statement in drifts:
            with c.begin_nested() as savepoint:
                c.execute(text(statement))
                for role in ('star_oam_projector', 'edge_inbox'):
                    proof = read_oam_sync_scope_boundary(c, expected_role=role)
                    assert proof['boundary_ok'] is False
                    failures = set(proof['boundary_failures'].split(','))
                    assert failures - {'session_binding','revision_and_binding'}
                savepoint.rollback()
        c.rollback()
    verify_oam_receipt_projection_database_boundary(projector_engine)
