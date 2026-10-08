"""Actual API seal COMMIT and independent late-writer fence on owned PG16."""
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
from app.return_condition_seal_schema import NAME
from app.formal_services.stock_loss_corrections import return_condition_request_seals as writer
from app.formal_services.stock_loss_corrections import return_condition_request_inputs as inputs
from app.formal_services.stock_loss_corrections import return_condition_recovery as recovery
from pg16_return_condition_authority_gate import owned_role_connection
from pg16_return_condition_key_gate import refused, header


def register(db, actor, command, raw_key=None):
    return db.scalar(text('SELECT public.rsc_register_condition_seal(:actor,:version,:person,CAST(:command AS jsonb),:key)'),
        dict(actor=actor.user_id, version=actor.authorization_version, person=actor.person_id,
            command=json.dumps(inputs.canonical(command)), key=raw_key or command.idempotency_key))


def authentication_without_inventory_wait(api, original):
    """Real authentication evidence adapter; no credentials or provider call."""
    from app.formal_services.authentication_audit import append_authentication_event, add_authentication_state_transition
    def record():
        with Session(api) as db:
            db.execute(text("SET LOCAL lock_timeout='1500ms'"))
            at = datetime.now(timezone.utc); identifier = str(uuid4()); request_id = uuid4().hex
            append_authentication_event(db, actor_user_id=original['receiverUserId'],
                action='owned_gate', aggregate_type='authentication_attempt', aggregate_id=identifier,
                request_id=request_id, client_type='web', outcome='rejected', reason_code='owned_fixture', occurred_at=at)
            add_authentication_state_transition(db, actor_user_id=original['receiverUserId'],
                aggregate_type='authentication_attempt', aggregate_id=identifier, request_id=request_id,
                from_status=None, to_status='rejected', reason_code='owned_fixture', occurred_at=at)
            db.commit()
            return True
    with Session(api) as inventory, ThreadPoolExecutor(max_workers=1) as executor:
        try:
            from app.formal_services import inventory_posting as posting
            posting._lock_inventory_ledger_head_for_atomic_batch(inventory)
            assert executor.submit(record).result(timeout=10)
        finally:
            inventory.rollback()
    return True


def run(owner, api, *, directory, original, command, permission_link_id):
    from pg16_return_condition_submission_gate import snapshot
    before = snapshot(owner)
    closed = command.model_copy(update={'request_id': uuid4().hex, 'idempotency_key': uuid4().hex,
        'expected_source_hash': 'a' * 64})
    # Internal audit request IDs remain reserved even though an ordinary client
    # request may carry the same prefix. A genuine API audit COMMIT must refuse
    # a fabricated namespace rather than leaving detached seal evidence.
    from app.formal_services.audit_chain import append_audit_event
    with Session(api) as db:
        at = datetime.now(timezone.utc)
        append_audit_event(db, stream_key='inventory', actor_user_id=original['receiverUserId'],
            action='owned_probe', aggregate_type='owned_condition_probe', aggregate_id=str(uuid4()),
            before_jsonb={}, after_jsonb={}, request_id='condition-seal:'+str(uuid4()),
            occurred_at=at, created_at=at)
        refused(db, db.commit, 'condition seal effect namespace required')
    for sql in ('INSERT INTO public.' + NAME + ' DEFAULT VALUES',
            'UPDATE public.' + NAME + " SET reason='changed' WHERE false",
            'DELETE FROM public.' + NAME + ' WHERE false', 'TRUNCATE public.' + NAME):
        with Session(api) as db:
            refused(db, lambda: db.execute(text(sql)), 'permission denied', code='42501')
    with Session(api) as db:
        actor = load_formal_principal(db, original['receiverUserId'])
        record = register(db, actor, closed)
        assert record['created'] is True
        refused(db, db.commit, 'condition seal requires one audit and no business effects')
    with Session(api) as db:
        actor = load_formal_principal(db, original['receiverUserId'])
        refused(db, lambda: register(db, actor, closed, uuid4().hex), 'condition seal original key does not match input')
    with Session(api) as db:
        actor = load_formal_principal(db, original['receiverUserId'])
        old = closed.model_copy(update={'request_id': original['inboundRequestId']})
        refused(db, lambda: register(db, actor, old), 'condition sealed key collides with business evidence')
    with Session(api) as db:
        actor = load_formal_principal(db, original['receiverUserId'])
        header(db, closed)
        refused(db, lambda: register(db, actor, closed), 'condition sealed key collides with business evidence')
    assert snapshot(owner) == before
    # Only the disposable owner's fixture can revoke an action inside this test.
    # The real service and actual deferred COMMIT execute as API.
    with owned_role_connection(owner, directory) as connection:
        connection.rollback()
        with Session(bind=connection) as db:
            db.execute(text('SET LOCAL ROLE star_oam_api'))
            writer.seal(db, actor=load_formal_principal(db, original['receiverUserId']), request=closed)
            db.execute(text('SET LOCAL ROLE star_oam_migrator'))
            db.execute(update(RolePermission).where(RolePermission.id == permission_link_id).values(effect='deny'))
            db.execute(text('SET LOCAL ROLE star_oam_api'))
            refused(db, db.commit, 'condition seal current read and action permission required')
    assert snapshot(owner) == before
    ready = Queue()
    def late():
        with Session(api) as db:
            db.execute(text("SET LOCAL statement_timeout='15000ms'"))
            ready.put(db.scalar(text('SELECT pg_backend_pid()')))
            header(db, closed)
            refused(db, db.commit, 'condition sealed key collides with business evidence')
            return True
    with Session(api) as db, ThreadPoolExecutor(max_workers=1) as executor:
        try:
            pid = db.scalar(text('SELECT pg_backend_pid()'))
            result = writer.seal(db, actor=load_formal_principal(db, original['receiverUserId']), request=closed)
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
            assert pending.result(timeout=10)
        finally:
            db.rollback()
    after = snapshot(owner)
    assert {name for name in before if before[name] != after[name]} == {
        NAME, 'audit_events', 'audit_chain_heads'}
    assert len(after[NAME]) == len(before[NAME]) + 1
    assert len(after['audit_events']) == len(before['audit_events']) + 1
    with Session(api) as db:
        db.execute(text('SET TRANSACTION READ ONLY'))
        actual = recovery.lookup(db, actor=load_formal_principal(db, original['receiverUserId']), request=closed)
        assert actual == result and actual['absence_sealed'] is True and not actual['retry_allowed']
    with Session(api) as db:
        actor = load_formal_principal(db, original['receiverUserId'])
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
            actor = load_formal_principal(db, original['receiverUserId'])
            assert recovery.lookup(db, actor=actor, request=closed)['request_state'] == 'sealed'
            assert writer.seal(db, actor=actor, request=command)['request_state'] == 'found'
            assert register(db, actor, closed)['created'] is False
            db.rollback()
    with Session(owner) as db:
        refused(db, lambda: db.execute(text('UPDATE public.' + NAME + " SET reason='changed'")),
            'condition history is append only')
    assert snapshot(owner) == after
    return dict(actualApiSealCommit=True, exactAuditOnly=True, missingAuditCommitRefused=True,
        wrongRawKeyRefused=True, legacyCoordinateRefused=True, directWritesDenied=True,
        lateActionRevocationCommitRefused=True, sqlReadOnlySealedLookup=True,
        repeatSealNoChange=True, sealedAndFoundSurviveActionRevocation=True,
        independentLateWriterBlocked=True, lateWriterCommitRefused=True,
        immutableSeal=True, reservedAuditNamespaceCommitRefused=True,
        currentStockVerified=False, fullRecoveryLifecycle=False)
