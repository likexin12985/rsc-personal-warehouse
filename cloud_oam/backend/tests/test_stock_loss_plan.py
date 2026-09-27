"""Whole report preflight: exact stock, own completed evidence and no writes.

Identity is the existing synthetic stock fixture. File creation/completion use
the real service and an in-memory object store; PG16 has a separate role gate.
"""
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from pydantic import ValidationError
from sqlalchemy import select, text

from app.foundation_models import FileObject
from app.models import User
from app.formal_services import formal_files, stock_loss_plan as plan
from app.formal_services.inventory_query import InventoryReadError
from app.stock_loss_schemas import StockLossPreviewIn
from test_formal_files_service import FakeStorage, SECRET
from test_stock_loss_sources import db, world, stock, allowed, request
from test_stock_loss_source_routes import client, private, PATH
from test_work_order_removed_registration import counts, inventory


@pytest.fixture
def evidence(db, allowed, monkeypatch):
    monkeypatch.setattr(formal_files, 'lock_formal_principal_graph', lambda *_: None)
    monkeypatch.setattr(formal_files, 'load_formal_principal', lambda *_: allowed.world.current_principal)
    storage = FakeStorage()
    rows = []
    for index in range(2):
        result = formal_files.create_file_upload_intent(db, actor=allowed.actor,
            command=formal_files.FileUploadIntentInput(purpose='stock_loss_evidence',
                original_filename=f'报损照片{index}.jpg', sha256=str(index+1)*64,
                size_bytes=128, mime_type='image/jpeg'), idempotency_key=uuid4().hex,
            idempotency_hmac_secret=SECRET, trace_request_id=uuid4().hex,
            storage=storage, upload_ttl_seconds=60)
        row = db.get(FileObject, result.file_id)
        storage.materialize(row)
        formal_files.complete_file_upload(db, actor=allowed.actor, file_id=row.id,
            trace_request_id=uuid4().hex, storage=storage)
        rows.append(row)
    db.flush()
    return tuple(rows)


def command(allowed, evidence, **changes):
    return StockLossPreviewIn(**(dict(**request(allowed).model_dump(), reason='现场发现物料损坏，申请核实',
        evidence_file_ids=tuple(row.id for row in evidence)) | changes))


def test_preview_covers_whole_intent_is_stable_and_query_only(db, allowed, evidence):
    before = counts(db), inventory(db)
    value = command(allowed, evidence)
    db.execute(text('PRAGMA query_only=ON'))
    result, document = plan.preview_loss(db, actor=allowed.actor, request=value)
    second, _ = plan.preview_loss(db, actor=allowed.actor,
        request=value.model_copy(update={'evidence_file_ids':tuple(reversed(value.evidence_file_ids))}))
    assert result.plan_hash == second.plan_hash and result.request_hash == second.request_hash
    assert result.planning_status == 'preview_only' and result.reason == value.reason
    assert {row.file_id for row in result.evidence} == {row.id for row in evidence}
    assert 'storage_key' not in result.model_dump_json() and 'qr_code' not in result.model_dump_json()
    assert document['intent']['operation_type'] == 'loss_report'
    assert all('metadata_sha256' in item for item in document['evidence'])
    changed, _ = plan.preview_loss(db, actor=allowed.actor, request=command(allowed,evidence,reason='另一项报损原因'))
    assert changed.request_hash != result.request_hash and changed.plan_hash != result.plan_hash
    changed, _ = plan.preview_loss(db, actor=allowed.actor, request=command(allowed,evidence[:1]))
    assert changed.request_hash != result.request_hash and changed.plan_hash != result.plan_hash
    assert (counts(db), inventory(db)) == before and not db.new and not db.dirty


@pytest.mark.parametrize('change', ['missing', 'pending', 'legacy', 'other_uploader', 'old_authority', 'future', 'content_hash', 'provider'])
def test_untrusted_or_uncompleted_evidence_refuses_entire_command(db, allowed, evidence, change):
    row = evidence[0]
    value = command(allowed,evidence)
    if change=='missing':
        value=value.model_copy(update={'evidence_file_ids':(uuid4(),)})
    elif change=='pending':
        row.status='pending';row.metadata_jsonb={k:v for k,v in row.metadata_jsonb.items() if k!='completion'}
    elif change=='legacy':
        row.metadata_jsonb={}
    elif change=='other_uploader':
        row.uploaded_by=db.scalar(select(User.id).where(User.id != allowed.actor.user_id).order_by(User.id).limit(1))
        assert row.uploaded_by is not None
        row.metadata_jsonb={**row.metadata_jsonb,'uploader_user_id':row.uploaded_by}
    elif change=='old_authority':
        row.metadata_jsonb={**row.metadata_jsonb,'authorization_version':allowed.actor.authorization_version+1}
    elif change=='provider':
        row.metadata_jsonb={**row.metadata_jsonb,'provider':'unverified-provider'}
    elif change=='future':
        row.metadata_jsonb={**row.metadata_jsonb,'completion':{**row.metadata_jsonb['completion'],
            'verified_at':(datetime.now(timezone.utc)+timedelta(days=1)).isoformat()}}
    else:
        row.sha256='f'*64
    db.flush()
    before=counts(db),inventory(db)
    with pytest.raises(InventoryReadError) as caught:
        plan.preview_loss(db,actor=allowed.actor,request=value)
    assert caught.value.code=='stock_loss_evidence_invalid'
    assert (counts(db),inventory(db))==before


def test_evidence_change_between_stock_reads_invalidates_preview(db, allowed, evidence, monkeypatch):
    original = plan._evidence
    calls = 0
    def changed(*args,**kwargs):
        nonlocal calls
        rows=original(*args,**kwargs);calls+=1
        # Simulate a different validated evidence snapshot observed at the
        # second read; do not weaken the real file integrity validator.
        if calls==2:
            rows=[{**rows[0],'metadata_sha256':'f'*64},*rows[1:]]
        return rows
    monkeypatch.setattr(plan,'_evidence',changed)
    before=counts(db),inventory(db)
    with pytest.raises(InventoryReadError,match='预检期间变化'):
        plan.preview_loss(db,actor=allowed.actor,request=command(allowed,evidence))
    assert (counts(db),inventory(db))==before


@pytest.mark.parametrize('field,value',[('reason','  '),('reason','损坏\x00'),('evidence_file_ids',())])
def test_reason_and_evidence_are_required(allowed, evidence, field, value):
    with pytest.raises(ValidationError):
        command(allowed,evidence,**{field:value})


def test_duplicate_evidence_is_not_a_second_proof(allowed, evidence):
    with pytest.raises(ValidationError):
        command(allowed,evidence,evidence_file_ids=(evidence[0].id,evidence[0].id))


def test_http_preview_is_private_has_no_stock_effect_and_submit_stays_closed(db, allowed, evidence, client):
    before=counts(db),inventory(db)
    value=command(allowed,evidence).model_dump(mode='json')
    response=client.post(PATH+'/preview',json=value)
    assert response.status_code==200,response.text
    assert response.json()['planning_status']=='preview_only'
    assert len(response.json()['evidence'])==2
    private(response)
    invalid=client.post(PATH+'/preview',json={**value,'evidence_file_ids':[]})
    assert invalid.status_code==422
    private(invalid)
    submit=client.post(PATH,json=value)
    assert submit.status_code==404
    private(submit)
    assert (counts(db),inventory(db))==before
