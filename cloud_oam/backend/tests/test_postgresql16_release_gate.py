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
from sqlalchemy import URL, create_engine, text
from sqlalchemy.orm import Session


CLOUD_ROOT = Path(__file__).resolve().parents[2]
ACKNOWLEDGEMENT = "I_UNDERSTAND_THIS_DATABASE_IS_EPHEMERAL"
DATABASE_NAME = "rsc_pg16_release_gate"
ROLE_NAMES = (
    "star_oam_migrator",
    "star_oam_api",
    "star_oam_backup",
    "star_oam_edge",
)
WORK_ORDER_LOCK_FUNCTION = (
    "public.rsc_lock_material_request_work_order_reference_0042(uuid)"
)


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
            for role_name in ROLE_NAMES[:3]:
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
                sql.SQL(
                    "GRANT CONNECT ON DATABASE {} TO "
                    "star_oam_migrator, star_oam_api, star_oam_backup"
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
                "star_oam_backup, star_oam_edge"
            )
            cursor.execute(
                "GRANT USAGE, CREATE ON SCHEMA public TO star_oam_migrator"
            )
            cursor.execute(
                "GRANT USAGE ON SCHEMA public TO star_oam_api, star_oam_backup"
            )
            for role_name in ROLE_NAMES:
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

    _run_alembic("upgrade", "head")
    _run_alembic("upgrade", "head")
    assert _current_revision() == "20260902_0042"
    assert _work_order_lock_function_exists() is True

    _run_alembic("downgrade", "20260902_0041")
    assert _current_revision() == "20260902_0041"
    assert _work_order_lock_function_exists() is False
    _run_alembic("upgrade", "head")
    assert _current_revision() == "20260902_0042"
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
    api_engine = create_engine(
        _sqlalchemy_url(
            role="star_oam_api",
            password=_role_password("star_oam_api"),
        ),
        pool_size=4,
        max_overflow=0,
        pool_timeout=5,
    )
    try:
        _validate_runtime_security(api_engine)
        _assert_sms_acl(api_engine)
        _assert_work_order_lock_acl_and_concurrency()
        _assert_database_owner_membership_boundary(api_engine)
        _assert_membership_drift_is_rejected(api_engine)
        _assert_single_owner_and_process_kill(api_engine)

        blocked_downgrade = _run_alembic(
            "downgrade", "20260901_0040", expect_success=False
        )
        assert "cannot downgrade 0041" in (
            blocked_downgrade.stdout + blocked_downgrade.stderr
        )
        assert _current_revision() == "20260902_0042"
    finally:
        api_engine.dispose()
