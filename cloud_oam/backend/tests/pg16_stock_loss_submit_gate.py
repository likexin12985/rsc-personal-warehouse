"""Real submission and raw incomplete-command rejection on owned PG16.

The source fixture creates actual personal opening and completed file facts.
Only synthetic identities, permissions and in-memory object storage are used.
"""
from datetime import datetime, timezone
from decimal import Decimal
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services import stock_loss_commands as commands, stock_loss_facts as facts, stock_loss_plan as plan
from app.formal_services import inventory_posting as posting
from app.foundation_models import OutboxEvent
from app.inventory_models import StockBalance
from app.stock_loss_schemas import StockLossSubmitIn
from app.stock_operation_models import StockLossFile, StockOperationLine


def snapshot(engine):
    tables=('stock_accounts','stock_balances','inventory_transactions','inventory_movements','inventory_movement_serials',
        'serial_current_positions','inventory_serials','stock_operation_orders','stock_operation_lines','stock_operation_serials',
        'stock_loss_files','audit_events','audit_chain_heads','inventory_ledger_heads','state_transition_events','outbox_events',
        'notification_events','notification_person_targets','notification_recipients','notification_target_bindings')
    with engine.connect() as db:
        return {name:tuple(sorted(repr(dict(row)) for row in db.execute(text('SELECT * FROM '+name)).mappings())) for name in tables}


def run(context):
    from pg16_stock_loss_seal_gate import prepare_generic_receipt_parent
    prepare_generic_receipt_parent(context)
    owner,api=(context['engines'][key] for key in ('star_oam_migrator','star_oam_api'))
    with Session(api) as db:
        actor=load_formal_principal(db,context['engineer_id'])
        preview,_=plan.preview_loss(db,actor=actor,request=context['request'])
    def request():
        return StockLossSubmitIn(**context['request'].model_dump(),expected_plan_hash=preview.plan_hash,
            idempotency_key=uuid4().hex,request_id=uuid4().hex)
    # Deliberately omit/change one fact while retaining the real posting and
    # the rest of the command. Bypass only the app's final readback so the test
    # must reach deferred PostgreSQL enforcement, not its Python counterpart.
    for omission in ('file','domain_outbox','notification','line_quantity'):
        before=snapshot(owner)
        with Session(api) as db:
            def mutate(session, *_):
                for row in tuple(session.new):
                    if (omission=='file' and isinstance(row,StockLossFile)) or (
                            omission=='domain_outbox' and isinstance(row,OutboxEvent) and row.aggregate_type=='stock_operation_order'):
                        session.expunge(row)
                    elif omission=='line_quantity' and isinstance(row,StockOperationLine):
                        row.quantity+=Decimal('1')
            event.listen(db,'before_flush',mutate)
            with patch.object(facts,'order_result',return_value=None):
                if omission=='notification':
                    with patch.object(commands,'record_business_notification',return_value=None):
                        commands.submit_loss(db,actor=load_formal_principal(db,actor.user_id),request=request())
                else:
                    commands.submit_loss(db,actor=load_formal_principal(db,actor.user_id),request=request())
            expected = 'verified opening recount observation graph' if omission=='line_quantity' else '0145'
            with pytest.raises(DBAPIError,match=expected):
                db.commit()
            db.rollback()
        assert snapshot(owner)==before,omission
    print('PG16 loss: four incomplete real commands rejected at COMMIT with full rollback',flush=True)
    value=request()
    from pg16_stock_loss_submission_boundaries import commit_expiry, shared_holds
    expiry=commit_expiry(context,request())
    read_grant_id=provision_recovery_read(owner)
    from pg16_stock_loss_seal_gate import run as seal_checks
    sealed=seal_checks(context,value)
    result, concurrent=concurrent_recovery_checks(context,value)
    committed=snapshot(owner)
    with Session(api) as db:
        repeated=commands.submit_loss(db,actor=load_formal_principal(db,actor.user_id),request=value)
        assert repeated==result
        db.commit()
    assert snapshot(owner)==committed
    with Session(api) as db:
        line=db.scalar(select(StockOperationLine).where(StockOperationLine.operation_id==result.operation_id))
        assert db.get(StockBalance,line.reserved_account_id).quantity==context['request'].lines[0].quantity
        assert db.get(StockBalance,context['account_id']).quantity==Decimal('1')-line.quantity
        assert db.scalar(text('SELECT count(*) FROM stock_loss_files WHERE operation_id=:id'),dict(id=result.operation_id))==1
        frozen_id=line.reserved_account_id
    # A normal inventory command cannot borrow even part of the held quantity
    # or the same SN. The actor has the same personal scope and permission.
    with Session(api) as db:
        move=posting.InventoryMovementCommand(from_account_id=frozen_id,to_account_id=context['account_id'],
            quantity=context['request'].lines[0].quantity,
            serial_ids=tuple(p.serial_id for p in context['request'].lines[0].serial_verifications))
        command=posting.InventoryPostingCommand(transaction_no='SYNTHETIC-LOSS-STEAL-'+uuid4().hex,
            movement_type='release',source_document_type='synthetic_loss_hold_probe',source_document_id=str(uuid4()),
            posting_key='synthetic-loss-hold-'+uuid4().hex,effective_at=datetime.now(timezone.utc),movements=(move,))
        posting.post_inventory_transaction(db,actor=load_formal_principal(db,actor.user_id),command=command,
            idempotency_key=uuid4().hex,request_id=uuid4().hex,permission_resource='stock_operation',permission_action='submit_loss')
        with pytest.raises(DBAPIError,match='0159 original and restored loss quantities must remain frozen'):
            db.commit()
        db.rollback()
    assert snapshot(owner)==committed
    print('PG16 loss: atomic freeze, original replay and exact held-stock protection PASS',flush=True)
    pooled=shared_holds(context,result)
    recovered = recovery_checks(context, value, result, read_grant_id)
    recovered.update(concurrent)
    return dict(passed=True,apiRoleCommit=True,originalReplayNoWrites=True,
        incompleteCommandCommitRollbacks=4,heldStockBorrowRejected=True,tracking=context['tracking'],
        recovery=recovered,seals=sealed,submissionExpiry=expiry,sharedHolds=pooled)


def provision_recovery_read(owner):
    from app.foundation_models import Permission, Role, RolePermission
    with Session(owner) as db:
        permission=db.scalar(select(Permission).where(Permission.resource=='stock_operation',Permission.action=='read',Permission.field_code==''))
        if permission is None:
            permission=Permission(resource='stock_operation',action='read',field_code='',description='Synthetic local loss recovery')
            db.add(permission);db.flush()
        role=db.scalar(select(Role.id).where(Role.code=='technician'))
        grant=db.scalar(select(RolePermission).where(RolePermission.role_id==role,RolePermission.permission_id==permission.id))
        if grant is None:
            grant=RolePermission(role_id=role,permission_id=permission.id,effect='allow');db.add(grant);db.flush()
        assert grant.effect=='allow'
        read_grant_id=grant.id
        db.commit()
    return read_grant_id


def concurrent_recovery_checks(context,value):
    """Two real API transactions: pending, rollback and commit during lookup.

    Commit is injected after the reader checks absence, before its final fence.
    No clocks or sleeps determine the interleaving, and neither transaction's
    fact reads are mocked. The successful writer is the main fixture's loss.
    """
    from app.stock_loss_schemas import StockLossRequestLookupIn
    from app.formal_services import stock_loss_recovery as recovery
    from app.formal_services.inventory_query import InventoryReadError
    owner,api=(context['engines'][key] for key in ('star_oam_migrator','star_oam_api'))
    before=snapshot(owner)
    for finish in ('rollback','commit'):
        with Session(api) as writer:
            result=commands.submit_loss(writer,actor=load_formal_principal(writer,context['engineer_id']),request=value)
            writer_pid=writer.scalar(text('SELECT pg_backend_pid()'))
            request=StockLossRequestLookupIn(operator_person_id=context['person_id'],request_id=value.request_id,
                idempotency_key=value.idempotency_key,request_hash=result.request_hash,expected_plan_hash=result.plan_hash)
            with Session(api) as reader:
                reader.execute(text('SET TRANSACTION READ ONLY'))
                reader.execute(text("SET LOCAL statement_timeout = '10s'"))
                assert reader.scalar(text('SELECT pg_backend_pid()'))!=writer_pid
                assert reader.scalar(text('SHOW transaction_isolation'))=='read committed'
                actor=load_formal_principal(reader,context['engineer_id'])
                pending=recovery.lookup_loss_request(reader,actor=actor,request=request)
                assert pending.lookup_status=='not_found' and pending.retry_permitted is False
                reader.commit()
            assert snapshot(owner)==before
            if finish=='rollback':
                writer.rollback()
                with Session(api) as reader:
                    reader.execute(text('SET TRANSACTION READ ONLY'))
                    absent=recovery.lookup_loss_request(reader,
                        actor=load_formal_principal(reader,context['engineer_id']),request=request)
                    assert absent.lookup_status=='not_found' and absent.retry_permitted is False
                    reader.commit()
                assert snapshot(owner)==before
                continue
            check=recovery._require_request_evidence
            def commit_after_absence(*args,**kwargs):
                check(*args,**kwargs)
                if args[0] is reader:
                    from pg16_stock_loss_seal_gate import commit_before_waiting_seal
                    commit_before_waiting_seal(writer,context,value)
            with Session(api) as reader:
                reader.execute(text('SET TRANSACTION READ ONLY'))
                actor=load_formal_principal(reader,context['engineer_id'])
                with patch.object(recovery,'_require_request_evidence',commit_after_absence):
                    with pytest.raises(InventoryReadError) as changed:
                        recovery.lookup_loss_request(reader,actor=actor,request=request)
                assert changed.value.code=='stock_loss_lookup_changed'
                reader.rollback()
            committed=snapshot(owner)
            with Session(api) as reader:
                reader.execute(text('SET TRANSACTION READ ONLY'))
                found=recovery.lookup_loss_request(reader,
                    actor=load_formal_principal(reader,context['engineer_id']),request=request)
                assert found.lookup_status=='found' and found.submission==result and found.retry_permitted is False
                reader.commit()
            assert snapshot(owner)==committed
    print('PG16 loss recovery: separate reader/writer, pending/rollback safe, mid-read commit rejected PASS',flush=True)
    return result,dict(separateApiTransactions=True,uncommittedNeverPermitsRetry=True,
        rolledBackNeverPermitsRetry=True,midReadCommitRejected=True,committedRetryRecoversOriginal=True,
        submitFirstSealRecoversOriginal=True)


def recovery_checks(context, value, result, read_grant_id):
    from app.foundation_models import RolePermission
    from app.stock_loss_schemas import StockLossRequestLookupIn
    from app.formal_services import stock_loss_recovery as recovery
    from app.formal_services.stock_loss_recovery import lookup_loss_request
    from app.formal_services.inventory_query import InventoryReadError
    owner, api = (context['engines'][key] for key in ('star_oam_migrator','star_oam_api'))
    with Session(owner) as db:
        # Reading a committed result remains available after write permission
        # is revoked; this owner change is confined to the synthetic fixture.
        db.get(RolePermission,context['grant_id']).effect='deny';db.commit()
    request=StockLossRequestLookupIn(operator_person_id=context['person_id'],request_id=value.request_id,
        idempotency_key=value.idempotency_key,request_hash=result.request_hash,expected_plan_hash=result.plan_hash)
    before=snapshot(owner)
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor=load_formal_principal(db,context['engineer_id'])
        recovered=lookup_loss_request(db,actor=actor,request=request)
        assert recovered.lookup_status=='found' and recovered.submission==result and recovered.retry_permitted is False
        absent=lookup_loss_request(db,actor=actor,request=request.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex}))
        assert absent.lookup_status=='not_found' and absent.retry_permitted is False
        with pytest.raises(InventoryReadError) as changed:
            lookup_loss_request(db,actor=actor,request=request.model_copy(update={'request_hash':'f'*64}))
        assert changed.value.code=='stock_loss_request_conflict'
        db.commit()
    assert snapshot(owner)==before
    check=recovery._require_request_evidence
    def revoke_after_read(*args,**kwargs):
        check(*args,**kwargs)
        with Session(owner) as writer:
            writer.get(RolePermission,read_grant_id).effect='deny';writer.commit()
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actor=load_formal_principal(db,context['engineer_id'])
        with patch.object(recovery,'_require_request_evidence',revoke_after_read):
            with pytest.raises(InventoryReadError) as denied:
                lookup_loss_request(db,actor=actor,request=request)
        assert denied.value.code=='stock_loss_read_forbidden'
        db.commit()
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        with pytest.raises(InventoryReadError) as denied:
            lookup_loss_request(db,actor=load_formal_principal(db,context['engineer_id']),request=request)
        assert denied.value.code=='stock_loss_read_forbidden'
        db.commit()
    assert snapshot(owner)==before
    print('PG16 loss recovery: read-only original/missing/conflict, revoked write and current read denial PASS',flush=True)
    return dict(readOnlyTransaction=True,originalRecovered=True,absenceNeverPermitsRetry=True,
        coordinateConflictRejected=True,readIndependentFromWrite=True,currentReadRevocationRejected=True,
        midReadRevocationRejected=True,factsUnchanged=True)
