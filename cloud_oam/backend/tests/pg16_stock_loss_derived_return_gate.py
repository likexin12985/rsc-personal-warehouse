"""Shared native/CI acceptance of approved-loss derived return facts and 0152."""
from datetime import datetime,timedelta,timezone
from uuid import UUID,uuid4
from unittest.mock import patch
from sqlalchemy import text

def run(context):
    from sqlalchemy import select,event,text
    from sqlalchemy.orm import Session
    from sqlalchemy.exc import DBAPIError
    from app.formal_access import load_formal_principal
    from app.foundation_models import OutboxEvent, StateTransitionEvent
    from app.inventory_models import StockLocation, CustodyAssignment
    from app.stock_operation_models import StockLossHeadquartersDecision,StockLossHeadquartersReview,StockOperationOrder,StockOperationLine,StockOperationSerial,StockLossDisposition
    from app.stock_loss_return_schemas import StockLossReturnExecuteIn
    from app.formal_services import stock_loss_return_plan as plan,stock_loss_return_commands as commands,stock_loss_return_facts as facts
    from app.formal_services import stock_loss_disposition_facts as dispositions
    from pg16_stock_loss_return_preview_gate import run as preview_gate
    from pg16_stock_loss_disposition_gate import snapshot
    assert preview_gate(context)['passed']
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    # The read-only gate intentionally ends the receiving assignment to prove
    # expired custody is refused. Establish a new synthetic current assignment;
    # never reopen its historical interval or seed a stock account.
    with Session(owner) as db:
        source_location=db.get(StockLocation,context['location_id'])
        receiver=db.get(StockLocation,source_location.parent_id)
        prior=db.scalars(select(CustodyAssignment).where(CustodyAssignment.location_id==receiver.id)).one()
        assert prior.valid_to is not None and prior.valid_to <= datetime.now(timezone.utc)
        db.add(CustodyAssignment(location_id=receiver.id,custodian_person_id=receiver.custodian_person_id,
            valid_from=datetime.now(timezone.utc)))
        db.commit()
    with Session(api) as db:
        decision=db.scalars(select(StockLossHeadquartersDecision)).one()
        review=db.get(StockLossHeadquartersReview,decision.review_id)
        order=db.get(StockOperationOrder,review.operation_id)
        transit=db.scalars(select(StockLocation).where(StockLocation.code.like('LOSS-RETURN-TRANSIT-%'))).one()
        actor=load_formal_principal(db,context['admin_id'])
        request=StockLossReturnExecuteIn(headquarters_decision_id=decision.id,expected_headquarters_review_hash=review.request_hash,
            expected_submission_plan_hash=order.plan_hash,target_location_id=transit.parent_id,transit_location_id=transit.id,
            expected_plan_hash='0'*64,request_id=uuid4().hex,idempotency_key=uuid4().hex)
        preview=plan.preview_loss_return(db,actor=actor,request=request)
        request=request.model_copy(update={'expected_plan_hash':preview['plan_hash']})
    before=snapshot(owner)
    damages=['child_outbox','child_quantity','root_quantity','child_reason','child_requester','child_actor',
        'child_condition','child_custody','child_command','child_state','root_outbox','root_plan']
    if context['tracking']=='serial':damages+=['child_serial_missing','child_serial_unverified']
    for damage in damages:
        with Session(api) as db:
            def mutate(session,*_):
                for row in tuple(session.new):
                    if damage=='child_outbox' and isinstance(row,OutboxEvent) and row.event_type==facts.CHILD_KIND:
                        session.expunge(row)
                    elif damage=='child_quantity' and isinstance(row,StockOperationLine) and row.source_loss_line_id is not None:
                        row.quantity+=1
                    elif damage=='root_quantity' and isinstance(row,StockLossDisposition):row.quantity+=1
                    elif damage=='child_state' and isinstance(row,StateTransitionEvent) and row.reason==facts.CHILD_KIND:
                        session.expunge(row)
                    elif damage=='root_outbox' and isinstance(row,OutboxEvent) and row.event_type==facts.KIND:
                        session.expunge(row)
                    elif damage=='root_plan' and isinstance(row,StockLossDisposition):
                        row.plan_jsonb=dict(row.plan_jsonb,unexpected='tamper')
                    elif isinstance(row,StockOperationOrder) and row.loss_headquarters_decision_id is not None:
                        if damage=='child_reason':row.reason+=' tampered'
                        elif damage=='child_requester':row.requester_id=actor.person_id
                        elif damage=='child_actor':row.actor_user_id=context['engineer_id']
                        elif damage=='child_custody':row.target_custody_assignment_id=UUID(preview['source_custody_assignment_id'])
                        elif damage=='child_command':row.command_jsonb=dict(row.command_jsonb,unexpected='tamper')
                    elif damage=='child_condition' and isinstance(row,StockOperationLine) and row.source_loss_line_id is not None:
                        row.target_condition='used'
                    elif isinstance(row,StockOperationSerial):
                        if damage=='child_serial_missing':session.expunge(row)
                        elif damage=='child_serial_unverified':row.sku_verified=False
            event.listen(db,'before_flush',mutate)
            with patch.object(dispositions,'verified',return_value=None):
                commands.execute_loss_return(db,actor=load_formal_principal(db,actor.user_id),request=request)
            try:db.commit()
            except DBAPIError as error:
                assert error.orig.sqlstate=='23514';db.rollback()
            else:raise AssertionError('malformed draft COMMIT accepted: '+damage)
        assert snapshot(owner)==before
        print('PG16 loss-derived return '+context['tracking']+': '+damage+' rejected at API COMMIT and rolled back',flush=True)
    # Force the real deferred proofs while the API transaction is open, then
    # prove both responsibility parents serialize a second connection's FK.
    with Session(api) as db:
        for table in ('stock_locations','custody_assignments'):
            assert not db.scalar(text("SELECT has_table_privilege(current_user,:table,'UPDATE')"),{'table':table})
        commands.execute_loss_return(db,actor=load_formal_principal(db,actor.user_id),request=request)
        db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
        for location_id in (context['location_id'],request.target_location_id):
            with Session(owner) as competing:
                competing.execute(text("SET LOCAL lock_timeout = '250ms'"))
                competing.execute(text("SET LOCAL statement_timeout = '2s'"))
                at=datetime.now(timezone.utc)
                competing.add(CustodyAssignment(location_id=location_id,custodian_person_id=actor.person_id,
                    valid_from=at,valid_to=at+timedelta(hours=1)))
                try:competing.flush()
                except DBAPIError as error:
                    assert error.orig.sqlstate=='55P03';competing.rollback()
                else:raise AssertionError('competing custody insertion was not blocked')
        db.rollback()
    assert snapshot(owner)==before
    print('PG16 loss-derived return '+context['tracking']+': private proof locks source and receiver; API masters remain SELECT-only',flush=True)
    from pg16_stock_loss_derived_return_boundaries import commit_expiry,reuse_pending
    expiry=commit_expiry(context,request)
    from concurrent.futures import ThreadPoolExecutor
    import threading
    barrier=threading.Barrier(2)
    def concurrent_execute(_):
        with Session(api) as db:
            current=load_formal_principal(db,actor.user_id)
            barrier.wait(timeout=15)
            answer=commands.execute_loss_return(db,actor=current,request=request)
            db.commit();return answer
    with ThreadPoolExecutor(max_workers=2) as pool:
        first,second=tuple(pool.map(concurrent_execute,range(2)))
    assert first==second
    result=first
    after=snapshot(owner)
    with Session(api) as db:
        assert commands.execute_loss_return(db,actor=load_formal_principal(db,actor.user_id),request=request)==result
        db.commit()
    assert snapshot(owner)==after
    reuse=reuse_pending(context,first=result,request=request)
    return {'passed':True,'commitTimeAuthorityExpiry':expiry,'sameRequestConcurrentRepliesEqual':True,'existingPendingAccountReuse':reuse,'tracking':context['tracking'],'actualDerivedReturnApiCommit':True,'malformedCommitRollbacks':len(damages),'malformedCases':damages,
        'exactReplayNoAdditionalFacts':True,'sourceAndReceiverConcurrentCustodyBlocked':True,'apiMasterUpdateNotGranted':True,'result':result,'formalMigrationInstalled':True,'productionAcceptance':False}

def catalog(owner):
    from sqlalchemy import text
    with owner.connect() as db:
        functions=tuple(db.execute(text("SELECT p.oid::regprocedure::text,p.proname,p.prosrc,p.proowner,p.proacl::text,p.proconfig FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public' ORDER BY p.oid::regprocedure::text")))
        constraints=tuple(db.execute(text("SELECT c.relname,k.conname,pg_get_constraintdef(k.oid) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' ORDER BY c.relname,k.conname")))
        triggers=tuple(db.execute(text("SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid),t.tgenabled FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND NOT t.tgisinternal ORDER BY c.relname,t.tgname")))
        roles=tuple(db.execute(text("SELECT c.relname,c.relowner,c.relacl::text FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname='public' AND c.relkind='r' ORDER BY c.relname")))
        return functions,constraints,triggers,roles




def release(engines,*,tracking,migrate,provision):
    if tracking not in ('quantity','serial'):raise ValueError('tracking must be quantity or serial')
    from app.database_security import validate_production_database_security
    from pg16_stock_loss_sources_gate import run as sources
    from pg16_stock_loss_disposition_gate import snapshot
    from test_postgresql16_release_gate import HEAD_REVISION
    owner,api=(engines[k] for k in ('star_oam_migrator','star_oam_api'))
    def security():
        validate_production_database_security(api,expected_runtime_role='star_oam_api',expected_migration_role='star_oam_migrator')
    migrate('initial-upgrade','upgrade','head');provision();security()
    before=catalog(owner)
    migrate('empty-downgrade','downgrade','20261130_0151')
    migrate('empty-reupgrade','upgrade','head')
    assert catalog(owner)==before
    security()
    with owner.connect() as db:assert db.scalar(text('SELECT version_num FROM alembic_version'))==HEAD_REVISION
    result=sources(engines,tracking=tracking,after_preview=run)
    result['returnSubmission']=result.pop('submission')
    assert result['passed'] and result['returnSubmission']['passed']
    before_facts=snapshot(owner);before=catalog(owner)
    migrate('retained-derived-return-downgrade','downgrade','20261130_0151','0152 derived-return provenance history requires retention')
    assert snapshot(owner)==before_facts and catalog(owner)==before
    security()
    result.update(runtimeSecurityBeforeAndAfter=True,emptyMigrationRoundtripCatalogAndAclExact=True,
        derivedReturnHistoryPreventsDowngrade=True,migrationHead=HEAD_REVISION)
    return result
