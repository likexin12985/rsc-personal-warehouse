"""Decision seal API COMMIT and independent late-writer fence on owned PG16."""
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
import json
from queue import Queue
from time import monotonic, sleep
from uuid import uuid4

from sqlalchemy import text, update
from sqlalchemy.orm import Session
from app.formal_access import load_formal_principal
from app.foundation_models import RolePermission
from app.return_condition_decision_seal_schema import NAME
from app.formal_services.stock_loss_corrections import return_condition_decision_request_seals as writer
from app.formal_services.stock_loss_corrections import return_condition_decision_seal_admission as inputs
from app.formal_services.stock_loss_corrections import return_condition_decision_sealed_recovery as recovery
from pg16_return_condition_authority_gate import owned_role_connection
from pg16_return_condition_key_gate import refused, header


def register(db, actor, command, raw_key=None):
    return db.scalar(text('SELECT public.rsc_register_condition_decision_seal(:actor,:version,:person,CAST(:command AS jsonb),:key)'),
        dict(actor=actor.user_id, version=actor.authorization_version, person=actor.person_id,
            command=json.dumps(inputs.canonical(command)), key=raw_key or command.idempotency_key))


def run(owner, api, *, directory, actor_id, command, permission_link_id):
    from pg16_return_condition_submission_gate import snapshot
    before = snapshot(owner)
    closed = command.model_copy(update={'request_id': uuid4().hex, 'idempotency_key': uuid4().hex,
        'expected_event_hash': 'a' * 64})
    # Internal audit request IDs remain reserved even though an ordinary client
    # request may carry the same prefix. A genuine API audit COMMIT must refuse
    # a fabricated namespace rather than leaving detached seal evidence.
    from app.formal_services.audit_chain import append_audit_event
    with Session(api) as db:
        at = datetime.now(timezone.utc)
        append_audit_event(db, stream_key='inventory', actor_user_id=actor_id,
            action='owned_probe', aggregate_type='owned_condition_probe', aggregate_id=str(uuid4()),
            before_jsonb={}, after_jsonb={}, request_id='condition-decision-seal:'+str(uuid4()),
            occurred_at=at, created_at=at)
        refused(db, db.commit, 'condition decision seal effect namespace required')
    for sql in ('INSERT INTO public.' + NAME + ' DEFAULT VALUES',
            'UPDATE public.' + NAME + " SET reason='changed' WHERE false",
            'DELETE FROM public.' + NAME + ' WHERE false', 'TRUNCATE public.' + NAME):
        with Session(api) as db:
            refused(db, lambda: db.execute(text(sql)), 'permission denied', code='42501')
    with Session(api) as db:
        actor = load_formal_principal(db, actor_id)
        record = register(db, actor, closed)
        assert record['created'] is True
        refused(db, db.commit, 'condition decision seal requires one audit and no business effects')
    with Session(api) as db:
        actor = load_formal_principal(db, actor_id)
        refused(db, lambda: register(db, actor, closed, uuid4().hex), 'decision seal original key and input differ')
    with Session(api) as db:
        actor = load_formal_principal(db, actor_id)
        header(db, closed)
        refused(db, lambda: register(db, actor, closed), 'condition decision sealed key collides with business evidence')
    assert snapshot(owner) == before
    # Only the disposable owner's fixture can revoke an action inside this test.
    # The real service and actual deferred COMMIT execute as API.
    with owned_role_connection(owner, directory) as connection:
        connection.rollback()
        with Session(bind=connection) as db:
            db.execute(text('SET LOCAL ROLE star_oam_api'))
            writer.seal(db, actor=load_formal_principal(db, actor_id), request=closed)
            db.execute(text('SET LOCAL ROLE star_oam_migrator'))
            db.execute(update(RolePermission).where(RolePermission.id == permission_link_id).values(effect='deny'))
            db.execute(text('SET LOCAL ROLE star_oam_api'))
            refused(db, db.commit, 'condition decision seal current read and action permission required')
    assert snapshot(owner) == before
    ready = Queue()
    def late():
        with Session(api) as db:
            db.execute(text("SET LOCAL statement_timeout='180000ms'"))
            ready.put(db.scalar(text('SELECT pg_backend_pid()')))
            header(db, closed)
            refused(db, db.commit, 'condition decision sealed key collides with business evidence')
            return True
    with Session(api) as db, ThreadPoolExecutor(max_workers=1) as executor:
        try:
            pid = db.scalar(text('SELECT pg_backend_pid()'))
            result = writer.seal(db, actor=load_formal_principal(db, actor_id), request=closed)
            assert result['request_state'] == 'sealed' and result['stock_effect'] == 'none'
            pending = executor.submit(late)
            waiting = ready.get(timeout=5)
            observed = False; deadline = monotonic() + 5
            while monotonic() < deadline:
                with api.connect() as observer:
                    observed = observer.scalar(text('SELECT :owner=ANY(pg_blocking_pids(:waiting))'),
                        dict(owner=pid, waiting=waiting))
                if observed: break
                if pending.done(): pending.result()
                sleep(.05)
            assert observed, 'late writer did not block on actual seal transaction'
            db.commit()
            assert pending.result(timeout=180)
        finally:
            db.rollback()
    after = snapshot(owner)
    assert {name for name in before if before[name] != after[name]} == {
        NAME, 'audit_events', 'audit_chain_heads'}
    assert len(after[NAME]) == len(before[NAME]) + 1
    assert len(after['audit_events']) == len(before['audit_events']) + 1
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actual = recovery.lookup(db, actor=load_formal_principal(db, actor_id), request=closed)
        assert actual == result and actual['absence_sealed'] is True and not actual['retry_allowed']
    with Session(api) as db:
        actor = load_formal_principal(db, actor_id)
        assert writer.seal(db, actor=actor, request=closed) == result
        repeated = register(db, actor, closed)
        assert repeated['created'] is False
        db.commit()
    assert snapshot(owner) == after
    with owned_role_connection(owner, directory) as connection:
        connection.rollback()
        with Session(bind=connection) as db:
            db.execute(update(RolePermission).where(RolePermission.id == permission_link_id).values(effect='deny'))
            db.execute(text('SET LOCAL ROLE star_oam_api'))
            actor = load_formal_principal(db, actor_id)
            assert recovery.lookup(db, actor=actor, request=closed)['request_state'] == 'sealed'
            assert register(db, actor, closed)['created'] is False
            db.rollback()
    with Session(owner) as db:
        refused(db, lambda: db.execute(text('UPDATE public.' + NAME + " SET reason='changed'")),
            'decision seal rows are immutable')
    assert snapshot(owner) == after
    return dict(closedRequest=closed,closedResult=result,actualApiSealCommit=True, exactAuditOnly=True, missingAuditCommitRefused=True,
        wrongRawKeyRefused=True, legacyCoordinateRefused=True, directWritesDenied=True,
        lateActionRevocationCommitRefused=True, sqlReadOnlySealedLookup=True,
        repeatSealNoChange=True, sealedSurvivesActionRevocation=True,
        independentLateWriterBlocked=True, lateWriterCommitRefused=True,
        immutableSeal=True, reservedAuditNamespaceCommitRefused=True,
        currentStockVerified=False, fullRecoveryLifecycle=False)
