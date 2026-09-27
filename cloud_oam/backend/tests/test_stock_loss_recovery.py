"""Lost responses recover exact immutable facts without granting a retry."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.foundation_models import AuditChainHead, AuditEvent, StateTransitionEvent
from app.inventory_models import InventoryLedgerHead, InventoryTransaction
from app.stock_operation_models import StockLossFile
from app.stock_loss_schemas import StockLossRequestLookupIn
from app.formal_services import stock_loss_recovery as recovery, stock_loss_commands as commands, stock_loss_plan as plan
from app.formal_services import inventory_posting as posting
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.inventory_query import InventoryReadError
from test_stock_loss_commands import db, world, stock, allowed, evidence, submission, snapshot


@pytest.fixture
def readable(allowed):
    actor = allowed.actor
    allowed.world.current_principal = replace(actor, entitlements=actor.entitlements+(
        replace(actor.entitlements[0], resource='stock_operation', action='read'),))
    allowed.actor = allowed.world.current_principal
    return allowed


def lookup(value):
    return StockLossRequestLookupIn(operator_person_id=value.operator_person_id, request_id=value.request_id,
        idempotency_key=value.idempotency_key, request_hash=plan.sources._hash(plan.intent(value)),
        expected_plan_hash=value.expected_plan_hash)


def test_missing_response_reads_original_without_writes_and_never_authorizes_retry(db, readable, evidence):
    value = submission(db, readable, evidence); db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    absent = recovery.lookup_loss_request(db, actor=readable.actor, request=lookup(value))
    assert absent.lookup_status=='not_found' and absent.retry_permitted is False
    assert snapshot(db)==before
    db.execute(text('PRAGMA query_only=OFF'))
    result = commands.submit_loss(db, actor=readable.actor, request=value); db.commit()
    before = snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    found = recovery.lookup_loss_request(db, actor=readable.actor, request=lookup(value))
    assert found.lookup_status=='found' and found.submission==result and found.retry_permitted is False
    assert recovery.lookup_loss_request(db, actor=readable.actor, request=lookup(value))==found
    assert snapshot(db)==before and not db.new and not db.dirty and not db.deleted
    assert value.idempotency_key not in found.model_dump_json()
    assert 'storage_key' not in found.model_dump_json() and 'qr_code' not in found.model_dump_json()


@pytest.mark.parametrize('field', ['request_id','idempotency_key','request_hash','expected_plan_hash'])
def test_changed_request_coordinates_never_return_another_success(db, readable, evidence, field):
    value = submission(db, readable, evidence)
    commands.submit_loss(db, actor=readable.actor, request=value); db.commit()
    request = lookup(value).model_copy(update={field:uuid4().hex if field in ('request_id','idempotency_key') else 'f'*64})
    before = snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        recovery.lookup_loss_request(db, actor=readable.actor, request=request)
    assert error.value.code=='stock_loss_request_conflict'
    assert snapshot(db)==before


def test_read_permission_is_independent_and_operator_is_exact(db, readable, evidence):
    value = submission(db, readable, evidence); db.commit()
    original = readable.actor
    readable.world.current_principal = replace(original, entitlements=tuple(p for p in original.entitlements
        if not (p.resource=='stock_operation' and p.action=='submit_loss')))
    assert recovery.lookup_loss_request(db, actor=original, request=lookup(value)).lookup_status=='not_found'
    readable.world.current_principal = replace(original, entitlements=tuple(p for p in original.entitlements
        if not (p.resource=='stock_operation' and p.action=='read')))
    with pytest.raises(InventoryReadError) as error:
        recovery.lookup_loss_request(db, actor=original, request=lookup(value))
    assert error.value.code=='stock_loss_read_forbidden'
    readable.world.current_principal = original
    with pytest.raises(InventoryReadError) as error:
        recovery.lookup_loss_request(db, actor=original,
            request=lookup(value).model_copy(update={'operator_person_id':uuid4()}))
    assert error.value.code=='operator_mismatch'


@pytest.mark.parametrize('fragment', ['domain_audit','posting_audit','posting_state','keyed_posting','return_audit','return_seal_audit'])
def test_orphan_evidence_cannot_be_reported_as_a_clean_miss(db, readable, evidence, fragment):
    value = submission(db, readable, evidence); request=lookup(value)
    at=datetime.now(timezone.utc); actor=readable.actor
    if fragment=='posting_state':
        db.add(StateTransitionEvent(aggregate_type='inventory_transaction',aggregate_id=str(uuid4()),
            from_status=None,to_status='posted',actor_id=actor.user_id,reason='synthetic orphan',
            idempotency_key=uuid4().hex,occurred_at=at,created_at=at,
            metadata_jsonb={'request_reference':posting._request_reference(value.request_id)}))
    elif fragment=='keyed_posting':
        db.add(InventoryTransaction(id=uuid4(),transaction_no='ORPHAN-'+uuid4().hex,movement_type='freeze',
            source_document_type='stock_operation_loss',source_document_id=str(uuid4()),posting_key=uuid4().hex,
            idempotency_key_hash=posting._storage_hash(value.idempotency_key),request_hash='f'*64,
            status='posted',effective_at=at,posted_at=at,created_at=at,actor_user_id=actor.user_id,
            ledger_cursor=db.scalar(select(InventoryLedgerHead.next_cursor))))
    else:
        is_return=fragment.startswith('return')
        append_audit_event(db, stream_key='material_request' if is_return else 'inventory', actor_user_id=actor.user_id,
            action='synthetic_orphan' if is_return else 'stock_loss_submitted' if fragment=='domain_audit' else 'inventory.transaction.posted',
            aggregate_type='stock_operation_command_seal' if fragment=='return_seal_audit' else
                'inventory_transaction' if fragment=='posting_audit' else 'stock_operation_order',
            aggregate_id=str(uuid4()),before_jsonb={},after_jsonb={'request_id':value.request_id},
            request_id=posting._request_reference(value.request_id) if fragment=='posting_audit' else
                'stock-return-seal:'+uuid4().hex if fragment=='return_seal_audit' else value.request_id,occurred_at=at)
    db.commit(); before=snapshot(db)
    with pytest.raises(InventoryReadError) as error:
        recovery.lookup_loss_request(db, actor=actor, request=request)
    assert error.value.code==('stock_loss_request_conflict' if fragment.startswith('return') else 'stock_loss_evidence_invalid')
    assert snapshot(db)==before


@pytest.mark.parametrize('change', ['inventory_cursor','audit_cursor','permission','authority'])
def test_change_during_read_invalidates_the_observation(db, readable, evidence, monkeypatch, change):
    value=submission(db,readable,evidence);db.commit()
    original=recovery._require_request_evidence
    def changed(*args,**kwargs):
        original(*args,**kwargs)
        if change=='inventory_cursor':
            db.scalar(select(InventoryLedgerHead)).next_cursor+=1;db.flush()
        elif change=='audit_cursor':
            db.scalar(select(AuditChainHead).where(AuditChainHead.stream_key=='inventory')).version+=1;db.flush()
        elif change=='permission':
            readable.world.current_principal=replace(readable.actor,entitlements=())
        else:
            readable.world.current_principal=replace(readable.actor,authorization_version=readable.actor.authorization_version+1)
    monkeypatch.setattr(recovery,'_require_request_evidence',changed)
    with pytest.raises((InventoryReadError,posting.InventoryPostingError)) as error:
        recovery.lookup_loss_request(db,actor=readable.actor,request=lookup(value))
    assert error.value.code=={'inventory_cursor':'stock_loss_lookup_changed','audit_cursor':'stock_loss_lookup_changed',
        'permission':'stock_loss_read_forbidden','authority':'actor_principal_stale'}[change]
    db.rollback()


def test_attachment_binding_time_is_checked_when_recovering_original(db,readable,evidence):
    value=submission(db,readable,evidence)
    commands.submit_loss(db,actor=readable.actor,request=value);db.commit()
    binding=db.scalar(select(StockLossFile));binding.created_at+=timedelta(seconds=1);db.flush()
    with pytest.raises(InventoryReadError) as error:
        recovery.lookup_loss_request(db,actor=readable.actor,request=lookup(value))
    assert error.value.code=='stock_loss_evidence_invalid'


def test_foreign_original_is_hidden_even_with_national_read_scope(db,readable,evidence):
    value=submission(db,readable,evidence)
    result=commands.submit_loss(db,actor=readable.actor,request=value);db.commit()
    reviewer=readable.world.headquarters_reviewer_user
    foreign=replace(readable.actor,user_id=reviewer.id,person_id=reviewer.person_id,
        authorization_version=reviewer.authorization_version,entitlements=tuple(
            replace(row,scope_type='national',scope_id='*') for row in readable.actor.entitlements))
    readable.world.current_principal=foreign
    request=lookup(value).model_copy(update={'operator_person_id':foreign.person_id})
    before=snapshot(db);db.execute(text('PRAGMA query_only=ON'))
    with pytest.raises(InventoryReadError) as error:
        recovery.lookup_loss_request(db,actor=foreign,request=request)
    assert error.value.code=='stock_loss_not_found' and error.value.status_code==404
    assert str(result.operation_id) not in str(error.value.as_detail())
    assert snapshot(db)==before
