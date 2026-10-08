"""Real loss approval/ledger/file services; scrap preparation writes nothing."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.formal_access import load_formal_principal
from app.foundation_models import FileObject
from app.inventory_models import CustodyAssignment
from app.stock_scrap_schemas import ScrapPreview
from app.formal_services import formal_files, stock_scrap_plan as plan
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.inventory_posting import InventoryPostingError
from test_formal_files_service import FakeStorage, SECRET
from test_stock_loss_dispositions import (
    db, world, stock, allowed, evidence, regional, headquarters, approved,
    prepare, snapshot, second_report,
)


def upload(db, actor, monkeypatch):
    # Keep the legacy engineer fixture's scoped loader unchanged outside this
    # upload; the new executor's upload itself uses the real identity loader.
    with monkeypatch.context() as scoped:
        scoped.setattr(formal_files, 'load_formal_principal', load_formal_principal)
        return _upload(db, actor)


def _upload(db, actor):
    storage = FakeStorage()
    result = formal_files.create_file_upload_intent(db, actor=actor,
        command=formal_files.FileUploadIntentInput(purpose='stock_loss_evidence',
            original_filename='报废实物核验.jpg', sha256='9' * 64, size_bytes=128, mime_type='image/jpeg'),
        idempotency_key=uuid4().hex, idempotency_hmac_secret=SECRET,
        trace_request_id=uuid4().hex, storage=storage, upload_ttl_seconds=60)
    row = db.get(FileObject, result.file_id)
    storage.materialize(row)
    formal_files.complete_file_upload(db, actor=actor, file_id=row.id,
        trace_request_id=uuid4().hex, storage=storage)
    db.commit()
    return row


def request(db, approved, monkeypatch, kind='scrap'):
    source = prepare(db, approved, kind)
    file = upload(db, approved.actor, monkeypatch)
    return ScrapPreview(source=dict(kind='original', **source.model_dump()),
        execution_reason='已核对批准报废的实物', evidence_file_ids=(file.id,))


def test_original_scrap_prepares_exact_external_boundary_query_only(db, allowed, approved, monkeypatch):
    command = request(db, approved, monkeypatch)
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    result = plan.prepare(db, actor=approved.actor, request=command)
    assert plan.prepare(db, actor=approved.actor, request=command).plan_hash == result.plan_hash
    value = result.document
    assert value['stage'] == 'scrap_stock_preparation_only' and value['stock_effect'] == 'none'
    assert value['target_account_id'] is None and value['external_boundary_code'] == 'stock_operation_scrap'
    assert value['quantity'] == '1.000' and value['predecessor_reversal_id'] is None
    assert len(value['serial_ids']) == int(allowed.tracked)
    assert all(row['lifecycle_before'] == 'active' and row['lifecycle_after'] == 'scrapped' for row in value['serials'])
    assert 'scrap_operation_id' not in value and 'storage_key' not in result.document_json
    assert snapshot(db) == before and not db.new and not db.dirty and not db.deleted


@pytest.mark.parametrize('kind', ['restore_available', 'convert_damaged', 'return_to_region'])
def test_non_scrap_approval_cannot_produce_a_scrap_plan(db, approved, monkeypatch, kind):
    command = request(db, approved, monkeypatch, kind)
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        plan.prepare(db, actor=approved.actor, request=command)
    assert error.value.code == 'stock_scrap_approval_required' and snapshot(db) == before


def test_shared_frozen_account_preserves_other_reports(db, allowed, approved, regional, monkeypatch):
    command = request(db, approved, monkeypatch)
    second_report(db, allowed, regional, monkeypatch)
    allowed.world.current_principal = approved.actor
    before = snapshot(db)
    result = plan.prepare(db, actor=approved.actor, request=command).document
    assert result['quantity'] == '1.000' and result['source_balance_quantity'] == '2.000'
    assert len(result['frozen_holds_before']['lines']) == 2
    assert result['serial_ids'] == ([str(allowed.serials[3].id)] if allowed.tracked else [])
    assert snapshot(db) == before


@pytest.mark.parametrize('change', ['permission', 'custody', 'evidence', 'approval_hash'])
def test_current_authority_custody_evidence_and_exact_approval_are_required(db, allowed, approved, monkeypatch, change):
    command = request(db, approved, monkeypatch)
    actor = approved.actor
    if change == 'permission':
        actor = replace(actor, entitlements=tuple(e for e in actor.entitlements if e.action != 'dispose_loss'))
        allowed.world.current_principal = actor
    elif change == 'custody':
        row = db.scalar(select(CustodyAssignment).where(CustodyAssignment.location_id == allowed.account.location_id))
        row.valid_to = datetime.now(timezone.utc) - timedelta(seconds=1)
        db.commit()
    elif change == 'evidence':
        command = command.model_copy(update={'evidence_file_ids': (uuid4(),)})
    else:
        command = command.model_copy(update={'source': command.source.model_copy(
            update={'expected_headquarters_review_hash': 'f' * 64})})
    before = snapshot(db)
    with pytest.raises(InventoryReadError):
        plan.prepare(db, actor=actor, request=command)
    assert snapshot(db) == before


def test_late_evidence_change_invalidates_the_complete_plan(db, approved, monkeypatch):
    command = request(db, approved, monkeypatch)
    actual = plan._evidence
    calls = []
    def changed(*args, **kwargs):
        result = actual(*args, **kwargs)
        calls.append(1)
        return result if len(calls) == 1 else [dict(result[0], sha256='f' * 64)]
    monkeypatch.setattr(plan, '_evidence', changed)
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        plan.prepare(db, actor=approved.actor, request=command)
    assert error.value.code == 'stock_scrap_read_changed' and len(calls) == 2
    assert snapshot(db) == before


def test_authority_revocation_during_last_evidence_read_cannot_return_a_plan(db, allowed, approved, monkeypatch):
    command = request(db, approved, monkeypatch)
    actual = plan._evidence
    calls = []
    def revoke(*args, **kwargs):
        result = actual(*args, **kwargs)
        calls.append(1)
        if len(calls) == 2:
            allowed.world.current_principal = replace(approved.actor,
                authorization_version=approved.actor.authorization_version + 1)
        return result
    monkeypatch.setattr(plan, '_evidence', revoke)
    before = snapshot(db)
    with pytest.raises(InventoryPostingError) as error:
        plan.prepare(db, actor=approved.actor, request=command)
    assert error.value.code == 'actor_principal_stale' and len(calls) == 2
    assert snapshot(db) == before
