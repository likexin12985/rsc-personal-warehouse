from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import importlib.util
import io
from pathlib import Path
import uuid

from alembic import command
from alembic.config import Config
import pytest
import sqlalchemy as sa
from sqlalchemy import inspect
from sqlalchemy.orm import Session

from app.database_security import (
    DatabaseSecurityBoundaryError,
    EXPECTED_MATERIAL_REQUEST_CANCELLATION_INDEXES,
    EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS,
    FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256,
    RUNTIME_DELETE_TABLES,
    RUNTIME_INSERT_TABLES,
    RUNTIME_READ_TABLES,
    RUNTIME_UPDATE_COLUMNS,
    RUNTIME_UPDATE_TABLES,
    _assert_material_request_cancellation_guards,
)
from app.demand_models import MaterialRequest, MaterialRequestLine
from app.formal_access import load_formal_principal
from app.formal_services.material_request_lifecycle import cancel_material_request

from test_material_request_draft_service import NOW, SECRET
from test_material_request_lifecycle_service import _approved_request, _cancel_input


ROOT = Path(__file__).resolve().parents[2]
ALEMBIC_INI = ROOT / "alembic.ini"
MIGRATION = (
    ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260901_0037_material_request_cancellation_boundary.py"
)
HEAD = "20260901_0037"
PREVIOUS_HEAD = "20260901_0036"


def _config(database_url: str, *, output: io.StringIO | None = None) -> Config:
    config = Config(str(ALEMBIC_INI), output_buffer=output)
    config.set_main_option("sqlalchemy.url", database_url)
    return config


def _migration_module():
    spec = importlib.util.spec_from_file_location("migration_0037", MIGRATION)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _upgrade(database_url: str, revision: str = HEAD) -> None:
    command.upgrade(_config(database_url), revision)


def _engine(database_url: str) -> sa.Engine:
    engine = sa.create_engine(database_url, connect_args={"timeout": 0.0})

    @sa.event.listens_for(engine, "connect")
    def _enable_foreign_keys(connection, _record) -> None:
        connection.execute("PRAGMA foreign_keys=ON")

    return engine


def _prepare_approved_request(
    database_url: str,
) -> tuple[sa.Engine, str, uuid.UUID, uuid.UUID, uuid.UUID, int]:
    """Seed through current services while preserving every 0037 guard.

    Migrations seed the production role/route/audit skeleton, whereas the
    focused service fixture owns its own equivalent test skeleton.  The setup
    removes only those empty seed rows.  0036 upload/binding triggers are
    captured and restored after the legacy test fixture has inserted its
    already-available file objects; cancellation itself runs with the complete
    migrated trigger set.
    """

    engine = _engine(database_url)
    with engine.begin() as connection:
        # This historical integration fixture deliberately keeps Alembic at
        # 0037 while seeding through the current service/ORM.  Add only the
        # later nullable SQLite column needed for current command reads and
        # INSERT RETURNING; 0046 PostgreSQL ownership/check/trigger behavior is
        # covered by its isolated PG16 release gate, not emulated here.
        assert connection.exec_driver_sql(
            "SELECT version_num FROM alembic_version"
        ).scalar_one() == HEAD
        command_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info('material_request_commands')"
            ).all()
        }
        assert "projection_manifest_sha256" not in command_columns
        connection.exec_driver_sql(
            "ALTER TABLE material_request_commands ADD COLUMN "
            "projection_manifest_sha256 VARCHAR(64) NULL"
        )

        audit_head_triggers = connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name='audit_chain_heads' ORDER BY name"
        ).all()
        for name, _sql in audit_head_triggers:
            connection.exec_driver_sql(f"DROP TRIGGER {name}")
        for table_name in (
            "approval_route_step_defs",
            "approval_route_versions",
            "role_permissions",
            "permissions",
            "roles",
            "audit_chain_heads",
        ):
            connection.exec_driver_sql(f"DELETE FROM {table_name}")
        for _name, sql in audit_head_triggers:
            connection.exec_driver_sql(sql)

        formal_file_triggers = connection.exec_driver_sql(
            "SELECT name, sql FROM sqlite_master WHERE type='trigger' "
            "AND tbl_name IN ('files','material_request_files',"
            "'approval_external_registrations') AND name LIKE '%0036' "
            "ORDER BY name"
        ).all()
        for name, _sql in formal_file_triggers:
            connection.exec_driver_sql(f"DROP TRIGGER {name}")

    with Session(engine) as session:
        world, request, line, version = _approved_request(
            session, key=f"migration-0037-{uuid.uuid4().hex[:10]}"
        )
        actor_user_id = world.actor_user.id
        revision_id = line.revision_id
        request_id = request.id
        line_id = line.id
        session.commit()
        command_manifests = session.execute(
            sa.text(
                "SELECT projection_manifest_sha256 "
                "FROM material_request_commands ORDER BY target_version"
            )
        ).scalars().all()
        assert command_manifests and set(command_manifests) == {None}

    with engine.begin() as connection:
        for _name, sql in formal_file_triggers:
            connection.exec_driver_sql(sql)
    return engine, actor_user_id, request_id, revision_id, line_id, version


def test_0037_schema_preflight_and_downgrade_are_fail_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0037-schema.db'}"
    _upgrade(database_url)
    engine = _engine(database_url)
    try:
        inspector = inspect(engine)
        assert "material_request_cancellation_line_facts" in inspector.get_table_names()
        action_indexes = {
            row["name"]: row for row in inspector.get_indexes("approval_actions")
        }
        assert action_indexes[
            "uq_approval_actions_cancel_fact_identity_0037"
        ]["column_names"] == ["id", "instance_id", "command_id"]
        assert action_indexes[
            "uq_approval_actions_cancel_fact_identity_0037"
        ]["unique"]
        fact_uniques = {
            row["name"]: tuple(row["column_names"])
            for row in inspector.get_unique_constraints(
                "material_request_cancellation_line_facts"
            )
        }
        assert fact_uniques == {
            "uq_material_request_cancel_facts_action_line_0037": (
                "cancel_action_id",
                "request_line_id",
            ),
            "uq_material_request_cancel_facts_command_line_0037": (
                "cancel_command_id",
                "request_line_id",
            ),
            "uq_material_request_cancel_facts_request_line_0037": (
                "request_line_id",
            ),
        }
        with engine.connect() as connection:
            trigger_names = {
                row[0]
                for row in connection.exec_driver_sql(
                    "SELECT name FROM sqlite_master WHERE type='trigger' "
                    "AND name LIKE '%0037'"
                )
            }
        assert trigger_names == set(_migration_module()._sqlite_trigger_names())
    finally:
        engine.dispose()

    command.downgrade(_config(database_url), PREVIOUS_HEAD)
    downgraded = _engine(database_url)
    try:
        with downgraded.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PREVIOUS_HEAD
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE name LIKE '%0037'"
            ).scalar_one() == 0
    finally:
        downgraded.dispose()

    preflight_url = f"sqlite+pysqlite:///{tmp_path / '0037-preflight.db'}"
    _upgrade(preflight_url, PREVIOUS_HEAD)
    preflight_engine = _engine(preflight_url)
    now = datetime.now(timezone.utc).isoformat()
    try:
        with preflight_engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO state_transition_events "
                "(id,aggregate_type,aggregate_id,from_status,to_status,reason,"
                "actor_id,idempotency_key,occurred_at,metadata_jsonb,created_at) "
                "VALUES (?, 'material_request', ?, 'approved', 'cancelled', "
                "'legacy cancellation', NULL, ?, ?, '{}', ?)",
                (uuid.uuid4().hex, str(uuid.uuid4()), str(uuid.uuid4()), now, now),
            )
    finally:
        preflight_engine.dispose()
    with pytest.raises(RuntimeError, match="0037 preflight failed"):
        _upgrade(preflight_url)
    verification = _engine(preflight_url)
    try:
        with verification.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar_one() == PREVIOUS_HEAD
            assert connection.exec_driver_sql(
                "SELECT count(*) FROM sqlite_master WHERE name LIKE '%0037'"
            ).scalar_one() == 0
    finally:
        verification.dispose()


def test_0037_postgresql_offline_sql_acl_functions_and_manifest_match(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    output = io.StringIO()
    command.upgrade(
        _config(
            "postgresql+psycopg://offline:offline@localhost/offline",
            output=output,
        ),
        f"{PREVIOUS_HEAD}:{HEAD}",
        sql=True,
    )
    sql = output.getvalue()
    migration = _migration_module()
    assert migration.down_revision == PREVIOUS_HEAD
    assert "DEFERRABLE INITIALLY DEFERRED" in sql
    assert sql.count("coordinate_match_count > 1") == 2
    assert sql.count("count(DISTINCT candidate.request_id)") == 2
    assert "is_uuid_coordinate" in sql
    assert "replace(lower(target_coordinate), '-', '')" in sql
    for table_name in (
        "inventory_transactions",
        "notification_events",
        "outbox_events",
    ):
        assert f"trg_{table_name}_request_parent_lock_0037" in sql
        assert f"trg_{table_name}_cancellation_graph_0037" in sql
    for function_name, function_sql, argument_types in (
        (migration.PG_VALIDATE_FUNCTION, migration._postgresql_validator_sql(), "uuid"),
        (migration.PG_DISPATCH_FUNCTION, migration._postgresql_dispatch_sql(), ""),
        (migration.PG_FACT_GUARD_FUNCTION, migration._postgresql_fact_guard_sql(), ""),
        (migration.PG_ACTION_GUARD_FUNCTION, migration._postgresql_action_guard_sql(), ""),
        (migration.PG_PARENT_LOCK_FUNCTION, migration._postgresql_parent_lock_sql(), ""),
    ):
        body = function_sql.split("AS $$", 1)[1].rsplit("$$", 1)[0]
        coordinate = (function_name, argument_types)
        assert hashlib.sha256(body.encode("utf-8")).hexdigest() == (
            FORMAL_FILE_INTERNAL_FUNCTION_BODY_SHA256[coordinate]
        )
        assert f"REVOKE EXECUTE ON FUNCTION public.{function_name}" in sql
    assert "GRANT UPDATE ON TABLE public.material_requests" not in sql
    assert "GRANT UPDATE ON TABLE public.material_request_lines" not in sql
    assert "GRANT UPDATE ON TABLE public.material_request_cancellation_line_facts" not in sql
    assert "GRANT DELETE ON TABLE public.material_request_cancellation_line_facts" not in sql
    assert set(migration.READ_TABLES) <= RUNTIME_READ_TABLES
    assert set(migration.INSERT_TABLES) <= RUNTIME_INSERT_TABLES
    assert set(migration.DELETE_TABLES) <= RUNTIME_DELETE_TABLES
    assert "material_request_cancellation_line_facts" not in RUNTIME_UPDATE_TABLES
    assert "material_request_cancellation_line_facts" not in RUNTIME_DELETE_TABLES
    assert {
        name: frozenset(columns)
        for name, columns in migration.UPDATE_COLUMNS.items()
    } == {
        name: RUNTIME_UPDATE_COLUMNS[name] for name in migration.UPDATE_COLUMNS
    }
    assert set(migration._postgresql_triggers()) == set(
        EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS
    )
    assert set(EXPECTED_MATERIAL_REQUEST_CANCELLATION_INDEXES) == {
        migration.ACTION_IDENTITY_INDEX,
        "ix_material_request_cancel_facts_instance_0037",
        "ix_material_request_cancel_facts_request_0037",
        "ix_material_request_cancel_facts_command_0037",
    }


def test_0037_database_security_catalog_manifest_rejects_guard_drift() -> None:
    triggers = []
    for name, (table_name, function_name, enabled, trigger_type) in sorted(
        EXPECTED_MATERIAL_REQUEST_CANCELLATION_TRIGGERS.items()
    ):
        is_deferred = function_name == (
            "rsc_require_material_request_cancellation_graph_0037"
        )
        triggers.append(
            {
                "trigger_name": name,
                "table_name": table_name,
                "function_schema": "public",
                "function_name": function_name,
                "enabled": enabled,
                "trigger_type": trigger_type,
                "is_constraint_trigger": is_deferred,
                "is_deferrable": is_deferred,
                "is_initially_deferred": is_deferred,
                "has_when_clause": False,
                "has_column_filter": False,
            }
        )
    indexes = [
        {
            "index_name": name,
            "table_name": expected["table"],
            "access_method": "btree",
            "is_unique": expected["unique"],
            "is_valid": True,
            "is_ready": True,
            "is_live": True,
            "key_columns": list(expected["columns"]),
            "predicate": None,
        }
        for name, expected in sorted(
            EXPECTED_MATERIAL_REQUEST_CANCELLATION_INDEXES.items()
        )
    ]
    _assert_material_request_cancellation_guards(
        triggers=triggers, indexes=indexes
    )

    drifted = [dict(row) for row in triggers]
    drifted[0]["enabled"] = "O"
    with pytest.raises(DatabaseSecurityBoundaryError, match="cancellation guard"):
        _assert_material_request_cancellation_guards(
            triggers=drifted, indexes=indexes
        )


def test_0037_sqlite_service_direct_guard_immutable_facts_and_downstream_race(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("OAM_DATABASE_URL", raising=False)
    database_url = f"sqlite+pysqlite:///{tmp_path / '0037-runtime.db'}"
    _upgrade(database_url)
    (
        engine,
        actor_user_id,
        request_id,
        revision_id,
        line_id,
        version,
    ) = _prepare_approved_request(database_url)
    try:
        # A terminal projection without its command/action/fact graph is never
        # accepted, even when every normal SQL CHECK would otherwise pass.
        with pytest.raises(sa.exc.DatabaseError, match="cancellation graph"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE material_requests SET status='cancelled', "
                    "cancelled_at=?, version=version+1, updated_at=? WHERE id=?",
                    (NOW.isoformat(), NOW.isoformat(), request_id.hex),
                )

        connection_one = engine.connect()
        connection_two = engine.connect()
        try:
            connection_one.exec_driver_sql("BEGIN IMMEDIATE")
            session_one = Session(
                bind=connection_one,
                join_transaction_mode="control_fully",
            )
            request = session_one.get(MaterialRequest, request_id)
            line = session_one.get(MaterialRequestLine, line_id)
            assert request is not None and line is not None
            actor = load_formal_principal(session_one, actor_user_id, now=NOW)
            result = cancel_material_request(
                session_one,
                actor=actor,
                material_request_id=request.id,
                expected_version=version,
                cancellation=_cancel_input(line),
                idempotency_key="migration-0037-cancel-idempotency",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-migration-0037-cancel",
            )
            assert result.request_status == "cancelled"

            # A second writer cannot cross the exact parent seam after cancel
            # preflight and before its commit.
            with pytest.raises(sa.exc.OperationalError, match="locked"):
                connection_two.exec_driver_sql(
                    "INSERT INTO notification_events "
                    "(id,event_type,business_type,business_id,dedup_key,"
                    "payload_jsonb,status,occurred_at,created_at) "
                    "VALUES (?, 'race', 'material_request', ?, ?, '{}', "
                    "'pending', ?, ?)",
                    (
                        uuid.uuid4().hex,
                        str(request_id),
                        f"race-{uuid.uuid4()}",
                        NOW.isoformat(),
                        NOW.isoformat(),
                    ),
                )
            connection_two.rollback()
            session_one.commit()
        finally:
            connection_two.close()
            connection_one.close()

        # Every recognized downstream coordinate is rejected after the parent
        # becomes cancelled; unrelated business types remain outside this guard.
        downstream = (
            (
                "INSERT INTO inventory_transactions "
                "(id,transaction_no,movement_type,source_document_type,"
                "source_document_id,posting_key,idempotency_key_hash,request_hash,"
                "status,effective_at,posted_at,ledger_cursor,reversed_transaction_id,"
                "actor_user_id,created_at) VALUES (?,?,'reserve','material_request',"
                "?,?,?,?,'posted',?,?,?,NULL,?,?)",
                (
                    uuid.uuid4().hex,
                    f"TX-{uuid.uuid4().hex[:12]}",
                    request_id.hex.upper(),
                    f"posting-{uuid.uuid4()}",
                    "a" * 64,
                    "b" * 64,
                    NOW.isoformat(),
                    NOW.isoformat(),
                    9_000_000_001,
                    actor_user_id,
                    NOW.isoformat(),
                ),
            ),
            (
                "INSERT INTO notification_events "
                "(id,event_type,business_type,business_id,dedup_key,payload_jsonb,"
                "status,occurred_at,created_at) VALUES (?, 'blocked', "
                "'material_request', ?, ?, '{}', 'pending', ?, ?)",
                (
                    uuid.uuid4().hex,
                    str(revision_id),
                    f"notification-{uuid.uuid4()}",
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            ),
            (
                "INSERT INTO outbox_events "
                "(id,event_type,aggregate_type,aggregate_id,payload_jsonb,status,"
                "attempts,idempotency_key,available_at,locked_at,locked_by,"
                "published_at,last_error,created_at,updated_at) VALUES "
                "(?,'blocked','material_request',?,'{}','pending',0,?,?,NULL,NULL,"
                "NULL,NULL,?,?)",
                (
                    uuid.uuid4().hex,
                    str(line_id),
                    f"outbox-{uuid.uuid4()}",
                    NOW.isoformat(),
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            ),
        )
        for statement, parameters in downstream:
            with pytest.raises(sa.exc.DatabaseError, match="cancellation graph"):
                with engine.begin() as connection:
                    connection.exec_driver_sql(statement, parameters)

        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO notification_events "
                "(id,event_type,business_type,business_id,dedup_key,payload_jsonb,"
                "status,occurred_at,created_at) VALUES (?, 'unrelated', "
                "'other_business', ?, ?, '{}', 'pending', ?, ?)",
                (
                    uuid.uuid4().hex,
                    str(request_id),
                    f"unrelated-{uuid.uuid4()}",
                    NOW.isoformat(),
                    NOW.isoformat(),
                ),
            )

        with pytest.raises(sa.exc.DatabaseError, match="cancellation graph"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "UPDATE material_request_cancellation_line_facts "
                    "SET reason='tampered' WHERE request_id=?",
                    (request_id.hex,),
                )
        with pytest.raises(sa.exc.DatabaseError, match="cancellation graph"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "DELETE FROM material_request_cancellation_line_facts "
                    "WHERE request_id=?",
                    (request_id.hex,),
                )

        # Deliberately create a cross-kind coordinate collision.  The resolver
        # must fail closed instead of selecting an arbitrary parent with LIMIT 1.
        collision_id = uuid.uuid4().hex
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO material_requests "
                "(id,request_no,requester_user_id,requester_person_id,requester_org_id,"
                "work_order_id,purpose,urgency,expected_date,address_snapshot_jsonb,"
                "address_masked_jsonb,contact_snapshot_jsonb,contact_masked_jsonb,note,"
                "approval_mode,status,revision_no,version,allocation_status,"
                "reservation_status,outbound_status,shipment_status,"
                "logistics_signature_status,oam_receipt_status,personal_inbound_status,"
                "notification_status,reconciliation_status,submitted_at,decided_at,"
                "withdrawn_at,cancelled_at,created_by_user_id,updated_at,created_at) "
                "SELECT ?, ?, requester_user_id,requester_person_id,requester_org_id,"
                "work_order_id,purpose,urgency,expected_date,address_snapshot_jsonb,"
                "address_masked_jsonb,contact_snapshot_jsonb,contact_masked_jsonb,note,"
                "approval_mode,'draft',1,0,'not_allocated','not_reserved','not_started',"
                "'not_started','not_signed','not_occurred','not_started','not_started',"
                "'not_started',NULL,NULL,NULL,NULL,created_by_user_id,?,? "
                "FROM material_requests WHERE id=?",
                (
                    collision_id,
                    str(request_id),
                    NOW.isoformat(),
                    NOW.isoformat(),
                    request_id.hex,
                ),
            )
        with pytest.raises(sa.exc.DatabaseError, match="cancellation graph"):
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "INSERT INTO notification_events "
                    "(id,event_type,business_type,business_id,dedup_key,payload_jsonb,"
                    "status,occurred_at,created_at) VALUES (?, 'ambiguous', "
                    "'material_request', ?, ?, '{}', 'pending', ?, ?)",
                    (
                        uuid.uuid4().hex,
                        str(request_id),
                        f"ambiguous-{uuid.uuid4()}",
                        NOW.isoformat(),
                        NOW.isoformat(),
                    ),
                )

        with pytest.raises(RuntimeError, match="cannot downgrade 0037"):
            command.downgrade(_config(database_url), PREVIOUS_HEAD)
    finally:
        engine.dispose()
