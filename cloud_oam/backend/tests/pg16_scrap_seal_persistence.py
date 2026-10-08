"""Actual API transactions for candidate seals; no HTTP activation claim."""
from datetime import datetime, timezone
from uuid import UUID, uuid4
from sqlalchemy import text
from sqlalchemy.orm import Session
from sqlalchemy.exc import DBAPIError
from app.formal_access import load_formal_principal
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.stock_scrap import bound_commands
from pg16_scrap_seal_sources import arguments
from pg16_stock_scrap_structure_gate import original_columns, facts

CALL = text('SELECT public.rsc_register_scrap_seal_0165('
    ':actor,:version,:person,:kind,CAST(:command AS jsonb),:key)')


def register(db, actor_id, command, *, audit=True, bad_audit=False):
    actor = load_formal_principal(db, actor_id)
    params = arguments(command) | dict(actor=actor.user_id, version=actor.authorization_version, person=actor.person_id)
    answer = db.scalar(CALL, params)
    seal = answer['seal']
    if answer['created'] and audit:
        at = datetime.fromisoformat(seal['created_at'])
        append_audit_event(db, stream_key='inventory', actor_user_id=actor.user_id,
            action='seal_scrap_request', aggregate_type='stock_scrap_request_seal', aggregate_id=seal['id'],
            before_jsonb={}, after_jsonb=answer['audit_payload'] | ({'stock_effect':'wrong'} if bad_audit else {}),
            request_id='scrap-seal:'+seal['id'], occurred_at=at, created_at=at)
    return answer


def snapshot(owner):
    with owner.connect() as db:
        return facts(db, original_columns(db))


def only_closure_changed(before, after):
    assert set(before)==set(after)
    assert {name for name in before if before[name]!=after[name]} == {
        'stock_scrap_request_seals','audit_events','audit_chain_heads'}


def rejected_transaction(engine, action, *, phase, message=None):
    reached = 'statement'
    with Session(engine) as db:
        try:
            action(db)
            reached = 'commit'
            db.commit()
        except DBAPIError as error:
            assert error.orig.sqlstate == '23514', error.orig
            assert reached == phase, (phase, reached, error.orig)
            if message:
                assert message in error.orig.diag.message_primary, error.orig
            db.rollback()
        else:
            raise AssertionError('invalid seal transaction committed')


def before_original(context, command):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    actor_id = context['admin_id']
    closed = command.model_copy(update=dict(request_id=uuid4().hex, idempotency_key=uuid4().hex))
    before = snapshot(owner)
    def orphan_audit(db):
        at=datetime.now(timezone.utc)
        identifier=str(uuid4())
        append_audit_event(db,stream_key='inventory',actor_user_id=actor_id,action='seal_scrap_request',
            aggregate_type='stock_scrap_request_seal',aggregate_id=identifier,before_jsonb={},
            after_jsonb={'id':identifier},request_id='scrap-seal:'+identifier,occurred_at=at,created_at=at)
    rejected_transaction(api,orphan_audit,phase='commit',message='exact retained seal required')
    assert snapshot(owner)==before
    for label, options in (('missing_audit',dict(audit=False)), ('wrong_audit',dict(bad_audit=True))):
        rejected_transaction(api, lambda db:register(db,actor_id,closed,**options), phase='commit',
            message='exactly one seal audit')
        assert snapshot(owner) == before, label
    from pg16_scrap_seal_races import commit_seal
    result = commit_seal(context, actor_id=actor_id, command=closed)
    assert not result['created'] and result['seal']['root_disposition_id'] is None
    retained = snapshot(owner)
    only_closure_changed(before,retained)
    def duplicate_audit(db):
        at=datetime.now(timezone.utc)
        append_audit_event(db,stream_key='inventory',actor_user_id=actor_id,action='seal_scrap_request',
            aggregate_type='stock_scrap_request_seal',aggregate_id=result['seal']['id'],before_jsonb={},
            after_jsonb=result['audit_payload'],request_id='scrap-seal-extra:'+str(uuid4()),occurred_at=at,created_at=at)
    rejected_transaction(api,duplicate_audit,phase='commit',message='exactly one seal audit')
    assert snapshot(owner)==retained
    with Session(api) as db:
        same = register(db, actor_id, closed)
        assert not same['created'] and same['seal'] == result['seal']
        db.commit()
    assert snapshot(owner) == retained

    from pg16_scrap_closed_execution import require_denial, raw_write
    with Session(api) as db:
        require_denial(db, actor=load_formal_principal(db, actor_id), command=closed)
        db.commit()
    assert snapshot(owner) == retained
    def late(db):
        actor = load_formal_principal(db, actor_id)
        raw_write(db, actor=actor, command=closed)
    rejected_transaction(api,late,phase='commit',message='sealed key or request has another registry fact')
    assert snapshot(owner) == retained
    with owner.connect() as db:
        assert db.scalar(text('SELECT count(*) FROM stock_loss_dispositions')) == 0
        assert db.scalar(text('SELECT count(*) FROM stock_scrap_request_seals')) == 1
    context['original_closed_request'] = (actor_id,closed,result)
    context.setdefault('closed_stage_write_proofs', {})['original'] = dict(
        serviceCode='request_sealed', rawBypassPhase='commit', sqlstate='23514', fullRollback=True)
    return dict(passed=True, originalWithoutRootCommitted=True, uniqueAudit=True,
        missingAndWrongAuditCommitRejected=True, repeatNoWrites=True,
        orphanAndDuplicateAuditCommitRejected=True, onlySealAndAuditChanged=True,
        lateActualScrapCommitRejected=True, fullRollback=True, productionAcceptance=False)


def after_history(context):
    owner, api = (context['engines'][name] for name in ('star_oam_migrator', 'star_oam_api'))
    cases = {}
    for actor,command,_ in context['scrap_lookup_cases'] + context['scrap_recovery_lookup_cases']:
        cases.setdefault(arguments(command)['kind'],(actor,command))
    assert len(cases)==6
    closed_cases=[]
    rejected=0
    for kind,(actor,executed) in cases.items():
        before=snapshot(owner)
        # A real executed request must never gain a seal, even with matching
        # source and present-day write grants. The service will return history.
        rejected_transaction(api,lambda db:register(db,actor,executed),phase='statement')
        assert snapshot(owner)==before
        rejected+=1
        command=executed.model_copy(update=dict(request_id=uuid4().hex,idempotency_key=uuid4().hex))
        with Session(api) as db:
            answer=register(db,actor,command)
            assert answer['created'] and answer['seal']['kind']==kind
            db.commit()
        closed_cases.append((actor,command,answer))
        retained=snapshot(owner)
        only_closure_changed(before,retained)
        with Session(api) as db:
            same=register(db,actor,command)
            assert not same['created'] and same['seal']==answer['seal']
            db.commit()
        assert snapshot(owner)==retained
        # Creation-time current authority must be repeated at real COMMIT,
        # without keeping plaintext keys anywhere in the database/session.
        other=command.model_copy(update=dict(request_id=uuid4().hex,idempotency_key=uuid4().hex))
        def revoke(db):
            register(db,actor,other)
            db.execute(text('UPDATE users SET authorization_version=authorization_version+1 WHERE id=:id'),dict(id=actor))
        rejected_transaction(owner,revoke,phase='commit',message='current recovery identity invalid')
        assert snapshot(owner)==retained
        rejected+=1
    # Direct API writes and private proof calls stay closed. Trusted owner
    # mutation is rejected by always-enabled append-only triggers as well.
    retained=snapshot(owner)
    identifier=UUID(closed_cases[0][2]['seal']['id'])
    denied=[]
    for engine,statements,state in (
        (api,('INSERT INTO stock_scrap_request_seals SELECT * FROM stock_scrap_request_seals WHERE false',
            'UPDATE stock_scrap_request_seals SET reason=reason WHERE id=:id',
            'DELETE FROM stock_scrap_request_seals WHERE id=:id','TRUNCATE stock_scrap_request_seals',
            'SELECT public.rsc_check_scrap_seal_0165(:id)'), '42501'),
        (owner,('UPDATE stock_scrap_request_seals SET reason=reason WHERE id=:id',
            'DELETE FROM stock_scrap_request_seals WHERE id=:id','TRUNCATE stock_scrap_request_seals'), '23514'),
    ):
        for sql in statements:
            with engine.connect() as db:
                try:db.execute(text(sql),dict(id=identifier));db.commit()
                except DBAPIError as error:
                    assert error.orig.sqlstate==state,error.orig
                    db.rollback();denied.append(state)
                else:raise AssertionError('direct seal mutation accepted')
    assert snapshot(owner)==retained
    with owner.begin() as db:
        for zone in ('UTC','Asia/Shanghai','America/Los_Angeles'):
            db.execute(text('SELECT set_config(\'TimeZone\',:zone,true)'),dict(zone=zone))
            for _,_,answer in [context['original_closed_request'],*closed_cases,*context.get('closed_stage_requests',[]),
                               *context.get('cross_registry_seal_cases',[])]:
                params=dict(id=UUID(answer['seal']['id']))
                db.execute(text('SELECT public.rsc_check_scrap_seal_0165(:id)'),params)
                assert db.scalar(text('SELECT public.rsc_scrap_seal_payload_0165(:id)'),params)==answer['audit_payload']
        retained_count=7+len(context.get('closed_stage_requests',[]))+len(context.get('cross_registry_seal_cases',[]))
        assert db.scalar(text('SELECT count(*) FROM stock_scrap_request_seals'))==retained_count
    assert snapshot(owner)==retained
    context['closed_scrap_requests']=closed_cases
    return dict(passed=True, sixKindsCommitted=sorted(cases), exactRepeatsNoWrites=6,
        actualExecutedRequestsRefused=6, lateVersionCommitRejected=6, fullRollback=True,
        apiDirectWritesAndPrivateCallsDenied=5, ownerMutationsDenied=3, sealsRetained=retained_count,
        auditPayloadTimezonesVerified=['UTC','Asia/Shanghai','America/Los_Angeles'],onlySealAndAuditChanged=True,
        historicalSourceRechecked=True, publicLookupIntegrated=False, concurrencyProven=False,
        formalMigration=False, productionAcceptance=False)
