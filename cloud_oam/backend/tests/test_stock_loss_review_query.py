from dataclasses import replace
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.formal_services import stock_loss_review_query as query
from app.formal_services.inventory_query import InventoryReadError
from app.foundation_models import AuditEvent, AuditChainHead
from app.foundation_models import FileObject
from app.stock_operation_models import StockOperationLine, StockLossFile
from app.formal_services import stock_loss_review_evidence as evidence_read
from formal_file_integrity import FormalFileError
from test_stock_loss_review_recovery import db, world, stock, allowed, evidence, regional, headquarters, review_world, state


def test_scoped_pending_and_detail_are_read_only_and_bind_the_original_lines(db, review_world):
    w=review_world; before=state(db)
    db.execute(text('PRAGMA query_only=ON'))
    result=query.list_review_reports(db,actor=w.actor,stage=w.stage)
    assert len(result.items)==1 and result.next_after_id is None
    report=result.items[0]
    expected='awaiting_regional' if w.stage=='regional' else 'awaiting_headquarters'
    assert report.approval_stage==expected and report.approval_stock_effect=='none'
    assert report.submission_plan_hash==w.request.expected_submission_plan_hash
    assert {r.line_id for r in report.lines}==set(db.scalars(select(StockOperationLine.id)
        .where(StockOperationLine.operation_id==w.request.operation_id)))
    detail=query.review_report_detail(db,actor=w.actor,stage=w.stage,operation_id=w.request.operation_id)
    assert detail.report==report
    assert 'idempotency_key' not in result.model_dump_json()
    assert 'stock_account_id' not in result.model_dump_json()
    assert 'request_id' not in result.model_dump_json()
    assert state(db)==before and not db.new and not db.dirty


def test_completed_review_leaves_pending_but_remains_in_history(db, review_world):
    w=review_world; reviewed=w.commit(); before=state(db)
    assert query.list_review_reports(db,actor=w.actor,stage=w.stage).items==()
    report=query.list_review_reports(db,actor=w.actor,stage=w.stage,view='all').items[0]
    assert (report.regional_review if w.stage=='regional' else report.headquarters_review).review_id==reviewed.review_id
    assert report.approval_stage==('awaiting_headquarters' if w.stage=='regional' else 'approved')
    assert state(db)==before


def test_write_permission_is_independent_of_current_read(db, allowed, review_world):
    w=review_world
    actor=replace(w.actor,entitlements=tuple(e for e in w.actor.entitlements if e.action!=w.service.ACTION))
    allowed.world.current_principal=actor
    assert query.review_report_detail(db,actor=actor,stage=w.stage,operation_id=w.request.operation_id).report.operation_id==w.request.operation_id
    actor=replace(actor,entitlements=())
    allowed.world.current_principal=actor
    with pytest.raises(InventoryReadError):query.list_review_reports(db,actor=actor,stage=w.stage)


def test_borrowed_read_entitlement_does_not_enable_an_approval_role(db, allowed, review_world):
    w=review_world
    actor=replace(w.actor,entitlements=tuple(replace(e,assignment_id=uuid4()) if e.action=='read' else e for e in w.actor.entitlements))
    allowed.world.current_principal=actor
    with pytest.raises(InventoryReadError):query.list_review_reports(db,actor=actor,stage=w.stage)


def test_corrupt_one_report_is_blocked_in_queue_and_refused_in_detail(db, review_world):
    w=review_world
    row=db.scalar(select(AuditEvent).where(AuditEvent.aggregate_type=='stock_operation_order',
        AuditEvent.aggregate_id==str(w.request.operation_id)))
    row.after_jsonb={};db.commit()
    result=query.list_review_reports(db,actor=w.actor,stage=w.stage)
    assert result.items[0].model_dump()=={'availability':'blocked','operation_id':w.request.operation_id,
        'reason_code':'stock_loss_review_evidence_unavailable'}
    with pytest.raises(InventoryReadError):
        query.review_report_detail(db,actor=w.actor,stage=w.stage,operation_id=w.request.operation_id)


def test_mid_read_audit_change_rejects_the_entire_result(db, review_world, monkeypatch):
    w=review_world;original=query._project
    def changed(*args,**kwargs):
        result=original(*args,**kwargs)
        head=db.scalar(select(AuditChainHead).where(AuditChainHead.stream_key=='inventory'))
        head.version+=1;db.flush()
        return result
    monkeypatch.setattr(query,'_project',changed)
    with pytest.raises(InventoryReadError) as caught:query.list_review_reports(db,actor=w.actor,stage=w.stage)
    assert caught.value.code=='stock_loss_review_query_changed'


@pytest.mark.parametrize('limit',[True,0,21])
def test_page_bounds_are_strict(db, review_world, limit):
    with pytest.raises(InventoryReadError):
        query.list_review_reports(db,actor=review_world.actor,stage=review_world.stage,limit=limit)


def test_reviewer_can_read_only_bound_evidence_with_current_read_authority(db, allowed, review_world):
    w=review_world
    file_id=db.scalar(select(StockLossFile.file_id).where(StockLossFile.operation_id==w.request.operation_id))
    row=db.get(FileObject,file_id);before=state(db)
    actor=replace(w.actor,entitlements=tuple(e for e in w.actor.entitlements if e.action!=w.service.ACTION))
    allowed.world.current_principal=actor
    db.execute(text('PRAGMA query_only=ON'))
    proof=evidence_read.authorize_bound_loss_evidence(db,actor=actor,row=row,foreign_bindings_present=False)
    assert proof=={'binding_type':'stock_loss_report','binding_count':1,
        'operation_id':str(w.request.operation_id),'access':w.stage+'_current_read'}
    assert state(db)==before
    actor=replace(actor,entitlements=())
    allowed.world.current_principal=actor
    with pytest.raises(FormalFileError):
        evidence_read.authorize_bound_loss_evidence(db,actor=actor,row=row,foreign_bindings_present=False)


def test_foreign_attachment_binding_blocks_review_evidence(db, review_world):
    w=review_world
    file_id=db.scalar(select(StockLossFile.file_id).where(StockLossFile.operation_id==w.request.operation_id))
    with pytest.raises(FormalFileError):
        evidence_read.authorize_bound_loss_evidence(db,actor=w.actor,row=db.get(FileObject,file_id),foreign_bindings_present=True)


@pytest.mark.parametrize("review_world", ["regional"], indirect=True)
def test_region_cannot_list_or_open_a_report_outside_its_current_grant(db, allowed, review_world, monkeypatch):
    w=review_world;other_scope=str(uuid4())
    actor=replace(w.actor,assignments=tuple(replace(g,scope_id=other_scope) for g in w.actor.assignments),
        entitlements=tuple(replace(e,scope_id=other_scope) for e in w.actor.entitlements))
    allowed.world.current_principal=actor
    def leaked(*args,**kwargs):raise AssertionError('out-of-scope report projection was attempted')
    monkeypatch.setattr(query,'_project',leaked)
    assert query.list_review_reports(db,actor=actor,stage='regional').items==()
    with pytest.raises(InventoryReadError) as caught:
        query.review_report_detail(db,actor=actor,stage='regional',operation_id=w.request.operation_id)
    assert caught.value.status_code==404


@pytest.mark.parametrize('review_world', ['regional'], indirect=True)
def test_sparse_pending_pages_advance_without_skipping_reports(db, allowed, regional, review_world):
    from test_stock_loss_plan import command
    from app.formal_services import formal_files, stock_loss_commands, stock_loss_plan
    from app.stock_loss_schemas import StockLossSubmitIn
    from test_formal_files_service import FakeStorage, SECRET
    w=review_world;allowed.world.current_principal=allowed.actor;storage=FakeStorage()
    upload=formal_files.create_file_upload_intent(db,actor=allowed.actor,
        command=formal_files.FileUploadIntentInput(purpose='stock_loss_evidence',original_filename='另一单照片.jpg',
            sha256='a'*64,size_bytes=128,mime_type='image/jpeg'),idempotency_key=uuid4().hex,
        idempotency_hmac_secret=SECRET,trace_request_id=uuid4().hex,storage=storage,upload_ttl_seconds=60)
    row=db.get(FileObject,upload.file_id);storage.materialize(row)
    formal_files.complete_file_upload(db,actor=allowed.actor,file_id=row.id,trace_request_id=uuid4().hex,storage=storage)
    value=command(allowed,(row,))
    if allowed.tracked:
        sn=allowed.serials[4]
        proof=value.lines[0].serial_verifications[0].model_copy(update={'serial_id':sn.id,'serial_no':sn.serial_no,'qr_code':sn.qr_code})
        value=value.model_copy(update={'lines':(value.lines[0].model_copy(update={'serial_verifications':(proof,)}),)})
    preview,_=stock_loss_plan.preview_loss(db,actor=allowed.actor,request=value)
    other=stock_loss_commands.submit_loss(db,actor=allowed.actor,request=StockLossSubmitIn(**value.model_dump(),
        expected_plan_hash=preview.plan_hash,request_id=uuid4().hex,idempotency_key=uuid4().hex));db.commit()
    allowed.world.current_principal=w.actor
    reports={regional.submission.operation_id:regional.submission,other.operation_id:other};first_id,second_id=sorted(reports)
    original=regional.request.model_copy(update={'operation_id':first_id,'expected_submission_plan_hash':reports[first_id].plan_hash})
    w.service.verify_regional_loss(db,actor=w.actor,request=original);db.commit()
    before=state(db);db.execute(text('PRAGMA query_only=ON'))
    page=query.list_review_reports(db,actor=w.actor,stage='regional',limit=1)
    assert page.items==() and page.next_after_id==first_id
    next_page=query.list_review_reports(db,actor=w.actor,stage='regional',limit=1,after_id=page.next_after_id)
    assert [r.operation_id for r in next_page.items]==[second_id] and next_page.next_after_id is None
    history=query.list_review_reports(db,actor=w.actor,stage='regional',limit=1,view='all')
    assert history.items[0].operation_id==first_id and history.next_after_id==first_id and state(db)==before


def test_actual_download_intent_audit_and_revoked_read(db, allowed, review_world):
    from app.formal_services import formal_files
    from test_formal_files_service import FakeStorage
    from test_work_order_removed_registration import inventory
    w=review_world;identifier=db.scalar(select(StockLossFile.file_id).where(StockLossFile.operation_id==w.request.operation_id))
    storage=FakeStorage();stock_before=inventory(db);trace=uuid4().hex
    actor=replace(w.actor,entitlements=tuple(e for e in w.actor.entitlements if e.action!=w.service.ACTION))
    allowed.world.current_principal=actor
    result=formal_files.create_file_download_intent(db,actor=actor,file_id=identifier,
        trace_request_id=trace,storage=storage,download_ttl_seconds=60);db.commit()
    assert result.file_id==identifier and result.purpose=='stock_loss_evidence'
    assert len(storage.download_calls)==1 and inventory(db)==stock_before
    audit=db.scalar(select(AuditEvent).where(AuditEvent.action=='file.download_intent.created',AuditEvent.request_id==trace))
    assert audit is not None and audit.actor_user_id==actor.user_id
    assert audit.after_jsonb['operation_id']==str(w.request.operation_id) and audit.after_jsonb['access']==w.stage+'_current_read'
    before=state(db);denied=replace(actor,entitlements=());allowed.world.current_principal=denied
    with pytest.raises(FormalFileError):
        formal_files.create_file_download_intent(db,actor=denied,file_id=identifier,
            trace_request_id=uuid4().hex,storage=storage,download_ttl_seconds=60)
    db.rollback();assert len(storage.download_calls)==1 and state(db)==before



def test_real_region_grant_requires_region_company_scope(db, regional):
    from app.formal_access import load_formal_principal, FormalAccessError
    from app.foundation_models import RoleAssignment, Organization
    from test_formal_access import make_organization
    assignment=db.scalar(select(RoleAssignment).where(RoleAssignment.user_id==regional.actor.user_id,
        RoleAssignment.scope_type=='organization'))
    hq=db.scalar(select(Organization.id).where(Organization.org_type=='headquarters'))
    assert hq is not None
    assignment.scope_id=str(hq);db.commit()
    with pytest.raises(FormalAccessError):load_formal_principal(db,regional.actor.user_id)
    other=make_organization(db,name='Another review region',org_type='region_company')
    assignment.scope_id=str(other.id);db.commit()
    actual=load_formal_principal(db,regional.actor.user_id)
    assert any(g.scope_id==str(other.id) for g in actual.assignments)
