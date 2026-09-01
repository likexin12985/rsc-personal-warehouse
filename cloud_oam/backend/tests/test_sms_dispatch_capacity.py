from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
import os
from pathlib import Path
import subprocess
import sys
import threading
import uuid

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import QueuePool

from app.config import Settings
from app.routers import auth
from app.sms import SmsSendResult


ROOT = Path(__file__).resolve().parents[2]
BACKEND = ROOT / "backend"
DISPATCH_LIMIT = 2
CONCURRENT_TASKS = 16


class BlockingSmsProvider:
    def __init__(self, *, limit: int, release: threading.Event) -> None:
        self.limit = limit
        self.release = release
        self.full = threading.Event()
        self.lock = threading.Lock()
        self.calls: list[str] = []
        self.active = 0
        self.max_active = 0

    def send(self, _mobile: str, out_id: str) -> SmsSendResult:
        with self.lock:
            self.calls.append(out_id)
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            if self.active == self.limit:
                self.full.set()
        try:
            assert self.release.wait(timeout=10)
            return SmsSendResult(biz_id=f"provider-{out_id}", out_id=out_id)
        finally:
            with self.lock:
                self.active -= 1


@dataclass(frozen=True)
class DispatchCapacityWorld:
    capacity: threading.BoundedSemaphore
    dispatch_engine: Engine
    main_engine: Engine
    main_session_factory: sessionmaker[Session]
    provider: BlockingSmsProvider
    release: threading.Event
    state: dict[str, int]


@pytest.fixture
def dispatch_capacity_world(
    monkeypatch: pytest.MonkeyPatch,
) -> DispatchCapacityWorld:
    release = threading.Event()
    capacity = threading.BoundedSemaphore(value=DISPATCH_LIMIT)
    dispatch_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=DISPATCH_LIMIT,
        max_overflow=0,
        pool_timeout=1,
    )
    main_engine = create_engine(
        "sqlite+pysqlite://",
        connect_args={"check_same_thread": False},
        poolclass=QueuePool,
        pool_size=1,
        max_overflow=0,
        pool_timeout=1,
    )
    raw_dispatch_factory = sessionmaker(
        bind=dispatch_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    main_session_factory = sessionmaker(bind=main_engine)
    provider = BlockingSmsProvider(limit=DISPATCH_LIMIT, release=release)
    state: dict[str, int] = {
        "session_calls": 0,
        "authorized": 0,
        "marked_sent": 0,
    }
    state_lock = threading.Lock()

    def dispatch_session_factory() -> Session:
        with state_lock:
            state["session_calls"] += 1
        return raw_dispatch_factory()

    def authorize_provider_call(db: Session, **_kwargs: object) -> bool:
        # Check out and retain one dedicated dispatch-pool connection until the
        # blocked provider call has completed and the outer transaction commits.
        assert db.scalar(text("SELECT 1")) == 1
        with state_lock:
            state["authorized"] += 1
        return True

    def mark_sent(_db: Session, **_kwargs: object) -> None:
        with state_lock:
            state["marked_sent"] += 1

    monkeypatch.setattr(auth, "SmsDispatchSessionLocal", dispatch_session_factory)
    monkeypatch.setattr(auth, "_SMS_PROVIDER_CAPACITY", capacity)
    monkeypatch.setattr(auth, "get_sms_provider", lambda: provider)
    monkeypatch.setattr(
        auth.authentication_challenge,
        "authorize_dispatch_provider_call",
        authorize_provider_call,
    )
    monkeypatch.setattr(auth.authentication_challenge, "mark_sent", mark_sent)

    world = DispatchCapacityWorld(
        capacity=capacity,
        dispatch_engine=dispatch_engine,
        main_engine=main_engine,
        main_session_factory=main_session_factory,
        provider=provider,
        release=release,
        state=state,
    )
    try:
        yield world
    finally:
        release.set()
        dispatch_engine.dispose()
        main_engine.dispose()


def test_sixteen_dispatch_tasks_fail_fast_before_session_when_capacity_is_full(
    dispatch_capacity_world: DispatchCapacityWorld,
) -> None:
    world = dispatch_capacity_world
    start = threading.Barrier(CONCURRENT_TASKS + 1)

    def dispatch(index: int) -> None:
        start.wait(timeout=5)
        challenge_id = uuid.uuid5(uuid.NAMESPACE_OID, f"sms-dispatch-{index}")
        auth._complete_formal_sms_dispatch(
            mobile=f"test-mobile-{index}",
            challenge_id=challenge_id,
            owner_token=f"owner-{index:04d}",
            request_id=f"request-{index:04d}",
        )

    executor = ThreadPoolExecutor(max_workers=CONCURRENT_TASKS)
    futures = [executor.submit(dispatch, index) for index in range(CONCURRENT_TASKS)]
    try:
        start.wait(timeout=5)
        assert world.provider.full.wait(timeout=5)

        completed, still_running = wait(futures, timeout=2)
        assert len(completed) == CONCURRENT_TASKS - DISPATCH_LIMIT
        assert len(still_running) == DISPATCH_LIMIT
        assert all(future.exception() is None for future in completed)
        assert world.state["session_calls"] == DISPATCH_LIMIT
        assert world.state["authorized"] == DISPATCH_LIMIT
        assert len(world.provider.calls) == DISPATCH_LIMIT
        assert world.provider.max_active == DISPATCH_LIMIT
        assert world.dispatch_engine.pool.checkedout() == DISPATCH_LIMIT

        # Both dedicated dispatch connections remain occupied above.  A normal
        # API query uses the independent main engine and therefore still runs.
        with world.main_session_factory() as db:
            assert db.scalar(text("SELECT 42")) == 42
        assert world.dispatch_engine.pool.checkedout() == DISPATCH_LIMIT
        assert world.main_engine.pool.checkedout() == 0
    finally:
        world.release.set()
        executor.shutdown(wait=True)

    assert [future.result(timeout=1) for future in futures] == [None] * CONCURRENT_TASKS
    assert world.state["marked_sent"] == DISPATCH_LIMIT
    assert world.dispatch_engine.pool.checkedout() == 0

    # Every admitted path released its permit, including the finally boundary.
    assert world.capacity.acquire(blocking=False)
    assert world.capacity.acquire(blocking=False)
    assert not world.capacity.acquire(blocking=False)
    world.capacity.release()
    world.capacity.release()


def test_sms_provider_concurrency_setting_has_safe_default_and_bounds() -> None:
    field = Settings.model_fields["sms_provider_max_concurrency"]
    assert field.default == 2
    assert isinstance(
        auth._SMS_PROVIDER_CAPACITY,
        type(threading.BoundedSemaphore()),
    )
    assert (
        Settings(
            _env_file=None,
            sms_provider_max_concurrency=1,
        ).sms_provider_max_concurrency
        == 1
    )
    assert (
        Settings(
            _env_file=None,
            sms_provider_max_concurrency=5,
        ).sms_provider_max_concurrency
        == 5
    )

    for invalid in (0, 6):
        with pytest.raises(ValidationError, match="sms_provider_max_concurrency"):
            Settings(_env_file=None, sms_provider_max_concurrency=invalid)


def test_production_postgresql_dispatch_pool_is_bounded_and_independent() -> None:
    environment = os.environ.copy()
    environment.update(
        {
            "PYTHONPATH": str(BACKEND),
            "OAM_ENVIRONMENT": "production",
            "OAM_DATABASE_URL": (
                "postgresql+psycopg://star_oam_api:test@127.0.0.1:1/test"
            ),
            "OAM_DATABASE_SCHEMA_MODE": "alembic",
            "OAM_LEGACY_PROTOTYPE_WRITES_ENABLED": "false",
            "OAM_ADMIN_MOBILE": "",
            "OAM_ADMIN_NAME": "",
            "OAM_ADMIN_INITIAL_PASSWORD": "",
            "OAM_PASSWORD_LOGIN_ENABLED": "false",
            "OAM_SMS_LOGIN_ENABLED": "false",
            "OAM_SMS_PROVIDER": "disabled",
            "OAM_SMS_PROVIDER_MAX_CONCURRENCY": "4",
            "OAM_WECHAT_LOGIN_ENABLED": "false",
            "OAM_WECHAT_PROVIDER": "disabled",
            "OAM_EDGE_SYNC_ENABLED": "false",
            "OAM_EDGE_SYNC_LEGACY_BATCHES_ENABLED": "false",
            "OAM_EDGE_SYNC_LEGACY_PERSONNEL_PROJECTION_ENABLED": "false",
        }
    )
    # SQLAlchemy engine construction is lazy.  The script only inspects engine
    # and pool objects; it never attempts a connection to the sentinel URL.
    script = """
from app import database

assert database.sms_dispatch_engine is not database.engine
assert database.sms_dispatch_engine.pool is not database.engine.pool
assert database.SessionLocal.kw["bind"] is database.engine
assert (
    database.SmsDispatchSessionLocal.kw["bind"]
    is database.sms_dispatch_engine
)
pool = database.sms_dispatch_engine.pool
assert pool.size() == 4
assert pool._max_overflow == 0
assert pool._timeout == 1
database.sms_dispatch_engine.dispose()
database.engine.dispose()
"""
    subprocess.run(
        [sys.executable, "-c", script],
        cwd=ROOT,
        env=environment,
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
