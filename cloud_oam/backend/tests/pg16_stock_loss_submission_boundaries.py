"""Real commit-time expiry and pooled loss holds on an owned synthetic PG16."""
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import time
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services import inventory_posting as posting, stock_loss_commands as commands
from app.formal_services import stock_loss_facts as facts, stock_loss_plan as plan
from app.foundation_models import FileObject, RoleAssignment
from app.inventory_models import FormalMaterial, InventorySerial, SerialCurrentPosition, StockAccount, StockBalance
from app.stock_loss_schemas import StockLossSubmitIn, StockLossPreviewIn
from app.stock_operation_models import StockOperationLine, StockOperationOrder, StockOperationSerial


def commit_expiry(context,value):
    from pg16_stock_loss_submit_gate import snapshot
    owner,api=(context['engines'][key] for key in ('star_oam_migrator','star_oam_api'))
    with Session(owner) as db:
        assignment=db.scalar(select(RoleAssignment).where(RoleAssignment.user_id==context['engineer_id']))
        identifier,old_end=assignment.id,assignment.valid_to
        # Allow the full source-proof/preview/command under a loaded local host.
        # Both sides of the real clock boundary and COMMIT refusal stay asserted.
        deadline=db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=45)
        assignment.valid_to=deadline;db.commit()
    try:
        before=snapshot(owner)
        with Session(api) as db:
            actor=load_formal_principal(db,context['engineer_id'])
            prepared,_=plan.preview_loss(db,actor=actor,request=value)
            pending=commands.submit_loss(db,actor=actor,request=value.model_copy(update={'expected_plan_hash':prepared.plan_hash}))
            assert pending.operation_id and db.scalar(text('SELECT clock_timestamp()'))<deadline
            # The complete normal command has returned. Only wall-clock time,
            # not forged audit/posted timestamps, now revokes this assignment.
            remaining=(deadline-db.scalar(text('SELECT clock_timestamp()'))).total_seconds()
            time.sleep(max(0,remaining)+.05)
            assert db.scalar(text('SELECT clock_timestamp()'))>deadline
            with pytest.raises(DBAPIError,match='0145 current submit_loss authority required') as caught:
                db.commit()
            assert caught.value.orig.sqlstate=='23514';db.rollback()
        assert snapshot(owner)==before
    finally:
        with Session(owner) as db:
            db.get(RoleAssignment,identifier).valid_to=old_end;db.commit()
    print('PG16 loss: complete normal submission expires before COMMIT; stock/audit/outbox/notification rollback PASS',flush=True)
    return dict(completeCommandBeforeExpiry=True,actualClockExpiry=True,commitRejected=True,fullRollback=True)


def shared_holds(context,first):
    from pg16_stock_loss_submit_gate import snapshot
    from app.formal_services import formal_files
    from test_formal_files_service import FakeStorage,SECRET
    owner,api=(context['engines'][key] for key in ('star_oam_migrator','star_oam_api'))
    tracked=context['tracking']=='serial'
    # Supplement already established synthetic personal stock through the real
    # API ledger. Fixture role creates serial identities, never their positions.
    with Session(owner) as db:
        source=db.get(StockAccount,context['account_id']);sku=db.get(FormalMaterial,source.material_id)
        extras=tuple(uuid4() for _ in range(3)) if tracked else ()
        at=datetime.now(timezone.utc)
        db.add_all(InventorySerial(id=identifier,material_id=source.material_id,lot_id=source.lot_id,
            serial_no='LOSS-POOL-'+identifier.hex,qr_code='LOSS-POOL-QR-'+identifier.hex,lifecycle_status='active',
            created_at=at,updated_at=at) for identifier in extras)
        db.commit()
        sku_code=sku.sku_code
    with Session(api) as db:
        key=uuid4().hex
        posting.post_inventory_transaction(db,actor=load_formal_principal(db,context['admin_id']),
            command=posting.InventoryPostingCommand(transaction_no='LOSS-POOL-IN-'+key,movement_type='inbound',
                source_document_type='synthetic_loss_pool_fixture',source_document_id=key,posting_key=key,
                effective_at=datetime.now(timezone.utc),movements=(posting.InventoryMovementCommand(
                    from_account_id=None,to_account_id=context['account_id'],quantity=Decimal(3),serial_ids=extras,
                    external_boundary_code='PG16_LOSS_POOL_FIXTURE'),)),idempotency_key=key,request_id=key)
        db.commit()
    storage=FakeStorage()
    with Session(api) as db:
        actor=load_formal_principal(db,context['engineer_id'])
        file=formal_files.create_file_upload_intent(db,actor=actor,
            command=formal_files.FileUploadIntentInput(purpose='stock_loss_evidence',original_filename='synthetic-second-loss.jpg',
                size_bytes=128,mime_type='image/jpeg',sha256='b'*64),idempotency_key=uuid4().hex,
            idempotency_hmac_secret=SECRET,trace_request_id=uuid4().hex,storage=storage,upload_ttl_seconds=60)
        storage.materialize(db.get(FileObject,file.file_id))
        formal_files.complete_file_upload(db,actor=actor,file_id=file.file_id,trace_request_id=uuid4().hex,storage=storage)
        db.commit();file_id=file.file_id
    with Session(api) as db:
        proofs=[]
        if tracked:
            serial=db.get(InventorySerial,extras[0])
            proofs=[dict(serial_id=serial.id,sku_code=sku_code,serial_no=serial.serial_no,qr_code=serial.qr_code)]
        request=StockLossPreviewIn(operator_person_id=context['person_id'],reason='Synthetic second independent loss',
            evidence_file_ids=(file_id,),lines=(dict(stock_account_id=context['account_id'],
                quantity='1' if tracked else '0.5',serial_verifications=proofs),))
        actor=load_formal_principal(db,context['engineer_id']);checked,_=plan.preview_loss(db,actor=actor,request=request)
        second=commands.submit_loss(db,actor=actor,request=StockLossSubmitIn(**request.model_dump(),
            expected_plan_hash=checked.plan_hash,idempotency_key=uuid4().hex,request_id=uuid4().hex))
        db.commit()
    with Session(api) as db:
        originals=tuple(db.scalars(select(StockOperationLine).where(StockOperationLine.operation_id.in_(
            (first.operation_id,second.operation_id)))))
        assert len(originals)==2 and originals[0].reserved_account_id==originals[1].reserved_account_id
        frozen=originals[0].reserved_account_id;total=sum(line.quantity for line in originals)
        assert db.get(StockBalance,frozen).quantity==total
        held=tuple(db.scalars(select(StockOperationSerial.serial_id).where(
            StockOperationSerial.line_id.in_(line.id for line in originals))))
        assert len(set(held))==(2 if tracked else 0)
        actor=load_formal_principal(db,context['engineer_id'])
        assert facts.order_result(db,actor=actor,order=db.get(StockOperationOrder,first.operation_id))==first

    def movement(source,target,quantity,serials=()):
        return posting.InventoryMovementCommand(from_account_id=source,to_account_id=target,
            quantity=quantity,serial_ids=serials)

    def post(db,moves,kind):
        key=uuid4().hex
        return posting.post_inventory_transaction(db,actor=load_formal_principal(db,context['engineer_id']),
            command=posting.InventoryPostingCommand(transaction_no='LOSS-POOL-'+key,movement_type=kind,
                source_document_type='synthetic_loss_hold_probe',source_document_id=key,posting_key=key,
                effective_at=datetime.now(timezone.utc),movements=moves),idempotency_key=key,request_id=key,
            permission_resource='stock_operation',permission_action='submit_loss')

    before=snapshot(owner)
    with Session(api) as db:
        # Remaining balance still covers either individual report, but cannot
        # cover their sum. A MAX/per-document-only guard would accept this.
        amount=min(line.quantity for line in originals)
        post(db,(movement(frozen,context['account_id'],amount,held[:1] if tracked else ()),),'release')
        with pytest.raises(DBAPIError,match='0150 unreleased loss quantities must remain frozen') as caught:db.commit()
        assert caught.value.orig.sqlstate=='23514';db.rollback()
    assert snapshot(owner)==before
    if tracked:
        with Session(api) as db:
            # Keep pooled quantity constant while swapping one protected SN.
            post(db,(movement(frozen,context['account_id'],Decimal(1),held[:1]),
                movement(context['account_id'],frozen,Decimal(1),(extras[1],))),'transfer')
            with pytest.raises(DBAPIError,match='0150 exact unreleased loss serials must remain frozen') as caught:db.commit()
            assert caught.value.orig.sqlstate=='23514';db.rollback()
        assert snapshot(owner)==before
    surplus=Decimal(1) if tracked else Decimal('0.125')
    ids=(extras[1],) if tracked else ()
    with Session(api) as db:
        post(db,(movement(context['account_id'],frozen,surplus,ids),),'freeze');db.commit()
    with Session(api) as db:
        assert db.get(StockBalance,frozen).quantity==total+surplus
        post(db,(movement(frozen,context['account_id'],surplus,ids),),'release');db.commit()
    with Session(api) as db:
        assert db.get(StockBalance,frozen).quantity==total
        assert all(db.get(SerialCurrentPosition,identifier).stock_account_id==frozen for identifier in held)
        actor=load_formal_principal(db,context['engineer_id'])
        for result in (first,second):
            assert facts.order_result(db,actor=actor,order=db.get(StockOperationOrder,result.operation_id))==result
    print('PG16 loss: two reports share exact frozen pool; sum/SN protection, surplus-only release and originals PASS',flush=True)
    return dict(reportCount=2,sameFrozenAccount=True,sumNotMaxProtected=True,
        exactSerialSwapRejected=tracked,unencumberedSurplusReleaseCommitted=True,originalsPreserved=True)
