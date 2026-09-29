"""Real API-role headquarters approvals, exact decisions, expiry and concurrency."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
import threading
import time
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import event, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.formal_services import stock_loss_headquarters_reviews as reviews, stock_loss_facts as facts
from app.formal_services import stock_loss_sources as sources, inventory_posting as posting
from app.foundation_models import Organization, Permission, Role, RolePermission, RoleAssignment, OutboxEvent, StateTransitionEvent
from app.inventory_models import StockLocation
from app.models import User
from app.stock_operation_models import StockOperationOrder as Order, StockLossHeadquartersReview as Review, StockLossHeadquartersDecision as Decision, StockLossRegionalReview as Regional, StockOperationLine as Line
from app.stock_loss_schemas import StockLossHeadquartersReviewIn
from pg16_stock_loss_submit_gate import snapshot
from test_formal_access import make_user, assign


def run(engines):
    owner,api=(engines[key] for key in ('star_oam_migrator','star_oam_api'))
    with Session(owner) as db:
        orders=tuple(db.scalars(select(Order).where(Order.operation_type=='loss_report').order_by(Order.id)))
        assert len(orders)==2
        order_ids=tuple(row.id for row in orders)
        owner_id=db.get(StockLocation,orders[0].source_location_id).owner_org_id
        requester_id=orders[0].actor_user_id
        user,person=make_user(db,db.scalar(select(Organization).where(Organization.org_type=='headquarters')),name='Synthetic headquarters loss reviewer')
        role=db.scalar(select(Role).where(Role.code=='admin'))
        assignment=assign(db,user,role,scope_type='national',scope_id='*')
        permission=Permission(resource='stock_operation',action=reviews.ACTION,field_code='',description='Synthetic review only')
        db.add(permission);db.flush()
        grant=RolePermission(role_id=role.id,permission_id=permission.id,effect='allow')
        db.add(grant);db.commit()
        reviewer_id,person_id,assignment_id,grant_id=user.id,person.id,assignment.id,grant.id
        regional_actor_id=db.scalar(select(Regional.actor_user_id).where(Regional.operation_id==order_ids[0]))
    def state():
        values=snapshot(owner)
        with owner.connect() as db:
            for table in ('stock_loss_regional_reviews','stock_loss_headquarters_reviews','stock_loss_headquarters_decisions'):
                values[table]=tuple(db.execute(text('SELECT * FROM '+table+' ORDER BY id')))
        return values
    def request(order_id=order_ids[0],kind='restore_available'):
        with Session(api) as db:
            plan_hash=db.get(Order,order_id).plan_hash
            regional=db.scalar(select(Regional).where(Regional.operation_id==order_id))
            regional_id,regional_hash=regional.id,regional.request_hash
            lines=tuple(db.scalars(select(Line.id).where(Line.operation_id==order_id)))
        return StockLossHeadquartersReviewIn(operation_id=order_id,expected_submission_plan_hash=plan_hash,
            regional_review_id=regional_id,expected_regional_review_hash=regional_hash,
            decisions=tuple(dict(line_id=line,disposition=kind,reason='Synthetic line decision') for line in lines),
            comment='Synthetic headquarters approval; disposition separately',request_id=uuid4().hex,idempotency_key=uuid4().hex)
    def command(db,value):
        return reviews.approve_headquarters_loss(db,actor=load_formal_principal(db,reviewer_id),request=value)
    def refused(db,match):
        with pytest.raises(DBAPIError,match=match) as caught:db.commit()
        assert caught.value.orig.sqlstate=='23514';db.rollback()

    for kind in ('restore_available','convert_used','convert_damaged','return_to_region','scrap'):
        before=state()
        with Session(api) as db:
            result=command(db,request(kind=kind))
            db.execute(text('SET CONSTRAINTS ALL IMMEDIATE'))
            assert result.stock_effect=='none' and result.disposition_stage=='pending'
            db.rollback()
        assert state()==before

    for omission in ('audit','state','outbox','notification'):
        before=state()
        with Session(api) as db:
            def remove(session,*_):
                for row in tuple(session.new):
                    if (omission=='state' and isinstance(row,StateTransitionEvent) and row.aggregate_type==reviews.AGGREGATE
                        or omission=='outbox' and isinstance(row,OutboxEvent) and row.aggregate_type==reviews.AGGREGATE):
                        session.expunge(row)
            event.listen(db,'before_flush',remove)
            with patch.object(reviews,'verified',return_value=None):
                if omission in ('audit','notification'):
                    name='append_audit_event' if omission=='audit' else 'record_business_notification'
                    with patch.object(reviews,name,return_value=None):command(db,request())
                else:command(db,request())
            refused(db,'0148 (complete|independent) headquarters review')
        assert state()==before

    # Raw SQL callers cannot replace current regional authority with a Python
    # preflight. Complete audit/notification facts still fail at COMMIT.
    for mutation in ('self','scope','version','hash','deny','inactive','regional_parent','regional_hash','cross_line','missing_line','reason','line_time'):
        if mutation=='deny':
            with Session(owner) as db:db.get(RolePermission,grant_id).effect='deny';db.commit()
        if mutation=='inactive':
            with Session(owner) as db:db.get(User,reviewer_id).is_active=False;db.commit()
        before=state();value=request()
        try:
            with Session(api) as db:
                parent=db.get(Order,value.operation_id)
                row=Review(id=uuid4(),operation_id=parent.id,operation_type='loss_report',owner_org_id=owner_id,
                    actor_user_id=reviewer_id,reviewer_person_id=person_id,
                    authorization_version=db.get(User,reviewer_id).authorization_version,
                    decision='approved',comment=value.comment,regional_review_id=value.regional_review_id,
                    regional_review_hash=value.expected_regional_review_hash,request_id=value.request_id,
                    idempotency_key_hash=posting._storage_hash('stock-loss-headquarters-review:'+value.idempotency_key),
                    request_hash=sources._hash(reviews.intent(value)),submission_plan_hash=parent.plan_hash,
                    created_at=datetime.now(timezone.utc))
                if mutation=='self':row.actor_user_id=parent.actor_user_id;row.reviewer_person_id=parent.requester_id
                if mutation=='scope':row.owner_org_id=db.scalar(select(Organization.id).where(Organization.org_type=='headquarters').limit(1))
                if mutation=='version':row.authorization_version+=1
                if mutation=='hash':row.request_hash='f'*64
                if mutation=='regional_parent':
                    other=db.scalar(select(Regional).where(Regional.operation_id==order_ids[1]))
                    row.regional_review_id=other.id;row.regional_review_hash=other.request_hash
                if mutation=='regional_hash':row.regional_review_hash='f'*64
                db.add(row);db.flush()
                decisions=[item.model_dump(mode='json') for item in value.decisions]
                for item in value.decisions:
                    if mutation=='missing_line':continue
                    line_id=item.line_id
                    if mutation=='cross_line':line_id=db.scalar(select(Line.id).where(Line.operation_id==order_ids[1]))
                    db.add(Decision(id=uuid4(),review_id=row.id,line_id=line_id,disposition=item.disposition,
                        reason=' ' if mutation=='reason' else item.reason,
                        created_at=row.created_at+timedelta(seconds=1) if mutation=='line_time' else row.created_at))
                db.flush()
                with patch.object(reviews,'decision_evidence',return_value=decisions):reviews._record(db,row=row,order=parent)
                reason={'self':'exact independent','scope':'exact independent','version':'current headquarters reviewer identity',
                    'hash':'headquarters review request hash','deny':'current headquarters review authority',
                    'inactive':'current headquarters reviewer identity','regional_parent':'exact independent',
                    'regional_hash':'exact independent','cross_line':'exact complete headquarters line decisions',
                    'missing_line':'exact complete headquarters line decisions','reason':'exact complete headquarters line decisions',
                    'line_time':'exact complete headquarters line decisions'}[mutation]
                refused(db,'0148 '+reason)
            assert state()==before
        finally:
            if mutation=='deny':
                with Session(owner) as db:db.get(RolePermission,grant_id).effect='allow';db.commit()
            if mutation=='inactive':
                with Session(owner) as db:db.get(User,reviewer_id).is_active=True;db.commit()

    with Session(owner) as db:
        assignment=db.get(RoleAssignment,assignment_id);old_end=assignment.valid_to
        deadline=db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=12)
        assignment.valid_to=deadline;db.commit()
    try:
        before=state()
        with Session(api) as db:
            pending=command(db,request())
            assert pending.stock_effect=='none' and db.scalar(text('SELECT clock_timestamp()'))<deadline
            time.sleep(max(0,(deadline-db.scalar(text('SELECT clock_timestamp()'))).total_seconds())+.05)
            refused(db,'0148 current headquarters review authority required')
        assert state()==before
    finally:
        with Session(owner) as db:db.get(RoleAssignment,assignment_id).valid_to=old_end;db.commit()

    before=state();value=request();started=threading.Event();waiter_pid=[]
    def competing_review():
        with Session(api) as db:
            waiter_pid.append(db.scalar(text('SELECT pg_backend_pid()')));started.set()
            result=command(db,value);db.commit();return result
    with Session(api) as first:
        first_pid=first.scalar(text('SELECT pg_backend_pid()'))
        result=command(first,value)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending=pool.submit(competing_review)
            try:
                assert started.wait(10)
                limit=time.monotonic()+15;observed=False
                while time.monotonic()<limit:
                    with owner.connect() as db:blockers=db.scalar(text('SELECT pg_blocking_pids(:pid)'),{'pid':waiter_pid[0]})
                    if first_pid in blockers:observed=True;break
                    time.sleep(.05)
                assert observed,'regional replay did not wait for exact first transaction'
                first.commit()
            finally:
                first.rollback()
            assert pending.result(timeout=30)==result
    after=state()
    for table in ('stock_accounts','stock_balances','inventory_transactions','inventory_movements','inventory_movement_serials',
            'serial_current_positions','inventory_serials','stock_operation_orders','stock_operation_lines','stock_operation_serials',
            'stock_loss_files','inventory_ledger_heads'):
        assert after[table]==before[table],table
    assert len(after['stock_loss_headquarters_reviews'])==1
    with Session(api) as db:
        assert command(db,value)==result;db.commit()
    assert state()==after
    from pg16_stock_loss_review_recovery_gate import run as recovery_checks
    recovery=recovery_checks(engines,stage='headquarters',reviewer_id=reviewer_id,write_grant_id=grant_id,
        command=value,missing_command=request(order_ids[1]),result=result,service=reviews)
    with Session(owner) as db:
        previous_statuses={identifier:db.get(User,identifier).account_status for identifier in (requester_id,regional_actor_id)}
        for identifier in previous_statuses:db.get(User,identifier).account_status='suspended'
        db.commit()
    try:
        with Session(api) as db:
            second=command(db,request(order_ids[1],kind='scrap'));db.commit()
            assert second.approval_stage=='approved' and second.stock_effect=='none'
            for order_id in order_ids:
                original=facts.submission_evidence(db,order=db.get(Order,order_id))
                assert original.status=='submitted'
    finally:
        with Session(owner) as db:
            for identifier,status in previous_statuses.items():db.get(User,identifier).account_status=status
            db.commit()
    before=state()
    for engine in (api,owner):
        with engine.begin() as db:
            with pytest.raises(DBAPIError) as caught:
                db.execute(text("UPDATE stock_loss_headquarters_reviews SET comment='overwrite' WHERE id=:id"),{'id':result.review_id})
            assert caught.value.orig.sqlstate==('42501' if engine is api else '55000')
            assert ('permission denied for table stock_loss_headquarters_reviews' if engine is api
                else '0090 work order facts are append-only') in str(caught.value.orig)
            db.rollback()
    assert state()==before
    for engine in (api,owner):
        with engine.begin() as db:
            with pytest.raises(DBAPIError) as caught:
                db.execute(text("UPDATE stock_loss_headquarters_decisions SET disposition='scrap' WHERE review_id=:id"),{'id':result.review_id})
            assert caught.value.orig.sqlstate==('42501' if engine is api else '55000')
            assert ('permission denied for table stock_loss_headquarters_decisions' if engine is api
                else '0090 work order facts are append-only') in str(caught.value.orig)
            db.rollback()
    assert state()==before
    print('PG16 headquarters loss: independent review/replay, raw refusals, expiry, exact lock race and departed-requester handling PASS',flush=True)
    return dict(passed=True,rawCommitRollbacks=16,commitExpiryRollback=True,exactConcurrentBlockerObserved=True,
        idempotentReplay=True,stockUnchanged=True,requesterSuspensionAllowed=True,immutableFacts=True,
        approvalStage='approved',fiveDecisionKindsValidated=True,regionalReviewerSuspensionAllowed=True,disposalCompleted=False,
        requestRecovery=recovery)
