"""Commit expiry and existing pending-account reuse on owned synthetic PG16."""
from datetime import datetime,timedelta,timezone
from decimal import Decimal
from uuid import UUID,uuid4
import time

import pytest
from sqlalchemy import select,text,func
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import Role,RoleAssignment,FileObject
from app.inventory_models import StockAccount,StockBalance,FormalMaterial,InventorySerial,SerialCurrentPosition
from app.stock_operation_models import StockLossDisposition,StockLossRegionalReview,StockLossHeadquartersDecision,StockOperationLine
from app.stock_loss_schemas import StockLossPreviewIn,StockLossSubmitIn,StockLossRegionalReviewIn,StockLossHeadquartersReviewIn
from app.stock_loss_return_schemas import StockLossReturnExecuteIn
from app.formal_services import stock_loss_return_plan as plan,stock_loss_return_commands as commands
from app.formal_services import stock_loss_disposition_facts as facts
from pg16_stock_loss_disposition_gate import snapshot


def commit_expiry(context,request):
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    with Session(owner) as db:
        assignment=db.scalars(select(RoleAssignment).join(Role,Role.id==RoleAssignment.role_id)
            .where(RoleAssignment.user_id==context['admin_id'],Role.code=='admin')).one()
        identifier,old_end=assignment.id,assignment.valid_to
        deadline=db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=60)
        assignment.valid_to=deadline;db.commit()
    before=snapshot(owner)
    try:
        with Session(api) as db:
            actor=load_formal_principal(db,context['admin_id'])
            preview=plan.preview_loss_return(db,actor=actor,request=request)
            value=request.model_copy(update={'expected_plan_hash':preview['plan_hash']})
            result=commands.execute_loss_return(db,actor=actor,request=value)
            assert result['status']=='posted' and db.scalar(text('SELECT clock_timestamp()'))<deadline
            remaining=(deadline-db.scalar(text('SELECT clock_timestamp()'))).total_seconds()
            time.sleep(max(0,remaining)+.05)
            assert db.scalar(text('SELECT clock_timestamp()'))>deadline
            with pytest.raises(DBAPIError,match='0150 current headquarters disposition authority required') as error:
                db.commit()
            assert error.value.orig.sqlstate=='23514';db.rollback()
        assert snapshot(owner)==before
    finally:
        with Session(owner) as db:db.get(RoleAssignment,identifier).valid_to=old_end;db.commit()
    print('PG16 loss return '+context['tracking']+': complete command then real authority expiry rejects COMMIT; all facts roll back PASS',flush=True)
    return dict(realClockExpiry=True,completeCommandReturnedBeforeExpiry=True,commitRejected=True,fullRollback=True)


def reuse_pending(context,*,first,request):
    from app.formal_services import inventory_posting as posting,formal_files,stock_loss_commands,stock_loss_plan
    from app.formal_services import stock_loss_regional_reviews as regional,stock_loss_headquarters_reviews as headquarters
    from test_formal_files_service import FakeStorage,SECRET
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    tracked=context['tracking']=='serial';extra=uuid4() if tracked else None
    with Session(owner) as db:
        source=db.get(StockAccount,context['account_id']);material=db.get(FormalMaterial,source.material_id)
        sku=material.sku_code
        regional_actor=db.scalars(select(StockLossRegionalReview).where(
            StockLossRegionalReview.operation_id==UUID(first['operation_id']))).one().actor_user_id
        if tracked:
            at=datetime.now(timezone.utc)
            db.add(InventorySerial(id=extra,material_id=source.material_id,lot_id=source.lot_id,
                serial_no='LOSS-RETURN-REUSE-'+extra.hex,qr_code='LOSS-RETURN-REUSE-QR-'+extra.hex,
                lifecycle_status='active',created_at=at,updated_at=at))
        db.commit()
    # Synthetic replenishment uses the real API ledger and explicit test-only
    # external boundary; only serial identity masters are created by fixture owner.
    with Session(api) as db:
        key=uuid4().hex
        posting.post_inventory_transaction(db,actor=load_formal_principal(db,context['admin_id']),
            command=posting.InventoryPostingCommand(transaction_no='LOSS-RETURN-REUSE-IN-'+key,movement_type='inbound',
                source_document_type='synthetic_loss_return_reuse_fixture',source_document_id=key,posting_key=key,
                effective_at=datetime.now(timezone.utc),movements=(posting.InventoryMovementCommand(
                    from_account_id=None,to_account_id=context['account_id'],quantity=Decimal(1),serial_ids=(extra,) if tracked else (),
                    external_boundary_code='PG16_LOSS_RETURN_REUSE_FIXTURE'),)),idempotency_key=key,request_id=key)
        db.commit()
    storage=FakeStorage()
    with Session(api) as db:
        actor=load_formal_principal(db,context['engineer_id'])
        file=formal_files.create_file_upload_intent(db,actor=actor,
            command=formal_files.FileUploadIntentInput(purpose='stock_loss_evidence',original_filename='synthetic-return-reuse.jpg',
                size_bytes=128,mime_type='image/jpeg',sha256='c'*64),idempotency_key=uuid4().hex,
            idempotency_hmac_secret=SECRET,trace_request_id=uuid4().hex,storage=storage,upload_ttl_seconds=60)
        storage.materialize(db.get(FileObject,file.file_id))
        formal_files.complete_file_upload(db,actor=actor,file_id=file.file_id,trace_request_id=uuid4().hex,storage=storage)
        db.commit();file_id=file.file_id
    with Session(api) as db:
        proofs=[]
        if tracked:
            serial=db.get(InventorySerial,extra)
            proofs=[dict(serial_id=extra,serial_no=serial.serial_no,qr_code=serial.qr_code,sku_code=sku)]
        value=StockLossPreviewIn(operator_person_id=context['person_id'],reason='Synthetic independent loss reuses pending dimensions',
            evidence_file_ids=(file_id,),lines=(dict(stock_account_id=context['account_id'],quantity='1',serial_verifications=proofs),))
        actor=load_formal_principal(db,context['engineer_id']);preview,_=stock_loss_plan.preview_loss(db,actor=actor,request=value)
        loss=stock_loss_commands.submit_loss(db,actor=actor,request=StockLossSubmitIn(**value.model_dump(),
            expected_plan_hash=preview.plan_hash,request_id=uuid4().hex,idempotency_key=uuid4().hex));db.commit()
    with Session(api) as db:
        review=regional.verify_regional_loss(db,actor=load_formal_principal(db,regional_actor),request=StockLossRegionalReviewIn(
            operation_id=loss.operation_id,expected_submission_plan_hash=preview.plan_hash,comment='Synthetic independent regional check',
            request_id=uuid4().hex,idempotency_key=uuid4().hex));db.commit()
    with Session(api) as db:
        line=db.scalars(select(StockOperationLine).where(StockOperationLine.operation_id==loss.operation_id)).one()
        approval=headquarters.approve_headquarters_loss(db,actor=load_formal_principal(db,context['admin_id']),
            request=StockLossHeadquartersReviewIn(operation_id=loss.operation_id,expected_submission_plan_hash=preview.plan_hash,
                regional_review_id=review.review_id,expected_regional_review_hash=review.request_hash,
                decisions=(dict(line_id=line.id,disposition='return_to_region',reason='Synthetic second approved return'),),
                comment='Synthetic independent HQ check',request_id=uuid4().hex,idempotency_key=uuid4().hex));db.commit()
        decision=db.scalars(select(StockLossHeadquartersDecision).where(StockLossHeadquartersDecision.review_id==approval.review_id)).one()
        next_request=StockLossReturnExecuteIn(headquarters_decision_id=decision.id,expected_headquarters_review_hash=approval.request_hash,
            expected_submission_plan_hash=preview.plan_hash,target_location_id=request.target_location_id,
            transit_location_id=request.transit_location_id,expected_plan_hash='0'*64,request_id=uuid4().hex,idempotency_key=uuid4().hex)
    pending_id=UUID(first['target_account_id'])
    with Session(api) as db:
        actor=load_formal_principal(db,context['admin_id']);planned=plan.preview_loss_return(db,actor=actor,request=next_request)
        assert planned['pending_account_id']==str(pending_id)
        count=db.scalar(select(func.count()).select_from(StockAccount));old_quantity=db.get(StockBalance,pending_id).quantity
        next_request=next_request.model_copy(update={'expected_plan_hash':planned['plan_hash']})
        second=commands.execute_loss_return(db,actor=actor,request=next_request);db.commit()
    with Session(api) as db:
        assert second['target_account_id']==first['target_account_id'] and second['return_operation_id']!=first['return_operation_id']
        assert db.scalar(select(func.count()).select_from(StockAccount))==count
        assert db.get(StockBalance,pending_id).quantity==old_quantity+Decimal(1)
        for result in (first,second):assert facts.verified(db,row=db.get(StockLossDisposition,UUID(result['disposition_id'])))==result
        if tracked:assert db.get(SerialCurrentPosition,extra).stock_account_id==pending_id
    before=snapshot(owner)
    with Session(api) as db:
        assert commands.execute_loss_return(db,actor=load_formal_principal(db,context['admin_id']),request=next_request)==second
        db.commit()
    assert snapshot(owner)==before
    print('PG16 loss return '+context['tracking']+': independent second approval reuses existing pending account; both histories intact PASS',flush=True)
    return dict(existingAccountReused=True,noNewStockAccount=True,independentApproval=True,oldAndNewHistoryVerified=True,exactReplayNoAdditionalFacts=True)
