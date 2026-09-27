"""Service composition with real approval/freezing; SQL COMMIT gates separate."""
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from app.formal_access import load_formal_principal
from app.foundation_models import Permission, Role, RolePermission
from app.inventory_models import StockBalance, SerialCurrentPosition, StockAccount, InventoryTransaction, CustodyAssignment
from app.stock_operation_models import StockLossHeadquartersDecision, StockLossDisposition, StockOperationOrder
from app.stock_loss_schemas import StockLossDispositionPreviewIn, StockLossDispositionExecuteIn
from app.formal_services import stock_loss_disposition_plan as plan, stock_loss_disposition_commands as commands
from app.formal_services import stock_loss_disposition_facts as facts, stock_loss_facts as original
from app.formal_services.inventory_query import InventoryReadError
from test_stock_loss_headquarters_reviews import db, world, stock, allowed, evidence, regional, headquarters, reviews, state


@pytest.fixture
def approved(db, allowed, headquarters):
    role = db.scalar(select(Role).where(Role.code=='admin'))
    permission=Permission(resource='stock_operation',action=plan.ACTION,field_code='',description='Synthetic disposition')
    db.add(permission); db.flush(); db.add(RolePermission(role_id=role.id,permission_id=permission.id,effect='allow')); db.commit()
    actor=load_formal_principal(db,headquarters.actor.user_id); allowed.world.current_principal=actor
    return SimpleNamespace(actor=actor,headquarters=headquarters)


def prepare(db, approved, kind='restore_available'):
    request=approved.headquarters.request
    request=request.model_copy(update={'decisions':(request.decisions[0].model_copy(update={'disposition':kind}),)})
    review=reviews.approve_headquarters_loss(db,actor=approved.actor,request=request); db.commit()
    decision=db.scalar(select(StockLossHeadquartersDecision).where(StockLossHeadquartersDecision.review_id==review.review_id))
    return StockLossDispositionPreviewIn(headquarters_decision_id=decision.id,
        expected_headquarters_review_hash=review.request_hash,expected_submission_plan_hash=review.submission_plan_hash)


def snapshot(db):
    tables = ('stock_loss_dispositions','stock_accounts','inventory_transactions','inventory_movements',
        'inventory_movement_serials','serial_current_positions','stock_balances','audit_events',
        'state_transition_events','outbox_events','notification_events','notification_person_targets')
    return state(db), tuple((table, tuple(db.execute(text('SELECT * FROM '+table+' ORDER BY 1')))) for table in tables)


def execute_request(preview, request):
    return StockLossDispositionExecuteIn(**request.model_dump(),expected_plan_hash=preview['plan_hash'],
        request_id=uuid4().hex,idempotency_key=uuid4().hex)


@pytest.mark.parametrize('kind',['restore_available','convert_used','convert_damaged'])
def test_actual_posting_releases_only_original_line_and_replays(db, approved, kind):
    request=prepare(db,approved,kind)
    before=snapshot(db)
    prepared=plan.preview_disposition(db,actor=approved.actor,request=request)
    assert snapshot(db)==before
    target=UUID(prepared['target_account_id']); source=UUID(prepared['source_account_id'])
    was=db.get(StockBalance,target); previous=was.quantity if was else 0
    request=execute_request(prepared,request)
    result=commands.execute_disposition(db,actor=approved.actor,request=request); db.commit()
    assert result['status']=='posted' and result['disposition']==kind
    assert db.get(StockBalance,source).quantity==0
    assert db.get(StockBalance,target).quantity==previous+1
    assert all(db.get(SerialCurrentPosition,UUID(s)).stock_account_id==target for s in prepared['serial_ids'])
    row=db.get(StockLossDisposition,UUID(result['disposition_id']))
    assert facts.verified(db,row=row)==result
    assert original.submission_evidence(db,order=db.get(StockOperationOrder,row.operation_id)).status=='submitted'
    before=snapshot(db)
    assert commands.execute_disposition(db,actor=approved.actor,request=request)==result
    db.commit(); assert snapshot(db)==before


@pytest.mark.parametrize('kind',['return_to_region','scrap'])
def test_separate_business_flows_cannot_use_simple_disposition(db,approved,kind):
    request=prepare(db,approved,kind); before=snapshot(db)
    with pytest.raises(InventoryReadError) as caught:plan.preview_disposition(db,actor=approved.actor,request=request)
    assert caught.value.code=='stock_loss_disposition_requires_dedicated_flow'
    assert snapshot(db)==before


def test_notification_failure_rolls_back_new_account_posting_and_all_facts(db,approved,monkeypatch):
    request=prepare(db,approved,'convert_damaged')
    prepared=plan.preview_disposition(db,actor=approved.actor,request=request)
    assert db.get(StockAccount,UUID(prepared['target_account_id'])) is None
    before=snapshot(db)
    def fail(*args,**kwargs):
        assert db.scalar(select(StockLossDisposition.id)) is not None
        raise RuntimeError('synthetic disposition notification failure')
    monkeypatch.setattr(commands,'record_business_notification',fail)
    with pytest.raises(RuntimeError,match='synthetic disposition notification failure'):
        commands.execute_disposition(db,actor=approved.actor,request=execute_request(prepared,request))
    db.rollback(); assert snapshot(db)==before
    assert db.get(StockAccount,UUID(prepared['target_account_id'])) is None


def test_preview_is_query_only_and_preserves_missing_target(db,approved):
    request=prepare(db,approved,'convert_used'); before=snapshot(db)
    db.execute(text('PRAGMA query_only=ON'))
    first=plan.preview_disposition(db,actor=approved.actor,request=request)
    second=plan.preview_disposition(db,actor=approved.actor,request=request)
    assert first['plan_hash']==second['plan_hash']
    assert db.get(StockAccount,UUID(first['target_account_id'])) is None
    assert snapshot(db)==before and not db.new and not db.dirty


@pytest.mark.parametrize('change',['missing_permission','deny','borrowed_allow','scope','stale','inactive'])
def test_current_disposition_authority_is_independent_from_historical_review(db,allowed,approved,change):
    from app.formal_services.inventory_posting import InventoryPostingError
    request=prepare(db,approved)
    actor=approved.actor
    if change=='missing_permission':actor=replace(actor,entitlements=tuple(e for e in actor.entitlements if e.action!=plan.ACTION))
    elif change=='deny':actor=replace(actor,entitlements=actor.entitlements+(replace(next(e for e in actor.entitlements if e.action==plan.ACTION),effect='deny'),))
    elif change=='borrowed_allow':actor=replace(actor,entitlements=tuple(replace(e,role_code='provincial_manager') if e.action==plan.ACTION else e for e in actor.entitlements))
    elif change=='scope':actor=replace(actor,assignments=tuple(replace(g,scope_type='organization',scope_id=str(allowed.account.owner_org_id)) for g in actor.assignments))
    elif change=='stale':allowed.world.current_principal=replace(actor,authorization_version=actor.authorization_version+1)
    else:actor=replace(actor,account_status='suspended',access_mode='restricted_handover')
    if change!='stale':allowed.world.current_principal=actor
    before=snapshot(db)
    with pytest.raises((InventoryReadError,InventoryPostingError)) as caught:
        plan.preview_disposition(db,actor=actor,request=request)
    assert caught.value.code=={'stale':'actor_principal_stale','inactive':'actor_inactive'}.get(change,'stock_loss_disposition_forbidden')
    db.rollback(); assert snapshot(db)==before


@pytest.mark.parametrize('kind', ['restore_available', 'convert_used', 'convert_damaged'])
def test_other_current_custodian_blocks_preview_and_execution(db, allowed, approved, kind):
    request = prepare(db, approved, kind)
    preview = plan.preview_disposition(db, actor=approved.actor, request=request)
    command = execute_request(preview, request)
    now = datetime.now(timezone.utc)
    overlap = CustodyAssignment(location_id=allowed.account.location_id,
        custodian_person_id=allowed.world.headquarters_reviewer_person.id,
        valid_from=now, valid_to=now + timedelta(hours=1))
    db.add(overlap)
    db.commit()
    before = snapshot(db)
    for operation, value in ((plan.preview_disposition, request), (commands.execute_disposition, command)):
        with pytest.raises(InventoryReadError) as caught:
            operation(db, actor=approved.actor, request=value)
        assert caught.value.code == 'stock_loss_disposition_custody_changed'
        db.rollback()
        assert snapshot(db) == before
    # A closed conflict does not permanently invalidate the original stock.
    overlap.valid_to = datetime.now(timezone.utc)
    db.commit()
    current = plan.preview_disposition(db, actor=approved.actor, request=request)
    assert current['plan_hash'] == preview['plan_hash']
    result = commands.execute_disposition(db, actor=approved.actor, request=command)
    db.commit()
    assert result['status'] == 'posted'
    assert facts.verified(db, row=db.get(StockLossDisposition, UUID(result['disposition_id']))) == result


def test_changed_preview_and_posted_request_never_rebind(db,approved):
    request=prepare(db,approved,'convert_used')
    prepared=plan.preview_disposition(db,actor=approved.actor,request=request)
    execute=execute_request(prepared,request); before=snapshot(db)
    with pytest.raises(InventoryReadError) as caught:
        commands.execute_disposition(db,actor=approved.actor,request=execute.model_copy(update={'expected_plan_hash':'f'*64}))
    assert caught.value.code=='stock_loss_disposition_plan_changed'
    db.rollback(); assert snapshot(db)==before
    result=commands.execute_disposition(db,actor=approved.actor,request=execute); db.commit(); before=snapshot(db)
    for field,new in [('expected_plan_hash','f'*64),('request_id',uuid4().hex),('idempotency_key',uuid4().hex)]:
        with pytest.raises(InventoryReadError) as caught:
            commands.execute_disposition(db,actor=approved.actor,request=execute.model_copy(update={field:new}))
        assert caught.value.code=='stock_loss_disposition_conflict'
        db.rollback(); assert snapshot(db)==before
    assert facts.verified(db,row=db.get(StockLossDisposition,UUID(result['disposition_id'])))==result


def second_report(db,allowed,regional,monkeypatch):
    from app.formal_services import formal_files, stock_loss_commands, stock_loss_plan
    from app.stock_loss_schemas import StockLossSubmitIn
    from app.foundation_models import FileObject
    from test_formal_files_service import FakeStorage, SECRET
    from test_stock_loss_plan import command
    allowed.world.current_principal=allowed.actor
    storage=FakeStorage()
    result=formal_files.create_file_upload_intent(db,actor=allowed.actor,
        command=formal_files.FileUploadIntentInput(purpose='stock_loss_evidence',original_filename='第二次报损.jpg',
            sha256='3'*64,size_bytes=128,mime_type='image/jpeg'),idempotency_key=uuid4().hex,
        idempotency_hmac_secret=SECRET,trace_request_id=uuid4().hex,storage=storage,upload_ttl_seconds=60)
    file=db.get(FileObject,result.file_id); storage.materialize(file)
    formal_files.complete_file_upload(db,actor=allowed.actor,file_id=file.id,trace_request_id=uuid4().hex,storage=storage)
    value=command(allowed,(file,))
    if allowed.tracked:
        sn=allowed.serials[4]
        value=value.model_copy(update={'lines':(value.lines[0].model_copy(update={'serial_verifications':(
            value.lines[0].serial_verifications[0].model_copy(update={'serial_id':sn.id,'serial_no':sn.serial_no,'qr_code':sn.qr_code}),)}),)})
    prepared,_=stock_loss_plan.preview_loss(db,actor=allowed.actor,request=value)
    value=StockLossSubmitIn(**value.model_dump(),expected_plan_hash=prepared.plan_hash,idempotency_key=uuid4().hex,request_id=uuid4().hex)
    result=stock_loss_commands.submit_loss(db,actor=allowed.actor,request=value); db.commit()
    return result


def test_other_reports_share_frozen_account_without_losing_quantity_or_sn(db,allowed,approved,regional,monkeypatch):
    request=prepare(db,approved,'convert_damaged')
    second=second_report(db,allowed,regional,monkeypatch)
    allowed.world.current_principal=approved.actor
    prepared=plan.preview_disposition(db,actor=approved.actor,request=request)
    assert prepared['source_balance_quantity']=='2.000' and len(prepared['holds'])==2
    result=commands.execute_disposition(db,actor=approved.actor,request=execute_request(prepared,request)); db.commit()
    source=db.get(StockAccount,UUID(prepared['source_account_id']))
    remaining,_,balance=plan._remaining(db,source)
    assert balance.quantity==1
    assert sorted(r.quantity for r in remaining)==[0,1]
    if allowed.tracked:
        assert db.get(SerialCurrentPosition,allowed.serials[4].id).stock_account_id==source.id
        assert db.get(SerialCurrentPosition,allowed.serials[3].id).stock_account_id==UUID(prepared['target_account_id'])
    assert facts.verified(db,row=db.get(StockLossDisposition,UUID(result['disposition_id'])))==result
    assert original.submission_evidence(db,order=db.get(StockOperationOrder,second.operation_id)).status=='submitted'


@pytest.mark.parametrize('field',['quantity','line_id','headquarters_decision_id','posting_movement_id','target_account_id','command_jsonb','plan_jsonb'])
def test_invalid_reference_or_tampered_fact_cannot_claim_a_release(db,approved,field):
    request=prepare(db,approved,'convert_used')
    prepared=plan.preview_disposition(db,actor=approved.actor,request=request)
    result=commands.execute_disposition(db,actor=approved.actor,request=execute_request(prepared,request)); db.commit()
    row=db.get(StockLossDisposition,UUID(result['disposition_id']))
    # SQLite fixture intentionally lacks production immutable triggers. These
    # corruptions exercise the read proof; PG COMMIT refusal is a separate gate.
    setattr(row,field,2 if field=='quantity' else {} if field.endswith('jsonb') else uuid4())
    if field in {'line_id','headquarters_decision_id','target_account_id'}:
        from sqlalchemy.exc import IntegrityError
        # An absent foreign key is refused before the read proof can run.
        with pytest.raises(IntegrityError,match='FOREIGN KEY constraint failed'):
            db.flush()
    else:
        db.flush()
        with pytest.raises(InventoryReadError):facts.verified(db,row=row)
        if field=='posting_movement_id':
            from sqlalchemy.exc import IntegrityError
            # The posting link is intentionally DEFERRABLE INITIALLY DEFERRED.
            with pytest.raises(IntegrityError,match='FOREIGN KEY constraint failed'):
                db.commit()
    db.rollback()
    assert facts.verified(db,row=db.get(StockLossDisposition,UUID(result['disposition_id'])))==result


def test_generic_inverse_cannot_rename_original_disposition(db,approved):
    from datetime import datetime, timezone
    from app.formal_services import inventory_posting as posting
    request=prepare(db,approved)
    prepared=plan.preview_disposition(db,actor=approved.actor,request=request)
    result=commands.execute_disposition(db,actor=approved.actor,request=execute_request(prepared,request)); db.commit()
    before=snapshot(db)
    inverse=posting.InventoryReversalCommand(original_transaction_id=UUID(result['posting_transaction_id']),
        transaction_no='LOSS-ILLEGAL-INVERSE',source_document_type='renamed_generic_adjustment',
        source_document_id=str(uuid4()),posting_key=uuid4().hex,effective_at=datetime.now(timezone.utc))
    with pytest.raises(posting.InventoryPostingError) as caught:
        posting.reverse_inventory_transaction(db,actor=approved.actor,command=inverse,idempotency_key=uuid4().hex,request_id=uuid4().hex)
    assert caught.value.code=='stock_loss_disposition_reversal_requires_command'
    db.rollback(); assert snapshot(db)==before


def test_former_applicant_and_reviewers_do_not_become_execution_authority(db,allowed,approved,regional):
    from app.models import User
    from app.foundation_models import Organization
    from test_formal_access import make_user, assign
    request=prepare(db,approved)
    # A different currently authorized headquarters executor may finish an
    # established decision after original people leave; their grants stay historical.
    org=db.scalar(select(Organization).where(Organization.org_type=='headquarters'))
    user,_=make_user(db,org,name='Synthetic distinct executor')
    role=db.scalar(select(Role).where(Role.code=='admin'))
    assign(db,user,role,scope_type='national',scope_id='*')
    for identifier in (allowed.actor.user_id,regional.actor.user_id,approved.actor.user_id):
        former=db.get(User,identifier); former.is_active=False; former.account_status='suspended'
    db.commit()
    actor=load_formal_principal(db,user.id); allowed.world.current_principal=actor
    prepared=plan.preview_disposition(db,actor=actor,request=request)
    result=commands.execute_disposition(db,actor=actor,request=execute_request(prepared,request)); db.commit()
    assert result['executor_person_id']==str(actor.person_id)
    assert result['status']=='posted'


def test_later_freeze_does_not_rewrite_prior_disposition_snapshot(db,allowed,approved,regional,monkeypatch):
    request=prepare(db,approved)
    prepared=plan.preview_disposition(db,actor=approved.actor,request=request)
    result=commands.execute_disposition(db,actor=approved.actor,request=execute_request(prepared,request)); db.commit()
    second_report(db,allowed,regional,monkeypatch)
    allowed.world.current_principal=approved.actor
    row=db.get(StockLossDisposition,UUID(result['disposition_id']))
    assert facts.verified(db,row=row)==result
    remaining,_,balance=plan._remaining(db,db.get(StockAccount,row.source_account_id))
    assert balance.quantity==1 and sorted(r.quantity for r in remaining)==[0,1]


def test_client_cannot_override_approved_dimensions(approved):
    from pydantic import ValidationError
    request=dict(headquarters_decision_id=uuid4(),expected_headquarters_review_hash='a'*64,expected_submission_plan_hash='b'*64)
    for field,value in {'disposition':'scrap','quantity':'100','target_account_id':str(uuid4()),'serial_ids':[str(uuid4())]}.items():
        with pytest.raises(ValidationError):StockLossDispositionPreviewIn.model_validate(request|{field:value})


def test_existing_but_wrong_movement_cannot_supply_line_evidence(db,approved):
    from app.inventory_models import InventoryMovement
    request=prepare(db,approved,'convert_used')
    prepared=plan.preview_disposition(db,actor=approved.actor,request=request)
    result=commands.execute_disposition(db,actor=approved.actor,request=execute_request(prepared,request)); db.commit()
    row=db.get(StockLossDisposition,UUID(result['disposition_id']))
    other=db.scalar(select(InventoryMovement.id).where(InventoryMovement.transaction_id!=row.posting_transaction_id))
    assert other is not None
    row.posting_movement_id=other; db.flush()
    with pytest.raises(InventoryReadError) as caught:facts.verified(db,row=row)
    assert caught.value.code=='stock_loss_disposition_evidence_invalid'
    db.rollback()
    assert facts.verified(db,row=db.get(StockLossDisposition,UUID(result['disposition_id'])))==result
