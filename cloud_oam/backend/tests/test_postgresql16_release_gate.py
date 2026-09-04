"""Opt-in destructive tests for one GitHub-hosted PostgreSQL 16 service.

The ordinary backend suite never opens PostgreSQL.  This module runs only when
the exact acknowledgement, GitHub-hosted runner markers, loopback-only
coordinates, and a fresh whole cluster are all present.  Its target must be the
empty ``rsc_pg16_release_gate`` database created by the private-repository CI
service container.  No local, production, or shared database is acceptable.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import uuid

import psycopg
from psycopg import sql
import pytest
from sqlalchemy import URL, create_engine, event, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from pg16_release_gate_diagnostics import (
    SanitizedPostgreSQLDiagnosticError,
    run_with_sanitized_database_diagnostics,
)


CLOUD_ROOT = Path(__file__).resolve().parents[2]
ACKNOWLEDGEMENT = "I_UNDERSTAND_THIS_DATABASE_IS_EPHEMERAL"
DATABASE_NAME = "rsc_pg16_release_gate"
RLS_REVISION = "20260902_0044"
APPROVAL_REVISION = "20260903_0045"
CONTENT_CAUSALITY_REVISION = "20260903_0046"
STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION = "20260903_0048"
STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION = "20260903_0049"
STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION = "20260903_0050"
STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION = "20260903_0051"
OPENING_TERMINAL_GUARD_EXECUTION_REVISION = "20260903_0052"
HEAD_REVISION = OPENING_TERMINAL_GUARD_EXECUTION_REVISION
OPENING_BACKFILL_DATABASE_PREFIX = f"{DATABASE_NAME}_0052_backfill_"
RLS_BINDING_TABLE = "oam_sync_scope_bindings"
RLS_READY_FUNCTION = "public.rsc_oam_runtime_binding_ready_0044()"
EDGE_RECEIVER_ROLE = "edge_inbox"
ROLE_NAMES = (
    "star_oam_migrator",
    "star_oam_api",
    "star_oam_backup",
    "star_oam_projector",
    "star_oam_edge",
    EDGE_RECEIVER_ROLE,
)
LOGIN_ROLE_NAMES = tuple(
    role_name
    for role_name in ROLE_NAMES
    if role_name not in {"star_oam_edge", EDGE_RECEIVER_ROLE}
)
BOOTSTRAP_ROLE_NAMES = tuple(
    role_name for role_name in ROLE_NAMES if role_name != EDGE_RECEIVER_ROLE
)
WORK_ORDER_LOCK_FUNCTION = (
    "public.rsc_lock_material_request_work_order_reference_0042(uuid)"
)
MATERIAL_REQUEST_STATUS_TRIGGER_0045 = (
    "trg_material_requests_status_transition_0045"
)
MATERIAL_REQUEST_FILE_GUARD_FUNCTION_0029 = (
    "rsc_guard_material_request_file_0029"
)
MATERIAL_REQUEST_DECISION_GUARD_FUNCTION_0029 = (
    "rsc_guard_material_request_decision_quantity_0029"
)
MATERIAL_REQUEST_CONTENT_MANIFEST_COLUMN_0046 = (
    "projection_manifest_sha256"
)
MATERIAL_REQUEST_CONTENT_MANIFEST_CONSTRAINT_0046 = (
    "ck_material_request_commands_projection_manifest_0046"
)
MATERIAL_REQUEST_CONTENT_FUNCTIONS_0046 = {
    "rsc_guard_material_request_content_write_0046": ("", "trigger"),
    "rsc_validate_material_request_content_causality_0046": ("uuid", "void"),
    "rsc_dispatch_material_request_content_causality_0046": ("", "trigger"),
}
MATERIAL_REQUEST_CONTENT_TRIGGER_TABLES_0046 = (
    "material_request_revisions",
    "material_request_lines",
    "material_request_files",
    "material_request_commands",
)
STOCKTAKE_START_COMPLETION_TABLE_0047 = "stocktake_start_completions"
STOCKTAKE_START_FUNCTIONS_0047 = {
    "rsc_guard_stocktake_start_completion_0047": ("", "trigger"),
    "rsc_validate_nonopening_stocktake_start_causality_0047": (
        "uuid",
        "void",
    ),
    "rsc_dispatch_nonopening_stocktake_start_causality_0047": (
        "",
        "trigger",
    ),
}
STOCKTAKE_START_TRIGGER_TABLES_0047 = (
    "stocktake_tasks",
    "stocktake_scopes",
    "inventory_freezes",
    "stocktake_snapshot_lines",
    "stocktake_rounds",
    STOCKTAKE_START_COMPLETION_TABLE_0047,
    "state_transition_events",
    "audit_events",
)
STOCKTAKE_START_SEALED_TABLES_0047 = tuple(
    table_name
    for table_name in STOCKTAKE_START_TRIGGER_TABLES_0047
    if table_name != STOCKTAKE_START_COMPLETION_TABLE_0047
)
STOCKTAKE_SCOPE_GUARD_FUNCTION_0048 = (
    "rsc_validate_stocktake_scope_region_owner_0025"
)
STOCKTAKE_SCOPE_GUARD_TRIGGER_0048 = (
    "trg_stocktake_scopes_region_owner_0025"
)
STOCKTAKE_SCOPE_GUARD_BODY_SHA256_0048 = (
    "904a443c2c5930356af0f15f444f29ec6b6ce61f32294e4b2e3b40dd3a0e4e8e"
)
STOCKTAKE_RECOUNT_GUARD_SECURITY_MIGRATION_0049 = (
    CLOUD_ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0049_stocktake_recount_guard_security.py"
)
STOCKTAKE_OBSERVATION_SCOPE_MODE_MIGRATION_0050 = (
    CLOUD_ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0050_stocktake_observation_scope_mode.py"
)
STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_MIGRATION_0051 = (
    CLOUD_ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0051_stocktake_difference_authorization_hash.py"
)
OPENING_TERMINAL_GUARD_EXECUTION_MIGRATION_0052 = (
    CLOUD_ROOT
    / "backend"
    / "alembic"
    / "versions"
    / "20260903_0052_opening_terminal_guard_execution.py"
)
STOCKTAKE_OBSERVATION_BODY_SHA256_0050 = (
    "06cf2fafa1d90f120fe4bba21cc1dc55dba70bd63f649671b4333a6159af06bb"
)
STOCKTAKE_RECOUNT_ALIAS_TRIGGER_0049 = (
    "trg_pg16_unapproved_recount_guard_alias_0049"
)
STOCKTAKE_OBSERVATION_ALIAS_TRIGGER_0050 = (
    "trg_pg16_unapproved_observation_guard_alias_0050"
)
STOCKTAKE_DIFFERENCE_ALIAS_TRIGGER_0051 = (
    "trg_pg16_unapproved_difference_completion_alias_0051"
)
OPENING_TERMINAL_ALIAS_TRIGGER_0052 = (
    "trg_pg16_unapproved_opening_terminal_alias_0052"
)
OPENING_TERMINAL_SHADOW_SCHEMA_0052 = "opening_terminal_shadow_gate_0052"
OPENING_HEAD_CALLER_ALIAS_TRIGGER_0052 = (
    "trg_pg16_unapproved_opening_head_caller_alias_0052"
)
STOCKTAKE_DIFFERENCE_IMMUTABLE_TRIGGER_0016 = (
    "trg_stocktake_difference_set_completions_immutable_0016"
)
STOCKTAKE_SCOPE_COUNT_IMMUTABLE_TRIGGER_0011 = (
    "trg_stocktake_scope_count_completions_immutable_0011"
)
PG16_REDACTED_DATABASE_FAILURE_CODES = frozenset(
    {
        "inventory_concurrent_conflict",
        "material_request_approval_audit_unavailable",
        "material_request_approval_concurrent_conflict",
        "material_request_approval_database_unavailable",
        "material_request_audit_chain_unavailable",
        "material_request_concurrent_conflict",
        "material_request_database_unavailable",
        "material_request_lifecycle_audit_unavailable",
        "material_request_lifecycle_concurrent_conflict",
        "material_request_lifecycle_database_unavailable",
        "opening_close_database_guard_rejected",
        "opening_count_audit_chain_unavailable",
        "opening_count_concurrent_conflict",
        "opening_count_database_guard_rejected",
        "opening_count_database_unavailable",
        "opening_finalize_database_guard_rejected",
        "opening_observation_disposition_audit_chain_unavailable",
        "opening_observation_disposition_concurrent_conflict",
        "opening_observation_disposition_database_guard_rejected",
        "opening_recount_database_guard_rejected",
        "opening_review_database_guard_rejected",
        "stocktake_audit_chain_unavailable",
        "stocktake_count_audit_chain_unavailable",
        "stocktake_count_concurrent_conflict",
        "stocktake_count_database_unavailable",
        "stocktake_count_reference_graph_invalid",
        "stocktake_concurrent_conflict",
        "stocktake_database_unavailable",
        "stocktake_difference_audit_chain_unavailable",
        "stocktake_difference_concurrent_conflict",
        "stocktake_difference_database_unavailable",
        "stocktake_difference_reference_graph_invalid",
        "stocktake_posting_database_guard_rejected",
    }
)
MATERIAL_REQUEST_NEUTRAL_AXES = {
    "allocation_status": "not_allocated",
    "reservation_status": "not_reserved",
    "outbound_status": "not_started",
    "shipment_status": "not_started",
    "logistics_signature_status": "not_signed",
    "oam_receipt_status": "not_occurred",
    "personal_inbound_status": "not_started",
    "notification_status": "not_started",
    "reconciliation_status": "not_started",
}
PROJECTOR_SHADOW_SCHEMA = "projector_shadow_gate"
TEST_OAM_SOURCE_COORDINATES = {
    "edge_source_instance": "pg16-reviewed-edge",
    "company_id": "pg16-reviewed-company",
    "org_code": "pg16-reviewed-org",
    "scope_key": "work-orders:recent-30d",
}
PROJECTOR_GATE_SOURCE_TIME = datetime(
    2026, 9, 2, 2, 0, tzinfo=timezone.utc
)
PROJECTOR_GATE_EXECUTOR_ID = "pg16-projector-employee-001"
PROJECTOR_GATE_WORK_ORDER_ID = "pg16-projector-work-order-001"
PROJECTOR_GATE_WORK_ORDER_NO = "PG16-WO-001"
PROJECTOR_UPDATE_COLUMNS = {
    "sync_runs": {
        "status",
        "manifest_sha256",
        "completed_at",
        "failure_code",
        "failure_detail",
        "updated_at",
    },
    "sync_batches": {"status", "validated_at"},
    "sync_inbox_events": {
        "status",
        "error_code",
        "error_detail",
        "processed_at",
    },
    "external_objects": {"current_version_id", "updated_at"},
    "external_object_versions": {"is_current", "valid_to"},
    "sync_conflicts": {
        "status",
        "resolution_jsonb",
        "resolved_by",
        "resolved_at",
        "updated_at",
    },
    "oam_work_orders": {
        "work_order_no",
        "organization_id",
        "engineer_person_id",
        "status",
        "source_updated_at",
        "updated_at",
    },
}
def _gate_enabled() -> bool:
    return (
        os.getenv("RSC_PG16_GATE_ACKNOWLEDGE_DISPOSABLE") == ACKNOWLEDGEMENT
        and os.getenv("GITHUB_ACTIONS") == "true"
        and os.getenv("RUNNER_ENVIRONMENT") == "github-hosted"
    )


pytestmark = pytest.mark.skipif(
    not _gate_enabled(),
    reason="requires the explicitly acknowledged disposable PostgreSQL 16 gate",
)


def _required_environment(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        pytest.fail(f"missing required PostgreSQL 16 gate setting: {name}")
    return value


def _connection_parameters(*, role: str, password: str) -> dict[str, object]:
    host = _required_environment("RSC_PG16_GATE_HOST")
    if host not in {"127.0.0.1", "localhost", "::1"}:
        pytest.fail("PostgreSQL 16 gate only accepts a loopback host")
    database = _required_environment("RSC_PG16_GATE_DATABASE")
    if database != DATABASE_NAME:
        pytest.fail("PostgreSQL 16 gate database name is not the reviewed disposable name")
    raw_port = _required_environment("RSC_PG16_GATE_PORT")
    try:
        port = int(raw_port)
    except ValueError:
        pytest.fail("PostgreSQL 16 gate port is invalid")
    if not 1 <= port <= 65535:
        pytest.fail("PostgreSQL 16 gate port is invalid")
    return {
        "host": host,
        "port": port,
        "dbname": database,
        "user": role,
        "password": password,
    }


def _isolated_connection_parameters(
    *,
    database_name: str,
    role: str,
    password: str,
) -> dict[str, object]:
    suffix = database_name.removeprefix(OPENING_BACKFILL_DATABASE_PREFIX)
    if (
        not database_name.startswith(OPENING_BACKFILL_DATABASE_PREFIX)
        or not suffix
        or not suffix.isalnum()
    ):
        pytest.fail("PostgreSQL 16 gate rejected an unreviewed isolated database")
    parameters = _connection_parameters(role=role, password=password)
    parameters["dbname"] = database_name
    return parameters


def _sqlalchemy_url(
    *,
    role: str,
    password: str,
    database_name: str = DATABASE_NAME,
) -> URL:
    if database_name == DATABASE_NAME:
        parameters = _connection_parameters(role=role, password=password)
    else:
        parameters = _isolated_connection_parameters(
            database_name=database_name,
            role=role,
            password=password,
        )
    return URL.create(
        "postgresql+psycopg",
        username=str(parameters["user"]),
        password=str(parameters["password"]),
        host=str(parameters["host"]),
        port=int(parameters["port"]),
        database=str(parameters["dbname"]),
    )


def _admin_parameters() -> dict[str, object]:
    admin_user = _required_environment("RSC_PG16_GATE_ADMIN_USER")
    if admin_user != "postgres":
        pytest.fail("PostgreSQL 16 gate requires the disposable postgres bootstrap role")
    return _connection_parameters(
        role=admin_user,
        password=_required_environment("RSC_PG16_GATE_ADMIN_PASSWORD"),
    )


def _admin_sqlalchemy_url() -> URL:
    parameters = _admin_parameters()
    return URL.create(
        "postgresql+psycopg",
        username=str(parameters["user"]),
        password=str(parameters["password"]),
        host=str(parameters["host"]),
        port=int(parameters["port"]),
        database=str(parameters["dbname"]),
    )


def _role_password(role_name: str) -> str:
    setting_by_role = {
        "star_oam_migrator": "RSC_PG16_GATE_MIGRATOR_PASSWORD",
        "star_oam_api": "RSC_PG16_GATE_API_PASSWORD",
        "star_oam_backup": "RSC_PG16_GATE_BACKUP_PASSWORD",
        "star_oam_projector": "RSC_PG16_GATE_PROJECTOR_PASSWORD",
        EDGE_RECEIVER_ROLE: "RSC_PG16_GATE_EDGE_RECEIVER_PASSWORD",
    }
    return _required_environment(setting_by_role[role_name])


def _assert_fresh_disposable_postgresql16() -> None:
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT current_database(), current_user, "
                "current_setting('server_version_num')::integer, "
                "(SELECT rolsuper FROM pg_roles WHERE rolname = current_user)"
            )
            database, user, version_number, is_superuser = cursor.fetchone()
            assert database == DATABASE_NAME
            assert user == "postgres"
            assert 160000 <= version_number < 170000
            assert is_superuser is True

            cursor.execute(
                "SELECT datname FROM pg_database "
                "WHERE datallowconn AND NOT datistemplate ORDER BY datname"
            )
            assert [row[0] for row in cursor.fetchall()] == [
                "postgres",
                DATABASE_NAME,
            ]

            cursor.execute(
                "SELECT rolname FROM pg_roles "
                "WHERE rolname NOT LIKE 'pg\\_%' ESCAPE '\\' ORDER BY rolname"
            )
            assert [row[0] for row in cursor.fetchall()] == ["postgres"]

            cursor.execute(
                "SELECT COUNT(*) FROM pg_class AS class_row "
                "JOIN pg_namespace AS schema_row "
                "ON schema_row.oid = class_row.relnamespace "
                "WHERE schema_row.nspname = 'public' "
                "AND class_row.relkind IN ('r', 'p', 'S', 'v', 'm', 'f')"
            )
            assert cursor.fetchone()[0] == 0
            cursor.execute(
                "SELECT rolname FROM pg_roles WHERE rolname = ANY(%s) ORDER BY rolname",
                (list(ROLE_NAMES),),
            )
            assert cursor.fetchall() == []


def _bootstrap_roles() -> None:
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            for role_name in LOGIN_ROLE_NAMES:
                cursor.execute(
                    sql.SQL(
                        "CREATE ROLE {} LOGIN PASSWORD {} "
                        "NOSUPERUSER NOCREATEDB NOCREATEROLE "
                        "NOREPLICATION NOBYPASSRLS INHERIT"
                    ).format(
                        sql.Identifier(role_name),
                        sql.Literal(_role_password(role_name)),
                    )
                )
            cursor.execute(
                "CREATE ROLE star_oam_edge NOLOGIN NOSUPERUSER NOCREATEDB "
                "NOCREATEROLE NOREPLICATION NOBYPASSRLS INHERIT"
            )
            cursor.execute(
                sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                    sql.Identifier(DATABASE_NAME)
                )
            )
            cursor.execute(
                "SELECT format("
                "'REVOKE CONNECT, TEMPORARY ON DATABASE %I FROM PUBLIC', "
                "datname) FROM pg_catalog.pg_database "
                "WHERE datallowconn AND datname <> current_database() "
                "ORDER BY datname"
            )
            for (statement,) in cursor.fetchall():
                cursor.execute(statement)
            cursor.execute(
                sql.SQL(
                    "GRANT CONNECT ON DATABASE {} TO "
                    "star_oam_migrator, star_oam_api, star_oam_backup, "
                    "star_oam_projector"
                ).format(sql.Identifier(DATABASE_NAME))
            )
            cursor.execute(
                sql.SQL("ALTER DATABASE {} OWNER TO star_oam_migrator").format(
                    sql.Identifier(DATABASE_NAME)
                )
            )
            cursor.execute("ALTER SCHEMA public OWNER TO star_oam_migrator")
            cursor.execute(
                "REVOKE ALL ON SCHEMA public FROM PUBLIC, star_oam_api, "
                "star_oam_backup, star_oam_projector, star_oam_edge"
            )
            cursor.execute(
                "GRANT USAGE, CREATE ON SCHEMA public TO star_oam_migrator"
            )
            cursor.execute(
                "GRANT USAGE ON SCHEMA public TO star_oam_api, "
                "star_oam_backup, star_oam_projector"
            )
            for role_name in BOOTSTRAP_ROLE_NAMES:
                cursor.execute(
                    sql.SQL("ALTER ROLE {} SET search_path = public").format(
                        sql.Identifier(role_name)
                    )
                )
            cursor.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator "
                "IN SCHEMA public REVOKE ALL ON TABLES FROM PUBLIC"
            )
            cursor.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator "
                "IN SCHEMA public GRANT SELECT ON TABLES TO star_oam_backup"
            )
            cursor.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator "
                "IN SCHEMA public REVOKE ALL ON SEQUENCES FROM PUBLIC"
            )
            cursor.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator "
                "IN SCHEMA public GRANT SELECT ON SEQUENCES TO star_oam_backup"
            )
            cursor.execute(
                "ALTER DEFAULT PRIVILEGES FOR ROLE star_oam_migrator "
                "IN SCHEMA public REVOKE EXECUTE ON FUNCTIONS FROM PUBLIC"
            )


def _migration_environment(
    *, database_name: str = DATABASE_NAME
) -> dict[str, str]:
    migration_url = _sqlalchemy_url(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
        database_name=database_name,
    )
    environment = os.environ.copy()
    environment.update(
        {
            "OAM_ENVIRONMENT": "production",
            "OAM_DATABASE_URL": migration_url.render_as_string(
                hide_password=False
            ),
            "OAM_DATABASE_EXPECTED_MIGRATION_ROLE": "star_oam_migrator",
            "OAM_DATABASE_EXPECTED_RUNTIME_ROLE": "star_oam_api",
        }
    )
    return environment


def _run_alembic(
    *arguments: str,
    expect_success: bool = True,
    database_name: str = DATABASE_NAME,
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=CLOUD_ROOT,
        env=_migration_environment(database_name=database_name),
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    if expect_success and completed.returncode != 0:
        pytest.fail(
            "Alembic release-gate command failed\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    if not expect_success and completed.returncode == 0:
        pytest.fail("Alembic release-gate command unexpectedly succeeded")
    return completed


def _run_deployment_sql(
    script_name: str,
    *,
    variables: dict[str, str],
    expect_success: bool = True,
) -> subprocess.CompletedProcess[str]:
    parameters = _connection_parameters(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    command = [
        "psql",
        "--no-password",
        "--set=ON_ERROR_STOP=1",
        f"--host={parameters['host']}",
        f"--port={parameters['port']}",
        f"--username={parameters['user']}",
        f"--dbname={parameters['dbname']}",
    ]
    for key, value in sorted(variables.items()):
        command.append(f"--set={key}={value}")
    command.extend(["--file", str(CLOUD_ROOT / "deployment" / script_name)])
    environment = os.environ.copy()
    environment["PGPASSWORD"] = str(parameters["password"])
    completed = subprocess.run(
        command,
        cwd=CLOUD_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    if expect_success and completed.returncode != 0:
        pytest.fail(
            "deployment SQL release-gate command failed\n"
            f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
        )
    if not expect_success and completed.returncode == 0:
        pytest.fail("deployment SQL release-gate command unexpectedly succeeded")
    return completed


def _run_edge_receiver_role_provision() -> subprocess.CompletedProcess[str]:
    parameters = _admin_parameters()
    edge_password = _role_password(EDGE_RECEIVER_ROLE)
    command = [
        "psql",
        "--no-password",
        "--set=ON_ERROR_STOP=1",
        f"--host={parameters['host']}",
        f"--port={parameters['port']}",
        f"--username={parameters['user']}",
        f"--dbname={parameters['dbname']}",
        f"--set=edge_role={EDGE_RECEIVER_ROLE}",
        "--file",
        str(CLOUD_ROOT / "deployment" / "provision_edge_receiver_role.sql"),
    ]
    assert edge_password not in " ".join(command)
    environment = os.environ.copy()
    environment["PGPASSWORD"] = str(parameters["password"])
    environment["OAM_DB_EDGE_RECEIVER_PASSWORD"] = edge_password
    completed = subprocess.run(
        command,
        cwd=CLOUD_ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert edge_password not in completed.stdout
    assert edge_password not in completed.stderr
    return completed


def _assert_edge_receiver_provision_rolls_back_on_cross_database_connect() -> None:
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("GRANT CONNECT ON DATABASE postgres TO PUBLIC")
    try:
        completed = _run_edge_receiver_role_provision()
        assert completed.returncode == 3
        with psycopg.connect(**_admin_parameters()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT COUNT(*) FROM pg_catalog.pg_roles "
                    "WHERE rolname = %s",
                    (EDGE_RECEIVER_ROLE,),
                )
                assert cursor.fetchone()[0] == 0
    finally:
        with psycopg.connect(
            **_admin_parameters(), autocommit=True
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE CONNECT ON DATABASE postgres FROM PUBLIC")


def _provision_edge_receiver_role() -> None:
    for _ in range(2):
        completed = _run_edge_receiver_role_provision()
        if completed.returncode != 0:
            pytest.fail(
                "edge role provisioning failed\n"
                f"stdout:\n{completed.stdout}\nstderr:\n{completed.stderr}"
            )


def _current_revision() -> str:
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            return cursor.fetchone()[0]


def _isolated_current_revision(database_name: str) -> str:
    with psycopg.connect(
        **_isolated_connection_parameters(
            database_name=database_name,
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT version_num FROM alembic_version")
            return cursor.fetchone()[0]


def _restore_main_projector_connect() -> None:
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "GRANT CONNECT ON DATABASE {} TO star_oam_projector"
                ).format(sql.Identifier(DATABASE_NAME))
            )


def _create_opening_backfill_database() -> str:
    database_name = (
        f"{OPENING_BACKFILL_DATABASE_PREFIX}{uuid.uuid4().hex[:12]}"
    )
    created = False
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("REVOKE CONNECT ON DATABASE {} FROM star_oam_projector").format(
                    sql.Identifier(DATABASE_NAME)
                )
            )
            try:
                cursor.execute(
                    sql.SQL("CREATE DATABASE {} OWNER star_oam_migrator").format(
                        sql.Identifier(database_name)
                    )
                )
                created = True
                cursor.execute(
                    sql.SQL("REVOKE ALL ON DATABASE {} FROM PUBLIC").format(
                        sql.Identifier(database_name)
                    )
                )
                cursor.execute(
                    sql.SQL(
                        "GRANT CONNECT ON DATABASE {} TO "
                        "star_oam_migrator, star_oam_api, star_oam_backup, "
                        "star_oam_projector"
                    ).format(sql.Identifier(database_name))
                )
            except BaseException as creation_error:
                cleanup_error: BaseException | None = None
                try:
                    if created:
                        cursor.execute(
                            sql.SQL(
                                "DROP DATABASE {} WITH (FORCE)"
                            ).format(sql.Identifier(database_name))
                        )
                except BaseException as exc:
                    cleanup_error = exc
                finally:
                    try:
                        _restore_main_projector_connect()
                    except BaseException as restoration_error:
                        if cleanup_error is not None:
                            raise restoration_error from cleanup_error
                        raise restoration_error from creation_error
                if cleanup_error is not None:
                    raise cleanup_error from creation_error
                raise

    isolated_admin = _admin_parameters()
    isolated_admin["dbname"] = database_name
    try:
        with psycopg.connect(
            **isolated_admin,
            autocommit=True,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER SCHEMA public OWNER TO star_oam_migrator"
                )
                cursor.execute(
                    "REVOKE ALL ON SCHEMA public FROM PUBLIC, star_oam_api, "
                    "star_oam_backup, star_oam_projector, star_oam_edge"
                )
                cursor.execute(
                    "GRANT USAGE, CREATE ON SCHEMA public "
                    "TO star_oam_migrator"
                )
                cursor.execute(
                    "GRANT USAGE ON SCHEMA public TO star_oam_api, "
                    "star_oam_backup, star_oam_projector"
                )
    except BaseException as setup_error:
        try:
            _drop_opening_backfill_database(database_name)
        except BaseException as cleanup_error:
            raise cleanup_error from setup_error
        raise
    return database_name


def _drop_opening_backfill_database(database_name: str) -> None:
    _isolated_connection_parameters(
        database_name=database_name,
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    cleanup_error: BaseException | None = None
    try:
        with psycopg.connect(
            **_admin_parameters(), autocommit=True
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT pg_catalog.pg_terminate_backend(pid) "
                    "FROM pg_catalog.pg_stat_activity "
                    "WHERE datname = %s "
                    "AND pid <> pg_catalog.pg_backend_pid()",
                    (database_name,),
                )
                cursor.fetchall()
                cursor.execute(
                    sql.SQL(
                        "DROP DATABASE IF EXISTS {} WITH (FORCE)"
                    ).format(sql.Identifier(database_name))
                )
    except BaseException as exc:
        cleanup_error = exc
        raise
    finally:
        try:
            _restore_main_projector_connect()
        except BaseException as restoration_error:
            if cleanup_error is not None:
                raise restoration_error from cleanup_error
            raise


def _table_exists(table_name: str) -> bool:
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regclass(%s)", (f"public.{table_name}",))
            return cursor.fetchone()[0] is not None


def _work_order_lock_function_exists() -> bool:
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT to_regprocedure(%s)", (WORK_ORDER_LOCK_FUNCTION,))
            return cursor.fetchone()[0] is not None


def _insert_unexpired_preflight_challenge(challenge_id: uuid.UUID) -> None:
    now = datetime.now(timezone.utc)
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO login_challenges (
                    id, mobile_hash, code_hash, verification_mode, provider,
                    provider_reference, client_type, purpose, attempts,
                    max_attempts, expires_at, status, idempotency_key,
                    requested_ip_hash, verified_at, consumed_at, created_at
                ) VALUES (
                    %s, %s, NULL, 'provider_managed', 'aliyun_dypns', NULL,
                    'web', 'login', 0, 5, %s, 'pending', %s, %s,
                    NULL, NULL, %s
                )
                """,
                (
                    challenge_id,
                    "a" * 64,
                    now + timedelta(minutes=5),
                    f"pg16-preflight-{challenge_id}",
                    "b" * 64,
                    now,
                ),
            )


def _delete_challenge(challenge_id: uuid.UUID) -> None:
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("DELETE FROM login_challenges WHERE id = %s", (challenge_id,))


def _validate_runtime_security(api_engine) -> None:
    from app.database_security import validate_production_database_security

    validate_production_database_security(
        api_engine,
        expected_runtime_role="star_oam_api",
        expected_migration_role="star_oam_migrator",
    )


def _assert_request_file_guard_execution_boundary(
    *,
    security_definer: bool,
) -> None:
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT function_row.prosecdef,
                       owner.rolname,
                       function_row.proconfig,
                       has_function_privilege(
                           'star_oam_api', function_row.oid, 'EXECUTE'
                       ),
                       EXISTS (
                           SELECT 1
                             FROM aclexplode(
                                 coalesce(
                                     function_row.proacl,
                                     acldefault(
                                         'f', function_row.proowner
                                     )
                                 )
                             ) AS function_acl
                            WHERE function_acl.grantee = 0
                              AND function_acl.privilege_type = 'EXECUTE'
                       ),
                       has_table_privilege(
                           'star_oam_api',
                           'public.approval_delegations',
                           'SELECT'
                       )
                  FROM pg_catalog.pg_proc AS function_row
                  JOIN pg_catalog.pg_namespace AS schema_row
                    ON schema_row.oid = function_row.pronamespace
                  JOIN pg_catalog.pg_roles AS owner
                    ON owner.oid = function_row.proowner
                 WHERE schema_row.nspname = 'public'
                   AND function_row.proname = %s
                   AND function_row.pronargs = 0
                """,
                (MATERIAL_REQUEST_FILE_GUARD_FUNCTION_0029,),
            )
            assert cursor.fetchone() == (
                security_definer,
                "star_oam_migrator",
                ["search_path=pg_catalog, public"],
                False,
                False,
                False,
            )


def _assert_decision_guard_variable_boundary(*, repaired: bool) -> None:
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT function_row.prosrc,
                       function_row.prosecdef,
                       owner.rolname,
                       function_row.proconfig,
                       has_function_privilege(
                           'star_oam_api', function_row.oid, 'EXECUTE'
                       ),
                       EXISTS (
                           SELECT 1
                             FROM aclexplode(
                                 coalesce(
                                     function_row.proacl,
                                     acldefault(
                                         'f', function_row.proowner
                                     )
                                 )
                             ) AS function_acl
                            WHERE function_acl.grantee = 0
                              AND function_acl.privilege_type = 'EXECUTE'
                       )
                  FROM pg_catalog.pg_proc AS function_row
                  JOIN pg_catalog.pg_namespace AS schema_row
                    ON schema_row.oid = function_row.pronamespace
                  JOIN pg_catalog.pg_roles AS owner
                    ON owner.oid = function_row.proowner
                 WHERE schema_row.nspname = 'public'
                   AND function_row.proname = %s
                   AND function_row.pronargs = 0
                """,
                (MATERIAL_REQUEST_DECISION_GUARD_FUNCTION_0029,),
            )
            row = cursor.fetchone()
    assert row is not None
    body, security_definer, owner, configuration, api_execute, public_execute = row
    assert (
        security_definer,
        owner,
        configuration,
        api_execute,
        public_execute,
    ) == (
        False,
        "star_oam_migrator",
        ["search_path=pg_catalog, public"],
        False,
        False,
    )
    assert ("#variable_conflict error" in body) is repaired
    assert ("line.request_id = guard_request_id" in body) is repaired
    assert ("line.revision_id = guard_request_revision_id" in body) is repaired
    assert ("line.request_id = request_id" in body) is (not repaired)


def _validate_projector_security(projector_engine) -> None:
    from app.oam_projection_security import (
        verify_oam_projection_database_boundary,
    )

    verify_oam_projection_database_boundary(
        projector_engine,
        expected_role="star_oam_projector",
        expected_migration_role="star_oam_migrator",
    )


def _validate_edge_security(edge_engine) -> None:
    from app.edge_database_security import verify_edge_database_boundary

    verify_edge_database_boundary(
        edge_engine,
        expected_role=EDGE_RECEIVER_ROLE,
        expected_migration_role="star_oam_migrator",
    )


def _assert_cross_database_connections_denied() -> None:
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            for role_name in ("star_oam_projector", EDGE_RECEIVER_ROLE):
                cursor.execute(
                    "SELECT has_database_privilege(%s, 'postgres', 'CONNECT')",
                    (role_name,),
                )
                assert cursor.fetchone()[0] is False

    for role_name in ("star_oam_projector", EDGE_RECEIVER_ROLE):
        parameters = _connection_parameters(
            role=role_name,
            password=_role_password(role_name),
        )
        parameters["dbname"] = "postgres"
        parameters["connect_timeout"] = 5
        with pytest.raises(psycopg.Error) as error:
            psycopg.connect(**parameters)
        assert "permission denied for database" in str(error.value).lower()


def _assert_projector_acl_revoked() -> None:
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    has_schema_privilege(
                        'star_oam_projector', 'public', 'USAGE'
                    ),
                    has_schema_privilege(
                        'star_oam_projector', 'public', 'CREATE'
                    ),
                    has_table_privilege(
                        'star_oam_projector',
                        'public.external_sync_snapshots',
                        'SELECT'
                    ),
                    has_table_privilege(
                        'star_oam_projector', 'public.sync_runs', 'INSERT'
                    ),
                    has_table_privilege(
                        'star_oam_projector', 'public.oam_work_orders', 'UPDATE'
                    ),
                    has_column_privilege(
                        'star_oam_projector',
                        'public.external_object_versions',
                        'is_current',
                        'UPDATE'
                    )
                """
            )
            assert cursor.fetchone() == (
                False,
                False,
                False,
                False,
                False,
                False,
            )


def _assert_0043_rejects_projector_membership_drift() -> None:
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "GRANT star_oam_backup TO star_oam_projector "
                "WITH INHERIT FALSE, SET FALSE, ADMIN FALSE"
            )
    try:
        blocked = _run_alembic("upgrade", "head", expect_success=False)
        assert "database role membership violates" in (
            blocked.stdout + blocked.stderr
        )
        assert _current_revision() == "20260902_0042"
    finally:
        with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("REVOKE star_oam_backup FROM star_oam_projector")


def _assert_0043_rejects_projector_cross_schema_drift() -> None:
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE SCHEMA {} AUTHORIZATION star_oam_projector").format(
                    sql.Identifier(PROJECTOR_SHADOW_SCHEMA)
                )
            )
    try:
        blocked = _run_alembic("upgrade", "head", expect_success=False)
        assert "projector cross-schema boundary is not closed" in (
            blocked.stdout + blocked.stderr
        )
        assert _current_revision() == "20260902_0042"
    finally:
        with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("DROP SCHEMA {} RESTRICT").format(
                        sql.Identifier(PROJECTOR_SHADOW_SCHEMA)
                    )
                )


def _assert_0043_rejects_unrevocable_parameter_acl() -> None:
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "GRANT SET ON PARAMETER session_replication_role "
                "TO star_oam_projector"
            )
    try:
        _run_alembic("upgrade", "head", expect_success=False)
        assert _current_revision() == "20260902_0042"
    finally:
        with psycopg.connect(
            **_admin_parameters(), autocommit=True
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "REVOKE SET ON PARAMETER session_replication_role "
                    "FROM star_oam_projector"
                )


def _inject_pre_0043_projector_acl_drift() -> int:
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "GRANT SELECT (status) ON TABLE public.material_requests "
                "TO star_oam_projector"
            )
            cursor.execute(
                "GRANT UPDATE (payload_jsonb) "
                "ON TABLE public.external_object_versions "
                "TO star_oam_projector"
            )
            cursor.execute(
                sql.SQL("GRANT TEMPORARY ON DATABASE {} TO star_oam_projector").format(
                    sql.Identifier(DATABASE_NAME)
                )
            )
            cursor.execute(
                "GRANT SELECT ON TABLE public.material_requests TO PUBLIC"
            )
            cursor.execute(
                "GRANT UPDATE (status) ON TABLE public.material_requests "
                "TO PUBLIC"
            )
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        ),
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_catalog.lo_create(0)")
            large_object_oid = int(cursor.fetchone()[0])
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT ON LARGE OBJECT {} TO star_oam_projector"
                ).format(sql.Literal(large_object_oid))
            )
    return large_object_oid


def _assert_pre_0043_acl_drift_was_cleaned(large_object_oid: int) -> None:
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    NOT EXISTS (
                        SELECT 1
                          FROM pg_catalog.pg_class AS relation
                          JOIN pg_catalog.pg_namespace AS schema_row
                            ON schema_row.oid = relation.relnamespace
                         CROSS JOIN LATERAL
                              pg_catalog.aclexplode(relation.relacl) AS acl
                         WHERE schema_row.nspname = 'public'
                           AND acl.grantee = 0
                    ),
                    NOT EXISTS (
                        SELECT 1
                          FROM pg_catalog.pg_attribute AS attribute_row
                          JOIN pg_catalog.pg_class AS relation
                            ON relation.oid = attribute_row.attrelid
                          JOIN pg_catalog.pg_namespace AS schema_row
                            ON schema_row.oid = relation.relnamespace
                         CROSS JOIN LATERAL pg_catalog.aclexplode(
                             attribute_row.attacl
                         ) AS acl
                         WHERE schema_row.nspname = 'public'
                           AND acl.grantee = 0
                    ),
                    NOT EXISTS (
                        SELECT 1
                          FROM pg_catalog.pg_largeobject_metadata AS large_object
                         CROSS JOIN LATERAL pg_catalog.aclexplode(
                             large_object.lomacl
                         ) AS acl
                          JOIN pg_catalog.pg_roles AS role_row
                            ON role_row.oid = acl.grantee
                         WHERE large_object.oid = %s
                           AND role_row.rolname = 'star_oam_projector'
                    )
                """,
                (large_object_oid,),
            )
            assert cursor.fetchone() == (True, True, True)
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        ),
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_catalog.lo_unlink(%s)",
                (large_object_oid,),
            )
            assert cursor.fetchone()[0] == 1


def _assert_projector_exact_column_acl() -> None:
    from app.oam_projection_security import PROJECTOR_INSERT_COLUMNS

    expected_inserts = {
        (table_name, column_name, "INSERT", False)
        for table_name, column_names in PROJECTOR_INSERT_COLUMNS.items()
        for column_name in column_names
    }
    expected_updates = {
        (table_name, column_name, "UPDATE", False)
        for table_name, column_names in PROJECTOR_UPDATE_COLUMNS.items()
        for column_name in column_names
    }
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    relation.relname,
                    attribute_row.attname,
                    acl.privilege_type,
                    acl.is_grantable
                FROM pg_catalog.pg_attribute AS attribute_row
                JOIN pg_catalog.pg_class AS relation
                  ON relation.oid = attribute_row.attrelid
                JOIN pg_catalog.pg_namespace AS schema_row
                  ON schema_row.oid = relation.relnamespace
                CROSS JOIN LATERAL
                    pg_catalog.aclexplode(attribute_row.attacl) AS acl
                JOIN pg_catalog.pg_roles AS grantee
                  ON grantee.oid = acl.grantee
                WHERE schema_row.nspname = 'public'
                  AND attribute_row.attnum > 0
                  AND NOT attribute_row.attisdropped
                  AND grantee.rolname = 'star_oam_projector'
                ORDER BY relation.relname, attribute_row.attname,
                         acl.privilege_type
                """
            )
            actual = set(cursor.fetchall())
            assert actual == expected_inserts | expected_updates
            cursor.execute(
                """
                SELECT
                    has_table_privilege(
                        'star_oam_projector', 'public.sync_runs', 'INSERT'
                    ),
                    has_table_privilege(
                        'star_oam_projector', 'public.sync_runs', 'UPDATE'
                    ),
                    has_column_privilege(
                        'star_oam_projector', 'public.sync_runs',
                        'updated_at', 'UPDATE'
                    ),
                    has_column_privilege(
                        'star_oam_projector', 'public.external_object_versions',
                        'payload_jsonb', 'UPDATE'
                    ),
                    has_column_privilege(
                        'star_oam_projector', 'public.material_requests',
                        'status', 'SELECT'
                    ),
                    has_database_privilege(
                        'star_oam_projector', current_database(), 'TEMPORARY'
                    )
                """
            )
            assert cursor.fetchone() == (False, False, True, False, False, False)


def _assert_0044_rejects_stray_permissive_policy() -> None:
    assert _current_revision() == "20260902_0043"
    policy_name = "pg16_stray_source_systems_public_select"
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        ),
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE public.source_systems ENABLE ROW LEVEL SECURITY"
            )
            cursor.execute(
                sql.SQL(
                    "CREATE POLICY {} ON public.source_systems "
                    "AS PERMISSIVE FOR SELECT TO PUBLIC USING (true)"
                ).format(sql.Identifier(policy_name))
            )
    try:
        blocked = _run_alembic("upgrade", "head", expect_success=False)
        assert "0044 RLS policy closure is invalid" in (
            blocked.stdout + blocked.stderr
        )
        assert _current_revision() == "20260902_0043"
        assert _table_exists(RLS_BINDING_TABLE) is False
    finally:
        with psycopg.connect(
            **_connection_parameters(
                role="star_oam_migrator",
                password=_role_password("star_oam_migrator"),
            ),
            autocommit=True,
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL(
                        "DROP POLICY IF EXISTS {} ON public.source_systems"
                    ).format(sql.Identifier(policy_name))
                )


def _assert_0044_rejects_untrusted_0043_projection_graph() -> None:
    assert _current_revision() == "20260902_0043"
    source_id = uuid.uuid4()
    external_object_id = uuid.uuid4()
    version_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    work_order_id = uuid.uuid4()
    source_code = f"pg16-preflight-broken-{source_id.hex}"
    now = datetime.now(timezone.utc)
    forged_payload = {
        "work_order_no": "PG16-FORGED-0043",
        "organization_id": str(organization_id),
        "engineer_person_id": None,
        "status": "active",
    }
    parameters = _connection_parameters(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    with psycopg.connect(**parameters, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO source_systems (
                    id, code, name, mode, enabled, configuration_jsonb,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, 'read_only', true, '{}'::jsonb, %s, %s)
                """,
                (source_id, source_code, "PG16 broken preflight sentinel", now, now),
            )
            cursor.execute(
                """
                INSERT INTO organizations (
                    id, external_object_id, code, name, parent_id, org_type,
                    province_code, status, created_at, updated_at
                ) VALUES (
                    %s, NULL, %s, %s, NULL, 'region_company', 'CN330000',
                    'active', %s, %s
                )
                """,
                (
                    organization_id,
                    f"PG16-FORGED-{organization_id.hex}",
                    "PG16 forged 0043 organization sentinel",
                    now,
                    now,
                ),
            )
            cursor.execute(
                """
                INSERT INTO external_objects (
                    id, source_system_id, entity_type, external_id,
                    current_version_id, deleted_at, created_at, updated_at
                ) VALUES (%s, %s, 'work_order', %s, %s, NULL, %s, %s)
                """,
                (
                    external_object_id,
                    source_id,
                    f"pg16-broken-{external_object_id.hex}",
                    version_id,
                    now,
                    now,
                ),
            )
            cursor.execute(
                """
                INSERT INTO external_object_versions (
                    id, external_object_id, source_version, source_updated_at,
                    valid_from, valid_to, payload_jsonb, payload_sha256,
                    is_current, created_at
                ) VALUES (
                    %s, %s, 'forged-without-inbox-provenance', %s, %s, NULL,
                    %s::jsonb, %s, true, %s
                )
                """,
                (
                    version_id,
                    external_object_id,
                    now,
                    now,
                    json.dumps(forged_payload),
                    "f" * 64,
                    now,
                ),
            )
            cursor.execute(
                """
                INSERT INTO oam_work_orders (
                    id, external_object_id, work_order_no, organization_id,
                    engineer_person_id, status, source_updated_at,
                    created_at, updated_at
                ) VALUES (
                    %s, %s, 'PG16-FORGED-0043', %s, NULL, 'active', %s, %s, %s
                )
                """,
                (
                    work_order_id,
                    external_object_id,
                    organization_id,
                    now,
                    now,
                    now,
                ),
            )
    try:
        blocked = _run_alembic("upgrade", "head", expect_success=False)
        assert "0044 requires an empty OAM sync graph" in (
            blocked.stdout + blocked.stderr
        )
        assert _current_revision() == "20260902_0043"
        assert _table_exists(RLS_BINDING_TABLE) is False
    finally:
        with psycopg.connect(**parameters, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM oam_work_orders WHERE id = %s",
                    (work_order_id,),
                )
                cursor.execute(
                    "DELETE FROM external_objects WHERE id = %s",
                    (external_object_id,),
                )
                cursor.execute(
                    "DELETE FROM organizations WHERE id = %s",
                    (organization_id,),
                )
                cursor.execute(
                    "DELETE FROM source_systems WHERE id = %s",
                    (source_id,),
                )


def _assert_0044_preflight_serializes_projector_writer() -> None:
    assert _current_revision() == "20260902_0043"
    source_id = uuid.uuid4()
    run_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    migrator_parameters = _connection_parameters(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    with psycopg.connect(
        **migrator_parameters, autocommit=True
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO source_systems (
                    id, code, name, mode, enabled, configuration_jsonb,
                    created_at, updated_at
                ) VALUES (%s, %s, %s, 'read_only', true, '{}'::jsonb, %s, %s)
                """,
                (
                    source_id,
                    f"pg16-preflight-race-{source_id.hex}",
                    "PG16 preflight serialization sentinel",
                    now,
                    now,
                ),
            )

    writer = psycopg.connect(
        **_connection_parameters(
            role="star_oam_projector",
            password=_role_password("star_oam_projector"),
        )
    )
    try:
        with writer.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO sync_runs (
                    id, source_system_id, run_key, scope_key, mode, status,
                    started_at, created_at, updated_at
                ) VALUES (%s, %s, %s, %s, 'full', 'pending', %s, %s, %s)
                """,
                (
                    run_id,
                    source_id,
                    f"pg16-preflight-race-{run_id.hex}",
                    "oam-work-order-scope:" + "a" * 64,
                    now,
                    now,
                    now,
                ),
            )

        with ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                _run_alembic,
                "upgrade",
                "head",
                expect_success=False,
            )
            deadline = time.monotonic() + 15
            migration_waiting = False
            while time.monotonic() < deadline and not future.done():
                with psycopg.connect(**_admin_parameters()) as inspection:
                    with inspection.cursor() as cursor:
                        cursor.execute(
                            """
                            SELECT EXISTS (
                                SELECT 1
                                  FROM pg_catalog.pg_stat_activity
                                 WHERE datname = current_database()
                                   AND usename = 'star_oam_migrator'
                                   AND wait_event_type = 'Lock'
                                   AND query LIKE
                                       'LOCK TABLE public.external_sync_snapshots,%'
                            )
                            """
                        )
                        migration_waiting = cursor.fetchone()[0]
                if not migration_waiting:
                    time.sleep(0.05)
            writer.commit()
            blocked = future.result(timeout=30)
            assert migration_waiting is True

        assert "0044 requires an empty OAM sync graph" in (
            blocked.stdout + blocked.stderr
        )
        assert _current_revision() == "20260902_0043"
        assert _table_exists(RLS_BINDING_TABLE) is False
    finally:
        writer.rollback()
        writer.close()
        with psycopg.connect(
            **migrator_parameters, autocommit=True
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("DELETE FROM sync_runs WHERE id = %s", (run_id,))
                cursor.execute(
                    "DELETE FROM source_systems WHERE id = %s",
                    (source_id,),
                )


def _provision_and_verify_deployment_acl() -> None:
    _run_deployment_sql(
        "create_oam_edge_staging.sql",
        variables={"edge_role": EDGE_RECEIVER_ROLE},
    )
    _run_deployment_sql(
        "verify_oam_edge_staging.sql",
        variables={
            "edge_role": EDGE_RECEIVER_ROLE,
            "projector_role": "star_oam_projector",
        },
    )


def _provision_and_verify_oam_work_order_source() -> None:
    _run_deployment_sql(
        "provision_oam_work_order_source.sql",
        variables=TEST_OAM_SOURCE_COORDINATES,
    )
    # Exact replay is idempotent, while a different reviewed coordinate must
    # never overwrite the existing source registration.
    _run_deployment_sql(
        "provision_oam_work_order_source.sql",
        variables=TEST_OAM_SOURCE_COORDINATES,
    )
    mismatched = {
        **TEST_OAM_SOURCE_COORDINATES,
        "company_id": "pg16-different-company",
    }
    blocked = _run_deployment_sql(
        "provision_oam_work_order_source.sql",
        variables=mismatched,
        expect_success=False,
    )
    assert "no automatic overwrite performed" in (
        blocked.stdout + blocked.stderr
    )
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT mode, enabled, configuration_jsonb "
                "FROM source_systems WHERE code = 'starcharge_oam'"
            )
            assert cursor.fetchone() == (
                "read_only",
                True,
                {
                    "projection_schema": "rsc.oam_work_order_projection.v1",
                    "edge_source_instance": "pg16-reviewed-edge",
                    "work_order_company_id": "pg16-reviewed-company",
                    "work_order_org_code": "pg16-reviewed-org",
                    "work_order_scope_key": "work-orders:recent-30d",
                },
            )


def _assert_raw_sql_denied(
    role_name: str,
    statement: str,
    parameters: tuple[object, ...],
    *,
    require_rls: bool = True,
) -> None:
    with psycopg.connect(
        **_connection_parameters(
            role=role_name,
            password=_role_password(role_name),
        )
    ) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg.Error) as error:
                cursor.execute(statement, parameters)
            assert error.value.sqlstate == "42501"
            if require_rls:
                assert "row-level security" in str(error.value).lower()
        connection.rollback()


def _seed_0044_unbound_source() -> uuid.UUID:
    source_id = uuid.uuid4()
    now = PROJECTOR_GATE_SOURCE_TIME - timedelta(hours=1)
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.source_systems "
                "(id, code, name, mode, enabled, configuration_jsonb, "
                "created_at, updated_at) "
                "VALUES (%s, %s, %s, 'read_only', true, %s::jsonb, %s, %s)",
                (
                    source_id,
                    "pg16-unbound-source",
                    "PG16 unbound attack source",
                    "{}",
                    now,
                    now,
                ),
            )
        connection.commit()
    return source_id


def _edge_snapshot_insert_sql() -> str:
    return (
        "INSERT INTO public.external_sync_snapshots "
        "(id, source_system, source_instance, snapshot_id, scope_key, "
        "sync_mode, company_id, org_code, snapshot_at, status, "
        "manifest_json, manifest_sha256, received_at, completed_at) "
        "VALUES (%s, %s, %s, %s, %s, 'full', %s, %s, %s, "
        "'receiving', '', '', %s, NULL)"
    )


def _edge_snapshot_parameters(
    *,
    source_system: str = "starcharge_oam",
    source_instance: str | None = None,
    scope_key: str | None = None,
    company_id: str | None = None,
    org_code: str | None = None,
) -> tuple[object, ...]:
    snapshot_at = PROJECTOR_GATE_SOURCE_TIME - timedelta(minutes=30)
    return (
        str(uuid.uuid4()),
        source_system,
        source_instance or TEST_OAM_SOURCE_COORDINATES["edge_source_instance"],
        f"pg16-rls-{uuid.uuid4().hex}",
        scope_key or TEST_OAM_SOURCE_COORDINATES["scope_key"],
        company_id or TEST_OAM_SOURCE_COORDINATES["company_id"],
        org_code or TEST_OAM_SOURCE_COORDINATES["org_code"],
        snapshot_at,
        snapshot_at,
    )


def _assert_0044_zero_binding_default_denies(unbound_source_id: uuid.UUID) -> None:
    for role_name in (EDGE_RECEIVER_ROLE, "star_oam_projector"):
        with psycopg.connect(
            **_connection_parameters(
                role=role_name,
                password=_role_password(role_name),
            )
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT {RLS_READY_FUNCTION}")
                assert cursor.fetchone()[0] is False

    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_projector",
            password=_role_password("star_oam_projector"),
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM public.source_systems")
            assert cursor.fetchone()[0] == 0

    _assert_raw_sql_denied(
        EDGE_RECEIVER_ROLE,
        _edge_snapshot_insert_sql(),
        _edge_snapshot_parameters(),
    )
    now = PROJECTOR_GATE_SOURCE_TIME - timedelta(minutes=20)
    _assert_raw_sql_denied(
        "star_oam_projector",
        "INSERT INTO public.sync_runs "
        "(id, source_system_id, run_key, scope_key, mode, status, "
        "started_at, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, 'full', 'pending', %s, %s, %s)",
        (
            uuid.uuid4(),
            unbound_source_id,
            f"pg16-zero-binding-{uuid.uuid4().hex}",
            "oam-work-order-scope:" + "0" * 64,
            now,
            now,
            now,
        ),
    )


def _formal_scope_key() -> str:
    coordinates = TEST_OAM_SOURCE_COORDINATES
    digest = hashlib.sha256(
        (
            coordinates["edge_source_instance"]
            + "\0"
            + coordinates["scope_key"]
        ).encode("utf-8")
    ).hexdigest()
    return f"oam-work-order-scope:{digest}"


def _assert_0044_bound_scope_attack_matrix(
    unbound_source_id: uuid.UUID,
) -> None:
    coordinates = TEST_OAM_SOURCE_COORDINATES
    expected_bindings = {
        (EDGE_RECEIVER_ROLE, "edge_ingress", "work_order"),
        ("star_oam_projector", "projector_read", "employee"),
        ("star_oam_projector", "projector_read", "work_order"),
        ("star_oam_projector", "projector_write", "work_order"),
    }
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT principal_name, capability, entity_type, "
                "source_system, source_instance, scope_key, company_id, "
                "org_code, formal_scope_key, enabled "
                f"FROM public.{RLS_BINDING_TABLE}"
            )
            rows = cursor.fetchall()
            assert {
                (row[0], row[1], row[2]) for row in rows
            } == expected_bindings
            assert len(rows) == 4
            assert all(
                row[3:8]
                == (
                    "starcharge_oam",
                    coordinates["edge_source_instance"],
                    coordinates["scope_key"],
                    coordinates["company_id"],
                    coordinates["org_code"],
                )
                for row in rows
            )
            assert all(row[8] == _formal_scope_key() for row in rows)
            assert all(row[9] is True for row in rows)
            cursor.execute(
                "SELECT id FROM public.source_systems "
                "WHERE code = 'starcharge_oam'"
            )
            source_id = cursor.fetchone()[0]

    for role_name in (EDGE_RECEIVER_ROLE, "star_oam_projector"):
        with psycopg.connect(
            **_connection_parameters(
                role=role_name,
                password=_role_password(role_name),
            )
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(f"SELECT {RLS_READY_FUNCTION}")
                assert cursor.fetchone()[0] is True

    legal_parameters = _edge_snapshot_parameters()
    legal_snapshot_id = str(legal_parameters[0])
    with psycopg.connect(
        **_connection_parameters(
            role=EDGE_RECEIVER_ROLE,
            password=_role_password(EDGE_RECEIVER_ROLE),
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(_edge_snapshot_insert_sql(), legal_parameters)
        connection.commit()

    for mutation in (
        {"source_system": "not-starcharge-oam"},
        {"source_instance": "pg16-wrong-edge"},
        {"scope_key": "work-orders:recent-31d"},
        {"company_id": "pg16-wrong-company"},
        {"org_code": "pg16-wrong-org"},
    ):
        _assert_raw_sql_denied(
            EDGE_RECEIVER_ROLE,
            _edge_snapshot_insert_sql(),
            _edge_snapshot_parameters(**mutation),
        )

    _assert_raw_sql_denied(
        EDGE_RECEIVER_ROLE,
        "INSERT INTO public.external_sync_snapshot_batches "
        "(id, snapshot_ref_id, source_instance, batch_id, entity_type, "
        "sequence, total_sequences, record_count, body_sha256, received_at) "
        "VALUES (%s, %s, %s, %s, 'employee', 1, 1, 0, %s, %s)",
        (
            str(uuid.uuid4()),
            legal_snapshot_id,
            coordinates["edge_source_instance"],
            f"pg16-wrong-entity-{uuid.uuid4().hex}",
            "0" * 64,
            PROJECTOR_GATE_SOURCE_TIME,
        ),
    )

    now = PROJECTOR_GATE_SOURCE_TIME
    for source_or_scope in (
        (unbound_source_id, _formal_scope_key()),
        (source_id, "oam-work-order-scope:" + "f" * 64),
    ):
        _assert_raw_sql_denied(
            "star_oam_projector",
            "INSERT INTO public.sync_runs "
            "(id, source_system_id, run_key, scope_key, mode, status, "
            "started_at, created_at, updated_at) "
            "VALUES (%s, %s, %s, %s, 'full', 'pending', %s, %s, %s)",
            (
                uuid.uuid4(),
                source_or_scope[0],
                f"pg16-cross-boundary-{uuid.uuid4().hex}",
                source_or_scope[1],
                now,
                now,
                now,
            ),
        )

    for role_name in (EDGE_RECEIVER_ROLE, "star_oam_projector"):
        with psycopg.connect(
            **_connection_parameters(
                role=role_name,
                password=_role_password(role_name),
            )
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SET row_security = off")
                with pytest.raises(psycopg.Error) as error:
                    cursor.execute(
                        "SELECT count(*) FROM public.external_sync_snapshots"
                    )
                assert error.value.sqlstate == "42501"
                assert "row-level security" in str(error.value).lower()
            connection.rollback()


def _assert_0044_downgrade_revokes_runtime_writes() -> None:
    assert _current_revision() == "20260902_0043"
    _assert_raw_sql_denied(
        EDGE_RECEIVER_ROLE,
        _edge_snapshot_insert_sql(),
        _edge_snapshot_parameters(),
        require_rls=False,
    )
    now = PROJECTOR_GATE_SOURCE_TIME
    _assert_raw_sql_denied(
        "star_oam_projector",
        "INSERT INTO public.sync_runs "
        "(id, source_system_id, run_key, scope_key, mode, status, "
        "started_at, created_at, updated_at) "
        "VALUES (%s, %s, %s, %s, 'full', 'pending', %s, %s, %s)",
        (
            uuid.uuid4(),
            uuid.uuid4(),
            f"pg16-downgrade-{uuid.uuid4().hex}",
            _formal_scope_key(),
            now,
            now,
            now,
        ),
        require_rls=False,
    )


def _assert_0044_rejects_nonempty_sync_downgrade() -> None:
    assert _current_revision() == RLS_REVISION
    blocked = _run_alembic(
        "downgrade", "20260902_0043", expect_success=False
    )
    assert "0044 requires an empty OAM sync graph" in (
        blocked.stdout + blocked.stderr
    )
    assert _current_revision() == RLS_REVISION
    assert _table_exists(RLS_BINDING_TABLE) is True


def _clear_disposable_oam_sync_graph() -> None:
    """Reset only the CI service database so downgrade closure can be tested."""

    assert _gate_enabled()
    statements = (
        "DELETE FROM public.sync_conflicts",
        "DELETE FROM public.oam_work_orders",
        "DELETE FROM public.external_object_mappings",
        "UPDATE public.people SET external_object_id = NULL "
        "WHERE external_object_id IS NOT NULL",
        "UPDATE public.organizations SET external_object_id = NULL "
        "WHERE external_object_id IS NOT NULL",
        "DELETE FROM public.external_object_versions",
        "DELETE FROM public.external_objects",
        "DELETE FROM public.sync_inbox_events",
        "DELETE FROM public.sync_batches",
        "DELETE FROM public.sync_runs",
        "DELETE FROM public.external_sync_current_records",
        "DELETE FROM public.external_sync_snapshot_records",
        "DELETE FROM public.external_sync_snapshot_batches",
        "DELETE FROM public.external_sync_snapshots",
    )
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
    ) as connection:
        with connection.cursor() as cursor:
            for statement in statements:
                cursor.execute(statement)
        connection.commit()


def _projector_gate_canonical(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def _projector_gate_sha256(value: object) -> str:
    return hashlib.sha256(
        _projector_gate_canonical(value).encode("utf-8")
    ).hexdigest()


def _assert_projector_raw_evidence_attack_matrix(
    *,
    run_id: uuid.UUID,
    batch_id: uuid.UUID,
    source_id: uuid.UUID,
    inbox_external_event_id: str,
    inbox_external_id: str,
    inbox_source_version: str,
    inbox_source_updated_at: datetime,
    inbox_payload: dict[str, object],
    inbox_payload_sha256: str,
    external_object_id: uuid.UUID,
    external_version_source_version: str,
    external_version_source_updated_at: datetime,
    external_version_payload: dict[str, object],
    external_version_payload_sha256: str,
    work_order_id: uuid.UUID,
    organization_id: uuid.UUID,
) -> None:
    """Prove raw projector SQL cannot detach formal rows from edge evidence."""

    projector_role = "star_oam_projector"
    created_at = PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=8)

    _assert_raw_sql_denied(
        projector_role,
        "INSERT INTO public.sync_batches "
        "(id, run_id, entity_type, sequence, record_count, body_sha256, "
        "status, created_at) "
        "VALUES (%s, %s, 'employee', 2, 0, %s, 'receiving', %s)",
        (uuid.uuid4(), run_id, "0" * 64, created_at),
    )

    def event_parameters(
        *,
        external_event_id: str = inbox_external_event_id,
        entity_type: str = "work_order",
        external_id: str = inbox_external_id,
        source_version: str = inbox_source_version,
        source_updated_at: datetime = inbox_source_updated_at,
        payload: dict[str, object] = inbox_payload,
        payload_sha256: str = inbox_payload_sha256,
    ) -> tuple[object, ...]:
        return (
            uuid.uuid4(),
            batch_id,
            source_id,
            external_event_id,
            entity_type,
            external_id,
            source_version,
            source_updated_at,
            _projector_gate_canonical(payload),
            payload_sha256,
            created_at,
        )

    event_insert = (
        "INSERT INTO public.sync_inbox_events "
        "(id, batch_id, source_system_id, external_event_id, entity_type, "
        "external_id, source_version, source_updated_at, payload_jsonb, "
        "payload_sha256, status, error_code, error_detail, processed_at, "
        "created_at) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, "
        "%s::jsonb, %s, 'validated', NULL, NULL, NULL, %s)"
    )

    alternate_payload = {**inbox_payload, "statusCode": "closed"}
    alternate_payload_hash = _projector_gate_sha256(alternate_payload)
    wrong_company_payload = {
        **inbox_payload,
        "authCompanyId": "pg16-forged-company",
    }
    wrong_company_hash = _projector_gate_sha256(wrong_company_payload)
    wrong_id_payload = {**inbox_payload, "id": "pg16-forged-work-order"}
    wrong_id_hash = _projector_gate_sha256(wrong_id_payload)
    wrong_time = inbox_source_updated_at + timedelta(minutes=1)
    wrong_time_payload = {
        **inbox_payload,
        "updateTime": wrong_time.astimezone(
            timezone(timedelta(hours=8))
        ).strftime("%Y-%m-%d %H:%M:%S"),
    }
    wrong_time_hash = _projector_gate_sha256(wrong_time_payload)
    inbox_attacks = {
        "event_id": event_parameters(
            external_event_id=f"{inbox_external_event_id}:forged"
        ),
        "payload": event_parameters(
            payload=alternate_payload,
            payload_sha256=alternate_payload_hash,
            source_version=(
                f"wo-v1:{inbox_source_updated_at.isoformat()}:"
                f"{alternate_payload_hash}"
            ),
        ),
        "company": event_parameters(
            payload=wrong_company_payload,
            payload_sha256=wrong_company_hash,
            source_version=(
                f"wo-v1:{inbox_source_updated_at.isoformat()}:"
                f"{wrong_company_hash}"
            ),
        ),
        "external_id": event_parameters(
            external_id="pg16-forged-work-order",
            payload=wrong_id_payload,
            payload_sha256=wrong_id_hash,
            source_version=(
                f"wo-v1:{inbox_source_updated_at.isoformat()}:"
                f"{wrong_id_hash}"
            ),
        ),
        "payload_hash": event_parameters(payload_sha256="f" * 64),
        "source_time": event_parameters(
            source_updated_at=wrong_time,
            payload=wrong_time_payload,
            payload_sha256=wrong_time_hash,
            source_version=f"wo-v1:{wrong_time.isoformat()}:{wrong_time_hash}",
        ),
        "entity_type": event_parameters(
            external_event_id=f"{inbox_external_event_id}:employee",
            entity_type="employee",
        ),
    }
    for attack_name, parameters in inbox_attacks.items():
        try:
            _assert_raw_sql_denied(projector_role, event_insert, parameters)
        except AssertionError as exc:
            raise AssertionError(
                "projector accepted forged sync_inbox_events evidence: "
                f"{attack_name}"
            ) from exc

    version_insert = (
        "INSERT INTO public.external_object_versions "
        "(id, external_object_id, source_version, source_updated_at, "
        "valid_from, valid_to, payload_jsonb, payload_sha256, is_current, "
        "created_at) VALUES (%s, %s, %s, %s, %s, %s, %s::jsonb, %s, "
        "false, %s)"
    )
    valid_from = created_at + timedelta(seconds=1)
    valid_to = valid_from + timedelta(seconds=1)

    def version_parameters(
        *,
        source_version: str = external_version_source_version,
        source_updated_at: datetime = external_version_source_updated_at,
        payload: dict[str, object] = external_version_payload,
        payload_sha256: str = external_version_payload_sha256,
    ) -> tuple[object, ...]:
        return (
            uuid.uuid4(),
            external_object_id,
            source_version,
            source_updated_at,
            valid_from,
            valid_to,
            _projector_gate_canonical(payload),
            payload_sha256,
            created_at,
        )

    forged_projection_payload = {
        **external_version_payload,
        "status": "closed",
    }
    forged_projection_hash = _projector_gate_sha256(
        forged_projection_payload
    )
    version_attacks = {
        "payload": version_parameters(
            payload=forged_projection_payload,
            payload_sha256=forged_projection_hash,
        ),
        "payload_hash": version_parameters(payload_sha256="e" * 64),
        "source_time": version_parameters(
            source_version=f"{external_version_source_version}:time",
            source_updated_at=(
                external_version_source_updated_at + timedelta(minutes=1)
            ),
        ),
        "source_version": version_parameters(
            source_version="wo-v2:" + "d" * 64 + ":" + "c" * 64
        ),
    }
    for attack_name, parameters in version_attacks.items():
        try:
            _assert_raw_sql_denied(projector_role, version_insert, parameters)
        except AssertionError as exc:
            raise AssertionError(
                "projector accepted forged external_object_versions evidence: "
                f"{attack_name}"
            ) from exc

    decoy_person_id = uuid.uuid4()
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.people "
                "(id, external_object_id, organization_id, employee_no, "
                "name, mobile_encrypted, mobile_hash, employment_status, "
                "source_updated_at, created_at, updated_at) "
                "VALUES (%s, NULL, %s, %s, %s, NULL, NULL, 'active', "
                "%s, %s, %s)",
                (
                    decoy_person_id,
                    organization_id,
                    f"PG16-DECOY-{decoy_person_id.hex[:12]}",
                    "PG16未映射伪造执行人",
                    created_at,
                    created_at,
                    created_at,
                ),
            )
        connection.commit()

    try:
        work_order_attacks = {
            "status": (
                "UPDATE public.oam_work_orders SET status = 'closed' "
                "WHERE id = %s",
                (work_order_id,),
            ),
            "source_updated_at": (
                "UPDATE public.oam_work_orders "
                "SET source_updated_at = source_updated_at + interval '1 minute' "
                "WHERE id = %s",
                (work_order_id,),
            ),
            "person_mapping": (
                "UPDATE public.oam_work_orders SET engineer_person_id = %s "
                "WHERE id = %s",
                (decoy_person_id, work_order_id),
            ),
        }
        for attack_name, (statement, parameters) in work_order_attacks.items():
            try:
                _assert_raw_sql_denied(
                    projector_role,
                    statement,
                    parameters,
                )
            except AssertionError as exc:
                raise AssertionError(
                    "projector accepted forged oam_work_orders evidence: "
                    f"{attack_name}"
                ) from exc
    finally:
        with psycopg.connect(
            **_connection_parameters(
                role="star_oam_migrator",
                password=_role_password("star_oam_migrator"),
            )
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "DELETE FROM public.people WHERE id = %s",
                    (decoy_person_id,),
                )
            connection.commit()


def _seed_pg16_projector_identity(migrator_engine) -> tuple[uuid.UUID, ...]:
    from app.formal_services import oam_work_order_projection as service
    from app.foundation_models import (
        ExternalObject,
        ExternalObjectMapping,
        ExternalObjectVersion,
        Organization,
        Person,
        SourceSystem,
    )
    from app.models import User

    evidence_time = PROJECTOR_GATE_SOURCE_TIME - timedelta(minutes=10)
    with Session(migrator_engine, expire_on_commit=False) as session:
        source = session.scalar(
            select(SourceSystem).where(
                SourceSystem.code == service.SOURCE_SYSTEM_CODE
            )
        )
        assert source is not None
        assert source.mode == service.SOURCE_SYSTEM_MODE
        assert source.enabled is True
        assert source.configuration_jsonb == {
            "projection_schema": service.PROJECTION_SCHEMA,
            "edge_source_instance": TEST_OAM_SOURCE_COORDINATES[
                "edge_source_instance"
            ],
            "work_order_company_id": TEST_OAM_SOURCE_COORDINATES[
                "company_id"
            ],
            "work_order_org_code": TEST_OAM_SOURCE_COORDINATES["org_code"],
            "work_order_scope_key": TEST_OAM_SOURCE_COORDINATES["scope_key"],
        }

        root = Organization(
            external_object_id=None,
            code=TEST_OAM_SOURCE_COORDINATES["org_code"],
            name="PG16 OAM正式投影组织根",
            parent_id=None,
            org_type="region_company",
            province_code="CN330000",
            status="active",
            created_at=evidence_time,
            updated_at=evidence_time,
        )
        session.add(root)
        session.flush()
        child = Organization(
            external_object_id=None,
            code="PG16-PROJECTOR-CHILD",
            name="PG16 OAM正式投影工程部门",
            parent_id=root.id,
            org_type="department",
            province_code="CN330000",
            status="active",
            created_at=evidence_time,
            updated_at=evidence_time,
        )
        session.add(child)
        session.flush()
        person = Person(
            external_object_id=None,
            organization_id=child.id,
            employee_no="PG16-PROJECTOR-EMP",
            name="PG16正式投影工程师",
            mobile_encrypted=None,
            mobile_hash=None,
            employment_status="active",
            source_updated_at=evidence_time,
            created_at=evidence_time,
            updated_at=evidence_time,
        )
        session.add(person)
        employee_version_id = uuid.uuid4()
        employee = ExternalObject(
            source_system_id=source.id,
            entity_type=service.EMPLOYEE_ENTITY,
            external_id=PROJECTOR_GATE_EXECUTOR_ID,
            current_version_id=employee_version_id,
            deleted_at=None,
            created_at=evidence_time,
            updated_at=evidence_time,
        )
        session.add(employee)
        session.flush()
        employee_payload = {
            "accountId": PROJECTOR_GATE_EXECUTOR_ID,
            "companyId": TEST_OAM_SOURCE_COORDINATES["company_id"],
            "orgCode": TEST_OAM_SOURCE_COORDINATES["org_code"],
            "status": "active",
        }
        session.add(
            ExternalObjectVersion(
                id=employee_version_id,
                external_object_id=employee.id,
                source_version="pg16-projector-employee-v1",
                source_updated_at=evidence_time,
                valid_from=evidence_time,
                valid_to=None,
                payload_jsonb=employee_payload,
                payload_sha256=_projector_gate_sha256(employee_payload),
                is_current=True,
                created_at=evidence_time,
            )
        )
        approver = User(
            id=str(uuid.uuid4()),
            person_id=None,
            account_status="active",
            last_login_at=None,
            authorization_version=1,
            mobile="13900001601",
            name="PG16映射审批管理员",
            password_hash="not-used-in-pg16-release-gate",
            role="admin",
            province=None,
            is_active=True,
            require_password_change=False,
            created_at=evidence_time,
            updated_at=evidence_time,
        )
        session.add(approver)
        session.flush()
        session.add(
            ExternalObjectMapping(
                external_object_id=employee.id,
                local_object_type="person",
                local_object_id=str(person.id),
                status="approved",
                approved_by=approver.id,
                approved_at=evidence_time,
                reason="PG16发布门显式人员映射",
                created_at=evidence_time,
                updated_at=evidence_time,
            )
        )
        session.commit()
        return root.id, child.id, person.id, source.id


def _projector_gate_payload(
    *,
    status: str,
    source_updated_at: datetime = PROJECTOR_GATE_SOURCE_TIME,
) -> dict[str, object]:
    return {
        "id": PROJECTOR_GATE_WORK_ORDER_ID,
        "code": PROJECTOR_GATE_WORK_ORDER_NO,
        "statusCode": status,
        "executorId": PROJECTOR_GATE_EXECUTOR_ID,
        "authCompanyId": TEST_OAM_SOURCE_COORDINATES["company_id"],
        "province": "浙江省",
        "updateTime": source_updated_at.astimezone(
            timezone(timedelta(hours=8))
        ).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _stage_pg16_projector_snapshot(
    migrator_engine,
    *,
    external_snapshot_id: str,
    status: str,
    snapshot_at: datetime,
    source_updated_at: datetime = PROJECTOR_GATE_SOURCE_TIME,
    poison_manifest: bool = False,
) -> str:
    from app.models import (
        ExternalSyncCurrentRecord,
        ExternalSyncSnapshot,
        ExternalSyncSnapshotBatch,
        ExternalSyncSnapshotRecord,
    )
    from app.schemas import EdgeSyncSnapshotCompleteIn

    payload = _projector_gate_payload(
        status=status,
        source_updated_at=source_updated_at,
    )
    payload_json = _projector_gate_canonical(payload)
    payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    business_key = f"work-order:{PROJECTOR_GATE_WORK_ORDER_NO}"
    final_wire = [
        {
            "business_key": business_key,
            "source_updated_at": source_updated_at.isoformat(),
            "data": payload,
        }
    ]
    delta_wire = [{**final_wire[0], "operation": "upsert"}]
    final_sha256 = _projector_gate_sha256(final_wire)
    delta_sha256 = _projector_gate_sha256(delta_wire)
    completed_at = snapshot_at + timedelta(minutes=1)

    with Session(migrator_engine, expire_on_commit=False) as session:
        snapshot = ExternalSyncSnapshot(
            source_system="starcharge_oam",
            source_instance=TEST_OAM_SOURCE_COORDINATES[
                "edge_source_instance"
            ],
            snapshot_id=external_snapshot_id,
            scope_key=TEST_OAM_SOURCE_COORDINATES["scope_key"],
            sync_mode="full",
            company_id=TEST_OAM_SOURCE_COORDINATES["company_id"],
            org_code=TEST_OAM_SOURCE_COORDINATES["org_code"],
            snapshot_at=snapshot_at,
            status="complete",
            manifest_json="pending",
            manifest_sha256="0" * 64,
            received_at=snapshot_at,
            completed_at=completed_at,
        )
        session.add(snapshot)
        session.flush()
        session.add(
            ExternalSyncSnapshotBatch(
                snapshot_ref_id=snapshot.id,
                source_instance=snapshot.source_instance,
                batch_id=f"batch-{external_snapshot_id}",
                entity_type="work_order",
                sequence=1,
                total_sequences=1,
                record_count=1,
                body_sha256=delta_sha256,
                received_at=snapshot_at,
            )
        )
        session.add(
            ExternalSyncSnapshotRecord(
                snapshot_ref_id=snapshot.id,
                entity_type="work_order",
                business_key=business_key,
                operation="upsert",
                source_updated_at=source_updated_at,
                payload_json=payload_json,
                payload_sha256=payload_sha256,
            )
        )
        current = session.scalar(
            select(ExternalSyncCurrentRecord).where(
                ExternalSyncCurrentRecord.source_instance
                == snapshot.source_instance,
                ExternalSyncCurrentRecord.scope_key == snapshot.scope_key,
                ExternalSyncCurrentRecord.entity_type == "work_order",
                ExternalSyncCurrentRecord.business_key == business_key,
            )
        )
        if current is None:
            current = ExternalSyncCurrentRecord(
                source_system="starcharge_oam",
                source_instance=snapshot.source_instance,
                scope_key=snapshot.scope_key,
                entity_type="work_order",
                business_key=business_key,
                source_updated_at=source_updated_at,
                payload_json=payload_json,
                payload_sha256=payload_sha256,
                last_snapshot_id=snapshot.id,
                created_at=snapshot_at,
                updated_at=snapshot_at,
            )
            session.add(current)
        else:
            current.source_updated_at = source_updated_at
            current.payload_json = payload_json
            current.payload_sha256 = payload_sha256
            current.last_snapshot_id = snapshot.id
            current.updated_at = snapshot_at
        manifest = EdgeSyncSnapshotCompleteIn(
            snapshot_id=external_snapshot_id,
            scope_key=snapshot.scope_key,
            sync_mode="full",
            company_id=snapshot.company_id,
            org_code=snapshot.org_code,
            snapshot_at=snapshot_at,
            entities=[
                {
                    "entity_type": "work_order",
                    "final_record_count": 1,
                    "final_sha256": final_sha256,
                    "delta_record_count": 1,
                    "delta_sha256": delta_sha256,
                    "batch_count": 1,
                }
            ],
        )
        snapshot.manifest_json = _projector_gate_canonical(
            manifest.model_dump(mode="json")
        )
        snapshot.manifest_sha256 = (
            "f" * 64
            if poison_manifest
            else hashlib.sha256(
                snapshot.manifest_json.encode("utf-8")
            ).hexdigest()
        )
        session.flush()
        internal_snapshot_id = snapshot.id
        session.commit()
        return internal_snapshot_id


def _remap_pg16_projector_identity(
    migrator_engine,
    *,
    organization_id: uuid.UUID,
    remapped_at: datetime,
) -> uuid.UUID:
    from app.formal_services import oam_work_order_projection as service
    from app.foundation_models import (
        ExternalObject,
        ExternalObjectMapping,
        Person,
    )
    from app.models import User

    with Session(migrator_engine, expire_on_commit=False) as session:
        employee = session.scalar(
            select(ExternalObject).where(
                ExternalObject.entity_type == service.EMPLOYEE_ENTITY,
                ExternalObject.external_id == PROJECTOR_GATE_EXECUTOR_ID,
            )
        )
        assert employee is not None
        mapping = session.scalar(
            select(ExternalObjectMapping).where(
                ExternalObjectMapping.external_object_id == employee.id,
                ExternalObjectMapping.local_object_type == "person",
                ExternalObjectMapping.status == "approved",
            )
        )
        assert mapping is not None
        mapping.status = "superseded"
        mapping.updated_at = remapped_at

        replacement = Person(
            external_object_id=None,
            organization_id=organization_id,
            employee_no="PG16-PROJECTOR-EMP-REMAPPED",
            name="PG16重新审批映射工程师",
            mobile_encrypted=None,
            mobile_hash=None,
            employment_status="active",
            source_updated_at=remapped_at,
            created_at=remapped_at,
            updated_at=remapped_at,
        )
        approver = User(
            id=str(uuid.uuid4()),
            person_id=None,
            account_status="active",
            last_login_at=None,
            authorization_version=1,
            mobile="13900001602",
            name="PG16重新映射审批管理员",
            password_hash="not-used-in-pg16-release-gate",
            role="admin",
            province=None,
            is_active=True,
            require_password_change=False,
            created_at=remapped_at,
            updated_at=remapped_at,
        )
        session.add_all((replacement, approver))
        session.flush()
        session.add(
            ExternalObjectMapping(
                external_object_id=employee.id,
                local_object_type="person",
                local_object_id=str(replacement.id),
                status="approved",
                approved_by=approver.id,
                approved_at=remapped_at,
                reason="PG16发布门映射单独变更",
                created_at=remapped_at,
                updated_at=remapped_at,
            )
        )
        session.commit()
        return replacement.id


def _assert_pg16_projection_chain_rejects_partial_updates(
    *,
    external_object_id: uuid.UUID,
    closed_version_id: uuid.UUID,
    closed_at: datetime,
    current_version_id: uuid.UUID,
    close_at: datetime,
) -> None:
    projector_parameters = _connection_parameters(
        role="star_oam_projector",
        password=_role_password("star_oam_projector"),
    )

    # A closed history row is immutable.  UPDATE USING intentionally hides it
    # from the version-transition policy, so neither rewriting its close time
    # nor reactivating it may affect a row.
    with psycopg.connect(**projector_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.external_object_versions "
                "SET valid_to = valid_to + interval '1 minute' "
                "WHERE id = %s RETURNING id",
                (closed_version_id,),
            )
            assert cursor.fetchone() is None
            cursor.execute(
                "UPDATE public.external_object_versions "
                "SET is_current = true, valid_to = NULL "
                "WHERE id = %s RETURNING id",
                (closed_version_id,),
            )
            assert cursor.fetchone() is None
            cursor.execute(
                "SELECT valid_to, is_current "
                "FROM public.external_object_versions WHERE id = %s",
                (closed_version_id,),
            )
            assert cursor.fetchone() == (closed_at, False)
        connection.commit()

    # The pointer-null form is rejected even before the deferred trigger by the
    # new-row RLS predicate.  It must never become an observable intermediate
    # state or reach commit.
    with psycopg.connect(**projector_parameters) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg.Error) as pointer_error:
                cursor.execute(
                    "UPDATE public.external_objects "
                    "SET current_version_id = NULL, updated_at = %s "
                    "WHERE id = %s",
                    (close_at, external_object_id),
                )
            assert pointer_error.value.sqlstate == "42501"
            assert "row-level security" in str(pointer_error.value).lower()
        connection.rollback()

    # Closing the pointed-to version is individually valid to the row policy,
    # so the statement succeeds while constraints remain deferred.  Forcing the
    # constraint proves the cross-row pointer/version chain blocks the partial
    # lifecycle before commit.
    with psycopg.connect(**projector_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.external_object_versions "
                "SET is_current = false, valid_to = %s WHERE id = %s "
                "RETURNING id",
                (close_at, current_version_id),
            )
            assert cursor.fetchone() == (current_version_id,)
            with pytest.raises(psycopg.Error) as version_error:
                cursor.execute(
                    "SET CONSTRAINTS "
                    "trg_external_object_versions_chain_0044 IMMEDIATE"
                )
            assert version_error.value.sqlstate == "42501"
            assert "0044 work-order current-version pointer is invalid" in str(
                version_error.value
            )
        connection.rollback()


def _assert_projector_real_publish_paths(projector_engine) -> None:
    from app.demand_models import OamWorkOrder
    from app.formal_services import oam_work_order_projection as service
    from app.foundation_models import (
        ExternalObject,
        ExternalObjectVersion,
        Organization,
        SourceSystem,
        SyncBatch,
        SyncConflict,
        SyncInboxEvent,
        SyncRun,
    )

    migrator_engine = create_engine(
        _sqlalchemy_url(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        ),
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    try:
        root_id, child_id, person_id, source_id = (
            _seed_pg16_projector_identity(migrator_engine)
        )
        initial_payload = _projector_gate_payload(status="processing")
        assert set(initial_payload) == set(service.WORK_ORDER_PAYLOAD_FIELDS)
        first_snapshot_id = _stage_pg16_projector_snapshot(
            migrator_engine,
            external_snapshot_id="pg16-projector-snapshot-publish",
            status="processing",
            snapshot_at=PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=5),
        )

        first_publish_at = PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=7)
        with Session(projector_engine, expire_on_commit=False) as session:
            assert session.execute(
                select(
                    func.current_user(),
                    func.session_user(),
                )
            ).one() == ("star_oam_projector", "star_oam_projector")
            first = service.publish_completed_work_order_snapshot(
                session,
                snapshot_id=first_snapshot_id,
                now=first_publish_at,
            )
            assert first.status == "completed"
            assert first.projected_records == 1
            assert first.created_records == 1
            assert first.duplicate is False
            session.commit()

        with Session(projector_engine) as session:
            source = session.get(SourceSystem, source_id)
            run = session.get(SyncRun, first.sync_run_id)
            assert source is not None and run is not None
            assert run.source_system_id == source.id
            assert run.status == "completed"
            assert run.failure_code is None
            assert run.failure_detail is None
            batch = session.scalar(
                select(SyncBatch).where(SyncBatch.run_id == run.id)
            )
            assert batch is not None and batch.status == "applied"
            event = session.scalar(
                select(SyncInboxEvent).where(
                    SyncInboxEvent.batch_id == batch.id
                )
            )
            assert event is not None and event.status == "applied"
            assert event.payload_jsonb == initial_payload
            assert set(event.payload_jsonb) == set(
                service.WORK_ORDER_PAYLOAD_FIELDS
            )
            assert event.payload_sha256 == _projector_gate_sha256(
                initial_payload
            )
            assert event.error_code is None and event.error_detail is None
            row = session.scalar(
                select(OamWorkOrder).where(
                    OamWorkOrder.work_order_no == PROJECTOR_GATE_WORK_ORDER_NO
                )
            )
            assert row is not None
            assert row.organization_id == child_id
            assert row.engineer_person_id == person_id
            assert row.status == "active"
            assert service._aware(row.source_updated_at) == (
                PROJECTOR_GATE_SOURCE_TIME
            )
            assert service._aware(row.updated_at) == first_publish_at
            child = session.get(Organization, child_id)
            root = session.get(Organization, root_id)
            assert child is not None and root is not None
            assert child.parent_id == root.id
            assert root.code == TEST_OAM_SOURCE_COORDINATES["org_code"]
            external = session.get(ExternalObject, row.external_object_id)
            assert external is not None
            assert external.external_id == PROJECTOR_GATE_WORK_ORDER_ID
            version = session.get(
                ExternalObjectVersion, external.current_version_id
            )
            assert version is not None and version.is_current is True
            assert version.source_version.startswith(
                service.PROJECTION_SOURCE_VERSION_PREFIX
            )
            source_version_parts = version.source_version.split(":")
            assert len(source_version_parts) == 3
            assert source_version_parts[1] == _projector_gate_sha256(
                initial_payload
            )
            assert len(source_version_parts[2]) == 64
            expected_projection_payload = {
                "work_order_no": PROJECTOR_GATE_WORK_ORDER_NO,
                "organization_id": str(child_id),
                "engineer_person_id": str(person_id),
                "status": "active",
            }
            assert version.payload_jsonb == expected_projection_payload
            assert version.payload_sha256 == _projector_gate_sha256(
                expected_projection_payload
            )
            assert event.source_version is not None
            assert event.source_updated_at is not None
            assert version.source_updated_at is not None
            stable_batch_id = batch.id
            stable_event_external_event_id = event.external_event_id
            stable_event_external_id = event.external_id
            stable_event_source_version = event.source_version
            stable_event_source_updated_at = event.source_updated_at
            stable_event_payload = dict(event.payload_jsonb)
            stable_event_payload_sha256 = event.payload_sha256
            stable_row_id = row.id
            stable_external_id = external.id
            stable_version_id = version.id
            stable_version_source_version = version.source_version
            stable_version_source_updated_at = version.source_updated_at
            stable_version_payload = dict(version.payload_jsonb)
            stable_version_payload_sha256 = version.payload_sha256
            stable_updated_at = row.updated_at

        with Session(projector_engine, expire_on_commit=False) as session:
            duplicate = service.publish_completed_work_order_snapshot(
                session,
                snapshot_id=first_snapshot_id,
                now=first_publish_at + timedelta(minutes=1),
            )
            assert duplicate.sync_run_id == first.sync_run_id
            assert duplicate.status == "completed"
            assert duplicate.duplicate is True
            assert duplicate.unchanged_records == 1
            session.commit()

        with Session(projector_engine) as session:
            duplicate_row = session.get(OamWorkOrder, stable_row_id)
            assert duplicate_row is not None
            assert duplicate_row.updated_at == stable_updated_at
            assert session.scalar(
                select(func.count())
                .select_from(ExternalObjectVersion)
                .where(
                    ExternalObjectVersion.external_object_id
                    == stable_external_id
                )
            ) == 1
            assert session.scalar(
                select(func.count())
                .select_from(SyncRun)
                .where(SyncRun.source_system_id == source_id)
            ) == 1

        # Exercise hostile raw SQL only after the normal idempotent replay has
        # proved the real publisher path.  Every attack rolls back, and the
        # temporary unmapped person is removed before later mapping scenarios.
        _assert_projector_raw_evidence_attack_matrix(
            run_id=first.sync_run_id,
            batch_id=stable_batch_id,
            source_id=source_id,
            inbox_external_event_id=stable_event_external_event_id,
            inbox_external_id=stable_event_external_id,
            inbox_source_version=stable_event_source_version,
            inbox_source_updated_at=stable_event_source_updated_at,
            inbox_payload=stable_event_payload,
            inbox_payload_sha256=stable_event_payload_sha256,
            external_object_id=stable_external_id,
            external_version_source_version=stable_version_source_version,
            external_version_source_updated_at=(
                stable_version_source_updated_at
            ),
            external_version_payload=stable_version_payload,
            external_version_payload_sha256=stable_version_payload_sha256,
            work_order_id=stable_row_id,
            organization_id=child_id,
        )

        conflict_snapshot_id = _stage_pg16_projector_snapshot(
            migrator_engine,
            external_snapshot_id="pg16-projector-snapshot-conflict",
            status="end",
            snapshot_at=PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=15),
        )
        conflict_publish_at = PROJECTOR_GATE_SOURCE_TIME + timedelta(
            minutes=17
        )
        with Session(projector_engine, expire_on_commit=False) as session:
            conflict_result = service.publish_completed_work_order_snapshot(
                session,
                snapshot_id=conflict_snapshot_id,
                now=conflict_publish_at,
            )
            assert conflict_result.status == "conflict"
            assert conflict_result.projected_records == 0
            assert conflict_result.conflict_records == 1
            session.commit()

        with Session(projector_engine) as session:
            conflict_run = session.get(SyncRun, conflict_result.sync_run_id)
            assert conflict_run is not None
            assert conflict_run.status == "conflict"
            assert (
                conflict_run.failure_code
                == "oam_work_order_projection_conflict"
            )
            conflict_batch = session.scalar(
                select(SyncBatch).where(
                    SyncBatch.run_id == conflict_result.sync_run_id
                )
            )
            assert conflict_batch is not None
            assert conflict_batch.status == "validated"
            conflict_event = session.scalar(
                select(SyncInboxEvent).where(
                    SyncInboxEvent.batch_id == conflict_batch.id
                )
            )
            assert conflict_event is not None
            assert conflict_event.status == "conflict"
            assert (
                conflict_event.error_code
                == "oam_work_order_projection_conflict"
            )
            conflict = session.scalar(
                select(SyncConflict).where(
                    SyncConflict.run_id == conflict_result.sync_run_id
                )
            )
            assert conflict is not None
            assert (
                conflict.conflict_type
                == "oam_work_order_same_time_version_conflict"
            )
            assert conflict.status == "open"
            preserved_row = session.get(OamWorkOrder, stable_row_id)
            assert preserved_row is not None
            assert preserved_row.status == "active"
            assert preserved_row.updated_at == stable_updated_at
            preserved_external = session.get(
                ExternalObject, stable_external_id
            )
            assert preserved_external is not None
            assert preserved_external.current_version_id == stable_version_id
            assert session.scalar(
                select(func.count())
                .select_from(ExternalObjectVersion)
                .where(
                    ExternalObjectVersion.external_object_id
                    == stable_external_id
                )
            ) == 1

        poisoned_snapshot_id = _stage_pg16_projector_snapshot(
            migrator_engine,
            external_snapshot_id="pg16-projector-snapshot-poisoned",
            status="closed",
            snapshot_at=PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=25),
            poison_manifest=True,
        )
        with Session(projector_engine, expire_on_commit=False) as session:
            with pytest.raises(
                service.OamWorkOrderProjectionError
            ) as publish_error:
                service.publish_completed_work_order_snapshot(
                    session,
                    snapshot_id=poisoned_snapshot_id,
                    now=PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=27),
                )
            assert (
                publish_error.value.code
                == "oam_work_order_manifest_hash_mismatch"
            )
            session.rollback()
            assert session.scalar(
                select(func.count())
                .select_from(SyncRun)
                .where(SyncRun.source_system_id == source_id)
            ) == 2
            failed_run_id = service.record_failed_work_order_snapshot(
                session,
                snapshot_id=poisoned_snapshot_id,
                failure_code=publish_error.value.code,
            )
            assert failed_run_id is not None
            session.commit()

        with Session(projector_engine) as session:
            failed_run = session.get(SyncRun, failed_run_id)
            assert failed_run is not None
            assert failed_run.status == "failed"
            assert (
                failed_run.failure_code
                == "oam_work_order_manifest_hash_mismatch"
            )
            assert failed_run.failure_detail == (
                "deterministic staging validation failed; retry is explicit"
            )
            assert session.scalar(
                select(func.count())
                .select_from(SyncBatch)
                .where(SyncBatch.run_id == failed_run.id)
            ) == 0
            assert session.scalar(
                select(func.count())
                .select_from(SyncInboxEvent)
                .join(SyncBatch, SyncInboxEvent.batch_id == SyncBatch.id)
                .where(SyncBatch.run_id == failed_run.id)
            ) == 0
            final_row = session.get(OamWorkOrder, stable_row_id)
            assert final_row is not None
            assert final_row.status == "active"
            assert final_row.updated_at == stable_updated_at
            final_external = session.get(ExternalObject, stable_external_id)
            assert final_external is not None
            assert final_external.current_version_id == stable_version_id
            assert session.scalar(
                select(func.count())
                .select_from(ExternalObjectVersion)
                .where(
                    ExternalObjectVersion.external_object_id
                    == stable_external_id
                )
            ) == 1
            assert sorted(
                session.scalars(
                    select(SyncRun.status).where(
                        SyncRun.source_system_id == source_id
                    )
                ).all()
            ) == ["completed", "conflict", "failed"]
            assert service.next_unpublished_work_order_snapshot_id(session) is None

        # A strictly newer OAM source observation must follow the real changed
        # path: close the old version, point to the new version, and update the
        # formal work-order row atomically under the deferred chain guard.
        status_source_time = PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=40)
        status_payload = _projector_gate_payload(
            status="end",
            source_updated_at=status_source_time,
        )
        status_snapshot_id = _stage_pg16_projector_snapshot(
            migrator_engine,
            external_snapshot_id="pg16-projector-snapshot-status-update",
            status="end",
            source_updated_at=status_source_time,
            snapshot_at=PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=45),
        )
        status_publish_at = PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=47)
        with Session(projector_engine, expire_on_commit=False) as session:
            status_result = service.publish_completed_work_order_snapshot(
                session,
                snapshot_id=status_snapshot_id,
                now=status_publish_at,
            )
            assert status_result.status == "completed"
            assert status_result.projected_records == 1
            assert status_result.created_records == 0
            # ``updated_records`` is the public result of plan.changed=True.
            assert status_result.updated_records == 1
            assert status_result.unchanged_records == 0
            assert status_result.conflict_records == 0
            assert status_result.duplicate is False
            session.commit()

        with Session(projector_engine) as session:
            status_row = session.get(OamWorkOrder, stable_row_id)
            status_external = session.get(ExternalObject, stable_external_id)
            first_version = session.get(
                ExternalObjectVersion, stable_version_id
            )
            assert status_row is not None
            assert status_external is not None
            assert first_version is not None
            assert status_row.status == "completed"
            assert status_row.organization_id == child_id
            assert status_row.engineer_person_id == person_id
            assert service._aware(status_row.source_updated_at) == (
                status_source_time
            )
            assert service._aware(status_row.updated_at) == status_publish_at
            assert first_version.is_current is False
            assert service._aware(first_version.valid_to) == status_publish_at
            status_version = session.get(
                ExternalObjectVersion,
                status_external.current_version_id,
            )
            assert status_version is not None
            assert status_version.id != stable_version_id
            assert status_version.is_current is True
            assert status_version.valid_to is None
            assert service._aware(status_version.source_updated_at) == (
                status_source_time
            )
            assert service._aware(status_version.valid_from) == status_publish_at
            assert status_version.payload_jsonb == {
                "work_order_no": PROJECTOR_GATE_WORK_ORDER_NO,
                "organization_id": str(child_id),
                "engineer_person_id": str(person_id),
                "status": "completed",
            }
            assert status_version.payload_sha256 == _projector_gate_sha256(
                status_version.payload_jsonb
            )
            assert status_version.source_version.split(":")[1] == (
                _projector_gate_sha256(status_payload)
            )
            assert session.scalar(
                select(func.count())
                .select_from(ExternalObjectVersion)
                .where(
                    ExternalObjectVersion.external_object_id
                    == stable_external_id
                )
            ) == 2
            status_version_id = status_version.id
            status_projection_source_version = status_version.source_version

        # The raw OAM work-order is deliberately unchanged here.  Only the
        # separately approved employee-to-person mapping changes, which must
        # still rotate projection evidence and the formal person pointer.
        remapped_at = PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=50)
        replacement_person_id = _remap_pg16_projector_identity(
            migrator_engine,
            organization_id=child_id,
            remapped_at=remapped_at,
        )
        mapping_snapshot_id = _stage_pg16_projector_snapshot(
            migrator_engine,
            external_snapshot_id="pg16-projector-snapshot-mapping-update",
            status="end",
            source_updated_at=status_source_time,
            snapshot_at=PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=55),
        )
        mapping_publish_at = PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=57)
        with Session(projector_engine, expire_on_commit=False) as session:
            mapping_result = service.publish_completed_work_order_snapshot(
                session,
                snapshot_id=mapping_snapshot_id,
                now=mapping_publish_at,
            )
            assert mapping_result.status == "completed"
            assert mapping_result.projected_records == 1
            assert mapping_result.created_records == 0
            assert mapping_result.updated_records == 1
            assert mapping_result.unchanged_records == 0
            assert mapping_result.conflict_records == 0
            assert mapping_result.duplicate is False
            session.commit()

        with Session(projector_engine) as session:
            mapping_row = session.get(OamWorkOrder, stable_row_id)
            mapping_external = session.get(ExternalObject, stable_external_id)
            previous_status_version = session.get(
                ExternalObjectVersion, status_version_id
            )
            assert mapping_row is not None
            assert mapping_external is not None
            assert previous_status_version is not None
            assert mapping_row.status == "completed"
            assert mapping_row.organization_id == child_id
            assert mapping_row.engineer_person_id == replacement_person_id
            assert service._aware(mapping_row.source_updated_at) == (
                status_source_time
            )
            assert service._aware(mapping_row.updated_at) == mapping_publish_at
            assert previous_status_version.is_current is False
            assert service._aware(previous_status_version.valid_to) == (
                mapping_publish_at
            )
            mapping_version = session.get(
                ExternalObjectVersion,
                mapping_external.current_version_id,
            )
            assert mapping_version is not None
            assert mapping_version.id != status_version_id
            assert mapping_version.is_current is True
            assert mapping_version.valid_to is None
            assert service._aware(mapping_version.source_updated_at) == (
                status_source_time
            )
            assert mapping_version.payload_jsonb == {
                "work_order_no": PROJECTOR_GATE_WORK_ORDER_NO,
                "organization_id": str(child_id),
                "engineer_person_id": str(replacement_person_id),
                "status": "completed",
            }
            assert mapping_version.payload_sha256 == _projector_gate_sha256(
                mapping_version.payload_jsonb
            )
            status_source_coordinates = status_projection_source_version.split(
                ":"
            )
            mapping_source_coordinates = mapping_version.source_version.split(
                ":"
            )
            assert len(status_source_coordinates) == 3
            assert len(mapping_source_coordinates) == 3
            assert mapping_source_coordinates[1] == status_source_coordinates[1]
            assert mapping_source_coordinates[2] != status_source_coordinates[2]
            assert session.scalar(
                select(func.count())
                .select_from(ExternalObjectVersion)
                .where(
                    ExternalObjectVersion.external_object_id
                    == stable_external_id
                )
            ) == 3
            mapping_version_id = mapping_version.id

        _assert_pg16_projection_chain_rejects_partial_updates(
            external_object_id=stable_external_id,
            closed_version_id=stable_version_id,
            closed_at=status_publish_at,
            current_version_id=mapping_version_id,
            close_at=PROJECTOR_GATE_SOURCE_TIME + timedelta(minutes=58),
        )
        with Session(projector_engine) as session:
            final_external = session.get(ExternalObject, stable_external_id)
            final_version = session.get(
                ExternalObjectVersion, mapping_version_id
            )
            final_row = session.get(OamWorkOrder, stable_row_id)
            assert final_external is not None
            assert final_version is not None
            assert final_row is not None
            assert final_external.current_version_id == mapping_version_id
            assert final_version.is_current is True
            assert final_version.valid_to is None
            assert final_row.engineer_person_id == replacement_person_id
            assert session.scalar(
                select(func.count())
                .select_from(ExternalObjectVersion)
                .where(
                    ExternalObjectVersion.external_object_id
                    == stable_external_id,
                    ExternalObjectVersion.is_current.is_(True),
                )
            ) == 1
    finally:
        migrator_engine.dispose()


def _assert_projector_membership_drift_is_rejected(projector_engine) -> None:
    from app.oam_projection_security import (
        OamProjectionDatabaseBoundaryError,
    )

    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "GRANT star_oam_backup TO star_oam_projector "
                "WITH INHERIT FALSE, SET FALSE, ADMIN FALSE"
            )
    try:
        with pytest.raises(OamProjectionDatabaseBoundaryError):
            _validate_projector_security(projector_engine)
    finally:
        with psycopg.connect(
            **_admin_parameters(), autocommit=True
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "REVOKE star_oam_backup FROM star_oam_projector"
                )
    _validate_projector_security(projector_engine)


def _assert_projector_cross_schema_drift_is_rejected(projector_engine) -> None:
    from app.oam_projection_security import (
        OamProjectionDatabaseBoundaryError,
    )

    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL("CREATE SCHEMA {} AUTHORIZATION star_oam_migrator").format(
                    sql.Identifier(PROJECTOR_SHADOW_SCHEMA)
                )
            )
            cursor.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO star_oam_projector").format(
                    sql.Identifier(PROJECTOR_SHADOW_SCHEMA)
                )
            )
            cursor.execute(
                sql.SQL("ALTER ROLE star_oam_projector SET search_path = {}, public").format(
                    sql.Identifier(PROJECTOR_SHADOW_SCHEMA)
                )
            )
    projector_engine.dispose()
    try:
        with pytest.raises(OamProjectionDatabaseBoundaryError):
            _validate_projector_security(projector_engine)
        blocked = _run_deployment_sql(
            "verify_oam_edge_staging.sql",
            variables={
                "edge_role": EDGE_RECEIVER_ROLE,
                "projector_role": "star_oam_projector",
            },
            expect_success=False,
        )
        assert "cross_schema" in (blocked.stdout + blocked.stderr)
    finally:
        with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER ROLE star_oam_projector SET search_path = public"
                )
                cursor.execute(
                    sql.SQL(
                        "REVOKE ALL ON SCHEMA {} FROM star_oam_projector"
                    ).format(sql.Identifier(PROJECTOR_SHADOW_SCHEMA))
                )
                cursor.execute(
                    sql.SQL("DROP SCHEMA {} RESTRICT").format(
                        sql.Identifier(PROJECTOR_SHADOW_SCHEMA)
                    )
                )
    projector_engine.dispose()
    _validate_projector_security(projector_engine)
    _run_deployment_sql(
        "verify_oam_edge_staging.sql",
        variables={
            "edge_role": EDGE_RECEIVER_ROLE,
            "projector_role": "star_oam_projector",
        },
    )


def _assert_public_and_cluster_acl_drift_is_rejected(
    projector_engine,
    edge_engine,
) -> None:
    from app.edge_database_security import EdgeDatabaseBoundaryError
    from app.oam_projection_security import (
        OamProjectionDatabaseBoundaryError,
    )

    verify_variables = {
        "edge_role": EDGE_RECEIVER_ROLE,
        "projector_role": "star_oam_projector",
    }

    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "GRANT SELECT ON TABLE public.external_sync_snapshots TO PUBLIC"
            )
    try:
        with pytest.raises(OamProjectionDatabaseBoundaryError):
            _validate_projector_security(projector_engine)
        with pytest.raises(EdgeDatabaseBoundaryError):
            _validate_edge_security(edge_engine)
        blocked = _run_deployment_sql(
            "verify_oam_edge_staging.sql",
            variables=verify_variables,
            expect_success=False,
        )
        assert "public_acl" in (blocked.stdout + blocked.stderr)
    finally:
        with psycopg.connect(
            **_admin_parameters(), autocommit=True
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "REVOKE SELECT ON TABLE "
                    "public.external_sync_snapshots FROM PUBLIC"
                )

    large_object_oid: int | None = None
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_catalog.lo_create(0)")
            large_object_oid = int(cursor.fetchone()[0])
            cursor.execute(
                sql.SQL(
                    "GRANT SELECT ON LARGE OBJECT {} TO star_oam_projector"
                ).format(sql.Literal(large_object_oid))
            )
    try:
        with pytest.raises(OamProjectionDatabaseBoundaryError):
            _validate_projector_security(projector_engine)
        blocked = _run_deployment_sql(
            "verify_oam_edge_staging.sql",
            variables=verify_variables,
            expect_success=False,
        )
        assert "cluster_objects" in (blocked.stdout + blocked.stderr)
    finally:
        if large_object_oid is not None:
            with psycopg.connect(
                **_admin_parameters(), autocommit=True
            ) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        "SELECT pg_catalog.lo_unlink(%s)",
                        (large_object_oid,),
                    )
                    assert cursor.fetchone()[0] == 1

    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "GRANT SET ON PARAMETER session_replication_role "
                "TO star_oam_projector"
            )
    try:
        with pytest.raises(OamProjectionDatabaseBoundaryError):
            _validate_projector_security(projector_engine)
        blocked = _run_deployment_sql(
            "verify_oam_edge_staging.sql",
            variables=verify_variables,
            expect_success=False,
        )
        assert "cluster_objects" in (blocked.stdout + blocked.stderr)
    finally:
        with psycopg.connect(
            **_admin_parameters(), autocommit=True
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "REVOKE SET ON PARAMETER session_replication_role "
                    "FROM star_oam_projector"
                )

    _validate_projector_security(projector_engine)
    _validate_edge_security(edge_engine)
    _run_deployment_sql(
        "verify_oam_edge_staging.sql",
        variables=verify_variables,
    )


def _assert_external_sync_scope_lock_serializes(
    api_engine,
    projector_engine,
) -> None:
    from app.external_sync_scope_lock import lock_external_sync_scope

    source_instance = "pg16-oam-source"
    scope_key = "work-orders:recent-30d:company:org"
    first = Session(api_engine)
    second_started = threading.Event()
    second_pid: list[int] = []
    second_acquired = threading.Event()

    def acquire_same_scope() -> None:
        with Session(projector_engine) as session:
            second_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
            second_started.set()
            lock_external_sync_scope(
                session,
                source_instance=source_instance,
                scope_key=scope_key,
            )
            second_acquired.set()
            session.rollback()

    executor = ThreadPoolExecutor(max_workers=1)
    future = None
    try:
        lock_external_sync_scope(
            first,
            source_instance=source_instance,
            scope_key=scope_key,
        )
        future = executor.submit(acquire_same_scope)
        assert second_started.wait(timeout=10)
        assert second_pid
        _wait_for_backend_lock(second_pid[0])
        assert second_acquired.is_set() is False

        # The lock is scope-specific: another scope remains independently
        # available while the matching API/projector pair is serialized.
        with Session(api_engine) as independent:
            independent.execute(text("SET LOCAL statement_timeout = '2s'"))
            lock_external_sync_scope(
                independent,
                source_instance=source_instance,
                scope_key=f"{scope_key}:other",
            )
            independent.rollback()

        first.commit()
        future.result(timeout=10)
        assert second_acquired.is_set() is True
    finally:
        first.rollback()
        first.close()
        executor.shutdown(wait=True, cancel_futures=True)


def _assert_external_sync_scope_session_lock_survives_commit(
    api_engine,
    projector_engine,
) -> None:
    from app.external_sync_scope_lock import (
        acquire_external_sync_scope_session_lock,
        release_external_sync_scope_session_lock,
    )

    source_instance = "pg16-oam-session-source"
    scope_key = "work-orders:recent-30d:session-lock"
    owner = api_engine.connect()
    contender_started = threading.Event()
    contender_acquired = threading.Event()
    contender_pid: list[int] = []

    def acquire_same_session_lock() -> None:
        with projector_engine.connect() as connection:
            contender_pid.append(
                int(connection.scalar(text("SELECT pg_backend_pid()")))
            )
            contender_started.set()
            acquire_external_sync_scope_session_lock(
                connection,
                source_instance=source_instance,
                scope_key=scope_key,
            )
            contender_acquired.set()
            assert release_external_sync_scope_session_lock(
                connection,
                source_instance=source_instance,
                scope_key=scope_key,
            ) is True
            connection.commit()

    executor = ThreadPoolExecutor(max_workers=1)
    future = None
    released = False
    try:
        acquire_external_sync_scope_session_lock(
            owner,
            source_instance=source_instance,
            scope_key=scope_key,
        )
        owner.commit()
        future = executor.submit(acquire_same_session_lock)
        assert contender_started.wait(timeout=10)
        assert contender_pid
        _wait_for_backend_lock(contender_pid[0])
        assert contender_acquired.is_set() is False

        # A session advisory lock deliberately survives transaction commit.
        owner.execute(text("SELECT 1"))
        owner.commit()
        assert contender_acquired.is_set() is False

        assert release_external_sync_scope_session_lock(
            owner,
            source_instance=source_instance,
            scope_key=scope_key,
        ) is True
        released = True
        owner.commit()
        future.result(timeout=10)
        assert contender_acquired.is_set() is True
    finally:
        if not released:
            release_external_sync_scope_session_lock(
                owner,
                source_instance=source_instance,
                scope_key=scope_key,
            )
            owner.commit()
        owner.close()
        executor.shutdown(wait=True, cancel_futures=True)


def _sms_role_access_evidence(api_engine) -> dict[str, dict[str, object]]:
    from app.database_security import _SMS_DISPATCH_ROLE_ACCESS_SQL

    with api_engine.connect() as connection:
        rows = connection.execute(
            _SMS_DISPATCH_ROLE_ACCESS_SQL,
            {
                "runtime_role": "star_oam_api",
                "migration_role": "star_oam_migrator",
            },
        ).mappings().all()
    return {str(row["role_label"]): dict(row) for row in rows}


def _assert_membership_drift_is_rejected(api_engine) -> None:
    from app.database_security import DatabaseSecurityBoundaryError

    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("ALTER ROLE star_oam_edge NOINHERIT")
            cursor.execute("GRANT star_oam_backup TO star_oam_edge")
    with pytest.raises(DatabaseSecurityBoundaryError):
        _validate_runtime_security(api_engine)
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("REVOKE star_oam_backup FROM star_oam_edge")
            cursor.execute(
                "GRANT star_oam_backup TO star_oam_edge "
                "WITH INHERIT FALSE, SET FALSE, ADMIN TRUE"
            )
    admin_only = _sms_role_access_evidence(api_engine)
    assert admin_only["edge"]["is_member_of_any_role"] is True
    assert admin_only["edge"]["can_set_select_role"] is False
    assert admin_only["edge"]["can_admin_select_role"] is True
    assert admin_only["backup"]["has_any_nonsuper_member"] is True
    with pytest.raises(DatabaseSecurityBoundaryError):
        _validate_runtime_security(api_engine)
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("REVOKE star_oam_backup FROM star_oam_edge")
            cursor.execute(
                "CREATE ROLE rsc_pg16_gate_bridge NOLOGIN NOINHERIT "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )
            cursor.execute("GRANT star_oam_backup TO rsc_pg16_gate_bridge")
            cursor.execute("GRANT rsc_pg16_gate_bridge TO star_oam_edge")
    with pytest.raises(DatabaseSecurityBoundaryError):
        _validate_runtime_security(api_engine)
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute("REVOKE rsc_pg16_gate_bridge FROM star_oam_edge")
            cursor.execute("REVOKE star_oam_backup FROM rsc_pg16_gate_bridge")
            cursor.execute("DROP ROLE rsc_pg16_gate_bridge")
            cursor.execute(
                "CREATE ROLE rsc_pg16_gate_admin_bridge NOLOGIN NOINHERIT "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )
            cursor.execute(
                "GRANT star_oam_backup TO rsc_pg16_gate_admin_bridge "
                "WITH INHERIT FALSE, SET FALSE, ADMIN TRUE"
            )
            cursor.execute(
                "GRANT rsc_pg16_gate_admin_bridge TO star_oam_edge "
                "WITH INHERIT FALSE, SET FALSE, ADMIN TRUE"
            )
    with pytest.raises(DatabaseSecurityBoundaryError):
        _validate_runtime_security(api_engine)
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "REVOKE rsc_pg16_gate_admin_bridge FROM star_oam_edge"
            )
            cursor.execute(
                "REVOKE star_oam_backup FROM rsc_pg16_gate_admin_bridge"
            )
            cursor.execute("DROP ROLE rsc_pg16_gate_admin_bridge")
            cursor.execute(
                "CREATE ROLE rsc_pg16_gate_mixed_bridge NOLOGIN NOINHERIT "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            )
            cursor.execute(
                "GRANT rsc_pg16_gate_mixed_bridge TO star_oam_edge "
                "WITH INHERIT FALSE, SET FALSE, ADMIN TRUE"
            )
            cursor.execute(
                "GRANT star_oam_backup TO rsc_pg16_gate_mixed_bridge "
                "WITH INHERIT FALSE, SET TRUE, ADMIN FALSE"
            )
    mixed_path = _sms_role_access_evidence(api_engine)
    assert mixed_path["edge"]["is_member_of_any_role"] is True
    assert mixed_path["edge"]["can_set_select_role"] is False
    assert mixed_path["edge"]["can_admin_select_role"] is False
    assert mixed_path["backup"]["has_any_nonsuper_member"] is True
    with pytest.raises(DatabaseSecurityBoundaryError):
        _validate_runtime_security(api_engine)
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "REVOKE rsc_pg16_gate_mixed_bridge FROM star_oam_edge"
            )
            cursor.execute(
                "REVOKE star_oam_backup FROM rsc_pg16_gate_mixed_bridge"
            )
            cursor.execute("DROP ROLE rsc_pg16_gate_mixed_bridge")
            cursor.execute("ALTER ROLE star_oam_edge INHERIT")
    _validate_runtime_security(api_engine)


def _assert_database_owner_membership_boundary(api_engine) -> None:
    from app.database_security import DatabaseSecurityBoundaryError

    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT "
                "pg_has_role('star_oam_migrator', 'pg_database_owner', "
                "'MEMBER'), "
                "pg_has_role('star_oam_api', 'pg_database_owner', 'MEMBER')"
            )
            implicit_owner_member, runtime_member = cursor.fetchone()
    assert implicit_owner_member is True
    assert runtime_member is False

    baseline = _sms_role_access_evidence(api_engine)
    assert baseline["migration"]["is_member_of_any_role"] is False

    bridge_role = "rsc_pg16_gate_migrator_bridge"
    with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "CREATE ROLE {} NOLOGIN NOINHERIT NOSUPERUSER "
                    "NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
                ).format(sql.Identifier(bridge_role))
            )
            cursor.execute(
                sql.SQL(
                    "GRANT {} TO star_oam_migrator "
                    "WITH INHERIT FALSE, SET FALSE, ADMIN FALSE"
                ).format(sql.Identifier(bridge_role))
            )
    try:
        drifted = _sms_role_access_evidence(api_engine)
        assert drifted["migration"]["is_member_of_any_role"] is True
        with pytest.raises(DatabaseSecurityBoundaryError):
            _validate_runtime_security(api_engine)
    finally:
        with psycopg.connect(
            **_admin_parameters(), autocommit=True
        ) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    sql.SQL("REVOKE {} FROM star_oam_migrator").format(
                        sql.Identifier(bridge_role)
                    )
                )
                cursor.execute(
                    sql.SQL("DROP ROLE {}").format(
                        sql.Identifier(bridge_role)
                    )
                )
    _validate_runtime_security(api_engine)


def _assert_sms_acl(api_engine) -> None:
    with api_engine.connect() as connection:
        evidence = connection.execute(
            text(
                "SELECT "
                "has_table_privilege(current_user, "
                "'public.sms_challenge_dispatches', 'SELECT') AS api_select, "
                "has_table_privilege(current_user, "
                "'public.sms_challenge_dispatches', 'INSERT') AS api_insert, "
                "has_table_privilege(current_user, "
                "'public.sms_challenge_dispatches', 'UPDATE') AS api_update, "
                "has_column_privilege(current_user, "
                "'public.sms_challenge_dispatches', 'status', 'UPDATE') "
                "AS api_status_update, "
                "has_column_privilege(current_user, "
                "'public.sms_challenge_dispatches', 'request_sha256', 'UPDATE') "
                "AS api_request_update, "
                "has_table_privilege('star_oam_backup', "
                "'public.sms_challenge_dispatches', 'SELECT') AS backup_select, "
                "has_table_privilege('star_oam_backup', "
                "'public.sms_challenge_dispatches', 'INSERT') AS backup_insert, "
                "has_table_privilege('star_oam_edge', "
                "'public.sms_challenge_dispatches', 'SELECT') AS edge_select"
            )
        ).mappings().one()
    assert dict(evidence) == {
        "api_select": True,
        "api_insert": True,
        "api_update": False,
        "api_status_update": True,
        "api_request_update": False,
        "backup_select": True,
        "backup_insert": False,
        "edge_select": False,
    }


def _insert_work_order_lock_fixture() -> uuid.UUID:
    source_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    person_id = uuid.uuid4()
    external_object_id = uuid.uuid4()
    work_order_id = uuid.uuid4()
    now = datetime.now(timezone.utc)
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO source_systems (
                    id, code, name, mode, enabled, configuration_jsonb,
                    updated_at, created_at
                ) VALUES (%s, %s, 'PG16 work-order lock source', 'read_only',
                          true, '{}'::jsonb, %s, %s)
                """,
                (source_id, f"pg16-lock-{source_id.hex}", now, now),
            )
            cursor.execute(
                """
                INSERT INTO organizations (
                    id, external_object_id, code, name, parent_id, org_type,
                    province_code, status, updated_at, created_at
                ) VALUES (%s, NULL, %s, 'PG16 lock organization', NULL,
                          'region_company', '320000', 'active', %s, %s)
                """,
                (organization_id, f"PG16-{organization_id.hex}", now, now),
            )
            cursor.execute(
                """
                INSERT INTO people (
                    id, external_object_id, organization_id, employee_no,
                    name, mobile_encrypted, mobile_hash, employment_status,
                    source_updated_at, updated_at, created_at
                ) VALUES (%s, NULL, %s, %s, 'PG16 lock engineer', NULL,
                          NULL, 'active', %s, %s, %s)
                """,
                (
                    person_id,
                    organization_id,
                    f"PG16-{person_id.hex}",
                    now,
                    now,
                    now,
                ),
            )
            cursor.execute(
                """
                INSERT INTO external_objects (
                    id, source_system_id, entity_type, external_id,
                    current_version_id, deleted_at, updated_at, created_at
                ) VALUES (%s, %s, 'work_order', %s, NULL, NULL, %s, %s)
                """,
                (
                    external_object_id,
                    source_id,
                    f"PG16-WO-{work_order_id.hex}",
                    now,
                    now,
                ),
            )
            cursor.execute(
                """
                INSERT INTO oam_work_orders (
                    id, external_object_id, work_order_no, organization_id,
                    engineer_person_id, status, source_updated_at, updated_at,
                    created_at
                ) VALUES (%s, %s, %s, %s, %s, 'active', %s, %s, %s)
                """,
                (
                    work_order_id,
                    external_object_id,
                    f"PG16-WO-{work_order_id.hex}",
                    organization_id,
                    person_id,
                    now,
                    now,
                    now,
                ),
            )
    return work_order_id


def _assert_work_order_lock_acl_and_concurrency() -> None:
    work_order_id = _insert_work_order_lock_fixture()
    function_name = "rsc_lock_material_request_work_order_reference_0042"
    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    migrator_parameters = _connection_parameters(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )

    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT owner.rolname,
                       has_function_privilege(
                           'star_oam_api', %s, 'EXECUTE'
                       ),
                       EXISTS (
                           SELECT 1
                             FROM aclexplode(
                                 coalesce(
                                     function_row.proacl,
                                     acldefault('f', function_row.proowner)
                                 )
                             ) AS function_acl
                            WHERE function_acl.grantee = 0
                              AND function_acl.privilege_type = 'EXECUTE'
                       ),
                       has_function_privilege(
                           'star_oam_backup', %s, 'EXECUTE'
                       ),
                       has_function_privilege('star_oam_edge', %s, 'EXECUTE')
                  FROM pg_proc AS function_row
                  JOIN pg_namespace AS schema_row
                    ON schema_row.oid = function_row.pronamespace
                  JOIN pg_roles AS owner
                    ON owner.oid = function_row.proowner
                 WHERE schema_row.nspname = 'public'
                   AND function_row.proname = %s
                """,
                (
                    WORK_ORDER_LOCK_FUNCTION,
                    WORK_ORDER_LOCK_FUNCTION,
                    WORK_ORDER_LOCK_FUNCTION,
                    function_name,
                ),
            )
            assert cursor.fetchone() == (
                "star_oam_migrator",
                True,
                False,
                False,
                False,
            )

    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                cursor.execute(
                    "UPDATE oam_work_orders SET status = 'pending' WHERE id = %s",
                    (work_order_id,),
                )
        connection.rollback()

    first_api = psycopg.connect(**api_parameters)
    second_api = psycopg.connect(**api_parameters)
    updater_started = threading.Event()
    updater_pid: list[int] = []
    try:
        with first_api.cursor() as cursor:
            cursor.execute(
                f"SELECT public.{function_name}(%s)",
                (work_order_id,),
            )
        # A second API transaction must acquire the same SHARE lock without
        # waiting; otherwise the helper over-serializes independent commands.
        with second_api.cursor() as cursor:
            cursor.execute("SET LOCAL statement_timeout = '2s'")
            cursor.execute(
                f"SELECT public.{function_name}(%s)",
                (work_order_id,),
            )

        def update_nonkey_status() -> None:
            with psycopg.connect(**migrator_parameters) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    updater_pid.append(cursor.fetchone()[0])
                    updater_started.set()
                    cursor.execute("SET LOCAL statement_timeout = '20s'")
                    cursor.execute(
                        "UPDATE oam_work_orders SET status = 'pending' "
                        "WHERE id = %s",
                        (work_order_id,),
                    )

        executor = ThreadPoolExecutor(max_workers=1)
        try:
            update_future = executor.submit(update_nonkey_status)
            assert updater_started.wait(timeout=10)
            assert updater_pid
            _wait_for_backend_lock(updater_pid[0])
            first_api.commit()
            # The second SHARE holder must continue to block the non-key UPDATE.
            _wait_for_backend_lock(updater_pid[0])
            second_api.commit()
            update_future.result(timeout=15)
        finally:
            # Always release both holders before joining the updater; otherwise
            # an assertion failure in the wait proof could strand CI.
            first_api.rollback()
            second_api.rollback()
            executor.shutdown(wait=True, cancel_futures=True)
    finally:
        first_api.close()
        second_api.close()

    with psycopg.connect(**migrator_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status FROM oam_work_orders WHERE id = %s",
                (work_order_id,),
            )
            assert cursor.fetchone()[0] == "pending"


def _assert_0045_approval_catalog_drift_is_rejected(api_engine) -> None:
    from app.database_security import DatabaseSecurityBoundaryError

    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT "
                "(SELECT count(*) FROM public.material_requests), "
                "(SELECT count(*) FROM public.approval_instances)"
            )
            assert cursor.fetchone() == (0, 0)

    parameters = _connection_parameters(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    with psycopg.connect(**parameters, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE public.material_requests ENABLE TRIGGER "
                f"{MATERIAL_REQUEST_STATUS_TRIGGER_0045}"
            )
    try:
        with psycopg.connect(**_admin_parameters()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT trigger_row.tgenabled "
                    "FROM pg_catalog.pg_trigger AS trigger_row "
                    "JOIN pg_catalog.pg_class AS relation "
                    "ON relation.oid = trigger_row.tgrelid "
                    "JOIN pg_catalog.pg_namespace AS schema_row "
                    "ON schema_row.oid = relation.relnamespace "
                    "WHERE schema_row.nspname = 'public' "
                    "AND relation.relname = 'material_requests' "
                    "AND trigger_row.tgname = %s",
                    (MATERIAL_REQUEST_STATUS_TRIGGER_0045,),
                )
                assert cursor.fetchone() == ("O",)
        with pytest.raises(DatabaseSecurityBoundaryError):
            _validate_runtime_security(api_engine)
    finally:
        with psycopg.connect(**parameters, autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "ALTER TABLE public.material_requests ENABLE ALWAYS TRIGGER "
                    f"{MATERIAL_REQUEST_STATUS_TRIGGER_0045}"
                )
    _validate_runtime_security(api_engine)


def _assert_0046_content_catalog(*, installed: bool) -> None:
    expected_immediate = {
        (table_name, f"trg_{table_name}_content_write_0046")
        for table_name in MATERIAL_REQUEST_CONTENT_TRIGGER_TABLES_0046
    }
    expected_deferred = {
        (table_name, f"trg_{table_name}_content_causality_0046")
        for table_name in MATERIAL_REQUEST_CONTENT_TRIGGER_TABLES_0046
    }
    expected_triggers = expected_immediate | expected_deferred

    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT data_type, character_maximum_length, is_nullable, "
                "column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' "
                "AND table_name = 'material_request_commands' "
                "AND column_name = %s",
                (MATERIAL_REQUEST_CONTENT_MANIFEST_COLUMN_0046,),
            )
            column = cursor.fetchone()
            cursor.execute(
                "SELECT constraint_row.convalidated, "
                "pg_catalog.pg_get_constraintdef(constraint_row.oid, true) "
                "FROM pg_catalog.pg_constraint AS constraint_row "
                "JOIN pg_catalog.pg_class AS relation "
                "ON relation.oid = constraint_row.conrelid "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = relation.relnamespace "
                "WHERE schema_row.nspname = 'public' "
                "AND relation.relname = 'material_request_commands' "
                "AND constraint_row.conname = %s",
                (MATERIAL_REQUEST_CONTENT_MANIFEST_CONSTRAINT_0046,),
            )
            constraint = cursor.fetchone()
            cursor.execute(
                "SELECT function_row.proname, "
                "pg_catalog.oidvectortypes(function_row.proargtypes), "
                "pg_catalog.pg_get_function_result(function_row.oid), "
                "function_row.provolatile, function_row.prosecdef, "
                "owner.rolname, function_row.proconfig, "
                "pg_catalog.has_function_privilege("
                "'star_oam_api', function_row.oid, 'EXECUTE'), "
                "EXISTS ("
                "SELECT 1 FROM pg_catalog.aclexplode("
                "COALESCE("
                "function_row.proacl, "
                "pg_catalog.acldefault('f', function_row.proowner)"
                ")) AS function_acl "
                "WHERE function_acl.grantee = 0 "
                "AND function_acl.privilege_type = 'EXECUTE'"
                ") "
                "FROM pg_catalog.pg_proc AS function_row "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = function_row.pronamespace "
                "JOIN pg_catalog.pg_roles AS owner "
                "ON owner.oid = function_row.proowner "
                "WHERE schema_row.nspname = 'public' "
                "AND function_row.proname = ANY(%s) "
                "ORDER BY function_row.proname",
                (list(MATERIAL_REQUEST_CONTENT_FUNCTIONS_0046),),
            )
            functions = cursor.fetchall()
            cursor.execute(
                "SELECT relation.relname, trigger_row.tgname, "
                "function_row.proname, trigger_row.tgenabled, "
                "trigger_row.tgconstraint <> 0, "
                "trigger_row.tgdeferrable, trigger_row.tginitdeferred, "
                "pg_catalog.pg_get_triggerdef(trigger_row.oid, true) "
                "FROM pg_catalog.pg_trigger AS trigger_row "
                "JOIN pg_catalog.pg_class AS relation "
                "ON relation.oid = trigger_row.tgrelid "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = relation.relnamespace "
                "JOIN pg_catalog.pg_proc AS function_row "
                "ON function_row.oid = trigger_row.tgfoid "
                "WHERE schema_row.nspname = 'public' "
                "AND trigger_row.tgname = ANY(%s) "
                "ORDER BY relation.relname, trigger_row.tgname",
                ([trigger_name for _, trigger_name in expected_triggers],),
            )
            triggers = cursor.fetchall()

    if not installed:
        assert column is None
        assert constraint is None
        assert functions == []
        assert triggers == []
        return

    assert column == ("character varying", 64, "YES", None)
    assert constraint is not None and constraint[0] is True
    constraint_definition = constraint[1]
    for required_fragment in (
        MATERIAL_REQUEST_CONTENT_MANIFEST_COLUMN_0046,
        "create",
        "update_draft",
        "submit",
        "[0-9a-f]{64}",
        "IS NOT NULL",
        "IS NULL",
    ):
        assert required_fragment in constraint_definition

    assert len(functions) == len(MATERIAL_REQUEST_CONTENT_FUNCTIONS_0046)
    for row in functions:
        function_name = row[0]
        assert (row[1], row[2]) == MATERIAL_REQUEST_CONTENT_FUNCTIONS_0046[
            function_name
        ]
        assert row[3:6] == ("v", True, "star_oam_migrator")
        assert tuple(row[6] or ()) == ("search_path=pg_catalog, public",)
        assert row[7:] == (False, False)

    assert {(row[0], row[1]) for row in triggers} == expected_triggers
    for row in triggers:
        table_name, trigger_name, function_name = row[:3]
        assert row[3] == "A"
        definition = row[7]
        if (table_name, trigger_name) in expected_immediate:
            assert row[4:7] == (False, False, False)
            assert function_name == "rsc_guard_material_request_content_write_0046"
            assert " BEFORE INSERT " in definition
            if table_name == "material_request_commands":
                assert " DELETE " not in definition
                assert " UPDATE " not in definition
            else:
                assert " DELETE " in definition
                assert " UPDATE " in definition
        else:
            assert row[4:7] == (True, True, True)
            assert (
                function_name
                == "rsc_dispatch_material_request_content_causality_0046"
            )
            assert " AFTER INSERT " in definition
            assert " DELETE " in definition
            assert " UPDATE " in definition
            assert "DEFERRABLE INITIALLY DEFERRED" in definition


def _assert_0047_start_catalog(*, installed: bool) -> None:
    expected_guard = (
        STOCKTAKE_START_COMPLETION_TABLE_0047,
        "trg_stocktake_start_completions_guard_0047",
    )
    expected_deferred = {
        (
            table_name,
            f"trg_{table_name}_stocktake_start_causality_0047",
        )
        for table_name in STOCKTAKE_START_TRIGGER_TABLES_0047
    }
    expected_sealed = {
        (
            table_name,
            f"trg_{table_name}_stocktake_start_sealed_0047",
        )
        for table_name in STOCKTAKE_START_SEALED_TABLES_0047
    }
    expected_triggers = {expected_guard, *expected_sealed, *expected_deferred}

    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT column_name, data_type, character_maximum_length, "
                "is_nullable, column_default "
                "FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = %s "
                "ORDER BY ordinal_position",
                (STOCKTAKE_START_COMPLETION_TABLE_0047,),
            )
            columns = cursor.fetchall()
            cursor.execute(
                "SELECT constraint_row.conname, constraint_row.contype, "
                "constraint_row.convalidated "
                "FROM pg_catalog.pg_constraint AS constraint_row "
                "JOIN pg_catalog.pg_class AS relation "
                "ON relation.oid = constraint_row.conrelid "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = relation.relnamespace "
                "WHERE schema_row.nspname = 'public' "
                "AND relation.relname = %s "
                "AND constraint_row.contype IN ('c', 'f', 'p', 'u') "
                "ORDER BY constraint_row.conname",
                (STOCKTAKE_START_COMPLETION_TABLE_0047,),
            )
            constraints = cursor.fetchall()
            cursor.execute(
                "SELECT function_row.proname, "
                "pg_catalog.oidvectortypes(function_row.proargtypes), "
                "pg_catalog.pg_get_function_result(function_row.oid), "
                "function_row.provolatile, function_row.prosecdef, "
                "owner.rolname, function_row.proconfig, "
                "pg_catalog.has_function_privilege("
                "'star_oam_api', function_row.oid, 'EXECUTE'), "
                "EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE("
                "function_row.proacl, "
                "pg_catalog.acldefault('f', function_row.proowner)"
                ")) AS function_acl WHERE function_acl.grantee = 0 "
                "AND function_acl.privilege_type = 'EXECUTE') "
                "FROM pg_catalog.pg_proc AS function_row "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = function_row.pronamespace "
                "JOIN pg_catalog.pg_roles AS owner "
                "ON owner.oid = function_row.proowner "
                "WHERE schema_row.nspname = 'public' "
                "AND function_row.proname = ANY(%s) "
                "ORDER BY function_row.proname",
                (list(STOCKTAKE_START_FUNCTIONS_0047),),
            )
            functions = cursor.fetchall()
            cursor.execute(
                "SELECT relation.relname, trigger_row.tgname, "
                "function_row.proname, trigger_row.tgenabled, "
                "trigger_row.tgconstraint <> 0, "
                "trigger_row.tgdeferrable, trigger_row.tginitdeferred, "
                "pg_catalog.pg_get_triggerdef(trigger_row.oid, true) "
                "FROM pg_catalog.pg_trigger AS trigger_row "
                "JOIN pg_catalog.pg_class AS relation "
                "ON relation.oid = trigger_row.tgrelid "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = relation.relnamespace "
                "JOIN pg_catalog.pg_proc AS function_row "
                "ON function_row.oid = trigger_row.tgfoid "
                "WHERE schema_row.nspname = 'public' "
                "AND trigger_row.tgname = ANY(%s) "
                "ORDER BY relation.relname, trigger_row.tgname",
                ([trigger_name for _, trigger_name in expected_triggers],),
            )
            triggers = cursor.fetchall()
            if installed:
                cursor.execute(
                    "SELECT "
                    "pg_catalog.has_table_privilege('star_oam_api', %s, 'SELECT'), "
                    "pg_catalog.has_table_privilege('star_oam_api', %s, 'INSERT'), "
                    "pg_catalog.has_table_privilege('star_oam_api', %s, 'UPDATE'), "
                    "pg_catalog.has_table_privilege('star_oam_api', %s, 'DELETE'), "
                    "pg_catalog.has_table_privilege('star_oam_api', %s, 'TRIGGER')",
                    (
                        f"public.{STOCKTAKE_START_COMPLETION_TABLE_0047}",
                    )
                    * 5,
                )
                table_acl = cursor.fetchone()
            else:
                table_acl = None

    if not installed:
        assert columns == []
        assert constraints == []
        assert functions == []
        assert triggers == []
        return

    expected_column_names = (
        "id",
        "task_id",
        "initial_round_id",
        "expected_task_version",
        "started_task_version",
        "cutoff_ledger_cursor",
        "cutoff_at",
        "scope_count",
        "snapshot_line_count",
        "active_freeze_count",
        "scope_manifest_sha256",
        "snapshot_manifest_sha256",
        "request_sha256",
        "idempotency_key_hash",
        "started_by_user_id",
        "started_by_person_id",
        "started_role_assignment_id",
        "authorization_version",
        "role_code",
        "scope_type",
        "scope_id_snapshot",
        "authorization_sha256",
        "graph_manifest_sha256",
        "started_at",
        "created_at",
    )
    assert tuple(row[0] for row in columns) == expected_column_names
    graph_column = next(row for row in columns if row[0] == "graph_manifest_sha256")
    assert graph_column == (
        "graph_manifest_sha256",
        "character varying",
        64,
        "NO",
        None,
    )
    assert len(constraints) == 15
    assert all(row[2] is True for row in constraints)
    assert {row[1] for row in constraints} == {"c", "f", "p", "u"}
    assert len(functions) == len(STOCKTAKE_START_FUNCTIONS_0047)
    for row in functions:
        function_name = row[0]
        assert (row[1], row[2]) == STOCKTAKE_START_FUNCTIONS_0047[
            function_name
        ]
        assert row[3:6] == ("v", True, "star_oam_migrator")
        assert tuple(row[6] or ()) == ("search_path=pg_catalog, public",)
        assert row[7:] == (False, False)
    assert {(row[0], row[1]) for row in triggers} == expected_triggers
    for row in triggers:
        table_name, trigger_name, function_name = row[:3]
        assert row[3] == "A"
        definition = row[7]
        if (table_name, trigger_name) == expected_guard:
            assert row[4:7] == (False, False, False)
            assert function_name == "rsc_guard_stocktake_start_completion_0047"
            assert " BEFORE INSERT OR DELETE OR UPDATE " in definition
        elif (table_name, trigger_name) in expected_sealed:
            assert row[4:7] == (False, False, False)
            assert function_name == "rsc_guard_stocktake_start_completion_0047"
            assert " BEFORE INSERT OR DELETE OR UPDATE " in definition
        else:
            assert row[4:7] == (True, True, True)
            assert (
                function_name
                == "rsc_dispatch_nonopening_stocktake_start_causality_0047"
            )
            assert " AFTER INSERT OR DELETE OR UPDATE " in definition
            assert "DEFERRABLE INITIALLY DEFERRED" in definition
    assert table_acl == (True, True, False, False, False)


def _load_stocktake_recount_guard_security_migration_0049() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_pg16_gate_migration_0049_recount_guard_security_manifest",
        STOCKTAKE_RECOUNT_GUARD_SECURITY_MIGRATION_0049,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_stocktake_observation_scope_mode_migration_0050() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_pg16_gate_migration_0050_observation_scope_mode_manifest",
        STOCKTAKE_OBSERVATION_SCOPE_MODE_MIGRATION_0050,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_stocktake_difference_authorization_hash_migration_0051() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_pg16_gate_migration_0051_difference_authorization_hash_manifest",
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_MIGRATION_0051,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _load_opening_terminal_guard_execution_migration_0052() -> object:
    spec = importlib.util.spec_from_file_location(
        "rsc_pg16_gate_migration_0052_opening_terminal_guard_manifest",
        OPENING_TERMINAL_GUARD_EXECUTION_MIGRATION_0052,
    )
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    return migration


def _0049_function_coordinate(signature: str) -> tuple[str, str, int]:
    assert signature.startswith("public.") and signature.endswith(")")
    function_name, argument_types = signature[len("public.") : -1].split(
        "(", 1
    )
    database_argument_types = argument_types.replace(
        "timestamptz", "timestamp with time zone"
    )
    argument_count = 0 if not argument_types else argument_types.count(",") + 1
    return function_name, database_argument_types, argument_count


def _expected_0049_function_body_sha256(
    migration: object,
    *,
    signature: str,
    expected_revision: str,
    observation_scope_mode_fixed: bool,
) -> str:
    legacy_scope_completion_revisions = frozenset(
        {
            STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION,
            STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION,
            STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION,
            STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION,
        }
    )
    hardened_scope_completion_revisions = frozenset(
        {OPENING_TERMINAL_GUARD_EXECUTION_REVISION, HEAD_REVISION}
    )
    assert expected_revision in (
        legacy_scope_completion_revisions
        | hardened_scope_completion_revisions
    ), f"unsupported 0049 catalog revision: {expected_revision}"

    expected_body_sha256 = migration.EXPECTED_FUNCTION_BODY_SHA256[signature]
    if (
        observation_scope_mode_fixed
        and signature == migration.OBSERVATION_CALLER_0021_SIGNATURE
    ):
        expected_body_sha256 = STOCKTAKE_OBSERVATION_BODY_SHA256_0050
    if (
        expected_revision in hardened_scope_completion_revisions
        and signature == migration.SCOPE_COMPLETION_CALLER_0021_SIGNATURE
    ):
        opening_migration = (
            _load_opening_terminal_guard_execution_migration_0052()
        )
        assert signature == (
            opening_migration.SCOPE_COMPLETION_GUARD_SIGNATURE_0021
        )
        expected_body_sha256 = (
            opening_migration.FIXED_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021
        )
    return expected_body_sha256


def _assert_0049_recount_guard_catalog(
    *,
    callers_security_definer: bool,
    expected_revision: str,
    observation_scope_mode_fixed: bool | None = None,
) -> None:
    migration = _load_stocktake_recount_guard_security_migration_0049()
    assert migration.revision == STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION
    assert migration.down_revision == STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION
    assert migration.PREVIOUS_SCHEMA_REVISION == migration.down_revision
    assert len(migration.CALLER_SIGNATURES) == 9
    assert len(migration.INVOKER_SIGNATURES) == 4
    assert len(migration.ALL_FUNCTION_SIGNATURES) == 13
    assert set(migration.CALLER_SIGNATURES).isdisjoint(
        migration.INVOKER_SIGNATURES
    )
    assert set(migration.ALL_FUNCTION_SIGNATURES) == (
        set(migration.CALLER_SIGNATURES)
        | set(migration.INVOKER_SIGNATURES)
    )
    assert {
        row[0] for row in migration.FUNCTION_CATALOG
    } == set(migration.ALL_FUNCTION_SIGNATURES) == set(
        migration.EXPECTED_FUNCTION_BODY_SHA256
    )
    assert len(migration.TRIGGER_CATALOG) == 15
    assert {
        row[2] for row in migration.TRIGGER_CATALOG
    } == set(migration.TRIGGER_FUNCTION_SIGNATURES)

    function_names = [row[1] for row in migration.FUNCTION_CATALOG]
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT schema_row.nspname, function_row.proname, "
                "pg_catalog.oidvectortypes(function_row.proargtypes), "
                "pg_catalog.pg_get_function_result(function_row.oid), "
                "function_row.prokind, function_row.pronargs, "
                "function_row.proretset, function_row.proargmodes, "
                "function_row.pronargdefaults, "
                "function_row.proargdefaults IS NULL, "
                "function_row.provariadic, function_row.provolatile, "
                "function_row.proisstrict, function_row.proleakproof, "
                "function_row.proparallel, function_row.prosecdef, "
                "language_row.lanname, owner.rolname, "
                "function_row.proconfig, "
                "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
                "function_row.prosrc, 'UTF8')), 'hex'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_api', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_backup', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_projector', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_edge', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "%s, function_row.oid, 'EXECUTE'), "
                "EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE("
                "function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantee = 0 "
                "AND function_acl.privilege_type = 'EXECUTE'), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner)))), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantee = function_row.proowner "
                "AND function_acl.grantor = function_row.proowner "
                "AND function_acl.privilege_type = 'EXECUTE' "
                "AND NOT function_acl.is_grantable), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantee <> function_row.proowner "
                "OR function_acl.grantor <> function_row.proowner "
                "OR function_acl.privilege_type <> 'EXECUTE' "
                "OR function_acl.is_grantable) "
                "FROM pg_catalog.pg_proc AS function_row "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = function_row.pronamespace "
                "JOIN pg_catalog.pg_language AS language_row "
                "ON language_row.oid = function_row.prolang "
                "JOIN pg_catalog.pg_roles AS owner "
                "ON owner.oid = function_row.proowner "
                "WHERE schema_row.nspname = 'public' "
                "AND function_row.proname = ANY(%s) "
                "ORDER BY function_row.proname, "
                "pg_catalog.oidvectortypes(function_row.proargtypes)",
                (EDGE_RECEIVER_ROLE, function_names),
            )
            function_rows = cursor.fetchall()
            cursor.execute(
                "SELECT relation_schema.nspname, relation.relname, "
                "trigger_row.tgname, function_schema.nspname, "
                "function_row.proname, "
                "pg_catalog.oidvectortypes(function_row.proargtypes), "
                "trigger_row.tgenabled, trigger_row.tgtype, "
                "trigger_row.tgconstraint <> 0, "
                "trigger_row.tgdeferrable, trigger_row.tginitdeferred, "
                "trigger_row.tgqual IS NULL, trigger_row.tgnargs, "
                "trigger_row.tgattr::text, "
                "pg_catalog.pg_get_triggerdef(trigger_row.oid, true) "
                "FROM pg_catalog.pg_trigger AS trigger_row "
                "JOIN pg_catalog.pg_class AS relation "
                "ON relation.oid = trigger_row.tgrelid "
                "JOIN pg_catalog.pg_namespace AS relation_schema "
                "ON relation_schema.oid = relation.relnamespace "
                "JOIN pg_catalog.pg_proc AS function_row "
                "ON function_row.oid = trigger_row.tgfoid "
                "JOIN pg_catalog.pg_namespace AS function_schema "
                "ON function_schema.oid = function_row.pronamespace "
                "WHERE NOT trigger_row.tgisinternal "
                "AND function_schema.nspname = 'public' "
                "AND function_row.proname = ANY(%s) "
                "ORDER BY relation.relname, trigger_row.tgname",
                (function_names,),
            )
            trigger_rows = cursor.fetchall()
            cursor.execute(
                "SELECT function_row.prosrc "
                "FROM pg_catalog.pg_proc AS function_row "
                "WHERE function_row.oid = pg_catalog.to_regprocedure(%s)",
                (RLS_READY_FUNCTION,),
            )
            readiness_row = cursor.fetchone()

    if observation_scope_mode_fixed is None:
        observation_scope_mode_fixed = expected_revision in {
            STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION,
            STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION,
            HEAD_REVISION,
        }
    expected_function_rows = []
    for (
        signature,
        function_name,
        return_type,
        language,
        volatility,
        legacy_search_path,
    ) in migration.FUNCTION_CATALOG:
        coordinate_name, argument_types, argument_count = (
            _0049_function_coordinate(signature)
        )
        assert coordinate_name == function_name
        expected_search_path = legacy_search_path
        if callers_security_definer and signature in migration.CALLER_SIGNATURES:
            expected_search_path = migration.FIXED_SEARCH_PATH
        expected_function_rows.append(
            (
                "public",
                function_name,
                argument_types,
                return_type,
                "f",
                argument_count,
                False,
                None,
                0,
                True,
                0,
                volatility,
                False,
                False,
                "u",
                callers_security_definer
                and signature in migration.CALLER_SIGNATURES,
                language,
                "star_oam_migrator",
                (
                    None
                    if expected_search_path is None
                    else [expected_search_path]
                ),
                _expected_0049_function_body_sha256(
                    migration,
                    signature=signature,
                    expected_revision=expected_revision,
                    observation_scope_mode_fixed=(
                        observation_scope_mode_fixed
                    ),
                ),
                False,
                False,
                False,
                False,
                False,
                False,
                1,
                1,
                0,
            )
        )
    assert function_rows == sorted(
        expected_function_rows,
        key=lambda row: (row[1], row[2]),
    )

    function_coordinates = {
        signature: _0049_function_coordinate(signature)[:2]
        for signature in migration.ALL_FUNCTION_SIGNATURES
    }
    expected_trigger_rows = []
    for (
        table_name,
        trigger_name,
        function_signature,
        trigger_type,
        is_constraint,
        is_deferrable,
        is_initially_deferred,
    ) in migration.TRIGGER_CATALOG:
        function_name, function_arguments = function_coordinates[
            function_signature
        ]
        expected_trigger_rows.append(
            (
                "public",
                table_name,
                trigger_name,
                "public",
                function_name,
                function_arguments,
                "A",
                trigger_type,
                is_constraint,
                is_deferrable,
                is_initially_deferred,
                True,
                0,
                "",
            )
        )
    expected_trigger_rows.sort(key=lambda row: (row[1], row[2]))
    assert [row[:14] for row in trigger_rows] == expected_trigger_rows
    for row in trigger_rows:
        trigger_definition = " ".join(row[14].split())
        trigger_type = row[7]
        assert f" TRIGGER {row[2]} " in trigger_definition
        assert (
            f" ON {row[1]} " in trigger_definition
            or f" ON public.{row[1]} " in trigger_definition
        )
        assert trigger_definition.endswith(f"{row[4]}()")
        assert " WHEN " not in trigger_definition
        if trigger_type == 7:
            assert " BEFORE INSERT ON " in trigger_definition
        elif trigger_type == 19:
            assert " BEFORE UPDATE ON " in trigger_definition
        elif trigger_type == 23:
            assert " BEFORE INSERT OR UPDATE ON " in trigger_definition
        else:
            assert trigger_type == 29
            assert trigger_definition.startswith("CREATE CONSTRAINT TRIGGER ")
            assert " AFTER INSERT OR DELETE OR UPDATE ON " in trigger_definition
            assert " DEFERRABLE INITIALLY DEFERRED " in trigger_definition

    assert readiness_row is not None
    assert (
        f"pg_catalog.min(version_num) = '{expected_revision}'"
        in readiness_row[0]
    )


def _assert_0049_api_direct_execute_denied() -> None:
    migration = _load_stocktake_recount_guard_security_migration_0049()
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_api",
            password=_role_password("star_oam_api"),
        ),
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            for signature in migration.ALL_FUNCTION_SIGNATURES:
                function_name, argument_types, _ = _0049_function_coordinate(
                    signature
                )
                arguments = ", ".join(
                    f"NULL::{argument_type.strip()}"
                    for argument_type in argument_types.split(",")
                    if argument_type.strip()
                )
                with pytest.raises(psycopg.Error) as error:
                    cursor.execute(
                        f"SELECT public.{function_name}({arguments})"
                    )
                assert error.value.sqlstate == "42501"


def _assert_0051_difference_completion_catalog(
    *,
    repaired: bool,
    expected_revision: str,
) -> None:
    migration = _load_stocktake_difference_authorization_hash_migration_0051()
    assert migration.revision == (
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION
    )
    assert migration.down_revision == STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION
    assert migration.PREVIOUS_SCHEMA_REVISION == migration.down_revision
    expected_body_sha256 = (
        migration.FIXED_BODY_SHA256
        if repaired
        else migration.LEGACY_BODY_SHA256
    )
    expected_source_fragment = (
        migration.FIXED_SOURCE_FRAGMENT
        if repaired
        else migration.LEGACY_SOURCE_FRAGMENT
    )
    forbidden_source_fragment = (
        migration.LEGACY_SOURCE_FRAGMENT
        if repaired
        else migration.FIXED_SOURCE_FRAGMENT
    )
    expected_trigger_enabled = (
        migration.FIXED_TRIGGER_ENABLED
        if repaired
        else migration.LEGACY_TRIGGER_ENABLED
    )

    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT schema_row.nspname, function_row.proname, "
                "pg_catalog.oidvectortypes(function_row.proargtypes), "
                "pg_catalog.pg_get_function_result(function_row.oid), "
                "function_row.prokind, function_row.pronargs, "
                "function_row.proretset, function_row.proargmodes, "
                "function_row.pronargdefaults, "
                "function_row.proargdefaults IS NULL, "
                "function_row.provariadic, function_row.provolatile, "
                "function_row.proisstrict, function_row.proleakproof, "
                "function_row.proparallel, function_row.prosecdef, "
                "language_row.lanname, owner.rolname, "
                "function_row.proconfig, "
                "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
                "function_row.prosrc, 'UTF8')), 'hex'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_api', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_backup', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_projector', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_edge', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "%s, function_row.oid, 'EXECUTE'), "
                "EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE("
                "function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantee = 0 "
                "AND function_acl.privilege_type = 'EXECUTE'), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner)))), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantee = function_row.proowner "
                "AND function_acl.grantor = function_row.proowner "
                "AND function_acl.privilege_type = 'EXECUTE' "
                "AND NOT function_acl.is_grantable), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantee <> function_row.proowner "
                "OR function_acl.grantor <> function_row.proowner "
                "OR function_acl.privilege_type <> 'EXECUTE' "
                "OR function_acl.is_grantable), function_row.prosrc "
                "FROM pg_catalog.pg_proc AS function_row "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = function_row.pronamespace "
                "JOIN pg_catalog.pg_language AS language_row "
                "ON language_row.oid = function_row.prolang "
                "JOIN pg_catalog.pg_roles AS owner "
                "ON owner.oid = function_row.proowner "
                "WHERE function_row.oid = pg_catalog.to_regprocedure(%s)",
                (EDGE_RECEIVER_ROLE, migration.FUNCTION_SIGNATURE),
            )
            function_rows = cursor.fetchall()
            cursor.execute(
                "SELECT pg_catalog.count(*) "
                "FROM pg_catalog.pg_proc AS function_row "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = function_row.pronamespace "
                "WHERE schema_row.nspname = 'public' "
                "AND function_row.proname = %s",
                (migration.FUNCTION_NAME,),
            )
            function_name_count = cursor.fetchone()
            cursor.execute(
                "SELECT relation_schema.nspname, relation.relname, "
                "trigger_row.tgname, function_schema.nspname, "
                "function_row.proname, "
                "pg_catalog.oidvectortypes(function_row.proargtypes), "
                "trigger_row.tgenabled, trigger_row.tgtype, "
                "trigger_row.tgconstraint <> 0, "
                "trigger_row.tgdeferrable, trigger_row.tginitdeferred, "
                "trigger_row.tgqual IS NULL, trigger_row.tgnargs, "
                "trigger_row.tgattr::text, "
                "pg_catalog.pg_get_triggerdef(trigger_row.oid, true) "
                "FROM pg_catalog.pg_trigger AS trigger_row "
                "JOIN pg_catalog.pg_class AS relation "
                "ON relation.oid = trigger_row.tgrelid "
                "JOIN pg_catalog.pg_namespace AS relation_schema "
                "ON relation_schema.oid = relation.relnamespace "
                "JOIN pg_catalog.pg_proc AS function_row "
                "ON function_row.oid = trigger_row.tgfoid "
                "JOIN pg_catalog.pg_namespace AS function_schema "
                "ON function_schema.oid = function_row.pronamespace "
                "WHERE NOT trigger_row.tgisinternal AND ("
                "trigger_row.tgfoid = pg_catalog.to_regprocedure(%s) "
                "OR trigger_row.tgname = %s) "
                "ORDER BY relation_schema.nspname, relation.relname, "
                "trigger_row.tgname",
                (migration.FUNCTION_SIGNATURE, migration.TRIGGER_NAME),
            )
            trigger_rows = cursor.fetchall()
            cursor.execute(
                "SELECT function_row.prosrc "
                "FROM pg_catalog.pg_proc AS function_row "
                "WHERE function_row.oid = pg_catalog.to_regprocedure(%s)",
                (RLS_READY_FUNCTION,),
            )
            readiness_row = cursor.fetchone()

    assert function_name_count == (1,)
    assert len(function_rows) == 1
    function_row = function_rows[0]
    assert function_row[:18] == (
        "public",
        migration.FUNCTION_NAME,
        "",
        "trigger",
        "f",
        0,
        False,
        None,
        0,
        True,
        0,
        "v",
        False,
        False,
        "u",
        False,
        "plpgsql",
        "star_oam_migrator",
    )
    assert tuple(function_row[18] or ()) == (migration.FIXED_SEARCH_PATH,)
    assert function_row[19:29] == (
        expected_body_sha256,
        False,
        False,
        False,
        False,
        False,
        False,
        1,
        1,
        0,
    )
    function_source = function_row[29]
    assert function_source.count(expected_source_fragment) == 1
    assert forbidden_source_fragment not in function_source

    assert len(trigger_rows) == 1
    trigger_row = trigger_rows[0]
    assert trigger_row[:14] == (
        "public",
        migration.TRIGGER_TABLE,
        migration.TRIGGER_NAME,
        "public",
        migration.FUNCTION_NAME,
        "",
        expected_trigger_enabled,
        7,
        False,
        False,
        False,
        True,
        0,
        "",
    )
    trigger_definition = " ".join(trigger_row[14].split())
    assert f" TRIGGER {migration.TRIGGER_NAME} " in trigger_definition
    assert (
        f" ON {migration.TRIGGER_TABLE} " in trigger_definition
        or f" ON public.{migration.TRIGGER_TABLE} " in trigger_definition
    )
    assert " BEFORE INSERT ON " in trigger_definition
    assert trigger_definition.endswith(f"{migration.FUNCTION_NAME}()")
    assert " WHEN " not in trigger_definition
    assert readiness_row is not None
    assert (
        f"pg_catalog.min(version_num) = '{expected_revision}'"
        in readiness_row[0]
    )


def _assert_0051_api_direct_execute_denied() -> None:
    migration = _load_stocktake_difference_authorization_hash_migration_0051()
    _assert_raw_sql_denied(
        "star_oam_api",
        f"SELECT {migration.FUNCTION_SIGNATURE}",
        (),
        require_rls=False,
    )


def _assert_0052_opening_terminal_catalog(
    *,
    hardened: bool,
    expected_revision: str,
) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    assert migration.revision == OPENING_TERMINAL_GUARD_EXECUTION_REVISION
    assert migration.down_revision == (
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION
    )
    assert migration.PREVIOUS_SCHEMA_REVISION == migration.down_revision
    assert len(migration.PERSISTENT_FUNCTION_SIGNATURES) == 7
    assert len(migration.HEAD_ONLY_FUNCTION_CATALOG) == 10
    assert len(migration.INHERITED_RECONCILIATION_FUNCTION_CATALOG) == 3
    assert len(migration.TRIGGER_CATALOG) == 12
    assert len(migration.REVIEW_GUARD_TRIGGER_CATALOG) == 6
    assert len(migration.INHERITED_RECONCILIATION_TRIGGER_CATALOG) == 6
    assert len(migration.OPENING_0052_TRIGGER_CATALOG) == 20
    assert len(migration.GRAPH_CLOSURE_TRIGGER_CATALOG) == 15
    assert len(migration.LOCK_TABLES) == 57

    persistent_function_catalog = (
        (
            migration.ACTOR_ASSIGNMENT_SIGNATURE,
            migration.ACTOR_ASSIGNMENT_FUNCTION,
            "boolean",
            "sql",
            "s",
            (
                "text",
                "uuid",
                "uuid",
                "bigint",
                "timestamp with time zone",
                "text",
                "text",
                "text",
            ),
            (
                "p_user_id",
                "p_person_id",
                "p_assignment_id",
                "p_authorization_version",
                "p_occurred_at",
                "p_role_code",
                "p_scope_type",
                "p_scope_id",
            ),
            False,
            False,
            None,
            migration.ACTOR_ASSIGNMENT_BODY_SHA256,
            False,
        ),
        (
            migration.REVIEW_COMPLETION_SIGNATURE,
            migration.REVIEW_COMPLETION_FUNCTION,
            "trigger",
            "plpgsql",
            "v",
            (),
            (),
            False,
            False,
            (migration.FIXED_SEARCH_PATH,) if hardened else None,
            migration.REVIEW_COMPLETION_BODY_SHA256,
            False,
        ),
        (
            migration.REVIEW_IMMUTABLE_SIGNATURE,
            migration.REVIEW_IMMUTABLE_FUNCTION,
            "trigger",
            "plpgsql",
            "v",
            (),
            (),
            False,
            False,
            None,
            migration.REVIEW_IMMUTABLE_BODY_SHA256,
            False,
        ),
        (
            migration.SCOPE_COMPLETION_GUARD_SIGNATURE_0021,
            migration.SCOPE_COMPLETION_GUARD_FUNCTION_0021,
            "trigger",
            "plpgsql",
            "v",
            (),
            (),
            True,
            False,
            (migration.FIXED_SEARCH_PATH,),
            (
                migration.FIXED_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021
                if hardened
                else migration.LEGACY_SCOPE_COMPLETION_GUARD_BODY_SHA256_0021
            ),
            False,
        ),
        (
            migration.GRAPH_SIGNATURE,
            migration.GRAPH_FUNCTION,
            "boolean",
            "sql",
            "s",
            ("uuid", "uuid"),
            ("p_task_id", "p_transaction_id"),
            False,
            False,
            (migration.FIXED_SEARCH_PATH,),
            migration.GRAPH_BODY_SHA256,
            False,
        ),
        (
            migration.COMMIT_SIGNATURE,
            migration.COMMIT_FUNCTION,
            "trigger",
            "plpgsql",
            "v",
            (),
            (),
            hardened,
            False,
            (migration.FIXED_SEARCH_PATH,),
            (
                migration.FIXED_COMMIT_BODY_SHA256
                if hardened
                else migration.LEGACY_COMMIT_BODY_SHA256
            ),
            False,
        ),
        (
            migration.ACCOUNT_SIGNATURE,
            migration.ACCOUNT_FUNCTION,
            "trigger",
            "plpgsql",
            "v",
            (),
            (),
            hardened,
            False,
            (migration.FIXED_SEARCH_PATH,),
            (
                migration.FIXED_ACCOUNT_BODY_SHA256
                if hardened
                else migration.LEGACY_ACCOUNT_BODY_SHA256
            ),
            False,
        ),
    )
    expected_function_catalog = [
        *persistent_function_catalog,
        *migration.INHERITED_RECONCILIATION_FUNCTION_CATALOG,
    ]
    if hardened:
        expected_function_catalog.extend(
            (
                row[0],
                row[1],
                row[2],
                row[3],
                row[4],
                row[5],
                row[6],
                row[7],
                False,
                row[8],
                row[9],
                False,
            )
            for row in migration.HEAD_ONLY_FUNCTION_CATALOG
        )
    function_names = sorted({row[1] for row in expected_function_catalog})
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT schema_row.nspname, function_row.proname, "
                "pg_catalog.oidvectortypes(function_row.proargtypes), "
                "pg_catalog.pg_get_function_result(function_row.oid), "
                "function_row.prokind, function_row.pronargs, "
                "function_row.proargnames, function_row.proallargtypes, "
                "function_row.proargmodes, function_row.pronargdefaults, "
                "function_row.proargdefaults IS NULL, "
                "function_row.provariadic, NOT function_row.proretset, "
                "function_row.provolatile, "
                "function_row.proisstrict, function_row.proleakproof, "
                "function_row.proparallel, function_row.prosecdef, "
                "language_row.lanname, owner.rolname, "
                "function_row.proconfig, "
                "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
                "function_row.prosrc, 'UTF8')), 'hex'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_api', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_backup', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_projector', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_edge', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "%s, function_row.oid, 'EXECUTE'), "
                "EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE("
                "function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantee = 0 "
                "AND function_acl.privilege_type = 'EXECUTE'), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner)))), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantee = function_row.proowner "
                "AND function_acl.grantor = function_row.proowner "
                "AND function_acl.privilege_type = 'EXECUTE' "
                "AND NOT function_acl.is_grantable), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantee = pg_catalog.to_regrole("
                "'star_oam_api') AND function_acl.grantor = "
                "function_row.proowner AND function_acl.privilege_type = "
                "'EXECUTE' AND NOT function_acl.is_grantable), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantor <> function_row.proowner "
                "OR function_acl.privilege_type <> 'EXECUTE' "
                "OR function_acl.is_grantable OR function_acl.grantee "
                "NOT IN (function_row.proowner, pg_catalog.to_regrole("
                "'star_oam_api'))), function_row.prosrc "
                "FROM pg_catalog.pg_proc AS function_row "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = function_row.pronamespace "
                "JOIN pg_catalog.pg_language AS language_row "
                "ON language_row.oid = function_row.prolang "
                "JOIN pg_catalog.pg_roles AS owner "
                "ON owner.oid = function_row.proowner "
                "WHERE schema_row.nspname = 'public' "
                "AND function_row.proname = ANY(%s) "
                "ORDER BY function_row.proname, "
                "pg_catalog.oidvectortypes(function_row.proargtypes)",
                (EDGE_RECEIVER_ROLE, function_names),
            )
            function_rows = cursor.fetchall()
            cursor.execute(
                "SELECT relation_schema.nspname, relation.relname, "
                "trigger_row.tgname, function_schema.nspname, "
                "function_row.proname, "
                "pg_catalog.oidvectortypes(function_row.proargtypes), "
                "trigger_row.tgenabled, trigger_row.tgtype, "
                "trigger_row.tgconstraint <> 0, "
                "trigger_row.tgdeferrable, trigger_row.tginitdeferred, "
                "trigger_row.tgconstrrelid = 0, "
                "trigger_row.tgconstrindid = 0, "
                "trigger_row.tgparentid = 0, "
                "trigger_row.tgqual IS NULL, "
                "trigger_row.tgoldtable IS NULL, "
                "trigger_row.tgnewtable IS NULL, trigger_row.tgnargs, "
                "trigger_row.tgattr::text, "
                "pg_catalog.pg_get_triggerdef(trigger_row.oid, true) "
                "FROM pg_catalog.pg_trigger AS trigger_row "
                "JOIN pg_catalog.pg_class AS relation "
                "ON relation.oid = trigger_row.tgrelid "
                "JOIN pg_catalog.pg_namespace AS relation_schema "
                "ON relation_schema.oid = relation.relnamespace "
                "JOIN pg_catalog.pg_proc AS function_row "
                "ON function_row.oid = trigger_row.tgfoid "
                "JOIN pg_catalog.pg_namespace AS function_schema "
                "ON function_schema.oid = function_row.pronamespace "
                "WHERE NOT trigger_row.tgisinternal "
                "AND function_schema.nspname = 'public' "
                "AND function_row.proname = ANY(%s) "
                "ORDER BY relation_schema.nspname, relation.relname, "
                "trigger_row.tgname",
                (function_names,),
            )
            trigger_rows = cursor.fetchall()
            cursor.execute(
                "SELECT function_row.prosrc "
                "FROM pg_catalog.pg_proc AS function_row "
                "WHERE function_row.oid = pg_catalog.to_regprocedure(%s)",
                (RLS_READY_FUNCTION,),
            )
            readiness_row = cursor.fetchone()
            head_only_presence = []
            for signature in migration.HEAD_ONLY_FUNCTION_SIGNATURES:
                cursor.execute(
                    "SELECT pg_catalog.to_regprocedure(%s) IS NOT NULL",
                    (signature,),
                )
                head_only_presence.append(cursor.fetchone() == (True,))

    assert head_only_presence == (
        [True] * len(migration.HEAD_ONLY_FUNCTION_SIGNATURES)
        if hardened
        else [False] * len(migration.HEAD_ONLY_FUNCTION_SIGNATURES)
    )

    expected_rows = []
    for (
        _signature,
        function_name,
        return_type,
        language,
        volatility,
        argument_types,
        argument_names,
        security_definer,
        strict,
        search_path,
        body_sha256,
        api_execute,
    ) in sorted(expected_function_catalog, key=lambda row: (row[1], row[0])):
        expected_rows.append(
            (
                "public",
                function_name,
                ", ".join(argument_types),
                return_type,
                "f",
                len(argument_types),
                list(argument_names) if argument_names else None,
                None,
                None,
                0,
                True,
                0,
                True,
                volatility,
                strict,
                False,
                "u",
                security_definer,
                language,
                "star_oam_migrator",
                list(search_path) if search_path else None,
                body_sha256,
                api_execute,
                False,
                False,
                False,
                False,
                False,
                1 + int(api_execute),
                1,
                int(api_execute),
                0,
            )
        )
    assert [row[:32] for row in function_rows] == expected_rows
    sources = {row[1]: row[32] for row in function_rows}
    assert (
        migration.FIXED_TASK_BRANCH
        if hardened
        else migration.LEGACY_TASK_BRANCH
    ) in sources[migration.COMMIT_FUNCTION]
    assert (
        migration.FIXED_ACCOUNT_PRINCIPAL_FRAGMENT
        if hardened
        else migration.LEGACY_ACCOUNT_PRINCIPAL_FRAGMENT
    ) in sources[migration.ACCOUNT_FUNCTION]
    scope_completion_source = sources[
        migration.SCOPE_COMPLETION_GUARD_FUNCTION_0021
    ]
    assert (
        migration.FIXED_SCOPE_COMPLETION_TOTAL_DECLARATION_0021
        if hardened
        else migration.LEGACY_SCOPE_COMPLETION_TOTAL_DECLARATION_0021
    ) in scope_completion_source
    assert (
        migration.LEGACY_SCOPE_COMPLETION_TOTAL_DECLARATION_0021
        if hardened
        else migration.FIXED_SCOPE_COMPLETION_TOTAL_DECLARATION_0021
    ) not in scope_completion_source

    expected_trigger_catalog = [
        *(
            (
                table_name,
                trigger_name,
                signature,
                trigger_type,
                True,
                True,
                True,
                "A",
            )
            for table_name, trigger_name, signature, trigger_type
            in migration.TRIGGER_CATALOG
        ),
        *(
            (
                table_name,
                trigger_name,
                signature,
                trigger_type,
                False,
                False,
                False,
                "A",
            )
            for table_name, trigger_name, signature, trigger_type
            in migration.REVIEW_GUARD_TRIGGER_CATALOG
        ),
        *(
            (
                table_name,
                trigger_name,
                signature,
                trigger_type,
                False,
                False,
                False,
                "A",
            )
            for table_name, trigger_name, signature, trigger_type
            in migration.INHERITED_RECONCILIATION_TRIGGER_CATALOG
        ),
    ]
    if hardened:
        expected_trigger_catalog.extend(migration.OPENING_0052_TRIGGER_CATALOG)
    expected_triggers = []
    for (
        table_name,
        trigger_name,
        signature,
        trigger_type,
        constraint,
        deferrable,
        initially_deferred,
        enabled,
    ) in expected_trigger_catalog:
        function_name, argument_types, _ = _0049_function_coordinate(
            signature
        )
        expected_triggers.append(
            (
                "public",
                table_name,
                trigger_name,
                "public",
                function_name,
                argument_types,
                enabled,
                trigger_type,
                constraint,
                deferrable,
                initially_deferred,
                True,
                True,
                True,
                True,
                True,
                True,
                0,
                "",
            )
        )
    expected_triggers.sort(key=lambda row: (row[0], row[1], row[2]))
    assert [row[:19] for row in trigger_rows] == expected_triggers
    for row in trigger_rows:
        trigger_definition = " ".join(row[19].split())
        expected_prefix = (
            "CREATE CONSTRAINT TRIGGER " if row[8] else "CREATE TRIGGER "
        )
        assert trigger_definition.startswith(expected_prefix)
        assert f" TRIGGER {row[2]} " in trigger_definition
        if row[9]:
            assert " DEFERRABLE INITIALLY DEFERRED " in trigger_definition
        else:
            assert " DEFERRABLE " not in trigger_definition
        assert trigger_definition.endswith(f"{row[4]}()")
        assert " WHEN " not in trigger_definition

    assert readiness_row is not None
    assert (
        f"pg_catalog.min(version_num) = '{expected_revision}'"
        in readiness_row[0]
    )


def _assert_0052_api_direct_execute_denied(*, hardened: bool = True) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    signatures = list(migration.PERSISTENT_FUNCTION_SIGNATURES)
    if hardened:
        signatures.extend(migration.HEAD_ONLY_FUNCTION_SIGNATURES)
    signatures.extend(
        row[0]
        for row in migration.INHERITED_RECONCILIATION_FUNCTION_CATALOG
        if not row[-1]
    )
    for signature in signatures:
        function_name, argument_types, _ = _0049_function_coordinate(signature)
        arguments = ", ".join(
            f"NULL::{argument_type.strip()}"
            for argument_type in argument_types.split(",")
            if argument_type.strip()
        )
        _assert_raw_sql_denied(
            "star_oam_api",
            f"SELECT public.{function_name}({arguments})",
            (),
            require_rls=False,
        )


def _set_0049_extra_trigger_alias(*, present: bool) -> None:
    parameters = _connection_parameters(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    with psycopg.connect(**parameters, autocommit=True) as connection:
        with connection.cursor() as cursor:
            if present:
                cursor.execute(
                    f"CREATE CONSTRAINT TRIGGER "
                    f"{STOCKTAKE_RECOUNT_ALIAS_TRIGGER_0049} "
                    "AFTER INSERT OR UPDATE OR DELETE ON "
                    "public.stocktake_review_items "
                    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                    "EXECUTE FUNCTION public."
                    "rsc_validate_stocktake_count_line_insert_0021()"
                )
                cursor.execute(
                    "ALTER TABLE public.stocktake_review_items "
                    "ENABLE ALWAYS TRIGGER "
                    f"{STOCKTAKE_RECOUNT_ALIAS_TRIGGER_0049}"
                )
            else:
                cursor.execute(
                    "DROP TRIGGER IF EXISTS "
                    f"{STOCKTAKE_RECOUNT_ALIAS_TRIGGER_0049} ON "
                    "public.stocktake_review_items"
                )


def _0049_extra_trigger_alias_exists() -> bool:
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT trigger_row.tgenabled "
                "FROM pg_catalog.pg_trigger AS trigger_row "
                "JOIN pg_catalog.pg_class AS relation "
                "ON relation.oid = trigger_row.tgrelid "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = relation.relnamespace "
                "WHERE schema_row.nspname = 'public' "
                "AND relation.relname = 'stocktake_review_items' "
                "AND trigger_row.tgname = %s",
                (STOCKTAKE_RECOUNT_ALIAS_TRIGGER_0049,),
            )
            rows = cursor.fetchall()
    assert rows in ([], [("A",)])
    return rows == [("A",)]


def _set_0050_extra_trigger_alias(*, present: bool) -> None:
    migration = _load_stocktake_observation_scope_mode_migration_0050()
    parameters = _connection_parameters(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    with psycopg.connect(**parameters, autocommit=True) as connection:
        with connection.cursor() as cursor:
            if present:
                cursor.execute(
                    f"CREATE TRIGGER {STOCKTAKE_OBSERVATION_ALIAS_TRIGGER_0050} "
                    f"BEFORE INSERT ON public.{migration.TRIGGER_TABLE} "
                    "FOR EACH ROW EXECUTE FUNCTION "
                    f"{migration.FUNCTION_SIGNATURE}"
                )
                cursor.execute(
                    f"ALTER TABLE public.{migration.TRIGGER_TABLE} "
                    "ENABLE ALWAYS TRIGGER "
                    f"{STOCKTAKE_OBSERVATION_ALIAS_TRIGGER_0050}"
                )
            else:
                cursor.execute(
                    "DROP TRIGGER IF EXISTS "
                    f"{STOCKTAKE_OBSERVATION_ALIAS_TRIGGER_0050} ON "
                    f"public.{migration.TRIGGER_TABLE}"
                )


def _0050_extra_trigger_alias_exists() -> bool:
    migration = _load_stocktake_observation_scope_mode_migration_0050()
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT trigger_row.tgenabled "
                "FROM pg_catalog.pg_trigger AS trigger_row "
                "JOIN pg_catalog.pg_class AS relation "
                "ON relation.oid = trigger_row.tgrelid "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = relation.relnamespace "
                "WHERE schema_row.nspname = 'public' "
                "AND relation.relname = %s "
                "AND trigger_row.tgname = %s",
                (
                    migration.TRIGGER_TABLE,
                    STOCKTAKE_OBSERVATION_ALIAS_TRIGGER_0050,
                ),
            )
            rows = cursor.fetchall()
    assert rows in ([], [("A",)])
    return rows == [("A",)]


def _set_0051_extra_trigger_alias(*, present: bool) -> None:
    migration = _load_stocktake_difference_authorization_hash_migration_0051()
    parameters = _connection_parameters(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    with psycopg.connect(**parameters, autocommit=True) as connection:
        with connection.cursor() as cursor:
            if present:
                cursor.execute(
                    f"CREATE TRIGGER {STOCKTAKE_DIFFERENCE_ALIAS_TRIGGER_0051} "
                    f"BEFORE INSERT ON public.{migration.TRIGGER_TABLE} "
                    "FOR EACH ROW EXECUTE FUNCTION "
                    f"{migration.FUNCTION_SIGNATURE}"
                )
                cursor.execute(
                    f"ALTER TABLE public.{migration.TRIGGER_TABLE} "
                    "ENABLE ALWAYS TRIGGER "
                    f"{STOCKTAKE_DIFFERENCE_ALIAS_TRIGGER_0051}"
                )
            else:
                cursor.execute(
                    "DROP TRIGGER IF EXISTS "
                    f"{STOCKTAKE_DIFFERENCE_ALIAS_TRIGGER_0051} ON "
                    f"public.{migration.TRIGGER_TABLE}"
                )


def _0051_extra_trigger_alias_exists() -> bool:
    migration = _load_stocktake_difference_authorization_hash_migration_0051()
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT trigger_row.tgenabled "
                "FROM pg_catalog.pg_trigger AS trigger_row "
                "JOIN pg_catalog.pg_class AS relation "
                "ON relation.oid = trigger_row.tgrelid "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = relation.relnamespace "
                "WHERE schema_row.nspname = 'public' "
                "AND relation.relname = %s "
                "AND trigger_row.tgname = %s",
                (
                    migration.TRIGGER_TABLE,
                    STOCKTAKE_DIFFERENCE_ALIAS_TRIGGER_0051,
                ),
            )
            rows = cursor.fetchall()
    assert rows in ([], [("A",)])
    return rows == [("A",)]


def _set_0051_primary_trigger_enabled(*, always: bool) -> None:
    migration = _load_stocktake_difference_authorization_hash_migration_0051()
    parameters = _connection_parameters(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    enable_mode = "ENABLE ALWAYS" if always else "ENABLE"
    with psycopg.connect(**parameters, autocommit=True) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"ALTER TABLE public.{migration.TRIGGER_TABLE} "
                f"{enable_mode} TRIGGER {migration.TRIGGER_NAME}"
            )


def _assert_0049_startup_rejects_extra_trigger_alias(api_engine) -> None:
    from app.database_security import DatabaseSecurityBoundaryError

    _set_0049_extra_trigger_alias(present=True)
    assert _0049_extra_trigger_alias_exists() is True
    try:
        with pytest.raises(DatabaseSecurityBoundaryError):
            _validate_runtime_security(api_engine)
    finally:
        _set_0049_extra_trigger_alias(present=False)
    assert _0049_extra_trigger_alias_exists() is False
    _validate_runtime_security(api_engine)


def _assert_0051_startup_rejects_trigger_drift(api_engine) -> None:
    from app.database_security import DatabaseSecurityBoundaryError

    _set_0051_extra_trigger_alias(present=True)
    assert _0051_extra_trigger_alias_exists() is True
    try:
        with pytest.raises(DatabaseSecurityBoundaryError):
            _validate_runtime_security(api_engine)
    finally:
        _set_0051_extra_trigger_alias(present=False)
    assert _0051_extra_trigger_alias_exists() is False

    _set_0051_primary_trigger_enabled(always=False)
    try:
        with pytest.raises(DatabaseSecurityBoundaryError):
            _validate_runtime_security(api_engine)
    finally:
        _set_0051_primary_trigger_enabled(always=True)
    _validate_runtime_security(api_engine)


def _assert_0048_scope_guard_catalog(
    *,
    security_definer: bool,
    expected_revision: str,
) -> None:
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT schema_row.nspname, function_row.proname, "
                "pg_catalog.oidvectortypes(function_row.proargtypes), "
                "pg_catalog.pg_get_function_result(function_row.oid), "
                "function_row.prokind, function_row.pronargs, "
                "function_row.provolatile, function_row.proisstrict, "
                "function_row.proleakproof, function_row.proparallel, "
                "function_row.prosecdef, language_row.lanname, "
                "owner.rolname, function_row.proconfig, "
                "pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to("
                "function_row.prosrc, 'UTF8')), 'hex'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_api', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_backup', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_projector', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "'star_oam_edge', function_row.oid, 'EXECUTE'), "
                "pg_catalog.has_function_privilege("
                "%s, function_row.oid, 'EXECUTE'), "
                "EXISTS (SELECT 1 FROM pg_catalog.aclexplode(COALESCE("
                "function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantee = 0 "
                "AND function_acl.privilege_type = 'EXECUTE'), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner)))), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantee = function_row.proowner "
                "AND function_acl.privilege_type = 'EXECUTE'), "
                "(SELECT pg_catalog.count(*) FROM pg_catalog.aclexplode("
                "COALESCE(function_row.proacl, pg_catalog.acldefault("
                "'f', function_row.proowner))) AS function_acl "
                "WHERE function_acl.grantee <> function_row.proowner "
                "OR function_acl.privilege_type <> 'EXECUTE') "
                "FROM pg_catalog.pg_proc AS function_row "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = function_row.pronamespace "
                "JOIN pg_catalog.pg_language AS language_row "
                "ON language_row.oid = function_row.prolang "
                "JOIN pg_catalog.pg_roles AS owner "
                "ON owner.oid = function_row.proowner "
                "WHERE schema_row.nspname = 'public' "
                "AND function_row.proname = %s "
                "ORDER BY function_row.oid",
                (
                    EDGE_RECEIVER_ROLE,
                    STOCKTAKE_SCOPE_GUARD_FUNCTION_0048,
                ),
            )
            function_rows = cursor.fetchall()
            cursor.execute(
                "SELECT schema_row.nspname, relation.relname, "
                "trigger_row.tgname, function_row.proname, "
                "trigger_row.tgenabled, trigger_row.tgtype, "
                "trigger_row.tgconstraint = 0, "
                "trigger_row.tgdeferrable, trigger_row.tginitdeferred, "
                "trigger_row.tgqual IS NULL, trigger_row.tgnargs, "
                "trigger_row.tgattr::text, "
                "pg_catalog.pg_get_triggerdef(trigger_row.oid, true) "
                "FROM pg_catalog.pg_trigger AS trigger_row "
                "JOIN pg_catalog.pg_class AS relation "
                "ON relation.oid = trigger_row.tgrelid "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = relation.relnamespace "
                "JOIN pg_catalog.pg_proc AS function_row "
                "ON function_row.oid = trigger_row.tgfoid "
                "WHERE NOT trigger_row.tgisinternal "
                "AND trigger_row.tgname = %s "
                "ORDER BY trigger_row.oid",
                (STOCKTAKE_SCOPE_GUARD_TRIGGER_0048,),
            )
            trigger_rows = cursor.fetchall()
            cursor.execute(
                "SELECT function_row.prosrc "
                "FROM pg_catalog.pg_proc AS function_row "
                "WHERE function_row.oid = pg_catalog.to_regprocedure(%s)",
                ("public.rsc_oam_runtime_binding_ready_0044()",),
            )
            readiness_row = cursor.fetchone()

    assert function_rows == [
        (
            "public",
            STOCKTAKE_SCOPE_GUARD_FUNCTION_0048,
            "",
            "trigger",
            "f",
            0,
            "v",
            False,
            False,
            "u",
            security_definer,
            "plpgsql",
            "star_oam_migrator",
            ["search_path=pg_catalog, public"],
            STOCKTAKE_SCOPE_GUARD_BODY_SHA256_0048,
            False,
            False,
            False,
            False,
            False,
            False,
            1,
            1,
            0,
        )
    ]
    assert len(trigger_rows) == 1
    trigger = trigger_rows[0]
    assert trigger[:12] == (
        "public",
        "stocktake_scopes",
        STOCKTAKE_SCOPE_GUARD_TRIGGER_0048,
        STOCKTAKE_SCOPE_GUARD_FUNCTION_0048,
        "A",
        7,
        True,
        False,
        False,
        True,
        0,
        "",
    )
    trigger_definition = " ".join(trigger[12].split())
    assert " BEFORE INSERT ON " in trigger_definition
    assert "stocktake_scopes FOR EACH ROW" in trigger_definition
    assert " FOR EACH ROW EXECUTE FUNCTION " in trigger_definition
    assert trigger_definition.endswith(
        f"{STOCKTAKE_SCOPE_GUARD_FUNCTION_0048}()"
    )
    assert " UPDATE " not in trigger_definition
    assert " DELETE " not in trigger_definition
    assert " WHEN " not in trigger_definition
    assert readiness_row is not None
    assert (
        f"pg_catalog.min(version_num) = '{expected_revision}'"
        in readiness_row[0]
    )


def _assert_0048_scope_guard_master_data_stays_read_only() -> None:
    master_tables = ("organizations", "stock_locations")
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT relation.relname, "
                "pg_catalog.has_table_privilege("
                "'star_oam_api', relation.oid, 'SELECT'), "
                "pg_catalog.has_table_privilege("
                "'star_oam_api', relation.oid, 'INSERT'), "
                "pg_catalog.has_table_privilege("
                "'star_oam_api', relation.oid, 'UPDATE'), "
                "pg_catalog.has_any_column_privilege("
                "'star_oam_api', relation.oid, 'UPDATE'), "
                "pg_catalog.has_table_privilege("
                "'star_oam_api', relation.oid, 'DELETE'), "
                "pg_catalog.has_table_privilege("
                "'star_oam_api', relation.oid, 'TRIGGER') "
                "FROM pg_catalog.pg_class AS relation "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = relation.relnamespace "
                "WHERE schema_row.nspname = 'public' "
                "AND relation.relname = ANY(%s) "
                "ORDER BY relation.relname",
                (list(master_tables),),
            )
            assert cursor.fetchall() == [
                ("organizations", True, False, False, False, False, False),
                ("stock_locations", True, False, False, False, False, False),
            ]

    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            for table_name in master_tables:
                cursor.execute(f"SELECT id FROM public.{table_name} LIMIT 0")

    _assert_raw_sql_denied(
        "star_oam_api",
        f"SELECT public.{STOCKTAKE_SCOPE_GUARD_FUNCTION_0048}()",
        (),
        require_rls=False,
    )
    for table_name in master_tables:
        _assert_raw_sql_denied(
            "star_oam_api",
            f"SELECT id FROM public.{table_name} LIMIT 1 FOR SHARE",
            (),
            require_rls=False,
        )
        _assert_raw_sql_denied(
            "star_oam_api",
            f"UPDATE public.{table_name} SET status = status WHERE FALSE",
            (),
            require_rls=False,
        )


def _set_0052_commit_security(*, security_definer: bool) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    security = "DEFINER" if security_definer else "INVOKER"
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        ),
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"ALTER FUNCTION {migration.COMMIT_SIGNATURE} "
                f"SECURITY {security}"
            )


def _set_0052_extra_trigger_alias(
    *,
    present: bool,
    relation_schema: str = "public",
) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    assert relation_schema in {"public", OPENING_TERMINAL_SHADOW_SCHEMA_0052}
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        ),
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            if present:
                if relation_schema != "public":
                    cursor.execute(
                        f"DROP SCHEMA IF EXISTS {relation_schema} CASCADE"
                    )
                    cursor.execute(f"CREATE SCHEMA {relation_schema}")
                    cursor.execute(
                        f"CREATE TABLE {relation_schema}.stocktake_tasks "
                        "(id uuid PRIMARY KEY)"
                    )
                cursor.execute(
                    f"CREATE CONSTRAINT TRIGGER "
                    f"{OPENING_TERMINAL_ALIAS_TRIGGER_0052} "
                    f"AFTER UPDATE ON {relation_schema}.stocktake_tasks "
                    "DEFERRABLE INITIALLY DEFERRED FOR EACH ROW "
                    f"EXECUTE FUNCTION {migration.COMMIT_SIGNATURE}"
                )
                cursor.execute(
                    f"ALTER TABLE {relation_schema}.stocktake_tasks "
                    "ENABLE ALWAYS TRIGGER "
                    f"{OPENING_TERMINAL_ALIAS_TRIGGER_0052}"
                )
            elif relation_schema != "public":
                cursor.execute(
                    f"DROP SCHEMA IF EXISTS {relation_schema} CASCADE"
                )
            else:
                cursor.execute(
                    f"DROP TRIGGER IF EXISTS "
                    f"{OPENING_TERMINAL_ALIAS_TRIGGER_0052} "
                    f"ON {relation_schema}.stocktake_tasks"
                )


def _0052_extra_trigger_alias_exists(
    *,
    relation_schema: str = "public",
) -> bool:
    assert relation_schema in {"public", OPENING_TERMINAL_SHADOW_SCHEMA_0052}
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_catalog.count(*) = 1 "
                "FROM pg_catalog.pg_trigger AS trigger_row "
                "JOIN pg_catalog.pg_class AS relation "
                "ON relation.oid = trigger_row.tgrelid "
                "JOIN pg_catalog.pg_namespace AS schema_row "
                "ON schema_row.oid = relation.relnamespace "
                "WHERE NOT trigger_row.tgisinternal "
                "AND schema_row.nspname = %s "
                "AND relation.relname = 'stocktake_tasks' "
                "AND trigger_row.tgname = %s",
                (relation_schema, OPENING_TERMINAL_ALIAS_TRIGGER_0052),
            )
            return cursor.fetchone() == (True,)


def _set_0052_head_caller_alias(
    *,
    signature: str,
    present: bool,
) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    allowed_signatures = {
        migration.INSERT_GUARD_SIGNATURE,
        migration.COUNT_WRITE_SIGNATURE,
        migration.GRAPH_CLOSURE_SIGNATURE,
    }
    assert signature in allowed_signatures
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        ),
        autocommit=True,
    ) as connection:
        with connection.cursor() as cursor:
            if present:
                cursor.execute(
                    f"DROP SCHEMA IF EXISTS "
                    f"{OPENING_TERMINAL_SHADOW_SCHEMA_0052} CASCADE"
                )
                cursor.execute(
                    f"CREATE SCHEMA {OPENING_TERMINAL_SHADOW_SCHEMA_0052}"
                )
                cursor.execute(
                    f"CREATE TABLE {OPENING_TERMINAL_SHADOW_SCHEMA_0052}."
                    "stocktake_tasks (id uuid PRIMARY KEY)"
                )
                cursor.execute(
                    f"CREATE TRIGGER {OPENING_HEAD_CALLER_ALIAS_TRIGGER_0052} "
                    f"BEFORE INSERT ON {OPENING_TERMINAL_SHADOW_SCHEMA_0052}."
                    "stocktake_tasks FOR EACH ROW EXECUTE FUNCTION "
                    f"{signature}"
                )
                cursor.execute(
                    f"ALTER TABLE {OPENING_TERMINAL_SHADOW_SCHEMA_0052}."
                    "stocktake_tasks ENABLE ALWAYS TRIGGER "
                    f"{OPENING_HEAD_CALLER_ALIAS_TRIGGER_0052}"
                )
            else:
                cursor.execute(
                    f"DROP SCHEMA IF EXISTS "
                    f"{OPENING_TERMINAL_SHADOW_SCHEMA_0052} CASCADE"
                )


def _assert_0052_startup_rejects_catalog_drift(api_engine) -> None:
    from app.database_security import DatabaseSecurityBoundaryError

    _set_0052_commit_security(security_definer=False)
    try:
        with pytest.raises(DatabaseSecurityBoundaryError):
            _validate_runtime_security(api_engine)
    finally:
        _set_0052_commit_security(security_definer=True)

    _set_0052_extra_trigger_alias(present=True)
    assert _0052_extra_trigger_alias_exists() is True
    try:
        with pytest.raises(DatabaseSecurityBoundaryError):
            _validate_runtime_security(api_engine)
    finally:
        _set_0052_extra_trigger_alias(present=False)
    assert _0052_extra_trigger_alias_exists() is False

    _set_0052_extra_trigger_alias(
        present=True,
        relation_schema=OPENING_TERMINAL_SHADOW_SCHEMA_0052,
    )
    assert _0052_extra_trigger_alias_exists(
        relation_schema=OPENING_TERMINAL_SHADOW_SCHEMA_0052,
    ) is True
    try:
        with pytest.raises(DatabaseSecurityBoundaryError):
            _validate_runtime_security(api_engine)
    finally:
        _set_0052_extra_trigger_alias(
            present=False,
            relation_schema=OPENING_TERMINAL_SHADOW_SCHEMA_0052,
        )
    assert _0052_extra_trigger_alias_exists(
        relation_schema=OPENING_TERMINAL_SHADOW_SCHEMA_0052,
    ) is False

    migration = _load_opening_terminal_guard_execution_migration_0052()
    for signature in (
        migration.INSERT_GUARD_SIGNATURE,
        migration.COUNT_WRITE_SIGNATURE,
        migration.GRAPH_CLOSURE_SIGNATURE,
    ):
        _set_0052_head_caller_alias(signature=signature, present=True)
        try:
            with pytest.raises(DatabaseSecurityBoundaryError):
                _validate_runtime_security(api_engine)
        finally:
            _set_0052_head_caller_alias(signature=signature, present=False)
    _validate_runtime_security(api_engine)


def _seed_0051_observation_only_completion(
    database_name: str,
    *,
    mutate_policy_after_completion: bool,
) -> dict[str, object]:
    from app.formal_access import load_formal_principal
    from app.formal_services.opening_stocktake import (
        INVENTORY_LEDGER_HEAD_ID,
        OpeningStocktakeScopeInput,
        StartOpeningStocktakeCommand,
        start_opening_stocktake,
    )
    from app.formal_services.opening_stocktake_count import (
        OpeningPhysicalObservationInput,
        SubmitOpeningStocktakeScopeCountCommand,
        submit_opening_stocktake_scope_count,
    )
    from app.foundation_models import (
        AuditChainHead,
        Permission,
        Role,
        SourceSystem,
    )
    from app.inventory_models import (
        InventoryLedgerHead,
        MaterialInventoryPolicy,
        StockAccount,
        StockBalance,
        StockLocation,
    )
    from app.stocktake_models import (
        FormalStocktakeScope,
        StocktakeCountLine,
        StocktakeCountObservation,
        StocktakeScopeCountCompletion,
    )
    import test_opening_stocktake_service as opening_fixtures

    _run_alembic(
        "upgrade",
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION,
        database_name=database_name,
    )
    assert _isolated_current_revision(database_name) == (
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION
    )

    migrator_parameters = _isolated_connection_parameters(
        database_name=database_name,
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    with psycopg.connect(**migrator_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE public.stocktake_scope_count_completions "
                "ADD COLUMN request_jsonb jsonb, "
                "ADD COLUMN request_resolution_jsonb jsonb"
            )

    engine = create_engine(
        _sqlalchemy_url(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
            database_name=database_name,
        ),
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    original_fixture_now = opening_fixtures.NOW
    try:
        with Session(engine, expire_on_commit=False) as session:
            fixture_now = session.scalar(select(func.now()))
            assert (
                isinstance(fixture_now, datetime)
                and fixture_now.tzinfo is not None
            )
            opening_fixtures.NOW = fixture_now

            roles = {
                role.code: role
                for role in session.scalars(
                    select(Role).where(
                        Role.code.in_(("admin", "provincial_manager"))
                    )
                )
            }
            assert set(roles) == {"admin", "provincial_manager"}
            assert {
                (permission.resource, permission.action)
                for permission in session.scalars(
                    select(Permission).where(
                        Permission.resource == "stocktake",
                        Permission.action.in_(("manage", "count")),
                        Permission.field_code == "",
                    )
                )
            } == {("stocktake", "manage"), ("stocktake", "count")}

            headquarters = opening_fixtures._organization(
                session,
                f"PG16-HQ-{uuid.uuid4().hex[:8]}",
                "PG16 0051 回填总部",
                "headquarters",
            )
            region = opening_fixtures._organization(
                session,
                f"PG16-REG-{uuid.uuid4().hex[:8]}",
                "PG16 0051 回填区域",
                "region_company",
                parent=headquarters,
            )
            source = session.scalar(
                select(SourceSystem)
                .where(func.lower(SourceSystem.code) == "oam")
                .order_by(SourceSystem.id)
                .limit(1)
            )
            if source is None:
                source = SourceSystem(
                    id=uuid.uuid4(),
                    code="OAM",
                    name="PG16 0051 回填只读控制源",
                    mode="read_only",
                    enabled=True,
                    configuration_jsonb={},
                    created_at=fixture_now - timedelta(days=2),
                    updated_at=fixture_now - timedelta(days=2),
                )
                session.add(source)
                session.flush()
            assert (source.mode, source.enabled) == ("read_only", True)
            manager = opening_fixtures._user_with_role(
                session,
                region,
                roles["provincial_manager"],
                "organization",
                str(region.id),
                "PG16 0051 回填区域负责人",
            )
            material = opening_fixtures._material(session, source, "serial")
            location = StockLocation(
                id=uuid.uuid4(),
                code=f"PG16-0051-WH-{uuid.uuid4().hex[:10]}",
                name="PG16 0051 回填区域仓",
                location_type="region",
                owner_org_id=region.id,
                parent_id=None,
                custodian_person_id=None,
                status="active",
                created_at=fixture_now - timedelta(days=1),
                updated_at=fixture_now - timedelta(days=1),
            )
            session.add(location)
            session.flush()
            account = StockAccount(
                id=uuid.uuid4(),
                owner_org_id=region.id,
                custodian_person_id=None,
                location_id=location.id,
                material_id=material.id,
                condition_code="new",
                availability_bucket="available",
                lot_id=None,
                created_at=fixture_now - timedelta(days=1),
                updated_at=fixture_now - timedelta(days=1),
            )
            session.add(account)
            session.flush()
            session.add(
                StockBalance(
                    stock_account_id=account.id,
                    quantity=Decimal("0"),
                    ledger_cursor=0,
                    version=1,
                    updated_at=fixture_now - timedelta(days=1),
                )
            )
            assert session.get(
                InventoryLedgerHead,
                INVENTORY_LEDGER_HEAD_ID,
            ) is not None
            assert session.scalar(
                select(AuditChainHead).where(
                    AuditChainHead.stream_key == "inventory"
                )
            ) is not None

            control = opening_fixtures._install_control_sync(
                session,
                source=source,
                region=region,
                material=material,
                rows=(
                    {
                        "external_business_key": (
                            f"PG16-0051-CONTROL-{uuid.uuid4().hex}"
                        ),
                        "material_id": material.id,
                        "condition_code": "new",
                        "control_qty": Decimal("0"),
                        "mapping_status": "resolved",
                        "mapping_note": "",
                    },
                ),
            )

            policy = session.scalar(
                select(MaterialInventoryPolicy).where(
                    MaterialInventoryPolicy.material_id == material.id
                )
            )
            assert policy is not None
            session.commit()

            started = start_opening_stocktake(
                session,
                actor=load_formal_principal(
                    session,
                    manager.user.id,
                    now=session.scalar(select(func.now())),
                ),
                command=StartOpeningStocktakeCommand(
                    task_no=f"PG16-0051-{uuid.uuid4().hex[:16]}",
                    region_org_id=region.id,
                    control_source_system_id=source.id,
                    control_sync_run_id=control.sync_run.id,
                    control_sync_scope_key=control.sync_run.scope_key,
                    scopes=(
                        OpeningStocktakeScopeInput(
                            owner_org_id=region.id,
                            location_id=location.id,
                            assignee_user_id=manager.user.id,
                            freeze_mode="hard",
                        ),
                    ),
                    control_lines=control.lines,
                    blind_count=True,
                    deadline=fixture_now + timedelta(days=2),
                    note="PG16 0051 observation-only migration fixture",
                ),
                idempotency_key=(
                    f"pg16-0052-legacy-start-{uuid.uuid4().hex}"
                ),
                request_id=f"pg16-0052-legacy-start-{uuid.uuid4().hex}",
            )
            session.commit()
            scope_id = session.scalar(
                select(FormalStocktakeScope.id).where(
                    FormalStocktakeScope.task_id == started.task_id
                )
            )
            assert isinstance(scope_id, uuid.UUID)

            missing_serial = (
                "PG16-0052-LEGACY-MISSING-SERIAL-"
                f"{uuid.uuid4().hex[:12].upper()}"
            )
            # 0051 is the broken predecessor under test: its 0022 task caller
            # rejects every legitimate counting -> submitted timestamp write.
            # Disable only that exact legacy UPDATE trigger while producing the
            # otherwise fully guarded historical graph, then restore ALWAYS
            # before presenting the database to the real 0052 migration.
            with psycopg.connect(
                **migrator_parameters,
                autocommit=True,
            ) as guard_connection:
                with guard_connection.cursor() as guard_cursor:
                    guard_cursor.execute(
                        "ALTER TABLE public.stocktake_tasks DISABLE TRIGGER "
                        "trg_stocktake_tasks_opening_commit_0022"
                    )
            try:
                count_result = submit_opening_stocktake_scope_count(
                    session,
                    actor=load_formal_principal(
                        session,
                        manager.user.id,
                        now=session.scalar(select(func.now())),
                    ),
                    command=SubmitOpeningStocktakeScopeCountCommand(
                        task_id=started.task_id,
                        round_id=started.initial_round_id,
                        scope_id=scope_id,
                        physical_observations=(
                            OpeningPhysicalObservationInput(
                                material_id=material.id,
                                material_identifier_raw=material.sku_code,
                                material_identifier_type="sku_code",
                                condition_code="new",
                                availability_bucket="available",
                                serial_no_raw=missing_serial,
                                serial_identifier_type="serial_no",
                                counted_qty=Decimal("1"),
                                count_method="manual",
                                remark="0051 legacy observation-only backfill",
                            ),
                        ),
                        zero_confirmed=False,
                    ),
                    idempotency_key=(
                        f"pg16-0052-legacy-count-{uuid.uuid4().hex}"
                    ),
                    request_id=f"pg16-0052-legacy-count-{uuid.uuid4().hex}",
                )
                assert count_result.round_sealed is True
                assert count_result.has_pending_verification is True
                session.commit()
            finally:
                session.rollback()
                with psycopg.connect(
                    **migrator_parameters,
                    autocommit=True,
                ) as guard_connection:
                    with guard_connection.cursor() as guard_cursor:
                        guard_cursor.execute(
                            "ALTER TABLE public.stocktake_tasks ENABLE ALWAYS "
                            "TRIGGER trg_stocktake_tasks_opening_commit_0022"
                        )

            completion = session.scalar(select(StocktakeScopeCountCompletion))
            observation = session.scalar(select(StocktakeCountObservation))
            assert completion is not None
            assert observation is not None
            assert observation.verification_status == "pending_verification"
            assert observation.material_id == material.id
            assert observation.serial_id is None
            implicit_zero_lines = tuple(
                session.scalars(select(StocktakeCountLine)).all()
            )
            assert len(implicit_zero_lines) == 1
            assert implicit_zero_lines[0].counted_qty == Decimal("0")
            original_request = completion.request_jsonb
            original_resolution = completion.request_resolution_jsonb
            assert original_request is not None
            assert original_resolution is not None
            assert original_resolution["items"][0]["target_type"] == (
                "observation"
            )
            assert original_resolution["items"][0]["serial_alias_keys"] == [
                missing_serial.lower()
            ]

            if mutate_policy_after_completion:
                session.execute(
                    text(
                        "UPDATE public.material_inventory_policies "
                        "SET updated_at = :updated_at WHERE id = :policy_id"
                    ),
                    {
                        "updated_at": completion.completed_at
                        + timedelta(seconds=1),
                        "policy_id": policy.id,
                    },
                )
                session.commit()
                assert session.scalar(
                    select(MaterialInventoryPolicy.updated_at).where(
                        MaterialInventoryPolicy.id == policy.id
                    )
                ) > completion.completed_at

            evidence = {
                "completion_id": completion.id,
                "expected_request_jsonb": original_request,
                "expected_request_resolution_jsonb": original_resolution,
                "observation_id": observation.id,
                "policy_id": policy.id,
                "request_sha256": completion.request_sha256,
                "serial_alias_key": missing_serial.lower(),
                "task_id": started.task_id,
            }
    finally:
        opening_fixtures.NOW = original_fixture_now
        engine.dispose()

    with psycopg.connect(**migrator_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "ALTER TABLE public.stocktake_scope_count_completions "
                "DROP COLUMN request_resolution_jsonb, "
                "DROP COLUMN request_jsonb"
            )
            cursor.execute(
                "SELECT pg_catalog.count(*) "
                "FROM public.stocktake_scope_count_completions "
                "WHERE id = %s",
                (evidence["completion_id"],),
            )
            assert cursor.fetchone() == (1,)
    return evidence


def _assert_0052_isolated_legacy_catalog_state(
    database_name: str,
    *,
    installed: bool,
) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    parameters = _isolated_connection_parameters(
        database_name=database_name,
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    with psycopg.connect(**parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT attribute.attname "
                "FROM pg_catalog.pg_attribute AS attribute "
                "WHERE attribute.attrelid = "
                "'public.stocktake_scope_count_completions'::regclass "
                "AND attribute.attnum > 0 AND NOT attribute.attisdropped "
                "AND attribute.attname = ANY(%s) ORDER BY attribute.attname",
                (
                    [
                        migration.COUNT_REQUEST_COLUMN,
                        migration.COUNT_REQUEST_RESOLUTION_COLUMN,
                    ],
                ),
            )
            columns = [row[0] for row in cursor.fetchall()]
            function_presence = []
            for signature in migration.HEAD_ONLY_FUNCTION_SIGNATURES:
                cursor.execute(
                    "SELECT pg_catalog.to_regprocedure(%s) IS NOT NULL",
                    (signature,),
                )
                function_presence.append(cursor.fetchone()[0])
            persistent_function_state = {}
            for signature in (
                migration.REVIEW_COMPLETION_SIGNATURE,
                migration.COMMIT_SIGNATURE,
                migration.ACCOUNT_SIGNATURE,
            ):
                cursor.execute(
                    "SELECT function_row.prosecdef, function_row.proconfig, "
                    "pg_catalog.encode(pg_catalog.sha256("
                    "pg_catalog.convert_to(function_row.prosrc, 'UTF8')), "
                    "'hex') FROM pg_catalog.pg_proc AS function_row "
                    "WHERE function_row.oid = pg_catalog.to_regprocedure(%s)",
                    (signature,),
                )
                persistent_function_state[signature] = cursor.fetchone()
            trigger_names = [
                row[1] for row in migration.OPENING_0052_TRIGGER_CATALOG
            ]
            cursor.execute(
                "SELECT trigger_row.tgname "
                "FROM pg_catalog.pg_trigger AS trigger_row "
                "WHERE NOT trigger_row.tgisinternal "
                "AND trigger_row.tgname = ANY(%s) "
                "ORDER BY trigger_row.tgname",
                (trigger_names,),
            )
            installed_trigger_names = [row[0] for row in cursor.fetchall()]

    expected_columns = sorted(
        [
            migration.COUNT_REQUEST_COLUMN,
            migration.COUNT_REQUEST_RESOLUTION_COLUMN,
        ]
    )
    assert columns == (expected_columns if installed else [])
    assert function_presence == (
        [True] * len(migration.HEAD_ONLY_FUNCTION_SIGNATURES)
        if installed
        else [False] * len(migration.HEAD_ONLY_FUNCTION_SIGNATURES)
    )
    assert persistent_function_state == {
        migration.REVIEW_COMPLETION_SIGNATURE: (
            False,
            [migration.FIXED_SEARCH_PATH] if installed else None,
            migration.REVIEW_COMPLETION_BODY_SHA256,
        ),
        migration.COMMIT_SIGNATURE: (
            installed,
            [migration.FIXED_SEARCH_PATH],
            (
                migration.FIXED_COMMIT_BODY_SHA256
                if installed
                else migration.LEGACY_COMMIT_BODY_SHA256
            ),
        ),
        migration.ACCOUNT_SIGNATURE: (
            installed,
            [migration.FIXED_SEARCH_PATH],
            (
                migration.FIXED_ACCOUNT_BODY_SHA256
                if installed
                else migration.LEGACY_ACCOUNT_BODY_SHA256
            ),
        ),
    }
    assert installed_trigger_names == (
        sorted(trigger_names) if installed else []
    )


def _assert_0052_legacy_backfill_and_atomic_rejection() -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()

    successful_database = _create_opening_backfill_database()
    try:
        success = _seed_0051_observation_only_completion(
            successful_database,
            mutate_policy_after_completion=False,
        )
        _assert_0052_isolated_legacy_catalog_state(
            successful_database,
            installed=False,
        )
        _run_alembic(
            "upgrade",
            HEAD_REVISION,
            database_name=successful_database,
        )
        assert _isolated_current_revision(successful_database) == HEAD_REVISION
        _assert_0052_isolated_legacy_catalog_state(
            successful_database,
            installed=True,
        )
        parameters = _isolated_connection_parameters(
            database_name=successful_database,
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
        with psycopg.connect(**parameters) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT request_jsonb, request_resolution_jsonb "
                    "FROM public.stocktake_scope_count_completions "
                    "WHERE id = %s",
                    (success["completion_id"],),
                )
                request_document, resolution_document = cursor.fetchone()
        assert request_document == success["expected_request_jsonb"]
        assert resolution_document == success[
            "expected_request_resolution_jsonb"
        ]
        assert request_document["schema"] == (
            "cloud_oam.opening_stocktake.scope_count_request.v1"
        )
        assert request_document["physical_observations"][0][
            "serial_no_raw"
        ].lower() == success["serial_alias_key"]
        assert resolution_document["request_sha256"] == success[
            "request_sha256"
        ]
        assert len(resolution_document["items"]) == 1
        resolution_item = resolution_document["items"][0]
        assert resolution_item["request_ordinal"] == 1
        assert resolution_item["serial_alias_keys"] == [
            success["serial_alias_key"]
        ]
        assert resolution_item["target_id"] == str(success["observation_id"])
        assert resolution_item["target_type"] == "observation"
    finally:
        _drop_opening_backfill_database(successful_database)

    rejected_database = _create_opening_backfill_database()
    try:
        rejected = _seed_0051_observation_only_completion(
            rejected_database,
            mutate_policy_after_completion=True,
        )
        _assert_0052_isolated_legacy_catalog_state(
            rejected_database,
            installed=False,
        )
        blocked = _run_alembic(
            "upgrade",
            HEAD_REVISION,
            expect_success=False,
            database_name=rejected_database,
        )
        output = blocked.stdout + blocked.stderr
        assert (
            migration.REQUEST_EVIDENCE_ERROR in output
            or migration.EXISTING_ROWS_ERROR in output
        )
        assert _isolated_current_revision(rejected_database) == (
            STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION
        )
        _assert_0052_isolated_legacy_catalog_state(
            rejected_database,
            installed=False,
        )
        parameters = _isolated_connection_parameters(
            database_name=rejected_database,
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
        with psycopg.connect(**parameters) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT completion.request_sha256, "
                    "observation.id IS NOT NULL, "
                    "policy.updated_at > completion.completed_at "
                    "FROM public.stocktake_scope_count_completions "
                    "AS completion "
                    "LEFT JOIN public.stocktake_count_observations "
                    "AS observation ON observation.id = %s "
                    "JOIN public.material_inventory_policies AS policy "
                    "ON policy.id = %s WHERE completion.id = %s",
                    (
                        rejected["observation_id"],
                        rejected["policy_id"],
                        rejected["completion_id"],
                    ),
                )
                assert cursor.fetchone() == (
                    rejected["request_sha256"],
                    True,
                    True,
                )
    finally:
        _drop_opening_backfill_database(rejected_database)


def _assert_0052_empty_graph_downgrade_and_reupgrade() -> None:
    assert _current_revision() == HEAD_REVISION
    migration = _load_opening_terminal_guard_execution_migration_0052()
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT pg_catalog.count(*) FROM public.stocktake_tasks "
                "WHERE task_type = 'opening'"
            )
            assert cursor.fetchone() == (0,)

    _assert_0052_opening_terminal_catalog(
        hardened=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0052_api_direct_execute_denied()

    _run_alembic(
        "downgrade",
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION,
    )
    assert _current_revision() == STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION
    _assert_0052_opening_terminal_catalog(
        hardened=False,
        expected_revision=STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION,
    )
    _assert_0052_api_direct_execute_denied(hardened=False)

    _set_0052_commit_security(security_definer=True)
    try:
        blocked = _run_alembic("upgrade", "head", expect_success=False)
        output = blocked.stdout + blocked.stderr
        assert migration.CATALOG_ERROR in output
        assert "function definition mismatch" in output
        assert _current_revision() == (
            STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION
        )
    finally:
        _set_0052_commit_security(security_definer=False)

    _set_0052_extra_trigger_alias(present=True)
    assert _0052_extra_trigger_alias_exists() is True
    try:
        blocked = _run_alembic("upgrade", "head", expect_success=False)
        output = blocked.stdout + blocked.stderr
        assert migration.CATALOG_ERROR in output
        assert "trigger binding mismatch" in output
        assert _current_revision() == (
            STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION
        )
    finally:
        _set_0052_extra_trigger_alias(present=False)
    assert _0052_extra_trigger_alias_exists() is False

    _set_0052_extra_trigger_alias(
        present=True,
        relation_schema=OPENING_TERMINAL_SHADOW_SCHEMA_0052,
    )
    assert _0052_extra_trigger_alias_exists(
        relation_schema=OPENING_TERMINAL_SHADOW_SCHEMA_0052,
    ) is True
    try:
        blocked = _run_alembic("upgrade", "head", expect_success=False)
        output = blocked.stdout + blocked.stderr
        assert migration.CATALOG_ERROR in output
        assert "trigger binding mismatch" in output
        assert _current_revision() == (
            STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION
        )
    finally:
        _set_0052_extra_trigger_alias(
            present=False,
            relation_schema=OPENING_TERMINAL_SHADOW_SCHEMA_0052,
        )
    assert _0052_extra_trigger_alias_exists(
        relation_schema=OPENING_TERMINAL_SHADOW_SCHEMA_0052,
    ) is False

    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    _assert_0052_opening_terminal_catalog(
        hardened=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0052_api_direct_execute_denied()


def _assert_0052_opening_history_downgrade_rejected(
    *,
    task_id: uuid.UUID,
) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, current_round_no, submitted_at, version "
                "FROM public.stocktake_tasks WHERE id = %s",
                (task_id,),
            )
            before = cursor.fetchone()
    assert before is not None

    blocked = _run_alembic(
        "downgrade",
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION,
        expect_success=False,
    )
    assert migration.DOWNGRADE_BLOCKER in (blocked.stdout + blocked.stderr)
    assert _current_revision() == HEAD_REVISION

    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT status, current_round_no, submitted_at, version "
                "FROM public.stocktake_tasks WHERE id = %s",
                (task_id,),
            )
            assert cursor.fetchone() == before
    _assert_0052_opening_terminal_catalog(
        hardened=True,
        expected_revision=HEAD_REVISION,
    )


def _assert_0052_api_orphan_stock_account_rejected(
    api_engine,
    *,
    fixture: dict[str, object],
) -> None:
    from app.inventory_models import StockAccount

    orphan_id = uuid.uuid4()
    with Session(api_engine) as session:
        now = session.scalar(select(func.now()))
        assert isinstance(now, datetime) and now.tzinfo is not None
        session.add(
            StockAccount(
                id=orphan_id,
                owner_org_id=fixture["region_org_id"],
                custodian_person_id=None,
                location_id=fixture["location_id"],
                material_id=fixture["material_id"],
                condition_code="used",
                availability_bucket="available",
                lot_id=None,
                created_at=now,
                updated_at=now,
            )
        )
        session.flush()
        with pytest.raises(DBAPIError) as failure:
            session.commit()
        assert getattr(failure.value.orig, "sqlstate", None) == "23514"
        assert (
            "API stock account insert must terminate a verified opening "
            "recount observation graph"
            in str(failure.value.orig)
        )
        session.rollback()

    with Session(api_engine) as session:
        assert session.get(StockAccount, orphan_id) is None


def _assert_0052_raw_opening_task_insert_rejected(
    api_engine,
    *,
    task_id: uuid.UUID,
) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    forged_task_id = uuid.uuid4()
    suffix = f"-RAW-{forged_task_id.hex[:8].upper()}"
    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.stocktake_tasks ("
                "id, task_no, task_type, region_org_id, status, blind_count, "
                "cutoff_ledger_cursor, cutoff_at, scope_manifest_sha256, "
                "snapshot_manifest_sha256, control_source_system_id, "
                "control_sync_run_id, control_snapshot_at, "
                "control_manifest_sha256, current_round_no, "
                "created_by_user_id, deadline, issued_at, frozen_at, "
                "submitted_at, posted_at, closed_at, cancelled_at, version, "
                "note, created_at, updated_at) "
                "SELECT %s, task_no || %s, task_type, region_org_id, "
                "'cancelled', blind_count, cutoff_ledger_cursor, cutoff_at, "
                "scope_manifest_sha256, snapshot_manifest_sha256, "
                "control_source_system_id, control_sync_run_id, "
                "control_snapshot_at, control_manifest_sha256, "
                "current_round_no, created_by_user_id, deadline, issued_at, "
                "frozen_at, NULL, NULL, NULL, pg_catalog.clock_timestamp(), "
                "version, note, created_at, pg_catalog.clock_timestamp() "
                "FROM public.stocktake_tasks WHERE id = %s",
                (forged_task_id, suffix, task_id),
            )
            assert cursor.rowcount == 1
            with pytest.raises(psycopg.Error) as failure:
                # The inherited 0047 deferred guard also rejects this forged
                # row at COMMIT, but reports its own error contract.  Force
                # the exact 0052 constraint so this assertion proves the new
                # opening-insert guard rather than whichever deferred trigger
                # PostgreSQL happens to evaluate first.
                cursor.execute(
                    sql.SQL("SET CONSTRAINTS {} IMMEDIATE").format(
                        sql.Identifier(migration.INSERT_GUARD_TRIGGER)
                    )
                )
            assert failure.value.sqlstate == "23514"
            assert migration.OPENING_INSERT_ERROR in str(failure.value)
        connection.rollback()

    with Session(api_engine) as session:
        assert session.execute(
            text(
                "SELECT pg_catalog.count(*) FROM public.stocktake_tasks "
                "WHERE id = :task_id"
            ),
            {"task_id": forged_task_id},
        ).scalar_one() == 0


def _assert_0052_cross_domain_posting_rejected(
    api_engine,
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    posted_by_user_id: str,
) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    posting_id = uuid.uuid4()
    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.stocktake_postings ("
                "id, task_id, round_id, posting_kind, "
                "effective_approval_completion_id, inventory_transaction_id, "
                "total_quantity, idempotency_key_hash, request_hash, "
                "posted_by_user_id, posted_at, created_at) "
                "VALUES (%s, %s, %s, 'difference_adjustment', NULL, NULL, "
                "0, %s, %s, %s, pg_catalog.transaction_timestamp(), "
                "pg_catalog.transaction_timestamp())",
                (
                    posting_id,
                    task_id,
                    round_id,
                    hashlib.sha256(f"posting-key:{posting_id}".encode()).hexdigest(),
                    hashlib.sha256(
                        f"posting-request:{posting_id}".encode()
                    ).hexdigest(),
                    posted_by_user_id,
                ),
            )
            assert cursor.rowcount == 1
            with pytest.raises(psycopg.Error) as failure:
                # The inherited evidence guard also rejects a posting for a
                # round that is still counting.  Force the exact 0052 graph
                # constraint so this regression proves the cross-domain
                # posting boundary independently of deferred-trigger order.
                cursor.execute(
                    "SET CONSTRAINTS "
                    "trg_stocktake_postings_graph_0052 IMMEDIATE"
                )
            assert failure.value.sqlstate == "23514"
            assert migration.GRAPH_CLOSURE_ERROR in str(failure.value)
        connection.rollback()

    with Session(api_engine) as session:
        assert session.execute(
            text(
                "SELECT pg_catalog.count(*) FROM public.stocktake_postings "
                "WHERE id = :posting_id"
            ),
            {"posting_id": posting_id},
        ).scalar_one() == 0


def _assert_0052_standalone_count_line_rejected(
    api_engine,
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    scope_id: uuid.UUID,
    stock_account_id: uuid.UUID,
    counted_by_user_id: str,
) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    count_line_id = uuid.uuid4()
    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.stocktake_count_lines ("
                "id, task_id, round_id, scope_id, stock_account_id, "
                "counted_qty, count_method, reason_code, remark, "
                "counted_by_user_id, counted_at, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, 5, 'manual', NULL, %s, %s, "
                "pg_catalog.transaction_timestamp(), "
                "pg_catalog.transaction_timestamp(), "
                "pg_catalog.transaction_timestamp())",
                (
                    count_line_id,
                    task_id,
                    round_id,
                    scope_id,
                    stock_account_id,
                    "forged standalone count line",
                    counted_by_user_id,
                ),
            )
            assert cursor.rowcount == 1
            with pytest.raises(psycopg.Error) as failure:
                cursor.execute(
                    "SET CONSTRAINTS "
                    "trg_stocktake_count_lines_graph_0052 IMMEDIATE"
                )
            assert failure.value.sqlstate == "23514"
            assert migration.GRAPH_CLOSURE_ERROR in str(failure.value)
        connection.rollback()

    with Session(api_engine) as session:
        assert session.execute(
            text(
                "SELECT pg_catalog.count(*) FROM "
                "public.stocktake_count_lines WHERE id = :line_id"
            ),
            {"line_id": count_line_id},
        ).scalar_one() == 0


def _assert_0052_standalone_count_serial_rejected(
    api_engine,
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    scope_id: uuid.UUID,
    stock_account_id: uuid.UUID,
    serial_id: uuid.UUID,
    counted_by_user_id: str,
) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    count_line_id = uuid.uuid4()
    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.stocktake_count_lines ("
                "id, task_id, round_id, scope_id, stock_account_id, "
                "counted_qty, count_method, reason_code, remark, "
                "counted_by_user_id, counted_at, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, 1, 'manual', NULL, %s, %s, "
                "pg_catalog.transaction_timestamp(), "
                "pg_catalog.transaction_timestamp(), "
                "pg_catalog.transaction_timestamp())",
                (
                    count_line_id,
                    task_id,
                    round_id,
                    scope_id,
                    stock_account_id,
                    "forged standalone count serial parent",
                    counted_by_user_id,
                ),
            )
            assert cursor.rowcount == 1
            cursor.execute(
                "INSERT INTO public.stocktake_count_serials ("
                "count_line_id, round_id, serial_id, result, created_at) "
                "VALUES (%s, %s, %s, 'present', "
                "pg_catalog.transaction_timestamp())",
                (count_line_id, round_id, serial_id),
            )
            assert cursor.rowcount == 1
            with pytest.raises(psycopg.Error) as failure:
                cursor.execute(
                    "SET CONSTRAINTS "
                    "trg_stocktake_count_serials_graph_0052 IMMEDIATE"
                )
            assert failure.value.sqlstate == "23514"
            assert migration.GRAPH_CLOSURE_ERROR in str(failure.value)
        connection.rollback()

    with Session(api_engine) as session:
        assert tuple(
            session.execute(
                text(
                    "SELECT (SELECT pg_catalog.count(*) FROM "
                    "public.stocktake_count_lines WHERE id = :line_id), "
                    "(SELECT pg_catalog.count(*) FROM "
                    "public.stocktake_count_serials "
                    "WHERE count_line_id = :line_id AND serial_id = :serial_id)"
                ),
                {"line_id": count_line_id, "serial_id": serial_id},
            ).one()
        ) == (0, 0)


def _assert_0052_standalone_count_observation_rejected(
    api_engine,
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    scope_id: uuid.UUID,
    counted_by_user_id: str,
) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    observation_id = uuid.uuid4()
    raw_identifier = f"PG16-FORGED-OBSERVATION-{observation_id.hex}"
    dimension_sha256 = hashlib.sha256(
        f"dimension:{observation_id}".encode()
    ).hexdigest()
    request_sha256 = hashlib.sha256(
        f"request:{observation_id}".encode()
    ).hexdigest()
    idempotency_key_hash = hashlib.sha256(
        f"idempotency:{observation_id}".encode()
    ).hexdigest()
    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.stocktake_count_observations ("
                "id, task_id, round_id, scope_id, observation_no, "
                "owner_org_id, location_id, custodian_person_id_snapshot, "
                "material_id, material_identifier_raw, "
                "material_identifier_type, condition_code, "
                "availability_bucket, lot_id, lot_no_raw, serial_id, "
                "serial_no_raw, serial_identifier_type, counted_qty, "
                "verification_status, count_method, reason_code, remark, "
                "counted_by_user_id, counted_at, dimension_sha256, "
                "request_sha256, idempotency_key_hash, created_at) "
                "SELECT %s, %s, %s, %s, 1, scope.owner_org_id, "
                "scope.location_id, scope.custodian_person_id_snapshot, "
                "NULL, %s, 'unknown', 'new', 'available', NULL, NULL, "
                "NULL, NULL, NULL, 1, 'pending_verification', 'manual', "
                "NULL, %s, %s, pg_catalog.transaction_timestamp(), %s, %s, "
                "%s, pg_catalog.transaction_timestamp() "
                "FROM public.stocktake_scopes AS scope "
                "WHERE scope.id = %s AND scope.task_id = %s",
                (
                    observation_id,
                    task_id,
                    round_id,
                    scope_id,
                    raw_identifier,
                    "forged standalone count observation",
                    counted_by_user_id,
                    dimension_sha256,
                    request_sha256,
                    idempotency_key_hash,
                    scope_id,
                    task_id,
                ),
            )
            assert cursor.rowcount == 1
            with pytest.raises(psycopg.Error) as failure:
                cursor.execute(
                    "SET CONSTRAINTS "
                    "trg_stocktake_count_observations_graph_0052 IMMEDIATE"
                )
            assert failure.value.sqlstate == "23514"
            assert migration.GRAPH_CLOSURE_ERROR in str(failure.value)
        connection.rollback()

    with Session(api_engine) as session:
        assert session.execute(
            text(
                "SELECT pg_catalog.count(*) FROM "
                "public.stocktake_count_observations "
                "WHERE id = :observation_id"
            ),
            {"observation_id": observation_id},
        ).scalar_one() == 0


def _assert_0052_standalone_scope_completion_rejected(
    api_engine,
    *,
    task_id: uuid.UUID,
    round_id: uuid.UUID,
    scope_id: uuid.UUID,
    stock_account_id: uuid.UUID,
    counted_by_user_id: str,
) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    count_line_id = uuid.uuid4()
    completion_id = uuid.uuid4()
    hashes = tuple(
        hashlib.sha256(f"{kind}:{completion_id}".encode()).hexdigest()
        for kind in ("evidence", "request", "idempotency", "authorization")
    )
    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.stocktake_count_lines ("
                "id, task_id, round_id, scope_id, stock_account_id, "
                "counted_qty, count_method, reason_code, remark, "
                "counted_by_user_id, counted_at, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, 5, 'manual', NULL, %s, %s, "
                "pg_catalog.transaction_timestamp(), "
                "pg_catalog.transaction_timestamp(), "
                "pg_catalog.transaction_timestamp())",
                (
                    count_line_id,
                    task_id,
                    round_id,
                    scope_id,
                    stock_account_id,
                    "forged scope completion parent",
                    counted_by_user_id,
                ),
            )
            assert cursor.rowcount == 1
            cursor.execute(
                "INSERT INTO public.stocktake_scope_count_completions ("
                "id, task_id, round_id, scope_id, count_line_count, "
                "observation_line_count, serial_count, total_counted_qty, "
                "zero_confirmed, evidence_manifest_sha256, request_sha256, "
                "idempotency_key_hash, completed_by_user_id, "
                "completed_by_person_id, completed_role_assignment_id, "
                "authorization_version, role_code, scope_type, "
                "scope_id_snapshot, authorization_sha256, "
                "count_ledger_cursor, completed_at, created_at) "
                "SELECT %s, %s, %s, %s, 1, 0, 0, 5, FALSE, %s, %s, %s, "
                "assignment.user_id, actor.person_id, assignment.id, "
                "actor.authorization_version, role.code, "
                "assignment.scope_type, assignment.scope_id, %s, NULL, "
                "pg_catalog.transaction_timestamp(), "
                "pg_catalog.transaction_timestamp() "
                "FROM public.role_assignments AS assignment "
                "JOIN public.roles AS role ON role.id = assignment.role_id "
                "JOIN public.users AS actor ON actor.id = assignment.user_id "
                "JOIN public.stocktake_scopes AS scope "
                "ON scope.id = %s AND scope.task_id = %s "
                "WHERE assignment.user_id = %s "
                "AND assignment.status = 'active' "
                "AND assignment.revoked_at IS NULL "
                "AND role.code = 'provincial_manager' "
                "AND role.status = 'active' AND NOT role.is_external "
                "AND assignment.scope_type = 'organization' "
                "AND assignment.scope_id = scope.owner_org_id::text "
                "ORDER BY assignment.id LIMIT 1",
                (
                    completion_id,
                    task_id,
                    round_id,
                    scope_id,
                    hashes[0],
                    hashes[1],
                    hashes[2],
                    hashes[3],
                    scope_id,
                    task_id,
                    counted_by_user_id,
                ),
            )
            assert cursor.rowcount == 1
            with pytest.raises(psycopg.Error) as failure:
                cursor.execute(
                    "SET CONSTRAINTS "
                    "trg_stocktake_scope_count_completions_graph_0052 IMMEDIATE"
                )
            assert failure.value.sqlstate == "23514"
            assert migration.GRAPH_CLOSURE_ERROR in str(failure.value)
        connection.rollback()

    with Session(api_engine) as session:
        assert tuple(
            session.execute(
                text(
                    "SELECT (SELECT pg_catalog.count(*) FROM "
                    "public.stocktake_count_lines WHERE id = :line_id), "
                    "(SELECT pg_catalog.count(*) FROM "
                    "public.stocktake_scope_count_completions "
                    "WHERE id = :completion_id)"
                ),
                {"line_id": count_line_id, "completion_id": completion_id},
            ).one()
        ) == (0, 0)


def _assert_0052_wrong_opening_audit_action_rejected(
    api_engine,
    *,
    review_id: uuid.UUID,
    actor_user_id: str,
) -> None:
    from app.formal_services.audit_chain import append_audit_event

    migration = _load_opening_terminal_guard_execution_migration_0052()
    request_id = f"pg16-opening-review-audit-forgery-{uuid.uuid4().hex}"
    with Session(api_engine) as session:
        occurred_at = session.scalar(select(func.now()))
        assert isinstance(occurred_at, datetime) and occurred_at.tzinfo is not None
        append_audit_event(
            session,
            stream_key="inventory",
            actor_user_id=actor_user_id,
            action="inventory.unrelated_event",
            aggregate_type="stocktake_review",
            aggregate_id=str(review_id),
            before_jsonb=None,
            after_jsonb={"forged": True},
            request_id=request_id,
            occurred_at=occurred_at,
        )
        with pytest.raises(DBAPIError) as failure:
            session.execute(
                text(
                    "SET CONSTRAINTS "
                    "trg_audit_events_opening_graph_0052 IMMEDIATE"
                )
            )
        assert getattr(failure.value.orig, "sqlstate", None) == "23514"
        assert migration.GRAPH_CLOSURE_ERROR in str(failure.value.orig)
        session.rollback()

    with Session(api_engine) as session:
        assert session.execute(
            text(
                "SELECT pg_catalog.count(*) FROM public.audit_events "
                "WHERE request_id = :request_id"
            ),
            {"request_id": request_id},
        ).scalar_one() == 0


def _assert_0052_review_state_and_outbox_rejected(
    api_engine,
    *,
    task_id: uuid.UUID,
    review_id: uuid.UUID,
    actor_user_id: str,
) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    state_event_id = uuid.uuid4()
    state_key = f"pg16-opening-review-state-forgery-{state_event_id.hex}"
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.state_transition_events ("
                "id, aggregate_type, aggregate_id, from_status, to_status, "
                "reason, actor_id, idempotency_key, occurred_at, "
                "metadata_jsonb, created_at) VALUES ("
                "%s, 'stocktake_task', %s, 'headquarters_review', "
                "'rejected', 'opening_headquarters_review_reject', %s, %s, "
                "pg_catalog.transaction_timestamp(), "
                "pg_catalog.jsonb_build_object('review_id', %s::text), "
                "pg_catalog.transaction_timestamp())",
                (
                    state_event_id,
                    str(task_id),
                    actor_user_id,
                    state_key,
                    str(review_id),
                ),
            )
            assert cursor.rowcount == 1
            with pytest.raises(psycopg.Error) as failure:
                connection.commit()
            assert failure.value.sqlstate == "23514"
            assert migration.GRAPH_CLOSURE_ERROR in str(failure.value)
        connection.rollback()

    outbox_event_id = uuid.uuid4()
    outbox_key = f"pg16-opening-review-outbox-forgery-{outbox_event_id.hex}"
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.outbox_events ("
                "id, event_type, aggregate_type, aggregate_id, payload_jsonb, "
                "status, attempts, idempotency_key, available_at, locked_at, "
                "locked_by, published_at, last_error, created_at, updated_at) "
                "VALUES (%s, 'stocktake.opening.headquarters_reviewed', "
                "'stocktake_task', %s, "
                "pg_catalog.jsonb_build_object('review_id', %s::text), "
                "'pending', 0, %s, pg_catalog.transaction_timestamp(), "
                "NULL, NULL, NULL, NULL, "
                "pg_catalog.transaction_timestamp(), "
                "pg_catalog.transaction_timestamp())",
                (
                    outbox_event_id,
                    str(task_id),
                    str(review_id),
                    outbox_key,
                ),
            )
            assert cursor.rowcount == 1
            with pytest.raises(psycopg.Error) as failure:
                connection.commit()
            assert failure.value.sqlstate == "23514"
            assert migration.GRAPH_CLOSURE_ERROR in str(failure.value)
        connection.rollback()

    with Session(api_engine) as session:
        assert tuple(session.execute(
            text(
                "SELECT (SELECT pg_catalog.count(*) FROM "
                "public.state_transition_events WHERE id = :state_id), "
                "(SELECT pg_catalog.count(*) FROM public.outbox_events "
                "WHERE id = :outbox_id)"
            ),
            {"state_id": state_event_id, "outbox_id": outbox_event_id},
        ).one()) == (0, 0)


def _assert_0052_forged_reconciliation_prefix_rejected(api_engine) -> None:
    migration = _load_opening_terminal_guard_execution_migration_0052()
    outbox_event_id = uuid.uuid4()
    aggregate_id = uuid.uuid4()
    forged_key = (
        "opening-reconciliation-"
        + hashlib.sha256(f"forged:{outbox_event_id}".encode()).hexdigest()
    )
    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.outbox_events ("
                "id, event_type, aggregate_type, aggregate_id, payload_jsonb, "
                "status, attempts, idempotency_key, available_at, locked_at, "
                "locked_by, published_at, last_error, created_at, updated_at) "
                "VALUES (%s, 'reconciliation.opening.forged', "
                "'reconciliation_run', %s, '{}'::jsonb, 'pending', 0, %s, "
                "pg_catalog.transaction_timestamp(), NULL, NULL, NULL, NULL, "
                "pg_catalog.transaction_timestamp(), "
                "pg_catalog.transaction_timestamp())",
                (outbox_event_id, str(aggregate_id), forged_key),
            )
            assert cursor.rowcount == 1
            with pytest.raises(psycopg.Error) as failure:
                connection.commit()
            assert failure.value.sqlstate == "23514"
            assert migration.GRAPH_CLOSURE_ERROR in str(failure.value)
        connection.rollback()

    with Session(api_engine) as session:
        assert session.execute(
            text(
                "SELECT pg_catalog.count(*) FROM public.outbox_events "
                "WHERE id = :outbox_event_id"
            ),
            {"outbox_event_id": outbox_event_id},
        ).scalar_one() == 0


def _assert_0052_premature_close_artifacts_rejected(
    api_engine,
    *,
    task_id: uuid.UUID,
    actor_user_id: str,
) -> None:
    from app.formal_services.audit_chain import append_audit_event

    migration = _load_opening_terminal_guard_execution_migration_0052()
    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )

    state_event_id = uuid.uuid4()
    state_key = f"pg16-premature-opening-close-state-{state_event_id.hex}"
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.state_transition_events ("
                "id, aggregate_type, aggregate_id, from_status, to_status, "
                "reason, actor_id, idempotency_key, occurred_at, "
                "metadata_jsonb, created_at) VALUES ("
                "%s, 'stocktake_task', %s, 'posted', 'closed', "
                "'opening_stocktake_closed', %s, %s, "
                "pg_catalog.transaction_timestamp(), '{}'::jsonb, "
                "pg_catalog.transaction_timestamp())",
                (state_event_id, str(task_id), actor_user_id, state_key),
            )
            assert cursor.rowcount == 1
            # Force the 0052 graph constraint itself.  The inherited 0022
            # terminal constraint is also deferred on these three tables and
            # sorts first by name at COMMIT, so a plain commit would prove only
            # the older guard and make this 0052 regression assertion ambiguous.
            with pytest.raises(psycopg.Error) as failure:
                cursor.execute(
                    "SET CONSTRAINTS "
                    "trg_state_transition_events_opening_graph_0052 IMMEDIATE"
                )
            assert failure.value.sqlstate == "23514"
            assert migration.GRAPH_CLOSURE_ERROR in str(failure.value)
        connection.rollback()

    outbox_event_id = uuid.uuid4()
    outbox_key = f"pg16-premature-opening-close-outbox-{outbox_event_id.hex}"
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "INSERT INTO public.outbox_events ("
                "id, event_type, aggregate_type, aggregate_id, payload_jsonb, "
                "status, attempts, idempotency_key, available_at, locked_at, "
                "locked_by, published_at, last_error, created_at, updated_at) "
                "VALUES (%s, 'stocktake.opening.closed', 'stocktake_task', %s, "
                "'{}'::jsonb, 'pending', 0, %s, "
                "pg_catalog.transaction_timestamp(), NULL, NULL, NULL, NULL, "
                "pg_catalog.transaction_timestamp(), "
                "pg_catalog.transaction_timestamp())",
                (outbox_event_id, str(task_id), outbox_key),
            )
            assert cursor.rowcount == 1
            with pytest.raises(psycopg.Error) as failure:
                cursor.execute(
                    "SET CONSTRAINTS "
                    "trg_outbox_events_opening_graph_0052 IMMEDIATE"
                )
            assert failure.value.sqlstate == "23514"
            assert migration.GRAPH_CLOSURE_ERROR in str(failure.value)
        connection.rollback()

    audit_request_id = f"pg16-premature-opening-close-{uuid.uuid4().hex}"
    with Session(api_engine) as session:
        occurred_at = session.scalar(select(func.now()))
        assert isinstance(occurred_at, datetime) and occurred_at.tzinfo is not None
        append_audit_event(
            session,
            stream_key="inventory",
            actor_user_id=actor_user_id,
            action="stocktake.opening.closed",
            aggregate_type="stocktake_task",
            aggregate_id=str(task_id),
            before_jsonb={"status": "posted"},
            after_jsonb={"status": "closed"},
            request_id=audit_request_id,
            occurred_at=occurred_at,
        )
        with pytest.raises(DBAPIError) as failure:
            session.execute(
                text(
                    "SET CONSTRAINTS "
                    "trg_audit_events_opening_graph_0052 IMMEDIATE"
                )
            )
        assert getattr(failure.value.orig, "sqlstate", None) == "23514"
        assert migration.GRAPH_CLOSURE_ERROR in str(failure.value.orig)
        session.rollback()

    with Session(api_engine) as session:
        assert tuple(
            session.execute(
                text(
                    "SELECT (SELECT pg_catalog.count(*) FROM "
                    "public.state_transition_events WHERE id = :state_id), "
                    "(SELECT pg_catalog.count(*) FROM public.outbox_events "
                    "WHERE id = :outbox_id), "
                    "(SELECT pg_catalog.count(*) FROM public.audit_events "
                    "WHERE request_id = :audit_request_id)"
                ),
                {
                    "state_id": state_event_id,
                    "outbox_id": outbox_event_id,
                    "audit_request_id": audit_request_id,
                },
            ).one()
        ) == (0, 0, 0)


def _assert_0052_raw_opening_task_transition_rejected(
    api_engine,
    *,
    task_id: uuid.UUID,
    new_status: str,
    round_increment: int = 0,
    expected_message: str,
) -> None:
    with Session(api_engine) as session:
        before = session.execute(
            text(
                "SELECT status, current_round_no, submitted_at, posted_at, "
                "closed_at, version, updated_at FROM stocktake_tasks "
                "WHERE id = :task_id"
            ),
            {"task_id": task_id},
        ).one()

        session.execute(
            text(
                "UPDATE stocktake_tasks SET status = :new_status, "
                "current_round_no = current_round_no + :round_increment, "
                "version = version + 1, updated_at = now() "
                "WHERE id = :task_id"
            ),
            {
                "new_status": new_status,
                "round_increment": round_increment,
                "task_id": task_id,
            },
        )
        with pytest.raises(DBAPIError) as failure:
            session.commit()
        assert getattr(failure.value.orig, "sqlstate", None) in {
            "23514",
            "55000",
        }
        assert expected_message in str(failure.value.orig)
        session.rollback()

    with Session(api_engine) as session:
        after = session.execute(
            text(
                "SELECT status, current_round_no, submitted_at, posted_at, "
                "closed_at, version, updated_at FROM stocktake_tasks "
                "WHERE id = :task_id"
            ),
            {"task_id": task_id},
        ).one()
    assert after == before


def _assert_0051_empty_graph_downgrade_and_reupgrade() -> None:
    assert _current_revision() == HEAD_REVISION
    migration = _load_stocktake_difference_authorization_hash_migration_0051()
    assert migration.revision == (
        STOCKTAKE_DIFFERENCE_AUTHORIZATION_HASH_REVISION
    )
    assert migration.down_revision == STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION

    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT pg_catalog.count(*) FROM public.{migration.TRIGGER_TABLE}"
            )
            assert cursor.fetchone() == (0,)

    _assert_0051_difference_completion_catalog(
        repaired=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0051_api_direct_execute_denied()

    _run_alembic("downgrade", STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION)
    assert _current_revision() == STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION
    _assert_0051_difference_completion_catalog(
        repaired=False,
        expected_revision=STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION,
    )
    _assert_0051_api_direct_execute_denied()

    _set_0051_primary_trigger_enabled(always=True)
    try:
        blocked = _run_alembic("upgrade", "head", expect_success=False)
        output = blocked.stdout + blocked.stderr
        assert migration.CATALOG_ERROR in output
        assert "trigger binding mismatch" in output
        assert _current_revision() == STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION
    finally:
        _set_0051_primary_trigger_enabled(always=False)
    _assert_0051_difference_completion_catalog(
        repaired=False,
        expected_revision=STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION,
    )

    _set_0051_extra_trigger_alias(present=True)
    assert _0051_extra_trigger_alias_exists() is True
    try:
        blocked = _run_alembic("upgrade", "head", expect_success=False)
        output = blocked.stdout + blocked.stderr
        assert migration.CATALOG_ERROR in output
        assert "trigger binding mismatch" in output
        assert _current_revision() == STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION
        assert _0051_extra_trigger_alias_exists() is True
    finally:
        _set_0051_extra_trigger_alias(present=False)
    assert _0051_extra_trigger_alias_exists() is False
    _assert_0051_difference_completion_catalog(
        repaired=False,
        expected_revision=STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION,
    )

    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    _assert_0051_difference_completion_catalog(
        repaired=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0051_api_direct_execute_denied()


def _assert_0050_empty_graph_downgrade_and_reupgrade() -> None:
    assert _current_revision() == HEAD_REVISION
    migration = _load_stocktake_observation_scope_mode_migration_0050()
    assert migration.revision == STOCKTAKE_OBSERVATION_SCOPE_MODE_REVISION
    assert migration.down_revision == STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION
    assert migration.LEGACY_BODY_SHA256 == (
        _load_stocktake_recount_guard_security_migration_0049()
        .EXPECTED_FUNCTION_BODY_SHA256[migration.FUNCTION_SIGNATURE]
    )
    assert migration.QUALIFIED_BODY_SHA256 == (
        STOCKTAKE_OBSERVATION_BODY_SHA256_0050
    )

    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                f"SELECT pg_catalog.count(*) FROM public.{migration.TRIGGER_TABLE}"
            )
            assert cursor.fetchone() == (0,)

    _assert_0049_recount_guard_catalog(
        callers_security_definer=True,
        expected_revision=HEAD_REVISION,
        observation_scope_mode_fixed=True,
    )
    _assert_0049_api_direct_execute_denied()

    _run_alembic("downgrade", STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION)
    assert _current_revision() == STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION
    _assert_0049_recount_guard_catalog(
        callers_security_definer=True,
        expected_revision=STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION,
        observation_scope_mode_fixed=False,
    )
    _assert_0049_api_direct_execute_denied()

    _set_0050_extra_trigger_alias(present=True)
    assert _0050_extra_trigger_alias_exists() is True
    try:
        blocked = _run_alembic("upgrade", "head", expect_success=False)
        output = blocked.stdout + blocked.stderr
        assert migration.CATALOG_ERROR in output
        assert "trigger binding mismatch" in output
        assert _current_revision() == STOCKTAKE_RECOUNT_GUARD_SECURITY_REVISION
        assert _0050_extra_trigger_alias_exists() is True
    finally:
        _set_0050_extra_trigger_alias(present=False)
    assert _0050_extra_trigger_alias_exists() is False

    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    _assert_0049_recount_guard_catalog(
        callers_security_definer=True,
        expected_revision=HEAD_REVISION,
        observation_scope_mode_fixed=True,
    )
    _assert_0049_api_direct_execute_denied()


def _assert_0049_empty_graph_downgrade_and_reupgrade() -> None:
    assert _current_revision() == HEAD_REVISION
    migration = _load_stocktake_recount_guard_security_migration_0049()
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT "
                + ", ".join(
                    "(SELECT pg_catalog.count(*) FROM public."
                    f"{table_name})"
                    for table_name in migration.TRIGGER_TABLES
                )
            )
            assert cursor.fetchone() == (0,) * len(migration.TRIGGER_TABLES)

    _assert_0049_recount_guard_catalog(
        callers_security_definer=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0048_scope_guard_catalog(
        security_definer=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0049_api_direct_execute_denied()

    _run_alembic("downgrade", STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION)
    assert _current_revision() == STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION
    _assert_0049_recount_guard_catalog(
        callers_security_definer=False,
        expected_revision=STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION,
    )
    _assert_0048_scope_guard_catalog(
        security_definer=True,
        expected_revision=STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION,
    )
    _assert_0049_api_direct_execute_denied()

    _set_0049_extra_trigger_alias(present=True)
    try:
        blocked = _run_alembic("upgrade", "head", expect_success=False)
        output = blocked.stdout + blocked.stderr
        assert "0049 stocktake recount caller catalog verification failed" in output
        assert "trigger binding mismatch" in output
        assert _current_revision() == STOCKTAKE_SCOPE_GUARD_SECURITY_REVISION
        assert _0049_extra_trigger_alias_exists() is True
    finally:
        _set_0049_extra_trigger_alias(present=False)
    assert _0049_extra_trigger_alias_exists() is False

    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    _assert_0049_recount_guard_catalog(
        callers_security_definer=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0048_scope_guard_catalog(
        security_definer=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0049_api_direct_execute_denied()


def _assert_0048_empty_graph_downgrade_and_reupgrade() -> None:
    assert _current_revision() == HEAD_REVISION
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT (SELECT pg_catalog.count(*) "
                "FROM public.stocktake_tasks), "
                "(SELECT pg_catalog.count(*) FROM public.stocktake_scopes)"
            )
            assert cursor.fetchone() == (0, 0)

    _assert_0048_scope_guard_catalog(
        security_definer=True,
        expected_revision=HEAD_REVISION,
    )
    _run_alembic("downgrade", "20260903_0047")
    assert _current_revision() == "20260903_0047"
    _assert_0048_scope_guard_catalog(
        security_definer=False,
        expected_revision="20260903_0047",
    )
    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    _assert_0048_scope_guard_catalog(
        security_definer=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0049_recount_guard_catalog(
        callers_security_definer=True,
        expected_revision=HEAD_REVISION,
    )


def _assert_0047_empty_graph_downgrade_and_reupgrade() -> None:
    assert _current_revision() == HEAD_REVISION
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT "
                "(SELECT count(*) FROM public.stocktake_start_completions), "
                "(SELECT count(*) FROM public.stocktake_tasks WHERE task_type "
                "IN ('full', 'sample', 'ad_hoc', 'personal', 'termination'))"
            )
            assert cursor.fetchone() == (0, 0)
    _assert_0047_start_catalog(installed=True)
    _run_alembic("downgrade", CONTENT_CAUSALITY_REVISION)
    assert _current_revision() == CONTENT_CAUSALITY_REVISION
    _assert_0047_start_catalog(installed=False)
    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    _assert_0047_start_catalog(installed=True)


def _assert_0046_empty_graph_downgrade_and_reupgrade() -> None:
    assert _current_revision() == HEAD_REVISION
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT (SELECT count(*) FROM public.material_requests), "
                "(SELECT count(*) FROM public.material_request_revisions), "
                "(SELECT count(*) FROM public.material_request_lines), "
                "(SELECT count(*) FROM public.material_request_files), "
                "(SELECT count(*) FROM public.material_request_commands)"
            )
            assert cursor.fetchone() == (0, 0, 0, 0, 0)
    _assert_0046_content_catalog(installed=True)
    _run_alembic("downgrade", APPROVAL_REVISION)
    assert _current_revision() == APPROVAL_REVISION
    _assert_0046_content_catalog(installed=False)
    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    _assert_0046_content_catalog(installed=True)


def _seed_material_request_approval_world():
    from unittest.mock import patch

    from app.demand_models import ApprovalRouteStepDef, ApprovalRouteVersion
    from app.foundation_models import (
        AuditChainHead,
        FileObject,
        Organization,
        Permission,
        Person,
        Role,
        RoleAssignment,
        RolePermission,
    )
    from app.inventory_models import FormalMaterial
    from app.models import User
    import test_material_request_draft_service as draft_fixtures

    expected_role_codes = {
        "admin",
        "provincial_manager",
        "technician",
        "star_headquarters_approver",
    }
    expected_permission_keys = {
        ("material_request", "create", ""),
        ("material_request", "update_draft", ""),
        ("material_request", "submit", ""),
        ("material_request", "approve_region", "approval_decision"),
        (
            "material_request",
            "approve_headquarters",
            "approval_decision",
        ),
        ("material_request", "register_external", "approval_evidence"),
        ("material_request", "verify_external", "approval_evidence"),
    }
    expected_role_permission_keys = {
        "technician": {
            ("material_request", "create", ""),
            ("material_request", "update_draft", ""),
            ("material_request", "submit", ""),
        },
        "provincial_manager": {
            ("material_request", "approve_region", "approval_decision"),
        },
        "admin": {
            (
                "material_request",
                "approve_headquarters",
                "approval_decision",
            ),
            ("material_request", "register_external", "approval_evidence"),
            ("material_request", "verify_external", "approval_evidence"),
        },
    }
    expected_steps = {
        1: ("provincial_manager", "internal", "organization"),
        2: ("admin", "internal", "national"),
        3: (
            "star_headquarters_approver",
            "external_registration",
            "document",
        ),
    }

    migrator_engine = create_engine(
        _sqlalchemy_url(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        ),
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    try:
        with Session(migrator_engine, expire_on_commit=False) as session:
            roles = tuple(
                session.scalars(
                    select(Role)
                    .where(Role.code.in_(expected_role_codes))
                    .order_by(Role.code)
                ).all()
            )
            roles_by_code = {row.code: row for row in roles}
            assert set(roles_by_code) == expected_role_codes
            assert all(row.status == "active" for row in roles)
            assert {
                row.code for row in roles if row.is_external
            } == {"star_headquarters_approver"}

            permissions = tuple(
                session.scalars(
                    select(Permission).where(
                        Permission.resource == "material_request"
                    )
                ).all()
            )
            permissions_by_key = {
                (row.resource, row.action, row.field_code): row
                for row in permissions
            }
            assert len(permissions_by_key) == len(permissions)
            assert expected_permission_keys <= set(permissions_by_key)

            role_permissions = tuple(
                session.scalars(
                    select(RolePermission).where(
                        RolePermission.role_id.in_(
                            tuple(row.id for row in roles)
                        ),
                        RolePermission.permission_id.in_(
                            tuple(
                                permissions_by_key[key].id
                                for key in expected_permission_keys
                            )
                        ),
                    )
                ).all()
            )
            role_permissions_by_pair = {
                (row.role_id, row.permission_id): row
                for row in role_permissions
            }
            assert len(role_permissions_by_pair) == len(role_permissions)
            for role_code, permission_keys in expected_role_permission_keys.items():
                for permission_key in permission_keys:
                    row = role_permissions_by_pair.get(
                        (
                            roles_by_code[role_code].id,
                            permissions_by_key[permission_key].id,
                        )
                    )
                    assert row is not None and row.effect == "allow"

            route = session.scalar(
                select(ApprovalRouteVersion).where(
                    ApprovalRouteVersion.route_code
                    == "material_request_three_stage",
                    ApprovalRouteVersion.version == 1,
                )
            )
            assert route is not None
            assert route.approval_mode == "external_registration"
            assert route.status == "active"
            assert route.effective_to is None
            route_steps = tuple(
                session.scalars(
                    select(ApprovalRouteStepDef)
                    .where(ApprovalRouteStepDef.route_version_id == route.id)
                    .order_by(ApprovalRouteStepDef.step_no)
                ).all()
            )
            assert {
                row.step_no: (
                    row.role_code,
                    row.source_mode,
                    row.scope_type,
                )
                for row in route_steps
            } == expected_steps

            audit_head = session.get(
                AuditChainHead,
                draft_fixtures.AUDIT_HEAD_ID,
            )
            assert audit_head is not None
            assert audit_head.stream_key == "material_request"
            assert audit_head.last_event_id is None
            assert audit_head.last_hash is None
            assert audit_head.version == 0

            catalog_models = (
                Role,
                Permission,
                RolePermission,
                ApprovalRouteVersion,
                ApprovalRouteStepDef,
                AuditChainHead,
            )
            catalog_counts = {
                model: session.scalar(select(func.count()).select_from(model))
                for model in catalog_models
            }
            fixture_increments = {
                Organization: 3,
                Person: 4,
                User: 4,
                RoleAssignment: 4,
                FormalMaterial: 2,
                FileObject: 1,
            }
            fixture_counts = {
                model: session.scalar(select(func.count()).select_from(model))
                for model in fixture_increments
            }

            def existing_role(*args, **kwargs):
                assert not args
                row = roles_by_code[str(kwargs["code"])]
                assert kwargs["status"] == row.status == "active"
                assert bool(kwargs["is_external"]) is bool(row.is_external)
                return row

            def existing_permission(*args, **kwargs):
                assert not args
                key = (
                    str(kwargs["resource"]),
                    str(kwargs["action"]),
                    str(kwargs["field_code"]),
                )
                assert key in expected_permission_keys
                return permissions_by_key[key]

            def existing_role_permission(*args, **kwargs):
                assert not args
                assert kwargs["effect"] == "allow"
                row = role_permissions_by_pair.get(
                    (kwargs["role_id"], kwargs["permission_id"])
                )
                assert row is not None and row.effect == "allow"
                return row

            def existing_route(*args, **kwargs):
                assert not args
                assert kwargs["route_code"] == route.route_code
                assert kwargs["version"] == route.version == 1
                assert kwargs["approval_mode"] == route.approval_mode
                assert kwargs["effective_to"] is route.effective_to is None
                assert kwargs["status"] == route.status == "active"
                return route

            route_steps_by_number = {row.step_no: row for row in route_steps}

            def existing_route_step(*args, **kwargs):
                assert not args
                row = route_steps_by_number[int(kwargs["step_no"])]
                assert kwargs["route_version_id"] == row.route_version_id
                assert (
                    kwargs["role_code"],
                    kwargs["source_mode"],
                    kwargs["scope_type"],
                ) == expected_steps[row.step_no]
                return row

            def existing_audit_head(*args, **kwargs):
                assert not args
                assert kwargs == {
                    "id": draft_fixtures.AUDIT_HEAD_ID,
                    "stream_key": "material_request",
                    "last_event_id": None,
                    "last_hash": None,
                    "version": 0,
                }
                return audit_head

            with (
                patch.object(
                    draft_fixtures,
                    "Role",
                    side_effect=existing_role,
                ),
                patch.object(
                    draft_fixtures,
                    "Permission",
                    side_effect=existing_permission,
                ),
                patch.object(
                    draft_fixtures,
                    "RolePermission",
                    side_effect=existing_role_permission,
                ),
                patch.object(
                    draft_fixtures,
                    "ApprovalRouteVersion",
                    side_effect=existing_route,
                ),
                patch.object(
                    draft_fixtures,
                    "ApprovalRouteStepDef",
                    side_effect=existing_route_step,
                ),
                patch.object(
                    draft_fixtures,
                    "AuditChainHead",
                    side_effect=existing_audit_head,
                ),
            ):
                world = draft_fixtures.make_world(session, admin_count=2)

            assert {
                model: session.scalar(select(func.count()).select_from(model))
                for model in catalog_models
            } == catalog_counts
            assert {
                model: session.scalar(select(func.count()).select_from(model))
                - fixture_counts[model]
                for model in fixture_increments
            } == fixture_increments
            request_id = draft_fixtures._create_id(
                world,
                "pg16-approval-projection-create",
            )
            return (
                request_id,
                draft_fixtures._draft(world, request_id),
                world.actor_user.id,
                world.manager_users[0].id,
                world.admin_users[0].id,
                world.admin_users[1].id,
            )
    finally:
        migrator_engine.dispose()


def _assert_material_request_snapshot(
    api_engine,
    *,
    request_id: uuid.UUID,
    expected_status: str,
    expected_version: int,
    expected_decided_at: bool,
) -> None:
    from app.demand_models import ApprovalInstance, MaterialRequest, MaterialRequestLine

    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        assert request is not None
        assert request.status == expected_status
        assert request.version == expected_version
        assert (request.decided_at is not None) is expected_decided_at
        assert {
            field: getattr(request, field)
            for field in MATERIAL_REQUEST_NEUTRAL_AXES
        } == MATERIAL_REQUEST_NEUTRAL_AXES

        lines = tuple(
            session.scalars(
                select(MaterialRequestLine)
                .where(MaterialRequestLine.request_id == request_id)
                .order_by(MaterialRequestLine.line_no)
            ).all()
        )
        assert len(lines) == 1
        if expected_status == "draft":
            assert lines[0].status == "draft"
        elif expected_status == "approval_in_progress":
            assert lines[0].status == "approval_pending"
            assert lines[0].final_approved_qty == 0
            assert lines[0].cancelled_qty == 0
            instances = tuple(
                session.scalars(
                    select(ApprovalInstance).where(
                        ApprovalInstance.request_id == request_id
                    )
                ).all()
            )
            assert len(instances) == 1
            assert instances[0].status == "active"
            assert instances[0].current_step_no == 1
            assert instances[0].completed_at is None
        elif expected_status == "returned":
            assert lines[0].status == "approval_pending"
            assert lines[0].final_approved_qty == Decimal("0.000")
            assert lines[0].cancelled_qty == Decimal("0.000")
            instances = tuple(
                session.scalars(
                    select(ApprovalInstance).where(
                        ApprovalInstance.request_id == request_id
                    )
                ).all()
            )
            assert len(instances) == 1
            assert instances[0].status == "returned"
            assert instances[0].current_step_no is None
            assert instances[0].current_step_id is None
            assert instances[0].completed_at is not None
        elif expected_status == "cancelled":
            assert request.cancelled_at is not None
            assert lines[0].status == "cancelled"
            assert lines[0].cancelled_qty == lines[0].final_approved_qty
            instances = tuple(
                session.scalars(
                    select(ApprovalInstance).where(
                        ApprovalInstance.request_id == request_id
                    )
                ).all()
            )
            assert len(instances) == 1
            assert instances[0].status == (
                "completed" if expected_decided_at else "returned"
            )
            assert instances[0].current_step_no is None
            assert instances[0].current_step_id is None
            assert instances[0].completed_at is not None


def _assert_0045_formal_withdrawal(
    api_engine,
    *,
    template_draft,
    requester_user_id: str,
) -> uuid.UUID:
    from app.demand_models import (
        ApprovalAction,
        ApprovalInstance,
        ApprovalStep,
        MaterialRequest,
        MaterialRequestCommand,
        MaterialRequestLine,
    )
    from app.formal_services.material_request_draft import (
        create_material_request_draft,
        derive_material_request_create_id,
        submit_material_request,
    )
    from app.formal_services.material_request_lifecycle import (
        withdraw_material_request,
    )
    from app.foundation_models import AuditEvent, StateTransitionEvent
    from test_material_request_approval_service import _principal
    from test_material_request_draft_service import SECRET, _contact_envelope

    create_key = "pg16-approval-formal-withdraw-create"
    with Session(api_engine) as session:
        requester = _principal(session, requester_user_id)
        request_id = derive_material_request_create_id(
            actor=requester,
            idempotency_key=create_key,
            idempotency_hmac_secret=SECRET,
        )
        created = create_material_request_draft(
            session,
            actor=requester,
            material_request_id=request_id,
            draft=replace(
                template_draft,
                contact_envelope=_contact_envelope(
                    request_id,
                    requester.person_id,
                ),
            ),
            idempotency_key=create_key,
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-pg16-approval-formal-withdraw-create",
        )
        session.commit()

    with Session(api_engine) as session:
        submitted = submit_material_request(
            session,
            actor=_principal(session, requester_user_id),
            material_request_id=request_id,
            expected_version=created.request_version,
            idempotency_key="pg16-approval-formal-withdraw-submit",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-pg16-approval-formal-withdraw-submit",
        )
        session.commit()

    with Session(api_engine) as session:
        withdrawn = withdraw_material_request(
            session,
            actor=_principal(session, requester_user_id),
            material_request_id=request_id,
            expected_version=submitted.version,
            reason="申请人撤回临时 PG16 验证需求",
            idempotency_key="pg16-approval-formal-withdraw-command",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-pg16-approval-formal-withdraw-command",
        )
        session.commit()

    assert withdrawn.request_status == "withdrawn"
    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        instance = session.scalar(
            select(ApprovalInstance).where(
                ApprovalInstance.request_id == request_id
            )
        )
        assert request is not None and instance is not None
        assert request.status == "withdrawn"
        assert request.version == withdrawn.request_version
        assert request.withdrawn_at == instance.completed_at
        assert request.updated_at == request.withdrawn_at
        assert instance.status == "withdrawn"
        assert instance.current_step_no is None
        assert instance.current_step_id is None
        step_statuses = tuple(
            session.scalars(
                select(ApprovalStep.status)
                .where(ApprovalStep.instance_id == instance.id)
                .order_by(ApprovalStep.step_no)
            )
        )
        assert step_statuses == ("cancelled", "cancelled", "cancelled")
        line_statuses = tuple(
            session.scalars(
                select(MaterialRequestLine.status).where(
                    MaterialRequestLine.request_id == request_id
                )
            )
        )
        assert line_statuses == ("approval_pending",)
        command = session.scalar(
            select(MaterialRequestCommand).where(
                MaterialRequestCommand.request_id == request_id,
                MaterialRequestCommand.operation == "withdraw",
            )
        )
        assert command is not None
        assert command.target_version == request.version
        assert command.occurred_at == request.withdrawn_at
        action = session.scalar(
            select(ApprovalAction).where(
                ApprovalAction.command_id == command.id,
                ApprovalAction.action == "withdraw",
            )
        )
        assert action is not None
        assert action.instance_id == instance.id
        assert action.step_id is None
        assert action.actor_user_id == requester_user_id
        assert session.scalar(
            select(func.count()).select_from(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "material_request",
                StateTransitionEvent.aggregate_id == str(request_id),
                StateTransitionEvent.to_status == "withdrawn",
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.aggregate_type == "material_request",
                AuditEvent.aggregate_id == str(request_id),
                AuditEvent.action == "material_request.withdraw",
            )
        ) == 1
    return request_id


def _assert_0045_region_return_then_cancel(
    api_engine,
    *,
    template_draft,
    requester_user_id: str,
    manager_user_id: str,
) -> tuple[uuid.UUID, int]:
    from app.demand_models import (
        ApprovalAction,
        ApprovalInstance,
        ApprovalReturnLineFact,
        ApprovalStep,
        MaterialRequest,
        MaterialRequestCancellationLineFact,
        MaterialRequestCommand,
        MaterialRequestLine,
    )
    from app.formal_services.material_request_approval import (
        MaterialRequestApprovalInput,
        decide_material_request_approval,
    )
    from app.formal_services.material_request_draft import (
        create_material_request_draft,
        derive_material_request_create_id,
        submit_material_request,
    )
    from app.formal_services.material_request_lifecycle import (
        MaterialRequestCancelInput,
        cancel_material_request,
    )
    from app.formal_services.material_request_policy import (
        ApprovalReturnInstruction,
    )
    from app.foundation_models import AuditEvent, StateTransitionEvent
    from test_material_request_approval_service import _principal
    from test_material_request_draft_service import SECRET, _contact_envelope

    create_key = "pg16-approval-region-return-create"
    with Session(api_engine) as session:
        requester = _principal(session, requester_user_id)
        request_id = derive_material_request_create_id(
            actor=requester,
            idempotency_key=create_key,
            idempotency_hmac_secret=SECRET,
        )
        created = create_material_request_draft(
            session,
            actor=requester,
            material_request_id=request_id,
            draft=replace(
                template_draft,
                contact_envelope=_contact_envelope(
                    request_id,
                    requester.person_id,
                ),
            ),
            idempotency_key=create_key,
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-pg16-approval-region-return-create",
        )
        session.commit()

    with Session(api_engine) as session:
        submitted = submit_material_request(
            session,
            actor=_principal(session, requester_user_id),
            material_request_id=request_id,
            expected_version=created.request_version,
            idempotency_key="pg16-approval-region-return-submit",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-pg16-approval-region-return-submit",
        )
        session.commit()

    with Session(api_engine) as session:
        instance = session.scalar(
            select(ApprovalInstance).where(
                ApprovalInstance.request_id == request_id,
                ApprovalInstance.status == "active",
            )
        )
        line = session.scalar(
            select(MaterialRequestLine).where(
                MaterialRequestLine.request_id == request_id,
                MaterialRequestLine.revision_no == submitted.revision_no,
            )
        )
        assert instance is not None and instance.current_step_id is not None
        assert line is not None
        step = session.get(ApprovalStep, instance.current_step_id)
        assert step is not None and step.step_no == 1 and step.status == "open"
        returned = decide_material_request_approval(
            session,
            actor=_principal(session, manager_user_id),
            material_request_id=request_id,
            approval_step_id=step.id,
            expected_request_version=submitted.version,
            expected_step_version=step.version,
            decision=MaterialRequestApprovalInput(
                action="return",
                return_lines=(
                    ApprovalReturnInstruction(
                        line.id,
                        line.requested_qty,
                        "请补充需求依据",
                    ),
                ),
                comment="区域审批退回申请人补充",
            ),
            idempotency_key="pg16-approval-region-return-command",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-pg16-approval-region-return-command",
        )
        returned_step_id = step.id
        # Force every deferred graph validator before the independent commit;
        # the following durable reread proves 0045 accepted the formal graph.
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        session.commit()

    assert returned.request_status == "returned"
    assert returned.instance_status == "returned"
    assert returned.current_step_id is None
    assert dict(returned.state_axes) == MATERIAL_REQUEST_NEUTRAL_AXES
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="returned",
        expected_version=returned.request_version,
        expected_decided_at=False,
    )
    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        instance = session.scalar(
            select(ApprovalInstance).where(
                ApprovalInstance.request_id == request_id
            )
        )
        returned_step = session.get(ApprovalStep, returned_step_id)
        return_fact = session.scalar(
            select(ApprovalReturnLineFact).where(
                ApprovalReturnLineFact.returned_from_step_id
                == returned_step_id
            )
        )
        line = session.scalar(
            select(MaterialRequestLine).where(
                MaterialRequestLine.request_id == request_id
            )
        )
        assert request is not None and instance is not None
        assert returned_step is not None and return_fact is not None
        assert line is not None
        assert request.submitted_at is not None
        assert request.decided_at is None and request.cancelled_at is None
        assert instance.status == "returned"
        assert instance.completed_at == returned_step.decided_at
        assert tuple(
            session.scalars(
                select(ApprovalStep.status)
                .where(ApprovalStep.instance_id == instance.id)
                .order_by(ApprovalStep.step_no)
            )
        ) == ("returned", "pending", "pending")
        assert return_fact.target_kind == "requester_revision"
        assert return_fact.target_step_id is None
        assert return_fact.required_review_qty == line.requested_qty
        return_action = session.get(
            ApprovalAction,
            return_fact.return_action_id,
        )
        assert return_action is not None
        assert return_action.action == "return"
        assert return_action.actor_user_id == manager_user_id
        assert return_action.step_id == returned_step_id
        assert session.scalar(
            select(func.count()).select_from(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "material_request",
                StateTransitionEvent.aggregate_id == str(request_id),
                StateTransitionEvent.from_status == "approval_in_progress",
                StateTransitionEvent.to_status == "returned",
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.aggregate_type == "material_request",
                AuditEvent.aggregate_id == str(request_id),
                AuditEvent.action == "material_request.approval.return",
            )
        ) == 1

    with Session(api_engine) as session:
        cancelled = cancel_material_request(
            session,
            actor=_principal(session, requester_user_id),
            material_request_id=request_id,
            expected_version=returned.request_version,
            cancellation=MaterialRequestCancelInput(
                reason="退回后确认无需继续申请",
                lines=(),
            ),
            idempotency_key="pg16-approval-returned-cancel-command",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-pg16-approval-returned-cancel-command",
        )
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        session.commit()

    assert cancelled.request_status == "cancelled"
    assert dict(cancelled.state_axes) == MATERIAL_REQUEST_NEUTRAL_AXES
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="cancelled",
        expected_version=cancelled.request_version,
        expected_decided_at=False,
    )
    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        instance = session.get(ApprovalInstance, cancelled.approval_instance_id)
        line = session.scalar(
            select(MaterialRequestLine).where(
                MaterialRequestLine.request_id == request_id
            )
        )
        assert request is not None and instance is not None and line is not None
        assert request.cancelled_at is not None
        assert request.cancelled_at == request.updated_at
        assert request.decided_at is None
        assert instance.status == "returned"
        assert line.status == "cancelled"
        assert line.final_approved_qty == Decimal("0.000")
        assert line.cancelled_qty == Decimal("0.000")
        assert session.scalar(
            select(func.count())
            .select_from(MaterialRequestCancellationLineFact)
            .where(
                MaterialRequestCancellationLineFact.request_id == request_id
            )
        ) == 0
        cancel_command = session.scalar(
            select(MaterialRequestCommand).where(
                MaterialRequestCommand.request_id == request_id,
                MaterialRequestCommand.operation == "cancel",
            )
        )
        assert cancel_command is not None
        assert cancel_command.target_version == request.version
        assert cancel_command.occurred_at == request.cancelled_at
        cancel_action = session.scalar(
            select(ApprovalAction).where(
                ApprovalAction.command_id == cancel_command.id,
                ApprovalAction.action == "cancel",
            )
        )
        assert cancel_action is not None
        assert cancel_action.instance_id == instance.id
        assert cancel_action.step_id is None
        assert cancel_action.actor_user_id == requester_user_id
        assert session.scalar(
            select(func.count()).select_from(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "material_request",
                StateTransitionEvent.aggregate_id == str(request_id),
                StateTransitionEvent.from_status == "returned",
                StateTransitionEvent.to_status == "cancelled",
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.aggregate_type == "material_request",
                AuditEvent.aggregate_id == str(request_id),
                AuditEvent.action == "material_request.cancel",
            )
        ) == 1
    return request_id, cancelled.request_version


def _assert_0045_formal_approved_cancel(
    api_engine,
    *,
    request_id: uuid.UUID,
    request_version: int,
    request_line_id: uuid.UUID,
    approved_qty: Decimal,
    requester_user_id: str,
) -> int:
    from app.demand_models import (
        ApprovalAction,
        ApprovalInstance,
        MaterialRequest,
        MaterialRequestCancellationLineFact,
        MaterialRequestCommand,
        MaterialRequestLine,
    )
    from app.formal_services.material_request_lifecycle import (
        MaterialRequestCancellationLineInput,
        MaterialRequestCancelInput,
        cancel_material_request,
    )
    from app.foundation_models import AuditEvent, StateTransitionEvent
    from test_material_request_approval_service import _principal
    from test_material_request_draft_service import SECRET

    line_reason = "审批完成且未进入履约，申请人确认整行取消"
    with Session(api_engine) as session:
        cancelled = cancel_material_request(
            session,
            actor=_principal(session, requester_user_id),
            material_request_id=request_id,
            expected_version=request_version,
            cancellation=MaterialRequestCancelInput(
                reason="已批准需求尚未履约，申请人确认取消",
                lines=(
                    MaterialRequestCancellationLineInput(
                        request_line_id,
                        approved_qty,
                        line_reason,
                    ),
                ),
            ),
            idempotency_key="pg16-approval-approved-cancel-command",
            idempotency_hmac_secret=SECRET,
            trace_request_id="trace-pg16-approval-approved-cancel-command",
        )
        session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        session.commit()

    assert cancelled.request_status == "cancelled"
    assert dict(cancelled.state_axes) == MATERIAL_REQUEST_NEUTRAL_AXES
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="cancelled",
        expected_version=cancelled.request_version,
        expected_decided_at=True,
    )
    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        line = session.get(MaterialRequestLine, request_line_id)
        instance = session.get(ApprovalInstance, cancelled.approval_instance_id)
        fact = session.scalar(
            select(MaterialRequestCancellationLineFact).where(
                MaterialRequestCancellationLineFact.request_id == request_id,
                MaterialRequestCancellationLineFact.request_line_id
                == request_line_id,
            )
        )
        assert request is not None and line is not None and instance is not None
        assert fact is not None
        assert request.cancelled_at is not None
        assert request.cancelled_at == request.updated_at
        assert request.decided_at == instance.completed_at
        assert instance.status == "completed"
        assert line.status == "cancelled"
        assert line.final_approved_qty == approved_qty
        assert line.cancelled_qty == approved_qty
        assert fact.final_approved_qty_before == approved_qty
        assert fact.cancelled_qty == approved_qty
        assert fact.reason == line_reason
        assert fact.actor_user_id == requester_user_id
        cancel_command = session.get(
            MaterialRequestCommand,
            fact.cancel_command_id,
        )
        cancel_action = session.get(ApprovalAction, fact.cancel_action_id)
        assert cancel_command is not None and cancel_action is not None
        assert cancel_command.operation == "cancel"
        assert cancel_command.target_version == request.version
        assert cancel_command.occurred_at == request.cancelled_at
        assert cancel_action.command_id == cancel_command.id
        assert cancel_action.instance_id == instance.id
        assert cancel_action.step_id is None
        assert cancel_action.action == "cancel"
        assert cancel_action.actor_user_id == requester_user_id
        assert session.scalar(
            select(func.count()).select_from(StateTransitionEvent).where(
                StateTransitionEvent.aggregate_type == "material_request",
                StateTransitionEvent.aggregate_id == str(request_id),
                StateTransitionEvent.from_status == "approved",
                StateTransitionEvent.to_status == "cancelled",
            )
        ) == 1
        assert session.scalar(
            select(func.count()).select_from(AuditEvent).where(
                AuditEvent.aggregate_type == "material_request",
                AuditEvent.aggregate_id == str(request_id),
                AuditEvent.action == "material_request.cancel",
            )
        ) == 1
    return cancelled.request_version


def _assert_0045_version_only_projection_drift_is_rejected(
    api_engine,
    *,
    request_id: uuid.UUID,
    expected_request_version: int,
) -> None:
    from app.demand_models import (
        ApprovalInstance,
        ApprovalStep,
        MaterialRequest,
        MaterialRequestLine,
    )

    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        instance = session.scalar(
            select(ApprovalInstance).where(
                ApprovalInstance.request_id == request_id,
                ApprovalInstance.status == "active",
            )
        )
        assert request is not None and instance is not None
        assert request.status == "approval_in_progress"
        assert request.version == expected_request_version
        assert instance.current_step_id is not None
        step = session.get(ApprovalStep, instance.current_step_id)
        line = session.scalar(
            select(MaterialRequestLine).where(
                MaterialRequestLine.request_id == request_id,
                MaterialRequestLine.revision_no == request.revision_no,
            )
        )
        assert step is not None and step.status == "open"
        assert line is not None and line.status == "approval_pending"
        targets = (
            (
                "current approval_step",
                "approval_steps",
                step.id,
                step.version,
                step.updated_at,
            ),
            (
                "approval_instance",
                "approval_instances",
                instance.id,
                instance.version,
                instance.updated_at,
            ),
            (
                "material_request_line",
                "material_request_lines",
                line.id,
                line.version,
                line.updated_at,
            ),
        )

    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    for label, table_name, row_id, original_version, original_updated_at in targets:
        for enforcement in ("force_deferred", "natural_commit"):
            drift_time = original_updated_at + timedelta(
                seconds=1 if enforcement == "force_deferred" else 2
            )
            with psycopg.connect(**api_parameters) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            "UPDATE public.{} "
                            "SET version = version + 1, updated_at = %s "
                            "WHERE id = %s"
                        ).format(sql.Identifier(table_name)),
                        (drift_time, row_id),
                    )
                    assert cursor.rowcount == 1, label
                    cursor.execute(
                        sql.SQL(
                            "SELECT version, updated_at FROM public.{} "
                            "WHERE id = %s"
                        ).format(sql.Identifier(table_name)),
                        (row_id,),
                    )
                    assert cursor.fetchone() == (
                        original_version + 1,
                        drift_time,
                    )
                    if enforcement == "force_deferred":
                        with pytest.raises(psycopg.Error) as drift_failure:
                            cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                    else:
                        with pytest.raises(psycopg.Error) as drift_failure:
                            connection.commit()
                    assert (
                        "formal material request approval projection is invalid"
                        in str(drift_failure.value)
                    ), label
                connection.rollback()

            with psycopg.connect(**api_parameters) as connection:
                with connection.cursor() as cursor:
                    cursor.execute(
                        sql.SQL(
                            "SELECT version, updated_at FROM public.{} "
                            "WHERE id = %s"
                        ).format(sql.Identifier(table_name)),
                        (row_id,),
                    )
                    assert cursor.fetchone() == (
                        original_version,
                        original_updated_at,
                    ), label

    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="approval_in_progress",
        expected_version=expected_request_version,
        expected_decided_at=False,
    )


def _assert_0029_decision_guard_rejects_cross_request_line(
    api_engine,
    *,
    request_id: uuid.UUID,
) -> None:
    now = datetime.now(timezone.utc)
    with api_engine.connect() as connection:
        current = connection.execute(
            text(
                """
                SELECT step.id AS step_id,
                       step.source_mode,
                       candidate.user_id,
                       candidate.person_id,
                       candidate.role_assignment_id,
                       candidate.authorization_version
                  FROM approval_instances AS instance
                  JOIN approval_steps AS step
                    ON step.id = instance.current_step_id
                  JOIN approval_step_candidates AS candidate
                    ON candidate.step_id = step.id
                   AND candidate.candidate_kind = 'assignee'
                 WHERE instance.request_id = :request_id
                   AND instance.status = 'active'
                 ORDER BY candidate.user_id
                 LIMIT 1
                """
            ),
            {"request_id": request_id},
        ).mappings().one()
        foreign_line = connection.execute(
            text(
                """
                SELECT line.id, line.requested_qty
                  FROM material_request_lines AS line
                 WHERE line.request_id <> :request_id
                 ORDER BY line.created_at, line.id
                 LIMIT 1
                """
            ),
            {"request_id": request_id},
        ).mappings().one()
        with pytest.raises(DBAPIError) as failure:
            connection.execute(
                text(
                    """
                    INSERT INTO approval_step_line_decisions (
                        id, step_id, request_line_id, input_qty,
                        approved_qty, rejected_qty, reason, decision_source,
                        external_registration_id, decided_by_user_id,
                        decided_by_person_id, decided_role_assignment_id,
                        authorization_version, decided_at, created_at
                    ) VALUES (
                        :id, :step_id, :request_line_id, :input_qty,
                        :approved_qty, 0, 'cross-request guard probe',
                        :decision_source, NULL, :decided_by_user_id,
                        :decided_by_person_id, :decided_role_assignment_id,
                        :authorization_version, :decided_at, :created_at
                    )
                    """
                ),
                {
                    "id": uuid.uuid4(),
                    "step_id": current["step_id"],
                    "request_line_id": foreign_line["id"],
                    "input_qty": foreign_line["requested_qty"],
                    "approved_qty": foreign_line["requested_qty"],
                    "decision_source": current["source_mode"],
                    "decided_by_user_id": current["user_id"],
                    "decided_by_person_id": current["person_id"],
                    "decided_role_assignment_id": current[
                        "role_assignment_id"
                    ],
                    "authorization_version": current[
                        "authorization_version"
                    ],
                    "decided_at": now,
                    "created_at": now,
                },
            )
        assert "formal material-request invariant violated" in str(failure.value)
        assert "ambiguous" not in str(failure.value).lower()
        connection.rollback()


def _assert_0045_external_pending_without_registration_is_rejected(
    api_engine,
    *,
    request_id: uuid.UUID,
    expected_request_version: int,
) -> None:
    from app.demand_models import (
        ApprovalExternalRegistration,
        ApprovalInstance,
        ApprovalStep,
        MaterialRequest,
    )

    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        instance = session.scalar(
            select(ApprovalInstance).where(
                ApprovalInstance.request_id == request_id,
                ApprovalInstance.status == "active",
            )
        )
        assert request is not None and instance is not None
        assert request.status == "approval_in_progress"
        assert request.version == expected_request_version
        assert instance.current_step_no == 3
        assert instance.current_step_id is not None
        step = session.get(ApprovalStep, instance.current_step_id)
        assert step is not None
        assert step.source_mode == "external_registration"
        assert step.status == "awaiting_external_evidence"
        assert session.scalar(
            select(func.count())
            .select_from(ApprovalExternalRegistration)
            .where(ApprovalExternalRegistration.step_id == step.id)
        ) == 0
        step_id = step.id
        original_step = (step.status, step.version, step.updated_at)

    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    for enforcement in ("force_deferred", "natural_commit"):
        drift_time = original_step[2] + timedelta(
            seconds=1 if enforcement == "force_deferred" else 2
        )
        with psycopg.connect(**api_parameters) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "UPDATE public.approval_steps "
                    "SET status = 'evidence_pending_verification', "
                    "version = version + 1, updated_at = %s "
                    "WHERE id = %s "
                    "AND status = 'awaiting_external_evidence'",
                    (drift_time, step_id),
                )
                assert cursor.rowcount == 1
                cursor.execute(
                    "SELECT count(*) "
                    "FROM public.approval_external_registrations "
                    "WHERE step_id = %s",
                    (step_id,),
                )
                assert cursor.fetchone() == (0,)
                if enforcement == "force_deferred":
                    with pytest.raises(psycopg.Error) as pending_failure:
                        cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
                else:
                    with pytest.raises(psycopg.Error) as pending_failure:
                        connection.commit()
                assert (
                    "formal material request approval projection is invalid"
                    in str(pending_failure.value)
                )
            connection.rollback()

        with psycopg.connect(**api_parameters) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT status, version, updated_at "
                    "FROM public.approval_steps WHERE id = %s",
                    (step_id,),
                )
                assert cursor.fetchone() == original_step
                cursor.execute(
                    "SELECT count(*) "
                    "FROM public.approval_external_registrations "
                    "WHERE step_id = %s",
                    (step_id,),
                )
                assert cursor.fetchone() == (0,)

    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        instance = session.scalar(
            select(ApprovalInstance).where(
                ApprovalInstance.request_id == request_id,
                ApprovalInstance.status == "active",
            )
        )
        step = (
            session.get(ApprovalStep, instance.current_step_id)
            if instance is not None and instance.current_step_id is not None
            else None
        )
        assert request is not None and instance is not None and step is not None
        assert request.status == "approval_in_progress"
        assert request.version == expected_request_version
        assert instance.current_step_no == 3
        assert step.status == "awaiting_external_evidence"
        assert {
            field: getattr(request, field)
            for field in MATERIAL_REQUEST_NEUTRAL_AXES
        } == MATERIAL_REQUEST_NEUTRAL_AXES


def _add_0045_external_command_action_pair(
    session: Session,
    *,
    marker: str,
    operation: str,
    action: str,
    request_id: uuid.UUID,
    request_reference: str,
    revision_id: uuid.UUID,
    revision_no: int,
    instance_id: uuid.UUID,
    step_id: uuid.UUID,
    target_version: int,
    actor_user_id: str,
    actor_person_id: uuid.UUID,
    actor_role_assignment_id: uuid.UUID,
    authorization_version: int,
    request_document: dict[str, object],
    result_document: dict[str, object],
) -> tuple[uuid.UUID, uuid.UUID]:
    from app.demand_models import ApprovalAction, MaterialRequestCommand

    def canonical_hash(document: dict[str, object]) -> str:
        return hashlib.sha256(
            json.dumps(
                document,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            ).encode("utf-8")
        ).hexdigest()

    occurred_at = session.scalar(select(func.current_timestamp()))
    assert isinstance(occurred_at, datetime)
    payload_document = {
        "operation": operation,
        "request_id": str(request_id),
        "instance_id": str(instance_id),
        "step_id": str(step_id),
        "target_version": target_version,
        "negative_gate_marker": marker,
    }
    request_hash = canonical_hash(payload_document)
    command = MaterialRequestCommand(
        id=uuid.uuid4(),
        operation=operation,
        request_id=request_id,
        target_version=target_version,
        idempotency_key_hash=hashlib.sha256(marker.encode("utf-8")).hexdigest(),
        request_reference=request_reference,
        request_hash=request_hash,
        result_hash=canonical_hash(result_document),
        request_jsonb={
            "schema": "rsc.material_request_approval_command.v1",
            "operation": operation,
            "request_id": str(request_id),
            "revision_id": str(revision_id),
            "revision_no": revision_no,
            "instance_id": str(instance_id),
            "target_version": target_version,
            "payload_sha256": request_hash,
            **request_document,
        },
        result_jsonb=result_document,
        actor_user_id=actor_user_id,
        actor_person_id=actor_person_id,
        actor_role_assignment_id=actor_role_assignment_id,
        authorization_version=authorization_version,
        occurred_at=occurred_at,
        created_at=occurred_at,
    )
    session.add(command)
    session.flush()
    action_row = ApprovalAction(
        id=uuid.uuid4(),
        instance_id=instance_id,
        step_id=step_id,
        command_id=command.id,
        action=action,
        actor_user_id=actor_user_id,
        actor_person_id=actor_person_id,
        actor_role_assignment_id=actor_role_assignment_id,
        authorization_version=authorization_version,
        source_mode="external_registration",
        comment="0045 PostgreSQL 负向门禁",
        occurred_at=occurred_at,
        created_at=occurred_at,
    )
    session.add(action_row)
    session.flush()
    return command.id, action_row.id


def _assert_0045_register_pair_without_registration_is_rejected(
    api_engine,
    *,
    request_id: uuid.UUID,
    expected_request_version: int,
    registering_admin_user_id: str,
) -> None:
    from sqlalchemy.exc import DBAPIError

    from app.demand_models import (
        ApprovalAction,
        ApprovalExternalRegistration,
        ApprovalInstance,
        ApprovalStep,
        ApprovalStepCandidate,
        MaterialRequest,
        MaterialRequestCommand,
    )

    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        instance = session.scalar(
            select(ApprovalInstance).where(
                ApprovalInstance.request_id == request_id,
                ApprovalInstance.status == "active",
            )
        )
        assert request is not None and instance is not None
        assert request.status == "approval_in_progress"
        assert request.version == expected_request_version
        assert instance.current_step_no == 3
        assert instance.current_step_id is not None
        step = session.get(ApprovalStep, instance.current_step_id)
        assert step is not None
        assert step.status == "awaiting_external_evidence"
        assert step.source_mode == "external_registration"
        actor = session.scalar(
            select(ApprovalStepCandidate).where(
                ApprovalStepCandidate.step_id == step.id,
                ApprovalStepCandidate.user_id == registering_admin_user_id,
                ApprovalStepCandidate.candidate_kind == "registrar",
            )
        )
        assert actor is not None
        assert session.scalar(
            select(func.count())
            .select_from(ApprovalExternalRegistration)
            .where(ApprovalExternalRegistration.step_id == step.id)
        ) == 0
        request_snapshot = (
            request.status,
            request.version,
            request.updated_at,
        )
        instance_snapshot = (
            instance.status,
            instance.version,
            instance.updated_at,
        )
        step_snapshot = (step.status, step.version, step.updated_at)
        request_no = request.request_no
        revision_id = instance.request_revision_id
        revision_no = instance.revision_no
        instance_id = instance.id
        instance_version = instance.version
        step_id = step.id
        step_attempt_no = step.attempt_no
        step_version = step.version
        actor_snapshot = (
            actor.user_id,
            actor.person_id,
            actor.role_assignment_id,
            actor.authorization_version,
        )

    for enforcement in ("force_deferred", "natural_commit"):
        fake_registration_id = uuid.uuid4()
        fake_registration_no = (
            f"PG16-ORPHAN-REGISTER-{fake_registration_id.hex[:16].upper()}"
        )
        manifest_sha256 = hashlib.sha256(
            f"{request_id}:{step_id}:{enforcement}".encode("utf-8")
        ).hexdigest()
        target_version = expected_request_version + 1
        marker = f"pg16-orphan-register-{enforcement}-{uuid.uuid4()}"
        result_document: dict[str, object] = {
            "kind": "external_registration",
            "request_id": str(request_id),
            "request_no": request_no,
            "request_status": "approval_in_progress",
            "request_version": target_version,
            "revision_id": str(revision_id),
            "revision_no": revision_no,
            "instance_id": str(instance_id),
            "instance_version": instance_version + 1,
            "step_id": str(step_id),
            "step_attempt_no": step_attempt_no,
            "step_status": "evidence_pending_verification",
            "step_version": step_version + 1,
            "registration_id": str(fake_registration_id),
            "registration_no": fake_registration_no,
            "registration_status": "pending_verification",
            "external_action": "approve",
            "decision_manifest_sha256": manifest_sha256,
            "state_axes": dict(MATERIAL_REQUEST_NEUTRAL_AXES),
        }
        with Session(api_engine) as session:
            command_id, action_id = _add_0045_external_command_action_pair(
                session,
                marker=marker,
                operation="register_external",
                action="register_external_evidence",
                request_id=request_id,
                request_reference=(
                    f"/api/v1/material-requests/{request_id}/approval-steps/"
                    f"{step_id}/external-evidence"
                ),
                revision_id=revision_id,
                revision_no=revision_no,
                instance_id=instance_id,
                step_id=step_id,
                target_version=target_version,
                actor_user_id=actor_snapshot[0],
                actor_person_id=actor_snapshot[1],
                actor_role_assignment_id=actor_snapshot[2],
                authorization_version=actor_snapshot[3],
                request_document={
                    "external_action": "approve",
                    "registration_id": str(fake_registration_id),
                    "return_lines": [],
                    "registration_comment": "",
                    "decision_manifest_sha256": manifest_sha256,
                    "sensitive_fields": "excluded",
                },
                result_document=result_document,
            )
            command = session.get(MaterialRequestCommand, command_id)
            action = session.get(ApprovalAction, action_id)
            request = session.get(MaterialRequest, request_id)
            instance = session.get(ApprovalInstance, instance_id)
            step = session.get(ApprovalStep, step_id)
            assert command is not None and action is not None
            assert request is not None and instance is not None
            assert step is not None

            # Make the forged command/action the exact latest request operation.
            # Keep the external step awaiting evidence: before 0045's reverse
            # command binding, a graph with no registration rows had no other
            # validator path that could prove this pair was orphaned.
            request.version += 1
            request.updated_at = command.occurred_at
            instance.version += 1
            instance.updated_at = command.occurred_at
            step.version += 1
            step.updated_at = command.occurred_at
            session.flush()

            assert (
                request.status,
                request.version,
                request.updated_at,
            ) == (
                "approval_in_progress",
                target_version,
                command.occurred_at,
            )
            assert (
                instance.status,
                instance.version,
                instance.updated_at,
            ) == (
                "active",
                instance_version + 1,
                command.occurred_at,
            )
            assert (
                step.status,
                step.version,
                step.updated_at,
            ) == (
                "awaiting_external_evidence",
                step_version + 1,
                command.occurred_at,
            )
            assert session.get(
                ApprovalExternalRegistration,
                fake_registration_id,
            ) is None
            if enforcement == "force_deferred":
                with pytest.raises(DBAPIError) as orphan_failure:
                    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            else:
                with pytest.raises(DBAPIError) as orphan_failure:
                    session.commit()
            assert (
                "formal material request approval projection is invalid"
                in str(orphan_failure.value)
            )
            session.rollback()

        with Session(api_engine) as session:
            request = session.get(MaterialRequest, request_id)
            instance = session.get(ApprovalInstance, instance_id)
            step = session.get(ApprovalStep, step_id)
            assert request is not None and instance is not None and step is not None
            assert (request.status, request.version, request.updated_at) == (
                request_snapshot
            )
            assert (instance.status, instance.version, instance.updated_at) == (
                instance_snapshot
            )
            assert (step.status, step.version, step.updated_at) == step_snapshot
            assert session.get(MaterialRequestCommand, command_id) is None
            assert session.get(ApprovalAction, action_id) is None
            assert session.get(
                ApprovalExternalRegistration,
                fake_registration_id,
            ) is None


def _assert_0045_verify_pair_without_verified_registration_is_rejected(
    api_engine,
    *,
    request_id: uuid.UUID,
    expected_request_version: int,
    verifying_admin_user_id: str,
) -> None:
    from sqlalchemy.exc import DBAPIError

    from app.demand_models import (
        ApprovalAction,
        ApprovalExternalRegistration,
        ApprovalInstance,
        ApprovalStep,
        ApprovalStepCandidate,
        MaterialRequest,
        MaterialRequestCommand,
    )

    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        instance = session.scalar(
            select(ApprovalInstance).where(
                ApprovalInstance.request_id == request_id,
                ApprovalInstance.status == "active",
            )
        )
        assert request is not None and instance is not None
        assert request.status == "approval_in_progress"
        assert request.version == expected_request_version
        assert instance.current_step_no == 3
        assert instance.current_step_id is not None
        step = session.get(ApprovalStep, instance.current_step_id)
        assert step is not None
        assert step.status == "awaiting_external_evidence"
        assert session.scalar(
            select(func.count())
            .select_from(ApprovalExternalRegistration)
            .where(ApprovalExternalRegistration.step_id == step.id)
        ) == 0
        actor = session.scalar(
            select(ApprovalStepCandidate).where(
                ApprovalStepCandidate.step_id == step.id,
                ApprovalStepCandidate.user_id == verifying_admin_user_id,
                ApprovalStepCandidate.candidate_kind == "verifier",
            )
        )
        assert actor is not None
        request_snapshot = (
            request.status,
            request.version,
            request.updated_at,
        )
        instance_snapshot = (
            instance.status,
            instance.version,
            instance.updated_at,
        )
        step_snapshot = (step.status, step.version, step.updated_at)
        request_no = request.request_no
        revision_id = instance.request_revision_id
        revision_no = instance.revision_no
        instance_id = instance.id
        instance_version = instance.version
        step_id = step.id
        step_attempt_no = step.attempt_no
        step_version = step.version
        actor_snapshot = (
            actor.user_id,
            actor.person_id,
            actor.role_assignment_id,
            actor.authorization_version,
        )

    for enforcement in ("force_deferred", "natural_commit"):
        fake_registration_id = uuid.uuid4()
        manifest_sha256 = hashlib.sha256(
            f"{request_id}:{step_id}:{fake_registration_id}".encode("utf-8")
        ).hexdigest()
        target_version = expected_request_version + 1
        marker = f"pg16-orphan-verify-{enforcement}-{uuid.uuid4()}"
        result_document: dict[str, object] = {
            "kind": "external_verification",
            "request_id": str(request_id),
            "request_no": request_no,
            "request_status": "approval_in_progress",
            "request_version": target_version,
            "revision_id": str(revision_id),
            "revision_no": revision_no,
            "instance_id": str(instance_id),
            "instance_status": "active",
            "instance_version": instance_version + 1,
            "step_id": str(step_id),
            "step_attempt_no": step_attempt_no,
            "step_status": "awaiting_external_evidence",
            "step_version": step_version + 1,
            "registration_id": str(fake_registration_id),
            "registration_status": "rejected",
            "verification_decision": "reject",
            "current_step_id": str(step_id),
            "current_step_no": 3,
            "opened_step_id": None,
            "opened_step_attempt_no": None,
            "state_axes": dict(MATERIAL_REQUEST_NEUTRAL_AXES),
        }
        with Session(api_engine) as session:
            command_id, action_id = _add_0045_external_command_action_pair(
                session,
                marker=marker,
                operation="verify_external",
                action="verify_external_reject",
                request_id=request_id,
                request_reference=(
                    f"/mr/{request_id}/steps/{step_id}/external/"
                    f"{fake_registration_id}/verify"
                ),
                revision_id=revision_id,
                revision_no=revision_no,
                instance_id=instance_id,
                step_id=step_id,
                target_version=target_version,
                actor_user_id=actor_snapshot[0],
                actor_person_id=actor_snapshot[1],
                actor_role_assignment_id=actor_snapshot[2],
                authorization_version=actor_snapshot[3],
                request_document={
                    "registration_id": str(fake_registration_id),
                    "verification_decision": "reject",
                    "registration_manifest_sha256": manifest_sha256,
                    "sensitive_fields": "excluded",
                },
                result_document=result_document,
            )
            command = session.get(MaterialRequestCommand, command_id)
            action = session.get(ApprovalAction, action_id)
            request = session.get(MaterialRequest, request_id)
            instance = session.get(ApprovalInstance, instance_id)
            step = session.get(ApprovalStep, step_id)
            assert command is not None and action is not None
            assert request is not None and instance is not None
            assert step is not None

            # Keep the request and current external step non-terminal, as they
            # would be after a legitimate verification rejection.  With no
            # registration row, the validator without 0045's reverse binding
            # had no row to iterate and therefore no way to reject this
            # otherwise coherent command/action pair.
            request.version += 1
            request.updated_at = command.occurred_at
            instance.version += 1
            instance.updated_at = command.occurred_at
            step.version += 1
            step.updated_at = command.occurred_at
            session.flush()

            assert (
                request.status,
                request.version,
                request.updated_at,
            ) == (
                "approval_in_progress",
                target_version,
                command.occurred_at,
            )
            assert (
                instance.status,
                instance.version,
                instance.updated_at,
            ) == (
                "active",
                instance_version + 1,
                command.occurred_at,
            )
            assert (
                step.status,
                step.version,
                step.updated_at,
            ) == (
                "awaiting_external_evidence",
                step_version + 1,
                command.occurred_at,
            )
            assert session.get(
                ApprovalExternalRegistration,
                fake_registration_id,
            ) is None
            if enforcement == "force_deferred":
                with pytest.raises(DBAPIError) as orphan_failure:
                    session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
            else:
                with pytest.raises(DBAPIError) as orphan_failure:
                    session.commit()
            assert (
                "formal material request approval projection is invalid"
                in str(orphan_failure.value)
            )
            session.rollback()

        with Session(api_engine) as session:
            request = session.get(MaterialRequest, request_id)
            instance = session.get(ApprovalInstance, instance_id)
            step = session.get(ApprovalStep, step_id)
            assert request is not None and instance is not None
            assert step is not None
            assert (request.status, request.version, request.updated_at) == (
                request_snapshot
            )
            assert (instance.status, instance.version, instance.updated_at) == (
                instance_snapshot
            )
            assert (step.status, step.version, step.updated_at) == step_snapshot
            assert session.get(
                ApprovalExternalRegistration,
                fake_registration_id,
            ) is None
            assert session.get(MaterialRequestCommand, command_id) is None
            assert session.get(ApprovalAction, action_id) is None


def _is_expected_pg16_database_boundary_failure(
    failure: BaseException,
) -> bool:
    try:
        code = getattr(failure, "code")
    except Exception:
        return False
    return (
        isinstance(code, str)
        and code in PG16_REDACTED_DATABASE_FAILURE_CODES
    )


def _reveal_pg16_service_database_error(engine, operation):
    """Expose only sanitized release-gate DB evidence hidden by services."""

    return run_with_sanitized_database_diagnostics(
        engine,
        operation,
        replace_unlinked_database_failure_when=(
            _is_expected_pg16_database_boundary_failure
        ),
    )


_MATERIAL_REQUEST_LINE_COLUMNS_0046 = (
    "id",
    "request_id",
    "revision_id",
    "revision_no",
    "line_no",
    "client_line_key",
    "material_id",
    "suggested_substitute_material_id",
    "requested_qty",
    "required_date",
    "note",
    "status",
    "final_approved_qty",
    "cancelled_qty",
    "version",
    "updated_at",
    "created_at",
)


def _assert_0046_sha256(value: object) -> None:
    assert isinstance(value, str)
    assert len(value) == 64
    assert value == value.lower()
    assert set(value) <= set("0123456789abcdef")


def _assert_0046_content_command_chain(
    api_engine,
    *,
    request_id: uuid.UUID,
    expected_content_operations: tuple[str, ...],
) -> tuple[tuple[str, int, str], ...]:
    from app.demand_models import MaterialRequestCommand

    with Session(api_engine) as session:
        commands = tuple(
            session.execute(
                select(
                    MaterialRequestCommand.operation,
                    MaterialRequestCommand.target_version,
                    MaterialRequestCommand.projection_manifest_sha256,
                )
                .where(MaterialRequestCommand.request_id == request_id)
                .order_by(MaterialRequestCommand.target_version)
            ).all()
        )
    content_commands = tuple(
        (operation, target_version, manifest)
        for operation, target_version, manifest in commands
        if operation in {"create", "update_draft", "submit"}
    )
    assert tuple(row[0] for row in content_commands) == expected_content_operations
    for _, _, manifest in content_commands:
        _assert_0046_sha256(manifest)
    for operation, _, manifest in commands:
        if operation not in {"create", "update_draft", "submit"}:
            assert manifest is None

    migrator_parameters = _connection_parameters(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    with psycopg.connect(**migrator_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT public."
                "rsc_validate_material_request_content_causality_0046(%s)",
                (request_id,),
            )
            # PostgreSQL's void datum is exposed by psycopg as an empty text
            # value, not Python None.  Successful row delivery proves the
            # validator completed; an invalid graph raises before this point.
            assert cursor.fetchone() is not None
    return content_commands


def _assert_0046_datestyle_validator_stability(
    *,
    request_id: uuid.UUID,
) -> None:
    migrator_parameters = _connection_parameters(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
    )
    with psycopg.connect(**migrator_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL DateStyle = 'SQL, DMY'")
            cursor.execute("SHOW DateStyle")
            assert cursor.fetchone() == ("SQL, DMY",)
            cursor.execute(
                "SELECT public."
                "rsc_validate_material_request_content_causality_0046(%s)",
                (request_id,),
            )
            assert cursor.fetchone() is not None


def _replace_0046_line_note_without_command(
    session: Session,
    *,
    request_id: uuid.UUID,
    replacement_note: str,
) -> tuple[uuid.UUID, str]:
    column_list = ", ".join(_MATERIAL_REQUEST_LINE_COLUMNS_0046)
    returned = session.execute(
        text(
            f"DELETE FROM public.material_request_lines "
            f"WHERE id = ("
            f"SELECT id FROM public.material_request_lines "
            f"WHERE request_id = :request_id ORDER BY line_no LIMIT 1"
            f") RETURNING {column_list}"
        ),
        {"request_id": request_id},
    ).mappings().one()
    original_note = str(returned["note"])
    replacement = dict(returned)
    replacement["note"] = replacement_note
    bind_list = ", ".join(f":{column}" for column in _MATERIAL_REQUEST_LINE_COLUMNS_0046)
    session.execute(
        text(
            f"INSERT INTO public.material_request_lines ({column_list}) "
            f"VALUES ({bind_list})"
        ),
        replacement,
    )
    return returned["id"], original_note


def _assert_0046_line_drift_is_rejected(
    api_engine,
    *,
    request_id: uuid.UUID,
    replica_mode: bool,
) -> None:
    test_engine = api_engine
    replica_engine = None
    if replica_mode:
        # The runtime role itself must not be able to disable ordinary
        # triggers or constraints through the cluster parameter.
        with Session(api_engine) as api_session:
            assert api_session.execute(text("SELECT current_user")).scalar_one() == (
                "star_oam_api"
            )
            with pytest.raises(DBAPIError) as parameter_failure:
                api_session.execute(
                    text("SET LOCAL session_replication_role = 'replica'")
                )
            assert isinstance(
                parameter_failure.value.orig,
                psycopg.errors.InsufficientPrivilege,
            )
            api_session.rollback()

        # Only the disposable cluster superuser may select replica mode.  The
        # API and migration roles deliberately lack this parameter privilege;
        # use the reviewed bootstrap identity only to select it, then assume
        # the migration role before proving that 0046's ENABLE ALWAYS triggers
        # still reject drift.
        replica_engine = create_engine(
            _admin_sqlalchemy_url(),
            pool_size=1,
            max_overflow=0,
            pool_timeout=5,
        )
        test_engine = replica_engine
    try:
        with Session(test_engine) as session:
            if replica_mode:
                session.execute(
                    text("SET LOCAL session_replication_role = 'replica'")
                )
                session.execute(text("SET LOCAL ROLE star_oam_migrator"))
                assert session.execute(
                    text(
                        "SELECT current_user, session_user, "
                        "current_setting('session_replication_role')"
                    )
                ).one() == ("star_oam_migrator", "postgres", "replica")
            line_id, original_note = _replace_0046_line_note_without_command(
                session,
                request_id=request_id,
                replacement_note=(
                    "0046 replica projection drift"
                    if replica_mode
                    else "0046 commandless projection drift"
                ),
            )
            with pytest.raises(DBAPIError) as failure:
                session.commit()
            assert "formal material request content projection is invalid" in str(
                failure.value
            )
            session.rollback()
    finally:
        if replica_engine is not None:
            replica_engine.dispose()

    with Session(api_engine) as session:
        durable_line = session.execute(
            text(
                "SELECT note FROM public.material_request_lines "
                "WHERE id = :line_id"
            ),
            {"line_id": line_id},
        ).one()
        assert durable_line == (original_note,)


def _create_0046_available_request_file(
    api_engine,
    *,
    uploader_user_id: str,
    marker: str,
) -> uuid.UUID:
    from app.formal_services import formal_files
    from app.foundation_models import FileObject
    from app.models import User

    file_id = uuid.uuid4()
    filename = f"{marker}.png"
    storage_key = (
        "formal-files/v1/request_attachment/"
        f"{file_id.hex[:2]}/{file_id.hex}"
    )
    file_sha256 = hashlib.sha256(f"0046-file:{marker}".encode()).hexdigest()
    prepared = formal_files._PreparedUpload(
        purpose="request_attachment",
        original_filename=filename,
        size_bytes=128,
        mime_type="image/png",
        sha256=file_sha256,
    )
    with Session(api_engine) as session:
        uploader = session.get(User, uploader_user_id)
        assert uploader is not None and uploader.person_id is not None
        created_at = session.scalar(select(func.transaction_timestamp()))
        assert created_at is not None
        metadata = {
            "authorization_version": uploader.authorization_version,
            "file_id": str(file_id),
            "idempotency_key_hash": hashlib.sha256(
                f"0046-file-key:{marker}".encode()
            ).hexdigest(),
            "provider": "aliyun_oss_v2",
            "purpose": "request_attachment",
            "request_sha256": formal_files._upload_request_hash(prepared),
            "schema": "cloud_oam.formal_file_upload_intent.v1",
            "storage_key": storage_key,
            "uploader_person_id": str(uploader.person_id),
            "uploader_user_id": uploader.id,
        }
        row = FileObject(
            id=file_id,
            storage_key=storage_key,
            sha256=file_sha256,
            size_bytes=prepared.size_bytes,
            mime_type=prepared.mime_type,
            original_filename=prepared.original_filename,
            uploaded_by=uploader.id,
            status="pending",
            metadata_jsonb=metadata,
            created_at=created_at,
        )
        session.add(row)
        session.flush()
        row.status = "available"
        row.metadata_jsonb = {
            **metadata,
            "completion": {
                "etag_sha256": hashlib.sha256(
                    f"0046-etag:{marker}".encode()
                ).hexdigest(),
                "head_manifest_sha256": hashlib.sha256(
                    f"0046-head:{marker}".encode()
                ).hexdigest(),
                "verified_at": created_at.isoformat(),
            },
        }
        session.flush()
        session.commit()
    return file_id


def _assert_0046_attachment_drift_is_rejected(
    api_engine,
    *,
    request_id: uuid.UUID,
    requester_user_id: str,
    requester_file_id: uuid.UUID,
    other_file_id: uuid.UUID,
) -> None:
    from app.demand_models import MaterialRequestFile

    with Session(api_engine) as session:
        origin = session.execute(
            text(
                "SELECT request_jsonb->>'revision_id', "
                "(request_jsonb->>'revision_no')::integer, occurred_at "
                "FROM public.material_request_commands "
                "WHERE request_id = :request_id "
                "AND operation IN ('create', 'update_draft') "
                "ORDER BY target_version DESC LIMIT 1"
            ),
            {"request_id": request_id},
        ).one()
        binding_id = session.scalar(
            select(MaterialRequestFile.id).where(
                MaterialRequestFile.request_id == request_id
            )
        )
        assert binding_id is not None
    revision_id = uuid.UUID(origin[0])
    revision_no = origin[1]
    origin_time = origin[2]

    inserted_binding_id = uuid.uuid4()
    with Session(api_engine) as session:
        with pytest.raises(DBAPIError) as add_failure:
            session.execute(
                text(
                    "INSERT INTO public.material_request_files ("
                    "id, request_id, revision_id, revision_no, request_line_id, "
                    "file_id, purpose, created_by_user_id, created_at"
                    ") VALUES ("
                    ":id, :request_id, :revision_id, :revision_no, NULL, "
                    ":file_id, 'request_attachment', :created_by, :created_at"
                    ")"
                ),
                {
                    "id": inserted_binding_id,
                    "request_id": request_id,
                    "revision_id": revision_id,
                    "revision_no": revision_no,
                    "file_id": requester_file_id,
                    "created_by": requester_user_id,
                    "created_at": origin_time,
                },
            )
            session.commit()
        assert "formal material request content projection is invalid" in str(
            add_failure.value
        )
        session.rollback()

    with Session(api_engine) as session:
        with pytest.raises(DBAPIError) as delete_failure:
            deleted = session.execute(
                text(
                    "DELETE FROM public.material_request_files "
                    "WHERE id = :binding_id"
                ),
                {"binding_id": binding_id},
            )
            assert deleted.rowcount == 1
            session.commit()
        assert "formal material request content projection is invalid" in str(
            delete_failure.value
        )
        session.rollback()

    cross_binding_id = uuid.uuid4()
    with Session(api_engine) as session:
        with pytest.raises(DBAPIError) as cross_failure:
            session.execute(
                text(
                    "INSERT INTO public.material_request_files ("
                    "id, request_id, revision_id, revision_no, request_line_id, "
                    "file_id, purpose, created_by_user_id, created_at"
                    ") VALUES ("
                    ":id, :request_id, :revision_id, :revision_no, NULL, "
                    ":file_id, 'request_attachment', :created_by, :created_at"
                    ")"
                ),
                {
                    "id": cross_binding_id,
                    "request_id": request_id,
                    "revision_id": revision_id,
                    "revision_no": revision_no,
                    "file_id": other_file_id,
                    "created_by": requester_user_id,
                    "created_at": origin_time,
                },
            )
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert "formal material request content projection is invalid" in str(
            cross_failure.value
        )
        session.rollback()

    with Session(api_engine) as session:
        assert session.get(MaterialRequestFile, binding_id) is not None
        assert session.get(MaterialRequestFile, inserted_binding_id) is None
        assert session.get(MaterialRequestFile, cross_binding_id) is None


def _assert_0046_prefilled_manifest_is_rejected(
    api_engine,
    *,
    request_id: uuid.UUID,
) -> None:
    forged_command_id = uuid.uuid4()
    with Session(api_engine) as session:
        with pytest.raises(DBAPIError) as prefilled_failure:
            session.execute(
                text(
                    "INSERT INTO public.material_request_commands ("
                    "id, operation, request_id, target_version, "
                    "idempotency_key_hash, request_reference, request_hash, "
                    "result_hash, projection_manifest_sha256, request_jsonb, "
                    "result_jsonb, actor_user_id, actor_person_id, "
                    "actor_role_assignment_id, authorization_version, "
                    "occurred_at, created_at"
                    ") SELECT :forged_id, 'update_draft', request_id, "
                    "target_version, :key_hash, request_reference, request_hash, "
                    "result_hash, :manifest, request_jsonb, result_jsonb, "
                    "actor_user_id, actor_person_id, actor_role_assignment_id, "
                    "authorization_version, occurred_at, created_at "
                    "FROM public.material_request_commands "
                    "WHERE request_id = :request_id AND operation = 'create'"
                ),
                {
                    "forged_id": forged_command_id,
                    "key_hash": hashlib.sha256(
                        f"0046-prefill:{request_id}".encode()
                    ).hexdigest(),
                    "manifest": "a" * 64,
                    "request_id": request_id,
                },
            )
            session.execute(text("SET CONSTRAINTS ALL IMMEDIATE"))
        assert "formal material request content projection is invalid" in str(
            prefilled_failure.value
        )
        session.rollback()
    with Session(api_engine) as session:
        assert session.execute(
            text(
                "SELECT count(*) FROM public.material_request_commands "
                "WHERE id = :command_id"
            ),
            {"command_id": forged_command_id},
        ).scalar_one() == 0


def _assert_0046_json_null_command_fields_are_rejected(
    api_engine,
    *,
    request_id: uuid.UUID,
) -> None:
    mutations = {
        "request-schema": (
            "jsonb_set(request_jsonb, '{schema}', 'null'::jsonb, false)",
            "result_jsonb",
        ),
        "request-payload-sha256": (
            "jsonb_set(request_jsonb, '{payload_sha256}', "
            "'null'::jsonb, false)",
            "result_jsonb",
        ),
        "result-kind": (
            "request_jsonb",
            "jsonb_set(result_jsonb, '{kind}', 'null'::jsonb, false)",
        ),
    }
    rejected_ids: list[uuid.UUID] = []
    for marker, (request_document, result_document) in mutations.items():
        forged_command_id = uuid.uuid4()
        rejected_ids.append(forged_command_id)
        with Session(api_engine) as session:
            with pytest.raises(DBAPIError) as null_failure:
                session.execute(
                    text(
                        "INSERT INTO public.material_request_commands ("
                        "id, operation, request_id, target_version, "
                        "idempotency_key_hash, request_reference, request_hash, "
                        "result_hash, request_jsonb, result_jsonb, "
                        "actor_user_id, actor_person_id, "
                        "actor_role_assignment_id, authorization_version, "
                        "occurred_at, created_at"
                        ") SELECT :forged_id, operation, request_id, "
                        "target_version, :key_hash, request_reference, "
                        f"request_hash, result_hash, {request_document}, "
                        f"{result_document}, actor_user_id, actor_person_id, "
                        "actor_role_assignment_id, authorization_version, "
                        "occurred_at, created_at "
                        "FROM public.material_request_commands "
                        "WHERE request_id = :request_id "
                        "AND operation = 'create'"
                    ),
                    {
                        "forged_id": forged_command_id,
                        "key_hash": hashlib.sha256(
                            f"0046-json-null:{marker}:{request_id}".encode()
                        ).hexdigest(),
                        "request_id": request_id,
                    },
                )
            assert "formal material request content projection is invalid" in str(
                null_failure.value
            )
            session.rollback()
    with Session(api_engine) as session:
        assert session.execute(
            text(
                "SELECT count(*) FROM public.material_request_commands "
                "WHERE id = ANY(CAST(:command_ids AS uuid[]))"
            ),
            {"command_ids": rejected_ids},
        ).scalar_one() == 0


def _assert_0046_approval_attempt_mutations_are_rejected(
    api_engine,
    *,
    request_id: uuid.UUID,
    source_operation: str,
) -> None:
    if source_operation in {"create", "update_draft"}:
        mutations = {
            "draft-non-null": (
                "jsonb_set(request_jsonb, '{approval_attempt_no}', "
                "'1'::jsonb, false)",
                "result_jsonb",
            ),
        }
    else:
        assert source_operation == "submit"
        mutations = {
            "submit-null": (
                "jsonb_set(request_jsonb, '{approval_attempt_no}', "
                "'null'::jsonb, false)",
                "jsonb_set(result_jsonb, '{approval_attempt_no}', "
                "'null'::jsonb, false)",
            ),
            "submit-non-positive": (
                "jsonb_set(request_jsonb, '{approval_attempt_no}', "
                "'0'::jsonb, false)",
                "jsonb_set(result_jsonb, '{approval_attempt_no}', "
                "'0'::jsonb, false)",
            ),
            "submit-mismatch": (
                "jsonb_set(request_jsonb, '{approval_attempt_no}', "
                "'1'::jsonb, false)",
                "jsonb_set(result_jsonb, '{approval_attempt_no}', "
                "'2'::jsonb, false)",
            ),
            "submit-consistent-but-not-real": (
                "jsonb_set(request_jsonb, '{approval_attempt_no}', "
                "'2'::jsonb, false)",
                "jsonb_set(result_jsonb, '{approval_attempt_no}', "
                "'2'::jsonb, false)",
            ),
            "submit-random-instance": (
                "request_jsonb",
                "jsonb_set(result_jsonb, '{approval_instance_id}', "
                "to_jsonb(CAST(:forged_instance_id AS text)), false)",
            ),
        }

    rejected_ids: list[uuid.UUID] = []
    for marker, (request_document, result_document) in mutations.items():
        forged_command_id = uuid.uuid4()
        rejected_ids.append(forged_command_id)
        statement_parameters = {
            "forged_id": forged_command_id,
            "key_hash": hashlib.sha256(
                f"0046-attempt:{marker}:{request_id}".encode()
            ).hexdigest(),
            "request_id": request_id,
            "source_operation": source_operation,
        }
        if marker == "submit-random-instance":
            statement_parameters["forged_instance_id"] = uuid.uuid4()
        with Session(api_engine) as session:
            with pytest.raises(DBAPIError) as attempt_failure:
                session.execute(
                    text(
                        "INSERT INTO public.material_request_commands ("
                        "id, operation, request_id, target_version, "
                        "idempotency_key_hash, request_reference, request_hash, "
                        "result_hash, request_jsonb, result_jsonb, "
                        "actor_user_id, actor_person_id, "
                        "actor_role_assignment_id, authorization_version, "
                        "occurred_at, created_at"
                        ") SELECT :forged_id, operation, request_id, "
                        "target_version, :key_hash, request_reference, "
                        f"request_hash, result_hash, {request_document}, "
                        f"{result_document}, actor_user_id, actor_person_id, "
                        "actor_role_assignment_id, authorization_version, "
                        "occurred_at, created_at "
                        "FROM public.material_request_commands "
                        "WHERE request_id = :request_id "
                        "AND operation = :source_operation"
                    ),
                    statement_parameters,
                )
            assert "formal material request content projection is invalid" in str(
                attempt_failure.value
            )
            session.rollback()
    with Session(api_engine) as session:
        assert session.execute(
            text(
                "SELECT count(*) FROM public.material_request_commands "
                "WHERE id = ANY(CAST(:command_ids AS uuid[]))"
            ),
            {"command_ids": rejected_ids},
        ).scalar_one() == 0


def _assert_0045_raw_projection_bypass_and_formal_approval(
    api_engine,
) -> tuple[uuid.UUID, int, str, str]:
    from app.demand_models import (
        ApprovalExternalRegistration,
        ApprovalInstance,
        ApprovalStep,
        ApprovalStepLineDecision,
        MaterialRequest,
        MaterialRequestFile,
        MaterialRequestLine,
    )
    from app.formal_services.material_request_draft import (
        amend_material_request_draft,
        create_material_request_draft,
        submit_material_request,
    )
    from app.formal_services.material_request_policy import ApprovalLineDecision
    from test_material_request_approval_service import (
        _approve,
        _evidence,
        _principal,
        _register_external,
        _verify_external,
    )
    from test_material_request_draft_service import SECRET

    (
        request_id,
        draft,
        requester_user_id,
        manager_user_id,
        registering_admin_user_id,
        verifying_admin_user_id,
    ) = _seed_material_request_approval_world()
    assert registering_admin_user_id != verifying_admin_user_id
    manifest_date = datetime(2026, 9, 15, tzinfo=timezone.utc).date()
    draft = replace(
        draft,
        expected_date=manifest_date,
        lines=tuple(
            replace(line, required_date=manifest_date) for line in draft.lines
        ),
    )

    # Draft creation is one committed formal action.
    with Session(api_engine) as session:
        created = _reveal_pg16_service_database_error(
            api_engine,
            lambda: create_material_request_draft(
                session,
                actor=_principal(session, requester_user_id),
                material_request_id=request_id,
                draft=draft,
                idempotency_key="pg16-approval-projection-create",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-pg16-approval-projection-create",
            )
        )
        session.commit()
    with Session(api_engine) as session:
        bindings = tuple(
            session.scalars(
                select(MaterialRequestFile)
                .where(MaterialRequestFile.request_id == request_id)
                .order_by(MaterialRequestFile.id)
            ).all()
        )
        assert len(bindings) == 1
        assert bindings[0].revision_id == created.revision_id
        assert bindings[0].revision_no == created.revision_no
        assert bindings[0].file_id == draft.attachment_file_ids[0]
        assert bindings[0].purpose == "request_attachment"
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="draft",
        expected_version=created.request_version,
        expected_decided_at=False,
    )
    created_commands = _assert_0046_content_command_chain(
        api_engine,
        request_id=request_id,
        expected_content_operations=("create",),
    )
    _assert_0046_datestyle_validator_stability(request_id=request_id)
    with Session(api_engine) as session:
        replayed_create = _reveal_pg16_service_database_error(
            api_engine,
            lambda: create_material_request_draft(
                session,
                actor=_principal(session, requester_user_id),
                material_request_id=request_id,
                draft=draft,
                idempotency_key="pg16-approval-projection-create",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-pg16-approval-projection-create-replay",
            )
        )
        assert replayed_create.idempotency_replayed is True
        assert replayed_create.request_version == created.request_version
        session.commit()
    assert _assert_0046_content_command_chain(
        api_engine,
        request_id=request_id,
        expected_content_operations=("create",),
    ) == created_commands
    _assert_0046_prefilled_manifest_is_rejected(
        api_engine,
        request_id=request_id,
    )
    _assert_0046_json_null_command_fields_are_rejected(
        api_engine,
        request_id=request_id,
    )
    _assert_0046_approval_attempt_mutations_are_rejected(
        api_engine,
        request_id=request_id,
        source_operation="create",
    )

    # Prepare two valid files before the update anchor.  The first can reach
    # the 0046 deferred digest check; the second proves the immediate guard
    # rejects a formally valid file owned by a different applicant.
    requester_spare_file_id = _create_0046_available_request_file(
        api_engine,
        uploader_user_id=requester_user_id,
        marker=f"requester-{request_id.hex[:12]}",
    )
    other_spare_file_id = _create_0046_available_request_file(
        api_engine,
        uploader_user_id=manager_user_id,
        marker=f"other-{request_id.hex[:12]}",
    )

    api_parameters = _connection_parameters(
        role="star_oam_api",
        password=_role_password("star_oam_api"),
    )
    bypass_time = datetime.now(timezone.utc)
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg.Error) as direct_failure:
                cursor.execute(
                    "UPDATE public.material_requests "
                    "SET status = 'approved', submitted_at = %s, "
                    "decided_at = %s, version = version + 1, updated_at = %s "
                    "WHERE id = %s",
                    (bypass_time, bypass_time, bypass_time, request_id),
                )
            assert "formal material request approval projection is invalid" in str(
                direct_failure.value
            )
        connection.rollback()
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="draft",
        expected_version=created.request_version,
        expected_decided_at=False,
    )

    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.material_requests "
                "SET version = version + 1, updated_at = %s "
                "WHERE id = %s AND status = 'draft'",
                (datetime.now(timezone.utc), request_id),
            )
            assert cursor.rowcount == 1
            with pytest.raises(psycopg.Error) as version_failure:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            assert "formal material request approval projection is invalid" in str(
                version_failure.value
            )
        connection.rollback()
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="draft",
        expected_version=created.request_version,
        expected_decided_at=False,
    )

    # Even a legitimate command cannot authorize content written afterwards
    # in the same transaction.  The deferred validator must reject the final
    # post-image and roll back the command, aggregate and replacement line.
    doomed_draft = replace(draft, note="0046 doomed update before line drift")
    with Session(api_engine) as session:
        doomed = _reveal_pg16_service_database_error(
            api_engine,
            lambda: amend_material_request_draft(
                session,
                actor=_principal(session, requester_user_id),
                material_request_id=request_id,
                expected_version=created.request_version,
                draft=doomed_draft,
                idempotency_key="pg16-approval-projection-doomed-update",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-pg16-approval-projection-doomed-update",
            )
        )
        assert doomed.version == created.request_version + 1
        _replace_0046_line_note_without_command(
            session,
            request_id=request_id,
            replacement_note="0046 drift after valid update command",
        )
        with pytest.raises(DBAPIError) as post_command_drift_failure:
            session.commit()
        assert "formal material request content projection is invalid" in str(
            post_command_drift_failure.value
        )
        session.rollback()
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="draft",
        expected_version=created.request_version,
        expected_decided_at=False,
    )
    assert _assert_0046_content_command_chain(
        api_engine,
        request_id=request_id,
        expected_content_operations=("create",),
    ) == created_commands

    # Commit one real draft update so create, update and submit have separate
    # immutable anchors.  A replay may return the original result but must not
    # create a fourth command or replace the database-owned digest.
    amended_draft = replace(draft, note="0046 committed draft update")
    with Session(api_engine) as session:
        amended = _reveal_pg16_service_database_error(
            api_engine,
            lambda: amend_material_request_draft(
                session,
                actor=_principal(session, requester_user_id),
                material_request_id=request_id,
                expected_version=created.request_version,
                draft=amended_draft,
                idempotency_key="pg16-approval-projection-update",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-pg16-approval-projection-update",
            )
        )
        # Keep psycopg's ORM reads on its required ISO DateStyle, then switch
        # only for commit so the deferred 0046 validator recomputes the digest
        # under SQL, DMY and proves that it matches the ISO-created manifest.
        session.execute(text("SET LOCAL DateStyle = 'SQL, DMY'"))
        assert session.execute(text("SHOW DateStyle")).scalar_one() == "SQL, DMY"
        session.commit()
    amended_commands = _assert_0046_content_command_chain(
        api_engine,
        request_id=request_id,
        expected_content_operations=("create", "update_draft"),
    )
    assert [row[1] for row in amended_commands] == [0, 1]
    assert amended_commands[0][2] != amended_commands[1][2]
    _assert_0046_datestyle_validator_stability(request_id=request_id)
    _assert_0046_approval_attempt_mutations_are_rejected(
        api_engine,
        request_id=request_id,
        source_operation="update_draft",
    )
    with Session(api_engine) as session:
        replayed_amend = _reveal_pg16_service_database_error(
            api_engine,
            lambda: amend_material_request_draft(
                session,
                actor=_principal(session, requester_user_id),
                material_request_id=request_id,
                expected_version=created.request_version,
                draft=amended_draft,
                idempotency_key="pg16-approval-projection-update",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-pg16-approval-projection-update-replay",
            )
        )
        assert replayed_amend.replayed is True
        assert replayed_amend.version == amended.version
        session.commit()
    assert _assert_0046_content_command_chain(
        api_engine,
        request_id=request_id,
        expected_content_operations=("create", "update_draft"),
    ) == amended_commands

    _assert_0046_line_drift_is_rejected(
        api_engine,
        request_id=request_id,
        replica_mode=False,
    )
    _assert_0046_attachment_drift_is_rejected(
        api_engine,
        request_id=request_id,
        requester_user_id=requester_user_id,
        requester_file_id=requester_spare_file_id,
        other_file_id=other_spare_file_id,
    )
    _assert_0046_line_drift_is_rejected(
        api_engine,
        request_id=request_id,
        replica_mode=True,
    )
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="draft",
        expected_version=amended.version,
        expected_decided_at=False,
    )

    # Submission is independently committed and creates the sealed revision,
    # active instance, frozen candidates and three approval steps atomically.
    with Session(api_engine) as session:
        submitted = _reveal_pg16_service_database_error(
            api_engine,
            lambda: submit_material_request(
                session,
                actor=_principal(session, requester_user_id),
                material_request_id=request_id,
                expected_version=amended.version,
                idempotency_key="pg16-approval-projection-submit",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-pg16-approval-projection-submit",
            )
        )
        # As above, exercise the deferred database validator under SQL, DMY
        # without asking psycopg to decode non-ISO timestamptz ORM results.
        session.execute(text("SET LOCAL DateStyle = 'SQL, DMY'"))
        assert session.execute(text("SHOW DateStyle")).scalar_one() == "SQL, DMY"
        session.commit()
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="approval_in_progress",
        expected_version=submitted.version,
        expected_decided_at=False,
    )
    submitted_commands = _assert_0046_content_command_chain(
        api_engine,
        request_id=request_id,
        expected_content_operations=("create", "update_draft", "submit"),
    )
    assert [row[1] for row in submitted_commands] == [0, 1, 2]
    assert submitted_commands[1][2] == submitted_commands[2][2]
    with Session(api_engine) as session:
        replayed_submit = _reveal_pg16_service_database_error(
            api_engine,
            lambda: submit_material_request(
                session,
                actor=_principal(session, requester_user_id),
                material_request_id=request_id,
                expected_version=amended.version,
                idempotency_key="pg16-approval-projection-submit",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-pg16-approval-projection-submit-replay",
            )
        )
        assert replayed_submit.replayed is True
        assert replayed_submit.version == submitted.version
        session.commit()
    assert _assert_0046_content_command_chain(
        api_engine,
        request_id=request_id,
        expected_content_operations=("create", "update_draft", "submit"),
    ) == submitted_commands
    _assert_0046_approval_attempt_mutations_are_rejected(
        api_engine,
        request_id=request_id,
        source_operation="submit",
    )
    _assert_0045_version_only_projection_drift_is_rejected(
        api_engine,
        request_id=request_id,
        expected_request_version=submitted.version,
    )

    # A withdrawn instance plus cancelled open step is still only a mutable
    # projection.  Without the requester's immutable command/action and the
    # matching state/audit evidence, the complete forged graph must roll back.
    bypass_time = datetime.now(timezone.utc)
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.approval_steps AS step "
                "SET status = 'cancelled', version = step.version + 1, "
                "updated_at = %s "
                "FROM public.approval_instances AS instance "
                "WHERE instance.request_id = %s "
                "AND instance.current_step_id = step.id",
                (bypass_time, request_id),
            )
            assert cursor.rowcount == 1
            cursor.execute(
                "UPDATE public.approval_instances "
                "SET status = 'withdrawn', current_step_no = NULL, "
                "current_step_id = NULL, completed_at = %s, "
                "version = version + 1, updated_at = %s "
                "WHERE request_id = %s AND status = 'active'",
                (bypass_time, bypass_time, request_id),
            )
            assert cursor.rowcount == 1
            cursor.execute(
                "UPDATE public.material_requests "
                "SET status = 'withdrawn', withdrawn_at = %s, "
                "version = version + 1, updated_at = %s "
                "WHERE id = %s AND status = 'approval_in_progress'",
                (bypass_time, bypass_time, request_id),
            )
            assert cursor.rowcount == 1
            with pytest.raises(psycopg.Error) as withdrawal_failure:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            assert "formal material request approval projection is invalid" in str(
                withdrawal_failure.value
            )
        connection.rollback()
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="approval_in_progress",
        expected_version=submitted.version,
        expected_decided_at=False,
    )
    _assert_0045_formal_withdrawal(
        api_engine,
        template_draft=draft,
        requester_user_id=requester_user_id,
    )
    _assert_0045_region_return_then_cancel(
        api_engine,
        template_draft=draft,
        requester_user_id=requester_user_id,
        manager_user_id=manager_user_id,
    )
    _assert_0029_decision_guard_rejects_cross_request_line(
        api_engine,
        request_id=request_id,
    )

    # approval_in_progress -> approved is a legal header transition, so the
    # statement itself succeeds.  The deferred 0045 graph validator must still
    # reject the forged projection when constraints are forced (and at commit).
    bypass_time = datetime.now(timezone.utc)
    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE public.material_requests "
                "SET status = 'approved', decided_at = %s, "
                "version = version + 1, updated_at = %s "
                "WHERE id = %s AND status = 'approval_in_progress'",
                (bypass_time, bypass_time, request_id),
            )
            assert cursor.rowcount == 1
            cursor.execute(
                "SELECT status FROM public.material_requests WHERE id = %s",
                (request_id,),
            )
            assert cursor.fetchone() == ("approved",)
            with pytest.raises(psycopg.Error) as deferred_failure:
                cursor.execute("SET CONSTRAINTS ALL IMMEDIATE")
            assert "formal material request approval projection is invalid" in str(
                deferred_failure.value
            )
        connection.rollback()
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="approval_in_progress",
        expected_version=submitted.version,
        expected_decided_at=False,
    )

    # Every formal approval command below owns a separate database transaction.
    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        line = session.scalar(
            select(MaterialRequestLine).where(
                MaterialRequestLine.request_id == request_id,
                MaterialRequestLine.revision_no == submitted.revision_no,
            )
        )
        assert request is not None and line is not None
        _, regional = _reveal_pg16_service_database_error(
            api_engine,
            lambda: _approve(
                session,
                actor=_principal(session, manager_user_id),
                request=request,
                request_version=submitted.version,
                quantities={line.id: line.requested_qty},
                key="pg16-approval-projection-region",
            )
        )
        line_id = line.id
        approved_qty = line.requested_qty
        session.commit()

    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        assert request is not None
        _, headquarters = _approve(
            session,
            actor=_principal(session, registering_admin_user_id),
            request=request,
            request_version=regional.request_version,
            quantities={line_id: approved_qty},
            key="pg16-approval-projection-headquarters",
        )
        session.commit()

    _assert_0045_external_pending_without_registration_is_rejected(
        api_engine,
        request_id=request_id,
        expected_request_version=headquarters.request_version,
    )
    _assert_0045_register_pair_without_registration_is_rejected(
        api_engine,
        request_id=request_id,
        expected_request_version=headquarters.request_version,
        registering_admin_user_id=registering_admin_user_id,
    )
    _assert_0045_verify_pair_without_verified_registration_is_rejected(
        api_engine,
        request_id=request_id,
        expected_request_version=headquarters.request_version,
        verifying_admin_user_id=verifying_admin_user_id,
    )

    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        assert request is not None
        evidence = _evidence(
            session,
            uploaded_by=registering_admin_user_id,
            marker="pg16-approval-projection-evidence",
        )
        step3, registration = _register_external(
            session,
            actor=_principal(session, registering_admin_user_id),
            request=request,
            request_version=headquarters.request_version,
            evidence=evidence,
            key="pg16-approval-projection-register",
            action="approve",
            lines=(ApprovalLineDecision(line_id, approved_qty, "同意"),),
        )
        step3_id = step3.id
        session.commit()

    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        step3 = session.get(ApprovalStep, step3_id)
        assert request is not None and step3 is not None
        verified = _verify_external(
            session,
            actor=_principal(session, verifying_admin_user_id),
            request=request,
            step=step3,
            registration_id=registration.registration_id,
            request_version=registration.request_version,
            step_version=registration.step_version,
            key="pg16-approval-projection-verify",
        )
        session.commit()

    assert verified.request_status == "approved"
    assert verified.instance_status == "completed"
    assert dict(verified.state_axes) == MATERIAL_REQUEST_NEUTRAL_AXES
    with Session(api_engine) as session:
        request = session.get(MaterialRequest, request_id)
        line = session.get(MaterialRequestLine, line_id)
        registration_row = session.get(
            ApprovalExternalRegistration,
            registration.registration_id,
        )
        instance = session.scalar(
            select(ApprovalInstance).where(
                ApprovalInstance.request_id == request_id
            )
        )
        assert request is not None and line is not None
        assert registration_row is not None and instance is not None
        assert request.status == "approved"
        assert {
            field: getattr(request, field)
            for field in MATERIAL_REQUEST_NEUTRAL_AXES
        } == MATERIAL_REQUEST_NEUTRAL_AXES
        assert line.status == "approved"
        assert line.final_approved_qty == approved_qty
        assert line.cancelled_qty == 0
        assert instance.status == "completed"
        assert instance.current_step_no is None
        assert instance.current_step_id is None
        assert registration_row.status == "accepted"
        assert registration_row.registered_by_user_id == registering_admin_user_id
        assert registration_row.verified_by_user_id == verifying_admin_user_id
        assert session.scalar(
            select(func.count()).select_from(ApprovalStepLineDecision).where(
                ApprovalStepLineDecision.step_id.in_(
                    select(ApprovalStep.id).where(
                        ApprovalStep.instance_id == instance.id
                    )
                )
            )
        ) == 3

    with psycopg.connect(**api_parameters) as connection:
        with connection.cursor() as cursor:
            with pytest.raises(psycopg.Error) as pending_failure:
                cursor.execute(
                    "UPDATE public.material_requests "
                    "SET status = 'cancellation_pending', "
                    "version = version + 1, updated_at = %s "
                    "WHERE id = %s AND status = 'approved'",
                    (datetime.now(timezone.utc), request_id),
                )
            assert "formal material request approval projection is invalid" in str(
                pending_failure.value
            )
        connection.rollback()
    cancelled_version = _assert_0045_formal_approved_cancel(
        api_engine,
        request_id=request_id,
        request_version=verified.request_version,
        request_line_id=line_id,
        approved_qty=approved_qty,
        requester_user_id=requester_user_id,
    )
    assert _assert_0046_content_command_chain(
        api_engine,
        request_id=request_id,
        expected_content_operations=("create", "update_draft", "submit"),
    ) == submitted_commands
    return (
        request_id,
        cancelled_version,
        manager_user_id,
        registering_admin_user_id,
    )


def _assert_0046_rejects_nonempty_content_downgrade(
    api_engine,
    *,
    request_id: uuid.UUID,
    expected_version: int,
) -> None:
    assert _current_revision() == HEAD_REVISION
    blocked = _run_alembic("downgrade", RLS_REVISION, expect_success=False)
    assert "cannot downgrade 0046" in (blocked.stdout + blocked.stderr)
    assert _current_revision() == HEAD_REVISION
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="cancelled",
        expected_version=expected_version,
        expected_decided_at=True,
    )


def _seed_0047_stocktake_inventory(
    api_engine,
    *,
    actor_user_id: str,
    assignee_user_id: str,
) -> dict[str, object]:
    """Establish the scope, then seed stock through the real posting service."""

    from unittest.mock import patch

    from app.foundation_models import (
        ExternalObject,
        ExternalObjectVersion,
        Organization,
        Person,
        Role,
        RoleAssignment,
        SourceSystem,
        SyncBatch,
        SyncInboxEvent,
        SyncRun,
    )
    from app.formal_services.opening_stocktake import (
        OPENING_CONTROL_ENTITY_TYPE,
        OpeningControlLineInput,
        canonical_opening_manifest_sha256,
        opening_control_batch_body_sha256,
        opening_control_manifest_sha256,
        opening_control_projection_payload,
    )
    from app.inventory_models import (
        CustodyAssignment,
        FormalMaterial,
        InventorySerial,
        MaterialInventoryPolicy,
        StockAccount,
        StockLocation,
    )
    from app.models import User

    migrator_engine = create_engine(
        _sqlalchemy_url(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        ),
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    try:
        with Session(migrator_engine, expire_on_commit=False) as session:
            now = session.scalar(select(func.now()))
            assert isinstance(now, datetime) and now.tzinfo is not None
            manager = session.get(User, assignee_user_id)
            assert manager is not None and manager.person_id is not None
            manager_person = session.get(Person, manager.person_id)
            assert manager_person is not None
            assignment_row = session.execute(
                select(RoleAssignment, Role)
                .join(Role, Role.id == RoleAssignment.role_id)
                .where(
                    RoleAssignment.user_id == assignee_user_id,
                    RoleAssignment.status == "active",
                    RoleAssignment.scope_type == "organization",
                    Role.code == "provincial_manager",
                    Role.status == "active",
                )
                .order_by(RoleAssignment.id)
            ).one()
            assignment, role = assignment_row
            assert role.is_external is False
            region_org_id = uuid.UUID(assignment.scope_id)
            region = session.get(Organization, region_org_id)
            assert region is not None
            assert (region.org_type, region.status) == ("region_company", "active")

            administrator = session.get(User, actor_user_id)
            assert (
                administrator is not None
                and administrator.person_id is not None
            )
            assert session.get(Person, administrator.person_id) is not None
            headquarters_assignment_row = session.execute(
                select(RoleAssignment, Role)
                .join(Role, Role.id == RoleAssignment.role_id)
                .where(
                    RoleAssignment.user_id == actor_user_id,
                    RoleAssignment.status == "active",
                    RoleAssignment.scope_type == "national",
                    RoleAssignment.scope_id == "*",
                    Role.code == "admin",
                    Role.status == "active",
                )
                .order_by(RoleAssignment.id)
            ).one()
            _headquarters_assignment, headquarters_role = (
                headquarters_assignment_row
            )
            assert headquarters_role.is_external is False

            oam_source = session.scalar(
                select(SourceSystem)
                .where(func.lower(SourceSystem.code) == "oam")
                .order_by(SourceSystem.id)
                .limit(1)
            )
            if oam_source is None:
                oam_source = SourceSystem(
                    id=uuid.uuid4(),
                    code="OAM",
                    name="PostgreSQL 16 隔离期初控制源",
                    mode="read_only",
                    enabled=True,
                    configuration_jsonb={},
                    created_at=now - timedelta(days=2),
                    updated_at=now - timedelta(days=2),
                )
                session.add(oam_source)
                session.flush()
            assert (
                oam_source.code.casefold(),
                oam_source.mode,
                oam_source.enabled,
            ) == ("oam", "read_only", True)

            material_created_at = now - timedelta(days=1)
            material_id = uuid.uuid4()
            material_sku_code = (
                f"PG16-STK-SKU-{material_id.hex[:16].upper()}"
            )
            material_external_version_id = uuid.uuid4()
            material_payload = {
                "baseUnit": "件",
                "materialCode": material_sku_code,
                "materialName": "PostgreSQL 16 隔离盘点物料",
                "specification": "",
                "status": "active",
            }
            material_external_object = ExternalObject(
                id=uuid.uuid4(),
                source_system_id=oam_source.id,
                entity_type="material",
                external_id=f"PG16-STOCKTAKE-MATERIAL-{uuid.uuid4().hex}",
                current_version_id=material_external_version_id,
                deleted_at=None,
                created_at=material_created_at,
                updated_at=material_created_at,
            )
            session.add(material_external_object)
            session.flush()
            material_external_version = ExternalObjectVersion(
                id=material_external_version_id,
                external_object_id=material_external_object.id,
                source_version="pg16-stocktake-material-v1",
                source_updated_at=material_created_at,
                valid_from=material_created_at,
                valid_to=None,
                payload_jsonb=material_payload,
                payload_sha256=_projector_gate_sha256(material_payload),
                is_current=True,
                created_at=material_created_at,
            )
            session.add(material_external_version)
            session.flush()
            material = FormalMaterial(
                id=material_id,
                external_object_id=material_external_object.id,
                sku_code=material_sku_code,
                name="PostgreSQL 16 隔离盘点物料",
                specification="",
                base_unit="件",
                status="active",
                source_updated_at=material_created_at,
                created_at=material_created_at,
                updated_at=material_created_at,
            )
            session.add(material)
            session.flush()
            assert material.external_object_id == material_external_object.id
            assert material_external_object.source_system_id == oam_source.id
            assert (
                material_external_object.current_version_id
                == material_external_version.id
            )

            concurrency_material_id = uuid.uuid4()
            concurrency_material_sku_code = (
                f"PG16-CONCURRENT-SKU-{concurrency_material_id.hex[:16].upper()}"
            )
            concurrency_material_version_id = uuid.uuid4()
            concurrency_material_payload = {
                "baseUnit": "件",
                "materialCode": concurrency_material_sku_code,
                "materialName": "PostgreSQL 16 并发别名物料",
                "specification": "",
                "status": "active",
            }
            concurrency_material_source = ExternalObject(
                id=uuid.uuid4(),
                source_system_id=oam_source.id,
                entity_type="material",
                external_id=(
                    "PG16-CONCURRENT-MATERIAL-"
                    f"{concurrency_material_id.hex}"
                ),
                current_version_id=concurrency_material_version_id,
                deleted_at=None,
                created_at=material_created_at,
                updated_at=material_created_at,
            )
            session.add(concurrency_material_source)
            session.flush()
            concurrency_material_version = ExternalObjectVersion(
                id=concurrency_material_version_id,
                external_object_id=concurrency_material_source.id,
                source_version="pg16-concurrent-material-v1",
                source_updated_at=material_created_at,
                valid_from=material_created_at,
                valid_to=None,
                payload_jsonb=concurrency_material_payload,
                payload_sha256=_projector_gate_sha256(
                    concurrency_material_payload
                ),
                is_current=True,
                created_at=material_created_at,
            )
            concurrency_material = FormalMaterial(
                id=concurrency_material_id,
                external_object_id=concurrency_material_source.id,
                sku_code=concurrency_material_sku_code,
                name="PostgreSQL 16 并发别名物料",
                specification="",
                base_unit="件",
                status="active",
                source_updated_at=material_created_at,
                created_at=material_created_at,
                updated_at=material_created_at,
            )
            session.add_all(
                (concurrency_material_version, concurrency_material)
            )
            session.flush()

            policy = MaterialInventoryPolicy(
                id=uuid.uuid4(),
                material_id=material.id,
                tracking_mode="none",
                quantity_scale=3,
                allow_fraction=True,
                effective_from=now - timedelta(days=1),
                effective_to=None,
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
            serial = InventorySerial(
                id=uuid.uuid4(),
                material_id=material.id,
                serial_no=f"PG16-STK-SERIAL-{material.id.hex[:16].upper()}",
                qr_code=f"PG16-STK-QR-{material.id.hex.upper()}",
                lot_id=None,
                lifecycle_status="active",
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
            concurrency_policy = MaterialInventoryPolicy(
                id=uuid.uuid4(),
                material_id=concurrency_material.id,
                tracking_mode="serial",
                quantity_scale=3,
                allow_fraction=False,
                effective_from=now - timedelta(days=1),
                effective_to=None,
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
            concurrency_serial = InventorySerial(
                id=uuid.uuid4(),
                material_id=concurrency_material.id,
                serial_no=(
                    "PG16-CONCURRENT-SERIAL-"
                    f"{concurrency_material.id.hex[:16].upper()}"
                ),
                qr_code=(
                    "PG16-CONCURRENT-QR-"
                    f"{concurrency_material.id.hex.upper()}"
                ),
                lot_id=None,
                lifecycle_status="active",
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
            location_id = uuid.uuid4()
            location = StockLocation(
                id=location_id,
                code=f"PG16-STK-{location_id.hex[:16].upper()}",
                name="PostgreSQL 16 隔离盘点仓",
                location_type="region",
                owner_org_id=region_org_id,
                parent_id=None,
                custodian_person_id=manager_person.id,
                status="active",
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
            concurrency_location_id = uuid.uuid4()
            concurrency_location = StockLocation(
                id=concurrency_location_id,
                code=(
                    "PG16-CONCURRENT-"
                    f"{concurrency_location_id.hex[:16].upper()}"
                ),
                name="PostgreSQL 16 并发别名隔离仓",
                location_type="region",
                owner_org_id=region_org_id,
                parent_id=None,
                custodian_person_id=manager_person.id,
                status="active",
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
            session.add_all(
                (
                    policy,
                    serial,
                    location,
                    concurrency_policy,
                    concurrency_serial,
                    concurrency_location,
                )
            )
            session.flush()
            session.add_all(
                (
                    CustodyAssignment(
                        id=uuid.uuid4(),
                        location_id=location.id,
                        custodian_person_id=manager_person.id,
                        valid_from=now - timedelta(days=1),
                        valid_to=None,
                        handover_case_id=None,
                        created_at=now - timedelta(days=1),
                        updated_at=now - timedelta(days=1),
                    ),
                    CustodyAssignment(
                        id=uuid.uuid4(),
                        location_id=concurrency_location.id,
                        custodian_person_id=manager_person.id,
                        valid_from=now - timedelta(days=1),
                        valid_to=None,
                        handover_case_id=None,
                        created_at=now - timedelta(days=1),
                        updated_at=now - timedelta(days=1),
                    ),
                )
            )
            account = StockAccount(
                id=uuid.uuid4(),
                owner_org_id=region_org_id,
                custodian_person_id=None,
                location_id=location.id,
                material_id=material.id,
                condition_code="new",
                availability_bucket="available",
                lot_id=None,
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
            concurrency_account = StockAccount(
                id=uuid.uuid4(),
                owner_org_id=region_org_id,
                custodian_person_id=None,
                location_id=concurrency_location.id,
                material_id=concurrency_material.id,
                condition_code="new",
                availability_bucket="available",
                lot_id=None,
                created_at=now - timedelta(days=1),
                updated_at=now - timedelta(days=1),
            )
            session.add_all((account, concurrency_account))
            session.flush()

            control_sync_run_id = uuid.uuid4()
            control_scope_key = (
                f"oam_inventory_control:region:{region_org_id}"
            )
            control_started_at = now - timedelta(hours=4)
            control_received_at = now - timedelta(hours=3)
            control_validated_at = now - timedelta(hours=2, minutes=30)
            control_completed_at = now - timedelta(hours=2)
            control_source_updated_at = now - timedelta(
                hours=3,
                minutes=30,
            )
            control_external_business_key = (
                f"PG16-STOCKTAKE-CONTROL-{location_id.hex}"
            )
            control_external_event_id = (
                f"event-{control_external_business_key}"
            )
            control_source_version = "pg16-stocktake-control-v1"
            control_event_id = uuid.uuid4()
            control_external_version_id = uuid.uuid4()
            control_payload = opening_control_projection_payload(
                external_business_key=control_external_business_key,
                region_org_id=region_org_id,
                material_id=material.id,
                condition_code="new",
                control_qty=Decimal("0.000"),
                mapping_status="resolved",
                mapping_note="",
            )
            control_payload_sha256 = canonical_opening_manifest_sha256(
                control_payload
            )
            control_line = OpeningControlLineInput(
                sync_inbox_event_id=control_event_id,
                external_object_version_id=control_external_version_id,
                external_business_key=control_external_business_key,
                material_id=material.id,
                condition_code="new",
                control_qty=Decimal("0.000"),
                mapping_status="resolved",
                source_updated_at=control_source_updated_at,
                payload_sha256=control_payload_sha256,
                mapping_note="",
            )
            control_manifest_sha256 = opening_control_manifest_sha256(
                source_system_id=oam_source.id,
                sync_run_id=control_sync_run_id,
                sync_scope_key=control_scope_key,
                region_org_id=region_org_id,
                lines=(control_line,),
            )
            control_sync_run = SyncRun(
                id=control_sync_run_id,
                source_system_id=oam_source.id,
                run_key=f"pg16-opening-control-{location_id.hex}",
                scope_key=control_scope_key,
                mode="full",
                watermark_from=None,
                watermark_to=None,
                status="completed",
                manifest_sha256=control_manifest_sha256,
                started_at=control_started_at,
                completed_at=control_completed_at,
                failure_code=None,
                failure_detail=None,
                created_at=control_started_at,
                updated_at=control_completed_at,
            )
            session.add(control_sync_run)
            session.flush()
            control_batch_id = uuid.uuid4()
            session.add(
                SyncBatch(
                    id=control_batch_id,
                    run_id=control_sync_run.id,
                    entity_type=OPENING_CONTROL_ENTITY_TYPE,
                    sequence=1,
                    record_count=1,
                    body_sha256=opening_control_batch_body_sha256(
                        sequence=1,
                        events=(
                            {
                                "event_sort_key": str(control_event_id),
                                "external_event_id": control_external_event_id,
                                "external_id": control_external_business_key,
                                "payload_sha256": control_payload_sha256,
                                "source_updated_at": control_source_updated_at.astimezone(
                                    timezone.utc
                                )
                                .isoformat(timespec="microseconds")
                                .replace("+00:00", "Z"),
                                "source_version": control_source_version,
                            },
                        ),
                    ),
                    status="applied",
                    received_at=control_received_at,
                    validated_at=control_validated_at,
                    created_at=control_received_at,
                )
            )
            session.flush()
            control_external_object = ExternalObject(
                id=uuid.uuid4(),
                source_system_id=oam_source.id,
                entity_type=OPENING_CONTROL_ENTITY_TYPE,
                external_id=control_external_business_key,
                current_version_id=control_external_version_id,
                deleted_at=None,
                created_at=control_source_updated_at,
                updated_at=control_source_updated_at,
            )
            session.add(control_external_object)
            session.flush()
            session.add_all(
                (
                    ExternalObjectVersion(
                        id=control_external_version_id,
                        external_object_id=control_external_object.id,
                        source_version=control_source_version,
                        source_updated_at=control_source_updated_at,
                        valid_from=control_source_updated_at,
                        valid_to=None,
                        payload_jsonb=control_payload,
                        payload_sha256=control_payload_sha256,
                        is_current=True,
                        created_at=control_source_updated_at,
                    ),
                    SyncInboxEvent(
                        id=control_event_id,
                        batch_id=control_batch_id,
                        source_system_id=oam_source.id,
                        external_event_id=control_external_event_id,
                        entity_type=OPENING_CONTROL_ENTITY_TYPE,
                        external_id=control_external_business_key,
                        source_version=control_source_version,
                        source_updated_at=control_source_updated_at,
                        payload_jsonb=control_payload,
                        payload_sha256=control_payload_sha256,
                        status="applied",
                        error_code=None,
                        error_detail=None,
                        processed_at=control_validated_at,
                        created_at=control_received_at,
                    ),
                )
            )
            session.commit()
            fixture: dict[str, object] = {
                "account_id": account.id,
                "assignee_person_id": manager_person.id,
                "control_lines": (control_line,),
                "control_scope_key": control_scope_key,
                "control_source_system_id": oam_source.id,
                "control_sync_run_id": control_sync_run.id,
                "concurrency_account_id": concurrency_account.id,
                "concurrency_location_id": concurrency_location.id,
                "concurrency_material_id": concurrency_material.id,
                "concurrency_material_sku_code": concurrency_material.sku_code,
                "concurrency_serial_id": concurrency_serial.id,
                "concurrency_serial_no": concurrency_serial.serial_no,
                "concurrency_serial_qr_code": concurrency_serial.qr_code,
                "deadline": now + timedelta(days=2),
                "location_id": location.id,
                "material_external_object_id": material_external_object.id,
                "material_external_version_id": material_external_version.id,
                "material_id": material.id,
                "material_policy_id": policy.id,
                "material_sku_code": material.sku_code,
                "opening_token": location_id.hex,
                "region_org_id": region_org_id,
                "serial_id": serial.id,
                "serial_no": serial.serial_no,
                "serial_qr_code": serial.qr_code,
            }
    finally:
        migrator_engine.dispose()

    _assert_0052_api_orphan_stock_account_rejected(
        api_engine,
        fixture=fixture,
    )

    from app.formal_access import load_formal_principal
    from app.formal_services.inventory_posting import (
        InventoryMovementCommand,
        InventoryPostingCommand,
        post_inventory_transaction,
    )
    from app.formal_services.opening_stocktake import (
        OpeningStocktakeScopeInput,
        StartOpeningStocktakeCommand,
        _start_opening_stocktake_impl,
    )
    import app.formal_services.opening_stocktake_count as opening_count_service
    from app.formal_services.opening_stocktake_count import (
        OpeningStocktakeCountError,
        OpeningPhysicalObservationInput,
        SubmitOpeningStocktakeScopeCountCommand,
        submit_opening_stocktake_scope_count,
    )
    from app.formal_services.opening_observation_disposition import (
        RecordOpeningObservationDispositionCommand,
        record_opening_observation_disposition,
    )
    from app.formal_services.opening_stocktake_finalize import (
        CloseOpeningStocktakeCommand,
        PostOpeningStocktakeCommand,
        close_posted_opening_stocktake,
        post_approved_opening_stocktake,
    )
    from app.formal_services.opening_stocktake_recount import (
        OpenOpeningStocktakeRecountCommand,
        OpeningStocktakeRecountScopeAssignmentInput,
        open_opening_stocktake_recount,
    )
    from app.formal_services.opening_stocktake_review import (
        OpeningStocktakeReviewItemInput,
        SubmitOpeningStocktakeReviewCommand,
        submit_opening_headquarters_review,
        submit_opening_region_review,
    )
    from app.stocktake_models import (
        FormalStocktakeScope,
        FormalStocktakeTask,
        InventoryOpeningEstablishment,
        StocktakeCountLine,
        StocktakeCountObservation,
        StocktakeCountSerial,
        StocktakeDifference,
        StocktakeDifferenceSetCompletion,
        StocktakeObservationDisposition,
        StocktakeRecountCase,
        StocktakeRecountScopeAssignment,
        StocktakeRound,
        StocktakeRoundSubmission,
        StocktakeScopeCountCompletion,
    )

    def current_principal(session: Session, user_id: str):
        principal_at = session.scalar(select(func.now()))
        assert (
            isinstance(principal_at, datetime)
            and principal_at.tzinfo is not None
        )
        return load_formal_principal(session, user_id, now=principal_at)

    opening_token = str(fixture["opening_token"])
    pending_identifier = (
        f"PG16-OPENING-PENDING-{opening_token[:16].upper()}"
    )
    with Session(api_engine, expire_on_commit=False) as session:
        started = _start_opening_stocktake_impl(
            session,
            actor=current_principal(session, assignee_user_id),
            command=StartOpeningStocktakeCommand(
                task_no=f"PG16-OPENING-{opening_token[:16].upper()}",
                region_org_id=fixture["region_org_id"],
                control_source_system_id=fixture[
                    "control_source_system_id"
                ],
                control_sync_run_id=fixture["control_sync_run_id"],
                control_sync_scope_key=str(fixture["control_scope_key"]),
                scopes=(
                    OpeningStocktakeScopeInput(
                        owner_org_id=fixture["region_org_id"],
                        location_id=fixture["location_id"],
                        assignee_user_id=assignee_user_id,
                        freeze_mode="hard",
                    ),
                ),
                control_lines=fixture["control_lines"],
                blind_count=True,
                deadline=fixture["deadline"],
                note="PG16 隔离门禁零期初建账",
            ),
            idempotency_key=f"pg16-opening-start-{opening_token}",
            request_id=f"trace-pg16-opening-start-{opening_token}",
        )
        assert started.status == "counting"
        assert started.scope_count == 1
        assert started.snapshot_line_count == 1
        assert started.control_line_count == 1
        session.commit()

    with Session(api_engine) as session:
        assert tuple(
            session.execute(
                text(
                    "SELECT before_jsonb IS NULL, "
                    "before_jsonb = 'null'::jsonb "
                    "FROM public.audit_events "
                    "WHERE aggregate_type = 'stocktake_task' "
                    "AND aggregate_id = :task_id "
                    "AND action = 'stocktake.opening.started'"
                ),
                {"task_id": str(started.task_id)},
            ).one()
        ) == (False, True)

    _assert_0052_raw_opening_task_insert_rejected(
        api_engine,
        task_id=started.task_id,
    )
    _assert_0052_forged_reconciliation_prefix_rejected(api_engine)
    _assert_0052_raw_opening_task_transition_rejected(
        api_engine,
        task_id=started.task_id,
        new_status="submitted",
        expected_message="opening task submission transition is invalid",
    )
    _assert_0052_raw_opening_task_transition_rejected(
        api_engine,
        task_id=started.task_id,
        new_status="approved",
        expected_message="opening task status transition is invalid",
    )
    _assert_0052_opening_history_downgrade_rejected(task_id=started.task_id)

    with Session(api_engine) as session:
        opening_scope_id = session.scalar(
            select(FormalStocktakeScope.id).where(
                FormalStocktakeScope.task_id == started.task_id
            )
        )
        assert isinstance(opening_scope_id, uuid.UUID)

    _assert_0052_standalone_count_line_rejected(
        api_engine,
        task_id=started.task_id,
        round_id=started.initial_round_id,
        scope_id=opening_scope_id,
        stock_account_id=fixture["account_id"],
        counted_by_user_id=assignee_user_id,
    )
    _assert_0052_standalone_count_serial_rejected(
        api_engine,
        task_id=started.task_id,
        round_id=started.initial_round_id,
        scope_id=opening_scope_id,
        stock_account_id=fixture["account_id"],
        serial_id=fixture["serial_id"],
        counted_by_user_id=assignee_user_id,
    )
    _assert_0052_standalone_count_observation_rejected(
        api_engine,
        task_id=started.task_id,
        round_id=started.initial_round_id,
        scope_id=opening_scope_id,
        counted_by_user_id=assignee_user_id,
    )
    _assert_0052_standalone_scope_completion_rejected(
        api_engine,
        task_id=started.task_id,
        round_id=started.initial_round_id,
        scope_id=opening_scope_id,
        stock_account_id=fixture["account_id"],
        counted_by_user_id=assignee_user_id,
    )

    def opening_count_command() -> SubmitOpeningStocktakeScopeCountCommand:
        return SubmitOpeningStocktakeScopeCountCommand(
            task_id=started.task_id,
            round_id=started.initial_round_id,
            scope_id=opening_scope_id,
            physical_observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw=str(
                        fixture["material_sku_code"]
                    ),
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    count_method="manual",
                    remark="PG16 0052 已知 SKU 原始请求证据",
                ),
                OpeningPhysicalObservationInput(
                    material_identifier_raw=pending_identifier,
                    material_identifier_type="unknown",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    count_method="manual",
                    remark="PG16 0051 待核实现场实物",
                ),
            ),
            zero_confirmed=False,
        )

    def opening_count_snapshot(engine) -> tuple[object, ...]:
        with Session(engine) as session:
            task = session.get(FormalStocktakeTask, started.task_id)
            round_row = session.get(StocktakeRound, started.initial_round_id)
            assert task is not None and round_row is not None
            counts = tuple(
                session.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(
                        model.task_id == started.task_id,
                        model.round_id == started.initial_round_id,
                    )
                )
                for model in (
                    StocktakeCountLine,
                    StocktakeCountObservation,
                    StocktakeScopeCountCompletion,
                    StocktakeRoundSubmission,
                    StocktakeDifference,
                    StocktakeDifferenceSetCompletion,
                )
            )
            return (
                task.status,
                task.version,
                task.submitted_at,
                round_row.status,
                round_row.submitted_at,
                round_row.count_manifest_sha256,
                *counts,
            )

    def reject_noncanonical_difference_completion_hash(
        engine,
        *,
        invalid_hash: str,
        replica_mode: bool,
    ) -> None:
        before = opening_count_snapshot(api_engine)
        assert before[:2] == ("counting", 0)
        assert before[2:6] == (None, "counting", None, None)
        assert before[6:] == (0, 0, 0, 0, 0, 0)
        if len(invalid_hash) == 63:
            def invalid_completion_factory(**values):
                values["authorization_sha256"] = invalid_hash
                return StocktakeDifferenceSetCompletion(**values)

            invalid_hash_patch = patch.object(
                opening_count_service,
                "StocktakeDifferenceSetCompletion",
                invalid_completion_factory,
            )
        else:
            invalid_hash_patch = patch.object(
                opening_count_service,
                "_authorization_sha256",
                return_value=invalid_hash,
            )

        with Session(engine, expire_on_commit=False) as session:
            try:
                if replica_mode:
                    session.execute(
                        text("SET LOCAL session_replication_role = 'replica'")
                    )
                    session.execute(text("SET LOCAL ROLE star_oam_migrator"))
                    assert session.execute(
                        text(
                            "SELECT current_user, session_user, "
                            "current_setting('session_replication_role')"
                        )
                    ).one() == (
                        "star_oam_migrator",
                        "postgres",
                        "replica",
                    )
                with invalid_hash_patch:
                    with pytest.raises(OpeningStocktakeCountError) as failure:
                        submit_opening_stocktake_scope_count(
                            session,
                            actor=current_principal(
                                session,
                                assignee_user_id,
                            ),
                            command=opening_count_command(),
                            idempotency_key=(
                                "pg16-opening-count-invalid-"
                                f"{len(invalid_hash)}-{invalid_hash[:1]}-"
                                f"{int(replica_mode)}-{opening_token}"
                            ),
                            request_id=(
                                "trace-pg16-opening-count-invalid-"
                                f"{len(invalid_hash)}-{invalid_hash[:1]}-"
                                f"{int(replica_mode)}-{opening_token}"
                            ),
                        )
                assert failure.value.code == (
                    "opening_count_database_guard_rejected"
                )
                assert failure.value.category == "precondition_failed"
                assert "authorization_sha256" not in str(failure.value)
                assert invalid_hash not in str(failure.value)
            finally:
                session.rollback()
        assert opening_count_snapshot(api_engine) == before

    for invalid_hash in ("a" * 63, "A" * 64, "g" * 64):
        reject_noncanonical_difference_completion_hash(
            api_engine,
            invalid_hash=invalid_hash,
            replica_mode=False,
        )

    replica_engine = create_engine(
        _admin_sqlalchemy_url(),
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    try:
        reject_noncanonical_difference_completion_hash(
            replica_engine,
            invalid_hash="A" * 64,
            replica_mode=True,
        )
    finally:
        replica_engine.dispose()

    before_resolution_tamper = opening_count_snapshot(api_engine)

    def submit_with_wide_aggregate_overflow() -> None:
        wide_quantity = Decimal("600000000000000.000")
        base_command = opening_count_command()
        wide_command = replace(
            base_command,
            physical_observations=tuple(
                replace(observation, counted_qty=wide_quantity)
                for observation in base_command.physical_observations
            ),
        )
        assert len(wide_command.physical_observations) == 2
        assert sum(
            (
                observation.counted_qty
                for observation in wide_command.physical_observations
            ),
            start=Decimal("0.000"),
        ) > Decimal("999999999999999.999")
        with Session(api_engine, expire_on_commit=False) as session:
            try:
                # Model a direct database writer that bypasses only the
                # service aggregate cap: each persisted row still fits
                # numeric(18, 3), while their sum deliberately does not.
                with patch.object(
                    opening_count_service,
                    "_require_aggregate_quantity",
                    return_value=Decimal("999999999999999.999"),
                ):
                    submit_opening_stocktake_scope_count(
                        session,
                        actor=current_principal(session, assignee_user_id),
                        command=wide_command,
                        idempotency_key=(
                            "pg16-opening-count-wide-aggregate-"
                            f"{opening_token}"
                        ),
                        request_id=(
                            "trace-pg16-opening-count-wide-aggregate-"
                            f"{opening_token}"
                        ),
                    )
                session.commit()
            finally:
                session.rollback()

    with pytest.raises(
        SanitizedPostgreSQLDiagnosticError
    ) as wide_aggregate_overflow:
        _reveal_pg16_service_database_error(
            api_engine,
            submit_with_wide_aggregate_overflow,
        )
    assert wide_aggregate_overflow.value.diagnostic.sqlstate == "23514"
    assert wide_aggregate_overflow.value.diagnostic.sqlstate != "22003"
    assert opening_count_snapshot(api_engine) == before_resolution_tamper

    def submit_with_forged_request_resolution() -> None:
        tampered = False

        def forge_resolution_before_insert(
            session: Session,
            _flush_context,
            _instances,
        ) -> None:
            nonlocal tampered
            for candidate in tuple(session.new):
                if not isinstance(candidate, StocktakeScopeCountCompletion):
                    continue
                document = candidate.request_resolution_jsonb
                assert isinstance(document, dict)
                items = [dict(row) for row in document["items"]]
                target_index = next(
                    index
                    for index, row in enumerate(items)
                    if row["target_type"] == "count_line"
                )
                items[target_index]["resolved_material_id"] = str(uuid.uuid4())
                candidate.request_resolution_jsonb = {
                    **document,
                    "items": items,
                }
                tampered = True

        with Session(api_engine, expire_on_commit=False) as session:
            event.listen(session, "before_flush", forge_resolution_before_insert)
            try:
                submit_opening_stocktake_scope_count(
                    session,
                    actor=current_principal(session, assignee_user_id),
                    command=opening_count_command(),
                    idempotency_key=(
                        f"pg16-opening-count-forged-resolution-{opening_token}"
                    ),
                    request_id=(
                        "trace-pg16-opening-count-forged-resolution-"
                        f"{opening_token}"
                    ),
                )
                assert tampered is True
                session.commit()
            finally:
                event.remove(
                    session,
                    "before_flush",
                    forge_resolution_before_insert,
                )
                session.rollback()

    with pytest.raises(SanitizedPostgreSQLDiagnosticError) as forged_resolution:
        _reveal_pg16_service_database_error(
            api_engine,
            submit_with_forged_request_resolution,
        )
    assert forged_resolution.value.diagnostic.sqlstate == "23514"
    assert opening_count_snapshot(api_engine) == before_resolution_tamper

    def submit_with_noncanonical_resolution_number(field_name: str) -> None:
        tampered = False

        def forge_number_before_insert(
            session: Session,
            _flush_context,
            _instances,
        ) -> None:
            nonlocal tampered
            for candidate in tuple(session.new):
                if not isinstance(candidate, StocktakeScopeCountCompletion):
                    continue
                document = candidate.request_resolution_jsonb
                assert isinstance(document, dict)
                items = [dict(row) for row in document["items"]]
                target_index = next(
                    index
                    for index, row in enumerate(items)
                    if row["target_type"] == "count_line"
                )
                if field_name == "request_ordinal":
                    items[target_index]["request_ordinal"] = float(
                        items[target_index]["request_ordinal"]
                    )
                else:
                    assert field_name == "policy.quantity_scale"
                    policy_document = dict(items[target_index]["policy"])
                    policy_document["quantity_scale"] = float(
                        policy_document["quantity_scale"]
                    )
                    items[target_index]["policy"] = policy_document
                candidate.request_resolution_jsonb = {
                    **document,
                    "items": items,
                }
                tampered = True

        with Session(api_engine, expire_on_commit=False) as session:
            event.listen(session, "before_flush", forge_number_before_insert)
            try:
                submit_opening_stocktake_scope_count(
                    session,
                    actor=current_principal(session, assignee_user_id),
                    command=opening_count_command(),
                    idempotency_key=(
                        "pg16-opening-count-noncanonical-number-"
                        f"{field_name.replace('.', '-')}-{opening_token}"
                    ),
                    request_id=(
                        "trace-pg16-opening-count-noncanonical-number-"
                        f"{field_name.replace('.', '-')}-{opening_token}"
                    ),
                )
                assert tampered is True
                session.commit()
            finally:
                event.remove(session, "before_flush", forge_number_before_insert)
                session.rollback()

    for noncanonical_number_field in (
        "request_ordinal",
        "policy.quantity_scale",
    ):
        with pytest.raises(
            SanitizedPostgreSQLDiagnosticError
        ) as noncanonical_number:
            _reveal_pg16_service_database_error(
                api_engine,
                lambda field_name=noncanonical_number_field: (
                    submit_with_noncanonical_resolution_number(field_name)
                ),
            )
        assert noncanonical_number.value.diagnostic.sqlstate == "23514"
        assert opening_count_snapshot(api_engine) == before_resolution_tamper

    def set_material_tracking_mode(tracking_mode: str) -> None:
        policy_engine = create_engine(
            _sqlalchemy_url(
                role="star_oam_migrator",
                password=_role_password("star_oam_migrator"),
            ),
            pool_size=1,
            max_overflow=0,
            pool_timeout=5,
        )
        try:
            with policy_engine.begin() as connection:
                updated = connection.execute(
                    text(
                        "UPDATE public.material_inventory_policies "
                        "SET tracking_mode = :tracking_mode "
                        "WHERE id = :policy_id"
                    ),
                    {
                        "tracking_mode": tracking_mode,
                        "policy_id": str(fixture["material_policy_id"]),
                    },
                )
                assert updated.rowcount == 1
        finally:
            policy_engine.dispose()

    def submit_cross_table_duplicate_serial() -> None:
        command = SubmitOpeningStocktakeScopeCountCommand(
            task_id=started.task_id,
            round_id=started.initial_round_id,
            scope_id=opening_scope_id,
            physical_observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw=str(
                        fixture["material_sku_code"]
                    ),
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    serial_no_raw=str(fixture["serial_no"]),
                    serial_identifier_type="serial_no",
                    count_method="manual",
                    remark="PG16 0052 已知账户 SN",
                ),
                OpeningPhysicalObservationInput(
                    material_identifier_raw=str(
                        fixture["material_sku_code"]
                    ),
                    material_identifier_type="sku_code",
                    condition_code="used",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    serial_no_raw=str(fixture["serial_no"]),
                    serial_identifier_type="serial_no",
                    count_method="manual",
                    remark="PG16 0052 无账户观察 SN",
                ),
            ),
            zero_confirmed=False,
        )
        with Session(api_engine, expire_on_commit=False) as session:
            with patch.object(
                opening_count_service,
                "_validate_round_serial_uniqueness",
                return_value=None,
            ):
                submit_opening_stocktake_scope_count(
                    session,
                    actor=current_principal(session, assignee_user_id),
                    command=command,
                    idempotency_key=(
                        "pg16-opening-count-cross-table-serial-"
                        f"{opening_token}"
                    ),
                    request_id=(
                        "trace-pg16-opening-count-cross-table-serial-"
                        f"{opening_token}"
                    ),
                )
            session.commit()

    def submit_cross_alias_duplicate_serial() -> None:
        command = SubmitOpeningStocktakeScopeCountCommand(
            task_id=started.task_id,
            round_id=started.initial_round_id,
            scope_id=opening_scope_id,
            physical_observations=(
                OpeningPhysicalObservationInput(
                    material_identifier_raw=str(
                        fixture["material_sku_code"]
                    ),
                    material_identifier_type="sku_code",
                    condition_code="new",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    serial_no_raw=str(fixture["serial_no"]),
                    serial_identifier_type="serial_no",
                    count_method="manual",
                    remark="PG16 0052 已知账户 SN 别名",
                ),
                OpeningPhysicalObservationInput(
                    material_identifier_raw=(
                        f"PG16-UNKNOWN-MATERIAL-{opening_token[:12]}"
                    ),
                    material_identifier_type="unknown",
                    condition_code="used",
                    availability_bucket="available",
                    counted_qty=Decimal("1.000"),
                    serial_no_raw=str(fixture["serial_qr_code"]).lower(),
                    serial_identifier_type="qr_code",
                    count_method="manual",
                    remark="PG16 0052 pending QR 大小写别名",
                ),
            ),
            zero_confirmed=False,
        )
        with Session(api_engine, expire_on_commit=False) as session:
            with patch.object(
                opening_count_service,
                "_validate_round_serial_uniqueness",
                return_value=None,
            ):
                submit_opening_stocktake_scope_count(
                    session,
                    actor=current_principal(session, assignee_user_id),
                    command=command,
                    idempotency_key=(
                        "pg16-opening-count-cross-alias-serial-"
                        f"{opening_token}"
                    ),
                    request_id=(
                        "trace-pg16-opening-count-cross-alias-serial-"
                        f"{opening_token}"
                    ),
                )
            session.commit()

    def assert_cross_alias_writers_serialize(
        *,
        isolation_task_id: uuid.UUID,
        isolation_round_id: uuid.UUID,
        isolation_scope_id: uuid.UUID,
    ) -> None:
        migration = _load_opening_terminal_guard_execution_migration_0052()
        api_parameters = _connection_parameters(
            role="star_oam_api",
            password=_role_password("star_oam_api"),
        )
        observation_id = uuid.uuid4()
        second_started = threading.Event()
        second_inserted = threading.Event()
        second_pid: list[int] = []
        second_failures: list[tuple[str | None, str]] = []

        def insert_competing_observation_row(
            cursor: psycopg.Cursor,
            *,
            competing_observation_id: uuid.UUID,
        ) -> None:
            cursor.execute(
                "INSERT INTO public.stocktake_count_observations ("
                "id, task_id, round_id, scope_id, observation_no, "
                "owner_org_id, location_id, "
                "custodian_person_id_snapshot, material_id, "
                "material_identifier_raw, material_identifier_type, "
                "condition_code, availability_bucket, lot_id, "
                "lot_no_raw, serial_id, serial_no_raw, "
                "serial_identifier_type, counted_qty, "
                "verification_status, count_method, reason_code, "
                "remark, counted_by_user_id, counted_at, "
                "dimension_sha256, request_sha256, "
                "idempotency_key_hash, created_at) "
                "SELECT %s, %s, %s, %s, 1, scope.owner_org_id, "
                "scope.location_id, "
                "scope.custodian_person_id_snapshot, NULL, %s, "
                "'unknown', 'used', 'available', NULL, NULL, NULL, "
                "%s, 'qr_code', 1, 'pending_verification', "
                "'manual', NULL, %s, %s, "
                "pg_catalog.transaction_timestamp(), %s, %s, %s, "
                "pg_catalog.transaction_timestamp() "
                "FROM public.stocktake_scopes AS scope "
                "WHERE scope.id = %s AND scope.task_id = %s",
                (
                    competing_observation_id,
                    isolation_task_id,
                    isolation_round_id,
                    isolation_scope_id,
                    f"PG16-CONCURRENT-UNKNOWN-{opening_token[:12]}",
                    str(fixture["concurrency_serial_qr_code"]).lower(),
                    "PG16 0052 并发 pending QR 别名",
                    assignee_user_id,
                    hashlib.sha256(
                        f"dimension:{competing_observation_id}".encode()
                    ).hexdigest(),
                    hashlib.sha256(
                        f"request:{competing_observation_id}".encode()
                    ).hexdigest(),
                    hashlib.sha256(
                        f"idempotency:{competing_observation_id}".encode()
                    ).hexdigest(),
                    isolation_scope_id,
                    isolation_task_id,
                ),
            )

        def insert_competing_observation() -> None:
            with psycopg.connect(**api_parameters) as connection:
                with connection.cursor() as cursor:
                    cursor.execute("SELECT pg_backend_pid()")
                    second_pid.append(cursor.fetchone()[0])
                    cursor.execute("SET LOCAL statement_timeout = '20s'")
                    second_started.set()
                    try:
                        insert_competing_observation_row(
                            cursor,
                            competing_observation_id=observation_id,
                        )
                        assert cursor.rowcount == 1
                        second_inserted.set()
                    except psycopg.Error as exc:
                        second_failures.append((exc.sqlstate, str(exc)))
                    finally:
                        connection.rollback()

        first = Session(api_engine, expire_on_commit=False)
        executor = ThreadPoolExecutor(max_workers=1)
        future = None
        try:
            first_counted = submit_opening_stocktake_scope_count(
                first,
                actor=current_principal(first, assignee_user_id),
                command=SubmitOpeningStocktakeScopeCountCommand(
                    task_id=isolation_task_id,
                    round_id=isolation_round_id,
                    scope_id=isolation_scope_id,
                    physical_observations=(
                        OpeningPhysicalObservationInput(
                            material_identifier_raw=str(
                                fixture["concurrency_material_sku_code"]
                            ),
                            material_identifier_type="sku_code",
                            condition_code="new",
                            availability_bucket="available",
                            counted_qty=Decimal("1.000"),
                            serial_no_raw=str(
                                fixture["concurrency_serial_no"]
                            ),
                            serial_identifier_type="serial_no",
                            count_method="manual",
                            remark="PG16 0052 并发胜者完整 scope graph",
                        ),
                    ),
                    zero_confirmed=False,
                ),
                idempotency_key=(
                    f"pg16-opening-concurrent-winner-{opening_token}"
                ),
                request_id=(
                    f"trace-pg16-opening-concurrent-winner-{opening_token}"
                ),
            )
            assert first_counted.task_status == "submitted"
            assert first_counted.round_status == "submitted"
            assert first_counted.round_sealed is True
            assert first.scalar(
                select(func.count())
                .select_from(StocktakeCountSerial)
                .join(
                    StocktakeCountLine,
                    StocktakeCountLine.id
                    == StocktakeCountSerial.count_line_id,
                )
                .where(
                    StocktakeCountLine.task_id == isolation_task_id,
                    StocktakeCountSerial.round_id == isolation_round_id,
                    StocktakeCountSerial.serial_id
                    == fixture["concurrency_serial_id"],
                )
            ) == 1
            assert first.scalar(
                select(func.count())
                .select_from(StocktakeCountObservation)
                .where(
                    StocktakeCountObservation.task_id == isolation_task_id,
                    StocktakeCountObservation.round_id == isolation_round_id,
                )
            ) == 0

            repeatable_read_failure: tuple[str | None, str] | None = None
            with psycopg.connect(
                **api_parameters,
                autocommit=True,
            ) as repeatable_read_connection:
                with repeatable_read_connection.cursor() as cursor:
                    cursor.execute(
                        "BEGIN ISOLATION LEVEL REPEATABLE READ READ WRITE"
                    )
                    cursor.execute(
                        "SELECT status FROM public.stocktake_tasks "
                        "WHERE id = %s",
                        (isolation_task_id,),
                    )
                    assert cursor.fetchone() == ("counting",)
                    try:
                        insert_competing_observation_row(
                            cursor,
                            competing_observation_id=uuid.uuid4(),
                        )
                    except psycopg.Error as exc:
                        repeatable_read_failure = (exc.sqlstate, str(exc))
                    finally:
                        cursor.execute("ROLLBACK")
            assert repeatable_read_failure is not None
            assert repeatable_read_failure[0] == "23514"
            assert migration.OPENING_COUNT_ISOLATION_ERROR in (
                repeatable_read_failure[1]
            )
            assert first.scalar(
                select(func.count())
                .select_from(StocktakeCountObservation)
                .where(
                    StocktakeCountObservation.task_id == isolation_task_id,
                    StocktakeCountObservation.round_id == isolation_round_id,
                )
            ) == 0

            future = executor.submit(insert_competing_observation)
            assert second_started.wait(timeout=10)
            assert second_pid
            _wait_for_backend_lock(second_pid[0])
            assert second_inserted.is_set() is False
            first.commit()
            future.result(timeout=15)
            assert second_inserted.is_set() is False
            assert len(second_failures) == 1
            assert second_failures[0][0] == "23514"
            assert migration.OPENING_ROUND_SERIAL_DUPLICATE_ERROR in (
                second_failures[0][1]
            )
        finally:
            first.rollback()
            first.close()
            executor.shutdown(wait=True, cancel_futures=True)

    set_material_tracking_mode("serial")
    try:
        with pytest.raises(
            SanitizedPostgreSQLDiagnosticError
        ) as duplicate_serial:
            _reveal_pg16_service_database_error(
                api_engine,
                submit_cross_table_duplicate_serial,
            )
        assert duplicate_serial.value.diagnostic.sqlstate == "23514"
        assert opening_count_snapshot(api_engine) == before_resolution_tamper
        with pytest.raises(
            SanitizedPostgreSQLDiagnosticError
        ) as duplicate_serial_alias:
            _reveal_pg16_service_database_error(
                api_engine,
                submit_cross_alias_duplicate_serial,
            )
        assert duplicate_serial_alias.value.diagnostic.sqlstate == "23514"
        assert opening_count_snapshot(api_engine) == before_resolution_tamper
    finally:
        set_material_tracking_mode("none")

    with Session(api_engine, expire_on_commit=False) as session:
        scope = session.scalar(
            select(FormalStocktakeScope).where(
                FormalStocktakeScope.task_id == started.task_id
            )
        )
        assert scope is not None
        counted = _reveal_pg16_service_database_error(
            api_engine,
            lambda: submit_opening_stocktake_scope_count(
                session,
                actor=current_principal(session, assignee_user_id),
                command=opening_count_command(),
                idempotency_key=f"pg16-opening-count-{opening_token}",
                request_id=f"trace-pg16-opening-count-{opening_token}",
            )
        )
        assert counted.task_status == "submitted"
        assert counted.round_status == "submitted"
        assert counted.round_sealed is True
        assert counted.has_pending_verification is True
        opening_scope_id = scope.id
        session.commit()

    with Session(api_engine) as session:
        initial_task = session.get(FormalStocktakeTask, started.task_id)
        initial_round = session.get(StocktakeRound, started.initial_round_id)
        difference_completion = session.scalar(
            select(StocktakeDifferenceSetCompletion).where(
                StocktakeDifferenceSetCompletion.task_id == started.task_id,
                StocktakeDifferenceSetCompletion.round_id
                == started.initial_round_id,
            )
        )
        round_submission = session.scalar(
            select(StocktakeRoundSubmission).where(
                StocktakeRoundSubmission.task_id == started.task_id,
                StocktakeRoundSubmission.round_id == started.initial_round_id,
            )
        )
        assert difference_completion is not None
        assert round_submission is not None
        assert initial_task is not None and initial_round is not None
        assert (
            initial_task.status,
            initial_task.current_round_no,
            initial_task.submitted_at,
        ) == ("submitted", 1, round_submission.submitted_at)
        assert initial_round.submitted_at == round_submission.submitted_at
        initial_submitted_at = round_submission.submitted_at
        sealing = session.get(
            StocktakeScopeCountCompletion,
            round_submission.sealing_completion_id,
        )
        assert sealing is not None
        assert isinstance(sealing.request_jsonb, dict)
        request_observations = sealing.request_jsonb.get(
            "physical_observations"
        )
        assert isinstance(request_observations, list)
        known_request_rows = tuple(
            row
            for row in request_observations
            if isinstance(row, dict)
            and row.get("material_identifier_raw")
            == fixture["material_sku_code"]
        )
        assert len(known_request_rows) == 1
        assert (
            known_request_rows[0].get("material_identifier_type"),
            known_request_rows[0].get("material_identifier_raw"),
            known_request_rows[0].get("material_id"),
            known_request_rows[0].get("counted_qty"),
        ) == (
            "sku_code",
            fixture["material_sku_code"],
            None,
            "1",
        )
        assert sealing.request_sha256 == opening_count_service._hash_document(
            sealing.request_jsonb
        )
        assert isinstance(sealing.request_resolution_jsonb, dict)
        resolution_document = sealing.request_resolution_jsonb
        assert (
            resolution_document.get("schema"),
            resolution_document.get("request_sha256"),
            resolution_document.get("task_id"),
            resolution_document.get("round_id"),
            resolution_document.get("scope_id"),
        ) == (
            (
                "cloud_oam.opening_stocktake."
                "scope_count_request_resolution.v1"
            ),
            sealing.request_sha256,
            str(started.task_id),
            str(started.initial_round_id),
            str(opening_scope_id),
        )
        resolution_items = resolution_document.get("items")
        assert isinstance(resolution_items, list)
        assert [row.get("request_ordinal") for row in resolution_items] == [
            1,
            2,
        ]
        assert [row.get("request_item_sha256") for row in resolution_items] == [
            opening_count_service._hash_document(row)
            for row in request_observations
        ]
        known_count_line = session.scalar(
            select(StocktakeCountLine).where(
                StocktakeCountLine.task_id == started.task_id,
                StocktakeCountLine.round_id == started.initial_round_id,
                StocktakeCountLine.stock_account_id == fixture["account_id"],
            )
        )
        assert known_count_line is not None
        assert known_count_line.counted_qty == Decimal("1.000")
        known_request_ordinal = request_observations.index(
            known_request_rows[0]
        ) + 1
        known_resolution = resolution_items[known_request_ordinal - 1]
        assert (
            known_resolution.get("target_type"),
            known_resolution.get("target_id"),
            known_resolution.get("resolved_material_id"),
            known_resolution.get("resolved_lot_id"),
            known_resolution.get("resolved_serial_id"),
            known_resolution.get("material_qr_mapping_id"),
            known_resolution.get("serial_qr_mapping_id"),
        ) == (
            "count_line",
            str(known_count_line.id),
            str(fixture["material_id"]),
            None,
            None,
            None,
            None,
        )
        assert known_resolution.get("policy", {}).get("id") == str(
            fixture["material_policy_id"]
        )
        assert known_resolution.get("policy", {}).get("tracking_mode") == "none"
        assert session.scalar(
            select(func.count())
            .select_from(StocktakeCountObservation)
            .where(
                StocktakeCountObservation.task_id == started.task_id,
                StocktakeCountObservation.round_id
                == started.initial_round_id,
                StocktakeCountObservation.material_identifier_raw
                == fixture["material_sku_code"],
            )
        ) == 0
        pending_request = next(
            row
            for row in request_observations
            if row.get("material_identifier_raw") == pending_identifier
        )
        pending_ordinal = request_observations.index(pending_request) + 1
        pending_resolution = resolution_items[pending_ordinal - 1]
        assert (
            pending_resolution.get("target_type"),
            pending_resolution.get("resolved_material_id"),
            pending_resolution.get("policy"),
        ) == ("observation", None, None)
        assert difference_completion.authorization_sha256 == (
            sealing.authorization_sha256
        )
        assert len(difference_completion.authorization_sha256) == 64
        assert difference_completion.authorization_sha256 == (
            difference_completion.authorization_sha256.lower()
        )
        assert set(difference_completion.authorization_sha256) <= set(
            "0123456789abcdef"
        )
    _assert_0052_cross_domain_posting_rejected(
        api_engine,
        task_id=started.task_id,
        round_id=started.initial_round_id,
        posted_by_user_id=assignee_user_id,
    )
    _assert_0051_difference_completion_catalog(
        repaired=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0052_raw_opening_task_transition_rejected(
        api_engine,
        task_id=started.task_id,
        new_status="hq_review",
        expected_message="opening region review evidence is incomplete",
    )

    with Session(api_engine, expire_on_commit=False) as session:
        observation = session.scalar(
            select(StocktakeCountObservation).where(
                StocktakeCountObservation.task_id == started.task_id,
                StocktakeCountObservation.round_id
                == started.initial_round_id,
                StocktakeCountObservation.material_identifier_raw
                == pending_identifier,
            )
        )
        assert observation is not None
        assert observation.verification_status == "pending_verification"
        disposed = _reveal_pg16_service_database_error(
            api_engine,
            lambda: record_opening_observation_disposition(
                session,
                actor=current_principal(session, assignee_user_id),
                command=RecordOpeningObservationDispositionCommand(
                    task_id=started.task_id,
                    round_id=started.initial_round_id,
                    observation_id=observation.id,
                    disposition="requires_recount",
                    reason_code="pg16_0049_recount_required",
                    comment="待核实现场实物必须由同一受控范围重新实盘",
                ),
                idempotency_key=(
                    f"pg16-opening-disposition-{opening_token}"
                ),
                request_id=(
                    f"trace-pg16-opening-disposition-{opening_token}"
                ),
            )
        )
        assert disposed.observation_id == observation.id
        assert disposed.disposition == "requires_recount"
        session.commit()

    with Session(api_engine, expire_on_commit=False) as session:
        initial_differences = tuple(
            session.scalars(
                select(StocktakeDifference)
                .where(
                    StocktakeDifference.task_id == started.task_id,
                    StocktakeDifference.round_id
                    == started.initial_round_id,
                )
                .order_by(StocktakeDifference.difference_no)
            ).all()
        )
        assert len(initial_differences) == 3
        pending_difference = next(
            row
            for row in initial_differences
            if row.observed_line_id == observation.id
        )
        assert (
            pending_difference.observed_line_id,
            pending_difference.difference_type,
            pending_difference.reason_code,
        ) == (
            observation.id,
            "excess",
            "opening_pending_verification",
        )
        physical_difference = next(
            row
            for row in initial_differences
            if row.observed_account_id == fixture["account_id"]
        )
        assert (
            physical_difference.difference_type,
            physical_difference.reason_code,
            physical_difference.book_qty,
            physical_difference.counted_qty,
        ) == (
            "excess",
            "opening_physical_excess",
            Decimal("0.000"),
            Decimal("1.000"),
        )
        regional_recount = _reveal_pg16_service_database_error(
            api_engine,
            lambda: submit_opening_region_review(
                session,
                actor=current_principal(session, assignee_user_id),
                command=SubmitOpeningStocktakeReviewCommand(
                    task_id=started.task_id,
                    round_id=started.initial_round_id,
                    decision="recount",
                    items=tuple(
                        OpeningStocktakeReviewItemInput(
                            difference_id=row.id,
                            decision=(
                                "pending_verification"
                                if row.difference_type
                                == "control_unassigned"
                                else "recount"
                            ),
                            comment="待核实观察已处置，进入同范围复盘",
                        )
                        for row in initial_differences
                    ),
                    comment="PG16 0049 隔离门禁区域要求复盘",
                ),
                idempotency_key=(
                    f"pg16-opening-region-recount-{opening_token}"
                ),
                request_id=(
                    f"trace-pg16-opening-region-recount-{opening_token}"
                ),
            )
        )
        assert regional_recount.resulting_task_status == "recount_required"
        assert regional_recount.item_count == len(initial_differences)
        assert regional_recount.pending_control_count == 1
        session.commit()

    _assert_0052_wrong_opening_audit_action_rejected(
        api_engine,
        review_id=regional_recount.review_id,
        actor_user_id=assignee_user_id,
    )
    _assert_0052_review_state_and_outbox_rejected(
        api_engine,
        task_id=started.task_id,
        review_id=regional_recount.review_id,
        actor_user_id=assignee_user_id,
    )
    _assert_0052_raw_opening_task_transition_rejected(
        api_engine,
        task_id=started.task_id,
        new_status="counting",
        round_increment=1,
        expected_message="opening recount task evidence is incomplete",
    )

    with Session(api_engine, expire_on_commit=False) as session:
        opened_recount = _reveal_pg16_service_database_error(
            api_engine,
            lambda: open_opening_stocktake_recount(
                session,
                actor=current_principal(session, assignee_user_id),
                command=OpenOpeningStocktakeRecountCommand(
                    task_id=started.task_id,
                    source_round_id=started.initial_round_id,
                    assignments=(
                        OpeningStocktakeRecountScopeAssignmentInput(
                            scope_id=opening_scope_id,
                            assignee_user_id=assignee_user_id,
                        ),
                    ),
                    reason="PG16 0049 隔离门禁按区域复核结论复盘",
                ),
                idempotency_key=f"pg16-opening-recount-{opening_token}",
                request_id=f"trace-pg16-opening-recount-{opening_token}",
            )
        )
        assert opened_recount.resulting_task_status == "counting"
        assert opened_recount.next_round_no == 2
        assert opened_recount.scope_count == 1
        session.commit()

    with Session(api_engine) as session:
        recount_open_task = session.get(FormalStocktakeTask, started.task_id)
        assert recount_open_task is not None
        assert (
            recount_open_task.status,
            recount_open_task.current_round_no,
            recount_open_task.submitted_at,
        ) == ("counting", 2, initial_submitted_at)

    with Session(api_engine, expire_on_commit=False) as session:
        recount_counted = _reveal_pg16_service_database_error(
            api_engine,
            lambda: submit_opening_stocktake_scope_count(
                session,
                actor=current_principal(session, assignee_user_id),
                command=SubmitOpeningStocktakeScopeCountCommand(
                    task_id=started.task_id,
                    round_id=opened_recount.next_round_id,
                    scope_id=opening_scope_id,
                    physical_observations=(),
                    zero_confirmed=False,
                ),
                idempotency_key=(
                    f"pg16-opening-recount-count-{opening_token}"
                ),
                request_id=(
                    f"trace-pg16-opening-recount-count-{opening_token}"
                ),
            )
        )
        assert recount_counted.task_status == "submitted"
        assert recount_counted.round_status == "submitted"
        assert recount_counted.round_sealed is True
        assert recount_counted.has_pending_verification is False
        session.commit()

    with Session(api_engine) as session:
        recount_submitted_task = session.get(
            FormalStocktakeTask,
            started.task_id,
        )
        recount_round = session.get(StocktakeRound, opened_recount.next_round_id)
        recount_submission = session.scalar(
            select(StocktakeRoundSubmission).where(
                StocktakeRoundSubmission.task_id == started.task_id,
                StocktakeRoundSubmission.round_id
                == opened_recount.next_round_id,
            )
        )
        assert recount_submitted_task is not None
        assert recount_round is not None and recount_submission is not None
        assert (
            recount_submitted_task.status,
            recount_submitted_task.current_round_no,
            recount_submitted_task.submitted_at,
        ) == ("submitted", 2, recount_submission.submitted_at)
        assert recount_round.submitted_at == recount_submission.submitted_at
        assert recount_submission.submitted_at > initial_submitted_at

    # Every row below was written by a formal service through star_oam_api and
    # survived its own COMMIT.  Together they execute all nine trigger callers
    # hardened by 0049: the 0016 disposition caller; the 0018 assignment
    # caller; the three 0021 count callers; and the 0032 case, task, round and
    # deferred recount-graph callers.
    with Session(api_engine, expire_on_commit=False) as session:
        opening_task = session.get(FormalStocktakeTask, started.task_id)
        recount_case = session.get(
            StocktakeRecountCase,
            opened_recount.recount_case_id,
        )
        recount_assignment = session.scalar(
            select(StocktakeRecountScopeAssignment).where(
                StocktakeRecountScopeAssignment.recount_case_id
                == opened_recount.recount_case_id
            )
        )
        rounds = tuple(
            session.scalars(
                select(StocktakeRound)
                .where(StocktakeRound.task_id == started.task_id)
                .order_by(StocktakeRound.round_no)
            ).all()
        )
        assert opening_task is not None
        assert (
            opening_task.status,
            opening_task.current_round_no,
        ) == ("submitted", 2)
        assert recount_case is not None
        assert (
            recount_case.task_id,
            recount_case.source_round_id,
            recount_case.next_round_no,
            recount_case.scope_count,
        ) == (started.task_id, started.initial_round_id, 2, 1)
        assert recount_assignment is not None
        assert (
            recount_assignment.task_id,
            recount_assignment.source_round_id,
            recount_assignment.scope_id,
            recount_assignment.assignee_user_id,
        ) == (
            started.task_id,
            started.initial_round_id,
            opening_scope_id,
            assignee_user_id,
        )
        assert tuple(
            (row.id, row.round_no, row.round_type, row.status)
            for row in rounds
        ) == (
            (started.initial_round_id, 1, "initial", "submitted"),
            (
                opened_recount.next_round_id,
                2,
                "recount",
                "submitted",
            ),
        )
        assert session.scalar(
            select(func.count()).select_from(StocktakeCountLine).where(
                StocktakeCountLine.task_id == started.task_id
            )
        ) == 2
        assert session.scalar(
            select(func.count())
            .select_from(StocktakeCountObservation)
            .where(StocktakeCountObservation.task_id == started.task_id)
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(StocktakeObservationDisposition)
            .where(StocktakeObservationDisposition.task_id == started.task_id)
        ) == 1
        assert session.scalar(
            select(func.count())
            .select_from(StocktakeScopeCountCompletion)
            .where(StocktakeScopeCountCompletion.task_id == started.task_id)
        ) == 2
        recount_differences = tuple(
            session.scalars(
                select(StocktakeDifference)
                .where(
                    StocktakeDifference.task_id == started.task_id,
                    StocktakeDifference.round_id
                    == opened_recount.next_round_id,
                )
                .order_by(StocktakeDifference.difference_no)
            ).all()
        )
        regional_review = _reveal_pg16_service_database_error(
            api_engine,
            lambda: submit_opening_region_review(
                session,
                actor=current_principal(session, assignee_user_id),
                command=SubmitOpeningStocktakeReviewCommand(
                    task_id=started.task_id,
                    round_id=opened_recount.next_round_id,
                    decision="approve",
                    items=tuple(
                        OpeningStocktakeReviewItemInput(
                            difference_id=row.id,
                            decision="accept_for_posting",
                        )
                        for row in recount_differences
                    ),
                    comment="PG16 0049 隔离门禁复盘区域复核通过",
                ),
                idempotency_key=(
                    f"pg16-opening-recount-region-{opening_token}"
                ),
                request_id=(
                    f"trace-pg16-opening-recount-region-{opening_token}"
                ),
            )
        )
        assert regional_review.resulting_task_status == "hq_review"
        assert regional_review.item_count == len(recount_differences)
        assert regional_review.pending_control_count == 0
        session.commit()

    _assert_0052_raw_opening_task_transition_rejected(
        api_engine,
        task_id=started.task_id,
        new_status="approved",
        expected_message=(
            "opening headquarters review evidence is incomplete"
        ),
    )

    with Session(api_engine) as session:
        recount_differences = tuple(
            session.scalars(
                select(StocktakeDifference)
                .where(
                    StocktakeDifference.task_id == started.task_id,
                    StocktakeDifference.round_id
                    == opened_recount.next_round_id,
                )
                .order_by(StocktakeDifference.difference_no)
            ).all()
        )
        headquarters_review = _reveal_pg16_service_database_error(
            api_engine,
            lambda: submit_opening_headquarters_review(
                session,
                actor=current_principal(session, actor_user_id),
                command=SubmitOpeningStocktakeReviewCommand(
                    task_id=started.task_id,
                    round_id=opened_recount.next_round_id,
                    decision="approve",
                    items=tuple(
                        OpeningStocktakeReviewItemInput(
                            difference_id=row.id,
                            decision="accept_for_posting",
                        )
                        for row in recount_differences
                    ),
                    comment="PG16 0049 隔离门禁复盘总部复核通过",
                ),
                idempotency_key=(
                    f"pg16-opening-recount-hq-{opening_token}"
                ),
                request_id=(
                    f"trace-pg16-opening-recount-hq-{opening_token}"
                ),
            )
        )
        assert headquarters_review.resulting_task_status == "approved"
        assert headquarters_review.item_count == len(recount_differences)
        assert headquarters_review.pending_control_count == 0
        approved_task = session.get(FormalStocktakeTask, started.task_id)
        assert approved_task is not None and approved_task.status == "approved"
        approved_version = approved_task.version
        session.commit()

    migration_0052 = _load_opening_terminal_guard_execution_migration_0052()
    review_helper = sql.Identifier(migration_0052.REVIEW_GRAPH_FUNCTION)
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    "SELECT public.{}(%s::uuid, FALSE), "
                    "public.{}(%s::uuid, TRUE), "
                    "public.{}(%s::uuid, FALSE), "
                    "public.{}(%s::uuid, TRUE)"
                ).format(
                    review_helper,
                    review_helper,
                    review_helper,
                    review_helper,
                ),
                (
                    regional_review.review_id,
                    regional_review.review_id,
                    headquarters_review.review_id,
                    headquarters_review.review_id,
                ),
            )
            assert tuple(cursor.fetchone()) == (False, True, True, True)

    with Session(api_engine, expire_on_commit=False) as session:
        opening_posted = _reveal_pg16_service_database_error(
            api_engine,
            lambda: post_approved_opening_stocktake(
                session,
                actor=current_principal(session, actor_user_id),
                command=PostOpeningStocktakeCommand(
                    task_id=started.task_id,
                    expected_version=approved_version,
                ),
                idempotency_key=f"pg16-opening-post-{opening_token}",
                request_id=f"trace-pg16-opening-post-{opening_token}",
            )
        )
        assert opening_posted.resulting_task_status == "posted"
        assert opening_posted.total_quantity == Decimal("0.000")
        assert opening_posted.inventory_transaction_id is None
        assert opening_posted.established_scope_count == 1
        assert opening_posted.pending_control_difference_count == 0
        session.commit()

    terminal_helper = sql.Identifier(migration_0052.TERMINAL_GRAPH_FUNCTION)
    with psycopg.connect(**_admin_parameters()) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                sql.SQL(
                    """
SELECT posting.total_quantity::text,
       pg_catalog.jsonb_typeof(
           state_event.metadata_jsonb -> 'total_quantity'
       ),
       state_event.metadata_jsonb ->> 'total_quantity',
       pg_catalog.jsonb_typeof(
           outbox_event.payload_jsonb -> 'total_quantity'
       ),
       outbox_event.payload_jsonb ->> 'total_quantity',
       pg_catalog.jsonb_typeof(
           audit_event.after_jsonb -> 'total_quantity'
       ),
       audit_event.after_jsonb ->> 'total_quantity',
       public.{}(posting.task_id, FALSE),
       public.{}(posting.task_id, TRUE)
  FROM public.stocktake_postings AS posting
  JOIN public.state_transition_events AS state_event
    ON state_event.aggregate_type = 'stocktake_task'
   AND state_event.aggregate_id = posting.task_id::text
   AND state_event.reason = 'opening_stocktake_posted'
  JOIN public.outbox_events AS outbox_event
    ON outbox_event.aggregate_type = 'stocktake_task'
   AND outbox_event.aggregate_id = posting.task_id::text
   AND outbox_event.event_type = 'stocktake.opening.posted'
  JOIN public.audit_events AS audit_event
    ON audit_event.aggregate_type = 'stocktake_posting'
   AND audit_event.aggregate_id = posting.id::text
   AND audit_event.action = 'stocktake.opening.posted'
 WHERE posting.id = %s::uuid
"""
                ).format(terminal_helper, terminal_helper),
                (opening_posted.posting_id,),
            )
            assert tuple(cursor.fetchone()) == (
                "0.000",
                "string",
                "0.000",
                "string",
                "0.000",
                "string",
                "0.000",
                True,
                True,
            )

    _assert_0052_premature_close_artifacts_rejected(
        api_engine,
        task_id=started.task_id,
        actor_user_id=actor_user_id,
    )

    with Session(api_engine) as session:
        opening_closed = _reveal_pg16_service_database_error(
            api_engine,
            lambda: close_posted_opening_stocktake(
                session,
                actor=current_principal(session, actor_user_id),
                command=CloseOpeningStocktakeCommand(
                    task_id=started.task_id,
                    expected_version=opening_posted.task_version,
                ),
                idempotency_key=f"pg16-opening-close-{opening_token}",
                request_id=f"trace-pg16-opening-close-{opening_token}",
            )
        )
        assert opening_closed.resulting_task_status == "closed"
        establishment = session.scalar(
            select(InventoryOpeningEstablishment).where(
                InventoryOpeningEstablishment.task_id == started.task_id,
                InventoryOpeningEstablishment.owner_org_id
                == fixture["region_org_id"],
                InventoryOpeningEstablishment.location_id
                == fixture["location_id"],
            )
        )
        assert establishment is not None
        seeded_material = session.get(FormalMaterial, fixture["material_id"])
        material_source = session.get(
            ExternalObject,
            fixture["material_external_object_id"],
        )
        material_version = session.get(
            ExternalObjectVersion,
            fixture["material_external_version_id"],
        )
        assert (
            seeded_material is not None
            and material_source is not None
            and material_version is not None
        )
        assert seeded_material.external_object_id == material_source.id
        assert (
            material_source.source_system_id,
            material_source.entity_type,
            material_source.deleted_at,
        ) == (fixture["control_source_system_id"], "material", None)
        assert material_source.current_version_id == material_version.id
        assert material_version.external_object_id == material_source.id
        assert material_version.is_current is True
        assert material_version.valid_to is None
        assert material_version.payload_sha256 == _projector_gate_sha256(
            material_version.payload_jsonb
        )
        session.commit()

    # Even a fully closed opening task retains state/event/audit evidence that
    # the legacy 0051 runtime cannot protect.  Downgrade therefore fails closed
    # without changing the version or the durable lifecycle row.
    _assert_0052_opening_history_downgrade_rejected(task_id=started.task_id)
    with Session(api_engine) as session:
        terminal_opening = session.get(FormalStocktakeTask, started.task_id)
        assert terminal_opening is not None
        assert (
            terminal_opening.task_type,
            terminal_opening.status,
            terminal_opening.version,
        ) == ("opening", "closed", opening_closed.task_version)

    with Session(api_engine, expire_on_commit=False) as session:
        isolation_started = _start_opening_stocktake_impl(
            session,
            actor=current_principal(session, assignee_user_id),
            command=StartOpeningStocktakeCommand(
                task_no=(
                    "PG16-OPENING-CONCURRENT-"
                    f"{opening_token[:12].upper()}"
                ),
                region_org_id=fixture["region_org_id"],
                control_source_system_id=fixture[
                    "control_source_system_id"
                ],
                control_sync_run_id=fixture["control_sync_run_id"],
                control_sync_scope_key=str(fixture["control_scope_key"]),
                scopes=(
                    OpeningStocktakeScopeInput(
                        owner_org_id=fixture["region_org_id"],
                        location_id=fixture["concurrency_location_id"],
                        assignee_user_id=assignee_user_id,
                        freeze_mode="hard",
                    ),
                ),
                control_lines=fixture["control_lines"],
                blind_count=True,
                deadline=fixture["deadline"],
                note="PG16 0052 双会话 SN 别名隔离任务",
            ),
            idempotency_key=(
                f"pg16-opening-concurrent-start-{opening_token}"
            ),
            request_id=(
                f"trace-pg16-opening-concurrent-start-{opening_token}"
            ),
        )
        assert isolation_started.status == "counting"
        assert isolation_started.scope_count == 1
        assert isolation_started.snapshot_line_count == 1
        session.commit()

    with Session(api_engine) as session:
        isolation_scope_id = session.scalar(
            select(FormalStocktakeScope.id).where(
                FormalStocktakeScope.task_id == isolation_started.task_id
            )
        )
        assert isinstance(isolation_scope_id, uuid.UUID)

    assert_cross_alias_writers_serialize(
        isolation_task_id=isolation_started.task_id,
        isolation_round_id=isolation_started.initial_round_id,
        isolation_scope_id=isolation_scope_id,
    )
    with Session(api_engine) as session:
        isolation_task = session.get(
            FormalStocktakeTask,
            isolation_started.task_id,
        )
        assert isolation_task is not None
        assert isolation_task.status == "submitted"
        assert session.scalar(
            select(func.count())
            .select_from(StocktakeScopeCountCompletion)
            .where(
                StocktakeScopeCountCompletion.task_id
                == isolation_started.task_id,
                StocktakeScopeCountCompletion.round_id
                == isolation_started.initial_round_id,
            )
        ) == 1

    with Session(api_engine, expire_on_commit=False) as session:
        effective_at = session.scalar(select(func.now()))
        assert isinstance(effective_at, datetime) and effective_at.tzinfo is not None
        posting = _reveal_pg16_service_database_error(
            api_engine,
            lambda: post_inventory_transaction(
                session,
                actor=current_principal(session, actor_user_id),
                command=InventoryPostingCommand(
                    transaction_no="PG16-STOCKTAKE-SEED-INBOUND-1",
                    movement_type="inbound",
                    source_document_type="pg16_stocktake_release_fixture",
                    source_document_id=str(fixture["account_id"]),
                    posting_key="pg16-stocktake-release-fixture-inbound-1",
                    effective_at=effective_at,
                    movements=(
                        InventoryMovementCommand(
                            from_account_id=None,
                            to_account_id=fixture["account_id"],
                            quantity=Decimal("5.000"),
                            external_boundary_code="PG16_RELEASE_FIXTURE",
                        ),
                    ),
                ),
                idempotency_key="pg16-stocktake-release-fixture-inbound-1",
                request_id="trace-pg16-stocktake-release-fixture-inbound-1",
            )
        )
        session.commit()
        fixture["cutoff_ledger_cursor"] = posting.ledger_cursor
        fixture["seed_transaction_id"] = posting.transaction_id
    return fixture


def _complete_0051_nonopening_stocktake_service_chain(
    api_engine,
    *,
    task_id: uuid.UUID,
    initial_round_id: uuid.UUID,
    actor_user_id: str,
    assignee_user_id: str,
    account_id: uuid.UUID,
    idempotency_hmac_secret: bytes,
) -> int:
    """Persist each independent non-opening lifecycle fact, then round-trip 0051."""

    from app.formal_services.stocktake_close import (
        CloseReconciledStocktakeCommand,
        ReconcileStocktakeForCloseCommand,
        close_reconciled_stocktake,
        reconcile_posted_stocktake_for_close,
    )
    from app.formal_services.stocktake_count import (
        StocktakeSnapshotCountInput,
        SubmitStocktakeInitialScopeCountCommand,
        submit_stocktake_initial_scope_count,
    )
    from app.formal_services.stocktake_difference import (
        GenerateStocktakeDifferenceCommand,
        generate_stocktake_initial_differences,
    )
    from app.formal_services.stocktake_posting import (
        PostApprovedStocktakeDifferencesCommand,
        post_approved_stocktake_differences,
    )
    from app.formal_services.stocktake_review import (
        SubmitStocktakeReviewCommand,
        submit_stocktake_headquarters_review,
        submit_stocktake_region_review,
    )
    from app.stocktake_models import (
        FormalStocktakeScope,
        FormalStocktakeTask,
        StocktakeCloseCompletion,
        StocktakeCloseReconciliationCompletion,
        StocktakeDifferenceSetCompletion,
        StocktakePostingCompletion,
        StocktakeReview,
        StocktakeRound,
        StocktakeRoundSubmission,
        StocktakeScopeCountCompletion,
    )
    from test_material_request_approval_service import _principal

    def lifecycle_state() -> tuple[object, ...]:
        with Session(api_engine) as session:
            task = session.get(FormalStocktakeTask, task_id)
            assert task is not None
            review_stages = frozenset(
                session.scalars(
                    select(StocktakeReview.review_stage).where(
                        StocktakeReview.task_id == task_id
                    )
                ).all()
            )
            return (
                task.task_type,
                task.status,
                task.version,
                review_stages,
                session.scalar(
                    select(func.count())
                    .select_from(StocktakePostingCompletion)
                    .where(StocktakePostingCompletion.task_id == task_id)
                ),
                session.scalar(
                    select(func.count())
                    .select_from(StocktakeCloseReconciliationCompletion)
                    .where(
                        StocktakeCloseReconciliationCompletion.task_id
                        == task_id
                    )
                ),
                session.scalar(
                    select(func.count())
                    .select_from(StocktakeCloseCompletion)
                    .where(StocktakeCloseCompletion.task_id == task_id)
                ),
            )

    with Session(api_engine, expire_on_commit=False) as session:
        scope = session.scalar(
            select(FormalStocktakeScope).where(
                FormalStocktakeScope.task_id == task_id
            )
        )
        task = session.get(FormalStocktakeTask, task_id)
        round_row = session.get(StocktakeRound, initial_round_id)
        assert scope is not None and task is not None and round_row is not None
        assert (task.task_type, task.status, round_row.status) == (
            "sample",
            "counting",
            "counting",
        )
        counted = _reveal_pg16_service_database_error(
            api_engine,
            lambda: submit_stocktake_initial_scope_count(
                session,
                actor=_principal(session, assignee_user_id),
                command=SubmitStocktakeInitialScopeCountCommand(
                    task_id=task_id,
                    round_id=initial_round_id,
                    scope_id=scope.id,
                    count_mode="blind",
                    account_counts=(
                        StocktakeSnapshotCountInput(
                            stock_account_id=account_id,
                            counted_qty=Decimal("5.000"),
                        ),
                    ),
                ),
                idempotency_key="pg16-sample-stocktake-count-v1",
                idempotency_hmac_secret=idempotency_hmac_secret,
                trace_request_id="trace-pg16-sample-stocktake-count-v1",
            ),
        )
        assert counted.scope_completed is True
        assert counted.round_submitted is True
        assert (counted.task_status, counted.round_status) == (
            "submitted",
            "submitted",
        )
        session.commit()

    # Count is durable while review, posting, reconciliation and closure are
    # still independently absent.
    assert lifecycle_state() == (
        "sample",
        "submitted",
        counted.task_version,
        frozenset(),
        0,
        0,
        0,
    )

    with Session(api_engine, expire_on_commit=False) as session:
        evaluated = _reveal_pg16_service_database_error(
            api_engine,
            lambda: generate_stocktake_initial_differences(
                session,
                actor=_principal(session, actor_user_id),
                command=GenerateStocktakeDifferenceCommand(
                    task_id=task_id,
                    round_id=initial_round_id,
                    expected_task_version=counted.task_version,
                ),
                idempotency_key="pg16-sample-stocktake-difference-v1",
                idempotency_hmac_secret=idempotency_hmac_secret,
                trace_request_id="trace-pg16-sample-stocktake-difference-v1",
            ),
        )
        assert evaluated.task_status == "submitted"
        assert evaluated.round_status == "submitted"
        assert evaluated.difference_status == "evaluated"
        assert evaluated.difference_count == 0
        assert evaluated.total_affected_qty == Decimal("0.000")
        session.commit()

    assert lifecycle_state() == (
        "sample",
        "submitted",
        evaluated.task_version,
        frozenset(),
        0,
        0,
        0,
    )
    with Session(api_engine) as session:
        difference_completion = session.get(
            StocktakeDifferenceSetCompletion,
            evaluated.completion_id,
        )
        submission = session.scalar(
            select(StocktakeRoundSubmission).where(
                StocktakeRoundSubmission.task_id == task_id,
                StocktakeRoundSubmission.round_id == initial_round_id,
            )
        )
        sealing = session.scalar(
            select(StocktakeScopeCountCompletion).where(
                StocktakeScopeCountCompletion.task_id == task_id,
                StocktakeScopeCountCompletion.round_id == initial_round_id,
            )
        )
        assert difference_completion is not None
        assert submission is not None and sealing is not None
        assert submission.sealing_completion_id == sealing.id
        assert difference_completion.round_submission_id == submission.id
        assert difference_completion.completed_at >= submission.submitted_at
        for authorization_sha256 in (
            sealing.authorization_sha256,
            difference_completion.authorization_sha256,
        ):
            assert len(authorization_sha256) == 64
            assert authorization_sha256 == authorization_sha256.lower()
            assert set(authorization_sha256) <= set("0123456789abcdef")

    with Session(api_engine, expire_on_commit=False) as session:
        regional = _reveal_pg16_service_database_error(
            api_engine,
            lambda: submit_stocktake_region_review(
                session,
                actor=_principal(session, assignee_user_id),
                command=SubmitStocktakeReviewCommand(
                    task_id=task_id,
                    round_id=initial_round_id,
                    expected_task_version=evaluated.task_version,
                    decision="approve",
                    items=(),
                    comment="PG16 sample 盘点区域零差异复核通过",
                ),
                idempotency_key="pg16-sample-stocktake-region-review-v1",
                idempotency_hmac_secret=idempotency_hmac_secret,
                trace_request_id="trace-pg16-sample-region-review-v1",
            ),
        )
        assert regional.review_stage == "region"
        assert regional.resulting_task_status == "hq_review"
        assert regional.ready_for_posting is False
        assert regional.item_count == 0
        session.commit()

    assert lifecycle_state() == (
        "sample",
        "hq_review",
        regional.task_version,
        frozenset({"region"}),
        0,
        0,
        0,
    )

    with Session(api_engine, expire_on_commit=False) as session:
        headquarters = _reveal_pg16_service_database_error(
            api_engine,
            lambda: submit_stocktake_headquarters_review(
                session,
                actor=_principal(session, actor_user_id),
                command=SubmitStocktakeReviewCommand(
                    task_id=task_id,
                    round_id=initial_round_id,
                    expected_task_version=regional.task_version,
                    decision="approve",
                    items=(),
                    comment="PG16 sample 盘点总部零差异复核通过",
                ),
                idempotency_key="pg16-sample-stocktake-hq-review-v1",
                idempotency_hmac_secret=idempotency_hmac_secret,
                trace_request_id="trace-pg16-sample-hq-review-v1",
            ),
        )
        assert headquarters.review_stage == "headquarters"
        assert headquarters.resulting_task_status == "approved"
        assert headquarters.ready_for_posting is True
        assert headquarters.item_count == 0
        session.commit()

    assert lifecycle_state() == (
        "sample",
        "approved",
        headquarters.task_version,
        frozenset({"region", "headquarters"}),
        0,
        0,
        0,
    )

    with Session(api_engine, expire_on_commit=False) as session:
        posted = _reveal_pg16_service_database_error(
            api_engine,
            lambda: post_approved_stocktake_differences(
                session,
                actor=_principal(session, actor_user_id),
                command=PostApprovedStocktakeDifferencesCommand(
                    task_id=task_id,
                    expected_task_version=headquarters.task_version,
                ),
                idempotency_key="pg16-sample-stocktake-post-v1",
                idempotency_hmac_secret=idempotency_hmac_secret,
                trace_request_id="trace-pg16-sample-stocktake-post-v1",
            ),
        )
        assert posted.resulting_task_status == "posted"
        assert posted.difference_count == 0
        assert posted.transaction_count == posted.movement_count == 0
        assert posted.total_quantity == Decimal("0.000")
        session.commit()

    assert lifecycle_state() == (
        "sample",
        "posted",
        posted.task_version,
        frozenset({"region", "headquarters"}),
        1,
        0,
        0,
    )

    with Session(api_engine, expire_on_commit=False) as session:
        reconciled = _reveal_pg16_service_database_error(
            api_engine,
            lambda: reconcile_posted_stocktake_for_close(
                session,
                actor=_principal(session, actor_user_id),
                command=ReconcileStocktakeForCloseCommand(
                    task_id=task_id,
                    expected_task_version=posted.task_version,
                ),
                idempotency_key="pg16-sample-stocktake-reconcile-v1",
                idempotency_hmac_secret=idempotency_hmac_secret,
                trace_request_id="trace-pg16-sample-reconcile-v1",
            ),
        )
        assert reconciled.resulting_task_status == "posted"
        assert reconciled.task_version == posted.task_version + 1
        assert reconciled.reconciliation_no == 1
        assert reconciled.book_total_qty == reconciled.physical_total_qty
        session.commit()

    # Reconciliation is durable but is not closure; the task must remain
    # posted until the distinct close command commits.
    assert lifecycle_state() == (
        "sample",
        "posted",
        reconciled.task_version,
        frozenset({"region", "headquarters"}),
        1,
        1,
        0,
    )

    with Session(api_engine, expire_on_commit=False) as session:
        closed = _reveal_pg16_service_database_error(
            api_engine,
            lambda: close_reconciled_stocktake(
                session,
                actor=_principal(session, actor_user_id),
                command=CloseReconciledStocktakeCommand(
                    task_id=task_id,
                    expected_task_version=reconciled.task_version,
                ),
                idempotency_key="pg16-sample-stocktake-close-v1",
                idempotency_hmac_secret=idempotency_hmac_secret,
                trace_request_id="trace-pg16-sample-stocktake-close-v1",
            ),
        )
        assert closed.resulting_task_status == "closed"
        assert closed.task_version == reconciled.task_version + 1
        assert closed.reconciliation_completion_id == reconciled.completion_id
        session.commit()

    expected_terminal_state = (
        "sample",
        "closed",
        closed.task_version,
        frozenset({"region", "headquarters"}),
        1,
        1,
        1,
    )
    assert lifecycle_state() == expected_terminal_state

    # The non-opening completion must remain intact after the complete service
    # graph commits.  Earlier-schema round trips are intentionally unavailable
    # once 0052 has durable opening history, because downgrade fails closed.
    assert lifecycle_state() == expected_terminal_state
    with Session(api_engine) as session:
        terminal_task = session.get(FormalStocktakeTask, task_id)
        completion = session.get(
            StocktakeDifferenceSetCompletion,
            evaluated.completion_id,
        )
        assert terminal_task is not None and completion is not None
        assert (terminal_task.task_type, terminal_task.status) == (
            "sample",
            "closed",
        )
        assert completion.control_difference_count == 0
        assert len(completion.authorization_sha256) == 64
        assert completion.authorization_sha256 == (
            completion.authorization_sha256.lower()
        )
        assert set(completion.authorization_sha256) <= set(
            "0123456789abcdef"
        )
    return closed.task_version


def _assert_0047_real_api_stocktake_start(
    api_engine,
    *,
    actor_user_id: str,
    assignee_user_id: str,
    material_request_id: uuid.UUID,
) -> tuple[uuid.UUID, int]:
    """Prove start guards, then finish only this stocktake's formal lifecycle."""

    from app.demand_models import MaterialRequest
    from app.formal_services.stocktake_task import (
        StocktakeTaskError,
        create_stocktake_task_draft,
        start_stocktake_task,
    )
    from app.foundation_models import AuditEvent, StateTransitionEvent
    from app.inventory_models import (
        InventoryLedgerHead,
        InventoryMovement,
        InventoryTransaction,
        StockBalance,
    )
    from app.stocktake_models import (
        FormalStocktakeScope,
        FormalStocktakeTask,
        InventoryFreeze,
        StocktakeRound,
        StocktakeSnapshotLine,
        StocktakeStartCompletion,
    )
    from app.stocktake_task_schemas import (
        StocktakeScopeSelectionIn,
        StocktakeTaskCreateIn,
        StocktakeTaskStartIn,
    )
    from app.formal_services.inventory_posting import INVENTORY_LEDGER_HEAD_ID
    from test_material_request_approval_service import _principal

    secret = b"pg16-stocktake-start-gate-secret-v1"
    fixture = _seed_0047_stocktake_inventory(
        api_engine,
        actor_user_id=actor_user_id,
        assignee_user_id=assignee_user_id,
    )
    create_key = "pg16-stocktake-start-create"
    start_key = "pg16-stocktake-start-command"
    draft = StocktakeTaskCreateIn(
        task_type="sample",
        region_org_id=fixture["region_org_id"],
        blind_count=True,
        scopes=(
            StocktakeScopeSelectionIn(
                owner_org_id=fixture["region_org_id"],
                location_id=fixture["location_id"],
                assignee_person_id=fixture["assignee_person_id"],
                scope_mode="location_all",
                material_id=None,
                condition_code=None,
                availability_bucket=None,
                freeze_mode="hard",
            ),
        ),
        deadline=fixture["deadline"],
        note="PG16 隔离门禁盘点启动",
    )

    with Session(api_engine) as session:
        neutral_request_before = session.execute(
            select(
                MaterialRequest.status,
                MaterialRequest.version,
                *(
                    getattr(MaterialRequest, field)
                    for field in MATERIAL_REQUEST_NEUTRAL_AXES
                ),
            ).where(MaterialRequest.id == material_request_id)
        ).one()
        inventory_before = (
            session.scalar(select(func.count()).select_from(InventoryTransaction)),
            session.scalar(select(func.count()).select_from(InventoryMovement)),
            session.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor,
            session.get(StockBalance, fixture["account_id"]).quantity,
        )
        created = _reveal_pg16_service_database_error(
            api_engine,
            lambda: create_stocktake_task_draft(
                session,
                actor=_principal(session, actor_user_id),
                draft=draft,
                idempotency_key=create_key,
                idempotency_hmac_secret=secret,
                trace_request_id="trace-pg16-stocktake-start-create",
            )
        )
        assert created.status == "draft"
        assert created.version == 0
        assert created.scope_count == 1
        session.commit()
    task_id = created.task_id

    # The service must independently rebuild the ledger head and selected
    # balance projection before it writes any start fact.  These corruptions
    # live only inside their transactions and are always rolled back.
    with Session(api_engine) as session:
        session.execute(
            text(
                "UPDATE public.stock_balances SET quantity = quantity + 1 "
                "WHERE stock_account_id = :account_id"
            ),
            {"account_id": fixture["account_id"]},
        )
        with pytest.raises(StocktakeTaskError) as balance_drift:
            start_stocktake_task(
                session,
                actor=_principal(session, actor_user_id),
                task_id=task_id,
                command=StocktakeTaskStartIn(expected_version=0),
                idempotency_key="pg16-stocktake-balance-drift",
                idempotency_hmac_secret=secret,
                trace_request_id="trace-pg16-stocktake-balance-drift",
            )
        assert balance_drift.value.code == "stocktake_balance_projection_drift"
        session.rollback()
    with Session(api_engine) as session:
        session.execute(
            text(
                "UPDATE public.inventory_ledger_heads "
                "SET next_cursor = next_cursor + 1 "
                "WHERE stream_key = 'inventory'"
            )
        )
        with pytest.raises(StocktakeTaskError) as ledger_drift:
            start_stocktake_task(
                session,
                actor=_principal(session, actor_user_id),
                task_id=task_id,
                command=StocktakeTaskStartIn(expected_version=0),
                idempotency_key="pg16-stocktake-ledger-drift",
                idempotency_hmac_secret=secret,
                trace_request_id="trace-pg16-stocktake-ledger-drift",
            )
        assert ledger_drift.value.code == "stocktake_ledger_projection_drift"
        session.rollback()

    with Session(api_engine) as session:
        started = _reveal_pg16_service_database_error(
            api_engine,
            lambda: start_stocktake_task(
                session,
                actor=_principal(session, actor_user_id),
                task_id=task_id,
                command=StocktakeTaskStartIn(expected_version=0),
                idempotency_key=start_key,
                idempotency_hmac_secret=secret,
                trace_request_id="trace-pg16-stocktake-start-command",
            )
        )
        assert started.status == "counting"
        assert started.version == 1
        assert started.cutoff_ledger_cursor == fixture["cutoff_ledger_cursor"]
        assert started.scope_count == 1
        assert started.snapshot_line_count == 1
        assert started.active_freeze_count == 1
        session.commit()

    with Session(api_engine) as session:
        task = session.get(FormalStocktakeTask, task_id)
        assert task is not None
        assert (
            task.status,
            task.version,
            task.current_round_no,
            task.cutoff_ledger_cursor,
        ) == ("counting", 1, 1, fixture["cutoff_ledger_cursor"])
        assert task.cutoff_at is not None
        assert task.issued_at is not None
        assert task.frozen_at is not None
        assert len(task.snapshot_manifest_sha256) == 64
        freezes = tuple(
            session.scalars(
                select(InventoryFreeze).where(InventoryFreeze.task_id == task_id)
            ).all()
        )
        snapshots = tuple(
            session.scalars(
                select(StocktakeSnapshotLine).where(
                    StocktakeSnapshotLine.task_id == task_id
                )
            ).all()
        )
        rounds = tuple(
            session.scalars(
                select(StocktakeRound).where(StocktakeRound.task_id == task_id)
            ).all()
        )
        completions = tuple(
            session.scalars(
                select(StocktakeStartCompletion).where(
                    StocktakeStartCompletion.task_id == task_id
                )
            ).all()
        )
        assert len(freezes) == len(snapshots) == len(rounds) == len(completions) == 1
        assert (freezes[0].status, freezes[0].freeze_mode) == ("active", "hard")
        assert snapshots[0].book_qty == Decimal("5.000")
        assert snapshots[0].ledger_cursor == fixture["cutoff_ledger_cursor"]
        assert rounds[0].id == started.initial_round_id
        assert (rounds[0].round_no, rounds[0].round_type, rounds[0].status) == (
            1,
            "initial",
            "counting",
        )
        completion = completions[0]
        assert completion.initial_round_id == rounds[0].id
        assert completion.expected_task_version == 0
        assert completion.started_task_version == 1
        assert completion.scope_count == 1
        assert completion.snapshot_line_count == 1
        assert completion.active_freeze_count == 1
        assert completion.scope_manifest_sha256 == task.scope_manifest_sha256
        assert completion.snapshot_manifest_sha256 == task.snapshot_manifest_sha256
        assert len(completion.authorization_sha256) == 64
        assert len(completion.graph_manifest_sha256) == 64
        assert set(completion.graph_manifest_sha256) != {"0"}
        transition_rows = tuple(
            session.execute(
                select(
                    StateTransitionEvent.from_status,
                    StateTransitionEvent.to_status,
                    StateTransitionEvent.reason,
                )
                .where(
                    StateTransitionEvent.aggregate_type == "stocktake_task",
                    StateTransitionEvent.aggregate_id == str(task_id),
                )
                .order_by(StateTransitionEvent.occurred_at, StateTransitionEvent.id)
            ).all()
        )
        assert transition_rows == (
            (None, "draft", "stocktake_task_created"),
            ("draft", "issued", "stocktake_task_issued"),
            ("issued", "frozen", "stocktake_task_frozen"),
            ("frozen", "counting", "stocktake_initial_round_started"),
        )
        audit_actions = tuple(
            session.scalars(
                select(AuditEvent.action)
                .where(
                    AuditEvent.aggregate_type == "stocktake_task",
                    AuditEvent.aggregate_id == str(task_id),
                )
                .order_by(AuditEvent.sequence_no)
            ).all()
        )
        assert audit_actions == (
            "stocktake.task.created",
            "stocktake.task.started",
        )
        assert (
            session.scalar(select(func.count()).select_from(InventoryTransaction)),
            session.scalar(select(func.count()).select_from(InventoryMovement)),
            session.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor,
            session.get(StockBalance, fixture["account_id"]).quantity,
        ) == inventory_before
        assert session.execute(
            select(
                MaterialRequest.status,
                MaterialRequest.version,
                *(
                    getattr(MaterialRequest, field)
                    for field in MATERIAL_REQUEST_NEUTRAL_AXES
                ),
            ).where(MaterialRequest.id == material_request_id)
        ).one() == neutral_request_before

    with Session(api_engine) as session:
        replay = _reveal_pg16_service_database_error(
            api_engine,
            lambda: start_stocktake_task(
                session,
                actor=_principal(session, actor_user_id),
                task_id=task_id,
                command=StocktakeTaskStartIn(expected_version=0),
                idempotency_key=start_key,
                idempotency_hmac_secret=secret,
                trace_request_id="trace-pg16-stocktake-start-replay",
            )
        )
        assert replay == replace(started, replayed=True)
        session.commit()
    with Session(api_engine) as session:
        assert session.scalar(
            select(func.count()).select_from(StocktakeStartCompletion).where(
                StocktakeStartCompletion.task_id == task_id
            )
        ) == 1
        with pytest.raises(StocktakeTaskError) as mismatch:
            start_stocktake_task(
                session,
                actor=_principal(session, actor_user_id),
                task_id=task_id,
                command=StocktakeTaskStartIn(expected_version=1),
                idempotency_key=start_key,
                idempotency_hmac_secret=secret,
                trace_request_id="trace-pg16-stocktake-start-mismatch",
            )
        assert mismatch.value.code == "stocktake_idempotency_conflict"
        session.rollback()

    # Bypass only the service precheck in this disposable gate to prove the
    # database independently rejects a second task whose wildcard dimensions
    # overlap the first task's active freeze.
    import app.formal_services.stocktake_task as stocktake_service
    from unittest.mock import patch

    with Session(api_engine) as session:
        overlapping = _reveal_pg16_service_database_error(
            api_engine,
            lambda: create_stocktake_task_draft(
                session,
                actor=_principal(session, actor_user_id),
                draft=replace(draft, note="PG16 跨任务冻结重叠拒绝样本"),
                idempotency_key="pg16-stocktake-overlap-create",
                idempotency_hmac_secret=secret,
                trace_request_id="trace-pg16-stocktake-overlap-create",
            )
        )
        session.commit()
    with patch.object(
        stocktake_service,
        "_require_no_active_overlapping_freeze",
        return_value=None,
    ):
        with Session(api_engine) as session:
            with pytest.raises(
                SanitizedPostgreSQLDiagnosticError
            ) as overlap_failure:
                _reveal_pg16_service_database_error(
                    api_engine,
                    lambda: start_stocktake_task(
                        session,
                        actor=_principal(session, actor_user_id),
                        task_id=overlapping.task_id,
                        command=StocktakeTaskStartIn(expected_version=0),
                        idempotency_key="pg16-stocktake-overlap-start",
                        idempotency_hmac_secret=secret,
                        trace_request_id="trace-pg16-stocktake-overlap-start",
                    )
                )
            assert "non-opening stocktake start causality is invalid" in str(
                overlap_failure.value
            )
            assert "sqlstate=\"P0001\"" in str(overlap_failure.value)
            session.rollback()
    with Session(api_engine) as session:
        overlapping_task = session.get(FormalStocktakeTask, overlapping.task_id)
        assert overlapping_task is not None
        assert (overlapping_task.status, overlapping_task.version) == ("draft", 0)
        assert session.scalar(
            select(func.count()).select_from(StocktakeStartCompletion).where(
                StocktakeStartCompletion.task_id == overlapping.task_id
            )
        ) == 0

    # A runtime writer now owns only the minimum columns needed by the real
    # service.  Updating all of them directly still cannot forge a start,
    # because the completion and the rest of the graph are absent.
    with Session(api_engine) as session:
        forged_draft = _reveal_pg16_service_database_error(
            api_engine,
            lambda: create_stocktake_task_draft(
                session,
                actor=_principal(session, actor_user_id),
                draft=replace(draft, note="PG16 直接伪造启动拒绝样本"),
                idempotency_key="pg16-stocktake-forged-start-draft",
                idempotency_hmac_secret=secret,
                trace_request_id="trace-pg16-stocktake-forged-start-draft",
            )
        )
        session.commit()
    with Session(api_engine) as session:
        session.execute(
            text(
                "UPDATE public.stocktake_tasks SET "
                "status = 'counting', "
                "cutoff_ledger_cursor = :cutoff, cutoff_at = CURRENT_TIMESTAMP, "
                "snapshot_manifest_sha256 = :manifest, current_round_no = 1, "
                "issued_at = CURRENT_TIMESTAMP, frozen_at = CURRENT_TIMESTAMP, "
                "version = 1, updated_at = CURRENT_TIMESTAMP "
                "WHERE id = :task_id"
            ),
            {
                "cutoff": fixture["cutoff_ledger_cursor"],
                "manifest": "0" * 64,
                "task_id": forged_draft.task_id,
            },
        )
        with pytest.raises(DBAPIError) as forged_failure:
            session.commit()
        assert "non-opening stocktake start causality is invalid" in str(
            forged_failure.value
        )
        session.rollback()
    with Session(api_engine) as session:
        durable_draft = session.get(FormalStocktakeTask, forged_draft.task_id)
        assert durable_draft is not None
        assert (
            durable_draft.status,
            durable_draft.version,
            durable_draft.current_round_no,
            durable_draft.cutoff_ledger_cursor,
            durable_draft.cutoff_at,
            durable_draft.snapshot_manifest_sha256,
            durable_draft.issued_at,
            durable_draft.frozen_at,
        ) == ("draft", 0, 0, None, None, None, None, None)
        assert session.scalar(
            select(func.count()).select_from(StocktakeStartCompletion).where(
                StocktakeStartCompletion.task_id == forged_draft.task_id
            )
        ) == 0

    # Once completion exists, no later scope row may extend the sealed start
    # boundary, even if every ordinary FK/region check would otherwise pass.
    with Session(api_engine) as session:
        scope_count_before = session.scalar(
            select(func.count()).select_from(FormalStocktakeScope).where(
                FormalStocktakeScope.task_id == task_id
            )
        )
        with pytest.raises(DBAPIError) as extra_scope_failure:
            session.execute(
                text(
                    "INSERT INTO public.stocktake_scopes ("
                    "id, task_id, scope_no, scope_mode, location_id, owner_org_id, "
                    "custodian_person_id_snapshot, assignee_user_id, material_id, "
                    "condition_code, availability_bucket, scope_key, scope_sha256, "
                    "created_at) "
                    "SELECT :new_id, task_id, scope_no + 1, 'filtered', location_id, "
                    "owner_org_id, custodian_person_id_snapshot, assignee_user_id, "
                    ":material_id, condition_code, availability_bucket, :scope_key, "
                    ":scope_sha256, CURRENT_TIMESTAMP "
                    "FROM public.stocktake_scopes WHERE task_id = :task_id "
                    "ORDER BY scope_no LIMIT 1"
                ),
                {
                    "material_id": fixture["material_id"],
                    "new_id": uuid.uuid4(),
                    "scope_key": f"sealed-extra:{uuid.uuid4()}",
                    "scope_sha256": uuid.uuid4().hex + uuid.uuid4().hex,
                    "task_id": task_id,
                },
            )
            session.commit()
        assert "non-opening stocktake start causality is invalid" in str(
            extra_scope_failure.value
        )
        session.rollback()
    with Session(api_engine) as session:
        assert session.scalar(
            select(func.count()).select_from(FormalStocktakeScope).where(
                FormalStocktakeScope.task_id == task_id
            )
        ) == scope_count_before

    # The same deferred validator seals an already completed start graph.
    with Session(api_engine) as session:
        session.execute(
            text(
                "UPDATE public.stocktake_tasks "
                "SET cutoff_at = cutoff_at + INTERVAL '1 microsecond' "
                "WHERE id = :task_id"
            ),
            {"task_id": task_id},
        )
        with pytest.raises(DBAPIError) as drift_failure:
            session.commit()
        assert "non-opening stocktake start causality is invalid" in str(
            drift_failure.value
        )
        session.rollback()

    # Completion evidence is append-only for the API role.
    with Session(api_engine) as session:
        with pytest.raises(DBAPIError) as completion_update_failure:
            session.execute(
                text(
                    "UPDATE public.stocktake_start_completions "
                    "SET scope_count = scope_count WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
        assert isinstance(
            completion_update_failure.value.orig,
            psycopg.errors.InsufficientPrivilege,
        )
        session.rollback()
    with Session(api_engine) as session:
        with pytest.raises(DBAPIError) as completion_delete_failure:
            session.execute(
                text(
                    "DELETE FROM public.stocktake_start_completions "
                    "WHERE task_id = :task_id"
                ),
                {"task_id": task_id},
            )
        assert isinstance(
            completion_delete_failure.value.orig,
            psycopg.errors.InsufficientPrivilege,
        )
        session.rollback()

    # Normal roles cannot select replica mode.  The disposable cluster owner
    # may select it only for this proof, then assumes the migration role; the
    # 0047 ENABLE ALWAYS trigger must still reject snapshot drift.
    with Session(api_engine) as session:
        with pytest.raises(DBAPIError) as parameter_failure:
            session.execute(
                text("SET LOCAL session_replication_role = 'replica'")
            )
        assert isinstance(
            parameter_failure.value.orig,
            psycopg.errors.InsufficientPrivilege,
        )
        session.rollback()
    with Session(api_engine) as session:
        snapshot_before = session.execute(
            select(StocktakeSnapshotLine.id, StocktakeSnapshotLine.book_qty).where(
                StocktakeSnapshotLine.task_id == task_id
            )
        ).one()
    replica_engine = create_engine(
        _admin_sqlalchemy_url(),
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    try:
        with Session(replica_engine) as session:
            session.execute(
                text("SET LOCAL session_replication_role = 'replica'")
            )
            session.execute(text("SET LOCAL ROLE star_oam_migrator"))
            assert session.execute(
                text(
                    "SELECT current_user, session_user, "
                    "current_setting('session_replication_role')"
                )
            ).one() == ("star_oam_migrator", "postgres", "replica")
            session.execute(
                text(
                    "UPDATE public.stocktake_snapshot_lines "
                    "SET book_qty = book_qty + 1 WHERE id = :snapshot_id"
                ),
                {"snapshot_id": snapshot_before.id},
            )
            with pytest.raises(DBAPIError) as replica_failure:
                session.commit()
            assert "non-opening stocktake start causality is invalid" in str(
                replica_failure.value
            )
            session.rollback()
    finally:
        replica_engine.dispose()
    with Session(api_engine) as session:
        assert session.execute(
            select(StocktakeSnapshotLine.id, StocktakeSnapshotLine.book_qty).where(
                StocktakeSnapshotLine.task_id == task_id
            )
        ).one() == snapshot_before
    terminal_version = _complete_0051_nonopening_stocktake_service_chain(
        api_engine,
        task_id=task_id,
        initial_round_id=started.initial_round_id,
        actor_user_id=actor_user_id,
        assignee_user_id=assignee_user_id,
        account_id=fixture["account_id"],
        idempotency_hmac_secret=secret,
    )
    with Session(api_engine) as session:
        assert (
            session.scalar(select(func.count()).select_from(InventoryTransaction)),
            session.scalar(select(func.count()).select_from(InventoryMovement)),
            session.get(InventoryLedgerHead, INVENTORY_LEDGER_HEAD_ID).next_cursor,
            session.get(StockBalance, fixture["account_id"]).quantity,
        ) == inventory_before
        assert session.execute(
            select(
                MaterialRequest.status,
                MaterialRequest.version,
                *(
                    getattr(MaterialRequest, field)
                    for field in MATERIAL_REQUEST_NEUTRAL_AXES
                ),
            ).where(MaterialRequest.id == material_request_id)
        ).one() == neutral_request_before
    return task_id, terminal_version


def _assert_0047_rejects_nonempty_start_downgrade(
    api_engine,
    *,
    task_id: uuid.UUID,
    expected_task_version: int,
) -> None:
    from app.stocktake_models import FormalStocktakeTask, StocktakeStartCompletion

    assert _current_revision() == HEAD_REVISION
    migration = _load_opening_terminal_guard_execution_migration_0052()
    blocked = _run_alembic(
        "downgrade",
        CONTENT_CAUSALITY_REVISION,
        expect_success=False,
    )
    assert migration.DOWNGRADE_BLOCKER in (blocked.stdout + blocked.stderr)
    assert _current_revision() == HEAD_REVISION
    _assert_0052_opening_terminal_catalog(
        hardened=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0051_difference_completion_catalog(
        repaired=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0051_api_direct_execute_denied()
    _assert_0048_scope_guard_catalog(
        security_definer=True,
        expected_revision=HEAD_REVISION,
    )
    _assert_0049_recount_guard_catalog(
        callers_security_definer=True,
        expected_revision=HEAD_REVISION,
    )
    with Session(api_engine) as session:
        task = session.get(FormalStocktakeTask, task_id)
        assert task is not None
        assert (task.task_type, task.status, task.version) == (
            "sample",
            "closed",
            expected_task_version,
        )
        completion = session.scalar(
            select(StocktakeStartCompletion).where(
                StocktakeStartCompletion.task_id == task_id
            )
        )
        assert completion is not None
        assert len(completion.graph_manifest_sha256) == 64


def _wait_for_backend_lock(backend_pid: int) -> None:
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        with psycopg.connect(**_admin_parameters()) as connection:
            with connection.cursor() as cursor:
                cursor.execute(
                    "SELECT wait_event_type FROM pg_stat_activity WHERE pid = %s",
                    (backend_pid,),
                )
                row = cursor.fetchone()
        if row is not None and row[0] == "Lock":
            return
        time.sleep(0.05)
    pytest.fail("expected PostgreSQL backend did not reach a lock wait")


def _insert_concurrency_fixture(challenge_id: uuid.UUID) -> datetime:
    now = datetime.now(timezone.utc)
    with psycopg.connect(
        **_connection_parameters(
            role="star_oam_migrator",
            password=_role_password("star_oam_migrator"),
        )
    ) as connection:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                INSERT INTO login_challenges (
                    id, mobile_hash, code_hash, verification_mode, provider,
                    provider_reference, client_type, purpose, attempts,
                    max_attempts, expires_at, status, idempotency_key,
                    requested_ip_hash, verified_at, consumed_at, created_at
                ) VALUES (
                    %s, %s, NULL, 'provider_managed', 'aliyun_dypns', NULL,
                    'web', 'login', 0, 5, %s, 'pending', %s, %s,
                    NULL, NULL, %s
                )
                """,
                (
                    challenge_id,
                    "c" * 64,
                    now + timedelta(minutes=5),
                    f"pg16-concurrency-{challenge_id}",
                    "d" * 64,
                    now,
                ),
            )
            cursor.execute(
                """
                INSERT INTO sms_challenge_dispatches (
                    challenge_id, provider, mobile_hash, status,
                    request_sha256, owner_token_hash, provider_reference,
                    claimed_at, lease_expires_at, accepted_at, uncertain_at,
                    expired_at, created_at
                ) VALUES (
                    %s, 'aliyun_dypns', %s, 'prepared', %s, NULL, NULL,
                    NULL, NULL, NULL, NULL, NULL, %s
                )
                """,
                (challenge_id, "c" * 64, "e" * 64, now),
            )
    return now


def _assert_single_owner_and_process_kill(api_engine) -> None:
    from app.formal_services import authentication_challenge

    challenge_id = uuid.uuid4()
    claim_time = _insert_concurrency_fixture(challenge_id)
    first_ready = threading.Event()
    second_started = threading.Event()
    release_first = threading.Event()
    claims: list[object] = []
    errors: list[BaseException] = []
    second_pid: list[int] = []

    def first_claim():
        try:
            with Session(api_engine) as session:
                result = authentication_challenge.claim_dispatch(
                    session,
                    challenge_id=challenge_id,
                    request_id="pg16-owner-first",
                    lease_seconds=60,
                    now=claim_time,
                )
                claims.append(result)
                first_ready.set()
                assert release_first.wait(timeout=10)
                session.commit()
        except BaseException as exc:  # pragma: no cover - CI failure evidence
            errors.append(exc)
            first_ready.set()

    def second_claim():
        try:
            assert first_ready.wait(timeout=10)
            with Session(api_engine) as session:
                second_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
                second_started.set()
                result = authentication_challenge.claim_dispatch(
                    session,
                    challenge_id=challenge_id,
                    request_id="pg16-owner-second",
                    lease_seconds=60,
                    now=claim_time,
                )
                claims.append(result)
                session.commit()
        except BaseException as exc:  # pragma: no cover - CI failure evidence
            errors.append(exc)

    with ThreadPoolExecutor(max_workers=2) as executor:
        first_future = executor.submit(first_claim)
        assert first_ready.wait(timeout=10)
        second_future = executor.submit(second_claim)
        assert second_started.wait(timeout=10)
        assert second_pid
        _wait_for_backend_lock(second_pid[0])
        release_first.set()
        first_future.result(timeout=15)
        second_future.result(timeout=15)

    assert errors == []
    assert len(claims) == 2
    acquired = [claim for claim in claims if claim.acquired]
    assert len(acquired) == 1
    owner_token = acquired[0].owner_token
    assert owner_token

    authorized = threading.Event()
    release_killed_session = threading.Event()
    worker_pid: list[int] = []
    worker_errors: list[BaseException] = []
    contender_started = threading.Event()
    contender_pid: list[int] = []
    contender_claims: list[object] = []
    contender_errors: list[BaseException] = []

    def hold_provider_authorization():
        try:
            with Session(api_engine) as session:
                worker_pid.append(session.scalar(text("SELECT pg_backend_pid()")))
                assert authentication_challenge.authorize_dispatch_provider_call(
                    session,
                    challenge_id=challenge_id,
                    owner_token=owner_token,
                    request_id="pg16-provider-kill",
                    minimum_remaining_seconds=1,
                    now=claim_time + timedelta(seconds=1),
                )
                authorized.set()
                assert release_killed_session.wait(timeout=10)
                session.commit()
        except BaseException as exc:  # expected after pg_terminate_backend
            worker_errors.append(exc)
            authorized.set()

    def blocked_contender():
        try:
            assert authorized.wait(timeout=10)
            with Session(api_engine) as session:
                contender_pid.append(
                    session.scalar(text("SELECT pg_backend_pid()"))
                )
                contender_started.set()
                result = authentication_challenge.claim_dispatch(
                    session,
                    challenge_id=challenge_id,
                    request_id="pg16-provider-window-contender",
                    lease_seconds=60,
                    now=claim_time + timedelta(seconds=2),
                )
                contender_claims.append(result)
                session.commit()
        except BaseException as exc:  # pragma: no cover - CI failure evidence
            contender_errors.append(exc)
            contender_started.set()

    with ThreadPoolExecutor(max_workers=2) as executor:
        owner_future = executor.submit(hold_provider_authorization)
        assert authorized.wait(timeout=10)
        assert worker_pid
        contender_future = executor.submit(blocked_contender)
        assert contender_started.wait(timeout=10)
        assert contender_pid
        _wait_for_backend_lock(contender_pid[0])
        with psycopg.connect(**_admin_parameters(), autocommit=True) as connection:
            with connection.cursor() as cursor:
                cursor.execute("SELECT pg_terminate_backend(%s)", (worker_pid[0],))
                assert cursor.fetchone()[0] is True
        release_killed_session.set()
        owner_future.result(timeout=15)
        contender_future.result(timeout=15)

    assert worker_errors
    assert contender_errors == []
    assert len(contender_claims) == 1
    assert contender_claims[0].acquired is False
    assert contender_claims[0].dispatch_status == "sending"
    with api_engine.connect() as connection:
        assert connection.scalar(
            text(
                "SELECT status FROM sms_challenge_dispatches "
                "WHERE challenge_id = :challenge_id"
            ),
            {"challenge_id": challenge_id},
        ) == "sending"

    with Session(api_engine) as session:
        replay = authentication_challenge.claim_dispatch(
            session,
            challenge_id=challenge_id,
            request_id="pg16-owner-after-kill",
            lease_seconds=60,
            now=claim_time + timedelta(seconds=61),
        )
        session.commit()
    assert replay.acquired is False
    assert replay.dispatch_status == "uncertain"


def test_postgresql16_migration_acl_concurrency_and_kill_gate():
    _assert_fresh_disposable_postgresql16()
    _bootstrap_roles()
    _assert_edge_receiver_provision_rolls_back_on_cross_database_connect()
    _provision_edge_receiver_role()
    _assert_0052_legacy_backfill_and_atomic_rejection()

    _run_alembic("upgrade", "20260902_0042")
    assert _current_revision() == "20260902_0042"
    _assert_0043_rejects_projector_membership_drift()
    _assert_0043_rejects_projector_cross_schema_drift()
    _assert_0043_rejects_unrevocable_parameter_acl()
    pre_0043_large_object_oid = _inject_pre_0043_projector_acl_drift()
    _run_alembic("upgrade", "20260902_0043")
    assert _current_revision() == "20260902_0043"
    _assert_0044_rejects_stray_permissive_policy()
    _assert_0044_rejects_untrusted_0043_projection_graph()
    _assert_0044_preflight_serializes_projector_writer()
    _run_alembic("upgrade", "head")
    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    _assert_0052_empty_graph_downgrade_and_reupgrade()
    _assert_0051_empty_graph_downgrade_and_reupgrade()
    _assert_0050_empty_graph_downgrade_and_reupgrade()
    _assert_0049_empty_graph_downgrade_and_reupgrade()
    _assert_0048_empty_graph_downgrade_and_reupgrade()
    _assert_0047_empty_graph_downgrade_and_reupgrade()
    _assert_0046_empty_graph_downgrade_and_reupgrade()
    assert _work_order_lock_function_exists() is True
    _assert_pre_0043_acl_drift_was_cleaned(pre_0043_large_object_oid)
    _assert_projector_exact_column_acl()
    unbound_source_id = _seed_0044_unbound_source()
    _provision_and_verify_deployment_acl()
    _assert_request_file_guard_execution_boundary(security_definer=True)
    _assert_decision_guard_variable_boundary(repaired=True)
    _assert_0044_zero_binding_default_denies(unbound_source_id)
    _provision_and_verify_oam_work_order_source()
    _assert_0044_bound_scope_attack_matrix(unbound_source_id)

    projector_engine = create_engine(
        _sqlalchemy_url(
            role="star_oam_projector",
            password=_role_password("star_oam_projector"),
        ),
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    edge_engine = create_engine(
        _sqlalchemy_url(
            role=EDGE_RECEIVER_ROLE,
            password=_role_password(EDGE_RECEIVER_ROLE),
        ),
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    try:
        _validate_projector_security(projector_engine)
        _validate_edge_security(edge_engine)
        _assert_cross_database_connections_denied()
        _assert_projector_real_publish_paths(projector_engine)
    finally:
        edge_engine.dispose()
        projector_engine.dispose()

    _run_alembic("downgrade", RLS_REVISION)
    assert _current_revision() == RLS_REVISION
    _assert_request_file_guard_execution_boundary(security_definer=False)
    _assert_decision_guard_variable_boundary(repaired=False)
    _assert_0044_rejects_nonempty_sync_downgrade()
    _clear_disposable_oam_sync_graph()
    _run_alembic("downgrade", "20260902_0043")
    _assert_0044_downgrade_revokes_runtime_writes()
    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    _provision_and_verify_deployment_acl()
    _assert_request_file_guard_execution_boundary(security_definer=True)
    _assert_decision_guard_variable_boundary(repaired=True)
    _provision_and_verify_oam_work_order_source()
    _assert_0044_bound_scope_attack_matrix(unbound_source_id)

    _clear_disposable_oam_sync_graph()
    _run_alembic("downgrade", "20260902_0042")
    assert _current_revision() == "20260902_0042"
    assert _work_order_lock_function_exists() is True
    _assert_projector_acl_revoked()
    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    assert _work_order_lock_function_exists() is True

    _run_alembic("downgrade", "20260902_0041")
    assert _current_revision() == "20260902_0041"
    assert _work_order_lock_function_exists() is False
    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    assert _work_order_lock_function_exists() is True

    _run_alembic("downgrade", "20260901_0040")
    assert _current_revision() == "20260901_0040"
    assert _table_exists("sms_challenge_dispatches") is False

    blocked_challenge = uuid.uuid4()
    _insert_unexpired_preflight_challenge(blocked_challenge)
    blocked_upgrade = _run_alembic("upgrade", "head", expect_success=False)
    assert "0041 preflight failed" in (blocked_upgrade.stdout + blocked_upgrade.stderr)
    assert _current_revision() == "20260901_0040"
    assert _table_exists("sms_challenge_dispatches") is False
    _delete_challenge(blocked_challenge)

    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    _provision_and_verify_deployment_acl()
    _provision_and_verify_oam_work_order_source()
    api_engine = create_engine(
        _sqlalchemy_url(
            role="star_oam_api",
            password=_role_password("star_oam_api"),
        ),
        pool_size=4,
        max_overflow=0,
        pool_timeout=5,
    )
    projector_engine = create_engine(
        _sqlalchemy_url(
            role="star_oam_projector",
            password=_role_password("star_oam_projector"),
        ),
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    edge_engine = create_engine(
        _sqlalchemy_url(
            role=EDGE_RECEIVER_ROLE,
            password=_role_password(EDGE_RECEIVER_ROLE),
        ),
        pool_size=1,
        max_overflow=0,
        pool_timeout=5,
    )
    try:
        _validate_runtime_security(api_engine)
        _assert_0052_opening_terminal_catalog(
            hardened=True,
            expected_revision=HEAD_REVISION,
        )
        _assert_0052_api_direct_execute_denied()
        _assert_0052_startup_rejects_catalog_drift(api_engine)
        _assert_0051_difference_completion_catalog(
            repaired=True,
            expected_revision=HEAD_REVISION,
        )
        _assert_0051_api_direct_execute_denied()
        _assert_0051_startup_rejects_trigger_drift(api_engine)
        _assert_0049_recount_guard_catalog(
            callers_security_definer=True,
            expected_revision=HEAD_REVISION,
        )
        _assert_0049_api_direct_execute_denied()
        _assert_0049_startup_rejects_extra_trigger_alias(api_engine)
        _assert_0048_scope_guard_catalog(
            security_definer=True,
            expected_revision=HEAD_REVISION,
        )
        _assert_0048_scope_guard_master_data_stays_read_only()
        _assert_0045_approval_catalog_drift_is_rejected(api_engine)
        _validate_projector_security(projector_engine)
        _validate_edge_security(edge_engine)
        _assert_cross_database_connections_denied()
        _assert_sms_acl(api_engine)
        _assert_external_sync_scope_lock_serializes(
            api_engine,
            projector_engine,
        )
        _assert_external_sync_scope_session_lock_survives_commit(
            api_engine,
            projector_engine,
        )
        _assert_work_order_lock_acl_and_concurrency()
        _assert_database_owner_membership_boundary(api_engine)
        _assert_membership_drift_is_rejected(api_engine)
        _assert_projector_membership_drift_is_rejected(projector_engine)
        _assert_projector_cross_schema_drift_is_rejected(projector_engine)
        _assert_public_and_cluster_acl_drift_is_rejected(
            projector_engine,
            edge_engine,
        )
        _assert_single_owner_and_process_kill(api_engine)

        # The preceding 0042 work-order lock proof intentionally creates a
        # migrator-owned formal fixture.  Remove only the disposable CI sync
        # graph after that proof so the multi-revision downgrade can reach and
        # independently exercise 0041's nonempty SMS challenge blocker.
        _clear_disposable_oam_sync_graph()
        blocked_downgrade = _run_alembic(
            "downgrade", "20260901_0040", expect_success=False
        )
        assert "cannot downgrade 0041" in (
            blocked_downgrade.stdout + blocked_downgrade.stderr
        )
        assert _current_revision() == HEAD_REVISION

        (
            request_id,
            final_request_version,
            manager_user_id,
            admin_user_id,
        ) = (
            _assert_0045_raw_projection_bypass_and_formal_approval(api_engine)
        )
        _assert_0046_rejects_nonempty_content_downgrade(
            api_engine,
            request_id=request_id,
            expected_version=final_request_version,
        )
        # This first builds and closes an opening stocktake through the API
        # session.  Its scope insert is the positive 0048 regression: the
        # unchanged 0025 trigger may read-lock master data only through its
        # migration-owned execution context.
        _assert_0049_recount_guard_catalog(
            callers_security_definer=True,
            expected_revision=HEAD_REVISION,
        )
        (
            stocktake_task_id,
            stocktake_task_version,
        ) = _assert_0047_real_api_stocktake_start(
            api_engine,
            actor_user_id=admin_user_id,
            assignee_user_id=manager_user_id,
            material_request_id=request_id,
        )
        _assert_0049_recount_guard_catalog(
            callers_security_definer=True,
            expected_revision=HEAD_REVISION,
        )
        _assert_0047_rejects_nonempty_start_downgrade(
            api_engine,
            task_id=stocktake_task_id,
            expected_task_version=stocktake_task_version,
        )
    finally:
        edge_engine.dispose()
        projector_engine.dispose()
        api_engine.dispose()
