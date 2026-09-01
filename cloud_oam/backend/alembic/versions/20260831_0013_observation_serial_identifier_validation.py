"""Align resolved stocktake serial evidence with its identifier type.

Revision ID: 20260831_0013
Revises: 20260831_0012
Create Date: 2026-08-31

Revision 0011 validated every resolved observation against ``serial_no`` even
when the immutable input type said that the physical value was a QR code.  This
revision replaces only that observation-insert guard.  It never resolves a raw
identifier, rewrites an observation, creates master data, or creates inventory.
"""

from typing import Sequence, Union

from alembic import context, op


revision: str = "20260831_0013"
down_revision: Union[str, Sequence[str], None] = "20260831_0012"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


TABLE_NAME = "stocktake_count_observations"
UPGRADE_BLOCKER = (
    "0013 preflight failed: existing resolved stocktake serial observations "
    "violate identifier mapping rules"
)
DOWNGRADE_BLOCKER = (
    "cannot downgrade 0013: resolved stocktake serial observations require "
    "identifier-aware mapping guards"
)


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError(
            "0013 supports only PostgreSQL production and SQLite local test schemas"
        )
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "sqlite":
        if context.is_offline_mode():
            raise RuntimeError("0013 SQLite upgrade requires an online connection")
        _ensure_sqlite_migration_transaction()
    _assert_existing_resolved_observations_are_compatible(dialect)
    _replace_observation_insert_guard(
        dialect,
        old_revision="0011",
        new_revision="0013",
        identifier_aware=True,
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0013 downgrade requires an online connection for fail-closed "
            "resolved-serial evidence checks"
        )

    dialect = _dialect_name()
    bind = op.get_bind()
    if dialect == "sqlite":
        _ensure_sqlite_migration_transaction()
    else:
        bind.exec_driver_sql(
            "LOCK TABLE stocktake_count_observations IN ACCESS EXCLUSIVE MODE"
        )
    has_resolved_observation = bind.exec_driver_sql(
        "SELECT 1 FROM stocktake_count_observations "
        "WHERE serial_id IS NOT NULL LIMIT 1"
    ).first()
    if has_resolved_observation is not None:
        raise RuntimeError(DOWNGRADE_BLOCKER)

    _replace_observation_insert_guard(
        dialect,
        old_revision="0013",
        new_revision="0011",
        identifier_aware=False,
    )


def _ensure_sqlite_migration_transaction() -> None:
    """Keep each SQLite preflight and trigger replacement indivisible."""

    bind = op.get_bind()
    driver_connection = bind.connection.driver_connection
    if not driver_connection.in_transaction:
        bind.exec_driver_sql("BEGIN IMMEDIATE")


def _assert_existing_resolved_observations_are_compatible(dialect: str) -> None:
    predicate = _invalid_resolved_observation_predicate(dialect)
    if context.is_offline_mode():
        if dialect != "postgresql":
            raise RuntimeError("0013 SQLite upgrade requires an online connection")
        op.execute(
            "LOCK TABLE stocktake_count_observations "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
        op.execute(
            f"""
DO $$
BEGIN
    IF EXISTS (
        SELECT 1
          FROM stocktake_count_observations AS observation
         WHERE observation.serial_id IS NOT NULL
           AND ({predicate})
    ) THEN
        RAISE EXCEPTION
            '0013 preflight failed: existing resolved stocktake serial observations violate identifier mapping rules';
    END IF;
END $$
"""
        )
        return

    bind = op.get_bind()
    if dialect == "postgresql":
        # Close the preflight/trigger-replacement race for production writers.
        bind.exec_driver_sql(
            "LOCK TABLE stocktake_count_observations "
            "IN SHARE ROW EXCLUSIVE MODE"
        )
    invalid = bind.exec_driver_sql(
        "SELECT 1 FROM stocktake_count_observations AS observation "
        "WHERE observation.serial_id IS NOT NULL "
        f"AND ({predicate}) LIMIT 1"
    ).first()
    if invalid is not None:
        raise RuntimeError(UPGRADE_BLOCKER)


def _invalid_resolved_observation_predicate(dialect: str) -> str:
    lot_equals = (
        "serial.lot_id IS NOT DISTINCT FROM observation.lot_id"
        if dialect == "postgresql"
        else "serial.lot_id IS observation.lot_id"
    )
    return f"""
observation.serial_identifier_type IS NULL
OR observation.serial_identifier_type = 'unknown'
OR (
    observation.serial_identifier_type = 'serial_no'
    AND NOT EXISTS (
        SELECT 1 FROM inventory_serials AS serial
         WHERE serial.id = observation.serial_id
           AND serial.material_id = observation.material_id
           AND serial.serial_no = observation.serial_no_raw
           AND {lot_equals}
    )
)
OR (
    observation.serial_identifier_type = 'qr_code'
    AND (
        NOT EXISTS (
            SELECT 1 FROM inventory_serials AS serial
             WHERE serial.id = observation.serial_id
               AND serial.material_id = observation.material_id
               AND serial.qr_code = observation.serial_no_raw
               AND {lot_equals}
        )
        OR (
            SELECT count(*) FROM qr_codes AS mapping
             WHERE mapping.code = observation.serial_no_raw
               AND mapping.object_type = 'serial'
               AND mapping.object_id = observation.serial_id
               AND mapping.status = 'active'
        ) <> 1
    )
)
OR observation.serial_identifier_type NOT IN ('serial_no', 'qr_code', 'unknown')
"""


def _replace_observation_insert_guard(
    dialect: str,
    *,
    old_revision: str,
    new_revision: str,
    identifier_aware: bool,
) -> None:
    if dialect == "postgresql":
        op.execute(
            "DROP TRIGGER "
            f"trg_stocktake_count_observations_validate_insert_{old_revision} "
            "ON stocktake_count_observations"
        )
        op.execute(
            "DROP FUNCTION "
            f"rsc_validate_stocktake_observation_insert_{old_revision}()"
        )
        op.execute(
            _postgresql_observation_function_sql(
                new_revision, identifier_aware=identifier_aware
            )
        )
        op.execute(
            "CREATE TRIGGER "
            f"trg_stocktake_count_observations_validate_insert_{new_revision} "
            "BEFORE INSERT ON stocktake_count_observations FOR EACH ROW "
            "EXECUTE FUNCTION "
            f"rsc_validate_stocktake_observation_insert_{new_revision}()"
        )
        return

    # Both operations run under BEGIN IMMEDIATE.  Removing both canonical
    # names first safely resumes an old half-replacement whose Alembic version
    # was not advanced, while the preflight above proves the target rule can
    # be installed without accepting ambiguous evidence.
    op.execute(
        "DROP TRIGGER IF EXISTS "
        f"trg_stocktake_count_observations_validate_insert_{old_revision}"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS "
        f"trg_stocktake_count_observations_validate_insert_{new_revision}"
    )
    op.execute(
        _sqlite_observation_trigger_sql(
            new_revision, identifier_aware=identifier_aware
        )
    )


def _postgresql_observation_function_sql(
    revision_suffix: str, *, identifier_aware: bool
) -> str:
    serial_validation = (
        _postgresql_identifier_aware_serial_validation()
        if identifier_aware
        else _postgresql_legacy_serial_validation()
    )
    return f"""
CREATE FUNCTION rsc_validate_stocktake_observation_insert_{revision_suffix}()
RETURNS trigger
LANGUAGE plpgsql
AS $$
DECLARE
    parent_status text;
    parent_started_at timestamptz;
    parent_cutoff_at timestamptz;
    scope_owner uuid;
    scope_location uuid;
    scope_custodian uuid;
    scope_assignee text;
    scope_mode text;
    scope_material uuid;
    scope_condition text;
    scope_availability text;
    policy_count integer;
    policy_tracking text;
    policy_scale integer;
BEGIN
    SELECT round_row.status, round_row.started_at, task.cutoff_at
      INTO parent_status, parent_started_at, parent_cutoff_at
      FROM stocktake_rounds AS round_row
      JOIN stocktake_tasks AS task ON task.id = round_row.task_id
     WHERE round_row.id = NEW.round_id
       AND round_row.task_id = NEW.task_id
     FOR UPDATE OF round_row;
    IF parent_status IS DISTINCT FROM 'counting'
       OR parent_cutoff_at IS NULL
       OR NEW.counted_at < parent_started_at
       OR EXISTS (
           SELECT 1 FROM stocktake_round_submissions
            WHERE task_id = NEW.task_id AND round_id = NEW.round_id
       )
       OR EXISTS (
           SELECT 1 FROM stocktake_scope_count_completions
            WHERE task_id = NEW.task_id AND round_id = NEW.round_id
              AND scope_id = NEW.scope_id
       ) THEN
        RAISE EXCEPTION 'stocktake observation scope is already sealed';
    END IF;

    SELECT owner_org_id, location_id, custodian_person_id_snapshot,
           assignee_user_id, scope_mode, material_id, condition_code,
           availability_bucket
      INTO scope_owner, scope_location, scope_custodian, scope_assignee,
           scope_mode, scope_material, scope_condition, scope_availability
      FROM stocktake_scopes
     WHERE id = NEW.scope_id AND task_id = NEW.task_id;
    IF scope_owner IS NULL
       OR NEW.owner_org_id IS DISTINCT FROM scope_owner
       OR NEW.location_id IS DISTINCT FROM scope_location
       OR NEW.custodian_person_id_snapshot IS DISTINCT FROM scope_custodian
       OR NEW.counted_by_user_id IS DISTINCT FROM scope_assignee
       OR (scope_mode = 'filtered' AND (
           (scope_material IS NOT NULL AND NEW.material_id IS DISTINCT FROM scope_material)
           OR (scope_condition IS NOT NULL AND NEW.condition_code IS DISTINCT FROM scope_condition)
           OR (scope_availability IS NOT NULL AND NEW.availability_bucket IS DISTINCT FROM scope_availability)
       )) THEN
        RAISE EXCEPTION 'stocktake observation is outside its frozen scope';
    END IF;
    IF NEW.material_id IS NOT NULL THEN
        SELECT count(*), min(tracking_mode), min(quantity_scale)
          INTO policy_count, policy_tracking, policy_scale
          FROM material_inventory_policies
         WHERE material_id = NEW.material_id
           AND effective_from <= parent_cutoff_at
           AND (effective_to IS NULL OR parent_cutoff_at < effective_to);
        IF policy_count <> 1
           OR NEW.counted_qty <> round(NEW.counted_qty, policy_scale)
           OR (policy_tracking = 'none' AND
               (NEW.lot_no_raw IS NOT NULL OR NEW.serial_no_raw IS NOT NULL))
           OR (policy_tracking = 'lot' AND
               (NEW.lot_no_raw IS NULL OR NEW.serial_no_raw IS NOT NULL))
           OR (policy_tracking = 'serial' AND
               (NEW.lot_no_raw IS NOT NULL OR NEW.serial_no_raw IS NULL))
           OR (policy_tracking = 'lot_and_serial' AND
               (NEW.lot_no_raw IS NULL OR NEW.serial_no_raw IS NULL)) THEN
            RAISE EXCEPTION 'stocktake observation violates cutoff tracking policy';
        END IF;
    END IF;
    IF NEW.lot_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM inventory_lots
         WHERE id = NEW.lot_id AND material_id = NEW.material_id
           AND lot_no = NEW.lot_no_raw
    ) THEN
        RAISE EXCEPTION 'stocktake observation lot mapping is inconsistent';
    END IF;
{serial_validation}

    -- Pending raw identifiers remain evidence; only a verified exact cutoff
    -- dimension is forced onto the existing snapshot account count line.
    IF NEW.verification_status = 'verified' AND (
        EXISTS (
            SELECT 1 FROM stock_accounts AS account
             WHERE account.owner_org_id = NEW.owner_org_id
               AND account.location_id = NEW.location_id
               AND account.custodian_person_id IS NOT DISTINCT FROM
                   NEW.custodian_person_id_snapshot
               AND account.material_id = NEW.material_id
               AND account.condition_code = NEW.condition_code
               AND account.availability_bucket = NEW.availability_bucket
               AND account.lot_id IS NOT DISTINCT FROM NEW.lot_id
               AND account.created_at <= parent_cutoff_at
        ) OR EXISTS (
            SELECT 1
              FROM stocktake_snapshot_lines AS snapshot
              JOIN stock_accounts AS account ON account.id = snapshot.stock_account_id
             WHERE snapshot.task_id = NEW.task_id
               AND snapshot.scope_id = NEW.scope_id
               AND account.owner_org_id = NEW.owner_org_id
               AND account.location_id = NEW.location_id
               AND account.custodian_person_id IS NOT DISTINCT FROM
                   NEW.custodian_person_id_snapshot
               AND account.material_id = NEW.material_id
               AND account.condition_code = NEW.condition_code
               AND account.availability_bucket = NEW.availability_bucket
               AND account.lot_id IS NOT DISTINCT FROM NEW.lot_id
        )
    ) THEN
        RAISE EXCEPTION 'verified observation must use the cutoff account count line';
    END IF;
    RETURN NEW;
END;
$$
"""


def _postgresql_legacy_serial_validation() -> str:
    return """    IF NEW.serial_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM inventory_serials
         WHERE id = NEW.serial_id AND material_id = NEW.material_id
           AND serial_no = NEW.serial_no_raw
           AND lot_id IS NOT DISTINCT FROM NEW.lot_id
    ) THEN
        RAISE EXCEPTION 'stocktake observation serial mapping is inconsistent';
    END IF;"""


def _postgresql_identifier_aware_serial_validation() -> str:
    return """    IF NEW.serial_id IS NOT NULL THEN
        IF NEW.serial_identifier_type IS NULL
           OR NEW.serial_identifier_type = 'unknown' THEN
            RAISE EXCEPTION 'stocktake observation serial identifier mapping is inconsistent';
        ELSIF NEW.serial_identifier_type = 'serial_no' THEN
            IF NOT EXISTS (
                SELECT 1 FROM inventory_serials AS serial
                 WHERE serial.id = NEW.serial_id
                   AND serial.material_id = NEW.material_id
                   AND serial.serial_no = NEW.serial_no_raw
                   AND serial.lot_id IS NOT DISTINCT FROM NEW.lot_id
            ) THEN
                RAISE EXCEPTION 'stocktake observation serial identifier mapping is inconsistent';
            END IF;
        ELSIF NEW.serial_identifier_type = 'qr_code' THEN
            IF NOT EXISTS (
                SELECT 1 FROM inventory_serials AS serial
                 WHERE serial.id = NEW.serial_id
                   AND serial.material_id = NEW.material_id
                   AND serial.qr_code = NEW.serial_no_raw
                   AND serial.lot_id IS NOT DISTINCT FROM NEW.lot_id
            ) OR (
                SELECT count(*) FROM qr_codes AS mapping
                 WHERE mapping.code = NEW.serial_no_raw
                   AND mapping.object_type = 'serial'
                   AND mapping.object_id = NEW.serial_id
                   AND mapping.status = 'active'
            ) <> 1 THEN
                RAISE EXCEPTION 'stocktake observation serial identifier mapping is inconsistent';
            END IF;
        ELSE
            RAISE EXCEPTION 'stocktake observation serial identifier mapping is inconsistent';
        END IF;
    END IF;"""


def _sqlite_observation_trigger_sql(
    revision_suffix: str, *, identifier_aware: bool
) -> str:
    serial_condition = (
        _sqlite_identifier_aware_serial_condition()
        if identifier_aware
        else _sqlite_legacy_serial_condition()
    )
    return f"""
CREATE TRIGGER trg_stocktake_count_observations_validate_insert_{revision_suffix}
BEFORE INSERT ON stocktake_count_observations
WHEN COALESCE((SELECT status FROM stocktake_rounds
                WHERE id = NEW.round_id AND task_id = NEW.task_id), 'missing')
       <> 'counting'
 OR (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id) IS NULL
 OR NEW.counted_at < (SELECT started_at FROM stocktake_rounds
                       WHERE id = NEW.round_id AND task_id = NEW.task_id)
 OR EXISTS (SELECT 1 FROM stocktake_round_submissions
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id)
 OR EXISTS (SELECT 1 FROM stocktake_scope_count_completions
             WHERE task_id = NEW.task_id AND round_id = NEW.round_id
               AND scope_id = NEW.scope_id)
 OR NOT EXISTS (
        SELECT 1 FROM stocktake_scopes AS scope
         WHERE scope.id = NEW.scope_id AND scope.task_id = NEW.task_id
           AND scope.owner_org_id = NEW.owner_org_id
           AND scope.location_id = NEW.location_id
           AND scope.custodian_person_id_snapshot IS
               NEW.custodian_person_id_snapshot
           AND scope.assignee_user_id = NEW.counted_by_user_id
           AND (scope.scope_mode = 'location_all' OR (
                (scope.material_id IS NULL OR scope.material_id = NEW.material_id)
            AND (scope.condition_code IS NULL OR
                 scope.condition_code = NEW.condition_code)
            AND (scope.availability_bucket IS NULL OR
                 scope.availability_bucket = NEW.availability_bucket)
           ))
    )
 OR (NEW.material_id IS NOT NULL AND (
        (SELECT count(*) FROM material_inventory_policies
          WHERE material_id = NEW.material_id
            AND effective_from <= (SELECT cutoff_at FROM stocktake_tasks
                                     WHERE id = NEW.task_id)
            AND (effective_to IS NULL OR
                 (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
                 < effective_to)) <> 1
        OR NEW.counted_qty <> round(
            NEW.counted_qty,
            (SELECT quantity_scale FROM material_inventory_policies
              WHERE material_id = NEW.material_id
                AND effective_from <= (SELECT cutoff_at FROM stocktake_tasks
                                         WHERE id = NEW.task_id)
                AND (effective_to IS NULL OR
                     (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
                     < effective_to))
        )
        OR CASE (SELECT tracking_mode FROM material_inventory_policies
                  WHERE material_id = NEW.material_id
                    AND effective_from <= (SELECT cutoff_at FROM stocktake_tasks
                                             WHERE id = NEW.task_id)
                    AND (effective_to IS NULL OR
                         (SELECT cutoff_at FROM stocktake_tasks
                           WHERE id = NEW.task_id) < effective_to))
             WHEN 'none' THEN NEW.lot_no_raw IS NOT NULL
                              OR NEW.serial_no_raw IS NOT NULL
             WHEN 'lot' THEN NEW.lot_no_raw IS NULL
                             OR NEW.serial_no_raw IS NOT NULL
             WHEN 'serial' THEN NEW.lot_no_raw IS NOT NULL
                                OR NEW.serial_no_raw IS NULL
             WHEN 'lot_and_serial' THEN NEW.lot_no_raw IS NULL
                                        OR NEW.serial_no_raw IS NULL
             ELSE 1
           END
    ))
 OR (NEW.lot_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM inventory_lots WHERE id = NEW.lot_id
          AND material_id = NEW.material_id AND lot_no = NEW.lot_no_raw
    ))
{serial_condition}
 OR (NEW.verification_status = 'verified' AND (
        EXISTS (
            SELECT 1 FROM stock_accounts AS account
             WHERE account.owner_org_id = NEW.owner_org_id
               AND account.location_id = NEW.location_id
               AND account.custodian_person_id IS
                   NEW.custodian_person_id_snapshot
               AND account.material_id = NEW.material_id
               AND account.condition_code = NEW.condition_code
               AND account.availability_bucket = NEW.availability_bucket
               AND account.lot_id IS NEW.lot_id
               AND account.created_at <=
                   (SELECT cutoff_at FROM stocktake_tasks WHERE id = NEW.task_id)
        ) OR EXISTS (
            SELECT 1
              FROM stocktake_snapshot_lines AS snapshot
              JOIN stock_accounts AS account
                ON account.id = snapshot.stock_account_id
             WHERE snapshot.task_id = NEW.task_id
               AND snapshot.scope_id = NEW.scope_id
               AND account.owner_org_id = NEW.owner_org_id
               AND account.location_id = NEW.location_id
               AND account.custodian_person_id IS
                   NEW.custodian_person_id_snapshot
               AND account.material_id = NEW.material_id
               AND account.condition_code = NEW.condition_code
               AND account.availability_bucket = NEW.availability_bucket
               AND account.lot_id IS NEW.lot_id
        )
    ))
BEGIN
    SELECT RAISE(ABORT,
        'stocktake observation is invalid, sealed, outside scope, or not unexpected');
END
"""


def _sqlite_legacy_serial_condition() -> str:
    return """ OR (NEW.serial_id IS NOT NULL AND NOT EXISTS (
        SELECT 1 FROM inventory_serials WHERE id = NEW.serial_id
          AND material_id = NEW.material_id AND serial_no = NEW.serial_no_raw
          AND lot_id IS NEW.lot_id
    ))"""


def _sqlite_identifier_aware_serial_condition() -> str:
    return """ OR (NEW.serial_id IS NOT NULL AND (
        NEW.serial_identifier_type IS NULL
        OR NEW.serial_identifier_type = 'unknown'
        OR (NEW.serial_identifier_type = 'serial_no' AND NOT EXISTS (
            SELECT 1 FROM inventory_serials AS serial
             WHERE serial.id = NEW.serial_id
               AND serial.material_id = NEW.material_id
               AND serial.serial_no = NEW.serial_no_raw
               AND serial.lot_id IS NEW.lot_id
        ))
        OR (NEW.serial_identifier_type = 'qr_code' AND (
            NOT EXISTS (
                SELECT 1 FROM inventory_serials AS serial
                 WHERE serial.id = NEW.serial_id
                   AND serial.material_id = NEW.material_id
                   AND serial.qr_code = NEW.serial_no_raw
                   AND serial.lot_id IS NEW.lot_id
            )
            OR (SELECT count(*) FROM qr_codes AS mapping
                 WHERE mapping.code = NEW.serial_no_raw
                   AND mapping.object_type = 'serial'
                   AND mapping.object_id = NEW.serial_id
                   AND mapping.status = 'active') <> 1
        ))
        OR NEW.serial_identifier_type NOT IN ('serial_no', 'qr_code', 'unknown')
    ))"""
