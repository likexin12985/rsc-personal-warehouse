"""Formal V1.0 material-request and three-stage approval schema.

These tables are deliberately separate from the quarantined v0.9 ``transfers``
prototype.  They stop at approved demand, substitution confirmation and
shortage planning: no table in this module allocates, reserves, dispatches,
ships, receives or posts inventory.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from decimal import Decimal
from typing import Any
import uuid

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    FetchedValue,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    JSON,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    text,
)
from sqlalchemy.dialects import postgresql
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


UUID_TYPE = Uuid(as_uuid=True)
QUANTITY = Numeric(18, 3)
RATIO = Numeric(18, 6)
JSON_DOCUMENT = JSON().with_variant(postgresql.JSONB(), "postgresql")


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


class OamWorkOrder(TimestampMixin, Base):
    """Read-only formal projection used only as a request reference."""

    __tablename__ = "oam_work_orders"
    __table_args__ = (
        UniqueConstraint(
            "external_object_id", name="uq_oam_work_orders_external_object"
        ),
        UniqueConstraint("work_order_no", name="uq_oam_work_orders_number"),
        CheckConstraint(
            "status IN ('pending', 'active', 'completed', 'closed', "
            "'cancelled', 'inactive')",
            name="ck_oam_work_orders_status",
        ),
        Index("ix_oam_work_orders_org_status", "organization_id", "status"),
        Index(
            "ix_oam_work_orders_engineer_status",
            "engineer_person_id",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    external_object_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("external_objects.id", ondelete="RESTRICT")
    )
    work_order_no: Mapped[str] = mapped_column(String(100))
    organization_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("organizations.id", ondelete="RESTRICT")
    )
    engineer_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("people.id", ondelete="RESTRICT"),
        nullable=True,
    )
    status: Mapped[str] = mapped_column(String(24), index=True)
    source_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class WorkOrderMaterialOperation(CreatedAtMixin, Base):
    """Immutable formal work-order material operation header.

    The operation is a business fact; its inventory transaction is supplied by
    the formal posting service and is never inferred from the old prototype
    ``work_order_materials`` table.
    """

    __tablename__ = "work_order_material_operations"
    __table_args__ = (
        UniqueConstraint("operation_no", name="uq_work_order_material_operations_no"),
        UniqueConstraint("idempotency_key_hash", name="uq_work_order_material_operations_key"),
        CheckConstraint(
            "operation_type IN ('occupy','release','consume','recover','reverse')",
            name="ck_work_order_material_operations_type",
        ),
        CheckConstraint(
            "status IN ('posted','cancelled','reversed')",
            name="ck_work_order_material_operations_status",
        ),
        Index("ix_work_order_material_operations_work_order", "oam_work_order_id", "created_at"),
        Index("uq_work_order_material_posting_0090", "posting_transaction_id", unique=True),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    operation_no: Mapped[str] = mapped_column(String(100))
    oam_work_order_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("oam_work_orders.id", ondelete="RESTRICT")
    )
    operator_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    operation_type: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24))
    posting_transaction_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("inventory_transactions.id", ondelete="RESTRICT"), nullable=True
    )
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_hash: Mapped[str] = mapped_column(String(64))
    replacement_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("work_order_replacements.id", name="fk_work_order_operation_replacement_0093",
                   use_alter=True, deferrable=True, initially="DEFERRED"),
        nullable=True,
    )


class WorkOrderMaterialLine(CreatedAtMixin, Base):
    """Immutable material dimension of one formal work-order operation."""

    __tablename__ = "work_order_material_lines"
    __table_args__ = (
        UniqueConstraint("operation_id", "line_no", name="uq_work_order_material_lines_no"),
        CheckConstraint("quantity > 0", name="ck_work_order_material_lines_positive"),
        Index("ix_work_order_material_lines_material", "material_id", "stock_account_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("work_order_material_operations.id", ondelete="RESTRICT")
    )
    line_no: Mapped[int] = mapped_column(BigInteger)
    material_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id", ondelete="RESTRICT")
    )
    stock_account_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("stock_accounts.id", ondelete="RESTRICT")
    )
    quantity: Mapped[Decimal] = mapped_column(QUANTITY)
    condition_before: Mapped[str] = mapped_column(String(24))
    condition_after: Mapped[str | None] = mapped_column(String(24), nullable=True)


class WorkOrderMaterialSerial(CreatedAtMixin, Base):
    """Per-SN evidence for SKU and QR verification."""

    __tablename__ = "work_order_material_serials"
    __table_args__ = (PrimaryKeyConstraint("operation_line_id", "serial_id", name="pk_work_order_material_serials"),)

    operation_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("work_order_material_lines.id", ondelete="RESTRICT")
    )
    serial_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    sku_verified: Mapped[bool] = mapped_column(Boolean)
    qr_verified: Mapped[bool] = mapped_column(Boolean)


class WorkOrderReplacement(CreatedAtMixin, Base):
    """One atomic replacement command linking consume and recovery stock facts."""

    __tablename__ = "work_order_replacements"
    __table_args__ = (
        UniqueConstraint("replacement_no", name="uq_work_order_replacements_no"),
        UniqueConstraint("idempotency_key_hash", name="uq_work_order_replacements_key"),
        UniqueConstraint("operator_person_id", "request_id", name="uq_work_order_replacements_request"),
        UniqueConstraint("consume_operation_id", name="uq_work_order_replacements_consume"),
        UniqueConstraint("recover_operation_id", name="uq_work_order_replacements_recover"),
        CheckConstraint("consume_operation_id <> recover_operation_id", name="ck_work_order_replacements_distinct_operations"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    replacement_no: Mapped[str] = mapped_column(String(100))
    oam_work_order_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("oam_work_orders.id", ondelete="RESTRICT"))
    operator_person_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"))
    consume_operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("work_order_material_operations.id", deferrable=True, initially="DEFERRED"),
    )
    recover_operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("work_order_material_operations.id", deferrable=True, initially="DEFERRED"),
    )
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_id: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    command_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)


class WorkOrderReplacementPair(CreatedAtMixin, Base):
    """Immutable installed/removed SN pairing for replacement work."""

    __tablename__ = "work_order_replacement_pairs"
    __table_args__ = (
        UniqueConstraint("operation_id", "installed_serial_id", name="uq_work_order_replacement_installed"),
        UniqueConstraint("operation_id", "removed_serial_id", name="uq_work_order_replacement_removed"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    operation_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("work_order_material_operations.id", ondelete="RESTRICT")
    )
    replacement_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("work_order_replacements.id", ondelete="RESTRICT"),
    )
    installed_serial_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    removed_serial_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)


class MaterialSubstitution(TimestampMixin, Base):
    __tablename__ = "material_substitutions"
    __table_args__ = (
        UniqueConstraint(
            "material_id",
            "substitute_material_id",
            "valid_from",
            name="uq_material_substitutions_pair_start",
        ),
        CheckConstraint(
            "material_id <> substitute_material_id",
            name="ck_material_substitutions_distinct_materials",
        ),
        CheckConstraint("ratio > 0", name="ck_material_substitutions_ratio"),
        CheckConstraint(
            "valid_to IS NULL OR valid_to > valid_from",
            name="ck_material_substitutions_validity",
        ),
        CheckConstraint(
            "status IN ('active', 'inactive')",
            name="ck_material_substitutions_status",
        ),
        Index(
            "uq_material_substitutions_current_pair",
            "material_id",
            "substitute_material_id",
            unique=True,
            postgresql_where=text("status = 'active' AND valid_to IS NULL"),
            sqlite_where=text("status = 'active' AND valid_to IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    material_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id", ondelete="RESTRICT"), index=True
    )
    substitute_material_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id", ondelete="RESTRICT"), index=True
    )
    ratio: Mapped[Decimal] = mapped_column(RATIO)
    valid_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    valid_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), index=True)


class ApprovalRouteVersion(TimestampMixin, Base):
    __tablename__ = "approval_route_versions"
    __table_args__ = (
        UniqueConstraint(
            "route_code", "version", name="uq_approval_route_versions_code_version"
        ),
        CheckConstraint("version > 0", name="ck_approval_route_versions_version"),
        CheckConstraint(
            "approval_mode IN ('external_registration', 'direct_star')",
            name="ck_approval_route_versions_mode",
        ),
        CheckConstraint(
            "status IN ('scheduled', 'active', 'inactive')",
            name="ck_approval_route_versions_status",
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_to > effective_from",
            name="ck_approval_route_versions_validity",
        ),
        Index(
            "uq_approval_route_versions_current",
            "route_code",
            unique=True,
            postgresql_where=text("status = 'active' AND effective_to IS NULL"),
            sqlite_where=text("status = 'active' AND effective_to IS NULL"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    route_code: Mapped[str] = mapped_column(String(80), index=True)
    version: Mapped[int] = mapped_column(Integer)
    approval_mode: Mapped[str] = mapped_column(String(32))
    effective_from: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    effective_to: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), index=True)


class ApprovalRouteStepDef(CreatedAtMixin, Base):
    __tablename__ = "approval_route_step_defs"
    __table_args__ = (
        UniqueConstraint(
            "route_version_id", "step_no", name="uq_approval_route_step_defs_step"
        ),
        CheckConstraint(
            "step_no BETWEEN 1 AND 3", name="ck_approval_route_step_defs_number"
        ),
        CheckConstraint(
            "source_mode IN ('internal', 'external_registration', 'direct_star')",
            name="ck_approval_route_step_defs_source_mode",
        ),
        CheckConstraint(
            "scope_type IN ('organization', 'national', 'document')",
            name="ck_approval_route_step_defs_scope_type",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    route_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("approval_route_versions.id", ondelete="RESTRICT"),
        index=True,
    )
    step_no: Mapped[int] = mapped_column(Integer)
    role_code: Mapped[str] = mapped_column(
        String(80), ForeignKey("roles.code", ondelete="RESTRICT")
    )
    source_mode: Mapped[str] = mapped_column(String(32))
    scope_type: Mapped[str] = mapped_column(String(24))


class MaterialRequest(TimestampMixin, Base):
    __tablename__ = "material_requests"
    __table_args__ = (
        UniqueConstraint("request_no", name="uq_material_requests_number"),
        CheckConstraint(
            "urgency IN ('normal', 'urgent', 'emergency')",
            name="ck_material_requests_urgency",
        ),
        CheckConstraint(
            "approval_mode IN ('external_registration', 'direct_star')",
            name="ck_material_requests_approval_mode",
        ),
        CheckConstraint(
            "status IN ('draft', 'submitted', 'approval_in_progress', 'returned', "
            "'approved', 'partially_approved', 'rejected', 'withdrawn', "
            "'cancellation_pending', 'cancelled')",
            name="ck_material_requests_status",
        ),
        CheckConstraint("version >= 0", name="ck_material_requests_version"),
        CheckConstraint(
            "revision_no > 0", name="ck_material_requests_revision"
        ),
        CheckConstraint(
            "(status = 'draft' AND submitted_at IS NULL) OR "
            "(status <> 'draft' AND submitted_at IS NOT NULL)",
            name="ck_material_requests_submission_state",
        ),
        CheckConstraint(
            "(status IN ('approved', 'partially_approved', 'rejected', "
            "'cancellation_pending') "
            "AND decided_at IS NOT NULL) OR "
            "(status NOT IN ('approved', 'partially_approved', 'rejected', "
            "'cancellation_pending', 'cancelled') AND decided_at IS NULL) OR "
            "status = 'cancelled'",
            name="ck_material_requests_decision_state",
        ),
        CheckConstraint(
            "(status = 'withdrawn' AND withdrawn_at IS NOT NULL) OR "
            "(status <> 'withdrawn' AND withdrawn_at IS NULL)",
            name="ck_material_requests_withdrawal_state",
        ),
        CheckConstraint(
            "(status = 'cancelled' AND cancelled_at IS NOT NULL) OR "
            "(status <> 'cancelled' AND cancelled_at IS NULL)",
            name="ck_material_requests_cancellation_state",
        ),
        CheckConstraint(
            "allocation_status IN ('not_allocated', 'partially_allocated', "
            "'allocated', 'shortage')",
            name="ck_material_requests_allocation_status",
        ),
        CheckConstraint(
            "reservation_status IN ('not_reserved', 'pending', 'reserved', "
            "'partially_released', 'released', 'fulfilled')",
            name="ck_material_requests_reservation_status",
        ),
        CheckConstraint(
            "outbound_status IN ('not_started', 'pending_pick', 'picked', 'outbound')",
            name="ck_material_requests_outbound_status",
        ),
        CheckConstraint(
            "shipment_status IN ('not_started', 'pending_handover', 'shipped', "
            "'in_transit', 'exception')",
            name="ck_material_requests_shipment_status",
        ),
        CheckConstraint(
            "logistics_signature_status IN ('not_signed', 'signed', 'refused', "
            "'exception')",
            name="ck_material_requests_signature_status",
        ),
        CheckConstraint(
            "oam_receipt_status IN ('not_occurred', 'synced', 'exception')",
            name="ck_material_requests_oam_receipt_status",
        ),
        CheckConstraint(
            "personal_inbound_status IN ('not_started', 'pending_acceptance', "
            "'partially_accepted', 'accepted', 'posted')",
            name="ck_material_requests_personal_inbound_status",
        ),
        CheckConstraint(
            "notification_status IN ('not_started', 'queued', 'sent', "
            "'delivered', 'read', 'failed')",
            name="ck_material_requests_notification_status",
        ),
        CheckConstraint(
            "reconciliation_status IN ('not_started', 'pending', 'staged', "
            "'validated', 'reconciled', 'conflict', 'failed')",
            name="ck_material_requests_reconciliation_status",
        ),
        Index(
            "ix_material_requests_requester_status",
            "requester_person_id",
            "status",
        ),
        Index(
            "ix_material_requests_org_status",
            "requester_org_id",
            "status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    request_no: Mapped[str] = mapped_column(String(100), index=True)
    requester_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    requester_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"), index=True
    )
    requester_org_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("organizations.id", ondelete="RESTRICT"), index=True
    )
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("oam_work_orders.id", ondelete="RESTRICT"),
        nullable=True,
        index=True,
    )
    purpose: Mapped[str] = mapped_column(Text)
    urgency: Mapped[str] = mapped_column(String(20))
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    address_snapshot_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    # Read endpoints use only these strict masked projections and never need
    # to decrypt the contact envelope or expose the full address detail.
    address_masked_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    # The service accepts only an encrypted/mobile-hash envelope here.  No
    # plaintext contact value may be copied to audit or outbox payloads.
    contact_snapshot_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    contact_masked_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    note: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    approval_mode: Mapped[str] = mapped_column(
        String(32), default="external_registration", server_default="external_registration"
    )
    status: Mapped[str] = mapped_column(
        String(32), default="draft", server_default="draft", index=True
    )
    revision_no: Mapped[int] = mapped_column(
        Integer, default=1, server_default=text("1")
    )
    version: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    # Independent V1 state axes.  Demand/approval commands create these at
    # their neutral values and never advance them; later bounded services own
    # their respective transitions.
    allocation_status: Mapped[str] = mapped_column(
        String(32), default="not_allocated", server_default="not_allocated"
    )
    reservation_status: Mapped[str] = mapped_column(
        String(32), default="not_reserved", server_default="not_reserved"
    )
    outbound_status: Mapped[str] = mapped_column(
        String(32), default="not_started", server_default="not_started"
    )
    shipment_status: Mapped[str] = mapped_column(
        String(32), default="not_started", server_default="not_started"
    )
    logistics_signature_status: Mapped[str] = mapped_column(
        String(32), default="not_signed", server_default="not_signed"
    )
    oam_receipt_status: Mapped[str] = mapped_column(
        String(32), default="not_occurred", server_default="not_occurred"
    )
    personal_inbound_status: Mapped[str] = mapped_column(
        String(32), default="not_started", server_default="not_started"
    )
    notification_status: Mapped[str] = mapped_column(
        String(32), default="not_started", server_default="not_started"
    )
    reconciliation_status: Mapped[str] = mapped_column(
        String(32), default="not_started", server_default="not_started"
    )
    submitted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )


class MaterialRequestRevision(TimestampMixin, Base):
    """One immutable-on-submit request-header evidence snapshot.

    ``MaterialRequest`` is only the current aggregate projection.  A returned
    request creates the next draft revision and never overwrites a previously
    sealed header, line set, attachment set or approval instance.
    """

    __tablename__ = "material_request_revisions"
    __table_args__ = (
        UniqueConstraint(
            "request_id", "revision_no", name="uq_material_request_revisions_number"
        ),
        UniqueConstraint(
            "id", "request_id", name="uq_material_request_revisions_id_request"
        ),
        UniqueConstraint(
            "id",
            "request_id",
            "revision_no",
            name="uq_material_request_revisions_identity",
        ),
        UniqueConstraint(
            "previous_revision_id",
            name="uq_material_request_revisions_previous",
        ),
        ForeignKeyConstraint(
            ["previous_revision_id", "request_id"],
            ["material_request_revisions.id", "material_request_revisions.request_id"],
            name="fk_material_request_revisions_previous_request",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "revision_no > 0", name="ck_material_request_revisions_number"
        ),
        CheckConstraint(
            "(revision_no = 1 AND previous_revision_id IS NULL) OR "
            "(revision_no > 1 AND previous_revision_id IS NOT NULL)",
            name="ck_material_request_revisions_chain",
        ),
        CheckConstraint(
            "urgency IN ('normal', 'urgent', 'emergency')",
            name="ck_material_request_revisions_urgency",
        ),
        CheckConstraint(
            "approval_mode IN ('external_registration', 'direct_star')",
            name="ck_material_request_revisions_approval_mode",
        ),
        CheckConstraint(
            "status IN ('draft', 'sealed')",
            name="ck_material_request_revisions_status",
        ),
        CheckConstraint(
            "(status = 'draft' AND content_manifest_sha256 IS NULL "
            "AND sealed_at IS NULL AND sealed_by_user_id IS NULL) OR "
            "(status = 'sealed' AND length(content_manifest_sha256) = 64 "
            "AND sealed_at IS NOT NULL AND sealed_by_user_id IS NOT NULL)",
            name="ck_material_request_revisions_seal_state",
        ),
        Index(
            "uq_material_request_revisions_current_draft",
            "request_id",
            unique=True,
            postgresql_where=text("status = 'draft'"),
            sqlite_where=text("status = 'draft'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("material_requests.id", ondelete="RESTRICT"),
        index=True,
    )
    revision_no: Mapped[int] = mapped_column(Integer)
    previous_revision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    work_order_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("oam_work_orders.id", ondelete="RESTRICT"),
        nullable=True,
    )
    purpose: Mapped[str] = mapped_column(Text)
    urgency: Mapped[str] = mapped_column(String(20))
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    address_snapshot_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    address_masked_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    contact_snapshot_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT,
        comment="sealed aliyun_kms envelope only; never plaintext contact data",
    )
    contact_masked_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    note: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    approval_mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(
        String(16), default="draft", server_default="draft", index=True
    )
    content_manifest_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    sealed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    sealed_by_user_id: Mapped[str | None] = mapped_column(
        String(36),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )


class MaterialRequestLine(TimestampMixin, Base):
    __tablename__ = "material_request_lines"
    __table_args__ = (
        UniqueConstraint(
            "revision_id", "line_no", name="uq_material_request_lines_number"
        ),
        UniqueConstraint(
            "revision_id",
            "client_line_key",
            name="uq_material_request_lines_client",
        ),
        UniqueConstraint(
            "id",
            "request_id",
            "revision_id",
            name="uq_material_request_lines_identity",
        ),
        ForeignKeyConstraint(
            ["revision_id", "request_id", "revision_no"],
            [
                "material_request_revisions.id",
                "material_request_revisions.request_id",
                "material_request_revisions.revision_no",
            ],
            name="fk_material_request_lines_revision",
            ondelete="RESTRICT",
        ),
        CheckConstraint("line_no > 0", name="ck_material_request_lines_number"),
        CheckConstraint(
            "revision_no > 0", name="ck_material_request_lines_revision"
        ),
        CheckConstraint(
            "requested_qty > 0", name="ck_material_request_lines_requested_qty"
        ),
        CheckConstraint(
            "suggested_substitute_material_id IS NULL OR "
            "suggested_substitute_material_id <> material_id",
            name="ck_material_request_lines_suggested_substitute",
        ),
        CheckConstraint(
            "final_approved_qty >= 0 AND final_approved_qty <= requested_qty",
            name="ck_material_request_lines_approved_qty",
        ),
        CheckConstraint(
            "cancelled_qty >= 0 AND cancelled_qty <= final_approved_qty",
            name="ck_material_request_lines_cancelled_qty",
        ),
        CheckConstraint(
            "status IN ('draft', 'approval_pending', 'approved', "
            "'partially_approved', 'rejected', 'cancelled')",
            name="ck_material_request_lines_status",
        ),
        CheckConstraint("version >= 0", name="ck_material_request_lines_version"),
        Index("ix_material_request_lines_material", "material_id"),
        Index(
            "ix_material_request_lines_suggested_substitute",
            "suggested_substitute_material_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("material_requests.id", ondelete="RESTRICT"),
        index=True,
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    line_no: Mapped[int] = mapped_column(Integer)
    client_line_key: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    material_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("materials.id", ondelete="RESTRICT")
    )
    suggested_substitute_material_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("materials.id", ondelete="RESTRICT"),
        nullable=True,
    )
    requested_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    required_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    status: Mapped[str] = mapped_column(
        String(24), default="draft", server_default="draft", index=True
    )
    final_approved_qty: Mapped[Decimal] = mapped_column(
        QUANTITY,
        default=Decimal("0.000"),
        server_default=text("0"),
        comment=(
            "derived projection only; immutable approval decisions are the fact source"
        ),
    )
    cancelled_qty: Mapped[Decimal] = mapped_column(
        QUANTITY,
        default=Decimal("0.000"),
        server_default=text("0"),
        comment=(
            "derived projection only; accepted cancellation commands are the fact source"
        ),
    )
    version: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )


class MaterialRequestFile(CreatedAtMixin, Base):
    """Formal request-input attachment binding, separate from approval proof."""

    __tablename__ = "material_request_files"
    __table_args__ = (
        ForeignKeyConstraint(
            ["revision_id", "request_id", "revision_no"],
            [
                "material_request_revisions.id",
                "material_request_revisions.request_id",
                "material_request_revisions.revision_no",
            ],
            name="fk_material_request_files_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["request_line_id", "request_id", "revision_id"],
            [
                "material_request_lines.id",
                "material_request_lines.request_id",
                "material_request_lines.revision_id",
            ],
            name="fk_material_request_files_line_request",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "purpose IN ('request_attachment', 'request_line_attachment')",
            name="ck_material_request_files_purpose",
        ),
        CheckConstraint(
            "(purpose = 'request_attachment' AND request_line_id IS NULL) OR "
            "(purpose = 'request_line_attachment' AND request_line_id IS NOT NULL)",
            name="ck_material_request_files_scope",
        ),
        Index(
            "uq_material_request_files_header",
            "revision_id",
            "file_id",
            unique=True,
            postgresql_where=text("request_line_id IS NULL"),
            sqlite_where=text("request_line_id IS NULL"),
        ),
        Index(
            "uq_material_request_files_line",
            "request_line_id",
            "file_id",
            unique=True,
            postgresql_where=text("request_line_id IS NOT NULL"),
            sqlite_where=text("request_line_id IS NOT NULL"),
        ),
        Index("ix_material_request_files_request", "request_id", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("material_requests.id", ondelete="RESTRICT"),
        index=True,
    )
    revision_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    request_line_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    file_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("files.id", ondelete="RESTRICT"), index=True
    )
    purpose: Mapped[str] = mapped_column(String(32))
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )


class ApprovalInstance(TimestampMixin, Base):
    __tablename__ = "approval_instances"
    __table_args__ = (
        UniqueConstraint(
            "request_id", "attempt_no", name="uq_approval_instances_attempt"
        ),
        UniqueConstraint(
            "request_revision_id",
            name="uq_approval_instances_request_revision",
        ),
        ForeignKeyConstraint(
            ["request_revision_id", "request_id", "revision_no"],
            [
                "material_request_revisions.id",
                "material_request_revisions.request_id",
                "material_request_revisions.revision_no",
            ],
            name="fk_approval_instances_request_revision",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["current_step_id", "id", "current_step_no"],
            [
                "approval_steps.id",
                "approval_steps.instance_id",
                "approval_steps.step_no",
            ],
            name="fk_approval_instances_current_step_0030",
            ondelete="RESTRICT",
            deferrable=True,
            initially="DEFERRED",
        ),
        CheckConstraint("attempt_no > 0", name="ck_approval_instances_attempt"),
        CheckConstraint(
            "revision_no > 0", name="ck_approval_instances_revision"
        ),
        CheckConstraint(
            "status IN ('active', 'returned', 'completed', 'rejected', "
            "'withdrawn', 'cancelled', 'superseded')",
            name="ck_approval_instances_status",
        ),
        CheckConstraint(
            "current_step_no IS NULL OR current_step_no BETWEEN 1 AND 3",
            name="ck_approval_instances_current_step",
        ),
        CheckConstraint(
            "(status = 'active' AND current_step_no IS NOT NULL "
            "AND current_step_id IS NOT NULL) OR "
            "(status IN ('returned', 'completed', 'rejected', 'withdrawn', "
            "'cancelled', 'superseded') AND current_step_no IS NULL "
            "AND current_step_id IS NULL)",
            name="ck_approval_instances_current_pointer_0030",
        ),
        CheckConstraint("version >= 0", name="ck_approval_instances_version"),
        Index(
            "uq_approval_instances_current_request",
            "request_id",
            unique=True,
            postgresql_where=text("status = 'active'"),
            sqlite_where=text("status = 'active'"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("material_requests.id", ondelete="RESTRICT"),
        index=True,
    )
    request_revision_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE, index=True)
    revision_no: Mapped[int] = mapped_column(Integer)
    route_version_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("approval_route_versions.id", ondelete="RESTRICT"),
    )
    attempt_no: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), index=True)
    current_step_no: Mapped[int | None] = mapped_column(Integer, nullable=True)
    current_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    version: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class ApprovalStep(TimestampMixin, Base):
    __tablename__ = "approval_steps"
    __table_args__ = (
        UniqueConstraint(
            "instance_id", "step_no", "attempt_no",
            name="uq_approval_steps_attempt",
        ),
        UniqueConstraint("id", "instance_id", name="uq_approval_steps_id_instance"),
        UniqueConstraint(
            "id",
            "instance_id",
            "step_no",
            name="uq_approval_steps_causal_identity_0030",
        ),
        ForeignKeyConstraint(
            ["predecessor_step_id", "instance_id"],
            ["approval_steps.id", "approval_steps.instance_id"],
            name="fk_approval_steps_predecessor_instance",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["supersedes_step_id", "instance_id"],
            ["approval_steps.id", "approval_steps.instance_id"],
            name="fk_approval_steps_supersedes_instance_0030",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["reopened_from_step_id", "instance_id"],
            ["approval_steps.id", "approval_steps.instance_id"],
            name="fk_approval_steps_reopened_from_instance_0030",
            ondelete="RESTRICT",
        ),
        CheckConstraint("step_no BETWEEN 1 AND 3", name="ck_approval_steps_number"),
        CheckConstraint("attempt_no > 0", name="ck_approval_steps_attempt"),
        CheckConstraint(
            "(attempt_no = 1 AND supersedes_step_id IS NULL "
            "AND reopened_from_step_id IS NULL) OR "
            "(attempt_no > 1 AND supersedes_step_id IS NOT NULL)",
            name="ck_approval_steps_rework_pointer_0030",
        ),
        CheckConstraint(
            "(step_no = 1 AND predecessor_step_id IS NULL) OR "
            "(step_no > 1 AND predecessor_step_id IS NOT NULL)",
            name="ck_approval_steps_predecessor",
        ),
        CheckConstraint(
            "source_mode IN ('internal', 'external_registration', 'direct_star')",
            name="ck_approval_steps_source_mode",
        ),
        CheckConstraint(
            "status IN ('pending', 'open', 'awaiting_external_evidence', "
            "'evidence_pending_verification', 'approved', 'partially_approved', "
            "'rejected', 'returned', 'cancelled', 'superseded')",
            name="ck_approval_steps_status",
        ),
        CheckConstraint(
            "(status IN ('approved', 'partially_approved', 'rejected') "
            "AND decided_at IS NOT NULL AND length(decision_manifest_sha256) = 64) "
            "OR (status = 'returned' AND decided_at IS NOT NULL "
            "AND decision_manifest_sha256 IS NULL) OR "
            "(status NOT IN ('approved', 'partially_approved', 'rejected', 'returned') "
            "AND decided_at IS NULL AND decision_manifest_sha256 IS NULL)",
            name="ck_approval_steps_decision_state",
        ),
        CheckConstraint("version >= 0", name="ck_approval_steps_version"),
        Index("ix_approval_steps_instance_status", "instance_id", "status"),
        Index(
            "uq_approval_steps_one_current_0030",
            "instance_id",
            unique=True,
            postgresql_where=text(
                "status IN ('open', 'awaiting_external_evidence', "
                "'evidence_pending_verification')"
            ),
            sqlite_where=text(
                "status IN ('open', 'awaiting_external_evidence', "
                "'evidence_pending_verification')"
            ),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("approval_instances.id", ondelete="RESTRICT"),
        index=True,
    )
    step_no: Mapped[int] = mapped_column(Integer)
    attempt_no: Mapped[int] = mapped_column(Integer)
    predecessor_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    supersedes_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    reopened_from_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    source_mode: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(32), index=True)
    assignee_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    assignee_snapshot_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    decision_manifest_sha256: Mapped[str | None] = mapped_column(
        String(64), nullable=True
    )
    opened_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )


class ApprovalStepCandidate(CreatedAtMixin, Base):
    __tablename__ = "approval_step_candidates"
    __table_args__ = (
        UniqueConstraint(
            "step_id", "user_id", "candidate_kind",
            name="uq_approval_step_candidates_user_kind",
        ),
        CheckConstraint(
            "candidate_kind IN ('assignee', 'registrar', 'verifier')",
            name="ck_approval_step_candidates_kind",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_approval_step_candidates_authorization_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("approval_steps.id", ondelete="RESTRICT"), index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    candidate_kind: Mapped[str] = mapped_column(String(20))
    snapshot_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)


class MaterialRequestCommand(CreatedAtMixin, Base):
    __tablename__ = "material_request_commands"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key_hash",
            name="uq_material_request_commands_idempotency",
        ),
        UniqueConstraint(
            "request_id", "target_version",
            name="uq_material_request_commands_target_version",
        ),
        CheckConstraint(
            "operation IN ('create', 'update_draft', 'submit', 'region_decide', "
            "'headquarters_decide', 'register_external', 'verify_external', "
            "'withdraw', 'cancel', 'propose_substitution', "
            "'confirm_substitution', 'reject_substitution', "
            "'create_supply_task', 'update_supply_task', 'cancel_supply_task', "
            "'allocate', 'reserve', 'release', 'pick', 'outbound', 'shipment', 'receipt', 'personal_inbound')",
            name="ck_material_request_commands_operation",
        ),
        CheckConstraint(
            "length(idempotency_key_hash) = 64 AND length(request_hash) = 64 "
            "AND length(result_hash) = 64",
            name="ck_material_request_commands_hashes",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_material_request_commands_authorization_version",
        ),
        CheckConstraint(
            "target_version >= 0", name="ck_material_request_commands_target_version"
        ),
        CheckConstraint(
            "occurred_at = created_at", name="ck_material_request_commands_time"
        ),
        Index(
            "ix_material_request_commands_request_time",
            "request_id",
            "occurred_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    operation: Mapped[str] = mapped_column(String(32))
    request_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("material_requests.id", ondelete="RESTRICT"),
        index=True,
    )
    target_version: Mapped[int] = mapped_column(BigInteger)
    idempotency_key_hash: Mapped[str] = mapped_column(String(64))
    request_reference: Mapped[str] = mapped_column(String(160))
    request_hash: Mapped[str] = mapped_column(String(64))
    result_hash: Mapped[str] = mapped_column(String(64))
    projection_manifest_sha256: Mapped[str | None] = mapped_column(
        String(64),
        nullable=True,
        server_default=FetchedValue(),
        comment="PostgreSQL trigger-owned material-request content projection digest",
    )
    request_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    result_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    actor_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    actor_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    actor_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalExternalRegistration(TimestampMixin, Base):
    __tablename__ = "approval_external_registrations"
    __table_args__ = (
        UniqueConstraint(
            "registration_no", name="uq_approval_external_registrations_number"
        ),
        CheckConstraint(
            "status IN ('pending_verification', 'accepted', 'rejected', 'superseded')",
            name="ck_approval_external_registrations_status",
        ),
        CheckConstraint(
            "external_action IN ('approve', 'partial_approve', 'reject', 'return')",
            name="ck_approval_external_registrations_action",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_approval_external_registrations_authorization_version",
        ),
        CheckConstraint(
            "length(decision_manifest_sha256) = 64",
            name="ck_approval_external_registrations_manifest",
        ),
        CheckConstraint(
            "(status = 'pending_verification' AND verified_by_user_id IS NULL "
            "AND verified_by_person_id IS NULL AND verified_role_assignment_id IS NULL "
            "AND verified_authorization_version IS NULL AND verified_at IS NULL) OR "
            "(status <> 'pending_verification' AND verified_by_user_id IS NOT NULL "
            "AND verified_by_person_id IS NOT NULL AND verified_role_assignment_id IS NOT NULL "
            "AND verified_authorization_version > 0 AND verified_at IS NOT NULL)",
            name="ck_approval_external_registrations_verification_state",
        ),
        CheckConstraint(
            "verified_by_user_id IS NULL OR (verified_by_user_id <> registered_by_user_id "
            "AND verified_by_person_id <> registered_by_person_id)",
            name="ck_approval_external_registrations_two_person",
        ),
        CheckConstraint(
            "verified_at IS NULL OR verified_at >= registered_at",
            name="ck_approval_external_registrations_time_order",
        ),
        CheckConstraint("version >= 0", name="ck_approval_external_registrations_version"),
        Index(
            "uq_approval_external_registrations_pending_step",
            "step_id",
            unique=True,
            postgresql_where=text("status = 'pending_verification'"),
            sqlite_where=text("status = 'pending_verification'"),
        ),
        Index(
            "uq_approval_external_registrations_accepted_step",
            "step_id",
            unique=True,
            postgresql_where=text("status = 'accepted'"),
            sqlite_where=text("status = 'accepted'"),
        ),
        Index(
            "uq_approval_external_registrations_evidence_file_0036",
            "evidence_file_id",
            unique=True,
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("approval_steps.id", ondelete="RESTRICT"), index=True
    )
    registration_no: Mapped[str] = mapped_column(String(100), index=True)
    external_action: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(24), index=True)
    evidence_file_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("files.id", ondelete="RESTRICT")
    )
    external_approver_snapshot_jsonb: Mapped[dict[str, Any]] = mapped_column(
        JSON_DOCUMENT
    )
    external_decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decision_manifest_sha256: Mapped[str] = mapped_column(String(64))
    registered_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    registered_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    registered_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    registered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    verified_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    verified_by_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"), nullable=True
    )
    verified_role_assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("role_assignments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    verified_authorization_version: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    verification_comment: Mapped[str] = mapped_column(
        Text, default="", server_default=text("''")
    )
    verified_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )


class ApprovalExternalRegistrationLine(CreatedAtMixin, Base):
    __tablename__ = "approval_external_registration_lines"
    __table_args__ = (
        UniqueConstraint(
            "registration_id", "request_line_id",
            name="uq_approval_external_registration_lines_line",
        ),
        CheckConstraint(
            "input_qty > 0 AND approved_qty >= 0 AND rejected_qty >= 0 "
            "AND approved_qty + rejected_qty = input_qty",
            name="ck_approval_external_registration_lines_quantities",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    registration_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("approval_external_registrations.id", ondelete="RESTRICT"),
        index=True,
    )
    request_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("material_request_lines.id", ondelete="RESTRICT"),
        index=True,
    )
    input_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    approved_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    rejected_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))


class ApprovalStepLineDecision(CreatedAtMixin, Base):
    __tablename__ = "approval_step_line_decisions"
    __table_args__ = (
        UniqueConstraint(
            "step_id", "request_line_id",
            name="uq_approval_step_line_decisions_line",
        ),
        CheckConstraint(
            "input_qty > 0 AND approved_qty >= 0 AND rejected_qty >= 0 "
            "AND approved_qty + rejected_qty = input_qty",
            name="ck_approval_step_line_decisions_quantities",
        ),
        CheckConstraint(
            "decision_source IN ('internal', 'external_registration', 'direct_star')",
            name="ck_approval_step_line_decisions_source",
        ),
        CheckConstraint(
            "(decision_source = 'external_registration' "
            "AND external_registration_id IS NOT NULL) OR "
            "(decision_source <> 'external_registration' "
            "AND external_registration_id IS NULL)",
            name="ck_approval_step_line_decisions_external_binding",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_approval_step_line_decisions_authorization_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    step_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("approval_steps.id", ondelete="RESTRICT"), index=True
    )
    request_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("material_request_lines.id", ondelete="RESTRICT"),
        index=True,
    )
    input_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    approved_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    rejected_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    decision_source: Mapped[str] = mapped_column(String(32))
    external_registration_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("approval_external_registrations.id", ondelete="RESTRICT"),
        nullable=True,
    )
    decided_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    decided_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalAction(CreatedAtMixin, Base):
    __tablename__ = "approval_actions"
    __table_args__ = (
        UniqueConstraint("command_id", name="uq_approval_actions_command"),
        UniqueConstraint(
            "id",
            "instance_id",
            "step_id",
            name="uq_approval_actions_causal_identity_0030",
        ),
        Index(
            "uq_approval_actions_cancel_fact_identity_0037",
            "id",
            "instance_id",
            "command_id",
            unique=True,
        ),
        ForeignKeyConstraint(
            ["step_id", "instance_id"],
            ["approval_steps.id", "approval_steps.instance_id"],
            name="fk_approval_actions_step_instance_0030",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "action IN ('submit', 'approve', 'partial_approve', 'reject', "
            "'return', 'withdraw', 'cancel', 'register_external_evidence', "
            "'verify_external_accept', 'verify_external_reject')",
            name="ck_approval_actions_action",
        ),
        CheckConstraint(
            "source_mode IN ('internal', 'external_registration', 'direct_star')",
            name="ck_approval_actions_source_mode",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_approval_actions_authorization_version",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    instance_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("approval_instances.id", ondelete="RESTRICT"),
        index=True,
    )
    step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("approval_steps.id", ondelete="RESTRICT"),
        nullable=True,
    )
    command_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("material_request_commands.id", ondelete="RESTRICT"),
    )
    action: Mapped[str] = mapped_column(String(40), index=True)
    actor_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    actor_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    actor_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    source_mode: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class ApprovalReturnLineFact(CreatedAtMixin, Base):
    """Immutable, line-level evidence for every approval return instruction.

    A first-step return targets a future requester revision and therefore has
    no target approval step.  A second/third-step return points at the newly
    appended immediately-lower step attempt, which in turn identifies the
    returning step through ``reopened_from_step_id``.
    """

    __tablename__ = "approval_return_line_facts"
    __table_args__ = (
        UniqueConstraint(
            "return_action_id",
            "request_line_id",
            name="uq_approval_return_line_facts_action_line_0030",
        ),
        ForeignKeyConstraint(
            ["return_action_id", "instance_id", "returned_from_step_id"],
            [
                "approval_actions.id",
                "approval_actions.instance_id",
                "approval_actions.step_id",
            ],
            name="fk_approval_return_line_facts_action_0030",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["returned_from_step_id", "instance_id"],
            ["approval_steps.id", "approval_steps.instance_id"],
            name="fk_approval_return_line_facts_source_0030",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["target_step_id", "instance_id"],
            ["approval_steps.id", "approval_steps.instance_id"],
            name="fk_approval_return_line_facts_target_step_0030",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["request_line_id", "request_id", "request_revision_id"],
            [
                "material_request_lines.id",
                "material_request_lines.request_id",
                "material_request_lines.revision_id",
            ],
            name="fk_approval_return_line_facts_request_line_0030",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "(target_kind = 'requester_revision' AND target_step_id IS NULL) OR "
            "(target_kind = 'approval_step' AND target_step_id IS NOT NULL)",
            name="ck_approval_return_line_facts_target_0030",
        ),
        CheckConstraint(
            "returned_step_input_qty > 0 AND target_step_max_qty > 0 "
            "AND required_review_qty > 0 "
            "AND required_review_qty <= target_step_max_qty",
            name="ck_approval_return_line_facts_quantities_0030",
        ),
        CheckConstraint(
            "length(trim(reason)) > 0",
            name="ck_approval_return_line_facts_reason_0030",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_approval_return_line_facts_authorization_0030",
        ),
        CheckConstraint(
            "occurred_at = created_at",
            name="ck_approval_return_line_facts_time_0030",
        ),
        Index(
            "ix_approval_return_line_facts_instance_0030",
            "instance_id",
            "occurred_at",
        ),
        Index(
            "ix_approval_return_line_facts_target_0030", "target_step_id"
        ),
        Index(
            "ix_approval_return_line_facts_request_line_0030",
            "request_line_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    return_action_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    returned_from_step_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    target_kind: Mapped[str] = mapped_column(String(24))
    target_step_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, nullable=True
    )
    request_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    request_revision_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    request_line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    returned_step_input_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    target_step_max_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    required_review_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    reason: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    actor_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    actor_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class MaterialRequestCancellationLineFact(CreatedAtMixin, Base):
    """Immutable, line-level evidence for a safe direct cancellation.

    The mutable ``material_request_lines.cancelled_qty`` column remains only a
    projection.  One row is required for every line whose approved quantity is
    positive, and a request line can be directly cancelled only once.
    """

    __tablename__ = "material_request_cancellation_line_facts"
    __table_args__ = (
        UniqueConstraint(
            "cancel_action_id",
            "request_line_id",
            name="uq_material_request_cancel_facts_action_line_0037",
        ),
        UniqueConstraint(
            "cancel_command_id",
            "request_line_id",
            name="uq_material_request_cancel_facts_command_line_0037",
        ),
        UniqueConstraint(
            "request_line_id",
            name="uq_material_request_cancel_facts_request_line_0037",
        ),
        ForeignKeyConstraint(
            ["cancel_action_id", "instance_id", "cancel_command_id"],
            [
                "approval_actions.id",
                "approval_actions.instance_id",
                "approval_actions.command_id",
            ],
            name="fk_material_request_cancel_facts_action_0037",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["request_line_id", "request_id", "request_revision_id"],
            [
                "material_request_lines.id",
                "material_request_lines.request_id",
                "material_request_lines.revision_id",
            ],
            name="fk_material_request_cancel_facts_request_line_0037",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "final_approved_qty_before > 0 "
            "AND cancelled_qty = final_approved_qty_before",
            name="ck_material_request_cancel_facts_quantities_0037",
        ),
        CheckConstraint(
            "length(trim(reason)) > 0 AND length(reason) <= 4000",
            name="ck_material_request_cancel_facts_reason_0037",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_material_request_cancel_facts_authorization_0037",
        ),
        CheckConstraint(
            "occurred_at = created_at",
            name="ck_material_request_cancel_facts_time_0037",
        ),
        Index(
            "ix_material_request_cancel_facts_instance_0037",
            "instance_id",
            "occurred_at",
        ),
        Index(
            "ix_material_request_cancel_facts_request_0037", "request_id"
        ),
        Index(
            "ix_material_request_cancel_facts_command_0037",
            "cancel_command_id",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    cancel_action_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    cancel_command_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    instance_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    request_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    request_revision_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    request_line_id: Mapped[uuid.UUID] = mapped_column(UUID_TYPE)
    final_approved_qty_before: Mapped[Decimal] = mapped_column(QUANTITY)
    cancelled_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    reason: Mapped[str] = mapped_column(Text)
    actor_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    actor_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    actor_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SubstitutionDecision(TimestampMixin, Base):
    __tablename__ = "substitution_decisions"
    __table_args__ = (
        CheckConstraint(
            "status IN ('proposed', 'confirmed', 'rejected', 'cancelled', 'superseded')",
            name="ck_substitution_decisions_status",
        ),
        CheckConstraint(
            "original_approved_qty > 0 AND ratio > 0 AND substitute_qty > 0",
            name="ck_substitution_decisions_quantities",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_substitution_decisions_authorization_version",
        ),
        CheckConstraint(
            "(status = 'proposed' AND decided_by_user_id IS NULL "
            "AND decided_by_person_id IS NULL AND decided_role_assignment_id IS NULL "
            "AND decided_authorization_version IS NULL AND decided_at IS NULL) OR "
            "(status <> 'proposed' AND decided_by_user_id IS NOT NULL "
            "AND decided_by_person_id IS NOT NULL AND decided_role_assignment_id IS NOT NULL "
            "AND decided_authorization_version > 0 AND decided_at IS NOT NULL)",
            name="ck_substitution_decisions_decision_state",
        ),
        CheckConstraint("version >= 0", name="ck_substitution_decisions_version"),
        Index(
            "uq_substitution_decisions_current_line",
            "request_line_id",
            unique=True,
            postgresql_where=text("status IN ('proposed', 'confirmed')"),
            sqlite_where=text("status IN ('proposed', 'confirmed')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    request_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("material_request_lines.id", ondelete="RESTRICT"),
        index=True,
    )
    substitution_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("material_substitutions.id", ondelete="RESTRICT"),
    )
    original_approved_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    ratio: Mapped[Decimal] = mapped_column(RATIO)
    substitute_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    status: Mapped[str] = mapped_column(String(24), index=True)
    proposed_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    proposed_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    proposed_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    proposed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    decided_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    decided_by_person_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT"), nullable=True
    )
    decided_role_assignment_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("role_assignments.id", ondelete="RESTRICT"),
        nullable=True,
    )
    decided_authorization_version: Mapped[int | None] = mapped_column(
        BigInteger, nullable=True
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    reason: Mapped[str] = mapped_column(Text, default="", server_default=text("''"))
    version: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )


class SupplyTask(TimestampMixin, Base):
    __tablename__ = "supply_tasks"
    __table_args__ = (
        UniqueConstraint("task_no", name="uq_supply_tasks_number"),
        CheckConstraint(
            "supply_type IN ('cross_region_transfer', "
            "'headquarters_replenishment', 'star_replenishment', "
            "'external_procurement_reference')",
            name="ck_supply_tasks_type",
        ),
        CheckConstraint(
            "status IN ('open', 'reference_registered', 'awaiting_supply', "
            "'cancelled', 'closed_no_supply')",
            name="ck_supply_tasks_status",
        ),
        CheckConstraint(
            "expected_qty > 0 AND original_equivalent_qty > 0",
            name="ck_supply_tasks_quantities",
        ),
        CheckConstraint(
            "authorization_version > 0",
            name="ck_supply_tasks_authorization_version",
        ),
        CheckConstraint(
            "status <> 'reference_registered' OR reference_no IS NOT NULL",
            name="ck_supply_tasks_reference_state",
        ),
        CheckConstraint(
            "(status = 'cancelled' AND cancelled_at IS NOT NULL "
            "AND cancelled_by_user_id IS NOT NULL) OR "
            "(status <> 'cancelled' AND cancelled_at IS NULL "
            "AND cancelled_by_user_id IS NULL)",
            name="ck_supply_tasks_cancellation_state",
        ),
        CheckConstraint("version >= 0", name="ck_supply_tasks_version"),
        Index("ix_supply_tasks_line_status", "request_line_id", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, primary_key=True, default=uuid4_value
    )
    task_no: Mapped[str] = mapped_column(String(100), index=True)
    request_line_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE,
        ForeignKey("material_request_lines.id", ondelete="RESTRICT"),
        index=True,
    )
    substitution_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID_TYPE,
        ForeignKey("substitution_decisions.id", ondelete="RESTRICT"),
        nullable=True,
    )
    supply_type: Mapped[str] = mapped_column(String(40), index=True)
    reference_no: Mapped[str | None] = mapped_column(String(160), nullable=True)
    expected_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    original_equivalent_qty: Mapped[Decimal] = mapped_column(QUANTITY)
    expected_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    created_by_user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT")
    )
    created_by_person_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("people.id", ondelete="RESTRICT")
    )
    created_role_assignment_id: Mapped[uuid.UUID] = mapped_column(
        UUID_TYPE, ForeignKey("role_assignments.id", ondelete="RESTRICT")
    )
    authorization_version: Mapped[int] = mapped_column(BigInteger)
    cancelled_by_user_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("users.id", ondelete="RESTRICT"), nullable=True
    )
    cancelled_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    version: Mapped[int] = mapped_column(
        BigInteger, default=0, server_default=text("0")
    )


__all__ = [
    "ApprovalAction",
    "ApprovalExternalRegistration",
    "ApprovalExternalRegistrationLine",
    "ApprovalInstance",
    "ApprovalRouteStepDef",
    "ApprovalRouteVersion",
    "ApprovalReturnLineFact",
    "ApprovalStep",
    "ApprovalStepCandidate",
    "ApprovalStepLineDecision",
    "MaterialRequest",
    "MaterialRequestCommand",
    "MaterialRequestCancellationLineFact",
    "MaterialRequestFile",
    "MaterialRequestLine",
    "MaterialRequestRevision",
    "MaterialSubstitution",
    "OamWorkOrder",
    "SubstitutionDecision",
    "SupplyTask",
]
