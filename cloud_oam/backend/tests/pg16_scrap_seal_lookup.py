"""Actual closure composition and READ ONLY recovery after write revocation."""
from uuid import uuid4
from sqlalchemy import text
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.stock_scrap_schemas import ScrapExecute, ScrapRequestLookup, ScrapRequestSeal
from app.stock_scrap_recovery_schemas import ScrapRecoveryRequestLookup, ScrapRecoveryRequestSeal
from app.formal_services.inventory_query import InventoryReadError
from app.formal_services.stock_scrap import request_lookup, recovery_lookup, request_seals
from pg16_scrap_seal_sources import arguments
from pg16_scrap_seal_persistence import snapshot, only_closure_changed


def model(command, *, close=False):
    if type(command) is ScrapExecute:
        return ScrapRequestSeal if close else ScrapRequestLookup
    return ScrapRecoveryRequestSeal if close else ScrapRecoveryRequestLookup


def handler(command):
    return request_lookup.lookup if type(command) is ScrapExecute else recovery_lookup.lookup


def create(context):
    owner,api=(context['engines'][n] for n in ('star_oam_migrator','star_oam_api'))
    kinds={}
    for actor,command,_ in context['scrap_lookup_cases']+context['scrap_recovery_lookup_cases']:
        kinds.setdefault(arguments(command)['kind'],(actor,command))
    result=[]
    from pg16_scrap_seal_revocation import commit_with_revocation_races
    for kind,(actor_id,original) in kinds.items():
        command=original.model_copy(update=dict(request_id=uuid4().hex,idempotency_key=uuid4().hex))
        before=snapshot(owner)
        answer=commit_with_revocation_races(context,actor_id=actor_id,command=command)
        assert answer['request_state']=='sealed' and answer['retry_allowed'] is False
        only_closure_changed(before,snapshot(owner))
        result.append((actor_id,command,answer))
    context['composed_seal_cases']=result
    return dict(sixKindsCommitted=sorted(kinds),onlySealAndAuditChanged=True,publicHttp=False)


def read(context):
    owner,api=(context['engines'][n] for n in ('star_oam_migrator','star_oam_api'))
    from pg16_scrap_closed_execution import require_denial
    original=[context['original_closed_request'],*context['closed_scrap_requests'],*context.get('closed_stage_requests',[]),
              *context.get('cross_registry_seal_cases',[])]
    cases=[(a,c,r['seal']['id']) for a,c,r in original]
    cases += [(a,c,r['seal']['seal_id']) for a,c,r in context['composed_seal_cases']]
    before=snapshot(owner);refused=0
    for index,(actor_id,command,identifier) in enumerate(cases):
        with Session(api) as db:
            db.execute(text('SET TRANSACTION READ ONLY'))
            db.execute(text('SELECT set_config(\'TimeZone\',:zone,true)'),
                dict(zone=('UTC','Asia/Shanghai','America/Los_Angeles')[index%3]))
            actor=load_formal_principal(db,actor_id)
            query=model(command)(operator_person_id=actor.person_id,original=command)
            answer=handler(command)(db,actor=actor,request=query)
            assert answer['request_state']=='sealed' and answer['retry_allowed'] is False
            assert answer['seal']['seal_id']==identifier and answer['seal']['sealed_at'].endswith('Z')
            if type(command) is ScrapExecute and command.source.kind=='original':
                assert answer['seal']['root_disposition_id'] is None
            for field in ('idempotency_key','request_id', 'execution_reason' if type(command) is ScrapExecute else 'reason'):
                changed=command.model_copy(update={field:uuid4().hex})
                try:handler(command)(db,actor=actor,request=query.model_copy(update={'original':changed}))
                except InventoryReadError as error:
                    assert error.status_code==409,error.code
                    refused+=1
                else:raise AssertionError('changed seal request recovered')
        # Repeating closure needs only today's read rights once the exact seal
        # exists. This uses a normal transaction for the ledger/principal lock.
        with Session(api) as db:
            actor=load_formal_principal(db,actor_id)
            same=request_seals.seal(db,actor=actor,request=model(command,close=True)(operator_person_id=actor.person_id,original=command))
            assert same==answer
            require_denial(db, actor=actor, command=command)
            for field in ('idempotency_key','request_id', 'execution_reason' if type(command) is ScrapExecute else 'reason'):
                require_denial(db, actor=actor, command=command.model_copy(update={field:uuid4().hex}), conflict=True)
            db.commit()
        assert snapshot(owner)==before
    for actor_id,command,expected in context['scrap_lookup_cases']+context['scrap_recovery_lookup_cases']:
        with Session(api) as db:
            actor=load_formal_principal(db,actor_id)
            answer=request_seals.seal(db,actor=actor,request=model(command,close=True)(operator_person_id=actor.person_id,original=command))
            assert answer['request_state']=='found' and answer['result']==expected and answer['retry_allowed'] is False
            db.commit()
        assert snapshot(owner)==before
    return dict(passed=True,exactSealsReadOnlyAfterWriteGrantsRemoved=len(cases),changedCommandsRejected=refused,
        repeatedClosureNoWrites=len(cases),executedClosureReturnsOriginal=10,fullDatabaseUnchanged=True,
        closedExecutionDeniedAfterWriteGrantsRemoved=len(cases),changedExecutionDenied=len(cases)*3,
        readerTimezones=['UTC','Asia/Shanghai','America/Los_Angeles'],productionAcceptance=False)
