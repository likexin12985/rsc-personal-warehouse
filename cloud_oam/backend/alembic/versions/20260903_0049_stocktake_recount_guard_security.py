"""Repair the stocktake nested guard execution closure without widening ACLs.

Revision ID: 20260903_0049
Revises: 20260903_0048
Create Date: 2026-09-03

Revisions 0011, 0016, 0018, 0021 and 0032 deliberately left internal
actor/scope helpers and their trigger callers unavailable to PUBLIC and the API
role.  Nine currently bound disposition/recount/count trigger functions
remained SECURITY INVOKER, however, and call one of those inaccessible helpers.
PostgreSQL therefore evaluates the nested call as the runtime API role and
rejects valid disposition, count, assignment, recount-case, round and deferred
graph transactions with SQLSTATE 42501.

This narrow forward repair locks every table carrying an affected live trigger
and fail-closed verifies the exact thirteen-function catalog, immutable body
hashes, fifteen trigger bindings, search paths, owners and closed ACLs.  It
changes only the nine direct trigger callers to migration-owned SECURITY
DEFINER.  The three internal helpers and the 0032 review-graph validator stay
SECURITY INVOKER; none of the thirteen functions becomes directly executable by
PUBLIC or the API role.  No table privilege or business row is changed.

Downgrade symmetrically verifies the hardened catalog before restoring the
nine callers and the two legacy unset search paths exactly.
SQLite is an explicit schema no-op for local migration-chain compatibility;
it is not PostgreSQL security evidence.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260903_0049"
down_revision: Union[str, Sequence[str], None] = "20260903_0048"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
OAM_RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
PREVIOUS_SCHEMA_REVISION = "20260903_0048"

ACTOR_ASSIGNMENT_HELPER_0011 = "rsc_stocktake_actor_assignment_valid_0011"
OBSERVATION_DISPOSITION_CALLER_0016 = (
    "rsc_validate_stocktake_observation_disposition_0016"
)
RECOUNT_ASSIGNMENT_CALLER_0018 = (
    "rsc_validate_stocktake_recount_scope_assignment_0018"
)
ROUND_ASSIGNMENT_HELPER_0021 = "rsc_stocktake_round_assignment_valid_0021"
COUNT_LINE_CALLER_0021 = "rsc_validate_stocktake_count_line_insert_0021"
OBSERVATION_CALLER_0021 = "rsc_validate_stocktake_observation_insert_0021"
SCOPE_COMPLETION_CALLER_0021 = (
    "rsc_validate_stocktake_scope_completion_insert_0021"
)
REVIEW_GRAPH_VALIDATOR_0032 = (
    "rsc_require_nonopening_stocktake_review_graph_0032"
)
RECOUNT_SCOPE_GRAPH_HELPER_0032 = (
    "rsc_stocktake_recount_scope_graph_valid_0032"
)
RECOUNT_CASE_CALLER_0032 = "rsc_validate_stocktake_recount_case_0032"
RECOUNT_TASK_CALLER_0032 = "rsc_validate_stocktake_recount_task_advance_0032"
RECOUNT_ROUND_CALLER_0032 = "rsc_validate_stocktake_recount_round_0032"
RECOUNT_GRAPH_CALLER_0032 = "rsc_require_stocktake_recount_graph_0032"

ACTOR_ASSIGNMENT_HELPER_0011_SIGNATURE = (
    f"public.{ACTOR_ASSIGNMENT_HELPER_0011}(text, uuid, uuid, bigint, "
    "timestamptz, text, text, text)"
)
OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE = (
    f"public.{OBSERVATION_DISPOSITION_CALLER_0016}()"
)
RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE = (
    f"public.{RECOUNT_ASSIGNMENT_CALLER_0018}()"
)
ROUND_ASSIGNMENT_HELPER_0021_SIGNATURE = (
    f"public.{ROUND_ASSIGNMENT_HELPER_0021}(uuid, uuid, uuid, text, uuid, "
    "uuid, bigint, text, text, text, timestamptz, boolean)"
)
COUNT_LINE_CALLER_0021_SIGNATURE = f"public.{COUNT_LINE_CALLER_0021}()"
OBSERVATION_CALLER_0021_SIGNATURE = f"public.{OBSERVATION_CALLER_0021}()"
SCOPE_COMPLETION_CALLER_0021_SIGNATURE = (
    f"public.{SCOPE_COMPLETION_CALLER_0021}()"
)
REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE = (
    f"public.{REVIEW_GRAPH_VALIDATOR_0032}()"
)
RECOUNT_SCOPE_GRAPH_HELPER_0032_SIGNATURE = (
    f"public.{RECOUNT_SCOPE_GRAPH_HELPER_0032}(uuid)"
)
RECOUNT_CASE_CALLER_0032_SIGNATURE = f"public.{RECOUNT_CASE_CALLER_0032}()"
RECOUNT_TASK_CALLER_0032_SIGNATURE = f"public.{RECOUNT_TASK_CALLER_0032}()"
RECOUNT_ROUND_CALLER_0032_SIGNATURE = f"public.{RECOUNT_ROUND_CALLER_0032}()"
RECOUNT_GRAPH_CALLER_0032_SIGNATURE = f"public.{RECOUNT_GRAPH_CALLER_0032}()"

CALLER_SIGNATURES = (
    OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE,
    RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE,
    COUNT_LINE_CALLER_0021_SIGNATURE,
    OBSERVATION_CALLER_0021_SIGNATURE,
    SCOPE_COMPLETION_CALLER_0021_SIGNATURE,
    RECOUNT_CASE_CALLER_0032_SIGNATURE,
    RECOUNT_TASK_CALLER_0032_SIGNATURE,
    RECOUNT_ROUND_CALLER_0032_SIGNATURE,
    RECOUNT_GRAPH_CALLER_0032_SIGNATURE,
)
INVOKER_SIGNATURES = (
    ACTOR_ASSIGNMENT_HELPER_0011_SIGNATURE,
    ROUND_ASSIGNMENT_HELPER_0021_SIGNATURE,
    REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE,
    RECOUNT_SCOPE_GRAPH_HELPER_0032_SIGNATURE,
)
ALL_FUNCTION_SIGNATURES = (*INVOKER_SIGNATURES, *CALLER_SIGNATURES)

EXPECTED_FUNCTION_BODY_SHA256 = {
    ACTOR_ASSIGNMENT_HELPER_0011_SIGNATURE:
        "09720a289e550a66f2ea400fdb0541d1646916d661538af0d2706f9fe5c326d1",
    OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE:
        "94cb47e8e75bd3eac0b448334992299c2cd3cd96f33798ab46e14e2dfe1f9c97",
    RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE:
        "2c4cd18b9b5dce1e4b0e0a9e2823dff1e8e16c08ddbcc77ba6f8d0c20e7c85d9",
    ROUND_ASSIGNMENT_HELPER_0021_SIGNATURE:
        "87618c74ed03d25dc0d98d6a0490cec54a5f3f8f836c4ccc7b5e08593852e717",
    COUNT_LINE_CALLER_0021_SIGNATURE:
        "319b1804e6fa6af3c7d3510524755d4b4d74b556a90640f6adfa6efa17f19693",
    OBSERVATION_CALLER_0021_SIGNATURE:
        "082b8afe54b38790b15d72b3f946fbf7c943b2780cd5ba3f43a9dee34db337f3",
    SCOPE_COMPLETION_CALLER_0021_SIGNATURE:
        "7470b118731f1fd6e53269457d511f73308eab704048109b32bd039f8df0824f",
    REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE:
        "2eaaa6b02e7a1abf4403ee3cacb12ccabb11d36f7687ca2821e5e93134a76a1a",
    RECOUNT_SCOPE_GRAPH_HELPER_0032_SIGNATURE:
        "a1308f871cb0c7ba6fb0584dc28520b0ea4361d956d3c3d30a758f8ffafa1619",
    RECOUNT_CASE_CALLER_0032_SIGNATURE:
        "65db070bee60e91203b32a6afe56092992ae7d1984ec92d6490faabc6930e211",
    RECOUNT_TASK_CALLER_0032_SIGNATURE:
        "0d6b43d9069eb641a89197c37ae4d1f806eb401b4d9d3719d10843dbb50d4b5f",
    RECOUNT_ROUND_CALLER_0032_SIGNATURE:
        "271d01c965cc4cc939d183afdcae7f52de4c22f193d9317b2938408e3c8b3904",
    RECOUNT_GRAPH_CALLER_0032_SIGNATURE:
        "294e748d5020b37057851154ecfed2ee66a85fde62bc9d0e4f8cae08aa7be1a2",
}

FIXED_SEARCH_PATH = "search_path=pg_catalog, public"
FUNCTION_CATALOG = (
    # signature, name, return type, language, volatility, legacy search path
    (
        ACTOR_ASSIGNMENT_HELPER_0011_SIGNATURE,
        ACTOR_ASSIGNMENT_HELPER_0011,
        "boolean",
        "sql",
        "s",
        None,
    ),
    (
        OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE,
        OBSERVATION_DISPOSITION_CALLER_0016,
        "trigger",
        "plpgsql",
        "v",
        None,
    ),
    (
        RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE,
        RECOUNT_ASSIGNMENT_CALLER_0018,
        "trigger",
        "plpgsql",
        "v",
        None,
    ),
    (
        ROUND_ASSIGNMENT_HELPER_0021_SIGNATURE,
        ROUND_ASSIGNMENT_HELPER_0021,
        "boolean",
        "sql",
        "s",
        FIXED_SEARCH_PATH,
    ),
    (
        COUNT_LINE_CALLER_0021_SIGNATURE,
        COUNT_LINE_CALLER_0021,
        "trigger",
        "plpgsql",
        "v",
        FIXED_SEARCH_PATH,
    ),
    (
        OBSERVATION_CALLER_0021_SIGNATURE,
        OBSERVATION_CALLER_0021,
        "trigger",
        "plpgsql",
        "v",
        FIXED_SEARCH_PATH,
    ),
    (
        SCOPE_COMPLETION_CALLER_0021_SIGNATURE,
        SCOPE_COMPLETION_CALLER_0021,
        "trigger",
        "plpgsql",
        "v",
        FIXED_SEARCH_PATH,
    ),
    (
        REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE,
        REVIEW_GRAPH_VALIDATOR_0032,
        "trigger",
        "plpgsql",
        "v",
        FIXED_SEARCH_PATH,
    ),
    (
        RECOUNT_SCOPE_GRAPH_HELPER_0032_SIGNATURE,
        RECOUNT_SCOPE_GRAPH_HELPER_0032,
        "boolean",
        "sql",
        "s",
        FIXED_SEARCH_PATH,
    ),
    (
        RECOUNT_CASE_CALLER_0032_SIGNATURE,
        RECOUNT_CASE_CALLER_0032,
        "trigger",
        "plpgsql",
        "v",
        FIXED_SEARCH_PATH,
    ),
    (
        RECOUNT_TASK_CALLER_0032_SIGNATURE,
        RECOUNT_TASK_CALLER_0032,
        "trigger",
        "plpgsql",
        "v",
        FIXED_SEARCH_PATH,
    ),
    (
        RECOUNT_ROUND_CALLER_0032_SIGNATURE,
        RECOUNT_ROUND_CALLER_0032,
        "trigger",
        "plpgsql",
        "v",
        FIXED_SEARCH_PATH,
    ),
    (
        RECOUNT_GRAPH_CALLER_0032_SIGNATURE,
        RECOUNT_GRAPH_CALLER_0032,
        "trigger",
        "plpgsql",
        "v",
        FIXED_SEARCH_PATH,
    ),
)

TRIGGER_TABLES = (
    "stocktake_tasks",
    "stocktake_rounds",
    "stocktake_recount_cases",
    "stocktake_recount_scope_assignments",
    "stocktake_count_lines",
    "stocktake_count_observations",
    "stocktake_observation_dispositions",
    "stocktake_scope_count_completions",
    "stocktake_reviews",
    "stocktake_review_items",
)

# table, trigger, function signature, tgtype, constraint, deferred, init-deferred
TRIGGER_CATALOG = (
    (
        "stocktake_observation_dispositions",
        "trg_stocktake_observation_dispositions_validate_0016",
        OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE,
        7,
        False,
        False,
        False,
    ),
    (
        "stocktake_recount_scope_assignments",
        "trg_stocktake_recount_scope_assignments_validate_0018",
        RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE,
        7,
        False,
        False,
        False,
    ),
    (
        "stocktake_count_lines",
        "trg_stocktake_count_lines_assignment_0021",
        COUNT_LINE_CALLER_0021_SIGNATURE,
        7,
        False,
        False,
        False,
    ),
    (
        "stocktake_count_observations",
        "trg_stocktake_count_observations_assignment_0021",
        OBSERVATION_CALLER_0021_SIGNATURE,
        7,
        False,
        False,
        False,
    ),
    (
        "stocktake_scope_count_completions",
        "trg_stocktake_scope_completions_assignment_0021",
        SCOPE_COMPLETION_CALLER_0021_SIGNATURE,
        7,
        False,
        False,
        False,
    ),
    (
        "stocktake_recount_cases",
        "trg_stocktake_recount_cases_review_path_0032",
        RECOUNT_CASE_CALLER_0032_SIGNATURE,
        7,
        False,
        False,
        False,
    ),
    (
        "stocktake_tasks",
        "trg_stocktake_tasks_recount_causality_0032",
        RECOUNT_TASK_CALLER_0032_SIGNATURE,
        19,
        False,
        False,
        False,
    ),
    (
        "stocktake_rounds",
        "trg_stocktake_rounds_recount_causality_0032",
        RECOUNT_ROUND_CALLER_0032_SIGNATURE,
        23,
        False,
        False,
        False,
    ),
    (
        "stocktake_tasks",
        "trg_nonopening_review_graph_task_0032",
        REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE,
        29,
        True,
        True,
        True,
    ),
    (
        "stocktake_reviews",
        "trg_nonopening_review_graph_review_0032",
        REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE,
        29,
        True,
        True,
        True,
    ),
    (
        "stocktake_review_items",
        "trg_nonopening_review_graph_item_0032",
        REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE,
        29,
        True,
        True,
        True,
    ),
    (
        "stocktake_tasks",
        "trg_stocktake_recount_graph_task_0032",
        RECOUNT_GRAPH_CALLER_0032_SIGNATURE,
        29,
        True,
        True,
        True,
    ),
    (
        "stocktake_rounds",
        "trg_stocktake_recount_graph_round_0032",
        RECOUNT_GRAPH_CALLER_0032_SIGNATURE,
        29,
        True,
        True,
        True,
    ),
    (
        "stocktake_recount_cases",
        "trg_stocktake_recount_graph_case_0032",
        RECOUNT_GRAPH_CALLER_0032_SIGNATURE,
        29,
        True,
        True,
        True,
    ),
    (
        "stocktake_recount_scope_assignments",
        "trg_stocktake_recount_graph_assignment_0032",
        RECOUNT_GRAPH_CALLER_0032_SIGNATURE,
        29,
        True,
        True,
        True,
    ),
)

TRIGGER_FUNCTION_SIGNATURES = (
    OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE,
    RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE,
    COUNT_LINE_CALLER_0021_SIGNATURE,
    OBSERVATION_CALLER_0021_SIGNATURE,
    SCOPE_COMPLETION_CALLER_0021_SIGNATURE,
    RECOUNT_CASE_CALLER_0032_SIGNATURE,
    RECOUNT_TASK_CALLER_0032_SIGNATURE,
    RECOUNT_ROUND_CALLER_0032_SIGNATURE,
    REVIEW_GRAPH_VALIDATOR_0032_SIGNATURE,
    RECOUNT_GRAPH_CALLER_0032_SIGNATURE,
)

CATALOG_ERROR = "0049 stocktake recount caller catalog verification failed"


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0049 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_trigger_tables()
    _verify_catalog(
        callers_security_definer=False,
        phase="legacy upgrade preflight",
    )
    _set_caller_security(security_definer=True)
    _verify_catalog(
        callers_security_definer=True,
        phase="hardened upgrade postflight",
    )
    _replace_oam_runtime_ready_function(revision)


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_trigger_tables()
    _verify_catalog(
        callers_security_definer=True,
        phase="hardened downgrade preflight",
    )
    _set_caller_security(security_definer=False)
    _verify_catalog(
        callers_security_definer=False,
        phase="legacy downgrade postflight",
    )
    _replace_oam_runtime_ready_function(PREVIOUS_SCHEMA_REVISION)


def _lock_trigger_tables() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in TRIGGER_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _sql_literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _function_catalog_values(*, callers_security_definer: bool) -> str:
    rows = []
    for (
        signature,
        function_name,
        return_type,
        language,
        volatility,
        legacy_search_path,
    ) in FUNCTION_CATALOG:
        security_definer = (
            callers_security_definer if signature in CALLER_SIGNATURES else False
        )
        expected_search_path = legacy_search_path
        if callers_security_definer and signature in CALLER_SIGNATURES:
            expected_search_path = FIXED_SEARCH_PATH
        search_path_sql = (
            "NULL::text"
            if expected_search_path is None
            else f"{_sql_literal(expected_search_path)}::text"
        )
        rows.append(
            "("
            + ", ".join(
                (
                    _sql_literal(signature),
                    _sql_literal(function_name),
                    _sql_literal(return_type),
                    _sql_literal(language),
                    _sql_literal(volatility),
                    "TRUE" if security_definer else "FALSE",
                    search_path_sql,
                    _sql_literal(EXPECTED_FUNCTION_BODY_SHA256[signature]),
                )
            )
            + ")"
        )
    return ",\n        ".join(rows)


def _trigger_catalog_values() -> str:
    rows = []
    for (
        table_name,
        trigger_name,
        function_signature,
        trigger_type,
        is_constraint,
        is_deferrable,
        is_initially_deferred,
    ) in TRIGGER_CATALOG:
        rows.append(
            "("
            + ", ".join(
                (
                    _sql_literal(table_name),
                    _sql_literal(trigger_name),
                    _sql_literal(function_signature),
                    str(trigger_type),
                    "TRUE" if is_constraint else "FALSE",
                    "TRUE" if is_deferrable else "FALSE",
                    "TRUE" if is_initially_deferred else "FALSE",
                )
            )
            + ")"
        )
    return ",\n        ".join(rows)


def _verify_catalog(*, callers_security_definer: bool, phase: str) -> None:
    function_values = _function_catalog_values(
        callers_security_definer=callers_security_definer
    )
    trigger_values = _trigger_catalog_values()
    function_literals = ", ".join(
        f"{_sql_literal(signature)}::pg_catalog.regprocedure"
        for signature in ALL_FUNCTION_SIGNATURES
    )
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0049$
DECLARE
    api_oid oid;
    migrator_oid oid;
    function_oid oid;
    expected_function record;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: migration role mismatch';
    END IF;

    SELECT role_row.oid
      INTO migrator_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{MIGRATION_ROLE}';
    SELECT role_row.oid
      INTO api_oid
      FROM pg_catalog.pg_roles AS role_row
     WHERE role_row.rolname = '{PRODUCTION_API_ROLE}';
    IF migrator_oid IS NULL OR api_oid IS NULL THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: required role is missing';
    END IF;

    FOR expected_function IN
        SELECT *
          FROM (VALUES
        {function_values}
          ) AS expected(
              signature,
              function_name,
              return_type,
              language_name,
              volatility,
              security_definer,
              search_path,
              body_sha256
          )
    LOOP
        function_oid := pg_catalog.to_regprocedure(expected_function.signature);
        IF function_oid IS NULL OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_proc AS function_row
              JOIN pg_catalog.pg_namespace AS schema_row
                ON schema_row.oid = function_row.pronamespace
             WHERE schema_row.nspname = 'public'
               AND function_row.proname = expected_function.function_name
        ) <> 1 THEN
            RAISE EXCEPTION
                '{CATALOG_ERROR}: {escaped_phase}: function identity mismatch';
        END IF;

        IF NOT EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS function_row
              JOIN pg_catalog.pg_language AS language_row
                ON language_row.oid = function_row.prolang
             WHERE function_row.oid = function_oid
               AND function_row.proowner = migrator_oid
               AND function_row.prokind = 'f'
               AND function_row.prorettype =
                   pg_catalog.to_regtype(expected_function.return_type)
               AND NOT function_row.proretset
               AND function_row.proargmodes IS NULL
               AND function_row.pronargdefaults = 0
               AND function_row.proargdefaults IS NULL
               AND function_row.provariadic = 0
               AND language_row.lanname = expected_function.language_name
               AND function_row.provolatile = expected_function.volatility
               AND NOT function_row.proisstrict
               AND NOT function_row.proleakproof
               AND function_row.proparallel = 'u'
               AND function_row.prosecdef IS NOT DISTINCT FROM
                   expected_function.security_definer
               AND function_row.proconfig IS NOT DISTINCT FROM
                   CASE
                       WHEN expected_function.search_path IS NULL
                       THEN NULL::text[]
                       ELSE ARRAY[expected_function.search_path]::text[]
                   END
               AND pg_catalog.encode(
                       pg_catalog.sha256(
                           pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                       ),
                       'hex'
                   ) = expected_function.body_sha256
        ) THEN
            RAISE EXCEPTION
                '{CATALOG_ERROR}: {escaped_phase}: function definition mismatch';
        END IF;

        IF EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS function_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(
                     function_row.proacl,
                     pg_catalog.acldefault('f', function_row.proowner)
                 )
             ) AS function_acl
             WHERE function_row.oid = function_oid
               AND (
                   function_acl.privilege_type <> 'EXECUTE'
                   OR function_acl.grantee <> migrator_oid
                   OR function_acl.grantor <> migrator_oid
                   OR function_acl.is_grantable
               )
        ) OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_proc AS function_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(
                     function_row.proacl,
                     pg_catalog.acldefault('f', function_row.proowner)
                 )
             ) AS function_acl
             WHERE function_row.oid = function_oid
               AND function_acl.privilege_type = 'EXECUTE'
               AND function_acl.grantee = migrator_oid
               AND function_acl.grantor = migrator_oid
               AND NOT function_acl.is_grantable
        ) <> 1 OR pg_catalog.has_function_privilege(
            api_oid,
            function_oid,
            'EXECUTE'
        ) OR EXISTS (
            SELECT 1
              FROM pg_catalog.pg_proc AS function_row
             CROSS JOIN LATERAL pg_catalog.aclexplode(
                 COALESCE(
                     function_row.proacl,
                     pg_catalog.acldefault('f', function_row.proowner)
                 )
             ) AS function_acl
             WHERE function_row.oid = function_oid
               AND function_acl.grantee = 0
        ) THEN
            RAISE EXCEPTION
                '{CATALOG_ERROR}: {escaped_phase}: function ACL mismatch';
        END IF;
    END LOOP;

    IF EXISTS (
        WITH expected_trigger(
            table_name,
            trigger_name,
            function_signature,
            trigger_type,
            is_constraint,
            is_deferrable,
            is_initially_deferred
        ) AS (VALUES
        {trigger_values}
        )
        SELECT 1
          FROM expected_trigger AS expected
         WHERE (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_trigger AS trigger_row
             WHERE NOT trigger_row.tgisinternal
               AND trigger_row.tgname = expected.trigger_name
               AND trigger_row.tgrelid = pg_catalog.to_regclass(
                   pg_catalog.format('public.%I', expected.table_name)
               )
               AND trigger_row.tgfoid = pg_catalog.to_regprocedure(
                   expected.function_signature
               )
               AND trigger_row.tgenabled = 'A'
               AND trigger_row.tgtype = expected.trigger_type
               AND (trigger_row.tgconstraint <> 0) IS NOT DISTINCT FROM
                   expected.is_constraint
               AND trigger_row.tgdeferrable IS NOT DISTINCT FROM
                   expected.is_deferrable
               AND trigger_row.tginitdeferred IS NOT DISTINCT FROM
                   expected.is_initially_deferred
               AND trigger_row.tgqual IS NULL
               AND trigger_row.tgnargs = 0
               AND trigger_row.tgattr = ''::pg_catalog.int2vector
         ) <> 1 OR (
            SELECT pg_catalog.count(*)
              FROM pg_catalog.pg_trigger AS trigger_row
             WHERE NOT trigger_row.tgisinternal
               AND trigger_row.tgname = expected.trigger_name
         ) <> 1
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid IN ({function_literals})
    ) <> {len(TRIGGER_CATALOG)} THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: trigger binding mismatch';
    END IF;
END
$rsc_0049$
"""
    )


def _set_caller_security(*, security_definer: bool) -> None:
    security = "DEFINER" if security_definer else "INVOKER"
    for signature in CALLER_SIGNATURES:
        op.execute(f"ALTER FUNCTION {signature} SECURITY {security}")
        if security_definer or signature not in {
            OBSERVATION_DISPOSITION_CALLER_0016_SIGNATURE,
            RECOUNT_ASSIGNMENT_CALLER_0018_SIGNATURE,
        }:
            op.execute(
                f"ALTER FUNCTION {signature} "
                "SET search_path = pg_catalog, public"
            )
        else:
            op.execute(f"ALTER FUNCTION {signature} RESET search_path")
    for signature in ALL_FUNCTION_SIGNATURES:
        op.execute(f"ALTER FUNCTION {signature} OWNER TO {MIGRATION_ROLE}")
        op.execute(
            f"REVOKE ALL ON FUNCTION {signature} "
            f"FROM PUBLIC, {PRODUCTION_API_ROLE}"
        )


def _replace_oam_runtime_ready_function(expected_revision: str) -> None:
    op.execute(_oam_runtime_ready_function_sql(expected_revision))


def _oam_runtime_ready_function_sql(expected_revision: str) -> str:
    if expected_revision not in {PREVIOUS_SCHEMA_REVISION, revision}:
        raise ValueError("unsupported OAM runtime readiness revision")
    return f"""
CREATE OR REPLACE FUNCTION public.{OAM_RUNTIME_READY_FUNCTION}()
RETURNS boolean
LANGUAGE sql
STABLE
SECURITY DEFINER
SET search_path = pg_catalog
AS $$
    SELECT (
        SELECT pg_catalog.count(*) = 1
           AND pg_catalog.min(version_num) = '{expected_revision}'
          FROM public.alembic_version
    ) AND CASE session_user::text
        WHEN 'edge_inbox' THEN EXISTS (
            SELECT 1
              FROM public.oam_sync_scope_bindings AS ingress
             WHERE ingress.enabled
               AND ingress.principal_name = session_user::text
               AND ingress.capability = 'edge_ingress'
               AND (
                   ingress.entity_type <> 'work_order'
                   OR EXISTS (
                       SELECT 1
                         FROM public.source_systems AS source
                        WHERE source.code = ingress.source_system
                          AND source.mode = 'read_only'
                          AND source.enabled
                          AND source.configuration_jsonb =
                              pg_catalog.jsonb_build_object(
                                  'projection_schema',
                                  'rsc.oam_work_order_projection.v1',
                                  'edge_source_instance', ingress.source_instance,
                                  'work_order_company_id', ingress.company_id,
                                  'work_order_org_code', ingress.org_code,
                                  'work_order_scope_key', ingress.scope_key
                              )
                   )
               )
        )
        WHEN 'star_oam_projector' THEN EXISTS (
            SELECT 1
              FROM public.oam_sync_scope_bindings AS write_work_order
              JOIN public.oam_sync_scope_bindings AS read_work_order
                ON read_work_order.source_system = write_work_order.source_system
               AND read_work_order.source_instance = write_work_order.source_instance
               AND read_work_order.scope_key = write_work_order.scope_key
               AND read_work_order.company_id = write_work_order.company_id
               AND read_work_order.org_code = write_work_order.org_code
               AND read_work_order.enabled
               AND read_work_order.principal_name = write_work_order.principal_name
               AND read_work_order.capability = 'projector_read'
               AND read_work_order.entity_type = 'work_order'
              JOIN public.oam_sync_scope_bindings AS read_employee
                ON read_employee.source_system = write_work_order.source_system
               AND read_employee.source_instance = write_work_order.source_instance
               AND read_employee.scope_key = write_work_order.scope_key
               AND read_employee.company_id = write_work_order.company_id
               AND read_employee.org_code = write_work_order.org_code
               AND read_employee.enabled
               AND read_employee.principal_name = write_work_order.principal_name
               AND read_employee.capability = 'projector_read'
               AND read_employee.entity_type = 'employee'
              JOIN public.source_systems AS source
                ON source.code = write_work_order.source_system
               AND source.mode = 'read_only'
               AND source.enabled
               AND source.configuration_jsonb = pg_catalog.jsonb_build_object(
                   'projection_schema', 'rsc.oam_work_order_projection.v1',
                   'edge_source_instance', write_work_order.source_instance,
                   'work_order_company_id', write_work_order.company_id,
                   'work_order_org_code', write_work_order.org_code,
                   'work_order_scope_key', write_work_order.scope_key
               )
             WHERE write_work_order.enabled
               AND write_work_order.principal_name = session_user::text
               AND write_work_order.capability = 'projector_write'
               AND write_work_order.entity_type = 'work_order'
        ) AND (
            SELECT pg_catalog.count(*) = 3
              FROM public.oam_sync_scope_bindings AS binding
             WHERE binding.enabled
               AND binding.principal_name = session_user::text
        )
        ELSE false
    END
$$
"""
