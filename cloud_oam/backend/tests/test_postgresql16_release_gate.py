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
from sqlalchemy import URL, create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session


CLOUD_ROOT = Path(__file__).resolve().parents[2]
ACKNOWLEDGEMENT = "I_UNDERSTAND_THIS_DATABASE_IS_EPHEMERAL"
DATABASE_NAME = "rsc_pg16_release_gate"
RLS_REVISION = "20260902_0044"
HEAD_REVISION = "20260903_0045"
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


def _sqlalchemy_url(*, role: str, password: str) -> URL:
    parameters = _connection_parameters(role=role, password=password)
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


def _migration_environment() -> dict[str, str]:
    migration_url = _sqlalchemy_url(
        role="star_oam_migrator",
        password=_role_password("star_oam_migrator"),
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
    *arguments: str, expect_success: bool = True
) -> subprocess.CompletedProcess[str]:
    completed = subprocess.run(
        [sys.executable, "-m", "alembic", "-c", "alembic.ini", *arguments],
        cwd=CLOUD_ROOT,
        env=_migration_environment(),
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


def _reveal_pg16_service_database_error(operation):
    """Expose only synthetic release-gate DB errors hidden by public services."""

    try:
        return operation()
    except Exception as exc:
        database_error = exc.__context__
        if isinstance(database_error, DBAPIError):
            raise database_error
        raise


def _assert_0045_raw_projection_bypass_and_formal_approval(
    api_engine,
) -> tuple[uuid.UUID, int]:
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

    # Draft creation is one committed formal action.
    with Session(api_engine) as session:
        created = _reveal_pg16_service_database_error(
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

    # Submission is independently committed and creates the sealed revision,
    # active instance, frozen candidates and three approval steps atomically.
    with Session(api_engine) as session:
        submitted = _reveal_pg16_service_database_error(
            lambda: submit_material_request(
                session,
                actor=_principal(session, requester_user_id),
                material_request_id=request_id,
                expected_version=created.request_version,
                idempotency_key="pg16-approval-projection-submit",
                idempotency_hmac_secret=SECRET,
                trace_request_id="trace-pg16-approval-projection-submit",
            )
        )
        session.commit()
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="approval_in_progress",
        expected_version=submitted.version,
        expected_decided_at=False,
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
    return request_id, cancelled_version


def _assert_0045_rejects_nonempty_approval_downgrade(
    api_engine,
    *,
    request_id: uuid.UUID,
    expected_version: int,
) -> None:
    assert _current_revision() == HEAD_REVISION
    blocked = _run_alembic("downgrade", RLS_REVISION, expect_success=False)
    assert "cannot downgrade 0045" in (blocked.stdout + blocked.stderr)
    assert _current_revision() == HEAD_REVISION
    _assert_material_request_snapshot(
        api_engine,
        request_id=request_id,
        expected_status="cancelled",
        expected_version=expected_version,
        expected_decided_at=True,
    )


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
    assert _work_order_lock_function_exists() is True
    _assert_pre_0043_acl_drift_was_cleaned(pre_0043_large_object_oid)
    _assert_projector_exact_column_acl()
    unbound_source_id = _seed_0044_unbound_source()
    _provision_and_verify_deployment_acl()
    _assert_request_file_guard_execution_boundary(security_definer=True)
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
    _assert_0044_rejects_nonempty_sync_downgrade()
    _clear_disposable_oam_sync_graph()
    _run_alembic("downgrade", "20260902_0043")
    _assert_0044_downgrade_revokes_runtime_writes()
    _run_alembic("upgrade", "head")
    assert _current_revision() == HEAD_REVISION
    _provision_and_verify_deployment_acl()
    _assert_request_file_guard_execution_boundary(security_definer=True)
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

        request_id, final_request_version = (
            _assert_0045_raw_projection_bypass_and_formal_approval(api_engine)
        )
        _assert_0045_rejects_nonempty_approval_downgrade(
            api_engine,
            request_id=request_id,
            expected_version=final_request_version,
        )
    finally:
        edge_engine.dispose()
        projector_engine.dispose()
        api_engine.dispose()
