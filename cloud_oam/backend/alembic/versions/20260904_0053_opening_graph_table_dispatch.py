"""Make the opening graph trigger dispatch table-safe.

Revision ID: 20260904_0053
Revises: 20260903_0052
Create Date: 2026-09-04

The 0052 opening graph closure is intentionally shared by fifteen tables.  Its
round branch used ``TG_TABLE_NAME = 'stocktake_rounds' AND NEW.status = ...``.
PostgreSQL resolves a trigger record field before SQL boolean short-circuiting,
so a deferred event from ``stocktake_round_submissions``,
``stocktake_differences`` or ``stocktake_difference_set_completions`` could
raise ``42703`` even though the row belonged to another table.

This forward-only repair keeps the existing function identity and every
trigger binding.  It replaces only that one branch with nested PL/pgSQL table
dispatch, after pinning the exact 0052 source, function shape, owner, ACL and
all fifteen trigger bindings.  The OAM read-only runtime readiness function is
advanced to this revision with the same exact-source protection.  No schema,
table privilege or business state is changed.  SQLite only advances the
revision because the affected PostgreSQL triggers do not exist there.

Downgrade restores the exact 0052 bodies only when no opening task or reserved
opening evidence exists.  Deployments must still freeze stocktake writes and
drain old transactions before applying this migration; a deferred trigger
event must never cross the function replacement boundary.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260904_0053"
down_revision: Union[str, Sequence[str], None] = "20260903_0052"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
PROJECTOR_ROLE = "star_oam_projector"
EDGE_ROLE = "edge_inbox"
PREVIOUS_SCHEMA_REVISION = "20260903_0052"
FIXED_SEARCH_PATH = "search_path=pg_catalog, public"

GRAPH_CLOSURE_FUNCTION = "rsc_require_opening_live_graph_0052"
GRAPH_CLOSURE_SIGNATURE = f"public.{GRAPH_CLOSURE_FUNCTION}()"
GRAPH_CLOSURE_BODY_SHA256_0052 = (
    "5d9dc35f6ff5a98ded70055d629484bbc9c2aa420f5bd84f3b45070563dd3c24"
)
GRAPH_CLOSURE_BODY_SHA256_0053 = (
    "4fd3e6f9dd5ab04b21d86b9c6575171c1de92a2a54f7391ecd226ac09b4d0564"
)

RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
RUNTIME_READY_SIGNATURE = f"public.{RUNTIME_READY_FUNCTION}()"
RUNTIME_READY_BODY_SHA256_0052 = (
    "7b87874563d011de3c6c02e598892700d4400dd0cccfee38c7f573068c319f12"
)
RUNTIME_READY_BODY_SHA256_0053 = (
    "f40e55742d5b70d0050beb09a1ef367bfd50e02f28b4c3e60d879bec6143b78d"
)

MIGRATION_ERROR = "0053 opening graph table dispatch catalog is invalid"
DOWNGRADE_BLOCKER = "0053 opening graph table dispatch downgrade is unsafe"


GRAPH_CLOSURE_TRIGGER_CATALOG = (
    ("stocktake_count_lines", "trg_stocktake_count_lines_graph_0052", 5),
    ("stocktake_count_serials", "trg_stocktake_count_serials_graph_0052", 5),
    (
        "stocktake_count_observations",
        "trg_stocktake_count_observations_graph_0052",
        5,
    ),
    (
        "stocktake_scope_count_completions",
        "trg_stocktake_scope_count_completions_graph_0052",
        5,
    ),
    (
        "stocktake_round_submissions",
        "trg_stocktake_round_submissions_graph_0052",
        5,
    ),
    ("stocktake_rounds", "trg_stocktake_rounds_graph_0052", 21),
    ("stocktake_reviews", "trg_stocktake_reviews_graph_0052", 5),
    ("stocktake_review_items", "trg_stocktake_review_items_graph_0052", 5),
    ("stocktake_differences", "trg_stocktake_differences_graph_0052", 5),
    (
        "stocktake_difference_set_completions",
        "trg_stocktake_difference_set_completions_graph_0052",
        5,
    ),
    (
        "stocktake_observation_dispositions",
        "trg_stocktake_observation_dispositions_graph_0052",
        5,
    ),
    ("stocktake_postings", "trg_stocktake_postings_graph_0052", 5),
    (
        "state_transition_events",
        "trg_state_transition_events_opening_graph_0052",
        5,
    ),
    ("outbox_events", "trg_outbox_events_opening_graph_0052", 5),
    ("audit_events", "trg_audit_events_opening_graph_0052", 5),
)

# The function reads the complete opening evidence graph.  Locking the same
# boundary as 0052 drains every prior writer and keeps deferred events from
# crossing the source replacement transaction.
LOCK_TABLES = (
    "auth_identities",
    "audit_chain_heads",
    "audit_events",
    "custody_assignments",
    "external_object_versions",
    "external_objects",
    "inventory_freezes",
    "inventory_ledger_heads",
    "inventory_lots",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_opening_establishments",
    "inventory_serials",
    "inventory_transactions",
    "materials",
    "material_inventory_policies",
    "opening_control_reconciliation_command_consumptions",
    "opening_control_reconciliation_runs",
    "organizations",
    "outbox_events",
    "people",
    "permissions",
    "qr_codes",
    "reconciliation_commands",
    "reconciliation_items",
    "role_assignments",
    "role_permissions",
    "roles",
    "serial_current_positions",
    "state_transition_events",
    "stock_accounts",
    "stock_balances",
    "stock_locations",
    "stocktake_control_snapshot_lines",
    "stocktake_count_lines",
    "stocktake_count_observations",
    "stocktake_count_serials",
    "stocktake_difference_set_completions",
    "stocktake_differences",
    "stocktake_observation_dispositions",
    "stocktake_posting_items",
    "stocktake_postings",
    "stocktake_recount_cases",
    "stocktake_recount_scope_assignments",
    "stocktake_review_items",
    "stocktake_reviews",
    "stocktake_round_submissions",
    "stocktake_rounds",
    "stocktake_scope_count_completions",
    "stocktake_scopes",
    "stocktake_snapshot_lines",
    "stocktake_tasks",
    "source_systems",
    "sync_batches",
    "sync_inbox_events",
    "sync_runs",
    "users",
)


GRAPH_ROUND_DISPATCH_0052 = """    IF TG_TABLE_NAME = 'stocktake_rounds' AND NEW.status = 'counting' THEN
        IF (
            NEW.round_type = 'initial'
            AND NEW.round_no = 1
            AND NEW.recount_case_id IS NULL
            AND NOT public.rsc_opening_start_graph_complete_0052(resolved_task_id, FALSE)
        ) OR (
            NEW.round_type = 'recount'
            AND NEW.round_no > 1
            AND (
                NEW.recount_case_id IS NULL
                OR NOT public.rsc_opening_recount_complete_0052(
                    NEW.recount_case_id,
                    FALSE
                )
            )
        ) OR NEW.round_type NOT IN ('initial', 'recount') THEN
            RAISE EXCEPTION '0052 opening immutable child graph is incomplete' USING ERRCODE = '23514';
        END IF;
        RETURN NEW;
    END IF;
"""

GRAPH_ROUND_DISPATCH_0053 = """    IF TG_TABLE_NAME = 'stocktake_rounds' THEN
        IF NEW.status = 'counting' THEN
            IF (
                NEW.round_type = 'initial'
                AND NEW.round_no = 1
                AND NEW.recount_case_id IS NULL
                AND NOT public.rsc_opening_start_graph_complete_0052(resolved_task_id, FALSE)
            ) OR (
                NEW.round_type = 'recount'
                AND NEW.round_no > 1
                AND (
                    NEW.recount_case_id IS NULL
                    OR NOT public.rsc_opening_recount_complete_0052(
                        NEW.recount_case_id,
                        FALSE
                    )
                )
            ) OR NEW.round_type NOT IN ('initial', 'recount') THEN
                RAISE EXCEPTION '0052 opening immutable child graph is incomplete' USING ERRCODE = '23514';
            END IF;
            RETURN NEW;
        END IF;
    END IF;
"""

RUNTIME_READY_REVISION_0052 = "20260903_0052"
RUNTIME_READY_REVISION_0053 = revision


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_boundary_tables()
    _verify_graph_catalog(
        expected_body_sha256=GRAPH_CLOSURE_BODY_SHA256_0052,
        phase="upgrade preflight",
    )
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0052,
        phase="upgrade preflight",
    )
    _replace_function_source(
        signature=GRAPH_CLOSURE_SIGNATURE,
        expected_body_sha256=GRAPH_CLOSURE_BODY_SHA256_0052,
        expected_replacement_body_sha256=GRAPH_CLOSURE_BODY_SHA256_0053,
        source_fragment=GRAPH_ROUND_DISPATCH_0052,
        replacement_fragment=GRAPH_ROUND_DISPATCH_0053,
        phase="graph upgrade",
    )
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0052,
        expected_replacement_body_sha256=RUNTIME_READY_BODY_SHA256_0053,
        source_fragment=RUNTIME_READY_REVISION_0052,
        replacement_fragment=RUNTIME_READY_REVISION_0053,
        phase="readiness upgrade",
    )
    _verify_graph_catalog(
        expected_body_sha256=GRAPH_CLOSURE_BODY_SHA256_0053,
        phase="upgrade postflight",
    )
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0053,
        phase="upgrade postflight",
    )


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_boundary_tables()
    _verify_graph_catalog(
        expected_body_sha256=GRAPH_CLOSURE_BODY_SHA256_0053,
        phase="downgrade preflight",
    )
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0053,
        phase="downgrade preflight",
    )
    _require_no_opening_evidence()
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0053,
        expected_replacement_body_sha256=RUNTIME_READY_BODY_SHA256_0052,
        source_fragment=RUNTIME_READY_REVISION_0053,
        replacement_fragment=RUNTIME_READY_REVISION_0052,
        phase="readiness downgrade",
    )
    _replace_function_source(
        signature=GRAPH_CLOSURE_SIGNATURE,
        expected_body_sha256=GRAPH_CLOSURE_BODY_SHA256_0053,
        expected_replacement_body_sha256=GRAPH_CLOSURE_BODY_SHA256_0052,
        source_fragment=GRAPH_ROUND_DISPATCH_0053,
        replacement_fragment=GRAPH_ROUND_DISPATCH_0052,
        phase="graph downgrade",
    )
    _verify_graph_catalog(
        expected_body_sha256=GRAPH_CLOSURE_BODY_SHA256_0052,
        phase="downgrade postflight",
    )
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0052,
        phase="downgrade postflight",
    )


def _dialect_name() -> str:
    return op.get_bind().dialect.name


def _lock_boundary_tables() -> None:
    op.execute(
        "LOCK TABLE "
        + ", ".join(f"public.{table_name}" for table_name in LOCK_TABLES)
        + " IN ACCESS EXCLUSIVE MODE"
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
            GRAPH_CLOSURE_SIGNATURE,
            GRAPH_CLOSURE_BODY_SHA256_0052,
            GRAPH_CLOSURE_BODY_SHA256_0053,
            GRAPH_ROUND_DISPATCH_0052,
            GRAPH_ROUND_DISPATCH_0053,
        ),
        (
            GRAPH_CLOSURE_SIGNATURE,
            GRAPH_CLOSURE_BODY_SHA256_0053,
            GRAPH_CLOSURE_BODY_SHA256_0052,
            GRAPH_ROUND_DISPATCH_0053,
            GRAPH_ROUND_DISPATCH_0052,
        ),
        (
            RUNTIME_READY_SIGNATURE,
            RUNTIME_READY_BODY_SHA256_0052,
            RUNTIME_READY_BODY_SHA256_0053,
            RUNTIME_READY_REVISION_0052,
            RUNTIME_READY_REVISION_0053,
        ),
        (
            RUNTIME_READY_SIGNATURE,
            RUNTIME_READY_BODY_SHA256_0053,
            RUNTIME_READY_BODY_SHA256_0052,
            RUNTIME_READY_REVISION_0053,
            RUNTIME_READY_REVISION_0052,
        ),
    }
    requested_replacement = (
        signature,
        expected_body_sha256,
        expected_replacement_body_sha256,
        source_fragment,
        replacement_fragment,
    )
    if requested_replacement not in supported_replacements:
        raise ValueError("unsupported 0053 function source replacement")

    escaped_signature = signature.replace("'", "''")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0053_replace$
DECLARE
    function_oid oid;
    function_source text;
    function_definition text;
    replacement_definition text;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: migration role mismatch';
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
                $rsc_0053_source${source_fragment}$rsc_0053_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0053_source${source_fragment}$rsc_0053_source$
    ) <> 1 OR pg_catalog.strpos(
        function_source,
        $rsc_0053_replacement${replacement_fragment}$rsc_0053_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: source mismatch';
    END IF;

    function_definition := pg_catalog.pg_get_functiondef(function_oid);
    IF (
        pg_catalog.length(function_definition)
        - pg_catalog.length(
            pg_catalog.replace(
                function_definition,
                $rsc_0053_source${source_fragment}$rsc_0053_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0053_source${source_fragment}$rsc_0053_source$
    ) <> 1 OR pg_catalog.strpos(
        function_definition,
        $rsc_0053_replacement${replacement_fragment}$rsc_0053_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: definition mismatch';
    END IF;

    replacement_definition := pg_catalog.replace(
        function_definition,
        $rsc_0053_source${source_fragment}$rsc_0053_source$,
        $rsc_0053_replacement${replacement_fragment}$rsc_0053_replacement$
    );
    EXECUTE replacement_definition;

    IF (
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
            '{MIGRATION_ERROR}: {escaped_phase}: replacement hash mismatch';
    END IF;
END
$rsc_0053_replace$
"""
    )


def _verify_graph_catalog(*, expected_body_sha256: str, phase: str) -> None:
    if expected_body_sha256 not in {
        GRAPH_CLOSURE_BODY_SHA256_0052,
        GRAPH_CLOSURE_BODY_SHA256_0053,
    }:
        raise ValueError("unsupported 0053 graph body hash")
    escaped_phase = phase.replace("'", "''")
    trigger_values = ",\n                    ".join(
        f"('{table_name}', '{trigger_name}', {trigger_type})"
        for table_name, trigger_name, trigger_type
        in GRAPH_CLOSURE_TRIGGER_CATALOG
    )
    op.execute(
        f"""
DO $rsc_0053_graph_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    function_oid oid := pg_catalog.to_regprocedure('{GRAPH_CLOSURE_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL OR migrator_oid IS NULL OR function_oid IS NULL
       OR (SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.proname = '{GRAPH_CLOSURE_FUNCTION}') <> 1 THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: graph identity mismatch';
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
           AND function_row.prosecdef
           AND function_row.proconfig = ARRAY['{FIXED_SEARCH_PATH}']::text[]
           AND pg_catalog.encode(
                   pg_catalog.sha256(
                       pg_catalog.convert_to(function_row.prosrc, 'UTF8')
                   ),
                   'hex'
               ) = '{expected_body_sha256}'
    ) THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: graph function mismatch';
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
            '{MIGRATION_ERROR}: {escaped_phase}: graph ACL mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM (VALUES
                    {trigger_values}
               ) AS expected_trigger(table_name, trigger_name, trigger_type)
         WHERE (SELECT pg_catalog.count(*)
                  FROM pg_catalog.pg_trigger AS trigger_row
                  JOIN pg_catalog.pg_class AS table_row
                    ON table_row.oid = trigger_row.tgrelid
                  JOIN pg_catalog.pg_namespace AS namespace_row
                    ON namespace_row.oid = table_row.relnamespace
                 WHERE NOT trigger_row.tgisinternal
                   AND trigger_row.tgname = expected_trigger.trigger_name
                   AND table_row.relname = expected_trigger.table_name
                   AND namespace_row.nspname = 'public'
                   AND trigger_row.tgfoid = function_oid
                   AND trigger_row.tgenabled = 'A'
                   AND trigger_row.tgtype = expected_trigger.trigger_type
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
    ) OR (SELECT pg_catalog.count(*)
            FROM pg_catalog.pg_trigger AS trigger_row
           WHERE NOT trigger_row.tgisinternal
             AND trigger_row.tgfoid = function_oid) <>
             {len(GRAPH_CLOSURE_TRIGGER_CATALOG)} THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: graph trigger mismatch';
    END IF;
END
$rsc_0053_graph_catalog$
"""
    )


def _verify_runtime_ready_catalog(
    *, expected_body_sha256: str, phase: str
) -> None:
    if expected_body_sha256 not in {
        RUNTIME_READY_BODY_SHA256_0052,
        RUNTIME_READY_BODY_SHA256_0053,
    }:
        raise ValueError("unsupported 0053 readiness body hash")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0053_readiness_catalog$
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
            '{MIGRATION_ERROR}: {escaped_phase}: readiness identity mismatch';
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
            '{MIGRATION_ERROR}: {escaped_phase}: readiness function mismatch';
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
            '{MIGRATION_ERROR}: {escaped_phase}: readiness ACL mismatch';
    END IF;
END
$rsc_0053_readiness_catalog$
"""
    )


def _require_no_opening_evidence() -> None:
    op.execute(
        f"""
DO $rsc_0053_downgrade$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_tasks AS task
         WHERE task.task_type = 'opening'
    ) OR EXISTS (
        SELECT 1
          FROM public.state_transition_events AS state_event
         WHERE pg_catalog.left(state_event.reason, 8) = 'opening_'
            OR (
                pg_catalog.left(state_event.idempotency_key, 8) = 'opening-'
                AND pg_catalog.left(state_event.idempotency_key, 23) <>
                    'opening-reconciliation-'
            )
    ) OR EXISTS (
        SELECT 1
          FROM public.outbox_events AS outbox_event
         WHERE pg_catalog.left(outbox_event.event_type, 18) =
               'stocktake.opening.'
            OR (
                pg_catalog.left(outbox_event.idempotency_key, 8) = 'opening-'
                AND pg_catalog.left(outbox_event.idempotency_key, 23) <>
                    'opening-reconciliation-'
            )
    ) OR EXISTS (
        SELECT 1
          FROM public.audit_events AS audit_event
         WHERE pg_catalog.left(audit_event.action, 18) =
               'stocktake.opening.'
    ) OR EXISTS (
        SELECT 1
          FROM public.inventory_transactions AS inventory_transaction
         WHERE inventory_transaction.movement_type = 'opening'
    ) OR EXISTS (
        SELECT 1
          FROM public.stocktake_postings AS posting
         WHERE posting.posting_kind = 'opening'
    ) OR EXISTS (
        SELECT 1 FROM public.inventory_opening_establishments
    ) THEN
        RAISE EXCEPTION '{DOWNGRADE_BLOCKER}';
    END IF;
END
$rsc_0053_downgrade$
"""
    )
