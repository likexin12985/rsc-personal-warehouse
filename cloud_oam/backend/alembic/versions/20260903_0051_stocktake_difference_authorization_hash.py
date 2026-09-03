"""Repair stocktake difference authorization-hash validation.

Revision ID: 20260903_0051
Revises: 20260903_0050
Create Date: 2026-09-03

Revision 0031 emitted the intended ``{64}`` regular-expression quantifier from
an f-string without escaping its braces.  PostgreSQL therefore stored the
validator as ``^[0-9a-f]64$`` and rejected canonical 64-character
authorization hashes.  Its trigger was also left origin-only, so it did not
enforce the invariant when replication-role execution was enabled.

This narrow forward repair locks the sole trigger table and fail-closed
verifies the exact function identity, shape, owner, ACL, SECURITY INVOKER
mode, search path, body hash and unique live trigger binding.  It replaces
only the malformed regular-expression fragment and restores the validator
trigger to ENABLE ALWAYS.  Downgrade performs the exact inverse replacement
and restores the legacy origin-only trigger state.  No business row or
privilege is changed.

SQLite is an explicit schema no-op for local migration-chain compatibility;
it is not PostgreSQL security evidence.
"""

from __future__ import annotations

from typing import Sequence, Union

from alembic import op


revision: str = "20260903_0051"
down_revision: Union[str, Sequence[str], None] = "20260903_0050"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


PRODUCTION_API_ROLE = "star_oam_api"
MIGRATION_ROLE = "star_oam_migrator"
OAM_RUNTIME_READY_FUNCTION = "rsc_oam_runtime_binding_ready_0044"
PREVIOUS_SCHEMA_REVISION = "20260903_0050"

FUNCTION_NAME = "rsc_validate_stocktake_difference_set_completion_0031"
FUNCTION_SIGNATURE = f"public.{FUNCTION_NAME}()"
TRIGGER_TABLE = "stocktake_difference_set_completions"
TRIGGER_NAME = "trg_stocktake_difference_set_completions_validate_0031"
FIXED_SEARCH_PATH = "search_path=pg_catalog, public"

LEGACY_BODY_SHA256 = (
    "6eca64aa504472b4b3ce8e164c3d314e1092e5319797bcf9ea2ad415b51c8ff0"
)
FIXED_BODY_SHA256 = (
    "ead5a0a72c25cd326a1d036dddd384bb583b66141512f568b8929ab31b4773a9"
)
LEGACY_SOURCE_FRAGMENT = (
    "       OR NEW.authorization_sha256 !~ '^[0-9a-f]64$'"
)
FIXED_SOURCE_FRAGMENT = (
    "       OR NEW.authorization_sha256 !~ '^[0-9a-f]{64}$'"
)

LEGACY_TRIGGER_ENABLED = "O"
FIXED_TRIGGER_ENABLED = "A"

CATALOG_ERROR = (
    "0051 stocktake difference authorization-hash catalog verification failed"
)
REPLACEMENT_ERROR = (
    "0051 stocktake difference authorization-hash replacement failed"
)
EXISTING_ROWS_ERROR = (
    "0051 existing stocktake difference completions are not canonical"
)

NONOPENING_AUTHORIZATION_SCHEMA = (
    "cloud_oam.stocktake.difference_authorization.v1"
)
NONOPENING_AUTHORIZATION_CANONICAL_KEYS = (
    "assignment_id",
    "authorization_version",
    "completed_at",
    "person_id",
    "role_code",
    "schema",
    "scope_id",
    "scope_type",
    "user_id",
)
_NONOPENING_AUTHORIZATION_SHA256_SQL = """
pg_catalog.encode(
    pg_catalog.sha256(
        pg_catalog.convert_to(
            '{"assignment_id":' ||
            pg_catalog.to_json(
                completion.completed_role_assignment_id::text
            )::text ||
            ',"authorization_version":' ||
            completion.authorization_version::text ||
            ',"completed_at":' ||
            pg_catalog.to_json(
                pg_catalog.to_char(
                    pg_catalog.timezone('UTC', completion.completed_at),
                    'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'
                )
            )::text ||
            ',"person_id":' ||
            pg_catalog.to_json(
                completion.completed_by_person_id::text
            )::text ||
            ',"role_code":' ||
            pg_catalog.to_json(completion.role_code::text)::text ||
            ',"schema":"cloud_oam.stocktake.difference_authorization.v1"' ||
            ',"scope_id":' ||
            pg_catalog.to_json(completion.scope_id_snapshot::text)::text ||
            ',"scope_type":' ||
            pg_catalog.to_json(completion.scope_type::text)::text ||
            ',"user_id":' ||
            pg_catalog.to_json(
                completion.completed_by_user_id::text
            )::text ||
            '}',
            'UTF8'
        )
    ),
    'hex'
)
""".strip()


def _dialect_name() -> str:
    dialect = op.get_context().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0051 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_trigger_table()
    _verify_catalog(
        expected_body_sha256=LEGACY_BODY_SHA256,
        expected_source_fragment=LEGACY_SOURCE_FRAGMENT,
        forbidden_source_fragment=FIXED_SOURCE_FRAGMENT,
        expected_trigger_enabled=LEGACY_TRIGGER_ENABLED,
        phase="legacy upgrade preflight",
    )
    _verify_existing_rows(phase="upgrade preflight")
    _replace_body_fragment(
        expected_body_sha256=LEGACY_BODY_SHA256,
        source_fragment=LEGACY_SOURCE_FRAGMENT,
        replacement_fragment=FIXED_SOURCE_FRAGMENT,
        phase="upgrade",
    )
    _set_trigger_always(always=True)
    _verify_catalog(
        expected_body_sha256=FIXED_BODY_SHA256,
        expected_source_fragment=FIXED_SOURCE_FRAGMENT,
        forbidden_source_fragment=LEGACY_SOURCE_FRAGMENT,
        expected_trigger_enabled=FIXED_TRIGGER_ENABLED,
        phase="fixed upgrade postflight",
    )
    _replace_oam_runtime_ready_function(revision)


def downgrade() -> None:
    if _dialect_name() == "sqlite":
        return

    _lock_trigger_table()
    _verify_catalog(
        expected_body_sha256=FIXED_BODY_SHA256,
        expected_source_fragment=FIXED_SOURCE_FRAGMENT,
        forbidden_source_fragment=LEGACY_SOURCE_FRAGMENT,
        expected_trigger_enabled=FIXED_TRIGGER_ENABLED,
        phase="fixed downgrade preflight",
    )
    _verify_existing_rows(phase="downgrade preflight")
    _replace_body_fragment(
        expected_body_sha256=FIXED_BODY_SHA256,
        source_fragment=FIXED_SOURCE_FRAGMENT,
        replacement_fragment=LEGACY_SOURCE_FRAGMENT,
        phase="downgrade",
    )
    _set_trigger_always(always=False)
    _verify_catalog(
        expected_body_sha256=LEGACY_BODY_SHA256,
        expected_source_fragment=LEGACY_SOURCE_FRAGMENT,
        forbidden_source_fragment=FIXED_SOURCE_FRAGMENT,
        expected_trigger_enabled=LEGACY_TRIGGER_ENABLED,
        phase="legacy downgrade postflight",
    )
    _replace_oam_runtime_ready_function(PREVIOUS_SCHEMA_REVISION)


def _lock_trigger_table() -> None:
    op.execute(
        f"LOCK TABLE public.{TRIGGER_TABLE} IN ACCESS EXCLUSIVE MODE"
    )


def _verify_existing_rows(*, phase: str) -> None:
    """Reject rows that violate stable historical invariants; never rewrite."""

    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0051_existing_rows$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM public.stocktake_difference_set_completions AS completion
          LEFT JOIN public.stocktake_tasks AS task
            ON task.id = completion.task_id
          LEFT JOIN public.stocktake_rounds AS round_row
            ON round_row.id = completion.round_id
           AND round_row.task_id = completion.task_id
          LEFT JOIN public.stocktake_round_submissions AS submission
            ON submission.id = completion.round_submission_id
           AND submission.task_id = completion.task_id
           AND submission.round_id = completion.round_id
          LEFT JOIN LATERAL (
              SELECT pg_catalog.count(*)::integer AS difference_count,
                     pg_catalog.count(*) FILTER (
                         WHERE difference.difference_type <>
                               'control_unassigned'
                     )::integer AS physical_difference_count,
                     pg_catalog.count(*) FILTER (
                         WHERE difference.difference_type =
                               'control_unassigned'
                     )::integer AS control_difference_count,
                     pg_catalog.count(*) FILTER (
                         WHERE difference.observed_line_id IS NOT NULL
                           AND EXISTS (
                               SELECT 1
                                 FROM public.stocktake_count_observations
                                      AS observation
                                WHERE observation.id =
                                      difference.observed_line_id
                                  AND observation.verification_status =
                                      'pending_verification'
                           )
                     )::integer AS pending_observation_difference_count,
                     COALESCE(
                         pg_catalog.sum(difference.affected_qty),
                         0
                     )::numeric(18, 3) AS total_affected_qty,
                     pg_catalog.min(difference.difference_no) AS min_number,
                     pg_catalog.max(difference.difference_no) AS max_number
                FROM public.stocktake_differences AS difference
               WHERE difference.task_id = completion.task_id
                 AND difference.round_id = completion.round_id
          ) AS actual ON true
         WHERE task.id IS NULL
            OR round_row.id IS NULL
            OR submission.id IS NULL
            OR task.task_type NOT IN (
                'opening',
                'full',
                'sample',
                'ad_hoc',
                'personal',
                'termination'
            )
            OR completion.authorization_sha256 !~ '^[0-9a-f]{{64}}$'
            OR completion.difference_count IS DISTINCT FROM
               actual.difference_count
            OR completion.physical_difference_count IS DISTINCT FROM
               actual.physical_difference_count
            OR completion.control_difference_count IS DISTINCT FROM
               actual.control_difference_count
            OR completion.pending_observation_difference_count IS DISTINCT FROM
               actual.pending_observation_difference_count
            OR completion.total_affected_qty IS DISTINCT FROM
               actual.total_affected_qty
            OR (
                actual.difference_count > 0
                AND (
                    actual.min_number IS DISTINCT FROM 1
                    OR actual.max_number IS DISTINCT FROM
                       actual.difference_count
                )
            )
            OR (
                task.task_type = 'opening'
                AND NOT EXISTS (
                    SELECT 1
                      FROM public.stocktake_scope_count_completions AS sealing
                     WHERE sealing.id = submission.sealing_completion_id
                       AND submission.submitted_by_user_id =
                           completion.completed_by_user_id
                       AND submission.submitted_by_person_id =
                           completion.completed_by_person_id
                       AND submission.submitted_role_assignment_id =
                           completion.completed_role_assignment_id
                       AND submission.authorization_version =
                           completion.authorization_version
                       AND submission.submitted_at = completion.completed_at
                       AND sealing.task_id = completion.task_id
                       AND sealing.round_id = completion.round_id
                       AND sealing.completed_by_user_id =
                           completion.completed_by_user_id
                       AND sealing.completed_by_person_id =
                           completion.completed_by_person_id
                       AND sealing.completed_role_assignment_id =
                           completion.completed_role_assignment_id
                       AND sealing.authorization_version =
                           completion.authorization_version
                       AND sealing.completed_at = completion.completed_at
                       AND sealing.role_code = completion.role_code
                       AND sealing.scope_type = completion.scope_type
                       AND sealing.scope_id_snapshot =
                           completion.scope_id_snapshot
                       AND sealing.authorization_sha256 =
                           completion.authorization_sha256
                )
            )
            OR (
                task.task_type IN (
                    'full', 'sample', 'ad_hoc', 'personal', 'termination'
                )
                AND (
                    completion.completed_at < submission.submitted_at
                    OR completion.control_difference_count <> 0
                    OR EXISTS (
                        SELECT 1
                          FROM public.stocktake_differences AS difference
                         WHERE difference.task_id = completion.task_id
                           AND difference.round_id = completion.round_id
                           AND difference.difference_type =
                               'control_unassigned'
                    )
                    OR NOT EXISTS (
                        SELECT 1
                          FROM public.users AS app_user
                          JOIN public.people AS person
                            ON person.id = app_user.person_id
                          JOIN public.role_assignments AS assignment
                            ON assignment.id =
                               completion.completed_role_assignment_id
                           AND assignment.user_id = app_user.id
                          JOIN public.roles AS role
                            ON role.id = assignment.role_id
                         WHERE app_user.id =
                               completion.completed_by_user_id
                           AND person.id =
                               completion.completed_by_person_id
                           AND role.code = completion.role_code
                           AND NOT role.is_external
                           AND assignment.scope_type =
                               completion.scope_type
                           AND assignment.scope_id =
                               completion.scope_id_snapshot
                           AND assignment.valid_from <=
                               completion.completed_at
                           AND (
                               assignment.valid_to IS NULL
                               OR completion.completed_at <
                                  assignment.valid_to
                           )
                           AND (
                               assignment.revoked_at IS NULL
                               OR completion.completed_at <
                                  assignment.revoked_at
                           )
                           AND (
                               (
                                   role.code = 'admin'
                                   AND assignment.scope_type = 'national'
                                   AND assignment.scope_id = '*'
                               )
                               OR (
                                   role.code = 'provincial_manager'
                                   AND assignment.scope_type = 'organization'
                                   AND assignment.scope_id =
                                       task.region_org_id::text
                               )
                           )
                    )
                    OR completion.authorization_sha256 IS DISTINCT FROM
                       ({_NONOPENING_AUTHORIZATION_SHA256_SQL})
                )
            )
    ) THEN
        RAISE EXCEPTION
            '{EXISTING_ROWS_ERROR}: {escaped_phase}';
    END IF;
END
$rsc_0051_existing_rows$
"""
    )


def _verify_catalog(
    *,
    expected_body_sha256: str,
    expected_source_fragment: str,
    forbidden_source_fragment: str,
    expected_trigger_enabled: str,
    phase: str,
) -> None:
    expected_states = {
        (
            LEGACY_BODY_SHA256,
            LEGACY_SOURCE_FRAGMENT,
            FIXED_SOURCE_FRAGMENT,
            LEGACY_TRIGGER_ENABLED,
        ),
        (
            FIXED_BODY_SHA256,
            FIXED_SOURCE_FRAGMENT,
            LEGACY_SOURCE_FRAGMENT,
            FIXED_TRIGGER_ENABLED,
        ),
    }
    if (
        expected_body_sha256,
        expected_source_fragment,
        forbidden_source_fragment,
        expected_trigger_enabled,
    ) not in expected_states:
        raise ValueError("unsupported stocktake difference catalog state")

    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0051_catalog$
DECLARE
    api_oid oid;
    migrator_oid oid;
    function_oid oid;
    function_source text;
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

    function_oid := pg_catalog.to_regprocedure('{FUNCTION_SIGNATURE}');
    IF function_oid IS NULL OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_proc AS function_row
          JOIN pg_catalog.pg_namespace AS schema_row
            ON schema_row.oid = function_row.pronamespace
         WHERE schema_row.nspname = 'public'
           AND function_row.proname = '{FUNCTION_NAME}'
    ) <> 1 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: function identity mismatch';
    END IF;

    SELECT function_row.prosrc
      INTO function_source
      FROM pg_catalog.pg_proc AS function_row
      JOIN pg_catalog.pg_language AS language_row
        ON language_row.oid = function_row.prolang
     WHERE function_row.oid = function_oid
       AND function_row.proowner = migrator_oid
       AND function_row.prokind = 'f'
       AND function_row.prorettype = pg_catalog.to_regtype('trigger')
       AND NOT function_row.proretset
       AND function_row.pronargs = 0
       AND function_row.proargtypes = ''::pg_catalog.oidvector
       AND function_row.proallargtypes IS NULL
       AND function_row.proargnames IS NULL
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
           ) = '{expected_body_sha256}';
    IF function_source IS NULL THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: function definition mismatch';
    END IF;

    IF (
        pg_catalog.length(function_source)
        - pg_catalog.length(
            pg_catalog.replace(
                function_source,
                $rsc_0051_expected${expected_source_fragment}$rsc_0051_expected$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0051_expected${expected_source_fragment}$rsc_0051_expected$
    ) <> 1 OR pg_catalog.strpos(
        function_source,
        $rsc_0051_forbidden${forbidden_source_fragment}$rsc_0051_forbidden$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: source fragment mismatch';
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

    IF (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgname = '{TRIGGER_NAME}'
           AND trigger_row.tgrelid =
               pg_catalog.to_regclass('public.{TRIGGER_TABLE}')
           AND trigger_row.tgfoid = function_oid
           AND trigger_row.tgenabled = '{expected_trigger_enabled}'
           AND trigger_row.tgtype = 7
           AND trigger_row.tgconstraint = 0
           AND NOT trigger_row.tgdeferrable
           AND NOT trigger_row.tginitdeferred
           AND trigger_row.tgqual IS NULL
           AND trigger_row.tgnargs = 0
           AND trigger_row.tgattr = ''::pg_catalog.int2vector
    ) <> 1 OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgname = '{TRIGGER_NAME}'
    ) <> 1 OR (
        SELECT pg_catalog.count(*)
          FROM pg_catalog.pg_trigger AS trigger_row
         WHERE NOT trigger_row.tgisinternal
           AND trigger_row.tgfoid = function_oid
    ) <> 1 THEN
        RAISE EXCEPTION
            '{CATALOG_ERROR}: {escaped_phase}: trigger binding mismatch';
    END IF;
END
$rsc_0051_catalog$
"""
    )


def _replace_body_fragment(
    *,
    expected_body_sha256: str,
    source_fragment: str,
    replacement_fragment: str,
    phase: str,
) -> None:
    expected_pairs = {
        (
            LEGACY_BODY_SHA256,
            LEGACY_SOURCE_FRAGMENT,
            FIXED_SOURCE_FRAGMENT,
        ),
        (
            FIXED_BODY_SHA256,
            FIXED_SOURCE_FRAGMENT,
            LEGACY_SOURCE_FRAGMENT,
        ),
    }
    if (
        expected_body_sha256,
        source_fragment,
        replacement_fragment,
    ) not in expected_pairs:
        raise ValueError("unsupported stocktake difference body replacement")

    escaped_phase = phase.replace("'", "''")
    op.execute(
        f"""
DO $rsc_0051_replace$
DECLARE
    function_oid oid;
    function_source text;
    function_definition text;
BEGIN
    IF current_user <> '{MIGRATION_ROLE}'
       OR session_user <> '{MIGRATION_ROLE}' THEN
        RAISE EXCEPTION
            '{REPLACEMENT_ERROR}: {escaped_phase}: migration role mismatch';
    END IF;

    function_oid := pg_catalog.to_regprocedure('{FUNCTION_SIGNATURE}');
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
                $rsc_0051_source${source_fragment}$rsc_0051_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0051_source${source_fragment}$rsc_0051_source$
    ) <> 1 OR pg_catalog.strpos(
        function_source,
        $rsc_0051_replacement${replacement_fragment}$rsc_0051_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{REPLACEMENT_ERROR}: {escaped_phase}: source mismatch';
    END IF;

    function_definition := pg_catalog.pg_get_functiondef(function_oid);
    IF (
        pg_catalog.length(function_definition)
        - pg_catalog.length(
            pg_catalog.replace(
                function_definition,
                $rsc_0051_source${source_fragment}$rsc_0051_source$,
                ''
            )
        )
    ) / pg_catalog.length(
        $rsc_0051_source${source_fragment}$rsc_0051_source$
    ) <> 1 OR pg_catalog.strpos(
        function_definition,
        $rsc_0051_replacement${replacement_fragment}$rsc_0051_replacement$
    ) <> 0 THEN
        RAISE EXCEPTION
            '{REPLACEMENT_ERROR}: {escaped_phase}: definition mismatch';
    END IF;

    EXECUTE pg_catalog.replace(
        function_definition,
        $rsc_0051_source${source_fragment}$rsc_0051_source$,
        $rsc_0051_replacement${replacement_fragment}$rsc_0051_replacement$
    );
END
$rsc_0051_replace$
"""
    )


def _set_trigger_always(*, always: bool) -> None:
    enable_mode = "ENABLE ALWAYS" if always else "ENABLE"
    op.execute(
        f"ALTER TABLE public.{TRIGGER_TABLE} {enable_mode} "
        f"TRIGGER {TRIGGER_NAME}"
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
