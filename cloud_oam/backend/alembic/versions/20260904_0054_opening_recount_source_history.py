"""Permit sealed opening recount assignments in historical source proof.

Revision ID: 20260904_0054
Revises: 20260904_0053
Create Date: 2026-09-04

The 0052 round-submission helper correctly rejected recount assignments while
an initial round was still the current submission.  The same predicate was
also applied when the sealed initial round was later re-proved as historical
evidence for a newly opened recount.  A valid recount must create exactly those
assignments before its task transition can commit, so the historical proof
could never succeed.

This forward-only repair preserves the existing helper identity, signature,
owner, ACL and callers.  It gates the initial-round downstream-assignment
rejection with ``NOT p_historical``: current submission proof remains strict,
while historical source proof may observe the successor facts that are sealed
independently by the recount graph.  The replacement pins the exact 0053
helper, all six callers, runtime readiness function and the complete opening
write boundary before changing source.  No table, row or privilege is changed.
SQLite only advances the revision because these PostgreSQL functions do not
exist there.

Downgrade restores the exact 0053 sources only when no opening task or reserved
opening evidence exists.  Deployments must freeze opening-stocktake writes and
drain old transactions before either direction; a deferred trigger event must
never cross the function replacement boundary.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260904_0054"
down_revision: Union[str, Sequence[str], None] = "20260904_0053"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
PROJECTOR_ROLE = "star_oam_projector"
EDGE_ROLE = "edge_inbox"
PREVIOUS_SCHEMA_REVISION = "20260904_0053"
FIXED_SEARCH_PATH = "search_path=pg_catalog, public"

ROUND_SUBMISSION_FUNCTION = "rsc_opening_round_submission_complete_0052"
ROUND_SUBMISSION_SIGNATURE = (
    f"public.{ROUND_SUBMISSION_FUNCTION}(uuid, uuid, boolean)"
)
ROUND_SUBMISSION_BODY_SHA256_0053 = (
    "fe1de83cfa07151d62506658cb65307a1823594ecc20df6964846461a897bd3f"
)
ROUND_SUBMISSION_BODY_SHA256_0054 = (
    "d57abd63b6be3b13ac0c19786f76fcc6e7eeaf4ec4dc9bd84d45fbe2452ff206"
)

RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
RUNTIME_READY_SIGNATURE = f"public.{RUNTIME_READY_FUNCTION}()"
RUNTIME_READY_BODY_SHA256_0053 = (
    "f40e55742d5b70d0050beb09a1ef367bfd50e02f28b4c3e60d879bec6143b78d"
)
RUNTIME_READY_BODY_SHA256_0054 = (
    "9be94cc48bd9d15f84d237177f29b929126593426c3cba7b1ad1947a1e66c011"
)

GRAPH_CLOSURE_FUNCTION = "rsc_require_opening_live_graph_0052"
GRAPH_CLOSURE_SIGNATURE = f"public.{GRAPH_CLOSURE_FUNCTION}()"
TERMINAL_COMMIT_FUNCTION = "rsc_require_opening_terminal_graph_0022"
TERMINAL_COMMIT_SIGNATURE = f"public.{TERMINAL_COMMIT_FUNCTION}()"

MIGRATION_ERROR = "0054 opening recount source history catalog is invalid"
DOWNGRADE_BLOCKER = "0054 opening recount source history downgrade is unsafe"


# These are every public function whose pinned 0053 source calls the repaired
# helper.  A migration must fail closed if a caller is added, removed, rebound,
# re-privileged or otherwise changed before this source-only repair runs.
# Fields: signature, name, return type, language, SECURITY DEFINER, body hash,
# exact helper call count, exact direct trigger count, argument types and names.
ROUND_SUBMISSION_CALLER_CATALOG = (
    (
        "public.rsc_opening_observation_disposition_complete_0052(uuid, boolean)",
        "rsc_opening_observation_disposition_complete_0052",
        "boolean",
        "sql",
        False,
        "4a5006029f95b5425704ba2b994c0ee2ff242c74e4e5c1660d368a400ffe5b0b",
        2,
        0,
        ("uuid", "boolean"),
        ("p_disposition_id", "p_historical"),
    ),
    (
        "public.rsc_opening_recount_complete_0052(uuid, boolean)",
        "rsc_opening_recount_complete_0052",
        "boolean",
        "sql",
        False,
        "b3a26276b6b46f8e7bcbbfbd5a33f9e8171be582f471ed330dba69ea85ebcd0f",
        2,
        0,
        ("uuid", "boolean"),
        ("p_recount_case_id", "p_historical"),
    ),
    (
        "public.rsc_opening_review_complete_0052(uuid, boolean)",
        "rsc_opening_review_complete_0052",
        "boolean",
        "sql",
        False,
        "f6b614e42e35da34e072cd12ba103688163a53fc763d3c48979883813369126a",
        2,
        0,
        ("uuid", "boolean"),
        ("p_review_id", "p_historical"),
    ),
    (
        "public.rsc_opening_scope_count_complete_0052(uuid, uuid, uuid, boolean)",
        "rsc_opening_scope_count_complete_0052",
        "boolean",
        "sql",
        False,
        "e0c2628cdf871c1b4e7adb8234fdce6ef1dd3ee9dc80930b8cb71684f670b1e7",
        1,
        0,
        ("uuid", "uuid", "uuid", "boolean"),
        ("p_task_id", "p_round_id", "p_scope_id", "p_historical"),
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        GRAPH_CLOSURE_FUNCTION,
        "trigger",
        "plpgsql",
        True,
        "4fd3e6f9dd5ab04b21d86b9c6575171c1de92a2a54f7391ecd226ac09b4d0564",
        5,
        15,
        (),
        (),
    ),
    (
        TERMINAL_COMMIT_SIGNATURE,
        TERMINAL_COMMIT_FUNCTION,
        "trigger",
        "plpgsql",
        True,
        "4678c65493a2ca0c8d596343053977db4d93d7758e00f1291e30e6be038d36e8",
        2,
        11,
        (),
        (),
    ),
)


# These are every direct trigger binding for the two trigger callers above.
# Counting bindings is insufficient: one legitimate trigger could otherwise be
# replaced by an equal-count alias on another table, including a relation not
# covered by the writer-draining lock boundary.  All 26 bindings are deferred
# constraint triggers, enabled ALWAYS, without transition tables, WHEN clauses
# or trigger arguments.
# Fields: caller signature, table, trigger, PostgreSQL tgtype.
ROUND_SUBMISSION_TRIGGER_CATALOG = (
    (
        GRAPH_CLOSURE_SIGNATURE,
        "stocktake_count_lines",
        "trg_stocktake_count_lines_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "stocktake_count_serials",
        "trg_stocktake_count_serials_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "stocktake_count_observations",
        "trg_stocktake_count_observations_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "stocktake_scope_count_completions",
        "trg_stocktake_scope_count_completions_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "stocktake_round_submissions",
        "trg_stocktake_round_submissions_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "stocktake_rounds",
        "trg_stocktake_rounds_graph_0052",
        21,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "stocktake_reviews",
        "trg_stocktake_reviews_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "stocktake_review_items",
        "trg_stocktake_review_items_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "stocktake_differences",
        "trg_stocktake_differences_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "stocktake_difference_set_completions",
        "trg_stocktake_difference_set_completions_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "stocktake_observation_dispositions",
        "trg_stocktake_observation_dispositions_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "stocktake_postings",
        "trg_stocktake_postings_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "state_transition_events",
        "trg_state_transition_events_opening_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "outbox_events",
        "trg_outbox_events_opening_graph_0052",
        5,
    ),
    (
        GRAPH_CLOSURE_SIGNATURE,
        "audit_events",
        "trg_audit_events_opening_graph_0052",
        5,
    ),
    (
        TERMINAL_COMMIT_SIGNATURE,
        "inventory_transactions",
        "trg_inventory_transactions_opening_commit_0022",
        5,
    ),
    (
        TERMINAL_COMMIT_SIGNATURE,
        "inventory_movements",
        "trg_inventory_movements_opening_commit_0022",
        5,
    ),
    (
        TERMINAL_COMMIT_SIGNATURE,
        "inventory_movement_serials",
        "trg_inventory_movement_serials_opening_commit_0022",
        5,
    ),
    (
        TERMINAL_COMMIT_SIGNATURE,
        "stocktake_postings",
        "trg_stocktake_postings_opening_commit_0022",
        5,
    ),
    (
        TERMINAL_COMMIT_SIGNATURE,
        "stocktake_posting_items",
        "trg_stocktake_posting_items_opening_commit_0022",
        5,
    ),
    (
        TERMINAL_COMMIT_SIGNATURE,
        "inventory_opening_establishments",
        "trg_inventory_opening_establishments_commit_0022",
        5,
    ),
    (
        TERMINAL_COMMIT_SIGNATURE,
        "inventory_freezes",
        "trg_inventory_freezes_opening_commit_0022",
        17,
    ),
    (
        TERMINAL_COMMIT_SIGNATURE,
        "stocktake_tasks",
        "trg_stocktake_tasks_opening_commit_0022",
        17,
    ),
    (
        TERMINAL_COMMIT_SIGNATURE,
        "state_transition_events",
        "trg_state_transition_events_opening_commit_0022",
        5,
    ),
    (
        TERMINAL_COMMIT_SIGNATURE,
        "outbox_events",
        "trg_outbox_events_opening_commit_0022",
        5,
    ),
    (
        TERMINAL_COMMIT_SIGNATURE,
        "audit_events",
        "trg_audit_events_opening_commit_0022",
        5,
    ),
)


# Match the complete 0052/0053 opening boundary.  ACCESS EXCLUSIVE drains all
# earlier writers, including deferred triggers that may call the helper through
# one of the six pinned callers.
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


INITIAL_ASSIGNMENT_REJECTION_0053 = """helper_round.round_type = 'initial'
                       AND EXISTS (
                           SELECT 1
                             FROM public.stocktake_recount_scope_assignments
                                  AS unexpected_assignment
                            WHERE unexpected_assignment.task_id = p_task_id
                              AND unexpected_assignment.source_round_id =
                                  helper_round.id
                              AND unexpected_assignment.scope_id =
                                  sealed_completion.scope_id
                       )"""

INITIAL_ASSIGNMENT_REJECTION_0054 = """helper_round.round_type = 'initial'
                       AND NOT p_historical
                       AND EXISTS (
                           SELECT 1
                             FROM public.stocktake_recount_scope_assignments
                                  AS unexpected_assignment
                            WHERE unexpected_assignment.task_id = p_task_id
                              AND unexpected_assignment.source_round_id =
                                  helper_round.id
                              AND unexpected_assignment.scope_id =
                                  sealed_completion.scope_id
                       )"""

RUNTIME_READY_REVISION_0053 = PREVIOUS_SCHEMA_REVISION
RUNTIME_READY_REVISION_0054 = revision


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_boundary_tables()
    _verify_round_submission_catalog(
        expected_body_sha256=ROUND_SUBMISSION_BODY_SHA256_0053,
        phase="upgrade preflight",
    )
    _verify_round_submission_callers(phase="upgrade preflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0053,
        phase="upgrade preflight",
    )
    _replace_function_source(
        signature=ROUND_SUBMISSION_SIGNATURE,
        expected_body_sha256=ROUND_SUBMISSION_BODY_SHA256_0053,
        expected_replacement_body_sha256=ROUND_SUBMISSION_BODY_SHA256_0054,
        source_fragment=INITIAL_ASSIGNMENT_REJECTION_0053,
        replacement_fragment=INITIAL_ASSIGNMENT_REJECTION_0054,
        expected_source_count=2,
        phase="round submission upgrade",
    )
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0053,
        expected_replacement_body_sha256=RUNTIME_READY_BODY_SHA256_0054,
        source_fragment=RUNTIME_READY_REVISION_0053,
        replacement_fragment=RUNTIME_READY_REVISION_0054,
        expected_source_count=1,
        phase="readiness upgrade",
    )
    _verify_round_submission_catalog(
        expected_body_sha256=ROUND_SUBMISSION_BODY_SHA256_0054,
        phase="upgrade postflight",
    )
    _verify_round_submission_callers(phase="upgrade postflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0054,
        phase="upgrade postflight",
    )


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_boundary_tables()
    _verify_round_submission_catalog(
        expected_body_sha256=ROUND_SUBMISSION_BODY_SHA256_0054,
        phase="downgrade preflight",
    )
    _verify_round_submission_callers(phase="downgrade preflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0054,
        phase="downgrade preflight",
    )
    _require_no_opening_evidence()
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0054,
        expected_replacement_body_sha256=RUNTIME_READY_BODY_SHA256_0053,
        source_fragment=RUNTIME_READY_REVISION_0054,
        replacement_fragment=RUNTIME_READY_REVISION_0053,
        expected_source_count=1,
        phase="readiness downgrade",
    )
    _replace_function_source(
        signature=ROUND_SUBMISSION_SIGNATURE,
        expected_body_sha256=ROUND_SUBMISSION_BODY_SHA256_0054,
        expected_replacement_body_sha256=ROUND_SUBMISSION_BODY_SHA256_0053,
        source_fragment=INITIAL_ASSIGNMENT_REJECTION_0054,
        replacement_fragment=INITIAL_ASSIGNMENT_REJECTION_0053,
        expected_source_count=2,
        phase="round submission downgrade",
    )
    _verify_round_submission_catalog(
        expected_body_sha256=ROUND_SUBMISSION_BODY_SHA256_0053,
        phase="downgrade postflight",
    )
    _verify_round_submission_callers(phase="downgrade postflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0053,
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
    expected_source_count: int,
    phase: str,
) -> None:
    supported_replacements = {
        (
            ROUND_SUBMISSION_SIGNATURE,
            ROUND_SUBMISSION_BODY_SHA256_0053,
            ROUND_SUBMISSION_BODY_SHA256_0054,
            INITIAL_ASSIGNMENT_REJECTION_0053,
            INITIAL_ASSIGNMENT_REJECTION_0054,
            2,
        ),
        (
            ROUND_SUBMISSION_SIGNATURE,
            ROUND_SUBMISSION_BODY_SHA256_0054,
            ROUND_SUBMISSION_BODY_SHA256_0053,
            INITIAL_ASSIGNMENT_REJECTION_0054,
            INITIAL_ASSIGNMENT_REJECTION_0053,
            2,
        ),
        (
            RUNTIME_READY_SIGNATURE,
            RUNTIME_READY_BODY_SHA256_0053,
            RUNTIME_READY_BODY_SHA256_0054,
            RUNTIME_READY_REVISION_0053,
            RUNTIME_READY_REVISION_0054,
            1,
        ),
        (
            RUNTIME_READY_SIGNATURE,
            RUNTIME_READY_BODY_SHA256_0054,
            RUNTIME_READY_BODY_SHA256_0053,
            RUNTIME_READY_REVISION_0054,
            RUNTIME_READY_REVISION_0053,
            1,
        ),
    }
    requested_replacement = (
        signature,
        expected_body_sha256,
        expected_replacement_body_sha256,
        source_fragment,
        replacement_fragment,
        expected_source_count,
    )
    if requested_replacement not in supported_replacements:
        raise ValueError("unsupported 0054 function source replacement")

    escaped_signature = signature.replace("'", "''")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0054_replace$
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
                $rsc_0054_source${source_fragment}$rsc_0054_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0054_source${source_fragment}$rsc_0054_source$
    ) <> {expected_source_count} OR pg_catalog.strpos(
        function_source,
        $rsc_0054_replacement${replacement_fragment}$rsc_0054_replacement$
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
                $rsc_0054_source${source_fragment}$rsc_0054_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0054_source${source_fragment}$rsc_0054_source$
    ) <> {expected_source_count} OR pg_catalog.strpos(
        function_definition,
        $rsc_0054_replacement${replacement_fragment}$rsc_0054_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: definition mismatch';
    END IF;

    replacement_definition := pg_catalog.replace(
        function_definition,
        $rsc_0054_source${source_fragment}$rsc_0054_source$,
        $rsc_0054_replacement${replacement_fragment}$rsc_0054_replacement$
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
            '{MIGRATION_ERROR}: {escaped_phase}: replacement mismatch';
    END IF;
END
$rsc_0054_replace$
"""
    )


def _verify_round_submission_catalog(
    *, expected_body_sha256: str, phase: str
) -> None:
    if expected_body_sha256 not in {
        ROUND_SUBMISSION_BODY_SHA256_0053,
        ROUND_SUBMISSION_BODY_SHA256_0054,
    }:
        raise ValueError("unsupported 0054 round submission body hash")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0054_round_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    function_oid oid := pg_catalog.to_regprocedure('{ROUND_SUBMISSION_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL OR function_oid IS NULL OR migrator_oid IS NULL
       OR (SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.proname = '{ROUND_SUBMISSION_FUNCTION}') <> 1 THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: helper identity mismatch';
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
           AND function_row.pronargs = 3
           AND pg_catalog.oidvectortypes(function_row.proargtypes) =
               'uuid, uuid, boolean'
           AND function_row.proargnames =
               ARRAY['p_task_id', 'p_round_id', 'p_historical']::text[]
           AND function_row.proallargtypes IS NULL
           AND function_row.proargmodes IS NULL
           AND function_row.pronargdefaults = 0
           AND function_row.proargdefaults IS NULL
           AND function_row.provariadic = 0
           AND language_row.lanname = 'sql'
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
    ) OR EXISTS (
        SELECT 1
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid = function_oid
    ) THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: helper function mismatch';
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
            '{MIGRATION_ERROR}: {escaped_phase}: helper ACL mismatch';
    END IF;
END
$rsc_0054_round_catalog$
"""
    )


def _verify_round_submission_callers(*, phase: str) -> None:
    escaped_phase = phase.replace("'", "''")
    caller_values = ",\n                    ".join(
        "(" + ", ".join(
            (
                f"'{signature}'",
                f"'{function_name}'",
                f"'{return_type}'",
                f"'{language_name}'",
                "TRUE" if security_definer else "FALSE",
                f"'{body_sha256}'",
                str(call_count),
                str(trigger_count),
                str(len(argument_types)),
                f"'{', '.join(argument_types)}'",
                (
                    f"'{','.join(argument_names)}'"
                    if argument_names
                    else "NULL::text"
                ),
            )
        ) + ")"
        for (
            signature,
            function_name,
            return_type,
            language_name,
            security_definer,
            body_sha256,
            call_count,
            trigger_count,
            argument_types,
            argument_names,
        ) in ROUND_SUBMISSION_CALLER_CATALOG
    )
    trigger_values = ",\n                    ".join(
        f"('{signature}', '{table_name}', '{trigger_name}', {trigger_type})"
        for signature, table_name, trigger_name, trigger_type
        in ROUND_SUBMISSION_TRIGGER_CATALOG
    )
    trigger_function_literals = ", ".join(
        f"'{signature}'::pg_catalog.regprocedure"
        for signature in (GRAPH_CLOSURE_SIGNATURE, TERMINAL_COMMIT_SIGNATURE)
    )
    op.execute(
        f"""
DO $rsc_0054_caller_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL OR migrator_oid IS NULL THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: caller role mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM (VALUES
                    {caller_values}
               ) AS expected_caller(
                   signature,
                   function_name,
                   return_type,
                   language_name,
                   security_definer,
                   body_sha256,
                   call_count,
                   trigger_count,
                   argument_count,
                   argument_types,
                   argument_names
               )
         WHERE NOT EXISTS (
             SELECT 1
               FROM pg_catalog.pg_proc AS function_row
               JOIN pg_catalog.pg_namespace AS namespace_row
                 ON namespace_row.oid = function_row.pronamespace
               JOIN pg_catalog.pg_language AS language_row
                 ON language_row.oid = function_row.prolang
              WHERE function_row.oid =
                    pg_catalog.to_regprocedure(expected_caller.signature)
                AND namespace_row.nspname = 'public'
                AND function_row.proname = expected_caller.function_name
                AND function_row.proowner = migrator_oid
                AND function_row.prokind = 'f'
                AND function_row.prorettype =
                    expected_caller.return_type::pg_catalog.regtype
                AND NOT function_row.proretset
                AND function_row.pronargs = expected_caller.argument_count
                AND pg_catalog.oidvectortypes(function_row.proargtypes) =
                    expected_caller.argument_types
                AND pg_catalog.array_to_string(
                        function_row.proargnames,
                        ','
                    ) IS NOT DISTINCT FROM expected_caller.argument_names
                AND function_row.proallargtypes IS NULL
                AND function_row.proargmodes IS NULL
                AND function_row.pronargdefaults = 0
                AND function_row.proargdefaults IS NULL
                AND function_row.provariadic = 0
                AND language_row.lanname = expected_caller.language_name
                AND function_row.provolatile = 'v'
                AND NOT function_row.proisstrict
                AND NOT function_row.proleakproof
                AND function_row.proparallel = 'u'
                AND function_row.prosecdef = expected_caller.security_definer
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
                            '{ROUND_SUBMISSION_FUNCTION}',
                            ''
                        )
                    )
                ) / pg_catalog.length('{ROUND_SUBMISSION_FUNCTION}') =
                    expected_caller.call_count
                AND NOT pg_catalog.has_function_privilege(
                    api_oid,
                    function_row.oid,
                    'EXECUTE'
                )
                AND NOT EXISTS (
                    SELECT 1
                      FROM pg_catalog.aclexplode(
                          COALESCE(
                              function_row.proacl,
                              pg_catalog.acldefault(
                                  'f', function_row.proowner
                              )
                          )
                      ) AS function_acl
                     WHERE function_acl.privilege_type <> 'EXECUTE'
                        OR function_acl.grantee <> migrator_oid
                        OR function_acl.grantor <> migrator_oid
                        OR function_acl.is_grantable
                )
                AND (
                    SELECT pg_catalog.count(*)
                      FROM pg_catalog.aclexplode(
                          COALESCE(
                              function_row.proacl,
                              pg_catalog.acldefault(
                                  'f', function_row.proowner
                              )
                          )
                      ) AS function_acl
                     WHERE function_acl.privilege_type = 'EXECUTE'
                       AND function_acl.grantee = migrator_oid
                       AND function_acl.grantor = migrator_oid
                       AND NOT function_acl.is_grantable
                ) = 1
                AND (
                    SELECT pg_catalog.count(*)
                      FROM pg_catalog.pg_trigger AS trigger_row
                     WHERE NOT trigger_row.tgisinternal
                       AND trigger_row.tgfoid = function_row.oid
                ) = expected_caller.trigger_count
                AND (
                    SELECT pg_catalog.count(*)
                      FROM pg_catalog.pg_proc AS named_function
                     WHERE named_function.proname =
                           expected_caller.function_name
                ) = 1
         )
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS candidate
         WHERE pg_catalog.strpos(
               candidate.prosrc,
               '{ROUND_SUBMISSION_FUNCTION}'
           ) > 0
    ) <> {len(ROUND_SUBMISSION_CALLER_CATALOG)} THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: caller catalog mismatch';
    END IF;
    IF EXISTS (
        SELECT 1
          FROM (VALUES
                    {trigger_values}
               ) AS expected_trigger(
                   function_signature,
                   table_name,
                   trigger_name,
                   trigger_type
               )
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
                   AND trigger_row.tgfoid = pg_catalog.to_regprocedure(
                       expected_trigger.function_signature
                   )
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
                   AND trigger_row.tgattr =
                       ''::pg_catalog.int2vector) <> 1
            OR (SELECT pg_catalog.count(*)
                  FROM pg_catalog.pg_trigger AS trigger_row
                 WHERE NOT trigger_row.tgisinternal
                   AND trigger_row.tgname =
                       expected_trigger.trigger_name) <> 1
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid IN ({trigger_function_literals})
    ) <> {len(ROUND_SUBMISSION_TRIGGER_CATALOG)} THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: caller trigger mismatch';
    END IF;
END
$rsc_0054_caller_catalog$
"""
    )


def _verify_runtime_ready_catalog(
    *, expected_body_sha256: str, phase: str
) -> None:
    if expected_body_sha256 not in {
        RUNTIME_READY_BODY_SHA256_0053,
        RUNTIME_READY_BODY_SHA256_0054,
    }:
        raise ValueError("unsupported 0054 readiness body hash")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0054_readiness_catalog$
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
$rsc_0054_readiness_catalog$
"""
    )


def _require_no_opening_evidence() -> None:
    op.execute(
        f"""
DO $rsc_0054_downgrade$
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
$rsc_0054_downgrade$
"""
    )
