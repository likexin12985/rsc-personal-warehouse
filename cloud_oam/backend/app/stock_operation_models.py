"""Formal inventory-operation documents, separate from prototype transfers.

A submitted return and its cancellation are immutable facts. Later dispatch
and acceptance facts will determine fulfillment; the original submission's
status never represents logistics signature or release of personal custody.
"""
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy import BigInteger, Boolean, CheckConstraint, ForeignKey, Index, Numeric, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .foundation_models import CreatedAtMixin, JSON_DOCUMENT, UUID_TYPE, uuid4_value


class StockOperationOrder(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_orders"
    __table_args__ = (
        UniqueConstraint("operation_no", name="uq_stock_operation_orders_no"),
        UniqueConstraint("idempotency_key_hash", name="uq_stock_operation_orders_key"),
        UniqueConstraint("actor_user_id", "request_id", name="uq_stock_operation_orders_request"),
        UniqueConstraint("posting_transaction_id", name="uq_stock_operation_orders_posting"),
        CheckConstraint("operation_type = 'return' AND status = 'submitted'", name="ck_stock_operation_orders_type_status"),
        CheckConstraint("source_location_id <> target_location_id AND source_location_id <> transit_location_id AND target_location_id <> transit_location_id", name="ck_stock_operation_orders_locations"),
        CheckConstraint("authorization_version > 0 AND length(reason) BETWEEN 1 AND 500", name="ck_stock_operation_orders_context"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    operation_no: Mapped[str] = mapped_column(String(100))
    operation_type: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))
    oam_work_order_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("oam_work_orders.id", ondelete="RESTRICT"))
    source_location_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_locations.id", ondelete="RESTRICT"))
    target_location_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_locations.id", ondelete="RESTRICT"))
    transit_location_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_locations.id", ondelete="RESTRICT"))
    target_custody_assignment_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("custody_assignments.id", ondelete="RESTRICT"))
    requester_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"))
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"))
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(160))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    plan_hash: Mapped[str] = mapped_column(String(64))
    command_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    plan_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    posting_transaction_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE,
        ForeignKey("inventory_transactions.id", deferrable=True, initially="DEFERRED"))


class StockOperationLine(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_lines"
    __table_args__ = (
        UniqueConstraint("operation_id", "line_no", name="uq_stock_operation_lines_order"),
        UniqueConstraint("operation_id", "source_recovery_line_id", name="uq_stock_operation_lines_origin"),
        Index("ix_stock_operation_lines_recovery", "source_recovery_line_id"),
        CheckConstraint("line_no > 0 AND quantity > 0", name="ck_stock_operation_lines_quantity"),
        CheckConstraint("stock_account_id <> reserved_account_id AND target_condition IN ('used','damaged')", name="ck_stock_operation_lines_dimensions"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_orders.id", ondelete="RESTRICT"))
    line_no: Mapped[int] = mapped_column(BigInteger)
    source_recovery_line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("work_order_material_lines.id", ondelete="RESTRICT"))
    stock_account_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT"))
    reserved_account_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT"))
    material_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("materials.id", ondelete="RESTRICT"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    target_condition: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text)


class StockOperationSerial(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_serials"
    __table_args__ = (UniqueConstraint("line_id", "serial_id", name="uq_stock_operation_serials_line"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_lines.id", ondelete="RESTRICT"))
    serial_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("inventory_serials.id", ondelete="RESTRICT"))
    sku_verified: Mapped[bool] = mapped_column(Boolean)
    qr_verified: Mapped[bool] = mapped_column(Boolean)


class StockOperationCancellation(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_cancellations"
    __table_args__ = (
        UniqueConstraint("operation_id", name="uq_stock_operation_cancellations_order"),
        UniqueConstraint("idempotency_key_hash", name="uq_stock_operation_cancellations_key"),
        UniqueConstraint("actor_user_id", "request_id", name="uq_stock_operation_cancellations_request"),
        UniqueConstraint("posting_transaction_id", name="uq_stock_operation_cancellations_posting"),
        CheckConstraint("authorization_version > 0 AND length(reason) BETWEEN 1 AND 500", name="ck_stock_operation_cancellations_context"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_orders.id", ondelete="RESTRICT"))
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"))
    operator_person_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"))
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(160))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    command_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    posting_transaction_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE,
        ForeignKey("inventory_transactions.id", deferrable=True, initially="DEFERRED"))
