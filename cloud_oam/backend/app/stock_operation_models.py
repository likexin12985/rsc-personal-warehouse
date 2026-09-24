"""Formal inventory-operation documents, separate from prototype transfers.

A submitted return and its cancellation are immutable facts. Later dispatch
and acceptance facts will determine fulfillment; the original submission's
status never represents logistics signature or release of personal custody.
"""
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy import BigInteger, Boolean, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, Index, Numeric, String, Text, UniqueConstraint, text
from datetime import datetime
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


class StockOperationCommandSeal(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_command_seals"
    __table_args__ = (
        UniqueConstraint("actor_user_id", "request_id", name="uq_stock_operation_seals_request"),
        CheckConstraint("operation_type IN ('submit_return','cancel_return','outbound_return','ship_return','receive_return')", name="ck_stock_operation_seals_type"),
        CheckConstraint("(operation_type='submit_return' AND operation_id IS NULL) OR (operation_type IN ('cancel_return','outbound_return','ship_return','receive_return') AND operation_id IS NOT NULL)", name="ck_stock_operation_seals_origin"),
        CheckConstraint("(operation_type='receive_return') = (shipment_id IS NOT NULL)", name="ck_stock_operation_seals_shipment"),
        CheckConstraint("authorization_version > 0 AND length(request_hash)=64", name="ck_stock_operation_seals_context"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"))
    operator_person_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"))
    oam_work_order_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("oam_work_orders.id", ondelete="RESTRICT"))
    operation_id: Mapped[uuid.UUID | None] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_orders.id", ondelete="RESTRICT"))
    operation_type: Mapped[str] = mapped_column(String(24))
    shipment_id: Mapped[uuid.UUID | None] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_shipments.id", ondelete="RESTRICT"))
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    request_id: Mapped[str] = mapped_column(String(160))
    request_reference: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))


class StockOperationOutbound(CreatedAtMixin, Base):
    """One physical departure; carrier handover and receiving remain separate."""
    __tablename__ = "stock_operation_outbounds"
    __table_args__ = (
        UniqueConstraint("outbound_no", name="uq_stock_operation_outbounds_no"),
        UniqueConstraint("idempotency_key_hash", name="uq_stock_operation_outbounds_key"),
        UniqueConstraint("actor_user_id", "request_id", name="uq_stock_operation_outbounds_request"),
        UniqueConstraint("posting_transaction_id", name="uq_stock_operation_outbounds_posting"),
        CheckConstraint("status = 'outbound' AND authorization_version > 0 AND length(reason) BETWEEN 1 AND 500", name="ck_stock_operation_outbounds_context"),
        CheckConstraint("outbound_at <= created_at", name="ck_stock_operation_outbounds_time"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    outbound_no: Mapped[str] = mapped_column(String(100))
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_orders.id", ondelete="RESTRICT"), index=True)
    status: Mapped[str] = mapped_column(String(24))
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"))
    operator_person_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"))
    target_custody_assignment_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("custody_assignments.id", ondelete="RESTRICT"))
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(Text)
    outbound_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    request_id: Mapped[str] = mapped_column(String(160))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    plan_hash: Mapped[str] = mapped_column(String(64))
    command_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    plan_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    posting_transaction_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("inventory_transactions.id", deferrable=True, initially="DEFERRED"))


class StockOperationOutboundLine(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_outbound_lines"
    __table_args__ = (
        UniqueConstraint("outbound_id", "line_no", name="uq_stock_operation_outbound_lines_order"),
        UniqueConstraint("outbound_id", "operation_line_id", name="uq_stock_operation_outbound_lines_origin"),
        CheckConstraint("line_no > 0 AND quantity > 0 AND source_stock_account_id <> transit_stock_account_id", name="ck_stock_operation_outbound_lines_context"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    outbound_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_outbounds.id", ondelete="RESTRICT"))
    line_no: Mapped[int] = mapped_column(BigInteger)
    operation_line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_lines.id", ondelete="RESTRICT"), index=True)
    source_stock_account_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT"))
    transit_stock_account_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT"))
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))


class StockOperationOutboundSerial(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_outbound_serials"
    __table_args__ = (UniqueConstraint("line_id", "serial_id", name="uq_stock_operation_outbound_serials_line"),)
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_outbound_lines.id", ondelete="RESTRICT"))
    serial_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("inventory_serials.id", ondelete="RESTRICT"))
    sku_verified: Mapped[bool] = mapped_column(Boolean)
    qr_verified: Mapped[bool] = mapped_column(Boolean)


class StockOperationShipment(CreatedAtMixin, Base):
    """Return-specific provenance for the shared immutable shipment header."""
    __tablename__ = "stock_operation_shipments"
    __table_args__ = (
        UniqueConstraint("actor_user_id", "request_id", name="uq_stock_operation_shipments_request"),
        UniqueConstraint("audit_version", name="uq_stock_operation_shipments_audit"),
        CheckConstraint("audit_version > 0", name="ck_stock_operation_shipments_audit"),
        CheckConstraint("length(reason) BETWEEN 1 AND 500 AND length(plan_hash)=64", name="ck_stock_operation_shipments_context"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("shipments.id", ondelete="RESTRICT"), primary_key=True)
    operation_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_orders.id", ondelete="RESTRICT"), index=True)
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"))
    target_custody_assignment_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("custody_assignments.id", ondelete="RESTRICT"))
    request_id: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str] = mapped_column(Text)
    plan_hash: Mapped[str] = mapped_column(String(64))
    audit_version: Mapped[int] = mapped_column(BigInteger)
    command_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    plan_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)


class StockOperationShipmentLine(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_shipment_lines"
    __table_args__ = (
        UniqueConstraint("shipment_id", "line_no", name="uq_stock_operation_shipment_lines_order"),
        UniqueConstraint("shipment_id", "outbound_line_id", name="uq_stock_operation_shipment_lines_origin"),
        UniqueConstraint("id", "outbound_line_id", name="uq_stock_operation_shipment_lines_binding"),
        CheckConstraint("line_no > 0 AND quantity > 0", name="ck_stock_operation_shipment_lines_quantity"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    shipment_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_shipments.id", ondelete="RESTRICT"))
    line_no: Mapped[int] = mapped_column(BigInteger)
    outbound_line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_outbound_lines.id", ondelete="RESTRICT"), index=True)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 3))


class StockOperationShipmentSerial(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_shipment_serials"
    __table_args__ = (
        UniqueConstraint("line_id", "serial_id", name="uq_stock_operation_shipment_serials_line"),
        UniqueConstraint("outbound_line_id", "serial_id", name="uq_stock_operation_shipment_serials_origin"),
        ForeignKeyConstraint(["line_id", "outbound_line_id"], ["stock_operation_shipment_lines.id", "stock_operation_shipment_lines.outbound_line_id"], ondelete="RESTRICT"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    outbound_line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    serial_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("inventory_serials.id", ondelete="RESTRICT"))


class StockOperationReceipt(CreatedAtMixin, Base):
    """Independent acceptance of a return parcel; inbound is a later fact."""
    __tablename__ = "stock_operation_receipts"
    __table_args__ = (
        UniqueConstraint("actor_user_id", "request_id", name="uq_stock_operation_receipts_request"),
        UniqueConstraint("audit_version", name="uq_stock_operation_receipts_audit"),
        CheckConstraint("authorization_version > 0 AND audit_version > 0", name="ck_stock_operation_receipts_versions"),
        CheckConstraint("length(reason) BETWEEN 1 AND 500 AND length(plan_hash)=64", name="ck_stock_operation_receipts_context"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("receipts.id", ondelete="RESTRICT"), primary_key=True)
    shipment_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_shipments.id", ondelete="RESTRICT"), index=True)
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"))
    operator_person_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"))
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    target_custody_assignment_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("custody_assignments.id", ondelete="RESTRICT"))
    request_id: Mapped[str] = mapped_column(String(160))
    reason: Mapped[str] = mapped_column(Text)
    plan_hash: Mapped[str] = mapped_column(String(64))
    audit_version: Mapped[int] = mapped_column(BigInteger)
    command_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    plan_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)


class StockOperationReceiptLine(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_receipt_lines"
    __table_args__ = (
        UniqueConstraint("receipt_id", "line_no", name="uq_stock_operation_receipt_lines_order"),
        UniqueConstraint("receipt_id", "shipment_line_id", name="uq_stock_operation_receipt_lines_origin"),
        UniqueConstraint("id", "shipment_line_id", name="uq_stock_operation_receipt_lines_binding"),
        UniqueConstraint("id", "receipt_id", name="uq_stock_operation_receipt_lines_receipt"),
        CheckConstraint("line_no > 0 AND accepted_qty >= 0 AND rejected_qty >= 0 AND shortage_qty >= 0 AND accepted_qty + rejected_qty + shortage_qty > 0 AND damaged_qty >= 0 AND damaged_qty <= accepted_qty", name="ck_stock_operation_receipt_lines_quantities"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    receipt_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_receipts.id", ondelete="RESTRICT"))
    shipment_line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_shipment_lines.id", ondelete="RESTRICT"), index=True)
    line_no: Mapped[int] = mapped_column(BigInteger)
    accepted_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    rejected_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    damaged_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3))
    shortage_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3))


class StockOperationReceiptSerial(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_receipt_serials"
    __table_args__ = (
        UniqueConstraint("line_id", "serial_id", name="uq_stock_operation_receipt_serials_line"),
        Index("uq_stock_operation_receipt_serials_confirmed", "shipment_line_id", "serial_id", unique=True,
            postgresql_where=text("result IN ('accepted','rejected')"), sqlite_where=text("result IN ('accepted','rejected')")),
        ForeignKeyConstraint(["line_id", "shipment_line_id"], ["stock_operation_receipt_lines.id", "stock_operation_receipt_lines.shipment_line_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["shipment_line_id", "serial_id"], ["stock_operation_shipment_serials.line_id", "stock_operation_shipment_serials.serial_id"], ondelete="RESTRICT"),
        CheckConstraint("result IN ('accepted','rejected','shortage') AND (NOT damaged OR result='accepted') AND ((result='accepted' AND sku_verified AND qr_verified) OR (result<>'accepted' AND NOT sku_verified AND NOT qr_verified))", name="ck_stock_operation_receipt_serials_proof"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    shipment_line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    serial_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    result: Mapped[str] = mapped_column(String(24))
    damaged: Mapped[bool] = mapped_column(Boolean)
    sku_verified: Mapped[bool] = mapped_column(Boolean)
    qr_verified: Mapped[bool] = mapped_column(Boolean)


class StockOperationReceiptException(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_receipt_exceptions"
    __table_args__ = (
        ForeignKeyConstraint(["line_id", "receipt_id"], ["stock_operation_receipt_lines.id", "stock_operation_receipt_lines.receipt_id"], ondelete="RESTRICT"),
        UniqueConstraint("line_id", "exception_type", name="uq_stock_operation_receipt_exceptions_kind"),
        CheckConstraint("exception_type IN ('shortage','damaged','wrong_material','wrong_serial','rejected') AND length(description) BETWEEN 1 AND 1000", name="ck_stock_operation_receipt_exceptions_detail"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    receipt_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    exception_type: Mapped[str] = mapped_column(String(32))
    description: Mapped[str] = mapped_column(Text)
    evidence_file_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("files.id", ondelete="RESTRICT"))


class StockOperationReturnInboundSeal(CreatedAtMixin, Base):
    """Permanently close one exact acceptance's uncertain inbound request."""

    __tablename__ = "stock_operation_return_inbound_seals"
    __table_args__ = (
        UniqueConstraint("actor_user_id", "request_id", name="uq_return_inbound_seals_request"),
        Index("ix_return_inbound_seals_receipt", "receipt_id"),
        CheckConstraint("authorization_version > 0 AND length(request_hash)=64", name="ck_return_inbound_seals_context"),
    )
    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    receipt_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_receipts.id", ondelete="RESTRICT"))
    shipment_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("stock_operation_shipments.id", ondelete="RESTRICT"))
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"))
    operator_person_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"))
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    request_id: Mapped[str] = mapped_column(String(160))
    request_reference: Mapped[str] = mapped_column(String(100))
    request_hash: Mapped[str] = mapped_column(String(64))


class StockOperationReturnInbound(CreatedAtMixin, Base):
    """The independent personal/region inbound fact after return acceptance.

    This document is deliberately separate from ``StockOperationReceipt``:
    acknowledgement of a parcel never changes inventory.  The row is created
    only in the same transaction as its immutable inventory posting.
    """

    __tablename__ = "stock_operation_return_inbounds"
    __table_args__ = (
        UniqueConstraint("inbound_no", name="uq_stock_operation_return_inbounds_no"),
        UniqueConstraint("receipt_id", name="uq_stock_operation_return_inbounds_receipt"),
        UniqueConstraint("actor_user_id", "request_id", name="uq_stock_operation_return_inbounds_request"),
        UniqueConstraint("idempotency_key_hash", name="uq_stock_operation_return_inbounds_key"),
        UniqueConstraint("posting_transaction_id", name="uq_stock_operation_return_inbounds_posting"),
        Index("ix_stock_operation_return_inbounds_receipt_id", "receipt_id"),
        CheckConstraint("status = 'posted'", name="ck_stock_operation_return_inbounds_status"),
        CheckConstraint(
            "authorization_version > 0 AND audit_version > 0 AND "
            "length(reason) BETWEEN 1 AND 500 AND "
            "length(idempotency_key_hash)=64 AND length(request_hash)=64 AND "
            "length(receipt_plan_hash)=64 AND length(plan_hash)=64",
            name="ck_stock_operation_return_inbounds_context",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    inbound_no: Mapped[str] = mapped_column(String(100))
    receipt_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_operation_receipts.id", ondelete="RESTRICT")
    )
    shipment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_operation_shipments.id", ondelete="RESTRICT")
    )
    actor_user_id: Mapped[str] = mapped_column(String(36), ForeignKey("users.id", ondelete="RESTRICT"))
    operator_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    target_location_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_locations.id", ondelete="RESTRICT")
    )
    target_custody_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("custody_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(24))
    reason: Mapped[str] = mapped_column(Text)
    request_id: Mapped[str] = mapped_column(String(160))
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    receipt_plan_hash: Mapped[str] = mapped_column(String(64))
    plan_hash: Mapped[str] = mapped_column(String(64))
    audit_version: Mapped[int] = mapped_column(BigInteger)
    command_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    plan_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    posting_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("inventory_transactions.id", ondelete="RESTRICT", deferrable=True, initially="DEFERRED"),
    )


class StockOperationReturnInboundLine(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_return_inbound_lines"
    __table_args__ = (
        UniqueConstraint("inbound_id", "line_no", name="uq_stock_operation_return_inbound_lines_order"),
        UniqueConstraint("inbound_id", "receipt_line_id", name="uq_stock_operation_return_inbound_lines_origin"),
        UniqueConstraint("id", "inbound_id", name="uq_stock_operation_return_inbound_lines_binding"),
        Index("ix_stock_operation_return_inbound_lines_receipt_line_id", "receipt_line_id"),
        CheckConstraint(
            "line_no > 0 AND accepted_qty > 0 AND condition_code IN ('used','damaged')",
            name="ck_stock_operation_return_inbound_lines_context",
        ),
        ForeignKeyConstraint(
            ["lot_id", "material_id"],
            ["inventory_lots.id", "inventory_lots.material_id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    inbound_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_operation_return_inbounds.id", ondelete="RESTRICT")
    )
    receipt_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_operation_receipt_lines.id", ondelete="RESTRICT")
    )
    line_no: Mapped[int] = mapped_column(BigInteger)
    source_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT")
    )
    target_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT")
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id", ondelete="RESTRICT")
    )
    lot_id: Mapped[uuid.UUID | None] = mapped_column(UUID_TYPE, nullable=True)
    condition_code: Mapped[str] = mapped_column(String(24))
    accepted_qty: Mapped[Decimal] = mapped_column(Numeric(18, 3))


class StockOperationReturnInboundSerial(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_return_inbound_serials"
    __table_args__ = (
        UniqueConstraint("line_id", "serial_id", name="uq_stock_operation_return_inbound_serials_line"),
        UniqueConstraint("inbound_id", "serial_id", name="uq_stock_operation_return_inbound_serials_once"),
        UniqueConstraint("receipt_serial_id", name="uq_stock_operation_return_inbound_serials_receipt"),
        Index("ix_stock_operation_return_inbound_serials_serial_id", "serial_id"),
        ForeignKeyConstraint(
            ["line_id", "inbound_id"],
            ["stock_operation_return_inbound_lines.id", "stock_operation_return_inbound_lines.inbound_id"],
            ondelete="RESTRICT",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    inbound_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    receipt_serial_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_operation_receipt_serials.id", ondelete="RESTRICT")
    )
    serial_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_serials.id", ondelete="RESTRICT")
    )


class StockOperationReturnInboundPosting(CreatedAtMixin, Base):
    __tablename__ = "stock_operation_return_inbound_postings"
    __table_args__ = (
        UniqueConstraint("inbound_id", name="uq_stock_operation_return_inbound_postings_inbound"),
        UniqueConstraint("inventory_transaction_id", name="uq_stock_operation_return_inbound_postings_transaction"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    inbound_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_operation_return_inbounds.id", ondelete="RESTRICT")
    )
    inventory_transaction_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_transactions.id", ondelete="RESTRICT")
    )
