"""Protected PG16 owner configuration/session locks; no real account or provider."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Barrier
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app import inventory_control_configuration as configuration
from app.foundation_models import AuditEvent
from app.inventory_control_authority_models import InventoryControlAuthorityDecision as Decision
from app.models import AuthSession
from app.security import create_access_token
from pg16_inventory_control_preparation_gate import _formal_stock
from test_inventory_control_authority import command


def assert_inventory_control_configuration_gate(owner_engine, world):
    before_stock = _formal_stock(owner_engine)
    with Session(owner_engine) as db:
        assert db.scalar(text('SELECT current_database()')) == 'rsc_pg16_release_gate'
        assert int(db.scalar(text('SHOW server_version_num'))) // 10000 == 16
        assert db.scalar(text('SELECT session_user')) == 'star_oam_migrator'
        now = configuration.authority._now(db)
        session = AuthSession(user_id=world.actor.user_id, refresh_token_hash=uuid4().hex+uuid4().hex,
            client_type='web', device_id='synthetic-control-'+uuid4().hex, ip_address='hmac:1:'+'a'*64,
            created_at=now-timedelta(seconds=1), expires_at=now+timedelta(hours=1))
        db.add(session); db.commit()
        session_id = session.id
        kwargs = dict(access_token=create_access_token(world.actor.user_id, session_id),
                      expected_authorization_version=world.actor.authorization_version, command=command(db, world))
        reviewed = configuration.preview_inventory_control_configuration(db, **kwargs)
        kwargs['review_sha256'] = reviewed['review_sha256']
    barrier = Barrier(2)
    def apply():
        with Session(owner_engine) as db:
            barrier.wait(timeout=20)
            result = configuration.execute_inventory_control_configuration(db, **kwargs)
            db.commit()
            return result
    with ThreadPoolExecutor(max_workers=2) as workers:
        first, second = list(workers.map(lambda _: apply(), range(2)))
    assert first == second and first['recorded']
    with Session(owner_engine) as db:
        assert configuration.read_inventory_control_configuration(db, **kwargs) == first
        assert len(tuple(db.scalars(select(AuditEvent).where(
            AuditEvent.request_id == 'control-configuration:' + first['decision']['decision_id'])))) == 1
        assert db.get(Decision, UUID(first['decision']['decision_id'])) is not None
        # Revoking the real synthetic session immediately closes this entry.
        db.get(AuthSession, session_id).revoked_at = configuration.authority._now(db)
        db.commit()
        with pytest.raises(configuration.ControlConfigurationError, match='authentication_required'):
            configuration.read_inventory_control_configuration(db, **kwargs)
        db.rollback()
    assert _formal_stock(owner_engine) == before_stock
    print('PG16 control configuration: signed live session, concurrent exact replay, atomic execution audit and revoked session refusal PASS', flush=True)
