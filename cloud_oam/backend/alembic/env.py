from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool, text
from sqlalchemy.engine import make_url


config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)


def _configure_database_url() -> None:
    """Resolve the migration URL without importing application startup code."""

    environment_url = os.getenv("OAM_DATABASE_URL", "").strip()
    configured_url = config.get_main_option("sqlalchemy.url", "").strip()
    database_url = environment_url or configured_url
    if not database_url:
        raise RuntimeError(
            "Alembic requires OAM_DATABASE_URL or sqlalchemy.url; refusing to "
            "guess a database target."
        )
    if os.getenv("OAM_ENVIRONMENT", "").strip().lower() == "production":
        lowered_url = database_url.lower()
        placeholder_markers = (
            "replace-with",
            "replace_me",
            "replace-me",
            "change-me",
            "changeme",
        )
        if not database_url.startswith("postgresql+psycopg://") or any(
            marker in lowered_url for marker in placeholder_markers
        ):
            raise RuntimeError(
                "production Alembic requires an explicit non-placeholder "
                "postgresql+psycopg database URL"
            )
        expected_migration_role = os.getenv(
            "OAM_DATABASE_EXPECTED_MIGRATION_ROLE", ""
        ).strip()
        expected_runtime_role = os.getenv(
            "OAM_DATABASE_EXPECTED_RUNTIME_ROLE", ""
        ).strip()
        configured_username = make_url(database_url).username
        if (
            not expected_migration_role
            or not expected_runtime_role
            or expected_migration_role == expected_runtime_role
            or configured_username != expected_migration_role
        ):
            raise RuntimeError(
                "production Alembic requires distinct expected migration and "
                "runtime roles and must connect as the migration role"
            )
    # ConfigParser treats percent signs as interpolation tokens.
    config.set_main_option("sqlalchemy.url", database_url.replace("%", "%%"))


def _load_target_metadata():
    """Load ORM metadata only for an explicitly requested autogenerate run.

    Normal upgrades and downgrades stay independent of application settings and
    the current ORM.  This prevents a later model edit from changing the meaning
    of an already-reviewed revision.
    """

    if os.getenv("ALEMBIC_LOAD_MODEL_METADATA") != "1":
        return None

    from app.models import Base  # Imported lazily by design.

    return Base.metadata


_configure_database_url()
target_metadata = _load_target_metadata()


def run_migrations_offline() -> None:
    if os.getenv("OAM_ENVIRONMENT", "").strip().lower() == "production":
        raise RuntimeError(
            "production Alembic requires the online migration path so the "
            "connected role, ownership and search path can be verified"
        )
    context.configure(
        url=config.get_main_option("sqlalchemy.url"),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    connectable = engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
        future=True,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )

        with context.begin_transaction():
            if os.getenv("OAM_ENVIRONMENT", "").strip().lower() == "production":
                expected_role = os.environ[
                    "OAM_DATABASE_EXPECTED_MIGRATION_ROLE"
                ]
                expected_runtime_role = os.environ[
                    "OAM_DATABASE_EXPECTED_RUNTIME_ROLE"
                ]
                evidence = connection.execute(
                    text(
                        "SELECT current_user AS role_name, role_row.rolsuper, "
                        "role_row.rolcreatedb, role_row.rolcreaterole, "
                        "role_row.rolreplication, role_row.rolbypassrls, "
                        "current_setting('session_replication_role') "
                        "AS session_replication_role, "
                        "has_schema_privilege(current_user, 'public', 'CREATE') "
                        "AS can_create_in_schema, "
                        "current_schema() AS current_schema_name, "
                        "current_schemas(FALSE) AS current_schema_path, "
                        "EXISTS (SELECT 1 FROM pg_auth_members AS membership "
                        "WHERE membership.member = role_row.oid) "
                        "AS has_any_role_membership, "
                        "EXISTS (SELECT 1 FROM pg_auth_members AS membership "
                        "WHERE membership.roleid = role_row.oid) "
                        "AS has_any_role_members, "
                        "COALESCE((SELECT EXISTS (SELECT 1 "
                        "FROM pg_auth_members AS runtime_membership "
                        "WHERE runtime_membership.member = runtime_role.oid "
                        "OR runtime_membership.roleid = runtime_role.oid) "
                        "FROM pg_roles AS runtime_role "
                        "WHERE runtime_role.rolname = :runtime_role), TRUE) "
                        "AS runtime_role_has_any_membership, "
                        "(SELECT pg_get_userbyid(database_row.datdba) "
                        "FROM pg_database AS database_row "
                        "WHERE database_row.datname = current_database()) "
                        "AS database_owner, "
                        "NOT EXISTS (SELECT 1 FROM pg_class AS class_row "
                        "JOIN pg_namespace AS class_schema "
                        "ON class_schema.oid = class_row.relnamespace "
                        "WHERE class_schema.nspname = 'public' "
                        "AND class_row.relkind IN ('r', 'p', 'S', 'v', 'm', 'f') "
                        "AND pg_get_userbyid(class_row.relowner) <> current_user) "
                        "AS owns_all_public_relations, "
                        "NOT EXISTS (SELECT 1 FROM pg_proc AS function_row "
                        "JOIN pg_namespace AS function_schema "
                        "ON function_schema.oid = function_row.pronamespace "
                        "WHERE function_schema.nspname = 'public' "
                        "AND pg_get_userbyid(function_row.proowner) <> current_user) "
                        "AS owns_all_public_functions, "
                        "NOT EXISTS (SELECT 1 FROM pg_class AS version_row "
                        "JOIN pg_namespace AS version_schema "
                        "ON version_schema.oid = version_row.relnamespace "
                        "WHERE version_row.relname = 'alembic_version' "
                        "AND version_schema.nspname <> 'public') "
                        "AS alembic_version_only_in_public, "
                        "(SELECT pg_get_userbyid(namespace_row.nspowner) "
                        "FROM pg_namespace AS namespace_row "
                        "WHERE namespace_row.nspname = 'public') AS schema_owner "
                        "FROM pg_roles AS role_row "
                        "WHERE role_row.rolname = current_user"
                    ),
                    {"runtime_role": expected_runtime_role},
                ).mappings().one_or_none()
                if (
                    evidence is None
                    or evidence["role_name"] != expected_role
                    or evidence["rolsuper"]
                    or evidence["rolcreatedb"]
                    or evidence["rolcreaterole"]
                    or evidence["rolreplication"]
                    or evidence["rolbypassrls"]
                    or evidence["session_replication_role"] != "origin"
                    or evidence["has_any_role_membership"]
                    or evidence["has_any_role_members"]
                    or evidence["runtime_role_has_any_membership"]
                    or not evidence["can_create_in_schema"]
                    or evidence["current_schema_name"] != "public"
                    or list(evidence["current_schema_path"] or []) != ["public"]
                    or evidence["database_owner"] != expected_role
                    or not evidence["owns_all_public_relations"]
                    or not evidence["owns_all_public_functions"]
                    or not evidence["alembic_version_only_in_public"]
                    or evidence["schema_owner"] != expected_role
                ):
                    raise RuntimeError(
                        "production Alembic database role boundary is not satisfied"
                    )
            if connection.dialect.name == "postgresql" and connection.scalar(
                text("SELECT to_regclass('public.alembic_version')")
            ) is not None:
                # Acquire the strongest version-table lock before Alembic's
                # first UPDATE. Upgrading that lock later in a revision can
                # deadlock with autovacuum waiting for the UPDATE transaction.
                # A fresh table is already exclusively locked by its creation.
                connection.execute(
                    text("LOCK TABLE public.alembic_version IN ACCESS EXCLUSIVE MODE")
                )
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
