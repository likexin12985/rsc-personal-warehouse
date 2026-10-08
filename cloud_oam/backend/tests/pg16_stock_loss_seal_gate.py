"""Real API-role loss seals, commit proofs and observed ledger-lock races."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime,timedelta,timezone
from threading import Event
import time
from unittest.mock import patch
from uuid import uuid4

import pytest
from sqlalchemy import select,text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.formal_access import load_formal_principal
from app.foundation_models import RoleAssignment
from app.stock_operation_models import StockLossRequestSeal as Seal
from app.stock_loss_schemas import StockLossSealIn
from app.formal_services import stock_loss_seals as seals,stock_loss_commands as commands,stock_loss_recovery as recovery
from app.formal_services import inventory_posting as posting,stock_loss_sources as sources,stock_loss_plan as plan
from app.formal_services.audit_chain import append_audit_event
from app.formal_services.inventory_query import InventoryReadError


def request(context,value):
    return StockLossSealIn(operator_person_id=context['person_id'],source_location_id=context['location_id'],
        request_id=value.request_id,idempotency_key=value.idempotency_key,
        request_hash=sources._hash(plan.intent(value)),expected_plan_hash=value.expected_plan_hash)


def all_facts(engine):
    from pg16_stock_loss_submit_gate import snapshot
    with engine.connect() as db:
        rows={table:tuple(sorted(repr(dict(row)) for row in db.execute(text('SELECT * FROM '+table)).mappings()))
            for table in ('stock_loss_request_seals','shipments','receipts')}
    return snapshot(engine),rows


def stock_facts(engine):
    from pg16_stock_loss_submit_gate import snapshot
    return {key:rows for key,rows in snapshot(engine).items() if key not in ('audit_events','audit_chain_heads')}


def raw_seal(db,actor,value,*,audit=True,changes=None):
    row=Seal(id=uuid4(),actor_user_id=actor.user_id,operator_person_id=actor.person_id,
        source_location_id=value.source_location_id,authorization_version=actor.authorization_version,
        request_id=value.request_id,request_reference=posting._request_reference(value.request_id),
        idempotency_key_hash=posting._storage_hash(value.idempotency_key),request_hash=value.request_hash,
        plan_hash=value.expected_plan_hash,created_at=datetime.now(timezone.utc))
    for key,new in (changes or {}).items():setattr(row,key,new)
    db.add(row);db.flush()
    if audit:
        append_audit_event(db,stream_key='inventory',actor_user_id=row.actor_user_id,action=seals.KIND,
            aggregate_type=seals.AGGREGATE,aggregate_id=str(row.id),before_jsonb={},after_jsonb=seals.payload(row),
            request_id='stock-loss-seal:'+str(row.id),occurred_at=row.created_at,created_at=row.created_at)
        db.flush()
    return row


def wait_blocked(owner,blocker,ready,pids,future):
    assert ready.wait(10),'loss waiter did not publish its backend pid'
    limit=time.monotonic()+15
    with owner.connect() as observer:
        while time.monotonic()<limit:
            if blocker in observer.scalar(text('SELECT pg_blocking_pids(:pid)'),dict(pid=pids['waiter'])):return
            if future.done():break
            time.sleep(.02)
    raise AssertionError('loss waiter did not block on the exact ledger holder')


def waiter(api,context,value,kind,ready,pids):
    with Session(api) as db:
        db.execute(text("SET LOCAL statement_timeout = '30s'"))
        pids['waiter']=db.scalar(text('SELECT pg_backend_pid()'))
        actor=load_formal_principal(db,context['engineer_id']);ready.set()
        try:
            result=(commands.submit_loss(db,actor=actor,request=value) if kind=='submit' else
                seals.seal_loss_request(db,actor=actor,request=request(context,value)))
            db.commit();return result
        except InventoryReadError as error:
            db.rollback();return error.code


def seal_first_race(context,value,kind):
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    before=stock_facts(owner);ready=Event();pids={}
    with ThreadPoolExecutor(max_workers=1) as pool:
        with Session(api) as first:
            first.execute(text("SET LOCAL statement_timeout = '30s'"))
            leader=seals.seal_loss_request(first,actor=load_formal_principal(first,context['engineer_id']),request=request(context,value))
            pid=first.scalar(text('SELECT pg_backend_pid()'))
            future=pool.submit(waiter,api,context,value,kind,ready,pids)
            try:
                wait_blocked(owner,pid,ready,pids,future)
                first.commit()
            finally:first.rollback()
        trailing=future.result(timeout=40)
    assert trailing==('stock_loss_request_sealed' if kind=='submit' else leader)
    assert stock_facts(owner)==before
    return leader


def commit_before_waiting_seal(writer,context,value):
    """Commit the pending real loss while its seal waits on the exact lock."""
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    ready=Event();pids={};pid=writer.scalar(text('SELECT pg_backend_pid()'))
    with ThreadPoolExecutor(max_workers=1) as pool:
        future=pool.submit(waiter,api,context,value,'seal',ready,pids)
        try:
            wait_blocked(owner,pid,ready,pids,future)
            writer.commit()
        finally:writer.rollback()
        result=future.result(timeout=40)
    assert result.lookup_status=='found' and result.submission.request_id==value.request_id
    with Session(api) as db:
        assert db.scalar(select(Seal.id).where(Seal.request_id==value.request_id)) is None


def prepare_generic_receipt_parent(context):
    """Establish a real open request/parcel before taking negative snapshots.

    The 0169 immediate root guard must run for receipt key probes too. Reuse
    the actual reviewed opening and fulfillment lifecycle, never insert fake
    outbound parents or disable its constraints.
    """
    from pathlib import Path
    from uuid import UUID
    from app.inventory_models import ShipmentLine, OutboundPosting
    from pg16_material_request_http_gate import run as fulfill

    directory=Path(__file__).resolve().parents[2]/'artifacts'/'loss-key-parent'/uuid4().hex
    directory.mkdir(parents=True)
    result=fulfill(context['engines'],requester=context['engineer_id'],
        manager=context['manager_id'],admin=context['admin_id'],verifier=context['reviewer_id'],
        directory=directory,tracking='quantity',existing_target_location_id=context['location_id'])
    assert result['passed'] and not result['businessClosed']
    with Session(context['engines']['star_oam_api']) as db:
        context['generic_receipt_shipment_id']=db.scalars(select(ShipmentLine.shipment_id)
            .join(OutboundPosting,OutboundPosting.id==ShipmentLine.outbound_posting_id)
            .where(OutboundPosting.request_id==UUID(result['requestId'])).distinct()).one()


def generic_probe(db,context,value,kind):
    """Build an intentionally incomplete key-fence probe, never commit it."""
    from app.inventory_models import Shipment,Receipt,StockLocation
    actor=load_formal_principal(db,context['engineer_id']);at=datetime.now(timezone.utc)
    target=db.scalar(select(StockLocation.id).where(StockLocation.id!=context['location_id']).limit(1))
    key=posting._storage_hash(value.idempotency_key)
    if kind=='receipts':
        db.add(Receipt(id=uuid4(),receipt_no='SYNTHETIC-KEY-'+uuid4().hex,
            shipment_id=context['generic_receipt_shipment_id'],
            status='accepted',received_at=at,receiver_person_id=context['person_id'],
            request_hash='b'*64,idempotency_key_hash=key,created_at=at));db.flush()
        return
    shipment=Shipment(id=uuid4(),shipment_no='SYNTHETIC-KEY-'+uuid4().hex,
        source_location_id=context['location_id'],target_location_id=target,target_person_id=context['person_id'],
        carrier='synthetic',tracking_no=uuid4().hex,status='shipped',shipped_at=at,
        idempotency_key_hash=key if kind=='shipments' else posting._storage_hash(uuid4().hex),
        request_hash='a'*64,actor_user_id=actor.user_id,actor_person_id=actor.person_id,
        authorization_version=actor.authorization_version,created_at=at)
    db.add(shipment);db.flush()


def generic_key_checks(context,value):
    """Specific key guard on raw pending carrier/acceptance probe rows.

    These are not complete shipment/receipt business commands. Explicitly fire
    only the new deferred key constraint, then roll back every probe row.
    """
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    before=all_facts(owner)
    for kind in ('shipments','receipts'):
        with Session(api) as db:
            generic_probe(db,context,value,kind)
            with pytest.raises(DBAPIError,match='0146 sealed loss key cannot execute'):
                db.execute(text('SET CONSTRAINTS trg_'+kind+'_loss_seal_0146 IMMEDIATE'))
            db.rollback()
        assert all_facts(owner)==before


def generic_key_races(context,fresh):
    """Observe exact advisory blockers and prove unrelated probes stay free."""
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))

    def probe(value,kind,ready,pids):
        with Session(api) as db:
            db.execute(text("SET LOCAL statement_timeout = '15s'"))
            pids['waiter']=db.scalar(text('SELECT pg_backend_pid()'))
            generic_probe(db,context,value,kind)
            ready.set()
            try:
                db.execute(text('SET CONSTRAINTS trg_'+kind+'_loss_seal_0146 IMMEDIATE'))
                return 'allowed'
            except DBAPIError as error:
                assert error.orig.sqlstate=='23514'
                assert '0146 sealed loss key cannot execute' in str(error.orig)
                return 'sealed'
            finally:db.rollback()

    for kind in ('shipments','receipts'):
        before=all_facts(owner)
        with ThreadPoolExecutor(max_workers=1) as pool,Session(api) as leader:
            posting._lock_inventory_ledger_head_for_atomic_batch(leader)
            try:
                # Must finish while the ledger is still held, not after release.
                future=pool.submit(probe,fresh(),kind,Event(),{})
                assert future.result(timeout=10)=='allowed'
            finally:leader.rollback()
        assert all_facts(owner)==before

        value=fresh();before=stock_facts(owner);ready=Event();pids={}
        with ThreadPoolExecutor(max_workers=1) as pool,Session(api) as leader:
            seals.seal_loss_request(leader,actor=load_formal_principal(leader,context['engineer_id']),
                request=request(context,value))
            # Acquire the seal's exact key lock before dispatching the waiter.
            leader.execute(text('SET CONSTRAINTS trg_stock_loss_request_seals_loss_seal_0146 IMMEDIATE'))
            pid=leader.scalar(text('SELECT pg_backend_pid()'))
            future=pool.submit(probe,value,kind,ready,pids)
            try:
                wait_blocked(owner,pid,ready,pids,future)
                with owner.connect() as observer:
                    assert observer.scalar(text("SELECT count(*) FROM pg_locks WHERE pid=:pid "
                        "AND locktype='advisory' AND NOT granted"),dict(pid=pids['waiter']))==1
                leader.commit()
            finally:leader.rollback()
            assert future.result(timeout=20)=='sealed'
        assert stock_facts(owner)==before
        with Session(api) as db:
            assert db.scalar(select(Seal.id).where(Seal.request_id==value.request_id)) is not None


def run(context,original):
    owner,api=(context['engines'][k] for k in ('star_oam_migrator','star_oam_api'))
    def fresh():return original.model_copy(update={'request_id':uuid4().hex,'idempotency_key':uuid4().hex})
    # Raw SQL/ORM writes bypass application authority and reconstruction.
    # PostgreSQL must reject each incomplete or altered fact at COMMIT.
    for fault in ('missing_audit','reference','operator','location'):
        before=all_facts(owner);value=request(context,fresh())
        with Session(api) as db:
            actor=load_formal_principal(db,context['engineer_id'])
            if fault=='operator':
                from app.foundation_models import Person
                changed={'operator_person_id':db.scalar(select(Person.id).where(Person.id!=actor.person_id).limit(1))}
            elif fault=='location':
                from app.inventory_models import StockLocation
                changed={'source_location_id':db.scalar(select(StockLocation.id).where(StockLocation.id!=value.source_location_id).limit(1))}
            else:changed={'request_reference':'wrong-reference'} if fault=='reference' else {}
            raw_seal(db,actor,value,audit=fault!='missing_audit',changes=changed)
            with pytest.raises(DBAPIError) as error:db.commit()
            assert error.value.orig.sqlstate=='23514'
            assert ('0145' if fault in ('operator','location') else '0146') in str(error.value.orig)
            db.rollback()
        assert all_facts(owner)==before
    # Authority is valid through INSERT, but genuinely expires before COMMIT.
    with Session(owner) as db:
        assignment=db.scalar(select(RoleAssignment).where(RoleAssignment.user_id==context['engineer_id']))
        assignment_id=assignment.id;old_end=assignment.valid_to
        deadline=db.scalar(text('SELECT clock_timestamp()'))+timedelta(seconds=6)
        assignment.valid_to=deadline;db.commit()
    try:
        before=all_facts(owner)
        with Session(api) as db:
            seals.seal_loss_request(db,actor=load_formal_principal(db,context['engineer_id']),request=request(context,fresh()))
            wall=db.scalar(text('SELECT clock_timestamp()'))
            assert wall<deadline,'loss seal expired before INSERT'
            time.sleep(max(0,(deadline-wall).total_seconds())+.05)
            assert db.scalar(text('SELECT clock_timestamp()'))>deadline
            with pytest.raises(DBAPIError,match='0145 current submit_loss authority required'):db.commit()
            db.rollback()
        assert all_facts(owner)==before
    finally:
        with Session(owner) as db:db.get(RoleAssignment,assignment_id).valid_to=old_end;db.commit()
    # Seal wins against a late application submission and against another seal.
    value=fresh();sealed=seal_first_race(context,value,'submit')
    seal_first_race(context,fresh(),'seal')
    generic_key_checks(context,value)
    generic_key_races(context,fresh)
    before=all_facts(owner)
    with Session(api) as db:
        # Only omit the app namespace test. This still builds the real complete
        # freeze with original attachments/audit/notification, then commits.
        with patch.object(commands,'_fresh_request',return_value=None):
            commands.submit_loss(db,actor=load_formal_principal(db,context['engineer_id']),request=value)
        with pytest.raises(DBAPIError,match='0146 sealed loss request cannot execute'):db.commit()
        db.rollback()
    assert all_facts(owner)==before
    for fragment in ('posting_audit','domain_audit','return_seal_audit','posting_state'):
        with Session(api) as db:
            at=datetime.now(timezone.utc)
            if fragment=='posting_state':
                from app.foundation_models import StateTransitionEvent
                db.add(StateTransitionEvent(aggregate_type='inventory_transaction',aggregate_id=str(uuid4()),
                    from_status=None,to_status='posted',actor_id=context['engineer_id'],reason='synthetic late proof',
                    idempotency_key=uuid4().hex,occurred_at=at,created_at=at,
                    metadata_jsonb={'request_reference':posting._request_reference(value.request_id)}));db.flush()
                constraint='trg_state_transition_events_loss_seal_0146'
            else:
                append_audit_event(db,stream_key='material_request' if fragment=='return_seal_audit' else 'inventory',
                    actor_user_id=context['engineer_id'],action='synthetic_late_loss_proof',
                    aggregate_type={'posting_audit':'inventory_transaction','domain_audit':'stock_operation_order',
                        'return_seal_audit':'stock_operation_command_seal'}[fragment],aggregate_id=str(uuid4()),
                    before_jsonb={},after_jsonb={'request_id':value.request_id},occurred_at=at,
                    request_id=posting._request_reference(value.request_id) if fragment=='posting_audit' else
                        'synthetic-return-seal:'+uuid4().hex if fragment=='return_seal_audit' else value.request_id)
                db.flush();constraint='trg_audit_events_loss_seal_0146'
            # Isolate the specific late-fragment guard before other immutable
            # posting proofs reject these deliberately orphaned probe rows.
            with pytest.raises(DBAPIError,match='0146 sealed loss request cannot execute'):
                db.execute(text('SET CONSTRAINTS '+constraint+' IMMEDIATE'))
            db.rollback()
        assert all_facts(owner)==before
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        lookup=request(context,value).model_dump(exclude={'source_location_id'})
        from app.stock_loss_schemas import StockLossRequestLookupIn
        found=recovery.lookup_loss_request(db,actor=load_formal_principal(db,context['engineer_id']),
            request=StockLossRequestLookupIn(**lookup))
        assert found==sealed;db.commit()
    assert all_facts(owner)==before
    for sql in ('UPDATE stock_loss_request_seals SET request_hash=request_hash',
                'DELETE FROM stock_loss_request_seals','TRUNCATE stock_loss_request_seals'):
        with Session(api) as db:
            with pytest.raises(DBAPIError):db.execute(text(sql));db.commit()
            db.rollback()
        assert all_facts(owner)==before
    print('PG16 loss seals: raw proof/expiry rollback, exact lock races, late complete command/fragments and immutable ACL PASS',flush=True)
    return dict(rawCommitRejections=4,commitExpiryRollback=True,sealFirstRejectsSubmission=True,
        concurrentSealsSingleResult=True,observedExactLedgerBlocker=True,
        completeLateCommandCommitRejected=True,lateFragmentsRejected=4,
        genericReceiptAndShipmentKeyProbesRejected=True,
        genericUnrelatedKeysAvoidLedgerWait=True,genericExactAdvisoryBlockersObserved=True,
        readOnlySealLookup=True,immutableRuntimeAcl=True,noStockOrNotificationsFromSeals=True)
