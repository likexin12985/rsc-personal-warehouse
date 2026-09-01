"""Add the empty V1.0 inventory master-data and immutable-ledger foundation.

Revision ID: 20260830_0009
Revises: 20260830_0008
Create Date: 2026-08-30

The v0.9 material table is renamed in place so all legacy foreign keys and
rows remain attached to it.  No legacy material, balance, transfer, stocktake,
work-order or OAM control quantity is copied into the formal inventory ledger.
"""

from datetime import datetime, timezone
from typing import Sequence, Union
import uuid

from alembic import context, op
import sqlalchemy as sa


revision: str = "20260830_0009"
down_revision: Union[str, Sequence[str], None] = "20260830_0008"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


ADMIN_ROLE_ID = uuid.UUID("10000000-0000-4000-8000-000000000001")
PROVINCIAL_MANAGER_ROLE_ID = uuid.UUID(
    "10000000-0000-4000-8000-000000000002"
)
INVENTORY_POST_PERMISSION_ID = uuid.UUID(
    "20000000-0000-4000-8000-000000000015"
)
INVENTORY_REVERSE_PERMISSION_ID = uuid.UUID(
    "20000000-0000-4000-8000-000000000016"
)
ADMIN_INVENTORY_POST_ROLE_PERMISSION_ID = uuid.UUID(
    "21000000-0000-4000-8000-000000000029"
)
PROVINCIAL_INVENTORY_POST_ROLE_PERMISSION_ID = uuid.UUID(
    "21000000-0000-4000-8000-000000000030"
)
ADMIN_INVENTORY_REVERSE_ROLE_PERMISSION_ID = uuid.UUID(
    "21000000-0000-4000-8000-000000000031"
)
INVENTORY_AUDIT_CHAIN_HEAD_ID = uuid.UUID(
    "30000000-0000-4000-8000-000000000003"
)
INVENTORY_LEDGER_HEAD_ID = uuid.UUID(
    "40000000-0000-4000-8000-000000000001"
)

IMMUTABLE_TABLES = (
    "inventory_transactions",
    "inventory_movements",
    "inventory_movement_serials",
)

INVENTORY_BUSINESS_TABLES = (
    "serial_current_positions",
    "stock_balances",
    "inventory_movement_serials",
    "inventory_movements",
    "inventory_transactions",
    "stock_accounts",
    "qr_codes",
    "inventory_serials",
    "inventory_lots",
    "custody_assignments",
    "stock_locations",
    "material_inventory_policies",
    "materials",
)


def upgrade() -> None:
    # Renaming, rather than interpreting, is the safety boundary. PostgreSQL and
    # supported SQLite both preserve the existing foreign-key targets.
    op.rename_table("materials", "legacy_v09_materials")
    if op.get_bind().dialect.name == "postgresql":
        # PostgreSQL keeps an implicitly named primary-key index unchanged when
        # its table is renamed. Free the schema-global ``materials_pkey`` name
        # before the new formal table is created.
        op.execute(
            "ALTER TABLE legacy_v09_materials RENAME CONSTRAINT "
            "materials_pkey TO legacy_v09_materials_pkey"
        )

    op.create_table(
        "materials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("external_object_id", sa.Uuid(), nullable=False),
        sa.Column("sku_code", sa.String(length=80), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("specification", sa.String(length=300), nullable=False),
        sa.Column("base_unit", sa.String(length=32), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("source_updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_materials_status"
        ),
        sa.ForeignKeyConstraint(["external_object_id"], ["external_objects.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "external_object_id", name="uq_materials_external_object_id"
        ),
        sa.UniqueConstraint("sku_code", name="uq_materials_sku_code"),
    )
    op.create_index("ix_formal_materials_name", "materials", ["name"])
    op.create_index("ix_formal_materials_status", "materials", ["status"])

    op.create_table(
        "material_inventory_policies",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("tracking_mode", sa.String(length=24), nullable=False),
        sa.Column("quantity_scale", sa.Integer(), nullable=False),
        sa.Column("allow_fraction", sa.Boolean(), nullable=False),
        sa.Column("effective_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("effective_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "tracking_mode IN ('none', 'lot', 'serial', 'lot_and_serial')",
            name="ck_material_inventory_policies_tracking_mode",
        ),
        sa.CheckConstraint(
            "quantity_scale BETWEEN 0 AND 3",
            name="ck_material_inventory_policies_quantity_scale",
        ),
        sa.CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_material_inventory_policies_validity",
        ),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "material_id",
            "effective_from",
            name="uq_material_inventory_policies_start",
        ),
    )
    op.create_index(
        "ix_material_inventory_policies_material",
        "material_inventory_policies",
        ["material_id"],
    )
    op.create_index(
        "uq_material_inventory_policies_current",
        "material_inventory_policies",
        ["material_id"],
        unique=True,
        postgresql_where=sa.text("effective_to IS NULL"),
        sqlite_where=sa.text("effective_to IS NULL"),
    )

    op.create_table(
        "stock_locations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=100), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("location_type", sa.String(length=24), nullable=False),
        sa.Column("owner_org_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("custodian_person_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "location_type IN ('headquarters', 'region', 'personal', "
            "'transit', 'quarantine')",
            name="ck_stock_locations_type",
        ),
        sa.CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_stock_locations_status"
        ),
        sa.CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_stock_locations_parent",
        ),
        sa.CheckConstraint(
            "location_type <> 'personal' OR "
            "(parent_id IS NOT NULL AND custodian_person_id IS NOT NULL)",
            name="ck_stock_locations_personal_binding",
        ),
        sa.ForeignKeyConstraint(["owner_org_id"], ["organizations.id"]),
        sa.ForeignKeyConstraint(["parent_id"], ["stock_locations.id"]),
        sa.ForeignKeyConstraint(["custodian_person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_stock_locations_code"),
    )
    op.create_index(
        "ix_stock_locations_owner_org", "stock_locations", ["owner_org_id"]
    )
    op.create_index("ix_stock_locations_parent", "stock_locations", ["parent_id"])
    op.create_index(
        "ix_stock_locations_custodian",
        "stock_locations",
        ["custodian_person_id"],
    )
    op.create_index(
        "ix_stock_locations_type_status",
        "stock_locations",
        ["location_type", "status"],
    )

    op.create_table(
        "custody_assignments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("custodian_person_id", sa.Uuid(), nullable=False),
        sa.Column("valid_from", sa.DateTime(timezone=True), nullable=False),
        sa.Column("valid_to", sa.DateTime(timezone=True), nullable=True),
        sa.Column("handover_case_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_custody_assignments_validity",
        ),
        sa.ForeignKeyConstraint(["location_id"], ["stock_locations.id"]),
        sa.ForeignKeyConstraint(["custodian_person_id"], ["people.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "location_id", "valid_from", name="uq_custody_assignments_start"
        ),
    )
    op.create_index(
        "ix_custody_assignments_custodian",
        "custody_assignments",
        ["custodian_person_id"],
    )
    op.create_index(
        "uq_custody_assignments_current_location",
        "custody_assignments",
        ["location_id"],
        unique=True,
        postgresql_where=sa.text("valid_to IS NULL"),
        sqlite_where=sa.text("valid_to IS NULL"),
    )

    op.create_table(
        "inventory_lots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("lot_no", sa.String(length=160), nullable=False),
        sa.Column("manufacture_date", sa.Date(), nullable=True),
        sa.Column("expiry_date", sa.Date(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "expiry_date IS NULL OR manufacture_date IS NULL OR "
            "expiry_date >= manufacture_date",
            name="ck_inventory_lots_date_order",
        ),
        sa.ForeignKeyConstraint(["material_id"], ["materials.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "material_id", "lot_no", name="uq_inventory_lots_material_no"
        ),
        sa.UniqueConstraint(
            "id", "material_id", name="uq_inventory_lots_id_material"
        ),
    )
    op.create_index(
        "ix_inventory_lots_expiry_date", "inventory_lots", ["expiry_date"]
    )

    op.create_table(
        "inventory_serials",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("serial_no", sa.String(length=200), nullable=False),
        sa.Column("qr_code", sa.String(length=250), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("lifecycle_status", sa.String(length=24), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "lifecycle_status IN ('active', 'consumed', 'returned', "
            "'scrapped', 'lost')",
            name="ck_inventory_serials_lifecycle",
        ),
        sa.ForeignKeyConstraint(
            ["lot_id", "material_id"],
            ["inventory_lots.id", "inventory_lots.material_id"],
            name="fk_inventory_serials_lot_material",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "material_id", "serial_no", name="uq_inventory_serials_material_no"
        ),
        sa.UniqueConstraint("qr_code", name="uq_inventory_serials_qr_code"),
    )
    op.create_index("ix_inventory_serials_lot", "inventory_serials", ["lot_id"])
    op.create_index(
        "ix_inventory_serials_status", "inventory_serials", ["lifecycle_status"]
    )

    op.create_table(
        "qr_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(length=250), nullable=False),
        sa.Column("object_type", sa.String(length=24), nullable=False),
        sa.Column("object_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("printed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "object_type IN ('location', 'material', 'lot', 'serial')",
            name="ck_qr_codes_object_type",
        ),
        sa.CheckConstraint("status IN ('active', 'void')", name="ck_qr_codes_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("code", name="uq_qr_codes_code"),
        sa.UniqueConstraint(
            "object_type", "object_id", name="uq_qr_codes_object"
        ),
    )
    op.create_index("ix_qr_codes_status", "qr_codes", ["status"])

    op.create_table(
        "inventory_ledger_heads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("stream_key", sa.String(length=40), nullable=False),
        sa.Column("next_cursor", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "stream_key = 'inventory'", name="ck_inventory_ledger_heads_stream"
        ),
        sa.CheckConstraint(
            "next_cursor > 0", name="ck_inventory_ledger_heads_next_cursor"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "stream_key", name="uq_inventory_ledger_heads_stream"
        ),
    )

    op.create_table(
        "stock_accounts",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_org_id", sa.Uuid(), nullable=False),
        sa.Column("custodian_person_id", sa.Uuid(), nullable=True),
        sa.Column("location_id", sa.Uuid(), nullable=False),
        sa.Column("material_id", sa.Uuid(), nullable=False),
        sa.Column("condition_code", sa.String(length=20), nullable=False),
        sa.Column("availability_bucket", sa.String(length=24), nullable=False),
        sa.Column("lot_id", sa.Uuid(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "condition_code IN ('new', 'used', 'damaged', 'scrapped')",
            name="ck_stock_accounts_condition",
        ),
        sa.CheckConstraint(
            "availability_bucket IN ('available', 'reserved', 'picking', "
            "'outbound', 'in_transit', 'arrived_pending', 'frozen', "
            "'return_pending', 'scrap_pending')",
            name="ck_stock_accounts_availability",
        ),
        sa.ForeignKeyConstraint(
            ["owner_org_id"], ["organizations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["custodian_person_id"], ["people.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["location_id"], ["stock_locations.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["material_id"], ["materials.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["lot_id", "material_id"],
            ["inventory_lots.id", "inventory_lots.material_id"],
            name="fk_stock_accounts_lot_material",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_stock_accounts_location_material",
        "stock_accounts",
        ["location_id", "material_id"],
    )
    op.create_index(
        "uq_stock_accounts_custodian_lot",
        "stock_accounts",
        [
            "owner_org_id",
            "custodian_person_id",
            "location_id",
            "material_id",
            "condition_code",
            "availability_bucket",
            "lot_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "custodian_person_id IS NOT NULL AND lot_id IS NOT NULL"
        ),
        sqlite_where=sa.text(
            "custodian_person_id IS NOT NULL AND lot_id IS NOT NULL"
        ),
    )
    op.create_index(
        "uq_stock_accounts_no_custodian_with_lot",
        "stock_accounts",
        [
            "owner_org_id",
            "location_id",
            "material_id",
            "condition_code",
            "availability_bucket",
            "lot_id",
        ],
        unique=True,
        postgresql_where=sa.text(
            "custodian_person_id IS NULL AND lot_id IS NOT NULL"
        ),
        sqlite_where=sa.text("custodian_person_id IS NULL AND lot_id IS NOT NULL"),
    )
    op.create_index(
        "uq_stock_accounts_with_custodian_no_lot",
        "stock_accounts",
        [
            "owner_org_id",
            "custodian_person_id",
            "location_id",
            "material_id",
            "condition_code",
            "availability_bucket",
        ],
        unique=True,
        postgresql_where=sa.text(
            "custodian_person_id IS NOT NULL AND lot_id IS NULL"
        ),
        sqlite_where=sa.text("custodian_person_id IS NOT NULL AND lot_id IS NULL"),
    )
    op.create_index(
        "uq_stock_accounts_no_custodian_no_lot",
        "stock_accounts",
        [
            "owner_org_id",
            "location_id",
            "material_id",
            "condition_code",
            "availability_bucket",
        ],
        unique=True,
        postgresql_where=sa.text(
            "custodian_person_id IS NULL AND lot_id IS NULL"
        ),
        sqlite_where=sa.text("custodian_person_id IS NULL AND lot_id IS NULL"),
    )

    op.create_table(
        "inventory_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("transaction_no", sa.String(length=100), nullable=False),
        sa.Column("movement_type", sa.String(length=32), nullable=False),
        sa.Column("source_document_type", sa.String(length=80), nullable=False),
        sa.Column("source_document_id", sa.String(length=80), nullable=False),
        sa.Column("posting_key", sa.String(length=200), nullable=False),
        sa.Column("idempotency_key_hash", sa.String(length=64), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("posted_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ledger_cursor", sa.BigInteger(), nullable=False),
        sa.Column("reversed_transaction_id", sa.Uuid(), nullable=True),
        sa.Column("actor_user_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "movement_type IN ('opening', 'transfer', 'reserve', 'release', "
            "'pick', 'outbound', 'transit', 'inbound', 'freeze', 'unfreeze', "
            "'consume', 'return', 'scrap', 'stocktake_gain', "
            "'stocktake_loss', 'status_change', 'reversal')",
            name="ck_inventory_transactions_movement_type",
        ),
        sa.CheckConstraint(
            "status = 'posted'", name="ck_inventory_transactions_status"
        ),
        sa.CheckConstraint(
            "ledger_cursor > 0", name="ck_inventory_transactions_ledger_cursor"
        ),
        sa.CheckConstraint(
            "length(idempotency_key_hash) = 64 AND length(request_hash) = 64",
            name="ck_inventory_transactions_hashes",
        ),
        sa.CheckConstraint(
            "length(posting_key) > 0 AND length(source_document_type) > 0 "
            "AND length(source_document_id) > 0",
            name="ck_inventory_transactions_business_keys",
        ),
        sa.CheckConstraint(
            "(movement_type = 'reversal' AND reversed_transaction_id IS NOT NULL) "
            "OR (movement_type <> 'reversal' AND reversed_transaction_id IS NULL)",
            name="ck_inventory_transactions_reversal_binding",
        ),
        sa.CheckConstraint(
            "reversed_transaction_id IS NULL OR reversed_transaction_id <> id",
            name="ck_inventory_transactions_not_self_reversal",
        ),
        sa.ForeignKeyConstraint(
            ["reversed_transaction_id"], ["inventory_transactions.id"]
        ),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transaction_no", name="uq_inventory_transactions_number"
        ),
        sa.UniqueConstraint(
            "posting_key", name="uq_inventory_transactions_posting_key"
        ),
        sa.UniqueConstraint(
            "idempotency_key_hash",
            name="uq_inventory_transactions_idempotency_hash",
        ),
        sa.UniqueConstraint(
            "ledger_cursor", name="uq_inventory_transactions_ledger_cursor"
        ),
        sa.UniqueConstraint(
            "reversed_transaction_id",
            name="uq_inventory_transactions_reversed_transaction",
        ),
    )
    op.create_index(
        "ix_inventory_transactions_source_document",
        "inventory_transactions",
        ["source_document_type", "source_document_id"],
    )
    op.create_index(
        "ix_inventory_transactions_posted_at", "inventory_transactions", ["posted_at"]
    )
    op.create_index(
        "ix_inventory_transactions_actor", "inventory_transactions", ["actor_user_id"]
    )

    op.create_table(
        "inventory_movements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("line_no", sa.Integer(), nullable=False),
        sa.Column("from_account_id", sa.Uuid(), nullable=True),
        sa.Column("to_account_id", sa.Uuid(), nullable=True),
        sa.Column("external_boundary_code", sa.String(length=100), nullable=True),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "line_no > 0", name="ck_inventory_movements_line_positive"
        ),
        sa.CheckConstraint("quantity > 0", name="ck_inventory_movements_quantity"),
        sa.CheckConstraint(
            "from_account_id IS NOT NULL OR to_account_id IS NOT NULL",
            name="ck_inventory_movements_has_endpoint",
        ),
        sa.CheckConstraint(
            "from_account_id IS NULL OR to_account_id IS NULL "
            "OR from_account_id <> to_account_id",
            name="ck_inventory_movements_distinct_accounts",
        ),
        sa.CheckConstraint(
            "(from_account_id IS NOT NULL AND to_account_id IS NOT NULL "
            "AND external_boundary_code IS NULL) OR "
            "(((from_account_id IS NULL AND to_account_id IS NOT NULL) OR "
            "(from_account_id IS NOT NULL AND to_account_id IS NULL)) "
            "AND external_boundary_code IS NOT NULL "
            "AND length(trim(external_boundary_code)) > 0)",
            name="ck_inventory_movements_boundary_binding",
        ),
        sa.ForeignKeyConstraint(["transaction_id"], ["inventory_transactions.id"]),
        sa.ForeignKeyConstraint(["from_account_id"], ["stock_accounts.id"]),
        sa.ForeignKeyConstraint(["to_account_id"], ["stock_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "transaction_id", "line_no", name="uq_inventory_movements_line"
        ),
        sa.UniqueConstraint(
            "id", "transaction_id", name="uq_inventory_movements_id_transaction"
        ),
    )
    op.create_index(
        "ix_inventory_movements_transaction", "inventory_movements", ["transaction_id"]
    )
    op.create_index(
        "ix_inventory_movements_from_account",
        "inventory_movements",
        ["from_account_id"],
    )
    op.create_index(
        "ix_inventory_movements_to_account",
        "inventory_movements",
        ["to_account_id"],
    )

    op.create_table(
        "inventory_movement_serials",
        sa.Column("movement_id", sa.Uuid(), nullable=False),
        sa.Column("transaction_id", sa.Uuid(), nullable=False),
        sa.Column("serial_id", sa.Uuid(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["movement_id", "transaction_id"],
            ["inventory_movements.id", "inventory_movements.transaction_id"],
            name="fk_inventory_movement_serials_movement_transaction",
        ),
        sa.ForeignKeyConstraint(["transaction_id"], ["inventory_transactions.id"]),
        sa.ForeignKeyConstraint(["serial_id"], ["inventory_serials.id"]),
        sa.PrimaryKeyConstraint(
            "movement_id", "serial_id", name="pk_inventory_movement_serials"
        ),
        sa.UniqueConstraint(
            "transaction_id",
            "serial_id",
            name="uq_inventory_movement_serials_transaction_serial",
        ),
    )
    op.create_index(
        "ix_inventory_movement_serials_transaction",
        "inventory_movement_serials",
        ["transaction_id"],
    )

    op.create_table(
        "stock_balances",
        sa.Column("stock_account_id", sa.Uuid(), nullable=False),
        sa.Column("quantity", sa.Numeric(precision=18, scale=3), nullable=False),
        sa.Column("ledger_cursor", sa.BigInteger(), nullable=False),
        sa.Column("version", sa.BigInteger(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("quantity >= 0", name="ck_stock_balances_quantity"),
        sa.CheckConstraint(
            "ledger_cursor >= 0", name="ck_stock_balances_ledger_cursor"
        ),
        sa.CheckConstraint("version >= 0", name="ck_stock_balances_version"),
        sa.ForeignKeyConstraint(["stock_account_id"], ["stock_accounts.id"]),
        sa.PrimaryKeyConstraint("stock_account_id"),
    )
    op.create_index(
        "ix_stock_balances_ledger_cursor", "stock_balances", ["ledger_cursor"]
    )

    op.create_table(
        "serial_current_positions",
        sa.Column("serial_id", sa.Uuid(), nullable=False),
        sa.Column("stock_account_id", sa.Uuid(), nullable=True),
        sa.Column("last_movement_id", sa.Uuid(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["serial_id"], ["inventory_serials.id"]),
        sa.ForeignKeyConstraint(["stock_account_id"], ["stock_accounts.id"]),
        sa.ForeignKeyConstraint(["last_movement_id"], ["inventory_movements.id"]),
        sa.PrimaryKeyConstraint("serial_id"),
    )
    op.create_index(
        "ix_serial_current_positions_account",
        "serial_current_positions",
        ["stock_account_id"],
    )
    op.create_index(
        "ix_serial_current_positions_movement",
        "serial_current_positions",
        ["last_movement_id"],
    )

    _seed_fixed_foundation_rows()
    _create_immutable_ledger_triggers()


def _seed_fixed_foundation_rows() -> None:
    seeded_at = datetime(2026, 8, 30, tzinfo=timezone.utc)

    ledger_head_table = sa.table(
        "inventory_ledger_heads",
        sa.column("id", sa.Uuid()),
        sa.column("stream_key", sa.String(length=40)),
        sa.column("next_cursor", sa.BigInteger()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        ledger_head_table,
        [
            {
                "id": INVENTORY_LEDGER_HEAD_ID,
                "stream_key": "inventory",
                "next_cursor": 1,
                "created_at": seeded_at,
                "updated_at": seeded_at,
            }
        ],
    )

    audit_head_table = sa.table(
        "audit_chain_heads",
        sa.column("id", sa.Uuid()),
        sa.column("stream_key", sa.String(length=160)),
        sa.column("last_event_id", sa.Uuid()),
        sa.column("last_hash", sa.String(length=64)),
        sa.column("version", sa.BigInteger()),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        audit_head_table,
        [
            {
                "id": INVENTORY_AUDIT_CHAIN_HEAD_ID,
                "stream_key": "inventory",
                "last_event_id": None,
                "last_hash": None,
                "version": 0,
                "created_at": seeded_at,
                "updated_at": seeded_at,
            }
        ],
    )

    permission_table = sa.table(
        "permissions",
        sa.column("id", sa.Uuid()),
        sa.column("resource", sa.String(length=100)),
        sa.column("action", sa.String(length=80)),
        sa.column("field_code", sa.String(length=100)),
        sa.column("description", sa.String(length=300)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        permission_table,
        [
            {
                "id": INVENTORY_POST_PERMISSION_ID,
                "resource": "inventory_transaction",
                "action": "post",
                "field_code": "",
                "description": "Post an atomic immutable inventory transaction",
                "created_at": seeded_at,
                "updated_at": seeded_at,
            },
            {
                "id": INVENTORY_REVERSE_PERMISSION_ID,
                "resource": "inventory_transaction",
                "action": "reverse",
                "field_code": "",
                "description": "Reverse one posted inventory transaction",
                "created_at": seeded_at,
                "updated_at": seeded_at,
            },
        ],
    )

    role_permission_table = sa.table(
        "role_permissions",
        sa.column("id", sa.Uuid()),
        sa.column("role_id", sa.Uuid()),
        sa.column("permission_id", sa.Uuid()),
        sa.column("effect", sa.String(length=12)),
        sa.column("created_at", sa.DateTime(timezone=True)),
    )
    op.bulk_insert(
        role_permission_table,
        [
            {
                "id": ADMIN_INVENTORY_POST_ROLE_PERMISSION_ID,
                "role_id": ADMIN_ROLE_ID,
                "permission_id": INVENTORY_POST_PERMISSION_ID,
                "effect": "allow",
                "created_at": seeded_at,
            },
            {
                "id": PROVINCIAL_INVENTORY_POST_ROLE_PERMISSION_ID,
                "role_id": PROVINCIAL_MANAGER_ROLE_ID,
                "permission_id": INVENTORY_POST_PERMISSION_ID,
                "effect": "allow",
                "created_at": seeded_at,
            },
            {
                "id": ADMIN_INVENTORY_REVERSE_ROLE_PERMISSION_ID,
                "role_id": ADMIN_ROLE_ID,
                "permission_id": INVENTORY_REVERSE_PERMISSION_ID,
                "effect": "allow",
                "created_at": seeded_at,
            },
        ],
    )


def _create_immutable_ledger_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        op.execute(
            """
CREATE FUNCTION rsc_block_inventory_ledger_mutation_0009()
RETURNS trigger
LANGUAGE plpgsql
AS $$
BEGIN
    RAISE EXCEPTION 'posted inventory ledger rows are immutable';
END;
$$
"""
        )
        for table_name in IMMUTABLE_TABLES:
            op.execute(
                f"CREATE TRIGGER trg_{table_name}_immutable "
                f"BEFORE UPDATE OR DELETE ON {table_name} "
                "FOR EACH ROW EXECUTE FUNCTION "
                "rsc_block_inventory_ledger_mutation_0009()"
            )
        return
    if dialect == "sqlite":
        for table_name in IMMUTABLE_TABLES:
            for operation in ("UPDATE", "DELETE"):
                suffix = operation.lower()
                op.execute(
                    f"CREATE TRIGGER trg_{table_name}_immutable_{suffix} "
                    f"BEFORE {operation} ON {table_name} "
                    "BEGIN SELECT RAISE(ABORT, "
                    "'posted inventory ledger rows are immutable'); END"
                )
        return
    raise RuntimeError(
        "0009 supports only PostgreSQL production and SQLite local test schemas"
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError(
            "0009 downgrade requires an online connection for fail-closed data checks"
        )

    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        lock_tables = ", ".join(
            (*INVENTORY_BUSINESS_TABLES, "inventory_ledger_heads", "audit_chain_heads")
        )
        bind.exec_driver_sql(f"LOCK TABLE {lock_tables} IN ACCESS EXCLUSIVE MODE")

    for table_name in INVENTORY_BUSINESS_TABLES:
        table = sa.table(table_name, sa.column("_sentinel"))
        if bind.execute(
            sa.select(sa.literal(1)).select_from(table).limit(1)
        ).first() is not None:
            raise RuntimeError(
                f"cannot downgrade 0009: formal inventory table {table_name} "
                "contains business data"
            )

    ledger_head_table = sa.table(
        "inventory_ledger_heads",
        sa.column("id", sa.Uuid()),
        sa.column("stream_key", sa.String(length=40)),
        sa.column("next_cursor", sa.BigInteger()),
    )
    ledger_heads = bind.execute(
        sa.select(
            ledger_head_table.c.id,
            ledger_head_table.c.stream_key,
            ledger_head_table.c.next_cursor,
        )
    ).all()
    if len(ledger_heads) != 1 or (
        ledger_heads[0].id != INVENTORY_LEDGER_HEAD_ID
        or ledger_heads[0].stream_key != "inventory"
        or ledger_heads[0].next_cursor != 1
    ):
        raise RuntimeError(
            "cannot downgrade 0009: inventory ledger head is not the unused seed"
        )

    audit_head_table = sa.table(
        "audit_chain_heads",
        sa.column("id", sa.Uuid()),
        sa.column("stream_key", sa.String(length=160)),
        sa.column("last_event_id", sa.Uuid()),
        sa.column("last_hash", sa.String(length=64)),
        sa.column("version", sa.BigInteger()),
    )
    inventory_heads = bind.execute(
        sa.select(
            audit_head_table.c.id,
            audit_head_table.c.stream_key,
            audit_head_table.c.last_event_id,
            audit_head_table.c.last_hash,
            audit_head_table.c.version,
        ).where(
            sa.or_(
                audit_head_table.c.id == INVENTORY_AUDIT_CHAIN_HEAD_ID,
                audit_head_table.c.stream_key == "inventory",
            )
        )
    ).all()
    if len(inventory_heads) != 1 or not (
        inventory_heads[0].id == INVENTORY_AUDIT_CHAIN_HEAD_ID
        and inventory_heads[0].stream_key == "inventory"
        and inventory_heads[0].last_event_id is None
        and inventory_heads[0].last_hash is None
        and inventory_heads[0].version == 0
    ):
        raise RuntimeError(
            "cannot downgrade 0009: inventory audit chain has been used or changed"
        )

    _drop_immutable_ledger_triggers()

    role_permission_table = sa.table("role_permissions", sa.column("id", sa.Uuid()))
    op.execute(
        role_permission_table.delete().where(
            role_permission_table.c.id.in_(
                (
                    ADMIN_INVENTORY_POST_ROLE_PERMISSION_ID,
                    PROVINCIAL_INVENTORY_POST_ROLE_PERMISSION_ID,
                    ADMIN_INVENTORY_REVERSE_ROLE_PERMISSION_ID,
                )
            )
        )
    )
    permission_table = sa.table("permissions", sa.column("id", sa.Uuid()))
    op.execute(
        permission_table.delete().where(
            permission_table.c.id.in_(
                (INVENTORY_POST_PERMISSION_ID, INVENTORY_REVERSE_PERMISSION_ID)
            )
        )
    )
    op.execute(
        audit_head_table.delete().where(
            audit_head_table.c.id == INVENTORY_AUDIT_CHAIN_HEAD_ID
        )
    )

    for table_name in (
        "serial_current_positions",
        "stock_balances",
        "inventory_movement_serials",
        "inventory_movements",
        "inventory_transactions",
        "stock_accounts",
        "inventory_ledger_heads",
        "qr_codes",
        "inventory_serials",
        "inventory_lots",
        "custody_assignments",
        "stock_locations",
        "material_inventory_policies",
        "materials",
    ):
        op.drop_table(table_name)

    if bind.dialect.name == "postgresql":
        op.execute(
            "ALTER TABLE legacy_v09_materials RENAME CONSTRAINT "
            "legacy_v09_materials_pkey TO materials_pkey"
        )
    op.rename_table("legacy_v09_materials", "materials")


def _drop_immutable_ledger_triggers() -> None:
    dialect = op.get_bind().dialect.name
    if dialect == "postgresql":
        for table_name in IMMUTABLE_TABLES:
            op.execute(
                f"DROP TRIGGER trg_{table_name}_immutable ON {table_name}"
            )
        op.execute("DROP FUNCTION rsc_block_inventory_ledger_mutation_0009()")
        return
    if dialect == "sqlite":
        for table_name in IMMUTABLE_TABLES:
            for operation in ("update", "delete"):
                op.execute(
                    f"DROP TRIGGER trg_{table_name}_immutable_{operation}"
                )
        return
    raise RuntimeError(
        "0009 supports only PostgreSQL production and SQLite local test schemas"
    )
