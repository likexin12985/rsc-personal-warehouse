"""Validate and make persisted audit events append-only.

Revision ID: 20260831_0015
Revises: 20260831_0014
Create Date: 2026-08-31

The chain head remains mutable because an append must advance it atomically;
individual audit events may only be inserted.  Before that boundary is
installed, an online upgrade proves that every existing event belongs to one
and only one complete, canonically hashed stream.  An offline PostgreSQL script
therefore refuses a populated audit table: it cannot reproduce the Python
canonical-JSON hash safely.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Sequence, Union

from alembic import context, op


revision: str = "20260831_0015"
down_revision: Union[str, Sequence[str], None] = "20260831_0014"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "audit_events"
HEAD_TABLE_NAME = "audit_chain_heads"
FUNCTION_NAME = "rsc_reject_audit_event_mutation_0015"
PG_ROW_TRIGGER_NAME = "trg_audit_events_immutable_0015"
PG_TRUNCATE_TRIGGER_NAME = "trg_audit_events_immutable_truncate_0015"
SQLITE_UPDATE_TRIGGER = "trg_audit_events_immutable_update_0015"
SQLITE_DELETE_TRIGGER = "trg_audit_events_immutable_delete_0015"
HEAD_FUNCTION_NAME = "rsc_validate_audit_chain_head_mutation_0015"
PG_HEAD_ROW_TRIGGER_NAME = "trg_audit_chain_heads_forward_only_0015"
PG_HEAD_TRUNCATE_TRIGGER_NAME = "trg_audit_chain_heads_no_truncate_0015"
SQLITE_HEAD_UPDATE_TRIGGER = "trg_audit_chain_heads_forward_only_0015"
SQLITE_HEAD_DELETE_TRIGGER = "trg_audit_chain_heads_no_delete_0015"
PRODUCTION_API_ROLE = "star_oam_api"
EXPECTED_HEAD_IDS = {
    "authorization": uuid.UUID("30000000-0000-4000-8000-000000000001"),
    "authentication": uuid.UUID("30000000-0000-4000-8000-000000000002"),
    "inventory": uuid.UUID("30000000-0000-4000-8000-000000000003"),
}
API_READ_TABLES = (
    "audit_chain_heads",
    "audit_events",
    "auth_identities",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "custody_assignments",
    "inventory_ledger_heads",
    "inventory_transactions",
    "login_challenges",
    "organizations",
    "people",
    "permissions",
    "role_assignments",
    "role_permissions",
    "roles",
    "state_transition_events",
    "stock_accounts",
    "stock_locations",
    "users",
)
API_INSERT_TABLES = (
    "audit_events",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "login_challenges",
    "role_assignments",
    "state_transition_events",
)
API_UPDATE_TABLES = (
    "audit_chain_heads",
    "auth_idempotency_operations",
    "auth_login_rate_limit_buckets",
    "auth_refresh_tokens",
    "auth_sessions",
    "login_challenges",
    "role_assignments",
    "users",
)
API_DELETE_TABLES = ("auth_login_rate_limit_buckets",)
SHA256_PATTERN = re.compile(r"^[0-9a-f]{64}$")
UPGRADE_BLOCKER = (
    "0015 preflight failed: existing audit chain evidence is incomplete or "
    "inconsistent"
)
OFFLINE_UPGRADE_BLOCKER = (
    "0015 populated audit chains require an online canonical-hash preflight"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0015: persisted audit events require database "
    "immutability"
)


class _AuditPreflightFailure(Exception):
    """Internal sentinel; callers receive only the fixed upgrade blocker."""


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0015 supports only PostgreSQL production and SQLite local test schemas"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        if context.is_offline_mode():
            raise RuntimeError("0015 SQLite upgrade requires an online connection")
        _ensure_sqlite_migration_transaction()
    _assert_existing_audit_chains_are_complete(dialect)

    if dialect == "postgresql":
        op.execute(
            f"""
CREATE FUNCTION {FUNCTION_NAME}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'audit events are immutable' USING ERRCODE = '55000';
END;
$$
"""
        )
        op.execute(
            f"CREATE TRIGGER {PG_ROW_TRIGGER_NAME} "
            f"BEFORE UPDATE OR DELETE ON {TABLE_NAME} FOR EACH ROW "
            f"EXECUTE FUNCTION {FUNCTION_NAME}()"
        )
        op.execute(
            f"CREATE TRIGGER {PG_TRUNCATE_TRIGGER_NAME} "
            f"BEFORE TRUNCATE ON {TABLE_NAME} FOR EACH STATEMENT "
            f"EXECUTE FUNCTION {FUNCTION_NAME}()"
        )
        _create_postgresql_chain_head_guards()
        for table_name, trigger_name in (
            (TABLE_NAME, PG_ROW_TRIGGER_NAME),
            (TABLE_NAME, PG_TRUNCATE_TRIGGER_NAME),
            (HEAD_TABLE_NAME, PG_HEAD_ROW_TRIGGER_NAME),
            (HEAD_TABLE_NAME, PG_HEAD_TRUNCATE_TRIGGER_NAME),
        ):
            op.execute(
                f"ALTER TABLE {table_name} ENABLE ALWAYS TRIGGER {trigger_name}"
            )
        _apply_postgresql_runtime_acl()
        return

    # Drop both canonical names inside the same BEGIN IMMEDIATE transaction.
    # This safely resumes the only possible old partial shape (one trigger was
    # created while the Alembic version remained at 0014).
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_UPDATE_TRIGGER}")
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_DELETE_TRIGGER}")
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_UPDATE_TRIGGER}
BEFORE UPDATE ON {TABLE_NAME}
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_DELETE_TRIGGER}
BEFORE DELETE ON {TABLE_NAME}
BEGIN
    SELECT RAISE(ABORT, 'audit events are immutable');
END
"""
    )
    _create_sqlite_chain_head_guards()


def _ensure_sqlite_migration_transaction() -> None:
    """Put SQLite preflight and every DDL statement in one write transaction."""

    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _apply_postgresql_runtime_acl() -> None:
    """Install the exact table ACL for currently mounted production routes."""

    op.execute(
        f"""
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_roles WHERE rolname = '{PRODUCTION_API_ROLE}'
    ) THEN
        RAISE EXCEPTION
            '0015 requires the provisioned star_oam_api runtime role';
    END IF;
END $$
"""
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    _revoke_postgresql_runtime_column_acl()
    op.execute(
        f"REVOKE EXECUTE ON ALL FUNCTIONS IN SCHEMA public "
        f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
    )
    _grant_table_privileges("SELECT", API_READ_TABLES)
    _grant_table_privileges("INSERT", API_INSERT_TABLES)
    _grant_table_privileges("UPDATE", API_UPDATE_TABLES)
    _grant_table_privileges("DELETE", API_DELETE_TABLES)
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {FUNCTION_NAME}() FROM PUBLIC"
    )
    op.execute(
        f"REVOKE ALL PRIVILEGES ON FUNCTION {HEAD_FUNCTION_NAME}() FROM PUBLIC"
    )


def _grant_table_privileges(privileges: str, table_names: Sequence[str]) -> None:
    if not table_names:
        return
    op.execute(
        f"GRANT {privileges} ON TABLE {', '.join(table_names)} "
        f"TO {PRODUCTION_API_ROLE}"
    )


def _revoke_postgresql_runtime_column_acl() -> None:
    """Remove historical column grants that table-level REVOKE does not clear."""

    op.execute(
        f"""
DO $$
DECLARE
    column_row record;
BEGIN
    FOR column_row IN
        SELECT class_row.relname, attribute_row.attname
          FROM pg_class AS class_row
          JOIN pg_namespace AS namespace_row
            ON namespace_row.oid = class_row.relnamespace
          JOIN pg_attribute AS attribute_row
            ON attribute_row.attrelid = class_row.oid
         WHERE namespace_row.nspname = 'public'
           AND class_row.relkind IN ('r', 'p', 'v', 'm', 'f')
           AND attribute_row.attnum > 0
           AND NOT attribute_row.attisdropped
         ORDER BY class_row.relname, attribute_row.attnum
    LOOP
        EXECUTE format(
            'REVOKE SELECT (%1$I), INSERT (%1$I), UPDATE (%1$I), '
            'REFERENCES (%1$I) ON TABLE public.%2$I FROM PUBLIC, '
            '{PRODUCTION_API_ROLE}',
            column_row.attname,
            column_row.relname
        );
    END LOOP;
END $$
"""
    )


def _create_postgresql_chain_head_guards() -> None:
    op.execute(
        f"""
CREATE FUNCTION {HEAD_FUNCTION_NAME}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
        RAISE EXCEPTION 'audit chain heads cannot be removed'
            USING ERRCODE = '55000';
    END IF;
    IF NEW.id IS DISTINCT FROM OLD.id
       OR NEW.stream_key IS DISTINCT FROM OLD.stream_key
       OR NEW.created_at IS DISTINCT FROM OLD.created_at
       OR NEW.version <> OLD.version + 1
       OR NEW.last_event_id IS NULL
       OR NEW.last_hash IS NULL
       OR NOT EXISTS (
           SELECT 1
             FROM {TABLE_NAME} AS event
            WHERE event.id = NEW.last_event_id
              AND event.event_hash = NEW.last_hash
              AND event.previous_hash IS NOT DISTINCT FROM OLD.last_hash
       ) THEN
        RAISE EXCEPTION 'audit chain head must advance by one immutable event'
            USING ERRCODE = '55000';
    END IF;
    RETURN NEW;
END;
$$
"""
    )
    op.execute(
        f"CREATE TRIGGER {PG_HEAD_ROW_TRIGGER_NAME} "
        f"BEFORE UPDATE OR DELETE ON {HEAD_TABLE_NAME} FOR EACH ROW "
        f"EXECUTE FUNCTION {HEAD_FUNCTION_NAME}()"
    )
    op.execute(
        f"CREATE TRIGGER {PG_HEAD_TRUNCATE_TRIGGER_NAME} "
        f"BEFORE TRUNCATE ON {HEAD_TABLE_NAME} FOR EACH STATEMENT "
        f"EXECUTE FUNCTION {HEAD_FUNCTION_NAME}()"
    )


def _create_sqlite_chain_head_guards() -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_HEAD_UPDATE_TRIGGER}")
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_HEAD_DELETE_TRIGGER}")
    op.execute(
        f"""
CREATE TRIGGER {SQLITE_HEAD_UPDATE_TRIGGER}
BEFORE UPDATE ON {HEAD_TABLE_NAME}
WHEN NEW.id IS NOT OLD.id
  OR NEW.stream_key IS NOT OLD.stream_key
  OR NEW.created_at IS NOT OLD.created_at
  OR NEW.version <> OLD.version + 1
  OR NEW.last_event_id IS NULL
  OR NEW.last_hash IS NULL
  OR NOT EXISTS (
      SELECT 1
        FROM {TABLE_NAME} AS event
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
CREATE TRIGGER {SQLITE_HEAD_DELETE_TRIGGER}
BEFORE DELETE ON {HEAD_TABLE_NAME}
BEGIN
    SELECT RAISE(ABORT, 'audit chain heads cannot be removed');
END
"""
    )


def _assert_existing_audit_chains_are_complete(dialect: str) -> None:
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0015 SQLite upgrade requires an online connection")
        op.execute(
            f"LOCK TABLE {TABLE_NAME}, {HEAD_TABLE_NAME} "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
        # Offline SQL cannot safely duplicate the application's exact
        # canonical JSON serializer.  Refuse to freeze any existing evidence;
        # operators must run the normal online Alembic migration instead.
        op.execute(
            f"""
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM {TABLE_NAME}) THEN
        RAISE EXCEPTION
            '{OFFLINE_UPGRADE_BLOCKER}';
    ELSIF (SELECT count(*) FROM {HEAD_TABLE_NAME}) <> 3
       OR EXISTS (
           SELECT 1
             FROM (VALUES
                 ('30000000-0000-4000-8000-000000000001'::uuid, 'authorization'),
                 ('30000000-0000-4000-8000-000000000002'::uuid, 'authentication'),
                 ('30000000-0000-4000-8000-000000000003'::uuid, 'inventory')
             ) AS expected(id, stream_key)
             LEFT JOIN {HEAD_TABLE_NAME} AS head
               ON head.id = expected.id
              AND head.stream_key = expected.stream_key
            WHERE head.id IS NULL
               OR head.version <> 0
               OR head.last_event_id IS NOT NULL
               OR head.last_hash IS NOT NULL
       ) THEN
        RAISE EXCEPTION '{UPGRADE_BLOCKER}';
    END IF;
END $$
"""
        )
        return

    bind = op.get_bind()
    if dialect == "postgresql":
        bind.exec_driver_sql(
            f"LOCK TABLE {TABLE_NAME}, {HEAD_TABLE_NAME} "
            "IN SHARE ROW EXCLUSIVE MODE"
        )

    try:
        heads = bind.exec_driver_sql(
            "SELECT id, stream_key, last_event_id, last_hash, version "
            f"FROM {HEAD_TABLE_NAME} ORDER BY stream_key"
        ).mappings().all()
        events = bind.exec_driver_sql(
            "SELECT id, actor_user_id, action, aggregate_type, aggregate_id, "
            "before_jsonb, after_jsonb, request_id, previous_hash, event_hash, "
            f"occurred_at FROM {TABLE_NAME} ORDER BY id"
        ).mappings().all()
        _verify_complete_audit_graph(heads=heads, events=events)
    except Exception:
        raise RuntimeError(UPGRADE_BLOCKER) from None


def _verify_complete_audit_graph(
    *,
    heads: Sequence[Any],
    events: Sequence[Any],
) -> None:
    events_by_hash: dict[str, list[dict[str, Any]]] = {}
    event_ids: set[uuid.UUID] = set()
    normalized_events: list[dict[str, Any]] = []
    try:
        for raw in events:
            event_id = _canonical_uuid(raw["id"])
            if event_id in event_ids:
                _fail_upgrade()
            event_ids.add(event_id)
            event_hash = _require_sha256(raw["event_hash"])
            previous_hash = (
                None
                if raw["previous_hash"] is None
                else _require_sha256(raw["previous_hash"])
            )
            event = {
                "id": event_id,
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

        owned_event_ids: set[uuid.UUID] = set()
        total_head_versions = 0
        stream_keys: set[str] = set()
        for raw_head in heads:
            stream_key = raw_head["stream_key"]
            if (
                not isinstance(stream_key, str)
                or not stream_key
                or stream_key in stream_keys
            ):
                _fail_upgrade()
            stream_keys.add(stream_key)
            expected_head_id = EXPECTED_HEAD_IDS.get(stream_key)
            if (
                expected_head_id is None
                or _canonical_uuid(raw_head["id"]) != expected_head_id
            ):
                _fail_upgrade()
            version = raw_head["version"]
            if (
                isinstance(version, bool)
                or not isinstance(version, int)
                or version < 0
            ):
                _fail_upgrade()
            total_head_versions += version
            if (
                version > len(normalized_events)
                or total_head_versions > len(normalized_events)
            ):
                _fail_upgrade()
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
            has_last_event = last_event_id is not None
            if has_last_event != (last_hash is not None):
                _fail_upgrade()
            if (version == 0) != (not has_last_event):
                _fail_upgrade()

            expected_hash = last_hash
            visited_in_stream: set[uuid.UUID] = set()
            for position in range(version):
                candidates = events_by_hash.get(expected_hash or "", [])
                if len(candidates) != 1:
                    _fail_upgrade()
                event = candidates[0]
                if position == 0 and event["id"] != last_event_id:
                    _fail_upgrade()
                if event["id"] in visited_in_stream:
                    _fail_upgrade()
                if event["id"] in owned_event_ids:
                    _fail_upgrade()
                visited_in_stream.add(event["id"])
                owned_event_ids.add(event["id"])
                if _calculate_event_hash(
                    stream_key=stream_key,
                    event=event,
                ) != event["event_hash"]:
                    _fail_upgrade()
                expected_hash = event["previous_hash"]
            if expected_hash is not None:
                _fail_upgrade()

        if total_head_versions != len(normalized_events):
            _fail_upgrade()
        if stream_keys != set(EXPECTED_HEAD_IDS):
            _fail_upgrade()
        if owned_event_ids != event_ids:
            _fail_upgrade()
    except _AuditPreflightFailure:
        raise
    except Exception:
        _fail_upgrade()


def _calculate_event_hash(*, stream_key: str, event: dict[str, Any]) -> str:
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
        _fail_upgrade()
    return parsed


def _canonical_json_document(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    parsed = json.loads(value) if isinstance(value, str) else value
    if not isinstance(parsed, dict):
        _fail_upgrade()
    # Round-trip with the same constraints as the runtime writer.  This also
    # rejects NaN and values that cannot be represented as canonical JSON.
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
        _fail_upgrade()
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    else:
        parsed = parsed.astimezone(timezone.utc)
    return parsed.isoformat(timespec="microseconds").replace("+00:00", "Z")


def _require_sha256(value: Any) -> str:
    if not isinstance(value, str) or SHA256_PATTERN.fullmatch(value) is None:
        _fail_upgrade()
    return value


def _fail_upgrade() -> None:
    raise _AuditPreflightFailure


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0015 downgrade requires an online connection for fail-closed "
            "audit evidence checks"
        )
    dialect = _dialect_name()
    bind = op.get_bind()
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        bind.exec_driver_sql("LOCK TABLE audit_events IN ACCESS EXCLUSIVE MODE")
    if bind.exec_driver_sql(
        "SELECT 1 FROM audit_events LIMIT 1"
    ).first() is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)

    if dialect == "postgresql":
        op.execute(
            f"DROP TRIGGER {PG_HEAD_TRUNCATE_TRIGGER_NAME} "
            f"ON {HEAD_TABLE_NAME}"
        )
        op.execute(
            f"DROP TRIGGER {PG_HEAD_ROW_TRIGGER_NAME} ON {HEAD_TABLE_NAME}"
        )
        op.execute(f"DROP FUNCTION {HEAD_FUNCTION_NAME}()")
        op.execute(f"DROP TRIGGER {PG_TRUNCATE_TRIGGER_NAME} ON {TABLE_NAME}")
        op.execute(f"DROP TRIGGER {PG_ROW_TRIGGER_NAME} ON {TABLE_NAME}")
        op.execute(f"DROP FUNCTION {FUNCTION_NAME}()")
        return
    # IF EXISTS makes an old interrupted, empty-table downgrade retryable.
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_HEAD_UPDATE_TRIGGER}")
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_HEAD_DELETE_TRIGGER}")
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_UPDATE_TRIGGER}")
    op.execute(f"DROP TRIGGER IF EXISTS {SQLITE_DELETE_TRIGGER}")
