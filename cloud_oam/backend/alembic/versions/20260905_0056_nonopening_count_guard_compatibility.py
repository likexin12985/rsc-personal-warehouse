"""Allow the sealed round-assignment guard to serve every task type.

Revision ID: 20260905_0056
Revises: 20260905_0055
Create Date: 2026-09-05

Revision 0021 introduced one assignment helper shared by the count-line,
observation and scope-completion guards.  Its initial-round branch was
accidentally restricted to ``task_type = 'opening'`` even though the formal
task constraint and all three callers are task-type generic.  As a result a
valid non-opening task can start, but its first count fact is rejected.

This forward repair keeps revision 0021 immutable.  PostgreSQL drains the
complete execution boundary, rejects any pre-existing non-opening count fact,
pins the exact helper and its only three SECURITY DEFINER callers, their closed
ACLs and ALWAYS trigger bindings, and replaces only the task-type predicate in
the helper body.  ``pg_get_functiondef`` plus ``CREATE OR REPLACE`` preserves
the helper OID, owner, attributes and ACL.  The read-only OAM readiness marker
advances only after the repaired catalog is proven.

Downgrade applies the exact inverse replacement only while no non-opening
count line, observation or scope completion exists.  Opening facts may remain.
SQLite is an explicit schema no-op because these PostgreSQL functions and
trigger bindings do not exist there.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260905_0056"
down_revision: Union[str, Sequence[str], None] = "20260905_0055"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
PROJECTOR_ROLE = "star_oam_projector"
EDGE_ROLE = "edge_inbox"
PREVIOUS_SCHEMA_REVISION = "20260905_0055"
FIXED_SEARCH_PATH = "search_path=pg_catalog, public"

HELPER_FUNCTION = "rsc_stocktake_round_assignment_valid_0021"
HELPER_SIGNATURE = (
    f"public.{HELPER_FUNCTION}(uuid, uuid, uuid, text, uuid, uuid, bigint, "
    "text, text, text, timestamptz, boolean)"
)
HELPER_ARGUMENT_TYPES = (
    "uuid",
    "uuid",
    "uuid",
    "text",
    "uuid",
    "uuid",
    "bigint",
    "text",
    "text",
    "text",
    "timestamp with time zone",
    "boolean",
)
HELPER_ARGUMENT_NAMES = (
    "p_task_id",
    "p_round_id",
    "p_scope_id",
    "p_user_id",
    "p_person_id",
    "p_assignment_id",
    "p_authorization_version",
    "p_role_code",
    "p_scope_type",
    "p_scope_id_snapshot",
    "p_occurred_at",
    "p_require_full_snapshot",
)

COUNT_LINE_CALLER = "rsc_validate_stocktake_count_line_insert_0021"
OBSERVATION_CALLER = "rsc_validate_stocktake_observation_insert_0021"
SCOPE_COMPLETION_CALLER = (
    "rsc_validate_stocktake_scope_completion_insert_0021"
)

CALLER_CATALOG = (
    (
        f"public.{COUNT_LINE_CALLER}()",
        COUNT_LINE_CALLER,
        "319b1804e6fa6af3c7d3510524755d4b4d74b556a90640f6adfa6efa17f19693",
        "stocktake_count_lines",
        "trg_stocktake_count_lines_assignment_0021",
    ),
    (
        f"public.{OBSERVATION_CALLER}()",
        OBSERVATION_CALLER,
        "06cf2fafa1d90f120fe4bba21cc1dc55dba70bd63f649671b4333a6159af06bb",
        "stocktake_count_observations",
        "trg_stocktake_count_observations_assignment_0021",
    ),
    (
        f"public.{SCOPE_COMPLETION_CALLER}()",
        SCOPE_COMPLETION_CALLER,
        "9fda4b71155e3bdb6802e2f09bf32b90284b0f0643f4d518ee3aa286bf9efdc9",
        "stocktake_scope_count_completions",
        "trg_stocktake_scope_completions_assignment_0021",
    ),
)

LEGACY_BODY_SHA256 = (
    "87618c74ed03d25dc0d98d6a0490cec54a5f3f8f836c4ccc7b5e08593852e717"
)
FIXED_BODY_SHA256 = (
    "d6c31c4284d2861a8eea3bc98a845e44d4013e454c81a9c591860cd015931708"
)
LEGACY_SOURCE_FRAGMENT = "AND task.task_type = 'opening'"
FIXED_SOURCE_FRAGMENT = (
    "AND task.task_type IN "
    "('opening', 'full', 'sample', 'ad_hoc', 'personal', 'termination')"
)

RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
RUNTIME_READY_SIGNATURE = f"public.{RUNTIME_READY_FUNCTION}()"
RUNTIME_READY_BODY_SHA256_0055 = (
    "3f6b6b7a849746154cbdd54ff8c1d3153aa24faa3cf081b6e0d3d6ef832c7f70"
)
RUNTIME_READY_BODY_SHA256_0056 = (
    "449f0f8526292713d6344d07d25d35654b0c9967183ea83559e989dc982b04c7"
)

NONOPENING_TASK_TYPES = (
    "full",
    "sample",
    "ad_hoc",
    "personal",
    "termination",
)
NONOPENING_SQL = "('full', 'sample', 'ad_hoc', 'personal', 'termination')"

CATALOG_ERROR = "0056 non-opening count guard catalog verification failed"
UPGRADE_BLOCKER = "0056 non-opening count guard upgrade is unsafe"
DOWNGRADE_BLOCKER = "0056 non-opening count guard downgrade is unsafe"


# This is the union of the helper's read set and the complete read/write set of
# its three trigger callers.  Alphabetical order is deliberate: deployments
# acquire one stable lock order while draining transactions that could execute
# an old or new helper body.
LOCK_TABLES = (
    "inventory_lots",
    "inventory_serials",
    "material_inventory_policies",
    "qr_codes",
    "role_assignments",
    "roles",
    "stock_accounts",
    "stocktake_count_lines",
    "stocktake_count_observations",
    "stocktake_count_serials",
    "stocktake_recount_scope_assignments",
    "stocktake_round_submissions",
    "stocktake_rounds",
    "stocktake_scope_count_completions",
    "stocktake_scopes",
    "stocktake_snapshot_lines",
    "stocktake_tasks",
    "users",
)


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_execution_boundary()
    _require_no_nonopening_count_facts(
        blocker=UPGRADE_BLOCKER,
        phase="upgrade preflight",
    )
    _verify_guard_catalog(fixed=False, phase="upgrade preflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0055,
        phase="upgrade preflight",
    )
    _replace_function_source(
        signature=HELPER_SIGNATURE,
        expected_body_sha256=LEGACY_BODY_SHA256,
        expected_replacement_body_sha256=FIXED_BODY_SHA256,
        source_fragment=LEGACY_SOURCE_FRAGMENT,
        replacement_fragment=FIXED_SOURCE_FRAGMENT,
        phase="helper upgrade",
    )
    _verify_guard_catalog(fixed=True, phase="upgrade replacement")
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0055,
        expected_replacement_body_sha256=RUNTIME_READY_BODY_SHA256_0056,
        source_fragment=PREVIOUS_SCHEMA_REVISION,
        replacement_fragment=revision,
        phase="readiness upgrade",
    )
    _verify_guard_catalog(fixed=True, phase="upgrade postflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0056,
        phase="upgrade postflight",
    )


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_execution_boundary()
    _require_no_nonopening_count_facts(
        blocker=DOWNGRADE_BLOCKER,
        phase="downgrade preflight",
    )
    _verify_guard_catalog(fixed=True, phase="downgrade preflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0056,
        phase="downgrade preflight",
    )
    # Retract readiness before removing the compatibility predicate.
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0056,
        expected_replacement_body_sha256=RUNTIME_READY_BODY_SHA256_0055,
        source_fragment=revision,
        replacement_fragment=PREVIOUS_SCHEMA_REVISION,
        phase="readiness downgrade",
    )
    _replace_function_source(
        signature=HELPER_SIGNATURE,
        expected_body_sha256=FIXED_BODY_SHA256,
        expected_replacement_body_sha256=LEGACY_BODY_SHA256,
        source_fragment=FIXED_SOURCE_FRAGMENT,
        replacement_fragment=LEGACY_SOURCE_FRAGMENT,
        phase="helper downgrade",
    )
    _verify_guard_catalog(fixed=False, phase="downgrade postflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0055,
        phase="downgrade postflight",
    )


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0056 supports only PostgreSQL and SQLite")
    return dialect


def _lock_execution_boundary() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _require_no_nonopening_count_facts(*, blocker: str, phase: str) -> None:
    if blocker not in {UPGRADE_BLOCKER, DOWNGRADE_BLOCKER}:
        raise ValueError("unsupported 0056 non-opening count blocker")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0056_facts$
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: migration role mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_count_lines AS fact
          JOIN public.stocktake_tasks AS task ON task.id = fact.task_id
         WHERE task.task_type IN {NONOPENING_SQL}
    ) OR EXISTS (
        SELECT 1
          FROM public.stocktake_count_observations AS fact
          JOIN public.stocktake_tasks AS task ON task.id = fact.task_id
         WHERE task.task_type IN {NONOPENING_SQL}
    ) OR EXISTS (
        SELECT 1
          FROM public.stocktake_scope_count_completions AS fact
          JOIN public.stocktake_tasks AS task ON task.id = fact.task_id
         WHERE task.task_type IN {NONOPENING_SQL}
    ) THEN
        RAISE EXCEPTION '{blocker}: {escaped_phase}';
    END IF;
END
$rsc_0056_facts$
"""
    )


def _replace_function_source(
    *,
    signature: str,
    expected_body_sha256: str,
    expected_replacement_body_sha256: str,
    source_fragment: str,
    replacement_fragment: str,
    phase: str,
) -> None:
    supported_replacements = {
        (
            HELPER_SIGNATURE,
            LEGACY_BODY_SHA256,
            FIXED_BODY_SHA256,
            LEGACY_SOURCE_FRAGMENT,
            FIXED_SOURCE_FRAGMENT,
        ),
        (
            HELPER_SIGNATURE,
            FIXED_BODY_SHA256,
            LEGACY_BODY_SHA256,
            FIXED_SOURCE_FRAGMENT,
            LEGACY_SOURCE_FRAGMENT,
        ),
        (
            RUNTIME_READY_SIGNATURE,
            RUNTIME_READY_BODY_SHA256_0055,
            RUNTIME_READY_BODY_SHA256_0056,
            PREVIOUS_SCHEMA_REVISION,
            revision,
        ),
        (
            RUNTIME_READY_SIGNATURE,
            RUNTIME_READY_BODY_SHA256_0056,
            RUNTIME_READY_BODY_SHA256_0055,
            revision,
            PREVIOUS_SCHEMA_REVISION,
        ),
    }
    requested = (
        signature,
        expected_body_sha256,
        expected_replacement_body_sha256,
        source_fragment,
        replacement_fragment,
    )
    if requested not in supported_replacements:
        raise ValueError("unsupported 0056 function source replacement")

    escaped_signature = signature.replace("'", "''")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0056_replace$
DECLARE
    function_oid oid;
    function_source text;
    function_definition text;
    replacement_definition text;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: migration role mismatch';
    END IF;

    function_oid := pg_catalog.to_regprocedure('{escaped_signature}');
    SELECT function_row.prosrc
      INTO function_source
      FROM pg_catalog.pg_proc AS function_row
     WHERE function_row.oid = function_oid
       AND pg_catalog.encode(
               pg_catalog.sha256(
                   pg_catalog.convert_to(function_row.prosrc, 'UTF8')
               ),
               'hex'
           ) = '{expected_body_sha256}';
    IF function_source IS NULL OR (
        pg_catalog.length(function_source)
        - pg_catalog.length(
            pg_catalog.replace(
                function_source,
                $rsc_0056_source${source_fragment}$rsc_0056_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0056_source${source_fragment}$rsc_0056_source$
    ) <> 1 OR pg_catalog.strpos(
        function_source,
        $rsc_0056_replacement${replacement_fragment}$rsc_0056_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: source mismatch';
    END IF;

    function_definition := pg_catalog.pg_get_functiondef(function_oid);
    IF (
        pg_catalog.length(function_definition)
        - pg_catalog.length(
            pg_catalog.replace(
                function_definition,
                $rsc_0056_source${source_fragment}$rsc_0056_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0056_source${source_fragment}$rsc_0056_source$
    ) <> 1 OR pg_catalog.strpos(
        function_definition,
        $rsc_0056_replacement${replacement_fragment}$rsc_0056_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: definition mismatch';
    END IF;

    replacement_definition := pg_catalog.replace(
        function_definition,
        $rsc_0056_source${source_fragment}$rsc_0056_source$,
        $rsc_0056_replacement${replacement_fragment}$rsc_0056_replacement$
    );
    EXECUTE replacement_definition;

    IF pg_catalog.to_regprocedure('{escaped_signature}') <> function_oid OR (
        SELECT pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                   ),
                   'hex'
               )
          FROM pg_catalog.pg_proc AS function_row
         WHERE function_row.oid = function_oid
    ) IS DISTINCT FROM '{expected_replacement_body_sha256}' THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: replacement mismatch';
    END IF;
END
$rsc_0056_replace$
"""
    )


def _verify_guard_catalog(*, fixed: bool, phase: str) -> None:
    if not isinstance(fixed, bool):
        raise ValueError("unsupported 0056 guard state")
    expected_body_sha256 = FIXED_BODY_SHA256 if fixed else LEGACY_BODY_SHA256
    expected_fragment = FIXED_SOURCE_FRAGMENT if fixed else LEGACY_SOURCE_FRAGMENT
    forbidden_fragment = LEGACY_SOURCE_FRAGMENT if fixed else FIXED_SOURCE_FRAGMENT
    escaped_phase = phase.replace("'", "''")
    caller_values = ",\n                    ".join(
        "(" + ", ".join(
            (
                f"'{signature}'",
                f"'{function_name}'",
                f"'{body_hash}'",
                f"'{table_name}'",
                f"'{trigger_name}'",
            )
        ) + ")"
        for signature, function_name, body_hash, table_name, trigger_name
        in CALLER_CATALOG
    )
    argument_types = ", ".join(HELPER_ARGUMENT_TYPES)
    argument_names = ",".join(HELPER_ARGUMENT_NAMES)
    caller_oids = ", ".join(
        f"'{signature}'::pg_catalog.regprocedure"
        for signature, *_ in CALLER_CATALOG
    )
    op.execute(
        f"""
DO $rsc_0056_guard_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    helper_oid oid := pg_catalog.to_regprocedure('{HELPER_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL OR helper_oid IS NULL OR migrator_oid IS NULL
       OR (SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS function_row
             JOIN pg_catalog.pg_namespace AS namespace_row
               ON namespace_row.oid = function_row.pronamespace
            WHERE namespace_row.nspname = 'public'
              AND function_row.proname = '{HELPER_FUNCTION}') <> 1 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: helper identity mismatch';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS namespace_row
            ON namespace_row.oid = function_row.pronamespace
          JOIN pg_catalog.pg_language AS language_row
            ON language_row.oid = function_row.prolang
         WHERE function_row.oid = helper_oid
           AND namespace_row.nspname = 'public'
           AND function_row.proowner = migrator_oid
           AND function_row.prokind = 'f'
           AND function_row.prorettype = 'boolean'::pg_catalog.regtype
           AND NOT function_row.proretset
           AND function_row.pronargs = {len(HELPER_ARGUMENT_TYPES)}
           AND pg_catalog.oidvectortypes(function_row.proargtypes) =
               '{argument_types}'
           AND function_row.proargnames =
               pg_catalog.string_to_array('{argument_names}', ',')
           AND function_row.proallargtypes IS NULL
           AND function_row.proargmodes IS NULL
           AND function_row.pronargdefaults = 0
           AND function_row.proargdefaults IS NULL
           AND function_row.provariadic = 0
           AND language_row.lanname = 'sql'
           AND function_row.provolatile = 's'
           AND NOT function_row.proisstrict
           AND NOT function_row.proleakproof
           AND function_row.proparallel = 'u'
           AND NOT function_row.prosecdef
           AND function_row.proconfig = ARRAY['{FIXED_SEARCH_PATH}']::text[]
           AND pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                   ),
                   'hex'
               ) = '{expected_body_sha256}'
           AND (
               pg_catalog.length(function_row.prosrc)
               - pg_catalog.length(
                   pg_catalog.replace(
                       function_row.prosrc,
                       $rsc_0056_expected${expected_fragment}$rsc_0056_expected$,
                       ''
                   )
               )
           ) / pg_catalog.length(
               $rsc_0056_expected${expected_fragment}$rsc_0056_expected$
           ) = 1
           AND pg_catalog.strpos(
               function_row.prosrc,
               $rsc_0056_forbidden${forbidden_fragment}$rsc_0056_forbidden$
           ) = 0
    ) THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: helper definition mismatch';
    END IF;

    IF pg_catalog.has_function_privilege(api_oid, helper_oid, 'EXECUTE')
       OR EXISTS (
           SELECT 1
             FROM pg_catalog.pg_proc AS function_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    function_row.proacl,
                    pg_catalog.acldefault('f', function_row.proowner)
                )
            ) AS function_acl
            WHERE function_row.oid = helper_oid
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
            WHERE function_row.oid = helper_oid
              AND function_acl.privilege_type = 'EXECUTE'
              AND function_acl.grantee = migrator_oid
              AND function_acl.grantor = migrator_oid
              AND NOT function_acl.is_grantable
       ) <> 1 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: helper ACL mismatch';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM (VALUES
                    {caller_values}
               ) AS expected_caller(
                   signature, function_name, body_sha256,
                   table_name, trigger_name
               )
         WHERE pg_catalog.to_regprocedure(expected_caller.signature) IS NULL
            OR (SELECT pg_catalog.count(*)
                  FROM pg_catalog.pg_proc AS function_row
                  JOIN pg_catalog.pg_namespace AS namespace_row
                    ON namespace_row.oid = function_row.pronamespace
                  JOIN pg_catalog.pg_language AS language_row
                    ON language_row.oid = function_row.prolang
                 WHERE function_row.oid = pg_catalog.to_regprocedure(
                           expected_caller.signature
                       )
                   AND namespace_row.nspname = 'public'
                   AND function_row.proname = expected_caller.function_name
                   AND function_row.proowner = migrator_oid
                   AND function_row.prokind = 'f'
                   AND function_row.prorettype = 'trigger'::pg_catalog.regtype
                   AND NOT function_row.proretset
                   AND function_row.pronargs = 0
                   AND pg_catalog.oidvectortypes(function_row.proargtypes) = ''
                   AND function_row.proargnames IS NULL
                   AND function_row.proallargtypes IS NULL
                   AND function_row.proargmodes IS NULL
                   AND function_row.pronargdefaults = 0
                   AND function_row.proargdefaults IS NULL
                   AND function_row.provariadic = 0
                   AND language_row.lanname = 'plpgsql'
                   AND function_row.provolatile = 'v'
                   AND NOT function_row.proisstrict
                   AND NOT function_row.proleakproof
                   AND function_row.proparallel = 'u'
                   AND function_row.prosecdef
                   AND function_row.proconfig =
                       ARRAY['{FIXED_SEARCH_PATH}']::text[]
                   AND pg_catalog.encode(
                           pg_catalog.sha256(
                               pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                           ),
                           'hex'
                       ) = expected_caller.body_sha256
                   AND (
                       pg_catalog.length(function_row.prosrc)
                       - pg_catalog.length(
                           pg_catalog.replace(
                               function_row.prosrc,
                               '{HELPER_FUNCTION}',
                               ''
                           )
                       )
                   ) / pg_catalog.length('{HELPER_FUNCTION}') = 1) <> 1
            OR pg_catalog.has_function_privilege(
                   api_oid,
                   pg_catalog.to_regprocedure(expected_caller.signature),
                   'EXECUTE'
               )
            OR EXISTS (
                SELECT 1
                  FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                     COALESCE(
                         function_row.proacl,
                         pg_catalog.acldefault('f', function_row.proowner)
                     )
                 ) AS function_acl
                 WHERE function_row.oid = pg_catalog.to_regprocedure(
                           expected_caller.signature
                       )
                   AND (
                       function_acl.privilege_type <> 'EXECUTE'
                       OR function_acl.grantee <> migrator_oid
                       OR function_acl.grantor <> migrator_oid
                       OR function_acl.is_grantable
                   )
            )
            OR (SELECT pg_catalog.count(*)
                  FROM pg_catalog.pg_proc AS function_row
                 CROSS JOIN LATERAL pg_catalog.aclexplode(
                     COALESCE(
                         function_row.proacl,
                         pg_catalog.acldefault('f', function_row.proowner)
                     )
                 ) AS function_acl
                 WHERE function_row.oid = pg_catalog.to_regprocedure(
                           expected_caller.signature
                       )
                   AND function_acl.privilege_type = 'EXECUTE'
                   AND function_acl.grantee = migrator_oid
                   AND function_acl.grantor = migrator_oid
                   AND NOT function_acl.is_grantable) <> 1
            OR (SELECT pg_catalog.count(*)
                  FROM pg_catalog.pg_trigger AS trigger_row
                  JOIN pg_catalog.pg_class AS table_row
                    ON table_row.oid = trigger_row.tgrelid
                  JOIN pg_catalog.pg_namespace AS namespace_row
                    ON namespace_row.oid = table_row.relnamespace
                 WHERE NOT trigger_row.tgisinternal
                   AND trigger_row.tgname = expected_caller.trigger_name
                   AND namespace_row.nspname = 'public'
                   AND table_row.relname = expected_caller.table_name
                   AND trigger_row.tgfoid = pg_catalog.to_regprocedure(
                           expected_caller.signature
                       )
                   AND trigger_row.tgenabled = 'A'
                   AND trigger_row.tgtype = 7
                   AND trigger_row.tgconstraint = 0
                   AND NOT trigger_row.tgdeferrable
                   AND NOT trigger_row.tginitdeferred
                   AND trigger_row.tgconstrrelid = 0
                   AND trigger_row.tgconstrindid = 0
                   AND trigger_row.tgparentid = 0
                   AND trigger_row.tgqual IS NULL
                   AND trigger_row.tgoldtable IS NULL
                   AND trigger_row.tgnewtable IS NULL
                   AND trigger_row.tgnargs = 0
                   AND trigger_row.tgattr = ''::pg_catalog.int2vector) <> 1
            OR (SELECT pg_catalog.count(*)
                  FROM pg_catalog.pg_trigger AS trigger_row
                 WHERE NOT trigger_row.tgisinternal
                   AND trigger_row.tgname = expected_caller.trigger_name) <> 1
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS caller_row
         WHERE pg_catalog.strpos(caller_row.prosrc, '{HELPER_FUNCTION}') > 0
    ) <> {len(CALLER_CATALOG)} OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS caller_row
         WHERE pg_catalog.strpos(caller_row.prosrc, '{HELPER_FUNCTION}') > 0
           AND caller_row.oid NOT IN ({caller_oids})
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid IN ({caller_oids})
    ) <> {len(CALLER_CATALOG)} THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: caller or trigger mismatch';
    END IF;
END
$rsc_0056_guard_catalog$
"""
    )


def _verify_runtime_ready_catalog(
    *, expected_body_sha256: str, phase: str
) -> None:
    if expected_body_sha256 not in {
        RUNTIME_READY_BODY_SHA256_0055,
        RUNTIME_READY_BODY_SHA256_0056,
    }:
        raise ValueError("unsupported 0056 readiness body hash")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0056_readiness_catalog$
DECLARE
    edge_oid oid := pg_catalog.to_regrole('{EDGE_ROLE}');
    function_oid oid := pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
    projector_oid oid := pg_catalog.to_regrole('{PROJECTOR_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR edge_oid IS NULL OR function_oid IS NULL OR migrator_oid IS NULL
       OR projector_oid IS NULL OR (
           SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.proname = '{RUNTIME_READY_FUNCTION}'
       ) <> 1 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: readiness identity mismatch';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS namespace_row
            ON namespace_row.oid = function_row.pronamespace
          JOIN pg_catalog.pg_language AS language_row
            ON language_row.oid = function_row.prolang
         WHERE function_row.oid = function_oid
           AND namespace_row.nspname = 'public'
           AND function_row.proowner = migrator_oid
           AND function_row.prokind = 'f'
           AND function_row.prorettype = 'boolean'::pg_catalog.regtype
           AND NOT function_row.proretset
           AND function_row.pronargs = 0
           AND pg_catalog.oidvectortypes(function_row.proargtypes) = ''
           AND function_row.proargnames IS NULL
           AND function_row.proallargtypes IS NULL
           AND function_row.proargmodes IS NULL
           AND function_row.pronargdefaults = 0
           AND function_row.proargdefaults IS NULL
           AND function_row.provariadic = 0
           AND language_row.lanname = 'sql'
           AND function_row.provolatile = 's'
           AND NOT function_row.proisstrict
           AND NOT function_row.proleakproof
           AND function_row.proparallel = 'u'
           AND function_row.prosecdef
           AND function_row.proconfig = ARRAY['search_path=pg_catalog']::text[]
           AND pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                   ),
                   'hex'
               ) = '{expected_body_sha256}'
    ) THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: readiness function mismatch';
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
               OR function_acl.grantor <> migrator_oid
               OR function_acl.grantee NOT IN (
                   migrator_oid, projector_oid, edge_oid
               )
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
           AND function_acl.grantor = migrator_oid
           AND function_acl.grantee IN (
               migrator_oid, projector_oid, edge_oid
           )
           AND NOT function_acl.is_grantable
    ) <> 3 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: readiness ACL mismatch';
    END IF;
END
$rsc_0056_readiness_catalog$
"""
    )
