"""Approved loss releases through real API-role PG16 transactions.

Only fresh owned clusters and synthetic identities/storage are accepted by
our callers; none of these grants are shipped to production.
"""
from uuid import UUID, uuid4
from dataclasses import replace
from sqlalchemy import select, text, event
from sqlalchemy.exc import DBAPIError
from unittest.mock import patch
import pytest
from decimal import Decimal
from datetime import datetime, timedelta, timezone
from concurrent.futures import ThreadPoolExecutor
import threading
import time
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.foundation_models import Organization, Permission, Role, RolePermission, FileObject, StateTransitionEvent, OutboxEvent, RoleAssignment
from app.inventory_models import StockAccount, StockBalance, SerialCurrentPosition, InventoryMovement
from app.stock_operation_models import StockOperationLine, StockLossHeadquartersDecision, StockLossDisposition, StockOperationSerial
from app.stock_loss_schemas import (StockLossSubmitIn, StockLossRegionalReviewIn,
    StockLossHeadquartersReviewIn, StockLossDispositionPreviewIn, StockLossDispositionExecuteIn)
from app.formal_services import (formal_files, stock_loss_plan, stock_loss_commands,
    stock_loss_regional_reviews as regional, stock_loss_headquarters_reviews as headquarters,
    stock_loss_disposition_plan as plan, stock_loss_disposition_commands as commands,
    stock_loss_disposition_facts as facts, inventory_posting as posting)
from test_formal_access import make_user, assign
from test_formal_files_service import FakeStorage, SECRET
from pg16_stock_loss_submit_gate import snapshot as stock_snapshot


def snapshot(owner):
    result=stock_snapshot(owner)
    with owner.connect() as db:
        for table in ('stock_loss_dispositions','stock_loss_regional_reviews',
                      'stock_loss_headquarters_reviews','stock_loss_headquarters_decisions'):
            result[table]=tuple(sorted(repr(dict(r)) for r in db.execute(text('SELECT * FROM '+table)).mappings()))
    return result


def run(context):
    owner,api=(context['engines'][key] for key in ('star_oam_migrator','star_oam_api'))
    with Session(owner) as db:
        source=db.get(StockAccount,context['account_id'])
        manager,_=make_user(db,db.get(Organization,source.owner_org_id),name='Synthetic disposition regional reviewer')
        roles={r.code:r for r in db.scalars(select(Role))}
        assign(db,manager,roles['provincial_manager'],scope_type='organization',scope_id=str(source.owner_org_id))
        for action,role in ((regional.ACTION,'provincial_manager'),(headquarters.ACTION,'admin'),(plan.ACTION,'admin')):
            permission=Permission(resource='stock_operation',action=action,field_code='',description='Synthetic owned PG16 disposition gate')
            db.add(permission);db.flush()
            db.add(RolePermission(role_id=roles[role].id,permission_id=permission.id,effect='allow'))
        db.commit();manager_id=manager.id
    request=context['request'];results=[]
    for kind in ('restore_available','convert_used','convert_damaged'):
        with Session(api) as db:
            actor=load_formal_principal(db,context['engineer_id'])
            if results:
                storage=FakeStorage()
                upload=formal_files.create_file_upload_intent(db,actor=actor,
                    command=formal_files.FileUploadIntentInput(purpose='stock_loss_evidence',original_filename='synthetic-disposition.jpg',
                        size_bytes=128,mime_type='image/jpeg',sha256='a'*64),
                    idempotency_key=uuid4().hex,idempotency_hmac_secret=SECRET,trace_request_id=uuid4().hex,storage=storage,upload_ttl_seconds=60)
                storage.materialize(db.get(FileObject,upload.file_id))
                formal_files.complete_file_upload(db,actor=actor,file_id=upload.file_id,trace_request_id=uuid4().hex,storage=storage)
                db.commit()
                request=request.model_copy(update={'evidence_file_ids':(upload.file_id,),
                    'lines':(request.lines[0].model_copy(update={'stock_account_id':UUID(results[-1]['target_account_id'])}),)})
            prepared,_=stock_loss_plan.preview_loss(db,actor=actor,request=request)
            submission=stock_loss_commands.submit_loss(db,actor=actor,request=StockLossSubmitIn(**request.model_dump(),
                expected_plan_hash=prepared.plan_hash,request_id=uuid4().hex,idempotency_key=uuid4().hex))
            db.commit()
        with Session(api) as db:
            review=regional.verify_regional_loss(db,actor=load_formal_principal(db,manager_id),
                request=StockLossRegionalReviewIn(operation_id=submission.operation_id,expected_submission_plan_hash=prepared.plan_hash,
                    comment='Synthetic independent regional verification',request_id=uuid4().hex,idempotency_key=uuid4().hex))
            db.commit()
        with Session(api) as db:
            line=db.scalar(select(StockOperationLine).where(StockOperationLine.operation_id==submission.operation_id))
            approval=headquarters.approve_headquarters_loss(db,actor=load_formal_principal(db,context['admin_id']),
                request=StockLossHeadquartersReviewIn(operation_id=submission.operation_id,expected_submission_plan_hash=prepared.plan_hash,
                    regional_review_id=review.review_id,expected_regional_review_hash=review.request_hash,
                    decisions=(dict(line_id=line.id,disposition=kind,reason='Synthetic approved disposition'),),
                    comment='Synthetic independent headquarters decision',request_id=uuid4().hex,idempotency_key=uuid4().hex))
            db.commit()
            decision=db.scalar(select(StockLossHeadquartersDecision).where(StockLossHeadquartersDecision.review_id==approval.review_id))
            value=StockLossDispositionPreviewIn(headquarters_decision_id=decision.id,
                expected_headquarters_review_hash=approval.request_hash,expected_submission_plan_hash=prepared.plan_hash)
        if kind=='restore_available':
            from pg16_stock_loss_submission_boundaries import shared_holds
            shared_holds(context,submission)
            with Session(api) as db:
                shared_line=db.scalar(select(StockOperationLine).where(StockOperationLine.operation_id!=submission.operation_id,
                    StockOperationLine.operation_type=='loss_report'))
                shared_line_id,shared_order_id=shared_line.id,shared_line.operation_id
                shared_source,shared_quantity=shared_line.reserved_account_id,shared_line.quantity
                shared_serials=tuple(db.scalars(select(StockOperationSerial.serial_id).where(StockOperationSerial.line_id==shared_line_id)))
        with Session(api) as db:
            original_move_id=db.scalar(select(InventoryMovement.id).where(InventoryMovement.transaction_id==submission.posting_transaction_id))
        before=snapshot(owner)
        preview_statements=[]
        def query_only(conn,cursor,statement,parameters,execution,executemany):
            preview_statements.append(statement.split()[0].upper())
            assert statement.lstrip().upper().startswith('SELECT '), 'preview wrote business data'
        event.listen(api,'before_cursor_execute',query_only)
        with Session(api) as db:
            actor=load_formal_principal(db,context['admin_id'])
            preview=plan.preview_disposition(db,actor=actor,request=value)
            assert preview['plan_hash']==plan.preview_disposition(db,actor=actor,request=value)['plan_hash']
            target=UUID(preview['target_account_id'])
            existing=db.get(StockAccount,target)
            assert bool(existing)==(kind=='restore_available')
            old_balance=db.get(StockBalance,target);old_quantity=old_balance.quantity if old_balance else 0
            db.commit()
        event.remove(api,'before_cursor_execute',query_only)
        assert preview_statements and set(preview_statements)=={'SELECT'}
        assert snapshot(owner)==before
        if kind=='restore_available':
            from pg16_stock_loss_custody_gate import assert_custody_uniqueness
            assert_custody_uniqueness(context,request=value,preview=preview)
        command=StockLossDispositionExecuteIn(**value.model_dump(),expected_plan_hash=preview['plan_hash'],
            request_id=uuid4().hex,idempotency_key=uuid4().hex)
        for omission in ('audit','state','outbox','notification','quantity','plan_hash','request_hash','authorization_version','line_id','posting_movement_id')+ (('serial_swap',) if context['tracking']=='serial' and preview['source_account_id']==str(shared_source) else ()):
            before=snapshot(owner)
            with Session(api) as db:
                def mutate(session,*_):
                    for row in tuple(session.new):
                        if isinstance(row,StockLossDisposition):
                            if omission=='quantity':row.quantity+=Decimal('0.001')
                            elif omission in ('plan_hash','request_hash'):setattr(row,omission,'f'*64)
                            elif omission=='authorization_version':row.authorization_version+=1
                            elif omission=='line_id':row.line_id=shared_line_id
                            elif omission=='posting_movement_id':row.posting_movement_id=original_move_id
                        elif (omission=='state' and isinstance(row,StateTransitionEvent) and row.aggregate_type==facts.AGGREGATE
                            or omission=='outbox' and isinstance(row,OutboxEvent) and row.aggregate_type==facts.AGGREGATE):
                            session.expunge(row)
                event.listen(db,'before_flush',mutate)
                with patch.object(facts,'verified',return_value=None):
                    if omission in ('audit','notification'):
                        method='append_audit_event' if omission=='audit' else 'record_business_notification'
                        with patch.object(commands,method,return_value=None):
                            commands.execute_disposition(db,actor=load_formal_principal(db,context['admin_id']),request=command)
                    elif omission=='serial_swap':
                        factory=facts.posting_command
                        def swapped(row):
                            original=factory(row)
                            return replace(original,movements=(replace(original.movements[0],serial_ids=shared_serials),))
                        with patch.object(facts,'posting_command',side_effect=swapped):
                            commands.execute_disposition(db,actor=load_formal_principal(db,context['admin_id']),request=command)
                    else:
                        commands.execute_disposition(db,actor=load_formal_principal(db,context['admin_id']),request=command)
                expected='verified opening recount observation graph' if omission in ('quantity','line_id','posting_movement_id') and kind!='restore_available' else '0150'
                with pytest.raises(DBAPIError,match=expected) as caught:db.commit()
                assert caught.value.orig.sqlstate=='23514'
                db.rollback()
            assert snapshot(owner)==before, omission
        print('PG16 disposition '+context['tracking']+' '+kind+': exact-reference and malformed commits rolled back',flush=True)
        if kind=='restore_available':
            with Session(owner) as db:
                assignment=db.scalar(select(RoleAssignment).join(Role,Role.id==RoleAssignment.role_id)
                    .where(RoleAssignment.user_id==context['admin_id'],Role.code=='admin'))
                assignment_id,old_end=assignment.id,assignment.valid_to
                # The complete serial proof must finish while authority is valid,
                # including when other release checks load the local host.
                deadline=db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=60)
                assignment.valid_to=deadline;db.commit()
            before=snapshot(owner)
            try:
                with Session(api) as db:
                    commands.execute_disposition(db,actor=load_formal_principal(db,context['admin_id']),request=command)
                    assert db.scalar(text('SELECT clock_timestamp()'))<deadline
                    time.sleep(max(0,(deadline-db.scalar(text('SELECT clock_timestamp()'))).total_seconds())+.05)
                    assert db.scalar(text('SELECT clock_timestamp()'))>deadline
                    with pytest.raises(DBAPIError,match='0150 current headquarters disposition authority required') as expired:
                        db.commit()
                    assert expired.value.orig.sqlstate=='23514';db.rollback()
                assert snapshot(owner)==before
            finally:
                with Session(owner) as db:db.get(RoleAssignment,assignment_id).valid_to=old_end;db.commit()
            print('PG16 disposition '+context['tracking']+': COMMIT-time expiry full rollback PASS',flush=True)
        if kind=='convert_damaged':
            barrier=threading.Barrier(2)
            def execute_concurrent(_):
                with Session(api) as db:
                    actor=load_formal_principal(db,context['admin_id'])
                    barrier.wait(timeout=15)
                    answer=commands.execute_disposition(db,actor=actor,request=command)
                    db.commit();return answer
            with ThreadPoolExecutor(max_workers=2) as pool:
                first,second=tuple(pool.map(execute_concurrent,range(2)))
            assert first==second;result=first
        else:
            with Session(api) as db:
                result=commands.execute_disposition(db,actor=load_formal_principal(db,context['admin_id']),request=command)
                db.commit()
        with Session(api) as db:
            assert db.get(StockBalance,UUID(preview['source_account_id'])).quantity==Decimal(preview['source_balance_quantity'])-request.lines[0].quantity
            assert db.get(StockBalance,target).quantity==old_quantity+request.lines[0].quantity
            assert all(db.get(SerialCurrentPosition,UUID(s)).stock_account_id==target for s in preview['serial_ids'])
            assert facts.verified(db,row=db.get(StockLossDisposition,UUID(result['disposition_id'])))==result
        committed=snapshot(owner)
        with Session(api) as db:
            assert commands.execute_disposition(db,actor=load_formal_principal(db,context['admin_id']),request=command)==result
            db.commit()
        assert snapshot(owner)==committed
        before=snapshot(owner)
        with Session(api) as db:
            inverse=posting.InventoryReversalCommand(original_transaction_id=UUID(result['posting_transaction_id']),
                transaction_no='SYNTHETIC-RENAMED-INVERSE-'+uuid4().hex,source_document_type='renamed_generic_adjustment',
                source_document_id=str(uuid4()),posting_key=uuid4().hex,effective_at=datetime.now(timezone.utc))
            # Bypass only the application origin check: the real inverse and
            # its audit must still be rejected independently by PostgreSQL.
            with patch.object(posting,'_require_generic_reversal_origin',return_value=None):
                posting.reverse_inventory_transaction(db,actor=load_formal_principal(db,context['admin_id']),
                    command=inverse,idempotency_key=uuid4().hex,request_id=uuid4().hex)
            with pytest.raises(DBAPIError,match='0150 (exact approved disposition graph required|disposition requires its dedicated reversal)') as refused:
                db.commit()
            assert refused.value.orig.sqlstate=='23514';db.rollback()
        assert snapshot(owner)==before
        results.append(result)
        print('PG16 disposition '+context['tracking']+' '+kind+': API COMMIT, stock/SN, read-only preview and replay PASS',flush=True)
    before=snapshot(owner)
    with Session(api) as db:
        assert db.get(StockBalance,shared_source).quantity==shared_quantity
        move=posting.InventoryMovementCommand(from_account_id=shared_source,to_account_id=context['account_id'],
            quantity=shared_quantity,serial_ids=shared_serials)
        command=posting.InventoryPostingCommand(transaction_no='SYNTHETIC-BORROW-'+uuid4().hex,movement_type='release',
            source_document_type='synthetic_held_stock_probe',source_document_id=str(uuid4()),posting_key=uuid4().hex,
            effective_at=datetime.now(timezone.utc),movements=(move,))
        posting.post_inventory_transaction(db,actor=load_formal_principal(db,context['engineer_id']),command=command,
            idempotency_key=uuid4().hex,request_id=uuid4().hex,permission_resource='stock_operation',permission_action='submit_loss')
        with pytest.raises(DBAPIError,match='0150 unreleased loss quantities must remain frozen') as refused:db.commit()
        assert refused.value.orig.sqlstate=='23514';db.rollback()
    assert snapshot(owner)==before
    print('PG16 disposition '+context['tracking']+': unrelated shared hold retained and borrowing rolled back PASS',flush=True)
    before=snapshot(owner)
    for statement in ("UPDATE stock_loss_dispositions SET request_hash=repeat('f',64)",
                      "DELETE FROM stock_loss_dispositions", "TRUNCATE stock_loss_dispositions",
                      "SELECT public.rsc_check_loss_disposition_0150(NULL::uuid,false)"):
        with Session(api) as db:
            with pytest.raises(DBAPIError) as refused:db.execute(text(statement));db.commit()
            assert refused.value.orig.sqlstate=='42501';db.rollback()
        assert snapshot(owner)==before
    with Session(api) as db:
        for row in db.scalars(select(StockLossDisposition)):
            assert facts.verified(db,row=row)['status']=='posted'
    from pg16_stock_loss_notification_gate import run as notification_checks
    notification = notification_checks(context)
    with Session(api) as db:
        for row in db.scalars(select(StockLossDisposition)):
            assert facts.verified(db,row=row)['status']=='posted'
    return dict(passed=True,tracking=context['tracking'],apiRoleCommits=3,
        kinds=[r['disposition'] for r in results],businessReadOnlyPreviews=3,openingProofRowLocksRequired=True,originalReplays=3,newTargetAccounts=2,
        malformedCommitFullRollbacks=32 if context['tracking']=='serial' else 30,commitTimeAuthorityExpiryRollback=True,concurrentSameRequestSinglePosting=True,
        historicalProofAfterLaterDispositions=True,renamedGenericInverseCommitRollbacks=3,apiPermissionDenials=4,
        sharedHoldAfterRelease=True,exactLineAndMovementRefusals=True,exactSerialSwapRefusal=context['tracking']=='serial',
        wholeLocationCustodyRefused=True,custodyOverlapApiCommitFullRollback=True,expiredAndFutureCustodyAllowed=True,
        concurrentCustodyInsertionSerialized=True,notificationDeduplication=notification,productionAcceptance=False)


def release(engines,*,tracking,migrate,provision):
    from app.database_security import validate_production_database_security
    from pg16_stock_loss_sources_gate import run as sources
    migrate('initial-upgrade','upgrade','head')
    migrate('empty-downgrade','downgrade','20261128_0149')
    migrate('empty-reupgrade','upgrade','head')
    provision()
    def security():
        validate_production_database_security(engines['star_oam_api'],expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    security()
    result=sources(engines,tracking=tracking,after_preview=run)
    migrate('retained-disposition-downgrade','downgrade','20261128_0149','0151 disposition custody proof history requires retention')
    from pg16_stock_loss_custody_gate import assert_original_retention
    assert_original_retention(engines['star_oam_migrator'])
    with engines['star_oam_migrator'].connect() as db:
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='20261130_0151'
    security()
    result.update(emptyRoundtrip=True,retainedDispositionBlocksDowngrade=True,
        custodyHistoryBlocksDowngrade=True,independent0150RetentionPreserved=True,runtimeSecurityBeforeAndAfter=True)
    return result
