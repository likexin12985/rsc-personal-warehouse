"""Repair non-opening stocktake start audit ordering.

Revision ID: 20260905_0055
Revises: 20260904_0054
Create Date: 2026-09-05

Revision 0047 seals a non-opening stocktake start with a PostgreSQL-computed
graph manifest.  Its guard and validator serialize the matching audit event
with ``stream_version`` but accidentally order that row by the nonexistent
``audit_events.sequence_no`` column.  PostgreSQL resolves the expression when
the completion fact is inserted, so every real non-opening start fails with
``42703`` before the transaction can commit.

This forward repair keeps revision 0047 immutable.  It drains the complete
start-evidence write boundary, pins the exact function identities, shapes,
owners, ACLs, caller and sixteen trigger bindings, verifies the canonical
``(stream_key, stream_version)`` audit sequence, and replaces only the invalid
ORDER BY fragment in the guard and validator.  ``pg_get_functiondef`` plus
``CREATE OR REPLACE`` preserves both function OIDs and every trigger binding.
The OAM read-only readiness function advances to this revision only after the
repair is proven.  No table, business row or privilege is changed.

Downgrade restores the exact 0054 source only while no non-opening start
completion exists.  Deployments must freeze stocktake writes and drain old
transactions before either direction; a deferred event must never cross the
function replacement boundary.  SQLite only advances the revision because
these PostgreSQL functions and constraint triggers do not exist there.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260905_0055"
down_revision: Union[str, Sequence[str], None] = "20260904_0054"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
PROJECTOR_ROLE = "star_oam_projector"
EDGE_ROLE = "edge_inbox"
PREVIOUS_SCHEMA_REVISION = "20260904_0054"
FIXED_SEARCH_PATH = "search_path=pg_catalog, public"

GUARD_FUNCTION = "rsc_guard_stocktake_start_completion_0047"
GUARD_SIGNATURE = f"public.{GUARD_FUNCTION}()"
VALIDATOR_FUNCTION = "rsc_validate_nonopening_stocktake_start_causality_0047"
VALIDATOR_SIGNATURE = f"public.{VALIDATOR_FUNCTION}(uuid)"
DISPATCH_FUNCTION = "rsc_dispatch_nonopening_stocktake_start_causality_0047"
DISPATCH_SIGNATURE = f"public.{DISPATCH_FUNCTION}()"
RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
RUNTIME_READY_SIGNATURE = f"public.{RUNTIME_READY_FUNCTION}()"

GUARD_BODY_SHA256_0054 = (
    "7d3729edf7f14a3f3be228b02fc25ad51a296c3ef972245271cab74ea589c2f3"
)
GUARD_BODY_SHA256_0055 = (
    "19fcb84567c36bbcf426eac4f844bfa70e32ee3247f50c402a444171c5879718"
)
VALIDATOR_BODY_SHA256_0054 = (
    "fac0c8ebdb46d53b1eec62f5ffb15e69a21a5975b9a99445270968f23d27ebe9"
)
VALIDATOR_BODY_SHA256_0055 = (
    "82537a53254a6493eea33f07795b8b47d941ecc6bd25ed8dcab8c35bcf18d3f8"
)
DISPATCH_BODY_SHA256 = (
    "7ae3cda26d356cabf5529bae88eb33e0f85bcab538e8ab9b8bb256eebf54ac40"
)
RUNTIME_READY_BODY_SHA256_0054 = (
    "9be94cc48bd9d15f84d237177f29b929126593426c3cba7b1ad1947a1e66c011"
)
RUNTIME_READY_BODY_SHA256_0055 = (
    "3f6b6b7a849746154cbdd54ff8c1d3153aa24faa3cf081b6e0d3d6ef832c7f70"
)

LEGACY_AUDIT_ORDER = "ORDER BY audit.sequence_no, audit.id"
FIXED_AUDIT_ORDER = "ORDER BY audit.stream_version, audit.id"
RUNTIME_READY_REVISION_0054 = PREVIOUS_SCHEMA_REVISION
RUNTIME_READY_REVISION_0055 = revision

MIGRATION_ERROR = "0055 non-opening start audit order catalog is invalid"
DOWNGRADE_BLOCKER = "0055 non-opening start audit order downgrade is unsafe"
NONOPENING_SQL = "('full', 'sample', 'ad_hoc', 'personal', 'termination')"
COMMAND_SCHEMA = "cloud_oam.stocktake.command.v1"


# Every table read by the 0047 guard/validator graph.  ACCESS EXCLUSIVE drains
# earlier writers before either function body changes, including transactions
# whose deferred dispatcher has not run yet.
LOCK_TABLES = (
    "audit_events",
    "auth_identities",
    "custody_assignments",
    "inventory_freezes",
    "inventory_ledger_heads",
    "inventory_lots",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_serials",
    "inventory_transactions",
    "material_inventory_policies",
    "materials",
    "organizations",
    "people",
    "permissions",
    "role_assignments",
    "role_permissions",
    "roles",
    "serial_current_positions",
    "state_transition_events",
    "stock_accounts",
    "stock_balances",
    "stock_locations",
    "stocktake_postings",
    "stocktake_rounds",
    "stocktake_scopes",
    "stocktake_snapshot_lines",
    "stocktake_start_completions",
    "stocktake_tasks",
    "users",
)


DEFERRED_TABLES = (
    "stocktake_tasks",
    "stocktake_scopes",
    "inventory_freezes",
    "stocktake_snapshot_lines",
    "stocktake_rounds",
    "stocktake_start_completions",
    "state_transition_events",
    "audit_events",
)


# Fields: signature, name, result, argument types, argument names,
# 0054 body hash, 0055 body hash, direct trigger count.
START_FUNCTION_CATALOG = (
    (
        GUARD_SIGNATURE,
        GUARD_FUNCTION,
        "trigger",
        (),
        (),
        GUARD_BODY_SHA256_0054,
        GUARD_BODY_SHA256_0055,
        8,
    ),
    (
        VALIDATOR_SIGNATURE,
        VALIDATOR_FUNCTION,
        "void",
        ("uuid",),
        ("checked_task_id",),
        VALIDATOR_BODY_SHA256_0054,
        VALIDATOR_BODY_SHA256_0055,
        0,
    ),
)


# All ordinary BEFORE triggers owned by the guard.  PostgreSQL tgtype 31 is
# row-level BEFORE INSERT OR DELETE OR UPDATE.
GUARD_TRIGGER_CATALOG = (
    (
        "stocktake_start_completions",
        "trg_stocktake_start_completions_guard_0047",
        31,
    ),
    *tuple(
        (
            table_name,
            f"trg_{table_name}_stocktake_start_sealed_0047",
            31,
        )
        for table_name in LOCK_TABLES
        if table_name in DEFERRED_TABLES
        if table_name != "stocktake_start_completions"
    ),
)


# All deferred AFTER triggers owned by the unchanged dispatcher.  PostgreSQL
# tgtype 29 is row-level AFTER INSERT OR DELETE OR UPDATE.
DISPATCH_TRIGGER_CATALOG = tuple(
    (
        table_name,
        f"trg_{table_name}_stocktake_start_causality_0047",
        29,
    )
    for table_name in DEFERRED_TABLES
)


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_boundary_tables()
    _verify_audit_sequence_catalog(phase="upgrade preflight")
    _verify_start_function_catalog(fixed=False, phase="upgrade preflight")
    _verify_dispatch_catalog(phase="upgrade preflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0054,
        phase="upgrade preflight",
    )
    _replace_function_source(
        signature=GUARD_SIGNATURE,
        expected_body_sha256=GUARD_BODY_SHA256_0054,
        expected_replacement_body_sha256=GUARD_BODY_SHA256_0055,
        source_fragment=LEGACY_AUDIT_ORDER,
        replacement_fragment=FIXED_AUDIT_ORDER,
        phase="guard upgrade",
    )
    _replace_function_source(
        signature=VALIDATOR_SIGNATURE,
        expected_body_sha256=VALIDATOR_BODY_SHA256_0054,
        expected_replacement_body_sha256=VALIDATOR_BODY_SHA256_0055,
        source_fragment=LEGACY_AUDIT_ORDER,
        replacement_fragment=FIXED_AUDIT_ORDER,
        phase="validator upgrade",
    )
    _verify_start_function_catalog(fixed=True, phase="upgrade replacement")
    _verify_dispatch_catalog(phase="upgrade replacement")
    _validate_existing_start_graphs(phase="upgrade replacement")
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0054,
        expected_replacement_body_sha256=RUNTIME_READY_BODY_SHA256_0055,
        source_fragment=RUNTIME_READY_REVISION_0054,
        replacement_fragment=RUNTIME_READY_REVISION_0055,
        phase="readiness upgrade",
    )
    _verify_audit_sequence_catalog(phase="upgrade postflight")
    _verify_start_function_catalog(fixed=True, phase="upgrade postflight")
    _verify_dispatch_catalog(phase="upgrade postflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0055,
        phase="upgrade postflight",
    )


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_boundary_tables()
    _verify_audit_sequence_catalog(phase="downgrade preflight")
    _verify_start_function_catalog(fixed=True, phase="downgrade preflight")
    _verify_dispatch_catalog(phase="downgrade preflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0055,
        phase="downgrade preflight",
    )
    _require_no_start_graph()
    _replace_function_source(
        signature=RUNTIME_READY_SIGNATURE,
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0055,
        expected_replacement_body_sha256=RUNTIME_READY_BODY_SHA256_0054,
        source_fragment=RUNTIME_READY_REVISION_0055,
        replacement_fragment=RUNTIME_READY_REVISION_0054,
        phase="readiness downgrade",
    )
    _replace_function_source(
        signature=VALIDATOR_SIGNATURE,
        expected_body_sha256=VALIDATOR_BODY_SHA256_0055,
        expected_replacement_body_sha256=VALIDATOR_BODY_SHA256_0054,
        source_fragment=FIXED_AUDIT_ORDER,
        replacement_fragment=LEGACY_AUDIT_ORDER,
        phase="validator downgrade",
    )
    _replace_function_source(
        signature=GUARD_SIGNATURE,
        expected_body_sha256=GUARD_BODY_SHA256_0055,
        expected_replacement_body_sha256=GUARD_BODY_SHA256_0054,
        source_fragment=FIXED_AUDIT_ORDER,
        replacement_fragment=LEGACY_AUDIT_ORDER,
        phase="guard downgrade",
    )
    _verify_start_function_catalog(fixed=False, phase="downgrade postflight")
    _verify_dispatch_catalog(phase="downgrade postflight")
    _verify_runtime_ready_catalog(
        expected_body_sha256=RUNTIME_READY_BODY_SHA256_0054,
        phase="downgrade postflight",
    )


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0055 supports only PostgreSQL and SQLite")
    return dialect


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
            GUARD_SIGNATURE,
            GUARD_BODY_SHA256_0054,
            GUARD_BODY_SHA256_0055,
            LEGACY_AUDIT_ORDER,
            FIXED_AUDIT_ORDER,
        ),
        (
            GUARD_SIGNATURE,
            GUARD_BODY_SHA256_0055,
            GUARD_BODY_SHA256_0054,
            FIXED_AUDIT_ORDER,
            LEGACY_AUDIT_ORDER,
        ),
        (
            VALIDATOR_SIGNATURE,
            VALIDATOR_BODY_SHA256_0054,
            VALIDATOR_BODY_SHA256_0055,
            LEGACY_AUDIT_ORDER,
            FIXED_AUDIT_ORDER,
        ),
        (
            VALIDATOR_SIGNATURE,
            VALIDATOR_BODY_SHA256_0055,
            VALIDATOR_BODY_SHA256_0054,
            FIXED_AUDIT_ORDER,
            LEGACY_AUDIT_ORDER,
        ),
        (
            RUNTIME_READY_SIGNATURE,
            RUNTIME_READY_BODY_SHA256_0054,
            RUNTIME_READY_BODY_SHA256_0055,
            RUNTIME_READY_REVISION_0054,
            RUNTIME_READY_REVISION_0055,
        ),
        (
            RUNTIME_READY_SIGNATURE,
            RUNTIME_READY_BODY_SHA256_0055,
            RUNTIME_READY_BODY_SHA256_0054,
            RUNTIME_READY_REVISION_0055,
            RUNTIME_READY_REVISION_0054,
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
        raise ValueError("unsupported 0055 function source replacement")

    escaped_signature = signature.replace("'", "''")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0055_replace$
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
                $rsc_0055_source${source_fragment}$rsc_0055_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0055_source${source_fragment}$rsc_0055_source$
    ) <> 1 OR pg_catalog.strpos(
        function_source,
        $rsc_0055_replacement${replacement_fragment}$rsc_0055_replacement$
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
                $rsc_0055_source${source_fragment}$rsc_0055_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0055_source${source_fragment}$rsc_0055_source$
    ) <> 1 OR pg_catalog.strpos(
        function_definition,
        $rsc_0055_replacement${replacement_fragment}$rsc_0055_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: definition mismatch';
    END IF;

    replacement_definition := pg_catalog.replace(
        function_definition,
        $rsc_0055_source${source_fragment}$rsc_0055_source$,
        $rsc_0055_replacement${replacement_fragment}$rsc_0055_replacement$
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
$rsc_0055_replace$
"""
    )


def _verify_audit_sequence_catalog(*, phase: str) -> None:
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0055_audit_catalog$
DECLARE
    audit_table_oid oid := pg_catalog.to_regclass('public.audit_events');
    stream_key_attnum smallint;
    stream_version_attnum smallint;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR audit_table_oid IS NULL THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: audit identity mismatch';
    END IF;

    SELECT attribute_row.attnum
      INTO stream_key_attnum
      FROM pg_catalog.pg_attribute AS attribute_row
     WHERE attribute_row.attrelid = audit_table_oid
       AND attribute_row.attname = 'stream_key'
       AND attribute_row.atttypid = 'character varying'::pg_catalog.regtype
       AND attribute_row.atttypmod = 164
       AND attribute_row.attnotnull
       AND attribute_row.attnum > 0
       AND NOT attribute_row.attisdropped;
    SELECT attribute_row.attnum
      INTO stream_version_attnum
      FROM pg_catalog.pg_attribute AS attribute_row
     WHERE attribute_row.attrelid = audit_table_oid
       AND attribute_row.attname = 'stream_version'
       AND attribute_row.atttypid = 'bigint'::pg_catalog.regtype
       AND attribute_row.attnotnull
       AND attribute_row.attnum > 0
       AND NOT attribute_row.attisdropped;

    IF stream_key_attnum IS NULL OR stream_version_attnum IS NULL
       OR EXISTS (
           SELECT 1
             FROM pg_catalog.pg_attribute AS attribute_row
            WHERE attribute_row.attrelid = audit_table_oid
              AND attribute_row.attname = 'sequence_no'
              AND attribute_row.attnum > 0
              AND NOT attribute_row.attisdropped
       ) OR NOT EXISTS (
           SELECT 1
             FROM pg_catalog.pg_constraint AS constraint_row
            WHERE constraint_row.conrelid = audit_table_oid
              AND constraint_row.conname =
                  'uq_audit_events_stream_version_0017'
              AND constraint_row.contype = 'u'
              AND constraint_row.convalidated
              AND constraint_row.conkey = ARRAY[
                  stream_key_attnum, stream_version_attnum
              ]::smallint[]
       ) THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: audit sequence mismatch';
    END IF;
END
$rsc_0055_audit_catalog$
"""
    )


def _verify_start_function_catalog(*, fixed: bool, phase: str) -> None:
    if not isinstance(fixed, bool):
        raise ValueError("unsupported 0055 start function state")
    escaped_phase = phase.replace("'", "''")
    function_values = ",\n                    ".join(
        "(" + ", ".join(
            (
                f"'{signature}'",
                f"'{function_name}'",
                f"'{result_type}'",
                str(len(argument_types)),
                f"'{', '.join(argument_types)}'",
                (
                    f"'{','.join(argument_names)}'"
                    if argument_names
                    else "NULL::text"
                ),
                f"'{fixed_hash if fixed else legacy_hash}'",
                str(trigger_count),
            )
        ) + ")"
        for (
            signature,
            function_name,
            result_type,
            argument_types,
            argument_names,
            legacy_hash,
            fixed_hash,
            trigger_count,
        ) in START_FUNCTION_CATALOG
    )
    trigger_values = ",\n                    ".join(
        f"('{table_name}', '{trigger_name}', {trigger_type})"
        for table_name, trigger_name, trigger_type in GUARD_TRIGGER_CATALOG
    )
    expected_fragment = FIXED_AUDIT_ORDER if fixed else LEGACY_AUDIT_ORDER
    forbidden_fragment = LEGACY_AUDIT_ORDER if fixed else FIXED_AUDIT_ORDER
    op.execute(
        f"""
DO $rsc_0055_start_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    guard_oid oid := pg_catalog.to_regprocedure('{GUARD_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
    validator_oid oid := pg_catalog.to_regprocedure('{VALIDATOR_SIGNATURE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL OR guard_oid IS NULL OR migrator_oid IS NULL
       OR validator_oid IS NULL THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: function identity mismatch';
    END IF;

    IF EXISTS (
        SELECT 1
          FROM (VALUES
                    {function_values}
               ) AS expected_function(
                   signature,
                   function_name,
                   result_type,
                   argument_count,
                   argument_types,
                   argument_names,
                   body_sha256,
                   trigger_count
               )
         WHERE NOT EXISTS (
             SELECT 1
               FROM pg_catalog.pg_proc AS function_row
               JOIN pg_catalog.pg_namespace AS namespace_row
                 ON namespace_row.oid = function_row.pronamespace
               JOIN pg_catalog.pg_language AS language_row
                 ON language_row.oid = function_row.prolang
              WHERE function_row.oid =
                    pg_catalog.to_regprocedure(expected_function.signature)
                AND namespace_row.nspname = 'public'
                AND function_row.proname = expected_function.function_name
                AND function_row.proowner = migrator_oid
                AND function_row.prokind = 'f'
                AND function_row.prorettype =
                    expected_function.result_type::pg_catalog.regtype
                AND NOT function_row.proretset
                AND function_row.pronargs = expected_function.argument_count
                AND pg_catalog.oidvectortypes(function_row.proargtypes) =
                    expected_function.argument_types
                AND pg_catalog.array_to_string(
                        function_row.proargnames,
                        ','
                    ) IS NOT DISTINCT FROM expected_function.argument_names
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
                    ) = expected_function.body_sha256
                AND (
                    pg_catalog.length(function_row.prosrc)
                    - pg_catalog.length(
                        pg_catalog.replace(
                            function_row.prosrc,
                            $rsc_0055_expected${expected_fragment}$rsc_0055_expected$,
                            ''
                        )
                    )
                ) / pg_catalog.length(
                    $rsc_0055_expected${expected_fragment}$rsc_0055_expected$
                ) = 1
                AND pg_catalog.strpos(
                    function_row.prosrc,
                    $rsc_0055_forbidden${forbidden_fragment}$rsc_0055_forbidden$
                ) = 0
                AND NOT pg_catalog.has_function_privilege(
                    api_oid, function_row.oid, 'EXECUTE'
                )
                AND NOT EXISTS (
                    SELECT 1
                      FROM pg_catalog.aclexplode(
                          COALESCE(
                              function_row.proacl,
                              pg_catalog.acldefault('f', function_row.proowner)
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
                              pg_catalog.acldefault('f', function_row.proowner)
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
                ) = expected_function.trigger_count
                AND (
                    SELECT pg_catalog.count(*)
                      FROM pg_catalog.pg_proc AS named_function
                     WHERE named_function.proname =
                           expected_function.function_name
                ) = 1
         )
    ) THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: function catalog mismatch';
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
                   AND namespace_row.nspname = 'public'
                   AND table_row.relname = expected_trigger.table_name
                   AND trigger_row.tgfoid = guard_oid
                   AND trigger_row.tgenabled = 'A'
                   AND trigger_row.tgtype = expected_trigger.trigger_type
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
                   AND trigger_row.tgname = expected_trigger.trigger_name) <> 1
    ) OR (SELECT pg_catalog.count(*)
            FROM pg_catalog.pg_trigger AS trigger_row
           WHERE NOT trigger_row.tgisinternal
             AND trigger_row.tgfoid = guard_oid) <>
             {len(GUARD_TRIGGER_CATALOG)}
       OR EXISTS (
           SELECT 1
             FROM pg_catalog.pg_trigger AS trigger_row
            WHERE NOT trigger_row.tgisinternal
              AND trigger_row.tgfoid = validator_oid
       ) THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: guard trigger mismatch';
    END IF;
END
$rsc_0055_start_catalog$
"""
    )


def _verify_dispatch_catalog(*, phase: str) -> None:
    escaped_phase = phase.replace("'", "''")
    trigger_values = ",\n                    ".join(
        f"('{table_name}', '{trigger_name}', {trigger_type})"
        for table_name, trigger_name, trigger_type in DISPATCH_TRIGGER_CATALOG
    )
    op.execute(
        f"""
DO $rsc_0055_dispatch_catalog$
DECLARE
    api_oid oid := pg_catalog.to_regrole('{PRODUCTION_API_ROLE}');
    dispatch_oid oid := pg_catalog.to_regprocedure('{DISPATCH_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}'
       OR api_oid IS NULL OR dispatch_oid IS NULL OR migrator_oid IS NULL
       OR (SELECT pg_catalog.count(*)
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.proname = '{DISPATCH_FUNCTION}') <> 1 THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: dispatcher identity mismatch';
    END IF;

    IF NOT EXISTS (
        SELECT 1
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS namespace_row
            ON namespace_row.oid = function_row.pronamespace
          JOIN pg_catalog.pg_language AS language_row
            ON language_row.oid = function_row.prolang
         WHERE function_row.oid = dispatch_oid
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
               ) = '{DISPATCH_BODY_SHA256}'
           AND (
               pg_catalog.length(function_row.prosrc)
               - pg_catalog.length(
                   pg_catalog.replace(
                       function_row.prosrc,
                       '{VALIDATOR_FUNCTION}',
                       ''
                   )
               )
           ) / pg_catalog.length('{VALIDATOR_FUNCTION}') = 3
    ) OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS caller_row
         WHERE pg_catalog.strpos(
                   caller_row.prosrc,
                   '{VALIDATOR_FUNCTION}'
               ) > 0
    ) <> 1 THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: dispatcher function mismatch';
    END IF;

    IF pg_catalog.has_function_privilege(api_oid, dispatch_oid, 'EXECUTE')
       OR EXISTS (
           SELECT 1
             FROM pg_catalog.pg_proc AS function_row
            CROSS JOIN LATERAL pg_catalog.aclexplode(
                COALESCE(
                    function_row.proacl,
                    pg_catalog.acldefault('f', function_row.proowner)
                )
            ) AS function_acl
            WHERE function_row.oid = dispatch_oid
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
            WHERE function_row.oid = dispatch_oid
              AND function_acl.privilege_type = 'EXECUTE'
              AND function_acl.grantee = migrator_oid
              AND function_acl.grantor = migrator_oid
              AND NOT function_acl.is_grantable
       ) <> 1 THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: dispatcher ACL mismatch';
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
                   AND namespace_row.nspname = 'public'
                   AND table_row.relname = expected_trigger.table_name
                   AND trigger_row.tgfoid = dispatch_oid
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
             AND trigger_row.tgfoid = dispatch_oid) <>
             {len(DISPATCH_TRIGGER_CATALOG)} THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: dispatcher trigger mismatch';
    END IF;
END
$rsc_0055_dispatch_catalog$
"""
    )


def _verify_runtime_ready_catalog(
    *, expected_body_sha256: str, phase: str
) -> None:
    if expected_body_sha256 not in {
        RUNTIME_READY_BODY_SHA256_0054,
        RUNTIME_READY_BODY_SHA256_0055,
    }:
        raise ValueError("unsupported 0055 readiness body hash")
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0055_readiness_catalog$
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
$rsc_0055_readiness_catalog$
"""
    )


def _validate_existing_start_graphs(*, phase: str) -> None:
    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0055_existing_graphs$
DECLARE
    completion_row record;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '{MIGRATION_ERROR}: {escaped_phase}: migration role mismatch';
    END IF;
    FOR completion_row IN
        SELECT completion.task_id
          FROM public.stocktake_start_completions AS completion
         ORDER BY completion.task_id
    LOOP
        PERFORM public.{VALIDATOR_FUNCTION}(completion_row.task_id);
    END LOOP;
END
$rsc_0055_existing_graphs$
"""
    )


def _legacy_start_exists_sql() -> str:
    return f"""
EXISTS (
    SELECT 1
      FROM public.stocktake_tasks AS task
     WHERE task.task_type IN {NONOPENING_SQL}
       AND (
           task.status <> 'draft'
           OR task.current_round_no <> 0
           OR task.cutoff_ledger_cursor IS NOT NULL
           OR task.cutoff_at IS NOT NULL
           OR task.snapshot_manifest_sha256 IS NOT NULL
           OR task.issued_at IS NOT NULL
           OR task.frozen_at IS NOT NULL
           OR EXISTS (
               SELECT 1 FROM public.inventory_freezes AS freeze_row
                WHERE freeze_row.task_id = task.id
           )
           OR EXISTS (
               SELECT 1 FROM public.stocktake_snapshot_lines AS snapshot
                WHERE snapshot.task_id = task.id
           )
           OR EXISTS (
               SELECT 1 FROM public.stocktake_rounds AS round_row
                WHERE round_row.task_id = task.id
           )
           OR EXISTS (
               SELECT 1 FROM public.state_transition_events AS event
                WHERE event.aggregate_type = 'stocktake_task'
                  AND lower(replace(event.aggregate_id, '-', '')) =
                      lower(replace(CAST(task.id AS text), '-', ''))
                  AND (
                      event.reason IN (
                          'stocktake_task_issued',
                          'stocktake_task_frozen',
                          'stocktake_initial_round_started'
                      )
                      OR event.idempotency_key LIKE
                         ('stocktake' || ':' || 'start' || ':' || '%')
                      OR (
                          event.metadata_jsonb->>'schema' = '{COMMAND_SCHEMA}'
                          AND event.metadata_jsonb->>'operation' = 'start'
                      )
                  )
           )
           OR EXISTS (
               SELECT 1 FROM public.audit_events AS audit
                WHERE audit.aggregate_type = 'stocktake_task'
                  AND lower(replace(audit.aggregate_id, '-', '')) =
                      lower(replace(CAST(task.id AS text), '-', ''))
                  AND audit.action = 'stocktake.task.started'
           )
       )
)
""".strip()


def _require_no_start_graph() -> None:
    op.execute(
        f"""
DO $rsc_0055_downgrade$
BEGIN
    IF EXISTS (
        SELECT 1 FROM public.stocktake_start_completions
    ) OR {_legacy_start_exists_sql()} THEN
        RAISE EXCEPTION '{DOWNGRADE_BLOCKER}';
    END IF;
END
$rsc_0055_downgrade$
"""
    )
