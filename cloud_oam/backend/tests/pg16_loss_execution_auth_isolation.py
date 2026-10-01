"""Real session/audit commits must not acquire the inventory ledger lock.

Run only inside the acknowledged, disposable PG16 release fixture. This uses
synthetic identities and real session services, not an SMS/WeChat provider.
Tokens remain in process memory and are never included in test evidence.
"""
import hashlib
import json
from uuid import uuid4

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth_sessions import create_session
from app.foundation_models import (
    AuditChainHead, AuditEvent, AuthRefreshToken, StateTransitionEvent,
)
from app.models import AuthSession, User
from app.formal_services.authentication_session import record_session_created
from app.formal_services.audit_chain import verify_audit_event_in_read_snapshot


PROTECTED = (
    'inventory_transactions', 'inventory_movements', 'inventory_movement_serials',
    'stock_balances', 'stock_accounts', 'stock_locations', 'inventory_ledger_heads',
    'serial_current_positions', 'daily_reconciliation_cutoffs',
    'outbox_events', 'notification_events', 'notification_recipients',
    'notification_deliveries', 'notification_attempts',
)


def facts(owner):
    with owner.connect() as connection:
        return {
            name: hashlib.sha256(json.dumps(connection.scalar(text(
                "SELECT COALESCE(jsonb_agg(to_jsonb(t) ORDER BY to_jsonb(t)::text), '[]'::jsonb) "
                'FROM public.' + name + ' t'
            )), sort_keys=True, default=str).encode()).hexdigest()
            for name in PROTECTED
        }


def verify(engines, *, user_id):
    owner, api = (engines[k] for k in ('star_oam_migrator', 'star_oam_api'))
    with owner.connect() as db:
        assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num'))) // 10000 == 16
    with Session(owner) as db:
        head = db.scalar(select(AuditChainHead).where(
            AuditChainHead.stream_key == 'authentication'))
        if head is None:
            db.add(AuditChainHead(stream_key='authentication', version=0))
            db.commit()
    before = facts(owner)
    cases = []
    for isolation in ('READ COMMITTED', 'REPEATABLE READ'):
        with Session(owner) as db:
            previous_version = db.scalar(select(AuditChainHead.version).where(
                AuditChainHead.stream_key == 'authentication'))
        with owner.connect() as inventory_lock:
            # .one() proves an existing row was locked; an empty result would
            # silently test no contention and give false confidence.
            inventory_lock.execute(text(
                "SELECT id FROM inventory_ledger_heads WHERE stream_key='inventory' FOR UPDATE"
            )).one()
            with api.connect().execution_options(isolation_level=isolation) as connection:
                with Session(connection) as db:
                    assert db.scalar(text('SELECT current_user')) == 'star_oam_api'
                    assert db.scalar(text('SHOW transaction_isolation')).upper() == isolation
                    db.execute(text("SET LOCAL lock_timeout='500ms'"))
                    db.execute(text("SET LOCAL statement_timeout='10s'"))
                    user = db.get(User, user_id)
                    assert user is not None
                    tokens = create_session(db, user, client_type='web',
                        device_id='synthetic-ledger-scope-' + uuid4().hex,
                        ip_address='127.0.0.1')
                    session_id = tokens.auth_session.id
                    record_session_created(db, tokens=tokens, actor_user_id=user.id,
                        request_id=uuid4().hex, reason_code='synthetic_inventory_lock_isolation')
                    db.commit()
                    del tokens
            # Read after COMMIT while the other transaction still holds the
            # inventory lock, so successful flush cannot masquerade as success.
            with Session(api) as db:
                session = db.get(AuthSession, session_id)
                assert session is not None and session.user_id == user_id
                assert session.revoked_at is None
                assert db.scalars(select(AuthRefreshToken).where(
                    AuthRefreshToken.session_id == session_id)).one().revoked_at is None
                state = db.scalars(select(StateTransitionEvent).where(
                    StateTransitionEvent.aggregate_type == 'auth_session',
                    StateTransitionEvent.aggregate_id == session_id)).one()
                assert state.actor_id == user_id and state.to_status == 'active'
                audit = db.scalars(select(AuditEvent).where(
                    AuditEvent.stream_key == 'authentication',
                    AuditEvent.aggregate_id == session_id)).one()
                assert audit.actor_user_id == user_id
                assert audit.action == 'authentication.session.created'
                verify_audit_event_in_read_snapshot(db, stream_key='authentication', event_id=audit.id)
                assert db.scalar(select(AuditChainHead.version).where(
                    AuditChainHead.stream_key == 'authentication')) == previous_version + 1
            inventory_lock.rollback()
        assert facts(owner) == before
        cases.append(dict(isolation=isolation, committedWhileLedgerLocked=True,
            sessionAndRefreshTokenPersisted=True, stateAndAuditVerified=True,
            inventoryAndNotificationsUnchanged=True))
    return dict(passed=True, actualApiRole=True, syntheticIdentityOnly=True,
        realProviderAcceptance=False, cases=cases)
