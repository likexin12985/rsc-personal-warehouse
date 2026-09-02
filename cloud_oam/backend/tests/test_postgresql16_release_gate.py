"""Opt-in destructive tests for one GitHub-hosted PostgreSQL 16 service.

The ordinary backend suite never opens PostgreSQL.  This module runs only when
the exact acknowledgement, GitHub-hosted runner markers, loopback-only
coordinates, and a fresh whole cluster are all present.  Its target must be the
empty ``rsc_pg16_release_gate`` database created by the private-repository CI
service container.  No local, production, or shared database is acceptable.
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
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
from sqlalchemy.orm import Session


CLOUD_ROOT = Path(__file__).resolve().parents[2]
ACKNOWLEDGEMENT = "I_UNDERSTAND_THIS_DATABASE_IS_EPHEMERAL"
DATABASE_NAME = "rsc_pg16_release_gate"
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


def _projector_gate_payload(*, status: str) -> dict[str, object]:
    return {
        "id": PROJECTOR_GATE_WORK_ORDER_ID,
        "code": PROJECTOR_GATE_WORK_ORDER_NO,
        "statusCode": status,
        "executorId": PROJECTOR_GATE_EXECUTOR_ID,
        "authCompanyId": TEST_OAM_SOURCE_COORDINATES["company_id"],
        "province": "浙江省",
        "updateTime": PROJECTOR_GATE_SOURCE_TIME.astimezone(
            timezone(timedelta(hours=8))
        ).strftime("%Y-%m-%d %H:%M:%S"),
    }


def _stage_pg16_projector_snapshot(
    migrator_engine,
    *,
    external_snapshot_id: str,
    status: str,
    snapshot_at: datetime,
    poison_manifest: bool = False,
) -> str:
    from app.models import (
        ExternalSyncCurrentRecord,
        ExternalSyncSnapshot,
        ExternalSyncSnapshotBatch,
        ExternalSyncSnapshotRecord,
    )
    from app.schemas import EdgeSyncSnapshotCompleteIn

    payload = _projector_gate_payload(status=status)
    payload_json = _projector_gate_canonical(payload)
    payload_sha256 = hashlib.sha256(payload_json.encode("utf-8")).hexdigest()
    business_key = f"work-order:{PROJECTOR_GATE_WORK_ORDER_NO}"
    final_wire = [
        {
            "business_key": business_key,
            "source_updated_at": PROJECTOR_GATE_SOURCE_TIME.isoformat(),
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
                source_updated_at=PROJECTOR_GATE_SOURCE_TIME,
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
                source_updated_at=PROJECTOR_GATE_SOURCE_TIME,
                payload_json=payload_json,
                payload_sha256=payload_sha256,
                last_snapshot_id=snapshot.id,
                created_at=snapshot_at,
                updated_at=snapshot_at,
            )
            session.add(current)
        else:
            current.source_updated_at = PROJECTOR_GATE_SOURCE_TIME
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
            stable_row_id = row.id
            stable_external_id = external.id
            stable_version_id = version.id
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
    _run_alembic("upgrade", "head")
    _run_alembic("upgrade", "head")
    assert _current_revision() == "20260902_0043"
    assert _work_order_lock_function_exists() is True
    _assert_pre_0043_acl_drift_was_cleaned(pre_0043_large_object_oid)
    _assert_projector_exact_column_acl()
    _provision_and_verify_deployment_acl()
    _provision_and_verify_oam_work_order_source()

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

    _run_alembic("downgrade", "20260902_0042")
    assert _current_revision() == "20260902_0042"
    assert _work_order_lock_function_exists() is True
    _assert_projector_acl_revoked()
    _run_alembic("upgrade", "head")
    assert _current_revision() == "20260902_0043"
    assert _work_order_lock_function_exists() is True

    _run_alembic("downgrade", "20260902_0041")
    assert _current_revision() == "20260902_0041"
    assert _work_order_lock_function_exists() is False
    _run_alembic("upgrade", "head")
    assert _current_revision() == "20260902_0043"
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
    _provision_and_verify_deployment_acl()
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

        blocked_downgrade = _run_alembic(
            "downgrade", "20260901_0040", expect_success=False
        )
        assert "cannot downgrade 0041" in (
            blocked_downgrade.stdout + blocked_downgrade.stderr
        )
        assert _current_revision() == "20260902_0043"
    finally:
        edge_engine.dispose()
        projector_engine.dispose()
        api_engine.dispose()
