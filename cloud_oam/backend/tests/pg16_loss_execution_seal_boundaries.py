"""Candidate API-role execution/sealing races on already-approved real loss facts.

Caller supplies fresh owned PG16 engines and an approval built by the existing
opening/loss services. No production DSN or permission seed is accepted here.
This module is not yet a passed gate or a formal CI entry point.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from threading import Barrier
from uuid import uuid4
from unittest.mock import patch
import time

import pytest
from sqlalchemy import select,text,event
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import Role,RoleAssignment
from app.inventory_models import StockLocation
from app.stock_operation_models import StockLossHeadquartersDecision,StockOperationLine,StockOperationOrder
from app.formal_services import stock_loss_disposition_facts as facts
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.inventory_query import InventoryReadError
from app.stock_loss_disposition_seal_schemas import command_document,StockLossDispositionSealIn,StockLossDerivedReturnSealIn
from app.stock_operation_models import StockLossDispositionRequestSeal as Seal
from app.formal_services import stock_loss_disposition_seals as seals
from app.formal_services import stock_loss_disposition_recovery as recovery
from app.formal_services import stock_loss_disposition_commands as dispositions
from app.formal_services import stock_loss_return_commands as returns
from pg16_stock_loss_disposition_gate import snapshot as original_snapshot


def snapshot(owner):
    result=original_snapshot(owner)
    with owner.connect() as db:
        result[Seal.__tablename__]=tuple(sorted(repr(dict(row)) for row in db.execute(
            text('SELECT * FROM '+Seal.__tablename__)).mappings()))
    return result


def actor(db,context):
    return load_formal_principal(db,context['admin_id'])


def lookup(db,context,command,flow):
    return recovery.lookup_disposition_request(db,actor=actor(db,context),request=command,flow=flow)


def close(db,context,command,flow):
    principal=actor(db,context)
    schema=StockLossDispositionSealIn if flow=='disposition' else StockLossDerivedReturnSealIn
    return seals.seal_execution_request(db,actor=principal,flow=flow,
        request=schema(operator_person_id=principal.person_id,original=command))


def execute(db,context,command,flow):
    method=dispositions.execute_disposition if flow=='disposition' else returns.execute_loss_return
    return method(db,actor=actor(db,context),request=command)


def raw_seal(db,context,command,flow,*,audit=True,mutate=None):
    """Bypass only Python admission; PG16 still validates the inserted facts."""
    principal=actor(db,context);proof=command_document(command,flow)
    decision=db.get(StockLossHeadquartersDecision,command.headquarters_decision_id)
    line=db.get(StockOperationLine,decision.line_id);parent=db.get(StockOperationOrder,line.operation_id)
    at=datetime.now(timezone.utc)
    row=Seal(id=uuid4(),flow=flow,operation_id=parent.id,operation_type='loss_report',line_id=line.id,
        headquarters_decision_id=decision.id,owner_org_id=db.get(StockLocation,parent.source_location_id).owner_org_id,
        actor_user_id=principal.user_id,executor_person_id=principal.person_id,
        authorization_version=principal.authorization_version,request_id=command.request_id,
        request_reference=proof['request_reference'],disposition_key_hash=proof['disposition_key_hash'],
        return_key_hash=proof['return_key_hash'],request_hash=proof['request_hash'],
        plan_hash=command.expected_plan_hash,command_jsonb=proof['command_jsonb'],created_at=at)
    if mutate is not None:mutate(row)
    db.add(row);db.flush()
    if audit:
        append_audit_event(db,stream_key='inventory',actor_user_id=principal.user_id,action=seals.KIND,
            aggregate_type=seals.AGGREGATE,aggregate_id=str(row.id),before_jsonb={},after_jsonb=seals.payload(row),
            request_id='stock-loss-disposition-seal:'+str(row.id),occurred_at=at,created_at=at)
        db.flush()


def fresh(command):
    return command.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})


def verify(context,command,flow):
    if flow not in ('disposition','return'):raise ValueError('explicit execution flow required')
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    with owner.connect() as db:
        assert db.scalar(text('SELECT current_database()'))=='rsc_pg16_release_gate'
        assert db.scalar(text('SELECT current_user'))=='star_oam_migrator'
        assert int(db.scalar(text('SHOW server_version_num')))//10000==16
        assert db.scalar(text('SELECT version_num FROM alembic_version'))=='20261212_0163'
    before=snapshot(owner)
    with Session(api) as db:
        execute(db,context,fresh(command),flow)
        db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'));db.rollback()
    assert snapshot(owner)==before
    # Commit-time database proof is independent of the service's readback.
    defects={
        'missing_audit':None,
        'request_reference':lambda row:setattr(row,'request_reference','inventory-request-'+'f'*64),
        'request_hash':lambda row:setattr(row,'request_hash','f'*64),
        'plan_hash':lambda row:setattr(row,'plan_hash','f'*64),
        'authorization_version':lambda row:setattr(row,'authorization_version',row.authorization_version+1),
        'command':lambda row:setattr(row,'command_jsonb',{**row.command_jsonb,'unexpected':True}),
    }
    for kind,mutate in defects.items():
        with Session(api) as db:
            raw_seal(db,context,fresh(command),flow,audit=kind!='missing_audit',mutate=mutate)
            with pytest.raises(DBAPIError) as error:db.commit()
            assert error.value.orig.sqlstate=='23514', (kind,error.value.orig.sqlstate,str(error.value.orig)[:500]);db.rollback()
        assert snapshot(owner)==before,kind
    closed=fresh(command)
    with Session(api) as db:
        sealed=close(db,context,closed,flow);db.commit()
        assert sealed['lookup_status']=='sealed' and sealed['seal']['stock_effect']=='none'
    after=snapshot(owner)
    assert {name for name in before if before[name]!=after[name]}=={
        Seal.__tablename__,'audit_events','audit_chain_heads'}
    assert len(after[Seal.__tablename__])==len(before[Seal.__tablename__])+1
    with Session(api) as db:
        assert lookup(db,context,closed,flow)==sealed
        assert close(db,context,closed,flow)==sealed;db.commit()
    assert snapshot(owner)==after
    for altered in (closed,closed.model_copy(update={'request_id':uuid4().hex}),
            closed.model_copy(update={'idempotency_key':uuid4().hex})):
        with Session(api) as db:
            with pytest.raises(InventoryReadError) as error:execute(db,context,altered,flow)
            assert error.value.code=='stock_loss_disposition_request_sealed';db.rollback()
        # Real posting/events execute while only application seal refusal and
        # final Python proof are bypassed; actual COMMIT must reject the write.
        with Session(api) as db:
            with patch.object(seals,'require_unsealed',return_value=None),patch.object(facts,'verified',return_value=None):
                execute(db,context,altered,flow)
            with pytest.raises(DBAPIError,match='0158 sealed execution request cannot execute') as error:db.commit()
            assert error.value.orig.sqlstate=='23514';db.rollback()
        assert snapshot(owner)==after
    # Two closures of one full original command append exactly one seal/audit.
    double=fresh(command);barrier=Barrier(2)
    def seal_twice(_):
        with Session(api) as db:
            barrier.wait(timeout=30);reply=close(db,context,double,flow);db.commit();return reply
    with ThreadPoolExecutor(max_workers=2) as pool:
        first,second=tuple(pool.map(seal_twice,(0,1)))
    assert first==second
    latest=snapshot(owner)
    assert len(latest[Seal.__tablename__])==len(after[Seal.__tablename__])+1
    assert len(latest['inventory_transactions'])==len(after['inventory_transactions'])
    # Current authority must still hold when PostgreSQL commits the seal.
    with Session(owner) as db:
        assignment=db.scalars(select(RoleAssignment).join(Role).where(
            RoleAssignment.user_id==context['admin_id'],Role.code=='admin')).one()
        identifier,old_end=assignment.id,assignment.valid_to
        deadline=db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=12)
        assignment.valid_to=deadline;db.commit()
    try:
        with Session(api) as db:
            close(db,context,fresh(command),flow)
            assert db.scalar(text('SELECT clock_timestamp()'))<deadline
            time.sleep(max(0,(deadline-db.scalar(text('SELECT clock_timestamp()'))).total_seconds())+.05)
            with pytest.raises(DBAPIError,match='0150 current headquarters disposition authority required') as error:db.commit()
            assert error.value.orig.sqlstate=='23514';db.rollback()
        assert snapshot(owner)==latest
    finally:
        with Session(owner) as db:db.get(RoleAssignment,identifier).valid_to=old_end;db.commit()
    # Execution and closure compete with actual connections and actual COMMIT.
    racing=fresh(command);barrier=Barrier(2)
    def compete(mode):
        with Session(api) as db:
            barrier.wait(timeout=30)
            if mode=='seal':
                answer=close(db,context,racing,flow);db.commit();return answer['lookup_status'],answer
            try:
                answer=execute(db,context,racing,flow);db.commit();return 'executed',answer
            except InventoryReadError as error:
                assert error.code=='stock_loss_disposition_request_sealed';db.rollback();return 'sealed_rejected',None
    with ThreadPoolExecutor(max_workers=2) as pool:
        sealed_result,posted_result=tuple(pool.map(compete,('seal','execute')))
    assert (sealed_result[0],posted_result[0]) in (('sealed','sealed_rejected'),('found','executed'))
    after_race=snapshot(owner)
    counts=(len(after_race[Seal.__tablename__])-len(latest[Seal.__tablename__]),
        len(after_race['inventory_transactions'])-len(latest['inventory_transactions']))
    assert counts==((1,0) if sealed_result[0]=='sealed' else (0,1))
    if posted_result[0]=='executed':actual,posted=racing,posted_result[1]
    else:
        # Explicit synthetic follow-up only: both new coordinates, same approved
        # line. Preserve the old seal and prove it never becomes a replay key.
        actual=fresh(command)
        with Session(api) as db:posted=execute(db,context,actual,flow);db.commit()
    committed=snapshot(owner)
    with Session(api) as db:
        assert close(db,context,actual,flow)==dict(lookup_status='found',retry_permitted=False,disposition=posted)
        db.commit()
        raw_seal(db,context,actual,flow)
        with pytest.raises(DBAPIError,match='0158 executed or unknown original request cannot be sealed') as error:db.commit()
        assert error.value.orig.sqlstate=='23514';db.rollback()
    assert snapshot(owner)==committed
    with Session(api) as db:
        assert lookup(db,context,closed,flow)==sealed
        assert lookup(db,context,actual,flow)['disposition']==posted
    return actual,posted,dict(passed=True,flow=flow,tracking=context['tracking'],
        positiveAllConstraints=True,malformedSealCommitRollbacks=len(defects),
        lateExecutionVariantsRejectedAtCommit=3,concurrentClosureUnique=True,
        commitTimeAuthorityExpiry=True,concurrentExecuteSealSingleWinner=True,
        executedRequestRawSealRejected=True,originalSealSurvivesLaterExecution=True,
        productionAcceptance=False)
