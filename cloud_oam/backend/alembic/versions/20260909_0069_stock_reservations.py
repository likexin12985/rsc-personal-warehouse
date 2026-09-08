"""Create immutable reservation facts and open the reservation projection axis.

Revision 0069 is the first fulfilment slice after source allocation.  A
reservation appends a fact and a posted ``reserve`` inventory transaction; it
never rewrites an allocation or a balance directly.  The API role can read and
append reservation facts and can advance only ``material_requests.reservation_status``.
Release, picking, outbound and receipt remain separate future facts.
"""

from __future__ import annotations

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260909_0069"
down_revision: str | None = "20260908_0068"
branch_labels: str | None = None
depends_on: str | None = None

MIGRATION_ROLE = "star_oam_migrator"
PRODUCTION_API_ROLE = "star_oam_api"
TABLES = ("stock_reservations", "stock_reservation_serials")
RESERVATION_TABLES = TABLES
REQUEST_GUARD_FUNCTION = "rsc_guard_material_request_identity_0029"
REQUEST_GUARD_SIGNATURE = f"public.{REQUEST_GUARD_FUNCTION}()"
APPROVAL_PROJECTION_SIGNATURE = (
    "public.rsc_validate_material_request_approval_projection_0045(uuid)"
)
SUPPLY_VALIDATE_SIGNATURE = (
    "public.rsc_validate_material_request_supply_causality_0059(uuid, bigint)"
)
SUPPLY_DISPATCH_SIGNATURE = (
    "public.rsc_dispatch_material_request_supply_causality_0059()"
)
RUNTIME_READY_SIGNATURE = "public.rsc_oam_runtime_binding_ready_0044()"

# 0068's readiness body is the exact prerequisite.  The 0069 body is the
# same function source with one exact revision marker replacement.
RUNTIME_READY_PREVIOUS_REVISION = "20260908_0068"
RUNTIME_READY_BODY_SHA256_0068 = (
    "b248454938a77c33684cc39652d8d709abfb3408d4a96d19036010fb0730292f"
)
RUNTIME_READY_BODY_SHA256_0069 = (
    "eee63dbc90cfe728d461f43c0ef824840fa20078723cf58776debb4750373e36"
)

# Existing published function hashes.  0069 replaces their source in place,
# preserving OID, owner, ACL, SECURITY DEFINER and search_path attributes.
REQUEST_GUARD_BODY_SHA256_0029 = (
    "ee818b78ed22f77eb272676d0b6d31d22bda0d250718c714bb3d1fc06ef353de"
)
REQUEST_GUARD_BODY_SHA256_0069 = (
    "b9f243b9f57c03cacb2f7f78ecda1308a250e625e141ee07629d065b18a928c1"
)
APPROVAL_PROJECTION_BODY_SHA256_0059 = (
    "b51b11c63f1ec0e5659a9a1b1d1cdce03b32c5c9b604b5c1cdf669781c9b6ca4"
)
APPROVAL_PROJECTION_BODY_SHA256_0069 = (
    "c4c7373e69992d651be7d7ad0d8fd88bdd73d241b4ff6fe824c37f9da0af42eb"
)
SUPPLY_VALIDATE_BODY_SHA256_0061 = (
    "f5803e9a3e0c931228692260c04c9bd4e544eb147aea45192277e57c7b969403"
)
SUPPLY_VALIDATE_BODY_SHA256_0069 = (
    "ffe14766ed4569ddfca748f5ec5d8c639b8b4edace208b668fd65412e2ef37dd"
)
SUPPLY_DISPATCH_BODY_SHA256_0060 = (
    "935be3c5144f0bb9d5dad8652bb8284bc7116caaefc9ccb5d177e922b41530c5"
)
SUPPLY_DISPATCH_BODY_SHA256_0069 = (
    "4228949da83f59ea1b46a8badd7c0fe8c58e188b7a88eac318ac032acd339e92"
)

CATALOG_ERROR = "0069 reservation catalog mismatch"
DOWNGRADE_BLOCKER = "cannot downgrade 0069 while reservation facts exist"
RESERVATION_GUARD_FUNCTION = "rsc_guard_stock_reservation_0069"
RESERVATION_GUARD_TRIGGER = "trg_stock_reservations_binding_0069"
RESERVATION_IMMUTABLE_TRIGGER = "trg_stock_reservations_immutable_0069"
RESERVATION_SERIAL_IMMUTABLE_TRIGGER = "trg_stock_reservation_serials_immutable_0069"
RESERVATION_SERIAL_BINDING_TRIGGER = "trg_stock_reservation_serials_binding_0069"

# The published operation check is replaced atomically in both dialects.  Keep
# the historical operations in the expression so old command rows remain
# valid and only the new fulfilment operations are opened.
COMMAND_OPERATION_CHECK_OLD = (
    "operation IN ('create', 'update_draft', 'submit', 'region_decide', "
    "'headquarters_decide', 'register_external', 'verify_external', 'withdraw', "
    "'cancel', 'propose_substitution', 'confirm_substitution', "
    "'reject_substitution', 'create_supply_task', 'update_supply_task', "
    "'cancel_supply_task')"
)
COMMAND_OPERATION_CHECK_NEW = (
    "operation IN ('create', 'update_draft', 'submit', 'region_decide', "
    "'headquarters_decide', 'register_external', 'verify_external', 'withdraw', "
    "'cancel', 'propose_substitution', 'confirm_substitution', "
    "'reject_substitution', 'create_supply_task', 'update_supply_task', "
    "'cancel_supply_task', 'allocate', 'reserve', 'release')"
)

# Exact old request-guard tail.  It is intentionally kept as a source
# replacement instead of recreating the function, so the production function
# OID and grants remain stable.
REQUEST_GUARD_AXIS_OLD = """    IF NEW.allocation_status <> 'not_allocated'
       OR NEW.reservation_status <> 'not_reserved'
       OR NEW.outbound_status <> 'not_started'
       OR NEW.shipment_status <> 'not_started'
       OR NEW.logistics_signature_status <> 'not_signed'
       OR NEW.oam_receipt_status <> 'not_occurred'
       OR NEW.personal_inbound_status <> 'not_started'
       OR NEW.notification_status <> 'not_started'
       OR NEW.reconciliation_status <> 'not_started' THEN
        RAISE EXCEPTION 'formal material-request invariant violated';
    END IF;"""

REQUEST_GUARD_AXIS_NEW = """    IF NEW.outbound_status <> 'not_started'
       OR NEW.shipment_status <> 'not_started'
       OR NEW.logistics_signature_status <> 'not_signed'
       OR NEW.oam_receipt_status <> 'not_occurred'
       OR NEW.personal_inbound_status <> 'not_started'
       OR NEW.notification_status <> 'not_started'
       OR NEW.reconciliation_status <> 'not_started' THEN
        RAISE EXCEPTION 'formal material-request invariant violated';
    END IF;
    IF NEW.allocation_status IS DISTINCT FROM OLD.allocation_status
       AND NEW.reservation_status IS DISTINCT FROM OLD.reservation_status THEN
        RAISE EXCEPTION 'formal material-request invariant violated';
    END IF;
    IF NEW.allocation_status IS DISTINCT FROM OLD.allocation_status THEN
        IF OLD.allocation_status NOT IN ('not_allocated', 'partially_allocated')
           OR NEW.allocation_status NOT IN ('partially_allocated', 'allocated')
           OR NEW.version <> OLD.version + 1
           OR NEW.updated_at <= OLD.updated_at
           OR NOT EXISTS (
               SELECT 1
                 FROM public.stock_allocations AS allocation
                 JOIN public.material_request_commands AS command
                   ON command.request_id = allocation.request_id
                  AND command.operation = 'allocate'
                  AND command.target_version = NEW.version
                WHERE allocation.request_id = NEW.id
                  AND allocation.request_version = NEW.version
                  AND allocation.status = 'allocated'
                  AND command.request_jsonb->>'request_id' = NEW.id::text
                  AND command.request_jsonb->>'target_version' = NEW.version::text
           ) THEN
            RAISE EXCEPTION 'formal material-request invariant violated';
        END IF;
    END IF;
    IF NEW.reservation_status IS DISTINCT FROM OLD.reservation_status THEN
        IF OLD.reservation_status NOT IN ('not_reserved', 'pending')
           OR NEW.reservation_status NOT IN ('pending', 'reserved')
           OR NEW.version <> OLD.version + 1
           OR NEW.updated_at <= OLD.updated_at
           OR NOT EXISTS (
               SELECT 1
                 FROM public.stock_reservations AS reservation
                 JOIN public.material_request_commands AS command
                   ON command.request_id = reservation.request_id
                  AND command.operation = 'reserve'
                  AND command.target_version = NEW.version
                WHERE reservation.request_id = NEW.id
                  AND reservation.request_version = NEW.version
                  AND reservation.status = 'reserved'
                  AND reservation.released_qty = 0
                  AND reservation.release_transaction_id IS NULL
                  AND command.request_jsonb->>'request_id' = NEW.id::text
                  AND command.request_jsonb->>'target_version' = NEW.version::text
           ) THEN
            RAISE EXCEPTION 'formal material-request invariant violated';
        END IF;
        IF NEW.reservation_status = 'reserved'
           AND EXISTS (
               SELECT 1
                 FROM public.material_request_lines AS line
                WHERE line.request_id = NEW.id
                  AND line.final_approved_qty > line.cancelled_qty
                  AND COALESCE((
                      SELECT sum(reservation.reserved_qty)
                        FROM public.stock_reservations AS reservation
                       WHERE reservation.request_line_id = line.id
                         AND reservation.status = 'reserved'
                         AND reservation.released_qty = 0
                  ), 0) < line.final_approved_qty - line.cancelled_qty
           ) THEN
            RAISE EXCEPTION 'formal material-request invariant violated';
        END IF;
    END IF;"""

# Supply validator only needs its fulfilment-axis comparison relaxed.  Supply
# still owns its three planning axes and remains strict for outbound and later
# states.  Anchor the neutral check at the request-status condition: without
# it, the replacement is a suffix of the old fragment and the required
# "new source must not already exist" preflight rejects the published body.
SUPPLY_VALIDATE_NEUTRAL_OLD = """    IF NOT FOUND OR request_row.status NOT IN (
           'approved','partially_approved','cancelled')
       OR request_row.allocation_status <> 'not_allocated'
       OR request_row.reservation_status <> 'not_reserved'
       OR request_row.outbound_status <> 'not_started'"""
SUPPLY_VALIDATE_NEUTRAL_NEW = """    IF NOT FOUND OR request_row.status NOT IN (
           'approved','partially_approved','cancelled')
       OR request_row.outbound_status <> 'not_started'"""
SUPPLY_VALIDATE_STATE_AXES_OLD = """           OR command_row.result_jsonb->'state_axes' <>
               pg_catalog.jsonb_build_object(
                   'allocation_status', request_row.allocation_status,
                   'reservation_status', request_row.reservation_status,
                   'outbound_status', request_row.outbound_status,
                   'shipment_status', request_row.shipment_status,
                   'logistics_signature_status',
                       request_row.logistics_signature_status,
                   'oam_receipt_status', request_row.oam_receipt_status,
                   'personal_inbound_status', request_row.personal_inbound_status,
                   'notification_status', request_row.notification_status,
                   'reconciliation_status', request_row.reconciliation_status)"""
SUPPLY_VALIDATE_STATE_AXES_NEW = """           OR command_row.result_jsonb->'state_axes'->>'allocation_status' IS NULL
           OR command_row.result_jsonb->'state_axes'->>'reservation_status' IS NULL
           OR command_row.result_jsonb->'state_axes'->>'allocation_status' NOT IN
               ('not_allocated', request_row.allocation_status)
           OR command_row.result_jsonb->'state_axes'->>'reservation_status' NOT IN
               ('not_reserved', request_row.reservation_status)
           OR command_row.result_jsonb->'state_axes'->>'outbound_status' <>
               request_row.outbound_status
           OR command_row.result_jsonb->'state_axes'->>'shipment_status' <>
               request_row.shipment_status
           OR command_row.result_jsonb->'state_axes'->>'logistics_signature_status' <>
               request_row.logistics_signature_status
           OR command_row.result_jsonb->'state_axes'->>'oam_receipt_status' <>
               request_row.oam_receipt_status
           OR command_row.result_jsonb->'state_axes'->>'personal_inbound_status' <>
               request_row.personal_inbound_status
           OR command_row.result_jsonb->'state_axes'->>'notification_status' <>
               request_row.notification_status
           OR command_row.result_jsonb->'state_axes'->>'reconciliation_status' <>
               request_row.reconciliation_status"""

SUPPLY_VALIDATE_CEILING_OLD = """    IF request_row.status = 'cancelled' THEN
        supply_ceiling_version := request_row.version - 1;
        IF (SELECT count(*) FROM public.material_request_commands AS command
             WHERE command.request_id = checked_request_id
               AND command.target_version = request_row.version
               AND command.operation = 'cancel') <> 1
           OR EXISTS (
               SELECT 1
                 FROM public.supply_tasks AS task
                 JOIN public.material_request_lines AS line
                   ON line.id = task.request_line_id
                WHERE line.request_id = checked_request_id
                  AND task.status NOT IN ('cancelled','closed_no_supply')
           ) THEN
            RAISE EXCEPTION 'formal material request supply projection is invalid';
        END IF;
    ELSE
        supply_ceiling_version := request_row.version;
    END IF;
    IF supply_ceiling_version <= approval_command_version THEN
        RAISE EXCEPTION 'formal material request supply projection is invalid';
    END IF;"""

SUPPLY_VALIDATE_CEILING_NEW = """    SELECT max(command.target_version)
      INTO supply_ceiling_version
      FROM public.material_request_commands AS command
     WHERE command.request_id = checked_request_id
       AND command.operation IN (
           'create_supply_task','update_supply_task','cancel_supply_task'
       );
    IF supply_ceiling_version IS NULL
       OR supply_ceiling_version <= approval_command_version THEN
        RAISE EXCEPTION 'formal material request supply projection is invalid';
    END IF;
    IF request_row.status = 'cancelled' THEN
        IF (SELECT count(*) FROM public.material_request_commands AS command
             WHERE command.request_id = checked_request_id
               AND command.target_version = request_row.version
               AND command.operation = 'cancel') <> 1
           OR EXISTS (
               SELECT 1
                 FROM public.supply_tasks AS task
                 JOIN public.material_request_lines AS line
                   ON line.id = task.request_line_id
                WHERE line.request_id = checked_request_id
                  AND task.status NOT IN ('cancelled','closed_no_supply')
           ) THEN
            RAISE EXCEPTION 'formal material request supply projection is invalid';
        END IF;
    END IF;"""

APPROVAL_PROJECTION_APPROVED_OLD = """        )) THEN
            RAISE EXCEPTION 'formal material request approval projection is invalid';
        END IF;
        IF EXISTS (
            SELECT 1
              FROM public.material_request_commands AS command
             WHERE command.request_id = checked_request_id
               AND command.operation IN (
                   'create_supply_task', 'update_supply_task',
                   'cancel_supply_task'
               )
        ) THEN
            PERFORM public.rsc_validate_material_request_supply_causality_0059(
                checked_request_id, terminal_command_version
            );
        ELSIF terminal_command_version <> request_row.version THEN
            RAISE EXCEPTION 'formal material request approval projection is invalid';
        END IF;
        RETURN;
    END IF;

    IF request_row.status = 'cancelled' THEN"""

APPROVAL_PROJECTION_APPROVED_NEW = """        )) THEN
            RAISE EXCEPTION 'formal material request approval projection is invalid';
        END IF;
        IF request_row.status = 'rejected' THEN
            IF terminal_command_version <> request_row.version THEN
                RAISE EXCEPTION 'formal material request approval projection is invalid';
            END IF;
        ELSE
            IF EXISTS (
                SELECT 1
                  FROM public.material_request_commands AS command
                 WHERE command.request_id = checked_request_id
                   AND command.operation IN (
                       'create_supply_task', 'update_supply_task',
                       'cancel_supply_task'
                   )
            ) THEN
                PERFORM public.rsc_validate_material_request_supply_causality_0059(
                    checked_request_id, terminal_command_version
                );
            END IF;
            IF EXISTS (
                SELECT 1
                  FROM public.material_request_commands AS command
                 WHERE command.request_id = checked_request_id
                   AND command.target_version > terminal_command_version
                   AND command.operation NOT IN (
                       'create_supply_task', 'update_supply_task',
                       'cancel_supply_task', 'allocate', 'reserve'
                   )
            ) THEN
                RAISE EXCEPTION 'formal material request approval projection is invalid';
            END IF;
        END IF;
        RETURN;
    END IF;

    IF request_row.status = 'cancelled' THEN"""


def _dialect_name() -> str:
    dialect = op.get_bind().dialect.name
    if dialect not in {"postgresql", "sqlite"}:
        raise RuntimeError("0069 supports only PostgreSQL and SQLite")
    return dialect


def upgrade() -> None:
    dialect = _dialect_name()
    if dialect == "postgresql":
        _lock_postgresql_upgrade_boundary()
        _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0068)
    _create_tables()
    _open_command_operations(dialect, upgrade=True)
    if dialect == "postgresql":
        _create_postgresql_reservation_guards()
        _grant_postgresql_runtime_acl()
        _replace_request_guard(upgrade=True)
        _replace_supply_functions(upgrade=True)
        _replace_approval_projection(upgrade=True)
        _replace_runtime_ready(
            expected_hash=RUNTIME_READY_BODY_SHA256_0068,
            replacement_hash=RUNTIME_READY_BODY_SHA256_0069,
            old_revision=RUNTIME_READY_PREVIOUS_REVISION,
            new_revision=revision,
        )
        _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0069)
    else:
        _replace_sqlite_request_update_guard(upgrade=True)
        _create_sqlite_reservation_guards()


def downgrade() -> None:
    dialect = _dialect_name()
    if not context.is_offline_mode():
        if dialect == "postgresql":
            _lock_postgresql_downgrade_boundary()
        if any(
            op.get_bind().execute(
                sa.text(f"SELECT EXISTS (SELECT 1 FROM {table_name})")
            ).scalar()
            for table_name in TABLES
        ):
            raise RuntimeError(DOWNGRADE_BLOCKER)
    if dialect == "postgresql":
        _verify_runtime_ready(RUNTIME_READY_BODY_SHA256_0069)
        _replace_runtime_ready(
            expected_hash=RUNTIME_READY_BODY_SHA256_0069,
            replacement_hash=RUNTIME_READY_BODY_SHA256_0068,
            old_revision=revision,
            new_revision=RUNTIME_READY_PREVIOUS_REVISION,
        )
        _replace_approval_projection(upgrade=False)
        _replace_supply_functions(upgrade=False)
        _replace_request_guard(upgrade=False)
        _drop_postgresql_reservation_guards()
        _revoke_postgresql_runtime_acl()
    else:
        _replace_sqlite_request_update_guard(upgrade=False)
        _drop_sqlite_reservation_guards()
    _open_command_operations(dialect, upgrade=False)
    op.drop_index("ix_stock_reservations_request", table_name="stock_reservations")
    op.drop_index(
        "ix_stock_reservations_stock_account_status", table_name="stock_reservations"
    )
    op.drop_index(
        "ix_stock_reservations_allocation_status", table_name="stock_reservations"
    )
    op.drop_index(
        "ix_stock_reservations_request_line_status", table_name="stock_reservations"
    )
    op.drop_table("stock_reservation_serials")
    op.drop_table("stock_reservations")


def _create_tables() -> None:
    op.create_table(
        "stock_reservations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("reservation_no", sa.String(length=100), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("request_line_id", sa.Uuid(), nullable=False),
        sa.Column("revision_id", sa.Uuid(), nullable=False),
        sa.Column("revision_no", sa.Integer(), nullable=False),
        sa.Column("allocation_id", sa.Uuid(), nullable=False),
        sa.Column("source_stock_account_id", sa.Uuid(), nullable=False),
        sa.Column("stock_account_id", sa.Uuid(), nullable=False),
        sa.Column("reserved_qty", sa.Numeric(18, 3), nullable=False),
        sa.Column(
            "released_qty", sa.Numeric(18, 3), server_default=sa.text("0"), nullable=False
        ),
        sa.Column("reserve_transaction_id", sa.Uuid(), nullable=False),
        sa.Column("release_transaction_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="reserved", nullable=False),
        sa.Column("request_version", sa.BigInteger(), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("actor_person_id", sa.Uuid(), nullable=False),
        sa.Column("authorization_version", sa.BigInteger(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["request_id"], ["material_requests.id"],
            name="fk_stock_reservations_request", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["request_line_id", "request_id", "revision_id"],
            ["material_request_lines.id", "material_request_lines.request_id", "material_request_lines.revision_id"],
            name="fk_stock_reservations_request_line", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["allocation_id"], ["stock_allocations.id"],
            name="fk_stock_reservations_allocation", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["source_stock_account_id"], ["stock_accounts.id"],
            name="fk_stock_reservations_source_account", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["stock_account_id"], ["stock_accounts.id"],
            name="fk_stock_reservations_target_account", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["reserve_transaction_id"], ["inventory_transactions.id"],
            name="fk_stock_reservations_reserve_transaction", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["release_transaction_id"], ["inventory_transactions.id"],
            name="fk_stock_reservations_release_transaction", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"], ["users.id"],
            name="fk_stock_reservations_actor_user", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["actor_person_id"], ["people.id"],
            name="fk_stock_reservations_actor_person", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_stock_reservations"),
        sa.UniqueConstraint("reservation_no", name="uq_stock_reservations_number"),
        sa.UniqueConstraint(
            "idempotency_key_hash", name="uq_stock_reservations_idempotency"
        ),
        sa.UniqueConstraint(
            "id", "allocation_id", name="uq_stock_reservations_id_allocation"
        ),
        sa.CheckConstraint("reserved_qty > 0", name="ck_stock_reservations_quantity"),
        sa.CheckConstraint(
            "released_qty >= 0 AND released_qty <= reserved_qty",
            name="ck_stock_reservations_released_quantity",
        ),
        sa.CheckConstraint("request_version >= 0", name="ck_stock_reservations_request_version"),
        sa.CheckConstraint("revision_no > 0", name="ck_stock_reservations_revision"),
        sa.CheckConstraint(
            "authorization_version > 0",
            name="ck_stock_reservations_authorization_version",
        ),
        sa.CheckConstraint(
            "source_stock_account_id <> stock_account_id",
            name="ck_stock_reservations_distinct_accounts",
        ),
        sa.CheckConstraint(
            "status IN ('reserved', 'released', 'fulfilled')",
            name="ck_stock_reservations_status",
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64 AND length(request_hash) = 64",
            name="ck_stock_reservations_hashes",
        ),
        sa.CheckConstraint(
            "reserve_transaction_id IS NOT NULL",
            name="ck_stock_reservations_reserve_transaction",
        ),
        sa.CheckConstraint(
            "reserve_transaction_id <> release_transaction_id OR release_transaction_id IS NULL",
            name="ck_stock_reservations_distinct_transactions",
        ),
        sa.CheckConstraint(
            "(status = 'released' AND release_transaction_id IS NOT NULL AND released_qty = reserved_qty) OR "
            "(status = 'reserved' AND release_transaction_id IS NULL AND released_qty = 0) OR "
            "(status = 'fulfilled' AND release_transaction_id IS NULL AND released_qty = 0)",
            name="ck_stock_reservations_status_projection",
        ),
    )
    op.create_index(
        "ix_stock_reservations_request_line_status", "stock_reservations", ["request_line_id", "status"]
    )
    op.create_index(
        "ix_stock_reservations_allocation_status", "stock_reservations", ["allocation_id", "status"]
    )
    op.create_index(
        "ix_stock_reservations_stock_account_status", "stock_reservations", ["stock_account_id", "status"]
    )
    op.create_index("ix_stock_reservations_request", "stock_reservations", ["request_id"])
    op.create_table(
        "stock_reservation_serials",
        sa.Column("reservation_id", sa.Uuid(), nullable=False),
        sa.Column("allocation_id", sa.Uuid(), nullable=False),
        sa.Column("serial_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["reservation_id", "allocation_id"],
            ["stock_reservations.id", "stock_reservations.allocation_id"],
            name="fk_stock_reservation_serials_reservation", ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["allocation_id", "serial_id"],
            ["stock_allocation_serials.allocation_id", "stock_allocation_serials.serial_id"],
            name="fk_stock_reservation_serials_allocation_serial", ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint(
            "reservation_id", "allocation_id", "serial_id",
            name="pk_stock_reservation_serials",
        ),
        sa.Index("ix_stock_reservation_serials_serial", "serial_id"),
    )


def _lock_postgresql_upgrade_boundary() -> None:
    op.execute(
        "LOCK TABLE public.alembic_version, public.material_requests, "
        "public.material_request_lines, public.material_request_commands, "
        "public.stock_allocations, public.stock_allocation_serials, "
        "public.stock_accounts, public.stock_balances, public.inventory_transactions, "
        "public.inventory_movements, public.inventory_movement_serials, "
        "public.inventory_serials, public.audit_events, public.state_transition_events "
        "IN ACCESS EXCLUSIVE MODE"
    )


def _lock_postgresql_downgrade_boundary() -> None:
    op.execute(
        "LOCK TABLE public.alembic_version, public.stock_reservation_serials, "
        "public.stock_reservations, public.material_request_commands, "
        "public.material_requests IN ACCESS EXCLUSIVE MODE"
    )


def _verify_runtime_ready(expected_hash: str) -> None:
    if expected_hash not in {
        RUNTIME_READY_BODY_SHA256_0068,
        RUNTIME_READY_BODY_SHA256_0069,
    }:
        raise ValueError("unsupported 0069 readiness hash")
    op.execute(
        f"""
DO $rsc_0069_readiness$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}');
    migrator_oid oid := pg_catalog.to_regrole('{MIGRATION_ROLE}');
BEGIN
    IF current_user <> '{MIGRATION_ROLE}' OR session_user <> '{MIGRATION_ROLE}'
       OR function_oid IS NULL OR migrator_oid IS NULL
       OR NOT EXISTS (
           SELECT 1 FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = function_oid
              AND function_row.proowner = migrator_oid
              AND function_row.prokind = 'f'
              AND function_row.pronargs = 0
              AND function_row.prorettype = 'boolean'::pg_catalog.regtype
              AND function_row.proretset = false
              AND function_row.proargmodes IS NULL
              AND function_row.pronargdefaults = 0
              AND function_row.provariadic = 0
              AND function_row.proparallel = 'u'
              AND function_row.provolatile = 's'
              AND function_row.prosecdef
              AND NOT function_row.proisstrict
              AND NOT function_row.proleakproof
              AND EXISTS (
                  SELECT 1 FROM pg_catalog.pg_language AS language_row
                   WHERE language_row.oid = function_row.prolang
                     AND language_row.lanname = 'sql'
              )
              AND function_row.proconfig = ARRAY['search_path=pg_catalog']::text[]
       )
       OR (SELECT pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
               function_row.prosrc, 'UTF8')), 'hex')
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = function_oid)
          IS DISTINCT FROM '{expected_hash}' THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: readiness function identity or hash mismatch';
    END IF;
END
$rsc_0069_readiness$
"""
    )


def _replace_runtime_ready(
    *, expected_hash: str, replacement_hash: str,
    old_revision: str, new_revision: str,
) -> None:
    if (expected_hash, replacement_hash, old_revision, new_revision) not in {
        (
            RUNTIME_READY_BODY_SHA256_0068,
            RUNTIME_READY_BODY_SHA256_0069,
            RUNTIME_READY_PREVIOUS_REVISION,
            revision,
        ),
        (
            RUNTIME_READY_BODY_SHA256_0069,
            RUNTIME_READY_BODY_SHA256_0068,
            revision,
            RUNTIME_READY_PREVIOUS_REVISION,
        ),
    }:
        raise ValueError("unsupported 0069 readiness replacement")
    op.execute(
        f"""
DO $rsc_0069_replace_readiness$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}');
    original_owner oid;
    original_acl aclitem[];
    original_security boolean;
    original_config text[];
    function_source text;
    function_definition text;
BEGIN
    SELECT function_row.proowner, function_row.proacl, function_row.prosecdef,
           function_row.proconfig, function_row.prosrc,
           pg_catalog.pg_get_functiondef(function_row.oid)
      INTO original_owner, original_acl, original_security, original_config,
           function_source, function_definition
      FROM pg_catalog.pg_proc AS function_row
     WHERE function_row.oid = function_oid
       AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
             function_row.prosrc, 'UTF8')), 'hex') = '{expected_hash}';
    IF current_user <> '{MIGRATION_ROLE}' OR session_user <> '{MIGRATION_ROLE}'
       OR function_source IS NULL
       OR (pg_catalog.length(function_source) - pg_catalog.length(
           pg_catalog.replace(function_source, '{old_revision}', ''))) /
           pg_catalog.length('{old_revision}') <> 1
       OR pg_catalog.strpos(function_source, '{new_revision}') <> 0 THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: readiness source mismatch';
    END IF;
    function_definition := pg_catalog.replace(
        function_definition, '{old_revision}', '{new_revision}');
    EXECUTE function_definition;
    IF pg_catalog.to_regprocedure('{RUNTIME_READY_SIGNATURE}') IS DISTINCT FROM function_oid
       OR (SELECT function_row.proowner IS DISTINCT FROM original_owner
                  OR function_row.proacl IS DISTINCT FROM original_acl
                  OR function_row.prosecdef IS DISTINCT FROM original_security
                  OR function_row.proconfig IS DISTINCT FROM original_config
                  OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                         function_row.prosrc, 'UTF8')), 'hex')
                     IS DISTINCT FROM '{replacement_hash}'
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = function_oid) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: readiness replacement drift';
    END IF;
END
$rsc_0069_replace_readiness$
"""
    )


def _replace_function_source(
    *, signature: str, expected_hash: str, replacement_hash: str,
    replacements: tuple[tuple[str, str], ...], label: str,
) -> None:
    checks: list[str] = []
    definition_replacements: list[str] = []
    for old, new in replacements:
        checks.append(
            f"""IF (pg_catalog.length(function_source) - pg_catalog.length(
            pg_catalog.replace(function_source, $old${old}$old$, ''))
        ) / pg_catalog.length($old${old}$old$) <> 1
       OR pg_catalog.strpos(function_source, $new${new}$new$) <> 0 THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: {label} source replacement mismatch';
    END IF;
    function_source := pg_catalog.replace(
        function_source, $old${old}$old$, $new${new}$new$);"""
        )
        definition_replacements.append(
            f"""function_definition := pg_catalog.replace(
        function_definition, $old${old}$old$, $new${new}$new$);"""
        )
    op.execute(
        f"""
DO $rsc_0069_replace_{label}$
DECLARE
    function_oid oid := pg_catalog.to_regprocedure('{signature}');
    original_owner oid;
    original_acl aclitem[];
    original_security boolean;
    original_config text[];
    function_source text;
    function_definition text;
BEGIN
    SELECT function_row.proowner, function_row.proacl,
           function_row.prosecdef, function_row.proconfig,
           function_row.prosrc, pg_catalog.pg_get_functiondef(function_row.oid)
      INTO original_owner, original_acl, original_security, original_config,
           function_source, function_definition
      FROM pg_catalog.pg_proc AS function_row
     WHERE function_row.oid = function_oid
       AND pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
             function_row.prosrc, 'UTF8')), 'hex') = '{expected_hash}';
    IF current_user <> '{MIGRATION_ROLE}' OR session_user <> '{MIGRATION_ROLE}'
       OR function_source IS NULL THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: {label} prerequisite mismatch';
    END IF;
    {''.join(checks)}
    {''.join(definition_replacements)}
    EXECUTE function_definition;
    IF pg_catalog.to_regprocedure('{signature}') IS DISTINCT FROM function_oid
       OR (SELECT function_row.proowner IS DISTINCT FROM original_owner
                  OR function_row.proacl IS DISTINCT FROM original_acl
                  OR function_row.prosecdef IS DISTINCT FROM original_security
                  OR function_row.proconfig IS DISTINCT FROM original_config
                  OR pg_catalog.encode(pg_catalog.sha256(pg_catalog.convert_to(
                         function_row.prosrc, 'UTF8')), 'hex')
                     IS DISTINCT FROM '{replacement_hash}'
             FROM pg_catalog.pg_proc AS function_row
            WHERE function_row.oid = function_oid) THEN
        RAISE EXCEPTION '{CATALOG_ERROR}: {label} replacement drift';
    END IF;
END
$rsc_0069_replace_{label}$
"""
    )


def _replace_request_guard(*, upgrade: bool) -> None:
    if upgrade:
        _replace_function_source(
            signature=REQUEST_GUARD_SIGNATURE,
            expected_hash=REQUEST_GUARD_BODY_SHA256_0029,
            replacement_hash=REQUEST_GUARD_BODY_SHA256_0069,
            replacements=((REQUEST_GUARD_AXIS_OLD, REQUEST_GUARD_AXIS_NEW),),
            label="request_guard",
        )
    else:
        _replace_function_source(
            signature=REQUEST_GUARD_SIGNATURE,
            expected_hash=REQUEST_GUARD_BODY_SHA256_0069,
            replacement_hash=REQUEST_GUARD_BODY_SHA256_0029,
            replacements=((REQUEST_GUARD_AXIS_NEW, REQUEST_GUARD_AXIS_OLD),),
            label="request_guard",
        )


def _replace_supply_functions(*, upgrade: bool) -> None:
    if upgrade:
        _replace_function_source(
            signature=SUPPLY_VALIDATE_SIGNATURE,
            expected_hash=SUPPLY_VALIDATE_BODY_SHA256_0061,
            replacement_hash=SUPPLY_VALIDATE_BODY_SHA256_0069,
            replacements=(
                (SUPPLY_VALIDATE_CEILING_OLD, SUPPLY_VALIDATE_CEILING_NEW),
                (SUPPLY_VALIDATE_NEUTRAL_OLD, SUPPLY_VALIDATE_NEUTRAL_NEW),
                (SUPPLY_VALIDATE_STATE_AXES_OLD, SUPPLY_VALIDATE_STATE_AXES_NEW),
            ),
            label="supply_validator",
        )
        _replace_function_source(
            signature=SUPPLY_DISPATCH_SIGNATURE,
            expected_hash=SUPPLY_DISPATCH_BODY_SHA256_0060,
            replacement_hash=SUPPLY_DISPATCH_BODY_SHA256_0069,
            replacements=((_dispatcher_body_0060(), _dispatcher_body_0069()),),
            label="supply_dispatcher",
        )
    else:
        _replace_function_source(
            signature=SUPPLY_DISPATCH_SIGNATURE,
            expected_hash=SUPPLY_DISPATCH_BODY_SHA256_0069,
            replacement_hash=SUPPLY_DISPATCH_BODY_SHA256_0060,
            replacements=((_dispatcher_body_0069(), _dispatcher_body_0060()),),
            label="supply_dispatcher",
        )
        _replace_function_source(
            signature=SUPPLY_VALIDATE_SIGNATURE,
            expected_hash=SUPPLY_VALIDATE_BODY_SHA256_0069,
            replacement_hash=SUPPLY_VALIDATE_BODY_SHA256_0061,
            replacements=(
                (SUPPLY_VALIDATE_STATE_AXES_NEW, SUPPLY_VALIDATE_STATE_AXES_OLD),
                (SUPPLY_VALIDATE_NEUTRAL_NEW, SUPPLY_VALIDATE_NEUTRAL_OLD),
                (SUPPLY_VALIDATE_CEILING_NEW, SUPPLY_VALIDATE_CEILING_OLD),
            ),
            label="supply_validator",
        )


def _replace_approval_projection(*, upgrade: bool) -> None:
    _replace_function_source(
        signature=APPROVAL_PROJECTION_SIGNATURE,
        expected_hash=(
            APPROVAL_PROJECTION_BODY_SHA256_0059
            if upgrade
            else APPROVAL_PROJECTION_BODY_SHA256_0069
        ),
        replacement_hash=(
            APPROVAL_PROJECTION_BODY_SHA256_0069
            if upgrade
            else APPROVAL_PROJECTION_BODY_SHA256_0059
        ),
        replacements=(
            (
                APPROVAL_PROJECTION_APPROVED_OLD
                if upgrade
                else APPROVAL_PROJECTION_APPROVED_NEW,
                APPROVAL_PROJECTION_APPROVED_NEW
                if upgrade
                else APPROVAL_PROJECTION_APPROVED_OLD,
            ),
        ),
        label="approval_projection",
    )


def _dispatcher_body_0060() -> str:
    # Keep this exact published body in sync with 0060.  The leading/trailing
    # newline is added by PostgreSQL to pg_proc.prosrc and by the hash tests.
    return """DECLARE
    target_request_id uuid;
    supply_fact boolean := false;
    has_supply_command boolean := false;
BEGIN
    IF TG_TABLE_NAME = 'material_requests' THEN
        target_request_id := COALESCE(NEW.id, OLD.id);
    ELSIF TG_TABLE_NAME = 'material_request_commands' THEN
        target_request_id := COALESCE(NEW.request_id, OLD.request_id);
        supply_fact := COALESCE(NEW.operation, OLD.operation) IN (
            'create_supply_task','update_supply_task','cancel_supply_task');
    ELSIF TG_TABLE_NAME = 'supply_tasks' THEN
        supply_fact := true;
        SELECT line.request_id INTO target_request_id
          FROM public.material_request_lines AS line
         WHERE line.id = COALESCE(NEW.request_line_id, OLD.request_line_id);
    ELSIF TG_TABLE_NAME = 'state_transition_events' THEN
        supply_fact := COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'supply_task';
        IF supply_fact THEN
            SELECT line.request_id INTO target_request_id
              FROM public.supply_tasks AS task
              JOIN public.material_request_lines AS line
                ON line.id = task.request_line_id
             WHERE task.id::text = COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        ELSIF COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'material_request' THEN
            SELECT request.id INTO target_request_id
              FROM public.material_requests AS request
             WHERE request.id::text = COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        END IF;
    ELSIF TG_TABLE_NAME = 'audit_events' THEN
        supply_fact := COALESCE(NEW.action, OLD.action) IN (
            'material_request.supply_task.create',
            'material_request.supply_task.update',
            'material_request.supply_task.cancel');
        IF COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'material_request' THEN
            SELECT request.id INTO target_request_id
              FROM public.material_requests AS request
             WHERE request.id::text = COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        ELSIF COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'supply_task' THEN
            SELECT line.request_id INTO target_request_id
              FROM public.supply_tasks AS task
              JOIN public.material_request_lines AS line
                ON line.id = task.request_line_id
             WHERE task.id::text = COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        END IF;
    END IF;
    IF supply_fact AND target_request_id IS NULL THEN
        RAISE EXCEPTION 'formal material request supply projection is invalid' USING ERRCODE = '23514';
    END IF;
    IF target_request_id IS NOT NULL THEN
        SELECT EXISTS (
            SELECT 1 FROM public.material_request_commands AS command
             WHERE command.request_id = target_request_id
               AND command.operation IN (
                   'create_supply_task','update_supply_task','cancel_supply_task'
               )) INTO has_supply_command;
        IF supply_fact AND NOT has_supply_command THEN
            RAISE EXCEPTION 'formal material request supply projection is invalid' USING ERRCODE = '23514';
        END IF;
        IF has_supply_command THEN
            PERFORM public.rsc_validate_material_request_approval_projection_0045(
                target_request_id);
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END"""


def _dispatcher_body_0069() -> str:
    return """DECLARE
    target_request_id uuid;
    supply_fact boolean := false;
    fulfilment_fact boolean := false;
    has_supply_command boolean := false;
BEGIN
    IF TG_TABLE_NAME = 'material_requests' THEN
        target_request_id := COALESCE(NEW.id, OLD.id);
        fulfilment_fact := NEW.allocation_status IS DISTINCT FROM OLD.allocation_status
            OR NEW.reservation_status IS DISTINCT FROM OLD.reservation_status;
    ELSIF TG_TABLE_NAME = 'material_request_commands' THEN
        target_request_id := COALESCE(NEW.request_id, OLD.request_id);
        supply_fact := COALESCE(NEW.operation, OLD.operation) IN (
            'create_supply_task','update_supply_task','cancel_supply_task');
        fulfilment_fact := COALESCE(NEW.operation, OLD.operation) IN (
            'allocate','reserve','release');
    ELSIF TG_TABLE_NAME = 'supply_tasks' THEN
        supply_fact := true;
        SELECT line.request_id INTO target_request_id
          FROM public.material_request_lines AS line
         WHERE line.id = COALESCE(NEW.request_line_id, OLD.request_line_id);
    ELSIF TG_TABLE_NAME = 'state_transition_events' THEN
        supply_fact := COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'supply_task';
        fulfilment_fact := COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'material_request'
            AND COALESCE(NEW.reason, OLD.reason) IN (
                'material_request_allocation_created',
                'material_request_reservation_created');
        IF supply_fact THEN
            SELECT line.request_id INTO target_request_id
              FROM public.supply_tasks AS task
              JOIN public.material_request_lines AS line
                ON line.id = task.request_line_id
             WHERE task.id::text = COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        ELSIF COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'material_request' THEN
            SELECT request.id INTO target_request_id
              FROM public.material_requests AS request
             WHERE request.id::text = COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        END IF;
    ELSIF TG_TABLE_NAME = 'audit_events' THEN
        supply_fact := COALESCE(NEW.action, OLD.action) IN (
            'material_request.supply_task.create',
            'material_request.supply_task.update',
            'material_request.supply_task.cancel');
        fulfilment_fact := COALESCE(NEW.action, OLD.action) IN (
            'material_request_allocation_created',
            'material_request_reservation_created');
        IF COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'material_request' THEN
            SELECT request.id INTO target_request_id
              FROM public.material_requests AS request
             WHERE request.id::text = COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        ELSIF COALESCE(NEW.aggregate_type, OLD.aggregate_type) = 'supply_task' THEN
            SELECT line.request_id INTO target_request_id
              FROM public.supply_tasks AS task
              JOIN public.material_request_lines AS line
                ON line.id = task.request_line_id
             WHERE task.id::text = COALESCE(NEW.aggregate_id, OLD.aggregate_id);
        END IF;
    END IF;
    IF fulfilment_fact AND TG_TABLE_NAME <> 'material_requests' THEN
        IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
        RETURN NEW;
    END IF;
    IF supply_fact AND target_request_id IS NULL THEN
        RAISE EXCEPTION 'formal material request supply projection is invalid' USING ERRCODE = '23514';
    END IF;
    IF target_request_id IS NOT NULL THEN
        SELECT EXISTS (
            SELECT 1 FROM public.material_request_commands AS command
             WHERE command.request_id = target_request_id
               AND command.operation IN (
                   'create_supply_task','update_supply_task','cancel_supply_task'
               )) INTO has_supply_command;
        IF supply_fact AND NOT has_supply_command THEN
            RAISE EXCEPTION 'formal material request supply projection is invalid' USING ERRCODE = '23514';
        END IF;
        -- 0045 is still run after the aggregate UPDATE.  It verifies the full
        -- command count/target-version chain, including allocate/reserve facts.
        IF has_supply_command OR fulfilment_fact THEN
            PERFORM public.rsc_validate_material_request_approval_projection_0045(
                target_request_id);
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END"""



def _create_postgresql_reservation_guards() -> None:
    """Install append-only and source/target/ledger binding guards."""
    serial_function = RESERVATION_SERIAL_BINDING_TRIGGER.replace("trg_", "rsc_guard_")
    op.execute(
        f"""
CREATE FUNCTION public.{RESERVATION_GUARD_FUNCTION}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        RAISE EXCEPTION '0069 reservation facts are append-only';
    END IF;
    IF NEW.status <> 'reserved'
       OR NEW.released_qty <> 0
       OR NEW.release_transaction_id IS NOT NULL THEN
        RAISE EXCEPTION '0069 reservation fact must start reserved';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM public.stock_allocations AS allocation
          JOIN public.stock_accounts AS source_account
            ON source_account.id = NEW.source_stock_account_id
          JOIN public.stock_accounts AS target_account
            ON target_account.id = NEW.stock_account_id
         WHERE allocation.id = NEW.allocation_id
           AND allocation.request_id = NEW.request_id
           AND allocation.request_line_id = NEW.request_line_id
           AND allocation.revision_id = NEW.revision_id
           AND allocation.revision_no = NEW.revision_no
           AND allocation.source_stock_account_id = NEW.source_stock_account_id
           AND source_account.availability_bucket = 'available'
           AND target_account.availability_bucket = 'reserved'
           AND source_account.id <> target_account.id
           AND source_account.owner_org_id IS NOT DISTINCT FROM target_account.owner_org_id
           AND source_account.custodian_person_id IS NOT DISTINCT FROM target_account.custodian_person_id
           AND source_account.location_id = target_account.location_id
           AND source_account.material_id = target_account.material_id
           AND source_account.condition_code = target_account.condition_code
           AND source_account.lot_id IS NOT DISTINCT FROM target_account.lot_id
    ) THEN
        RAISE EXCEPTION '0069 reservation source/target binding is invalid';
    END IF;
    IF NOT EXISTS (
        SELECT 1
          FROM public.inventory_transactions AS transaction_row
          JOIN public.inventory_movements AS movement
            ON movement.transaction_id = transaction_row.id
         WHERE transaction_row.id = NEW.reserve_transaction_id
           AND transaction_row.movement_type = 'reserve'
           AND transaction_row.status = 'posted'
           AND movement.from_account_id = NEW.source_stock_account_id
           AND movement.to_account_id = NEW.stock_account_id
           AND movement.quantity = NEW.reserved_qty
    ) THEN
        RAISE EXCEPTION '0069 reservation transaction binding is invalid';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    op.execute(
        f"""
CREATE FUNCTION public.{serial_function}()
RETURNS trigger
LANGUAGE plpgsql
SECURITY DEFINER
SET search_path = pg_catalog, public
AS $$
BEGIN
    IF TG_OP IN ('UPDATE', 'DELETE') THEN
        RAISE EXCEPTION '0069 reservation serial facts are append-only';
    END IF;
    IF NOT EXISTS (
        SELECT 1 FROM public.stock_reservations AS reservation
         WHERE reservation.id = NEW.reservation_id
           AND reservation.allocation_id = NEW.allocation_id
           AND reservation.status = 'reserved'
    ) OR NOT EXISTS (
        SELECT 1 FROM public.stock_allocation_serials AS allocation_serial
         WHERE allocation_serial.allocation_id = NEW.allocation_id
           AND allocation_serial.serial_id = NEW.serial_id
    ) THEN
        RAISE EXCEPTION '0069 reservation serial binding is invalid';
    END IF;
    RETURN NEW;
END
$$
"""
    )
    for table_name, trigger_name, timing in (
        ("stock_reservations", RESERVATION_GUARD_TRIGGER, "INSERT"),
        ("stock_reservations", RESERVATION_IMMUTABLE_TRIGGER, "UPDATE OR DELETE"),
        ("stock_reservation_serials", RESERVATION_SERIAL_BINDING_TRIGGER, "INSERT"),
        ("stock_reservation_serials", RESERVATION_SERIAL_IMMUTABLE_TRIGGER, "UPDATE OR DELETE"),
    ):
        function_name = RESERVATION_GUARD_FUNCTION if table_name == "stock_reservations" else serial_function
        op.execute(
            f"CREATE TRIGGER {trigger_name} BEFORE {timing} ON public.{table_name} "
            f"FOR EACH ROW EXECUTE FUNCTION public.{function_name}()"
        )
        op.execute(f"ALTER TABLE public.{table_name} ENABLE ALWAYS TRIGGER {trigger_name}")
    for function_name in (RESERVATION_GUARD_FUNCTION, serial_function):
        op.execute(f"ALTER FUNCTION public.{function_name}() OWNER TO {MIGRATION_ROLE}")
        op.execute(f"REVOKE ALL ON FUNCTION public.{function_name}() FROM PUBLIC")
        op.execute(f"REVOKE ALL ON FUNCTION public.{function_name}() FROM {PRODUCTION_API_ROLE}")


def _drop_postgresql_reservation_guards() -> None:
    serial_function = RESERVATION_SERIAL_BINDING_TRIGGER.replace("trg_", "rsc_guard_")
    for table_name, trigger_name in (
        ("stock_reservation_serials", RESERVATION_SERIAL_IMMUTABLE_TRIGGER),
        ("stock_reservation_serials", RESERVATION_SERIAL_BINDING_TRIGGER),
        ("stock_reservations", RESERVATION_IMMUTABLE_TRIGGER),
        ("stock_reservations", RESERVATION_GUARD_TRIGGER),
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name} ON public.{table_name}")
    for function_name in (serial_function, RESERVATION_GUARD_FUNCTION):
        op.execute(f"DROP FUNCTION IF EXISTS public.{function_name}()")


def _create_sqlite_reservation_guards() -> None:
    op.execute(
        f"""
CREATE TRIGGER {RESERVATION_IMMUTABLE_TRIGGER}
BEFORE UPDATE ON stock_reservations
BEGIN SELECT RAISE(ABORT, '0069 reservation facts are append-only'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {RESERVATION_IMMUTABLE_TRIGGER}_delete
BEFORE DELETE ON stock_reservations
BEGIN SELECT RAISE(ABORT, '0069 reservation facts are append-only'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {RESERVATION_GUARD_TRIGGER}
BEFORE INSERT ON stock_reservations
WHEN NEW.status <> 'reserved'
  OR NEW.released_qty <> 0
  OR NEW.release_transaction_id IS NOT NULL
  OR NOT EXISTS (
      SELECT 1
        FROM stock_allocations AS allocation
        JOIN stock_accounts AS source_account
          ON lower(replace(source_account.id, '-', '')) = lower(replace(NEW.source_stock_account_id, '-', ''))
        JOIN stock_accounts AS target_account
          ON lower(replace(target_account.id, '-', '')) = lower(replace(NEW.stock_account_id, '-', ''))
       WHERE lower(replace(allocation.id, '-', '')) = lower(replace(NEW.allocation_id, '-', ''))
         AND lower(replace(allocation.request_id, '-', '')) = lower(replace(NEW.request_id, '-', ''))
         AND lower(replace(allocation.request_line_id, '-', '')) = lower(replace(NEW.request_line_id, '-', ''))
         AND lower(replace(allocation.revision_id, '-', '')) = lower(replace(NEW.revision_id, '-', ''))
         AND allocation.revision_no = NEW.revision_no
         AND lower(replace(allocation.source_stock_account_id, '-', '')) = lower(replace(NEW.source_stock_account_id, '-', ''))
         AND source_account.availability_bucket = 'available'
         AND target_account.availability_bucket = 'reserved'
         AND lower(replace(source_account.id, '-', '')) <> lower(replace(target_account.id, '-', ''))
         AND source_account.owner_org_id IS target_account.owner_org_id
         AND source_account.custodian_person_id IS target_account.custodian_person_id
         AND source_account.location_id IS target_account.location_id
         AND source_account.material_id IS target_account.material_id
         AND source_account.condition_code IS target_account.condition_code
         AND source_account.lot_id IS target_account.lot_id
  )
  OR NOT EXISTS (
      SELECT 1
        FROM inventory_transactions AS transaction_row
        JOIN inventory_movements AS movement ON movement.transaction_id = transaction_row.id
       WHERE lower(replace(transaction_row.id, '-', '')) = lower(replace(NEW.reserve_transaction_id, '-', ''))
         AND transaction_row.movement_type = 'reserve'
         AND transaction_row.status = 'posted'
         AND lower(replace(movement.from_account_id, '-', '')) = lower(replace(NEW.source_stock_account_id, '-', ''))
         AND lower(replace(movement.to_account_id, '-', '')) = lower(replace(NEW.stock_account_id, '-', ''))
         AND movement.quantity = NEW.reserved_qty
  )
BEGIN SELECT RAISE(ABORT, '0069 reservation binding is invalid'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {RESERVATION_SERIAL_IMMUTABLE_TRIGGER}
BEFORE UPDATE ON stock_reservation_serials
BEGIN SELECT RAISE(ABORT, '0069 reservation serial facts are append-only'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {RESERVATION_SERIAL_IMMUTABLE_TRIGGER}_delete
BEFORE DELETE ON stock_reservation_serials
BEGIN SELECT RAISE(ABORT, '0069 reservation serial facts are append-only'); END
"""
    )
    op.execute(
        f"""
CREATE TRIGGER {RESERVATION_SERIAL_BINDING_TRIGGER}
BEFORE INSERT ON stock_reservation_serials
WHEN NOT EXISTS (
    SELECT 1 FROM stock_reservations AS reservation
     WHERE lower(replace(reservation.id, '-', '')) = lower(replace(NEW.reservation_id, '-', ''))
       AND lower(replace(reservation.allocation_id, '-', '')) = lower(replace(NEW.allocation_id, '-', ''))
       AND reservation.status = 'reserved'
) OR NOT EXISTS (
    SELECT 1 FROM stock_allocation_serials AS allocation_serial
     WHERE lower(replace(allocation_serial.allocation_id, '-', '')) = lower(replace(NEW.allocation_id, '-', ''))
       AND lower(replace(allocation_serial.serial_id, '-', '')) = lower(replace(NEW.serial_id, '-', ''))
)
BEGIN SELECT RAISE(ABORT, '0069 reservation serial binding is invalid'); END
"""
    )


def _drop_sqlite_reservation_guards() -> None:
    for trigger_name in (
        f"{RESERVATION_SERIAL_BINDING_TRIGGER}_delete",
        RESERVATION_SERIAL_BINDING_TRIGGER,
        f"{RESERVATION_SERIAL_IMMUTABLE_TRIGGER}_delete",
        RESERVATION_SERIAL_IMMUTABLE_TRIGGER,
        f"{RESERVATION_IMMUTABLE_TRIGGER}_delete",
        RESERVATION_IMMUTABLE_TRIGGER,
        RESERVATION_GUARD_TRIGGER,
    ):
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")

def _grant_postgresql_runtime_acl() -> None:
    for table_name in TABLES:
        op.execute(f"ALTER TABLE public.{table_name} OWNER TO {MIGRATION_ROLE}")
        op.execute(f"REVOKE ALL ON TABLE public.{table_name} FROM PUBLIC")
        op.execute(
            f"GRANT SELECT, INSERT ON TABLE public.{table_name} TO {PRODUCTION_API_ROLE}"
        )
    op.execute(
        f"GRANT UPDATE (reservation_status) ON TABLE public.material_requests TO {PRODUCTION_API_ROLE}"
    )


def _revoke_postgresql_runtime_acl() -> None:
    op.execute(
        f"REVOKE UPDATE (reservation_status) ON TABLE public.material_requests FROM {PRODUCTION_API_ROLE}"
    )


def _open_command_operations(dialect: str, *, upgrade: bool) -> None:
    if dialect == "postgresql":
        if upgrade:
            op.execute(
                "ALTER TABLE public.material_request_commands DROP CONSTRAINT "
                "ck_material_request_commands_operation"
            )
            op.execute(
                "ALTER TABLE public.material_request_commands ADD CONSTRAINT "
                f"ck_material_request_commands_operation CHECK ({COMMAND_OPERATION_CHECK_NEW})"
            )
        else:
            op.execute(
                "ALTER TABLE public.material_request_commands DROP CONSTRAINT "
                "ck_material_request_commands_operation"
            )
            op.execute(
                "ALTER TABLE public.material_request_commands ADD CONSTRAINT "
                f"ck_material_request_commands_operation CHECK ({COMMAND_OPERATION_CHECK_OLD})"
            )
        return
    # SQLite rebuilds the command table for a CHECK replacement.  Existing
    # approval/content/supply triggers reference that table and SQLite rejects
    # the temporary rename unless those dependent triggers are preserved first.
    dependent_triggers = _capture_sqlite_table_triggers("material_request_commands")
    _drop_sqlite_table_triggers(dependent_triggers)
    try:
        with op.batch_alter_table("material_request_commands", recreate="always") as batch:
            batch.drop_constraint("ck_material_request_commands_operation", type_="check")
            batch.create_check_constraint(
                "ck_material_request_commands_operation",
                COMMAND_OPERATION_CHECK_NEW if upgrade else COMMAND_OPERATION_CHECK_OLD,
            )
    finally:
        _restore_sqlite_table_triggers(dependent_triggers)


def _capture_sqlite_table_triggers(table_name: str) -> tuple[tuple[str, str], ...]:
    rows = op.get_bind().exec_driver_sql(
        "SELECT name, sql FROM sqlite_master "
        "WHERE type = 'trigger' AND (tbl_name = ? OR sql LIKE ?) ORDER BY name",
        (table_name, f"%{table_name}%"),
    ).all()
    return tuple((str(row[0]), str(row[1])) for row in rows if row[1])


def _drop_sqlite_table_triggers(triggers: tuple[tuple[str, str], ...]) -> None:
    for trigger_name, _ in triggers:
        op.execute(f"DROP TRIGGER IF EXISTS {trigger_name}")


def _restore_sqlite_table_triggers(triggers: tuple[tuple[str, str], ...]) -> None:
    for _, trigger_sql in triggers:
        op.execute(trigger_sql)


def _replace_sqlite_request_update_guard(*, upgrade: bool) -> None:
    # The full historical trigger is kept in a generated constant below.  A
    # second trigger cannot override the old abort, so replace it atomically.
    op.execute("DROP TRIGGER IF EXISTS trg_material_requests_update_guard_0029")
    op.execute(_sqlite_request_update_trigger_sql(upgrade=upgrade))


def _sqlite_request_update_trigger_sql(*, upgrade: bool) -> str:
    sql = SQLITE_REQUEST_UPDATE_TRIGGER_0069 if upgrade else SQLITE_REQUEST_UPDATE_TRIGGER_0029
    return sql


# Filled by the source generator below from 0029's exact trigger.  Keeping
# these constants in the migration makes SQLite deployments independent of
# importing an older Alembic module at runtime.
SQLITE_REQUEST_UPDATE_TRIGGER_0029 = """CREATE TRIGGER trg_material_requests_update_guard_0029
BEFORE UPDATE ON material_requests
WHEN json_valid(NEW.contact_snapshot_jsonb) <> 1
OR json_type(NEW.contact_snapshot_jsonb) <> 'object'
OR (SELECT count(*) FROM json_each(NEW.contact_snapshot_jsonb)) <> 9
OR json_type(NEW.contact_snapshot_jsonb, '$.schema') <> 'text'
OR json_extract(NEW.contact_snapshot_jsonb, '$.schema') <> 'rsc.material_request_contact.v1'
OR json_type(NEW.contact_snapshot_jsonb, '$.provider') <> 'text'
OR json_extract(NEW.contact_snapshot_jsonb, '$.provider') <> 'aliyun_kms'
OR json_type(NEW.contact_snapshot_jsonb, '$.kms_key_id') <> 'text'
OR length(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id')) NOT BETWEEN 3 AND 256
OR substr(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id'), 1, 1) GLOB '[^A-Za-z0-9]'
OR json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id') GLOB '*[^-A-Za-z0-9_./:@+]*'
OR lower(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id')) LIKE '%replace-with%'
OR lower(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id')) LIKE '%replace_me%'
OR lower(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id')) LIKE '%replace-me%'
OR lower(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id')) LIKE '%change-me%'
OR lower(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id')) LIKE '%changeme%'
OR json_type(NEW.contact_snapshot_jsonb, '$.key_version') <> 'integer'
OR json_extract(NEW.contact_snapshot_jsonb, '$.key_version') <= 0
OR json_type(NEW.contact_snapshot_jsonb, '$.ciphertext_b64') <> 'text'
OR length(json_extract(NEW.contact_snapshot_jsonb, '$.ciphertext_b64')) < 24
OR length(json_extract(NEW.contact_snapshot_jsonb, '$.ciphertext_b64')) % 4 <> 0
OR json_extract(NEW.contact_snapshot_jsonb, '$.ciphertext_b64') GLOB '*[^A-Za-z0-9+/=]*'
OR length(json_extract(NEW.contact_snapshot_jsonb, '$.ciphertext_b64')) - length(rtrim(json_extract(NEW.contact_snapshot_jsonb, '$.ciphertext_b64'), '=')) > 2
OR instr(rtrim(json_extract(NEW.contact_snapshot_jsonb, '$.ciphertext_b64'), '='), '=') > 0
OR json_type(NEW.contact_snapshot_jsonb, '$.nonce_b64') <> 'text'
OR length(json_extract(NEW.contact_snapshot_jsonb, '$.nonce_b64')) <> 16
OR json_extract(NEW.contact_snapshot_jsonb, '$.nonce_b64') GLOB '*[^A-Za-z0-9+/]*'
OR json_type(NEW.contact_snapshot_jsonb, '$.aad_sha256') <> 'text'
OR length(json_extract(NEW.contact_snapshot_jsonb, '$.aad_sha256')) <> 64
OR json_extract(NEW.contact_snapshot_jsonb, '$.aad_sha256') GLOB '*[^0-9a-f]*'
OR json_type(NEW.contact_snapshot_jsonb, '$.mobile_hmac') <> 'text'
OR substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 1, 5) <> 'hmac:'
OR instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6), ':') NOT BETWEEN 2 AND 11
OR substr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6, instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6), ':') - 1), 1, 1) GLOB '[^1-9]'
OR substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6, instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6), ':') - 1) GLOB '*[^0-9]*'
OR length(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6 + instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6), ':'))) <> 64
OR substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6 + instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6), ':')) GLOB '*[^0-9a-f]*'
OR json_type(NEW.contact_snapshot_jsonb, '$.contact_hmac') <> 'text'
OR substr(json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'), 1, 5) <> 'hmac:'
OR instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'), 6), ':') NOT BETWEEN 2 AND 11
OR substr(
    json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'),
    6,
    instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'), 6), ':') - 1
) GLOB '*[^0-9]*'
OR substr(
    substr(
        json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'),
        6,
        instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'), 6), ':') - 1
    ), 1, 1
) GLOB '[^1-9]'
OR length(substr(
    json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'),
    6 + instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'), 6), ':')
)) <> 64
OR substr(
    json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'),
    6 + instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'), 6), ':')
) GLOB '*[^0-9a-f]*'
  OR json_valid(NEW.address_masked_jsonb) <> 1
OR json_type(NEW.address_masked_jsonb) <> 'object'
OR (SELECT count(*) FROM json_each(NEW.address_masked_jsonb)) <> 5
OR json_type(NEW.address_masked_jsonb, '$.province_code') <> 'text'
OR json_type(NEW.address_masked_jsonb, '$.province_name') <> 'text'
OR json_type(NEW.address_masked_jsonb, '$.city_name') <> 'text'
OR json_type(NEW.address_masked_jsonb, '$.district_name') <> 'text'
OR json_type(NEW.address_masked_jsonb, '$.detail_masked') <> 'text'
OR length(json_extract(NEW.address_masked_jsonb, '$.province_code')) NOT BETWEEN 1 AND 12
OR length(json_extract(NEW.address_masked_jsonb, '$.province_name')) NOT BETWEEN 1 AND 80
OR length(json_extract(NEW.address_masked_jsonb, '$.city_name')) NOT BETWEEN 1 AND 80
OR length(json_extract(NEW.address_masked_jsonb, '$.district_name')) NOT BETWEEN 1 AND 80
OR length(json_extract(NEW.address_masked_jsonb, '$.detail_masked')) NOT BETWEEN 1 AND 500
OR json_extract(NEW.address_masked_jsonb, '$.province_code') <> trim(json_extract(NEW.address_masked_jsonb, '$.province_code'))
OR json_extract(NEW.address_masked_jsonb, '$.province_name') <> trim(json_extract(NEW.address_masked_jsonb, '$.province_name'))
OR json_extract(NEW.address_masked_jsonb, '$.city_name') <> trim(json_extract(NEW.address_masked_jsonb, '$.city_name'))
OR json_extract(NEW.address_masked_jsonb, '$.district_name') <> trim(json_extract(NEW.address_masked_jsonb, '$.district_name'))
OR json_extract(NEW.address_masked_jsonb, '$.detail_masked') <> trim(json_extract(NEW.address_masked_jsonb, '$.detail_masked'))
OR (instr(json_extract(NEW.address_masked_jsonb, '$.detail_masked'), '*') = 0 AND instr(json_extract(NEW.address_masked_jsonb, '$.detail_masked'), '＊') = 0 AND instr(json_extract(NEW.address_masked_jsonb, '$.detail_masked'), '•') = 0)
OR json_valid(NEW.contact_masked_jsonb) <> 1
OR json_type(NEW.contact_masked_jsonb) <> 'object'
OR (SELECT count(*) FROM json_each(NEW.contact_masked_jsonb)) <> 2
OR json_type(NEW.contact_masked_jsonb, '$.name_masked') <> 'text'
OR json_type(NEW.contact_masked_jsonb, '$.mobile_masked') <> 'text'
OR length(json_extract(NEW.contact_masked_jsonb, '$.name_masked')) NOT BETWEEN 1 AND 120
OR length(json_extract(NEW.contact_masked_jsonb, '$.mobile_masked')) NOT BETWEEN 1 AND 32
OR json_extract(NEW.contact_masked_jsonb, '$.name_masked') <> trim(json_extract(NEW.contact_masked_jsonb, '$.name_masked'))
OR json_extract(NEW.contact_masked_jsonb, '$.mobile_masked') <> trim(json_extract(NEW.contact_masked_jsonb, '$.mobile_masked'))
OR (instr(json_extract(NEW.contact_masked_jsonb, '$.name_masked'), '*') = 0 AND instr(json_extract(NEW.contact_masked_jsonb, '$.name_masked'), '＊') = 0 AND instr(json_extract(NEW.contact_masked_jsonb, '$.name_masked'), '•') = 0)
OR (instr(json_extract(NEW.contact_masked_jsonb, '$.mobile_masked'), '*') = 0 AND instr(json_extract(NEW.contact_masked_jsonb, '$.mobile_masked'), '＊') = 0 AND instr(json_extract(NEW.contact_masked_jsonb, '$.mobile_masked'), '•') = 0)
OR json_extract(NEW.contact_masked_jsonb, '$.mobile_masked') GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'
  OR NEW.id IS NOT OLD.id
  OR NEW.request_no IS NOT OLD.request_no
  OR NEW.requester_user_id IS NOT OLD.requester_user_id
  OR NEW.requester_person_id IS NOT OLD.requester_person_id
  OR NEW.requester_org_id IS NOT OLD.requester_org_id
  OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
  OR NEW.created_at IS NOT OLD.created_at
  OR (OLD.submitted_at IS NOT NULL AND NEW.submitted_at IS NOT OLD.submitted_at)
  OR (OLD.submitted_at IS NOT NULL AND NEW.approval_mode IS NOT OLD.approval_mode)
  OR (NEW.revision_no IS NOT OLD.revision_no AND NOT (
      OLD.status = 'returned'
      AND NEW.status = 'returned'
      AND NEW.revision_no = OLD.revision_no + 1
      AND EXISTS (
          SELECT 1 FROM material_request_revisions AS revision
           WHERE revision.request_id = NEW.id
             AND revision.revision_no = NEW.revision_no
             AND revision.status = 'draft'
             AND revision.work_order_id IS NEW.work_order_id
             AND revision.purpose = NEW.purpose
             AND revision.urgency = NEW.urgency
             AND revision.expected_date IS NEW.expected_date
             AND revision.address_snapshot_jsonb = NEW.address_snapshot_jsonb
             AND revision.address_masked_jsonb = NEW.address_masked_jsonb
             AND revision.contact_snapshot_jsonb = NEW.contact_snapshot_jsonb
             AND revision.contact_masked_jsonb = NEW.contact_masked_jsonb
             AND revision.note = NEW.note
             AND revision.approval_mode = NEW.approval_mode
      )
  ))
  OR ((NEW.work_order_id IS NOT OLD.work_order_id
       OR NEW.purpose IS NOT OLD.purpose
       OR NEW.urgency IS NOT OLD.urgency
       OR NEW.expected_date IS NOT OLD.expected_date
       OR NEW.address_snapshot_jsonb IS NOT OLD.address_snapshot_jsonb
       OR NEW.address_masked_jsonb IS NOT OLD.address_masked_jsonb
       OR NEW.contact_snapshot_jsonb IS NOT OLD.contact_snapshot_jsonb
       OR NEW.contact_masked_jsonb IS NOT OLD.contact_masked_jsonb
       OR NEW.note IS NOT OLD.note)
      AND NOT EXISTS (
          SELECT 1 FROM material_request_revisions AS revision
           WHERE revision.request_id = NEW.id
             AND revision.revision_no = NEW.revision_no
             AND revision.status = 'draft'
             AND revision.work_order_id IS NEW.work_order_id
             AND revision.purpose = NEW.purpose
             AND revision.urgency = NEW.urgency
             AND revision.expected_date IS NEW.expected_date
             AND revision.address_snapshot_jsonb = NEW.address_snapshot_jsonb
             AND revision.address_masked_jsonb = NEW.address_masked_jsonb
             AND revision.contact_snapshot_jsonb = NEW.contact_snapshot_jsonb
             AND revision.contact_masked_jsonb = NEW.contact_masked_jsonb
             AND revision.note = NEW.note
             AND revision.approval_mode = NEW.approval_mode
      ))
  OR (OLD.status IN ('draft', 'returned')
      AND NEW.status IN ('submitted', 'approval_in_progress')
      AND NOT EXISTS (
          SELECT 1 FROM material_request_revisions AS revision
           WHERE revision.request_id = NEW.id
             AND revision.revision_no = NEW.revision_no
             AND revision.status = 'sealed'
      ))
  OR NEW.allocation_status <> 'not_allocated'
  OR NEW.reservation_status <> 'not_reserved'
  OR NEW.outbound_status <> 'not_started'
  OR NEW.shipment_status <> 'not_started'
  OR NEW.logistics_signature_status <> 'not_signed'
  OR NEW.oam_receipt_status <> 'not_occurred'
  OR NEW.personal_inbound_status <> 'not_started'
  OR NEW.notification_status <> 'not_started'
  OR NEW.reconciliation_status <> 'not_started'
BEGIN
    SELECT RAISE(ABORT, 'formal material-request invariant violated');
END"""

SQLITE_REQUEST_UPDATE_TRIGGER_0069 = """CREATE TRIGGER trg_material_requests_update_guard_0029
BEFORE UPDATE ON material_requests
WHEN json_valid(NEW.contact_snapshot_jsonb) <> 1
OR json_type(NEW.contact_snapshot_jsonb) <> 'object'
OR (SELECT count(*) FROM json_each(NEW.contact_snapshot_jsonb)) <> 9
OR json_type(NEW.contact_snapshot_jsonb, '$.schema') <> 'text'
OR json_extract(NEW.contact_snapshot_jsonb, '$.schema') <> 'rsc.material_request_contact.v1'
OR json_type(NEW.contact_snapshot_jsonb, '$.provider') <> 'text'
OR json_extract(NEW.contact_snapshot_jsonb, '$.provider') <> 'aliyun_kms'
OR json_type(NEW.contact_snapshot_jsonb, '$.kms_key_id') <> 'text'
OR length(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id')) NOT BETWEEN 3 AND 256
OR substr(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id'), 1, 1) GLOB '[^A-Za-z0-9]'
OR json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id') GLOB '*[^-A-Za-z0-9_./:@+]*'
OR lower(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id')) LIKE '%replace-with%'
OR lower(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id')) LIKE '%replace_me%'
OR lower(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id')) LIKE '%replace-me%'
OR lower(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id')) LIKE '%change-me%'
OR lower(json_extract(NEW.contact_snapshot_jsonb, '$.kms_key_id')) LIKE '%changeme%'
OR json_type(NEW.contact_snapshot_jsonb, '$.key_version') <> 'integer'
OR json_extract(NEW.contact_snapshot_jsonb, '$.key_version') <= 0
OR json_type(NEW.contact_snapshot_jsonb, '$.ciphertext_b64') <> 'text'
OR length(json_extract(NEW.contact_snapshot_jsonb, '$.ciphertext_b64')) < 24
OR length(json_extract(NEW.contact_snapshot_jsonb, '$.ciphertext_b64')) % 4 <> 0
OR json_extract(NEW.contact_snapshot_jsonb, '$.ciphertext_b64') GLOB '*[^A-Za-z0-9+/=]*'
OR length(json_extract(NEW.contact_snapshot_jsonb, '$.ciphertext_b64')) - length(rtrim(json_extract(NEW.contact_snapshot_jsonb, '$.ciphertext_b64'), '=')) > 2
OR instr(rtrim(json_extract(NEW.contact_snapshot_jsonb, '$.ciphertext_b64'), '='), '=') > 0
OR json_type(NEW.contact_snapshot_jsonb, '$.nonce_b64') <> 'text'
OR length(json_extract(NEW.contact_snapshot_jsonb, '$.nonce_b64')) <> 16
OR json_extract(NEW.contact_snapshot_jsonb, '$.nonce_b64') GLOB '*[^A-Za-z0-9+/]*'
OR json_type(NEW.contact_snapshot_jsonb, '$.aad_sha256') <> 'text'
OR length(json_extract(NEW.contact_snapshot_jsonb, '$.aad_sha256')) <> 64
OR json_extract(NEW.contact_snapshot_jsonb, '$.aad_sha256') GLOB '*[^0-9a-f]*'
OR json_type(NEW.contact_snapshot_jsonb, '$.mobile_hmac') <> 'text'
OR substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 1, 5) <> 'hmac:'
OR instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6), ':') NOT BETWEEN 2 AND 11
OR substr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6, instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6), ':') - 1), 1, 1) GLOB '[^1-9]'
OR substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6, instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6), ':') - 1) GLOB '*[^0-9]*'
OR length(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6 + instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6), ':'))) <> 64
OR substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6 + instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.mobile_hmac'), 6), ':')) GLOB '*[^0-9a-f]*'
OR json_type(NEW.contact_snapshot_jsonb, '$.contact_hmac') <> 'text'
OR substr(json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'), 1, 5) <> 'hmac:'
OR instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'), 6), ':') NOT BETWEEN 2 AND 11
OR substr(
    json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'),
    6,
    instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'), 6), ':') - 1
) GLOB '*[^0-9]*'
OR substr(
    substr(
        json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'),
        6,
        instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'), 6), ':') - 1
    ), 1, 1
) GLOB '[^1-9]'
OR length(substr(
    json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'),
    6 + instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'), 6), ':')
)) <> 64
OR substr(
    json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'),
    6 + instr(substr(json_extract(NEW.contact_snapshot_jsonb, '$.contact_hmac'), 6), ':')
) GLOB '*[^0-9a-f]*'
  OR json_valid(NEW.address_masked_jsonb) <> 1
OR json_type(NEW.address_masked_jsonb) <> 'object'
OR (SELECT count(*) FROM json_each(NEW.address_masked_jsonb)) <> 5
OR json_type(NEW.address_masked_jsonb, '$.province_code') <> 'text'
OR json_type(NEW.address_masked_jsonb, '$.province_name') <> 'text'
OR json_type(NEW.address_masked_jsonb, '$.city_name') <> 'text'
OR json_type(NEW.address_masked_jsonb, '$.district_name') <> 'text'
OR json_type(NEW.address_masked_jsonb, '$.detail_masked') <> 'text'
OR length(json_extract(NEW.address_masked_jsonb, '$.province_code')) NOT BETWEEN 1 AND 12
OR length(json_extract(NEW.address_masked_jsonb, '$.province_name')) NOT BETWEEN 1 AND 80
OR length(json_extract(NEW.address_masked_jsonb, '$.city_name')) NOT BETWEEN 1 AND 80
OR length(json_extract(NEW.address_masked_jsonb, '$.district_name')) NOT BETWEEN 1 AND 80
OR length(json_extract(NEW.address_masked_jsonb, '$.detail_masked')) NOT BETWEEN 1 AND 500
OR json_extract(NEW.address_masked_jsonb, '$.province_code') <> trim(json_extract(NEW.address_masked_jsonb, '$.province_code'))
OR json_extract(NEW.address_masked_jsonb, '$.province_name') <> trim(json_extract(NEW.address_masked_jsonb, '$.province_name'))
OR json_extract(NEW.address_masked_jsonb, '$.city_name') <> trim(json_extract(NEW.address_masked_jsonb, '$.city_name'))
OR json_extract(NEW.address_masked_jsonb, '$.district_name') <> trim(json_extract(NEW.address_masked_jsonb, '$.district_name'))
OR json_extract(NEW.address_masked_jsonb, '$.detail_masked') <> trim(json_extract(NEW.address_masked_jsonb, '$.detail_masked'))
OR (instr(json_extract(NEW.address_masked_jsonb, '$.detail_masked'), '*') = 0 AND instr(json_extract(NEW.address_masked_jsonb, '$.detail_masked'), '＊') = 0 AND instr(json_extract(NEW.address_masked_jsonb, '$.detail_masked'), '•') = 0)
OR json_valid(NEW.contact_masked_jsonb) <> 1
OR json_type(NEW.contact_masked_jsonb) <> 'object'
OR (SELECT count(*) FROM json_each(NEW.contact_masked_jsonb)) <> 2
OR json_type(NEW.contact_masked_jsonb, '$.name_masked') <> 'text'
OR json_type(NEW.contact_masked_jsonb, '$.mobile_masked') <> 'text'
OR length(json_extract(NEW.contact_masked_jsonb, '$.name_masked')) NOT BETWEEN 1 AND 120
OR length(json_extract(NEW.contact_masked_jsonb, '$.mobile_masked')) NOT BETWEEN 1 AND 32
OR json_extract(NEW.contact_masked_jsonb, '$.name_masked') <> trim(json_extract(NEW.contact_masked_jsonb, '$.name_masked'))
OR json_extract(NEW.contact_masked_jsonb, '$.mobile_masked') <> trim(json_extract(NEW.contact_masked_jsonb, '$.mobile_masked'))
OR (instr(json_extract(NEW.contact_masked_jsonb, '$.name_masked'), '*') = 0 AND instr(json_extract(NEW.contact_masked_jsonb, '$.name_masked'), '＊') = 0 AND instr(json_extract(NEW.contact_masked_jsonb, '$.name_masked'), '•') = 0)
OR (instr(json_extract(NEW.contact_masked_jsonb, '$.mobile_masked'), '*') = 0 AND instr(json_extract(NEW.contact_masked_jsonb, '$.mobile_masked'), '＊') = 0 AND instr(json_extract(NEW.contact_masked_jsonb, '$.mobile_masked'), '•') = 0)
OR json_extract(NEW.contact_masked_jsonb, '$.mobile_masked') GLOB '*[0-9][0-9][0-9][0-9][0-9][0-9][0-9]*'
  OR NEW.id IS NOT OLD.id
  OR NEW.request_no IS NOT OLD.request_no
  OR NEW.requester_user_id IS NOT OLD.requester_user_id
  OR NEW.requester_person_id IS NOT OLD.requester_person_id
  OR NEW.requester_org_id IS NOT OLD.requester_org_id
  OR NEW.created_by_user_id IS NOT OLD.created_by_user_id
  OR NEW.created_at IS NOT OLD.created_at
  OR (OLD.submitted_at IS NOT NULL AND NEW.submitted_at IS NOT OLD.submitted_at)
  OR (OLD.submitted_at IS NOT NULL AND NEW.approval_mode IS NOT OLD.approval_mode)
  OR (NEW.revision_no IS NOT OLD.revision_no AND NOT (
      OLD.status = 'returned'
      AND NEW.status = 'returned'
      AND NEW.revision_no = OLD.revision_no + 1
      AND EXISTS (
          SELECT 1 FROM material_request_revisions AS revision
           WHERE revision.request_id = NEW.id
             AND revision.revision_no = NEW.revision_no
             AND revision.status = 'draft'
             AND revision.work_order_id IS NEW.work_order_id
             AND revision.purpose = NEW.purpose
             AND revision.urgency = NEW.urgency
             AND revision.expected_date IS NEW.expected_date
             AND revision.address_snapshot_jsonb = NEW.address_snapshot_jsonb
             AND revision.address_masked_jsonb = NEW.address_masked_jsonb
             AND revision.contact_snapshot_jsonb = NEW.contact_snapshot_jsonb
             AND revision.contact_masked_jsonb = NEW.contact_masked_jsonb
             AND revision.note = NEW.note
             AND revision.approval_mode = NEW.approval_mode
      )
  ))
  OR ((NEW.work_order_id IS NOT OLD.work_order_id
       OR NEW.purpose IS NOT OLD.purpose
       OR NEW.urgency IS NOT OLD.urgency
       OR NEW.expected_date IS NOT OLD.expected_date
       OR NEW.address_snapshot_jsonb IS NOT OLD.address_snapshot_jsonb
       OR NEW.address_masked_jsonb IS NOT OLD.address_masked_jsonb
       OR NEW.contact_snapshot_jsonb IS NOT OLD.contact_snapshot_jsonb
       OR NEW.contact_masked_jsonb IS NOT OLD.contact_masked_jsonb
       OR NEW.note IS NOT OLD.note)
      AND NOT EXISTS (
          SELECT 1 FROM material_request_revisions AS revision
           WHERE revision.request_id = NEW.id
             AND revision.revision_no = NEW.revision_no
             AND revision.status = 'draft'
             AND revision.work_order_id IS NEW.work_order_id
             AND revision.purpose = NEW.purpose
             AND revision.urgency = NEW.urgency
             AND revision.expected_date IS NEW.expected_date
             AND revision.address_snapshot_jsonb = NEW.address_snapshot_jsonb
             AND revision.address_masked_jsonb = NEW.address_masked_jsonb
             AND revision.contact_snapshot_jsonb = NEW.contact_snapshot_jsonb
             AND revision.contact_masked_jsonb = NEW.contact_masked_jsonb
             AND revision.note = NEW.note
             AND revision.approval_mode = NEW.approval_mode
      ))
  OR (OLD.status IN ('draft', 'returned')
      AND NEW.status IN ('submitted', 'approval_in_progress')
      AND NOT EXISTS (
          SELECT 1 FROM material_request_revisions AS revision
           WHERE revision.request_id = NEW.id
             AND revision.revision_no = NEW.revision_no
             AND revision.status = 'sealed'
      ))
  OR NEW.outbound_status <> 'not_started'
  OR NEW.shipment_status <> 'not_started'
  OR NEW.logistics_signature_status <> 'not_signed'
  OR NEW.oam_receipt_status <> 'not_occurred'
  OR NEW.personal_inbound_status <> 'not_started'
  OR NEW.notification_status <> 'not_started'
  OR NEW.reconciliation_status <> 'not_started'
  OR (NEW.allocation_status IS NOT OLD.allocation_status
      AND NEW.reservation_status IS NOT OLD.reservation_status)
  OR (NEW.allocation_status IS NOT OLD.allocation_status AND NOT (
      OLD.allocation_status IN ('not_allocated', 'partially_allocated')
      AND NEW.allocation_status IN ('partially_allocated', 'allocated')
      AND NEW.version = OLD.version + 1
      AND NEW.updated_at > OLD.updated_at
      AND EXISTS (
          SELECT 1
            FROM stock_allocations AS allocation
            JOIN material_request_commands AS command
              ON lower(replace(command.request_id, '-', '')) =
                 lower(replace(allocation.request_id, '-', ''))
             AND command.operation = 'allocate'
             AND command.target_version = NEW.version
           WHERE lower(replace(allocation.request_id, '-', '')) =
                 lower(replace(NEW.id, '-', ''))
             AND allocation.request_version = NEW.version
             AND allocation.status = 'allocated'
             AND lower(replace(json_extract(command.request_jsonb, '$.request_id'), '-', '')) =
                 lower(replace(NEW.id, '-', ''))
             AND json_extract(command.request_jsonb, '$.target_version') = NEW.version
      )
  ))
  OR (NEW.reservation_status IS NOT OLD.reservation_status AND NOT (
      OLD.reservation_status IN ('not_reserved', 'pending')
      AND NEW.reservation_status IN ('pending', 'reserved')
      AND NEW.version = OLD.version + 1
      AND NEW.updated_at > OLD.updated_at
      AND EXISTS (
          SELECT 1
            FROM stock_reservations AS reservation
            JOIN material_request_commands AS command
              ON lower(replace(command.request_id, '-', '')) =
                 lower(replace(reservation.request_id, '-', ''))
             AND command.operation = 'reserve'
             AND command.target_version = NEW.version
           WHERE lower(replace(reservation.request_id, '-', '')) =
                 lower(replace(NEW.id, '-', ''))
             AND reservation.request_version = NEW.version
             AND reservation.status = 'reserved'
             AND reservation.released_qty = 0
             AND reservation.release_transaction_id IS NULL
             AND lower(replace(json_extract(command.request_jsonb, '$.request_id'), '-', '')) =
                 lower(replace(NEW.id, '-', ''))
             AND json_extract(command.request_jsonb, '$.target_version') = NEW.version
      )
  ))
  OR (NEW.reservation_status = 'reserved' AND EXISTS (
      SELECT 1
        FROM material_request_lines AS line
       WHERE lower(replace(line.request_id, '-', '')) = lower(replace(NEW.id, '-', ''))
         AND line.final_approved_qty > line.cancelled_qty
         AND COALESCE((
             SELECT sum(reservation.reserved_qty)
               FROM stock_reservations AS reservation
              WHERE lower(replace(reservation.request_line_id, '-', '')) =
                    lower(replace(line.id, '-', ''))
                AND reservation.status = 'reserved'
                AND reservation.released_qty = 0
         ), 0) < line.final_approved_qty - line.cancelled_qty
  ))
BEGIN
    SELECT RAISE(ABORT, 'formal material-request invariant violated');
END"""

__all__ = [
    "APPROVAL_PROJECTION_BODY_SHA256_0059",
    "APPROVAL_PROJECTION_BODY_SHA256_0069",
    "CATALOG_ERROR",
    "COMMAND_OPERATION_CHECK_NEW",
    "REQUEST_GUARD_BODY_SHA256_0029",
    "REQUEST_GUARD_BODY_SHA256_0069",
    "RUNTIME_READY_BODY_SHA256_0068",
    "RUNTIME_READY_BODY_SHA256_0069",
    "RUNTIME_READY_PREVIOUS_REVISION",
    "RESERVATION_TABLES",
    "SUPPLY_DISPATCH_BODY_SHA256_0060",
    "SUPPLY_DISPATCH_BODY_SHA256_0069",
    "SUPPLY_VALIDATE_BODY_SHA256_0061",
    "SUPPLY_VALIDATE_BODY_SHA256_0069",
    "TABLES",
    "revision",
    "down_revision",
]
