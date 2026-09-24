"""Immutable control-publication preparation facts, separate from inventory.

These owner-managed records do not grant source/catalogue authority and are
not a published SyncRun. No API, edge or work-order projector ACL is granted.
Later approval and publication must bind these exact preserved versions.
"""
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import BigInteger, CheckConstraint, DateTime, ForeignKey, ForeignKeyConstraint, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base
from .foundation_models import JSON_DOCUMENT, UUID_TYPE, CreatedAtMixin, uuid4_value


class InventoryControlSourceBinding(CreatedAtMixin, Base):
    __tablename__ = "inventory_control_source_bindings"
    __table_args__ = (
        UniqueConstraint("source_system_id", "region_org_id", "target_region_code", "binding_sha256", name="uq_control_binding_identity"),
        CheckConstraint("length(binding_sha256)=64 AND length(target_region_code) BETWEEN 1 AND 160", name="ck_control_binding_context"),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    source_system_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("source_systems.id", ondelete="RESTRICT"))
    region_org_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("organizations.id", ondelete="RESTRICT"))
    target_region_code: Mapped[str] = mapped_column(String(160))
    binding_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    binding_sha256: Mapped[str] = mapped_column(String(64))


class InventoryControlCatalogVersion(CreatedAtMixin, Base):
    __tablename__ = "inventory_control_catalog_versions"
    __table_args__ = (
        UniqueConstraint("binding_id", "catalog_revision", name="uq_control_catalog_revision"),
        UniqueConstraint("id", "binding_id", name="uq_control_catalog_binding"),
        CheckConstraint("length(catalog_sha256)=64 AND length(catalog_revision) BETWEEN 1 AND 160", name="ck_control_catalog_context"),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    binding_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("inventory_control_source_bindings.id", ondelete="RESTRICT"))
    catalog_revision: Mapped[str] = mapped_column(String(160))
    catalog_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    catalog_sha256: Mapped[str] = mapped_column(String(64))


class InventoryControlCaptureChain(CreatedAtMixin, Base):
    __tablename__ = "inventory_control_capture_chains"
    __table_args__ = (
        UniqueConstraint("catalog_id", "capture_chain_sha256", name="uq_control_capture_content"),
        UniqueConstraint("id", "catalog_id", name="uq_control_capture_catalog"),
        CheckConstraint("length(capture_chain_sha256)=64 AND snapshot_count BETWEEN 1 AND 64", name="ck_control_capture_context"),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    catalog_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("inventory_control_catalog_versions.id", ondelete="RESTRICT"))
    evidence_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    capture_chain_sha256: Mapped[str] = mapped_column(String(64))
    snapshot_count: Mapped[int] = mapped_column(BigInteger)


class InventoryControlCaptureSnapshot(CreatedAtMixin, Base):
    __tablename__ = "inventory_control_capture_snapshots"
    __table_args__ = (
        UniqueConstraint("capture_chain_id", "sequence", name="uq_control_capture_sequence"),
        UniqueConstraint("capture_chain_id", "snapshot_ref_id", name="uq_control_capture_snapshot"),
        CheckConstraint("sequence BETWEEN 1 AND 64 AND length(manifest_sha256)=64 AND length(evidence_sha256)=64 AND length(staging_sha256)=64", name="ck_control_snapshot_context"),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    capture_chain_id: Mapped[UUID] = mapped_column(UUID_TYPE, ForeignKey("inventory_control_capture_chains.id", ondelete="RESTRICT"))
    sequence: Mapped[int] = mapped_column(BigInteger)
    snapshot_ref_id: Mapped[str] = mapped_column(String(36), ForeignKey("external_sync_snapshots.id", ondelete="RESTRICT"))
    manifest_sha256: Mapped[str] = mapped_column(String(64))
    evidence_sha256: Mapped[str] = mapped_column(String(64))
    staging_sha256: Mapped[str] = mapped_column(String(64))


class InventoryControlPreparation(CreatedAtMixin, Base):
    __tablename__ = "inventory_control_preparations"
    __table_args__ = (
        ForeignKeyConstraint(["catalog_id", "binding_id"], ["inventory_control_catalog_versions.id", "inventory_control_catalog_versions.binding_id"], ondelete="RESTRICT"),
        ForeignKeyConstraint(["capture_chain_id", "catalog_id"], ["inventory_control_capture_chains.id", "inventory_control_capture_chains.catalog_id"], ondelete="RESTRICT"),
        UniqueConstraint("capture_chain_id", "control_manifest_sha256", name="uq_control_preparation_content"),
        CheckConstraint("length(control_manifest_sha256)=64 AND checked_at<=created_at", name="ck_control_preparation_context"),
    )
    id: Mapped[UUID] = mapped_column(UUID_TYPE, primary_key=True, default=uuid4_value)
    binding_id: Mapped[UUID] = mapped_column(UUID_TYPE)
    catalog_id: Mapped[UUID] = mapped_column(UUID_TYPE)
    capture_chain_id: Mapped[UUID] = mapped_column(UUID_TYPE)
    checked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    control_manifest_jsonb: Mapped[dict[str, Any]] = mapped_column(JSON_DOCUMENT)
    control_manifest_sha256: Mapped[str] = mapped_column(String(64))


TABLES = (
    InventoryControlSourceBinding.__tablename__, InventoryControlCatalogVersion.__tablename__,
    InventoryControlCaptureChain.__tablename__, InventoryControlCaptureSnapshot.__tablename__,
    InventoryControlPreparation.__tablename__,
)
