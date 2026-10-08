"""Real API-role approval seals, late complete commands and immutable SQL ACL."""
from concurrent.futures import ThreadPoolExecutor
import threading
from datetime import timedelta
import time
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import RoleAssignment
from pg16_stock_operation_permission_policy import require_formal_grant
from app.stock_operation_models import StockLossReviewRequestSeal as Seal
from app.stock_loss_schemas import StockLossReviewRequestLookupIn
from app.stock_loss_review_seal_schemas import StockLossRegionalReviewSealIn, StockLossHeadquartersReviewSealIn
from app.formal_services import stock_loss_review_seals as seals, stock_loss_review_recovery as recovery
from app.formal_services import stock_loss_sources as sources
from pg16_stock_loss_submit_gate import snapshot


def run(engines, *, stage, reviewer_id, assignment_id, command, service):
    owner, api = (engines[k] for k in ('star_oam_migrator','star_oam_api'))
    with Session(owner) as db:
        require_formal_grant(db,role_code=('provincial_manager' if stage=='regional' else 'admin'),action='read')
        db.commit()
    with Session(api) as db:actor=load_formal_principal(db,reviewer_id)
    schema = StockLossRegionalReviewSealIn if stage=='regional' else StockLossHeadquartersReviewSealIn
    original = command.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    value = schema(operator_person_id=actor.person_id,original=original,request_hash=sources._hash(service.intent(original)))
    lookup = StockLossReviewRequestLookupIn(operation_id=original.operation_id,operator_person_id=actor.person_id,
        expected_submission_plan_hash=original.expected_submission_plan_hash,request_id=original.request_id,
        idempotency_key=original.idempotency_key,request_hash=value.request_hash)
    def create(db):return seals.seal_review_request(db,actor=load_formal_principal(db,reviewer_id),request=value,stage=stage)
    def state():
        result=snapshot(owner)
        with owner.connect() as db:
            result['seals']=tuple(db.execute(text('SELECT * FROM stock_loss_review_request_seals ORDER BY id')))
            for table in ('stock_loss_regional_reviews','stock_loss_headquarters_reviews','stock_loss_headquarters_decisions'):
                result[table]=tuple(db.execute(text('SELECT * FROM '+table+' ORDER BY id')))
        return result
    for fault in ('audit','hash','intent','version'):
        before=state()
        with Session(api) as db:
            def mutate(session,*_):
                for row in tuple(session.new):
                    if isinstance(row,Seal):
                        if fault=='hash':row.request_hash='f'*64
                        elif fault=='intent':row.command_intent_jsonb=dict(row.command_intent_jsonb,comment='changed after request hash')
                        elif fault=='version':row.authorization_version+=1
            event.listen(db,'before_flush',mutate)
            with patch.object(seals,'verified',return_value=None):
                if fault=='audit':
                    with patch.object(seals,'append_audit_event',return_value=None):create(db)
                else:create(db)
            with pytest.raises(DBAPIError) as caught:db.commit()
            assert caught.value.orig.sqlstate=='23514';db.rollback()
        assert state()==before
    from pg16_stock_loss_review_seal_adversarial_gate import raw_seal_refusals, raw_fragment_refusals
    raw_faults=raw_seal_refusals(engines,stage=stage,reviewer_id=reviewer_id,assignment_id=assignment_id,
        command=original,service=service,state=state)
    with Session(owner) as db:
        assignment=db.get(RoleAssignment,assignment_id);old_end=assignment.valid_to
        deadline=db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=12)
        assignment.valid_to=deadline;db.commit()
    try:
        before=state()
        with Session(api) as db:
            create(db)
            assert db.scalar(text('SELECT clock_timestamp()'))<deadline
            time.sleep(max(0,(deadline-db.scalar(text('SELECT clock_timestamp()'))).total_seconds())+.05)
            with pytest.raises(DBAPIError) as caught:db.commit()
            assert caught.value.orig.sqlstate=='23514';db.rollback()
        assert state()==before
    finally:
        with Session(owner) as db:db.get(RoleAssignment,assignment_id).valid_to=old_end;db.commit()
    # A separate reader sees neither uncommitted seal nor rolled-back audit.
    before=state()
    with Session(api) as pending:
        create(pending)
        with Session(api) as reader:
            reader.execute(text('SET TRANSACTION READ ONLY'))
            assert recovery.lookup_review_request(reader,actor=load_formal_principal(reader,reviewer_id),
                request=lookup,stage=stage).model_dump()=={'lookup_status':'not_found','retry_permitted':False}
        pending.rollback()
    assert state()==before
    with Session(api) as reader:
        reader.execute(text('SET TRANSACTION READ ONLY'))
        assert recovery.lookup_review_request(reader,actor=load_formal_principal(reader,reviewer_id),
            request=lookup,stage=stage).lookup_status=='not_found'
    # Observe the exact ledger blocker before committing one winner. The
    # second seal must return its durable original, not append another audit.
    started=threading.Event();waiter_pid=[]
    def concurrent_seal():
        with Session(api) as db:
            waiter_pid.append(db.scalar(text('SELECT pg_backend_pid()')));started.set()
            answer=create(db);db.commit();return answer
    late_ready=threading.Event();late_pids=[]
    def concurrent_late_approval():
        approve=service.verify_regional_loss if stage=='regional' else service.approve_headquarters_loss
        with Session(api) as db,patch.object(seals,'require_unsealed',return_value=None):
            late_pids.append(db.scalar(text('SELECT pg_backend_pid()')));late_ready.set()
            approve(db,actor=load_formal_principal(db,reviewer_id),request=original)
            with pytest.raises(DBAPIError,match='0156 sealed approval request') as caught:db.commit()
            assert caught.value.orig.sqlstate=='23514';db.rollback()
        return True
    with Session(api) as first:
        first_pid=first.scalar(text('SELECT pg_backend_pid()'))
        result=create(first)
        with ThreadPoolExecutor(max_workers=2) as pool:
            future=pool.submit(concurrent_seal)
            late=pool.submit(concurrent_late_approval)
            try:
                assert started.wait(10)
                wait_for_blocker(owner,waiter_pid[0],first_pid)
                assert late_ready.wait(10)
                wait_for_blocker(owner,late_pids[0],first_pid)
                first.commit()
            finally:first.rollback()
            assert future.result(timeout=30)==result
            assert late.result(timeout=30)
    after=state()
    for table in before:
        if table not in ('audit_events','audit_chain_heads','seals'):assert before[table]==after[table],table
    with Session(api) as db:assert create(db)==result;db.commit()
    assert state()==after
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        assert recovery.lookup_review_request(db,actor=load_formal_principal(db,reviewer_id),request=lookup,stage=stage)==result
    approve=service.verify_regional_loss if stage=='regional' else service.approve_headquarters_loss
    # Bypass only Python's tombstone lookup: the entire approval must still
    # reach PostgreSQL COMMIT and be rejected with all facts rolled back.
    for changed in ({},{'request_id':uuid4().hex},{'idempotency_key':uuid4().hex}):
        with Session(api) as db,patch.object(seals,'require_unsealed',return_value=None):
            approve(db,actor=load_formal_principal(db,reviewer_id),request=original.model_copy(update=changed))
            with pytest.raises(DBAPIError,match='0156 sealed approval request') as caught:db.commit()
            assert caught.value.orig.sqlstate=='23514';db.rollback()
        assert state()==after
    fragments=raw_fragment_refusals(engines,stage=stage,actor=actor,command=original,state=state)
    for sql in ('UPDATE stock_loss_review_request_seals SET stage=stage','DELETE FROM stock_loss_review_request_seals','TRUNCATE stock_loss_review_request_seals'):
        with api.connect() as db:
            with pytest.raises(DBAPIError):db.execute(text(sql))
            db.rollback()
    assert state()==after
    print('PG16 '+stage+' seal: original recovery, raw intent/authority refusals, fragment fences, COMMIT expiry, exact lock races and immutable API ACL PASS',flush=True)
    return dict(passed=True,rawCommitRollbacks=4+len(raw_faults),rawAuthorityAndIntentRefusals=raw_faults,
        rawFragmentImmediateFenceRefusals=fragments,commitExpiryRollback=True,lateCompleteCommandsRejected=3,
        immutableApiAcl=True,originalReadOnlyRecovery=True,stockAndNotificationsUnchanged=True,
        pendingAndRollbackReadOnly=True,concurrentSealsSingleResult=True,exactSealBlockerObserved=True,sealFirstLateApprovalCommitRejected=True)



def wait_for_blocker(owner, waiter_pid, blocker_pid):
    # Multiple row-lock waiters can queue behind each other. Require a real
    # blocker path to the exact writer, not merely any blocked connection.
    deadline=time.monotonic()+15
    paths=[]
    while time.monotonic()<deadline:
        with owner.connect() as db:
            paths=db.execute(text('''
                WITH RECURSIVE wait_chain(pid, path) AS (
                    SELECT CAST(:waiter AS integer), ARRAY[CAST(:waiter AS integer)]
                    UNION ALL
                    SELECT next_pid, path || next_pid FROM wait_chain
                    CROSS JOIN LATERAL unnest(pg_blocking_pids(pid)) AS next_pid
                    WHERE NOT next_pid = ANY(path)
                ) SELECT path FROM wait_chain WHERE pid = :blocker
                  ORDER BY cardinality(path)
            '''),{'waiter':waiter_pid,'blocker':blocker_pid}).scalars().all()
        if paths:
            assert paths[0][0]==waiter_pid and paths[0][-1]==blocker_pid and len(paths[0])>=2
            print('PG16 exact approval lock path: '+str(paths[0]),flush=True)
            return paths[0]
        time.sleep(.05)
    raise AssertionError('approval seal contender did not wait through a lock path to the exact writer')


def seal_competitor(api, *, stage, reviewer_id, command, service, ready, pids):
    schema=StockLossRegionalReviewSealIn if stage=='regional' else StockLossHeadquartersReviewSealIn
    with Session(api) as db:
        pids.append(db.scalar(text('SELECT pg_backend_pid()')));ready.set()
        actor=load_formal_principal(db,reviewer_id)
        value=schema(operator_person_id=actor.person_id,original=command,
            request_hash=sources._hash(service.intent(command)))
        result=seals.seal_review_request(db,actor=actor,request=value,stage=stage)
        db.commit();return result
