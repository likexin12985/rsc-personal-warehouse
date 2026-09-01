"""Persist and enforce the exact audit stream membership of every event.

Revision ID: 20260831_0017
Revises: 20260831_0016
Create Date: 2026-08-31

The v1 audit hash already includes ``stream_key`` but the original table did
not persist either that key or its sequence number.  This revision derives the
only canonical mapping from the three immutable heads, stores it, and makes a
PostgreSQL transaction unable to commit an event that was not consumed by the
matching forward-only head.  The existing v1 hash document is deliberately
unchanged.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Sequence, Union
import uuid

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260831_0017"
down_revision: Union[str, Sequence[str], None] = "20260831_0016"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


EVENT_TABLE = "audit_events"
HEAD_TABLE = "audit_chain_heads"
PRODUCTION_API_ROLE = "star_oam_api"
EXPECTED_HEAD_IDS = {
    "authorization": uuid.UUID("30000000-0000-4000-8000-000000000001"),
    "authentication": uuid.UUID("30000000-0000-4000-8000-000000000002"),
    "inventory": uuid.UUID("30000000-0000-4000-8000-000000000003"),
}
MAX_BIGINT = 2**63 - 1
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")

CHECK_STREAM_KEY = "ck_audit_events_stream_key_0017"
CHECK_STREAM_VERSION = "ck_audit_events_stream_version_0017"
UNIQUE_STREAM_VERSION = "uq_audit_events_stream_version_0017"
FK_STREAM_HEAD = "fk_audit_events_stream_key_0017"

PG_HEAD_FUNCTION = "rsc_validate_audit_stream_head_binding_0017"
PG_HEAD_TRIGGER = "trg_audit_chain_heads_stream_binding_0017"
PG_COMMIT_FUNCTION = "rsc_require_audit_event_commit_binding_0017"
PG_COMMIT_TRIGGER = "trg_audit_events_commit_binding_0017"

SQLITE_EVENT_INSERT_TRIGGER = "trg_audit_events_stream_binding_insert_0017"
SQLITE_HEAD_TRIGGER = "trg_audit_chain_heads_stream_binding_0017"
SQLITE_EVENT_UPDATE_0015 = "trg_audit_events_immutable_update_0015"
SQLITE_EVENT_DELETE_0015 = "trg_audit_events_immutable_delete_0015"
SQLITE_HEAD_UPDATE_0015 = "trg_audit_chain_heads_forward_only_0015"
SQLITE_HEAD_DELETE_0015 = "trg_audit_chain_heads_no_delete_0015"

PG_EVENT_UPDATE_0015 = "trg_audit_events_immutable_0015"
EXPECTED_PG_0015_TRIGGERS = {
    "trg_audit_events_immutable_0015",
    "trg_audit_events_immutable_truncate_0015",
    "trg_audit_chain_heads_forward_only_0015",
    "trg_audit_chain_heads_no_truncate_0015",
}
EXPECTED_SQLITE_0015_TRIGGERS = {
    SQLITE_EVENT_UPDATE_0015,
    SQLITE_EVENT_DELETE_0015,
    "trg_audit_chain_heads_forward_only_0015",
    "trg_audit_chain_heads_no_delete_0015",
}

UPGRADE_BLOCKER = (
    "0017 preflight failed: audit stream membership cannot be derived "
    "canonically from the fixed immutable heads"
)
OFFLINE_UPGRADE_BLOCKER = (
    "0017 populated audit streams require an online canonical mapping preflight"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0017: persisted audit events require stream binding"
)


class _AuditBindingPreflightFailure(RuntimeError):
    """Internal sentinel hidden behind the fixed migration blocker."""


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0017 supports only PostgreSQL production and SQLite local test schemas"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0017 SQLite upgrade requires an online connection")
        _upgrade_postgresql_offline()
        return

    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        _lock_postgresql_audit_tables()

    mapping = _canonical_stream_mapping(dialect)
    if dialect == "sqlite" and mapping and _sqlite_foreign_keys_enabled():
        # Recreating a referenced SQLite parent table records a deferred DROP
        # violation even if the replacement has the same name and rows.  Never
        # turn FK enforcement off silently on a connection that enabled it.
        raise RuntimeError(
            "0017 SQLite populated upgrade requires a controlled migration "
            "connection with PRAGMA foreign_keys disabled"
        )

    op.add_column(
        EVENT_TABLE,
        sa.Column("stream_key", sa.String(length=160), nullable=True),
    )
    op.add_column(
        EVENT_TABLE,
        sa.Column("stream_version", sa.BigInteger(), nullable=True),
    )

    if dialect == "postgresql":
        op.execute(
            f"ALTER TABLE {EVENT_TABLE} DISABLE TRIGGER {PG_EVENT_UPDATE_0015}"
        )
    else:
        _drop_sqlite_0015_event_guards()
        _drop_sqlite_0015_head_guards()

    _backfill_stream_coordinates(mapping)
    _verify_backfilled_coordinates(mapping)

    if dialect == "postgresql":
        op.execute(
            f"ALTER TABLE {EVENT_TABLE} ENABLE ALWAYS TRIGGER "
            f"{PG_EVENT_UPDATE_0015}"
        )
        _create_postgresql_constraints()
        _create_postgresql_guards()
        _apply_postgresql_acl()
    else:
        _rebuild_sqlite_event_constraints()
        _create_sqlite_0015_event_guards()
        _create_sqlite_0015_head_guards()
        _create_sqlite_0017_guards()
        _assert_sqlite_foreign_keys_clean()


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0017 downgrade requires an online connection for fail-closed "
            "audit evidence checks"
        )
    dialect = _dialect_name()
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        _lock_postgresql_audit_tables()

    bind = op.get_bind()
    if bind.exec_driver_sql(f"SELECT 1 FROM {EVENT_TABLE} LIMIT 1").first():
        raise RuntimeError(DOWNGRADE_BLOCKER)
    try:
        heads = bind.exec_driver_sql(
            f"SELECT id, stream_key, last_event_id, last_hash, version "
            f"FROM {HEAD_TABLE} ORDER BY stream_key"
        ).mappings().all()
        _require_empty_fixed_heads(heads)
    except Exception:
        raise RuntimeError(DOWNGRADE_BLOCKER) from None

    if dialect == "postgresql":
        op.execute(f"DROP TRIGGER {PG_COMMIT_TRIGGER} ON {EVENT_TABLE}")
        op.execute(f"DROP FUNCTION {PG_COMMIT_FUNCTION}()")
        op.execute(f"DROP TRIGGER {PG_HEAD_TRIGGER} ON {HEAD_TABLE}")
        op.execute(f"DROP FUNCTION {PG_HEAD_FUNCTION}()")
        op.drop_constraint(FK_STREAM_HEAD, EVENT_TABLE, type_="foreignkey")
        op.drop_constraint(UNIQUE_STREAM_VERSION, EVENT_TABLE, type_="unique")
        op.drop_constraint(CHECK_STREAM_VERSION, EVENT_TABLE, type_="check")
        op.drop_constraint(CHECK_STREAM_KEY, EVENT_TABLE, type_="check")
        op.drop_column(EVENT_TABLE, "stream_version")
        op.drop_column(EVENT_TABLE, "stream_key")
        return

    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_HEAD_TRIGGER}")
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_EVENT_INSERT_TRIGGER}")
    _drop_sqlite_0015_event_guards()
    _drop_sqlite_0015_head_guards()
    with op.batch_alter_table(EVENT_TABLE, recreate="always") as batch_op:
        batch_op.drop_constraint(FK_STREAM_HEAD, type_="foreignkey")
        batch_op.drop_constraint(UNIQUE_STREAM_VERSION, type_="unique")
        batch_op.drop_constraint(CHECK_STREAM_VERSION, type_="check")
        batch_op.drop_constraint(CHECK_STREAM_KEY, type_="check")
        batch_op.drop_column("stream_version")
        batch_op.drop_column("stream_key")
    _create_sqlite_0015_event_guards()
    _create_sqlite_0015_head_guards()
    _assert_sqlite_foreign_keys_clean()


def _upgrade_postgresql_offline() -> None:
    _lock_postgresql_audit_tables()
    op.execute(
        f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM {EVENT_TABLE}) THEN
        RAISE EXCEPTION '{OFFLINE_UPGRADE_BLOCKER}';
    ELSIF (SELECT count(*) FROM {HEAD_TABLE}) <> 3
       OR EXISTS (
           SELECT 1
             FROM (VALUES
                 ('30000000-0000-4000-8000-000000000001'::uuid, 'authorization'),
                 ('30000000-0000-4000-8000-000000000002'::uuid, 'authentication'),
                 ('30000000-0000-4000-8000-000000000003'::uuid, 'inventory')
             ) AS expected(id, stream_key)
             LEFT JOIN {HEAD_TABLE} AS head
               ON head.id = expected.id
              AND head.stream_key = expected.stream_key
            WHERE head.id IS NULL
               OR head.version <> 0
               OR head.last_event_id IS NOT NULL
               OR head.last_hash IS NOT NULL
       ) THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    ELSIF 4 <> (
        SELECT count(*)
          FROM pg_trigger AS trigger_row
          JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid
          JOIN pg_namespace AS schema_row
            ON schema_row.oid = table_row.relnamespace
         WHERE schema_row.nspname = 'public'
           AND trigger_row.tgname IN (
               'trg_audit_events_immutable_0015',
               'trg_audit_events_immutable_truncate_0015',
               'trg_audit_chain_heads_forward_only_0015',
               'trg_audit_chain_heads_no_truncate_0015'
           )
           AND trigger_row.tgenabled = 'A'
    ) THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END $$
"""
    )
    op.add_column(
        EVENT_TABLE,
        sa.Column("stream_key", sa.String(length=160), nullable=False),
    )
    op.add_column(
        EVENT_TABLE,
        sa.Column("stream_version", sa.BigInteger(), nullable=False),
    )
    _create_postgresql_constraints()
    _create_postgresql_guards()
    _apply_postgresql_acl()


def _lock_postgresql_audit_tables() -> None:
    # Runtime appenders lock one head before inserting their event.  Taking the
    # same table first lets an in-flight writer finish before DDL blocks every
    # new writer, avoiding an event/head lock-order inversion.
    op.execute(f"LOCK TABLE {HEAD_TABLE} IN ACCESS EXCLUSIVE MODE")
    op.execute(f"LOCK TABLE {EVENT_TABLE} IN ACCESS EXCLUSIVE MODE")


def _ensure_sqlite_migration_transaction() -> None:
    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _sqlite_foreign_keys_enabled() -> bool:
    return bool(op.get_bind().exec_driver_sql("PRAGMA foreign_keys").scalar_one())


def _canonical_stream_mapping(dialect: str) -> list[dict[str, Any]]:
    bind = op.get_bind()
    try:
        _require_existing_0015_guards(dialect)
        heads = bind.exec_driver_sql(
            f"SELECT id, stream_key, last_event_id, last_hash, version "
            f"FROM {HEAD_TABLE} ORDER BY stream_key"
        ).mappings().all()
        events = bind.exec_driver_sql(
            "SELECT id, actor_user_id, action, aggregate_type, aggregate_id, "
            "before_jsonb, after_jsonb, request_id, previous_hash, event_hash, "
            f"occurred_at FROM {EVENT_TABLE} ORDER BY id"
        ).mappings().all()
        return _derive_stream_mapping(heads=heads, events=events)
    except Exception:
        raise RuntimeError(UPGRADE_BLOCKER) from None


def _require_existing_0015_guards(dialect: str) -> None:
    bind = op.get_bind()
    if dialect == "postgresql":
        rows = bind.exec_driver_sql(
            "SELECT trigger_row.tgname, trigger_row.tgenabled "
            "FROM pg_trigger AS trigger_row "
            "JOIN pg_class AS table_row ON table_row.oid = trigger_row.tgrelid "
            "JOIN pg_namespace AS schema_row "
            "ON schema_row.oid = table_row.relnamespace "
            "WHERE schema_row.nspname = 'public' "
            "AND trigger_row.tgname IN ("
            "'trg_audit_events_immutable_0015', "
            "'trg_audit_events_immutable_truncate_0015', "
            "'trg_audit_chain_heads_forward_only_0015', "
            "'trg_audit_chain_heads_no_truncate_0015') "
            "AND NOT trigger_row.tgisinternal",
        ).mappings().all()
        actual = {row["tgname"]: row["tgenabled"] for row in rows}
        if actual != {name: "A" for name in EXPECTED_PG_0015_TRIGGERS}:
            _fail_preflight()
        return
    rows = bind.exec_driver_sql(
        "SELECT name FROM sqlite_master WHERE type = 'trigger' "
        "AND name LIKE '%0015'"
    ).all()
    if {row[0] for row in rows} != EXPECTED_SQLITE_0015_TRIGGERS:
        _fail_preflight()


def _derive_stream_mapping(
    *,
    heads: Sequence[Any],
    events: Sequence[Any],
) -> list[dict[str, Any]]:
    try:
        normalized_events: list[dict[str, Any]] = []
        events_by_hash: dict[str, list[dict[str, Any]]] = {}
        event_ids: set[uuid.UUID] = set()
        for raw in events:
            event_id = _canonical_uuid(raw["id"])
            if event_id in event_ids:
                _fail_preflight()
            event_ids.add(event_id)
            event_hash = _require_sha256(raw["event_hash"])
            previous_hash = (
                None
                if raw["previous_hash"] is None
                else _require_sha256(raw["previous_hash"])
            )
            event = {
                "id": event_id,
                "raw_id": raw["id"],
                "actor_user_id": raw["actor_user_id"],
                "action": raw["action"],
                "aggregate_type": raw["aggregate_type"],
                "aggregate_id": raw["aggregate_id"],
                "before_jsonb": _canonical_json_document(raw["before_jsonb"]),
                "after_jsonb": _canonical_json_document(raw["after_jsonb"]),
                "request_id": raw["request_id"],
                "previous_hash": previous_hash,
                "event_hash": event_hash,
                "occurred_at": _canonical_datetime(raw["occurred_at"]),
            }
            normalized_events.append(event)
            events_by_hash.setdefault(event_hash, []).append(event)

        mapping: list[dict[str, Any]] = []
        owned_event_ids: set[uuid.UUID] = set()
        stream_keys: set[str] = set()
        total_versions = 0
        for raw_head in heads:
            stream_key = raw_head["stream_key"]
            if (
                not isinstance(stream_key, str)
                or stream_key in stream_keys
                or stream_key not in EXPECTED_HEAD_IDS
                or _canonical_uuid(raw_head["id"])
                != EXPECTED_HEAD_IDS[stream_key]
            ):
                _fail_preflight()
            stream_keys.add(stream_key)
            version = raw_head["version"]
            if (
                isinstance(version, bool)
                or not isinstance(version, int)
                or version < 0
                or version > MAX_BIGINT
            ):
                _fail_preflight()
            total_versions += version
            if (
                version > len(normalized_events)
                or total_versions > len(normalized_events)
                or total_versions > MAX_BIGINT
            ):
                _fail_preflight()
            last_event_id = (
                None
                if raw_head["last_event_id"] is None
                else _canonical_uuid(raw_head["last_event_id"])
            )
            last_hash = (
                None
                if raw_head["last_hash"] is None
                else _require_sha256(raw_head["last_hash"])
            )
            if (version == 0) != (last_event_id is None and last_hash is None):
                _fail_preflight()
            if (last_event_id is None) != (last_hash is None):
                _fail_preflight()

            expected_hash = last_hash
            visited: set[uuid.UUID] = set()
            for position in range(version):
                candidates = events_by_hash.get(expected_hash or "", [])
                if len(candidates) != 1:
                    _fail_preflight()
                event = candidates[0]
                if position == 0 and event["id"] != last_event_id:
                    _fail_preflight()
                if event["id"] in visited or event["id"] in owned_event_ids:
                    _fail_preflight()
                visited.add(event["id"])
                owned_event_ids.add(event["id"])
                if _calculate_v1_event_hash(
                    stream_key=stream_key,
                    event=event,
                ) != event["event_hash"]:
                    _fail_preflight()
                mapping.append(
                    {
                        "event_id": event["raw_id"],
                        "canonical_event_id": event["id"],
                        "stream_key": stream_key,
                        "stream_version": version - position,
                    }
                )
                expected_hash = event["previous_hash"]
            if expected_hash is not None:
                _fail_preflight()

        if stream_keys != set(EXPECTED_HEAD_IDS):
            _fail_preflight()
        if total_versions != len(normalized_events):
            _fail_preflight()
        if owned_event_ids != event_ids or len(mapping) != len(events):
            _fail_preflight()
        return mapping
    except _AuditBindingPreflightFailure:
        raise
    except Exception:
        _fail_preflight()


def _require_empty_fixed_heads(heads: Sequence[Any]) -> None:
    if len(heads) != len(EXPECTED_HEAD_IDS):
        raise ValueError("fixed head count")
    actual: set[str] = set()
    for row in heads:
        stream_key = row["stream_key"]
        if (
            stream_key in actual
            or stream_key not in EXPECTED_HEAD_IDS
            or _canonical_uuid(row["id"]) != EXPECTED_HEAD_IDS[stream_key]
            or row["version"] != 0
            or row["last_event_id"] is not None
            or row["last_hash"] is not None
        ):
            raise ValueError("nonempty or invalid fixed head")
        actual.add(stream_key)
    if actual != set(EXPECTED_HEAD_IDS):
        raise ValueError("fixed head set")


def _backfill_stream_coordinates(mapping: Sequence[dict[str, Any]]) -> None:
    if not mapping:
        return
    op.get_bind().execute(
        sa.text(
            f"UPDATE {EVENT_TABLE} SET stream_key = :stream_key, "
            "stream_version = :stream_version WHERE id = :event_id"
        ),
        list(mapping),
    )


def _verify_backfilled_coordinates(mapping: Sequence[dict[str, Any]]) -> None:
    rows = op.get_bind().exec_driver_sql(
        f"SELECT id, stream_key, stream_version FROM {EVENT_TABLE} ORDER BY id"
    ).mappings().all()
    expected = {
        row["canonical_event_id"]: (row["stream_key"], row["stream_version"])
        for row in mapping
    }
    actual: dict[uuid.UUID, tuple[str, int]] = {}
    for row in rows:
        event_id = _canonical_uuid(row["id"])
        stream_key = row["stream_key"]
        stream_version = row["stream_version"]
        if (
            event_id in actual
            or not isinstance(stream_key, str)
            or stream_key not in EXPECTED_HEAD_IDS
            or isinstance(stream_version, bool)
            or not isinstance(stream_version, int)
            or stream_version <= 0
            or stream_version > MAX_BIGINT
        ):
            raise RuntimeError(UPGRADE_BLOCKER)
        actual[event_id] = (stream_key, stream_version)
    if actual != expected:
        raise RuntimeError(UPGRADE_BLOCKER)


def _create_postgresql_constraints() -> None:
    op.alter_column(
        EVENT_TABLE,
        "stream_key",
        existing_type=sa.String(length=160),
        nullable=False,
    )
    op.alter_column(
        EVENT_TABLE,
        "stream_version",
        existing_type=sa.BigInteger(),
        nullable=False,
    )
    op.create_check_constraint(
        CHECK_STREAM_KEY,
        EVENT_TABLE,
        "stream_key IN ('authorization', 'authentication', 'inventory')",
    )
    op.create_check_constraint(
        CHECK_STREAM_VERSION,
        EVENT_TABLE,
        "stream_version > 0",
    )
    op.create_unique_constraint(
        UNIQUE_STREAM_VERSION,
        EVENT_TABLE,
        ["stream_key", "stream_version"],
    )
    op.create_foreign_key(
        FK_STREAM_HEAD,
        EVENT_TABLE,
        HEAD_TABLE,
        ["stream_key"],
        ["stream_key"],
        onupdate="RESTRICT",
        ondelete="RESTRICT",
    )


def _rebuild_sqlite_event_constraints() -> None:
    with op.batch_alter_table(EVENT_TABLE, recreate="always") as batch_op:
        batch_op.alter_column(
            "stream_key",
            existing_type=sa.String(length=160),
            nullable=False,
        )
        batch_op.alter_column(
            "stream_version",
            existing_type=sa.BigInteger(),
            nullable=False,
        )
        batch_op.create_check_constraint(
            CHECK_STREAM_KEY,
            "stream_key IN ('authorization', 'authentication', 'inventory')",
        )
        batch_op.create_check_constraint(
            CHECK_STREAM_VERSION,
            "stream_version > 0",
        )
        batch_op.create_unique_constraint(
            UNIQUE_STREAM_VERSION,
            ["stream_key", "stream_version"],
        )
        batch_op.create_foreign_key(
            FK_STREAM_HEAD,
            HEAD_TABLE,
            ["stream_key"],
            ["stream_key"],
            onupdate="RESTRICT",
            ondelete="RESTRICT",
        )


def _create_postgresql_guards() -> None:
    op.execute(
        f"""
CREATE FUNCTION {PG_HEAD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM {EVENT_TABLE} AS event
         WHERE event.id = NEW.last_event_id
           AND event.event_hash = NEW.last_hash
           AND event.stream_key = NEW.stream_key
           AND event.stream_version = NEW.version
    ) THEN
        RAISE EXCEPTION 'audit head/event stream binding is invalid'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$
"""
    )
    op.execute(
        f"CREATE TRIGGER {PG_HEAD_TRIGGER} BEFORE UPDATE ON {HEAD_TABLE} "
        f"FOR EACH ROW EXECUTE FUNCTION {PG_HEAD_FUNCTION}()"
    )
    op.execute(
        f"""
CREATE FUNCTION {PG_COMMIT_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
          FROM {HEAD_TABLE} AS head
         WHERE head.stream_key = NEW.stream_key
           AND head.version >= NEW.stream_version
    ) THEN
        RAISE EXCEPTION 'audit event was not bound to its stream before commit'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$
"""
    )
    op.execute(
        f"CREATE CONSTRAINT TRIGGER {PG_COMMIT_TRIGGER} "
        f"AFTER INSERT ON {EVENT_TABLE} DEFERRABLE INITIALLY DEFERRED "
        f"FOR EACH ROW EXECUTE FUNCTION {PG_COMMIT_FUNCTION}()"
    )
    for table_name, trigger_name in (
        (HEAD_TABLE, PG_HEAD_TRIGGER),
        (EVENT_TABLE, PG_COMMIT_TRIGGER),
    ):
        op.execute(
            f"ALTER TABLE {table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
        )


def _apply_postgresql_acl() -> None:
    for function_name in (PG_HEAD_FUNCTION, PG_COMMIT_FUNCTION):
        op.execute(
            f"REVOKE EXECUTE ON FUNCTION {function_name}() "
            f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
        )


def _drop_sqlite_0015_event_guards() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_EVENT_UPDATE_0015}")
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_EVENT_DELETE_0015}")


def _create_sqlite_0015_event_guards() -> None:
    _drop_sqlite_0015_event_guards()
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_EVENT_UPDATE_0015}
BEFORE UPDATE ON {EVENT_TABLE}
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_EVENT_DELETE_0015}
BEFORE DELETE ON {EVENT_TABLE}
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END
"""
    )


def _drop_sqlite_0015_head_guards() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_HEAD_UPDATE_0015}")
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_HEAD_DELETE_0015}")


def _create_sqlite_0015_head_guards() -> None:
    _drop_sqlite_0015_head_guards()
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_HEAD_UPDATE_0015}
BEFORE UPDATE ON {HEAD_TABLE}
WHEN NEW.id IS NOT OLD.id
  OR NEW.stream_key IS NOT OLD.stream_key
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.version <> OLD.version + 1
  OR NEW.last_event_id IS NULL
  OR NEW.last_hash IS NULL
  OR NOT EXISTS (
      SELECT 1
        FROM {EVENT_TABLE} AS event
       WHERE event.id = NEW.last_event_id
         AND event.event_hash = NEW.last_hash
         AND event.previous_hash IS OLD.last_hash
  )
BEGIN
    SELECT RAISE(ABORT, 'audit chain head must advance by one immutable event');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_HEAD_DELETE_0015}
BEFORE DELETE ON {HEAD_TABLE}
BEGIN
    SELECT RAISE(ABORT, 'audit chain heads cannot be removed');
END
"""
    )


def _create_sqlite_0017_guards() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_EVENT_INSERT_TRIGGER}")
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_HEAD_TRIGGER}")
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_EVENT_INSERT_TRIGGER}
BEFORE INSERT ON {EVENT_TABLE}
WHEN NEW.stream_key NOT IN ('authorization', 'authentication', 'inventory')
  OR NEW.stream_version <= 0
  OR NOT EXISTS (
      SELECT 1
        FROM {HEAD_TABLE} AS head
       WHERE head.stream_key = NEW.stream_key
         AND NEW.stream_version = head.version + 1
         AND NEW.previous_hash IS head.last_hash
  )
BEGIN
    SELECT RAISE(ABORT, 'audit event stream binding is invalid');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_HEAD_TRIGGER}
BEFORE UPDATE ON {HEAD_TABLE}
WHEN NOT EXISTS (
    SELECT 1
      FROM {EVENT_TABLE} AS event
     WHERE event.id = NEW.last_event_id
       AND event.event_hash = NEW.last_hash
       AND event.stream_key = NEW.stream_key
       AND event.stream_version = NEW.version
)
BEGIN
    SELECT RAISE(ABORT, 'audit head/event stream binding is invalid');
END
"""
    )


def _assert_sqlite_foreign_keys_clean() -> None:
    if op.get_bind().exec_driver_sql("PRAGMA foreign_key_check").first():
        raise RuntimeError(UPGRADE_BLOCKER)


def _calculate_v1_event_hash(
    *,
    stream_key: str,
    event: dict[str, Any],
) -> str:
    # Frozen v1 canonical document.  stream_version is structural metadata and
    # must never be added to this historical digest.
    document = {
        "action": event["action"],
        "actor_user_id": event["actor_user_id"],
        "after_jsonb": event["after_jsonb"],
        "aggregate_id": event["aggregate_id"],
        "aggregate_type": event["aggregate_type"],
        "before_jsonb": event["before_jsonb"],
        "event_id": str(event["id"]),
        "occurred_at": event["occurred_at"],
        "previous_hash": event["previous_hash"],
        "request_id": event["request_id"],
        "stream_key": stream_key,
    }
    encoded = json.dumps(
        document,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _canonical_uuid(value: Any) -> uuid.UUID:
    parsed = uuid.UUID(str(value))
    if parsed.int == 0:
        _fail_preflight()
    return parsed


def _canonical_json_document(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        _fail_preflight()
    return json.loads(
        json.dumps(
            parsed,
            ensure_ascii=False,
            allow_nan=False,
            sort_keys=True,
            separators=(",", ":"),
        )
    )


def _canonical_datetime(value: Any) -> str:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    else:
        _fail_preflight()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_sha256(value: Any) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        _fail_preflight()
    return value


def _fail_preflight() -> None:
    raise _AuditBindingPreflightFailure
