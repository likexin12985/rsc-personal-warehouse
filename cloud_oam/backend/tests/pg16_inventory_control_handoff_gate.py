"""Real-role handoff checks called only inside the protected ephemeral PG16 gate."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from unittest.mock import patch
from uuid import uuid4

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
import pytest
from sqlalchemy import event, select, text
from sqlalchemy.orm import Session

from app import inventory_control_handoff as handoff
from app import inventory_control_configuration as configuration
from app.config import get_settings
from app.foundation_models import AuditEvent
from app.inventory_control_authority_models import InventoryControlAuthorityDecision as Decision
from app.models import AuthSession
from app.security import create_access_token
from pg16_inventory_control_preparation_gate import _formal_stock, _rejected
from test_inventory_control_authority import command


def assert_inventory_control_handoff_gate(owner_engine, api_engine, world):
    before = _formal_stock(owner_engine)
    api_key, owner_key = Ed25519PrivateKey.generate(), Ed25519PrivateKey.generate()
    common = dict(environment='production', control_configuration_handoff_enabled=True,
        control_configuration_deployment_id=str(uuid4()))
    api_settings = get_settings().model_copy(update=dict(common,
        control_configuration_api_private_key=api_key.private_bytes_raw().hex(),
        control_configuration_owner_public_key=owner_key.public_key().public_bytes_raw().hex(),
        control_configuration_owner_private_key='', control_configuration_api_public_key=''))
    owner_settings = get_settings().model_copy(update=dict(common,
        control_configuration_owner_private_key=owner_key.private_bytes_raw().hex(),
        control_configuration_api_public_key=api_key.public_key().public_bytes_raw().hex(),
        control_configuration_api_private_key='', control_configuration_owner_public_key=''))
    with Session(owner_engine) as db:
        assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num'))) // 10000 == 16
        assert db.scalar(text('SELECT session_user')) == 'star_oam_migrator'
        current = configuration.authority.resolve_inventory_control_authority(db, preparation_id=world.root)
        grant = db.get(Decision, current['source_grant_id'])
        cmd = command(db, world, action='catalog_grant', grant=configuration.authority._result(grant))
        now = configuration.authority._now(db)
        session = AuthSession(user_id=world.actor.user_id, refresh_token_hash=uuid4().hex+uuid4().hex,
            client_type='web', device_id='synthetic-handoff-'+uuid4().hex, ip_address='hmac:1:'+'a'*64,
            created_at=now-timedelta(seconds=1), expires_at=now+timedelta(hours=1))
        db.add(session); db.commit()
        session_id = session.id
        token = create_access_token(world.actor.user_id, session_id)
    kwargs = dict(access_token=token, actor=world.actor, expected_version=world.actor.authorization_version, command=cmd)
    statements = []
    def capture(conn, cursor, sql, *rest): statements.append(sql)
    def issue(purpose, response=None):
        with patch.object(handoff, 'get_settings', lambda: api_settings), Session(api_engine) as db:
            assert db.scalar(text('SELECT session_user')) == 'star_oam_api'
            packet = handoff.issue_control_handoff(db, **kwargs, purpose=purpose, owner_response=response)
            db.commit(); return packet
    event.listen(api_engine, 'before_cursor_execute', capture)
    try:
        packet = issue('preview')
        with patch.object(handoff, 'get_settings', lambda: owner_settings), Session(owner_engine) as db:
            review = handoff.run_owner_handoff(db, packet); db.rollback()
        with patch.object(handoff, 'get_settings', lambda: api_settings), Session(api_engine) as db:
            assert handoff.inspect_owner_response(db, **kwargs, envelope=review)['can_execute']
        execute = issue('execute', review)
    finally:
        event.remove(api_engine, 'before_cursor_execute', capture)
    assert not any('inventory_control_' in sql for sql in statements)
    barrier = Barrier(2)
    def apply():
        with Session(owner_engine) as db:
            barrier.wait(timeout=20)
            result = handoff.run_owner_handoff(db, execute); db.commit(); return result
    with patch.object(handoff, 'get_settings', lambda: owner_settings), ThreadPoolExecutor(max_workers=2) as workers:
        first, second = list(workers.map(lambda _: apply(), range(2)))
    assert first['payload']['result'] == second['payload']['result']
    result = first['payload']['result']
    assert result['recorded'] and not result['projection_published'] and not result['start_ready']
    status = issue('status', first)
    with patch.object(handoff, 'get_settings', lambda: owner_settings), Session(owner_engine) as db:
        assert handoff.run_owner_handoff(db, status)['payload']['result'] == result
        assert len(tuple(db.scalars(select(AuditEvent).where(
            AuditEvent.request_id == 'control-configuration:' + result['decision']['decision_id'])))) == 1
        db.get(AuthSession, session_id).revoked_at = configuration.authority._now(db); db.commit()
        with pytest.raises(configuration.ControlConfigurationError, match='authentication_required'):
            handoff.run_owner_handoff(db, status)
        db.rollback()
    _rejected(api_engine, 'SELECT * FROM inventory_control_authority_decisions', state='42501')
    assert _formal_stock(owner_engine) == before
    print('PG16 signed handoff: separate signing authorities, real API issuance ACL, owner concurrent replay, session revocation and independent states PASS', flush=True)
