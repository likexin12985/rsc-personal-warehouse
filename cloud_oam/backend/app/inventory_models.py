"""V1.0 stage-two inventory master data and immutable ledger schema.

These models deliberately share no rows with the quarantined v0.9 inventory
tables.  In particular, OAM control balances and legacy prototype balances are
never interpreted as personal-warehouse inventory facts.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    PrimaryKeyConstraint,
    String,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


UUID_TYPE = Uuid(as_uuid=True)
QUANTITY = Numeric(18, 3)


def uuid4_value() -> uuid.UUID:
    return uuid.uuid4()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CreatedAtMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )


class TimestampMixin(CreatedAtMixin):
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class FormalMaterial(TimestampMixin, Base):
    __tablename__ = "materials"
    __table_args__ = (
        UniqueConstraint(
            "external_object_id", name="uq_materials_external_object_id"
        ),
        UniqueConstraint("sku_code", name="uq_materials_sku_code"),
        CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_materials_status"
        ),
        Index("ix_formal_materials_name", "name"),
        Index("ix_formal_materials_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    external_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("external_objects.id")
    )
    sku_code: Mapped[str] = mapped_column(String(80))
    name: Mapped[str] = mapped_column(String(200))
    specification: Mapped[str] = mapped_column(String(300), default="")
    base_unit: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(20), default="active")
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaterialInventoryPolicy(TimestampMixin, Base):
    __tablename__ = "material_inventory_policies"
    __table_args__ = (
        UniqueConstraint(
            "material_id",
            "effective_from",
            name="uq_material_inventory_policies_start",
        ),
        CheckConstraint(
            "tracking_mode IN ('none', 'lot', 'serial', 'lot_and_serial')",
            name="ck_material_inventory_policies_tracking_mode",
        ),
        CheckConstraint(
            "quantity_scale BETWEEN 0 AND 3",
            name="ck_material_inventory_policies_quantity_scale",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_material_inventory_policies_validity",
        ),
        Index("ix_material_inventory_policies_material", "material_id"),
        Index(
            "uq_material_inventory_policies_current",
            "material_id",
            unique=True,
            postgresql_where=text("effective_to IS NULL"),
            sqlite_where=text("effective_to IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id")
    )
    tracking_mode: Mapped[str] = mapped_column(String(24))
    quantity_scale: Mapped[int] = mapped_column(Integer)
    allow_fraction: Mapped[bool] = mapped_column(Boolean)
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class StockLocation(TimestampMixin, Base):
    __tablename__ = "stock_locations"
    __table_args__ = (
        UniqueConstraint("code", name="uq_stock_locations_code"),
        CheckConstraint(
            "location_type IN ('headquarters', 'region', 'personal', "
            "'transit', 'quarantine')",
            name="ck_stock_locations_type",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')", name="ck_stock_locations_status"
        ),
        CheckConstraint(
            "parent_id IS NULL OR parent_id <> id",
            name="ck_stock_locations_parent",
        ),
        CheckConstraint(
            "location_type <> 'personal' OR "
            "(parent_id IS NOT NULL AND custodian_person_id IS NOT NULL)",
            name="ck_stock_locations_personal_binding",
        ),
        Index("ix_stock_locations_owner_org", "owner_org_id"),
        Index("ix_stock_locations_parent", "parent_id"),
        Index("ix_stock_locations_custodian", "custodian_person_id"),
        Index("ix_stock_locations_type_status", "location_type", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    code: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(200))
    location_type: Mapped[str] = mapped_column(String(24))
    owner_org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("organizations.id")
    )
    parent_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("stock_locations.id"), nullable=True
    )
    custodian_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("people.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), default="active")


class CustodyAssignment(TimestampMixin, Base):
    __tablename__ = "custody_assignments"
    __table_args__ = (
        UniqueConstraint(
            "location_id", "valid_from", name="uq_custody_assignments_start"
        ),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_custody_assignments_validity",
        ),
        Index("ix_custody_assignments_custodian", "custodian_person_id"),
        Index(
            "uq_custody_assignments_current_location",
            "location_id",
            unique=True,
            postgresql_where=text("valid_to IS NULL"),
            sqlite_where=text("valid_to IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_locations.id")
    )
    custodian_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id")
    )
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # The stage-four handover table does not exist yet.  Keep the reviewed UUID
    # reference without inventing an unenforceable foreign key in this tranche.
    handover_case_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )


class InventoryLot(TimestampMixin, Base):
    __tablename__ = "inventory_lots"
    __table_args__ = (
        UniqueConstraint(
            "material_id", "lot_no", name="uq_inventory_lots_material_no"
        ),
        UniqueConstraint("id", "material_id", name="uq_inventory_lots_id_material"),
        CheckConstraint(
            "expiry_date IS NULL OR manufacture_date IS NULL OR "
            "expiry_date >= manufacture_date",
            name="ck_inventory_lots_date_order",
        ),
        Index("ix_inventory_lots_expiry_date", "expiry_date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id")
    )
    lot_no: Mapped[str] = mapped_column(String(160))
    manufacture_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    expiry_date: Mapped[date | None] = mapped_column(Date, nullable=True)


class InventorySerial(TimestampMixin, Base):
    __tablename__ = "inventory_serials"
    __table_args__ = (
        UniqueConstraint(
            "material_id", "serial_no", name="uq_inventory_serials_material_no"
        ),
        UniqueConstraint("qr_code", name="uq_inventory_serials_qr_code"),
        ForeignKeyConstraint(
            ["lot_id", "material_id"],
            ["inventory_lots.id", "inventory_lots.material_id"],
            name="fk_inventory_serials_lot_material",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "lifecycle_status IN ('active', 'consumed', 'returned', "
            "'scrapped', 'lost')",
            name="ck_inventory_serials_lifecycle",
        ),
        Index("ix_inventory_serials_lot", "lot_id"),
        Index("ix_inventory_serials_status", "lifecycle_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id", ondelete="RESTRICT")
    )
    serial_no: Mapped[str] = mapped_column(String(200))
    qr_code: Mapped[str] = mapped_column(String(250))
    lot_id: Mapped[uuid.UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    lifecycle_status: Mapped[str] = mapped_column(String(24), default="active")


class QrCode(TimestampMixin, Base):
    __tablename__ = "qr_codes"
    __table_args__ = (
        UniqueConstraint("code", name="uq_qr_codes_code"),
        UniqueConstraint(
            "object_type", "object_id", name="uq_qr_codes_object"
        ),
        CheckConstraint(
            "object_type IN ('location', 'material', 'lot', 'serial')",
            name="ck_qr_codes_object_type",
        ),
        CheckConstraint(
            "status IN ('active', 'void')", name="ck_qr_codes_status"
        ),
        Index("ix_qr_codes_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    code: Mapped[str] = mapped_column(String(250))
    object_type: Mapped[str] = mapped_column(String(24))
    object_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    status: Mapped[str] = mapped_column(String(20), default="active")
    printed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class InventoryLedgerHead(TimestampMixin, Base):
    __tablename__ = "inventory_ledger_heads"
    __table_args__ = (
        UniqueConstraint("stream_key", name="uq_inventory_ledger_heads_stream"),
        CheckConstraint(
            "stream_key = 'inventory'", name="ck_inventory_ledger_heads_stream"
        ),
        CheckConstraint(
            "next_cursor > 0", name="ck_inventory_ledger_heads_next_cursor"
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True)
    stream_key: Mapped[str] = mapped_column(String(40))
    next_cursor: Mapped[int] = mapped_column(BigInteger, default=1)


class StockAccount(TimestampMixin, Base):
    __tablename__ = "stock_accounts"
    __table_args__ = (
        CheckConstraint(
            "condition_code IN ('new', 'used', 'damaged', 'scrapped')",
            name="ck_stock_accounts_condition",
        ),
        CheckConstraint(
            "availability_bucket IN ('available', 'reserved', 'picking', "
            "'outbound', 'in_transit', 'arrived_pending', 'frozen', "
            "'return_pending', 'scrap_pending')",
            name="ck_stock_accounts_availability",
        ),
        ForeignKeyConstraint(
            ["lot_id", "material_id"],
            ["inventory_lots.id", "inventory_lots.material_id"],
            name="fk_stock_accounts_lot_material",
            ondelete="RESTRICT",
        ),
        Index("ix_stock_accounts_location_material", "location_id", "material_id"),
        Index(
            "uq_stock_accounts_custodian_lot",
            "owner_org_id",
            "custodian_person_id",
            "location_id",
            "material_id",
            "condition_code",
            "availability_bucket",
            "lot_id",
            unique=True,
            postgresql_where=text(
                "custodian_person_id IS NOT NULL AND lot_id IS NOT NULL"
            ),
            sqlite_where=text(
                "custodian_person_id IS NOT NULL AND lot_id IS NOT NULL"
            ),
        ),
        Index(
            "uq_stock_accounts_no_custodian_with_lot",
            "owner_org_id",
            "location_id",
            "material_id",
            "condition_code",
            "availability_bucket",
            "lot_id",
            unique=True,
            postgresql_where=text(
                "custodian_person_id IS NULL AND lot_id IS NOT NULL"
            ),
            sqlite_where=text("custodian_person_id IS NULL AND lot_id IS NOT NULL"),
        ),
        Index(
            "uq_stock_accounts_with_custodian_no_lot",
            "owner_org_id",
            "custodian_person_id",
            "location_id",
            "material_id",
            "condition_code",
            "availability_bucket",
            unique=True,
            postgresql_where=text(
                "custodian_person_id IS NOT NULL AND lot_id IS NULL"
            ),
            sqlite_where=text("custodian_person_id IS NOT NULL AND lot_id IS NULL"),
        ),
        Index(
            "uq_stock_accounts_no_custodian_no_lot",
            "owner_org_id",
            "location_id",
            "material_id",
            "condition_code",
            "availability_bucket",
            unique=True,
            postgresql_where=text(
                "custodian_person_id IS NULL AND lot_id IS NULL"
            ),
            sqlite_where=text("custodian_person_id IS NULL AND lot_id IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    owner_org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    custodian_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"), nullable=True
    )
    location_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_locations.id", ondelete="RESTRICT")
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id", ondelete="RESTRICT")
    )
    condition_code: Mapped[str] = mapped_column(String(20))
    availability_bucket: Mapped[str] = mapped_column(String(24))
    lot_id: Mapped[uuid.UUID | None] = mapped_column(UUID_TYPE, nullable=True)


class StockAllocation(TimestampMixin, Base):
    """Immutable source assignment fact, before any reservation or movement.

    Allocation records capture the approved request coordinate and the exact
    inventory projection version used for the decision.  The runtime role is
    granted INSERT/SELECT only; release and reservation are separate future
    facts so this table cannot be rewritten into a later state.
    """

    __tablename__ = "stock_allocations"
    __table_args__ = (
        UniqueConstraint("allocation_no", name="uq_stock_allocations_number"),
        UniqueConstraint("idempotency_key_hash", name="uq_stock_allocations_idempotency"),
        CheckConstraint("allocated_qty > 0", name="ck_stock_allocations_quantity"),
        CheckConstraint("request_version >= 0", name="ck_stock_allocations_request_version"),
        CheckConstraint("revision_no > 0", name="ck_stock_allocations_revision"),
        CheckConstraint("source_balance_version >= 0", name="ck_stock_allocations_balance_version"),
        CheckConstraint("source_ledger_cursor >= 0", name="ck_stock_allocations_ledger_cursor"),
        CheckConstraint("authorization_version > 0", name="ck_stock_allocations_authorization_version"),
        CheckConstraint("status = 'allocated'", name="ck_stock_allocations_status"),
        CheckConstraint("length(idempotency_key_hash) = 64 AND length(request_hash) = 64", name="ck_stock_allocations_hashes"),
        Index("ix_stock_allocations_request_line", "request_line_id", "status"),
        Index("ix_stock_allocations_source_account", "source_stock_account_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    allocation_no: Mapped[str] = mapped_column(String(100))
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("material_requests.id", ondelete="RESTRICT"), index=True
    )
    request_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("material_request_lines.id", ondelete="RESTRICT"), index=True
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    revision_no: Mapped[int] = mapped_column(Integer)
    request_version: Mapped[int] = mapped_column(BigInteger)
    source_stock_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT"), index=True
    )
    allocated_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    source_balance_version: Mapped[int] = mapped_column(BigInteger)
    source_ledger_cursor: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(20), default="allocated", server_default="allocated")
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"))
    actor_person_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"))
    authorization_version: Mapped[int] = mapped_column(BigInteger)


class StockAllocationSerial(CreatedAtMixin, Base):
    """Optional serial evidence attached to an immutable allocation."""

    __tablename__ = "stock_allocation_serials"
    __table_args__ = (
        PrimaryKeyConstraint("allocation_id", "serial_id", name="pk_stock_allocation_serials"),
        UniqueConstraint("serial_id", name="uq_stock_allocation_serials_serial"),
        ForeignKeyConstraint(
            ["allocation_id"], ["stock_allocations.id"],
            name="fk_stock_allocation_serials_allocation", ondelete="RESTRICT"
        ),
        ForeignKeyConstraint(
            ["serial_id"], ["inventory_serials.id"],
            name="fk_stock_allocation_serials_serial", ondelete="RESTRICT"
        ),
    )

    allocation_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    serial_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)


class InventoryTransaction(CreatedAtMixin, Base):
    __tablename__ = "inventory_transactions"
    __table_args__ = (
        UniqueConstraint(
            "transaction_no", name="uq_inventory_transactions_number"
        ),
        UniqueConstraint("posting_key", name="uq_inventory_transactions_posting_key"),
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_inventory_transactions_idempotency_hash",
        ),
        UniqueConstraint(
            "ledger_cursor", name="uq_inventory_transactions_ledger_cursor"
        ),
        UniqueConstraint(
            "reversed_transaction_id",
            name="uq_inventory_transactions_reversed_transaction",
        ),
        CheckConstraint(
            "movement_type IN ('opening', 'transfer', 'reserve', 'release', "
            "'pick', 'outbound', 'transit', 'inbound', 'freeze', 'unfreeze', "
            "'consume', 'return', 'scrap', 'stocktake_gain', "
            "'stocktake_loss', 'status_change', 'reversal')",
            name="ck_inventory_transactions_movement_type",
        ),
        CheckConstraint(
            "status = 'posted'", name="ck_inventory_transactions_status"
        ),
        CheckConstraint(
            "ledger_cursor > 0", name="ck_inventory_transactions_ledger_cursor"
        ),
        CheckConstraint(
            "length(idempotency_key_hash) = 64 AND length(request_hash) = 64",
            name="ck_inventory_transactions_hashes",
        ),
        CheckConstraint(
            "length(posting_key) > 0 AND length(source_document_type) > 0 "
            "AND length(source_document_id) > 0",
            name="ck_inventory_transactions_business_keys",
        ),
        CheckConstraint(
            "(movement_type = 'reversal' AND reversed_transaction_id IS NOT NULL) "
            "OR (movement_type <> 'reversal' AND reversed_transaction_id IS NULL)",
            name="ck_inventory_transactions_reversal_binding",
        ),
        CheckConstraint(
            "reversed_transaction_id IS NULL OR reversed_transaction_id <> id",
            name="ck_inventory_transactions_not_self_reversal",
        ),
        Index(
            "ix_inventory_transactions_source_document",
            "source_document_type",
            "source_document_id",
        ),
        Index("ix_inventory_transactions_posted_at", "posted_at"),
        Index("ix_inventory_transactions_actor", "actor_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    transaction_no: Mapped[str] = mapped_column(String(100))
    movement_type: Mapped[str] = mapped_column(String(32))
    source_document_type: Mapped[str] = mapped_column(String(80))
    source_document_id: Mapped[str] = mapped_column(String(80))
    posting_key: Mapped[str] = mapped_column(String(200))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), default="posted")
    effective_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    posted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ledger_cursor: Mapped[int] = mapped_column(BigInteger)
    reversed_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_transactions.id"), nullable=True
    )
    actor_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id")
    )


class InventoryMovement(CreatedAtMixin, Base):
    __tablename__ = "inventory_movements"
    __table_args__ = (
        UniqueConstraint(
            "transaction_id", "line_no", name="uq_inventory_movements_line"
        ),
        UniqueConstraint(
            "id", "transaction_id", name="uq_inventory_movements_id_transaction"
        ),
        CheckConstraint("line_no > 0", name="ck_inventory_movements_line_positive"),
        CheckConstraint("quantity > 0", name="ck_inventory_movements_quantity"),
        CheckConstraint(
            "from_account_id IS NOT NULL OR to_account_id IS NOT NULL",
            name="ck_inventory_movements_has_endpoint",
        ),
        CheckConstraint(
            "from_account_id IS NULL OR to_account_id IS NULL "
            "OR from_account_id <> to_account_id",
            name="ck_inventory_movements_distinct_accounts",
        ),
        CheckConstraint(
            "(from_account_id IS NOT NULL AND to_account_id IS NOT NULL "
            "AND external_boundary_code IS NULL) OR "
            "(((from_account_id IS NULL AND to_account_id IS NOT NULL) OR "
            "(from_account_id IS NOT NULL AND to_account_id IS NULL)) "
            "AND external_boundary_code IS NOT NULL "
            "AND length(trim(external_boundary_code)) > 0)",
            name="ck_inventory_movements_boundary_binding",
        ),
        Index("ix_inventory_movements_transaction", "transaction_id"),
        Index("ix_inventory_movements_from_account", "from_account_id"),
        Index("ix_inventory_movements_to_account", "to_account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_transactions.id")
    )
    line_no: Mapped[int] = mapped_column(Integer)
    from_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id"), nullable=True
    )
    to_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id"), nullable=True
    )
    external_boundary_code: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)


class InventoryMovementSerial(CreatedAtMixin, Base):
    __tablename__ = "inventory_movement_serials"
    __table_args__ = (
        PrimaryKeyConstraint(
            "movement_id", "serial_id", name="pk_inventory_movement_serials"
        ),
        UniqueConstraint(
            "transaction_id",
            "serial_id",
            name="uq_inventory_movement_serials_transaction_serial",
        ),
        ForeignKeyConstraint(
            ["movement_id", "transaction_id"],
            ["inventory_movements.id", "inventory_movements.transaction_id"],
            name="fk_inventory_movement_serials_movement_transaction",
        ),
        Index(
            "ix_inventory_movement_serials_transaction", "transaction_id"
        ),
    )

    movement_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_transactions.id")
    )
    serial_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_serials.id")
    )


class StockBalance(TimestampMixin, Base):
    __tablename__ = "stock_balances"
    __table_args__ = (
        CheckConstraint("quantity >= 0", name="ck_stock_balances_quantity"),
        CheckConstraint(
            "ledger_cursor >= 0", name="ck_stock_balances_ledger_cursor"
        ),
        CheckConstraint("version >= 0", name="ck_stock_balances_version"),
        Index("ix_stock_balances_ledger_cursor", "ledger_cursor"),
    )

    stock_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id"), primary_key=True
    )
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    ledger_cursor: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(BigInteger)


class SerialCurrentPosition(Base):
    __tablename__ = "serial_current_positions"
    __table_args__ = (
        Index("ix_serial_current_positions_account", "stock_account_id"),
        Index("ix_serial_current_positions_movement", "last_movement_id"),
    )

    serial_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_serials.id"), primary_key=True
    )
    stock_account_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id"), nullable=True
    )
    last_movement_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_movements.id")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow
    )
