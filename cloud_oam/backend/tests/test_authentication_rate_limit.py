from __future__ import annotations

from datetime import datetime, timedelta, timezone
import re

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Session

from app.formal_services import authentication_rate_limit as rate_limit
from app.formal_services.authentication_rate_limit import (
    AuthenticationLoginRateLimitError,
    cleanup_expired_authentication_login_rate_limit_buckets,
    consume_authentication_login_rate_limits,
    postgresql_authentication_rate_limit_advisory_statement,
)
from app.foundation_models import AuthLoginRateLimitBucket, Role


SECRET = "authentication-rate-limit-test-secret-at-least-32-characters"
OTHER_SECRET = "authentication-rate-limit-rotated-secret-at-least-32-characters"
NOW = datetime(2026, 8, 30, 5, 0, 15, tzinfo=timezone.utc)


@pytest.fixture
def limiter_engine(tmp_path):
    engine = create_engine(f"sqlite+pysqlite:///{tmp_path / 'rate-limit.db'}")
    Role.__table__.create(engine)
    AuthLoginRateLimitBucket.__table__.create(engine)
    try:
        yield engine
    finally:
        engine.dispose()


def _consume(engine, **overrides):
    values = {
        "operation_type": "sms_login",
        "hmac_secret": SECRET,
        "hash_version": 1,
        "window_seconds": 60,
        "global_limit": 100,
        "ip_address_value": "198.51.100.44",
        "ip_limit": 100,
        "identity_identifier": "13900000831",
        "identity_limit": 100,
        "now": NOW,
    }
    values.update(overrides)
    return consume_authentication_login_rate_limits(engine, **values)


def test_sqlite_consumes_global_ip_identity_in_fixed_order_without_raw_values(
    limiter_engine,
):
    first = _consume(limiter_engine, identity_limit=2)
    second = _consume(limiter_engine, identity_limit=2)
    denied = _consume(limiter_engine, identity_limit=2)

    assert first.allowed is True
    assert first.consumed_scopes == ("global", "ip", "identity")
    assert second.allowed is True
    assert denied.allowed is False
    assert denied.denied_scope == "identity"
    assert denied.consumed_scopes == ("global", "ip")
    assert denied.retry_after == 45

    with Session(limiter_engine) as db:
        rows = list(
            db.scalars(
                select(AuthLoginRateLimitBucket).order_by(
                    AuthLoginRateLimitBucket.scope_type
                )
            )
        )
        counts = {row.scope_type: row.request_count for row in rows}
        assert counts == {"global": 3, "identity": 2, "ip": 3}
        assert {row.hash_version for row in rows} == {1}
        assert all(re.fullmatch(r"[0-9a-f]{64}", row.scope_hmac) for row in rows)
        serialized = " ".join(
            "|".join(
                (
                    row.operation_type,
                    row.scope_type,
                    str(row.hash_version),
                    row.scope_hmac,
                )
            )
            for row in rows
        )
    assert "198.51.100.44" not in serialized
    assert "13900000831" not in serialized


def test_narrow_scope_denial_keeps_earlier_global_consumption(limiter_engine):
    assert _consume(limiter_engine, ip_limit=1).allowed is True
    denied = _consume(limiter_engine, ip_limit=1)
    assert denied.allowed is False
    assert denied.denied_scope == "ip"
    assert denied.consumed_scopes == ("global",)

    with Session(limiter_engine) as db:
        counts = {
            row.scope_type: row.request_count
            for row in db.scalars(select(AuthLoginRateLimitBucket))
        }
    assert counts == {"global": 2, "ip": 1, "identity": 1}


def test_hash_rotation_fails_closed_until_old_window_is_inactive(limiter_engine):
    assert _consume(limiter_engine).allowed is True

    with pytest.raises(
        AuthenticationLoginRateLimitError,
        match="authentication_login_rate_limit_hash_rotation_in_progress",
    ):
        _consume(
            limiter_engine,
            hmac_secret=OTHER_SECRET,
            hash_version=2,
        )

    after_old_window = NOW + timedelta(seconds=60)
    rotated = _consume(
        limiter_engine,
        hmac_secret=OTHER_SECRET,
        hash_version=2,
        now=after_old_window,
    )
    assert rotated.allowed is True
    with Session(limiter_engine) as db:
        assert set(
            db.scalars(select(AuthLoginRateLimitBucket.hash_version))
        ) == {1, 2}


def test_secret_change_without_version_bump_cannot_silently_double_quota(
    limiter_engine,
):
    assert _consume(limiter_engine).allowed is True
    with pytest.raises(
        AuthenticationLoginRateLimitError,
        match="authentication_login_rate_limit_secret_changed_without_version",
    ):
        _consume(
            limiter_engine,
            hmac_secret=OTHER_SECRET,
            hash_version=1,
        )

    with Session(limiter_engine) as db:
        globals_ = list(
            db.scalars(
                select(AuthLoginRateLimitBucket).where(
                    AuthLoginRateLimitBucket.scope_type == "global"
                )
            )
        )
    assert len(globals_) == 1
    assert globals_[0].request_count == 1


def test_window_change_cannot_silently_create_a_second_active_quota(
    limiter_engine,
):
    assert _consume(limiter_engine, window_seconds=60).allowed is True
    with pytest.raises(
        AuthenticationLoginRateLimitError,
        match="authentication_login_rate_limit_window_change_in_progress",
    ):
        _consume(limiter_engine, window_seconds=30)

    with Session(limiter_engine) as db:
        rows = list(db.scalars(select(AuthLoginRateLimitBucket)))
    assert len(rows) == 3
    assert {row.window_seconds for row in rows} == {60}
    assert {row.request_count for row in rows} == {1}


def test_cleanup_keeps_current_and_previous_window_margin(limiter_engine):
    assert _consume(limiter_engine).allowed is True
    # Window ends at 05:01.  A full additional window is retained, so neither
    # the boundary nor the immediately previous window is cleanup eligible.
    assert cleanup_expired_authentication_login_rate_limit_buckets(
        limiter_engine,
        now=datetime(2026, 8, 30, 5, 1, tzinfo=timezone.utc),
    ) == 0
    assert cleanup_expired_authentication_login_rate_limit_buckets(
        limiter_engine,
        now=datetime(2026, 8, 30, 5, 1, 59, tzinfo=timezone.utc),
    ) == 0
    assert cleanup_expired_authentication_login_rate_limit_buckets(
        limiter_engine,
        now=datetime(2026, 8, 30, 5, 2, tzinfo=timezone.utc),
        batch_size=2,
    ) == 2
    with Session(limiter_engine) as db:
        assert db.scalar(select(AuthLoginRateLimitBucket).limit(1)) is not None
    assert cleanup_expired_authentication_login_rate_limit_buckets(
        limiter_engine,
        now=datetime(2026, 8, 30, 5, 2, tzinfo=timezone.utc),
        batch_size=10,
    ) == 1


def test_limiter_commit_is_independent_from_uncommitted_request_session(
    limiter_engine,
):
    request_db = Session(limiter_engine, expire_on_commit=False)
    try:
        request_db.add(
            Role(
                code="rate-limit-uncommitted-sentinel",
                name="not committed",
                is_external=False,
                status="active",
            )
        )
        assert request_db.in_transaction() is True
        # The service refuses a request Connection, but an Engine creates its
        # own Session and commits only the limiter counters.
        assert _consume(limiter_engine).allowed is True
        request_db.rollback()
    finally:
        request_db.close()

    with Session(limiter_engine) as db:
        assert db.scalar(
            select(Role).where(Role.code == "rate-limit-uncommitted-sentinel")
        ) is None
        assert db.scalar(select(AuthLoginRateLimitBucket).limit(1)) is not None


def test_non_engine_bind_fails_closed(limiter_engine):
    with limiter_engine.connect() as connection:
        with pytest.raises(
            AuthenticationLoginRateLimitError,
            match="authentication_login_rate_limit_independent_bind_required",
        ):
            consume_authentication_login_rate_limits(
                connection,  # type: ignore[arg-type]
                operation_type="sms_login",
                hmac_secret=SECRET,
                hash_version=1,
                window_seconds=60,
                global_limit=1,
                now=NOW,
            )


def test_postgresql_advisory_and_cleanup_sql_shapes_are_explicit():
    lock_sql = str(
        postgresql_authentication_rate_limit_advisory_statement().compile(
            dialect=postgresql.dialect()
        )
    )
    assert lock_sql == "SELECT pg_advisory_xact_lock(%(lock_key)s)"

    cleanup_sql = str(
        rate_limit._expired_bucket_ids_statement(  # noqa: SLF001
            NOW,
            batch_size=100,
            postgresql_locking=True,
        ).compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "cleanup_after <=" in cleanup_sql
    assert "LIMIT 100" in cleanup_sql
    assert "FOR UPDATE SKIP LOCKED" in cleanup_sql
