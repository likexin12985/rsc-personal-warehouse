"""Allow approved non-opening review proof to survive posting and closure.

Revision ID: 20260905_0058
Revises: 20260905_0057
Create Date: 2026-09-05

Revision 0032 binds a deferred review-graph validator to non-opening tasks,
reviews and review items.  Its final-approved branch accepted only the
transient ``approved`` task status.  A valid posting transaction therefore
reached ``posted`` and was rejected by that validator at COMMIT; the same
predicate would reject reconciliation updates while posted and the final
transition to ``closed``.

This narrow forward repair leaves revision 0032 immutable.  PostgreSQL drains
and locks the complete validator read/trigger boundary, rejects any existing
non-opening posted or closed fact, proves the exact function and its three
deferred ALWAYS constraint triggers, then replaces only the approved-status
predicate.  ``pg_get_functiondef`` plus ``CREATE OR REPLACE`` preserves the
function OID, owner, attributes, ACL and trigger dependencies.  Runtime
readiness advances only after the repaired catalog is proven.

Downgrade applies the exact inverse replacement only while no non-opening
posted or closed fact exists.  SQLite is an explicit schema no-op because the
PostgreSQL function and constraint triggers do not exist there.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260905_0058"
down_revision: Union[str, Sequence[str], None] = "20260905_0057"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
PROJECTOR_ROLE = "star_oam_projector"
EDGE_ROLE = "edge_inbox"
PREVIOUS_SCHEMA_REVISION = "20260905_0057"
FIXED_SEARCH_PATH = "search_path=pg_catalog, public"

REVIEW_GRAPH_FUNCTION = "rsc_require_nonopening_stocktake_review_graph_0032"
REVIEW_GRAPH_SIGNATURE = f"public.{REVIEW_GRAPH_FUNCTION}()"
UPSTREAM_REVIEW_LOCK_FUNCTION = (
    "rsc_lock_nonopening_stocktake_review_graph_0032"
)
UPSTREAM_REVIEW_LOCK_SIGNATURE = (
    f"public.{UPSTREAM_REVIEW_LOCK_FUNCTION}(uuid, uuid)"
)
DIFFERENCE_REPLAY_LOCK_FUNCTION = (
    "rsc_lock_nonopening_stocktake_difference_replay_graph_0057"
)
DIFFERENCE_REPLAY_LOCK_SIGNATURE = (
    f"public.{DIFFERENCE_REPLAY_LOCK_FUNCTION}(uuid, uuid, text)"
)
DIFFERENCE_REPLAY_LOCK_BODY_SHA256 = (
    "7771bc7f9b59465fb47426eaabbff79deeb92967c0c77bbed79c0fe01585596c"
)
REVIEW_GRAPH_TRIGGERS = (
    (
        "stocktake_review_items",
        "trg_nonopening_review_graph_item_0032",
    ),
    (
        "stocktake_reviews",
        "trg_nonopening_review_graph_review_0032",
    ),
    (
        "stocktake_tasks",
        "trg_nonopening_review_graph_task_0032",
    ),
)

LEGACY_BODY_SHA256 = (
    "2eaaa6b02e7a1abf4403ee3cacb12ccabb11d36f7687ca2821e5e93134a76a1a"
)
FIXED_BODY_SHA256 = (
    "17903808923c509cb2695f818152c598908c6fb12077abf8b398a52191caf58e"
)
LEGACY_SOURCE_FRAGMENT = "task_row.status = 'approved'"
FIXED_SOURCE_FRAGMENT = (
    "task_row.status IN ('approved', 'posted', 'closed')"
)

RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
RUNTIME_READY_SIGNATURE = f"public.{RUNTIME_READY_FUNCTION}()"
RUNTIME_READY_BODY_SHA256_0057 = (
    "9b97c355d0fcbb5ee4dfcf1e90fd76339b2eafd64f5f5b287a21aa3d364cfdc5"
)
RUNTIME_READY_BODY_SHA256_0058 = (
    "194c166aa7eeca78e70b9ab9376f060457d7e9715fd9bead34b3cf1703f32907"
)

NONOPENING_SQL = "('full', 'sample', 'ad_hoc', 'personal', 'termination')"
CATALOG_ERROR = "0058 non-opening review terminal catalog verification failed"
UPGRADE_BLOCKER = (
    "0058 non-opening review terminal upgrade is unsafe while posted or "
    "closed facts exist"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0058 while non-opening posted or closed facts exist"
)


# Union of the review validator read set, its three trigger targets and the
# readiness revision table.  Alphabetical order makes migration lock order
# deterministic while in-flight review/post/reconcile/close transactions drain.
LOCK_TABLES = (
    "alembic_version",
    "role_assignments",
    "roles",
    "stocktake_difference_set_completions",
    "stocktake_differences",
    "stocktake_review_items",
    "stocktake_reviews",
    "stocktake_rounds",
    "stocktake_tasks",
    "users",
)


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_execution_boundary()
    _require_no_nonopening_terminal_facts(
        blocker=UPGRADE_BLOCKER,
        phase="upgrade preflight",
    )
    _verify_review_catalog(fixed=False, phase="upgrade preflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0057,
        phase="upgrade preflight",
    )
    _replace_function_source(
        signature=REVIEW_GRAPH_SIGNATURE,
        expected_body_sha256=LEGACY_BODY_SHA256,
        expected_replacement_body_sha256=FIXED_BODY_SHA256,
        source_fragment=LEGACY_SOURCE_FRAGMENT,
        replacement_fragment=FIXED_SOURCE_FRAGMENT,
        phase="review validator upgrade",
    )
    _verify_review_catalog(fixed=True, phase="upgrade replacement")
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0057,
        expected_replacement_body_sha256=RUNTIME_READY_BODY_SHA256_0058,
        source_fragment=PREVIOUS_SCHEMA_REVISION,
        replacement_fragment=revision,
        phase="readiness upgrade",
    )
    _verify_review_catalog(fixed=True, phase="upgrade postflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0058,
        phase="upgrade postflight",
    )


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_execution_boundary()
    _require_no_nonopening_terminal_facts(
        blocker=DOWNGRADE_BLOCKER,
        phase="downgrade preflight",
    )
    _verify_review_catalog(fixed=True, phase="downgrade preflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0058,
        phase="downgrade preflight",
    )
    # Retract readiness before restoring the validator that cannot represent
    # the posted and closed terminal states.
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0058,
        expected_replacement_body_sha256=RUNTIME_READY_BODY_SHA256_0057,
        source_fragment=revision,
        replacement_fragment=PREVIOUS_SCHEMA_REVISION,
        phase="readiness downgrade",
    )
    _replace_function_source(
        signature=REVIEW_GRAPH_SIGNATURE,
        expected_body_sha256=FIXED_BODY_SHA256,
        expected_replacement_body_sha256=LEGACY_BODY_SHA256,
        source_fragment=FIXED_SOURCE_FRAGMENT,
        replacement_fragment=LEGACY_SOURCE_FRAGMENT,
        phase="review validator downgrade",
    )
    _verify_review_catalog(fixed=False, phase="downgrade postflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0057,
        phase="downgrade postflight",
    )


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0058 supports only PostgreSQL and SQLite")
    return dialect


def _lock_execution_boundary() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
    )


def _require_no_nonopening_terminal_facts(*, blocker: str, phase: str) -> None:
    if blocker not in {UPGRADE_BLOCKER, DOWNGRADE_BLOCKER}:
        raise ValueError("unsupported 0058 non-opening terminal blocker")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0058_facts$
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: migration role mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_tasks AS task
         WHERE task.task_type IN {NONOPENING_SQL}
           AND task.status IN ('posted', 'closed')
    ) THEN
        RAISE EXCEPTION '{blocker}: {escaped_phase}';
    END IF;
END
$rsc_0058_facts$
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
            REVIEW_GRAPH_SIGNATURE,
            LEGACY_BODY_SHA256,
            FIXED_BODY_SHA256,
            LEGACY_SOURCE_FRAGMENT,
            FIXED_SOURCE_FRAGMENT,
        ),
        (
            REVIEW_GRAPH_SIGNATURE,
            FIXED_BODY_SHA256,
            LEGACY_BODY_SHA256,
            FIXED_SOURCE_FRAGMENT,
            LEGACY_SOURCE_FRAGMENT,
        ),
        (
            RUNTIME_READY_SIGNATURE,
            RUNTIME_READY_BODY_SHA256_0057,
            RUNTIME_READY_BODY_SHA256_0058,
            PREVIOUS_SCHEMA_REVISION,
            revision,
        ),
        (
            RUNTIME_READY_SIGNATURE,
            RUNTIME_READY_BODY_SHA256_0058,
            RUNTIME_READY_BODY_SHA256_0057,
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
        raise ValueError("unsupported 0058 function source replacement")

    escaped_signature = signature.replace("'", "''")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0058_replace$
DECLARE
    function_oid oid;
    function_source text;
    function_definition text;
    replacement_definition text;
    trigger_oids oid[];
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
                $rsc_0058_source${source_fragment}$rsc_0058_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0058_source${source_fragment}$rsc_0058_source$
    ) <> 1 OR pg_catalog.strpos(
        function_source,
        $rsc_0058_replacement${replacement_fragment}$rsc_0058_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: source mismatch';
    END IF;

    SELECT pg_catalog.array_agg(trigger_row.oid ORDER BY trigger_row.oid)
      INTO trigger_oids
      FROM pg_catalog.pg_trigger AS trigger_row
     WHERE NOT trigger_row.tgisinternal
       AND trigger_row.tgfoid = function_oid;

    function_definition := pg_catalog.pg_get_functiondef(function_oid);
    IF (
        pg_catalog.length(function_definition)
        - pg_catalog.length(
            pg_catalog.replace(
                function_definition,
                $rsc_0058_source${source_fragment}$rsc_0058_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0058_source${source_fragment}$rsc_0058_source$
    ) <> 1 OR pg_catalog.strpos(
        function_definition,
        $rsc_0058_replacement${replacement_fragment}$rsc_0058_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: definition mismatch';
    END IF;

    replacement_definition := pg_catalog.replace(
        function_definition,
        $rsc_0058_source${source_fragment}$rsc_0058_source$,
        $rsc_0058_replacement${replacement_fragment}$rsc_0058_replacement$
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
    ) IS DISTINCT FROM '{expected_replacement_body_sha256}' OR (
        SELECT pg_catalog.array_agg(trigger_row.oid ORDER BY trigger_row.oid)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid = function_oid
    ) IS DISTINCT FROM trigger_oids THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: replacement mismatch';
    END IF;
END
$rsc_0058_replace$
"""
    )


def _verify_review_catalog(*, fixed: bool, phase: str) -> None:
    if not isinstance(fixed, bool):
        raise ValueError("unsupported 0058 review catalog state")
    expected_body_sha256 = FIXED_BODY_SHA256 if fixed else LEGACY_BODY_SHA256
    expected_fragment = FIXED_SOURCE_FRAGMENT if fixed else LEGACY_SOURCE_FRAGMENT
    forbidden_fragment = LEGACY_SOURCE_FRAGMENT if fixed else FIXED_SOURCE_FRAGMENT
    escaped_phase = phase.replace("'", "''")
    trigger_values = ",\n                    ".join(
        f"('{table_name}', '{trigger_name}')"
        for table_name, trigger_name in REVIEW_GRAPH_TRIGGERS
    )
    op.execute(
        f"""
DO $rsc_0058_review_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    difference_lock_oid oid := pg_catalog.to_regprocedure(
        '{DIFFERENCE_REPLAY_LOCK_SIGNATURE}'
    );
    function_oid oid := pg_catalog.to_regprocedure('{REVIEW_GRAPH_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
    upstream_lock_oid oid := pg_catalog.to_regprocedure(
        '{UPSTREAM_REVIEW_LOCK_SIGNATURE}'
    );
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL OR difference_lock_oid IS NULL
       OR function_oid IS NULL OR migrator_oid IS NULL
       OR upstream_lock_oid IS NULL
       OR (SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS function_row
             JOIN pg_catalog.pg_namespace AS namespace_row
               ON namespace_row.oid = function_row.pronamespace
            WHERE namespace_row.nspname = 'public'
              AND function_row.proname = '{REVIEW_GRAPH_FUNCTION}') <> 1 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: function identity mismatch';
    END IF;

    IF (SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS namespace_row
            ON namespace_row.oid = function_row.pronamespace
         WHERE namespace_row.nspname = 'public'
           AND function_row.proname = '{DIFFERENCE_REPLAY_LOCK_FUNCTION}') <> 1
       OR NOT EXISTS (
           SELECT 1
             FROM pg_catalog.pg_proc AS function_row
             JOIN pg_catalog.pg_namespace AS namespace_row
               ON namespace_row.oid = function_row.pronamespace
             JOIN pg_catalog.pg_language AS language_row
               ON language_row.oid = function_row.prolang
            WHERE function_row.oid = difference_lock_oid
              AND namespace_row.nspname = 'public'
              AND function_row.proowner = migrator_oid
              AND function_row.prokind = 'f'
              AND function_row.prorettype = 'void'::pg_catalog.regtype
              AND NOT function_row.proretset
              AND function_row.pronargs = 3
              AND pg_catalog.oidvectortypes(function_row.proargtypes) =
                  'uuid, uuid, text'
              AND function_row.proargnames = ARRAY[
                  'requested_task_id', 'requested_round_id',
                  'requested_actor_user_id'
              ]::text[]
              AND function_row.proallargtypes IS NULL
              AND function_row.proargmodes IS NULL
              AND function_row.pronargdefaults = 0
              AND function_row.proargdefaults IS NULL
              AND function_row.provariadic = 0
              AND language_row.lanname = 'plpgsql'
              AND function_row.provolatile = 'v'
              AND function_row.prosecdef
              AND NOT function_row.proisstrict
              AND NOT function_row.proleakproof
              AND function_row.proparallel = 'u'
              AND function_row.proconfig =
                  ARRAY['{FIXED_SEARCH_PATH}']::text[]
              AND pg_catalog.encode(
                      pg_catalog.sha256(
                          pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                      ),
                      'hex'
                  ) = '{DIFFERENCE_REPLAY_LOCK_BODY_SHA256}'
              AND (
                  pg_catalog.length(function_row.prosrc)
                  - pg_catalog.length(
                      pg_catalog.replace(
                          function_row.prosrc,
                          '{UPSTREAM_REVIEW_LOCK_FUNCTION}',
                          ''
                      )
                  )
              ) / pg_catalog.length('{UPSTREAM_REVIEW_LOCK_FUNCTION}') = 1
       ) OR NOT pg_catalog.has_function_privilege(
           api_oid,
           difference_lock_oid,
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
            WHERE function_row.oid = difference_lock_oid
              AND (
                  function_acl.privilege_type <> 'EXECUTE'
                  OR function_acl.grantee NOT IN (migrator_oid, api_oid)
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
            WHERE function_row.oid = difference_lock_oid
              AND function_acl.privilege_type = 'EXECUTE'
              AND function_acl.grantee IN (migrator_oid, api_oid)
              AND function_acl.grantor = migrator_oid
              AND NOT function_acl.is_grantable
       ) <> 2 OR (
           SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS caller_row
            WHERE pg_catalog.strpos(
                caller_row.prosrc,
                '{UPSTREAM_REVIEW_LOCK_FUNCTION}'
            ) > 0
       ) <> 1 OR pg_catalog.strpos(
           (SELECT caller_row.prosrc
              FROM pg_catalog.pg_proc AS caller_row
             WHERE caller_row.oid = difference_lock_oid),
           '{UPSTREAM_REVIEW_LOCK_FUNCTION}'
       ) = 0 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: 0057 caller mismatch';
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
                       $rsc_0058_expected${expected_fragment}$rsc_0058_expected$,
                       ''
                   )
               )
           ) / pg_catalog.length(
               $rsc_0058_expected${expected_fragment}$rsc_0058_expected$
           ) = 1
           AND pg_catalog.strpos(
               function_row.prosrc,
               $rsc_0058_forbidden${forbidden_fragment}$rsc_0058_forbidden$
           ) = 0
    ) THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: function definition mismatch';
    END IF;

    IF pg_catalog.has_function_privilege(api_oid, function_oid, 'EXECUTE')
       OR EXISTS (
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
       ) <> 1 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: function ACL mismatch';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM (VALUES
                    {trigger_values}
               ) AS expected_trigger(table_name, trigger_name)
         WHERE (SELECT pg_catalog.count(*)
                  FROM pg_catalog.pg_trigger AS trigger_row
                  JOIN pg_catalog.pg_class AS table_row
                    ON table_row.oid = trigger_row.tgrelid
                  JOIN pg_catalog.pg_namespace AS namespace_row
                    ON namespace_row.oid = table_row.relnamespace
                 WHERE NOT trigger_row.tgisinternal
                   AND trigger_row.tgname = expected_trigger.trigger_name
                   AND namespace_row.nspname = 'public'
                   AND table_row.relname = expected_trigger.table_name
                   AND trigger_row.tgfoid = function_oid
                   AND trigger_row.tgenabled = 'A'
                   AND trigger_row.tgtype = 29
                   AND trigger_row.tgconstraint <> 0
                   AND trigger_row.tgdeferrable
                   AND trigger_row.tginitdeferred
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
                   AND trigger_row.tgname = expected_trigger.trigger_name) <> 1
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid = function_oid
    ) <> {len(REVIEW_GRAPH_TRIGGERS)} THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: trigger or caller mismatch';
    END IF;
END
$rsc_0058_review_catalog$
"""
    )


def _verify_runtime_ready_catalog(
    *, expected_body_sha256: str, phase: str
) -> None:
    if expected_body_sha256 not in {
        RUNTIME_READY_BODY_SHA256_0057,
        RUNTIME_READY_BODY_SHA256_0058,
    }:
        raise ValueError("unsupported 0058 readiness body hash")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0058_readiness_catalog$
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
$rsc_0058_readiness_catalog$
"""
    )
